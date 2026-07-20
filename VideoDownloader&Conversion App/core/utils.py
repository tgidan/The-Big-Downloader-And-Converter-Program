"""
core/utils.py

Filename sanitization and conflict-detection helpers, zero UI/yt-dlp imports.

Public API:
sanitize_filename(name)                  – make a string safe for use as a file base name
find_conflicts(directory, base_name)     – existing files whose stem matches base_name
unique_path(directory, base_name, ext)   – a non-colliding path for base_name
"""

from __future__ import annotations

import re
from pathlib import Path

# Character classes #

_ILLEGAL_CHARS   = re.compile(r'[\\/*?:"<>|]')
_CONTROL_CHARS   = re.compile(r"[\x00-\x1f]")
_WHITESPACE_RUN  = re.compile(r"\s+")

_RESERVED_NAMES = {
    "con", "prn", "aux", "nul",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
}

_MAX_LENGTH = 200


# sanitize_filename #

"""
Make `name` safe to use as a file base name on Windows, macOS, and Linux.

Applies, in order: whitespace trim, removal of characters illegal on any of
the three platforms, removal of ASCII control characters, whitespace-run
collapsing, leading/trailing dot stripping, Windows reserved device name
rejection (prefixed with an underscore), and a 200-character cap re-stripped
of any trailing whitespace/dots the truncation exposed.

Returns "download" if the result would otherwise be empty.
"""
def sanitize_filename(name: str) -> str:
    name = name.strip()
    name = _ILLEGAL_CHARS.sub("", name)
    name = _CONTROL_CHARS.sub("", name)
    name = _WHITESPACE_RUN.sub(" ", name).strip()
    name = name.strip(".")

    stem = name.split(".", 1)[0]
    if stem.lower() in _RESERVED_NAMES:
        name = "_" + name

    name = name[:_MAX_LENGTH].rstrip(" .")

    return name or "download"


# find_conflicts / unique_path #

"""Existing files in `directory` whose stem matches `base_name`."""
def find_conflicts(directory: str, base_name: str) -> list[Path]:
    dir_path = Path(directory)
    if not dir_path.is_dir():
        return []

    target = base_name.lower()
    return sorted(
        p for p in dir_path.iterdir()
        if p.is_file() and p.stem.lower() == target
    )


"""Return a non-colliding path by appending ' (n)' before the extension."""
def unique_path(directory: str, base_name: str, ext: str) -> Path:
    ext = ext.lstrip(".")
    suffix = f".{ext}" if ext else ""

    candidate_stem = base_name
    n = 0
    while find_conflicts(directory, candidate_stem):
        n += 1
        candidate_stem = f"{base_name} ({n})"

    return Path(directory) / f"{candidate_stem}{suffix}"
