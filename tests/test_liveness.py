"""Tests for transcript matching.

Two failure modes matter and they pull in opposite directions: rejecting the
legitimate user because the speech model misheard a word ending, and accepting
a transcript that does not actually contain the prompt. The tests below pin
both edges.
"""

from __future__ import annotations

import pytest

from echolock.liveness import (
    check_phrase,
    edit_distance,
    normalise,
    tolerance_for,
    words_match,
)


class TestEditDistance:
    @pytest.mark.parametrize(
        "a,b,expected",
        [
            ("", "", 0),
            ("abc", "abc", 0),
            ("", "abc", 3),
            ("abc", "", 3),
            ("kitten", "sitting", 3),
            ("flaw", "lawn", 2),
            ("lantern", "lantirn", 1),
        ],
    )
    def test_known_distances(self, a, b, expected):
        assert edit_distance(a, b) == expected

    def test_symmetric(self):
        assert edit_distance("compass", "compost") == edit_distance("compost", "compass")

    def test_cap_short_circuits_without_changing_verdict(self):
        """Capping may overstate a large distance but never understates a small one."""
        assert edit_distance("apple", "zzzzzzzz", cap=2) > 2
        assert edit_distance("lantern", "lantirn", cap=2) == 1


class TestTolerance:
    def test_short_words_require_exact_match(self):
        assert tolerance_for("opal") == 0
        assert tolerance_for("river") == 0

    def test_longer_words_allow_more_slack(self):
        assert tolerance_for("compass") == 1
        assert tolerance_for("waterfall") == 2

    def test_tolerance_never_exceeds_half_the_word(self):
        """Otherwise a word could match something barely resembling it."""
        from echolock.wordlist import WORDS

        assert all(tolerance_for(w) < len(w) / 2 for w in WORDS)


class TestWordsMatch:
    def test_exact(self):
        assert words_match("compass", "compass")

    def test_within_tolerance(self):
        assert words_match("compass", "compas")

    def test_beyond_tolerance(self):
        assert not words_match("compass", "combat")

    def test_short_word_typo_rejected(self):
        assert not words_match("opal", "opel")


class TestNormalise:
    def test_lowercases_and_strips_punctuation(self):
        assert normalise("Lantern, quiet.  COMPASS!") == ["lantern", "quiet", "compass"]

    def test_keeps_apostrophes(self):
        assert normalise("don't") == ["don't"]

    def test_empty(self):
        assert normalise("   ") == []


class TestCheckPhrase:
    PHRASE = ["lantern", "quiet", "compass", "drift"]

    def test_exact_transcript_passes(self):
        result = check_phrase("lantern quiet compass drift", self.PHRASE)
        assert result.passed and result.ratio == 1.0 and not result.missing

    def test_tolerates_filler_words(self):
        result = check_phrase("um lantern the quiet uh compass drift", self.PHRASE)
        assert result.passed

    def test_tolerates_small_transcription_errors(self):
        result = check_phrase("lantirn quiet compas drift", self.PHRASE)
        assert result.passed

    def test_missing_word_fails(self):
        result = check_phrase("lantern quiet compass", self.PHRASE)
        assert not result.passed
        assert result.missing == ["drift"]
        assert result.ratio == pytest.approx(0.75)

    def test_wrong_order_fails(self):
        """Order is enforced, so a shuffled reading does not pass."""
        result = check_phrase("drift compass quiet lantern", self.PHRASE)
        assert not result.passed

    def test_empty_transcript_fails(self):
        result = check_phrase("", self.PHRASE)
        assert not result.passed
        assert result.summary == "nothing was transcribed"

    def test_unrelated_speech_fails(self):
        result = check_phrase("the weather is nice today", self.PHRASE)
        assert not result.passed and result.ratio == 0.0

    def test_yesterdays_phrase_fails(self):
        """The replay case the daily rotation exists to stop."""
        result = check_phrase("harbor sugar penguin marble", self.PHRASE)
        assert not result.passed

    def test_repeated_word_needs_two_utterances(self):
        """A prompt with a repeat is not satisfied by saying it once."""
        assert not check_phrase("echo alpha", ["echo", "echo"]).passed
        assert check_phrase("echo echo", ["echo", "echo"]).passed

    def test_relaxed_ratio_accepts_partial(self):
        result = check_phrase("lantern quiet compass", self.PHRASE, min_ratio=0.75)
        assert result.passed

    def test_rejects_empty_expected(self):
        with pytest.raises(ValueError):
            check_phrase("anything", [])

    def test_transcript_is_recorded(self):
        assert check_phrase("  hello  ", self.PHRASE).transcript == "hello"
