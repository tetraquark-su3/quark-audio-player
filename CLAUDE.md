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
- Tests: `pytest` (config in `pyproject.toml`, dev deps in
  `requirements-dev.txt` — includes PyQt6, since `ui/icon_manager.py` needs
  it at import time even for its Qt-free tests). Run with `pytest` from the
  repo root; test files live in `tests/`. Coverage is still partial — only
  `ui/playlist_persistence.py`, the pure/static slice of `ui/icon_manager.py`,
  and `ui/file_browser_panel.py::list_mount_roots` are tested. Most of
  `MainWindow` and the rest of the app have no automated tests yet — don't
  assume broader coverage exists when asked to "verify" something.

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
- `ui/main_window.py` — `MainWindow`, ~1360 lines. Wires VLC (`MediaPlayer` +
  `MediaListPlayer`) to the playlist, visualisations, file browser, settings,
  EQ, and shortcuts. One large class doing both UI construction and playback
  logic — a natural split candidate, not a "just rewrite it" candidate. Four
  of the seven split points below have been extracted so far
  (`PlaylistPersistence`, `IconManager`, `AlbumArtPanel`, `FileBrowserPanel`);
  see MainWindow extraction candidates below for the rest.
- `ui/playlist_persistence.py` — JSON load/save/save-as for the playlist
  track list, extracted from `MainWindow`. Pure Python, no Qt/VLC
  dependency; tested in `tests/test_playlist_persistence.py`.
- `ui/icon_manager.py` — button icon generation/theming (SVG→`QPixmap`→
  `QIcon`, PNG fallback, primary/accent toggle-color swap), extracted from
  `MainWindow`. Unlike `playlist_persistence.py` it has real Qt surface
  (constructs `QIcon`, calls `QPushButton.setIcon`), so only its pure helper
  (`toggle_icon_colors`) and `ICON_MAP`'s static structure are unit-tested
  (`tests/test_icon_manager.py`) — the Qt-touching methods need a live
  `QApplication`, which this project doesn't bootstrap in tests.
- `ui/album_art_panel.py` — `AlbumArtPanel(QLabel)`: album art display,
  themed "no art" placeholder, and the save/viewer dialog, extracted from
  `MainWindow`. Self-contained widget (built and added to the layout by
  `MainWindow`, not a manager operating on an externally-owned label like
  `IconManager`) — its own `_pixmap`/`_art_bytes` state, `has_art` property
  read by `MainWindow._apply_config` instead of the old direct attribute
  poke. Entirely Qt-bound (`QPixmap`, a modal `QDialog.exec()`,
  `QFileDialog`); no pure-Python logic worth isolating the way
  `toggle_icon_colors` was for `IconManager`, so it ships with zero unit
  tests — same `QApplication` gap as `icon_manager.py`.
- `ui/file_browser_panel.py` — `FileBrowserPanel(QWidget)`: root selector
  (Home / Windows drives / Linux mount points) + `QFileSystemModel`-backed
  `QTreeView`, extracted from `MainWindow`. Self-contained widget added
  directly to `h_splitter`, same shape as `AlbumArtPanel`. Talks to
  `MainWindow` via signals (`add_file_requested`, `add_files_requested`,
  `add_folder_requested`), not constructor-injected callbacks — deliberate:
  those signals are today connected to `MainWindow._add_file(s)`/
  `_add_folder`, which are themselves the documented scope of the
  not-yet-done `PlaylistIngestionManager` extraction below; re-pointing a
  signal connection when that lands is a one-line change in `MainWindow`,
  vs. re-threading a callback reference through the panel. `Key_Return`
  handling (add selected files/folders) moved from
  `MainWindow.keyPressEvent`'s `self._file_tree.hasFocus()` check into the
  panel's own `keyPressEvent` override (`self._tree.hasFocus()`, identical
  condition) — `MainWindow` no longer needs to know the tree exists.
  `list_mount_roots()` (Windows drive letters / Linux mount points under
  `/run/media/$USER` and `/mnt`) was pulled out of the `QComboBox`-population
  loop into a Qt-free function and is unit-tested
  (`tests/test_file_browser_panel.py`) — the one piece of real logic here
  that isn't Qt-bound. Everything else (tree/model wiring, context menu,
  keyPressEvent) needs a live `QApplication`, same gap as `icon_manager.py`/
  `album_art_panel.py`. One small intentional behavior change: the
  immediate "Added: <filename>" status message (previously shown only on
  double-click) now also fires for a single-file `Key_Return` add, via the
  `add_file_requested` → `MainWindow._on_browser_add_file` wrapper — folded
  the two call sites together rather than adding a fourth signal just to
  keep that message double-click-only.
