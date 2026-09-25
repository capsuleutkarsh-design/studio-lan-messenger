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
