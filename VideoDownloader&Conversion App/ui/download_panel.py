"""
ui/download_panel.py

Download queue sidebar.

Polls QueueManager.update_queue every 100 ms and renders one _JobRow
per job.  Each row shows:
  • Title (truncated with hover tooltip showing full text)
  • Format label
  • Progress bar + speed / ETA  (downloading state only)
  • Status badge
  • Pause / Resume button
  • Cancel / Remove button
  • Up / Down buttons to reorder pending jobs

Pause semantics:
"Pause" cancels the active download (via QueueManager.cancel) but
keeps the job in the queue as "pending" again, so Start-next won't
fire automatically.  "Resume" moves the paused job to the front of
the pending block and calls start_next().

Public API:
DownloadPanel(master, queue_manager)
DownloadPanel.set_job_title(job_id, title)   – call right after qm.add()
"""

from __future__ import annotations

import re
import tkinter as tk
from dataclasses import dataclass, field
from typing import Callable, Optional

import customtkinter as ctk

from core.queue_manager import QueueManager, Status

# Constants #
_BDX        = ("#791F1F", "#A32D2D")
_BDX_HOVER  = ("#5C1418", "#C44040")
_BDX_TEXT   = "#FCEBEB"
_BADGE_FG   = ("#F7C1C1", "#5C1418")
_BADGE_TEXT = ("#791F1F", "#F7C1C1")
_POLL_MS    = 100
_TITLE_MAX  = 34   # chars before truncation in the row label


# Data model #

@dataclass
class _JobState:
    job_id:   str
    title:    str    = "Fetching title…"
    fmt_info: str    = ""
    status:   Status = "pending"
    progress: float  = 0.0
    speed:     str    = ""
    eta:       str    = ""
    paused:    bool   = False   # True when cancelled-via-pause
    error_msg: str    = ""


# Tooltip helper #
"""Lightweight hover tooltip for any widget."""
class _Tooltip:

    def __init__(self, widget: tk.Widget, text: str) -> None:
        self._widget = widget
        self._text   = text
        self._win: Optional[tk.Toplevel] = None
        widget.bind("<Enter>",  self._show, add="+")
        widget.bind("<Leave>",  self._hide, add="+")
        widget.bind("<Motion>", self._move, add="+")

    def update_text(self, text: str) -> None:
        self._text = text

    def _show(self, event: tk.Event) -> None:
        if self._win or not self._text:
            return
        self._win = tk.Toplevel(self._widget)
        self._win.wm_overrideredirect(True)
        self._win.wm_geometry(f"+{event.x_root + 14}+{event.y_root + 6}")
        ctk.CTkLabel(
            self._win,
            text=self._text,
            fg_color=("gray15", "gray85"),
            text_color=("gray90", "gray10"),
            corner_radius=4,
            padx=8,
            pady=4,
            font=ctk.CTkFont(size=11),
        ).pack()

    def _hide(self, event: tk.Event = None) -> None:  # type: ignore[assignment]
        if self._win:
            self._win.destroy()
            self._win = None

    def _move(self, event: tk.Event) -> None:
        if self._win:
            self._win.wm_geometry(f"+{event.x_root + 14}+{event.y_root + 6}")


