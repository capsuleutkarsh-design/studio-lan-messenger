"""Dialogs of the client."""

import base64
import datetime
import os
import re
import time

from PySide6.QtCore import QRect, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QAbstractTextDocumentLayout, QColor, QGuiApplication, QIcon, QPainter, QPalette, QPixmap, \
    QTextDocument
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QButtonGroup, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog,
    QFormLayout, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QListView, QListWidget, QListWidgetItem, QMenu,
    QMessageBox, QPlainTextEdit, QPushButton, QScrollArea, QStyle, QStyledItemDelegate, QStyleOptionViewItem,
    QVBoxLayout, QWidget,
)

from common import protocol as P
from common import theme as T
from common.icons import add_show_password, icon
from client import stickers
from client import avatars
from client.ui.widgets import (
    SEP, Avatar, IconButton, clip, day_word, esc, fmt_time, fmt_when, linkify, menu_text, open_link, paint_avatar, plain,
    popup_pos, rich_safe,
)


def heading(text, first=True):
    """The one small heading of every dialog: upper case, muted, letter-spaced ('STATUS', '10 MEMBERS')."""
    head = plain(QLabel(str(text).upper()))
    head.setStyleSheet(f"color: {T.MUTED}; font-size: {T.pt(T.FONT_XS)}; font-weight: 800; letter-spacing: 1px;"
                       f" padding-top: {0 if first else 10}px;")
    return head


def hint(text=""):
    """A muted line under a list or a field: how to use it, or what is still missing."""
    lbl = plain(QLabel(text))
    lbl.setWordWrap(True)
    lbl.setStyleSheet(f"color: {T.MUTED}; font-size: {T.pt(T.FONT_S)};")
    return lbl


def error_label():
    """An inline error line (hidden while empty): mistakes show next to the fields, not in a pop-up."""
    lbl = plain(QLabel())
    lbl.setWordWrap(True)
    lbl.setStyleSheet(f"color: {T.DANGER}; font-size: {T.pt(T.FONT_S)}; font-weight: 600;")
    lbl.hide()
    return lbl


def danger(button):
    """A red (destructive) button that greys out properly while it can't be used."""
    T.polish(button, danger=True)
    button.setStyleSheet(f"QPushButton:disabled {{ color: {T.FAINT}; }}")
    return button


def set_error(label, text):
    label.setText(text or "")
    label.setVisible(bool(text))


def person_icon(store, uid, status=None, size=28):
    """A person's avatar (photo or initials) with a status dot, as a list-row icon."""
    name = store.user_name(uid)
    pm = QPixmap(size * 2, size * 2)
    pm.setDevicePixelRatio(2)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    paint_avatar(p, QRect(0, 0, size, size), name, name, status=status, uid=uid)
    p.end()
    return QIcon(pm)


# ------------------------------------------------------------------ list rows with two tones of text
RICH_ROLE = Qt.UserRole + 1        # a row's rich text; the item's own text stays plain (search filters, tests)


class RichRows(QStyledItemDelegate):
    """Paints the rows of a QListWidget whose items carry RICH_ROLE html - a name in the text colour, the rest
    muted, a second line... - wrapped to the list's width. Rows without it are drawn as usual.
    The list needs setResizeMode(QListView.Adjust) so the rows re-wrap when it is resized."""
    CHECK_GAP = 6

    @staticmethod
    def _style(widget):
        return widget.style() if widget is not None else QApplication.style()

    @staticmethod
    def _doc(html, font, width):
        doc = QTextDocument()
        doc.setDocumentMargin(0)
        doc.setDefaultFont(font)
        doc.setDefaultStyleSheet(f"body {{ color: {T.TEXT}; }}")
        doc.setHtml(html)
        doc.setTextWidth(max(40, width))
        return doc

    def paint(self, p, option, index):
        html = index.data(RICH_ROLE)
        if not html:
            return super().paint(p, option, index)
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        style = self._style(opt.widget)
        rect = style.subElementRect(QStyle.SE_ItemViewItemText, opt, opt.widget)
        if opt.features & QStyleOptionViewItem.HasCheckIndicator:
            rect.adjust(self.CHECK_GAP, 0, 0, 0)              # a little air between the box and the name
        opt.text = ""
        style.drawControl(QStyle.CE_ItemViewItem, opt, p, opt.widget)   # background, hover, check box, icon
        doc = self._doc(html, opt.font, rect.width())
        p.save()
        top = rect.top() + max(0.0, (rect.height() - doc.size().height()) / 2)
        p.translate(rect.left(), top)
        p.setClipRect(QRectF(0, 0, rect.width(), rect.height()))
        ctx = QAbstractTextDocumentLayout.PaintContext()
        ctx.palette.setColor(QPalette.Text, QColor(T.TEXT))
        doc.documentLayout().draw(p, ctx)
        p.restore()

    def sizeHint(self, option, index):
        html = index.data(RICH_ROLE)
        base = super().sizeHint(option, index)
        if not html:
            return base
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        view = opt.widget
        width = view.viewport().width() if view is not None else 400
        opt.rect = QRect(0, 0, width, 200)
        rect = self._style(view).subElementRect(QStyle.SE_ItemViewItemText, opt, view)
        if opt.features & QStyleOptionViewItem.HasCheckIndicator:
            rect.adjust(self.CHECK_GAP, 0, 0, 0)
        doc = self._doc(html, opt.font, rect.width())
        pad = 200 - rect.height()                 # the item's own padding above and below the text
        return QSize(base.width(), max(base.height(), int(doc.size().height() + 0.99) + max(8, pad)))


def rich_list(word_wrap=True):
    """A QListWidget drawn with RichRows."""
    lst = QListWidget()
    lst.setItemDelegate(RichRows(lst))
    lst.setResizeMode(QListView.Adjust)
    lst.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
    lst.setWordWrap(word_wrap)
    return lst


def muted(text):
    """Rich-text span in the muted colour."""
    return f"<span style='color:{T.MUTED}'>{text}</span>"


def _highlight(text, query):
    """text as rich text with the searched words in bold."""
    words = sorted({w for w in str(query or "").split() if len(w) >= 2}, key=len, reverse=True)
    if not words:
        return esc(text)
    out, last = [], 0
    for m in re.finditer("|".join(re.escape(w) for w in words), text, re.I):
        out.append(esc(text[last:m.start()]))
        out.append(f"<b>{esc(m.group(0))}</b>")
        last = m.end()
    out.append(esc(text[last:]))
    return "".join(out)


def who_where(store, msg):
    """('Farhan Qureshi', 'to you') / ('You', 'to Meera Iyer') / ('Rajiv Menon', 'in AK74 Project'): who wrote a
    message and where - a direct chat is named from both sides, so it never reads 'Farhan → Farhan'."""
    me = store.my_id
    sender = "You" if msg["sender_id"] == me else store.user_name(msg["sender_id"])
    conv = msg["conv"]
    if not store.conv_exists(conv):
        return sender, "in a chat you left"
    if conv.startswith("r:"):
        return sender, f"in {store.title(conv)}"
    target = P.parse_conv(conv)[1]
    if target == me:
        return sender, "in My space"
    return sender, (f"to {store.title(conv)}" if msg["sender_id"] == me else "to you")


def message_row(store, msg, snippet, query="", extra=""):
    """(plain text, rich text) of a message in a list: 'Farhan Qureshi to you · Today 17:30' over the text."""
    sender, where = who_where(store, msg)
    when = fmt_when(msg["ts"]) if msg.get("ts") else ""
    tail = "".join(SEP + x for x in (when, extra) if x)
    text = clip(snippet, 300)
    plain_text = f"{sender} {where}{tail}\n{text}"
    rich = (f"<span style='font-size:{T.pt(T.FONT_S)}'><b>{esc(sender)}</b> {muted(esc(where + tail))}</span>"
            f"<br>{_highlight(text, query)}")
    return plain_text, rich


def _buttons(dialog, ok_text="Save", ok_enabled=True):
    bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
    ok = bb.button(QDialogButtonBox.Ok)
    ok.setText(ok_text)
    ok.setEnabled(ok_enabled)
    T.polish(ok, primary=True)
    bb.accepted.connect(dialog.accept)
    bb.rejected.connect(dialog.reject)
    return bb


class Dialog(QDialog):
    def __init__(self, parent, title, width=440):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(width)
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(22, 20, 22, 18)
        self.lay.setSpacing(12)

    def showEvent(self, e):
        super().showEvent(e)
        T.dark_title_bar(self)
        T.tidy_forms(self)


