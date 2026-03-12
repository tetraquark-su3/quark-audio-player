"""
ui/visualizations.py
All six audio-visualisation widgets: spectrum, spectrogram, oscilloscope,
Lissajous, spectral flux, and VU-meter.
"""

import numpy as np

from PyQt6.QtWidgets import QWidget
from PyQt6.QtCore    import Qt
from PyQt6.QtGui     import (
    QColor, QLinearGradient, QPainter, QPen
)

# Shared constants for frequency-axis labels
_SAMPLE_RATE = 48_000
_FFT_BLOCK   = 2_048
_HZ_PER_BIN  = _SAMPLE_RATE / _FFT_BLOCK
_FREQ_MIN    = _HZ_PER_BIN
_FREQ_MAX    = _HZ_PER_BIN * 512


def _freq_label(freq: int) -> str:
    return f"{freq}Hz" if freq < 1_000 else f"{freq // 1_000}kHz"


# ---------------------------------------------------------------------------
# Spectrum (FFT bar chart)
# ---------------------------------------------------------------------------

class SpectrumWidget(QWidget):
    """Logarithmic bar spectrum with gradient fill."""

    FREQ_LABELS = [63, 125, 250, 500, 1_000, 2_000, 4_000, 8_000, 11_000]

    def __init__(self) -> None:
        super().__init__()
        self.setMinimumHeight(80)
        self._smoothed: list[float] = []
        self._primary = "#e94560"
        self._accent  = "#a8c0ff"

    def set_bars(self, bars: list[float]) -> None:
        if not self._smoothed or len(self._smoothed) != len(bars):
            self._smoothed = bars
        else:
            alpha = 0.35
            self._smoothed = [
                alpha * new + (1 - alpha) * old
                for new, old in zip(bars, self._smoothed)
            ]
        self.update()

    def set_colors(self, primary: str, accent: str) -> None:
        self._primary = primary
        self._accent  = accent

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.update()

    def paintEvent(self, _event) -> None:
        if not self._smoothed:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        n = len(self._smoothed)
        bar_w = w / n
        gap   = max(1, bar_w * 0.15)
        h_bars = h - 16

        for i, value in enumerate(self._smoothed):
            height = max(1, int(value * h_bars))
            x = int(i * bar_w)
            grad = QLinearGradient(x, h_bars, x, h_bars - height)
            grad.setColorAt(0.0, QColor(self._primary))
            grad.setColorAt(1.0, QColor(self._accent))
            painter.fillRect(
                int(x + gap), max(0, h_bars - height),
                int(bar_w - gap * 2), min(height, h_bars),
                grad,
            )

        painter.setPen(QColor("#8899aa"))
        for freq in self.FREQ_LABELS:
            pos = np.log10(freq / _FREQ_MIN) / np.log10(_FREQ_MAX / _FREQ_MIN)
            x_lbl = int(pos * w)
            if x_lbl < w - 30:
                painter.drawText(x_lbl, h, _freq_label(freq))


# ---------------------------------------------------------------------------
# Spectrogram (scrolling heat-map)
# ---------------------------------------------------------------------------

