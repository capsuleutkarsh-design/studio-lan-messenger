"""Sticker packs shipped with the client (assets/stickers/<pack>/NN.webp + pack.json).

A sticker is sent as a message of kind "sticker" whose body is its id, e.g. "desi_chat/03.webp".
Every client has the same packs, so only the id travels over the network.
"""

import json
import os
import re

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap

from common.icons import asset

STICKER_RE = re.compile(r"^[a-z0-9_]{1,40}/[0-9]{2,3}\.webp$")
RECENT_MAX = 24

_packs = None
_cache = {}


def packs():
    """[{id, title, stickers: [sticker id, ...]}] in display order."""
    global _packs
    if _packs is None:
        _packs = []
        root = asset("stickers")
        try:
            with open(os.path.join(root, "index.json"), encoding="utf-8") as f:
                order = json.load(f)
        except (OSError, ValueError):
            order = []
        for pack_id in order:
            try:
                with open(os.path.join(root, pack_id, "pack.json"), encoding="utf-8") as f:
                    meta = json.load(f)
            except (OSError, ValueError):
                continue
            _packs.append({"id": pack_id, "title": meta.get("title", pack_id),
                           "stickers": [f"{pack_id}/{name}" for name in meta.get("stickers", [])]})
    return _packs


def path(sticker_id):
    if not STICKER_RE.match(sticker_id or ""):
        return None
    p = asset("stickers", *sticker_id.split("/"))
    return p if os.path.exists(p) else None


def pixmap(sticker_id, size):
    """Sticker scaled to fit size x size (cached); None if this client doesn't have it."""
    key = (sticker_id, size)
    if key not in _cache:
        p = path(sticker_id)
        pm = QPixmap(p) if p else QPixmap()
        _cache[key] = None if pm.isNull() else pm.scaled(size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    return _cache[key]


def remember(config, sticker_id):
    recent = [s for s in config["recent_stickers"] if s != sticker_id]
    config["recent_stickers"] = ([sticker_id] + recent)[:RECENT_MAX]
    config.save()


def summary(msg):
    """Short text for a message in lists, notifications and quotes."""
    if msg.get("deleted"):
        return "Message deleted"
    if msg.get("kind") == "sticker":
        return "Sticker"
    if msg.get("kind") == "buzz":
        return "⚡ Buzz!"
    if msg.get("kind") == "poll":
        return f"📊 Poll: {msg.get('body', '')}"
    if msg.get("body"):
        from client.ui.chat_view import is_nuke, is_snippet
        body = msg["body"]
        if msg.get("kind", "text") == "text" and is_snippet(body):
            lines = body.count("\n") + 1
            return f"📄 {'Nuke script' if is_nuke(body) else 'Long text'} · {lines:,} lines"
        return body
    if msg.get("file"):
        return f"📎 {msg['file']['name']}"
    return ""
