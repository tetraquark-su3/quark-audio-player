# Quark Audio Player

A desktop audio player built with Python, PyQt6 and VLC.

---

## Features

- **Playback** — Play, pause, stop, next, previous, seek
- **Gapless-style transitions** — VLC `MediaListPlayer` chains tracks automatically (~100–200 ms gap)
- **Shuffle & Repeat** — shuffle rebuilds the VLC media list in random order on every track change
- **Equalizer** — 10-band EQ powered by VLC's DSP, with presets and per-session persistence
- **6 visualisations** — Spectrum, Spectrogram, Oscilloscope, Lissajous, Spectral Flux, VU Meter
- **Playlist** — drag-and-drop reorder, sortable columns (track, artist, album, title, duration), search/filter, undo delete, JSON save/load
- **File browser** — built-in tree browser with drive/mount-point selector (Home, Root, removable media, Windows drive letters)
- **Album art** — embedded art displayed in the control bar; click to view full resolution or save to disk (original bytes preserved, no re-encoding)
- **Theming** — fully configurable colours, font family and size via Settings dialog
- **Keyboard shortcuts** — configurable (play/pause, next, previous, search, undo)
- **Single-instance** — opening a file while the player is already running adds it to the current playlist via a local socket
- **Cross-platform** — Linux and Windows (see Dependencies)

---

## Project structure

```
quark_audio_player/
│
├── main.py              # Entry point, single-instance socket
├── vlc_setup.py         # VLC library path setup (frozen + dev, Linux + Windows)
│
├── config/
│   └── settings.py      # Defaults, load/save config, is_audio, derive_color
│
├── audio/
│   └── engine.py        # Metadata, album art, FFT helpers, SampleLoader
│
├── ui/
│   ├── main_window.py   # MainWindow — wires everything together
│   ├── playlist.py      # PlaylistWidget (O(1) duplicate check, custom sort header)
│   ├── visualizations.py# 6 visualisation widgets
│   ├── dialogs.py       # SettingsDialog, EqualizerDialog
|   |── icons.py         # Style constants, icons definitions and per-style builder,
│   ├── widgets.py       # ClickableSlider, DropArea, ShortcutField
│   └── style.py         # QSS stylesheet builder
│
└── assets/              # PNG icons + app icon
```

---

## Dependencies

### Python packages

```
pip install PyQt6 python-vlc soundfile mutagen numpy
```

### System

