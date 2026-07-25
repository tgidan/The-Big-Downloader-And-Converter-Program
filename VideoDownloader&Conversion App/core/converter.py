"""
core/converter.py

ffmpeg wrapper, zero UI imports.
All progress is communicated via queue.Queue; never touches widgets directly.

Public API:
resolve_ffmpeg_binary(configured_path)          – concrete ffmpeg path, or raise
probe(path, ffmpeg_path)                        – source duration in seconds, or None
plan_output_path(input_path, output_dir, fmt)   – non-colliding target path
convert(input, output, fmt, q, …)               – blocking conversion; push progress to q
supported_formats()                             – target formats this module can produce
ConversionResult                                – outcome of one conversion
"""

from __future__ import annotations

import json
import queue
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from typing import NamedTuple

from core.utils import sanitize_filename

# Messages #

# Reused verbatim from core/downloader.py:64-68 so the Convert tab shows users the
# exact same guidance the downloader does when ffmpeg is missing.
_FFMPEG_MISSING = (
    "ffmpeg is not installed or not on PATH.\n"
    "Download it from https://ffmpeg.org/download.html and add it to your PATH, "
    "or place it alongside this application."
)


# Binary resolution #

"""Return True when running on Windows."""
def _is_windows() -> bool:
    return sys.platform == "win32"


"""Platform-correct executable name for `stem` (e.g. 'ffmpeg' -> 'ffmpeg.exe')."""
def _binary_name(stem: str) -> str:
    return f"{stem}.exe" if _is_windows() else stem


"""
Return a concrete path to the ffmpeg executable, or raise RuntimeError.

`configured_path` is the `ffmpeg_path` config value. It may be a directory
containing the binary or a direct path to the binary itself — the same
dir-or-binary handling as downloader._resolve_ffmpeg (core/downloader.py:45-59).

The one deliberate difference from downloader._resolve_ffmpeg: when ffmpeg is
found on PATH that function returns None, because yt-dlp resolves ffmpeg itself
and only needs to be told about non-PATH locations (core/downloader.py:61-62).
This module invokes ffmpeg directly via subprocess, so a PATH hit is resolved to
a real path via shutil.which instead. None is never a valid return here.
"""
def resolve_ffmpeg_binary(configured_path: str | None) -> str:
    if configured_path:
        candidate = Path(configured_path)
        # Accept either a directory (containing ffmpeg) or a direct path to the binary
        if candidate.is_dir():
            binary = candidate / _binary_name("ffmpeg")
        else:
            binary = candidate
        if binary.is_file():
            return str(binary)
        raise RuntimeError(
            f"ffmpeg not found at the specified location: {configured_path}\n"
            "Make sure the bundled ffmpeg binary is present."
        )

    found = shutil.which("ffmpeg")
    if found:
        return found

    raise RuntimeError(_FFMPEG_MISSING)


"""
Return a concrete path to the ffprobe executable, or None if it can't be found.

Mirrors library_manager._resolve_ffprobe (core/library_manager.py:73-83): if
`ffmpeg_path` is a directory, look for ffprobe inside it; if it points at the
ffmpeg binary, look for ffprobe alongside it; otherwise fall back to PATH.

Deliberately a local twin rather than an import: that function is private to a
module about browsing the video library, and this file must not grow a
dependency on it just to read a duration. downloader.py and library_manager.py
already each carry their own copy of this resolution logic — a third one here
follows the established convention rather than breaking it.

Never raises. ffprobe is only needed to compute a percentage, so its absence
degrades progress reporting rather than failing the conversion.
"""
def _resolve_ffprobe_binary(ffmpeg_path: str | None) -> str | None:
    name = _binary_name("ffprobe")

    if ffmpeg_path:
        candidate = Path(ffmpeg_path)
        directory = candidate if candidate.is_dir() else candidate.parent
        binary = directory / name
        if binary.is_file():
            return str(binary)

    return shutil.which("ffprobe")


# Probing #

