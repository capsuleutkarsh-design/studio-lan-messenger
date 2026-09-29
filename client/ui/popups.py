"""New-message pop-ups in the corner of the screen that you can answer without opening Quillo.

Reminder cards (planner_ui.ReminderPopup) share the same corner: PopupStack stacks both in one column, the
reminders at the bottom (they stay until Done or Snooze) and the message pop-ups above them.
"""

import time

from PySide6.QtCore import QPoint, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFontMetrics, QGuiApplication
from PySide6.QtWidgets import QFrame, QGraphicsDropShadowEffect, QHBoxLayout, QLabel, QLineEdit, QVBoxLayout, QWidget

from common import theme as T
from client.ui.widgets import ELLIPSIS, Avatar, ElidedLabel, IconButton, clip, first_name, plain, rich_safe

SHOW_MS = 9000          # how long a pop-up stays when nobody touches it
MAX_POPUPS = 3

CARD_W = 364            # the visible card; the window around it is a little wider, for the shadow
MARGINS = (10, 6, 10, 12)   # transparent edge around the card: room for the soft shadow (it falls downwards)
SCREEN_GAP = 8          # between the column of cards and the screen edge


def float_card(card, name, border=None):
    """Style a floating card (the visible part of a pop-up window): panel background, a firm edge, round corners
    and a soft shadow, so it stands out over any other application in both themes."""
    card.setObjectName(name)
    card.setStyleSheet(f"#{name} {{ background: {T.PANEL}; border: 1px solid {border or T.FLOAT_BORDER};"
                       f" border-radius: {T.RADIUS_L}px; }}")
    shadow = QGraphicsDropShadowEffect(card)
    shadow.setBlurRadius(24)
    shadow.setOffset(0, 4)
    shadow.setColor(QColor(0, 0, 0, 70 if T.DARK else 40))
    card.setGraphicsEffect(shadow)
    return card


def float_window(w):
    """The see-through window around a floating card (its corners and shadow show the desktop behind)."""
    w.setAttribute(Qt.WA_TranslucentBackground)
    w.setAttribute(Qt.WA_DeleteOnClose)
    w.setFixedWidth(CARD_W + MARGINS[0] + MARGINS[2])
    outer = QVBoxLayout(w)
    outer.setContentsMargins(*MARGINS)
    outer.setSpacing(0)
    return outer


def screen_area(screen=None):
    """The available area of `screen` (the main window's screen), or of the primary screen."""
    screen = screen or QGuiApplication.primaryScreen()
    return screen.availableGeometry() if screen else None


def two_lines(text, fm, width, lines=2):
    """text wrapped at word boundaries into at most `lines` lines of `width` px; a longer text ends in '…'."""
    words = " ".join(str(text or "").split()).split(" ")
    out, i = [], 0
    while i < len(words) and len(out) < lines:
        line = words[i]
        i += 1
        if fm.horizontalAdvance(line) > width:          # one very long word (a path, a link): cut it
            line = fm.elidedText(line, Qt.ElideRight, width)
        while i < len(words) and fm.horizontalAdvance(line + " " + words[i]) <= width:
            line += " " + words[i]
            i += 1
        out.append(line)
    if i < len(words) and out:
        last = out[-1]
        while last and fm.horizontalAdvance(last + ELLIPSIS) > width:
            last = last.rsplit(" ", 1)[0] if " " in last else last[:-1]
        out[-1] = last.rstrip(" ,;:.-") + ELLIPSIS
    return "\n".join(out)


