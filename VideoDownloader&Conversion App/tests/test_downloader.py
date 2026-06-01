"""
tests/test_downloader.py

Tests for core/downloader.py.
"""

from __future__ import annotations

import queue
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yt_dlp

# Make 'core' importable when running pytest from any working directory
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.downloader import (
    _is_windows,
    _resolve_ffmpeg,
    _LoudnormPP,
    fetch_formats,
    build_video_format_string,
    build_audio_format_string,
    download,
    check_ytdlp_update,
)


# _is_windows #

def test_is_windows_true():
    with patch("sys.platform", "win32"):
        assert _is_windows() is True


def test_is_windows_false():
    with patch("sys.platform", "linux"):
        assert _is_windows() is False


# _resolve_ffmpeg #

class TestResolveFfmpeg:

    def test_explicit_directory_windows(self, tmp_path):
        (tmp_path / "ffmpeg.exe").touch()
        with patch("core.downloader._is_windows", return_value=True):
            result = _resolve_ffmpeg(str(tmp_path))
        assert result == str(tmp_path / "ffmpeg.exe")

    def test_explicit_directory_posix(self, tmp_path):
        (tmp_path / "ffmpeg").touch()
        with patch("core.downloader._is_windows", return_value=False):
            result = _resolve_ffmpeg(str(tmp_path))
        assert result == str(tmp_path / "ffmpeg")

    def test_explicit_file_path(self, tmp_path):
        binary = tmp_path / "ffmpeg.exe"
        binary.touch()
        result = _resolve_ffmpeg(str(binary))
        assert result == str(binary)

    def test_explicit_directory_missing_binary_raises(self, tmp_path):
        with patch("core.downloader._is_windows", return_value=True):
            with pytest.raises(RuntimeError, match="ffmpeg not found at the specified location"):
                _resolve_ffmpeg(str(tmp_path))

    def test_explicit_nonexistent_file_raises(self, tmp_path):
        with pytest.raises(RuntimeError, match="ffmpeg not found at the specified location"):
            _resolve_ffmpeg(str(tmp_path / "nonexistent_ffmpeg"))

    def test_none_with_ffmpeg_on_path_returns_none(self):
        with patch("core.downloader.shutil.which", return_value="/usr/bin/ffmpeg"):
            result = _resolve_ffmpeg(None)
        assert result is None

    def test_none_without_ffmpeg_on_path_raises(self):
        with patch("core.downloader.shutil.which", return_value=None):
            with pytest.raises(RuntimeError, match="ffmpeg is not installed"):
                _resolve_ffmpeg(None)


# ── fetch_formats ─────────────────────────────────────────────────────────────

"""Context-manager mock for yt_dlp.YoutubeDL returning fake info."""
def _ydl_ctx(info: dict):
    instance = MagicMock()
    instance.extract_info.return_value = info
    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=instance)
    ctx.__exit__ = MagicMock(return_value=False)
    return ctx


