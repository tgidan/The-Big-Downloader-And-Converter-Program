"""
core/downloader.py

yt-dlp wrapper, zero UI imports.
All progress is communicated via queue.Queue; never touches widgets directly.

Public API:
fetch_formats(url)                               – list of available format dicts
fetch_info(url)                                  – (formats, info) without downloading
build_video_format_string(height)                – yt-dlp format string for a height cap
build_audio_format_string()                      – yt-dlp format string for best audio
download(url, format_string, output_dir, q, …)  – blocking download; push progress to q
check_ytdlp_update()                             – run yt-dlp --update-to stable
get_ytdlp_latest_version()                       – fetch latest version string from PyPI
"""

from __future__ import annotations

import queue
import shutil
import subprocess
from pathlib import Path

import yt_dlp

# ffmpeg validation #

"""Return True when running on Windows."""
def _is_windows() -> bool:
    import sys
    return sys.platform == "win32"

"""
Return the ffmpeg executable path, or raise RuntimeError if unavailable.

Priority:
    1. Explicit ffmpeg_location (PyInstaller bundles ship ffmpeg alongside the app)
    2. ffmpeg on PATH
"""
def _resolve_ffmpeg(ffmpeg_location: str | None) -> str | None:
    
    if ffmpeg_location:
        candidate = Path(ffmpeg_location)
        # Accept either a directory (containing ffmpeg) or a direct path to the binary
        if candidate.is_dir():
            binary = candidate / ("ffmpeg.exe" if _is_windows() else "ffmpeg")
        else:
            binary = candidate
        if binary.is_file():
            return str(binary)
        raise RuntimeError(
            f"ffmpeg not found at the specified location: {ffmpeg_location}\n"
            "Make sure the bundled ffmpeg binary is present."
        )

    if shutil.which("ffmpeg"):
        return None  # yt-dlp will find it on PATH automatically

    raise RuntimeError(
        "ffmpeg is not installed or not on PATH.\n"
        "Download it from https://ffmpeg.org/download.html and add it to your PATH, "
        "or place it alongside this application."
    )


# Format fetching #

"""Parse the formats list from a raw yt-dlp info dict."""
def _extract_formats(info: dict) -> list[dict]:
    formats = []
    for f in info.get("formats", []):
        height = f.get("height")
        vcodec = f.get("vcodec", "none")
        fps    = f.get("fps")
        parts  = []
        if height:
            parts.append(f"{height}p" + (f", {int(fps)}fps" if fps else ""))
        if vcodec == "none":
            parts.append("audio only")
        note = f.get("format_note") or (", ".join(parts) if parts else f.get("format_id", ""))
        formats.append({
            "format_id": f["format_id"],
            "ext":       f.get("ext", ""),
            "height":    height,
            "note":      note,
            "vcodec":    vcodec,
            "acodec":    f.get("acodec", "none"),
            "filesize":  f.get("filesize") or f.get("filesize_approx"),
        })
    return formats


"""
Retrieve available formats for a URL without downloading anything.

Returns a list of dicts with keys: format_id, ext, height, note, vcodec,
acodec, filesize. Raises yt_dlp.utils.DownloadError for unavailable videos.
"""
def fetch_formats(url: str) -> list[dict]:
    ydl_opts = {"quiet": True, "no_warnings": True, "skip_download": True}
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)
    return _extract_formats(info)


"""
Fetch formats AND the full yt-dlp info dict (title, thumbnail, duration…).

Returns (formats, info) so callers can display a rich video preview without
a second network round-trip.
"""
def fetch_info(url: str) -> tuple[list[dict], dict]:
    ydl_opts = {"quiet": True, "no_warnings": True, "skip_download": True}
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)
    return _extract_formats(info), info


# Format string helpers #

"""e.g. height=1080 -> 'bestvideo[height<=1080]+bestaudio/best'"""
def build_video_format_string(height: int) -> str:
    return f"bestvideo[height<={height}]+bestaudio/best"


"""Return the yt-dlp format string for the best available audio stream."""
def build_audio_format_string() -> str:
    return "bestaudio/best"


# Loudness normalisation postprocessor #

