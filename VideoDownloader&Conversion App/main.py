"""
main.py

Entry point for TBD&C — The Big Downloader & Converter.

Usage:
  python main.py
"""

from __future__ import annotations

import sys

import customtkinter as ctk

from core import config_manager
from core.queue_manager import QueueManager
from ui.app_window import AppWindow


"""
Prevent customtkinter's DPI check from crashing on plain tk.Toplevel
windows (created by filedialog and tooltip helpers) that lack the CTk
block_update_dimensions_event method.
"""
def _patch_scaling_tracker() -> None:
    try:
        from customtkinter.windows.widgets.scaling.scaling_tracker import ScalingTracker
        original = ScalingTracker.__dict__["check_dpi_scaling"]
        if isinstance(original, classmethod):
            orig_func = original.__func__
            def _safe(cls, *args, **kwargs):
                try:
                    orig_func(cls, *args, **kwargs)
                except AttributeError:
                    pass
            ScalingTracker.check_dpi_scaling = classmethod(_safe)
        else:
            def _safe(*args, **kwargs):
                try:
                    original(*args, **kwargs)
                except AttributeError:
                    pass
            ScalingTracker.check_dpi_scaling = _safe
    except Exception:
        pass


"""
Validate required tools before the window opens.
Hard failures (missing yt-dlp or ffmpeg) show a blocking error dialog
and exit; soft warnings are deferred to the in-app banner.
"""
def _preflight_checks() -> None:
    import shutil
    import tkinter as tk
    import tkinter.messagebox as mb
    from pathlib import Path

    def _fatal(title: str, message: str) -> None:
        root = tk.Tk()
        root.withdraw()
        mb.showerror(title, message, parent=root)
        root.destroy()
        sys.exit(1)

    # yt-dlp
    try:
        import yt_dlp as _  # noqa: F401
    except ImportError:
        _fatal(
            "Missing dependency",
            "yt-dlp is not installed.\n\nRun:  pip install yt-dlp",
        )

    # ffmpeg
    from core import config_manager

    ffmpeg_location: str | None = config_manager.get("ffmpeg_location")
    ffmpeg_ok = False

    if ffmpeg_location:
        candidate = Path(ffmpeg_location)
        binary = (
            candidate / ("ffmpeg.exe" if sys.platform == "win32" else "ffmpeg")
            if candidate.is_dir()
            else candidate
        )
        ffmpeg_ok = binary.is_file()
    else:
        ffmpeg_ok = bool(shutil.which("ffmpeg"))

    if not ffmpeg_ok:
        where = f"\n\nLooked in: {ffmpeg_location}" if ffmpeg_location else ""
        _fatal(
            "ffmpeg not found",
            "ffmpeg is required but could not be found."
            + where
            + "\n\nInstall it, then restart the application:\n"
            "  Windows:  winget install Gyan.FFmpeg\n"
            "  macOS:    brew install ffmpeg\n"
            "  Linux:    sudo apt install ffmpeg\n\n"
            "Or download from https://ffmpeg.org/download.html and add it to PATH.",
        )


"""Initialise CTk, load config, build AppWindow, start the event loop."""
def main() -> None:
    _patch_scaling_tracker()
    _preflight_checks()

    ctk.set_appearance_mode("System")
    ctk.set_default_color_theme("blue")

    config_manager.load()
    qm = QueueManager(max_concurrent=1)

    app = AppWindow(queue_manager=qm)
    app.mainloop()


if __name__ == "__main__":
    main()
