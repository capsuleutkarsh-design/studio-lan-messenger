"""1.6.1: per-room file retention, the storage report and clean-up, and the audit fixes on the server."""

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

PORT = 16150


class Client(base.Client):
    def __init__(self, username, password="Artist2026"):
        base.PORT, old = PORT, base.PORT
        try:
            super().__init__(username, password)
        finally:
            base.PORT = old


class Console(base.Client):
    """A server-console connection (admin requests only)."""
    def __init__(self, username, password="Artist2026"):       # noqa: super().__init__ logs in as a chat user
        self.sock = base.tls_connect(PORT)
        self.file = self.sock.makefile("rb")
        self.rid, self.pending = 0, []
        self.sock.sendall(P.encode({"op": "login", "username": username, "password": password, "console": True}))
        self.login = self.read()

    def lines(self):
        """Everything the server sends until it closes the connection."""
        out = []
        for line in self.file:
            out.append(json.loads(line))
        return out


class StorageTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.core = c = ServerCore(cls.tmp)
        c.config.update(tcp_port=PORT, discovery_port=PORT + 1, file_retention_days=3)
        c.start()
        mk = lambda u, **kw: c.call(c.admin_create_user, must_change=False, username=u,  # noqa: E731
                                    password="Artist2026", display_name=u.title(), **kw)
        cls.a, cls.b = mk("ann"), mk("ben")
        cls.boss = mk("boss", is_admin=1)
        cls.keep = c.call(c.admin_save_room, None, "Plates", "", [cls.a, cls.b])
        cls.month = c.call(c.admin_save_room, None, "Dailies", "", [cls.a, cls.b])
        cls.normal = c.call(c.admin_save_room, None, "Chat", "", [cls.a, cls.b])

    @classmethod
    def tearDownClass(cls):
        cls.core.stop()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def up(self, client, conv, name, data=b"data"):
        s = base.tls_connect(PORT)
        f = s.makefile("rb")
        s.sendall(P.encode({"op": "upload", "token": client.login["token"], "name": name, "size": len(data)}))
        hdr = json.loads(f.readline())
        self.assertTrue(hdr["ok"], hdr)
        s.sendall(data)
        json.loads(f.readline())
        s.close()
        self.assertTrue(client.request("send", conv=conv, text="", file_id=hdr["file_id"])["ok"])
        return hdr["file_id"]

    def age(self, file_id, days):
        self.core.call(self.core.db._exec, "UPDATE files SET created_at=? WHERE id=?",
                       time.time() - days * 86400, file_id)

    def purged(self, file_id):
        return bool(self.core.call(self.core.db.get_file, file_id)["purged"])

    def test_room_retention(self):
        c, a = self.core, Client("ann")
        c.call(c.admin_set_room_retention, self.keep, 0)          # keep forever
        c.call(c.admin_set_room_retention, self.month, 30)        # a month
        rooms = {r["id"]: r for r in c.call(c.admin_rooms)}
        self.assertEqual((rooms[self.keep]["file_retention_days"], rooms[self.month]["file_retention_days"],
                          rooms[self.normal]["file_retention_days"]), (0, 30, None))
        plate = self.up(a, f"r:{self.keep}", "plate.exr")
        daily = self.up(a, f"r:{self.month}", "daily.mov")
        old_daily = self.up(a, f"r:{self.month}", "old_daily.mov")
        chat = self.up(a, f"r:{self.normal}", "chat.png")
        direct = self.up(a, f"u:{self.b}", "direct.txt")
        for fid in (plate, daily, chat, direct):
            self.age(fid, 10)
        self.age(old_daily, 40)
        c.call(c.purge_files)
        self.assertFalse(self.purged(plate))          # the room keeps files forever
        self.assertFalse(self.purged(daily))          # 10 days < the room's 30
        self.assertTrue(self.purged(old_daily))       # 40 days > 30
        self.assertTrue(self.purged(chat))            # server default: 3 days
        self.assertTrue(self.purged(direct))
        # a file forwarded into a keep-forever room is kept, even though the other chat would drop it
        shared = self.up(a, f"r:{self.normal}", "shared.nk")
        self.assertTrue(a.request("send", conv=f"r:{self.keep}", text="", file_id=shared)["ok"])
        self.age(shared, 10)
        c.call(c.purge_files)
        self.assertFalse(self.purged(shared))
        # back to the server default; bad values are refused
        c.call(c.admin_set_room_retention, self.keep, None)
        self.assertIsNone({r["id"]: r for r in c.call(c.admin_rooms)}[self.keep]["file_retention_days"])
        with self.assertRaises(ValueError):
            c.call(c.admin_set_room_retention, self.keep, -1)
        with self.assertRaises(ValueError):
            c.call(c.admin_set_room_retention, 99999, 5)
        a.close()

    def test_storage_report_and_cleanup(self):
        c, a = self.core, Client("ann")
        c.call(c.admin_set_room_retention, self.keep, 0)
        big = self.up(a, f"r:{self.keep}", "big.exr", b"x" * 5000)
        small = self.up(a, f"r:{self.keep}", "small.txt", b"y" * 10)
        report = c.call(c.admin_storage)
        self.assertGreaterEqual(report["total_bytes"], 5010)
        self.assertEqual(report["largest"][0]["name"], "big.exr")
        self.assertEqual(report["largest"][0]["room"], "Plates")
        ann = next(u for u in report["by_user"] if u["username"] == "ann")
        self.assertGreaterEqual(ann["files"], 2)
        plates = next(r for r in report["by_room"] if r["name"] == "Plates")
        self.assertEqual(plates["retention"], 0)
        self.assertTrue(report["disk"]["free"] > 0)
        self.assertEqual(report["default_days"], 3)
        # manual clean-up: only files older than the chosen age, even in a keep-forever room
        self.age(big, 60)
        r = c.call(c.admin_cleanup_files, 30)
        self.assertGreaterEqual(r["removed"], 1)
        self.assertTrue(self.purged(big))
        self.assertFalse(self.purged(small))
        with self.assertRaises(ValueError):
            c.call(c.admin_cleanup_files, 0)
        a.close()

    def test_mute_needs_a_real_user(self):
        a = Client("ann")
        self.assertFalse(a.request("mute", conv="u:987654321", muted=True)["ok"])
        self.assertTrue(a.request("mute", conv=f"u:{self.b}", muted=True)["ok"])
        a.request("mute", conv=f"u:{self.b}", muted=False)
        a.close()

    def test_failed_sign_ins_are_forgotten(self):
        c = self.core
        for i in range(30):
            c.call(c._login_failed, "10.9.9.9", f"nobody{i}")
        self.assertGreater(len(c.failed_logins), 30)
        c.call(c.db._exec, "SELECT 1")
        old = time.time() - c.LOGIN_WINDOW - 5
        for times in c.failed_logins.values():
            times[:] = [old]
        c.call(c._prune_login_failures)
        self.assertFalse(any(k[0] == "10.9.9.9" for k in c.failed_logins))

    def test_disabled_admin_loses_the_console(self):
        c = self.core
        admin2 = c.call(c.admin_create_user, must_change=False, username="admin2", password="Artist2026",
                        display_name="Second Admin", is_admin=1)
        con = Console("admin2")
        self.assertTrue(con.login.get("console"), con.login)
        self.assertTrue(con.request("admin_call", fn="admin_stats")["ok"])
        c.call(c.admin_update_user, admin2, disabled=1)
        con.sock.sendall(P.encode({"op": "admin_call", "rid": 99, "fn": "admin_users"}))
        got = con.lines()                              # the server answers "kicked" and hangs up
        self.assertIn("kicked", [m.get("op") for m in got])
        self.assertFalse(any(m.get("op") == "reply" and m.get("ok") for m in got))
        con.sock.close()

    def test_upload_waits_for_password_change(self):
        c = self.core
        c.call(c.admin_create_user, must_change=True, username="newbie", password="Artist2026",
               display_name="Newbie")
        n = Client("newbie")
        s = base.tls_connect(PORT)
        f = s.makefile("rb")
        s.sendall(P.encode({"op": "upload", "token": n.login["token"], "name": "x.bin", "size": 4}))
        self.assertFalse(json.loads(f.readline())["ok"])
        s.close()
        n.close()


if __name__ == "__main__":
    unittest.main()
