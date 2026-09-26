"""Managed departments: the owner creates departments/sections and picks which ones get a chat room."""

import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.core import ServerCore  # noqa: E402
from server.db import Database  # noqa: E402
from tests import test_server as base  # noqa: E402

PORT = 16000


class Client(base.Client):
    def __init__(self, username, password="Artist2026", port=PORT):
        base.PORT, old = port, base.PORT
        try:
            super().__init__(username, password)
        finally:
            base.PORT = old


def rooms_by_name(core):
    return {r["name"]: r for r in core.call(core.admin_rooms)}


class DepartmentsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.core = c = ServerCore(cls.tmp)
        c.config.update(tcp_port=PORT, discovery_port=PORT + 1)
        c.start()
        cls.admin = c.call(c.admin_create_user, must_change=False, username="boss", password="Artist2026",
                           is_admin=1)
        cls.artist = c.call(c.admin_create_user, must_change=False, username="plain", password="Artist2026")

    @classmethod
    def tearDownClass(cls):
        cls.core.stop()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def dept(self, dept_id):
        return next(d for d in self.core.call(self.core.admin_departments) if d["id"] == dept_id)

    def user(self, uid):
        return next(u for u in self.core.call(self.core.admin_users) if u["id"] == uid)

    def test_create_rename_delete(self):
        c = self.core
        fx = c.call(c.admin_save_department, None, "FX")
        sim = c.call(c.admin_save_department, None, "Sim", fx)
        with self.assertRaises(ValueError):
            c.call(c.admin_save_department, None, "fx")                 # names are unique, any capitals
        with self.assertRaises(ValueError):
            c.call(c.admin_save_department, None, "sim", fx)
        with self.assertRaises(ValueError):
            c.call(c.admin_save_department, None, "Deeper", sim)        # no sections inside sections
        with self.assertRaises(ValueError):
            c.call(c.admin_save_department, None, "<b>")
        uid = c.call(c.admin_create_user, must_change=False, username="fxguy", password="Artist2026",
                     department="fx", section="SIM")
        self.assertEqual((self.user(uid)["department"], self.user(uid)["section"]), ("FX", "Sim"))  # stored spelling
        self.assertEqual((self.dept(fx)["people"], self.dept(sim)["people"]), (1, 1))

        # rooms follow renames; users' text too
        c.call(c.admin_set_department_room, fx, True)
        c.call(c.admin_set_department_room, sim, True)
        c.call(c.admin_save_department, fx, "Effects")
        c.call(c.admin_save_department, sim, "Simulation")
        self.assertEqual((self.user(uid)["department"], self.user(uid)["section"]), ("Effects", "Simulation"))
        rooms = rooms_by_name(c)
        self.assertEqual(rooms["Effects"]["members"], [uid])
        self.assertEqual(rooms["Effects · Simulation"]["members"], [uid])
        self.assertNotIn("FX", rooms)

        # deleting is refused while people are in it
        with self.assertRaises(ValueError) as e:
            c.call(c.admin_delete_department, fx)
        self.assertIn("1 person", str(e.exception))
        with self.assertRaises(ValueError):
            c.call(c.admin_delete_department, sim)
        c.call(c.admin_update_user, uid, department="", section="")
        c.call(c.admin_delete_department, fx)                       # takes its (empty) section with it
        ids = {d["id"] for d in c.call(c.admin_departments)}
        self.assertFalse({fx, sim} & ids)
        rooms = rooms_by_name(c)                                     # the rooms stay, as normal rooms
        self.assertFalse(rooms["Effects"]["auto"])
        self.assertFalse(rooms["Effects · Simulation"]["auto"])
        actions = [a["action"] for a in c.call(c.admin_audit, "Effects") + c.call(c.admin_audit, "FX")]
        for action in ("department renamed", "chat room turned on", "department deleted"):
            self.assertIn(action, actions)

    def test_room_members_and_untick_keeps_room(self):
        c = self.core
        light = c.call(c.admin_save_department, None, "Lighting")
        a = c.call(c.admin_create_user, must_change=False, username="lite1", password="Artist2026",
                   department="Lighting")
        b = c.call(c.admin_create_user, must_change=False, username="lite2", password="Artist2026",
                   department="Lighting")
        gone = c.call(c.admin_create_user, must_change=False, username="lite3", password="Artist2026",
                      department="Lighting")
        c.call(c.admin_update_user, gone, disabled=1)
        self.assertNotIn("Lighting", rooms_by_name(c))                # nothing is created by itself
        c.call(c.admin_set_department_room, light, True)
        room = rooms_by_name(c)["Lighting"]
        self.assertTrue(room["auto"])
        self.assertEqual(set(room["members"]), {a, b})                # active people only

        lite = Client("lite1")
        r = lite.request("send", conv=f"r:{room['id']}", text="render done")
        self.assertTrue(r["ok"], r)
        c.call(c.admin_set_department_room, light, False)
        pushed = lite.wait_for("room")["room"]
        self.assertEqual((pushed["id"], pushed["auto"]), (room["id"], False))
        lite.close()
        kept = rooms_by_name(c)["Lighting"]
        self.assertEqual((kept["id"], kept["auto"], set(kept["members"])), (room["id"], False, {a, b}))
        self.assertEqual(c.call(c.admin_review_history, f"r:{room['id']}")[-1]["body"], "render done")

        # ticking again continues the same room
        c.call(c.admin_set_department_room, light, True)
        again = rooms_by_name(c)["Lighting"]
        self.assertEqual((again["id"], again["auto"]), (room["id"], True))

    def test_unknown_department_refused(self):
        c = self.core
        comp = c.call(c.admin_save_department, None, "Comp")
        c.call(c.admin_save_department, None, "Roto", comp)
        with self.assertRaises(ValueError) as e:
            c.call(c.admin_create_user, must_change=False, username="x1", password="Artist2026", department="Nope")
        self.assertIn("Departments page", str(e.exception))
        with self.assertRaises(ValueError):
            c.call(c.admin_create_user, must_change=False, username="x2", password="Artist2026",
                   department="Comp", section="Paint")
        with self.assertRaises(ValueError):
            c.call(c.admin_create_user, must_change=False, username="x3", password="Artist2026", section="Roto")
        # the CSV import calls admin_create_user with these fields
        uid = c.call(c.admin_create_user, username="csv1", password="Artist2026", display_name="Csv",
                     department="comp", section="roto", designation="", title="")
        self.assertEqual((self.user(uid)["department"], self.user(uid)["section"]), ("Comp", "Roto"))
        with self.assertRaises(ValueError):
            c.call(c.admin_update_user, uid, department="Nowhere")
        with self.assertRaises(ValueError):
            c.call(c.admin_update_user, uid, section="Nowhere")
        c.call(c.admin_update_user, uid, department="")              # no department is fine
        self.assertEqual((self.user(uid)["department"], self.user(uid)["section"]), ("", ""))

    def test_remote_console_and_rights(self):
        from server.console_api import RemoteApi
        api = RemoteApi("127.0.0.1", PORT)
        old_appdata = os.environ.get("APPDATA")
        os.environ["APPDATA"] = self.tmp                  # console pins go to a temp folder
        try:
            self.assertEqual(api.connect("boss", "Artist2026"), "")
            dept_id = api.call("admin_save_department", None, "Remote Dept")
            api.call("admin_set_department_room", dept_id, True)
            self.assertTrue(next(d for d in api.call("admin_departments") if d["id"] == dept_id)["has_room"])
            api.call("admin_save_department", dept_id, "Remote Renamed")
            with self.assertRaises(ValueError):
                api.call("admin_create_user", username="r1", password="Artist2026", department="Missing")
            api.call("admin_delete_department", dept_id)
        finally:
            api.close()
            if old_appdata is None:
                os.environ.pop("APPDATA", None)
            else:
                os.environ["APPDATA"] = old_appdata
        plain = Client("plain")
        for fn, args in (("admin_departments", []), ("admin_save_department", [None, "Hack"]),
                         ("admin_set_department_room", [1, True]), ("admin_delete_department", [1])):
            r = plain.request("admin_call", fn=fn, args=args)
            self.assertFalse(r["ok"], fn)
            self.assertIn("Administrator", r["error"])
        plain.close()
        self.assertFalse(any(d["name"] == "Hack" for d in self.core.call(self.core.admin_departments)))


