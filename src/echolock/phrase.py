"""Passphrase generation.

The sentence shown on the unlock screen changes every day. Its purpose is not
secrecy, since it is displayed in plain text exactly when it is needed, but
freshness: a recording of the enrolled speaker made on a previous day contains
the wrong words and fails the liveness check.

A phrase has two parts, and the split matters. :attr:`Phrase.text` is the whole
sentence, which is what the user reads. :attr:`Phrase.keywords` is only the
words that were chosen at random, which is what gets verified. Connecting words
like "the" are unstressed and short, and speech models drop them constantly;
requiring them would fail honest attempts without adding anything, because they
are the same in every prompt and so carry no freshness.

Two properties matter for the sentence itself:

* **Deterministic within a day.** The same phrase must appear on every attempt
  on a given date, or a user who glances at the screen and then starts speaking
  would be reading a stale prompt. Derivation is HMAC-based rather than seeding
  :mod:`random`, because the standard library gives no cross-version guarantee
  that a seeded PRNG yields the same sequence.

* **Unpredictable from outside.** The date alone must not determine the phrase.
  Each installation holds a random salt, so an attacker who knows today's date
  and reads this source still cannot work out tomorrow's sentence and prepare a
  recording, or a synthesised clip, of the enrolled speaker saying it.

:func:`ephemeral_phrase` is the stronger variant: a fresh sentence per attempt,
which shrinks the replay window from a day to a single prompt.
"""

from __future__ import annotations

import hmac
import secrets
from dataclasses import dataclass
from datetime import date
from hashlib import sha256

from .wordlist import ADJECTIVES, NOUNS, VERBS

DEFAULT_KEYWORDS = 4
SALT_BYTES = 32

# Each template pairs a sentence frame with the part of speech for every slot.
# Grouped by how many random words they contain, so the setting that used to
# choose a phrase length still means something.
TEMPLATES: dict[int, tuple[tuple[str, tuple[str, ...]], ...]] = {
    3: (
        ("The {0} {1} the {2}.", ("noun", "verb", "noun")),
        ("A {0} {1} {2} the water.", ("adjective", "noun", "verb")),
        ("The {0} {1} beyond the {2}.", ("noun", "verb", "noun")),
    ),
    4: (
        ("The {0} {1} {2} the {3}.", ("adjective", "noun", "verb", "noun")),
        ("A {0} {1} {2} beyond the {3}.", ("adjective", "noun", "verb", "noun")),
        ("The {0} {1} beneath a {2} {3}.", ("noun", "verb", "adjective", "noun")),
        ("The {0} {1} near the {2} {3}.", ("noun", "verb", "adjective", "noun")),
    ),
    5: (
        ("The {0} {1} {2} the {3} {4}.", ("adjective", "noun", "verb", "adjective", "noun")),
        ("A {0} {1} {2} past the {3} {4}.", ("adjective", "noun", "verb", "adjective", "noun")),
        ("The {0} {1} above a {2} {3} and {4}.",
         ("adjective", "noun", "adjective", "noun", "noun")),
    ),
    6: (
        ("The {0} {1} {2} the {3} {4} and the {5}.",
         ("adjective", "noun", "verb", "adjective", "noun", "noun")),
        ("A {0} {1} {2} beneath the {3} {4} and {5}.",
         ("adjective", "noun", "verb", "adjective", "noun", "noun")),
    ),
}

_POOLS: dict[str, tuple[str, ...]] = {
    "adjective": ADJECTIVES,
    "noun": NOUNS,
    "verb": VERBS,
}

MIN_KEYWORDS = min(TEMPLATES)
MAX_KEYWORDS = max(TEMPLATES)


@dataclass(frozen=True)
class Phrase:
    """A prompt: the sentence to read, and the words that are checked."""

    text: str
    keywords: tuple[str, ...]

    def __str__(self) -> str:
        return self.text


def new_salt() -> str:
    """Return a fresh installation salt as hex."""
    return secrets.token_hex(SALT_BYTES)


def _values_from_digest(digest: bytes, count: int) -> list[int]:
    """Expand a digest into *count* 32-bit values, rehashing as needed."""
    out: list[int] = []
    block = digest
    counter = 0
    while len(out) < count:
        for offset in range(0, len(block) - 3, 4):
            if len(out) >= count:
                break
            out.append(int.from_bytes(block[offset:offset + 4], "big"))
        counter += 1
        block = sha256(digest + counter.to_bytes(4, "big")).digest()
    return out


