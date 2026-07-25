"""
ui/converter_panel.py

File converter panel — mounted in the "Convert" tab of the main window.

Left pane picks the source files, the output folder and the target format;
the right sidebar shows one row per conversion job with its own progress bar
and cancel button.  Owns a ConversionQueue and polls its update_queue every
100 ms, the same way DownloadPanel polls QueueManager.

Two contracts inherited from core/ that shape this file:
  • A job's `percent` is None when the source duration is unknown, so the row
    shows an indeterminate bar and treats "finished" — not a 100% progress
    message — as completion.
  • ConversionQueue advances itself once primed, so start_next() is called
    once after enqueuing and never again from a terminal message.

Public API:
ConverterPanel(master)
status_text(status, percent)          – row status caption for a job state
truncate_filename(name, max_len)      – middle-elided name for a narrow row
resolve_convert_dir(configured, fallback) – Convert-tab output folder
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from tkinter import filedialog

import customtkinter as ctk

from core import config_manager, converter
from core.conversion_queue import ConversionQueue

# Colours: match the app palette #
_BDX        = ("#791F1F", "#A32D2D")
_BDX_HOVER  = ("#5C1418", "#C44040")
_BDX_TEXT   = "#FCEBEB"
_MUTED      = ("gray50", "gray60")   # de-emphasised body text
_HEADING    = ("gray40", "gray55")
_ERR_COLOR  = ("#A32D2D", "#E24B4A")
_OK_COLOR   = ("#3B6D11", "#639922")
_WARN_COLOR = ("#92400E", "#FBBF24")
_DIVIDER    = ("gray88", "gray22")

_POLL_MS   = 100
_NAME_MAX  = 30   # chars before a filename is elided in a row
_SOURCE_EXT = ".webm"

_CONVERT_DIR_KEY = "convert_output_dir"
_OUTPUT_DIR_KEY  = "output_dir"


# Pure helpers: testable without a display #

"""
Return the folder the Convert tab should write to.

