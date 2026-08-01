"""End-to-end checks against real synthesised speech.

The rest of the suite runs on a source-filter model of a voice, which is fast,
deterministic, and needs no dependencies -- but is not speech. These tests use
Windows' built-in speech synthesiser to produce genuine recordings from several
distinct voices, then run the whole pipeline over them: enrol one speaker,
transcribe with the real offline model, and check the unlock decision.

They are skipped unless the machine can supply both halves, so ordinary runs and
continuous integration are unaffected:

* Windows with System.Speech available, for generating the audio
* a Vosk model on disk, found via ``VOSK_MODEL_PATH``

This is where the default sensitivity was measured. Synthetic speakers sit
further apart than real voices do, and a threshold that looked safe against
them admitted impostors here; see :data:`echolock.voiceprint.DEFAULT_SENSITIVITY`.
"""

from __future__ import annotations

import os
import subprocess
import sys
import wave
from pathlib import Path

import numpy as np
import pytest

from echolock.features import FeatureConfig
from echolock.verifier import verify
from echolock.voiceprint import build_voiceprint

pytestmark = pytest.mark.skipif(
    sys.platform != "win32" or not os.environ.get("VOSK_MODEL_PATH"),
    reason="needs Windows speech synthesis and VOSK_MODEL_PATH",
)

PHRASE = ["peacock", "yodel", "daisy", "vendor"]
OTHER_PHRASE = ["harbor", "sugar", "penguin", "marble"]

SENTENCES = [
    "The quick brown fox jumps over the lazy dog.",
    "Sunlight filtered through the tall kitchen window.",
    "Seven bright copper kettles lined the wooden shelf.",
    "She parked the car and walked the rest of the way.",
    "Autumn leaves gathered along the narrow garden path.",
    "The train arrives at quarter past eleven tomorrow.",
    "Fresh bread and strong coffee for breakfast again.",
    "A grey cat slept beneath the blue painted bench.",
]

ENROLLED = "Microsoft David Desktop"
IMPOSTORS = ("Microsoft Zira Desktop", "Microsoft Hazel Desktop")


def _speak(voice: str, text: str, path: Path, rate: int) -> bool:
    """Render *text* in *voice* to a wav file. False if the voice is missing."""
    script = (
        "Add-Type -AssemblyName System.Speech; "
        "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
        f"try {{ $s.SelectVoice('{voice}') }} catch {{ exit 2 }}; "
        f"$s.Rate = {rate}; $s.SetOutputToWaveFile('{path}'); "
        f"$s.Speak('{text}'); $s.Dispose()"
    )
    result = subprocess.run(
        ["powershell", "-NoProfile", "-Command", script],
        capture_output=True, timeout=120,
    )
    return result.returncode == 0 and path.exists()


def _load(path: Path) -> np.ndarray:
    """Read a wav file as mono float audio at 16 kHz."""
    with wave.open(str(path), "rb") as handle:
        rate, frames = handle.getframerate(), handle.getnframes()
        audio = np.frombuffer(handle.readframes(frames), dtype=np.int16)
    audio = audio.astype(np.float64) / 32768.0
    if rate != 16_000:
        target = np.linspace(0, len(audio) / rate, int(len(audio) * 16_000 / rate), endpoint=False)
        audio = np.interp(target, np.arange(len(audio)) / rate, audio)
    return audio


@pytest.fixture(scope="module")
def corpus(tmp_path_factory) -> dict:
    """Synthesise the recordings once for the whole module."""
    directory = tmp_path_factory.mktemp("speech")
    data: dict = {"enrol": [], "impostors": {}}

    for index, sentence in enumerate(SENTENCES):
        path = directory / f"enrol_{index}.wav"
        if not _speak(ENROLLED, sentence, path, rate=(index % 3) - 1):
            pytest.skip(f"voice unavailable: {ENROLLED}")
        data["enrol"].append(_load(path))

    for name, phrase, key in (
        (ENROLLED, PHRASE, "genuine_right_phrase"),
        (ENROLLED, OTHER_PHRASE, "genuine_wrong_phrase"),
    ):
        path = directory / f"{key}.wav"
        if not _speak(name, " ".join(phrase), path, rate=-1):
            pytest.skip(f"voice unavailable: {name}")
        data[key] = _load(path)

    for voice in IMPOSTORS:
        tag = voice.split()[1]
        path = directory / f"impostor_{tag}.wav"
        if _speak(voice, " ".join(PHRASE), path, rate=-1):
            data["impostors"][tag] = _load(path)
    if not data["impostors"]:
        pytest.skip("no impostor voices available")
    return data


@pytest.fixture(scope="module")
def transcriber():
    from echolock.asr import SpeechUnavailable, VoskTranscriber

    try:
        return VoskTranscriber()
    except SpeechUnavailable as exc:
        pytest.skip(str(exc))


@pytest.fixture(scope="module")
def profile(corpus):
    return build_voiceprint(corpus["enrol"], FeatureConfig())


class TestRealTranscription:
    def test_recognises_the_phrase(self, corpus, transcriber):
        heard = transcriber.transcribe(corpus["genuine_right_phrase"], 16_000)
        assert all(word in heard for word in PHRASE), heard

    def test_does_not_hallucinate_the_phrase(self, corpus, transcriber):
        """A different utterance must not transcribe into the prompted words."""
        heard = transcriber.transcribe(corpus["genuine_wrong_phrase"], 16_000)
        assert not any(word in heard for word in PHRASE), heard


class TestRealUnlock:
    def test_enrolled_speaker_with_correct_phrase_unlocks(self, corpus, profile, transcriber):
        decision = verify(corpus["genuine_right_phrase"], PHRASE, profile, transcriber)
        assert decision.unlocked, decision.reason

    def test_replayed_recording_of_the_owner_is_refused(self, corpus, profile, transcriber):
        """The attack the rotating phrase exists to stop, on real audio."""
        decision = verify(corpus["genuine_wrong_phrase"], PHRASE, profile, transcriber)
        assert not decision.unlocked
        assert decision.identity_ok, "it really is the enrolled speaker"
        assert not decision.liveness_ok, "but the words are from another day"

    def test_impostors_reading_the_prompt_are_refused(self, corpus, profile, transcriber):
        """The other attack: a stranger who can see the screen."""
        for tag, audio in corpus["impostors"].items():
            decision = verify(audio, PHRASE, profile, transcriber)
            assert not decision.unlocked, f"{tag} was accepted"
            assert decision.liveness_ok, f"{tag} did read the prompt correctly"
            assert not decision.identity_ok, f"{tag} passed the voice check"

    def test_genuine_scores_beat_impostors_by_a_clear_margin(self, corpus, profile):
        genuine = profile.score(corpus["genuine_right_phrase"])
        impostors = [profile.score(a) for a in corpus["impostors"].values()]
        assert genuine - max(impostors) > 0.3
