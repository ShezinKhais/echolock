"""Shared fixtures.

The tests need speech-like audio from several distinguishable speakers, and
they need it without a microphone, a recording session, or anyone's real voice
in the repository. So the suite synthesises voices with a source-filter model:
a buzzy glottal source at some fundamental frequency, shaped by a handful of
formant resonances. Formant positions are what distinguish one vocal tract from
another, so speakers built with different formants are genuinely different
signals for the feature pipeline -- not merely noise with different seeds.

Filtering is done in the frequency domain with numpy alone, keeping the test
suite free of extra dependencies.
"""

from __future__ import annotations

import numpy as np
import pytest

SAMPLE_RATE = 16_000

# (centre Hz, bandwidth Hz, amplitude) per formant. The first three roughly
# correspond to distinct vowel-space positions and vocal tract lengths.
SPEAKERS: dict[str, list[tuple[float, float, float]]] = {
    "ana": [(730, 90, 1.0), (1090, 110, 0.55), (2440, 160, 0.28)],
    "ben": [(300, 80, 1.0), (870, 100, 0.60), (2240, 150, 0.30)],
    "cleo": [(660, 85, 1.0), (1720, 120, 0.70), (2410, 160, 0.40)],
    "dev": [(520, 95, 1.0), (1300, 115, 0.50), (2900, 170, 0.35)],
}


def synth_voice(
    formants: list[tuple[float, float, float]],
    f0: float = 120.0,
    duration: float = 1.6,
    seed: int = 0,
    sample_rate: int = SAMPLE_RATE,
) -> np.ndarray:
    """Synthesise a speech-like signal for a given formant structure."""
    rng = np.random.RandomState(seed)
    n = int(sample_rate * duration)
    t = np.arange(n) / sample_rate

    # Source: a pulse train with natural-sounding pitch drift and jitter, so
    # repeated takes from one speaker vary the way real takes do.
    wobble = 1.0 + 0.03 * np.sin(2 * np.pi * 3.1 * t + rng.uniform(0, 6.28))
    drift = 1.0 + 0.02 * np.cumsum(rng.randn(n)) / np.sqrt(n)
    phase = 2 * np.pi * np.cumsum(f0 * wobble * drift) / sample_rate
    source = np.sign(np.sin(phase)) * 0.5 + 0.5 * np.sin(phase)

    # Filter: sum of Lorentzian resonances applied to the spectrum.
    spectrum = np.fft.rfft(source)
    freqs = np.fft.rfftfreq(n, 1 / sample_rate)
    envelope = np.zeros_like(freqs)
    for centre, bandwidth, amplitude in formants:
        envelope += amplitude / (1.0 + ((freqs - centre) / (bandwidth / 2)) ** 2)
    envelope += 0.02  # a little broadband energy so nothing is exactly zero

    shaped = np.fft.irfft(spectrum * envelope, n=n)
    peak = np.max(np.abs(shaped))
    if peak > 0:
        shaped = shaped / peak
    return (shaped + 0.002 * rng.randn(n)).astype(np.float64)


def takes(speaker: str, count: int, *, offset: int = 0, duration: float = 1.6) -> list[np.ndarray]:
    """Return *count* varied recordings from one synthetic speaker."""
    formants = SPEAKERS[speaker]
    return [
        synth_voice(
            formants,
            f0=110.0 + 4.0 * ((i + offset) % 5),
            duration=duration,
            seed=1000 * (offset + i) + len(speaker),
        )
        for i in range(count)
    ]


@pytest.fixture(scope="session")
def speaker_takes() -> dict[str, list[np.ndarray]]:
    """Twelve recordings for each synthetic speaker."""
    return {name: takes(name, 12) for name in SPEAKERS}


@pytest.fixture
def silence() -> np.ndarray:
    return np.zeros(SAMPLE_RATE, dtype=np.float64)


@pytest.fixture
def echolock_home(tmp_path, monkeypatch):
    """Point the profile directory at a temporary location."""
    home = tmp_path / "profile"
    monkeypatch.setenv("ECHOLOCK_HOME", str(home))
    return home
