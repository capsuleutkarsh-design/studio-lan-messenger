"""Help: the keyboard shortcuts sheet (Ctrl+/) and the welcome tour for new people."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton, QStackedWidget, QVBoxLayout, QWidget,
)

from common import theme as T
from common.icons import pixmap
from client.ui.widgets import plain

SHORTCUTS = [
    ("Find your way", [
        ("Ctrl+K", "Jump to a person or a room"),
        ("Ctrl+F", "Search every message"),
        ("Ctrl+/  or  F1", "This list"),
        ("Ctrl+Shift+M", "Compact view (a narrow window on the right)"),
        ("Alt+Shift+↓", "Next chat with unread messages"),
        ("Alt+Shift+↑", "Previous chat with unread messages"),
    ]),
    ("Writing", [
        ("Enter", "Send"),
        ("Shift+Enter", "New line"),
        ("↑  in an empty box", "Edit your last message"),
        ("Esc", "Stop replying or editing"),
        ("@name", "Mention someone (in a room)"),
        ("Ctrl+V", "Paste a screenshot or files to send them"),
        ("Ctrl+Shift+S", "Screenshot: pick an area and send it"),
    ]),
    ("Reading", [
        ("Ctrl +", "Bigger message text"),
        ("Ctrl −", "Smaller message text"),
        ("Ctrl 0", "Normal message text"),
    ]),
    ("Picture viewer", [
        ("←  →", "Previous / next picture in the chat"),
        ("Home  End", "First / last picture"),
        ("Esc", "Close"),
    ]),
]


def _key_chip(text):
    lbl = plain(QLabel(text))
    lbl.setStyleSheet(f"background: {T.SURFACE}; color: {T.TEXT}; border: 1px solid {T.HAIR}; border-radius: 7px;"
                      " padding: 3px 9px; font-family: 'Segoe UI'; font-size: 9pt; font-weight: 700;")
    return lbl


class ShortcutsDialog(QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("Keyboard shortcuts")
        self.setMinimumWidth(640)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(26, 22, 26, 20)
        lay.setSpacing(14)
        title = QLabel("Keyboard shortcuts")
        title.setStyleSheet("font-size: 15pt; font-weight: 800;")
        lay.addWidget(title)
        cols = QHBoxLayout()
        cols.setSpacing(30)
        halves = (SHORTCUTS[:2], SHORTCUTS[2:])
        for half in halves:
            grid = QGridLayout()                 # one grid per column: the descriptions line up
            grid.setHorizontalSpacing(12)
            grid.setVerticalSpacing(6)
            row = 0
            for heading, items in half:
                h = plain(QLabel(heading.upper()))
                h.setStyleSheet(f"color: {T.ACCENT}; font-size: 8pt; font-weight: 800; letter-spacing: 1px;"
                                " padding-top: 8px;")
                grid.addWidget(h, row, 0, 1, 2)
                row += 1
                for keys, what in items:
                    grid.addWidget(_key_chip(keys), row, 0, Qt.AlignLeft)
                    w = plain(QLabel(what))
                    w.setStyleSheet(f"color: {T.MUTED}; font-size: 9.5pt;")
                    grid.addWidget(w, row, 1)
                    row += 1
            grid.setColumnStretch(1, 1)
            grid.setRowStretch(row, 1)
            cols.addLayout(grid, 1)
        lay.addLayout(cols)
        row = QHBoxLayout()
        row.addStretch(1)
        ok = QPushButton("Close")
        T.polish(ok, primary=True)
        ok.clicked.connect(self.accept)
        row.addWidget(ok)
        lay.addLayout(row)


# (icon, title, text, button text, action key)
TOUR = [
    ("smile", "Welcome to Quillo",
     "Chat, share files and folders, and keep up with your studio - all on your own network.\n\n"
     "Four quick steps to get set up. It takes under a minute.", None, None),
    ("user", "Your photo and status",
     "Add a photo so people recognise you, and set a status like 🍽️ Lunch or 🎬 Rendering, so they know "
     "whether to wait.", "Set my photo & status", "profile"),
    ("users", "Find your team",
     "People lists everyone by department. The org chart shows who reports to whom. Click anyone to chat.",
     "Open the org chart", "org"),
    ("hash", "Your rooms",
     "Rooms are group chats for a show, a team or a topic. Your department's room is already there. "
     "Right-click a chat → Pin to the top to keep it above the rest.", "Show my rooms", "rooms"),
    ("folder", "Files, folders and shots",
     "Drag files or whole folders into a chat. Paste a \\\\server\\path and it becomes a card with Open folder. "
     "Shot names like FAL_030 become links to everything said about the shot.\n\n"
     "Press Ctrl+/ any time to see all the shortcuts.", None, None),
]


class WelcomeTour(QDialog):
    """Four short steps on first sign-in (and once for everyone after the update). Skip closes it for good."""

    def __init__(self, ctx):
        super().__init__(ctx)
        self.ctx = ctx
        self.setWindowTitle("Welcome to Quillo")
        self.setFixedSize(520, 440)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.pages = QStackedWidget()
        for ic, title, text, button, action in TOUR:
            self.pages.addWidget(self._page(ic, title, text, button, action))
        lay.addWidget(self.pages, 1)

        foot = QFrame()
        foot.setObjectName("tourfoot")
        foot.setStyleSheet(f"#tourfoot {{ background: {T.PANEL}; border-top: 1px solid {T.HAIR}; }}")
        fl = QHBoxLayout(foot)
        fl.setContentsMargins(20, 12, 20, 12)
        self.skip = QPushButton("Skip")
        T.polish(self.skip, flat=True)
        self.skip.clicked.connect(self.accept)
        fl.addWidget(self.skip)
        fl.addStretch(1)
        self.dots = []
        for _ in TOUR:
            d = QLabel()
            d.setFixedSize(8, 8)
            self.dots.append(d)
            fl.addWidget(d)
        fl.addStretch(1)
        self.back = QPushButton("Back")
        self.back.clicked.connect(lambda: self.go(self.pages.currentIndex() - 1))
        self.next = QPushButton("Next")
        T.polish(self.next, primary=True)
        self.next.clicked.connect(lambda: self.go(self.pages.currentIndex() + 1))
        fl.addWidget(self.back)
        fl.addWidget(self.next)
        lay.addWidget(foot)
        self.go(0)

    def _page(self, ic, title, text, button, action):
        w = QWidget()
        T.bg_pane(w)
        v = QVBoxLayout(w)
        v.setContentsMargins(40, 36, 40, 20)
        v.setSpacing(12)
        box = QLabel()
        box.setFixedSize(76, 76)
        box.setAlignment(Qt.AlignCenter)
        box.setStyleSheet(f"background: {T.ACCENT_SOFT}; border-radius: 38px;")
        box.setPixmap(pixmap(ic, T.ACCENT, 36))
        v.addWidget(box, 0, Qt.AlignHCenter)
        v.addSpacing(6)
        t = plain(QLabel(title))
        t.setAlignment(Qt.AlignCenter)
        t.setStyleSheet("font-size: 16pt; font-weight: 800;")
        v.addWidget(t)
        d = plain(QLabel(text))
        d.setWordWrap(True)
        d.setAlignment(Qt.AlignCenter)
        d.setStyleSheet(f"color: {T.MUTED}; font-size: 10pt;")
        v.addWidget(d)
        if button:
            b = QPushButton(button)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda: self._do(action))
            v.addSpacing(4)
            v.addWidget(b, 0, Qt.AlignHCenter)
        v.addStretch(1)
        return w

    def _do(self, action):
        ctx = self.ctx
        if action == "profile":
            ctx.edit_status_message()
        elif action == "org":
            ctx.rail_clicked("directory")
        elif action == "rooms":
            ctx.rail["rooms"].setChecked(True)
            ctx.rail_clicked("rooms")

    def go(self, i):
        if i >= len(TOUR):
            self.accept()
            return
        i = max(0, i)
        self.pages.setCurrentIndex(i)
        for k, d in enumerate(self.dots):
            d.setStyleSheet(f"background: {T.ACCENT if k == i else T.BORDER}; border-radius: 4px;")
        self.back.setVisible(i > 0)
        last = i == len(TOUR) - 1
        self.next.setText("Start using Quillo" if last else ("Show me" if i == 0 else "Next"))
        self.skip.setVisible(not last)
