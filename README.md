# The Big Downloader & Converter (TBD&C)

A desktop app for downloading videos with **yt-dlp** and browsing the resulting
library, built with **CustomTkinter**. Any site yt-dlp supports works, not
just YouTube.

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
    placeholder instead of real values.

  If ffmpeg is on your system `PATH`, it's picked up automatically. Otherwise,
  set a path in **Settings → ffmpeg path** — it can point either at the
  directory containing the `ffmpeg`/`ffprobe` binaries or directly at the
  `ffmpeg` binary itself (see `core/downloader.py:44-65` for the exact
  resolution logic; `core/library_manager.py` mirrors it for `ffprobe`).

## Running the app

```bash
cd "VideoDownloader&Conversion App"
python main.py
```

## Configuration

Runtime settings (output directory, last-used quality, ffmpeg path, loudness
normalization, cookie file, library view mode) are persisted to:

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
│   ├── config_manager.py     # Reads/writes ~/.tbdc/config.json
│   ├── library_manager.py    # Folder tree, video listing/search, ffprobe
│   │                         #   metadata, ffmpeg thumbnails, move/create
│   └── utils.py               # Filename sanitization, conflict/uniqueness
├── ui/
│   ├── app_window.py          # Root window; wires every panel together
│   ├── url_panel.py            # URL input, format fetch, preview card
│   ├── quality_panel.py        # Quality picker, output folder + subfolder
│   ├── download_panel.py       # Queue sidebar (progress, pause, reorder)
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
functions extracted from the settings panel.

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
