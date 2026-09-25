"""Colours and the Qt stylesheet shared by the client and the server console.

Looks: "midnight" (default, deep blue-grey), "light", "system" (light or midnight, following the Windows
setting) and "classic" (the original PyBlackBox black + lime), each with a choice of accent colour. `apply()` must run before any window is built:
widgets read the module-level colours (T.ACCENT, T.PANEL, ...) when they are created.
"""

import ctypes
import hashlib
import sys

THEMES = {"midnight": "Midnight (dark)", "light": "Light", "system": "Follow Windows (light or dark)",
          "classic": "Classic (black & lime)", "independence": "Independence Day (15 August)",
          "republic": "Republic Day (26 January)", "christmas": "Christmas"}

# Festival looks: a palette + fixed accent + decorations (stripe, home banner, login). They switch on by
# themselves on the day when "festival themes" is on; FESTIVAL holds the active one (or None).
FESTIVALS = {
    "independence": dict(name="Independence Day", days=[(8, 15)], accent="#ff9933",
                         greeting="Happy Independence Day", sub="Jai Hind!  ·  15 August", emoji="",
                         stripe=["#ff9933", "#ffffff", "#138808"], art="chakra", stickers="independence",
                         glow=["#ff9933", "#138808"]),
    "republic": dict(name="Republic Day", days=[(1, 26)], accent="#5b8cff",
                     greeting="Happy Republic Day", sub="Jai Hind!  ·  26 January", emoji="",
                     stripe=["#ff9933", "#ffffff", "#138808"], art="republic", stickers="republic",
                     glow=["#ff9933", "#138808"]),
    "christmas": dict(name="Christmas", days=[(12, 24), (12, 25), (12, 26)], accent="#e5484d",
                      greeting="Merry Christmas", sub="Wishing you joy, peace and a great year ahead", emoji="🎄",
                      stripe=["#e5484d", "#ffffff", "#e5484d", "#ffffff", "#1f8a4c"], art="snow", stickers="",
                      glow=["#e5484d", "#1f8a4c"], gold="#f2c14e"),
}
FESTIVAL = None
ACCENTS = {"violet": "#8b7bff", "blue": "#4c9aff", "teal": "#1fc7a8", "lime": "#bdff00",
           "orange": "#ff8a3d", "pink": "#ff5c9a"}

_PALETTES = {
    "midnight": dict(
        DARK=True, RAIL="#0b0d12", BG="#10131a", PANEL="#151924", SURFACE="#1d2230", SURFACE_HOVER="#272d3e",
        BORDER="#222838", TEXT="#e8eaf0", MUTED="#9aa2b5", FAINT="#687086", BUBBLE_OTHER="#1d2230",
        INPUT_FOCUS_BG="#141823", TINT="rgba(0,0,0,0.22)", TOOLTIP="#07080b", SCROLL="#2c3346",
        SCROLL_HOVER="#3d465e", DANGER="#ff5470", WARN_BG="#4a2a06", WARN_TEXT="#ffd6a8"),
    "light": dict(
        DARK=False, RAIL="#e8ebf2", BG="#f5f6fa", PANEL="#ffffff", SURFACE="#eef0f5", SURFACE_HOVER="#e2e6ee",
        BORDER="#e0e3eb", TEXT="#1b1f2a", MUTED="#5c6477", FAINT="#8b93a5", BUBBLE_OTHER="#ffffff",
        INPUT_FOCUS_BG="#ffffff", TINT="rgba(20,30,60,0.06)", TOOLTIP="#1b1f2a", SCROLL="#c9ceda",
        SCROLL_HOVER="#aab1c2", DANGER="#e5364f", WARN_BG="#ffe9cf", WARN_TEXT="#6b3b00"),
    "independence": dict(
        DARK=True, RAIL="#070b17", BG="#0b1022", PANEL="#10172d", SURFACE="#18213b", SURFACE_HOVER="#222c4a",
        BORDER="#1f2944", TEXT="#eef0f7", MUTED="#a3abc2", FAINT="#6b7592", BUBBLE_OTHER="#18213b",
        INPUT_FOCUS_BG="#0e1428", TINT="rgba(0,0,0,0.22)", TOOLTIP="#05070f", SCROLL="#2a3452",
        SCROLL_HOVER="#3b4870", DANGER="#ff5470", WARN_BG="#4a2a06", WARN_TEXT="#ffd6a8"),
    "republic": dict(
        DARK=True, RAIL="#060b19", BG="#0a1126", PANEL="#0f1831", SURFACE="#162343", SURFACE_HOVER="#1f2f56",
        BORDER="#1c2a4b", TEXT="#eef1fa", MUTED="#a1acc8", FAINT="#687497", BUBBLE_OTHER="#162343",
        INPUT_FOCUS_BG="#0c142c", TINT="rgba(0,0,0,0.22)", TOOLTIP="#040810", SCROLL="#27365c",
        SCROLL_HOVER="#38497a", DANGER="#ff5470", WARN_BG="#4a2a06", WARN_TEXT="#ffd6a8"),
    "christmas": dict(
        DARK=True, RAIL="#08110c", BG="#0d1812", PANEL="#122219", SURFACE="#1a2e22", SURFACE_HOVER="#233c2d",
        BORDER="#1e3326", TEXT="#f1f4ef", MUTED="#a9b8ab", FAINT="#6f8574", BUBBLE_OTHER="#1a2e22",
        INPUT_FOCUS_BG="#0f1d15", TINT="rgba(0,0,0,0.22)", TOOLTIP="#050a07", SCROLL="#2a4332",
        SCROLL_HOVER="#3a5a44", DANGER="#ff6b6b", WARN_BG="#4a2a06", WARN_TEXT="#ffd6a8"),
    "classic": dict(
        DARK=True, RAIL="#000000", BG="#151617", PANEL="#1e2021", SURFACE="#26282a", SURFACE_HOVER="#2f3133",
        BORDER="#2f3032", TEXT="#e6e6e6", MUTED="#a6a6a6", FAINT="#6b7074", BUBBLE_OTHER="#28282b",
        INPUT_FOCUS_BG="#0e0e0f", TINT="rgba(0,0,0,0.25)", TOOLTIP="#0b0b0c", SCROLL="#3a3c3f",
        SCROLL_HOVER="#55585c", DANGER="#ff4d6d", WARN_BG="#5a2a00", WARN_TEXT="#ffd2a6"),
}

