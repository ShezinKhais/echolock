"""Running the overlay automatically when Windows starts.

This is as close to "unlock the PC with my voice" as a normal application can
honestly get, and the gap is worth stating plainly.

Windows will not let a background program authenticate a user. The login screen
runs on a separate secure desktop owned by Winlogon, which ordinary processes
cannot draw on or send input to. Replacing it means writing a Credential
Provider: a COM component in C++, loaded into the login process, where a defect
locks the account out of the machine entirely. That is the only supported way
to put a new factor in front of a real Windows login, and it is not something
to attach to a voice model that occasionally rejects its own owner.

What this does instead is start the overlay right after login. The practical
effect is that the desktop stays covered until the enrolled speaker says the
phrase, so the machine is guarded in the way the user actually wanted, while
Windows' own password remains the thing that truly protects the account. The
overlay's Escape route still falls back to the Windows lock, so nothing here
can leave the session less protected than it started.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

SHORTCUT_NAME = "EchoLock.lnk"


class AutostartUnavailable(RuntimeError):
    """Raised when autostart cannot be configured on this platform."""


def startup_dir() -> Path:
    """The per-user Startup folder."""
    if os.name != "nt":
        raise AutostartUnavailable("autostart is only implemented for Windows")
    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise AutostartUnavailable("APPDATA is not set, so the Startup folder cannot be found")
    return Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"


def shortcut_path() -> Path:
    return startup_dir() / SHORTCUT_NAME


def _launch_command() -> tuple[Path, str]:
    """What the shortcut should run: an executable and its arguments.

    A packaged build is already the application, so the shortcut points at it
    directly. A source install has to go through the interpreter, and it uses
    the windowed one so no console flashes up at login.
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable), "lock"
    candidate = Path(sys.executable).with_name("pythonw.exe")
    interpreter = candidate if candidate.exists() else Path(sys.executable)
    return interpreter, "-m echolock lock"


def is_enabled() -> bool:
    try:
        return shortcut_path().exists()
    except AutostartUnavailable:
        return False


def enable() -> Path:
    """Create the Startup shortcut. Returns its path."""
    target = shortcut_path()
    target.parent.mkdir(parents=True, exist_ok=True)

    # Built through the shell's own COM object rather than by writing .lnk
    # bytes, so Windows produces a shortcut it will definitely accept.
    executable, arguments = _launch_command()
    script = (
        "$s = (New-Object -ComObject WScript.Shell).CreateShortcut('{link}'); "
        "$s.TargetPath = '{exe}'; "
        "$s.Arguments = '{args}'; "
        "$s.WorkingDirectory = '{cwd}'; "
        "$s.Description = 'EchoLock voice overlay'; "
        "$s.Save()"
    ).format(link=target, exe=executable, args=arguments, cwd=Path.home())

    result = subprocess.run(
        ["powershell", "-NoProfile", "-Command", script],
        capture_output=True, text=True, timeout=60,
    )
    if result.returncode != 0 or not target.exists():
        raise AutostartUnavailable(
            f"could not create the Startup shortcut: {result.stderr.strip() or 'unknown error'}"
        )
    return target


def disable() -> bool:
    """Remove the Startup shortcut. Returns whether one was there."""
    target = shortcut_path()
    if target.exists():
        target.unlink()
        return True
    return False
