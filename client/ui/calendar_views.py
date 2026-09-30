"""Calendar views: month grid, week/day time grid, agenda list and the small month on Home.

They draw 'entries' (see entries_from()) and emit what was clicked; the calendar page does the rest.
Everything is painted (no widget per item), so a busy month stays fast.
"""

import datetime
import math

from PySide6.QtCore import QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QScrollArea, QSizePolicy, QWidget

from common import theme as T
from common.fmt import SEP, day_word, fmt_date, fmt_time, fmt_time_range

DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
WEEKEND = {6}                    # the studio works Monday to Saturday
KINDS = {  # kind: (label, colour) - hues far enough apart to tell at a glance
    "meeting": ("Meetings", None),                 # the accent colour
    "event": ("Personal", "#5b8def"),
    "note": ("Notes", "#e0a33a"),
    "deadline": ("Deadlines", "#ec6a6a"),
    "holiday": ("Holidays", "#9b7fe6"),
    "leave": ("Leave", "#8fae5f"),
    "birthday": ("Birthdays", "#d56bb3"),
}
# one symbol per kind, the same in the '+ New' menu, the views and the side panel (common/icons.py names)
KIND_ICONS = {"meeting": "users", "event": "event", "note": "note", "deadline": "time", "holiday": "sun",
              "leave": "palm", "birthday": "cake", "anniversary": "gift"}
# the kinds whose rows carry their symbol (meetings and personal events are told apart by colour and time)
_ROW_ICONS = {k: KIND_ICONS[k] for k in ("note", "deadline", "holiday", "leave", "birthday", "anniversary")}


def kind_color(kind):
    if kind == "anniversary":
        kind = "birthday"
    colour = KINDS.get(kind, ("", None))[1]
    return colour or T.ACCENT


def kind_text_color(kind, bg=None):
    """The kind's colour as text: its hue, made readable (4.5:1) on the page, the panel and a ticked chip."""
    return T.readable_on(kind_color(kind), bg or [T.BG, T.PANEL, T.ACCENT_SOFT])


def kind_icon_color(kind, bg=None):
    """The kind's colour for a symbol (icons need 3:1)."""
    return T.readable_on(kind_color(kind), bg or [T.BG, T.PANEL], 3.0)


class Entry:
    """One thing shown on the calendar (an occurrence, a holiday, a day of leave, a birthday).

    title: one line of text with its symbol in front ('🎂 Priya Sharma's birthday') for plain-text lists;
    name + icon: the same without the symbol, and the icons.py name the views draw in a fixed slot."""
    __slots__ = ("key", "kind", "title", "start", "end", "all_day", "item", "sub", "name", "icon")

    def __init__(self, key, kind, title, start, end, all_day, item=None, sub="", name=None, icon=None):
        self.key, self.kind, self.title = key, kind, title
        self.start, self.end, self.all_day, self.item, self.sub = start, end, all_day, item, sub
        self.name = title if name is None else name
        self.icon = icon

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
        return fmt_time(self.start)


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
        mark = _ROW_ICONS.get(kind)
        if kind == "meeting" and i.get("my_rsvp") == "no":
            prefix, mark = "✕ ", "close"                  # a meeting I said I can't go to
        out.append(Entry(i["id"], kind, prefix + i["title"], start, end, i["all_day"], i,
                         i.get("location") or "", name=i["title"], icon=mark))
    if "holiday" in layers:
        for h in data.get("holidays", []):
            d = datetime.datetime.fromisoformat(h["day"])
            out.append(Entry("h" + h["day"] + h["name"], "holiday", h["name"], d, d + datetime.timedelta(days=1),
                             True, h, icon=_ROW_ICONS["holiday"]))
    if room_id is None and "leave" in layers:
        for lv in data.get("leave", []):
            s = datetime.datetime.fromisoformat(lv["first_day"])
            e = datetime.datetime.fromisoformat(lv["last_day"]) + datetime.timedelta(days=1)
            name = "You're on leave" if lv.get("mine") else f"{lv['name']} on leave"
            out.append(Entry(f"l{lv['id']}", "leave", "🌴 " + name, s, e, True, lv, lv.get("note", ""),
                             name=name, icon=_ROW_ICONS["leave"]))
    if room_id is None and "birthday" in layers:
        for p in data.get("people_days", []):
            d = datetime.datetime.fromisoformat(p["day"])
            mine = p["user_id"] == me_id
            if p["kind"] == "birthday":
                name = "Your birthday" if mine else f"{p['name']}'s birthday"
                symbol = "🎂 "
            else:
                years = f"{p['years']} year{'s' if p['years'] != 1 else ''}"
                name = f"Your {years} at the studio" if mine else f"{p['name']} – {years} at the studio"
                symbol = "🎉 "
            out.append(Entry(f"p{p['kind']}{p['user_id']}{p['day']}", p["kind"], symbol + name, d,
                             d + datetime.timedelta(days=1), True, p, name=name, icon=_ROW_ICONS[p["kind"]]))
    out.sort(key=lambda e: (e.start.date(), not e.all_day, e.start, e.name.lower()))
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