class TestFetchFormats:

    def _patch(self, info):
        return patch("core.downloader.yt_dlp.YoutubeDL", return_value=_ydl_ctx(info))

    def test_video_format_fields(self):
        info = {"formats": [{
            "format_id": "137", "ext": "mp4", "height": 1080, "fps": 30,
            "vcodec": "avc1", "acodec": "none", "filesize": 500_000, "format_note": None,
        }]}
        with self._patch(info):
            result = fetch_formats("http://example.com")
        assert len(result) == 1
        f = result[0]
        assert f["format_id"] == "137"
        assert f["ext"] == "mp4"
        assert f["height"] == 1080
        assert f["vcodec"] == "avc1"
        assert f["acodec"] == "none"
        assert f["filesize"] == 500_000

    def test_note_built_from_height_and_fps(self):
        info = {"formats": [{
            "format_id": "137", "ext": "mp4", "height": 1080, "fps": 30,
            "vcodec": "avc1", "acodec": "none", "filesize": None, "format_note": None,
        }]}
        with self._patch(info):
            result = fetch_formats("http://example.com")
        assert "1080p" in result[0]["note"]
        assert "30fps" in result[0]["note"]

    def test_note_built_from_height_without_fps(self):
        info = {"formats": [{
            "format_id": "22", "ext": "mp4", "height": 720, "fps": None,
            "vcodec": "avc1", "acodec": "mp4a", "filesize": None, "format_note": None,
        }]}
        with self._patch(info):
            result = fetch_formats("http://example.com")
        assert "720p" in result[0]["note"]
        assert "fps" not in result[0]["note"]

    def test_format_note_takes_priority_over_computed(self):
        info = {"formats": [{
            "format_id": "22", "ext": "mp4", "height": 720, "fps": None,
            "vcodec": "avc1", "acodec": "mp4a", "filesize": None, "format_note": "HD",
        }]}
        with self._patch(info):
            result = fetch_formats("http://example.com")
        assert result[0]["note"] == "HD"

    def test_audio_only_note_and_filesize_approx(self):
        info = {"formats": [{
            "format_id": "251", "ext": "webm", "height": None, "fps": None,
            "vcodec": "none", "acodec": "opus", "filesize": None,
            "filesize_approx": 12_345, "format_note": None,
        }]}
        with self._patch(info):
            result = fetch_formats("http://example.com")
        f = result[0]
        assert f["height"] is None
        assert "audio only" in f["note"]
        assert f["filesize"] == 12_345

    def test_empty_formats_returns_empty_list(self):
        with self._patch({"formats": []}):
            assert fetch_formats("http://example.com") == []

    def test_multiple_formats_preserve_order(self):
        info = {"formats": [
            {"format_id": "1", "ext": "mp4", "height": 360, "fps": None,
             "vcodec": "avc1", "acodec": "mp4a", "filesize": 1000, "format_note": None},
            {"format_id": "2", "ext": "mp4", "height": 720, "fps": 60,
             "vcodec": "avc1", "acodec": "mp4a", "filesize": 2000, "format_note": None},
        ]}
        with self._patch(info):
            result = fetch_formats("http://example.com")
        assert [f["format_id"] for f in result] == ["1", "2"]


# ── format string helpers ─────────────────────────────────────────────────────

@pytest.mark.parametrize("height,expected", [
    (360,  "bestvideo[height<=360]+bestaudio/best"),
    (720,  "bestvideo[height<=720]+bestaudio/best"),
    (1080, "bestvideo[height<=1080]+bestaudio/best"),
    (2160, "bestvideo[height<=2160]+bestaudio/best"),
])
def test_build_video_format_string(height, expected):
    assert build_video_format_string(height) == expected


def test_build_audio_format_string():
    assert build_audio_format_string() == "bestaudio/best"


# ── download ──────────────────────────────────────────────────────────────────

