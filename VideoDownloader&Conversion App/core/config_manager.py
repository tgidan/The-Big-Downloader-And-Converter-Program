"""
core/config_manager.py

Lightweight JSON key-value store persisted to ~/.tbdc/config.json.

Public API:
load()                   – read config from disk (called automatically on first access)
save()                   – flush current state to disk
get(key, default)        – read a value
set(key, value)          – write a value and save immediately
update(values)           – update multiple keys in one disk write
validate_paths()         – check path fields; returns {key: error_message}
reset_to_defaults()      – restore all keys to defaults and persist
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Paths #
_CONFIG_DIR  = Path.home() / ".tbdc"
_CONFIG_FILE = _CONFIG_DIR / "config.json"

# Defaults #
_DEFAULTS: dict = {
    "output_dir":             str(Path.home() / "Downloads"),
    "last_quality":           "bestvideo+bestaudio/best",
    "ffmpeg_path":            None,
    "loudness_normalization": False,
    "loudness_target_lufs":   -14.0,
    "cookiefile":             None,
}

# State #
_data:   dict = {}
_loaded: bool = False


# Internal #

"""Lazy-load config on first access."""
def _ensure_loaded() -> None:
    global _loaded
    if not _loaded:
        load()


# Public #

"""Read config from disk; resets to defaults if file is absent or corrupt."""
def load() -> None:
    global _data, _loaded
    try:
        on_disk = json.loads(_CONFIG_FILE.read_text(encoding="utf-8"))
        if not isinstance(on_disk, dict):
            raise ValueError("config root must be a JSON object")
        _data = {**_DEFAULTS, **on_disk}
    except (FileNotFoundError, json.JSONDecodeError, ValueError):
        _data = dict(_DEFAULTS)
    _loaded = True


"""Flush the current in-memory config to ~/.tbdc/config.json."""
def save() -> None:
    _CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    _CONFIG_FILE.write_text(json.dumps(_data, indent=2), encoding="utf-8")


"""Return the value for key, or default if absent."""
def get(key: str, default=None):
    _ensure_loaded()
    return _data.get(key, default)


"""Set key to value and immediately persist to disk."""
def set(key: str, value) -> None:
    _ensure_loaded()
    _data[key] = value
    save()


"""Update multiple keys in a single disk write."""
def update(values: dict) -> None:
    _ensure_loaded()
    _data.update(values)
    save()


"""Reset all values to factory defaults and persist to disk."""
def reset_to_defaults() -> None:
    global _data
    _data = dict(_DEFAULTS)
    save()


"""
Check path-type config values for existence.

Returns a dict mapping field name to an error string for every field whose
configured path does not exist on disk.  An empty dict means all paths are
valid (or not set).
"""
def validate_paths() -> dict[str, str]:
    _ensure_loaded()
    errors: dict[str, str] = {}

    output_dir = _data.get("output_dir")
    if output_dir and not Path(output_dir).is_dir():
        errors["output_dir"] = f"Directory not found: {output_dir}"

    ffmpeg_path = _data.get("ffmpeg_path")
    if ffmpeg_path:
        p = Path(ffmpeg_path)
        if not p.exists():
            errors["ffmpeg_path"] = f"Path not found: {ffmpeg_path}"
        elif p.is_dir():
            binary = p / ("ffmpeg.exe" if sys.platform == "win32" else "ffmpeg")
            if not binary.is_file():
                errors["ffmpeg_path"] = f"ffmpeg binary not found in: {ffmpeg_path}"

    cookiefile = _data.get("cookiefile")
    if cookiefile and not Path(cookiefile).is_file():
        errors["cookiefile"] = f"Cookie file not found: {cookiefile}"

    return errors
