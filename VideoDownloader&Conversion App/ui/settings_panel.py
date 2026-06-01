"""
ui/settings_panel.py

Modal settings dialog — lets users view and edit preferences without touching
config.json directly.

Public API:
validate_settings(*, output_dir, ffmpeg_path, loudness_normalization,
                  loudness_target_lufs, cookiefile) -> dict[str, str]
SettingsPanel(master, on_save)
"""

from __future__ import annotations

import sys
from pathlib import Path
from tkinter import filedialog
from typing import Callable

import customtkinter as ctk

from core import config_manager

# Colours: match app_window.py palette #
_BDX       = "#791F1F"
_BDX_DM    = "#5C1418"
_BDX_HOVER = "#5C1418"
_BDX_TEXT  = "#FCEBEB"

_QUALITY_OPTIONS = [
    "bestvideo+bestaudio/best",
    "bestvideo[height<=2160]+bestaudio/best",
    "bestvideo[height<=1440]+bestaudio/best",
    "bestvideo[height<=1080]+bestaudio/best",
    "bestvideo[height<=720]+bestaudio/best",
    "bestvideo[height<=480]+bestaudio/best",
    "bestaudio/best",
]


# Pure validation: testable without CTk #

"""
Validate settings form values before saving.

All arguments are strings (as they appear in UI entry fields) except
loudness_normalization which is a bool.

Returns a dict mapping field name to an error message for every invalid field.
An empty dict means all values are acceptable.
"""
def validate_settings(
    *,
    output_dir: str,
    ffmpeg_path: str,
    loudness_normalization: bool,
    loudness_target_lufs: str,
    cookiefile: str,
) -> dict[str, str]:
    errors: dict[str, str] = {}

    if output_dir and not Path(output_dir).is_dir():
        errors["output_dir"] = f"Directory not found: {output_dir}"

    if ffmpeg_path:
        p = Path(ffmpeg_path)
        if not p.exists():
            errors["ffmpeg_path"] = f"Path not found: {ffmpeg_path}"
        elif p.is_dir():
            binary = p / ("ffmpeg.exe" if sys.platform == "win32" else "ffmpeg")
            if not binary.is_file():
                errors["ffmpeg_path"] = f"ffmpeg binary not found in: {ffmpeg_path}"

    if loudness_normalization:
        try:
            lufs = float(loudness_target_lufs)
            if not (-70.0 <= lufs <= 0.0):
                errors["lufs"] = "LUFS target must be between -70 and 0"
        except ValueError:
            errors["lufs"] = f"Invalid LUFS value: {loudness_target_lufs!r}"

    if cookiefile and not Path(cookiefile).is_file():
        errors["cookiefile"] = f"Cookie file not found: {cookiefile}"

    return errors


# UI #

