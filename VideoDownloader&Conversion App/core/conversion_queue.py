"""
core/conversion_queue.py

Manages an ordered queue of file conversion jobs.
All status updates are pushed onto `update_queue` (a queue.Queue) so the UI
can poll with root.after() without blocking the main thread.

Mirrors core/queue_manager.py, with one deliberate structural difference:
QueueManager runs downloader.download() in a nested thread because yt-dlp is
not interruptible, so its cancel event can only be observed between polls of a
private queue. A conversion is an ffmpeg subprocess and IS interruptible —
converter.convert() takes the cancel event and terminates ffmpeg itself — so
the job runs directly on the worker thread with no nesting and no polling.
"""

from __future__ import annotations

import queue
import threading
import uuid
from dataclasses import dataclass
from typing import Callable, Literal

from core import config_manager, converter

Status = Literal["pending", "converting", "done", "error", "cancelled"]


"""A single conversion job tracked by ConversionQueue."""
@dataclass
class ConversionJob:
    id:            str
    input_path:    str
    output_path:   str
    target_format: str
    status:        Status = "pending"
    progress:      float  = 0.0


"""
Minimal stand-in for the queue.Queue that converter.convert() pushes onto.

convert() only ever calls .put(), so handing it this instead of a real queue
lets each message be translated and forwarded to update_queue the moment it is
produced. A real queue would have to be drained by somebody — either after
convert() returns, which would withhold every progress update until the job was
already finished, or by a second thread, which is exactly the nesting this
module set out to avoid.
"""
class _ProgressChannel:

    def __init__(self, handler: Callable[[dict], None]) -> None:
        self._handler = handler

    def put(self, message: dict, block: bool = True, timeout: float | None = None) -> None:
        self._handler(message)


