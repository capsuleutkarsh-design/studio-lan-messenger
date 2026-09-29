"""Visual organisation views shared by the client directory and the server console.

PeopleGrid - people as cards in rows and columns, grouped by department (section shown on the card).
OrgChart   - top-down reporting chart (boxes and connector lines), with zoom.

Both are painted in one widget each (no child widget per person), so they stay fast with
thousands of people. ``users`` are dicts as sent by the server (id, name, department, section,
designation, level, manager_id, status, status_msg, status_emoji, avatar).
"""

import math

from PySide6.QtCore import QPoint, QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QSizePolicy, QToolTip, QWidget

from common import theme as T
from common.fmt import ELLIPSIS, SEP

# Painter for avatars: (painter, rect, name, key, status=, ring=, uid=). The client plugs in its own
# (with profile photos); the default draws coloured initials.
avatar_painter = None


def _paint_avatar(p, rect, u, ring):
    if avatar_painter:
        avatar_painter(p, rect, u["name"], u["name"], status=u.get("status"), ring=ring, uid=u["id"])
        return
    p.save()
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(T.avatar_color(u["name"])))
    p.drawEllipse(rect)
    f = QFont("Segoe UI")
    f.setPixelSize(max(9, int(rect.height() * 0.38)))
    f.setBold(True)
    p.setFont(f)
    p.setPen(QColor("#ffffff"))
    p.drawText(rect, Qt.AlignCenter, T.initials(u["name"]))
    p.restore()


def _font(px, bold=False, weight=None):
    f = QFont("Segoe UI")
    f.setPixelSize(px)
    if weight is not None:
        f.setWeight(weight)
    else:
        f.setBold(bold)
    return f


def matches(u, q):
    return not q or any(q in (u.get(k) or "").lower()
                        for k in ("name", "username", "department", "section", "designation", "title"))


def _sort_key(u):
    return (-(u.get("level") or 0), u["name"].lower())


def by_department(lead, people):
    """[(department, people)] when people come from two or more departments, else None.
    The lead's own department comes first and 'No department' last; the people keep their order."""
    depts, names = {}, {}
    for u in people:
        d = (u.get("department") or "").strip()
        names.setdefault(d.lower(), d)
        depts.setdefault(d.lower(), []).append(u)
    if len(depts) < 2:
        return None
    own = ((lead or {}).get("department") or "").strip().lower()
    order = sorted(depts, key=lambda d: (d != own, d == "", d))
    return [(names[d] or "No department", depts[d]) for d in order]


def department_groups(lead, children, kids):
    """The chart's children for lead: the people themselves, or one department label node per
    department when they come from several (kids gets each label's people)."""
    split = by_department(lead, children)
    if not split:
        return children
    groups = []
    for name, people in split:
        g = {"id": ("dept", lead["id"], name.lower()), "group": name, "count": len(people)}
        kids[g["id"]] = people
        groups.append(g)
    return groups


def branch_size(uid, kids, seen=None):
    """How many people are below uid in the chart (kids: uid or department-label id -> children)."""
    seen = set() if seen is None else seen
    n = 0
    for c in kids.get(uid, []):
        if c["id"] in seen:
            continue
        seen.add(c["id"])
        n += (0 if "group" in c else 1) + branch_size(c["id"], kids, seen)
    return n


def _meta():
    """Small text people still need to read (META; MUTED before a theme has set it)."""
    return T.META or T.MUTED


def _lead(u):
    """Leads and above (their designation is written a little stronger)."""
    return (u.get("level") or 0) >= 60


def _status_line(u):
    custom = " ".join(x for x in (u.get("status_emoji", ""), u.get("status_msg", "")) if x)
    return custom