def _draw_icon(p, name, colour, rect):
    """A symbol from common/icons.py, tinted, in rect (drawn from the sharp 2x pixmap)."""
    from common.icons import pixmap
    pm = pixmap(name, colour, int(round(rect.width())))
    p.drawPixmap(rect, pm, QRectF(pm.rect()))


def _lines(fm, text, width, max_lines):
    """text wrapped on words into at most max_lines lines of `width` px; the last one ends in '…' when cut."""
    words, lines, cur, i = text.split(), [], "", 0
    width = max(1, int(width))
    while i < len(words) and len(lines) < max_lines - 1:
        trial = f"{cur} {words[i]}" if cur else words[i]
        if fm.horizontalAdvance(trial) <= width:
            cur, i = trial, i + 1
        elif cur:
            lines.append(cur)
            cur = ""
        else:                                    # one word longer than the line: break it
            n = len(words[i])
            while n > 1 and fm.horizontalAdvance(words[i][:n]) > width:
                n -= 1
            lines.append(words[i][:n])
            words[i] = words[i][n:]
    rest = " ".join(([cur] if cur else []) + words[i:])
    if rest:
        lines.append(fm.elidedText(rest, Qt.ElideRight, width))
    return lines


def entry_when(e):
    """'All day', 'Due 18:00' or '16:00 – 16:30'."""
    if e.all_day:
        return "All day"
    if e.kind == "deadline":
        return f"Due {fmt_time(e.start)}"
    return fmt_time_range(e.start, e.end)


def entry_tip(e):
    """The tooltip of an entry: the whole name, when, and where."""
    return f"{e.name}\n{entry_when(e)}" + (f"\n{e.sub}" if e.sub else "")


_tip = entry_tip                     # the old name


