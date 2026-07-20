"""
tests/test_library_manager.py

Tests for core/library_manager.py.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.library_manager import (
    DirectoryNode,
    VideoFile,
    _resolve_ffprobe,
    get_directory_tree,
    list_videos,
    search_videos,
    fill_metadata,
    create_subfolder,
    move_video,
)


# Helpers #

"""Build a small nested tree: root/Movies/Action, root/.hidden, root/top.mp4."""
def _make_tree(root: Path) -> None:
    (root / "Movies" / "Action").mkdir(parents=True)
    (root / "Movies" / "Comedy").mkdir(parents=True)
    (root / ".hidden").mkdir()
    (root / "top.mp4").write_text("x")


# _resolve_ffprobe #

class TestResolveFfprobe:

    def test_directory_with_ffprobe_windows(self, tmp_path):
        (tmp_path / "ffprobe.exe").touch()
        with patch("core.library_manager.sys.platform", "win32"):
            result = _resolve_ffprobe(str(tmp_path))
        assert result == str(tmp_path / "ffprobe.exe")

    def test_directory_with_ffprobe_posix(self, tmp_path):
        (tmp_path / "ffprobe").touch()
        with patch("core.library_manager.sys.platform", "linux"):
            result = _resolve_ffprobe(str(tmp_path))
        assert result == str(tmp_path / "ffprobe")

    def test_binary_path_resolves_sibling_ffprobe(self, tmp_path):
        (tmp_path / "ffmpeg.exe").touch()
        (tmp_path / "ffprobe.exe").touch()
        with patch("core.library_manager.sys.platform", "win32"):
            result = _resolve_ffprobe(str(tmp_path / "ffmpeg.exe"))
        assert result == str(tmp_path / "ffprobe.exe")

    def test_directory_without_ffprobe_falls_back_to_path(self, tmp_path):
        with patch("core.library_manager.shutil.which", return_value="/usr/bin/ffprobe"):
            result = _resolve_ffprobe(str(tmp_path))
        assert result == "/usr/bin/ffprobe"

    def test_none_with_ffprobe_on_path_returns_path_result(self):
        with patch("core.library_manager.shutil.which", return_value="/usr/bin/ffprobe"):
            result = _resolve_ffprobe(None)
        assert result == "/usr/bin/ffprobe"

    def test_none_without_ffprobe_anywhere_returns_none(self):
        with patch("core.library_manager.shutil.which", return_value=None):
            result = _resolve_ffprobe(None)
        assert result is None


# get_directory_tree #

class TestGetDirectoryTree:

    def test_returns_directory_node(self, tmp_path):
        tree = get_directory_tree(str(tmp_path))
        assert isinstance(tree, DirectoryNode)
        assert tree.path == str(tmp_path)

    def test_nested_subdirectories_present(self, tmp_path):
        _make_tree(tmp_path)
        tree = get_directory_tree(str(tmp_path))
        names = {child.name for child in tree.children}
        assert "Movies" in names

        movies = next(c for c in tree.children if c.name == "Movies")
        child_names = {c.name for c in movies.children}
        assert child_names == {"Action", "Comedy"}

    def test_hidden_directories_skipped(self, tmp_path):
        _make_tree(tmp_path)
        tree = get_directory_tree(str(tmp_path))
        names = {child.name for child in tree.children}
        assert ".hidden" not in names

    def test_hidden_directory_skipped_at_nested_depth(self, tmp_path):
        (tmp_path / "Movies" / ".hidden_nested").mkdir(parents=True)
        tree = get_directory_tree(str(tmp_path))
        movies = next(c for c in tree.children if c.name == "Movies")
        assert all(not c.name.startswith(".") for c in movies.children)

    def test_files_are_not_included_as_children(self, tmp_path):
        (tmp_path / "top.mp4").write_text("x")
        tree = get_directory_tree(str(tmp_path))
        assert tree.children == []

    def test_empty_directory_has_no_children(self, tmp_path):
        tree = get_directory_tree(str(tmp_path))
        assert tree.children == []


# list_videos #

class TestListVideos:

    def test_video_files_included(self, tmp_path):
        (tmp_path / "movie.mp4").write_text("x")
        (tmp_path / "clip.mkv").write_text("x")
        result = list_videos(str(tmp_path))
        names = {v.name for v in result}
        assert names == {"movie", "clip"}

    def test_non_video_files_ignored(self, tmp_path):
        (tmp_path / "movie.mp4").write_text("x")
        (tmp_path / "readme.txt").write_text("x")
        (tmp_path / "poster.jpg").write_text("x")
        result = list_videos(str(tmp_path))
        assert [v.name for v in result] == ["movie"]

    def test_non_recursive_ignores_subdirectory_files(self, tmp_path):
        (tmp_path / "top.mp4").write_text("x")
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "nested.mp4").write_text("x")
        result = list_videos(str(tmp_path))
        assert [v.name for v in result] == ["top"]

    def test_record_has_path_name_and_size(self, tmp_path):
        f = tmp_path / "movie.mp4"
        f.write_text("hello world")
        result = list_videos(str(tmp_path))
        v = result[0]
        assert v.path == str(f)
        assert v.name == "movie"
        assert v.size_bytes == len("hello world")

    def test_resolution_and_duration_unset_by_default(self, tmp_path):
        (tmp_path / "movie.mp4").write_text("x")
        v = list_videos(str(tmp_path))[0]
        assert v.resolution is None
        assert v.duration is None

    def test_audio_extension_included(self, tmp_path):
        (tmp_path / "song.mp3").write_text("x")
        result = list_videos(str(tmp_path))
        assert [v.name for v in result] == ["song"]

    def test_nonexistent_directory_returns_empty_list(self, tmp_path):
        assert list_videos(str(tmp_path / "nope")) == []

    def test_case_insensitive_extension_match(self, tmp_path):
        (tmp_path / "movie.MP4").write_text("x")
        result = list_videos(str(tmp_path))
        assert [v.name for v in result] == ["movie"]


# search_videos #

class TestSearchVideos:

    def test_matches_across_nested_directories(self, tmp_path):
        (tmp_path / "Movies" / "Action").mkdir(parents=True)
        (tmp_path / "top match.mp4").write_text("x")
        (tmp_path / "Movies" / "Action" / "another match.mp4").write_text("x")
        (tmp_path / "Movies" / "no hit here.mp4").write_text("x")

        result = search_videos(str(tmp_path), "match")
        names = {v.name for v in result}
        assert names == {"top match", "another match"}

    def test_case_insensitive(self, tmp_path):
        (tmp_path / "Interstellar.mp4").write_text("x")
        result = search_videos(str(tmp_path), "INTERSTELLAR")
        assert [v.name for v in result] == ["Interstellar"]

    def test_partial_substring_match(self, tmp_path):
        (tmp_path / "The Great Escape.mp4").write_text("x")
        result = search_videos(str(tmp_path), "great")
        assert [v.name for v in result] == ["The Great Escape"]

    def test_no_match_returns_empty_list(self, tmp_path):
        (tmp_path / "movie.mp4").write_text("x")
        assert search_videos(str(tmp_path), "nonexistent") == []

    def test_hidden_directories_excluded_from_search(self, tmp_path):
        hidden = tmp_path / ".hidden"
        hidden.mkdir()
        (hidden / "match.mp4").write_text("x")
        assert search_videos(str(tmp_path), "match") == []

    def test_non_video_files_excluded_from_search(self, tmp_path):
        (tmp_path / "match.txt").write_text("x")
        assert search_videos(str(tmp_path), "match") == []


# fill_metadata #

class TestFillMetadata:

    _FFPROBE_JSON = json.dumps({
        "streams": [
            {"codec_type": "video", "width": 1920, "height": 1080, "duration": "220.000000"},
            {"codec_type": "audio", "duration": "220.100000"},
        ],
        "format": {"duration": "220.100000"},
    })

    def _video(self, tmp_path) -> VideoFile:
        f = tmp_path / "movie.mp4"
        f.write_text("x" * 10)
        return VideoFile(path=str(f), name="movie", size_bytes=10)

    def test_returns_size_only_when_ffprobe_unavailable(self, tmp_path):
        video = self._video(tmp_path)
        with patch("core.library_manager._resolve_ffprobe", return_value=None):
            result = fill_metadata(video)
        assert result.size_bytes == 10
        assert result.resolution is None
        assert result.duration is None

    def test_does_not_raise_when_ffprobe_unavailable(self, tmp_path):
        video = self._video(tmp_path)
        with patch("core.library_manager._resolve_ffprobe", return_value=None):
            fill_metadata(video)  # must not raise

    def test_populates_resolution_and_duration_on_success(self, tmp_path):
        video = self._video(tmp_path)
        mock_result = MagicMock(stdout=self._FFPROBE_JSON)
        with patch("core.library_manager._resolve_ffprobe", return_value="/usr/bin/ffprobe"):
            with patch("core.library_manager.subprocess.run", return_value=mock_result):
                result = fill_metadata(video)
        assert result.resolution == "1920x1080"
        assert result.duration == 220.1

    def test_prefers_format_duration_over_stream_duration(self, tmp_path):
        video = self._video(tmp_path)
        data = json.loads(self._FFPROBE_JSON)
        data["streams"][0]["duration"] = "999.000000"
        data["format"]["duration"] = "220.100000"
        mock_result = MagicMock(stdout=json.dumps(data))
        with patch("core.library_manager._resolve_ffprobe", return_value="/usr/bin/ffprobe"):
            with patch("core.library_manager.subprocess.run", return_value=mock_result):
                result = fill_metadata(video)
        assert result.duration == 220.1

    def test_falls_back_to_stream_duration_when_format_lacks_it(self, tmp_path):
        video = self._video(tmp_path)
        data = json.loads(self._FFPROBE_JSON)
        data["format"] = {}
        mock_result = MagicMock(stdout=json.dumps(data))
        with patch("core.library_manager._resolve_ffprobe", return_value="/usr/bin/ffprobe"):
            with patch("core.library_manager.subprocess.run", return_value=mock_result):
                result = fill_metadata(video)
        assert result.duration == 220.0

    def test_swallows_subprocess_timeout(self, tmp_path):
        video = self._video(tmp_path)
        with patch("core.library_manager._resolve_ffprobe", return_value="/usr/bin/ffprobe"):
            with patch(
                "core.library_manager.subprocess.run",
                side_effect=subprocess.TimeoutExpired(cmd="ffprobe", timeout=15),
            ):
                result = fill_metadata(video)
        assert result.size_bytes == 10
        assert result.resolution is None

    def test_swallows_malformed_json_output(self, tmp_path):
        video = self._video(tmp_path)
        mock_result = MagicMock(stdout="not json")
        with patch("core.library_manager._resolve_ffprobe", return_value="/usr/bin/ffprobe"):
            with patch("core.library_manager.subprocess.run", return_value=mock_result):
                result = fill_metadata(video)
        assert result.size_bytes == 10
        assert result.resolution is None

    def test_swallows_oserror_from_subprocess(self, tmp_path):
        video = self._video(tmp_path)
        with patch("core.library_manager._resolve_ffprobe", return_value="/usr/bin/ffprobe"):
            with patch("core.library_manager.subprocess.run", side_effect=OSError("no such file")):
                result = fill_metadata(video)
        assert result.size_bytes == 10
        assert result.resolution is None

    def test_missing_video_stream_leaves_resolution_none(self, tmp_path):
        video = self._video(tmp_path)
        data = {"streams": [{"codec_type": "audio", "duration": "5.0"}], "format": {"duration": "5.0"}}
        mock_result = MagicMock(stdout=json.dumps(data))
        with patch("core.library_manager._resolve_ffprobe", return_value="/usr/bin/ffprobe"):
            with patch("core.library_manager.subprocess.run", return_value=mock_result):
                result = fill_metadata(video)
        assert result.resolution is None
        assert result.duration == 5.0


# create_subfolder #

class TestCreateSubfolder:

    def test_creates_directory_under_parent(self, tmp_path):
        result = create_subfolder(str(tmp_path), str(tmp_path), "New Folder")
        assert result.is_dir()
        assert result == tmp_path / "New Folder"

    def test_sanitizes_illegal_characters_in_name(self, tmp_path):
        result = create_subfolder(str(tmp_path), str(tmp_path), 'Bad*Name?')
        assert result.name == "BadName"
        assert result.is_dir()

    def test_creates_under_nested_parent(self, tmp_path):
        parent = tmp_path / "Movies"
        parent.mkdir()
        result = create_subfolder(str(tmp_path), str(parent), "Action")
        assert result == parent / "Action"
        assert result.is_dir()

    def test_refuses_parent_outside_root(self, tmp_path):
        outside = tmp_path.parent / "outside_root_dir"
        outside.mkdir(exist_ok=True)
        try:
            with pytest.raises(ValueError):
                create_subfolder(str(tmp_path), str(outside), "New Folder")
        finally:
            outside.rmdir()

    def test_dot_dot_name_cannot_escape_root(self, tmp_path):
        # sanitize_filename strips a name that is only dots down to "download",
        # so ".." can never become a literal path-traversal component.
        result = create_subfolder(str(tmp_path), str(tmp_path), "..")
        assert result == tmp_path / "download"
        assert tmp_path in result.parents

    def test_existing_directory_is_a_no_op(self, tmp_path):
        (tmp_path / "Existing").mkdir()
        result = create_subfolder(str(tmp_path), str(tmp_path), "Existing")
        assert result == tmp_path / "Existing"
        assert result.is_dir()


# move_video #

class TestMoveVideo:

    def test_moves_file_to_destination(self, tmp_path):
        src_dir = tmp_path / "src"
        dest_dir = tmp_path / "dest"
        src_dir.mkdir()
        dest_dir.mkdir()
        src = src_dir / "movie.mp4"
        src.write_text("content")

        result = move_video(str(src), str(dest_dir))

        assert result == dest_dir / "movie.mp4"
        assert result.read_text() == "content"
        assert not src.exists()

    def test_colliding_destination_name_is_not_overwritten(self, tmp_path):
        src_dir = tmp_path / "src"
        dest_dir = tmp_path / "dest"
        src_dir.mkdir()
        dest_dir.mkdir()

        existing = dest_dir / "movie.mp4"
        existing.write_text("original destination content")

        src = src_dir / "movie.mp4"
        src.write_text("incoming content")

        result = move_video(str(src), str(dest_dir))

        assert result == dest_dir / "movie (1).mp4"
        assert existing.read_text() == "original destination content"
        assert result.read_text() == "incoming content"
        assert not src.exists()

    def test_multiple_collisions_increment(self, tmp_path):
        dest_dir = tmp_path / "dest"
        dest_dir.mkdir()
        (dest_dir / "movie.mp4").write_text("a")
        (dest_dir / "movie (1).mp4").write_text("b")

        src = tmp_path / "movie.mp4"
        src.write_text("c")

        result = move_video(str(src), str(dest_dir))
        assert result == dest_dir / "movie (2).mp4"