# Job row widget #
"""
A single queue entry.  Reads from a shared _JobState object and
re-renders on refresh().
"""
class _JobRow(ctk.CTkFrame):
    def __init__(
        self,
        master,
        state:          _JobState,
        on_pause_resume: callable,
        on_cancel:       callable,
        on_move_up:      callable,
        on_move_down:    callable,
        **kwargs,
    ) -> None:
        super().__init__(master, corner_radius=8, border_width=1, **kwargs)
        self._state          = state
        self._on_pause_resume = on_pause_resume
        self._on_cancel      = on_cancel
        self._on_move_up     = on_move_up
        self._on_move_down   = on_move_down
        self._build()
        self.refresh()

    def _build(self) -> None:
        # Top row #
        top = ctk.CTkFrame(self, fg_color="transparent")
        top.pack(fill="x", padx=10, pady=(8, 2))
        top.columnconfigure(1, weight=1)

        # Reorder 
        reorder_col = ctk.CTkFrame(top, fg_color="transparent")
        reorder_col.grid(row=0, column=0, padx=(0, 6))

        btn_kw = dict(
            width=18, height=14,
            fg_color="transparent",
            text_color=("gray60", "gray45"),
            hover_color=("gray88", "gray22"),
            font=ctk.CTkFont(size=9),
        )
        ctk.CTkButton(reorder_col, text="▲", command=self._on_move_up,  **btn_kw).pack()
        ctk.CTkButton(reorder_col, text="▼", command=self._on_move_down, **btn_kw).pack()

        # Title + format
        meta_col = ctk.CTkFrame(top, fg_color="transparent")
        meta_col.grid(row=0, column=1, sticky="ew")

        self._title_lbl = ctk.CTkLabel(
            meta_col,
            text="",
            anchor="w",
            font=ctk.CTkFont(size=12, weight="bold"),
        )
        self._title_lbl.pack(fill="x")
        self._title_tooltip = _Tooltip(self._title_lbl, "")

        self._fmt_lbl = ctk.CTkLabel(
            meta_col,
            text="",
            anchor="w",
            font=ctk.CTkFont(size=11),
            text_color=("gray50", "gray60"),
        )
        self._fmt_lbl.pack(fill="x")

        # Pause / Resume + Cancel
        actions = ctk.CTkFrame(top, fg_color="transparent")
        actions.grid(row=0, column=2, padx=(6, 0))

        btn2_kw = dict(
            width=28, height=28,
            fg_color="transparent",
            border_width=1,
            border_color=("gray70", "gray40"),
            text_color=("gray40", "gray60"),
            hover_color=("gray90", "gray20"),
            font=ctk.CTkFont(size=12),
        )
        self._pause_btn = ctk.CTkButton(
            actions, text="⏸", command=self._on_pause_resume, **btn2_kw
        )
        self._pause_btn.pack(side="left", padx=(0, 4))

        self._cancel_btn = ctk.CTkButton(
            actions, text="✕", command=self._on_cancel, **btn2_kw
        )
        self._cancel_btn.pack(side="left")

        # Progress bar #
        self._prog_bar = ctk.CTkProgressBar(
            self,
            progress_color=_BDX,
            height=3,
            corner_radius=2,
        )
        self._prog_bar.set(0)
        # Packed/forgotten dynamically in refresh()

        # Bottom row: speed + ETA #
        bottom = ctk.CTkFrame(self, fg_color="transparent")
        bottom.pack(fill="x", padx=10, pady=(0, 8))

        self._speed_lbl = ctk.CTkLabel(
            bottom, text="",
            font=ctk.CTkFont(size=11),
            text_color=("gray50", "gray60"),
        )
        self._speed_lbl.pack(side="left")

        self._eta_lbl = ctk.CTkLabel(
            bottom, text="",
            font=ctk.CTkFont(size=11),
            text_color=("gray60", "gray50"),
        )
        self._eta_lbl.pack(side="right")

    # Refresh #
    """Re-render from the current _JobState."""
    def refresh(self) -> None:
        s = self._state

        # Title (truncated display + tooltip with full text)
        display = s.title[:_TITLE_MAX] + "…" if len(s.title) > _TITLE_MAX else s.title
        self._title_lbl.configure(text=display)
        self._title_tooltip.update_text(s.title)
        self._fmt_lbl.configure(text=s.fmt_info)

        status = s.status
        paused = s.paused

        if status == "downloading":
            self.configure(
                border_color=_BDX,
                fg_color=("gray98", "gray13"),
            )
            self._prog_bar.pack(fill="x", padx=10, pady=(0, 4))
            self._prog_bar.set(max(0.0, min(1.0, s.progress / 100)))
            self._speed_lbl.configure(
                text=f"{s.progress:.0f}%  ·  {s.speed}",
                text_color=("gray45", "gray55"),
            )
            self._eta_lbl.configure(text=f"ETA {s.eta}")
            self._pause_btn.configure(text="⏸", state="normal")
            self._cancel_btn.configure(state="normal")

        elif status == "pending" and paused:
            self.configure(
                border_color=("gray70", "gray35"),
                fg_color=("gray98", "gray13"),
            )
            self._prog_bar.pack_forget()
            self._speed_lbl.configure(
                text="paused",
                text_color=("gray55", "gray50"),
            )
            self._eta_lbl.configure(text="")
            self._pause_btn.configure(text="▶", state="normal")
            self._cancel_btn.configure(state="normal")

        elif status == "pending":
            self.configure(
                border_color=("gray78", "gray32"),
                fg_color=("gray98", "gray13"),
            )
            self._prog_bar.pack_forget()
            self._speed_lbl.configure(
                text="pending",
                text_color=("gray60", "gray48"),
            )
            self._eta_lbl.configure(text="")
            self._pause_btn.configure(text="⏸", state="disabled")
            self._cancel_btn.configure(state="normal")

        elif status == "done":
            self.configure(
                border_color=("#3B6D11", "#639922"),
                fg_color=("gray98", "gray13"),
            )
            self._prog_bar.pack_forget()
            self._speed_lbl.configure(
                text="✓  done",
                text_color=("#3B6D11", "#639922"),
            )
            self._eta_lbl.configure(text="")
            self._pause_btn.configure(state="disabled")
            self._cancel_btn.configure(text="✕", state="normal")

        elif status == "error":
            self.configure(
                border_color=("#A32D2D", "#E24B4A"),
                fg_color=("gray98", "gray13"),
            )
            self._prog_bar.pack_forget()
            self._speed_lbl.configure(
                text="error",
                text_color=("#A32D2D", "#E24B4A"),
            )
            self._eta_lbl.configure(text="")
            self._pause_btn.configure(state="disabled")
            self._cancel_btn.configure(text="✕", state="normal")

        elif status == "cancelled":
            self.configure(
                border_color=("gray75", "gray32"),
                fg_color=("gray95", "gray11"),
            )
            self._prog_bar.pack_forget()
            self._speed_lbl.configure(
                text="cancelled",
                text_color=("gray60", "gray45"),
            )
            self._eta_lbl.configure(text="")
            self._pause_btn.configure(state="disabled")
            self._cancel_btn.configure(text="✕", state="normal")


