from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Optional

from .base import ToolPage
from .progress import ProgressPanel


CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0
CARD_BG = "#ffffff"
PAGE_BG = "#f8fafc"
LABEL_FG = "#374151"
MUTED_FG = "#6b7280"


def natural_key(name: str):
    return [int(part) if part.isdigit() else part.lower()
            for part in re.split(r"(\d+)", name)]


def app_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


def find_ffmpeg() -> Optional[str]:
    candidates: list[Path] = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.append(Path(meipass) / "ffmpeg.exe")
    candidates.append(app_root() / "ffmpeg.exe")
    for c in candidates:
        if c.is_file():
            return str(c)
    found = shutil.which("ffmpeg")
    return found


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


def scan_mp3(root: Path) -> list[Path]:
    found: list[Path] = []

    def walk(directory: Path) -> None:
        try:
            children = list(directory.iterdir())
        except (PermissionError, OSError):
            return
        files = sorted(
            (c for c in children if c.is_file() and c.suffix.lower() == ".mp3"),
            key=lambda p: natural_key(p.name),
        )
        found.extend(files)
        subdirs = sorted(
            (c for c in children if c.is_dir()),
            key=lambda p: natural_key(p.name),
        )
        for sub in subdirs:
            walk(sub)

    walk(root)
    return found


def to_safe_concat_path(path: str) -> str:
    """On Windows, convert to ASCII 8.3 short path when possible, else return as-is (UTF-8)."""
    if os.name != "nt":
        return path
    try:
        import ctypes
        from ctypes import wintypes

        get_short = ctypes.windll.kernel32.GetShortPathNameW
        get_short.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
        get_short.restype = wintypes.DWORD

        buf = ctypes.create_unicode_buffer(520)
        size = get_short(path, buf, 520)
        if size == 0:
            return path
        if size > 520:
            buf = ctypes.create_unicode_buffer(size + 1)
            if get_short(path, buf, size + 1) == 0:
                return path
        short = buf.value
        if not short:
            return path
        try:
            short.encode("ascii")
            return short
        except UnicodeEncodeError:
            return path
    except Exception:
        return path


def escape_concat_path(path: str) -> str:
    return path.replace("'", "'\\''")


_FFMPEG_TIME_RE = re.compile(r"time=(\d+):(\d+):(\d+(?:\.\d+)?)")


def _format_hms(seconds: float) -> str:
    if seconds < 0:
        seconds = 0
    total = int(round(seconds))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


class MP3MergerPage(ToolPage):
    name = "MP3 Merger"
    description = "Gộp nhiều file MP3 thành 1 file duy nhất"

    def __init__(self, parent: tk.Misc, app) -> None:
        self._files: list[Path] = []
        self._busy = False
        self._cancel_flag = False
        self._proc: subprocess.Popen | None = None
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

    def _pick_src(self) -> None:
        d = filedialog.askdirectory(title="Chọn thư mục cha chứa các file MP3")
        if d:
            self.src_var.set(d)
            if not self.out_var.get():
                self.out_var.set(str(Path(d) / "merged.mp3"))

    def _pick_out(self) -> None:
        f = filedialog.asksaveasfilename(
            title="Lưu file MP3 đã gộp",
            defaultextension=".mp3",
            filetypes=[("File MP3", "*.mp3"), ("Tất cả file", "*.*")],
        )
        if f:
            self.out_var.set(f)

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

    def _start(self) -> None:
        if self._busy:
            return
        if not self._files:
            self._scan()
            if not self._files:
                messagebox.showinfo("Không có file", "Không tìm thấy file MP3 nào trong thư mục nguồn.")
                return

        ffmpeg = find_ffmpeg()
        if not ffmpeg:
            ffmpeg = filedialog.askopenfilename(
                title="Tìm đường dẫn ffmpeg.exe",
                filetypes=[("ffmpeg", "ffmpeg.exe"), ("File thực thi", "*.exe")],
            )
            if not ffmpeg or not Path(ffmpeg).is_file():
                messagebox.showerror(
                    "Không tìm thấy ffmpeg",
                    "Thiếu ffmpeg.exe. Đặt cạnh A4071-Tool.exe hoặc chọn thủ công.",
                )
                return

        out = self.out_var.get().strip()
        if not out:
            messagebox.showerror("Lỗi", "Hãy chọn đường dẫn file xuất ra.")
            return
        out_path = Path(out)
        if out_path.exists():
            if not messagebox.askyesno("Ghi đè?", f"{out} đã tồn tại.\nGhi đè?"):
                return
        for f in self._files:
            try:
                if out_path.resolve() == f.resolve():
                    messagebox.showerror(
                        "Lỗi",
                        "File xuất ra trùng với một trong các file nguồn. Hãy chọn đường dẫn khác.",
                    )
                    return
            except OSError:
                pass

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
            # Cancelled during pre-scan. Release the lock and reset state.
            self.progress.set_indeterminate("Đã hủy")
            self._busy = False
            self._cancel_flag = False
            self.frame.after(0, lambda: self.start_btn.configure(state="normal"))
            self.frame.after(0, lambda: self.cancel_btn.configure(state="disabled"))
            self.frame.after(0, lambda: self.app.end_busy(self.name))
            return
        if total is not None and total > 0:
            self.progress.start("Đang gộp...")
        else:
            self.progress.set_indeterminate("Đang gộp... (không xác định được tiến độ)")
        self._do_merge(ffmpeg, files, out, total)

    def _do_merge(
        self,
        ffmpeg: str,
        files: list[Path],
        out: Path,
        total_sec: float | None = None,
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
            self.progress.set_indeterminate("Đã hủy")
        elif error:
            self.progress.set_indeterminate(f"Thất bại: {error}")
            self.frame.after(0, lambda e=error: messagebox.showerror("Thất bại", e))
        self._busy = False
        self._cancel_flag = False
        self.frame.after(0, lambda: self.start_btn.configure(state="normal"))
        self.frame.after(0, lambda: self.cancel_btn.configure(state="disabled"))
        self.frame.after(0, lambda: self.app.end_busy(self.name))

    def _log(self, msg: str) -> None:
        def do() -> None:
            self.log_box.configure(state="normal")
            self.log_box.insert("end", msg)
            self.log_box.see("end")
            self.log_box.configure(state="disabled")
        self.frame.after(0, do)

    def _clear_log(self) -> None:
        self.log_box.configure(state="normal")
        self.log_box.delete("1.0", "end")
        self.log_box.configure(state="disabled")

    def _cancel(self) -> None:
        if not self._busy:
            return
        self._cancel_flag = True
        self.progress.set_indeterminate("Đang hủy...")
        proc = self._proc
        if proc is not None:
            try:
                proc.terminate()
            except OSError:
                pass

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

    def request_cancel(self) -> None:
        if self._busy:
            self._cancel()
