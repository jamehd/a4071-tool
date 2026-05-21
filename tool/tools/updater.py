from __future__ import annotations

import hashlib
import json
import os
import socket
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
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
if not errorlevel 1 goto launch
set /a tries+=1
if %tries% lss 10 (
    timeout /t 1 /nobreak >nul
    goto retry
)
del "%~f0"
exit /b 1
:launch
start "" "{CURRENT_EXE}"
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
            subprocess.DETACHED_PROCESS  # type: ignore[attr-defined]
            | subprocess.CREATE_NEW_PROCESS_GROUP
            | subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]
        )

    subprocess.Popen(
        ["cmd", "/c", str(bat_path)],
        creationflags=creationflags,
        close_fds=True,
        cwd=str(bat_path.parent),
    )
    sys.exit(0)