# Main panel #
"""
Queue sidebar.  Polls QueueManager.update_queue and renders _JobRow
widgets inside a scrollable frame.
"""
class DownloadPanel(ctk.CTkFrame):
    

    def __init__(
        self,
        master,
        queue_manager: QueueManager,
        on_download: Optional[Callable[[], None]] = None,
        **kwargs,
    ) -> None:
        super().__init__(master, fg_color="transparent", **kwargs)
        self._qm             = queue_manager
        self._on_download_cb = on_download
        self._states: dict[str, _JobState] = {}
        self._rows:   dict[str, _JobRow]   = {}
        self._order:  list[str]            = []   # display / priority order
        self._pending_titles: dict[str, str] = {}  # pre-registered titles

        self._build()
        self._poll()

    # Public #
    """
    Set a human-readable title for a job.
    Safe to call before the job_added message is processed.
    """
    def set_job_title(self, job_id: str, title: str) -> None:
        
        if job_id in self._states:
            self._states[job_id].title = title
            self._refresh_row(job_id)
        else:
            self._pending_titles[job_id] = title

    # Layout #

    def _build(self) -> None:
        # Header
        hdr = ctk.CTkFrame(self, fg_color="transparent")
        hdr.pack(fill="x", pady=(0, 8))

        ctk.CTkLabel(
            hdr,
            text="Queue",
            font=ctk.CTkFont(size=13, weight="bold"),
            anchor="w",
        ).pack(side="left")

        self._count_badge = ctk.CTkLabel(
            hdr,
            text="0",
            font=ctk.CTkFont(size=11, weight="bold"),
            fg_color=_BADGE_FG,
            text_color=_BADGE_TEXT,
            corner_radius=10,
            padx=8,
            pady=1,
        )
        self._count_badge.pack(side="left", padx=(6, 0))

        ctk.CTkButton(
            hdr,
            text="Clear done",
            width=80,
            height=24,
            fg_color="transparent",
            text_color=("gray50", "gray55"),
            hover_color=("gray90", "gray20"),
            font=ctk.CTkFont(size=11),
            command=self._clear_done,
        ).pack(side="right")

        # Download button
        self._dl_btn = ctk.CTkButton(
            self,
            text="⬇  Download",
            height=36,
            fg_color=_BDX,
            hover_color=_BDX_HOVER,
            text_color=_BDX_TEXT,
            font=ctk.CTkFont(size=13, weight="bold"),
            command=self._trigger_download,
            state="normal" if self._on_download_cb else "disabled",
        )
        self._dl_btn.pack(fill="x", pady=(0, 8))

        # Scrollable job list
        self._scroll = ctk.CTkScrollableFrame(
            self,
            fg_color="transparent",
            label_text="",
        )
        self._scroll.pack(fill="both", expand=True)

        # Status / error label (below active progress bar)
        self._status_lbl = ctk.CTkLabel(
            self,
            text="",
            font=ctk.CTkFont(size=11),
            text_color=("gray50", "gray55"),
            anchor="w",
            wraplength=240,
            justify="left",
        )
        self._status_lbl.pack(fill="x", pady=(4, 0))

        # Footer hint
        ctk.CTkLabel(
            self,
            text="▲ ▼  to reorder pending jobs",
            font=ctk.CTkFont(size=11),
            text_color=("gray62", "gray48"),
        ).pack(pady=(6, 0))

    def _trigger_download(self) -> None:
        if self._on_download_cb:
            self._on_download_cb()

    # Polling #
    """Drain update_queue and apply all pending messages."""
    def _poll(self) -> None:
        
        try:
            while True:
                msg = self._qm.update_queue.get_nowait()
                self._process(msg)
        except Exception:
            pass
        self.after(_POLL_MS, self._poll)

    def _process(self, msg: dict) -> None:
        t      = msg.get("type", "")
        job_id = msg.get("job_id", "")

        if t == "job_added":
            job = next((j for j in self._qm.jobs if j.id == job_id), None)
            if not job:
                return
            title = self._pending_titles.pop(job_id, job.url)
            state = _JobState(
                job_id   = job_id,
                title    = title,
                fmt_info = _fmt_label(job.format_string),
                status   = "pending",
            )
            self._states[job_id] = state
            self._order.append(job_id)
            self._build_row(job_id)
            self._update_count()

        elif t == "status_change":
            if job_id not in self._states:
                return
            new_status = msg.get("status", "")
            state      = self._states[job_id]

            # A cancel triggered by pause should stay as "pending"
            if new_status == "cancelled" and state.paused:
                state.status = "pending"
            else:
                state.status = new_status  # type: ignore[assignment]

            self._refresh_row(job_id)
            self._update_count()

        elif t == "progress":
            if job_id not in self._states:
                return
            s          = self._states[job_id]
            s.status   = "downloading"
            s.progress = msg.get("percent", 0.0)
            s.speed    = msg.get("speed", "")
            s.eta      = msg.get("eta", "")
            self._refresh_row(job_id)
            speed_str = f"  ·  {s.speed}" if s.speed else ""
            self._status_lbl.configure(
                text=f"Downloading {s.progress:.0f}%{speed_str}",
                text_color=("gray45", "gray55"),
            )

        elif t == "finished":
            if job_id not in self._states:
                return
            s          = self._states[job_id]
            s.status   = "done"
            s.progress = 100.0
            s.paused   = False
            self._refresh_row(job_id)
            self._update_count()
            self._status_lbl.configure(text="", text_color=("gray50", "gray55"))
            self._qm.start_next()

        elif t == "error":
            if job_id not in self._states:
                return
            s           = self._states[job_id]
            s.status    = "error"
            s.paused    = False
            s.error_msg = msg.get("message", "Unknown error")
            self._refresh_row(job_id)
            self._update_count()
            short = s.error_msg[:120] + "…" if len(s.error_msg) > 120 else s.error_msg
            self._status_lbl.configure(
                text=f"Error: {short}",
                text_color=("#A32D2D", "#E24B4A"),
            )
            self._qm.start_next()

    # Row helpers #

    def _build_row(self, job_id: str) -> None:
        state = self._states[job_id]
        row   = _JobRow(
            self._scroll,
            state           = state,
            on_pause_resume = lambda jid=job_id: self._pause_resume(jid),
            on_cancel       = lambda jid=job_id: self._cancel(jid),
            on_move_up      = lambda jid=job_id: self._move_up(jid),
            on_move_down    = lambda jid=job_id: self._move_down(jid),
        )
        row.pack(fill="x", pady=(0, 6))
        self._rows[job_id] = row

    def _refresh_row(self, job_id: str) -> None:
        if job_id in self._rows:
            self._rows[job_id].refresh()

    """Re-pack all rows according to self._order."""
    def _rebuild_order(self) -> None:
        
        for row in self._rows.values():
            row.pack_forget()
        for jid in self._order:
            if jid in self._rows:
                self._rows[jid].pack(fill="x", pady=(0, 6))

    def _update_count(self) -> None:
        active = sum(
            1 for s in self._states.values()
            if s.status in ("pending", "downloading")
        )
        self._count_badge.configure(text=str(active))

    # Queue actions #

    def _pause_resume(self, job_id: str) -> None:
        state = self._states.get(job_id)
        if not state:
            return

        if state.status == "downloading":
            # Pause: flag first so _process() intercepts the cancel message
            state.paused = True
            self._qm.cancel(job_id)

        elif state.status == "pending" and state.paused:
            # Resume: clear flag, float to front of pending block, kick off
            state.paused = False

            # Move to the position right after any currently-downloading job
            self._order.remove(job_id)
            insert_at = next(
                (
                    i for i, jid in enumerate(self._order)
                    if self._states[jid].status == "pending"
                ),
                len(self._order),
            )
            self._order.insert(insert_at, job_id)
            self._rebuild_order()
            self._qm.start_next()

    def _cancel(self, job_id: str) -> None:
        state = self._states.get(job_id)
        if not state:
            return

        if state.status in ("done", "error", "cancelled"):
            # Remove the row entirely
            self._order.remove(job_id)
            self._rows.pop(job_id).destroy()
            del self._states[job_id]
            self._update_count()
        else:
            state.paused = False
            self._qm.cancel(job_id)

    def _move_up(self, job_id: str) -> None:
        state = self._states.get(job_id)
        if not state or state.status not in ("pending",):
            return
        idx = self._order.index(job_id)
        if idx > 0:
            above = self._order[idx - 1]
            if self._states[above].status != "downloading":
                self._order[idx - 1], self._order[idx] = (
                    self._order[idx], self._order[idx - 1]
                )
                self._rebuild_order()

    def _move_down(self, job_id: str) -> None:
        state = self._states.get(job_id)
        if not state or state.status not in ("pending",):
            return
        idx = self._order.index(job_id)
        if idx < len(self._order) - 1:
            self._order[idx], self._order[idx + 1] = (
                self._order[idx + 1], self._order[idx]
            )
            self._rebuild_order()

    def _clear_done(self) -> None:
        to_remove = [
            jid for jid, s in self._states.items()
            if s.status in ("done", "cancelled") and not s.paused
        ]
        for jid in to_remove:
            self._order.remove(jid)
            self._rows.pop(jid).destroy()
            del self._states[jid]
        self._update_count()


# Utility #
"""Convert a yt-dlp format string to a short human-readable label."""
def _fmt_label(fmt_str: str) -> str:
    
    if "bestaudio" in fmt_str and "bestvideo" not in fmt_str:
        return "audio · mp3"
    m = re.search(r"height<=(\d+)", fmt_str)
    if m:
        return f"{m.group(1)}p · mp4"
    return fmt_str or "—"
