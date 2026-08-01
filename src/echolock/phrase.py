"""Passphrase generation.

The phrase shown on the unlock screen changes every day. Its purpose is not
secrecy -- it is displayed in plain text exactly when it is needed -- but
freshness: a recording of the enrolled speaker made on a previous day contains
the wrong words and fails the liveness check.

Two properties matter:

* **Deterministic within a day.** The same phrase must appear on every attempt
  on a given date, otherwise a user who glances at the screen and then starts
  speaking would be reading a stale phrase. Derivation is HMAC-based rather
  than seeding :mod:`random`, because the standard library gives no
  cross-version guarantee that a seeded PRNG yields the same sequence.

* **Unpredictable from outside.** The date alone must not determine the phrase.
  Each installation holds a random salt, so an attacker who knows today's date
  and reads this source still cannot work out tomorrow's phrase and prepare a
  recording (or a synthesised clip) of the enrolled speaker saying it.

:func:`ephemeral_phrase` is the stronger variant: a fresh phrase per attempt,
which shrinks the replay window from a day to a single prompt. It costs the
user nothing except that the phrase cannot be memorised in advance.
"""

from __future__ import annotations

import hmac
import secrets
from datetime import date
from hashlib import sha256

from .wordlist import WORDS

DEFAULT_WORD_COUNT = 4
SALT_BYTES = 32


def new_salt() -> str:
    """Return a fresh installation salt as hex."""
    return secrets.token_hex(SALT_BYTES)


def _indices_from_digest(digest: bytes, count: int, pool_size: int) -> list[int]:
    """Map a digest to *count* pool indices without modulo bias.

    Four bytes per index gives a 32-bit value; values in the final partial
    bucket are rejected and the digest is re-expanded, so every word remains
    equally likely regardless of the pool size.
    """
    limit = (2**32 // pool_size) * pool_size
    out: list[int] = []
    block = digest
    counter = 0
    while len(out) < count:
        for offset in range(0, len(block) - 3, 4):
            if len(out) >= count:
                break
            value = int.from_bytes(block[offset:offset + 4], "big")
            if value < limit:  # reject the biased tail
                out.append(value % pool_size)
        counter += 1
        block = sha256(digest + counter.to_bytes(4, "big")).digest()
    return out


def phrase_for(day: date, salt: str, words: int = DEFAULT_WORD_COUNT) -> list[str]:
    """Return the passphrase for *day* under this installation's *salt*."""
    if words < 1:
        raise ValueError("words must be at least 1")
    digest = hmac.new(
        salt.encode("utf-8"), day.isoformat().encode("ascii"), sha256
    ).digest()
    return [WORDS[i] for i in _indices_from_digest(digest, words, len(WORDS))]


def phrase_today(salt: str, words: int = DEFAULT_WORD_COUNT) -> list[str]:
    """Return today's passphrase (local date)."""
    return phrase_for(date.today(), salt, words)


def ephemeral_phrase(words: int = DEFAULT_WORD_COUNT) -> list[str]:
    """Return a one-shot passphrase drawn fresh from the system CSPRNG.

    Used by ``--per-attempt`` mode. Unlike the daily phrase this is never
    reproducible, so a recording is useless the moment the prompt changes.
    """
    if words < 1:
        raise ValueError("words must be at least 1")
    return [secrets.choice(WORDS) for _ in range(words)]


def format_phrase(words: list[str]) -> str:
    """Render a phrase for display."""
    return " ".join(words)
