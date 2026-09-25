"""Full-page views: home, announcements and file transfers."""

import datetime

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QProgressBar, QPushButton, QScrollArea, QVBoxLayout, QWidget,
)

from common import protocol as P
from common import theme as T
from common.icons import icon, pixmap
from client.ui.widgets import IconButton, brand, esc, linkify, open_link, open_path, plain, show_in_folder


class PageHeader(QFrame):
    def __init__(self, title, subtitle=""):
        super().__init__()
        self.setFixedHeight(68)
        self.setStyleSheet(f"PageHeader {{ background: {T.BG}; border-bottom: 1px solid {T.BORDER}; }}")
        self.lay = QHBoxLayout(self)
        self.lay.setContentsMargins(24, 10, 18, 10)
        col = QVBoxLayout()
        col.setSpacing(0)
        t = QLabel(title)
        t.setStyleSheet("font-size: 13pt; font-weight: 700;")
        col.addStretch(1)
        col.addWidget(t)
        if subtitle:
            s = QLabel(subtitle)
            s.setStyleSheet(f"color: {T.MUTED}; font-size: 9pt;")
            col.addWidget(s)
        col.addStretch(1)
        self.lay.addLayout(col, 1)


def scroll_column():
    area = QScrollArea()
    area.setWidgetResizable(True)
    area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    w = QWidget()
    T.bg_pane(w)
    lay = QVBoxLayout(w)
    lay.setContentsMargins(24, 16, 24, 16)
    lay.setSpacing(10)
    lay.addStretch(1)
    area.setWidget(w)
    return area, lay


class HomePage(QWidget):
    def __init__(self):
        super().__init__()
        T.bg_pane(self)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(40, 40, 40, 40)
        lay.addStretch(2)
        lay.addWidget(brand(84, 22), 0, Qt.AlignHCenter)
        lay.addSpacing(20)
        self.title = plain(QLabel("Welcome"))
        self.title.setAlignment(Qt.AlignCenter)
        self.title.setStyleSheet("font-size: 20pt; font-weight: 800;")
        lay.addWidget(self.title)
        self.sub = plain(QLabel("Pick a person or a room on the left to start chatting."))
        self.sub.setAlignment(Qt.AlignCenter)
        self.sub.setStyleSheet(f"color: {T.MUTED}; font-size: 10.5pt;")
        lay.addWidget(self.sub)
        lay.addSpacing(28)
        cards = QHBoxLayout()
        cards.setSpacing(14)
        cards.addStretch(1)
        for ic, title, text in (
                ("users", "Talk to anyone", "Everyone in the studio is under People, grouped by department."),
                ("attachment", "Share files & folders", "Drag & drop into a chat — even to people who are offline."),
                ("sticker", "Stickers", "Hundreds of desi, festival and mood stickers next to the emoji button.")):
            cards.addWidget(self._card(ic, title, text))
        cards.addStretch(1)
        lay.addLayout(cards)
        lay.addSpacing(22)
        keys = QLabel(f"<span style='color:{T.FAINT}'>Shortcuts:</span>&nbsp; "
                      f"{self._key('Ctrl')} {self._key('K')} <span style='color:{T.MUTED}'>find a person or room</span>"
                      f"&nbsp;&nbsp;&nbsp;{self._key('Ctrl')} {self._key('F')} "
                      f"<span style='color:{T.MUTED}'>search messages</span>"
                      f"&nbsp;&nbsp;&nbsp;{self._key('↑')} <span style='color:{T.MUTED}'>edit your last message</span>")
        keys.setAlignment(Qt.AlignCenter)
        keys.setStyleSheet("font-size: 9pt;")
        lay.addWidget(keys)
        lay.addStretch(3)

    @staticmethod
    def _key(k):
        return (f"<span style='background:{T.SURFACE}; color:{T.TEXT}; font-weight:600;'>&nbsp;{k}&nbsp;</span>")

    @staticmethod
    def _card(ic, title, text):
        card = QFrame()
        card.setObjectName("homecard")
        card.setFixedWidth(230)
        card.setStyleSheet(f"#homecard {{ background: {T.PANEL}; border: 1px solid {T.BORDER}; border-radius: 16px; }}")
        cl = QVBoxLayout(card)
        cl.setContentsMargins(18, 18, 18, 18)
        cl.setSpacing(8)
        tile = QLabel()
        tile.setFixedSize(40, 40)
        tile.setAlignment(Qt.AlignCenter)
        tile.setStyleSheet(f"background: {T.ACCENT_SOFT}; border-radius: 11px;")
        tile.setPixmap(pixmap(ic, T.ACCENT, 20))
        cl.addWidget(tile)
        t = QLabel(title)
        t.setStyleSheet("font-weight: 700; font-size: 10.5pt;")
        cl.addWidget(t)
        d = QLabel(text)
        d.setWordWrap(True)
        d.setStyleSheet(f"color: {T.MUTED}; font-size: 9pt;")
        cl.addWidget(d)
        cl.addStretch(1)
        return card

    def set_name(self, name, server):
        self.title.setText(f"Welcome back, {name.split()[0] if name else ''}")
        self.sub.setText(f"Connected to {server}. Pick a person or a room on the left to start chatting.")


