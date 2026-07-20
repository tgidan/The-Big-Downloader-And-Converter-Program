"""
ui/library_panel.py

Library browser — a modeless window onto the configured output directory:
a folder tree on the left, the videos in the selected folder on the right.

Scanning and metadata reads run on daemon threads and marshal results back
via self.after(0, ...), matching the pattern in ui/app_window.py. Resolution
comes from ffprobe via core.library_manager and renders as "—" when ffprobe
is unavailable, never as an error.

Public API:
LibraryPanel(master)
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from typing import Callable

import customtkinter as ctk

from core import config_manager, library_manager as lm

# Colours: match app_window.py / settings_panel.py palette #
_BDX       = "#791F1F"
_BDX_DM    = "#5C1418"
_BDX_HOVER = "#5C1418"
_BDX_TEXT  = "#FCEBEB"

_ROW_HOVER    = ("gray90", "gray20")
_ROW_SELECTED = ("gray85", "gray28")
_NAME_MAX     = 18   # chars before truncation in the video-list name column


"""Format a byte count as a short human-readable string, e.g. '142.3 MB'."""
def _format_size(size_bytes: int) -> str:
    size = float(size_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024
    return f"{size:.1f} GB"


"""Open `path` with the OS's default handler for its file type."""
def _open_with_default_app(path: str) -> None:
    if sys.platform == "win32":
        os.startfile(path)  # noqa: S606 — no shell involved, safe with any path characters
    elif sys.platform == "darwin":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])


