"""
ui/playlist.py
PlaylistWidget: QTreeWidget subclass managing the track list.

Sorting is O(n log n) in the Qt layer but duplicate-checking on insertion
uses a Python set for O(1) average-case lookup.
"""

from __future__ import annotations

import os
from typing import Optional

from PyQt6.QtWidgets import QTreeWidget, QTreeWidgetItem, QMenu, QHeaderView
from PyQt6.QtCore    import Qt, pyqtSignal, QMimeData, QPoint
from PyQt6.QtGui     import QDrag, QPainter, QColor, QPen, QPolygon

class PlaylistItem(QTreeWidgetItem):
    def __lt__(self, other: QTreeWidgetItem) -> bool:
        col = self.treeWidget().sortColumn()
        if col == PlaylistWidget.COL_TRACK:
            try:
                return int(self.text(col)) < int(other.text(col))
            except ValueError:
                pass
        if col == PlaylistWidget.COL_DUR:
            def to_seconds(s: str) -> int:
                parts = s.split(":")
                try:
                    return int(parts[0]) * 60 + int(parts[1])
                except (ValueError, IndexError):
                    return 0
            return to_seconds(self.text(col)) < to_seconds(other.text(col))
        return self.text(col) < other.text(col)

class SortableHeader(QHeaderView):
    def __init__(self, parent=None):
        super().__init__(Qt.Orientation.Horizontal, parent)

    def paintSection(self, painter, rect, logical_index):
        super().paintSection(painter, rect, logical_index)
        if logical_index != self.sortIndicatorSection():
            return
        asc = self.sortIndicatorOrder() == Qt.SortOrder.AscendingOrder
        x = rect.right() - 14
        y = rect.center().y()
        painter.save()
        painter.setRenderHint(painter.RenderHint.Antialiasing)
        painter.setBrush(QColor("#a8c0ff"))
        painter.setPen(Qt.PenStyle.NoPen)
        if asc:
            painter.drawPolygon(QPolygon([
                QPoint(x,     y + 3),
                QPoint(x + 8, y + 3),
                QPoint(x + 4, y - 3),
            ]))
        else:
            painter.drawPolygon(QPolygon([
                QPoint(x,     y - 3),
                QPoint(x + 8, y - 3),
                QPoint(x + 4, y + 3),
            ]))
        painter.restore()