def month_rows(month):
    """How many week rows a month needs (Monday first): 4, 5 or 6."""
    first = month.replace(day=1)
    nxt = datetime.date(first.year + first.month // 12, first.month % 12 + 1, 1)
    return math.ceil((first.weekday() + (nxt - first).days) / 7)


# ======================================================================= month
class MonthView(QWidget):
    """The weeks of one month, Monday first. Click a day to pick it, double-click (or '+N more') to open it,
    click an item for details. In a MonthScroll a quiet week fills its share of the page and a busy one
    grows tall enough to list its items, and the month scrolls; the day names stay on top."""
    day_clicked = Signal(object)          # date
    day_activated = Signal(object)        # date (double click, or '+N more')
    entry_clicked = Signal(object)        # Entry

    HEAD = 30
    MORE = "more"                         # the '+N more' line of a day, in _hits
    MIN_ROW = 96                          # room for three items: a busy month scrolls rather than squeezing
    LINE = 20                             # one item line (18 + 2 apart)
    MAX_LINES = 6                         # more items than this in a day: '+N more' opens the day

    def __init__(self):
        super().__init__()
        self.setMouseTracking(True)
        self.setMinimumWidth(300)         # it shrinks with the window (the day panel folds away first)
        self._view_h = None               # the scroll area's height, once it is in a MonthScroll
        self._tops, self._hs = [], []     # top and height of each week row
        self.month = datetime.date.today().replace(day=1)
        self.selected = datetime.date.today()
        self.days = {}
        self.holidays = set()
        self._hits = []
        self._hover = None

    def set_data(self, month, entries, holidays):
        self.month = month.replace(day=1)
        self.days = by_day(entries)
        self.holidays = holidays
        self.relayout()

    def first_day(self):
        return self.month - datetime.timedelta(days=self.month.weekday())

    def rows(self):
        return month_rows(self.month)

    def _need(self, n):
        """Height of a week row whose busiest day has n items."""
        lines = min(n, self.MAX_LINES) + (1 if n > self.MAX_LINES else 0)
        return 30 + lines * self.LINE + 6

    def relayout(self):
        """Row heights: quiet weeks share the space, a busy week grows so its items show (and the month
        scrolls). On its own (no MonthScroll) the rows just share the widget's height."""
        rows = self.rows()
        scrolling = self._view_h is not None
        avail = (self._view_h if scrolling and self._view_h else self.height()) - self.HEAD
        first = self.first_day()
        if scrolling:
            base = max(self.MIN_ROW, avail / rows)
            self._hs = []
            for r in range(rows):
                n = max(len(self.days.get(first + datetime.timedelta(days=r * 7 + c), [])) for c in range(7))
                self._hs.append(max(base, self._need(n)))
        else:
            self._hs = [max(1.0, avail / rows)] * rows
        self._tops, y = [], self.HEAD
        for h in self._hs:
            self._tops.append(y)
            y += h
        if scrolling:
            self.setMinimumHeight(int(y) + 2)
        self.update()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self._view_h is None:
            self.relayout()

    def _head_top(self):
        """Where the day names are drawn: the top of what is visible, so they stay on top while scrolling."""
        return max(0, self.visibleRegion().boundingRect().top()) if self._view_h is not None else 0

    def _cell(self, i):
        if len(self._tops) != self.rows():
            self.relayout()
        row = i // 7
        cw = self.width() / 7
        return QRectF((i % 7) * cw, self._tops[row], cw, self._hs[row])

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        p.fillRect(self.rect(), QColor(T.BG))         # the page colour between the days (the scroll area has none)
        self._hits = []
        today = datetime.date.today()
        first = self.first_day()
        fm = QFontMetrics(_font(8.5))
        for i in range(self.rows() * 7):
            day = first + datetime.timedelta(days=i)
            r = self._cell(i).adjusted(2, 2, -2, -2)
            outside = day.month != self.month.month
            bg = T.PANEL
            if day.weekday() in WEEKEND:
                bg = T.mix(T.BG, T.PANEL, 0.6)
            if day in self.holidays:
                bg = T.mix(kind_color("holiday"), T.PANEL, 0.10)
            if outside:
                bg = T.mix(T.BG, T.PANEL, 0.75)        # a faint card: the grid stays one shape
            if day == self._hover:
                bg = T.mix(T.TEXT, bg, 0.05)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(bg))
            p.drawRoundedRect(r, 12, 12)
            if day == self.selected:
                p.setPen(QPen(QColor(T.ACCENT), 1.6))
                p.setBrush(Qt.NoBrush)
                p.drawRoundedRect(r.adjusted(0.8, 0.8, -0.8, -0.8), 12, 12)
            # the day number (today in a filled circle)
            num = QRectF(r.x() + 6, r.y() + 5, 22, 22)
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
            # items (days of the next / last month keep readable items: they can be in this week)
            items = self.days.get(day, [])
            line_h = 18
            top = r.y() + 30
            room = int((r.bottom() - top - 2) // (line_h + 2))
            shown = items if len(items) <= room else items[:max(0, room - 1)]
            p.setFont(_font(8.5))
            for e in shown:
                pill = QRectF(r.x() + 3, top, r.width() - 6, line_h)          # the whole cell width for the name
                colour = QColor(kind_color(e.kind))
                x = pill.x() + 5
                # a narrow cell keeps the name: an all-day item's tint (a timed one's dot) says the kind there
                symbol = e.icon if pill.width() >= 90 else None
                if e.all_day:
                    soft = QColor(colour)
                    soft.setAlphaF(0.14 if outside else 0.18)
                    p.setPen(Qt.NoPen)
                    p.setBrush(soft)
                    p.drawRoundedRect(pill, 7, 7)
                elif not symbol:
                    p.setPen(Qt.NoPen)
                    p.setBrush(colour)
                    p.drawEllipse(QRectF(pill.x() + 4, pill.center().y() - 3, 6, 6))
                    x = pill.x() + 13
                if symbol:
                    _draw_icon(p, symbol, kind_icon_color(e.kind), QRectF(x - 1, pill.center().y() - 6, 12, 12))
                    x += 14
                right = pill.right() - 2
                text_colour = QColor(T.MUTED if outside else T.TEXT)
                if pill.width() >= 150 and e.time_text():     # a wide cell: the time first, in the meta colour
                    t = e.time_text() + " "
                    p.setPen(QColor(T.META))
                    p.drawText(QRectF(x, pill.y(), right - x, line_h), Qt.AlignVCenter, t)
                    x += fm.horizontalAdvance(t)
                p.setPen(text_colour)
                p.drawText(QRectF(x, pill.y(), max(0.0, right - x), line_h), Qt.AlignVCenter,
                           fm.elidedText(e.name, Qt.ElideRight, int(max(0, right - x))))
                self._hits.append((pill, day, e))
                top += line_h + 2
            if len(shown) < len(items):
                more = QRectF(r.x() + 3, top, r.width() - 6, line_h)
                p.setPen(QColor(T.ACCENT if day == self._hover else T.MUTED))
                p.setFont(_font(8, True))
                p.drawText(more.adjusted(3, 0, 0, 0), Qt.AlignVCenter, f"+{len(items) - len(shown)} more")
                self._hits.append((more, day, self.MORE))
        # the day names last, on the page colour, so the weeks scrolling under them are hidden
        off = self._head_top()
        p.fillRect(QRectF(0, off, self.width(), self.HEAD), QColor(T.BG))
        p.setFont(_font(8, True))
        for i, name in enumerate(DAY_NAMES):
            r = self._cell(i)
            p.setPen(QColor(T.META if i in WEEKEND else T.MUTED))
            p.drawText(QRectF(r.x() + 10, off, r.width() - 10, self.HEAD), Qt.AlignVCenter | Qt.AlignLeft,
                       name.upper())
        p.end()

    def _hit(self, pos):
        if pos.y() < self._head_top() + self.HEAD:          # the day names, over whatever scrolled under them
            return None, None
        best = None
        for rect, day, target in self._hits:
            if rect.contains(pos):
                if target is not None:
                    return day, target
                best = (day, None)
        return best or (None, None)

    def mousePressEvent(self, e):
        day, target = self._hit(e.position())
        if day is None:
            return
        self.selected = day
        self.update()
        if target == self.MORE:
            self.day_activated.emit(day)
        elif target is not None:
            self.entry_clicked.emit(target)
        else:
            self.day_clicked.emit(day)

    def mouseDoubleClickEvent(self, e):
        day, target = self._hit(e.position())
        if day is not None and target is None:
            self.day_activated.emit(day)

    def mouseMoveEvent(self, e):
        day, target = self._hit(e.position())
        if day != self._hover:
            self._hover = day
            self.update()
        self.setCursor(Qt.PointingHandCursor if day is not None else Qt.ArrowCursor)
        if target == self.MORE:
            tip = "Open this day"
        elif target is not None:
            tip = entry_tip(target)
        else:
            tip = "Double-click to open this day" if day is not None else ""
        if tip != self.toolTip():
            self.setToolTip(tip)

    def leaveEvent(self, e):
        super().leaveEvent(e)
        if self._hover is not None:
            self._hover = None
            self.update()


# ======================================================================= week / day
class TimeGrid(QWidget):
    """Hours down the side, one column per day; all-day items in a strip on top."""
    entry_clicked = Signal(object)
    slot_activated = Signal(object)        # datetime (double click on an empty time)
    day_clicked = Signal(object)           # date (a day's name on top, in the week)

    HOUR = 46
    LEFT = 56
    HEAD = 38
    RADIUS = 18
    MARK = 20                              # a deadline is a moment: a short marker at the due time

    def __init__(self):
        super().__init__()
        self.setMouseTracking(True)
        self.start = datetime.date.today()
        self.ndays = 7
        self.days = {}
        self.holidays = set()
        self.allday_rows = 0
        self._hits = []
        self._heads = []

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

    def _block_end(self, e):
        """Where an item's block ends: a deadline only takes a short marker, whatever its end."""
        if e.kind == "deadline":
            return e.start + datetime.timedelta(hours=self.MARK / self.HOUR)
        return e.end

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        self._hits = []
        today = datetime.date.today()
        top = self._top()
        p.fillRect(self.rect(), QColor(T.BG))
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(T.PANEL))
        p.drawRoundedRect(QRectF(self.LEFT - 6, 0, self.width() - self.LEFT + 6, top + 24 * self.HOUR + 6),
                          self.RADIUS, self.RADIUS)
        # hours
        p.setFont(_font(8))
        for h in range(24):
            y = top + h * self.HOUR
            p.setPen(QPen(QColor(T.HAIR), 1))
            p.drawLine(self.LEFT, int(y), self.width(), int(y))
            if h:
                p.setPen(QColor(T.META))
                p.drawText(QRectF(0, y - 8, self.LEFT - 8, 16), Qt.AlignRight | Qt.AlignVCenter, f"{h:02d}:00")
        f_title, f_meta = _font(8.5, True), _font(8)
        fm, fm8 = QFontMetrics(f_title), QFontMetrics(f_meta)
        for i in range(self.ndays):
            day = self._day(i)
            x, w = self._col(i)
            if day == today and self.ndays > 1:
                mark = QColor(T.ACCENT_SOFT)
                mark.setAlphaF(0.45)
                p.fillRect(QRectF(x, top, w, 24 * self.HOUR), mark)
            elif day.weekday() in WEEKEND or day in self.holidays:
                shade = QColor(kind_color("holiday") if day in self.holidays else T.BG)
                shade.setAlphaF(0.08 if day in self.holidays else 0.45)
                p.fillRect(QRectF(x, top, w, 24 * self.HOUR), shade)
            p.setPen(QPen(QColor(T.HAIR), 1))
            p.drawLine(int(x), self.HEAD - 6, int(x), top + 24 * self.HOUR)
            # timed items, side by side when they overlap
            timed = sorted([e for e in self.days.get(day, []) if not e.all_day], key=lambda e: e.start)
            narrow = w < 100
            inset = 2 if narrow else 4
            midnight = datetime.datetime.combine(day, datetime.time())
            for e, col, cols in _columns(timed, self._block_end):
                s = max(e.start, midnight)
                end = min(self._block_end(e), midnight + datetime.timedelta(days=1))
                y0 = top + (s - midnight).total_seconds() / 3600 * self.HOUR
                y1 = top + (end - midnight).total_seconds() / 3600 * self.HOUR
                cw = (w - 2 * inset) / cols
                r = QRectF(x + inset + col * cw, y0 + 1, cw - (2 if narrow else 3), max(self.MARK, y1 - y0 - 2))
                self._paint_block(p, e, r, narrow, fm, fm8, f_title, f_meta)
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
        self._paint_head(p, top, today)
        p.end()

    def _paint_block(self, p, e, r, narrow, fm, fm8, f_title, f_meta):
        """One timed item: its name (on two lines when the column is narrow), then when and where."""
        c = QColor(kind_color(e.kind))
        fill = QColor(c)
        fill.setAlphaF(0.22)
        p.setPen(Qt.NoPen)
        p.setBrush(fill)
        p.drawRoundedRect(r, 9, 9)
        p.setBrush(c)
        p.drawRoundedRect(QRectF(r.x(), r.y(), 4, r.height()), 2, 2)
        tx = r.x() + (7 if narrow else 9)
        right = r.right() - 3
        lh = min(fm.height(), 14)                            # a tight line step: 45 minutes hold two lines
        total = max(1, int((r.height() - 2) // lh))          # lines of text that fit in the block
        band = QRectF(tx, r.y(), 0, min(r.height(), lh + 6)) if total == 1 else QRectF(tx, r.y() + 2, 0, lh)
        if e.icon and right - tx > 40:
            _draw_icon(p, e.icon, kind_icon_color(e.kind), QRectF(tx, band.center().y() - 6, 12, 12))
            tx += 15
        tw = right - tx
        if tw < 4:
            return
        detail = entry_when(e) + (SEP + e.sub if e.sub else "")
        if total == 1:
            # one line: the name, and when it is after it in the meta colour if the block is wide (day view)
            name = fm.elidedText(e.name, Qt.ElideRight, int(tw))
            p.setPen(QColor(T.TEXT))
            p.setFont(f_title)
            p.drawText(QRectF(tx, band.y(), tw, band.height()), Qt.AlignVCenter | Qt.AlignLeft, name)
            used = fm.horizontalAdvance(name)
            if r.width() > 240 and name == e.name and tw - used > 60:
                p.setPen(QColor(T.MUTED))
                p.setFont(f_meta)
                p.drawText(QRectF(tx + used, band.y(), tw - used, band.height()), Qt.AlignVCenter | Qt.AlignLeft,
                           fm8.elidedText("  " + detail, Qt.ElideRight, int(tw - used)))
            return
        if fm.horizontalAdvance(e.name) <= tw:
            names = [e.name]
        else:                                      # a narrow column: the name on two (or more) lines
            names = _lines(fm, e.name, tw, total - 1 if total >= 3 else total)
        p.setPen(QColor(T.TEXT))
        p.setFont(f_title)
        y = band.y()
        for line in names:
            p.drawText(QRectF(tx, y, tw, lh), Qt.AlignLeft | Qt.AlignVCenter, line)
            y += lh
        if len(names) < total:
            p.setPen(QColor(T.MUTED))
            p.setFont(f_meta)
            p.drawText(QRectF(tx, y, tw, lh), Qt.AlignLeft | Qt.AlignVCenter,
                       fm8.elidedText(detail, Qt.ElideRight, int(tw)))

    def _paint_head(self, p, top, today):
        """The day names and the all-day items stay at the top while the hours scroll under them."""
        off = max(0, self.visibleRegion().boundingRect().top())
        self._heads = []
        p.fillRect(QRectF(0, off, self.width(), top - 2), QColor(T.BG))
        # the panel's rounded top corners, like the rest of the page (the bottom edge stays square)
        head = QRectF(self.LEFT - 6, off, self.width() - self.LEFT + 6, top - 2)
        path = QPainterPath()
        path.setFillRule(Qt.WindingFill)
        path.addRoundedRect(head, self.RADIUS, self.RADIUS)
        path.addRect(head.adjusted(0, self.RADIUS, 0, 0))
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(T.PANEL))
        p.drawPath(path.simplified())
        if off:
            p.setPen(QPen(QColor(T.HAIR), 1))
            p.drawLine(self.LEFT - 6, int(off + top - 2), self.width(), int(off + top - 2))
        fm = QFontMetrics(_font(8.5))
        for i in range(self.ndays):
            day = self._day(i)
            x, w = self._col(i)
            name_rect = QRectF(x, off, w, self.HEAD - 6)
            if self.ndays == 1:
                label = f"{day:%A}"                  # the page title already says the date
            else:
                label = f"{DAY_NAMES[day.weekday()]} {day.day}"
                self._heads.append((name_rect, day))
            if day == today and self.ndays > 1:
                pw = min(w - 6, max(68, QFontMetrics(_font(9, True)).horizontalAdvance(label) + 20))
                pill = QRectF(x + w / 2 - pw / 2, off + 6, pw, self.HEAD - 16)
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(T.ACCENT_SOFT))
                p.drawRoundedRect(pill, 10, 10)
            p.setPen(QColor(T.ACCENT if day == today else (T.TEXT if day.weekday() not in WEEKEND else T.MUTED)))
            p.setFont(_font(9, True))
            p.drawText(name_rect.adjusted(0, 4, 0, 0), Qt.AlignCenter, label)
            y = off + self.HEAD
            for e in [e for e in self.days.get(day, []) if e.all_day]:
                r = QRectF(x + 3, y, w - 6, 20)
                c = QColor(kind_color(e.kind))
                c.setAlphaF(0.2)
                p.setPen(Qt.NoPen)
                p.setBrush(c)
                p.drawRoundedRect(r, 7, 7)
                tx = r.x() + 7
                if e.icon and r.width() > 50:
                    _draw_icon(p, e.icon, kind_icon_color(e.kind), QRectF(tx - 1, r.center().y() - 6, 12, 12))
                    tx += 14
                p.setPen(QColor(T.TEXT))
                p.setFont(_font(8.5))
                p.drawText(QRectF(tx, r.y(), r.right() - tx - 4, r.height()), Qt.AlignVCenter,
                           fm.elidedText(e.name, Qt.ElideRight, int(max(0, r.right() - tx - 4))))
                self._hits.append((r, e))
                y += 22

    def _entry_at(self, pos):
        for r, e in reversed(self._hits):
            if r.contains(pos):
                return e
        return None

    def _head_at(self, pos):
        for r, day in self._heads:
            if r.contains(pos):
                return day
        return None

    def mousePressEvent(self, e):
        entry = self._entry_at(e.position())
        if entry is not None:
            self.entry_clicked.emit(entry)
            return
        day = self._head_at(e.position())
        if day is not None:
            self.day_clicked.emit(day)

    def mouseDoubleClickEvent(self, e):
        if self._entry_at(e.position()) is not None:
            return
        x, y = e.position().x(), e.position().y()
        if x < self.LEFT or y < self._top() + max(0, self.visibleRegion().boundingRect().top()):
            return                           # the day names / all-day strip on top
        i = int((x - self.LEFT) / ((self.width() - self.LEFT) / self.ndays))
        hour = (y - self._top()) / self.HOUR
        half = int(hour * 2) / 2
        when = datetime.datetime.combine(self._day(i), datetime.time()) + datetime.timedelta(hours=half)
        self.slot_activated.emit(when)

    def mouseMoveEvent(self, e):
        entry = self._entry_at(e.position())
        day = None if entry else self._head_at(e.position())
        self.setCursor(Qt.PointingHandCursor if entry or day else Qt.ArrowCursor)
        tip = entry_tip(entry) if entry else (f"Open {fmt_date(day)}" if day else "")
        if tip != self.toolTip():
            self.setToolTip(tip)


