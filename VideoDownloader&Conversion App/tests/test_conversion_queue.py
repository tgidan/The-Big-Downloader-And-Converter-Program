"""Tests for core/conversion_queue.py"""

from __future__ import annotations

import queue
import sys
import threading
import time
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.conversion_queue import ConversionJob, ConversionQueue, _ProgressChannel
from core.converter import ConversionResult


# ── Helpers ───────────────────────────────────────────────────────────────────

def _drain(q: queue.Queue, timeout: float = 2.0) -> list[dict]:
    """Collect all messages from update_queue, waiting up to `timeout` seconds."""
    messages = []
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            messages.append(q.get(timeout=0.05))
        except queue.Empty:
            break
    return messages


def _wait_for_status(cq: ConversionQueue, job_id: str, status: str, timeout: float = 2.0) -> bool:
    """Poll update_queue until a status_change/finished arrives for job_id."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            msg = cq.update_queue.get(timeout=0.05)
        except queue.Empty:
            continue
        if msg.get("job_id") == job_id and msg.get("status") == status:
            return True
        if msg.get("job_id") == job_id and msg.get("type") == "finished" and status == "done":
            return True
    return False


def _wait_for_job_status(cq: ConversionQueue, status: str, timeout: float = 2.0) -> bool:
    """Poll the job list itself until the first job reaches `status`."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cq.jobs and cq.jobs[0].status == status:
            return True
        time.sleep(0.02)
    return False


def _make_instant_convert(messages: list[dict], result: ConversionResult | None = None):
    """
    Returns a callable mimicking converter.convert(): pushes `messages` onto
    the progress channel and returns a ConversionResult.
    """
    def _fake_convert(input_path, output_path, target_format, progress_queue, **kwargs):
        for m in messages:
            progress_queue.put(m)
        return result or ConversionResult(True, False, output_path, "")
    return _fake_convert


_DONE = [{"status": "done", "filename": "clip.mp3"}]


# ── _ProgressChannel ──────────────────────────────────────────────────────────

class TestProgressChannel:

    def test_put_forwards_to_handler(self):
        seen = []
        _ProgressChannel(seen.append).put({"status": "done"})
        assert seen == [{"status": "done"}]

    def test_accepts_queue_put_signature(self):
        seen = []
        _ProgressChannel(seen.append).put({"status": "done"}, True, 1.0)
        assert len(seen) == 1


# ── ConversionJob dataclass ───────────────────────────────────────────────────

class TestConversionJob:

    def test_default_status_is_pending(self):
        job = ConversionJob(id="x", input_path="a.webm", output_path="a.mp3", target_format="mp3")
        assert job.status == "pending"

    def test_default_progress_is_zero(self):
        job = ConversionJob(id="x", input_path="a.webm", output_path="a.mp3", target_format="mp3")
        assert job.progress == 0.0

    def test_fields_set_correctly(self):
        job = ConversionJob(
            id="abc", input_path="/in/clip.webm",
            output_path="/out/clip.mp3", target_format="mp3",
        )
        assert job.id == "abc"
        assert job.input_path == "/in/clip.webm"
        assert job.output_path == "/out/clip.mp3"
        assert job.target_format == "mp3"


# ── ConversionQueue.add ───────────────────────────────────────────────────────

