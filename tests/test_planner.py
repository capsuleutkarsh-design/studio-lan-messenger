"""Reminders and scheduled messages."""

import os
import shutil
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.core import ServerCore  # noqa: E402
from tests import test_server as base  # noqa: E402

PORT = 15650


class Client(base.Client):
    def __init__(self, username, password="Artist2026"):
        base.PORT, old = PORT, base.PORT
        try:
            super().__init__(username, password)
        finally:
            base.PORT = old


class PlannerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.core = c = ServerCore(cls.tmp)
        c.config.update(tcp_port=PORT, discovery_port=PORT + 1)
        c.start()
        mk = lambda u: c.call(c.admin_create_user, must_change=False, username=u,  # noqa: E731
                              password="Artist2026", display_name=u.title())
        cls.a, cls.b, cls.c = mk("ann"), mk("ben"), mk("cat")

    @classmethod
    def tearDownClass(cls):
        cls.core.stop()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def later(self, seconds):
        self.core.call(self.core.run_due, time.time() + seconds)

    def test_reminders(self):
        a, b = Client("ann"), Client("ben")
        m = b.request("send", conv=f"u:{self.a}", text="Send me the FAL_030 comp by 5")["message"]
        r = a.request("reminder_add", message_id=m["id"], conv=f"u:{self.b}", due_at=time.time() + 1800)
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["reminder"]["snippet"], "Send me the FAL_030 comp by 5")
        self.assertEqual(r["reminder"]["sender_name"], "Ben")
        free = a.request("reminder_add", text="Call the client", due_at=time.time() + 3600)["reminder"]
        # validation
        self.assertFalse(a.request("reminder_add", text="x", due_at=time.time() - 3600)["ok"])
        self.assertFalse(a.request("reminder_add", text="", due_at=time.time() + 60)["ok"])
        other = b.request("send", conv=f"u:{self.c}", text="private")["message"]
        self.assertFalse(a.request("reminder_add", message_id=other["id"], due_at=time.time() + 60)["ok"])
        self.assertFalse(b.request("reminder_done", id=free["id"])["ok"])      # not Ben's reminder
        # it fires at its time, only for Ann
        self.later(1900)
        fired = a.wait_for("reminder")["reminder"]
        self.assertEqual(fired["id"], r["reminder"]["id"])
        self.assertEqual(fired["state"], 1)
        # a fired reminder is still in the list after signing in again, until Done
        a2 = Client("ann")
        self.assertIn(r["reminder"]["id"], [x["id"] for x in a2.login["reminders"] if x["state"] == 1])
        self.assertTrue(a.request("reminder_snooze", id=fired["id"], minutes=10)["ok"])
        self.later(1900 + 700)
        self.assertEqual(a.wait_for("reminder")["reminder"]["id"], fired["id"])
        self.assertTrue(a.request("reminder_done", id=fired["id"])["ok"])
        self.assertTrue(a.request("reminder_delete", id=free["id"])["ok"])
        self.assertEqual(a.request("reminders")["reminders"], [])
        for x in (a, a2, b):
            x.close()

    def test_scheduled_messages(self):
        a, b = Client("ann"), Client("ben")
        s1 = a.request("schedule_add", conv=f"u:{self.b}", text="Good morning! Dailies at 10", due_at=time.time() + 600)
        self.assertTrue(s1["ok"], s1)
        s2 = a.request("schedule_add", conv=f"u:{self.b}", sticker="moods/07.webp", due_at=time.time() + 700)["scheduled"]
        s3 = a.request("schedule_add", conv=f"u:{self.b}", text="cancel me", due_at=time.time() + 800)["scheduled"]
        self.assertFalse(a.request("schedule_add", conv=f"u:{self.b}", text="", due_at=time.time() + 60)["ok"])
        self.assertFalse(a.request("schedule_add", conv=f"u:{self.b}", text="x", due_at=time.time() - 600)["ok"])
        self.assertTrue(a.request("schedule_update", id=s1["scheduled"]["id"], text="Good morning! Dailies at 11")["ok"])
        self.assertTrue(a.request("schedule_delete", id=s3["id"])["ok"])
        self.assertEqual(len(a.request("scheduled")["scheduled"]), 2)
        self.later(60)                                   # nothing due yet
        self.later(900)
        got = [b.wait_for("message")["message"] for _ in range(2)]
        self.assertEqual(got[0]["body"], "Good morning! Dailies at 11")
        self.assertEqual(got[0]["sender_id"], self.a)
        self.assertEqual((got[1]["kind"], got[1]["body"]), ("sticker", "moods/07.webp"))
        self.assertFalse(any(m["body"] == "cancel me" for m in b.request("history", conv=f"u:{self.a}")["messages"]))
        self.assertEqual(a.request("scheduled")["scheduled"], [])
        self.assertFalse(a.request("schedule_delete", id=s2["id"])["ok"])    # already sent
        # send now, and failure when the sender left the room
        room = b.request("create_room", name="Night shift", members=[self.a])["room_id"]
        now = a.request("schedule_add", conv=f"r:{room}", text="sending early", due_at=time.time() + 5000)["scheduled"]
        self.assertTrue(a.request("schedule_send_now", id=now["id"])["ok"])
        self.assertTrue(any(m["body"] == "sending early" for m in b.request("history", conv=f"r:{room}")["messages"]))
        late = a.request("schedule_add", conv=f"r:{room}", text="too late", due_at=time.time() + 3000)["scheduled"]
        a.request("room_leave", room_id=room)
        self.later(3100)
        failed = [x for x in a.request("scheduled")["scheduled"] if x["id"] == late["id"]]
        self.assertEqual(failed[0]["state"], "failed")
        self.assertTrue(failed[0]["error"])
        a.close()
        b.close()


if __name__ == "__main__":
    unittest.main()
