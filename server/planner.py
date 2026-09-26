"""Reminders and scheduled messages (kept on the server, so they fire even when the PC was off).

Reminder:          "remind me about this message / chat / thing at 15:00"
                   pending (0) -> fired (1, shown to the user until they press Done) -> done (2)
Scheduled message: written now, sent by the server at a chosen time as if the user sent it then
                   pending -> sent | failed | cancelled
"""

import asyncio
import logging
import math
import time
from types import SimpleNamespace

from common import protocol as P

log = logging.getLogger("server")

MAX_AHEAD = 366 * 86400          # nothing further than a year ahead
MAX_PENDING = 200                # per person
LATE_LIMIT = 12 * 3600           # a scheduled message this late (server was off) is not sent any more


class PlannerMixin:
    """Mixed into ServerCore. Needs: db, sessions, push_user, _post, _internal_conv, msg_for."""

    # ------------------------------------------------------------ helpers
    def _due(self, value):
        from server.core import ClientError
        try:
            due = float(value)
        except (TypeError, ValueError, OverflowError):
            raise ClientError("Pick a date and time")
        if not math.isfinite(due):
            raise ClientError("Pick a date and time")
        if due < time.time() - 60:
            raise ClientError("That time is in the past")
        due = max(due, time.time())                  # "now" arrives a moment late over the network
        if due > time.time() + MAX_AHEAD:
            raise ClientError("Pick a time within the next year")
        return due

    def _reminder_public(self, r):
        d = {"id": r["id"], "conv": r["conv"] or "", "text": r["text"], "due_at": r["due_at"],
             "state": r["state"], "message_id": r["message_id"]}
        if r["message_id"]:
            m = self.db.get_message(r["message_id"])
            # only while the person can still see that chat (they may have left the room since)
            if m and not m["deleted"] and r["user_id"] in self._participants(m):
                d["snippet"] = ("Sticker" if m["kind"] == "sticker" else m["body"][:200]
                                or (f"📎 {m['file_name']}" if m["file_name"] else ""))
                d["sender_name"] = self._user_name(m["sender_id"])
        return d

    def _push_reminders(self, uid):
        rows = self.db.reminders_for(uid)
        self.push_user(uid, {"op": "reminders", "reminders": [self._reminder_public(r) for r in rows]})

    def _scheduled_public(self, r):
        return {"id": r["id"], "conv": r["conv"], "text": r["text"], "sticker": r["sticker"] or "",
                "due_at": r["due_at"], "state": r["state"], "error": r["error"] or ""}

    def _push_scheduled(self, uid):
        rows = self.db.scheduled_for(uid)
        self.push_user(uid, {"op": "scheduled", "scheduled": [self._scheduled_public(r) for r in rows]})

    # ---------------------------------------------------------- reminders
    def h_reminder_add(self, s, req):
        from server.core import ClientError
        text = str(req.get("text") or "").strip()[:500]
        due = self._due(req.get("due_at"))
        conv, message_id = str(req.get("conv") or ""), None
        if conv:
            self._internal_conv(s, conv)                     # must be a chat this person is in
        if req.get("message_id"):
            m = self.db.get_message(int(req["message_id"]))
            if not m or m["deleted"] or s.user_id not in self._participants(m):
                raise ClientError("Message not found")
            message_id = m["id"]
        if not text and not message_id:
            raise ClientError("Write what to remind you about")
        if self.db.count_pending_reminders(s.user_id) >= MAX_PENDING:
            raise ClientError("You have too many reminders — clear some first")
        rid = self.db.add_reminder(s.user_id, conv, message_id, text, due)
        self._push_reminders(s.user_id)
        return {"reminder": self._reminder_public(self.db.get_reminder(rid))}

    def _own_reminder(self, s, rid):
        from server.core import ClientError
        r = self.db.get_reminder(int(rid or 0))
        if not r or r["user_id"] != s.user_id:
            raise ClientError("Reminder not found")
        return r

    def h_reminder_done(self, s, req):
        r = self._own_reminder(s, req.get("id"))
        self.db.set_reminder_state(r["id"], 2)
        self._push_reminders(s.user_id)

    def h_reminder_delete(self, s, req):
        r = self._own_reminder(s, req.get("id"))
        self.db.delete_reminder(r["id"])
        self._push_reminders(s.user_id)

    def h_reminder_snooze(self, s, req):
        from server.core import ClientError
        r = self._own_reminder(s, req.get("id"))
        if r["state"] == 2:
            raise ClientError("That reminder is already done")
        due = self._due(req.get("due_at")) if req.get("due_at") else time.time() + 60 * max(
            1, min(int(req.get("minutes") or 10), 7 * 24 * 60))
        self.db.reschedule_reminder(r["id"], due)
        self._push_reminders(s.user_id)

    def h_reminders(self, s, req):
        return {"reminders": [self._reminder_public(r) for r in self.db.reminders_for(s.user_id)]}

    # ------------------------------------------------- scheduled messages
    def h_schedule_add(self, s, req):
        from server.core import STICKER_RE, ClientError
        conv = str(req.get("conv") or "")
        kind, target, _ = self._internal_conv(s, conv)
        if kind == "u":
            other = self.db.get_user(target)
            if not other or other["deleted"] or not self.can_see(s.user_id, target):
                raise ClientError("User not found")
        text = req.get("text") or ""
        sticker = str(req.get("sticker") or "")
        if not isinstance(text, str) or len(text) > P.MAX_TEXT:
            raise ClientError("Message too long")
        if sticker and not STICKER_RE.match(sticker):
            raise ClientError("Unknown sticker")
        if not text.strip() and not sticker:
            raise ClientError("Write the message first")
        if self.db.count_pending_scheduled(s.user_id) >= MAX_PENDING:
            raise ClientError("You have too many scheduled messages")
        sid = self.db.add_scheduled(s.user_id, conv, text, sticker, self._due(req.get("due_at")))
        self._push_scheduled(s.user_id)
        return {"scheduled": self._scheduled_public(self.db.get_scheduled(sid))}

    def _own_scheduled(self, s, sid):
        from server.core import ClientError
        r = self.db.get_scheduled(int(sid or 0))
        if not r or r["user_id"] != s.user_id or r["state"] != "pending":
            raise ClientError("That scheduled message is not waiting any more")
        return r

    def h_schedule_update(self, s, req):
        from server.core import ClientError
        r = self._own_scheduled(s, req.get("id"))
        text = req.get("text", r["text"])
        if not isinstance(text, str) or len(text) > P.MAX_TEXT or (not text.strip() and not r["sticker"]):
            raise ClientError("Invalid message")
        due = self._due(req["due_at"]) if req.get("due_at") else r["due_at"]
        self.db.update_scheduled(r["id"], text, due)
        self._push_scheduled(s.user_id)

    def h_schedule_delete(self, s, req):
        r = self._own_scheduled(s, req.get("id"))
        self.db.set_scheduled_state(r["id"], "cancelled")
        self._push_scheduled(s.user_id)

    def h_schedule_send_now(self, s, req):
        r = self._own_scheduled(s, req.get("id"))
        self._send_scheduled(r)

    def h_scheduled(self, s, req):
        return {"scheduled": [self._scheduled_public(r) for r in self.db.scheduled_for(s.user_id)]}

    def _send_scheduled(self, r, late=False):
        from server.core import ClientError
        user = self.db.get_user(r["user_id"])
        try:
            if late:
                raise ClientError("Not sent: the server was off at the scheduled time")
            if not user or user["deleted"] or user["disabled"]:
                raise ClientError("The account is disabled")
            kind, target = P.parse_conv(r["conv"])
            sender = SimpleNamespace(user_id=r["user_id"])     # stands in for a live session
            key = self._internal_conv(sender, r["conv"])[2]
            text, msg_kind = (r["sticker"], "sticker") if r["sticker"] else (r["text"], "text")
            out = self._post(sender, kind, target, key, text, msg_kind)
            self.db.set_scheduled_state(r["id"], "sent", message_id=out["message"]["id"])
        except (ClientError, ValueError) as e:
            self.db.set_scheduled_state(r["id"], "failed", error=str(e))
            log.info("Scheduled message %s not sent: %s", r["id"], e)
        except Exception:  # noqa: BLE001 - never retry a broken row forever
            log.exception("Scheduled message %s failed", r["id"])
            self.db.set_scheduled_state(r["id"], "failed", error="Server error")
        self._push_scheduled(r["user_id"])

    # ------------------------------------------------------------ the clock
    async def _planner(self):
        while True:
            await asyncio.sleep(1)            # to the second: "send in 30 seconds" means 30 seconds
            try:
                self.run_due()
            except Exception:  # noqa: BLE001 - the clock must keep ticking
                log.exception("planner failed")

    def run_due(self, now=None):
        now = now or time.time()
        for r in self.db.due_scheduled(now):
            self._send_scheduled(r, late=now - r["due_at"] > LATE_LIMIT)
        fired = {}
        for r in self.db.due_reminders(now):
            try:              # each item on its own: one bad row must not hold up everyone else's
                self.db.set_reminder_state(r["id"], 1)
                fired.setdefault(r["user_id"], []).append(self._reminder_public(self.db.get_reminder(r["id"])))
            except Exception:  # noqa: BLE001
                log.exception("Reminder %s failed", r["id"])
        for uid, items in fired.items():
            for item in items:
                self.push_user(uid, {"op": "reminder", "reminder": item})
            self._push_reminders(uid)

    def planner_boot(self, uid):
        """Login payload part: reminders (including ones that fired while the PC was off) + scheduled."""
        return {"reminders": [self._reminder_public(r) for r in self.db.reminders_for(uid)],
                "scheduled": [self._scheduled_public(r) for r in self.db.scheduled_for(uid)]}

    PLANNER_HANDLERS = ("reminder_add", "reminder_done", "reminder_delete", "reminder_snooze", "reminders",
                        "schedule_add", "schedule_update", "schedule_delete", "schedule_send_now", "scheduled")
