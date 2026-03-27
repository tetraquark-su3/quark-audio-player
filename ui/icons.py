"""
ui/icons.py
Dynamic SVG icon generation — three styles, fully theme-aware.

Each icon is a pure function returning an SVG string.
_render_svg() converts it to a QPixmap via Qt's SVG renderer.
_load_icon() is the public entry point used by MainWindow.
"""

from __future__ import annotations
import os
from typing import Optional

from PyQt6.QtCore    import QByteArray, QRectF, Qt, QSize
from PyQt6.QtGui     import QIcon, QPixmap, QPainter, QColor
from PyQt6.QtSvg     import QSvgRenderer

# ---------------------------------------------------------------------------
# Style constants
# ---------------------------------------------------------------------------

ICON_STYLES = ["neon", "gradient", "dash"]
DEFAULT_STYLE = "neon"

# ---------------------------------------------------------------------------
# SVG helpers
# ---------------------------------------------------------------------------

def _svg(body: str, size: int = 64) -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'viewBox="0 0 {size} {size}" width="{size}" height="{size}">'
        f'{body}</svg>'
    )


def _render_svg(svg_str: str, pixel_size: int = 32) -> Optional[QPixmap]:
    try:
        renderer = QSvgRenderer(QByteArray(svg_str.encode()))
        if not renderer.isValid():
            return None
        px = QPixmap(pixel_size, pixel_size)
        px.fill(Qt.GlobalColor.transparent)
        painter = QPainter(px)
        renderer.render(painter, QRectF(0, 0, pixel_size, pixel_size))
        painter.end()
        return px
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Per-style attribute builders
# ---------------------------------------------------------------------------

def _attrs(style: str, primary: str, accent: str) -> dict:
    """Return a dict of SVG presentation attributes for the given style."""
    if style == "neon":
        d = {
            "fill":         "none",
            "stroke":       accent,
            "stroke_width": "3",
            "filter":       f'filter="url(#glow)"',
            "defs": (
                f'<defs>'
                f'<filter id="glow" x="-50%" y="-50%" width="200%" height="200%">'
                f'<feGaussianBlur stdDeviation="2.5" result="blur"/>'
                f'<feMerge><feMergeNode in="blur"/><feMergeNode in="SourceGraphic"/></feMerge>'
                f'</filter>'
                f'</defs>'
            ),
            "fill_accent":   "none",
            "stroke_accent": primary,
        }
    elif style == "gradient":
        d = {
            "fill":         "url(#grad)",
            "stroke":       "none",
            "stroke_width": "0",
            "filter":       "",
            "defs": (
                f'<defs>'
                f'<linearGradient id="grad" x1="0" y1="0" x2="1" y2="1">'
                f'<stop offset="0%" stop-color="{primary}"/>'
                f'<stop offset="100%" stop-color="{accent}"/>'
                f'</linearGradient>'
                f'</defs>'
            ),
            "fill_accent":   "url(#grad)",
            "stroke_accent": "none",
        }
    elif style == "filled":
        d = {
            "fill":         accent,
            "stroke":       primary,
            "stroke_width": "2",
            "filter":       "",
            "defs":         "",
            "fill_accent":  primary,
            "stroke_accent": accent,
        }
    else:  # dash
        d = {
            "fill":         "none",
            "stroke":       accent,
            "stroke_width": "2.5",
            "filter":       "",
            "defs":         "",
            "fill_accent":  "none",
            "stroke_accent": primary,
            "_dash":        "stroke-dasharray=\"6 3\"",
        }
    # Always inject raw colors and style name for icons that need them
    d["_primary"] = primary
    d["_accent"]  = accent
    d["_style"]   = style
    return d

def _hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    h = hex_color.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _path(d: str, a: dict, use_accent: bool = False) -> str:
    fill   = a["fill_accent"]   if use_accent else a["fill"]
    stroke = a["stroke_accent"] if use_accent else a["stroke"]
    sw     = a["stroke_width"]
    filt   = a["filter"]
    dash   = a.get("_dash", "")
    return (
        f'<path d="{d}" fill="{fill}" stroke="{stroke}" '
        f'stroke-width="{sw}" stroke-linecap="round" stroke-linejoin="round" {dash} {filt}/>'
    )


def _rect(x, y, w, h, rx, a: dict, use_accent: bool = False) -> str:
    fill   = a["fill_accent"]   if use_accent else a["fill"]
    stroke = a["stroke_accent"] if use_accent else a["stroke"]
    sw     = a["stroke_width"]
    filt   = a["filter"]
    dash   = a.get("_dash", "")
    return (
        f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{rx}" '
        f'fill="{fill}" stroke="{stroke}" stroke-width="{sw}" {dash} {filt}/>'
    )


