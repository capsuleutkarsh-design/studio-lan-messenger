"""Image previews: small image attachments are downloaded quietly into a local cache
(%LOCALAPPDATA%\\LANMessenger\\previews) so chats can show thumbnails."""

import os
import time

from PySide6.QtCore import QObject, Signal

KEEP_DAYS = 30


def cache_dir() -> str:
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or os.path.expanduser("~")
    path = os.path.join(base, "LANMessenger", "previews")
    os.makedirs(path, exist_ok=True)
    return path


class PreviewCache(QObject):
    ready = Signal(str, str)        # file_id, local path
    failed = Signal(str)            # file_id

    def __init__(self, transfers, parent=None):
        super().__init__(parent)
        self.transfers = transfers
        self.folder = cache_dir()
        transfers.changed.connect(self._changed)
        self._cleanup()

    def path_for(self, file_info):
        ext = os.path.splitext(file_info.get("name", ""))[1].lower()[:8]
        fid = "".join(ch for ch in str(file_info["id"]) if ch.isalnum() or ch in "-_")[:64]
        return os.path.join(self.folder, f"{fid}{ext}")

    def request(self, file_info):
        """Local path if cached, else start a background download and return None."""
        path = self.path_for(file_info)
        if os.path.exists(path):
            return path
        if self.transfers.conn.online:
            self.transfers.download(file_info, dest_path=path, hidden=True)
        return None

    def _changed(self, t):
        if not getattr(t, "hidden", False) or t.kind != "download":
            return
        if t.state == "done":
            self.ready.emit(t.file_id, t.dest_path)
        elif t.state in ("failed", "cancelled"):
            self.failed.emit(t.file_id)

    def _cleanup(self):
        cutoff = time.time() - KEEP_DAYS * 86400
        try:
            for name in os.listdir(self.folder):
                p = os.path.join(self.folder, name)
                if os.path.getmtime(p) < cutoff:
                    os.remove(p)
        except OSError:
            pass


# EXR frames, DPX, TIFF, MOV/MP4 dailies and very large pictures: the server makes a small JPEG with ffmpeg
# (it has the file), so nobody downloads a 400 MB clip just to see what is in it.
THUMB_EXT = {".exr", ".dpx", ".tif", ".tiff", ".tga", ".psd", ".mov", ".mp4", ".m4v", ".avi", ".mkv", ".mxf",
             ".webm"}
BIG_IMAGE_EXT = {".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp"}
VIDEO_EXT = {".mov", ".mp4", ".m4v", ".avi", ".mkv", ".mxf", ".webm"}


def is_thumbable(file_info, preview_max=15 * 1024 * 1024):
    ext = os.path.splitext(file_info.get("name", ""))[1].lower()
    if file_info.get("purged") or not file_info.get("size"):
        return False
    return ext in THUMB_EXT or (ext in BIG_IMAGE_EXT and file_info["size"] > preview_max)


def is_video(file_info):
    return os.path.splitext(file_info.get("name", ""))[1].lower() in VIDEO_EXT


class ThumbCache(QObject):
    ready = Signal(str, int, str)        # file_id, size, local path
    failed = Signal(str, int)

    def __init__(self, conn, parent=None):
        super().__init__(parent)
        self.conn = conn
        self.folder = cache_dir()
        self.pending = set()

    def path_for(self, file_info, size):
        fid = "".join(ch for ch in str(file_info["id"]) if ch.isalnum() or ch in "-_")[:64]
        return os.path.join(self.folder, f"{fid}_t{size}.jpg")

    def request(self, file_info, size=320):
        """Local path of the preview if it is here already; else ask the server for it (ready / failed)."""
        path = self.path_for(file_info, size)
        if os.path.exists(path):
            return path
        key = (file_info["id"], size)
        if key in self.pending or not self.conn.online:
            return None
        self.pending.add(key)

        def done(reply):
            self.pending.discard(key)
            if not reply.get("ok") or not reply.get("data"):
                self.failed.emit(file_info["id"], size)
                return
            import base64
            try:
                with open(path, "wb") as fh:
                    fh.write(base64.b64decode(reply["data"]))
            except (OSError, ValueError):
                self.failed.emit(file_info["id"], size)
                return
            self.ready.emit(file_info["id"], size, path)
        self.conn.request("thumb", done, file_id=file_info["id"], size=size)
        return None
