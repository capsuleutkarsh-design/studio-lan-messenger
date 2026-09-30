"""Full-page views: home, news (announcements), the organisation and file transfers."""

import datetime
import os

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame, QGridLayout, QHBoxLayout, QLabel, QProgressBar, QPushButton, QScrollArea, QSizePolicy, QVBoxLayout,
    QWidget,
)

from common import protocol as P
from common import theme as T
from common.icons import icon, pixmap
from client.ui.widgets import (ELLIPSIS, SEP, ElidedLabel, EmptyState, IconButton, clip, day_word, first_name,
                               fmt_date, fmt_time, fmt_when, linkify, open_file, open_link, open_path, plain,
                               rich_safe, show_in_folder)

# Rows inside the Home cards start this far in from the card's heading (ConvItem.INSET is set to it there too),
# so every card's content lines up with its heading.
ROW_INSET = 4


class PageHeader(QFrame):
    def __init__(self, title, subtitle=""):
        super().__init__()
        self.setFixedHeight(68)
        self.setStyleSheet(f"PageHeader {{ background: {T.BG}; }}")
        self.lay = QHBoxLayout(self)
        # the page's own gutter on both sides: header buttons end where the cards below them end
        self.lay.setContentsMargins(T.PAGE_GUTTER, 10, T.PAGE_GUTTER, 10)
        self.lay.setSpacing(T.SPACE_S)
        col = QVBoxLayout()
        col.setSpacing(0)
        t = plain(QLabel(title))
        t.setStyleSheet(f"font-size: {T.pt(T.FONT_XL)}; font-weight: 700;")
        col.addStretch(1)
        col.addWidget(t)
        if subtitle:
            s = plain(QLabel(subtitle))
            s.setStyleSheet(f"color: {T.MUTED}; font-size: {T.pt(T.FONT_S)};")
            col.addWidget(s)
        col.addStretch(1)
        self.lay.addLayout(col, 1)


def scroll_column():
    area = QScrollArea()
    area.setWidgetResizable(True)
    area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    w = QWidget()
    T.bg_pane(w)
    lay = QVBoxLayout(w)
    lay.setContentsMargins(T.PAGE_GUTTER, 16, T.PAGE_GUTTER, 16)
    lay.setSpacing(10)
    lay.addStretch(1)
    area.setWidget(w)
    return area, lay


TIPS = [
    ("hash", "Shot names are links", "Write FAL_030 in a message: a click on it shows everything said about that shot."),
    ("pin", "Keep chats at the top", "Right-click a chat in the list → Pin to the top. Up to 10 chats stay above the rest."),
    ("target", "Focus time", "Click your photo → Focus time: only @mentions, your lead and people you choose get through."),
    ("list", "All the shortcuts", "Press Ctrl+/ to see every keyboard shortcut. Ctrl + and Ctrl − change the text size."),
    ("sticker", "Stickers", "Click the sticker button next to the emoji button — 459 desi, filmy, festival and mood stickers."),
    ("chart", "Quick polls", "Where for lunch? Which dailies slot? Attach button → Create a poll."),
    ("file", "Nuke scripts", "Paste a Nuke script straight into a chat. Others click Copy and paste it into Nuke."),
    ("pin", "Pin what matters", "Right-click a message → Pin to the top, so nobody misses the dailies time."),
    ("smile", "React instead of replying", "Hover a message and click 🙂 — a 👍 is quicker than 'ok noted'."),
    ("folder", "Send whole folders", "Attach → Send a folder: image sequences are zipped and extracted with one click."),
    ("search", "Find anything", "Ctrl+K jumps to a person or room, Ctrl+F searches every message you have."),
    ("screen", "Show your screen", "In a direct chat, click the screen button to share your screen (view only)."),
    ("bell_off", "Mute noisy rooms", "Mute a busy room from its ••• menu — you'll still hear when someone @mentions you."),
]

# The Calendar card's rows: an outline icon per kind of entry (calendar_views.KIND_ICONS, in the kind's colour) in a
# fixed slot, and the entry's name without the emoji the plain-text lists put in front
KIND_NAMES = {"meeting": "Meeting", "event": "Personal event", "note": "Note", "deadline": "Deadline",
              "holiday": "Holiday", "leave": "Leave", "birthday": "Birthday", "anniversary": "Work anniversary"}


def _entry_icon(e):
    from client.ui.calendar_views import KIND_ICONS
    return getattr(e, "icon", None) or KIND_ICONS.get(e.kind, "calendar")


def _entry_title(e):
    """The entry's name for the Calendar card; a meeting I said no to says so."""
    name = getattr(e, "name", None) or e.title
    if e.kind == "meeting" and (e.item or {}).get("my_rsvp") == "no":
        name = name[2:] if name.startswith("✕ ") else name
        return f"{name}{SEP}declined"
    return name


def _panel(title, action_text=None, action=None, spacing=2):
    """Rounded card with a heading row; returns (frame, body layout). spacing: between the body's rows."""
    frame = QFrame()
    frame.setObjectName("panel")
    frame.setStyleSheet(f"#panel {{ background: {T.PANEL}; border-radius: {T.RADIUS_XL}px; }}")
    lay = QVBoxLayout(frame)
    lay.setContentsMargins(18, 14, 18, 16)
    lay.setSpacing(8)
    head = QHBoxLayout()
    t = plain(QLabel(title.upper()))
    t.setStyleSheet(f"color: {T.MUTED}; font-size: {T.pt(T.FONT_XS)}; font-weight: 800; letter-spacing: 1px;")
    head.addWidget(t, 1)
    if action_text:
        b = QPushButton(action_text)
        T.polish(b, flat=True)
        b.setStyleSheet(f"color: {T.ACCENT}; font-size: {T.pt(T.FONT_S)}; padding: 2px 4px;")
        b.setCursor(Qt.PointingHandCursor)
        b.clicked.connect(action)
        head.addWidget(b)
    lay.addLayout(head)
    body = QVBoxLayout()
    body.setSpacing(spacing)
    lay.addLayout(body)
    lay.addStretch(1)
    return frame, body


def _caption(text):
    """A small heading inside a card ('Recent' under the unread chats)."""
    c = plain(QLabel(text))
    c.setStyleSheet(f"color: {T.META}; font-size: {T.pt(T.FONT_S)}; font-weight: 600; background: transparent;"
                    f" padding: 8px 0 2px {ROW_INSET}px;")
    return c


