"""The conversation page: header, message list and composer."""

import datetime
import os
import re
import tempfile
import time

from PySide6.QtCore import QMimeData, QPoint, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor, QCursor, QFont, QFontMetrics, QGuiApplication, QPainter, QPen, QTextCursor, QTextDocument, QTextOption,
)
from PySide6.QtWidgets import (
    QFileDialog, QFrame, QGridLayout, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMenu,
    QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QScrollArea, QSizePolicy, QToolButton, QVBoxLayout,
    QWidget, QWidgetAction,
)

from common import protocol as P
from common import theme as T
from common.icons import icon, pixmap
from client.ui.widgets import (
    Avatar, IconButton, esc, first_name, fmt_day, fmt_last_seen, linkify, open_link, open_path, plain, rich_safe,
    show_in_folder,
)

GROUP_SECONDS = 300
RENDER_MAX = 150        # message widgets built when a chat opens; scrolling up shows more
EMOJIS = ("😀 😂 😊 😍 😎 🤔 😅 😭 😡 👍 👎 👌 🙏 👏 💪 🙌 🎉 🔥 ✅ ❌ ⚠️ ❓ 💡 ⭐ "
          "❤️ 💯 🚀 🎬 🎥 🖥️ 📁 📎 ☕ 🍕 🕐 👀 🤝 😴 🤯 🥳").split()


def _file_icon_name(name):
    ext = os.path.splitext(name)[1].lower()
    return "image" if ext in (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".exr", ".tif", ".tiff",
                              ".dpx", ".psd", ".webp") else "file"


# ============================================================ messages
def _hline():
    line = QFrame()
    line.setFixedHeight(1)
    line.setStyleSheet(f"background: {T.HAIR}; border: none;")
    return line


class DaySeparator(QWidget):
    def __init__(self, ts):
        super().__init__()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 16, 0, 8)
        lay.setSpacing(12)
        lbl = QLabel(fmt_day(ts))
        lbl.setStyleSheet(f"color: {T.MUTED}; font-size: 8pt; font-weight: 700;")
        lay.addWidget(_hline(), 1)
        lay.addWidget(lbl)
        lay.addWidget(_hline(), 1)


class SystemLine(QWidget):
    def __init__(self, msg):
        super().__init__()
        self.msg = msg
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 6, 0, 6)
        t = datetime.datetime.fromtimestamp(msg["ts"]).strftime("%H:%M")
        lbl = plain(QLabel(f"{msg['body']}  ·  {t}"))
        lbl.setStyleSheet(f"color: {T.MUTED}; font-size: 8pt; background: {T.TINT}; border-radius: 10px;"
                          " padding: 3px 12px;")
        lay.addStretch(1)
        lay.addWidget(lbl)
        lay.addStretch(1)

    def set_max_width(self, w):
        pass


class BuzzLine(QWidget):
    """'⚡ Bob buzzed you' in the middle of the chat."""

    def __init__(self, msg, text):
        super().__init__()
        self.msg = msg
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 8, 0, 8)
        t = datetime.datetime.fromtimestamp(msg["ts"]).strftime("%H:%M")
        pill = plain(QLabel(f"⚡  {text}  ·  {t}"))
        pill.setStyleSheet(f"color: {T.ACCENT}; font-weight: 700; font-size: 9pt; background: {T.ACCENT_SOFT};"
                           f" border: 1px solid {T.ACCENT_FOCUS}; border-radius: 12px; padding: 4px 14px;")
        lay.addStretch(1)
        lay.addWidget(pill)
        lay.addStretch(1)

    def set_max_width(self, w):
        pass


class FileCard(QFrame):
    """Attachment inside a bubble: name, size, and download/open actions."""

    def __init__(self, ctx, file_info, conv, sent_ts=None):
        super().__init__()
        self.ctx = ctx
        self.info = file_info
        self.conv = conv
        self.sent_ts = sent_ts
        self.setObjectName("filecard")
        self.setStyleSheet(f"#filecard {{ background: {T.TINT}; border-radius: 14px; }}")
        self.setMinimumWidth(270)
        lay = QGridLayout(self)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setHorizontalSpacing(12)
        lay.setVerticalSpacing(4)
        ic = QLabel()
        ic.setFixedSize(42, 42)
        ic.setAlignment(Qt.AlignCenter)
        ic.setStyleSheet(f"background: {T.ACCENT_SOFT}; border-radius: 11px;")
        ic.setPixmap(pixmap(_file_icon_name(file_info["name"]), T.ACCENT, 22))
        lay.addWidget(ic, 0, 0, 2, 1)
        self.name = plain(QLabel(file_info["name"]))
        self.name.setStyleSheet("font-weight: 600;")
        self.name.setWordWrap(True)
        self.name.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lay.addWidget(self.name, 0, 1)
        self.meta = plain(QLabel())
        self.meta.setStyleSheet(f"color: {T.MUTED}; font-size: 8pt;")
        lay.addWidget(self.meta, 1, 1)
        self.bar = QProgressBar()
        self.bar.setFixedHeight(6)
        self.bar.setRange(0, 1000)
        self.bar.hide()
        lay.addWidget(self.bar, 2, 0, 1, 3)
        btns = QHBoxLayout()
        btns.setSpacing(2)
        self.b_download = IconButton("download", "Download", 32, 18, T.ACCENT, T.ACCENT)
        self.b_download.clicked.connect(self.download)
        self.b_cancel = IconButton("close", "Cancel download", 32, 16)
        self.b_cancel.clicked.connect(self.cancel)
        self.b_open = IconButton("open", "Open", 32, 18, T.ACCENT, T.ACCENT)
        self.b_open.clicked.connect(self.open)
        self.b_folder = IconButton("folder", "Show in folder", 32, 18)
        self.b_folder.clicked.connect(self.folder)
        self.b_extract = IconButton("upload", "Extract the zip into a folder", 32, 18, T.ACCENT, T.ACCENT)
        self.b_extract.clicked.connect(self.extract)
        for b in (self.b_download, self.b_cancel, self.b_extract, self.b_open, self.b_folder):
            btns.addWidget(b)
        lay.addLayout(btns, 0, 2, 2, 1)
        lay.setColumnStretch(1, 1)
        ctx.transfers.changed.connect(self._on_transfer)
        self.refresh()

    def local_path(self):
        return self.ctx.config.downloaded_path(self.info["id"])

    def refresh(self):
        size = P.human_size(self.info["size"])
        t = self.ctx.transfers.active_download(self.info["id"])
        path = self.local_path()
        for b in (self.b_download, self.b_cancel, self.b_extract, self.b_open, self.b_folder):
            b.hide()
        self.bar.setVisible(bool(t))
        if t:
            pct = t.done / t.size if t.size else 1
            self.bar.setValue(int(pct * 1000))
            speed = f"  ·  {P.human_size(t.speed)}/s" if t.speed else ""
            self.meta.setText(f"{P.human_size(t.done)} of {size}{speed}")
            self.b_cancel.show()
        elif path:
            self.meta.setText(f"{size}  ·  Downloaded")
            self.b_open.show()
            self.b_folder.show()
            self.b_extract.setVisible(path.lower().endswith(".zip"))
        elif self.info.get("purged"):
            self.meta.setText(f"{size}  ·  No longer on the server")
        else:
            self.meta.setText(size + self._expiry())
            self.b_download.show()

    def _expiry(self):
        """'  ·  available until Sat 28 Sep' when the server deletes shared files after a while."""
        days = getattr(self.ctx.store, "file_retention_days", 0)
        if not days or not self.sent_ts:
            return ""
        until = self.sent_ts + days * 86400
        left = until - time.time()
        if left <= 0:
            return "  ·  about to be removed from the server"
        when = datetime.datetime.fromtimestamp(until)
        if left < 86400:
            return f"  ·  download before {when:%H:%M} today" if when.date() == datetime.date.today()                 else f"  ·  download before {when:%a %H:%M}"
        return f"  ·  available until {when:%a %d %b}"

    def _on_transfer(self, t):
        if t.kind == "download" and t.file_id == self.info["id"]:
            self.refresh()
            if t.state == "failed":
                self.meta.setText(f"Download failed: {t.error}")

    def download(self):
        self.ctx.download_file(self.info, self.conv)
        self.refresh()

    def cancel(self):
        t = self.ctx.transfers.active_download(self.info["id"])
        if t:
            t.cancel()

    def open(self):
        if self.local_path():
            open_path(self.local_path())

    def folder(self):
        if self.local_path():
            show_in_folder(self.local_path())

    def extract(self):
        if self.local_path():
            self.meta.setText("Extracting...")
            self.ctx.extract_zip(self.local_path())


IMAGE_EXT = (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp")
_PATH_RE = __import__("re").compile(r'\\\\[^\s<>"|?*]+(?:\\[^\s<>"|?*]+)*|[A-Za-z]:\\[^\s<>"|?*]+')
PREVIEW_MAX_BYTES = 15 * 1024 * 1024


_THUMBS = {}          # path -> rounded preview pixmap (small LRU: dicts keep insertion order)


def thumbnail(path):
    """Chat-sized preview of an image, decoded at that size (not full resolution) and cached."""
    from PySide6.QtGui import QImageReader, QPixmap
    pm = _THUMBS.pop(path, None)
    if pm is None:
        reader = QImageReader(path)
        reader.setAutoTransform(True)
        size = reader.size()
        if size.isValid() and (size.width() > 340 or size.height() > 260):
            reader.setScaledSize(size.scaled(340, 260, Qt.KeepAspectRatio))
        img = reader.read()
        if img.isNull():
            return None
        pm = rounded(QPixmap.fromImage(img), 10)
        while len(_THUMBS) >= 200:
            _THUMBS.pop(next(iter(_THUMBS)))
    _THUMBS[path] = pm
    return pm


def rounded(pm, radius):
    """Copy of a pixmap with rounded corners."""
    from PySide6.QtGui import QPainter, QPainterPath, QPixmap
    out = QPixmap(pm.size())
    out.fill(Qt.transparent)
    p = QPainter(out)
    p.setRenderHint(QPainter.Antialiasing)
    path = QPainterPath()
    path.addRoundedRect(0, 0, pm.width(), pm.height(), radius, radius)
    p.setClipPath(path)
    p.drawPixmap(0, 0, pm)
    p.end()
    return out


def is_previewable(file_info):
    return (os.path.splitext(file_info.get("name", ""))[1].lower() in IMAGE_EXT
            and 0 < file_info.get("size", 0) <= PREVIEW_MAX_BYTES and not file_info.get("purged"))


class ImagePreview(QLabel):
    """Thumbnail of an image attachment (downloaded quietly into a cache)."""

    def __init__(self, ctx, file_info):
        super().__init__()
        self.ctx = ctx
        self.info = file_info
        self.path = None
        self.setCursor(Qt.PointingHandCursor)
        self.setAlignment(Qt.AlignCenter)
        self.setMinimumSize(120, 80)
        self.setStyleSheet(f"background: {T.TINT}; border-radius: 12px; color: {T.FAINT};")
        self.setText("Loading preview...")
        ctx.previews.ready.connect(self._ready)
        ctx.previews.failed.connect(self._failed)
        path = ctx.previews.request(file_info)
        if path:
            self._show(path)

    def _ready(self, file_id, path):
        if file_id == self.info["id"]:
            self._show(path)

    def _failed(self, file_id):
        if file_id == self.info["id"] and not self.path:
            self.setText("Preview not available")

    def _show(self, path):
        pm = thumbnail(path)
        if pm is None:
            self.setText("No preview")
            return
        self.path = path
        self.setStyleSheet("background: transparent;")
        self.setPixmap(pm)
        self.setFixedSize(pm.size())
        self.setToolTip("Click to open")

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton and self.path:
            open_path(self.path)


_NUKE_RE = re.compile(r"^(set cut_paste_input|version \d+|Root \{|push \$|[A-Z][A-Za-z0-9]+ \{$)", re.M)
SNIPPET_LINES = 25          # longer messages are shown as a collapsed code card
SNIPPET_CHARS = 2500
PREVIEW_LINES = 12
NUKE_PREVIEW_LINES = 5


def is_nuke(text):
    return "cut_paste_input" in text or len(_NUKE_RE.findall(text)) >= 2


def is_snippet(text):
    return is_nuke(text) or text.count("\n") >= SNIPPET_LINES or len(text) > SNIPPET_CHARS


class SnippetCard(QFrame):
    """Long text / Nuke script: monospace preview, Copy, Show all, Save as file. Never lays out
    thousands of lines as rich text (that is what freezes other chat programs)."""

    def __init__(self, ctx, text):
        super().__init__()
        self.ctx, self.text = ctx, text
        self.nuke = is_nuke(text)
        self.preview = NUKE_PREVIEW_LINES if self.nuke else PREVIEW_LINES
        self.setObjectName("snippet")
        self.setStyleSheet(f"#snippet {{ background: {T.TINT}; border-radius: 14px; }}")
        self.setMinimumWidth(220)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 8, 8, 10)
        lay.setSpacing(6)
        head = QHBoxLayout()
        head.setSpacing(6)
        lines = text.count("\n") + 1
        kind = "NUKE SCRIPT" if self.nuke else "TEXT"
        tag = QLabel(f"{kind}  ·  {lines:,} line{'s' if lines != 1 else ''}  ·  {P.human_size(len(text.encode('utf-8')))}")
        tag.setStyleSheet(f"color: {T.ACCENT}; font-size: 8pt; font-weight: 800;")
        head.addWidget(tag, 1)
        for ic, tip, fn in (("list", "Copy (then paste into Nuke with Ctrl+V)" if self.nuke else "Copy", self.copy),
                            ("download", "Save as a file", self.save)):
            b = IconButton(ic, tip, 30, 16, round_=False)
            b.clicked.connect(fn)
            head.addWidget(b)
        lay.addLayout(head)
        self.view = QPlainTextEdit()
        self.view.setReadOnly(True)
        self.view.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.view.setStyleSheet(f"QPlainTextEdit {{ background: {T.BG}; border: 1px solid {T.HAIR};"
                                " border-radius: 10px; font-family: Consolas; font-size: 9pt; padding: 4px; }")
        lay.addWidget(self.view)
        self.more = QPushButton()
        T.polish(self.more, flat=True)
        self.more.setStyleSheet(f"color: {T.ACCENT}; font-size: 8.5pt; padding: 2px 4px; text-align: left;")
        self.more.setCursor(Qt.PointingHandCursor)
        self.more.clicked.connect(self.toggle)
        lay.addWidget(self.more, 0, Qt.AlignLeft)
        self.expanded = False
        self._show()

    def _show(self):
        all_lines = self.text.split("\n")
        shown = all_lines if self.expanded else all_lines[:self.preview]
        self.view.setPlainText("\n".join(shown))
        fm = QFontMetrics(self.view.font())
        rows = min(len(shown), 28 if self.expanded else self.preview)
        self.view.setFixedHeight(rows * fm.lineSpacing() + 16 + (14 if self.expanded else 0))
        self.view.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded if self.expanded else Qt.ScrollBarAlwaysOff)
        hidden = len(all_lines) - self.preview
        self.more.setVisible(hidden > 0)
        self.more.setText("Show less" if self.expanded else f"Show all ({hidden:,} more lines)")

    def toggle(self):
        self.expanded = not self.expanded
        self._show()

    def copy(self):
        QGuiApplication.clipboard().setText(self.text)
        self.ctx.toast("Copied — paste it into Nuke with Ctrl+V" if self.nuke else "Copied to the clipboard")

    def save(self):
        ext = ".nk" if self.nuke else ".txt"
        path, _ = QFileDialog.getSaveFileName(self, "Save as", os.path.join(
            self.ctx.config["download_dir"], f"snippet{ext}"), f"*{ext};;All files (*)")
        if path:
            with open(path, "w", encoding="utf-8", newline="\n") as f:
                f.write(self.text)
            self.ctx.toast(f"Saved {os.path.basename(path)}")


