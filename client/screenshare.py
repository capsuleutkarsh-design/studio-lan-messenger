"""View-only screen sharing between two people (always with the sharer's consent).

Frames are JPEG images (only sent when the screen changed), relayed by the server over their own
encrypted connection, so chat and file transfers are not slowed down.
"""

import json
import time
import zlib

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QObject, QPoint, Qt, QTimer, Signal
from PySide6.QtGui import QGuiApplication, QPixmap
from PySide6.QtWidgets import (
    QComboBox, QHBoxLayout, QLabel, QMessageBox, QPushButton, QScrollArea, QVBoxLayout, QWidget,
)

from common import protocol as P
from common import theme as T
from client.network import make_socket, peer_fingerprint

# Frames are sent only when the screen changed. While things move: up to FPS frames a second at a
# quality that follows the network (MOTION_*); once the picture has been still for SETTLE seconds one
# sharp frame (SHARP_*) follows, so text and code are crisp. A full send queue lowers the quality
# and skips frames, so chat, file transfers and ping on the LAN are not squeezed.
FPS = 8
MAX_WIDTH = 2560                 # 4K screens are scaled to this: text stays readable
MOTION_START, MOTION_MIN, MOTION_MAX = 62, 38, 75
SHARP_START, SHARP_MIN, SHARP_MAX = 86, 72, 92
SHARP_BUDGET = 900 * 1024        # a sharp frame bigger than this lowers the next one (very dense screens)
SETTLE = 0.35                    # seconds without change before the sharp frame
BUSY_BYTES = 384 * 1024          # this much still waiting to go out: the network is busy
KEEPALIVE = 3.0                  # resend the last frame this often even when nothing changes
IDLE_INTERVAL = 350              # ms between looks at a screen that has been still for a while
SMALL_CHANGE = 2                 # up to this many of 24 screen strips changed = typing, a caret, a clock
SMALL_EVERY = 1.0                # small changes are sent (sharp) at most this often


class _Relay(QObject):
    """One relay connection (publisher or subscriber) that checks the server identity like chat does."""
    ready = Signal()
    failed = Signal(str)

    def __init__(self, conn, op, share_id, parent=None):
        super().__init__(parent)
        self.conn = conn
        self.sock = make_socket(self)
        self.header = P.encode({"op": op, "token": conn.token, "share_id": share_id})
        self.answered = False
        self.buf = b""
        self.sock.connected.connect(lambda: None if conn.tls else self.sock.write(self.header))
        self.sock.encrypted.connect(self._encrypted)
        self.sock.errorOccurred.connect(lambda _e: self.failed.emit(self.sock.errorString()))
        if conn.tls:
            self.sock.connectToHostEncrypted(conn.host, conn.port)
        else:
            self.sock.connectToHost(conn.host, conn.port)

    def _encrypted(self):
        if self.conn.fingerprint and peer_fingerprint(self.sock) != self.conn.fingerprint:
            self.failed.emit("server identity mismatch")
            self.sock.abort()
            return
        self.sock.write(self.header)

    def read_answer(self):
        """Consume the JSON answer line; returns True once accepted."""
        if self.answered:
            return True
        self.buf += bytes(self.sock.readAll())
        if b"\n" not in self.buf:
            return False
        line, self.buf = self.buf.split(b"\n", 1)
        if not json.loads(line).get("ok"):
            self.failed.emit("not allowed")
            return False
        self.answered = True
        self.ready.emit()
        return True

    def close(self):
        self.sock.abort()


