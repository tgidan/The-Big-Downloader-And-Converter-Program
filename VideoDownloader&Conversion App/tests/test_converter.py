"""
tests/test_converter.py

Tests for core/converter.py.  ffmpeg/ffprobe are mocked throughout — no real
binary is ever invoked.
"""

from __future__ import annotations

import json
import queue
import subprocess
import sys
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Make 'core' importable when running pytest from any working directory
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.converter import (
    ConversionResult,
    _binary_name,
    _is_windows,
    _last_error_line,
    _progress_seconds,
    _resolve_ffprobe_binary,
    convert,
    plan_output_path,
    probe,
    resolve_ffmpeg_binary,
    supported_formats,
)


# _is_windows / _binary_name #

def test_is_windows_true():
    with patch("sys.platform", "win32"):
        assert _is_windows() is True


def test_is_windows_false():
    with patch("sys.platform", "linux"):
        assert _is_windows() is False


def test_binary_name_windows():
    with patch("core.converter._is_windows", return_value=True):
        assert _binary_name("ffmpeg") == "ffmpeg.exe"


def test_binary_name_posix():
    with patch("core.converter._is_windows", return_value=False):
        assert _binary_name("ffmpeg") == "ffmpeg"


# resolve_ffmpeg_binary #

class TestResolveFfmpegBinary:

    def test_explicit_directory_windows(self, tmp_path):
        (tmp_path / "ffmpeg.exe").touch()
        with patch("core.converter._is_windows", return_value=True):
            assert resolve_ffmpeg_binary(str(tmp_path)) == str(tmp_path / "ffmpeg.exe")

    def test_explicit_directory_posix(self, tmp_path):
        (tmp_path / "ffmpeg").touch()
        with patch("core.converter._is_windows", return_value=False):
            assert resolve_ffmpeg_binary(str(tmp_path)) == str(tmp_path / "ffmpeg")

    def test_explicit_file_path(self, tmp_path):
        binary = tmp_path / "ffmpeg.exe"
        binary.touch()
        assert resolve_ffmpeg_binary(str(binary)) == str(binary)

    def test_explicit_directory_missing_binary_raises(self, tmp_path):
        with patch("core.converter._is_windows", return_value=True):
            with pytest.raises(RuntimeError, match="ffmpeg not found at the specified location"):
                resolve_ffmpeg_binary(str(tmp_path))

    def test_explicit_nonexistent_file_raises(self, tmp_path):
        with pytest.raises(RuntimeError, match="ffmpeg not found at the specified location"):
            resolve_ffmpeg_binary(str(tmp_path / "nope_ffmpeg"))

    """The whole reason this function exists: unlike downloader._resolve_ffmpeg,
    a PATH hit resolves to a real path instead of None."""
    def test_path_fallback_returns_concrete_path(self):
        with patch("core.converter.shutil.which", return_value="/usr/bin/ffmpeg"):
            result = resolve_ffmpeg_binary(None)
        assert result is not None
        assert result == "/usr/bin/ffmpeg"

    def test_empty_configured_path_uses_path_fallback(self):
        with patch("core.converter.shutil.which", return_value="/usr/bin/ffmpeg"):
            assert resolve_ffmpeg_binary("") == "/usr/bin/ffmpeg"

    def test_nothing_found_raises(self):
        with patch("core.converter.shutil.which", return_value=None):
            with pytest.raises(RuntimeError, match="ffmpeg is not installed"):
                resolve_ffmpeg_binary(None)

    def test_nothing_found_message_names_ffmpeg_and_the_fix(self):
        with patch("core.converter.shutil.which", return_value=None):
            with pytest.raises(RuntimeError) as exc:
                resolve_ffmpeg_binary(None)
        message = str(exc.value)
        assert "ffmpeg" in message
        assert "ffmpeg.org/download.html" in message
        assert "PATH" in message


# _resolve_ffprobe_binary #

