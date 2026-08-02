"""Vocabulary for the spoken passphrase.

The phrase is a short sentence rather than a list of unconnected words. A
sentence is easier to read aloud naturally, and it also transcribes better: a
small speech model uses context, so words in a grammatical frame are recognised
more reliably than the same words in isolation.

Only the slots filled at random carry the security value. Words are grouped by
part of speech so a template can produce a sentence that reads sensibly, and the
verifier checks the chosen words rather than the whole string, letting the
connecting words be misheard or dropped without failing an honest attempt.

Two properties are enforced by tests:

* No two vocabulary words sit within the edit distance the liveness matcher
  tolerates. If they did, a recording of one would satisfy a prompt containing
  the other, and two different prompts accepting the same audio is exactly what
  the rotating phrase exists to prevent.
* Every word a template can produce, connecting words included, appears in
  SPOKEN, which becomes the recogniser's vocabulary. A word missing from it
  could not be transcribed and would fail every attempt.

Phrase secrecy is not the goal, since the sentence is displayed on screen when
it is needed, so this list is deliberately public and readable.
"""

from __future__ import annotations

ADJECTIVES: tuple[str, ...] = (
    "amber", "ancient", "brittle", "copper", "crimson", "crooked", "distant",
    "faded", "frozen", "gentle", "golden", "hidden", "hollow", "humble",
    "jagged", "marble", "narrow", "nimble", "polished", "quiet", "restless",
    "rugged", "rusty", "silent", "solemn", "sturdy", "tangled", "velvet",
    "weary",
)

NOUNS: tuple[str, ...] = (
    "anchor", "beacon", "bridge", "canyon", "cellar", "chimney", "compass",
    "cottage", "falcon", "garden", "glacier", "harbor", "harvest", "kettle",
    "ladder", "lantern", "meadow", "mountain", "orchard", "pebble", "quarry",
    "raven", "river", "saddle", "sparrow", "thicket", "timber", "tunnel",
    "vessel", "willow",
)

VERBS: tuple[str, ...] = (
    "borders", "carries", "circles", "covers", "crosses", "crowns", "divides",
    "follows", "guards", "marks", "shadows", "shelters", "shields",
    "surrounds", "touches", "watches",
)

# Connecting words used by the templates. They are never verified, because a
# speech model drops short unstressed words constantly, but the recogniser
# still has to know them or it would be forced to map them onto the nearest
# content word.
CONNECTORS: tuple[str, ...] = (
    "the", "a", "and", "beyond", "beneath", "above", "near", "under", "past",
)

# Words that can be chosen at random, and therefore carry the freshness.
WORDS: tuple[str, ...] = tuple(sorted(ADJECTIVES + NOUNS + VERBS))

# Everything a prompt can contain, which is what the recogniser is limited to.
SPOKEN: tuple[str, ...] = tuple(sorted(set(WORDS + CONNECTORS)))


def validate_pool() -> None:
    """Raise if the vocabulary has duplicates or unusable entries.

    Called by the tests; cheap enough to assert rather than trust a
    hand-maintained literal.
    """
    if len(WORDS) != len(set(WORDS)):
        seen: set[str] = set()
        dupes = sorted({w for w in WORDS if w in seen or seen.add(w)})  # type: ignore[func-returns-value]
        raise ValueError(f"duplicate words in vocabulary: {dupes}")
    bad = [w for w in WORDS if not w.isalpha() or not w.islower() or len(w) < 4]
    if bad:
        raise ValueError(f"unusable words in vocabulary: {bad}")
    overlap = ((set(ADJECTIVES) & set(NOUNS)) | (set(NOUNS) & set(VERBS))
               | (set(ADJECTIVES) & set(VERBS)))
    if overlap:
        raise ValueError(f"words in more than one category: {sorted(overlap)}")