class AnnouncementCard(QFrame):
    show_reads = Signal(dict)

    def __init__(self, store, ann):
        super().__init__()
        unread = not ann.get("read")
        border = T.ACCENT if unread else T.BORDER
        self.setStyleSheet(f"AnnouncementCard {{ background: {T.PANEL}; border-radius: 14px;"
                           f" border-left: 4px solid {border}; }}")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 14, 18, 14)
        head = QHBoxLayout()
        title = plain(QLabel(ann["title"]))
        title.setStyleSheet("font-size: 11.5pt; font-weight: 700;")
        title.setWordWrap(True)
        head.addWidget(title, 1)
        when = datetime.datetime.fromtimestamp(ann["ts"]).strftime("%d %b %Y, %H:%M")
        w = QLabel(when)
        w.setStyleSheet(f"color: {T.FAINT}; font-size: 8.5pt;")
        head.addWidget(w, 0, Qt.AlignTop)
        lay.addLayout(head)
        target = ann.get("target_label") or ann.get("department") or ""
        to = f" → {target}" if target and target != "Everyone" else ""
        sender = plain(QLabel(f"{store.user_name(ann['sender_id'])}{to}"))
        sender.setStyleSheet(f"color: {T.ACCENT if unread else T.MUTED}; font-size: 9pt;")
        lay.addWidget(sender)
        body = QLabel(linkify(ann["body"]))
        body.setWordWrap(True)
        body.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.LinksAccessibleByMouse)
        body.linkActivated.connect(open_link)
        body.setStyleSheet("padding-top: 6px;")
        lay.addWidget(body)
        if ann["sender_id"] == store.my_id or store.me.get("is_admin"):
            reads = QPushButton(" Who has read this?")
            reads.setIcon(icon("users", T.TEXT, 14))
            reads.setCursor(Qt.PointingHandCursor)
            reads.clicked.connect(lambda: self.show_reads.emit(ann))
            lay.addWidget(reads, 0, Qt.AlignLeft)


class AnnouncementsPage(QWidget):
    compose = Signal()
    show_reads = Signal(dict)

    def __init__(self, store):
        super().__init__()
        self.store = store
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.header = PageHeader("Announcements", "Studio-wide messages")
        self.b_new = QPushButton(" New announcement")
        self.b_new.setIcon(icon("megaphone", T.ACCENT_TEXT, 16))
        T.polish(self.b_new, primary=True)
        self.b_new.clicked.connect(self.compose.emit)
        self.header.lay.addWidget(self.b_new)
        lay.addWidget(self.header)
        self.area, self.col = scroll_column()
        lay.addWidget(self.area, 1)
        store.announcements_changed.connect(self.rebuild)
        store.me_changed.connect(self.rebuild)

    def rebuild(self):
        me = self.store.me
        from client.ui.dialogs import announce_targets
        self.b_new.setVisible(bool(announce_targets(self.store)))
        while self.col.count() > 1:
            w = self.col.takeAt(0).widget()
            if w:
                w.deleteLater()
        if not self.store.announcements:
            empty = QLabel("No announcements yet.")
            empty.setAlignment(Qt.AlignCenter)
            empty.setStyleSheet(f"color: {T.FAINT}; padding: 40px;")
            self.col.insertWidget(0, empty)
        for i, a in enumerate(self.store.announcements):
            card = AnnouncementCard(self.store, a)
            card.show_reads.connect(self.show_reads.emit)
            self.col.insertWidget(i, card)

    def mark_all_read(self):
        for a in list(self.store.announcements):
            if not a.get("read"):
                self.store.mark_announcement_read(a["id"])


