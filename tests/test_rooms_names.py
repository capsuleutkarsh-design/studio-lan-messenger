"""1.6.2: room owner hand-over, own name change, VFX designations, client versions, send-later to the second."""

import os
import shutil
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from common import protocol as P  # noqa: E402
from server.core import ServerCore  # noqa: E402
from server.db import VFX_ROLES, Database  # noqa: E402
from tests import test_server as base  # noqa: E402

PORT = 16250


class Client(base.Client):
    def __init__(self, username, password="Artist2026", version=None):
        self.sock = base.tls_connect(PORT)
        self.file = self.sock.makefile("rb")
        self.rid, self.pending = 0, []
        login = {"op": "login", "username": username, "password": password}
        if version:
            login["version"] = version
        self.sock.sendall(P.encode(login))
        self.login = self.read()


class RoomsNamesTest(unittest.TestCase):
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

    def room(self, rid):
        return {r["id"]: r for r in self.core.call(self.core.admin_rooms)}.get(rid)

    def test_owner_leaves_and_hands_over(self):
        a, b, c = Client("ann"), Client("ben"), Client("cat")
        rid = a.request("create_room", name="Comp dailies", members=[self.b])["room_id"]
        time.sleep(0.05)
        self.assertTrue(a.request("room_update", room_id=rid, add=[self.c])["ok"])
        self.assertEqual(self.room(rid)["owner_id"], self.a)
        # the owner hands the room over; only members can be made owner
        self.assertFalse(b.request("room_update", room_id=rid, owner=self.b)["ok"])          # not the owner
        self.assertTrue(a.request("room_update", room_id=rid, owner=self.b)["ok"])
        self.assertEqual(self.room(rid)["owner_id"], self.b)
        self.assertFalse(b.request("room_update", room_id=rid, owner=999999)["ok"])
        # the owner leaves: the longest member becomes owner (ann joined first)
        self.assertTrue(b.request("room_leave", room_id=rid)["ok"])
        self.assertEqual(self.room(rid)["owner_id"], self.a)
        self.assertTrue(a.request("room_update", room_id=rid, name="Comp dailies v2")["ok"])   # can manage again
        # the last people leave: the room is closed
        self.assertTrue(a.request("room_leave", room_id=rid)["ok"])
        self.assertTrue(c.request("room_leave", room_id=rid)["ok"])
        self.assertIsNone(self.room(rid))
        for x in (a, b, c):
            x.sock.close()

    def test_change_own_name(self):
        a = Client("ann")
        self.assertTrue(a.login["allow_name_change"])
        r = a.request("set_name", name="  Ann   Kapoor ")
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["name"], "Ann Kapoor")
        self.assertEqual(self.core.call(self.core.db.get_user, self.a)["display_name"], "Ann Kapoor")
        for bad in ("", "x", "<b>Boss</b>", "a" * 81):
            self.assertFalse(a.request("set_name", name=bad)["ok"], bad)
        self.core.config.update(allow_name_change=False)
        try:
            self.assertFalse(a.request("set_name", name="Someone Else")["ok"])
        finally:
            self.core.config.update(allow_name_change=True)
        a.sock.close()

    def test_versions_and_update_check(self):
        a = Client("cat", version="1.6.2")
        sessions = [s for s in self.core.call(self.core.admin_sessions) if s["username"] == "cat"]
        self.assertEqual(sessions[0]["version"], "1.6.2")
        self.assertIn("1.6.2", self.core.call(self.core.admin_updates)["versions"])
        r = a.request("update_check")
        self.assertTrue(r["ok"])
        self.assertIsNone(r["update"])                  # nothing in the updates folder
        folder = self.core.call(self.core.admin_updates)["folder"]
        with open(os.path.join(folder, "Quillo-Client-Setup-9.0.0.exe"), "wb") as f:
            f.write(b"MZ")
        self.assertEqual(self.core.call(self.core.admin_check_updates)["version"], "9.0.0")
        self.assertEqual(a.request("update_check")["update"]["version"], "9.0.0")
        os.remove(os.path.join(folder, "Quillo-Client-Setup-9.0.0.exe"))
        a.sock.close()

    def test_send_later_to_the_second(self):
        a = Client("ann")
        due = time.time() + 2
        r = a.request("schedule_add", conv=f"u:{self.b}", text="in two seconds", due_at=due)
        self.assertTrue(r["ok"], r)
        end = time.time() + 6
        while time.time() < end:
            m = a.read()
            if m.get("op") == "message" and m["message"]["body"] == "in two seconds":
                self.assertLess(abs(m["message"]["ts"] - due), 1.6)       # the planner looks every second
                break
        else:
            self.fail("scheduled message not sent")
        a.sock.close()

    def test_vfx_designations(self):
        names = {r["name"] for r in self.core.call(self.core.admin_roles)}
        for n in ("VFX Supervisor", "CG Supervisor", "Compositing Lead", "Compositor", "Roto Artist",
                  "Pipeline TD", "VFX Producer", "Production Coordinator"):
            self.assertIn(n, names)
        self.assertIn("Artist", names)                  # the originals stay
        # added once: a designation the admin deletes does not come back on the next start
        path = os.path.join(tempfile.mkdtemp(), "x.db")
        db = Database(path)
        db._exec("DELETE FROM roles WHERE name='Matte Painter'")
        db.con.close()
        db = Database(path)
        self.assertNotIn("Matte Painter", {r["name"] for r in db.list_roles()})
        self.assertEqual(len({n for n, *_ in VFX_ROLES}), len(VFX_ROLES))
        db.con.close()


if __name__ == "__main__":
    unittest.main()
