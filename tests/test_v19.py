"""1.9.0: stability and the new pieces - uploads that carry on, sends that are never stored twice, threads,
search filters, fast sign-in, and "My space" without a phantom unread count."""

import json
import os
import shutil
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from common import protocol as P  # noqa: E402
from server.core import ServerCore  # noqa: E402
from tests import test_server as base  # noqa: E402

PORT = 16850


def connect():
    return base.tls_connect(PORT)


class Client(base.Client):
    def __init__(self, username, password="Artist2026"):
        base.PORT, old = PORT, base.PORT
        try:
            super().__init__(username, password)
        finally:
            base.PORT = old


def read_line(sock):
    buf = b""
    while not buf.endswith(b"\n"):
        chunk = sock.recv(1)
        if not chunk:
            break
        buf += chunk
    return json.loads(buf)


class V19Test(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.core = c = ServerCore(self.tmp)
        c.config.update(tcp_port=PORT, discovery_port=PORT + 1)
        c.start()
        mk = lambda u, n: c.call(c.admin_create_user, must_change=False, username=u,  # noqa: E731
                                 password="Artist2026", display_name=n)
        self.a, self.b = mk("ann", "Ann Rao"), mk("ben", "Ben Das")

    def tearDown(self):
        self.core.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ------------------------------------------------------------ uploads
    def test_an_interrupted_upload_carries_on(self):
        a = Client("ann")
        data = os.urandom(3 * P.CHUNK + 1234)
        s = connect()
        s.sendall(P.encode({"op": "upload", "token": a.login["token"], "name": "plate.exr", "size": len(data)}))
        hdr = read_line(s)
        self.assertTrue(hdr["ok"])
        self.assertEqual(hdr["offset"], 0)
        s.sendall(data[:P.CHUNK + 500])
        time.sleep(0.5)
        s.close()                                              # the network drops
        time.sleep(0.5)
        f = self.core.call(self.core.db.get_file, hdr["file_id"])
        self.assertIsNotNone(f, "the part already received must be kept")
        self.assertFalse(f["complete"])

        s = connect()
        s.sendall(P.encode({"op": "upload", "token": a.login["token"], "name": "plate.exr", "size": len(data),
                            "resume": hdr["file_id"]}))
        again = read_line(s)
        self.assertEqual(again["file_id"], hdr["file_id"])
        offset = again["offset"]
        self.assertGreater(offset, 0)
        s.sendall(data[offset:])
        done = read_line(s)
        self.assertTrue(done.get("done"), done)
        s.close()
        f = self.core.call(self.core.db.get_file, hdr["file_id"])
        with open(f["path"], "rb") as fh:
            self.assertEqual(fh.read(), data)

        # somebody else, or another size, cannot take the upload over: they start a new one
        b = Client("ben")
        s = connect()
        s.sendall(P.encode({"op": "upload", "token": b.login["token"], "name": "x", "size": 10,
                            "resume": hdr["file_id"]}))
        other = read_line(s)
        self.assertNotEqual(other["file_id"], hdr["file_id"])
        s.close()
        a.close()
        b.close()

    # ------------------------------------------------------------ outbox
    def test_a_resend_is_stored_once(self):
        a = Client("ann")
        first = a.request("send", conv=f"u:{self.b}", text="On my way", client_id="c-123")
        second = a.request("send", conv=f"u:{self.b}", text="On my way", client_id="c-123")
        self.assertEqual(first["message"]["id"], second["message"]["id"])
        self.assertTrue(second.get("duplicate"))
        history = a.request("history", conv=f"u:{self.b}")["messages"]
        self.assertEqual(sum(m["body"] == "On my way" for m in history), 1)
        a.close()

    # ------------------------------------------------------------ threads
    def test_threads(self):
        a, b = Client("ann"), Client("ben")
        room = a.request("create_room", name="Comp", members=[self.b])["room_id"]
        root = a.request("send", conv=f"r:{room}", text="FAL_030 v12 is up")["message"]
        r1 = b.request("send", conv=f"r:{room}", text="Edge looks soft", thread_root=root["id"])["message"]
        self.assertEqual(r1["thread_root"], root["id"])
        # Ben's reply lives only in the thread: it does not make the room unread for Ann
        unread = {(k, t): n for k, t, _last, n in self.core.call(self.core.db.recent_conversations, self.a)}
        self.assertEqual(unread[("r", room)], 0)
        # a reply to a reply stays in the same thread
        r2 = a.request("send", conv=f"r:{room}", text="Fixed in v13", thread_root=r1["id"], also_chat=True)
        self.assertEqual(r2["message"]["thread_root"], root["id"])
        self.assertTrue(r2["message"]["thread_broadcast"])

        timeline = [m["body"] for m in a.request("history", conv=f"r:{room}")["messages"]]
        self.assertIn("FAL_030 v12 is up", timeline)
        self.assertNotIn("Edge looks soft", timeline)            # only in the thread
        self.assertIn("Fixed in v13", timeline)                  # "also send to the chat"
        thread = b.request("thread", id=root["id"])
        self.assertEqual(thread["root"]["thread_count"], 2)
        self.assertEqual([m["body"] for m in thread["messages"]], ["Edge looks soft", "Fixed in v13"])
        update = a.wait_for("message_update")["message"]          # everyone sees "2 replies"
        self.assertEqual(update["id"], root["id"])

        # outsiders cannot open it
        self.core.call(self.core.admin_create_user, must_change=False, username="cat", password="Artist2026")
        c = Client("cat")
        self.assertFalse(c.request("thread", id=root["id"])["ok"])
        for cl in (a, b, c):
            cl.close()

    # ------------------------------------------------------------ search
    def test_search_filters(self):
        a = Client("ann")
        room = a.request("create_room", name="Lighting", members=[self.b])["room_id"]
        a.request("send", conv=f"r:{room}", text="render farm is busy")
        a.request("send", conv=f"u:{self.b}", text="render done for FAL_010")
        b = Client("ben")
        b.request("send", conv=f"u:{self.a}", text="render queued")
        everything = a.request("search", query="render")["messages"]
        self.assertEqual(len(everything), 3)
        in_room = a.request("search", query="render", conv=f"r:{room}")["messages"]
        self.assertEqual([m["body"] for m in in_room], ["render farm is busy"])
        from_ben = a.request("search", query="render", **{"from": self.b})["messages"]
        self.assertEqual([m["body"] for m in from_ben], ["render queued"])
        today = time.strftime("%Y-%m-%d")
        self.assertEqual(len(a.request("search", query="render", since=today, until=today)["messages"]), 3)
        self.assertEqual(a.request("search", query="render", until="2020-01-01")["messages"], [])
        self.assertFalse(a.request("search", query="r")["ok"])                  # too short, no filter
        self.assertTrue(a.request("search", query="", **{"from": self.b})["ok"])  # a filter is enough
        a.close()
        b.close()

    # ------------------------------------------------------------ sign-in
    def test_my_space_is_not_unread(self):
        db = self.core.db
        from server.db import direct_key
        self.core.call(lambda: db.add_message(direct_key(self.a, self.a), self.a, "note to self",
                                              recipient_id=self.a))
        convs = self.core.call(db.recent_conversations, self.a)
        mine = [c for c in convs if c[0] == "u" and c[1] == self.a]
        self.assertEqual(mine[0][3], 0)

    def test_sign_in_uses_indexes(self):
        plan = self.core.call(lambda: self.core.db._all(
            "EXPLAIN QUERY PLAN SELECT conv, MAX(id) FROM messages WHERE sender_id=? AND recipient_id IS NOT NULL"
            " GROUP BY conv", self.a))
        self.assertTrue(any("idx_msg_sender_conv" in r[3] for r in plan), [r[3] for r in plan])

    # ------------------------------------------------------------ admin work in the background
    def test_backup_does_not_block_chat(self):
        """While a backup runs, the server still answers."""
        import asyncio
        a = Client("ann")
        c = self.core
        fut = asyncio.run_coroutine_threadsafe(c.backup_now(), c.loop)
        self.assertTrue(a.request("ping")["ok"])
        self.assertTrue(fut.result(60)["ok"])
        a.close()

    # ------------------------------------------------------------ found in the 1.9.0 review
    def test_restart_starts_the_jobs_again(self):
        """Stop / Start in the console: a job left 'running' by the old loop must not block the new one."""
        c = self.core
        c.call(c._start_job, "backup", c.backup_async)
        c.stop()
        c.start()
        self.assertEqual(c.__dict__.get("_jobs"), {})
        self.assertTrue(c.call(c.backup_now)["ok"])

    def test_backup_due_looks_where_backups_go(self):
        c = self.core
        central = tempfile.mkdtemp()                  # the file server: outside the server's data folder
        self.addCleanup(shutil.rmtree, central, True)
        c.config.update(storage_dir=central, backup_hour=0)
        self.assertTrue(c.call(c._backup_due))
        self.assertTrue(c.call(c.backup_now)["ok"])
        self.assertIn("Database backups", c.backup_folder())
        c.last_backup = None                      # as after a restart
        self.assertFalse(c.call(c._backup_due), "today's backup is in the central folder already")

    def test_no_snapshot_is_left_behind(self):
        c = self.core
        stale = c.config.db_path + ".snapshot-1-1.tmp"
        with open(stale, "wb") as f:
            f.write(b"x" * 100)
        c.db.remove_stale_snapshots()
        self.assertFalse(os.path.exists(stale))
        target = os.path.join(self.tmp, "no such folder", "copy.db")
        with self.assertRaises(Exception):
            c.db.backup_to(target)
        leftovers = [f for f in os.listdir(os.path.dirname(c.config.db_path)) if ".snapshot-" in f]
        self.assertEqual(leftovers, [])

    def test_reserved_room_names_get_a_folder(self):
        from server import archive
        self.assertEqual(archive._safe("NUL"), "NUL_")
        self.assertEqual(archive._safe("com1.txt"), "com1.txt_")
        self.assertEqual(archive._safe("Comp"), "Comp")

    def test_only_quillo_folders_are_locked(self):
        from server import safecopy
        chosen = os.path.join(self.tmp, "HR shared")
        os.makedirs(chosen)
        r = safecopy.lock_folder(chosen)
        self.assertFalse(r["ok"])
        self.assertTrue(r.get("chosen"))
        self.assertTrue(safecopy.lock_folder(os.path.join(self.tmp, "gone", "Chat backup")).get("missing"))

    def test_thread_replies_are_never_the_last_message(self):
        a, b = Client("ann"), Client("ben")
        root = a.request("send", conv=f"u:{self.b}", text="Plates are in")["message"]
        reply = b.request("send", conv=f"u:{self.a}", text="Which reel?", thread_root=root["id"])["message"]
        recent = {(k, t): last for k, t, last, _n in self.core.call(self.core.db.recent_conversations, self.a)}
        self.assertEqual(recent[("u", self.b)], root["id"])
        again = Client("ben")
        self.assertIn(root["id"], again.login.get("my_threads", []))
        self.assertNotEqual(reply["id"], recent[("u", self.b)])
        for cl in (a, b, again):
            cl.close()

    def test_a_resume_replaces_the_stalled_upload(self):
        """The dropped connection's handler may still be waiting; the resume must not hit 'At most 4 uploads'."""
        a = Client("ann")
        size = 2 * P.CHUNK
        stalled = []
        for i in range(4):                               # four uploads whose connections hang
            s = connect()
            s.sendall(P.encode({"op": "upload", "token": a.login["token"], "name": f"f{i}", "size": size}))
            stalled.append((s, read_line(s)["file_id"]))
            s.sendall(b"x" * 100)
        time.sleep(0.3)
        s = connect()
        s.sendall(P.encode({"op": "upload", "token": a.login["token"], "name": "f0", "size": size,
                            "resume": stalled[0][1]}))
        again = read_line(s)
        self.assertTrue(again["ok"], again)
        self.assertEqual(again["file_id"], stalled[0][1])
        s.sendall(b"x" * (size - again["offset"]))
        self.assertTrue(read_line(s).get("done"))
        s.close()
        for sock, _fid in stalled:
            sock.close()
        a.close()


if __name__ == "__main__":
    unittest.main()
