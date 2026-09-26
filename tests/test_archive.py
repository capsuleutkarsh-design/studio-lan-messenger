"""Readable chat backup (text logs) and message retention."""

import glob
import os
import shutil
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.core import ServerCore  # noqa: E402
from tests import test_server as base  # noqa: E402

PORT = 15550


class Client(base.Client):
    def __init__(self, username, password="Artist2026"):
        base.PORT, old = PORT, base.PORT
        try:
            super().__init__(username, password)
        finally:
            base.PORT = old


class ArchiveTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.core = c = ServerCore(self.tmp)
        c.config.update(tcp_port=PORT, discovery_port=PORT + 1,
                        message_retention_days=90)
        c.start()
        mk = lambda u, n: c.call(c.admin_create_user, must_change=False, username=u,  # noqa: E731
                                 password="Artist2026", display_name=n)
        self.a, self.b = mk("ann", "Ann Rao"), mk("ben", "Ben Das")

    def tearDown(self):
        self.core.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _age(self, msg_id, days):
        db = self.core.db
        self.core.call(lambda: db._exec("UPDATE messages SET created_at=? WHERE id=?",
                                        time.time() - days * 86400, msg_id))

    def test_backup_then_retention(self):
        a = Client("ann")
        room = a.request("create_room", name="Comp Team", members=[self.b])["room_id"]
        old = a.request("send", conv=f"u:{self.b}", text="Old news\nsecond line")["message"]
        a.request("react", message_id=old["id"], emoji="👍")
        a.request("pin", conv=f"u:{self.b}", message_id=old["id"], pinned=True)
        poll = a.request("create_poll", conv=f"r:{room}", question="Lunch?", options=["Pizza", "Thali"])["message"]
        a.request("vote", poll_id=poll["poll"]["id"], options=[1])
        new = a.request("send", conv=f"u:{self.b}", text="Fresh message")["message"]
        self._age(old["id"], 120)
        self._age(poll["id"], 120)

        # retention never removes anything that is not in the text logs yet
        self.assertEqual(self.core.call(lambda: __import__("server.archive", fromlist=["x"]).apply_retention(
            self.core.db, self.core.config)), 0)

        result = self.core.call(self.core.chat_backup_now)
        self.assertTrue(result["ok"], result)
        self.assertGreaterEqual(result["messages"], 4)
        self.assertEqual(result["removed"], 2)                 # the two old ones
        text = ""
        for path in glob.glob(os.path.join(result["folder"], "*", "*.txt")):
            with open(path, encoding="utf-8") as f:
                text += f.read()
        self.assertIn("Ann Rao: Old news", text)
        self.assertIn("second line", text)
        self.assertIn("Fresh message", text)
        self.assertIn("[poll] Lunch?", text)
        self.assertIn("Thali (1)", text)
        self.assertTrue(any("Room - Comp Team" in p for p in glob.glob(os.path.join(result["folder"], "*", "*"))))

        hist = a.request("history", conv=f"u:{self.b}")["messages"]
        ids = [m["id"] for m in hist]
        self.assertNotIn(old["id"], ids)                        # moved out of the app
        self.assertIn(new["id"], ids)
        self.assertEqual(a.request("pins", conv=f"u:{self.b}")["pins"], [])

        # the next run only appends what is new, never the same message twice
        a.request("send", conv=f"u:{self.b}", text="Tomorrow's message")
        again = self.core.call(self.core.chat_backup_now)
        self.assertEqual(again["messages"], 1)
        text = ""
        for path in glob.glob(os.path.join(result["folder"], "*", "*.txt")):
            with open(path, encoding="utf-8") as f:
                text += f.read()
        self.assertEqual(text.count("Fresh message"), 1)
        a.close()

    def test_retention_off_keeps_everything(self):
        self.core.config.update(message_retention_days=0)
        a = Client("ann")
        m = a.request("send", conv=f"u:{self.b}", text="Keep me")["message"]
        self._age(m["id"], 400)
        result = self.core.call(self.core.chat_backup_now)
        self.assertEqual(result["removed"], 0)
        self.assertIn(m["id"], [x["id"] for x in a.request("history", conv=f"u:{self.b}")["messages"]])
        a.close()


if __name__ == "__main__":
    unittest.main()
