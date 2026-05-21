# A4071-Tool Auto-Update Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** On every launch (after API key verification), the tool checks the backend for a newer version, prompts the user, and on accept downloads + verifies + replaces the running `A4071-Tool.exe` via a Windows `.bat` helper that relaunches the app.

**Architecture:** Two new endpoints on the existing Go backend (`/api/version`, `/api/download`) gated by the existing API key middleware, serving from a configured update directory. A new `tool/tools/updater.py` module owns version checking, download with SHA-256 verification, and the helper-script swap. `A4071App` triggers the check ~500 ms after the main screen mounts and surfaces a Toplevel dialog for the user's choice.

**Tech Stack:** Go 1.22 (chi router) on the backend. Python 3.10+, Tkinter, `urllib.request`, `hashlib`, `subprocess`, `threading` — stdlib only — on the tool side. No new third-party deps either side.

**Spec:** [`docs/superpowers/specs/2026-05-21-app-update-design.md`](../specs/2026-05-21-app-update-design.md)

**Testing note:** Pure-logic helpers (semver compare, .bat script rendering) get unit tests under `tool/tests/`. Network calls, UI dialogs, and the on-disk swap get manual smoke tests, matching the spec.

---

## File Structure

**Backend (Go):**
- Create `backend/update.go` — env-var loaded config, `/api/version` and `/api/download` handlers.
- Modify `backend/main.go` — wire the new routes behind `apiKeyAuth`, call config loader at startup.
- Modify `docker-compose.yml` — add `UPDATE_DIR`, `UPDATE_VERSION`, `UPDATE_NOTES`, bind mount.

**Tool (Python):**
- Create `tool/tools/updater.py` — module owning all update logic and the dialog widget.
- Create `tool/tests/test_updater.py` — unit tests for `parse_version`, `compare_versions`, and `_render_updater_bat`.
- Modify `tool/a4071_tool.py` — kick the update check after `_show_main`; pass the saved API key.

The update logic and its small UI dialog live together in `tool/tools/updater.py`. The dialog is tightly coupled to download progress + error states and is used from only one place, so splitting it into its own file would add overhead without clarity.

---

## Task 1: Backend — config loader and `/api/version` handler

**Files:**
- Create: `backend/update.go`
- Test: manual via `curl`

- [ ] **Step 1: Create `backend/update.go` with config loader and version handler**

```go
package main

import (
	"net/http"
	"os"
	"path/filepath"
	"strings"
)

type updateConfig struct {
	dir     string
	version string
	notes   string
}

func loadUpdateConfig() updateConfig {
	return updateConfig{
		dir:     getenv("UPDATE_DIR", "/srv/updates"),
		version: strings.TrimSpace(os.Getenv("UPDATE_VERSION")),
		notes:   os.Getenv("UPDATE_NOTES"),
	}
}

func (a *App) handleVersion(w http.ResponseWriter, r *http.Request) {
	if a.update.version == "" {
		writeJSON(w, 503, map[string]string{"error": "no release available"})
		return
	}
	exePath := filepath.Join(a.update.dir, "A4071-Tool.exe")
	shaPath := filepath.Join(a.update.dir, "A4071-Tool.exe.sha256")
	st, err := os.Stat(exePath)
	if err != nil {
		writeJSON(w, 503, map[string]string{"error": "no release available"})
		return
	}
	shaBytes, err := os.ReadFile(shaPath)
	if err != nil {
		writeJSON(w, 503, map[string]string{"error": "no release available"})
		return
	}
	writeJSON(w, 200, map[string]any{
		"latest": a.update.version,
		"notes":  a.update.notes,
		"sha256": strings.ToLower(strings.TrimSpace(string(shaBytes))),
		"size":   st.Size(),
	})
}
```

- [ ] **Step 2: Add `update` field to `App` and load config in `main`**

Modify `backend/main.go`:

Change the `App` struct (around line 24):

```go
type App struct {
	db        *pgxpool.Pool
	adminUser string
	adminPass string
	jwtSecret []byte
	update    updateConfig
}
```

Change the App construction in `main()` (around line 56):

```go
app := &App{
	db:        pool,
	adminUser: getenv("ADMIN_USERNAME", "admin"),
	adminPass: getenv("ADMIN_PASSWORD", "adminpass"),
	jwtSecret: []byte(getenv("JWT_SECRET", "dev_secret")),
	update:    loadUpdateConfig(),
}
```

