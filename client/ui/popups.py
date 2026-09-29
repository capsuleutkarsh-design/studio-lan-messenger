"""New-message pop-ups in the corner of the screen that you can answer without opening Quillo."""

import time

from PySide6.QtCore import QPoint, Qt, QTimer, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QLineEdit, QVBoxLayout, QWidget

from common import theme as T
from client.ui.widgets import Avatar, IconButton, first_name, plain

SHOW_MS = 9000          # how long a pop-up stays when nobody touches it
MAX_POPUPS = 3


class MessagePopup(QWidget):
    replied = Signal(str, str, object)     # conv, text, thread root (or None)
    opened = Signal(str)                   # conv
    gone = Signal(object)

    def __init__(self, store, conv, thread_root=None):
        super().__init__(None, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.store, self.conv, self.thread_root = store, conv, thread_root
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setFixedWidth(380)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)
        card = QFrame()
        card.setObjectName("popcard")
        card.setStyleSheet(f"#popcard {{ background: {T.PANEL}; border: 1px solid {T.BORDER}; border-radius: 16px; }}")
        outer.addWidget(card)
        lay = QVBoxLayout(card)
        lay.setContentsMargins(14, 12, 10, 12)
        lay.setSpacing(8)

        top = QHBoxLayout()
        top.setSpacing(10)
        self.avatar = Avatar(36)
        top.addWidget(self.avatar, 0, Qt.AlignTop)
        col = QVBoxLayout()
        col.setSpacing(1)
        self.title = plain(QLabel())
        self.title.setStyleSheet("font-weight: 700; font-size: 10pt; background: transparent;")
        self.text = plain(QLabel())
        self.text.setWordWrap(True)
        self.text.setStyleSheet(f"color: {T.MUTED}; font-size: 9.5pt; background: transparent;")
        self.text.setMaximumHeight(60)
        col.addWidget(self.title)
        col.addWidget(self.text)
        top.addLayout(col, 1)
        close = IconButton("close", "Close", 26, 12)
        close.clicked.connect(self.close)
        top.addWidget(close, 0, Qt.AlignTop)
        lay.addLayout(top)
        for w in (self.avatar, self.title, self.text):
            w.setCursor(Qt.PointingHandCursor)
            w.mousePressEvent = self._open

        row = QHBoxLayout()
        row.setSpacing(6)
        self.input = QLineEdit()
        self.input.setStyleSheet(f"QLineEdit {{ background: {T.SURFACE}; border: 1px solid {T.HAIR};"
                                 " border-radius: 12px; padding: 6px 10px; }"
                                 f"QLineEdit:focus {{ border: 1px solid {T.ACCENT_FOCUS}; }}")
        self.input.returnPressed.connect(self._send)
        self.input.textChanged.connect(lambda: self.timer.stop())
        row.addWidget(self.input, 1)
        send = IconButton("send", "Send (Enter)", 32, 16, T.ACCENT, T.ACCENT)
        send.clicked.connect(self._send)
        row.addWidget(send)
        lay.addLayout(row)
        self.timer = QTimer(self, singleShot=True, interval=SHOW_MS, timeout=self._maybe_close)
        self.count = 0

    def show_message(self, title, text, sender_id):
        self.count += 1
        name = self.store.user_name(sender_id)
        room = self.conv.startswith("r:")
        self.avatar.set(self.store.title(self.conv) if room else name, self.conv if room else name,
                        room=room, uid=None if room else sender_id)
        more = f"   (+{self.count - 1} more)" if self.count > 1 else ""
        self.title.setText(title + more)
        self.text.setText(text[:220])
        who = "the room" if room and not self.thread_root else first_name(name)
        self.input.setPlaceholderText(f"Reply to {who}..." if not self.thread_root else "Reply in the thread...")
        self.stamp = time.time()
        self.timer.start()

    def enterEvent(self, e):
        self.timer.stop()
        super().enterEvent(e)

    def leaveEvent(self, e):
        if not self.input.text() and not self.input.hasFocus():
            self.timer.start(3000)
        super().leaveEvent(e)

    def _maybe_close(self):
        if self.underMouse() or self.input.text() or self.input.hasFocus():
            self.timer.start(3000)
            return
        self.close()

    def _open(self, _e=None):
        self.opened.emit(self.conv)
        self.close()

    def _send(self):
        text = self.input.text().strip()
        if not text:
            return
        self.replied.emit(self.conv, text, self.thread_root)
        self.input.clear()
        self.close()

    def closeEvent(self, e):
        self.gone.emit(self)
        super().closeEvent(e)


class PopupStack:
    """Keeps the pop-ups in the bottom-right corner, newest at the bottom, one per chat."""

    def __init__(self, store, on_reply, on_open):
        self.store, self.on_reply, self.on_open = store, on_reply, on_open
        self.popups = []

    def show(self, conv, title, text, sender_id, thread_root=None):
        pop = next((p for p in self.popups if p.conv == conv and p.thread_root == thread_root), None)
        if pop is None:
            pop = MessagePopup(self.store, conv, thread_root)
            pop.replied.connect(self.on_reply)
            pop.opened.connect(self.on_open)
            pop.gone.connect(self._gone)
            self.popups.append(pop)
            while len(self.popups) > MAX_POPUPS:
                self.popups[0].close()
        pop.show_message(title, text, sender_id)
        pop.adjustSize()
        pop.show()
        self._place()

    def _gone(self, pop):
        if pop in self.popups:
            self.popups.remove(pop)
            QTimer.singleShot(0, self._place)

    def _place(self):
        screen = QGuiApplication.primaryScreen()
        if not screen:
            return
        area = screen.availableGeometry()
        y = area.bottom() - 8
        for pop in reversed(self.popups):
            pop.adjustSize()
            y -= pop.height()
            pop.move(QPoint(area.right() - pop.width() - 8, y))
        # (newest at the bottom, the older ones stacked above it)

    def close_all(self):
        for p in list(self.popups):
            p.close()
