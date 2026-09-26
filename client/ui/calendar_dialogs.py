"""Calendar windows: add or change an item, see its details (and answer a meeting), take leave, and the
studio holiday list (admins and HR)."""

import datetime

from PySide6.QtCore import QDate, Qt, QTime
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDateEdit, QDialogButtonBox, QFileDialog, QFormLayout, QHBoxLayout,
    QHeaderView, QLabel, QLineEdit, QMessageBox, QPlainTextEdit, QPushButton, QSpinBox, QTableWidget,
    QTableWidgetItem, QTimeEdit, QWidget,
)

from common import theme as T
from common.icons import icon
from client.ui.calendar_views import kind_color
from client.ui.dialogs import Dialog, MemberPicker
from client.ui.widgets import rich_safe

KIND_TITLES = {"meeting": "Meeting", "event": "Personal event", "note": "Note of the day", "deadline": "Deadline"}
REPEATS = [("Does not repeat", None), ("Every day", "daily"), ("Every working day (Mon–Sat)", "workdays"),
           ("Every week on...", "weekly"), ("Every month", "monthly")]


def can_lead(store):
    me = store.me
    return bool(me.get("is_admin") or (me.get("level") or 0) >= 60)


def studio_wide(store):
    me, perms = store.me, store.me.get("perms") or {}
    return bool(me.get("is_admin") or perms.get("manage_users") or perms.get("announce") == "all")


def can_manage_holidays(store):
    me, perms = store.me, store.me.get("perms") or {}
    return bool(me.get("is_admin") or perms.get("manage_users"))


def _qdate(d):
    return QDate(d.year, d.month, d.day)


def _pydate(q):
    return datetime.date(q.year(), q.month(), q.day())