class DirectoryPage(QWidget):
    """Studio directory: people as cards, an org chart of the reporting lines, or a list."""

    def __init__(self, ctx):
        super().__init__()
        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import QMenu
        from common import orgviews
        from client.ui.widgets import paint_avatar
        orgviews.avatar_painter = paint_avatar          # profile photos in the cards and the chart
        self.ctx = ctx
        self.store = ctx.store
        self._QMenu = QMenu
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(PageHeader("Organisation", "Everyone you can reach — by department, or who reports to whom"))
        body = QWidget()
        T.bg_pane(body)
        bl = QVBoxLayout(body)
        bl.setContentsMargins(24, 14, 24, 16)
        self.browser = orgviews.OrgBrowser(view=ctx.config["directory_view"])
        self.browser.open_person.connect(self._open)
        self.browser.person_menu.connect(self._menu)
        self.browser.view_changed.connect(self._remember_view)
        bl.addWidget(self.browser, 1)
        lay.addWidget(body, 1)
        self._timer = QTimer(self, singleShot=True, interval=300,
                             timeout=lambda: self.rebuild() if self.isVisible() else None)
        for sig in (self.store.users_changed, self.store.user_updated, self.store.me_changed):
            sig.connect(lambda *_: self._timer.start() if not self._timer.isActive() else None)
        ctx.avatars.changed.connect(lambda _uid: self.browser.update_views())

    def rebuild(self):
        self.browser.set_people(self.store.all_people(), me_id=self.store.my_id)

    def showEvent(self, e):
        super().showEvent(e)
        self.rebuild()

    def _remember_view(self, view):
        self.ctx.config["directory_view"] = view
        self.ctx.config.save()

    def _open(self, uid):
        if uid and uid != self.store.my_id:
            self.ctx.open_conv(P.direct_conv(uid))

    def _menu(self, uid, pos):
        if not uid or uid == self.store.my_id:
            return
        m = self._QMenu(self)
        conv = P.direct_conv(uid)
        m.addAction(icon("chat", T.TEXT, 16), "Send message", lambda: self.ctx.open_conv(conv))
        m.addAction(icon("attachment", T.TEXT, 16), "Send files...", lambda: self.ctx._send_files_to(conv))
        m.addAction(icon("screen", T.TEXT, 16), "Ask to see their screen...",
                    lambda: self.ctx.screens.invite(uid, "request"))
        self.ctx.add_manage_actions(m, uid)
        m.exec(pos)


