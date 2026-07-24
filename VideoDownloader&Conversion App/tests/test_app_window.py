"""
tests/test_app_window.py

Tests for ui/app_window.py — focuses on the pure resolve_active_tab() function.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ui.app_window import resolve_active_tab


# known tab names #

class TestKnownNames:

    def test_download_passes_through(self):
        assert resolve_active_tab("Download") == "Download"

    def test_convert_passes_through(self):
        assert resolve_active_tab("Convert") == "Convert"


# fallbacks #

class TestFallback:

    def test_missing_value_falls_back(self):
        assert resolve_active_tab(None) == "Download"

    def test_empty_string_falls_back(self):
        assert resolve_active_tab("") == "Download"

    def test_unknown_name_falls_back(self):
        assert resolve_active_tab("Library") == "Download"

    def test_wrong_case_falls_back(self):
        assert resolve_active_tab("download") == "Download"

    @pytest.mark.parametrize("value", [0, 1, True, 3.5, [], {}, ("Download",)])
    def test_non_string_falls_back(self, value):
        assert resolve_active_tab(value) == "Download"

    def test_never_raises_on_unhashable(self):
        assert resolve_active_tab({"tab": "Convert"}) == "Download"


# custom valid_names #

class TestCustomValidNames:

    def test_honours_supplied_names(self):
        assert resolve_active_tab("Extras", ("Download", "Extras")) == "Extras"

    def test_name_absent_from_supplied_names_falls_back(self):
        assert resolve_active_tab("Convert", ("Download",)) == "Download"

    def test_empty_names_always_falls_back(self):
        assert resolve_active_tab("Convert", ()) == "Download"
