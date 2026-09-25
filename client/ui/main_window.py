"""Main client window: navigation rail, sidebar and content pages."""

import ctypes
import os
import sys

from PySide6.QtCore import QEvent, QRect, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QIcon, QKeySequence, QPainter, QShortcut
from PySide6.QtWidgets import (
    QApplication, QButtonGroup, QFileDialog, QFrame, QHBoxLayout, QLabel, QMainWindow, QMenu,
    QMessageBox, QStackedWidget, QSystemTrayIcon, QVBoxLayout, QWidget,
)

from common import protocol as P
from common import theme as T
from common.icons import asset, icon, logo_widget
from client.ui.chat_view import ChatView
from client.ui.dialogs import (
    AnnouncementPopup, ComposeAnnouncementDialog, NewRoomDialog, RoomInfoDialog, SearchDialog,
    ProfileDialog, SettingsDialog,
)
from client.ui.pages import AnnouncementsPage, DirectoryPage, HomePage, TransfersPage
from client.ui.sidebar import Sidebar
from client import stickers
from client.ui.widgets import MeButton, RailButton, plain


def idle_seconds() -> float:
    if sys.platform != "win32":
        return 0
    class LASTINPUTINFO(ctypes.Structure):
        _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]
    lii = LASTINPUTINFO()
    lii.cbSize = ctypes.sizeof(lii)
    if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(lii)):
        return 0
    return ((ctypes.windll.kernel32.GetTickCount() - lii.dwTime) & 0xFFFFFFFF) / 1000


