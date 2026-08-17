"""
ui/settings_controller.py
Thin wrapper around SettingsDialog/EqualizerDialog (ui/dialogs.py),
extracted from MainWindow._open_settings/_open_equalizer.

Deliberately narrow scope: MainWindow keeps self._config as the single
source of truth and keeps orchestrating _apply_config()/_apply_shortcuts()
itself (see CLAUDE.md for the full rationale) — SettingsController's only
job is "show this modal dialog, hand back what the user chose". No
config_changed signal either: both call sites are synchronous, blocking
QDialog.exec() calls made directly by MainWindow on its own button clicks,
so a plain return value is a better fit than Qt-signal indirection (unlike
ui/file_browser_panel.py, where the panel itself detects the user action
asynchronously and doesn't know who consumes it).

Carries no state between calls — config/eq_state/player/parent are all
passed in fresh each time, mirroring how the dialogs themselves work. Kept
as a class rather than two module-level functions only for naming
consistency with IconManager/FileBrowserPanel.

Note on testability: like ui/icon_manager.py and ui/album_art_panel.py,
there is no pure-Python logic here to isolate — both methods just
construct a QDialog, call .exec() (modal), and read back a property. Zero
unit tests under the current setup (no pytest-qt, no QApplication fixture).
"""

from __future__ import annotations

from PyQt6.QtWidgets import QDialog, QWidget

from ui.dialogs import EqualizerDialog, SettingsDialog


class SettingsController:
    """Opens the Settings/Equalizer dialogs and hands back their result.
    Does not touch MainWindow's config storage or apply anything itself."""

    def open_settings_dialog(self, config: dict, parent: QWidget) -> dict | None:
        """Show the settings dialog. Returns the new config dict if the
        user accepted, None if they cancelled (caller's config is left
        untouched either way — SettingsDialog copies it internally)."""
        dlg = SettingsDialog(config, parent)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            return dlg.config
        return None

    def open_equalizer_dialog(self, player, eq_state: dict, parent: QWidget) -> dict:
        """Show the equalizer dialog (live-previews on *player* while open).
        Always returns the resulting eq_state, even on Close/Cancel — the
        equalizer intentionally persists whatever was last heard."""
        dlg = EqualizerDialog(player, eq_state, parent)
        dlg.exec()
        return dlg.eq_state
