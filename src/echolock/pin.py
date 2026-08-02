"""The fallback secret for the overlay.

Voice verification fails sometimes, and it fails for reasons that have nothing
to do with who is standing there: a cold, a noisy room, a microphone that came
unplugged. A lock with no second way in is a lock that eventually traps its
owner, so the overlay needs one.

This is deliberately not the Windows password. Nothing here can recover the
secret it stores, because it does not store the secret: it stores a PBKDF2
digest and a random salt, and checking an entry means deriving the digest again
and comparing. Reading this file tells an attacker nothing they can type. That
is the whole reason the earlier credential-provider design was abandoned, which
required a recoverable Windows password and could not avoid it.

Two properties matter beyond the hashing.

**Work factor.** A four-digit PIN is ten thousand possibilities, which is
nothing to a machine. The iteration count makes each guess cost real time, so
an attacker who copies this file cannot simply enumerate the space for free.

**Rate limiting that survives a restart.** Guessing is throttled by a delay that
doubles with each failure, and the counter is written to disk. An attacker who
kills the overlay and reopens it finds the same lockout waiting, because a
throttle held only in memory is bypassed by whatever ends the process.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .storage import profile_dir

PIN_FILE = "pin.json"

# Chosen so a single check costs roughly a tenth of a second on a normal
# machine: unnoticeable when typing one PIN, ruinous when trying ten thousand.
ITERATIONS = 240_000
SALT_BYTES = 16
DIGEST = "sha256"

MIN_LENGTH = 4

# Failures before any delay applies, then the wait doubles each time. Three
# free tries covers ordinary fumbling; the fourth starts costing.
FREE_ATTEMPTS = 3
BASE_DELAY_SECONDS = 5.0
MAX_DELAY_SECONDS = 300.0


class PinError(ValueError):
    """Raised when a PIN cannot be set."""


@dataclass
class _Record:
    salt: str
    digest: str
    iterations: int = ITERATIONS
    failures: int = 0
    locked_until: float = 0.0
    created: str = field(default="")


def pin_path() -> Path:
    return profile_dir() / PIN_FILE


def is_set() -> bool:
    return pin_path().exists()


def _derive(pin: str, salt: bytes, iterations: int) -> str:
    return hashlib.pbkdf2_hmac(DIGEST, pin.encode("utf-8"), salt, iterations).hex()


def _load() -> _Record:
    data = json.loads(pin_path().read_text(encoding="utf-8"))
    known = {f for f in _Record.__dataclass_fields__}
    return _Record(**{k: v for k, v in data.items() if k in known})


def _restrict(path: Path) -> None:
    """Best-effort tightening of the file's permissions."""
    if os.name != "nt":
        try:
            path.chmod(0o600)
        except OSError:
            pass


def _save(record: _Record) -> None:
    path = pin_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(record), indent=2), encoding="utf-8")
    _restrict(path)


def set_pin(pin: str) -> None:
    """Replace the stored PIN. The value itself is never written anywhere."""
    if len(pin) < MIN_LENGTH:
        raise PinError(f"a PIN needs at least {MIN_LENGTH} characters")
    if pin.isdigit() and len(set(pin)) == 1:
        raise PinError("that is the same digit repeated; pick something else")
    if pin.isdigit() and pin in {"1234", "12345", "123456", "0123", "4321"}:
        raise PinError("that is one of the most-guessed PINs; pick something else")

    from datetime import datetime, timezone

    salt = secrets.token_bytes(SALT_BYTES)
    _save(
        _Record(
            salt=salt.hex(),
            digest=_derive(pin, salt, ITERATIONS),
            iterations=ITERATIONS,
            created=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        )
    )


def clear() -> bool:
    """Remove the stored PIN. Returns whether one was there."""
    path = pin_path()
    if not path.exists():
        return False
    path.unlink()
    return True


def delay_remaining() -> float:
    """Seconds left before another attempt is allowed."""
    if not is_set():
        return 0.0
    try:
        record = _load()
    except (OSError, ValueError, TypeError):
        return 0.0
    return max(0.0, record.locked_until - time.time())


def check(pin: str) -> bool:
    """Whether *pin* is correct, honouring and updating the throttle."""
    if not is_set():
        return False
    try:
        record = _load()
    except (OSError, ValueError, TypeError):
        # A corrupted record must not become an open door.
        return False

    if record.locked_until > time.time():
        return False

    salt = bytes.fromhex(record.salt)
    candidate = _derive(pin, salt, record.iterations)

    # Constant time, so the comparison itself does not leak how much of the
    # digest matched.
    if hmac.compare_digest(candidate, record.digest):
        record.failures = 0
        record.locked_until = 0.0
        _save(record)
        return True

    record.failures += 1
    if record.failures > FREE_ATTEMPTS:
        over = record.failures - FREE_ATTEMPTS
        delay = min(BASE_DELAY_SECONDS * (2 ** (over - 1)), MAX_DELAY_SECONDS)
        record.locked_until = time.time() + delay
    _save(record)
    return False


def status() -> dict:
    """Describe the stored PIN without revealing anything about its value."""
    if not is_set():
        return {"set": False}
    try:
        record = _load()
    except (OSError, ValueError, TypeError):
        return {"set": True, "readable": False}
    return {
        "set": True,
        "readable": True,
        "created": record.created,
        "failures": record.failures,
        "locked_for": round(max(0.0, record.locked_until - time.time()), 1),
        "iterations": record.iterations,
    }
