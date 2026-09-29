"""Calendar windows: add or change an item, see its details (and answer a meeting), take leave, and the
studio holiday list (admins and HR)."""

import datetime

from PySide6.QtCore import QDate, Qt, QTime
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView, QButtonGroup, QCheckBox, QComboBox, QDateEdit, QDialogButtonBox, QFileDialog, QFormLayout,
    QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMessageBox, QPlainTextEdit, QPushButton, QSpinBox, QTableWidget,
    QTableWidgetItem, QTimeEdit, QVBoxLayout, QWidget,
)

from common import theme as T
from common.fmt import SEP, fmt_date, fmt_range, fmt_time, fmt_time_range
from common.icons import icon
from client.ui.calendar_views import kind_color
from client.ui.dialogs import Dialog, MemberPicker
from client.ui.widgets import rich_safe

KIND_TITLES = {"meeting": "Meeting", "event": "Personal event", "note": "Note of the day", "deadline": "Deadline"}
REPEATS = [("Does not repeat", None), ("Every day", "daily"), ("Every working day (Mon–Sat)", "workdays"),
           ("Every week on…", "weekly"), ("Every month", "monthly")]
DATE_FORMAT = "ddd d MMM yyyy"          # date boxes: 'Tue 6 Oct 2026' (no zero-padded day, like the rest)
NIGHT = 18                              # an item that starts from 18:00 on may end the next morning


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


def _hint(text=""):
    """A small muted line under a field."""
    lbl = QLabel(text)
    lbl.setWordWrap(True)
    lbl.setStyleSheet(f"color: {T.MUTED}; font-size: {T.pt(T.FONT_S)};")
    return lbl


def _ask(parent, title, text):
    """A yes/no question where No is the default, so a stray Enter doesn't do something that can't be undone."""
    return QMessageBox.question(parent, title, text, QMessageBox.Yes | QMessageBox.No, QMessageBox.No) \
        == QMessageBox.Yes


