"""The studio calendar (server side): holidays, meetings, personal events, notes of the day, deadlines, leave,
birthdays and work anniversaries.

Mixed into ServerCore like the planner. Times are Unix timestamps in the studio's local time zone (server and
PCs share one LAN); whole days are ISO dates. Repeats follow common/recur.py.

Who sees an item (events.scope):
    private  - only its owner (personal events, my note of the day)
    people   - the owner and the invited people (meetings)
    room     - the members of room scope_ref (room meetings, room deadlines, room notes)
    dept     - everyone in department scope_ref, sections included (team deadlines and notes)
    studio   - everyone (studio notes, studio deadlines)
"""

import datetime
import json
import logging
import time

from common import india_holidays, ics, recur

log = logging.getLogger("server")

KINDS = ("meeting", "event", "note", "deadline")
SCOPES = ("private", "people", "room", "dept", "studio")
RSVP = ("yes", "maybe", "no")
PRESET_YEARS = range(2026, 2036)

CALENDAR_SCHEMA = """
CREATE TABLE IF NOT EXISTS holidays(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    day TEXT NOT NULL,
    name TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'other',
    observed INTEGER NOT NULL DEFAULT 0,
    confirm INTEGER NOT NULL DEFAULT 0,
    source TEXT NOT NULL DEFAULT 'manual',
    UNIQUE(day, name)
);
CREATE INDEX IF NOT EXISTS idx_holidays_day ON holidays(day);
CREATE TABLE IF NOT EXISTS events(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    title TEXT NOT NULL,
    notes TEXT NOT NULL DEFAULT '',
    location TEXT NOT NULL DEFAULT '',
    start REAL NOT NULL,
    end REAL NOT NULL,
    all_day INTEGER NOT NULL DEFAULT 0,
    owner_id INTEGER NOT NULL,
    scope TEXT NOT NULL,
    scope_ref TEXT NOT NULL DEFAULT '',
    rule TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    deleted INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_events_start ON events(deleted, start);
CREATE TABLE IF NOT EXISTS event_people(
    event_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    rsvp TEXT NOT NULL DEFAULT '',
    PRIMARY KEY(event_id, user_id)
);
CREATE TABLE IF NOT EXISTS event_changes(
    event_id INTEGER NOT NULL,
    occ REAL NOT NULL,
    cancelled INTEGER NOT NULL DEFAULT 0,
    start REAL,
    end REAL,
    title TEXT,
    PRIMARY KEY(event_id, occ)
);
CREATE TABLE IF NOT EXISTS leave(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    first_day TEXT NOT NULL,
    last_day TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_leave_days ON leave(last_day, first_day);
"""


def _date(value, what="Date"):
    try:
        return datetime.date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        from server.core import ClientError
        raise ClientError(f"{what} is not a date")


def _dt(ts):
    return datetime.datetime.fromtimestamp(float(ts))