- [ ] **Step 3: Wire the `/api/version` route**

In `backend/main.go`, find the existing `apiKeyAuth` routes (around line 88) and add the version route alongside:

```go
r.With(app.apiKeyAuth).Get("/api/verify", app.verifyKey)
r.With(app.apiKeyAuth).Get("/api/me", app.verifyKey)
r.With(app.apiKeyAuth).Get("/api/version", app.handleVersion)
```

- [ ] **Step 4: Manual smoke test — no release configured**

```bash
docker compose up --build -d backend
# create a key first (use admin UI or curl flow)
KEY=sk_xxx
curl -sS -H "X-API-Key: $KEY" http://localhost:4071/api/version
```

Expected: `{"error":"no release available"}` with status 503.

- [ ] **Step 5: Commit**

```bash
git add backend/update.go backend/main.go
git commit -m "feat(backend): add /api/version endpoint with env-driven release config"
```

---

## Task 2: Backend — `/api/download` handler

**Files:**
- Modify: `backend/update.go`
- Modify: `backend/main.go`

- [ ] **Step 1: Add the download handler to `backend/update.go`**

Append to `backend/update.go`:

```go
func (a *App) handleDownload(w http.ResponseWriter, r *http.Request) {
	if a.update.version == "" {
		writeJSON(w, 503, map[string]string{"error": "no release available"})
		return
	}
	exePath := filepath.Join(a.update.dir, "A4071-Tool.exe")
	f, err := os.Open(exePath)
	if err != nil {
		writeJSON(w, 503, map[string]string{"error": "no release available"})
		return
	}
	defer f.Close()
	st, err := f.Stat()
	if err != nil {
		writeJSON(w, 500, map[string]string{"error": "stat failed"})
		return
	}
	w.Header().Set("Content-Type", "application/octet-stream")
	w.Header().Set("Content-Disposition", `attachment; filename="A4071-Tool.exe"`)
	http.ServeContent(w, r, "A4071-Tool.exe", st.ModTime(), f)
}
```

Add the import to the top of `backend/update.go` if not already imported (the `update.go` from Task 1 should already have `net/http`, `os`, `path/filepath`, `strings`).

- [ ] **Step 2: Wire the route**

In `backend/main.go`, alongside the `/api/version` route added in Task 1:

```go
r.With(app.apiKeyAuth).Get("/api/version", app.handleVersion)
r.With(app.apiKeyAuth).Get("/api/download", app.handleDownload)
```

- [ ] **Step 3: Manual smoke — still no release**

```bash
docker compose restart backend
curl -sS -o /tmp/out.bin -w "%{http_code}\n" -H "X-API-Key: $KEY" http://localhost:4071/api/download
```

Expected: `503` printed.

- [ ] **Step 4: Commit**

```bash
git add backend/update.go backend/main.go
git commit -m "feat(backend): add /api/download endpoint streaming the release exe"
```

---

## Task 3: Backend — docker-compose env + volume

**Files:**
- Modify: `docker-compose.yml`

- [ ] **Step 1: Add env vars and bind mount to the backend service**

Modify the `backend` service in `docker-compose.yml`:

```yaml
  backend:
    build: ./backend
    container_name: a4071_backend
    restart: unless-stopped
    environment:
      DATABASE_URL: postgres://app:app_secret@postgres:5432/apikeys?sslmode=disable
      ADMIN_USERNAME: admin
      ADMIN_PASSWORD: adminpass
      JWT_SECRET: change_me_in_prod_super_secret_key
      PORT: "4071"
      UPDATE_DIR: /srv/updates
      UPDATE_VERSION: ""
      UPDATE_NOTES: ""
    volumes:
      - ./updates:/srv/updates:ro
    ports:
      - "4071:4071"
    depends_on:
      postgres:
        condition: service_healthy
```

- [ ] **Step 2: Create the `updates/` directory with a placeholder README**

```bash
mkdir updates
```

Create `updates/README.md`:

```markdown
# Release Drop Directory

Place release artifacts here:

- `A4071-Tool.exe` — the new binary to serve via `/api/download`.
- `A4071-Tool.exe.sha256` — single-line lowercase hex SHA-256 of the exe.

Then update `UPDATE_VERSION` and `UPDATE_NOTES` in `docker-compose.yml`
and run `docker compose up -d backend`.
```

