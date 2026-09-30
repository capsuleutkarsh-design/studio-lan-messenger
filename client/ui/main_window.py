"""Main client window: navigation rail, sidebar and content pages."""

import ctypes
import datetime
import os
import sys
import time

from PySide6.QtCore import QEvent, QPoint, QRect, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QIcon, QKeySequence, QPainter, QShortcut
from PySide6.QtWidgets import (
    QApplication, QButtonGroup, QFileDialog, QFrame, QHBoxLayout, QLabel, QMainWindow, QMenu,
    QMessageBox, QStackedWidget, QSystemTrayIcon, QVBoxLayout, QWidget, QWidgetAction,
)

from common import protocol as P
from common import theme as T
from common.icons import asset, icon, license_label, logo_widget
from client.ui.chat_view import ChatView
from client.ui.dialogs import (
    AnnouncementPopup, ComposeAnnouncementDialog, NewRoomDialog, RoomInfoDialog, SearchDialog,
    ProfileDialog, SettingsDialog,
)
from client.ui.pages import AnnouncementsPage, DirectoryPage, HomePage, TransfersPage
from client.ui.sidebar import Sidebar
from client import stickers
from client.ui.widgets import (
    ELLIPSIS, SEP, Avatar, CompactTabs, ElidedLabel, MeButton, RailButton, clip, first_name, fmt_time, menu_text,
    plain, rich_safe,
)


def bring_to_front(window):
    """Windows only lets the app that the user is using take the focus. A click on our notification is the
    user asking for us, so step past that lock the documented way (a harmless Alt key event), then activate."""
    if sys.platform != "win32" or not window.isVisible():
        return
    try:
        user32 = ctypes.windll.user32
        hwnd = int(window.winId())
        if user32.GetForegroundWindow() == hwnd:
            return
        user32.ShowWindow(hwnd, 9)                     # SW_RESTORE (also un-minimises)
        user32.keybd_event(0x12, 0, 0, 0)              # Alt down ...
        user32.keybd_event(0x12, 0, 2, 0)              # ... and up (KEYEVENTF_KEYUP)
        user32.SetForegroundWindow(hwnd)
        user32.BringWindowToTop(hwnd)
    except (AttributeError, OSError):
        pass


