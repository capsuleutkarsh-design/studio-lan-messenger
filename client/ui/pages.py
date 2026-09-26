"""Full-page views: home, announcements and file transfers."""

import datetime

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame, QGridLayout, QHBoxLayout, QLabel, QProgressBar, QPushButton, QScrollArea, QSizePolicy, QVBoxLayout,
    QWidget,
)

from common import protocol as P
from common import theme as T
from common.icons import icon, pixmap
from client.ui.widgets import IconButton, esc, linkify, open_link, open_path, plain, show_in_folder


class PageHeader(QFrame):
    def __init__(self, title, subtitle=""):
        super().__init__()
        self.setFixedHeight(68)
        self.setStyleSheet(f"PageHeader {{ background: {T.BG}; }}")
        self.lay = QHBoxLayout(self)
        self.lay.setContentsMargins(24, 10, 18, 10)
        col = QVBoxLayout()
        col.setSpacing(0)
        t = QLabel(title)
        t.setStyleSheet("font-size: 13pt; font-weight: 700;")
        col.addStretch(1)
        col.addWidget(t)
        if subtitle:
            s = QLabel(subtitle)
            s.setStyleSheet(f"color: {T.MUTED}; font-size: 9pt;")
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
    lay.setContentsMargins(24, 16, 24, 16)
    lay.setSpacing(10)
    lay.addStretch(1)
    area.setWidget(w)
    return area, lay


TIPS = [
    ("sticker", "Stickers", "Click the sticker button next to the emoji button — 185 desi, festival and mood stickers."),
    ("chart", "Quick polls", "Where for lunch? Which dailies slot? Attach button → Create a poll."),
    ("file", "Nuke scripts", "Paste a Nuke script straight into a chat. Others click Copy and paste it into Nuke."),
    ("pin", "Pin what matters", "Right-click a message → Pin to the top, so nobody misses the dailies time."),
    ("smile", "React instead of replying", "Hover a message and click 🙂 — a 👍 is quicker than 'ok noted'."),
    ("folder", "Send whole folders", "Attach → Send a folder: image sequences are zipped and extracted with one click."),
    ("search", "Find anything", "Ctrl+K jumps to a person or room, Ctrl+F searches every message you have."),
    ("screen", "Show your screen", "In a direct chat, click the screen button to share your screen (view only)."),
    ("bell", "Mute noisy rooms", "Mute a busy room from its ••• menu — you'll still hear when someone @mentions you."),
]