def _columns(timed, end_of=lambda e: e.end):
    """[(entry, column, columns)] - overlapping items share the width. end_of: where an item's block ends."""
    out, group, group_end = [], [], None
    for e in timed + [None]:
        if e is None or (group_end is not None and e.start >= group_end):
            cols = []
            placed = []
            for g in group:
                for ci, last in enumerate(cols):
                    if g.start >= last:
                        cols[ci] = end_of(g)
                        placed.append((g, ci))
                        break
                else:
                    cols.append(end_of(g))
                    placed.append((g, len(cols) - 1))
            out += [(g, ci, len(cols)) for g, ci in placed]
            group, group_end = [], None
        if e is not None:
            group.append(e)
            group_end = max(group_end, end_of(e)) if group_end else end_of(e)
    return out


class MonthScroll(QScrollArea):
    """The month view in a scroll area: the wheel scrolls through a month whose busy weeks are tall."""

    def __init__(self, view):
        super().__init__()
        self.view = view
        view._view_h = 0
        self.setWidget(view)
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setFrameShape(QScrollArea.NoFrame)
        self.setStyleSheet("QScrollArea { background: transparent; }")
        # the day names stay on top: repaint the whole month on scroll, not just the part that moved in
        self.verticalScrollBar().valueChanged.connect(lambda _v: view.update())

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.view._view_h = self.viewport().height()
        self.view.relayout()

    def to_top(self):
        self.verticalScrollBar().setValue(0)