- [ ] **Step 3: Update `.gitignore` so dropped checksums don't get committed**

The existing `.gitignore` already has `*.exe`, which covers
`updates/A4071-Tool.exe`. Append just the sha256 entry:

```
# Auto-update release drops (sha256 sidecar of UPDATE_DIR exe)
/updates/A4071-Tool.exe.sha256
```

- [ ] **Step 4: Manual smoke — drop a fake release and verify the endpoints**

```bash
echo "fake exe contents" > updates/A4071-Tool.exe
# sha256 of "fake exe contents\n" — compute fresh
sha256sum updates/A4071-Tool.exe | awk '{print $1}' > updates/A4071-Tool.exe.sha256

# Edit docker-compose.yml: UPDATE_VERSION: "0.2.0", UPDATE_NOTES: "Test release"
docker compose up -d backend

curl -sS -H "X-API-Key: $KEY" http://localhost:4071/api/version
# Expected: {"latest":"0.2.0","notes":"Test release","sha256":"...","size":18}

curl -sS -o /tmp/out.bin -H "X-API-Key: $KEY" http://localhost:4071/api/download
cat /tmp/out.bin
# Expected: "fake exe contents"
```

- [ ] **Step 5: Clean up the fake release artifacts before commit**

```bash
rm updates/A4071-Tool.exe updates/A4071-Tool.exe.sha256
```

- [ ] **Step 6: Commit**

```bash
git add docker-compose.yml updates/README.md .gitignore
git commit -m "feat(infra): bind-mount updates/ and add UPDATE_* env to backend"
```

---

## Task 4: Tool — `updater.py` skeleton with dataclasses and version compare

**Files:**
- Create: `tool/tools/updater.py`
- Create: `tool/tests/test_updater.py`

- [ ] **Step 1: Write the failing test for `parse_version` and `compare_versions`**

Create `tool/tests/test_updater.py`:

```python
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.updater import compare_versions, parse_version


class ParseVersionTests(unittest.TestCase):
    def test_three_parts(self) -> None:
        self.assertEqual(parse_version("0.1.0"), (0, 1, 0))

    def test_two_parts_pads_with_zero(self) -> None:
        self.assertEqual(parse_version("1.2"), (1, 2, 0))

    def test_one_part_pads_with_zeros(self) -> None:
        self.assertEqual(parse_version("3"), (3, 0, 0))

    def test_garbage_returns_none(self) -> None:
        self.assertIsNone(parse_version("abc"))
        self.assertIsNone(parse_version("1.x"))
        self.assertIsNone(parse_version(""))


class CompareVersionsTests(unittest.TestCase):
    def test_newer_minor(self) -> None:
        self.assertGreater(compare_versions("0.2.0", "0.1.0"), 0)

    def test_older_patch(self) -> None:
        self.assertLess(compare_versions("0.1.0", "0.1.1"), 0)

    def test_equal(self) -> None:
        self.assertEqual(compare_versions("0.1.0", "0.1.0"), 0)

    def test_unparseable_returns_zero(self) -> None:
        self.assertEqual(compare_versions("nope", "0.1.0"), 0)
        self.assertEqual(compare_versions("0.1.0", "nope"), 0)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd tool
python -m unittest tests.test_updater -v
```

Expected: ImportError — `tools.updater` does not exist yet.

- [ ] **Step 3: Create `tool/tools/updater.py` with the dataclasses and helpers**

```python
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
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
cd tool
python -m unittest tests.test_updater -v
```

Expected: all 8 tests pass.

- [ ] **Step 5: Commit**

```bash
git add tool/tools/updater.py tool/tests/test_updater.py
git commit -m "feat(tool): add updater module skeleton with semver compare"
```

---

## Task 5: Tool — `check_update` function

**Files:**
- Modify: `tool/tools/updater.py`

- [ ] **Step 1: Add the imports `check_update` needs**

At the top of `tool/tools/updater.py`, replace the existing import block with:

```python
from __future__ import annotations

import json
import socket
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Union
```

- [ ] **Step 2: Append `check_update` to `tool/tools/updater.py`**

```python
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
```

- [ ] **Step 3: Smoke test from a frozen-mode shim (no real PyInstaller build yet)**