def _row_icon(name, color, tip=""):
    """A 16 px icon in a box as tall as the first line of the row's title, so it sits level with that line."""
    ic = QLabel()
    ic.setFixedSize(16, 19)
    ic.setAlignment(Qt.AlignHCenter | Qt.AlignVCenter)
    ic.setPixmap(pixmap(name, color, 15))
    ic.setStyleSheet("background: transparent;")
    if tip:
        ic.setToolTip(tip)
    return ic


class _Clickable(QFrame):
    """A row / tile that calls `fn` when clicked."""

    def __init__(self, fn, radius=12):
        super().__init__()
        self.fn = fn
        self.setObjectName("click")
        self.setCursor(Qt.PointingHandCursor)
        self.setStyleSheet(f"#click {{ background: transparent; border-radius: {radius}px; }}"
                           f"#click:hover {{ background: {T.SURFACE}; }}")

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.fn()


class HomePage(QWidget):
    """Dashboard: greeting, quick actions, unread chats, announcements, my team online, a tip."""

    def __init__(self, ctx):
        super().__init__()
        from PySide6.QtCore import QTimer
        self.ctx = ctx
        self.store = ctx.store
        T.bg_pane(self)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        area = self.area = QScrollArea()
        area.setWidgetResizable(True)
        area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        page = QWidget()
        T.bg_pane(page)
        row = QHBoxLayout(page)
        row.setContentsMargins(T.PAGE_GUTTER, T.PAGE_GUTTER, T.PAGE_GUTTER, T.PAGE_GUTTER)
        self.col = QWidget()
        self.col.setMaximumWidth(1080)
        row.addWidget(self.col, 1)
        area.setWidget(page)
        outer.addWidget(area)
        self.lay = QVBoxLayout(self.col)
        self.lay.setContentsMargins(0, 0, 0, 0)
        self.lay.setSpacing(18)
        self.tip_index = int(datetime.date.today().toordinal()) % len(TIPS)
        self._timer = QTimer(self, singleShot=True, interval=250,
                             timeout=lambda: self.rebuild() if self.isVisible() else None)
        s = self.store
        for sig in (s.unread_changed, s.announcements_changed, s.users_changed, s.me_changed, s.rooms_changed,
                    s.planner_changed, s.calendar_changed):
            sig.connect(self.schedule)
        s.conv_changed.connect(self.schedule)
        s.user_updated.connect(self.schedule)
        self._clock = QTimer(self, interval=60_000, timeout=self.schedule)
        self._clock.start()
        # the month shown in the Calendar card, and the months already fetched for it
        self._cal_month = datetime.date.today().replace(day=1)
        self._cal_cache = {}
        s.calendar_changed.connect(self._cal_cache.clear)

    def schedule(self, *_):
        if not self._timer.isActive():
            self._timer.start()

    def set_name(self, name, server):          # called after sign-in
        self.schedule()

    def showEvent(self, e):
        super().showEvent(e)
        self.rebuild()

    # ---- responsive layout: nothing may be wider than the window (there is no sideways scrolling)
    def _layout_key(self):
        """(two card columns?, action tiles per row, room for the profile box?) for the current width."""
        w = max(300, self.area.viewport().width() - 64)      # the page gutters, with some slack
        return w >= 760, max(2, min(6, w // 240)), w >= 640, w

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if getattr(self, "_built_key", None) and self._layout_key()[:3] != self._built_key[:3]:
            self.schedule()

    # ------------------------------------------------------------ build
    def rebuild(self):
        banner = getattr(self, "_banner", None)
        while self.lay.count():
            it = self.lay.takeAt(0)
            if banner is not None and it.widget() is banner:
                continue                      # keep the banner (and its animation) across rebuilds
            if it.widget():
                it.widget().deleteLater()
            elif it.layout():
                self._drop_layout(it.layout())
        if getattr(self, "_banner", None) is not None:
            self._banner.hide()
        if not self.store.me:
            return
        self._built_key = self._layout_key()
        wide = self._built_key[0]
        if T.FESTIVAL:
            if getattr(self, "_banner", None) is None:
                from client.ui.festive import FestiveBanner
                self._banner = FestiveBanner()
            self.lay.addWidget(self._banner)
            self._banner.show()
        self.lay.addWidget(self._hero())
        self.lay.addLayout(self._actions())
        grid = QGridLayout()
        grid.setHorizontalSpacing(18)
        grid.setVerticalSpacing(18)
        upcoming = self.store.reminders or [x for x in self.store.scheduled if x["state"] == "pending"]
        cards = [self._catch_up(), self._calendar(), self._announcements(), self._team(),
                 self._coming_up() if upcoming else self._tip()]
        cards = [c for c in cards if c is not None]
        if len(cards) % 2 and wide and len(cards) > 1 and upcoming:
            cards.append(self._tip())          # an even number of cards fills both columns
        for i, card in enumerate(cards):          # two columns when there is room, else one below the other
            grid.addWidget(card, i // 2 if wide else i, i % 2 if wide else 0)
        if wide:
            grid.setColumnStretch(0, 3)
            grid.setColumnStretch(1, 2)
        self.lay.addLayout(grid)
        self.lay.addStretch(1)
        # any text wraps rather than making the page wider than the window (no sideways scrolling here)
        for label in self.col.findChildren(QLabel):
            if label.text() and label.sizePolicy().horizontalPolicy() != QSizePolicy.Ignored \
                    and not isinstance(label, ElidedLabel):
                label.setWordWrap(True)

    def _drop_layout(self, lay):
        while lay.count():
            it = lay.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
            elif it.layout():
                self._drop_layout(it.layout())

    def _hero(self):
        from client.ui.widgets import Avatar
        s, me = self.store, self.store.me
        hero = QFrame()
        hero.setObjectName("hero")
        deep = T.mix(T.ACCENT, T.BG, 0.28)
        hero.setStyleSheet(f"#hero {{ border-radius: 24px;"
                           f" background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 {deep}, stop:1 {T.PANEL}); }}")
        h = QHBoxLayout(hero)
        h.setContentsMargins(28, 24, 24, 24)
        h.setSpacing(18)
        col = QVBoxLayout()
        col.setSpacing(4)
        hour = datetime.datetime.now().hour
        part = "Good morning" if hour < 12 else "Good afternoon" if hour < 17 else "Good evening"
        first = first_name(me.get("name"), "")
        hi = plain(QLabel(f"{part}, {first}"))
        hi.setWordWrap(True)
        hi.setStyleSheet("font-size: 21pt; font-weight: 800;")
        col.addWidget(hi)
        # "Tuesday 1 October · Studio": no zero-padded day
        on_hero = [deep, T.PANEL]                # the gradient's two ends: text must read on both
        today = plain(QLabel(fmt_date(datetime.date.today(), long=True, year=False) + SEP + s.server_name))
        today.setWordWrap(True)
        today.setStyleSheet(f"color: {T.readable_on(T.MUTED, on_hero)}; font-size: {T.pt(T.FONT_M)};")
        col.addWidget(today)
        col.addSpacing(8)
        chats = sum(1 for c in s.convs.values() if c.unread and s.conv_exists(c.conv) and not s.is_muted(c.conv))
        anns = s.unread_announcements()
        bits = []
        if chats:
            bits.append(f"<b>{chats}</b> unread chat{'s' if chats != 1 else ''}")
        if anns:
            bits.append(f"<b>{anns}</b> new announcement{'s' if anns != 1 else ''}")
        online = sum(1 for u in s.users.values() if u.get("status", "offline") != "offline")
        bits.append(f"<b>{online}</b> {'person' if online == 1 else 'people'} online")
        summary = QLabel(SEP.join(bits) if (chats or anns) else "You're all caught up" + SEP + bits[-1])
        summary.setWordWrap(True)
        summary.setStyleSheet(f"color: {T.TEXT}; font-size: {T.pt(T.FONT_BODY)};")
        col.addWidget(summary)
        h.addLayout(col, 1)
        if not self._built_key[2]:
            return hero

        me_box = _Clickable(self.ctx.edit_status_message, 16)
        mb = QHBoxLayout(me_box)
        mb.setContentsMargins(12, 10, 14, 10)
        mb.setSpacing(12)
        av = Avatar(56)
        status = me.get("status", "online")
        av.set(me.get("name", ""), me.get("name", ""), status=status, uid=s.my_id, ring=T.PANEL)
        mb.addWidget(av)
        mc = QVBoxLayout()
        mc.setSpacing(1)
        n = plain(QLabel(me.get("name", "")))
        n.setStyleSheet(f"font-weight: 700; font-size: {T.pt(T.FONT_BODY)};")
        mc.addWidget(n)
        st = plain(QLabel(s.status_text(me) or T.STATUS_LABELS.get(status, status)))
        # the status colour as text: readable on the gradient (the light theme's green 'Online' was ~2:1)
        colour = T.STATUS_COLORS.get(status, T.MUTED) if not s.status_text(me) else T.MUTED
        st.setStyleSheet(f"color: {T.readable_on(colour, on_hero)}; font-size: {T.pt(T.FONT_S)};")
        mc.addWidget(st)
        edit = plain(QLabel("Set status & photo"))
        edit.setStyleSheet(f"color: {T.readable_on(T.ACCENT, on_hero)}; font-size: 8.5pt; font-weight: 600;")
        mc.addWidget(edit)
        mb.addLayout(mc)
        h.addWidget(me_box, 0, Qt.AlignVCenter)
        return hero

    def _actions(self):
        from client.ui.dialogs import ComposeAnnouncementDialog, announce_targets
        ctx, s = self.ctx, self.store
        items = [("chat", "New chat", f"Message anyone{SEP}Ctrl+K", ctx.focus_search)]
        if s.perm("create_rooms"):
            items.append(("hash", "New room", "Group chat", lambda: ctx.new_room()))
        if announce_targets(s):
            items.append(("megaphone", "Announce", "To your team or studio", lambda: ComposeAnnouncementDialog(ctx).exec()))
        items += [("clock", "Reminder", "Remind me later", lambda: ctx.new_reminder()),
                  ("smile", "Set status", f"Lunch, meeting, rendering{ELLIPSIS}", ctx.edit_status_message),
                  ("org", "Org chart", "Who's who", lambda: ctx.rail_clicked("directory"))]
        fit = min(len(items), self._built_key[1])
        rows = -(-len(items) // fit)
        per_row = -(-len(items) // rows)
        # one row layout per line of tiles: a shorter last line shares the whole width instead of leaving a hole
        grid = QVBoxLayout()
        grid.setSpacing(12)
        for start in range(0, len(items), per_row):
            line = QHBoxLayout()
            line.setSpacing(12)
            for ic, title, sub, fn in items[start:start + per_row]:
                line.addWidget(self._tile(ic, title, sub, fn), 1)
            grid.addLayout(line)
        return grid

    def _tile(self, ic, title, sub, fn):
        tile = _Clickable(fn, 18)
        tile.setStyleSheet(f"#click {{ background: {T.PANEL}; border-radius: 18px; }}"
                           f"#click:hover {{ background: {T.SURFACE}; }}")
        tl = QHBoxLayout(tile)
        tl.setContentsMargins(14, 12, 14, 12)
        tl.setSpacing(12)
        box = QLabel()
        box.setFixedSize(38, 38)
        box.setAlignment(Qt.AlignCenter)
        box.setStyleSheet(f"background: {T.ACCENT_SOFT}; border-radius: 11px;")
        box.setPixmap(pixmap(ic, T.ACCENT, 19))
        tl.addWidget(box)
        c = QVBoxLayout()
        c.setSpacing(0)
        a = QLabel(title)
        a.setStyleSheet(f"font-weight: 700; font-size: {T.pt(T.FONT_M)}; background: transparent;")
        b = QLabel(sub)
        b.setStyleSheet(f"color: {T.MUTED}; font-size: 8.5pt; background: transparent;")
        for lbl in (a, b):                 # a narrow tile cuts the text instead of widening the page
            lbl.setMinimumWidth(1)
            lbl.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        tile.setToolTip(f"{title} — {sub}")
        c.addWidget(a)
        c.addWidget(b)
        tl.addLayout(c, 1)
        return tile

    def _catch_up(self):
        s = self.store
        frame, body = _panel("Catch up", "All chats", lambda: self.ctx.rail_clicked("chats"))
        unread = [c for c in s.convs.values() if c.unread and c.last and s.conv_exists(c.conv)]
        unread.sort(key=lambda c: (s.is_muted(c.conv), -c.last_ts))
        recent = []
        if len(unread) < 3:
            # one or two unread chats left the card mostly empty: fill it up with the latest other chats
            mine = P.direct_conv(s.my_id)
            recent = sorted((c for c in s.convs.values() if c.last and s.conv_exists(c.conv) and c.conv != mine
                             and not c.unread), key=lambda c: -c.last_ts)[:4 - len(unread)]
        if not unread and not recent:
            return None                         # nothing unread and no chats yet: the hero says "all caught up"
        if not unread:
            done = plain(QLabel("You're all caught up."))
            done.setStyleSheet(f"color: {T.MUTED}; background: transparent; padding: 4px 0 0 {ROW_INSET}px;")
            body.addWidget(done)
        for c in unread[:6]:
            body.addWidget(self._conv_item(c))
        if len(unread) > 6:
            more = plain(QLabel(f"+ {len(unread) - 6} more with unread messages"))
            more.setStyleSheet(f"color: {T.META}; font-size: {T.pt(T.FONT_S)}; padding: 4px {ROW_INSET}px;")
            body.addWidget(more)
        if recent:
            body.addWidget(_caption("Recent"))
            for c in recent:
                body.addWidget(self._conv_item(c))
        return frame

    def _conv_item(self, c):
        from client import stickers
        from client.ui.widgets import ConvItem, fmt_list_time
        s = self.store
        kind, target = P.parse_conv(c.conv)
        item = ConvItem(c.conv)
        item.INSET = ROW_INSET                  # the avatar lines up with the card's heading
        m = c.last
        text = clip(stickers.summary(m), 120)
        if m["sender_id"] == s.my_id:
            text = "You: " + text
        elif kind == "r":
            text = first_name(s.user_name(m["sender_id"]), "Someone") + ": " + text
        if kind == "u":
            u = s.users.get(target, {})
            item.set_data(u.get("name", s.title(c.conv)), text, fmt_list_time(c.last_ts), c.unread,
                          u.get("status", "offline"), muted=s.is_muted(c.conv))
        else:
            item.set_data(s.title(c.conv), text, fmt_list_time(c.last_ts), c.unread, room=True,
                          muted=s.is_muted(c.conv))
        item.clicked.connect(self.ctx.open_conv)
        return item

    def _announcements(self):
        s = self.store
        anns = s.announcements[:3]
        if not anns:
            return None
        frame, body = _panel("Announcements", "All news", lambda: self.ctx.rail_clicked("announcements"))
        for a in anns:
            unread = not a.get("read")
            row = _Clickable(lambda: self.ctx.rail_clicked("announcements"), T.RADIUS_M)
            rl = QHBoxLayout(row)
            rl.setContentsMargins(ROW_INSET, 8, 8, 8)
            rl.setSpacing(10)
            # the "new" dot: level with the title's first line; read ones keep the space (titles stay aligned)
            dot = QLabel()
            dot.setFixedSize(8, 8)
            dot.setStyleSheet(f"background: {T.ACCENT if unread else 'transparent'}; border-radius: 4px;")
            dot.setToolTip("New" if unread else "")
            dc = QVBoxLayout()
            dc.setContentsMargins(0, 6, 0, 0)
            dc.addWidget(dot)
            dc.addStretch(1)
            rl.addLayout(dc)
            c = QVBoxLayout()
            c.setSpacing(1)
            t = plain(QLabel(a["title"]))
            t.setWordWrap(True)
            t.setStyleSheet(f"font-weight: {'800' if unread else '600'}; background: transparent;")
            c.addWidget(t)
            who = plain(QLabel(f"{s.user_name(a['sender_id'])}{SEP}{fmt_when(a['ts'])}"))
            who.setStyleSheet(f"color: {T.META}; font-size: {T.pt(T.FONT_S)}; background: transparent;")
            c.addWidget(who)
            preview = plain(QLabel(clip(a["body"], 110)))
            preview.setWordWrap(True)
            preview.setStyleSheet(f"color: {T.MUTED}; font-size: {T.pt(T.FONT_S)}; background: transparent;")
            c.addWidget(preview)
            rl.addLayout(c, 1)
            body.addWidget(row)
        return frame

    def _team(self):
        from client.ui.widgets import Avatar
        s, me = self.store, self.store.me
        people = s.direct_reports()
        label = "My team"
        if not people and me.get("manager_id"):
            people = [u for u in s.users.values() if u.get("manager_id") == me["manager_id"]]
            boss = s.users.get(me["manager_id"])
            people = ([boss] if boss else []) + people
            label = "My team"
        if not people and me.get("section"):
            people = [u for u in s.users.values() if u.get("section") == me["section"]
                      and u.get("department") == me.get("department")]
            label = f"{me.get('department')}{SEP}{me['section']}"
        if not people and me.get("department"):
            people = [u for u in s.users.values() if u.get("department") == me["department"]]
            label = me["department"]
        if not people:
            return None                         # no team set up yet: nothing to show
        order = {"online": 0, "busy": 1, "away": 2, "invisible": 3, "offline": 4}
        people.sort(key=lambda u: (order.get(u.get("status", "offline"), 4), u["name"].lower()))
        online = sum(1 for u in people if u.get("status", "offline") != "offline")
        frame, body = _panel(f"{label}{SEP}{online}/{len(people)} online",
                             "Org chart", lambda: self.ctx.rail_clicked("directory"))
        grid = QGridLayout()
        grid.setSpacing(6)
        width = self._built_key[3] * (0.58 if self._built_key[0] else 1.0) - 40
        cols = max(3, min(8, int(width // 92)))
        for i, u in enumerate(people[:15]):
            cell = _Clickable(lambda uid=u["id"]: self.ctx.open_conv(P.direct_conv(uid)), 12)
            cell.setFixedWidth(86)
            cell.setToolTip(rich_safe(f"{u['name']}\n{s.status_text(u) or T.STATUS_LABELS.get(u.get('status', 'offline'))}"
                                      "\nClick to chat"))
            cl = QVBoxLayout(cell)
            cl.setContentsMargins(4, 8, 4, 6)
            cl.setSpacing(4)
            av = Avatar(44)
            av.set(u["name"], u["name"], status=u.get("status", "offline"), uid=u["id"], ring=T.PANEL)
            cl.addWidget(av, 0, Qt.AlignHCenter)
            nm = plain(QLabel(first_name(u["name"], "?")))
            nm.setAlignment(Qt.AlignCenter)
            nm.setMinimumWidth(1)
            nm.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
            nm.setStyleSheet(f"font-size: 8.5pt; background: transparent;"
                             f" color: {T.TEXT if u.get('status', 'offline') != 'offline' else T.META};")
            cl.addWidget(nm)
            grid.addWidget(cell, i // cols, i % cols)
        grid.setColumnStretch(cols, 1)
        body.addLayout(grid)
        return frame

    def _calendar(self):
        """A small month (dots on days with something) and what's on in the next days."""
        from client.ui.calendar_views import MiniMonth, entries_from, kind_color
        frame, body = _panel("Calendar", "Open calendar", lambda: self.ctx.open_calendar(), spacing=8)
        data = self.store.calendar
        layers = {"meeting", "event", "note", "deadline", "holiday", "leave", "birthday", "anniversary"}
        entries = entries_from(data, layers, self.store.my_id)
        today = datetime.date.today()

        # month bar: ‹ September 2026 ›  Today  (the mouse wheel over the month does the same)
        bar = QHBoxLayout()
        bar.setSpacing(4)
        prev = IconButton("back", "Previous month", 28, 14)
        nxt = IconButton("next", "Next month", 28, 14)
        label = QLabel()
        label.setStyleSheet("font-weight: 700; background: transparent;")
        back = QPushButton("Today")
        T.polish(back, flat=True)
        back.setStyleSheet(f"color: {T.ACCENT}; font-size: {T.pt(T.FONT_S)}; padding: 2px 6px;")
        back.setCursor(Qt.PointingHandCursor)
        bar.addWidget(prev)
        bar.addWidget(label)
        bar.addWidget(nxt)
        bar.addStretch(1)
        bar.addWidget(back)
        body.addLayout(bar)

        mini = MiniMonth()
        mini.day_clicked.connect(lambda d: self.ctx.open_calendar(d))
        body.addWidget(mini)
        self._cal_widgets = (mini, label, back)
        prev.clicked.connect(lambda: self._cal_step(-1))
        nxt.clicked.connect(lambda: self._cal_step(1))
        mini.month_step.connect(self._cal_step)
        back.clicked.connect(lambda: self._cal_step(0))
        self._cal_show()
        soon = [e for e in entries if today <= e.start.date() <= today + datetime.timedelta(days=6)
                or (e.all_day and e.start.date() <= today < e.end.date())][:4]
        if not soon:
            empty = QLabel("Nothing planned this week.")
            empty.setStyleSheet(f"color: {T.MUTED}; background: transparent; padding-left: {ROW_INSET}px;")
            body.addWidget(empty)
        whens = []
        for e in soon:
            # icon and day sit level with the title's first line, also when a long title wraps
            row = QHBoxLayout()
            row.setContentsMargins(ROW_INSET, 0, 0, 0)
            row.setSpacing(10)
            colour = T.readable_on(kind_color(e.kind), T.PANEL, 3.0)
            row.addWidget(_row_icon(_entry_icon(e), colour, KIND_NAMES.get(e.kind, "")), 0, Qt.AlignTop)
            d = e.start.date()
            day = "Today" if d <= today else (day_word(d) or fmt_date(d, year=False))     # 'Thu 1 Oct', not 'Thu 01'
            when = plain(QLabel(day + ("" if e.all_day else f" {fmt_time(e.start)}")))
            whens.append(when)
            when.setStyleSheet(f"color: {T.META}; font-size: {T.pt(T.FONT_S)}; background: transparent;"
                               " padding-top: 2px;")
            row.addWidget(when, 0, Qt.AlignTop)
            t = plain(QLabel(_entry_title(e)))
            t.setStyleSheet("font-weight: 600; background: transparent;")
            row.addWidget(t, 1, Qt.AlignTop)
            body.addLayout(row)
        # the day column is as wide as its longest day, no wider: long titles get the room and wrap less
        if whens:
            from PySide6.QtGui import QFontMetrics
            from client.ui.widgets import ui_font
            fm = QFontMetrics(ui_font(T.FONT_S))
            width = max(fm.horizontalAdvance(w.text()) for w in whens) + 8
            for w in whens:
                w.setFixedWidth(width)
        return frame

    # ---- the Calendar card's month: step through months without rebuilding the page
    _CAL_LAYERS = {"meeting", "event", "note", "deadline", "holiday", "leave", "birthday", "anniversary"}

    def _cal_step(self, step):
        """step: -1 / +1 month, or 0 for this month."""
        if step == 0:
            self._cal_month = datetime.date.today().replace(day=1)
        else:
            months = self._cal_month.year * 12 + self._cal_month.month - 1 + step
            self._cal_month = datetime.date(months // 12, months % 12 + 1, 1)
        self._cal_show()

    def _cal_show(self):
        from client.ui.calendar_views import entries_from
        widgets = getattr(self, "_cal_widgets", None)
        if not widgets:
            return
        mini, label, back = widgets
        try:
            mini.objectName()
        except RuntimeError:                   # the page was rebuilt meanwhile
            return
        month = self._cal_month
        label.setText(f"{month:%B %Y}")
        back.setVisible(month != datetime.date.today().replace(day=1))
        data = self._cal_cache.get(month)
        if data is None:
            data = self.store.calendar         # what Home already has, until the month arrives
            self._cal_fetch(month)
        entries = entries_from(data, self._CAL_LAYERS, self.store.my_id)
        holidays = {datetime.date.fromisoformat(h["day"]) for h in (data or {}).get("holidays", [])}
        mini.set_data(month, entries, holidays)

    def _cal_fetch(self, month):
        """Ask the server for everything in the six weeks the small month shows."""
        pending = self.__dict__.setdefault("_cal_pending", set())
        if month in pending or not self.ctx.conn.online:
            return
        first = month - datetime.timedelta(days=month.weekday())
        last = first + datetime.timedelta(days=41)
        pending.add(month)

        def done(reply):
            pending.discard(month)
            if reply.get("ok"):
                self._cal_cache[month] = reply
                if month == self._cal_month:
                    self._cal_show()
        self.ctx.conn.request("cal_range", done, start=first.isoformat(), end=last.isoformat())

    def _coming_up(self):
        """My next reminders and scheduled messages."""
        s = self.store
        frame, body = _panel("Coming up", "New reminder", lambda: self.ctx.new_reminder(), spacing=10)
        items = [("r", r["due_at"], r) for r in s.reminders]
        items += [("s", x["due_at"], x) for x in s.scheduled if x["state"] == "pending"]
        items.sort(key=lambda t: (t[2].get("state") != 1, t[1]))       # fired reminders first
        for kind, due, x in items[:5]:
            fired = x.get("state") == 1
            row = QHBoxLayout()
            row.setContentsMargins(ROW_INSET, 0, 0, 0)
            row.setSpacing(10)
            # outline icons of one size: an alarm clock for a reminder, the send arrow for a scheduled message
            row.addWidget(_row_icon("clock" if kind == "r" else "send", T.DANGER if fired else T.ACCENT,
                                    "Reminder" if kind == "r" else "Scheduled message"), 0, Qt.AlignTop)
            col = QVBoxLayout()
            col.setSpacing(1)
            if kind == "r":
                what = x.get("text") or (f"{x.get('sender_name', '')}: {x.get('snippet', '')}" if x.get("snippet")
                                         else f"Chat with {self.store.title(x['conv'])}" if x.get("conv") else "Reminder")
                when = "Due now" if fired else fmt_when(due)
            else:
                what = "Sticker" if x["sticker"] else x["text"]
                to = self.store.title(x["conv"]) if s.conv_exists(x["conv"]) else "?"
                when = f"{fmt_when(due)}{SEP}sends to {to}"
            t = plain(QLabel(clip(what, 80)))
            t.setStyleSheet("font-weight: 600; background: transparent;")
            w = plain(QLabel(when))
            w.setStyleSheet(f"color: {T.DANGER if fired else T.META}; font-size: {T.pt(T.FONT_S)};"
                            " background: transparent;")
            col.addWidget(t)
            col.addWidget(w)
            row.addLayout(col, 1)
            # a worded button says what it does (a bare tick or chat bubble meant something different per row)
            btn = None
            if kind == "r":
                btn = QPushButton("Done")
                btn.setToolTip("Mark this reminder as done")
                btn.clicked.connect(lambda _=False, rid=x["id"]: self.ctx.conn.request("reminder_done", None, id=rid))
            elif s.conv_exists(x["conv"]):
                btn = QPushButton("Open")
                btn.setToolTip("Open the chat")
                btn.clicked.connect(lambda _=False, c=x["conv"]: self.ctx.open_conv(c))
            if btn is not None:
                T.polish(btn, chip=True)
                btn.setCursor(Qt.PointingHandCursor)
                row.addWidget(btn, 0, Qt.AlignTop)
            body.addLayout(row)
        if len(items) > 5:
            more = plain(QLabel(f"+ {len(items) - 5} more"))
            more.setStyleSheet(f"color: {T.META}; font-size: {T.pt(T.FONT_S)}; padding-left: {ROW_INSET}px;")
            body.addWidget(more)
        return frame

    def _tip(self):
        ic, title, text = TIPS[self.tip_index]
        frame, body = _panel("Tip", "Next tip", self._next_tip)
        row = QHBoxLayout()
        row.setContentsMargins(ROW_INSET, 0, 0, 0)
        row.setSpacing(14)
        box = QLabel()
        box.setFixedSize(46, 46)
        box.setAlignment(Qt.AlignCenter)
        box.setStyleSheet(f"background: {T.ACCENT_SOFT}; border-radius: 13px;")
        box.setPixmap(pixmap(ic, T.ACCENT, 22))
        row.addWidget(box, 0, Qt.AlignTop)
        c = QVBoxLayout()
        c.setSpacing(4)
        t = QLabel(title)
        t.setStyleSheet(f"font-weight: 800; font-size: {T.pt(T.FONT_L)};")
        d = QLabel(text)
        d.setWordWrap(True)
        d.setStyleSheet(f"color: {T.MUTED};")
        c.addWidget(t)
        c.addWidget(d)
        row.addLayout(c, 1)
        body.addLayout(row)
        return frame

    def _next_tip(self):
        self.tip_index = (self.tip_index + 1) % len(TIPS)
        self.rebuild()


class AnnouncementCard(QFrame):
    show_reads = Signal(dict)

    def __init__(self, store, ann, unread=None):
        """unread: draw it as new (default: when it isn't read). The News page keeps what was new when it was
        opened drawn as new, although the read receipts go out straight away."""
        super().__init__()
        self.unread = unread = (not ann.get("read")) if unread is None else bool(unread)
        # a thin edge all round: a thick left border on a rounded card drew as a curved sliver
        edge = T.ACCENT_FOCUS if unread else "transparent"
        self.setStyleSheet(f"AnnouncementCard {{ background: {T.PANEL}; border-radius: {T.RADIUS_XL}px;"
                           f" border: 1px solid {edge}; }}")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 14, 18, 14)
        lay.setSpacing(2)
        head = QHBoxLayout()
        head.setSpacing(T.SPACE_S)
        title = plain(QLabel(ann["title"]))
        title.setStyleSheet(f"font-size: {T.pt(T.FONT_L)}; font-weight: 700;")
        title.setWordWrap(True)
        head.addWidget(title, 1)
        if unread:
            self.new_pill = plain(QLabel("New"))
            self.new_pill.setStyleSheet(f"background: {T.ACCENT_SOFT}; color: {T.ACCENT}; border-radius: 8px;"
                                        f" padding: 1px 8px; font-size: {T.pt(T.FONT_XS)}; font-weight: 700;"
                                        " margin-top: 2px;")
            head.addWidget(self.new_pill, 0, Qt.AlignTop)
        w = plain(QLabel(fmt_when(ann["ts"])))
        w.setStyleSheet(f"color: {T.META}; font-size: {T.pt(T.FONT_S)}; margin-top: 3px;")
        head.addWidget(w, 0, Qt.AlignTop)
        lay.addLayout(head)
        target = ann.get("target_label") or ann.get("department") or ""
        to = f"{SEP}to {target}" if target and target != "Everyone" else ""
        sender = plain(QLabel(f"{store.user_name(ann['sender_id'])}{to}"))
        sender.setStyleSheet(f"color: {T.MUTED}; font-size: {T.pt(T.FONT_S)};")
        lay.addWidget(sender)
        lay.addSpacing(6)
        body = QLabel(linkify(ann["body"]))
        body.setWordWrap(True)
        body.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.LinksAccessibleByMouse)
        body.linkActivated.connect(open_link)
        body.setIndent(0)                       # level with the title and the sender (no automatic indent)
        body.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(body)
        if ann["sender_id"] == store.my_id or store.me.get("is_admin"):
            read, total = ann.get("read_count"), ann.get("total")
            text = f" Read by {read} of {total}" if read is not None and total else " Who has read this?"
            lay.addSpacing(8)
            reads = QPushButton(text)
            reads.setIcon(icon("users", T.TEXT, 14))
            reads.setToolTip("See who has read this announcement")
            reads.setCursor(Qt.PointingHandCursor)
            reads.clicked.connect(lambda: self.show_reads.emit(ann))
            lay.addWidget(reads, 0, Qt.AlignLeft)


class AnnouncementsPage(QWidget):
    compose = Signal()
    show_reads = Signal(dict)

    def __init__(self, store):
        super().__init__()
        self.store = store
        self._fresh = set()                     # ids that were new while the page is open: they stay marked
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.header = PageHeader("News", "Announcements for the studio, your department or team")
        self.b_new = QPushButton(" New announcement")
        self.b_new.setIcon(icon("megaphone", T.ACCENT_TEXT, 16))
        T.polish(self.b_new, primary=True)
        self.b_new.clicked.connect(self.compose.emit)
        self.header.lay.addWidget(self.b_new)
        lay.addWidget(self.header)
        self.area, self.col = scroll_column()
        lay.addWidget(self.area, 1)
        store.announcements_changed.connect(self.rebuild)
        store.me_changed.connect(self.rebuild)

    def _note_fresh(self):
        self._fresh |= {a["id"] for a in self.store.announcements if not a.get("read")}

    def showEvent(self, e):
        self._note_fresh()
        super().showEvent(e)

    def hideEvent(self, e):
        super().hideEvent(e)
        self._fresh.clear()                     # next time only what is new by then is marked

    def rebuild(self):
        from client.ui.dialogs import announce_targets
        if self.isVisible():
            self._note_fresh()
        self.b_new.setVisible(bool(announce_targets(self.store)))
        while self.col.count() > 1:
            w = self.col.takeAt(0).widget()
            if w:
                w.deleteLater()
        if not self.store.announcements:
            can = bool(announce_targets(self.store))
            self.col.insertWidget(0, EmptyState(
                "megaphone", "No announcements yet",
                "Studio news, holidays and dailies times show up here." if not can else
                "Tell your team or the whole studio something important: everyone gets a pop-up.",
                "New announcement" if can else None, self.compose.emit if can else None))
        for i, a in enumerate(self.store.announcements):
            card = AnnouncementCard(self.store, a, unread=not a.get("read") or a["id"] in self._fresh)
            card.show_reads.connect(self.show_reads.emit)
            self.col.insertWidget(i, card)

    def mark_all_read(self):
        for a in list(self.store.announcements):
            if not a.get("read"):
                self.store.mark_announcement_read(a["id"])


class DirectoryPage(QWidget):
    """Studio directory: people as cards, an org chart of the reporting lines, or a list."""

    def __init__(self, ctx):
        super().__init__()
        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import QMenu
        from common import orgviews
        from client.ui.widgets import paint_avatar
        orgviews.avatar_painter = paint_avatar          # profile photos in the cards and the chart
        self.ctx = ctx
        self.store = ctx.store
        self._QMenu = QMenu
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(PageHeader("Organisation", "Everyone you can reach — by department, or who reports to whom"))
        body = QWidget()
        T.bg_pane(body)
        bl = QVBoxLayout(body)
        bl.setContentsMargins(T.PAGE_GUTTER, 14, T.PAGE_GUTTER, 16)
        # the chart's "not in a reporting line" note is only for people who can set 'Reports to' (see rebuild)
        self.browser = orgviews.OrgBrowser(view=ctx.config["directory_view"], loner_hint="",
                                           me_action_text="Set status")
        self.browser.open_person.connect(self._open)
        self.browser.person_menu.connect(self._menu)
        self.browser.view_changed.connect(self._remember_view)
        self.browser.edit_me.connect(ctx.edit_status_message)
        bl.addWidget(self.browser, 1)
        lay.addWidget(body, 1)
        self._timer = QTimer(self, singleShot=True, interval=300,
                             timeout=lambda: self.rebuild() if self.isVisible() else None)
        for sig in (self.store.users_changed, self.store.user_updated, self.store.me_changed):
            sig.connect(lambda *_: self._timer.start() if not self._timer.isActive() else None)
        ctx.avatars.changed.connect(lambda _uid: self.browser.update_views())

    def rebuild(self):
        me = self.store.me or {}
        can_edit = me.get("is_admin") or self.store.perm("manage_users")
        self.browser.set_loner_hint("set 'Reports to' in the server console" if can_edit else "")
        self.browser.set_people(self.store.all_people(), me_id=self.store.my_id)

    def showEvent(self, e):
        super().showEvent(e)
        self.rebuild()

    def _remember_view(self, view):
        self.ctx.config["directory_view"] = view
        self.ctx.config.save()

    def _open(self, uid):
        if uid and uid != self.store.my_id:
            self.ctx.open_conv(P.direct_conv(uid))

    def _menu(self, uid, pos):
        if not uid or uid == self.store.my_id:
            return
        m = self._QMenu(self)
        conv = P.direct_conv(uid)
        m.addAction(icon("chat", T.TEXT, 16), "Send message", lambda: self.ctx.open_conv(conv))
        m.addAction(icon("attachment", T.TEXT, 16), f"Send files{ELLIPSIS}", lambda: self.ctx._send_files_to(conv))
        m.addAction(icon("screen", T.TEXT, 16), self.store.screen_view_label(uid),
                    lambda: self.ctx.screens.invite(uid, "request"))
        if getattr(self.store, "buzz_enabled", False):
            m.addAction(icon("zap", T.TEXT, 16), "Buzz", lambda: self.ctx.buzz_conv(conv))
        self.ctx.add_manage_actions(m, uid)
        m.exec(pos)


# ------------------------------------------------------------------ file transfers
def transfer_status(t):
    """How a running transfer is doing - the chat's upload strip's wording, so both say the same thing:
    '211 MB of 3.0 GB · 6% · 221 MB/s · about 13 s left'."""
    from client.ui.chat_view import transfer_status as status
    return status(t)


class TransferRow(QFrame):
    removed = Signal(object)            # this row: "Remove from list", or a retry took its place

    def __init__(self, manager, t, store):
        super().__init__()
        self.manager = manager
        self.t = t
        self._failed = None
        self.setStyleSheet(f"TransferRow {{ background: {T.PANEL}; border-radius: 16px; }}")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 10, 10, 10)
        lay.setSpacing(12)
        ic = QLabel()
        ic.setFixedSize(38, 38)
        ic.setAlignment(Qt.AlignCenter)
        ic.setStyleSheet(f"background: {T.SURFACE}; border-radius: 10px;")
        ic.setPixmap(pixmap("upload" if t.kind == "upload" else "download", T.ACCENT, 20))
        lay.addWidget(ic)
        col = QVBoxLayout()
        col.setSpacing(3)
        # long names are cut in the middle (the version and extension stay visible), never wider than the row
        self.name = ElidedLabel(t.name, mode=Qt.ElideMiddle)
        self.name.setStyleSheet("font-weight: 600;")
        where = ""
        if t.conv and store.conv_exists(t.conv):
            where = ("to " if t.kind == "upload" else "from ") + store.title(t.conv)
        self.where = where
        self.meta = ElidedLabel()
        self.bar = QProgressBar()
        self.bar.setFixedHeight(6)
        self.bar.setRange(0, 1000)
        keep = self.bar.sizePolicy()
        keep.setRetainSizeWhenHidden(True)      # the row keeps its height when the bar goes
        self.bar.setSizePolicy(keep)
        col.addWidget(self.name)
        col.addWidget(self.bar)
        col.addWidget(self.meta)
        lay.addLayout(col, 1)
        self.b_cancel = IconButton("close", "Cancel", 32, 16)
        self.b_cancel.clicked.connect(t.cancel)
        self.b_retry = QPushButton("Send again" if t.kind == "upload" else "Download again")
        T.polish(self.b_retry, chip=True)
        self.b_retry.setCursor(Qt.PointingHandCursor)

        def retry():
            if manager.retry(t) is None:
                from PySide6.QtWidgets import QMessageBox
                QMessageBox.information(self, "Send again", rich_safe(
                    f"{t.name} is no longer where it was, so it cannot be sent again.\n"
                    "Drop the file into the chat once more."))
                return
            self.removed.emit(self)             # the new try has its own row at the top
        self.b_retry.clicked.connect(retry)
        self.b_open = IconButton("open", "Open", 32, 18)
        self.b_open.clicked.connect(lambda: open_file(t.dest_path if t.kind == "download" else t.path))
        self.b_folder = IconButton("folder", "Show in folder", 32, 18)
        self.b_folder.clicked.connect(lambda: show_in_folder(t.dest_path if t.kind == "download" else t.path))
        self.b_remove = IconButton("close", "Remove from list", 32, 16)
        self.b_remove.clicked.connect(lambda: self.removed.emit(self))
        for b in (self.b_cancel, self.b_retry, self.b_open, self.b_folder, self.b_remove):
            lay.addWidget(b)
        self.refresh()

    def refresh(self):
        t = self.t
        pct = t.done / t.size if t.size else (1 if t.state == "done" else 0)
        self.bar.setValue(int(pct * 1000))
        failed = t.state == "failed"
        if t.active:
            state = transfer_status(t)
        elif t.state == "done":
            state = f"{P.human_size(t.size)}{SEP}{'Sent' if t.kind == 'upload' else 'Downloaded'}"
        elif t.state == "cancelled":
            state = "Cancelled"
        else:
            state = f"Failed: {t.error}" if t.error else "Failed"
        self.meta.setText(f"{state}{SEP}{self.where}" if self.where else state)
        if failed != self._failed:
            self._failed = failed
            self.meta.setStyleSheet(f"color: {T.DANGER if failed else T.META}; font-size: {T.pt(T.FONT_S)};")
        self.bar.setVisible(t.active)
        self.b_cancel.setVisible(t.active)
        self.b_retry.setVisible(t.state in ("failed", "cancelled"))
        self.b_open.setVisible(t.state == "done")
        self.b_folder.setVisible(t.state == "done")
        self.b_remove.setVisible(not t.active)


class TransfersPage(QWidget):
    def __init__(self, manager, store):
        super().__init__()
        self.manager = manager
        self.store = store
        self.rows = {}
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        header = PageHeader("File transfers", "Uploads and downloads of this session")
        self.clear = QPushButton("Clear finished")
        self.clear.setToolTip("Remove finished, cancelled and failed transfers from the list")
        self.clear.clicked.connect(self.clear_finished)
        folder = QPushButton(" Downloads folder")
        folder.setIcon(icon("folder", T.TEXT, 16))
        folder.clicked.connect(lambda: open_path(manager.config["download_dir"]))
        header.lay.addWidget(folder)
        header.lay.addWidget(self.clear)
        lay.addWidget(header)
        self.area, self.col = scroll_column()
        lay.addWidget(self.area, 1)
        self.empty = EmptyState("download", "No file transfers yet",
                                "Drag files or whole folders into a chat to send them. What you send and "
                                "download shows up here, with its progress.",
                                "Open the downloads folder", lambda: open_path(manager.config["download_dir"]))
        self.col.insertWidget(0, self.empty)
        manager.added.connect(self.add)
        manager.changed.connect(self.changed)
        self._update_clear()

    def add(self, t):
        if getattr(t, "hidden", False):
            return
        self.empty.hide()
        row = TransferRow(self.manager, t, self.store)
        row.removed.connect(self.remove_row)
        self.rows[id(t)] = row
        self.col.insertWidget(0, row)
        self._update_clear()

    def changed(self, t):
        row = self.rows.get(id(t))
        if row:
            row.refresh()
            self._update_clear()

    def remove_row(self, row):
        """One finished / cancelled / failed row off the list (and out of the manager's list)."""
        for key, r in list(self.rows.items()):
            if r is row:
                del self.rows[key]
        t = row.t
        if not t.active:
            temp = getattr(t, "temp_file", None)       # a packed folder's zip, as clear_finished does
            if temp:
                try:
                    os.remove(temp)
                except OSError:
                    pass
            if t in getattr(self.manager, "transfers", []):
                self.manager.transfers.remove(t)
        row.hide()
        row.deleteLater()
        self.empty.setVisible(not self.rows)
        self._update_clear()

    def clear_finished(self):
        self.manager.clear_finished()
        for key, row in list(self.rows.items()):
            if not row.t.active:
                row.deleteLater()
                del self.rows[key]
        self.empty.setVisible(not self.rows)
        self._update_clear()

    def _update_clear(self):
        self.clear.setEnabled(any(not r.t.active for r in self.rows.values()))

    def active_count(self):
        return sum(1 for r in self.rows.values() if r.t.active)
