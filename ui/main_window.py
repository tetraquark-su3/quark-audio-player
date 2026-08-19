"""
ui/main_window.py
MainWindow: wires together VLC playback, playlist, visualisations,
file browser, settings, equalizer, and keyboard shortcuts.
"""

from __future__ import annotations

import os
import sys
import numpy as np
from PyQt6.QtCore    import Qt, QTimer, pyqtSlot, pyqtSignal
from PyQt6.QtGui     import QColor, QKeySequence, QShortcut, QIcon, QPainter, QPen
from PyQt6.QtWidgets import (
    QApplication, QFileDialog, QHBoxLayout,
    QLabel, QMainWindow, QMessageBox, QSplitter, QSplitterHandle, QTabWidget,
    QTextEdit, QVBoxLayout, QWidget, QPushButton,
    QLineEdit
)
from audio.engine     import (
    SampleLoader, build_detail_text, compute_fft_frame, read_metadata,
)
from config.settings  import (
    DEFAULT_CONFIG, PLAYLIST_PATH,
    is_audio, load_config, save_config,
)
from ui.album_art_panel import AlbumArtPanel
from ui.file_browser_panel import FileBrowserPanel
from ui.icon_manager  import IconManager
from ui.playback_controller import PlaybackController
from ui.playlist      import PlaylistWidget
from ui.playlist_ingestion_manager import PlaylistIngestionManager
from ui.playlist_persistence import PlaylistPersistence, PlaylistPersistenceError
from ui.settings_controller import SettingsController
from ui.style         import build_stylesheet
from ui.visualizations import (
    LissajousWidget, OscilloscopeWidget, SpectralFluxWidget,
    SpectrogramWidget, SpectrumWidget, VUMeterWidget,
)
from ui.widgets       import ClickableSlider
from ui.icons        import ICON_STYLES

# ---------------------------------------------------------------------------
# Styled splitter — wide handle with visible grip dots + hover highlight
# ---------------------------------------------------------------------------

class _StyledSplitterHandle(QSplitterHandle):
    """
    Custom splitter handle that draws three grip dots and highlights
    with the application's primary colour on hover.
    """
    _HANDLE_WIDTH = 6   # pixels

    def __init__(self, orientation, parent, primary_color: str = "#e94560",
                 surface_color: str = "#2a2a44", dot_color: str = "#9098b0") -> None:
        super().__init__(orientation, parent)
        self._primary  = QColor(primary_color)
        self._surface  = QColor(surface_color)
        self._dot      = QColor(dot_color)
        self._hovered  = False
        self.setMouseTracking(True)

    def update_colors(self, primary: str, surface: str, dot: str) -> None:
        self._primary = QColor(primary)
        self._surface = QColor(surface)
        self._dot     = QColor(dot)
        self.update()

    def enterEvent(self, event) -> None:
        self._hovered = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._hovered = False
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()

        # Background
        bg = self._primary if self._hovered else self._surface
        painter.fillRect(0, 0, w, h, bg)

        # Three grip dots centred on the handle
        dot_color = QColor(255, 255, 255, 180) if self._hovered else self._dot
        painter.setBrush(dot_color)
        painter.setPen(Qt.PenStyle.NoPen)
        r = 1.5
        if self.orientation() == Qt.Orientation.Vertical:
            # Dots arranged horizontally
            cx, cy = w / 2, h / 2
            for dx in (-5, 0, 5):
                painter.drawEllipse(
                    int(cx + dx - r), int(cy - r), int(r * 2), int(r * 2)
                )
        else:
            # Dots arranged vertically
            cx, cy = w / 2, h / 2
            for dy in (-5, 0, 5):
                painter.drawEllipse(
                    int(cx - r), int(cy + dy - r), int(r * 2), int(r * 2)
                )


class _StyledSplitter(QSplitter):
    """QSplitter that creates _StyledSplitterHandle instances."""

    def __init__(self, orientation, primary: str = "#e94560",
                 surface: str = "#2a2a44", dot: str = "#9098b0",
                 parent=None) -> None:
        super().__init__(orientation, parent)
        self._primary = primary
        self._surface = surface
        self._dot     = dot
        self.setHandleWidth(_StyledSplitterHandle._HANDLE_WIDTH)

    def createHandle(self) -> _StyledSplitterHandle:
        return _StyledSplitterHandle(
            self.orientation(), self,
            self._primary, self._surface, self._dot,
        )

    def update_colors(self, primary: str, surface: str, dot: str) -> None:
        self._primary = primary
        self._surface = surface
        self._dot     = dot
        for i in range(self.count() + 1):
            h = self.handle(i)
            if isinstance(h, _StyledSplitterHandle):
                h.update_colors(primary, surface, dot)