class TimeGridView(QScrollArea):
    def __init__(self):
        super().__init__()
        self.grid = TimeGrid()
        self.setWidget(self.grid)
        self.setWidgetResizable(True)
        # the day names stay on top: repaint the whole grid on scroll, not just the part that moved in
        self.verticalScrollBar().valueChanged.connect(lambda _v: self.grid.update())
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
        """Start the day at 08:00, just under the day names (they stay on top)."""
        self.verticalScrollBar().setValue(int(8 * TimeGrid.HOUR - 12))


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
    ROW = 40
    TIME_X = 32                  # from the row's left edge: the colour dot, the time, the symbol, the name
    ICON_X = 108
    NAME_X = 132

    def __init__(self, view):
        super().__init__()
        self.view = view
        self.rows = []
        self._hits = []
        self._hover = None
        self.setMouseTracking(True)

    def set_data(self, start, ndays, entries):
        days = by_day(entries)
        self.rows = [(d, days[d]) for d in sorted(days) if start <= d < start + datetime.timedelta(days=ndays)]
        h = sum(40 + (self.ROW + 4) * len(items) for _d, items in self.rows) + 40
        self.setMinimumHeight(max(h, 200))
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        self._hits = []
        p.fillRect(self.rect(), QColor(T.BG))
        today = datetime.date.today()
        y = 8
        if not self.rows:
            p.setPen(QColor(T.MUTED))
            p.setFont(_font(10))
            p.drawText(QRectF(0, 40, self.width(), 30), Qt.AlignCenter, "Nothing coming up.")
        f_name, f_sub, f_time = _font(9.5, True), _font(9.5), _font(9)
        fm, fm_sub = QFontMetrics(f_name), QFontMetrics(f_sub)
        for day, items in self.rows:
            p.setPen(QColor(T.ACCENT if day == today else T.MUTED))
            p.setFont(_font(8.5, True))
            word = day_word(day)
            label = (f"{word}{SEP}" if word else "") + fmt_date(day, long=True)
            p.drawText(QRectF(18, y + 12, self.width() - 36, 20), Qt.AlignVCenter, label.upper())
            y += 40
            for e in items:
                r = QRectF(12, y, self.width() - 24, self.ROW)
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(T.SURFACE_HOVER if (day, e.key) == self._hover else T.PANEL))
                p.drawRoundedRect(r, 12, 12)
                p.setBrush(QColor(kind_color(e.kind)))
                p.drawEllipse(QRectF(r.x() + 14, r.center().y() - 4, 8, 8))
                p.setPen(QColor(T.MUTED))
                p.setFont(f_time)
                when = "All day" if e.all_day else fmt_time(e.start)
                p.drawText(QRectF(r.x() + self.TIME_X, r.y(), self.ICON_X - self.TIME_X - 4, r.height()),
                           Qt.AlignVCenter, when)
                if e.icon:                         # a fixed slot, so every name starts at the same place
                    _draw_icon(p, e.icon, kind_icon_color(e.kind),
                               QRectF(r.x() + self.ICON_X, r.center().y() - 8, 16, 16))
                x = r.x() + self.NAME_X
                width = r.right() - 12 - x
                name = fm.elidedText(e.name, Qt.ElideRight, int(max(0, width)))
                p.setPen(QColor(T.TEXT))
                p.setFont(f_name)
                p.drawText(QRectF(x, r.y(), max(0.0, width), r.height()), Qt.AlignVCenter, name)
                used = fm.horizontalAdvance(name)
                if e.sub and name == e.name and width - used > 40:     # where: after the name, not part of it
                    p.setPen(QColor(T.MUTED))
                    p.setFont(f_sub)
                    p.drawText(QRectF(x + used, r.y(), width - used, r.height()), Qt.AlignVCenter,
                               fm_sub.elidedText(SEP + e.sub, Qt.ElideRight, int(width - used)))
                self._hits.append((r, (day, e.key), e))
                y += self.ROW + 4
        p.end()

    def mousePressEvent(self, e):
        for r, _key, entry in self._hits:
            if r.contains(e.position()):
                self.view.entry_clicked.emit(entry)
                return

    def mouseMoveEvent(self, e):
        hit = next(((key, entry) for r, key, entry in self._hits if r.contains(e.position())), (None, None))
        self.setCursor(Qt.PointingHandCursor if hit[0] else Qt.ArrowCursor)
        tip = entry_tip(hit[1]) if hit[1] else ""
        if tip != self.toolTip():
            self.setToolTip(tip)
        if hit[0] != self._hover:
            self._hover = hit[0]
            self.update()

    def leaveEvent(self, e):
        super().leaveEvent(e)
        if self._hover is not None:
            self._hover = None
            self.update()


