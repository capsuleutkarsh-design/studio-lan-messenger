"""File uploads/downloads. Each transfer uses its own TCP connection so big
files never block the chat connection."""

import json
import os
import time

from PySide6.QtCore import QFile, QIODevice, QObject, QTimer, Signal

from common import protocol as P
from common.files import replace_file
from client.network import make_socket, peer_fingerprint

HIGH_WATER = 4 * P.CHUNK      # keep at most this many bytes queued in the socket
MAX_RESUMES = 5               # an upload the network interrupts carries on by itself this many times

# Errors that another try would only repeat: the upload stops and says so.
_FINAL_UPLOAD_ERRORS = ("too large", "disk space", "storage is not available", "Cannot open file",
                        "Could not read", "identity does not match", "changed on disk")


class Transfer(QObject):
    progress = Signal(object)      # self
    finished = Signal(object)      # self (check .state / .error)

    def __init__(self, kind, name, size, conv):
        super().__init__()
        self.kind = kind           # "upload" | "download"
        self.name = name
        self.size = size
        self.conv = conv
        self.done = 0
        self.state = "waiting"     # waiting | running | done | failed | cancelled
        self.hidden = False
        self.error = ""
        self.started = time.time()
        self.speed = 0.0
        self._last = (time.time(), 0)
        self._shown = 0.0          # when progress was last signalled
        self.sock = make_socket(self)
        self.sock.errorOccurred.connect(self._on_socket_error)
        self.sock.connected.connect(self._on_tcp_connected)
        self.sock.encrypted.connect(self._on_encrypted)
        self.tls = False
        self.expected_fp = ""
        self.header_done = False
        self.buf = b""
        self.stall = QTimer(self, interval=60000, singleShot=True,
                            timeout=lambda: self._fail("Transfer timed out"))

    def open_connection(self, conn):
        """Connect like the chat connection does (same encryption, same server identity)."""
        self.tls, self.expected_fp = conn.tls, conn.fingerprint
        if self.tls:
            self.sock.connectToHostEncrypted(conn.host, conn.port)
        else:
            self.sock.connectToHost(conn.host, conn.port)

    def _on_tcp_connected(self):
        if not self.tls:
            self._on_ready()

    def _on_encrypted(self):
        if self.expected_fp and peer_fingerprint(self.sock) != self.expected_fp:
            self._fail("The server's identity does not match (security check failed)")
            return
        self._on_ready()

    def _on_ready(self):
        pass

    def _queued(self):
        """Bytes still waiting in the socket (before and after encryption)."""
        return int(self.sock.bytesToWrite()) + int(self.sock.encryptedBytesToWrite())

    @property
    def active(self):
        return self.state in ("waiting", "running")

    def _tick(self, n):
        self.done += n
        now = time.time()
        t0, d0 = self._last
        if now - t0 >= 0.5:
            self.speed = (self.done - d0) / (now - t0)
            self._last = (now, self.done)
        self.stall.start()
        if now - self._shown >= 0.1 or self.done >= self.size:     # ~10 UI updates a second is plenty
            self._shown = now
            self.progress.emit(self)

    def _read_line(self):
        self.buf += bytes(self.sock.readAll())
        if b"\n" not in self.buf:
            return None
        line, self.buf = self.buf.split(b"\n", 1)
        return json.loads(line)

    def cancel(self):
        if self.active:
            self.state = "cancelled"
            self.sock.abort()
            self._cleanup()
            self.finished.emit(self)

    def _fail(self, error):
        if self.active:
            self.state = "failed"
            self.error = error
            self.sock.abort()
            self._cleanup()
            self.finished.emit(self)

    def _on_socket_error(self, _e):
        if self.active and not (self.kind == "download" and self.done >= self.size and self.header_done):
            self._fail(self.sock.errorString())

    def _cleanup(self):
        self.stall.stop()


