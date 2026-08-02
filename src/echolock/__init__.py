"""EchoLock: voice and passphrase unlock overlay.

Verifies two independent things before dismissing its overlay: that the voice
belongs to the enrolled speaker, and that the speaker is saying the phrase
currently on screen. Neither check is sufficient alone: the first accepts a
recording, the second accepts anyone who can read.

The overlay is a convenience layer in front of an unlocked session, never a
replacement for the operating system's own authentication. See :mod:`echolock.ui`
for why that boundary is where it is.

The public names below are resolved on first use rather than imported eagerly.
Every one of them lives in a module that imports numpy, and the command line and
the desktop window both start by importing this package: binding them at import
time meant that printing a passphrase, or drawing an empty window, waited on the
whole numeric stack to load. They behave exactly as if imported normally; the
only difference is when the cost is paid.
"""

from typing import TYPE_CHECKING

__version__ = "1.0.0"

_LAZY = {
    "FeatureConfig": "features",
    "mfcc": "features",
    "check_phrase": "liveness",
    "ephemeral_phrase": "phrase",
    "phrase_for": "phrase",
    "phrase_today": "phrase",
    "Decision": "verifier",
    "verify": "verifier",
    "Voiceprint": "voiceprint",
    "build_voiceprint": "voiceprint",
    "embed": "voiceprint",
}

if TYPE_CHECKING:  # so type checkers and editors still see the real names
    from .features import FeatureConfig, mfcc
    from .liveness import check_phrase
    from .phrase import ephemeral_phrase, phrase_for, phrase_today
    from .verifier import Decision, verify
    from .voiceprint import Voiceprint, build_voiceprint, embed


def __getattr__(name: str):
    """Import the module owning `name` the first time it is asked for."""
    module_name = _LAZY.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from importlib import import_module

    value = getattr(import_module(f".{module_name}", __name__), name)
    globals()[name] = value  # bind it, so this runs once per name
    return value


def __dir__() -> list[str]:
    return sorted(__all__)


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
