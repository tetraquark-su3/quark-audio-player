"""
ui/file_browser_panel.py
Self-contained QWidget owning the file-browser column: the root selector
(Home / Windows drive letters / Linux mount points), the QFileSystemModel-
backed QTreeView, and their double-click / context-menu / Enter-key wiring —
extracted from MainWindow (_change_root, _on_file_double_click,
_on_file_context_menu, plus the Key_Return branch formerly living in
MainWindow.keyPressEvent).

Does not touch the playlist directly: it emits signals for MainWindow (or a
future PlaylistIngestionManager — see CLAUDE.md, that extraction still owns
_add_file/_add_files/_add_folder) to act on. This mirrors how
ui/playlist.py already talks to MainWindow (order_changed, doubleClicked)
rather than taking constructor-injected callbacks — chosen explicitly so
MainWindow can re-point these connections to a different owner later
without FileBrowserPanel itself changing.

Note on testability: list_mount_roots() is the one piece of Qt-free logic
here (OS drive/mount-point enumeration, previously inlined into the
QComboBox-population loop) and is unit-tested in
tests/test_file_browser_panel.py. Everything else — the QTreeView/
QFileSystemModel wiring, the context menu, keyPressEvent — needs a live
QApplication and ships without tests, the same gap as ui/icon_manager.py
and ui/album_art_panel.py.
"""

from __future__ import annotations

import os
import string
import sys

from PyQt6.QtCore    import QDir, QModelIndex, Qt, pyqtSignal
from PyQt6.QtGui     import QFileSystemModel
from PyQt6.QtWidgets import (
    QComboBox, QLabel, QMenu, QTreeView, QVBoxLayout, QWidget,
)

from config.settings import is_audio


def list_mount_roots() -> list[tuple[str, str]]:
    """Return [(label, path), ...] of OS-specific browsable roots, on top
    of the always-present Home entry: Windows drive letters (C:\\, D:\\, …),
    or Linux mount points under /run/media/$USER and /mnt. Pure filesystem
    probing — no Qt involved, safe to unit-test without a QApplication.
    """
    roots: list[tuple[str, str]] = []
    if sys.platform == "win32":
        for letter in string.ascii_uppercase:
            drive = f"{letter}:\\"
            if os.path.exists(drive):
                roots.append((drive, drive))
    else:
        roots.append(("Root /", "/"))
        user = os.environ.get("USER", "")
        for base in [f"/run/media/{user}", "/mnt"]:
            if os.path.isdir(base):
                for entry in os.listdir(base):
                    path = os.path.join(base, entry)
                    if os.path.ismount(path):
                        roots.append((entry, path))
    return roots


class FileBrowserPanel(QWidget):
    """File-browser column: root selector + file tree. Emits requests to
    add files/folders to the playlist instead of touching it directly."""

    add_file_requested   = pyqtSignal(str)         # single audio file (double-click, Enter)
    add_files_requested  = pyqtSignal(list)        # multiple audio files (context menu)
    add_folder_requested = pyqtSignal(str)         # a folder (context menu, Enter)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("leftPanel")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        lbl_explorer = QLabel("  File Browser")
        lbl_explorer.setObjectName("sectionLabel")
        layout.addWidget(lbl_explorer)

        self._root_combo = QComboBox()
        self._root_combo.addItem("Home", QDir.homePath())
        drive_icon = self.style().standardIcon(
            self.style().StandardPixmap.SP_DriveHDIcon
        )
        for label, path in list_mount_roots():
            self._root_combo.addItem(drive_icon, label, path)
        self._root_combo.currentIndexChanged.connect(self._change_root)
        layout.addWidget(self._root_combo)

        self._fs_model = QFileSystemModel()
        self._fs_model.setRootPath(QDir.homePath())
        self._tree = QTreeView()
        self._tree.setModel(self._fs_model)
        self._tree.setRootIndex(self._fs_model.index(QDir.homePath()))
        self._tree.setColumnWidth(0, 220)
        for col in [1, 2, 3]:
            self._tree.hideColumn(col)
        self._tree.setHeaderHidden(True)
        self._tree.setSelectionMode(QTreeView.SelectionMode.ExtendedSelection)
        self._tree.doubleClicked.connect(self._on_double_click)
        self._tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._tree.customContextMenuRequested.connect(self._on_context_menu)
        layout.addWidget(self._tree)

    def _change_root(self, index: int) -> None:
        path = self._root_combo.itemData(index)
        self._fs_model.setRootPath(path)
        self._tree.setRootIndex(self._fs_model.index(path))

    def _on_double_click(self, index: QModelIndex) -> None:
        path = self._fs_model.filePath(index)
        if os.path.isfile(path) and is_audio(path):
            self.add_file_requested.emit(path)

    def _on_context_menu(self, position) -> None:
        indexes = self._tree.selectedIndexes()
        if not indexes:
            index = self._tree.indexAt(position)
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
            action.triggered.connect(lambda: self.add_files_requested.emit(audio_paths))
        if dir_paths:
            for d in dir_paths:
                action = menu.addAction(f'Add folder "{os.path.basename(d)}" to playlist')
                action.triggered.connect(
                    lambda checked=False, folder=d: self.add_folder_requested.emit(folder))
        if not menu.isEmpty():
            menu.exec(self._tree.viewport().mapToGlobal(position))

    def keyPressEvent(self, event) -> None:  # noqa: N802 (Qt override)
        if event.key() == Qt.Key.Key_Return and self._tree.hasFocus():
            for index in self._tree.selectedIndexes():
                path = self._fs_model.filePath(index)
                if os.path.isdir(path):
                    self.add_folder_requested.emit(path)
                elif is_audio(path):
                    self.add_file_requested.emit(path)
            return
        super().keyPressEvent(event)