class MigrationTest(unittest.TestCase):
    """An older database: free-text departments and automatic rooms made from them."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        db = Database(os.path.join(self.tmp, "messenger.db"))
        self.u1 = db.create_user("ann", "Artist2026", "Ann", department="Comp", section="Roto")
        self.u2 = db.create_user("bob", "Artist2026", "Bob", department="comp ", section="roto")
        self.u3 = db.create_user("cat", "Artist2026", "Cat", department="Lighting")
        self.u4 = db.create_user("dan", "Artist2026", "Dan")
        self.dept_room = db.create_room("Comp", None, [self.u1, self.u2], auto_key="dept:comp")
        self.sect_room = db.create_room("Comp · Roto", None, [self.u1, self.u2], auto_key="sect:comp\x1froto")
        db.add_message(f"r:{self.dept_room}", self.u1, "old history", room_id=self.dept_room)
        db.con.execute("DROP TABLE departments")          # as before this version
        db.con.commit()
        db.close()
        with open(os.path.join(self.tmp, "config.json"), "w", encoding="utf-8") as f:
            f.write('{"auto_department_rooms": true, "auto_section_rooms": true}')     # old settings
        self.core = ServerCore(self.tmp)
        with open(self.core.config.path, encoding="utf-8") as f:
            self.assertNotIn("auto_department_rooms", f.read())                     # dropped
        self.core.config.update(tcp_port=PORT + 10, discovery_port=PORT + 11)
        self.core.start()

    def tearDown(self):
        self.core.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_seeded_and_rooms_kept(self):
        c = self.core
        depts = c.call(c.admin_departments)
        tops = {d["name"]: d for d in depts if d["parent_id"] is None}
        self.assertEqual(set(tops), {"Comp", "Lighting"})
        sects = [d for d in depts if d["parent_id"] is not None]
        self.assertEqual([(s["name"], s["parent_id"]) for s in sects], [("Roto", tops["Comp"]["id"])])
        self.assertFalse(any(d["has_room"] for d in depts))
        self.assertEqual((tops["Comp"]["people"], tops["Lighting"]["people"], sects[0]["people"]), (2, 1, 2))
        users = {u["id"]: u for u in c.call(c.admin_users)}
        self.assertEqual((users[self.u2]["department"], users[self.u2]["section"]), ("Comp", "Roto"))
        rooms = {r["id"]: r for r in c.call(c.admin_rooms)}
        for rid in (self.dept_room, self.sect_room):                 # still there, now normal rooms
            self.assertFalse(rooms[rid]["auto"])
            self.assertEqual(set(rooms[rid]["members"]), {self.u1, self.u2})
        self.assertEqual(c.call(c.admin_review_history, f"r:{self.dept_room}")[-1]["body"], "old history")
        # ticking Chat room picks up the old room again, with its history
        c.call(c.admin_set_department_room, tops["Comp"]["id"], True)
        c.call(c.admin_set_department_room, sects[0]["id"], True)
        rooms = {r["id"]: r for r in c.call(c.admin_rooms)}
        self.assertTrue(rooms[self.dept_room]["auto"])
        self.assertTrue(rooms[self.sect_room]["auto"])
        self.assertEqual(len(rooms), 2)
        # a second start does not seed again
        c.stop()
        c.start()
        self.assertEqual(len(c.call(c.admin_departments)), 3)


if __name__ == "__main__":
    unittest.main()
