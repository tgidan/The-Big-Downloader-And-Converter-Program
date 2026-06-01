"""
tests/test_config_manager.py

Tests for core/config_manager.py.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import core.config_manager as cm


# Helpers #

"""Reset module state and redirect storage to a tmp path."""
def _reset(config_file: Path) -> None:
    cm._data   = {}
    cm._loaded = False
    cm._CONFIG_DIR  = config_file.parent
    cm._CONFIG_FILE = config_file


# load #

class TestLoad:

    def test_missing_file_resets_to_defaults(self, tmp_path):
        _reset(tmp_path / "config.json")
        cm.load()
        assert cm._data["output_dir"]   == str(Path.home() / "Downloads")
        assert cm._data["last_quality"] == "bestvideo+bestaudio/best"
        assert cm._data["cookiefile"]   is None

    def test_valid_file_merged_over_defaults(self, tmp_path):
        cfg = tmp_path / "config.json"
        cfg.write_text(json.dumps({"output_dir": "/my/videos"}), encoding="utf-8")
        _reset(cfg)
        cm.load()
        assert cm._data["output_dir"]   == "/my/videos"
        assert cm._data["last_quality"] == "bestvideo+bestaudio/best"

    def test_corrupt_file_resets_to_defaults(self, tmp_path):
        cfg = tmp_path / "config.json"
        cfg.write_text("not json", encoding="utf-8")
        _reset(cfg)
        cm.load()
        assert cm._data["last_quality"] == "bestvideo+bestaudio/best"

    def test_non_object_json_resets_to_defaults(self, tmp_path):
        cfg = tmp_path / "config.json"
        cfg.write_text(json.dumps([1, 2, 3]), encoding="utf-8")
        _reset(cfg)
        cm.load()
        assert cm._data["last_quality"] == "bestvideo+bestaudio/best"

    def test_sets_loaded_flag(self, tmp_path):
        _reset(tmp_path / "config.json")
        cm.load()
        assert cm._loaded is True

    def test_extra_keys_in_file_preserved(self, tmp_path):
        cfg = tmp_path / "config.json"
        cfg.write_text(json.dumps({"custom_key": "hello"}), encoding="utf-8")
        _reset(cfg)
        cm.load()
        assert cm._data["custom_key"] == "hello"


# save #

class TestSave:

    def test_creates_directory_and_file(self, tmp_path):
        cfg = tmp_path / "subdir" / "config.json"
        _reset(cfg)
        cm._data = {"output_dir": "/out"}
        cm.save()
        assert cfg.exists()

    def test_written_content_is_valid_json(self, tmp_path):
        cfg = tmp_path / "config.json"
        _reset(cfg)
        cm._data = {"output_dir": "/out", "cookiefile": None}
        cm.save()
        parsed = json.loads(cfg.read_text(encoding="utf-8"))
        assert parsed == {"output_dir": "/out", "cookiefile": None}

    def test_round_trip(self, tmp_path):
        cfg = tmp_path / "config.json"
        _reset(cfg)
        cm._data = dict(cm._DEFAULTS)
        cm.save()
        cm._data   = {}
        cm._loaded = False
        cm.load()
        assert cm._data["last_quality"] == "bestvideo+bestaudio/best"


# get #

class TestGet:

    def test_returns_value_for_known_key(self, tmp_path):
        _reset(tmp_path / "config.json")
        cm._data   = {"output_dir": "/videos"}
        cm._loaded = True
        assert cm.get("output_dir") == "/videos"

    def test_returns_none_for_missing_key(self, tmp_path):
        _reset(tmp_path / "config.json")
        cm._data   = {}
        cm._loaded = True
        assert cm.get("nonexistent") is None

    def test_returns_supplied_default_for_missing_key(self, tmp_path):
        _reset(tmp_path / "config.json")
        cm._data   = {}
        cm._loaded = True
        assert cm.get("nonexistent", "fallback") == "fallback"

    def test_triggers_lazy_load(self, tmp_path):
        _reset(tmp_path / "config.json")
        cm._loaded = False
        cm.get("output_dir")
        assert cm._loaded is True


# set #

class TestSet:

    def test_value_stored_in_data(self, tmp_path):
        _reset(tmp_path / "config.json")
        cm._data   = dict(cm._DEFAULTS)
        cm._loaded = True
        cm.set("output_dir", "/new/path")
        assert cm._data["output_dir"] == "/new/path"

    def test_value_persisted_to_disk(self, tmp_path):
        cfg = tmp_path / "config.json"
        _reset(cfg)
        cm._data   = dict(cm._DEFAULTS)
        cm._loaded = True
        cm.set("cookiefile", "/cookies.txt")
        parsed = json.loads(cfg.read_text(encoding="utf-8"))
        assert parsed["cookiefile"] == "/cookies.txt"

    def test_new_key_accepted(self, tmp_path):
        cfg = tmp_path / "config.json"
        _reset(cfg)
        cm._data   = {}
        cm._loaded = True
        cm.set("custom", 42)
        assert cm._data["custom"] == 42


# update #

class TestUpdate:

    def test_updates_multiple_keys_in_memory(self, tmp_path):
        _reset(tmp_path / "config.json")
        cm._data   = dict(cm._DEFAULTS)
        cm._loaded = True
        cm.update({"output_dir": "/new", "cookiefile": "/c.txt"})
        assert cm._data["output_dir"] == "/new"
        assert cm._data["cookiefile"] == "/c.txt"

    def test_persists_all_keys_in_one_write(self, tmp_path):
        cfg = tmp_path / "config.json"
        _reset(cfg)
        cm._data   = dict(cm._DEFAULTS)
        cm._loaded = True
        cm.update({"output_dir": "/new", "last_quality": "bestaudio/best"})
        parsed = json.loads(cfg.read_text(encoding="utf-8"))
        assert parsed["output_dir"]   == "/new"
        assert parsed["last_quality"] == "bestaudio/best"

    def test_does_not_overwrite_unmentioned_keys(self, tmp_path):
        _reset(tmp_path / "config.json")
        cm._data   = {**cm._DEFAULTS, "cookiefile": "/existing.txt"}
        cm._loaded = True
        cm.update({"output_dir": "/new"})
        assert cm._data["cookiefile"] == "/existing.txt"


# reset_to_defaults #

class TestResetToDefaults:

    def test_replaces_all_data_with_defaults(self, tmp_path):
        _reset(tmp_path / "config.json")
        cm._data   = {"output_dir": "/custom", "last_quality": "custom_q"}
        cm._loaded = True
        cm.reset_to_defaults()
        assert cm._data["output_dir"]   == str(Path.home() / "Downloads")
        assert cm._data["last_quality"] == "bestvideo+bestaudio/best"

    def test_persists_defaults_to_disk(self, tmp_path):
        cfg = tmp_path / "config.json"
        _reset(cfg)
        cm._data   = {}
        cm._loaded = True
        cm.reset_to_defaults()
        parsed = json.loads(cfg.read_text(encoding="utf-8"))
        assert parsed["last_quality"] == "bestvideo+bestaudio/best"

    def test_new_defaults_present(self, tmp_path):
        _reset(tmp_path / "config.json")
        cm._data   = {}
        cm._loaded = True
        cm.reset_to_defaults()
        assert cm._data["ffmpeg_path"]            is None
        assert cm._data["loudness_normalization"] is False
        assert cm._data["loudness_target_lufs"]   == -14.0


# validate_paths #

class TestValidatePaths:

    def test_valid_output_dir_no_error(self, tmp_path):
        _reset(tmp_path / "config.json")
        cm._data   = {**cm._DEFAULTS, "output_dir": str(tmp_path)}
        cm._loaded = True
        assert "output_dir" not in cm.validate_paths()

    def test_missing_output_dir_returns_error(self, tmp_path):
        _reset(tmp_path / "config.json")
        cm._data   = {**cm._DEFAULTS, "output_dir": str(tmp_path / "nope")}
        cm._loaded = True
        assert "output_dir" in cm.validate_paths()

    def test_none_ffmpeg_path_no_error(self, tmp_path):
        _reset(tmp_path / "config.json")
        cm._data   = {**cm._DEFAULTS, "ffmpeg_path": None}
        cm._loaded = True
        assert "ffmpeg_path" not in cm.validate_paths()

    def test_missing_ffmpeg_file_returns_error(self, tmp_path):
        _reset(tmp_path / "config.json")
        cm._data   = {**cm._DEFAULTS, "ffmpeg_path": str(tmp_path / "ffmpeg.exe")}
        cm._loaded = True
        assert "ffmpeg_path" in cm.validate_paths()

    def test_valid_ffmpeg_binary_no_error(self, tmp_path):
        binary = tmp_path / "ffmpeg.exe"
        binary.write_text("fake")
        _reset(tmp_path / "config.json")
        cm._data   = {**cm._DEFAULTS, "ffmpeg_path": str(binary)}
        cm._loaded = True
        assert "ffmpeg_path" not in cm.validate_paths()

    def test_ffmpeg_dir_without_binary_returns_error(self, tmp_path):
        ffdir = tmp_path / "ffdir"
        ffdir.mkdir()
        _reset(tmp_path / "config.json")
        cm._data   = {**cm._DEFAULTS, "ffmpeg_path": str(ffdir)}
        cm._loaded = True
        assert "ffmpeg_path" in cm.validate_paths()

    def test_none_cookiefile_no_error(self, tmp_path):
        _reset(tmp_path / "config.json")
        cm._data   = {**cm._DEFAULTS, "cookiefile": None}
        cm._loaded = True
        assert "cookiefile" not in cm.validate_paths()

    def test_missing_cookiefile_returns_error(self, tmp_path):
        _reset(tmp_path / "config.json")
        cm._data   = {**cm._DEFAULTS, "cookiefile": str(tmp_path / "cookies.txt")}
        cm._loaded = True
        assert "cookiefile" in cm.validate_paths()

    def test_valid_cookiefile_no_error(self, tmp_path):
        cookie = tmp_path / "cookies.txt"
        cookie.write_text("data")
        _reset(tmp_path / "config.json")
        cm._data   = {**cm._DEFAULTS, "cookiefile": str(cookie)}
        cm._loaded = True
        assert "cookiefile" not in cm.validate_paths()

    def test_multiple_bad_paths_all_reported(self, tmp_path):
        _reset(tmp_path / "config.json")
        cm._data = {
            **cm._DEFAULTS,
            "output_dir": str(tmp_path / "nope"),
            "cookiefile": str(tmp_path / "nope.txt"),
        }
        cm._loaded = True
        errors = cm.validate_paths()
        assert "output_dir" in errors
        assert "cookiefile" in errors

    def test_all_valid_returns_empty_dict(self, tmp_path):
        cookie = tmp_path / "cookies.txt"
        cookie.write_text("data")
        _reset(tmp_path / "config.json")
        cm._data = {
            **cm._DEFAULTS,
            "output_dir": str(tmp_path),
            "ffmpeg_path": None,
            "cookiefile":  str(cookie),
        }
        cm._loaded = True
        assert cm.validate_paths() == {}


# new defaults #

class TestNewDefaults:

    def test_ffmpeg_path_default_is_none(self, tmp_path):
        _reset(tmp_path / "config.json")
        cm.load()
        assert cm._data.get("ffmpeg_path") is None

    def test_loudness_normalization_default_is_false(self, tmp_path):
        _reset(tmp_path / "config.json")
        cm.load()
        assert cm._data.get("loudness_normalization") is False

    def test_loudness_target_lufs_default(self, tmp_path):
        _reset(tmp_path / "config.json")
        cm.load()
        assert cm._data.get("loudness_target_lufs") == -14.0