class SpectrogramWidget(QWidget):
    """Scrolling colour spectrogram."""

    def __init__(self) -> None:
        super().__init__()
        self._frame_acc: list[list[float]] = []  # accumulate frames
        self._frames_per_bin = 15  # 15 frames @ 30fps = 0.5s per bin
        self.setMinimumHeight(80)
        self._columns: list[list[float]] = []
        self._max_cols = 200
    
    def set_frames_per_bin(self, n: int) -> None:
        self._frames_per_bin = max(1, n)
        self._frame_acc.clear()
        self._columns.clear()
        self.update()

    def set_max_cols(self, n: int) -> None:
        self._max_cols = n
        if len(self._columns) > self._max_cols:
            self._columns = self._columns[-self._max_cols:]
        self.update()

    def add_column(self, fft: list[float]) -> None:
        if not self.isVisible():
            return
        self._frame_acc.append(fft)
        if len(self._frame_acc) < self._frames_per_bin:
            return
        if not self._frame_acc or not self._frame_acc[0]:  # guard
            self._frame_acc.clear()
            return
        n_bins = len(self._frame_acc[0])
        n_frames = len(self._frame_acc)
        averaged = [
            sum(self._frame_acc[f][b] for f in range(n_frames)) / n_frames
            for b in range(n_bins)
        ]
        self._frame_acc.clear()
        self._columns.append(averaged)
        if len(self._columns) > self._max_cols:
            self._columns.pop(0)
        self.update()

    def showEvent(self, event) -> None:
            super().showEvent(event)
            self._columns.clear()
            self._frame_acc.clear()
            self.update()

    def paintEvent(self, _event) -> None:
        if not self._columns:
            return
        painter = QPainter(self)
        w, h = self.width(), self.height()
        if w == 0:
            return
        h_spec = h - 16
        col_w  = w / self._max_cols
        for ci, col in enumerate(self._columns):
            n_bins = len(col)
            x  = int(ci * col_w)
            lw = max(1, int(col_w) + 1)
            for pi in range(h_spec):
                # Linear position from top (0) to bottom (1)
                pos = pi / h_spec
                # Map linearly to log-spaced bin index
                bi = int(np.exp(pos * np.log(n_bins)))
                bi = min(bi, n_bins - 1)
                v   = max(0.0, min(1.0, col[bi]))
                if v < 0.25:
                    r, g, b = 0, 0, int(v * 4 * 200)
                elif v < 0.5:
                    r, g, b = 0, int((v - 0.25) * 4 * 200), 200
                elif v < 0.75:
                    r, g, b = int((v - 0.5) * 4 * 255), 255, int(200 - (v - 0.5) * 4 * 200)
                else:
                    r, g, b = 255, int(255 - (v - 0.75) * 4 * 255), 0
                painter.fillRect(x, pi, lw, 1, QColor(r, g, b))

        painter.setPen(QColor("#8899aa"))
        for freq in [63, 125, 250, 500, 1_000, 2_000, 4_000, 8_000]:
            pos   = np.log(freq / _FREQ_MIN) / np.log(_FREQ_MAX / _FREQ_MIN)
            y_lbl = int(pos * h_spec)
            painter.drawText(2, y_lbl, _freq_label(freq))


# ---------------------------------------------------------------------------
# Oscilloscope
# ---------------------------------------------------------------------------

class OscilloscopeWidget(QWidget):
    """Time-domain waveform display."""

    def __init__(self) -> None:
        super().__init__()
        self.setMinimumHeight(80)
        self._smoothed: list[float] = []
        self._primary = "#e94560"

    def set_samples(self, samples: list[float]) -> None:
        if not self._smoothed or len(self._smoothed) != len(samples):
            self._smoothed = samples
        else:
            alpha = 0.4
            self._smoothed = [
                alpha * new + (1 - alpha) * old
                for new, old in zip(samples, self._smoothed)
            ]
        self.update()

    def set_colors(self, primary: str, _accent: str) -> None:
        self._primary = primary

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.update()

    def paintEvent(self, _event) -> None:
        if not self._smoothed:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        mid  = h // 2
        n    = len(self._smoothed)
        pen  = QPen(QColor(self._primary))
        pen.setWidth(3)
        painter.setPen(pen)
        pts = [(int(i / n * w), int(mid - v * mid * 0.9)) for i, v in enumerate(self._smoothed)]
        for i in range(1, len(pts)):
            painter.drawLine(pts[i-1][0], pts[i-1][1], pts[i][0], pts[i][1])


# ---------------------------------------------------------------------------
# Lissajous
# ---------------------------------------------------------------------------

