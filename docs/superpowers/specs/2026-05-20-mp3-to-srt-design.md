# MP3 → SRT Tool — Design

## Overview

Add a second tool page to A4071-Tool that converts a single `.mp3` audio
file into an `.srt` subtitle file suitable for video captions. The tool
runs entirely offline using `faster-whisper` with the `medium.en` model.

The tool follows the same UI pattern as the existing MP3 Merger page:
sidebar entry, input card, action bar, log panel, and status label.

## Goals

- One-click transcription of a single English `.mp3` to a `.srt` file.
- Caption-friendly cue packing: ~42 chars per line, 2 lines max, ≤7s per cue.
- Offline operation. Model is loaded from local disk only.
- Automatic GPU when CUDA is available, with a transparent fallback to CPU.
- Clear, actionable error messages when prerequisites are missing.

## Non-goals

- Batch transcription (multiple MP3 files). Single file in, single file out.
- Languages other than English. The model is fixed to `medium.en`.
- Translation. Output text language equals input audio language.
- Bundling cuBLAS / cuDNN DLLs. GPU users install CUDA themselves.
- Bundling the Whisper model in the build. The user supplies it on disk.
- Editing or previewing subtitles inside the app.

## User experience

### Layout (single page)

```
┌─ Đầu vào ─────────────────────────────────┐
│ File MP3:     [______________] [Chọn...]  │
│ File xuất ra: [______________] [Lưu...]   │
└───────────────────────────────────────────┘

[Bắt đầu]  [Hủy]  [Xóa log]              Thiết bị: auto

┌─ Nhật ký ─────────────────────────────────┐
│ ...                                       │
└───────────────────────────────────────────┘
Trạng thái: Sẵn sàng
```

### Default behaviour

- Picking an MP3 auto-fills the output path: same folder, same stem, `.srt`.
- The output path is editable; the user may pick any path via `Save As`.
- If the output file exists, the user is prompted to overwrite.
- `Hủy` is disabled until a run is in progress; `Bắt đầu` is disabled during a run.

### First-run model check

On `Bắt đầu`, before loading the model:

1. Look for `models/medium.en/` next to `A4071-Tool.exe` (or, in dev mode,
   next to the project root).
2. The folder must contain at least `model.bin` and `config.json` (the
   standard faster-whisper layout from the
   `Systran/faster-whisper-medium.en` HuggingFace repo).
3. If missing or incomplete, show a messagebox:

   > "Không tìm thấy model `medium.en`. Hãy tải tại
   > https://huggingface.co/Systran/faster-whisper-medium.en
   > và giải nén vào thư mục `models/medium.en/` cạnh A4071-Tool.exe."

   No download is triggered by the app.

### Logging

- The log panel mirrors MP3 Merger: scrollable, read-only, append-only.
- Per-segment lines are logged in `[hh:mm:ss → hh:mm:ss] text` form.
- The status label shows the current phase: `Đang nạp model...`,
  `Đang phiên âm... 37%`, `Đang ghi SRT`, `Hoàn tất`, `Đã hủy`, `Lỗi: ...`.
- Progress percent is `last_segment_end / audio_duration`. `audio_duration`
  comes from the `TranscriptionInfo` object returned by
  `model.transcribe()` alongside the segment iterator.

## Architecture

### File layout

```
tool/
├── a4071_tool.py            # register MP3ToSrtPage in tool_classes
├── tools/
│   ├── base.py              # unchanged
│   ├── mp3_merger.py        # unchanged
│   └── mp3_to_srt.py        # NEW: page + transcription pipeline
```

`mp3_to_srt.py` contains both the UI page and the pipeline functions.
The file is expected to stay small (~300 lines); splitting further would
add navigation cost without isolation benefit.

### Module surface (within `mp3_to_srt.py`)

```python
@dataclass(frozen=True)
class Word:
    start: float          # seconds
    end: float            # seconds
    text: str             # raw token incl. leading space

@dataclass(frozen=True)
class Cue:
    index: int            # 1-based
    start: float
    end: float
    lines: list[str]      # 1 or 2 lines

def find_model_dir() -> Path | None:
    """Look for models/medium.en next to the EXE (or project root in dev)."""

def transcribe(
    mp3_path: Path,
    model_dir: Path,
    on_segment: Callable[[float, float, str, float | None], None],
    cancel: Callable[[], bool],
) -> list[Word]:
    """Run faster-whisper with word_timestamps=True. Calls on_segment
    after each segment with (start, end, text, progress_or_None).
    Returns the flat list of words for the entire file. Raises
    CancelledError if cancel() returns True between segments."""

def pack_cues(
    words: list[Word],
    max_chars_per_line: int = 42,
    max_lines: int = 2,
    max_duration: float = 7.0,
) -> list[Cue]:
    """Greedy line-breaking on word timestamps."""

def write_srt(cues: list[Cue], srt_path: Path) -> None:
    """Write standard SRT (UTF-8 with BOM, CRLF line endings)."""
```

