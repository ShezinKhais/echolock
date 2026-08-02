"""The half of the credential provider that is not C++.

A Windows credential provider is a COM object loaded into ``LogonUI.exe``. That
is a hostile place to put a speech pipeline: numpy and a 26 MB speech model
cannot be loaded into the logon process, and a defect there is not a crash but a
machine nobody can log into. So the C++ side is kept as small as it can be, and
everything that thinks lives here, in the program that is already tested.

The two talk over a process boundary with JSON on stdout and a meaningful exit
code, which means the C++ never parses anything complicated and this module can
be exercised from a terminal like any other command.

    EchoLock.exe provider begin     -> {"text": "...", "session": "..."}
    EchoLock.exe provider verify    -> {"unlocked": true, ...}   exit 0
                                       {"unlocked": false, ...}  exit 1
                                       {"error": "..."}          exit 2

`begin` writes the phrase to a short-lived session file and prints the sentence
for the tile to display. `verify` records, checks it, and reports. Splitting
them matters: the phrase has to be on screen before the microphone opens, or the
user is being asked to read something they have not been shown.

Two rules hold throughout. This module never touches the stored password: the
C++ side decrypts that itself, only after a zero exit, so a bug here cannot leak
a credential it never had. And every failure path exits non-zero, because the
provider treats anything that is not an explicit success as a refusal and falls
back to the password field.
"""

from __future__ import annotations

import json
import os
import secrets
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from .storage import Config, profile_dir, profile_exists, profile_path

SESSION_FILE = "provider-session.json"

# How long a phrase stays valid between `begin` and `verify`. Long enough to
# read a sentence and press a button, short enough that a session file left
# behind by an abandoned attempt cannot be used later.
SESSION_TTL_SECONDS = 120.0

EXIT_OK = 0
EXIT_DENIED = 1
EXIT_ERROR = 2


@dataclass
class Session:
    token: str
    text: str
    keywords: list[str]
    created: float

    def expired(self, now: float | None = None) -> bool:
        return (now if now is not None else time.time()) - self.created > SESSION_TTL_SECONDS


def session_path() -> Path:
    return profile_dir() / SESSION_FILE


def _emit(payload: dict, code: int) -> int:
    """Print one JSON object and return the exit code for it."""
    sys.stdout.write(json.dumps(payload) + "\n")
    sys.stdout.flush()
    return code


def clear_session() -> None:
    path = session_path()
    if path.exists():
        try:
            path.unlink()
        except OSError:
            pass


def begin() -> int:
    """Choose the phrase for this attempt and report it for display."""
    if not profile_exists():
        return _emit({"error": "no voice profile is enrolled"}, EXIT_ERROR)

    from .phrase import ephemeral_phrase, phrase_today

    config = Config.load()
    phrase = (
        ephemeral_phrase(config.word_count)
        if config.per_attempt_phrase
        else phrase_today(config.salt, config.word_count)
    )

    session = Session(
        token=secrets.token_hex(16),
        text=phrase.text,
        keywords=list(phrase.keywords),
        created=time.time(),
    )
    path = session_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(session.__dict__), encoding="utf-8")

    return _emit(
        {"text": session.text, "session": session.token, "seconds": config.record_seconds},
        EXIT_OK,
    )


def _load_session(token: str) -> Session:
    path = session_path()
    if not path.exists():
        raise RuntimeError("no attempt in progress")
    data = json.loads(path.read_text(encoding="utf-8"))
    session = Session(**data)
    if not secrets.compare_digest(session.token, token):
        raise RuntimeError("session token does not match")
    if session.expired():
        raise RuntimeError("the phrase expired; start another attempt")
    return session


def verify_attempt(token: str) -> int:
    """Record once and decide, against the phrase `begin` chose."""
    try:
        session = _load_session(token)
    except (RuntimeError, ValueError, OSError, TypeError) as exc:
        return _emit({"error": str(exc)}, EXIT_ERROR)

    # One attempt per phrase. Removing it first means a crash mid-verification
    # cannot leave a session that a second process could reuse.
    clear_session()

    try:
        from .asr import VoskTranscriber
        from .audio import record
        from .features import FeatureConfig
        from .verifier import verify
        from .voiceprint import Voiceprint

        config = Config.load()
        voiceprint = Voiceprint.load(profile_path())
        transcriber = VoskTranscriber(config.vosk_model_path or None, config.sample_rate)
        audio = record(config.record_seconds, config.sample_rate, device=config.input_device)
        decision = verify(
            audio, list(session.keywords), voiceprint, transcriber,
            FeatureConfig(sample_rate=config.sample_rate),
            min_phrase_ratio=config.min_phrase_ratio,
        )
    except Exception as exc:  # noqa: BLE001
        # Any fault at all is a refusal, never a pass. The tile falls back to
        # the password field, which is where an uncertain outcome belongs.
        return _emit({"error": f"{type(exc).__name__}: {exc}"}, EXIT_ERROR)

    payload = {
        "unlocked": bool(decision.unlocked),
        "reason": decision.reason,
        "score": None if decision.score is None else round(float(decision.score), 4),
        "threshold": None if decision.threshold is None else round(float(decision.threshold), 4),
    }
    return _emit(payload, EXIT_OK if decision.unlocked else EXIT_DENIED)


def status() -> int:
    """Describe whether the tile could work, without recording anything."""
    from . import vault

    payload = {
        "profile": profile_exists(),
        "credential_stored": vault.exists() if vault.is_supported() else False,
        "platform_supported": os.name == "nt",
    }
    # Whether a model is actually on disk, not merely whether the vosk package
    # imports. The class imports perfectly well with no model installed, so the
    # previous check reported a working tile when the phrase could never be
    # verified.
    try:
        from .download import is_installed

        payload["speech_model"] = bool(is_installed())
    except Exception:  # noqa: BLE001
        payload["speech_model"] = False
    payload["ready"] = all(
        (payload["profile"], payload["credential_stored"], payload["platform_supported"])
    )
    return _emit(payload, EXIT_OK if payload["ready"] else EXIT_DENIED)
