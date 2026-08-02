"""Command-line interface.

    echolock enrol      record samples and build the voiceprint
    echolock phrase     print today's passphrase
    echolock check      record once and report the decision, without unlocking
    echolock lock       show the unlock overlay
    echolock status     describe the stored profile
    echolock devices    list microphones
    echolock reset      delete the profile
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__
from .defaults import DEFAULT_SENSITIVITY
from .phrase import enrolment_prompts, ephemeral_phrase, format_phrase, phrase_today
from .storage import Config, config_path, profile_dir, profile_exists, profile_path

# `features` and `voiceprint` are imported inside the commands that need them.
# They pull in numpy, which is a sixth of a second, and most invocations here
# never touch it: printing a phrase, listing microphones, showing the help, or
# toggling a setting are all pure Python.


def _stdout_utf8() -> None:
    """Windows consoles default to cp1252 and mangle the arrows used below."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def _load_profile():
    if not profile_exists():
        raise SystemExit("No profile found. Run 'echolock enrol' first.")
    from .voiceprint import Voiceprint

    return Voiceprint.load(profile_path())


def _current_phrase(config: Config):
    """The prompt for this attempt: a sentence plus the words to verify."""
    if config.per_attempt_phrase:
        return ephemeral_phrase(config.word_count)
    return phrase_today(config.salt, config.word_count)


# -- commands --------------------------------------------------------------

def cmd_enrol(args: argparse.Namespace) -> int:
    from .audio import AudioUnavailable, looks_clipped, peak_level, record

    config = Config.load()
    samples: list = []
    count = args.samples

    print(f"\nEnrolling a voiceprint from {count} recordings.")
    print("Speak normally, at the distance you would actually sit from the microphone.\n")

    prompts = enrolment_prompts(count, config.word_count)
    try:
        for i in range(count):
            print(f"  [{i + 1}/{count}] Read aloud:  {prompts[i]}")
            input("        press Enter when ready to record... ")
            audio = record(args.seconds, config.sample_rate,
                           device=args.device if args.device is not None else config.input_device)

            level = peak_level(audio)
            if level < 0.01:
                print("        nothing was heard; skipping this take.\n")
                continue
            if looks_clipped(audio):
                print("        that clipped; move back a little. Skipping this take.\n")
                continue
            samples.append(audio)
            print(f"        captured (peak {level:.2f})\n")
    except AudioUnavailable as exc:
        raise SystemExit(f"error: {exc}")
    except KeyboardInterrupt:
        return 130

    if len(samples) < 3:
        raise SystemExit(
            f"Only {len(samples)} usable recordings; at least 3 are needed. "
            "Check the microphone level and try again."
        )

    from .features import FeatureConfig
    from .voiceprint import InsufficientAudio, build_voiceprint

    cfg = FeatureConfig(sample_rate=config.sample_rate)
    try:
        voiceprint = build_voiceprint(samples, cfg, sensitivity=args.sensitivity)
    except InsufficientAudio as exc:
        raise SystemExit(f"error: {exc}")

    voiceprint.save(profile_path())
    calibration = voiceprint.calibration

    print(f"Profile saved to {profile_path()}")
    print(f"  recordings used:   {voiceprint.n_samples}")
    print(f"  threshold:         {voiceprint.threshold:+.3f}")
    print(f"  enrolment scores:  {calibration['loo_min']:+.3f} to {calibration['loo_max']:+.3f}")

    if calibration["clamped"]:
        print(
            "\n  Warning: your recordings varied so much that the calculated threshold\n"
            "  would have accepted almost any voice, so it was capped instead. Only\n"
            f"  {calibration['enrolment_pass_rate']:.0%} of your own takes clear the capped threshold, which means\n"
            "  unlocking will be unreliable. Re-enrol in a quiet room, holding a\n"
            "  steady distance from the microphone."
        )
    elif calibration["loo_max"] - calibration["loo_min"] > 1.0:
        print(
            "\n  Note: your takes varied quite a bit, so the threshold is looser than\n"
            "  ideal. Re-enrolling somewhere quieter would make it more selective."
        )
    print(f"\nToday's phrase: {format_phrase(_current_phrase(config))}")
    print("Try it with 'echolock check'.")
    return 0