def _circle(cx, cy, r, a: dict, use_accent: bool = False) -> str:
    fill   = a["fill_accent"]   if use_accent else a["fill"]
    stroke = a["stroke_accent"] if use_accent else a["stroke"]
    sw     = a["stroke_width"]
    filt   = a["filter"]
    dash   = a.get("_dash", "")
    return (
        f'<circle cx="{cx}" cy="{cy}" r="{r}" '
        f'fill="{fill}" stroke="{stroke}" stroke-width="{sw}" {dash} {filt}/>'
    )


# ---------------------------------------------------------------------------
# Icon definitions  (64×64 viewBox)
# ---------------------------------------------------------------------------

def _icon_play(a: dict) -> str:
    return _svg(a["defs"] + _path("M18 12 L50 32 L18 52 Z", a))

def _icon_pause(a: dict) -> str:
    return _svg(a["defs"] +
        _rect(14, 12, 13, 40, 3, a) +
        _rect(37, 12, 13, 40, 3, a))

def _icon_stop(a: dict) -> str:
    return _svg(a["defs"] + _rect(12, 12, 40, 40, 4, a))

def _icon_prev(a: dict) -> str:
    return _svg(a["defs"] +
        _rect(10, 12, 5, 40, 2, a, use_accent=True) +
        _path("M54 12 L22 32 L54 52 Z", a))

def _icon_next(a: dict) -> str:
    return _svg(a["defs"] +
        _path("M10 12 L42 32 L10 52 Z", a) +
        _rect(49, 12, 5, 40, 2, a, use_accent=True))

def _icon_shuffle(a: dict) -> str:
    return _svg(a["defs"] + _path(
        "M10 20 Q26 20 34 32 Q42 44 58 44 M50 38 L58 44 L50 50 "
        "M58 20 Q42 20 34 32 Q26 44 10 44 "
        "M50 14 L58 20 L50 26", a))

def _icon_repeat(a: dict) -> str:
    return _svg(a["defs"] + _path(
        "M14 22 Q14 14 22 14 L46 14 M40 8 L46 14 L40 20 "
        "M50 42 Q50 50 42 50 L18 50 M24 56 L18 50 L24 44", a))

def _icon_volume(a: dict) -> str:
    return _svg(a["defs"] +
        _path("M8 24 L8 40 L20 40 L36 52 L36 12 L20 24 Z", a) +
        _path("M42 22 Q52 32 42 42", a, use_accent=True) +
        _path("M46 14 Q62 32 46 50", a, use_accent=True))

def _icon_mute(a: dict) -> str:
    return _svg(a["defs"] +
        _path("M8 24 L8 40 L20 40 L36 52 L36 12 L20 24 Z", a) +
        _path("M44 24 L56 40 M56 24 L44 40", a, use_accent=True))

def _icon_settings(a: dict) -> str:
    import math
    color        = a["stroke"] if a["stroke"] != "none" else a["fill"]
    accent_color = a["stroke_accent"] if a["stroke_accent"] != "none" else a["fill_accent"]
    sw = a["stroke_width"] if a["stroke_width"] != "0" else "3"
    teeth = ""
    for i in range(8):
        angle = i * math.pi / 4
        cx = 32 + 19 * math.cos(angle)
        cy = 32 + 19 * math.sin(angle)
        rot = math.degrees(angle)
        teeth += (
            f'<rect x="{cx-3:.1f}" y="{cy-3:.1f}" width="6" height="6" rx="1.5" '
            f'fill="{color}" stroke="none" '
            f'transform="rotate({rot:.1f} {cx:.1f} {cy:.1f})"/>'
        )
    return _svg(
        a["defs"] +
        f'<circle cx="32" cy="32" r="16" fill="none" stroke="{color}" stroke-width="{sw}"/>' +
        teeth +
        f'<circle cx="32" cy="32" r="7" fill="none" stroke="{accent_color}" stroke-width="{sw}"/>'
    )


def _icon_eq(a: dict) -> str:
    bars = [(10,38,8,16),(20,26,8,28),(30,18,8,36),(40,30,8,24),(50,22,8,32)]
    color  = a["stroke"] if a["stroke"] != "none" else a["fill"]
    accent = a["stroke_accent"] if a["stroke_accent"] != "none" else a["fill_accent"]
    sw     = a["stroke_width"] if a["stroke_width"] != "0" else "2.5"
    body   = a["defs"]
    for i, (x, y, w, h) in enumerate(bars):
        c    = accent if i % 2 == 1 else color
        fill = c if a["stroke"] == "none" else "none"
        body += f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="3" fill="{fill}" stroke="{c}" stroke-width="{sw}"/>'
        mid_y = y + h // 2
        body += f'<line x1="{x-2}" y1="{mid_y}" x2="{x+w+2}" y2="{mid_y}" stroke="{c}" stroke-width="{sw}" stroke-linecap="round"/>'
    return _svg(body)


