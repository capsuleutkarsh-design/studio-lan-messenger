"""Organisation features: sections, designations/permissions, reporting lines,
visibility and automatic rooms."""

import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.core import ServerCore  # noqa: E402
from tests import test_server as base  # noqa: E402

PORT = 15250


class Client(base.Client):
    def __init__(self, username, password="Artist2026"):
        base.PORT, old = PORT, base.PORT
        try:
            super().__init__(username, password)
        finally:
            base.PORT = old


class OrgTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.core = c = ServerCore(cls.tmp)
        c.config.update(tcp_port=PORT, discovery_port=PORT + 1)
        c.start()
        # departments are managed on the Departments page; Compositing and its sections get a chat room
        comp = c.call(c.admin_save_department, None, "Compositing")
        c.call(c.admin_set_department_room, comp, True)
        for sect in ("Roto", "Paint"):
            c.call(c.admin_set_department_room, c.call(c.admin_save_department, None, sect, comp), True)
        for dept in ("Lighting", "Admin"):
            c.call(c.admin_save_department, None, dept)

        def mk(username, dept, section, designation, reports_to=""):
            return c.call(c.admin_create_user, must_change=False, username=username, password="Artist2026",
                          display_name=username.title(), department=dept, section=section,
                          designation=designation, reports_to=reports_to)
        cls.sup = mk("sup", "Compositing", "", "Supervisor")
        cls.lead = mk("lead", "Compositing", "Roto", "Lead", "sup")
        cls.artist = mk("artist", "Compositing", "Roto", "Artist", "lead")
        cls.paint = mk("paint", "Compositing", "Paint", "Artist", "sup")
        cls.light = mk("light", "Lighting", "", "Artist")
        cls.hr = mk("hr", "Admin", "", "HR")
        cls.trainee = mk("trainee", "Lighting", "", "Trainee")

    @classmethod
    def tearDownClass(cls):
        cls.core.stop()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def rooms_of(self, client):
        return {r["name"]: r for r in client.login["rooms"]}

    def test_auto_rooms(self):
        a = Client("artist")
        rooms = self.rooms_of(a)
        self.assertIn("Compositing", rooms)
        self.assertIn("Compositing · Roto", rooms)
        self.assertTrue(rooms["Compositing"]["auto"])
        self.assertEqual(set(rooms["Compositing · Roto"]["members"]), {self.lead, self.artist})
        # cannot leave an automatic room
        self.assertFalse(a.request("room_leave", room_id=rooms["Compositing"]["id"])["ok"])
        # moving a user moves their auto rooms
        self.core.call(self.core.admin_update_user, self.paint, section="Roto")
        a.wait_for("room")
        c = self.core
        roto = next(r for r in c.call(c.admin_rooms) if r["name"] == "Compositing · Roto")
        self.assertIn(self.paint, roto["members"])
        paint = next(r for r in c.call(c.admin_rooms) if r["name"] == "Compositing · Paint")
        self.assertNotIn(self.paint, paint["members"])          # a ticked room stays, even when empty
        self.assertFalse(any(r["name"] == "Lighting" for r in c.call(c.admin_rooms)))   # not ticked: no room
        self.core.call(self.core.admin_update_user, self.paint, section="Paint")
        a.close()

    def test_directory_fields(self):
        a = Client("artist")
        me = a.login["me"]
        self.assertEqual((me["designation"], me["section"], me["manager_id"]), ("Artist", "Roto", self.lead))
        self.assertFalse(me["perms"]["manage_users"])
        users = {u["id"]: u for u in a.login["users"]}
        self.assertEqual(users[self.lead]["designation"], "Lead")
        a.close()

    def test_announcement_permissions(self):
        artist, lead, sup, light = Client("artist"), Client("lead"), Client("sup"), Client("light")
        self.assertFalse(artist.request("announce", body="hi", target="all")["ok"])
        # lead: own section yes, whole department no
        self.assertFalse(lead.request("announce", body="x", target="department", department="Compositing")["ok"])
        r = lead.request("announce", title="Roto", body="Roto meeting", target="section",
                         department="Compositing", section="Roto")
        self.assertTrue(r["ok"], r)
        self.assertEqual(artist.wait_for("announcement")["announcement"]["title"], "Roto")
        # team announcement reaches indirect reports only
        r = sup.request("announce", title="Team", body="team", target="team")
        self.assertTrue(r["ok"], r)
        self.assertEqual(artist.wait_for("announcement")["announcement"]["title"], "Team")
        # supervisor: own department yes, other department no
        self.assertFalse(sup.request("announce", body="x", target="department", department="Lighting")["ok"])
        self.assertTrue(sup.request("announce", title="Comp", body="x", target="department",
                                    department="Compositing")["ok"])
        # lighting never received the compositing ones
        light.request("ping")
        self.assertFalse(any(m["op"] == "announcement" for m in light.pending))
        for c in (artist, lead, sup, light):
            c.close()

    def test_room_creation_permission(self):
        t = Client("trainee")
        self.assertFalse(t.request("create_room", name="x", members=[])["ok"])
        t.close()

    def test_restricted_visibility(self):
        c = self.core
        role = next(r for r in c.call(c.admin_roles) if r["name"] == "Trainee")
        c.call(c.admin_save_role, role["id"], see_all=0)
        try:
            t = Client("trainee")
            ids = {u["id"] for u in t.login["users"]}
            self.assertIn(self.light, ids)        # same department
            self.assertIn(self.hr, ids)           # HR is always visible
            self.assertNotIn(self.artist, ids)    # other department
            self.assertFalse(t.request("send", conv=f"u:{self.artist}", text="hi")["ok"])
            t.close()
        finally:
            c.call(c.admin_save_role, role["id"], see_all=1)

    def test_reporting_loop_rejected(self):
        c = self.core
        with self.assertRaises(ValueError):
            c.call(c.admin_update_user, self.sup, manager_id=self.artist)

    def test_hr_manage_user(self):
        hr, artist = Client("hr"), Client("artist")
        self.assertFalse(artist.request("manage_user", user_id=self.trainee, action="reset_password",
                                        password="Changed2026")["ok"])
        r = hr.request("manage_user", user_id=self.trainee, action="reset_password", password="Changed2026")
        self.assertTrue(r["ok"], r)
        self.assertEqual(Client("trainee", "Changed2026").login["op"], "login_ok")
        self.core.call(self.core.admin_update_user, self.trainee, password="Artist2026", must_change=False)
        hr.close()
        artist.close()


if __name__ == "__main__":
    unittest.main()
