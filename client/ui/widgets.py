"""Small reusable widgets for the client (PyBlackBox look)."""

import datetime
import html
import os
import re

from PySide6.QtCore import QEvent, QObject, QPoint, QRect, QRectF, QSize, Qt, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QFont, QFontMetrics, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QLabel, QSizePolicy, QToolButton, QWidget

from common import theme as T
from common.fmt import (  # noqa: F401 - the shared text/date helpers, re-exported for the client UI
    DASH, ELLIPSIS, SEP, clip, day_word, fmt_date, fmt_range, fmt_time, fmt_time_range, fmt_when, menu_text,
)
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
    """The time column of a list: '16:05' today, 'Yesterday', 'Tue' this week, then '29 Sep' ('29 Sep 2025')."""
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
    return fmt_date(d, weekday=False)


def fmt_day(ts):
    """A day heading in a chat: 'Today', 'Yesterday', 'Tuesday 29 September' ('... 2025' in another year)."""
    return day_word(ts) if day_word(ts) in ("Today", "Yesterday") else fmt_date(ts, long=True)


def fmt_last_seen(ts):
    if not ts:
        return "Offline"
    d = datetime.datetime.fromtimestamp(ts)
    if d.date() == datetime.date.today():
        return f"Last seen today at {d:%H:%M}"
    return f"Last seen {fmt_list_time(ts).lower() if fmt_list_time(ts) == 'Yesterday' else fmt_list_time(ts)}"


def ui_font(size=T.FONT_M, bold=False, weight=None, italic=False, zoom=1.0):
    """The app font for painted widgets at a scale size in pt (T.FONT_S ...), set in pixels like the rest of the
    painted UI: ui_font(T.FONT_S, bold=True)."""
    f = QFont("Segoe UI")
    f.setPixelSize(T.px(size, zoom))
    if weight is not None:
        f.setWeight(weight)
    elif bold:
        f.setBold(True)
    f.setItalic(italic)
    return f


def popup_pos(anchor, size, above=False, align_right=True, gap=6):
    """Where (global top-left) to open a pop-up or menu of `size` from the button `anchor`: below it (above=True:
    above it), right edges lined up (align_right=False: left edges), flipped and nudged to stay on the anchor's
    screen. For a menu: menu.exec(popup_pos(button, menu.sizeHint(), above=True))."""
    top_left = anchor.mapToGlobal(QPoint(0, 0))
    r = QRect(top_left, anchor.size())
    w, h = size.width(), size.height()
    x = r.right() - w + 1 if align_right else r.left()
    y = r.top() - h - gap if above else r.bottom() + 1 + gap
    screen = anchor.screen()
    if screen is not None:
        area = screen.availableGeometry()
        if above and y < area.top():
            y = r.bottom() + 1 + gap              # no room above: open below instead
        elif not above and y + h > area.bottom() + 1:
            y = r.top() - h - gap                 # no room below: open above instead
        x = max(area.left(), min(x, area.right() + 1 - w))
        y = max(area.top(), min(y, area.bottom() + 1 - h))
    return QPoint(x, y)


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
    return _mark_shots("".join(out)).replace("\n", "<br>")


def studio_paths(text):
    """The studio paths in a message (\\\\server\\share\\..., X:\\...), in order, without repeats."""
    found = []
    for m in _LINK_RE.finditer(text or ""):
        if m.group("url"):
            continue
        path = m.group("quoted") or m.group(0).rstrip(".,;:)!'")
        if path not in found:
            found.append(path)
    return found


# ------------------------------------------------------------------ shot names
# FAL_030 in a message links to everything said about that shot. The pattern is the server's (Settings in the
# server console), the click is handled by the main window (search).
SHOT_SCHEME = "quillo-shot:"
_SHOTS = {"re": None, "open": None, "status": None}
_TAG = re.compile(r"(<[^>]*>)")


def set_shot_pattern(pattern):
    from common import protocol as P
    _SHOTS["re"] = P.shot_regex(pattern)


def set_shot_handler(fn):
    _SHOTS["open"] = fn


def set_shot_statuses(fn):
    """fn(shot) -> status key or None: a shot name in a message shows its status (FAL_030 ✅)."""
    _SHOTS["status"] = fn


