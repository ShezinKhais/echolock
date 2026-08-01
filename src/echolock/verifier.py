"""Combining the identity and liveness checks into one decision.

Both checks must pass. They defend against different attacks and neither is
sufficient alone:

* Identity alone accepts any recording of the enrolled speaker.
* Liveness alone accepts anyone willing to read the words off the screen.

Every failure path returns "locked". An exception while decoding audio, a
profile that will not load, a transcriber that is unavailable -- all of these
deny rather than allow, so a malfunction cannot become an opening. That is the
only safe default for something standing in front of a desktop session.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Protocol

import numpy as np

from .features import FeatureConfig
from .liveness import LivenessResult, check_phrase
from .voiceprint import InsufficientAudio, Voiceprint


class Transcriber(Protocol):
    """Anything that can turn audio into text."""

    def transcribe(self, audio: np.ndarray, sample_rate: int) -> str: ...


@dataclass(frozen=True)
class Decision:
    """The result of one unlock attempt."""

    unlocked: bool
    identity_ok: bool
    liveness_ok: bool
    score: float | None
    threshold: float
    liveness: LivenessResult | None
    reason: str

    @property
    def margin(self) -> float | None:
        """How far the identity score cleared (or missed) the threshold."""
        if self.score is None:
            return None
        return self.score - self.threshold


def verify(
    audio: np.ndarray,
    expected_phrase: list[str],
    voiceprint: Voiceprint,
    transcriber: Transcriber | Callable[[np.ndarray, int], str],
    cfg: FeatureConfig | None = None,
    min_phrase_ratio: float = 1.0,
) -> Decision:
    """Decide whether *audio* unlocks the session.

    *transcriber* may be an object with a ``transcribe`` method or a plain
    callable taking ``(audio, sample_rate)``; the tests use the latter to run
    the whole decision path without a speech model installed.
    """
    cfg = cfg or FeatureConfig(voiceprint.sample_rate)

    def deny(reason: str, **kwargs) -> Decision:
        return Decision(
            unlocked=False,
            identity_ok=kwargs.get("identity_ok", False),
            liveness_ok=kwargs.get("liveness_ok", False),
            score=kwargs.get("score"),
            threshold=voiceprint.threshold,
            liveness=kwargs.get("liveness"),
            reason=reason,
        )

    # -- liveness first: cheap, and its failure message is the useful one -----
    try:
        raw = (
            transcriber.transcribe(audio, cfg.sample_rate)
            if hasattr(transcriber, "transcribe")
            else transcriber(audio, cfg.sample_rate)
        )
    except Exception as exc:  # noqa: BLE001 - any transcriber failure denies
        return deny(f"could not transcribe audio: {exc}")

    liveness = check_phrase(raw, expected_phrase, min_ratio=min_phrase_ratio)

    # -- identity ------------------------------------------------------------
    try:
        score = voiceprint.score(audio, cfg)
    except InsufficientAudio as exc:
        return deny(str(exc), liveness=liveness, liveness_ok=liveness.passed)
    except Exception as exc:  # noqa: BLE001
        return deny(f"could not evaluate voice: {exc}", liveness=liveness,
                    liveness_ok=liveness.passed)

    identity_ok = score >= voiceprint.threshold

    if identity_ok and liveness.passed:
        return Decision(
            unlocked=True,
            identity_ok=True,
            liveness_ok=True,
            score=score,
            threshold=voiceprint.threshold,
            liveness=liveness,
            reason="voice and phrase both verified",
        )

    # Report both outcomes, but do not spell out which half an attacker got
    # closer on beyond what the user needs to retry honestly.
    if not liveness.passed and not identity_ok:
        reason = "phrase did not match and voice was not recognised"
    elif not liveness.passed:
        reason = f"phrase did not match ({liveness.summary})"
    else:
        reason = "voice was not recognised"

    return Decision(
        unlocked=False,
        identity_ok=identity_ok,
        liveness_ok=liveness.passed,
        score=score,
        threshold=voiceprint.threshold,
        liveness=liveness,
        reason=reason,
    )
