# A4071-Tool API Key Authentication — Design

## Overview

Add an authentication gate to the A4071 desktop tool. The tool must not
expose any feature until the user has presented an API key that the
backend confirms as valid via `GET /api/verify`.

The key, once verified, is persisted on disk so subsequent launches
re-verify automatically. A "Logout" action removes the saved key.

## Goals

- Block access to all tool pages until verification succeeds.
- Re-verify online on every launch so revoked keys lose access immediately.
- Avoid asking the user to paste the key on every launch.
- No new third-party Python dependencies (stick to the standard library).

## Non-goals

- Encrypting the stored API key (plaintext in `config.json` is acceptable
  for the current scope; DPAPI can be added later if required).
- Offline grace periods. If the server is unreachable, the user is blocked.
- Username/password login. The single secret is the API key.

## Backend contract (already in place)

- `GET http://a4071-tool.j4m.dev:4071/api/verify`
- Header: `X-API-Key: sk_xxx`
- Responses:
  - `200 {"status":"ok","id":<int>,"name":"<key name>"}`
  - `401` invalid / unknown key
  - Any network error → treat as "server unreachable"

## Architecture

A new module `tool/tools/auth.py` owns API verification and on-disk
config. `A4071App` becomes a screen switcher between `LoginScreen`
(when unauthenticated) and the existing main UI (when authenticated).

```
tool/
  a4071_tool.py        # App shell: orchestrates Login <-> Main, header w/ logout
  tools/
    auth.py            # NEW: verify_key(), load/save/clear_config(), API_BASE
    base.py            # unchanged
    mp3_merger.py      # unchanged
```

### auth.py

```python
API_BASE = "http://a4071-tool.j4m.dev:4071"
VERIFY_PATH = "/api/verify"
TIMEOUT_SEC = 5

@dataclass
class VerifyOk:
    name: str

@dataclass
class VerifyInvalid: ...

@dataclass
class VerifyNetworkError:
    message: str

VerifyResult = VerifyOk | VerifyInvalid | VerifyNetworkError

def verify_key(api_key: str) -> VerifyResult: ...
def config_path() -> Path: ...        # %APPDATA%/A4071-Tool/config.json (Windows)
                                       # ~/.a4071-tool/config.json (fallback)
def load_config() -> dict | None: ...
def save_config(api_key: str, name: str) -> None: ...
def clear_config() -> None: ...
```

`verify_key` uses `urllib.request` with a 5 s timeout. Any non-200/401
response, socket error, or timeout maps to `VerifyNetworkError`.

### A4071App changes

`__init__` no longer builds the full UI directly. Instead it:

1. Builds the empty root container.
2. Calls `_bootstrap()` which:
   - Loads `config.json`.
   - If a key is present: shows a transient "Đang xác thực…" overlay and
     calls `verify_key` on a worker thread; on completion, either swaps
     to the main screen or shows the login screen with an inline error.
   - If no key: shows the login screen immediately.

New methods:

- `_show_login(error: str | None = None)` — destroys current screen
  frames, mounts `LoginScreen`.
- `_show_main(name: str)` — destroys login, builds sidebar + content +
  header (with key name + Logout button), then re-runs `_register_tools`.
- `_on_logout()` — `clear_config()`, then `_show_login()`.

### LoginScreen

Tkinter Frame, centered card on the existing light background.

Widgets (top to bottom inside the card):

- Title `A4071-Tool` (Segoe UI Semibold 18, dark)
- Subtitle `Nhập API Key để tiếp tục` (Segoe UI 10, muted)
- Entry, `show="*"`, placeholder `sk_...`
- Checkbox `Hiện key` — toggles `show=""` / `show="*"`
- Primary button `Đăng nhập` (full width)
- Error label (red, empty until set)

Behavior:

- Enter key in the Entry triggers submit.
- On submit:
  - Strip whitespace; if empty, show "Nhập API key trước."
  - Disable button + Entry, set button text "Đang kiểm tra…".
  - Run `verify_key` on a worker thread; marshal the result back to the
    Tk main thread via `self.after(0, ...)`.
  - On `VerifyOk`: `save_config(key, name)` then app `_show_main(name)`.
  - On `VerifyInvalid`: re-enable widgets, show "API key không hợp lệ."
  - On `VerifyNetworkError`: re-enable widgets, show
    "Không kết nối được server. Thử lại."

### Main screen changes

- Header right side (next to the existing title/description):
  - Muted label showing the key `name` (Segoe UI 9).
  - Plain text button `Đăng xuất` that calls `_on_logout`.
- No other change to sidebar/tool registration logic.

## Data flow

```
launch -> load_config()
            |
            +-- None ----------------------------> LoginScreen
            |
            +-- {api_key, name} -> verify_key()
                                       |
                                       +-- VerifyOk        -> save_config + MainScreen
                                       +-- VerifyInvalid   -> clear_config + LoginScreen("Key đã bị thu hồi")
                                       +-- NetworkError    -> LoginScreen("Không kết nối được server. Thử lại.")
                                          (config kept; user can retry)

LoginScreen submit -> verify_key() -> same three outcomes as above,
                                       starting from no saved config.

MainScreen logout -> clear_config -> LoginScreen
```

## Threading model

Tkinter is single-threaded. Network calls run on `threading.Thread`
workers; results are delivered back to the UI with `widget.after(0, cb)`.
No `tkinter` widget is touched from the worker thread.

## Storage layout

`%APPDATA%/A4071-Tool/config.json` (Windows) or
`~/.a4071-tool/config.json` (fallback):

```json
{
  "api_key": "sk_xxx",
  "name": "Display name from /api/verify"
}
```

`save_config` creates the directory if it does not exist and writes the
file with mode `0o600` on POSIX (best-effort on Windows).

## Error handling

| Condition                          | UX                                                           |
|------------------------------------|--------------------------------------------------------------|
| No saved key                       | Login screen, no error.                                      |
| Saved key + 200 ok                 | Skip directly to main screen.                                |
| Saved key + 401                    | Clear stored key. Login screen with "Key đã bị thu hồi."     |
| Saved key + network error          | Keep stored key. Login screen with retry message.            |
| Login submit + 200 ok              | Save key, enter main screen.                                 |
| Login submit + 401                 | Inline error "API key không hợp lệ."                         |
| Login submit + network error      | Inline error "Không kết nối được server. Thử lại."           |
| Login submit with empty entry      | Inline error "Nhập API key trước."                           |
| Malformed JSON in saved config     | Treat as no saved key; overwrite on next successful login.   |

## Testing strategy

Manual:

1. Fresh install (no config.json) — login screen appears, invalid key
   shows error, valid key transitions to main.
2. Re-launch — auto-enters main with no UI flash beyond the brief
   "Đang xác thực…" indicator.
3. Logout — returns to login, config.json removed.
4. Revoke key on backend, re-launch — pushed back to login with
   "Key đã bị thu hồi."
5. Stop backend, re-launch — login screen with network-error message,
   config.json retained.
6. Stop backend, log in from scratch — inline network-error message.

No automated tests are added in this iteration (matching the rest of the
tool, which has none).

## Out of scope / future work

- DPAPI encryption of stored key on Windows.
- Periodic re-verification while the app is running.
- A "change server" UI for advanced users.
- Auto-update of cached `name` if backend renames the key.
