"""The Calendar page: month / week / day / agenda, layers you switch on and off, the chosen day on the right,
and a room's own calendar (opened from the room header)."""

import datetime

from PySide6.QtCore import QPoint, QRect, QRectF, QSize, QTimer, Qt, Signal
from PySide6.QtGui import QColor, QFontMetrics, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QButtonGroup, QFrame, QHBoxLayout, QLabel, QLayout, QMenu, QPushButton, QScrollArea, QSizePolicy,
    QStackedWidget, QVBoxLayout, QWidget,
)

from common import theme as T
from common.fmt import SEP, day_word, fmt_date, fmt_range, fmt_time
from common.icons import icon
from client.ui.calendar_dialogs import (
    EventDialog, HolidaysDialog, ItemDialog, LeaveDialog, can_lead, can_manage_holidays, import_ics,
)
from client.ui.calendar_views import (
    KIND_ICONS, KINDS, AgendaView, MonthView, TimeGridView, by_day, entries_from, entry_tip, kind_color,
    kind_icon_color, kind_text_color,
)
from client.ui.widgets import ElidedLabel, IconButton, popup_pos, ui_font

VIEWS = ("month", "week", "day", "agenda")
NARROW = 860          # a narrower page folds the chosen-day panel away (the views show the day)
COMPACT = 760         # a narrower page gets short titles ('Sep 2026') and an icon-only New button


def _chip(text, checked=True, checkable=True, tall=False):
    b = QPushButton(text)
    b.setCheckable(checkable)
    if checkable:
        b.setChecked(checked)
    b.setCursor(Qt.PointingHandCursor)
    b.setAutoDefault(False)
    T.polish(b, chip=True, tall=tall)
    return b


def _dot_icon(on, off):
    """A round colour swatch for a layer chip: the kind's colour when the layer is on, a faint one when off."""
    ic = QIcon()
    for colour, state in ((off, QIcon.Off), (on, QIcon.On)):
        pm = QPixmap(20, 20)
        pm.setDevicePixelRatio(2)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(colour))
        p.drawEllipse(QRectF(1.5, 1.5, 7, 7))
        p.end()
        ic.addPixmap(pm, QIcon.Normal, state)
        ic.addPixmap(pm, QIcon.Active, state)
    return ic