def _shot_html(name):
    from common import protocol as P
    status = _SHOTS["status"](name) if _SHOTS["status"] else None
    badge = ""
    if status in P.SHOT_STATUS:
        emoji, label = P.SHOT_STATUS[status]
        badge = f'&nbsp;<span title="{html.escape(label)}">{emoji}</span>'
    return (f'<a href="{SHOT_SCHEME}{html.escape(name, quote=True)}" '
            f'style="color:{T.ACCENT}; text-decoration:none; font-weight:600">{name}</a>{badge}')


def shot_names(text):
    rx = _SHOTS["re"]
    return list(dict.fromkeys(m.group(0) for m in rx.finditer(text or ""))) if rx else []


def _mark_shots(markup):
    """Shot names in message HTML become links - never inside a tag or inside another link."""
    rx = _SHOTS["re"]
    if not rx:
        return markup
    parts = _TAG.split(markup)
    in_link = False
    for i, part in enumerate(parts):
        if i % 2:                            # a tag
            low = part.lower()
            in_link = True if low.startswith("<a ") else False if low.startswith("</a") else in_link
        elif part and not in_link:
            parts[i] = rx.sub(lambda m: _shot_html(m.group(0)), part)
    return "".join(parts)


def open_link(url):
    if url.startswith(PATH_SCHEME):
        path_menu(QUrl.fromPercentEncoding(url[len(PATH_SCHEME):].encode()))
        return
    if url.startswith(SHOT_SCHEME):
        if _SHOTS["open"]:
            _SHOTS["open"](html.unescape(url[len(SHOT_SCHEME):]))
        return
    QDesktopServices.openUrl(QUrl(url))


def open_studio_path(path, parent=None):
    """'Open folder' on a path card: asks first for an untrusted computer, then opens the folder (or the folder
    holding the file / the frames)."""
    host = unc_host(path)
    if not link_host_trusted(host) and not _confirm_host(host, parent):
        return
    target, sequence = path_target(path)
    _reveal(target, bool(os.path.splitext(target)[1]) and not sequence)


# ------------------------------------------------------------------ studio paths
_SEQUENCE = re.compile(r"(#+|@+|%0?\d*d|\$F\d?)", re.I)     # frame placeholders: ####, %04d, @@@, $F4
# Programs, scripts, shortcuts and disk images: never started from a chat (shown in their folder instead).
_RUNNABLE = {".exe", ".bat", ".cmd", ".com", ".msi", ".msp", ".mst", ".ps1", ".psm1", ".psc1", ".ps1xml", ".vbs",
             ".vbe", ".vb", ".js", ".jse", ".wsf", ".wsh", ".wsc", ".sct", ".scr", ".lnk", ".url", ".reg", ".hta",
             ".cpl", ".msc", ".jar", ".pif", ".appref-ms", ".application", ".scf", ".library-ms", ".search-ms",
             ".settingcontent-ms", ".iso", ".img", ".vhd", ".vhdx", ".inf", ".ins", ".isp", ".chm", ".hlp",
             ".gadget", ".xll", ".dll", ".sys", ".cab", ".xbap", ".shb", ".diagcab"}

# The studio's own file servers, from the server's settings: links to them open without asking.
_TRUSTED = {"hosts": set(), "config": None}


def set_link_policy(hosts, config=None):
    """hosts: the server's list of studio file servers; config: where "don't ask again" is remembered."""
    _TRUSTED["hosts"] = {h.strip().lower().lstrip("\\/") for h in hosts if h and h.strip()}
    _TRUSTED["config"] = config


def unc_host(path):
    """'fileserver' for a \\\\fileserver\\share path, '' for anything else (a drive letter, this PC)."""
    p = windows_path(path)
    if not p.startswith("\\\\"):
        return ""
    return p[2:].split("\\")[0].lower()


def link_host_trusted(host):
    if not host:
        return True
    import socket
    config = _TRUSTED["config"]
    allowed = set(_TRUSTED["hosts"]) | {h.lower() for h in (config["trusted_link_hosts"] if config else [])}
    allowed |= {"localhost", "127.0.0.1", socket.gethostname().lower()}
    return host in allowed or host.split(".")[0] in allowed


