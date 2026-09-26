"""Small reusable widgets for the client (PyBlackBox look)."""

import datetime
import html
import os
import re

from PySide6.QtCore import QObject, QRect, QRectF, QSize, Qt, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QFont, QFontMetrics, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QLabel, QSizePolicy, QToolButton, QWidget

from common import theme as T
from common.icons import icon


# ------------------------------------------------------------------ helpers
def plain(label):
    """Show text exactly as typed (QLabel would otherwise render '<b>..' in names as HTML)."""
    label.setTextFormat(Qt.PlainText)
    return label


def esc(text) -> str:
    """Escape user text that is placed inside a rich-text label."""
    return html.escape(str(text or ""), quote=False)


def rich_safe(text) -> str:
    """User text for places where Qt guesses the format (tooltips, message boxes, input dialogs):
    a room called '<img src=...>' must show as those characters, not as an image."""
    return "<qt>" + html.escape(str(text or ""), quote=False).replace("\n", "<br>") + "</qt>"


def first_name(name, fallback="them") -> str:
    parts = str(name or "").split()
    return parts[0] if parts else fallback


def fmt_list_time(ts):
    if not ts:
        return ""
    d = datetime.datetime.fromtimestamp(ts)
    today = datetime.date.today()
    if d.date() == today:
        return d.strftime("%H:%M")
    if (today - d.date()).days == 1:
        return "Yesterday"
    if (today - d.date()).days < 7:
        return d.strftime("%a")
    return d.strftime("%d/%m/%y")


def fmt_day(ts):
    d = datetime.datetime.fromtimestamp(ts).date()
    today = datetime.date.today()
    if d == today:
        return "Today"
    if (today - d).days == 1:
        return "Yesterday"
    return d.strftime("%A, %d %B %Y")


def fmt_last_seen(ts):
    if not ts:
        return "Offline"
    d = datetime.datetime.fromtimestamp(ts)
    if d.date() == datetime.date.today():
        return f"Last seen today at {d:%H:%M}"
    return f"Last seen {fmt_list_time(ts).lower() if fmt_list_time(ts) == 'Yesterday' else fmt_list_time(ts)}"


# web links, and studio paths: \\server\share\..., //server/share/... (Nuke), X:\... or X:/...;
# a path with spaces works when it is in "double quotes"
_PATH = r'(?:\\\\[\w.$-]+|//[\w.$-]+|(?<![\w/])[A-Za-z]:)[\\/][^\s<>"|?*]*'
_LINK_RE = re.compile(r'"(?P<quoted>(?:\\\\|//|[A-Za-z]:[\\/])[^"<>|?*\n]+)"'
                      r'|(?P<url>https?://[^\s<>"]+|www\.[^\s<>"]+)'
                      r'|(?<![\w:/\\])(?P<path>' + _PATH + ')')
PATH_SCHEME = "studio-path:"


_LONG_WORD = re.compile(r"\S{30,}")


def breakable(text):
    """Allow very long words / paths to wrap (QLabel never breaks inside a word)."""
    def split(m):
        w = m.group(0)
        return "​".join(w[i:i + 20] for i in range(0, len(w), 20))
    return _LONG_WORD.sub(split, text)


def linkify(text, mark=None):
    """Escape text and turn URLs, UNC paths (\\\\server\\share) and drive paths into links.

    mark: optional function applied to the escaped plain text between links (e.g. @mention highlights)."""
    mark = mark or (lambda escaped: escaped)
    out = []
    pos = 0
    for m in _LINK_RE.finditer(text):
        out.append(mark(html.escape(breakable(text[pos:m.start()]))))
        if m.group("quoted"):
            target, rest, shown = m.group("quoted"), "", m.group(0)
        else:
            target = m.group(0).rstrip(".,;:)!'")
            rest, shown = m.group(0)[len(target):], target
        if m.group("url"):
            href = "http://" + target if target.startswith("www.") else target
        else:                                # a studio path: opened through the path menu, never executed
            href = PATH_SCHEME + QUrl.toPercentEncoding(target).data().decode()
            shown = "📁 " + shown
        out.append(f'<a href="{html.escape(href, quote=True)}" style="color:{T.ACCENT}; text-decoration:none">'
                   f'{html.escape(breakable(shown))}</a>{html.escape(rest)}')
        pos = m.end()
    out.append(mark(html.escape(breakable(text[pos:]))))
    return "".join(out).replace("\n", "<br>")


