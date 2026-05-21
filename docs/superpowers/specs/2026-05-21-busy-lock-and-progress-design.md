# Busy Lock & Progress Bar Redesign

**Date:** 2026-05-21
**Scope:** A4071-Tool desktop app (`tool/`)
**Goal:** While a tool is running, prevent the user from triggering any other action that could break the running script; and replace the current text-only status with a real progress bar that shows percent + ETA + current state.

---

## Motivation

Today, both tools (`MP3 Merger`, `MP3 to SRT`) only disable their own "Start" button while a job runs. Everything else stays clickable:

- Sidebar navigation can switch pages mid-run.
- "Chọn…" / "Quét" / "Xóa log" / "Đăng xuất" all fire.
- Input fields can be edited mid-run, changing state the worker thread is still using.

This is fragile and has caused unexpected errors.

Separately, progress feedback is weak:

- `MP3 Merger` only shows a one-line status (`"Đang gộp…"`).
- `MP3 to SRT` updates a status string with a percent number but no visual bar and no ETA.

Users have asked: how is it going, and how long until it finishes?

---

## Non-goals

- No job queue / multiple concurrent jobs. One tool at a time, as today.
- No persistent history of past runs.
- No fundamental rework of the worker-thread model.
- No changes to login, update, or auth screens.

---

## Architecture

Two reusable building blocks, plus per-tool integration:

1. **Busy lock** owned by `A4071App`, propagated to every `ToolPage` and to sidebar / logout controls.
2. **`ProgressPanel` widget** in `tool/tools/progress.py`, embedded in each tool page in place of the current status label.

```
A4071App
 ├── _busy_owner: str | None
 ├── begin_busy(owner) / end_busy(owner)
 └── _apply_busy_lock(locked)
       ├── sidebar items.set_enabled(not locked)
       ├── logout link disabled/enabled
       └── for each page: page.set_busy_lock(locked)

ToolPage (abstract)
 └── set_busy_lock(locked)   # default no-op, tools override

ProgressPanel (tk.Frame)
 ├── start(status)
 ├── set_indeterminate(status)
 ├── set_progress(pct, status)
 ├── finish(status)
 └── reset()
```

---

## Component 1: Busy lock

### State

`A4071App._busy_owner: str | None` — name of the running tool, or `None` if idle.

### App API

```python
def begin_busy(self, owner: str) -> None:
    if self._busy_owner is not None:
        return  # first-wins; ignore re-entrant calls
    self._busy_owner = owner
    self._apply_busy_lock(True)

def end_busy(self, owner: str) -> None:
    if self._busy_owner != owner:
        return  # ignore mismatched release
    self._busy_owner = None
    self._apply_busy_lock(False)

def _apply_busy_lock(self, locked: bool) -> None:
    for item in self._sidebar_items.values():
        item.set_enabled(not locked)
    self._logout_btn_set_enabled(not locked)
    for page in self._pages.values():
        page.set_busy_lock(locked)
```

### `ToolPage` contract

Add a default no-op method:

```python
def set_busy_lock(self, locked: bool) -> None:
    """Override to disable/enable input controls.
    The cancel button (if any) should remain enabled."""
```

### Sidebar item changes

`SidebarItem` gains:

- `_enabled: bool` (default `True`)
- `set_enabled(enabled: bool)`:
  - When disabled: `cursor="arrow"`, text color = `SIDEBAR_MUTED_FG`, click/hover handlers no-op.
  - When enabled: restore previous appearance, including correct active/inactive bg.

### Logout link changes

Promote the local `logout_btn` to an instance attribute, gain a `_logout_enabled: bool` flag. When disabled: text color = muted, cursor arrow, click handler ignores the event.

### Window close

`_on_close` should attempt to gracefully cancel the running tool before destroying. Concretely: if `_busy_owner` is set, ask the current page to cancel (new method `request_cancel(self) -> None` on `ToolPage`, default no-op). Then proceed to destroy. The workers are daemon threads and will die with the process; this just gives ffmpeg a chance to terminate cleanly.

---

## Component 2: `ProgressPanel` widget

New file: `tool/tools/progress.py`.

### Visual

```
[████████████░░░░░░░░░░░░]            ← ttk.Progressbar, length stretches with parent
45% • còn ~2 phút • Đang phiên âm: 00:01:23 / 00:05:00
```

Two rows: bar on top, single-line info label below. Info label is composed of up to three segments joined by `" • "`:

- Percent (only if determinate mode and pct > 0).
- ETA (only if determinate mode and pct > 1 — below 1% ETA is too noisy).
- Status text (always shown).

### Public API

All methods are safe to call from worker threads; they marshal to the UI thread via `after(0, ...)`.

