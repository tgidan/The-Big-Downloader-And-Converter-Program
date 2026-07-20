"""
tests/test_utils.py

Tests for core/utils.py.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.utils import find_conflicts, sanitize_filename, unique_path


# illegal characters #

class TestIllegalChars:

    def test_removes_all_illegal_chars(self):
        assert sanitize_filename('a\\b/c*d?e:f"g<h>i|j') == "abcdefghij"

    def test_leaves_normal_punctuation(self):
        assert sanitize_filename("Rick, Morty - S01E01!") == "Rick, Morty - S01E01!"


# control characters #

class TestControlChars:

    def test_strips_control_characters(self):
        assert sanitize_filename("Hello\x00\x1fWorld") == "HelloWorld"


# whitespace collapse #

class TestWhitespaceCollapse:

    def test_collapses_internal_whitespace_runs(self):
        assert sanitize_filename("My    Video     Title") == "My Video Title"

    def test_tabs_and_newlines_are_stripped_as_control_chars(self):
        # \t and \n are ASCII control chars (0x09, 0x0a) and are removed by the
        # control-char step before whitespace collapsing ever sees them.
        assert sanitize_filename("Line1\n\n\tLine2") == "Line1Line2"


# leading/trailing dots #

class TestDotStripping:

    def test_strips_leading_and_trailing_dots(self):
        assert sanitize_filename("...secret.file...") == "secret.file"

    def test_strips_single_leading_dot(self):
        assert sanitize_filename(".hidden") == "hidden"


# reserved device names #

class TestReservedNames:

    def test_bare_reserved_name_is_prefixed(self):
        assert sanitize_filename("CON") == "_CON"

    def test_reserved_name_case_insensitive(self):
        assert sanitize_filename("con") == "_con"

    def test_reserved_name_with_extension_is_prefixed(self):
        assert sanitize_filename("PRN.txt") == "_PRN.txt"

    def test_reserved_com_port_is_prefixed(self):
        assert sanitize_filename("COM1") == "_COM1"

    def test_reserved_lpt_port_is_prefixed(self):
        assert sanitize_filename("LPT9") == "_LPT9"

    def test_non_reserved_name_containing_reserved_substring_untouched(self):
        assert sanitize_filename("CONsole Longplay") == "CONsole Longplay"

    def test_com_ten_is_not_reserved(self):
        assert sanitize_filename("COM10") == "COM10"


# 200-char cap #

class TestLengthCap:

    def test_truncates_to_200_chars(self):
        name = "a" * 250
        result = sanitize_filename(name)
        assert len(result) == 200
        assert result == "a" * 200

    def test_truncation_does_not_leave_trailing_dot_or_space(self):
        name = "a" * 198 + " ." + "b" * 10
        result = sanitize_filename(name)
        assert result == "a" * 198
        assert not result.endswith(".")
        assert not result.endswith(" ")


# empty / whitespace-only input #

class TestEmptyInput:

    def test_empty_string_returns_download(self):
        assert sanitize_filename("") == "download"

    def test_whitespace_only_returns_download(self):
        assert sanitize_filename("   \t\n  ") == "download"

    def test_only_illegal_chars_returns_download(self):
        assert sanitize_filename("***???:::") == "download"

    def test_only_dots_returns_download(self):
        assert sanitize_filename("...") == "download"


# unicode #

class TestUnicode:

    def test_japanese_title_survives_intact(self):
        assert sanitize_filename("日本語のタイトル") == "日本語のタイトル"

    def test_emoji_survives_intact(self):
        assert sanitize_filename("Amazing Trip 🎥🌍") == "Amazing Trip 🎥🌍"

    def test_mixed_unicode_with_illegal_chars(self):
        assert sanitize_filename("日本語:タイトル?") == "日本語タイトル"


# idempotency #

class TestIdempotency:

    @pytest.mark.parametrize("raw", [
        'a\\b/c*d?e:f"g<h>i|j',
        "My    Video     Title",
        "...secret.file...",
        "CON",
        "con",
        "PRN.txt",
        "COM1",
        "a" * 250,
        "a" * 198 + " ." + "b" * 10,
        "",
        "   \t\n  ",
        "***???:::",
        "日本語のタイトル",
        "Amazing Trip 🎥🌍",
        "100% Real Footage",
    ])
    def test_double_sanitize_is_stable(self, raw):
        once = sanitize_filename(raw)
        twice = sanitize_filename(once)
        assert twice == once


# find_conflicts #

class TestFindConflicts:

    def test_no_matches_returns_empty_list(self, tmp_path):
        assert find_conflicts(str(tmp_path), "video") == []

    def test_matching_stem_returned(self, tmp_path):
        f = tmp_path / "video.mp4"
        f.write_text("x")
        assert find_conflicts(str(tmp_path), "video") == [f]

    def test_matches_regardless_of_extension(self, tmp_path):
        mp4 = tmp_path / "video.mp4"
        mp4.write_text("x")
        mp3 = tmp_path / "video.mp3"
        mp3.write_text("x")
        result = find_conflicts(str(tmp_path), "video")
        assert set(result) == {mp4, mp3}

    def test_case_insensitive_match(self, tmp_path):
        f = tmp_path / "Video.mp4"
        f.write_text("x")
        assert find_conflicts(str(tmp_path), "video") == [f]

    def test_directories_are_not_matches(self, tmp_path):
        (tmp_path / "video").mkdir()
        assert find_conflicts(str(tmp_path), "video") == []

    def test_partial_stem_is_not_a_match(self, tmp_path):
        f = tmp_path / "video2.mp4"
        f.write_text("x")
        assert find_conflicts(str(tmp_path), "video") == []

    def test_nonexistent_directory_returns_empty_list(self, tmp_path):
        assert find_conflicts(str(tmp_path / "missing"), "video") == []


# unique_path #

class TestUniquePath:

    def test_no_conflict_returns_plain_name(self, tmp_path):
        result = unique_path(str(tmp_path), "video", "mp4")
        assert result == tmp_path / "video.mp4"

    def test_single_conflict_appends_one(self, tmp_path):
        (tmp_path / "video.mp4").write_text("x")
        result = unique_path(str(tmp_path), "video", "mp4")
        assert result == tmp_path / "video (1).mp4"

    def test_conflict_ignores_extension(self, tmp_path):
        (tmp_path / "video.mp3").write_text("x")
        result = unique_path(str(tmp_path), "video", "mp4")
        assert result == tmp_path / "video (1).mp4"

    def test_multiple_conflicts_increment(self, tmp_path):
        (tmp_path / "video.mp4").write_text("x")
        (tmp_path / "video (1).mp4").write_text("x")
        result = unique_path(str(tmp_path), "video", "mp4")
        assert result == tmp_path / "video (2).mp4"

    def test_ext_with_leading_dot_normalised(self, tmp_path):
        result = unique_path(str(tmp_path), "video", ".mp4")
        assert result == tmp_path / "video.mp4"

    def test_empty_ext_produces_no_suffix(self, tmp_path):
        result = unique_path(str(tmp_path), "video", "")
        assert result == tmp_path / "video"