Create a one-off helper file `tool/_smoke_check.py` (not committed):

```python
import sys
sys.frozen = True  # type: ignore[attr-defined]
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))

from tools.updater import check_update
print(check_update("invalid_key", "0.1.0"))
```

Run:

```bash
cd tool
python _smoke_check.py
```

Expected: `CheckSkipped(reason='unauthorized')` (the backend rejects an unknown API key with 401).

Then with a real key:

```bash
# Edit _smoke_check.py replacing "invalid_key" with a real $KEY
python _smoke_check.py
```

Expected: with `UPDATE_VERSION=""` on the server → `CheckSkipped(reason='bad_response')` (the 503 path). Drop a fake release like Task 3 Step 4 with `UPDATE_VERSION=0.0.5` → `UpToDate()`. Bump to `0.2.0` → `UpdateAvailable(...)`.

- [ ] **Step 4: Delete the smoke shim**

```bash
rm tool/_smoke_check.py
```

- [ ] **Step 5: Commit**

```bash
git add tool/tools/updater.py
git commit -m "feat(tool): add check_update against /api/version"
```

---

## Task 6: Tool — `download_update` function

**Files:**
- Modify: `tool/tools/updater.py`

- [ ] **Step 1: Add the needed imports**

At the top of `tool/tools/updater.py`, expand the import block to include:

```python
from __future__ import annotations

import hashlib
import json
import os
import socket
import sys
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Union
```

- [ ] **Step 2: Append `download_update` to `tool/tools/updater.py`**

```python
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
```

- [ ] **Step 3: Manual smoke — drive `download_update` from another smoke shim**

Create `tool/_smoke_download.py` (not committed):

```python
import sys
sys.frozen = True  # type: ignore[attr-defined]
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))

from tools.updater import check_update, download_update, UpdateAvailable

KEY = "sk_xxx"  # replace
result = check_update(KEY, "0.1.0")
print("check:", result)
if isinstance(result, UpdateAvailable):
    path = download_update(result, KEY, lambda d, t: print(f"\r{d}/{t}", end=""))
    print("\nsaved:", path)
```

Run with a real fake release on the backend (Task 3 Step 4 setup but `UPDATE_VERSION=0.2.0`):

```bash
cd tool
python _smoke_download.py
```

Expected: progress callbacks fire, prints `saved: ...A4071-Tool-update.exe`.

Now corrupt the server-side `A4071-Tool.exe.sha256` to a wrong hash and re-run:

Expected: raises `UpdateError("File tải về bị lỗi...")`. Restore the correct sha256 after.

- [ ] **Step 4: Delete the smoke shim**

```bash
rm tool/_smoke_download.py
```

- [ ] **Step 5: Commit**

```bash
git add tool/tools/updater.py
git commit -m "feat(tool): add SHA-256 verified download_update"
```

---

## Task 7: Tool — `_render_updater_bat` and `apply_update_and_exit`

**Files:**
- Modify: `tool/tools/updater.py`
- Modify: `tool/tests/test_updater.py`

- [ ] **Step 1: Add the failing test for `_render_updater_bat`**

Append to `tool/tests/test_updater.py`:

```python
from tools.updater import _render_updater_bat


class RenderUpdaterBatTests(unittest.TestCase):
    def test_substitutes_pid_and_paths(self) -> None:
        script = _render_updater_bat(
            pid=4242,
            new_exe=r"C:\Users\foo\AppData\Local\Temp\A4071-Tool-update.exe",
            current_exe=r"C:\Apps\A4071-Tool\A4071-Tool.exe",
        )
        self.assertIn('PID eq 4242', script)
        self.assertIn(r'"C:\Users\foo\AppData\Local\Temp\A4071-Tool-update.exe"', script)
        self.assertIn(r'"C:\Apps\A4071-Tool\A4071-Tool.exe"', script)
        self.assertIn("move /Y", script)
        self.assertIn("start \"\"", script)
        self.assertIn("del \"%~f0\"", script)

    def test_no_extra_quotes_or_braces(self) -> None:
        script = _render_updater_bat(
            pid=1,
            new_exe=r"C:\a.exe",
            current_exe=r"C:\b.exe",
        )
        self.assertNotIn("{PID}", script)
        self.assertNotIn("{NEW_EXE}", script)
        self.assertNotIn("{CURRENT_EXE}", script)
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd tool
python -m unittest tests.test_updater -v
```

