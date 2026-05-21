# Busy Lock & Progress Bar Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** While a tool is running, disable every other control except Cancel; and replace the text-only status with a real progress bar showing percent + ETA + current state.

**Architecture:** Centralize a "busy lock" in `A4071App` that propagates to sidebar items, the logout link, and every `ToolPage` (which disables its own input widgets). Introduce a reusable `ProgressPanel` widget (`tk.Frame` wrapping a `ttk.Progressbar` + info label with ETA). MP3 Merger pre-scans total duration (ffprobe → MP3 header fallback) then parses ffmpeg `time=` for live %. MP3 to SRT feeds the panel from its existing per-segment callback.

**Tech Stack:** Python 3, Tkinter / ttk, `subprocess` for ffmpeg / ffprobe, `unittest` / `pytest` for tests.

**Spec:** `docs/superpowers/specs/2026-05-21-busy-lock-and-progress-design.md`

---

## File Map

**New:**
- `tool/tools/progress.py` — `ProgressPanel` widget + `format_eta` pure function
- `tool/tests/test_progress.py` — `format_eta` tests + a smoke test for `ProgressPanel`
- `tool/tests/test_mp3_duration.py` — `_parse_mp3_first_frame_bitrate_bps` + `mp3_duration` tests

**Modified:**
- `tool/tools/base.py` — Default `set_busy_lock` / `request_cancel` no-op methods on `ToolPage`
- `tool/a4071_tool.py` — `_busy_owner` state, `begin_busy` / `end_busy`, sidebar item disable, logout disable, `_on_close` cancel hook, `SidebarItem.set_enabled`
- `tool/tools/mp3_merger.py` — Promote widgets to attributes, `find_ffprobe`, `mp3_duration`, ffmpeg `time=` parsing, cancel button + flag, `ProgressPanel`, `set_busy_lock`, `request_cancel`, `begin_busy` / `end_busy` wiring
- `tool/tools/mp3_to_srt.py` — `transcribe_mp3` callback signature update, promote widgets to attributes, `ProgressPanel`, phase reporting, `set_busy_lock`, `request_cancel`, `begin_busy` / `end_busy` wiring

---

## Task 1: `format_eta` pure function

**Files:**
- Create: `tool/tools/progress.py`
- Create: `tool/tests/test_progress.py`

- [ ] **Step 1: Write the failing tests**

Create `tool/tests/test_progress.py`:

```python
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.progress import format_eta


class FormatEtaTests(unittest.TestCase):
    def test_returns_none_when_pct_is_zero(self) -> None:
        self.assertIsNone(format_eta(10.0, 0.0))

    def test_returns_none_when_pct_below_one(self) -> None:
        self.assertIsNone(format_eta(10.0, 0.5))

    def test_returns_none_when_elapsed_is_zero(self) -> None:
        self.assertIsNone(format_eta(0.0, 50.0))

    def test_seconds_bucket(self) -> None:
        # 10s elapsed at 25% -> 30s remaining
        self.assertEqual(format_eta(10.0, 25.0), "còn ~30 giây")

    def test_minutes_and_seconds_under_5_minutes(self) -> None:
        # 60s elapsed at 25% -> 180s remaining = 3 phút 0 giây
        self.assertEqual(format_eta(60.0, 25.0), "còn ~3 phút 0 giây")

    def test_minutes_only_at_or_above_5(self) -> None:
        # 60s elapsed at 10% -> 540s remaining = 9 phút
        self.assertEqual(format_eta(60.0, 10.0), "còn ~9 phút")

    def test_hours_format(self) -> None:
        # 3600s elapsed at 25% -> 10800s remaining = 3h 0m
        self.assertEqual(format_eta(3600.0, 25.0), "còn ~3h 0m")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test, verify it fails**

Run: `python -m pytest tool/tests/test_progress.py -v`
Expected: `ImportError` / `ModuleNotFoundError` for `tools.progress`.

- [ ] **Step 3: Create the minimal `progress.py` with `format_eta`**

Create `tool/tools/progress.py`:

```python
from __future__ import annotations