class EventDialog(Dialog):
    """Add or change a meeting, personal event, note of the day or deadline.

    mode 'new' / 'series' (the whole thing) / 'one' (just this occurrence of a repeating item)."""

    def __init__(self, ctx, kind, start=None, item=None, mode="new", room_id=None):
        super().__init__(ctx, ("New " if mode == "new" else "Edit ") + KIND_TITLES[kind].lower()
                         + (" (this one only)" if mode == "one" else ""), 520)
        self.ctx, self.store, self.kind, self.item, self.mode = ctx, ctx.store, kind, item, mode
        self.deadline = kind == "deadline"           # a moment: 'Due' at one time, no end time
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
        self.date.setDisplayFormat(DATE_FORMAT)
        self.date.setMinimumWidth(170)
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
        form.addRow("Due" if self.deadline else "When", when)
        # an end before the start is only right at night (22:00 - 02:00): say so under the times
        self.when_hint = _hint()
        form.addRow("", self.when_hint)
        form.setRowVisible(self.when_hint, False)
        self._form = form
        self._start_was = self.t_start.time()
        self.t_start.timeChanged.connect(self._start_moved)
        self.t_end.timeChanged.connect(self._check_times)
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
        self.until.setDisplayFormat("d MMM yyyy")
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
        self.scope_hint = None
        if kind in ("meeting", "note", "deadline") and mode != "one":
            self._fill_scope(kind, item, room_id)
            form.addRow({"meeting": "Invite", "note": "Who sees it", "deadline": "Who it's for"}[kind], self.scope)
            if kind == "meeting":
                invited = [p["id"] for p in (item or {}).get("people", [])]
                self.picker = MemberPicker(self.store, checked=invited)
                self.picker.list.setMinimumHeight(170)
                # the same fill as the other fields (the list is white on the light theme otherwise)
                self.picker.list.setStyleSheet(f"QListWidget {{ background: {T.SURFACE}; }}")
                self.picker.list.itemChanged.connect(self._scope_changed)
                form.addRow("", self.picker)
            self.scope_hint = _hint()
            form.addRow("", self.scope_hint)
            self.scope.currentIndexChanged.connect(self._scope_changed)
            self._scope_changed()
        if kind in ("meeting", "event") and mode != "one":
            self.location = QLineEdit((item or {}).get("location", ""))
            self.location.setPlaceholderText("Room, link or desk (optional)")
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
        self.save_btn = bb.button(QDialogButtonBox.Save)
        T.polish(self.save_btn, primary=True)
        bb.accepted.connect(self._save)
        bb.rejected.connect(self.reject)
        self.lay.addWidget(bb)
        # Save needs a title (the server would only answer "Give it a title")
        self.title.textChanged.connect(lambda text: self.save_btn.setEnabled(bool(text.strip())))
        self.save_btn.setEnabled(bool(self.title.text().strip()))
        self._check_times()
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
            if studio_wide(self.store):
                options.append(("The whole studio", ("studio", "")))
            if can_lead(self.store):
                if me.get("department"):
                    options.append((f"My department{SEP}{me['department']}", ("dept", me["department"])))
                for r in sorted(self.store.rooms.values(), key=lambda r: r["name"].lower()):
                    options.append((f"# {r['name']}", ("room", str(r["id"]))))
        else:
            for r in sorted(self.store.rooms.values(), key=lambda r: r["name"].lower()):
                options.append((f"# {r['name']}", ("room", str(r["id"]))))
            if me.get("department"):
                options.append((f"My department{SEP}{me['department']}", ("dept", me["department"])))
            if studio_wide(self.store):
                options.append(("The whole studio", ("studio", "")))
        for label, data in options:
            self.scope.addItem(label, data)
        want = (item["scope"], str(item["scope_ref"])) if item else (("room", str(room_id)) if room_id else None)
        if want:
            for i in range(self.scope.count()):
                if self.scope.itemData(i) == want:
                    self.scope.setCurrentIndex(i)

    def _scope_changed(self, *_):
        """Show the people list only for 'People I choose', and say under it who will see the item."""
        scope, ref = self.scope.currentData() or ("private", "")
        if self.picker is not None:
            self._form.setRowVisible(self.picker, scope == "people")
        if self.scope_hint is None:
            return
        room = self.store.rooms.get(int(ref)) if scope == "room" and str(ref).isdigit() else None
        where = f"# {room['name']}" if room else "the room"
        if scope == "people":
            n = len(self.picker.selected()) if self.picker is not None else 0
            text = "Nobody invited yet – tick the people above." if not n else \
                f"{n} {'person' if n == 1 else 'people'} invited."
        elif scope == "room":
            text = f"Everyone in {where} is invited." if self.kind == "meeting" else \
                f"Shows on the calendar of everyone in {where}."
        elif scope == "dept":
            text = f"Shows on the calendar of everyone in {ref}."
        elif scope == "studio":
            text = "Shows on everyone's calendar."
        else:
            text = "Only you see it."
        self.scope_hint.setText(text)

    def _all_day(self, on):
        self.t_start.setVisible(not on)
        for w in (self.t_end, self.dash):
            w.setVisible(not on and not self.deadline)
        self._check_times()

    def _start_moved(self, new):
        """Moving the start moves the end with it, so the length stays (10:00–10:30 → 14:00–14:30)."""
        old, self._start_was = self._start_was, new
        self.t_end.setTime(self.t_end.time().addSecs(old.secsTo(new)))
        self._check_times()

    def _overnight(self):
        """True when the times say it ends the next day (the end is not after the start)."""
        return not self.deadline and not self.all_day.isChecked() and self.t_end.time() <= self.t_start.time()

    def _check_times(self, *_):
        if not hasattr(self, "_form"):
            return
        over = self._overnight()
        if over and self.t_start.time().hour() >= NIGHT:
            self.when_hint.setText(f"Ends the next day at {self.t_end.time().toString('HH:mm')}.")
            self.when_hint.setStyleSheet(f"color: {T.MUTED}; font-size: {T.pt(T.FONT_S)};")
        elif over:
            self.when_hint.setText("Ends before it starts – check the end time.")
            self.when_hint.setStyleSheet(f"color: {T.DANGER}; font-size: {T.pt(T.FONT_S)};")
        self._form.setRowVisible(self.when_hint, over)

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
        if self.deadline:
            return s, s + datetime.timedelta(minutes=15)      # a moment; the server wants an end after it
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
        if self._overnight() and self.t_start.time().hour() < NIGHT and not _ask(
                self, KIND_TITLES[self.kind], "It ends before it starts. Save it as ending the next day?"):
            return
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