def open_link(url):
    if url.startswith(PATH_SCHEME):
        path_menu(QUrl.fromPercentEncoding(url[len(PATH_SCHEME):].encode()))
        return
    QDesktopServices.openUrl(QUrl(url))


# ------------------------------------------------------------------ studio paths
_SEQUENCE = re.compile(r"(#+|@+|%0?\d*d|\$F\d?)", re.I)     # frame placeholders: ####, %04d, @@@, $F4
_RUNNABLE = {".exe", ".bat", ".cmd", ".com", ".msi", ".ps1", ".vbs", ".js", ".jse", ".wsf", ".scr", ".lnk", ".reg"}


def windows_path(path):
    """'//srv/proj/FAL_030/comp' or 'Z:/proj/x' -> the Windows form Explorer understands."""
    p = path.strip().replace("/", "\\")
    return p.rstrip("\\") if len(p) > 3 else p


def path_target(path):
    """(what to open, is it a frame sequence) - a sequence path opens the folder holding the frames."""
    p = windows_path(path)
    head, name = os.path.split(p)
    if _SEQUENCE.search(name):
        return head, True
    return p, False


def path_menu(path, parent=None):
    """Clicking a path in a chat: open it in Explorer, open the file, or copy the path."""
    from PySide6.QtGui import QCursor, QGuiApplication
    from PySide6.QtWidgets import QMenu
    from common.icons import icon
    target, sequence = path_target(path)
    ext = os.path.splitext(target)[1].lower()
    looks_like_file = bool(ext) and not sequence
    m = QMenu(parent)
    m.addAction(icon("folder", T.TEXT, 16), "Show frames in Explorer" if sequence else
                ("Show in Explorer" if looks_like_file else "Open folder"), lambda: _reveal(target, looks_like_file))
    if looks_like_file and ext not in _RUNNABLE:
        m.addAction(icon("open", T.TEXT, 16), "Open file", lambda: _check_then(target, open_path))
    m.addSeparator()
    m.addAction(icon("copy", T.TEXT, 16), "Copy path", lambda: QGuiApplication.clipboard().setText(windows_path(path)))
    m.exec(QCursor.pos())


def _reveal(target, is_file):
    def go(p):
        if os.path.isdir(p):
            open_path(p)
        else:
            show_in_folder(p)
    _check_then(target, go, allow_parent=True)


def _check_then(target, action, allow_parent=False):
    """Network paths can take seconds to answer: look them up off the UI thread, then act (or say why not)."""
    import threading
    from PySide6.QtCore import QTimer

    def look():
        found = target if os.path.exists(target) else None
        if not found and allow_parent and os.path.isdir(os.path.dirname(target)):
            found = os.path.dirname(target)       # the shot folder exists, the version not yet: open the folder
        QTimer.singleShot(0, _receiver, lambda: action(found) if found else _unreachable(target))
    threading.Thread(target=look, daemon=True).start()


def _unreachable(target):
    from PySide6.QtGui import QCursor
    from PySide6.QtWidgets import QToolTip
    QToolTip.showText(QCursor.pos(), rich_safe(f"Can't reach {target}\nfrom this PC (not there, or no access)."))


class _Receiver(QObject):
    """Lives on the UI thread, so worker threads can hand results back with QTimer.singleShot."""


_receiver = _Receiver()


def open_path(path):
    QDesktopServices.openUrl(QUrl.fromLocalFile(path))


def show_in_folder(path):
    if os.name == "nt" and os.path.exists(path):
        import subprocess
        subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
    else:
        open_path(os.path.dirname(path))