def format_eta(elapsed_sec: float, pct: float) -> str | None:
    """Return a human-readable Vietnamese ETA, or None if we can't estimate yet.

    - pct ≤ 1 or elapsed ≤ 0 → None (too early to estimate)
    - < 60s → "còn ~X giây"
    - 60s–5min → "còn ~X phút Y giây"
    - 5min–1h → "còn ~X phút"
    - ≥ 1h → "còn ~Hh Mm"
    """
    if pct <= 1.0 or elapsed_sec <= 0:
        return None
    remaining = elapsed_sec * (100.0 - pct) / pct
    if remaining < 60:
        return f"còn ~{int(round(remaining))} giây"
    if remaining < 3600:
        minutes = int(remaining // 60)
        seconds = int(round(remaining - minutes * 60))
        if minutes >= 5:
            return f"còn ~{minutes} phút"
        return f"còn ~{minutes} phút {seconds} giây"
    hours = int(remaining // 3600)
    minutes = int((remaining - hours * 3600) // 60)
    return f"còn ~{hours}h {minutes}m"
```

- [ ] **Step 4: Run test, verify it passes**

Run: `python -m pytest tool/tests/test_progress.py -v`
Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add tool/tools/progress.py tool/tests/test_progress.py
git commit -m "Add format_eta helper with bucketed Vietnamese output"
```

---

## Task 2: MP3 duration parser

**Files:**
- Create: `tool/tests/test_mp3_duration.py`
- Modify: `tool/tools/mp3_merger.py` (append at module level — don't touch the class yet)

- [ ] **Step 1: Write the failing tests**

Create `tool/tests/test_mp3_duration.py`:

```python
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.mp3_merger import _parse_mp3_first_frame_bitrate_bps, mp3_duration


def _mp3_frame_header_128kbps_44100_mono() -> bytes:
    # MPEG-1 Layer III, bitrate index 9 (128 kbps), sample rate index 0
    # (44100 Hz), no padding, mono.
    # FF FB 90 40
    #   FF       sync byte 1
    #   FB       sync (top 3 bits), MPEG-1 (11), Layer III (01), no CRC (1)
    #   90       bitrate idx 9 (1001), samplerate idx 0 (00), no padding,
    #            private (0)
    #   40       mono (11), mode ext 00, copyright 0, original 1, emphasis 00
    return bytes([0xFF, 0xFB, 0x90, 0x40])


class ParseFirstFrameTests(unittest.TestCase):
    def test_returns_bitrate_for_mpeg1_layer3_frame(self) -> None:
        header = _mp3_frame_header_128kbps_44100_mono()
        self.assertEqual(_parse_mp3_first_frame_bitrate_bps(header), 128_000)

    def test_skips_id3v2_tag_before_frame(self) -> None:
        # ID3v2 header: "ID3" + version (2 bytes) + flags (1) + syncsafe size (4)
        # Size 10 bytes (encoded), then 10 bytes of dummy tag, then frame header.
        tag_size = 10
        size_bytes = bytes([
            (tag_size >> 21) & 0x7F,
            (tag_size >> 14) & 0x7F,
            (tag_size >> 7) & 0x7F,
            tag_size & 0x7F,
        ])
        data = (
            b"ID3" + b"\x03\x00" + b"\x00" + size_bytes
            + b"\x00" * tag_size
            + _mp3_frame_header_128kbps_44100_mono()
        )
        self.assertEqual(_parse_mp3_first_frame_bitrate_bps(data), 128_000)

    def test_returns_none_for_invalid_bytes(self) -> None:
        self.assertIsNone(_parse_mp3_first_frame_bitrate_bps(b"\x00" * 32))

    def test_returns_none_for_too_short_buffer(self) -> None:
        self.assertIsNone(_parse_mp3_first_frame_bitrate_bps(b"\xFF\xFB"))


class Mp3DurationTests(unittest.TestCase):
    def test_returns_none_for_missing_file(self) -> None:
        self.assertIsNone(mp3_duration(Path("does/not/exist.mp3"), ffprobe=None))

    def test_estimates_duration_from_cbr_header(self) -> None:
        # 128 kbps = 16,000 bytes/sec. A 32,000-byte file → 2.0 seconds.
        header = _mp3_frame_header_128kbps_44100_mono()
        payload = header + b"\x00" * (32_000 - len(header))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "tone.mp3"
            path.write_bytes(payload)
            self.assertAlmostEqual(mp3_duration(path, ffprobe=None), 2.0, places=2)

    def test_returns_none_for_non_mp3_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "junk.mp3"
            path.write_bytes(b"NOT AN MP3" * 100)
            self.assertIsNone(mp3_duration(path, ffprobe=None))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test, verify it fails**

Run: `python -m pytest tool/tests/test_mp3_duration.py -v`
Expected: `ImportError` — `_parse_mp3_first_frame_bitrate_bps` and `mp3_duration` don't exist yet.

- [ ] **Step 3: Add the parser and the duration helper to `mp3_merger.py`**

Open `tool/tools/mp3_merger.py`. At the top, add `subprocess` is already imported and `CREATE_NO_WINDOW` is already defined.

After the existing `find_ffmpeg()` function (around line 46), add:

```python
def find_ffprobe() -> Optional[str]:
    candidates: list[Path] = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.append(Path(meipass) / "ffprobe.exe")
    candidates.append(app_root() / "ffprobe.exe")
    for c in candidates:
        if c.is_file():
            return str(c)
    found = shutil.which("ffprobe")
    return found


_MPEG1_L3_BITRATES_KBPS = [
    32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320,
]


def _parse_mp3_first_frame_bitrate_bps(data: bytes) -> Optional[int]:
    """Return the CBR bitrate in bits/sec, or None if not a recognizable
    MPEG-1 Layer III frame. Skips a leading ID3v2 tag if present."""
    offset = 0
    if len(data) >= 10 and data[:3] == b"ID3":
        size = (
            ((data[6] & 0x7F) << 21)
            | ((data[7] & 0x7F) << 14)
            | ((data[8] & 0x7F) << 7)
            | (data[9] & 0x7F)
        )
        offset = 10 + size
    while offset + 4 <= len(data):
        b0 = data[offset]
        b1 = data[offset + 1]
        b2 = data[offset + 2]
        if b0 == 0xFF and (b1 & 0xE0) == 0xE0:
            version = (b1 >> 3) & 0x3   # 0x3 = MPEG-1
            layer = (b1 >> 1) & 0x3     # 0x1 = Layer III
            bitrate_idx = (b2 >> 4) & 0xF
            if version == 0x3 and layer == 0x1 and 1 <= bitrate_idx <= 14:
                return _MPEG1_L3_BITRATES_KBPS[bitrate_idx - 1] * 1000
            return None
        offset += 1
    return None


def mp3_duration(path: Path, ffprobe: Optional[str]) -> Optional[float]:
    """Best-effort duration in seconds. Tries ffprobe first if available,
    then falls back to CBR estimation from the first MP3 frame header.
    Returns None on any failure."""
    if ffprobe:
        try:
            out = subprocess.check_output(
                [
                    ffprobe, "-v", "error", "-i", str(path),
                    "-show_entries", "format=duration",
                    "-of", "csv=p=0",
                ],
                stderr=subprocess.DEVNULL,
                creationflags=CREATE_NO_WINDOW,
                timeout=10,
            ).decode("ascii", errors="ignore").strip()
            if out:
                d = float(out)
                if d > 0:
                    return d
        except (subprocess.SubprocessError, ValueError, OSError):
            pass
    try:
        size = path.stat().st_size
        with path.open("rb") as f:
            head = f.read(4096)
    except OSError:
        return None
    bps = _parse_mp3_first_frame_bitrate_bps(head)
    if not bps:
        return None
    return size / (bps / 8)
```

- [ ] **Step 4: Run test, verify it passes**

Run: `python -m pytest tool/tests/test_mp3_duration.py -v`
Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add tool/tools/mp3_merger.py tool/tests/test_mp3_duration.py
git commit -m "Add ffprobe lookup and MP3 CBR duration estimator"
```

---

## Task 3: `ProgressPanel` widget

**Files:**
- Modify: `tool/tools/progress.py` (extend; `format_eta` already lives there)
- Modify: `tool/tests/test_progress.py` (append a smoke test)

- [ ] **Step 1: Append a smoke test to `test_progress.py`**

Add to the end of `tool/tests/test_progress.py`, before the `if __name__` block:

```python
import tkinter as tk

from tools.progress import ProgressPanel


class ProgressPanelSmokeTests(unittest.TestCase):
    def setUp(self) -> None:
        try:
            self.root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(f"Tk not available: {exc}")
        self.root.withdraw()

    def tearDown(self) -> None:
        self.root.destroy()

    def test_full_lifecycle_does_not_raise(self) -> None:
        panel = ProgressPanel(self.root)
        panel.pack()
        panel.start("Bắt đầu")
        panel.set_progress(50.0, "Đang chạy")
        panel.set_indeterminate("Tạm dừng")
        panel.set_progress(75.0, "Tiếp tục")
        panel.finish("Hoàn tất")
        panel.reset()
        self.root.update_idletasks()
```

- [ ] **Step 2: Run test, verify it fails**

Run: `python -m pytest tool/tests/test_progress.py::ProgressPanelSmokeTests -v`
Expected: `ImportError: cannot import name 'ProgressPanel'`.

- [ ] **Step 3: Implement `ProgressPanel`**

Append to `tool/tools/progress.py`:

```python
import time
import tkinter as tk
from tkinter import ttk
from typing import Literal


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
        self._switch_mode("determinate")
        self._start_time = time.monotonic()
        self._bar.configure(value=0.0)
        self._info_var.set(status)

    def _set_indeterminate(self, status: str) -> None:
        self._switch_mode("indeterminate")
        self._info_var.set(status)

    def _set_progress(self, pct: float, status: str) -> None:
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
        self._switch_mode("determinate")
        self._bar.configure(value=100.0)
        self._start_time = None
        self._info_var.set(status)

    def _reset(self) -> None:
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
```

- [ ] **Step 4: Run test, verify it passes**

Run: `python -m pytest tool/tests/test_progress.py -v`
Expected: 8 passed (or smoke test skipped on a headless box).

- [ ] **Step 5: Commit**

```bash
git add tool/tools/progress.py tool/tests/test_progress.py
git commit -m "Add ProgressPanel widget with thread-safe API"
```

---

## Task 4: `ToolPage` busy-lock contract

**Files:**
- Modify: `tool/tools/base.py`

- [ ] **Step 1: Add default `set_busy_lock` and `request_cancel`**

Replace the entire body of `tool/tools/base.py` with:

```python
from __future__ import annotations

import tkinter as tk
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from a4071_tool import A4071App


class ToolPage(ABC):
    name: str = "Tool"
    description: str = ""

    def __init__(self, parent: tk.Misc, app: "A4071App") -> None:
        self.app = app
        self.frame = tk.Frame(parent, bg="#f8fafc")
        self.build_ui(self.frame)

    @abstractmethod
    def build_ui(self, parent: tk.Misc) -> None:
        ...

    def show(self) -> None:
        self.frame.pack(fill="both", expand=True)

    def hide(self) -> None:
        self.frame.pack_forget()

    def set_busy_lock(self, locked: bool) -> None:
        """Disable / re-enable input controls on this page.
        Override in concrete tools. Cancel buttons stay under the tool's
        own start/finish logic and should NOT be touched here."""

    def request_cancel(self) -> None:
        """Request cancellation of the currently running job, if any.
        Override in concrete tools that support cancel."""
```

- [ ] **Step 2: Run the existing test suite to make sure nothing breaks**

Run: `python -m pytest tool/tests -v`
Expected: all passing tests still pass.

- [ ] **Step 3: Commit**

```bash
git add tool/tools/base.py
git commit -m "Add set_busy_lock and request_cancel hooks to ToolPage"
```

---

## Task 5: `SidebarItem.set_enabled`

**Files:**
- Modify: `tool/a4071_tool.py:50-83` (the `SidebarItem` class)

- [ ] **Step 1: Replace `SidebarItem` with enable-aware version**

Replace the `SidebarItem` class in `tool/a4071_tool.py` (lines 50-82) with:

```python
class SidebarItem(tk.Frame):
    def __init__(self, parent: tk.Misc, label: str, on_click) -> None:
        super().__init__(parent, bg=SIDEBAR_BG, cursor="hand2")
        self._label = tk.Label(
            self, text=label, bg=SIDEBAR_BG, fg=SIDEBAR_FG,
            anchor="w", padx=20, pady=11, font=("Segoe UI", 10),
        )
        self._label.pack(fill="x")
        self._on_click = on_click
        self._active = False
        self._enabled = True
        for widget in (self, self._label):
            widget.bind("<Button-1>", self._click)
            widget.bind("<Enter>", self._enter)
            widget.bind("<Leave>", self._leave)

    def _click(self, _evt) -> None:
        if not self._enabled:
            return
        self._on_click()

    def _enter(self, _evt) -> None:
        if not self._enabled or self._active:
            return
        self._set_bg(SIDEBAR_HOVER)

    def _leave(self, _evt) -> None:
        if not self._enabled or self._active:
            return
        self._set_bg(SIDEBAR_BG)

    def _set_bg(self, color: str) -> None:
        self.configure(bg=color)
        self._label.configure(bg=color)

    def set_active(self, active: bool) -> None:
        self._active = active
        self._set_bg(SIDEBAR_ACTIVE if active else SIDEBAR_BG)
        self._apply_fg()

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = enabled
        self.configure(cursor="hand2" if enabled else "arrow")
        self._apply_fg()

    def _apply_fg(self) -> None:
        if not self._enabled:
            self._label.configure(fg=SIDEBAR_MUTED_FG)
        else:
            self._label.configure(fg=SIDEBAR_FG)
```

- [ ] **Step 2: Smoke-test the app boot path**

Run: `python tool/a4071_tool.py` (or just `python -c "from a4071_tool import SidebarItem"`from inside `tool/`)
Expected: no exception. (Quit the GUI if it opens.)

- [ ] **Step 3: Commit**

```bash
git add tool/a4071_tool.py
git commit -m "Add enabled state to SidebarItem"
```

---

## Task 6: Busy lock plumbing in `A4071App`

**Files:**
- Modify: `tool/a4071_tool.py` — `__init__`, `_show_main`, add new methods, `_on_close`

- [ ] **Step 1: Add `_busy_owner` to `__init__`**

In `tool/a4071_tool.py`, find the `__init__` method of `A4071App` (around line 86). After `self._api_key: str | None = None` (line 96), add:

```python
        self._busy_owner: str | None = None
        self._logout_btn: tk.Label | None = None
        self._logout_enabled: bool = True
```

- [ ] **Step 2: Promote `logout_btn` to an attribute in `_show_main`**

In `_show_main`, find these lines (around 213-218):

```python
        logout_btn = tk.Label(
            header, text="Đăng xuất", bg=HEADER_BG, fg=HEADER_LINK_FG,
            font=("Segoe UI", 9, "underline"), padx=20, cursor="hand2",
        )
        logout_btn.pack(side="right", fill="y")
        logout_btn.bind("<Button-1>", lambda _e: self._on_logout())
```

Replace with:

```python
        self._logout_btn = tk.Label(
            header, text="Đăng xuất", bg=HEADER_BG, fg=HEADER_LINK_FG,
            font=("Segoe UI", 9, "underline"), padx=20, cursor="hand2",
        )
        self._logout_btn.pack(side="right", fill="y")
        self._logout_btn.bind("<Button-1>", lambda _e: self._on_logout_clicked())
        self._logout_enabled = True
```

- [ ] **Step 3: Add `_on_logout_clicked`, `_set_logout_enabled`, `begin_busy`, `end_busy`, `_apply_busy_lock`**

Add these methods to `A4071App` (anywhere in the class — put them just before the existing `_on_logout` method around line 260):

```python
    def _on_logout_clicked(self) -> None:
        if not self._logout_enabled:
            return
        self._on_logout()

    def _set_logout_enabled(self, enabled: bool) -> None:
        self._logout_enabled = enabled
        if self._logout_btn is None:
            return
        self._logout_btn.configure(
            fg=HEADER_LINK_FG if enabled else HEADER_MUTED_FG,
            cursor="hand2" if enabled else "arrow",
        )

    def begin_busy(self, owner: str) -> None:
        if self._busy_owner is not None:
            return
        self._busy_owner = owner
        self._apply_busy_lock(True)

    def end_busy(self, owner: str) -> None:
        if self._busy_owner != owner:
            return
        self._busy_owner = None
        self._apply_busy_lock(False)

    def _apply_busy_lock(self, locked: bool) -> None:
        for item in self._sidebar_items.values():
            item.set_enabled(not locked)
        self._set_logout_enabled(not locked)
        for page in self._pages.values():
            try:
                page.set_busy_lock(locked)
            except tk.TclError:
                pass
```

- [ ] **Step 4: Hook cancel into `_on_close`**

Find the existing `_on_close` (around line 101). Replace with:

```python
    def _on_close(self) -> None:
        if self._busy_owner and self._busy_owner in self._pages:
            try:
                self._pages[self._busy_owner].request_cancel()
            except Exception:
                pass
        try:
            self.withdraw()
        except tk.TclError:
            pass
        self.destroy()
```

- [ ] **Step 5: Smoke-test app boot**

Run: `python tool/a4071_tool.py`
Expected: app starts, sidebar / logout look unchanged. Close it.

- [ ] **Step 6: Commit**

```bash
git add tool/a4071_tool.py
git commit -m "Add busy lock state and propagation in A4071App"
```

---

## Task 7: MP3 Merger — UI refactor (keep widget refs, add ProgressPanel + Cancel)

**Files:**
- Modify: `tool/tools/mp3_merger.py` — `__init__`, `build_ui`, remove `status_var` bottom label

- [ ] **Step 1: Update `__init__` to also track cancel flag**

In `tool/tools/mp3_merger.py`, find the `MP3MergerPage.__init__` (around line 113). Replace with:

```python
    def __init__(self, parent: tk.Misc, app) -> None:
        self._files: list[Path] = []
        self._busy = False
        self._cancel_flag = False
        self._proc: subprocess.Popen | None = None
        super().__init__(parent, app)
```

- [ ] **Step 2: Update top-of-file imports**

The file already imports `ProgressPanel` indirectly — add this near the other `from .base import ToolPage` line (line 15):

```python
from .progress import ProgressPanel
```

- [ ] **Step 3: Rewrite `build_ui` to promote widgets and embed ProgressPanel**

Replace the entire `build_ui` method (lines 118-197) with:

```python
    def build_ui(self, parent: tk.Misc) -> None:
        outer = tk.Frame(parent, bg=PAGE_BG)
        outer.pack(fill="both", expand=True, padx=20, pady=16)

        card = tk.LabelFrame(
            outer, text=" Đầu vào ", bg=CARD_BG, fg=LABEL_FG,
            font=("Segoe UI Semibold", 10), padx=12, pady=10,
            bd=1, relief="solid",
        )
        card.pack(fill="x")

        tk.Label(card, text="Thư mục nguồn:", bg=CARD_BG, fg=LABEL_FG).grid(
            row=0, column=0, sticky="w", pady=4)
        self.src_var = tk.StringVar()
        self.src_entry = tk.Entry(card, textvariable=self.src_var)
        self.src_entry.grid(row=0, column=1, sticky="ew", padx=8, pady=4)
        self.src_pick_btn = ttk.Button(card, text="Chọn...", command=self._pick_src)
        self.src_pick_btn.grid(row=0, column=2, pady=4)

        tk.Label(card, text="File xuất ra:", bg=CARD_BG, fg=LABEL_FG).grid(
            row=1, column=0, sticky="w", pady=4)
        self.out_var = tk.StringVar()
        self.out_entry = tk.Entry(card, textvariable=self.out_var)
        self.out_entry.grid(row=1, column=1, sticky="ew", padx=8, pady=4)
        self.out_pick_btn = ttk.Button(card, text="Lưu thành...", command=self._pick_out)
        self.out_pick_btn.grid(row=1, column=2, pady=4)

        card.columnconfigure(1, weight=1)

        bar = tk.Frame(outer, bg=PAGE_BG)
        bar.pack(fill="x", pady=(10, 8))
        self.scan_btn = ttk.Button(bar, text="Quét", command=self._scan)
        self.scan_btn.pack(side="left")
        self.start_btn = ttk.Button(bar, text="Bắt đầu gộp", command=self._start)
        self.start_btn.pack(side="left", padx=8)
        self.cancel_btn = ttk.Button(
            bar, text="Hủy", command=self._cancel, state="disabled"
        )
        self.cancel_btn.pack(side="left")
        self.clear_log_btn = ttk.Button(bar, text="Xóa log", command=self._clear_log)
        self.clear_log_btn.pack(side="left", padx=8)
        self.count_var = tk.StringVar(value="0 file")
        tk.Label(bar, textvariable=self.count_var, bg=PAGE_BG, fg=MUTED_FG).pack(side="right")

        paned = ttk.PanedWindow(outer, orient="vertical")
        paned.pack(fill="both", expand=True)

        list_card = tk.LabelFrame(
            paned, text=" Danh sách file (theo thứ tự gộp) ", bg=CARD_BG, fg=LABEL_FG,
            font=("Segoe UI Semibold", 10), padx=6, pady=6,
            bd=1, relief="solid",
        )
        paned.add(list_card, weight=2)
        self.listbox = tk.Listbox(
            list_card, activestyle="none", bd=0, highlightthickness=0)
        lvsb = ttk.Scrollbar(list_card, orient="vertical", command=self.listbox.yview)
        lhsb = ttk.Scrollbar(list_card, orient="horizontal", command=self.listbox.xview)
        self.listbox.configure(yscrollcommand=lvsb.set, xscrollcommand=lhsb.set)
        self.listbox.grid(row=0, column=0, sticky="nsew")
        lvsb.grid(row=0, column=1, sticky="ns")
        lhsb.grid(row=1, column=0, sticky="ew")
        list_card.rowconfigure(0, weight=1)
        list_card.columnconfigure(0, weight=1)

        log_card = tk.LabelFrame(
            paned, text=" Nhật ký ", bg=CARD_BG, fg=LABEL_FG,
            font=("Segoe UI Semibold", 10), padx=6, pady=6,
            bd=1, relief="solid",
        )
        paned.add(log_card, weight=1)
        self.log_box = tk.Text(
            log_card, height=6, wrap="word", bd=0,
            highlightthickness=0, state="disabled",
        )
        log_vsb = ttk.Scrollbar(log_card, orient="vertical", command=self.log_box.yview)
        self.log_box.configure(yscrollcommand=log_vsb.set)
        self.log_box.grid(row=0, column=0, sticky="nsew")
        log_vsb.grid(row=0, column=1, sticky="ns")
        log_card.rowconfigure(0, weight=1)
        log_card.columnconfigure(0, weight=1)

        self.progress = ProgressPanel(outer)
        self.progress.pack(fill="x", pady=(8, 0))
```

Note: the old bottom `status_var` label is removed — `ProgressPanel` replaces it. Status updates inside the worker should be moved to the progress panel.

- [ ] **Step 4: Add stub `_cancel`, `_set_status` no longer needed — remove**

Add to the class (anywhere — put after `_clear_log` at the bottom):

```python
    def _cancel(self) -> None:
        if not self._busy:
            return
        self._cancel_flag = True
        self.progress.set_indeterminate("Đang hủy...")
        if self._proc is not None:
            try:
                self._proc.terminate()
            except OSError:
                pass
```

Find and DELETE the `_set_status` method (around line 345-346):

```python
    def _set_status(self, msg: str) -> None:
        self.frame.after(0, lambda: self.status_var.set(msg))
```

(Subsequent tasks rewire callers to talk to the progress panel.)

- [ ] **Step 5: Remove every reference to `self.status_var`**

In `_scan` (around lines 215-228): drop the two `self.status_var.set(...)` lines and the `self.frame.update_idletasks()` line. Replace the "Đang quét..." status with `self.progress.set_indeterminate("Đang quét...")` and the final "Tìm thấy..." status with `self.progress.finish(f"Tìm thấy {len(files)} file MP3")`.

The result for `_scan`:

```python
    def _scan(self) -> None:
        src = self.src_var.get().strip()
        if not src or not Path(src).is_dir():
            messagebox.showerror("Lỗi", "Hãy chọn thư mục nguồn hợp lệ.")
            return
        self.progress.set_indeterminate("Đang quét...")
        self.frame.update_idletasks()
        files = scan_mp3(Path(src))
        self._files = files
        self.listbox.delete(0, "end")
        for f in files:
            self.listbox.insert("end", str(f))
        self.count_var.set(f"{len(files)} file")
        self.progress.finish(f"Tìm thấy {len(files)} file MP3")
```

In `_start` (around line 230-278): drop the `self.status_var.set("Đang gộp...")` line — the worker will set its own status.

- [ ] **Step 6: Smoke-boot the app**

Run: `python tool/a4071_tool.py`
Expected: app boots, MP3 Merger page renders with new Cancel button (disabled) and ProgressPanel below the log card. Close it.

- [ ] **Step 7: Commit**

```bash
git add tool/tools/mp3_merger.py
git commit -m "MP3 Merger: promote widgets to attrs, add cancel button and ProgressPanel"
```

---

## Task 8: MP3 Merger — pre-scan total duration

**Files:**
- Modify: `tool/tools/mp3_merger.py` — `_start`, new `_do_merge_with_prep`

- [ ] **Step 1: Replace the thread launch in `_start`**

Find the bottom of `_start` (around lines 271-278). Replace:

```python
        self._busy = True
        self.start_btn.configure(state="disabled")
        self.status_var.set("Đang gộp...")
        threading.Thread(
            target=self._do_merge,
            args=(ffmpeg, list(self._files), out_path),
            daemon=True,
        ).start()
```

with:

```python
        self._busy = True
        self._cancel_flag = False
        self.app.begin_busy(self.name)
        self.start_btn.configure(state="disabled")
        self.cancel_btn.configure(state="normal")
        self.progress.set_indeterminate("Đang tính tổng thời lượng...")
        threading.Thread(
            target=self._do_merge_with_prep,
            args=(ffmpeg, list(self._files), out_path),
            daemon=True,
        ).start()
```

- [ ] **Step 2: Add `_do_merge_with_prep` method**

Add this method just before `_do_merge`:

```python
    def _do_merge_with_prep(
        self, ffmpeg: str, files: list[Path], out: Path
    ) -> None:
        ffprobe = find_ffprobe()
        total: float | None = 0.0
        for f in files:
            if self._cancel_flag:
                break
            d = mp3_duration(f, ffprobe)
            if d is None:
                total = None
                break
            total += d
        if self._cancel_flag:
            self._on_merge_done(cancelled=True, success=False, error=None, out=out)
            return
        if total is not None and total > 0:
            self.progress.start("Đang gộp...")
        else:
            self.progress.set_indeterminate("Đang gộp... (không xác định được tiến độ)")
        self._do_merge(ffmpeg, files, out, total)
```

- [ ] **Step 3: Smoke-boot the app**

Run: `python tool/a4071_tool.py`
Expected: clicks compile-wise still work; merging will fail later because `_do_merge` doesn't yet accept the new signature (we fix in Task 9). Skip actually running a merge for now.

- [ ] **Step 4: Commit**

```bash
git add tool/tools/mp3_merger.py
git commit -m "MP3 Merger: pre-scan total duration before merging"
```

---

## Task 9: MP3 Merger — parse ffmpeg `time=` and wire `_on_merge_done`

**Files:**
- Modify: `tool/tools/mp3_merger.py` — `_do_merge` signature + body, add `_on_merge_done`, add helper

- [ ] **Step 1: Add `re` import (if missing) and time-format helpers**

Near the top of `tool/tools/mp3_merger.py`, `re` is already imported (line 4). Below the existing module-level helpers (e.g. after `escape_concat_path`), add:

```python
_FFMPEG_TIME_RE = re.compile(r"time=(\d+):(\d+):(\d+(?:\.\d+)?)")


def _format_hms(seconds: float) -> str:
    if seconds < 0:
        seconds = 0
    total = int(round(seconds))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"
```

- [ ] **Step 2: Replace `_do_merge` with the new version**

Replace the entire `_do_merge` method (lines 280-335) with:

```python
    def _do_merge(
        self,
        ffmpeg: str,
        files: list[Path],
        out: Path,
        total_sec: float | None,
    ) -> None:
        list_path: Optional[str] = None
        success = False
        error: str | None = None
        cancelled = False
        try:
            with tempfile.NamedTemporaryFile(
                "w", delete=False, suffix=".txt", encoding="utf-8"
            ) as tf:
                list_path = tf.name
                for f in files:
                    safe = to_safe_concat_path(str(f))
                    tf.write(f"file '{escape_concat_path(safe)}'\n")

            cmd = [
                ffmpeg, "-hide_banner", "-y",
                "-f", "concat", "-safe", "0",
                "-i", list_path,
                "-c", "copy",
                str(out),
            ]
            self._log("Lệnh: " + subprocess.list2cmdline(cmd) + "\n")

            self._proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=CREATE_NO_WINDOW,
            )
            assert self._proc.stdout is not None
            for line in self._proc.stdout:
                self._log(line)
                if self._cancel_flag:
                    try:
                        self._proc.terminate()
                    except OSError:
                        pass
                    break
                if total_sec is not None:
                    m = _FFMPEG_TIME_RE.search(line)
                    if m:
                        processed = (
                            int(m.group(1)) * 3600
                            + int(m.group(2)) * 60
                            + float(m.group(3))
                        )
                        pct = min(99.5, processed / total_sec * 100.0)
                        self.progress.set_progress(
                            pct,
                            f"Đang gộp: {_format_hms(processed)} / {_format_hms(total_sec)}",
                        )
            rc = self._proc.wait()

            if self._cancel_flag:
                cancelled = True
            elif rc == 0:
                success = True
                self._log(f"\nHoàn tất. File: {out}\n")
            else:
                error = f"ffmpeg kết thúc với mã {rc}"
                self._log(f"\n{error}\n")
        except Exception as exc:
            error = str(exc)
            self._log(f"\nLỗi: {exc}\n")
        finally:
            if list_path:
                try:
                    os.unlink(list_path)
                except OSError:
                    pass
            self._proc = None
            self._on_merge_done(
                cancelled=cancelled, success=success, error=error, out=out
            )
```

- [ ] **Step 3: Add `_on_merge_done`**

Add this method just after `_do_merge`:

```python
    def _on_merge_done(
        self,
        *,
        cancelled: bool,
        success: bool,
        error: str | None,
        out: Path,
    ) -> None:
        if success:
            self.progress.finish(f"Hoàn tất: {out}")
            self.frame.after(0, lambda: messagebox.showinfo(
                "Hoàn tất", f"Đã gộp thành công:\n{out}"))
        elif cancelled:
            self.progress.reset()
            self.frame.after(0, lambda: self.progress.set_indeterminate("Đã hủy"))
        elif error:
            self.progress.set_indeterminate(f"Thất bại: {error}")
            self.frame.after(0, lambda e=error: messagebox.showerror("Thất bại", e))
        self._busy = False
        self._cancel_flag = False
        self.frame.after(0, lambda: self.start_btn.configure(state="normal"))
        self.frame.after(0, lambda: self.cancel_btn.configure(state="disabled"))
        self.app.end_busy(self.name)
```

- [ ] **Step 4: Manual smoke test — actually run a merge**

Prep: 2–3 short MP3 files in a folder. Run `python tool/a4071_tool.py`, log in, pick the folder, click "Quét", click "Bắt đầu gộp". Expected:
- Bar progresses from 0% → ~100% with ETA shown.
- Info reads `"Đang gộp: 00:00:XX / 00:00:YY"`.
- Final messagebox shows success.

- [ ] **Step 5: Commit**

```bash
git add tool/tools/mp3_merger.py
git commit -m "MP3 Merger: stream ffmpeg progress into ProgressPanel"
```

---

## Task 10: MP3 Merger — `set_busy_lock` + `request_cancel`

**Files:**
- Modify: `tool/tools/mp3_merger.py` — append two methods on the class

- [ ] **Step 1: Add `set_busy_lock` and `request_cancel`**

Add at the bottom of the `MP3MergerPage` class:

```python
    def set_busy_lock(self, locked: bool) -> None:
        state = "disabled" if locked else "normal"
        for w in (
            self.src_entry, self.out_entry,
            self.src_pick_btn, self.out_pick_btn,
            self.scan_btn, self.start_btn, self.clear_log_btn,
        ):
            try:
                w.configure(state=state)
            except tk.TclError:
                pass
        # cancel_btn is governed by _start/_on_merge_done, never touched here.

    def request_cancel(self) -> None:
        if self._busy:
            self._cancel()
```

- [ ] **Step 2: Manual smoke test**

Run: `python tool/a4071_tool.py`
1. Start a merge. While running, click the MP3 to SRT sidebar item → expect no navigation. Try clicking "Chọn..." / "Quét" → expect grayed and unresponsive. Click "Đăng xuất" → expect no action.
2. Click "Hủy" → merge stops, controls re-enable.

- [ ] **Step 3: Commit**

```bash
git add tool/tools/mp3_merger.py
git commit -m "MP3 Merger: wire set_busy_lock and request_cancel"
```

---

## Task 11: `transcribe_mp3` extended callback signature

**Files:**
- Modify: `tool/tools/mp3_to_srt.py` — `transcribe_mp3` signature + body

- [ ] **Step 1: Update `transcribe_mp3` signature**

Find `transcribe_mp3` (around line 214). Replace its signature with:

```python
def transcribe_mp3(
    mp3_path: Path,
    model_dir: Path | None,
    device: str,
    on_log: Callable[[str], None],
    on_progress: Callable[[float, str, float, float], None],
    is_cancelled: Callable[[], bool],
    on_phase: Callable[[str], None] | None = None,
) -> list[Word]:
```

- [ ] **Step 2: Call `on_phase("transcribe")` after model load**

After the existing `on_log(f"Đã nạp model trên {device_label}.\n")` line, add:

```python
    if on_phase is not None:
        on_phase("transcribe")
```

- [ ] **Step 3: Update the progress call inside the loop**

Find these lines (around line 252-253):

```python
        pct = min(99.0, (segment.end / duration) * 100.0)
        on_progress(pct, f"Đang phiên âm... {pct:.0f}%")
```

Replace with:

```python
        pct = min(99.0, (segment.end / duration) * 100.0)
        on_progress(
            pct,
            f"Đang phiên âm: {_format_timestamp(segment.end)} / {_format_timestamp(duration)}",
            float(segment.end),
            duration,
        )
```

- [ ] **Step 4: Run existing tests (they don't touch this function but verify import works)**

Run: `python -m pytest tool/tests/test_mp3_to_srt.py -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add tool/tools/mp3_to_srt.py
git commit -m "Extend transcribe_mp3 callback with time positions and phase hook"
```

---

## Task 12: MP3 to SRT — UI refactor + ProgressPanel + busy lock + phase wiring

**Files:**
- Modify: `tool/tools/mp3_to_srt.py` — imports, `build_ui`, `_launch_transcription`, `_do_run`, `set_busy_lock`, `request_cancel`

- [ ] **Step 1: Add ProgressPanel import**

At the top of `tool/tools/mp3_to_srt.py`, near the `from .base import ToolPage` line, add:

```python
from .progress import ProgressPanel
```

- [ ] **Step 2: Rewrite `build_ui` to promote widgets and embed ProgressPanel**

Replace the entire `build_ui` method of `MP3ToSrtPage` (lines 491-572) with:

```python
    def build_ui(self, parent: tk.Misc) -> None:
        outer = tk.Frame(parent, bg=PAGE_BG)
        outer.pack(fill="both", expand=True, padx=20, pady=16)

        card = tk.LabelFrame(
            outer, text=" Đầu vào ", bg=CARD_BG, fg=LABEL_FG,
            font=("Segoe UI Semibold", 10), padx=12, pady=10,
            bd=1, relief="solid",
        )
        card.pack(fill="x")

        tk.Label(card, text="File MP3:", bg=CARD_BG, fg=LABEL_FG).grid(
            row=0, column=0, sticky="w", pady=4)
        self.src_var = tk.StringVar()
        self.src_entry = tk.Entry(card, textvariable=self.src_var)
        self.src_entry.grid(row=0, column=1, sticky="ew", padx=8, pady=4)
        self.src_pick_btn = ttk.Button(card, text="Chọn...", command=self._pick_src)
        self.src_pick_btn.grid(row=0, column=2, pady=4)

        tk.Label(card, text="File xuất ra:", bg=CARD_BG, fg=LABEL_FG).grid(
            row=1, column=0, sticky="w", pady=4)
        self.out_var = tk.StringVar()
        self.out_entry = tk.Entry(card, textvariable=self.out_var)
        self.out_entry.grid(row=1, column=1, sticky="ew", padx=8, pady=4)
        self.out_pick_btn = ttk.Button(card, text="Lưu thành...", command=self._pick_out)
        self.out_pick_btn.grid(row=1, column=2, pady=4)

        tk.Label(card, text="Thiết bị:", bg=CARD_BG, fg=LABEL_FG).grid(
            row=2, column=0, sticky="w", pady=4)
        self.device_select_var = tk.StringVar(value=DEVICE_LABELS[DEVICE_CPU])
        self.device_combo = ttk.Combobox(
            card,
            textvariable=self.device_select_var,
            values=[DEVICE_LABELS[DEVICE_CPU], DEVICE_LABELS[DEVICE_GPU]],
            state="readonly",
            width=18,
        )
        self.device_combo.grid(row=2, column=1, sticky="w", padx=8, pady=4)

        tk.Label(card, text="Model:", bg=CARD_BG, fg=LABEL_FG).grid(
            row=3, column=0, sticky="w", pady=4)
        self.model_size_var = tk.StringVar(value=DEFAULT_MODEL_SIZE)
        self.model_combo = ttk.Combobox(
            card,
            textvariable=self.model_size_var,
            values=[f"{name} ({MODEL_SIZE_HINTS[name]})" for name in MODEL_SIZES],
            state="readonly",
            width=24,
        )
        self.model_combo.grid(row=3, column=1, sticky="w", padx=8, pady=4)
        self.model_size_var.set(f"{DEFAULT_MODEL_SIZE} ({MODEL_SIZE_HINTS[DEFAULT_MODEL_SIZE]})")

        card.columnconfigure(1, weight=1)

        bar = tk.Frame(outer, bg=PAGE_BG)
        bar.pack(fill="x", pady=(10, 8))
        self.start_btn = ttk.Button(bar, text="Bắt đầu", command=self._start)
        self.start_btn.pack(side="left")
        self.cancel_btn = ttk.Button(bar, text="Hủy", command=self._cancel, state="disabled")
        self.cancel_btn.pack(side="left", padx=8)
        self.clear_log_btn = ttk.Button(bar, text="Xóa log", command=self._clear_log)
        self.clear_log_btn.pack(side="left")

        log_card = tk.LabelFrame(
            outer, text=" Nhật ký ", bg=CARD_BG, fg=LABEL_FG,
            font=("Segoe UI Semibold", 10), padx=6, pady=6,
            bd=1, relief="solid",
        )
        log_card.pack(fill="both", expand=True)
        self.log_box = tk.Text(
            log_card, height=10, wrap="word", bd=0,
            highlightthickness=0, state="disabled",
        )
        log_vsb = ttk.Scrollbar(log_card, orient="vertical", command=self.log_box.yview)
        self.log_box.configure(yscrollcommand=log_vsb.set)
        self.log_box.grid(row=0, column=0, sticky="nsew")
        log_vsb.grid(row=0, column=1, sticky="ns")
        log_card.rowconfigure(0, weight=1)
        log_card.columnconfigure(0, weight=1)

        self.progress = ProgressPanel(outer)
        self.progress.pack(fill="x", pady=(8, 0))
```

(Note: bottom `status_var` label is removed.)

- [ ] **Step 3: Remove `_set_status`, replace all callers with progress panel updates**

Delete the `_set_status` method.

Update `_cancel` (around line 620-623):

```python
    def _cancel(self) -> None:
        if self._busy:
            self._cancel_flag = True
            self.progress.set_indeterminate("Đang hủy...")
```

In `_start`, there are three calls to `self._set_status(...)`:

1. Inside `on_download_finish` (failure branch): `self._set_status("Sẵn sàng")` → replace with `self.progress.reset()`.
2. Inside `on_download_finish` (retry-failure branch): `self._set_status("Sẵn sàng")` → replace with `self.progress.reset()`.
3. After the `on_download_finish` definition: `self._set_status("Đang tải model...")` → replace with `self.progress.set_indeterminate("Đang tải model...")`.

In `_launch_transcription` (around line 686-696), replace with:

```python
    def _launch_transcription(self, src_path: Path, out_path: Path, model_dir: Path, device: str) -> None:
        self._busy = True
        self._cancel_flag = False
        self.app.begin_busy(self.name)
        self.start_btn.configure(state="disabled")
        self.cancel_btn.configure(state="normal")
        self.progress.set_indeterminate("Đang nạp model...")
        threading.Thread(
            target=self._do_run,
            args=(src_path, out_path, model_dir, device),
            daemon=True,
        ).start()
```

In `_do_run` (around line 698-732), replace with:

```python
    def _do_run(self, mp3_path: Path, out_path: Path, model_dir: Path, device: str) -> None:
        def on_progress(pct: float, msg: str, cur: float, total: float) -> None:
            self.progress.set_progress(pct, msg)

        def on_phase(phase: str) -> None:
            if phase == "transcribe":
                self.progress.start("Đang phiên âm...")

        try:
            words = transcribe_mp3(
                mp3_path=mp3_path,
                model_dir=model_dir,
                device=device,
                on_log=self._log,
                on_progress=on_progress,
                is_cancelled=lambda: self._cancel_flag,
                on_phase=on_phase,
            )
            if self._cancel_flag:
                raise CancelledError()
            self.progress.set_indeterminate("Đang ghi SRT...")
            cues = pack_cues(words)
            write_srt(cues, out_path)
            self._log(f"\nĐã ghi {len(cues)} cue → {out_path}\n")
            self.progress.finish(f"Hoàn tất: {out_path.name}")
            self.frame.after(0, lambda: messagebox.showinfo(
                "Hoàn tất", f"Đã tạo phụ đề:\n{out_path}"))
        except CancelledError:
            self._log("\nĐã hủy.\n")
            self.progress.set_indeterminate("Đã hủy")
        except ModelMissingError as exc:
            self._log(f"\n{exc}\n")
            self.progress.set_indeterminate("Thiếu model")
            self.frame.after(0, lambda e=exc: messagebox.showerror("Thiếu model", str(e)))
        except Exception as exc:
            self._log(f"\nLỗi: {exc}\n")
            self.progress.set_indeterminate(f"Lỗi: {exc}")
            self.frame.after(0, lambda e=exc: messagebox.showerror("Lỗi", str(e)))
        finally:
            self._busy = False
            self._cancel_flag = False
            self.frame.after(0, lambda: self.start_btn.configure(state="normal"))
            self.frame.after(0, lambda: self.cancel_btn.configure(state="disabled"))
            self.app.end_busy(self.name)
```

- [ ] **Step 4: Add `set_busy_lock` and `request_cancel`**

Append at the bottom of `MP3ToSrtPage`:

```python
    def set_busy_lock(self, locked: bool) -> None:
        state = "disabled" if locked else "normal"
        for w in (
            self.src_entry, self.out_entry,
            self.src_pick_btn, self.out_pick_btn,
            self.start_btn, self.clear_log_btn,
        ):
            try:
                w.configure(state=state)
            except tk.TclError:
                pass
        combo_state = "disabled" if locked else "readonly"
        for c in (self.device_combo, self.model_combo):
            try:
                c.configure(state=combo_state)
            except tk.TclError:
                pass

    def request_cancel(self) -> None:
        if self._busy:
            self._cancel()
```

- [ ] **Step 5: Manual smoke test**

Prep: a short MP3 (10–30s) you have a model for. Run `python tool/a4071_tool.py`. On MP3 to SRT:
1. Pick MP3, set output, click "Bắt đầu".
2. Verify: bar spins (indeterminate) for "Đang nạp model..." then switches to determinate with `%` + ETA + `"Đang phiên âm: 00:00:XX / 00:00:YY"`.
3. While running: sidebar disabled, logout muted, input fields disabled, only Cancel clickable.
4. Click Cancel → bar shows "Đang hủy..." → finishes with "Đã hủy".
5. Re-run without canceling → completes, messagebox appears.

- [ ] **Step 6: Commit**

```bash
git add tool/tools/mp3_to_srt.py
git commit -m "MP3 to SRT: wire ProgressPanel, phase reporting, and busy lock"
```

---

## Task 13: Full regression + final commit

- [ ] **Step 1: Run the entire test suite**

Run: `python -m pytest tool/tests -v`
Expected: all tests pass (or smoke test skipped on headless).

- [ ] **Step 2: Manual end-to-end script test**

Run `python tool/a4071_tool.py`. Walk through both tools:
- MP3 Merger: scan → start → observe progress → cancel mid-run → start again → completes.
- MP3 to SRT: start → observe nạp model → phiên âm → ghi SRT → completes. Cancel mid-run, retry.

For each: while running, confirm sidebar/logout/all-other-controls are unclickable.

- [ ] **Step 3: Verify clean working tree**

Run: `git status`
Expected: clean. (Any leftover `tool/A4071-Tool.rar` / `tool/A4071-Tool/` build artifacts pre-existed and are out of scope.)

- [ ] **Step 4: Done — no extra commit needed**

The work is split across the Task 1–12 commits, each green at its checkpoint.