def _pick(value: int, pool: tuple[str, ...]) -> str:
    """Choose from *pool* without modulo bias."""
    limit = (2**32 // len(pool)) * len(pool)
    if value >= limit:  # the biased tail; fold it back deterministically
        value = int.from_bytes(sha256(value.to_bytes(4, "big")).digest()[:4], "big") % limit
    return pool[value % len(pool)]


def _clamp_keywords(count: int) -> int:
    if count < MIN_KEYWORDS or count > MAX_KEYWORDS:
        raise ValueError(f"keywords must be between {MIN_KEYWORDS} and {MAX_KEYWORDS}")
    return count


def _fix_articles(text: str) -> str:
    """Correct "a" to "an" before a vowel.

    The templates cannot know which word will land after the article, so
    agreement is repaired once the slots are filled. Every word in the
    vocabulary is spelled the way it sounds at the front, so testing the first
    letter is enough here; no "hour" or "union" cases exist to trip it.
    """
    words = text.split()
    for i, word in enumerate(words[:-1]):
        if word.lower() == "a" and words[i + 1][:1].lower() in "aeiou":
            words[i] = "An" if word[0].isupper() else "an"
    return " ".join(words)


def _build(values: list[int], keywords: int) -> Phrase:
    """Assemble a sentence from a stream of random values."""
    options = TEMPLATES[keywords]
    frame, slots = options[values[0] % len(options)]
    chosen = [_pick(values[i + 1], _POOLS[kind]) for i, kind in enumerate(slots)]
    text = _fix_articles(frame.format(*chosen).capitalize())
    return Phrase(text=text, keywords=tuple(chosen))


def phrase_for(day: date, salt: str, keywords: int = DEFAULT_KEYWORDS) -> Phrase:
    """Return the passphrase for *day* under this installation's *salt*."""
    keywords = _clamp_keywords(keywords)
    digest = hmac.new(
        salt.encode("utf-8"), day.isoformat().encode("ascii"), sha256
    ).digest()
    return _build(_values_from_digest(digest, keywords + 1), keywords)


def phrase_today(salt: str, keywords: int = DEFAULT_KEYWORDS) -> Phrase:
    """Return today's passphrase (local date)."""
    return phrase_for(date.today(), salt, keywords)


def ephemeral_phrase(keywords: int = DEFAULT_KEYWORDS) -> Phrase:
    """Return a one-shot passphrase drawn fresh from the system CSPRNG.

    Used by per-attempt mode. Unlike the daily phrase this is never
    reproducible, so a recording is useless the moment the prompt changes.
    """
    keywords = _clamp_keywords(keywords)
    values = [secrets.randbits(32) for _ in range(keywords + 1)]
    return _build(values, keywords)


def format_phrase(phrase: Phrase | str) -> str:
    """Render a phrase for display."""
    return phrase.text if isinstance(phrase, Phrase) else str(phrase)


SENTENCE_PROMPTS = (
    "The quick brown fox jumps over the lazy dog.",
    "Sunlight filtered through the tall kitchen window.",
    "Seven bright copper kettles lined the wooden shelf.",
    "She parked the car and walked the rest of the way.",
    "Autumn leaves gathered along the narrow garden path.",
    "The train arrives at quarter past eleven tomorrow.",
    "Fresh bread and strong coffee for breakfast again.",
    "A grey cat slept beneath the blue painted bench.",
    "Distant thunder rolled across the open valley floor.",
    "He counted every step from the door to the corner.",
)


def enrolment_prompts(count: int, keywords: int = DEFAULT_KEYWORDS) -> list[str]:
    """Return *count* things to read aloud during enrolment.

    Alternates everyday sentences with generated passphrase sentences. The two
    read differently: a familiar sentence flows, while a sentence assembled from
    random words is read more deliberately because the speaker cannot anticipate
    the next one. Enrolling only on the first style builds a profile of a voice
    the user does not use at the unlock prompt, which costs real margin.
    """
    prompts: list[str] = []
    for i in range(count):
        if i % 2 == 0:
            prompts.append(SENTENCE_PROMPTS[(i // 2) % len(SENTENCE_PROMPTS)])
        else:
            prompts.append(ephemeral_phrase(keywords).text)
    return prompts