class MemberPicker(QWidget):
    """Search box + checkable list of users. A click on the name or on the box ticks it; `changed` sends how many
    are ticked."""
    changed = Signal(int)

    def __init__(self, store, checked=(), exclude=()):
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search people…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._filter)
        lay.addWidget(self.search)
        self.list = rich_list(word_wrap=False)
        self.list.setMinimumHeight(260)
        users = sorted(store.users.values(), key=lambda u: (u["department"].lower(), u["name"].lower()))
        for u in users:
            if u["id"] in exclude or u.get("username") == "admin":     # the built-in console account
                continue
            line = store.designation_line(u)
            it = QListWidgetItem(u["name"] + (SEP + line if line else ""))
            it.setData(RICH_ROLE, esc(u["name"]) + (muted(esc(SEP + line)) if line else ""))
            it.setData(Qt.UserRole, u["id"])
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Checked if u["id"] in checked else Qt.Unchecked)
            self.list.addItem(it)
        # a click on the check box is toggled by Qt itself (and its press never reaches itemPressed); a click
        # anywhere else on the row toggles it here
        self._pressed = None
        self.list.itemPressed.connect(lambda it: setattr(self, "_pressed", (it, it.checkState())))
        self.list.itemClicked.connect(self._clicked)
        self.list.itemChanged.connect(lambda _it: self.changed.emit(self.count()))
        lay.addWidget(self.list, 1)

    def _clicked(self, it):
        pressed, self._pressed = self._pressed, None
        if not pressed or pressed[0] is not it or pressed[1] != it.checkState():
            return                                         # the box itself was clicked: already toggled
        it.setCheckState(Qt.Unchecked if it.checkState() == Qt.Checked else Qt.Checked)

    def _filter(self, q):
        q = q.lower()
        for i in range(self.list.count()):
            it = self.list.item(i)
            it.setHidden(bool(q) and q not in it.text().lower())

    def selected(self):
        return [self.list.item(i).data(Qt.UserRole) for i in range(self.list.count())
                if self.list.item(i).checkState() == Qt.Checked]

    def count(self):
        return len(self.selected())


class NewRoomDialog(Dialog):
    def __init__(self, parent, store, preselect=()):
        super().__init__(parent, "New chat room", 460)
        form = QFormLayout()
        self.name = QLineEdit()
        self.name.setPlaceholderText("e.g. Comp — Project X")
        self.topic = QLineEdit()
        self.topic.setPlaceholderText("What it's for (optional)")
        form.addRow("Room name", self.name)
        form.addRow("Topic", self.topic)
        self.lay.addLayout(form)
        self.people = heading("Add people", first=False)
        self.lay.addWidget(self.people)
        self.picker = MemberPicker(store, checked=preselect)
        self.picker.changed.connect(self._count)
        self._count(self.picker.count())
        self.lay.addWidget(self.picker, 1)
        bb = _buttons(self, "Create room", False)
        self.name.textChanged.connect(lambda t: bb.button(QDialogButtonBox.Ok).setEnabled(bool(t.strip())))
        self.lay.addWidget(bb)
        self.name.setFocus()

    def _count(self, n):
        self.people.setText(f"ADD PEOPLE{SEP}{n} SELECTED" if n else "ADD PEOPLE")


class AddMembersDialog(Dialog):
    def __init__(self, parent, store, exclude):
        super().__init__(parent, "Add people", 420)
        self.picker = MemberPicker(store, exclude=exclude)
        self.lay.addWidget(self.picker, 1)
        bb = _buttons(self, "Add", False)
        self.picker.changed.connect(lambda n: (bb.button(QDialogButtonBox.Ok).setEnabled(n > 0),
                                               bb.button(QDialogButtonBox.Ok).setText(f"Add {n}" if n else "Add")))
        self.lay.addWidget(bb)


