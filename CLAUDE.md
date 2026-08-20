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
- Linux standalone build: `Dockerfile` + `docker-build.sh`, reproducing a
  pipeline that previously existed only as shell history and a gitignored
  `building on ubuntu.txt` (recovered from the initial commit — see git
  history). Runs PyInstaller inside a `ubuntu:22.04` container (chosen
  over `24.04` for a glibc-compatibility failure on elementaryOS 7;
  `20.04` was tried once but never adopted). Does not bundle ffmpeg — see
  README's "Building a standalone Linux executable" for why. Output goes
  to `dist-linux-docker/`, never `dist/`, so it can't silently replace the
  binary currently in daily use.

  Verified end to end (2026-08-20): `./docker-build.sh` completed
  successfully and the resulting `dist-linux-docker/quark-player` was
  launched directly (not `python main.py`) and driven live under a real
  `DISPLAY`. Confirmed working: window opens without crashing; VLC finds
  its bundled plugins and plays a real file through to the end (this is
  the specific thing the `vlc/plugins` vs `vlc` destination fix, see
  `docker-build.sh`'s comment, was for); SVG icons render (Pillow bundled
  correctly); the Equalizer dialog opens and its presets apply. Getting
  here needed two apt packages the original shell-history pipeline never
  installed explicitly — `binutils` (PyInstaller needs `objdump` on
  Linux) and `libpython3.10` (PyInstaller links against
  `libpython3.10.so.1.0`) — both now declared in `Dockerfile` with
  comments explaining why. Also needed `--specpath build-linux-docker`
  in `docker-build.sh`, so the generated `.spec` doesn't collide with the
  pre-existing root-owned `quark-player.spec` in the repo root now that
  the container runs as the invoking user (`--user`) instead of root.
- Tests: `pytest` (config in `pyproject.toml`, dev deps in
  `requirements-dev.txt` — includes PyQt6, since `ui/icon_manager.py` needs
  it at import time even for its Qt-free tests). Run with `pytest` from the
  repo root; test files live in `tests/`. Coverage is still partial — only
  `ui/playlist_persistence.py`, the pure/static slice of `ui/icon_manager.py`,
  and `ui/file_browser_panel.py::list_mount_roots` are tested. Most of
  `MainWindow` and the rest of the app have no automated tests yet — don't
  assume broader coverage exists when asked to "verify" something.

## Architecture map
- `main.py` — entry point; single-instance detection is platform-split.
  POSIX: an `fcntl` file lock (`_acquire_lock()`), atomic and race-free.
  Windows (no `fcntl`): `_acquire_lock()` is a no-op there (always returns
  `_WINDOWS_LOCK_SENTINEL`, never `None`), so `main()` instead probes the
  localhost socket directly via `_try_send_to_existing()` *before* even
  calling `_acquire_lock()` — see Gotchas below for the known race this
  leaves open on Windows. The same socket (port 47847) is also how a
  newly-opened file gets forwarded to an already-running instance, on
  every platform.
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
- `ui/main_window.py` — `MainWindow`, ~900 lines. Wires VLC playback (now
  via `PlaybackController`) to the playlist, visualisations, file browser,
  settings, EQ, and shortcuts. One large class doing both UI construction
  and playback logic — a natural split candidate, not a "just rewrite it"
  candidate. All seven split points identified by the initial audit have
  now been extracted (`PlaylistPersistence`, `IconManager`, `AlbumArtPanel`,
  `FileBrowserPanel`, `SettingsController`, `PlaybackController`,
  `PlaylistIngestionManager`) — see MainWindow extraction candidates below
  for the full history.
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
- `ui/settings_controller.py` — `SettingsController`: thin wrapper around
  `SettingsDialog`/`EqualizerDialog` (`ui/dialogs.py`), extracted from
  `MainWindow._open_settings`/`_open_equalizer`. Deliberately narrow: two
  methods (`open_settings_dialog`, `open_equalizer_dialog`), no constructor
  state — config/eq_state/player/parent are passed in fresh each call, kept
  as a class only for naming consistency with `IconManager`/
  `FileBrowserPanel`. `self._config` stays owned by `MainWindow`, which
  also keeps `_apply_config`/`_apply_shortcuts` (both orchestrate objects
  `MainWindow` already owns — viz widgets, splitters, playlist, IconManager,
  its own `QShortcut`s — the same reasoning `IconManager`'s scope note
  already applies to the broader theming). This narrows the extraction from
  what the one-line candidate description below used to imply (it listed
  `_config`/`_apply_config`/`_apply_shortcuts` as in scope) — noted
  explicitly rather than silently dropped. No `config_changed` signal
  either, unlike `FileBrowserPanel`'s signal choice: both dialogs are opened
  through a synchronous, blocking `QDialog.exec()` call made directly by
  `MainWindow` on its own button click, with exactly one known caller, so a
  plain return value is the better fit — signals earn their keep when a
  widget detects a user action itself and doesn't know who consumes it,
  which isn't the case here. `_apply_eq()` (re-attaching the saved EQ to
  the VLC player on track change) isn't here either — it moved into
  `PlaybackController` (see below) once that extraction landed, not into
  this one.
  Zero unit tests, same reasoning as `IconManager`/`AlbumArtPanel`: both
  methods just build a `QDialog`, call `.exec()`, and read back a property
  — no pure logic to isolate.
