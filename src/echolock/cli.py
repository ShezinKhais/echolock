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
from .features import FeatureConfig
from .phrase import ephemeral_phrase, format_phrase, phrase_today
from .storage import Config, config_path, profile_dir, profile_exists, profile_path
from .voiceprint import DEFAULT_SENSITIVITY, InsufficientAudio, Voiceprint, build_voiceprint

ENROL_PROMPTS = [
    "The quick brown fox jumps over the lazy dog.",
    "Sunlight filtered through the tall kitchen window.",
    "Seven bright copper kettles lined the wooden shelf.",
    "She parked the car and walked the rest of the way.",
    "Autumn leaves gathered along the narrow garden path.",
    "The train arrives at quarter past eleven tomorrow.",
    "Fresh bread and strong coffee for breakfast again.",
    "A grey cat slept beneath the blue painted bench.",
    "Distant thunder rolled across the open valley floor.",
    "He counted every step from the door to the corner.",
]


def _stdout_utf8() -> None:
    """Windows consoles default to cp1252 and mangle the arrows used below."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def _load_profile() -> Voiceprint:
    if not profile_exists():
        raise SystemExit("No profile found. Run 'echolock enrol' first.")
    return Voiceprint.load(profile_path())


def _current_phrase(config: Config) -> list[str]:
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

    try:
        for i in range(count):
            prompt = ENROL_PROMPTS[i % len(ENROL_PROMPTS)]
            print(f"  [{i + 1}/{count}] Read aloud:  {prompt}")
            input("        press Enter when ready to record... ")
            audio = record(args.seconds, config.sample_rate, device=args.device)

            level = peak_level(audio)
            if level < 0.01:
                print("        nothing was heard; skipping this take.\n")
                continue
            if looks_clipped(audio):
                print("        that clipped -- move back a little; skipping this take.\n")
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
        print(f"Example: {format_phrase(ephemeral_phrase(config.word_count))}")
    else:
        print(format_phrase(phrase_today(config.salt, config.word_count)))
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    from .asr import SpeechUnavailable, VoskTranscriber
    from .audio import AudioUnavailable, record
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
        audio = record(args.seconds or config.record_seconds, config.sample_rate, device=args.device)
    except AudioUnavailable as exc:
        raise SystemExit(f"error: {exc}")

    decision = verify(
        audio, phrase, voiceprint, transcriber,
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

    voiceprint = Voiceprint.load(profile_path())
    print(f"\nVoiceprint")
    print(f"  enrolled:      {voiceprint.created_at}")
    print(f"  recordings:    {voiceprint.n_samples}")
    print(f"  sample rate:   {voiceprint.sample_rate} Hz")
    print(f"  threshold:     {voiceprint.threshold:+.3f}")
    print(f"  dimensions:    {voiceprint.centroid.size}")
    print(f"\nPhrase")
    print(f"  words:         {config.word_count}")
    print(f"  mode:          {'per attempt' if config.per_attempt_phrase else 'daily'}")
    if not config.per_attempt_phrase:
        print(f"  today:         {format_phrase(phrase_today(config.salt, config.word_count))}")

    try:
        from .asr import VoskTranscriber

        transcriber = VoskTranscriber(config.vosk_model_path or None, config.sample_rate)
        print(f"\nSpeech model:    {transcriber.model_path}")
    except Exception as exc:  # noqa: BLE001
        print(f"\nSpeech model:    unavailable ({exc})")
    print()
    return 0


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