class TestResolveFfprobeBinary:

    def test_directory_containing_ffprobe(self, tmp_path):
        (tmp_path / "ffprobe.exe").touch()
        with patch("core.converter._is_windows", return_value=True):
            assert _resolve_ffprobe_binary(str(tmp_path)) == str(tmp_path / "ffprobe.exe")

    def test_alongside_the_ffmpeg_binary(self, tmp_path):
        (tmp_path / "ffmpeg.exe").touch()
        (tmp_path / "ffprobe.exe").touch()
        with patch("core.converter._is_windows", return_value=True):
            result = _resolve_ffprobe_binary(str(tmp_path / "ffmpeg.exe"))
        assert result == str(tmp_path / "ffprobe.exe")

    def test_falls_back_to_path_when_not_beside_ffmpeg(self, tmp_path):
        with patch("core.converter._is_windows", return_value=True):
            with patch("core.converter.shutil.which", return_value="/usr/bin/ffprobe"):
                result = _resolve_ffprobe_binary(str(tmp_path / "ffmpeg.exe"))
        assert result == "/usr/bin/ffprobe"

    def test_none_uses_path(self):
        with patch("core.converter.shutil.which", return_value="/usr/bin/ffprobe"):
            assert _resolve_ffprobe_binary(None) == "/usr/bin/ffprobe"

    def test_returns_none_when_nowhere(self):
        with patch("core.converter.shutil.which", return_value=None):
            assert _resolve_ffprobe_binary(None) is None


# probe #

"""Build a mock subprocess.run result carrying `payload` as JSON stdout."""
def _probe_result(payload: dict) -> MagicMock:
    result = MagicMock()
    result.stdout = json.dumps(payload)
    result.stderr = ""
    result.returncode = 0
    return result


class TestProbe:

    def _run(self, payload_or_exc, ffprobe="/usr/bin/ffprobe"):
        with patch("core.converter._resolve_ffprobe_binary", return_value=ffprobe):
            if isinstance(payload_or_exc, Exception):
                with patch("core.converter.subprocess.run", side_effect=payload_or_exc):
                    return probe("clip.webm")
            with patch(
                "core.converter.subprocess.run",
                return_value=_probe_result(payload_or_exc),
            ):
                return probe("clip.webm")

    def test_duration_from_format_section(self):
        assert self._run({"format": {"duration": "8.008000"}}) == pytest.approx(8.008)

    def test_duration_returned_as_float(self):
        assert isinstance(self._run({"format": {"duration": "12.5"}}), float)

    def test_falls_back_to_stream_duration(self):
        payload = {"format": {}, "streams": [{"codec_type": "audio", "duration": "3.25"}]}
        assert self._run(payload) == pytest.approx(3.25)

    def test_no_ffprobe_returns_none(self):
        assert self._run({"format": {"duration": "8.0"}}, ffprobe=None) is None

    def test_missing_duration_returns_none(self):
        assert self._run({"format": {}}) is None

    def test_non_numeric_duration_returns_none(self):
        assert self._run({"format": {"duration": "N/A"}}) is None

    def test_malformed_json_returns_none(self):
        with patch("core.converter._resolve_ffprobe_binary", return_value="/usr/bin/ffprobe"):
            bad = MagicMock()
            bad.stdout = "not json at all"
            with patch("core.converter.subprocess.run", return_value=bad):
                assert probe("clip.webm") is None

    def test_empty_output_returns_none(self):
        with patch("core.converter._resolve_ffprobe_binary", return_value="/usr/bin/ffprobe"):
            empty = MagicMock()
            empty.stdout = ""
            with patch("core.converter.subprocess.run", return_value=empty):
                assert probe("clip.webm") is None

    def test_timeout_returns_none(self):
        assert self._run(subprocess.TimeoutExpired(cmd="ffprobe", timeout=15)) is None

    def test_os_error_returns_none(self):
        assert self._run(OSError("boom")) is None

    def test_decodes_output_as_utf8_with_replacement(self):
        """Non-ASCII paths must not blow up on the default locale codec."""
        with patch("core.converter._resolve_ffprobe_binary", return_value="/usr/bin/ffprobe"):
            with patch(
                "core.converter.subprocess.run",
                return_value=_probe_result({"format": {"duration": "1.0"}}),
            ) as run:
                probe("Ünïcödé — clip.webm")
        assert run.call_args.kwargs["encoding"] == "utf-8"
        assert run.call_args.kwargs["errors"] == "replace"


# plan_output_path #