```python
class ProgressPanel(tk.Frame):
    def __init__(self, parent: tk.Misc, *, bg: str = "#f8fafc") -> None: ...

    def start(self, status: str = "Bắt đầu…") -> None:
        """Reset to 0%, switch to determinate mode, start ETA timer."""

    def set_indeterminate(self, status: str) -> None:
        """Switch bar to indeterminate (spinning); hide %/ETA; show status only."""

    def set_progress(self, pct: float, status: str) -> None:
        """Update bar to pct (0–100), recompute and show ETA, set status text."""

    def finish(self, status: str = "Hoàn tất") -> None:
        """Snap to 100%, stop ETA, show final status."""

    def reset(self) -> None:
        """Bar to 0, clear info label, stop indeterminate animation."""
```

### ETA computation

Pure function, easy to test:

```python
def format_eta(elapsed_sec: float, pct: float) -> str | None:
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

### Internals

- `_start_time: float | None` — set by `start()`, cleared by `reset()` / `finish()`.
- `_mode: Literal["idle", "determinate", "indeterminate"]`.
- Switching mode calls `progressbar.configure(mode=...)` and starts/stops the indeterminate animation accordingly.

---

## Component 3: MP3 Merger integration

### New helpers

```python
def find_ffprobe() -> Optional[str]:
    """Mirror of find_ffmpeg — look for ffprobe.exe next to the bundled
    ffmpeg.exe, then PATH."""

def mp3_duration(path: Path, ffprobe: Optional[str]) -> Optional[float]:
    """Return duration in seconds. Strategy:
      1. If ffprobe is available, run it.
      2. Else parse the first MP3 frame header for bitrate, estimate
         duration = file_size_bytes / (bitrate_bps / 8).
      3. Else return None.
    Errors at any step → None."""
```

The MP3 header fallback is a tiny CBR estimator written inline (no new dependency); accurate for TTS-generated MP3s which are virtually always CBR.

### UI changes

- Remove the bottom `status_var` label; replace with `ProgressPanel`.
- Add a "Hủy" button next to "Bắt đầu gộp" (disabled by default, enabled while running).

### Start flow

```python
def _start(self):
    # ... existing validation ...
    self.app.begin_busy(self.name)
    self.progress.set_indeterminate("Đang tính tổng thời lượng…")
    threading.Thread(target=self._do_merge_with_prep,
                     args=(ffmpeg, list(self._files), out_path), daemon=True).start()

def _do_merge_with_prep(self, ffmpeg, files, out):
    ffprobe = find_ffprobe()
    total = 0.0
    ok = True
    for f in files:
        d = mp3_duration(f, ffprobe)
        if d is None:
            ok = False
            break
        total += d
    if ok and total > 0:
        self.progress.start("Đang gộp…")
        self._do_merge(ffmpeg, files, out, total_sec=total)
    else:
        self.progress.set_indeterminate("Đang gộp… (không xác định được tiến độ)")
        self._do_merge(ffmpeg, files, out, total_sec=None)
```

### ffmpeg progress parsing

Modify `_do_merge` to accept `total_sec: float | None`. Add a stderr parser:

```python
TIME_RE = re.compile(r"time=(\d+):(\d+):(\d+(?:\.\d+)?)")

for line in proc.stdout:
    self._log(line)
    if self._cancel_flag:
        proc.terminate()
        break
    if total_sec is not None:
        m = TIME_RE.search(line)
        if m:
            h, mn, s = int(m.group(1)), int(m.group(2)), float(m.group(3))
            processed = h * 3600 + mn * 60 + s
            pct = min(99.5, processed / total_sec * 100.0)
            self.progress.set_progress(
                pct,
                f"Đang gộp: {fmt_hms(processed)} / {fmt_hms(total_sec)}",
            )
```

On success: `progress.finish("Hoàn tất")`. In `finally`: `app.end_busy(self.name)`, re-enable cancel button to disabled.

### Cancel

`_cancel()` sets `_cancel_flag = True`. Worker observes it, calls `proc.terminate()`, jumps to `finally`.

### `set_busy_lock(locked)`

```python
def set_busy_lock(self, locked: bool) -> None:
    state = "disabled" if locked else "normal"
    for w in (self.src_entry, self.out_entry, self.src_pick_btn,
              self.out_pick_btn, self.scan_btn, self.start_btn,
              self.clear_log_btn):
        w.configure(state=state)
    # cancel_btn is controlled separately by _start / finally
```

This requires promoting all of those widgets to `self.<name>` attributes (currently several are created with `ttk.Button(...).grid(...)` chained, losing the reference).

### `request_cancel`

```python
def request_cancel(self) -> None:
    if self._busy:
        self._cancel_flag = True
