"""Threads: a side panel with the message that started a thread and every reply to it.

Replies live in the thread, so the chat itself stays readable; "Also send to the chat" puts a reply in both.
The chat shows "N replies" under the first message (MessageRow), and people who started or joined a thread
are told about new replies (MainWindow._on_thread_message).
"""

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QCheckBox, QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea, QVBoxLayout, QWidget,
)

from common import theme as T
from client.ui.widgets import IconButton, plain


class ThreadPanel(QFrame):
    WIDTH = 400

    def __init__(self, ctx):
        super().__init__()
        from client.ui.chat_view import MessageInput
        self.ctx = ctx
        self.store = ctx.store
        self.conv = None
        self.root_id = None
        self.rows = {}
        self._gen = 0
        self.setObjectName("thread")
        self.setFixedWidth(self.WIDTH)
        self.setStyleSheet(f"#thread {{ background: {T.PANEL}; border-left: 1px solid {T.HAIR}; }}")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        head = QHBoxLayout()
        head.setContentsMargins(18, 14, 10, 10)
        col = QVBoxLayout()
        col.setSpacing(0)
        title = plain(QLabel("Thread"))
        title.setStyleSheet("font-size: 13pt; font-weight: 800; background: transparent;")
        col.addWidget(title)
        self.where = plain(QLabel())
        self.where.setStyleSheet(f"color: {T.MUTED}; font-size: 9pt; background: transparent;")
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
        self.mlay.setContentsMargins(14, 4, 14, 8)
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
        self.input.setPlaceholderText("Reply in the thread...")
        self.input.send.connect(self.send)
        cl.addWidget(self.input)
        row = QHBoxLayout()
        self.also = QCheckBox("Also send to the chat")
        self.also.setStyleSheet(f"color: {T.MUTED}; font-size: 9pt; background: transparent;")
        row.addWidget(self.also, 1)
        send = QPushButton("Send")
        T.polish(send)
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
        self._clear()
        self.hide()

    def showing(self, root_id):
        return self.isVisible() and self.root_id == root_id

    # ------------------------------------------------------------ rows
    def _clear(self):
        for row in self.rows.values():
            row.deleteLater()
        self.rows.clear()
        while self.mlay.count() > 1:
            item = self.mlay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def _row(self, m):
        from client.ui.chat_view import MessageRow
        row = MessageRow(self.ctx, m, m["sender_id"] == self.store.my_id, True, True,
                         m["conv"].startswith("r:"), in_thread=True)
        row.set_max_width(self.WIDTH - 60)
        return row

    def _add(self, m, root=False):
        row = self._row(m)
        self.rows[m["id"]] = row
        self.mlay.insertWidget(self.mlay.count() - 1, row)
        if root:
            line = QLabel()
            line.setFixedHeight(1)
            line.setStyleSheet(f"background: {T.HAIR}; margin: 6px 0;")
            self.mlay.insertWidget(self.mlay.count() - 1, line)

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
