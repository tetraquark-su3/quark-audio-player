"""
ui/playlist_ingestion_manager.py
PlaylistIngestionManager: owns the background metadata-reading worker and
the file/files/folder ingestion methods (dedup, enqueue, live-append when
playback is active) — extracted from MainWindow (see CLAUDE.md,
"PlaylistIngestionManager" extraction, 7/7 — the last of the seven
candidates identified by the initial MainWindow-extraction audit).

Ownership boundary
-------------------
_MetadataWorker (background QThread reading audio metadata via
audio.engine.read_metadata, moved verbatim including its docstring on the
stop()/wait() shutdown trade-off — see Strictly preserved below), the
dedup+enqueue methods (add_file/add_files/add_folder, the public renames of
the former _add_file/_add_files/_add_folder), _pending_play, and
_on_track_ready (the worker's track_ready consumer, which both adds the
finished track to the playlist and — when a track is already playing —
appends it live to PlaybackController's VLC media list).

Deliberately NOT moved: _on_browser_add_file, _open_from_socket, _on_drop.
All three mix ingestion with a MainWindow-owned side effect (status bar
message, window raise/activate, drag-and-drop event handling) — same
"orchestrates things MainWindow already owns" reasoning that kept
_apply_config/_apply_shortcuts out of SettingsController. They stay in
MainWindow and call into this class's public add_file/add_files/add_folder.

Two direct-reference dependencies, not callbacks
--------------------------------------------------
playback: PlaybackController — held as a direct reference (constructor
argument, never reassigned), not wrapped in per-operation callables. This
is the first extraction to depend on another already-extracted class
rather than only on MainWindow: PlaybackController's own module docstring
("Interface for the not-yet-extracted PlaylistIngestionManager") already
designed current_track and append_to_active_list as a small, stable public
surface for exactly this dependency, and the callback-avoidance reasoning
that keeps PlaybackController from depending on MainWindow doesn't apply
between two sibling extracted classes with no risk of a circular import.
Same shape of dependency PlaybackController itself has on self._playlist.

playlist: PlaylistWidget — same direct-reference treatment, for
item_by_path (dedup, and the pending-play lookup in _on_track_ready) and
add_track (in _on_track_ready).

status_message: Callable[[str], None] — kept as a callback, not a direct
QMainWindow reference, same pattern PlaybackController itself uses: this
class has no business holding a live QMainWindow reference just to call
statusBar().showMessage() occasionally.

Two-phase construction (__init__ / bind()), same reason as PlaybackController
------------------------------------------------------------------------------
MainWindow.__init__ constructs this class at the same point it used to
construct _MetadataWorker directly — before _build_ui() creates
self._playlist — because starting the metadata-reading thread at that
exact point in startup is itself part of the behaviour being preserved
verbatim (see Strictly preserved below). __init__ takes playback and
status_message (both already available at that point) and starts the
worker thread; bind(playlist) is called once, in the same end-of-
_build_ui() block that calls PlaybackController.bind(), once
self._playlist exists. Nothing reaches add_file/add_files/add_folder
before that point (no auto-ingestion on construction, same as
PlaybackController's pre-bind() argument), so no runtime guard was added.

Strictly preserved, not simplified
-------------------------------------
_MetadataWorker (including its docstring on the stop()/wait() shutdown
trade-off) and the dedup/enqueue/pending-play/on_track_ready logic are
moved verbatim — CLAUDE.md's remaining-extraction note is explicit that
this logic is already correct and is being moved, not improved.

shutdown() ordering — the one deliberate deviation from today's sequence
----------------------------------------------------------------------------
MainWindow.closeEvent() calls this class's shutdown() *before*
PlaybackController.shutdown(), reversing today's order (today:
_playback.shutdown() runs first, _meta_worker.stop()/wait() runs last).
Reason: while the worker thread is still draining, a track_ready signal
can still land on _on_track_ready and, if a track is already playing, call
self._playback.append_to_active_list(path) — PlaybackController.shutdown()
doesn't clear current_track, so that guard stays open even after VLC has
been told to stop. Stopping ingestion first bounds (does not perfectly
eliminate — Qt's queued cross-thread signals aren't delivered until the
main thread's event loop resumes, which it doesn't do *during*
QThread.wait() either way) the window in which that call can happen. See
CLAUDE.md's PlaylistIngestionManager entry for the empirically observed
effect of this reordering on playlist persistence (a file dropped and
closed-over immediately).
"""

from __future__ import annotations

import os
import re
import threading
from typing import Callable

from PyQt6.QtCore import QObject, QThread, pyqtSignal, pyqtSlot

from audio.engine import read_metadata
from config.settings import is_audio
from ui.playback_controller import PlaybackController
from ui.playlist import PlaylistWidget


def _natural_key(s: str):
    return [int(c) if c.isdigit() else c.lower() for c in re.split(r'(\d+)', s)]


