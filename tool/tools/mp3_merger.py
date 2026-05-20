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


class MP3MergerPage(ToolPage):
    name = "MP3 Merger"
    description = "Gộp nhiều file MP3 thành 1 file duy nhất"

    def __init__(self, parent: tk.Misc, app) -> None:
        self._files: list[Path] = []
        self._busy = False
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

        card.columnconfigure(1, weight=1)

        bar = tk.Frame(outer, bg=PAGE_BG)
        bar.pack(fill="x", pady=(10, 8))
        ttk.Button(bar, text="Quét", command=self._scan).pack(side="left")
        self.start_btn = ttk.Button(bar, text="Bắt đầu gộp", command=self._start)
        self.start_btn.pack(side="left", padx=8)
        ttk.Button(bar, text="Xóa log", command=self._clear_log).pack(side="left")
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

        self.status_var = tk.StringVar(value="Sẵn sàng")
        tk.Label(
            outer, textvariable=self.status_var, bg=PAGE_BG,
            fg=MUTED_FG, anchor="w",
        ).pack(fill="x", pady=(6, 0))

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
        self.status_var.set("Đang quét...")
        self.frame.update_idletasks()
        files = scan_mp3(Path(src))
        self._files = files
        self.listbox.delete(0, "end")
        for f in files:
            self.listbox.insert("end", str(f))
        self.count_var.set(f"{len(files)} file")
        self.status_var.set(f"Tìm thấy {len(files)} file MP3")

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
        self.start_btn.configure(state="disabled")
        self.status_var.set("Đang gộp...")
        threading.Thread(
            target=self._do_merge,
            args=(ffmpeg, list(self._files), out_path),
            daemon=True,
        ).start()

    def _do_merge(self, ffmpeg: str, files: list[Path], out: Path) -> None:
        list_path: Optional[str] = None
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

            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=CREATE_NO_WINDOW,
            )
            assert proc.stdout is not None
            for line in proc.stdout:
                self._log(line)
            rc = proc.wait()

            if rc == 0:
                self._log(f"\nHoàn tất. File: {out}\n")
                self._set_status(f"Hoàn tất: {out}")
                self.frame.after(0, lambda: messagebox.showinfo(
                    "Hoàn tất", f"Đã gộp thành công:\n{out}"))
            else:
                self._log(f"\nffmpeg kết thúc với mã {rc}\n")
                self._set_status(f"Thất bại (mã {rc})")
                self.frame.after(0, lambda: messagebox.showerror(
                    "Thất bại", f"ffmpeg kết thúc với mã {rc}"))
        except Exception as exc:
            self._log(f"\nLỗi: {exc}\n")
            self._set_status(f"Lỗi: {exc}")
            self.frame.after(0, lambda e=exc: messagebox.showerror("Lỗi", str(e)))
        finally:
            if list_path:
                try:
                    os.unlink(list_path)
                except OSError:
                    pass
            self._busy = False
            self.frame.after(0, lambda: self.start_btn.configure(state="normal"))

    def _log(self, msg: str) -> None:
        def do() -> None:
            self.log_box.configure(state="normal")
            self.log_box.insert("end", msg)
            self.log_box.see("end")
            self.log_box.configure(state="disabled")
        self.frame.after(0, do)

    def _set_status(self, msg: str) -> None:
        self.frame.after(0, lambda: self.status_var.set(msg))

    def _clear_log(self) -> None:
        self.log_box.configure(state="normal")
        self.log_box.delete("1.0", "end")
        self.log_box.configure(state="disabled")
