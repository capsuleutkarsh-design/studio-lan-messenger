"""Colours and the Qt stylesheet shared by the client and the server console.

Looks: "midnight" (default, deep blue-grey), "light", "system" (light or midnight, following the Windows
setting) and "classic" (the original PyBlackBox black + lime), each with a choice of accent colour. `apply()` must run before any window is built:
widgets read the module-level colours (T.ACCENT, T.PANEL, ...) when they are created.
"""

import ctypes
import hashlib
import os
import sys

THEMES = {"midnight": "Midnight (dark)", "light": "Light", "system": "Follow Windows (light or dark)",
          "classic": "Classic (black & lime)", "independence": "Independence Day (15 August)",
          "republic": "Republic Day (26 January)", "christmas": "Christmas"}

# Festival looks: a palette + fixed accent + decorations (stripe, home banner, login). They switch on by
# themselves on the day when "festival themes" is on; FESTIVAL holds the active one (or None).
FESTIVALS = {
    "independence": dict(name="Independence Day", days=[(8, 15)], accent="#ff9933",
                         greeting="Happy Independence Day", sub="Jai Hind!  ·  15 August", emoji="",
                         stripe=["#ff9933", "#ffffff", "#138808"], art="chakra", stickers="festival_icons",
                         glow=["#ff9933", "#138808"]),
    "republic": dict(name="Republic Day", days=[(1, 26)], accent="#5b8cff",
                     greeting="Happy Republic Day", sub="Jai Hind!  ·  26 January", emoji="",
                     stripe=["#ff9933", "#ffffff", "#138808"], art="republic", stickers="festival_icons",
                     glow=["#ff9933", "#138808"]),
    "christmas": dict(name="Christmas", days=[(12, 24), (12, 25), (12, 26)], accent="#e5484d",
                      greeting="Merry Christmas", sub="Wishing you joy, peace and a great year ahead", emoji="🎄",
                      stripe=["#e5484d", "#ffffff", "#e5484d", "#ffffff", "#1f8a4c"], art="snow", stickers="festival_icons",
                      glow=["#e5484d", "#1f8a4c"], gold="#f2c14e"),
}
FESTIVAL = None
# "quillo" is the logo's teal; with the dark theme it also turns the surfaces navy (see QUILLO_NAVY)
ACCENTS = {"quillo": "#3cc8b4", "violet": "#8b7bff", "blue": "#4c9aff", "teal": "#1fc7a8", "lime": "#bdff00",
           "orange": "#ff8a3d", "pink": "#ff5c9a"}
ACCENT_NAMES = {"quillo": "Quillo (navy & teal)"}
DEFAULT_ACCENT = "quillo"

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

# Midnight with the Quillo accent: the logo's deep navy instead of blue-grey
QUILLO_NAVY = dict(
    RAIL="#081030", BG="#0b1433", PANEL="#101b40", SURFACE="#17244f", SURFACE_HOVER="#20305f",
    BORDER="#1d2b58", MUTED="#9ea9c8", FAINT="#67739a", BUBBLE_OTHER="#17244f", INPUT_FOCUS_BG="#0d173a",
    TOOLTIP="#050a1f", SCROLL="#27376a", SCROLL_HOVER="#3a4c86")
# Light with the Quillo accent: a navy tint in the rail and buttons, like the logo on white
QUILLO_LIGHT = dict(RAIL="#e6ebf5", SURFACE="#edf1f8", SURFACE_HOVER="#e0e7f3", BORDER="#dce3ef")