class TestAdd:

    def test_add_returns_string_id(self):
        cq = ConversionQueue()
        job_id = cq.add("a.webm", "a.mp3", "mp3")
        assert isinstance(job_id, str) and job_id

    def test_add_creates_pending_job(self):
        cq = ConversionQueue()
        job_id = cq.add("a.webm", "a.mp3", "mp3")
        assert cq.jobs[0].status == "pending"
        assert cq.jobs[0].id == job_id

    def test_add_emits_job_added_message(self):
        cq = ConversionQueue()
        job_id = cq.add("a.webm", "a.mp3", "mp3")
        assert cq.update_queue.get_nowait() == {"type": "job_added", "job_id": job_id}

    def test_add_multiple_preserves_order(self):
        cq = ConversionQueue()
        ids = [cq.add(f"{i}.webm", f"{i}.mp3", "mp3") for i in range(3)]
        assert [j.id for j in cq.jobs] == ids

    def test_add_stores_paths_and_format(self):
        cq = ConversionQueue()
        cq.add("/in/clip.webm", "/out/clip.mp3", "mp3")
        job = cq.jobs[0]
        assert job.input_path == "/in/clip.webm"
        assert job.output_path == "/out/clip.mp3"
        assert job.target_format == "mp3"

    def test_ids_are_unique(self):
        cq = ConversionQueue()
        ids = [cq.add("a.webm", "a.mp3", "mp3") for _ in range(5)]
        assert len(set(ids)) == 5


# ── ConversionQueue.start_next ────────────────────────────────────────────────

class TestStartNext:

    def test_returns_false_when_no_pending_jobs(self):
        cq = ConversionQueue()
        assert cq.start_next() is False

    def test_returns_true_when_job_started(self):
        cq = ConversionQueue()
        cq.add("a.webm", "a.mp3", "mp3")
        with patch("core.conversion_queue.converter.convert",
                   side_effect=_make_instant_convert(_DONE)):
            assert cq.start_next() is True

    def test_job_transitions_to_converting(self):
        cq = ConversionQueue()
        cq.add("a.webm", "a.mp3", "mp3")
        started = threading.Event()

        def _slow(*a, **kw):
            started.set()
            time.sleep(0.5)
            return ConversionResult(True, False, "a.mp3", "")

        with patch("core.conversion_queue.converter.convert", side_effect=_slow):
            cq.start_next()
            started.wait(timeout=1.0)
            assert cq.jobs[0].status == "converting"

    def test_emits_status_change_to_converting(self):
        cq = ConversionQueue()
        job_id = cq.add("a.webm", "a.mp3", "mp3")
        cq.update_queue.get_nowait()  # consume job_added
        with patch("core.conversion_queue.converter.convert",
                   side_effect=_make_instant_convert(_DONE)):
            cq.start_next()
        msgs = _drain(cq.update_queue)
        assert {"type": "status_change", "job_id": job_id, "status": "converting"} in msgs

    def test_does_not_exceed_max_concurrent(self):
        cq = ConversionQueue(max_concurrent=1)
        cq.add("a.webm", "a.mp3", "mp3")
        cq.add("b.webm", "b.mp3", "mp3")
        started = threading.Event()

        def _slow(*a, **kw):
            started.set()
            time.sleep(1.0)
            return ConversionResult(True, False, "a.mp3", "")

        with patch("core.conversion_queue.converter.convert", side_effect=_slow):
            assert cq.start_next() is True
            started.wait(timeout=1.0)
            assert cq.start_next() is False

    """Jobs run in the order they were added, one at a time."""
    def test_runs_jobs_in_order_one_at_a_time(self):
        cq = ConversionQueue(max_concurrent=1)
        for name in ("a", "b", "c"):
            cq.add(f"{name}.webm", f"{name}.mp3", "mp3")

        calls: list[str] = []
        overlap = []
        running = threading.Lock()

        def _record(input_path, *a, **kw):
            if not running.acquire(blocking=False):
                overlap.append(input_path)
            else:
                calls.append(input_path)
                time.sleep(0.05)
                running.release()
            return ConversionResult(True, False, "x.mp3", "")

        with patch("core.conversion_queue.converter.convert", side_effect=_record):
            for _ in range(3):
                cq.start_next()
                time.sleep(0.15)

        assert calls == ["a.webm", "b.webm", "c.webm"]
        assert overlap == []

    def test_skips_non_pending_jobs(self):
        cq = ConversionQueue()
        cq.add("a.webm", "a.mp3", "mp3")
        cq.add("b.webm", "b.mp3", "mp3")
        cq.cancel(cq.jobs[0].id)

        calls = []

        def _capture(input_path, *a, **kw):
            calls.append(input_path)
            return ConversionResult(True, False, "b.mp3", "")

        with patch("core.conversion_queue.converter.convert", side_effect=_capture):
            cq.start_next()
            time.sleep(0.15)

        assert calls == ["b.webm"]