# ------------------------------------------------------------------ avatar
def paint_avatar(p: QPainter, rect: QRect, name: str, key: str, room=False, status=None,
                 ring=None, uid=None):
    """Round avatar: the person's photo if they have one, else coloured initials; plus a status dot."""
    ring = ring or T.PANEL
    p.save()
    p.setRenderHint(QPainter.Antialiasing)
    p.setRenderHint(QPainter.SmoothPixmapTransform)
    p.setPen(Qt.NoPen)
    from client import avatars
    photo = avatars.cache.pixmap(uid) if (uid is not None and avatars.cache and not room) else None
    if photo is not None:
        path = QPainterPath()
        path.addEllipse(QRectF(rect))
        p.save()
        p.setClipPath(path)
        p.drawPixmap(rect, photo)
        p.restore()
    else:
        p.setBrush(QColor(T.avatar_color(key)))
        if room:
            p.drawRoundedRect(QRectF(rect), rect.width() * 0.3, rect.width() * 0.3)
        else:
            p.drawEllipse(rect)
        f = QFont("Segoe UI")
        f.setPixelSize(max(9, int(rect.height() * 0.38)))
        f.setBold(True)
        p.setFont(f)
        p.setPen(QColor("#ffffff"))
        p.drawText(rect, Qt.AlignCenter, "#" if room and not name else T.initials(name))
    if status:
        d = max(10, int(rect.width() * 0.32))
        dot = QRect(rect.right() - d + 2, rect.bottom() - d + 2, d, d)
        p.setPen(QPen(QColor(ring), 3))
        p.setBrush(QColor(T.STATUS_COLORS.get(status, T.STATUS_COLORS["offline"])))
        p.drawEllipse(dot)
    p.restore()


class Avatar(QWidget):
    clicked = Signal()

    def __init__(self, size=40, parent=None):
        super().__init__(parent)
        self.setFixedSize(size, size)
        self.name, self.key, self.room, self.status, self.ring = "", "", False, None, T.PANEL
        self.uid = None

    def set(self, name, key, room=False, status=None, ring=None, uid=None):
        self.name, self.key, self.room, self.status, self.uid = name, key, room, status, uid
        if ring:
            self.ring = ring
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        paint_avatar(p, self.rect().adjusted(1, 1, -1, -1), self.name, self.key, self.room, self.status,
                     self.ring, self.uid)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.clicked.emit()


# ------------------------------------------------------------ icon button
class IconButton(QToolButton):
    def __init__(self, icon_name, tooltip="", size=36, icon_size=18, color=None, hover=None,
                 round_=True, parent=None):
        super().__init__(parent)
        color, hover = color or T.MUTED, hover or T.TEXT
        self.icon_name, self.color, self.hover_color, self.isz = icon_name, color, hover, icon_size
        self.setIcon(icon(icon_name, color, icon_size))
        self.setIconSize(QSize(icon_size, icon_size))
        self.setFixedSize(size, size)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip(tooltip)
        r = size // 2 if round_ else 10
        self.setStyleSheet(f"""
            QToolButton {{ background: transparent; border: none; border-radius: {r}px; }}
            QToolButton:hover {{ background: {T.SURFACE_HOVER}; }}
            QToolButton:pressed {{ background: {T.BORDER}; }}
            QToolButton::menu-indicator {{ image: none; }}""")

    def enterEvent(self, e):
        self.setIcon(icon(self.icon_name, self.hover_color, self.isz))
        super().enterEvent(e)

    def leaveEvent(self, e):
        self.setIcon(icon(self.icon_name, self.color, self.isz))
        super().leaveEvent(e)


