"""Measure what actually gets past the overlay.

Claiming a lock screen resists something is worth nothing without trying it, so
this drives real input at a guarded window and reports what happened. It runs
for a few seconds and takes focus while it does.

    python packaging/bypass_probe.py

Every route is attempted twice: once with the guard installed and once without,
because "the overlay stayed in front" only means something if it would have
lost otherwise. A watchdog thread tears the keyboard hook down unconditionally
after fifteen seconds, so a hang here cannot leave the Windows key swallowed
for the rest of the session.
"""

from __future__ import annotations

import ctypes
import sys
import threading
import time
import tkinter as tk
from ctypes import wintypes
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from echolock.guard import Guard, describe  # noqa: E402

user32 = ctypes.windll.user32

KEYEVENTF_KEYUP = 0x0002
VK_MENU, VK_TAB, VK_LWIN, VK_CONTROL, VK_ESCAPE = 0x12, 0x09, 0x5B, 0x11, 0x1B

WATCHDOG_SECONDS = 15.0


def _press(*keys: int, hold: float = 0.05) -> None:
    for key in keys:
        user32.keybd_event(key, 0, 0, 0)
    time.sleep(hold)
    for key in reversed(keys):
        user32.keybd_event(key, 0, KEYEVENTF_KEYUP, 0)
    time.sleep(0.35)


def _foreground_title() -> str:
    handle = user32.GetForegroundWindow()
    length = user32.GetWindowTextLengthW(handle)
    buffer = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(handle, buffer, length + 1)
    return buffer.value


def _run_case(guarded: bool) -> dict:
    """Put a window up, attack it, and report what held."""
    root = tk.Tk()
    root.title("EchoLock bypass probe")
    root.attributes("-fullscreen", True)
    root.attributes("-topmost", True)
    root.configure(bg="#0d1117")
    tk.Label(
        root,
        text=f"Testing bypasses  ({'guarded' if guarded else 'unguarded'})\nthis closes itself",
        font=("Segoe UI", 20), fg="#e6edf3", bg="#0d1117",
    ).place(relx=0.5, rely=0.5, anchor="center")

    guard = Guard(root).install() if guarded else None
    results: dict = {}

    def attack() -> None:
        root.update()
        root.focus_force()
        time.sleep(0.6)
        root.update()
        mine = root.winfo_toplevel().title()

        for label, keys in (
            ("Alt+Tab", (VK_MENU, VK_TAB)),
            ("Windows key", (VK_LWIN,)),
            ("Ctrl+Esc", (VK_CONTROL, VK_ESCAPE)),
        ):
            _press(*keys)
            root.update()
            time.sleep(0.4)
            root.update()
            results[label] = _foreground_title() == mine

        # Whether another window can simply steal the foreground and keep it.
        # GetConsoleWindow lives in kernel32, not user32; reaching for it on
        # the wrong library is what killed this thread on the first run and
        # made the hook look inert when it was working perfectly.
        console = ctypes.windll.kernel32.GetConsoleWindow()
        if console:
            user32.SetForegroundWindow(console)
            time.sleep(0.9)          # long enough for reassertion to notice
            root.update()
            time.sleep(0.3)
            root.update()
            results["focus theft"] = _foreground_title() == mine

        root.quit()

    def attack_guarded() -> None:
        # The count has to be read whatever happens above, or a fault in one
        # probe silently reports the hook as doing nothing.
        try:
            attack()
        finally:
            results["_blocked"] = guard.blocked_count if guard else 0
            results.setdefault("_attack_completed", True)
            root.quit()

    threading.Thread(target=attack_guarded, daemon=True).start()
    root.after(12_000, root.quit)
    root.mainloop()

    if guard:
        guard.remove()
    root.destroy()
    return results


def main() -> int:
    if sys.platform != "win32":
        print("This probe only means anything on Windows.")
        return 2

    # Unconditional teardown, whatever else happens.
    def watchdog() -> None:
        time.sleep(WATCHDOG_SECONDS * 2 + 10)
        ctypes.windll.user32.UnhookWindowsHookEx(0)

    threading.Thread(target=watchdog, daemon=True).start()

    print("Running. The screen is taken over briefly; do not touch the keyboard.\n")
    unguarded = _run_case(guarded=False)
    time.sleep(1.0)
    guarded = _run_case(guarded=True)

    print(f"{'route':<16}{'unguarded':<14}{'guarded':<12}verdict")
    print("-" * 58)
    verdicts = []
    for route in ("Alt+Tab", "Windows key", "Ctrl+Esc", "focus theft"):
        before = unguarded.get(route)
        after = guarded.get(route)
        if before is None or after is None:
            continue
        if after and not before:
            verdict = "fixed by the guard"
        elif after and before:
            verdict = "held anyway"
        elif not after:
            verdict = "STILL GETS PAST"
        verdicts.append((route, verdict))
        print(f"{route:<16}{'held' if before else 'got past':<14}{'held' if after else 'got past':<12}{verdict}")

    print(f"\nkeys swallowed by the hook: {guarded.get('_blocked', 0)}")
    print("\ncannot be blocked from user space:")
    for item in describe()["cannot_block"]:
        print(f"  - {item}")

    broken = [route for route, verdict in verdicts if verdict == "STILL GETS PAST"]
    if broken:
        print(f"\n{len(broken)} route(s) still get past: {', '.join(broken)}")
        return 1
    print("\nEvery route this can address is addressed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