# ── Arguments handed to converter.convert ─────────────────────────────────────

class TestConvertArguments:

    def _capture_kwargs(self, cq: ConversionQueue) -> dict:
        captured: dict = {}

        def _capture(input_path, output_path, target_format, progress_queue, **kw):
            captured["args"] = (input_path, output_path, target_format)
            captured.update(kw)
            return ConversionResult(True, False, output_path, "")

        with patch("core.conversion_queue.converter.convert", side_effect=_capture):
            cq.start_next()
            time.sleep(0.2)
        return captured

    def test_positional_arguments_passed_through(self):
        cq = ConversionQueue()
        cq.add("/in/clip.webm", "/out/clip.mp3", "mp3")
        captured = self._capture_kwargs(cq)
        assert captured["args"] == ("/in/clip.webm", "/out/clip.mp3", "mp3")

    """ffmpeg_path is read from config per job, so Settings changes take effect."""
    def test_ffmpeg_path_read_from_config(self):
        cq = ConversionQueue()
        cq.add("a.webm", "a.mp3", "mp3")
        with patch("core.conversion_queue.config_manager.get", return_value="C:/ff/ffmpeg.exe"):
            captured = self._capture_kwargs(cq)
        assert captured["ffmpeg_path"] == "C:/ff/ffmpeg.exe"

    def test_none_ffmpeg_path_passed_through(self):
        cq = ConversionQueue()
        cq.add("a.webm", "a.mp3", "mp3")
        with patch("core.conversion_queue.config_manager.get", return_value=None):
            captured = self._capture_kwargs(cq)
        assert captured["ffmpeg_path"] is None

    """The cancel event goes straight to convert(); no nested polling thread."""
    def test_cancel_event_handed_to_convert(self):
        cq = ConversionQueue()
        cq.add("a.webm", "a.mp3", "mp3")
        captured = self._capture_kwargs(cq)
        assert isinstance(captured["cancel_event"], threading.Event)


# ── Progress forwarding ───────────────────────────────────────────────────────

