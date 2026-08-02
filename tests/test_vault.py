"""The stored Windows credential.

The security claim being tested is narrow and worth stating: the blob is
unreadable without the machine key, and `clear` really removes it. Everything
else about this feature is a documented trade-off, not a defect.
"""

from __future__ import annotations

import os

import pytest

from echolock import vault

windows_only = pytest.mark.skipif(os.name != "nt", reason="the credential store is Windows-only")


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("ECHOLOCK_HOME", str(tmp_path))
    yield tmp_path


def test_is_supported_matches_the_platform():
    assert vault.is_supported() == (os.name == "nt")


def test_nothing_is_stored_by_default():
    assert vault.exists() is False


def test_clear_on_an_empty_store_reports_nothing_removed():
    assert vault.clear() is False


@windows_only
def test_a_password_survives_a_round_trip():
    vault.store("correct horse battery staple")
    assert vault.exists() is True
    assert vault.load() == "correct horse battery staple"


@windows_only
def test_non_ascii_passwords_survive():
    secret = "pässwörd-ção-你好"
    vault.store(secret)
    assert vault.load() == secret


@windows_only
def test_the_file_on_disk_does_not_contain_the_password():
    vault.store("plaintext-would-be-a-bug")
    raw = vault.vault_path().read_bytes()
    assert b"plaintext-would-be-a-bug" not in raw


@windows_only
def test_the_entropy_is_required_to_decrypt():
    """A blob lifted from here should not open with a plain DPAPI call."""
    blob = vault.protect(b"secret")

    import ctypes

    class Blob(ctypes.Structure):
        _fields_ = [("cbData", ctypes.c_ulong), ("pbData", ctypes.POINTER(ctypes.c_char))]

    buffer = ctypes.create_string_buffer(blob, len(blob))
    incoming = Blob(len(blob), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))
    out = Blob()
    ok = ctypes.windll.crypt32.CryptUnprotectData(
        ctypes.byref(incoming), None, None, None, None, 0, ctypes.byref(out)
    )
    assert not ok, "decrypted without the application entropy"


@windows_only
def test_clear_removes_the_file():
    vault.store("gone shortly")
    assert vault.clear() is True
    assert vault.exists() is False
    assert not vault.vault_path().exists()


@windows_only
def test_an_empty_password_is_refused():
    with pytest.raises(ValueError):
        vault.store("")
    assert vault.exists() is False


@windows_only
def test_loading_a_missing_credential_raises():
    with pytest.raises(vault.VaultUnavailable):
        vault.load()


@windows_only
def test_a_corrupted_blob_raises_rather_than_returning_junk():
    vault.store("something")
    vault.vault_path().write_bytes(b"not a dpapi blob at all")
    with pytest.raises(vault.VaultUnavailable):
        vault.load()
