"""The studio calendar on the server: visibility, permissions, repeats, RSVP, leave, birthdays, holidays, .ics."""

import datetime as dt
import os
import shutil
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.core import ServerCore  # noqa: E402
from tests import test_server as base  # noqa: E402

PORT = 16450


class Client(base.Client):
    def __init__(self, username, password="Artist2026"):
        base.PORT, old = PORT, base.PORT
        try:
            super().__init__(username, password)
        finally:
            base.PORT = old


def ts(y, m, d, h=0, mi=0):
    return dt.datetime(y, m, d, h, mi).timestamp()


class CalendarServerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.core = c = ServerCore(cls.tmp)
        c.config.update(tcp_port=PORT, discovery_port=PORT + 1)
        c.start()
        c.call(c.admin_save_department, name="Compositing")
        c.call(c.admin_save_department, name="Lighting")
        c.call(c.admin_save_department, name="HR")
        mk = lambda u, **kw: c.call(c.admin_create_user, must_change=False, username=u,  # noqa: E731
                                    password="Artist2026", display_name=u.title(), **kw)
        cls.lead = mk("lead", department="Compositing", designation="Compositing Lead")
        cls.art = mk("art", department="Compositing", designation="Compositor", birthday="15-10",
                     joined_on="01-10-2020")
        cls.lit = mk("lit", department="Lighting", designation="Lighting Artist")
        cls.hr = mk("hr", department="HR", designation="HR")
        cls.room = c.call(c.admin_save_room, None, "FAL Delivery", "", [cls.lead, cls.art])

    @classmethod
    def tearDownClass(cls):
        cls.core.stop()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def range(self, client, first="2026-10-01", last="2026-10-31"):
        r = client.request("cal_range", start=first, end=last)
        self.assertTrue(r["ok"], r)
        return r

    def save(self, client, **ev):
        return client.request("cal_save", event=ev)

    def test_holidays_seeded(self):
        c = self.core
        names = {h["name"]: h for h in c.call(c.holiday_list, 2026)}
        self.assertTrue(names["Gandhi Jayanti"]["observed"])             # national: ticked
        self.assertFalse(names["Diwali (Lakshmi Puja)"]["observed"])     # festival: for the admin to tick
        self.assertTrue(names["Diwali (Lakshmi Puja)"]["confirm"])
        self.assertTrue(any(h["day"].startswith("2035") for h in c.call(c.holiday_list, 2035)))
        a = Client("art")
        got = [h["name"] for h in self.range(a)["holidays"]]
        self.assertIn("Gandhi Jayanti", got)
        self.assertNotIn("Diwali (Lakshmi Puja)", [h["name"] for h in self.range(a, "2026-11-01", "2026-11-30")["holidays"]])
        a.close()

    def test_visibility_and_permissions(self):
        lead, art, lit = Client("lead"), Client("art"), Client("lit")
        # personal events and private notes: only me
        self.assertTrue(self.save(art, kind="event", title="Dentist", start=ts(2026, 10, 5, 9), end=ts(2026, 10, 5, 10))["ok"])
        self.assertTrue(self.save(art, kind="note", scope="private", title="Buy milk", all_day=True,
                                  start=ts(2026, 10, 5), end=ts(2026, 10, 6))["ok"])
        # an artist can't add deadlines, team notes or studio notes
        for ev in (dict(kind="deadline", scope="room", scope_ref=self.room, title="Delivery"),
                   dict(kind="note", scope="dept", scope_ref="Compositing", title="Team"),
                   dict(kind="note", scope="studio", title="Studio")):
            self.assertFalse(self.save(art, start=ts(2026, 10, 9, 18), end=ts(2026, 10, 9, 19), **ev)["ok"], ev)
        # a lead can add a room deadline and a team note for their own department - not for another one
        self.assertTrue(self.save(lead, kind="deadline", scope="room", scope_ref=self.room, title="FAL delivery",
                                  start=ts(2026, 10, 9, 18), end=ts(2026, 10, 9, 18, 30))["ok"])
        self.assertTrue(self.save(lead, kind="note", scope="dept", scope_ref="Compositing", title="Publish by 5",
                                  all_day=True, start=ts(2026, 10, 7), end=ts(2026, 10, 8))["ok"])
        self.assertFalse(self.save(lead, kind="note", scope="dept", scope_ref="Lighting", title="x", all_day=True,
                                   start=ts(2026, 10, 7), end=ts(2026, 10, 8))["ok"])
        # HR can post a studio note
        hr = Client("hr")
        self.assertTrue(self.save(hr, kind="note", scope="studio", title="Fire drill at 11", all_day=True,
                                  start=ts(2026, 10, 12), end=ts(2026, 10, 13))["ok"])
        titles = lambda c: {i["title"] for i in self.range(c)["items"]}  # noqa: E731
        self.assertEqual(titles(art) & {"Dentist", "Buy milk", "FAL delivery", "Publish by 5", "Fire drill at 11"},
                         {"Dentist", "Buy milk", "FAL delivery", "Publish by 5", "Fire drill at 11"})
        self.assertEqual(titles(lit) & {"Dentist", "Buy milk", "FAL delivery", "Publish by 5", "Fire drill at 11"},
                         {"Fire drill at 11"})                          # other team, not in the room
        # the room got a line about the deadline
        for x in (lead, art, lit, hr):
            x.close()

    def test_meeting_repeat_rsvp_and_one_occurrence(self):
        lead, art, lit = Client("lead"), Client("art"), Client("lit")
        r = self.save(lead, kind="meeting", scope="people", people=[self.art], title="Dailies",
                      start=ts(2026, 10, 5, 17), end=ts(2026, 10, 5, 17, 30), rule={"freq": "workdays"})
        self.assertTrue(r["ok"], r)
        eid = r["event_id"]
        self.assertEqual(art.wait_for("event_invite")["title"], "Dailies")
        week = [i for i in self.range(art, "2026-10-05", "2026-10-11")["items"] if i["title"] == "Dailies"]
        self.assertEqual(len(week), 6)                                   # Mon-Sat, no Sunday
        self.assertEqual(week[0]["rule_text"], "Every working day")
        self.assertFalse([i for i in self.range(lit, "2026-10-05", "2026-10-11")["items"] if i["title"] == "Dailies"])
        self.assertTrue(art.request("cal_rsvp", event_id=eid, answer="maybe")["ok"])
        self.assertFalse(lit.request("cal_rsvp", event_id=eid, answer="yes")["ok"])       # not invited
        mine = self.range(art, "2026-10-05", "2026-10-05")["items"][0]
        self.assertEqual(mine["my_rsvp"], "maybe")
        # cancel Wednesday, move Thursday to 18:00 with a new title
        self.assertFalse(art.request("cal_delete", event_id=eid)["ok"])                   # not mine
        self.assertTrue(lead.request("cal_delete", event_id=eid, occ=ts(2026, 10, 7, 17))["ok"])
        self.assertTrue(lead.request("cal_move", event_id=eid, occ=ts(2026, 10, 8, 17), start=ts(2026, 10, 8, 18),
                                     end=ts(2026, 10, 8, 18, 30), title="Dailies (late)")["ok"])
        week = [i for i in self.range(art, "2026-10-05", "2026-10-11")["items"] if i["event_id"] == eid]
        self.assertEqual(len(week), 5)
        late = [i for i in week if i["title"] == "Dailies (late)"]
        self.assertEqual(dt.datetime.fromtimestamp(late[0]["start"]).hour, 18)
        # delete the series
        self.assertTrue(lead.request("cal_delete", event_id=eid)["ok"])
        self.assertFalse([i for i in self.range(art, "2026-10-05", "2026-10-11")["items"] if i["event_id"] == eid])
        # a room meeting: every member sees it and it is announced in the room
        r = self.save(lead, kind="meeting", scope="room", scope_ref=self.room, title="Client review",
                      start=ts(2026, 10, 14, 15), end=ts(2026, 10, 14, 16))
        self.assertTrue(r["ok"])
        self.assertIn("Client review", {i["title"] for i in self.range(art)["items"]})
        self.assertFalse(self.save(lit, kind="meeting", scope="room", scope_ref=self.room, title="x",
                                   start=ts(2026, 10, 14, 15), end=ts(2026, 10, 14, 16))["ok"])
        for x in (lead, art, lit):
            x.close()

    def test_leave_birthdays_anniversaries(self):
        art, lead = Client("art"), Client("lead")
        today = dt.date.today()
        self.assertTrue(art.request("cal_leave_add", first_day=today.isoformat(),
                                    last_day=(today + dt.timedelta(days=2)).isoformat(), note="Family trip")["ok"])
        c = self.core
        self.assertTrue(c.call(c.on_leave_today, self.art))
        leave = self.range(lead, today.isoformat(), (today + dt.timedelta(days=3)).isoformat())["leave"]
        entry = next(x for x in leave if x["user_id"] == self.art)
        self.assertEqual(entry["note"], "")                               # the reason stays private
        self.assertFalse(lead.request("cal_leave_delete", id=entry["id"])["ok"])           # not theirs
        self.assertTrue(art.request("cal_leave_delete", id=entry["id"])["ok"])
        self.assertFalse(c.call(c.on_leave_today, self.art))
        days = self.range(lead, "2026-10-01", "2026-10-31")["people_days"]
        kinds = {(d["kind"], d["day"], d.get("years")) for d in days if d["user_id"] == self.art}
        self.assertIn(("birthday", "2026-10-15", None), kinds)
        self.assertIn(("anniversary", "2026-10-01", 6), kinds)
        art.close()
        lead.close()

    def test_holiday_management_and_ics(self):
        art, hr = Client("art"), Client("hr")
        self.assertFalse(art.request("cal_holiday_save", day="2026-12-31", name="Studio party")["ok"])
        r = hr.request("cal_holiday_save", day="2026-12-31", name="Studio party")
        self.assertTrue(r["ok"], r)
        self.assertIn("Studio party", [h["name"] for h in self.range(art, "2026-12-01", "2026-12-31")["holidays"]])
        dussehra = next(h for h in hr.request("cal_holidays", year=2026)["holidays"] if h["name"] == "Dussehra")
        self.assertTrue(hr.request("cal_holiday_observe", ids=[dussehra["id"]], observed=True)["ok"])
        self.assertIn("Dussehra", [h["name"] for h in self.range(art)["holidays"]])
        self.assertTrue(hr.request("cal_holiday_add_year", year=2036)["ok"])
        ics = ("BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nDTSTART;VALUE=DATE:20261225\r\nDTEND;VALUE=DATE:20261227\r\n"
               "SUMMARY:Year-end break\r\nEND:VEVENT\r\nBEGIN:VEVENT\r\nDTSTART:20261103T100000\r\n"
               "DTEND:20261103T110000\r\nSUMMARY:Dentist\\, again\r\nRRULE:FREQ=WEEKLY;BYDAY=TU;COUNT=3\r\n"
               "END:VEVENT\r\nEND:VCALENDAR\r\n")
        self.assertFalse(art.request("cal_import_ics", target="holidays", text=ics)["ok"])
        self.assertEqual(hr.request("cal_import_ics", target="holidays", text=ics)["added"], 2)
        self.assertEqual(art.request("cal_import_ics", target="mine", text=ics)["added"], 2)
        nov = [i for i in self.range(art, "2026-11-01", "2026-11-30")["items"] if i["title"] == "Dentist, again"]
        self.assertEqual(len(nov), 3)
        self.assertFalse(art.request("cal_import_ics", target="mine", text="not a calendar")["ok"])
        art.close()
        hr.close()


if __name__ == "__main__":
    unittest.main()