class ReplyQuote(QFrame):
    clicked = Signal(int)

    def __init__(self, ctx, reply):
        super().__init__()
        self.reply = reply
        self.setObjectName("quote")
        self.setCursor(Qt.PointingHandCursor)
        color = T.avatar_color(reply.get("sender_name", ""))
        self.setStyleSheet(f"#quote {{ background: {T.TINT}; border-left: 3px solid {color};"
                           f" border-radius: 6px; }}")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 4, 8, 4)
        lay.setSpacing(0)
        who = plain(QLabel(reply.get("sender_name", "")))
        who.setStyleSheet(f"color: {color}; font-weight: 700; font-size: 8.5pt; background: transparent;")
        text = plain(QLabel(reply.get("snippet", "")[:140].replace("\n", " ")))
        text.setStyleSheet(f"color: {T.MUTED}; font-size: 9pt; background: transparent;")
        lay.addWidget(who)
        lay.addWidget(text)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.clicked.emit(self.reply["id"])


QUICK_REACTIONS = ("👍", "❤️", "😂", "😮", "😢", "🙏", "🔥", "🎉")


class ReactionBar(QWidget):
    """Emoji reaction chips under a message: '👍 3'. Click one to add / remove yours."""

    def __init__(self, ctx, msg):
        super().__init__()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 2, 0, 0)
        lay.setSpacing(4)
        store = ctx.store
        for r in msg.get("reactions", []):
            b = QPushButton(f"{r['emoji']} {r['count']}")
            b.setCursor(Qt.PointingHandCursor)
            names = [("You" if u == store.my_id else store.user_name(u)) for u in r.get("users", [])]
            more = r["count"] - len(names)
            b.setToolTip(rich_safe(", ".join(names) + (f" and {more} more" if more > 0 else "")
                                   + f" reacted {r['emoji']}"))
            mine = r.get("mine")
            b.setStyleSheet(
                f"QPushButton {{ font-family: 'Segoe UI Emoji', 'Segoe UI'; font-size: 9pt; font-weight: 600;"
                f" padding: 1px 8px; border-radius: 11px; min-height: 20px;"
                f" background: {T.ACCENT_SOFT if mine else T.TINT};"
                f" border: 1px solid {T.ACCENT_FOCUS if mine else 'transparent'}; }}"
                f"QPushButton:hover {{ border: 1px solid {T.ACCENT}; }}")
            b.clicked.connect(lambda _=False, e=r["emoji"]: ctx.chat.toggle_reaction(msg, e))
            lay.addWidget(b)
        lay.addStretch(1)


class ReactionPicker(QMenu):
    """Quick reactions (plus the full emoji list)."""
    picked = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        w = QWidget()
        row = QHBoxLayout(w)
        row.setContentsMargins(6, 4, 6, 4)
        row.setSpacing(2)
        for e in QUICK_REACTIONS:
            b = QToolButton()
            b.setText(e)
            b.setFixedSize(38, 38)
            b.setCursor(Qt.PointingHandCursor)
            b.setStyleSheet("QToolButton { border: none; border-radius: 19px; font-family: 'Segoe UI Emoji';"
                            " font-size: 17pt; background: transparent; }"
                            f"QToolButton:hover {{ background: {T.SURFACE_HOVER}; }}")
            b.clicked.connect(lambda _=False, e=e: (self.picked.emit(e), self.close()))
            row.addWidget(b)
        more = IconButton("plus", "More emoji", 34, 16)
        more.clicked.connect(self._more)
        row.addWidget(more)
        act = QWidgetAction(self)
        act.setDefaultWidget(w)
        self.addAction(act)

    def _more(self):
        pos = self.pos()
        self.close()
        m = EmojiMenu(self.parent())
        m.picked.connect(self.picked.emit)
        m.exec(pos)


class PollOption(QWidget):
    """One answer of a poll: a bar filled by its share of the votes."""
    clicked = Signal(int)

    def __init__(self, index, option, total, mine, multi, enabled):
        super().__init__()
        self.index, self.option, self.total, self.mine, self.multi = index, option, total, mine, multi
        self.setFixedHeight(40)
        self.setMinimumWidth(200)
        self.setEnabled(enabled)
        if enabled:
            self.setCursor(Qt.PointingHandCursor)
        self._hover = False

    def enterEvent(self, e):
        self._hover = True
        self.update()

    def leaveEvent(self, e):
        self._hover = False
        self.update()

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton and self.isEnabled():
            self.clicked.emit(self.index)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setPen(QPen(QColor(T.ACCENT if self._hover else T.HAIR), 1))
        p.setBrush(QColor(T.BG))
        p.drawRoundedRect(r, 12, 12)
        share = self.option["count"] / self.total if self.total else 0
        if share:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(T.ACCENT_FOCUS if self.mine else T.ACCENT_SOFT))
            p.drawRoundedRect(QRectF(r.x(), r.y(), max(24.0, r.width() * share), r.height()), 12, 12)
        # choice marker: circle (single answer) or square (multiple answers)
        box = QRectF(12, (self.height() - 16) / 2, 16, 16)
        p.setPen(QPen(QColor(T.ACCENT if self.mine else T.MUTED), 1.6))
        p.setBrush(QColor(T.ACCENT) if self.mine else Qt.NoBrush)
        if self.multi:
            p.drawRoundedRect(box, 4, 4)
        else:
            p.drawEllipse(box)
        if self.mine:
            p.setPen(QPen(QColor(T.ACCENT_TEXT), 2))
            p.drawLine(box.left() + 4, box.center().y(), box.left() + 7, box.bottom() - 4)
            p.drawLine(box.left() + 7, box.bottom() - 4, box.right() - 3, box.top() + 4)
        f = QFont("Segoe UI")
        f.setPixelSize(13)
        f.setBold(self.mine)
        p.setFont(f)
        p.setPen(QColor(T.TEXT))
        pct = f"{round(share * 100)}%" if self.total else ""
        right_w = 90
        p.drawText(QRectF(38, 0, self.width() - 38 - right_w, self.height()), Qt.AlignVCenter | Qt.AlignLeft,
                   p.fontMetrics().elidedText(self.option["text"], Qt.ElideRight, self.width() - 38 - right_w))
        f.setBold(True)
        p.setFont(f)
        p.setPen(QColor(T.MUTED))
        count = self.option["count"]
        p.drawText(QRectF(self.width() - right_w, 0, right_w - 12, self.height()), Qt.AlignVCenter | Qt.AlignRight,
                   f"{pct}  ·  {count}" if self.total else "")


class PollCard(QWidget):
    """A poll inside a message bubble."""

    def __init__(self, ctx, msg):
        super().__init__()
        self.ctx, self.msg = ctx, msg
        poll = msg["poll"]
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 2, 0, 2)
        lay.setSpacing(6)
        head = QHBoxLayout()
        head.setSpacing(6)
        ic = QLabel()
        ic.setPixmap(pixmap("chart", T.ACCENT, 14))
        head.addWidget(ic)
        tag = QLabel("CLOSED POLL" if poll["closed"] else "POLL")
        tag.setStyleSheet(f"color: {T.ACCENT}; font-size: 8pt; font-weight: 800; letter-spacing: 1px;")
        head.addWidget(tag)
        head.addStretch(1)
        lay.addLayout(head)
        q = plain(QLabel(poll["question"]))
        q.setWordWrap(True)
        q.setStyleSheet("font-size: 11.5pt; font-weight: 700;")
        lay.addWidget(q)
        lay.addSpacing(2)
        mine = set(poll.get("mine", []))
        total_votes = sum(o["count"] for o in poll["options"])
        for i, o in enumerate(poll["options"]):
            w = PollOption(i, o, total_votes, i in mine, poll["multi"], not poll["closed"])
            w.clicked.connect(self.vote)
            if o.get("voters"):
                w.setToolTip(", ".join(("You" if u == ctx.store.my_id else ctx.store.user_name(u))
                                       for u in o["voters"][:25]))
            lay.addWidget(w)
        foot = QHBoxLayout()
        bits = [f"{poll['total']} {'person' if poll['total'] == 1 else 'people'} voted"]
        if poll["multi"]:
            bits.append("pick any")
        if poll["anonymous"]:
            bits.append("anonymous")
        info = QLabel("  ·  ".join(bits))
        info.setStyleSheet(f"color: {T.MUTED}; font-size: 8.5pt;")
        foot.addWidget(info, 1)
        if not poll["anonymous"] and poll["total"]:
            who = QPushButton("View votes")
            T.polish(who, flat=True)
            who.setStyleSheet(f"color: {T.ACCENT}; font-size: 8.5pt; padding: 2px 6px;")
            who.setCursor(Qt.PointingHandCursor)
            who.clicked.connect(self.show_votes)
            foot.addWidget(who)
        lay.addLayout(foot)

    def vote(self, index):
        poll = self.msg["poll"]
        mine = set(poll.get("mine", []))
        if poll["multi"]:
            mine ^= {index}
        else:
            mine = set() if mine == {index} else {index}
        self.ctx.conn.request("vote", lambda r: None if r.get("ok") else self.ctx.toast(r.get("error")),
                              poll_id=poll["id"], options=sorted(mine))

    def show_votes(self):
        poll = self.msg["poll"]
        store = self.ctx.store
        lines = []
        for o in poll["options"]:
            names = [("You" if u == store.my_id else store.user_name(u)) for u in o.get("voters", [])]
            lines.append(f"{o['text']}  —  {o['count']}\n    " + (", ".join(names) or "nobody"))
        QMessageBox.information(self, "Poll votes", rich_safe(poll["question"] + "\n\n" + "\n\n".join(lines)))