def cmd_phrase(args: argparse.Namespace) -> int:
    config = Config.load()
    if config.per_attempt_phrase:
        print("Per-attempt phrases are enabled; each unlock prompt is generated fresh.")
        print(f"Example: {ephemeral_phrase(config.word_count).text}")
    else:
        print(phrase_today(config.salt, config.word_count).text)
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    from .asr import SpeechUnavailable, VoskTranscriber
    from .audio import AudioUnavailable, record
    from .features import FeatureConfig
    from .verifier import verify

    config = Config.load()
    voiceprint = _load_profile()
    phrase = _current_phrase(config)

    try:
        transcriber = VoskTranscriber(config.vosk_model_path or None, config.sample_rate)
    except SpeechUnavailable as exc:
        raise SystemExit(f"error: {exc}")

    print(f"\nSay:  {format_phrase(phrase)}")
    input("press Enter, then speak... ")
    try:
        audio = record(args.seconds or config.record_seconds, config.sample_rate,
                           device=args.device if args.device is not None else config.input_device)
    except AudioUnavailable as exc:
        raise SystemExit(f"error: {exc}")

    decision = verify(
        audio, list(phrase.keywords), voiceprint, transcriber,
        FeatureConfig(sample_rate=config.sample_rate),
        min_phrase_ratio=config.min_phrase_ratio,
    )

    print(f"\n  result:     {'UNLOCK' if decision.unlocked else 'DENY'}")
    print(f"  reason:     {decision.reason}")
    if decision.liveness is not None:
        print(f"  heard:      {decision.liveness.transcript or '(nothing)'}")
    if decision.score is not None:
        print(f"  voice score {decision.score:+.3f}  (threshold {decision.threshold:+.3f}, "
              f"margin {decision.margin:+.3f})")
    print()
    return 0 if decision.unlocked else 1


def cmd_lock(args: argparse.Namespace) -> int:
    from .ui import run_overlay

    if not profile_exists():
        raise SystemExit("No profile found. Run 'echolock enrol' first.")
    return run_overlay(lock_session=not args.no_session_lock)


def cmd_status(args: argparse.Namespace) -> int:
    config = Config.load()
    print(f"\nProfile directory: {profile_dir()}")
    print(f"Config:            {config_path()}")

    if not profile_exists():
        print("\nNo voiceprint enrolled yet. Run 'echolock enrol'.")
        return 1

    from .voiceprint import Voiceprint

    voiceprint = Voiceprint.load(profile_path())
    print(f"\nVoiceprint")
    print(f"  enrolled:      {voiceprint.created_at}")
    print(f"  recordings:    {voiceprint.n_samples}")
    print(f"  sample rate:   {voiceprint.sample_rate} Hz")
    print(f"  threshold:     {voiceprint.threshold:+.3f}")
    print(f"  dimensions:    {voiceprint.centroid.size}")
    print(f"\nPhrase")
    print(f"  random words:  {config.word_count}")
    print(f"  mode:          {'per attempt' if config.per_attempt_phrase else 'daily'}")
    if not config.per_attempt_phrase:
        print(f"  today:         {phrase_today(config.salt, config.word_count).text}")

    try:
        from .asr import VoskTranscriber

        transcriber = VoskTranscriber(config.vosk_model_path or None, config.sample_rate)
        print(f"\nSpeech model:    {transcriber.model_path}")
    except Exception as exc:  # noqa: BLE001
        print(f"\nSpeech model:    unavailable ({exc})")
    print()
    return 0


def cmd_config(args: argparse.Namespace) -> int:
    """Show or change stored settings."""
    config = Config.load()
    changed = []

    if args.per_attempt is not None:
        config.per_attempt_phrase = args.per_attempt == "on"
        changed.append(f"per_attempt_phrase={config.per_attempt_phrase}")
    if args.words is not None:
        if args.words < 2:
            raise SystemExit("error: --words must be at least 2")
        config.word_count = args.words
        changed.append(f"word_count={args.words}")
    if args.seconds is not None:
        if args.seconds <= 0:
            raise SystemExit("error: --seconds must be positive")
        config.record_seconds = args.seconds
        changed.append(f"record_seconds={args.seconds}")
    if args.device is not None:
        config.input_device = args.device
        changed.append(f"input_device={args.device}")

    if changed:
        config.save()
        print("updated: " + ", ".join(changed))
        return 0

    print(f"\nSettings ({config_path()})\n")
    print(f"  phrase mode      {'per attempt' if config.per_attempt_phrase else 'daily'}")
    print(f"  words per phrase {config.word_count}")
    print(f"  record seconds   {config.record_seconds}")
    print(f"  input device     {config.input_device if config.input_device is not None else 'system default'}")
    print(f"  sample rate      {config.sample_rate}")
    print("\nChange with, for example:  echolock config --per-attempt on\n")
    return 0