"""
Runs EBU R128 loudness normalization on the final output file as a separate
ffmpeg pass, completely isolated from yt-dlp's own merge/fixup pipeline.

Registered with `when="after_move"` so it only runs once, on the finished
file, after yt-dlp has completed all its own postprocessing.
"""
class _LoudnormPP(yt_dlp.postprocessor.FFmpegPostProcessor):

    def __init__(self, downloader=None, *, target_lufs: float = -14.0) -> None:
        super().__init__(downloader)
        self._target_lufs = target_lufs

    def run(self, info: dict) -> tuple[list, dict]:
        filepath = info.get("filepath")
        if not filepath or not Path(filepath).is_file():
            return [], info

        p   = Path(filepath)
        tmp = p.parent / (p.stem + ".loudnorm" + p.suffix)

        try:
            self.run_ffmpeg(
                str(p), str(tmp),
                ["-af", f"loudnorm=I={int(self._target_lufs)}:TP=-1:LRA=11",
                 "-c:v", "copy"],
            )
        except Exception:
            tmp.unlink(missing_ok=True)
            raise

        tmp.replace(p)
        return [], info


# Download #

"""
Download a single URL.
Runs synchronously, call this from a background thread.

Progress updates are pushed onto `progress_queue` as dicts:
    { "status": "downloading", "percent": float, "speed": str, "eta": str, "filename": str }
    { "status": "finished",   "filename": str }
    { "status": "error",      "message": str }

Raises RuntimeError if ffmpeg is required but unavailable.
"""
def download(
    url: str,
    format_string: str,
    output_dir: str,
    progress_queue: queue.Queue,
    *,
    audio_only: bool = False,
    ffmpeg_location: str | None = None,
    cookiefile: str | None = None,
    loudness_normalization: bool = False,
    loudness_target_lufs: float = -14.0,
) -> None:

    try:
        resolved_ffmpeg = _resolve_ffmpeg(ffmpeg_location)
    except RuntimeError as exc:
        progress_queue.put({"status": "error", "message": str(exc)})
        return

    def _progress_hook(d: dict) -> None:
        status = d.get("status")
        if status == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            downloaded = d.get("downloaded_bytes", 0)
            percent = (downloaded / total * 100) if total else 0.0
            progress_queue.put({
                "status":   "downloading",
                "percent":  round(percent, 1),
                "speed":    d.get("_speed_str", ""),
                "eta":      d.get("_eta_str", ""),
                "filename": d.get("filename", ""),
            })
        elif status == "finished":
            progress_queue.put({
                "status":   "finished",
                "filename": d.get("filename", ""),
            })
        elif status == "error":
            progress_queue.put({
                "status":  "error",
                "message": str(d.get("error", "Unknown error")),
            })

    ydl_opts: dict = {
        "format":         format_string,
        "outtmpl":        str(Path(output_dir) / "%(title)s.%(ext)s"),
        "progress_hooks": [_progress_hook],
        "quiet":          True,
        "no_warnings":    True,
    }

    if resolved_ffmpeg:
        ydl_opts["ffmpeg_location"] = resolved_ffmpeg

    if cookiefile:
        ydl_opts["cookiefile"] = cookiefile

    if audio_only:
        ydl_opts["postprocessors"] = [
            {
                "key":              "FFmpegExtractAudio",
                "preferredcodec":   "mp3",
                "preferredquality": "192",
            }
        ]

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            if loudness_normalization and not audio_only:
                ydl.add_post_processor(
                    _LoudnormPP(ydl, target_lufs=loudness_target_lufs),
                    when="after_move",
                )
            ydl.download([url])
    except yt_dlp.utils.DownloadError as exc:
        progress_queue.put({"status": "error", "message": str(exc)})



# yt-dlp update check (optional startup call) #

"""
Run `yt-dlp --update-to stable` and return the output message.

Returns None when yt-dlp is already up to date, output is empty, or any
exception occurs (no network, missing binary, etc.).
"""
def check_ytdlp_update() -> str | None:
    try:
        result = subprocess.run(
            ["yt-dlp", "--update-to", "stable"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        combined = (result.stdout + result.stderr).strip()
        if not combined or "up to date" in combined.lower():
            return None
        return result.stdout.strip() or result.stderr.strip() or None
    except Exception:
        return None


"""Return the latest yt-dlp version string from PyPI, or None on error."""
def get_ytdlp_latest_version() -> str | None:
    try:
        import json as _json
        import urllib.request
        with urllib.request.urlopen("https://pypi.org/pypi/yt-dlp/json", timeout=8) as resp:
            data = _json.loads(resp.read())
        return data["info"]["version"]
    except Exception:
        return None
