"""Tests for the combined unlock decision.

These exercise the security-critical logic: that both checks are required, and
that every abnormal path denies rather than allows. A fake transcriber stands
in for the speech model so the whole decision path runs without one installed.
"""

from __future__ import annotations

import numpy as np
import pytest

from echolock.verifier import Decision, verify
from echolock.voiceprint import build_voiceprint

from conftest import SPEAKERS, synth_voice, takes

PHRASE = ["lantern", "quiet", "compass", "drift"]


def fixed_transcript(text: str):
    """A transcriber that always returns *text*."""
    return lambda audio, sample_rate: text


def exploding_transcriber(audio, sample_rate):
    raise RuntimeError("speech model unavailable")


@pytest.fixture(scope="module")
def enrolled():
    return build_voiceprint(takes("ana", 8))


@pytest.fixture(scope="module")
def genuine_audio():
    return synth_voice(SPEAKERS["ana"], duration=1.6, seed=4242)


@pytest.fixture(scope="module")
def impostor_audio():
    return synth_voice(SPEAKERS["ben"], duration=1.6, seed=4242)


class TestBothChecksRequired:
    def test_right_voice_right_phrase_unlocks(self, enrolled, genuine_audio):
        decision = verify(genuine_audio, PHRASE, enrolled, fixed_transcript("lantern quiet compass drift"))
        assert decision.unlocked
        assert decision.identity_ok and decision.liveness_ok

    def test_right_voice_wrong_phrase_denies(self, enrolled, genuine_audio):
        """The replay case: the enrolled speaker, but yesterday's words."""
        decision = verify(genuine_audio, PHRASE, enrolled, fixed_transcript("harbor sugar penguin marble"))
        assert not decision.unlocked
        assert decision.identity_ok and not decision.liveness_ok
        assert "phrase" in decision.reason

    def test_wrong_voice_right_phrase_denies(self, enrolled, impostor_audio):
        """Someone else reading the prompt off the screen."""
        decision = verify(impostor_audio, PHRASE, enrolled, fixed_transcript("lantern quiet compass drift"))
        assert not decision.unlocked
        assert decision.liveness_ok and not decision.identity_ok
        assert "voice" in decision.reason

    def test_wrong_voice_wrong_phrase_denies(self, enrolled, impostor_audio):
        decision = verify(impostor_audio, PHRASE, enrolled, fixed_transcript("something else entirely"))
        assert not decision.unlocked
        assert not decision.identity_ok and not decision.liveness_ok


class TestFailClosed:
    def test_transcriber_error_denies(self, enrolled, genuine_audio):
        decision = verify(genuine_audio, PHRASE, enrolled, exploding_transcriber)
        assert not decision.unlocked
        assert "transcribe" in decision.reason

    def test_silence_denies(self, enrolled):
        decision = verify(np.zeros(16_000), PHRASE, enrolled,
                          fixed_transcript("lantern quiet compass drift"))
        assert not decision.unlocked

    def test_noise_denies(self, enrolled):
        noise = np.random.RandomState(1).randn(16_000) * 0.3
        decision = verify(noise, PHRASE, enrolled, fixed_transcript("lantern quiet compass drift"))
        assert not decision.unlocked

    def test_empty_transcript_denies(self, enrolled, genuine_audio):
        decision = verify(genuine_audio, PHRASE, enrolled, fixed_transcript(""))
        assert not decision.unlocked

    def test_scoring_error_denies(self, enrolled, genuine_audio, monkeypatch):
        """An unexpected failure inside scoring must not fall through to unlock."""
        monkeypatch.setattr(
            type(enrolled), "score",
            lambda self, signal, cfg=None: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        decision = verify(genuine_audio, PHRASE, enrolled, fixed_transcript("lantern quiet compass drift"))
        assert not decision.unlocked
        assert "boom" in decision.reason


class TestTranscriberInterface:
    def test_accepts_an_object_with_transcribe(self, enrolled, genuine_audio):
        class Model:
            def transcribe(self, audio, sample_rate):
                return "lantern quiet compass drift"

        assert verify(genuine_audio, PHRASE, enrolled, Model()).unlocked

    def test_accepts_a_plain_callable(self, enrolled, genuine_audio):
        assert verify(genuine_audio, PHRASE, enrolled,
                      fixed_transcript("lantern quiet compass drift")).unlocked


class TestDecisionReporting:
    def test_margin_is_signed_distance_from_threshold(self, enrolled, genuine_audio):
        decision = verify(genuine_audio, PHRASE, enrolled, fixed_transcript("lantern quiet compass drift"))
        assert decision.margin == pytest.approx(decision.score - decision.threshold)
        assert decision.margin > 0

    def test_impostor_margin_is_negative(self, enrolled, impostor_audio):
        decision = verify(impostor_audio, PHRASE, enrolled, fixed_transcript("lantern quiet compass drift"))
        assert decision.margin < 0

    def test_liveness_detail_is_carried(self, enrolled, genuine_audio):
        decision = verify(genuine_audio, PHRASE, enrolled, fixed_transcript("lantern quiet compass"))
        assert decision.liveness is not None
        assert decision.liveness.missing == ["drift"]

    def test_decision_is_immutable(self, enrolled, genuine_audio):
        decision = verify(genuine_audio, PHRASE, enrolled, fixed_transcript("lantern quiet compass drift"))
        with pytest.raises(Exception):
            decision.unlocked = False  # type: ignore[misc]

    def test_relaxed_ratio_is_honoured(self, enrolled, genuine_audio):
        decision = verify(
            genuine_audio, PHRASE, enrolled,
            fixed_transcript("lantern quiet compass"), min_phrase_ratio=0.75,
        )
        assert decision.unlocked


class TestReplayScenario:
    def test_yesterdays_recording_fails_todays_prompt(self, enrolled, genuine_audio):
        """End to end: real speaker, real recording, stale words."""
        yesterday = ["harbor", "sugar", "penguin", "marble"]
        today = ["lantern", "quiet", "compass", "drift"]
        # The attacker holds audio of the enrolled speaker saying yesterday's
        # phrase, and plays it at today's prompt.
        decision = verify(genuine_audio, today, enrolled,
                          fixed_transcript(" ".join(yesterday)))
        assert not decision.unlocked
        assert decision.identity_ok, "the voice really is the enrolled speaker"
        assert not decision.liveness_ok, "but the words are from the wrong day"
