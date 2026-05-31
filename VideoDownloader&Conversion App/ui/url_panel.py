"""
ui/url_panel.py

URL input panel — entry field, clipboard paste, async format fetch,
and a preview card showing thumbnail + title + metadata.

Public API:
URLPanel(master, on_formats_fetched)
URLPanel.get_url() -> str   – current entry text, stripped
"""

from __future__ import annotations

import io
import threading
import tkinter as tk
import urllib.request
from typing import Callable

import customtkinter as ctk

from core.downloader import fetch_info

# Accent colours #
_BDX        = ("#791F1F", "#A32D2D")   # button fill  (light, dark)
_BDX_HOVER  = ("#5C1418", "#C44040")   # button hover
_BDX_TEXT   = "#FCEBEB"                # always-light text on bordeaux bg
_BADGE_FG   = ("#F7C1C1", "#5C1418")   # badge background
_BADGE_TEXT = ("#791F1F", "#F7C1C1")   # badge text
_ERR_COLOR  = ("#B91C1C", "#F87171")   # error label text

_SPINNER_FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]


"""
URL entry row + format-fetch trigger + video preview card.

master              parent widget
on_formats_fetched  callback: (url, formats, info) -> None — fired on main thread
"""
class URLPanel(ctk.CTkFrame):

    def __init__(
        self,
        master,
        on_formats_fetched: Callable[[str, list[dict], dict], None],
        **kwargs,
    ) -> None:
        super().__init__(master, fg_color="transparent", **kwargs)
        self._callback    = on_formats_fetched
        self._fetching    = False
        self._thumb_ref   = None   # keep CTkImage alive (prevent GC)
        self._spinner_idx = 0
        self._spinner_job = None

        self._build()

    # Layout #

    def _build(self) -> None:
        # Section label
        ctk.CTkLabel(
            self,
            text="URL",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=("gray40", "gray55"),
            anchor="w",
        ).pack(anchor="w", pady=(0, 6))

        # Input row
        row = ctk.CTkFrame(self, fg_color="transparent")
        row.pack(fill="x", pady=(0, 8))
        row.columnconfigure(0, weight=1)

        self._entry = ctk.CTkEntry(
            row,
            placeholder_text="Paste a YouTube, Vimeo, SoundCloud… URL",
            height=36,
        )
        self._entry.grid(row=0, column=0, sticky="ew", padx=(0, 6))
        self._entry.bind("<Return>", lambda _e: self._start_fetch())
        self._entry.bind("<KeyRelease>", lambda _e: self._clear_results())

        self._fetch_btn = ctk.CTkButton(
            row,
            text="Fetch Info",
            width=80,
            height=36,
            fg_color=_BDX,
            hover_color=_BDX_HOVER,
            text_color=_BDX_TEXT,
            command=self._start_fetch,
        )
        self._fetch_btn.grid(row=0, column=1, padx=(0, 6))

        ctk.CTkButton(
            row,
            text="Paste",
            width=56,
            height=36,
            fg_color="transparent",
            border_width=1,
            border_color=("gray70", "gray40"),
            text_color=("gray40", "gray60"),
            hover_color=("gray90", "gray20"),
            font=ctk.CTkFont(size=12),
            command=self._paste_clipboard,
        ).grid(row=0, column=2)

        # Loading spinner (hidden until fetch starts)
        self._spinner_lbl = ctk.CTkLabel(
            self,
            text="",
            font=ctk.CTkFont(size=11),
            text_color=("gray50", "gray55"),
            anchor="w",
        )
        # Not packed initially; shown in _spinner_start().

        # Error label (hidden until a fetch fails)
        self._error_lbl = ctk.CTkLabel(
            self,
            text="",
            font=ctk.CTkFont(size=11),
            text_color=_ERR_COLOR,
            anchor="w",
            wraplength=420,
            justify="left",
        )
        # Not packed initially; shown in _on_fetch_err().

        # Preview card (hidden until fetch succeeds)
        self._card = ctk.CTkFrame(self, corner_radius=8)
        # Not packed initially; shown in _on_fetch_ok.

        inner = ctk.CTkFrame(self._card, fg_color="transparent")
        inner.pack(fill="x", padx=12, pady=10)
        inner.columnconfigure(1, weight=1)

        # Thumbnail placeholder (76 × 50)
        self._thumb_lbl = ctk.CTkLabel(
            inner,
            text="▶",
            width=76,
            height=50,
            fg_color=("gray85", "gray22"),
            corner_radius=6,
            font=ctk.CTkFont(size=18),
            text_color=("gray60", "gray50"),
        )
        self._thumb_lbl.grid(row=0, column=0, rowspan=3, padx=(0, 12))

        # Title
        self._title_lbl = ctk.CTkLabel(
            inner,
            text="",
            anchor="w",
            font=ctk.CTkFont(size=13, weight="bold"),
            wraplength=300,
            justify="left",
        )
        self._title_lbl.grid(row=0, column=1, sticky="ew")

        # Source · uploader · duration
        self._meta_lbl = ctk.CTkLabel(
            inner,
            text="",
            anchor="w",
            font=ctk.CTkFont(size=11),
            text_color=("gray50", "gray60"),
        )
        self._meta_lbl.grid(row=1, column=1, sticky="ew", pady=(2, 4))

        # "N formats loaded" badge
        self._badge = ctk.CTkLabel(
            inner,
            text="",
            font=ctk.CTkFont(size=11, weight="bold"),
            fg_color=_BADGE_FG,
            text_color=_BADGE_TEXT,
            corner_radius=4,
            padx=8,
            pady=2,
        )
        self._badge.grid(row=2, column=1, sticky="w")

    # Public #

    def get_url(self) -> str:
        return self._entry.get().strip()

    # User actions #

    def _paste_clipboard(self) -> None:
        try:
            text = self.clipboard_get().strip()
        except tk.TclError:
            return
        self._entry.delete(0, tk.END)
        self._entry.insert(0, text)
        self._clear_results()

    def _start_fetch(self) -> None:
        if self._fetching:
            return
        url = self._entry.get().strip()
        if not url:
            return

        self._fetching = True
        self._fetch_btn.configure(text="Fetching…", state="disabled")
        self._clear_results()
        self._spinner_start()

        threading.Thread(
            target=self._fetch_thread,
            args=(url,),
            daemon=True,
            name="tbdc-fetch",
        ).start()

    # Loading indicator #

    def _spinner_start(self) -> None:
        self._spinner_idx = 0
        self._spinner_lbl.pack(anchor="w", pady=(0, 6))
        self._spinner_tick()

    def _spinner_stop(self) -> None:
        if self._spinner_job is not None:
            self.after_cancel(self._spinner_job)
            self._spinner_job = None
        self._spinner_lbl.pack_forget()

    def _spinner_tick(self) -> None:
        self._spinner_lbl.configure(
            text=f"{_SPINNER_FRAMES[self._spinner_idx]}  Fetching formats…"
        )
        self._spinner_idx = (self._spinner_idx + 1) % len(_SPINNER_FRAMES)
        self._spinner_job = self.after(80, self._spinner_tick)

    # Clear results #

    def _clear_results(self) -> None:
        self._card.pack_forget()
        self._error_lbl.pack_forget()

    # Background thread #

    """Runs on a daemon thread; posts results back via after()."""
    def _fetch_thread(self, url: str) -> None:
        try:
            formats, info = fetch_info(url)
            self.after(0, self._on_fetch_ok, url, formats, info)
        except Exception as exc:
            self.after(0, self._on_fetch_err, str(exc))

    # Main-thread callbacks #

    def _on_fetch_ok(self, url: str, formats: list[dict], info: dict) -> None:
        self._fetching = False
        self._fetch_btn.configure(text="Fetch Info", state="normal")
        self._spinner_stop()

        title    = info.get("title", "Unknown title")
        uploader = info.get("uploader") or info.get("channel", "")
        dur_s    = int(info.get("duration") or 0)
        mins, s  = divmod(dur_s, 60)
        hrs,  m  = divmod(mins, 60)
        dur_str  = f"{hrs}:{m:02d}:{s:02d}" if hrs else f"{m}:{s:02d}"
        source   = info.get("extractor_key", "")

        meta_parts = [p for p in [source, uploader, dur_str] if p]

        self._title_lbl.configure(text=title)
        self._meta_lbl.configure(text=" · ".join(meta_parts))
        self._badge.configure(text=f"{len(formats)} formats loaded")
        self._card.pack(fill="x")

        # Async thumbnail — requires Pillow; fails silently otherwise
        thumb_url = info.get("thumbnail")
        if thumb_url:
            threading.Thread(
                target=self._load_thumb,
                args=(thumb_url,),
                daemon=True,
                name="tbdc-thumb",
            ).start()

        self._callback(url, formats, info)

    def _on_fetch_err(self, message: str) -> None:
        self._fetching = False
        self._fetch_btn.configure(text="Fetch Info", state="normal")
        self._spinner_stop()

        # Trim noisy yt-dlp prefix and cap length for display
        msg = message.removeprefix("ERROR: ")
        if len(msg) > 180:
            msg = msg[:177] + "…"
        self._error_lbl.configure(text=msg)
        self._error_lbl.pack(anchor="w", pady=(0, 8))

    # Thumbnail loader #

    """Download and display the video thumbnail (Pillow optional)."""
    def _load_thumb(self, thumb_url: str) -> None:
        try:
            from PIL import Image  # soft dependency

            with urllib.request.urlopen(thumb_url, timeout=6) as resp:
                data = resp.read()

            img     = Image.open(io.BytesIO(data)).resize((76, 50), Image.LANCZOS)
            ctk_img = ctk.CTkImage(img, size=(76, 50))
            self._thumb_ref = ctk_img    # keep strong reference

            self.after(0, lambda: self._thumb_lbl.configure(image=ctk_img, text=""))
        except Exception:
            pass  # thumbnail is decorative — never crash on failure
