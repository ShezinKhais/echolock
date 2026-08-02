"""Keeping the overlay in front while it is up.

A window that covers the screen is not the same as a window you cannot get
past, and the difference is the entire question when the overlay is being
relied on rather than enjoyed. Left alone, a fullscreen tkinter window is
dismissed by Alt+Tab, by the Windows key, by anything that takes focus. This
module closes the cheap routes and is explicit about the one it cannot.

Two mechanisms, both active only while the overlay is showing.

**Focus reassertion.** A timer puts the window back on top and takes focus
again if something else took it. This handles Alt+Tab, clicking a taskbar
button, and applications that raise themselves.

**A low-level keyboard hook.** Windows offers no way to disable the Windows key
for one window, so the shortcut keys are swallowed at the hook instead. The
hook only ever decides block-or-pass on the key currently being pressed: it
does not record keystrokes, accumulate them, or write anything anywhere, and
the blocked set is a fixed list in this file.

**What defeats it, unavoidably.** Ctrl+Alt+Delete is the secure attention
sequence. No hook sees it, by design, because that guarantee is what lets you
trust the real login screen is real. From that screen a user reaches Task
Manager and ends this process, revealing the desktop. Blocking that would mean
a Group Policy change disabling Task Manager for the whole machine, which is a
system-wide security setting this program has no business making on its own.

So the overlay resists everything casual and loses to a determined person with
physical access. That is worth stating plainly rather than discovering later.
"""

from __future__ import annotations

import ctypes
import os
from ctypes import wintypes

WH_KEYBOARD_LL = 13
WM_KEYDOWN = 0x0100
WM_SYSKEYDOWN = 0x0104

VK_LWIN = 0x5B
VK_RWIN = 0x5C
VK_TAB = 0x09
VK_ESCAPE = 0x1B
VK_F4 = 0x73

LLKHF_ALTDOWN = 0x20


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode", wintypes.DWORD),
        ("scanCode", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(wintypes.ULONG)),
    ]


HOOKPROC = ctypes.WINFUNCTYPE(
    ctypes.c_long, ctypes.c_int, wintypes.WPARAM, ctypes.POINTER(KBDLLHOOKSTRUCT)
)


def is_supported() -> bool:
    return os.name == "nt"


class Guard:
    """Holds the overlay in front for as long as it is installed."""

    REASSERT_MS = 400

    def __init__(self, root, *, block_keys: bool = True):
        self.root = root
        self.block_keys = block_keys and is_supported()
        self._hook = None
        self._proc = None
        self._active = False
        self.blocked_count = 0

    # -- keyboard ----------------------------------------------------------

    def _should_block(self, vk: int, flags: int) -> bool:
        """Whether this key would take the user away from the overlay."""
        if vk in (VK_LWIN, VK_RWIN):
            return True
        alt_down = bool(flags & LLKHF_ALTDOWN)
        if alt_down and vk in (VK_TAB, VK_ESCAPE, VK_F4):
            return True
        # Ctrl+Esc opens Start, the same destination as the Windows key.
        if vk == VK_ESCAPE and ctypes.windll.user32.GetAsyncKeyState(0x11) & 0x8000:
            return True
        return False

    def _install_hook(self) -> None:
        if not self.block_keys:
            return

        def callback(code, wparam, lparam):
            # Anything other than a plain pass-through decision is handed
            # straight on. Slow hooks are silently removed by Windows.
            if code == 0 and wparam in (WM_KEYDOWN, WM_SYSKEYDOWN):
                data = lparam[0]
                if self._should_block(data.vkCode, data.flags):
                    self.blocked_count += 1
                    return 1  # swallow it
            return ctypes.windll.user32.CallNextHookEx(None, code, wparam, lparam)

        self._proc = HOOKPROC(callback)
        self._hook = ctypes.windll.user32.SetWindowsHookExW(
            WH_KEYBOARD_LL, self._proc, None, 0
        )
        if not self._hook:
            # Not fatal. Focus reassertion still applies, and the overlay was
            # never the security boundary in the first place.
            self._proc = None

    def _remove_hook(self) -> None:
        if self._hook:
            ctypes.windll.user32.UnhookWindowsHookEx(self._hook)
        self._hook = None
        self._proc = None

    # -- focus -------------------------------------------------------------

    def _reassert(self) -> None:
        if not self._active:
            return
        try:
            self.root.attributes("-topmost", True)
            if not self.root.focus_displayof():
                self.root.focus_force()
        except Exception:  # noqa: BLE001 - the window may be closing
            return
        self.root.after(self.REASSERT_MS, self._reassert)

    # -- lifecycle ---------------------------------------------------------

    def install(self) -> "Guard":
        self._active = True
        self._install_hook()
        self.root.after(self.REASSERT_MS, self._reassert)
        return self

    def remove(self) -> None:
        self._active = False
        self._remove_hook()

    def __enter__(self) -> "Guard":
        return self.install()

    def __exit__(self, *_exc) -> None:
        self.remove()


def describe() -> dict:
    """What this can and cannot stop, for the interface to show honestly."""
    return {
        "blocks": ["Windows key", "Ctrl+Esc", "Alt+Tab", "Alt+Escape", "Alt+F4", "focus theft"],
        "cannot_block": ["Ctrl+Alt+Delete", "Task Manager reached from it", "a reboot"],
        "supported": is_supported(),
    }