class _MetadataWorker(QThread):
    """
    Background thread that reads audio metadata without blocking the UI.
    Push paths with enqueue() or enqueue_many().
    Emits track_ready(path, meta) on the Qt thread for each file.

    Call stop() then a bounded wait() to shut it down cleanly (see
    PlaylistIngestionManager.shutdown()). stop() pushes a sentinel onto the
    queue so run() exits on its own after processing whatever is already
    queued — that covers the common case (thread idle or between reads). It
    cannot interrupt a read_metadata() call already in progress on a slow
    file; there's no safe way to preempt a blocking call from another
    thread in Python. That's why shutdown()'s wait() is bounded rather than
    unconditional — the app must still be able to close promptly even if
    this thread is stuck, at the cost of Qt's "QThread: Destroyed while
    thread is still running" warning in that rare case.
    """
    track_ready = pyqtSignal(str, dict)

    _STOP = object()  # sentinel pushed onto the queue to end run()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._queue: list = []
        self._lock = threading.Lock()
        self._sem  = threading.Semaphore(0)
        self.start()

    def enqueue(self, path: str) -> None:
        with self._lock:
            self._queue.append(path)
        self._sem.release()

    def enqueue_many(self, paths: list) -> None:
        with self._lock:
            self._queue.extend(paths)
        for _ in paths:
            self._sem.release()

    def stop(self) -> None:
        """Ask run() to exit after it's done with whatever it's currently doing."""
        with self._lock:
            self._queue.append(self._STOP)
        self._sem.release()

    def run(self) -> None:
        while True:
            self._sem.acquire()
            with self._lock:
                if not self._queue:
                    continue
                path = self._queue.pop(0)
            if path is self._STOP:
                return
            try:
                meta = read_metadata(path)
            except Exception:
                meta = {
                    "title": os.path.basename(path), "artist": "Unknown",
                    "album": "", "track": "", "duration_str": "0:00",
                    "bitrate": "", "sample_rate": "",
                }
            self.track_ready.emit(path, meta)


class PlaylistIngestionManager(QObject):
    """Owns the background metadata worker and the add_file/add_files/
    add_folder ingestion methods.

    Two-phase construction — see module docstring: __init__ starts the
    metadata worker only; bind() wires the playlist widget once
    MainWindow._build_ui() has created it."""

    def __init__(
        self,
        playback: PlaybackController,
        status_message: Callable[[str], None],
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._playback = playback
        self._status_message = status_message
        self._playlist: PlaylistWidget | None = None

        self._meta_worker = _MetadataWorker(self)
        self._meta_worker.track_ready.connect(self._on_track_ready)
        self._pending_play: str | None = None   # path to auto-play once added

    def bind(self, playlist: PlaylistWidget) -> None:
        """Wire the playlist widget in once _build_ui() has created it.
        Must be called exactly once, after self._playlist exists."""
        self._playlist = playlist

    # ------------------------------------------------------------------
    # Ingestion
    # ------------------------------------------------------------------

    def add_file(self, path: str, play_when_ready: bool = False) -> None:
        """Enqueue path for async metadata loading.
        If play_when_ready is True and the player is idle, the track will
        start playing as soon as its metadata arrives.
        """
        if self._playlist.item_by_path(path) is not None:
            return  # duplicate
        if play_when_ready and not self._playback.player.is_playing():
            self._pending_play = path
        self._meta_worker.enqueue(path)

    def add_files(self, paths: list) -> None:
        new_paths = [p for p in paths if self._playlist.item_by_path(p) is None]
        if not new_paths:
            return
        self._meta_worker.enqueue_many(new_paths)
        n = len(new_paths)
        self._status_message(f"Loading {n} track{'s' if n > 1 else ''}…")

    def add_folder(self, folder: str) -> None:
        paths = [
            os.path.join(folder, name)
            for name in sorted(os.listdir(folder), key=_natural_key)
            if is_audio(os.path.join(folder, name))
        ]
        new_paths = [p for p in paths if self._playlist.item_by_path(p) is None]
        if new_paths:
            self._meta_worker.enqueue_many(new_paths)

    @pyqtSlot(str, dict)
    def _on_track_ready(self, path: str, meta: dict) -> None:
        """Called by _MetadataWorker when metadata for a path is ready."""
        self._playlist.add_track(
            path,
            meta["track"],
            meta["artist"],
            meta["album"],
            meta["title"],
            meta["duration_str"],
        )
        self._status_message(f"Added: {meta['artist']} — {meta['title']}")
        if self._pending_play == path:
            self._pending_play = None
            item = self._playlist.item_by_path(path)
            if item is not None:
                self._playback.play_item(item)
        elif self._playback.current_track is not None:
            # A track was added while playback is active: append it directly
            # to the existing VLC media list instead of rebuilding from scratch.
            # Rebuilding calls set_media_list() which resets VLC's internal
            # position pointer and causes tracks to be skipped or replayed.
            self._playback.append_to_active_list(path)

    # ------------------------------------------------------------------
    # Shutdown
    # ------------------------------------------------------------------

    def shutdown(self) -> None:
        """Called from MainWindow.closeEvent(), before PlaybackController's
        own shutdown() — see module docstring's "shutdown() ordering"
        section for why. Ask the metadata worker to stop and give it a
        bounded moment to do so cleanly. If it's stuck in a slow
        read_metadata() call, wait() times out and the caller closes
        anyway rather than hang app shutdown (see _MetadataWorker's
        docstring for why that's the trade-off)."""
        self._meta_worker.stop()
        self._meta_worker.wait(2000)