class MessageRow(QWidget):
    """One message: a bubble (text, quote, image, file) or a sticker, aligned left or right."""

    def __init__(self, ctx, msg, mine, show_name, show_avatar, is_room):
        super().__init__()
        self.ctx = ctx
        self.msg = msg
        self.mine = mine
        self.is_room = is_room
        self.first = show_name
        self.seen = ""
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 8 if show_name else 1, 0, 1)
        outer.setSpacing(10)

        deleted = msg.get("deleted")
        self.sticker = msg.get("kind") == "sticker" and not deleted
        self.mention = not mine and not deleted and ctx.store.mentions_me(msg)
        self.bubble = QFrame()
        self.bubble.setObjectName("bubble")
        self.bubble.setStyleSheet(self._bubble_style())
        self.bubble.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Preferred)
        b = QVBoxLayout(self.bubble)
        b.setContentsMargins(*((0, 0, 0, 0) if self.sticker else (12, 8, 12, 6)))
        b.setSpacing(4)

        sender = ctx.store.user_name(msg["sender_id"])
        if show_name and is_room and not mine:
            name = plain(QLabel(sender))
            name.setStyleSheet(f"color: {T.avatar_color(sender)}; font-weight: 700; font-size: 9pt;")
            b.addWidget(name)

        self.text = None
        self._ideal = None
        if deleted:
            gone = plain(QLabel("This message was deleted"))
            gone.setStyleSheet(f"color: {T.FAINT}; font-style: italic;")
            b.addWidget(gone)
        elif self.sticker:
            from client import stickers
            art = QLabel()
            pm = stickers.pixmap(msg["body"], 150)
            if pm:
                art.setPixmap(pm)
            else:
                art.setText("Sticker (update Quillo to see it)")
                art.setStyleSheet(f"color: {T.FAINT}; font-style: italic;")
            if msg.get("reply"):
                q = ReplyQuote(ctx, msg["reply"])
                q.clicked.connect(lambda mid: ctx.chat.scroll_to(mid))
                b.addWidget(q)
            b.addWidget(art, 0, Qt.AlignRight if mine else Qt.AlignLeft)
        else:
            if msg.get("forwarded"):
                fwd = plain(QLabel("↪ Forwarded"))
                fwd.setStyleSheet(f"color: {T.MUTED}; font-size: 8.5pt; font-style: italic;")
                b.addWidget(fwd)
            if msg.get("reply"):
                q = ReplyQuote(ctx, msg["reply"])
                q.clicked.connect(lambda mid: ctx.chat.scroll_to(mid))
                b.addWidget(q)
            f = msg.get("file")
            if f:
                if is_previewable(f):
                    b.addWidget(ImagePreview(ctx, f))
                b.addWidget(FileCard(ctx, f, msg["conv"], msg["ts"]))
            if msg.get("kind") == "poll" and msg.get("poll"):
                b.addWidget(PollCard(ctx, msg))
            elif msg.get("body") and is_snippet(msg["body"]):
                b.addWidget(SnippetCard(ctx, msg["body"]))
            elif msg.get("body"):
                self.text = QLabel(linkify(msg["body"], ctx.store.mention_marker()
                                           if msg["conv"].startswith("r:") and "@" in msg["body"] else None))
                self.text.setWordWrap(True)
                self.text.setTextFormat(Qt.RichText)
                self.text.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.LinksAccessibleByMouse)
                self.text.setOpenExternalLinks(False)
                self.text.linkActivated.connect(open_link)
                self.text.setStyleSheet("font-size: 10.5pt;")
                self.text.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
                self.text.setContextMenuPolicy(Qt.NoContextMenu)
                b.addWidget(self.text)

        if msg.get("reactions") and not deleted:
            b.addWidget(ReactionBar(ctx, msg))

        self.meta = QLabel()
        self.meta.setTextFormat(Qt.RichText)
        if self.sticker:
            self.meta.setStyleSheet(f"color: {T.MUTED}; font-size: 8pt; background: {T.TINT};"
                                    " border-radius: 8px; padding: 1px 8px;")
            b.addWidget(self.meta, 0, Qt.AlignRight if mine else Qt.AlignLeft)
        else:
            self.meta.setStyleSheet(f"color: {T.FAINT}; font-size: 8pt;")
            self.meta.setAlignment(Qt.AlignRight)
            b.addWidget(self.meta)
        self.update_meta()

        if mine:
            outer.addStretch(1)
            outer.addWidget(self.bubble)
        else:
            if is_room:
                av = Avatar(34)
                if show_avatar:
                    av.set(sender, sender, ring=T.BG, uid=msg["sender_id"])
                else:
                    av.setFixedSize(34, 0)
                outer.addWidget(av, 0, Qt.AlignTop)
            outer.addWidget(self.bubble)
            outer.addStretch(1)

    def _bubble_style(self, flash=False):
        if self.sticker:
            border = f"border: 2px solid {T.ACCENT}; border-radius: 12px;" if flash else ""
            return f"#bubble {{ background: transparent; {border} }}"
        bg = T.BUBBLE_ME if self.mine else T.BUBBLE_OTHER
        border = ""
        if self.mention:
            bg = T.mix(T.ACCENT, T.BUBBLE_OTHER, 0.16)
            border = f"border-left: 3px solid {T.ACCENT};"
        elif not T.DARK and not self.mine:
            border = f"border: 1px solid {T.HAIR};"
        if flash:
            border = f"border: 2px solid {T.ACCENT};"
        r = [18, 18, 18, 18]                     # tl, tr, br, bl
        if self.first:
            r[1 if self.mine else 0] = 6         # small "tail" corner on the first bubble of a group
        return (f"#bubble {{ background: {bg}; {border} border-top-left-radius: {r[0]}px;"
                f" border-top-right-radius: {r[1]}px; border-bottom-right-radius: {r[2]}px;"
                f" border-bottom-left-radius: {r[3]}px; }}")

    def update_meta(self):
        t = datetime.datetime.fromtimestamp(self.msg["ts"]).strftime("%H:%M")
        if self.msg.get("edited") and not self.msg.get("deleted"):
            t = f"edited&nbsp;·&nbsp;{t}"
        if self.seen:
            t = f"{esc(self.seen)}&nbsp;·&nbsp;{t}"
        if self.mine and not self.is_room and not self.msg.get("deleted"):
            read, delivered = self.msg.get("read"), self.msg.get("delivered")
            path = T.tick_image(bool(read or delivered), T.ACCENT if read else T.MUTED)
            tip = "Read" if read else "Delivered" if delivered else "Sent"
            tick = f"<img src='{path}' width='17' height='11'>" if path else ("✓✓" if read or delivered else "✓")
            self.meta.setText(f"{t}&nbsp;&nbsp;{tick}")
            self.meta.setToolTip(tip)
        else:
            self.meta.setText(t)

    def set_seen(self, text):
        self.seen = text
        self.update_meta()

    def flash(self):
        """Briefly highlight (after jumping to this message)."""
        self.bubble.setStyleSheet(self._bubble_style(flash=True))
        QTimer.singleShot(1500, self, lambda: self.bubble.setStyleSheet(self._bubble_style()))

    def set_max_width(self, w):
        self.bubble.setMaximumWidth(w)
        if self.text:
            # word-wrapped QLabels shrink to a narrow column; size them to their text instead
            if self._ideal is None:
                self.text.ensurePolished()
                doc = QTextDocument()
                doc.setDefaultFont(self.text.font())
                doc.setDocumentMargin(0)
                doc.setHtml(self.text.text())
                self._ideal = int(doc.idealWidth()) + 6
            self.text.setMaximumWidth(w - 24)
            self.text.setMinimumWidth(min(self._ideal, w - 24))

    def enterEvent(self, e):
        if not self.msg.get("deleted"):
            self.ctx.chat.hover_bar.attach(self)
        super().enterEvent(e)

    def leaveEvent(self, e):
        QTimer.singleShot(60, self.ctx.chat.hover_bar.maybe_hide)
        super().leaveEvent(e)

    def contextMenuEvent(self, e):
        m = self.build_menu()
        if m:
            m.exec(e.globalPos())

    def build_menu(self):
        msg, ctx = self.msg, self.ctx
        if msg.get("deleted"):
            return None
        m = QMenu(self)
        chat = ctx.chat
        m.addAction(icon("smile", T.TEXT, 16), "React...", lambda: chat.react_menu(msg, QCursor.pos()))
        m.addAction(icon("reply", T.TEXT, 16), "Reply", lambda: chat.start_reply(msg))
        from client.ui.planner_ui import when_menu
        m.addMenu(when_menu(m, "Remind me about this",
                            lambda ts: ctx.add_reminder(ts, "", msg["conv"], msg["id"])))
        if self.mine and msg["kind"] in ("text", "file"):
            m.addAction(icon("edit", T.TEXT, 16), "Edit", lambda: chat.start_edit(msg))
        if msg["kind"] != "poll":
            m.addAction(icon("forward", T.TEXT, 16), "Forward...", lambda: ctx.forward_message(msg))
        poll = msg.get("poll")
        if poll and (poll["creator_id"] == ctx.store.my_id or ctx.store.me.get("is_admin")):
            m.addAction(icon("chart", T.TEXT, 16), "Reopen poll" if poll["closed"] else "Close poll (stop voting)",
                        lambda: ctx.conn.request("close_poll", None, poll_id=poll["id"], reopen=poll["closed"]))
        pinned = any(p["id"] == msg["id"] for p in ctx.store.conversation(msg["conv"]).pins)
        m.addAction(icon("pin", T.TEXT, 16), "Unpin" if pinned else "Pin to the top",
                    lambda: ctx.conn.request("pin", lambda r: None if r.get("ok") else ctx.toast(r.get("error")),
                                             conv=msg["conv"], message_id=msg["id"], pinned=not pinned))
        if self.is_room and self.mine:
            m.addAction(icon("users", T.TEXT, 16), "Seen by...", lambda: chat.show_seen_by(msg))
        m.addSeparator()
        if self.sticker:
            m.addAction(icon("sticker", T.TEXT, 16), "Send this sticker", lambda: chat.send_sticker(msg["body"]))
        paths = [x.rstrip(".,;:)") for x in _PATH_RE.findall(msg.get("body") or "")] if not self.sticker else []
        for path in paths[:5]:
            short = path if len(path) < 50 else "..." + path[-47:]
            sub = m.addMenu(icon("folder", T.TEXT, 16), short)
            sub.addAction("Copy path", lambda p=path: QGuiApplication.clipboard().setText(p))
            sub.addAction("Open folder", lambda p=path: open_path(p))
        if msg.get("body") and not self.sticker:
            sel = self.text.selectedText() if self.text else ""
            m.addAction(icon("list", T.TEXT, 16), "Copy selection" if sel else "Copy text",
                        lambda: QGuiApplication.clipboard().setText(sel or msg["body"]))
        f = msg.get("file")
        if f:
            path = ctx.config.downloaded_path(f["id"])
            if path:
                m.addAction(icon("open", T.TEXT, 16), "Open file", lambda: open_path(path))
                m.addAction(icon("folder", T.TEXT, 16), "Show in folder", lambda: show_in_folder(path))
            elif not f.get("purged"):
                m.addAction(icon("download", T.TEXT, 16), "Download", lambda: ctx.download_file(f, msg["conv"]))
                m.addAction(icon("download", T.TEXT, 16), "Save as...", lambda: ctx.download_file_as(f, msg["conv"]))
            m.addAction("Copy file name", lambda: QGuiApplication.clipboard().setText(f["name"]))
        if self.mine or ctx.store.me.get("is_admin"):
            m.addSeparator()
            m.addAction(icon("trash", T.DANGER, 16), "Delete for everyone" if self.mine else "Delete (moderation)",
                        lambda: chat.delete_message(msg))
        return m


