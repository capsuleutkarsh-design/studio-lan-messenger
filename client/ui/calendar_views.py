"""Calendar views: month grid, week/day time grid, agenda list and the small month on Home.

They draw 'entries' (see entries_from()) and emit what was clicked; the calendar page does the rest.
Everything is painted (no widget per item), so a busy month stays fast.
"""

import datetime

from PySide6.QtCore import QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import QScrollArea, QSizePolicy, QWidget

from common import theme as T

DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
WEEKEND = {6}                    # the studio works Monday to Saturday
KINDS = {  # kind: (label, colour)
    "meeting": ("Meetings", None),                 # the accent colour
    "event": ("My events", "#5b8def"),
    "note": ("Notes", "#e0a33a"),
    "deadline": ("Deadlines", "#ec6a6a"),
    "holiday": ("Holidays", "#e0668f"),
    "leave": ("Leave", "#5fae8b"),
    "birthday": ("Birthdays", "#d56bb3"),
}


def kind_color(kind):
    if kind == "anniversary":
        kind = "birthday"
    colour = KINDS.get(kind, ("", None))[1]
    return colour or T.ACCENT


class Entry:
    """One thing shown on the calendar (an occurrence, a holiday, a day of leave, a birthday)."""
    __slots__ = ("key", "kind", "title", "start", "end", "all_day", "item", "sub")

    def __init__(self, key, kind, title, start, end, all_day, item=None, sub=""):
        self.key, self.kind, self.title = key, kind, title
        self.start, self.end, self.all_day, self.item, self.sub = start, end, all_day, item, sub

    def days(self):
        """The dates this entry is on."""
        d = self.start.date()
        last = (self.end - datetime.timedelta(seconds=1)).date() if self.end > self.start else d
        out = []
        while d <= last:
            out.append(d)
            d += datetime.timedelta(days=1)
        return out

    def time_text(self):
        if self.all_day:
            return ""
        return f"{self.start:%H:%M}"


def entries_from(data, layers, me_id=None, room_id=None):
    """Entries for the views from a cal_range reply, with only the layers that are switched on.

    room_id: a room's calendar - only that room's meetings, deadlines and notes (plus holidays)."""
    out = []
    if not data:
        return out
    for i in data.get("items", []):
        kind = i["kind"]
        if kind not in layers:
            continue
        if room_id is not None and not (i["scope"] == "room" and str(i["scope_ref"]) == str(room_id)):
            continue
        start = datetime.datetime.fromtimestamp(i["start"])
        end = datetime.datetime.fromtimestamp(i["end"])
        prefix = {"deadline": "⏳ ", "note": "📝 "}.get(kind, "")
        if kind == "meeting" and i.get("my_rsvp") == "no":
            prefix = "✕ "
        out.append(Entry(i["id"], kind, prefix + i["title"], start, end, i["all_day"], i,
                         i.get("location") or ""))
    if "holiday" in layers:
        for h in data.get("holidays", []):
            d = datetime.datetime.fromisoformat(h["day"])
            out.append(Entry("h" + h["day"] + h["name"], "holiday", h["name"], d, d + datetime.timedelta(days=1),
                             True, h))
    if room_id is None and "leave" in layers:
        for lv in data.get("leave", []):
            s = datetime.datetime.fromisoformat(lv["first_day"])
            e = datetime.datetime.fromisoformat(lv["last_day"]) + datetime.timedelta(days=1)
            title = "You're on leave" if lv.get("mine") else f"{lv['name']} on leave"
            out.append(Entry(f"l{lv['id']}", "leave", "🌴 " + title, s, e, True, lv, lv.get("note", "")))
    if room_id is None and "birthday" in layers:
        for p in data.get("people_days", []):
            d = datetime.datetime.fromisoformat(p["day"])
            if p["kind"] == "birthday":
                title = "🎂 Your birthday" if p["user_id"] == me_id else f"🎂 {p['name']}"
            else:
                title = f"🎉 {p['name']} · {p['years']} year{'s' if p['years'] != 1 else ''}"
            out.append(Entry(f"p{p['kind']}{p['user_id']}{p['day']}", p["kind"], title, d,
                             d + datetime.timedelta(days=1), True, p))
    out.sort(key=lambda e: (e.start.date(), not e.all_day, e.start, e.title.lower()))
    return out