"""
Captures ydl_opts (including the progress_hook closure) so the hook can be
exercised directly without touching the network.
"""
class TestDownload:

    def _make_fake_ydl_cls(self, captured: dict):
        class FakeYDL:
            def __init__(self_, opts):
                captured.update(opts)
                captured.setdefault("_post_processors", [])
                self_.params = opts   # required by FFmpegPostProcessor.get_param()
            def __enter__(self_): return self_
            def __exit__(self_, *a): return False
            def download(self_, urls): pass
            def add_post_processor(self_, pp, when="post_process"):
                captured["_post_processors"].append((pp, when))
        return FakeYDL

    def _run(self, captured: dict, *, url="http://example.com",
             fmt="best", output_dir, **kwargs) -> queue.Queue:
        q = queue.Queue()
        with patch("core.downloader.yt_dlp.YoutubeDL", self._make_fake_ydl_cls(captured)):
            with patch("core.downloader._resolve_ffmpeg", return_value=None):
                download(url, fmt, str(output_dir), q, **kwargs)
        return q

    # ── progress hook ────────────────────────────────────────────────────────

    def test_hook_downloading_basic(self, tmp_path):
        opts = {}
        q = self._run(opts, output_dir=tmp_path)
        opts["progress_hooks"][0]({
            "status": "downloading",
            "total_bytes": 1000, "downloaded_bytes": 250,
            "_speed_str": "500KB/s", "_eta_str": "2s", "filename": "vid.mp4",
        })
        assert q.get_nowait() == {
            "status": "downloading", "percent": 25.0,
            "speed": "500KB/s", "eta": "2s", "filename": "vid.mp4",
        }

    def test_hook_downloading_uses_estimate_when_total_bytes_none(self, tmp_path):
        opts = {}
        q = self._run(opts, output_dir=tmp_path)
        opts["progress_hooks"][0]({
            "status": "downloading",
            "total_bytes": None, "total_bytes_estimate": 2000,
            "downloaded_bytes": 1000,
            "_speed_str": "", "_eta_str": "", "filename": "",
        })
        assert q.get_nowait()["percent"] == 50.0

    def test_hook_downloading_zero_total_gives_zero_percent(self, tmp_path):
        opts = {}
        q = self._run(opts, output_dir=tmp_path)
        opts["progress_hooks"][0]({
            "status": "downloading",
            "total_bytes": None, "total_bytes_estimate": None,
            "downloaded_bytes": 500,
            "_speed_str": "", "_eta_str": "", "filename": "",
        })
        assert q.get_nowait()["percent"] == 0.0

    def test_hook_finished(self, tmp_path):
        opts = {}
        q = self._run(opts, output_dir=tmp_path)
        opts["progress_hooks"][0]({"status": "finished", "filename": "done.mp4"})
        assert q.get_nowait() == {"status": "finished", "filename": "done.mp4"}

    def test_hook_error(self, tmp_path):
        opts = {}
        q = self._run(opts, output_dir=tmp_path)
        opts["progress_hooks"][0]({"status": "error", "error": "write failed"})
        msg = q.get_nowait()
        assert msg["status"] == "error"
        assert msg["message"] == "write failed"

    def test_hook_error_fallback_message(self, tmp_path):
        opts = {}
        q = self._run(opts, output_dir=tmp_path)
        opts["progress_hooks"][0]({"status": "error"})  # no "error" key
        assert q.get_nowait()["message"] == "Unknown error"

    # ── DownloadError is caught ───────────────────────────────────────────────

    def test_download_error_puts_error_on_queue(self, tmp_path):
        q = queue.Queue()

        class FailYDL:
            def __init__(self, opts): pass
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def download(self, urls):
                raise yt_dlp.utils.DownloadError("network failure")

        with patch("core.downloader.yt_dlp.YoutubeDL", FailYDL):
            with patch("core.downloader._resolve_ffmpeg", return_value=None):
                download("http://example.com", "best", str(tmp_path), q)

        msg = q.get_nowait()
        assert msg["status"] == "error"
        assert "network failure" in msg["message"]

    # ── opts configuration ────────────────────────────────────────────────────

    def test_audio_only_adds_ffmpeg_extract_audio_postprocessor(self, tmp_path):
        opts = {}
        self._run(opts, output_dir=tmp_path, audio_only=True)
        assert any(p["key"] == "FFmpegExtractAudio" for p in opts["postprocessors"])

    def test_no_audio_only_has_no_postprocessors(self, tmp_path):
        opts = {}
        self._run(opts, output_dir=tmp_path, audio_only=False)
        assert "postprocessors" not in opts

    def test_cookiefile_passed_to_opts(self, tmp_path):
        opts = {}
        self._run(opts, output_dir=tmp_path, cookiefile="/cookies.txt")
        assert opts["cookiefile"] == "/cookies.txt"

    def test_no_cookiefile_absent_from_opts(self, tmp_path):
        opts = {}
        self._run(opts, output_dir=tmp_path)
        assert "cookiefile" not in opts

    def test_resolved_ffmpeg_location_added_to_opts(self, tmp_path):
        opts = {}
        q = queue.Queue()
        with patch("core.downloader.yt_dlp.YoutubeDL", self._make_fake_ydl_cls(opts)):
            with patch("core.downloader._resolve_ffmpeg", return_value="/usr/bin/ffmpeg"):
                download("http://example.com", "best", str(tmp_path), q)
        assert opts["ffmpeg_location"] == "/usr/bin/ffmpeg"

    def test_none_ffmpeg_not_added_to_opts(self, tmp_path):
        opts = {}
        self._run(opts, output_dir=tmp_path)
        assert "ffmpeg_location" not in opts

    def test_outtmpl_contains_output_dir(self, tmp_path):
        opts = {}
        self._run(opts, output_dir=tmp_path)
        assert str(tmp_path) in opts["outtmpl"]

    def test_format_string_passed_to_opts(self, tmp_path):
        opts = {}
        self._run(opts, output_dir=tmp_path, fmt="bestvideo+bestaudio")
        assert opts["format"] == "bestvideo+bestaudio"

    # ── loudness normalization ────────────────────────────────────────────────

    def test_loudness_normalization_off_no_pp_registered(self, tmp_path):
        opts = {}
        self._run(opts, output_dir=tmp_path, loudness_normalization=False)
        assert opts["_post_processors"] == []

    def test_loudness_normalization_off_no_postprocessor_args(self, tmp_path):
        opts = {}
        self._run(opts, output_dir=tmp_path, loudness_normalization=False)
        assert "postprocessor_args" not in opts

    def test_loudness_normalization_on_registers_one_pp(self, tmp_path):
        opts = {}
        self._run(opts, output_dir=tmp_path, loudness_normalization=True)
        assert len(opts["_post_processors"]) == 1

    def test_loudness_normalization_on_registers_after_move(self, tmp_path):
        opts = {}
        self._run(opts, output_dir=tmp_path, loudness_normalization=True)
        _, when = opts["_post_processors"][0]
        assert when == "after_move"

    def test_loudness_normalization_pp_is_loudnorm_type(self, tmp_path):
        opts = {}
        self._run(opts, output_dir=tmp_path, loudness_normalization=True)
        pp, _ = opts["_post_processors"][0]
        assert isinstance(pp, _LoudnormPP)

    def test_loudness_normalization_default_lufs_on_pp(self, tmp_path):
        opts = {}
        self._run(opts, output_dir=tmp_path, loudness_normalization=True)
        pp, _ = opts["_post_processors"][0]
        assert pp._target_lufs == -14.0

    def test_loudness_normalization_custom_lufs_on_pp(self, tmp_path):
        opts = {}
        self._run(opts, output_dir=tmp_path, loudness_normalization=True, loudness_target_lufs=-23.0)
        pp, _ = opts["_post_processors"][0]
        assert pp._target_lufs == -23.0

    def test_loudness_normalization_skipped_for_audio_only(self, tmp_path):
        opts = {}
        self._run(opts, output_dir=tmp_path, loudness_normalization=True, audio_only=True)
        assert opts["_post_processors"] == []

    def test_loudness_normalization_no_postprocessor_args_in_opts(self, tmp_path):
        opts = {}
        self._run(opts, output_dir=tmp_path, loudness_normalization=True)
        assert "postprocessor_args" not in opts


