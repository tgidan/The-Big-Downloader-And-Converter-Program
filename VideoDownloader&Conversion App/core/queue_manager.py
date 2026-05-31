"""
core/queue_manager.py

Manages an ordered queue of download jobs.
All status updates are pushed onto `update_queue` (a queue.Queue) so the UI
can poll with root.after() without blocking the main thread.
"""

from __future__ import annotations

import queue
import threading
import uuid
from dataclasses import dataclass, field
from typing import Literal

from core import config_manager, downloader

Status = Literal["pending", "downloading", "done", "error", "cancelled"]


@dataclass
class DownloadJob:
    id: str
    url: str
    format_string: str
    output_dir: str
    status: Status = "pending"
    progress: float = 0.0


"""
Maintains an ordered list of DownloadJobs and drives downloads on
background threads.

Update messages pushed to `update_queue`:
    {"type": "job_added",     "job_id": str}
    {"type": "status_change", "job_id": str, "status": str}
    {"type": "progress",      "job_id": str, "percent": float,
                                "speed": str, "eta": str, "filename": str}
    {"type": "finished",      "job_id": str, "filename": str}
    {"type": "error",         "job_id": str, "message": str}
"""
class QueueManager:
    def __init__(self, max_concurrent: int = 1) -> None:
        self._max_concurrent = max_concurrent
        self._jobs: list[DownloadJob] = []
        self._lock = threading.Lock()
        self.update_queue: queue.Queue = queue.Queue()
        self._cancel_events: dict[str, threading.Event] = {}
        self._active_count: int = 0

    # Public API #

    @property
    def jobs(self) -> list[DownloadJob]:
        with self._lock:
            return list(self._jobs)
        
    """Enqueue a new download job. Returns the job_id."""
    def add(self, url: str, format_string: str, output_dir: str) -> str:
       
        job = DownloadJob(
            id=str(uuid.uuid4()),
            url=url,
            format_string=format_string,
            output_dir=output_dir,
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
            job.status = "downloading"
            self._active_count += 1
            cancel_event = threading.Event()
            self._cancel_events[job.id] = cancel_event

        thread = threading.Thread(
            target=self._run_job,
            args=(job, cancel_event),
            daemon=True,
            name=f"dl-{job.id[:8]}",
        )
        thread.start()
        return True

    """
    Cancel a job.  If still pending, marks it cancelled immediately.
    If active, signals the background thread to stop after the next
    progress poll interval (~100 ms).
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

    def _set_status(self, job: DownloadJob, status: Status) -> None:
        with self._lock:
            job.status = status
        self.update_queue.put(
            {"type": "status_change", "job_id": job.id, "status": status}
        )

    """
    Runs on a daemon thread.  Starts downloader.download() in a nested
    daemon thread so the cancel_event can be checked between progress
    polls without blocking on the yt-dlp call.
    """
    def _run_job(self, job: DownloadJob, cancel_event: threading.Event) -> None:
        
        job_queue: queue.Queue = queue.Queue()

        dl_thread = threading.Thread(
            target=downloader.download,
            args=(job.url, job.format_string, job.output_dir, job_queue),
            kwargs={"ffmpeg_location": config_manager.get("ffmpeg_location")},
            daemon=True,
            name=f"yt-{job.id[:8]}",
        )
        dl_thread.start()

        try:
            while True:
                if cancel_event.is_set():
                    self._set_status(job, "cancelled")
                    return

                try:
                    msg = job_queue.get(timeout=0.1)
                except queue.Empty:
                    if not dl_thread.is_alive():
                        # yt-dlp finished without emitting a final status message
                        self._set_status(job, "done")
                        return
                    continue

                status = msg.get("status")

                if status == "downloading":
                    with self._lock:
                        job.progress = msg["percent"]
                    self.update_queue.put({
                        "type":     "progress",
                        "job_id":   job.id,
                        "percent":  msg["percent"],
                        "speed":    msg.get("speed", ""),
                        "eta":      msg.get("eta", ""),
                        "filename": msg.get("filename", ""),
                    })

                elif status == "finished":
                    with self._lock:
                        job.progress = 100.0
                    self._set_status(job, "done")
                    self.update_queue.put({
                        "type":     "finished",
                        "job_id":   job.id,
                        "filename": msg.get("filename", ""),
                    })
                    return

                elif status == "error":
                    self._set_status(job, "error")
                    self.update_queue.put({
                        "type":    "error",
                        "job_id":  job.id,
                        "message": msg.get("message", "Unknown error"),
                    })
                    return

        finally:
            with self._lock:
                self._active_count -= 1
                self._cancel_events.pop(job.id, None)