`configured` is the convert_output_dir config value and wins when set;
otherwise conversions follow the shared download `fallback` folder. The two
are kept separate so picking a folder here never retargets downloads.
"""
def resolve_convert_dir(configured, fallback: str) -> str:
    if isinstance(configured, str) and configured.strip():
        return configured
    return fallback


"""Row caption for a job state; percent may be None (unknown duration)."""
def status_text(status: str, percent: float | None = None) -> str:
    if status == "converting":
        return "converting…" if percent is None else f"converting {percent:.0f}%"
    return {
        "pending":   "pending",
        "done":      "✓  done",
        "error":     "error",
        "cancelled": "cancelled",
    }.get(status, status)


"""Middle-elide `name` so both the stem and the extension stay readable."""
def truncate_filename(name: str, max_len: int = _NAME_MAX) -> str:
    if max_len <= 1 or len(name) <= max_len:
        return name
    keep = max_len - 1
    head = (keep + 1) // 2
    tail = keep - head
    return name[:head] + "…" + (name[len(name) - tail:] if tail else "")


# Data model #

@dataclass
class _JobState:
    job_id:    str
    name:      str
    status:    str            = "pending"
    percent:   float | None   = 0.0
    error_msg: str            = ""


# Job row #

"""One conversion job: name, progress bar, status caption, cancel button."""
class _JobRow(ctk.CTkFrame):

    def __init__(self, master, state: _JobState, on_cancel, **kwargs) -> None:
        super().__init__(master, corner_radius=8, border_width=1, **kwargs)
        self._state     = state
        self._on_cancel = on_cancel
        self._spinning  = False
        self._build()
        self.refresh()

    def _build(self) -> None:
        top = ctk.CTkFrame(self, fg_color="transparent")
        top.pack(fill="x", padx=10, pady=(8, 2))
        top.columnconfigure(0, weight=1)

        self._name_lbl = ctk.CTkLabel(
            top, text="", anchor="w",
            font=ctk.CTkFont(size=12, weight="bold"),
        )
        self._name_lbl.grid(row=0, column=0, sticky="ew")

        self._cancel_btn = ctk.CTkButton(
            top, text="✕", width=28, height=28,
            fg_color="transparent",
            border_width=1,
            border_color=("gray70", "gray40"),
            text_color=("gray40", "gray60"),
            hover_color=("gray90", "gray20"),
            font=ctk.CTkFont(size=12),
            command=self._on_cancel,
        )
        self._cancel_btn.grid(row=0, column=1, padx=(6, 0))

        self._prog_bar = ctk.CTkProgressBar(
            self, progress_color=_BDX, height=3, corner_radius=2,
        )
        self._prog_bar.set(0)
        # Packed/forgotten dynamically in refresh()

        self._status_lbl = ctk.CTkLabel(
            self, text="", anchor="w",
            font=ctk.CTkFont(size=11),
            text_color=_MUTED,
            wraplength=240,
            justify="left",
        )
        self._status_lbl.pack(fill="x", padx=10, pady=(0, 8))

    """Start or stop the marching bar used when percent is unknown."""
    def _set_indeterminate(self, on: bool) -> None:
        if on and not self._spinning:
            self._prog_bar.configure(mode="indeterminate")
            self._prog_bar.start()
            self._spinning = True
        elif not on and self._spinning:
            self._prog_bar.stop()
            self._prog_bar.configure(mode="determinate")
            self._spinning = False

    """Re-render from the current _JobState."""
    def refresh(self) -> None:
        s = self._state
        self._name_lbl.configure(text=truncate_filename(s.name))

        if s.status == "converting":
            self.configure(border_color=_BDX, fg_color=("gray98", "gray13"))
            self._prog_bar.pack(fill="x", padx=10, pady=(0, 4))
            if s.percent is None:
                self._set_indeterminate(True)
            else:
                self._set_indeterminate(False)
                self._prog_bar.set(max(0.0, min(1.0, s.percent / 100)))
            self._status_lbl.configure(
                text=status_text(s.status, s.percent), text_color=_MUTED,
            )
            self._cancel_btn.configure(state="normal")
            return

        self._set_indeterminate(False)
        self._prog_bar.pack_forget()
        self._cancel_btn.configure(state="normal")

        if s.status == "done":
            self.configure(border_color=_OK_COLOR, fg_color=("gray98", "gray13"))
            self._status_lbl.configure(text=status_text(s.status), text_color=_OK_COLOR)
        elif s.status == "error":
            self.configure(border_color=_ERR_COLOR, fg_color=("gray98", "gray13"))
            detail = s.error_msg[:160] + "…" if len(s.error_msg) > 160 else s.error_msg
            self._status_lbl.configure(
                text=f"{status_text(s.status)}: {detail}" if detail else status_text(s.status),
                text_color=_ERR_COLOR,
            )
        elif s.status == "cancelled":
            self.configure(border_color=("gray75", "gray32"), fg_color=("gray95", "gray11"))
            self._status_lbl.configure(text=status_text(s.status), text_color=_MUTED)
        else:
            self.configure(border_color=("gray78", "gray32"), fg_color=("gray98", "gray13"))
            self._status_lbl.configure(text=status_text(s.status), text_color=_MUTED)


# Main panel #

"""
File converter.

