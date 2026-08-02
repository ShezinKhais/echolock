"""The unlock overlay.

**What this is, precisely.** A fullscreen always-on-top window that covers the
desktop and dismisses when the enrolled speaker reads the current passphrase.
It is a convenience layer, not a security boundary, and the distinction is
worth stating plainly because it drives the whole design.

A background application on Windows cannot type into the real login screen:
Winlogon runs on a separate secure desktop that ordinary processes are
deliberately unable to reach. That isolation is the same mechanism that stops
malware from automating its way past a password prompt, and working around it
is neither possible from here nor desirable.

So the overlay does not replace Windows authentication and does not pretend to.
It sits in front of an already-unlocked session as a fast path for the person
who owns it, and every way out of it leads somewhere at least as safe:

* the phrase is verified: the overlay closes, revealing the session it was
  already covering, so nothing has been unlocked that was locked
* attempts are exhausted, or the user presses Escape: the real Windows lock
  is invoked, so the session ends up more protected than before
* the process is killed, or the machine reboots: Windows' own lock screen is
  untouched and still applies

There is no failure mode in which this software grants access that the
operating system would have denied. That property is what makes it responsible
to run, and it is the reason the fallback path locks the session rather than
simply closing the window.
"""

from __future__ import annotations

import queue
import threading
from dataclasses import dataclass

from .features import FeatureConfig
from .phrase import ephemeral_phrase, format_phrase, phrase_today
from .storage import Config, profile_path
from .verifier import Decision, verify
from .voiceprint import Voiceprint

MAX_ATTEMPTS = 3

BACKGROUND = "#0d1117"
PANEL = "#161b22"
TEXT = "#e6edf3"
DIM = "#8b949e"
ACCENT = "#58a6ff"
GOOD = "#3fb950"
BAD = "#f85149"


def lock_windows_session() -> bool:
    """Invoke the real Windows lock screen. Returns whether it succeeded."""
    try:
        import ctypes  # noqa: PLC0415

        return bool(ctypes.windll.user32.LockWorkStation())  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001 - not Windows, or the call was refused
        return False


@dataclass
class _Attempt:
    """Result passed from the worker thread back to the UI thread."""

    decision: Decision | None = None
    error: str | None = None