# the current colours - set by apply() from a palette plus the accent
DARK = True
RAIL = BG = PANEL = SURFACE = SURFACE_HOVER = BORDER = TEXT = MUTED = FAINT = ""
BUBBLE_OTHER = INPUT_FOCUS_BG = TINT = TOOLTIP = SCROLL = SCROLL_HOVER = DANGER = WARN_BG = WARN_TEXT = ""
ACCENT = ACCENT_TEXT = ACCENT_HOVER = ACCENT_SOFT = ACCENT_FOCUS = BUBBLE_ME = ""

STATUS_COLORS = {
    "online": "#3ecf6e",
    "away": "#ffa24c",
    "busy": "#ff4d5e",
    "invisible": "#8a8f99",
    "offline": "#5a6070",
}
STATUS_LABELS = {
    "online": "Online",
    "away": "Away",
    "busy": "Do not disturb",
    "invisible": "Invisible",
    "offline": "Offline",
}

AVATAR_COLORS = ["#7c6cf0", "#12b886", "#3b8cf0", "#f0764a", "#e0445a", "#e64f9c",
                 "#15aabf", "#f2a73b", "#9775fa", "#20c997", "#fa8c6c", "#4dabf7"]


# ------------------------------------------------------------------ colour maths
def _rgb(c):
    c = c.lstrip("#")
    return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))


def _hex(rgb):
    return "#" + "".join(f"{max(0, min(255, round(v))):02x}" for v in rgb)


def mix(a, b, t):
    """Colour between a (t=1) and b (t=0)."""
    ra, rb = _rgb(a), _rgb(b)
    return _hex(ra[i] * t + rb[i] * (1 - t) for i in range(3))


def luminance(c):
    r, g, b = (v / 255 for v in _rgb(c))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def rgba(c, alpha):
    r, g, b = _rgb(c)
    return f"rgba({r},{g},{b},{alpha})"


# ------------------------------------------------------------------ current theme
THEME = "midnight"
ACCENT_NAME = "violet"
STYLESHEET = ""


def windows_uses_light() -> bool:
    """True when Windows is set to light mode for apps (Settings > Personalisation > Colours)."""
    if sys.platform != "win32":
        return False
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as key:
            return bool(winreg.QueryValueEx(key, "AppsUseLightTheme")[0])
    except OSError:
        return False


def festival_today(day=None):
    """Key of the festival on this date ('independence', 'republic', 'christmas') or None."""
    import datetime
    day = day or datetime.date.today()
    for key, f in FESTIVALS.items():
        if (day.month, day.day) in f["days"]:
            return key
    return None


