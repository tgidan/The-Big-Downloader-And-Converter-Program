"""
tests/test_converter_panel.py

Tests for ui/converter_panel.py — the pure, widget-free helpers.  Importing
the module must not build any widget, same as tests/test_app_window.py.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ui.converter_panel import resolve_convert_dir, status_text, truncate_filename


# resolve_convert_dir #

class TestResolveConvertDir:

    def test_configured_value_wins(self):
        assert resolve_convert_dir("/conversions", "/downloads") == "/conversions"

    """None means 'follow the download folder' — the default for a fresh config."""
    def test_none_falls_back(self):
        assert resolve_convert_dir(None, "/downloads") == "/downloads"

    def test_empty_string_falls_back(self):
        assert resolve_convert_dir("", "/downloads") == "/downloads"

    def test_whitespace_only_falls_back(self):
        assert resolve_convert_dir("   ", "/downloads") == "/downloads"

    @pytest.mark.parametrize("value", [0, 1, True, 3.5, [], {}, ("/x",)])
    def test_non_string_falls_back(self, value):
        assert resolve_convert_dir(value, "/downloads") == "/downloads"

    def test_never_raises_on_unhashable(self):
        assert resolve_convert_dir({"dir": "/x"}, "/downloads") == "/downloads"


# status_text #

class TestStatusText:

    def test_pending(self):
        assert status_text("pending") == "pending"

    def test_converting_with_percent(self):
        assert status_text("converting", 42.4) == "converting 42%"

    def test_converting_rounds_to_whole_percent(self):
        assert status_text("converting", 99.6) == "converting 100%"

    """An unknown source duration must read as indeterminate, not '0%'."""
    def test_converting_with_none_percent(self):
        assert status_text("converting", None) == "converting…"

    def test_converting_zero_percent_still_shows_a_number(self):
        assert status_text("converting", 0.0) == "converting 0%"

    def test_done(self):
        assert "done" in status_text("done")

    def test_error(self):
        assert status_text("error") == "error"

    def test_cancelled(self):
        assert status_text("cancelled") == "cancelled"

    def test_unknown_status_passes_through(self):
        assert status_text("weird") == "weird"


# truncate_filename #

class TestTruncateFilename:

    def test_short_name_unchanged(self):
        assert truncate_filename("clip.mp3", 30) == "clip.mp3"

    def test_name_at_limit_unchanged(self):
        name = "a" * 30
        assert truncate_filename(name, 30) == name

    def test_long_name_is_shortened_to_the_limit(self):
        result = truncate_filename("x" * 100, 30)
        assert len(result) == 30

    def test_long_name_keeps_head_and_tail(self):
        result = truncate_filename("beginning_middle_padding_end.mp3", 20)
        assert result.startswith("beginning")
        assert result.endswith(".mp3")
        assert "…" in result

    """Middle elision keeps the extension visible, unlike a plain suffix cut."""
    def test_extension_survives_truncation(self):
        assert truncate_filename("a" * 60 + ".mp3", 24).endswith(".mp3")

    def test_unicode_name_handled(self):
        result = truncate_filename("Ünïcödé  ñame — a very long title here.mp3", 20)
        assert len(result) == 20
        assert result.startswith("Ünïcödé")

    def test_limit_of_one_returns_name_unchanged(self):
        assert truncate_filename("abcdef", 1) == "abcdef"

    def test_empty_name(self):
        assert truncate_filename("", 30) == ""