def when_text(entry):
    """The date and time of an entry for people: 'Tuesday 29 September, 16:00 – 16:30', 'Due Friday 2 October,
    18:00', '28 Sep – 2 Oct' (all-day, several days)."""
    s, end = entry.start, entry.end
    if entry.all_day:
        last = (end - datetime.timedelta(seconds=1)).date()
        return fmt_range(s, last, weekday=True) if last != s.date() else fmt_date(s, long=True)
    if entry.kind == "deadline":
        return f"Due {fmt_date(s, long=True)}, {fmt_time(s)}"
    if (end - datetime.timedelta(seconds=1)).date() != s.date():         # ends another day
        return f"{fmt_date(s, long=True)}, {fmt_time(s)} – {fmt_date(end)}, {fmt_time(end)}"
    return f"{fmt_date(s, long=True)}, {fmt_time_range(s, end)}"


class ItemDialog(Dialog):
    """Details of one calendar entry, with the actions that fit (answer, open the room, change, delete)."""

    def __init__(self, ctx, entry):
        super().__init__(ctx, "Calendar", 460)
        self.ctx, self.entry = ctx, entry
        item = entry.item or {}
        store = ctx.store
        head = QHBoxLayout()
        head.setSpacing(10)
        dot = QLabel()
        dot.setFixedSize(12, 12)
        dot.setStyleSheet(f"background: {kind_color(entry.kind)}; border-radius: 6px;")
        dot_box = QVBoxLayout()                      # level with the title's first line, also when it wraps
        dot_box.setContentsMargins(0, 7, 0, 0)
        dot_box.addWidget(dot)
        dot_box.addStretch(1)
        head.addLayout(dot_box)
        title = QLabel(getattr(entry, "name", entry.title))
        title.setTextFormat(Qt.PlainText)
        title.setWordWrap(True)
        title.setStyleSheet(f"font-size: {T.pt(T.FONT_XL)}; font-weight: 700;")
        head.addWidget(title, 1)
        self.lay.addLayout(head)
        # the date and time: the main fact, in the text colour
        self.when = QLabel(when_text(entry))
        self.when.setTextFormat(Qt.PlainText)
        self.when.setWordWrap(True)
        self.when.setStyleSheet(f"font-size: {T.pt(T.FONT_M)}; font-weight: 600;")
        details = QVBoxLayout()
        details.setSpacing(4)
        details.setContentsMargins(22, 0, 0, 0)       # under the title, not under the dot
        details.addWidget(self.when)
        lines = []
        if item.get("rule_text"):
            rt = item["rule_text"]
            lines.append("Repeats " + rt[:1].lower() + rt[1:])
        kind_text = {"meeting": "Meeting", "event": "Personal event", "note": "Note of the day",
                     "deadline": "Deadline", "holiday": "Studio holiday", "leave": "Leave",
                     "birthday": "Birthday", "anniversary": "Work anniversary"}[entry.kind]
        scope = item.get("scope")
        if scope == "room":
            room = store.rooms.get(int(item["scope_ref"]))
            kind_text += f"{SEP}# {room['name'] if room else 'a room'}"
        elif scope == "dept":
            kind_text += f"{SEP}{item['scope_ref']} department"
        elif scope == "studio":
            kind_text += f"{SEP}whole studio"
        elif scope == "private":
            kind_text += f"{SEP}only you"
        lines.append(kind_text)
        if item.get("owner_name") and item.get("owner_id") != store.my_id:
            lines.append(f"By {item['owner_name']}")
        if item.get("location"):
            lines.append(f"Where: {item['location']}")
        if entry.kind == "leave" and item.get("note") and item.get("mine"):
            lines.append(f"Reason: {item['note']}")
        if entry.kind == "holiday" and item.get("confirm"):
            lines.append("The date follows the lunar calendar – check it with your almanac.")
        info = QLabel("\n".join(lines))
        info.setTextFormat(Qt.PlainText)
        info.setWordWrap(True)
        info.setStyleSheet(f"color: {T.MUTED};")
        details.addWidget(info)
        if entry.kind == "meeting" and item.get("people"):
            marks = {"yes": "✓ ", "maybe": "? ", "no": "✕ "}
            people = ", ".join(f"{marks.get(p['rsvp'], '')}{p['name']}" for p in item["people"])
            who = QLabel(f"Invited: {people}")
            who.setTextFormat(Qt.PlainText)
            who.setWordWrap(True)
            who.setStyleSheet(f"color: {T.MUTED}; font-size: {T.pt(T.FONT_S)};")
            details.addSpacing(2)
            details.addWidget(who)
        self.lay.addLayout(details)
        if item.get("notes"):
            notes = QLabel(item["notes"])
            notes.setTextFormat(Qt.PlainText)
            notes.setWordWrap(True)
            notes.setStyleSheet(f"background: {T.SURFACE}; border-radius: 12px; padding: 10px;")
            self.lay.addWidget(notes)
        # my answer to a meeting: chips on their own row, the current one ticked
        self.rsvp_buttons = {}
        if entry.kind == "meeting" and item.get("owner_id") != store.my_id:
            answers = QHBoxLayout()
            answers.setSpacing(6)
            lab = QLabel("Your answer")
            lab.setStyleSheet(f"color: {T.MUTED};")
            answers.addWidget(lab)
            answers.addSpacing(6)
            self.rsvp_group = QButtonGroup(self)
            self.rsvp_group.setExclusive(True)
            mine = item.get("my_rsvp")
            for answer, label in (("yes", "Going"), ("maybe", "Maybe"), ("no", "Can't go")):
                b = QPushButton(("✓ " if mine == answer else "") + label)
                b.setCheckable(True)
                b.setChecked(mine == answer)
                b.setAutoDefault(False)
                b.setCursor(Qt.PointingHandCursor)
                T.polish(b, chip=True, tall=True)
                b.clicked.connect(lambda _=False, a=answer: self._rsvp(a))
                self.rsvp_group.addButton(b)
                self.rsvp_buttons[answer] = b
                answers.addWidget(b)
            answers.addStretch(1)
            self.lay.addLayout(answers)
        # what can be done with it, then Close on the right
        row = QHBoxLayout()
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
            wish.setIcon(icon("chat", T.TEXT, 15))
            wish.clicked.connect(lambda: self._wish(item["user_id"], f"Happy work anniversary – "
                                                                    f"{item['years']} years! 🎉"))
            row.addWidget(wish)
        if entry.kind == "leave" and item.get("mine"):
            rm = QPushButton("Cancel this leave")
            T.polish(rm, danger=True)
            rm.clicked.connect(self._cancel_leave)
            row.addWidget(rm)
        if item.get("can_edit"):
            edit = QPushButton(" Edit")
            edit.setIcon(icon("edit", T.TEXT, 15))
            edit.clicked.connect(self._edit)
            row.addWidget(edit)
            delete = QPushButton(" Delete")
            delete.setIcon(icon("trash", T.DANGER, 15))
            T.polish(delete, danger=True)
            delete.clicked.connect(self._delete)
            row.addWidget(delete)
        row.addStretch(1)
        close = QPushButton("Close")
        close.setDefault(True)
        close.clicked.connect(self.reject)
        row.addWidget(close)
        for i in range(row.count()):
            w = row.itemAt(i).widget()
            if isinstance(w, QPushButton) and w is not close:
                w.setAutoDefault(False)
        self.lay.addLayout(row)

    def _reply(self, reply):
        if reply.get("ok"):
            self.accept()
        else:
            QMessageBox.warning(self, "Calendar", reply.get("error", "Didn't work"))

    def _rsvp(self, answer):
        for a, b in self.rsvp_buttons.items():
            b.setText(("✓ " if a == answer else "") + b.text().removeprefix("✓ "))
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
        cancel = box.addButton("Cancel", QMessageBox.RejectRole)
        box.setDefaultButton(cancel if action == "Delete" else one)
        box.exec()
        return "one" if box.clickedButton() is one else "series" if box.clickedButton() is allb else None

    def _edit(self):
        mode = self._which("Edit")
        if mode:
            self.accept()
            EventDialog(self.ctx, self.entry.kind, item=self.entry.item, mode=mode).exec()

    def _delete(self):
        mode = self._which("Delete")
        if not mode:
            return
        item = self.entry.item
        if mode == "series" and not _ask(self, "Delete", rich_safe(f"Delete “{item['title']}”?")):
            return
        kw = {"event_id": item["event_id"]}
        if mode == "one":
            kw["occ"] = item["occ"]
        self.ctx.conn.request("cal_delete", self._reply, **kw)