"""
Maintains an ordered list of ConversionJobs and runs them on background
threads, one at a time by default.

Update messages pushed to `update_queue`:
    {"type": "job_added",     "job_id": str}
    {"type": "status_change", "job_id": str, "status": str}
    {"type": "progress",      "job_id": str, "percent": float | None,
                                "filename": str}
    {"type": "finished",      "job_id": str, "filename": str}
    {"type": "error",         "job_id": str, "message": str}

`percent` is None when the source duration could not be determined — see
converter.convert(). Consumers must render an indeterminate state for it and
treat "finished" as completion rather than waiting for a 100% progress message.

Jobs advance on their own: a finishing worker starts the next pending job
before it exits, so the caller only has to prime the queue with one
start_next() after adding. Calling start_next() again on a terminal message is
safe but unnecessary — it returns False while the queue is busy.
"""
class ConversionQueue:
    def __init__(self, max_concurrent: int = 1) -> None:
        self._max_concurrent = max_concurrent
        self._jobs: list[ConversionJob] = []
        self._lock = threading.Lock()
        self.update_queue: queue.Queue = queue.Queue()
        self._cancel_events: dict[str, threading.Event] = {}
        self._active_count: int = 0

    # Public API #

    """Thread-safe snapshot of the current job list."""
    @property
    def jobs(self) -> list[ConversionJob]:
        with self._lock:
            return list(self._jobs)

    """Enqueue a new conversion job. Returns the job_id."""
    def add(self, input_path: str, output_path: str, target_format: str) -> str:
        job = ConversionJob(
            id=str(uuid.uuid4()),
            input_path=input_path,
            output_path=output_path,
            target_format=target_format,
        )
        with self._lock:
            self._jobs.append(job)
        self.update_queue.put({"type": "job_added", "job_id": job.id})
        return job.id

    """
    Pick the next pending job and start it on a daemon thread.
    Does nothing and returns False if at the concurrency limit or no
    pending jobs remain.
    """
    def start_next(self) -> bool:
        with self._lock:
            if self._active_count >= self._max_concurrent:
                return False
            job = next((j for j in self._jobs if j.status == "pending"), None)
            if job is None:
                return False
            job.status = "converting"
            self._active_count += 1
            cancel_event = threading.Event()
            self._cancel_events[job.id] = cancel_event

        self.update_queue.put(
            {"type": "status_change", "job_id": job.id, "status": "converting"}
        )

        thread = threading.Thread(
            target=self._run_job,
            args=(job, cancel_event),
            daemon=True,
            name=f"conv-{job.id[:8]}",
        )
        thread.start()
        return True

    """
    Cancel a job.  If still pending, marks it cancelled immediately and
    ffmpeg is never started.  If active, signals convert() to terminate
    the running ffmpeg process.
    """
    def cancel(self, job_id: str) -> None:
        with self._lock:
            job = next((j for j in self._jobs if j.id == job_id), None)
            if job is None:
                return
            if job.status == "pending":
                job.status = "cancelled"
                self.update_queue.put(
                    {"type": "status_change", "job_id": job_id, "status": "cancelled"}
                )
                return
            event = self._cancel_events.get(job_id)

        if event:
            event.set()

    # Internal #

    """Update job.status and push a status_change event to update_queue."""
    def _set_status(self, job: ConversionJob, status: Status) -> None:
        with self._lock:
            job.status = status
        self.update_queue.put(
            {"type": "status_change", "job_id": job.id, "status": status}
        )

    """
    Translate one converter.convert() message into its job-scoped equivalent
    and forward it. Called on the worker thread, as convert() produces them.
    """
    def _on_convert_message(self, job: ConversionJob, message: dict) -> None:
        status = message.get("status")

        if status == "converting":
            percent = message.get("percent")
            if percent is not None:
                with self._lock:
                    job.progress = percent
            self.update_queue.put({
                "type":     "progress",
                "job_id":   job.id,
                "percent":  percent,
                "filename": message.get("filename", ""),
            })

        elif status == "done":
            with self._lock:
                job.progress = 100.0
            self._set_status(job, "done")
            self.update_queue.put({
                "type":     "finished",
                "job_id":   job.id,
                "filename": message.get("filename", ""),
            })

        elif status == "error":
            self._set_status(job, "error")
            self.update_queue.put({
                "type":    "error",
                "job_id":  job.id,
                "message": message.get("message", "Unknown error"),
            })

        elif status == "cancelled":
            self._set_status(job, "cancelled")

    """
    Runs on a daemon thread.  Calls converter.convert() directly — no nested
    thread — and hands it the cancel event so it can terminate ffmpeg itself.

    ffmpeg_path is read from config here rather than held as constructor state,
    so a path changed in Settings takes effect on the next job with no rewiring
    (same as QueueManager._run_job, core/queue_manager.py:160-165).

    The slot is released in `finally` whatever happens, so one failed job can
    never wedge the queue, and the next pending job is started from there too.

    That last part is deliberate. The terminal message reaches update_queue
    before this thread finishes unwinding, so a consumer that calls start_next()
    the moment it reads "finished" can find the slot still occupied and get
    False back — leaving the rest of the queue stranded with nobody left to
    start it. Advancing from here removes the race instead of depending on the
    UI polling late enough to miss it. A start_next() call from the consumer as
    well is harmless: it just returns False once the queue is already busy.
    """
    def _run_job(self, job: ConversionJob, cancel_event: threading.Event) -> None:
        channel = _ProgressChannel(lambda msg: self._on_convert_message(job, msg))

        try:
            result = converter.convert(
                job.input_path,
                job.output_path,
                job.target_format,
                channel,
                cancel_event=cancel_event,
                ffmpeg_path=config_manager.get("ffmpeg_path"),
            )

            # Defensive: convert() always emits a terminal message, but if a
            # future change ever returns without one, reconcile from the result
            # rather than leaving the job stuck reading as still converting.
            with self._lock:
                unresolved = job.status == "converting"
            if unresolved:
                if result.cancelled:
                    self._set_status(job, "cancelled")
                elif result.ok:
                    self._set_status(job, "done")
                else:
                    self._set_status(job, "error")

        except Exception as exc:  # noqa: BLE001 — a crash must not strand the queue
            self._set_status(job, "error")
            self.update_queue.put({
                "type":    "error",
                "job_id":  job.id,
                "message": str(exc) or "Conversion failed",
            })

        finally:
            with self._lock:
                self._active_count -= 1
                self._cancel_events.pop(job.id, None)
            self.start_next()
