"""Desktop interface.

Everything the command line can do, in one window: enrol a voice, watch a test
attempt score against the threshold, change settings, and start the overlay.

The interface is built on tkinter, which ships with Python, so the GUI adds no
dependency to a project whose selling point is that its core needs only numpy.

Two rules shape the code. Recording and model loading block for seconds at a
time, so they run on worker threads and report back through a queue that the
Tk event loop polls; anything else freezes the window mid-recording. And every
panel has to render before the microphone, the speech model, or a profile are
known to exist, because the first thing a new user sees is a window with none
of them present, and it should explain that rather than fail to open.
"""

from __future__ import annotations

import queue
import threading
import tkinter as tk
from dataclasses import dataclass
from tkinter import messagebox, ttk

from .defaults import DEFAULT_SENSITIVITY
from .phrase import enrolment_prompts, ephemeral_phrase, format_phrase, phrase_today
from .storage import Config, profile_exists, profile_path

# `features`, `voiceprint` and `audio` are imported where they are used rather
# than here. None of them is needed to draw the window, and all of them are
# slow: numpy alone is a sixth of a second, which is a sixth of a second of
# nothing on screen.

BG = "#0d1117"
PANEL = "#161b22"
BORDER = "#30363d"
TEXT = "#e6edf3"
DIM = "#8b949e"
ACCENT = "#58a6ff"
GOOD = "#3fb950"
WARN = "#d29922"
BAD = "#f85149"

FONT = "Segoe UI"


@dataclass
class Message:
    """Anything a worker thread sends back to the interface."""

    kind: str
    payload: object = None


def _card(parent: tk.Widget, title: str) -> tk.Frame:
    """A titled panel."""
    outer = tk.Frame(parent, bg=PANEL, highlightbackground=BORDER, highlightthickness=1)
    outer.pack(fill="x", pady=(0, 12))
    if title:
        tk.Label(
            outer, text=title.upper(), font=(FONT, 8, "bold"), fg=DIM, bg=PANEL,
        ).pack(anchor="w", padx=14, pady=(10, 0))
    return outer


def _button(parent: tk.Widget, text: str, command, *, primary: bool = False) -> tk.Button:
    return tk.Button(
        parent, text=text, command=command,
        font=(FONT, 10, "bold" if primary else "normal"),
        bg=ACCENT if primary else PANEL,
        fg="#0d1117" if primary else TEXT,
        activebackground=ACCENT if primary else BORDER,
        activeforeground="#0d1117" if primary else TEXT,
        relief="flat", bd=0, padx=16, pady=8, cursor="hand2",
        highlightbackground=BORDER, highlightthickness=0 if primary else 1,
    )


class ScoreBar(tk.Canvas):
    """Shows where an attempt landed relative to the threshold.

    A bare number does not tell a user whether -1.1 was comfortable or lucky.
    Drawing the threshold as a line and the attempt as a marker makes the margin
    visible, which is the number that actually predicts whether unlocking will
    keep working tomorrow.
    """

    LOW, HIGH = -4.0, 0.0

    def __init__(self, parent: tk.Widget):
        super().__init__(parent, height=54, bg=PANEL, highlightthickness=0)
        self._score: float | None = None
        self._threshold: float | None = None
        self.bind("<Configure>", lambda _e: self._redraw())

    def show(self, score: float | None, threshold: float | None) -> None:
        self._score, self._threshold = score, threshold
        self._redraw()

    def _x(self, value: float) -> float:
        span = self.HIGH - self.LOW
        clamped = max(self.LOW, min(self.HIGH, value))
        return 14 + (clamped - self.LOW) / span * (max(self.winfo_width(), 60) - 28)

    def _redraw(self) -> None:
        self.delete("all")
        width = max(self.winfo_width(), 60)
        track_y = 30
        self.create_line(14, track_y, width - 14, track_y, fill=BORDER, width=6)

        if self._threshold is None:
            return

        # Everything to the right of the threshold is the accepting region.
        self.create_line(
            self._x(self._threshold), track_y, width - 14, track_y,
            fill="#1f3b2a", width=6,
        )
        tx = self._x(self._threshold)
        self.create_line(tx, track_y - 11, tx, track_y + 11, fill=WARN, width=2)
        self.create_text(tx, track_y + 21, text="threshold", fill=WARN, font=(FONT, 7))

        if self._score is None:
            return
        sx = self._x(self._score)
        passing = self._score >= self._threshold
        self.create_oval(
            sx - 7, track_y - 7, sx + 7, track_y + 7,
            fill=GOOD if passing else BAD, outline=BG, width=2,
        )
        self.create_text(
            sx, track_y - 18, text=f"{self._score:+.2f}",
            fill=GOOD if passing else BAD, font=(FONT, 8, "bold"),
        )


