"""Liveness check: did the speaker say *this* prompt, now?

This is the half of verification that defeats replay. The voiceprint answers
"does this sound like the enrolled speaker", which a recording of that speaker
also satisfies. Requiring today's words to be spoken means an old recording
carries the wrong content and fails, so an attacker needs audio of the right
person saying the right words from the right day.

Matching has to tolerate imperfect transcription. A small offline speech model
mishears word endings, drops short words, and inserts filler, so requiring an
exact string would reject the legitimate user constantly, and a check the
user disables is worth nothing. Words therefore match within a small edit
distance, scaled to word length, and must appear in the prompted order:
insertions ("um", "the") are ignored, but reordering is not accepted.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_WORD_RE = re.compile(r"[a-z0-9']+")


def normalise(text: str) -> list[str]:
    """Lowercase *text* and split it into comparable word tokens."""
    return _WORD_RE.findall(text.lower())


def edit_distance(a: str, b: str, cap: int | None = None) -> int:
    """Levenshtein distance between *a* and *b*.

    Uses two rows rather than a full matrix; *cap* allows early exit once the
    distance cannot come in under the caller's tolerance.
    """
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    if cap is not None and abs(len(a) - len(b)) > cap:
        return cap + 1

    previous = list(range(len(b) + 1))
    for i, ch_a in enumerate(a, start=1):
        current = [i]
        for j, ch_b in enumerate(b, start=1):
            current.append(
                min(
                    previous[j] + 1,          # deletion
                    current[j - 1] + 1,       # insertion
                    previous[j - 1] + (ch_a != ch_b),  # substitution
                )
            )
        if cap is not None and min(current) > cap:
            return cap + 1
        previous = current
    return previous[-1]


def tolerance_for(word: str) -> int:
    """Edit distance allowed when matching *word*.

    Short words get no slack: at length 4 a single edit already reaches several
    other pool entries, so allowing one would blur distinct prompts together.
    """
    if len(word) <= 5:
        return 0
    if len(word) <= 8:
        return 1
    return 2


def words_match(expected: str, heard: str) -> bool:
    """Whether *heard* is close enough to count as *expected*."""
    cap = tolerance_for(expected)
    return edit_distance(expected, heard, cap=cap) <= cap


@dataclass(frozen=True)
class LivenessResult:
    """Outcome of comparing a transcript against the prompted phrase."""

    passed: bool
    ratio: float
    matched: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    transcript: str = ""

    @property
    def summary(self) -> str:
        if self.passed:
            return f"phrase recognised ({len(self.matched)}/{len(self.matched) + len(self.missing)} words)"
        if not self.transcript.strip():
            return "nothing was transcribed"
        return f"missing: {', '.join(self.missing)}"


def check_phrase(
    transcript: str,
    expected: list[str],
    min_ratio: float = 1.0,
) -> LivenessResult:
    """Check whether *transcript* contains *expected*, in order.

    *min_ratio* is the fraction of prompted words that must be found. It
    defaults to 1.0, every word, because the prompt is visible on screen
    and there is no reason a cooperating user cannot read all of it. Lowering
    it trades replay resistance for tolerance of a poor microphone.
    """
    if not expected:
        raise ValueError("expected phrase is empty")

    heard = normalise(transcript)
    matched: list[str] = []
    missing: list[str] = []

    cursor = 0
    for word in expected:
        found_at = None
        for index in range(cursor, len(heard)):
            if words_match(word, heard[index]):
                found_at = index
                break
        if found_at is None:
            missing.append(word)
        else:
            matched.append(word)
            cursor = found_at + 1  # enforce order; consume up to this token

    ratio = len(matched) / len(expected)
    return LivenessResult(
        passed=ratio >= min_ratio,
        ratio=ratio,
        matched=matched,
        missing=missing,
        transcript=transcript.strip(),
    )