class HoverBar(QFrame):
    """Quick actions shown next to the message under the mouse (one bar shared by all rows)."""

    def __init__(self, chat, parent):
        super().__init__(parent)
        self.chat = chat
        self.row = None
        self.setObjectName("hoverbar")
        self.setStyleSheet(f"#hoverbar {{ background: {T.PANEL}; border: 1px solid {T.HAIR}; border-radius: 14px; }}")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(3, 3, 3, 3)
        lay.setSpacing(0)
        for name, tip, fn in (("smile", "React", self._react), ("reply", "Reply", self._reply),
                              ("forward", "Forward", self._forward), ("more_options", "More", self._more)):
            btn = IconButton(name, tip, 30, 16, round_=False)
            btn.clicked.connect(fn)
            lay.addWidget(btn)
        self.adjustSize()
        self.hide()

    def attach(self, row):
        if row is not self.row:
            self.row = row
        bub = row.bubble
        top_left = bub.mapTo(self.parent(), bub.rect().topLeft())
        x = top_left.x() - self.width() - 6 if row.mine else top_left.x() + bub.width() + 6
        x = max(0, min(x, self.parent().width() - self.width()))
        self.move(x, top_left.y() + max(0, (min(bub.height(), 60) - self.height()) // 2))
        self.raise_()
        self.show()

    def maybe_hide(self):
        if self.underMouse() or (self.row and self.row.underMouse()):
            return
        self.hide()

    def leaveEvent(self, e):
        QTimer.singleShot(60, self.maybe_hide)
        super().leaveEvent(e)

    def _react(self):
        if self.row:
            self.chat.react_menu(self.row.msg, self.mapToGlobal(self.rect().bottomLeft()))

    def _reply(self):
        if self.row:
            self.chat.start_reply(self.row.msg)

    def _forward(self):
        if self.row:
            self.chat.ctx.forward_message(self.row.msg)

    def _more(self):
        if self.row:
            m = self.row.build_menu()
            if m:
                m.exec(self.mapToGlobal(self.rect().bottomLeft()))


# ============================================================ composer
class MentionPopup(QListWidget):
    """Suggestions shown while typing '@name' in a room."""
    picked = Signal(str)

    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowFlags(Qt.ToolTip)
        self.setFocusPolicy(Qt.NoFocus)
        self.setStyleSheet(f"QListWidget {{ background: {T.PANEL}; border: 1px solid {T.HAIR};"
                           f" border-radius: 14px; padding: 6px; }} QListWidget::item {{ padding: 6px 8px; }}"
                           f" QListWidget::item:selected {{ background: {T.ACCENT_SOFT}; }}")
        self.itemClicked.connect(lambda it: self.picked.emit(it.data(Qt.UserRole)))

    def show_for(self, items, anchor: QWidget):
        """items: (token, label, kind) - kind 'group' (everyone, here, a team) or 'person'."""
        self.clear()
        for token, label, kind in items[:8]:
            it = QListWidgetItem(icon("users" if kind == "group" else "user", T.ACCENT if kind == "group" else T.MUTED, 15),
                                 f"@{token}   ·   {label}" if kind == "group" else f"{label}   @{token}")
            it.setData(Qt.UserRole, token)
            self.addItem(it)
        if not self.count():
            self.hide()
            return
        self.setCurrentRow(0)
        h = min(8, self.count()) * 30 + 12
        self.setFixedSize(340, h)
        pos = anchor.mapToGlobal(anchor.rect().topLeft())
        self.move(pos.x(), pos.y() - h - 4)
        self.show()

    def handle_key(self, key) -> bool:
        """Keyboard control while visible; True when the key was used."""
        if not self.isVisible():
            return False
        if key in (Qt.Key_Down, Qt.Key_Up):
            row = self.currentRow() + (1 if key == Qt.Key_Down else -1)
            self.setCurrentRow(max(0, min(self.count() - 1, row)))
            return True
        if key in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Tab):
            if self.currentItem():
                self.picked.emit(self.currentItem().data(Qt.UserRole))
            return True
        if key == Qt.Key_Escape:
            self.hide()
            return True
        return False


class MessageInput(QPlainTextEdit):
    send = Signal()
    files_pasted = Signal(list)
    edit_last = Signal()           # Up arrow in an empty box
    cancel = Signal()              # Esc: cancel reply / edit
    focused = Signal()             # focus in/out (the composer outline follows it)

    def __init__(self):
        super().__init__()
        self.popup = None
        self.setPlaceholderText("Write a message...")
        self.setWordWrapMode(QTextOption.WrapAtWordBoundaryOrAnywhere)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.setStyleSheet("QPlainTextEdit { background: transparent; border: none; padding: 6px 4px;"
                           " font-size: 10.5pt; }"
                           "QPlainTextEdit:focus { background: transparent; border: none; }")
        self.document().contentsChanged.connect(self._fit)
        self._fit()

    def _fit(self):
        fm = QFontMetrics(self.font())
        doc_lines = int(self.document().size().height())
        lines = max(1, min(6, doc_lines))
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded if doc_lines > 6 else Qt.ScrollBarAlwaysOff)
        self.setFixedHeight(lines * fm.lineSpacing() + 24)

    def keyPressEvent(self, e):
        if self.popup is not None and self.popup.handle_key(e.key()):
            return
        if e.key() in (Qt.Key_Return, Qt.Key_Enter) and not e.modifiers() & Qt.ShiftModifier:
            self.send.emit()
            return
        if e.key() == Qt.Key_Up and not self.toPlainText():
            self.edit_last.emit()
            return
        if e.key() == Qt.Key_Escape:
            self.cancel.emit()
            return
        super().keyPressEvent(e)

    def focusInEvent(self, e):
        super().focusInEvent(e)
        self.focused.emit()

    def focusOutEvent(self, e):
        super().focusOutEvent(e)
        self.focused.emit()

    def current_mention(self):
        """The '@abc' word being typed at the cursor, or None."""
        cur = self.textCursor()
        before = self.toPlainText()[:cur.position()]
        word = before.split()[-1] if before and not before[-1].isspace() else ""
        return word[1:] if word.startswith("@") else None

    def complete_mention(self, username):
        cur = self.textCursor()
        partial = self.current_mention() or ""
        for _ in range(len(partial) + 1):
            cur.deletePreviousChar()
        cur.insertText(f"@{username} ")
        self.setTextCursor(cur)

    def canInsertFromMimeData(self, source):
        return source.hasImage() or source.hasUrls() or super().canInsertFromMimeData(source)

    def insertFromMimeData(self, source: QMimeData):
        if source.hasUrls() and all(u.isLocalFile() for u in source.urls()):
            self.files_pasted.emit([u.toLocalFile() for u in source.urls()])
            return
        if source.hasImage():
            img = source.imageData()
            path = os.path.join(tempfile.gettempdir(), f"pasted_{time.strftime('%Y%m%d_%H%M%S')}.png")
            if img.save(path, "PNG"):
                self.files_pasted.emit([path])
                return
        super().insertFromMimeData(source)


