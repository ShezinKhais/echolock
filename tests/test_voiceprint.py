"""Tests for enrolment and speaker scoring.

The important claim this project makes is that the voiceprint separates the
enrolled speaker from everyone else. These tests check that claim directly, on
every synthetic speaker in turn, rather than only asserting that the plumbing
runs.
"""

from __future__ import annotations

import numpy as np
import pytest

from echolock.features import FeatureConfig
from echolock.voiceprint import (
    MAX_THRESHOLD_LENIENCY,
    InsufficientAudio,
    Voiceprint,
    build_voiceprint,
    embed,
)

from conftest import SPEAKERS


class TestEmbedding:
    def test_shape_is_four_blocks_of_coefficients(self):
        """mean and spread, of the coefficients and of their deltas."""
        cfg = FeatureConfig()
        assert embed(_take("ana"), cfg).shape == (4 * cfg.n_mfcc,)

    def test_finite(self):
        assert np.all(np.isfinite(embed(_take("ana"))))

    def test_deterministic(self):
        signal = _take("ana")
        assert np.array_equal(embed(signal), embed(signal))

    def test_rejects_signal_shorter_than_a_frame(self):
        with pytest.raises(InsufficientAudio):
            embed(np.zeros(10))

    def test_rejects_too_little_speech(self):
        with pytest.raises(InsufficientAudio, match="voiced frames"):
            embed(_take("ana", duration=0.2))

    def test_same_speaker_embeddings_cluster(self):
        """Different takes from one speaker sit closer than takes from two."""
        a1, a2 = _take("ana", seed=1), _take("ana", seed=2)
        b1 = _take("ben", seed=1)
        within = np.linalg.norm(embed(a1) - embed(a2))
        between = np.linalg.norm(embed(a1) - embed(b1))
        assert between > within


class TestEnrolment:
    def test_requires_several_recordings(self):
        with pytest.raises(ValueError, match="at least 3"):
            build_voiceprint([_take("ana")])

    def test_records_sample_count_and_rate(self):
        cfg = FeatureConfig()
        print_ = build_voiceprint(_takes("ana", 8), cfg)
        assert print_.n_samples == 8
        assert print_.sample_rate == cfg.sample_rate

    def test_scale_is_never_zero(self):
        """A zero scale would divide by nothing and dominate the distance."""
        assert np.all(build_voiceprint(_takes("ana", 8)).scale > 0)

    def test_threshold_admits_every_enrolment_sample(self):
        """A profile that rejects its own enrolment could only ever deny."""
        samples = _takes("ana", 8)
        print_ = build_voiceprint(samples)
        assert all(print_.score(s) >= print_.threshold for s in samples)

    def test_calibration_is_reported(self):
        calibration = build_voiceprint(_takes("ana", 8)).calibration
        assert {"loo_mean", "loo_std", "loo_min", "loo_max", "sensitivity"} <= calibration.keys()

    def test_higher_sensitivity_lowers_the_bar(self):
        samples = _takes("ana", 8)
        strict = build_voiceprint(samples, sensitivity=1.0)
        lenient = build_voiceprint(samples, sensitivity=5.0)
        assert lenient.threshold <= strict.threshold


class TestThresholdClamp:
    """The guard against a bad enrolment session producing a permissive profile."""

    def test_threshold_never_goes_below_the_cap(self):
        """An absurd sensitivity must not open the profile to everyone."""
        print_ = build_voiceprint(_takes("ana", 8), sensitivity=50.0)
        assert print_.threshold >= MAX_THRESHOLD_LENIENCY

    def test_clamping_is_reported(self):
        print_ = build_voiceprint(_takes("ana", 8), sensitivity=50.0)
        assert print_.calibration["clamped"] is True

    def test_normal_enrolment_is_not_clamped(self):
        print_ = build_voiceprint(_takes("ana", 8))
        assert print_.calibration["clamped"] is False
        assert print_.calibration["enrolment_pass_rate"] == 1.0

    def test_clamped_profile_still_rejects_impostors(self, speaker_takes):
        """The point of the cap: even a degenerate profile keeps others out."""
        print_ = build_voiceprint(speaker_takes["ana"][:8], sensitivity=50.0)
        impostors = [
            take
            for name, samples in speaker_takes.items()
            if name != "ana"
            for take in samples[8:]
        ]
        assert not any(print_.matches(t) for t in impostors)

    def test_inconsistent_enrolment_lowers_the_pass_rate(self):
        """Mixing two speakers is the shape of a bad enrolment session."""
        mixed = _takes("ana", 5) + _takes("ben", 3)
        print_ = build_voiceprint(mixed)
        assert print_.calibration["loo_std"] > build_voiceprint(_takes("ana", 8)).calibration["loo_std"]


