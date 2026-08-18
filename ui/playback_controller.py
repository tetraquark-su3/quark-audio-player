"""
ui/playback_controller.py
PlaybackController: owns VLC (Instance/MediaPlayer/MediaListPlayer/MediaList),
current-track/shuffle/repeat state, the progress/end-of-playlist-grace
timers, and the play/pause/stop/next/prev/shuffle/repeat/seek/EQ
orchestration — extracted from MainWindow (see CLAUDE.md, "PlaybackController"
extraction, 6/7).

Ownership boundary
-------------------
Everything that is *lecture VLC* in the literal sense moves here: the
vlc.Instance, MediaPlayer, MediaListPlayer, MediaList + its lock,
current_track/current_item, shuffle/repeat/_shuffle_order,
_timer_progress/_timer_end_grace, _rebuild_media_list, _apply_eq, both
cross-thread pyqtSignals (VLC's MediaListPlayerNextItemSet event and the
background media-append thread), and the MediaPlayerEncounteredError event
handler (found during the audit that produced this file — same family as
the next-item event, not originally named in the extraction request but
structurally identical VLC-event wiring).

Deliberately NOT moved, even though the ported methods touch them
constantly: the six visualisation widgets, _timer_fft/_update_fft, the
SampleLoader (self._loader), the info labels/album-art panel, the icon
manager, and the status bar. All of those stay MainWindow-owned — see
CLAUDE.md's remaining-scope line ("progress/end timers", not "FFT timer")
and the Architecture map entry for the reasoning. PlaybackController reaches
them only through constructor-injected callables (set_play_icon,
update_ui_for_track, reset_visualizations, status_message, timer_fft_start/
stop, loader_load) plus two direct widget references it writes to on every
progress tick (progress_slider, time_label, bound via bind() — see
Two-phase construction below) — not a pyqtSignal-per-event design. Signals
earn their keep when the emitter doesn't know its consumer (see
ui/settings_controller.py's docstring); here MainWindow is the only
consumer, known up front, so passing its own bound methods is the more
direct fit, exactly like SettingsController's dialogs. The two signals that
*do* exist on this class (_vlc_next_item_signal, _media_appended_signal)
are not a style choice — they marshal calls made by VLC on its own
background threads onto the Qt thread, which only a pyqtSignal can do
safely; PlaybackController subclasses QObject purely to carry them, unlike
every plain-Python class extracted before it.

Two-phase construction (__init__ / bind())
----------------------------------------------
MainWindow.__init__ constructs the VLC engine before _build_ui() runs,
because _build_ui() itself needs self._playback.player for the initial
`audio_set_volume(80)` call — same ordering constraint the original inline
code had (self._player was built before _build_ui() too). But
PlaybackController also needs the playlist widget, the progress slider and
the time label, and none of those exist until _build_ui() has created them
(self._playlist, self._progress, self._time_label all live inside
_build_ui(), not before it).

Splitting the constructor in two would be possible by threading these
three through as constructor args if _build_ui() built them before the
volume-set line — and today's _build_ui() ordering actually happens to
allow that (playlist/progress/time_label are all created before the
volume-set line). It was deliberately not done that way: it would mean
moving the ~20-line VLC-engine construction block out of __init__ and into
the middle of the 300-line widget-construction method, which works against
the separation this extraction exists to create, and makes the dependency
on _build_ui()'s internal ordering an implicit, easy-to-silently-break one
instead of an explicit one.

Instead: __init__ takes only what exists early (eq_state_provider and the
UI callbacks — all bound MainWindow methods, callable without their
underlying widgets existing yet, since they're not actually invoked until
real playback happens) and builds the VLC engine. bind(playlist,
progress_slider, time_label) is called exactly once, as the last line of
_build_ui(), once those three widgets are real. Nothing can trigger actual
playback between PlaybackController's construction and that bind() call:
MainWindow.__init__ runs _apply_config()/_apply_shortcuts()/_load_playlist()
only after _build_ui() returns, no track auto-plays on construction, and a
command-line/socket file open (main.py, _open_from_socket) can only reach
MainWindow after __init__ has fully returned. So no runtime guard against
calling playback methods pre-bind() was added — see bind()'s docstring.

Interface for the not-yet-extracted PlaylistIngestionManager
--------------------------------------------------------------
_media_list_lock and _media_list are private and stay that way.
PlaylistIngestionManager (see CLAUDE.md, extraction 7/7, still pending)
will need exactly two things once it exists, both already public here:
  - current_track (read-only property) — for the same guard
    MainWindow._on_track_ready uses today ("is a track currently active,
    should this new file be appended live instead of just queued").
  - append_to_active_list(path) — the public rename of the former
    _append_to_media_list, encapsulating the lock+add+retry+shuffle-fallback
    protocol described in its docstring below. Nobody outside this class
    touches the lock or the list reference directly, today or later.

Strictly preserved, not simplified
-------------------------------------
_rebuild_media_list, append_to_active_list, and _on_media_appended are
ported verbatim (including their docstrings/comments) — this is the exact
race-condition protocol documented in CLAUDE.md's Gotchas section, and it
is not touched here beyond the mechanical rename of the first.

shutdown() is deliberately NOT a call to stop(): MainWindow.closeEvent()
never called _stop() in the original code either — it called
_list_player.stop() plus the three timer .stop()s directly, with no icon
change, no progress/visualisation reset, and no status message. Routing
closeEvent through the public stop() would be a small but real behaviour
change (extra UI churn on a window that's closing), so shutdown() mirrors
the original sequence exactly instead of reusing stop().

The five "playback has stopped" call sites (stop(), next_track()'s
end-of-playlist branch, _on_end_grace_timeout, _on_vlc_error, and
shutdown()) are NOT unified into one shared code path even though they
look similar — they differ in real, present-day ways (stop() never resets
time_label; next_track()'s end-of-playlist branch and
_on_end_grace_timeout do; _on_vlc_error resets neither progress nor
time_label; shutdown() touches no UI at all). Collapsing them would be an
uninvited behaviour change, not a pure move — noted as a possible future
cleanup in CLAUDE.md, not done here.

Not moved: volume/mute (_on_volume_changed, _toggle_mute) — out of scope,
was not part of the extraction request and stays in MainWindow, reaching
this class only via the public `player` property.
"""

