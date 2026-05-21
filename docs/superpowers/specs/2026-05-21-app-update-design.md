# A4071-Tool Auto-Update — Design

## Overview

Add an in-app auto-update mechanism to the A4071 desktop tool. On every
launch (after API key verification succeeds), the app checks the backend
for a newer version. If one exists, it prompts the user; if they accept,
the app downloads the new binary, exits, and a small helper script
overwrites the running executable on disk and relaunches the app.

The update channel is gated by the user's API key — only authenticated
users receive update metadata and downloads.

## Goals

- Every launch checks `latest` version against bundled `APP_VERSION`.
- User chooses: install now, or skip and keep using the current build.
- Replace only `A4071-Tool.exe`. Leave `ffmpeg.exe` and `cuda\*.dll`
  alone.
- Verify download integrity (SHA-256) before replacing the binary.
- No new third-party Python dependencies (stick to stdlib).
- Reuse the existing backend (no new infra, no DB).

## Non-goals

- Background or scheduled updates while the app is running. Check only
  on launch.
- Delta / incremental patching.
- Multi-channel releases (stable/beta). One channel.
- Rollback UI. If a release is bad, admin replaces the file on the
  backend and the next launch picks it up.
- Updating `ffmpeg.exe`, CUDA DLLs, or bundled models.
- Code signing (separate workstream; the SHA-256 check covers integrity
  but not authorship).

## Backend contract (new)

Two new endpoints on the existing Go backend, both behind the existing
`apiKeyAuth` middleware (`X-API-Key` header).

### `GET /api/version`

Returns the latest available version metadata.

```json
{
  "latest": "0.2.0",
  "notes": "- Cập nhật model whisper\n- Sửa lỗi merge MP3",
  "sha256": "abc123...64-hex-chars",
  "size": 47185920
}
```

`401` if the API key is missing/invalid. The app treats anything else
(network error, 5xx, malformed JSON) as "no update available" and
continues silently.

### `GET /api/download`

Streams the latest `A4071-Tool.exe` binary with
`Content-Type: application/octet-stream` and `Content-Length` set.

`401` if the API key is missing/invalid.

### Server-side configuration

No new database tables. The backend reads three env vars on startup:

- `UPDATE_DIR` — filesystem path, default `/srv/updates`.
- `UPDATE_VERSION` — semver string, e.g. `0.2.0`. The version the
  backend currently advertises.
- `UPDATE_NOTES` — optional human-readable release notes (newline-
  separated).

`UPDATE_DIR/A4071-Tool.exe` is the file `/api/download` serves.
`UPDATE_DIR/A4071-Tool.exe.sha256` (single line, lowercase hex) is what
`/api/version` reports as `sha256`. If either file is missing, both
endpoints return `503 {"error":"no release available"}`.

Release workflow for an admin: copy the new exe into `UPDATE_DIR`, write
its SHA-256 alongside, update the two env vars in `docker-compose.yml`,
`docker compose up -d backend`.

## Architecture

A new module `tool/tools/updater.py` owns version checking, download,
integrity verification, and helper-script generation. `A4071App` calls
into it after the user reaches the main screen for the first time.

```
tool/
  a4071_tool.py        # adds update check after _show_main
  tools/
    updater.py         # NEW: check + download + apply helpers
    auth.py            # unchanged
    base.py            # unchanged
    mp3_merger.py      # unchanged
    mp3_to_srt.py      # unchanged
```

### updater.py — public API

```python
APP_VERSION = "0.1.0"  # imported from a4071_tool

@dataclass(frozen=True)
class UpdateAvailable:
    latest: str
    notes: str
    sha256: str
    size: int
    download_url: str   # built by check_update from API_BASE + "/api/download"

@dataclass(frozen=True)
class UpToDate: ...

@dataclass(frozen=True)
class CheckSkipped:
    reason: str   # "not_frozen" | "network_error" | "bad_response"

CheckResult = UpdateAvailable | UpToDate | CheckSkipped

def check_update(api_key: str, current_version: str) -> CheckResult: ...

def download_update(
    info: UpdateAvailable,
    api_key: str,
    on_progress: Callable[[int, int], None],   # (bytes_done, total)
) -> Path:                                      # returns temp .exe path
    ...

def apply_update_and_exit(new_exe: Path, current_exe: Path) -> None:
    """Writes the updater .bat, spawns it detached, then sys.exit(0)."""
```

`check_update` returns `CheckSkipped("not_frozen")` immediately when
`sys.frozen` is false, so developers running `python a4071_tool.py`
never see the update prompt.

