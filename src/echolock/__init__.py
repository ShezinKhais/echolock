"""EchoLock: voice and passphrase unlock overlay.

Verifies two independent things before dismissing its overlay -- that the voice
belongs to the enrolled speaker, and that the speaker is saying the phrase
currently on screen. Neither check is sufficient alone: the first accepts a
recording, the second accepts anyone who can read.

The overlay is a convenience layer in front of an unlocked session, never a
replacement for the operating system's own authentication. See :mod:`echolock.ui`
for why that boundary is where it is.
"""

from .features import FeatureConfig, mfcc
from .liveness import check_phrase
from .phrase import ephemeral_phrase, phrase_for, phrase_today
from .verifier import Decision, verify
from .voiceprint import Voiceprint, build_voiceprint, embed

__version__ = "1.0.0"
__all__ = [
    "Decision",
    "FeatureConfig",
    "Voiceprint",
    "build_voiceprint",
    "check_phrase",
    "embed",
    "ephemeral_phrase",
    "mfcc",
    "phrase_for",
    "phrase_today",
    "verify",
    "__version__",
]