Expected: ImportError on `_render_updater_bat`.

- [ ] **Step 3: Add `_render_updater_bat` and `apply_update_and_exit` to `tool/tools/updater.py`**

First, expand the imports:

```python
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
```

Then append at the end of the file:

```python
_UPDATER_BAT_TEMPLATE = r"""@echo off
setlocal
:wait
tasklist /FI "PID eq {PID}" 2>nul | find "{PID}" >nul
if not errorlevel 1 (
    timeout /t 1 /nobreak >nul
    goto wait
)
move /Y "{NEW_EXE}" "{CURRENT_EXE}" >nul
if errorlevel 1 (
    exit /b 1
)
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
        encoding="ascii",
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
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
cd tool
python -m unittest tests.test_updater -v
```

Expected: all tests including the two new ones pass.

- [ ] **Step 5: Commit**

```bash
git add tool/tools/updater.py tool/tests/test_updater.py
git commit -m "feat(tool): add bat-helper based apply_update_and_exit"
```

---

## Task 8: Tool — `UpdateDialog` UI

**Files:**
- Modify: `tool/tools/updater.py`

- [ ] **Step 1: Add Tkinter imports**

Expand the import block at the top of `tool/tools/updater.py`:

```python
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
```

- [ ] **Step 2: Append the dialog class to `tool/tools/updater.py`**

```python
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
            self._progress.configure(maximum=max(1, total), value=done)
            mb_done = done / (1024 * 1024)
            mb_total = total / (1024 * 1024)
            self._status.configure(text=f"{mb_done:.1f} / {mb_total:.1f} MB")
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

        def apply() -> None:
            try:
                apply_update_and_exit(path, self._current_exe)
            except OSError:
                self._show_error("Không khởi động được trình cập nhật.")
        self.after(0, apply)
```

- [ ] **Step 3: Run all unit tests to confirm nothing regressed**

```bash
cd tool
python -m unittest tests.test_updater -v
```