class TestPlanOutputPath:

    def test_clean_name(self, tmp_path):
        result = plan_output_path("/src/clip.webm", str(tmp_path), "mp3")
        assert result == tmp_path / "clip.mp3"

    def test_extension_leading_dot_accepted(self, tmp_path):
        result = plan_output_path("/src/clip.webm", str(tmp_path), ".mp3")
        assert result == tmp_path / "clip.mp3"

    def test_illegal_characters_sanitized(self, tmp_path):
        result = plan_output_path('/src/My*Clip?Name.webm', str(tmp_path), "mp3")
        assert result == tmp_path / "MyClipName.mp3"

    def test_spaces_and_unicode_preserved(self, tmp_path):
        result = plan_output_path("/src/Ünïcödé  ñame — test.webm", str(tmp_path), "mp3")
        assert result == tmp_path / "Ünïcödé ñame — test.mp3"

    def test_existing_target_is_auto_numbered(self, tmp_path):
        (tmp_path / "clip.mp3").write_text("x")
        result = plan_output_path("/src/clip.webm", str(tmp_path), "mp3")
        assert result == tmp_path / "clip (1).mp3"

    def test_numbering_continues_past_multiple_collisions(self, tmp_path):
        (tmp_path / "clip.mp3").write_text("x")
        (tmp_path / "clip (1).mp3").write_text("x")
        result = plan_output_path("/src/clip.webm", str(tmp_path), "mp3")
        assert result == tmp_path / "clip (2).mp3"

    """
    The trap this function exists for: core.utils.find_conflicts matches on stem
    alone, so unique_path() would number this even though no .mp3 exists.
    """
    def test_same_stem_source_beside_target_is_not_a_collision(self, tmp_path):
        (tmp_path / "clip.webm").write_text("x")
        result = plan_output_path(str(tmp_path / "clip.webm"), str(tmp_path), "mp3")
        assert result == tmp_path / "clip.mp3"

    def test_other_extensions_with_same_stem_ignored(self, tmp_path):
        (tmp_path / "clip.mp4").write_text("x")
        (tmp_path / "clip.wav").write_text("x")
        result = plan_output_path("/src/clip.webm", str(tmp_path), "mp3")
        assert result == tmp_path / "clip.mp3"

    def test_overwrite_returns_plain_target(self, tmp_path):
        (tmp_path / "clip.mp3").write_text("x")
        result = plan_output_path("/src/clip.webm", str(tmp_path), "mp3", overwrite=True)
        assert result == tmp_path / "clip.mp3"

    def test_overwrite_skips_numbering_even_with_many_collisions(self, tmp_path):
        (tmp_path / "clip.mp3").write_text("x")
        (tmp_path / "clip (1).mp3").write_text("x")
        result = plan_output_path("/src/clip.webm", str(tmp_path), "mp3", overwrite=True)
        assert result == tmp_path / "clip.mp3"

    def test_empty_stem_falls_back_to_download(self, tmp_path):
        result = plan_output_path("/src/???.webm", str(tmp_path), "mp3")
        assert result == tmp_path / "download.mp3"

    def test_returns_a_path_object(self, tmp_path):
        assert isinstance(plan_output_path("/src/clip.webm", str(tmp_path), "mp3"), Path)


# ConversionResult #

class TestConversionResult:

    def test_fields_in_order(self):
        result = ConversionResult(True, False, "/out/clip.mp3", "")
        assert result.ok is True
        assert result.cancelled is False
        assert result.output_path == "/out/clip.mp3"
        assert result.message == ""

    def test_is_a_tuple(self):
        assert tuple(ConversionResult(False, True, None, "stopped")) == (
            False, True, None, "stopped",
        )


# supported_formats #

def test_supported_formats_contains_mp3():
    assert "mp3" in supported_formats()


def test_supported_formats_is_sorted():
    assert supported_formats() == sorted(supported_formats())


# _progress_seconds #

class TestProgressSeconds:

    """out_time_us is microseconds — 8 000 000 is 8 seconds, not 8 000."""
    def test_out_time_us_converted_to_seconds(self):
        assert _progress_seconds("out_time_us=8000000") == pytest.approx(8.0)

    def test_trailing_newline_tolerated(self):
        assert _progress_seconds("out_time_us=4000000\n") == pytest.approx(4.0)

    """out_time_ms is misnamed and also holds microseconds — never read it."""
    def test_out_time_ms_is_ignored(self):
        assert _progress_seconds("out_time_ms=8000000") is None

    def test_other_keys_ignored(self):
        assert _progress_seconds("speed= 307x") is None
        assert _progress_seconds("progress=continue") is None
        assert _progress_seconds("total_size=43029") is None

    def test_na_value_returns_none(self):
        assert _progress_seconds("out_time_us=N/A") is None

    def test_line_without_separator_returns_none(self):
        assert _progress_seconds("garbage") is None

    def test_blank_line_returns_none(self):
        assert _progress_seconds("\n") is None