# ======================================================================== card grid
class PeopleGrid(QWidget):
    """Cards in rows and columns; the number of columns follows the width."""
    open_person = Signal(int)                 # double-click or the card's Chat button
    person_menu = Signal(int, QPoint)
    person_selected = Signal(int)
    edit_me = Signal()                        # the button on my own card (me_action_text)

    # cards are CARD_MIN_W..CARD_MAX_W wide: the columns share the width instead of leaving a strip on the right
    CARD_W, CARD_H, GAP, MARGIN = 210, 184, 14, 4
    CARD_MIN_W, CARD_MAX_W = 190, 240
    HEAD_H, HEAD_GAP, DEPT_GAP = 34, 12, 22   # department heading, heading -> its cards, cards -> next heading

    def __init__(self, parent=None, action_text="Chat", me_action_text=""):
        super().__init__(parent)
        self.setMouseTracking(True)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        self.action_text = action_text
        self.me_action_text = me_action_text  # e.g. "Set status" on my own card ('' = no button there)
        self.card_w = self.CARD_W
        self.groups = []                      # [(dept, [(section, [users])])]
        self.me_id = None
        self.items = []                       # [(kind, QRect, payload)]
        self.hover = None
        self.selected = None
        self._width = 0

    def set_people(self, users, query="", me_id=None):
        q = query.strip().lower()
        self.me_id = me_id
        depts = {}
        for u in users:
            if matches(u, q):
                depts.setdefault(u.get("department") or "No department", {}) \
                     .setdefault(u.get("section") or "", []).append(u)
        self.groups = []
        for dept in sorted(depts, key=lambda d: (d == "No department", d.lower())):
            # one flowing row of cards per department (the section is written on each card): a row per
            # section left mostly empty rows when sections are small
            people = sorted((u for us in depts[dept].values() for u in us), key=_sort_key)
            self.groups.append((dept, [("", people)]))
        self._relayout()

    def count(self):
        return sum(len(p) for _, secs in self.groups for _, p in secs)

    # ---------------------------------------------------------------- layout
    def _columns(self, w):
        """(columns, card width) for the width w: as many columns as fit at the smallest card width, then the
        cards grow to share the row (up to CARD_MAX_W)."""
        inner = max(w - 2 * self.MARGIN, self.CARD_MIN_W)
        cols = max(1, (inner + self.GAP) // (self.CARD_MIN_W + self.GAP))
        card_w = min(self.CARD_MAX_W, (inner - (cols - 1) * self.GAP) // cols)
        return cols, max(card_w, min(self.CARD_MIN_W, inner))

    def _relayout(self):
        w = max(self.width(), self.CARD_MIN_W + 2 * self.MARGIN)
        cols, self.card_w = self._columns(w)
        self.items = []
        y = 0
        for n, (dept, sections) in enumerate(self.groups):
            if n:
                y += self.DEPT_GAP
            people = [u for _, us in sections for u in us]
            online = sum(1 for u in people if u.get("status", "offline") != "offline")
            self.items.append(("dept", QRect(self.MARGIN, y, w - 2 * self.MARGIN, self.HEAD_H),
                               (dept, f"{online}/{len(people)} online")))
            y += self.HEAD_H + self.HEAD_GAP
            for section, users in sections:
                if section:
                    self.items.append(("section", QRect(self.MARGIN, y, w - 2 * self.MARGIN, 26),
                                       (section, len(users))))
                    y += 30
                for i, u in enumerate(users):
                    r, c = divmod(i, cols)
                    rect = QRect(self.MARGIN + c * (self.card_w + self.GAP), y + r * (self.CARD_H + self.GAP),
                                 self.card_w, self.CARD_H)
                    self.items.append(("card", rect, u))
                rows = math.ceil(len(users) / cols)
                y += rows * (self.CARD_H + self.GAP) - self.GAP if rows else 0
        self.setMinimumHeight(max(y + self.MARGIN, 60))
        self.update()

    def resizeEvent(self, e):
        if e.size().width() != self._width:
            self._width = e.size().width()
            self._relayout()
        super().resizeEvent(e)

    def sizeHint(self):
        return QSize(self.CARD_W * 3, self.minimumHeight())

    def _has_button(self, u):
        return bool(self.me_action_text if u["id"] == self.me_id else self.action_text)

    # ------------------------------------------------------------- painting
    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        clip = e.rect()
        for kind, rect, data in self.items:
            if not rect.intersects(clip):
                continue
            if kind == "dept":
                p.setFont(_font(12, True))
                p.setPen(QColor(T.TEXT))
                name_w = QFontMetrics(p.font()).horizontalAdvance(data[0].upper()) + 10
                p.drawText(rect.adjusted(2, 8, 0, 0), Qt.AlignLeft | Qt.AlignVCenter, data[0].upper())
                p.setFont(_font(11))
                p.setPen(QColor(T.MUTED))
                p.drawText(rect.adjusted(2 + name_w, 8, 0, 0), Qt.AlignLeft | Qt.AlignVCenter,
                           SEP.lstrip() + data[1])
                p.setPen(QPen(QColor(T.BORDER), 1))
                p.drawLine(rect.left(), rect.bottom(), rect.right(), rect.bottom())
            elif kind == "section":
                p.setFont(_font(11, True))
                p.setPen(QColor(T.ACCENT))
                p.drawText(rect.adjusted(2, 0, 0, 0), Qt.AlignLeft | Qt.AlignVCenter, f"# {data[0]}{SEP}{data[1]}")
            else:
                self._paint_card(p, rect, data)

    def _paint_card(self, p, rect, u):
        uid = u["id"]
        hover, sel, me = self.hover == uid, self.selected == uid, uid == self.me_id
        r = QRectF(rect).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setPen(QPen(QColor(T.ACCENT if sel else (T.ACCENT_FOCUS if hover else T.BORDER)), 1.5 if sel else 1))
        p.setBrush(QColor(T.ACCENT_SOFT if me else (T.SURFACE if hover else T.PANEL)))
        p.drawRoundedRect(r, 14, 14)
        av = QRect(rect.center().x() - 30, rect.top() + 16, 60, 60)
        _paint_avatar(p, av, u, T.SURFACE if hover else T.PANEL)
        inner = rect.adjusted(10, 0, -10, 0)
        p.setFont(_font(13, True))
        p.setPen(QColor(T.TEXT))
        name = u["name"] + (" (you)" if me else "")
        p.drawText(QRect(inner.left(), rect.top() + 84, inner.width(), 20), Qt.AlignCenter,
                   QFontMetrics(p.font()).elidedText(name, Qt.ElideRight, inner.width()))
        # a lead's designation: a little stronger, in the text colour (the accent colour means "a link")
        lead = _lead(u)
        p.setFont(_font(11, weight=QFont.DemiBold if lead else None))
        p.setPen(QColor(T.TEXT if lead else T.MUTED))
        role = SEP.join(x for x in (u.get("designation") or u.get("title"), u.get("section")) if x)
        p.drawText(QRect(inner.left(), rect.top() + 104, inner.width(), 16), Qt.AlignCenter,
                   QFontMetrics(p.font()).elidedText(role, Qt.ElideRight, inner.width()))
        p.setFont(_font(11))
        status = u.get("status", "offline")
        line = _status_line(u) or T.STATUS_LABELS.get(status, status)
        p.setPen(QColor(_meta() if status == "offline" and not _status_line(u) else T.MUTED))
        p.drawText(QRect(inner.left(), rect.top() + 121, inner.width(), 16), Qt.AlignCenter,
                   QFontMetrics(p.font()).elidedText(line, Qt.ElideRight, inner.width()))
        text = self.me_action_text if me else self.action_text
        if text:
            btn = self._button_rect(rect)
            p.setPen(Qt.NoPen)
            # my own card has the soft accent fill already: its button is the panel colour instead
            p.setBrush(QColor(T.ACCENT if hover else (T.PANEL if me else T.ACCENT_SOFT)))
            p.drawRoundedRect(QRectF(btn), 10, 10)
            p.setFont(_font(11, True))
            p.setPen(QColor(T.ACCENT_TEXT if hover else T.ACCENT))
            p.drawText(btn, Qt.AlignCenter, text)

    def _button_rect(self, rect):
        return QRect(rect.center().x() - 48, rect.bottom() - 38, 96, 26)

    # ------------------------------------------------------------ mouse
    def _card_at(self, pos):
        for kind, rect, data in self.items:
            if kind == "card" and rect.contains(pos):
                return rect, data
        return None, None

    def mouseMoveEvent(self, e):
        rect, u = self._card_at(e.position().toPoint())
        uid = u["id"] if u else None
        if uid != self.hover:
            self.hover = uid
            self.setCursor(Qt.PointingHandCursor if uid else Qt.ArrowCursor)
            self.update()

    def leaveEvent(self, e):
        self.hover = None
        self.update()

    def mousePressEvent(self, e):
        rect, u = self._card_at(e.position().toPoint())
        if not u:
            return
        self.selected = u["id"]
        self.update()
        self.person_selected.emit(u["id"])
        if e.button() == Qt.LeftButton and self._has_button(u) \
                and self._button_rect(rect).contains(e.position().toPoint()):
            if u["id"] == self.me_id:
                self.edit_me.emit()
            else:
                self.open_person.emit(u["id"])

    def mouseDoubleClickEvent(self, e):
        rect, u = self._card_at(e.position().toPoint())
        if u and u["id"] != self.me_id:
            self.open_person.emit(u["id"])

    def contextMenuEvent(self, e):
        rect, u = self._card_at(e.pos())
        if u:
            self.person_menu.emit(u["id"], e.globalPos())


# ======================================================================== org chart
class OrgChart(QWidget):
    """Reporting lines as a top-down chart. Big teams without sub-teams are stacked in columns.

    When someone's direct reports come from two or more departments, each department gets its own
    branch under that person: a small department label with that department's people below it.
    """
    open_person = Signal(int)
    person_menu = Signal(int, QPoint)
    person_selected = Signal(int)
    zoom_changed = Signal(float)

    BOX_W, BOX_H, HGAP, VGAP, STACK_GAP, STACK_ROWS = 200, 72, 20, 58, 14, 6
    GROUP_H = 30                  # department label between a lead and that department's people
    PAD = 30
    # after the "not in a reporting line" heading: where 'Reports to' is set. The console's wording is the default;
    # the client sets its own (or '' for people who can't change it).
    LONER_HINT = "set 'Reports to' on the Users page"

    def __init__(self, parent=None, loner_hint=None):
        super().__init__(parent)
        self.setMouseTracking(True)
        self.loner_hint = self.LONER_HINT if loner_hint is None else loner_hint
        self.zoom = 1.0
        self.user_zoomed = False      # zoomed by hand (wheel, buttons): the browser stops fitting the chart
        self.boxes = {}               # uid -> QRectF (chart units)
        self.group_boxes = []         # [(QRectF, department label node)]
        self.lines = []               # [(QPointF...)] polylines
        self.labels = []              # [(QRectF, text)]
        self.people = {}
        self.reports = {}
        self.matches = set()
        self.me_id = None
        self.hover = None
        self.selected = None
        self.bounds = QRectF(0, 0, 400, 200)

    # ---------------------------------------------------------------- layout
    def set_people(self, users, query="", me_id=None):
        from PySide6.QtCore import QPointF
        self.me_id = me_id
        q = query.strip().lower()
        self.people = {u["id"]: u for u in users}
        kids = {}
        for u in users:
            m = u.get("manager_id")
            if m == u["id"] or m not in self.people:
                m = None
            kids.setdefault(m, []).append(u)
        for k in kids.values():
            k.sort(key=_sort_key)
        self.reports = {uid: len(kids.get(uid, [])) for uid in self.people}
        self.matches = {u["id"] for u in users if q and matches(u, q)}
        for uid, u in self.people.items():
            if kids.get(uid):
                kids[uid] = department_groups(u, kids[uid], kids)
        for k in list(kids.values()):             # a department pill counts everyone in its branch, not only
            for g in k:                           # the lead's direct reports (people read it as the team size)
                if "group" in g:
                    g["count"] = branch_size(g["id"], kids)
        self.boxes, self.group_boxes, self.lines, self.labels = {}, [], [], []
        W, H = self.BOX_W, self.BOX_H

        def leaf(u):
            return not kids.get(u["id"])

        width_cache = {}

        def width(u, seen=frozenset()):
            if u["id"] in width_cache:
                return width_cache[u["id"]]
            ch = [c for c in kids.get(u["id"], []) if c["id"] not in seen]
            if not ch:
                w = W
            elif all(leaf(c) for c in ch) and len(ch) > 3:
                cols = math.ceil(len(ch) / self.STACK_ROWS)
                w = max(W, cols * W + (cols - 1) * self.HGAP)
            else:
                w = max(W, sum(width(c, seen | {u["id"]}) for c in ch) + self.HGAP * (len(ch) - 1))
            width_cache[u["id"]] = w
            return w

        def place(u, left, top, seen=frozenset()):
            w = width(u, seen)
            h = self.GROUP_H if "group" in u else H
            box = QRectF(left + (w - W) / 2, top, W, h)
            if "group" in u:
                self.group_boxes.append((box, u))
            else:
                self.boxes[u["id"]] = box
            ch = [c for c in kids.get(u["id"], []) if c["id"] not in seen]
            if not ch:
                return top + h
            px, pb = box.center().x(), box.bottom()
            ctop = top + h + self.VGAP
            bottom = ctop
            if all(leaf(c) for c in ch) and len(ch) > 3:
                cols = math.ceil(len(ch) / self.STACK_ROWS)
                span = cols * W + (cols - 1) * self.HGAP
                x0 = left + (w - span) / 2
                mid = pb + self.VGAP / 2
                first_spine = x0 - 10
                last_spine = x0 + (cols - 1) * (W + self.HGAP) - 10
                self.lines.append([QPointF(px, pb), QPointF(px, mid)])
                self.lines.append([QPointF(min(px, first_spine), mid), QPointF(max(px, last_spine), mid)])
                for i, c in enumerate(ch):
                    col, row = divmod(i, self.STACK_ROWS)
                    cx = x0 + col * (W + self.HGAP)
                    cy = ctop + row * (H + self.STACK_GAP)
                    self.boxes[c["id"]] = QRectF(cx, cy, W, H)
                    spine = cx - 10
                    self.lines.append([QPointF(spine, mid), QPointF(spine, cy + H / 2), QPointF(cx, cy + H / 2)])
                    bottom = max(bottom, cy + H)
                return bottom
            mid = pb + self.VGAP / 2
            x = left + (w - (sum(width(c, seen | {u["id"]}) for c in ch) + self.HGAP * (len(ch) - 1))) / 2
            centers = []
            for c in ch:
                cw = width(c, seen | {u["id"]})
                centers.append(x + cw / 2)
                bottom = max(bottom, place(c, x, ctop, seen | {u["id"]}))
                x += cw + self.HGAP
            self.lines.append([QPointF(px, pb), QPointF(px, mid)])
            if len(centers) > 1:
                self.lines.append([QPointF(centers[0], mid), QPointF(centers[-1], mid)])
            for cx in centers:
                self.lines.append([QPointF(cx, mid), QPointF(cx, ctop)])
            return bottom

        roots = kids.get(None, [])
        trees = [u for u in roots if not leaf(u)]
        loners = [u for u in roots if leaf(u)]
        x, y0, bottom = 0.0, 0.0, 0.0
        for u in trees:
            bottom = max(bottom, place(u, x, y0))
            x += width(u) + self.HGAP * 3
        total_w = max(x - self.HGAP * 3, W * 5 + self.HGAP * 4)
        if loners:
            top = (bottom + self.VGAP) if trees else 0.0
            hint = (self.loner_hint or "").strip()
            if hint.startswith("(") and hint.endswith(")"):
                hint = hint[1:-1]                 # given with its brackets already
            hint = f"  ({hint})" if hint else ""
            self.labels.append((QRectF(0, top, total_w, 24), f"NOT IN A REPORTING LINE{SEP}{len(loners)}{hint}"))
            top += 32
            per_row = max(1, int((total_w + self.HGAP) // (W + self.HGAP)))
            for i, u in enumerate(loners):
                r, c = divmod(i, per_row)
                self.boxes[u["id"]] = QRectF(c * (W + self.HGAP), top + r * (H + self.STACK_GAP), W, H)
            bottom = top + math.ceil(len(loners) / per_row) * (H + self.STACK_GAP)
        self.bounds = QRectF(0, 0, max(total_w, x), max(bottom, H))
        self._resize()

    def _resize(self):
        z = self.zoom
        self.setFixedSize(int((self.bounds.width() + 2 * self.PAD) * z), int((self.bounds.height() + 2 * self.PAD) * z))
        self.update()

    def set_zoom(self, z):
        self.zoom = max(0.3, min(2.0, z))
        self._resize()
        self.zoom_changed.emit(self.zoom)

    def fit_zoom(self, viewport_size):
        if self.bounds.width() <= 0:
            return
        z = min((viewport_size.width() - 10) / (self.bounds.width() + 2 * self.PAD),
                (viewport_size.height() - 10) / (self.bounds.height() + 2 * self.PAD), 1.0)
        self.set_zoom(z)

    def root_rect(self):
        """Widget coordinates of the top person of the first (widest) reporting tree, or None."""
        tops = [b for uid, b in self.boxes.items() if self.reports.get(uid)]
        if not tops:
            return None
        return self._to_widget(min(tops, key=lambda b: (b.top(), b.left())))

    def first_match_rect(self):
        """Widget coordinates of the first search match (to scroll to it)."""
        for uid in self.matches:
            b = self.boxes.get(uid)
            if b:
                return self._to_widget(b)
        return None

    def _to_widget(self, r):
        z = self.zoom
        return QRect(int((r.x() + self.PAD) * z), int((r.y() + self.PAD) * z), int(r.width() * z), int(r.height() * z))

    # ------------------------------------------------------------- painting
    def paintEvent(self, e):
        p = QPainter(self)
        p.fillRect(e.rect(), QColor(T.BG))
        p.setRenderHint(QPainter.Antialiasing)
        p.scale(self.zoom, self.zoom)
        p.translate(self.PAD, self.PAD)
        p.setPen(QPen(QColor(T.SCROLL), 1.5))
        for pts in self.lines:
            for a, b in zip(pts, pts[1:]):
                p.drawLine(a, b)
        p.setFont(_font(11, True))
        p.setPen(QColor(T.MUTED))
        for rect, text in self.labels:
            p.drawText(rect, Qt.AlignLeft | Qt.AlignVCenter, text)
        for box, g in self.group_boxes:
            self._paint_group(p, box, g)
        for uid, box in self.boxes.items():
            self._paint_box(p, box, self.people[uid])

    def _paint_group(self, p, box, g):
        """Department label under a lead whose team spans several departments: its name and how many people
        are in that branch. Muted text - it is a label, not a link."""
        p.setPen(QPen(QColor(T.BORDER), 1))
        p.setBrush(QColor(T.SURFACE))
        p.drawRoundedRect(box, box.height() / 2, box.height() / 2)
        p.setFont(_font(11, True))
        fm = QFontMetrics(p.font())
        count = f"{SEP}{g['count']}"
        name = fm.elidedText(g["group"].upper(), Qt.ElideRight, int(box.width() - 28 - fm.horizontalAdvance(count)))
        p.setPen(QColor(T.MUTED))
        p.drawText(box, Qt.AlignCenter, name + count)

    def _paint_box(self, p, box, u):
        uid = u["id"]
        hit = uid in self.matches
        sel, hover, me = self.selected == uid, self.hover == uid, uid == self.me_id
        pen = QPen(QColor(T.ACCENT if (hit or sel) else (T.ACCENT_FOCUS if hover else T.BORDER)), 2 if (hit or sel) else 1)
        p.setPen(pen)
        p.setBrush(QColor(T.ACCENT_SOFT if me else (T.SURFACE if hover else T.PANEL)))
        p.drawRoundedRect(box, 12, 12)
        if _lead(u):                          # leads and above: the box's top edge in the accent colour
            p.save()
            edge = QPainterPath()
            edge.addRoundedRect(box, 12, 12)
            p.setClipPath(edge)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(T.ACCENT))
            p.drawRect(QRectF(box.left(), box.top(), box.width(), 3))
            p.restore()
        av = QRect(int(box.left()) + 12, int(box.top()) + 14, 40, 40)
        _paint_avatar(p, av, u, T.SURFACE if hover else T.PANEL)
        tx = box.left() + 62
        tw = box.width() - 70
        p.setFont(_font(12, True))
        p.setPen(QColor(T.TEXT))
        p.drawText(QRectF(tx, box.top() + 9, tw, 18), Qt.AlignLeft | Qt.AlignVCenter,
                   QFontMetrics(p.font()).elidedText(u["name"], Qt.ElideRight, int(tw)))
        # designation: a lead's a little stronger in the text colour (accent is for links); department in META
        p.setFont(_font(11, weight=QFont.DemiBold if _lead(u) else None))
        p.setPen(QColor(T.TEXT if _lead(u) else T.MUTED))
        p.drawText(QRectF(tx, box.top() + 27, tw, 16), Qt.AlignLeft | Qt.AlignVCenter,
                   QFontMetrics(p.font()).elidedText(u.get("designation") or "", Qt.ElideRight, int(tw)))
        p.setFont(_font(11))
        p.setPen(QColor(_meta()))
        where = SEP.join(x for x in (u.get("department"), u.get("section")) if x)
        p.drawText(QRectF(tx, box.top() + 43, tw, 16), Qt.AlignLeft | Qt.AlignVCenter,
                   QFontMetrics(p.font()).elidedText(where, Qt.ElideRight, int(tw)))
        n = self.reports.get(uid, 0)
        if n:                                                 # team size badge on the bottom edge
            p.setFont(_font(10, True))
            text = f"{n} report{'s' if n != 1 else ''}"
            bw = QFontMetrics(p.font()).horizontalAdvance(text) + 14
            badge = QRectF(box.center().x() - bw / 2, box.bottom() - 9, bw, 18)
            p.setPen(QPen(QColor(T.BORDER), 1))
            p.setBrush(QColor(T.SURFACE))
            p.drawRoundedRect(badge, 9, 9)
            p.setPen(QColor(T.MUTED))
            p.drawText(badge, Qt.AlignCenter, text)

    # ------------------------------------------------------------ mouse
    def _uid_at(self, pos):
        z = self.zoom
        x, y = pos.x() / z - self.PAD, pos.y() / z - self.PAD
        for uid, box in self.boxes.items():
            if box.contains(x, y):
                return uid
        return None

    def mouseMoveEvent(self, e):
        uid = self._uid_at(e.position())
        if uid != self.hover:
            self.hover = uid
            self.setCursor(Qt.PointingHandCursor if uid else Qt.ArrowCursor)
            u = self.people.get(uid)
            if u:
                status = u.get("status", "offline")
                QToolTip.showText(e.globalPosition().toPoint(),
                                  f"{u['name']}\n{_status_line(u) or T.STATUS_LABELS.get(status, status)}", self)
            self.update()

    def leaveEvent(self, e):
        self.hover = None
        self.update()

    def mousePressEvent(self, e):
        uid = self._uid_at(e.position())
        self.selected = uid
        self.update()
        if uid:
            self.person_selected.emit(uid)

    def mouseDoubleClickEvent(self, e):
        uid = self._uid_at(e.position())
        if uid and uid != self.me_id:
            self.open_person.emit(uid)

    def contextMenuEvent(self, e):
        uid = self._uid_at(e.pos())
        if uid:
            self.person_menu.emit(uid, e.globalPos())

    def wheelEvent(self, e):
        if e.modifiers() & Qt.ControlModifier:
            self.user_zoomed = True
            self.set_zoom(self.zoom * (1.1 if e.angleDelta().y() > 0 else 1 / 1.1))
            e.accept()
        else:
            super().wheelEvent(e)


# ======================================================================== browser
class OrgBrowser(QWidget):
    """Search + [Cards | Org chart | List] switch + department filter, used by the client and the console.

    loner_hint: the note after the chart's "Not in a reporting line" heading (None: the console's wording, '': no
    note - for people who can't set 'Reports to'). me_action_text: a button on my own card (e.g. "Set status"),
    which emits edit_me."""
    open_person = Signal(int)
    person_menu = Signal(int, QPoint)
    view_changed = Signal(str)
    edit_me = Signal()

    VIEWS = (("cards", "Cards"), ("chart", "Org chart"), ("list", "List"))
    MIN_FIT_ZOOM = 0.6            # the chart shrinks to fit the width when it opens, but never below this

    def __init__(self, parent=None, action_text="Chat", view="cards", loner_hint=None, me_action_text=""):
        super().__init__(parent)
        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import (
            QComboBox, QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton, QScrollArea, QStackedWidget, QVBoxLayout,
        )
        from common import orgtree
        from common.icons import icon
        self.orgtree = orgtree
        self.users, self.me_id = [], None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        from PySide6.QtWidgets import QGridLayout
        self.bar = bar = QGridLayout()
        bar.setHorizontalSpacing(8)
        bar.setVerticalSpacing(8)
        tools = QWidget()
        self.tools = QHBoxLayout(tools)
        self.tools.setContentsMargins(0, 0, 0, 0)
        self.tools.setSpacing(8)
        self.tools_widget = tools
        self.search = QLineEdit()
        self.search.setPlaceholderText(f"Search people, teams or roles{ELLIPSIS}")
        self.search.setToolTip("Search by name, username, department, section or designation")
        self.search.setMinimumWidth(240)          # the list view's extra drop-down must not squeeze it to a stub
        self.search.addAction(icon("search", _meta(), 16), QLineEdit.LeadingPosition)
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(lambda _: self.refresh())
        bar.addWidget(self.search, 0, 0)
        self.dept = QComboBox()
        self.dept.setMinimumWidth(150)
        self.dept.currentIndexChanged.connect(lambda _: self.refresh())
        self.tools.addWidget(self.dept, 1)
        self.list_mode = QComboBox()
        self.list_mode.addItem("By department & section", "department")
        self.list_mode.addItem("By reporting line", "reporting")
        self.list_mode.currentIndexChanged.connect(lambda _: self.refresh())
        self.tools.addWidget(self.list_mode)
        self.view_buttons = {}
        for key, label in self.VIEWS:
            b = QPushButton(label)
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            T.polish(b, chip=True, tall=True)     # as tall as the search box and the drop-downs next to them
            b.clicked.connect(lambda _=False, k=key: self.set_view(k))
            self.tools.addWidget(b)
            self.view_buttons[key] = b
        bar.addWidget(tools, 0, 1)
        bar.setColumnStretch(0, 1)
        self._narrow = False
        lay.addLayout(bar)

        self.stack = QStackedWidget()
        # cards
        self.grid = PeopleGrid(action_text=action_text, me_action_text=me_action_text)
        self.grid_area = QScrollArea()
        self.grid_area.setWidgetResizable(True)
        self.grid_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.grid_area.setWidget(self.grid)
        self.grid.setAutoFillBackground(False)
        self.stack.addWidget(self.grid_area)
        # chart: a rounded frame around a plain scroll area (a scroll area's own square viewport painted over
        # rounded corners and broke the frame's edge)
        chart_page = QWidget()
        cl = QVBoxLayout(chart_page)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(6)
        self.chart = OrgChart(loner_hint=loner_hint)
        self.chart_frame = QFrame()
        self.chart_frame.setObjectName("orgChartFrame")
        self.chart_frame.setStyleSheet(f"#orgChartFrame {{ background: {T.BG}; border: 1px solid {T.HAIR};"
                                       f" border-radius: {T.RADIUS_L}px; }}")
        fl = QVBoxLayout(self.chart_frame)
        fl.setContentsMargins(5, 5, 5, 5)          # clear of the rounded corners
        self.chart_area = QScrollArea()
        self.chart_area.setFrameShape(QFrame.NoFrame)
        self.chart_area.setWidgetResizable(False)
        self.chart_area.setAlignment(Qt.AlignHCenter | Qt.AlignTop)
        self.chart_area.setWidget(self.chart)
        self.chart_area.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        self.chart_area.viewport().setAutoFillBackground(False)
        T.bg_pane(self.chart_area.viewport())
        fl.addWidget(self.chart_area)
        cl.addWidget(self.chart_frame, 1)
        zb = QHBoxLayout()
        hint = QLabel(f"Ctrl + mouse wheel to zoom{SEP}double-click someone to {action_text.lower()}")
        hint.setStyleSheet(f"color: {_meta()}; font-size: {T.pt(T.FONT_S)};")
        zb.addWidget(hint, 1)

        def by_hand(fn):
            def run():
                self.chart.user_zoomed = True     # from now on the chart keeps the zoom it was given
                fn()
            return run
        for text, fn in (("−", lambda: self.chart.set_zoom(self.chart.zoom / 1.2)),
                         ("100%", lambda: self.chart.set_zoom(1.0)),
                         ("+", lambda: self.chart.set_zoom(self.chart.zoom * 1.2)),
                         ("Fit", lambda: self.chart.fit_zoom(self.chart_area.viewport().size()))):
            b = QPushButton(text)
            T.polish(b, chip=True)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(by_hand(fn))
            zb.addWidget(b)
            if text == "100%":
                self.zoom_label = b
                b.setToolTip("Back to 100%")
        self.chart.zoom_changed.connect(lambda z: self.zoom_label.setText(f"{round(z * 100)}%"))
        cl.addLayout(zb)
        self.stack.addWidget(chart_page)
        # list
        self.tree = orgtree.make_tree()
        self.tree.itemDoubleClicked.connect(lambda it: it.data(0, Qt.UserRole) and self.open_person.emit(
            it.data(0, Qt.UserRole)))
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._tree_menu)
        self.stack.addWidget(self.tree)
        lay.addWidget(self.stack, 1)

        for w in (self.grid, self.chart):
            w.open_person.connect(self.open_person.emit)
            w.person_menu.connect(self.person_menu.emit)
        self.grid.edit_me.connect(self.edit_me.emit)
        # fit the chart to the width again when the window changes size (until someone zooms by hand)
        self._fit_timer = QTimer(self, singleShot=True, interval=120, timeout=self._auto_fit)
        self.view = None
        self.set_view(view if view in dict(self.VIEWS) else "cards", announce=False)

    def set_loner_hint(self, text):
        """The note after the chart's "Not in a reporting line" heading ('' for none)."""
        if text != self.chart.loner_hint:
            self.chart.loner_hint = text
            if self.view == "chart":
                self.refresh()

    def resizeEvent(self, e):
        """Narrow window (e.g. compact view): filters and view switch go on a second row."""
        narrow = e.size().width() < 640
        if narrow != self._narrow:
            self._narrow = narrow
            self.bar.removeWidget(self.tools_widget)
            if narrow:
                self.bar.addWidget(self.tools_widget, 1, 0)
            else:
                self.bar.addWidget(self.tools_widget, 0, 1)
        super().resizeEvent(e)
        if self.view == "chart" and not self.chart.user_zoomed:
            self._fit_timer.start()

    def showEvent(self, e):
        super().showEvent(e)
        if self.view == "chart":
            self._fit_timer.start()

    def _tree_menu(self, pos):
        it = self.tree.itemAt(pos)
        uid = it.data(0, Qt.UserRole) if it else None
        if uid:
            self.person_menu.emit(uid, self.tree.viewport().mapToGlobal(pos))

    def set_view(self, key, announce=True):
        self.view = key
        for k, b in self.view_buttons.items():
            b.setChecked(k == key)
        self.stack.setCurrentIndex([k for k, _ in self.VIEWS].index(key))
        self.list_mode.setVisible(key == "list")
        self.refresh()
        if key == "chart":
            from PySide6.QtCore import QTimer
            QTimer.singleShot(0, self._fit_and_scroll)
        if announce:
            self.view_changed.emit(key)

    def set_people(self, users, me_id=None):
        self.users, self.me_id = list(users), me_id
        current = self.dept.currentData()
        depts = sorted({u.get("department") for u in self.users if u.get("department")}, key=str.lower)
        self.dept.blockSignals(True)
        self.dept.clear()
        self.dept.addItem("All departments", "")
        for d in depts:
            self.dept.addItem(d, d)
        self.dept.setCurrentIndex(max(0, self.dept.findData(current or "")))
        self.dept.blockSignals(False)
        self.refresh()

    def refresh(self):
        dept = self.dept.currentData() or ""
        users = [u for u in self.users if not dept or u.get("department") == dept]
        q = self.search.text()
        if self.view == "cards":
            self.grid.set_people(users, q, self.me_id)
        elif self.view == "chart":
            self.chart.set_people(users, q, self.me_id)
            self._fit_and_scroll()
        else:
            mode = self.list_mode.currentData()
            self.orgtree.fill(self.tree, users, mode, q, me_id=self.me_id)
            # by department & section the department is already the group (and the section its sub-group):
            # a "Department / Section" column would only repeat it on every row
            self.tree.setColumnHidden(2, mode == "department")

    def update_views(self):
        self.grid.update()
        self.chart.update()

    def _fit_and_scroll(self):
        self._auto_fit()
        self._scroll_to_match()

    def _auto_fit(self):
        """Until someone zooms by hand, the chart shows as big as possible up to 100%: shrunk (down to
        MIN_FIT_ZOOM) when it is wider than the view, so no branch starts off-screen. When even that is too wide,
        the view starts centred on the top person instead of at the left edge."""
        if self.view != "chart" or self.chart.user_zoomed or not self.chart_area.isVisible():
            return
        view = self.chart_area.viewport().size()
        if view.width() < 80:
            return                              # not laid out yet
        full = self.chart.bounds.width() + 2 * self.chart.PAD
        z = min(1.0, max(self.MIN_FIT_ZOOM, (view.width() - 10) / full)) if full > 0 else 1.0
        if abs(z - self.chart.zoom) > 0.005:
            self.chart.set_zoom(z)
        if self.chart.width() > view.width():
            root = self.chart.root_rect()
            if root is not None:
                bar = self.chart_area.horizontalScrollBar()
                bar.setRange(0, max(0, self.chart.width() - view.width()))
                bar.setValue(int(root.center().x() - view.width() / 2))

    def _scroll_to_match(self):
        r = self.chart.first_match_rect() if self.search.text().strip() else None
        if r:
            self.chart_area.ensureVisible(r.center().x(), r.center().y(), r.width(), r.height() * 2)
