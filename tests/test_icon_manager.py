"""
tests/test_icon_manager.py
Covers only the pure helper `toggle_icon_colors` and the static structure
of `ICON_MAP` from ui.icon_manager. IconManager's Qt-touching methods
(load_icon, refresh_all, set_play_icon, set_mute_icon, set_toggle_icon,
initial_button_icon) construct real QIcon/QPushButton objects and require
a live QApplication, which this project does not bootstrap yet (no
pytest-qt, no conftest.py) — they are intentionally not covered here.
"""

from ui.icon_manager import ICON_MAP, toggle_icon_colors

PRIMARY = "#e94560"
ACCENT = "#a8c0ff"


def test_toggle_icon_colors_active_swaps_primary_and_accent():
    assert toggle_icon_colors(PRIMARY, ACCENT, active=True) == (ACCENT, PRIMARY)


def test_toggle_icon_colors_inactive_returns_no_override():
    assert toggle_icon_colors(PRIMARY, ACCENT, active=False) == ("", "")


def test_icon_map_has_keys_indexed_directly_without_get():
    # These labels are looked up via ICON_MAP[...] (not .get()) somewhere
    # in IconManager/MainWindow, so a missing key would raise KeyError.
    for label in ("Muted", "Volume", ">", "||"):
        assert label in ICON_MAP


def test_icon_map_has_no_duplicate_icon_names():
    values = list(ICON_MAP.values())
    assert len(values) == len(set(values))
