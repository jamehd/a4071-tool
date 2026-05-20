from __future__ import annotations

import os
import sys
import threading
import tkinter as tk
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Callable

from .base import ToolPage


SENTENCE_END = {".", "?", "!"}

CARD_BG = "#ffffff"
PAGE_BG = "#f8fafc"
LABEL_FG = "#374151"
MUTED_FG = "#6b7280"


@dataclass(frozen=True)
class Word:
    start: float
    end: float
    text: str


@dataclass(frozen=True)
class Cue:
    index: int
    start: float
    end: float
    lines: list[str]


def _strip_leading(text: str) -> str:
    return text.lstrip()


def _ends_sentence(text: str) -> bool:
    stripped = text.rstrip()
    return bool(stripped) and stripped[-1] in SENTENCE_END


def pack_cues(
    words: list[Word],
    max_chars_per_line: int = 42,
    max_lines: int = 2,
    max_duration: float = 7.0,
) -> list[Cue]:
    if not words:
        return []

    cues: list[Cue] = []
    cue_lines: list[str] = [""]
    cue_start: float = words[0].start
    cue_end: float = words[0].start

    def finalize() -> None:
        nonlocal cue_lines, cue_start, cue_end
        cleaned = [line for line in cue_lines if line]
        if cleaned:
            cues.append(
                Cue(
                    index=len(cues) + 1,
                    start=cue_start,
                    end=cue_end,
                    lines=cleaned,
                )
            )
        cue_lines = [""]

    for word in words:
        token = _strip_leading(word.text)
        if not token:
            continue
        if not cue_lines[-1] and len(cue_lines) == 1 and not cues and cue_start == words[0].start and cue_end == words[0].start:
            cue_start = word.start

        current_line = cue_lines[-1]
        candidate = (current_line + " " + token).strip() if current_line else token

        too_long = len(candidate) > max_chars_per_line
        too_far = (word.end - cue_start) > max_duration

        if too_far and (current_line or len(cue_lines) > 1):
            finalize()
            cue_start = word.start
            cue_end = word.end
            cue_lines = [token]
            continue

        if too_long:
            if len(cue_lines) < max_lines:
                cue_lines.append(token)
                cue_end = word.end
            else:
                finalize()
                cue_start = word.start
                cue_end = word.end
                cue_lines = [token]
            if _ends_sentence(token):
                finalize()
                cue_start = word.end
                cue_end = word.end
            continue

        cue_lines[-1] = candidate
        cue_end = word.end

        if _ends_sentence(token):
            finalize()
            cue_start = word.end
            cue_end = word.end

    finalize()
    return cues