def _icon_save(a: dict) -> str:
    # Floppy disk
    return _svg(a["defs"] +
        _rect(10, 10, 44, 44, 4, a) +
        _rect(18, 10, 28, 18, 2, a, use_accent=True) +
        _rect(20, 36, 24, 16, 2, a, use_accent=True))

def _icon_load(a: dict) -> str:
    # Circle with downward arrow
    sw = a["stroke_width"] if a["stroke_width"] != "0" else "3"
    # Use gradient stroke if in gradient style, otherwise normal stroke color
    circle_stroke = "url(#grad)" if a["stroke"] == "none" else a["stroke"]
    return _svg(a["defs"] +
        _path("M32 18 L32 46 M22 36 L32 46 L42 36", a) +
        f'<circle cx="32" cy="32" r="22" fill="none" '
        f'stroke="{circle_stroke}" stroke-width="{sw}"/>'
    )

def _icon_no_art(a: dict) -> str:
    """Album cover placeholder: a stylised vinyl record (concentric circles)."""
    color  = a.get("_accent",  "#ffffff")
    accent = a.get("_primary", "#aaaaaa")
    sw     = a["stroke_width"] if a["stroke_width"] != "0" else "2.5"
    r, g, b = _hex_to_rgb(color)
    fill_bg = "rgba({},{},{},0.15)".format(r, g, b)
    filt    = a["filter"]
    dash    = a.get("_dash", "")
    style   = a.get("_style", "neon")

    if style == "gradient":
        outer_stroke = "url(#grad)"
        label_stroke = "url(#grad)"
        hole_fill    = "url(#grad)"
        outer_fill   = fill_bg
    elif style == "filled":
        outer_stroke = accent
        label_stroke = color
        hole_fill    = color
        outer_fill   = fill_bg
    else:  # neon, dash
        outer_stroke = color
        label_stroke = accent
        hole_fill    = accent
        outer_fill   = fill_bg

    parts = [
        a["defs"],
        f'<circle cx="32" cy="32" r="26" fill="{outer_fill}" stroke="{outer_stroke}" stroke-width="{sw}" {dash} {filt}/>',
        f'<circle cx="32" cy="32" r="12" fill="none" stroke="{label_stroke}" stroke-width="{sw}" {dash} {filt}/>',
        f'<circle cx="32" cy="32" r="19" fill="none" stroke="{outer_stroke}" stroke-width="1" opacity="0.4" {filt}/>',
        f'<circle cx="32" cy="32" r="22" fill="none" stroke="{outer_stroke}" stroke-width="1" opacity="0.25" {filt}/>',
        f'<circle cx="32" cy="32" r="3" fill="{hole_fill}" stroke="none"/>',
    ]
    return _svg("".join(parts))

# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

_ICON_BUILDERS = {
    "icon_play":     _icon_play,
    "icon_pause":    _icon_pause,
    "icon_stop":     _icon_stop,
    "icon_prev":     _icon_prev,
    "icon_next":     _icon_next,
    "icon_shuffle":  _icon_shuffle,
    "icon_repeat":   _icon_repeat,
    "icon_volume":   _icon_volume,
    "icon_mute":     _icon_mute,
    "icon_settings": _icon_settings,
    "icon_eq":       _icon_eq,
    "icon_save":     _icon_save,
    "icon_load":     _icon_load,
    "icon_no_art":   _icon_no_art,
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def render_no_art_pixmap(
    style: str,
    primary: str,
    accent: str,
    size: int = 150,
) -> "QPixmap":
    """
    Return a QPixmap of the 'no art' vinyl icon at *size* pixels.
    Used directly by MainWindow._show_no_art() to fill the album art label.
    """
    a   = _attrs(style, primary, accent)
    svg = _icon_no_art(a)
    px  = _render_svg(svg, size)
    if px is None:
        from PyQt6.QtGui import QPixmap
        return QPixmap()
    return px


def load_icon(
    name: str,
    style: str,
    primary: str,
    accent: str,
    pixel_size: int = 32,
    assets_dir: str = "",
) -> QIcon:
    """
    Return a QIcon for *name* rendered in *style* with *primary*/*accent* colors.
    Falls back to the PNG in *assets_dir* if SVG rendering fails.
    """
    builder = _ICON_BUILDERS.get(name)
    if builder is not None:
        try:
            a   = _attrs(style, primary, accent)
            svg = builder(a)
            px  = _render_svg(svg, pixel_size)
            if px is not None:
                return QIcon(px)
        except Exception as e:
            print(f"[icons] SVG render failed for {name}: {e}")

    # PNG fallback
    if assets_dir:
        path = os.path.join(assets_dir, f"{name}.png")
        if os.path.exists(path):
            return QIcon(path)

    return QIcon()