class EventDialog(Dialog):
    """Add or change a meeting, personal event, note of the day or deadline.

    mode 'new' / 'series' (the whole thing) / 'one' (just this occurrence of a repeating item)."""

    def __init__(self, ctx, kind, start=None, item=None, mode="new", room_id=None):
        super().__init__(ctx, ("New " if mode == "new" else "Change ") + KIND_TITLES[kind].lower()
                         + (" (this one only)" if mode == "one" else ""), 520)
        self.ctx, self.store, self.kind, self.item, self.mode = ctx, ctx.store, kind, item, mode
        if item and mode == "series":
            start = datetime.datetime.fromtimestamp(item["series_start"])
            end = datetime.datetime.fromtimestamp(item["series_end"])
        elif item:
            start = datetime.datetime.fromtimestamp(item["start"])
            end = datetime.datetime.fromtimestamp(item["end"])
        else:
            start = start or (datetime.datetime.now().replace(minute=0, second=0, microsecond=0)
                              + datetime.timedelta(hours=1))
            end = start + (datetime.timedelta(minutes=30) if kind == "meeting" else datetime.timedelta(hours=1))
        form = QFormLayout()
        form.setSpacing(10)
        form.setLabelAlignment(Qt.AlignLeft)
        self.title = QLineEdit(item["title"] if item else "")
        self.title.setPlaceholderText({"meeting": "e.g. Dailies, Client review", "event": "e.g. Dentist",
                                       "note": "e.g. Fire drill at 11", "deadline": "e.g. FAL delivery"}[kind])
        self.title.setMinimumHeight(36)
        form.addRow("Title", self.title)
        # when
        self.date = QDateEdit(_qdate(start.date()))
        self.date.setCalendarPopup(True)
        self.date.setDisplayFormat("ddd dd MMM yyyy")
        self.all_day = QCheckBox("All day")
        self.all_day.setChecked(bool(item["all_day"]) if item else kind == "note")
        self.t_start = QTimeEdit(QTime(start.hour, start.minute))
        self.t_end = QTimeEdit(QTime(end.hour, end.minute))
        for t in (self.t_start, self.t_end):
            t.setDisplayFormat("HH:mm")
        when = QHBoxLayout()
        when.addWidget(self.date, 1)
        when.addWidget(self.t_start)
        self.dash = QLabel("–")
        when.addWidget(self.dash)
        when.addWidget(self.t_end)
        when.addWidget(self.all_day)
        form.addRow("When", when)
        self.all_day.toggled.connect(self._all_day)
        self._all_day(self.all_day.isChecked())
        # repeat
        self.repeat = QComboBox()
        for label, freq in REPEATS:
            self.repeat.addItem(label, freq)
        self.days_row = QWidget()
        dl = QHBoxLayout(self.days_row)
        dl.setContentsMargins(0, 0, 0, 0)
        self.day_boxes = []
        for i, n in enumerate(("Mo", "Tu", "We", "Th", "Fr", "Sa", "Su")):
            b = QCheckBox(n)
            self.day_boxes.append(b)
            dl.addWidget(b)
        dl.addStretch(1)
        self.ends = QComboBox()
        self.ends.addItems(["Never ends", "Ends on", "Ends after"])
        self.until = QDateEdit(_qdate(start.date() + datetime.timedelta(days=90)))
        self.until.setCalendarPopup(True)
        self.until.setDisplayFormat("dd MMM yyyy")
        self.count = QSpinBox()
        self.count.setRange(2, 500)
        self.count.setValue(10)
        self.count.setSuffix(" times")
        ends = QHBoxLayout()
        ends.addWidget(self.ends)
        ends.addWidget(self.until)
        ends.addWidget(self.count)
        ends.addStretch(1)
        self.repeat_row = QWidget()
        rl = QFormLayout(self.repeat_row)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.addRow(self.repeat)
        rl.addRow(self.days_row)
        rl.addRow(ends)
        form.addRow("Repeat", self.repeat_row)
        rule = (item or {}).get("rule")
        if rule:
            self.repeat.setCurrentIndex(max(0, [f for _l, f in REPEATS].index(rule["freq"])))
            for d in rule.get("days") or []:
                self.day_boxes[int(d)].setChecked(True)
            if rule.get("until"):
                self.ends.setCurrentIndex(1)
                self.until.setDate(_qdate(datetime.date.fromisoformat(rule["until"])))
            elif rule.get("count"):
                self.ends.setCurrentIndex(2)
                self.count.setValue(int(rule["count"]))
        else:
            self.day_boxes[start.weekday()].setChecked(True)
        self.repeat.currentIndexChanged.connect(self._repeat_changed)
        self.ends.currentIndexChanged.connect(self._repeat_changed)
        self._repeat_changed()
        self.repeat_row.setVisible(mode != "one")
        form.labelForField(self.repeat_row).setVisible(mode != "one")
        # who sees it
        self.scope = QComboBox()
        self.picker = None
        if kind in ("meeting", "note", "deadline") and mode != "one":
            self._fill_scope(kind, item, room_id)
            form.addRow({"meeting": "Invite", "note": "Who sees it", "deadline": "For"}[kind], self.scope)
            if kind == "meeting":
                invited = [p["id"] for p in (item or {}).get("people", [])]
                self.picker = MemberPicker(self.store, checked=invited)
                self.picker.list.setMinimumHeight(170)
                form.addRow("", self.picker)
                self.scope.currentIndexChanged.connect(
                    lambda _i: self.picker.setVisible(self.scope.currentData() == ("people", "")))
                self.picker.setVisible(self.scope.currentData() == ("people", ""))
        if kind in ("meeting", "event") and mode != "one":
            self.location = QLineEdit((item or {}).get("location", ""))
            self.location.setPlaceholderText("room, link or desk (optional)")
            form.addRow("Where", self.location)
        else:
            self.location = None
        if mode != "one":
            self.notes = QPlainTextEdit((item or {}).get("notes", ""))
            self.notes.setPlaceholderText("Details (optional)")
            self.notes.setFixedHeight(70)
            form.addRow("Notes", self.notes)
        else:
            self.notes = None
        self.lay.addLayout(form)
        bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        T.polish(bb.button(QDialogButtonBox.Save), primary=True)
        bb.accepted.connect(self._save)
        bb.rejected.connect(self.reject)
        self.lay.addWidget(bb)
        self.title.setFocus()

    def _fill_scope(self, kind, item, room_id):
        me = self.store.me
        options = []
        if kind == "meeting":
            options.append(("People I choose", ("people", "")))
            for r in sorted(self.store.rooms.values(), key=lambda r: r["name"].lower()):
                options.append((f"Everyone in # {r['name']}", ("room", str(r["id"]))))
        elif kind == "note":
            options.append(("Only me", ("private", "")))
            if can_lead(self.store):
                if me.get("department"):
                    options.append((f"My team · {me['department']}", ("dept", me["department"])))
                for r in sorted(self.store.rooms.values(), key=lambda r: r["name"].lower()):
                    options.append((f"# {r['name']}", ("room", str(r["id"]))))
            if studio_wide(self.store):
                options.append(("The whole studio", ("studio", "")))
        else:
            for r in sorted(self.store.rooms.values(), key=lambda r: r["name"].lower()):
                options.append((f"# {r['name']}", ("room", str(r["id"]))))
            if me.get("department"):
                options.append((f"My department · {me['department']}", ("dept", me["department"])))
            if studio_wide(self.store):
                options.append(("The whole studio", ("studio", "")))
        for label, data in options:
            self.scope.addItem(label, data)
        want = (item["scope"], str(item["scope_ref"])) if item else (("room", str(room_id)) if room_id else None)
        if want:
            for i in range(self.scope.count()):
                if self.scope.itemData(i) == want:
                    self.scope.setCurrentIndex(i)

    def _all_day(self, on):
        for w in (self.t_start, self.t_end, self.dash):
            w.setVisible(not on)

    def _repeat_changed(self, *_):
        freq = self.repeat.currentData()
        self.days_row.setVisible(freq == "weekly")
        repeating = freq is not None
        self.ends.setVisible(repeating)
        self.until.setVisible(repeating and self.ends.currentIndex() == 1)
        self.count.setVisible(repeating and self.ends.currentIndex() == 2)

    def _times(self):
        day = _pydate(self.date.date())
        if self.all_day.isChecked():
            s = datetime.datetime.combine(day, datetime.time())
            return s, s + datetime.timedelta(days=1)
        s = datetime.datetime.combine(day, self.t_start.time().toPython())
        e = datetime.datetime.combine(day, self.t_end.time().toPython())
        if e <= s:
            e += datetime.timedelta(days=1)                  # e.g. a night shift 22:00 - 02:00
        return s, e

    def _rule(self):
        freq = self.repeat.currentData()
        if not freq:
            return None
        rule = {"freq": freq}
        if freq == "weekly":
            rule["days"] = [i for i, b in enumerate(self.day_boxes) if b.isChecked()] or \
                [_pydate(self.date.date()).weekday()]
        if self.ends.currentIndex() == 1:
            rule["until"] = _pydate(self.until.date()).isoformat()
        elif self.ends.currentIndex() == 2:
            rule["count"] = self.count.value()
        return rule

    def _save(self):
        s, e = self._times()
        if self.mode == "one":
            req = ("cal_move", {"event_id": self.item["event_id"], "occ": self.item["occ"], "start": s.timestamp(),
                                "end": e.timestamp(), "title": self.title.text().strip()})
        else:
            scope, ref = self.scope.currentData() if self.scope.count() else ("private", "")
            ev = {"kind": self.kind, "title": self.title.text().strip(), "start": s.timestamp(),
                  "end": e.timestamp(), "all_day": self.all_day.isChecked(), "rule": self._rule(),
                  "scope": scope, "scope_ref": ref, "notes": self.notes.toPlainText() if self.notes else "",
                  "location": self.location.text() if self.location else ""}
            if self.picker is not None and scope == "people":
                ev["people"] = self.picker.selected()
                if not ev["people"]:
                    QMessageBox.information(self, "Meeting", "Tick the people to invite, or pick a room.")
                    return
            if self.item:
                ev["id"] = self.item["event_id"]
            req = ("cal_save", {"event": ev})

        def done(reply):
            if reply.get("ok"):
                self.accept()
            else:
                QMessageBox.warning(self, "Calendar", reply.get("error", "Not saved"))
        self.ctx.conn.request(req[0], done, **req[1])