# _last_error_line #

class TestLastErrorLine:

    def test_returns_last_line(self):
        assert _last_error_line(["first", "second"]) == "second"

    def test_empty_list_returns_empty_string(self):
        assert _last_error_line([]) == ""


# convert — fakes #

"""Readable stand-in for a Popen pipe; `on_read` fires before each readline."""
class _FakeStream:

    def __init__(self, lines, on_read=None) -> None:
        self._lines = list(lines)
        self._on_read = on_read
        self.closed = False

    def readline(self) -> str:
        if self._on_read is not None:
            self._on_read()
        return self._lines.pop(0) if self._lines else ""

    def close(self) -> None:
        self.closed = True


"""Stand-in for subprocess.Popen running ffmpeg."""
class _FakeProc:

    def __init__(
        self,
        stdout_lines,
        stderr_lines=(),
        returncode=0,
        on_read=None,
    ) -> None:
        self.stdout = _FakeStream(stdout_lines, on_read)
        self.stderr = _FakeStream(stderr_lines)
        self._returncode = returncode
        self.returncode = None
        self.terminated = False
        self.killed = False

    def wait(self, timeout=None):
        self.returncode = 1 if self.terminated else self._returncode
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True

    def kill(self) -> None:
        self.killed = True


"""One realistic -progress block, as ffmpeg 8.1.1 emits it."""
def _block(out_time_us: int, end: bool = False) -> list[str]:
    return [
        "bitrate=  43.0kbits/s\n",
        "total_size=43029\n",
        f"out_time_us={out_time_us}\n",
        f"out_time_ms={out_time_us}\n",
        "out_time=00:00:08.000000\n",
        "dup_frames=0\n",
        "drop_frames=0\n",
        "speed= 307x\n",
        f"progress={'end' if end else 'continue'}\n",
    ]


"""Drain a queue.Queue into a list."""
def _drain(q: queue.Queue) -> list[dict]:
    messages = []
    while True:
        try:
            messages.append(q.get_nowait())
        except queue.Empty:
            return messages


# convert #