# ── _LoudnormPP ───────────────────────────────────────────────────────────────

class TestLoudnormPP:

    def _pp(self, lufs: float = -14.0) -> _LoudnormPP:
        return _LoudnormPP(None, target_lufs=lufs)

    def _mock_run_ffmpeg(self, pp: _LoudnormPP, captured: list):
        """Patch run_ffmpeg to write a sentinel file and record args."""
        def fake(inp, out, args):
            Path(out).write_text("normalized")
            captured.extend(args)
        pp.run_ffmpeg = fake

    def test_skips_missing_filepath_key(self):
        _, info = self._pp().run({})
        assert info == {}

    def test_skips_nonexistent_file(self, tmp_path):
        _, info = self._pp().run({"filepath": str(tmp_path / "nope.mp4")})
        assert info == {"filepath": str(tmp_path / "nope.mp4")}

    def test_replaces_original_with_normalized(self, tmp_path):
        src = tmp_path / "video.mp4"
        src.write_text("original")
        pp = self._pp()
        self._mock_run_ffmpeg(pp, [])
        pp.run({"filepath": str(src)})
        assert src.read_text() == "normalized"

    def test_filter_contains_loudnorm_with_lufs(self, tmp_path):
        src = tmp_path / "video.mp4"
        src.write_text("x")
        pp = self._pp(lufs=-23.0)
        args: list = []
        self._mock_run_ffmpeg(pp, args)
        pp.run({"filepath": str(src)})
        filter_str = " ".join(args)
        assert "loudnorm" in filter_str
        assert "I=-23" in filter_str

    def test_filter_tp_and_lra_constants(self, tmp_path):
        src = tmp_path / "video.mp4"
        src.write_text("x")
        pp = self._pp()
        args: list = []
        self._mock_run_ffmpeg(pp, args)
        pp.run({"filepath": str(src)})
        filter_str = " ".join(args)
        assert "TP=-1" in filter_str
        assert "LRA=11" in filter_str

    def test_video_stream_copied(self, tmp_path):
        src = tmp_path / "video.mp4"
        src.write_text("x")
        pp = self._pp()
        args: list = []
        self._mock_run_ffmpeg(pp, args)
        pp.run({"filepath": str(src)})
        assert "-c:v" in args
        assert "copy" in args

    def test_cleans_up_temp_on_ffmpeg_failure(self, tmp_path):
        src = tmp_path / "video.mp4"
        src.write_text("original")
        pp = self._pp()

        def failing_run_ffmpeg(inp, out, args):
            Path(out).write_text("partial")
            raise RuntimeError("ffmpeg crashed")

        pp.run_ffmpeg = failing_run_ffmpeg

        with pytest.raises(RuntimeError):
            pp.run({"filepath": str(src)})

        assert not list(tmp_path.glob("*.loudnorm*"))
        assert src.read_text() == "original"


