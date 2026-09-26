"""Tinted SVG icons.

Icons come either from assets/icons/icon_<name>.svg (the PyBlackBox set) or
from the outline paths below. Every icon is recoloured on the fly, so one
SVG serves every state (normal / hover / active).
"""

import os
import sys
from functools import lru_cache

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

from common import theme

_OUTLINE = {
    "chat": '<path d="M21 12a8.5 8.5 0 0 1-12.3 7.6L3.5 21l1.4-5A8.5 8.5 0 1 1 21 12z"/>',
    "users": '<circle cx="9" cy="8" r="3.5"/><path d="M2.5 20c0-3.6 2.9-6 6.5-6s6.5 2.4 6.5 6"/>'
             '<path d="M16 4.6a3.5 3.5 0 0 1 0 6.8"/><path d="M18 14.3c2.1.7 3.5 2.6 3.5 5.7"/>',
    "megaphone": '<path d="M3 10v4a1 1 0 0 0 1 1h3l7 4.5v-15L7 9H4a1 1 0 0 0-1 1z"/>'
                 '<path d="M17.5 9a4 4 0 0 1 0 6"/><path d="M20 6.5a8 8 0 0 1 0 11"/>',
    "copy": '<rect x="9" y="9" width="11" height="11" rx="2.5"/><path d="M5 15V6.5A2.5 2.5 0 0 1 7.5 4H15"/>',
    "folder": '<path d="M3 6.5A2.5 2.5 0 0 1 5.5 4H9l2.2 2.5h7.3A2.5 2.5 0 0 1 21 9v8.5a2.5 2.5 0 0 1-2.5 2.5h-13A2.5 2.5 0 0 1 3 17.5z"/>',
    "download": '<path d="M12 4v11"/><path d="M7 10.5l5 5 5-5"/><path d="M5 20h14"/>',
    "upload": '<path d="M12 16V5"/><path d="M7 9.5l5-5 5 5"/><path d="M5 20h14"/>',
    "logout": '<path d="M15 4h3a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2h-3"/><path d="M10 16l4-4-4-4"/><path d="M14 12H4"/>',
    "file": '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5"/>',
    "plus": '<path d="M12 5v14M5 12h14"/>',
    "close": '<path d="M6 6l12 12M18 6L6 18"/>',
    "check": '<path d="M5 12.5l4.5 4.5L19 7.5"/>',
    "dashboard": '<rect x="3" y="3" width="7" height="9" rx="1.5"/><rect x="14" y="3" width="7" height="5" rx="1.5"/>'
                 '<rect x="14" y="12" width="7" height="9" rx="1.5"/><rect x="3" y="16" width="7" height="5" rx="1.5"/>',
    "list": '<path d="M9 6h12M9 12h12M9 18h12"/><path d="M4 6h.01M4 12h.01M4 18h.01"/>',
    "info": '<circle cx="12" cy="12" r="9"/><path d="M12 11v6"/><path d="M12 7.5v.01"/>',
    "edit": '<path d="M4 20h4L19 9a2.8 2.8 0 0 0-4-4L4 16z"/><path d="M13.5 6.5l4 4"/>',
    "trash": '<path d="M4 7h16"/><path d="M10 11v6M14 11v6"/><path d="M6 7l1 12a2 2 0 0 0 2 2h6a2 2 0 0 0 2-2l1-12"/>'
             '<path d="M9 7V4.5A1.5 1.5 0 0 1 10.5 3h3A1.5 1.5 0 0 1 15 4.5V7"/>',
    "key": '<circle cx="8" cy="15" r="4"/><path d="M11 12l9-9"/><path d="M16 7l3 3"/><path d="M18.5 4.5l2 2"/>',
    "eye": '<path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/>',
    "eye_off": '<path d="M9.9 5.2A10.5 10.5 0 0 1 12 5c6.4 0 10 7 10 7a17 17 0 0 1-2.6 3.4"/>'
               '<path d="M6.6 6.6C3.8 8.5 2 12 2 12s3.6 7 10 7a10 10 0 0 0 5.4-1.6"/>'
               '<path d="M9.9 9.9a3 3 0 0 0 4.2 4.2"/><path d="M3 3l18 18"/>',
    "bell": '<path d="M6 16V11a6 6 0 0 1 12 0v5l2 2H4z"/><path d="M10 21h4"/>',
    "open": '<path d="M14 4h6v6"/><path d="M20 4l-9 9"/><path d="M18 14v4a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4"/>',
    "server": '<rect x="3" y="4" width="18" height="7" rx="2"/><rect x="3" y="13" width="18" height="7" rx="2"/>'
              '<path d="M7 7.5h.01M7 16.5h.01"/>',
    "power": '<path d="M12 3v9"/><path d="M6.4 6.4a8 8 0 1 0 11.2 0"/>',
    "refresh": '<path d="M20 12a8 8 0 1 1-2.3-5.7"/><path d="M20 4v5h-5"/>',
    "hash": '<path d="M5 9h15M4 15h15M10 3L8 21M16 3l-2 18"/>',
    "user": '<circle cx="12" cy="8" r="4"/><path d="M4 21c0-4.4 3.6-7 8-7s8 2.6 8 7"/>',
    "org": '<rect x="9" y="3" width="6" height="5" rx="1"/><rect x="3" y="16" width="6" height="5" rx="1"/>'
           '<rect x="15" y="16" width="6" height="5" rx="1"/><path d="M12 8v4"/>'
           '<path d="M6 16v-2a2 2 0 0 1 2-2h8a2 2 0 0 1 2 2v2"/>',
    "badge": '<rect x="4" y="3" width="16" height="18" rx="2.5"/><circle cx="12" cy="10" r="3"/>'
             '<path d="M8 17c.8-1.8 2.2-2.7 4-2.7s3.2.9 4 2.7"/>',
    "chart": '<path d="M4 20V4"/><path d="M4 20h16"/><rect x="7" y="11" width="3" height="6" rx="0.8"/>'
             '<rect x="12" y="7" width="3" height="10" rx="0.8"/><rect x="17" y="13" width="3" height="4" rx="0.8"/>',
    "image": '<rect x="3" y="4" width="18" height="16" rx="2"/><circle cx="9" cy="10" r="2"/><path d="M21 16l-5-5-9 9"/>',
    "sticker": '<path d="M15.5 3H6a3 3 0 0 0-3 3v12a3 3 0 0 0 3 3h7l8-8V6.5"/><path d="M13 21v-5a3 3 0 0 1 3-3h5"/>'
               '<path d="M8 9.5h.01M13 9.5h.01"/><path d="M8.5 14c.8.7 1.8 1 3 .9"/><path d="M18 2v5M15.5 4.5h5"/>',
    "reply": '<path d="M9 14L4 9l5-5"/><path d="M4 9h10.5a5.5 5.5 0 0 1 0 11H11"/>',
    "forward": '<path d="M15 14l5-5-5-5"/><path d="M20 9H9.5a5.5 5.5 0 0 0 0 11H13"/>',
    "pin": '<path d="M12 17v5"/><path d="M9 3h6l-1 6 3 3v2H7v-2l3-3z"/>',
    "smile": '<circle cx="12" cy="12" r="9"/><path d="M8.5 14.5c.9 1.2 2.1 1.8 3.5 1.8s2.6-.6 3.5-1.8"/>'
             '<path d="M9 9.5h.01M15 9.5h.01"/>',
    "screen": '<rect x="2" y="4" width="20" height="13" rx="2"/><path d="M8 21h8M12 17v4"/>',
    "palette": '<path d="M12 3a9 9 0 1 0 0 18c1 0 1.6-.8 1.6-1.6 0-.5-.2-.9-.5-1.2-.3-.3-.5-.7-.5-1.2'
               ' 0-.9.7-1.6 1.6-1.6H16a5 5 0 0 0 5-5c0-4-4-7.4-9-7.4z"/>'
               '<path d="M7.5 11.5h.01M10 7.5h.01M14.5 7.5h.01M17 11h.01"/>',
    "clock": '<circle cx="12" cy="13" r="8"/><path d="M12 9v4l2.5 2.5"/><path d="M5 3.5L2.5 6M19 3.5L21.5 6"/>',
    "compact": '<rect x="7" y="2.5" width="10" height="19" rx="2.5"/><path d="M11 18.5h2"/>',
    "back": '<path d="M15 5l-7 7 7 7"/>',
    "zap": '<path d="M13 2L4 14h7l-1 8 9-12h-7z"/>',
    "at": '<circle cx="12" cy="12" r="4"/><path d="M16 8v5a3 3 0 0 0 6 0v-1a10 10 0 1 0-4 8"/>',
}


