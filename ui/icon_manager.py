"""
ui/icon_manager.py
Owns ICON_MAP + the control-button icon registry; wraps ui.icons.load_icon
with MainWindow's button bookkeeping (play/mute/toggle icon swaps, and the
legacy PNG-exists-gated icon-vs-text-label fallback used at button
construction time).

Note on testability: unlike ui/playlist_persistence.py, this module is NOT
Qt-free — its whole job is to call QPushButton.setIcon/setText, which
requires a live QApplication. Only `toggle_icon_colors` and the static
`ICON_MAP` structure are unit-tested (see tests/test_icon_manager.py); the
Qt-touching methods below are not covered by this project's current test
setup (no pytest-qt, no QApplication fixture).
"""

from __future__ import annotations

import os

from PyQt6.QtCore    import QSize
from PyQt6.QtGui     import QIcon
from PyQt6.QtWidgets import QPushButton

from ui.icons import load_icon

ICON_MAP: dict[str, str] = {
    "Settings": "icon_settings",
    "|<":       "icon_prev",
    ">":        "icon_play",
    "||":       "icon_pause",
    "[]":       "icon_stop",
    ">|":       "icon_next",
    "Shuffle":  "icon_shuffle",
    "Repeat":   "icon_repeat",
    "Save":     "icon_save",
    "Load":     "icon_load",
    "EQ":       "icon_eq",
    "Volume":   "icon_volume",
    "Muted":    "icon_mute",
    "no_art":   "icon_no_art",   # unused today, kept as-is — out of scope
}


def toggle_icon_colors(primary: str, accent: str, active: bool) -> tuple[str, str]:
    """Pure helper: decide the (override_primary, override_accent) pair for
    a toggle button's icon. Active -> colors swapped (accent, primary) so
    the icon renders "lit up"; inactive -> ("", "") meaning no override.
    Isolated as a free function so it's unit-testable without QApplication
    — guards the same bug class fixed once already in this repo (commit
    c8b9d16, primary/accent inversion in ui/icons.py::_icon_no_art).
    """
    return (accent, primary) if active else ("", "")


class IconManager:
    """Generates themed button icons and applies them to the registered
    control buttons, mirroring MainWindow's former _load_icon/
    _refresh_icons/_set_play_icon/_toggle_mute/_set_toggle_icon/_ctrl_btn
    icon-handling logic."""

    def __init__(self, assets_dir: str) -> None:
        self._assets_dir = assets_dir
        self._icon_buttons: dict[str, QPushButton] = {}

    def register_buttons(self, buttons: dict[str, QPushButton]) -> None:
        """Call once, after all control buttons exist (MainWindow._build_ui).
        Replaces any prior registration."""
        self._icon_buttons = buttons

    def load_icon(self, name: str, style: str, primary: str, accent: str,
                  override_primary: str = "", override_accent: str = "") -> QIcon:
        """Generate a themed SVG icon, with PNG fallback.
        override_primary/accent allow swapping colours for active toggle states.
        """
        return load_icon(
            name  = name,
            style = style,
            primary = override_primary or primary,
            accent  = override_accent  or accent,
            pixel_size = 32,
            assets_dir = self._assets_dir,
        )

    def refresh_all(self, style: str, primary: str, accent: str, *,
                     playing: bool, muted: bool,
                     shuffle_active: bool, repeat_active: bool) -> None:
        """Retint all registered button icons to match the current theme."""
        for label, btn in self._icon_buttons.items():
            icon_name = ICON_MAP.get(label, "")
            if icon_name:
                btn.setIcon(self.load_icon(icon_name, style, primary, accent))
        # play button may currently show pause icon
        self.set_play_icon(self._icon_buttons[">"], playing, style, primary, accent)
        # mute button may currently show muted icon
        if muted:
            self._icon_buttons["Volume"].setIcon(
                self.load_icon(ICON_MAP["Muted"], style, primary, accent))
        # shuffle/repeat: accent-swapped icon when active
        self.set_toggle_icon(self._icon_buttons["Shuffle"], "icon_shuffle",
                              shuffle_active, style, primary, accent)
        self.set_toggle_icon(self._icon_buttons["Repeat"], "icon_repeat",
                              repeat_active, style, primary, accent)

    def set_play_icon(self, btn: QPushButton, playing: bool,
                       style: str, primary: str, accent: str) -> None:
        label = "||" if playing else ">"
        self._set_icon_or_text(btn, ICON_MAP[label], label, style, primary, accent)

    def set_mute_icon(self, btn: QPushButton, muted: bool,
                       style: str, primary: str, accent: str) -> None:
        icon_name = ICON_MAP["Muted"] if muted else ICON_MAP["Volume"]
        fallback  = "Muted" if muted else "Volume"
        self._set_icon_or_text(btn, icon_name, fallback, style, primary, accent)

    def set_toggle_icon(self, btn: QPushButton, icon_name: str, active: bool,
                         style: str, primary: str, accent: str) -> None:
        """Regenerate a toggle button icon with swapped colours when active."""
        override_primary, override_accent = toggle_icon_colors(primary, accent, active)
        icon = self.load_icon(icon_name, style, primary, accent,
                               override_primary=override_primary,
                               override_accent=override_accent)
        btn.setIcon(icon)
        btn.setIconSize(QSize(22, 22))

    def initial_button_icon(self, btn: QPushButton, label: str,
                             style: str, primary: str, accent: str) -> bool:
        """Used by MainWindow._ctrl_btn at construction time. Returns True
        if an icon was applied; False means the fallback text (already
        applied internally) is what's showing."""
        icon_name = ICON_MAP.get(label, "")
        return self._set_icon_or_text(btn, icon_name, label, style, primary, accent,
                                       set_icon_size=True)

    def _set_icon_or_text(self, btn: QPushButton, icon_name: str, fallback_text: str,
                           style: str, primary: str, accent: str,
                           set_icon_size: bool = False) -> bool:
        icon_path = os.path.join(self._assets_dir, f"{icon_name}.png") if icon_name else ""
        if icon_name and os.path.exists(icon_path):
            btn.setIcon(self.load_icon(icon_name, style, primary, accent))
            if set_icon_size:
                btn.setIconSize(QSize(22, 22))
            return True
        btn.setText(fallback_text)
        return False