Semver comparison: a tiny local helper parses `MAJOR.MINOR.PATCH` into
a tuple of ints and compares lexicographically. Anything that doesn't
parse → treated as equal (no update).

### Threading model

`check_update` and `download_update` run on `threading.Thread` workers
created by the UI. Progress callbacks marshal back to Tkinter via
`widget.after(0, cb)`. No widget is touched from a worker thread. This
matches the pattern already used by `auth.py`.

### A4071App changes

1. After `_show_main(name)` finishes, schedule a one-shot check:
   ```python
   self.after(500, self._kick_update_check)
   ```
   The 500 ms delay lets the main UI paint first so the user sees the
   app before any dialog.

2. `_kick_update_check` spawns a worker thread calling
   `check_update(api_key, APP_VERSION)`. On the UI thread:
   - `UpToDate` / `CheckSkipped` → do nothing.
   - `UpdateAvailable` → call `_prompt_update(info)`.

3. `_prompt_update(info)` shows a modal Tk dialog (custom Toplevel, not
   `messagebox`, so the release notes render multi-line):
   - Title: `Cập nhật A4071-Tool`
   - Body: `Đã có phiên bản {info.latest} (hiện tại {APP_VERSION}).`
   - Notes box (read-only Text, scrollable, max 8 lines visible).
   - Buttons: `[Cập nhật ngay]`  `[Để sau]`
   - Closing the dialog (X) = "Để sau".
   - "Để sau" → dismiss; nothing more this session.
   - "Cập nhật ngay" → swap dialog content to progress mode.

4. Progress mode (same Toplevel, content swapped):
   - Label `Đang tải bản {info.latest}…`
   - `ttk.Progressbar` (determinate, 0..info.size).
   - Sub-label showing `{done_mb:.1f} / {total_mb:.1f} MB`.
   - No buttons. Closing the window is blocked
     (`protocol("WM_DELETE_WINDOW", lambda: None)`).
   - On completion: `apply_update_and_exit(...)`.
   - On error (network, SHA-256 mismatch, disk): swap to error mode
     with `[Đóng]`, and re-enable the close button.

### Download flow

```python
def download_update(info, api_key, on_progress):
    tmp_dir = Path(tempfile.gettempdir())
    part = tmp_dir / "A4071-Tool-update.exe.part"
    final = tmp_dir / "A4071-Tool-update.exe"
    part.unlink(missing_ok=True); final.unlink(missing_ok=True)

    req = urllib.request.Request(info.download_url,
                                 headers={"X-API-Key": api_key})
    with urllib.request.urlopen(req, timeout=15) as resp:
        total = int(resp.headers.get("Content-Length") or info.size)
        h = hashlib.sha256()
        done = 0
        with open(part, "wb") as f:
            while True:
                chunk = resp.read(64 * 1024)
                if not chunk: break
                f.write(chunk); h.update(chunk)
                done += len(chunk)
                on_progress(done, total)

    if h.hexdigest().lower() != info.sha256.lower():
        part.unlink(missing_ok=True)
        raise UpdateError("Checksum mismatch")

    os.replace(part, final)
    return final
```

### Apply step — the updater .bat

`apply_update_and_exit` writes a single-shot batch script and spawns it
detached, then calls `sys.exit(0)`.

```bat
@echo off
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
```

Substitutions:

- `{PID}` — `os.getpid()` of the running app at the moment we generate
  the .bat.
- `{NEW_EXE}` — absolute path of the downloaded file.
- `{CURRENT_EXE}` — `Path(sys.executable)` (the running .exe).

Spawn:

```python
subprocess.Popen(
    ["cmd", "/c", str(bat_path)],
    creationflags=subprocess.DETACHED_PROCESS
                | subprocess.CREATE_NEW_PROCESS_GROUP
                | subprocess.CREATE_NO_WINDOW,
    close_fds=True,
    cwd=str(bat_path.parent),
)
sys.exit(0)
```

The app's existing `_on_close` is bypassed here — we call `sys.exit(0)`
directly after Popen, which terminates the Python interpreter and frees
the file lock on the .exe within ~1 s. The `tasklist` loop in the .bat
polls every second until the PID is gone, then does the swap.

The trailing `(goto) 2>nul & del "%~f0"` is the standard idiom for a
.bat that deletes itself on exit.

## Data flow