# the current colours - set by apply() from a palette plus the accent
DARK = True
RAIL = BG = PANEL = SURFACE = SURFACE_HOVER = BORDER = TEXT = MUTED = FAINT = ""
BUBBLE_OTHER = INPUT_FOCUS_BG = TINT = TOOLTIP = SCROLL = SCROLL_HOVER = DANGER = WARN_BG = WARN_TEXT = ""
ACCENT = ACCENT_TEXT = ACCENT_HOVER = ACCENT_SOFT = ACCENT_FOCUS = BUBBLE_ME = ""
HAIR = ""          # a soft hairline, halfway between BORDER and PANEL - edges you notice without seeing
# Text colours by job: TEXT for content, MUTED for secondary lines, META for small information people still need
# to read (times, sub-labels, hints, dates; 4.5:1 on PANEL, BG and SURFACE), FAINT only for decoration and
# disabled text (it is below 4.5:1 - and it colours the licence line, which must stay as it is).
META = ""
FLOAT_BORDER = ""  # the edge of floating surfaces (menus, pop-ups, drop-down lists): firmer than HAIR
CONTROL_EDGE = ""  # the outline of unticked check boxes (SCROLL alone nearly vanishes on the light theme)
# Count badges: neutral (ACCENT - active transfers, unread in a quiet chat), alert (DANGER - unread for you,
# failed), muted (a muted chat). See badge_colors().
BADGE_BG = BADGE_TEXT = BADGE_ALERT_BG = BADGE_ALERT_TEXT = BADGE_MUTED_BG = BADGE_MUTED_TEXT = ""

STATUS_COLORS = {
    "online": "#3ecf6e",
    "away": "#ffa24c",
    "busy": "#ff4d5e",
    "invisible": "#8a8f99",
    "offline": "#5a6070",          # light themes; apply() swaps in OFFLINE_DARK on dark ones
}
OFFLINE_DARK = "#7c859c"           # the dark grey vanished on navy / black panels (2.7:1)
STATUS_LABELS = {
    "online": "Online",
    "away": "Away",
    "busy": "Do not disturb",
    "invisible": "Invisible",
    "offline": "Offline",
}

# Twelve clearly different hues, each dark enough for white initials (3.4:1 or more). Everyone keeps their place in
# the list, so a person's colour only shifts shade (the three oranges became orange, olive and indigo).
AVATAR_COLORS = ["#7c6cf0", "#129f5d", "#3b8cf0", "#e25a1c", "#e0445a", "#e64f9c",
                 "#0f98b4", "#bf7d0a", "#a35bd0", "#0d9c89", "#649926", "#4c5fd6"]

# ------------------------------------------------------------------ type, spacing and corner scale
# Use these in new or touched code instead of one-off numbers (callers are moved over as they are edited).
# Text sizes are in pt for style sheets - pt(FONT_S) gives "9pt" - and px(FONT_S) gives pixels for QPainter
# fonts (QFont.setPixelSize). Settings > Text size scales the whole app through QT_SCALE_FACTOR, so the sizes need
# no zoom of their own; chat zoom (Ctrl + / Ctrl -) passes its factor: pt(FONT_BODY, zoom=pct / 100).
FONT_XS = 8        # section captions (upper case, letter-spaced), badges
FONT_S = 9         # meta: times, sub-labels, hints, chips
FONT_M = 10        # controls and ordinary text (the style sheet's default)
FONT_BODY = 10.5   # message text
FONT_L = 11.5      # card, pop-up and dialog titles
FONT_XL = 13       # panel titles (chat header name)
FONT_XXL = 15      # page headings (QLabel[heading="true"])
# Spacing: a 4 px step. Gutters: 16 inside the sidebar, 24 around pages and chats.
SPACE_XS, SPACE_S, SPACE_M, SPACE_L, SPACE_XL, SPACE_XXL = 4, 8, 12, 16, 20, 24
GUTTER, PAGE_GUTTER = 16, 24
# Corners: 6 small bits (check boxes, quotes, tags), 10 rows / icon buttons / menu items, 12 inputs and buttons,
# 14 menus, cards and pop-ups, 18 message bubbles and big panels. A pill is height // 2.
RADIUS_S, RADIUS_M, RADIUS_CONTROL, RADIUS_L, RADIUS_XL = 6, 10, 12, 14, 18


def pt(size, zoom=1.0) -> str:
    """A style-sheet font size: pt(FONT_S) -> '9pt'."""
    return f"{size * zoom:g}pt"


def px(size, zoom=1.0) -> int:
    """A scale size (pt) as pixels at 96 dpi, for QFont.setPixelSize in painted widgets: px(FONT_M) -> 13."""
    return max(1, round(size * zoom * 4 / 3))


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


def _rel_lum(c):
    """WCAG relative luminance (luminance() above is the quick, uncorrected one the accent rules use)."""
    def lin(v):
        v /= 255
        return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = _rgb(c)
    return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)