def cmd_autostart(args: argparse.Namespace) -> int:
    """Turn the login-time overlay on or off."""
    from .autostart import AutostartUnavailable, disable, enable, is_enabled, shortcut_path

    # Asking the state is a read-only question and must answer on any platform,
    # including one where autostart cannot be configured at all. Only the
    # commands that change something report an unsupported platform as an error.
    if args.action == "status":
        try:
            location = f"  ({shortcut_path()})"
        except AutostartUnavailable as exc:
            print(f"Autostart is off and cannot be configured here: {exc}")
            return 0
        print(f"Autostart is {'on' if is_enabled() else 'off'}{location}")
        return 0

    try:
        if args.action == "on":
            path = enable()
            print(f"EchoLock will start at login.\n  {path}")
            print(
                "\nThis covers the desktop after you log in. It does not replace the\n"
                "Windows login itself, which no ordinary program is allowed to do."
            )
        else:
            print("Autostart removed." if disable() else "Autostart was not enabled.")
    except AutostartUnavailable as exc:
        raise SystemExit(f"error: {exc}")
    return 0


def cmd_credential(args: argparse.Namespace) -> int:
    """Store, inspect or remove the password the sign-in tile submits."""
    from .vault import VaultUnavailable, clear, exists, is_supported, store, vault_path

    if args.action == "status":
        if not is_supported():
            print("The credential store is only implemented for Windows.")
            return 0
        print(f"Credential stored: {'yes' if exists() else 'no'}  ({vault_path()})")
        return 0

    if args.action == "clear":
        print("Credential removed." if clear() else "No credential was stored.")
        return 0

    # Setting one. The password is read from the terminal without echo and is
    # never accepted as an argument, because arguments end up in shell history
    # and in the process list where any other user can read them.
    import getpass

    print(
        "\nThis stores your Windows password so the sign-in tile can submit it\n"
        "after your voice matches. Read this before continuing:\n\n"
        "  It is encrypted with a key held by this machine, because nothing\n"
        "  else is available before you log in. That stops someone reading it\n"
        "  from a copy of the file or a stolen drive. It does NOT stop code\n"
        "  already running as Administrator on this machine from recovering\n"
        "  it. You are trading some account security for convenience.\n\n"
        "  'echolock credential clear' removes it at any time.\n"
    )
    if input("Type 'yes' to continue: ").strip().lower() != "yes":
        print("Nothing was stored.")
        return 1

    password = getpass.getpass("Windows password (not shown): ")
    if not password:
        raise SystemExit("error: nothing entered; nothing was stored")
    if password != getpass.getpass("Type it again: "):
        raise SystemExit("error: the two entries did not match; nothing was stored")

    try:
        path = store(password)
    except (VaultUnavailable, ValueError) as exc:
        raise SystemExit(f"error: {exc}")
    finally:
        del password

    print(f"\nStored.  {path}")
    print("The tile also needs an enrolled voice; check with 'echolock provider status'.")
    return 0


def cmd_provider(args: argparse.Namespace) -> int:
    """The machine-readable half of the sign-in tile."""
    from . import provider

    if args.action == "begin":
        return provider.begin()
    if args.action == "verify":
        return provider.verify_attempt(args.session)
    return provider.status()


def cmd_download(args: argparse.Namespace) -> int:
    """Fetch the offline speech model."""
    from .download import DownloadFailed, download_model, is_installed

    if is_installed() and not args.force:
        print("A speech model is already installed. Use --force to fetch it again.")
        return 0

    last = [-1]

    def report(done: int, total: int) -> None:
        percent = int(done * 100 / max(total, 1))
        if percent != last[0]:
            last[0] = percent
            print(f"\r  downloading speech model... {percent:3d}%", end="", flush=True)

    print("Fetching the offline speech model (about 40 MB).")
    try:
        path = download_model(progress=report)
    except DownloadFailed as exc:
        print()
        raise SystemExit(f"error: {exc}")
    print("\r  downloading speech model... done      ")
    print(f"\nInstalled to {path}")
    return 0


def cmd_watch(args: argparse.Namespace) -> int:
    """Show the overlay whenever the session goes idle."""
    from .idle import IdleUnavailable, is_supported, watch

    if not profile_exists():
        raise SystemExit("No profile found. Run 'echolock enrol' first.")
    if not is_supported():
        raise SystemExit("error: idle detection is only implemented for Windows")

    print(f"Watching for {args.minutes:g} minutes of inactivity. Ctrl+C to stop.")
    print("The overlay covers the desktop; it does not replace the Windows lock.")
    try:
        watch(args.minutes, on_lock=lambda: print("  idle, covering the screen"))
    except IdleUnavailable as exc:
        raise SystemExit(f"error: {exc}")
    except KeyboardInterrupt:
        print("\nStopped.")
    return 0


