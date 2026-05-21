from __future__ import annotations

import hashlib
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import tkinter as tk
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from tkinter import ttk
from typing import Callable, Union


API_BASE = "http://a4071-tool.j4m.dev:4071"
VERSION_PATH = "/api/version"
DOWNLOAD_PATH = "/api/download"
HTTP_TIMEOUT_SEC = 10
DOWNLOAD_TIMEOUT_SEC = 60


@dataclass(frozen=True)
class UpdateAvailable:
    latest: str
    notes: str
    sha256: str
    size: int
    download_url: str


@dataclass(frozen=True)
class UpToDate:
    pass


@dataclass(frozen=True)
class CheckSkipped:
    reason: str  # "not_frozen" | "network_error" | "bad_response" | "unauthorized"


CheckResult = Union[UpdateAvailable, UpToDate, CheckSkipped]


class UpdateError(Exception):
    pass


def parse_version(s: str) -> tuple[int, int, int] | None:
    if not s:
        return None
    parts = s.strip().split(".")
    if len(parts) > 3:
        return None
    nums: list[int] = []
    for p in parts:
        if not p.isdigit():
            return None
        nums.append(int(p))
    while len(nums) < 3:
        nums.append(0)
    return (nums[0], nums[1], nums[2])


def compare_versions(a: str, b: str) -> int:
    pa = parse_version(a)
    pb = parse_version(b)
    if pa is None or pb is None:
        return 0
    if pa < pb:
        return -1
    if pa > pb:
        return 1
    return 0


def check_update(api_key: str, current_version: str) -> CheckResult:
    if not getattr(sys, "frozen", False):
        return CheckSkipped("not_frozen")

    url = f"{API_BASE}{VERSION_PATH}"
    req = urllib.request.Request(url, headers={"X-API-Key": api_key})
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_SEC) as resp:
            if resp.status != 200:
                return CheckSkipped("bad_response")
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            return CheckSkipped("unauthorized")
        return CheckSkipped("bad_response")
    except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError):
        return CheckSkipped("network_error")
    except json.JSONDecodeError:
        return CheckSkipped("bad_response")

    latest = str(payload.get("latest") or "")
    sha256 = str(payload.get("sha256") or "").lower()
    size = int(payload.get("size") or 0)
    notes = str(payload.get("notes") or "")
    if not latest or not sha256 or size <= 0:
        return CheckSkipped("bad_response")

    if compare_versions(latest, current_version) <= 0:
        return UpToDate()

    return UpdateAvailable(
        latest=latest,
        notes=notes,
        sha256=sha256,
        size=size,
        download_url=f"{API_BASE}{DOWNLOAD_PATH}",
    )


def download_update(
    info: UpdateAvailable,
    api_key: str,
    on_progress: Callable[[int, int], None],
) -> Path:
    tmp_dir = Path(tempfile.gettempdir())
    part = tmp_dir / "A4071-Tool-update.exe.part"
    final = tmp_dir / "A4071-Tool-update.exe"
    part.unlink(missing_ok=True)
    final.unlink(missing_ok=True)

    req = urllib.request.Request(
        info.download_url, headers={"X-API-Key": api_key}
    )
    try:
        with urllib.request.urlopen(req, timeout=DOWNLOAD_TIMEOUT_SEC) as resp:
            if resp.status != 200:
                raise UpdateError(f"Server trả về mã {resp.status}.")
            total = int(resp.headers.get("Content-Length") or info.size)
            h = hashlib.sha256()
            done = 0
            on_progress(0, total)
            with open(part, "wb") as f:
                while True:
                    chunk = resp.read(64 * 1024)
                    if not chunk:
                        break
                    f.write(chunk)
                    h.update(chunk)
                    done += len(chunk)
                    on_progress(done, total)
    except urllib.error.HTTPError as exc:
        part.unlink(missing_ok=True)
        raise UpdateError(f"Tải bản cập nhật thất bại ({exc.code}).") from exc
    except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError) as exc:
        part.unlink(missing_ok=True)
        raise UpdateError("Tải bản cập nhật thất bại. Kiểm tra kết nối.") from exc
    except OSError as exc:
        part.unlink(missing_ok=True)
        raise UpdateError("Không ghi được file tạm.") from exc

    if h.hexdigest().lower() != info.sha256.lower():
        part.unlink(missing_ok=True)
        raise UpdateError("File tải về bị lỗi. Vui lòng thử lại.")

    os.replace(part, final)
    return final