def assets_dir() -> str:
    if getattr(sys, "frozen", False):
        return os.path.join(getattr(sys, "_MEIPASS", os.path.dirname(sys.executable)), "assets")
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets")


def asset(*parts) -> str:
    return os.path.join(assets_dir(), *parts)


def logo_widget(size: int):
    """The Quillo logo (the Q mark on its white tile - a fixed brand mark that works on any background)."""
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtWidgets import QLabel
    w = QLabel()
    ratio = max(2.0, QGuiApplication.instance().devicePixelRatio() if QGuiApplication.instance() else 1.0)
    pm = QPixmap(asset("logo.png"))
    if not pm.isNull():
        pm = pm.scaled(round(size * ratio), round(size * ratio), Qt.KeepAspectRatio, Qt.SmoothTransformation)
        pm.setDevicePixelRatio(ratio)
        w.setPixmap(pm)
    w.setFixedSize(size, size)
    w.setStyleSheet("background: transparent;")
    return w


@lru_cache(maxsize=None)
def _svg_bytes(name: str) -> bytes:
    if name in _OUTLINE:
        return ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
                'stroke="#fff" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">'
                + _OUTLINE[name] + '</svg>').encode()
    try:
        with open(asset("icons", f"icon_{name}.svg"), "rb") as f:
            return f.read()
    except OSError:              # a missing icon file shows nothing instead of breaking the window
        return b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"/>'