class Upload(Transfer):
    """Uploads a local file; emits finished with .file_id set on success.

    When the connection drops part-way, the upload waits and carries on from what the server already has
    (resume_wanted asks the manager to start it again), instead of starting a 10 GB file from zero."""

    resume_wanted = Signal(object)

    def __init__(self, path, conv, caption=""):
        super().__init__("upload", os.path.basename(path), os.path.getsize(path), conv)
        self.path = path
        self.caption = caption
        self.file_id = None
        self.file = QFile(path)
        self.sock.readyRead.connect(self._on_ready_read)
        self.sock.bytesWritten.connect(self._pump)
        self.sock.encryptedBytesWritten.connect(self._pump)
        self.sent = 0
        self.resumes = 0

    def start(self, conn):
        try:
            stamp = (os.path.getsize(self.path), os.path.getmtime(self.path))
        except OSError:
            stamp = None
        if self.file_id and stamp != getattr(self, "stamp", None):
            # saved again since the first try (a re-render of the same size): carrying on would glue the
            # new end onto the old beginning - send it whole instead
            self.file_id = None
            if stamp and stamp[0] != self.size:
                self._fail("The file changed on disk - send it again")
                return
        self.stamp = stamp
        if not self.file.isOpen() and not self.file.open(QIODevice.ReadOnly):
            self._fail(f"Cannot open file: {self.file.errorString()}")
            return
        self.state = "running"
        self.error = ""
        self.header_done = False
        self.buf = b""
        self.token = conn.token
        self.stall.start()
        self.open_connection(conn)

    def _on_ready(self):
        header = {"op": "upload", "token": self.token, "name": self.name, "size": self.size}
        if self.file_id:
            header["resume"] = self.file_id          # the server continues from what it already has
        self.sock.write(P.encode(header))

    def _fail(self, error):
        if self.active and self.resumes < MAX_RESUMES and not any(k in error for k in _FINAL_UPLOAD_ERRORS):
            self.resumes += 1
            self.sock.abort()
            self._cleanup()
            self.state = "waiting"
            self.error = f"Connection lost - carrying on ({self.resumes}/{MAX_RESUMES})..."
            self.progress.emit(self)
            self.resume_wanted.emit(self)
            return
        super()._fail(error)

    def _on_ready_read(self):
        while True:
            msg = self._read_line()
            if msg is None:
                return
            if not msg.get("ok"):
                self._fail(msg.get("error", "Upload refused"))
                return
            if msg.get("done"):
                self.state = "done"
                self._cleanup()
                self.sock.disconnectFromHost()
                self.finished.emit(self)
                return
            self.file_id = msg["file_id"]
            offset = max(0, min(int(msg.get("offset") or 0), self.size))
            if not self.file.seek(offset):
                self._fail("Could not read the file")
                return
            self.sent = self.done = offset
            self._last = (time.time(), offset)
            self.header_done = True
            self.progress.emit(self)
            self._pump()

    def _pump(self, written=0):
        if not self.header_done or self.state != "running":
            return
        if written:
            acked = max(0, min(self.sent - self._queued(), self.size))
            if acked > self.done:
                self._tick(acked - self.done)
        # re-checked on every turn: anything reacting to the progress above may have stopped the upload
        while self.state == "running" and self.sent < self.size and self._queued() < HIGH_WATER:
            chunk = self.file.read(P.CHUNK)
            if not chunk:
                self._fail("Could not read the file")
                return
            self.sock.write(chunk)
            self.sent += len(chunk)

    def _cleanup(self):
        super()._cleanup()
        if self.file.isOpen():
            self.file.close()


class Download(Transfer):
    """Downloads a server file into dest_path (resumes from a .part file)."""

    def __init__(self, file_info, dest_path, conv=None):
        super().__init__("download", file_info["name"], file_info["size"], conv)
        self.file_id = file_info["id"]
        self.dest_path = dest_path
        self.part_path = dest_path + ".part"
        self.file = QFile(self.part_path)
        self.sock.readyRead.connect(self._on_ready_read)
        self.sock.disconnected.connect(self._on_disconnected)

    def start(self, conn):
        folder = os.path.dirname(self.dest_path)
        try:
            os.makedirs(folder, exist_ok=True)
        except OSError as e:
            self._fail(f"Cannot use the download folder {folder} ({e.strerror or e}). "
                       f"Choose another folder in Settings.")
            return
        offset = os.path.getsize(self.part_path) if os.path.exists(self.part_path) else 0
        if offset > self.size:
            offset = 0
        if not self.file.open(QIODevice.Append if offset else QIODevice.WriteOnly):
            self._fail(f"Cannot write file: {self.file.errorString()}")
            return
        self.offset = offset
        self.done = offset
        self.state = "running"
        self.token = conn.token
        self.stall.start()
        self.open_connection(conn)

    def _on_ready(self):
        self.sock.write(P.encode({"op": "download", "token": self.token, "file_id": self.file_id,
                                  "offset": self.offset}))

    def _on_ready_read(self):
        if not self.header_done:
            msg = self._read_line()
            if msg is None:
                return
            if not msg.get("ok"):
                self._fail(msg.get("error", "Download refused"))
                return
            if msg.get("offset", 0) != self.offset:        # server could not resume
                self.file.resize(0)
                self.file.seek(0)
                self.done = 0
            self.header_done = True
            data, self.buf = self.buf, b""
        else:
            data = bytes(self.sock.readAll())
        if data:
            self.file.write(data)
            self._tick(len(data))
        if self.done >= self.size:
            self._complete()

    def _on_disconnected(self):
        if self.state == "running":
            if self.header_done and self.done >= self.size:
                self._complete()
            else:
                self._fail("Connection closed before the download finished")

    def _complete(self):
        if self.state != "running":
            return
        self.file.close()
        final = self.dest_path
        try:
            if os.path.exists(final):
                os.remove(final)
            replace_file(self.part_path, final)
        except OSError as e:
            self._fail(f"Cannot save file: {e}")
            return
        self.state = "done"
        self._cleanup()
        self.sock.disconnectFromHost()
        self.finished.emit(self)

    def _cleanup(self):
        super()._cleanup()
        if self.file.isOpen():
            self.file.close()
        if self.state == "cancelled" and os.path.exists(self.part_path):
            try:
                os.remove(self.part_path)
            except OSError:
                pass