def cmd_gui(args: argparse.Namespace) -> int:
    from .gui import run_gui

    return run_gui()


def cmd_devices(args: argparse.Namespace) -> int:
    from .audio import AudioUnavailable, list_devices

    try:
        devices = list_devices()
    except AudioUnavailable as exc:
        raise SystemExit(f"error: {exc}")
    if not devices:
        print("No input devices found.")
        return 1
    print("\nInput devices:")
    for device in devices:
        print(f"  [{device['index']:>2}] {device['name']}  ({device['channels']} ch)")
    print()
    return 0


def cmd_reset(args: argparse.Namespace) -> int:
    if not args.yes:
        answer = input(f"Delete the profile in {profile_dir()}? [y/N] ").strip().lower()
        if answer != "y":
            print("Cancelled.")
            return 1
    removed = []
    for path in (profile_path(), profile_path().with_suffix(".json"), config_path()):
        if path.exists():
            path.unlink()
            removed.append(path.name)
    print(f"Removed: {', '.join(removed) if removed else 'nothing to remove'}")
    return 0


# -- parser ----------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="echolock",
        description="Voice and passphrase unlock overlay. Not a replacement for "
                    "your operating system's authentication.",
    )
    parser.add_argument("--version", action="version", version=f"echolock {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("enrol", aliases=["enroll"], help="record samples and build the voiceprint")
    p.add_argument("-n", "--samples", type=int, default=10, help="recordings to take (default: 10)")
    p.add_argument("-s", "--seconds", type=float, default=4.0, help="seconds per recording")
    p.add_argument("--sensitivity", type=float, default=DEFAULT_SENSITIVITY,
                   help="threshold placement in standard deviations (higher accepts more variation)")
    p.add_argument("--device", type=int, default=None, help="input device index")
    p.set_defaults(func=cmd_enrol)

    p = sub.add_parser("phrase", help="print today's passphrase")
    p.set_defaults(func=cmd_phrase)

    p = sub.add_parser("check", help="record once and report the decision without unlocking")
    p.add_argument("-s", "--seconds", type=float, default=None, help="seconds to record")
    p.add_argument("--device", type=int, default=None, help="input device index")
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("lock", help="show the unlock overlay")
    p.add_argument("--no-session-lock", action="store_true",
                   help="do not also lock the Windows session behind the overlay "
                        "(demo only; the overlay alone is not a security boundary)")
    p.set_defaults(func=cmd_lock)

    p = sub.add_parser("status", help="describe the stored profile")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("config", help="show or change settings")
    p.add_argument("--per-attempt", choices=["on", "off"],
                   help="generate a fresh phrase for every attempt instead of daily")
    p.add_argument("--words", type=int, help="words per phrase")
    p.add_argument("--seconds", type=float, help="seconds to record per attempt")
    p.add_argument("--device", type=int, help="input device index to remember")
    p.set_defaults(func=cmd_config)

    p = sub.add_parser("autostart", help="run the overlay when Windows starts")
    p.add_argument("action", nargs="?", choices=["on", "off", "status"], default="status")
    p.set_defaults(func=cmd_autostart)

    p = sub.add_parser("credential", help="password the Windows sign-in tile submits")
    p.add_argument("action", nargs="?", choices=["set", "clear", "status"], default="status")
    p.set_defaults(func=cmd_credential)

    # Not in the help: this is spoken by the credential provider, not by people,
    # and listing it invites someone to run `verify` by hand and wonder why it
    # refuses without a session token.
    p = sub.add_parser("provider")
    p.add_argument("action", nargs="?", choices=["begin", "verify", "status"], default="status")
    p.add_argument("--session", default="", help="token returned by 'begin'")
    p.set_defaults(func=cmd_provider)

    p = sub.add_parser("download-model", help="fetch the offline speech model")
    p.add_argument("--force", action="store_true", help="fetch even if one is installed")
    p.set_defaults(func=cmd_download)

    p = sub.add_parser("watch", help="cover the screen after a period of inactivity")
    p.add_argument("-m", "--minutes", type=float, default=5.0,
                   help="minutes of inactivity before the overlay appears (default: 5)")
    p.set_defaults(func=cmd_watch)

    p = sub.add_parser("gui", help="open the desktop interface")
    p.set_defaults(func=cmd_gui)

    p = sub.add_parser("devices", help="list microphones")
    p.set_defaults(func=cmd_devices)

    p = sub.add_parser("reset", help="delete the profile")
    p.add_argument("-y", "--yes", action="store_true", help="do not ask for confirmation")
    p.set_defaults(func=cmd_reset)

    return parser


def main(argv: list[str] | None = None) -> int:
    _stdout_utf8()
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        print()
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
