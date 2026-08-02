"""Storage for the Windows password the credential provider submits.

Read this before enabling the feature, because it is the part that trades away
security rather than adding it.

For voice to serve as a second way into Windows, something has to hand Windows
a real password once the voice matches. That password must be recoverable
*before* anyone is logged in, which rules out the protection normally used for
user secrets: DPAPI's per-user key is derived from the account's credentials and
is not available at the logon desktop. The only key that exists at that point
belongs to the machine.

So the blob here is encrypted with :c:func:`CryptProtectData` in machine scope,
and it is important to be exact about what that buys, because it is less than it
sounds. Machine scope means the key belongs to this installation of Windows: the
blob is useless on another machine or from a drive read elsewhere. It does *not*
mean elevation is required to decrypt it. Any process on this machine that can
read the file can call :c:func:`CryptUnprotectData` and recover the password,
with no Administrator rights involved. This was measured rather than assumed: an
ordinary unelevated process decrypted a test blob on the first attempt.

The file's access control list is therefore the only barrier between another
account on this machine and the password, which is why :func:`_restrict` is a
correctness requirement here rather than hardening. An entropy value tied to this
module is mixed in, so a generic DPAPI tool cannot open the blob without knowing
what it belongs to, but that is obscurity and is not counted as protection.

The honest summary: enabling this converts "my Windows password exists only in
my head" into "my Windows password is on this disk, recoverable by anything that
can read one file on this machine". A phone avoids that with a secure element
that releases a key only on a sensor match; a desktop with no such hardware
binding cannot. Nothing here is enabled by default, :func:`clear` removes it
completely, and :mod:`echolock.pin` exists precisely so the overlay has a
fallback that needs no recoverable secret at all.
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


def _blob(data: bytes) -> tuple[_Blob, ctypes.Array]:
    """Build a DATA_BLOB and return the buffer backing it.

    The caller has to keep that buffer alive. The structure holds a raw pointer,
    which is not a reference Python counts, so letting the buffer go out of scope
    while the API is still reading it is a use-after-free. It happens to work
    most of the time, which is exactly what makes it worth being careful about.
    """
    buffer = ctypes.create_string_buffer(data, len(data))
    return _Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char))), buffer


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
    incoming, _keep_in = _blob(secret)
    entropy, _keep_entropy = _blob(_ENTROPY)
    ok = crypt32.CryptProtectData(
        ctypes.byref(incoming),
        ctypes.c_wchar_p("EchoLock credential"),
        ctypes.byref(entropy),
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
    incoming, _keep_in = _blob(blob)
    entropy, _keep_entropy = _blob(_ENTROPY)
    ok = crypt32.CryptUnprotectData(
        ctypes.byref(incoming),
        None,
        ctypes.byref(entropy),
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

    # Machine-scope DPAPI does not separate one account from another, so this
    # is the only thing standing between another user on this machine and the
    # password. If it cannot be applied the credential is removed rather than
    # left lying readable, because a file the user believes is protected is
    # worse than one they know was never stored.
    try:
        _restrict(target)
    except VaultUnavailable:
        clear()
        raise
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


def _current_sid() -> str | None:
    """This account's SID, which is stable and not affected by display language."""
    import subprocess

    try:
        result = subprocess.run(
            ["whoami", "/user", "/fo", "csv", "/nh"],
            capture_output=True, text=True, timeout=30, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    parts = [field.strip('" ') for field in result.stdout.strip().split(",")]
    return parts[-1] if parts and parts[-1].startswith("S-1-") else None


def _restrict(target: Path) -> None:
    """Cut the file's access down to this account, SYSTEM and Administrators.

    Machine-scope DPAPI does not isolate one account from another, so this list
    is what actually keeps another user on this machine from reading the
    password. Two consequences follow.

    Principals are named by SID rather than by name. `USERNAME` can be empty in
    a service context, which would previously have produced the argument ':F',
    and group names are translated on a localised Windows so "Administrators"
    simply would not resolve.

    And if the command fails the inherited permissions are put back, because
    `/inheritance:r` removes the existing access first: a half-applied change
    leaves a file that not even its owner can open. Failing back to ordinary
    permissions is bad; failing into an unreadable file is worse, and silently
    doing so, as this did before, is worst.
    """
    if not is_supported():
        return
    import subprocess

    sid = _current_sid()
    if sid is None:
        raise VaultUnavailable(
            "could not determine this account's SID, so the credential file "
            "cannot be protected from other users on this machine"
        )

    grants = []
    for principal in (sid, "S-1-5-18", "S-1-5-32-544"):  # you, SYSTEM, Administrators
        grants += ["/grant:r", f"*{principal}:F"]

    try:
        result = subprocess.run(
            ["icacls", str(target), "/inheritance:r", *grants],
            capture_output=True, text=True, timeout=30, check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        _restore_inheritance(target)
        raise VaultUnavailable(f"could not set permissions on the credential file: {exc}")

    if result.returncode != 0:
        _restore_inheritance(target)
        raise VaultUnavailable(
            "could not restrict the credential file, so it would be readable by "
            f"other accounts: {result.stdout.strip() or result.stderr.strip()}"
        )

    # Confirm rather than assume: a zero exit from icacls with no effective
    # access would be the worst of both outcomes.
    try:
        target.read_bytes()
    except OSError as exc:
        _restore_inheritance(target)
        raise VaultUnavailable(f"the credential file became unreadable: {exc}")


def _restore_inheritance(target: Path) -> None:
    """Undo a partial permission change, so the file stays usable."""
    import subprocess

    try:
        subprocess.run(
            ["icacls", str(target), "/reset"],
            capture_output=True, text=True, timeout=30, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        pass
