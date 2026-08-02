"""Tests for the signal-processing layer.

The MFCC pipeline is written from scratch, so these check it against
independent references (scipy's DCT, the analytic mel formula) rather than
against its own past output.
"""

from __future__ import annotations

import numpy as np
import pytest

from echolock.features import (
    FeatureConfig,
    deltas,
    dct_matrix,
    frame_energy_db,
    frame_signal,
    hz_to_mel,
    mel_filterbank,
    mel_to_hz,
    mfcc,
    pre_emphasise,
    voiced_mask,
)

from conftest import SPEAKERS, synth_voice


class TestMelScale:
    def test_round_trip(self):
        hz = np.array([0.0, 80.0, 440.0, 1000.0, 4000.0, 8000.0])
        assert np.allclose(mel_to_hz(hz_to_mel(hz)), hz)

    def test_zero_maps_to_zero(self):
        assert hz_to_mel(0.0) == pytest.approx(0.0)

    def test_monotonic(self):
        hz = np.linspace(0, 8000, 100)
        assert np.all(np.diff(hz_to_mel(hz)) > 0)

    def test_compresses_high_frequencies(self):
        """Equal mel steps span more hertz higher up, which is the point of the scale."""
        low = mel_to_hz(200.0) - mel_to_hz(100.0)
        high = mel_to_hz(2200.0) - mel_to_hz(2100.0)
        assert high > low


class TestDCT:
    def test_matches_scipy_orthonormal_dct(self):
        scipy_fftpack = pytest.importorskip("scipy.fftpack")
        rng = np.random.RandomState(0)
        x = rng.randn(26)
        mine = dct_matrix(20, 26) @ x
        theirs = scipy_fftpack.dct(x, type=2, norm="ortho")[:20]
        assert np.allclose(mine, theirs)

    def test_is_orthonormal(self):
        m = dct_matrix(26, 26)
        assert np.allclose(m @ m.T, np.eye(26), atol=1e-12)

    def test_constant_input_concentrates_in_first_coefficient(self):
        out = dct_matrix(10, 26) @ np.ones(26)
        assert abs(out[0]) > 1.0
        assert np.allclose(out[1:], 0.0, atol=1e-12)


class TestPreEmphasis:
    def test_first_sample_unchanged(self):
        x = np.array([1.0, 2.0, 3.0])
        assert pre_emphasise(x, 0.97)[0] == 1.0

    def test_removes_dc(self):
        """A constant signal is almost entirely suppressed."""
        out = pre_emphasise(np.ones(100), 0.97)
        assert np.all(np.abs(out[1:]) < 0.05)

    def test_empty_signal(self):
        assert pre_emphasise(np.array([]), 0.97).size == 0


class TestFraming:
    def test_frame_count_and_shape(self):
        frames = frame_signal(np.arange(1000.0), 100, 50)
        assert frames.shape == (19, 100)

    def test_frames_overlap_correctly(self):
        frames = frame_signal(np.arange(100.0), 10, 5)
        assert np.array_equal(frames[0], np.arange(10.0))
        assert np.array_equal(frames[1], np.arange(5.0, 15.0))

    def test_signal_shorter_than_frame(self):
        assert frame_signal(np.zeros(10), 100, 50).shape == (0, 100)