| Platform | Requirement |
|----------|-------------|
| Linux    | `sudo apt install vlc` (or equivalent) |
| Windows  | [VLC](https://www.videolan.org/vlc/) installed at `C:\Program Files\VideoLAN\VLC` |

> **Windows note:** `vlc_setup.py` sets `PYTHON_VLC_LIB_PATH` and `PYTHON_VLC_MODULE_PATH` automatically for both dev and PyInstaller-frozen builds.

> **ffmpeg (optional):** used as a fallback decoder for files that `soundfile` cannot read (e.g. non-standard MP3s). Install via `winget install ffmpeg` on Windows or `sudo apt install ffmpeg` on Linux. Without it, visualisations will be silently disabled for those files — playback via VLC is unaffected.

---

## Running

```bash
python main.py
# or pass a file directly:
python main.py /path/to/track.flac
```

If an instance is already running, the file is forwarded to it via the single-instance socket (port 47847).

---

## Building a standalone Windows executable

Requires [PyInstaller](https://pyinstaller.org) and [ffmpeg](https://ffmpeg.org/download.html) installed.

```powershell
$VLC = "C:\Program Files\VideoLAN\VLC"
$FFMPEG = (Get-Command ffmpeg).Source

pyinstaller --onefile --windowed --name quark-player `
  --icon "assets\icon_app.ico" `
  --add-data "assets;assets" `
  --add-binary "$VLC\libvlc.dll;." `
  --add-binary "$VLC\libvlccore.dll;." `
  --add-data "$VLC\plugins;plugins" `
  --add-binary "$FFMPEG;." `
  main.py
```

The resulting `dist\quark-player.exe` is fully self-contained — VLC libraries and ffmpeg are bundled inside. No installation required on the target machine.

> Always delete `build/`, `dist/` and any `.spec` file before rebuilding to avoid stale cache issues:
> ```powershell
> Remove-Item -Recurse -Force build, dist, *.spec
> ```

---

## Building a standalone Linux executable

Requires [Docker](https://docs.docker.com/get-docker/).

```bash
./docker-build.sh
```

This builds a `ubuntu:22.04`-based image (`Dockerfile`, installing VLC,
Python and PyInstaller) and runs PyInstaller against the mounted repo
inside it, writing the result to `dist-linux-docker/quark-player` — not
`dist/`, so it never overwrites a working build. Once you've confirmed
the new binary works, move it into `dist/` yourself.

> Historical note: builds were run on Ubuntu inside Docker rather than
> directly on the host, and specifically on `ubuntu:22.04` — a newer tag
> (`ubuntu:24.04`) was tried first and produced a binary linked against a
> glibc too new to run on at least one user's system (elementaryOS 7,
> itself based on Ubuntu 22.04). `ubuntu:20.04` was tried once around the
> same time but was never adopted for a real build.

> **ffmpeg note:** unlike the Windows build above, this build does not
> bundle its own copy of ffmpeg — bundling it into the binary was tried
> once in this project's history and never adopted for a real release
> build. `SampleLoader._run_ffmpeg` resolves `ffmpeg` via `shutil.which()`
> at runtime, on the machine running the binary, not the one that built
> it — so the fallback decoder is *possible* on this build, not
> *guaranteed* the way it is on Windows: it works as long as the end
> user's machine has ffmpeg installed (see Dependencies), same as running
> `python main.py` directly, but this build doesn't ensure that for them
> the way the Windows build's `--add-binary` does.

---

## Configuration

Settings are stored in `~/.config/quark_audio_player.json`.  
The playlist is persisted separately in `~/.config/quark_audio_player_playlist.json`.

Key settings:

| Key | Default | Description |
|-----|---------|-------------|
| `primary_color` | `#e94560` | Accent / highlight colour |
| `accent_color` | `#a8c0ff` | Secondary accent (labels, header text) |
| `background_color` | `#1a1a2e` | Window background |
| `fps` | `60` | Visualisation frame rate |
| `bar_count` | `64` | Number of spectrum bars |
| `font_family` | `Cantarell` | UI font |
| `font_size` | `13` | UI font size (px) |
| `shortcuts` | see below | Configurable keyboard shortcuts |

Default shortcuts:

| Action | Default |
|--------|---------|
| Play / Pause | `Space` |
| Next track | `→` |
| Previous track | `←` |
| Search | `Ctrl+F` |
| Undo delete | `Ctrl+Z` |

---

## Architecture notes

### Playback engine
`MainWindow` owns both a `vlc.MediaPlayer` (handles EQ, volume, position) and a `vlc.MediaListPlayer` linked to it. When a track is played, `_rebuild_media_list` builds a `vlc.MediaList` from the current playlist row onwards (shuffled if shuffle is on) and hands it to the `MediaListPlayer`. VLC handles all subsequent track transitions internally; the `MediaListPlayerNextItemSet` event signals the UI to update.

### Sample loader
`SampleLoader` (in `engine.py`) decodes the current file to a float32 PCM array in a background thread using `soundfile` (with an `ffmpeg` fallback for formats soundfile cannot handle). This array feeds all six visualisation widgets via `compute_fft_frame`. `soundfile` is imported lazily inside the loader thread — it is never imported at startup.

### Album art
`read_album_art` returns a `(original, display)` tuple. `original` contains the unmodified bytes extracted from the file tags — used when saving to disk. `display` is a shrunk copy (≤ 800 px) used for rendering, so large print-quality covers do not bloat memory.

### Sorting
`PlaylistWidget` uses a custom `SortableHeader` subclass to draw sort arrows (working around a Qt6 regression where `setSortingEnabled(False)` silently resets `sectionsClickable`). Sort state is tracked in `_sort_col` / `_sort_order` instance variables rather than querying the header, for the same reason.