class _Flow(QLayout):
    """Widgets in a row that wraps onto the next line when the page is narrow (the layer chips)."""

    def __init__(self, parent=None, spacing=6):
        super().__init__(parent)
        self._items, self._space = [], spacing
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item):
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, i):
        return self._items[i] if 0 <= i < len(self._items) else None

    def takeAt(self, i):
        return self._items.pop(i) if 0 <= i < len(self._items) else None

    def expandingDirections(self):
        return Qt.Orientation(0)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._place(QRect(0, 0, width, 0), move=False)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._place(rect, move=True)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        size = QSize()
        for it in self._items:
            size = size.expandedTo(it.minimumSize())
        return size

    def _place(self, rect, move):
        x, y, line = rect.x(), rect.y(), 0
        for it in self._items:
            hint = it.sizeHint()
            if line and x + hint.width() > rect.right() + 1:
                x, y, line = rect.x(), y + line + self._space, 0
            if move:
                it.setGeometry(QRect(QPoint(x, y), hint))
            x += hint.width() + self._space
            line = max(line, hint.height())
        return y + line - rect.y()


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
        self._narrow = self._compact = False
        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 18, 24, 16)
        outer.setSpacing(12)

        # toolbar: today / previous / next first (they never move), the title, the view, New
        bar = QHBoxLayout()
        bar.setSpacing(8)
        today = _chip("Today", checkable=False, tall=True)
        today.clicked.connect(self.go_today)
        bar.addWidget(today)
        prev = IconButton("back", "Previous", 34, 16)
        prev.clicked.connect(lambda: self.step(-1))
        nxt = IconButton("next", "Next", 34, 16)
        nxt.clicked.connect(lambda: self.step(1))
        bar.addWidget(prev)
        bar.addWidget(nxt)
        bar.addSpacing(6)
        self.title = ElidedLabel()
        self.title.setStyleSheet("font-size: 16pt; font-weight: 700;")
        bar.addWidget(self.title, 1)
        self.view_group = QButtonGroup(self)
        for v in VIEWS:
            b = _chip(v.capitalize(), v == self.view, tall=True)
            self.view_group.addButton(b)
            b.clicked.connect(lambda _=False, v=v: self.set_view(v))
            bar.addWidget(b)
        self.view_group.setExclusive(True)
        # a narrow page: one chip that opens the four views in a menu ('Month ▾')
        self.view_btn = _chip("", checkable=False, tall=True)
        self.view_btn.setToolTip("Month, week, day or agenda")
        self.view_btn.clicked.connect(self._view_menu)
        self.view_btn.hide()
        bar.addWidget(self.view_btn)
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
        self.room_label = ElidedLabel()
        self.room_label.setStyleSheet("font-weight: 600; background: transparent;")
        rb.addWidget(self.room_label, 1)
        back = QPushButton("Show my whole calendar")
        back.clicked.connect(lambda: self.set_room(None))
        rb.addWidget(back)
        self.room_bar.hide()
        outer.addWidget(self.room_bar)

        # layers: they wrap onto a second line on a narrow window instead of being cut
        layer_box = QWidget()
        self.layer_row = _Flow(layer_box, spacing=6)
        self.layer_buttons = {}
        for kind, (label, _c) in KINDS.items():
            b = _chip(label, kind in self.layers)
            b.setIcon(_dot_icon(kind_icon_color(kind), T.FAINT))
            b.setIconSize(QSize(10, 10))
            b.setToolTip(f"Show or hide {label.lower()}" + (" and work anniversaries" if kind == "birthday" else ""))
            b.setStyleSheet(f"QPushButton:checked {{ color: {kind_text_color(kind)}; }}")
            b.toggled.connect(lambda on, k=kind: self.toggle_layer(k, on))
            self.layer_row.addWidget(b)
            self.layer_buttons[kind] = b
        outer.addWidget(layer_box)

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
        self.month.day_activated.connect(self.open_day)
        self.week.grid.day_clicked.connect(self.open_day)
        for w in (self.month, self.week.grid, self.day.grid, self.agenda):
            w.entry_clicked.connect(self.open_entry)
        for w in (self.week.grid, self.day.grid):
            w.slot_activated.connect(lambda when: self.new_item("meeting", when))
        body.addWidget(self.stack, 1)
        self.side = QFrame()
        self.side.setObjectName("dayside")
        self.side.setFixedWidth(280)
        self.side.setStyleSheet(f"#dayside {{ background: {T.PANEL}; border-radius: 18px; }}")
        sl = QVBoxLayout(self.side)
        sl.setContentsMargins(16, 14, 16, 14)
        sl.setSpacing(8)
        self.side_title = ElidedLabel()
        self.side_title.setStyleSheet("font-size: 11.5pt; font-weight: 700; background: transparent;")
        sl.addWidget(self.side_title)
        self.side_list = QVBoxLayout()
        self.side_list.setContentsMargins(0, 0, 0, 0)       # rows line up with the title and the buttons
        self.side_list.setSpacing(6)
        holder = QWidget()
        holder.setStyleSheet("background: transparent;")
        holder.setLayout(self.side_list)
        self.side_area = area = QScrollArea()
        area.setWidgetResizable(True)
        area.setFrameShape(QScrollArea.NoFrame)
        area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        area.setWidget(holder)
        area.setStyleSheet("QScrollArea { background: transparent; }")
        area.viewport().setStyleSheet("background: transparent;")
        sl.addWidget(area, 1)
        adds = QHBoxLayout()
        for kind, label in (("meeting", "Meeting"), ("note", "Note")):
            b = QPushButton(" " + label)
            b.setIcon(icon("plus", T.TEXT, 14))
            b.setToolTip(f"New {label.lower()} on the chosen day")
            b.clicked.connect(lambda _=False, k=kind: self.new_item(k, self._default_start(self.selected)))
            adds.addWidget(b)
        sl.addLayout(adds)
        body.addWidget(self.side)
        outer.addLayout(body, 1)

        self.store.calendar_changed.connect(self.refresh)
        self._refresh_timer = QTimer(self, singleShot=True, interval=150, timeout=self.fetch)
        self.set_view(self.view, fetch=False)

    # ------------------------------------------------------------ small windows
    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._fit()

    def _fit(self):
        """Fold the day panel away on a narrow page (and in Agenda, which lists the days already); shorten
        the title and the New button on a very narrow one."""
        w = self.width()
        self._narrow = w < NARROW
        self.side.setVisible(not self._narrow and self.view != "agenda")
        compact = w < COMPACT
        if compact != self._compact:
            self._compact = compact
            for b in self.view_group.buttons():
                b.setVisible(not compact)
            self.view_btn.setVisible(compact)
            self.new_btn.setText("" if compact else " New")
            self.new_btn.setToolTip("New meeting, event, note or leave" if compact else "")
            self._title()

    # ------------------------------------------------------------ navigation
    def showEvent(self, e):
        super().showEvent(e)
        self._fit()
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
        self.room_label.setText(f"# {room['name']}{SEP}meetings, deadlines and notes of this room" if room else "")
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
        self.view_btn.setText(f"{view.capitalize()}  ▾")
        self.stack.setCurrentIndex(VIEWS.index(view))
        if view == "day":
            self.anchor = self.selected
        self._fit()
        if fetch:
            self.fetch()

    def _view_menu(self):
        m = QMenu(self)
        for v in VIEWS:
            a = m.addAction(v.capitalize(), lambda v=v: self.set_view(v))
            a.setCheckable(True)
            a.setChecked(v == self.view)
        m.exec(popup_pos(self.view_btn, m.sizeHint()))

    def pick_day(self, day):
        self.selected = day
        self._fill_side()

    def open_day(self, day):
        """A day in Day view (double-click in the month, '+N more', a day's name in the week)."""
        self.pick_day(day)
        self.set_view("day")

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
        """'September 2026', '28 Sep – 4 Oct 2026', 'Tuesday 29 September 2026', 'From 29 September 2026';
        on a narrow page 'Sep 2026', '28 Sep – 4 Oct', 'Tue 29 Sep', 'From 29 Sep'."""
        a, short = self.anchor, self._compact
        if self.view == "month":
            text = f"{a:%b %Y}" if short else f"{a:%B %Y}"
        elif self.view == "week":
            s = a - datetime.timedelta(days=a.weekday())
            text = fmt_range(s, s + datetime.timedelta(days=6), year=None if short else True)
        elif self.view == "day":
            text = fmt_date(a) if short else fmt_date(a, long=True, year=True)
        else:
            text = "From " + (fmt_date(a, weekday=False) if short else fmt_date(a, weekday=False, long=True, year=True))
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
        word = day_word(day)
        self.side_title.setText(f"{word}{SEP}{fmt_date(day)}" if word else fmt_date(day, long=True))
        items = by_day(self.entries()).get(day, [])
        if not items:
            empty = QLabel("Nothing on this day." + ("\nDouble-click a day to open it." if self.view == "month" else ""))
            empty.setStyleSheet(f"color: {T.MUTED}; background: transparent;")
            self.side_list.addWidget(empty)
        for e in items:
            self.side_list.addWidget(self._side_row(e))
        self.side_list.addStretch(1)

    def _side_row(self, e):
        """One item of the chosen day: its time (meta colour), its symbol, and its name cut with '…'."""
        b = QPushButton()
        b.setCursor(Qt.PointingHandCursor)
        b.setAutoDefault(False)
        b.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        b.setFixedHeight(36)
        b.setToolTip(entry_tip(e))
        b.setStyleSheet(f"QPushButton {{ text-align: left; padding: 0; border: none; border-radius: 12px;"
                        f" background: {T.mix(kind_color(e.kind), T.PANEL, 0.14)}; }}"
                        f"QPushButton:hover {{ background: {T.mix(kind_color(e.kind), T.PANEL, 0.24)}; }}")
        lay = QHBoxLayout(b)
        lay.setContentsMargins(10, 0, 10, 0)
        lay.setSpacing(8)
        when = QLabel("All day" if e.all_day else fmt_time(e.start))
        when.setFont(ui_font(T.FONT_S))
        when.setStyleSheet(f"color: {T.META}; background: transparent;")
        fm = QFontMetrics(when.font())
        when.setFixedWidth(max(fm.horizontalAdvance("All day"), fm.horizontalAdvance("00:00")) + 6)
        mark = QLabel()
        mark.setFixedSize(14, 14)
        mark.setStyleSheet("background: transparent;")
        if e.icon:
            mark.setPixmap(icon(e.icon, kind_icon_color(e.kind, [T.PANEL]), 14).pixmap(14, 14))
        name = ElidedLabel(e.name)
        name.setStyleSheet("font-weight: 500; background: transparent;")
        for w in (when, mark, name):
            w.setAttribute(Qt.WA_TransparentForMouseEvents)
        lay.addWidget(when)
        lay.addWidget(mark)
        lay.addWidget(name, 1)
        b.clicked.connect(lambda _=False, e=e: self.open_entry(e))
        return b

    # ------------------------------------------------------------ actions
    @staticmethod
    def _default_start(day, hour=9, now=None):
        """When a new item on `day` starts: 09:00 (deadlines 18:00), but never in the past - today after that
        hour it is the next half hour (15:10 -> 15:30)."""
        start = datetime.datetime.combine(day, datetime.time(hour))
        now = now or datetime.datetime.now()
        if day == now.date() and now >= start:
            now = now.replace(second=0, microsecond=0)
            start = now + datetime.timedelta(minutes=30 - now.minute % 30)
            start = min(start, datetime.datetime.combine(day, datetime.time(23, 30)))
        return start

    def new_menu(self):
        m = QMenu(self)
        day = self.selected
        m.addAction(icon(KIND_ICONS["meeting"], T.TEXT, 16), "Meeting…",
                    lambda: self.new_item("meeting", self._default_start(day)))
        m.addAction(icon(KIND_ICONS["event"], T.TEXT, 16), "Personal event…",
                    lambda: self.new_item("event", self._default_start(day)))
        m.addAction(icon(KIND_ICONS["note"], T.TEXT, 16), "Note of the day…",
                    lambda: self.new_item("note", self._default_start(day)))
        if can_lead(self.store):
            m.addAction(icon(KIND_ICONS["deadline"], T.TEXT, 16), "Deadline…",
                        lambda: self.new_item("deadline", self._default_start(day, 18)))
        m.addAction(icon(KIND_ICONS["leave"], T.TEXT, 16), "Leave / out of office…", self.new_leave)
        m.addSeparator()
        m.addAction(icon("upload", T.TEXT, 16), "Import my calendar (.ics)…",
                    lambda: import_ics(self.ctx, self, "mine", after=self.fetch))
        if can_manage_holidays(self.store):
            m.addAction(icon(KIND_ICONS["holiday"], T.TEXT, 16), "Studio holidays…", self.holidays)
        # under the button, right edges lined up, kept on the screen
        m.exec(popup_pos(self.new_btn, m.sizeHint()))

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
