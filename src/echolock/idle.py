"""Showing the overlay after a period without input.

This is what makes EchoLock behave like a lock screen during a session: step
away, the desktop is covered, come back and speak to reveal it. It is the
closest honest equivalent to the real thing.

It is not the Windows lock screen and does not try to be. Windows will not let
an ordinary process authenticate a user: the login screen runs on a separate
secure desktop that other processes cannot draw on or send input to, which is
the same isolation that stops malware from automating its way past a password.
The only supported way to add a factor there is a Credential Provider, a COM
component loaded into the login process, where a defect locks the account out
of the machine.

A design that stored the Windows password and typed it in on a voice match
would also be worse than useless: the account's security would drop to that of
the voice model, and the password would sit on disk in a recoverable form.

So the overlay guards an unlocked session, and the Windows lock still guards
the account. Both exits from the overlay are safe: speaking reveals the session
that was already yours, and Escape hands over to the real lock.
"""

from __future__ import annotations

import ctypes
import time
from ctypes import wintypes


class IdleUnavailable(RuntimeError):
    """Raised when idle time cannot be measured on this platform."""


class _LastInputInfo(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]


def idle_seconds() -> float:
    """Seconds since the last keyboard or mouse input."""
    try:
        info = _LastInputInfo()
        info.cbSize = ctypes.sizeof(info)
        if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):  # type: ignore[attr-defined]
            raise IdleUnavailable("GetLastInputInfo failed")
        # Both counters wrap at the same 32-bit boundary, so the subtraction
        # stays correct across a wrap as long as it is masked back to 32 bits.
        elapsed = (ctypes.windll.kernel32.GetTickCount() - info.dwTime) & 0xFFFFFFFF  # type: ignore[attr-defined]
        return elapsed / 1000.0
    except AttributeError as exc:  # not Windows
        raise IdleUnavailable("idle detection is only implemented for Windows") from exc


def is_supported() -> bool:
    try:
        idle_seconds()
        return True
    except IdleUnavailable:
        return False


def watch(minutes: float, poll_seconds: float = 5.0, on_lock=None) -> None:
    """Block, showing the overlay whenever the session has been idle *minutes*.

    After the overlay closes, the watch resumes. The idle timer is naturally
    reset by the input the user gives to dismiss the overlay, so there is no
    need to track state beyond waiting for activity again.
    """
    from .ui import run_overlay

    threshold = max(10.0, minutes * 60.0)
    armed = True

    while True:
        try:
            idle = idle_seconds()
        except IdleUnavailable as exc:
            raise SystemExit(f"error: {exc}")

        if idle >= threshold and armed:
            armed = False
            if on_lock:
                on_lock()
            run_overlay(lock_session=True)
            # Wait for the session to look active again before re-arming, so
            # the overlay cannot immediately retrigger on the same idle period.
            while idle_seconds() >= threshold:
                time.sleep(poll_seconds)
            armed = True
        elif idle < threshold:
            armed = True
        time.sleep(poll_seconds)
