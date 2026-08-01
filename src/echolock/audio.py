"""Microphone capture.

This is the one module that needs real hardware, so it is kept deliberately
thin and its dependency is imported lazily. Everything else -- features,
scoring, liveness, the decision itself -- runs on plain arrays and stays
testable on a machine with no sound card, which is also what lets the test
suite cover the security-critical paths in continuous integration.
"""

from __future__ import annotations

import numpy as np

DEFAULT_SAMPLE_RATE = 16_000


class AudioUnavailable(RuntimeError):
    """Raised when no working capture device can be used."""


def _sounddevice():
    """Import sounddevice on demand, with an actionable message if missing."""
    try:
        import sounddevice  # noqa: PLC0415 - deliberately deferred
    except Exception as exc:  # noqa: BLE001 - ImportError or a backend failure
        raise AudioUnavailable(
            "microphone support needs the 'sounddevice' package: "
            "pip install echolock[audio]"
        ) from exc
    return sounddevice


def list_devices() -> list[dict]:
    """Return the available input devices."""
    sd = _sounddevice()
    try:
        return [
            {"index": i, "name": d["name"], "channels": d["max_input_channels"]}
            for i, d in enumerate(sd.query_devices())
            if d["max_input_channels"] > 0
        ]
    except Exception as exc:  # noqa: BLE001
        raise AudioUnavailable(f"could not query audio devices: {exc}") from exc


def record(
    seconds: float,
    sample_rate: int = DEFAULT_SAMPLE_RATE,
    device: int | None = None,
) -> np.ndarray:
    """Capture *seconds* of mono audio as float32 in [-1, 1]."""
    if seconds <= 0:
        raise ValueError("seconds must be positive")
    sd = _sounddevice()
    frames = int(seconds * sample_rate)
    try:
        buffer = sd.rec(
            frames, samplerate=sample_rate, channels=1, dtype="float32", device=device
        )
        sd.wait()
    except Exception as exc:  # noqa: BLE001
        raise AudioUnavailable(f"recording failed: {exc}") from exc
    return np.asarray(buffer, dtype=np.float64).ravel()


def peak_level(signal: np.ndarray) -> float:
    """Peak absolute amplitude, for telling the user their microphone is live."""
    if signal.size == 0:
        return 0.0
    return float(np.max(np.abs(signal)))


def looks_clipped(signal: np.ndarray, threshold: float = 0.99) -> bool:
    """Whether the recording is likely clipped and should be redone.

    Clipping flattens waveform peaks, which distorts the spectrum the features
    are built from, so it is worth catching during enrolment rather than
    baking into the profile.
    """
    if signal.size == 0:
        return False
    return float(np.mean(np.abs(signal) >= threshold)) > 0.005


def to_int16(signal: np.ndarray) -> bytes:
    """Convert float audio to 16-bit PCM bytes, the format speech models expect."""
    clipped = np.clip(np.asarray(signal, dtype=np.float64), -1.0, 1.0)
    return (clipped * 32767.0).astype(np.int16).tobytes()