- `ui/playback_controller.py` — `PlaybackController`, ~640 lines. Owns the
  VLC `Instance`/`MediaPlayer`/`MediaListPlayer`/`MediaList` + its lock,
  current track/item, shuffle/repeat/`_shuffle_order`, the progress/
  end-of-playlist-grace timers, `_rebuild_media_list`, `_apply_eq`, the
  `MediaListPlayerNextItemSet`/`MediaPlayerEncounteredError` VLC event
  handlers, and play/pause/stop/next/prev/shuffle/repeat/seek — extracted
  from `MainWindow`. Full rationale and scope notes under "MainWindow
  extraction candidates" below.
- `ui/playlist_ingestion_manager.py` — `PlaylistIngestionManager`. Owns
  `_MetadataWorker` (background `QThread` reading metadata via
  `audio.engine.read_metadata`), the dedup+enqueue methods
  (`add_file`/`add_files`/`add_folder`, public renames of the former
  `_add_file`/`_add_files`/`_add_folder`), `_pending_play`, and
  `_on_track_ready` — extracted from `MainWindow`. Full rationale and scope
  notes under "MainWindow extraction candidates" below.
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
`MainWindow.__init__`/`_build_ui` used to mix VLC wiring, UI construction,
and playback logic in ~340 lines with no separation, making the wiring hard
to test in isolation. Concrete split points identified by audit. Progress:
7 of 7 done — nothing left of the original audit's scope.

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
- `SettingsController` (`ui/settings_controller.py`) — narrower than the
  one-liner below implied: only `_open_settings`/`_open_equalizer`'s
  dialog-opening plumbing moved. `_config`, `_apply_config`, and
  `_apply_shortcuts` were deliberately left in `MainWindow` — see the
  Architecture map entry above for the full rationale (both orchestrate
  objects `MainWindow` already owns; no `config_changed` signal either,
  since both call sites are synchronous with one known caller). No unit
  tests — no pure logic to isolate, same as `IconManager`/`AlbumArtPanel`.
