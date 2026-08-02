"""Does a microphone still work once the workstation is locked?

This decides whether a credential provider is worth writing at all. A provider
that cannot hear anything at the logon desktop is a provider that can only ever
offer a button that does nothing, so the question is settled before any COM code
exists rather than after.

The interesting number is not the raw level, which depends entirely on how the
microphone gain happens to be set, but whether the locked capture resembles the
unlocked one. So the probe is run twice and the two are compared:

    python packaging/locked_mic_probe.py meter      live level bar, to check the mic
    python packaging/locked_mic_probe.py baseline   capture now, unlocked
    python packaging/locked_mic_probe.py locked     wait, then capture while locked

Results append to locked_mic_probe.txt. Thresholds match the ones the
application itself uses during enrolment, so "audible" here means exactly what
it means everywhere else in the program. No audio is written to disk; only the
measured levels are kept.
"""

from __future__ import annotations

import datetime as _dt
import math
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

# The level below which the enrolment dialog says "nothing was heard". Reused
# rather than reinvented: a probe that grades on its own scale can call a
# working microphone silent, which is exactly what an earlier version did.
AUDIBLE = 0.01

DELAY_SECONDS = 20.0
RECORD_SECONDS = 6.0
METER_SECONDS = 12.0
REPORT = Path(__file__).with_name("locked_mic_probe.txt")


def _dbfs(level: float) -> str:
    return "-inf dBFS" if level <= 0 else f"{20 * math.log10(level):+.1f} dBFS"


def _append(lines: list[str]) -> None:
    stamp = _dt.datetime.now().isoformat(timespec="seconds")
    body = "\n".join(f"  {line}" for line in lines)
    with REPORT.open("a", encoding="utf-8") as handle:
        handle.write(f"\n[{stamp}]\n{body}\n")


def _describe_device() -> str:
    from echolock.audio import list_devices
    from echolock.storage import Config

    index = Config.load().input_device
    if index is None:
        return "system default"
    for device in list_devices():
        if device["index"] == index:
            return f"[{index}] {device['name']}"
    return f"[{index}] (not currently present)"


def meter() -> int:
    """A live level bar, so a silent microphone is obvious in one second."""
    import numpy as np
    import sounddevice as sd

    from echolock.storage import Config

    config = Config.load()
    print(f"Device: {_describe_device()}   speak normally, {METER_SECONDS:.0f} seconds\n")

    peak_seen = 0.0

    def callback(indata, _frames, _time, _status):
        nonlocal peak_seen
        level = float(np.max(np.abs(indata))) if indata.size else 0.0
        peak_seen = max(peak_seen, level)
        filled = int(min(level, 0.5) / 0.5 * 40)
        mark = "audible" if level >= AUDIBLE else "       "
        print(f"\r  |{'#' * filled}{'.' * (40 - filled)}| {level:6.4f}  {mark}", end="", flush=True)

    with sd.InputStream(
        samplerate=config.sample_rate, channels=1, dtype="float32",
        device=config.input_device, callback=callback,
    ):
        time.sleep(METER_SECONDS)

    print(f"\n\nHighest level seen: {peak_seen:.4f} ({_dbfs(peak_seen)})")
    if peak_seen < AUDIBLE:
        print("Nothing audible. Check the microphone is not muted before going further.")
        return 1
    print("The microphone works. Now run 'baseline', then 'locked'.")
    return 0


def capture(mode: str) -> int:
    """Record once and record what was measured, under a mode label."""
    from echolock.audio import AudioUnavailable, peak_level, record
    from echolock.features import FeatureConfig, voiced_mask
    from echolock.storage import Config

    config = Config.load()
    lines = [f"mode: {mode}", f"device: {_describe_device()}"]

    if mode == "locked":
        print(f"Lock the screen now (Win+L). Recording starts in {DELAY_SECONDS:.0f} seconds,")
        print(f"and runs for {RECORD_SECONDS:.0f}. Keep speaking through it.")
        for remaining in range(int(DELAY_SECONDS), 0, -1):
            print(f"  starting in {remaining:2d} ", end="\r", flush=True)
            time.sleep(1.0)
        print(" " * 30, end="\r")
    else:
        print(f"Speak now, for {RECORD_SECONDS:.0f} seconds.")

    try:
        audio = record(RECORD_SECONDS, config.sample_rate, device=config.input_device)
    except AudioUnavailable as exc:
        lines.append(f"FAIL  recording raised: {exc}")
        _append(lines)
        print("\n".join(lines))
        return 1

    level = peak_level(audio)
    voiced = voiced_mask(audio, FeatureConfig(sample_rate=config.sample_rate))
    voiced_frames, total_frames = int(voiced.sum()), int(voiced.size)

    lines += [
        f"peak level:    {level:.4f} ({_dbfs(level)})",
        f"voiced frames: {voiced_frames} of {total_frames}",
    ]

    # Speech is what matters, not loudness. A capture can be quiet and still
    # carry plenty of voiced frames, which is the input the verifier actually
    # consumes; a dead microphone produces neither.
    if level < AUDIBLE:
        lines.append("verdict: SILENT, nothing reached the recorder")
    elif voiced_frames < 30:
        lines.append("verdict: audible but too little speech to verify against")
    else:
        lines.append("verdict: usable speech captured")

    _append(lines)
    print("\n".join(lines))

    if mode == "locked" and level >= AUDIBLE and voiced_frames >= 30:
        print("\nThe microphone is reachable while locked. Compare the two entries in")
        print(f"{REPORT.name}: similar numbers mean locking costs nothing.")
    return 0


def main(argv: list[str]) -> int:
    mode = (argv[0] if argv else "meter").lower()
    if mode not in {"meter", "baseline", "locked"}:
        print(__doc__)
        return 2
    return meter() if mode == "meter" else capture(mode)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