class TestProgressForwarding:

    def _run_with_messages(
        self, messages: list[dict], result: ConversionResult | None = None,
    ) -> tuple[ConversionQueue, str]:
        cq = ConversionQueue()
        job_id = cq.add("clip.webm", "clip.mp3", "mp3")
        with patch("core.conversion_queue.converter.convert",
                   side_effect=_make_instant_convert(messages, result)):
            cq.start_next()
        return cq, job_id

    def test_converting_forwarded_as_progress_type(self):
        cq, job_id = self._run_with_messages([
            {"status": "converting", "percent": 50.0, "filename": "clip.mp3"},
            {"status": "done", "filename": "clip.mp3"},
        ])
        msgs = _drain(cq.update_queue)
        progress = [m for m in msgs if m.get("type") == "progress"]
        assert progress[0]["percent"] == 50.0
        assert progress[0]["job_id"] == job_id
        assert progress[0]["filename"] == "clip.mp3"

    def test_progress_updates_job_progress(self):
        cq, _ = self._run_with_messages([
            {"status": "converting", "percent": 40.0, "filename": "clip.mp3"},
        ])
        time.sleep(0.2)
        assert cq.jobs[0].progress == 40.0

    """A None percent must be forwarded as-is, never coerced to a number."""
    def test_none_percent_forwarded_unchanged(self):
        cq, _ = self._run_with_messages([
            {"status": "converting", "percent": None, "filename": "clip.mp3"},
            {"status": "done", "filename": "clip.mp3"},
        ])
        msgs = _drain(cq.update_queue)
        progress = [m for m in msgs if m.get("type") == "progress"]
        assert progress[0]["percent"] is None

    def test_none_percent_leaves_job_progress_untouched(self):
        cq, _ = self._run_with_messages([
            {"status": "converting", "percent": None, "filename": "clip.mp3"},
        ])
        time.sleep(0.2)
        assert cq.jobs[0].progress == 0.0

    def test_done_sets_progress_to_100(self):
        cq, _ = self._run_with_messages(_DONE)
        time.sleep(0.2)
        assert cq.jobs[0].progress == 100.0

    def test_done_emits_status_change_done(self):
        cq, job_id = self._run_with_messages(_DONE)
        assert _wait_for_status(cq, job_id, "done")

    def test_done_emits_finished_message(self):
        cq, job_id = self._run_with_messages(_DONE)
        msgs = _drain(cq.update_queue)
        finished = [m for m in msgs if m.get("type") == "finished"]
        assert any(m["job_id"] == job_id and m["filename"] == "clip.mp3" for m in finished)

    def test_error_sets_job_status_to_error(self):
        cq, _ = self._run_with_messages(
            [{"status": "error", "message": "ffmpeg blew up"}],
            ConversionResult(False, False, None, "ffmpeg blew up"),
        )
        time.sleep(0.2)
        assert cq.jobs[0].status == "error"

    def test_error_emits_error_message(self):
        cq, job_id = self._run_with_messages(
            [{"status": "error", "message": "Invalid data found"}],
            ConversionResult(False, False, None, "Invalid data found"),
        )
        msgs = _drain(cq.update_queue)
        errors = [m for m in msgs if m.get("type") == "error"]
        assert any(m["job_id"] == job_id and "Invalid data" in m["message"] for m in errors)

    def test_cancelled_message_sets_cancelled_status(self):
        cq, _ = self._run_with_messages(
            [{"status": "cancelled", "filename": "clip.mp3"}],
            ConversionResult(False, True, None, "Cancelled"),
        )
        time.sleep(0.2)
        assert cq.jobs[0].status == "cancelled"

    """convert() always emits a terminal message, but a silent return
    must still resolve the job rather than leave it reading as converting."""
    def test_silent_return_reconciled_from_result(self):
        cq, _ = self._run_with_messages([], ConversionResult(True, False, "clip.mp3", ""))
        time.sleep(0.2)
        assert cq.jobs[0].status == "done"

    def test_silent_failure_reconciled_from_result(self):
        cq, _ = self._run_with_messages([], ConversionResult(False, False, None, "nope"))
        time.sleep(0.2)
        assert cq.jobs[0].status == "error"


# ── Slot management: a bad job must not wedge the queue ───────────────────────

