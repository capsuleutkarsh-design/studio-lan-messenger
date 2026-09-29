"""Left column: heading, search, filters and the Chats / Contacts / Rooms lists."""

from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QLineEdit, QMenu, QPushButton, QScrollArea, QStackedWidget,
    QVBoxLayout, QWidget,
)

from common import protocol as P
from common import theme as T
from common.icons import icon
from client import stickers
from client.ui.widgets import (
    ELLIPSIS, SEP, ConvItem, EmptyState, IconButton, SectionLabel, first_name, fmt_last_seen, fmt_list_time,
    popup_pos,
)


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
        self.empty = EmptyState()

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

    def show_empty(self, title, text="", icon_name="chat", button=None, action=None):
        self.empty.set(icon_name, title, text, button, action)
        self.add(self.empty)
        self.empty.show()


class SidebarEdge(QWidget):
    """The thin strip between the chat list and the chat: drag it to make the list wider or narrower,
    double-click for the normal width."""
    moved = Signal(int)
    released = Signal()

    def __init__(self, sidebar):
        super().__init__()
        self.sidebar = sidebar
        self.setFixedWidth(5)
        self.setCursor(Qt.SizeHorCursor)
        self.setToolTip(f"Drag to resize the list{SEP}double-click for the normal width")
        self._start = None
        self._hover = False

    def enterEvent(self, e):
        self._hover = True
        self.update()

    def leaveEvent(self, e):
        self._hover = False
        self.update()

    def paintEvent(self, _):
        from PySide6.QtGui import QColor, QPainter
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(T.PANEL))
        if self._hover or self._start is not None:
            p.fillRect(self.width() // 2 - 1, 0, 2, self.height(), QColor(T.ACCENT))

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._start = (e.globalPosition().x(), self.sidebar.width())

    def mouseMoveEvent(self, e):
        if self._start is not None:
            x0, w0 = self._start
            self.moved.emit(int(w0 + e.globalPosition().x() - x0))

    def mouseReleaseEvent(self, e):
        if self._start is not None:
            self._start = None
            self.update()
            self.released.emit()

    def mouseDoubleClickEvent(self, e):
        self.moved.emit(Sidebar.WIDTH)
        self.released.emit()


class Sidebar(QFrame):
    open_conv = Signal(str)
    new_room = Signal()
    conv_menu = Signal(str, object)
    show_saved = Signal()
    go_page = Signal(str)                   # an empty list's button: "Find people" opens People
    toast = Signal(str)                     # a short confirmation for the main window ("Marked 3 chats as read")
    search_messages = Signal(str)           # "Nothing found" in the list: look for the words in messages instead

    PAGES = ("chats", "contacts", "rooms")
    WIDTH, MIN_WIDTH, MAX_WIDTH = 330, 250, 560     # the edge next to the chat can be dragged
    PLACEHOLDERS = {"chats": "Search chats, people and rooms", "contacts": "Search people", "rooms": "Search rooms"}
    MAX_EXTRA = 30                          # people / rooms without a chat shown under a search, at most

    def __init__(self, store):
        super().__init__()
        self.store = store
        self.active_conv = None
        self.typing = {}
        self.offline = False                # the server is away: nobody's presence is known
        self._kb = None                     # the row picked with the arrow keys in the search box
        self.setFixedWidth(self.WIDTH)
        self.setStyleSheet(f"Sidebar {{ background: {T.PANEL}; }}")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 16, 0, 0)
        lay.setSpacing(10)

        head = QHBoxLayout()
        head.setContentsMargins(16, 0, 12, 0)             # one left edge: heading, search, chips and avatars
        self.heading = QLabel("Chats")
        self.heading.setStyleSheet("font-size: 16pt; font-weight: 800;")
        self.heading.setMinimumHeight(36)          # the same height with or without the buttons beside it
        head.addWidget(self.heading, 1)
        self.b_read_all = IconButton("check_all", "Mark every chat as read", 36, 18, T.TEXT, T.ACCENT, round_=False)
        self.b_read_all.clicked.connect(self._read_all)
        self.b_read_all.hide()
        head.addWidget(self.b_read_all)
        self.b_saved = IconButton("bookmark", "Saved for later", 36, 18, T.TEXT, T.ACCENT, round_=False)
        self.b_saved.clicked.connect(self.show_saved.emit)
        head.addWidget(self.b_saved)
        self.b_new_room = IconButton("plus", "New chat or room", 36, 18, T.TEXT, T.ACCENT, round_=False)
        self.b_new_room.clicked.connect(self._plus_clicked)
        head.addWidget(self.b_new_room)
        lay.addLayout(head)

        self.search = QLineEdit()
        self.search.setPlaceholderText(self.PLACEHOLDERS["chats"])
        self.search.addAction(icon("search", T.META, 16), QLineEdit.LeadingPosition)
        self.search.setClearButtonEnabled(True)
        self.search.setStyleSheet(f"QLineEdit {{ border-radius: 14px; padding: 7px 10px; background: {T.SURFACE};"
                                  f" border: 1px solid {T.SURFACE}; }}"
                                  f"QLineEdit:focus {{ border: 1px solid {T.ACCENT_FOCUS}; background: {T.SURFACE}; }}")
        self.search.textChanged.connect(lambda: self.rebuild())
        self.search.returnPressed.connect(self.open_first)
        self.search.installEventFilter(self)            # arrow keys walk the list, Esc clears
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
        store.prefs_changed.connect(lambda key: key in ("", "pinned_chats") and self.schedule_rebuild())
        store.unread_changed.connect(lambda *_: self._update_read_all())

    # ---------------------------------------------------------------- api
    def schedule_rebuild(self, *_):
        if not self._rebuild_timer.isActive():
            self._rebuild_timer.start()

    def update_permissions(self):
        rooms = bool(self.store.perm("create_rooms"))
        # Chats: new chat (and new room); Rooms: new room; People: none (every row there starts a chat)
        self.b_new_room.setVisible(self.page == "chats" or (self.page == "rooms" and rooms))
        self.b_new_room.setToolTip("New room" if self.page == "rooms" else
                                   "New chat or room" if rooms else "New chat")

    def _plus_clicked(self):
        if self.page != "chats":
            self.new_room.emit()
            return
        if not self.store.perm("create_rooms"):
            self.new_chat()
            return
        m = QMenu(self)
        m.addAction(icon("chat", T.TEXT, 16), "New chat" + ELLIPSIS, self.new_chat)
        m.addAction(icon("hash", T.TEXT, 16), "New room" + ELLIPSIS, self.new_room.emit)
        m.exec(popup_pos(self.b_new_room, m.sizeHint()))

    def new_chat(self):
        """A chat with anyone: the People list, with the cursor in its search box."""
        self.go_page.emit("contacts")
        self.search.setFocus()
        self.search.selectAll()

    def set_offline(self, offline):
        """Connection lost: the presence dots would be stale, so they go until the server is back."""
        if self.offline != bool(offline):
            self.offline = bool(offline)
            self.rebuild()

    def set_filter(self, key):
        self.filter = key
        for k, b in self.chip_buttons.items():
            b.setChecked(k == key)
        self.rebuild()

    def _update_read_all(self):
        self.b_read_all.setVisible(self.page == "chats" and self.store.total_unread() > 0)

    def _read_all(self):
        n = self.store.mark_all_read()
        self._update_read_all()
        if n:
            self.toast.emit(f"Marked {n} chat{'s' if n != 1 else ''} as read")

    def show_page(self, name):
        self.page = name
        self._update_read_all()
        self.b_saved.setVisible(name == "chats")
        self.chips.setVisible(name == "chats")
        self.heading.setText({"chats": "Chats", "contacts": "People", "rooms": "Rooms"}[name])
        self.search.setPlaceholderText(self.PLACEHOLDERS[name])
        self.update_permissions()                  # "+ new room" belongs to Chats and Rooms, not People
        self.stack.setCurrentWidget(self.lists[name])
        self.rebuild()

    def set_active(self, conv):
        previous, self.active_conv = self.active_conv, conv
        for lst in self.lists.values():
            for c, item in lst.items.items():
                item.set_active(c == conv)
                if c in (previous, conv):
                    self._fill_item(item)         # a draft shows once you leave the chat

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
            return first_name(self.store.user_name(m["sender_id"]), "Someone") + ": " + text
        return text

    def _fill_item(self, item):
        s = self.store
        conv = item.conv
        kind, target = P.parse_conv(conv)
        c = s.convs.get(conv)
        unread = c.unread if c else 0
        when = fmt_list_time(c.last_ts) if c and c.last else ""
        if kind == "u" and target == s.my_id and self.page == "chats":      # My space: notes, not a person
            item.set_data("My space", self._preview(conv) if c and c.last else "Notes, to-dos and files for yourself",
                          when, 0, None)
        elif kind == "u":
            u = s.users.get(target, {})
            status = u.get("status", "offline")
            if self.offline:                   # not known while the server is away: no dot rather than a stale one
                chats = self.page == "chats"
                item.set_data(u.get("name", "?"), self._preview(conv) if chats else
                              (u.get("designation") or u.get("title") or ""), when if chats else "",
                              unread, None, muted=s.is_muted(conv), pinned=chats and s.is_pinned(conv))
                if chats:
                    self._draft(item, c)
                item.typing = bool(self.typing.get(conv))
                item.set_active(conv == self.active_conv)
                return
            if self.page == "contacts":
                designation = u.get("designation") or u.get("title") or ""
                custom = self.store.status_text(u)
                if status == "offline":
                    sub = custom or " · ".join(x for x in (designation, fmt_last_seen(u.get("last_seen"))) if x)
                else:
                    sub = custom or " · ".join(x for x in (designation, T.STATUS_LABELS.get(status)) if x)
                item.set_data(u.get("name", "?"), sub, "", unread, status, dim=status == "offline")
            else:
                # someone found by a search, not talked to yet: who they are instead of an empty line
                sub = self._preview(conv) or SEP.join(x for x in (u.get("designation") or u.get("title"),
                                                                  u.get("department")) if x) or "No messages yet"
                item.set_data(u.get("name", "?"), sub, when, unread, status,
                              muted=s.is_muted(conv), pinned=self.page == "chats" and s.is_pinned(conv))
                self._draft(item, c)
        else:
            room = s.rooms.get(target, {})
            about = room.get("topic") or f"{len(room.get('members', []))} members"
            sub = (self._preview(conv) or about) if self.page == "chats" else about
            item.set_data(room.get("name", "Room"), sub, when, unread, room=True, muted=s.is_muted(conv),
                          pinned=self.page == "chats" and s.is_pinned(conv))
            if self.page == "chats":
                self._draft(item, c)
        item.typing = bool(self.typing.get(conv))
        item.set_active(conv == self.active_conv)

    def _draft(self, item, c):
        """Half-typed text left in a chat shows in the list, in red, until it is sent."""
        text = (c.draft or "").strip() if c else ""
        item.draft = bool(text) and item.conv != self.active_conv
        if item.draft:
            item.subtitle = text.replace("\n", " ")
            item.update()

    def _make_item(self, conv):
        item = ConvItem(conv)
        item.clicked.connect(self.open_conv.emit)
        item.context.connect(self.conv_menu.emit)
        self._fill_item(item)
        return item

    # ------------------------------------------------------------ keyboard
    def eventFilter(self, obj, e):
        if obj is self.search and e.type() == QEvent.KeyPress:
            key = e.key()
            if key in (Qt.Key_Down, Qt.Key_Up):
                self._move_kb(1 if key == Qt.Key_Down else -1)
                return True
            if key == Qt.Key_Escape and self.search.text():
                self.search.clear()
                return True
        return super().eventFilter(obj, e)

    def _rows(self):
        return list(self.lists[self.page].items.values())      # dict order is the order on screen

    def _move_kb(self, step):
        rows = self._rows()
        if not rows:
            return
        convs = [r.conv for r in rows]
        i = convs.index(self._kb) + step if self._kb in convs else (0 if step > 0 else len(rows) - 1)
        self._set_kb(convs[max(0, min(len(rows) - 1, i))])

    def _set_kb(self, conv):
        """Light up one row (the same look as the mouse hover) as the one Enter opens."""
        for r in self._rows():
            lit = r.conv == conv
            if r._hover != lit:
                r._hover = lit
                r.update()
        self._kb = conv
        row = self.lists[self.page].items.get(conv)
        if row:
            self.lists[self.page].ensureWidgetVisible(row, 0, 8)

    def open_first(self):
        """Enter in the search box: open the row picked with the arrow keys, or else the first one."""
        rows = self._rows()
        if not rows or (self._kb is None and not self.search.text().strip()):
            return
        conv = self._kb if self._kb in self.lists[self.page].items else rows[0].conv
        self.open_conv.emit(conv)
        if self.search.text():
            self.search.clear()

    def rebuild(self):
        lst = self.lists[self.page]
        bar = lst.verticalScrollBar()
        pos = bar.value()
        lst.setUpdatesEnabled(False)
        lst.clear()
        self._kb = None
        s = self.store
        if self.page == "chats":
            def wanted(conv):
                c = s.convs.get(conv)
                if self.filter == "unread" and not (c and c.unread):
                    return False
                if self.filter in ("people", "rooms") and not conv.startswith("u:" if self.filter == "people"
                                                                               else "r:"):
                    return False
                return self._query_match(s.title(conv))

            mine = P.direct_conv(s.my_id) if s.my_id else None
            pinned = [c for c in s.pinned_chats() if c != mine and wanted(c)]
            convs = [c for c in s.convs.values() if (c.last or (c.draft or "").strip()) and s.conv_exists(c.conv)
                     and c.conv != mine and c.conv not in pinned and wanted(c.conv)]
            convs.sort(key=lambda c: c.last_ts, reverse=True)
            n = 0
            if mine and self.filter in ("all", "people") and self._query_match("My space", "notes", "me"):
                lst.add(self._make_item(mine))              # always at the top, even while empty
                n += 1
            people, rooms = self._search_extras(set(pinned) | {c.conv for c in convs} | {mine})
            if pinned:
                lst.add(SectionLabel("Pinned"))
                for conv in pinned:
                    lst.add(self._make_item(conv))
                    n += 1
            if convs and (pinned or people or rooms):
                lst.add(SectionLabel("Chats" if self.filter == "all" else "Recent"))
            for c in convs:
                lst.add(self._make_item(c.conv))
                n += 1
            # typing a name finds everyone, not only the people you have already talked to (Ctrl+K, New chat)
            for label, group in (("People", people), ("Rooms", rooms)):
                if group:
                    lst.add(SectionLabel(label))
                for conv in group:
                    lst.add(self._make_item(conv))
                    n += 1
            if not n:
                q = self.search.text().strip()
                if q:
                    lst.show_empty("Nothing found", f"No chat, person or room called “{q}”.", "search",
                                   "Search messages", lambda: self.search_messages.emit(q))
                elif self.filter == "unread":
                    lst.show_empty("You're all caught up", "No unread messages.", "check")
                elif self.filter == "rooms":
                    lst.show_empty("No room chats yet", "Rooms you are in show here once someone writes.", "hash",
                                   "Browse rooms", lambda: self.go_page.emit("rooms"))
                else:
                    lst.show_empty("No chats yet", "Pick someone in People to start chatting.", "chat",
                                   "Find people", lambda: self.go_page.emit("contacts"))
        elif self.page == "contacts":
            order = {"online": 0, "busy": 1, "away": 2, "offline": 3}

            def sort_key(u):
                return (order.get(u["status"], 3), -(u.get("level") or 0), u["name"].lower())

            def online(members):
                if self.offline:
                    return f"{len(members)}"
                return f"{sum(1 for u in members if u['status'] != 'offline')}/{len(members)} online"

            matches = [u for u in s.users.values() if not s.is_builtin(u) and self._query_match(
                u["name"], u["username"], u["department"], u.get("section"), u.get("designation"), u["title"])]
            team = [u for u in matches if u.get("manager_id") == s.my_id]
            if team:
                lst.add(SectionLabel(f"My team{SEP}{online(team)}"))
                for u in sorted(team, key=sort_key):
                    lst.add(self._make_item(P.direct_conv(u["id"])))
            groups = {}
            for u in matches:
                groups.setdefault(u["department"] or "Other", {}).setdefault(u.get("section") or "", []).append(u)
            for dept in sorted(groups, key=lambda d: (d == "Other", d.lower())):
                sections = groups[dept]
                everyone = [u for members in sections.values() for u in members]
                lst.add(SectionLabel(f"{dept}{SEP}{online(everyone)}"))
                for u in sorted(sections.pop("", []), key=sort_key):
                    lst.add(self._make_item(P.direct_conv(u["id"])))
                for sect in sorted(sections, key=str.lower):
                    lst.add(SectionLabel(f"{sect}{SEP}{online(sections[sect])}", sub=True))
                    for u in sorted(sections[sect], key=sort_key):
                        lst.add(self._make_item(P.direct_conv(u["id"])))
            if not matches:
                if self.search.text():
                    lst.show_empty("Nobody found", "Try a first name, a department or a section.", "search")
                else:
                    lst.show_empty("Nobody here yet", "People appear here once the admin adds them.", "users")
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
                if self.search.text():
                    lst.show_empty("Nothing found", "No room with that name.", "search")
                elif self.store.perm("create_rooms"):
                    lst.show_empty("No rooms yet", "A room is a group chat for a show, a team or a topic.",
                                   "hash", "Create a room", self.new_room.emit)
                else:
                    lst.show_empty("No rooms yet", "You're added to rooms by the people who create them.", "hash")
        lst.setUpdatesEnabled(True)
        bar.setValue(pos)

    def _search_extras(self, listed):
        """While searching the Chats list: the people and rooms that match but have no chat yet."""
        s = self.store
        if not self.search.text().strip() or self.filter == "unread":
            return [], []
        people, rooms = [], []
        if self.filter in ("all", "people"):
            order = {"online": 0, "busy": 1, "away": 2, "offline": 3}
            found = [u for u in s.users.values() if not s.is_builtin(u) and u.get("id") != s.my_id
                     and P.direct_conv(u["id"]) not in listed
                     and self._query_match(u.get("name"), u.get("username"), u.get("department"),
                                           u.get("section"), u.get("designation"), u.get("title"))]
            found.sort(key=lambda u: (order.get(u.get("status"), 3), (u.get("name") or "").lower()))
            people = [P.direct_conv(u["id"]) for u in found[:self.MAX_EXTRA]]
        if self.filter in ("all", "rooms"):
            found = [r for r in s.rooms.values() if P.room_conv(r["id"]) not in listed
                     and self._query_match(r.get("name"), r.get("topic"))]
            found.sort(key=lambda r: (r.get("name") or "").lower())
            rooms = [P.room_conv(r["id"]) for r in found[:self.MAX_EXTRA]]
        return people, rooms

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
            first = next((c for c in lst.items if c != P.direct_conv(self.store.my_id)
                          and not self.store.is_pinned(c)), None)
            if conv not in lst.items or (first != conv and not self.store.is_pinned(conv)):
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