def apply(theme="midnight", accent=None, festivals=False):
    """Switch the module colours to a theme + accent and rebuild STYLESHEET.

    festivals=True: on 15 Aug, 26 Jan and 24-26 Dec the festival look replaces the chosen theme."""
    global THEME, ACCENT_NAME, ACCENT, ACCENT_TEXT, ACCENT_HOVER, ACCENT_SOFT, ACCENT_FOCUS
    global BUBBLE_ME, STYLESHEET, FESTIVAL
    if festivals and festival_today():
        theme = festival_today()
    if theme == "system":
        theme = "light" if windows_uses_light() else "midnight"
    theme = theme if theme in _PALETTES else "midnight"
    FESTIVAL = FESTIVALS.get(theme)
    if accent not in ACCENTS:
        accent = "lime" if theme == "classic" else "violet"
    THEME, ACCENT_NAME = theme, accent
    pal = _PALETTES[theme]
    globals().update(pal)
    acc = FESTIVAL["accent"] if FESTIVAL else ACCENTS[accent]
    if not pal["DARK"] and luminance(acc) > 0.55:
        acc = mix(acc, "#000000", 0.62)           # bright accents are unreadable on white
    ACCENT = acc
    ACCENT_TEXT = "#10131a" if luminance(acc) > 0.55 else "#ffffff"
    ACCENT_HOVER = mix(acc, "#ffffff", 0.85) if pal["DARK"] else mix(acc, "#000000", 0.88)
    ACCENT_SOFT = mix(acc, pal["PANEL"], 0.20 if pal["DARK"] else 0.13)
    ACCENT_FOCUS = mix(acc, pal["SURFACE"], 0.55)
    BUBBLE_ME = mix(acc, pal["BG"], 0.26 if pal["DARK"] else 0.16)
    STYLESHEET = _stylesheet()
    return STYLESHEET


def avatar_color(key: str) -> str:
    h = int(hashlib.md5(key.encode("utf-8")).hexdigest(), 16)
    return AVATAR_COLORS[h % len(AVATAR_COLORS)]


def initials(name: str) -> str:
    """Two letters for an avatar, ignoring symbols/emoji ('<b>Bob Lee' -> 'BL')."""
    cleaned = "".join(ch if ch.isalnum() else " " for ch in name)
    parts = [p for p in cleaned.split() if p]
    if not parts:
        return "?"
    if len(parts) == 1:
        return parts[0][:2].upper()
    return (parts[0][0] + parts[-1][0]).upper()