class PlaylistWidget(QTreeWidget):
    """
    A sortable, searchable playlist with:
    - O(1) duplicate detection via a path hash-set
    - History stack for undo-delete (capped at MAX_HISTORY)
    """

    order_changed = pyqtSignal()  # emitted after drag-and-drop reorder

    MAX_HISTORY = 100

    COLUMNS    = ["#", "Artist", "Album", "Title", "Duration"]
    COL_TRACK  = 0
    COL_ARTIST = 1
    COL_ALBUM  = 2
    COL_TITLE  = 3
    COL_DUR    = 4

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._drop_color = QColor("#e94560")  # overridable via set_accent_color()
        self.setHeader(SortableHeader(self)) 
        self.setColumnCount(len(self.COLUMNS))
        self.setHeaderLabels(self.COLUMNS)
        self.header().setSortIndicatorShown(True)
        self.header().setSectionsClickable(True)
        self.header().setSectionsMovable(False)
        self.header().sectionClicked.connect(self._on_header_clicked)
        self.setSortingEnabled(False)
        # Qt6: setSortingEnabled(False) silently resets sectionsClickable to False.
        # We must re-enable it explicitly every time after calling setSortingEnabled(False).
        self.header().setSectionsClickable(True)
        self.setRootIsDecorated(False)
        self.setAlternatingRowColors(False)
        self.setSelectionMode(QTreeWidget.SelectionMode.ExtendedSelection)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._on_context_menu)
        self.setDragDropMode(QTreeWidget.DragDropMode.NoDragDrop)
        self.viewport().setAcceptDrops(True)
        self.setAcceptDrops(True)
        self._drag_start_row:     int | None = None
        self._drop_indicator_row: int | None = None

        # O(1) duplicate check: maps absolute_path -> QTreeWidgetItem
        self._path_index: dict[str, PlaylistItem] = {}

        # Undo history: list of (row, track, artist, album, title, dur, path)
        self._history: list[tuple] = []

        # Sort state — NOT read from header because setSortingEnabled(False)
        # silently resets sortIndicatorSection() to 0 in Qt6.
        self._sort_col:   int          = -1
        self._sort_order: Qt.SortOrder = Qt.SortOrder.AscendingOrder
    
    def set_accent_color(self, color: str) -> None:
        self._drop_color = QColor(color)

    # ------------------------------------------------------------------
    # Header / sorting
    # ------------------------------------------------------------------

    def _on_header_clicked(self, col: int) -> None:
        """Sort on click; toggle direction on same column."""
        # Read from our own variables — NOT from header.sortIndicatorSection(),
        # because setSortingEnabled(False) silently resets it to 0 in Qt6.
        if col == self._sort_col:
            order = (Qt.SortOrder.DescendingOrder
                     if self._sort_order == Qt.SortOrder.AscendingOrder
                     else Qt.SortOrder.AscendingOrder)
        else:
            order = Qt.SortOrder.AscendingOrder
        self.setSortingEnabled(True)
        self.sortItems(col, order)
        self.setSortingEnabled(False)
        # setSortingEnabled(False) resets both sectionsClickable and the
        # visual sort indicator — restore both explicitly.
        self.header().setSectionsClickable(True)
        self.header().setSortIndicator(col, order)
        self._sort_col   = col
        self._sort_order = order

    # ------------------------------------------------------------------
    # Context menu
    # ------------------------------------------------------------------

    def _on_context_menu(self, position) -> None:
        items = self.selectedItems()
        if not items:
            return
        menu = QMenu(self)
        action = menu.addAction(f"Remove {len(items)} track(s)")
        action.triggered.connect(self.remove_selected)
        menu.exec(self.viewport().mapToGlobal(position))

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def item_by_path(self, path: str) -> Optional[QTreeWidgetItem]:
        return self._path_index.get(path)

    def add_track(self, path: str, track: str, artist: str, album: str,
                  title: str, duration: str) -> Optional[QTreeWidgetItem]:
        if path in self._path_index:
            return None
        item = PlaylistItem([track, artist, album, title, duration])
        item.setData(0, Qt.ItemDataRole.UserRole, path)
        self.addTopLevelItem(item)
        self._path_index[path] = item
        return item

    def remove_selected(self) -> int:
        items = self.selectedItems()
        for item in items:
            row  = self.indexOfTopLevelItem(item)
            path = item.data(0, Qt.ItemDataRole.UserRole)
            self._history.append((
                row,
                item.text(self.COL_TRACK),
                item.text(self.COL_ARTIST),
                item.text(self.COL_ALBUM),
                item.text(self.COL_TITLE),
                item.text(self.COL_DUR),
                path,
            ))
            self.takeTopLevelItem(row)
            self._path_index.pop(path, None)
        if len(self._history) > self.MAX_HISTORY:
            self._history = self._history[-self.MAX_HISTORY:]
        return len(items)

    def undo_delete(self) -> bool:
        if not self._history:
            return False
        row, track, artist, album, title, dur, path = self._history.pop()
        item = PlaylistItem([track, artist, album, title, dur])
        item.setData(0, Qt.ItemDataRole.UserRole, path)
        self.insertTopLevelItem(row, item)
        self._path_index[path] = item
        return True

    def clear_all(self) -> None:
        self.clear()
        self._path_index.clear()
        self._history.clear()

    def filter(self, text: str) -> None:
        text = text.lower()
        for i in range(self.topLevelItemCount()):
            item = self.topLevelItem(i)
            visible = (
                text in item.text(self.COL_ARTIST).lower()
                or text in item.text(self.COL_ALBUM).lower()
                or text in item.text(self.COL_TITLE).lower()
            )
            item.setHidden(not visible)

    def clear_filter(self) -> None:
        for i in range(self.topLevelItemCount()):
            self.topLevelItem(i).setHidden(False)

    def to_list(self) -> list[dict]:
        result = []
        for i in range(self.topLevelItemCount()):
            it = self.topLevelItem(i)
            result.append({
                "track":    it.text(self.COL_TRACK),
                "artist":   it.text(self.COL_ARTIST),
                "album":    it.text(self.COL_ALBUM),
                "title":    it.text(self.COL_TITLE),
                "duration": it.text(self.COL_DUR),
                "path":     it.data(0, Qt.ItemDataRole.UserRole),
            })
        return result

    def from_list(self, data: list[dict], replace: bool = True) -> int:
        if replace:
            self.clear_all()
        count = 0
        for d in data:
            path = d.get("path", "")
            if path and os.path.exists(path):
                item = self.add_track(
                    path,
                    d.get("track",    ""),
                    d.get("artist",   "Unknown"),
                    d.get("album",    ""),
                    d.get("title",    os.path.basename(path)),
                    d.get("duration", ""),
                )
                if item is not None:
                    count += 1
        return count

    # ------------------------------------------------------------------
    # Convenience
    # ------------------------------------------------------------------

    def item_at_row(self, row: int) -> Optional[QTreeWidgetItem]:
        if 0 <= row < self.topLevelItemCount():
            return self.topLevelItem(row)
        return None

    def path_of(self, item: QTreeWidgetItem) -> str:
        return item.data(0, Qt.ItemDataRole.UserRole)

    # ------------------------------------------------------------------
    # Drag-and-drop (fully manual — Qt's InternalMove is bypassed)
    # ------------------------------------------------------------------

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            idx = self.indexAt(event.position().toPoint())
            self._drag_start_row = idx.row() if idx.isValid() else None
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if (event.buttons() & Qt.MouseButton.LeftButton
                and self._drag_start_row is not None
                and self.selectedItems()):
            drag = QDrag(self)
            mime = QMimeData()
            mime.setText("internal")
            drag.setMimeData(mime)
            drag.exec(Qt.DropAction.MoveAction)
            self._drag_start_row = None
            return
        super().mouseMoveEvent(event)

    def dragEnterEvent(self, event) -> None:
        if event.source() is self:
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event) -> None:
        if event.source() is self:
            pos    = event.position().toPoint()
            target = self.indexAt(pos)
            if not target.isValid():
                self._drop_indicator_row = self.topLevelItemCount()
            else:
                rect = self.visualRect(target)
                mid  = rect.top() + rect.height() // 2
                self._drop_indicator_row = (
                    target.row() if pos.y() < mid else target.row() + 1
                )
            self.viewport().update()
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragLeaveEvent(self, event) -> None:
        self._drop_indicator_row = None
        self.viewport().update()
        super().dragLeaveEvent(event)

    def dropEvent(self, event) -> None:
        if event.source() is not self:
            event.ignore()
            return
        dragged = self.selectedItems()
        if not dragged:
            event.ignore()
            return

        pos    = event.position().toPoint()
        target = self.indexAt(pos)
        if not target.isValid():
            insert_row = self.topLevelItemCount()
        else:
            rect = self.visualRect(target)
            mid  = rect.top() + rect.height() // 2
            insert_row = target.row() if pos.y() < mid else target.row() + 1

        dragged_sorted = sorted(
            [(self.indexOfTopLevelItem(item), item) for item in dragged],
            reverse=True,
        )
        items_ordered = []
        for row, item in dragged_sorted:
            self.takeTopLevelItem(row)
            if row < insert_row:
                insert_row -= 1
            items_ordered.append(item)
        items_ordered.reverse()

        for i, item in enumerate(items_ordered):
            self.insertTopLevelItem(insert_row + i, item)

        self.clearSelection()
        for item in items_ordered:
            item.setSelected(True)

        self._path_index = {}
        for i in range(self.topLevelItemCount()):
            it   = self.topLevelItem(i)
            path = it.data(0, Qt.ItemDataRole.UserRole)
            if path:
                self._path_index[path] = it

        self._drop_indicator_row = None
        self.viewport().update()
        self.setSortingEnabled(False)
        self.header().setSectionsClickable(True)
        # After a manual reorder the sort-by-column no longer applies.
        self._sort_col = -1
        self.header().setSortIndicator(-1, Qt.SortOrder.AscendingOrder)
        self.order_changed.emit()
        event.accept()

    # ------------------------------------------------------------------
    # Drop indicator painting
    # ------------------------------------------------------------------

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        if self._drop_indicator_row is None:
            return
        painter = QPainter(self.viewport())
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        color = self._drop_color
        pen   = QPen(color)
        pen.setWidth(2)
        painter.setPen(pen)

        n = self.topLevelItemCount()
        if self._drop_indicator_row >= n:
            last = self.topLevelItem(n - 1) if n > 0 else None
            y = self.visualRect(self.indexFromItem(last)).bottom() if last else 0
        else:
            item = self.topLevelItem(self._drop_indicator_row)
            y = self.visualRect(self.indexFromItem(item)).top()

        vw = self.viewport().width()
        painter.drawLine(8, y, vw - 8, y)
        painter.setBrush(color)
        painter.setPen(Qt.PenStyle.NoPen)
        size = 5
        painter.drawPolygon([
            QPoint(0,        y - size),
            QPoint(0,        y + size),
            QPoint(size * 2, y),
        ])
        painter.drawPolygon([
            QPoint(vw,            y - size),
            QPoint(vw,            y + size),
            QPoint(vw - size * 2, y),
        ])
        painter.end()
