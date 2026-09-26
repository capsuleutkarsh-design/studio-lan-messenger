"""The Calendar page: month / week / day / agenda, layers you switch on and off, the chosen day on the right,
and a room's own calendar (opened from the room header)."""

import datetime

from PySide6.QtCore import QTimer, Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup, QFrame, QHBoxLayout, QLabel, QMenu, QPushButton, QScrollArea, QStackedWidget, QVBoxLayout, QWidget,
)

from common import theme as T
from common.icons import icon
from client.ui.calendar_dialogs import (
    EventDialog, HolidaysDialog, ItemDialog, LeaveDialog, can_lead, can_manage_holidays, import_ics,
)
from client.ui.calendar_views import (
    KINDS, AgendaView, MonthView, TimeGridView, by_day, entries_from, kind_color,
)
from client.ui.widgets import IconButton

VIEWS = ("month", "week", "day", "agenda")


def _chip(text, checked=True):
    b = QPushButton(text)
    b.setCheckable(True)
    b.setChecked(checked)
    b.setCursor(Qt.PointingHandCursor)
    T.polish(b, chip=True)
    return b


class CalendarPage(QWidget):
    changed = Signal()

    def __init__(self, ctx):
        super().__init__()
        self.ctx, self.store, self.config = ctx, ctx.store, ctx.config
        T.bg_pane(self)
        self.view = self.config.get("calendar_view") or "month"
        if self.view not in VIEWS:
            self.view = "month"
        self.anchor = datetime.date.today()           # the day the views are built around
        self.selected = datetime.date.today()
        self.room_id = None
        saved = self.config.get("calendar_layers")
        self.layers = set(saved) if isinstance(saved, list) and saved else set(KINDS) | {"anniversary"}
        self.data = None
        self._gen = 0
        self._morning = True              # week/day views start at 08:00 after a change of view or week
        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 18, 24, 16)
        outer.setSpacing(12)

        # toolbar: title, today / previous / next, the view, New
        bar = QHBoxLayout()
        bar.setSpacing(8)
        self.title = QLabel()
        self.title.setStyleSheet("font-size: 16pt; font-weight: 700;")
        bar.addWidget(self.title)
        bar.addSpacing(8)
        today = QPushButton("Today")
        today.clicked.connect(self.go_today)
        bar.addWidget(today)
        prev = IconButton("back", "Previous", 34, 16)
        prev.clicked.connect(lambda: self.step(-1))
        nxt = IconButton("next", "Next", 34, 16)
        nxt.clicked.connect(lambda: self.step(1))
        bar.addWidget(prev)
        bar.addWidget(nxt)
        bar.addStretch(1)
        self.view_group = QButtonGroup(self)
        for v in VIEWS:
            b = _chip(v.capitalize(), v == self.view)
            self.view_group.addButton(b)
            b.clicked.connect(lambda _=False, v=v: self.set_view(v))
            bar.addWidget(b)
        self.view_group.setExclusive(True)
        bar.addSpacing(10)
        self.new_btn = QPushButton(" New")
        self.new_btn.setIcon(icon("plus", T.ACCENT_TEXT, 16))
        T.polish(self.new_btn, primary=True)
        self.new_btn.clicked.connect(self.new_menu)
        bar.addWidget(self.new_btn)
        outer.addLayout(bar)

        # a room's own calendar
        self.room_bar = QFrame()
        self.room_bar.setObjectName("roombar")
        self.room_bar.setStyleSheet(f"#roombar {{ background: {T.ACCENT_SOFT}; border-radius: 12px; }}")
        rb = QHBoxLayout(self.room_bar)
        rb.setContentsMargins(14, 6, 8, 6)
        self.room_label = QLabel()
        self.room_label.setTextFormat(Qt.PlainText)
        self.room_label.setStyleSheet("font-weight: 600; background: transparent;")
        rb.addWidget(self.room_label, 1)
        back = QPushButton("Show my whole calendar")
        back.clicked.connect(lambda: self.set_room(None))
        rb.addWidget(back)
        self.room_bar.hide()
        outer.addWidget(self.room_bar)

        # layers
        self.layer_row = QHBoxLayout()
        self.layer_row.setSpacing(6)
        self.layer_buttons = {}
        for kind, (label, _c) in KINDS.items():
            b = _chip("●  " + label, kind in self.layers)
            b.setStyleSheet(f"QPushButton:checked {{ color: {kind_color(kind)}; }}")
            b.toggled.connect(lambda on, k=kind: self.toggle_layer(k, on))
            self.layer_row.addWidget(b)
            self.layer_buttons[kind] = b
        self.layer_row.addStretch(1)
        outer.addLayout(self.layer_row)

        # the views + the chosen day
        body = QHBoxLayout()
        body.setSpacing(16)
        self.stack = QStackedWidget()
        self.month = MonthView()
        self.week = TimeGridView()
        self.day = TimeGridView()
        self.agenda = AgendaView()
        for w in (self.month, self.week, self.day, self.agenda):
            self.stack.addWidget(w)
        self.month.day_clicked.connect(self.pick_day)
        self.month.day_activated.connect(lambda d: (self.pick_day(d), self.set_view("day")))
        for w in (self.month, self.week.grid, self.day.grid, self.agenda):
            w.entry_clicked.connect(self.open_entry)
        for w in (self.week.grid, self.day.grid):
            w.slot_activated.connect(lambda when: self.new_item("meeting", when))
        body.addWidget(self.stack, 1)
        self.side = QFrame()
        self.side.setObjectName("dayside")
        self.side.setFixedWidth(280)
        self.side.setStyleSheet(f"#dayside {{ background: {T.PANEL}; border-radius: 20px; }}")
        sl = QVBoxLayout(self.side)
        sl.setContentsMargins(16, 14, 16, 14)
        sl.setSpacing(8)
        self.side_title = QLabel()
        self.side_title.setStyleSheet("font-size: 11.5pt; font-weight: 700; background: transparent;")
        sl.addWidget(self.side_title)
        self.side_list = QVBoxLayout()
        self.side_list.setSpacing(6)
        holder = QWidget()
        holder.setStyleSheet("background: transparent;")
        holder.setLayout(self.side_list)
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setFrameShape(QScrollArea.NoFrame)
        area.setWidget(holder)
        area.setStyleSheet("QScrollArea { background: transparent; }")
        area.viewport().setStyleSheet("background: transparent;")
        sl.addWidget(area, 1)
        adds = QHBoxLayout()
        for kind, label in (("meeting", "Meeting"), ("note", "Note")):
            b = QPushButton(" " + label)
            b.setIcon(icon("plus", T.TEXT, 14))
            b.clicked.connect(lambda _=False, k=kind: self.new_item(k, self._at_nine(self.selected)))
            adds.addWidget(b)
        sl.addLayout(adds)
        body.addWidget(self.side)
        outer.addLayout(body, 1)

        self.store.calendar_changed.connect(self.refresh)
        self._refresh_timer = QTimer(self, singleShot=True, interval=150, timeout=self.fetch)
        self.set_view(self.view, fetch=False)

    # ------------------------------------------------------------ navigation
    def showEvent(self, e):
        super().showEvent(e)
        self.fetch()

    def go_today(self):
        self.anchor = self.selected = datetime.date.today()
        self.fetch()

    def go_to(self, day, view=None):
        self.anchor = self.selected = day
        if view:
            self.set_view(view, fetch=False)
        self.fetch()

    def set_room(self, room_id):
        self.room_id = room_id
        room = self.store.rooms.get(room_id) if room_id else None
        self.room_label.setText(f"# {room['name']}  ·  meetings, deadlines and notes of this room" if room else "")
        self.room_bar.setVisible(bool(room))
        self.fetch()

    def step(self, direction):
        self._morning = True
        if self.view == "month":
            m = self.anchor.month - 1 + direction
            self.anchor = datetime.date(self.anchor.year + m // 12, m % 12 + 1, 1)
        elif self.view == "week":
            self.anchor += datetime.timedelta(weeks=direction)
        elif self.view == "day":
            self.anchor += datetime.timedelta(days=direction)
            self.selected = self.anchor
        else:
            self.anchor += datetime.timedelta(days=30 * direction)
        self.fetch()

    def set_view(self, view, fetch=True):
        self._morning = self._morning or view != self.view
        self.view = view
        self.config["calendar_view"] = view
        self.config.save()
        for b in self.view_group.buttons():
            b.setChecked(b.text().lower() == view)
        self.stack.setCurrentIndex(VIEWS.index(view))
        if view == "day":
            self.anchor = self.selected
        if fetch:
            self.fetch()

    def pick_day(self, day):
        self.selected = day
        self._fill_side()

    def toggle_layer(self, kind, on):
        (self.layers.add if on else self.layers.discard)(kind)
        if kind == "birthday":
            (self.layers.add if on else self.layers.discard)("anniversary")
        self.config["calendar_layers"] = sorted(self.layers)
        self.config.save()
        self._show()

    # ------------------------------------------------------------ data
    def _range(self):
        a = self.anchor
        if self.view == "month":
            first = a.replace(day=1)
            start = first - datetime.timedelta(days=first.weekday())
            return start, start + datetime.timedelta(days=41)
        if self.view == "week":
            start = a - datetime.timedelta(days=a.weekday())
            return start, start + datetime.timedelta(days=6)
        if self.view == "day":
            return a, a
        return a, a + datetime.timedelta(days=59)

    def refresh(self, *_):
        if self.isVisible():
            self._refresh_timer.start()

    def fetch(self):
        if not self.ctx.conn.online:
            return
        first, last = self._range()
        # the side panel's day may be outside the view: fetch it too
        first, last = min(first, self.selected), max(last, self.selected)
        self._gen += 1
        gen = self._gen

        def done(reply):
            if gen != self._gen or not reply.get("ok"):
                return
            self.data = reply
            self._show()
        self.ctx.conn.request("cal_range", done, start=first.isoformat(), end=last.isoformat())
        self._title()

    def _title(self):
        a = self.anchor
        if self.view == "month":
            text = f"{a:%B %Y}"
        elif self.view == "week":
            s = a - datetime.timedelta(days=a.weekday())
            e = s + datetime.timedelta(days=6)
            text = f"{s:%d %b} – {e:%d %b %Y}" if s.month != e.month else f"{s:%d} – {e:%d %B %Y}"
        elif self.view == "day":
            text = f"{a:%A %d %B %Y}"
        else:
            text = f"From {a:%d %B %Y}"
        self.title.setText(text)

    def entries(self):
        return entries_from(self.data, self.layers | ({"anniversary"} if "birthday" in self.layers else set()),
                            self.store.my_id, self.room_id)

    def _holidays(self):
        return {datetime.date.fromisoformat(h["day"]) for h in (self.data or {}).get("holidays", [])}

    def _show(self):
        entries = self.entries()
        hol = self._holidays()
        first, _last = self._range()
        if self.view == "month":
            self.month.selected = self.selected
            self.month.set_data(self.anchor, entries, hol)
        elif self.view in ("week", "day"):
            view = self.week if self.view == "week" else self.day
            view.grid.set_data(first if self.view == "week" else self.anchor, 7 if self.view == "week" else 1,
                               entries, hol)
            if self._morning:
                self._morning = False
                QTimer.singleShot(0, view.scroll_to_morning)
        else:
            self.agenda.set_data(self.anchor, 60, entries)
        self._fill_side()
        self._title()

    def _fill_side(self):
        while self.side_list.count():
            it = self.side_list.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        day = self.selected
        today = datetime.date.today()
        prefix = "Today · " if day == today else ("Tomorrow · " if day == today + datetime.timedelta(days=1) else "")
        self.side_title.setText(f"{prefix}{day:%a %d %B}")
        items = by_day(self.entries()).get(day, [])
        if not items:
            empty = QLabel("Nothing on this day." + ("\nDouble-click a day to open it." if self.view == "month" else ""))
            empty.setStyleSheet(f"color: {T.MUTED}; background: transparent;")
            self.side_list.addWidget(empty)
        for e in items:
            b = QPushButton()
            b.setCursor(Qt.PointingHandCursor)
            when = "All day" if e.all_day else f"{e.start:%H:%M}"
            b.setText(f"{when}   {e.title}")
            b.setToolTip(e.title + (f"\n{e.sub}" if e.sub else ""))
            b.setStyleSheet(f"QPushButton {{ text-align: left; padding: 8px 10px; border: none; border-radius: 12px;"
                            f" background: {T.mix(kind_color(e.kind), T.PANEL, 0.14)}; font-weight: 500; }}"
                            f"QPushButton:hover {{ background: {T.mix(kind_color(e.kind), T.PANEL, 0.24)}; }}")
            b.clicked.connect(lambda _=False, e=e: self.open_entry(e))
            self.side_list.addWidget(b)
        self.side_list.addStretch(1)

    # ------------------------------------------------------------ actions
    @staticmethod
    def _at_nine(day):
        return datetime.datetime.combine(day, datetime.time(9))

    def new_menu(self):
        m = QMenu(self)
        when = self._at_nine(self.selected)
        m.addAction(icon("users", T.TEXT, 16), "Meeting...", lambda: self.new_item("meeting", when))
        m.addAction(icon("clock", T.TEXT, 16), "Personal event...", lambda: self.new_item("event", when))
        m.addAction(icon("edit", T.TEXT, 16), "Note of the day...", lambda: self.new_item("note", when))
        if can_lead(self.store):
            m.addAction(icon("zap", T.TEXT, 16), "Deadline...", lambda: self.new_item("deadline", when.replace(hour=18)))
        m.addAction(icon("logout", T.TEXT, 16), "Leave / out of office...", self.new_leave)
        m.addSeparator()
        m.addAction(icon("upload", T.TEXT, 16), "Import my calendar (.ics)...",
                    lambda: import_ics(self.ctx, self, "mine", after=self.fetch))
        if can_manage_holidays(self.store):
            m.addAction(icon("dashboard", T.TEXT, 16), "Studio holidays...", self.holidays)
        m.exec(self.new_btn.mapToGlobal(self.new_btn.rect().bottomLeft()))

    def new_item(self, kind, when=None):
        if EventDialog(self.ctx, kind, start=when, room_id=self.room_id).exec():
            self.fetch()

    def new_leave(self):
        if LeaveDialog(self.ctx, self.selected).exec():
            self.fetch()

    def holidays(self):
        HolidaysDialog(self.ctx, self.anchor.year).exec()
        self.fetch()

    def open_entry(self, entry):
        self.selected = entry.start.date()
        self._fill_side()
        if ItemDialog(self.ctx, entry).exec():
            self.fetch()