class LissajousWidget(QWidget):
    """Lissajous figure: left channel vs right channel."""

    def __init__(self) -> None:
        super().__init__()
        self.setMinimumHeight(80)
        self._left:  list[float] = []
        self._right: list[float] = []
        self._primary = "#e94560"
        self._accent  = "#a8c0ff"

    def set_samples(self, left: list[float], right: list[float]) -> None:
        self._left  = left
        self._right = right
        self.update()

    def set_colors(self, primary: str, accent: str) -> None:
        self._primary = primary
        self._accent  = accent

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.update()

    def paintEvent(self, _event) -> None:
        if not self._left or not self._right:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        cx, cy = w // 2, h // 2
        rx, ry = cx * 0.9, cy * 0.9
        painter.fillRect(0, 0, w, h, QColor(26, 26, 46, 40))
        cp = QColor(self._primary)
        ca = QColor(self._accent)
        n   = min(len(self._left), len(self._right))
        pen = QPen()
        pen.setWidth(1)
        for i in range(1, n):
            t = i / n
            r = int(cp.red()   * t + ca.red()   * (1 - t))
            g = int(cp.green() * t + ca.green() * (1 - t))
            b = int(cp.blue()  * t + ca.blue()  * (1 - t))
            pen.setColor(QColor(r, g, b, 200))
            painter.setPen(pen)
            x1 = int(cx + self._left[i-1]  * rx)
            y1 = int(cy - self._right[i-1] * ry)
            x2 = int(cx + self._left[i]    * rx)
            y2 = int(cy - self._right[i]   * ry)
            painter.drawLine(x1, y1, x2, y2)


# ---------------------------------------------------------------------------
# Spectral Flux
# ---------------------------------------------------------------------------

class SpectralFluxWidget(QWidget):
    """Scrolling spectral-flux (onset strength) plot."""

    def __init__(self) -> None:
        super().__init__()
        self.setMinimumHeight(80)
        self._history:   list[float] = []
        self._prev:      list[float] | None = None
        self._primary  = "#e94560"
        self._accent   = "#a8c0ff"
        self._max_points = 2_000

    def set_max_points(self, n: int) -> None:
        self._max_points = n
        if len(self._history) > self._max_points:
            self._history = self._history[-self._max_points:]
        self.update()

    def update_spectrum(self, spectrum: list[float]) -> None:
        if not self.isVisible():
            self._prev = spectrum  # still update prev to avoid flux spike on resume
            return
        if self._prev is not None and len(spectrum) == len(self._prev):
            flux = float(np.sum(np.abs(np.array(spectrum) - np.array(self._prev))))
            self._history.append(flux)
            if len(self._history) > self._max_points:
                self._history.pop(0)
        self._prev = spectrum
        self.update()

    def set_colors(self, primary: str, accent: str) -> None:
        self._primary = primary
        self._accent  = accent

    def showEvent(self, event) -> None:
        """Clear stale history on tab switch — missing data would be misleading."""
        super().showEvent(event)
        self._history.clear()
        self._prev = None
        self.update()

    def paintEvent(self, _event) -> None:
        if not self._history:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        if w == 0:
            return
        h_use = h - 16
        painter.fillRect(0, 0, w, h, QColor("#1a1a2e"))
        cp = QColor(self._primary)
        ca = QColor(self._accent)
        col_w = w / self._max_points

        max_flux = max(self._history) if self._history else 1.0
        if max_flux < 1e-6:
            max_flux = 1.0
        for i, flux in enumerate(self._history):
            t     = flux / max_flux
            bar_h = int(t * h_use)
            x     = int(i * col_w)
            lw    = max(1, int(col_w))
            r = int(cp.red()   * t + ca.red()   * (1 - t))
            g = int(cp.green() * t + ca.green() * (1 - t))
            b = int(cp.blue()  * t + ca.blue()  * (1 - t))
            grad = QLinearGradient(x, h_use, x, h_use - bar_h)
            grad.setColorAt(0.0, QColor(r, g, b, 255))
            grad.setColorAt(1.0, QColor(r, g, b, 80))
            painter.fillRect(x, h_use - bar_h, lw, bar_h, grad)