- `ui/playlist.py`, `ui/visualizations.py`, `ui/dialogs.py`, `ui/widgets.py`,
  `ui/icons.py`, `ui/style.py` — self-contained UI pieces.

## Dead code (confirmed via audit, 2026-08-11)
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
to test in isolation. Concrete split points identified by audit. Progress:
4 of 7 done, 3 remaining.

Done:
- `PlaylistPersistence` (`ui/playlist_persistence.py`) — load/save/save-as
  JSON, testable without Qt. Extracted, tested, pushed.
- `IconManager` (`ui/icon_manager.py`) — `_load_icon`, `_refresh_icons`,
  `ICON_MAP`, `_icon_buttons`. Extracted, tested, pushed. Scope note: this
  covers only the "Icon" half of the originally-named "IconManager/
  ThemeManager" candidate — the broader theming still in
  `MainWindow._apply_config` (QSS regen via `build_stylesheet`,
  splitter-handle colors via `derive_color`, visualization `set_colors`,
  playlist accent color) is untouched; a separate future "ThemeManager"
  extraction would cover that if ever done.
- `AlbumArtPanel` (`ui/album_art_panel.py`) — `_update_album_art`,
  `_open_art_viewer`, `_show_no_art`, plus the `_full_art_pixmap`/
  `_full_art_bytes` state they mutated. Extracted as a self-contained
  `QLabel` subclass MainWindow instantiates and adds to its layout
  (`self._album_art = AlbumArtPanel()`), not a manager bolted onto an
  externally-built label. No unit tests — no pure-Python logic in it worth
  isolating, unlike `IconManager`'s `toggle_icon_colors`; see the
  Architecture map entry above for detail.
- `FileBrowserPanel` (`ui/file_browser_panel.py`) — `_change_root`,
  `_on_file_double_click`, `_on_file_context_menu`, the mount-point
  enumeration, and the `Key_Return` branch formerly in
  `MainWindow.keyPressEvent`. Extracted as a self-contained `QWidget`
  added directly to `h_splitter`. Talks back to `MainWindow` via signals,
  not callbacks — see the Architecture map entry above for the full
  rationale (chosen so re-pointing to `PlaylistIngestionManager` later is a
  one-line reconnect). `list_mount_roots()` is genuinely unit-tested
  (Qt-free); the rest ships without tests like `IconManager`/`AlbumArtPanel`.

Remaining (next step, in no particular order):
- `PlaybackController` — VLC player/list_player, current track/item, shuffle,
  repeat, EQ apply, progress/end timers.
- `PlaylistIngestionManager` — `_MetadataWorker` + `_add_file(s)`/
  `_add_folder` + `_append_to_media_list`.
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
- `MainWindow._media_list_lock` guards only the read/swap of the
  `self._media_list` *reference* in `_append_to_media_list()` /
  `_rebuild_media_list()` — never the blocking VLC-level
  `media_list.lock()/add_media()/unlock()` call. Don't widen it to cover
  that VLC call "for extra safety": VLC's own lock can block for a while
  during playback, and `_rebuild_media_list()` runs on the Qt thread, so
  holding `_media_list_lock` around the VLC call would let a slow VLC lock
  freeze the UI — exactly what the background-thread design in
  `_append_to_media_list()` exists to avoid. The residual race (a rebuild
  landing between the read and the VLC add completing) is handled instead
  by `_on_media_appended()` detecting the mismatch and retrying.
- `MainWindow._toggle_mute` captures the pre-mute volume from
  `self._volume.value()` (the Qt slider), not `self._player.audio_get_volume()`
  (VLC). VLC's `audio_get_volume()` can return `-1` when no audio output is
  active yet (e.g. no media loaded/played), and even when it doesn't, its
  value isn't guaranteed to exactly match what was last set. The slider is
  the actual source of truth for "what volume the user wants" — VLC is just
  downstream of it via `_on_volume_changed`. Don't "simplify" this back to
  reading VLC; that round-trip is what caused the mute/unmute-resets-to-0%
  bug fixed in this history.

## Workflow
- Commit before any multi-file change; prefer small, reviewable diffs over
  large rewrites.
- No CI/lint config yet — if you add one, keep it minimal and note the
  command here.