```

---

## Component 4: MP3 to SRT integration

### UI changes

- Remove the bottom `status_var` label; replace with `ProgressPanel`.

### `transcribe_mp3` signature change

Extend the progress callback so we can show real time positions:

```python
on_progress: Callable[[float, str, float, float], None]
# (pct, status, current_sec, total_sec)
```

Inside the loop:

```python
pct = min(99.0, (segment.end / duration) * 100.0)
on_progress(
    pct,
    f"Đang phiên âm: {fmt_hms(segment.end)} / {fmt_hms(duration)}",
    float(segment.end),
    duration,
)
```

Update `test_mp3_to_srt.py` if it stubs the callback.

### Phase flow inside `_do_run`

```python
self.progress.set_indeterminate("Đang nạp model…")
# ... load model happens inside transcribe_mp3 ...
self.progress.start("Đang phiên âm…")
words = transcribe_mp3(..., on_progress=self._on_transcribe_progress, ...)
self.progress.set_indeterminate("Đang ghi SRT…")
cues = pack_cues(words); write_srt(cues, out_path)
self.progress.finish(f"Hoàn tất: {out_path.name}")
```

The "Đang nạp model" → "Đang phiên âm" switch needs to happen from inside `transcribe_mp3` after model loads. Simplest: pass an `on_phase: Callable[[str], None]` callback into `transcribe_mp3`, call it after model load with `"transcribe"`. The page maps that to `progress.start(...)`.

### `set_busy_lock(locked)`

Same pattern as MP3 Merger; covers `src_entry`, `out_entry`, both pick buttons, device combobox, model combobox, start_btn, clear log button. `cancel_btn` stays under the existing busy/idle logic.

### `_do_run` `finally`

Add `app.end_busy(self.name)` alongside the existing button-state restore.

### `request_cancel`

Reuses existing `_cancel_flag` + `_busy` mechanism.

---

## Component 5: Edge cases & error handling

- **Pre-scan duration partial failure (MP3 Merger):** any file returning `None` → drop to indeterminate mode for the merge, keep the merge running.
- **ffmpeg cancel mid-run:** `proc.terminate()` causes nonzero exit; the existing nonzero-rc path logs it but with the cancel flag we suppress the error messagebox and log "Đã hủy" instead.
- **App close while busy:** `_on_close` calls `request_cancel` on the active page, then destroys. The page's `_do_*` `finally` block races with destruction but that's OK — the process is exiting.
- **`begin_busy` re-entry:** first-wins guard prevents two tools from acquiring the lock; should not happen in practice since locks are released in `finally`, but defensive.
- **`end_busy` from the wrong owner:** ignored, prevents an early `end_busy` from another page accidentally unlocking a still-busy tool.

---

## Testing strategy

Pure-function tests only — tkinter UI is not unit-tested.

New tests:

- `tests/test_progress.py`
  - `format_eta` for buckets: < 60s, 60–300s, 300–3600s, ≥ 3600s
  - `format_eta` returns `None` for pct ≤ 1 or elapsed ≤ 0
- `tests/test_mp3_duration.py`
  - Synthetic minimal MP3 frame header (single CBR frame, known bitrate) → expected duration
  - Invalid bytes → `None`
  - Path that does not exist → `None`

Update:

- `tests/test_mp3_to_srt.py` — if it directly invokes `transcribe_mp3`, adjust the `on_progress` stub to the new 4-arg signature.

Manual smoke test (Vietnamese, run by hand after implementation):

1. Bắt đầu merge một thư mục lớn → kiểm tra sidebar + nút "Chọn…" + "Đăng xuất" đều bị disable; bar chạy, ETA giảm dần; nút Hủy hoạt động.
2. Bắt đầu phiên âm 1 file MP3 dài → kiểm tra phase "nạp model" (indeterminate) → "phiên âm" (determinate) → "ghi SRT" (indeterminate) → "hoàn tất".
3. Đóng app khi đang chạy → kiểm tra không có process ffmpeg sót lại.

---

## File touch list

New:

- `tool/tools/progress.py`
- `tool/tests/test_progress.py`
- `tool/tests/test_mp3_duration.py`

Modified:

- `tool/a4071_tool.py` — busy lock, sidebar/logout enable/disable, `_on_close` cancel hook.
- `tool/tools/base.py` — `set_busy_lock` and `request_cancel` default methods.
- `tool/tools/mp3_merger.py` — `ProgressPanel`, ffprobe + MP3 duration, ffmpeg time= parsing, cancel button, `set_busy_lock`.
- `tool/tools/mp3_to_srt.py` — `ProgressPanel`, callback signature, phase reporting, `set_busy_lock`.
- `tool/tests/test_mp3_to_srt.py` — callback signature update (only if existing tests touch it).