class EnrolDialog(tk.Toplevel):
    """Walks through the enrolment recordings."""

    def __init__(self, parent: "EchoLockGUI", samples: int, sensitivity: float):
        super().__init__(parent.root)
        self.parent = parent
        self.total = samples
        self.sensitivity = sensitivity
        self.index = 0
        self.prompts = enrolment_prompts(samples, parent.config.word_count)
        self.captured: list = []
        self.results: queue.Queue[Message] = queue.Queue()
        self.busy = False

        self.title("Enrol your voice")
        self.configure(bg=BG)
        self.geometry("560x330")
        self.transient(parent.root)
        self.grab_set()
        self.protocol("WM_DELETE_WINDOW", self._cancel)

        tk.Label(
            self, text="Read each line aloud in your normal speaking voice.",
            font=(FONT, 10), fg=DIM, bg=BG, wraplength=500,
        ).pack(pady=(18, 4))
        tk.Label(
            self, text="Sit where you normally sit. The profile learns your microphone\n"
                       "and your distance from it as much as your voice.",
            font=(FONT, 8), fg=DIM, bg=BG, justify="center",
        ).pack(pady=(0, 14))

        self.progress = tk.Label(self, text="", font=(FONT, 9), fg=ACCENT, bg=BG)
        self.progress.pack()

        self.prompt = tk.Label(
            self, text="", font=(FONT, 13), fg=TEXT, bg=PANEL,
            wraplength=480, padx=20, pady=18, justify="center",
        )
        self.prompt.pack(fill="x", padx=24, pady=12)

        self.status = tk.Label(self, text="", font=(FONT, 9), fg=DIM, bg=BG)
        self.status.pack()

        row = tk.Frame(self, bg=BG)
        row.pack(pady=14)
        self.record_button = _button(row, "Record  (Enter)", self._record, primary=True)
        self.record_button.pack(side="left", padx=6)
        _button(row, "Cancel", self._cancel).pack(side="left", padx=6)

        self.bind("<Return>", lambda _e: self._record())
        self._show_prompt()

    def _show_prompt(self) -> None:
        self.progress.config(text=f"Recording {self.index + 1} of {self.total}")
        self.prompt.config(text=self.prompts[self.index])

    def _record(self) -> None:
        if self.busy:
            return
        self.busy = True
        self.record_button.config(state="disabled", text="Listening...")
        self.status.config(text="speak now", fg=ACCENT)

        config = self.parent.config
        seconds = config.record_seconds

        def worker() -> None:
            try:
                from .audio import looks_clipped, peak_level, record

                audio = record(seconds, config.sample_rate, device=config.input_device)
                self.results.put(Message("take", (audio, peak_level(audio), looks_clipped(audio))))
            except Exception as exc:  # noqa: BLE001
                self.results.put(Message("error", str(exc)))

        threading.Thread(target=worker, daemon=True).start()
        self.after(120, self._poll)

    def _poll(self) -> None:
        try:
            message = self.results.get_nowait()
        except queue.Empty:
            self.after(120, self._poll)
            return

        self.busy = False
        self.record_button.config(state="normal", text="Record  (Enter)")

        if message.kind == "error":
            self.status.config(text=str(message.payload), fg=BAD)
            return

        audio, level, clipped = message.payload  # type: ignore[misc]
        if level < 0.01:
            self.status.config(text="nothing was heard; try that one again", fg=BAD)
            return
        if clipped:
            self.status.config(text="that clipped; move back a little and retry", fg=BAD)
            return

        self.captured.append(audio)
        self.status.config(text=f"captured (peak {level:.2f})", fg=GOOD)
        self.index += 1
        if self.index >= self.total:
            self._finish()
        else:
            self._show_prompt()

    def _finish(self) -> None:
        if len(self.captured) < 3:
            messagebox.showerror(
                "Not enough audio",
                f"Only {len(self.captured)} usable recordings. At least 3 are needed.",
                parent=self,
            )
            self.destroy()
            return
        from .features import FeatureConfig
        from .voiceprint import build_voiceprint

        try:
            voiceprint = build_voiceprint(
                self.captured,
                FeatureConfig(sample_rate=self.parent.config.sample_rate),
                sensitivity=self.sensitivity,
            )
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("Enrolment failed", str(exc), parent=self)
            self.destroy()
            return

        voiceprint.save(profile_path())
        calibration = voiceprint.calibration
        spread = calibration["loo_max"] - calibration["loo_min"]

        note = f"Saved from {voiceprint.n_samples} recordings.\n\nThreshold {voiceprint.threshold:+.3f}\nConsistency spread {spread:.2f}"
        if calibration["clamped"]:
            note += ("\n\nYour takes varied so much that the threshold had to be capped, "
                     "and unlocking will be unreliable. Re-enrol somewhere quieter.")
        elif spread > 1.0:
            note += "\n\nYour takes varied a fair amount, so the threshold is looser than ideal."
        else:
            note += "\n\nThat is a consistent set of recordings."

        self.destroy()
        messagebox.showinfo("Enrolment complete", note, parent=self.parent.root)
        self.parent.refresh()

    def _cancel(self) -> None:
        self.destroy()


