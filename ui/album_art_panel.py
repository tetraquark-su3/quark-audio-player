"""
ui/album_art_panel.py
Self-contained QLabel subclass owning the album-art display, fullscreen-ish
viewer dialog, and "no art" placeholder state — extracted from MainWindow
(_update_album_art / _open_art_viewer / _show_no_art).

Note on testability: unlike ui/playlist_persistence.py, this module is NOT
Qt-free. Its state transitions are all entangled with real QPixmap/QDialog
objects (QPixmap.isNull(), .width(), .scaled(), a modal QDialog.exec()) —
there is no pure-Python decision logic worth pulling out separately, unlike
ui/icon_manager.py's toggle_icon_colors(). This class ships with no unit
tests under the current setup (no pytest-qt, no QApplication fixture); the
extraction's value is architectural (isolating ~90 lines of self-contained
widget logic out of MainWindow), not a testability win.
"""

from __future__ import annotations

import os

from PyQt6.QtCore    import Qt
from PyQt6.QtGui     import QPixmap
from PyQt6.QtWidgets import (
    QDialog, QFileDialog, QHBoxLayout, QLabel, QPushButton,
    QScrollArea, QVBoxLayout, QWidget,
)

from audio.engine import read_album_art
from ui.icons      import render_no_art_pixmap

THUMB_SIZE = 150
MAX_DISPLAY_SIZE = 1200
VIEWER_IMAGE_SIZE = (660, 640)


class AlbumArtPanel(QLabel):
    """Displays the current track's album art (or a themed placeholder),
    and opens a fullscreen-ish viewer/save dialog on click."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(THUMB_SIZE, THUMB_SIZE)
        self.setObjectName("albumArt")
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._pixmap: QPixmap | None = None    # full-size, display-scaled
        self._art_bytes: bytes | None = None    # raw bytes, for lossless save

    def mousePressEvent(self, _e) -> None:  # noqa: N802 (Qt override)
        self._open_viewer()

    @property
    def has_art(self) -> bool:
        return self._pixmap is not None

    def update_for_track(self, path: str, *, style: str, primary: str, accent: str) -> None:
        """Load and display the embedded album art for *path*, or fall back
        to the themed placeholder if the track has none."""
        original, display = read_album_art(path)
        self._art_bytes = original
        if display:
            px = QPixmap()
            px.loadFromData(display)
            if not px.isNull():
                # Downscale giant covers for display only (original kept intact)
                if px.width() > MAX_DISPLAY_SIZE or px.height() > MAX_DISPLAY_SIZE:
                    px = px.scaled(MAX_DISPLAY_SIZE, MAX_DISPLAY_SIZE,
                                   Qt.AspectRatioMode.KeepAspectRatio,
                                   Qt.TransformationMode.SmoothTransformation)
                self._pixmap = px
                thumb = px.scaled(THUMB_SIZE, THUMB_SIZE,
                                  Qt.AspectRatioMode.KeepAspectRatio,
                                  Qt.TransformationMode.SmoothTransformation)
                self.setPixmap(thumb)
                self.setText("")
                return
        self._art_bytes = None
        self._pixmap = None
        self.show_no_art(style, primary, accent)

    def show_no_art(self, style: str, primary: str, accent: str) -> None:
        """Render the themed 'no art' placeholder. Used for the initial
        state, the no-art fallback, and theme refreshes."""
        px = render_no_art_pixmap(
            style   = style,
            primary = primary,
            accent  = accent,
            size    = THUMB_SIZE,
        )
        self.setPixmap(px)
        self.setText("")

    def _open_viewer(self) -> None:
        """Fullscreen-ish dialog showing the album art with a Save button."""
        if not self._pixmap or self._pixmap.isNull():
            return
        dlg = QDialog(self)
        dlg.setWindowTitle("Album Art")
        dlg.resize(700, 730)
        vlay = QVBoxLayout(dlg)
        vlay.setContentsMargins(10, 10, 10, 10)
        vlay.setSpacing(8)

        img_label = QLabel()
        img_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        img_label.setPixmap(self._pixmap.scaled(
            *VIEWER_IMAGE_SIZE,
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
                if self._art_bytes:
                    with open(path, "wb") as f:
                        f.write(self._art_bytes)
                else:
                    fmt = "PNG" if path.lower().endswith(".png") else "JPEG"
                    self._pixmap.save(path, fmt, 95)
                window = self.window()
                if hasattr(window, "statusBar"):
                    window.statusBar().showMessage(f"Saved: {os.path.basename(path)}")

        btn_save.clicked.connect(_save)
        btn_close = QPushButton("Close")
        btn_close.clicked.connect(dlg.accept)
        btn_row.addWidget(btn_save)
        btn_row.addWidget(btn_close)
        vlay.addLayout(btn_row)
        dlg.exec()