def _stylesheet():
    return f"""
* {{ font-family: "Segoe UI"; font-size: 10pt; color: {TEXT}; }}
QMainWindow, QDialog, QWidget#root {{ background: {BG}; }}
QToolTip {{ background: {TOOLTIP}; color: #eef0f5; border: none; padding: 6px 10px; border-radius: 6px; }}
QLabel {{ background: transparent; }}
QLabel[muted="true"] {{ color: {MUTED}; }}
QLabel[heading="true"] {{ font-size: 15pt; font-weight: 700; }}

QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox, QComboBox {{
    background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 10px;
    padding: 7px 11px; selection-background-color: {ACCENT}; selection-color: {ACCENT_TEXT};
}}
QLineEdit:hover, QPlainTextEdit:hover, QTextEdit:hover, QSpinBox:hover, QComboBox:hover {{
    border: 1px solid {SURFACE_HOVER}; }}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus, QSpinBox:focus, QComboBox:focus {{
    border: 1px solid {ACCENT}; background: {INPUT_FOCUS_BG}; }}
QComboBox::drop-down {{ border: none; width: 24px; }}
QComboBox QAbstractItemView {{ background: {PANEL}; border: 1px solid {BORDER}; padding: 4px;
    selection-background-color: {ACCENT_SOFT}; selection-color: {TEXT}; outline: none; }}
QSpinBox::up-button, QSpinBox::down-button {{ width: 0; border: none; }}

QPushButton {{
    background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 10px; padding: 8px 16px;
    font-weight: 600;
}}
QPushButton:hover {{ background: {SURFACE_HOVER}; }}
QPushButton:pressed {{ background: {BORDER}; }}
QPushButton:disabled {{ color: {FAINT}; }}
QPushButton[primary="true"] {{ background: {ACCENT}; color: {ACCENT_TEXT}; border: none; }}
QPushButton[primary="true"]:hover {{ background: {ACCENT_HOVER}; }}
QPushButton[primary="true"]:disabled {{ background: {ACCENT_FOCUS}; color: {MUTED}; }}
QPushButton[danger="true"] {{ color: {DANGER}; }}
QPushButton[flat="true"] {{ background: transparent; border: none; padding: 6px; }}
QPushButton[flat="true"]:hover {{ background: {SURFACE_HOVER}; }}
QPushButton[chip="true"] {{ background: transparent; border: 1px solid {BORDER}; border-radius: 13px;
    padding: 3px 12px; font-weight: 600; font-size: 9pt; color: {MUTED}; }}
QPushButton[chip="true"]:hover {{ background: {SURFACE}; color: {TEXT}; }}
QPushButton[chip="true"]:checked {{ background: {ACCENT_SOFT}; border: 1px solid {ACCENT_FOCUS}; color: {TEXT}; }}

QCheckBox, QRadioButton {{ spacing: 8px; }}
QCheckBox::indicator {{ width: 16px; height: 16px; border-radius: 5px; background: {SURFACE};
    border: 1px solid {SCROLL}; }}
QCheckBox::indicator:checked {{ background: {ACCENT}; border: 1px solid {ACCENT}; }}

QScrollArea {{ border: none; background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 0; }}
QScrollBar::handle:vertical {{ background: {SCROLL}; border-radius: 3px; min-height: 30px; margin: 2px 3px; }}
QScrollBar::handle:vertical:hover {{ background: {SCROLL_HOVER}; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 0; }}
QScrollBar::handle:horizontal {{ background: {SCROLL}; border-radius: 3px; min-width: 30px; margin: 3px 2px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: none; }}

QTableWidget, QTableView, QListWidget, QTreeWidget {{
    background: {PANEL}; border: 1px solid {BORDER}; border-radius: 12px;
    gridline-color: {BORDER}; alternate-background-color: {BG}; outline: none;
    selection-background-color: {ACCENT_SOFT}; selection-color: {TEXT};
}}
QListWidget::item, QTreeWidget::item {{ padding: 5px; border-radius: 6px; }}
QListWidget::item:hover, QTreeWidget::item:hover {{ background: {SURFACE}; }}
QListWidget::item:selected, QTreeWidget::item:selected {{ background: {ACCENT_SOFT}; }}
QHeaderView::section {{ background: {PANEL}; border: none; border-bottom: 1px solid {BORDER};
    padding: 7px; font-weight: 600; color: {MUTED}; }}
QTableCornerButton::section {{ background: {PANEL}; border: none; }}

QMenu {{ background: {PANEL}; border: 1px solid {BORDER}; border-radius: 10px; padding: 5px; }}
QMenu::item {{ padding: 7px 24px 7px 12px; border-radius: 6px; }}
QMenu::item:selected {{ background: {SURFACE_HOVER}; }}
QMenu::item:disabled {{ color: {FAINT}; }}
QMenu::separator {{ height: 1px; background: {BORDER}; margin: 5px 8px; }}
QMenu::icon {{ padding-left: 6px; }}

QProgressBar {{ background: {SURFACE}; border: none; border-radius: 3px; height: 6px;
    text-align: center; color: transparent; }}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 3px; }}

QTabWidget::pane {{ border: none; }}
QTabBar::tab {{ background: transparent; padding: 8px 16px; color: {MUTED}; border: none;
    border-bottom: 2px solid transparent; font-weight: 600; }}
QTabBar::tab:hover {{ color: {TEXT}; }}
QTabBar::tab:selected {{ color: {TEXT}; border-bottom: 2px solid {ACCENT}; }}
QMessageBox {{ background: {PANEL}; }}
"""


apply()          # defaults until the app applies the user's choice


def dark_title_bar(widget):
    """Ask Windows 10/11 to draw a native title bar that matches the theme."""
    if sys.platform != "win32":
        return
    try:
        hwnd = int(widget.winId())
        value = ctypes.c_int(1 if DARK else 0)
        for attr in (20, 19):   # DWMWA_USE_IMMERSIVE_DARK_MODE (new, old)
            if ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(value),
                                                          ctypes.sizeof(value)) == 0:
                break
    except Exception:  # noqa: BLE001 - purely cosmetic
        pass


def bg_pane(widget, color=None):
    """Give one widget a background without leaking the style into its children."""
    from PySide6.QtCore import Qt
    if not widget.objectName():
        widget.setObjectName(f"pane{id(widget)}")
    widget.setAttribute(Qt.WA_StyledBackground, True)
    widget.setStyleSheet(f"#{widget.objectName()} {{ background: {color or BG}; }}")
    return widget


def polish(widget, **props):
    """Set dynamic style properties (e.g. primary=True) and re-apply the stylesheet."""
    for k, v in props.items():
        widget.setProperty(k, v)
    widget.style().unpolish(widget)
    widget.style().polish(widget)
    return widget
