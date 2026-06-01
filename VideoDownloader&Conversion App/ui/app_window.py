"""
ui/app_window.py

Main application window for TBD&C.

Wiring:
  URLPanel.on_formats_fetched    ->  QualityPanel.populate()
  QualityPanel.on_add_to_queue   ->  AppWindow._add_to_queue()
  AppWindow._add_to_queue()      ->  QueueManager.add() + start_next()
  DownloadPanel polls QueueManager.update_queue every 100 ms

Public API:
AppWindow(queue_manager)
"""

from __future__ import annotations

import os
import shutil
import threading
from pathlib import Path

import customtkinter as ctk

from core.queue_manager import QueueManager
from ui.download_panel import DownloadPanel
from ui.quality_panel import QualityPanel
from ui.url_panel import URLPanel

# Accent colours #
_BDX       = "#791F1F"
_BDX_DM    = "#5C1418"   # darker bordeaux for dark-mode header
_BDX_HOVER = "#5C1418"
_BDX_TEXT  = "#FCEBEB"
_BDX_SUB   = "#E24B4A"   # subtitle / de-emphasised on bordeaux bg

# Warning banner colours #
_WARN_BG    = ("#FEF3C7", "#3D2A00")
_WARN_TEXT  = ("#92400E", "#FBBF24")
_WARN_HOVER = ("#FDE68A", "#5C4000")