- `PlaybackController` (`ui/playback_controller.py`) — VLC
  `Instance`/`MediaPlayer`/`MediaListPlayer`/`MediaList` + its lock,
  current track/item, shuffle/repeat/`_shuffle_order`, the progress/
  end-of-playlist-grace timers, `_rebuild_media_list`, `_apply_eq`, and
  play/pause/stop/next/prev/shuffle/repeat/seek. Broader than the one-line
  candidate this replaced implied, in one respect: it also picked up
  `_on_vlc_error` (the `MediaPlayerEncounteredError` event handler),
  surfaced during the extraction audit as the same kind of VLC-event
  wiring as the next-item handler even though the original candidate
  line didn't name it — same "scope deviated from the one-liner" pattern
  already noted for `SettingsController` above.

  Two-phase construction (`__init__` / `bind()`), unlike every other
  extracted class here (which take everything they need in one
  constructor call): `MainWindow.__init__` constructs `PlaybackController`
  early — at roughly the same point the raw VLC setup used to live —
  because `MainWindow._build_ui()`'s initial `audio_set_volume(80)` call
  needs `self._playback.player` to already exist. But the playlist widget,
  progress slider, and time label it also needs aren't built until
  `_build_ui()` creates them. So `__init__` takes only `eq_state_provider`
  and the UI-update callables (`set_play_icon`, `update_ui_for_track`,
  `reset_visualizations`, `status_message`, `timer_fft_start/stop`,
  `loader_load` — all bound `MainWindow` methods or lambdas, safe to hand
  over before their underlying widgets exist since none of them run until
  real playback happens), and `bind(playlist, progress_slider, time_label)`
  is called exactly once, as the very last statement in `_build_ui()`.
  Nothing between construction and that `bind()` call can reach a playback
  method — no auto-play on construction, and a command-line/socket file
  open can only reach `MainWindow` after `__init__` has fully returned —
  so no runtime guard was added for calling a playback method pre-`bind()`;
  see the module docstring's "Two-phase construction" section for the full
  argument.

  `_timer_fft`/`_update_fft` deliberately did **not** move here alongside
  `_timer_progress`/`_timer_end_grace`, even though all three are started/
  stopped at the same call sites: `_update_fft` only pushes decoded PCM
  samples to the six visualisation widgets and reads `SampleLoader`, both
  MainWindow-owned, with no VLC-state logic of its own — same "orchestrates
  objects MainWindow already owns" reasoning `_apply_config`/
  `_apply_shortcuts` got under `SettingsController` above.
  `PlaybackController` reaches it only through injected
  `timer_fft_start`/`timer_fft_stop` callables, invoked at the exact points
  `self._timer_fft.start()`/`.stop()` used to be called inline.

  Public interface deliberately kept narrow, designed with
  `PlaylistIngestionManager` (extraction 7/7, the one remaining) in mind:
  `current_track` (read-only property) and `append_to_active_list(path)`
  (public rename of the former `_append_to_media_list`) are the only two
  things that extraction will need. `_media_list_lock`/`_media_list` stay
  private and are never exposed as attributes — `append_to_active_list`
  fully encapsulates the lock+add+retry+shuffle-fallback protocol
  internally. When `PlaylistIngestionManager` is extracted,
  `MainWindow._on_track_ready`'s `self._playback.append_to_active_list(path)`
  call (guarded by `self._playback.current_track is not None`) becomes that
  extraction's to make instead — a one-line reconnect, not a new interface
  to design.

  `_rebuild_media_list`, `append_to_active_list`, and `_on_media_appended`
  — the race-condition protocol documented in Gotchas below (the
  `_media_list_lock` one) — were moved **verbatim**, docstrings and inline
  comments included, not rewritten, restructured, or "improved" beyond the
  mechanical rename of `_append_to_media_list` to the public
  `append_to_active_list`.

  No unit tests — same `QApplication`/live-VLC-instance gap as
  `IconManager`/`AlbumArtPanel`/`FileBrowserPanel`/`SettingsController`.
  Verified instead by scripting a real run of the app (`QTest.mouseClick`
  against the live widgets under a real `DISPLAY`, real audio files),
  exercising play/pause/stop, next/prev, shuffle/repeat, progress
  advancing, seek-by-click, volume + mute/unmute (the fix in Gotchas
  below), the equalizer dialog, and a live drag-and-drop-equivalent append
  during playback — no regressions found.