class TestSlotRelease:

    def test_active_count_zero_at_start(self):
        cq = ConversionQueue()
        with cq._lock:
            assert cq._active_count == 0

    def test_active_count_increments_when_job_starts(self):
        cq = ConversionQueue()
        cq.add("a.webm", "a.mp3", "mp3")
        started = threading.Event()

        def _slow(*a, **kw):
            started.set()
            time.sleep(1.0)
            return ConversionResult(True, False, "a.mp3", "")

        with patch("core.conversion_queue.converter.convert", side_effect=_slow):
            cq.start_next()
            started.wait(timeout=1.0)
            with cq._lock:
                assert cq._active_count == 1

    def test_done_job_frees_the_slot(self):
        cq = ConversionQueue()
        job_id = cq.add("a.webm", "a.mp3", "mp3")
        with patch("core.conversion_queue.converter.convert",
                   side_effect=_make_instant_convert(_DONE)):
            cq.start_next()
        _wait_for_status(cq, job_id, "done")
        with cq._lock:
            assert cq._active_count == 0

    """An errored job must free its slot, or one bad file stalls everything."""
    def test_errored_job_frees_the_slot_and_next_job_runs(self):
        cq = ConversionQueue()
        cq.add("bad.webm", "bad.mp3", "mp3")
        cq.add("good.webm", "good.mp3", "mp3")

        calls: list[str] = []

        def _fake(input_path, output_path, target_format, progress_queue, **kw):
            calls.append(input_path)
            if input_path == "bad.webm":
                progress_queue.put({"status": "error", "message": "Invalid data found"})
                return ConversionResult(False, False, None, "Invalid data found")
            progress_queue.put({"status": "done", "filename": output_path})
            return ConversionResult(True, False, output_path, "")

        with patch("core.conversion_queue.converter.convert", side_effect=_fake):
            cq.start_next()
            deadline = time.monotonic() + 3.0
            while time.monotonic() < deadline and len(calls) < 2:
                time.sleep(0.02)
            time.sleep(0.1)   # let the second worker unwind

        assert calls == ["bad.webm", "good.webm"]
        assert cq.jobs[0].status == "error"
        assert cq.jobs[1].status == "done"
        with cq._lock:
            assert cq._active_count == 0

    """A crash inside convert() must not strand the slot either."""
    def test_exception_in_convert_frees_the_slot(self):
        cq = ConversionQueue()
        cq.add("a.webm", "a.mp3", "mp3")
        with patch("core.conversion_queue.converter.convert",
                   side_effect=RuntimeError("boom")):
            cq.start_next()
            time.sleep(0.25)

        with cq._lock:
            assert cq._active_count == 0
        assert cq.jobs[0].status == "error"

    def test_exception_in_convert_emits_error_message(self):
        cq = ConversionQueue()
        job_id = cq.add("a.webm", "a.mp3", "mp3")
        with patch("core.conversion_queue.converter.convert",
                   side_effect=RuntimeError("boom")):
            cq.start_next()
        msgs = _drain(cq.update_queue)
        errors = [m for m in msgs if m.get("type") == "error"]
        assert any(m["job_id"] == job_id and "boom" in m["message"] for m in errors)

    def test_two_slots_allows_two_concurrent(self):
        cq = ConversionQueue(max_concurrent=2)
        cq.add("a.webm", "a.mp3", "mp3")
        cq.add("b.webm", "b.mp3", "mp3")
        started = threading.Event()

        def _slow(*a, **kw):
            started.set()
            time.sleep(1.0)
            return ConversionResult(True, False, "a.mp3", "")

        with patch("core.conversion_queue.converter.convert", side_effect=_slow):
            assert cq.start_next() is True
            assert cq.start_next() is True
            assert cq.start_next() is False


# ── Auto-advance: the queue drains itself ─────────────────────────────────────

