"""
config/settings.py
Handles application configuration: defaults, load, save, color utilities.
"""

import os
import json

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

CONFIG_PATH = os.path.expanduser("~/.config/quark_audio_player.json")
PLAYLIST_PATH = os.path.expanduser("~/.config/quark_audio_player_playlist.json")

# ---------------------------------------------------------------------------
# Audio
# ---------------------------------------------------------------------------

AUDIO_EXTENSIONS: set[str] = {
    ".mp3", ".flac", ".ogg", ".wav", ".aac", ".m4a", ".opus", ".wma"
}

MAX_HISTORY_SIZE = 100

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

DEFAULT_CONFIG: dict = {
    "primary_color":    "#e94560",
    "accent_color":     "#a8c0ff",
    "background_color": "#1a1a2e",
    "selection_color":  "#c73652",
    "fps":              60,
    "bar_count":        64,
    "flux_history":     1000,
    "max_cols":         200,
    "spectrogram_resolution": 15,  # frames per bin (15 @ 30fps = 0.5s)
    "font_family":      "Cantarell",
    "font_size":        13,
    "icon_style":       "neon",   # "neon" | "gradient" | "dash" | "filled"
    "shortcuts": {
        "play_pause": "Space",
        "next":       "Right",
        "previous":   "Left",
        "search":     "Ctrl+F",
        "undo":       "Ctrl+Z",
    },
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def is_audio(path: str) -> bool:
    """Return True if *path* has a recognised audio extension."""
    _, ext = os.path.splitext(path)
    return ext.lower() in AUDIO_EXTENSIONS


def derive_color(hex_color: str, delta: int) -> str:
    """Lighten (delta > 0) or darken (delta < 0) a hex color."""
    hex_color = hex_color.lstrip("#")
    r = max(0, min(255, int(hex_color[0:2], 16) + delta))
    g = max(0, min(255, int(hex_color[2:4], 16) + delta))
    b = max(0, min(255, int(hex_color[4:6], 16) + delta))
    return f"#{r:02x}{g:02x}{b:02x}"


def is_dark_bg(hex_color: str) -> bool:
    """True if hex_color should be treated as a dark background.
    ITU-R BT.601 perceptual luminance (green weighted far higher than red/
    blue, matching human brightness perception) — not a naive RGB mean.
    Shared by ui/style.py::build_stylesheet and MainWindow._apply_config so
    their dark/light classification (and the surface-lift/text-hierarchy
    direction that depends on it) can never diverge between the two."""
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return (0.299 * r + 0.587 * g + 0.114 * b) < 128


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def load_config() -> dict:
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH) as f:
                data = json.load(f)
            return {**DEFAULT_CONFIG, **data}
        except Exception:
            pass
    return DEFAULT_CONFIG.copy()


def save_config(config: dict) -> None:
    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
    with open(CONFIG_PATH, "w") as f:
        json.dump(config, f, indent=2)