from __future__ import annotations

import os
import random
import sys
import threading
from typing import Callable

import vlc
from PyQt6.QtCore import QObject, QTimer, pyqtSignal, pyqtSlot
from PyQt6.QtWidgets import QLabel

from ui.playlist import PlaylistWidget
from ui.widgets import ClickableSlider


class PlaybackController(QObject):
    """Owns VLC playback (player/list_player/media_list) and the
    play/pause/stop/next/prev/shuffle/repeat/seek orchestration around it.

    Two-phase construction — see module docstring: __init__ builds the VLC
    engine only; bind() wires the playlist/progress-bar/time-label widgets
    once MainWindow._build_ui() has created them."""

    # Fired from VLC's own callback threads → marshalled onto the Qt thread.
    # Internal wiring only; nothing outside this class connects to these.
    _vlc_next_item_signal  = pyqtSignal()
    _media_appended_signal = pyqtSignal(str, object)  # path, target_list

    def __init__(
        self,
        eq_state_provider: Callable[[], dict],
        set_play_icon: Callable[[bool], None],
        update_ui_for_track: Callable[[str], None],
        reset_visualizations: Callable[[], None],
        status_message: Callable[[str], None],
        timer_fft_start: Callable[[], None],
        timer_fft_stop: Callable[[], None],
        loader_load: Callable[[str], None],
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._eq_state_provider     = eq_state_provider
        self._set_play_icon         = set_play_icon
        self._update_ui_for_track   = update_ui_for_track
        self._reset_visualizations  = reset_visualizations
        self._status_message        = status_message
        self._timer_fft_start       = timer_fft_start
        self._timer_fft_stop        = timer_fft_stop
        self._loader_load           = loader_load

        # Set by bind(), once _build_ui() has created these widgets.
        self._playlist: PlaylistWidget | None = None
        self._progress: ClickableSlider | None = None
        self._time_label: QLabel | None = None

        self._current_track = None           # int | None
        self._current_item  = None           # QTreeWidgetItem | None
        self._shuffle       = False
        self._repeat        = False
        self._shuffle_order: list[int] = []   # fixed random order, generated once

        # VLC — MediaPlayer handles EQ/volume; MediaListPlayer handles transitions
        self._vlc = vlc.Instance("--reset-plugins-cache")
        self._player = self._vlc.media_player_new()
        self._player.event_manager().event_attach(
            vlc.EventType.MediaPlayerEncounteredError, self._on_vlc_error
        )
        self._list_player = self._vlc.media_list_player_new()
        self._list_player.set_media_player(self._player)
        self._list_player.set_playback_mode(vlc.PlaybackMode.default)
        self._list_player.event_manager().event_attach(
            vlc.EventType.MediaListPlayerNextItemSet,
            lambda _e: self._vlc_next_item_signal.emit(),
        )
        self._media_list  = self._vlc.media_list_new()
        self._list_player.set_media_list(self._media_list)
        # Guards only the read/swap of self._media_list itself — never the
        # blocking VLC-level media_list.lock()/add_media()/unlock() sequence,
        # which must stay outside it so a slow VLC lock during playback can
        # never make _rebuild_media_list() block the Qt thread. See
        # append_to_active_list()/_on_media_appended() for the full protocol.
        self._media_list_lock = threading.Lock()

        # Timers
        self._timer_progress = QTimer()
        self._timer_progress.setInterval(500)
        self._timer_progress.timeout.connect(self._update_progress)

        # Short grace period before declaring end-of-playlist: if a track is
        # appended within this window, _on_media_appended cancels the timer
        # and the visualisations never freeze.
        self._timer_end_grace = QTimer()
        self._timer_end_grace.setSingleShot(True)
        self._timer_end_grace.setInterval(200)
        self._timer_end_grace.timeout.connect(self._on_end_grace_timeout)

        self._vlc_next_item_signal.connect(self._on_vlc_next_item)
        self._media_appended_signal.connect(self._on_media_appended)

    def bind(
        self,
        playlist: PlaylistWidget,
        progress_slider: ClickableSlider,
        time_label: QLabel,
    ) -> None:
        """Wire the playlist/progress-bar/time-label widgets, once
        MainWindow._build_ui() has created them. Call exactly once, as the
        last line of _build_ui() — see the module docstring's "Two-phase
        construction" section for why nothing can reach a playback method
        before this has run, and so why no guard is needed here against
        being called too late/early."""
        self._playlist   = playlist
        self._progress   = progress_slider
        self._time_label = time_label
        self._playlist.order_changed.connect(self._resync_current_track)

    # ------------------------------------------------------------------
    # Public read-only state
    # ------------------------------------------------------------------

    @property
    def player(self):
        """The underlying vlc.MediaPlayer — used by MainWindow for volume
        control and the equalizer dialog's live preview, neither of which
        is part of this extraction's scope."""
        return self._player

    @property
    def current_track(self) -> int | None:
        return self._current_track

    @property
    def current_item(self):
        return self._current_item

    @property
    def shuffle(self) -> bool:
        return self._shuffle

    @property
    def repeat(self) -> bool:
        return self._repeat

    # ------------------------------------------------------------------
    # Append a new track to the live VLC media list
    # ------------------------------------------------------------------

    def append_to_active_list(self, path: str) -> None:
        """
        Append *path* to the live VLC media list without touching the Qt thread.

        libvlc_media_list_lock() is a blocking mutex — if VLC holds it during
        playback (which it frequently does), calling it on the Qt main thread
        causes the UI to freeze for the entire lock duration.  We therefore
        run the lock/add/unlock sequence in a daemon thread and emit a signal
        back to the Qt thread once done so the timers are updated safely.

        self._media_list itself can be swapped by _rebuild_media_list() (on
        the Qt thread) while this daemon thread is running — that's why the
        list reference is re-read from self._media_list under
        self._media_list_lock right before use, instead of being captured
        once up front. The lock only guards that quick read (and the swap in
        _rebuild_media_list()); it is released before the slow VLC-level
        lock/add/unlock, so a concurrent rebuild is never blocked on it. If a
        rebuild still lands in the (now narrow) gap between the read and the
        VLC add completing, _on_media_appended() detects the mismatch and
        retries — see there.
        """
        if self._shuffle:
            # In shuffle mode we can't simply append — fall back to a full
            # rebuild (safe because shuffle resets position intentionally).
            self._rebuild_media_list(from_row=self._current_track, reshuffle=False)
            return

        item = self._playlist.item_by_path(path)
        if item is None:
            return

        vlc_instance = self._vlc

        def _do_append():
            media = vlc_instance.media_new(path)
            with self._media_list_lock:
                target_list = self._media_list  # re-read now, not at call time
            target_list.lock()
            try:
                target_list.add_media(media)
            finally:
                target_list.unlock()
            # Signal the Qt thread with *which* list the media actually
            # landed in, so it can tell whether a rebuild raced past it.
            self._media_appended_signal.emit(path, target_list)

        threading.Thread(target=_do_append, daemon=True).start()

    @pyqtSlot(str, object)
    def _on_media_appended(self, path: str, target_list) -> None:
        """Called on the Qt thread after a background append completes."""
        if target_list is not self._media_list:
            # self._media_list was swapped (a rebuild fired) while the
            # background add was in flight — the media was added to a list
            # nobody plays from anymore. Retry against the current list
            # instead of silently losing the track.
            self.append_to_active_list(path)
            return

        item = self._playlist.item_by_path(path)
        if item is None:
            return  # track was removed from the playlist in the meantime

        # Cancel any pending end-of-playlist grace timer — a new track just
        # arrived so we must not freeze/reset the visualisations.
        self._timer_end_grace.stop()

        if self._player.is_playing():
            self._timer_progress.start()
            self._timer_fft_start()
        else:
            state = self._player.get_state()
            if state in (vlc.State.Ended, vlc.State.Stopped, vlc.State.NothingSpecial):
                # Playlist had ended — play the newly added track directly
                # instead of _list_player.play() which restarts from index 0.
                self.play_item(item)

    # ------------------------------------------------------------------
    # Playback
    # ------------------------------------------------------------------

    def play_item(self, item) -> None:
        if item is None:
            return
        path = self._playlist.path_of(item)
        if not os.path.exists(path):
            self._status_message(f"File not found: {path}")
            return
        if not os.access(path, os.R_OK):
            self._status_message(f"Permission denied: {os.path.basename(path)}")
            return

        row = self._playlist.indexOfTopLevelItem(item)
        self._rebuild_media_list(from_row=row, reshuffle=True)
        # Rebuild the MediaList starting from this track so VLC can
        # chain automatically into the next ones.

        # Play the first item in the freshly built list (= our target track)
        self._list_player.stop()
        self._list_player.play_item_at_index(0)

        self._current_track = row
        self._current_item  = item
        self._playlist.clearSelection()
        self._playlist.setCurrentItem(item)

        self._update_ui_for_track(path)
        self._apply_eq()

        self._timer_progress.start()
        self._timer_fft_start()
        self._loader_load(path)

    def _rebuild_media_list(self, from_row: int = 0, reshuffle: bool = False) -> None:
        """
        Rebuild self._media_list from the playlist starting at from_row.
        In shuffle mode, uses a stable pre-generated order (_shuffle_order)
        so each track plays exactly once.  Pass reshuffle=True to force a
        new random order (e.g. when shuffle is toggled on, or a new track
        is manually selected).
        """
        ml = self._vlc.media_list_new()
        n  = self._playlist.topLevelItemCount()

        if self._shuffle and from_row < n:
            # Generate a new order only when explicitly requested or the
            # stored order is stale (wrong size or doesn't contain from_row).
            if (reshuffle
                    or len(self._shuffle_order) != n
                    or from_row not in self._shuffle_order):
                rest = [r for r in range(n) if r != from_row]
                random.shuffle(rest)
                self._shuffle_order = [from_row] + rest

            # Slice the order so it starts at from_row's position
            start_idx = self._shuffle_order.index(from_row)
            rows = self._shuffle_order[start_idx:]
        else:
            rows = list(range(from_row, n))

        for r in rows:
            it = self._playlist.item_at_row(r)
            if it is not None:
                ml.add_media(self._playlist.path_of(it))

        # Swap the list on the player. Guarded by the same short lock
        # append_to_active_list()'s background thread uses to re-read
        # self._media_list, so it never sees a half-swapped reference —
        # see append_to_active_list()/_on_media_appended() for the full
        # protocol this is part of.
        with self._media_list_lock:
            self._media_list = ml
            self._list_player.set_media_list(ml)

    def _apply_eq(self) -> None:
        """Re-attach equalizer to the current MediaPlayer (survives track changes)."""
        eq_state = self._eq_state_provider()
        if eq_state:
            eq = vlc.libvlc_audio_equalizer_new()
            vlc.libvlc_audio_equalizer_set_preamp(eq, eq_state.get("preamp", 0.0))
            for i, amp in enumerate(eq_state.get("bands", [])):
                vlc.libvlc_audio_equalizer_set_amp_at_index(eq, amp, i)
            vlc.libvlc_media_player_set_equalizer(self._player, eq)
            vlc.libvlc_audio_equalizer_release(eq)

    @pyqtSlot()
    def _on_vlc_next_item(self) -> None:
        """
        Called (via signal) when VLC's MediaListPlayer moves to the next item.
        Identifies the new track by asking VLC which media it is playing
        (via get_media().get_mrl()) rather than maintaining a fragile index
        counter that can drift when tracks are appended asynchronously.
        """
        if self._repeat:
            if self._current_item is not None:
                path = self._playlist.path_of(self._current_item)
                self._update_ui_for_track(path)
                self._apply_eq()
                self._loader_load(path)
                self._timer_progress.start()
                self._timer_fft_start()
            return

        # Ask VLC which media it just started
        current_media = self._player.get_media()
        if current_media is None:
            return

        # MRL is a URI — convert to local path for lookup
        import urllib.parse
        mrl = current_media.get_mrl()
        if mrl.startswith("file://"):
            # urlparse handles both Unix (file:///home/...) and Windows
            # (file:///C:/...) correctly; mrl[7:] left a leading slash on
            # Windows paths (/C:/Music/...) which broke the playlist lookup.
            new_path = urllib.parse.unquote(urllib.parse.urlparse(mrl).path)
            if sys.platform == "win32" and len(new_path) > 2 \
                    and new_path[0] == "/" and new_path[2] == ":":
                new_path = new_path[1:]  # /C:/foo → C:/foo
        else:
            new_path = mrl

        # Find this path in the playlist
        new_item = self._playlist.item_by_path(new_path)
        if new_item is None:
            # Try a looser match (Windows drive letters, encoding differences)
            new_path_norm = os.path.normpath(new_path)
            for r in range(self._playlist.topLevelItemCount()):
                it = self._playlist.item_at_row(r)
                if it and os.path.normpath(self._playlist.path_of(it)) == new_path_norm:
                    new_item = it
                    break

        if new_item is None:
            return

        new_row = self._playlist.indexOfTopLevelItem(new_item)
        self._current_track = new_row
        self._current_item  = new_item
        self._playlist.clearSelection()
        self._playlist.setCurrentItem(new_item)
        self._playlist.scrollToItem(new_item)   # keep current track visible
        self._update_ui_for_track(new_path)
        self._apply_eq()
        self._loader_load(new_path)
        self._timer_progress.start()
        self._timer_fft_start()

    def _resync_current_track(self) -> None:
        """Recompute _current_track row index after a drag-and-drop reorder."""
        if self._current_item is not None:
            self._current_track = self._playlist.indexOfTopLevelItem(self._current_item)

    def toggle_play(self) -> None:
        if self._player.is_playing():
            self._list_player.pause()
            self._set_play_icon(False)
            self._timer_progress.stop()
            self._timer_fft_stop()
        else:
            if self._current_track is None and self._playlist.topLevelItemCount() > 0:
                self.play_item(self._playlist.topLevelItem(0))
            else:
                self._list_player.play()
                self._set_play_icon(True)
                self._timer_progress.start()
                self._timer_fft_start()

    def stop(self) -> None:
        self._list_player.stop()
        self._set_play_icon(False)
        self._progress.setValue(0)
        self._timer_progress.stop()
        self._timer_fft_stop()
        self._timer_end_grace.stop()
        self._reset_visualizations()
        self._status_message("Stopped.")

    def next_track(self) -> None:
        if self._current_track is None:
            return
        if self._repeat:
            self.play_item(self._playlist.item_at_row(self._current_track))
            return
        result = self._list_player.next()
        if result == -1:
            self._list_player.stop()
            self._timer_fft_stop()
            self._timer_progress.stop()
            self._set_play_icon(False)
            self._progress.setValue(0)
            self._time_label.setText("0:00 / 0:00")
            self._reset_visualizations()
            self._status_message("End of playlist.")

    def prev_track(self) -> None:
        if self._current_track is None:
            return
        if self._current_track == 0:
            self._player.set_position(0.0)
            return
        self.play_item(self._playlist.item_at_row(self._current_track - 1))

    def toggle_shuffle(self, active: bool) -> None:
        """active is the new checked-state of the Shuffle button — read and
        passed in by MainWindow, which owns the button; icon repainting is
        likewise left to MainWindow (IconManager), not done here."""
        self._shuffle = active
        if not self._shuffle:
            self._shuffle_order = []   # clear stale order
        if self._current_track is not None:
            self._rebuild_media_list(from_row=self._current_track, reshuffle=True)

    def toggle_repeat(self, active: bool) -> None:
        """active is the new checked-state of the Repeat button — see
        toggle_shuffle()'s docstring for why MainWindow passes it in."""
        self._repeat = active
        mode = vlc.PlaybackMode.repeat if self._repeat else vlc.PlaybackMode.default
        self._list_player.set_playback_mode(mode)

    def seek(self, value: int) -> None:
        self._player.set_position(value / 1_000.0)

    # ------------------------------------------------------------------
    # Progress timer
    # ------------------------------------------------------------------

    def _update_progress(self) -> None:
        state = self._player.get_state()
        if state in (vlc.State.Ended, vlc.State.Stopped) and not self._player.is_playing():
            # Only declare end-of-playlist if the list_player is also stopped
            # (not just mid-transition between tracks).
            lp_state = self._list_player.get_state()
            if lp_state not in (vlc.State.Ended, vlc.State.Stopped, vlc.State.NothingSpecial):
                return  # VLC is transitioning — don't touch timers
            # Use a short grace period: if a track is appended within 200 ms
            # (e.g. user drops a file just as the last track ends), the timer
            # will be cancelled by _on_media_appended before it fires, so we
            # never freeze the visualisations for a track that's about to play.
            if not self._timer_end_grace.isActive():
                self._timer_end_grace.start()
            return
        # Still playing — cancel any pending end-of-playlist grace timer
        if self._timer_end_grace.isActive():
            self._timer_end_grace.stop()
        total = self._player.get_length()
        if total > 0:
            self._progress.setValue(int(self._player.get_position() * 1_000))
            cur = self._player.get_time()
            self._time_label.setText(
                f"{self._ms_to_str(cur)} / {self._ms_to_str(total)}"
            )

    def _on_end_grace_timeout(self) -> None:
        """Fires 200 ms after end-of-playlist is detected — confirms it's real."""
        if self._player.is_playing():
            return  # a new track started during the grace period
        self._timer_progress.stop()
        self._timer_fft_stop()
        self._set_play_icon(False)
        self._progress.setValue(0)
        self._time_label.setText("0:00 / 0:00")
        self._reset_visualizations()
        self._status_message("End of playlist.")

    @staticmethod
    def _ms_to_str(ms: int) -> str:
        s = max(0, ms // 1_000)
        return f"{s // 60}:{s % 60:02d}"

    # ------------------------------------------------------------------
    # VLC error callback
    # ------------------------------------------------------------------

    def _on_vlc_error(self, _event) -> None:
        self._status_message("Playback error: corrupt or unsupported file.")
        self._list_player.stop()
        self._set_play_icon(False)
        self._timer_progress.stop()
        self._timer_fft_stop()
        self._reset_visualizations()

    # ------------------------------------------------------------------
    # Shutdown
    # ------------------------------------------------------------------

    def shutdown(self) -> None:
        """Called from MainWindow.closeEvent(). Deliberately not stop():
        the original closeEvent never called _stop() either — it stopped
        the list_player and the two timers this class owns directly, with
        no icon change, no progress/visualisation reset, and no status
        message. Mirrors that exact sequence rather than reusing stop()."""
        self._list_player.stop()
        self._timer_progress.stop()
        self._timer_end_grace.stop()