```
launch
  -> auth (existing) -> _show_main()
                          -> after 500ms: _kick_update_check()
                                            |
                                            +-- CheckSkipped -> noop
                                            +-- UpToDate     -> noop
                                            +-- UpdateAvailable
                                                  -> _prompt_update()
                                                       |
                                                       +-- [Để sau]      -> noop (this session)
                                                       +-- [Cập nhật ngay]
                                                             -> download (worker)
                                                                  |
                                                                  +-- error  -> error dialog
                                                                  +-- ok     -> write .bat
                                                                                 -> Popen detached
                                                                                 -> sys.exit(0)
                                                                                 ----- .bat side -----
                                                                                 wait for PID exit
                                                                                 -> move new over old
                                                                                 -> start new exe
                                                                                 -> delete .bat
```

## Error handling

| Condition                                       | UX                                                       |
|-------------------------------------------------|----------------------------------------------------------|
| `sys.frozen` is false (dev run)                 | Skip silently.                                           |
| `/api/version` 401                              | Skip silently (token revoked path handles itself).       |
| `/api/version` network error / 5xx / bad JSON   | Skip silently. Log to stderr if console build.           |
| `latest <= APP_VERSION`                         | No dialog.                                               |
| Download fails (network, 4xx, 5xx)              | Error mode: "Tải bản cập nhật thất bại. Thử lại sau."    |
| SHA-256 mismatch                                | Error mode: "File tải về bị lỗi. Vui lòng thử lại."      |
| Disk full / cannot write to %TEMP%              | Error mode: "Không ghi được file tạm."                   |
| `move /Y` in .bat fails (rare, AV lock)         | App relaunches old version; user sees the prompt again.  |
| User closes app while download in progress      | Window-close handler blocks during download.             |

The check is best-effort: any failure path that isn't the happy
"update available, user clicks update" silently falls through to the
normal app. We never block startup on the update server.

## Storage / disk layout

| Path                                           | Lifetime                                  |
|------------------------------------------------|-------------------------------------------|
| `%TEMP%\A4071-Tool-update.exe.part`            | During download. Deleted on completion or error. |
| `%TEMP%\A4071-Tool-update.exe`                 | Verified download. Consumed (moved) by the .bat. |
| `%TEMP%\a4071-update-<pid>.bat`                | Self-deletes after the move + relaunch.   |
| The `dist\A4071-Tool.exe` itself               | Replaced atomically by `move /Y`.         |

No long-lived state on disk. Nothing in `%APPDATA%` changes for the
update feature.

## Version handling

`APP_VERSION` stays defined in `a4071_tool.py` as today (`"0.1.0"`).
`updater.py` imports it (or accepts it as a parameter from the caller —
see API above) so the bundled version is always the one stamped into
the PyInstaller-built exe.

For dev mode (`python a4071_tool.py`), `sys.frozen` is false, the check
is skipped, and the displayed version in the sidebar remains
`APP_VERSION`.

## Testing strategy

Manual, given the rest of the tool has no automated tests:

1. **Dev mode skip** — Run `python a4071_tool.py`. No update dialog
   should appear even if backend advertises a newer version.
2. **No update** — Build, backend `UPDATE_VERSION=0.1.0`. Launch. No
   dialog. Sidebar still says `v0.1.0`.
3. **Update available, declined** — Backend `UPDATE_VERSION=0.2.0`.
   Launch. Dialog appears with notes. Click "Để sau". Dialog dismisses;
   app fully usable. Re-launching prompts again.
4. **Update available, accepted, happy path** — Same setup, click
   "Cập nhật ngay". Progress bar advances. App exits. Within ~2 s the
   new exe launches; sidebar now reads `v0.2.0`.
5. **Checksum mismatch** — Corrupt the file on the server, re-launch +
   accept. After download finishes, error dialog with "File tải về bị
   lỗi". App still runnable after closing the dialog.
6. **Network drop mid-download** — Disconnect during download. Error
   dialog with "Tải bản cập nhật thất bại". Old version intact.
7. **Backend down at startup** — Stop backend. Launch app (with a
   cached valid key). Update check silently fails; main UI loads
   normally.
8. **401 from /api/version** — Revoke key on backend between auth
   verify and update check (race). Update check silently fails; main UI
   loads normally.

## Out of scope / future work

- Background re-check after N hours.
- Beta/canary channel selection.
- Code-signing the exe (and validating signature in addition to SHA-256).
- Updating `ffmpeg.exe` or CUDA DLLs.
- Showing a "What's new" panel after a successful update.
- "Remind me later" with a real timer (current "Để sau" = "skip until
  next launch").
- Self-healing if the .bat fails to relaunch (e.g., a registry-based
  RunOnce fallback).
