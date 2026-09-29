"""Wire protocol shared by the server and the client.

Every TCP connection starts with one JSON line whose "op" decides what the
connection is:

* ``login``    - a chat session. Afterwards both sides exchange JSON lines.
* ``upload``   - a file upload: JSON header, then exactly ``size`` raw bytes.
* ``download`` - a file download: JSON header, server answers a JSON line and
                 then streams the raw bytes.

Requests sent by the client on a chat session may carry a ``rid``; the server
answers them with ``{"op": "reply", "rid": ..., "ok": bool, ...}``. Messages
pushed by the server without being asked have no ``rid``.
"""

import json

APP_NAME = "Quillo"
PROTOCOL_VERSION = 1

TCP_PORT = 5150
DISCOVERY_PORT = 5151
DISCOVERY_MAGIC = b"LANMSG_DISCOVER_V1"

MAX_LINE = 4 * 1024 * 1024          # max size of one JSON line
MAX_TEXT = 100_000                  # max characters in one chat message (~2,500 lines of Nuke script)
CHUNK = 256 * 1024                  # file transfer chunk size
PING_INTERVAL = 25                  # seconds between client pings
IDLE_TIMEOUT = 90                   # server drops sessions silent for this long

STATUSES = ("online", "away", "busy", "invisible")

# Shot names that become links in chats (the server admin can change the pattern): FAL_030, FAL_030_0010,
# SEQ010_SH0020. Letters and digits of the show / sequence, "_", optional SH, the shot number, optional task no.
SHOT_PATTERN_DEFAULT = r"\b[A-Z]{2,6}[0-9]{0,4}_(?:SH)?[0-9]{2,5}(?:_[0-9]{2,5})?\b"


def shot_regex(pattern):
    """The compiled shot pattern, or None when it is empty or not a valid regular expression."""
    import re
    if not pattern or len(pattern) > 300:
        return None
    try:
        rx = re.compile(pattern)
    except re.error:
        return None
    return None if rx.match("") else rx          # a pattern matching nothing-at-all would link every gap


def encode(obj) -> bytes:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n"


def decode(line: bytes):
    return json.loads(line.decode("utf-8"))


def direct_conv(user_id: int) -> str:
    return f"u:{user_id}"


def room_conv(room_id: int) -> str:
    return f"r:{room_id}"


def parse_conv(conv: str):
    """Return ("u", id) or ("r", id); raise ValueError on bad input."""
    kind, _, num = str(conv).partition(":")
    if kind not in ("u", "r") or not num.isdigit() or len(num) > 15:
        raise ValueError(f"bad conversation id: {conv!r}")
    return kind, int(num)


def human_size(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"
