"""Where the profile lives on disk, and what is in it.

Everything stays on the machine that enrolled it. The voiceprint is a set of
summary statistics, means and spreads of cepstral coefficients, rather than the
recordings themselves, which are discarded once enrolment finishes. That is a
deliberate choice: the statistics are enough to compare a new sample against,
and cannot be played back as audio the way stored recordings could.

The profile is not encrypted, and the README says so plainly. It is not a
credential: possessing it lets someone check whether audio matches the enrolled
speaker, but it does not unlock anything by itself, and this tool never guards
anything the operating system's own authentication does not already guard.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .phrase import DEFAULT_WORD_COUNT, new_salt

APP_NAME = "EchoLock"
PROFILE_FILE = "voiceprint.npz"
CONFIG_FILE = "config.json"
ENV_OVERRIDE = "ECHOLOCK_HOME"


def profile_dir() -> Path:
    """Return the directory holding this user's profile, creating nothing.

    ``ECHOLOCK_HOME`` overrides the location, which keeps tests off the real
    profile and lets a user relocate it.
    """
    override = os.environ.get(ENV_OVERRIDE)
    if override:
        return Path(override).expanduser()
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local")
        return Path(base) / APP_NAME
    xdg = os.environ.get("XDG_DATA_HOME")
    base_path = Path(xdg) if xdg else Path.home() / ".local" / "share"
    return base_path / APP_NAME.lower()


def profile_path() -> Path:
    return profile_dir() / PROFILE_FILE


def config_path() -> Path:
    return profile_dir() / CONFIG_FILE


@dataclass
class Config:
    """Per-installation settings."""

    salt: str = field(default_factory=new_salt)
    word_count: int = DEFAULT_WORD_COUNT
    per_attempt_phrase: bool = False
    min_phrase_ratio: float = 1.0
    record_seconds: float = 4.0
    sample_rate: int = 16_000
    vosk_model_path: str = ""

    @classmethod
    def load(cls, path: Path | None = None) -> "Config":
        """Load the config, creating one with a fresh salt if absent."""
        path = path or config_path()
        if not path.exists():
            config = cls()
            config.save(path)
            return config
        data = json.loads(path.read_text(encoding="utf-8"))
        known = {f for f in cls.__dataclass_fields__}  # ignore unknown future keys
        return cls(**{k: v for k, v in data.items() if k in known})

    def save(self, path: Path | None = None) -> None:
        path = path or config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")
        _restrict_permissions(path)


def _restrict_permissions(path: Path) -> None:
    """Best-effort tighten of file permissions to the owner.

    The salt is the one value here worth protecting: knowing it would let
    someone work out future phrases and prepare audio in advance. On POSIX this
    is a chmod; on Windows the profile already sits under the per-user
    LOCALAPPDATA tree, and setting ACLs is left to the platform.
    """
    if os.name != "nt":
        try:
            path.chmod(0o600)
        except OSError:
            pass


def profile_exists() -> bool:
    return profile_path().exists()