def pixmap(name: str, color: str | None = None, size: int = 20, scale: float = 2.0) -> QPixmap:
    """Icon tinted with `color` (default: the theme's text colour; "" keeps the original colours)."""
    return _pixmap(name, theme.TEXT if color is None else color, size, scale)


@lru_cache(maxsize=512)
def _pixmap(name: str, color: str, size: int, scale: float) -> QPixmap:
    renderer = QSvgRenderer(QByteArray(_svg_bytes(name)))
    px = int(size * scale)
    pm = QPixmap(px, px)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    view = renderer.defaultSize()
    # keep the aspect ratio of non-square icons (the PyBlackBox ones)
    w, h = view.width() or 1, view.height() or 1
    k = min(px / w, px / h)
    renderer.render(p, QRectF((px - w * k) / 2, (px - h * k) / 2, w * k, h * k))
    if color:
        p.setCompositionMode(QPainter.CompositionMode_SourceIn)
        p.fillRect(pm.rect(), QColor(color))
    p.end()
    pm.setDevicePixelRatio(scale)
    return pm


def add_show_password(edit):
    """An eye button at the end of a password box: click to see what you typed, click again to hide it."""
    from PySide6.QtWidgets import QLineEdit
    action = edit.addAction(icon("eye", theme.MUTED, 16), QLineEdit.TrailingPosition)
    action.setToolTip("Show password")

    def toggle():
        hidden = edit.echoMode() == QLineEdit.Password
        edit.setEchoMode(QLineEdit.Normal if hidden else QLineEdit.Password)
        action.setIcon(icon("eye_off" if hidden else "eye", theme.MUTED, 16))
        action.setToolTip("Hide password" if hidden else "Show password")
    action.triggered.connect(toggle)
    return action


def icon(name: str, color: str | None = None, size: int = 20, active_color: str | None = None) -> QIcon:
    ic = QIcon()
    ic.addPixmap(pixmap(name, color, size), QIcon.Normal, QIcon.Off)
    if active_color:
        ic.addPixmap(pixmap(name, active_color, size), QIcon.Normal, QIcon.On)
        ic.addPixmap(pixmap(name, active_color, size), QIcon.Active, QIcon.On)
    return ic


def license_label(color=None, align=Qt.AlignRight, wrap=False):
    """The very small licence line shown at the bottom of every window; click it to read the licence."""
    from PySide6.QtCore import QUrl
    from PySide6.QtGui import QDesktopServices
    from PySide6.QtWidgets import QLabel
    from common import theme as T
    from common.version import LICENSE_LINE, license_path
    lbl = QLabel(LICENSE_LINE)
    lbl.setTextFormat(Qt.PlainText)
    lbl.setWordWrap(wrap)
    lbl.setAlignment(align | Qt.AlignVCenter)
    lbl.setCursor(Qt.PointingHandCursor)
    lbl.setToolTip("Free to download and use. Changed it? Tell the author and share it back - click to read the licence.")
    lbl.setStyleSheet(f"color: {color or T.FAINT}; font-size: 7pt; background: transparent; padding: 2px 10px 3px 10px;")
    lbl.mousePressEvent = lambda _e: QDesktopServices.openUrl(QUrl.fromLocalFile(license_path()))
    return lbl
