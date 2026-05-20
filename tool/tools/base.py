from __future__ import annotations

import tkinter as tk
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from a4071_tool import A4071App


class ToolPage(ABC):
    name: str = "Tool"
    description: str = ""

    def __init__(self, parent: tk.Misc, app: "A4071App") -> None:
        self.app = app
        self.frame = tk.Frame(parent, bg="#f8fafc")
        self.build_ui(self.frame)

    @abstractmethod
    def build_ui(self, parent: tk.Misc) -> None:
        ...

    def show(self) -> None:
        self.frame.pack(fill="both", expand=True)

    def hide(self) -> None:
        self.frame.pack_forget()