def unique_path(folder, name):
    base, ext = os.path.splitext(name)
    path = os.path.join(folder, name)
    n = 1
    while os.path.exists(path) or os.path.exists(path + ".part"):
        path = os.path.join(folder, f"{base} ({n}){ext}")
        n += 1
    return path


class TransferManager(QObject):
    added = Signal(object)
    changed = Signal(object)
    upload_done = Signal(object)
    download_done = Signal(object)

    def __init__(self, conn, config, parent=None):
        super().__init__(parent)
        self.conn = conn
        self.config = config
        self.transfers: list[Transfer] = []

    def _start(self, t: Transfer):
        self.transfers.append(t)
        t.progress.connect(self.changed.emit)
        t.finished.connect(self._on_finished)
        self.added.emit(t)
        if not self.conn.online:
            t._fail("Not connected to the server")
            return
        try:
            t.start(self.conn)
        except Exception as e:  # noqa: BLE001 - never leave a transfer stuck in "waiting"
            t._fail(str(e))

    def upload(self, path, conv, caption=""):
        t = Upload(path, conv, caption)
        t.resume_wanted.connect(self._resume_later)
        self._start(t)
        return t

    def _resume_later(self, t):
        """An upload lost its connection: try again in a few seconds, or once signed in again."""
        def go():
            if t.state != "waiting":
                return                              # cancelled meanwhile
            if self.conn.online:
                t.start(self.conn)
            else:
                self.conn.logged_in.connect(once)

        def once(*_):
            try:
                self.conn.logged_in.disconnect(once)
            except (RuntimeError, TypeError):
                pass
            QTimer.singleShot(1000, go)
        QTimer.singleShot(3000 * t.resumes, go)

    def download(self, file_info, dest_path=None, conv=None, hidden=False):
        """hidden=True: background download (image previews), not listed in File transfers."""
        for t in self.transfers:
            if t.kind == "download" and t.file_id == file_info["id"] and t.active and t.hidden == hidden:
                return t
        name = os.path.basename(str(file_info["name"]).replace("\\", "/")) or "file"     # never trust a path
        dest_path = dest_path or unique_path(self.config["download_dir"], name)
        t = Download(file_info, dest_path, conv)
        t.hidden = hidden
        self._start(t)
        return t

    def active_download(self, file_id):
        for t in self.transfers:
            if t.kind == "download" and t.file_id == file_id and t.active and not t.hidden:
                return t
        return None

    def _on_finished(self, t):
        self.changed.emit(t)
        temp = getattr(t, "temp_file", None)       # packed folder: remove the temporary zip
        if temp and t.state in ("done", "cancelled"):
            try:
                os.remove(temp)
            except OSError:
                pass
        if t.state == "done":
            if t.kind == "upload":
                self.upload_done.emit(t)
            else:
                if not t.hidden:
                    self.config.remember_download(t.file_id, t.dest_path)
                self.download_done.emit(t)
        if t.hidden and t in self.transfers:       # background preview downloads: nobody lists them
            self.transfers.remove(t)

    def retry(self, t):
        """Start the transfer again; None when an upload's file (or packed folder) is gone."""
        if t.kind == "upload":
            if not os.path.isfile(t.path):
                return None
            new = self.upload(t.path, t.conv, t.caption)
            new.file_id = t.file_id                     # "Retry" carries on from what the server has too
            new.stamp = getattr(t, "stamp", None)       # ... unless the file changed since (Upload.start)
            if getattr(t, "temp_file", None):       # the packed folder's zip now belongs to the new try
                new.temp_file, t.temp_file = t.temp_file, None
            return new
        return self.download({"id": t.file_id, "name": t.name, "size": t.size}, t.dest_path, t.conv)

    def clear_finished(self):
        for t in self.transfers:
            temp = getattr(t, "temp_file", None)
            if temp and not t.active:
                try:
                    os.remove(temp)
                except OSError:
                    pass
        self.transfers = [t for t in self.transfers if t.active]
