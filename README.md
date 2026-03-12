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
- **File browser** — built-in tree browser with drive/mount-point selector (Home, Root, removable media)
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
│   ├── engine.py        # Metadata, album art, FFT helpers, SampleLoader
│   └── gapless.py       # GaplessEngine (sounddevice — reserved for future use)
│
├── ui/
│   ├── main_window.py   # MainWindow — wires everything together
│   ├── playlist.py      # PlaylistWidget (O(1) duplicate check, custom sort header)
│   ├── visualizations.py# 6 visualisation widgets
│   ├── dialogs.py       # SettingsDialog, EqualizerDialog
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

---

## Running

```bash
python main.py
# or pass a file directly:
python main.py /path/to/track.flac
```

If an instance is already running, the file is forwarded to it via the single-instance socket (port 47847).

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
`SampleLoader` (in `engine.py`) decodes the current file to a float32 PCM array in a background thread using `soundfile` (with an `ffmpeg` fallback for formats soundfile cannot handle). This array feeds all six visualisation widgets via `compute_fft_frame`.  `soundfile` is imported lazily inside the loader thread — it is never imported at startup.

### Sorting
`PlaylistWidget` uses a custom `SortableHeader` subclass to draw sort arrows (working around a Qt6 regression where `setSortingEnabled(False)` silently resets `sectionsClickable`). Sort state is tracked in `_sort_col` / `_sort_order` instance variables rather than querying the header, for the same reason.