def status_dot(color, size=16, dot=10):
    """A plain filled dot in a status colour, for the status menus (the current one is marked by bold and a
    tick, so the dot itself never changes)."""
    from PySide6.QtGui import QPixmap
    ratio = 2
    pm = QPixmap(size * ratio, size * ratio)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(color))
    off = (size - dot) * ratio // 2
    p.drawEllipse(off, off, dot * ratio, dot * ratio)
    p.end()
    pm.setDevicePixelRatio(ratio)
    return QIcon(pm)


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
        from client.outbox import Outbox
        self.outbox = Outbox(conn, store, self)
        self.outbox.refused.connect(self._outbox_refused)
        if conn.online:                     # the first sign-in happened just before this window was made
            self.outbox._on_logged_in()
        self.quitting = False
        self.compact = False
        self._normal_geometry = None
        self.popups = {}
        self.auto_away = False
        self.last_notified_conv = None
        self.base_icon = QIcon(asset("app.ico"))
        self.setWindowIcon(self.base_icon)
        self.setWindowTitle("Quillo")
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
        self.banner.setWordWrap(True)
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
        self.focus_bar = QFrame()
        self.focus_bar.setStyleSheet(f"QFrame {{ background: {T.ACCENT_SOFT}; }}")
        fb = QHBoxLayout(self.focus_bar)
        fb.setContentsMargins(16, 4, 10, 4)
        fb.setSpacing(T.SPACE_S)
        focus_icon = QLabel()
        focus_icon.setPixmap(icon("target", T.ACCENT, 16).pixmap(16, 16))
        focus_icon.setStyleSheet("background: transparent;")
        fb.addWidget(focus_icon)
        self.focus_label = ElidedLabel()               # a long "who gets through" is cut, whole in the tooltip
        self.focus_label.setStyleSheet(f"color: {T.TEXT}; font-weight: 600; background: transparent;")
        fb.addWidget(self.focus_label, 1)
        focus_end = QPushButton("End now")
        focus_end.clicked.connect(self.end_focus)
        fb.addWidget(focus_end)
        self.focus_bar.hide()
        outer.addWidget(self.focus_bar)
        self.focus_missed = 0                 # messages that waited while focus time was on
        self.pending_update = None
        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        outer.addLayout(body, 1)
        outer.addWidget(license_label())            # the licence line, very small, at the very bottom
        self._outer = outer

        # ---- navigation rail
        rail = self.rail_frame = QFrame()
        rail.setObjectName("rail")
        rail.setFixedWidth(RailButton.W)
        rail.setStyleSheet(f"#rail {{ background: {T.RAIL}; }}")
        rl = self.rail_layout = QVBoxLayout(rail)
        rl.setContentsMargins(0, 11, 0, 10)
        rl.setSpacing(2)
        home_logo = self.home_logo = logo_widget(34)
        home_logo.setFixedSize(44, 44)                  # the 34 px mark with room for a hover halo around it
        home_logo.setAlignment(Qt.AlignCenter)
        home_logo.setCursor(Qt.PointingHandCursor)
        home_logo.setToolTip("Home")
        home_logo.mousePressEvent = lambda _e: self.go_home()
        home_logo.installEventFilter(self)
        rl.addWidget(home_logo, 0, Qt.AlignHCenter)
        rl.addSpacing(13)                               # under the logo (less on a short window: _fit_rail)
        self.rail_group = QButtonGroup(self)
        self.rail = {}
        for key, ic, label, tip in [("chats", "chat", "Chats", "Chats"), ("contacts", "users", "People", "People"),
                                    ("rooms", "hash", "Rooms", "Rooms"),
                                    ("directory", "org", "Org", "Org chart and directory"),
                                    ("calendar", "calendar", "Calendar",
                                     "Calendar — meetings, holidays, deadlines and leave"),
                                    ("announcements", "megaphone", "News", "News — studio and team announcements"),
                                    ("transfers", "download", "Files", "File transfers"),
                                    ("myspace", "note", "My space",
                                     "My space — notes, to-dos and files only you can see")]:
            b = RailButton(ic, tip, label)
            b.clicked.connect(lambda _=False, k=key: self.rail_clicked(k))
            self.rail_group.addButton(b)
            self.rail[key] = b
            rl.addWidget(b, 0, Qt.AlignHCenter)
        rl.addStretch(1)
        self.b_pin = RailButton("on_top", "Keep on top of other windows")
        self.b_pin.clicked.connect(lambda: self.set_on_top(self.b_pin.isChecked()))
        self.b_pin.hide()
        rl.addWidget(self.b_pin, 0, Qt.AlignHCenter)
        self.b_compact = RailButton("compact", "Compact view — dock a narrow window to the side of the screen "
                                    "(Ctrl+Shift+M)")
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
        self.rail_level = 0                             # 0 full, 1 no labels, 2 tighter, 3 tightest (_fit_rail)
        rail.installEventFilter(self)                   # its height changes with the window and the bars above it
        self.stripe = None
        if T.FESTIVAL:
            from client.ui.festive import FestiveStripe
            self.stripe = FestiveStripe()
            body.addWidget(self.stripe)
        body.addWidget(rail)

        # ---- compact view: the rail becomes a tab bar along the bottom (like a phone app)
        self.tabs = CompactTabs([("chats", "chat", "Chats"), ("contacts", "users", "People"),
                                 ("calendar", "calendar", "Calendar"), ("transfers", "download", "Files"),
                                 ("more", "menu", "More")], self._compact_tab, self._compact_badge)
        self.tabs.clicked.connect(self._compact_tab_clicked)
        self.tabs.hide()
        outer.insertWidget(outer.count() - 1, self.tabs)          # above the licence line
        for b in self.rail.values():
            b.changed.connect(self.tabs.update)

        # ---- sidebar
        self.sidebar = Sidebar(store)
        self.sidebar.open_conv.connect(self.open_conv)
        self.sidebar.new_room.connect(self.new_room)
        self.sidebar.conv_menu.connect(self.conv_menu)
        self.sidebar.go_page.connect(lambda key: (self.rail[key].setChecked(True), self.rail_clicked(key)))
        self.sidebar.show_saved.connect(self.show_saved)
        self.sidebar.full_view.connect(lambda: self.set_compact(False))
        self.sidebar.toast.connect(self.toast)
        self.sidebar.search_messages.connect(lambda q: SearchDialog(self, q).exec())
        self._sidebar_want = 330                        # the width asked for; the window may leave less room
        self._set_sidebar_width(config.get("sidebar_width", 330), save=False)
        body.addWidget(self.sidebar)
        from client.ui.sidebar import SidebarEdge
        self.sidebar_edge = SidebarEdge(self.sidebar)
        self.sidebar_edge.moved.connect(lambda w: self._set_sidebar_width(w, save=False))
        self.sidebar_edge.released.connect(lambda: self._set_sidebar_width(self.sidebar.width()))
        body.addWidget(self.sidebar_edge)

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
        from client.ui.calendar_page import CalendarPage
        self.calendar = CalendarPage(self)
        for w in (self.home, self.chat, self.announcements, self.transfers_page, self.directory, self.calendar):
            self.stack.addWidget(w)
        body.addWidget(self.stack, 1)
        from client.ui.thread_panel import ThreadPanel
        self.thread_panel = ThreadPanel(self)
        self.thread_panel.installEventFilter(self)       # opening / closing it changes the room for the list
        body.addWidget(self.thread_panel)
        self.stack.currentChanged.connect(
            lambda _i: self.stack.currentWidget() is not self.chat and self.thread_panel.close_thread())
        self.stack.currentChanged.connect(lambda _i: self.tabs.update())

        self.toast_label = plain(QLabel(self))
        self.toast_label.setAlignment(Qt.AlignCenter)
        self.toast_label.setStyleSheet(f"background: {T.TOOLTIP}; color: #eef0f5; border-radius: 16px;"
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
        store.thread_message.connect(self._on_thread_message)
        store.announcement.connect(self._on_announcement)
        store.room_removed.connect(self._room_removed)
        store.users_changed.connect(self._check_open_conv)
        store.update_available.connect(self._on_update_available)
        store.reminder_fired.connect(self._show_reminder)
        store.event_invite.connect(self._on_event_invite)
        store.calendar_changed.connect(self._refresh_upcoming_soon)
        self._meeting_timer = QTimer(self, interval=20_000, timeout=self._check_meetings)
        self._meeting_timer.start()
        self._upcoming_timer = QTimer(self, singleShot=True, interval=1500, timeout=self._refresh_upcoming)
        self._reminded = set()
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
        QShortcut(QKeySequence("Ctrl+Shift+S"), self, activated=lambda: self.chat.take_screenshot())
        QShortcut(QKeySequence("Ctrl+/"), self, activated=self.show_shortcuts)
        QShortcut(QKeySequence("Alt+Shift+Down"), self, activated=self.next_unread)
        QShortcut(QKeySequence("Alt+Shift+Up"), self, activated=lambda: self.next_unread(back=True))
        QShortcut(QKeySequence("F1"), self, activated=self.show_shortcuts)
        for keys, step in (("Ctrl+=", 1), ("Ctrl++", 1), ("Ctrl+-", -1), ("Ctrl+0", 0)):
            QShortcut(QKeySequence(keys), self, activated=lambda step=step: self.chat.zoom(step))
        from client.ui import chat_view
        from client.ui.widgets import set_shot_handler, set_shot_statuses
        chat_view.ZOOM["pct"] = int(config.get("chat_zoom", 100) or 100)
        set_shot_handler(self.show_shot)
        set_shot_statuses(lambda shot: (store.shot_status(shot) or (None,))[0])
        # a shot's new status shows next to its name in the open chat
        self._shots_redraw = QTimer(self, singleShot=True, interval=300,
                                    timeout=lambda: self.chat.conv and self.chat.render_all())
        store.shots_changed.connect(lambda _s: self._shots_redraw.start())
        self._drafts_timer = QTimer(self, interval=15_000, timeout=self._save_drafts)
        self._drafts_timer.start()
        from client.ui.popups import PopupStack
        self.popup_stack = PopupStack(store, self._quick_reply, self._popup_open, screen=self.screen,
                                      below=lambda: list(self.reminder_cards.values()))
        store.prefs_changed.connect(self._prefs_changed)
        self._focus_timer = QTimer(self, interval=20_000, timeout=self._update_focus)
        self._focus_timer.start()
        self.chat.back.connect(lambda: self._compact_show("list"))
        self.rail["chats"].setChecked(True)
        self.sidebar.show_page("chats")

    # ================================================================ tray
    def _make_tray(self):
        self.tray = QSystemTrayIcon(self.base_icon, self)
        self.tray.setToolTip("Quillo")
        m = QMenu()
        m.addAction("Open Quillo", self.show_normal)
        self.tray_compact = m.addAction("Compact view", lambda: (self.show_normal(), self.set_compact(not self.compact)))
        self.tray_compact.setCheckable(True)
        status_menu = m.addMenu("Status")
        for st in P.STATUSES:
            status_menu.addAction(status_dot(T.STATUS_COLORS[st]), T.STATUS_LABELS[st],
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
        target = self.last_notified_conv
        if self.compact and self.isVisible():
            self._compact_show("content")
        self.show_normal()
        if target == "announcements":
            self.rail["announcements"].setChecked(True)
            self.rail_clicked("announcements")
        elif target and target.startswith("calendar:"):
            self.open_calendar(datetime.date.fromisoformat(target[9:]))
        elif target and self.store.conv_exists(target):
            self.open_conv(target)
        QTimer.singleShot(150, lambda: bring_to_front(self))    # Windows sometimes needs a second try

    def show_normal(self):
        self.show()
        self.setWindowState((self.windowState() & ~Qt.WindowMinimized) | Qt.WindowActive)
        self.raise_()
        self.activateWindow()
        bring_to_front(self)
        T.dark_title_bar(self)
        if self.config["compact_mode"] and not self.compact:
            QTimer.singleShot(0, lambda: self.set_compact(True))
        if getattr(self, "_tour_pending", False):
            QTimer.singleShot(800, self._maybe_tour)

    # ============================================================ compact view
    COMPACT_WIDTHS = (380, 440, 520)            # Settings: narrow, normal, wide
    TAB_PAGES = ("chats", "contacts", "calendar", "transfers")

    @property
    def COMPACT_WIDTH(self):
        w = int(self.config.get("compact_width") or 440)
        return w if w in self.COMPACT_WIDTHS else 440

    def _compact_tab(self):
        """The bottom tab that looks selected: the page's own tab, or More for the pages kept in its menu."""
        checked = next((k for k, b in self.rail.items() if b.isChecked()), None)
        if checked in self.TAB_PAGES:
            return checked
        return "more" if checked else None

    def _compact_badge(self, key):
        if key == "more":                       # News lives in the More menu: its count shows on More
            b = self.rail["announcements"]
        else:
            b = self.rail.get(key)
        return (b.badge, b.badge_kind) if b is not None else (0, "alert")

    def _compact_tab_clicked(self, key):
        if key == "more":
            self.compact_more_menu()
        else:
            self.rail_clicked(key)

    def compact_more_menu(self):
        """Compact view's More tab: me and my status, the other pages, and the window's own switches."""
        me = self.store.me
        m = QMenu(self)
        self._menu_note(m, "", self._me_card())
        m.addSeparator()
        cur = me.get("status", "online")
        sm = m.addMenu(status_dot(T.STATUS_COLORS.get(cur, T.STATUS_COLORS["online"])),
                       f"Status: {T.STATUS_LABELS.get(cur, cur)}")
        for st in P.STATUSES:
            a = sm.addAction(status_dot(T.STATUS_COLORS[st]), T.STATUS_LABELS[st] + ("\t✓" if st == cur else ""),
                             lambda st=st: self.set_status(st))
            if st == cur:
                f = a.font()
                f.setBold(True)
                a.setFont(f)
        m.addAction(icon("smile", T.TEXT, 16), menu_text("Profile photo & status" + ELLIPSIS),
                    self.edit_status_message)
        m.addSeparator()
        news = self.rail["announcements"].badge
        m.addAction(icon("home", T.TEXT, 16), "Home", self.go_home)
        for key, ic, label in (("rooms", "hash", "Rooms"), ("directory", "org", "Org chart"),
                               ("announcements", "megaphone", f"News  ·  {news} new" if news else "News"),
                               ("myspace", "note", "My space")):
            on = self.rail[key].isChecked()
            a = m.addAction(icon(ic, T.ACCENT if on else T.TEXT, 16), label, lambda k=key: self.rail_clicked(k))
            if on:
                f = a.font()
                f.setBold(True)
                a.setFont(f)
        m.addSeparator()
        m.addAction(icon("search", T.TEXT, 16), "Search messages\tCtrl+F", self.show_search)
        m.addAction(icon("bookmark", T.TEXT, 16), "Saved for later", self.show_saved)
        m.addMenu(self.focus_menu(m))
        m.addAction(icon("clock", T.TEXT, 16), "New reminder" + ELLIPSIS, self.new_reminder)
        m.addSeparator()
        top = m.addAction(icon("on_top", T.TEXT, 16), "Keep on top of other windows")
        top.setCheckable(True)
        top.setChecked(self.b_pin.isChecked())
        top.toggled.connect(self.set_on_top)
        m.addAction(icon("compact", T.TEXT, 16), "Leave compact view\tCtrl+Shift+M", lambda: self.set_compact(False))
        dark = T.DARK
        m.addAction(icon("palette", T.TEXT, 16), "Switch to light mode" if dark else "Switch to dark mode",
                    lambda: self.switch_mode("light" if dark else "midnight"))
        m.addAction(icon("settings", T.TEXT, 16), "Settings", self.show_settings)
        m.addAction(icon("logout", T.DANGER, 16), "Sign out", self.confirm_logout)
        m.exec(self._above_tab("more", m.sizeHint()))

    def _above_tab(self, key, size):
        """A pop-up above a bottom tab, its right edge level with the tab's, kept on the screen."""
        r = self.tabs.tab_rect(key)
        g = self.tabs.mapToGlobal(QPoint(r.right(), 0))
        x, y = g.x() - size.width(), g.y() - size.height() - 4
        screen = self.tabs.screen()
        if screen is not None:
            area = screen.availableGeometry()
            x = max(area.left(), min(x, area.right() + 1 - size.width()))
            y = max(area.top(), y)
        return QPoint(x, y)

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
        self.rail_frame.setVisible(not on)          # compact: the tab bar along the bottom instead
        if self.stripe:
            self.stripe.setVisible(not on)
        self.tabs.setVisible(on)
        self.sidebar.b_full.setVisible(on)          # ⤢ above the list (and in the chat header): back to full size
        self.chat.set_compact(on)
        self.b_pin.setVisible(on)
        QTimer.singleShot(0, self._fit_rail)
        if on:
            self._normal_geometry = self.saveGeometry()
            if self.isMaximized() or self.isFullScreen():
                self.showNormal()
            self.setMinimumSize(360, 480)
            self.sidebar.setMinimumWidth(0)
            self.sidebar.setMaximumWidth(16777215)
            self.sidebar_edge.hide()
            self._dock()
            self._compact_show("content" if self.stack.currentWidget() is self.chat and self.chat.conv
                               else "list")
            self.set_on_top(self.config["compact_on_top"], save=False)
        else:
            self.set_on_top(False, save=False)
            self.sidebar_edge.show()
            self.sidebar.show()
            self.stack.show()
            self._set_sidebar_width(self.config.get("sidebar_width", 330), save=False)
            self.setMinimumSize(960, 600)
            if self._normal_geometry:
                self.restoreGeometry(self._normal_geometry)
            else:
                self.resize(1280, 800)

    def _set_sidebar_width(self, w, save=True):
        from client.ui.sidebar import Sidebar
        try:
            w = int(w)
        except (TypeError, ValueError):
            w = Sidebar.WIDTH
        w = max(Sidebar.MIN_WIDTH, min(Sidebar.MAX_WIDTH, w))
        self._sidebar_want = w
        self._fit_columns()
        if save:
            self.config["sidebar_width"] = w
            self.config.save()

    # ---- a small window: the columns and the rail give way instead of overlapping
    CHAT_MIN = 380          # the chat never gets narrower than this while the list or a thread is beside it

    def _fit_columns(self):
        """The chat list gets the width asked for (dragged or saved) when there is room, less when the window is
        narrow, and steps aside while a thread is open in a window too narrow for three columns. The asked-for
        width is kept (not saved smaller), so it comes back when the window grows."""
        from client.ui.sidebar import Sidebar
        if self.compact:
            return
        if not hasattr(self, "thread_panel"):         # still being built
            self.sidebar.setFixedWidth(self._sidebar_want)
            return
        thread = self.thread_panel.minimumWidth() if not self.thread_panel.isHidden() else 0
        used = self.rail_frame.width() + (self.stripe.width() if self.stripe else 0) + self.sidebar_edge.width()
        room = self.width() - used - thread - self.CHAT_MIN
        fits = room >= Sidebar.MIN_WIDTH or not thread
        self.sidebar.setFixedWidth(max(Sidebar.MIN_WIDTH, min(self._sidebar_want, room)))
        if fits == self.sidebar.isHidden():
            self.sidebar.setVisible(fits)
            self.sidebar_edge.setVisible(fits)

    RAIL_LEVELS = 5         # 0 full, 1 no labels, 2 tight gaps, 3 Search in the Me menu, 4 Compact view too

    def _rail_need(self, level):
        """The height the rail's contents take at a level (see RAIL_LEVELS)."""
        nav = RailButton.H if level == 0 else RailButton.H_COMPACT
        toggles = ([self.b_pin] if not self.b_pin.isHidden() else []) + [self.b_compact, self.b_search, self.b_settings]
        if level >= 3:
            toggles.remove(self.b_search)
        if level >= 4:
            toggles.remove(self.b_compact)
        top, bottom, gap, spacing, me_gap = (11, 10, 13, 2, 6) if level < 2 else (6, 4, 4, 0, 2)
        items = 1 + len(self.rail) + len(toggles) + 1              # logo, pages, toggles, me
        return (top + bottom + self.home_logo.height() + gap + nav * len(self.rail)
                + sum(b.height() for b in toggles) + me_gap + self.me_btn.height() + spacing * (items - 1))

    def _fit_rail(self):
        """A short window (or one with the focus / update / reconnect bar showing): first the page labels go
        (the tooltips still name the pages), then the gaps shrink, then Search and Compact view move from the
        rail into the Me menu - so the icons never run into each other."""
        h = self.rail_frame.height()
        last = self.RAIL_LEVELS - 1
        level = next((lv for lv in range(last) if self._rail_need(lv) <= h), last)
        if level == self.rail_level:
            return
        self.rail_level = level
        for b in self.rail.values():
            b.set_compact(level >= 1)
        rl = self.rail_layout
        tight = level >= 2
        rl.setContentsMargins(0, 6 if tight else 11, 0, 4 if tight else 10)
        rl.setSpacing(0 if tight else 2)
        logo_gap = rl.itemAt(rl.indexOf(self.home_logo) + 1)
        if logo_gap is not None and logo_gap.spacerItem() is not None:
            logo_gap.spacerItem().changeSize(0, 4 if tight else 13)
        me_gap = rl.itemAt(rl.indexOf(self.me_btn) - 1)
        if me_gap is not None and me_gap.spacerItem() is not None:
            me_gap.spacerItem().changeSize(0, 2 if tight else 6)
        self.b_search.setVisible(level < 3)
        self.b_compact.setVisible(level < 4)
        rl.invalidate()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if not hasattr(self, "toast_label"):            # still being built
            return
        self._fit_columns()
        if self.toast_label.isVisible():
            QTimer.singleShot(0, self._place_toast)
        QTimer.singleShot(0, self._fit_columns)        # again once the thread panel has followed the new width

    def eventFilter(self, obj, e):
        t = e.type()
        if obj is getattr(self, "rail_frame", None) and t == QEvent.Resize:
            self._fit_rail()
        elif obj is getattr(self, "thread_panel", None) and t in (QEvent.Show, QEvent.Hide):
            QTimer.singleShot(0, self._fit_columns)
        elif obj is getattr(self, "home_logo", None) and t in (QEvent.Enter, QEvent.Leave):
            # the logo is the way Home: a soft halo on hover, like the rail buttons' pill
            obj.setStyleSheet(f"background: {T.SURFACE_HOVER}; border-radius: {T.RADIUS_M}px;"
                              if t == QEvent.Enter else "background: transparent;")
        return super().eventFilter(obj, e)

    def _dock(self):
        """Compact view: full height against the right (or left, in Settings) edge of the screen."""
        screen = self.screen() or QApplication.primaryScreen()
        area = screen.availableGeometry()
        frame = self.frameGeometry()
        extra_w = frame.width() - self.width()          # window borders
        extra_h = frame.height() - self.height()        # title bar + borders
        w = self.COMPACT_WIDTH
        self.resize(w, area.height() - extra_h)
        left = self.config.get("compact_side") == "left"
        self.move(area.left() if left else area.right() - w - extra_w + 1, area.top())

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
        from client.ui.widgets import set_link_policy, set_shot_pattern
        self._load_drafts()
        set_link_policy(boot.get("trusted_link_hosts", []), self.config)
        set_shot_pattern(self.store.shot_pattern)
        self._update_focus()
        if self.banner.isVisible() or getattr(self, "_was_offline", False):
            self.toast("Back online")
        self._was_offline = False
        self.banner.hide()
        self.sidebar.set_offline(False)
        self.home.set_name(self.store.me.get("name", ""), self.store.server_name)
        self.setWindowTitle(self._title())
        self._me_changed()
        if self.chat.conv and self.stack.currentWidget() is self.chat:
            if self.store.conv_exists(self.chat.conv):
                self.chat.open(self.chat.conv)
            else:
                self.stack.setCurrentWidget(self.home)
        elif self.chat.conv:                # the cached messages were dropped: reload when the chat is shown
            self.chat_stale = self.store.conv_exists(self.chat.conv)
            if not self.chat_stale:
                self.chat.conv = None
        if boot.get("must_change_password"):
            QTimer.singleShot(300, lambda: self._force_password_change(boot["must_change_password"]))
            return
        # reminders that came due while this PC was off / signed out
        for r in [r for r in self.store.reminders if r.get("state") == 1][:5]:
            self._show_reminder(r, sound=False)
        QTimer.singleShot(900, self._maybe_tour)
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
        QTimer.singleShot(900, self._maybe_tour)

    def _on_connection_lost(self, reason):
        # what people care about: it is being fixed, and what they send is not lost (the outbox keeps it)
        self.banner.setText(f"Can't reach the Quillo server — reconnecting{ELLIPSIS} "
                            "Messages you send will go out when it's back.")
        self.banner.setToolTip(rich_safe(str(reason or "")))
        self.banner.show()
        self._was_offline = True
        self.sidebar.set_offline(True)
        self._show_me(connected=False)

    def _title(self):
        return f"Quillo — {self.store.me.get('name', '')}{SEP}{self.store.server_name}"

    def _show_me(self, connected=True):
        me = self.store.me
        status = me.get("status", "online") if connected else "offline"
        line = ("Reconnecting" + ELLIPSIS) if not connected else (
            self.store.status_text(me) or T.STATUS_LABELS.get(status, status))
        if connected and self.store.focus_until():
            line += f"\nFocus time until {self._until(self.store.focus_until())}"
        self.me_btn.set_me(me.get("name", ""), status, rich_safe(f"{me.get('name', '')}\n{line}"),
                           uid=self.store.my_id)

    @staticmethod
    def _menu_note(menu, text, widget=None):
        """A row at the top of a menu that says something (not a greyed-out, 'unavailable' item)."""
        w = widget
        if w is None:
            w = plain(QLabel(text))
            w.setStyleSheet(f"color: {T.META}; font-size: {T.pt(T.FONT_S)}; padding: 6px 12px 4px 12px;"
                            " background: transparent;")
        a = QWidgetAction(menu)
        a.setDefaultWidget(w)
        menu.addAction(a)
        return a

    def _me_card(self):
        """The Me menu's header: my avatar, my name and what the others see as my status."""
        me = self.store.me
        w = QWidget()
        w.setStyleSheet("background: transparent;")
        row = QHBoxLayout(w)
        row.setContentsMargins(10, 6, 16, 6)
        row.setSpacing(10)
        status = me.get("status", "online") if self.conn.online else "offline"
        av = Avatar(36)
        av.set(me.get("name", ""), me.get("name", ""), status=status, ring=T.PANEL, uid=self.store.my_id)
        row.addWidget(av)
        col = QVBoxLayout()
        col.setSpacing(0)
        name = plain(QLabel(me.get("name", "")))
        name.setStyleSheet(f"font-weight: 700; font-size: {T.pt(T.FONT_M)};")
        col.addWidget(name)
        line = ("Reconnecting" + ELLIPSIS) if not self.conn.online else (
            self.store.status_text(me) or T.STATUS_LABELS.get(status, status))
        sub = plain(QLabel(clip(line, 40)))
        sub.setStyleSheet(f"color: {T.META}; font-size: {T.pt(T.FONT_S)};")
        col.addWidget(sub)
        row.addLayout(col, 1)
        return w

    @staticmethod
    def _beside(anchor, size, gap=6):
        """A pop-up to the right of a rail button, its bottom level with the button's (the rail's lowest button
        opens upwards, so the menu never runs off the bottom of the screen)."""
        g = anchor.mapToGlobal(QPoint(anchor.width() + gap, anchor.height()))
        x, y = g.x(), g.y() - size.height()
        screen = anchor.screen()
        if screen is not None:
            area = screen.availableGeometry()
            x = max(area.left(), min(x, area.right() + 1 - size.width()))
            y = max(area.top(), min(y, area.bottom() + 1 - size.height()))
        return QPoint(x, y)

    def me_menu(self):
        """My name, status choices, status message, settings and sign out."""
        me = self.store.me
        m = QMenu(self)
        self._menu_note(m, "", self._me_card())
        m.addSeparator()
        for st in P.STATUSES:
            current = me.get("status") == st
            # the one I am on: bold with a tick on the right (the status icons leave no room for Qt's own tick)
            a = m.addAction(status_dot(T.STATUS_COLORS[st]),
                            T.STATUS_LABELS[st] + ("\t✓" if current else ""), lambda st=st: self.set_status(st))
            if current:
                f = a.font()
                f.setBold(True)
                a.setFont(f)
        m.addAction(icon("smile", T.TEXT, 16), menu_text("Profile photo & status" + ELLIPSIS),
                    self.edit_status_message)
        m.addMenu(self.focus_menu(m))
        m.addAction(icon("clock", T.TEXT, 16), "New reminder" + ELLIPSIS, self.new_reminder)
        m.addSeparator()
        m.addAction(icon("bookmark", T.TEXT, 16), "Saved for later", self.show_saved)
        if self.b_search.isHidden():            # a short window moved these from the rail to here
            m.addAction(icon("search", T.TEXT, 16), "Search messages\tCtrl+F", self.show_search)
        if self.b_compact.isHidden() and not self.compact:
            m.addAction(icon("compact", T.TEXT, 16), "Compact view\tCtrl+Shift+M", lambda: self.set_compact(True))
        m.addAction(icon("list", T.TEXT, 16), "Keyboard shortcuts\tCtrl+/", self.show_shortcuts)
        m.addAction(icon("info", T.TEXT, 16), "Welcome tour", self.show_tour)
        m.addSeparator()
        dark = T.DARK
        m.addAction(icon("palette", T.TEXT, 16), "Switch to light mode" if dark else "Switch to dark mode",
                    lambda: self.switch_mode("light" if dark else "midnight"))
        m.addAction(icon("settings", T.TEXT, 16), "Settings", self.show_settings)
        m.addAction(icon("logout", T.DANGER, 16), "Sign out", self.confirm_logout)
        m.exec(self._beside(self.me_btn, m.sizeHint()))

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
        self.tray.setToolTip(f"Quillo — {total} unread" if total else "Quillo")
        title = self._title()
        self.setWindowTitle(f"({total}) {title}" if total else title)

    def _update_transfers_badge(self):
        # work in progress, not something wrong: the calm accent badge, not the red one of unread messages
        self.rail["transfers"].set_badge(self.transfers_page.active_count(), kind="neutral")

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
        if key == "myspace":
            self.open_my_space()
            return
        self.rail[key].setChecked(True)
        self._compact_show("list" if key in ("chats", "contacts", "rooms") else "content")
        if key in ("chats", "contacts", "rooms"):
            if self.sidebar.isHidden() and not self.compact:
                self.thread_panel.close_thread()        # the list stepped aside for a thread: asked for, it's back
            self.sidebar.show_page(key)
            if self.stack.currentWidget() in (self.announcements, self.transfers_page, self.directory, self.calendar):
                if self.chat.conv and getattr(self, "chat_stale", False):
                    self.chat_stale = False
                    self.chat.open(self.chat.conv)
                self.stack.setCurrentWidget(self.chat if self.chat.conv else self.home)
            # the list marks the chat on the right again (another page cleared it)
            self.sidebar.set_active(self.chat.conv if self.stack.currentWidget() is self.chat else None)
            return
        self.sidebar.set_active(None)               # a page is showing, not a chat: no row looks open
        if key == "directory":
            self.stack.setCurrentWidget(self.directory)
        elif key == "announcements":
            self.stack.setCurrentWidget(self.announcements)
            self.announcements.rebuild()
            QTimer.singleShot(1500, self.announcements.mark_all_read)
        elif key == "transfers":
            self.stack.setCurrentWidget(self.transfers_page)
        elif key == "calendar":
            self.calendar.set_room(None) if self.calendar.room_id else None
            self.stack.setCurrentWidget(self.calendar)

    def open_conv(self, conv):
        if not self.store.conv_exists(conv):
            return
        self.chat.save_draft()                 # before the list redraws: the chat I leave shows its draft
        if not self.isVisible():
            self.show_normal()
        if self.rail_group.checkedButton() in (self.rail["announcements"], self.rail["transfers"],
                                               self.rail["directory"], self.rail["calendar"]):
            self.rail["chats"].setChecked(True)
            self.sidebar.show_page("chats")
        self.sidebar.set_active(conv)
        self.stack.setCurrentWidget(self.chat)
        self._compact_show("content")
        if self.thread_panel.conv not in (None, conv):
            self.thread_panel.close_thread()            # a thread belongs to its chat
        self.chat.open(conv)
        self.store.mark_read(conv)

    def open_thread(self, conv, root_id):
        """Show a thread in the panel on the right (its chat is opened too)."""
        if self.chat.conv != conv or self.stack.currentWidget() is not self.chat:
            self.open_conv(conv)
        self.thread_panel.open_thread(conv, root_id)

    def changeEvent(self, e):
        super().changeEvent(e)
        if e.type() == QEvent.ActivationChange and self.isActiveWindow():
            if self.chat.conv and self.stack.currentWidget() is self.chat:
                self.store.mark_read(self.chat.conv)

    # ===================================================== notifications
    def _on_thread_message(self, msg, is_new):
        """A reply in a thread: told only to the people in it (who started it, or replied), or @mentioned."""
        if not is_new or msg["sender_id"] == self.store.my_id or msg.get("thread_broadcast"):
            return                              # a reply also sent to the chat is announced as a chat message
        root_id = msg["thread_root"]
        if self.thread_panel.showing(root_id) and self.isActiveWindow():
            return
        root = self.store.conversation(msg["conv"]).messages.get(root_id)
        mention = self.store.mentions_me(msg)
        involved = root_id in self.store.my_threads or (root and root["sender_id"] == self.store.my_id)
        if not (involved or mention):
            return
        if not mention and (self.store.is_muted(msg["conv"]) or self.store.me.get("status") == "busy"):
            return
        if self.store.focus_until() and not self.store.gets_through_focus(msg):
            self.focus_missed += 1
            return
        sender = self.store.user_name(msg["sender_id"])
        where = "" if msg["conv"].startswith("u:") else f" in {self.store.title(msg['conv'])}"
        self.notify(f"{sender} replied in a thread{where}", stickers.summary(msg), msg["conv"], msg)

    def _on_message(self, msg, is_new):
        if not is_new or msg["sender_id"] == self.store.my_id or msg["kind"] == "system":
            return
        focus = self.store.focus_until() and not self.store.gets_through_focus(msg)
        if msg["kind"] == "buzz":
            if focus:                        # focus time: a buzz waits like any other message
                self.focus_missed += 1
                return
            self.buzzed(msg)
            return
        if self.is_viewing(msg["conv"]):
            return
        mention = self.store.mentions_me(msg)
        # muted chats and "Do not disturb" stay silent, unless someone @mentions you
        if not mention and (self.store.is_muted(msg["conv"]) or self.store.me.get("status") == "busy"):
            return
        if focus:                            # focus time: only @mentions, my lead and the people I chose
            self.focus_missed += 1
            return
        sender = self.store.user_name(msg["sender_id"])
        where = "" if msg["conv"].startswith("u:") else f" in {self.store.title(msg['conv'])}"
        title = f"{sender} mentioned you{where}" if mention else f"{sender}{where}"
        self.notify(title, stickers.summary(msg), msg["conv"], msg)

    # ======================================================= reminders
    def add_reminder(self, due_at, text="", conv="", message_id=None):
        from client.ui.planner_ui import fmt_due

        def done(reply):
            self.toast(f"⏰ Reminder set for {fmt_due(due_at, inline=True)}" if reply.get("ok")
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
        card.destroyed.connect(lambda *_, rid=r["id"], c=card: (
            self.reminder_cards.get(rid) is c and self.reminder_cards.pop(rid, None),
            QTimer.singleShot(0, self.popup_stack.place)))
        self.reminder_cards[r["id"]] = card
        # one column with the message pop-ups: reminders at the bottom, messages above - never on top of each other
        card.adjustSize()
        card.show()
        card.raise_()
        self.popup_stack.place()
        if sound and self.config["sounds"]:
            play_sound()
        if sound:
            self.last_notified_conv = r.get("conv") or None       # clicking it opens that chat
            self.tray.showMessage("⏰ Reminder", r.get("text") or r.get("snippet", ""), self.base_icon, 5000)

    def buzzed(self, msg):
        """Someone buzzed me: come to the front, open the chat, shake and ring - even on Do not disturb."""
        sender = self.store.user_name(msg["sender_id"])
        if msg["conv"].startswith("r:"):
            sender = f"{sender} in {self.store.title(msg['conv'])}"
        if not self.config["allow_buzz"]:
            self.notify(f"⚡ {sender} buzzed you", "Buzz", msg["conv"])
            return
        hidden = not self.isVisible() or self.isMinimized()
        self.show_normal()
        if hidden:
            # it was out of sight: pop up as the phone-style view on the right, above everything for a while
            self.set_compact(True, remember=False)
            self._dock()
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

    def notify(self, title, text, target, msg=None):
        self.last_notified_conv = target
        if self.config["notifications"]:
            if msg is not None and self.config["quick_reply"]:
                thread = msg.get("thread_root") if not msg.get("thread_broadcast") else None
                self.popup_stack.show(msg["conv"], title, text, msg["sender_id"], thread)
            else:
                self.tray.showMessage(title, clip(text, 200), self.base_icon, 5000)
        if self.config["sounds"]:
            play_sound()
        QApplication.alert(self, 0)

    def _quick_reply(self, conv, text, thread_root):
        """An answer typed into a pop-up: sent like any message (it waits in the outbox if the server is away)."""
        if not self.store.conv_exists(conv):
            return
        self.outbox.add(conv, text=text, thread_root=thread_root)
        self.store.mark_read(conv)

    def _popup_open(self, conv):
        self.show_normal()
        if self.store.conv_exists(conv):
            self.open_conv(conv)

    # ============================================================ focus time
    def focus_menu(self, parent):
        """Focus time: silence everything except @mentions, my lead and a few people I choose."""
        m = QMenu("Focus time", parent)
        m.setIcon(icon("target", T.TEXT, 16))
        until = self.store.focus_until()
        now = datetime.datetime.now()
        if until:
            self._menu_note(m, f"On until {self._until(until)}")
            m.addAction(icon("close", T.TEXT, 16), "End focus time", self.end_focus)
            m.addSeparator()
        for label, minutes in (("30 minutes", 30), ("1 hour", 60), ("2 hours", 120), ("4 hours", 240)):
            m.addAction(label, lambda minutes=minutes: self.start_focus(time.time() + minutes * 60))
        evening = now.replace(hour=18, minute=0, second=0, microsecond=0)
        if now < evening - datetime.timedelta(minutes=30):
            m.addAction("Until 18:00", lambda: self.start_focus(evening.timestamp()))
        m.addSeparator()
        n = len(self.store.focus_people())
        m.addAction(icon("users", T.TEXT, 16), "Who can still reach me" + ELLIPSIS + (f"{SEP}{n} chosen" if n else ""),
                    self.choose_focus_people)
        return m

    @staticmethod
    def _until(ts):
        """'16:00' today, 'tomorrow at 00:40' past midnight, 'Fri 2 Oct at 09:00' further away."""
        from client.ui.planner_ui import fmt_due
        end = datetime.datetime.fromtimestamp(ts)
        return fmt_time(end) if end.date() == datetime.date.today() else fmt_due(ts, inline=True)

    def _focus_reach(self):
        """Who still gets through focus time, as one phrase: '@mentions, Lea Kapoor (your lead) and 2 people you
        chose' - the same words in the bar and in the toast."""
        parts = ["@mentions"]
        lead = self.store.manager_name(self.store.my_id)
        if lead:
            parts.append(f"{lead} (your lead)")
        n = len(self.store.focus_people())
        if n:
            parts.append(f"{n} {'person' if n == 1 else 'people'} you chose")
        return parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1]

    def start_focus(self, until):
        f = dict(self.store.prefs.get("focus") or {})
        f["until"] = until
        self.focus_missed = 0
        self.store.set_pref("focus", f)
        self.toast(f"🎯 Focus time until {self._until(until)} — only {self._focus_reach()} get through")

    def end_focus(self):
        f = dict(self.store.prefs.get("focus") or {})
        f["until"] = 0
        self.store.set_pref("focus", f)

    def choose_focus_people(self):
        from client.ui.dialogs import Dialog, MemberPicker, _buttons
        dlg = Dialog(self, "Who can reach me in focus time", 460)
        note = QLabel("@mentions and the person you report to always get through. Tick anyone else who should.")
        note.setWordWrap(True)
        T.polish(note, muted=True)
        dlg.lay.addWidget(note)
        picker = MemberPicker(self.store, checked=self.store.focus_people())
        dlg.lay.addWidget(picker, 1)
        dlg.lay.addWidget(_buttons(dlg))
        if dlg.exec():
            f = dict(self.store.prefs.get("focus") or {})
            f["people"] = picker.selected()[:50]
            self.store.set_pref("focus", f)

    def _prefs_changed(self, key):
        if key in ("", "focus"):
            self._update_focus()

    def _update_focus(self):
        until = self.store.focus_until() if self.store.me else 0
        was_on = self.focus_bar.isVisible()
        if until:
            waiting = f"{SEP}{self.focus_missed} waiting" if self.focus_missed else ""
            self.focus_label.setText(f"Focus time until {self._until(until)}{SEP}"
                                     f"Only {self._focus_reach()} get through{waiting}")
            self.focus_bar.show()
        else:
            self.focus_bar.hide()
            if was_on:
                n = self.focus_missed
                self.focus_missed = 0
                self.toast("Focus time is over" + (f" — {n} message{'s' if n != 1 else ''} came in" if n else ""))
        self._show_me(connected=self.conn.online)

    # ============================================================ help
    def show_shortcuts(self):
        from client.ui.help import ShortcutsDialog
        ShortcutsDialog(self).exec()

    def show_tour(self):
        from client.ui.help import WelcomeTour
        if getattr(self, "_tour", None) is not None and self._tour.isVisible():
            self._tour.raise_()
            return
        self._tour = WelcomeTour(self)
        self._tour.finished.connect(lambda _r: self._tour_done())
        self._tour.open()                      # not exec(): the chat keeps running behind it

    def _tour_key(self):
        return f"{self.conn.host}:{self.store.me.get('username', '')}"

    def _maybe_tour(self):
        """New people see the welcome tour once, and everyone once after this update."""
        s = self.store
        if not s.me or s.prefs.get("tour_done"):
            return
        if not self.isVisible():               # started with Windows, in the tray: when the window first opens
            self._tour_pending = True
            return
        self._tour_pending = False
        if not getattr(s, "prefs_on_server", False) and self._tour_key() in (self.config.get("tour_seen") or []):
            return
        self.show_tour()

    def _tour_done(self):
        self.store.set_pref("tour_done", 1)
        seen = list(self.config.get("tour_seen") or [])
        if self._tour_key() not in seen:
            self.config["tour_seen"] = (seen + [self._tour_key()])[-20:]
            self.config.save()

    # ============================================================ pinned chats, shot names
    def pin_chat(self, conv, pinned):
        if not self.store.set_pinned(conv, pinned):
            self.toast(f"Up to {self.store.MAX_PINNED} chats can be pinned — unpin one first.")
        elif pinned:
            self.toast(f"📌 {self.store.title(conv)} stays at the top of your chats")

    def next_unread(self, back=False):
        """Alt+Shift+↓: the next chat with unread messages, in the order of the chat list (muted ones last)."""
        s = self.store
        order = s.pinned_chats() + [c.conv for c in sorted(s.convs.values(), key=lambda c: -c.last_ts)]
        seen, convs = set(), []
        for conv in order:
            if conv not in seen and s.conv_exists(conv) and s.conversation(conv).unread:
                seen.add(conv)
                convs.append(conv)
        convs.sort(key=lambda c: s.is_muted(c))            # stable: keeps the list order otherwise
        if not convs:
            self.toast("✅ No unread messages")
            return
        current = self.chat.conv if self.stack.currentWidget() is self.chat else None
        if back:
            convs.reverse()
        target = convs[0] if current not in convs else convs[(convs.index(current) + 1) % len(convs)]
        self.rail["chats"].setChecked(True)
        self.sidebar.show_page("chats")
        self.open_conv(target)

    def save_for_later(self, msg, saved):
        self.store.set_saved(msg, saved)
        self.toast("🔖 Saved for later — find it with the bookmark above your chats" if saved
                   else "Removed from saved")

    def show_saved(self):
        from client.ui.dialogs import SavedDialog
        SavedDialog(self).exec()

    def set_shot_status(self, shot, status, conv=""):
        def done(reply):
            if not reply.get("ok"):
                self.toast(f"Status not set: {reply.get('error')}")
        self.conn.request("shot_status_set", done, shot=shot, status=status, conv=conv or "")

    # ---- drafts: half-typed text stays with its chat, even across a restart
    def _drafts_key(self):
        return f"{self.conn.host}:{self.store.me.get('username', '')}"

    def _load_drafts(self):
        saved = (self.config.get("drafts") or {}).get(self._drafts_key(), {})
        for conv, text in saved.items():
            c = self.store.conversation(conv)
            if text and not c.draft:
                c.draft = text

    def _save_drafts(self):
        if not self.store.me:
            return
        self.chat.save_draft()
        drafts = {c.conv: c.draft for c in self.store.convs.values() if (c.draft or "").strip()}
        all_drafts = dict(self.config.get("drafts") or {})
        if all_drafts.get(self._drafts_key(), {}) != drafts:
            all_drafts[self._drafts_key()] = drafts
            self.config["drafts"] = all_drafts
            self.config.save()

    # ---- "In a meeting" while a calendar meeting of mine runs
    def _meeting_status(self, data, now):
        if not self.config.get("meeting_status", True) or not self.conn.online:
            return
        me = self.store.me
        ongoing = [i for i in data.get("items", []) if i["kind"] == "meeting" and not i["all_day"]
                   and i.get("my_rsvp") != "no" and i["start"] <= now < i["end"]]
        auto = getattr(self, "_auto_meeting", None)
        if ongoing and not auto and not me.get("status_msg") and me.get("status") in ("online", "away"):
            end = max(i["end"] for i in ongoing)
            self._auto_meeting = end
            self.conn.send("set_status", status=me.get("status", "online"), status_msg="In a meeting",
                           status_emoji="📅", status_until=end)
        elif auto and not ongoing:
            self._auto_meeting = None
            if me.get("status_msg") == "In a meeting":           # ended early / was deleted: clear it now
                self.conn.send("set_status", status=me.get("status", "online"), status_msg="", status_emoji="")

    def show_shot(self, name):
        """A shot name (FAL_030) was clicked: everything said about it, in every chat I can see."""
        SearchDialog(self, name).exec()

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
        """A short note over the content: centred on the chat (or page), not on the window, sitting just above
        the chat's composer (on other pages near the bottom), and wrapped if long."""
        self.toast_label.setText(text)
        self._place_toast()
        self.toast_label.raise_()
        self.toast_label.show()
        self.toast_timer.start(ms)
        # a bar shown just before (focus time, reconnect) moves the chat down once the layout runs: follow it
        QTimer.singleShot(0, self._place_toast)

    def _place_toast(self):
        lab = self.toast_label
        text = lab.text()
        central = self.centralWidget()
        if central.layout() is not None:
            central.layout().activate()
        if self.stack.isVisible():
            area = QRect(self.stack.mapTo(self, QPoint(0, 0)), self.stack.size())
        else:
            area = QRect(central.mapTo(self, QPoint(0, 0)), central.size())
        max_w = max(160, min(640, area.width() - 48))
        lab.setWordWrap(False)
        lab.setMinimumSize(0, 0)
        lab.setMaximumSize(16777215, 16777215)
        lab.setText(text)
        lab.adjustSize()
        if lab.width() > max_w:
            lab.setWordWrap(True)
            lab.setFixedWidth(max_w)
            lab.setFixedHeight(lab.heightForWidth(max_w))
        x = area.center().x() - lab.width() // 2
        composer = getattr(self.chat, "composer", None)
        if (self.stack.isVisible() and self.stack.currentWidget() is self.chat and composer is not None
                and composer.isVisible()):
            # above everything that sits on the composer (scheduled line, uploads, outbox), over the messages
            blocks = [composer.parentWidget()] + [getattr(self.chat, n, None) for n in ("uploads", "outbox_strip")]
            top = min(w.mapTo(self, QPoint(0, 0)).y() for w in blocks if w is not None and w.isVisible())
            y = top - lab.height() - 8
        else:
            y = area.bottom() - lab.height() - 28
        lab.move(max(8, min(x, self.width() - lab.width() - 8)), max(8, y))

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
    def send_file(self, conv, path, caption=""):
        if os.path.isdir(path):
            self.send_folder(conv, path)
            return
        if not os.path.isfile(path):
            return
        size = os.path.getsize(path)
        if self.store.max_file_size and size > self.store.max_file_size:
            self.toast(f"“{os.path.basename(path)}” is too large (max {P.human_size(self.store.max_file_size)}).")
            return
        self.transfers.upload(path, conv, caption)

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
        # Through the outbox: if the chat connection is down right now (it reconnects separately from the
        # upload), the file is posted when it is back - not "File not sent" after a 20 GB upload.
        self.outbox.add(t.conv, text=t.caption, file_id=t.file_id, label=f"📎 {t.name}",
                        thread_root=getattr(t, "thread_root", None))

    def _outbox_refused(self, item, error):
        """The server would not take a message: say so, and put the text back in its own chat."""
        what = "File" if item.get("file_id") else "Sticker" if item.get("sticker") else "Message"
        self.toast(f"{what} to {self.store.title(item['conv'])} not sent: {error}")
        text = item.get("text", "")
        if not text or item.get("file_id") or item.get("sticker"):
            return
        c = self.store.conversation(item["conv"])
        if self.chat.conv == item["conv"] and not self.chat.input.toPlainText():
            self.chat.input.setPlainText(text)
        elif not c.draft:
            c.draft = text                  # waiting in that chat's box, not in the one open now

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
            try:                             # a half-finished download of another file with this name
                os.remove(path + ".part")
            except OSError:
                pass
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
        if room and QMessageBox.question(self, "Leave room", rich_safe(f"Leave “{room['name']}”?")) == QMessageBox.Yes:
            self.conn.request("room_leave", lambda r: None if r.get("ok") else self.toast(r.get("error")),
                              room_id=room_id)

    def buzz_conv(self, conv):
        """Buzz a person or a whole room (from the chat header, the chat list or the people list)."""
        if not self.conn.online:
            self.toast("Not connected to the server")
            return
        if not getattr(self.store, "buzz_enabled", False):
            self.toast("Buzz is switched off on this server")
            return

        def done(reply):
            if reply.get("ok"):
                self.store.add_message(reply["message"])
                self.toast("⚡ Buzzed " + ("the room" if conv.startswith("r:") else self.store.title(conv)))
            else:
                self.toast(reply.get("error", "Not sent"))
        self.conn.request("buzz", done, conv=conv)

    def go_home(self):
        """The logo: back to the Home screen from anywhere."""
        self.rail["chats"].setChecked(True)
        self.sidebar.show_page("chats")
        self.sidebar.set_active(None)
        self.stack.setCurrentWidget(self.home)
        self._compact_show("content")
        self.home.rebuild()

    # ======================================================== calendar
    def open_calendar(self, day=None, room_id=None, view=None):
        """The calendar page (a room's own calendar when room_id is given), at a day."""
        self.rail["calendar"].setChecked(True)
        self._compact_show("content")
        self.stack.setCurrentWidget(self.calendar)
        if room_id is not None or self.calendar.room_id:
            self.calendar.room_id = room_id
            self.calendar.set_room(room_id)
        if day:
            self.calendar.go_to(day, view)

    def _refresh_upcoming_soon(self):
        self._upcoming_timer.start()

    def _refresh_upcoming(self):
        """The next two weeks for Home and the meeting reminders."""
        if not self.conn.online:
            return
        today = datetime.date.today()

        def done(reply):
            if reply.get("ok"):
                self.store.calendar = reply
                self.home.schedule()
        self.conn.request("cal_range", done, start=today.isoformat(),
                          end=(today + datetime.timedelta(days=14)).isoformat())

    def _check_meetings(self):
        """Remind me a few minutes before a meeting (Settings: how many), once per meeting."""
        minutes = int(self.config.get("meeting_reminder_min", 10) or 0)
        data = self.store.calendar or {}
        now = time.time()
        if data:
            self._meeting_status(data, now)
        if not minutes or not data:
            return
        if not hasattr(self, "_upcoming_fetched") or now - self._upcoming_fetched > 1800:
            self._upcoming_fetched = now
            self._refresh_upcoming()
        for item in data.get("items", []):
            if item["kind"] != "meeting" or item.get("my_rsvp") == "no" or item["all_day"]:
                continue
            key = item["id"]
            if key in self._reminded or not (0 < item["start"] - now <= minutes * 60):
                continue
            self._reminded.add(key)
            start = datetime.datetime.fromtimestamp(item["start"])
            room = self.store.rooms.get(int(item["scope_ref"])) if item["scope"] == "room" else None
            where = f"{SEP}{room['name']}" if room else (f"{SEP}{item['location']}" if item.get("location") else "")
            target = f"r:{room['id']}" if room else f"calendar:{start.date().isoformat()}"
            self.last_notified_conv = target
            mins = max(1, round((item['start'] - now) / 60))
            self.tray.showMessage(f"📅 {item['title']} at {fmt_time(start)}",
                                  f"In {mins} minute{'s' if mins != 1 else ''}{where}", self.base_icon, 8000)
            self.toast(f"📅 {item['title']} at {fmt_time(start)}{where}", 6000)
            if self.config["sounds"]:
                play_sound()

    def _on_event_invite(self, ev):
        self.notify(f"📅 {ev.get('from', 'Someone')} invited you", f"{ev.get('title', '')}{SEP}{ev.get('when', '')}",
                    "calendar:" + datetime.date.today().isoformat())

    def open_my_space(self):
        self.rail["chats"].setChecked(True)
        self.sidebar.show_page("chats")
        self.open_conv(P.direct_conv(self.store.my_id))

    def conv_menu(self, conv, pos):
        m = QMenu(self)
        kind, target = P.parse_conv(conv)
        mine = kind == "u" and target == self.store.my_id
        # the chat itself: open, read, pin, mute
        m.addAction(icon("chat", T.TEXT, 16), "Open chat", lambda: self.open_conv(conv))
        if self.store.conversation(conv).unread:
            m.addAction(icon("check_all", T.TEXT, 16), "Mark as read", lambda: self.store.mark_read(conv))
        pinned = self.store.is_pinned(conv)
        m.addAction(icon("pin", T.TEXT, 16), "Unpin" if pinned else "Pin", lambda: self.pin_chat(conv, not pinned))
        muted = self.store.is_muted(conv)
        m.addAction(icon("bell" if muted else "bell_off", T.TEXT, 16),
                    "Unmute notifications" if muted else "Mute notifications",
                    lambda: self.set_muted(conv, not muted))
        if mine:
            m.exec(pos)
            return
        # doing something with the person or the room
        m.addSeparator()
        if getattr(self.store, "buzz_enabled", False):
            m.addAction(icon("zap", T.TEXT, 16), "Buzz the room" if kind == "r" else "Buzz",
                        lambda: self.buzz_conv(conv))
        if kind == "u":
            m.addAction(icon("attachment", T.TEXT, 16), "Send files" + ELLIPSIS, lambda: self._send_files_to(conv))
            if self.store.perm("create_rooms"):
                m.addAction(icon("hash", T.TEXT, 16), "Create room with this person",
                            lambda: self.new_room([target]))
            m.addSeparator()
            m.addAction(icon("screen", T.TEXT, 16), "Share my screen" + ELLIPSIS,
                        lambda: self.screens.invite(target, "offer"))
            m.addAction(icon("eye", T.TEXT, 16), self.store.screen_view_label(target),
                        lambda: self.screens.invite(target, "request"))
            self.add_manage_actions(m, target)
        else:
            m.addAction(icon("users", T.TEXT, 16), "Members", lambda: self.show_room_info(conv))
            m.addAction(icon("attachment", T.TEXT, 16), "Send files" + ELLIPSIS, lambda: self._send_files_to(conv))
            if not self.store.rooms.get(target, {}).get("auto"):
                m.addSeparator()
                m.addAction(icon("logout", T.DANGER, 16), "Leave room", lambda: self.leave_room(target))
        m.exec(pos)

    # ======================================================== policy / updates
    def _quit_for_update(self):
        """Quit without questions: the update installer is waiting to replace Quillo's files."""
        self.quitting = True
        self.screens.stop()
        self.conn.logout()
        self.tray.hide()
        QApplication.quit()

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

    @staticmethod
    def _per_user_install():
        """Installed "just for me" (under %LOCALAPPDATA%): updating needs no administrator."""
        base = os.path.normcase(os.path.abspath(os.environ.get("LOCALAPPDATA", "") or "~"))
        return os.path.normcase(os.path.abspath(sys.executable)).startswith(base + os.sep)

    def _on_update_available(self, info):
        if not info or not self._is_newer(info["version"]) or not getattr(sys, "frozen", False):
            return
        self.pending_update = info
        if self._is_admin_user() or self._per_user_install():
            self.update_label.setText(f"Quillo {info['version']} is available.")
            self.update_btn.show()
        else:
            self.update_label.setText(f"Quillo {info['version']} is available — "
                                      "please ask IT to update this PC.")
            self.update_btn.hide()
        self.update_bar.show()

    def check_for_updates(self, parent=None):
        """Settings > Check for updates: ask the server now and say what it offers."""
        from common.version import APP_VERSION

        def done(reply):
            info = reply.get("update") if reply.get("ok") else None
            if not reply.get("ok"):
                QMessageBox.information(parent or self, "Updates", reply.get("error", "Not connected"))
            elif info and self._is_newer(info["version"]):
                self._on_update_available(info)
                QMessageBox.information(parent or self, "Updates",
                                        f"Quillo {info['version']} is available (this PC has {APP_VERSION}).\n"
                                        + ("Use 'Install now' in the bar at the top of the window."
                                           if self.update_btn.isVisible() else "Please ask IT to update this PC."))
            else:
                QMessageBox.information(parent or self, "Updates", f"Quillo {APP_VERSION} is up to date.")
        self.conn.request("update_check", done)

    def install_update(self):
        info = self.pending_update
        if not info:
            return
        import tempfile
        dest = os.path.join(tempfile.gettempdir(), "LANMessenger",
                            os.path.basename(str(info["name"]).replace("\\", "/")) or "LANMessenger-update.exe")
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        self.update_btn.setEnabled(False)
        self.update_label.setText(f"Downloading Quillo {info['version']}{ELLIPSIS}")
        t = self.transfers.download({"id": "client-update", "name": info["name"], "size": info["size"]},
                                    dest_path=dest, hidden=True)

        def finished(tr):
            if tr.state != "done":
                self.update_label.setText(f"Update download failed: {tr.error}")
                self.update_btn.setEnabled(True)
                return
            self.update_label.setText(f"Installing the update — Quillo restarts by itself{ELLIPSIS}")
            # The installer closes this app (Restart Manager) and opens it again afterwards.
            args = "/SILENT /SUPPRESSMSGBOXES /NORESTART /UPDATE"
            if self._per_user_install():         # same place, same mode: no administrator prompt
                rc = ctypes.windll.shell32.ShellExecuteW(None, "open", dest, args + " /CURRENTUSER", None, 1)
            else:
                rc = ctypes.windll.shell32.ShellExecuteW(None, "runas", dest, args, None, 1)
            if rc <= 32:
                self.update_label.setText("The update was not started (administrator permission needed).")
                self.update_btn.setEnabled(True)
                return
            # step aside so the installer can replace the files; it opens Quillo again when it is done
            QTimer.singleShot(1500, self._quit_for_update)
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
        first = first_name(u["name"])
        menu.addSeparator()
        menu.addAction(icon("key", T.TEXT, 16), menu_text(f"Reset {first}'s password{ELLIPSIS}"),
                       lambda: self.reset_user_password(uid))
        menu.addAction(icon("power", T.DANGER, 16), menu_text(f"Disable {first}'s account{ELLIPSIS}"),
                       lambda: self.disable_user(uid))

    def reset_user_password(self, uid):
        """A new password for someone (HR / IT): one field with the show-password eye, and what happens next."""
        from PySide6.QtWidgets import QDialogButtonBox, QLineEdit
        from common.icons import add_show_password
        from client.ui.dialogs import Dialog, _buttons
        name = self.store.user_name(uid)
        dlg = Dialog(self, "Reset password", 420)
        lab = plain(QLabel(f"New password for {name}"))
        lab.setStyleSheet("font-weight: 700;")
        dlg.lay.addWidget(lab)
        pw = QLineEdit()
        pw.setEchoMode(QLineEdit.Password)
        add_show_password(pw)
        dlg.lay.addWidget(pw)
        note = plain(QLabel(f"{first_name(name)} is signed out right away and signs in with this password."))
        note.setWordWrap(True)
        T.polish(note, muted=True)
        dlg.lay.addWidget(note)
        bb = _buttons(dlg, "Reset password", ok_enabled=False)
        ok = bb.button(QDialogButtonBox.Ok)
        pw.textChanged.connect(lambda t: ok.setEnabled(bool(t.strip())))
        dlg.lay.addWidget(bb)
        pw.setFocus()
        if dlg.exec() and pw.text().strip():
            self.conn.request("manage_user", lambda r: QMessageBox.information(
                self, "Reset password", f"{name}'s password was changed." if r.get("ok")
                else r.get("error", "Failed")), user_id=uid, action="reset_password", password=pw.text())

    def disable_user(self, uid):
        name = self.store.user_name(uid)
        if QMessageBox.question(self, "Disable account", rich_safe(
                f"Disable {name}'s account? They are signed out and "
                "cannot sign in until the admin enables it again."),
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes:
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
        # A copy started with Windows carries --minimized. Passing it on brought the restarted
        # Quillo back hidden in the tray, so it looked as if it never restarted.
        args = [a for a in args if a != "--minimized"]
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
        if QMessageBox.question(self, "New look", "Restart Quillo now to switch to "
                                f"{'light' if theme == 'light' else 'dark'} mode?") == QMessageBox.Yes:
            self.restart()
        else:
            self.toast("The new look is used from the next start.")

    def focus_search(self):
        """Ctrl+K / Home's New chat: the Chats search, which finds every chat, person and room (Enter opens the
        first match, arrow keys pick another)."""
        if self.sidebar.page != "chats":
            self.rail_clicked("chats")
        if self.compact:
            self._compact_show("list")
        if self.sidebar.isHidden():                     # stepped aside for a thread on a narrow window
            self.thread_panel.close_thread()
        self.sidebar.search.setFocus()
        self.sidebar.search.selectAll()

    def show_search(self):
        SearchDialog(self).exec()

    def show_settings(self):
        SettingsDialog(self).exec()

    def confirm_logout(self):
        if QMessageBox.question(self, "Sign out", "Sign out of Quillo?") == QMessageBox.Yes:
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
            self.tray.showMessage("Quillo", "Still running here in the tray. "
                                  "Right-click the icon to quit.", self.base_icon, 4000)

    def signed_out(self):
        """Sign-out: close everything that belongs to this account so the next person sees none of it."""
        self._save_drafts()
        self.screens.stop()
        for w in list(self.reminder_cards.values()) + list(self.popups.values()):
            w.close()
        self.reminder_cards.clear()
        self.popups.clear()
        self.popup_stack.close_all()
        if getattr(self, "_tour", None) is not None:
            self._tour.close()
            self._tour = None
        self.focus_bar.hide()
        self.focus_missed = 0
        self.chat.conv = None
        self.chat._clear()
        self.stack.setCurrentWidget(self.home)
        self.store.reset()

    def quit(self):
        if self.quitting:
            return
        active = [t for t in self.transfers.transfers if t.active and not getattr(t, "hidden", False)]
        if active and QMessageBox.question(
                self, "Quit", f"{len(active)} file transfer{'s are' if len(active) != 1 else ' is'} still running. "
                "Quit anyway?") != QMessageBox.Yes:
            return
        self.quitting = True
        self._save_drafts()
        self.screens.stop()
        self.popup_stack.close_all()
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