class ItemDialog(Dialog):
    """Details of one calendar entry, with the actions that fit (answer, open the room, change, delete)."""

    def __init__(self, ctx, entry):
        super().__init__(ctx, "Calendar", 460)
        self.ctx, self.entry = ctx, entry
        item = entry.item or {}
        head = QHBoxLayout()
        dot = QLabel()
        dot.setFixedSize(12, 12)
        dot.setStyleSheet(f"background: {kind_color(entry.kind)}; border-radius: 6px;")
        head.addWidget(dot, 0, Qt.AlignTop)
        title = QLabel(entry.title)
        title.setTextFormat(Qt.PlainText)
        title.setWordWrap(True)
        title.setStyleSheet("font-size: 13pt; font-weight: 700;")
        head.addWidget(title, 1)
        self.lay.addLayout(head)
        when = f"{entry.start:%A %d %B %Y}"
        if entry.all_day:
            last = (entry.end - datetime.timedelta(seconds=1)).date()
            if last != entry.start.date():
                when = f"{entry.start:%a %d %b} – {last:%a %d %b %Y}"
        else:
            when += f",  {entry.start:%H:%M} – {entry.end:%H:%M}"
        lines = [when]
        if item.get("rule_text"):
            lines.append("🔁 " + item["rule_text"])
        store = ctx.store
        kind_text = {"meeting": "Meeting", "event": "Personal event", "note": "Note of the day",
                     "deadline": "Deadline", "holiday": "Studio holiday", "leave": "Leave",
                     "birthday": "Birthday", "anniversary": "Work anniversary"}[entry.kind]
        scope = item.get("scope")
        if scope == "room":
            room = store.rooms.get(int(item["scope_ref"]))
            kind_text += f"  ·  # {room['name'] if room else 'a room'}"
        elif scope == "dept":
            kind_text += f"  ·  {item['scope_ref']} team"
        elif scope == "studio":
            kind_text += "  ·  whole studio"
        elif scope == "private":
            kind_text += "  ·  only you"
        lines.append(kind_text)
        if item.get("owner_name") and item.get("owner_id") != store.my_id:
            lines.append(f"By {item['owner_name']}")
        if item.get("location"):
            lines.append(f"📍 {item['location']}")
        if entry.kind == "holiday" and item.get("confirm"):
            lines.append("The date follows the lunar calendar - check it with your almanac.")
        info = QLabel("\n".join(lines))
        info.setTextFormat(Qt.PlainText)
        info.setWordWrap(True)
        info.setStyleSheet(f"color: {T.MUTED};")
        self.lay.addWidget(info)
        if item.get("notes"):
            notes = QLabel(item["notes"])
            notes.setTextFormat(Qt.PlainText)
            notes.setWordWrap(True)
            notes.setStyleSheet(f"background: {T.SURFACE}; border-radius: 12px; padding: 10px;")
            self.lay.addWidget(notes)
        if entry.kind == "meeting" and item.get("people"):
            marks = {"yes": "✓", "maybe": "?", "no": "✕", "": "·"}
            people = "   ".join(f"{marks[p['rsvp']]} {p['name']}" for p in item["people"])
            who = QLabel(f"Invited:  {people}")
            who.setTextFormat(Qt.PlainText)
            who.setWordWrap(True)
            who.setStyleSheet(f"color: {T.MUTED}; font-size: 9pt;")
            self.lay.addWidget(who)
        row = QHBoxLayout()
        if entry.kind == "meeting" and item.get("owner_id") != store.my_id:
            for answer, label in (("yes", "Going"), ("maybe", "Maybe"), ("no", "Can't go")):
                b = QPushButton(label)
                b.setCheckable(True)
                b.setChecked(item.get("my_rsvp") == answer)
                b.clicked.connect(lambda _=False, a=answer: self._rsvp(a))
                row.addWidget(b)
        if scope == "room":
            chat = QPushButton(" Open room")
            chat.setIcon(icon("chat", T.TEXT, 15))
            chat.clicked.connect(lambda: (self.accept(), ctx.open_conv(f"r:{item['scope_ref']}")))
            row.addWidget(chat)
        if entry.kind == "birthday" and item.get("user_id") != store.my_id:
            wish = QPushButton(" Send wishes")
            wish.setIcon(icon("chat", T.TEXT, 15))
            wish.clicked.connect(lambda: self._wish(item["user_id"], "Happy birthday! 🎂🎉"))
            row.addWidget(wish)
        if entry.kind == "anniversary" and item.get("user_id") != store.my_id:
            wish = QPushButton(" Congratulate")
            wish.clicked.connect(lambda: self._wish(item["user_id"], f"Happy work anniversary - "
                                                                    f"{item['years']} years! 🎉"))
            row.addWidget(wish)
        if entry.kind == "leave" and item.get("mine"):
            rm = QPushButton("Cancel this leave")
            T.polish(rm, danger=True)
            rm.clicked.connect(self._cancel_leave)
            row.addWidget(rm)
        row.addStretch(1)
        if item.get("can_edit"):
            edit = QPushButton(" Change")
            edit.setIcon(icon("edit", T.TEXT, 15))
            edit.clicked.connect(self._edit)
            row.addWidget(edit)
            delete = QPushButton(" Delete")
            delete.setIcon(icon("trash", T.DANGER, 15))
            T.polish(delete, danger=True)
            delete.clicked.connect(self._delete)
            row.addWidget(delete)
        close = QPushButton("Close")
        close.clicked.connect(self.reject)
        row.addWidget(close)
        self.lay.addLayout(row)

    def _reply(self, reply):
        if reply.get("ok"):
            self.accept()
        else:
            QMessageBox.warning(self, "Calendar", reply.get("error", "Didn't work"))

    def _rsvp(self, answer):
        self.ctx.conn.request("cal_rsvp", self._reply, event_id=self.entry.item["event_id"], answer=answer)

    def _wish(self, uid, text):
        self.accept()
        self.ctx.open_conv(f"u:{uid}")
        self.ctx.chat.input.setPlainText(text)
        self.ctx.chat.input.setFocus()

    def _cancel_leave(self):
        self.ctx.conn.request("cal_leave_delete", self._reply, id=self.entry.item["id"])

    def _which(self, action):
        """For a repeating item: this one or all of them? (None = cancelled)"""
        item = self.entry.item
        if not item.get("rule"):
            return "series"
        box = QMessageBox(self)
        box.setWindowTitle(action)
        box.setText(rich_safe(f"“{item['title']}” repeats ({item['rule_text'].lower()}). {action}:"))
        one = box.addButton("Only this one", QMessageBox.AcceptRole)
        allb = box.addButton("All of them", QMessageBox.AcceptRole)
        box.addButton("Cancel", QMessageBox.RejectRole)
        box.exec()
        return "one" if box.clickedButton() is one else "series" if box.clickedButton() is allb else None

    def _edit(self):
        mode = self._which("Change")
        if mode:
            self.accept()
            EventDialog(self.ctx, self.entry.kind, item=self.entry.item, mode=mode).exec()

    def _delete(self):
        mode = self._which("Delete")
        if not mode:
            return
        item = self.entry.item
        if mode == "series" and QMessageBox.question(self, "Delete", rich_safe(f"Delete “{item['title']}”?")) \
                != QMessageBox.Yes:
            return
        kw = {"event_id": item["event_id"]}
        if mode == "one":
            kw["occ"] = item["occ"]
        self.ctx.conn.request("cal_delete", self._reply, **kw)