class TestFilterbank:
    def test_shape(self):
        cfg = FeatureConfig()
        assert mel_filterbank(cfg).shape == (cfg.n_filters, cfg.n_fft // 2 + 1)

    def test_every_filter_has_energy(self):
        assert np.all(mel_filterbank(FeatureConfig()).sum(axis=1) > 0)

    def test_filters_are_non_negative(self):
        assert np.all(mel_filterbank(FeatureConfig()) >= 0)

    def test_centres_ascend(self):
        bank = mel_filterbank(FeatureConfig())
        peaks = bank.argmax(axis=1)
        assert np.all(np.diff(peaks) > 0)


class TestMFCC:
    def test_shape(self):
        cfg = FeatureConfig()
        coeffs = mfcc(synth_voice(SPEAKERS["ana"], duration=1.0), cfg)
        assert coeffs.ndim == 2 and coeffs.shape[1] == cfg.n_mfcc
        assert coeffs.shape[0] > 50

    def test_silence_is_finite(self):
        """Digital silence must not produce -inf or NaN via the log."""
        coeffs = mfcc(np.zeros(16_000))
        assert coeffs.shape[0] > 0
        assert np.all(np.isfinite(coeffs))

    def test_too_short_returns_empty(self):
        assert mfcc(np.zeros(10)).shape == (0, FeatureConfig().n_mfcc)

    def test_deterministic(self):
        signal = synth_voice(SPEAKERS["ana"], duration=1.0)
        assert np.array_equal(mfcc(signal), mfcc(signal))

    def test_robust_to_small_noise(self):
        rng = np.random.RandomState(3)
        signal = synth_voice(SPEAKERS["ana"], duration=1.0)
        clean = mfcc(signal).mean(0)
        noisy = mfcc(signal + 0.001 * rng.randn(signal.size)).mean(0)
        cosine = clean @ noisy / (np.linalg.norm(clean) * np.linalg.norm(noisy))
        assert cosine > 0.99

    def test_distinguishes_formant_structure(self):
        """Different vocal tracts must move the features more than noise does."""
        a = mfcc(synth_voice(SPEAKERS["ana"], duration=1.0)).mean(0)
        b = mfcc(synth_voice(SPEAKERS["ben"], duration=1.0)).mean(0)
        a2 = mfcc(synth_voice(SPEAKERS["ana"], duration=1.0, seed=99)).mean(0)
        assert np.linalg.norm(a - b) > np.linalg.norm(a - a2)

    def test_gain_change_is_an_offset_not_a_reshape(self):
        """Scaling amplitude shifts the log-spectrum, so higher coefficients hold."""
        signal = synth_voice(SPEAKERS["cleo"], duration=1.0)
        quiet = mfcc(signal * 0.25).mean(0)
        loud = mfcc(signal).mean(0)
        assert np.allclose(quiet[1:], loud[1:], atol=0.35)


class TestVoicedMask:
    def test_selects_loud_frames_only(self):
        sr = 16_000
        signal = np.concatenate(
            [np.zeros(sr), synth_voice(SPEAKERS["ana"], duration=1.0), np.zeros(sr)]
        )
        mask = voiced_mask(signal)
        assert 0 < mask.sum() < mask.size
        # The middle third holds the speech; most selected frames live there.
        centre = mask[mask.size // 3 : 2 * mask.size // 3]
        assert centre.mean() > 0.8

    def test_digital_silence_selects_nothing(self):
        """The absolute floor covers the relative threshold's blind spot.

        Every frame of an all-silent signal sits within the relative window of
        the loudest frame, so without an absolute floor silence would count as
        entirely voiced and could produce an embedding.
        """
        assert not voiced_mask(np.zeros(16_000)).any()

    def test_very_quiet_recording_selects_nothing(self):
        signal = synth_voice(SPEAKERS["ana"], duration=1.0) * 1e-4
        assert not voiced_mask(signal).any()

    def test_relative_threshold_still_applies_within_loud_audio(self):
        """Scaling a normal recording must not change which frames are voiced."""
        signal = synth_voice(SPEAKERS["ana"], duration=1.0)
        assert np.array_equal(voiced_mask(signal), voiced_mask(signal * 0.5))

    def test_empty_signal(self):
        assert voiced_mask(np.zeros(10)).size == 0


class TestEnergy:
    def test_louder_signal_has_higher_db(self):
        signal = synth_voice(SPEAKERS["ana"], duration=0.5)
        assert frame_energy_db(signal).mean() > frame_energy_db(signal * 0.1).mean()

    def test_silence_is_floored_not_infinite(self):
        assert np.all(np.isfinite(frame_energy_db(np.zeros(16_000))))


class TestDeltas:
    def test_shape_preserved(self):
        assert deltas(np.random.RandomState(0).randn(50, 20)).shape == (50, 20)

    def test_constant_features_give_zero_motion(self):
        assert np.allclose(deltas(np.ones((30, 5))), 0.0)

    def test_linear_ramp_gives_constant_slope(self):
        ramp = np.arange(40.0)[:, None] * np.ones((1, 3))
        middle = deltas(ramp)[10:-10]
        assert np.allclose(middle, middle[0], atol=1e-9)
        assert middle[0, 0] > 0

    def test_single_frame(self):
        assert np.allclose(deltas(np.ones((1, 5))), 0.0)

    def test_empty(self):
        assert deltas(np.empty((0, 5))).shape == (0, 5)