class EchoLockGUI:
    """The main window."""

    def __init__(self) -> None:
        self.config = Config.load()
        self.voiceprint: Voiceprint | None = None
        self.phrase = None
        self.transcriber = None
        self.model_error: str | None = None
        self.watching = False
        self._watch_stop = threading.Event()
        self.results: queue.Queue[Message] = queue.Queue()
        self.testing = False

        self.root = tk.Tk()
        self.root.title("EchoLock")
        self.root.configure(bg=BG)
        self.root.geometry("620x760")
        self.root.minsize(560, 680)

        self._build()

        # Draw the window before doing anything expensive. Reading the profile
        # pulls in numpy and listing microphones opens the audio backend, and
        # neither is needed for the layout; running them first left the screen
        # empty for most of the startup time. The window comes up filled with
        # placeholders and finishes itself a frame later.
        self.root.update_idletasks()
        self.root.after_idle(self._start)

    # -- layout -----------------------------------------------------------

    def _build(self) -> None:
        outer = tk.Frame(self.root, bg=BG)
        outer.pack(fill="both", expand=True, padx=18, pady=16)

        header = tk.Frame(outer, bg=BG)
        header.pack(fill="x", pady=(0, 14))
        tk.Label(header, text="EchoLock", font=(FONT, 17, "bold"), fg=ACCENT, bg=BG).pack(side="left")
        self.state_label = tk.Label(header, text="", font=(FONT, 9), fg=DIM, bg=BG)
        self.state_label.pack(side="right")

        # -- phrase --------------------------------------------------------
        card = _card(outer, "phrase to say")
        self.phrase_label = tk.Label(
            card, text="", font=("Consolas", 17, "bold"), fg=TEXT, bg=PANEL, wraplength=520,
        )
        self.phrase_label.pack(padx=14, pady=(8, 6))

        row = tk.Frame(card, bg=PANEL)
        row.pack(fill="x", padx=14, pady=(0, 12))
        self.mode_var = tk.BooleanVar(value=self.config.per_attempt_phrase)
        tk.Checkbutton(
            row, text="New phrase every attempt", variable=self.mode_var,
            command=self._toggle_mode, font=(FONT, 9), fg=TEXT, bg=PANEL,
            selectcolor=BG, activebackground=PANEL, activeforeground=TEXT,
            highlightthickness=0, bd=0,
        ).pack(side="left")
        _button(row, "New phrase", self._new_phrase).pack(side="right")

        # -- test ----------------------------------------------------------
        card = _card(outer, "test your voice")
        self.bar = ScoreBar(card)
        self.bar.pack(fill="x", padx=14, pady=(6, 0))
        self.result_label = tk.Label(card, text="", font=(FONT, 11, "bold"), fg=DIM, bg=PANEL)
        self.result_label.pack(pady=(2, 2))
        self.detail_label = tk.Label(
            card, text="", font=(FONT, 9), fg=DIM, bg=PANEL, wraplength=520, justify="center",
        )
        self.detail_label.pack(pady=(0, 6))
        self.test_button = _button(card, "Speak now", self._test, primary=True)
        self.test_button.pack(pady=(0, 14))
        # Only shown when the speech model is absent; getting it is otherwise a
        # download, an unpack, and a folder most people would never find.
        self.download_button = _button(card, "Download speech model (40 MB)", self._download)

        # -- profile -------------------------------------------------------
        card = _card(outer, "voice profile")
        self.profile_label = tk.Label(
            card, text="", font=(FONT, 9), fg=DIM, bg=PANEL, justify="left", anchor="w",
        )
        self.profile_label.pack(fill="x", padx=14, pady=(6, 8))
        row = tk.Frame(card, bg=PANEL)
        row.pack(fill="x", padx=14, pady=(0, 12))
        self.enrol_button = _button(row, "Enrol", self._enrol)
        self.enrol_button.pack(side="left")
        _button(row, "Delete profile", self._reset).pack(side="right")

        # -- settings ------------------------------------------------------
        card = _card(outer, "settings")
        grid = tk.Frame(card, bg=PANEL)
        grid.pack(fill="x", padx=14, pady=(8, 12))

        tk.Label(grid, text="Microphone", font=(FONT, 9), fg=DIM, bg=PANEL).grid(row=0, column=0, sticky="w", pady=3)
        self.device_var = tk.StringVar()
        self.device_menu = ttk.Combobox(grid, textvariable=self.device_var, state="readonly", width=34)
        self.device_menu.grid(row=0, column=1, sticky="e", pady=3)
        self.device_menu.bind("<<ComboboxSelected>>", self._pick_device)

        tk.Label(grid, text="Words in phrase", font=(FONT, 9), fg=DIM, bg=PANEL).grid(row=1, column=0, sticky="w", pady=3)
        self.words_var = tk.IntVar(value=self.config.word_count)
        tk.Spinbox(
            grid, from_=2, to=8, textvariable=self.words_var, width=5, command=self._save_settings,
            bg=BG, fg=TEXT, buttonbackground=PANEL, relief="flat", highlightthickness=1,
            highlightbackground=BORDER,
        ).grid(row=1, column=1, sticky="e", pady=3)

        tk.Label(grid, text="Seconds to record", font=(FONT, 9), fg=DIM, bg=PANEL).grid(row=2, column=0, sticky="w", pady=3)
        self.seconds_var = tk.DoubleVar(value=self.config.record_seconds)
        tk.Spinbox(
            grid, from_=2.0, to=10.0, increment=0.5, textvariable=self.seconds_var, width=5,
            command=self._save_settings, bg=BG, fg=TEXT, buttonbackground=PANEL,
            relief="flat", highlightthickness=1, highlightbackground=BORDER,
        ).grid(row=2, column=1, sticky="e", pady=3)
        grid.columnconfigure(1, weight=1)

        # -- lock ----------------------------------------------------------
        card = _card(outer, "screen lock")

        login_row = tk.Frame(card, bg=PANEL)
        login_row.pack(fill="x", padx=14, pady=(8, 2))
        self.autostart_var = tk.BooleanVar(value=False)
        tk.Checkbutton(
            login_row, text="Lock at every Windows sign-in", variable=self.autostart_var,
            command=self._toggle_autostart, font=(FONT, 9), fg=TEXT, bg=PANEL,
            selectcolor=BG, activebackground=PANEL, activeforeground=TEXT,
            highlightthickness=0, bd=0,
        ).pack(side="left")
        tk.Label(
            card,
            text="The overlay opens as soon as you sign in, so the desktop stays hidden\n"
                 "until the phrase is spoken. Windows still asks for your password first.",
            font=(FONT, 8), fg=DIM, bg=PANEL, justify="left", anchor="w",
        ).pack(fill="x", padx=32, pady=(0, 8))

        idle_row = tk.Frame(card, bg=PANEL)
        idle_row.pack(fill="x", padx=14, pady=(0, 2))
        self.idle_var = tk.BooleanVar(value=False)
        tk.Checkbutton(
            idle_row, text="Lock after", variable=self.idle_var,
            command=self._toggle_watch, font=(FONT, 9), fg=TEXT, bg=PANEL,
            selectcolor=BG, activebackground=PANEL, activeforeground=TEXT,
            highlightthickness=0, bd=0,
        ).pack(side="left")
        self.idle_minutes = tk.DoubleVar(value=5.0)
        tk.Spinbox(
            idle_row, from_=1.0, to=60.0, increment=1.0, textvariable=self.idle_minutes,
            width=4, bg=BG, fg=TEXT, buttonbackground=PANEL, relief="flat",
            highlightthickness=1, highlightbackground=BORDER,
        ).pack(side="left", padx=6)
        tk.Label(idle_row, text="minutes of inactivity", font=(FONT, 9), fg=DIM, bg=PANEL).pack(side="left")

        self.lock_button = _button(card, "Lock now", self._lock, primary=True)
        self.lock_button.pack(fill="x", padx=14, pady=(10, 12))

        tk.Label(
            outer,
            text="Escape falls back to the Windows login, so this never leaves the\n"
                 "session less protected than the operating system already makes it.",
            font=(FONT, 8), fg=DIM, bg=BG, justify="center",
        ).pack()

        # The profile has not been read yet, so nothing that needs one is
        # clickable. `refresh` enables these once it knows what exists.
        self.test_button.config(state="disabled")
        self.lock_button.config(state="disabled")
        self.state_label.config(text="starting...", fg=DIM)

    # -- state ------------------------------------------------------------

    def _start(self) -> None:
        """The work that was too slow to do before the window appeared."""
        self._populate_devices()
        self.refresh()
        self._load_model()

    def refresh(self) -> None:
        """Re-read the profile and repaint everything that depends on it."""
        if profile_exists():
            from .voiceprint import Voiceprint

            self.voiceprint = Voiceprint.load(profile_path())
        else:
            self.voiceprint = None
        self._new_phrase()

        if self.voiceprint is None:
            self.profile_label.config(text="No voice enrolled yet.\nEnrol to get started.")
            self.enrol_button.config(text="Enrol")
            self.test_button.config(state="disabled")
            self.lock_button.config(state="disabled")
            self.bar.show(None, None)
        else:
            calibration = self.voiceprint.calibration
            spread = calibration.get("loo_max", 0) - calibration.get("loo_min", 0)
            quality = "consistent" if spread <= 1.0 else "variable"
            self.profile_label.config(
                text=(
                    f"Enrolled {self.voiceprint.created_at[:10]} from "
                    f"{self.voiceprint.n_samples} recordings\n"
                    f"Threshold {self.voiceprint.threshold:+.3f}    "
                    f"Spread {spread:.2f} ({quality})"
                )
            )
            self.enrol_button.config(text="Re-enrol")
            self.test_button.config(state="normal")
            self.lock_button.config(state="normal")
            self.bar.show(None, self.voiceprint.threshold)
        self._refresh_autostart()
        self._update_state_label()

    def _update_state_label(self) -> None:
        if self.voiceprint is None:
            self.state_label.config(text="not enrolled", fg=WARN)
        elif self.model_error:
            self.state_label.config(text="speech model missing", fg=BAD)
        elif self.transcriber is None:
            self.state_label.config(text="loading speech model...", fg=DIM)
        else:
            self.state_label.config(text="ready", fg=GOOD)

    def _populate_devices(self) -> None:
        try:
            from .audio import list_devices

            devices = list_devices()
        except Exception:  # noqa: BLE001
            devices = []
        self._devices = devices
        labels = ["System default"] + [f"[{d['index']}] {d['name']}" for d in devices]
        self.device_menu["values"] = labels
        current = self.config.input_device
        match = next((i for i, d in enumerate(devices) if d["index"] == current), None)
        self.device_menu.current(0 if match is None else match + 1)

    # -- speech model -----------------------------------------------------

    def _load_model(self) -> None:
        def worker() -> None:
            try:
                from .asr import VoskTranscriber

                model = VoskTranscriber(self.config.vosk_model_path or None, self.config.sample_rate)
                self.results.put(Message("model", model))
            except Exception as exc:  # noqa: BLE001
                self.results.put(Message("model_error", str(exc)))

        threading.Thread(target=worker, daemon=True).start()
        self.root.after(150, self._poll_model)

    def _poll_model(self) -> None:
        try:
            message = self.results.get_nowait()
        except queue.Empty:
            self.root.after(150, self._poll_model)
            return
        if message.kind == "model":
            self.transcriber = message.payload
        else:
            self.model_error = str(message.payload)
            self.detail_label.config(
                text="The speech model is not installed, so the phrase cannot be checked.",
                fg=WARN,
            )
            self.download_button.pack(pady=(0, 14))
        self._update_state_label()

    # -- actions ----------------------------------------------------------

    def _new_phrase(self) -> None:
        if self.config.per_attempt_phrase:
            self.phrase = ephemeral_phrase(self.config.word_count)
        else:
            self.phrase = phrase_today(self.config.salt, self.config.word_count)
        self.phrase_label.config(text=self.phrase.text)

    def _toggle_mode(self) -> None:
        self.config.per_attempt_phrase = self.mode_var.get()
        self.config.save()
        self._new_phrase()

    def _pick_device(self, _event=None) -> None:
        selection = self.device_menu.current()
        self.config.input_device = None if selection <= 0 else self._devices[selection - 1]["index"]
        self.config.save()

    def _save_settings(self) -> None:
        try:
            self.config.word_count = max(2, int(self.words_var.get()))
            self.config.record_seconds = max(1.0, float(self.seconds_var.get()))
        except (TypeError, ValueError):
            return
        self.config.save()
        self._new_phrase()

    def _enrol(self) -> None:
        EnrolDialog(self, samples=10, sensitivity=DEFAULT_SENSITIVITY)

    def _reset(self) -> None:
        if not messagebox.askyesno("Delete profile", "Delete the enrolled voice profile?"):
            return
        for path in (profile_path(), profile_path().with_suffix(".json")):
            if path.exists():
                path.unlink()
        self.refresh()

    def _test(self) -> None:
        if self.testing or self.voiceprint is None:
            return
        self.testing = True
        self.test_button.config(state="disabled", text="Listening...")
        self.result_label.config(text="", fg=DIM)
        self.detail_label.config(text="say the phrase above", fg=ACCENT)

        phrase, config, voiceprint = self.phrase, self.config, self.voiceprint
        transcriber = self.transcriber

        def worker() -> None:
            try:
                from .audio import record
                from .features import FeatureConfig
                from .verifier import verify

                audio = record(config.record_seconds, config.sample_rate, device=config.input_device)
                if transcriber is None:
                    # No speech model: report the voice match alone, and say so.
                    self.results.put(Message("voice_only", voiceprint.score(audio)))
                    return
                decision = verify(
                    audio, list(phrase.keywords), voiceprint, transcriber,
                    FeatureConfig(sample_rate=config.sample_rate),
                    min_phrase_ratio=config.min_phrase_ratio,
                )
                self.results.put(Message("decision", decision))
            except Exception as exc:  # noqa: BLE001
                self.results.put(Message("test_error", str(exc)))

        threading.Thread(target=worker, daemon=True).start()
        self.root.after(120, self._poll_test)

    def _poll_test(self) -> None:
        try:
            message = self.results.get_nowait()
        except queue.Empty:
            self.root.after(120, self._poll_test)
            return

        # A late model-load reply can arrive on the same queue; handle and keep waiting.
        if message.kind in {"model", "model_error"}:
            if message.kind == "model":
                self.transcriber = message.payload
            else:
                self.model_error = str(message.payload)
            self._update_state_label()
            self.root.after(120, self._poll_test)
            return

        self.testing = False
        self.test_button.config(state="normal", text="Speak now")

        if message.kind == "test_error":
            self.result_label.config(text="Could not record", fg=BAD)
            self.detail_label.config(text=str(message.payload), fg=BAD)
            return

        if message.kind == "voice_only":
            score = float(message.payload)  # type: ignore[arg-type]
            self.bar.show(score, self.voiceprint.threshold)
            passing = score >= self.voiceprint.threshold
            self.result_label.config(
                text="Voice matches" if passing else "Voice does not match",
                fg=GOOD if passing else BAD,
            )
            self.detail_label.config(text="phrase not checked (no speech model)", fg=WARN)
            return

        decision = message.payload
        self.bar.show(decision.score, decision.threshold)
        self.result_label.config(
            text="Unlocked" if decision.unlocked else "Denied",
            fg=GOOD if decision.unlocked else BAD,
        )
        heard = decision.liveness.transcript if decision.liveness else ""
        detail = decision.reason
        if heard:
            detail += f'\nheard: "{heard}"'
        if decision.margin is not None:
            detail += f"\nmargin {decision.margin:+.3f}"
        self.detail_label.config(text=detail, fg=DIM)

        if self.config.per_attempt_phrase:
            self._new_phrase()

    def _download(self) -> None:
        """Fetch the speech model in the background, reporting progress."""
        self.download_button.config(state="disabled", text="Downloading...")

        def worker() -> None:
            try:
                from .download import download_model

                download_model(progress=lambda done, total: self.results.put(
                    Message("download_progress", int(done * 100 / max(total, 1)))
                ))
                self.results.put(Message("download_done"))
            except Exception as exc:  # noqa: BLE001
                self.results.put(Message("download_error", str(exc)))

        threading.Thread(target=worker, daemon=True).start()
        self.root.after(150, self._poll_download)

    def _poll_download(self) -> None:
        try:
            message = self.results.get_nowait()
        except queue.Empty:
            self.root.after(150, self._poll_download)
            return

        if message.kind == "download_progress":
            self.download_button.config(text=f"Downloading... {message.payload}%")
            self.root.after(150, self._poll_download)
            return
        if message.kind == "download_done":
            self.download_button.pack_forget()
            self.model_error = None
            self.detail_label.config(text="")
            self._load_model()
            return
        if message.kind == "download_error":
            self.download_button.config(state="normal", text="Download speech model (40 MB)")
            messagebox.showerror("Download failed", str(message.payload), parent=self.root)
            return
        # Anything else belongs to another poller; put it back and keep waiting.
        self.results.put(message)
        self.root.after(150, self._poll_download)

    def _toggle_autostart(self) -> None:
        """Add or remove the Startup shortcut that locks the screen at sign-in."""
        from .autostart import AutostartUnavailable, disable, enable

        wanted = self.autostart_var.get()
        try:
            if wanted:
                enable()
            else:
                disable()
        except AutostartUnavailable as exc:
            self.autostart_var.set(not wanted)
            messagebox.showinfo("Not available", str(exc), parent=self.root)
            return

        if wanted:
            messagebox.showinfo(
                "Locking at sign-in",
                "EchoLock will cover the desktop the next time you sign in.\n\n"
                "Windows still asks for your password at the login screen. That "
                "screen belongs to the operating system and no ordinary program "
                "can draw on it, so this guards the session behind it instead.",
                parent=self.root,
            )

    def _refresh_autostart(self) -> None:
        """Show whether the Startup shortcut is actually there."""
        try:
            from .autostart import is_enabled

            self.autostart_var.set(is_enabled())
        except Exception:  # noqa: BLE001
            self.autostart_var.set(False)

    def _toggle_watch(self) -> None:
        """Start or stop covering the screen after a period of inactivity."""
        from .idle import is_supported

        if not self.idle_var.get():
            self._watch_stop.set()
            self.watching = False
            return
        if not is_supported():
            self.idle_var.set(False)
            messagebox.showinfo(
                "Not available",
                "Idle detection is only implemented for Windows.",
                parent=self.root,
            )
            return
        if self.voiceprint is None:
            self.idle_var.set(False)
            messagebox.showinfo("Enrol first", "Enrol a voice before arming this.", parent=self.root)
            return

        self._watch_stop.clear()
        self.watching = True

        def worker() -> None:
            import time

            from .idle import idle_seconds

            threshold = max(30.0, float(self.idle_minutes.get()) * 60.0)
            armed = True
            while not self._watch_stop.is_set():
                try:
                    idle = idle_seconds()
                except Exception:  # noqa: BLE001
                    return
                if idle >= threshold and armed:
                    armed = False
                    # The overlay owns the Tk main loop, so ask the interface
                    # thread to raise it rather than touching widgets here.
                    self.results.put(Message("idle_trigger"))
                elif idle < threshold:
                    armed = True
                time.sleep(3.0)

        threading.Thread(target=worker, daemon=True).start()
        self.root.after(1000, self._poll_idle)

    def _poll_idle(self) -> None:
        if not self.watching:
            return
        try:
            message = self.results.get_nowait()
        except queue.Empty:
            self.root.after(1000, self._poll_idle)
            return
        if message.kind == "idle_trigger":
            self._lock()
        else:
            self.results.put(message)
        self.root.after(1000, self._poll_idle)

    def _lock(self) -> None:
        from .ui import run_overlay

        self.root.withdraw()
        try:
            run_overlay(lock_session=True)
        finally:
            self.root.deiconify()
            self.refresh()

    def run(self) -> int:
        self.root.mainloop()
        return 0


def run_gui() -> int:
    """Open the desktop interface."""
    try:
        return EchoLockGUI().run()
    except tk.TclError as exc:
        raise SystemExit(f"error: could not open a window ({exc})")
