"""
ui/library_panel.py

Library browser — a modeless window onto the configured output directory:
a folder tree on the left, the videos in the selected folder on the right.

Scanning and metadata reads run on daemon threads and marshal results back
via self.after(0, ...), matching the pattern in ui/app_window.py. Resolution
comes from ffprobe via core.library_manager and renders as "—" when ffprobe
is unavailable, never as an error.

List / thumbnail view is a toggle in the toolbar, persisted via
config_manager's "library_view_mode" key. Thumbnails are extracted by
core.library_manager (ffmpeg, cached under ~/.tbdc/) and populate the grid
progressively; a generic placeholder is shown until each one is ready, or
permanently if ffmpeg is unavailable.

Public API:
LibraryPanel(master, on_folder_created=None)
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, simpledialog
from typing import Callable

import customtkinter as ctk

from core import config_manager, library_manager as lm
from core.utils import sanitize_filename

# Colours: match app_window.py / settings_panel.py palette #
_BDX       = "#791F1F"
_BDX_DM    = "#5C1418"
_BDX_HOVER = "#5C1418"
_BDX_TEXT  = "#FCEBEB"

_ROW_HOVER    = ("gray90", "gray20")
_ROW_SELECTED = ("gray85", "gray28")
_NAME_MAX     = 18   # chars before truncation in the video-list name column

_VIEW_LIST       = "list"
_VIEW_THUMBNAILS = "thumbnails"
_VIEW_LABELS     = {_VIEW_LIST: "List", _VIEW_THUMBNAILS: "Thumbnails"}
_VIEW_MODES      = {label: mode for mode, label in _VIEW_LABELS.items()}

_THUMB_SIZE      = (160, 90)   # matches core.library_manager's extraction width
_THUMB_CARD_SLOT = 192         # approx. card width (thumbnail + padding), used to fit columns to the pane width


"""Format a byte count as a short human-readable string, e.g. '142.3 MB'."""
def _format_size(size_bytes: int) -> str:
    size = float(size_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024
    return f"{size:.1f} GB"


"""Build a generic dark placeholder frame with a play-triangle glyph, drawn
procedurally so no image asset is needed (Pillow is a soft dependency here,
matching ui/url_panel.py's _load_thumb)."""
def _make_placeholder_image():
    from PIL import Image, ImageDraw  # soft dependency

    w, h = _THUMB_SIZE
    img = Image.new("RGB", (w, h), (45, 45, 48))
    draw = ImageDraw.Draw(img)
    cx, cy, r = w // 2, h // 2, min(w, h) // 4
    draw.polygon(
        [(cx - r // 2, cy - r), (cx - r // 2, cy + r), (cx + r, cy)],
        fill=(90, 90, 95),
    )
    return img


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

    """
    Open non-modally, build the layout, and kick off the initial scan.

    on_folder_created  callback: () -> None, fired after a folder is
                        successfully created via the toolbar button — lets
                        callers (e.g. QualityPanel's destination dropdown)
                        refresh their own view of the folder tree.
    """
    def __init__(self, master, on_folder_created: Callable[[], None] | None = None) -> None:
        super().__init__(master)
        self._on_folder_created = on_folder_created
        self._root_dir    = config_manager.get("output_dir") or str(Path.home())
        self._current_dir = self._root_dir

        self._tree_rows: dict[str, ctk.CTkFrame] = {}
        self._selected_tree_row: ctk.CTkFrame | None = None
        self._context_menu: tk.Menu | None = None

        self._search_query: str = ""       # "" means no active search
        self._search_after_id: str | None = None

        stored_mode = config_manager.get("library_view_mode", _VIEW_LIST)
        self._view_mode = stored_mode if stored_mode in _VIEW_LABELS else _VIEW_LIST
        self._thumb_refs: dict[str, ctk.CTkImage] = {}        # keep alive — Tk drops GC'd images
        self._thumb_card_labels: dict[str, ctk.CTkLabel] = {}
        self._thumb_cards: list[ctk.CTkFrame] = []            # positioned by _layout_thumbnail_grid
        self._placeholder_thumb: ctk.CTkImage | None = None
        self._last_videos: list[lm.VideoFile] = []
        self._last_empty_text: str = ""
        self._last_show_folder: bool = False

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

    """Toolbar with the New Folder button and the cross-folder search field."""
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
            command=self._prompt_new_folder,
        ).pack(side="left")

        self._search_entry = ctk.CTkEntry(
            bar,
            placeholder_text="Search library…",
            font=ctk.CTkFont(size=12),
        )
        self._search_entry.pack(side="left", fill="x", expand=True, padx=(10, 0))
        self._search_entry.bind("<KeyRelease>", self._on_search_key)

        self._view_toggle = ctk.CTkSegmentedButton(
            bar,
            values=[_VIEW_LABELS[_VIEW_LIST], _VIEW_LABELS[_VIEW_THUMBNAILS]],
            font=ctk.CTkFont(size=12),
            command=self._on_view_mode_changed,
        )
        self._view_toggle.set(_VIEW_LABELS[self._view_mode])
        self._view_toggle.pack(side="left", padx=(10, 0))

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

        self._video_header = ctk.CTkFrame(container, fg_color=("gray90", "gray17"), height=28)
        self._video_header.grid(row=0, column=0, sticky="ew")
        self._video_header.grid_propagate(False)
        self._video_header.columnconfigure(0, weight=1)

        col_kw = dict(font=ctk.CTkFont(size=11, weight="bold"), text_color=("gray40", "gray60"))
        ctk.CTkLabel(self._video_header, text="Name", anchor="w", **col_kw).grid(row=0, column=0, sticky="ew", padx=(10, 0))
        ctk.CTkLabel(self._video_header, text="Resolution", width=90, anchor="w", **col_kw).grid(row=0, column=1)
        ctk.CTkLabel(self._video_header, text="Size", width=80, anchor="w", **col_kw).grid(row=0, column=2, padx=(0, 10))

        self._video_scroll = ctk.CTkScrollableFrame(container, fg_color="transparent")
        self._video_scroll.grid(row=1, column=0, sticky="nsew")
        self._video_scroll.bind("<Configure>", self._on_video_pane_resize)

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

    """Show a loading status and scan the root directory on a daemon thread.

    `select_path` is highlighted once the scan completes; the root is
    selected by default, which covers both the initial load and a refresh
    after a folder is created without one.
    """
    def _start_tree_scan(self, select_path: str | None = None) -> None:
        self._status_lbl.configure(text="Scanning library…")
        threading.Thread(
            target=lambda: self._tree_scan_thread(select_path),
            daemon=True,
            name="tbdc-library-scan",
        ).start()

    """Runs off the UI thread: build the directory tree, then marshal back."""
    def _tree_scan_thread(self, select_path: str | None) -> None:
        tree = lm.get_directory_tree(self._root_dir)
        self.after(0, lambda: self._on_tree_scanned(tree, select_path))

    """UI thread: render the tree, clear the loading status, select a folder."""
    def _on_tree_scanned(self, tree: lm.DirectoryNode, select_path: str | None = None) -> None:
        self._status_lbl.configure(text="")
        self._render_tree(tree)
        self._select_folder(select_path or self._root_dir)

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
        is_stale = lambda p=path: p != self._current_dir or bool(self._search_query)
        self._start_metadata_scan(videos, is_stale=is_stale, render=self._render_videos)
        self._start_thumbnail_scan(videos, is_stale=is_stale)

    # New Folder #

    """
    Prompt for a name, create it under the currently selected folder (root
    if nothing is selected), and refresh the tree selecting the new folder.
    Shows a message box rather than failing silently on error — including a
    name collision, which create_subfolder() itself treats as a no-op since
    it's built on mkdir(exist_ok=True).
    """
    def _prompt_new_folder(self) -> None:
        name = simpledialog.askstring("New Folder", "Folder name:", parent=self)
        if not name:
            return

        target = Path(self._current_dir) / sanitize_filename(name)
        if target.is_dir():
            messagebox.showerror(
                "Couldn't create folder",
                f"A folder named '{target.name}' already exists here.",
                parent=self,
            )
            return

        try:
            new_dir = lm.create_subfolder(self._root_dir, self._current_dir, name)
        except (ValueError, OSError) as exc:
            messagebox.showerror("Couldn't create folder", str(exc), parent=self)
            return

        self._start_tree_scan(select_path=str(new_dir))
        if self._on_folder_created:
            self._on_folder_created()

    # Video list #

    """Clear and rebuild the video list pane from a list of VideoFile records."""
    def _render_videos(self, videos: list[lm.VideoFile]) -> None:
        self._render_video_rows(videos, "No videos in this folder", show_folder=False)

    """
    Clear and rebuild the video list pane as cross-folder search results.
    Each row shows its containing folder, since results span folders.
    """
    def _render_search_results(self, videos: list[lm.VideoFile]) -> None:
        self._render_video_rows(videos, "No matches", show_folder=True)

    """
    Shared renderer behind _render_videos / _render_search_results. Also the
    re-render target when the list/thumbnail toggle changes, so it caches
    its last arguments for _on_view_mode_changed to replay.
    """
    def _render_video_rows(self, videos: list[lm.VideoFile], empty_text: str, *, show_folder: bool) -> None:
        self._last_videos      = videos
        self._last_empty_text  = empty_text
        self._last_show_folder = show_folder

        for child in self._video_scroll.winfo_children():
            child.destroy()
        self._thumb_refs.clear()
        self._thumb_card_labels.clear()
        self._thumb_cards = []

        is_thumbnails = self._view_mode == _VIEW_THUMBNAILS
        if is_thumbnails:
            self._video_header.grid_remove()
        else:
            self._video_header.grid()

        if not videos:
            ctk.CTkLabel(
                self._video_scroll,
                text=empty_text,
                font=ctk.CTkFont(size=12),
                text_color=("gray50", "gray55"),
            ).pack(pady=20)
            return

        if is_thumbnails:
            for video in videos:
                self._thumb_cards.append(self._add_thumbnail_card(video, show_folder=show_folder))
            self._layout_thumbnail_grid()
        else:
            for video in videos:
                self._add_video_row(video, show_folder=show_folder)

    """Return a display string for the folder containing `video`, relative to the root."""
    def _folder_label(self, video: lm.VideoFile) -> str:
        folder = str(Path(video.path).parent)
        if folder == self._root_dir:
            return "(root)"
        return str(Path(folder).relative_to(self._root_dir))

    """Add one video row: name (+ folder, if show_folder) / resolution / size / Open button."""
    def _add_video_row(self, video: lm.VideoFile, *, show_folder: bool = False) -> None:
        row = ctk.CTkFrame(self._video_scroll, fg_color="transparent")
        row.pack(fill="x", pady=1)
        row.columnconfigure(0, weight=1)

        name_cell = ctk.CTkFrame(row, fg_color="transparent")
        name_cell.grid(row=0, column=0, sticky="ew", padx=(10, 10))

        display_name = video.name[:_NAME_MAX] + "…" if len(video.name) > _NAME_MAX else video.name
        name_lbl = ctk.CTkLabel(
            name_cell, text=display_name, anchor="w", width=1,
            font=ctk.CTkFont(size=12),
        )
        name_lbl.pack(fill="x")

        bind_targets = [row, name_cell, name_lbl]
        if show_folder:
            folder_lbl = ctk.CTkLabel(
                name_cell, text=self._folder_label(video), anchor="w", width=1,
                font=ctk.CTkFont(size=10), text_color=("gray50", "gray55"),
            )
            folder_lbl.pack(fill="x")
            bind_targets.append(folder_lbl)

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

        for widget in bind_targets:
            widget.bind("<Double-Button-1>", lambda _e, v=video: _open_with_default_app(v.path))
            widget.bind("<Button-3>", lambda e, v=video: self._show_context_menu(e, v))

    """Lazily build (once) and return the shared placeholder CTkImage."""
    def _placeholder_image(self) -> ctk.CTkImage:
        if self._placeholder_thumb is None:
            self._placeholder_thumb = ctk.CTkImage(_make_placeholder_image(), size=_THUMB_SIZE)
        return self._placeholder_thumb

    """
    Build one thumbnail-grid card: placeholder (or cached) image, filename,
    folder. Not placed in the grid yet — _layout_thumbnail_grid positions
    every card at once, sized to the pane's current width.
    """
    def _add_thumbnail_card(self, video: lm.VideoFile, *, show_folder: bool = False) -> ctk.CTkFrame:
        card = ctk.CTkFrame(self._video_scroll, fg_color=("gray92", "gray17"), corner_radius=8)

        img_lbl = ctk.CTkLabel(card, text="", image=self._placeholder_image())
        img_lbl.pack(padx=8, pady=(8, 4))
        self._thumb_card_labels[video.path] = img_lbl

        display_name = video.name[:_NAME_MAX] + "…" if len(video.name) > _NAME_MAX else video.name
        name_lbl = ctk.CTkLabel(card, text=display_name, font=ctk.CTkFont(size=12))
        name_lbl.pack(padx=8, pady=(0, 8 if not show_folder else 0))

        bind_targets = [card, img_lbl, name_lbl]
        if show_folder:
            folder_lbl = ctk.CTkLabel(
                card, text=self._folder_label(video),
                font=ctk.CTkFont(size=10), text_color=("gray50", "gray55"),
            )
            folder_lbl.pack(padx=8, pady=(0, 8))
            bind_targets.append(folder_lbl)

        for widget in bind_targets:
            widget.bind("<Double-Button-1>", lambda _e, v=video: _open_with_default_app(v.path))
            widget.bind("<Button-3>", lambda e, v=video: self._show_context_menu(e, v))

        return card

    """
    Return how many card columns fit in the pane's current width, so cards
    wrap into more rows instead of being clipped off the right edge (the
    pane only scrolls vertically). Falls back to 1 before the pane has been
    laid out at all.
    """
    def _thumbnail_columns(self) -> int:
        available = self._video_scroll.winfo_width() - 24  # allow for the scrollbar
        return max(1, available // _THUMB_CARD_SLOT)

    """(Re)position every built thumbnail card into a grid sized to the pane's current width."""
    def _layout_thumbnail_grid(self) -> None:
        if not self._thumb_cards:
            return
        cols = self._thumbnail_columns()
        for col in range(cols):
            self._video_scroll.columnconfigure(col, weight=1)
        for idx, card in enumerate(self._thumb_cards):
            card.grid(row=idx // cols, column=idx % cols, padx=8, pady=8, sticky="n")

    """Re-flow the thumbnail grid when the pane is resized; a no-op in list view."""
    def _on_video_pane_resize(self, _event=None) -> None:
        if self._view_mode == _VIEW_THUMBNAILS:
            self._layout_thumbnail_grid()

    """
    Fill in resolution/duration for `videos` on a daemon thread, then
    re-render via `render` — unless `is_stale()` says the view has since
    moved on (folder changed, or a search started/changed/cleared).
    Shared by the per-folder view and cross-folder search results.
    """
    def _start_metadata_scan(
        self,
        videos: list[lm.VideoFile],
        *,
        is_stale: Callable[[], bool],
        render: Callable[[list[lm.VideoFile]], None],
    ) -> None:
        if not videos:
            return
        threading.Thread(
            target=self._metadata_scan_thread,
            args=(videos, is_stale, render),
            daemon=True,
            name="tbdc-library-metadata",
        ).start()

    """Fill metadata for every video in `videos`, off the UI thread."""
    def _metadata_scan_thread(
        self,
        videos: list[lm.VideoFile],
        is_stale: Callable[[], bool],
        render: Callable[[list[lm.VideoFile]], None],
    ) -> None:
        ffmpeg_path = config_manager.get("ffmpeg_path")
        for video in videos:
            lm.fill_metadata(video, ffmpeg_path)
        self.after(0, lambda: self._on_metadata_scanned(videos, is_stale, render))

    """UI thread: re-render, unless the view has since moved on."""
    def _on_metadata_scanned(
        self,
        videos: list[lm.VideoFile],
        is_stale: Callable[[], bool],
        render: Callable[[list[lm.VideoFile]], None],
    ) -> None:
        if is_stale():
            return
        render(videos)

    # Thumbnails #

    """
    Extract (or fetch from cache) a thumbnail per video on a daemon thread,
    updating each card as its thumbnail becomes ready rather than waiting
    for the whole batch — a no-op unless thumbnail view is active.
    """
    def _start_thumbnail_scan(self, videos: list[lm.VideoFile], *, is_stale: Callable[[], bool]) -> None:
        if self._view_mode != _VIEW_THUMBNAILS or not videos:
            return
        threading.Thread(
            target=self._thumbnail_scan_thread,
            args=(videos, is_stale),
            daemon=True,
            name="tbdc-library-thumbnails",
        ).start()

    """Extract each thumbnail off the UI thread, marshalling one update per file."""
    def _thumbnail_scan_thread(self, videos: list[lm.VideoFile], is_stale: Callable[[], bool]) -> None:
        ffmpeg_path = config_manager.get("ffmpeg_path")
        for video in videos:
            thumb_path = lm.extract_thumbnail(video, ffmpeg_path)
            if thumb_path:
                self.after(0, lambda p=video.path, tp=thumb_path: self._on_thumbnail_ready(p, tp, is_stale))

    """UI thread: swap one card's placeholder for its real thumbnail, unless stale."""
    def _on_thumbnail_ready(self, video_path: str, thumb_path: str, is_stale: Callable[[], bool]) -> None:
        if is_stale():
            return
        label = self._thumb_card_labels.get(video_path)
        if label is None or not label.winfo_exists():
            return

        try:
            from PIL import Image  # soft dependency, matches ui/url_panel.py

            img = Image.open(thumb_path)
            ctk_img = ctk.CTkImage(img, size=_THUMB_SIZE)
        except Exception:
            return

        self._thumb_refs[video_path] = ctk_img   # keep alive — prevents GC blanking the image
        label.configure(image=ctk_img, text="")

    # View mode #

    """Toggle between list and thumbnail view, persist the choice, and re-render."""
    def _on_view_mode_changed(self, label: str) -> None:
        mode = _VIEW_MODES.get(label, _VIEW_LIST)
        if mode == self._view_mode:
            return
        self._view_mode = mode
        config_manager.set("library_view_mode", mode)

        videos = self._last_videos
        self._render_video_rows(videos, self._last_empty_text, show_folder=self._last_show_folder)
        self._start_thumbnail_scan(videos, is_stale=lambda snapshot=videos: snapshot is not self._last_videos)

    # Search #

    """Debounce keystrokes: reschedule the actual search 300 ms after the last one."""
    def _on_search_key(self, _event=None) -> None:
        if self._search_after_id is not None:
            self.after_cancel(self._search_after_id)
        query = self._search_entry.get().strip()
        self._search_after_id = self.after(300, lambda: self._run_search(query))

    """
    Run (or clear) the search. An empty query restores the normal
    selected-folder view; otherwise scan the whole tree on a daemon thread.
    """
    def _run_search(self, query: str) -> None:
        self._search_after_id = None
        self._search_query = query

        if not query:
            self._status_lbl.configure(text="")
            self._select_folder(self._current_dir)
            return

        self._status_lbl.configure(text=f"Searching for '{query}'…")
        threading.Thread(
            target=self._search_thread,
            args=(query,),
            daemon=True,
            name="tbdc-library-search",
        ).start()

    """Runs off the UI thread: recursive, case-insensitive filename search."""
    def _search_thread(self, query: str) -> None:
        results = lm.search_videos(self._root_dir, query)
        self.after(0, lambda: self._on_search_done(query, results))

    """UI thread: render results and kick off metadata fill-in, unless superseded."""
    def _on_search_done(self, query: str, results: list[lm.VideoFile]) -> None:
        if query != self._search_query:
            return  # user kept typing, or cleared the field, before this returned
        self._status_lbl.configure(text="")
        self._render_search_results(results)
        is_stale = lambda q=query: q != self._search_query
        self._start_metadata_scan(results, is_stale=is_stale, render=self._render_search_results)
        self._start_thumbnail_scan(results, is_stale=is_stale)

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