def _confirm_host(host, parent=None):
    """A link to a computer that is not a studio file server: opening it lets that computer see your Windows
    sign-in, so ask first (and offer not to ask again for it)."""
    from PySide6.QtWidgets import QCheckBox, QMessageBox
    box = QMessageBox(QMessageBox.Warning, "Open a link to another computer?",
                      f"This link points to \\\\{host}, which is not one of your studio's file servers.\n\n"
                      "Opening it lets that computer see your Windows sign-in. Open it only if you trust "
                      "whoever sent it.", QMessageBox.Open | QMessageBox.Cancel, parent)
    box.setDefaultButton(QMessageBox.Cancel)
    again = QCheckBox(f"Don't ask again for {host}")
    box.setCheckBox(again)
    if box.exec() != QMessageBox.Open:
        return False
    config = _TRUSTED["config"]
    if again.isChecked() and config is not None:
        config["trusted_link_hosts"] = sorted(set(config["trusted_link_hosts"]) | {host})
        config.save()
    return True


def open_file(path):
    """Open a received or linked file with its program - but never start a program or script from a chat."""
    if os.path.splitext(path)[1].lower() in _RUNNABLE:
        from PySide6.QtGui import QCursor
        from PySide6.QtWidgets import QToolTip
        QToolTip.showText(QCursor.pos(), "Programs and scripts are not started from Quillo - "
                                         "it is shown in its folder instead.")
        show_in_folder(path)
        return
    open_path(path)


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
    host = unc_host(path)
    if not link_host_trusted(host) and not _confirm_host(host, parent):
        return                       # nothing is looked up on an untrusted computer until you say so
    target, sequence = path_target(path)
    ext = os.path.splitext(target)[1].lower()
    looks_like_file = bool(ext) and not sequence
    m = QMenu(parent)
    m.addAction(icon("folder", T.TEXT, 16), "Show frames in Explorer" if sequence else
                ("Show in Explorer" if looks_like_file else "Open folder"), lambda: _reveal(target, looks_like_file))
    if looks_like_file and ext not in _RUNNABLE:
        m.addAction(icon("open", T.TEXT, 16), "Open file", lambda: _check_then(target, open_file))
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
    photo = None
    if avatars.cache and room and str(key).startswith("r:"):          # key = the room's conv "r:<id>"
        photo = avatars.cache.room_pixmap(int(str(key)[2:]))
    elif avatars.cache and uid is not None and not room:
        photo = avatars.cache.pixmap(uid)
    if photo is not None:
        path = QPainterPath()
        if room:
            path.addRoundedRect(QRectF(rect), rect.width() * 0.3, rect.width() * 0.3)
            p.fillPath(path, QColor(T.SURFACE_HOVER))       # a tile behind see-through pictures (stickers, logos)
        else:
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
    """A round icon-only button. hover_bg / press_bg: the hover and pressed backgrounds, for buttons on a
    fixed-colour surface (the dark image viewer) where the theme's own hover colour would vanish."""

    def __init__(self, icon_name, tooltip="", size=36, icon_size=18, color=None, hover=None,
                 round_=True, parent=None, hover_bg=None, press_bg=None):
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
            QToolButton:hover {{ background: {hover_bg or T.SURFACE_HOVER}; }}
            QToolButton:pressed {{ background: {press_bg or T.BORDER}; }}
            QToolButton::menu-indicator {{ image: none; }}""")

    def enterEvent(self, e):
        self.setIcon(icon(self.icon_name, self.hover_color, self.isz))
        super().enterEvent(e)

    def leaveEvent(self, e):
        self.setIcon(icon(self.icon_name, self.color, self.isz))
        super().leaveEvent(e)


class RailButton(QToolButton):
    """Navigation button: icon with a small label; the active one gets an accent pill and a side marker.

    A button without a label is a toggle (keep on top, compact view): when on it shows an accent icon on a soft
    pill but no side marker, so only the page you are on looks selected."""

    W, H = 72, 58
    H_COMPACT = 46
    changed = Signal()              # its badge or checked state changed (the compact tab bar mirrors both)

    def __init__(self, icon_name, tooltip, label="", parent=None):
        super().__init__(parent)
        self.toggled.connect(lambda _on: self.changed.emit())
        self.icon_name = icon_name
        self.label = label
        self.badge = 0
        self.badge_kind = "alert"
        self.compact = False
        self.setCheckable(True)
        self.setFixedSize(self.W, self.H if label else self.H_COMPACT)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip(tooltip)
        self._hover = False

    def set_badge(self, n, kind="alert"):
        """A count on the button: kind 'alert' (red: unread for you, failed) or 'neutral' (accent: work in
        progress, like active transfers) - see T.badge_colors."""
        if (self.badge, self.badge_kind) != (n, kind):
            self.badge, self.badge_kind = n, kind
            self.update()
            self.changed.emit()

    def set_compact(self, on):
        """Short windows: hide the label (the tooltip still names the page) so the rail needs less height."""
        if self.compact != bool(on):
            self.compact = bool(on)
            self.setFixedSize(self.W, self.H if self.label and not self.compact else self.H_COMPACT)
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
        show_label = bool(self.label) and not self.compact
        pill = QRectF((self.W - 44) / 2, 4, 44, 32 if show_label else 38)
        if on and self.label:                     # the page you are on
            p.setBrush(QColor(T.ACCENT))
            p.drawRoundedRect(QRectF(0, pill.top() + 6, 3, pill.height() - 12), 1.5, 1.5)
            p.setBrush(QColor(T.ACCENT_SOFT))
            p.drawRoundedRect(pill, 10, 10)
        elif on:                                  # a toggle that is on
            p.setBrush(QColor(T.SURFACE_HOVER))
            p.drawRoundedRect(pill, 10, 10)
        elif self._hover:
            p.setBrush(QColor(T.SURFACE_HOVER))
            p.drawRoundedRect(pill, 10, 10)
        color = T.ACCENT if on else (T.TEXT if self._hover else T.MUTED)
        pm = icon(self.icon_name, color, 20).pixmap(20, 20)
        p.drawPixmap(int(pill.center().x()) - 10, int(pill.center().y()) - 10, pm)
        if show_label:
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
            bg, fg = T.badge_colors(self.badge_kind)
            p.setBrush(QColor(bg))
            p.setPen(QPen(QColor(T.RAIL), 2))
            p.drawRoundedRect(b, 9, 9)
            p.setPen(QColor(fg))
            p.drawText(b, Qt.AlignCenter, text)


class CompactTabs(QWidget):
    """Compact view's bottom tab bar (like a phone app): a few pages as icons with a short name under each.
    The page you are on gets the accent colour on a soft pill; counts show as small badges.

    items: [(key, icon name, label)]. current() returns the key that looks selected; badge(key) returns
    (count, kind) - both are asked at paint time, so update() is all it needs after a change."""
    clicked = Signal(str)
    H = 58

    def __init__(self, items, current, badge, parent=None):
        super().__init__(parent)
        self.items, self._current, self._badge = items, current, badge
        self._hover = -1
        self.setFixedHeight(self.H)
        self.setMouseTracking(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setAttribute(Qt.WA_StyledBackground, False)

    def _cell(self, i):
        w = self.width() / max(1, len(self.items))
        return QRectF(i * w, 0, w, self.height())

    def tab_rect(self, key):
        """Where a tab is (a pop-up menu opens above it)."""
        for i, item in enumerate(self.items):
            if item[0] == key:
                return self._cell(i).toRect()
        return self.rect()

    def _at(self, pos):
        n = len(self.items)
        return min(n - 1, max(0, int(pos.x() * n / max(1, self.width())))) if n else -1

    def mouseMoveEvent(self, e):
        i = self._at(e.position())
        if i != self._hover:
            self._hover = i
            self.setToolTip(self.items[i][2] if i >= 0 else "")
            self.update()

    def leaveEvent(self, e):
        self._hover = -1
        self.update()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton and self.rect().contains(e.position().toPoint()):
            i = self._at(e.position())
            if i >= 0:
                self.clicked.emit(self.items[i][0])

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), QColor(T.RAIL))
        p.setPen(QPen(QColor(T.HAIR), 1))
        p.drawLine(0, 0, self.width(), 0)
        p.setPen(Qt.NoPen)
        current = self._current()
        f = QFont("Segoe UI")
        f.setPixelSize(10)
        for i, (key, ic, label) in enumerate(self.items):
            cell = self._cell(i)
            on = key == current
            pill = QRectF(cell.center().x() - 26, 7, 52, 28)
            if on or i == self._hover:
                p.setBrush(QColor(T.ACCENT_SOFT if on else T.SURFACE_HOVER))
                p.drawRoundedRect(pill, 14, 14)
            color = T.ACCENT if on else (T.TEXT if i == self._hover else T.MUTED)
            pm = icon(ic, color, 20).pixmap(20, 20)
            p.drawPixmap(int(pill.center().x()) - 10, int(pill.center().y()) - 10, pm)
            f.setBold(on)
            p.setFont(f)
            p.setPen(QColor(T.TEXT if on or i == self._hover else T.MUTED))
            p.drawText(QRectF(cell.left(), pill.bottom() + 2, cell.width(), 16), Qt.AlignHCenter | Qt.AlignTop,
                       label)
            p.setPen(Qt.NoPen)
            n, kind = self._badge(key)
            if n:
                text = "99+" if n > 99 else str(n)
                bf = QFont("Segoe UI")
                bf.setPixelSize(10)
                bf.setBold(True)
                p.setFont(bf)
                bw = max(18, QFontMetrics(bf).horizontalAdvance(text) + 8)
                b = QRectF(pill.center().x() + 4, pill.top() - 4, bw, 18)
                bg, fg = T.badge_colors(kind)
                p.setBrush(QColor(bg))
                p.setPen(QPen(QColor(T.RAIL), 2))
                p.drawRoundedRect(b, 9, 9)
                p.setPen(QColor(fg))
                p.drawText(b, Qt.AlignCenter, text)
                p.setPen(Qt.NoPen)


class MeButton(QToolButton):
    """My avatar with its status dot at the bottom of the rail (click: status menu)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(RailButton.W, 52)
        self.setCursor(Qt.PointingHandCursor)
        self.name, self.status, self.uid = "", "online", None
        self._hover = False

    def enterEvent(self, e):
        self._hover = True
        self.update()

    def leaveEvent(self, e):
        self._hover = False
        self.update()

    def set_me(self, name, status, tooltip="", uid=None):
        self.name, self.status, self.uid = name, status, uid
        self.setToolTip(tooltip)
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        if self._hover:                           # a soft halo, like the rail buttons' hover pill
            p.setRenderHint(QPainter.Antialiasing)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(T.SURFACE_HOVER))
            p.drawEllipse(QRect((self.width() - 48) // 2, 2, 48, 48))
        paint_avatar(p, QRect((self.width() - 40) // 2, 6, 40, 40), self.name, self.name,
                     status=self.status, ring=T.SURFACE_HOVER if self._hover else T.RAIL, uid=self.uid)


# -------------------------------------------------------------- list item
class ConvItem(QWidget):
    """A row in the sidebar lists (painted for speed with hundreds of people)."""
    clicked = Signal(str)
    context = Signal(str, object)

    HEIGHT = 64
    INSET = 16          # avatar's left edge; text starts at INSET + 56 (set 4 on Home cards to line up)

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
        self.pinned = False
        self.draft = False                      # the subtitle is my unsent text: "Draft: ..."
        self._hover = False

    def set_data(self, title, subtitle="", time_text="", unread=0, status=None, room=False, dim=False,
                 muted=False, pinned=False):
        self.title, self.subtitle, self.time, self.unread = title, subtitle, time_text, unread
        self.status, self.room, self.dim, self.muted, self.pinned = status, room, dim, muted, pinned
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
        edge = max(0, self.INSET - 10)
        if bg != T.PANEL:
            p.setBrush(QColor(bg))
            p.drawRoundedRect(QRectF(edge, 2, w - 2 * edge, self.HEIGHT - 4), 12, 12)
        if self.dim:
            p.setOpacity(0.5)
        uid = int(self.conv[2:]) if self.conv.startswith("u:") else None
        paint_avatar(p, QRect(self.INSET, 10, 44, 44), self.title, self.conv if self.room else self.title,
                     self.room, self.status, bg, uid)
        p.setOpacity(1)
        bold = bool(self.unread) and not self.muted

        right = w - edge - 12
        f = QFont("Segoe UI")
        f.setPixelSize(11)
        f.setBold(bold)
        p.setFont(f)
        time_w = 0
        if self.time:
            time_w = QFontMetrics(f).horizontalAdvance(self.time) + 6
            p.setPen(QColor(T.ACCENT if bold else T.META))
            p.drawText(QRect(right - time_w, 12, time_w, 18), Qt.AlignRight | Qt.AlignVCenter, self.time)
        if self.muted:
            time_w += 18
            p.drawPixmap(right - time_w + 2, 14, icon("bell_off", T.META, 13).pixmap(13, 13))
        if self.pinned:
            time_w += 18
            p.drawPixmap(right - time_w + 2, 14, icon("pin", T.META, 13).pixmap(13, 13))

        badge_w = 0
        if self.unread:
            text = "99+" if self.unread > 99 else str(self.unread)
            bf = QFont("Segoe UI")
            bf.setPixelSize(11)
            bf.setBold(True)
            badge_w = max(20, QFontMetrics(bf).horizontalAdvance(text) + 12)
            b = QRectF(right - badge_w, 34, badge_w, 20)
            p.setPen(Qt.NoPen)
            bg_badge, fg_badge = T.badge_colors("muted" if self.muted else "neutral")
            p.setBrush(QColor(bg_badge))
            p.drawRoundedRect(b, 10, 10)
            p.setFont(bf)
            p.setPen(QColor(fg_badge))
            p.drawText(b, Qt.AlignCenter, text)

        x = self.INSET + 56
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
        sub = "typing…" if self.typing else self.subtitle.replace("\n", " ")
        if self.draft and not self.typing:
            df = QFont(sf)
            df.setBold(True)
            p.setFont(df)
            p.setPen(QColor(T.DANGER))
            p.drawText(QRect(x, 33, avail, 20), Qt.AlignLeft | Qt.AlignVCenter, "Draft:")
            dw = QFontMetrics(df).horizontalAdvance("Draft: ")
            x, avail = x + dw, avail - dw
            p.setFont(sf)
            p.setPen(QColor(T.MUTED))
        p.drawText(QRect(x, 33, avail, 20), Qt.AlignLeft | Qt.AlignVCenter,
                   QFontMetrics(sf).elidedText(sub, Qt.ElideRight, avail))


class EmptyState(QWidget):
    """What an empty list or page shows: a soft round icon, a line saying why it's empty, and (often) the one
    button that fills it."""

    def __init__(self, icon_name="chat", title="", text="", button=None, action=None, parent=None, compact=False):
        from PySide6.QtWidgets import QPushButton, QVBoxLayout
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 18 if compact else 40, 24, 18 if compact else 40)
        lay.setSpacing(8)
        size = 44 if compact else 64
        self.icon = QLabel()
        self.icon.setFixedSize(size, size)
        self.icon.setAlignment(Qt.AlignCenter)
        self.icon.setStyleSheet(f"background: {T.ACCENT_SOFT}; border-radius: {size // 2}px;")
        lay.addWidget(self.icon, 0, Qt.AlignHCenter)
        lay.addSpacing(4)
        self.title = plain(QLabel())
        self.title.setAlignment(Qt.AlignCenter)
        self.title.setWordWrap(True)
        self.title.setStyleSheet(f"color: {T.TEXT}; font-size: {'10pt' if compact else '11.5pt'}; font-weight: 700;"
                                 " background: transparent;")
        lay.addWidget(self.title)
        self.text = plain(QLabel())
        self.text.setAlignment(Qt.AlignCenter)
        self.text.setWordWrap(True)
        self.text.setStyleSheet(f"color: {T.MUTED}; font-size: 9pt; background: transparent;")
        lay.addWidget(self.text)
        self.button = QPushButton()
        self.button.setCursor(Qt.PointingHandCursor)
        T.polish(self.button, primary=True)
        self.button.clicked.connect(lambda: self._action and self._action())
        lay.addSpacing(6)
        lay.addWidget(self.button, 0, Qt.AlignHCenter)
        self._action = None
        self.set(icon_name, title, text, button, action)

    def set(self, icon_name="chat", title="", text="", button=None, action=None):
        size = self.icon.width()
        self.icon.setPixmap(icon(icon_name, T.ACCENT, size // 2).pixmap(size // 2, size // 2))
        self.title.setText(title)
        self.title.setVisible(bool(title))
        self.text.setText(text)
        self.text.setVisible(bool(text))
        self._action = action
        self.button.setText(button or "")
        self.button.setVisible(bool(button and action))


class SectionLabel(QLabel):
    """Group header in the sidebar lists (sub=True: a section inside a department)."""

    def __init__(self, text, parent=None, sub=False):
        if sub:
            super().__init__(text, parent)
            self.setTextFormat(Qt.PlainText)
            self.setStyleSheet(f"color: {T.META}; font-size: 8.5pt; font-weight: 600;"
                               " padding: 8px 16px 2px 24px;")
        else:
            super().__init__(text.upper(), parent)
            self.setTextFormat(Qt.PlainText)
            self.setStyleSheet(f"color: {T.MUTED}; font-size: 7.5pt; font-weight: 700;"
                               " padding: 14px 16px 4px 16px; letter-spacing: 1px;")


class ElidedLabel(QLabel):
    """One line of plain text that ends in '…' when it doesn't fit; the whole text is then its tooltip.

    Use it for titles, subtitles, file names and paths that sit next to other things in a row. text() returns
    the whole text. It asks for room for the whole text but can shrink to a few letters, so give the column it
    sits in the stretch (layout.addWidget(label, 1)). mode: Qt.ElideRight (default), Qt.ElideMiddle for paths
    and file names (the end says which file), Qt.ElideLeft."""

    def __init__(self, text="", parent=None, mode=Qt.ElideRight):
        super().__init__(parent)
        self.setTextFormat(Qt.PlainText)
        self.setWordWrap(False)
        self._full, self._mode, self._tip = "", mode, ""
        self.setText(text)

    # ---- the whole text
    def setText(self, text):
        self._full = " ".join(str(text or "").splitlines())
        self._refresh()
        self.updateGeometry()

    def text(self):
        return self._full

    def shown_text(self):
        """What is painted right now (the whole text, or its cut version)."""
        return super().text()

    def is_elided(self):
        return super().text() != self._full

    def set_elide_mode(self, mode):
        self._mode = mode
        self._refresh()

    def setToolTip(self, tip):
        """A tooltip of your own wins; without one the whole text shows when it is cut."""
        self._tip = tip or ""
        self._refresh_tip()

    # ---- sizes: ask for the whole text, accept a few letters
    def sizeHint(self):
        s = super().sizeHint()
        fm = self.fontMetrics()
        extra = fm.horizontalAdvance(self._full) - fm.horizontalAdvance(super().text())
        return QSize(s.width() + max(0, extra), s.height())

    def minimumSizeHint(self):
        s = super().minimumSizeHint()
        fm = self.fontMetrics()
        chrome = max(0, s.width() - fm.horizontalAdvance(super().text()))   # margins, padding, indent
        return QSize(chrome + fm.horizontalAdvance("M" + ELLIPSIS), s.height())

    # ---- keep the cut in step with the width and the font
    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._refresh()

    def changeEvent(self, e):
        super().changeEvent(e)
        if e.type() in (QEvent.FontChange, QEvent.StyleChange):
            self._refresh()
            self.updateGeometry()

    def _refresh(self):
        avail = self.contentsRect().width() - 2 * self.margin() - max(0, self.indent())
        shown = self.fontMetrics().elidedText(self._full, self._mode, max(0, avail))
        if shown != super().text():
            super().setText(shown)
        self._refresh_tip()

    def _refresh_tip(self):
        tip = self._tip or (rich_safe(self._full) if self.is_elided() else "")
        if tip != self.toolTip():
            super().setToolTip(tip)
