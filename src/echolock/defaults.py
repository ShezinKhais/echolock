"""Tuning constants that other modules need without the numeric core.

These live apart from :mod:`echolock.voiceprint` for one reason: importing that
module imports numpy, which costs over a tenth of a second before anything is on
screen. The command line needs the sensitivity default to build its argument
parser, and the window needs it to open an enrolment dialog, and neither should
pay for the whole numeric stack to read one float. :mod:`echolock.voiceprint`
re-exports everything here, so the constants still have their obvious home.
"""

from __future__ import annotations

# How far below the enrolled speaker's own worst take the accept threshold sits,
# in standard deviations of their leave-one-out scores. Calibrated against real
# speech rather than synthetic samples: 3.0 read as reasonable on generated
# voices, which sit further apart than real ones do, and admitted 2 impostors in
# 24 attempts once measured on actual recordings.
DEFAULT_SENSITIVITY = 2.0