# ---------------------------------------------------------------------------
# VU Meter
# ---------------------------------------------------------------------------

class VUMeterWidget(QWidget):
    """Stereo level meter with coloured gradient (green → yellow → red) and peak hold."""

    DECAY      = 0.15   # fraction to drop per frame (fast fall)
    PEAK_HOLD  = 40     # frames to hold peak before it drops
    PEAK_DECAY = 0.03   # fraction per frame after hold expires

    def __init__(self) -> None:
        super().__init__()
        self.setMinimumHeight(80)
        self._left    = 0.0
        self._right   = 0.0
        self._peak_l  = 0.0
        self._peak_r  = 0.0
        self._hold_l  = 0
        self._hold_r  = 0
        self._primary = "#e94560"
        self._accent  = "#a8c0ff"

    def set_levels(self, left: float, right: float) -> None:
        # Smooth rise, fast fall
        self._left  = max(left,  self._left  * (1.0 - self.DECAY))
        self._right = max(right, self._right * (1.0 - self.DECAY))

        # Peak hold left
        if left >= self._peak_l:
            self._peak_l = left
            self._hold_l = self.PEAK_HOLD
        else:
            if self._hold_l > 0:
                self._hold_l -= 1
            else:
                self._peak_l = max(0.0, self._peak_l - self.PEAK_DECAY)

        # Peak hold right
        if right >= self._peak_r:
            self._peak_r = right
            self._hold_r = self.PEAK_HOLD
        else:
            if self._hold_r > 0:
                self._hold_r -= 1
            else:
                self._peak_r = max(0.0, self._peak_r - self.PEAK_DECAY)

        self.update()

    def set_colors(self, primary: str, accent: str) -> None:
        self._primary = primary
        self._accent  = accent

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.update()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h   = self.width(), self.height()
        margin = 14
        bar_h  = (h - margin * 3) // 2
        ca     = QColor(self._accent)
        bar_x  = 30
        bar_w  = w - bar_x - 8

        for i, (level, peak, label) in enumerate([
            (self._left,  self._peak_l, "L"),
            (self._right, self._peak_r, "R"),
        ]):
            y     = margin + i * (bar_h + margin)
            fill_w = int(level * bar_w)

            # Background track
            painter.fillRect(bar_x, y, bar_w, bar_h, QColor(20, 20, 40))

            # Gradient fill
            if fill_w > 0:
                grad = QLinearGradient(bar_x, y, bar_x + bar_w, y)
                grad.setColorAt(0.0,  QColor("#00cc66"))   # green
                grad.setColorAt(0.6,  QColor("#aacc00"))   # yellow-green
                grad.setColorAt(0.75, QColor("#ffcc00"))   # yellow
                grad.setColorAt(0.9,  QColor("#ff6600"))   # orange
                grad.setColorAt(1.0,  QColor(self._primary))  # red
                painter.fillRect(bar_x, y, fill_w, bar_h, grad)

            # Peak hold tick
            pk_x = int(peak * bar_w)
            if pk_x > 2:
                pk_color = QColor("#ff4444") if peak > 0.9 else QColor("#ffffff")
                painter.fillRect(bar_x + pk_x - 2, y, 3, bar_h, pk_color)

            # Subtle segment lines
            painter.setPen(QColor(0, 0, 0, 60))
            for seg in range(1, 20):
                sx = bar_x + int(seg / 20 * bar_w)
                painter.drawLine(sx, y, sx, y + bar_h)

            # Channel label
            painter.setPen(ca)
            painter.drawText(0, y, bar_x - 2, bar_h, Qt.AlignmentFlag.AlignCenter, label)

            # dB scale ticks
            painter.setPen(QColor("#445566"))
            for threshold in [0.25, 0.5, 0.75, 1.0]:
                x_tick = bar_x + int(threshold * bar_w)
                painter.drawLine(x_tick, y + bar_h, x_tick, y + bar_h + 3)
