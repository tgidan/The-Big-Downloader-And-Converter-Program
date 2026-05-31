"""
core/config_manager.py

Lightweight JSON key-value store persisted to ~/.tbdc/config.json.

Public API:
load()             – read config from disk into memory
save()             – flush current state to disk
get(key, default)  – read a value; lazy-loads on first call
set(key, value)    – write a value and save immediately
"""

from __future__ import annotations

import json
from pathlib import Path

# Paths #
_CONFIG_DIR  = Path.home() / ".tbdc"
_CONFIG_FILE = _CONFIG_DIR / "config.json"

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

"""Read config from disk; resets to empty dict if file is absent or corrupt."""
def load() -> None:
    global _data, _loaded
    try:
        _data = json.loads(_CONFIG_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        _data = {}
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