def by_day(entries):
    days = {}
    for e in entries:
        for d in e.days():
            days.setdefault(d, []).append(e)
    return days


def _font(size, bold=False):
    f = QFont("Segoe UI")
    f.setPointSizeF(size)
    f.setBold(bold)
    return f


# ======================================================================= month
class MonthView(QWidget):
    """Six weeks, Monday first. Click a day to pick it, double-click to open it, click an item for details."""
    day_clicked = Signal(object)          # date
    day_activated = Signal(object)        # date (double click)
    entry_clicked = Signal(object)        # Entry

    HEAD = 30

    def __init__(self):
        super().__init__()
        self.setMouseTracking(True)
        self.setMinimumSize(420, 360)
        self.month = datetime.date.today().replace(day=1)
        self.selected = datetime.date.today()
        self.days = {}
        self.holidays = set()
        self._hits = []

    def set_data(self, month, entries, holidays):
        self.month = month.replace(day=1)
        self.days = by_day(entries)
        self.holidays = holidays
        self.update()

    def first_day(self):
        return self.month - datetime.timedelta(days=self.month.weekday())

    def _cell(self, i):
        w, h = self.width(), self.height() - self.HEAD
        cw, ch = w / 7, h / 6
        return QRectF((i % 7) * cw, self.HEAD + (i // 7) * ch, cw, ch)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        self._hits = []
        today = datetime.date.today()
        p.setFont(_font(8, True))
        for i, name in enumerate(DAY_NAMES):
            r = self._cell(i)
            p.setPen(QColor(T.FAINT if i in WEEKEND else T.MUTED))
            p.drawText(QRectF(r.x() + 10, 0, r.width() - 10, self.HEAD), Qt.AlignVCenter | Qt.AlignLeft,
                       name.upper())
        first = self.first_day()
        fm = QFontMetrics(_font(8.5))
        for i in range(42):
            day = first + datetime.timedelta(days=i)
            r = self._cell(i).adjusted(2, 2, -2, -2)
            outside = day.month != self.month.month
            bg = QColor(T.PANEL)
            if day.weekday() in WEEKEND:
                bg = QColor(T.mix(T.BG, T.PANEL, 0.6))
            if day in self.holidays:
                bg = QColor(T.mix(kind_color("holiday"), T.PANEL, 0.10))
            if outside:
                bg = QColor(T.BG)
            p.setPen(Qt.NoPen)
            p.setBrush(bg)
            p.drawRoundedRect(r, 12, 12)
            if day == self.selected:
                p.setPen(QPen(QColor(T.ACCENT), 1.6))
                p.setBrush(Qt.NoBrush)
                p.drawRoundedRect(r.adjusted(0.8, 0.8, -0.8, -0.8), 12, 12)
            # the day number (today in a filled circle)
            num = QRectF(r.x() + 6, r.y() + 5, 24, 22)
            if day == today:
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(T.ACCENT))
                p.drawEllipse(num)
                p.setPen(QColor(T.ACCENT_TEXT))
            else:
                p.setPen(QColor(T.FAINT if outside else (T.MUTED if day.weekday() in WEEKEND else T.TEXT)))
            p.setFont(_font(9, day == today))
            p.drawText(num, Qt.AlignCenter, str(day.day))
            self._hits.append((r, day, None))
            # items
            items = self.days.get(day, [])
            line_h = 18
            top = r.y() + 30
            room = int((r.bottom() - top - 2) // (line_h + 2))
            shown = items if len(items) <= room else items[:max(0, room - 1)]
            for e in shown:
                pill = QRectF(r.x() + 5, top, r.width() - 10, line_h)
                colour = QColor(kind_color(e.kind))
                if e.all_day:
                    soft = QColor(colour)
                    soft.setAlphaF(0.18 if not outside else 0.1)
                    p.setPen(Qt.NoPen)
                    p.setBrush(soft)
                    p.drawRoundedRect(pill, 7, 7)
                    text_x = pill.x() + 7
                else:
                    p.setPen(Qt.NoPen)
                    p.setBrush(colour)
                    p.drawEllipse(QRectF(pill.x() + 5, pill.center().y() - 3, 6, 6))
                    text_x = pill.x() + 15
                p.setPen(QColor(T.FAINT if outside else T.TEXT))
                p.setFont(_font(8.5))
                narrow = pill.width() < 150            # a narrow cell: the name matters more than the time
                label = e.title if narrow or not e.time_text() else f"{e.time_text()} {e.title}"
                p.drawText(QRectF(text_x, pill.y(), pill.right() - text_x - 3, line_h), Qt.AlignVCenter,
                           fm.elidedText(label, Qt.ElideRight, int(pill.right() - text_x - 3)))
                self._hits.append((pill, day, e))
                top += line_h + 2
            if len(shown) < len(items):
                p.setPen(QColor(T.MUTED))
                p.setFont(_font(8, True))
                p.drawText(QRectF(r.x() + 8, top, r.width() - 12, line_h), Qt.AlignVCenter,
                           f"+{len(items) - len(shown)} more")
        p.end()

    def _hit(self, pos):
        best = None
        for rect, day, entry in self._hits:
            if rect.contains(pos):
                if entry is not None:
                    return day, entry
                best = (day, None)
        return best or (None, None)

    def mousePressEvent(self, e):
        day, entry = self._hit(e.position())
        if day is None:
            return
        self.selected = day
        self.update()
        if entry is not None:
            self.entry_clicked.emit(entry)
        else:
            self.day_clicked.emit(day)

    def mouseDoubleClickEvent(self, e):
        day, entry = self._hit(e.position())
        if day is not None and entry is None:
            self.day_activated.emit(day)

    def mouseMoveEvent(self, e):
        _day, entry = self._hit(e.position())
        self.setCursor(Qt.PointingHandCursor if entry else Qt.ArrowCursor)
        self.setToolTip(_tip(entry) if entry else "")


def _tip(e):
    when = "All day" if e.all_day else f"{e.start:%H:%M} – {e.end:%H:%M}"
    return f"{e.title}\n{when}" + (f"\n{e.sub}" if e.sub else "")


# ======================================================================= week / day
class TimeGrid(QWidget):
    """Hours down the side, one column per day; all-day items in a strip on top."""
    entry_clicked = Signal(object)
    slot_activated = Signal(object)        # datetime (double click on an empty time)

    HOUR = 46
    LEFT = 56
    HEAD = 38

    def __init__(self):
        super().__init__()
        self.setMouseTracking(True)
        self.start = datetime.date.today()
        self.ndays = 7
        self.days = {}
        self.holidays = set()
        self.allday_rows = 0
        self._hits = []

    def set_data(self, start, ndays, entries, holidays):
        self.start, self.ndays = start, ndays
        self.days = by_day(entries)
        self.holidays = holidays
        self.allday_rows = max([len([e for e in self.days.get(self._day(i), []) if e.all_day])
                                for i in range(ndays)] + [0])
        self.setMinimumHeight(self._top() + 24 * self.HOUR + 8)
        self.update()

    def _day(self, i):
        return self.start + datetime.timedelta(days=i)

    def _top(self):
        return self.HEAD + (self.allday_rows * 22 + 8 if self.allday_rows else 0)

    def _col(self, i):
        w = (self.width() - self.LEFT) / self.ndays
        return self.LEFT + i * w, w

    def sizeHint(self):
        return QSize(700, self._top() + 24 * self.HOUR)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        self._hits = []
        today = datetime.date.today()
        top = self._top()
        p.fillRect(self.rect(), QColor(T.BG))
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(T.PANEL))
        p.drawRoundedRect(QRectF(self.LEFT - 6, 0, self.width() - self.LEFT + 6, top + 24 * self.HOUR + 6), 18, 18)
        # hours
        p.setFont(_font(8))
        for h in range(24):
            y = top + h * self.HOUR
            p.setPen(QPen(QColor(T.HAIR), 1))
            p.drawLine(self.LEFT, int(y), self.width(), int(y))
            if h:
                p.setPen(QColor(T.FAINT))
                p.drawText(QRectF(0, y - 8, self.LEFT - 8, 16), Qt.AlignRight | Qt.AlignVCenter, f"{h:02d}:00")
        for i in range(self.ndays):
            day = self._day(i)
            x, w = self._col(i)
            if day.weekday() in WEEKEND or day in self.holidays:
                shade = QColor(kind_color("holiday") if day in self.holidays else T.BG)
                shade.setAlphaF(0.08 if day in self.holidays else 0.45)
                p.fillRect(QRectF(x, top, w, 24 * self.HOUR), shade)
            p.setPen(QPen(QColor(T.HAIR), 1))
            p.drawLine(int(x), self.HEAD - 6, int(x), top + 24 * self.HOUR)
            # header
            head = QRectF(x, 0, w, self.HEAD - 6)
            p.setPen(QColor(T.ACCENT if day == today else T.MUTED))
            p.setFont(_font(9, day == today))
            p.drawText(head, Qt.AlignCenter, f"{DAY_NAMES[day.weekday()]} {day.day}")
            # all-day strip
            y = self.HEAD
            for e in [e for e in self.days.get(day, []) if e.all_day]:
                r = QRectF(x + 3, y, w - 6, 20)
                c = QColor(kind_color(e.kind))
                c.setAlphaF(0.2)
                p.setPen(Qt.NoPen)
                p.setBrush(c)
                p.drawRoundedRect(r, 7, 7)
                p.setPen(QColor(T.TEXT))
                p.setFont(_font(8.5))
                p.drawText(r.adjusted(7, 0, -4, 0), Qt.AlignVCenter,
                           QFontMetrics(_font(8.5)).elidedText(e.title, Qt.ElideRight, int(r.width() - 11)))
                self._hits.append((r, e))
                y += 22
            # timed items, side by side when they overlap
            timed = sorted([e for e in self.days.get(day, []) if not e.all_day], key=lambda e: e.start)
            for e, col, cols in _columns(timed):
                s = max(e.start, datetime.datetime.combine(day, datetime.time()))
                end = min(e.end, datetime.datetime.combine(day, datetime.time()) + datetime.timedelta(days=1))
                y0 = top + (s.hour + s.minute / 60) * self.HOUR
                y1 = top + ((end - datetime.datetime.combine(day, datetime.time())).total_seconds() / 3600) * self.HOUR
                cw = (w - 8) / cols
                r = QRectF(x + 4 + col * cw, y0 + 1, cw - 3, max(20, y1 - y0 - 2))
                c = QColor(kind_color(e.kind))
                fill = QColor(c)
                fill.setAlphaF(0.22)
                p.setPen(Qt.NoPen)
                p.setBrush(fill)
                p.drawRoundedRect(r, 9, 9)
                p.setBrush(c)
                p.drawRoundedRect(QRectF(r.x(), r.y(), 4, r.height()), 2, 2)
                p.setPen(QColor(T.TEXT))
                p.setFont(_font(8.5, True))
                fm = QFontMetrics(_font(8.5, True))
                p.drawText(QRectF(r.x() + 9, r.y() + 3, r.width() - 12, 16), Qt.AlignLeft,
                           fm.elidedText(e.title, Qt.ElideRight, int(r.width() - 12)))
                if r.height() > 34:
                    p.setPen(QColor(T.MUTED))
                    p.setFont(_font(8))
                    p.drawText(QRectF(r.x() + 9, r.y() + 19, r.width() - 12, 16), Qt.AlignLeft,
                               f"{e.start:%H:%M} – {e.end:%H:%M}" + (f"  ·  {e.sub}" if e.sub else ""))
                self._hits.append((r, e))
        # now
        now = datetime.datetime.now()
        if self.start <= now.date() < self._day(self.ndays):
            x, w = self._col((now.date() - self.start).days)
            y = top + (now.hour + now.minute / 60) * self.HOUR
            p.setPen(QPen(QColor(T.DANGER), 2))
            p.drawLine(int(x), int(y), int(x + w), int(y))
            p.setBrush(QColor(T.DANGER))
            p.drawEllipse(QRectF(x - 4, y - 4, 8, 8))
        p.end()

    def _entry_at(self, pos):
        for r, e in reversed(self._hits):
            if r.contains(pos):
                return e
        return None

    def mousePressEvent(self, e):
        entry = self._entry_at(e.position())
        if entry is not None:
            self.entry_clicked.emit(entry)

    def mouseDoubleClickEvent(self, e):
        if self._entry_at(e.position()) is not None:
            return
        x, y = e.position().x(), e.position().y()
        if x < self.LEFT or y < self._top():
            return
        i = int((x - self.LEFT) / ((self.width() - self.LEFT) / self.ndays))
        hour = (y - self._top()) / self.HOUR
        half = int(hour * 2) / 2
        when = datetime.datetime.combine(self._day(i), datetime.time()) + datetime.timedelta(hours=half)
        self.slot_activated.emit(when)

    def mouseMoveEvent(self, e):
        entry = self._entry_at(e.position())
        self.setCursor(Qt.PointingHandCursor if entry else Qt.ArrowCursor)
        self.setToolTip(_tip(entry) if entry else "")


def _columns(timed):
    """[(entry, column, columns)] - overlapping items share the width."""
    out, group, group_end = [], [], None
    for e in timed + [None]:
        if e is None or (group_end is not None and e.start >= group_end):
            cols = []
            placed = []
            for g in group:
                for ci, last in enumerate(cols):
                    if g.start >= last:
                        cols[ci] = g.end
                        placed.append((g, ci))
                        break
                else:
                    cols.append(g.end)
                    placed.append((g, len(cols) - 1))
            out += [(g, ci, len(cols)) for g, ci in placed]
            group, group_end = [], None
        if e is not None:
            group.append(e)
            group_end = max(group_end, e.end) if group_end else e.end
    return out


class TimeGridView(QScrollArea):
    def __init__(self):
        super().__init__()
        self.grid = TimeGrid()
        self.setWidget(self.grid)
        self.setWidgetResizable(True)
        self.setFrameShape(QScrollArea.NoFrame)
        self.setStyleSheet("QScrollArea { background: transparent; }")
        self._scrolled = False

    def showEvent(self, e):
        super().showEvent(e)
        if not self._scrolled:
            self._scrolled = True
            from PySide6.QtCore import QTimer
            QTimer.singleShot(0, self.scroll_to_morning)

    def scroll_to_morning(self):
        """Start the day at 08:00 (or just before the first item of the day, if earlier)."""
        self.verticalScrollBar().setValue(int(self.grid._top() + 8 * TimeGrid.HOUR - 20))


# ======================================================================= agenda
class AgendaView(QScrollArea):
    entry_clicked = Signal(object)

    def __init__(self):
        super().__init__()
        self.setWidgetResizable(True)
        self.setFrameShape(QScrollArea.NoFrame)
        self.body = _AgendaBody(self)
        self.setWidget(self.body)

    def set_data(self, start, days, entries):
        self.body.set_data(start, days, entries)


class _AgendaBody(QWidget):
    def __init__(self, view):
        super().__init__()
        self.view = view
        self.rows = []
        self._hits = []
        self.setMouseTracking(True)

    def set_data(self, start, ndays, entries):
        days = by_day(entries)
        self.rows = [(d, days[d]) for d in sorted(days) if start <= d < start + datetime.timedelta(days=ndays)]
        h = sum(40 + 44 * len(items) for _d, items in self.rows) + 40
        self.setMinimumHeight(max(h, 200))
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        self._hits = []
        p.fillRect(self.rect(), QColor(T.BG))
        today = datetime.date.today()
        y = 8
        if not self.rows:
            p.setPen(QColor(T.MUTED))
            p.setFont(_font(10))
            p.drawText(QRectF(0, 40, self.width(), 30), Qt.AlignCenter, "Nothing coming up.")
        for day, items in self.rows:
            p.setPen(QColor(T.ACCENT if day == today else T.MUTED))
            p.setFont(_font(8.5, True))
            label = "TODAY  ·  " if day == today else ("TOMORROW  ·  " if day == today + datetime.timedelta(days=1) else "")
            p.drawText(QRectF(18, y + 12, self.width() - 36, 20), Qt.AlignVCenter,
                       label + f"{day:%A %d %B}".upper())
            y += 40
            for e in items:
                r = QRectF(12, y, self.width() - 24, 40)
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(T.PANEL))
                p.drawRoundedRect(r, 12, 12)
                p.setBrush(QColor(kind_color(e.kind)))
                p.drawEllipse(QRectF(r.x() + 14, r.center().y() - 4, 8, 8))
                p.setPen(QColor(T.MUTED))
                p.setFont(_font(9))
                when = "All day" if e.all_day else f"{e.start:%H:%M}"
                p.drawText(QRectF(r.x() + 32, r.y(), 70, r.height()), Qt.AlignVCenter, when)
                p.setPen(QColor(T.TEXT))
                p.setFont(_font(9.5, True))
                fm = QFontMetrics(_font(9.5, True))
                title_w = int(r.width() - 120)
                p.drawText(QRectF(r.x() + 108, r.y(), title_w, r.height()), Qt.AlignVCenter,
                           fm.elidedText(e.title + (f"   ·  {e.sub}" if e.sub else ""), Qt.ElideRight, title_w))
                self._hits.append((r, e))
                y += 44
        p.end()

    def mousePressEvent(self, e):
        for r, entry in self._hits:
            if r.contains(e.position()):
                self.view.entry_clicked.emit(entry)
                return

    def mouseMoveEvent(self, e):
        over = any(r.contains(e.position()) for r, _e in self._hits)
        self.setCursor(Qt.PointingHandCursor if over else Qt.ArrowCursor)


