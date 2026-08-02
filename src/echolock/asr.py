"""Offline speech recognition for the liveness check.

Vosk runs entirely on the local machine from a downloaded model, which is the
only acceptable arrangement here: sending someone's voice to a web service in
order to unlock their own desktop would leak far more than it protects.

Recognition is constrained to the passphrase pool rather than open vocabulary.
Vosk accepts a word list at construction and will then only emit those words,
which raises accuracy sharply on a small model and removes a whole class of
failure where a rare word gets transcribed as something unrelated. It does not
weaken the check: the prompt is drawn from that same pool, and a recording of
the wrong words still cannot produce the right ones.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import numpy as np

from .audio import to_int16
from .wordlist import WORDS

MODEL_ENV = "VOSK_MODEL_PATH"


class SpeechUnavailable(RuntimeError):
    """Raised when the offline speech model cannot be loaded."""


def find_model(explicit: str | None = None) -> Path:
    """Locate the Vosk model directory.

    Searches, in order: the explicit argument, ``VOSK_MODEL_PATH``, and a
    ``models/`` directory beside the profile or the working directory.
    """
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit).expanduser())
    env = os.environ.get(MODEL_ENV)
    if env:
        candidates.append(Path(env).expanduser())

    from .storage import profile_dir

    for base in (profile_dir(), Path.cwd()):
        models = base / "models"
        if models.is_dir():
            candidates.extend(sorted(p for p in models.iterdir() if p.is_dir()))

    for candidate in candidates:
        if candidate.is_dir() and (candidate / "am").exists():
            return candidate

    # Report where the search actually looked. Without this the message is
    # unactionable when a model *is* installed but somewhere unexpected, which
    # is the confusing case: the user can see the folder and the tool cannot.
    if candidates:
        searched = "\n".join(
            f"    {path}"
            f"{'' if path.is_dir() else '   (does not exist)'}"
            f"{'   (no am/ subfolder, so not a model)' if path.is_dir() and not (path / 'am').exists() else ''}"
            for path in candidates
        )
        detail = f"Looked in:\n{searched}\n\n"
    else:
        detail = (
            f"Nothing to look at: no {MODEL_ENV} is set and neither\n"
            f"    {profile_dir() / 'models'}\n    {Path.cwd() / 'models'}\n"
            "exists.\n\n"
        )

    raise SpeechUnavailable(
        "no Vosk model found.\n\n"
        f"{detail}"
        "Download a small English model from https://alphacephei.com/vosk/models "
        "(vosk-model-small-en-us is enough), unpack it, and either put the "
        f"extracted folder in\n    {profile_dir() / 'models'}\n"
        f"or point {MODEL_ENV} at it."
    )


class VoskTranscriber:
    """Wraps a local Vosk model, restricted to the passphrase vocabulary."""

    def __init__(self, model_path: str | Path | None = None, sample_rate: int = 16_000):
        try:
            from vosk import KaldiRecognizer, Model, SetLogLevel  # noqa: PLC0415
        except Exception as exc:  # noqa: BLE001
            raise SpeechUnavailable(
                "speech recognition needs the 'vosk' package: "
                "pip install echolock[speech]"
            ) from exc

        SetLogLevel(-1)  # keep Kaldi's chatter out of the terminal UI
        self._path = find_model(str(model_path) if model_path else None)
        self._model = Model(str(self._path))
        self._sample_rate = sample_rate
        self._recogniser_cls = KaldiRecognizer
        # "[unk]" lets the decoder emit an explicit unknown rather than forcing
        # every sound onto the nearest pool word, which would let arbitrary
        # speech drift into a passing transcript.
        self._grammar = json.dumps(list(WORDS) + ["[unk]"])

    @property
    def model_path(self) -> Path:
        return self._path

    def transcribe(self, audio: np.ndarray, sample_rate: int | None = None) -> str:
        """Return the recognised text for *audio*."""
        rate = sample_rate or self._sample_rate
        recogniser = self._recogniser_cls(self._model, rate, self._grammar)
        recogniser.AcceptWaveform(to_int16(audio))
        result = json.loads(recogniser.FinalResult())
        return str(result.get("text", ""))