Expected: all tests still pass (the dialog has no unit tests; it's manually exercised in Task 10).

- [ ] **Step 4: Commit**

```bash
git add tool/tools/updater.py
git commit -m "feat(tool): add UpdateDialog Toplevel with prompt/progress/error views"
```

---

## Task 9: Tool — wire the check into `A4071App`

**Files:**
- Modify: `tool/a4071_tool.py`

- [ ] **Step 1: Add the updater import**

In `tool/a4071_tool.py`, after the existing imports from `tools.*`, add:

```python
from tools.updater import UpdateAvailable, UpdateDialog, check_update
```

- [ ] **Step 2: Track the API key on the app and trigger the check after main screen mounts**

Modify `_handle_bootstrap` in `tool/a4071_tool.py` (around line 119) so we remember the verified key:

```python
def _handle_bootstrap(self, cfg: dict, result) -> None:
    if isinstance(result, VerifyOk):
        if result.name and result.name != cfg.get("name"):
            save_config(cfg["api_key"], result.name)
        self._show_main(result.name or cfg.get("name", ""), cfg["api_key"])
    elif isinstance(result, VerifyInvalid):
        clear_config()
        self._show_login(initial_error="Key đã bị thu hồi. Đăng nhập lại.")
    elif isinstance(result, VerifyNetworkError):
        self._show_login(
            initial_error=f"Không kết nối được server. Thử lại. ({result.message})"
        )
```

Modify `_on_login_success` to pass the key through:

```python
def _on_login_success(self, api_key: str, name: str) -> None:
    save_config(api_key, name)
    self._show_main(name, api_key)
```

Change `_show_main` signature and add the post-mount update kick at the end of the method:

```python
def _show_main(self, name: str, api_key: str) -> None:
    self._api_key = api_key
    self._set_window(1020, 660, resizable=True)
    # ... existing body unchanged through self._register_tools() ...
    if self._pages:
        self.show_page(next(iter(self._pages)))
    self.after(500, self._kick_update_check)
```

(Keep all existing UI construction in `_show_main` between `self._set_window(...)` and `if self._pages: ...`.)

Add `self._api_key: str | None = None` to `__init__`.

- [ ] **Step 3: Add `_kick_update_check` and `_handle_check_result`**

Add these methods to `A4071App`:

```python
def _kick_update_check(self) -> None:
    if not self._api_key:
        return
    key = self._api_key

    def worker() -> None:
        result = check_update(key, APP_VERSION)
        self.after(0, lambda: self._handle_check_result(result))

    threading.Thread(target=worker, daemon=True).start()

def _handle_check_result(self, result) -> None:
    if not isinstance(result, UpdateAvailable):
        return
    current_exe = Path(sys.executable)
    UpdateDialog(
        self,
        info=result,
        api_key=self._api_key or "",
        current_version=APP_VERSION,
        current_exe=current_exe,
    )
```

- [ ] **Step 4: Manual smoke — dev mode**

```bash
cd tool
python a4071_tool.py
```

Log in with a valid key. Expected: main screen loads as before. No update dialog (because `sys.frozen` is false). No crash.

- [ ] **Step 5: Commit**

```bash
git add tool/a4071_tool.py
git commit -m "feat(tool): trigger update check after main screen mounts"
```

---

## Task 10: End-to-end build + manual verification

**Files:**
- None modified — this task is a build + smoke flow.

- [ ] **Step 1: Build a v0.1.0 baseline**

Confirm `APP_VERSION = "0.1.0"` in `tool/a4071_tool.py`. Then:

```cmd
cd tool
build.bat
```

Copy `dist\A4071-Tool.exe` to a sandbox directory, e.g.
`C:\Sandbox\A4071-Tool\`, along with `dist\ffmpeg.exe` and `dist\cuda\`.

- [ ] **Step 2: Bump source to v0.2.0 and build again**

Edit `tool/a4071_tool.py`: `APP_VERSION = "0.2.0"`. Re-run `build.bat`.
Take the resulting `dist\A4071-Tool.exe` and copy it to your backend's
`updates\A4071-Tool.exe`. Compute and write the sha256:

```powershell
(Get-FileHash updates\A4071-Tool.exe -Algorithm SHA256).Hash.ToLower() | Out-File -Encoding ascii -NoNewline updates\A4071-Tool.exe.sha256
```

Update `docker-compose.yml`:

```yaml
UPDATE_VERSION: "0.2.0"
UPDATE_NOTES: "- Tự động cập nhật\n- Sửa lỗi nhỏ"
```

Restart the backend: `docker compose up -d backend`.

Revert `APP_VERSION` in source back to `0.1.0` (we want the deployed v0.1.0 sandbox build to see v0.2.0 as new).

- [ ] **Step 3: Launch the v0.1.0 sandbox build**

Run `C:\Sandbox\A4071-Tool\A4071-Tool.exe`. Log in.

Expected:
1. Main screen appears.
2. ~0.5 s later the update dialog appears showing version 0.2.0 and the release notes.
3. Sidebar still reads `v0.1.0`.

- [ ] **Step 4: Click "Để sau"**

Expected: dialog dismisses, app fully usable, sidebar reads `v0.1.0`. Re-launch — dialog appears again.

- [ ] **Step 5: Click "Cập nhật ngay"**

Expected: progress bar advances, dialog window cannot be closed, MB counter updates. On completion app exits. Within ~2 seconds a new window appears with sidebar reading `v0.2.0`. `A4071-Tool.exe` on disk now has the new SHA-256.

- [ ] **Step 6: Corrupt-checksum test**

Edit `updates\A4071-Tool.exe.sha256` to a wrong value, restart backend, set source `APP_VERSION` back so sandbox sees v0.2.0 as newer (or just bump `UPDATE_VERSION` to `0.3.0`). Launch sandbox, click "Cập nhật ngay".

Expected: progress completes, then error view: "File tải về bị lỗi. Vui lòng thử lại." `[Đóng]` button enabled. App still on old version. Fix the sha256 after this test.

- [ ] **Step 7: Network drop test**

Stop the backend mid-download: `docker compose stop backend` while the progress bar is running.

Expected: error view "Tải bản cập nhật thất bại. Kiểm tra kết nối." App still usable after closing the error.

- [ ] **Step 8: Backend down at startup**

Stop backend. Launch sandbox build (with a previously cached valid key).

Expected: main screen loads as if everything is fine; no dialog appears. Console log (if any) shows the silent skip.

- [ ] **Step 9: No commit needed for this task**

This is a verification task. If any step fails, file a follow-up task and re-enter the relevant earlier task with the bug fix.
