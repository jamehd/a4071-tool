# A4071-Tool API Key Auth Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Gate the A4071 desktop tool behind a login screen that verifies an API key against `http://a4071-tool.j4m.dev:4071/api/verify` and persists the verified key locally for subsequent launches.

**Architecture:** A new `tool/tools/auth.py` owns the HTTP verification call and config storage. A new `tool/login_screen.py` provides the login UI. `tool/a4071_tool.py` becomes a screen switcher: bootstrap verifies any stored key, then mounts either the login screen or the existing main UI (sidebar + tool pages) with a Logout action in the header.

**Tech Stack:** Python 3.10+, Tkinter, `urllib.request` (stdlib), `threading` for non-blocking network calls. No new third-party deps.

**Spec:** [`docs/superpowers/specs/2026-05-20-tool-api-key-auth-design.md`](../specs/2026-05-20-tool-api-key-auth-design.md)

**Testing note:** The tool has no automated test harness. Each task ends with a quick manual smoke check in lieu of unit tests, matching the spec.

---

## File Structure

- Create `tool/tools/auth.py` — API client + config persistence (stdlib only).
- Create `tool/login_screen.py` — `LoginScreen(tk.Frame)` widget.
- Modify `tool/a4071_tool.py` — replace direct UI build with screen switcher; add logout button + key-name label in header.

The login screen lives at `tool/login_screen.py` (not `tool/tools/`) because `tool/tools/` is reserved for feature pages registered into the sidebar.

---

## Task 1: auth.py — API client and config storage

**Files:**
- Create: `tool/tools/auth.py`

- [ ] **Step 1: Create the file**

Write `tool/tools/auth.py`:

```python
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
```

- [ ] **Step 2: Smoke check the module**

Run from `tool/`:

```bash
python -c "from tools.auth import verify_key, load_config, save_config, clear_config, config_path; print(config_path()); print(verify_key('sk_not_a_real_key'))"
```

Expected:
- A path like `C:\Users\<user>\AppData\Roaming\A4071-Tool\config.json`
- A `VerifyInvalid()` or `VerifyNetworkError(...)` (depending on whether the server is reachable). Either result proves the module imports cleanly and runs.

- [ ] **Step 3: Commit**

```bash
git add tool/tools/auth.py
git commit -m "Add auth module for tool API key verification and config storage"
```

---

## Task 2: LoginScreen widget

**Files:**
- Create: `tool/login_screen.py`

- [ ] **Step 1: Create the file**

Write `tool/login_screen.py`:

```python
from __future__ import annotations

import threading
import tkinter as tk
from tkinter import ttk
from typing import Callable

from tools.auth import (
    VerifyInvalid,
    VerifyNetworkError,
    VerifyOk,
    verify_key,
)


CARD_BG = "#ffffff"
PAGE_BG = "#f8fafc"
TITLE_FG = "#111827"
MUTED_FG = "#6b7280"
ERROR_FG = "#b91c1c"
PRIMARY_BG = "#2563eb"
PRIMARY_FG = "#ffffff"
PRIMARY_HOVER = "#1d4ed8"
PRIMARY_DISABLED = "#93c5fd"
BORDER = "#e5e7eb"


class LoginScreen(tk.Frame):
    def __init__(
        self,
        parent: tk.Misc,
        on_success: Callable[[str, str], None],
        initial_error: str | None = None,
    ) -> None:
        super().__init__(parent, bg=PAGE_BG)
        self._on_success = on_success
        self._busy = False

        card = tk.Frame(self, bg=CARD_BG, highlightthickness=1, highlightbackground=BORDER)
        card.place(relx=0.5, rely=0.5, anchor="center", width=380)

        tk.Label(
            card, text="A4071-Tool", bg=CARD_BG, fg=TITLE_FG,
            font=("Segoe UI Semibold", 18), pady=4,
        ).pack(pady=(28, 4))
        tk.Label(
            card, text="Nhập API Key để tiếp tục", bg=CARD_BG, fg=MUTED_FG,
            font=("Segoe UI", 10),
        ).pack(pady=(0, 18))

        self._key_var = tk.StringVar()
        self._entry = ttk.Entry(
            card, textvariable=self._key_var, show="*", font=("Segoe UI", 10),
        )
        self._entry.pack(fill="x", padx=24)
        self._entry.bind("<Return>", lambda _e: self._submit())

        self._show_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            card, text="Hiện key", variable=self._show_var,
            command=self._toggle_show,
        ).pack(anchor="w", padx=24, pady=(6, 18))

        self._button = tk.Button(
            card, text="Đăng nhập", command=self._submit,
            bg=PRIMARY_BG, fg=PRIMARY_FG, activebackground=PRIMARY_HOVER,
            activeforeground=PRIMARY_FG, relief="flat",
            font=("Segoe UI Semibold", 10), pady=8, cursor="hand2",
            borderwidth=0,
        )
        self._button.pack(fill="x", padx=24)

        self._error = tk.Label(
            card, text=initial_error or "", bg=CARD_BG, fg=ERROR_FG,
            font=("Segoe UI", 9), wraplength=320, justify="left",
        )
        self._error.pack(fill="x", padx=24, pady=(12, 24))

        self.after(50, self._entry.focus_set)

    def _toggle_show(self) -> None:
        self._entry.configure(show="" if self._show_var.get() else "*")

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        state = "disabled" if busy else "normal"
        self._entry.configure(state=state)
        if busy:
            self._button.configure(
                text="Đang kiểm tra…", state="disabled",
                bg=PRIMARY_DISABLED, cursor="arrow",
            )
        else:
            self._button.configure(
                text="Đăng nhập", state="normal",
                bg=PRIMARY_BG, cursor="hand2",
            )

    def _set_error(self, message: str) -> None:
        self._error.configure(text=message)

    def _submit(self) -> None:
        if self._busy:
            return
        key = self._key_var.get().strip()
        if not key:
            self._set_error("Nhập API key trước.")
            return
        self._set_error("")
        self._set_busy(True)

        def worker() -> None:
            result = verify_key(key)
            self.after(0, lambda: self._handle_result(key, result))

        threading.Thread(target=worker, daemon=True).start()

    def _handle_result(self, key: str, result) -> None:
        if isinstance(result, VerifyOk):
            self._on_success(key, result.name)
            return
        self._set_busy(False)
        if isinstance(result, VerifyInvalid):
            self._set_error("API key không hợp lệ.")
        elif isinstance(result, VerifyNetworkError):
            self._set_error(f"Không kết nối được server. Thử lại. ({result.message})")
```

- [ ] **Step 2: Quick visual check**

Create a throwaway `tool/_login_preview.py` temporarily:

```python
import tkinter as tk
from login_screen import LoginScreen

root = tk.Tk()
root.geometry("1020x660")
LoginScreen(root, on_success=lambda k, n: print("ok", n)).pack(fill="both", expand=True)
root.mainloop()
```

Run `python _login_preview.py` from `tool/`. Confirm: card is centered, entry hides characters, "Hiện key" toggles visibility, empty submit shows "Nhập API key trước.", invalid key shows error after a brief "Đang kiểm tra…".

Delete `_login_preview.py` before committing.

- [ ] **Step 3: Commit**

```bash
git add tool/login_screen.py
git commit -m "Add LoginScreen widget for tool auth gate"
```

---

## Task 3: Refactor A4071App for screen switching + logout

**Files:**
- Modify: `tool/a4071_tool.py` (entire file)

- [ ] **Step 1: Rewrite `tool/a4071_tool.py`**

Replace the file's contents with:

```python
from __future__ import annotations

import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import ttk

from login_screen import LoginScreen
from tools.auth import (
    VerifyInvalid,
    VerifyNetworkError,
    VerifyOk,
    clear_config,
    load_config,
    save_config,
    verify_key,
)
from tools.base import ToolPage
from tools.mp3_merger import MP3MergerPage


APP_TITLE = "A4071-Tool"
APP_VERSION = "0.1.0"

SIDEBAR_BG = "#1f2937"
SIDEBAR_FG = "#e5e7eb"
SIDEBAR_HOVER = "#374151"
SIDEBAR_ACTIVE = "#2563eb"
SIDEBAR_DIVIDER = "#374151"
SIDEBAR_BRAND_FG = "#ffffff"
SIDEBAR_MUTED_FG = "#9ca3af"

CONTENT_BG = "#f8fafc"
HEADER_BG = "#ffffff"
HEADER_DIVIDER = "#e5e7eb"
HEADER_FG = "#111827"
HEADER_MUTED_FG = "#6b7280"
HEADER_LINK_FG = "#2563eb"


def app_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent


class SidebarItem(tk.Frame):
    def __init__(self, parent: tk.Misc, label: str, on_click) -> None:
        super().__init__(parent, bg=SIDEBAR_BG, cursor="hand2")
        self._label = tk.Label(
            self, text=label, bg=SIDEBAR_BG, fg=SIDEBAR_FG,
            anchor="w", padx=20, pady=11, font=("Segoe UI", 10),
        )
        self._label.pack(fill="x")
        self._on_click = on_click
        self._active = False
        for widget in (self, self._label):
            widget.bind("<Button-1>", self._click)
            widget.bind("<Enter>", self._enter)
            widget.bind("<Leave>", self._leave)

    def _click(self, _evt) -> None:
        self._on_click()

    def _enter(self, _evt) -> None:
        if not self._active:
            self._set_bg(SIDEBAR_HOVER)

    def _leave(self, _evt) -> None:
        if not self._active:
            self._set_bg(SIDEBAR_BG)

    def _set_bg(self, color: str) -> None:
        self.configure(bg=color)
        self._label.configure(bg=color)

    def set_active(self, active: bool) -> None:
        self._active = active
        self._set_bg(SIDEBAR_ACTIVE if active else SIDEBAR_BG)


class A4071App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("1020x660")
        self.minsize(860, 560)
        self.configure(bg=CONTENT_BG)

        self._screen: tk.Frame | None = None
        self._pages: dict[str, ToolPage] = {}
        self._sidebar_items: dict[str, SidebarItem] = {}
        self._active_key: str | None = None

        self.after(0, self._bootstrap)

    def _bootstrap(self) -> None:
        cfg = load_config()
        if not cfg:
            self._show_login()
            return
        self._show_verifying()

        def worker() -> None:
            result = verify_key(cfg["api_key"])
            self.after(0, lambda: self._handle_bootstrap(cfg, result))

        threading.Thread(target=worker, daemon=True).start()

    def _handle_bootstrap(self, cfg: dict, result) -> None:
        if isinstance(result, VerifyOk):
            if result.name and result.name != cfg.get("name"):
                save_config(cfg["api_key"], result.name)
            self._show_main(result.name or cfg.get("name", ""))
        elif isinstance(result, VerifyInvalid):
            clear_config()
            self._show_login(initial_error="Key đã bị thu hồi. Đăng nhập lại.")
        elif isinstance(result, VerifyNetworkError):
            self._show_login(
                initial_error=f"Không kết nối được server. Thử lại. ({result.message})"
            )

    def _swap_screen(self, new_screen: tk.Frame) -> None:
        if self._screen is not None:
            self._screen.destroy()
        self._screen = new_screen
        new_screen.pack(fill="both", expand=True)

    def _show_verifying(self) -> None:
        screen = tk.Frame(self, bg=CONTENT_BG)
        tk.Label(
            screen, text="Đang xác thực…", bg=CONTENT_BG, fg=HEADER_MUTED_FG,
            font=("Segoe UI", 11),
        ).place(relx=0.5, rely=0.5, anchor="center")
        self._swap_screen(screen)

    def _show_login(self, initial_error: str | None = None) -> None:
        self._pages.clear()
        self._sidebar_items.clear()
        self._active_key = None
        screen = LoginScreen(self, on_success=self._on_login_success, initial_error=initial_error)
        self._swap_screen(screen)

    def _on_login_success(self, api_key: str, name: str) -> None:
        save_config(api_key, name)
        self._show_main(name)

    def _show_main(self, name: str) -> None:
        screen = tk.Frame(self, bg=CONTENT_BG)

        sidebar = tk.Frame(screen, bg=SIDEBAR_BG, width=220)
        sidebar.pack(side="left", fill="y")
        sidebar.pack_propagate(False)

        tk.Label(
            sidebar, text=APP_TITLE, bg=SIDEBAR_BG, fg=SIDEBAR_BRAND_FG,
            font=("Segoe UI Semibold", 14), padx=20, pady=18, anchor="w",
        ).pack(fill="x")
        tk.Frame(sidebar, bg=SIDEBAR_DIVIDER, height=1).pack(fill="x")

        self._nav = tk.Frame(sidebar, bg=SIDEBAR_BG)
        self._nav.pack(fill="both", expand=True, pady=(8, 0))

        tk.Label(
            sidebar, text=f"v{APP_VERSION}", bg=SIDEBAR_BG, fg=SIDEBAR_MUTED_FG,
            font=("Segoe UI", 8), padx=20, pady=10, anchor="w",
        ).pack(fill="x", side="bottom")

        right = tk.Frame(screen, bg=CONTENT_BG)
        right.pack(side="left", fill="both", expand=True)

        header = tk.Frame(right, bg=HEADER_BG, height=56)
        header.pack(fill="x")
        header.pack_propagate(False)
        self._page_title = tk.Label(
            header, text="", bg=HEADER_BG, fg=HEADER_FG,
            font=("Segoe UI Semibold", 14), padx=20, anchor="w",
        )
        self._page_title.pack(side="left", fill="y")
        self._page_desc = tk.Label(
            header, text="", bg=HEADER_BG, fg=HEADER_MUTED_FG,
            font=("Segoe UI", 9), padx=8, anchor="w",
        )
        self._page_desc.pack(side="left", fill="y")

        logout_btn = tk.Label(
            header, text="Đăng xuất", bg=HEADER_BG, fg=HEADER_LINK_FG,
            font=("Segoe UI", 9, "underline"), padx=20, cursor="hand2",
        )
        logout_btn.pack(side="right", fill="y")
        logout_btn.bind("<Button-1>", lambda _e: self._on_logout())

        if name:
            tk.Label(
                header, text=name, bg=HEADER_BG, fg=HEADER_MUTED_FG,
                font=("Segoe UI", 9), padx=8,
            ).pack(side="right", fill="y")

        tk.Frame(right, bg=HEADER_DIVIDER, height=1).pack(fill="x")

        self._content = tk.Frame(right, bg=CONTENT_BG)
        self._content.pack(fill="both", expand=True)

        self._swap_screen(screen)
        self._register_tools()
        if self._pages:
            self.show_page(next(iter(self._pages)))

    def _on_logout(self) -> None:
        clear_config()
        self._show_login()

    def _register_tools(self) -> None:
        tool_classes: list[type[ToolPage]] = [MP3MergerPage]
        for cls in tool_classes:
            page = cls(self._content, self)
            self._pages[page.name] = page
            item = SidebarItem(
                self._nav, page.name,
                on_click=lambda k=page.name: self.show_page(k),
            )
            item.pack(fill="x")
            self._sidebar_items[page.name] = item

    def show_page(self, key: str) -> None:
        if key not in self._pages:
            return
        if self._active_key:
            self._pages[self._active_key].hide()
            self._sidebar_items[self._active_key].set_active(False)
        self._active_key = key
        page = self._pages[key]
        page.show()
        self._sidebar_items[key].set_active(True)
        self._page_title.configure(text=page.name)
        self._page_desc.configure(text=page.description)


def main() -> None:
    A4071App().mainloop()


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Verify import graph**

From `tool/`:

```bash
python -c "import a4071_tool"
```

Expected: no traceback (just a clean exit).

- [ ] **Step 3: Commit**

```bash
git add tool/a4071_tool.py
git commit -m "Gate A4071-Tool behind API key login screen"
```

---

## Task 4: Manual smoke test

**Files:** none (verification only)

- [ ] **Step 1: Cold start, no config**

If `%APPDATA%/A4071-Tool/config.json` exists from a previous run, delete it.

From `tool/`:

```bash
python a4071_tool.py
```

Expected: login card appears centered. Window title `A4071-Tool`.

- [ ] **Step 2: Invalid key**

Type `sk_bad` in the entry, click Đăng nhập.

Expected: button text changes to "Đang kiểm tra…", then error label shows "API key không hợp lệ." (or "Không kết nối được server. Thử lại." if the backend is offline — fine, that path is also a covered branch).

- [ ] **Step 3: Valid key**

Obtain a valid key from the admin UI (`http://localhost:4072`) or backend DB. Paste it, click Đăng nhập.

Expected: main screen appears with sidebar and MP3 Merger page. Header right side shows the key name and an "Đăng xuất" link.

- [ ] **Step 4: Auto re-login on restart**

Close the window and re-run `python a4071_tool.py`.

Expected: brief "Đang xác thực…" then straight into the main screen — no login prompt.

- [ ] **Step 5: Logout**

Click "Đăng xuất" in the header.

Expected: returns to login screen. `%APPDATA%/A4071-Tool/config.json` is gone.

- [ ] **Step 6: Revoked key**

Log in successfully. Then, in the admin UI, delete that key. Close and re-run the tool.

Expected: brief "Đang xác thực…" then login screen with "Key đã bị thu hồi. Đăng nhập lại."

- [ ] **Step 7: Server offline at relaunch**

Log in successfully, then stop the backend. Close and re-run the tool.

Expected: login screen with "Không kết nối được server. Thử lại. (…)". `config.json` is preserved (not deleted).

- [ ] **Step 8: Commit (only if any fixes were made)**

If steps 1–7 surfaced issues that you fixed, commit the fix. Otherwise nothing to do.