class MainWindow(QMainWindow):
    logout_requested = Signal()

    def __init__(self, conn, store, transfers, config):
        super().__init__()
        self.conn = conn
        self.store = store
        self.transfers = transfers
        self.config = config
        self.quitting = False
        self.compact = False
        self._normal_geometry = None
        self.popups = {}
        self.auto_away = False
        self.last_notified_conv = None
        self.base_icon = QIcon(asset("app.ico"))
        self.setWindowIcon(self.base_icon)
        self.setWindowTitle("LAN Messenger")
        self.resize(1280, 800)
        self.setMinimumSize(960, 600)
        store.is_viewing = self.is_viewing

        root = QWidget()
        root.setObjectName("root")
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self.banner = plain(QLabel())
        self.banner.setAlignment(Qt.AlignCenter)
        self.banner.setStyleSheet(f"background: {T.WARN_BG}; color: {T.WARN_TEXT}; padding: 7px; font-weight: 600;")
        self.banner.hide()
        outer.addWidget(self.banner)
        self.update_bar = QFrame()
        self.update_bar.setStyleSheet(f"QFrame {{ background: {T.ACCENT_SOFT}; }}")
        ub = QHBoxLayout(self.update_bar)
        ub.setContentsMargins(16, 5, 10, 5)
        self.update_label = plain(QLabel())
        self.update_label.setStyleSheet(f"color: {T.TEXT}; font-weight: 600; background: transparent;")
        ub.addWidget(self.update_label, 1)
        from PySide6.QtWidgets import QPushButton
        self.update_btn = QPushButton("Install now")
        T.polish(self.update_btn, primary=True)
        self.update_btn.clicked.connect(self.install_update)
        later = QPushButton("Later")
        later.clicked.connect(self.update_bar.hide)
        ub.addWidget(self.update_btn)
        ub.addWidget(later)
        self.update_bar.hide()
        outer.addWidget(self.update_bar)
        self.pending_update = None
        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        outer.addLayout(body, 1)

        # ---- navigation rail
        rail = QFrame()
        rail.setObjectName("rail")
        rail.setFixedWidth(RailButton.W)
        rail.setStyleSheet(f"#rail {{ background: {T.RAIL}; border-right: 1px solid {T.BORDER}; }}")
        rl = QVBoxLayout(rail)
        rl.setContentsMargins(0, 16, 0, 10)
        rl.setSpacing(2)
        rl.addWidget(logo_widget(34), 0, Qt.AlignHCenter)
        rl.addSpacing(18)
        self.rail_group = QButtonGroup(self)
        self.rail = {}
        for key, ic, label, tip in [("chats", "chat", "Chats", "Chats"), ("contacts", "users", "People", "People"),
                                    ("rooms", "hash", "Rooms", "Chat rooms"),
                                    ("directory", "org", "Org", "Directory / org chart"),
                                    ("announcements", "megaphone", "News", "Announcements"),
                                    ("transfers", "download", "Files", "File transfers")]:
            b = RailButton(ic, tip, label)
            b.clicked.connect(lambda _=False, k=key: self.rail_clicked(k))
            self.rail_group.addButton(b)
            self.rail[key] = b
            rl.addWidget(b, 0, Qt.AlignHCenter)
        rl.addStretch(1)
        self.b_pin = RailButton("pin", "Keep on top of other windows")
        self.b_pin.clicked.connect(lambda: self.set_on_top(self.b_pin.isChecked()))
        self.b_pin.hide()
        rl.addWidget(self.b_pin, 0, Qt.AlignHCenter)
        self.b_compact = RailButton("compact", "Compact view — dock a narrow window to the right (Ctrl+Shift+M)")
        self.b_compact.clicked.connect(lambda: self.set_compact(self.b_compact.isChecked()))
        rl.addWidget(self.b_compact, 0, Qt.AlignHCenter)
        self.b_search = RailButton("search", "Search messages (Ctrl+F)")
        self.b_search.setCheckable(False)
        self.b_search.clicked.connect(self.show_search)
        rl.addWidget(self.b_search, 0, Qt.AlignHCenter)
        self.b_settings = RailButton("settings", "Settings")
        self.b_settings.setCheckable(False)
        self.b_settings.clicked.connect(self.show_settings)
        rl.addWidget(self.b_settings, 0, Qt.AlignHCenter)
        rl.addSpacing(6)
        self.me_btn = MeButton()
        self.me_btn.clicked.connect(self.me_menu)
        rl.addWidget(self.me_btn, 0, Qt.AlignHCenter)
        if T.FESTIVAL:
            from client.ui.festive import FestiveStripe
            body.addWidget(FestiveStripe())
        body.addWidget(rail)

        # ---- sidebar
        self.sidebar = Sidebar(store)
        self.sidebar.open_conv.connect(self.open_conv)
        self.sidebar.new_room.connect(self.new_room)
        self.sidebar.conv_menu.connect(self.conv_menu)
        body.addWidget(self.sidebar)

        # ---- content
        self.stack = QStackedWidget()
        from client.previews import PreviewCache
        from client.folders import Extractor
        self.previews = PreviewCache(transfers, self)
        from client.avatars import AvatarCache
        self.avatars = AvatarCache(conn, store, self)
        self._avatar_repaint = QTimer(self, singleShot=True, interval=120, timeout=self._repaint_avatars)
        self.avatars.changed.connect(lambda _uid: self._avatar_repaint.start())
        self.extractor = Extractor(self)
        self.extractor.done.connect(self._extracted)
        self.extractor.failed.connect(lambda _z, err: self.toast(f"Could not extract: {err}"))
        self.home = HomePage(self)
        self.chat = ChatView(self)
        self.announcements = AnnouncementsPage(store)
        self.announcements.show_reads.connect(self.show_announcement_reads)
        self.announcements.compose.connect(lambda: ComposeAnnouncementDialog(self).exec())
        self.transfers_page = TransfersPage(transfers, store)
        self.directory = DirectoryPage(self)
        for w in (self.home, self.chat, self.announcements, self.transfers_page, self.directory):
            self.stack.addWidget(w)
        body.addWidget(self.stack, 1)

        self.toast_label = plain(QLabel(self))
        self.toast_label.setStyleSheet(f"background: {T.TOOLTIP}; color: #eef0f5; border-radius: 12px;"
                                       " padding: 10px 18px; font-weight: 600;")
        self.toast_label.hide()
        self.toast_timer = QTimer(self, singleShot=True, timeout=self.toast_label.hide)

        self._make_tray()
        from client.screenshare import ScreenShareManager
        self.screens = ScreenShareManager(self)

        # ---- wiring
        store.me_changed.connect(self._me_changed)
        store.unread_changed.connect(self._update_unread)
        store.announcements_changed.connect(self._update_unread)
        store.message_added.connect(self._on_message)
        store.announcement.connect(self._on_announcement)
        store.room_removed.connect(self._room_removed)
        store.users_changed.connect(self._check_open_conv)
        store.update_available.connect(self._on_update_available)
        store.reminder_fired.connect(self._show_reminder)
        self.reminder_cards = {}
        transfers.upload_done.connect(self._upload_done)
        transfers.added.connect(lambda _t: self._update_transfers_badge())
        transfers.changed.connect(lambda _t: self._update_transfers_badge())
        conn.logged_in.connect(self._on_logged_in)
        conn.connection_lost.connect(self._on_connection_lost)

        self.idle_timer = QTimer(self, interval=20000, timeout=self._check_idle)
        self.idle_timer.start()

        QShortcut(QKeySequence("Ctrl+F"), self, activated=self.show_search)
        QShortcut(QKeySequence("Ctrl+K"), self, activated=self.focus_search)
        QShortcut(QKeySequence("Ctrl+Shift+M"), self, activated=lambda: self.set_compact(not self.compact))
        self.chat.back.connect(lambda: self._compact_show("list"))
        self.rail["chats"].setChecked(True)
        self.sidebar.show_page("chats")

    # ================================================================ tray
    def _make_tray(self):
        self.tray = QSystemTrayIcon(self.base_icon, self)
        self.tray.setToolTip("LAN Messenger")
        m = QMenu()
        m.addAction("Open LAN Messenger", self.show_normal)
        self.tray_compact = m.addAction("Compact view", lambda: (self.show_normal(), self.set_compact(not self.compact)))
        self.tray_compact.setCheckable(True)
        status_menu = m.addMenu("Status")
        for st in P.STATUSES:
            status_menu.addAction(icon("user", T.STATUS_COLORS[st], 16), T.STATUS_LABELS[st],
                                  lambda st=st: self.set_status(st))
        m.addSeparator()
        m.addAction("Sign out", self.confirm_logout)
        m.addAction("Quit", self.quit)
        self.tray_menu = m
        self.tray.setContextMenu(m)
        self.tray.activated.connect(self._tray_activated)
        self.tray.messageClicked.connect(self._notification_clicked)
        self.tray.show()

    def _tray_activated(self, reason):
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            if self.isVisible() and self.isActiveWindow():
                self.hide()
            else:
                self.show_normal()

    def _notification_clicked(self):
        self.show_normal()
        if self.last_notified_conv == "announcements":
            self.rail_clicked("announcements")
        elif self.last_notified_conv and self.store.conv_exists(self.last_notified_conv):
            self.open_conv(self.last_notified_conv)

    def show_normal(self):
        self.show()
        self.setWindowState((self.windowState() & ~Qt.WindowMinimized) | Qt.WindowActive)
        self.raise_()
        self.activateWindow()
        T.dark_title_bar(self)
        if self.config["compact_mode"] and not self.compact:
            QTimer.singleShot(0, lambda: self.set_compact(True))

    # ============================================================ compact view
    COMPACT_WIDTH = 440

    def set_compact(self, on, remember=True):
        """Narrow, full-height window docked to the right edge of the screen (like a phone)."""
        on = bool(on)
        self.b_compact.setChecked(on)
        self.tray_compact.setChecked(on)
        if on == self.compact:
            return
        self.compact = on
        if remember:
            self.config["compact_mode"] = on
            self.config.save()
        self.chat.set_compact(on)
        self.b_pin.setVisible(on)
        if on:
            self._normal_geometry = self.saveGeometry()
            if self.isMaximized() or self.isFullScreen():
                self.showNormal()
            self.setMinimumSize(360, 480)
            self.sidebar.setMinimumWidth(0)
            self.sidebar.setMaximumWidth(16777215)
            self._dock_right()
            self._compact_show("content" if self.stack.currentWidget() is self.chat and self.chat.conv
                               else "list")
            self.set_on_top(self.config["compact_on_top"], save=False)
        else:
            self.set_on_top(False, save=False)
            self.sidebar.setFixedWidth(330)
            self.sidebar.show()
            self.stack.show()
            self.setMinimumSize(960, 600)
            if self._normal_geometry:
                self.restoreGeometry(self._normal_geometry)
            else:
                self.resize(1280, 800)

    def _dock_right(self):
        screen = self.screen() or QApplication.primaryScreen()
        area = screen.availableGeometry()
        frame = self.frameGeometry()
        extra_w = frame.width() - self.width()          # window borders
        extra_h = frame.height() - self.height()        # title bar + borders
        self.resize(self.COMPACT_WIDTH, area.height() - extra_h)
        self.move(area.right() - self.COMPACT_WIDTH - extra_w + 1, area.top())

    def _compact_show(self, which):
        """In compact view show either the list (sidebar) or the content (chat / page), never both."""
        if not self.compact:
            return
        self.sidebar.setVisible(which == "list")
        self.stack.setVisible(which != "list")

    def set_on_top(self, on, save=True):
        on = bool(on) and self.compact
        self.b_pin.setChecked(on)
        if save:
            self.config["compact_on_top"] = on
            self.config.save()
        if bool(self.windowFlags() & Qt.WindowStaysOnTopHint) != on:
            geo, visible = self.geometry(), self.isVisible()
            self.setWindowFlag(Qt.WindowStaysOnTopHint, on)
            if visible:
                self.show()
                self.setGeometry(geo)
            T.dark_title_bar(self)

    def _tray_icon(self, unread):
        if not unread:
            return self.base_icon
        pm = self.base_icon.pixmap(64, 64)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        p.setBrush(QColor(T.DANGER))
        p.setPen(Qt.NoPen)
        p.drawEllipse(QRect(30, 0, 34, 34))
        f = QFont("Segoe UI")
        f.setPixelSize(22)
        f.setBold(True)
        p.setFont(f)
        p.setPen(QColor("#ffffff"))
        p.drawText(QRect(30, 0, 34, 34), Qt.AlignCenter, "9+" if unread > 9 else str(unread))
        p.end()
        return QIcon(pm)

    # ============================================================ state
    def is_viewing(self, conv):
        return (self.chat.conv == conv and self.stack.currentWidget() is self.chat and self.isVisible()
                and self.stack.isVisible()
                and self.isActiveWindow() and not self.isMinimized())

    def _on_logged_in(self, boot):
        self.banner.hide()
        self.home.set_name(self.store.me.get("name", ""), self.store.server_name)
        self.setWindowTitle(f"LAN Messenger — {self.store.me.get('name', '')}  ·  {self.store.server_name}")
        self._me_changed()
        if self.chat.conv and self.stack.currentWidget() is self.chat:
            if self.store.conv_exists(self.chat.conv):
                self.chat.open(self.chat.conv)
            else:
                self.stack.setCurrentWidget(self.home)
        if boot.get("review_notice"):
            QTimer.singleShot(500, self._review_notice)
        if boot.get("must_change_password"):
            QTimer.singleShot(300, lambda: self._force_password_change(boot["must_change_password"]))
            return
        # reminders that came due while this PC was off / signed out
        for r in [r for r in self.store.reminders if r.get("state") == 1][:5]:
            self._show_reminder(r, sound=False)
        # unread announcements pop up after login (max 3)
        unread = [a for a in self.store.announcements if not a.get("read")]
        for a in reversed(unread[:3]):
            self._popup_announcement(a)

    def _force_password_change(self, reason):
        from client.ui.dialogs import ChangePasswordDialog
        self.show_normal()
        if not ChangePasswordDialog(self, reason).exec():
            self.logout_requested.emit()
            return
        for a in reversed([a for a in self.store.announcements if not a.get("read")][:3]):
            self._popup_announcement(a)

    def _on_connection_lost(self, reason):
        self.banner.setText(f"Connection to the server lost — reconnecting...  ({reason})")
        self.banner.show()
        self._show_me(connected=False)

    def _show_me(self, connected=True):
        me = self.store.me
        status = me.get("status", "online") if connected else "offline"
        line = "Reconnecting..." if not connected else (self.store.status_text(me) or T.STATUS_LABELS.get(status, status))
        self.me_btn.set_me(me.get("name", ""), status, f"{me.get('name', '')}\n{line}", uid=self.store.my_id)

    def me_menu(self):
        """My name, status choices, status message, settings and sign out."""
        me = self.store.me
        m = QMenu(self)
        head = m.addAction(me.get("name", ""))
        head.setEnabled(False)
        m.addSeparator()
        for st in P.STATUSES:
            a = m.addAction(icon("user", T.STATUS_COLORS[st], 16), T.STATUS_LABELS[st],
                            lambda st=st: self.set_status(st))
            a.setCheckable(True)
            a.setChecked(me.get("status") == st)
        m.addAction(icon("smile", T.TEXT, 16), "Profile photo & status...", self.edit_status_message)
        m.addAction(icon("clock", T.TEXT, 16), "New reminder...", self.new_reminder)
        m.addSeparator()
        dark = T.DARK
        m.addAction(icon("palette", T.TEXT, 16), "Switch to light mode" if dark else "Switch to dark mode",
                    lambda: self.switch_mode("light" if dark else "midnight"))
        m.addAction(icon("settings", T.TEXT, 16), "Settings", self.show_settings)
        m.addAction(icon("logout", T.DANGER, 16), "Sign out", self.confirm_logout)
        m.exec(self.me_btn.mapToGlobal(self.me_btn.rect().topRight()))

    def _me_changed(self):
        self._show_me(connected=self.conn.online)
        self.sidebar.update_permissions()
        self.announcements.rebuild()

    def _update_unread(self, *_):
        n = self.store.total_unread()
        a = self.store.unread_announcements()
        self.rail["chats"].set_badge(n)
        self.rail["announcements"].set_badge(a)
        total = n + a
        self.tray.setIcon(self._tray_icon(total))
        self.tray.setToolTip(f"LAN Messenger — {total} unread" if total else "LAN Messenger")
        title = f"LAN Messenger — {self.store.me.get('name', '')}  ·  {self.store.server_name}"
        self.setWindowTitle(f"({total}) {title}" if total else title)

    def _update_transfers_badge(self):
        self.rail["transfers"].set_badge(self.transfers_page.active_count())

    def _check_open_conv(self):
        if self.chat.conv and not self.store.conv_exists(self.chat.conv) and self.stack.currentWidget() is self.chat:
            self.stack.setCurrentWidget(self.home)

    def _room_removed(self, room_id):
        if self.chat.conv == P.room_conv(room_id):
            self.chat.conv = None
            self.stack.setCurrentWidget(self.home)
            self.toast("You are no longer a member of that room.")

    # ======================================================== navigation
    def rail_clicked(self, key):
        self.rail[key].setChecked(True)
        self._compact_show("list" if key in ("chats", "contacts", "rooms") else "content")
        if key in ("chats", "contacts", "rooms"):
            self.sidebar.show_page(key)
            if self.stack.currentWidget() in (self.announcements, self.transfers_page, self.directory):
                self.stack.setCurrentWidget(self.chat if self.chat.conv else self.home)
        elif key == "directory":
            self.stack.setCurrentWidget(self.directory)
        elif key == "announcements":
            self.stack.setCurrentWidget(self.announcements)
            self.announcements.rebuild()
            QTimer.singleShot(1500, self.announcements.mark_all_read)
        elif key == "transfers":
            self.stack.setCurrentWidget(self.transfers_page)

    def open_conv(self, conv):
        if not self.store.conv_exists(conv):
            return
        if not self.isVisible():
            self.show_normal()
        if self.rail_group.checkedButton() in (self.rail["announcements"], self.rail["transfers"],
                                               self.rail["directory"]):
            self.rail["chats"].setChecked(True)
            self.sidebar.show_page("chats")
        self.sidebar.set_active(conv)
        self.stack.setCurrentWidget(self.chat)
        self._compact_show("content")
        self.chat.open(conv)
        self.store.mark_read(conv)

    def changeEvent(self, e):
        super().changeEvent(e)
        if e.type() == QEvent.ActivationChange and self.isActiveWindow():
            if self.chat.conv and self.stack.currentWidget() is self.chat:
                self.store.mark_read(self.chat.conv)

    # ===================================================== notifications
    def _on_message(self, msg, is_new):
        if not is_new or msg["sender_id"] == self.store.my_id or msg["kind"] == "system":
            return
        if msg["kind"] == "buzz":
            self.buzzed(msg)
            return
        if self.is_viewing(msg["conv"]):
            return
        mention = self.store.mentions_me(msg)
        # muted chats and "Do not disturb" stay silent, unless someone @mentions you
        if not mention and (self.store.is_muted(msg["conv"]) or self.store.me.get("status") == "busy"):
            return
        sender = self.store.user_name(msg["sender_id"])
        where = "" if msg["conv"].startswith("u:") else f" in {self.store.title(msg['conv'])}"
        title = f"{sender} mentioned you{where}" if mention else f"{sender}{where}"
        self.notify(title, stickers.summary(msg), msg["conv"])

    # ======================================================= reminders
    def add_reminder(self, due_at, text="", conv="", message_id=None):
        from client.ui.planner_ui import fmt_due

        def done(reply):
            self.toast(f"⏰ Reminder set for {fmt_due(due_at)}" if reply.get("ok")
                       else f"Reminder not set: {reply.get('error')}")
        self.conn.request("reminder_add", done, due_at=due_at, text=text, conv=conv, message_id=message_id)

    def new_reminder(self, conv="", message=None):
        """Ask what and when, then set a reminder (optionally about a chat or a message)."""
        from client.ui.planner_ui import TimeDialog
        label = "Remind me about" + (" this message" if message else f" {self.store.title(conv)}" if conv else "")
        dlg = TimeDialog(self, "New reminder", text="", text_label=label, ok_text="Set reminder")
        dlg.text.setPlaceholderText("e.g. Send the FAL_030 comp to Dev")
        if dlg.exec():
            self.add_reminder(dlg.timestamp(), dlg.message(), conv, message["id"] if message else None)

    def _show_reminder(self, r, sound=True):
        from client.ui.planner_ui import ReminderPopup
        old = self.reminder_cards.pop(r["id"], None)
        if old:
            old.close()
        card = ReminderPopup(self, r)
        card.done.connect(lambda rid: self.conn.request("reminder_done", None, id=rid))
        card.snooze.connect(lambda rid, ts: self.conn.request("reminder_snooze", None, id=rid, due_at=ts))
        card.open_chat.connect(lambda conv: (self.show_normal(), self.open_conv(conv)))
        card.destroyed.connect(lambda *_, rid=r["id"], c=card: self.reminder_cards.get(rid) is c
                               and self.reminder_cards.pop(rid, None))
        self.reminder_cards[r["id"]] = card
        card.show_at(len(self.reminder_cards) - 1)
        if sound and self.config["sounds"]:
            play_sound()
        if sound:
            self.tray.showMessage("⏰ Reminder", r.get("text") or r.get("snippet", ""), self.base_icon, 5000)

    def buzzed(self, msg):
        """Someone buzzed me: come to the front, open the chat, shake and ring - even on Do not disturb."""
        sender = self.store.user_name(msg["sender_id"])
        if not self.config["allow_buzz"]:
            self.notify(f"⚡ {sender} buzzed you", "Buzz", msg["conv"])
            return
        hidden = not self.isVisible() or self.isMinimized()
        self.show_normal()
        if hidden:
            # it was out of sight: pop up as the phone-style view on the right, above everything for a while
            self.set_compact(True, remember=False)
            self._dock_right()
            if not self.b_pin.isChecked():
                self.set_on_top(True, save=False)
                QTimer.singleShot(15000, lambda: self.set_on_top(self.config["compact_on_top"], save=False)
                                  if self.compact else None)
        self.open_conv(msg["conv"])
        self.last_notified_conv = msg["conv"]
        self.tray.showMessage(f"⚡ BUZZ from {sender}", f"{sender} needs your attention", self.base_icon, 6000)
        play_sound("buzz")
        QApplication.alert(self, 3000)
        self.glow()
        if not hidden:
            self.shake()

    def glow(self):
        """A pulsing accent border around the whole window for a couple of seconds."""
        from PySide6.QtGui import QPen

        class Glow(QWidget):
            def __init__(self, parent):
                super().__init__(parent)
                self.setAttribute(Qt.WA_TransparentForMouseEvents)
                self.tick = 0
                self.setGeometry(parent.rect())
                self.timer = QTimer(self, interval=60, timeout=self.step)
                self.timer.start()
                self.show()
                self.raise_()

            def step(self):
                self.tick += 1
                if self.tick > 45:
                    self.deleteLater()
                    return
                self.setGeometry(self.parent().rect())
                self.update()

            def paintEvent(self, _):
                import math
                p = QPainter(self)
                p.setRenderHint(QPainter.Antialiasing)
                c = QColor(T.ACCENT)
                c.setAlpha(int(90 + 165 * abs(math.sin(self.tick / 4))))
                p.setPen(QPen(c, 6))
                p.drawRect(self.rect().adjusted(3, 3, -3, -3))
        Glow(self.centralWidget())

    def shake(self):
        from PySide6.QtCore import QPoint, QPropertyAnimation
        if self.isMaximized() or self.isFullScreen():
            return
        if getattr(self, "_shaking", False):          # still shaking: don't start again from mid-swing
            return
        start = self.pos()
        anim = QPropertyAnimation(self, b"pos", self)
        self._shaking = True
        anim.finished.connect(lambda: setattr(self, "_shaking", False))
        anim.finished.connect(anim.deleteLater)
        anim.setDuration(650)
        steps = 12
        for i in range(steps + 1):
            dx = 0 if i in (0, steps) else (14 if i % 2 else -14) * (1 - i / steps)
            anim.setKeyValueAt(i / steps, start + QPoint(int(dx), 0))
        anim.start()

    def notify(self, title, text, target):
        self.last_notified_conv = target
        if self.config["notifications"]:
            self.tray.showMessage(title, text[:200], self.base_icon, 5000)
        if self.config["sounds"]:
            play_sound()
        QApplication.alert(self, 0)

    def _on_announcement(self, ann):
        if ann["sender_id"] == self.store.my_id:
            ann["read"] = True
            self.store.mark_announcement_read(ann["id"])
            return
        self._popup_announcement(ann)
        if self.config["sounds"]:
            play_sound()

    def _popup_announcement(self, ann):
        if ann["id"] in self.popups:
            return
        p = AnnouncementPopup(self, ann)
        p.acknowledged.connect(self._ack_announcement)
        self.popups[ann["id"]] = p
        p.show()
        p.raise_()
        p.activateWindow()

    def _ack_announcement(self, ann_id):
        self.popups.pop(ann_id, None)
        self.store.mark_announcement_read(ann_id)

    def toast(self, text, ms=3500):
        self.toast_label.setText(text)
        self.toast_label.adjustSize()
        self.toast_label.move((self.width() - self.toast_label.width()) // 2 + 150,
                              self.height() - self.toast_label.height() - 110)
        self.toast_label.raise_()
        self.toast_label.show()
        self.toast_timer.start(ms)

    # ============================================================ status
    def set_status(self, status, auto=False):
        self.auto_away = auto
        self.conn.status = status
        self.store.me["status"] = status
        self.conn.send("set_status", status=status)
        self._me_changed()

    def edit_status_message(self):
        ProfileDialog(self).exec()

    def _check_idle(self):
        minutes = int(self.config["auto_away_minutes"] or 0)
        if not minutes or not self.conn.online:
            return
        idle = idle_seconds()
        status = self.store.me.get("status")
        if status == "online" and idle > minutes * 60:
            self.set_status("away", auto=True)
        elif self.auto_away and status == "away" and idle < 30:
            self.set_status("online")

    # ============================================================= files
    def send_file(self, conv, path):
        if os.path.isdir(path):
            self.send_folder(conv, path)
            return
        if not os.path.isfile(path):
            return
        size = os.path.getsize(path)
        if self.store.max_file_size and size > self.store.max_file_size:
            self.toast(f"“{os.path.basename(path)}” is too large (max {P.human_size(self.store.max_file_size)}).")
            return
        self.transfers.upload(path, conv)

    def send_folder(self, conv, folder):
        """Pack a folder (e.g. an image sequence) into a zip in the background, then send it."""
        from client.folders import PackJob, free_space, temp_dir
        job = PackJob(folder, conv)
        name = os.path.basename(os.path.normpath(folder))
        if not job.files:
            self.toast(f"“{name}” is empty.")
            return
        if self.store.max_file_size and job.size > self.store.max_file_size:
            self.toast(f"“{name}” is too large ({P.human_size(job.size)}, max {P.human_size(self.store.max_file_size)}).")
            return
        free = free_space(temp_dir())
        if free is not None and free < job.size + 100 * 1024 * 1024:
            self.toast(f"Not enough free disk space to pack “{name}” ({P.human_size(job.size)} needed).")
            return
        self.transfers.added.emit(job)          # shows "packing" progress in the chat's upload strip
        job.progress.connect(self.transfers.changed.emit)

        def packed(j):
            self.transfers.changed.emit(j)
            if j.state == "done":
                caption = f"📁 {name}  ({j.files} files, {P.human_size(j.size)})"
                t = self.transfers.upload(j.zip_path, conv, caption)
                t.temp_file = j.zip_path
            elif j.state == "failed":
                self.toast(f"Could not pack “{name}”: {j.error}")
        job.finished.connect(packed)
        job.start()

    def extract_zip(self, zip_path):
        self.extractor.extract(zip_path)

    def _extracted(self, zip_path, folder):
        from client.ui.widgets import open_path
        self.toast(f"Extracted to {folder}")
        open_path(folder)
        for card in self.chat.findChildren(QWidget):
            if hasattr(card, "b_extract"):
                card.refresh()

    def _upload_done(self, t):
        def done(reply):
            if reply.get("ok"):
                self.store.add_message(reply["message"])
            else:
                self.toast(f"File not sent: {reply.get('error')}")
        self.conn.request("send", done, conv=t.conv, text=t.caption, file_id=t.file_id)

    def download_file(self, info, conv=None):
        existing = self.config.downloaded_path(info["id"])
        if existing:
            return
        self.transfers.download(info, conv=conv)
        self._update_transfers_badge()

    def download_file_as(self, info, conv=None):
        path, _ = QFileDialog.getSaveFileName(self, "Save file as",
                                              os.path.join(self.config["download_dir"],
                                                           os.path.basename(info["name"].replace("\\", "/"))))
        if path:
            self.transfers.download(info, path, conv)

    # ============================================================= rooms
    def new_room(self, preselect=()):
        dlg = NewRoomDialog(self, self.store, preselect)
        if not dlg.exec():
            return

        def done(reply):
            if reply.get("ok"):
                conv = P.room_conv(reply["room_id"])
                QTimer.singleShot(200, lambda: self.open_conv(conv))
            else:
                QMessageBox.warning(self, "New room", reply.get("error", "Failed"))
        self.conn.request("create_room", done, name=dlg.name.text(), topic=dlg.topic.text(),
                          members=dlg.picker.selected())

    def show_room_info(self, conv):
        if not conv or not conv.startswith("r:"):
            return
        room = self.store.rooms.get(P.parse_conv(conv)[1])
        if room:
            RoomInfoDialog(self, room).exec()

    def leave_room(self, room_id):
        room = self.store.rooms.get(room_id)
        if room and QMessageBox.question(self, "Leave room", f"Leave “{room['name']}”?") == QMessageBox.Yes:
            self.conn.request("room_leave", lambda r: None if r.get("ok") else self.toast(r.get("error")),
                              room_id=room_id)

    def conv_menu(self, conv, pos):
        m = QMenu(self)
        kind, target = P.parse_conv(conv)
        m.addAction(icon("chat", T.TEXT, 16), "Open chat", lambda: self.open_conv(conv))
        muted = self.store.is_muted(conv)
        m.addAction(icon("bell", T.TEXT, 16), "Unmute notifications" if muted else "Mute notifications",
                    lambda: self.set_muted(conv, not muted))
        if kind == "u":
            m.addAction(icon("attachment", T.TEXT, 16), "Send files...", lambda: self._send_files_to(conv))
            if self.store.perm("create_rooms"):
                m.addAction(icon("hash", T.TEXT, 16), "Create room with this person",
                            lambda: self.new_room([target]))
            m.addAction(icon("dashboard", T.TEXT, 16), "Share my screen...",
                        lambda: self.screens.invite(target, "offer"))
            m.addAction(icon("search", T.TEXT, 16), "Ask to see their screen...",
                        lambda: self.screens.invite(target, "request"))
            self.add_manage_actions(m, target)
        else:
            m.addAction(icon("users", T.TEXT, 16), "Members", lambda: self.show_room_info(conv))
            m.addAction(icon("attachment", T.TEXT, 16), "Send files...", lambda: self._send_files_to(conv))
            if not self.store.rooms.get(target, {}).get("auto"):
                m.addSeparator()
                m.addAction(icon("logout", T.DANGER, 16), "Leave room", lambda: self.leave_room(target))
        m.exec(pos)

    # ======================================================== policy / updates
    def _review_notice(self):
        """Tell the user once per server that administrators may review chats."""
        key = f"{self.conn.host}:{self.conn.port}"
        seen = self.config.get("review_notice_seen") or []
        if key in seen:
            return
        QMessageBox.information(
            self, "Chat policy",
            "Your studio's administrators can review conversations on this messenger "
            "(for example for HR or security investigations). Every review is recorded.\n\n"
            "Please use it for work communication.")
        self.config["review_notice_seen"] = seen + [key]
        self.config.save()

    @staticmethod
    def _is_newer(version):
        from common.version import APP_VERSION
        try:
            return tuple(int(x) for x in version.split(".")) > tuple(int(x) for x in APP_VERSION.split("."))
        except ValueError:
            return False

    @staticmethod
    def _is_admin_user():
        try:
            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        except (AttributeError, OSError):
            return False

    def _on_update_available(self, info):
        if not info or not self._is_newer(info["version"]) or not getattr(sys, "frozen", False):
            return
        self.pending_update = info
        if self._is_admin_user():
            self.update_label.setText(f"LAN Messenger {info['version']} is available.")
            self.update_btn.show()
        else:
            self.update_label.setText(f"LAN Messenger {info['version']} is available — "
                                      "please ask IT to update this PC.")
            self.update_btn.hide()
        self.update_bar.show()

    def install_update(self):
        info = self.pending_update
        if not info:
            return
        import tempfile
        dest = os.path.join(tempfile.gettempdir(), "LANMessenger",
                            os.path.basename(str(info["name"]).replace("\\", "/")) or "LANMessenger-update.exe")
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        self.update_btn.setEnabled(False)
        self.update_label.setText(f"Downloading version {info['version']}...")
        t = self.transfers.download({"id": "client-update", "name": info["name"], "size": info["size"]},
                                    dest_path=dest, hidden=True)

        def finished(tr):
            if tr.state != "done":
                self.update_label.setText(f"Update download failed: {tr.error}")
                self.update_btn.setEnabled(True)
                return
            self.update_label.setText("Installing the update — LAN Messenger restarts by itself...")
            # The installer closes this app (Restart Manager) and opens it again afterwards.
            rc = ctypes.windll.shell32.ShellExecuteW(None, "runas", dest,
                                                     "/SILENT /SUPPRESSMSGBOXES /NORESTART", None, 1)
            if rc <= 32:
                self.update_label.setText("The update was not started (administrator permission needed).")
                self.update_btn.setEnabled(True)
        t.finished.connect(finished)

    # ================================================ forward / mute / reads
    def forward_message(self, msg):
        from client.ui.dialogs import ForwardDialog
        dlg = ForwardDialog(self, msg)
        if not dlg.exec() or not dlg.target():
            return
        conv = dlg.target()
        f = msg.get("file")

        def done(reply):
            if reply.get("ok"):
                self.store.add_message(reply["message"])
                self.toast(f"Forwarded to {self.store.title(conv)}")
            else:
                self.toast(f"Not forwarded: {reply.get('error')}")
        if msg.get("kind") == "sticker":
            self.conn.request("send", done, conv=conv, sticker=msg["body"], forwarded=True)
            return
        if msg.get("kind") == "poll":
            self.toast("Polls can't be forwarded — create a new poll instead.")
            return
        self.conn.request("send", done, conv=conv, text=msg.get("body", ""), file_id=f["id"] if f else None,
                          forwarded=True)

    def set_muted(self, conv, muted):
        self.store.set_muted(conv, muted)
        self.toast(f"{self.store.title(conv)}: notifications {'muted' if muted else 'on'}"
                   + (" (you still hear @mentions)" if muted else ""))

    def show_announcement_reads(self, ann):
        from client.ui.dialogs import ReadReceiptsDialog

        def done(reply):
            if reply.get("ok"):
                ReadReceiptsDialog(self, ann["title"], reply).exec()
            else:
                self.toast(reply.get("error", "Not available"))
        self.conn.request("announcement_reads", done, id=ann["id"])

    # ====================================================== HR / IT tools
    def add_manage_actions(self, menu, uid):
        """Reset password / disable account, for designations with 'manage accounts'."""
        u = self.store.users.get(uid)
        if not u or not self.store.perm("manage_users"):
            return
        if u.get("is_admin") and not self.store.me.get("is_admin"):
            return
        menu.addSeparator()
        menu.addAction(icon("key", T.TEXT, 16), f"Reset {u['name'].split()[0]}'s password...",
                       lambda: self.reset_user_password(uid))
        menu.addAction(icon("power", T.DANGER, 16), "Disable account...", lambda: self.disable_user(uid))

    def reset_user_password(self, uid):
        from PySide6.QtWidgets import QInputDialog, QLineEdit
        name = self.store.user_name(uid)
        pw, ok = QInputDialog.getText(self, "Reset password", f"New password for {name}:", QLineEdit.Password)
        if ok and pw:
            self.conn.request("manage_user", lambda r: QMessageBox.information(
                self, "Reset password", "Password changed." if r.get("ok") else r.get("error", "Failed")),
                user_id=uid, action="reset_password", password=pw)

    def disable_user(self, uid):
        name = self.store.user_name(uid)
        if QMessageBox.question(self, "Disable account", f"Disable {name}'s account? They are signed out and "
                                "cannot sign in until the admin enables it again.") == QMessageBox.Yes:
            self.conn.request("manage_user", lambda r: self.toast(
                f"{name}'s account was disabled." if r.get("ok") else r.get("error", "Failed")),
                user_id=uid, action="disable")

    def _send_files_to(self, conv):
        paths, _ = QFileDialog.getOpenFileNames(self, f"Send files to {self.store.title(conv)}")
        for p in paths:
            self.send_file(conv, p)
        if paths:
            self.open_conv(conv)

    # =========================================================== dialogs
    def restart(self):
        """Quit and start again (used to apply a new theme)."""
        self.quit()
        if not self.quitting:
            return                            # the user chose to wait for transfers
        from PySide6.QtCore import QProcess
        server = getattr(QApplication.instance(), "instance_server", None)
        if server:
            server.close()                    # let the new copy become the single instance
        args = sys.argv[1:] if getattr(sys, "frozen", False) else sys.argv
        QProcess.startDetached(sys.executable, args)

    def _repaint_avatars(self):
        """A profile photo arrived: repaint everything that draws avatars."""
        from client.ui.widgets import Avatar, ConvItem
        for w in self.findChildren(QWidget):
            if isinstance(w, (Avatar, ConvItem, MeButton)):
                w.update()

    def switch_mode(self, theme):
        self.config["theme"] = theme
        self.config.save()
        if QMessageBox.question(self, "New look", "Restart LAN Messenger now to switch to "
                                f"{'light' if theme == 'light' else 'dark'} mode?") == QMessageBox.Yes:
            self.restart()
        else:
            self.toast("The new look is used from the next start.")

    def focus_search(self):
        """Ctrl+K: jump to the people / rooms search in the sidebar."""
        if self.sidebar.page != "chats":
            self.rail_clicked("chats")
        self.sidebar.search.setFocus()
        self.sidebar.search.selectAll()

    def show_search(self):
        SearchDialog(self).exec()

    def show_settings(self):
        SettingsDialog(self).exec()

    def confirm_logout(self):
        if QMessageBox.question(self, "Sign out", "Sign out of LAN Messenger?") == QMessageBox.Yes:
            self.logout_requested.emit()

    # ============================================================ window
    def closeEvent(self, e):
        if self.quitting:                      # the app is exiting: let the window go
            e.accept()
            return
        if not self.config["close_to_tray"]:
            e.ignore()
            self.quit()                        # asks about running transfers, then exits
            return
        e.ignore()
        self.hide()
        if not self.config.get("tray_hint_shown"):
            self.config["tray_hint_shown"] = True
            self.config.save()
            self.tray.showMessage("LAN Messenger", "Still running here in the tray. "
                                  "Right-click the icon to quit.", self.base_icon, 4000)

    def signed_out(self):
        """Sign-out: close everything that belongs to this account so the next person sees none of it."""
        self.screens.stop()
        for w in list(self.reminder_cards.values()) + list(self.popups.values()):
            w.close()
        self.reminder_cards.clear()
        self.popups.clear()
        self.chat.conv = None
        self.chat._clear()
        self.stack.setCurrentWidget(self.home)
        self.store.reset()

    def quit(self):
        if self.quitting:
            return
        active = [t for t in self.transfers.transfers if t.active and not getattr(t, "hidden", False)]
        if active and QMessageBox.question(
                self, "Quit", f"{len(active)} file transfer(s) are still running. Quit anyway?") != QMessageBox.Yes:
            return
        self.quitting = True
        self.screens.stop()
        self.conn.logout()
        self.tray.hide()
        QApplication.quit()