# ======================================================================= small month (Home)
class MiniMonth(QWidget):
    """A small month with a dot under days that have something; click a day to open it."""
    day_clicked = Signal(object)

    def __init__(self):
        super().__init__()
        self.month = datetime.date.today().replace(day=1)
        self.marks = {}
        self.holidays = set()
        self.setMinimumSize(250, 210)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setMouseTracking(True)
        self._cells = []

    def sizeHint(self):
        return QSize(280, 214)

    def set_data(self, month, entries, holidays):
        self.month = month.replace(day=1)
        self.marks = {}
        for e in entries:
            for d in e.days():
                self.marks.setdefault(d, []).append(kind_color(e.kind))
        self.holidays = holidays
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        self._cells = []
        w = self.width()
        cw = w / 7
        ch = 28
        p.setFont(_font(7.5, True))
        for i, n in enumerate(DAY_NAMES):
            p.setPen(QColor(T.FAINT))
            p.drawText(QRectF(i * cw, 0, cw, 20), Qt.AlignCenter, n[0])
        first = self.month - datetime.timedelta(days=self.month.weekday())
        today = datetime.date.today()
        for i in range(42):
            day = first + datetime.timedelta(days=i)
            if i >= 35 and day.month != self.month.month:
                break
            r = QRectF((i % 7) * cw, 22 + (i // 7) * ch, cw, ch)
            circle = QRectF(r.center().x() - 12, r.y() + 1, 24, 22)
            if day == today:
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(T.ACCENT))
                p.drawEllipse(circle)
            elif day in self.holidays:
                c = QColor(kind_color("holiday"))
                c.setAlphaF(0.18)
                p.setPen(Qt.NoPen)
                p.setBrush(c)
                p.drawEllipse(circle)
            outside = day.month != self.month.month
            p.setPen(QColor(T.ACCENT_TEXT if day == today else
                            (T.FAINT if outside or day.weekday() in WEEKEND else T.TEXT)))
            p.setFont(_font(8.5, day == today))
            p.drawText(circle, Qt.AlignCenter, str(day.day))
            colours = list(dict.fromkeys(self.marks.get(day, [])))[:3]
            for k, c in enumerate(colours):
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(c))
                x = r.center().x() - (len(colours) - 1) * 3.5 + k * 7
                p.drawEllipse(QRectF(x - 2, r.y() + 23, 4, 4))
            self._cells.append((r, day))
        p.end()

    def mousePressEvent(self, e):
        for r, day in self._cells:
            if r.contains(e.position()):
                self.day_clicked.emit(day)
                return

    def mouseMoveEvent(self, e):
        over = any(r.contains(e.position()) for r, _d in self._cells)
        self.setCursor(Qt.PointingHandCursor if over else Qt.ArrowCursor)