class TransferRow(QFrame):
    def __init__(self, manager, t, store):
        super().__init__()
        self.manager = manager
        self.t = t
        self.setStyleSheet(f"TransferRow {{ background: {T.PANEL}; border-radius: 12px; }}")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 10, 10, 10)
        lay.setSpacing(12)
        ic = QLabel()
        ic.setFixedSize(38, 38)
        ic.setAlignment(Qt.AlignCenter)
        ic.setStyleSheet(f"background: {T.SURFACE}; border-radius: 10px;")
        ic.setPixmap(pixmap("upload" if t.kind == "upload" else "download", T.ACCENT, 20))
        lay.addWidget(ic)
        col = QVBoxLayout()
        col.setSpacing(3)
        self.name = plain(QLabel(t.name))
        self.name.setStyleSheet("font-weight: 600;")
        where = ""
        if t.conv and store.conv_exists(t.conv):
            where = ("to " if t.kind == "upload" else "from ") + store.title(t.conv)
        self.where = where
        self.meta = QLabel()
        self.meta.setStyleSheet(f"color: {T.MUTED}; font-size: 8.5pt;")
        self.bar = QProgressBar()
        self.bar.setFixedHeight(6)
        self.bar.setRange(0, 1000)
        col.addWidget(self.name)
        col.addWidget(self.bar)
        col.addWidget(self.meta)
        lay.addLayout(col, 1)
        self.b_cancel = IconButton("close", "Cancel", 32, 16)
        self.b_cancel.clicked.connect(t.cancel)
        self.b_retry = IconButton("refresh", "Retry", 32, 18)
        self.b_retry.clicked.connect(lambda: manager.retry(t))
        self.b_open = IconButton("open", "Open", 32, 18, T.ACCENT, T.ACCENT)
        self.b_open.clicked.connect(lambda: open_path(t.dest_path if t.kind == "download" else t.path))
        self.b_folder = IconButton("folder", "Show in folder", 32, 18)
        self.b_folder.clicked.connect(lambda: show_in_folder(t.dest_path if t.kind == "download" else t.path))
        for b in (self.b_cancel, self.b_retry, self.b_open, self.b_folder):
            lay.addWidget(b)
        self.refresh()

    def refresh(self):
        t = self.t
        pct = t.done / t.size if t.size else (1 if t.state == "done" else 0)
        self.bar.setValue(int(pct * 1000))
        if t.active:
            speed = f"  ·  {P.human_size(t.speed)}/s" if t.speed else ""
            state = f"{P.human_size(t.done)} of {P.human_size(t.size)}{speed}"
        elif t.state == "done":
            state = f"{P.human_size(t.size)}  ·  {'Sent' if t.kind == 'upload' else 'Downloaded'}"
        elif t.state == "cancelled":
            state = "Cancelled"
        else:
            state = f"<span style='color:{T.DANGER}'>Failed: {esc(t.error)}</span>"
        self.meta.setText(f"{state}  ·  {esc(self.where)}" if self.where else state)
        self.bar.setVisible(t.active)
        self.b_cancel.setVisible(t.active)
        self.b_retry.setVisible(t.state in ("failed", "cancelled"))
        self.b_open.setVisible(t.state == "done")
        self.b_folder.setVisible(t.state == "done")


class TransfersPage(QWidget):
    def __init__(self, manager, store):
        super().__init__()
        self.manager = manager
        self.store = store
        self.rows = {}
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        header = PageHeader("File transfers", "Uploads and downloads of this session")
        clear = QPushButton("Clear finished")
        clear.clicked.connect(self.clear_finished)
        folder = QPushButton(" Downloads folder")
        folder.setIcon(icon("folder", T.TEXT, 16))
        folder.clicked.connect(lambda: open_path(manager.config["download_dir"]))
        header.lay.addWidget(folder)
        header.lay.addWidget(clear)
        lay.addWidget(header)
        self.area, self.col = scroll_column()
        lay.addWidget(self.area, 1)
        self.empty = QLabel("No file transfers yet.\nDrag files into a chat to send them.")
        self.empty.setAlignment(Qt.AlignCenter)
        self.empty.setStyleSheet(f"color: {T.FAINT}; padding: 40px;")
        self.col.insertWidget(0, self.empty)
        manager.added.connect(self.add)
        manager.changed.connect(self.changed)

    def add(self, t):
        if getattr(t, "hidden", False):
            return
        self.empty.hide()
        row = TransferRow(self.manager, t, self.store)
        self.rows[id(t)] = row
        self.col.insertWidget(0, row)

    def changed(self, t):
        row = self.rows.get(id(t))
        if row:
            row.refresh()

    def clear_finished(self):
        self.manager.clear_finished()
        for key, row in list(self.rows.items()):
            if not row.t.active:
                row.deleteLater()
                del self.rows[key]
        self.empty.setVisible(not self.rows)

    def active_count(self):
        return sum(1 for r in self.rows.values() if r.t.active)