def play_sound(kind="notify"):
    """Short sound (generated once, played with winsound - no extra dependencies).
    kind: 'notify' (two-note chime) or 'buzz' (three rough pulses)."""
    if sys.platform != "win32":
        QApplication.beep()
        return
    import winsound
    path = _SOUND_PATHS.get(kind)
    if path is None:
        from client.config import config_dir
        path = _SOUND_PATHS[kind] = os.path.join(config_dir(), f"{kind}.wav")
        if not os.path.exists(path):
            _make_sound(path, kind)
    try:
        winsound.PlaySound(path, winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
    except RuntimeError:
        pass


_SOUND_PATHS = {}


def _make_sound(path, kind):
    import math
    import struct
    import wave
    rate = 44100
    frames = bytearray()
    if kind == "buzz":
        total = int(rate * 0.75)
        for i in range(total):
            t = i / rate
            pulse = int(t / 0.25)
            local = t - pulse * 0.25
            v = 0.0
            if local < 0.17:
                env = min(1.0, local * 60) * math.exp(-local * 6)
                v = (math.sin(2 * math.pi * 196 * t) + 0.6 * math.sin(2 * math.pi * 392 * t)
                     + 0.3 * (1 if math.sin(2 * math.pi * 98 * t) > 0 else -1)) * env
            frames += struct.pack("<h", int(max(-1, min(1, v * 0.3)) * 32767))
    else:
        total = int(rate * 0.45)
        for i in range(total):
            t = i / rate
            v = 0.0
            for freq, start, dur in ((880.0, 0.0, 0.2), (1318.5, 0.11, 0.34)):
                if start <= t < start + dur:
                    env = math.exp(-(t - start) * 9)
                    v += math.sin(2 * math.pi * freq * (t - start)) * env
            frames += struct.pack("<h", int(max(-1, min(1, v * 0.35)) * 32767))
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(bytes(frames))