class EmojiMenu(QMenu):
    picked = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        w = QWidget()
        grid = QGridLayout(w)
        grid.setContentsMargins(6, 6, 6, 6)
        grid.setSpacing(2)
        for i, e in enumerate(EMOJIS):
            b = QToolButton()
            b.setText(e)
            b.setFixedSize(36, 36)
            b.setCursor(Qt.PointingHandCursor)
            b.setStyleSheet("QToolButton { border: none; border-radius: 8px; font-family: 'Segoe UI Emoji';"
                            " font-size: 16pt; background: transparent; }"
                            f"QToolButton:hover {{ background: {T.SURFACE_HOVER}; }}")
            b.clicked.connect(lambda _=False, e=e: (self.picked.emit(e), self.close()))
            grid.addWidget(b, i // 8, i % 8)
        act = QWidgetAction(self)
        act.setDefaultWidget(w)
        self.addAction(act)


class StickerPicker(QFrame):
    """Popup with the sticker packs: a tab per pack (plus Recent) and a grid of stickers."""
    picked = Signal(str)

    CELL = 92

    def __init__(self, config, parent=None):
        super().__init__(parent, Qt.Popup)
        from client import stickers
        self.config = config
        self.packs = stickers.packs()
        self.setObjectName("stickers")
        self.setStyleSheet(f"#stickers {{ background: {T.PANEL}; border: 1px solid {T.HAIR}; border-radius: 18px; }}")
        self.setFixedSize(self.CELL * 5 + 34, 420)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(6)
        top = QHBoxLayout()
        self.title = QLabel()
        self.title.setStyleSheet(f"color: {T.MUTED}; font-size: 8pt; font-weight: 700; padding: 2px 6px;")
        top.addWidget(self.title, 1)
        credit = QLabel("Stickers designed by Freepik")
        credit.setStyleSheet(f"color: {T.FAINT}; font-size: 7.5pt; padding: 2px 6px;")
        top.addWidget(credit)
        lay.addLayout(top)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        lay.addWidget(self.scroll, 1)
        tabs_area = QScrollArea()
        tabs_area.setFixedHeight(50)
        tabs_area.setWidgetResizable(True)
        tabs_area.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        tabs_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        tabs = QWidget()
        T.bg_pane(tabs, T.PANEL)
        tl = QHBoxLayout(tabs)
        tl.setContentsMargins(0, 0, 0, 0)
        tl.setSpacing(2)
        self.tab_buttons = []
        tabs_area.setWidget(tabs)
        lay.addWidget(_hline())
        lay.addWidget(tabs_area)
        self.tabs_area = tabs_area
        entries = [("recent", "Recently used", None)] + [(pk["id"], pk["title"], pk["stickers"][0])
                                                          for pk in self.packs if pk["stickers"]]
        for key, title, cover in entries:
            b = QToolButton()
            b.setCheckable(True)
            b.setFixedSize(44, 44)
            b.setToolTip(title)
            b.setCursor(Qt.PointingHandCursor)
            if cover:
                pm = stickers.pixmap(cover, 34)
                if pm:
                    from PySide6.QtGui import QIcon
                    b.setIcon(QIcon(pm))
            else:
                b.setIcon(icon("refresh", T.MUTED, 18))
            b.setIconSize(QSize(34, 34) if cover else QSize(18, 18))
            b.setStyleSheet(f"QToolButton {{ border: none; border-radius: 10px; background: transparent; }}"
                            f"QToolButton:hover {{ background: {T.SURFACE_HOVER}; }}"
                            f"QToolButton:checked {{ background: {T.ACCENT_SOFT}; }}")
            b.clicked.connect(lambda _=False, k=key: self.show_pack(k))
            tl.addWidget(b)
            self.tab_buttons.append((key, b))
        tl.addStretch(1)
        def horizontal_wheel(e):                  # mouse wheel scrolls the tab strip sideways
            bar = tabs_area.horizontalScrollBar()
            bar.setValue(bar.value() - e.angleDelta().y())
        tabs_area.wheelEvent = horizontal_wheel
        self.show_pack(self.first_pack())

    def first_pack(self):
        """The festival's own pack on its day, else recently used, else the first pack."""
        fest = (T.FESTIVAL or {}).get("stickers")
        if fest and any(p["id"] == fest for p in self.packs):
            return fest
        return "recent" if self.config["recent_stickers"] else (self.packs[0]["id"] if self.packs else "recent")

    def show_pack(self, key):
        from client import stickers
        for k, b in self.tab_buttons:
            b.setChecked(k == key)
        if key == "recent":
            ids, title = [x for x in self.config["recent_stickers"] if stickers.path(x)], "RECENTLY USED"
        else:
            pk = next(p for p in self.packs if p["id"] == key)
            ids, title = pk["stickers"], pk["title"].upper()
        self.title.setText(title)
        grid_w = QWidget()
        T.bg_pane(grid_w, T.PANEL)
        grid = QGridLayout(grid_w)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(2)
        for i, sid in enumerate(ids):
            b = QToolButton()
            b.setFixedSize(self.CELL, self.CELL)
            b.setCursor(Qt.PointingHandCursor)
            pm = stickers.pixmap(sid, self.CELL - 12)
            if pm:
                from PySide6.QtGui import QIcon
                b.setIcon(QIcon(pm))
                b.setIconSize(pm.size())
            b.setStyleSheet(f"QToolButton {{ border: none; border-radius: 12px; background: transparent; }}"
                            f"QToolButton:hover {{ background: {T.SURFACE_HOVER}; }}")
            b.clicked.connect(lambda _=False, x=sid: (self.picked.emit(x), self.close()))
            grid.addWidget(b, i // 5, i % 5)
        if not ids:
            empty = QLabel("Stickers you send show up here.\nPick a pack below.")
            empty.setAlignment(Qt.AlignCenter)
            empty.setStyleSheet(f"color: {T.FAINT}; padding: 40px;")
            grid.addWidget(empty, 0, 0)
        grid.setRowStretch(grid.rowCount(), 1)
        self.scroll.setWidget(grid_w)
        self.scroll.verticalScrollBar().setValue(0)


class UploadStrip(QFrame):
    """Shows uploads of the current conversation above the composer."""

    def __init__(self, ctx):
        super().__init__()
        self.ctx = ctx
        self.conv = None
        self.rows = {}
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(12, 0, 12, 0)
        self.lay.setSpacing(4)
        ctx.transfers.added.connect(self._changed)
        ctx.transfers.changed.connect(self._changed)
        self.hide()

    def set_conv(self, conv):
        self.conv = conv
        for w in self.rows.values():
            w.deleteLater()
        self.rows.clear()
        for t in self.ctx.transfers.transfers:
            self._changed(t)

    def _changed(self, t):
        if t.kind != "upload" or t.conv != self.conv:
            return
        row = self.rows.get(id(t))
        if not t.active and t.state != "failed":
            if row:
                row.deleteLater()
                del self.rows[id(t)]
            self.setVisible(bool(self.rows))
            return
        if not row:
            row = QFrame()
            row.setStyleSheet(f"background: {T.PANEL}; border-radius: 10px;")
            h = QHBoxLayout(row)
            h.setContentsMargins(10, 6, 6, 6)
            ic = QLabel()
            ic.setPixmap(pixmap("upload", T.ACCENT, 16))
            h.addWidget(ic)
            row.label = QLabel()
            row.label.setStyleSheet("background: transparent;")
            h.addWidget(row.label, 1)
            row.bar = QProgressBar()
            row.bar.setFixedSize(140, 6)
            row.bar.setRange(0, 1000)
            h.addWidget(row.bar)
            row.btn = IconButton("close", "Cancel", 28, 14)
            row.btn.clicked.connect(lambda: (t.cancel() if t.active else None, self._drop(t)))
            h.addWidget(row.btn)
            self.rows[id(t)] = row
            self.lay.addWidget(row)
        if t.state == "failed":
            row.label.setText(f"<span style='color:{T.DANGER}'>Upload failed:</span> {esc(t.name)} — {esc(t.error)}")
            row.bar.hide()
        else:
            speed = f" · {P.human_size(t.speed)}/s" if t.speed else ""
            verb = "Packing folder" if hasattr(t, "files") else "Sending"
            row.label.setText(f"{verb} <b>{esc(t.name)}</b>  {P.human_size(t.done)} / {P.human_size(t.size)}{speed}")
            row.bar.setValue(int(t.done / t.size * 1000) if t.size else 0)
        self.show()

    def _drop(self, t):
        row = self.rows.pop(id(t), None)
        if row:
            row.deleteLater()
        self.setVisible(bool(self.rows))


# ============================================================ chat view
class ChatView(QWidget):
    back = Signal()

    def __init__(self, ctx):
        super().__init__()
        self.ctx = ctx
        self.store = ctx.store
        self.conv = None
        self.rows = []               # message widgets in order
        self.render_limit = RENDER_MAX
        self.rendered = 0            # messages currently shown
        self.last_typing_sent = 0
        self.typing_users = {}       # uid -> expiry
        self._base_subtitle = ""
        self.setAcceptDrops(True)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        # header
        head = self.head = QFrame()
        head.setObjectName("chathead")
        head.setFixedHeight(68)
        head.setStyleSheet(f"#chathead {{ background: {T.BG}; border-bottom: 1px solid {T.HAIR}; }}")
        hl = QHBoxLayout(head)
        hl.setContentsMargins(22, 10, 16, 10)
        hl.setSpacing(12)
        self.b_back = IconButton("back", "Back to the list", 34, 18, round_=False)
        self.b_back.clicked.connect(self._go_back)
        self.b_back.hide()
        hl.addWidget(self.b_back)
        self.avatar = Avatar(42)
        self.avatar.ring = T.BG
        hl.addWidget(self.avatar)
        col = QVBoxLayout()
        col.setSpacing(1)
        self.title = plain(QLabel())
        self.title.setStyleSheet("font-size: 12.5pt; font-weight: 700;")
        self.subtitle = QLabel()
        self.subtitle.setStyleSheet(f"color: {T.MUTED}; font-size: 9pt;")
        col.addStretch(1)
        col.addWidget(self.title)
        col.addWidget(self.subtitle)
        col.addStretch(1)
        hl.addLayout(col, 1)
        self.b_buzz = IconButton("zap", "Buzz — shake their window and ring, even if they are busy", 38, 19)
        self.b_buzz.clicked.connect(self.buzz)
        self.b_screen = IconButton("screen", "Share screen", 38, 19)
        self.b_screen.clicked.connect(self.screen_menu)
        self.b_search = IconButton("search", "Search messages", 38, 18)
        self.b_search.clicked.connect(lambda: self.ctx.show_search())
        self.b_members = IconButton("users", "Members", 38, 19)
        self.b_members.clicked.connect(lambda: self.ctx.show_room_info(self.conv))
        self.b_more = IconButton("more_options", "More", 38, 18)
        self.b_more.clicked.connect(self.more_menu)
        for b in (self.b_buzz, self.b_screen, self.b_search, self.b_members, self.b_more):
            hl.addWidget(b)
        lay.addWidget(head)

        # pinned message card
        self.pin_bar = QFrame()
        self.pin_bar.setObjectName("pinbar")
        self.pin_bar.setCursor(Qt.PointingHandCursor)
        self.pin_bar.setStyleSheet(f"#pinbar {{ background: {T.PANEL}; border-bottom: 1px solid {T.HAIR}; }}")
        pb = QHBoxLayout(self.pin_bar)
        pb.setContentsMargins(22, 7, 16, 7)
        pb.setSpacing(12)
        mark = QFrame()
        mark.setFixedSize(3, 30)
        mark.setStyleSheet(f"background: {T.ACCENT}; border-radius: 1px;")
        pb.addWidget(mark)
        pin_ic = QLabel()
        pin_ic.setPixmap(pixmap("pin", T.ACCENT, 16))
        pb.addWidget(pin_ic)
        pcol = QVBoxLayout()
        pcol.setSpacing(0)
        self.pin_title = QLabel("Pinned message")
        self.pin_title.setStyleSheet(f"color: {T.ACCENT}; font-size: 8.5pt; font-weight: 700;")
        self.pin_text = plain(QLabel())
        self.pin_text.setStyleSheet(f"color: {T.TEXT}; font-size: 9.5pt;")
        pcol.addWidget(self.pin_title)
        pcol.addWidget(self.pin_text)
        pb.addLayout(pcol, 1)
        self.pin_list = IconButton("list", "All pinned messages", 32, 16)
        self.pin_list.clicked.connect(self.pins_menu)
        pb.addWidget(self.pin_list)
        self.pin_bar.mousePressEvent = self._pin_clicked
        self.pin_bar.hide()
        lay.addWidget(self.pin_bar)

        # messages
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setStyleSheet(f"QScrollArea {{ background: {T.BG}; }}")
        self.container = QWidget()
        T.bg_pane(self.container)
        self.mlay = QVBoxLayout(self.container)
        self.mlay.setContentsMargins(24, 10, 24, 14)
        self.mlay.setSpacing(0)
        self.loading = plain(QLabel("Loading..."))
        self.loading.setAlignment(Qt.AlignCenter)
        self.loading.setStyleSheet(f"color: {T.FAINT}; padding: 8px;")
        self.mlay.addWidget(self.loading)
        self.mlay.addStretch(1)
        self.scroll.setWidget(self.container)
        self.hover_bar = HoverBar(self, self.container)
        self.scroll.verticalScrollBar().valueChanged.connect(self._on_scroll)
        self.scroll.verticalScrollBar().rangeChanged.connect(self._on_range)
        lay.addWidget(self.scroll, 1)
        self._stick_bottom = True
        self._keep_from_bottom = None

        self.uploads = UploadStrip(ctx)
        lay.addWidget(self.uploads)

        # composer: reply/edit strip on top of a rounded input card
        comp_wrap = QWidget()
        T.bg_pane(comp_wrap)
        cw = QVBoxLayout(comp_wrap)
        cw.setContentsMargins(20, 6, 20, 16)
        cw.setSpacing(0)
        self.composer = QFrame()
        self.composer.setObjectName("composer")
        cl = QHBoxLayout(self.composer)
        cl.setContentsMargins(8, 5, 6, 5)
        cl.setSpacing(2)
        self.b_attach = IconButton("attachment", "Attach files (or drag & drop / paste)", 36, 18)
        self.b_attach.clicked.connect(self.attach_menu)
        self.b_emoji = IconButton("smile", "Emoji", 36, 19)
        self.emoji_menu = EmojiMenu(self)
        self.emoji_menu.picked.connect(lambda e: (self.input.insertPlainText(e), self.input.setFocus()))
        self.b_emoji.clicked.connect(lambda: self.emoji_menu.exec(self._popup_pos(self.b_emoji, self.emoji_menu)))
        self.b_sticker = IconButton("sticker", "Stickers", 36, 19)
        self.b_sticker.clicked.connect(self.open_stickers)
        self.b_later = IconButton("clock", "Send later, or remind me", 36, 19)
        self.b_later.clicked.connect(self.later_menu)
        self.b_shot = IconButton("image", "Screenshot — pick an area of the screen and send it (Ctrl+Shift+S)", 36, 19)
        self.b_shot.clicked.connect(self.take_screenshot)
        self.sticker_picker = None
        self.input = MessageInput()
        self.input.send.connect(self.send_text)
        self.input.files_pasted.connect(self.send_files)
        self.input.textChanged.connect(self._on_typing)
        self.input.textChanged.connect(self._update_mention_popup)
        self.input.textChanged.connect(self._update_send_button)
        self.input.textChanged.connect(self._update_long_bar)
        self.input.edit_last.connect(self._edit_last)
        self.input.cancel.connect(self.cancel_action)
        self.input.focused.connect(self._style_composer)
        self.mention_popup = MentionPopup(self)
        self.mention_popup.picked.connect(self._pick_mention)
        self.input.popup = self.mention_popup
        self.reply_to = None
        self.editing = None
        self.b_send = QPushButton()
        self.b_send.setFixedSize(40, 40)
        self.b_send.setCursor(Qt.PointingHandCursor)
        self.b_send.setIconSize(QSize(18, 18))
        self.b_send.setToolTip("Send (Enter). Shift+Enter for a new line")
        self.b_send.clicked.connect(self.send_text)
        cl.addWidget(self.b_attach, 0, Qt.AlignBottom)
        cl.addWidget(self.input, 1)
        cl.addWidget(self.b_shot, 0, Qt.AlignBottom)
        cl.addWidget(self.b_emoji, 0, Qt.AlignBottom)
        cl.addWidget(self.b_sticker, 0, Qt.AlignBottom)
        cl.addWidget(self.b_later, 0, Qt.AlignBottom)
        cl.addSpacing(4)
        cl.addWidget(self.b_send, 0, Qt.AlignBottom)

        self.action_bar = QFrame()
        self.action_bar.setObjectName("actionbar")
        self.action_bar.setStyleSheet(f"#actionbar {{ background: {T.PANEL}; border: 1px solid {T.HAIR};"
                                      " border-bottom: none; border-top-left-radius: 18px;"
                                      " border-top-right-radius: 18px; }")
        ab = QHBoxLayout(self.action_bar)
        ab.setContentsMargins(14, 8, 8, 8)
        ab.setSpacing(10)
        self.action_icon = QLabel()
        ab.addWidget(self.action_icon)
        acol = QVBoxLayout()
        acol.setSpacing(0)
        self.action_title = plain(QLabel())
        self.action_title.setStyleSheet(f"color: {T.ACCENT}; font-size: 8.5pt; font-weight: 700;")
        self.action_label = plain(QLabel())
        self.action_label.setStyleSheet(f"color: {T.MUTED}; font-size: 9pt;")
        acol.addWidget(self.action_title)
        acol.addWidget(self.action_label)
        ab.addLayout(acol, 1)
        x = IconButton("close", "Cancel (Esc)", 28, 13)
        x.clicked.connect(self.cancel_action)
        ab.addWidget(x)
        self.action_bar.hide()
        self.sched_bar = QPushButton()
        self.sched_bar.setCursor(Qt.PointingHandCursor)
        self.sched_bar.setIcon(icon("clock", T.ACCENT, 15))
        self.sched_bar.setStyleSheet(f"QPushButton {{ text-align: left; background: transparent; border: none;"
                                     f" color: {T.ACCENT}; font-size: 9pt; font-weight: 600; padding: 2px 6px 6px 6px; }}"
                                     f"QPushButton:hover {{ color: {T.TEXT}; }}")
        self.sched_bar.clicked.connect(self.scheduled_menu)
        self.sched_bar.hide()
        cw.addWidget(self.sched_bar, 0, Qt.AlignLeft)
        self.long_bar = QWidget()
        lb = QHBoxLayout(self.long_bar)
        lb.setContentsMargins(6, 0, 6, 6)
        lb.setSpacing(8)
        self.long_label = plain(QLabel())
        self.long_label.setStyleSheet(f"color: {T.MUTED}; font-size: 9pt;")
        lb.addWidget(self.long_label)
        self.long_file = QPushButton()
        self.long_file.setCursor(Qt.PointingHandCursor)
        self.long_file.setStyleSheet(f"QPushButton {{ background: transparent; border: none; color: {T.ACCENT};"
                                     f" font-size: 9pt; font-weight: 700; padding: 0; }}"
                                     f"QPushButton:hover {{ color: {T.TEXT}; }}")
        self.long_file.clicked.connect(lambda: self.send_text_as_file(self.input.toPlainText().strip()))
        lb.addWidget(self.long_file)
        lb.addStretch(1)
        self.long_bar.hide()
        cw.addWidget(self.long_bar)
        cw.addWidget(self.action_bar)
        cw.addWidget(self.composer)
        self.offline_note = plain(QLabel())
        self.offline_note.setWordWrap(True)
        self.offline_note.setStyleSheet(f"color: {T.MUTED}; font-size: 8pt; padding: 6px 14px 0 14px;")
        self.offline_note.hide()
        cw.addWidget(self.offline_note)
        lay.addWidget(comp_wrap)
        self._style_composer()
        self._update_send_button()

        # drop overlay
        self.drop_overlay = QLabel("Drop files to send", self)
        self.drop_overlay.setAlignment(Qt.AlignCenter)
        self.drop_overlay.setStyleSheet(f"background: {T.rgba(T.BG, 0.92)}; color: {T.ACCENT}; font-size: 16pt;"
                                        f"font-weight: 700; border: 2px dashed {T.ACCENT}; border-radius: 20px;")
        self.drop_overlay.hide()

        self.typing_timer = QTimer(self, interval=1000, timeout=self._expire_typing)

        s = self.store
        s.message_added.connect(self._on_message)
        s.history_loaded.connect(self._on_history)
        s.receipt.connect(self._on_receipt)
        s.typing.connect(self._on_typing_event)
        s.user_updated.connect(self._on_user_updated)
        s.users_changed.connect(self.update_header)
        s.rooms_changed.connect(self.update_header)
        s.message_updated.connect(self._on_message_updated)
        s.pins_changed.connect(self._on_pins_changed)
        s.planner_changed.connect(self._update_scheduled_bar)
        self.seen_timer = QTimer(self, interval=15000, timeout=self._update_seen)

    # ------------------------------------------------------------ open
    def open(self, conv):
        if self.conv and self.conv in self.store.convs:
            prev = self.store.conversation(self.conv)
            prev.draft = self.input.toPlainText()
            if self.conv != conv:
                prev.trim()
        self.conv = conv
        self.render_limit = RENDER_MAX
        self.typing_users.clear()
        self.cancel_action()
        self.mention_popup.hide()
        c = self.store.conversation(conv)
        self._on_pins_changed(conv)
        self.store.load_pins(conv)
        self.input.blockSignals(True)
        self.input.setPlainText(c.draft)
        self.input.blockSignals(False)
        self.input.moveCursor(QTextCursor.End)
        self.update_header()
        self._update_scheduled_bar()
        self.uploads.set_conv(conv)
        self._stick_bottom = True
        self.render_all()
        if not c.history_requested:
            self.loading.show()
            self.store.load_history(conv)
        self.input.setFocus()

    def update_header(self, *_):
        if not self.conv:
            return
        kind, target = P.parse_conv(self.conv)
        title = self.store.title(self.conv)
        self.title.setText(title)
        is_room = kind == "r"
        self.b_members.setVisible(is_room)
        self.b_screen.setVisible(not is_room and target != self.store.my_id and not self.compact)
        self.b_search.setVisible(not self.compact)
        self.b_buzz.setVisible(target != self.store.my_id and getattr(self.store, "buzz_enabled", False)
                               if not is_room else getattr(self.store, "buzz_enabled", False))
        self.b_buzz.setToolTip("Buzz the whole room — everyone's window shakes and rings (once a minute)"
                               if is_room else "Buzz — shake their window and ring, even if they are busy")
        if is_room:
            room = self.store.rooms.get(target)
            self.avatar.set(title, self.conv, room=True)
            if room:
                names = [self.store.user_name(u) for u in room["members"] if u != self.store.my_id]
                sub = f"{len(room['members'])} members"
                if room.get("auto"):
                    sub += "  ·  Automatic room"
                if room.get("topic"):
                    sub = f"{room['topic']}  ·  {sub}"
                self.subtitle.setToolTip(rich_safe(", ".join(sorted(names))))
            else:
                sub = ""
            self.offline_note.hide()
        elif target == self.store.my_id:
            me = self.store.me
            self.avatar.set(title, title, status=me.get("status", "online"), uid=target)
            sub = "Your personal space: notes, to-dos, links and files  ·  only you can see it"
            self.subtitle.setToolTip("")
            self.offline_note.hide()
        else:
            u = self.store.users.get(target) or {}
            status = u.get("status", "offline")
            self.avatar.set(title, title, status=status, uid=target)
            parts = [T.STATUS_LABELS.get(status, status) if status != "offline"
                     else fmt_last_seen(u.get("last_seen"))]
            if self.store.status_text(u):
                parts.append(self.store.status_text(u))
            line = self.store.designation_line(u)
            if line:
                parts.append(line)
            manager = self.store.manager_name(target)
            if manager:
                parts.append(f"Reports to {manager}")
            sub = "  ·  ".join(parts)
            self.subtitle.setToolTip("")
            if status == "offline":
                self.offline_note.setText(f"{title} is offline — messages and files will be delivered when they log in.")
                self.offline_note.show()
            else:
                self.offline_note.hide()
        self._base_subtitle = esc(sub)
        self._show_subtitle()

    def _show_subtitle(self):
        now = time.time()
        typers = [uid for uid, exp in self.typing_users.items() if exp > now]
        if typers:
            names = [esc(first_name(self.store.user_name(u), "Someone")) for u in typers]
            text = (f"{names[0]} is typing..." if len(names) == 1 else f"{', '.join(names)} are typing...")
            self.subtitle.setText(f"<span style='color:{T.ACCENT}'>{text}</span>")
        else:
            self.subtitle.setText(self._base_subtitle)

    # ---------------------------------------------------------- render
    def _clear(self):
        self.hover_bar.hide()
        self.hover_bar.row = None
        for w in self.rows:
            w.deleteLater()
        self.rows = []

    def render_all(self):
        self.setUpdatesEnabled(False)
        self._clear()
        c = self.store.conversation(self.conv)
        msgs = sorted(c.messages.values(), key=lambda m: m["id"])
        cut = max(0, len(msgs) - self.render_limit)
        prev = msgs[cut - 1] if cut else None
        for m in msgs[cut:]:
            self._append(m, prev)
            prev = m
        self.rendered = len(msgs) - cut
        empty = not msgs and c.complete
        self.loading.setText(self._empty_text() if empty else "Loading...")
        self.loading.setStyleSheet(f"color: {T.MUTED if empty else T.FAINT}; font-size: {'11pt' if empty else '10pt'};"
                                   f" padding: {'80px 20px' if empty else '8px'};")
        self.loading.setVisible(empty or (not c.complete and bool(c.history_requested) and not msgs))
        self.setUpdatesEnabled(True)
        self._apply_widths()
        QTimer.singleShot(300, self._update_seen)

    def _empty_text(self):
        kind, target = P.parse_conv(self.conv)
        if kind == "r":
            return f"No messages in {self.store.title(self.conv)} yet.\nSay hello to the room 👋"
        if target == self.store.my_id:
            return "Your own space: notes, links and files for later.\nOnly you can see this chat."
        first = first_name(self.store.user_name(target))
        return f"No messages yet.\nSay hi to {first} 👋"

    def _append(self, m, prev):
        is_room = self.conv.startswith("r:")
        new_day = not prev or datetime.date.fromtimestamp(prev["ts"]) != datetime.date.fromtimestamp(m["ts"])
        if new_day:
            sep = DaySeparator(m["ts"])
            self.mlay.insertWidget(self.mlay.count(), sep)
            self.rows.append(sep)
        if m["kind"] == "system":
            w = SystemLine(m)
        elif m["kind"] == "buzz":
            mine = m["sender_id"] == self.store.my_id
            room_buzz = self.conv.startswith("r:")
            w = BuzzLine(m, ("You buzzed the room" if room_buzz else f"You buzzed {self.store.title(self.conv)}")
                         if mine else f"{self.store.user_name(m['sender_id'])} buzzed "
                                      + ("the room" if room_buzz else "you"))
        else:
            grouped = (prev and not new_day and prev["sender_id"] == m["sender_id"]
                       and prev["kind"] not in ("system", "buzz")
                       and m["ts"] - prev["ts"] < GROUP_SECONDS)
            w = MessageRow(self.ctx, m, m["sender_id"] == self.store.my_id, not grouped, not grouped, is_room)
        self.mlay.insertWidget(self.mlay.count(), w)
        self.rows.append(w)
        return w

    def _apply_widths(self):
        w = int(max(300, self.scroll.viewport().width() - 40) * 0.72)
        for r in self.rows:
            if isinstance(r, MessageRow):
                r.set_max_width(w)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._apply_widths()
        self.drop_overlay.setGeometry(self.rect().adjusted(12, 12, -12, -12))

    def _last_msg(self):
        for r in reversed(self.rows):
            if hasattr(r, "msg"):
                return r.msg
        return None

    def _on_message(self, msg, is_new):
        if msg["conv"] != self.conv:
            return
        if msg["sender_id"] in self.typing_users:
            self.typing_users.pop(msg["sender_id"], None)
            self._show_subtitle()
        last = self._last_msg()
        if last and msg["id"] < last["id"]:
            self.render_all()
            return
        if last and msg["id"] == last["id"]:
            return
        if any(getattr(r, "msg", {}).get("id") == msg["id"] for r in self.rows[-20:]):
            return
        near_bottom = self._at_bottom() or msg["sender_id"] == self.store.my_id
        if near_bottom and self.rendered >= self.render_limit + 60:
            # a busy chat left open all day: drop the oldest widgets instead of growing forever
            self.render_limit = RENDER_MAX
            self._stick_bottom = True
            self.render_all()
            return
        w = self._append(msg, last)
        self.rendered += 1
        if isinstance(w, MessageRow):
            w.set_max_width(int(max(300, self.scroll.viewport().width() - 40) * 0.72))
        self.loading.hide()
        if near_bottom:
            self._stick_bottom = True
        QTimer.singleShot(500, self._update_seen)

    def _on_history(self, conv, msgs, older):
        if conv != self.conv:
            return
        bar = self.scroll.verticalScrollBar()
        if older:
            self._keep_from_bottom = bar.maximum() - bar.value()
            self._stick_bottom = False
            self.render_limit = self.rendered + len(msgs)
        else:
            self._stick_bottom = True
        self.render_all()

    def _on_receipt(self, conv):
        if conv == self.conv:
            for r in self.rows:
                if isinstance(r, MessageRow):
                    r.update_meta()

    def _on_user_updated(self, uid):
        if self.conv == P.direct_conv(uid) or (self.conv and self.conv.startswith("r:")):
            self.update_header()

    def _at_bottom(self):
        bar = self.scroll.verticalScrollBar()
        return bar.value() >= bar.maximum() - 60

    def _on_range(self, _min, maximum):
        bar = self.scroll.verticalScrollBar()
        if self._keep_from_bottom is not None:
            bar.setValue(maximum - self._keep_from_bottom)
            self._keep_from_bottom = None
        elif self._stick_bottom:
            bar.setValue(maximum)

    def _on_scroll(self, value):
        self.hover_bar.hide()
        bar = self.scroll.verticalScrollBar()
        self._stick_bottom = value >= bar.maximum() - 60
        if value == 0 and bar.maximum() > 0 and self.conv:
            c = self.store.conversation(self.conv)
            if len(c.messages) > self.rendered:        # already in memory: just show more
                self._keep_from_bottom = bar.maximum() - bar.value()
                self._stick_bottom = False
                self.render_limit = self.rendered + 100
                self.render_all()
            elif not c.complete and c.history_requested:
                self.store.load_history(self.conv, older=True)

    # ---------------------------------------------------------- typing
    def _on_typing(self):
        if not self.conv or not self.input.toPlainText().strip():
            return
        now = time.time()
        if now - self.last_typing_sent > 3:
            self.last_typing_sent = now
            self.ctx.conn.send("typing", conv=self.conv)

    def _on_typing_event(self, conv, uid):
        if conv == self.conv:
            self.typing_users[uid] = time.time() + 5
            self._show_subtitle()
            self.typing_timer.start()

    def _expire_typing(self):
        now = time.time()
        self.typing_users = {u: e for u, e in self.typing_users.items() if e > now}
        self._show_subtitle()
        if not self.typing_users:
            self.typing_timer.stop()

    # ------------------------------------------------------------ send
    def send_text(self):
        text = self.input.toPlainText().strip()
        if not text or not self.conv:
            return
        if not self.ctx.conn.online:
            self.ctx.toast("Not connected — your message was not sent.")
            return
        conv = self.conv
        if self.editing:
            msg = self.editing
            self.cancel_action()
            self.input.clear()
            self.ctx.conn.request("edit", lambda r: None if r.get("ok") else self.ctx.toast(
                f"Not edited: {r.get('error')}"), id=msg["id"], text=text)
            return
        if len(text) > P.MAX_TEXT:
            nuke = is_nuke(text)
            if QMessageBox.question(
                    self, "Long message",
                    f"This is {len(text):,} characters — too long for one chat message "
                    f"(max {P.MAX_TEXT:,}).\n\nSend it as a {'.nk' if nuke else '.txt'} file instead?") != QMessageBox.Yes:
                return
            self.send_text_as_file(text)
            return
        reply_to = self.reply_to["id"] if self.reply_to else None
        self.cancel_action()
        self.input.clear()
        self.last_typing_sent = 0

        def done(reply):
            if reply.get("ok"):
                self.store.add_message(reply["message"])
            else:
                self.ctx.toast(f"Message not sent: {reply.get('error')}")
                if not self.input.toPlainText():
                    self.input.setPlainText(text)
        self.ctx.conn.request("send", done, conv=conv, text=text, reply_to=reply_to)
        self._stick_bottom = True

    def send_text_as_file(self, text):
        """Send the composer text as a .nk (Nuke script) or .txt attachment instead of a message."""
        if not text or not self.conv:
            return
        nuke = is_nuke(text)
        path = os.path.join(tempfile.gettempdir(),
                            f"{'script' if nuke else 'message'}_{time.strftime('%Y%m%d_%H%M%S')}"
                            f"{'.nk' if nuke else '.txt'}")
        try:
            with open(path, "w", encoding="utf-8", newline="\n") as f:
                f.write(text)
        except OSError as e:
            self.ctx.toast(f"Could not create the file: {e}")
            return
        self.cancel_action()
        self.input.clear()
        self.send_files([path])

    def _update_long_bar(self):
        """Long text in the composer: say it goes as a compact card, offer a file instead."""
        text = self.input.toPlainText().strip()
        if not text or self.editing or not is_snippet(text) or len(text) > P.MAX_TEXT:
            self.long_bar.hide()
            return
        nuke = is_nuke(text)
        lines = text.count("\n") + 1
        self.long_label.setText(f"📄 {'Nuke script' if nuke else 'Long text'} · {lines:,} lines — "
                                f"sends as a compact card  ·")
        self.long_file.setText(f"Send as {'.nk' if nuke else '.txt'} file instead")
        self.long_bar.show()

    def pick_folder(self):
        d = QFileDialog.getExistingDirectory(self, "Send a folder (it is zipped automatically)")
        if d and self.conv:
            self.ctx.send_folder(self.conv, d)

    def pick_files(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "Send files", "")
        if paths:
            self.send_files(paths)

    def send_files(self, paths):
        if self.conv:
            for p in paths:
                self.ctx.send_file(self.conv, p)

    def take_screenshot(self):
        """Hide, let the person pick an area of any screen, then preview and send it to this chat."""
        if not self.conv or getattr(self, "_snipper", None):
            return
        from client.ui.snip import ScreenshotDialog, Snipper
        conv = self.conv
        self._snipper = Snipper(self.window())

        def done(image):
            self._snipper = None
            if image.isNull() or not self.store.conv_exists(conv):
                return
            dlg = ScreenshotDialog(self.window(), image, self.store.title(conv))
            if dlg.exec():
                path = dlg.save()
                if path:
                    self.ctx.send_file(conv, path, dlg.caption.text().strip())
                else:
                    self.ctx.toast("Could not save the screenshot")
        self._snipper.done.connect(done)
        self._snipper.start()

    def _popup_pos(self, button, popup):
        """Open a popup above a composer button, kept inside the window."""
        g = button.mapToGlobal(button.rect().topRight())
        size = popup.sizeHint()
        return QPoint(g.x() - size.width(), g.y() - size.height() - 8)

    def _style_composer(self):
        focus = self.input.hasFocus()
        top = "18px" if not self.action_bar.isVisible() else "0px"
        self.composer.setStyleSheet(
            f"#composer {{ background: {T.SURFACE}; border: 1px solid {T.ACCENT_FOCUS if focus else T.HAIR};"
            f" border-top-left-radius: {top}; border-top-right-radius: {top};"
            " border-bottom-left-radius: 18px; border-bottom-right-radius: 18px; }")

    def _update_send_button(self):
        ready = bool(self.input.toPlainText().strip())
        self.b_send.setIcon(icon("send", T.ACCENT_TEXT if ready else T.FAINT, 18))
        self.b_send.setStyleSheet(
            f"QPushButton {{ background: {T.ACCENT if ready else T.SURFACE_HOVER}; border: none;"
            " border-radius: 14px; padding: 0; }"
            f"QPushButton:hover {{ background: {T.ACCENT_HOVER if ready else T.SURFACE_HOVER}; }}")

    def attach_menu(self):
        m = QMenu(self)
        m.addAction(icon("attachment", T.TEXT, 16), "Send files...", self.pick_files)
        m.addAction(icon("folder", T.TEXT, 16), "Send a folder (zipped)...", self.pick_folder)
        m.addAction(icon("image", T.TEXT, 16), "Take a screenshot...   Ctrl+Shift+S", self.take_screenshot)
        m.addSeparator()
        m.addAction(icon("chart", T.TEXT, 16), "Create a poll...", self.create_poll)
        g = self.b_attach.mapToGlobal(self.b_attach.rect().topLeft())
        m.exec(QPoint(g.x(), g.y() - m.sizeHint().height() - 8))

    def screen_menu(self):
        kind, target = P.parse_conv(self.conv)
        m = QMenu(self)
        m.addAction(icon("screen", T.TEXT, 16), "Share my screen...", lambda: self.ctx.screens.invite(target, "offer"))
        m.addAction(icon("search", T.TEXT, 16), "Ask to see their screen...",
                    lambda: self.ctx.screens.invite(target, "request"))
        m.exec(self.b_screen.mapToGlobal(self.b_screen.rect().bottomLeft()))

    # ----------------------------------------------------------- compact
    compact = False

    def set_compact(self, on):
        self.compact = on
        self.b_back.setVisible(on)
        self.input.setPlaceholderText("Message..." if on else "Write a message...")
        self.head.layout().setContentsMargins(8 if on else 22, 10, 10 if on else 16, 10)
        self.update_header()

    def _go_back(self):
        if self.store.conv_exists(self.conv or ""):
            self.store.mark_read(self.conv)
        self.back.emit()

    # -------------------------------------------------------------- buzz
    def buzz(self):
        if self.conv:
            self._stick_bottom = True
            self.ctx.buzz_conv(self.conv)

    # ------------------------------------------ send later / reminders
    def later_menu(self):
        from client.ui.planner_ui import when_menu
        m = QMenu(self)
        text = self.input.toPlainText().strip()
        if text:
            sub = when_menu(m, "Send this message later", self.schedule_text, "Pick a date & time...")
            sub.setIcon(icon("send", T.TEXT, 16))
            m.addMenu(sub)
        else:
            a = m.addAction(icon("send", T.FAINT, 16), "Send later — type a message first")
            a.setEnabled(False)
        m.addMenu(when_menu(m, "Remind me about this chat",
                            lambda ts: self.ctx.add_reminder(ts, "", self.conv)))
        m.addAction(icon("edit", T.TEXT, 16), "New reminder with a note...", lambda: self.ctx.new_reminder(self.conv))
        g = self.b_later.mapToGlobal(self.b_later.rect().topLeft())
        m.exec(QPoint(g.x(), g.y() - m.sizeHint().height() - 8))

    def schedule_text(self, due_at):
        from client.ui.planner_ui import fmt_due
        text = self.input.toPlainText().strip()
        if not text or not self.conv:
            return

        def done(reply):
            if reply.get("ok"):
                self.ctx.toast(f"🕒 Will be sent {fmt_due(due_at)}")
            else:
                self.ctx.toast(f"Not scheduled: {reply.get('error')}")
                if not self.input.toPlainText():
                    self.input.setPlainText(text)
        self.input.clear()
        self.ctx.conn.request("schedule_add", done, conv=self.conv, text=text, due_at=due_at)

    def _update_scheduled_bar(self):
        from client.ui.planner_ui import fmt_due
        mine = [x for x in self.store.scheduled if x["conv"] == self.conv]
        waiting = [x for x in mine if x["state"] == "pending"]
        failed = [x for x in mine if x["state"] == "failed"]
        if not mine:
            self.sched_bar.hide()
            return
        if failed:
            text = f"{len(failed)} scheduled message{'s' if len(failed) > 1 else ''} could not be sent — view"
        elif len(waiting) == 1:
            text = f"1 message scheduled for {fmt_due(waiting[0]['due_at'])} — view"
        else:
            text = f"{len(waiting)} messages scheduled, next {fmt_due(waiting[0]['due_at'])} — view"
        self.sched_bar.setText(" " + text)
        self.sched_bar.show()

    def scheduled_menu(self):
        from client.ui.planner_ui import TimeDialog, fmt_due
        m = QMenu(self)
        for x in [x for x in self.store.scheduled if x["conv"] == self.conv]:
            label = "Sticker" if x["sticker"] else x["text"].replace("\n", " ")[:50]
            if x["state"] == "failed":
                sub = m.addMenu(icon("close", T.DANGER, 16), f"Not sent: {label}")
                sub.addAction(x["error"] or "Could not be sent").setEnabled(False)
                continue
            sub = m.addMenu(icon("clock", T.TEXT, 16), f"{fmt_due(x['due_at'])}  ·  {label}")
            sub.addAction(icon("send", T.TEXT, 16), "Send now",
                          lambda sid=x["id"]: self.ctx.conn.request("schedule_send_now", None, id=sid))

            def edit(x=x):
                dlg = TimeDialog(self, "Edit scheduled message", x["due_at"], text=x["text"],
                                 text_label="Message", ok_text="Save")
                if x["sticker"]:
                    dlg.text.setEnabled(False)
                if dlg.exec():
                    self.ctx.conn.request("schedule_update", lambda r: None if r.get("ok") else self.ctx.toast(
                        r.get("error")), id=x["id"], text=dlg.message() if not x["sticker"] else "",
                        due_at=dlg.timestamp())
            sub.addAction(icon("edit", T.TEXT, 16), "Edit text or time...", edit)
            sub.addAction(icon("trash", T.DANGER, 16), "Delete",
                          lambda sid=x["id"]: self.ctx.conn.request("schedule_delete", None, id=sid))
        m.exec(self.sched_bar.mapToGlobal(self.sched_bar.rect().topLeft()) - QPoint(0, m.sizeHint().height() + 4))

    # --------------------------------------------------------- reactions
    def react_menu(self, msg, pos):
        m = ReactionPicker(self)
        m.picked.connect(lambda e: self.toggle_reaction(msg, e, only_add=True))
        m.exec(pos)

    def toggle_reaction(self, msg, emoji, only_add=False):
        mine = any(r["emoji"] == emoji and r.get("mine") for r in msg.get("reactions", []))
        if mine and only_add:
            return
        self.ctx.conn.request("react", lambda r: None if r.get("ok") else self.ctx.toast(r.get("error")),
                              message_id=msg["id"], emoji=emoji, on=not mine)

    # ------------------------------------------------------------- polls
    def create_poll(self):
        from client.ui.dialogs import PollDialog
        if not self.conv:
            return
        dlg = PollDialog(self)
        if not dlg.exec():
            return

        def done(reply):
            if reply.get("ok"):
                self.store.add_message(reply["message"])
            else:
                self.ctx.toast(f"Poll not created: {reply.get('error')}")
        self.ctx.conn.request("create_poll", done, conv=self.conv, **dlg.values())
        self._stick_bottom = True

    # ---------------------------------------------------------- stickers
    def open_stickers(self):
        if not self.conv:
            return
        if self.sticker_picker is None:
            self.sticker_picker = StickerPicker(self.ctx.config, self)
            self.sticker_picker.picked.connect(self.send_sticker)
        else:
            self.sticker_picker.show_pack(self.sticker_picker.first_pack())
        pk = self.sticker_picker
        g = self.b_sticker.mapToGlobal(self.b_sticker.rect().topRight())
        pk.move(g.x() - pk.width() + 20, g.y() - pk.height() - 10)
        pk.show()

    def send_sticker(self, sticker_id):
        if not self.conv:
            return
        if not self.ctx.conn.online:
            self.ctx.toast("Not connected — the sticker was not sent.")
            return
        from client import stickers
        stickers.remember(self.ctx.config, sticker_id)
        reply_to = self.reply_to["id"] if self.reply_to else None
        if self.reply_to:
            self.cancel_action()

        def done(reply):
            if reply.get("ok"):
                self.store.add_message(reply["message"])
            else:
                self.ctx.toast(f"Sticker not sent: {reply.get('error')}")
        self.ctx.conn.request("send", done, conv=self.conv, sticker=sticker_id, reply_to=reply_to)
        self._stick_bottom = True
        self.input.setFocus()

    def more_menu(self):
        m = QMenu(self)
        kind, target = P.parse_conv(self.conv)
        muted = self.store.is_muted(self.conv)
        m.addAction(icon("bell", T.TEXT, 16), "Unmute notifications" if muted else "Mute notifications",
                    lambda: self.ctx.set_muted(self.conv, not muted))
        m.addAction(icon("search", T.TEXT, 16), "Search messages", self.ctx.show_search)
        m.addAction(icon("attachment", T.TEXT, 16), "Send files...", self.pick_files)
        m.addAction(icon("folder", T.TEXT, 16), "Send a folder (zipped)...", self.pick_folder)
        if kind == "r":
            m.addAction(icon("users", T.TEXT, 16), "Room members", lambda: self.ctx.show_room_info(self.conv))
            if not self.store.rooms.get(target, {}).get("auto"):
                m.addSeparator()
                m.addAction(icon("logout", T.DANGER, 16), "Leave room", lambda: self.ctx.leave_room(target))
        else:
            m.addSeparator()
            m.addAction(icon("screen", T.TEXT, 16), "Share my screen...",
                        lambda: self.ctx.screens.invite(target, "offer"))
            m.addAction(icon("search", T.TEXT, 16), "Ask to see their screen...",
                        lambda: self.ctx.screens.invite(target, "request"))
            self.ctx.add_manage_actions(m, target)
        m.exec(self.b_more.mapToGlobal(self.b_more.rect().bottomLeft()))

    # ------------------------------------------------ reply / edit / delete
    def _show_action(self, icon_name, title, text):
        self.action_icon.setPixmap(pixmap(icon_name, T.ACCENT, 18))
        self.action_title.setText(title)
        self.action_label.setText(text.replace("\n", " ")[:110])
        self.action_bar.show()
        self._style_composer()

    def start_reply(self, msg):
        from client import stickers
        self.editing = None
        self.reply_to = msg
        self._show_action("reply", f"Replying to {self.store.user_name(msg['sender_id'])}", stickers.summary(msg))
        self.input.setFocus()

    def start_edit(self, msg):
        self.reply_to = None
        self.editing = msg
        self._show_action("edit", "Editing message", "Enter to save, Esc to cancel")
        self.input.setPlainText(msg.get("body", ""))
        self.input.moveCursor(QTextCursor.End)
        self.input.setFocus()

    def cancel_action(self):
        was_editing = self.editing is not None
        self.reply_to = None
        self.editing = None
        self.action_bar.hide()
        self._style_composer()
        if was_editing:
            self.input.clear()

    def _edit_last(self):
        for r in reversed(self.rows):
            m = getattr(r, "msg", None)
            if m and m["sender_id"] == self.store.my_id and not m.get("deleted") and m["kind"] in ("text", "file"):
                self.start_edit(m)
                return

    def delete_message(self, msg):
        mine = msg["sender_id"] == self.store.my_id
        text = ("Delete this message for everyone?" if mine else
                f"Delete this message from {self.store.user_name(msg['sender_id'])} for everyone? "
                "(recorded in the audit log)")
        if QMessageBox.question(self, "Delete message", rich_safe(text)) == QMessageBox.Yes:
            self.ctx.conn.request("delete_message", lambda r: None if r.get("ok") else self.ctx.toast(
                r.get("error", "Not deleted")), id=msg["id"])

    def _on_message_updated(self, msg):
        if msg["conv"] != self.conv:
            return
        for i, r in enumerate(self.rows):
            if isinstance(r, MessageRow) and r.msg["id"] == msg["id"]:
                if bool(r.msg.get("deleted")) != bool(msg.get("deleted")):
                    break                       # grouping around it may change: rebuild everything
                new = MessageRow(self.ctx, msg, r.mine, r.first, r.first, r.is_room)
                new.seen = r.seen
                new.update_meta()
                new.set_max_width(int(max(300, self.scroll.viewport().width() - 40) * 0.72))
                if self.hover_bar.row is r:
                    self.hover_bar.hide()
                    self.hover_bar.row = None
                self.mlay.insertWidget(self.mlay.indexOf(r), new)
                self.mlay.removeWidget(r)
                r.deleteLater()
                self.rows[i] = new
                return
        bar = self.scroll.verticalScrollBar()
        pos, at_bottom = bar.value(), self._at_bottom()
        self.render_all()
        if not at_bottom:
            QTimer.singleShot(0, lambda: bar.setValue(pos))

    def scroll_to(self, msg_id):
        for r in self.rows:
            if getattr(r, "msg", {}).get("id") == msg_id:
                self._stick_bottom = False
                self.scroll.ensureWidgetVisible(r, 0, 80)
                if isinstance(r, MessageRow):
                    r.flash()
                return
        self.ctx.toast("That message is further up — scroll up to load older messages.")

    # ------------------------------------------------------------- pins
    def _on_pins_changed(self, conv):
        if conv != self.conv:
            return
        pins = self.store.conversation(conv).pins
        if not pins:
            self.pin_bar.hide()
            return
        from client import stickers
        latest = pins[0]
        self.pin_title.setText("Pinned message" if len(pins) == 1 else f"Pinned message  ·  1 of {len(pins)}")
        self.pin_text.setText(f"{self.store.user_name(latest['sender_id'])}: "
                              + stickers.summary(latest).replace("\n", " ")[:120])
        self.pin_bar.show()

    def _pin_clicked(self, e):
        pins = self.store.conversation(self.conv).pins if self.conv else []
        if e.button() == Qt.LeftButton and pins:
            self.scroll_to(pins[0]["id"])

    def pins_menu(self):
        pins = self.store.conversation(self.conv).pins
        m = QMenu(self)
        from client import stickers
        for p in pins:
            label = f"{self.store.user_name(p['sender_id'])}: {stickers.summary(p)[:60]}"
            sub = m.addMenu(label)
            sub.addAction("Show in chat", lambda mid=p["id"]: self.scroll_to(mid))
            sub.addAction("Unpin", lambda mid=p["id"]: self.ctx.conn.request(
                "pin", None, conv=self.conv, message_id=mid, pinned=False))
        m.exec(self.pin_list.mapToGlobal(self.pin_list.rect().bottomLeft()))

    # ---------------------------------------------------------- mentions
    def _update_mention_popup(self):
        if not self.conv or not self.conv.startswith("r:"):
            self.mention_popup.hide()
            return
        q = self.input.current_mention()
        if q is None:
            self.mention_popup.hide()
            return
        from client import mentions
        room = self.store.rooms.get(P.parse_conv(self.conv)[1], {})
        members = [self.store.me if uid == self.store.my_id else self.store.users.get(uid)
                   for uid in room.get("members", [])]
        self.mention_popup.show_for(mentions.suggestions(q, [u for u in members if u], self.store.my_id),
                                    self.composer)

    def _pick_mention(self, username):
        self.input.complete_mention(username)
        self.mention_popup.hide()
        self.input.setFocus()

    # ----------------------------------------------------------- seen by
    def _my_last_row(self):
        """My message if it is the latest visible one (deleted messages are skipped)."""
        for r in reversed(self.rows):
            if isinstance(r, MessageRow) and not r.msg.get("deleted"):
                return r if r.mine else None
        return None

    def _update_seen(self):
        if not self.conv or not self.conv.startswith("r:") or not self.isVisible():
            self.seen_timer.stop()
            return
        row = self._my_last_row()
        for r in self.rows:
            if isinstance(r, MessageRow) and r.seen and r is not row:
                r.set_seen("")
        if not row:
            return
        self.seen_timer.start()

        def done(reply):
            if reply.get("ok") and row in self.rows:
                n, total = len(reply["read"]), reply["total"]
                row.set_seen("Seen by everyone" if n and n == total else f"Seen by {n} of {total}")
        self.ctx.conn.request("read_by", done, conv=self.conv, message_id=row.msg["id"])

    def show_seen_by(self, msg):
        def done(reply):
            if not reply.get("ok"):
                self.ctx.toast(reply.get("error", "Not available"))
                return
            names = reply.get("names", {})
            read = sorted(names[str(u)] for u in reply["read"] if str(u) in names)
            unread = sorted(n for u, n in names.items() if int(u) not in reply["read"])
            QMessageBox.information(self, "Seen by", rich_safe(
                f"Seen by {len(read)} of {reply['total']}:\n" + ("\n".join(read) or "nobody yet")
                + ("\n\nNot yet:\n" + "\n".join(unread) if unread else "")))
        self.ctx.conn.request("read_by", done, conv=self.conv, message_id=msg["id"])

    # ------------------------------------------------------ drag & drop
    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls() and any(u.isLocalFile() for u in e.mimeData().urls()):
            e.acceptProposedAction()
            self.drop_overlay.setGeometry(self.rect().adjusted(12, 12, -12, -12))
            self.drop_overlay.raise_()
            self.drop_overlay.show()

    def dragLeaveEvent(self, e):
        self.drop_overlay.hide()

    def dropEvent(self, e):
        self.drop_overlay.hide()
        paths = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
        if paths:
            e.acceptProposedAction()
            self.send_files(paths)