class TestAutoAdvance:

    """
    One start_next() must drain the whole queue. Regression test for a stall
    found in a real run: the terminal message reaches update_queue before the
    worker releases its slot, so a consumer calling start_next() the instant it
    reads "finished" got False and the remaining jobs were stranded forever.
    """
    def test_single_start_next_drains_the_whole_queue(self):
        cq = ConversionQueue(max_concurrent=1)
        for name in ("a", "b", "c"):
            cq.add(f"{name}.webm", f"{name}.mp3", "mp3")

        calls: list[str] = []

        def _fake(input_path, output_path, target_format, progress_queue, **kw):
            calls.append(input_path)
            progress_queue.put({"status": "done", "filename": output_path})
            return ConversionResult(True, False, output_path, "")

        with patch("core.conversion_queue.converter.convert", side_effect=_fake):
            cq.start_next()   # primed once, and only once
            deadline = time.monotonic() + 3.0
            while time.monotonic() < deadline and len(calls) < 3:
                time.sleep(0.02)

        assert calls == ["a.webm", "b.webm", "c.webm"]
        assert [j.status for j in cq.jobs] == ["done", "done", "done"]

    """The stall reproduced exactly: react to the terminal message immediately."""
    def test_consumer_reacting_instantly_to_finished_does_not_stall(self):
        cq = ConversionQueue(max_concurrent=1)
        for name in ("a", "b"):
            cq.add(f"{name}.webm", f"{name}.mp3", "mp3")

        calls: list[str] = []

        def _fake(input_path, output_path, target_format, progress_queue, **kw):
            calls.append(input_path)
            progress_queue.put({"status": "done", "filename": output_path})
            return ConversionResult(True, False, output_path, "")

        with patch("core.conversion_queue.converter.convert", side_effect=_fake):
            cq.start_next()
            deadline = time.monotonic() + 3.0
            seen = 0
            while time.monotonic() < deadline and seen < 2:
                try:
                    msg = cq.update_queue.get(timeout=0.05)
                except queue.Empty:
                    continue
                if msg.get("type") == "finished":
                    seen += 1
                    cq.start_next()   # the racing call that used to lose

        assert calls == ["a.webm", "b.webm"]

    """An errored job must advance the queue too, not just a successful one."""
    def test_errored_job_auto_advances(self):
        cq = ConversionQueue(max_concurrent=1)
        cq.add("bad.webm", "bad.mp3", "mp3")
        cq.add("good.webm", "good.mp3", "mp3")

        calls: list[str] = []

        def _fake(input_path, output_path, target_format, progress_queue, **kw):
            calls.append(input_path)
            if input_path == "bad.webm":
                progress_queue.put({"status": "error", "message": "Invalid data found"})
                return ConversionResult(False, False, None, "Invalid data found")
            progress_queue.put({"status": "done", "filename": output_path})
            return ConversionResult(True, False, output_path, "")

        with patch("core.conversion_queue.converter.convert", side_effect=_fake):
            cq.start_next()
            deadline = time.monotonic() + 3.0
            while time.monotonic() < deadline and len(calls) < 2:
                time.sleep(0.02)

        assert calls == ["bad.webm", "good.webm"]

    """A cancelled job must not stop the rest of the queue either."""
    def test_cancelled_job_auto_advances(self):
        cq = ConversionQueue(max_concurrent=1)
        cq.add("a.webm", "a.mp3", "mp3")
        cq.add("b.webm", "b.mp3", "mp3")

        calls: list[str] = []
        running = threading.Event()

        def _fake(input_path, output_path, target_format, progress_queue, **kw):
            calls.append(input_path)
            if input_path == "a.webm":
                running.set()
                kw["cancel_event"].wait(timeout=2.0)
                progress_queue.put({"status": "cancelled", "filename": output_path})
                return ConversionResult(False, True, None, "Cancelled")
            progress_queue.put({"status": "done", "filename": output_path})
            return ConversionResult(True, False, output_path, "")

        with patch("core.conversion_queue.converter.convert", side_effect=_fake):
            cq.start_next()
            running.wait(timeout=1.0)
            cq.cancel(cq.jobs[0].id)
            deadline = time.monotonic() + 3.0
            while time.monotonic() < deadline and len(calls) < 2:
                time.sleep(0.02)

        assert calls == ["a.webm", "b.webm"]
        assert cq.jobs[0].status == "cancelled"

    """Auto-advance must stop at the concurrency limit, not run everything."""
    def test_auto_advance_respects_max_concurrent(self):
        cq = ConversionQueue(max_concurrent=1)
        for name in ("a", "b", "c"):
            cq.add(f"{name}.webm", f"{name}.mp3", "mp3")

        concurrent = []
        active = threading.Semaphore(1)

        def _fake(input_path, output_path, target_format, progress_queue, **kw):
            if not active.acquire(blocking=False):
                concurrent.append(input_path)
            else:
                time.sleep(0.05)
                active.release()
            progress_queue.put({"status": "done", "filename": output_path})
            return ConversionResult(True, False, output_path, "")

        with patch("core.conversion_queue.converter.convert", side_effect=_fake):
            cq.start_next()
            time.sleep(0.5)

        assert concurrent == []

    def test_auto_advance_stops_when_queue_is_empty(self):
        cq = ConversionQueue(max_concurrent=1)
        cq.add("a.webm", "a.mp3", "mp3")

        with patch("core.conversion_queue.converter.convert",
                   side_effect=_make_instant_convert(_DONE)):
            cq.start_next()
            time.sleep(0.3)

        with cq._lock:
            assert cq._active_count == 0
        assert cq.start_next() is False