# ── check_ytdlp_update ────────────────────────────────────────────────────────

class TestCheckYtdlpUpdate:

    def _run(self, stdout="", stderr="", raises=None):
        if raises is not None:
            with patch("core.downloader.subprocess.run", side_effect=raises):
                return check_ytdlp_update()
        mock_result = MagicMock()
        mock_result.stdout = stdout
        mock_result.stderr = stderr
        with patch("core.downloader.subprocess.run", return_value=mock_result):
            return check_ytdlp_update()

    def test_up_to_date_returns_none(self):
        assert self._run(stdout="yt-dlp is up to date") is None

    def test_up_to_date_case_insensitive(self):
        assert self._run(stdout="yt-dlp is Up To Date (2024.01.01)") is None

    def test_update_message_returned(self):
        assert self._run(stdout="Updated yt-dlp to 2024.09.01") == "Updated yt-dlp to 2024.09.01"

    def test_stderr_included_in_check(self):
        # "up to date" in stderr should still return None
        assert self._run(stderr="yt-dlp is up to date") is None

    def test_non_update_stderr_returned(self):
        result = self._run(stderr="New version: 2024.09.01")
        assert result == "New version: 2024.09.01"

    def test_empty_output_returns_none(self):
        assert self._run(stdout="", stderr="") is None

    def test_whitespace_only_output_returns_none(self):
        assert self._run(stdout="   ", stderr="  ") is None

    def test_generic_exception_returns_none(self):
        assert self._run(raises=Exception("timeout")) is None

    def test_timeout_expired_returns_none(self):
        assert self._run(raises=subprocess.TimeoutExpired(cmd="yt-dlp", timeout=10)) is None
