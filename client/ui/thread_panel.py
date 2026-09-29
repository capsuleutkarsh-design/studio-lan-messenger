"""Threads: a side panel with the message that started a thread and every reply to it.

Replies live in the thread, so the chat itself stays readable; "Also send to the chat" puts a reply in both.
The chat shows "N replies" under the first message (MessageRow), and people who started or joined a thread
are told about new replies (MainWindow._on_thread_message).
"""

from PySide6.QtCore import QEvent, QSize, Qt, QTimer
from PySide6.QtWidgets import (
    QCheckBox, QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea, QSizePolicy, QVBoxLayout, QWidget,
)

from common import theme as T
from client.ui.widgets import ElidedLabel, IconButton


class ThreadPanel(QFrame):
    WIDTH = 400                  # its width with room to spare
    MIN_WIDTH = 300              # a small window squeezes it this far before the chat has to give way
    SIDE = 14                    # the reply list's side margins

    def __init__(self, ctx):
        super().__init__()
        from client.ui.chat_view import MentionPopup, MessageInput
        self.ctx = ctx
        self.store = ctx.store
        self.conv = None
        self.root_id = None
        self.rows = {}
        self.count_label = None      # 'N replies' between the first message and the replies
        self._gen = 0
        self.setObjectName("thread")
        self.setMinimumWidth(self.MIN_WIDTH)
        self.setMaximumWidth(self.WIDTH)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
        self.setStyleSheet(f"#thread {{ background: {T.PANEL}; border-left: 1px solid {T.HAIR}; }}")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        head = QHBoxLayout()
        head.setContentsMargins(18, 14, 10, 10)
        col = QVBoxLayout()
        col.setSpacing(0)
        title = QLabel("Thread")
        title.setStyleSheet(f"font-size: {T.pt(T.FONT_XL)}; font-weight: 800; background: transparent;")
        col.addWidget(title)
        self.where = ElidedLabel()
        self.where.setStyleSheet(f"color: {T.MUTED}; font-size: {T.pt(T.FONT_S)}; background: transparent;")
        col.addWidget(self.where)
        head.addLayout(col, 1)
        close = IconButton("close", "Close the thread", 32, 16)
        close.clicked.connect(self.close_thread)
        head.addWidget(close, 0, Qt.AlignTop)
        lay.addLayout(head)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        inner = QWidget()
        inner.setStyleSheet("background: transparent;")
        self.mlay = QVBoxLayout(inner)
        self.mlay.setContentsMargins(self.SIDE, 4, self.SIDE, 8)
        self.mlay.setSpacing(2)
        self.mlay.addStretch(1)
        self.scroll.setWidget(inner)
        lay.addWidget(self.scroll, 1)

        comp = QFrame()
        comp.setObjectName("threadcomp")
        comp.setStyleSheet(f"#threadcomp {{ background: {T.SURFACE}; border-radius: 14px; }}")
        cl = QVBoxLayout(comp)
        cl.setContentsMargins(10, 6, 8, 6)
        cl.setSpacing(2)
        self.input = MessageInput()
        self.input.setPlaceholderText("Reply in the thread…")
        self.input.send.connect(self.send)
        self.input.textChanged.connect(self._on_text)
        cl.addWidget(self.input)
        # '@' suggestions, as in the chat's own box
        self.mention_popup = MentionPopup(self)
        self.mention_popup.picked.connect(self._pick_mention)
        self.input.popup = self.mention_popup
        row = QHBoxLayout()
        self.also = QCheckBox("Also send to the chat")
        self.also.setStyleSheet(f"color: {T.MUTED}; font-size: {T.pt(T.FONT_S)}; background: transparent;")
        row.addWidget(self.also, 1)
        self.b_send = send = QPushButton("Send")
        T.polish(send, primary=True)
        send.setCursor(Qt.PointingHandCursor)
        send.setToolTip("Send (Enter). Shift+Enter for a new line")
        send.setEnabled(False)                   # nothing typed yet
        send.clicked.connect(self.send)
        row.addWidget(send)
        cl.addLayout(row)
        wrap = QVBoxLayout()
        wrap.setContentsMargins(12, 6, 12, 12)
        wrap.addWidget(comp)
        lay.addLayout(wrap)

        self.store.thread_message.connect(self._on_thread_message)
        self.store.message_updated.connect(self._on_updated)
        self.hide()

    def sizeHint(self):
        return QSize(self.WIDTH, super().sizeHint().height())

    # ------------------------------------------------------------ width
    def _fit_width(self):
        """WIDTH in a roomy window, down to MIN_WIDTH in a small one (the window reads minimumWidth() to share
        out its columns, so the minimum itself follows the window)."""
        win = self.window()
        if win is None or win is self:
            return
        w = max(self.MIN_WIDTH, min(self.WIDTH, self.MIN_WIDTH + win.width() - 1180))
        if w != self.minimumWidth():
            self.setMinimumWidth(w)

    def showEvent(self, e):
        win = self.window()
        if win is not None and win is not self and not getattr(self, "_watching", False):
            win.installEventFilter(self)          # before the window's own resizeEvent shares out the columns
            self._watching = True
        self._fit_width()
        super().showEvent(e)

    def eventFilter(self, obj, e):
        if e.type() == QEvent.Resize and obj is self.window():
            self._fit_width()
        return super().eventFilter(obj, e)

    # ------------------------------------------------------------ open / close
    def open_thread(self, conv, msg_id):
        self.conv = conv
        self.root_id = msg_id
        self.where.setText(self.store.title(conv))
        self._clear()
        self.show()
        self.input.setFocus()
        self._gen += 1
        gen = self._gen

        def done(reply):
            if gen != self._gen or not reply.get("ok"):
                if gen == self._gen:
                    self.ctx.toast(reply.get("error") or "The thread could not be opened")
                    self.close_thread()
                return
            self.root_id = reply["root"]["id"]
            self._clear()
            self._add(reply["root"], root=True)
            for m in reply["messages"]:
                self._add(m)
            QTimer.singleShot(0, self._to_bottom)
        self.ctx.conn.request("thread", done, id=msg_id)

    def close_thread(self):
        self._gen += 1
        self.root_id = None
        self.conv = None
        self.mention_popup.hide()
        self._clear()
        self.hide()

    def showing(self, root_id):
        return self.isVisible() and self.root_id == root_id

    # ------------------------------------------------------------ rows
    def _clear(self):
        for row in self.rows.values():
            row.deleteLater()
        self.rows.clear()
        self.count_label = None
        while self.mlay.count() > 1:
            item = self.mlay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def _row_width(self):
        """The widest a bubble may be: the list's width less its margins, the avatar column and some air, so a
        reply never touches (or runs past) the panel's right edge."""
        return max(200, self.scroll.viewport().width() - 2 * self.SIDE - 44 - 8)

    def _row(self, m):
        from client.ui.chat_view import MessageRow
        row = MessageRow(self.ctx, m, m["sender_id"] == self.store.my_id, True, True,
                         m["conv"].startswith("r:"), in_thread=True)
        row.set_max_width(self._row_width())
        return row

    def _add(self, m, root=False):
        row = self._row(m)
        self.rows[m["id"]] = row
        self.mlay.insertWidget(self.mlay.count() - 1, row)
        if root:
            self.mlay.insertWidget(self.mlay.count() - 1, self._divider())
        self._update_count()

    def _divider(self):
        """hairline · 'N replies' · hairline: the first message is not taken for one more reply."""
        w = QWidget()
        lay = QHBoxLayout(w)
        lay.setContentsMargins(0, 10, 0, 6)
        lay.setSpacing(10)

        def line():
            f = QFrame()
            f.setFixedHeight(1)
            f.setStyleSheet(f"background: {T.HAIR}; border: none;")
            return f
        self.count_label = QLabel()
        self.count_label.setStyleSheet(f"color: {T.MUTED}; font-size: {T.pt(T.FONT_XS)}; font-weight: 700;"
                                       " background: transparent;")
        lay.addWidget(line(), 1)
        lay.addWidget(self.count_label)
        lay.addWidget(line(), 1)
        return w

    def _update_count(self):
        if self.count_label is not None:
            n = max(0, len(self.rows) - 1)
            self.count_label.setText("No replies yet" if not n else f"{n} {'reply' if n == 1 else 'replies'}")

    def resizeEvent(self, e):
        super().resizeEvent(e)
        w = self._row_width()
        for row in self.rows.values():
            row.set_max_width(w)

    def _to_bottom(self):
        bar = self.scroll.verticalScrollBar()
        bar.setValue(bar.maximum())

    def _on_thread_message(self, m, _live):
        if not self.isVisible() or m.get("thread_root") != self.root_id:
            return
        if m["id"] in self.rows:
            self._on_updated(m)
            return
        at_bottom = self.scroll.verticalScrollBar().value() >= self.scroll.verticalScrollBar().maximum() - 40
        self._add(m)
        if at_bottom or m["sender_id"] == self.store.my_id:
            QTimer.singleShot(0, self._to_bottom)

    def _on_updated(self, m):
        old = self.rows.get(m["id"])
        if old is None or not self.isVisible():
            return
        new = self._row(m)
        self.mlay.insertWidget(self.mlay.indexOf(old), new)
        self.mlay.removeWidget(old)
        old.deleteLater()
        self.rows[m["id"]] = new

    # ------------------------------------------------------------ composer
    def _on_text(self):
        self.b_send.setEnabled(bool(self.input.toPlainText().strip()))
        self._update_mention_popup()

    def _update_mention_popup(self):
        q = self.input.current_mention() if self.conv and self.conv.startswith("r:") else None
        if q is None:
            self.mention_popup.hide()
            return
        from client.ui.chat_view import mention_items
        self.mention_popup.show_for(mention_items(self.store, self.conv, q), self.input.parentWidget())

    def _pick_mention(self, username):
        self.input.complete_mention(username)
        self.mention_popup.hide()
        self.input.setFocus()

    # ------------------------------------------------------------ send
    def send(self):
        text = self.input.toPlainText().strip()
        if not text or not self.root_id:
            return
        self.input.clear()
        self.ctx.outbox.add(self.conv, text=text, thread_root=self.root_id, also_chat=self.also.isChecked(),
                            label=f"Thread reply: {text}")
        self.also.setChecked(False)
        if not self.ctx.conn.online:
            self.ctx.toast("The server is not reachable — your reply is sent as soon as it is back.")
