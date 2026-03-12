"""
ui/style.py
Generates the application QSS stylesheet from config values.
"""

from config.settings import derive_color


def build_stylesheet(config: dict) -> str:
    cp  = config["primary_color"]      # accent (e.g. #e94560)
    ca  = config["accent_color"]       # secondary accent (e.g. #a8c0ff)
    cf  = config["background_color"]   # base background
    cs  = config["selection_color"]    # selection highlight

    # Surface hierarchy — very subtle steps from the base
    s1  = derive_color(cf,  8)   # cards / panels — barely lifted
    s2  = derive_color(cf, 16)   # hover states
    s3  = derive_color(cf, 28)   # active / pressed

    # Text hierarchy
    t1  = "#f0f0f8"   # primary text   — bright
    t2  = "#9098b0"   # secondary text — muted
    t3  = "#50586a"   # tertiary text  — very muted (labels, hints)

    ff       = config.get("font_family", "Cantarell")
    ft       = config.get("font_size",   13)
    ft_section = ft + 4          # section labels slightly bigger than base
    ft_small   = max(10, ft - 2) # tabs / hints slightly smaller

    return f"""
        /* ── Reset / base ───────────────────────────────────────── */
        * {{
            outline: none;
        }}
        QMainWindow, QWidget {{
            background-color: {cf};
            color: {t1};
            font-family: '{ff}';
            font-size: {ft}px;
            border: none;
        }}

        /* ── Panel structure ────────────────────────────────────── */
        #leftPanel {{
            background-color: {cf};
            border-right: 1px solid {s2};
        }}
        #rightPanel {{
            background-color: {cf};
        }}
        #sectionLabel {{
            background-color: transparent;
            color: {t1};
            font-size: {ft_section}px;
            font-weight: bold;
            letter-spacing: 1px;
            padding: 10px 14px 8px 14px;
            border-bottom: 1px solid {s2};
        }}

        /* ── File browser tree ──────────────────────────────────── */
        QTreeView {{
            background-color: transparent;
            color: {t2};
            border: none;
            outline: none;
            selection-background-color: transparent;
        }}
        QTreeView::item {{
            padding: 4px 6px;
            border-radius: 5px;
            color: {t1};
        }}
        QTreeView::item:hover {{
            background-color: {s1};
            color: {t1};
        }}
        QTreeView::item:selected {{
            background-color: {s2};
            color: {ca};
        }}
        QTreeView::branch {{
            background: transparent;
        }}

        /* ── Playlist ───────────────────────────────────────────── */
        QTreeWidget {{
            background-color: transparent;
            color: {t2};
            border: none;
            outline: none;
            selection-background-color: transparent;
            alternate-background-color: transparent;
        }}
        QTreeWidget::item {{
            padding: 8px 10px;
            border-bottom: 1px solid {s1};
            color: {t1};
        }}
        QTreeWidget::item:hover {{
            background-color: {s1};
            color: {t1};
            border-bottom: 1px solid {s2};
        }}
        QTreeWidget::item:selected {{
            background-color: {s2};
            color: {t1};
            border-left: 2px solid {cp};
            border-bottom: 1px solid {s2};
        }}

        /* ── Playlist header ────────────────────────────────────── */
        QHeaderView {{
            background-color: transparent;
            border: none;
        }}
        QHeaderView::section {{
            background-color: {s1};
            color: {t1};
            font-size: {ft}px;
            font-weight: normal;
            padding: 7px 10px;
            border: none;
            border-bottom: 1px solid {s2};
            border-right: 2px solid {cp};
        }}
        QHeaderView::section:last {{
            border-right: none;
        }}
        QHeaderView::section:hover {{
            background-color: {s2};
            color: {ca};
        }}

        /* ── Control bar ────────────────────────────────────────── */
        #controlBar {{
            background-color: {s1};
            border-top: 1px solid {s2};
        }}
        /* ── Now-playing info labels ────────────────────────────── */
        #labelArtist {{
            color: {ca};
            font-size: {ft}px;
            font-weight: normal;
            padding: 0px 4px;
        }}
        #labelTitle {{
            color: {ca};
            font-size: {ft}px;
            font-weight: bold;
            padding: 0px 4px;
        }}
        #labelTech {{
            color: {ca};
            font-size: {ft}px;
            font-weight: normal;
            padding: 0px 4px;
        }}
        #labelYear {{
            color: {ca};
            font-size: {ft}px;
            font-weight: normal;
            padding: 0px 4px;
        }}

        /* ── Buttons ────────────────────────────────────────────── */
        #controlButton {{
            background-color: transparent;
            color: {t2};
            border: none;
            border-radius: 8px;
            padding: 0px;
        }}
        #controlButton:hover {{
            background-color: {s2};
            color: {t1};
        }}
        #controlButton:pressed {{
            background-color: {s3};
        }}
        #controlButton:checked {{
            background-color: transparent;
            color: {cp};
        }}
        #controlButton:checked:hover {{
            background-color: {s2};
        }}

        /* ── Progress / volume sliders ──────────────────────────── */
        #timeLabel {{
            color: {t1};
            font-size: {ft}px;
            font-family: {ff};
        }}
        QSlider {{
            background: transparent;
        }}
        QSlider::groove:horizontal {{
            background: {s2};
            height: 3px;
            border-radius: 2px;
        }}
        QSlider::sub-page:horizontal {{
            background: qlineargradient(
                x1:0, y1:0, x2:1, y2:0,
                stop:0 {cp}, stop:1 {ca}
            );
            height: 3px;
            border-radius: 2px;
        }}
        QSlider::handle:horizontal {{
            background: {t1};
            width: 10px;
            height: 10px;
            margin: -4px 0;
            border-radius: 5px;
        }}
        QSlider::handle:horizontal:hover {{
            background: {ca};
            width: 12px;
            height: 12px;
            margin: -5px 0;
            border-radius: 6px;
        }}

        /* ── Scrollbar ──────────────────────────────────────────── */
        QScrollBar:vertical {{
            background: transparent;
            width: 4px;
            margin: 0;
        }}
        QScrollBar::handle:vertical {{
            background: {s3};
            border-radius: 2px;
            min-height: 32px;
        }}
        QScrollBar::handle:vertical:hover {{
            background: {t3};
        }}
        QScrollBar::add-line:vertical,
        QScrollBar::sub-line:vertical {{
            height: 0;
        }}
        QScrollBar:horizontal {{
            background: transparent;
            height: 4px;
        }}
        QScrollBar::handle:horizontal {{
            background: {s3};
            border-radius: 2px;
        }}
        QScrollBar::add-line:horizontal,
        QScrollBar::sub-line:horizontal {{
            width: 0;
        }}

        /* ── Status bar ─────────────────────────────────────────── */
        QStatusBar {{
            background-color: {cf};
            color: {t1};
            font-size: 11px;
            border-top: 1px solid {s1};
        }}

        /* ── Album art ──────────────────────────────────────────── */
        #albumArt {{
            background-color: {s2};
            border-radius: 10px;
            color: {t3};
            font-size: 28px;
        }}

        /* ── Tabs ───────────────────────────────────────────────── */
        QTabWidget::pane {{
            border: none;
            background: transparent;
        }}
        QTabBar {{
            background: transparent;
        }}
        QTabBar::tab {{
            background: transparent;
            color: {t2};
            padding: 7px 16px;
            border: none;
            font-size: {ft_small}px;
            font-weight: normal;
        }}
        QTabBar::tab:selected {{
            color: {ca};
            font-weight: bold;
            border-bottom: 2px solid {cp};
        }}
        QTabBar::tab:hover:!selected {{
            color: {t1};
            background: transparent;
        }}

        /* ── Search bar ─────────────────────────────────────────── */
        QLineEdit {{
            background-color: {s1};
            color: {t1};
            border: 1px solid {s2};
            border-radius: 6px;
            padding: 5px 10px;
            selection-background-color: {cs};
        }}
        QLineEdit:focus {{
            border-color: {cp};
            background-color: {s2};
        }}
        QLineEdit::placeholder {{
            color: {t3};
        }}

        /* ── Dialogs (Settings, EQ) ─────────────────────────────── */
        QDialog {{
            background-color: {cf};
        }}
        QLabel {{
            color: {t2};
            background: transparent;
        }}
        QPushButton {{
            background-color: {s2};
            color: {t1};
            border: none;
            border-radius: 7px;
            padding: 7px 18px;
            font-weight: bold;
        }}
        QPushButton:hover   {{ background-color: {s3}; }}
        QPushButton:pressed {{ background-color: {cs}; color: white; }}
        QPushButton:default {{
            background-color: {cp};
            color: white;
        }}
        QPushButton:default:hover {{ background-color: {cs}; }}

        QSpinBox, QDoubleSpinBox, QComboBox {{
            background-color: {s1};
            color: {t1};
            border: 1px solid {s2};
            border-radius: 6px;
            padding: 4px 8px;
            selection-background-color: {cs};
        }}
        QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {{
            border-color: {cp};
        }}
        QSpinBox::up-button, QSpinBox::down-button,
        QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{
            background: {s2};
            border: none;
            width: 16px;
        }}
        QComboBox::drop-down {{
            border: none;
            width: 22px;
            background: transparent;
        }}
        QComboBox QAbstractItemView {{
            background-color: {s1};
            border: 1px solid {s3};
            selection-background-color: {cs};
            color: {t1};
            outline: none;
        }}

        QTextEdit {{
            background-color: {s1};
            color: {t2};
            border: none;
            border-radius: 6px;
            padding: 6px;
            selection-background-color: {cs};
        }}

        /* ── EQ sliders (vertical) ──────────────────────────────── */
        QSlider#eqSlider::groove:vertical {{
            background: {s2};
            width: 3px;
            border-radius: 2px;
        }}
        QSlider#eqSlider::sub-page:vertical {{
            background: {ca};
            border-radius: 2px;
        }}
        QSlider#eqSlider::add-page:vertical {{
            background: {s2};
            border-radius: 2px;
        }}
        QSlider#eqSlider::handle:vertical {{
            background: {cp};
            width: 12px;
            height: 12px;
            margin: 0 -5px;
            border-radius: 6px;
        }}
        QSlider#eqSlider::handle:vertical:hover {{
            background: {t1};
        }}

        /* ── Context menus ──────────────────────────────────────── */
        QMenu {{
            background-color: {s1};
            border: 1px solid {s2};
            border-radius: 8px;
            padding: 4px;
            color: {t1};
        }}
        QMenu::item {{
            padding: 7px 20px;
            border-radius: 5px;
        }}
        QMenu::item:selected {{
            background-color: {s2};
            color: {ca};
        }}
        QMenu::separator {{
            height: 1px;
            background: {s2};
            margin: 4px 10px;
        }}

        /* ── Tooltip ────────────────────────────────────────────── */
        QToolTip {{
            background-color: {s3};
            color: {t1};
            border: 1px solid {s2};
            border-radius: 5px;
            padding: 4px 8px;
            font-size: 11px;
        }}
    """
