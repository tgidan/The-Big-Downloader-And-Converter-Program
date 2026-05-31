"""Tests for core/queue_manager.py"""

from __future__ import annotations

import queue
import sys
import threading
import time
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.queue_manager import DownloadJob, QueueManager


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


def _wait_for_status(qm: QueueManager, job_id: str, status: str, timeout: float = 2.0) -> bool:
    """Poll update_queue until a status_change/finished/error arrives for job_id."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            msg = qm.update_queue.get(timeout=0.05)
        except queue.Empty:
            continue
        if msg.get("job_id") == job_id and msg.get("status") == status:
            return True
        if msg.get("job_id") == job_id and msg.get("type") == "finished" and status == "done":
            return True
    return False


def _make_instant_download(messages: list[dict]):
    """
    Returns a callable that mimics downloader.download():
    pushes `messages` onto the progress_queue and returns.
    """
    def _fake_download(url, format_string, output_dir, progress_queue, **kwargs):
        for m in messages:
            progress_queue.put(m)
    return _fake_download


# ── DownloadJob dataclass ─────────────────────────────────────────────────────

class TestDownloadJob:

    def test_default_status_is_pending(self):
        job = DownloadJob(id="x", url="u", format_string="f", output_dir="/tmp")
        assert job.status == "pending"

    def test_default_progress_is_zero(self):
        job = DownloadJob(id="x", url="u", format_string="f", output_dir="/tmp")
        assert job.progress == 0.0

    def test_fields_set_correctly(self):
        job = DownloadJob(id="abc", url="http://x.com", format_string="best", output_dir="/out")
        assert job.id == "abc"
        assert job.url == "http://x.com"
        assert job.format_string == "best"
        assert job.output_dir == "/out"


# ── QueueManager.add ──────────────────────────────────────────────────────────

class TestAdd:

    def test_add_returns_string_id(self):
        qm = QueueManager()
        job_id = qm.add("http://x.com", "best", "/tmp")
        assert isinstance(job_id, str) and job_id

    def test_add_creates_pending_job(self):
        qm = QueueManager()
        job_id = qm.add("http://x.com", "best", "/tmp")
        assert qm.jobs[0].status == "pending"
        assert qm.jobs[0].id == job_id

    def test_add_emits_job_added_message(self):
        qm = QueueManager()
        job_id = qm.add("http://x.com", "best", "/tmp")
        msg = qm.update_queue.get_nowait()
        assert msg == {"type": "job_added", "job_id": job_id}

    def test_add_multiple_preserves_order(self):
        qm = QueueManager()
        ids = [qm.add(f"http://{i}.com", "best", "/tmp") for i in range(3)]
        assert [j.id for j in qm.jobs] == ids

    def test_add_stores_url_format_output_dir(self):
        qm = QueueManager()
        qm.add("http://x.com", "bestvideo+bestaudio", "/downloads")
        job = qm.jobs[0]
        assert job.url == "http://x.com"
        assert job.format_string == "bestvideo+bestaudio"
        assert job.output_dir == "/downloads"

    def test_ids_are_unique(self):
        qm = QueueManager()
        ids = [qm.add("http://x.com", "best", "/tmp") for _ in range(5)]
        assert len(set(ids)) == 5


# ── QueueManager.start_next ───────────────────────────────────────────────────

class TestStartNext:

    def test_returns_false_when_no_pending_jobs(self):
        qm = QueueManager()
        assert qm.start_next() is False

    def test_returns_true_when_job_started(self):
        qm = QueueManager()
        qm.add("http://x.com", "best", "/tmp")
        with patch("core.queue_manager.downloader.download", return_value=None):
            result = qm.start_next()
        assert result is True

    def test_job_transitions_to_downloading(self):
        qm = QueueManager()
        job_id = qm.add("http://x.com", "best", "/tmp")

        started = threading.Event()

        def _slow(url, fmt, out, pq, **kw):
            started.set()
            time.sleep(0.5)

        with patch("core.queue_manager.downloader.download", side_effect=_slow):
            qm.start_next()
            started.wait(timeout=1.0)
            assert qm.jobs[0].status == "downloading"

    def test_does_not_exceed_max_concurrent(self):
        qm = QueueManager(max_concurrent=1)
        qm.add("http://a.com", "best", "/tmp")
        qm.add("http://b.com", "best", "/tmp")

        started = threading.Event()

        def _slow(url, fmt, out, pq, **kw):
            started.set()
            time.sleep(1.0)

        with patch("core.queue_manager.downloader.download", side_effect=_slow):
            assert qm.start_next() is True
            started.wait(timeout=1.0)
            assert qm.start_next() is False  # limit reached

    def test_second_start_next_returns_false_when_first_running(self):
        qm = QueueManager(max_concurrent=1)
        qm.add("http://a.com", "best", "/tmp")
        qm.add("http://b.com", "best", "/tmp")

        barrier = threading.Event()

        def _block(url, fmt, out, pq, **kw):
            barrier.wait(timeout=2.0)

        with patch("core.queue_manager.downloader.download", side_effect=_block):
            qm.start_next()
            time.sleep(0.05)
            result = qm.start_next()
            barrier.set()

        assert result is False

    def test_skips_non_pending_jobs(self):
        qm = QueueManager()
        qm.add("http://a.com", "best", "/tmp")
        qm.add("http://b.com", "best", "/tmp")

        # Cancel the first job so it becomes cancelled
        job_id_a = qm.jobs[0].id
        qm.cancel(job_id_a)

        calls = []

        def _capture(url, *a, **kw):
            calls.append(url)

        with patch("core.queue_manager.downloader.download", side_effect=_capture):
            qm.start_next()
            time.sleep(0.15)

        assert calls == ["http://b.com"]


# ── Progress forwarding ───────────────────────────────────────────────────────

class TestProgressForwarding:

    def _run_with_messages(self, messages: list[dict]) -> tuple[QueueManager, str]:
        qm = QueueManager()
        job_id = qm.add("http://x.com", "best", "/tmp")
        with patch(
            "core.queue_manager.downloader.download",
            side_effect=_make_instant_download(messages),
        ):
            qm.start_next()
        return qm, job_id

    def _collect(self, qm: QueueManager, timeout: float = 1.5) -> list[dict]:
        msgs = []
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                msgs.append(qm.update_queue.get(timeout=0.05))
            except queue.Empty:
                break
        return msgs

    def test_downloading_message_forwarded_as_progress_type(self):
        qm, job_id = self._run_with_messages([
            {"status": "downloading", "percent": 50.0, "speed": "1MB/s", "eta": "5s", "filename": "v.mp4"},
            {"status": "finished", "filename": "v.mp4"},
        ])
        msgs = self._collect(qm)
        progress_msgs = [m for m in msgs if m.get("type") == "progress"]
        assert len(progress_msgs) >= 1
        assert progress_msgs[0]["percent"] == 50.0
        assert progress_msgs[0]["speed"] == "1MB/s"
        assert progress_msgs[0]["eta"] == "5s"
        assert progress_msgs[0]["job_id"] == job_id

    def test_downloading_updates_job_progress(self):
        qm, job_id = self._run_with_messages([
            {"status": "downloading", "percent": 75.0, "speed": "", "eta": "", "filename": ""},
            {"status": "finished", "filename": ""},
        ])
        time.sleep(0.3)
        assert qm.jobs[0].progress == 100.0  # finished sets to 100

    def test_finished_sets_job_progress_to_100(self):
        qm, job_id = self._run_with_messages([
            {"status": "finished", "filename": "v.mp4"},
        ])
        time.sleep(0.3)
        assert qm.jobs[0].progress == 100.0

    def test_finished_emits_status_change_done(self):
        qm, job_id = self._run_with_messages([
            {"status": "finished", "filename": "v.mp4"},
        ])
        found = _wait_for_status(qm, job_id, "done")
        assert found

    def test_finished_emits_finished_type_message(self):
        qm, job_id = self._run_with_messages([
            {"status": "finished", "filename": "vid.mp4"},
        ])
        msgs = self._collect(qm)
        finished = [m for m in msgs if m.get("type") == "finished"]
        assert any(m["job_id"] == job_id and m["filename"] == "vid.mp4" for m in finished)

    def test_error_sets_job_status_to_error(self):
        qm, job_id = self._run_with_messages([
            {"status": "error", "message": "network fail"},
        ])
        time.sleep(0.3)
        assert qm.jobs[0].status == "error"

    def test_error_emits_error_type_message(self):
        qm, job_id = self._run_with_messages([
            {"status": "error", "message": "network fail"},
        ])
        msgs = self._collect(qm)
        error_msgs = [m for m in msgs if m.get("type") == "error"]
        assert any(m["job_id"] == job_id and "network fail" in m["message"] for m in error_msgs)

    def test_no_message_from_downloader_marks_done(self):
        """If yt-dlp thread exits without pushing anything, job should become done."""
        qm, job_id = self._run_with_messages([])  # empty — no messages
        found = _wait_for_status(qm, job_id, "done", timeout=2.0)
        assert found


# ── QueueManager.cancel ───────────────────────────────────────────────────────

class TestCancel:

    def test_cancel_pending_job_sets_cancelled_immediately(self):
        qm = QueueManager()
        job_id = qm.add("http://x.com", "best", "/tmp")
        qm.cancel(job_id)
        assert qm.jobs[0].status == "cancelled"

    def test_cancel_pending_emits_status_change(self):
        qm = QueueManager()
        job_id = qm.add("http://x.com", "best", "/tmp")
        qm.update_queue.get_nowait()  # consume job_added
        qm.cancel(job_id)
        msg = qm.update_queue.get_nowait()
        assert msg == {"type": "status_change", "job_id": job_id, "status": "cancelled"}

    def test_cancel_pending_job_is_skipped_by_start_next(self):
        qm = QueueManager()
        job_id = qm.add("http://x.com", "best", "/tmp")
        qm.cancel(job_id)
        result = qm.start_next()
        assert result is False

    def test_cancel_nonexistent_id_does_nothing(self):
        qm = QueueManager()
        qm.cancel("no-such-id")  # must not raise

    def test_cancel_active_job_sets_cancelled_status(self):
        qm = QueueManager()
        job_id = qm.add("http://x.com", "best", "/tmp")

        running = threading.Event()

        def _long(url, fmt, out, pq, **kw):
            running.set()
            time.sleep(2.0)

        with patch("core.queue_manager.downloader.download", side_effect=_long):
            qm.start_next()
            running.wait(timeout=1.0)
            qm.cancel(job_id)

        found = _wait_for_status(qm, job_id, "cancelled", timeout=1.0)
        assert found

    def test_cancel_active_job_decrements_active_count(self):
        qm = QueueManager()
        qm.add("http://x.com", "best", "/tmp")

        running = threading.Event()

        def _long(url, fmt, out, pq, **kw):
            running.set()
            time.sleep(2.0)

        with patch("core.queue_manager.downloader.download", side_effect=_long):
            qm.start_next()
            running.wait(timeout=1.0)
            qm.cancel(qm.jobs[0].id)
            # wait for _run_job finally block
            time.sleep(0.3)

        with qm._lock:
            assert qm._active_count == 0


# ── Active count / slot management ───────────────────────────────────────────

class TestActiveCount:

    def test_active_count_zero_at_start(self):
        qm = QueueManager()
        with qm._lock:
            assert qm._active_count == 0

    def test_active_count_increments_when_job_starts(self):
        qm = QueueManager()
        qm.add("http://x.com", "best", "/tmp")
        started = threading.Event()

        def _slow(url, fmt, out, pq, **kw):
            started.set()
            time.sleep(1.0)

        with patch("core.queue_manager.downloader.download", side_effect=_slow):
            qm.start_next()
            started.wait(timeout=1.0)
            with qm._lock:
                assert qm._active_count == 1

    def test_active_count_decrements_after_job_finishes(self):
        qm = QueueManager()
        job_id = qm.add("http://x.com", "best", "/tmp")

        with patch(
            "core.queue_manager.downloader.download",
            side_effect=_make_instant_download([{"status": "finished", "filename": ""}]),
        ):
            qm.start_next()

        _wait_for_status(qm, job_id, "done", timeout=2.0)
        with qm._lock:
            assert qm._active_count == 0

    def test_two_slots_allows_two_concurrent(self):
        qm = QueueManager(max_concurrent=2)
        qm.add("http://a.com", "best", "/tmp")
        qm.add("http://b.com", "best", "/tmp")

        started = threading.Event()

        def _slow(url, fmt, out, pq, **kw):
            started.set()
            time.sleep(1.0)

        with patch("core.queue_manager.downloader.download", side_effect=_slow):
            assert qm.start_next() is True
            assert qm.start_next() is True
            assert qm.start_next() is False  # third would exceed limit


# ── jobs property ─────────────────────────────────────────────────────────────

class TestJobsProperty:

    def test_returns_list_copy(self):
        qm = QueueManager()
        qm.add("http://x.com", "best", "/tmp")
        snapshot = qm.jobs
        snapshot.clear()
        assert len(qm.jobs) == 1  # original unaffected

    def test_empty_when_no_jobs_added(self):
        qm = QueueManager()
        assert qm.jobs == []
