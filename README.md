# The Big Downloader & Converter (TBD&C)

A desktop app for downloading videos with **yt-dlp** and browsing the resulting
library, built with **CustomTkinter**. Any site yt-dlp supports works, not
just YouTube.

The main window is split into two tabs: **Download**, which holds the URL
input, quality picker and queue sidebar, and **Convert**, which converts
`.webm` files to `.mp3` — one at a time, with per-file progress and a cancel
button. Other target formats aren't built yet. The header buttons, the warning
banner and the status bar sit outside the tabs and stay visible from both.
Whichever tab you leave selected is the one you come back to next launch.

---

## Requirements

- Python 3.10+
- Python packages, installed via pip:

  ```bash
  pip install -r "VideoDownloader&Conversion App/requirements.txt"
  ```

  | Package | Purpose |
  | --- | --- |
  | `yt-dlp` | Video extraction and downloading |
  | `customtkinter` | UI framework |
  | `Pillow` | Thumbnail/image handling |

- **ffmpeg and ffprobe — optional, external, not pip packages.** They are not
  listed in `requirements.txt` and are not bundled with the app. Without
  them, the app still runs, but:
  - downloads that require merging separate video/audio streams will fail
    (single-file formats still work);
  - the library panel's resolution, duration, and thumbnail columns show a
    placeholder instead of real values;
  - the **Convert** tab doesn't work at all — clicking **Convert** shows an
    inline error naming ffmpeg and how to install it, rather than failing
    silently or crashing. The Download tab keeps working regardless.

  If ffmpeg is on your system `PATH`, it's picked up automatically. Otherwise,
  set a path in **Settings → ffmpeg path** — it can point either at the
  directory containing the `ffmpeg`/`ffprobe` binaries or directly at the
  `ffmpeg` binary itself (see `core/downloader.py:44-65` for the exact
  resolution logic; `core/library_manager.py` mirrors it for `ffprobe`).
  The converter resolves a `PATH` hit to a concrete path of its own, because it
  runs ffmpeg directly — `core/downloader.py:61-62` returns `None` there
  instead, which is all yt-dlp needs since it does its own lookup.

## Running the app

```bash
cd "VideoDownloader&Conversion App"
python main.py
```

## Configuration

Runtime settings (output directory, conversion output folder, last-used
quality, ffmpeg path, loudness normalization, cookie file, library view mode,
last active tab) are persisted to:

```text
~/.tbdc/config.json
```

This file is created automatically on first run and edited via the in-app
Settings panel — you shouldn't need to touch it by hand.

The `config.json` at the root of `VideoDownloader&Conversion App/` is
**unused** — an empty leftover file. Editing it has no effect on the app.

---

## Project structure

```text
VideoDownloader&Conversion App/
├── main.py                   # Entry point; preflight checks, CTk bootstrap
├── requirements.txt
├── assets/                   # Icon and logo
├── core/                     # Zero UI imports — safe to call from any thread
│   ├── downloader.py         # yt-dlp wrapper; download(), format helpers
│   ├── queue_manager.py      # Ordered download queue, background threads
│   ├── converter.py          # ffmpeg wrapper; convert(), probe(), output paths
│   ├── conversion_queue.py   # Ordered conversion queue, background threads
│   ├── config_manager.py     # Reads/writes ~/.tbdc/config.json
│   ├── library_manager.py    # Folder tree, video listing/search, ffprobe
│   │                         #   metadata, ffmpeg thumbnails, move/create
│   └── utils.py               # Filename sanitization, conflict/uniqueness
├── ui/
│   ├── app_window.py          # Root window; Download/Convert tabs, wires panels
│   ├── url_panel.py            # URL input, format fetch, preview card
│   ├── quality_panel.py        # Quality picker, output folder + subfolder
│   ├── download_panel.py       # Queue sidebar (progress, pause, reorder)
│   ├── converter_panel.py      # Convert tab; file picker, jobs (see below)
│   ├── settings_panel.py       # Preferences dialog
│   └── library_panel.py        # Library browser (see below)
└── tests/                     # pytest; core/ modules and pure UI helpers
```

## Running the tests

```bash
cd "VideoDownloader&Conversion App"
pip install pytest
python -m pytest tests/ -v
```

Tests cover the `core/` modules (mocked yt-dlp/ffprobe/ffmpeg calls — no
network access or real binaries required) plus the pure, UI-framework-free
functions extracted from the settings, app-window and converter panels.

---

## The Convert tab

Converts `.webm` files to `.mp3`, extracting the audio stream and discarding
the video. It needs ffmpeg — see **Requirements** above.

- **Add files…** — a multi-select picker filtered to `.webm`. Selected files
  are listed below the button and can be removed one at a time or cleared all
  at once.
- **Output folder** — defaults to your download folder, but it's stored
  separately (`convert_output_dir`). Pointing conversions somewhere else
  deliberately leaves the Download tab's destination alone.
- **Convert to** — the target format. Only `mp3` for now; `mp4`, `png` and
  `jpeg` are planned.
- **Jobs run one at a time, in the order you added them.** Each gets a row in
  the right-hand sidebar with its own progress bar, status and **✕** button.
  A file that fails shows ffmpeg's own error message on its row, and the rest
  of the queue carries on.
- **Name collisions are never resolved silently.** If an output file already
  exists, converting stops and asks: **Auto-number** writes `name (1).mp3`,
  **Overwrite** replaces the existing file. Nothing is written until you pick.
- **Cancelling** stops ffmpeg and deletes the half-written file, so an
  interrupted job never leaves a broken `.mp3` behind.

---

## The library panel

Opened via the 🎬 button in the header. It browses whatever directory is
currently configured as the output folder.

- **Folder tree** (left) — every subfolder under the output directory, built
  fresh each time the panel opens. Click a folder to list the videos in it.
- **Video list** (right) — filename, resolution, and file size for each video
  in the selected folder. Resolution comes from `ffprobe` and shows `—` when
  ffmpeg isn't configured.
- **Search** — typing in the search field filters across *every* folder, not
  just the selected one, matching filenames case-insensitively. Each result
  shows which folder it's in. Clearing the field restores the normal
  per-folder view.
- **New Folder** — creates a real subdirectory under the currently selected
  folder (or the root, if none is selected). The name is sanitized before
  hitting disk; a name collision or invalid location shows an error dialog
  rather than failing silently.
- **List / Thumbnail toggle** — switches between the text-row list and a
  grid of extracted video-frame thumbnails. Thumbnails are generated via
  ffmpeg on a background thread and cached under `~/.tbdc/thumbnails/`; a
  generic placeholder is shown until each one is ready, or permanently if
  ffmpeg isn't available. The chosen mode is remembered between sessions.
- **Opening a video** — double-click a row/card, or use its Open button, to
  launch it in the OS's default player.
- **Right-click a video** for "Open containing folder", "Move to
  subfolder…", and "Copy file path".
- **No delete.** The library panel deliberately does not offer a way to
  delete files — accidental data loss from a video library is considered
  worse than the inconvenience of deleting files through the OS file
  manager instead.