class TestSeparation:
    """The core claim: accept the enrolled speaker, reject everyone else.

    Stated as the two rates a speaker-verification system is actually measured
    by. They are asserted asymmetrically on purpose. A false accept is a
    security failure and is required to be zero. A false reject is a usability
    failure, annoying but never dangerous, so it is bounded rather than
    forbidden, because no biometric threshold accepts every genuine attempt and
    a test demanding that would only be satisfiable by loosening the threshold
    until impostors got in.
    """

    @pytest.mark.parametrize("target", sorted(SPEAKERS))
    def test_no_impostor_is_ever_accepted(self, target, speaker_takes):
        print_ = build_voiceprint(speaker_takes[target][:8])
        impostors = [
            take
            for name, samples in speaker_takes.items()
            if name != target
            for take in samples[8:]
        ]
        accepted = [t for t in impostors if print_.matches(t)]
        assert not accepted, f"{len(accepted)}/{len(impostors)} impostors accepted"

    def test_genuine_acceptance_rate_is_high(self, speaker_takes):
        """Measured across every speaker, not asserted per sample."""
        accepted = total = 0
        for target, samples in speaker_takes.items():
            print_ = build_voiceprint(samples[:8])
            for take in samples[8:]:
                total += 1
                accepted += bool(print_.matches(take))
        assert accepted / total >= 0.90, f"only {accepted}/{total} genuine takes accepted"

    @pytest.mark.parametrize("target", sorted(SPEAKERS))
    def test_score_margin_is_wide(self, target, speaker_takes):
        """Genuine and impostor scores should not merely differ but be far apart."""
        print_ = build_voiceprint(speaker_takes[target][:8])
        genuine = [print_.score(t) for t in speaker_takes[target][8:]]
        impostor = [
            print_.score(t)
            for name, samples in speaker_takes.items()
            if name != target
            for t in samples[8:]
        ]
        assert min(genuine) - max(impostor) > 1.0

    def test_silence_does_not_authenticate(self):
        print_ = build_voiceprint(_takes("ana", 8))
        with pytest.raises(InsufficientAudio):
            print_.score(np.zeros(16_000))

    def test_noise_does_not_authenticate(self):
        print_ = build_voiceprint(_takes("ana", 8))
        noise = np.random.RandomState(0).randn(16_000) * 0.3
        assert not print_.matches(noise)


class TestPersistence:
    def test_round_trip(self, tmp_path):
        original = build_voiceprint(_takes("ana", 8))
        path = tmp_path / "voiceprint.npz"
        original.save(path)
        loaded = Voiceprint.load(path)

        assert np.allclose(loaded.centroid, original.centroid)
        assert np.allclose(loaded.scale, original.scale)
        assert loaded.threshold == pytest.approx(original.threshold)
        assert loaded.n_samples == original.n_samples
        assert loaded.sample_rate == original.sample_rate

    def test_loaded_profile_scores_identically(self, tmp_path):
        original = build_voiceprint(_takes("ana", 8))
        path = tmp_path / "voiceprint.npz"
        original.save(path)
        sample = _take("ana", seed=555)
        assert Voiceprint.load(path).score(sample) == pytest.approx(original.score(sample))

    def test_writes_readable_sidecar(self, tmp_path):
        import json

        path = tmp_path / "voiceprint.npz"
        build_voiceprint(_takes("ana", 8)).save(path)
        meta = json.loads(path.with_suffix(".json").read_text(encoding="utf-8"))
        assert meta["n_samples"] == 8 and "calibration" in meta

    def test_no_audio_is_stored(self, tmp_path):
        """Only summary statistics persist, never the recordings themselves."""
        path = tmp_path / "voiceprint.npz"
        print_ = build_voiceprint(_takes("ana", 8))
        print_.save(path)
        with np.load(path, allow_pickle=False) as data:
            stored = set(data.keys())
        assert stored == {"centroid", "scale", "threshold", "meta"}
        assert print_.centroid.size == 4 * FeatureConfig().n_mfcc  # far too small to be audio

    def test_rejects_unknown_version(self, tmp_path):
        import json

        path = tmp_path / "voiceprint.npz"
        print_ = build_voiceprint(_takes("ana", 8))
        meta = print_._meta() | {"version": 999}
        np.savez(
            path,
            centroid=print_.centroid,
            scale=print_.scale,
            threshold=np.array([print_.threshold]),
            meta=np.array([json.dumps(meta)]),
        )
        with pytest.raises(ValueError, match="not supported"):
            Voiceprint.load(path)


# -- helpers ---------------------------------------------------------------

def _take(speaker: str, seed: int = 0, duration: float = 1.6) -> np.ndarray:
    from conftest import synth_voice

    return synth_voice(SPEAKERS[speaker], duration=duration, seed=seed)


def _takes(speaker: str, count: int) -> list[np.ndarray]:
    from conftest import takes

    return takes(speaker, count)