class LeaveDialog(Dialog):
    def __init__(self, ctx, day=None):
        super().__init__(ctx, "Leave / out of office", 420)
        self.ctx = ctx
        day = day or datetime.date.today()
        note = QLabel("Your team sees you're away (not why), and your status shows 'On leave'.")
        note.setWordWrap(True)
        note.setStyleSheet(f"color: {T.MUTED};")
        self.lay.addWidget(note)
        form = QFormLayout()
        self.first = QDateEdit(_qdate(day))
        self.last = QDateEdit(_qdate(day))
        for d in (self.first, self.last):
            d.setCalendarPopup(True)
            d.setDisplayFormat("ddd dd MMM yyyy")
        self.first.dateChanged.connect(lambda q: self.last.setDate(q) if self.last.date() < q else None)
        self.reason = QLineEdit()
        self.reason.setPlaceholderText("Only you see this (optional)")
        form.addRow("From", self.first)
        form.addRow("To", self.last)
        form.addRow("Note", self.reason)
        self.lay.addLayout(form)
        bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        T.polish(bb.button(QDialogButtonBox.Save), primary=True)
        bb.accepted.connect(self._save)
        bb.rejected.connect(self.reject)
        self.lay.addWidget(bb)

    def _save(self):
        def done(reply):
            if reply.get("ok"):
                self.accept()
            else:
                QMessageBox.warning(self, "Leave", reply.get("error", "Not saved"))
        self.ctx.conn.request("cal_leave_add", done, first_day=_pydate(self.first.date()).isoformat(),
                              last_day=_pydate(self.last.date()).isoformat(), note=self.reason.text())


