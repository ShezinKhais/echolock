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
from echolock.wordlist import CONNECTORS, SPOKEN, WORDS, validate_pool


class TestWordPool:
    def test_pool_is_valid(self):
        validate_pool()

    def test_enough_distinct_sentences_exist(self):
        """Pool size is not the measure; the number of possible prompts is.

        Words are grouped by part of speech and combined through templates, so
        a smaller vocabulary still yields a very large prompt space. What must
        hold is that prompts do not recur often enough for a recording of one
        to be worth keeping.
        """
        from echolock.phrase import DEFAULT_KEYWORDS, TEMPLATES, _POOLS

        total = 0
        for frame, slots in TEMPLATES[DEFAULT_KEYWORDS]:
            combinations = 1
            for kind in slots:
                combinations *= len(_POOLS[kind])
            total += combinations
        assert total > 100_000, f"only {total:,} distinct prompts"

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
        assert phrase_for(day, salt).text == phrase_for(day, salt).text

    def test_changes_between_days(self):
        salt = new_salt()
        a = phrase_for(date(2026, 3, 14), salt)
        b = phrase_for(date(2026, 3, 15), salt)
        assert a.text != b.text

    def test_depends_on_salt(self):
        """Two installations must not show the same phrase on the same day."""
        day = date(2026, 3, 14)
        assert phrase_for(day, new_salt()).text != phrase_for(day, new_salt()).text

    def test_word_count_respected(self):
        salt = new_salt()
        assert len(phrase_for(date(2026, 1, 1), salt, keywords=6).keywords) == 6

    def test_rejects_impossible_keyword_counts(self):
        for bad in (0, 1, 99):
            with pytest.raises(ValueError):
                phrase_for(date(2026, 1, 1), new_salt(), keywords=bad)

    def test_words_come_from_the_pool(self):
        salt = new_salt()
        for offset in range(60):
            day = date.fromordinal(date(2026, 1, 1).toordinal() + offset)
            assert all(w in WORDS for w in phrase_for(day, salt).keywords)

    def test_today_matches_explicit_date(self):
        salt = new_salt()
        assert phrase_today(salt).text == phrase_for(date.today(), salt).text

    def test_distribution_is_broad(self):
        """A year of phrases should touch much of the pool, not a narrow slice."""
        salt = new_salt()
        seen = Counter()
        for offset in range(365):
            day = date.fromordinal(date(2026, 1, 1).toordinal() + offset)
            seen.update(phrase_for(day, salt).keywords)
        assert len(seen) > len(WORDS) * 0.7

    def test_phrases_rarely_repeat_within_a_year(self):
        """A year of prompts should be almost entirely distinct.

        Almost, not entirely. The phrases are drawn independently, so this is a
        birthday problem: across roughly 1.7 million possible sentences, 365
        draws collide about 3.8% of the time. Demanding 365 unique values, as
        this test first did, therefore failed around one run in twenty-six on a
        random salt, which is a flaky test rather than a real defect.

        Fixed salts keep the result reproducible, and the bound asserts what the
        design actually promises: repeats are rare, not impossible.
        """
        for salt in ("00" * 16, "a3" * 16, "5f" * 16):
            phrases = {
                phrase_for(date.fromordinal(date(2026, 1, 1).toordinal() + i), salt).text
                for i in range(365)
            }
            assert len(phrases) >= 362, f"{365 - len(phrases)} repeats in a year"

    def test_a_repeat_never_lands_on_consecutive_days(self):
        """Two identical prompts in a row would be noticeable and look broken."""
        salt = new_salt()
        texts = [
            phrase_for(date.fromordinal(date(2026, 1, 1).toordinal() + i), salt).text
            for i in range(365)
        ]
        assert all(a != b for a, b in zip(texts, texts[1:]))


class TestEphemeralPhrase:
    def test_varies_between_calls(self):
        """Per-attempt mode must not repeat; that is its whole advantage."""
        assert len({ephemeral_phrase().text for _ in range(50)}) > 45

    def test_keyword_count(self):
        assert len(ephemeral_phrase(keywords=3).keywords) == 3

    def test_rejects_impossible_keyword_counts(self):
        with pytest.raises(ValueError):
            ephemeral_phrase(keywords=0)


class TestFormatting:
    def test_renders_the_sentence(self):
        phrase = ephemeral_phrase()
        assert format_phrase(phrase) == phrase.text


class TestSalt:
    def test_salt_is_unique(self):
        assert len({new_salt() for _ in range(100)}) == 100

    def test_salt_is_hex_and_long(self):
        salt = new_salt()
        assert len(salt) == 64
        int(salt, 16)  # raises if not hex


class TestEnrolmentPrompts:
    """Enrolment must cover both speaking styles the user will actually use."""

    def test_returns_the_requested_count(self):
        from echolock.phrase import enrolment_prompts

        assert len(enrolment_prompts(10)) == 10

    def test_mixes_familiar_and_generated_sentences(self):
        """Both are sentences, but they are read with different deliberateness."""
        from echolock.phrase import SENTENCE_PROMPTS, enrolment_prompts

        prompts = enrolment_prompts(6)
        familiar = [p for p in prompts if p in SENTENCE_PROMPTS]
        generated = [p for p in prompts if p not in SENTENCE_PROMPTS]
        assert len(familiar) == 3 and len(generated) == 3

    def test_every_prompt_reads_as_a_sentence(self):
        from echolock.phrase import enrolment_prompts

        for prompt in enrolment_prompts(8):
            assert prompt[0].isupper() and prompt.endswith(".")

    def test_handles_odd_counts(self):
        from echolock.phrase import enrolment_prompts

        assert len(enrolment_prompts(1)) == 1
        assert len(enrolment_prompts(7)) == 7