def _format_timestamp(seconds: float) -> str:
    if seconds < 0:
        seconds = 0.0
    total_ms = int(round(seconds * 1000))
    hours, rem = divmod(total_ms, 3600 * 1000)
    minutes, rem = divmod(rem, 60 * 1000)
    secs, ms = divmod(rem, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{ms:03d}"


def write_srt(cues: list[Cue], srt_path: Path) -> None:
    parts: list[str] = []
    for cue in cues:
        parts.append(str(cue.index))
        parts.append(f"{_format_timestamp(cue.start)} --> {_format_timestamp(cue.end)}")
        parts.extend(cue.lines)
        parts.append("")
    body = "\r\n".join(parts)
    if not body.endswith("\r\n"):
        body += "\r\n"
    srt_path.write_bytes(b"\xef\xbb\xbf" + body.encode("utf-8"))


def _exe_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


MODEL_SIZES = ["tiny.en", "base.en", "small.en", "medium.en"]
MODEL_SIZE_HINTS = {
    "tiny.en": "~75 MB",
    "base.en": "~140 MB",
    "small.en": "~470 MB",
    "medium.en": "~1.5 GB",
}
DEFAULT_MODEL_SIZE = "small.en"


def _model_repo_url(model_name: str) -> str:
    return f"https://huggingface.co/Systran/faster-whisper-{model_name}/resolve/main"


def find_model_dir(model_name: str = DEFAULT_MODEL_SIZE) -> Path | None:
    candidate = _exe_dir() / "models" / model_name
    if not candidate.is_dir():
        return None
    if not (candidate / "model.bin").is_file():
        return None
    if not (candidate / "config.json").is_file():
        return None
    return candidate


class CancelledError(Exception):
    pass


class ModelMissingError(Exception):
    pass


DEVICE_CPU = "cpu"
DEVICE_GPU = "cuda"
DEVICE_LABELS = {DEVICE_CPU: "CPU", DEVICE_GPU: "GPU (CUDA)"}


def _register_cuda_dll_dir() -> None:
    if not hasattr(os, "add_dll_directory"):
        return
    cuda_dir = _exe_dir() / "cuda"
    if cuda_dir.is_dir():
        try:
            os.add_dll_directory(str(cuda_dir))
        except OSError:
            pass


def _load_model(model_dir: Path, device: str):
    if device == DEVICE_GPU:
        _register_cuda_dll_dir()
    from faster_whisper import WhisperModel

    if device == DEVICE_GPU:
        return WhisperModel(str(model_dir), device="cuda", compute_type="float16"), DEVICE_LABELS[DEVICE_GPU]
    return WhisperModel(str(model_dir), device="cpu", compute_type="int8"), DEVICE_LABELS[DEVICE_CPU]


def transcribe_mp3(
    mp3_path: Path,
    model_dir: Path | None,
    device: str,
    on_log: Callable[[str], None],
    on_progress: Callable[[float, str], None],
    is_cancelled: Callable[[], bool],
) -> list[Word]:
    if model_dir is None:
        raise ModelMissingError("Không tìm thấy model. Hãy tải model trước.")

    on_log("Đang nạp model...\n")
    model, device_label = _load_model(model_dir, device)
    on_log(f"Đã nạp model trên {device_label}.\n")

    segments, info = model.transcribe(
        str(mp3_path),
        language="en",
        word_timestamps=True,
        vad_filter=True,
    )
    duration = max(float(info.duration or 0.0), 0.001)

    collected: list[Word] = []
    for segment in segments:
        if is_cancelled():
            raise CancelledError()

        start_ts = _format_timestamp(segment.start)
        end_ts = _format_timestamp(segment.end)
        on_log(f"[{start_ts} → {end_ts}] {segment.text.strip()}\n")

        if segment.words:
            for word in segment.words:
                collected.append(Word(start=float(word.start), end=float(word.end), text=word.word))
        else:
            collected.append(Word(start=float(segment.start), end=float(segment.end), text=segment.text))

        pct = min(99.0, (segment.end / duration) * 100.0)
        on_progress(pct, f"Đang phiên âm... {pct:.0f}%")

    return collected


MODEL_FILES = [
    "model.bin",
    "config.json",
    "tokenizer.json",
    "vocabulary.txt",
]


class DownloadError(Exception):
    pass


def _download_file(
    url: str,
    dest: Path,
    on_bytes: Callable[[int, int], None],
    is_cancelled: Callable[[], bool],
    chunk_size: int = 1024 * 256,
) -> None:
    """Stream-download url to dest. Raises CancelledError on cancel,
    DownloadError on network failure. Writes to dest.tmp then renames."""
    tmp = dest.with_suffix(dest.suffix + ".part")
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "A4071-Tool/0.1"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            total = int(resp.headers.get("Content-Length") or -1)
            downloaded = 0
            with tmp.open("wb") as f:
                while True:
                    if is_cancelled():
                        raise CancelledError()
                    chunk = resp.read(chunk_size)
                    if not chunk:
                        break
                    f.write(chunk)
                    downloaded += len(chunk)
                    on_bytes(downloaded, total)
        tmp.replace(dest)
    except CancelledError:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
        raise
    except (urllib.error.URLError, OSError) as exc:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
        raise DownloadError(f"Tải {url} thất bại: {exc}") from exc


def download_model(
    target_dir: Path,
    base_url: str,
    on_file_start: Callable[[str, int, int], None],
    on_file_bytes: Callable[[int, int], None],
    is_cancelled: Callable[[], bool],
    files: list[str] = MODEL_FILES,
) -> None:
    """Download all model files into target_dir. On cancel or error, cleanup
    any partial files (including completed files from this run, since a partial
    install is unusable). Raises CancelledError or DownloadError."""
    target_dir.mkdir(parents=True, exist_ok=True)
    completed: list[Path] = []
    try:
        for idx, filename in enumerate(files, 1):
            on_file_start(filename, idx, len(files))
            dest = target_dir / filename
            _download_file(
                url=f"{base_url}/{filename}",
                dest=dest,
                on_bytes=on_file_bytes,
                is_cancelled=is_cancelled,
            )
            completed.append(dest)
    except (CancelledError, DownloadError):
        for path in completed:
            try:
                path.unlink()
            except OSError:
                pass
        raise


def _fmt_size(n: int) -> str:
    units = ["B", "KB", "MB", "GB"]
    f = float(n)
    for u in units:
        if f < 1024.0 or u == units[-1]:
            return f"{f:.1f} {u}" if u != "B" else f"{int(f)} {u}"
        f /= 1024.0
    return f"{n} B"


class ModelDownloadDialog(tk.Toplevel):
    """Modal progress dialog for model download. Self-runs the download
    in a worker thread, calls on_finish(success: bool, error: str | None)
    when the dialog closes."""

    def __init__(
        self,
        parent: tk.Misc,
        target_dir: Path,
        model_name: str,
        base_url: str,
        size_hint: str,
        on_finish: Callable[[bool, str | None], None],
    ) -> None:
        super().__init__(parent)
        self.title("Tải model")
        self.resizable(False, False)
        self.configure(bg=PAGE_BG, padx=20, pady=16)
        self.transient(parent.winfo_toplevel())
        self.grab_set()
        self.protocol("WM_DELETE_WINDOW", self._cancel)

        self._target_dir = target_dir
        self._base_url = base_url
        self._on_finish = on_finish
        self._cancelled = False
        self._closed = False

        tk.Label(
            self,
            text=f"Đang tải model {model_name} ({size_hint})...",
            bg=PAGE_BG,
            fg=LABEL_FG,
            font=("Segoe UI Semibold", 10),
        ).pack(anchor="w")

        self.file_var = tk.StringVar(value="Chuẩn bị...")
        tk.Label(
            self,
            textvariable=self.file_var,
            bg=PAGE_BG,
            fg=MUTED_FG,
            font=("Segoe UI", 9),
        ).pack(anchor="w", pady=(8, 4))

        self.progress = ttk.Progressbar(self, length=420, mode="determinate", maximum=100.0)
        self.progress.pack(fill="x")

        self.bytes_var = tk.StringVar(value="")
        tk.Label(
            self,
            textvariable=self.bytes_var,
            bg=PAGE_BG,
            fg=MUTED_FG,
            font=("Segoe UI", 9),
        ).pack(anchor="w", pady=(4, 8))

        self.cancel_btn = ttk.Button(self, text="Hủy", command=self._cancel)
        self.cancel_btn.pack(anchor="e")

        self.update_idletasks()
        parent_root = parent.winfo_toplevel()
        px = parent_root.winfo_rootx() + (parent_root.winfo_width() - self.winfo_width()) // 2
        py = parent_root.winfo_rooty() + (parent_root.winfo_height() - self.winfo_height()) // 2
        self.geometry(f"+{max(0, px)}+{max(0, py)}")

        threading.Thread(target=self._run, daemon=True).start()

    def _cancel(self) -> None:
        if not self._cancelled and not self._closed:
            self._cancelled = True
            self.cancel_btn.configure(state="disabled", text="Đang hủy...")

    def _on_file_start(self, filename: str, idx: int, total_count: int) -> None:
        def do() -> None:
            self.file_var.set(f"({idx}/{total_count}) {filename}")
            self.progress.configure(value=0.0)
            self.bytes_var.set("")
        self.after(0, do)

    def _on_file_bytes(self, downloaded: int, total: int) -> None:
        def do() -> None:
            if total > 0:
                pct = (downloaded / total) * 100.0
                self.progress.configure(value=min(100.0, pct))
                self.bytes_var.set(f"{_fmt_size(downloaded)} / {_fmt_size(total)}")
            else:
                self.bytes_var.set(f"{_fmt_size(downloaded)}")
        self.after(0, do)

    def _is_cancelled(self) -> bool:
        return self._cancelled

    def _run(self) -> None:
        error: str | None = None
        success = False
        try:
            download_model(
                target_dir=self._target_dir,
                base_url=self._base_url,
                on_file_start=self._on_file_start,
                on_file_bytes=self._on_file_bytes,
                is_cancelled=self._is_cancelled,
            )
            success = True
        except CancelledError:
            error = None
        except DownloadError as exc:
            error = str(exc)
        except Exception as exc:
            error = f"Lỗi không xác định: {exc}"
        finally:
            self.after(0, lambda: self._close(success, error))

    def _close(self, success: bool, error: str | None) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self.grab_release()
        except tk.TclError:
            pass
        self.destroy()
        self._on_finish(success, error)


class MP3ToSrtPage(ToolPage):
    name = "MP3 to SRT"
    description = "Phiên âm MP3 thành phụ đề SRT cho video caption"

    def __init__(self, parent: tk.Misc, app) -> None:
        self._busy = False
        self._cancel_flag = False
        super().__init__(parent, app)

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
        tk.Entry(card, textvariable=self.src_var).grid(
            row=0, column=1, sticky="ew", padx=8, pady=4)
        ttk.Button(card, text="Chọn...", command=self._pick_src).grid(
            row=0, column=2, pady=4)

        tk.Label(card, text="File xuất ra:", bg=CARD_BG, fg=LABEL_FG).grid(
            row=1, column=0, sticky="w", pady=4)
        self.out_var = tk.StringVar()
        tk.Entry(card, textvariable=self.out_var).grid(
            row=1, column=1, sticky="ew", padx=8, pady=4)
        ttk.Button(card, text="Lưu thành...", command=self._pick_out).grid(
            row=1, column=2, pady=4)

        tk.Label(card, text="Thiết bị:", bg=CARD_BG, fg=LABEL_FG).grid(
            row=2, column=0, sticky="w", pady=4)
        self.device_select_var = tk.StringVar(value=DEVICE_LABELS[DEVICE_CPU])
        ttk.Combobox(
            card,
            textvariable=self.device_select_var,
            values=[DEVICE_LABELS[DEVICE_CPU], DEVICE_LABELS[DEVICE_GPU]],
            state="readonly",
            width=18,
        ).grid(row=2, column=1, sticky="w", padx=8, pady=4)

        tk.Label(card, text="Model:", bg=CARD_BG, fg=LABEL_FG).grid(
            row=3, column=0, sticky="w", pady=4)
        self.model_size_var = tk.StringVar(value=DEFAULT_MODEL_SIZE)
        ttk.Combobox(
            card,
            textvariable=self.model_size_var,
            values=[f"{name} ({MODEL_SIZE_HINTS[name]})" for name in MODEL_SIZES],
            state="readonly",
            width=24,
        ).grid(row=3, column=1, sticky="w", padx=8, pady=4)
        self.model_size_var.set(f"{DEFAULT_MODEL_SIZE} ({MODEL_SIZE_HINTS[DEFAULT_MODEL_SIZE]})")

        card.columnconfigure(1, weight=1)

        bar = tk.Frame(outer, bg=PAGE_BG)
        bar.pack(fill="x", pady=(10, 8))
        self.start_btn = ttk.Button(bar, text="Bắt đầu", command=self._start)
        self.start_btn.pack(side="left")
        self.cancel_btn = ttk.Button(bar, text="Hủy", command=self._cancel, state="disabled")
        self.cancel_btn.pack(side="left", padx=8)
        ttk.Button(bar, text="Xóa log", command=self._clear_log).pack(side="left")

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

        self.status_var = tk.StringVar(value="Sẵn sàng")
        tk.Label(
            outer, textvariable=self.status_var, bg=PAGE_BG,
            fg=MUTED_FG, anchor="w",
        ).pack(fill="x", pady=(6, 0))

    def _pick_src(self) -> None:
        f = filedialog.askopenfilename(
            title="Chọn file MP3",
            filetypes=[("File MP3", "*.mp3"), ("Tất cả file", "*.*")],
        )
        if f:
            self.src_var.set(f)
            if not self.out_var.get():
                self.out_var.set(str(Path(f).with_suffix(".srt")))

    def _pick_out(self) -> None:
        f = filedialog.asksaveasfilename(
            title="Lưu file SRT",
            defaultextension=".srt",
            filetypes=[("File SRT", "*.srt"), ("Tất cả file", "*.*")],
        )
        if f:
            self.out_var.set(f)

    def _clear_log(self) -> None:
        self.log_box.configure(state="normal")
        self.log_box.delete("1.0", "end")
        self.log_box.configure(state="disabled")

    def _log(self, msg: str) -> None:
        def do() -> None:
            self.log_box.configure(state="normal")
            self.log_box.insert("end", msg)
            self.log_box.see("end")
            self.log_box.configure(state="disabled")
        self.frame.after(0, do)

    def _set_status(self, msg: str) -> None:
        self.frame.after(0, lambda: self.status_var.set(msg))

    def _selected_device(self) -> str:
        label = self.device_select_var.get()
        return DEVICE_GPU if label == DEVICE_LABELS[DEVICE_GPU] else DEVICE_CPU

    def _selected_model_size(self) -> str:
        label = self.model_size_var.get()
        for name in MODEL_SIZES:
            if label.startswith(name):
                return name
        return DEFAULT_MODEL_SIZE

    def _cancel(self) -> None:
        if self._busy:
            self._cancel_flag = True
            self._set_status("Đang hủy...")

    def _start(self) -> None:
        if self._busy:
            return
        src = self.src_var.get().strip()
        out = self.out_var.get().strip()
        if not src or not Path(src).is_file():
            messagebox.showerror("Lỗi", "Hãy chọn file MP3 hợp lệ.")
            return
        if not out:
            messagebox.showerror("Lỗi", "Hãy chọn đường dẫn file SRT.")
            return
        src_path = Path(src)
        out_path = Path(out)
        try:
            if src_path.resolve() == out_path.resolve():
                messagebox.showerror("Lỗi", "File xuất ra trùng với file nguồn.")
                return
        except OSError:
            pass
        if out_path.exists():
            if not messagebox.askyesno("Ghi đè?", f"{out_path} đã tồn tại.\nGhi đè?"):
                return

        model_size = self._selected_model_size()
        size_hint = MODEL_SIZE_HINTS[model_size]
        model_dir = find_model_dir(model_size)
        if model_dir is None:
            if not messagebox.askyesno(
                "Thiếu model",
                f"Không tìm thấy model {model_size} ({size_hint}).\n\nTải về ngay?",
            ):
                return
            target_dir = _exe_dir() / "models" / model_size
            device = self._selected_device()

            def on_download_finish(success: bool, error: str | None) -> None:
                if not success:
                    if error:
                        messagebox.showerror("Lỗi tải model", error)
                    self._set_status("Sẵn sàng")
                    return
                retry_dir = find_model_dir(model_size)
                if retry_dir is None:
                    messagebox.showerror("Lỗi", "Tải xong nhưng vẫn không tìm thấy model.")
                    self._set_status("Sẵn sàng")
                    return
                self._launch_transcription(src_path, out_path, retry_dir, device)

            self._set_status("Đang tải model...")
            ModelDownloadDialog(
                self.frame,
                target_dir=target_dir,
                model_name=model_size,
                base_url=_model_repo_url(model_size),
                size_hint=size_hint,
                on_finish=on_download_finish,
            )
            return

        self._launch_transcription(src_path, out_path, model_dir, self._selected_device())

    def _launch_transcription(self, src_path: Path, out_path: Path, model_dir: Path, device: str) -> None:
        self._busy = True
        self._cancel_flag = False
        self.start_btn.configure(state="disabled")
        self.cancel_btn.configure(state="normal")
        self._set_status("Đang nạp model...")
        threading.Thread(
            target=self._do_run,
            args=(src_path, out_path, model_dir, device),
            daemon=True,
        ).start()

    def _do_run(self, mp3_path: Path, out_path: Path, model_dir: Path, device: str) -> None:
        try:
            words = transcribe_mp3(
                mp3_path=mp3_path,
                model_dir=model_dir,
                device=device,
                on_log=self._log,
                on_progress=lambda pct, msg: self._set_status(msg),
                is_cancelled=lambda: self._cancel_flag,
            )
            if self._cancel_flag:
                raise CancelledError()
            self._set_status("Đang ghi SRT...")
            cues = pack_cues(words)
            write_srt(cues, out_path)
            self._log(f"\nĐã ghi {len(cues)} cue → {out_path}\n")
            self._set_status(f"Hoàn tất: {out_path}")
            self.frame.after(0, lambda: messagebox.showinfo(
                "Hoàn tất", f"Đã tạo phụ đề:\n{out_path}"))
        except CancelledError:
            self._log("\nĐã hủy.\n")
            self._set_status("Đã hủy")
        except ModelMissingError as exc:
            self._log(f"\n{exc}\n")
            self._set_status("Thiếu model")
            self.frame.after(0, lambda e=exc: messagebox.showerror("Thiếu model", str(e)))
        except Exception as exc:
            self._log(f"\nLỗi: {exc}\n")
            self._set_status(f"Lỗi: {exc}")
            self.frame.after(0, lambda e=exc: messagebox.showerror("Lỗi", str(e)))
        finally:
            self._busy = False
            self._cancel_flag = False
            self.frame.after(0, lambda: self.start_btn.configure(state="normal"))
            self.frame.after(0, lambda: self.cancel_btn.configure(state="disabled"))