class RoomInfoDialog(Dialog):
    def __init__(self, ctx, room):
        super().__init__(ctx, room["name"], 440)
        self.ctx = ctx
        self.room = room
        store = ctx.store
        me = store.my_id
        self.auto = bool(room.get("auto"))
        self.can_manage = (room["owner_id"] == me or store.me.get("is_admin")) and not self.auto

        form = QFormLayout()
        self.name = QLineEdit(room["name"])
        self.topic = QLineEdit(room.get("topic", ""))
        if not self.can_manage:                 # read-only: plain readable text, not a greyed-out box
            for e, color in ((self.name, T.TEXT), (self.topic, T.MUTED)):
                e.setReadOnly(True)
                e.setFocusPolicy(Qt.NoFocus)
                e.setStyleSheet(f"QLineEdit {{ background: transparent; border: none; padding-left: 0;"
                                f" color: {color}; }}")
            if not self.topic.text():
                self.topic.setPlaceholderText("No topic")
        form.addRow("Room name", self.name)
        form.addRow("Topic", self.topic)
        self.lay.addLayout(form)
        if self.can_manage:
            self.b_save = QPushButton(menu_text("Save name & topic"))
            self.b_save.clicked.connect(self.save)
            self.b_save.setEnabled(False)
            edited = lambda *_: self.b_save.setEnabled(bool(self.name.text().strip()) and (  # noqa: E731
                self.name.text(), self.topic.text()) != (room["name"], room.get("topic", "")))
            self.name.textChanged.connect(edited)
            self.topic.textChanged.connect(edited)
            self.lay.addWidget(self.b_save, 0, Qt.AlignRight)

        if self.auto:
            note = QLabel("This room is managed automatically: everyone in the department / section is a "
                          "member, and people join or leave it when the admin changes their department.")
            note.setWordWrap(True)
            T.polish(note, muted=True)
            self.lay.addWidget(note)
        head = QHBoxLayout()
        n = len(room["members"])
        head.addWidget(heading(f"{n} member{'s' if n != 1 else ''}"), 0, Qt.AlignVCenter)
        head.addStretch(1)
        if not self.auto:
            add = QPushButton(" Add people")
            add.setIcon(icon("plus", T.ACCENT_TEXT, 16))
            T.polish(add, primary=True)
            add.clicked.connect(self.add_people)
            head.addWidget(add)
        self.lay.addLayout(head)

        self.list = rich_list(word_wrap=False)
        self.list.setMinimumHeight(260)
        self.list.setIconSize(QSize(28, 28))
        if self.can_manage:
            self.list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        members = sorted(room["members"], key=lambda u: store.user_name(u).lower())
        for uid in members:
            u = store.users.get(uid, {})
            status = store.me.get("status", "online") if uid == me else u.get("status", "offline")
            status_word = T.STATUS_LABELS.get(status, status.capitalize())
            designation = (store.me if uid == me else u).get("designation") or ""
            parts = [x for x in ("Owner" if uid == room["owner_id"] else "", designation, status_word) if x]
            name = store.user_name(uid)
            you = " (you)" if uid == me else ""
            it = QListWidgetItem(person_icon(store, uid, status), f"{name}{you}{SEP}{SEP.join(parts)}")
            it.setData(RICH_ROLE, f"{esc(name)}{muted(esc(you + SEP + SEP.join(parts)))}")
            it.setData(Qt.UserRole, uid)
            if uid != me:
                it.setToolTip(f"Double-click to chat with {name}")
            self.list.addItem(it)
        self.list.itemDoubleClicked.connect(self.open_chat)
        self.lay.addWidget(self.list, 1)
        self.lay.addWidget(hint("Double-click someone to chat with them."))

        row = QHBoxLayout()
        if self.can_manage:
            self.b_remove = rm = QPushButton("Remove selected")
            danger(rm)
            rm.clicked.connect(self.remove)
            row.addWidget(rm)
            self.b_owner = owner = QPushButton("Make owner")
            owner.setToolTip("Hand the room over to the selected member (they can then rename it and "
                             "remove members)")
            owner.clicked.connect(self.make_owner)
            row.addWidget(owner)
            self.list.itemSelectionChanged.connect(self._selection)
            self._selection()
        row.addStretch(1)
        if not self.auto:
            leave = QPushButton(" Leave room")
            leave.setIcon(icon("logout", T.DANGER, 16))
            T.polish(leave, danger=True)
            leave.clicked.connect(self.leave)
            row.addWidget(leave)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        row.addWidget(close)
        self.lay.addLayout(row)

    def _update(self, **kw):
        def done(reply):
            if not reply.get("ok"):
                QMessageBox.warning(self, "Room", reply.get("error", "Failed"))
        self.ctx.conn.request("room_update", done, room_id=self.room["id"], **kw)

    def save(self):
        self._update(name=self.name.text().strip(), topic=self.topic.text().strip())
        self.accept()

    def add_people(self):
        dlg = AddMembersDialog(self, self.ctx.store, exclude=set(self.room["members"]))
        if dlg.exec() and dlg.picker.selected():
            self._update(add=dlg.picker.selected())
            self.accept()

    def _removable(self):
        return [it.data(Qt.UserRole) for it in self.list.selectedItems() if it.data(Qt.UserRole) != self.ctx.store.my_id]

    def _selection(self):
        """Remove / Make owner only work on a selection: enabled when there is one they can act on."""
        uids = self._removable()
        self.b_remove.setEnabled(bool(uids))
        self.b_remove.setText(f"Remove {len(uids)} people" if len(uids) > 1 else "Remove selected")
        items = self.list.selectedItems()
        self.b_owner.setEnabled(len(items) == 1 and items[0].data(Qt.UserRole) != self.room["owner_id"])

    def remove(self):
        uids = self._removable()
        if not uids:
            return
        store = self.ctx.store
        who = store.user_name(uids[0]) if len(uids) == 1 else f"{len(uids)} people"
        if QMessageBox.question(self, "Remove from room", rich_safe(
                f"Remove {who} from “{self.room['name']}”?\nThey stop getting its messages and files."),
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        self._update(remove=uids)
        self.accept()

    def make_owner(self):
        items = self.list.selectedItems()
        uid = items[0].data(Qt.UserRole) if len(items) == 1 else None
        if not uid or uid == self.room["owner_id"]:
            QMessageBox.information(self, "Make owner", "Select the one member who should own the room.")
            return
        name = self.ctx.store.user_name(uid)
        if QMessageBox.question(self, "Make owner", rich_safe(
                f"Make {name} the owner of this room?\nThey can then rename it and remove members.")) \
                == QMessageBox.Yes:
            self._update(owner=uid)
            self.accept()

    def leave(self):
        self.accept()
        self.ctx.leave_room(self.room["id"])

    def open_chat(self, it):
        uid = it.data(Qt.UserRole)
        if uid != self.ctx.store.my_id:
            self.accept()
            self.ctx.open_conv(P.direct_conv(uid))


class PollDialog(Dialog):
    """Question, 2-10 answers, and options."""

    def __init__(self, parent):
        super().__init__(parent, "Create a poll", 460)
        self.question = QLineEdit()
        self.question.setPlaceholderText("Ask a question, e.g. Where should we go for the team lunch?")
        self.question.setMaxLength(300)
        self.question.setMinimumHeight(38)
        self.lay.addWidget(heading("Question"))
        self.lay.addWidget(self.question)
        self.lay.addWidget(heading("Answers", first=False))
        self.answers_box = QVBoxLayout()
        self.answers_box.setSpacing(6)
        self.lay.addLayout(self.answers_box)
        self.answers = []
        self.answer_rows = []                  # (row widget, line edit, remove button)
        for _ in range(3):
            self.add_answer()
        self.b_add = QPushButton(" Add an answer")
        self.b_add.setIcon(icon("plus", T.TEXT, 14))
        self.b_add.clicked.connect(lambda: self.add_answer(focus=True))
        self.lay.addWidget(self.b_add, 0, Qt.AlignLeft)
        self.multi = QCheckBox("People can pick more than one answer")
        self.anonymous = QCheckBox("Anonymous — nobody sees who voted for what")
        self.lay.addWidget(self.multi)
        self.lay.addWidget(self.anonymous)
        self.missing = hint()
        self.lay.addWidget(self.missing)
        self.bb = _buttons(self, "Create poll", False)
        self.lay.addWidget(self.bb)
        self.question.textChanged.connect(self._check)
        self._check()

    def add_answer(self, focus=False):
        if len(self.answers) >= 10:
            return
        row = QWidget()
        rl = QHBoxLayout(row)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(4)
        e = QLineEdit()
        e.setMaxLength(100)
        e.textChanged.connect(self._check)
        rl.addWidget(e, 1)
        rm = IconButton("close", "Remove this answer", 34, 14)
        keep = rm.sizePolicy()
        keep.setRetainSizeWhenHidden(True)             # the answer boxes keep one width
        rm.setSizePolicy(keep)
        rm.clicked.connect(lambda: self.remove_answer(row))
        rl.addWidget(rm)
        self.answers_box.addWidget(row)
        self.answers.append(e)
        self.answer_rows.append((row, e, rm))
        self._renumber()
        if focus:
            e.setFocus()

    def remove_answer(self, row):
        """Drop an answer row (a poll keeps at least two)."""
        for k, (w, e, _rm) in enumerate(self.answer_rows):
            if w is row and len(self.answer_rows) > 2:
                del self.answer_rows[k]
                self.answers.remove(e)
                w.hide()
                w.deleteLater()
                break
        self._renumber()
        self._check()

    def _renumber(self):
        many = len(self.answer_rows) > 2
        for k, (_w, e, rm) in enumerate(self.answer_rows):
            e.setPlaceholderText(f"Answer {k + 1}")
            rm.setVisible(many)
        if hasattr(self, "b_add"):
            self.b_add.setEnabled(len(self.answers) < 10)

    def _options(self):
        return [e.text().strip() for e in self.answers if e.text().strip()]

    def _check(self):
        has_q = bool(self.question.text().strip())
        n = len({o.lower() for o in self._options()})
        ok = has_q and n >= 2
        self.bb.button(QDialogButtonBox.Ok).setEnabled(ok)
        if ok:
            missing = ""
        elif not has_q:
            missing = "Add a question and at least two different answers."
        else:
            missing = "Add at least two different answers."
        self.missing.setText(missing)
        self.missing.setVisible(bool(missing))

    def values(self):
        return {"question": self.question.text().strip(), "options": self._options(),
                "multi": self.multi.isChecked(), "anonymous": self.anonymous.isChecked()}


STATUS_PRESETS = [("🍽️", "Out for lunch", "1h"), ("☕", "Tea break", "30m"), ("📅", "In a meeting", "1h"),
                  ("🎬", "Rendering — don't touch", "4h"), ("🎧", "Focus mode", "4h"),
                  ("🏠", "Working from home", "today"), ("🤒", "Out sick", "today"), ("🌴", "On leave", "week")]
CLEAR_AFTER = [("never", "Don't clear"), ("30m", "30 minutes"), ("1h", "1 hour"), ("4h", "4 hours"),
               ("today", "Today"), ("week", "This week")]
CLEARS_WHEN = {"30m": "clears after 30 minutes", "1h": "clears after an hour", "4h": "clears after 4 hours",
               "today": "clears at the end of the day", "week": "clears at the end of the week"}


def clear_after_time(key):
    """Timestamp for a 'clear after' choice (None = never)."""
    now = time.time()
    if key in ("30m", "1h", "4h"):
        return now + {"30m": 1800, "1h": 3600, "4h": 4 * 3600}[key]
    today_end = datetime.datetime.combine(datetime.date.today(), datetime.time(23, 59, 59))
    if key == "today":
        return today_end.timestamp()
    if key == "week":
        return (today_end + datetime.timedelta(days=6 - today_end.weekday())).timestamp()
    return None


class ProfileDialog(Dialog):
    """My photo and my custom status (emoji + text + when to clear it)."""

    def __init__(self, ctx):
        super().__init__(ctx, "My profile", 480)
        self.ctx = ctx
        store = ctx.store
        me = store.me

        top = QHBoxLayout()
        top.setSpacing(16)
        self.avatar = Avatar(88)
        self.avatar.set(me.get("name", ""), me.get("name", ""), uid=store.my_id, ring=T.BG)
        top.addWidget(self.avatar)
        col = QVBoxLayout()
        col.setSpacing(2)
        name_row = QHBoxLayout()
        name_row.setSpacing(6)
        self.name_label = name = plain(QLabel(me.get("name", "")))
        name.setStyleSheet("font-size: 14pt; font-weight: 800;")
        name_row.addWidget(name)
        if getattr(store, "allow_name_change", False):
            rename = QPushButton()
            rename.setIcon(icon("edit", T.MUTED, 15))
            rename.setToolTip("Change my name")
            rename.setFixedSize(28, 28)
            rename.setCursor(Qt.PointingHandCursor)
            rename.setStyleSheet(f"QPushButton {{ background: transparent; border: none; border-radius: 14px; }}"
                                 f"QPushButton:hover {{ background: {T.SURFACE_HOVER}; }}")
            rename.clicked.connect(self.change_name)
            name_row.addWidget(rename)
        name_row.addStretch(1)
        col.addLayout(name_row)
        who = plain(QLabel(" · ".join(x for x in (f"@{me.get('username', '')}", store.designation_line(me)) if x)))
        T.polish(who, muted=True)
        col.addWidget(who)
        btns = QHBoxLayout()
        btns.setSpacing(6)
        change = QPushButton(" Change photo...")
        change.setIcon(icon("image", T.TEXT, 16))
        change.clicked.connect(self.change_photo)
        self.remove = QPushButton("Remove")
        self.remove.clicked.connect(self.remove_photo)
        self.remove.setVisible(bool(me.get("avatar")))
        btns.addWidget(change)
        btns.addWidget(self.remove)
        btns.addStretch(1)
        col.addSpacing(6)
        col.addLayout(btns)
        top.addLayout(col, 1)
        self.lay.addLayout(top)
        self.lay.addSpacing(8)

        self.lay.addWidget(heading("Status"))
        row = QHBoxLayout()
        row.setSpacing(6)
        self.emoji = QPushButton()
        self.emoji.setFixedSize(42, 38)
        self.emoji.setIconSize(QSize(20, 20))
        self.emoji.setStyleSheet("font-family: 'Segoe UI Emoji'; font-size: 14pt; padding: 0;")
        self.emoji.clicked.connect(self.pick_emoji)
        self._show_emoji(me.get("status_emoji") or "")
        row.addWidget(self.emoji)
        self.text = QLineEdit(me.get("status_msg", ""))
        self.text.setPlaceholderText("What's your status?")
        self.text.setMaxLength(120)
        self.text.setMinimumHeight(38)
        self.text.textEdited.connect(lambda _t: self._mark_preset())
        row.addWidget(self.text, 1)
        clear = IconButton("close", "Clear status", 36, 14, round_=False)
        clear.clicked.connect(self.clear_status)
        row.addWidget(clear)
        self.lay.addLayout(row)

        presets = QGridLayout()
        presets.setSpacing(6)
        self.presets = QButtonGroup(self)
        self.presets.setExclusive(False)       # none is ticked once the text is typed by hand
        self.preset_buttons = []
        for i, (emo, text, after) in enumerate(STATUS_PRESETS):
            b = QPushButton(f"{emo}  {text}")
            b.setCheckable(True)
            b.setStyleSheet("text-align: left; font-weight: 400; padding: 7px 10px;")
            b.setCursor(Qt.PointingHandCursor)
            b.setToolTip(f"{text} · {CLEARS_WHEN.get(after, '')}".rstrip(" ·"))
            b.clicked.connect(lambda _=False, e=emo, t=text, a=after: self.use_preset(e, t, a))
            self.presets.addButton(b)
            self.preset_buttons.append((b, emo, text))
            presets.addWidget(b, i // 2, i % 2)
        presets.setColumnStretch(0, 1)
        presets.setColumnStretch(1, 1)
        self.lay.addLayout(presets)
        self._mark_preset()

        form = QFormLayout()
        self.after = QComboBox()
        for key, label in CLEAR_AFTER:
            self.after.addItem(label, key)
        form.addRow("Clear after", self.after)
        if me.get("status_until"):
            until = me["status_until"]
            word = day_word(until)
            note = QLabel("Your current status clears " + (f"{word.lower()} at {fmt_time(until)}." if word
                                                            else f"on {fmt_when(until)}."))
            T.polish(note, muted=True)
            form.addRow("", note)
        self.lay.addLayout(form)
        self.lay.addWidget(_buttons(self, "Save status"))

    def change_name(self):
        from PySide6.QtWidgets import QInputDialog, QLineEdit
        current = self.ctx.store.me.get("name", "")
        name, ok = QInputDialog.getText(self, "Change my name", "The name everyone sees:", QLineEdit.Normal, current)
        name = " ".join((name or "").split())
        if not ok or not name or name == current:
            return

        def done(reply):
            if reply.get("ok"):
                self.name_label.setText(reply["name"])
                self.avatar.set(reply["name"], reply["name"], uid=self.ctx.store.my_id, ring=T.BG)
            else:
                QMessageBox.warning(self, "Change my name", reply.get("error", "Not changed"))
        self.ctx.conn.request("set_name", done, name=name)

    # ------------------------------------------------------------ photo
    def change_photo(self):
        path, _ = QFileDialog.getOpenFileName(self, "Choose a profile photo", "",
                                              "Pictures (*.png *.jpg *.jpeg *.bmp *.gif *.webp)")
        if not path:
            return
        try:
            data = avatars.prepare(path)
        except ValueError as e:
            QMessageBox.warning(self, "Profile photo", str(e))
            return

        def done(reply):
            if not reply.get("ok"):
                QMessageBox.warning(self, "Profile photo", reply.get("error", "Not saved"))
                return
            self.ctx.store.me["avatar"] = reply["avatar"]
            if avatars.cache:
                avatars.cache.put_mine(reply["avatar"], data)
            self.remove.show()
            self.avatar.update()
            self.ctx.store.me_changed.emit()
        self.ctx.conn.request("set_avatar", done, data=base64.b64encode(data).decode())

    def remove_photo(self):
        def done(reply):
            if reply.get("ok"):
                self.ctx.store.me["avatar"] = 0
                self.remove.hide()
                self.avatar.update()
                self.ctx.store.me_changed.emit()
        self.ctx.conn.request("set_avatar", done, data=None)

    # ----------------------------------------------------------- status
    def pick_emoji(self):
        from client.ui.chat_view import EmojiMenu
        m = EmojiMenu(self)
        m.picked.connect(self._set_emoji)
        m.exec(popup_pos(self.emoji, m.sizeHint(), align_right=False))

    def _show_emoji(self, e):
        """The emoji that will be saved - or, with none picked, a muted smiley outline that says so."""
        self._emoji_set = bool(e)
        if e:
            self.emoji.setIcon(QIcon())
            self.emoji.setText(e)
            self.emoji.setToolTip("Change the emoji")
        else:
            self.emoji.setText("")
            self.emoji.setIcon(icon("emoji", T.MUTED, 20))
            self.emoji.setToolTip("Pick an emoji (optional)")

    def _set_emoji(self, e):
        self._show_emoji(e)
        self._mark_preset()

    def _mark_preset(self):
        """Tick the preset the status matches now (none once it is typed by hand)."""
        text = self.text.text().strip()
        emoji = self.emoji.text() if self._emoji_set else ""
        for b, emo, t in self.preset_buttons:
            b.setChecked(t == text and emo == emoji)

    def use_preset(self, emoji, text, after):
        self._show_emoji(emoji)
        self.text.setText(text)
        self.after.setCurrentIndex(max(0, self.after.findData(after)))
        self._mark_preset()

    def clear_status(self):
        self._show_emoji("")
        self.text.clear()
        self.after.setCurrentIndex(0)
        self._mark_preset()

    def accept(self):
        text = self.text.text().strip()
        emoji = self.emoji.text() if (self._emoji_set and text) else ""
        until = clear_after_time(self.after.currentData()) if text else None
        me = self.ctx.store.me
        me.update(status_msg=text, status_emoji=emoji, status_until=until)
        self.ctx.conn.send("set_status", status=me.get("status", "online"), status_msg=text,
                           status_emoji=emoji, status_until=until)
        self.ctx.store.me_changed.emit()
        super().accept()


class ChangePasswordDialog(Dialog):
    """Change password. With `reason`, it is required (shown right after sign-in)."""

    def __init__(self, ctx, reason=""):
        super().__init__(ctx, "Choose a new password" if reason else "Change password", 400)
        self.ctx = ctx
        if reason:
            note = plain(QLabel(reason))
            note.setWordWrap(True)
            note.setStyleSheet(f"color: {T.ACCENT}; font-weight: 600;")
            self.lay.addWidget(note)
        form = QFormLayout()
        self.old = QLineEdit()
        self.new = QLineEdit()
        self.new2 = QLineEdit()
        for e in (self.old, self.new, self.new2):
            e.setEchoMode(QLineEdit.Password)
            add_show_password(e)
        if reason and ctx.conn.password:
            self.old.setText(ctx.conn.password)        # they just typed it to sign in
        form.addRow("Current password", self.old)
        form.addRow("New password", self.new)
        form.addRow("Confirm new password", self.new2)
        self.lay.addLayout(form)
        self.error = error_label()
        self.lay.addWidget(self.error)
        rules = QLabel("Pick something you'll remember but others won't guess (your studio may ask for a "
                       "minimum length).")
        rules.setWordWrap(True)
        T.polish(rules, muted=True)
        self.lay.addWidget(rules)
        self.bb = bb = _buttons(self, "Change password", False)
        if reason:
            bb.button(QDialogButtonBox.Cancel).setText("Sign out")
        self.lay.addWidget(bb)
        for e in (self.old, self.new, self.new2):
            e.textChanged.connect(self._check)
        self._check()
        (self.new if reason else self.old).setFocus()

    def _check(self):
        """Change is possible once all three are filled in and the new ones match; a mismatch shows right away
        (but not while the second one is still being typed)."""
        old, new, new2 = self.old.text(), self.new.text(), self.new2.text()
        mismatch = bool(new2) and new != new2 and not new.startswith(new2)
        set_error(self.error, "The new passwords don't match." if mismatch else "")
        self.bb.button(QDialogButtonBox.Ok).setEnabled(bool(old and new) and new == new2)

    def accept(self):
        if not self.bb.button(QDialogButtonBox.Ok).isEnabled():
            return
        if self.new.text() != self.new2.text():
            set_error(self.error, "The new passwords don't match.")
            return

        def done(reply):
            try:
                set_error(self.error, "")
            except RuntimeError:               # the dialog is gone
                return
            if reply.get("ok"):
                if self.ctx.config["remember"]:
                    self.ctx.config.set_password(self.new.text())
                    self.ctx.config.save()
                self.ctx.conn.password = self.new.text()
                super(ChangePasswordDialog, self).accept()
                if hasattr(self.ctx, "toast"):
                    self.ctx.toast("Your password was changed")
                else:
                    QMessageBox.information(self, "Change password", "Your password was changed.")
            else:
                set_error(self.error, reply.get("error", "The password was not changed."))
                self.bb.button(QDialogButtonBox.Ok).setEnabled(True)
        self.bb.button(QDialogButtonBox.Ok).setEnabled(False)       # no second request while this one runs
        self.ctx.conn.request("change_password", done, old=self.old.text(), new=self.new.text())


def section(form, title, first=False):
    """A small heading row that groups the settings below it."""
    form.addRow(heading(title, first))


def indented(widget, by=26):
    """A setting that depends on the one above it, set in under it."""
    w = QWidget()
    row = QHBoxLayout(w)
    row.setContentsMargins(by, 0, 0, 0)
    row.addWidget(widget, 1)
    return w


AWAY_CHOICES = (0, 5, 10, 15, 30, 60, 120)


def away_label(minutes):
    if not minutes:
        return "Never"
    if minutes % 60 == 0:
        return "1 hour idle" if minutes == 60 else f"{minutes // 60} hours idle"
    return f"{minutes} minutes idle"


class SettingsDialog(Dialog):
    def __init__(self, ctx):
        super().__init__(ctx, "Settings", 520)
        self.ctx = ctx
        cfg = ctx.config
        form = QFormLayout()
        form.setSpacing(10)

        # appearance
        self.theme = QComboBox()
        for key, label in T.THEMES.items():
            self.theme.addItem(label, key)
        self.theme.setCurrentIndex(max(0, self.theme.findData(cfg["theme"])))
        self.festivals = QCheckBox("Festival themes on the day: 15 August, 26 January, Christmas")
        self.festivals.setChecked(cfg["festival_themes"])
        swatches = QHBoxLayout()
        swatches.setSpacing(8)
        self.accent = cfg["accent"]
        self.swatch_buttons = {}
        for key, color in T.ACCENTS.items():
            b = QPushButton()
            b.setFixedSize(28, 28)
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.setToolTip(T.ACCENT_NAMES.get(key, key.capitalize()))
            if key == "quillo":              # the logo's two colours
                color = f"qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #13235a, stop:0.5 #13235a, stop:0.51 {color}, stop:1 {color})"
            b.setStyleSheet(f"QPushButton {{ background: {color}; border-radius: 14px; border: 2px solid {T.PANEL}; padding: 0; }}"
                            f"QPushButton:checked {{ border: 3px solid {T.TEXT}; }}")
            b.clicked.connect(lambda _=False, k=key: self._pick_accent(k))
            swatches.addWidget(b)
            self.swatch_buttons[key] = b
        swatches.addStretch(1)
        self._pick_accent(self.accent)

        row = QHBoxLayout()
        self.download_dir = QLineEdit(cfg["download_dir"])
        browse = QPushButton("Browse")
        browse.clicked.connect(self.browse)
        row.addWidget(self.download_dir, 1)
        row.addWidget(browse)
        self.notifications = QCheckBox("Show a notification for new messages")
        self.notifications.setChecked(cfg["notifications"])
        self.quick_reply = QCheckBox("Show it as a pop-up I can reply from (instead of a Windows notification)")
        self.quick_reply.setChecked(cfg["quick_reply"])
        self.quick_reply.setEnabled(cfg["notifications"])
        self.notifications.toggled.connect(self.quick_reply.setEnabled)
        self.text_scale = QComboBox()
        for value, label in ((0.9, "Small"), (1.0, "Normal"), (1.15, "Large"), (1.3, "Extra large"),
                             (1.5, "Huge")):
            self.text_scale.addItem(label, value)
        cur = float(cfg.get("text_scale") or 1)
        self.text_scale.setCurrentIndex(min(range(self.text_scale.count()),
                                            key=lambda i: abs(self.text_scale.itemData(i) - cur)))
        self.sounds = QCheckBox("Play a sound for new messages")
        self.sounds.setChecked(cfg["sounds"])
        self.close_to_tray = QCheckBox("Keep running in the tray when the window is closed")
        self.close_to_tray.setChecked(cfg["close_to_tray"])
        self.autostart = QCheckBox("Start Quillo when Windows starts")
        self.autostart.setChecked(cfg["start_with_windows"])
        self.allow_buzz = QCheckBox("Let people buzz me (shakes this window and rings, even on Do not disturb)")
        self.allow_buzz.setChecked(cfg["allow_buzz"])
        self.meeting_status = QCheckBox("Set my status to “📅 In a meeting” while one of my calendar meetings is on")
        self.meeting_status.setChecked(cfg["meeting_status"])
        self.meet_remind = QComboBox()
        for minutes in (0, 5, 10, 15, 30, 60):
            self.meet_remind.addItem("Don't remind me" if not minutes else f"{minutes} minutes before", minutes)
        self.meet_remind.setCurrentIndex(max(0, self.meet_remind.findData(int(cfg.get("meeting_reminder_min", 10)))))
        self.away = QComboBox()               # a list, like the other choices (a spin box hides its arrows)
        saved = int(cfg["auto_away_minutes"] or 0)
        for minutes in sorted(set(AWAY_CHOICES) | {saved}):
            self.away.addItem(away_label(minutes), minutes)
        self.away.setCurrentIndex(max(0, self.away.findData(saved)))
        section(form, "Appearance", first=True)
        form.addRow("Theme", self.theme)
        form.addRow("", self.festivals)
        form.addRow("Accent colour", swatches)
        form.addRow("Text size", self.text_scale)
        section(form, "Notifications")
        form.addRow("", self.notifications)
        form.addRow("", indented(self.quick_reply))
        form.addRow("", self.sounds)
        form.addRow("", self.allow_buzz)
        form.addRow("Meeting reminder", self.meet_remind)
        form.addRow("", self.meeting_status)
        section(form, "Files")
        form.addRow("Download folder", row)
        section(form, "Startup & presence")
        form.addRow("", self.close_to_tray)
        form.addRow("", self.autostart)
        form.addRow("Set me as away after", self.away)
        section(form, "Account & connection")
        # everything above the Save / Cancel row scrolls, so the dialog fits a laptop screen
        content = QWidget()
        cl = QVBoxLayout(content)
        cl.setContentsMargins(0, 0, 14, 4)           # room for the scroll bar
        cl.setSpacing(12)
        cl.addLayout(form)

        secure = (f"<span style='color:{T.ACCENT}'>&#128274; Encrypted connection</span><br>"
                  f"Server fingerprint: <span style='font-family:Consolas; font-size:8pt'>"
                  f"{esc(ctx.conn.fingerprint[:47])}<br>{esc(ctx.conn.fingerprint[48:])}</span>"
                  if ctx.conn.fingerprint else
                  f"<span style='color:{T.DANGER}'>Connection is NOT encrypted</span>")
        from common.version import APP_VERSION, LICENSE_LINE
        info = QLabel(f"Connected to <b>{esc(ctx.store.server_name)}</b> at {esc(ctx.conn.host)}:{ctx.conn.port}"
                      f" as <b>{esc(ctx.store.me.get('username', ''))}</b><br>{secure}"
                      f"<br><span style='color:{T.FAINT}'>Version {APP_VERSION}  ·  {esc(LICENSE_LINE)}</span>")
        info.setTextInteractionFlags(Qt.TextSelectableByMouse)
        T.polish(info, muted=True)
        cl.addWidget(info)
        pw = QPushButton(" Change password")
        pw.setIcon(icon("key", T.TEXT, 16))
        pw.clicked.connect(lambda: ChangePasswordDialog(ctx).exec())
        upd = QPushButton(" Check for updates")
        upd.setIcon(icon("refresh", T.TEXT, 16))
        upd.clicked.connect(lambda: ctx.check_for_updates(self))
        credits = QPushButton(" Credits")
        credits.setIcon(icon("info", T.TEXT, 16))
        from common.licence import show_credits
        credits.clicked.connect(lambda: show_credits(self))
        acct = QHBoxLayout()
        acct.addWidget(pw)
        acct.addWidget(upd)
        acct.addWidget(credits)
        acct.addStretch(1)
        cl.addLayout(acct)
        cl.addStretch(1)
        self.content = content
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setWidget(content)
        content.setAutoFillBackground(False)
        self.scroll.viewport().setAutoFillBackground(False)
        self.lay.setContentsMargins(22, 20, 8, 18)   # the scroll bar sits in the right margin
        self.lay.addWidget(self.scroll, 1)
        bb = _buttons(self)
        bb.setContentsMargins(0, 0, 14 + self.scroll.verticalScrollBar().sizeHint().width(), 0)
        self.lay.addWidget(bb)
        self._fit_screen()

    def _fit_screen(self):
        """As tall as the settings need, but never taller than 85% of the screen: the rest scrolls."""
        hint = self.content.sizeHint()
        self.scroll.setMinimumWidth(hint.width() + self.scroll.verticalScrollBar().sizeHint().width())
        screen = self.screen() or QGuiApplication.primaryScreen()
        avail = screen.availableGeometry().height() if screen else 800
        m = self.lay.contentsMargins()
        want = hint.height() + m.top() + m.bottom() + self.lay.spacing() + 44
        self.resize(self.sizeHint().width(), min(want, int(avail * 0.85)))

    def _pick_accent(self, key):
        self.accent = key
        for k, b in self.swatch_buttons.items():
            b.setChecked(k == key)

    def browse(self):
        d = QFileDialog.getExistingDirectory(self, "Download folder", self.download_dir.text())
        if d:
            self.download_dir.setText(d)

    def accept(self):
        cfg = self.ctx.config
        cfg["download_dir"] = self.download_dir.text().strip() or cfg["download_dir"]
        cfg["notifications"] = self.notifications.isChecked()
        cfg["quick_reply"] = self.quick_reply.isChecked()
        cfg["sounds"] = self.sounds.isChecked()
        cfg["close_to_tray"] = self.close_to_tray.isChecked()
        cfg["auto_away_minutes"] = int(self.away.currentData() or 0)
        cfg["allow_buzz"] = self.allow_buzz.isChecked()
        cfg["meeting_status"] = self.meeting_status.isChecked()
        cfg["meeting_reminder_min"] = self.meet_remind.currentData()
        if cfg["start_with_windows"] != self.autostart.isChecked():
            cfg["start_with_windows"] = self.autostart.isChecked()
            from client.config import set_autostart
            try:
                set_autostart(cfg["start_with_windows"])
            except OSError as e:
                QMessageBox.warning(self, "Settings", f"Could not change Windows startup: {e}")
        look_changed = ((cfg["theme"], cfg["accent"], cfg["festival_themes"], float(cfg["text_scale"] or 1))
                        != (self.theme.currentData(), self.accent, self.festivals.isChecked(),
                            self.text_scale.currentData()))
        cfg["text_scale"] = self.text_scale.currentData()
        cfg["theme"], cfg["accent"] = self.theme.currentData(), self.accent
        cfg["festival_themes"] = self.festivals.isChecked()
        cfg.save()
        super().accept()
        if look_changed and QMessageBox.question(
                self.ctx, "New look", "Restart Quillo now to apply the new look?") == QMessageBox.Yes:
            self.ctx.restart()


ANNOUNCE_RANK = {"none": 0, "team": 1, "section": 2, "department": 3, "all": 4}


def announce_targets(store):
    """(label, (kind, department, section)) choices allowed by my designation."""
    me = store.me
    rank = ANNOUNCE_RANK.get(store.perm("announce") or "none", 0)
    my_dept, my_sect = me.get("department", ""), me.get("section", "")
    out = []
    if rank >= 4:
        out.append(("Everyone in the studio", ("all", "", "")))
        for d in store.departments():
            out.append((f"Department: {d}", ("department", d, "")))
            for s in store.sections(d):
                out.append((f"      Section: {d} · {s}", ("section", d, s)))
    elif rank == 3 and my_dept:
        out.append((f"My department: {my_dept}", ("department", my_dept, "")))
        for s in store.sections(my_dept):
            out.append((f"      Section: {my_dept} · {s}", ("section", my_dept, s)))
    elif rank == 2 and my_dept and my_sect:
        out.append((f"My section: {my_dept} · {my_sect}", ("section", my_dept, my_sect)))
    if rank >= 1 and store.direct_reports():
        out.append(("My team (everyone reporting to me)", ("team", "", "")))
    return out


class ComposeAnnouncementDialog(Dialog):
    def __init__(self, ctx):
        super().__init__(ctx, "New announcement", 520)
        self.ctx = ctx
        form = QFormLayout()
        self.title = QLineEdit()
        self.title.setPlaceholderText("e.g. Dailies moved to 4 PM")
        self.dept = QComboBox()
        for label, target in announce_targets(ctx.store):
            self.dept.addItem(label, target)
        form.addRow("Title", self.title)
        form.addRow("Send to", self.dept)
        form.addRow("", hint("Everyone you send it to gets a pop-up that stays on top until they click "
                             "Got it. It can't be taken back, so check it first."))
        self.lay.addLayout(form)
        self.body = QPlainTextEdit()
        self.body.setMinimumHeight(180)
        self.body.setPlaceholderText("Write the announcement…")
        self.lay.addWidget(self.body, 1)
        self.error = error_label()
        self.lay.addWidget(self.error)
        self.bb = _buttons(self, "Send", False)
        self.body.textChanged.connect(
            lambda: self.bb.button(QDialogButtonBox.Ok).setEnabled(bool(self.body.toPlainText().strip())))
        self.lay.addWidget(self.bb)
        self.title.setFocus()

    def accept(self):
        if not self.body.toPlainText().strip():
            return

        def done(reply):
            try:
                self.bb.button(QDialogButtonBox.Ok).setEnabled(True)
            except RuntimeError:
                return
            if reply.get("ok"):
                super(ComposeAnnouncementDialog, self).accept()
                if hasattr(self.ctx, "toast"):
                    self.ctx.toast("Announcement sent")
            else:
                set_error(self.error, reply.get("error", "Not sent. Try again."))
        self.bb.button(QDialogButtonBox.Ok).setEnabled(False)       # one click sends it once
        kind, dept, sect = self.dept.currentData()
        self.ctx.conn.request("announce", done, title=self.title.text(), body=self.body.toPlainText(),
                              target=kind, department=dept, section=sect)


class AnnouncementPopup(QDialog):
    """Stays on top until acknowledged, like Output Messenger's announcements."""
    acknowledged = Signal(int)

    def __init__(self, ctx, ann):
        super().__init__(None, Qt.Window | Qt.WindowStaysOnTopHint)
        self.ann = ann
        self.setWindowTitle("Announcement")
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.ctx = ctx
        self.setMinimumWidth(460)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 22, 24, 18)
        lay.setSpacing(10)
        top = QHBoxLayout()
        ic = QLabel()
        ic.setPixmap(icon("megaphone", T.ACCENT, 34).pixmap(34, 34))
        top.addWidget(ic)
        col = QVBoxLayout()
        title = plain(QLabel(ann["title"]))
        title.setWordWrap(True)
        title.setStyleSheet("font-size: 14pt; font-weight: 700;")
        sender = ctx.store.user_name(ann["sender_id"])
        when = fmt_when(ann["ts"])
        target = ann.get("target_label") or ann.get("department") or ""
        to = f" to {target}" if target and target != "Everyone" else ""
        meta = QLabel(f"From <b>{esc(sender)}</b>{esc(to)}{SEP}{esc(when)}")
        T.polish(meta, muted=True)
        col.addWidget(title)
        col.addWidget(meta)
        top.addLayout(col, 1)
        lay.addLayout(top)
        body = QLabel(linkify(ann["body"]))
        body.setWordWrap(True)
        body.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.LinksAccessibleByMouse)
        body.linkActivated.connect(open_link)
        body.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        body.setStyleSheet(f"font-size: {T.pt(T.FONT_BODY)};")
        box = QFrame()                       # the message sits in a panel as tall as its text (long ones scroll)
        box.setObjectName("annbody")
        box.setStyleSheet(f"#annbody {{ background: {T.PANEL}; border-radius: 12px; }}")
        bl = QVBoxLayout(box)
        bl.setContentsMargins(14, 12, 14, 12)
        body.ensurePolished()
        if body.heightForWidth(460 - 48 - 28) > 360:
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QFrame.NoFrame)
            scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            scroll.setWidget(body)
            scroll.viewport().setAutoFillBackground(False)
            scroll.setFixedHeight(360)
            bl.addWidget(scroll)
        else:
            bl.addWidget(body)
        lay.addWidget(box)
        lay.addStretch(0)
        buttons = QHBoxLayout()
        if hasattr(ctx, "rail_clicked") and "announcements" in getattr(ctx, "rail", {}):
            news = QPushButton("Open News")
            T.polish(news, flat=True)
            news.setToolTip("See every announcement on the News page")
            news.clicked.connect(self._open_news)
            buttons.addWidget(news)
        buttons.addStretch(1)
        ok = QPushButton("Got it")
        T.polish(ok, primary=True)
        ok.setDefault(True)
        ok.clicked.connect(self.close)
        buttons.addWidget(ok)
        lay.addLayout(buttons)
        self.adjustSize()

    def _open_news(self):
        ctx = self.ctx
        try:
            if hasattr(ctx, "show_normal"):
                ctx.show_normal()
            else:
                ctx.show()
            ctx.rail["announcements"].setChecked(True)
            ctx.rail_clicked("announcements")
            ctx.raise_()
            ctx.activateWindow()
        finally:
            self.close()

    def showEvent(self, e):
        super().showEvent(e)
        T.dark_title_bar(self)

    def closeEvent(self, e):
        self.acknowledged.emit(self.ann["id"])
        super().closeEvent(e)


