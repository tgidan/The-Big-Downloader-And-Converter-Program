"""
core/library_manager.py

Read-only browsing and simple file operations over the downloaded video
library, zero UI and zero yt-dlp imports. Synchronous throughout — safe
to call from a worker thread; the UI layer owns its own threading.

Public API:
DirectoryNode                                 – plain-data tree node (name, path, children)
VideoFile                                     – plain-data file record (path, name, size_bytes, resolution, duration)
get_directory_tree(root)                      – nested DirectoryNode tree of subdirectories under root
list_videos(directory)                        – VideoFile records for one directory, non-recursive
search_videos(root, query)                    – recursive case-insensitive filename substring search
fill_metadata(video, ffmpeg_path=None)        – fill resolution/duration via ffprobe; never raises
extract_thumbnail(video, ffmpeg_path=None)    – cached single-frame thumbnail path via ffmpeg; never raises
create_subfolder(root, parent_dir, name)      – make a sanitized subfolder, refusing to escape root
move_video(video_path, dest_dir)              – move a file into dest_dir without clobbering
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

from core.utils import sanitize_filename, unique_path

# Extensions #

_VIDEO_EXTENSIONS = {".mp4", ".mkv", ".webm", ".avi", ".mov", ".flv", ".wmv", ".m4v", ".ts", ".3gp"}
_AUDIO_EXTENSIONS = {".mp3", ".m4a", ".aac", ".flac", ".wav", ".ogg", ".opus"}
_MEDIA_EXTENSIONS = _VIDEO_EXTENSIONS | _AUDIO_EXTENSIONS

# Thumbnails are cached under ~/.tbdc/, never inside the user's output
# directory — that must contain only their videos (config_manager.py:23).
_THUMBNAIL_CACHE_DIR = Path.home() / ".tbdc" / "thumbnails"
_THUMBNAIL_SIZE = 160  # target width in pixels; height keeps aspect ratio


# Data types #

"""A directory and its subdirectories, as plain data (no widget references)."""
@dataclass
class DirectoryNode:
    name:     str
    path:     str
    children: list["DirectoryNode"] = field(default_factory=list)


"""A single media file, as plain data. resolution/duration start unset."""
@dataclass
class VideoFile:
    path:       str
    name:       str
    size_bytes: int
    resolution: str | None = None
    duration:   float | None = None


# ffprobe resolution #

"""
Resolve the ffprobe binary from `ffmpeg_path`, mirroring the dir-or-binary
handling in downloader._resolve_ffmpeg: if ffmpeg_path is a directory, look
for ffprobe inside it; if it's a direct path to the ffmpeg binary, look for
ffprobe alongside it. Falls back to PATH. Returns None — never raises — if
ffprobe can't be found either way.
"""
def _resolve_ffprobe(ffmpeg_path: str | None) -> str | None:
    binary_name = "ffprobe.exe" if sys.platform == "win32" else "ffprobe"

    if ffmpeg_path:
        candidate = Path(ffmpeg_path)
        directory = candidate if candidate.is_dir() else candidate.parent
        binary = directory / binary_name
        if binary.is_file():
            return str(binary)

    return shutil.which("ffprobe")


"""
Resolve the ffmpeg binary itself from `ffmpeg_path`, mirroring the same
dir-or-binary handling as downloader._resolve_ffmpeg (a directory containing
the binary, or a direct path to it). Falls back to PATH. Returns None —
never raises — if ffmpeg can't be found either way.
"""
def _resolve_ffmpeg(ffmpeg_path: str | None) -> str | None:
    binary_name = "ffmpeg.exe" if sys.platform == "win32" else "ffmpeg"

    if ffmpeg_path:
        candidate = Path(ffmpeg_path)
        binary = (candidate / binary_name) if candidate.is_dir() else candidate
        if binary.is_file():
            return str(binary)

    return shutil.which("ffmpeg")


# Directory tree #

"""
Build the tree of subdirectories under `root` as a DirectoryNode.
Hidden/dotfile directories are skipped entirely, at every depth.
"""
def get_directory_tree(root: str) -> DirectoryNode:
    root_path = Path(root)
    return DirectoryNode(
        name=root_path.name or str(root_path),
        path=str(root_path),
        children=_child_nodes(root_path),
    )


"""Recurse into `directory`, skipping hidden/dotfile subdirectories."""
def _child_nodes(directory: Path) -> list[DirectoryNode]:
    try:
        entries = sorted(directory.iterdir(), key=lambda p: p.name.lower())
    except OSError:
        return []

    children = []
    for entry in entries:
        if entry.is_dir() and not entry.name.startswith("."):
            children.append(DirectoryNode(
                name=entry.name,
                path=str(entry),
                children=_child_nodes(entry),
            ))
    return children


# Listing / search #

"""Build a VideoFile record from a matched media file path."""
def _video_file(entry: Path) -> VideoFile:
    return VideoFile(
        path=str(entry),
        name=entry.stem,
        size_bytes=entry.stat().st_size,
    )


"""
List media files directly inside `directory` (non-recursive). Filters by
extension rather than opening every file; resolution/duration are left
unset until fill_metadata() populates them.
"""
def list_videos(directory: str) -> list[VideoFile]:
    dir_path = Path(directory)
    if not dir_path.is_dir():
        return []

    return [
        _video_file(entry)
        for entry in sorted(dir_path.iterdir(), key=lambda p: p.name.lower())
        if entry.is_file() and entry.suffix.lower() in _MEDIA_EXTENSIONS
    ]


"""
Recursively search the tree under `root` for media files whose filename
contains `query` as a case-insensitive substring. Hidden directories are
skipped, same as get_directory_tree.
"""
def search_videos(root: str, query: str) -> list[VideoFile]:
    needle = query.lower()
    matches: list[VideoFile] = []

    def _walk(directory: Path) -> None:
        try:
            entries = list(directory.iterdir())
        except OSError:
            return
        for entry in entries:
            if entry.is_dir():
                if not entry.name.startswith("."):
                    _walk(entry)
            elif entry.is_file() and entry.suffix.lower() in _MEDIA_EXTENSIONS:
                if needle in entry.name.lower():
                    matches.append(_video_file(entry))

    _walk(Path(root))
    return matches


# Metadata #

"""
Fill in resolution and duration for `video` using ffprobe, resolved from
`ffmpeg_path` the same way downloader._resolve_ffmpeg resolves ffmpeg.

