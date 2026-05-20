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
