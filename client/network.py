"""Client networking: the chat session connection and LAN server discovery.

Everything runs on the Qt event loop (no threads).
"""

import json

from PySide6.QtCore import QCryptographicHash, QObject, QTimer, Signal
from PySide6.QtNetwork import QAbstractSocket, QHostAddress, QNetworkInterface, QSslSocket, QUdpSocket

from common import protocol as P


def make_socket(parent=None) -> QSslSocket:
    """Socket that can be encrypted. The server's certificate is self-made, so instead of a
    certificate authority we check its fingerprint ourselves (see Connection._on_encrypted)."""
    sock = QSslSocket(parent)
    sock.setPeerVerifyMode(QSslSocket.VerifyNone)
    return sock


def peer_fingerprint(sock: QSslSocket) -> str:
    der = bytes(sock.peerCertificate().toDer())
    digest = bytes(QCryptographicHash.hash(der, QCryptographicHash.Sha256))
    return ":".join(f"{b:02X}" for b in digest)


class Connection(QObject):
    logged_in = Signal(dict)        # login_ok payload (also after a reconnect)
    login_failed = Signal(str)      # wrong password, account disabled... (no retry)
    connection_lost = Signal(str)   # dropped; reconnecting automatically
    kicked = Signal(str)            # the admin disconnected us (no retry)
    event = Signal(dict)            # messages pushed by the server
    identity_changed = Signal(str, str, str)   # server key, remembered fingerprint, new fingerprint
    pins_changed = Signal()                    # a server fingerprint was remembered (save config)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.tls = True
        self.pins = {}              # "host:port" -> fingerprint (trust on first use)
        self.fingerprint = ""
        self.sock = make_socket(self)
        self.sock.connected.connect(self._on_connected)
        self.sock.encrypted.connect(self._on_encrypted)
        self.sock.readyRead.connect(self._on_ready_read)
        self.sock.disconnected.connect(lambda: self._on_lost("Disconnected from server"))
        self.sock.errorOccurred.connect(self._on_error)
        self.buffer = b""
        self.rid = 0
        self.callbacks = {}
        self.host = ""
        self.port = P.TCP_PORT
        self.username = ""
        self.password = ""
        self.status = "online"
        self.token = None
        self.online = False
        self.auto_reconnect = False
        self.retry_delay = 2000
        self.retry_timer = QTimer(self, singleShot=True, timeout=self._connect)
        self.ping_timer = QTimer(self, interval=P.PING_INTERVAL * 1000, timeout=self._ping)
        self.connect_timeout = QTimer(self, singleShot=True, interval=8000,
                                      timeout=lambda: self._fail_attempt("Server not reachable"))
        self.handshake_done = False

    # ---------------------------------------------------------------- api
    def login(self, host, port, username, password, status="online"):
        self.host, self.port = host, int(port)
        self.username, self.password, self.status = username, password, status
        self.auto_reconnect = False       # only after the first successful login
        self.retry_delay = 2000
        self._connect()

    def logout(self):
        self.auto_reconnect = False
        self.retry_timer.stop()
        self.ping_timer.stop()
        self.online = False
        self.token = None
        self.sock.abort()

    def send(self, op, **kw):
        if self.online:
            self.sock.write(P.encode({"op": op, **kw}))

    def request(self, op, callback=None, **kw):
        """Send a request; callback(reply_dict) is called with the answer.

        When offline the callback gets {"ok": False, "error": ...} right away."""
        if not self.online:
            if callback:
                QTimer.singleShot(0, lambda: callback({"ok": False, "error": "Not connected to the server"}))
            return
        self.rid += 1
        if callback:
            self.callbacks[self.rid] = callback
        self.sock.write(P.encode({"op": op, "rid": self.rid, **kw}))

    # ----------------------------------------------------------- internals
    def _connect(self):
        self.sock.abort()
        self.buffer = b""
        self.handshake_done = False
        self.connect_timeout.start()
        if self.tls:
            self.sock.connectToHostEncrypted(self.host, self.port)
        else:
            self.sock.connectToHost(self.host, self.port)

    @property
    def server_key(self):
        return f"{self.host.lower()}:{self.port}"

    def _on_connected(self):
        self.sock.setSocketOption(QAbstractSocket.KeepAliveOption, 1)
        if not self.tls:
            self.fingerprint = ""
            self._send_login()

    def _on_encrypted(self):
        fp = peer_fingerprint(self.sock)
        known = self.pins.get(self.server_key)
        if known and known != fp:
            # another (or a reinstalled) server answers on this address: never send it the password
            self.connect_timeout.stop()
            self.auto_reconnect = False
            self.handshake_done = True
            self.sock.abort()
            self.identity_changed.emit(self.server_key, known, fp)
            return
        if not known:
            self.pins[self.server_key] = fp
            self.pins_changed.emit()
        self.fingerprint = fp
        self._send_login()

    def trust(self, server_key, fingerprint):
        self.pins[server_key] = fingerprint
        self.pins_changed.emit()

    def _send_login(self):
        self.sock.write(P.encode({"op": "login", "username": self.username, "password": self.password,
                                  "status": self.status, "protocol": P.PROTOCOL_VERSION}))

    def _on_ready_read(self):
        chunk = bytes(self.sock.readAll())
        if b"\n" not in chunk:                 # part of a long line (e.g. a big history page)
            self.buffer += chunk
            return
        data = self.buffer + chunk
        *lines, self.buffer = data.split(b"\n")
        for line in lines:
            if not line.strip():
                continue
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            self._handle(msg)

    def _handle(self, msg):
        op = msg.get("op")
        if op == "login_ok":
            self.connect_timeout.stop()
            self.handshake_done = True
            self.online = True
            self.auto_reconnect = True
            self.retry_delay = 2000
            self.token = msg["token"]
            self.ping_timer.start()
            self.logged_in.emit(msg)
        elif op == "login_error":
            self.connect_timeout.stop()
            self.auto_reconnect = False
            self.online = False
            self.sock.abort()
            self.login_failed.emit(msg.get("error", "Login failed"))
        elif op == "reply":
            cb = self.callbacks.pop(msg.get("rid"), None)
            if cb:
                cb(msg)
        elif op == "kicked":
            self.auto_reconnect = False
            self.online = False
            self.sock.abort()
            self.kicked.emit(msg.get("reason", "Disconnected by the server"))
        else:
            self.event.emit(msg)

    def _ping(self):
        self.request("ping")

    def _on_error(self, _err):
        if self.sock.state() != QAbstractSocket.ConnectedState:
            self._fail_attempt(self.sock.errorString())

    def _fail_attempt(self, reason):
        self.connect_timeout.stop()
        if self.online:
            self._on_lost(reason)
            return
        self.sock.abort()
        if self.auto_reconnect:
            self._schedule_retry()
        elif not self.handshake_done:
            self.handshake_done = True        # report only once per attempt
            self.login_failed.emit(f"Cannot reach server {self.host}:{self.port} ({reason})")

    def _on_lost(self, reason):
        if not self.online:
            return
        self.online = False
        self.ping_timer.stop()
        for cb in self.callbacks.values():
            try:
                cb({"ok": False, "error": "Connection lost"})
            except Exception:  # noqa: BLE001
                pass
        self.callbacks.clear()
        if self.auto_reconnect:
            self.connection_lost.emit(reason)
            self._schedule_retry()

    def _schedule_retry(self):
        self.retry_timer.start(self.retry_delay)
        self.retry_delay = min(self.retry_delay * 2, 15000)


