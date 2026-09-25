"""Integration tests: start a real server and talk to it over TCP/UDP.

Run with:  .venv\\Scripts\\python -m unittest discover tests
"""

import json
import os
import shutil
import socket
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from common import protocol as P  # noqa: E402
from server.core import ServerCore  # noqa: E402

PORT = 15150
UDP_PORT = 15151


def tls_connect(port=None):
    """Encrypted connection to the test server (all tests run with TLS on)."""
    from server.tls import client_context
    raw = socket.create_connection(("127.0.0.1", port or PORT), timeout=5)
    return client_context().wrap_socket(raw)


class Client:
    def __init__(self, username, password="Artist2026"):
        self.sock = tls_connect()
        self.file = self.sock.makefile("rb")
        self.rid = 0
        self.pending = []
        self.sock.sendall(P.encode({"op": "login", "username": username, "password": password}))
        self.login = self.read()

    def read(self):
        return json.loads(self.file.readline())

    def request(self, op, **kw):
        self.rid += 1
        self.sock.sendall(P.encode({"op": op, "rid": self.rid, **kw}))
        while True:
            msg = self.read()
            if msg.get("op") == "reply" and msg.get("rid") == self.rid:
                return msg
            self.pending.append(msg)

    def wait_for(self, op, timeout=5):
        for i, m in enumerate(self.pending):
            if m["op"] == op:
                return self.pending.pop(i)
        end = time.time() + timeout
        while time.time() < end:
            msg = self.read()
            if msg["op"] == op:
                return msg
            self.pending.append(msg)
        raise AssertionError(f"no {op}")

    def close(self):
        self.file.close()
        self.sock.close()


class ServerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.core = ServerCore(cls.tmp)
        cls.core.config.update(tcp_port=PORT, discovery_port=UDP_PORT, server_name="Test")
        cls.core.start()
        cls.alice = cls.core.call(cls.core.admin_create_user, must_change=False, username="alice", password="Artist2026",
                                  display_name="Alice", department="Comp")
        cls.bob = cls.core.call(cls.core.admin_create_user, must_change=False, username="bob", password="Artist2026",
                                display_name="Bob", department="Lighting")

    @classmethod
    def tearDownClass(cls):
        cls.core.stop()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_bad_login(self):
        c = Client("alice", "wrong")
        self.assertEqual(c.login["op"], "login_error")
        c.close()

    def test_default_admin(self):
        c = Client("admin", "admin")
        self.assertEqual(c.login["op"], "login_ok")
        self.assertTrue(c.login["me"]["is_admin"])
        c.close()

    def test_direct_message_offline_and_receipts(self):
        a = Client("alice")
        self.assertEqual(a.login["op"], "login_ok")
        r = a.request("send", conv=f"u:{self.bob}", text="hello bob")
        self.assertTrue(r["ok"], r)
        self.assertFalse(r["message"]["delivered"])      # bob offline
        mid = r["message"]["id"]

        b = Client("bob")
        recent = {x["conv"]: x for x in b.login["recent"]}
        self.assertEqual(recent[f"u:{self.alice}"]["unread"], 1)
        rec = a.wait_for("receipt")
        self.assertEqual(rec["type"], "delivered")

        hist = b.request("history", conv=f"u:{self.alice}")
        self.assertEqual(hist["messages"][-1]["body"], "hello bob")
        b.request("mark_read", conv=f"u:{self.alice}", up_to=mid)
        rec = a.wait_for("receipt")
        self.assertEqual((rec["type"], rec["up_to"]), ("read", mid))

        # live message + typing
        a.request("typing", conv=f"u:{self.bob}")
        self.assertEqual(b.wait_for("typing")["conv"], f"u:{self.alice}")
        a.request("send", conv=f"u:{self.bob}", text="live")
        self.assertEqual(b.wait_for("message")["message"]["body"], "live")
        a.close()
        b.close()

    def test_rooms_and_files(self):
        a = Client("alice")
        b = Client("bob")
        r = a.request("create_room", name="Comp Team", members=[self.bob])
        self.assertTrue(r["ok"], r)
        room = r["room_id"]
        self.assertEqual(b.wait_for("room")["room"]["name"], "Comp Team")

        # upload a file
        data = os.urandom(700_000)
        s = tls_connect()
        f = s.makefile("rb")
        s.sendall(P.encode({"op": "upload", "token": a.login["token"], "name": "shot.exr",
                            "size": len(data)}))
        hdr = json.loads(f.readline())
        self.assertTrue(hdr["ok"], hdr)
        s.sendall(data)
        done = json.loads(f.readline())
        self.assertTrue(done.get("done"))
        s.close()

        r = a.request("send", conv=f"r:{room}", text="", file_id=hdr["file_id"])
        self.assertTrue(r["ok"], r)
        msg = b.wait_for("message")["message"]
        while msg["kind"] == "system":
            msg = b.wait_for("message")["message"]
        self.assertEqual(msg["file"]["name"], "shot.exr")

        # bob downloads it with an offset
        s = tls_connect()
        f = s.makefile("rb")
        s.sendall(P.encode({"op": "download", "token": b.login["token"], "file_id": hdr["file_id"],
                            "offset": 100}))
        hdr2 = json.loads(f.readline())
        self.assertTrue(hdr2["ok"], hdr2)
        got = f.read(len(data) - 100)
        self.assertEqual(got, data[100:])
        s.close()

        # a stranger cannot download it
        self.core.call(self.core.admin_create_user, must_change=False, username="eve", password="Artist2026")
        e = Client("eve")
        s = tls_connect()
        s.sendall(P.encode({"op": "download", "token": e.login["token"], "file_id": hdr["file_id"]}))
        self.assertFalse(json.loads(s.makefile("rb").readline())["ok"])
        s.close()

        # leave the room
        b.request("room_leave", room_id=room)
        self.assertEqual(b.wait_for("room_removed")["room_id"], room)
        self.assertFalse(b.request("history", conv=f"r:{room}")["ok"])
        for c in (a, b, e):
            c.close()

    def test_presence_and_announcement(self):
        a = Client("alice")
        b = Client("bob")
        b.request("set_status", status="busy", status_msg="Rendering")
        p = a.wait_for("presence")
        while p["user_id"] != self.bob or p["status"] == "online":   # skip bob's login presence
            p = a.wait_for("presence")
        self.assertEqual((p["status"], p["status_msg"]), ("busy", "Rendering"))

        self.assertFalse(a.request("announce", title="x", body="y")["ok"])   # not allowed
        self.core.call(self.core.announce, 0, "Lunch", "Pizza is here", "")
        self.assertEqual(a.wait_for("announcement")["announcement"]["title"], "Lunch")
        b.close()
        p = a.wait_for("presence")
        while p["user_id"] != self.bob:
            p = a.wait_for("presence")
        self.assertEqual(p["status"], "offline")
        a.close()

    def test_disable_kicks(self):
        self.core.call(self.core.admin_create_user, must_change=False, username="carl", password="Artist2026")
        c = Client("carl")
        uid = c.login["me"]["id"]
        self.core.call(self.core.admin_update_user, uid, disabled=1)
        self.assertEqual(c.wait_for("kicked")["op"], "kicked")
        self.assertEqual(Client("carl").login["op"], "login_error")

    def test_discovery(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(3)
        s.sendto(P.DISCOVERY_MAGIC, ("127.0.0.1", UDP_PORT))
        data, _ = s.recvfrom(4096)
        info = json.loads(data)
        self.assertEqual((info["name"], info["port"]), ("Test", PORT))
        s.close()


if __name__ == "__main__":
    unittest.main()
