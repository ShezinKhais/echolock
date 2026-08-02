"""Storage for the Windows password the credential provider submits.

Read this before enabling the feature, because it is the part that trades away
security rather than adding it.

For voice to serve as a second way into Windows, something has to hand Windows
a real password once the voice matches. That password must be recoverable
*before* anyone is logged in, which rules out the protection normally used for
user secrets: DPAPI's per-user key is derived from the account's credentials and
is not available at the logon desktop. The only key that exists at that point
belongs to the machine.

So the blob here is encrypted with :c:func:`CryptProtectData` in machine scope.
That defeats someone who copies the file, or who pulls the drive out and reads
it elsewhere, because the key never leaves this installation of Windows. It does
*not* defeat code already running as Administrator or SYSTEM on this machine:
such code can call :c:func:`CryptUnprotectData` and recover the password, in the
same way it could install a keylogger. An entropy value tied to this module is
mixed in, which means an attacker must know they are looking at EchoLock data,
but that is obscurity and is not counted as protection.

The honest summary: enabling this converts "my Windows password exists only in
my head" into "my Windows password is on this disk, recoverable by anything with
Administrator rights". A phone avoids that with a secure element that releases a
key only on a sensor match; a desktop with no such hardware binding cannot.
Nothing here is enabled by default, and :func:`clear` removes it completely.
"""

from __future__ import annotations

import ctypes
import os
from ctypes import wintypes
from pathlib import Path

from .storage import profile_dir

VAULT_FILE = "credential.bin"

# Mixed into the encryption alongside the machine key. Not a secret: it is in
# published source. Its only job is to make the blob specific to this program,
# so a blob lifted from here cannot be decrypted by a generic DPAPI tool
# without knowing what it belongs to.
_ENTROPY = b"echolock.credential.v1"

CRYPTPROTECT_LOCAL_MACHINE = 0x04
CRYPTPROTECT_UI_FORBIDDEN = 0x01


class VaultUnavailable(RuntimeError):
    """Raised when the credential store cannot be used on this platform."""


class _Blob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def vault_path() -> Path:
    return profile_dir() / VAULT_FILE


def is_supported() -> bool:
    return os.name == "nt"


def _blob(data: bytes) -> _Blob:
    buffer = ctypes.create_string_buffer(data, len(data))
    return _Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))


def _read_blob(blob: _Blob) -> bytes:
    return ctypes.string_at(blob.pbData, blob.cbData)


def _crypt32():
    if not is_supported():
        raise VaultUnavailable("the credential store is only implemented for Windows")
    return ctypes.windll.crypt32


def protect(secret: bytes) -> bytes:
    """Encrypt *secret* under the machine key."""
    crypt32 = _crypt32()
    out = _Blob()
    ok = crypt32.CryptProtectData(
        ctypes.byref(_blob(secret)),
        ctypes.c_wchar_p("EchoLock credential"),
        ctypes.byref(_blob(_ENTROPY)),
        None, None,
        CRYPTPROTECT_LOCAL_MACHINE | CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(out),
    )
    if not ok:
        raise VaultUnavailable(f"CryptProtectData failed (error {ctypes.GetLastError()})")
    try:
        return _read_blob(out)
    finally:
        ctypes.windll.kernel32.LocalFree(out.pbData)


def unprotect(blob: bytes) -> bytes:
    """Decrypt what :func:`protect` produced, on the machine that produced it."""
    crypt32 = _crypt32()
    out = _Blob()
    ok = crypt32.CryptUnprotectData(
        ctypes.byref(_blob(blob)),
        None,
        ctypes.byref(_blob(_ENTROPY)),
        None, None,
        CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(out),
    )
    if not ok:
        raise VaultUnavailable(f"CryptUnprotectData failed (error {ctypes.GetLastError()})")
    try:
        return _read_blob(out)
    finally:
        ctypes.windll.kernel32.LocalFree(out.pbData)


def store(password: str) -> Path:
    """Encrypt and save the password. Returns where it was written."""
    if not password:
        raise ValueError("refusing to store an empty password")
    target = vault_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(protect(password.encode("utf-8")))

    # Owner-only, so another account on this machine cannot read the blob
    # without first elevating. Elevated code can still read it; this narrows
    # the window rather than closing it.
    _restrict(target)
    return target


def load() -> str:
    """Decrypt the stored password."""
    target = vault_path()
    if not target.exists():
        raise VaultUnavailable("no credential stored")
    return unprotect(target.read_bytes()).decode("utf-8")


def exists() -> bool:
    return vault_path().exists()


def clear() -> bool:
    """Delete the stored credential. Returns whether one was there."""
    target = vault_path()
    if not target.exists():
        return False
    # Overwrite before unlinking. On a journalling filesystem this is not a
    # guarantee that no copy survives, but leaving the ciphertext in place when
    # the user asked for it gone would be worse.
    try:
        size = target.stat().st_size
        with target.open("r+b") as handle:
            handle.write(os.urandom(size))
            handle.flush()
            os.fsync(handle.fileno())
    except OSError:
        pass
    target.unlink()
    return True


def _restrict(target: Path) -> None:
    """Reduce the file's ACL to its owner and the administrators group."""
    if not is_supported():
        return
    import subprocess

    try:
        subprocess.run(
            ["icacls", str(target), "/inheritance:r",
             "/grant:r", f"{os.environ.get('USERNAME', '')}:F",
             "/grant:r", "*S-1-5-18:F",       # SYSTEM, which is what LogonUI runs as
             "/grant:r", "*S-1-5-32-544:F"],  # Administrators
            capture_output=True, text=True, timeout=30, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        # A tightened ACL is a hardening step, not a correctness requirement.
        pass