class MessagePopup(QWidget):
    replied = Signal(str, str, object)     # conv, text, thread root (or None)
    opened = Signal(str)                   # conv
    gone = Signal(object)

    AVATAR = 36
    PAD = (14, 12, 10, 12)                  # inside the card

    def __init__(self, store, conv, thread_root=None):
        super().__init__(None, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.store, self.conv, self.thread_root = store, conv, thread_root
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        outer = float_window(self)
        card = float_card(QFrame(), "popcard")
        outer.addWidget(card)
        lay = QVBoxLayout(card)
        lay.setContentsMargins(*self.PAD)
        lay.setSpacing(T.SPACE_S)

        top = QHBoxLayout()
        top.setSpacing(10)
        self.avatar = Avatar(self.AVATAR)
        top.addWidget(self.avatar, 0, Qt.AlignTop)
        col = QVBoxLayout()
        col.setSpacing(2)
        head = QHBoxLayout()
        head.setSpacing(6)
        self.title = ElidedLabel()
        self.title.setStyleSheet(f"font-weight: 700; font-size: {T.pt(T.FONT_M)}; background: transparent;")
        head.addWidget(self.title, 1)
        self.more = plain(QLabel())                        # '+2 more': messages that came in since
        self.more.setStyleSheet(f"color: {T.META}; font-size: {T.pt(T.FONT_S)}; background: transparent;")
        self.more.hide()
        head.addWidget(self.more)
        # in a room the title is the room (it used to be cut off the end of 'Farhan mentioned you in AK74 P'),
        # and who wrote, or mentioned you, is a small line under it
        self.context = ElidedLabel()
        self.context.setStyleSheet(f"color: {T.META}; font-size: {T.pt(T.FONT_S)}; background: transparent;")
        self.context.hide()
        self.text = plain(QLabel())
        self.text.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self.text.setStyleSheet(f"color: {T.MUTED}; font-size: {T.pt(T.FONT_M)}; background: transparent;")
        col.addLayout(head)
        col.addWidget(self.context)
        col.addWidget(self.text)
        col.addStretch(1)
        top.addLayout(col, 1)
        close = IconButton("close", "Close", 26, 12)
        close.clicked.connect(self.close)
        top.addWidget(close, 0, Qt.AlignTop)
        lay.addLayout(top)
        for w in (self.avatar, self.title, self.context, self.text):
            w.setCursor(Qt.PointingHandCursor)
            w.mousePressEvent = self._open

        row = QHBoxLayout()
        row.setSpacing(6)
        self.input = QLineEdit()
        self.input.setStyleSheet(f"QLineEdit {{ background: {T.SURFACE}; border: 1px solid {T.HAIR};"
                                 f" border-radius: {T.RADIUS_CONTROL}px; padding: 6px 10px; }}"
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

    def _text_width(self):
        """How wide the message text can be: the card less its padding, the avatar and the close button."""
        return CARD_W - self.PAD[0] - self.PAD[2] - self.AVATAR - 10 - 26 - 10 - 2

    def show_message(self, title, text, sender_id):
        self.count += 1
        name = self.store.user_name(sender_id)
        room = self.conv.startswith("r:")
        self.avatar.set(self.store.title(self.conv) if room else name, self.conv if room else name,
                        room=room, uid=None if room else sender_id)
        where = f" in {self.store.title(self.conv)}" if room else ""
        if where and title.endswith(where) and len(title) > len(where):
            self.title.setText(where[4:])                 # 'AK74 Project'
            self.context.setText(title[:-len(where)])     # 'Farhan Qureshi mentioned you'
        else:
            self.title.setText(title)
            self.context.setText("")
        self.context.setVisible(bool(self.context.text()))
        self.more.setText(f"+{self.count - 1} more")
        self.more.setVisible(self.count > 1)
        self.text.ensurePolished()
        self.text.setText(two_lines(text, QFontMetrics(self.text.font()), self._text_width()))
        cut = self.text.text().replace("\n", " ") != " ".join(str(text or "").split())
        self.text.setToolTip(rich_safe(clip(text, 600, one_line=False)) if cut else "")
        if self.thread_root:
            hint = "Reply in the thread"
        elif room:
            hint = f"Reply in {clip(self.store.title(self.conv), 32)}"
        else:
            hint = f"Reply to {first_name(name)}"
        self.input.setPlaceholderText(hint + ELLIPSIS)
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
    """Keeps the pop-ups in the bottom-right corner, newest at the bottom, one per chat.

    screen: returns the screen to use (the main window's), below: returns the windows that sit under the
    pop-ups in the same column (the reminder cards), bottom first."""

    def __init__(self, store, on_reply, on_open, screen=None, below=None):
        self.store, self.on_reply, self.on_open = store, on_reply, on_open
        self.screen = screen or (lambda: None)
        self.below = below or (lambda: [])
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
        self.place()

    def _gone(self, pop):
        if pop in self.popups:
            self.popups.remove(pop)
            QTimer.singleShot(0, self.place)

    def place(self):
        """One column in the bottom-right corner: the reminder cards at the bottom, the message pop-ups above
        them (newest lowest), so a reminder and a message never cover each other."""
        area = screen_area(self.screen())
        if area is None:
            return
        # the see-through margins around each card already keep it off the screen edge
        y = area.bottom() + 1 - max(0, SCREEN_GAP - MARGINS[3])
        right = area.right() + 1 - max(0, SCREEN_GAP - MARGINS[2])
        for w in [w for w in self.below() if w.isVisible()] + list(reversed(self.popups)):
            w.adjustSize()
            y -= w.height()
            w.move(QPoint(right - w.width(), y))

    _place = place

    def close_all(self):
        for p in list(self.popups):
            p.close()