class Overlay:
    """Fullscreen tkinter overlay driving the verification loop."""

    def __init__(self, lock_session: bool = True):
        import tkinter as tk  # noqa: PLC0415 - only needed when the UI runs

        self._tk = tk
        self.lock_session = lock_session
        self.config = Config.load()
        self.voiceprint = Voiceprint.load(profile_path())
        self.feature_config = FeatureConfig(sample_rate=self.config.sample_rate)
        self.attempts_left = MAX_ATTEMPTS
        self.unlocked = False
        self._results: queue.Queue[_Attempt] = queue.Queue()
        self._transcriber = None
        self._busy = False

        self.phrase = self._next_phrase()
        self._build_window()

    # -- construction -----------------------------------------------------

    def _next_phrase(self) -> list[str]:
        if self.config.per_attempt_phrase:
            return ephemeral_phrase(self.config.word_count)
        return phrase_today(self.config.salt, self.config.word_count)

    def _build_window(self) -> None:
        tk = self._tk
        self.root = tk.Tk()
        self.root.title("EchoLock")
        self.root.configure(bg=BACKGROUND)
        self.root.attributes("-fullscreen", True)
        self.root.attributes("-topmost", True)
        # Closing the window must not simply reveal the desktop.
        self.root.protocol("WM_DELETE_WINDOW", self._fall_back_to_windows)
        self.root.bind("<Escape>", lambda _event: self._fall_back_to_windows())
        self.root.bind("<Return>", lambda _event: self._start_attempt())
        self.root.bind("<space>", lambda _event: self._start_attempt())

        frame = tk.Frame(self.root, bg=BACKGROUND)
        frame.place(relx=0.5, rely=0.5, anchor="center")

        tk.Label(frame, text="EchoLock", font=("Segoe UI", 15), fg=ACCENT, bg=BACKGROUND).pack()
        tk.Label(
            frame, text="Say the phrase below to unlock",
            font=("Segoe UI", 12), fg=DIM, bg=BACKGROUND,
        ).pack(pady=(4, 26))

        self.phrase_label = tk.Label(
            frame, text=format_phrase(self.phrase), font=("Consolas", 34, "bold"),
            fg=TEXT, bg=PANEL, padx=36, pady=22,
        )
        self.phrase_label.pack()

        self.status_label = tk.Label(
            frame, text="Press Enter or Space to speak",
            font=("Segoe UI", 13), fg=DIM, bg=BACKGROUND,
        )
        self.status_label.pack(pady=(26, 6))

        self.detail_label = tk.Label(
            frame, text="", font=("Segoe UI", 10), fg=DIM, bg=BACKGROUND,
        )
        self.detail_label.pack()

        tk.Label(
            frame,
            text="Escape  ->  use the Windows login instead",
            font=("Segoe UI", 10), fg=DIM, bg=BACKGROUND,
        ).pack(pady=(30, 0))

        self.root.after(100, self._prepare_transcriber)

    # -- worker ------------------------------------------------------------

    def _prepare_transcriber(self) -> None:
        """Load the speech model off the UI thread; it takes a moment."""

        def load() -> None:
            try:
                from .asr import VoskTranscriber

                self._transcriber = VoskTranscriber(
                    self.config.vosk_model_path or None, self.config.sample_rate
                )
                self._results.put(_Attempt())
            except Exception as exc:  # noqa: BLE001
                self._results.put(_Attempt(error=str(exc)))

        self._set_status("Loading speech model...", DIM)
        threading.Thread(target=load, daemon=True).start()
        self.root.after(120, self._poll_ready)

    def _poll_ready(self) -> None:
        try:
            outcome = self._results.get_nowait()
        except queue.Empty:
            self.root.after(120, self._poll_ready)
            return
        if outcome.error:
            self._set_status("Speech model unavailable", BAD)
            self.detail_label.config(text=f"{outcome.error}\nPress Escape to use the Windows login.")
        else:
            self._set_status("Press Enter or Space to speak", DIM)

    def _start_attempt(self) -> None:
        if self._busy or self._transcriber is None or self.unlocked:
            return
        self._busy = True
        self._set_status("Listening...", ACCENT)
        self.detail_label.config(text="")

        def worker() -> None:
            try:
                from .audio import record

                audio = record(self.config.record_seconds, self.config.sample_rate)
                decision = verify(
                    audio, self.phrase, self.voiceprint, self._transcriber,
                    self.feature_config, min_phrase_ratio=self.config.min_phrase_ratio,
                )
                self._results.put(_Attempt(decision=decision))
            except Exception as exc:  # noqa: BLE001 - a failure here must deny
                self._results.put(_Attempt(error=str(exc)))

        threading.Thread(target=worker, daemon=True).start()
        self.root.after(120, self._poll_attempt)

    def _poll_attempt(self) -> None:
        try:
            outcome = self._results.get_nowait()
        except queue.Empty:
            self.root.after(120, self._poll_attempt)
            return

        self._busy = False
        if outcome.error is not None or outcome.decision is None:
            self._register_failure(outcome.error or "verification failed")
            return
        if outcome.decision.unlocked:
            self.unlocked = True
            self._set_status("Unlocked", GOOD)
            self.root.after(400, self.root.destroy)
            return
        self._register_failure(outcome.decision.reason)

    def _register_failure(self, reason: str) -> None:
        self.attempts_left -= 1
        if self.attempts_left <= 0:
            self._set_status("Too many attempts", BAD)
            self.detail_label.config(text="Falling back to the Windows login.")
            self.root.after(900, self._fall_back_to_windows)
            return

        # A fresh phrase after every failure, so a listener who overheard the
        # previous prompt gains nothing from the next attempt.
        self.phrase = self._next_phrase()
        self.phrase_label.config(text=format_phrase(self.phrase))
        self._set_status(f"Not recognised  ({self.attempts_left} left)", BAD)
        self.detail_label.config(text=f"{reason}\nPress Enter to try again.")

    # -- exits -------------------------------------------------------------

    def _fall_back_to_windows(self) -> None:
        """Hand over to the real lock screen and close."""
        if self.lock_session:
            lock_windows_session()
        self.root.destroy()

    def _set_status(self, text: str, colour: str) -> None:
        self.status_label.config(text=text, fg=colour)

    def run(self) -> int:
        self.root.mainloop()
        return 0 if self.unlocked else 1


def run_overlay(lock_session: bool = True) -> int:
    """Show the overlay; returns 0 if it was unlocked by voice."""
    try:
        overlay = Overlay(lock_session=lock_session)
    except Exception as exc:  # noqa: BLE001
        # If the overlay cannot even start, leave the machine safer than found.
        if lock_session:
            lock_windows_session()
        raise SystemExit(f"error: could not start the overlay: {exc}")
    return overlay.run()