class ForwardDialog(Dialog):
    """Pick a person or room to forward a message to."""

    def __init__(self, ctx, msg):
        super().__init__(ctx, "Forward message", 420)
        self.setMinimumHeight(460)
        store = ctx.store
        preview = plain(QLabel(clip(stickers.summary(msg), 160)))
        preview.setWordWrap(True)
        preview.setStyleSheet(f"background: {T.PANEL}; border-left: 3px solid {T.ACCENT}; border-radius: 6px;"
                              f" padding: 8px; color: {T.MUTED};")
        self.lay.addWidget(preview)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search people and rooms…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._filter)
        self.lay.addWidget(self.search)
        self.list = QListWidget()
        self.list.setIconSize(QSize(24, 24))
        recent = sorted((c for c in store.convs.values() if c.last and store.conv_exists(c.conv)),
                        key=lambda c: c.last_ts, reverse=True)
        seen = set()
        targets = [c.conv for c in recent]
        targets += [P.room_conv(r) for r in sorted(store.rooms, key=lambda r: store.rooms[r]["name"].lower())]
        targets += [P.direct_conv(u) for u in sorted(store.users, key=lambda u: store.users[u]["name"].lower())
                    if not store.is_builtin(store.users[u])]
        for conv in targets:
            if conv in seen or conv == msg["conv"]:
                continue
            seen.add(conv)
            kind, target = P.parse_conv(conv)
            if kind == "u":
                status = store.me.get("status", "online") if target == store.my_id else \
                    store.users.get(target, {}).get("status", "offline")
                ic = person_icon(store, target, status, 24)
            else:
                ic = icon("hash", T.MUTED, 18)
            it = QListWidgetItem(ic, store.title(conv))
            it.setData(Qt.UserRole, conv)
            self.list.addItem(it)
        self.list.itemDoubleClicked.connect(lambda _: self.target() and self.accept())
        self.list.currentItemChanged.connect(lambda *_: self._update_ok())
        self.lay.addWidget(self.list, 1)
        self.none = hint("No person or room matches that.")
        self.none.hide()
        self.lay.addWidget(self.none)
        self.bb = _buttons(self, "Forward", False)
        self.lay.addWidget(self.bb)
        self.search.setFocus()

    def _filter(self, q):
        q = q.lower().strip()
        first = None
        for i in range(self.list.count()):
            it = self.list.item(i)
            it.setHidden(bool(q) and q not in it.text().lower())
            if first is None and not it.isHidden():
                first = it
        cur = self.list.currentItem()
        if q and (cur is None or cur.isHidden()):           # typing a name picks the best match: Enter sends
            if first is not None:
                self.list.setCurrentItem(first)
            else:
                self.list.setCurrentRow(-1)
                self.list.clearSelection()
        self.none.setVisible(first is None)
        self._update_ok()

    def _update_ok(self):
        name = self.list.currentItem().text() if self.target() else ""
        ok = self.bb.button(QDialogButtonBox.Ok)
        ok.setEnabled(bool(name))
        ok.setText(menu_text(f"Forward to {clip(name, 24)}") if name else "Forward")

    def target(self):
        """The chosen chat - never one the search box has hidden."""
        it = self.list.currentItem()
        return it.data(Qt.UserRole) if it is not None and not it.isHidden() else None

    def accept(self):
        if self.target():
            super().accept()