_UPDATER_BAT_TEMPLATE = r"""@echo off
chcp 65001 >nul
setlocal
:wait
tasklist /FI "PID eq {PID}" 2>nul | find "{PID}" >nul
if not errorlevel 1 (
    timeout /t 1 /nobreak >nul
    goto wait
)
set /a tries=0
:retry
move /Y "{NEW_EXE}" "{CURRENT_EXE}" >nul
if not errorlevel 1 goto done
set /a tries+=1
if %tries% lss 10 (
    timeout /t 1 /nobreak >nul
    goto retry
)
del "%~f0"
exit /b 1
:done
(goto) 2>nul & del "%~f0"
"""


def _render_updater_bat(pid: int, new_exe: str, current_exe: str) -> str:
    return (
        _UPDATER_BAT_TEMPLATE
        .replace("{PID}", str(pid))
        .replace("{NEW_EXE}", new_exe)
        .replace("{CURRENT_EXE}", current_exe)
    )


def apply_update_and_exit(new_exe: Path, current_exe: Path) -> None:
    pid = os.getpid()
    bat_path = Path(tempfile.gettempdir()) / f"a4071-update-{pid}.bat"
    bat_path.write_text(
        _render_updater_bat(pid, str(new_exe), str(current_exe)),
        encoding="utf-8",
    )

    creationflags = 0
    if sys.platform == "win32":
        creationflags = (
            subprocess.CREATE_NEW_PROCESS_GROUP
            | subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]
        )

    subprocess.Popen(
        ["cmd", "/c", str(bat_path)],
        creationflags=creationflags,
        close_fds=True,
        cwd=str(bat_path.parent),
    )
    os._exit(0)


_DIALOG_BG = "#f8fafc"
_HEADER_FG = "#111827"
_MUTED_FG = "#6b7280"
_PRIMARY_BG = "#2563eb"
_PRIMARY_FG = "#ffffff"
_SECONDARY_FG = "#2563eb"
_ERROR_FG = "#b91c1c"


