from __future__ import annotations

import json
import socket
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Union


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
