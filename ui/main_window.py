"""
ui/main_window.py
MainWindow: wires together VLC playback, playlist, visualisations,
file browser, settings, equalizer, and keyboard shortcuts.
"""

from __future__ import annotations

import os
import sys
import re
import random
import threading
import numpy as np
import vlc
from PyQt6.QtCore    import QDir, QModelIndex, Qt, QTimer, QThread, pyqtSlot, pyqtSignal
from PyQt6.QtGui     import QColor, QKeySequence, QPixmap, QShortcut, QIcon, QPainter, QFileSystemModel, QPen
from PyQt6.QtWidgets import (
    QApplication, QComboBox, QDialog, QFileDialog, QHBoxLayout,
    QLabel, QMainWindow, QMessageBox, QSplitter, QSplitterHandle, QTabWidget,
    QTextEdit, QTreeView, QVBoxLayout, QWidget, QPushButton, 
    QLineEdit
)
from audio.engine     import (
    SampleLoader, build_detail_text, compute_fft_frame, read_album_art, read_metadata,
)
from config.settings  import (
    DEFAULT_CONFIG, PLAYLIST_PATH,
    is_audio, load_config, save_config,
)
from ui.dialogs       import EqualizerDialog, SettingsDialog
from ui.icon_manager  import IconManager
from ui.playlist      import PlaylistWidget
from ui.playlist_persistence import PlaylistPersistence, PlaylistPersistenceError
from ui.style         import build_stylesheet
from ui.visualizations import (
    LissajousWidget, OscilloscopeWidget, SpectralFluxWidget,
    SpectrogramWidget, SpectrumWidget, VUMeterWidget,
)
from ui.widgets       import ClickableSlider
from ui.icons        import render_no_art_pixmap, ICON_STYLES

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

def _natural_key(s: str):
        return [int(c) if c.isdigit() else c.lower() for c in re.split(r'(\d+)', s)]


class _MetadataWorker(QThread):
    """
    Background thread that reads audio metadata without blocking the UI.
    Push paths with enqueue() or enqueue_many().
    Emits track_ready(path, meta) on the Qt thread for each file.

    Call stop() then a bounded wait() to shut it down cleanly (see
    MainWindow.closeEvent()). stop() pushes a sentinel onto the queue so
    run() exits on its own after processing whatever is already queued —
    that covers the common case (thread idle or between reads). It cannot
    interrupt a read_metadata() call already in progress on a slow file;
    there's no safe way to preempt a blocking call from another thread in
    Python. That's why closeEvent()'s wait() is bounded rather than
    unconditional — the app must still be able to close promptly even if
    this thread is stuck, at the cost of Qt's "QThread: Destroyed while
    thread is still running" warning in that rare case.
    """
    track_ready = pyqtSignal(str, dict)

    _STOP = object()  # sentinel pushed onto the queue to end run()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        import threading
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