"""
Return the duration of the media file at `path` in seconds, or None.

Reads `format.duration` from ffprobe's JSON output — the same call and the same
string-to-float parsing already done by library_manager.fill_metadata
(core/library_manager.py:203-239).

Never raises: a missing ffprobe, a non-media file, malformed JSON, or a timeout
all yield None so the caller can fall back to progress without a percentage.

Output is decoded as UTF-8 with errors replaced — the default locale codec
raises on non-ASCII paths echoed back in ffprobe's output.
"""
def probe(path: str, ffmpeg_path: str | None = None) -> float | None:
    ffprobe = _resolve_ffprobe_binary(ffmpeg_path)
    if not ffprobe:
        return None

    try:
        result = subprocess.run(
            [ffprobe, "-v", "quiet", "-print_format", "json",
             "-show_format", "-show_streams", str(path)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
        )
        data = json.loads(result.stdout)
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        return None

    duration_str = data.get("format", {}).get("duration")
    if duration_str is None:
        stream = next(iter(data.get("streams", [])), None)
        duration_str = stream.get("duration") if stream else None

    try:
        return float(duration_str)
    except (TypeError, ValueError):
        return None


# Output path planning #

"""
Return the path a conversion of `input_path` should write to.

The base name is the input's stem run through core.utils.sanitize_filename, and
the extension is `target_format`. When a file already exists at that exact path,
' (n)' is appended before the extension until the name is free — the same shape
core.utils.unique_path produces, so output names stay consistent app-wide.

unique_path is deliberately NOT used here. It is built on find_conflicts, which
matches on stem alone and ignores the extension (core/utils.py:69-73), so with
only 'clip.webm' present unique_path(dir, 'clip', 'mp3') already returns
'clip (1).mp3'. Converting a file into its own folder — the commonest case —
would then always produce a needlessly numbered name. A conflict here means the
concrete target path exists; the source file beside it is not a conflict.

`overwrite=True` returns the plain target path with no numbering, for callers
that have obtained the user's consent to replace an existing file.
"""
def plan_output_path(
    input_path: str,
    output_dir: str,
    target_format: str,
    *,
    overwrite: bool = False,
) -> Path:

    directory = Path(output_dir)
    ext = target_format.lstrip(".").lower()
    suffix = f".{ext}" if ext else ""
    base = sanitize_filename(Path(input_path).stem)

    target = directory / f"{base}{suffix}"
    if overwrite:
        return target

    n = 0
    while target.exists():
        n += 1
        target = directory / f"{base} ({n}){suffix}"

    return target


# Result #

"""
Outcome of a single conversion.

ok          – the conversion completed and the output file was written
cancelled   – the job was stopped via its cancel_event (ok is False)
output_path – the file written, or None when nothing was produced
message     – ffmpeg's failure detail, or a short human-readable summary
"""
class ConversionResult(NamedTuple):
    ok: bool
    cancelled: bool
    output_path: str | None
    message: str


# Conversion #

# ffmpeg arguments per target format. webm -> mp3 is an audio extraction plus a
# re-encode: -vn drops the video stream entirely and libmp3lame encodes the
# demuxed Opus/Vorbis audio. -q:a 2 is LAME's VBR ~190 kbps — a sane fixed
# default; bitrate/quality controls are deliberately out of scope for now.
_ENCODER_ARGS: dict[str, list[str]] = {
    "mp3": ["-vn", "-c:a", "libmp3lame", "-q:a", "2"],
}

# Grace periods for shutting the subprocess down. Neither is a poll interval:
# cancel_event is observed once per -progress block, which ffmpeg emits roughly
# twice a second.
_STDERR_JOIN_TIMEOUT = 5.0    # wait for the stderr reader to finish
_TERMINATE_TIMEOUT   = 10.0   # wait after terminate() before escalating to kill()


"""Target formats convert() knows how to produce."""
def supported_formats() -> list[str]:
    return sorted(_ENCODER_ARGS)


"""Read `stream` to exhaustion into `sink`. Runs on its own thread."""
def _drain_stream(stream, sink: list[str]) -> None:
    try:
        for line in iter(stream.readline, ""):
            if line.strip():
                sink.append(line.rstrip())
    except (OSError, ValueError):
        pass


"""Last meaningful line of ffmpeg's stderr, or '' when it said nothing."""
def _last_error_line(lines: list[str]) -> str:
    return lines[-1].strip() if lines else ""


"""Delete a half-written output file, ignoring the case where none exists."""
def _remove_partial(output_path: str) -> None:
    try:
        Path(output_path).unlink(missing_ok=True)
    except OSError:
        pass


"""
Parse one `key=value` -progress line into elapsed output seconds, or None.

Only `out_time_us` is read. ffmpeg's sibling `out_time_ms` key is misnamed —
it carries MICROSECONDS too (verified on 8.1.1: an 8.0 s source reports
out_time_ms=8000000), so reading it as milliseconds yields percentages 1000x
too small. Early blocks can carry "N/A" before any output has been written.
"""
def _progress_seconds(line: str) -> float | None:
    key, sep, value = line.strip().partition("=")
    if not sep or key != "out_time_us":
        return None
    try:
        return float(value) / 1_000_000
    except ValueError:
        return None


"""
Convert `input_path` to `output_path`, blocking until ffmpeg exits.
Runs synchronously, call this from a background thread.

Progress updates are pushed onto `progress_queue` as dicts:
    { "status": "converting", "percent": float | None, "filename": str }
    { "status": "done",       "filename": str }
    { "status": "error",      "message": str }
    { "status": "cancelled",  "filename": str }

`percent` is derived from ffmpeg's -progress stream divided by the source
duration from probe(). When the duration cannot be determined, `percent` is
None for the whole job rather than a fabricated number — the caller should
render an indeterminate state and treat "done" as completion. A short
conversion may emit only ffmpeg's final progress block, so jumping straight
from 0 to 100 with no intermediate updates is normal; a successful run with a
known duration always ends on 100.

`cancel_event`, when set, terminates the ffmpeg process and deletes the target
file. ffmpeg leaves a partial file behind on terminate, so this cleanup is
this function's responsibility, not ffmpeg's. The event is observed between
progress blocks (~0.5 s). A partial file is also removed when ffmpeg fails,
so a broken output never survives to collide with the next attempt.

Expected failures — missing ffmpeg, an unsupported format, unreadable input —
are reported through `progress_queue` and the returned ConversionResult rather
than raised, mirroring downloader.download (core/downloader.py:196-282).

`options` is accepted and ignored; it reserves room for bitrate/quality
controls without changing the signature later.
"""
def convert(
    input_path: str,
    output_path: str,
    target_format: str,
    progress_queue: queue.Queue,
    *,
    cancel_event: threading.Event | None = None,
    ffmpeg_path: str | None = None,
    **options,
) -> ConversionResult:

    filename = Path(output_path).name

    """Push a terminal error message and build the matching result."""
    def _fail(message: str) -> ConversionResult:
        progress_queue.put({"status": "error", "message": message})
        return ConversionResult(False, False, None, message)

    """Push a terminal cancelled message and build the matching result."""
    def _cancelled() -> ConversionResult:
        progress_queue.put({"status": "cancelled", "filename": filename})
        return ConversionResult(False, True, None, "Cancelled")

    # A cancel that arrives before the process starts must still be honoured
    if cancel_event is not None and cancel_event.is_set():
        return _cancelled()

    try:
        ffmpeg = resolve_ffmpeg_binary(ffmpeg_path)
    except RuntimeError as exc:
        return _fail(str(exc))

    encoder_args = _ENCODER_ARGS.get(target_format.lstrip(".").lower())
    if encoder_args is None:
        return _fail(f"Unsupported target format: {target_format}")

    if not Path(input_path).is_file():
        return _fail(f"Input file not found: {input_path}")

    duration = probe(input_path, ffmpeg_path)

    # Every argument stays a separate list element — that is what makes spaces,
    # unicode and em-dashes in paths work without any quoting of our own.
    cmd = [
        ffmpeg, "-hide_banner", "-nostdin", "-loglevel", "error",
        "-progress", "pipe:1", "-y",
        "-i", str(input_path),
        *encoder_args,
        str(output_path),
    ]

    try:
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except OSError as exc:
        return _fail(f"Could not start ffmpeg: {exc}")

    # stderr is drained on its own thread: reading stdout in a loop while ffmpeg
    # writes to a stderr pipe nobody is emptying deadlocks as soon as that pipe
    # buffer fills, which a short test clip never reaches but a real file does.
    stderr_lines: list[str] = []
    stderr_thread = threading.Thread(
        target=_drain_stream,
        args=(proc.stderr, stderr_lines),
        daemon=True,
        name="ffmpeg-stderr",
    )
    stderr_thread.start()

    cancelled = False
    last_percent = 0.0

    # readline() rather than `for line in proc.stdout` — iteration read-ahead
    # would hold progress blocks back in a buffer instead of surfacing them live
    for line in iter(proc.stdout.readline, ""):
        if cancel_event is not None and cancel_event.is_set():
            cancelled = True
            break

        seconds = _progress_seconds(line)
        if seconds is None:
            continue

        if duration and duration > 0:
            last_percent = max(0.0, min(100.0, seconds / duration * 100))
            percent: float | None = round(last_percent, 1)
        else:
            percent = None

        progress_queue.put({
            "status":   "converting",
            "percent":  percent,
            "filename": filename,
        })

    proc.stdout.close()

    if cancelled:
        proc.terminate()
        try:
            proc.wait(timeout=_TERMINATE_TIMEOUT)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        stderr_thread.join(timeout=_STDERR_JOIN_TIMEOUT)
        _remove_partial(output_path)
        return _cancelled()

    returncode = proc.wait()
    stderr_thread.join(timeout=_STDERR_JOIN_TIMEOUT)

    # Any non-zero code is a failure. ffmpeg returns raw AVERROR values here —
    # a corrupt input exits 3199971767, not 1 — so never test a specific code.
    if returncode != 0:
        _remove_partial(output_path)
        return _fail(
            _last_error_line(stderr_lines)
            or f"ffmpeg exited with code {returncode}"
        )

    # Top up to 100 only when percentages were real all along; with an unknown
    # duration a lone 100.0 after a run of Nones would be the fabrication this
    # function promises not to make. "done" is what marks completion there.
    if duration and duration > 0 and last_percent < 100.0:
        progress_queue.put({
            "status":   "converting",
            "percent":  100.0,
            "filename": filename,
        })

    progress_queue.put({"status": "done", "filename": filename})
    return ConversionResult(True, False, str(output_path), "")
