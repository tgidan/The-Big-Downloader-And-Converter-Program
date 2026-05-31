# The Big Downloader & Converter

A desktop app for downloading videos and converting them to common formats, built with **yt-dlp** and **CustomTkinter**. Primary focus is YouTube, but any site supported by yt-dlp works.

---

## Requirements

- Python 3.10+
- [ffmpeg](https://ffmpeg.org/download.html) — must be on `PATH` or bundled alongside the app
- Python packages (install via pip):

```bash
pip install -r "VideoDownloader&Conversion App/requirements.txt"
```

| Package | Purpose |
| --- | --- |
| `yt-dlp` | Video extraction and downloading |
| `customtkinter` | Modern Tkinter UI components |

---

## Project structure

```text
VideoDownloader&Conversion App/
├── main.py                  # Entry point
├── requirements.txt
├── config.json              # Persisted user settings
├── assets/                  # Icons, images
├── core/
│   ├── downloader.py        # yt-dlp wrapper (no UI imports)
│   ├── queue_manager.py     # Download queue logic
│   └── config_manager.py   # Load/save config.json
├── ui/
│   ├── app_window.py        # Root window
│   ├── url_panel.py         # URL input panel
│   ├── quality_panel.py     # Format/quality selector
│   └── download_panel.py   # Progress display
└── tests/
    ├── test_downloader.py   # Unit tests for core/downloader.py
    └── test_queue_manager.py  # Unit tests for core/queue_manager.py
```

---

## core/downloader.py

The download engine. Has **zero UI imports** — all progress is communicated via `queue.Queue` so it can safely run on a background thread.

### Public API

#### `fetch_formats(url) -> list[dict]`

Queries yt-dlp for available formats without downloading anything. Returns a list of format dicts:

```python
{
    "format_id": str,
    "ext":       str,
    "height":    int | None,   # None for audio-only streams
    "note":      str,          # human-readable label, e.g. "1080p, 30fps"
    "vcodec":    str,
    "acodec":    str,
    "filesize":  int | None,   # bytes; falls back to filesize_approx
}
```

Raises `yt_dlp.utils.DownloadError` for private or unavailable videos.

---

#### `build_video_format_string(height) -> str`

Returns a yt-dlp format selector string for the given resolution cap:

```python
build_video_format_string(1080)
# → "bestvideo[height<=1080]+bestaudio/best"
```

---

#### `build_audio_format_string() -> str`

Returns `"bestaudio/best"`.

---

#### `download(url, format_string, output_dir, progress_queue, *, audio_only, ffmpeg_location, cookiefile)`

Downloads a single URL. **Blocking — call from a background thread.**

| Parameter | Type | Description |
| --- | --- | --- |
| `url` | `str` | URL to download |
| `format_string` | `str` | yt-dlp format selector (from the helpers above) |
| `output_dir` | `str` | Destination directory |
| `progress_queue` | `queue.Queue` | Receives progress dicts (see below) |
| `audio_only` | `bool` | Extract audio as MP3 (192 kbps) via FFmpegExtractAudio |
| `ffmpeg_location` | `str \| None` | Path to ffmpeg binary or directory; `None` uses PATH |
| `cookiefile` | `str \| None` | Path to a Netscape-format cookie file |

#### Progress queue messages

```python
# While downloading
{"status": "downloading", "percent": float, "speed": str, "eta": str, "filename": str}

# When complete
{"status": "finished", "filename": str}

# On error
{"status": "error", "message": str}
```

`yt_dlp.utils.DownloadError` is caught internally and forwarded as an `"error"` message.

---

#### `check_ytdlp_update() -> str | None`

Checks whether a newer yt-dlp release is available. Returns a message string if an update exists, `None` if already up to date or if the check fails. Non-fatal — swallows all exceptions.

---

### Private helpers

| Helper | Description |
| --- | --- |
| `_is_windows()` | Returns `True` when running on Windows |
| `_resolve_ffmpeg(ffmpeg_location)` | Resolves the ffmpeg binary path; raises `RuntimeError` if unavailable |

---

## core/queue_manager.py

Manages an ordered list of download jobs and drives them on background threads. Has **zero UI imports** — all updates flow through a `queue.Queue` the UI can poll with `root.after()`.

### Types

#### `DownloadJob` (dataclass)

| Field | Type | Default | Description |
| --- | --- | --- | --- |
| `id` | `str` | — | UUID assigned at creation |
| `url` | `str` | — | URL to download |
| `format_string` | `str` | — | yt-dlp format selector |
| `output_dir` | `str` | — | Destination directory |
| `status` | `str` | `"pending"` | One of `pending` / `downloading` / `done` / `error` / `cancelled` |
| `progress` | `float` | `0.0` | Download completion, 0–100 |

### API

#### `QueueManager(max_concurrent=1)`

| Parameter | Type | Default | Description |
| --- | --- | --- | --- |
| `max_concurrent` | `int` | `1` | Maximum simultaneous downloads |

---

#### `add(url, format_string, output_dir) -> str`

Enqueues a new job and returns its `job_id`. Emits a `job_added` message to `update_queue`.

---

#### `start_next() -> bool`

Picks the next `pending` job and starts it on a daemon `threading.Thread`. Returns `False` if the concurrency limit is already reached or no pending jobs remain.

---

#### `cancel(job_id) -> None`

Cancels a job:

- **Pending** — status is set to `cancelled` immediately; no thread is started.
- **Active** — a `threading.Event` is set; the background thread sees it within ~100 ms and stops processing. The underlying yt-dlp call may continue briefly before the daemon thread exits naturally.

---

#### `update_queue` (`queue.Queue`)

The UI polls this queue (via `root.after()`) to receive all state changes. Message shapes:

```python
# A new job was enqueued
{"type": "job_added",     "job_id": str}

# A job's status changed
{"type": "status_change", "job_id": str, "status": str}

# Download progress tick
{"type": "progress",      "job_id": str, "percent": float,
                          "speed": str, "eta": str, "filename": str}

# Download completed successfully
{"type": "finished",      "job_id": str, "filename": str}

# Download failed
{"type": "error",         "job_id": str, "message": str}
```

---

#### `jobs` (property) `-> list[DownloadJob]`

Returns a snapshot copy of the current job list. Safe to iterate from the UI thread.

---

### Concurrency model

```text
QueueManager.start_next()
    └─ daemon Thread (_run_job)          ← managed by QueueManager
           └─ daemon Thread (downloader.download)   ← talks to yt-dlp
```

`_run_job` polls the inner thread's progress queue with a 100 ms timeout, forwarding messages to `update_queue` and checking the cancel event on every iteration. Expanding to N parallel downloads requires only raising `max_concurrent`.

---

## Running the tests

Tests are written with **pytest** and cover `core/downloader.py` entirely using mocks — no network access or real ffmpeg required.

```bash
# Install pytest if needed
pip install pytest

# Run from the app directory
cd "VideoDownloader&Conversion App"
python -m pytest tests/ -v
```

### What is tested

| Area | Cases covered |
| --- | --- |
| `_is_windows` | Windows and non-Windows platform detection |
| `_resolve_ffmpeg` | Explicit directory (Windows & POSIX binary names), explicit file path, missing binary, ffmpeg on `PATH`, ffmpeg absent from `PATH` |
| `fetch_formats` | All returned fields, note building (height+fps, height only, `format_note` priority, audio-only), `filesize_approx` fallback, empty format list, order preservation |
| `build_video_format_string` | Parametrized for 360p / 720p / 1080p / 2160p |
| `build_audio_format_string` | Fixed return value |
| `download` — progress hook | `"downloading"` (normal, estimate fallback, zero total), `"finished"`, `"error"` (with and without error key) |
| `download` — error handling | `DownloadError` caught and forwarded to queue |
| `download` — opts | `audio_only` postprocessor, `cookiefile`, resolved ffmpeg path, absent ffmpeg key, `outtmpl`, format string |
| `check_ytdlp_update` | Up-to-date (case-insensitive), update message, stderr output, empty/whitespace output, generic exception, `TimeoutExpired` |