- `PlaylistIngestionManager` (`ui/playlist_ingestion_manager.py`) — the
  last of the seven, closing out the audit. Owns `_MetadataWorker` (moved
  verbatim, including its docstring on the stop()/wait() shutdown
  trade-off), the dedup+enqueue methods (`add_file`/`add_files`/
  `add_folder`, public renames of the former `_add_file`/`_add_files`/
  `_add_folder`), `_pending_play`, `_on_track_ready` (the worker's
  `track_ready` consumer, including the branch that appends live to
  `PlaybackController`'s VLC media list when a track is already playing),
  and `_natural_key` (module-level, used only by `add_folder`).

  First extraction to depend on another already-extracted class rather
  than only on `MainWindow`: `playback: PlaybackController` is held as a
  direct reference (constructor argument, never reassigned), not wrapped
  in per-operation callables — `PlaybackController`'s own module docstring
  had already designed `current_track`/`append_to_active_list` as "the
  interface for the not-yet-extracted `PlaylistIngestionManager`", and the
  callback-avoidance reasoning that keeps `PlaybackController` from
  depending on `MainWindow` doesn't apply between two sibling extracted
  classes with no circular-import risk — same shape of dependency
  `PlaybackController` itself has on `self._playlist`. `playlist:
  PlaylistWidget` gets the same direct-reference treatment, for
  `item_by_path`/`add_track`. `status_message` stays a callback (mirrors
  `PlaybackController`'s own `status_message`) — this class has no
  business holding a live `QMainWindow` reference just to call
  `statusBar().showMessage()` occasionally.

  Two-phase construction (`__init__`/`bind()`), same shape as
  `PlaybackController`'s: `MainWindow.__init__` constructs this class at
  the exact point it used to construct `_MetadataWorker` directly —
  before `_build_ui()` creates `self._playlist` — so the worker thread
  starts at the same relative moment in startup as before, preserving that
  detail of behaviour rather than changing it as a side effect of the
  extraction. `bind(playlist)` is called once, in the same end-of-
  `_build_ui()` block as `self._playback.bind(...)`; order between the two
  `bind()` calls doesn't matter (neither depends on the other).

  `MainWindow.closeEvent()` calls this class's `shutdown()` *before*
  `PlaybackController.shutdown()`, reversing the pre-extraction order
  (`_playback.shutdown()` first, `_meta_worker.stop()`/`wait()` last) —
  while the worker is still draining, a late `track_ready` could still
  call `self._playback.append_to_active_list()` via `_on_track_ready`'s
  `current_track` branch, and `PlaybackController.shutdown()` doesn't
  clear `current_track`, so that guard stays open even after VLC has been
  told to stop. Verified empirically, not just reasoned about: dropping a
  file and closing the window in the same synchronous callback (no delay)
  across 4/4 scripted runs never saved the file into
  `~/.config/quark_audio_player_playlist.json`, regardless of shutdown
  order — a control run with a real 300ms delay between drop and close did
  save it. The actual mechanism: `_MetadataWorker.track_ready` is a queued
  cross-thread signal only delivered when the main thread's Qt event loop
  next runs, and `QThread.wait()` blocks the calling thread without
  pumping that loop — so `_on_track_ready` can't run between the drop and
  `_save_playlist()` when there's no event-loop turn in between, no matter
  which `shutdown()` goes first. So: this reordering protects the
  `append_to_active_list`-on-a-stopped-`PlaybackController` case for a
  track genuinely mid-playback; it does not make a last-second drop
  survive into the saved playlist, and shouldn't be described that way
  again.

  `FileBrowserPanel`'s `add_files_requested`/`add_folder_requested`
  signals retarget to `self._ingestion.add_files`/
  `self._ingestion.add_folder` — literally the one-line reconnect its own
  docstring promised. `add_file_requested` stays connected to
  `MainWindow._on_browser_add_file`, which stays in `MainWindow` (only its
  body's call target changes, from `self._add_file(path)` to
  `self._ingestion.add_file(path)`) — it mixes ingestion with a
  `MainWindow`-owned side effect (the status bar message), same
  "orchestrates things `MainWindow` already owns" reasoning that kept
  `_apply_config`/`_apply_shortcuts` out of `SettingsController`.
  `_open_from_socket` and `_on_drop` stay in `MainWindow` for the same
  reason (window raise/activate, drag-and-drop event handling) and now
  call `self._ingestion.add_file`/`add_folder` instead of the old private
  methods. `FileBrowserPanel` itself was not given a reference to
  `PlaylistIngestionManager` — it stays a leaf widget that emits signals
  without knowing who listens, consistent with the signal-not-callback
  choice already justified in its own docstring.

  Testability lands close to zero, same category as `IconManager`/
  `AlbumArtPanel`/`SettingsController`/most of `PlaybackController`.
  `_MetadataWorker` is a real `QThread` doing real file I/O and emitting a
  real Qt signal — not unit-testable without a live thread and event loop.
  `add_file`/`add_files`/`_on_track_ready` all need a live `PlaylistWidget`
  for `item_by_path`/`add_track`. `_natural_key` (module-level, genuinely
  Qt-free) is the one candidate that's actually pure — the same shape of
  candidate `list_mount_roots()` was for `FileBrowserPanel` — but unlike
  `list_mount_roots()`, which had real OS-conditional branching (Windows
  drive letters vs. Linux mount points under specific paths) worth a
  regression net, `_natural_key` is a one-line sort-key function (regex
  split + int/lower) with a single call site and no branching of its own;
  deliberately not given a dedicated test module, the effort/value ratio
  too low to justify it as its own precedent. Verified instead by live,
  script-driven runs exercising the real `dropEvent()` → `_on_drop()` →
  `self._ingestion.add_file()` → `_MetadataWorker` path end to end (the
  same runs used for the shutdown-order finding above).

All seven MainWindow extraction candidates identified by the initial audit
are now done — `MainWindow` no longer contains any of the responsibilities
that audit flagged.

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
- `PlaybackController._media_list_lock` (moved from `MainWindow` — see
  the `PlaybackController` entry above) guards only the read/swap of the
  `self._media_list` *reference* in `append_to_active_list()` /
  `_rebuild_media_list()` — never the blocking VLC-level
  `media_list.lock()/add_media()/unlock()` call. Don't widen it to cover
  that VLC call "for extra safety": VLC's own lock can block for a while
  during playback, and `_rebuild_media_list()` runs on the Qt thread, so
  holding `_media_list_lock` around the VLC call would let a slow VLC lock
  freeze the UI — exactly what the background-thread design in
  `append_to_active_list()` exists to avoid. The residual race (a rebuild
  landing between the read and the VLC add completing) is handled instead
  by `_on_media_appended()` detecting the mismatch and retrying.
- `MainWindow._toggle_mute` captures the pre-mute volume from
  `self._volume.value()` (the Qt slider), not
  `self._playback.player.audio_get_volume()` (VLC — reached through
  `PlaybackController`'s public `player` property since the extraction).
  VLC's `audio_get_volume()` can return `-1` when no audio output is
  active yet (e.g. no media loaded/played), and even when it doesn't, its
  value isn't guaranteed to exactly match what was last set. The slider is
  the actual source of truth for "what volume the user wants" — VLC is just
  downstream of it via `_on_volume_changed`. Don't "simplify" this back to
  reading VLC; that round-trip is what caused the mute/unmute-resets-to-0%
  bug fixed in this history.
- `MainWindow.closeEvent()` calling `PlaylistIngestionManager.shutdown()`
  before `PlaybackController.shutdown()` does **not** make a file dropped
  right before closing survive into the saved playlist — an earlier guess
  in this history that it would was checked empirically and found false.
  See the `PlaylistIngestionManager` entry under "MainWindow extraction
  candidates" for the full empirical result and the actual mechanism
  (event-loop opportunity, not shutdown order).
- Single-instance detection on Windows has no equivalent to POSIX's
  atomic `flock()`. `main.py::_acquire_lock()` always returns a no-op
  sentinel there (`_WINDOWS_LOCK_SENTINEL`, never `None` — it can't tell
  you another instance exists), so `main()` instead probes the localhost
  socket via `_try_send_to_existing()` *before* `_acquire_lock()` is even
  called on Windows. This is a **known, accepted limitation, not a
  silently-fixed one**: two near-simultaneous launches on Windows can
  both reach that probe before either has bound its listener socket in
  `_start_listener()`, both conclude "nobody's home", and both proceed to
  start a full instance — a race POSIX's atomic file lock closes
  instantaneously and Windows currently doesn't. Deliberately left this
  way rather than implementing a native Windows lock (e.g.
  `msvcrt.locking`) untested, with no Windows machine available to verify
  it. Side effect worth knowing before "fixing" the symptom: reusing
  `_try_send_to_existing()` as the Windows probe means every normal
  Windows launch (no other instance running) also eats that function's
  full retry budget (up to 8 attempts, ~1.75s worst case) before falling
  through to start normally — that loop was tuned for "we already know an
  instance exists, keep retrying while its listener thread finishes
  starting", not for a fast "is anyone home" check.
- `libvlc_audio_equalizer_new()` returns `NULL` on a VLC install without
  audio-equalizer support. `ui/dialogs.py::EqualizerDialog.__init__`
  already guarded this; two other sites needed the same guard and are now
  fixed — an ultrareview report (`merged_bug_002`) had bundled both under
  one number, but they're independent bugs at independent call sites with
  independent fixes, not one bug with two symptoms:
    - `PlaybackController._apply_eq()` (`ui/playback_controller.py`) ran
      on every track change and unconditionally passed the `NULL` result
      to `set_preamp`/`set_amp_at_index`/`set_equalizer`, crashing the
      first time a track played with a saved (non-empty) `eq_state` on
      such an install. Fixed to skip attaching an equalizer and continue
      playback normally (flat, no EQ) when `NULL`. Since this runs
      silently on every track, a popup per track would be intrusive — it
      instead warns exactly once per app run via `status_message`
      (`self._eq_unavailable_warned`), then stays silent for the rest of
      the session. `_apply_eq()` only ever *reads* `eq_state` (via
      `eq_state_provider()`, injected read-only) — it was never the site
      that could corrupt the saved value; confirmed by inspection, not
      assumed.
    - `EqualizerDialog.eq_state` (`ui/dialogs.py`) — the actual site that
      could corrupt a saved `eq_state`: on the same `NULL` condition,
      `__init__` disables the sliders and returns before the block that
      restores the saved state into them, so they sit at their
      construction-time 0 default. The property used to read those
      sliders unconditionally, so opening and closing the EQ dialog once
      on such an install — even without touching anything — silently
      overwrote a previously-saved `eq_state` with zeros, via
      `MainWindow._open_equalizer`'s unconditional
      `self._config["eq_state"] = ...` + `save_config()`. Fixed to return
      `self._eq_state` (the state the dialog was opened with) unchanged
      when `self._equalizer is None`, instead of reading the sliders —
      consistent with `open_equalizer_dialog()`'s documented contract
      ("persists whatever was last heard"): nothing could be heard or
      changed when the controls were disabled, so the old state is what
      "last heard" means here.
  Verified with `vlc.libvlc_audio_equalizer_new` mocked to return `None`
  (no Windows/EQ-less-VLC machine available to trigger the real
  condition) — `_apply_eq()` warns exactly once across 3 calls and never
  crashes; `EqualizerDialog.eq_state` returns the original saved dict
  unchanged; both confirmed *not* regressed on the normal (real
  equalizer) path with separate control runs.
- `PlaybackController._on_vlc_error` (`MediaPlayerEncounteredError`
  handler) used to be attached directly to libvlc's event manager
  (`event_attach(..., self._on_vlc_error)`) instead of being wired through
  a `pyqtSignal` like its sibling `_on_vlc_next_item`
  (`MediaListPlayerNextItemSet`). libvlc invokes registered callbacks on
  its own internal C thread, not the Qt thread — so the handler's body
  (`status_message` → `QStatusBar.showMessage`, `_set_play_icon` →
  `QPushButton.setIcon`, `_timer_progress.stop()`/`_timer_fft_stop()` →
  `QTimer.stop()`) ran off the Qt thread. `QTimer.stop()` from another
  thread is explicitly unsupported by Qt (`QObject::killTimer: Timers
  cannot be stopped from another thread`); the `QWidget` calls are
  unsupported too. Fixed to match `_on_vlc_next_item`'s pattern exactly —
  not a new approach: added `_vlc_error_signal = pyqtSignal()`, changed
  the `event_attach` callback to `lambda _e: self._vlc_error_signal.emit()`
  (the only thing now running on libvlc's thread — safe, since `.emit()`
  across threads just queues delivery), connected
  `self._vlc_error_signal.connect(self._on_vlc_error)` in `__init__`
  alongside the existing `_vlc_next_item_signal` connect, and marked
  `_on_vlc_error` `@pyqtSlot()`. `_on_vlc_error`'s body is untouched; its
  signature dropped an `_event` parameter that was never read (confirmed
  by grep — no direct `self._on_vlc_error(...)` call exists anywhere in
  the repo, only the `.connect()` reference, so nothing depended on the
  old signature). Verified by emitting `_vlc_error_signal` from a real
  background Python thread and confirming all three side effects ran with
  `QThread.currentThread()` equal to the Qt main thread.

  This was an ultrareview finding (`bug_017`), but it wasn't a fresh
  discovery — the `PlaybackController` extraction's own audit had already
  flagged `_on_vlc_error` as "the same kind of VLC-event wiring as the
  next-item handler" (see its "Done" entry above) while moving it
  verbatim. The audit noticed the *category* match but not that the two
  handlers' actual wiring differed — `_on_vlc_next_item` was signal-based
  and thread-safe, `_on_vlc_error` was a direct callback and wasn't. The
  bug was real and pre-existing (moved as-is from `MainWindow`, not
  introduced by the extraction), just not connected to its own "same kind
  of wiring" observation at the time.

## Workflow
- Commit before any multi-file change; prefer small, reviewable diffs over
  large rewrites.
- No CI/lint config yet — if you add one, keep it minimal and note the
  command here.