def _panel(title, action_text=None, action=None):
    """Rounded card with a heading row; returns (frame, body layout)."""
    frame = QFrame()
    frame.setObjectName("panel")
    frame.setStyleSheet(f"#panel {{ background: {T.PANEL}; border-radius: 20px; }}")
    lay = QVBoxLayout(frame)
    lay.setContentsMargins(18, 14, 18, 16)
    lay.setSpacing(8)
    head = QHBoxLayout()
    t = QLabel(title.upper())
    t.setStyleSheet(f"color: {T.MUTED}; font-size: 8pt; font-weight: 800; letter-spacing: 1px;")
    head.addWidget(t, 1)
    if action_text:
        b = QPushButton(action_text)
        T.polish(b, flat=True)
        b.setStyleSheet(f"color: {T.ACCENT}; font-size: 9pt; padding: 2px 4px;")
        b.setCursor(Qt.PointingHandCursor)
        b.clicked.connect(action)
        head.addWidget(b)
    lay.addLayout(head)
    body = QVBoxLayout()
    body.setSpacing(2)
    lay.addLayout(body)
    lay.addStretch(1)
    return frame, body


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
        row.setContentsMargins(32, 28, 32, 28)
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
                    s.planner_changed):
            sig.connect(self.schedule)
        s.conv_changed.connect(self.schedule)
        s.user_updated.connect(self.schedule)
        self._clock = QTimer(self, interval=60_000, timeout=self.schedule)
        self._clock.start()

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
        w = max(300, self.area.viewport().width() - 64)
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
        cards = [self._catch_up(), self._announcements(), self._team(),
                 self._coming_up() if upcoming else self._tip()]
        for i, card in enumerate(cards):          # two columns when there is room, else one below the other
            grid.addWidget(card, i // 2 if wide else i, i % 2 if wide else 0)
        if wide:
            grid.setColumnStretch(0, 3)
            grid.setColumnStretch(1, 2)
        self.lay.addLayout(grid)
        self.lay.addStretch(1)
        # any text wraps rather than making the page wider than the window (no sideways scrolling here)
        for label in self.col.findChildren(QLabel):
            if label.text() and label.sizePolicy().horizontalPolicy() != QSizePolicy.Ignored:
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
        first = (me.get("name") or "").split()[0] if me.get("name") else ""
        hi = plain(QLabel(f"{part}, {first}"))
        hi.setWordWrap(True)
        hi.setStyleSheet("font-size: 21pt; font-weight: 800;")
        col.addWidget(hi)
        today = QLabel(datetime.date.today().strftime("%A, %d %B")
                       + f"  ·  {s.server_name}")
        today.setWordWrap(True)
        today.setStyleSheet(f"color: {T.MUTED}; font-size: 10pt;")
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
        summary = QLabel("  ·  ".join(bits) if (chats or anns) else "You're all caught up  ·  " + bits[-1])
        summary.setWordWrap(True)
        summary.setStyleSheet(f"color: {T.TEXT}; font-size: 10.5pt;")
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
        n.setStyleSheet("font-weight: 700; font-size: 10.5pt;")
        mc.addWidget(n)
        st = plain(QLabel(s.status_text(me) or T.STATUS_LABELS.get(status, status)))
        st.setStyleSheet(f"color: {T.STATUS_COLORS.get(status, T.MUTED) if not s.status_text(me) else T.MUTED};"
                         " font-size: 9pt;")
        mc.addWidget(st)
        edit = QLabel("Set status & photo")
        edit.setStyleSheet(f"color: {T.ACCENT}; font-size: 8.5pt; font-weight: 600;")
        mc.addWidget(edit)
        mb.addLayout(mc)
        h.addWidget(me_box, 0, Qt.AlignVCenter)
        return hero

    def _actions(self):
        from client.ui.dialogs import ComposeAnnouncementDialog, announce_targets
        ctx, s = self.ctx, self.store
        items = [("chat", "New chat", "Ctrl+K", ctx.focus_search)]
        if s.perm("create_rooms"):
            items.append(("hash", "New room", "Group chat", lambda: ctx.new_room()))
        if announce_targets(s):
            items.append(("megaphone", "Announce", "To your team or studio", lambda: ComposeAnnouncementDialog(ctx).exec()))
        items += [("clock", "Reminder", "Remind me later", lambda: ctx.new_reminder()),
                  ("smile", "Set status", "Lunch, meeting, rendering…", ctx.edit_status_message),
                  ("org", "Org chart", "Who's who", lambda: ctx.rail_clicked("directory"))]
        fit = min(len(items), self._built_key[1])
        rows = -(-len(items) // fit)
        per_row = -(-len(items) // rows)
        row = QGridLayout()
        row.setSpacing(12)
        for i, (ic, title, sub, fn) in enumerate(items):
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
            a.setStyleSheet("font-weight: 700; font-size: 10pt; background: transparent;")
            b = QLabel(sub)
            b.setStyleSheet(f"color: {T.MUTED}; font-size: 8.5pt; background: transparent;")
            for lbl in (a, b):                 # a narrow tile cuts the text instead of widening the page
                lbl.setMinimumWidth(1)
                lbl.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
            tile.setToolTip(f"{title} — {sub}")
            c.addWidget(a)
            c.addWidget(b)
            tl.addLayout(c, 1)
            row.addWidget(tile, i // per_row, i % per_row)
        for col in range(per_row):
            row.setColumnStretch(col, 1)
        return row

    def _catch_up(self):
        from client import stickers
        from client.ui.widgets import ConvItem, fmt_list_time
        s = self.store
        frame, body = _panel("Catch up", "All chats", lambda: self.ctx.rail_clicked("chats"))
        convs = [c for c in s.convs.values() if c.unread and c.last and s.conv_exists(c.conv)]
        convs.sort(key=lambda c: (s.is_muted(c.conv), -c.last_ts))
        if not convs:
            recent = sorted((c for c in s.convs.values() if c.last and s.conv_exists(c.conv)),
                            key=lambda c: -c.last_ts)[:4]
            done = QLabel("🎉  You're all caught up." + ("  Recent chats:" if recent else ""))
            done.setStyleSheet(f"color: {T.MUTED}; padding: 4px 2px 6px 2px;")
            body.addWidget(done)
            convs = recent
        for c in convs[:6]:
            kind, target = P.parse_conv(c.conv)
            item = ConvItem(c.conv)
            m = c.last
            text = stickers.summary(m)[:120].replace("\n", " ")
            if m["sender_id"] == s.my_id:
                text = "You: " + text
            elif kind == "r":
                text = s.user_name(m["sender_id"]).split()[0] + ": " + text
            if kind == "u":
                u = s.users.get(target, {})
                item.set_data(u.get("name", s.title(c.conv)), text, fmt_list_time(c.last_ts), c.unread,
                              u.get("status", "offline"), muted=s.is_muted(c.conv))
            else:
                item.set_data(s.title(c.conv), text, fmt_list_time(c.last_ts), c.unread, room=True,
                              muted=s.is_muted(c.conv))
            item.clicked.connect(self.ctx.open_conv)
            body.addWidget(item)
        if len(convs) > 6:
            more = QLabel(f"+ {len(convs) - 6} more with unread messages")
            more.setStyleSheet(f"color: {T.MUTED}; font-size: 9pt; padding: 4px 8px;")
            body.addWidget(more)
        return frame

    def _announcements(self):
        s = self.store
        frame, body = _panel("Announcements", "See all", lambda: self.ctx.rail_clicked("announcements"))
        anns = s.announcements[:3]
        if not anns:
            empty = QLabel("No announcements yet.")
            empty.setStyleSheet(f"color: {T.MUTED}; padding: 4px 2px;")
            body.addWidget(empty)
        for a in anns:
            row = _Clickable(lambda: self.ctx.rail_clicked("announcements"), 10)
            rl = QHBoxLayout(row)
            rl.setContentsMargins(8, 8, 8, 8)
            rl.setSpacing(10)
            dot = QLabel()
            dot.setFixedSize(8, 8)
            dot.setStyleSheet(f"background: {T.ACCENT if not a.get('read') else T.BORDER}; border-radius: 4px;")
            rl.addWidget(dot, 0, Qt.AlignTop | Qt.AlignLeft)
            c = QVBoxLayout()
            c.setSpacing(1)
            t = plain(QLabel(a["title"]))
            t.setWordWrap(True)
            t.setStyleSheet(f"font-weight: {'800' if not a.get('read') else '600'}; background: transparent;")
            c.addWidget(t)
            when = datetime.datetime.fromtimestamp(a["ts"]).strftime("%d %b, %H:%M")
            who = plain(QLabel(f"{s.user_name(a['sender_id'])}  ·  {when}"))
            who.setStyleSheet(f"color: {T.MUTED}; font-size: 8.5pt; background: transparent;")
            c.addWidget(who)
            preview = plain(QLabel(a["body"].replace("\n", " ")[:110]))
            preview.setWordWrap(True)
            preview.setStyleSheet(f"color: {T.MUTED}; font-size: 9pt; background: transparent;")
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
            label = f"{me.get('department')} · {me['section']}"
        if not people and me.get("department"):
            people = [u for u in s.users.values() if u.get("department") == me["department"]]
            label = me["department"]
        order = {"online": 0, "busy": 1, "away": 2, "invisible": 3, "offline": 4}
        people.sort(key=lambda u: (order.get(u.get("status", "offline"), 4), u["name"].lower()))
        online = sum(1 for u in people if u.get("status", "offline") != "offline")
        frame, body = _panel(f"{label}  ·  {online}/{len(people)} online" if people else "My team",
                             "Organisation", lambda: self.ctx.rail_clicked("directory"))
        if not people:
            empty = QLabel("Your team shows up here once the admin sets your department and 'Reports to'.")
            empty.setWordWrap(True)
            empty.setStyleSheet(f"color: {T.MUTED}; padding: 4px 2px;")
            body.addWidget(empty)
            return frame
        grid = QGridLayout()
        grid.setSpacing(6)
        width = self._built_key[3] * (0.58 if self._built_key[0] else 1.0) - 40
        cols = max(3, min(8, int(width // 92)))
        for i, u in enumerate(people[:15]):
            cell = _Clickable(lambda uid=u["id"]: self.ctx.open_conv(P.direct_conv(uid)), 12)
            cell.setFixedWidth(86)
            cell.setToolTip(f"{u['name']}\n{s.status_text(u) or T.STATUS_LABELS.get(u.get('status', 'offline'))}"
                            "\nClick to chat")
            cl = QVBoxLayout(cell)
            cl.setContentsMargins(4, 8, 4, 6)
            cl.setSpacing(4)
            av = Avatar(44)
            av.set(u["name"], u["name"], status=u.get("status", "offline"), uid=u["id"], ring=T.PANEL)
            cl.addWidget(av, 0, Qt.AlignHCenter)
            nm = QLabel(u["name"].split()[0])
            nm.setAlignment(Qt.AlignCenter)
            nm.setMinimumWidth(1)
            nm.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
            nm.setStyleSheet(f"font-size: 8.5pt; background: transparent;"
                             f" color: {T.TEXT if u.get('status', 'offline') != 'offline' else T.FAINT};")
            cl.addWidget(nm)
            grid.addWidget(cell, i // cols, i % cols)
        grid.setColumnStretch(cols, 1)
        body.addLayout(grid)
        return frame

    def _coming_up(self):
        """My next reminders and scheduled messages."""
        from client.ui.planner_ui import fmt_due
        s = self.store
        frame, body = _panel("Coming up", "New reminder", lambda: self.ctx.new_reminder())
        items = [("r", r["due_at"], r) for r in s.reminders]
        items += [("s", x["due_at"], x) for x in s.scheduled if x["state"] == "pending"]
        items.sort(key=lambda t: (t[2].get("state") != 1, t[1]))       # fired reminders first
        for kind, due, x in items[:5]:
            row = QHBoxLayout()
            row.setSpacing(10)
            ic = QLabel()
            ic.setPixmap(pixmap("clock" if kind == "r" else "send", T.DANGER if x.get("state") == 1 else T.ACCENT, 16))
            row.addWidget(ic, 0, Qt.AlignTop)
            col = QVBoxLayout()
            col.setSpacing(0)
            if kind == "r":
                what = x.get("text") or (f"{x.get('sender_name', '')}: {x.get('snippet', '')}" if x.get("snippet")
                                         else f"Chat with {self.store.title(x['conv'])}" if x.get("conv") else "Reminder")
                when = "Due now" if x.get("state") == 1 else fmt_due(due)
            else:
                what = "Sticker" if x["sticker"] else x["text"]
                when = f"Sends {fmt_due(due)} to {self.store.title(x['conv']) if s.conv_exists(x['conv']) else '?'}"
            t = plain(QLabel(what.replace("\n", " ")[:80]))
            t.setStyleSheet("font-weight: 600; background: transparent;")
            w = QLabel(when)
            w.setStyleSheet(f"color: {T.DANGER if x.get('state') == 1 else T.MUTED}; font-size: 8.5pt;"
                            " background: transparent;")
            col.addWidget(t)
            col.addWidget(w)
            row.addLayout(col, 1)
            if kind == "r":
                done = IconButton("check", "Done", 28, 15, round_=False)
                done.clicked.connect(lambda _=False, rid=x["id"]: self.ctx.conn.request("reminder_done", None, id=rid))
                row.addWidget(done)
            elif s.conv_exists(x["conv"]):
                go = IconButton("chat", "Open the chat", 28, 15, round_=False)
                go.clicked.connect(lambda _=False, c=x["conv"]: self.ctx.open_conv(c))
                row.addWidget(go)
            body.addLayout(row)
        if len(items) > 5:
            more = QLabel(f"+ {len(items) - 5} more")
            more.setStyleSheet(f"color: {T.MUTED}; font-size: 9pt;")
            body.addWidget(more)
        return frame

    def _tip(self):
        ic, title, text = TIPS[self.tip_index]
        frame, body = _panel("Tip", "Next tip", self._next_tip)
        row = QHBoxLayout()
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
        t.setStyleSheet("font-weight: 800; font-size: 11pt;")
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

    def __init__(self, store, ann):
        super().__init__()
        unread = not ann.get("read")
        border = T.ACCENT if unread else T.BORDER
        self.setStyleSheet(f"AnnouncementCard {{ background: {T.PANEL}; border-radius: 18px;"
                           f" border-left: 4px solid {border}; }}")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 14, 18, 14)
        head = QHBoxLayout()
        title = plain(QLabel(ann["title"]))
        title.setStyleSheet("font-size: 11.5pt; font-weight: 700;")
        title.setWordWrap(True)
        head.addWidget(title, 1)
        when = datetime.datetime.fromtimestamp(ann["ts"]).strftime("%d %b %Y, %H:%M")
        w = QLabel(when)
        w.setStyleSheet(f"color: {T.FAINT}; font-size: 8.5pt;")
        head.addWidget(w, 0, Qt.AlignTop)
        lay.addLayout(head)
        target = ann.get("target_label") or ann.get("department") or ""
        to = f" → {target}" if target and target != "Everyone" else ""
        sender = plain(QLabel(f"{store.user_name(ann['sender_id'])}{to}"))
        sender.setStyleSheet(f"color: {T.ACCENT if unread else T.MUTED}; font-size: 9pt;")
        lay.addWidget(sender)
        body = QLabel(linkify(ann["body"]))
        body.setWordWrap(True)
        body.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.LinksAccessibleByMouse)
        body.linkActivated.connect(open_link)
        body.setStyleSheet("padding-top: 6px;")
        lay.addWidget(body)
        if ann["sender_id"] == store.my_id or store.me.get("is_admin"):
            reads = QPushButton(" Who has read this?")
            reads.setIcon(icon("users", T.TEXT, 14))
            reads.setCursor(Qt.PointingHandCursor)
            reads.clicked.connect(lambda: self.show_reads.emit(ann))
            lay.addWidget(reads, 0, Qt.AlignLeft)


class AnnouncementsPage(QWidget):
    compose = Signal()
    show_reads = Signal(dict)

    def __init__(self, store):
        super().__init__()
        self.store = store
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.header = PageHeader("Announcements", "Studio-wide messages")
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

    def rebuild(self):
        me = self.store.me
        from client.ui.dialogs import announce_targets
        self.b_new.setVisible(bool(announce_targets(self.store)))
        while self.col.count() > 1:
            w = self.col.takeAt(0).widget()
            if w:
                w.deleteLater()
        if not self.store.announcements:
            empty = QLabel("No announcements yet.")
            empty.setAlignment(Qt.AlignCenter)
            empty.setStyleSheet(f"color: {T.FAINT}; padding: 40px;")
            self.col.insertWidget(0, empty)
        for i, a in enumerate(self.store.announcements):
            card = AnnouncementCard(self.store, a)
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
        bl.setContentsMargins(24, 14, 24, 16)
        self.browser = orgviews.OrgBrowser(view=ctx.config["directory_view"])
        self.browser.open_person.connect(self._open)
        self.browser.person_menu.connect(self._menu)
        self.browser.view_changed.connect(self._remember_view)
        bl.addWidget(self.browser, 1)
        lay.addWidget(body, 1)
        self._timer = QTimer(self, singleShot=True, interval=300,
                             timeout=lambda: self.rebuild() if self.isVisible() else None)
        for sig in (self.store.users_changed, self.store.user_updated, self.store.me_changed):
            sig.connect(lambda *_: self._timer.start() if not self._timer.isActive() else None)
        ctx.avatars.changed.connect(lambda _uid: self.browser.update_views())

    def rebuild(self):
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
        m.addAction(icon("attachment", T.TEXT, 16), "Send files...", lambda: self.ctx._send_files_to(conv))
        m.addAction(icon("screen", T.TEXT, 16), "Ask to see their screen...",
                    lambda: self.ctx.screens.invite(uid, "request"))
        self.ctx.add_manage_actions(m, uid)
        m.exec(pos)


class TransferRow(QFrame):
    def __init__(self, manager, t, store):
        super().__init__()
        self.manager = manager
        self.t = t
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
        self.name = plain(QLabel(t.name))
        self.name.setStyleSheet("font-weight: 600;")
        where = ""
        if t.conv and store.conv_exists(t.conv):
            where = ("to " if t.kind == "upload" else "from ") + store.title(t.conv)
        self.where = where
        self.meta = QLabel()
        self.meta.setStyleSheet(f"color: {T.MUTED}; font-size: 8.5pt;")
        self.bar = QProgressBar()
        self.bar.setFixedHeight(6)
        self.bar.setRange(0, 1000)
        col.addWidget(self.name)
        col.addWidget(self.bar)
        col.addWidget(self.meta)
        lay.addLayout(col, 1)
        self.b_cancel = IconButton("close", "Cancel", 32, 16)
        self.b_cancel.clicked.connect(t.cancel)
        self.b_retry = IconButton("refresh", "Retry", 32, 18)
        self.b_retry.clicked.connect(lambda: manager.retry(t))
        self.b_open = IconButton("open", "Open", 32, 18, T.ACCENT, T.ACCENT)
        self.b_open.clicked.connect(lambda: open_path(t.dest_path if t.kind == "download" else t.path))
        self.b_folder = IconButton("folder", "Show in folder", 32, 18)
        self.b_folder.clicked.connect(lambda: show_in_folder(t.dest_path if t.kind == "download" else t.path))
        for b in (self.b_cancel, self.b_retry, self.b_open, self.b_folder):
            lay.addWidget(b)
        self.refresh()

    def refresh(self):
        t = self.t
        pct = t.done / t.size if t.size else (1 if t.state == "done" else 0)
        self.bar.setValue(int(pct * 1000))
        if t.active:
            speed = f"  ·  {P.human_size(t.speed)}/s" if t.speed else ""
            state = f"{P.human_size(t.done)} of {P.human_size(t.size)}{speed}"
        elif t.state == "done":
            state = f"{P.human_size(t.size)}  ·  {'Sent' if t.kind == 'upload' else 'Downloaded'}"
        elif t.state == "cancelled":
            state = "Cancelled"
        else:
            state = f"<span style='color:{T.DANGER}'>Failed: {esc(t.error)}</span>"
        self.meta.setText(f"{state}  ·  {esc(self.where)}" if self.where else state)
        self.bar.setVisible(t.active)
        self.b_cancel.setVisible(t.active)
        self.b_retry.setVisible(t.state in ("failed", "cancelled"))
        self.b_open.setVisible(t.state == "done")
        self.b_folder.setVisible(t.state == "done")


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
        clear = QPushButton("Clear finished")
        clear.clicked.connect(self.clear_finished)
        folder = QPushButton(" Downloads folder")
        folder.setIcon(icon("folder", T.TEXT, 16))
        folder.clicked.connect(lambda: open_path(manager.config["download_dir"]))
        header.lay.addWidget(folder)
        header.lay.addWidget(clear)
        lay.addWidget(header)
        self.area, self.col = scroll_column()
        lay.addWidget(self.area, 1)
        self.empty = QLabel("No file transfers yet.\nDrag files into a chat to send them.")
        self.empty.setAlignment(Qt.AlignCenter)
        self.empty.setStyleSheet(f"color: {T.FAINT}; padding: 40px;")
        self.col.insertWidget(0, self.empty)
        manager.added.connect(self.add)
        manager.changed.connect(self.changed)

    def add(self, t):
        if getattr(t, "hidden", False):
            return
        self.empty.hide()
        row = TransferRow(self.manager, t, self.store)
        self.rows[id(t)] = row
        self.col.insertWidget(0, row)

    def changed(self, t):
        row = self.rows.get(id(t))
        if row:
            row.refresh()

    def clear_finished(self):
        self.manager.clear_finished()
        for key, row in list(self.rows.items()):
            if not row.t.active:
                row.deleteLater()
                del self.rows[key]
        self.empty.setVisible(not self.rows)

    def active_count(self):
        return sum(1 for r in self.rows.values() if r.t.active)
