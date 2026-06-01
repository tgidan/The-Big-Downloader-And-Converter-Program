"""
main.py

Entry point for TBD&C — The Big Downloader & Converter.
Bootstraps CTk, loads config, and wires QueueManager -> AppWindow.

Public API:
main()   – initialise CTk, load config, create AppWindow, start event loop
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
Missing yt-dlp is a hard fatal (the app cannot function at all).
Missing ffmpeg is a soft warning — the user can configure a path in Settings
without restarting, so we never block startup for it.
"""
def _preflight_checks() -> None:
    import tkinter as tk
    import tkinter.messagebox as mb

    def _fatal(title: str, message: str) -> None:
        root = tk.Tk()
        root.withdraw()
        mb.showerror(title, message, parent=root)
        root.destroy()
        sys.exit(1)

    # yt-dlp — hard failure: nothing works without it
    try:
        import yt_dlp as _  # noqa: F401
    except ImportError:
        _fatal(
            "Missing dependency",
            "yt-dlp is not installed.\n\nRun:  pip install yt-dlp",
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