master   parent widget
"""
class ConverterPanel(ctk.CTkFrame):

    def __init__(self, master, **kwargs) -> None:
        super().__init__(master, fg_color="transparent", **kwargs)

        self._queue = ConversionQueue(max_concurrent=1)
        self._sources: list[str] = []
        self._output_dir = resolve_convert_dir(
            config_manager.get(_CONVERT_DIR_KEY),
            config_manager.get(_OUTPUT_DIR_KEY),
        )
        self._overwrite_approved = False
        self._states: dict[str, _JobState] = {}
        self._rows:   dict[str, _JobRow]   = {}

        self._build()
        self._poll()

    # Layout #

    """Left pane (sources, folder, format), separator, right job sidebar."""
    def _build(self) -> None:
        content = ctk.CTkFrame(self, fg_color="transparent")
        content.pack(fill="both", expand=True)
        content.grid_rowconfigure(0, weight=1)
        content.grid_columnconfigure(0, weight=1)

        left = ctk.CTkScrollableFrame(
            content,
            fg_color=("white", "gray10"),
            label_text="",
            scrollbar_button_color=("gray80", "gray30"),
        )
        left.grid(row=0, column=0, sticky="nsew")
        self._build_inputs(left)

        ctk.CTkFrame(content, width=1, fg_color=_DIVIDER).grid(row=0, column=1, sticky="ns")

        sidebar = ctk.CTkFrame(
            content, fg_color=("gray97", "gray11"), width=280, corner_radius=0,
        )
        sidebar.grid(row=0, column=2, sticky="nsew")
        sidebar.grid_propagate(False)
        self._build_sidebar(sidebar)

    """Small bold all-caps section heading."""
    def _section(self, parent, text: str, pady=(0, 8)) -> None:
        ctk.CTkLabel(
            parent, text=text,
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=_HEADING, anchor="w",
        ).pack(anchor="w", pady=pady)

    """Source list, output folder, target format, Convert button."""
    def _build_inputs(self, parent) -> None:
        wrap = ctk.CTkFrame(parent, fg_color="transparent")
        wrap.pack(fill="both", expand=True, padx=24, pady=(22, 24))

        # Source files
        self._section(wrap, "SOURCE FILES")

        btn_row = ctk.CTkFrame(wrap, fg_color="transparent")
        btn_row.pack(fill="x", pady=(0, 8))

        ctk.CTkButton(
            btn_row, text="+ Add files…", width=120, height=32,
            fg_color=_BDX, hover_color=_BDX_HOVER, text_color=_BDX_TEXT,
            font=ctk.CTkFont(size=12, weight="bold"),
            command=self._pick_files,
        ).pack(side="left")

        ctk.CTkButton(
            btn_row, text="Clear all", width=80, height=32,
            fg_color="transparent", border_width=1,
            border_color=("gray70", "gray40"),
            text_color=("gray40", "gray60"),
            hover_color=("gray90", "gray20"),
            font=ctk.CTkFont(size=12),
            command=self._clear_sources,
        ).pack(side="left", padx=(8, 0))

        self._files_frame = ctk.CTkFrame(wrap, fg_color="transparent")
        self._files_frame.pack(fill="x", pady=(0, 4))

        self._empty_lbl = ctk.CTkLabel(
            wrap,
            text=f"No files selected — add one or more {_SOURCE_EXT} files.",
            font=ctk.CTkFont(size=11), text_color=_MUTED, anchor="w",
        )
        self._empty_lbl.pack(fill="x", pady=(0, 8))

        ctk.CTkFrame(wrap, height=1, fg_color=_DIVIDER).pack(fill="x", pady=14)

        # Output folder
        self._section(wrap, "OUTPUT FOLDER", pady=(0, 6))
        dir_row = ctk.CTkFrame(wrap, fg_color="transparent")
        dir_row.pack(fill="x", pady=(0, 4))
        dir_row.columnconfigure(0, weight=1)

        self._dir_lbl = ctk.CTkLabel(
            dir_row, text=self._output_dir,
            font=ctk.CTkFont(size=11), text_color=("gray40", "gray60"), anchor="w",
        )
        self._dir_lbl.grid(row=0, column=0, sticky="ew", padx=(0, 8))

        ctk.CTkButton(
            dir_row, text="Browse…", width=80, height=28,
            fg_color=("gray80", "gray25"), hover_color=("gray70", "gray35"),
            text_color=("gray20", "gray80"), font=ctk.CTkFont(size=12),
            command=self._pick_output_dir,
        ).grid(row=0, column=1, sticky="e")

        ctk.CTkLabel(
            wrap,
            text="Separate from the download folder — changing it here leaves downloads alone.",
            font=ctk.CTkFont(size=11), text_color=_MUTED, anchor="w",
        ).pack(fill="x", pady=(0, 14))

        # Target format
        self._section(wrap, "CONVERT TO", pady=(0, 4))
        self._format_menu = ctk.CTkOptionMenu(
            wrap, values=converter.supported_formats(), dynamic_resizing=False,
        )
        self._format_menu.set(converter.supported_formats()[0])
        self._format_menu.pack(fill="x", pady=(0, 14))

        # Conflict warning (hidden until a collision is detected)
        self._warning_row = ctk.CTkFrame(wrap, fg_color="transparent")
        self._warning_lbl = ctk.CTkLabel(
            self._warning_row, text="",
            font=ctk.CTkFont(size=11), text_color=_WARN_COLOR,
            anchor="w", wraplength=380, justify="left",
        )
        self._warning_lbl.pack(fill="x", expand=True)

        warn_btns = ctk.CTkFrame(self._warning_row, fg_color="transparent")
        warn_btns.pack(fill="x", pady=(6, 0))

        _warn_kw = dict(
            width=100, height=26, fg_color="transparent", border_width=1,
            border_color=("gray70", "gray40"), text_color=("gray40", "gray60"),
            hover_color=("gray90", "gray20"), font=ctk.CTkFont(size=11),
        )
        ctk.CTkButton(
            warn_btns, text="Auto-number",
            command=lambda: self._start_conversion(overwrite=False), **_warn_kw,
        ).pack(side="left")
        ctk.CTkButton(
            warn_btns, text="Overwrite",
            command=lambda: self._start_conversion(overwrite=True), **_warn_kw,
        ).pack(side="left", padx=(8, 0))

        # Convert
        self._convert_btn = ctk.CTkButton(
            wrap, text="⇄  Convert", height=40,
            fg_color=_BDX, hover_color=_BDX_HOVER, text_color=_BDX_TEXT,
            font=ctk.CTkFont(size=14, weight="bold"),
            command=self._on_convert,
        )
        self._convert_btn.pack(fill="x")

        self._status_lbl = ctk.CTkLabel(
            wrap, text="", font=ctk.CTkFont(size=11), text_color=_MUTED,
            anchor="w", wraplength=380, justify="left",
        )
        self._status_lbl.pack(fill="x", pady=(10, 0))

    """Right sidebar: job count badge and one row per conversion."""
    def _build_sidebar(self, parent) -> None:
        inner = ctk.CTkFrame(parent, fg_color="transparent")
        inner.pack(fill="both", expand=True, padx=14, pady=14)

        hdr = ctk.CTkFrame(inner, fg_color="transparent")
        hdr.pack(fill="x", pady=(0, 8))

        ctk.CTkLabel(
            hdr, text="Conversions",
            font=ctk.CTkFont(size=13, weight="bold"), anchor="w",
        ).pack(side="left")

        self._count_badge = ctk.CTkLabel(
            hdr, text="0",
            font=ctk.CTkFont(size=11, weight="bold"),
            fg_color=("#F7C1C1", "#5C1418"), text_color=("#791F1F", "#F7C1C1"),
            corner_radius=10, padx=8, pady=1,
        )
        self._count_badge.pack(side="left", padx=(6, 0))

        ctk.CTkButton(
            hdr, text="Clear done", width=80, height=24,
            fg_color="transparent", text_color=_MUTED,
            hover_color=("gray90", "gray20"), font=ctk.CTkFont(size=11),
            command=self._clear_done,
        ).pack(side="right")

        self._scroll = ctk.CTkScrollableFrame(inner, fg_color="transparent", label_text="")
        self._scroll.pack(fill="both", expand=True)

    # Source files #

    """Multi-select picker filtered to the source extension."""
    def _pick_files(self) -> None:
        paths = filedialog.askopenfilenames(
            parent=self.winfo_toplevel(),
            title="Choose files to convert",
            filetypes=[("WebM video", f"*{_SOURCE_EXT}"), ("All files", "*.*")],
            initialdir=self._output_dir,
        )
        added = [p for p in paths if p not in self._sources]
        self._sources.extend(added)
        self._overwrite_approved = False
        self._hide_warning()
        self._refresh_file_list()
        if added:
            self._set_status(f"{len(self._sources)} file(s) selected.", _MUTED)

    def _remove_source(self, path: str) -> None:
        if path in self._sources:
            self._sources.remove(path)
            self._hide_warning()
            self._refresh_file_list()

    def _clear_sources(self) -> None:
        self._sources.clear()
        self._hide_warning()
        self._refresh_file_list()
        self._set_status("", _MUTED)

    """Rebuild the selected-file rows from self._sources."""
    def _refresh_file_list(self) -> None:
        for child in self._files_frame.winfo_children():
            child.destroy()

        if not self._sources:
            self._empty_lbl.pack(fill="x", pady=(0, 8))
            return
        self._empty_lbl.pack_forget()

        for path in self._sources:
            row = ctk.CTkFrame(self._files_frame, fg_color="transparent")
            row.pack(fill="x", pady=1)
            row.columnconfigure(0, weight=1)

            ctk.CTkLabel(
                row, text=truncate_filename(Path(path).name, 46),
                font=ctk.CTkFont(size=12), anchor="w",
            ).grid(row=0, column=0, sticky="ew")

            ctk.CTkButton(
                row, text="✕", width=24, height=24,
                fg_color="transparent", text_color=_MUTED,
                hover_color=("gray90", "gray20"), font=ctk.CTkFont(size=11),
                command=lambda p=path: self._remove_source(p),
            ).grid(row=0, column=1)

    # Output folder #

    """
    Pick the conversion output folder.

    Persisted under convert_output_dir, deliberately NOT output_dir — the
    Download tab reads that one, and choosing a folder here must not move
    where downloads land.
    """
    def _pick_output_dir(self) -> None:
        path = filedialog.askdirectory(
            parent=self.winfo_toplevel(),
            title="Choose conversion output folder",
            initialdir=self._output_dir,
        )
        if path:
            self._output_dir = path
            self._dir_lbl.configure(text=path)
            config_manager.set(_CONVERT_DIR_KEY, path)
            self._hide_warning()

    # Status / warning #

    def _set_status(self, text: str, color) -> None:
        self._status_lbl.configure(text=text, text_color=color)

    def _hide_warning(self) -> None:
        self._warning_row.pack_forget()

    """Show the collision prompt above the Convert button."""
    def _show_warning(self, names: list[str]) -> None:
        shown = ", ".join(names[:3]) + ("…" if len(names) > 3 else "")
        self._warning_lbl.configure(
            text=f"{len(names)} output file(s) already exist ({shown}). "
                 "Auto-number them, or overwrite the existing files?"
        )
        self._warning_row.pack(fill="x", pady=(0, 12), before=self._convert_btn)

    # Convert #

    """
    Validate, check for collisions, then enqueue.

    ffmpeg is resolved here rather than in __init__: the panel is built for
    every startup regardless of which tab is showing, and a raise there would
    take the whole window down.
    """
    def _on_convert(self) -> None:
        self._hide_warning()

        if not self._sources:
            self._set_status(f"Add at least one {_SOURCE_EXT} file first.", _ERR_COLOR)
            return

        try:
            converter.resolve_ffmpeg_binary(config_manager.get("ffmpeg_path"))
        except RuntimeError as exc:
            self._set_status(str(exc), _ERR_COLOR)
            return

        target_format = self._format_menu.get()
        collisions = [
            Path(p).name for p in self._sources
            if converter.plan_output_path(
                p, self._output_dir, target_format,
            ).exists()
        ]

        if collisions:
            self._show_warning(collisions)
            return

        self._start_conversion(overwrite=False)

    """Enqueue every selected file, then prime the queue exactly once."""
    def _start_conversion(self, *, overwrite: bool) -> None:
        self._hide_warning()
        target_format = self._format_menu.get()

        try:
            os.makedirs(self._output_dir, exist_ok=True)
        except OSError as exc:
            self._set_status(f"Cannot use output folder: {exc}", _ERR_COLOR)
            return

        queued = 0
        for source in self._sources:
            output = converter.plan_output_path(
                source, self._output_dir, target_format, overwrite=overwrite,
            )
            job_id = self._queue.add(source, str(output), target_format)
            self._states[job_id] = _JobState(job_id=job_id, name=Path(output).name)
            queued += 1

        self._sources.clear()
        self._refresh_file_list()
        self._overwrite_approved = False

        # ConversionQueue advances itself from its worker threads, so this is
        # primed once and never called again from a terminal message — see
        # core/conversion_queue.py's _run_job.
        self._queue.start_next()

        note = "overwriting existing files" if overwrite else "auto-numbering any clashes"
        self._set_status(f"Queued {queued} file(s) — {note}.", _MUTED)

    # Polling #

    """Drain update_queue and apply all pending messages."""
    def _poll(self) -> None:
        try:
            while True:
                self._process(self._queue.update_queue.get_nowait())
        except Exception:
            pass
        self.after(_POLL_MS, self._poll)

    def _process(self, msg: dict) -> None:
        t      = msg.get("type", "")
        job_id = msg.get("job_id", "")
        state  = self._states.get(job_id)
        if state is None:
            return

        if t == "job_added":
            self._build_row(job_id)

        elif t == "status_change":
            state.status = msg.get("status", state.status)
            self._refresh_row(job_id)

        elif t == "progress":
            state.status  = "converting"
            state.percent = msg.get("percent")
            self._refresh_row(job_id)

        elif t == "finished":
            # "finished" is completion — with an unknown duration there is no
            # 100% progress message to wait for.
            state.status  = "done"
            state.percent = 100.0
            self._refresh_row(job_id)

        elif t == "error":
            state.status    = "error"
            state.error_msg = msg.get("message", "Unknown error")
            self._refresh_row(job_id)
            self._set_status(f"Error: {state.error_msg[:120]}", _ERR_COLOR)

        self._update_count()

    # Rows #

    def _build_row(self, job_id: str) -> None:
        if job_id in self._rows:
            return
        row = _JobRow(
            self._scroll,
            state=self._states[job_id],
            on_cancel=lambda jid=job_id: self._cancel(jid),
        )
        row.pack(fill="x", pady=(0, 6))
        self._rows[job_id] = row

    def _refresh_row(self, job_id: str) -> None:
        if job_id in self._rows:
            self._rows[job_id].refresh()

    def _update_count(self) -> None:
        active = sum(
            1 for s in self._states.values() if s.status in ("pending", "converting")
        )
        self._count_badge.configure(text=str(active))

    """Cancel a running job, or drop a finished row from the list."""
    def _cancel(self, job_id: str) -> None:
        state = self._states.get(job_id)
        if state is None:
            return
        if state.status in ("done", "error", "cancelled"):
            self._rows.pop(job_id).destroy()
            del self._states[job_id]
            self._update_count()
        else:
            self._queue.cancel(job_id)

    def _clear_done(self) -> None:
        for jid in [
            j for j, s in self._states.items()
            if s.status in ("done", "error", "cancelled")
        ]:
            self._rows.pop(jid).destroy()
            del self._states[jid]
        self._update_count()