class SavedDialog(Dialog):
    """Messages saved for later: double-click one to go to it."""

    def __init__(self, ctx):
        super().__init__(ctx, "Saved for later", 560)
        self.ctx = ctx
        self.setMinimumHeight(480)
        self.list = rich_list()
        self.list.itemActivated.connect(self._open)
        self.list.itemDoubleClicked.connect(self._open)
        self.list.currentItemChanged.connect(lambda *_: self._update_remove())
        self.lay.addWidget(self.list, 1)
        self.help = hint("Double-click a message to go to it.")
        self.lay.addWidget(self.help)
        row = QHBoxLayout()
        self.remove = QPushButton(" Remove from saved")
        danger(self.remove)
        self.remove.setIcon(icon("trash", T.DANGER, 15))
        self.remove.clicked.connect(self._remove)
        row.addWidget(self.remove)
        row.addStretch(1)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        row.addWidget(close)
        self.lay.addLayout(row)
        ctx.store.prefs_changed.connect(lambda k: k in ("", "saved") and self._fill())
        self._fill()

    def _fill(self):
        try:
            self.list.clear()
        except RuntimeError:
            return
        store = self.ctx.store
        items = store.saved()
        for x in items:
            text, rich = message_row(store, x, x.get("snippet", ""))
            it = QListWidgetItem(icon("bookmark", T.ACCENT, 16), text)
            it.setData(RICH_ROLE, rich)
            it.setData(Qt.UserRole, x)
            self.list.addItem(it)
        if not items:
            it = QListWidgetItem("Nothing saved yet.\nRight-click a message → Save for later.")
            it.setData(RICH_ROLE, f"<b>Nothing saved yet</b><br>{muted('Right-click a message → Save for later.')}")
            it.setFlags(Qt.NoItemFlags)
            self.list.addItem(it)
        self.help.setVisible(bool(items))
        self._update_remove()

    def _update_remove(self):
        try:
            it = self.list.currentItem()
            self.remove.setEnabled(bool(it is not None and it.data(Qt.UserRole)))
        except RuntimeError:
            pass

    def _open(self, it):
        x = it.data(Qt.UserRole)
        if not x or not self.ctx.store.conv_exists(x["conv"]):
            return
        self.ctx.open_conv(x["conv"])
        QTimer.singleShot(300, lambda: self.ctx.chat.scroll_to(x["id"]))
        self.accept()

    def _remove(self):
        it = self.list.currentItem()
        x = it.data(Qt.UserRole) if it else None
        if x:
            self.ctx.store.set_saved(x, False)


