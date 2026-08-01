"""Mel-frequency cepstral coefficients, implemented directly on numpy.

MFCCs compress a frame of audio into a short vector describing the shape of its
spectral envelope -- roughly, the resonances of the vocal tract that produced
it. Two people saying the same word land in different regions of that space,
which is what makes the features usable for deciding *who* is speaking rather
than *what* was said.

The pipeline is the textbook one, written out rather than imported so that each
stage is inspectable:

1. pre-emphasis, lifting the quiet high frequencies
2. framing into overlapping short windows, over which speech is ~stationary
3. a Hamming window per frame, to stop the FFT seeing frame edges as clicks
4. power spectrum via FFT
5. a triangular mel filterbank, mimicking the ear's coarser resolution at high
   frequencies
6. logarithm, matching loudness perception and turning channel gain into an
   additive offset
7. DCT, which decorrelates the filterbank energies and concentrates them in the
   low coefficients

No cepstral mean normalisation is applied. CMN would subtract each
coefficient's average over the utterance, which cancels the microphone's
colouration -- but it also cancels the speaker's average spectral shape, and
that average is precisely the signal this project needs. The cost is that a
profile enrolled on one microphone will not transfer cleanly to another; see
the README.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

# Analysis parameters. 25 ms frames at a 10 ms hop is the near-universal
# default for speech: long enough to resolve pitch, short enough that the
# vocal tract has not moved much within a frame.
FRAME_MS = 25.0
HOP_MS = 10.0
PRE_EMPHASIS = 0.97
N_FILTERS = 26
N_MFCC = 20
FMIN_HZ = 80.0        # below the lowest human f0 worth keeping
FMAX_FRACTION = 0.45  # of the sample rate, staying under Nyquist

# Frames quieter than this are treated as silence no matter how the rest of the
# recording is scaled. Speech at any usable recording level sits far above it;
# room tone typically falls below.
ABSOLUTE_SILENCE_DB = -60.0


@dataclass(frozen=True)
class FeatureConfig:
    """Knobs for the feature pipeline, kept explicit so tests can vary them."""

    sample_rate: int = 16_000
    frame_ms: float = FRAME_MS
    hop_ms: float = HOP_MS
    pre_emphasis: float = PRE_EMPHASIS
    n_filters: int = N_FILTERS
    n_mfcc: int = N_MFCC
    fmin: float = FMIN_HZ

    @property
    def frame_length(self) -> int:
        return max(1, int(round(self.sample_rate * self.frame_ms / 1000.0)))

    @property
    def hop_length(self) -> int:
        return max(1, int(round(self.sample_rate * self.hop_ms / 1000.0)))

    @property
    def fmax(self) -> float:
        return self.sample_rate * FMAX_FRACTION

    @property
    def n_fft(self) -> int:
        """Next power of two at or above the frame length."""
        return 1 << (self.frame_length - 1).bit_length()


def hz_to_mel(hz: np.ndarray | float) -> np.ndarray | float:
    """Convert hertz to the mel scale (O'Shaughnessy's formula)."""
    return 2595.0 * np.log10(1.0 + np.asarray(hz, dtype=float) / 700.0)


def mel_to_hz(mel: np.ndarray | float) -> np.ndarray | float:
    """Inverse of :func:`hz_to_mel`."""
    return 700.0 * (10.0 ** (np.asarray(mel, dtype=float) / 2595.0) - 1.0)


def pre_emphasise(signal: np.ndarray, coeff: float) -> np.ndarray:
    """Apply a first-order high-pass: ``y[n] = x[n] - a*x[n-1]``.

    Speech has far more energy at low frequencies; without this the top of the
    spectrum contributes almost nothing to the filterbank.
    """
    if signal.size == 0:
        return signal.astype(np.float64, copy=True)
    out = np.empty_like(signal, dtype=np.float64)
    out[0] = signal[0]
    out[1:] = signal[1:] - coeff * signal[:-1]
    return out


def frame_signal(signal: np.ndarray, frame_length: int, hop_length: int) -> np.ndarray:
    """Slice *signal* into overlapping frames, shape ``(n_frames, frame_length)``.

    Returns an empty array when the signal is shorter than one frame; callers
    treat that as "not enough audio" rather than an error.
    """
    if signal.size < frame_length:
        return np.empty((0, frame_length), dtype=np.float64)
    windows = np.lib.stride_tricks.sliding_window_view(signal, frame_length)
    return np.ascontiguousarray(windows[::hop_length], dtype=np.float64)


def mel_filterbank(cfg: FeatureConfig) -> np.ndarray:
    """Build the triangular mel filterbank, shape ``(n_filters, n_fft//2 + 1)``.

    Filter *i* rises linearly from mel point *i* to *i+1* and falls to *i+2*, so
    adjacent filters overlap at half amplitude and the bank tiles the band
    evenly in mel space -- narrow filters at low frequency, wide ones high up.
    """
    n_bins = cfg.n_fft // 2 + 1
    mel_points = np.linspace(
        hz_to_mel(cfg.fmin), hz_to_mel(cfg.fmax), cfg.n_filters + 2
    )
    hz_points = mel_to_hz(mel_points)
    bin_freqs = np.linspace(0.0, cfg.sample_rate / 2.0, n_bins)

    bank = np.zeros((cfg.n_filters, n_bins), dtype=np.float64)
    for i in range(cfg.n_filters):
        left, centre, right = hz_points[i], hz_points[i + 1], hz_points[i + 2]
        if right <= left:  # degenerate band; leave the filter empty
            continue
        rising = (bin_freqs - left) / max(centre - left, 1e-9)
        falling = (right - bin_freqs) / max(right - centre, 1e-9)
        bank[i] = np.clip(np.minimum(rising, falling), 0.0, None)
    return bank


def dct_matrix(n_out: int, n_in: int) -> np.ndarray:
    """Orthonormal DCT-II matrix, shape ``(n_out, n_in)``.

    Built as a matrix because ``n_in`` is the filter count (tens, not
    thousands): a small dense multiply is clearer than an FFT-based transform
    and fast enough here.
    """
    n = np.arange(n_in)
    k = np.arange(n_out)[:, None]
    basis = np.cos(np.pi * k * (2 * n + 1) / (2 * n_in))
    scale = np.full((n_out, 1), np.sqrt(2.0 / n_in))
    if n_out > 0:
        scale[0] = np.sqrt(1.0 / n_in)
    return basis * scale


def mfcc(signal: np.ndarray, cfg: FeatureConfig | None = None) -> np.ndarray:
    """Return MFCCs for *signal*, shape ``(n_frames, n_mfcc)``.

    *signal* is mono float audio, nominally in [-1, 1]. An all-silent or
    too-short input yields an empty ``(0, n_mfcc)`` array.
    """
    cfg = cfg or FeatureConfig()
    signal = np.asarray(signal, dtype=np.float64).ravel()

    emphasised = pre_emphasise(signal, cfg.pre_emphasis)
    frames = frame_signal(emphasised, cfg.frame_length, cfg.hop_length)
    if frames.shape[0] == 0:
        return np.empty((0, cfg.n_mfcc), dtype=np.float64)

    windowed = frames * np.hamming(cfg.frame_length)
    spectrum = np.fft.rfft(windowed, n=cfg.n_fft, axis=1)
    power = (np.abs(spectrum) ** 2) / cfg.n_fft

    energies = power @ mel_filterbank(cfg).T
    # Floor before the log so digital silence gives a large negative constant
    # rather than -inf, which would poison every downstream statistic.
    log_energies = np.log(np.maximum(energies, 1e-10))
    return log_energies @ dct_matrix(cfg.n_mfcc, cfg.n_filters).T


def frame_energy_db(signal: np.ndarray, cfg: FeatureConfig | None = None) -> np.ndarray:
    """Per-frame RMS energy in decibels, aligned with :func:`mfcc`'s frames."""
    cfg = cfg or FeatureConfig()
    signal = np.asarray(signal, dtype=np.float64).ravel()
    frames = frame_signal(signal, cfg.frame_length, cfg.hop_length)
    if frames.shape[0] == 0:
        return np.empty(0, dtype=np.float64)
    rms = np.sqrt(np.mean(frames**2, axis=1))
    return 20.0 * np.log10(np.maximum(rms, 1e-10))


def voiced_mask(
    signal: np.ndarray,
    cfg: FeatureConfig | None = None,
    floor_db: float = 35.0,
    absolute_floor_db: float = ABSOLUTE_SILENCE_DB,
) -> np.ndarray:
    """Boolean mask of frames loud enough to be speech.

    Two thresholds apply and a frame must clear both.

    The relative one -- *floor_db* below the loudest frame -- adapts to
    whatever level the microphone happened to record at instead of assuming a
    fixed gain. Dropping quiet frames matters because a recording that is
    mostly room tone would otherwise yield a "voiceprint" of the room.

    The absolute one exists because a relative threshold alone has a blind
    spot: in a signal that is entirely silent, the loudest frame is also
    silent, so every frame sits within *floor_db* of it and the whole recording
    is declared voiced. Real speech never approaches this floor, so requiring
    it rules out digital silence and dead microphones, which must not be able
    to produce an embedding at all.
    """
    db = frame_energy_db(signal, cfg)
    if db.size == 0:
        return np.zeros(0, dtype=bool)
    return (db >= (db.max() - floor_db)) & (db >= absolute_floor_db)


def deltas(features: np.ndarray, width: int = 2) -> np.ndarray:
    """First-order regression deltas over a ``2*width+1`` frame span.

    Captures how the spectrum moves rather than where it sits, which carries
    speaking-style information and is less sensitive to microphone colouration
    than the static coefficients.
    """
    if features.shape[0] == 0:
        return np.empty_like(features)
    if features.shape[0] == 1:
        return np.zeros_like(features)
    padded = np.pad(features, ((width, width), (0, 0)), mode="edge")
    offsets = np.arange(-width, width + 1)
    denominator = 2.0 * np.sum(offsets**2)
    out = np.zeros_like(features)
    for offset in offsets:
        if offset == 0:
            continue
        start = width + offset
        out += offset * padded[start:start + features.shape[0]]
    return out / denominator