"""Library browser window: folder tree (left) + video list (right)."""
class LibraryPanel(ctk.CTkToplevel):

    """Open non-modally, build the layout, and kick off the initial scan."""
    def __init__(self, master) -> None:
        super().__init__(master)
        self._root_dir    = config_manager.get("output_dir") or str(Path.home())
        self._current_dir = self._root_dir

        self._tree_rows: dict[str, ctk.CTkFrame] = {}
        self._selected_tree_row: ctk.CTkFrame | None = None
        self._context_menu: tk.Menu | None = None

        self.title("Library")
        self.geometry("760x520")
        self.minsize(560, 360)

        self._build()
        self._start_tree_scan()

    # Layout #

    """Toolbar row, then a left tree pane / right video pane side by side."""
    def _build(self) -> None:
        self._build_toolbar()

        body = ctk.CTkFrame(self, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=12, pady=(0, 0))
        body.columnconfigure(0, weight=1, minsize=180)
        body.columnconfigure(1, weight=2)
        body.rowconfigure(0, weight=1)

        self._build_tree_pane(body)
        self._build_video_pane(body)

        self._build_statusbar()

    """Toolbar with New Folder / search placeholders (wired up in a later commit)."""
    def _build_toolbar(self) -> None:
        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.pack(fill="x", padx=12, pady=12)

        ctk.CTkButton(
            bar,
            text="+ New Folder",
            width=110, height=28,
            fg_color=(_BDX, "#A32D2D"),
            hover_color=(_BDX_HOVER, "#C44040"),
            text_color=_BDX_TEXT,
            font=ctk.CTkFont(size=12, weight="bold"),
            state="disabled",
        ).pack(side="left")

        self._search_entry = ctk.CTkEntry(
            bar,
            placeholder_text="Search library… (coming soon)",
            font=ctk.CTkFont(size=12),
            state="disabled",
        )
        self._search_entry.pack(side="left", fill="x", expand=True, padx=(10, 0))

    """Left pane: scrollable hand-rolled folder tree (no ttk.Treeview — see Prompt 2.1)."""
    def _build_tree_pane(self, parent) -> None:
        self._tree_scroll = ctk.CTkScrollableFrame(
            parent,
            label_text="FOLDERS",
            label_font=ctk.CTkFont(size=11, weight="bold"),
            label_text_color=("gray40", "gray55"),
        )
        self._tree_scroll.grid(row=0, column=0, sticky="nsew", padx=(0, 8))

    """Right pane: column header row + scrollable video list."""
    def _build_video_pane(self, parent) -> None:
        container = ctk.CTkFrame(parent, fg_color="transparent")
        container.grid(row=0, column=1, sticky="nsew")
        container.rowconfigure(1, weight=1)
        container.columnconfigure(0, weight=1)

        header = ctk.CTkFrame(container, fg_color=("gray90", "gray17"), height=28)
        header.grid(row=0, column=0, sticky="ew")
        header.grid_propagate(False)
        header.columnconfigure(0, weight=1)

        col_kw = dict(font=ctk.CTkFont(size=11, weight="bold"), text_color=("gray40", "gray60"))
        ctk.CTkLabel(header, text="Name", anchor="w", **col_kw).grid(row=0, column=0, sticky="ew", padx=(10, 0))
        ctk.CTkLabel(header, text="Resolution", width=90, anchor="w", **col_kw).grid(row=0, column=1)
        ctk.CTkLabel(header, text="Size", width=80, anchor="w", **col_kw).grid(row=0, column=2, padx=(0, 10))

        self._video_scroll = ctk.CTkScrollableFrame(container, fg_color="transparent")
        self._video_scroll.grid(row=1, column=0, sticky="nsew")

    """Thin status/loading-indicator row at the bottom of the window."""
    def _build_statusbar(self) -> None:
        self._status_lbl = ctk.CTkLabel(
            self,
            text="",
            font=ctk.CTkFont(size=11),
            text_color=("gray40", "gray60"),
            anchor="w",
        )
        self._status_lbl.pack(fill="x", padx=14, pady=(4, 8))

    # Tree scan (initial + refresh) #

    """Show a loading status and scan the root directory on a daemon thread."""
    def _start_tree_scan(self) -> None:
        self._status_lbl.configure(text="Scanning library…")
        threading.Thread(
            target=self._tree_scan_thread,
            daemon=True,
            name="tbdc-library-scan",
        ).start()

    """Runs off the UI thread: build the directory tree, then marshal back."""
    def _tree_scan_thread(self) -> None:
        tree = lm.get_directory_tree(self._root_dir)
        self.after(0, lambda: self._on_tree_scanned(tree))

    """UI thread: render the tree, clear the loading status, select the root."""
    def _on_tree_scanned(self, tree: lm.DirectoryNode) -> None:
        self._status_lbl.configure(text="")
        self._render_tree(tree)
        self._select_folder(self._root_dir)

    """Clear and rebuild the tree pane from a DirectoryNode, flattened with indentation."""
    def _render_tree(self, tree: lm.DirectoryNode) -> None:
        for child in self._tree_scroll.winfo_children():
            child.destroy()
        self._tree_rows.clear()
        self._selected_tree_row = None

        self._add_tree_row(tree.name or "(root)", tree.path, depth=0)
        for child in tree.children:
            self._add_tree_node(child, depth=1)

    """Recursively add `node` and its children to the tree pane."""
    def _add_tree_node(self, node: lm.DirectoryNode, depth: int) -> None:
        self._add_tree_row(node.name, node.path, depth)
        for child in node.children:
            self._add_tree_node(child, depth + 1)

    """Add one clickable, indented folder row to the tree pane."""
    def _add_tree_row(self, name: str, path: str, depth: int) -> None:
        row = ctk.CTkFrame(self._tree_scroll, fg_color="transparent")
        row.pack(fill="x", pady=1)

        label = ctk.CTkButton(
            row,
            text=("📁 " if depth == 0 else "└ ") + name,
            anchor="w",
            fg_color="transparent",
            hover_color=_ROW_HOVER,
            text_color=("gray10", "gray90"),
            height=26,
            command=lambda p=path: self._select_folder(p),
        )
        label.pack(fill="x", padx=(18 * depth, 4))

        self._tree_rows[path] = row

    """Select `path`: highlight its row and repopulate the video list."""
    def _select_folder(self, path: str) -> None:
        if self._selected_tree_row is not None:
            self._selected_tree_row.configure(fg_color="transparent")
        row = self._tree_rows.get(path)
        if row is not None:
            row.configure(fg_color=_ROW_SELECTED)
        self._selected_tree_row = row

        self._current_dir = path
        videos = lm.list_videos(path)
        self._render_videos(videos)
        self._start_metadata_scan(path, videos)

    # Video list #

    """Clear and rebuild the video list pane from a list of VideoFile records."""
    def _render_videos(self, videos: list[lm.VideoFile]) -> None:
        for child in self._video_scroll.winfo_children():
            child.destroy()

        if not videos:
            ctk.CTkLabel(
                self._video_scroll,
                text="No videos in this folder",
                font=ctk.CTkFont(size=12),
                text_color=("gray50", "gray55"),
            ).pack(pady=20)
            return

        for video in videos:
            self._add_video_row(video)

    """Add one video row: name / resolution / size, Open button, dbl-click and right-click."""
    def _add_video_row(self, video: lm.VideoFile) -> None:
        row = ctk.CTkFrame(self._video_scroll, fg_color="transparent")
        row.pack(fill="x", pady=1)
        row.columnconfigure(0, weight=1)

        display_name = video.name[:_NAME_MAX] + "…" if len(video.name) > _NAME_MAX else video.name
        name_lbl = ctk.CTkLabel(
            row, text=display_name, anchor="w", width=1,
            font=ctk.CTkFont(size=12),
        )
        name_lbl.grid(row=0, column=0, sticky="ew", padx=(10, 10))

        ctk.CTkLabel(
            row, text=video.resolution or "—", width=90, anchor="w",
            font=ctk.CTkFont(size=12), text_color=("gray40", "gray60"),
        ).grid(row=0, column=1)

        ctk.CTkLabel(
            row, text=_format_size(video.size_bytes), width=80, anchor="w",
            font=ctk.CTkFont(size=12), text_color=("gray40", "gray60"),
        ).grid(row=0, column=2)

        ctk.CTkButton(
            row, text="Open", width=60, height=24,
            fg_color=("gray80", "gray25"),
            hover_color=("gray70", "gray35"),
            text_color=("gray20", "gray80"),
            font=ctk.CTkFont(size=11),
            command=lambda: _open_with_default_app(video.path),
        ).grid(row=0, column=3, padx=(6, 10))

        for widget in (row, name_lbl):
            widget.bind("<Double-Button-1>", lambda _e, v=video: _open_with_default_app(v.path))
            widget.bind("<Button-3>", lambda e, v=video: self._show_context_menu(e, v))

    """Runs on a daemon thread: fill in resolution/duration, then marshal back if still relevant."""
    def _start_metadata_scan(self, path: str, videos: list[lm.VideoFile]) -> None:
        if not videos:
            return
        threading.Thread(
            target=self._metadata_scan_thread,
            args=(path, videos),
            daemon=True,
            name="tbdc-library-metadata",
        ).start()

    """Fill metadata for every video in `videos`, off the UI thread."""
    def _metadata_scan_thread(self, path: str, videos: list[lm.VideoFile]) -> None:
        ffmpeg_path = config_manager.get("ffmpeg_path")
        for video in videos:
            lm.fill_metadata(video, ffmpeg_path)
        self.after(0, lambda: self._on_metadata_scanned(path, videos))

    """UI thread: re-render the video list, unless the user has since selected another folder."""
    def _on_metadata_scanned(self, path: str, videos: list[lm.VideoFile]) -> None:
        if path != self._current_dir:
            return
        self._render_videos(videos)

    # Context menu #

    """Build (once) and show the right-click context menu for a video row."""
    def _show_context_menu(self, event, video: lm.VideoFile) -> None:
        menu = tk.Menu(self, tearoff=0)
        menu.add_command(
            label="Open containing folder",
            command=lambda: _open_with_default_app(str(Path(video.path).parent)),
        )
        menu.add_command(
            label="Move to subfolder…",
            command=lambda: self._prompt_move(video),
        )
        menu.add_command(
            label="Copy file path",
            command=lambda: self._copy_to_clipboard(video.path),
        )
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    """Copy `text` to the system clipboard."""
    def _copy_to_clipboard(self, text: str) -> None:
        self.clipboard_clear()
        self.clipboard_append(text)

    """Open a small modal listing every known folder; move `video` there on confirm."""
    def _prompt_move(self, video: lm.VideoFile) -> None:
        dialog = ctk.CTkToplevel(self)
        dialog.title("Move to subfolder")
        dialog.geometry("360x360")
        dialog.grab_set()
        dialog.focus()

        ctk.CTkLabel(
            dialog,
            text=f"Move '{video.name}' to:",
            font=ctk.CTkFont(size=13, weight="bold"),
            anchor="w",
        ).pack(fill="x", padx=16, pady=(16, 8))

        options = sorted(self._tree_rows.keys(), key=lambda p: p.lower())
        chosen = ctk.StringVar(value=self._root_dir)

        scroll = ctk.CTkScrollableFrame(dialog, fg_color="transparent")
        scroll.pack(fill="both", expand=True, padx=16)
        for path in options:
            label = "(root)" if path == self._root_dir else str(Path(path).relative_to(self._root_dir))
            ctk.CTkRadioButton(scroll, text=label, variable=chosen, value=path).pack(anchor="w", pady=2)

        btn_row = ctk.CTkFrame(dialog, fg_color="transparent")
        btn_row.pack(fill="x", padx=16, pady=12)

        ctk.CTkButton(
            btn_row, text="Cancel",
            fg_color="transparent", border_width=1,
            border_color=("gray70", "gray40"),
            text_color=("gray45", "gray60"),
            hover_color=("gray90", "gray20"),
            command=dialog.destroy,
        ).pack(side="right", padx=(8, 0))

        ctk.CTkButton(
            btn_row, text="Move",
            fg_color=(_BDX, "#A32D2D"),
            hover_color=(_BDX_HOVER, "#C44040"),
            text_color=_BDX_TEXT,
            command=lambda: self._confirm_move(video, chosen.get(), dialog),
        ).pack(side="right")

    """Move `video` to `dest_dir` via library_manager, close the dialog, refresh the view."""
    def _confirm_move(self, video: lm.VideoFile, dest_dir: str, dialog: ctk.CTkToplevel) -> None:
        try:
            lm.move_video(video.path, dest_dir)
        except OSError as exc:
            self._status_lbl.configure(text=f"Move failed: {exc}")
        dialog.destroy()
        self._select_folder(self._current_dir)
