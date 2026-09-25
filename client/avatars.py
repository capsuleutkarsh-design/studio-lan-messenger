"""Profile photos: fetched from the server once per version and kept in a local cache folder."""

import base64
import os

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QObject, QRect, Qt, Signal
from PySide6.QtGui import QImage, QPixmap

SIZE = 256                      # photos are stored as 256 x 256
MAX_BYTES = 380 * 1024          # stay under the server limit (400 KB)

cache = None                    # the running AvatarCache (widgets paint photos through it)


def cache_dir():
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    path = os.path.join(base, "LANMessenger", "avatars")
    os.makedirs(path, exist_ok=True)
    return path


def prepare(path):
    """Load a picture, crop the centre square, scale to 256 px and encode it (PNG, or JPEG if big).

    Returns the encoded bytes, or raises ValueError with a message for the user."""
    img = QImage(path)
    if img.isNull():
        raise ValueError("That file is not a picture Windows can open.")
    side = min(img.width(), img.height())
    img = img.copy(QRect((img.width() - side) // 2, (img.height() - side) // 2, side, side))
    img = img.scaled(SIZE, SIZE, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
    for fmt, quality in (("PNG", -1), ("JPG", 90), ("JPG", 75)):
        ba = QByteArray()
        buf = QBuffer(ba)
        buf.open(QIODevice.WriteOnly)
        (img.convertToFormat(QImage.Format_RGB32) if fmt == "JPG" else img).save(buf, fmt, quality)
        if ba.size() <= MAX_BYTES:
            return bytes(ba)
    raise ValueError("The picture is too large even after shrinking it.")


class AvatarCache(QObject):
    changed = Signal(int)               # user id whose photo became available

    def __init__(self, conn, store, parent=None):
        super().__init__(parent)
        global cache
        cache = self
        self.conn = conn
        self.store = store
        self.dir = cache_dir()
        self.pixmaps = {}               # (uid, ver) -> QPixmap
        self.pending = set()
        self.missing = set()            # (uid, ver) the server doesn't have

    def version(self, uid):
        if uid == self.store.my_id:
            return int(self.store.me.get("avatar") or 0)
        return int((self.store.users.get(uid) or {}).get("avatar") or 0)

    def pixmap(self, uid):
        """The user's photo, or None (and fetch it in the background)."""
        if uid is None:
            return None
        ver = self.version(uid)
        if not ver:
            return None
        key = (uid, ver)
        pm = self.pixmaps.get(key)
        if pm is not None:
            return pm
        path = os.path.join(self.dir, f"{uid}_{ver}.img")
        if os.path.exists(path):
            pm = QPixmap(path)
            if not pm.isNull():
                self.pixmaps[key] = pm
                return pm
        if key not in self.pending and key not in self.missing and self.conn.online:
            self.pending.add(key)
            self.conn.request("get_avatar", lambda r, k=key: self._got(k, r), user_id=uid)
        return None

    def _got(self, key, reply):
        self.pending.discard(key)
        uid, ver = key
        if not reply.get("ok"):
            self.missing.add(key)
            return
        data = base64.b64decode(reply["data"])
        pm = QPixmap()
        if not pm.loadFromData(data):
            self.missing.add(key)
            return
        for name in os.listdir(self.dir):          # older versions of this person's photo
            if name.startswith(f"{uid}_"):
                try:
                    os.remove(os.path.join(self.dir, name))
                except OSError:
                    pass
        try:
            with open(os.path.join(self.dir, f"{uid}_{reply.get('ver', ver)}.img"), "wb") as f:
                f.write(data)
        except OSError:
            pass
        self.pixmaps[key] = pm
        self.changed.emit(uid)

    def put_mine(self, ver, data):
        """My own new photo: cache it directly (no round trip)."""
        uid = self.store.my_id
        pm = QPixmap()
        if ver and pm.loadFromData(data):
            self.pixmaps[(uid, ver)] = pm
            try:
                with open(os.path.join(self.dir, f"{uid}_{ver}.img"), "wb") as f:
                    f.write(data)
            except OSError:
                pass
        self.changed.emit(uid)