`MP3ToSrtPage(ToolPage)` owns the Tk widgets and orchestrates the
pipeline on a worker thread. The pipeline functions themselves are
pure and Tk-free, so they can be tested in isolation if needed.

### Data flow

```
User clicks Bắt đầu
   │
   ▼
Validate inputs (mp3 exists, out path set, model present)
   │
   ▼  (background thread)
WhisperModel(model_dir, device="auto", compute_type=<auto>)
   │
   ▼
model.transcribe(mp3, word_timestamps=True, language="en", vad_filter=True)
   │
   ▼  (streaming segments)
for segment in segments:
    append words → buffer
    log + update progress via Tk.after(0, ...)
    if cancel: raise CancelledError
   │
   ▼
pack_cues(words)  →  write_srt(cues, out)
   │
   ▼
messagebox.showinfo("Hoàn tất", str(out))
```

### Cue packing algorithm

1. Strip leading whitespace from `Word.text`.
2. Walk words greedily, appending to the current line until any of:
   - Adding the word would exceed `max_chars_per_line`.
   - A sentence-ending punctuation (`.`, `?`, `!`) closes the current word.
   - The accumulated duration `word.end - cue_start` would exceed `max_duration`.
3. On a line break:
   - If the cue already has `max_lines` lines, finalize the cue and start a new one.
   - Otherwise start a new line within the same cue.
4. Cue timestamps: `cue.start = first_word.start`, `cue.end = last_word.end`.
5. Empty cues are skipped (defensive, should not occur with VAD).

This is a straightforward greedy algorithm. It is not optimal but is
predictable and matches what YouTube auto-captions produce in practice.

### Device selection

- First attempt: `device="auto"`, `compute_type="float16"`. This loads
  CUDA when its DLLs are present, else faster-whisper raises.
- On any exception from the first attempt: log `Không tìm thấy CUDA, dùng CPU`
  and retry with explicit `device="cpu"`, `compute_type="int8"`.
- The chosen device is captured in a local variable and surfaced in the
  status bar after the first successful load.
- The status bar shows `Thiết bị: GPU (CUDA)` or `Thiết bị: CPU` after the
  first successful load.

## Error handling

| Condition | Behavior |
|---|---|
| MP3 path empty / not a file | Inline error messagebox, no run starts |
| Output path empty | Inline error messagebox |
| Output equals input path | Inline error messagebox |
| Output exists | Yes/No overwrite prompt |
| Model folder missing | Messagebox with HuggingFace URL, no run starts |
| Model load fails (corrupt) | Log exception, status "Lỗi", messagebox |
| CUDA load fails | Auto-retry with CPU; logged, not user-facing error |
| MP3 cannot be decoded | Log exception, status "Lỗi", messagebox |
| User cancels | Thread observes flag at segment boundary, status "Đã hủy" |
| Disk write fails | Log exception, status "Lỗi", messagebox |

The worker thread always re-enables the buttons and resets `_busy` in a
`finally` block.

## Build implications

- New runtime dependency: `faster-whisper` (pulls `ctranslate2`, `tokenizers`,
  `huggingface-hub`, `onnxruntime` via VAD).
- `A4071-Tool.spec` needs hidden imports for `ctranslate2` and the VAD
  ONNX assets shipped with faster-whisper. The exact additions are an
  implementation detail; the spec doc here only flags that the `.spec`
  file will change.
- Base EXE size is expected to grow by roughly 150 MB (ctranslate2 +
  onnxruntime). The user-supplied model adds ~1.5 GB on disk but does
  not enter the EXE.
- `ffmpeg.exe` is not required by faster-whisper itself; it stays in
  the project for the MP3 Merger tool.

## Testing strategy

Manual smoke tests on Windows after build:

1. Launch app, log in, click `MP3 to SRT` in the sidebar.
2. With model folder missing → expect messagebox with HF URL.
3. With model present, short MP3 (<30s, English speech) → expect `.srt`
   produced next to the MP3, openable in VLC as captions.
4. Mid-run cancel → expect status `Đã hủy`, no partial SRT written.
5. Re-run with output file present → overwrite prompt appears.
6. On a machine without CUDA → expect CPU fallback log line, run completes.
7. Output SRT visual check: lines ≤42 chars, cues ≤7s, timestamps monotonic.

Automated tests are out of scope for this iteration; the pure functions
(`pack_cues`, `write_srt`) are written so they could be unit-tested
later without Tk.

## Open questions

None at design approval time. Implementation may surface follow-ups
(e.g., exact PyInstaller hidden imports) which will be resolved in the
plan or during execution.