class HolidaysDialog(Dialog):
    """The studio's holiday list for a year: tick the days the studio is closed, add or change days, import."""

    def __init__(self, ctx, year=None):
        super().__init__(ctx, "Studio holidays", 640)
        self.ctx = ctx
        self.year = year or datetime.date.today().year
        top = QHBoxLayout()
        self.year_box = QSpinBox()
        self.year_box.setRange(2000, 2100)
        self.year_box.setValue(self.year)
        self.year_box.valueChanged.connect(self.load)
        top.addWidget(QLabel("Year"))
        top.addWidget(self.year_box)
        top.addStretch(1)
        add = QPushButton(" Add a day")
        add.setIcon(icon("plus", T.TEXT, 15))
        add.clicked.connect(lambda: self._edit(None))
        top.addWidget(add)
        imp = QPushButton(" Import .ics")
        imp.setIcon(icon("upload", T.TEXT, 15))
        imp.clicked.connect(self._import)
        top.addWidget(imp)
        self.lay.addLayout(top)
        hint = QLabel("Ticked days are holidays on everyone's calendar. Festival dates follow the lunar "
                      "calendar - the ones marked 'check' may be a day off from your almanac.")
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color: {T.MUTED}; font-size: 9pt;")
        self.lay.addWidget(hint)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Studio closed", "Date", "Holiday"])
        self.table.verticalHeader().hide()
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setShowGrid(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.verticalHeader().setDefaultSectionSize(36)
        self.table.setMinimumHeight(360)
        self.table.itemChanged.connect(self._ticked)
        self.table.doubleClicked.connect(lambda: self._edit(self._selected()))
        self.lay.addWidget(self.table, 1)
        row = QHBoxLayout()
        edit = QPushButton("Change...")
        edit.clicked.connect(lambda: self._edit(self._selected()))
        delete = QPushButton("Delete")
        T.polish(delete, danger=True)
        delete.clicked.connect(self._delete)
        more = QPushButton("Add India's list for a year...")
        more.clicked.connect(self._add_year)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        for b in (edit, delete, more):
            row.addWidget(b)
        row.addStretch(1)
        row.addWidget(close)
        self.lay.addLayout(row)
        self.rows = []
        self._loading = False
        self.load()

    def load(self, *_):
        self.year = self.year_box.value()

        def done(reply):
            if not reply.get("ok"):
                QMessageBox.warning(self, "Holidays", reply.get("error", "Not loaded"))
                return
            self.rows = reply["holidays"]
            self._loading = True
            self.table.setRowCount(len(self.rows))
            for r, h in enumerate(self.rows):
                tick = QTableWidgetItem()
                tick.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled | Qt.ItemIsSelectable)
                tick.setCheckState(Qt.Checked if h["observed"] else Qt.Unchecked)
                tick.setData(Qt.UserRole, h["id"])
                self.table.setItem(r, 0, tick)
                d = datetime.date.fromisoformat(h["day"])
                self.table.setItem(r, 1, QTableWidgetItem(f"{d:%a %d %b}"))
                name = QTableWidgetItem(h["name"] + ("   · check the date" if h["confirm"] else ""))
                if h["confirm"]:
                    name.setForeground(Qt.darkYellow)
                self.table.setItem(r, 2, name)
            self._loading = False
        self.ctx.conn.request("cal_holidays", done, year=self.year)

    def _selected(self):
        rows = self.table.selectionModel().selectedRows()
        return self.rows[rows[0].row()] if rows else None

    def _ticked(self, it):
        if self._loading or it.column() != 0:
            return
        self.ctx.conn.request("cal_holiday_observe", None, ids=[it.data(Qt.UserRole)],
                              observed=it.checkState() == Qt.Checked)

    def _edit(self, h):
        dlg = Dialog(self, "Change holiday" if h else "Add a holiday", 360)
        form = QFormLayout()
        day = QDateEdit(_qdate(datetime.date.fromisoformat(h["day"]) if h else datetime.date(self.year, 1, 1)))
        day.setCalendarPopup(True)
        day.setDisplayFormat("ddd dd MMM yyyy")
        name = QLineEdit(h["name"] if h else "")
        name.setPlaceholderText("e.g. Studio anniversary")
        form.addRow("Date", day)
        form.addRow("Name", name)
        dlg.lay.addLayout(form)
        bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        dlg.lay.addWidget(bb)
        if dlg.exec():
            self.ctx.conn.request("cal_holiday_save", lambda r: self.load() if r.get("ok") else
                                  QMessageBox.warning(self, "Holidays", r.get("error", "Not saved")),
                                  id=h["id"] if h else None, day=_pydate(day.date()).isoformat(),
                                  name=name.text(), observed=True)

    def _delete(self):
        h = self._selected()
        if h and QMessageBox.question(self, "Delete", rich_safe(f"Delete {h['name']} ({h['day']})?")) == QMessageBox.Yes:
            self.ctx.conn.request("cal_holiday_delete", lambda r: self.load(), id=h["id"])

    def _add_year(self):
        from PySide6.QtWidgets import QInputDialog
        year, ok = QInputDialog.getInt(self, "Add a year", "Add India's holiday list for the year:",
                                       max(self.year + 1, 2036), 2000, 2100)
        if ok:
            def done(r):
                if r.get("ok"):
                    QMessageBox.information(self, "Holidays", f"Added {r['added']} days for {year} - tick the ones "
                                                              "your studio keeps.")
                    self.year_box.setValue(year)
            self.ctx.conn.request("cal_holiday_add_year", done, year=year)

    def _import(self):
        import_ics(self.ctx, self, "holidays", after=self.load)


def import_ics(ctx, parent, target, after=None):
    """Pick an .ics file and add it as studio holidays or as my own events."""
    path, _ = QFileDialog.getOpenFileName(parent, "Import a calendar (.ics)", "", "Calendar files (*.ics)")
    if not path:
        return
    try:
        with open(path, encoding="utf-8-sig", errors="replace") as f:
            text = f.read()
    except OSError as e:
        QMessageBox.warning(parent, "Import", f"Could not read the file: {e}")
        return

    def done(reply):
        if reply.get("ok"):
            what = "holidays" if target == "holidays" else "events"
            QMessageBox.information(parent, "Import", f"Added {reply['added']} {what}.")
            if after:
                after()
        else:
            QMessageBox.warning(parent, "Import", reply.get("error", "Not imported"))
    ctx.conn.request("cal_import_ics", done, target=target, text=text)