class MainWindow(QMainWindow):
    """Quark Audio Player — main application window."""
    _socket_file_received  = pyqtSignal(str)
    _vlc_next_item_signal  = pyqtSignal()   # fired from VLC thread → Qt slot
    _media_appended_signal = pyqtSignal(str, object)  # path, target_list — fired from append thread → Qt slot

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def __init__(self) -> None:
        super().__init__()
        self._vlc = vlc.Instance("--reset-plugins-cache")
        self.setWindowTitle("Quark Audio Player v0.6.6")
        app_icon_path = os.path.join(ASSETS_DIR, "icon_app.png")
        if os.path.exists(app_icon_path):
            self.setWindowIcon(QIcon(app_icon_path))
        self.setMinimumSize(900, 600)
        self.resize(1_100, 680)

        self._config        = load_config()
        self.setAcceptDrops(True)
        self._current_track = None           # int | None
        self._current_item  = None           # QTreeWidgetItem | None
        self._shuffle       = False
        self._repeat        = False
        self._shuffle_order: list[int] = []   # fixed random order, generated once
        self._shortcuts: dict[str, QShortcut] = {}

        # VLC — MediaPlayer handles EQ/volume; MediaListPlayer handles transitions
        self._player      = self._vlc.media_player_new()
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
        # _append_to_media_list()/_on_media_appended() for the full protocol.
        self._media_list_lock = threading.Lock()

        # Background audio sample loader
        self._loader = SampleLoader()
        self._known_sample_rate = 0  # last rate pushed to the freq-axis widgets

        # Async metadata worker — keeps UI responsive while reading tags
        self._meta_worker = _MetadataWorker(self)
        self._meta_worker.track_ready.connect(self._on_track_ready)
        self._pending_play: str | None = None   # path to auto-play once added

        # Timers
        self._timer_progress = QTimer()
        self._timer_progress.setInterval(500)
        self._timer_progress.timeout.connect(self._update_progress)

        self._timer_fft = QTimer()
        self._timer_fft.setInterval(1_000 // self._config["fps"])
        self._timer_fft.timeout.connect(self._update_fft)

        # Short grace period before declaring end-of-playlist: if a track is
        # appended within this window, _on_media_appended cancels the timer
        # and the visualisations never freeze.
        self._timer_end_grace = QTimer()
        self._timer_end_grace.setSingleShot(True)
        self._timer_end_grace.setInterval(200)
        self._timer_end_grace.timeout.connect(self._on_end_grace_timeout)


        self._playlist_persistence = PlaylistPersistence(PLAYLIST_PATH)
        self._icon_manager = IconManager(ASSETS_DIR)

        self._build_ui()
        self._apply_config()
        self._apply_shortcuts()
        self._load_playlist()

        self._socket_file_received.connect(self._open_from_socket)
        self._vlc_next_item_signal.connect(self._on_vlc_next_item)
        self._media_appended_signal.connect(self._on_media_appended)

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
        left_panel = QWidget()
        left_panel.setObjectName("leftPanel")
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(0)

        lbl_explorer = QLabel("  File Browser")
        lbl_explorer.setObjectName("sectionLabel")
        left_layout.addWidget(lbl_explorer)

        self._root_combo = QComboBox()
        self._root_combo.addItem("Home", QDir.homePath())

        drive_icon = self.style().standardIcon(
            self.style().StandardPixmap.SP_DriveHDIcon
        )

        if sys.platform == "win32":
            # Énumère toutes les lettres de lecteur montées (C:\, D:\, etc.)
            import string
            for letter in string.ascii_uppercase:
                drive = f"{letter}:\\"
                if os.path.exists(drive):
                    self._root_combo.addItem(drive_icon, drive, drive)
        else:
            # Linux : points de montage classiques
            user = os.environ.get("USER", "")
            self._root_combo.addItem("Root /", "/")
            for base in [f"/run/media/{user}", "/mnt"]:
                if os.path.isdir(base):
                    for entry in os.listdir(base):
                        path = os.path.join(base, entry)
                        if os.path.ismount(path):
                            self._root_combo.addItem(drive_icon, entry, path)

        self._root_combo.currentIndexChanged.connect(self._change_root)
        left_layout.addWidget(self._root_combo)
        self._fs_model = QFileSystemModel()
        self._fs_model.setRootPath(QDir.homePath())
        self._file_tree = QTreeView()
        self._file_tree.setModel(self._fs_model)
        self._file_tree.setRootIndex(self._fs_model.index(QDir.homePath()))
        self._file_tree.setColumnWidth(0, 220)
        for col in [1, 2, 3]:
            self._file_tree.hideColumn(col)
        self._file_tree.setHeaderHidden(True)
        self._file_tree.setSelectionMode(QTreeView.SelectionMode.ExtendedSelection)
        self._file_tree.doubleClicked.connect(self._on_file_double_click)
        self._file_tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._file_tree.customContextMenuRequested.connect(self._on_file_context_menu)
        left_layout.addWidget(self._file_tree)

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
            lambda idx: self._play_item(self._playlist.topLevelItem(idx.row()))
        )
        self._playlist.itemSelectionChanged.connect(self._on_selection_changed)
        self._playlist.order_changed.connect(self._resync_current_track)

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
        h_splitter.addWidget(left_panel)
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

        self._album_art = QLabel()
        self._album_art.setFixedSize(150, 150)
        self._album_art.setObjectName("albumArt")
        self._album_art.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._album_art.setCursor(Qt.CursorShape.PointingHandCursor)
        self._album_art.mousePressEvent = lambda _e: self._open_art_viewer()
        art_col.addWidget(self._album_art)
        self._full_art_pixmap: QPixmap | None = None

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
        self._progress.sliderMoved.connect(self._seek)
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
        self._btn_prev     = self._ctrl_btn("|<",       self._prev_track)
        self._btn_play     = self._ctrl_btn(">",        self._toggle_play)
        self._btn_stop     = self._ctrl_btn("[]",       self._stop)
        self._btn_next     = self._ctrl_btn(">|",       self._next_track)
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
        self._player.audio_set_volume(80)

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

        self._show_no_art()
        
    def _refresh_icons(self) -> None:
        """Retint all button icons to match the current background."""
        self._icon_manager.refresh_all(
            style   = self._config.get("icon_style", "neon"),
            primary = self._config["primary_color"],
            accent  = self._config["accent_color"],
            playing = self._player.is_playing(),
            muted   = self._btn_mute.isChecked(),
            shuffle_active = self._shuffle,
            repeat_active  = self._repeat,
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
        # Update splitter handle colours to match the new theme
        from config.settings import derive_color
        _dark = (sum(int(cf.lstrip("#")[i*2:i*2+2], 16) for i in range(3)) / 3) < 128
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
        if hasattr(self, "_album_art") and self._full_art_pixmap is None:
            self._show_no_art()

    def _apply_shortcuts(self) -> None:
        for sc in self._shortcuts.values():
            sc.setEnabled(False)
        self._shortcuts.clear()
        mapping = {
            "play_pause": self._toggle_play,
            "next":       self._next_track,
            "previous":   self._prev_track,
        }
        shortcuts = self._config.get("shortcuts", DEFAULT_CONFIG["shortcuts"])
        for key, slot in mapping.items():
            seq = shortcuts.get(key, DEFAULT_CONFIG["shortcuts"][key])
            sc  = QShortcut(QKeySequence(seq), self)
            sc.activated.connect(slot)
            self._shortcuts[key] = sc

    def _open_settings(self) -> None:
        dlg = SettingsDialog(self._config, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._config = dlg.config
            self._apply_config()
            self._apply_shortcuts()
            save_config(self._config)

    # ------------------------------------------------------------------
    # File browser
    # ------------------------------------------------------------------
    @pyqtSlot(str)
    def _open_from_socket(self, path: str) -> None:
        self.raise_()
        self.activateWindow()
        item = self._playlist.item_by_path(path)
        if item is not None:
            if not self._player.is_playing():
                self._play_item(item)
        else:
            self._add_file(path, play_when_ready=not self._player.is_playing())

    def _change_root(self, index: int) -> None:
        path = self._root_combo.itemData(index)
        self._fs_model.setRootPath(path)
        self._file_tree.setRootIndex(self._fs_model.index(path))

    def _on_file_double_click(self, index: QModelIndex) -> None:
        path = self._fs_model.filePath(index)
        if os.path.isfile(path) and is_audio(path):
            self._add_file(path)
            self.statusBar().showMessage(f"Added: {os.path.basename(path)}")

    def _on_file_context_menu(self, position) -> None:
        from PyQt6.QtWidgets import QMenu
        indexes = self._file_tree.selectedIndexes()
        if not indexes:
            index = self._file_tree.indexAt(position)
            if not index.isValid():
                return
            indexes = [index]

        # Collect unique paths (selectedIndexes may repeat per column)
        seen = set()
        paths = []
        for idx in indexes:
            p = self._fs_model.filePath(idx)
            if p not in seen:
                seen.add(p)
                paths.append(p)

        audio_paths = [p for p in paths if os.path.isfile(p) and is_audio(p)]
        dir_paths   = [p for p in paths if os.path.isdir(p)]

        menu = QMenu(self)
        if audio_paths:
            n = len(audio_paths)
            label = f"Add {n} track{'s' if n > 1 else ''} to playlist"
            action = menu.addAction(label)
            action.triggered.connect(lambda: self._add_files(audio_paths))
        if dir_paths:
            for d in dir_paths:
                action = menu.addAction(f'Add folder "{os.path.basename(d)}" to playlist')
                action.triggered.connect(lambda checked=False, folder=d: self._add_folder(folder))
        if not menu.isEmpty():
            menu.exec(self._file_tree.viewport().mapToGlobal(position))

    def _add_files(self, paths: list) -> None:
        new_paths = [p for p in paths if self._playlist.item_by_path(p) is None]
        if not new_paths:
            return
        self._meta_worker.enqueue_many(new_paths)
        n = len(new_paths)
        self.statusBar().showMessage(f"Loading {n} track{'s' if n > 1 else ''}…")

    def _add_folder(self, folder: str) -> None:
        paths = [
            os.path.join(folder, name)
            for name in sorted(os.listdir(folder), key=_natural_key)
            if is_audio(os.path.join(folder, name))
        ]
        new_paths = [p for p in paths if self._playlist.item_by_path(p) is None]
        if new_paths:
            self._meta_worker.enqueue_many(new_paths)

    def _add_file(self, path: str, play_when_ready: bool = False) -> None:
        """Enqueue path for async metadata loading.
        If play_when_ready is True and the player is idle, the track will
        start playing as soon as its metadata arrives.
        """
        if self._playlist.item_by_path(path) is not None:
            return  # duplicate
        if play_when_ready and not self._player.is_playing():
            self._pending_play = path
        self._meta_worker.enqueue(path)
        
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
        self.statusBar().showMessage(
            f"Added: {meta['artist']} — {meta['title']}"
        )
        if self._pending_play == path:
            self._pending_play = None
            item = self._playlist.item_by_path(path)
            if item is not None:
                self._play_item(item)
        elif self._current_track is not None:
            # A track was added while playback is active: append it directly
            # to the existing VLC media list instead of rebuilding from scratch.
            # Rebuilding calls set_media_list() which resets VLC's internal
            # position pointer and causes tracks to be skipped or replayed.
            self._append_to_media_list(path)

    # ------------------------------------------------------------------
    # Append a new track to the live VLC media list
    # ------------------------------------------------------------------

    def _append_to_media_list(self, path: str) -> None:
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
            self._append_to_media_list(path)
            return

        item = self._playlist.item_by_path(path)
        if item is None:
            return  # track was removed from the playlist in the meantime

        # Cancel any pending end-of-playlist grace timer — a new track just
        # arrived so we must not freeze/reset the visualisations.
        self._timer_end_grace.stop()

        if self._player.is_playing():
            self._timer_progress.start()
            self._timer_fft.start()
        else:
            state = self._player.get_state()
            if state in (vlc.State.Ended, vlc.State.Stopped, vlc.State.NothingSpecial):
                # Playlist had ended — play the newly added track directly
                # instead of _list_player.play() which restarts from index 0.
                self._play_item(item)

    def _on_drop(self, event) -> None:
        for url in event.mimeData().urls():
            path = url.toLocalFile()
            if os.path.isfile(path) and is_audio(path):
                self._add_file(path)
            elif os.path.isdir(path):
                self._add_folder(path)
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

    def _update_album_art(self, path: str) -> None:
        original, display = read_album_art(path)
        self._full_art_bytes = original  # raw bytes for lossless save
        if display:
            px = QPixmap()
            px.loadFromData(display)
            if not px.isNull():
                # Downscale giant covers for display only (original kept intact)
                if px.width() > 1200 or px.height() > 1200:
                    px = px.scaled(1200, 1200,
                                   Qt.AspectRatioMode.KeepAspectRatio,
                                   Qt.TransformationMode.SmoothTransformation)
                self._full_art_pixmap = px
                thumb = px.scaled(150, 150,
                                  Qt.AspectRatioMode.KeepAspectRatio,
                                  Qt.TransformationMode.SmoothTransformation)
                self._album_art.setPixmap(thumb)
                self._album_art.setText("")
                return
        self._full_art_bytes = None
        self._full_art_pixmap = None
        self._show_no_art()

    def _open_art_viewer(self) -> None:
        """Fullscreen-ish dialog showing the album art with a Save button."""
        if not self._full_art_pixmap or self._full_art_pixmap.isNull():
            return
        from PyQt6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QScrollArea
        dlg = QDialog(self)
        dlg.setWindowTitle("Album Art")
        dlg.resize(700, 730)
        vlay = QVBoxLayout(dlg)
        vlay.setContentsMargins(10, 10, 10, 10)
        vlay.setSpacing(8)

        img_label = QLabel()
        img_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        img_label.setPixmap(self._full_art_pixmap.scaled(
            660, 640,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        ))
        scroll = QScrollArea()
        scroll.setWidget(img_label)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(scroll.Shape.NoFrame)
        vlay.addWidget(scroll)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_save = QPushButton("Save image…")

        def _save():
            path, _ = QFileDialog.getSaveFileName(
                dlg, "Save album art",
                os.path.expanduser("~/cover.jpg"),
                "JPEG (*.jpg);;PNG (*.png)",
            )
            if path:
                if self._full_art_bytes:
                    with open(path, "wb") as f:
                        f.write(self._full_art_bytes)
                else:
                    fmt = "PNG" if path.lower().endswith(".png") else "JPEG"
                    self._full_art_pixmap.save(path, fmt, 95)
                self.statusBar().showMessage(f"Saved: {os.path.basename(path)}")

        btn_save.clicked.connect(_save)
        btn_close = QPushButton("Close")
        btn_close.clicked.connect(dlg.accept)
        btn_row.addWidget(btn_save)
        btn_row.addWidget(btn_close)
        vlay.addLayout(btn_row)
        dlg.exec()

    def _show_no_art(self) -> None:
        px = render_no_art_pixmap(
            style   = self._config.get("icon_style", "neon"),
            primary = self._config["primary_color"],
            accent  = self._config["accent_color"],
            size    = 150,
        )
        self._album_art.setPixmap(px)
        self._album_art.setText("")

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


    def _play_item(self, item) -> None:
        if item is None:
            return
        path = self._playlist.path_of(item)
        if not os.path.exists(path):
            self.statusBar().showMessage(f"File not found: {path}")
            return
        if not os.access(path, os.R_OK):
            self.statusBar().showMessage(f"Permission denied: {os.path.basename(path)}")
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
        self._timer_fft.start()
        self._loader.load(path)

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
        # _append_to_media_list()'s background thread uses to re-read
        # self._media_list, so it never sees a half-swapped reference —
        # see _append_to_media_list()/_on_media_appended() for the full
        # protocol this is part of.
        with self._media_list_lock:
            self._media_list = ml
            self._list_player.set_media_list(ml)

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

        self._update_album_art(path)
        self._set_play_icon(True)
        self._detail_text.setText(build_detail_text(path))
        self.statusBar().showMessage(f"Playing: {artist} — {title}")

    def _apply_eq(self) -> None:
        """Re-attach equalizer to the current MediaPlayer (survives track changes)."""
        eq_state = self._config.get("eq_state", {})
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
                self._loader.load(path)
                self._timer_progress.start()
                self._timer_fft.start()
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
        self._loader.load(new_path)
        self._timer_progress.start()
        self._timer_fft.start()

    def _resync_current_track(self) -> None:
        """Recompute _current_track row index after a drag-and-drop reorder."""
        if self._current_item is not None:
            self._current_track = self._playlist.indexOfTopLevelItem(self._current_item)

    def _toggle_play(self) -> None:
        if self._player.is_playing():
            self._list_player.pause()
            self._set_play_icon(False)
            self._timer_progress.stop()
            self._timer_fft.stop()
        else:
            if self._current_track is None and self._playlist.topLevelItemCount() > 0:
                self._play_item(self._playlist.topLevelItem(0))
            else:
                self._list_player.play()
                self._set_play_icon(True)
                self._timer_progress.start()
                self._timer_fft.start()

    def _on_volume_changed(self, v: int) -> None:
        self._player.audio_set_volume(v)
        self._volume_label.setText(f"{v}%")

    def _toggle_mute(self) -> None:
        if self._btn_mute.isChecked():
            self._volume_before_mute = self._player.audio_get_volume()
            self._player.audio_set_volume(0)
        else:
            restored = getattr(self, "_volume_before_mute", 80)
            self._player.audio_set_volume(restored)
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

    def _stop(self) -> None:
        self._list_player.stop()
        self._set_play_icon(False)
        self._progress.setValue(0)
        self._timer_progress.stop()
        self._timer_fft.stop()
        self._timer_end_grace.stop()
        self._reset_visualizations()
        self.statusBar().showMessage("Stopped.")

    def _next_track(self) -> None:
        if self._current_track is None:
            return
        if self._repeat:
            self._play_item(self._playlist.item_at_row(self._current_track))
            return
        result = self._list_player.next()
        if result == -1:
            self._list_player.stop()
            self._timer_fft.stop()
            self._timer_progress.stop()
            self._set_play_icon(False)
            self._progress.setValue(0)
            self._time_label.setText("0:00 / 0:00")
            self._reset_visualizations()
            self.statusBar().showMessage("End of playlist.")

    def _prev_track(self) -> None:
        if self._current_track is None:
            return
        if self._current_track == 0:
            self._player.set_position(0.0)
            return
        self._play_item(self._playlist.item_at_row(self._current_track - 1))

    def _set_toggle_icon(self, btn, icon_name: str, active: bool) -> None:
        """Regenerate a toggle button icon with swapped colours when active."""
        self._icon_manager.set_toggle_icon(
            btn, icon_name, active,
            style   = self._config.get("icon_style", "neon"),
            primary = self._config["primary_color"],
            accent  = self._config["accent_color"],
        )

    def _toggle_shuffle(self) -> None:
        self._shuffle = self._btn_shuffle.isChecked()
        self._set_toggle_icon(self._btn_shuffle, "icon_shuffle", self._shuffle)
        if not self._shuffle:
            self._shuffle_order = []   # clear stale order
        if self._current_track is not None:
            self._rebuild_media_list(from_row=self._current_track, reshuffle=True)

    def _toggle_repeat(self) -> None:
        self._repeat = self._btn_repeat.isChecked()
        self._set_toggle_icon(self._btn_repeat, "icon_repeat", self._repeat)
        mode = vlc.PlaybackMode.repeat if self._repeat else vlc.PlaybackMode.default
        self._list_player.set_playback_mode(mode)

    def _seek(self, value: int) -> None:
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
        self._timer_fft.stop()
        self._set_play_icon(False)
        self._progress.setValue(0)
        self._time_label.setText("0:00 / 0:00")
        self._reset_visualizations()
        self.statusBar().showMessage("End of playlist.")

    @staticmethod
    def _ms_to_str(ms: int) -> str:
        s = max(0, ms // 1_000)
        return f"{s // 60}:{s % 60:02d}"

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
        pos = self._player.get_position()
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
        dlg = EqualizerDialog(self._player, eq_state, self)
        dlg.exec()
        # Always save state (even on Close), so it survives re-open and app restart
        self._config["eq_state"] = dlg.eq_state
        save_config(self._config)

    # ------------------------------------------------------------------
    # VLC error callback
    # ------------------------------------------------------------------

    def _on_vlc_error(self, _event) -> None:
        self.statusBar().showMessage("Playback error: corrupt or unsupported file.")
        self._list_player.stop()
        self._set_play_icon(False)
        self._timer_progress.stop()
        self._timer_fft.stop()
        self._reset_visualizations()

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

        if event.key() == Qt.Key.Key_Return and self._file_tree.hasFocus():
            for index in self._file_tree.selectedIndexes():
                path = self._fs_model.filePath(index)
                if os.path.isdir(path):
                    self._add_folder(path)
                elif is_audio(path):
                    self._add_file(path)
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
        self._list_player.stop()
        self._timer_fft.stop()
        self._timer_progress.stop()
        self._timer_end_grace.stop()
        self._save_playlist()

        # Ask the metadata worker to stop and give it a bounded moment to do
        # so cleanly. If it's stuck in a slow read_metadata() call, wait()
        # times out and we close anyway rather than hang the app shut down
        # (see _MetadataWorker's docstring for why that's the trade-off).
        self._meta_worker.stop()
        self._meta_worker.wait(2000)

        event.accept()
