from __future__ import annotations

import time
import tkinter as tk
from tkinter import ttk
from typing import Literal


def format_eta(elapsed_sec: float, pct: float) -> str | None:
    """Return a human-readable Vietnamese ETA, or None if we can't estimate yet.

    - pct <= 1 or elapsed <= 0 -> None (too early to estimate)
    - < 60s -> "còn ~X giây"
    - 60s-5min -> "còn ~X phút Y giây"
    - 5min-1h -> "còn ~X phút"
    - >= 1h -> "còn ~Hh Mm"
    """
    if pct <= 1.0 or elapsed_sec <= 0:
        return None
    remaining = int(round(elapsed_sec * (100.0 - pct) / pct))
    if remaining < 60:
        return f"còn ~{remaining} giây"
    if remaining < 3600:
        minutes, seconds = divmod(remaining, 60)
        if minutes >= 5:
            return f"còn ~{minutes} phút"
        return f"còn ~{minutes} phút {seconds} giây"
    hours, rem = divmod(remaining, 3600)
    minutes = rem // 60
    return f"còn ~{hours}h {minutes}m"


_INFO_FG = "#374151"
_PANEL_BG = "#f8fafc"


class ProgressPanel(tk.Frame):
    """Two-row progress widget: bar on top, info line below.

    Info line format (segments joined by " • "):
        "<pct>% • <eta> • <status>"
    Each segment is omitted when unknown.

    All public methods are safe to call from worker threads; they marshal
    the actual UI update onto the Tk main loop via after(0, ...).
    """

    def __init__(self, parent: tk.Misc, *, bg: str = _PANEL_BG) -> None:
        super().__init__(parent, bg=bg)
        self._bg = bg
        self._mode: Literal["idle", "determinate", "indeterminate"] = "idle"
        self._start_time: float | None = None

        self._bar = ttk.Progressbar(self, mode="determinate", maximum=100.0)
        self._bar.pack(fill="x")

        self._info_var = tk.StringVar(value="")
        tk.Label(
            self,
            textvariable=self._info_var,
            bg=bg,
            fg=_INFO_FG,
            anchor="w",
            font=("Segoe UI", 9),
        ).pack(fill="x", pady=(4, 0))

    # ----- thread-safe public API -----

    def start(self, status: str = "Bắt đầu...") -> None:
        self.after(0, lambda: self._start(status))

    def set_indeterminate(self, status: str) -> None:
        self.after(0, lambda: self._set_indeterminate(status))

    def set_progress(self, pct: float, status: str) -> None:
        self.after(0, lambda: self._set_progress(pct, status))

    def finish(self, status: str = "Hoàn tất") -> None:
        self.after(0, lambda: self._finish(status))

    def reset(self) -> None:
        self.after(0, self._reset)

    # ----- UI-thread implementations -----

    def _start(self, status: str) -> None:
        if not self.winfo_exists():
            return
        self._switch_mode("determinate")
        self._start_time = time.monotonic()
        self._bar.configure(value=0.0)
        self._info_var.set(status)

    def _set_indeterminate(self, status: str) -> None:
        if not self.winfo_exists():
            return
        self._switch_mode("indeterminate")
        self._info_var.set(status)

    def _set_progress(self, pct: float, status: str) -> None:
        if not self.winfo_exists():
            return
        if self._mode != "determinate":
            self._switch_mode("determinate")
            if self._start_time is None:
                self._start_time = time.monotonic()
        pct = max(0.0, min(100.0, pct))
        self._bar.configure(value=pct)
        elapsed = (
            time.monotonic() - self._start_time if self._start_time else 0.0
        )
        parts = [f"{int(pct)}%"]
        eta = format_eta(elapsed, pct)
        if eta:
            parts.append(eta)
        if status:
            parts.append(status)
        self._info_var.set(" • ".join(parts))

    def _finish(self, status: str) -> None:
        if not self.winfo_exists():
            return
        self._switch_mode("determinate")
        self._bar.configure(value=100.0)
        self._start_time = None
        self._info_var.set(status)

    def _reset(self) -> None:
        if not self.winfo_exists():
            return
        self._switch_mode("idle")
        self._bar.configure(value=0.0)
        self._info_var.set("")
        self._start_time = None

    def _switch_mode(
        self, mode: Literal["idle", "determinate", "indeterminate"]
    ) -> None:
        if mode == self._mode:
            return
        if self._mode == "indeterminate":
            try:
                self._bar.stop()
            except tk.TclError:
                pass
        if mode == "indeterminate":
            self._bar.configure(mode="indeterminate")
            self._bar.start(80)
        else:
            self._bar.configure(mode="determinate")
        self._mode = mode
