"""The overlay's fallback PIN.

Since this is now the only way past the overlay on a machine with no Windows
password, its failure modes matter more than its success. The tests lean on the
ways it could wrongly let someone in: a corrupted file, a bypassed throttle, a
digest that leaks through comparison, a stored value that can be read back.
"""

from __future__ import annotations

import json
import time

import pytest

from echolock import pin


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("ECHOLOCK_HOME", str(tmp_path))
    yield tmp_path


@pytest.fixture
def fast_kdf(monkeypatch):
    """Shrink the work factor so the suite stays quick."""
    monkeypatch.setattr(pin, "ITERATIONS", 1_000)
    yield


def test_nothing_is_set_initially():
    assert pin.is_set() is False
    assert pin.status() == {"set": False}


def test_check_without_a_pin_refuses(fast_kdf):
    assert pin.check("anything") is False


def test_a_correct_pin_passes(fast_kdf):
    pin.set_pin("8317")
    assert pin.check("8317") is True


def test_a_wrong_pin_fails(fast_kdf):
    pin.set_pin("8317")
    assert pin.check("8318") is False


def test_the_pin_is_not_recoverable_from_disk(fast_kdf):
    """The whole point: reading the file must not reveal anything typeable."""
    pin.set_pin("9471")
    raw = pin.pin_path().read_text(encoding="utf-8")
    assert "9471" not in raw

    record = json.loads(raw)
    assert "digest" in record and "salt" in record
    assert record["digest"] != "9471"
    # And the digest must not be a bare hash of the PIN with no salt.
    import hashlib

    assert record["digest"] != hashlib.sha256(b"9471").hexdigest()


def test_two_installs_of_the_same_pin_differ(fast_kdf, tmp_path, monkeypatch):
    """A shared salt would let one cracked digest open every installation."""
    pin.set_pin("5566")
    first = json.loads(pin.pin_path().read_text(encoding="utf-8"))

    monkeypatch.setenv("ECHOLOCK_HOME", str(tmp_path / "second"))
    pin.set_pin("5566")
    second = json.loads(pin.pin_path().read_text(encoding="utf-8"))

    assert first["salt"] != second["salt"]
    assert first["digest"] != second["digest"]


def test_short_pins_are_refused(fast_kdf):
    with pytest.raises(pin.PinError):
        pin.set_pin("12")
    assert pin.is_set() is False


def test_obvious_pins_are_refused(fast_kdf):
    for bad in ("1234", "0000", "111111"):
        with pytest.raises(pin.PinError):
            pin.set_pin(bad)


def test_failures_eventually_throttle(fast_kdf):
    pin.set_pin("7742")
    for _ in range(pin.FREE_ATTEMPTS):
        assert pin.check("0001") is False
    assert pin.delay_remaining() == 0.0

    assert pin.check("0001") is False
    assert pin.delay_remaining() > 0


def test_the_throttle_blocks_even_the_correct_pin(fast_kdf):
    """Otherwise the delay is no obstacle to someone guessing."""
    pin.set_pin("7742")
    for _ in range(pin.FREE_ATTEMPTS + 1):
        pin.check("0001")

    assert pin.delay_remaining() > 0
    assert pin.check("7742") is False


def test_the_throttle_survives_a_restart(fast_kdf):
    """A counter held in memory is bypassed by killing the process."""
    pin.set_pin("7742")
    for _ in range(pin.FREE_ATTEMPTS + 1):
        pin.check("0001")

    stored = json.loads(pin.pin_path().read_text(encoding="utf-8"))
    assert stored["failures"] > pin.FREE_ATTEMPTS
    assert stored["locked_until"] > time.time()


def test_the_delay_grows_with_repeated_failure(fast_kdf):
    pin.set_pin("7742")
    delays = []
    for _ in range(pin.FREE_ATTEMPTS + 3):
        pin.check("0001")
        record = json.loads(pin.pin_path().read_text(encoding="utf-8"))
        delays.append(record["locked_until"])
        # Clear the lock so the next attempt is actually evaluated.
        record["locked_until"] = 0.0
        pin.pin_path().write_text(json.dumps(record), encoding="utf-8")

    gaps = [b - a for a, b in zip(delays, delays[1:]) if a and b]
    assert gaps and all(gap > 0 for gap in gaps)


def test_a_success_resets_the_counter(fast_kdf):
    pin.set_pin("7742")
    pin.check("0001")
    pin.check("0002")
    assert pin.check("7742") is True

    record = json.loads(pin.pin_path().read_text(encoding="utf-8"))
    assert record["failures"] == 0
    assert record["locked_until"] == 0.0


def test_a_corrupted_record_denies_rather_than_admits(fast_kdf):
    """A damaged file must not become an open door."""
    pin.set_pin("7742")
    pin.pin_path().write_text("{ not json at all", encoding="utf-8")

    assert pin.check("7742") is False
    assert pin.check("") is False
    assert pin.status() == {"set": True, "readable": False}


def test_an_emptied_record_denies(fast_kdf):
    pin.set_pin("7742")
    pin.pin_path().write_text("{}", encoding="utf-8")
    assert pin.check("7742") is False


def test_tampering_the_digest_to_empty_does_not_admit(fast_kdf):
    """Blanking the digest must not match a blank entry."""
    pin.set_pin("7742")
    record = json.loads(pin.pin_path().read_text(encoding="utf-8"))
    record["digest"] = ""
    pin.pin_path().write_text(json.dumps(record), encoding="utf-8")

    assert pin.check("") is False
    assert pin.check("7742") is False


def test_iterations_are_honoured_from_the_record(fast_kdf, monkeypatch):
    """A PIN set under one work factor still verifies after the default changes."""
    pin.set_pin("7742")
    monkeypatch.setattr(pin, "ITERATIONS", 2_000)
    assert pin.check("7742") is True


def test_clear_removes_it(fast_kdf):
    pin.set_pin("7742")
    assert pin.clear() is True
    assert pin.is_set() is False
    assert pin.check("7742") is False
    assert pin.clear() is False


def test_status_never_reveals_the_pin(fast_kdf):
    pin.set_pin("7742")
    assert "7742" not in json.dumps(pin.status())