class RailButton(QToolButton):
    """Navigation button: icon with a small label; the active one gets an accent pill and a side marker."""

    W, H = 72, 58

    def __init__(self, icon_name, tooltip, label="", parent=None):
        super().__init__(parent)
        self.icon_name = icon_name
        self.label = label
        self.badge = 0
        self.setCheckable(True)
        self.setFixedSize(self.W, self.H if label else 46)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip(tooltip)
        self._hover = False

    def set_badge(self, n):
        self.badge = n
        self.update()

    def enterEvent(self, e):
        self._hover = True
        self.update()

    def leaveEvent(self, e):
        self._hover = False
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        on = self.isChecked()
        pill = QRectF((self.W - 44) / 2, 4, 44, 32 if self.label else 38)
        if on:
            p.setBrush(QColor(T.ACCENT))
            p.drawRoundedRect(QRectF(0, pill.top() + 6, 3, pill.height() - 12), 1.5, 1.5)
            p.setBrush(QColor(T.ACCENT_SOFT))
            p.drawRoundedRect(pill, 10, 10)
        elif self._hover:
            p.setBrush(QColor(T.SURFACE_HOVER))
            p.drawRoundedRect(pill, 10, 10)
        color = T.ACCENT if on else (T.TEXT if self._hover else T.MUTED)
        pm = icon(self.icon_name, color, 20).pixmap(20, 20)
        p.drawPixmap(int(pill.center().x()) - 10, int(pill.center().y()) - 10, pm)
        if self.label:
            f = QFont("Segoe UI")
            f.setPixelSize(10)
            f.setBold(on)
            p.setFont(f)
            p.setPen(QColor(T.TEXT if on or self._hover else T.MUTED))
            p.drawText(QRect(0, int(pill.bottom()) + 2, self.W, 16), Qt.AlignHCenter | Qt.AlignTop, self.label)
        if self.badge:
            text = "99+" if self.badge > 99 else str(self.badge)
            f = QFont("Segoe UI")
            f.setPixelSize(10)
            f.setBold(True)
            p.setFont(f)
            w = max(18, QFontMetrics(f).horizontalAdvance(text) + 8)
            b = QRectF(pill.right() - w / 2 - 2, pill.top() - 3, w, 18)
            p.setBrush(QColor(T.DANGER))
            p.setPen(QPen(QColor(T.RAIL), 2))
            p.drawRoundedRect(b, 9, 9)
            p.setPen(QColor("#ffffff"))
            p.drawText(b, Qt.AlignCenter, text)