# ======================================================================= small month (Home)
class MiniMonth(QWidget):
    """A small month with a dot under days that have something; click a day to open it.

    It is as tall as the month's weeks (5 or 6 rows), so no gap is left under a short month.
    The mouse wheel over it asks for the previous or next month (month_step: -1 / +1)."""
    day_clicked = Signal(object)
    month_step = Signal(int)

    ROW = 28
    HEAD = 22

    def __init__(self):
        super().__init__()
        self.month = datetime.date.today().replace(day=1)
        self.marks = {}
        self.holidays = set()
        self.setMinimumWidth(250)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setFixedHeight(self._height())
        self.setMouseTracking(True)
        self._cells = []

    def _height(self):
        return self.HEAD + month_rows(self.month) * self.ROW + 8

    def sizeHint(self):
        return QSize(280, self._height())

    def set_data(self, month, entries, holidays):
        self.month = month.replace(day=1)
        self.marks = {}
        for e in entries:
            for d in e.days():
                self.marks.setdefault(d, []).append(kind_color(e.kind))
        self.holidays = holidays
        self.setFixedHeight(self._height())
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        self._cells = []
        w = self.width()
        cw = w / 7
        ch = self.ROW
        p.setFont(_font(7.5, True))
        for i, n in enumerate(DAY_NAMES):
            p.setPen(QColor(T.META))
            p.drawText(QRectF(i * cw, 0, cw, 20), Qt.AlignCenter, n[0])
        first = self.month - datetime.timedelta(days=self.month.weekday())
        today = datetime.date.today()
        for i in range(month_rows(self.month) * 7):
            day = first + datetime.timedelta(days=i)
            r = QRectF((i % 7) * cw, self.HEAD + (i // 7) * ch, cw, ch)
            circle = QRectF(r.center().x() - 11, r.y() + 1, 22, 22)
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
            # only days of another month are faint; Sundays stay readable
            p.setPen(QColor(T.ACCENT_TEXT if day == today else
                            (T.FAINT if outside else (T.MUTED if day.weekday() in WEEKEND else T.TEXT))))
            p.setFont(_font(8.5, day == today))
            p.drawText(circle, Qt.AlignCenter, str(day.day))
            colours = list(dict.fromkeys(self.marks.get(day, [])))[:3]
            for k, c in enumerate(colours):
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(c))
                x = r.center().x() - (len(colours) - 1) * 3.5 + k * 7
                p.drawEllipse(QRectF(x - 2, r.y() + 24, 4, 4))
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

    def wheelEvent(self, e):
        # A touchpad sends many small steps: move one month per full notch, not per step.
        self._wheel = getattr(self, "_wheel", 0) + e.angleDelta().y()
        while abs(self._wheel) >= 120:
            step = -1 if self._wheel > 0 else 1
            self._wheel -= -120 * step
            self.month_step.emit(step)
        e.accept()
