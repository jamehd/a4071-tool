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
        super().__init__(parent, bg=CARD_BG)
        self._on_success = on_success
        self._busy = False

        card = tk.Frame(self, bg=CARD_BG)
        card.pack(fill="both", expand=True)

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
