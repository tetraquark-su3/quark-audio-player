# Quark Audio Player

## Project overview
Desktop audio player for Linux and Windows: PyQt6 for the UI, VLC (via
python-vlc) for playback. Reads local audio files, extracts metadata/album
art with mutagen, and renders six real-time visualisations (spectrum,
spectrogram, oscilloscope, Lissajous, spectral flux, VU meter) from a
background-decoded PCM buffer kept in sync with VLC's playback position.

## Run / build
- Dev: `python main.py` — needs PyQt6, python-vlc, soundfile, mutagen, numpy,
  and VLC installed system-wide. ffmpeg is an optional fallback decoder.
- Windows standalone build: PyInstaller via `quark-player.spec`. That spec
  currently hardcodes Linux paths (`/usr/lib/x86_64-linux-gnu/...`) for the
  VLC binaries — it needs OS-specific paths before it will build on Windows.
- No test suite exists yet. Don't assume one when asked to "verify" something.

## Architecture map
- `main.py` — entry point; single-instance lock (`fcntl`) + localhost socket
  (port 47847) forwards a newly-opened file to the already-running instance.
- `vlc_setup.py` — **must be imported before any `import vlc`**; sets
  `PYTHON_VLC_LIB_PATH` / `PYTHON_VLC_MODULE_PATH` for frozen vs dev builds,
  Windows vs Linux.
- `config/settings.py` — defaults, JSON load/save, `is_audio()`, `derive_color()`.
- `audio/engine.py` — metadata/album-art extraction (mutagen), `SampleLoader`
  (background thread, `soundfile` → `ffmpeg` fallback, both lazily imported),
  FFT helpers (`compute_fft_frame`). Also defines `compute_sync_pos()` (would
  compensate VLC's reported decoder position with `audio_get_delay()` so
  visuals match what's actually audible) — **dead code**, no callers found
  anywhere in the project; see Dead code below.
- `audio/gapless.py` — `GaplessEngine` (sounddevice PCM streaming). **Currently
  dead code**: not imported or instantiated anywhere outside this file.
  Confirm intent (wire it up, or drop it) before changing it.
- `ui/main_window.py` — `MainWindow`, ~1500 lines. Wires VLC (`MediaPlayer` +
  `MediaListPlayer`) to the playlist, visualisations, file browser, settings,
  EQ, and shortcuts. One large class doing both UI construction and playback
  logic — a natural split candidate, not a "just rewrite it" candidate. See
  MainWindow extraction candidates below for concrete split points.
- `ui/playlist.py`, `ui/visualizations.py`, `ui/dialogs.py`, `ui/widgets.py`,
  `ui/icons.py`, `ui/style.py` — self-contained UI pieces.

## Dead code (confirmed via audit, 2026-08-11)
- `audio/gapless.py` — entire file (`GaplessEngine`), zero references outside
  itself.
- `audio/engine.py::compute_sync_pos()` — zero callers anywhere in the
  project, despite a detailed docstring describing VLC audio-delay
  compensation for visualisation sync. Confirm whether this should actually
  be wired into the FFT/visualisation update path, or dropped — don't assume
  it's live just because the docstring reads like it is.
- `ICON_STYLES` (`ui/icons.py`) — imported in `ui/main_window.py` but never
  used there.

## MainWindow extraction candidates
`MainWindow.__init__`/`_build_ui` currently mix VLC wiring, UI construction,
and playback logic in ~340 lines with no separation, making the wiring hard
to test in isolation. Concrete split points identified by audit:
- `PlaybackController` — VLC player/list_player, current track/item, shuffle,
  repeat, EQ apply, progress/end timers.
- `PlaylistIngestionManager` — `_MetadataWorker` + `_add_file(s)`/
  `_add_folder` + `_append_to_media_list`.
- `FileBrowserPanel` — file tree, root combo, mount-point enumeration.
- `IconManager`/`ThemeManager` — `_load_icon`, `_refresh_icons`, `ICON_MAP`,
  `_icon_buttons`.
- `PlaylistPersistence` — load/save/save-as JSON, testable without Qt.
- `AlbumArtPanel` — update/viewer/no-art handling.
- `SettingsController` — `_config`, `_apply_config`, `_apply_shortcuts`,
  settings/EQ dialogs.

## Conventions
- Python 3.10+ type hints throughout; short docstrings on public functions.
- Widgets are styled via `objectName` + the QSS built in `ui/style.py` — don't
  hardcode colors in widget code, read them from `config`.
- Heavy/optional deps (`soundfile`, the `ffmpeg` subprocess call) are imported
  lazily, inside the thread/function that needs them — follow this pattern
  for any new optional dependency.

## Gotchas
- `compute_sync_pos()` is dead code today (see Dead code above). If you wire
  it up, note the VLC audio-delay compensation logic itself is subtle —
  verify manually against real playback, not just that it runs.
- `PlaylistWidget`'s custom `SortableHeader` and `_sort_col`/`_sort_order`
  work around a Qt6 regression in `setSortingEnabled` — don't "simplify" this
  back to the standard Qt sorting API.

## Workflow
- Commit before any multi-file change; prefer small, reviewable diffs over
  large rewrites.
- No CI/lint config yet — if you add one, keep it minimal and note the
  command here.