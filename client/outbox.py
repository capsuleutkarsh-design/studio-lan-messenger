"""Messages waiting for the server.

Everything typed is put here first, saved to disk, and sent in order. While the server is away (a restart,
the server PC off, the network down) messages simply wait - greyed, with a clock, in their chat - and go
out when Quillo is signed in again, also after Quillo or the PC was restarted.

Each message carries its own id (client_id). If the connection drops after the server stored a message
but before its answer arrived, the message is sent again and the server hands back the one it has instead
of storing it twice.
"""

import json
import os
import re
import time
import uuid

from PySide6.QtCore import QObject, Signal

from client.config import config_dir
from common.files import replace_file

# answers that mean "try again later", not "the server said no"
_LATER = ("Not connected", "Connection lost", "timed out", "Please change your password first", "Signed out",
          "Disconnected", "Not signed in")


class Outbox(QObject):
    changed = Signal(str)               # conv whose waiting messages changed
    refused = Signal(dict, str)         # item, error - the server will not take it; the text goes back

    def __init__(self, conn, store, parent=None):
        super().__init__(parent)
        self.conn = conn
        self.store = store
        self.items = []
        self.path = ""
        self._in_flight = None
        conn.logged_in.connect(self._on_logged_in)

    # ------------------------------------------------------------ storage
    def _account_file(self):
        key = f"{self.conn.host}_{self.conn.port}_{self.conn.username}".lower()
        return os.path.join(config_dir(), "outbox", re.sub(r"[^a-z0-9._-]+", "_", key) + ".json")

    def _load(self):
        self.items = []
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
            self.items = [i for i in data if isinstance(i, dict) and i.get("client_id") and i.get("conv")]
        except (OSError, ValueError):
            pass

    def _save(self):
        if not self.path:
            return
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            with open(self.path + ".tmp", "w", encoding="utf-8") as f:
                json.dump(self.items, f, ensure_ascii=False)
            replace_file(self.path + ".tmp", self.path)
        except OSError:
            pass                        # the messages still go out while Quillo runs

    def _on_logged_in(self, *_):
        path = self._account_file()
        if path != self.path:           # another account (or the first sign-in): its own waiting messages
            self.path = path
            self._in_flight = None
            self._load()
            for conv in {i["conv"] for i in self.items}:
                self.changed.emit(conv)
        self._in_flight = None
        self.flush()

    # ------------------------------------------------------------ public
    def add(self, conv, text="", reply_to=None, file_id=None, sticker=None, thread_root=None, also_chat=False,
            label=""):
        """Queue a message; it is sent at once when the server is there."""
        item = {"client_id": uuid.uuid4().hex, "conv": conv, "text": text, "created": time.time(),
                "label": label or text}
        for key, value in (("reply_to", reply_to), ("file_id", file_id), ("sticker", sticker),
                           ("thread_root", thread_root), ("also_chat", also_chat)):
            if value:
                item[key] = value
        self.items.append(item)
        self._save()
        self.changed.emit(conv)
        self.flush()
        return item

    def pending(self, conv):
        return [i for i in self.items if i["conv"] == conv]

    def cancel(self, client_id):
        """Drop a message that has not gone out yet. One already on its way can't be taken back."""
        for item in self.items:
            if item["client_id"] == client_id and item is not self._in_flight:
                self.items.remove(item)
                self._save()
                self.changed.emit(item["conv"])
                return True
        return False

    # ------------------------------------------------------------ sending
    def flush(self):
        if self._in_flight is not None or not self.items or not self.conn.online:
            return
        item = self._in_flight = self.items[0]
        req = {"conv": item["conv"], "text": item.get("text", ""), "client_id": item["client_id"]}
        for key in ("reply_to", "file_id", "sticker", "thread_root", "also_chat"):
            if item.get(key):
                req[key] = item[key]
        self.conn.request("send", lambda reply: self._answered(item, reply), **req)

    def _answered(self, item, reply):
        if self._in_flight is item:
            self._in_flight = None
        if reply.get("ok"):
            self._drop(item)
            self.store.add_message(reply["message"])
        elif any(k in (reply.get("error") or "") for k in _LATER):
            return                      # waits; sent when signed in again (_on_logged_in)
        else:
            self._drop(item)
            self.refused.emit(item, reply.get("error") or "Not sent")
        self.flush()

    def _drop(self, item):
        if item in self.items:
            self.items.remove(item)
            self._save()
        self.changed.emit(item["conv"])
