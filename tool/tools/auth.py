from __future__ import annotations

import json
import os
import socket
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Union


API_BASE = "http://a4071-tool.j4m.dev:4071"
VERIFY_PATH = "/api/verify"
TIMEOUT_SEC = 5
APP_DIR_NAME = "A4071-Tool"
CONFIG_FILENAME = "config.json"


@dataclass(frozen=True)
class VerifyOk:
    name: str


@dataclass(frozen=True)
class VerifyInvalid:
    pass


@dataclass(frozen=True)
class VerifyNetworkError:
    message: str


VerifyResult = Union[VerifyOk, VerifyInvalid, VerifyNetworkError]


def verify_key(api_key: str) -> VerifyResult:
    url = f"{API_BASE}{VERIFY_PATH}"
    req = urllib.request.Request(url, headers={"X-API-Key": api_key})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_SEC) as resp:
            if resp.status != 200:
                return VerifyNetworkError(f"Server trả về mã {resp.status}.")
            payload = json.loads(resp.read().decode("utf-8"))
            name = payload.get("name") or ""
            return VerifyOk(name=name)
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            return VerifyInvalid()
        return VerifyNetworkError(f"Server trả về mã {exc.code}.")
    except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError) as exc:
        return VerifyNetworkError(str(exc) or "Không kết nối được server.")
    except json.JSONDecodeError:
        return VerifyNetworkError("Phản hồi không hợp lệ.")


def config_path() -> Path:
    appdata = os.environ.get("APPDATA")
    if appdata:
        return Path(appdata) / APP_DIR_NAME / CONFIG_FILENAME
    return Path.home() / ".a4071-tool" / CONFIG_FILENAME


def load_config() -> dict | None:
    path = config_path()
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict) or not data.get("api_key"):
        return None
    return data


def save_config(api_key: str, name: str) -> None:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"api_key": api_key, "name": name}, ensure_ascii=False),
        encoding="utf-8",
    )
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def clear_config() -> None:
    path = config_path()
    try:
        path.unlink()
    except FileNotFoundError:
        pass
