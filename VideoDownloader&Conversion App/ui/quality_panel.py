"""
ui/quality_panel.py

Format / quality selector panel.

Public API:
QualityPanel(master, on_add_to_queue)
QualityPanel.populate(url, formats, info)         – rebuild dropdown from fetched formats
QualityPanel.get_selected_format() -> str         – yt-dlp format string for selection
QualityPanel.get_output_dir() -> str              – currently chosen output directory
QualityPanel.open_output_picker()                 – open directory-chooser dialog
"""

from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path
from tkinter import filedialog
from typing import Callable

import customtkinter as ctk

from core import config_manager
from core.downloader import build_audio_format_string, build_video_format_string

# Accent colours #
_BDX       = ("#791F1F", "#A32D2D")
_BDX_HOVER = ("#5C1418", "#C44040")
_BDX_TEXT  = "#FCEBEB"

_DEFAULT_OUTPUT = str(Path.home() / "Downloads" / "TBD&C")
_OUTPUT_DIR_KEY = "output_dir"

# Container preference order (lower index = higher priority)
_EXT_ORDER = {"mp4": 0, "webm": 1}


"""
Quality / format selector.

master            parent widget
on_add_to_queue   callback: (url, format_string, audio_only) -> None
"""
class QualityPanel(ctk.CTkFrame):

    def __init__(
        self,
        master,
        on_add_to_queue: Callable[[str, str, bool], None],
        **kwargs,
    ) -> None:
        super().__init__(master, fg_color="transparent", **kwargs)
        self._callback   = on_add_to_queue
        self._url        = ""
        self._formats: list[dict] = []
        # label -> (yt-dlp format string, audio_only flag)
        self._format_map: dict[str, tuple[str, bool]] = {}
        self._output_dir = config_manager.get(_OUTPUT_DIR_KEY, _DEFAULT_OUTPUT)

        self._build()
        self._set_state("disabled")

    # Public #

    """Receive a fresh format list and rebuild the dropdown."""
    def populate(self, url: str, formats: list[dict], info: dict) -> None:
        self._url     = url
        self._formats = formats
        self._format_map = {}

        # Collect which containers are actually available per resolution
        res_containers: dict[int, set[str]] = defaultdict(set)
        for f in formats:
            height = f.get("height")
            ext    = f.get("ext", "")
            vcodec = f.get("vcodec", "none")
            if height and vcodec != "none" and ext in _EXT_ORDER:
                res_containers[height].add(ext)

        labels: list[str] = []

        for height in sorted(res_containers.keys(), reverse=True):
            for ext in sorted(res_containers[height], key=lambda e: _EXT_ORDER.get(e, 99)):
                label = f"{height}p ({ext})"
                # Prefer the exact container but fall back gracefully
                fmt = (
                    f"bestvideo[height<={height}][ext={ext}]+bestaudio[ext=m4a]"
                    f"/bestvideo[height<={height}][ext={ext}]+bestaudio"
                    f"/bestvideo[height<={height}]+bestaudio/best"
                )
                self._format_map[label] = (fmt, False)
                labels.append(label)

        # Fallback: formats have heights but no recognised container
        if not labels:
            heights = sorted(
                {
                    f["height"]
                    for f in formats
                    if f.get("height") and f.get("vcodec", "none") != "none"
                },
                reverse=True,
            )
            for h in heights:
                label = f"{h}p (best)"
                self._format_map[label] = (build_video_format_string(h), False)
                labels.append(label)

        audio_label = "Audio only (mp3)"
        self._format_map[audio_label] = (build_audio_format_string(), True)
        labels.append(audio_label)

        self._format_menu.configure(values=labels)
        self._format_menu.set(labels[0])

        self._set_state("normal")
        self._refresh_info()

    """Return the yt-dlp format string for the currently selected option."""
    def get_selected_format(self) -> str:
        label = self._format_menu.get()
        fmt, _ = self._format_map.get(label, (build_video_format_string(1080), False))
        return fmt

    """Return the currently chosen output directory path."""
    def get_output_dir(self) -> str:
        return self._output_dir

    """Open the output directory dialog (callable from outside the panel)."""
    def open_output_picker(self) -> None:
        self._pick_output_dir()

    # Layout #

    def _build(self) -> None:
        # Section heading
        ctk.CTkLabel(
            self,
            text="DOWNLOAD OPTIONS",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=("gray40", "gray55"),
            anchor="w",
        ).pack(anchor="w", pady=(0, 8))

        # Quality label
        ctk.CTkLabel(
            self,
            text="Quality",
            font=ctk.CTkFont(size=12),
            text_color=("gray40", "gray60"),
            anchor="w",
        ).pack(anchor="w", pady=(0, 4))

        # Format dropdown
        self._format_menu = ctk.CTkOptionMenu(
            self,
            values=["—"],
            command=lambda _: self._refresh_info(),
            dynamic_resizing=False,
        )
        self._format_menu.pack(fill="x", pady=(0, 10))

        # File-info strip
        self._info_strip = ctk.CTkFrame(self, corner_radius=6)
        self._info_strip.pack(fill="x", pady=(0, 18))

        self._size_lbl = ctk.CTkLabel(
            self._info_strip,
            text="~ — MB",
            font=ctk.CTkFont(size=11),
            text_color=("gray50", "gray55"),
        )
        self._size_lbl.pack(side="left", padx=(12, 18), pady=7)

        self._codec_lbl = ctk.CTkLabel(
            self._info_strip,
            text="—",
            font=ctk.CTkFont(size=11),
            text_color=("gray50", "gray55"),
        )
        self._codec_lbl.pack(side="left", padx=(0, 18), pady=7)

        self._eta_lbl = ctk.CTkLabel(
            self._info_strip,
            text="—",
            font=ctk.CTkFont(size=11),
            text_color=("gray50", "gray55"),
        )
        self._eta_lbl.pack(side="left", pady=7)

        # Output folder heading
        ctk.CTkLabel(
            self,
            text="OUTPUT FOLDER",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=("gray40", "gray55"),
            anchor="w",
        ).pack(anchor="w", pady=(0, 6))

        # Directory row: path label + Browse button
        dir_row = ctk.CTkFrame(self, fg_color="transparent")
        dir_row.pack(fill="x", pady=(0, 14))
        dir_row.columnconfigure(0, weight=1)

        self._dir_lbl = ctk.CTkLabel(
            dir_row,
            text=self._output_dir,
            font=ctk.CTkFont(size=11),
            text_color=("gray40", "gray60"),
            anchor="w",
        )
        self._dir_lbl.grid(row=0, column=0, sticky="ew", padx=(0, 8))

        ctk.CTkButton(
            dir_row,
            text="Browse…",
            width=80,
            height=28,
            fg_color=("gray80", "gray25"),
            hover_color=("gray70", "gray35"),
            text_color=("gray20", "gray80"),
            font=ctk.CTkFont(size=12),
            command=self._pick_output_dir,
        ).grid(row=0, column=1, sticky="e")

        # Add to queue button
        self._add_btn = ctk.CTkButton(
            self,
            text="+ Add to queue",
            height=40,
            fg_color=_BDX,
            hover_color=_BDX_HOVER,
            text_color=_BDX_TEXT,
            font=ctk.CTkFont(size=14, weight="bold"),
            command=self._add_to_queue,
        )
        self._add_btn.pack(fill="x")

    # State helpers #

    def _set_state(self, state: str) -> None:
        self._format_menu.configure(state=state)
        self._add_btn.configure(state=state)
        # Browse button is always enabled

    # Info strip #

    def _refresh_info(self) -> None:
        label = self._format_menu.get()
        _, audio_only = self._format_map.get(label, ("", False))

        if audio_only:
            self._codec_lbl.configure(text="mp3 · 192k")
            self._size_lbl.configure(text="~ small")
            self._eta_lbl.configure(text="—")
            return

        if not self._formats:
            return

        m_height = re.match(r"^(\d+)p", label)
        m_ext    = re.search(r"\((\w+)\)", label)
        height   = int(m_height.group(1)) if m_height else None
        ext      = m_ext.group(1) if m_ext else None

        if not height:
            self._codec_lbl.configure(text="—")
            self._size_lbl.configure(text="—")
            return

        matches = [
            f for f in self._formats
            if f.get("height") == height
            and f.get("vcodec", "none") != "none"
            and (ext is None or f.get("ext") == ext)
        ]
        # Widen to any format at this height if the exact container isn't found
        if not matches:
            matches = [
                f for f in self._formats
                if f.get("height") == height and f.get("vcodec", "none") != "none"
            ]

        if not matches:
            self._codec_lbl.configure(text="—")
            self._size_lbl.configure(text="—")
            return

        best   = max(matches, key=lambda f: f.get("filesize") or 0)
        vcodec = (best.get("vcodec") or "").split(".")[0]
        acodec = (best.get("acodec") or "").split(".")[0]
        codec  = f"{vcodec}+{acodec}" if acodec not in ("none", "") else vcodec
        self._codec_lbl.configure(text=codec or "—")

        size = best.get("filesize")
        self._size_lbl.configure(
            text=f"~{size / 1_000_000:.0f} MB" if size else "size unknown"
        )
        self._eta_lbl.configure(text="—")  # ETA only known at download time

    # Output dir #

    def _pick_output_dir(self) -> None:
        path = filedialog.askdirectory(
            parent=self.winfo_toplevel(),
            title="Choose output folder",
            initialdir=self._output_dir,
        )
        if path:
            self._output_dir = path
            self._dir_lbl.configure(text=path)
            config_manager.set(_OUTPUT_DIR_KEY, path)

    # Queue action #

    def _add_to_queue(self) -> None:
        if not self._url:
            return
        label = self._format_menu.get()
        fmt, audio_only = self._format_map.get(label, (build_video_format_string(1080), False))
        self._callback(self._url, fmt, audio_only)