BASE_DIR  = getattr(sys, "_MEIPASS", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ASSETS_DIR = os.path.join(BASE_DIR, "assets")

class MainWindow(QMainWindow):
    """Quark Audio Player — main application window."""
    _socket_file_received  = pyqtSignal(str)

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Quark Audio Player v0.6.6")
        app_icon_path = os.path.join(ASSETS_DIR, "icon_app.png")
        if os.path.exists(app_icon_path):
            self.setWindowIcon(QIcon(app_icon_path))
        self.setMinimumSize(900, 600)
        self.resize(1_100, 680)

        self._config        = load_config()
        self.setAcceptDrops(True)
        self._shortcuts: dict[str, QShortcut] = {}

        # VLC playback (player/list_player/media_list, current track,
        # shuffle/repeat, progress/end-grace timers) — see
        # ui/playback_controller.py. Constructed here (not after _build_ui())
        # because _build_ui() needs self._playback.player for the initial
        # volume-set call; the playlist/progress-slider/time-label widgets
        # it also needs don't exist yet at this point, so those are wired
        # in later via bind() — see PlaybackController's module docstring
        # ("Two-phase construction") for why. timer_fft_start/stop and
        # loader_load are lambdas for the same reason: self._timer_fft and
        # self._loader are also created after this point in __init__.
        self._playback = PlaybackController(
            eq_state_provider     = lambda: self._config.get("eq_state", {}),
            set_play_icon         = self._set_play_icon,
            update_ui_for_track   = self._update_ui_for_track,
            reset_visualizations  = self._reset_visualizations,
            status_message        = lambda msg: self.statusBar().showMessage(msg),
            timer_fft_start       = lambda: self._timer_fft.start(),
            timer_fft_stop        = lambda: self._timer_fft.stop(),
            loader_load           = lambda path: self._loader.load(path),
            parent                = self,
        )

        # Background audio sample loader
        self._loader = SampleLoader()
        self._known_sample_rate = 0  # last rate pushed to the freq-axis widgets

        # Async file/folder ingestion (metadata worker + dedup/enqueue) —
        # see ui/playlist_ingestion_manager.py. Constructed here (same point
        # _MetadataWorker used to be constructed directly) so the worker
        # thread starts at the same relative moment in startup as before;
        # bind(self._playlist) is called at the end of _build_ui(), same
        # two-phase pattern as self._playback above and for the same reason
        # (self._playlist doesn't exist yet at this point).
        self._ingestion = PlaylistIngestionManager(
            playback       = self._playback,
            status_message = lambda msg: self.statusBar().showMessage(msg),
            parent         = self,
        )

        # FFT/visualisation timer — stays here rather than moving into
        # PlaybackController; see its module docstring for why.
        self._timer_fft = QTimer()
        self._timer_fft.setInterval(1_000 // self._config["fps"])
        self._timer_fft.timeout.connect(self._update_fft)

        self._playlist_persistence = PlaylistPersistence(PLAYLIST_PATH)
        self._icon_manager = IconManager(ASSETS_DIR)
        self._settings = SettingsController()

        self._build_ui()
        self._apply_config()
        self._apply_shortcuts()
        self._load_playlist()

        self._socket_file_received.connect(self._open_from_socket)

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # ── Visualisation tabs ──────────────────────────────────────
        self._viz_tabs = QTabWidget()
        self._viz_tabs.setObjectName("vizTabs")

        self._spectrum     = SpectrumWidget()
        self._spectrogram  = SpectrogramWidget()
        self._oscilloscope = OscilloscopeWidget()
        self._lissajous    = LissajousWidget()
        self._flux         = SpectralFluxWidget()
        self._vumeter      = VUMeterWidget()

        self._viz_tabs.addTab(self._spectrum,    "Spectrum")
        self._viz_tabs.addTab(self._spectrogram, "Spectrogram")
        self._viz_tabs.addTab(self._oscilloscope,"Oscilloscope")
        self._viz_tabs.addTab(self._lissajous,   "Lissajous")
        self._viz_tabs.addTab(self._flux,        "Spectral Flux")
        self._viz_tabs.addTab(self._vumeter,     "VU Meter")

        # ── Left panel: file browser ────────────────────────────────
        self._file_browser = FileBrowserPanel()
        self._file_browser.add_file_requested.connect(self._on_browser_add_file)
        self._file_browser.add_files_requested.connect(self._ingestion.add_files)
        self._file_browser.add_folder_requested.connect(self._ingestion.add_folder)

        # ── Right panel: playlist + detail ──────────────────────────
        right_panel = QWidget()
        right_panel.setObjectName("rightPanel")
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(0)

        lbl_playlist = QLabel("  Playlist")
        lbl_playlist.setObjectName("sectionLabel")
        right_layout.addWidget(lbl_playlist)

        # Search bar (hidden by default)
        self._search_bar = QWidget()
        sl = QHBoxLayout(self._search_bar)
        sl.setContentsMargins(4, 4, 4, 4)
        self._search_field = QLineEdit()
        self._search_field.setPlaceholderText("Search artist / title / album…")
        self._search_field.textChanged.connect(lambda t: self._playlist.filter(t))
        btn_close_search = QPushButton("X")
        btn_close_search.setFixedSize(24, 24)
        btn_close_search.clicked.connect(self._close_search)
        sl.addWidget(self._search_field)
        sl.addWidget(btn_close_search)
        self._search_bar.hide()
        right_layout.addWidget(self._search_bar)

        # Playlist
        self._playlist = PlaylistWidget()
        self._playlist.doubleClicked.connect(
            lambda idx: self._playback.play_item(self._playlist.topLevelItem(idx.row()))
        )
        self._playlist.itemSelectionChanged.connect(self._on_selection_changed)
        # order_changed → _resync_current_track wiring now done inside
        # PlaybackController.bind() (see call at the end of this method),
        # since _resync_current_track lives there now.

        # Tabs: playlist + metadata detail
        content_tabs = QTabWidget()
        playlist_tab = QWidget()
        pt_layout = QVBoxLayout(playlist_tab)
        pt_layout.setContentsMargins(0, 0, 0, 0)
        pt_layout.addWidget(self._playlist)
        content_tabs.addTab(playlist_tab, "Playlist")

        detail_tab = QWidget()
        dt_layout = QVBoxLayout(detail_tab)
        self._detail_text = QTextEdit()
        self._detail_text.setReadOnly(True)
        dt_layout.addWidget(self._detail_text)
        content_tabs.addTab(detail_tab, "Details")
        right_layout.addWidget(content_tabs)

        # ── Splitters ────────────────────────────────────────────────
        h_splitter = _StyledSplitter(Qt.Orientation.Horizontal)
        h_splitter.addWidget(self._file_browser)
        h_splitter.addWidget(right_panel)
        h_splitter.setSizes([400, 700])

        v_splitter = _StyledSplitter(Qt.Orientation.Vertical)
        v_splitter.addWidget(self._viz_tabs)
        v_splitter.addWidget(h_splitter)
        v_splitter.setSizes([150, 500])
        layout.addWidget(v_splitter)

        self._h_splitter = h_splitter
        self._v_splitter = v_splitter

        # ── Control bar ──────────────────────────────────────────────
        control_bar = QWidget()
        control_bar.setObjectName("controlBar")
        control_bar.setFixedHeight(170)
        cb_layout = QHBoxLayout(control_bar)
        cb_layout.setContentsMargins(8, 8, 16, 8)
        cb_layout.setSpacing(12)

        # Album art column — image + artist/title stacked vertically
        art_col = QVBoxLayout()
        art_col.setContentsMargins(0, 0, 0, 0)
        art_col.setSpacing(4)

        self._album_art = AlbumArtPanel()
        art_col.addWidget(self._album_art)

        cb_layout.addLayout(art_col)

        ctrl_col = QVBoxLayout()
        ctrl_col.setContentsMargins(0, 0, 0, 0)
        ctrl_col.setSpacing(6)
        cb_layout.addLayout(ctrl_col)

        # track_label placed below progress bar (see below)

        prog_row = QHBoxLayout()
        self._progress = ClickableSlider(Qt.Orientation.Horizontal)
        self._progress.setObjectName("progressBar")
        self._progress.setRange(0, 1_000)
        self._progress.sliderMoved.connect(self._playback.seek)
        self._progress.setFixedHeight(24)
        self._time_label = QLabel("0:00 / 0:00")
        self._time_label.setObjectName("timeLabel")
        self._time_label.setFixedWidth(110)
        self._time_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        prog_row.addWidget(self._progress)
        prog_row.addWidget(self._time_label)
        self._label_artist = QLabel("")
        self._label_artist.setObjectName("labelArtist")
        self._label_artist.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ctrl_col.addWidget(self._label_artist)

        self._label_title = QLabel("— No track —")
        self._label_title.setObjectName("labelTitle")
        self._label_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._label_title.setWordWrap(True)
        ctrl_col.addWidget(self._label_title)

        ctrl_col.addLayout(prog_row)

        self._label_tech = QLabel("")
        self._label_tech.setObjectName("labelTech")
        self._label_tech.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ctrl_col.addWidget(self._label_tech)

        self._label_year = QLabel("")
        self._label_year.setObjectName("labelYear")
        self._label_year.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ctrl_col.addWidget(self._label_year)

        btn_row = QHBoxLayout()
        self._btn_settings = self._ctrl_btn("Settings", self._open_settings)
        self._btn_prev     = self._ctrl_btn("|<",       self._playback.prev_track)
        self._btn_play     = self._ctrl_btn(">",        self._playback.toggle_play)
        self._btn_stop     = self._ctrl_btn("[]",       self._playback.stop)
        self._btn_next     = self._ctrl_btn(">|",       self._playback.next_track)
        for btn in [self._btn_settings, self._btn_prev, self._btn_play,
                    self._btn_stop, self._btn_next]:
            btn_row.addWidget(btn)

        btn_row.addStretch()

        # ── Volume ──────────────────────────────────────────────────────────
        self._volume = ClickableSlider(Qt.Orientation.Horizontal)
        self._volume.setObjectName("volumeSlider")
        self._volume.setRange(0, 100)
        self._volume.setValue(80)
        self._volume_before_mute = 80
        self._volume.setFixedWidth(100)
        self._volume.setFixedHeight(36)
        self._volume_label = QLabel("80%")
        self._volume_label.setObjectName("timeLabel")
        self._volume_label.setFixedSize(36, 36)
        self._volume_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._volume.valueChanged.connect(self._on_volume_changed)
        self._playback.player.audio_set_volume(80)

        self._btn_mute = self._ctrl_btn("Volume", self._toggle_mute, checkable=True)
        btn_row.addWidget(self._btn_mute)
        btn_row.addWidget(self._volume)
        btn_row.addWidget(self._volume_label)

        self._btn_shuffle = self._ctrl_btn("Shuffle", self._toggle_shuffle, checkable=True)
        self._btn_repeat  = self._ctrl_btn("Repeat",  self._toggle_repeat,  checkable=True)
        self._btn_save    = self._ctrl_btn("Save",    self._save_playlist_as)
        self._btn_load    = self._ctrl_btn("Load",    self._load_playlist_from)
        self._btn_eq      = self._ctrl_btn("EQ",      self._open_equalizer)
        for btn in [self._btn_shuffle, self._btn_repeat,
                    self._btn_save, self._btn_load, self._btn_eq]:
            btn_row.addWidget(btn)

        ctrl_col.addLayout(btn_row)
        layout.addWidget(control_bar)

        self.statusBar().showMessage(
            "Welcome! Double-click an audio file to add it to the playlist."
        )

        self._icon_buttons: dict[str, QPushButton] = {
            "Settings": self._btn_settings,
            "|<":       self._btn_prev,
            ">":        self._btn_play,
            "[]":       self._btn_stop,
            ">|":       self._btn_next,
            "Shuffle":  self._btn_shuffle,
            "Repeat":   self._btn_repeat,
            "Save":     self._btn_save,
            "Load":     self._btn_load,
            "EQ":       self._btn_eq,
            "Volume":   self._btn_mute,
        }
        self._icon_manager.register_buttons(self._icon_buttons)

        self._album_art.show_no_art(
            self._config.get("icon_style", "neon"),
            self._config["primary_color"],
            self._config["accent_color"],
        )

        # Wire the playlist/progress-bar/time-label widgets into
        # PlaybackController now that they exist — see its module docstring
        # ("Two-phase construction") and __init__'s comment above the
        # PlaybackController(...) call for why this can't happen earlier.
        # bind() assumes every widget it might ever need (and everything
        # else built above) is already in place.
        self._playback.bind(self._playlist, self._progress, self._time_label)

        # Same two-phase reasoning for PlaylistIngestionManager — see its
        # module docstring. Order relative to self._playback.bind() above
        # doesn't matter (neither bind() call depends on the other), but
        # this one must stay the true last statement in _build_ui(): it's
        # what makes self._ingestion usable at all (self._playlist is None
        # until this runs).
        self._ingestion.bind(self._playlist)

    def _refresh_icons(self) -> None:
        """Retint all button icons to match the current background."""
        self._icon_manager.refresh_all(
            style   = self._config.get("icon_style", "neon"),
            primary = self._config["primary_color"],
            accent  = self._config["accent_color"],
            playing = self._playback.player.is_playing(),
            muted   = self._btn_mute.isChecked(),
            shuffle_active = self._playback.shuffle,
            repeat_active  = self._playback.repeat,
        )

    # ------------------------------------------------------------------
    # Button factory
    # ------------------------------------------------------------------

    def _load_icon(self, name: str, override_primary: str = "", override_accent: str = "") -> QIcon:
        """Generate a themed SVG icon, with PNG fallback.
        override_primary/accent allow swapping colours for active toggle states.
        """
        return self._icon_manager.load_icon(
            name,
            style   = self._config.get("icon_style", "neon"),
            primary = self._config["primary_color"],
            accent  = self._config["accent_color"],
            override_primary = override_primary,
            override_accent  = override_accent,
        )

    def _ctrl_btn(self, label, slot, checkable=False):
        btn = QPushButton()
        btn.setObjectName("controlButton")
        btn.setFixedSize(54, 36)
        btn.setCheckable(checkable)
        btn.clicked.connect(slot)
        self._icon_manager.initial_button_icon(
            btn, label,
            style   = self._config.get("icon_style", "neon"),
            primary = self._config["primary_color"],
            accent  = self._config["accent_color"],
        )
        tooltip_map = {
            "Settings": "Settings",
            "|<":       "Previous track",
            ">":        "Play",
            "||":       "Pause",
            "[]":       "Stop",
            ">|":       "Next track",
            "Shuffle":  "Shuffle",
            "Repeat":   "Repeat",
            "Save":     "Save playlist",
            "Load":     "Load playlist",
            "EQ":       "Equalizer",
            "Volume":   "Mute / Unmute",
            "Muted":    "Mute / Unmute",
        }
        btn.setToolTip(tooltip_map.get(label, label))
        return btn

    # ------------------------------------------------------------------
    # Config / style
    # ------------------------------------------------------------------

    def _apply_config(self) -> None:
        fps = self._config["fps"]
        self._timer_fft.setInterval(1_000 // fps)
        self._flux.set_max_points(self._config.get("flux_history", 2000))
        self._spectrogram.set_frames_per_bin(self._config.get("spectrogram_resolution", 15))
        cp = self._config["primary_color"]
        ca = self._config["accent_color"]
        cf = self._config["background_color"]
        for w in [self._spectrum, self._oscilloscope, self._lissajous,
                self._flux, self._vumeter]:
            w.set_colors(cp, ca)
        self.setStyleSheet(build_stylesheet(self._config))
        self._playlist.set_accent_color(self._config["accent_color"])
        # Update splitter handle colours to match the new theme. Uses the
        # same is_dark_bg() as ui/style.py::build_stylesheet — was a
        # simpler, unweighted RGB-mean formula that could disagree with
        # build_stylesheet's perceptual one on saturated backgrounds,
        # lifting/darkening surfaces in the opposite direction.
        from config.settings import derive_color, is_dark_bg
        _dark = is_dark_bg(cf)
        _step = 1 if _dark else -1
        s2  = derive_color(cf, 16 * _step)
        dot = "#9098b0" if _dark else "#7070a0"
        if hasattr(self, "_h_splitter"):
            self._h_splitter.update_colors(cp, s2, dot)
            self._v_splitter.update_colors(cp, s2, dot)
        # guard: _icon_buttons doesn't exist yet on first call from __init__
        if hasattr(self, "_icon_buttons"):
            self._refresh_icons()
        # Re-render the vinyl placeholder with the new style/colors
        if hasattr(self, "_album_art") and not self._album_art.has_art:
            self._album_art.show_no_art(self._config.get("icon_style", "neon"), cp, ca)

    def _apply_shortcuts(self) -> None:
        for sc in self._shortcuts.values():
            sc.setEnabled(False)
        self._shortcuts.clear()
        mapping = {
            "play_pause": self._playback.toggle_play,
            "next":       self._playback.next_track,
            "previous":   self._playback.prev_track,
        }
        shortcuts = self._config.get("shortcuts", DEFAULT_CONFIG["shortcuts"])
        for key, slot in mapping.items():
            seq = shortcuts.get(key, DEFAULT_CONFIG["shortcuts"][key])
            sc  = QShortcut(QKeySequence(seq), self)
            sc.activated.connect(slot)
            self._shortcuts[key] = sc

    def _open_settings(self) -> None:
        new_config = self._settings.open_settings_dialog(self._config, self)
        if new_config is not None:
            self._config = new_config
            self._apply_config()
            self._apply_shortcuts()
            save_config(self._config)

    # ------------------------------------------------------------------
    # External file-open (single-instance socket, see main.py)
    # ------------------------------------------------------------------
    @pyqtSlot(str)
    def _open_from_socket(self, path: str) -> None:
        self.raise_()
        self.activateWindow()
        item = self._playlist.item_by_path(path)
        if item is not None:
            if not self._playback.player.is_playing():
                self._playback.play_item(item)
        else:
            self._ingestion.add_file(path, play_when_ready=not self._playback.player.is_playing())

    # ------------------------------------------------------------------
    # File browser (wiring to FileBrowserPanel's signals)
    # ------------------------------------------------------------------
    def _on_browser_add_file(self, path: str) -> None:
        self._ingestion.add_file(path)
        self.statusBar().showMessage(f"Added: {os.path.basename(path)}")

    def _on_drop(self, event) -> None:
        for url in event.mimeData().urls():
            path = url.toLocalFile()
            if os.path.isfile(path) and is_audio(path):
                self._ingestion.add_file(path)
            elif os.path.isdir(path):
                self._ingestion.add_folder(path)
        event.accept()

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    def _close_search(self) -> None:
        self._search_bar.hide()
        self._search_field.clear()
        self._playlist.clear_filter()

    # ------------------------------------------------------------------
    # Playlist persistence
    # ------------------------------------------------------------------

    def _load_playlist(self) -> None:
        try:
            data = self._playlist_persistence.load_default()
        except PlaylistPersistenceError as e:
            print(f"[Playlist] Cannot load: {e}")
            return
        if data is not None:
            self._playlist.from_list(data, replace=True)

    def _save_playlist(self) -> None:
        try:
            self._playlist_persistence.save_default(self._playlist.to_list())
        except PlaylistPersistenceError as e:
            print(f"[Playlist] Cannot save: {e}")

    def _save_playlist_as(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Save playlist",
            os.path.expanduser("~/playlist.json"),
            "JSON playlists (*.json)",
        )
        if not path:
            return
        try:
            self._playlist_persistence.save(path, self._playlist.to_list())
            self.statusBar().showMessage(f"Saved: {os.path.basename(path)}")
        except PlaylistPersistenceError as e:
            self.statusBar().showMessage(f"Error: {e}")

    def _load_playlist_from(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Load playlist",
            os.path.expanduser("~"),
            "JSON playlists (*.json)",
        )
        if not path:
            return
        choice = QMessageBox.question(
            self, "Load playlist",
            "Replace current playlist or append?",
            QMessageBox.StandardButton.Reset    # Replace
            | QMessageBox.StandardButton.Yes    # Append
            | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Yes,
        )
        if choice == QMessageBox.StandardButton.Cancel:
            return
        replace = (choice == QMessageBox.StandardButton.Reset)
        try:
            data = self._playlist_persistence.load(path)
            count = self._playlist.from_list(data, replace=replace)
            self.statusBar().showMessage(f"{count} tracks loaded from {os.path.basename(path)}")
        except PlaylistPersistenceError as e:
            self.statusBar().showMessage(f"Error: {e}")

    # ------------------------------------------------------------------
    # Metadata / album art display
    # ------------------------------------------------------------------

    def _on_selection_changed(self) -> None:
        items = self._playlist.selectedItems()
        if items:
            self._detail_text.setText(
                build_detail_text(self._playlist.path_of(items[0]))
            )

    # ------------------------------------------------------------------
    # Playback
    # ------------------------------------------------------------------

    def _set_play_icon(self, playing: bool) -> None:
        self._icon_manager.set_play_icon(
            self._btn_play, playing,
            style   = self._config.get("icon_style", "neon"),
            primary = self._config["primary_color"],
            accent  = self._config["accent_color"],
        )

    def _update_ui_for_track(self, path: str) -> None:
        """Update info labels, album art, status bar and detail pane."""
        meta   = read_metadata(path)
        artist = meta["artist"]
        title  = meta["title"]
        br     = meta.get("bitrate", "")
        sr     = meta.get("sample_rate", "")
        year   = meta.get("year", "")

        self._label_artist.setText(artist)
        self._label_title.setText(title)
        self._label_tech.setText(f"{br}  ·  {sr}" if br and sr else br or sr)
        self._label_year.setText(year)

        self._album_art.update_for_track(
            path,
            style   = self._config.get("icon_style", "neon"),
            primary = self._config["primary_color"],
            accent  = self._config["accent_color"],
        )
        self._set_play_icon(True)
        self._detail_text.setText(build_detail_text(path))
        self.statusBar().showMessage(f"Playing: {artist} — {title}")

    def _on_volume_changed(self, v: int) -> None:
        self._playback.player.audio_set_volume(v)
        self._volume_label.setText(f"{v}%")

    def _toggle_mute(self) -> None:
        if self._btn_mute.isChecked():
            self._volume_before_mute = self._volume.value()
            self._playback.player.audio_set_volume(0)
        else:
            restored = getattr(self, "_volume_before_mute", 80)
            self._playback.player.audio_set_volume(restored)
            self._volume.setValue(restored)
        self._icon_manager.set_mute_icon(
            self._btn_mute, self._btn_mute.isChecked(),
            style   = self._config.get("icon_style", "neon"),
            primary = self._config["primary_color"],
            accent  = self._config["accent_color"],
        )

    def _reset_visualizations(self) -> None:
        """Reset all visualisation widgets to their blank/idle state."""
        self._spectrum.reset()
        self._spectrogram.reset()
        self._oscilloscope.reset()
        self._lissajous.reset()
        self._flux.reset()
        self._vumeter.reset()

    def _set_toggle_icon(self, btn, icon_name: str, active: bool) -> None:
        """Regenerate a toggle button icon with swapped colours when active."""
        self._icon_manager.set_toggle_icon(
            btn, icon_name, active,
            style   = self._config.get("icon_style", "neon"),
            primary = self._config["primary_color"],
            accent  = self._config["accent_color"],
        )

    def _toggle_shuffle(self) -> None:
        """Reads the button's new checked-state and hands it to
        PlaybackController (which owns _shuffle/_shuffle_order and the
        rebuild-on-toggle logic); only the icon repaint stays here, since
        PlaybackController doesn't know about buttons/IconManager — see
        PlaybackController.toggle_shuffle()'s docstring."""
        self._playback.toggle_shuffle(self._btn_shuffle.isChecked())
        self._set_toggle_icon(self._btn_shuffle, "icon_shuffle", self._playback.shuffle)

    def _toggle_repeat(self) -> None:
        """See _toggle_shuffle()'s docstring — same split."""
        self._playback.toggle_repeat(self._btn_repeat.isChecked())
        self._set_toggle_icon(self._btn_repeat, "icon_repeat", self._playback.repeat)

    # ------------------------------------------------------------------
    # FFT timer
    # ------------------------------------------------------------------

    def _update_fft(self) -> None:
        samples = self._loader.samples
        if samples is None:
            return
        sr = self._loader.sample_rate
        if sr and sr != self._known_sample_rate:
            self._known_sample_rate = sr
            self._spectrum.set_sample_rate(sr)
            self._spectrogram.set_sample_rate(sr)
        pos = self._playback.player.get_position()
        try:
            frame = compute_fft_frame(samples, pos, self._config["bar_count"])
        except Exception as e:
            print(f"[FFT] {e}")
            return
        if frame is None:
            return

        self._spectrum.set_bars(frame["bars"])
        self._spectrogram.add_column(frame["bars"])
        self._oscilloscope.set_samples(frame["mono"])
        self._lissajous.set_samples(frame["left"], frame["right"])
        self._flux.update_spectrum(frame["bars"])

        lv = float(np.sqrt(np.mean(np.square(np.array(frame["left"])))))
        rv = float(np.sqrt(np.mean(np.square(np.array(frame["right"])))))
        # Scale RMS: 0.3 RMS (loud/clipped material) → 1.0, with headroom below
        # Typical well-mastered loud track peaks ~0.25–0.35 RMS; quiet ~0.03–0.08
        scale = 2.0
        self._vumeter.set_levels(min(1.0, lv * scale), min(1.0, rv * scale))

    # ------------------------------------------------------------------
    # Equalizer
    # ------------------------------------------------------------------

    def _open_equalizer(self) -> None:
        eq_state = self._config.get("eq_state", {})
        # Always save state (even on Close), so it survives re-open and app restart
        self._config["eq_state"] = self._settings.open_equalizer_dialog(
            self._playback.player, eq_state, self)
        save_config(self._config)

    # ------------------------------------------------------------------
    # Keyboard events
    # ------------------------------------------------------------------

    def keyPressEvent(self, event) -> None:
        rc = self._config.get("shortcuts", DEFAULT_CONFIG["shortcuts"])

        if event.key() == Qt.Key.Key_Escape and self._search_bar.isVisible():
            self._close_search()
            return

        search_seq = QKeySequence(rc.get("search", "Ctrl+F"))
        if search_seq.matches(QKeySequence(event.keyCombination())) \
                == QKeySequence.SequenceMatch.ExactMatch:
            self._search_bar.show()
            self._search_field.setFocus()
            return

        if event.key() == Qt.Key.Key_Delete:
            removed = self._playlist.remove_selected()
            if removed:
                self.statusBar().showMessage(
                    f"{removed} track(s) removed. Ctrl+Z to undo."
                )
            return

        undo_seq = QKeySequence(rc.get("undo", "Ctrl+Z"))
        if undo_seq.matches(QKeySequence(event.keyCombination())) \
                == QKeySequence.SequenceMatch.ExactMatch:
            if self._playlist.undo_delete():
                self.statusBar().showMessage("Undo: track restored.")
            return

    # ------------------------------------------------------------------
    # Window-level drag-and-drop (replaces DropArea wrapper)
    # ------------------------------------------------------------------

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            self._on_drop(event)
        else:
            event.ignore()

    # ------------------------------------------------------------------
    # Close
    # ------------------------------------------------------------------

    def closeEvent(self, event) -> None:
        # self._ingestion.shutdown() runs FIRST, before self._playback's —
        # deliberately reversed from the pre-extraction ordering (which had
        # _playback.shutdown() first, _meta_worker.stop()/wait() last).
        # While the metadata worker is still draining, a track_ready signal
        # could still call self._playback.append_to_active_list() (see
        # PlaylistIngestionManager._on_track_ready's current_track branch);
        # PlaybackController.shutdown() doesn't clear current_track, so that
        # guard stays open even after VLC has been told to stop. Stopping
        # ingestion first bounds that window instead of leaving it open for
        # the rest of closeEvent(). See ui/playlist_ingestion_manager.py's
        # module docstring ("shutdown() ordering") and CLAUDE.md's
        # PlaylistIngestionManager entry for the empirically observed effect
        # on playlist persistence.
        self._ingestion.shutdown()

        # Deliberately self._playback.shutdown(), not .stop(): mirrors the
        # original sequence exactly (list_player + the two playback timers,
        # no icon/UI reset, no status message) — see shutdown()'s docstring
        # in ui/playback_controller.py for why reusing stop() here would be
        # a small but real behaviour change.
        self._playback.shutdown()
        self._timer_fft.stop()
        self._save_playlist()

        event.accept()
