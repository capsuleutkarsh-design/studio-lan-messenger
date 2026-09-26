"""Left column: heading, search, filters and the Chats / Contacts / Rooms lists."""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton, QScrollArea, QStackedWidget,
    QVBoxLayout, QWidget,
)

from common import protocol as P
from common import theme as T
from common.icons import icon
from client import stickers
from client.ui.widgets import ConvItem, IconButton, SectionLabel, fmt_last_seen, fmt_list_time, plain


class ItemList(QScrollArea):
    """Scrollable column of ConvItems and section labels."""

    def __init__(self):
        super().__init__()
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        w = QWidget()
        T.bg_pane(w, T.PANEL)
        self.lay = QVBoxLayout(w)
        self.lay.setContentsMargins(0, 2, 0, 8)
        self.lay.setSpacing(0)
        self.lay.addStretch(1)
        self.setWidget(w)
        self.items: dict[str, ConvItem] = {}
        self.empty = plain(QLabel())
        self.empty.setWordWrap(True)
        self.empty.setAlignment(Qt.AlignCenter)
        self.empty.setStyleSheet(f"color: {T.FAINT}; padding: 40px 24px; line-height: 150%;")

    def clear(self):
        while self.lay.count() > 1:
            w = self.lay.takeAt(0).widget()
            if w is self.empty:
                w.hide()
            elif w:
                w.deleteLater()
        self.items = {}

    def add(self, w):
        self.lay.insertWidget(self.lay.count() - 1, w)
        if isinstance(w, ConvItem):
            self.items[w.conv] = w

    def show_empty(self, text):
        self.empty.setText(text)
        self.add(self.empty)
        self.empty.show()


