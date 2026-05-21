from __future__ import annotations

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
