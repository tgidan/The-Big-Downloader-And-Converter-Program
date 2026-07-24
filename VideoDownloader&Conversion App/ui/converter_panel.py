"""
ui/converter_panel.py

File converter panel — placeholder.

Mounted in the "Convert" tab of the main window.  It renders nothing but a
"Coming soon" notice for now; the conversion engine, file pickers and job
queue land in later cases.

Public API:
ConverterPanel(master)
"""

from __future__ import annotations

import customtkinter as ctk

# Colours #
_MUTED = ("gray50", "gray60")   # de-emphasised body text


"""
Placeholder for the file converter.

master   parent widget
"""
class ConverterPanel(ctk.CTkFrame):

    def __init__(self, master, **kwargs) -> None:
        super().__init__(master, fg_color="transparent", **kwargs)

        self._build()

    # Layout #

    """Centred heading plus one line of supporting text."""
    def _build(self) -> None:
        center = ctk.CTkFrame(self, fg_color="transparent")
        center.place(relx=0.5, rely=0.5, anchor="center")

        ctk.CTkLabel(
            center,
            text="Coming soon",
            font=ctk.CTkFont(size=16, weight="bold"),
        ).pack()

        ctk.CTkLabel(
            center,
            text="File conversion isn't wired up yet.",
            font=ctk.CTkFont(size=12),
            text_color=_MUTED,
        ).pack(pady=(6, 0))