class Discovery(QObject):
    """Broadcasts a discovery packet and collects the servers that answer."""
    found = Signal(str, int, str)       # host, port, server name
    finished = Signal()

    def __init__(self, parent=None, port=P.DISCOVERY_PORT):
        super().__init__(parent)
        self.port = port
        self.sock = QUdpSocket(self)
        self.sock.readyRead.connect(self._read)
        self.seen = set()
        self.timer = QTimer(self, singleShot=True, timeout=self._finish)

    def start(self, timeout_ms=1500):
        self.seen.clear()
        if self.sock.state() != QAbstractSocket.BoundState:
            self.sock.bind(QHostAddress.AnyIPv4, 0)
        targets = {QHostAddress(QHostAddress.Broadcast).toString()}
        for iface in QNetworkInterface.allInterfaces():
            flags = iface.flags()
            if not (flags & QNetworkInterface.IsUp) or flags & QNetworkInterface.IsLoopBack:
                continue
            for entry in iface.addressEntries():
                bc = entry.broadcast()
                if not bc.isNull():
                    targets.add(bc.toString())
        targets.add("127.0.0.1")
        for t in targets:
            self.sock.writeDatagram(P.DISCOVERY_MAGIC, QHostAddress(t), self.port)
        self.timer.start(timeout_ms)

    def _read(self):
        while self.sock.hasPendingDatagrams():
            dg = self.sock.receiveDatagram()
            try:
                info = json.loads(bytes(dg.data()))
            except ValueError:
                continue
            host = dg.senderAddress().toString().replace("::ffff:", "")
            key = (host, int(info.get("port", P.TCP_PORT)))
            if key in self.seen:
                continue
            self.seen.add(key)
            self.found.emit(host, key[1], str(info.get("name", "Server")))

    def _finish(self):
        self.finished.emit()
