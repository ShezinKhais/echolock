"""A single command that answers "is this actually going to work?".

Two audiences, one check list.

For whoever is running it, the useful question is not whether the program
starts but whether the next thing they try will succeed: is a voice enrolled,
is the speech model there, does the microphone open, will the overlay come up
at sign-in. Finding that out by attempting each one and reading an error is a
poor introduction.

For the packaged build it serves a second purpose that matters more than it
sounds. Almost everything here is imported at the moment it is used, which is
what keeps startup fast, and a deferred import is one PyInstaller cannot see by
walking the source. A module missing from the build therefore stays invisible
until someone clicks the button that needs it. Importing every one of them here
turns that class of packaging fault into a failed check on a machine that has
the binary, rather than a crash on a machine that has the user.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass

# Every module the program reaches only at runtime. The list is deliberately
# exhaustive rather than representative: its value is that it fails when the
# build is short of something.
RUNTIME_MODULES = (
    "echolock.asr",
    "echolock.audio",
    "echolock.autostart",
    "echolock.download",
    "echolock.features",
    "echolock.guard",
    "echolock.gui",
    "echolock.idle",
    "echolock.liveness",
    "echolock.pin",
    "echolock.provider",
    "echolock.ui",
    "echolock.vault",
    "echolock.verifier",
    "echolock.voiceprint",
)

OK, WARN, FAIL = "ok", "warn", "fail"


@dataclass
class Check:
    name: str
    state: str
    detail: str = ""


def _packaging() -> list[Check]:
    frozen = getattr(sys, "frozen", False)
    missing = []
    for name in RUNTIME_MODULES:
        try:
            __import__(name)
        except Exception as exc:  # noqa: BLE001
            missing.append(f"{name} ({type(exc).__name__}: {exc})")

    checks = [
        Check("running from", OK, "packaged executable" if frozen else "source"),
    ]
    if missing:
        checks.append(
            Check(
                "runtime modules", FAIL,
                f"{len(missing)} of {len(RUNTIME_MODULES)} could not be imported:\n    "
                + "\n    ".join(missing),
            )
        )
    else:
        checks.append(Check("runtime modules", OK, f"all {len(RUNTIME_MODULES)} import"))
    return checks


def _profile() -> list[Check]:
    from .storage import Config, profile_dir, profile_exists, profile_path

    checks = [Check("profile directory", OK, str(profile_dir()))]

    if not profile_exists():
        checks.append(Check("voice profile", FAIL, "not enrolled; run 'echolock enrol'"))
        return checks

    try:
        from .voiceprint import Voiceprint

        voiceprint = Voiceprint.load(profile_path())
    except Exception as exc:  # noqa: BLE001
        checks.append(Check("voice profile", FAIL, f"could not be read: {exc}"))
        return checks

    calibration = voiceprint.calibration
    spread = calibration.get("loo_max", 0) - calibration.get("loo_min", 0)
    detail = (
        f"{voiceprint.n_samples} recordings, threshold {voiceprint.threshold:+.3f}, "
        f"spread {spread:.2f}"
    )
    if calibration.get("clamped"):
        checks.append(Check("voice profile", WARN, detail + "\n    threshold was capped; re-enrol somewhere quieter"))
    elif spread > 1.0:
        checks.append(Check("voice profile", WARN, detail + "\n    takes varied a fair amount, so the threshold is loose"))
    else:
        checks.append(Check("voice profile", OK, detail))

    config = Config.load()
    checks.append(
        Check("phrase mode", OK, "fresh every attempt" if config.per_attempt_phrase else "changes daily")
    )
    return checks


def _speech() -> list[Check]:
    try:
        from .download import is_installed
    except Exception as exc:  # noqa: BLE001
        return [Check("speech model", FAIL, f"the downloader is unavailable: {exc}")]

    if is_installed():
        return [Check("speech model", OK, "installed")]
    return [
        Check(
            "speech model", FAIL,
            "not installed, so the phrase cannot be checked and only the voice "
            "would be verified.\n    Run 'echolock download-model'",
        )
    ]


def _microphone() -> list[Check]:
    try:
        from .audio import list_devices
        from .storage import Config

        devices = list_devices()
    except Exception as exc:  # noqa: BLE001
        return [Check("microphone", FAIL, str(exc))]

    if not devices:
        return [Check("microphone", FAIL, "no input device was found")]

    chosen = Config.load().input_device
    if chosen is None:
        return [Check("microphone", OK, f"{len(devices)} inputs, using the system default")]
    match = next((d for d in devices if d["index"] == chosen), None)
    if match is None:
        return [
            Check(
                "microphone", WARN,
                f"device {chosen} is remembered but not present now; "
                "the system default will be used",
            )
        ]
    return [Check("microphone", OK, f"[{chosen}] {match['name']}")]


def _fallbacks() -> list[Check]:
    checks = []

    from . import pin

    if pin.is_set():
        checks.append(Check("fallback PIN", OK, "set"))
    else:
        checks.append(
            Check(
                "fallback PIN", WARN,
                "not set. If your voice is not recognised there is no other way "
                "past the overlay.\n    Run 'echolock pin set'",
            )
        )

    from .autostart import is_enabled

    checks.append(
        Check("lock at sign-in", OK if is_enabled() else WARN,
              "enabled" if is_enabled() else "off; run 'echolock autostart on'")
    )

    from .guard import is_supported

    checks.append(
        Check(
            "overlay guard", OK if is_supported() else WARN,
            "holds focus and blocks the Windows key" if is_supported()
            else "only implemented for Windows",
        )
    )
    return checks


def run() -> list[Check]:
    """Every check, in the order a new install would hit them."""
    checks = _packaging()
    for section in (_profile, _speech, _microphone, _fallbacks):
        try:
            checks += section()
        except Exception as exc:  # noqa: BLE001
            checks.append(Check(section.__name__.strip("_"), FAIL, f"check itself failed: {exc}"))
    return checks


def report() -> int:
    """Print the checks. Returns non-zero if anything is broken."""
    checks = run()
    marks = {OK: "ok  ", WARN: "warn", FAIL: "FAIL"}

    print()
    for check in checks:
        print(f"  [{marks[check.state]}]  {check.name}")
        if check.detail:
            for line in check.detail.splitlines():
                print(f"           {line}")
    failures = [c for c in checks if c.state == FAIL]
    warnings = [c for c in checks if c.state == WARN]

    print()
    if failures:
        print(f"{len(failures)} problem(s) would stop this working.")
    elif warnings:
        print(f"Usable. {len(warnings)} thing(s) worth setting up.")
    else:
        print("Everything checks out.")
    if os.name != "nt":
        print("Note: locking and autostart are Windows-only; verification runs anywhere.")
    return 1 if failures else 0
