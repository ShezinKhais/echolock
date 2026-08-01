"""Tests for passphrase generation and the word pool."""

from __future__ import annotations

from collections import Counter
from datetime import date

import pytest

from echolock.phrase import (
    ephemeral_phrase,
    format_phrase,
    new_salt,
    phrase_for,
    phrase_today,
)
from echolock.wordlist import WORDS, validate_pool


class TestWordPool:
    def test_pool_is_valid(self):
        validate_pool()

    def test_pool_is_large_enough_to_vary(self):
        assert len(WORDS) >= 200

    def test_no_two_words_are_confusable(self):
        """No two pool entries may sit within the matcher's edit tolerance.

        If they did, a recording of one word would satisfy a prompt containing
        the other, so two different daily phrases could accept the same audio --
        which is precisely the property the rotating phrase exists to prevent.

        The check applies at least one edit even to short words, where the
        matcher currently demands an exact hit. That margin means loosening
        :func:`tolerance_for` later cannot silently reintroduce a collision;
        this test fails first.
        """
        from echolock.liveness import edit_distance, tolerance_for

        collisions = []
        for i, word in enumerate(WORDS):
            for other in WORDS[i + 1:]:
                allowance = max(tolerance_for(word), tolerance_for(other), 1)
                if abs(len(word) - len(other)) > allowance:
                    continue
                if edit_distance(word, other, cap=allowance) <= allowance:
                    collisions.append((word, other))
        assert not collisions, f"confusable words in pool: {collisions[:10]}"


class TestDailyPhrase:
    def test_deterministic_for_a_day(self):
        salt = new_salt()
        day = date(2026, 3, 14)
        assert phrase_for(day, salt) == phrase_for(day, salt)

    def test_changes_between_days(self):
        salt = new_salt()
        a = phrase_for(date(2026, 3, 14), salt)
        b = phrase_for(date(2026, 3, 15), salt)
        assert a != b

    def test_depends_on_salt(self):
        """Two installations must not show the same phrase on the same day."""
        day = date(2026, 3, 14)
        assert phrase_for(day, new_salt()) != phrase_for(day, new_salt())

    def test_word_count_respected(self):
        salt = new_salt()
        assert len(phrase_for(date(2026, 1, 1), salt, words=6)) == 6

    def test_rejects_zero_words(self):
        with pytest.raises(ValueError):
            phrase_for(date(2026, 1, 1), new_salt(), words=0)

    def test_words_come_from_the_pool(self):
        salt = new_salt()
        for offset in range(60):
            day = date.fromordinal(date(2026, 1, 1).toordinal() + offset)
            assert all(w in WORDS for w in phrase_for(day, salt))

    def test_today_matches_explicit_date(self):
        salt = new_salt()
        assert phrase_today(salt) == phrase_for(date.today(), salt)

    def test_distribution_is_broad(self):
        """A year of phrases should touch much of the pool, not a narrow slice."""
        salt = new_salt()
        seen = Counter()
        for offset in range(365):
            day = date.fromordinal(date(2026, 1, 1).toordinal() + offset)
            seen.update(phrase_for(day, salt))
        assert len(seen) > len(WORDS) * 0.7

    def test_phrases_rarely_repeat_within_a_year(self):
        salt = new_salt()
        phrases = {
            tuple(phrase_for(date.fromordinal(date(2026, 1, 1).toordinal() + i), salt))
            for i in range(365)
        }
        assert len(phrases) == 365


class TestEphemeralPhrase:
    def test_varies_between_calls(self):
        """Per-attempt mode must not repeat; that is its whole advantage."""
        assert len({tuple(ephemeral_phrase()) for _ in range(50)}) > 45

    def test_word_count(self):
        assert len(ephemeral_phrase(words=3)) == 3

    def test_rejects_zero_words(self):
        with pytest.raises(ValueError):
            ephemeral_phrase(words=0)


class TestFormatting:
    def test_joins_with_spaces(self):
        assert format_phrase(["alpha", "beta"]) == "alpha beta"


class TestSalt:
    def test_salt_is_unique(self):
        assert len({new_salt() for _ in range(100)}) == 100

    def test_salt_is_hex_and_long(self):
        salt = new_salt()
        assert len(salt) == 64
        int(salt, 16)  # raises if not hex
