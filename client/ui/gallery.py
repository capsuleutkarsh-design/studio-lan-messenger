"""Pictures in a chat: several images sent one after another show as one gallery, and a click opens a viewer
where the arrow keys move through every picture of the chat."""

import datetime
import os

from PySide6.QtCore import QRectF, QSize, QSizeF, Qt, Signal
from PySide6.QtGui import QColor, QGuiApplication, QImageReader, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (
    QDialog, QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton, QScrollArea, QSizePolicy, QVBoxLayout, QWidget,
)

from common import theme as T
from client.ui.widgets import Avatar, IconButton, open_file, plain

IMAGE_EXT = (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp")
PREVIEW_MAX_BYTES = 15 * 1024 * 1024
GALLERY_MAX = 12            # a longer burst starts a new gallery


def is_previewable(file_info):
    return (os.path.splitext(file_info.get("name", ""))[1].lower() in IMAGE_EXT
            and 0 < file_info.get("size", 0) <= PREVIEW_MAX_BYTES and not file_info.get("purged"))


def is_plain_image(msg):
    """Just a picture: no caption, quote, reactions, thread or edits - these can share a gallery."""
    f = msg.get("file")
    return bool(f and is_previewable(f) and msg.get("kind") == "file" and not msg.get("body")
                and not msg.get("reply") and not msg.get("reactions") and not msg.get("forwarded")
                and not msg.get("deleted") and not msg.get("edited") and not msg.get("thread_count")
                and not msg.get("thread_root"))


def square(path, size):
    """A square, centre-cropped, rounded thumbnail decoded at that size."""
    reader = QImageReader(path)
    reader.setAutoTransform(True)
    s = reader.size()
    if s.isValid() and s.width() and s.height():
        k = size / min(s.width(), s.height())
        reader.setScaledSize(QSize(max(size, round(s.width() * k)), max(size, round(s.height() * k))))
    img = reader.read()
    if img.isNull():
        return None
    x, y = (img.width() - size) // 2, (img.height() - size) // 2
    src = QPixmap.fromImage(img.copy(max(0, x), max(0, y), size, size))
    out = QPixmap(size, size)
    out.fill(Qt.transparent)
    p = QPainter(out)
    p.setRenderHint(QPainter.Antialiasing)
    clip = QPainterPath()
    clip.addRoundedRect(QRectF(0, 0, size, size), 10, 10)
    p.setClipPath(clip)
    p.drawPixmap(0, 0, src)
    p.end()
    return out


class Thumb(QLabel):
    """One picture of a gallery. Right-click: the same menu as a message (react, reply, forward, delete...)."""
    opened = Signal(dict)

    def __init__(self, ctx, msg, mine, is_room, size):
        super().__init__()
        self.ctx, self.msg, self.mine, self.is_room = ctx, msg, mine, is_room
        self.in_thread, self.sticker, self.text = False, False, None       # what MessageRow.build_menu reads
        self.size_px = size
        self.path = None
        self.setFixedSize(size, size)
        self.setAlignment(Qt.AlignCenter)
        self.setCursor(Qt.PointingHandCursor)
        self.setStyleSheet(f"background: {T.TINT}; border-radius: 10px; color: {T.FAINT}; font-size: 8pt;")
        self.setToolTip(msg["file"]["name"])
        self.setText("...")
        ctx.previews.ready.connect(self._ready)
        ctx.previews.failed.connect(self._failed)
        path = ctx.previews.request(msg["file"])
        if path:
            self._show(path)

    def _ready(self, file_id, path):
        if file_id == self.msg["file"]["id"]:
            self._show(path)

    def _failed(self, file_id):
        if file_id == self.msg["file"]["id"] and not self.path:
            self.setText("No preview")

    def _show(self, path):
        pm = square(path, self.size_px)
        if pm is None:
            self.setText("No preview")
            return
        self.path = path
        self.setStyleSheet("background: transparent;")
        self.setPixmap(pm)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.opened.emit(self.msg)

    def contextMenuEvent(self, e):
        from client.ui.chat_view import MessageRow
        m = MessageRow.build_menu(self)
        if m:
            m.exec(e.globalPos())


class GalleryRow(QWidget):
    """Pictures one person sent one after another, as one bubble with a grid of squares."""

    def __init__(self, ctx, msgs, mine, show_name, is_room):
        super().__init__()
        self.ctx, self.mine, self.is_room, self.first = ctx, mine, is_room, show_name
        self.msgs = []
        self.thumbs = []
        self.seen = ""
        self.cols = 3
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 8 if show_name else 1, 0, 1)
        outer.setSpacing(10)
        self.bubble = QFrame()
        self.bubble.setObjectName("bubble")
        bg = T.BUBBLE_ME if mine else T.BUBBLE_OTHER
        border = "" if T.DARK or mine else f"border: 1px solid {T.HAIR};"
        self.bubble.setStyleSheet(f"#bubble {{ background: {bg}; {border} border-radius: 18px; }}")
        self.bubble.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Preferred)
        b = QVBoxLayout(self.bubble)
        b.setContentsMargins(8, 8, 8, 6)
        b.setSpacing(6)
        sender = ctx.store.user_name(msgs[0]["sender_id"])
        if show_name and is_room and not mine:
            name = plain(QLabel(sender))
            name.setStyleSheet(f"color: {T.avatar_color(sender)}; font-weight: 700; font-size: 9pt; padding-left: 4px;")
            b.addWidget(name)
        self.grid = QGridLayout()
        self.grid.setSpacing(4)
        b.addLayout(self.grid)
        foot = QHBoxLayout()
        foot.setContentsMargins(4, 0, 4, 0)
        self.count = QPushButton()
        T.polish(self.count, flat=True)
        self.count.setCursor(Qt.PointingHandCursor)
        self.count.setToolTip("Download every picture of this gallery into your download folder")
        self.count.setStyleSheet(f"QPushButton {{ color: {T.ACCENT}; font-size: 8.5pt; font-weight: 700;"
                                 " background: transparent; border: none; padding: 0; }")
        self.count.clicked.connect(self.download_all)
        foot.addWidget(self.count)
        foot.addStretch(1)
        self.meta = QLabel()
        self.meta.setStyleSheet(f"color: {T.FAINT}; font-size: 8pt;")
        foot.addWidget(self.meta)
        b.addLayout(foot)
        if mine:
            outer.addStretch(1)
            outer.addWidget(self.bubble)
        else:
            if is_room:
                av = Avatar(34)
                if show_name:
                    av.set(sender, sender, ring=T.BG, uid=msgs[0]["sender_id"])
                else:
                    av.setFixedSize(34, 0)
                outer.addWidget(av, 0, Qt.AlignTop)
            outer.addWidget(self.bubble)
            outer.addStretch(1)
        for m in msgs:
            self.add(m)

    @property
    def msg(self):
        return self.msgs[-1]

    def has(self, msg_id):
        return any(m["id"] == msg_id for m in self.msgs)

    def add(self, msg):
        self.msgs.append(msg)
        t = Thumb(self.ctx, msg, self.mine, self.is_room, 132)
        t.opened.connect(lambda m: self.ctx.chat.open_viewer(m))
        self.thumbs.append(t)
        self._place()
        n = len(self.msgs)
        self.count.setText(f"{n} pictures  ·  Download all" if n > 1 else "Download")
        self.update_meta()

    def _place(self):
        for t in self.thumbs:
            self.grid.removeWidget(t)
        cols = min(self.cols, len(self.thumbs)) or 1
        for i, t in enumerate(self.thumbs):
            self.grid.addWidget(t, i // cols, i % cols)

    def set_max_width(self, w):
        cols = max(1, min(3, (w - 16) // 136))
        if cols != self.cols:
            self.cols = cols
            self._place()

    def update_meta(self):
        t = datetime.datetime.fromtimestamp(self.msg["ts"]).strftime("%H:%M")
        self.meta.setText(f"{self.seen}  ·  {t}" if self.seen else t)

    def set_seen(self, text):
        self.seen = text
        self.update_meta()

    def flash(self):
        pass

    def download_all(self):
        for m in self.msgs:
            if not self.ctx.config.downloaded_path(m["file"]["id"]):
                self.ctx.download_file(m["file"], m["conv"])
        self.ctx.toast(f"Downloading {len(self.msgs)} picture{'s' if len(self.msgs) > 1 else ''} "
                       "to your download folder")


class _ViewCanvas(QWidget):
    """Paints the picture as large as fits, centred (or a line of text while it loads). Compare mode: a second
    picture under a wipe line you drag across, or both side by side."""

    def __init__(self):
        super().__init__()
        self.image = None
        self.other = None                 # the version it is compared with
        self.mode = ""                    # "" | "wipe" | "side"
        self.split = 0.5
        self.labels = ("", "")
        self.text = ""
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMinimumSize(10, 10)

    def show_image(self, image, text=""):
        self.image, self.text = image, text
        self.update()

    def _fit(self, pm, area):
        size = QSizeF(pm.size())
        if size.width() > area.width() or size.height() > area.height():
            size = size.scaled(area.size(), Qt.KeepAspectRatio)
        return QRectF(area.x() + (area.width() - size.width()) / 2, area.y() + (area.height() - size.height()) / 2,
                      size.width(), size.height())

    def paintEvent(self, _):
        p = QPainter(self)
        if self.image is None or self.image.isNull():
            p.setPen(QColor("#9aa2b5"))
            p.drawText(self.rect(), Qt.AlignCenter, self.text)
            return
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        p.setRenderHint(QPainter.Antialiasing)
        other = self.other if self.mode and self.other is not None and not self.other.isNull() else None
        if other is not None and self.mode == "side":
            half = self.width() / 2
            for i, pm in enumerate((self.image, other)):
                r = self._fit(pm, QRectF(i * half + 6, 26, half - 12, self.height() - 32))
                p.drawPixmap(r, pm, QRectF(pm.rect()))
            self._labels(p, 6, half + 6)
            return
        r = self._fit(self.image, QRectF(self.rect()))
        p.drawPixmap(r, self.image, QRectF(self.image.rect()))
        if other is not None:                      # wipe: the other version right of the line
            x = r.x() + r.width() * self.split
            p.save()
            p.setClipRect(QRectF(x, r.y(), r.right() - x, r.height()))
            p.drawPixmap(r, other, QRectF(other.rect()))
            p.restore()
            p.setPen(QPen(QColor("#ffffff"), 2))
            p.drawLine(int(x), int(r.y()), int(x), int(r.bottom()))
            p.setBrush(QColor(T.ACCENT))
            p.drawEllipse(QRectF(x - 13, r.center().y() - 13, 26, 26))
            p.setPen(QColor("#ffffff"))
            p.drawText(QRectF(x - 13, r.center().y() - 13, 26, 26), Qt.AlignCenter, "⇆")
            self._labels(p, r.x() + 8, r.right() - 8, right_aligned=True, top=r.y() + 8)

    def _labels(self, p, x1, x2, right_aligned=False, top=4):
        f = p.font()
        f.setBold(True)
        p.setFont(f)
        for text, x, align in ((self.labels[0], x1, Qt.AlignLeft), (self.labels[1], x2, Qt.AlignRight
                                                                    if right_aligned else Qt.AlignLeft)):
            if not text:
                continue
            w = p.fontMetrics().horizontalAdvance(text) + 16
            box = QRectF(x - (w if align == Qt.AlignRight else 0), top, w, 22)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(0, 0, 0, 160))
            p.drawRoundedRect(box, 8, 8)
            p.setPen(QColor("#ffffff"))
            p.drawText(box, Qt.AlignCenter, text)

    def mousePressEvent(self, e):
        self.mouseMoveEvent(e)

    def mouseMoveEvent(self, e):
        if self.mode == "wipe" and self.image is not None and e.buttons() & Qt.LeftButton:
            r = self._fit(self.image, QRectF(self.rect()))
            if r.width():
                self.split = max(0.0, min(1.0, (e.position().x() - r.x()) / r.width()))
                self.update()


class ImageViewer(QDialog):
    """Full-window picture viewer: ← → (or the strip at the bottom) move through every picture in the chat."""

    def __init__(self, ctx, msgs, current):
        super().__init__(ctx)
        self.ctx = ctx
        self.msgs = msgs
        self.i = next((k for k, m in enumerate(msgs) if m["id"] == current["id"]), 0)
        self.image = None
        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setStyleSheet("QDialog { background: #06080d; }")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        top = QWidget()
        top.setFixedHeight(54)
        top.setStyleSheet("background: rgba(0,0,0,0.35);")
        tl = QHBoxLayout(top)
        tl.setContentsMargins(18, 8, 10, 8)
        self.caption = QLabel()
        self.caption.setStyleSheet("color: #e8eaf0; font-size: 10pt; background: transparent;")
        tl.addWidget(self.caption, 1)
        self.counter = QLabel()
        self.counter.setStyleSheet("color: #9aa2b5; font-size: 10pt; font-weight: 700; background: transparent;"
                                   " padding: 0 12px;")
        tl.addWidget(self.counter)
        for name, tip, fn in (("compare", "Compare with the next picture: drag the line (C)", self.toggle_compare),
                              ("pen", "Draw on it and send it to the chat", self._draw),
                              ("open", "Open in its program", self._open), ("download", "Save as...", self._save),
                              ("copy", "Copy the picture", self._copy), ("close", "Close (Esc)", self.close)):
            b = IconButton(name, tip, 36, 18, "#c9cedb", "#ffffff")
            b.clicked.connect(fn)
            tl.addWidget(b)
            if name == "compare":
                self.b_compare = b
        self.side = QPushButton("Side by side")
        self.side.setCheckable(True)
        self.side.setStyleSheet("QPushButton { background: rgba(255,255,255,0.08); color: #e8eaf0; border: none;"
                                " border-radius: 10px; padding: 6px 12px; } QPushButton:checked { background: "
                                f"{T.ACCENT}; color: {T.ACCENT_TEXT}; }}")
        self.side.toggled.connect(self._side)
        self.side.hide()
        tl.insertWidget(2, self.side)
        self.cmp_index = None
        lay.addWidget(top)

        mid = QHBoxLayout()
        mid.setContentsMargins(10, 0, 10, 0)
        self.prev = IconButton("back", "Previous (←)", 48, 24, "#c9cedb", "#ffffff")
        self.prev.clicked.connect(lambda: self.go(self.i - 1))
        self.next = IconButton("next", "Next (→)", 48, 24, "#c9cedb", "#ffffff")
        self.next.clicked.connect(lambda: self.go(self.i + 1))
        self.view = _ViewCanvas()
        mid.addWidget(self.prev)
        mid.addWidget(self.view, 1)
        mid.addWidget(self.next)
        lay.addLayout(mid, 1)

        self.strip_area = QScrollArea()
        self.strip_area.setFixedHeight(76)
        self.strip_area.setWidgetResizable(True)
        self.strip_area.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.strip_area.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        strip = QWidget()
        strip.setStyleSheet("background: transparent;")
        sl = QHBoxLayout(strip)
        sl.setContentsMargins(12, 6, 12, 10)
        sl.setSpacing(6)
        sl.addStretch(1)
        self.strip = []
        for k, m in enumerate(msgs):
            t = QLabel()
            t.setFixedSize(58, 58)
            t.setCursor(Qt.PointingHandCursor)
            t.setAlignment(Qt.AlignCenter)
            t.mousePressEvent = lambda e, k=k: self.go(k)
            sl.addWidget(t)
            self.strip.append(t)
        sl.addStretch(1)
        self.strip_area.setWidget(strip)
        self.strip_area.setVisible(len(msgs) > 1)
        lay.addWidget(self.strip_area)

        ctx.previews.ready.connect(self._ready)
        win = ctx.frameGeometry() if ctx.isVisible() else QGuiApplication.primaryScreen().availableGeometry()
        self.setGeometry(win)
        self._fill_strip()
        self.go(self.i)

    # ------------------------------------------------------------------ pictures
    def _path(self, m):
        return self.ctx.previews.request(m["file"])

    def _fill_strip(self):
        for k, m in enumerate(self.msgs):
            path = self.ctx.previews.path_for(m["file"])
            pm = square(path, 54) if os.path.exists(path) else None
            if pm:
                self.strip[k].setPixmap(pm)
            self._mark(k)

    def _mark(self, k):
        on = k == self.i
        other = k == getattr(self, "cmp_index", None)
        color = T.ACCENT if on else ("#ffcc33" if other else "transparent")
        self.strip[k].setStyleSheet(f"border: 2px solid {color}; border-radius: 12px;"
                                    " background: rgba(255,255,255,0.06);")

    def _ready(self, file_id, _path):
        for k, m in enumerate(self.msgs):
            if m["file"]["id"] == file_id:
                self._fill_strip()
                if k == self.i:
                    self.go(self.i)
                elif k == self.cmp_index:
                    self._update_compare()

    def go(self, k):
        if not self.msgs:
            return
        old, self.i = self.i, max(0, min(len(self.msgs) - 1, k))
        self._mark(old)
        self._mark(self.i)
        self.strip_area.ensureWidgetVisible(self.strip[self.i])
        m = self.msgs[self.i]
        store = self.ctx.store
        when = datetime.datetime.fromtimestamp(m["ts"]).strftime("%d %b %H:%M")
        self.caption.setText(f"{store.user_name(m['sender_id'])}  ·  {when}  ·  {m['file']['name']}")
        self.counter.setText(f"{self.i + 1} / {len(self.msgs)}" if len(self.msgs) > 1 else "")
        self.prev.setEnabled(self.i > 0)
        self.next.setEnabled(self.i < len(self.msgs) - 1)
        self.image = self._load(m)
        if self.cmp_index is not None:
            self._update_compare()
        self._fit()

    def _load(self, m):
        path = self._path(m)
        if not path:
            return None
        reader = QImageReader(path)
        reader.setAutoTransform(True)
        screen = self.screen().availableGeometry().size() if self.screen() else QSize(2560, 1440)
        s = reader.size()
        if s.isValid() and (s.width() > screen.width() or s.height() > screen.height()):
            reader.setScaledSize(s.scaled(screen, Qt.KeepAspectRatio))
        img = reader.read()
        return None if img.isNull() else QPixmap.fromImage(img)

    # ------------------------------------------------------------------ compare two versions
    def toggle_compare(self):
        if len(self.msgs) < 2:
            self.ctx.toast("Compare needs a second picture in this chat")
            return
        if self.cmp_index is None:
            self.cmp_index = self.i + 1 if self.i + 1 < len(self.msgs) else self.i - 1
            self.side.show()
            self.view.mode = "side" if self.side.isChecked() else "wipe"
        else:
            self.cmp_index = None
            self.side.hide()
            self.view.mode = ""
        self._update_compare()

    def _side(self, on):
        if self.cmp_index is not None:
            self.view.mode = "side" if on else "wipe"
            self.view.update()

    def _update_compare(self):
        if self.cmp_index is None:
            self.view.other = None
            self.view.labels = ("", "")
        else:
            if self.cmp_index == self.i:          # the arrows moved onto the other one: compare with the next
                self.cmp_index = self.i + 1 if self.i + 1 < len(self.msgs) else self.i - 1
            self.view.other = self._load(self.msgs[self.cmp_index])
            name = lambda m: os.path.splitext(m["file"]["name"])[0]  # noqa: E731
            self.view.labels = (name(self.msgs[self.i]), name(self.msgs[self.cmp_index]))
        for k in range(len(self.strip)):
            self._mark(k)
        self.view.update()

    # ------------------------------------------------------------------ draw on it
    def _draw(self):
        from client.ui.annotate import AnnotateDialog
        m = self.msgs[self.i]
        path = self._path(m)
        if not path:
            return
        from PySide6.QtGui import QImage
        img = QImage(path)
        if img.isNull():
            return
        conv = m["conv"]
        dlg = AnnotateDialog(self, img, self.ctx.store.title(conv))
        if dlg.exec():
            out = dlg.save("Notes on " + os.path.splitext(m["file"]["name"])[0])
            if out:
                self.ctx.send_file(conv, out, dlg.caption.text().strip())
                self.ctx.toast("Sent to " + self.ctx.store.title(conv))

    def _fit(self):
        self.view.show_image(self.image, "Loading..." if self.ctx.conn.online else "Not available offline")

    def keyPressEvent(self, e):
        key = e.key()
        if key in (Qt.Key_Left, Qt.Key_Up, Qt.Key_PageUp):
            self.go(self.i - 1)
        elif key in (Qt.Key_Right, Qt.Key_Down, Qt.Key_PageDown, Qt.Key_Space):
            self.go(self.i + 1)
        elif key == Qt.Key_Home:
            self.go(0)
        elif key == Qt.Key_End:
            self.go(len(self.msgs) - 1)
        elif key == Qt.Key_C:
            self.toggle_compare()
        elif key == Qt.Key_Escape:
            self.close()
        else:
            super().keyPressEvent(e)

    def wheelEvent(self, e):
        self.go(self.i + (1 if e.angleDelta().y() < 0 else -1))

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton and not self.view.geometry().contains(e.position().toPoint()) \
                and not self.strip_area.geometry().contains(e.position().toPoint()):
            self.close()                             # a click on the dark area closes, like most viewers

    # ------------------------------------------------------------------ actions
    def _open(self):
        m = self.msgs[self.i]
        path = self.ctx.config.downloaded_path(m["file"]["id"]) or self._path(m)
        if path:
            open_file(path)

    def _save(self):
        m = self.msgs[self.i]
        self.ctx.download_file_as(m["file"], m["conv"])

    def _copy(self):
        if self.image is not None:
            QGuiApplication.clipboard().setPixmap(self.image)
            self.ctx.toast("Picture copied")

    def paintEvent(self, e):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#06080d"))