class LeaveDialog(Dialog):
    def __init__(self, ctx, day=None):
        super().__init__(ctx, "Leave / out of office", 460)
        self.ctx = ctx
        day = day or datetime.date.today()
        note = QLabel("Your team sees you're away (not why). Your status shows “On leave”.")
        note.setWordWrap(True)
        note.setStyleSheet(f"color: {T.MUTED};")
        self.lay.addWidget(note)
        form = QFormLayout()
        form.setSpacing(10)
        self.first = QDateEdit(_qdate(day))
        self.last = QDateEdit(_qdate(day))
        for d in (self.first, self.last):
            d.setCalendarPopup(True)
            d.setDisplayFormat(DATE_FORMAT)
        self.first.dateChanged.connect(lambda q: self.last.setDate(q) if self.last.date() < q else None)
        self.reason = QLineEdit()
        self.reason.setPlaceholderText("Only you see this (optional)")
        form.addRow("First day", self.first)
        form.addRow("Last day", self.last)
        form.addRow("Reason", self.reason)
        # an automatic answer to direct messages, once a day per person
        self.auto = QCheckBox("Answer direct messages for me (once a day per person)")
        self.auto.setChecked(True)
        self.auto_text = QPlainTextEdit()
        self.auto_text.setFixedHeight(64)
        form.addRow("Auto-reply", self.auto)
        form.addRow("", self.auto_text)
        self.lay.addLayout(form)
        self.auto.toggled.connect(self.auto_text.setEnabled)
        self._auto_default = ""
        self.last.dateChanged.connect(self._fill_auto)
        self._fill_auto()
        bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        T.polish(bb.button(QDialogButtonBox.Save), primary=True)
        bb.accepted.connect(self._save)
        bb.rejected.connect(self.reject)
        self.lay.addWidget(bb)
        self.reason.setFocus()                   # not the date box (its weekday would open selected)

    def _save(self):
        def done(reply):
            if reply.get("ok"):
                self.accept()
            else:
                QMessageBox.warning(self, "Leave", reply.get("error", "Not saved"))
        auto = self.auto_text.toPlainText().strip() if self.auto.isChecked() else ""
        self.ctx.conn.request("cal_leave_add", done, first_day=_pydate(self.first.date()).isoformat(),
                              last_day=_pydate(self.last.date()).isoformat(), note=self.reason.text(),
                              auto_reply=auto)

    def _fill_auto(self, *_):
        """'I'm on leave and back on Mon 5 Oct…' - follows the last day until the person writes their own."""
        back = _pydate(self.last.date()) + datetime.timedelta(days=1)
        while back.weekday() == 6:                 # the studio works Monday to Saturday
            back += datetime.timedelta(days=1)
        text = f"I'm on leave and back on {fmt_date(back)}. I'll reply then."
        if self.auto_text.toPlainText() in ("", self._auto_default):
            self.auto_text.setPlainText(text)
        self._auto_default = text


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
        add.setIcon(icon("plus", T.ACCENT_TEXT, 15))
        T.polish(add, primary=True)
        add.clicked.connect(lambda: self._edit(None))
        imp = QPushButton(" Import .ics")
        imp.setIcon(icon("upload", T.TEXT, 15))
        imp.clicked.connect(self._import)
        more = QPushButton(" Add India's list…")
        more.setIcon(icon("calendar", T.TEXT, 15))
        more.setToolTip("Add India's public holidays and festivals for a year")
        more.clicked.connect(self._add_year)
        for b in (add, imp, more):
            b.setAutoDefault(False)
            top.addWidget(b)
        self.lay.addLayout(top)
        hint = QLabel("Ticked days are holidays on everyone's calendar. Festival dates follow the lunar "
                      "calendar – the ones marked “Check the date” may be a day off from your almanac.")
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color: {T.MUTED}; font-size: {T.pt(T.FONT_S)};")
        self.lay.addWidget(hint)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Studio closed", "Date", "Holiday", "Note"])
        self.table.horizontalHeader().setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.table.verticalHeader().hide()
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setShowGrid(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.table.verticalHeader().setDefaultSectionSize(36)
        self.table.setMinimumHeight(360)
        self.table.itemChanged.connect(self._ticked)
        self.table.doubleClicked.connect(lambda: self._edit(self._selected()))
        self.lay.addWidget(self.table, 1)
        row = QHBoxLayout()
        self.edit_btn = QPushButton(" Edit")
        self.edit_btn.setIcon(icon("edit", T.TEXT, 15))
        self.edit_btn.clicked.connect(lambda: self._edit(self._selected()))
        self.delete_btn = QPushButton(" Delete")
        self.delete_btn.setIcon(icon("trash", T.DANGER, 15))
        T.polish(self.delete_btn, danger=True)
        self.delete_btn.setStyleSheet(f"QPushButton:disabled {{ color: {T.FAINT}; }}")   # red only when it works
        self.delete_btn.clicked.connect(self._delete)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        for b in (self.edit_btn, self.delete_btn):
            b.setAutoDefault(False)
            b.setEnabled(False)                  # until a day is picked
            row.addWidget(b)
        row.addStretch(1)
        row.addWidget(close)
        self.lay.addLayout(row)
        self.table.itemSelectionChanged.connect(self._selection_changed)
        self.rows = []
        self._loading = False
        self.load()
        self.table.setFocus()                    # not the Year box

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
                self.table.setItem(r, 1, QTableWidgetItem(fmt_date(d, year=False)))
                self.table.setItem(r, 2, QTableWidgetItem(h["name"]))
                note = QTableWidgetItem("Check the date" if h["confirm"] else "")
                if h["confirm"]:
                    note.setForeground(QColor(T.readable_on(T.WARN_TEXT, [T.PANEL, T.SURFACE])))
                    note.setToolTip("A festival: its date follows the lunar calendar")
                self.table.setItem(r, 3, note)
            self._loading = False
            self._selection_changed()
        self.ctx.conn.request("cal_holidays", done, year=self.year)

    def _selected(self):
        rows = self.table.selectionModel().selectedRows()
        return self.rows[rows[0].row()] if rows and rows[0].row() < len(self.rows) else None

    def _selection_changed(self):
        picked = self._selected() is not None
        self.edit_btn.setEnabled(picked)
        self.delete_btn.setEnabled(picked)

    def _ticked(self, it):
        if self._loading or it.column() != 0:
            return
        self.ctx.conn.request("cal_holiday_observe", None, ids=[it.data(Qt.UserRole)],
                              observed=it.checkState() == Qt.Checked)

    def _edit(self, h):
        dlg = Dialog(self, "Edit holiday" if h else "Add a holiday", 360)
        form = QFormLayout()
        day = QDateEdit(_qdate(datetime.date.fromisoformat(h["day"]) if h else datetime.date(self.year, 1, 1)))
        day.setCalendarPopup(True)
        day.setDisplayFormat(DATE_FORMAT)
        name = QLineEdit(h["name"] if h else "")
        name.setPlaceholderText("e.g. Studio anniversary")
        form.addRow("Date", day)
        form.addRow("Name", name)
        dlg.lay.addLayout(form)
        bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        save = bb.button(QDialogButtonBox.Save)
        T.polish(save, primary=True)
        name.textChanged.connect(lambda t: save.setEnabled(bool(t.strip())))
        save.setEnabled(bool(name.text().strip()))
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        dlg.lay.addWidget(bb)
        name.setFocus()
        if dlg.exec():
            self.ctx.conn.request("cal_holiday_save", lambda r: self.load() if r.get("ok") else
                                  QMessageBox.warning(self, "Holidays", r.get("error", "Not saved")),
                                  id=h["id"] if h else None, day=_pydate(day.date()).isoformat(),
                                  name=name.text(), observed=True)

    def _delete(self):
        h = self._selected()
        if h and _ask(self, "Delete", rich_safe(f"Delete {h['name']} "
                                                f"({fmt_date(datetime.date.fromisoformat(h['day']), year=True)})?")):
            self.ctx.conn.request("cal_holiday_delete", lambda r: self.load(), id=h["id"])

    def _add_year(self):
        from PySide6.QtWidgets import QInputDialog
        year, ok = QInputDialog.getInt(self, "Add a year", "Add India's holiday list for the year:",
                                       max(self.year + 1, 2036), 2000, 2100)
        if ok:
            def done(r):
                if r.get("ok"):
                    QMessageBox.information(self, "Holidays", f"Added {r['added']} days for {year} – tick the ones "
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