def contrast(a, b) -> float:
    """WCAG contrast ratio of two colours: 1 (same) .. 21 (black on white). Text wants 4.5, big text and
    icons 3."""
    la, lb = sorted((_rel_lum(a), _rel_lum(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def readable_on(fg, bg, ratio=4.5):
    """fg, darkened (on a light bg) or lightened (on a dark bg) just enough to reach `ratio` against bg.

    bg can be a list of backgrounds: the result is readable on all of them. Colours that already pass come back
    unchanged, so the hue stays recognisable."""
    bgs = list(bg) if isinstance(bg, (list, tuple)) else [bg]
    if min(contrast(fg, b) for b in bgs) >= ratio:
        return fg
    toward = "#000000" if sum(_rel_lum(b) for b in bgs) / len(bgs) > 0.18 else "#ffffff"
    for step in range(1, 41):
        c = mix(toward, fg, step / 40)
        if min(contrast(c, b) for b in bgs) >= ratio:
            return c
    return toward


def ink_on(bg):
    """White or near-black text for a coloured background (badges, chips, avatars), whichever reads better."""
    return "#ffffff" if contrast("#ffffff", bg) >= contrast("#10131a", bg) else "#10131a"


def name_color(key_or_color, bg=None):
    """A person's colour for text - sender names, reply-quote names and bars, calendar kind labels.

    Takes an avatar key (the same key as avatar_color) or a colour, and keeps its hue but makes it readable
    (4.5:1) on bg - by default the panel and the other person's bubble."""
    c = key_or_color if str(key_or_color).startswith("#") else avatar_color(str(key_or_color))
    return readable_on(c, bg or [PANEL, BUBBLE_OTHER])


def badge_colors(kind="neutral"):
    """(background, text) of a count badge: 'neutral' (accent), 'alert' (danger) or 'muted'."""
    return {"alert": (BADGE_ALERT_BG, BADGE_ALERT_TEXT),
            "muted": (BADGE_MUTED_BG, BADGE_MUTED_TEXT)}.get(kind, (BADGE_BG, BADGE_TEXT))


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
    global BUBBLE_ME, STYLESHEET, FESTIVAL, HAIR, META, FLOAT_BORDER, CONTROL_EDGE
    global BADGE_BG, BADGE_TEXT, BADGE_ALERT_BG, BADGE_ALERT_TEXT, BADGE_MUTED_BG, BADGE_MUTED_TEXT
    if festivals and festival_today():
        theme = festival_today()
    if theme == "system":
        theme = "light" if windows_uses_light() else "midnight"
    theme = theme if theme in _PALETTES else "midnight"
    FESTIVAL = FESTIVALS.get(theme)
    if accent not in ACCENTS:
        accent = "lime" if theme == "classic" else DEFAULT_ACCENT
    THEME, ACCENT_NAME = theme, accent
    pal = dict(_PALETTES[theme])
    if accent == "quillo" and not FESTIVAL:
        pal.update(QUILLO_NAVY if theme == "midnight" else QUILLO_LIGHT if theme == "light" else {})
    globals().update(pal)
    acc = FESTIVAL["accent"] if FESTIVAL else ACCENTS[accent]
    if not pal["DARK"] and luminance(acc) > 0.55:
        acc = mix(acc, "#000000", 0.62)           # bright accents are unreadable on white
    if not pal["DARK"]:
        acc = readable_on(acc, pal["BG"])         # links and times in the accent colour: 4.5:1 on the page
    ACCENT = acc
    ACCENT_TEXT = "#10131a" if luminance(acc) > 0.55 else "#ffffff"
    ACCENT_HOVER = mix(acc, "#ffffff", 0.85) if pal["DARK"] else mix(acc, "#000000", 0.88)
    # selected rows: on the light theme 0.13 was the same shade as a hovered row
    ACCENT_SOFT = mix(acc, pal["PANEL"], 0.20)
    ACCENT_FOCUS = mix(acc, pal["SURFACE"], 0.55)
    BUBBLE_ME = mix(acc, pal["BG"], 0.34 if pal["DARK"] else 0.22)     # my own messages: clearly mine
    HAIR = mix(pal["BORDER"], pal["PANEL"], 0.55)
    META = readable_on(pal["FAINT"], [pal["PANEL"], pal["BG"], pal["SURFACE"]])
    FLOAT_BORDER = readable_on(pal["BORDER"], pal["PANEL"], 1.5)
    CONTROL_EDGE = readable_on(pal["SCROLL"], pal["SURFACE"], 2.2 if pal["DARK"] else 3.0)
    STATUS_COLORS["offline"] = OFFLINE_DARK if pal["DARK"] else "#5a6070"
    BADGE_BG, BADGE_TEXT = acc, ACCENT_TEXT
    BADGE_ALERT_BG, BADGE_ALERT_TEXT = pal["DANGER"], "#ffffff"
    BADGE_MUTED_BG, BADGE_MUTED_TEXT = pal["SURFACE_HOVER"], pal["MUTED"]
    STYLESHEET = _stylesheet()
    round_popups()
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


def _icon_file(name, svg):
    """Write a small SVG used by the stylesheet (QSS can only load images from files)."""
    import tempfile
    folder = os.path.join(tempfile.gettempdir(), "LANMessenger", "style")
    path = os.path.join(folder, name)
    try:
        os.makedirs(folder, exist_ok=True)
        if not os.path.exists(path):
            with open(path, "w", encoding="utf-8") as f:
                f.write(svg)
    except OSError:
        return ""
    return path.replace("\\", "/")


def tick_image(double, color):
    """Message receipt ticks as a small image (the text glyph looks like a square root at small sizes).

    Drawn at twice the size it is shown (17 x 11) so it stays crisp."""
    stroke = f'fill="none" stroke="{color}" stroke-width="3.4" stroke-linecap="round" stroke-linejoin="round"'
    first = f'<path d="M3 12.5l5.5 5.5L20 4" {stroke}/>'
    second = f'<path d="M15.5 16.5l1.5 1.5L31 4" {stroke}/>'
    return _icon_file(f"tick{'2' if double else '1'}_v2_{color.strip('#')}.svg",
                      f'<svg xmlns="http://www.w3.org/2000/svg" width="34" height="22" viewBox="0 0 34 22">'
                      f'{first}{second if double else ""}</svg>')


def check_image():
    return _check_image()


def _check_image():
    color = ACCENT_TEXT
    return _icon_file(f"check_{color.strip('#')}.svg",
                      '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16"><path d="M3.5 8.3l3 3 6-6.3" '
                      f'fill="none" stroke="{color}" stroke-width="2.2" stroke-linecap="round" '
                      'stroke-linejoin="round"/></svg>')


def _arrow_image():
    color = MUTED
    return _icon_file(f"arrow_{color.strip('#')}.svg",
                      '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16"><path d="M4 6l4 4 4-4" fill="none" '
                      f'stroke="{color}" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>')


# ------------------------------------------------------------------ round pop-ups
# A menu is its own window, and Windows fills the window's corners, so the style sheet's border-radius showed
# square corners on the light theme. A see-through window (without the square system shadow) lets the rounded
# background be the edge. Done once per app by a thin style wrapper, so every menu - ours, Qt's own right-click
# menus and QMenu subclasses (emoji, reactions) - is covered without touching each one.
_POPUP_STYLE = {"style": None}


def round_popup(widget):
    """Make one custom pop-up window (a QFrame/QListWidget with a rounded style sheet) show round corners.

    Call it before the first show(). The widget must paint its own rounded background (style sheet
    background + border-radius; on a QListWidget keep 5 px or more padding so the list stays inside the curve)."""
    from PySide6.QtCore import Qt
    widget.setAttribute(Qt.WA_TranslucentBackground, True)
    flags = widget.windowFlags() | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint
    if flags != widget.windowFlags() and not widget.testAttribute(Qt.WA_WState_Created):
        widget.setWindowFlags(flags)
    return widget


def round_popups(app=None):
    """Install the round-menu style wrapper on the running QApplication (apply() does this when an app exists;
    call it yourself when the style sheet is set without apply(), as the server console does). Safe to repeat."""
    try:
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QApplication, QMenu, QProxyStyle, QWidget
    except ImportError:                          # colours only (no Qt): nothing to round
        return
    app = app or QApplication.instance()
    if app is None or not isinstance(app, QApplication) or _POPUP_STYLE["style"] is not None:
        return

    class PopupStyle(QProxyStyle):
        def polish(self, arg):
            if isinstance(arg, QMenu) and arg.windowType() == Qt.Popup:
                round_popup(arg)
            elif isinstance(arg, QWidget) and arg.property("roundPopup") and arg.isWindow():
                round_popup(arg)
            return super().polish(arg)

    try:
        style = PopupStyle(app.style().name())   # a fresh copy of the current style, wrapped
        app.setStyle(style)
    except Exception:  # noqa: BLE001 - purely cosmetic; square corners are better than no app
        return
    _POPUP_STYLE["style"] = style


def _stylesheet():
    check, arrow = _check_image(), _arrow_image()
    return f"""
* {{ font-family: "Segoe UI"; font-size: 10pt; color: {TEXT}; }}
QMainWindow, QDialog, QWidget#root {{ background: {BG}; }}
QToolTip {{ background: {TOOLTIP}; color: #eef0f5; border: none; padding: 7px 11px; border-radius: 9px; }}
QLabel {{ background: transparent; }}
QLabel[muted="true"] {{ color: {MUTED}; }}
QLabel[heading="true"] {{ font-size: 15pt; font-weight: 700; }}

QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox, QComboBox, QDateTimeEdit {{
    background: {SURFACE}; border: 1px solid {HAIR}; border-radius: 12px;
    padding: 7px 12px; selection-background-color: {ACCENT}; selection-color: {ACCENT_TEXT};
}}
QLineEdit:hover, QPlainTextEdit:hover, QTextEdit:hover, QSpinBox:hover, QComboBox:hover, QDateTimeEdit:hover {{
    border: 1px solid {SURFACE_HOVER}; }}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus, QSpinBox:focus, QComboBox:focus, QDateTimeEdit:focus {{
    border: 1px solid {ACCENT}; background: {INPUT_FOCUS_BG}; }}
QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled, QDateTimeEdit:disabled {{ color: {FAINT}; }}
QComboBox::drop-down, QDateTimeEdit::drop-down {{ border: none; width: 26px; }}
QComboBox::down-arrow, QDateTimeEdit::down-arrow {{ image: url("{arrow}"); width: 14px; height: 14px; }}
QDateTimeEdit::up-button, QDateTimeEdit::down-button {{ width: 0; border: none; }}
QTimeEdit::up-arrow, QTimeEdit::down-arrow, QTimeEdit::drop-down {{ image: none; width: 0; height: 0; }}
QTimeEdit {{ padding-right: 12px; min-width: 64px; }}
QCalendarWidget QWidget {{ background: {PANEL}; }}
QCalendarWidget QToolButton {{ background: transparent; border: none; padding: 4px 8px; font-weight: 700; }}
QCalendarWidget QAbstractItemView {{ selection-background-color: {ACCENT}; selection-color: {ACCENT_TEXT}; }}
QComboBox QAbstractItemView {{ background: {PANEL}; border: 1px solid {FLOAT_BORDER}; padding: 6px;
    selection-background-color: {ACCENT_SOFT}; selection-color: {TEXT}; outline: none; }}
QSpinBox::up-button, QSpinBox::down-button {{ width: 0; border: none; }}

QPushButton {{
    background: {SURFACE}; border: 1px solid {HAIR}; border-radius: 12px; padding: 8px 16px;
    font-weight: 600;
}}
QPushButton:hover {{ background: {SURFACE_HOVER}; }}
QPushButton:pressed {{ background: {HAIR}; }}
QPushButton:disabled {{ color: {FAINT}; }}
QPushButton:checked {{ background: {ACCENT_SOFT}; border: 1px solid {ACCENT_FOCUS}; }}
QPushButton[primary="true"] {{ background: {ACCENT}; color: {ACCENT_TEXT}; border: none; }}
QPushButton[primary="true"]:hover {{ background: {ACCENT_HOVER}; }}
QPushButton[primary="true"]:disabled {{ background: {SURFACE}; color: {FAINT}; border: 1px solid {HAIR}; }}
QPushButton[danger="true"] {{ color: {DANGER}; }}
QPushButton[danger="true"]:disabled {{ color: {FAINT}; }}
QPushButton[flat="true"] {{ background: transparent; border: none; padding: 6px; }}
QPushButton[flat="true"]:hover {{ background: {SURFACE_HOVER}; }}
QPushButton[chip="true"] {{ background: transparent; border: 1px solid {HAIR}; border-radius: 11px;
    padding: 3px 12px; font-weight: 600; font-size: 9pt; color: {MUTED}; min-height: 16px; }}
QPushButton[chip="true"]:hover {{ background: {SURFACE}; color: {TEXT}; }}
QPushButton[chip="true"]:checked {{ background: {ACCENT_SOFT}; border: 1px solid {ACCENT_FOCUS}; color: {TEXT}; }}
QPushButton[chip="true"][tall="true"] {{ border-radius: 12px; padding: 7px 14px; min-height: 19px; }}

QCheckBox, QRadioButton {{ spacing: 8px; }}
QCheckBox::indicator {{ width: 16px; height: 16px; border-radius: 6px; background: {SURFACE};
    border: 1px solid {CONTROL_EDGE}; }}
QCheckBox::indicator:hover {{ border: 1px solid {ACCENT}; }}
QCheckBox::indicator:checked {{ background: {ACCENT}; border: 1px solid {ACCENT}; image: url("{check}"); }}
QCheckBox:disabled {{ color: {FAINT}; }}
QListView::indicator, QTreeView::indicator, QTableView::indicator {{ width: 16px; height: 16px; border-radius: 6px;
    background: {SURFACE}; border: 1px solid {CONTROL_EDGE}; }}
QListView::indicator:checked, QTreeView::indicator:checked, QTableView::indicator:checked {{
    background: {ACCENT}; border: 1px solid {ACCENT}; image: url("{check}"); }}

QScrollArea {{ border: none; background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 0; }}
QScrollBar::handle:vertical {{ background: {SCROLL}; border-radius: 3px; min-height: 30px; margin: 2px 3px; }}
QScrollBar::handle:vertical:hover {{ background: {SCROLL_HOVER}; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 0; }}
QScrollBar::handle:horizontal {{ background: {SCROLL}; border-radius: 3px; min-width: 30px; margin: 3px 2px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: none; }}
QAbstractScrollArea::corner {{ background: transparent; border: none; }}

QTableWidget, QTableView, QListWidget, QTreeWidget {{
    background: {PANEL}; border: none; border-radius: 16px;
    gridline-color: {HAIR}; alternate-background-color: {BG}; outline: none;
    selection-background-color: {ACCENT_SOFT}; selection-color: {TEXT};
}}
QListWidget::item, QTreeWidget::item {{ padding: 6px; border-radius: 9px; }}
QListWidget::item:hover, QTreeWidget::item:hover {{ background: {SURFACE}; }}
QListWidget::item:selected, QTreeWidget::item:selected {{ background: {ACCENT_SOFT}; }}
QHeaderView::section {{ background: {PANEL}; border: none; border-bottom: 1px solid {HAIR};
    padding: 7px; font-weight: 600; color: {MUTED}; }}
QTableCornerButton::section {{ background: {PANEL}; border: none; }}

QMenu {{ background: {PANEL}; border: 1px solid {FLOAT_BORDER}; border-radius: 14px; padding: 6px; }}
QMenu::item {{ padding: 8px 26px 8px 12px; border-radius: 9px; }}
QMenu::item:selected {{ background: {SURFACE_HOVER}; }}
QMenu::item:disabled {{ color: {FAINT}; }}
QMenu::separator {{ height: 1px; background: {HAIR}; margin: 6px 10px; }}
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


def tidy_forms(root):
    """Form labels sit level with their one-line fields (Qt puts them at the top of a tall rounded box)."""
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QFormLayout, QLabel
    for form in root.findChildren(QFormLayout):
        for row in range(form.rowCount()):
            label = form.itemAt(row, QFormLayout.LabelRole)
            field = form.itemAt(row, QFormLayout.FieldRole)
            if not (label and field and isinstance(label.widget(), QLabel)):
                continue
            h = field.sizeHint().height()
            if 20 <= h <= 52:                    # a one-line field; long lists and text boxes keep the top
                label.widget().setMinimumHeight(h)
                label.widget().setAlignment(Qt.AlignLeft | Qt.AlignVCenter)


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
