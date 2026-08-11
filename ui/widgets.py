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
        # Defaults match the previous hardcoded style; overridden by
        # set_focus_color() / set_theme() once the real config is known.
        self._focus_color      = "#e94560"
        self._background_color = "#1a1a2e"
        self._border_color     = "#444466"
        self._text_color       = "#e0e0f0"
        self._apply_style()

    def set_focus_color(self, color: str) -> None:
        """Update the focus border color to match the current theme."""
        self._focus_color = color
        self._apply_style()

    def set_theme(self, background_color: str, border_color: str, text_color: str) -> None:
        """Update the base (non-focus) colors to match the current theme."""
        self._background_color = background_color
        self._border_color     = border_color
        self._text_color       = text_color
        self._apply_style()

    def _apply_style(self) -> None:
        self.setStyleSheet(f"""
            QLineEdit {{
                border: 2px solid {self._border_color};
                border-radius: 4px;
                padding: 2px 6px;
                background: {self._background_color};
                color: {self._text_color};
            }}
            QLineEdit:focus {{
                border: 2px solid {self._focus_color};
                background: {self._background_color};
                color: {self._text_color};
            }}
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
        if event.button() == Qt.MouseButton.LeftButton and self.width() > 0:
            span  = self.maximum() - self.minimum()
            value = self.minimum() + round(event.position().x() / self.width() * span)
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