class SharerBar(QWidget):
    """Always-on-top bar shown while my screen is being shared."""
    stop = Signal()
    screen_changed = Signal(int)

    def __init__(self, viewer_name):
        super().__init__(None, Qt.Window | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        frame = QWidget(self)
        frame.setObjectName("bar")
        frame.setStyleSheet(f"#bar {{ background: #3a0d12; border: 2px solid {T.DANGER}; border-radius: 14px; }}")
        lay = QHBoxLayout(frame)
        lay.setContentsMargins(14, 6, 8, 6)
        dot = QLabel("●")
        dot.setStyleSheet(f"color: {T.DANGER}; font-size: 14pt; background: transparent;")
        text = QLabel(f"You are sharing your screen with {viewer_name}")
        text.setTextFormat(Qt.PlainText)
        text.setStyleSheet("color: white; font-weight: 600; background: transparent;")
        self.screens = QComboBox()
        for i, sc in enumerate(QGuiApplication.screens()):
            self.screens.addItem(f"Monitor {i + 1} ({sc.size().width()}×{sc.size().height()})", i)
        self.screens.setVisible(self.screens.count() > 1)
        self.screens.currentIndexChanged.connect(lambda _: self.screen_changed.emit(self.screens.currentData()))
        stop = QPushButton("Stop sharing")
        stop.setStyleSheet(f"QPushButton {{ background: {T.DANGER}; color: white; border-radius: 10px;"
                           f" padding: 6px 14px; font-weight: 700; }}")
        stop.clicked.connect(self.stop.emit)
        for w in (dot, text, self.screens, stop):
            lay.addWidget(w)
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(frame)
        self.adjustSize()
        geo = QGuiApplication.primaryScreen().availableGeometry()
        self.move(geo.center().x() - self.width() // 2, geo.top() + 8)
        self._drag = None

    def mousePressEvent(self, e):
        self._drag = e.globalPosition().toPoint() - self.pos()

    def mouseMoveEvent(self, e):
        if self._drag is not None:
            self.move(e.globalPosition().toPoint() - self._drag)


class ViewerWindow(QWidget):
    """Shows the other person's screen."""
    closed = Signal()

    def __init__(self, sharer_name):
        super().__init__(None, Qt.Window)
        self.setWindowTitle(f"{sharer_name}'s screen — Quillo")
        self.resize(1280, 760)
        T.bg_pane(self, "#000000")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        top = QWidget()
        T.bg_pane(top, T.PANEL)
        tl = QHBoxLayout(top)
        tl.setContentsMargins(12, 6, 8, 6)
        self.info = QLabel(f"Waiting for {sharer_name}'s screen...")
        self.info.setTextFormat(Qt.PlainText)
        self.info.setStyleSheet(f"color: {T.MUTED};")
        tl.addWidget(self.info, 1)
        self.fit_btn = QPushButton("Actual size")
        self.fit_btn.setCheckable(True)
        self.fit_btn.toggled.connect(self._toggle_fit)
        full = QPushButton("Full screen")
        full.clicked.connect(lambda: self.showNormal() if self.isFullScreen() else self.showFullScreen())
        stop = QPushButton("Stop viewing")
        T.polish(stop, danger=True)
        stop.clicked.connect(self.close)
        for b in (self.fit_btn, full, stop):
            tl.addWidget(b)
        lay.addWidget(top)
        self.area = QScrollArea()
        self.area.setAlignment(Qt.AlignCenter)
        self.area.setStyleSheet("QScrollArea { background: black; border: none; }")
        self.image = QLabel()
        self.image.setAlignment(Qt.AlignCenter)
        self.image.setStyleSheet("background: black;")
        self.area.setWidget(self.image)
        self.area.setWidgetResizable(True)
        lay.addWidget(self.area, 1)
        self.pixmap = None
        self.frames = 0
        self.last = time.time()
        self.sharer_name = sharer_name

    def show_frame(self, pm: QPixmap):
        self.pixmap = pm
        self.frames += 1
        self._render()
        now = time.time()
        if now - self.last > 1:
            fps = self.frames / (now - self.last)
            self.info.setText(f"{self.sharer_name}'s screen  ·  {pm.width()}×{pm.height()}  ·  "
                              f"{fps:.0f} fps  ·  view only")
            self.last, self.frames = now, 0

    def _render(self):
        if not self.pixmap:
            return
        if self.fit_btn.isChecked():
            self.image.setPixmap(self.pixmap)
        else:
            self.image.setPixmap(self.pixmap.scaled(self.area.viewport().size(), Qt.KeepAspectRatio,
                                                    Qt.SmoothTransformation))

    def _toggle_fit(self, actual):
        self.area.setWidgetResizable(not actual)
        self.fit_btn.setText("Fit to window" if actual else "Actual size")
        self._render()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._render()

    def mouseDoubleClickEvent(self, e):
        self.showNormal() if self.isFullScreen() else self.showFullScreen()

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape and self.isFullScreen():
            self.showNormal()
        else:
            super().keyPressEvent(e)

    def closeEvent(self, e):
        self.closed.emit()
        super().closeEvent(e)


class ScreenShareManager(QObject):
    """Handles invitations, the sharer side (capture + publish) and the viewer side."""

    def __init__(self, win):
        super().__init__(win)
        self.win = win
        self.conn = win.conn
        self.store = win.store
        self.share_id = None
        self.role = None
        self.relay = None
        self.bar = None
        self.viewer = None
        self.timer = QTimer(self, interval=int(1000 / FPS), timeout=self._capture)
        self.screen_index = 0
        self.last_hash = None
        self.last_sent = 0
        self.last_frame = b""
        self.changed_at = 0.0
        self.sharp_sent = True
        self.small_pending = False
        self.quality = MOTION_START
        self.sharp_q = SHARP_START
        self.rx = b""
        self.conn.event.connect(self._event)

    # ------------------------------------------------------------ actions
    def invite(self, user_id, kind):
        """kind='offer': share my screen with user; kind='request': ask to see theirs."""
        if self.share_id:
            self.win.toast("A screen share is already running.")
            return
        name = self.store.user_name(user_id)

        def done(r):
            if r.get("ok"):
                self.win.toast(f"Waiting for {name} to accept..." if kind == "request"
                               else f"Asked {name} to view your screen...")
            else:
                self.win.toast(r.get("error", "Screen share not possible"))
        self.conn.request("screen_invite", done, kind=kind, to=user_id)

    def stop(self):
        if self.share_id:
            self.conn.send("screen_stop", share_id=self.share_id)
        self._cleanup()

    # ------------------------------------------------------------ server events
    def _event(self, ev):
        op = ev.get("op")
        if op == "screen_invite":
            self._invited(ev)
        elif op == "screen_start":
            self._start(ev)
        elif op == "screen_stopped" and ev.get("share_id") == self.share_id:
            self.win.toast("Screen sharing ended.")
            self._cleanup()
        elif op == "screen_declined":
            self.win.toast(f"{ev.get('by_name', 'They')} declined the screen share.")

    def _invited(self, ev):
        name = ev.get("from_name", "Someone")
        self.win.show_normal()
        if ev["kind"] == "request":
            text = (f"{name} asks to SEE YOUR SCREEN.\n\nThey will see everything on your monitor until you "
                    f"click “Stop sharing”. They cannot control your PC.")
            yes = "Allow — share my screen"
        else:
            text = f"{name} wants to show you their screen."
            yes = "View screen"
        box = QMessageBox(self.win)
        box.setWindowTitle("Screen sharing")
        box.setIcon(QMessageBox.Question)
        box.setText(text)
        allow = box.addButton(yes, QMessageBox.AcceptRole)
        box.addButton("Decline", QMessageBox.RejectRole)
        box.exec()
        self.conn.send("screen_answer", share_id=ev["share_id"], accept=box.clickedButton() is allow)

    def _start(self, ev):
        self._cleanup()
        self.share_id = ev["share_id"]
        me = self.store.my_id
        if ev["sharer"] == me:
            self.role = "sharer"
            self.relay = _Relay(self.conn, "screen_pub", self.share_id, self)
            self.relay.sock.readyRead.connect(lambda: self.relay.read_answer())
            self.relay.ready.connect(self._begin_capture)
            self.relay.failed.connect(lambda err: (self.win.toast(f"Screen share failed: {err}"), self.stop()))
            self.bar = SharerBar(ev["viewer_name"])
            self.bar.stop.connect(self.stop)
            self.bar.screen_changed.connect(self._set_screen)
            self.bar.show()
        else:
            self.role = "viewer"
            self.viewer = ViewerWindow(ev["sharer_name"])
            self.viewer.closed.connect(self.stop)
            self.viewer.show()
            self.relay = _Relay(self.conn, "screen_sub", self.share_id, self)
            self.relay.sock.readyRead.connect(self._receive)
            self.relay.failed.connect(lambda err: (self.win.toast(f"Screen share failed: {err}"), self.stop()))

    # ------------------------------------------------------------ sharer
    def _set_screen(self, index):
        self.screen_index = index
        self.last_hash = None

    def _begin_capture(self):
        self.timer.start()
        self._capture()

    def _capture(self):
        if not self.relay or self.role != "sharer":
            return
        sock = self.relay.sock
        backlog = int(sock.bytesToWrite()) + int(sock.encryptedBytesToWrite())
        if backlog > BUSY_BYTES:                     # network busy: skip this frame and go easier
            self.quality = max(MOTION_MIN, self.quality - 6)
            return
        if backlog == 0:                             # everything went out: we can afford a little more
            self.quality = min(MOTION_MAX, self.quality + 1)
        screens = QGuiApplication.screens()
        screen = screens[min(self.screen_index, len(screens) - 1)]
        pm = screen.grabWindow(0)
        if pm.width() > MAX_WIDTH:
            pm = pm.scaledToWidth(MAX_WIDTH, Qt.SmoothTransformation)
        now = time.time()
        # cheap change test on the raw pixels (no JPEG encoding for a screen that did not change)
        bands = self._bands(pm.toImage())
        changed = (len(bands) if self.last_hash is None or len(self.last_hash) != len(bands)
                   else sum(a != b for a, b in zip(bands, self.last_hash)))
        if changed:
            self.last_hash = bands
        if changed > SMALL_CHANGE:                   # scrolling, playback, a window moved: smooth mode
            self.changed_at, self.sharp_sent = now, False
            self.timer.setInterval(int(1000 / FPS))
            self._send(sock, self._jpeg(pm, self.quality), now)
        elif changed or self.small_pending:          # typing, a caret, a clock: keep it sharp
            if now - self.last_sent >= SMALL_EVERY:
                self.small_pending = False
                self._send(sock, self._sharp(pm) if self.sharp_sent else self._jpeg(pm, self.quality), now)
            else:
                self.small_pending = True
        elif not self.sharp_sent and now - self.changed_at >= SETTLE:
            self.sharp_sent = True                   # still for a moment: one crisp frame for reading
            self._send(sock, self._sharp(pm), now)
        elif now - self.last_sent >= KEEPALIVE and self.last_frame:
            self._send(sock, self.last_frame, now)   # lets a viewer that just joined see the screen
        if now - self.changed_at > 2 and self.timer.interval() != IDLE_INTERVAL:
            self.timer.setInterval(IDLE_INTERVAL)    # a still screen: look less often (saves CPU)

    @staticmethod
    def _bands(image, count=24):
        """A checksum per horizontal strip of the screen: how much changed, cheaply (no copy of the pixels)."""
        bits = memoryview(image.constBits())
        step = max(1, image.height() // count) * image.bytesPerLine()
        return [zlib.crc32(bits[i:i + step]) for i in range(0, len(bits), step)]

    def _sharp(self, pm):
        """A crisp frame for reading; its quality follows how heavy such frames turn out on this screen."""
        jpeg = self._jpeg(pm, self.sharp_q)
        if len(jpeg) > SHARP_BUDGET:
            self.sharp_q = max(SHARP_MIN, self.sharp_q - 4)
        elif len(jpeg) < SHARP_BUDGET // 2:
            self.sharp_q = min(SHARP_MAX, self.sharp_q + 2)
        return jpeg

    @staticmethod
    def _jpeg(pm, quality):
        data = QByteArray()
        buf = QBuffer(data)
        buf.open(QIODevice.WriteOnly)
        pm.save(buf, "JPG", quality)
        return bytes(data)

    def _send(self, sock, jpeg, now):
        self.last_frame, self.last_sent = jpeg, now
        sock.write(len(jpeg).to_bytes(4, "big") + jpeg)

    # ------------------------------------------------------------ viewer
    def _receive(self):
        if not self.relay.read_answer():
            return
        self.rx += self.relay.buf + bytes(self.relay.sock.readAll())
        self.relay.buf = b""
        latest = None
        while len(self.rx) >= 4:
            size = int.from_bytes(self.rx[:4], "big")
            if len(self.rx) < 4 + size:
                break
            latest, self.rx = self.rx[4:4 + size], self.rx[4 + size:]
        if latest and self.viewer:
            pm = QPixmap()
            if pm.loadFromData(latest, "JPG"):
                self.viewer.show_frame(pm)

    # ------------------------------------------------------------ cleanup
    def _cleanup(self):
        self.timer.stop()
        if self.relay:
            self.relay.close()
            self.relay.deleteLater()
        self.relay = None
        if self.bar:
            self.bar.close()
            self.bar.deleteLater()
        self.bar = None
        if self.viewer:
            v, self.viewer = self.viewer, None
            v.closed.disconnect()
            v.close()
            v.deleteLater()
        self.share_id = None
        self.role = None
        self.rx = b""
        self.last_hash = None
        self.last_frame = b""
        self.quality = MOTION_START
        self.sharp_sent = True