"""Modal dialog for editing user preferences."""
class SettingsPanel(ctk.CTkToplevel):

    """Open modal, configure geometry, build all UI sections, load config values."""
    def __init__(
        self,
        master,
        on_save: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(master)
        self._on_save = on_save

        self.title("Settings")
        self.geometry("520x580")
        self.resizable(False, False)
        self.grab_set()
        self.focus()

        self._build()
        self._load_values()

    # Layout #

    """Assemble all settings sections with dividers into a scrollable frame, then footer."""
    def _build(self) -> None:
        self._scroll = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self._scroll.pack(fill="both", expand=True)
        self._scroll.columnconfigure(0, weight=1)

        self._build_output_dir(self._scroll)
        self._build_divider(self._scroll)
        self._build_quality(self._scroll)
        self._build_divider(self._scroll)
        self._build_ffmpeg(self._scroll)
        self._build_divider(self._scroll)
        self._build_normalization(self._scroll)
        self._build_divider(self._scroll)
        self._build_cookiefile(self._scroll)

        self._build_footer()

    """Pack a small bold all-caps section header into parent."""
    def _section_label(self, parent, text: str) -> None:
        ctk.CTkLabel(
            parent,
            text=text,
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=("gray40", "gray55"),
            anchor="w",
        ).pack(fill="x", padx=22, pady=(16, 4))

    """Pack an initially-empty red error label into parent and return it."""
    def _error_label(self, parent) -> ctk.CTkLabel:
        lbl = ctk.CTkLabel(
            parent,
            text="",
            font=ctk.CTkFont(size=11),
            text_color=("#CC3333", "#FF6666"),
            anchor="w",
        )
        lbl.pack(fill="x", padx=22, pady=(0, 2))
        return lbl

    """Pack a 1 px horizontal separator line."""
    def _build_divider(self, parent) -> None:
        ctk.CTkFrame(
            parent, height=1, fg_color=("gray88", "gray22"),
        ).pack(fill="x", padx=22, pady=0)

    """Output directory row: text entry + Browse button + inline error label."""
    def _build_output_dir(self, parent) -> None:
        self._section_label(parent, "OUTPUT DIRECTORY")
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", padx=22, pady=(0, 2))
        row.columnconfigure(0, weight=1)

        self._output_dir_var = ctk.StringVar()
        ctk.CTkEntry(
            row,
            textvariable=self._output_dir_var,
            font=ctk.CTkFont(size=12),
        ).grid(row=0, column=0, sticky="ew", padx=(0, 8))
        ctk.CTkButton(
            row, text="Browse…", width=80, height=28,
            fg_color=("gray80", "gray25"),
            hover_color=("gray70", "gray35"),
            text_color=("gray20", "gray80"),
            font=ctk.CTkFont(size=12),
            command=self._pick_output_dir,
        ).grid(row=0, column=1)

        self._output_dir_err = self._error_label(parent)

    """Default download quality option menu."""
    def _build_quality(self, parent) -> None:
        self._section_label(parent, "DEFAULT QUALITY")
        self._quality_var = ctk.StringVar()
        ctk.CTkOptionMenu(
            parent,
            variable=self._quality_var,
            values=_QUALITY_OPTIONS,
            dynamic_resizing=False,
            font=ctk.CTkFont(size=12),
        ).pack(fill="x", padx=22, pady=(0, 10))

    """ffmpeg path row: hint label + text entry + Browse button + error label."""
    def _build_ffmpeg(self, parent) -> None:
        self._section_label(parent, "FFMPEG PATH")
        ctk.CTkLabel(
            parent,
            text="Leave blank to auto-detect from PATH",
            font=ctk.CTkFont(size=11),
            text_color=("gray50", "gray55"),
            anchor="w",
        ).pack(fill="x", padx=22, pady=(0, 6))

        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", padx=22, pady=(0, 2))
        row.columnconfigure(0, weight=1)

        self._ffmpeg_var = ctk.StringVar()
        ctk.CTkEntry(
            row,
            textvariable=self._ffmpeg_var,
            placeholder_text="Detected automatically",
            font=ctk.CTkFont(size=12),
        ).grid(row=0, column=0, sticky="ew", padx=(0, 8))
        ctk.CTkButton(
            row, text="Browse…", width=80, height=28,
            fg_color=("gray80", "gray25"),
            hover_color=("gray70", "gray35"),
            text_color=("gray20", "gray80"),
            font=ctk.CTkFont(size=12),
            command=self._pick_ffmpeg,
        ).grid(row=0, column=1)

        self._ffmpeg_err = self._error_label(parent)

    """EBU R128 loudness switch with conditional LUFS entry and error label."""
    def _build_normalization(self, parent) -> None:
        self._section_label(parent, "LOUDNESS NORMALIZATION")

        self._norm_var = ctk.BooleanVar(value=False)
        ctk.CTkSwitch(
            parent,
            text="Normalize audio loudness (EBU R128)",
            variable=self._norm_var,
            onvalue=True,
            offvalue=False,
            command=self._on_norm_toggle,
            font=ctk.CTkFont(size=12),
        ).pack(anchor="w", padx=22, pady=(0, 8))

        lufs_row = ctk.CTkFrame(parent, fg_color="transparent")
        lufs_row.pack(fill="x", padx=22, pady=(0, 2))

        ctk.CTkLabel(
            lufs_row,
            text="Target (LUFS)",
            font=ctk.CTkFont(size=12),
            text_color=("gray40", "gray60"),
        ).pack(side="left")

        self._lufs_var = ctk.StringVar(value="-14.0")
        self._lufs_entry = ctk.CTkEntry(
            lufs_row,
            textvariable=self._lufs_var,
            width=80,
            font=ctk.CTkFont(size=12),
        )
        self._lufs_entry.pack(side="left", padx=(12, 0))

        self._lufs_err = self._error_label(parent)

    """Cookie file row: hint label + text entry + Browse button + error label."""
    def _build_cookiefile(self, parent) -> None:
        self._section_label(parent, "COOKIE FILE")
        ctk.CTkLabel(
            parent,
            text="For age-restricted or throttled content (optional)",
            font=ctk.CTkFont(size=11),
            text_color=("gray50", "gray55"),
            anchor="w",
        ).pack(fill="x", padx=22, pady=(0, 6))

        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", padx=22, pady=(0, 2))
        row.columnconfigure(0, weight=1)

        self._cookiefile_var = ctk.StringVar()
        ctk.CTkEntry(
            row,
            textvariable=self._cookiefile_var,
            placeholder_text="Optional — leave blank to skip",
            font=ctk.CTkFont(size=12),
        ).grid(row=0, column=0, sticky="ew", padx=(0, 8))
        ctk.CTkButton(
            row, text="Browse…", width=80, height=28,
            fg_color=("gray80", "gray25"),
            hover_color=("gray70", "gray35"),
            text_color=("gray20", "gray80"),
            font=ctk.CTkFont(size=12),
            command=self._pick_cookiefile,
        ).grid(row=0, column=1)

        self._cookiefile_err = self._error_label(parent)

    """Footer with status label, Reset to defaults, and Save buttons."""
    def _build_footer(self) -> None:
        ctk.CTkFrame(
            self, height=1, fg_color=("gray88", "gray22"), corner_radius=0,
        ).pack(fill="x")

        footer = ctk.CTkFrame(self, fg_color="transparent", height=54)
        footer.pack(fill="x", padx=22)
        footer.pack_propagate(False)

        self._status_lbl = ctk.CTkLabel(
            footer,
            text="",
            font=ctk.CTkFont(size=11),
            text_color=("gray40", "gray60"),
            anchor="w",
        )
        self._status_lbl.pack(side="left", pady=14)

        ctk.CTkButton(
            footer,
            text="Save",
            width=90, height=32,
            fg_color=(_BDX, "#A32D2D"),
            hover_color=(_BDX_HOVER, "#C44040"),
            text_color=_BDX_TEXT,
            font=ctk.CTkFont(size=13, weight="bold"),
            command=self._save,
        ).pack(side="right", pady=11)

        ctk.CTkButton(
            footer,
            text="Reset to defaults",
            width=130, height=32,
            fg_color="transparent",
            border_width=1,
            border_color=("gray70", "gray40"),
            text_color=("gray45", "gray60"),
            hover_color=("gray90", "gray20"),
            font=ctk.CTkFont(size=12),
            command=self._reset_defaults,
        ).pack(side="right", padx=(0, 8), pady=11)

    # Load / save #

    """Populate all form fields from the current config and sync the norm toggle."""
    def _load_values(self) -> None:
        config_manager.load()

        self._output_dir_var.set(config_manager.get("output_dir") or "")

        quality = config_manager.get("last_quality", _QUALITY_OPTIONS[0])
        # If the stored value is not in the preset list, add it so the menu shows it
        if quality not in _QUALITY_OPTIONS:
            _QUALITY_OPTIONS.append(quality)
        self._quality_var.set(quality)

        self._ffmpeg_var.set(config_manager.get("ffmpeg_path") or "")
        self._norm_var.set(bool(config_manager.get("loudness_normalization", False)))
        self._lufs_var.set(str(config_manager.get("loudness_target_lufs", -14.0)))
        self._cookiefile_var.set(config_manager.get("cookiefile") or "")

        self._on_norm_toggle()

    """Validate form, persist on success, display per-field errors on failure."""
    def _save(self) -> None:
        self._clear_errors()

        values = self._read_form()
        errors = validate_settings(**values)
        if errors:
            self._show_errors(errors)
            return

        config_manager.update({
            "output_dir":             values["output_dir"],
            "last_quality":           self._quality_var.get(),
            "ffmpeg_path":            values["ffmpeg_path"] or None,
            "loudness_normalization": values["loudness_normalization"],
            "loudness_target_lufs":   float(values["loudness_target_lufs"]) if values["loudness_normalization"] else config_manager.get("loudness_target_lufs", -14.0),
            "cookiefile":             values["cookiefile"] or None,
        })

        self._status_lbl.configure(
            text="✓ Settings saved",
            text_color=("#2D7A2D", "#4CAF50"),
        )
        self.after(3000, lambda: self._status_lbl.configure(text=""))

        if self._on_save:
            self._on_save()

    """Restore factory defaults, reload the form, and notify via on_save callback."""
    def _reset_defaults(self) -> None:
        config_manager.reset_to_defaults()
        self._load_values()
        self._clear_errors()
        self._status_lbl.configure(
            text="✓ Reset to defaults",
            text_color=("gray40", "gray60"),
        )
        self.after(3000, lambda: self._status_lbl.configure(text=""))
        if self._on_save:
            self._on_save()

    # Helpers #

    """Return stripped form values as a dict keyed by config field name."""
    def _read_form(self) -> dict:
        return {
            "output_dir":             self._output_dir_var.get().strip(),
            "ffmpeg_path":            self._ffmpeg_var.get().strip(),
            "loudness_normalization": self._norm_var.get(),
            "loudness_target_lufs":   self._lufs_var.get().strip(),
            "cookiefile":             self._cookiefile_var.get().strip(),
        }

    """Display per-field error labels and set the status bar to a failure message."""
    def _show_errors(self, errors: dict[str, str]) -> None:
        mapping = {
            "output_dir": self._output_dir_err,
            "ffmpeg_path": self._ffmpeg_err,
            "lufs": self._lufs_err,
            "cookiefile": self._cookiefile_err,
        }
        for key, lbl in mapping.items():
            if key in errors:
                lbl.configure(text=errors[key])
        self._status_lbl.configure(
            text="Fix the errors above to save",
            text_color=("#CC3333", "#FF6666"),
        )

    """Clear all error labels and reset the status bar text."""
    def _clear_errors(self) -> None:
        for lbl in (
            self._output_dir_err,
            self._ffmpeg_err,
            self._lufs_err,
            self._cookiefile_err,
        ):
            lbl.configure(text="")
        self._status_lbl.configure(text="")

    """Enable or disable the LUFS entry to match the normalisation switch state."""
    def _on_norm_toggle(self) -> None:
        state = "normal" if self._norm_var.get() else "disabled"
        self._lufs_entry.configure(state=state)

    # File pickers #

    """Open a directory picker and update the output dir entry."""
    def _pick_output_dir(self) -> None:
        path = filedialog.askdirectory(
            parent=self,
            title="Choose default output folder",
            initialdir=self._output_dir_var.get() or str(Path.home()),
        )
        if path:
            self._output_dir_var.set(path)

    """Open a file picker for the ffmpeg binary and update the entry."""
    def _pick_ffmpeg(self) -> None:
        current = self._ffmpeg_var.get()
        initialdir = str(Path(current).parent) if current else str(Path.home())
        filetypes = (
            [("Executable", "*.exe"), ("All files", "*.*")]
            if sys.platform == "win32"
            else [("All files", "*.*")]
        )
        path = filedialog.askopenfilename(
            parent=self,
            title="Select ffmpeg binary",
            initialdir=initialdir,
            filetypes=filetypes,
        )
        if path:
            self._ffmpeg_var.set(path)

    """Open a file picker for the cookie file and update the entry."""
    def _pick_cookiefile(self) -> None:
        current = self._cookiefile_var.get()
        initialdir = str(Path(current).parent) if current else str(Path.home())
        path = filedialog.askopenfilename(
            parent=self,
            title="Select cookie file",
            initialdir=initialdir,
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
        )
        if path:
            self._cookiefile_var.set(path)