class TestConcurrencyLimit:

    def test_two_slots_allows_two_concurrent(self):
        cq = ConversionQueue(max_concurrent=2)
        cq.add("a.webm", "a.mp3", "mp3")
        cq.add("b.webm", "b.mp3", "mp3")
        started = threading.Event()

        def _slow(*a, **kw):
            started.set()
            time.sleep(1.0)
            return ConversionResult(True, False, "a.mp3", "")

        with patch("core.conversion_queue.converter.convert", side_effect=_slow):
            assert cq.start_next() is True
            assert cq.start_next() is True
            assert cq.start_next() is False


# ── ConversionQueue.cancel ────────────────────────────────────────────────────

class TestCancel:

    def test_cancel_pending_job_sets_cancelled_immediately(self):
        cq = ConversionQueue()
        job_id = cq.add("a.webm", "a.mp3", "mp3")
        cq.cancel(job_id)
        assert cq.jobs[0].status == "cancelled"

    def test_cancel_pending_emits_status_change(self):
        cq = ConversionQueue()
        job_id = cq.add("a.webm", "a.mp3", "mp3")
        cq.update_queue.get_nowait()  # consume job_added
        cq.cancel(job_id)
        assert cq.update_queue.get_nowait() == {
            "type": "status_change", "job_id": job_id, "status": "cancelled",
        }

    """A pending cancel must never reach ffmpeg."""
    def test_cancel_pending_never_starts_convert(self):
        cq = ConversionQueue()
        job_id = cq.add("a.webm", "a.mp3", "mp3")
        cq.cancel(job_id)
        with patch("core.conversion_queue.converter.convert") as convert:
            assert cq.start_next() is False
        convert.assert_not_called()

    def test_cancel_nonexistent_id_does_nothing(self):
        ConversionQueue().cancel("no-such-id")  # must not raise

    def test_cancel_active_job_sets_the_event(self):
        cq = ConversionQueue()
        job_id = cq.add("a.webm", "a.mp3", "mp3")
        running = threading.Event()
        seen: dict = {}

        def _long(input_path, output_path, target_format, progress_queue, **kw):
            seen["event"] = kw["cancel_event"]
            running.set()
            kw["cancel_event"].wait(timeout=2.0)
            progress_queue.put({"status": "cancelled", "filename": output_path})
            return ConversionResult(False, True, None, "Cancelled")

        with patch("core.conversion_queue.converter.convert", side_effect=_long):
            cq.start_next()
            running.wait(timeout=1.0)
            cq.cancel(job_id)
            assert seen["event"].is_set() is True

        assert _wait_for_job_status(cq, "cancelled")

    def test_cancel_active_job_frees_the_slot(self):
        cq = ConversionQueue()
        job_id = cq.add("a.webm", "a.mp3", "mp3")
        running = threading.Event()

        def _long(input_path, output_path, target_format, progress_queue, **kw):
            running.set()
            kw["cancel_event"].wait(timeout=2.0)
            progress_queue.put({"status": "cancelled", "filename": output_path})
            return ConversionResult(False, True, None, "Cancelled")

        with patch("core.conversion_queue.converter.convert", side_effect=_long):
            cq.start_next()
            running.wait(timeout=1.0)
            cq.cancel(job_id)
            time.sleep(0.3)

        with cq._lock:
            assert cq._active_count == 0


# ── jobs property ─────────────────────────────────────────────────────────────

class TestJobsProperty:

    def test_returns_list_copy(self):
        cq = ConversionQueue()
        cq.add("a.webm", "a.mp3", "mp3")
        snapshot = cq.jobs
        snapshot.clear()
        assert len(cq.jobs) == 1

    def test_empty_when_no_jobs_added(self):
        assert ConversionQueue().jobs == []