class UpdateDialog(tk.Toplevel):
    """Single Toplevel that swaps between prompt/progress/error views.

    The caller owns the api_key and the current_exe path. The dialog
    drives the download on a worker thread, marshals progress back to
    the UI thread, and on success calls apply_update_and_exit which
    terminates the process.
    """

    def __init__(
        self,
        parent: tk.Misc,
        info: UpdateAvailable,
        api_key: str,
        current_version: str,
        current_exe: Path,
    ) -> None:
        super().__init__(parent)
        self._info = info
        self._api_key = api_key
        self._current_exe = current_exe
        self._closeable = True

        self.title("Cập nhật A4071-Tool")
        self.configure(bg=_DIALOG_BG)
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()
        self.protocol("WM_DELETE_WINDOW", self._on_close_request)

        self._body = tk.Frame(self, bg=_DIALOG_BG, padx=24, pady=20)
        self._body.pack(fill="both", expand=True)

        self._show_prompt(current_version)
        self._center_on_parent(parent)

    def _center_on_parent(self, parent: tk.Misc) -> None:
        self.update_idletasks()
        try:
            px = parent.winfo_rootx()
            py = parent.winfo_rooty()
            pw = parent.winfo_width()
            ph = parent.winfo_height()
        except tk.TclError:
            return
        w = self.winfo_width()
        h = self.winfo_height()
        x = px + max(0, (pw - w) // 2)
        y = py + max(0, (ph - h) // 2)
        self.geometry(f"+{x}+{y}")

    def _clear_body(self) -> None:
        for child in self._body.winfo_children():
            child.destroy()

    def _on_close_request(self) -> None:
        if self._closeable:
            self.destroy()

    def _show_prompt(self, current_version: str) -> None:
        self._clear_body()
        tk.Label(
            self._body,
            text=f"Đã có phiên bản {self._info.latest}",
            bg=_DIALOG_BG, fg=_HEADER_FG,
            font=("Segoe UI Semibold", 13),
            anchor="w",
        ).pack(fill="x")
        tk.Label(
            self._body,
            text=f"Phiên bản hiện tại: {current_version}",
            bg=_DIALOG_BG, fg=_MUTED_FG,
            font=("Segoe UI", 9), anchor="w",
        ).pack(fill="x", pady=(2, 12))

        notes = self._info.notes.strip() or "Không có ghi chú."
        notes_frame = tk.Frame(self._body, bg=_DIALOG_BG)
        notes_frame.pack(fill="both", expand=True)
        text = tk.Text(
            notes_frame, height=8, width=52, wrap="word",
            bg="#ffffff", fg=_HEADER_FG, relief="solid", bd=1,
            font=("Segoe UI", 9),
        )
        scroll = ttk.Scrollbar(notes_frame, orient="vertical", command=text.yview)
        text.configure(yscrollcommand=scroll.set)
        text.insert("1.0", notes)
        text.configure(state="disabled")
        text.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        btns = tk.Frame(self._body, bg=_DIALOG_BG)
        btns.pack(fill="x", pady=(16, 0))
        tk.Button(
            btns, text="Để sau", bg=_DIALOG_BG, fg=_SECONDARY_FG,
            relief="flat", cursor="hand2",
            font=("Segoe UI", 10), padx=12, pady=6,
            command=self._on_close_request,
        ).pack(side="right", padx=(8, 0))
        tk.Button(
            btns, text="Cập nhật ngay", bg=_PRIMARY_BG, fg=_PRIMARY_FG,
            relief="flat", cursor="hand2",
            font=("Segoe UI Semibold", 10), padx=14, pady=6,
            command=self._start_download,
        ).pack(side="right")

    def _start_download(self) -> None:
        self._closeable = False
        self._show_progress()
        thread = threading.Thread(target=self._worker, daemon=True)
        thread.start()

    def _show_progress(self) -> None:
        self._clear_body()
        tk.Label(
            self._body,
            text=f"Đang tải bản {self._info.latest}…",
            bg=_DIALOG_BG, fg=_HEADER_FG,
            font=("Segoe UI Semibold", 12), anchor="w",
        ).pack(fill="x")
        self._progress = ttk.Progressbar(
            self._body, orient="horizontal", length=420,
            mode="determinate", maximum=max(1, self._info.size),
        )
        self._progress.pack(fill="x", pady=(14, 4))
        self._status = tk.Label(
            self._body, text="0.0 / 0.0 MB",
            bg=_DIALOG_BG, fg=_MUTED_FG,
            font=("Segoe UI", 9), anchor="w",
        )
        self._status.pack(fill="x")

    def _show_error(self, message: str) -> None:
        self._closeable = True
        self._clear_body()
        tk.Label(
            self._body,
            text="Cập nhật thất bại",
            bg=_DIALOG_BG, fg=_ERROR_FG,
            font=("Segoe UI Semibold", 12), anchor="w",
        ).pack(fill="x")
        tk.Label(
            self._body, text=message,
            bg=_DIALOG_BG, fg=_HEADER_FG,
            font=("Segoe UI", 10), anchor="w", justify="left",
            wraplength=420,
        ).pack(fill="x", pady=(8, 16))
        tk.Button(
            self._body, text="Đóng", bg=_DIALOG_BG, fg=_SECONDARY_FG,
            relief="flat", cursor="hand2",
            font=("Segoe UI", 10), padx=12, pady=6,
            command=self.destroy,
        ).pack(side="right")

    def _on_progress(self, done: int, total: int) -> None:
        def apply() -> None:
            try:
                self._progress.configure(maximum=max(1, total), value=done)
                mb_done = done / (1024 * 1024)
                mb_total = total / (1024 * 1024)
                self._status.configure(text=f"{mb_done:.1f} / {mb_total:.1f} MB")
            except tk.TclError:
                pass
        try:
            self.after(0, apply)
        except tk.TclError:
            pass

    def _worker(self) -> None:
        try:
            path = download_update(self._info, self._api_key, self._on_progress)
        except UpdateError as exc:
            msg = str(exc)
            self.after(0, lambda: self._show_error(msg))
            return
        except Exception:
            self.after(0, lambda: self._show_error("Đã có lỗi không xác định."))
            return

        self.after(0, lambda: self._show_ready(path))

    def _show_ready(self, new_exe: Path) -> None:
        self._closeable = True
        self._clear_body()
        tk.Label(
            self._body,
            text="Sẵn sàng cập nhật",
            bg=_DIALOG_BG, fg=_HEADER_FG,
            font=("Segoe UI Semibold", 13),
            anchor="w",
        ).pack(fill="x")
        tk.Label(
            self._body,
            text=(
                f"Đã tải xong bản {self._info.latest}.\n"
                "Nhấn 'Hoàn tất' để đóng app và áp dụng cập nhật.\n"
                "Sau đó mở lại A4071-Tool để dùng bản mới."
            ),
            bg=_DIALOG_BG, fg=_HEADER_FG,
            font=("Segoe UI", 10), anchor="w", justify="left",
            wraplength=420,
        ).pack(fill="x", pady=(8, 16))

        def finish() -> None:
            try:
                apply_update_and_exit(new_exe, self._current_exe)
            except OSError:
                try:
                    self._show_error("Không ghi được trình cập nhật.")
                except tk.TclError:
                    pass

        tk.Button(
            self._body, text="Hoàn tất", bg=_PRIMARY_BG, fg=_PRIMARY_FG,
            relief="flat", cursor="hand2",
            font=("Segoe UI Semibold", 10), padx=14, pady=6,
            command=finish,
        ).pack(side="right")