"""Root window — instantiates and wires all panels."""
class AppWindow(ctk.CTk):

    """Wire panels, set geometry and icon, then schedule startup checks after 600 ms."""
    def __init__(self, queue_manager: QueueManager) -> None:
        super().__init__()
        self._qm = queue_manager

        self.title("TBD&C - The Big Downloader & Converter")
        self.geometry("940x640")
        self.minsize(820, 520)
        self._set_icon()

        self._build()
        self.after(600, self._startup_checks)
    
    """Load window icon from assets/icon.ico; silently skip if absent."""
    def _set_icon(self) -> None:
        from pathlib import Path
        icon = Path(__file__).parent.parent / "assets" / "icon.ico"
        if icon.is_file():
            try:
                self.iconbitmap(str(icon))
            except Exception:
                pass

    # Layout #

    """Grid all four rows: header (0), warning banner (1), content (2), status bar (3)."""
    def _build(self) -> None:
        self.grid_rowconfigure(2, weight=1)  # content row; row 1 is the collapsible banner
        self.grid_columnconfigure(0, weight=1)

        self._build_header()          # row 0
        self._build_warning_banner()  # row 1  (hidden until warnings arrive)
        self._build_content()         # row 2
        self._build_statusbar()       # row 3

    """Bordeaux header strip: app name, subtitle, and folder/settings icon buttons."""
    def _build_header(self) -> None:
        hdr = ctk.CTkFrame(
            self,
            fg_color=(_BDX, _BDX_DM),
            corner_radius=0,
            height=46,
        )
        hdr.grid(row=0, column=0, sticky="ew")
        hdr.grid_propagate(False)
        hdr.columnconfigure(1, weight=1)

        # App name
        ctk.CTkLabel(
            hdr,
            text="⬇  TBD&C",
            font=ctk.CTkFont(size=15, weight="bold"),
            text_color=_BDX_TEXT,
        ).grid(row=0, column=0, padx=(16, 8), pady=12, sticky="w")

        # Subtitle
        ctk.CTkLabel(
            hdr,
            text="The Big Downloader & Converter",
            font=ctk.CTkFont(size=11),
            text_color=_BDX_SUB,
        ).grid(row=0, column=1, padx=4, sticky="w")

        # Icon buttons (right side)
        icons = ctk.CTkFrame(hdr, fg_color="transparent")
        icons.grid(row=0, column=2, padx=12)

        icon_kw = dict(
            width=32, height=32,
            fg_color="transparent",
            text_color="#F7C1C1",
            hover_color=_BDX_HOVER,
            font=ctk.CTkFont(size=17),
        )
        ctk.CTkButton(
            icons, text="📁",
            command=self._pick_output_dir,
            **icon_kw,
        ).pack(side="left", padx=4)
        ctk.CTkButton(
            icons, text="⚙",
            command=self._open_settings,
            **icon_kw,
        ).pack(side="left", padx=4)

    """Left scroll pane (URL + Quality), vertical separator, right sidebar (Queue)."""
    def _build_content(self) -> None:
        content = ctk.CTkFrame(self, fg_color="transparent")
        content.grid(row=2, column=0, sticky="nsew")
        content.grid_rowconfigure(0, weight=1)
        content.grid_columnconfigure(0, weight=1)

        # Left panel: URL + Quality (scrollable in case window is short) #
        left_scroll = ctk.CTkScrollableFrame(
            content,
            fg_color=("white", "gray10"),
            label_text="",
            scrollbar_button_color=("gray80", "gray30"),
        )
        left_scroll.grid(row=0, column=0, sticky="nsew")

        self._url_panel = URLPanel(
            left_scroll,
            on_formats_fetched=self._on_formats_fetched,
        )
        self._url_panel.pack(fill="x", padx=24, pady=(22, 0))

        # Divider between URL and Quality panels
        ctk.CTkFrame(
            left_scroll,
            height=1,
            fg_color=("gray88", "gray22"),
        ).pack(fill="x", padx=24, pady=18)

        self._quality_panel = QualityPanel(
            left_scroll,
            on_add_to_queue=self._add_to_queue,
        )
        self._quality_panel.pack(fill="x", padx=24, pady=(0, 24))

        # Vertical separator #
        ctk.CTkFrame(
            content,
            width=1,
            fg_color=("gray88", "gray22"),
        ).grid(row=0, column=1, sticky="ns")

        # Right sidebar: Queue #
        sidebar = ctk.CTkFrame(
            content,
            fg_color=("gray97", "gray11"),
            width=280,
            corner_radius=0,
        )
        sidebar.grid(row=0, column=2, sticky="nsew")
        sidebar.grid_propagate(False)

        self._download_panel = DownloadPanel(
            sidebar,
            queue_manager=self._qm,
            on_download=self._on_download_btn,
        )
        self._download_panel.pack(fill="both", expand=True, padx=14, pady=14)

    """Thin footer row with a mutable status label."""
    def _build_statusbar(self) -> None:
        bar = ctk.CTkFrame(
            self,
            fg_color=("gray92", "gray10"),
            corner_radius=0,
            height=26,
        )
        bar.grid(row=3, column=0, sticky="ew")
        bar.grid_propagate(False)

        self._status_lbl = ctk.CTkLabel(
            bar,
            text="Starting up…",
            font=ctk.CTkFont(size=11),
            text_color=("gray50", "gray55"),
        )
        self._status_lbl.pack(side="left", padx=14)

    """Amber dismissible banner; hidden until _show_warnings() is called."""
    def _build_warning_banner(self) -> None:
        # Not gridded here: shown on demand via _show_warnings()
        self._banner = ctk.CTkFrame(self, fg_color=_WARN_BG, corner_radius=0)

        inner = ctk.CTkFrame(self._banner, fg_color="transparent")
        inner.pack(fill="x", padx=12, pady=5)

        self._banner_lbl = ctk.CTkLabel(
            inner,
            text="",
            font=ctk.CTkFont(size=11),
            text_color=_WARN_TEXT,
            justify="left",
            anchor="w",
        )
        self._banner_lbl.pack(side="left", fill="x", expand=True)

        btn_frame = ctk.CTkFrame(inner, fg_color="transparent")
        btn_frame.pack(side="right")

        # Packed conditionally in _show_warnings()
        self._banner_update_btn = ctk.CTkButton(
            btn_frame,
            text="Update now",
            width=80, height=22,
            fg_color="transparent",
            border_width=1,
            border_color=_WARN_TEXT,
            text_color=_WARN_TEXT,
            hover_color=_WARN_HOVER,
            font=ctk.CTkFont(size=11),
            command=self._show_update_dialog,
        )

        ctk.CTkButton(
            btn_frame,
            text="✕",
            width=22, height=22,
            fg_color="transparent",
            text_color=_WARN_TEXT,
            hover_color=_WARN_HOVER,
            font=ctk.CTkFont(size=11),
            command=self._dismiss_banner,
        ).pack(side="right", padx=(6, 0))

    """Populate and grid the amber warning banner; show Update button when has_update."""
    def _show_warnings(self, warnings: list[str], has_update: bool = False) -> None:
        if not warnings:
            return
        self._banner_lbl.configure(text="⚠  " + "  ·  ".join(warnings))
        if has_update:
            self._banner_update_btn.pack(side="left", padx=(0, 6))
        else:
            self._banner_update_btn.pack_forget()
        self._banner.grid(row=1, column=0, sticky="ew")

    """Remove the warning banner from the grid layout."""
    def _dismiss_banner(self) -> None:
        self._banner.grid_remove()

    # Panel callbacks #
    """Relay from URLPanel -> QualityPanel; cache the video title."""
    def _on_formats_fetched(
        self, url: str, formats: list[dict], info: dict
    ) -> None:
        self._last_title = info.get("title", url)
        self._last_url   = url
        self._quality_panel.populate(url, formats, info)

    """Relay from QualityPanel -> QueueManager."""
    def _add_to_queue(self, url: str, format_string: str, audio_only: bool) -> None:
        output_dir = self._quality_panel.get_output_dir()
        os.makedirs(output_dir, exist_ok=True)

        job_id = self._qm.add(url, format_string, output_dir)

        # Set a human-readable title before the first poll cycle fires
        title = getattr(self, "_last_title", url)
        self._download_panel.set_job_title(job_id, title)

        # Kick off the queue if it's currently idle
        self._qm.start_next()

    """Download button in DownloadPanel: read siblings and enqueue."""
    def _on_download_btn(self) -> None:
        url = self._url_panel.get_url()
        if not url:
            return
        fmt = self._quality_panel.get_selected_format()
        self._add_to_queue(url, fmt, False)

    # Header actions #

    """Open the output-folder picker via QualityPanel."""
    def _pick_output_dir(self) -> None:
        self._quality_panel.open_output_picker()

    """Open the SettingsPanel modal dialog."""
    def _open_settings(self) -> None:
        from ui.settings_panel import SettingsPanel
        SettingsPanel(self, on_save=self._on_settings_saved)

    """Re-run startup checks on a background thread after settings are saved."""
    def _on_settings_saved(self) -> None:
        threading.Thread(
            target=self._check_thread,
            daemon=True,
            name="tbdc-settings-check",
        ).start()

    # Startup checks #

    """Spawn the background check thread (versions, update nag, cookie path)."""
    def _startup_checks(self) -> None:
        threading.Thread(
            target=self._check_thread,
            daemon=True,
            name="tbdc-startup",
        ).start()

    """Check yt-dlp version, compare to PyPI, validate cookie path; update status bar."""
    def _check_thread(self) -> None:
        # Versions
        try:
            import yt_dlp as _ydl
            version = _ydl.version.__version__
        except Exception:
            self.after(0, lambda: self._status_lbl.configure(text="yt-dlp not found"))
            return

        ffmpeg_ok = bool(shutil.which("ffmpeg"))
        status = (
            f"yt-dlp {version}  ·  "
            f"ffmpeg {'available' if ffmpeg_ok else '⚠ not found'}"
        )
        self.after(0, lambda: self._status_lbl.configure(text=status))

        # Soft warnings
        warnings: list[str] = []
        has_update = False

        # yt-dlp vs. PyPI
        try:
            from core.downloader import get_ytdlp_latest_version
            latest = get_ytdlp_latest_version()
            if latest and latest > version:
                warnings.append(
                    f"yt-dlp {version} is outdated — latest: {latest}. "
                    "YouTube extractors may be broken."
                )
                has_update = True
        except Exception:
            pass

        # Cookie file existence
        try:
            from core import config_manager
            cookiefile = config_manager.get("cookiefile")
            if cookiefile and not Path(cookiefile).is_file():
                warnings.append(f"Cookie file not found: {cookiefile}")
        except Exception:
            pass

        if warnings:
            _w, _u = list(warnings), has_update
            self.after(0, lambda: self._show_warnings(_w, _u))

    # Update dialog #

    """Modal offering to run yt-dlp --update-to stable."""
    def _show_update_dialog(self) -> None:
        dlg = ctk.CTkToplevel(self)
        dlg.title("yt-dlp update")
        dlg.geometry("400x210")
        dlg.resizable(False, False)
        dlg.grab_set()
        dlg.focus()

        ctk.CTkLabel(
            dlg,
            text="yt-dlp update available",
            font=ctk.CTkFont(size=14, weight="bold"),
            anchor="w",
        ).pack(fill="x", padx=22, pady=(22, 4))

        ctk.CTkLabel(
            dlg,
            text=(
                "yt-dlp updates frequently fix broken site extractors.\n"
                "Keeping it current is strongly recommended."
            ),
            font=ctk.CTkFont(size=12),
            text_color=("gray40", "gray60"),
            justify="left",
            anchor="w",
        ).pack(fill="x", padx=22, pady=(0, 18))

        btn_row = ctk.CTkFrame(dlg, fg_color="transparent")
        btn_row.pack(fill="x", side="bottom", padx=22, pady=(0, 20))

        ctk.CTkButton(
            btn_row,
            text="Skip",
            fg_color="transparent",
            border_width=1,
            border_color=("gray70", "gray40"),
            text_color=("gray45", "gray60"),
            hover_color=("gray90", "gray20"),
            command=dlg.destroy,
        ).pack(side="right", padx=(8, 0))

        ctk.CTkButton(
            btn_row,
            text="Update now",
            fg_color=(_BDX, "#A32D2D"),
            hover_color=(_BDX_HOVER, "#C44040"),
            text_color=_BDX_TEXT,
            command=lambda: self._run_update(dlg),
        ).pack(side="right")

    """Dismiss dialog, start yt-dlp update in a daemon thread, update status bar text."""
    def _run_update(self, dlg: ctk.CTkToplevel) -> None:
        dlg.destroy()
        import subprocess

        threading.Thread(
            target=lambda: subprocess.run(
                ["yt-dlp", "--update-to", "stable"],
                capture_output=True,
            ),
            daemon=True,
            name="tbdc-update",
        ).start()
        self._status_lbl.configure(text="Updating yt-dlp…")