class Sidebar(QFrame):
    open_conv = Signal(str)
    new_room = Signal()
    conv_menu = Signal(str, object)

    PAGES = ("chats", "contacts", "rooms")

    def __init__(self, store):
        super().__init__()
        self.store = store
        self.active_conv = None
        self.typing = {}
        self.setFixedWidth(330)
        self.setStyleSheet(f"Sidebar {{ background: {T.PANEL}; }}")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 16, 0, 0)
        lay.setSpacing(10)

        head = QHBoxLayout()
        head.setContentsMargins(20, 0, 12, 0)
        self.heading = QLabel("Chats")
        self.heading.setStyleSheet("font-size: 16pt; font-weight: 800;")
        head.addWidget(self.heading, 1)
        self.b_new_room = IconButton("plus", "New chat room", 36, 18, T.TEXT, T.ACCENT, round_=False)
        self.b_new_room.clicked.connect(self.new_room.emit)
        head.addWidget(self.b_new_room)
        lay.addLayout(head)

        self.search = QLineEdit()
        self.search.setPlaceholderText("Search people and rooms")
        self.search.addAction(icon("search", T.FAINT, 16), QLineEdit.LeadingPosition)
        self.search.setClearButtonEnabled(True)
        self.search.setStyleSheet(f"QLineEdit {{ border-radius: 14px; padding: 7px 10px; background: {T.SURFACE};"
                                  f" border: 1px solid {T.SURFACE}; }}"
                                  f"QLineEdit:focus {{ border: 1px solid {T.ACCENT_FOCUS}; background: {T.SURFACE}; }}")
        self.search.textChanged.connect(lambda: self.rebuild())
        sw = QHBoxLayout()
        sw.setContentsMargins(16, 0, 16, 0)
        sw.addWidget(self.search)
        lay.addLayout(sw)

        # quick filters for the Chats list
        self.filter = "all"
        self.chips = QWidget()
        cl = QHBoxLayout(self.chips)
        cl.setContentsMargins(16, 0, 16, 2)
        cl.setSpacing(6)
        self.chip_buttons = {}
        for key, label in (("all", "All"), ("unread", "Unread"), ("people", "People"), ("rooms", "Rooms")):
            b = QPushButton(label)
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            T.polish(b, chip=True)
            b.clicked.connect(lambda _=False, k=key: self.set_filter(k))
            cl.addWidget(b)
            self.chip_buttons[key] = b
        cl.addStretch(1)
        self.chip_buttons["all"].setChecked(True)
        lay.addWidget(self.chips)

        self.stack = QStackedWidget()
        self.lists = {name: ItemList() for name in self.PAGES}
        for name in self.PAGES:
            self.stack.addWidget(self.lists[name])
        lay.addWidget(self.stack, 1)
        self.page = "chats"

        # bursts of changes (e.g. 200 people coming online at 9 AM) -> one rebuild
        from PySide6.QtCore import QTimer
        self._rebuild_timer = QTimer(self, singleShot=True, interval=200, timeout=self.rebuild)
        store.users_changed.connect(self.schedule_rebuild)
        store.rooms_changed.connect(self.schedule_rebuild)
        store.user_updated.connect(self._user_updated)
        store.conv_changed.connect(self._conv_changed)
        store.typing.connect(self._typing)

    # ---------------------------------------------------------------- api
    def schedule_rebuild(self, *_):
        if not self._rebuild_timer.isActive():
            self._rebuild_timer.start()

    def update_permissions(self):
        self.b_new_room.setVisible(bool(self.store.perm("create_rooms")))

    def set_filter(self, key):
        self.filter = key
        for k, b in self.chip_buttons.items():
            b.setChecked(k == key)
        self.rebuild()

    def show_page(self, name):
        self.page = name
        self.chips.setVisible(name == "chats")
        self.heading.setText({"chats": "Chats", "contacts": "People", "rooms": "Rooms"}[name])
        self.stack.setCurrentWidget(self.lists[name])
        self.rebuild()

    def set_active(self, conv):
        self.active_conv = conv
        for lst in self.lists.values():
            for c, item in lst.items.items():
                item.set_active(c == conv)

    # ------------------------------------------------------------ building
    def _query_match(self, *texts):
        q = self.search.text().strip().lower()
        return not q or any(q in (t or "").lower() for t in texts)

    def _preview(self, conv):
        c = self.store.convs.get(conv)
        if not c or not c.last:
            return ""
        m = c.last
        if m["kind"] == "system":
            return m["body"]
        text = stickers.summary(m)[:200].replace("\n", " ")
        if m["sender_id"] == self.store.my_id:
            return "You: " + text
        if conv.startswith("r:"):
            return self.store.user_name(m["sender_id"]).split()[0] + ": " + text
        return text

    def _fill_item(self, item):
        s = self.store
        conv = item.conv
        kind, target = P.parse_conv(conv)
        c = s.convs.get(conv)
        unread = c.unread if c else 0
        when = fmt_list_time(c.last_ts) if c and c.last else ""
        if kind == "u":
            u = s.users.get(target, {})
            status = u.get("status", "offline")
            if self.page == "contacts":
                designation = u.get("designation") or u.get("title") or ""
                custom = self.store.status_text(u)
                if status == "offline":
                    sub = custom or " · ".join(x for x in (designation, fmt_last_seen(u.get("last_seen"))) if x)
                else:
                    sub = custom or " · ".join(x for x in (designation, T.STATUS_LABELS.get(status)) if x)
                item.set_data(u.get("name", "?"), sub, "", unread, status, dim=status == "offline")
            else:
                item.set_data(u.get("name", "?"), self._preview(conv), when, unread, status,
                              muted=s.is_muted(conv))
        else:
            room = s.rooms.get(target, {})
            sub = self._preview(conv) if self.page == "chats" else (
                room.get("topic") or f"{len(room.get('members', []))} members")
            item.set_data(room.get("name", "Room"), sub, when, unread, room=True, muted=s.is_muted(conv))
        item.typing = bool(self.typing.get(conv))
        item.set_active(conv == self.active_conv)

    def _make_item(self, conv):
        item = ConvItem(conv)
        item.clicked.connect(self.open_conv.emit)
        item.context.connect(self.conv_menu.emit)
        self._fill_item(item)
        return item

    def rebuild(self):
        lst = self.lists[self.page]
        bar = lst.verticalScrollBar()
        pos = bar.value()
        lst.setUpdatesEnabled(False)
        lst.clear()
        s = self.store
        if self.page == "chats":
            convs = [c for c in s.convs.values() if c.last and s.conv_exists(c.conv)]
            if self.filter == "unread":
                convs = [c for c in convs if c.unread]
            elif self.filter in ("people", "rooms"):
                convs = [c for c in convs if c.conv.startswith("u:" if self.filter == "people" else "r:")]
            convs.sort(key=lambda c: c.last_ts, reverse=True)
            n = 0
            for c in convs:
                if self._query_match(s.title(c.conv)):
                    lst.add(self._make_item(c.conv))
                    n += 1
            if not n:
                if self.search.text():
                    lst.show_empty("Nothing found.")
                elif self.filter == "unread":
                    lst.show_empty("You're all caught up.")
                else:
                    lst.show_empty("No conversations yet.\nPick someone in People to start chatting.")
        elif self.page == "contacts":
            order = {"online": 0, "busy": 1, "away": 2, "offline": 3}

            def sort_key(u):
                return (order.get(u["status"], 3), -(u.get("level") or 0), u["name"].lower())

            def online(members):
                return f"{sum(1 for u in members if u['status'] != 'offline')}/{len(members)} online"

            matches = [u for u in s.users.values() if self._query_match(
                u["name"], u["username"], u["department"], u.get("section"), u.get("designation"), u["title"])]
            team = [u for u in matches if u.get("manager_id") == s.my_id]
            if team:
                lst.add(SectionLabel(f"My team  ·  {online(team)}"))
                for u in sorted(team, key=sort_key):
                    lst.add(self._make_item(P.direct_conv(u["id"])))
            groups = {}
            for u in matches:
                groups.setdefault(u["department"] or "Other", {}).setdefault(u.get("section") or "", []).append(u)
            for dept in sorted(groups, key=lambda d: (d == "Other", d.lower())):
                sections = groups[dept]
                everyone = [u for members in sections.values() for u in members]
                lst.add(SectionLabel(f"{dept}  ·  {online(everyone)}"))
                for u in sorted(sections.pop("", []), key=sort_key):
                    lst.add(self._make_item(P.direct_conv(u["id"])))
                for sect in sorted(sections, key=str.lower):
                    lst.add(SectionLabel(f"{sect}  ·  {online(sections[sect])}", sub=True))
                    for u in sorted(sections[sect], key=sort_key):
                        lst.add(self._make_item(P.direct_conv(u["id"])))
            if not matches:
                lst.show_empty("Nobody found." if self.search.text() else "No other users yet.")
        else:
            rooms = [r for r in s.rooms.values() if self._query_match(r["name"], r.get("topic"))]
            manual = sorted((r for r in rooms if not r.get("auto")), key=lambda r: r["name"].lower())
            auto = sorted((r for r in rooms if r.get("auto")), key=lambda r: r["name"].lower())
            n = 0
            for label, group in (("Your rooms", manual), ("Department & section rooms", auto)):
                if group and manual and auto:
                    lst.add(SectionLabel(label))
                for r in group:
                    lst.add(self._make_item(P.room_conv(r["id"])))
                    n += 1
            if not n:
                lst.show_empty("You are not in any room yet.\nClick + to create one." if not self.search.text()
                               else "Nothing found.")
        lst.setUpdatesEnabled(True)
        bar.setValue(pos)

    # ------------------------------------------------------------- updates
    def _user_updated(self, uid):
        conv = P.direct_conv(uid)
        if self.page == "contacts":
            self.schedule_rebuild()  # status changes the ordering
            return
        item = self.lists[self.page].items.get(conv)
        if item:
            self._fill_item(item)

    def _conv_changed(self, conv):
        if self.page == "chats":
            lst = self.lists["chats"]
            first = next(iter(lst.items), None)
            if conv not in lst.items or first != conv:
                self.schedule_rebuild()   # new conversation or moved to the top
                return
        item = self.lists[self.page].items.get(conv)
        if item:
            self._fill_item(item)

    def _typing(self, conv, uid):
        from PySide6.QtCore import QTimer
        self.typing[conv] = self.typing.get(conv, 0) + 1
        self._refresh_typing(conv)

        def stop():
            self.typing[conv] -= 1
            self._refresh_typing(conv)
        QTimer.singleShot(5000, stop)

    def _refresh_typing(self, conv):
        item = self.lists[self.page].items.get(conv)
        if item:
            item.typing = bool(self.typing.get(conv))
            item.update()