class ReadReceiptsDialog(Dialog):
    """Who has / hasn't read an announcement - or seen a message (verb="Seen", window_title="Seen by"):
    two groups, the ones who read it and the ones still to."""

    def __init__(self, parent, title, reads, window_title="Read by", verb="Read"):
        super().__init__(parent, window_title, 420)
        head = plain(QLabel(clip(title, 200)))
        head.setWordWrap(True)
        head.setStyleSheet(f"font-weight: 700; font-size: {T.pt(T.FONT_L)};")
        self.lay.addWidget(head)
        read, unread = reads.get("read") or [], reads.get("unread") or []
        n_read, n_all = len(read), len(read) + len(unread)
        count = plain(QLabel(f"{verb} by {n_read} of {n_all}" if n_all else "Nobody to read it yet"))
        T.polish(count, muted=True)
        self.lay.addWidget(count)
        self.list = lst = QListWidget()
        lst.setSelectionMode(QAbstractItemView.NoSelection)
        lst.setFocusPolicy(Qt.NoFocus)
        groups = ((verb, read, "check", T.ACCENT), (f"Not {verb.lower()} yet", unread, "close", T.MUTED))
        for label, people, ic, color in groups:
            if not people:
                continue
            h = QListWidgetItem(f"{label.upper()}{SEP}{len(people)}")
            h.setFlags(Qt.NoItemFlags)
            f = h.font()
            f.setBold(True)
            f.setPointSizeF(T.FONT_XS)
            h.setFont(f)
            h.setForeground(QColor(T.MUTED))
            lst.addItem(h)
            for person in people:
                lst.addItem(QListWidgetItem(icon(ic, color, 14), person["name"]))
        lst.setMinimumHeight(300)
        self.lay.addWidget(lst, 1)
        close = QPushButton("Close")
        T.polish(close, primary=True)
        close.setDefault(True)
        close.clicked.connect(self.accept)
        self.lay.addWidget(close, 0, Qt.AlignRight)