class MeButton(QToolButton):
    """My avatar with its status dot at the bottom of the rail (click: status menu)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(RailButton.W, 52)
        self.setCursor(Qt.PointingHandCursor)
        self.name, self.status, self.uid = "", "online", None

    def set_me(self, name, status, tooltip="", uid=None):
        self.name, self.status, self.uid = name, status, uid
        self.setToolTip(tooltip)
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        paint_avatar(p, QRect((self.width() - 40) // 2, 6, 40, 40), self.name, self.name,
                     status=self.status, ring=T.RAIL, uid=self.uid)


# -------------------------------------------------------------- list item
class ConvItem(QWidget):
    """A row in the sidebar lists (painted for speed with hundreds of people)."""
    clicked = Signal(str)
    context = Signal(str, object)

    HEIGHT = 64

    def __init__(self, conv, parent=None):
        super().__init__(parent)
        self.conv = conv
        self.setFixedHeight(self.HEIGHT)
        self.setCursor(Qt.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.title = ""
        self.subtitle = ""
        self.time = ""
        self.unread = 0
        self.status = None
        self.room = False
        self.active = False
        self.dim = False
        self.typing = False
        self.muted = False
        self._hover = False

    def set_data(self, title, subtitle="", time_text="", unread=0, status=None, room=False, dim=False,
                 muted=False):
        self.title, self.subtitle, self.time, self.unread = title, subtitle, time_text, unread
        self.status, self.room, self.dim, self.muted = status, room, dim, muted
        self.update()

    def set_active(self, active):
        if self.active != active:
            self.active = active
            self.update()

    def enterEvent(self, e):
        self._hover = True
        self.update()

    def leaveEvent(self, e):
        self._hover = False
        self.update()

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.clicked.emit(self.conv)

    def contextMenuEvent(self, e):
        self.context.emit(self.conv, e.globalPos())

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        w = self.width()
        bg = T.PANEL
        if self.active:
            bg = T.ACCENT_SOFT
        elif self._hover:
            bg = T.SURFACE
        if bg != T.PANEL:
            p.setBrush(QColor(bg))
            p.drawRoundedRect(QRectF(6, 2, w - 12, self.HEIGHT - 4), 12, 12)
        if self.dim:
            p.setOpacity(0.5)
        uid = int(self.conv[2:]) if self.conv.startswith("u:") else None
        paint_avatar(p, QRect(16, 10, 44, 44), self.title, self.conv if self.room else self.title,
                     self.room, self.status, bg, uid)
        p.setOpacity(1)
        bold = bool(self.unread) and not self.muted

        right = w - 18
        f = QFont("Segoe UI")
        f.setPixelSize(11)
        f.setBold(bold)
        p.setFont(f)
        time_w = 0
        if self.time:
            time_w = QFontMetrics(f).horizontalAdvance(self.time) + 6
            p.setPen(QColor(T.ACCENT if bold else T.FAINT))
            p.drawText(QRect(right - time_w, 12, time_w, 18), Qt.AlignRight | Qt.AlignVCenter, self.time)
        if self.muted:
            time_w += 18
            p.drawPixmap(right - time_w + 2, 14, icon("bell", T.FAINT, 13).pixmap(13, 13))

        badge_w = 0
        if self.unread:
            text = "99+" if self.unread > 99 else str(self.unread)
            bf = QFont("Segoe UI")
            bf.setPixelSize(11)
            bf.setBold(True)
            badge_w = max(20, QFontMetrics(bf).horizontalAdvance(text) + 12)
            b = QRectF(right - badge_w, 34, badge_w, 20)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(T.SURFACE_HOVER if self.muted else T.ACCENT))
            p.drawRoundedRect(b, 10, 10)
            p.setFont(bf)
            p.setPen(QColor(T.MUTED if self.muted else T.ACCENT_TEXT))
            p.drawText(b, Qt.AlignCenter, text)

        x = 72
        if self.dim:
            p.setOpacity(0.6)
        nf = QFont("Segoe UI")
        nf.setPixelSize(13)
        nf.setWeight(QFont.Bold if bold else QFont.DemiBold)
        p.setFont(nf)
        p.setPen(QColor(T.TEXT))
        avail = right - x - time_w - 4
        p.drawText(QRect(x, 11, avail, 20), Qt.AlignLeft | Qt.AlignVCenter,
                   QFontMetrics(nf).elidedText(self.title, Qt.ElideRight, avail))
        sf = QFont("Segoe UI")
        sf.setPixelSize(12)
        sf.setItalic(self.typing)
        p.setFont(sf)
        p.setPen(QColor(T.ACCENT if self.typing else (T.TEXT if bold else T.MUTED)))
        avail = right - x - badge_w - 8
        sub = "typing..." if self.typing else self.subtitle.replace("\n", " ")
        p.drawText(QRect(x, 33, avail, 20), Qt.AlignLeft | Qt.AlignVCenter,
                   QFontMetrics(sf).elidedText(sub, Qt.ElideRight, avail))


class SectionLabel(QLabel):
    """Group header in the sidebar lists (sub=True: a section inside a department)."""

    def __init__(self, text, parent=None, sub=False):
        if sub:
            super().__init__(text, parent)
            self.setTextFormat(Qt.PlainText)
            self.setStyleSheet(f"color: {T.FAINT}; font-size: 8.5pt; font-weight: 600;"
                               " padding: 8px 18px 2px 26px;")
        else:
            super().__init__(text.upper(), parent)
            self.setTextFormat(Qt.PlainText)
            self.setStyleSheet(f"color: {T.MUTED}; font-size: 7.5pt; font-weight: 700;"
                               " padding: 14px 18px 4px 18px; letter-spacing: 1px;")