Never raises. Missing ffmpeg is a soft warning in this app (main.py
_preflight_checks never blocks startup on it), so any failure here —
ffprobe absent, a non-media file, malformed output, or a timeout — simply
leaves `video` with only the size already known. Mutates and returns
`video` for convenience.
"""
def fill_metadata(video: VideoFile, ffmpeg_path: str | None = None) -> VideoFile:
    ffprobe = _resolve_ffprobe(ffmpeg_path)
    if not ffprobe:
        return video

    try:
        result = subprocess.run(
            [ffprobe, "-v", "quiet", "-print_format", "json",
             "-show_streams", "-show_format", video.path],
            capture_output=True,
            text=True,
            timeout=15,
        )
        data = json.loads(result.stdout)
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        return video

    video_stream = next(
        (s for s in data.get("streams", []) if s.get("codec_type") == "video"),
        None,
    )

    if video_stream:
        width, height = video_stream.get("width"), video_stream.get("height")
        if width and height:
            video.resolution = f"{width}x{height}"

    duration_str = data.get("format", {}).get("duration") or (
        video_stream.get("duration") if video_stream else None
    )
    if duration_str is not None:
        try:
            video.duration = float(duration_str)
        except (TypeError, ValueError):
            pass

    return video


"""
Return the cache path for a thumbnail of `video_path`. Keyed on the
resolved absolute path plus size and mtime, so a replaced file (same name,
different content) gets a fresh thumbnail instead of a stale cached one.
"""
def _thumbnail_cache_path(video_path: str) -> Path:
    p = Path(video_path)
    try:
        stat = p.stat()
        key = f"{p.resolve()}|{stat.st_size}|{stat.st_mtime_ns}"
    except OSError:
        key = str(p.resolve())
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()
    return _THUMBNAIL_CACHE_DIR / f"{digest}.jpg"


"""
Return the path to a cached single-frame JPEG thumbnail for `video`,
extracting and caching it via ffmpeg if not already cached.

Resolves ffmpeg from `ffmpeg_path` the same way fill_metadata resolves
ffprobe. Never raises: ffmpeg absent, a non-media file, a clip shorter than
the 1-second seek point, or a timeout all simply return None so the caller
can fall back to a generic placeholder — thumbnails are a nice-to-have,
never a requirement (see main.py's soft-warning treatment of missing ffmpeg).
"""
def extract_thumbnail(video: VideoFile, ffmpeg_path: str | None = None) -> str | None:
    cache_path = _thumbnail_cache_path(video.path)
    if cache_path.is_file():
        return str(cache_path)

    ffmpeg = _resolve_ffmpeg(ffmpeg_path)
    if not ffmpeg:
        return None

    try:
        _THUMBNAIL_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            [ffmpeg, "-y", "-ss", "1", "-i", video.path,
             "-frames:v", "1", "-update", "1",
             "-vf", f"scale={_THUMBNAIL_SIZE}:-1",
             "-an", str(cache_path)],
            capture_output=True,
            timeout=20,
        )
    except (OSError, subprocess.SubprocessError):
        return None

    return str(cache_path) if cache_path.is_file() else None


# Folder / file operations #

"""
Create a subfolder named `name` under `parent_dir`, sanitizing the name
first. Raises ValueError if `parent_dir` is not `root` itself or somewhere
inside it — sanitize_filename already strips path separators from `name`,
so the created folder can never itself land outside `parent_dir`.
"""
def create_subfolder(root: str, parent_dir: str, name: str) -> Path:
    root_path   = Path(root).resolve()
    parent_path = Path(parent_dir).resolve()

    if parent_path != root_path and root_path not in parent_path.parents:
        raise ValueError(f"{parent_dir} is outside library root {root}")

    safe_name = sanitize_filename(name)
    new_dir = parent_path / safe_name
    new_dir.mkdir(parents=True, exist_ok=True)
    return new_dir


"""
Move a media file at `video_path` into `dest_dir`, using unique_path to
avoid clobbering an existing file with the same name there.
"""
def move_video(video_path: str, dest_dir: str) -> Path:
    src = Path(video_path)
    dest = unique_path(dest_dir, src.stem, src.suffix)
    return src.replace(dest)