class CalendarMixin:
    """Needs: db, org, config, sessions, push_user, push_all, can_see, audit, _user_name, _room_system_message."""

    CALENDAR_HANDLERS = ("cal_range", "cal_save", "cal_delete", "cal_move", "cal_rsvp", "cal_leave_add",
                         "cal_leave_delete", "cal_holidays", "cal_holiday_save", "cal_holiday_delete",
                         "cal_holiday_observe", "cal_holiday_add_year", "cal_import_ics")

    # ------------------------------------------------------------ setup
    def calendar_setup(self):
        self.db.con.executescript(CALENDAR_SCHEMA)
        if not self.db.get_meta("holidays_seeded"):
            for year in PRESET_YEARS:
                self._seed_year(year)
            self.db.set_meta("holidays_seeded", "1")
        self._leave_today = (None, set())

    def _seed_year(self, year):
        """India's holidays for a year: national days ticked, the rest for the admin to tick."""
        added = 0
        for h in india_holidays.holidays(year):
            cur = self.db._exec("INSERT OR IGNORE INTO holidays(day, name, kind, observed, confirm, source)"
                                " VALUES(?,?,?,?,?,'preset')", h["date"], h["name"], h["kind"],
                                int(h["kind"] == "national"), int(h["confirm"]))
            added += cur.rowcount
        return added

    # ------------------------------------------------------------ permissions
    def _cal_can_manage_holidays(self, uid):
        row = self.db.get_user(uid)
        return bool(row and (row["is_admin"] or self.org.perms(uid).get("manage_users")))

    def _cal_studio_wide(self, uid):
        """Studio notes and studio deadlines: admins, HR/IT and designations that announce to everyone."""
        row = self.db.get_user(uid)
        perms = self.org.perms(uid)
        return bool(row and (row["is_admin"] or perms.get("manage_users") or perms.get("announce") == "all"))

    def _cal_is_lead(self, uid):
        row = self.db.get_user(uid)
        return bool(row and (row["is_admin"] or self.org.level(uid) >= 60))

    def _cal_members(self, ev):
        """User ids who see an event (None = everyone)."""
        scope, ref = ev["scope"], ev["scope_ref"]
        if scope == "studio":
            return None
        if scope == "private":
            return {ev["owner_id"]}
        if scope == "people":
            return {ev["owner_id"]} | {r[0] for r in self.db._all("SELECT user_id FROM event_people WHERE event_id=?",
                                                                     ev["id"])}
        if scope == "room":
            return {ev["owner_id"]} | set(self.db.room_member_ids(int(ref or 0)))
        if scope == "dept":
            return {ev["owner_id"]} | {r[0] for r in self.db._all(
                "SELECT id FROM users WHERE deleted=0 AND lower(department)=lower(?)", ref)}
        return {ev["owner_id"]}

    def _cal_sees(self, uid, ev, dept=None):
        scope, ref = ev["scope"], ev["scope_ref"]
        if ev["owner_id"] == uid or scope == "studio":
            return True
        if scope == "people":
            return self.db._one("SELECT 1 FROM event_people WHERE event_id=? AND user_id=?", ev["id"], uid) is not None
        if scope == "room":
            return self.db.is_room_member(int(ref or 0), uid)
        if scope == "dept":
            return (dept or "").lower() == (ref or "").lower() and bool(ref)
        return False

    def _cal_can_edit(self, uid, ev):
        row = self.db.get_user(uid)
        return ev["owner_id"] == uid or bool(row and row["is_admin"])

    # ------------------------------------------------------------ reading
    def h_cal_range(self, s, req):
        """Everything this person sees between two dates: occurrences, holidays, leave, birthdays."""
        first, last = _date(req.get("start"), "Start"), _date(req.get("end"), "End")
        if last < first or (last - first).days > 400:
            from server.core import ClientError
            raise ClientError("Pick a shorter range")
        return self.calendar_range(s.user_id, first, last)

    def calendar_range(self, uid, first, last):
        range_start = datetime.datetime.combine(first, datetime.time())
        range_end = datetime.datetime.combine(last + datetime.timedelta(days=1), datetime.time())
        me = self.db.get_user(uid)
        dept = me["department"] if me else ""
        names = {}

        def name(u):
            if u not in names:
                names[u] = self._user_name(u)
            return names[u]
        items = []
        rows = self.db._all("SELECT * FROM events WHERE deleted=0 AND start<? AND (rule IS NOT NULL OR end>?)",
                            range_end.timestamp(), range_start.timestamp())
        for ev in rows:
            if not self._cal_sees(uid, ev, dept):
                continue
            items.extend(self._occurrences(ev, uid, range_start, range_end, name))
        items.sort(key=lambda i: (i["start"], i["title"].lower()))
        holidays = [{"day": r["day"], "name": r["name"], "kind": r["kind"], "confirm": bool(r["confirm"])}
                    for r in self.db._all("SELECT * FROM holidays WHERE observed=1 AND day>=? AND day<=? ORDER BY day",
                                          first.isoformat(), last.isoformat())]
        leave = [{"id": r["id"], "user_id": r["user_id"], "name": name(r["user_id"]), "first_day": r["first_day"],
                  "last_day": r["last_day"], "note": r["note"] if r["user_id"] == uid else "",
                  "mine": r["user_id"] == uid}
                 for r in self.db._all("SELECT * FROM leave WHERE last_day>=? AND first_day<=? ORDER BY first_day",
                                       first.isoformat(), last.isoformat())
                 if r["user_id"] == uid or self.can_see(uid, r["user_id"])]
        return {"items": items, "holidays": holidays, "leave": leave,
                "people_days": self._people_days(uid, first, last)}

    def _occurrences(self, ev, uid, range_start, range_end, name):
        changes = {r["occ"]: r for r in self.db._all("SELECT * FROM event_changes WHERE event_id=?", ev["id"])}
        rule = json.loads(ev["rule"]) if ev["rule"] else None
        start, end = _dt(ev["start"]), _dt(ev["end"])
        skip = [_dt(o) for o in changes]
        workdays = tuple(self._workdays())
        occ = recur.expand(start, end, rule, range_start, range_end, workdays=workdays, skip=skip)
        for occ_ts, ch in changes.items():            # moved occurrences come back at their new time
            if not ch["cancelled"] and ch["start"] is not None:
                s, e = _dt(ch["start"]), _dt(ch["end"])
                if s < range_end and e > range_start:
                    occ.append((s, e, occ_ts, ch["title"]))
        people = [{"id": r[0], "name": name(r[0]), "rsvp": r[1]} for r in self.db._all(
            "SELECT user_id, rsvp FROM event_people WHERE event_id=?", ev["id"])]
        mine = next((p["rsvp"] for p in people if p["id"] == uid), "")
        out = []
        for o in occ:
            s, e = o[0], o[1]
            occ_ts = o[2] if len(o) > 2 else s.timestamp()
            title = (o[3] if len(o) > 3 and o[3] else None) or ev["title"]
            out.append({"id": f"{ev['id']}@{occ_ts:.0f}", "event_id": ev["id"], "occ": occ_ts, "kind": ev["kind"],
                        "title": title, "notes": ev["notes"], "location": ev["location"],
                        "start": s.timestamp(), "end": e.timestamp(), "all_day": bool(ev["all_day"]),
                        "scope": ev["scope"], "scope_ref": ev["scope_ref"], "owner_id": ev["owner_id"],
                        "owner_name": name(ev["owner_id"]), "rule": rule,
                        "rule_text": recur.describe(rule, start), "people": people, "my_rsvp": mine,
                        "moved": len(o) > 2, "can_edit": self._cal_can_edit(uid, ev),
                        "series_start": ev["start"], "series_end": ev["end"]})
        return out

    def _workdays(self):
        return (0, 1, 2, 3, 4, 5)            # the studio works Monday to Saturday

    def _people_days(self, uid, first, last):
        """Birthdays (day and month) and work anniversaries of the people this person can see, in the range."""
        out = []
        years = range(first.year, last.year + 1)
        for r in self.db._all("SELECT id, display_name, birthday, joined_on FROM users WHERE deleted=0 AND disabled=0"
                              " AND (birthday<>'' OR joined_on<>'')"):
            if r["id"] != uid and not self.can_see(uid, r["id"]):
                continue
            for y in years:
                if r["birthday"]:
                    md = r["birthday"][-5:]
                    d = self._on_year(y, md)
                    if d and first <= d <= last:
                        out.append({"day": d.isoformat(), "kind": "birthday", "user_id": r["id"],
                                    "name": r["display_name"]})
                if r["joined_on"]:
                    joined = datetime.date.fromisoformat(r["joined_on"])
                    d = self._on_year(y, r["joined_on"][5:])
                    if d and first <= d <= last and y > joined.year:
                        out.append({"day": d.isoformat(), "kind": "anniversary", "user_id": r["id"],
                                    "name": r["display_name"], "years": y - joined.year})
        return sorted(out, key=lambda x: (x["day"], x["name"].lower()))

    @staticmethod
    def _on_year(year, md):
        try:
            return datetime.date(year, int(md[:2]), int(md[3:5]))
        except ValueError:
            return datetime.date(year, 2, 28) if md == "02-29" else None

    # ------------------------------------------------------------ writing events
    def h_cal_save(self, s, req):
        from server.core import ClientError
        ev = req.get("event") or {}
        if not isinstance(ev, dict):
            raise ClientError("Invalid event")
        kind = ev.get("kind")
        if kind not in KINDS:
            raise ClientError("Unknown kind of calendar item")
        title = " ".join(str(ev.get("title") or "").split())[:120]
        if not title:
            raise ClientError("Give it a title")
        try:
            start, end = float(ev["start"]), float(ev["end"])
        except (KeyError, TypeError, ValueError):
            raise ClientError("Pick a date and time")
        all_day = bool(ev.get("all_day"))
        if end <= start:
            if all_day:
                end = start + 86400
            else:
                raise ClientError("It has to end after it starts")
        if end - start > 31 * 86400:
            raise ClientError("One item can last at most 31 days")
        try:
            rule = recur.clean(ev.get("rule"))
        except (TypeError, ValueError):
            raise ClientError("That repeat is not possible")
        scope, ref = str(ev.get("scope") or ""), str(ev.get("scope_ref") or "")
        people = [int(u) for u in ev.get("people") or [] if str(u).isdigit()]
        scope, ref = self._cal_check_scope(s.user_id, kind, scope, ref, people)
        notes = str(ev.get("notes") or "")[:2000]
        location = " ".join(str(ev.get("location") or "").split())[:200]
        now = time.time()
        event_id = ev.get("id")
        if event_id:
            old = self.db._one("SELECT * FROM events WHERE id=? AND deleted=0", int(event_id))
            if not old or not self._cal_can_edit(s.user_id, old):
                raise ClientError("Only the person who made it can change it")
            before = self._cal_members(old)
            self.db._exec("UPDATE events SET kind=?, title=?, notes=?, location=?, start=?, end=?, all_day=?,"
                          " scope=?, scope_ref=?, rule=?, updated_at=? WHERE id=?", kind, title, notes, location,
                          start, end, int(all_day), scope, ref, json.dumps(rule) if rule else None, now, old["id"])
            if old["start"] != start or (old["rule"] or "") != (json.dumps(rule) if rule else ""):
                self.db._exec("DELETE FROM event_changes WHERE event_id=?", old["id"])   # the series moved
            event_id = old["id"]
        else:
            before = set()
            cur = self.db._exec("INSERT INTO events(kind, title, notes, location, start, end, all_day, owner_id,"
                                " scope, scope_ref, rule, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                                kind, title, notes, location, start, end, int(all_day), s.user_id, scope, ref,
                                json.dumps(rule) if rule else None, now, now)
            event_id = cur.lastrowid
        if scope == "people":
            keep = set(people) - {s.user_id}
            existing = {r[0] for r in self.db._all("SELECT user_id FROM event_people WHERE event_id=?", event_id)}
            for uid in existing - keep:
                self.db._exec("DELETE FROM event_people WHERE event_id=? AND user_id=?", event_id, uid)
            for uid in keep - existing:
                self.db._exec("INSERT OR IGNORE INTO event_people(event_id, user_id) VALUES(?,?)", event_id, uid)
        ev_row = self.db._one("SELECT * FROM events WHERE id=?", event_id)
        after = self._cal_members(ev_row)
        self._cal_changed(before, after)
        if not ev.get("id"):
            self._cal_announce(s.user_id, ev_row, rule)
        return {"event_id": event_id}

    def _cal_check_scope(self, uid, kind, scope, ref, people):
        from server.core import ClientError
        if kind == "event":
            return "private", ""
        if kind == "meeting":
            if scope == "room":
                if not self.db.is_room_member(int(ref or 0), uid):
                    raise ClientError("You are not in that room")
                return "room", str(int(ref))
            for p in people:
                if not self.can_see(uid, p):
                    raise ClientError("You can't invite someone you can't see")
            return "people", ""
        if kind == "note":
            if scope == "private":
                return "private", ""
            if scope == "studio":
                if not self._cal_studio_wide(uid):
                    raise ClientError("Only administrators, HR and management can post a studio note")
                return "studio", ""
            if not self._cal_is_lead(uid):
                raise ClientError("Only leads, supervisors and production can post a team note")
            return self._cal_team_scope(uid, scope, ref)
        # deadline
        if not self._cal_is_lead(uid):
            raise ClientError("Only production, supervisors and leads can add deadlines")
        if scope == "studio":
            if not self._cal_studio_wide(uid):
                raise ClientError("Only administrators, HR and management can add a studio-wide deadline")
            return "studio", ""
        return self._cal_team_scope(uid, scope, ref)

    def _cal_team_scope(self, uid, scope, ref):
        from server.core import ClientError
        if scope == "room":
            if not self.db.is_room_member(int(ref or 0), uid):
                raise ClientError("You are not in that room")
            return "room", str(int(ref))
        if scope == "dept":
            dept = self.db.find_department(ref)
            if not dept:
                raise ClientError("Pick a department")
            me = self.db.get_user(uid)
            if dept["name"].lower() != (me["department"] or "").lower() and not self._cal_studio_wide(uid):
                raise ClientError("You can post only for your own department")
            return "dept", dept["name"]
        raise ClientError("Pick a room or a department")

    def _cal_announce(self, uid, ev, rule):
        """A new meeting or deadline tells the people concerned (a line in the room, or an invitation)."""
        when = _dt(ev["start"])
        text = f"{when:%a %d %b}" + ("" if ev["all_day"] else f", {when:%H:%M}")
        if rule:
            text += f" · {recur.describe(rule, when)}"
        if ev["scope"] == "room" and ev["kind"] in ("meeting", "deadline"):
            icon = "📅" if ev["kind"] == "meeting" else "⏳"
            what = "scheduled" if ev["kind"] == "meeting" else "set a deadline:"
            self._room_system_message(int(ev["scope_ref"]), uid, f"{icon} {self._user_name(uid)} {what} "
                                                                 f"“{ev['title']}” · {text}")
        if ev["scope"] == "people":
            for (invitee,) in self.db._all("SELECT user_id FROM event_people WHERE event_id=?", ev["id"]):
                self.push_user(invitee, {"op": "event_invite", "event_id": ev["id"], "title": ev["title"],
                                         "when": text, "from": self._user_name(uid)})

    def _cal_changed(self, *groups):
        """Tell everyone who (now or before) sees an item to fetch their calendar again."""
        everyone = any(g is None for g in groups)
        targets = set().union(*[g for g in groups if g]) if not everyone else set(self.sessions)
        for uid in targets:
            if uid in self.sessions:
                self.push_user(uid, {"op": "calendar_changed"})

    def h_cal_delete(self, s, req):
        """Delete a whole series, or (occ given) just that occurrence."""
        from server.core import ClientError
        ev = self.db._one("SELECT * FROM events WHERE id=? AND deleted=0", int(req.get("event_id") or 0))
        if not ev or not self._cal_can_edit(s.user_id, ev):
            raise ClientError("Only the person who made it can delete it")
        members = self._cal_members(ev)
        if req.get("occ") is not None and ev["rule"]:
            self.db._exec("INSERT OR REPLACE INTO event_changes(event_id, occ, cancelled) VALUES(?,?,1)",
                          ev["id"], float(req["occ"]))
        else:
            self.db._exec("UPDATE events SET deleted=1, updated_at=? WHERE id=?", time.time(), ev["id"])
        self._cal_changed(members)

    def h_cal_move(self, s, req):
        """Change one occurrence of a repeating item (new time and/or title) without touching the rest."""
        from server.core import ClientError
        ev = self.db._one("SELECT * FROM events WHERE id=? AND deleted=0", int(req.get("event_id") or 0))
        if not ev or not self._cal_can_edit(s.user_id, ev):
            raise ClientError("Only the person who made it can change it")
        start, end = float(req["start"]), float(req["end"])
        if end <= start:
            raise ClientError("It has to end after it starts")
        title = " ".join(str(req.get("title") or "").split())[:120] or None
        if not ev["rule"]:
            self.db._exec("UPDATE events SET start=?, end=?, title=COALESCE(?, title), updated_at=? WHERE id=?",
                          start, end, title, time.time(), ev["id"])
        else:
            self.db._exec("INSERT OR REPLACE INTO event_changes(event_id, occ, cancelled, start, end, title)"
                          " VALUES(?,?,0,?,?,?)", ev["id"], float(req["occ"]), start, end, title)
        self._cal_changed(self._cal_members(ev))

    def h_cal_rsvp(self, s, req):
        from server.core import ClientError
        answer = req.get("answer")
        if answer not in RSVP:
            raise ClientError("Answer yes, maybe or no")
        ev = self.db._one("SELECT * FROM events WHERE id=? AND deleted=0 AND kind='meeting'",
                          int(req.get("event_id") or 0))
        me = self.db.get_user(s.user_id)
        if not ev or not self._cal_sees(s.user_id, ev, me["department"]):
            raise ClientError("Meeting not found")
        self.db._exec("INSERT INTO event_people(event_id, user_id, rsvp) VALUES(?,?,?) ON CONFLICT(event_id, user_id)"
                      " DO UPDATE SET rsvp=excluded.rsvp", ev["id"], s.user_id, answer)
        self._cal_changed({ev["owner_id"], s.user_id})

    # ------------------------------------------------------------ leave
    def h_cal_leave_add(self, s, req):
        from server.core import ClientError
        first, last = _date(req.get("first_day"), "First day"), _date(req.get("last_day"), "Last day")
        if last < first or (last - first).days > 366:
            raise ClientError("Check the dates")
        note = " ".join(str(req.get("note") or "").split())[:200]
        self.db._exec("INSERT INTO leave(user_id, first_day, last_day, note, created_at) VALUES(?,?,?,?,?)",
                      s.user_id, first.isoformat(), last.isoformat(), note, time.time())
        self._leave_changed(s.user_id)

    def h_cal_leave_delete(self, s, req):
        from server.core import ClientError
        row = self.db._one("SELECT * FROM leave WHERE id=?", int(req.get("id") or 0))
        me = self.db.get_user(s.user_id)
        if not row or (row["user_id"] != s.user_id and not me["is_admin"]):
            raise ClientError("Not found")
        self.db._exec("DELETE FROM leave WHERE id=?", row["id"])
        self._leave_changed(row["user_id"])

    def _leave_changed(self, uid):
        self._leave_today = (None, set())
        self._org_user_changed(uid)
        self._pub_cache.pop(uid, None)
        self._push_user(uid)
        self._cal_changed(None)

    def on_leave_today(self, uid):
        today = datetime.date.today().isoformat()
        day, users = getattr(self, "_leave_today", (None, set()))
        if day != today:
            users = {r[0] for r in self.db._all("SELECT user_id FROM leave WHERE first_day<=? AND last_day>=?",
                                                today, today)}
            self._leave_today = (today, users)
        return uid in users

    # ------------------------------------------------------------ holidays
    def h_cal_holidays(self, s, req):
        """The holiday list of a year - all of them for admins/HR (to tick), the observed ones for others."""
        year = int(req.get("year") or datetime.date.today().year)
        manage = self._cal_can_manage_holidays(s.user_id)
        return {"holidays": self.holiday_list(year, not manage), "can_manage": manage}

    def holiday_list(self, year, observed_only=False):
        rows = self.db._all("SELECT * FROM holidays WHERE day>=? AND day<=?" + (" AND observed=1" if observed_only else "")
                            + " ORDER BY day, name", f"{year}-01-01", f"{year}-12-31")
        return [{"id": r["id"], "day": r["day"], "name": r["name"], "kind": r["kind"], "observed": bool(r["observed"]),
                 "confirm": bool(r["confirm"]), "source": r["source"]} for r in rows]

    def _cal_holiday_guard(self, s):
        from server.core import ClientError
        if not self._cal_can_manage_holidays(s.user_id):
            raise ClientError("Only administrators and HR can change the holiday list")

    def h_cal_holiday_save(self, s, req):
        self._cal_holiday_guard(s)
        return {"id": self.holiday_save(req.get("id"), req.get("day"), req.get("name"), req.get("observed", True),
                                        actor=self._user_name(s.user_id))}

    def holiday_save(self, hid, day, name, observed=True, actor=None):
        from server.core import ClientError
        day = _date(day, "Day").isoformat()
        name = " ".join(str(name or "").split())[:80]
        if not name:
            raise ClientError("Give the holiday a name")
        if hid:
            self.db._exec("UPDATE holidays SET day=?, name=?, observed=?, confirm=0 WHERE id=?", day, name,
                          int(bool(observed)), int(hid))
        else:
            cur = self.db._exec("INSERT OR REPLACE INTO holidays(day, name, kind, observed, confirm, source)"
                                " VALUES(?,?,'studio',?,0,'manual')", day, name, int(bool(observed)))
            hid = cur.lastrowid
        self.audit(actor, "holiday saved", f"{day} {name}", "observed" if observed else "not observed")
        self._cal_changed(None)
        return int(hid)

    def h_cal_holiday_delete(self, s, req):
        self._cal_holiday_guard(s)
        self.holiday_delete(req.get("id"), actor=self._user_name(s.user_id))

    def holiday_delete(self, hid, actor=None):
        row = self.db._one("SELECT * FROM holidays WHERE id=?", int(hid or 0))
        if row:
            self.db._exec("DELETE FROM holidays WHERE id=?", row["id"])
            self.audit(actor, "holiday deleted", f"{row['day']} {row['name']}")
            self._cal_changed(None)

    def h_cal_holiday_observe(self, s, req):
        self._cal_holiday_guard(s)
        self.holiday_observe(req.get("ids") or [], bool(req.get("observed", True)), actor=self._user_name(s.user_id))

    def holiday_observe(self, ids, observed, actor=None):
        ids = [int(i) for i in ids if str(i).isdigit()]
        for hid in ids:
            self.db._exec("UPDATE holidays SET observed=?, confirm=0 WHERE id=?", int(observed), hid)
        if ids:
            self.audit(actor, "holidays " + ("ticked" if observed else "unticked"), f"{len(ids)} days")
            self._cal_changed(None)

    def h_cal_holiday_add_year(self, s, req):
        self._cal_holiday_guard(s)
        return {"added": self.holiday_add_year(req.get("year"))}

    def holiday_add_year(self, year):
        from server.core import ClientError
        year = int(year or 0)
        if not 2000 <= year <= 2100:
            raise ClientError("Pick a year between 2000 and 2100")
        added = self._seed_year(year)
        self._cal_changed(None)
        return added

    # ------------------------------------------------------------ .ics import
    def h_cal_import_ics(self, s, req):
        """Holidays (admins/HR) or my own events from an .ics file (Outlook, Google, a holiday site)."""
        from server.core import ClientError
        target = req.get("target")
        text = str(req.get("text") or "")
        if len(text) > 5 * 1024 * 1024:
            raise ClientError("That file is too big (5 MB at most)")
        try:
            events = ics.parse(text)
        except Exception:  # noqa: BLE001 - a broken file must not break the server
            raise ClientError("That doesn't look like an .ics calendar file")
        if not events:
            raise ClientError("No events found in that file")
        if target == "holidays":
            self._cal_holiday_guard(s)
            added = 0
            for e in events:
                if not e["all_day"]:
                    continue
                day = e["start"].date()
                while day < e["end"].date() and added < 2000:          # a holiday of several days
                    cur = self.db._exec("INSERT OR IGNORE INTO holidays(day, name, kind, observed, confirm, source)"
                                        " VALUES(?,?,'studio',1,0,'ics')", day.isoformat(), e["title"][:80])
                    added += cur.rowcount
                    day += datetime.timedelta(days=1)
            self.audit(self._user_name(s.user_id), "holidays imported", f"{added} days from an .ics file")
            self._cal_changed(None)
            return {"added": added}
        added = 0
        now = time.time()
        for e in events[:2000]:
            self.db._exec("INSERT INTO events(kind, title, notes, location, start, end, all_day, owner_id, scope,"
                          " scope_ref, rule, created_at, updated_at) VALUES('event',?,?,?,?,?,?,?, 'private', '', ?,?,?)",
                          e["title"], e["notes"], e["location"], e["start"].timestamp(), e["end"].timestamp(),
                          int(e["all_day"]), s.user_id, json.dumps(e["rule"]) if e["rule"] else None, now, now)
            added += 1
        self._cal_changed({s.user_id})
        return {"added": added}

    # ------------------------------------------------------------ reminders
    def calendar_boot(self, uid):
        """At sign-in: the next two weeks, so Home and reminders work straight away."""
        today = datetime.date.today()
        try:
            return {"calendar": self.calendar_range(uid, today, today + datetime.timedelta(days=14))}
        except Exception:  # noqa: BLE001 - the calendar must never stop a sign-in
            log.exception("calendar at sign-in failed")
            return {"calendar": None}
