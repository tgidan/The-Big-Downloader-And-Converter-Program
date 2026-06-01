"""
tests/test_settings_panel.py

Tests for ui/settings_panel.py — focuses on the pure validate_settings() function.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ui.settings_panel import validate_settings


# Helpers #

"""Return a dict of fully-valid inputs, with any overrides applied."""
def _inputs(tmp_path, **overrides) -> dict:
    base = {
        "output_dir":             str(tmp_path),
        "ffmpeg_path":            "",
        "loudness_normalization": False,
        "loudness_target_lufs":   "-14.0",
        "cookiefile":             "",
    }
    base.update(overrides)
    return base


# output_dir #

class TestOutputDir:

    def test_existing_dir_no_error(self, tmp_path):
        assert "output_dir" not in validate_settings(**_inputs(tmp_path))

    def test_missing_dir_returns_error(self, tmp_path):
        inputs = _inputs(tmp_path, output_dir=str(tmp_path / "nope"))
        assert "output_dir" in validate_settings(**inputs)

    def test_empty_string_no_error(self, tmp_path):
        assert "output_dir" not in validate_settings(**_inputs(tmp_path, output_dir=""))


# ffmpeg_path #

class TestFfmpegPath:

    def test_empty_path_no_error(self, tmp_path):
        assert "ffmpeg_path" not in validate_settings(**_inputs(tmp_path))

    def test_nonexistent_path_returns_error(self, tmp_path):
        inputs = _inputs(tmp_path, ffmpeg_path=str(tmp_path / "ffmpeg.exe"))
        assert "ffmpeg_path" in validate_settings(**inputs)

    def test_valid_binary_file_no_error(self, tmp_path):
        binary = tmp_path / "ffmpeg.exe"
        binary.write_text("fake")
        inputs = _inputs(tmp_path, ffmpeg_path=str(binary))
        assert "ffmpeg_path" not in validate_settings(**inputs)

    def test_dir_without_binary_returns_error(self, tmp_path):
        ffdir = tmp_path / "ffdir"
        ffdir.mkdir()
        inputs = _inputs(tmp_path, ffmpeg_path=str(ffdir))
        assert "ffmpeg_path" in validate_settings(**inputs)

    def test_dir_with_binary_no_error(self, tmp_path):
        ffdir = tmp_path / "ffdir"
        ffdir.mkdir()
        binary_name = "ffmpeg.exe" if sys.platform == "win32" else "ffmpeg"
        (ffdir / binary_name).write_text("fake")
        inputs = _inputs(tmp_path, ffmpeg_path=str(ffdir))
        assert "ffmpeg_path" not in validate_settings(**inputs)


# loudness normalization #

class TestLoudnessNormalization:

    def test_off_ignores_invalid_lufs(self, tmp_path):
        inputs = _inputs(tmp_path, loudness_normalization=False, loudness_target_lufs="garbage")
        assert "lufs" not in validate_settings(**inputs)

    def test_on_valid_lufs_no_error(self, tmp_path):
        inputs = _inputs(tmp_path, loudness_normalization=True, loudness_target_lufs="-14.0")
        assert "lufs" not in validate_settings(**inputs)

    def test_on_non_numeric_lufs_returns_error(self, tmp_path):
        inputs = _inputs(tmp_path, loudness_normalization=True, loudness_target_lufs="abc")
        assert "lufs" in validate_settings(**inputs)

    def test_on_lufs_above_zero_returns_error(self, tmp_path):
        inputs = _inputs(tmp_path, loudness_normalization=True, loudness_target_lufs="1.0")
        assert "lufs" in validate_settings(**inputs)

    def test_on_lufs_below_minus_seventy_returns_error(self, tmp_path):
        inputs = _inputs(tmp_path, loudness_normalization=True, loudness_target_lufs="-71.0")
        assert "lufs" in validate_settings(**inputs)

    @pytest.mark.parametrize("lufs", ["-70.0", "-23.0", "-14.0", "0.0"])
    def test_on_boundary_values_no_error(self, tmp_path, lufs):
        inputs = _inputs(tmp_path, loudness_normalization=True, loudness_target_lufs=lufs)
        assert "lufs" not in validate_settings(**inputs)


# cookiefile #

class TestCookiefile:

    def test_empty_string_no_error(self, tmp_path):
        assert "cookiefile" not in validate_settings(**_inputs(tmp_path))

    def test_missing_file_returns_error(self, tmp_path):
        inputs = _inputs(tmp_path, cookiefile=str(tmp_path / "cookies.txt"))
        assert "cookiefile" in validate_settings(**inputs)

    def test_existing_file_no_error(self, tmp_path):
        cookie = tmp_path / "cookies.txt"
        cookie.write_text("data")
        inputs = _inputs(tmp_path, cookiefile=str(cookie))
        assert "cookiefile" not in validate_settings(**inputs)


# multiple errors #

class TestMultipleErrors:

    def test_all_bad_paths_reported_together(self, tmp_path):
        inputs = _inputs(
            tmp_path,
            output_dir=str(tmp_path / "nope"),
            cookiefile=str(tmp_path / "nope.txt"),
        )
        errors = validate_settings(**inputs)
        assert "output_dir" in errors
        assert "cookiefile" in errors

    def test_all_valid_returns_empty_dict(self, tmp_path):
        assert validate_settings(**_inputs(tmp_path)) == {}
