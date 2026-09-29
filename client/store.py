"""In-memory state of the logged-in client, fed by the server's messages."""

import time

from PySide6.QtCore import QObject, Signal

from common import protocol as P

ADMIN_ID = 0
KEEP_CACHED = 200        # messages kept in memory for a chat that is not on screen (older ones reload on demand)


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

    def trim(self, keep=KEEP_CACHED):
        """Forget all but the newest `keep` messages; scrolling up fetches them again from the server."""
        if len(self.messages) > keep:
            for mid in sorted(self.messages)[:-keep]:
                del self.messages[mid]
            self.complete = False


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
    reminder_fired = Signal(dict)          # a reminder is due now
    planner_changed = Signal()             # reminders / scheduled messages list changed
    calendar_changed = Signal()            # something on the calendar changed: fetch again
    thread_message = Signal(dict, bool)    # a reply in a thread (message, arrived just now)
    event_invite = Signal(dict)            # someone invited me to a meeting
    prefs_changed = Signal(str)            # a personal setting (pinned chats, focus time...) changed
    shots_changed = Signal(str)            # a shot's status changed ("" = all of them, after sign-in)

    def __init__(self, conn):
        super().__init__()
        self.conn = conn
        self.me = {}
        self.users: dict[int, dict] = {}
        self.rooms: dict[int, dict] = {}
        self.convs: dict[str, Conversation] = {}
        self.my_threads: set[int] = set()   # threads I replied in (their first message's id)
        self.announcements: list[dict] = []
        self.names: dict[int, str] = {}           # sender names seen in messages (incl. deleted users)
        self.muted: set[str] = set()
        self.reminders: list[dict] = []
        self.scheduled: list[dict] = []
        self.calendar = None                      # the next two weeks (Home, meeting reminders)
        self.server_name = ""
        self.max_file_size = 0
        self.prefs = {}                           # personal settings kept on the server (follow me to any PC)
        self.shots = {}                           # "FAL_030" -> (status, set by, when)
        self.shot_pattern = P.SHOT_PATTERN_DEFAULT
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
            return "My space" if target == self.my_id else self.user_name(target)
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
        """@me, @everyone, @here or my department/section - only in rooms (a direct chat is already to me)."""
        from client import mentions
        if not str(msg.get("conv", "")).startswith("r:") or "@" not in (msg.get("body") or ""):
            return False
        return mentions.mentions(msg["body"], self.me)

    def mention_marker(self):
        """Highlights @tokens in message HTML: people and groups in the accent colour, me as a chip."""
        from client import mentions
        from common import theme as T
        known = {u["username"].lower() for u in self.users.values()} | set(mentions.GROUPS)
        for u in list(self.users.values()) + [self.me]:
            for key in ("department", "section"):
                if u.get(key):
                    known.add(mentions.group_token(u[key]).lower())
        mine = mentions.my_tokens(self.me)
        return lambda escaped: mentions.mark(escaped, known, mine, T.ACCENT, T.ACCENT_SOFT)

    def unread_announcements(self):
        return sum(1 for a in self.announcements if not a.get("read"))

    @staticmethod
    def is_builtin(u):
        """The server's own "admin" account (Administrator): not a colleague, so not in people lists."""
        return (u or {}).get("username") == "admin"

    def all_people(self):
        """Visible users plus me (for org views)."""
        return [u for u in self.users.values() if not self.is_builtin(u)] + (
            [dict(self.me, status=self.me.get("status", "online"))] if self.me else [])

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
        """Custom status, e.g. '🍽️ Lunch' (empty if none); 'On leave' while on leave."""
        custom = " ".join(x for x in ((u or {}).get("status_emoji", ""), (u or {}).get("status_msg", "")) if x)
        if (u or {}).get("on_leave") and not custom:
            return "🌴 On leave"
        return custom

    def designation_line(self, u):
        """'Lead · Compositing · Roto'"""
        parts = []
        for x in (u.get("designation") or u.get("title"), u.get("department"), u.get("section")):
            if x and x.lower() not in (p.lower() for p in parts):
                parts.append(x)
        return " · ".join(parts)

    # ----------------------------------------------------------- bootstrap
    def reset(self):
        """Forget everything about the previous account (sign-out, or someone else signs in on this PC)."""
        self.me = {}
        self.users, self.rooms, self.convs, self.names = {}, {}, {}, {}
        self.announcements, self.reminders, self.scheduled = [], [], []
        self.muted = set()
        self.my_threads = set()
        self.prefs = {}

    def load(self, boot):
        """Apply a login_ok payload. Also used after reconnecting."""
        if self.me and self.me.get("id") != boot["me"]["id"]:
            self.reset()
        # after a reconnect the cache may miss messages sent while we were away: start each chat's cache
        # over (drafts, pins and unread counts stay) so the next look loads it cleanly from the server
        for c in self.convs.values():
            c.messages.clear()
            c.history_requested = False
            c.complete = False
        self.me = boot["me"]
        self.server_name = boot.get("server_name", "")
        self.max_file_size = boot.get("max_file_size", 0)
        self.trusted_link_hosts = boot.get("trusted_link_hosts", [])
        self.my_threads = set(boot.get("my_threads", []))
        self.file_retention_days = boot.get("file_retention_days", 0)
        self.buzz_enabled = boot.get("buzz_enabled", False)
        self.allow_name_change = boot.get("allow_name_change", False)
        self.reminders = boot.get("reminders", [])
        self.scheduled = boot.get("scheduled", [])
        self.calendar = boot.get("calendar")
        self.planner_changed.emit()
        self.calendar_changed.emit()
        self.users = {u["id"]: u for u in boot["users"] if u["id"] != self.my_id}
        self.rooms = {r["id"]: r for r in boot["rooms"]}
        for item in boot["recent"]:
            c = self.conversation(item["conv"])
            c.unread = item["unread"]
            last = item["last"]
            if last and not (last.get("thread_root") and not last.get("thread_broadcast")):
                self.remember_name(last)
                c.add(last)
        self.announcements = boot.get("announcements", [])
        self.muted = set(boot.get("muted", []))
        # a server before 1.10 keeps no personal settings: they then last until Quillo is closed
        self.prefs_on_server = "prefs" in boot
        self.prefs = dict(boot.get("prefs") or {})
        self.shots = {str(s).upper(): (st, uid, ts) for s, st, uid, ts in boot.get("shots") or []}
        self.shots_changed.emit("")
        self.shot_pattern = boot.get("shot_pattern", P.SHOT_PATTERN_DEFAULT)
        self.prefs_changed.emit("")
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
            if m.get("thread_root") and not m.get("thread_broadcast"):
                self.thread_message.emit(m, False)          # it lives in the thread panel, not the chat
                return
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
        elif op == "reminder":
            self.reminder_fired.emit(ev["reminder"])
        elif op == "reminders":
            self.reminders = ev["reminders"]
            self.planner_changed.emit()
        elif op == "scheduled":
            self.scheduled = ev["scheduled"]
            self.planner_changed.emit()
        elif op == "update_available":
            self.update_available.emit(ev["update"])
        elif op == "calendar_changed":
            self.calendar_changed.emit()
        elif op == "event_invite":
            self.event_invite.emit(ev)
            self.calendar_changed.emit()
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
        elif op == "pref":
            self.prefs[ev["key"]] = ev["value"]
            self.prefs_changed.emit(ev["key"])
        elif op == "shot_status":
            shot = str(ev["shot"]).upper()
            self.shots[shot] = (ev["status"], ev.get("user_id"), ev.get("ts"))
            self.shots_changed.emit(shot)
        elif op == "announcement":
            ann = ev["announcement"]
            self.announcements.insert(0, ann)
            self.announcements_changed.emit()
            self.announcement.emit(ann)

    def add_message(self, msg, live=False):
        self.remember_name(msg)
        root = msg.get("thread_root")
        if root:
            if msg["sender_id"] == self.my_id:
                self.my_threads.add(root)
            self.thread_message.emit(msg, live)
            if not msg.get("thread_broadcast"):
                return                     # it lives in the thread: not in the chat, not unread there
        c = self.conversation(msg["conv"])
        is_new = msg["id"] not in c.messages
        c.add(msg)
        if len(c.messages) > KEEP_CACHED + 50 and not self.is_viewing(msg["conv"]):
            c.trim()
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

    # ------------------------------------------------ personal settings
    def set_pref(self, key, value):
        self.prefs[key] = value
        if getattr(self, "prefs_on_server", False):
            self.conn.send("set_pref", key=key, value=value)
        self.prefs_changed.emit(key)

    # ------------------------------------------------ saved for later
    MAX_SAVED = 200

    def saved(self):
        return [x for x in self.prefs.get("saved") or [] if isinstance(x, dict) and x.get("id")]

    def is_saved(self, msg_id):
        return any(x["id"] == msg_id for x in self.saved())

    def set_saved(self, msg, saved):
        from client import stickers
        items = [x for x in self.saved() if x["id"] != msg["id"]]
        if saved:
            items.insert(0, {"id": msg["id"], "conv": msg["conv"], "sender_id": msg["sender_id"],
                             "snippet": stickers.summary(msg)[:160], "ts": msg["ts"], "saved_at": time.time()})
        self.set_pref("saved", items[:self.MAX_SAVED])

    def mark_all_read(self):
        """Every chat read at once (Monday morning)."""
        n = 0
        for c in list(self.convs.values()):
            if c.unread and self.conv_exists(c.conv):
                self.mark_read(c.conv)
                n += 1
        return n

    def shot_status(self, shot):
        return self.shots.get(str(shot).upper())

    MAX_PINNED = 10

    def pinned_chats(self):
        return [c for c in self.prefs.get("pinned_chats") or [] if isinstance(c, str) and self.conv_exists(c)]

    def is_pinned(self, conv):
        return conv in (self.prefs.get("pinned_chats") or [])

    def set_pinned(self, conv, pinned):
        """Keep a chat at the top of the Chats list (False: back among the others). False when the list is full."""
        pins = [c for c in self.prefs.get("pinned_chats") or [] if c != conv]
        if pinned:
            if len([c for c in pins if self.conv_exists(c)]) >= self.MAX_PINNED:
                return False
            pins.append(conv)
        self.set_pref("pinned_chats", pins)
        return True

    def focus_until(self):
        """End of my focus time (a timestamp), or 0 when it is not on."""
        f = self.prefs.get("focus") or {}
        until = float(f.get("until") or 0) if isinstance(f, dict) else 0
        return until if until > time.time() else 0

    def focus_people(self):
        f = self.prefs.get("focus") or {}
        return [int(u) for u in f.get("people") or [] if str(u).isdigit()] if isinstance(f, dict) else []

    def gets_through_focus(self, msg):
        """While focus time is on: @mentions, my lead ('Reports to') and the people I chose still reach me."""
        sender = msg.get("sender_id")
        return (self.mentions_me(msg) or (sender and sender == self.me.get("manager_id"))
                or sender in self.focus_people())

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