class SearchDialog(Dialog):
    """Search what you can see, by text or file name - narrowed to a person, a chat, some days, or files."""
    open_conv = Signal(str)

    def __init__(self, ctx, query=""):
        from PySide6.QtCore import QDate
        from PySide6.QtWidgets import QCheckBox, QComboBox, QDateEdit
        super().__init__(ctx, f"Everything about {query}" if query else "Search messages", 640)
        self.ctx = ctx
        self.setMinimumHeight(520)
        self.results = []
        row = QHBoxLayout()
        self.query = QLineEdit()
        self.query.setPlaceholderText("Search text or file names in all your chats…")
        self.query.setClearButtonEnabled(True)
        self.query.returnPressed.connect(self.search)
        go = QPushButton("Search")
        T.polish(go, primary=True)
        go.clicked.connect(self.search)
        row.addWidget(self.query, 1)
        row.addWidget(go)
        self.lay.addLayout(row)

        store = ctx.store
        filters = QHBoxLayout()
        filters.setSpacing(8)
        self.who = QComboBox()
        self.who.addItem("From anyone", None)
        self.who.addItem("From me", store.my_id)
        for uid, u in sorted(store.users.items(), key=lambda kv: (kv[1].get("name") or "").lower()):
            if store.is_builtin(u):
                continue
            self.who.addItem(f"From {u.get('name') or u.get('username')}", uid)
        self.where = QComboBox()
        self.where.addItem("In all chats", None)
        chats = [c for c in store.convs if store.conv_exists(c)]
        for conv in sorted(chats, key=lambda c: store.title(c).lower()):
            self.where.addItem(("# " if conv.startswith("r:") else "") + store.title(conv), conv)
        if ctx.chat.conv and self.where.findData(ctx.chat.conv) < 0 and store.conv_exists(ctx.chat.conv):
            self.where.addItem(store.title(ctx.chat.conv), ctx.chat.conv)
        for combo in (self.who, self.where):
            combo.setMaxVisibleItems(18)
            filters.addWidget(combo, 1)
        self.files = QCheckBox("Files only")
        filters.addWidget(self.files)
        self.lay.addLayout(filters)

        days = QHBoxLayout()
        days.setSpacing(8)
        self.use_dates = QCheckBox("Between")
        days.addWidget(self.use_dates)
        today = QDate.currentDate()
        self.since = QDateEdit(today.addMonths(-1))
        self.until = QDateEdit(today)
        for d in (self.since, self.until):
            d.setCalendarPopup(True)
            d.setDisplayFormat("d MMM yyyy")
            d.setMinimumWidth(150)
            d.setEnabled(False)
        self.use_dates.toggled.connect(self.since.setEnabled)
        self.use_dates.toggled.connect(self.until.setEnabled)
        days.addWidget(self.since)
        days.addWidget(plain(QLabel("and")))
        days.addWidget(self.until)
        days.addStretch(1)
        self.lay.addLayout(days)

        self.status = plain(QLabel())
        T.polish(self.status, muted=True)
        self.lay.addWidget(self.status)
        self.list = rich_list()
        self.list.itemActivated.connect(self._open)
        self.list.itemDoubleClicked.connect(self._open)
        self.lay.addWidget(self.list, 1)
        self._request = 0                      # only the newest search fills the list
        self.more = QPushButton("Show older results")
        self.more.clicked.connect(lambda: self.search(more=True))
        self.more.hide()
        self.lay.addWidget(self.more)
        self.shot = None
        from client.ui.widgets import shot_names
        if query and shot_names(query) == [query]:
            self._shot_header(query)
        if query:
            self.query.setText(query)
            QTimer.singleShot(0, self.search)

    def _shot_header(self, shot):
        """Searching a shot: its status on top, and a button to change it."""
        self.shot = shot
        box = QFrame()
        box.setObjectName("shotbox")
        box.setStyleSheet(f"#shotbox {{ background: {T.PANEL}; border-radius: 14px; }}")
        row = QHBoxLayout(box)
        row.setContentsMargins(14, 8, 8, 8)
        self.shot_label = QLabel()
        self.shot_label.setTextFormat(Qt.RichText)
        row.addWidget(self.shot_label, 1)
        change = QPushButton("Set status")
        change.clicked.connect(lambda: self._status_menu(change))
        row.addWidget(change)
        self.lay.insertWidget(0, box)
        self._show_shot()
        self.ctx.store.shots_changed.connect(lambda s: s in ("", shot.upper()) and self._show_shot())

    def _show_shot(self):
        st = self.ctx.store.shot_status(self.shot)
        if st and st[0] in P.SHOT_STATUS:
            emoji, label = P.SHOT_STATUS[st[0]]
            who = self.ctx.store.user_name(st[1]) if st[1] else ""
            when = fmt_when(st[2]) if st[2] else ""
            by = SEP.join(x for x in (f"set by {who}" if who else "", when) if x)
            text = (f"<b style='font-size:12pt'>{esc(self.shot)}</b> &nbsp; {emoji} <b>{label}</b>"
                    + (f"<span style='color:{T.MUTED}'>{SEP}{esc(by)}</span>" if by else ""))
        else:
            text = (f"<b style='font-size:12pt'>{esc(self.shot)}</b>"
                    f"<span style='color:{T.MUTED}'>{SEP}no status yet</span>")
        try:
            self.shot_label.setText(text)
        except RuntimeError:                   # the dialog is gone
            pass

    def _status_menu(self, button):
        m = QMenu(self)
        current = (self.ctx.store.shot_status(self.shot) or (None,))[0]
        for key, emoji, label in P.SHOT_STATUSES:
            a = m.addAction(f"{emoji}  {label}", lambda k=key: self.ctx.set_shot_status(self.shot, k))
            a.setCheckable(True)
            a.setChecked(key == current)
        m.exec(popup_pos(button, m.sizeHint()))       # right edges lined up: it stays inside the dialog

    def _filters(self):
        f = {}
        if self.who.currentData():
            f["from"] = self.who.currentData()
        if self.where.currentData():
            f["conv"] = self.where.currentData()
        if self.files.isChecked():
            f["files"] = True
        if self.use_dates.isChecked():
            first, last = sorted((self.since.date(), self.until.date()))
            f["since"], f["until"] = first.toString("yyyy-MM-dd"), last.toString("yyyy-MM-dd")
        return f

    def search(self, more=False):
        q = self.query.text().strip()
        filters = self._filters()
        if len(q) < 2 and not filters:
            self.status.setText("Type at least 2 characters, or choose a filter.")
            return
        if more and self.results:
            filters["before"] = self.results[-1]["id"]
        else:
            self.results = []
            self.list.clear()
        self.status.setText("Searching…")
        self._request += 1
        request = self._request

        def done(reply):
            try:
                if request != self._request:       # a newer search was started meanwhile
                    return
            except RuntimeError:                   # the dialog is gone
                return
            if not reply.get("ok"):
                self.status.setText(reply.get("error", "Search failed"))
                return
            msgs = reply["messages"]
            self.results += msgs
            self.status.setText(self.count_text(len(self.results), q, bool(reply.get("more"))))
            self.more.setVisible(bool(reply.get("more")))
            store = self.ctx.store
            for m in msgs:
                text, rich = message_row(store, m, stickers.summary(m), q,
                                         "in a thread" if m.get("thread_root") else "")
                it = QListWidgetItem(text)
                it.setData(RICH_ROLE, rich)
                it.setData(Qt.UserRole, m)
                self.list.addItem(it)
        self.ctx.conn.request("search", done, query=q, **filters)

    @staticmethod
    def count_text(n, q="", more=False):
        """'1 result', '27 results so far', or what to try when nothing matches."""
        if not n:
            what = f"No messages match “{q}”." if q else "No messages match these filters."
            return what + " Try fewer words or clear the filters."
        return f"{n} result{'s' if n != 1 else ''}" + (" so far" if more else "")

    def _open(self, it):
        m = it.data(Qt.UserRole)
        conv = m["conv"]
        if not self.ctx.store.conv_exists(conv):
            return
        if m.get("thread_root"):
            self.ctx.open_thread(conv, m["thread_root"])
        else:
            self.ctx.open_conv(conv)
            QTimer.singleShot(300, lambda: self.ctx.chat.scroll_to(m["id"]))
        self.accept()
