"""
ui/widgets.py
Small reusable widgets: ShortcutField, ClickableSlider, DropArea.
"""

from PyQt6.QtWidgets import QSlider, QWidget, QLineEdit
from PyQt6.QtCore    import Qt
from PyQt6.QtGui     import QMouseEvent, QKeySequence, QKeyEvent

class ShortcutField(QLineEdit):
    """A read-only field that captures the next key combination pressed."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setReadOnly(True)
        self.setPlaceholderText("Click then press shortcut…")
        self.setStyleSheet("""
            QLineEdit {
                border: 2px solid #444466;
                border-radius: 4px;
                padding: 2px 6px;
                background: #1a1a2e;
                color: #e0e0f0;
            }
            QLineEdit:focus {
                border: 2px solid #e94560;
                background: #2a1a3e;
                color: #ffffff;
            }
        """)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        key = event.key()
        if key in (Qt.Key.Key_Control, Qt.Key.Key_Shift,
                Qt.Key.Key_Alt, Qt.Key.Key_Meta):
            return
        # let Escape propagate so the dialog can close normally
        if key == Qt.Key.Key_Escape:
            super().keyPressEvent(event)
            return
        sequence = QKeySequence(event.keyCombination()).toString()
        self.setText(sequence)

class ClickableSlider(QSlider):
    """Slider that jumps to wherever the user clicks (not just drags)."""

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            value = round(event.position().x() / self.width() * self.maximum())
            self.setValue(value)
            self.sliderMoved.emit(value)
        super().mousePressEvent(event)


class DropArea(QWidget):
    """
    Transparent overlay that accepts file/folder drag-and-drop.
    *callback* receives the QDropEvent.
    """

    def __init__(self, callback, parent=None) -> None:
        super().__init__(parent)
        self._callback = callback
        self.setAcceptDrops(True)

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            self._callback(event)
            event.acceptProposedAction()
        else:
            event.ignore()