class TestConvert:

    def _run(
        self,
        tmp_path,
        *,
        stdout_lines=None,
        stderr_lines=(),
        returncode=0,
        duration=8.0,
        cancel_event=None,
        on_read=None,
        target_format="mp3",
        capture=None,
    ):
        source = tmp_path / "clip.webm"
        source.write_text("fake media")
        output = tmp_path / "clip.mp3"

        proc = _FakeProc(
            stdout_lines if stdout_lines is not None else _block(8_000_000, end=True),
            stderr_lines,
            returncode,
            on_read,
        )
        if capture is not None:
            capture["proc"] = proc

        q: queue.Queue = queue.Queue()

        def _fake_popen(cmd, **kwargs):
            if capture is not None:
                capture["cmd"] = cmd
                capture["kwargs"] = kwargs
            return proc

        with patch("core.converter.resolve_ffmpeg_binary", return_value="/usr/bin/ffmpeg"):
            with patch("core.converter.probe", return_value=duration):
                with patch("core.converter.subprocess.Popen", side_effect=_fake_popen):
                    result = convert(
                        str(source), str(output), target_format, q,
                        cancel_event=cancel_event,
                    )

        return result, _drain(q), output

    # argv #

    def test_selects_mp3_encoder(self, tmp_path):
        cap: dict = {}
        self._run(tmp_path, capture=cap)
        assert "libmp3lame" in cap["cmd"]

    def test_discards_video_stream(self, tmp_path):
        cap: dict = {}
        self._run(tmp_path, capture=cap)
        assert "-vn" in cap["cmd"]

    def test_requests_progress_on_stdout(self, tmp_path):
        cap: dict = {}
        self._run(tmp_path, capture=cap)
        assert cap["cmd"][cap["cmd"].index("-progress") + 1] == "pipe:1"

    def test_passes_nostdin(self, tmp_path):
        cap: dict = {}
        self._run(tmp_path, capture=cap)
        assert "-nostdin" in cap["cmd"]

    def test_input_and_output_are_separate_argv_elements(self, tmp_path):
        cap: dict = {}
        _, _, output = self._run(tmp_path, capture=cap)
        assert cap["cmd"][cap["cmd"].index("-i") + 1] == str(tmp_path / "clip.webm")
        assert cap["cmd"][-1] == str(output)

    def test_decodes_output_as_utf8_with_replacement(self, tmp_path):
        cap: dict = {}
        self._run(tmp_path, capture=cap)
        assert cap["kwargs"]["encoding"] == "utf-8"
        assert cap["kwargs"]["errors"] == "replace"

    # progress #

    """
    Percent must come from out_time_us. Reading out_time_ms as milliseconds
    would make this 4 000 s of a 8 s source and clamp to 100, not 50.
    """
    def test_percent_derived_from_out_time_us(self, tmp_path):
        _, msgs, _ = self._run(
            tmp_path, stdout_lines=_block(4_000_000) + _block(8_000_000, end=True),
            duration=8.0,
        )
        converting = [m for m in msgs if m["status"] == "converting"]
        assert converting[0]["percent"] == pytest.approx(50.0)

    def test_percent_reaches_100_on_success(self, tmp_path):
        _, msgs, _ = self._run(tmp_path, duration=8.0)
        converting = [m for m in msgs if m["status"] == "converting"]
        assert converting[-1]["percent"] == pytest.approx(100.0)

    def test_percent_clamped_to_100(self, tmp_path):
        _, msgs, _ = self._run(
            tmp_path, stdout_lines=_block(99_000_000, end=True), duration=8.0,
        )
        converting = [m for m in msgs if m["status"] == "converting"]
        assert all(m["percent"] <= 100.0 for m in converting)

    def test_progress_messages_carry_filename(self, tmp_path):
        _, msgs, _ = self._run(tmp_path)
        converting = [m for m in msgs if m["status"] == "converting"]
        assert converting and all(m["filename"] == "clip.mp3" for m in converting)

    """A fast conversion emits only ffmpeg's final block — still a valid run."""
    def test_single_end_block_still_completes(self, tmp_path):
        result, msgs, _ = self._run(
            tmp_path, stdout_lines=_block(8_000_000, end=True), duration=8.0,
        )
        assert result.ok is True
        assert msgs[-1] == {"status": "done", "filename": "clip.mp3"}

    def test_no_progress_output_at_all_still_completes(self, tmp_path):
        result, msgs, _ = self._run(tmp_path, stdout_lines=[])
        assert result.ok is True
        assert msgs[-1]["status"] == "done"

    """Never fabricate a percentage when the source duration is unknown."""
    def test_unknown_duration_yields_none_percent(self, tmp_path):
        _, msgs, _ = self._run(tmp_path, duration=None)
        converting = [m for m in msgs if m["status"] == "converting"]
        assert converting
        assert all(m["percent"] is None for m in converting)

    def test_zero_duration_yields_none_percent(self, tmp_path):
        _, msgs, _ = self._run(tmp_path, duration=0.0)
        converting = [m for m in msgs if m["status"] == "converting"]
        assert all(m["percent"] is None for m in converting)

    # success #

    def test_success_returns_ok_result(self, tmp_path):
        result, _, output = self._run(tmp_path)
        assert result.ok is True
        assert result.cancelled is False
        assert result.output_path == str(output)

    def test_success_emits_done_last(self, tmp_path):
        _, msgs, _ = self._run(tmp_path)
        assert msgs[-1]["status"] == "done"

    # failure #

    def test_nonzero_exit_reports_error(self, tmp_path):
        result, msgs, _ = self._run(
            tmp_path, returncode=1,
            stderr_lines=["Error opening input files: Invalid data found when processing input\n"],
        )
        assert result.ok is False
        assert msgs[-1]["status"] == "error"

    def test_error_message_surfaces_ffmpeg_stderr(self, tmp_path):
        _, msgs, _ = self._run(
            tmp_path, returncode=1,
            stderr_lines=["EBML header parsing failed\n",
                          "Error opening input files: Invalid data found when processing input\n"],
        )
        assert "Invalid data found" in msgs[-1]["message"]

    """ffmpeg returns raw AVERROR values — a corrupt input exits 3199971767."""
    def test_large_averror_exit_code_is_a_failure(self, tmp_path):
        result, msgs, _ = self._run(
            tmp_path, returncode=3199971767,
            stderr_lines=["Error opening input files: Invalid data found when processing input\n"],
        )
        assert result.ok is False
        assert msgs[-1]["status"] == "error"

    def test_silent_failure_falls_back_to_exit_code_message(self, tmp_path):
        _, msgs, _ = self._run(tmp_path, returncode=69, stderr_lines=[])
        assert "69" in msgs[-1]["message"]

    def test_failure_removes_partial_output(self, tmp_path):
        source = tmp_path / "clip.webm"
        source.write_text("fake media")
        output = tmp_path / "clip.mp3"
        output.write_text("half an mp3")

        proc = _FakeProc([], ["boom\n"], returncode=1)
        q: queue.Queue = queue.Queue()
        with patch("core.converter.resolve_ffmpeg_binary", return_value="/usr/bin/ffmpeg"):
            with patch("core.converter.probe", return_value=8.0):
                with patch("core.converter.subprocess.Popen", return_value=proc):
                    convert(str(source), str(output), "mp3", q)

        assert not output.exists()

    def test_missing_ffmpeg_reports_error_without_raising(self, tmp_path):
        source = tmp_path / "clip.webm"
        source.write_text("fake media")
        q: queue.Queue = queue.Queue()
        with patch(
            "core.converter.resolve_ffmpeg_binary",
            side_effect=RuntimeError("ffmpeg is not installed or not on PATH."),
        ):
            result = convert(str(source), str(tmp_path / "clip.mp3"), "mp3", q)

        assert result.ok is False
        assert "ffmpeg" in q.get_nowait()["message"]

    def test_unsupported_format_reports_error(self, tmp_path):
        result, msgs, _ = self._run(tmp_path, target_format="flac")
        assert result.ok is False
        assert "flac" in msgs[-1]["message"]

    def test_missing_input_reports_error(self, tmp_path):
        q: queue.Queue = queue.Queue()
        with patch("core.converter.resolve_ffmpeg_binary", return_value="/usr/bin/ffmpeg"):
            result = convert(
                str(tmp_path / "nope.webm"), str(tmp_path / "nope.mp3"), "mp3", q,
            )
        assert result.ok is False
        assert "not found" in q.get_nowait()["message"]

    # cancellation #

    def test_cancel_before_start_never_launches_ffmpeg(self, tmp_path):
        source = tmp_path / "clip.webm"
        source.write_text("fake media")
        event = threading.Event()
        event.set()
        q: queue.Queue = queue.Queue()

        with patch("core.converter.subprocess.Popen") as popen:
            result = convert(
                str(source), str(tmp_path / "clip.mp3"), "mp3", q, cancel_event=event,
            )

        popen.assert_not_called()
        assert result.cancelled is True
        assert q.get_nowait() == {"status": "cancelled", "filename": "clip.mp3"}

    def test_cancel_mid_conversion_emits_cancelled(self, tmp_path):
        event = threading.Event()
        result, msgs, _ = self._run(
            tmp_path,
            stdout_lines=_block(1_000_000) * 8,
            cancel_event=event,
            on_read=event.set,
        )
        assert result.cancelled is True
        assert result.ok is False
        assert msgs[-1] == {"status": "cancelled", "filename": "clip.mp3"}

    def test_cancel_terminates_the_process(self, tmp_path):
        event = threading.Event()
        cap: dict = {}
        self._run(
            tmp_path, stdout_lines=_block(1_000_000) * 8,
            cancel_event=event, on_read=event.set, capture=cap,
        )
        assert cap["proc"].terminated is True

    """ffmpeg leaves a partial file behind on terminate — convert() clears it."""
    def test_cancel_removes_partial_output(self, tmp_path):
        source = tmp_path / "clip.webm"
        source.write_text("fake media")
        output = tmp_path / "clip.mp3"
        event = threading.Event()

        def _start_writing():
            output.write_text("partial mp3 bytes")
            event.set()

        proc = _FakeProc(_block(1_000_000) * 8, on_read=_start_writing)
        q: queue.Queue = queue.Queue()
        with patch("core.converter.resolve_ffmpeg_binary", return_value="/usr/bin/ffmpeg"):
            with patch("core.converter.probe", return_value=8.0):
                with patch("core.converter.subprocess.Popen", return_value=proc):
                    result = convert(
                        str(source), str(output), "mp3", q, cancel_event=event,
                    )

        assert result.cancelled is True
        assert not output.exists()

    def test_cancel_emits_no_done_message(self, tmp_path):
        event = threading.Event()
        _, msgs, _ = self._run(
            tmp_path, stdout_lines=_block(1_000_000) * 8,
            cancel_event=event, on_read=event.set,
        )
        assert not any(m["status"] == "done" for m in msgs)

    def test_unset_cancel_event_runs_to_completion(self, tmp_path):
        result, _, _ = self._run(tmp_path, cancel_event=threading.Event())
        assert result.ok is True
