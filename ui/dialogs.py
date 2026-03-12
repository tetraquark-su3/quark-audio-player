"""
ui/dialogs.py
Settings dialog (FenetreReglages -> SettingsDialog)
Equalizer dialog (FenetreEgaliseur -> EqualizerDialog)
"""

import vlc

from PyQt6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QSlider, QSpinBox, QVBoxLayout,
    QColorDialog, QFontDialog, QMessageBox, QDoubleSpinBox
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui  import QColor, QFont, QKeySequence
from ui.widgets import ShortcutField
from config.settings import DEFAULT_CONFIG


class EqualizerDialog(QDialog):
    """10-band VLC equalizer with preset support."""

    def __init__(self, media_player, eq_state: dict | None = None, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Equalizer")
        self.setMinimumWidth(600)
        self._player    = media_player
        self._equalizer = vlc.libvlc_audio_equalizer_new()
        self._eq_state  = eq_state or {}

        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(16, 16, 16, 16)

        # --- Presets ---
        row = QHBoxLayout()
        row.addWidget(QLabel("Preset:"))
        self._combo_presets = QComboBox()
        n = vlc.libvlc_audio_equalizer_get_preset_count()
        # Block signals while populating so currentIndexChanged does not fire
        self._combo_presets.blockSignals(True)
        for i in range(n):
            name = vlc.libvlc_audio_equalizer_get_preset_name(i)
            if isinstance(name, bytes):
                name = name.decode()
            self._combo_presets.addItem(name)
        self._combo_presets.blockSignals(False)
        self._combo_presets.currentIndexChanged.connect(self._apply_preset)
        row.addWidget(self._combo_presets)
        row.addStretch()
        btn_reset = QPushButton("Reset")
        btn_reset.clicked.connect(self._reset)
        row.addWidget(btn_reset)
        layout.addLayout(row)

        # --- Preamp ---
        row2 = QHBoxLayout()
        row2.addWidget(QLabel("Preamp"))
        self._slider_preamp = QSlider(Qt.Orientation.Horizontal)
        self._slider_preamp.setRange(-200, 200)
        self._slider_preamp.setValue(0)
        self._slider_preamp.valueChanged.connect(self._update_preamp)
        self._lbl_preamp = QLabel("0.0 dB")
        self._lbl_preamp.setFixedWidth(60)
        row2.addWidget(self._slider_preamp)
        row2.addWidget(self._lbl_preamp)
        layout.addLayout(row2)

        # --- Band sliders ---
        self._sliders:   list[QSlider] = []
        self._lbl_vals:  list[QLabel]  = []
        bands_row = QHBoxLayout()
        n_bands = vlc.libvlc_audio_equalizer_get_band_count()
        for i in range(n_bands):
            freq = vlc.libvlc_audio_equalizer_get_band_frequency(i)
            col  = QVBoxLayout()
            col.setAlignment(Qt.AlignmentFlag.AlignHCenter)

            lbl_val = QLabel("0.0")
            lbl_val.setAlignment(Qt.AlignmentFlag.AlignHCenter)
            lbl_val.setFixedWidth(45)

            sl = QSlider(Qt.Orientation.Vertical)
            sl.setRange(-200, 200)
            sl.setValue(0)
            sl.setFixedHeight(120)
            sl.valueChanged.connect(lambda v, idx=i: self._update_band(idx, v))

            freq_str = f"{int(freq)}Hz" if freq < 1_000 else f"{int(freq / 1_000)}kHz"
            lbl_freq = QLabel(freq_str)
            lbl_freq.setAlignment(Qt.AlignmentFlag.AlignHCenter)
            lbl_freq.setFixedWidth(45)

            col.addWidget(lbl_val)
            col.addWidget(sl)
            col.addWidget(lbl_freq)
            bands_row.addLayout(col)
            self._sliders.append(sl)
            self._lbl_vals.append(lbl_val)
        layout.addLayout(bands_row)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        # Restore previously saved state (if any) into sliders + equalizer object
        if self._eq_state:
            preamp = self._eq_state.get("preamp", 0.0)
            self._slider_preamp.blockSignals(True)
            self._slider_preamp.setValue(int(preamp * 10))
            self._lbl_preamp.setText(f"{preamp:.1f} dB")
            vlc.libvlc_audio_equalizer_set_preamp(self._equalizer, preamp)
            self._slider_preamp.blockSignals(False)
            for i, sl in enumerate(self._sliders):
                amp = self._eq_state.get("bands", [0.0] * len(self._sliders))[i] if i < len(self._eq_state.get("bands", [])) else 0.0
                sl.blockSignals(True)
                sl.setValue(int(amp * 10))
                self._lbl_vals[i].setText(f"{amp:.1f}")
                vlc.libvlc_audio_equalizer_set_amp_at_index(self._equalizer, amp, i)
                sl.blockSignals(False)
            # Restore the name of the previously saved state (if any)
            # Signals are blocked to not write over the new values
            preset_name = self._eq_state.get("preset", "")
            if preset_name:
                idx = self._combo_presets.findText(preset_name)
                if idx >= 0:
                    self._combo_presets.blockSignals(True)
                    self._combo_presets.setCurrentIndex(idx)
                    self._combo_presets.blockSignals(False)

        # Attach equalizer immediately so it is active from the moment the
        # dialog opens, even before the user moves any slider.
        vlc.libvlc_media_player_set_equalizer(self._player, self._equalizer)

    # ------------------------------------------------------------------

    def _update_band(self, idx: int, value: int) -> None:
        db = value / 10.0
        self._lbl_vals[idx].setText(f"{db:.1f}")
        vlc.libvlc_audio_equalizer_set_amp_at_index(self._equalizer, db, idx)
        vlc.libvlc_media_player_set_equalizer(self._player, self._equalizer)

    def _update_preamp(self, value: int) -> None:
        db = value / 10.0
        self._lbl_preamp.setText(f"{db:.1f} dB")
        vlc.libvlc_audio_equalizer_set_preamp(self._equalizer, db)
        vlc.libvlc_media_player_set_equalizer(self._player, self._equalizer)

    def _apply_preset(self, index: int) -> None:
        eq = vlc.libvlc_audio_equalizer_new_from_preset(index)
        # Block slider signals while loading preset values to avoid redundant
        # set_equalizer calls; one final call is made after all values are set.
        self._slider_preamp.blockSignals(True)
        for sl in self._sliders:
            sl.blockSignals(True)
        self._slider_preamp.setValue(int(vlc.libvlc_audio_equalizer_get_preamp(eq) * 10))
        preamp_db = vlc.libvlc_audio_equalizer_get_preamp(eq)
        self._lbl_preamp.setText(f"{preamp_db:.1f} dB")
        vlc.libvlc_audio_equalizer_set_preamp(self._equalizer, preamp_db)
        for i, sl in enumerate(self._sliders):
            amp = vlc.libvlc_audio_equalizer_get_amp_at_index(eq, i)
            sl.setValue(int(amp * 10))
            self._lbl_vals[i].setText(f"{amp:.1f}")
            vlc.libvlc_audio_equalizer_set_amp_at_index(self._equalizer, amp, i)
        self._slider_preamp.blockSignals(False)
        for sl in self._sliders:
            sl.blockSignals(False)
        vlc.libvlc_audio_equalizer_release(eq)
        vlc.libvlc_media_player_set_equalizer(self._player, self._equalizer)

    @property
    def eq_state(self) -> dict:
        n = len(self._sliders)
        return {
            "preamp":  self._slider_preamp.value() / 10.0,
            "bands":   [self._sliders[i].value() / 10.0 for i in range(n)],
            "preset":  self._combo_presets.currentText(),   # ← ajout
        }

    def _reset(self) -> None:
        # Block signals, zero everything in the equalizer object, then attach once.
        self._slider_preamp.blockSignals(True)
        for sl in self._sliders:
            sl.blockSignals(True)
        self._slider_preamp.setValue(0)
        self._lbl_preamp.setText("0.0 dB")
        vlc.libvlc_audio_equalizer_set_preamp(self._equalizer, 0.0)
        for i, sl in enumerate(self._sliders):
            sl.setValue(0)
            self._lbl_vals[i].setText("0.0")
            vlc.libvlc_audio_equalizer_set_amp_at_index(self._equalizer, 0.0, i)
        self._slider_preamp.blockSignals(False)
        for sl in self._sliders:
            sl.blockSignals(False)
        self._combo_presets.blockSignals(True)
        self._combo_presets.setCurrentIndex(0)  # "Flat" ou équivalent
        self._combo_presets.blockSignals(False)
        vlc.libvlc_media_player_set_equalizer(self._player, self._equalizer)


# ---------------------------------------------------------------------------
# Settings dialog
# ---------------------------------------------------------------------------

class SettingsDialog(QDialog):
    """Application settings: colours, font, FPS, bar count, shortcuts."""

    SHORTCUT_LABELS = {
        "play_pause": "Play / Pause",
        "next":       "Next track",
        "previous":   "Previous track",
        "search":     "Search",
        "undo":       "Undo delete",
    }

    def __init__(self, config: dict, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setMinimumWidth(340)
        self._config = config.copy()
        layout = QFormLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(16, 16, 16, 16)

        # Colour buttons
        self._btn_primary    = self._make_color_btn("primary_color")
        self._btn_accent     = self._make_color_btn("accent_color")
        self._btn_background = self._make_color_btn("background_color")
        self._btn_selection  = self._make_color_btn("selection_color")
        layout.addRow("Primary colour:",    self._btn_primary)
        layout.addRow("Accent colour:",     self._btn_accent)
        layout.addRow("Background colour:", self._btn_background)
        layout.addRow("Selection colour:",  self._btn_selection)

        # Font
        self._btn_font = QPushButton("Choose…")
        self._btn_font.setFixedHeight(32)
        self._btn_font.clicked.connect(self._choose_font)
        fam = self._config.get("font_family", "Cantarell")
        sz  = self._config.get("font_size",   13)
        self._btn_font.setText(f"{fam} {sz}pt")
        layout.addRow("Font:", self._btn_font)

        # FPS
        self._spin_fps = QSpinBox()
        self._spin_fps.setRange(10, 60)
        self._spin_fps.setValue(self._config["fps"])
        self._spin_fps.setSuffix(" fps")
        layout.addRow("Refresh rate:", self._spin_fps)

        # Bar count
        self._spin_bars = QSpinBox()
        self._spin_bars.setRange(16, 128)
        self._spin_bars.setValue(self._config["bar_count"])
        self._spin_bars.setSuffix(" bars")
        layout.addRow("FFT bars:", self._spin_bars)

        # Flux history
        self._spin_flux = QSpinBox()
        self._spin_flux.setRange(100, 5000)
        self._spin_flux.setValue(self._config.get("flux_history", 2000))
        self._spin_flux.setSuffix(" points")
        layout.addRow("Flux history:", self._spin_flux)

        # Spectrogram time resolution
        self._spin_spectrogram_res = QDoubleSpinBox()
        self._spin_spectrogram_res.setRange(0.1, 5.0)
        self._spin_spectrogram_res.setSingleStep(0.1)
        self._spin_spectrogram_res.setDecimals(1)
        fps    = self._config.get("fps", 60)
        frames = self._config.get("spectrogram_resolution", 15)
        self._spin_spectrogram_res.setValue(round(frames / fps, 1))
        self._spin_spectrogram_res.setSuffix("s / bin")
        layout.addRow("Spectrogram resolution:", self._spin_spectrogram_res)

        # Shortcuts
        self._shortcut_fields: dict[str, QLineEdit] = {}
        shortcuts = self._config.get("shortcuts", DEFAULT_CONFIG["shortcuts"])
        for key, label in self.SHORTCUT_LABELS.items():
            field = ShortcutField()
            field.setText(shortcuts.get(key, DEFAULT_CONFIG["shortcuts"][key]))
            field.setFixedHeight(28)
            self._shortcut_fields[key] = field
            layout.addRow(f"{label}:", field)

        btn_reset_defaults = QPushButton("Reset to defaults")
        btn_reset_defaults.clicked.connect(self._reset_defaults)
        layout.addRow(btn_reset_defaults)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _make_color_btn(self, config_key: str) -> QPushButton:
        btn = QPushButton()
        btn.setFixedHeight(32)
        self._refresh_color_btn(btn, self._config[config_key])
        btn.clicked.connect(lambda _, k=config_key, b=btn: self._choose_color(k, b))
        return btn

    def _refresh_color_btn(self, btn: QPushButton, color: str) -> None:
        btn.setStyleSheet(f"background-color: {color}; border-radius: 4px;")
        btn.setText(color)

    def _choose_color(self, config_key: str, btn: QPushButton) -> None:
        color = QColorDialog.getColor(QColor(self._config[config_key]), self)
        if color.isValid():
            self._config[config_key] = color.name()
            self._refresh_color_btn(btn, color.name())

    def _choose_font(self) -> None:
        current = QFont(
            self._config.get("font_family", "Cantarell"),
            self._config.get("font_size",   13),
        )
        result = QFontDialog.getFont(current, self)
        font, ok = (result[0], result[1]) if isinstance(result[0], QFont) else (result[1], result[0])
        if ok and isinstance(font, QFont):
            self._config["font_family"] = font.family()
            self._config["font_size"]   = font.pointSize()
            self._btn_font.setText(f"{font.family()} {font.pointSize()}pt")

    def _reset_defaults(self) -> None:
        self._config = DEFAULT_CONFIG.copy()
        self._refresh_color_btn(self._btn_primary,    self._config["primary_color"])
        self._refresh_color_btn(self._btn_accent,     self._config["accent_color"])
        self._refresh_color_btn(self._btn_background, self._config["background_color"])
        self._refresh_color_btn(self._btn_selection,  self._config["selection_color"])
        self._spin_fps.setValue(self._config["fps"])
        self._spin_bars.setValue(self._config["bar_count"])
        self._spin_flux.setValue(self._config.get("flux_history", 2000))
        fam    = self._config.get("font_family", "Cantarell")
        sz     = self._config.get("font_size", 13)
        fps    = self._config.get("fps", 60)
        frames = self._config.get("spectrogram_resolution", 15)
        self._spin_spectrogram_res.setValue(round(frames / fps, 1))
        self._btn_font.setText(f"{fam} {sz}pt")
        shortcuts = self._config.get("shortcuts", DEFAULT_CONFIG["shortcuts"])
        for key, field in self._shortcut_fields.items():
            field.setText(shortcuts.get(key, ""))

    def _on_accept(self) -> None:
        shortcuts = {k: f.text() for k, f in self._shortcut_fields.items()}
        if len(set(shortcuts.values())) != len(shortcuts):
            QMessageBox.warning(self, "Error", "Two actions share the same shortcut.")
            return
        self._config["fps"]          = self._spin_fps.value()
        self._config["bar_count"]    = self._spin_bars.value()
        self._config["flux_history"] = self._spin_flux.value()
        self._config["shortcuts"]    = shortcuts
        # fps must be committed first so the frames-per-bin calculation is correct
        self._config["spectrogram_resolution"] = max(1, round(
            self._spin_spectrogram_res.value() * self._config["fps"]
        ))
        self.accept()

    # ------------------------------------------------------------------
    # Result
    # ------------------------------------------------------------------

    @property
    def config(self) -> dict:
        return self._config
