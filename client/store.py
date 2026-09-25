"""In-memory state of the logged-in client, fed by the server's messages."""

import time

from PySide6.QtCore import QObject, Signal

from common import protocol as P

ADMIN_ID = 0


class Conversation:
    def __init__(self, conv):
        self.conv = conv
        self.kind, self.target = P.parse_conv(conv)
        self.messages: dict[int, dict] = {}
        self.last: dict | None = None
        self.unread = 0
        self.history_requested = False
        self.complete = False
        self.draft = ""
        self.read_up_to = 0          # (direct chats) highest id the other side has read
        self.delivered_up_to = 0
        self.pins: list[dict] = []

    @property
    def last_ts(self):
        return self.last["ts"] if self.last else 0

    def oldest_id(self):
        return min(self.messages) if self.messages else None

    def add(self, msg):
        self.messages[msg["id"]] = msg
        if not self.last or msg["id"] >= self.last["id"]:
            self.last = msg


class Store(QObject):
    users_changed = Signal()
    user_updated = Signal(int)
    rooms_changed = Signal()
    room_removed = Signal(int)
    conv_changed = Signal(str)             # last message / unread count of a conversation
    message_added = Signal(dict, bool)     # message, is_new (live, not history)
    message_updated = Signal(dict)         # edited / deleted
    pins_changed = Signal(str)             # conv
    history_loaded = Signal(str, list, bool)
    receipt = Signal(str)                  # conv whose ticks changed
    typing = Signal(str, int)
    announcement = Signal(dict)            # new announcement pushed live
    announcements_changed = Signal()
    me_changed = Signal()
    unread_changed = Signal(int)
    update_available = Signal(dict)

    def __init__(self, conn):
        super().__init__()
        self.conn = conn
        self.me = {}
        self.users: dict[int, dict] = {}
        self.rooms: dict[int, dict] = {}
        self.convs: dict[str, Conversation] = {}
        self.announcements: list[dict] = []
        self.names: dict[int, str] = {}           # sender names seen in messages (incl. deleted users)
        self.muted: set[str] = set()
        self.server_name = ""
        self.max_file_size = 0
        self.is_viewing = lambda conv: False      # set by the main window
        conn.event.connect(self.handle_event)

    # ------------------------------------------------------------ helpers
    @property
    def my_id(self):
        return self.me.get("id")

    def conversation(self, conv) -> Conversation:
        if conv not in self.convs:
            self.convs[conv] = Conversation(conv)
        return self.convs[conv]

    def user_name(self, uid):
        if uid == ADMIN_ID:
            return "Administrator"
        if uid == self.my_id:
            return self.me.get("name", "Me")
        u = self.users.get(uid)
        return u["name"] if u else self.names.get(uid, "Unknown user")

    def remember_name(self, msg):
        if msg.get("sender_name"):
            self.names[msg["sender_id"]] = msg["sender_name"]

    def title(self, conv):
        kind, target = P.parse_conv(conv)
        if kind == "u":
            return self.user_name(target)
        room = self.rooms.get(target)
        return room["name"] if room else "Room"

    def conv_exists(self, conv):
        kind, target = P.parse_conv(conv)
        return (target in self.users or target == self.my_id) if kind == "u" else target in self.rooms

    def total_unread(self):
        return sum(c.unread for c in self.convs.values() if self.conv_exists(c.conv) and c.conv not in self.muted)

    def is_muted(self, conv):
        return conv in self.muted

    def mentions_me(self, msg) -> bool:
        body = (msg.get("body") or "").lower()
        name = (self.me.get("username") or "").lower()
        if not name or "@" not in body:
            return False
        i = body.find("@" + name)
        while i >= 0:
            end = i + 1 + len(name)
            if end >= len(body) or not (body[end].isalnum() or body[end] in "._-"):
                return True
            i = body.find("@" + name, end)
        return False

    def unread_announcements(self):
        return sum(1 for a in self.announcements if not a.get("read"))

    def all_people(self):
        """Visible users plus me (for org views)."""
        return list(self.users.values()) + ([dict(self.me, status=self.me.get("status", "online"))]
                                            if self.me else [])

    def departments(self):
        return sorted({u["department"] for u in self.all_people() if u.get("department")}, key=str.lower)

    def sections(self, department):
        return sorted({u["section"] for u in self.all_people() if u.get("section")
                       and u.get("department", "").lower() == department.lower()}, key=str.lower)

    def perm(self, key):
        return (self.me.get("perms") or {}).get(key)

    def direct_reports(self, uid=None):
        uid = self.my_id if uid is None else uid
        return [u for u in self.users.values() if u.get("manager_id") == uid]

    def manager_name(self, uid):
        u = self.me if uid == self.my_id else self.users.get(uid, {})
        m = u.get("manager_id")
        return self.user_name(m) if m and (m in self.users or m == self.my_id) else ""

    @staticmethod
    def status_text(u):
        """Custom status, e.g. '🍽️ Lunch' (empty if none)."""
        return " ".join(x for x in ((u or {}).get("status_emoji", ""), (u or {}).get("status_msg", "")) if x)

    def designation_line(self, u):
        """'Lead · Compositing · Roto'"""
        return " · ".join(x for x in (u.get("designation") or u.get("title"), u.get("department"),
                                       u.get("section")) if x)

    # ----------------------------------------------------------- bootstrap
    def load(self, boot):
        """Apply a login_ok payload. Also used after reconnecting."""
        self.me = boot["me"]
        self.server_name = boot.get("server_name", "")
        self.max_file_size = boot.get("max_file_size", 0)
        self.users = {u["id"]: u for u in boot["users"] if u["id"] != self.my_id}
        self.rooms = {r["id"]: r for r in boot["rooms"]}
        for item in boot["recent"]:
            c = self.conversation(item["conv"])
            c.unread = item["unread"]
            if item["last"]:
                self.remember_name(item["last"])
                c.add(item["last"])
        # after a reconnect the cached history may have gaps: reload lazily
        for c in self.convs.values():
            c.history_requested = False
        self.announcements = boot.get("announcements", [])
        self.muted = set(boot.get("muted", []))
        self.review_notice = bool(boot.get("review_notice"))
        if boot.get("update"):
            self.update_available.emit(boot["update"])
        self.me_changed.emit()
        self.users_changed.emit()
        self.rooms_changed.emit()
        self.announcements_changed.emit()
        self.unread_changed.emit(self.total_unread())

    # -------------------------------------------------------------- events
    def handle_event(self, ev):
        op = ev.get("op")
        if op == "message":
            self.add_message(ev["message"], live=True)
        elif op == "message_update":
            m = ev["message"]
            self.remember_name(m)
            c = self.conversation(m["conv"])
            if m["id"] in c.messages or (c.last and c.last["id"] == m["id"]):
                c.messages[m["id"]] = m
                if c.last and c.last["id"] == m["id"]:
                    c.last = m
                self.message_updated.emit(m)
                self.conv_changed.emit(m["conv"])
        elif op == "pins":
            self.conversation(ev["conv"]).pins = ev["pins"]
            self.pins_changed.emit(ev["conv"])
        elif op == "muted":
            (self.muted.add if ev["muted"] else self.muted.discard)(ev["conv"])
            self.conv_changed.emit(ev["conv"])
            self.unread_changed.emit(self.total_unread())
        elif op == "update_available":
            self.update_available.emit(ev["update"])
        elif op == "presence":
            uid = ev["user_id"]
            if uid in self.users:
                self.users[uid]["status"] = ev["status"]
                self.users[uid]["status_msg"] = ev.get("status_msg", "")
                self.users[uid]["status_emoji"] = ev.get("status_emoji", "")
                if ev["status"] == "offline":
                    self.users[uid]["last_seen"] = time.time()
                self.user_updated.emit(uid)
        elif op == "my_status":
            self.me["status"] = ev["status"]
            self.me["status_msg"] = ev["status_msg"]
            self.me["status_emoji"] = ev.get("status_emoji", "")
            self.me["status_until"] = ev.get("status_until")
            self.me_changed.emit()
        elif op == "user":
            u = ev["user"]
            if u["id"] == self.my_id:
                self.me.update({k: v for k, v in u.items() if k != "status"})
                self.me_changed.emit()
            else:
                self.users[u["id"]] = u
                self.users_changed.emit()
        elif op == "directory":
            status = self.me.get("status")
            self.me = ev["me"]
            if status:
                self.me["status"] = status
            self.users = {u["id"]: u for u in ev["users"] if u["id"] != self.my_id}
            self.me_changed.emit()
            self.users_changed.emit()
        elif op == "user_removed":
            if self.users.pop(ev["user_id"], None):
                self.users_changed.emit()
        elif op == "room":
            self.rooms[ev["room"]["id"]] = ev["room"]
            self.conversation(P.room_conv(ev["room"]["id"]))
            self.rooms_changed.emit()
        elif op == "room_removed":
            rid = ev["room_id"]
            self.rooms.pop(rid, None)
            self.convs.pop(P.room_conv(rid), None)
            self.rooms_changed.emit()
            self.room_removed.emit(rid)
            self.unread_changed.emit(self.total_unread())
        elif op == "receipt":
            c = self.conversation(ev["conv"])
            for m in c.messages.values():
                if m["sender_id"] == self.my_id and m["id"] <= ev["up_to"]:
                    m["delivered"] = True
                    if ev["type"] == "read":
                        m["read"] = True
            self.receipt.emit(ev["conv"])
        elif op == "read_sync":
            c = self.conversation(ev["conv"])
            c.unread = 0
            self.conv_changed.emit(ev["conv"])
            self.unread_changed.emit(self.total_unread())
        elif op == "typing":
            self.typing.emit(ev["conv"], ev["user_id"])
        elif op == "announcement":
            ann = ev["announcement"]
            self.announcements.insert(0, ann)
            self.announcements_changed.emit()
            self.announcement.emit(ann)

    def add_message(self, msg, live=False):
        self.remember_name(msg)
        c = self.conversation(msg["conv"])
        is_new = msg["id"] not in c.messages
        c.add(msg)
        if live and is_new and msg["sender_id"] != self.my_id and msg["kind"] != "system":
            if self.is_viewing(msg["conv"]):
                self.mark_read(msg["conv"])
            else:
                c.unread += 1
                self.unread_changed.emit(self.total_unread())
        self.message_added.emit(msg, is_new and live)
        self.conv_changed.emit(msg["conv"])

    # ------------------------------------------------------------- actions
    def load_history(self, conv, older=False):
        c = self.conversation(conv)
        if older and c.complete:
            return
        if not older and c.history_requested:
            return
        c.history_requested = True
        before = c.oldest_id() if older else None

        def done(reply):
            if not reply.get("ok"):
                c.history_requested = False
                return
            msgs = reply["messages"]
            for m in msgs:
                self.remember_name(m)
                c.messages[m["id"]] = m
            if msgs and (not c.last or msgs[-1]["id"] >= c.last["id"]):
                c.last = msgs[-1]
            c.complete = reply.get("complete", False)
            self.history_loaded.emit(conv, msgs, older)
        self.conn.request("history", done, conv=conv, before=before, limit=60)

    def load_pins(self, conv):
        def done(reply):
            if reply.get("ok"):
                self.conversation(conv).pins = reply["pins"]
                self.pins_changed.emit(conv)
        self.conn.request("pins", done, conv=conv)

    def set_muted(self, conv, muted):
        (self.muted.add if muted else self.muted.discard)(conv)
        self.conn.send("mute", conv=conv, muted=muted)
        self.conv_changed.emit(conv)
        self.unread_changed.emit(self.total_unread())

    def mark_read(self, conv):
        c = self.conversation(conv)
        had = c.unread
        c.unread = 0
        if c.last:
            self.conn.send("mark_read", conv=conv, up_to=c.last["id"])
        if had:
            self.conv_changed.emit(conv)
            self.unread_changed.emit(self.total_unread())

    def mark_announcement_read(self, ann_id):
        for a in self.announcements:
            if a["id"] == ann_id and not a.get("read"):
                a["read"] = True
                self.conn.send("announcement_read", id=ann_id)
                self.announcements_changed.emit()
