"""Dialogs of the client."""

import base64
import datetime
import os
import time

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QGridLayout, QHBoxLayout, QLabel,
    QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPlainTextEdit, QPushButton, QSpinBox,
    QVBoxLayout, QWidget,
)

from common import protocol as P
from common import theme as T
from common.icons import icon
from client import stickers
from client import avatars
from client.ui.widgets import Avatar, IconButton, esc, fmt_list_time, linkify, open_link, plain


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


class MemberPicker(QWidget):
    """Search box + checkable list of users."""

    def __init__(self, store, checked=(), exclude=()):
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search people...")
        self.search.textChanged.connect(self._filter)
        lay.addWidget(self.search)
        self.list = QListWidget()
        self.list.setMinimumHeight(260)
        users = sorted(store.users.values(), key=lambda u: (u["department"].lower(), u["name"].lower()))
        for u in users:
            if u["id"] in exclude or u.get("username") == "admin":     # the built-in console account
                continue
            line = store.designation_line(u)
            it = QListWidgetItem(f"{u['name']}" + (f"  ·  {line}" if line else ""))
            it.setData(Qt.UserRole, u["id"])
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Checked if u["id"] in checked else Qt.Unchecked)
            self.list.addItem(it)
        self.list.itemClicked.connect(
            lambda it: it.setCheckState(Qt.Unchecked if it.checkState() == Qt.Checked else Qt.Checked))
        lay.addWidget(self.list, 1)

    def _filter(self, q):
        q = q.lower()
        for i in range(self.list.count()):
            it = self.list.item(i)
            it.setHidden(bool(q) and q not in it.text().lower())

    def selected(self):
        return [self.list.item(i).data(Qt.UserRole) for i in range(self.list.count())
                if self.list.item(i).checkState() == Qt.Checked]


class NewRoomDialog(Dialog):
    def __init__(self, parent, store, preselect=()):
        super().__init__(parent, "New chat room", 460)
        form = QFormLayout()
        self.name = QLineEdit()
        self.name.setPlaceholderText("e.g. Comp - Project X")
        self.topic = QLineEdit()
        self.topic.setPlaceholderText("optional")
        form.addRow("Room name", self.name)
        form.addRow("Topic", self.topic)
        self.lay.addLayout(form)
        self.lay.addWidget(QLabel("Add people"))
        self.picker = MemberPicker(store, checked=preselect)
        self.lay.addWidget(self.picker, 1)
        bb = _buttons(self, "Create room", False)
        self.name.textChanged.connect(lambda t: bb.button(QDialogButtonBox.Ok).setEnabled(bool(t.strip())))
        self.lay.addWidget(bb)


class AddMembersDialog(Dialog):
    def __init__(self, parent, store, exclude):
        super().__init__(parent, "Add people", 420)
        self.picker = MemberPicker(store, exclude=exclude)
        self.lay.addWidget(self.picker, 1)
        self.lay.addWidget(_buttons(self, "Add"))


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
        self.name.setEnabled(bool(self.can_manage))
        self.topic.setEnabled(bool(self.can_manage))
        form.addRow("Room name", self.name)
        form.addRow("Topic", self.topic)
        self.lay.addLayout(form)
        if self.can_manage:
            save = QPushButton("Save name & topic")
            save.clicked.connect(self.save)
            self.lay.addWidget(save, 0, Qt.AlignRight)

        if self.auto:
            note = QLabel("This room is managed automatically: everyone in the department / section is a "
                          "member, and people join or leave it when the admin changes their department.")
            note.setWordWrap(True)
            T.polish(note, muted=True)
            self.lay.addWidget(note)
        head = QHBoxLayout()
        head.addWidget(QLabel(f"<b>{len(room['members'])} members</b>"))
        head.addStretch(1)
        if not self.auto:
            add = QPushButton(" Add people")
            add.setIcon(icon("plus", T.ACCENT_TEXT, 16))
            T.polish(add, primary=True)
            add.clicked.connect(self.add_people)
            head.addWidget(add)
        self.lay.addLayout(head)

        self.list = QListWidget()
        self.list.setMinimumHeight(260)
        members = sorted(room["members"], key=lambda u: store.user_name(u).lower())
        for uid in members:
            u = store.users.get(uid, {})
            status = "online" if uid == me else u.get("status", "offline")
            owner = "  (owner)" if uid == room["owner_id"] else ""
            you = "  (you)" if uid == me else ""
            designation = (store.me if uid == me else u).get("designation") or ""
            it = QListWidgetItem(icon("user", T.STATUS_COLORS.get(status, T.MUTED), 16),
                                 f"{store.user_name(uid)}{you}{owner}" + (f"   ·  {designation}" if designation else ""))
            it.setData(Qt.UserRole, uid)
            it.setToolTip(T.STATUS_LABELS.get(status, status))
            self.list.addItem(it)
        self.list.itemDoubleClicked.connect(self.open_chat)
        self.lay.addWidget(self.list, 1)

        row = QHBoxLayout()
        if self.can_manage:
            rm = QPushButton("Remove selected")
            T.polish(rm, danger=True)
            rm.clicked.connect(self.remove)
            row.addWidget(rm)
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
        self._update(name=self.name.text(), topic=self.topic.text())
        self.accept()

    def add_people(self):
        dlg = AddMembersDialog(self, self.ctx.store, exclude=set(self.room["members"]))
        if dlg.exec() and dlg.picker.selected():
            self._update(add=dlg.picker.selected())
            self.accept()

    def remove(self):
        items = self.list.selectedItems()
        uids = [it.data(Qt.UserRole) for it in items if it.data(Qt.UserRole) != self.ctx.store.my_id]
        if uids:
            self._update(remove=uids)
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
        self.lay.addWidget(QLabel("<b>Question</b>"))
        self.lay.addWidget(self.question)
        self.lay.addWidget(QLabel("<b>Answers</b>"))
        self.answers_box = QVBoxLayout()
        self.answers_box.setSpacing(6)
        self.lay.addLayout(self.answers_box)
        self.answers = []
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
        self.bb = _buttons(self, "Create poll", False)
        self.lay.addWidget(self.bb)
        self.question.textChanged.connect(self._check)

    def add_answer(self, focus=False):
        if len(self.answers) >= 10:
            return
        e = QLineEdit()
        e.setPlaceholderText(f"Answer {len(self.answers) + 1}")
        e.setMaxLength(100)
        e.textChanged.connect(self._check)
        self.answers_box.addWidget(e)
        self.answers.append(e)
        if hasattr(self, "b_add"):
            self.b_add.setEnabled(len(self.answers) < 10)
        if focus:
            e.setFocus()

    def _options(self):
        return [e.text().strip() for e in self.answers if e.text().strip()]

    def _check(self):
        ok = bool(self.question.text().strip()) and len({o.lower() for o in self._options()}) >= 2
        self.bb.button(QDialogButtonBox.Ok).setEnabled(ok)

    def values(self):
        return {"question": self.question.text().strip(), "options": self._options(),
                "multi": self.multi.isChecked(), "anonymous": self.anonymous.isChecked()}


STATUS_PRESETS = [("🍽️", "Out for lunch", "1h"), ("☕", "Tea break", "30m"), ("📅", "In a meeting", "1h"),
                  ("🎬", "Rendering — don't touch my PC", "4h"), ("🎧", "Focus mode", "4h"),
                  ("🏠", "Working from home", "today"), ("🤒", "Out sick", "today"), ("🌴", "On leave", "week")]
CLEAR_AFTER = [("never", "Don't clear"), ("30m", "30 minutes"), ("1h", "1 hour"), ("4h", "4 hours"),
               ("today", "Today"), ("week", "This week")]


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
        name = plain(QLabel(me.get("name", "")))
        name.setStyleSheet("font-size: 14pt; font-weight: 800;")
        col.addWidget(name)
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

        head = QLabel("STATUS")
        head.setStyleSheet(f"color: {T.MUTED}; font-size: 8pt; font-weight: 700;")
        self.lay.addWidget(head)
        row = QHBoxLayout()
        row.setSpacing(6)
        self.emoji = QPushButton(me.get("status_emoji") or "🙂")
        self.emoji.setFixedSize(42, 38)
        self.emoji.setStyleSheet("font-family: 'Segoe UI Emoji'; font-size: 14pt; padding: 0;")
        self.emoji.setToolTip("Pick an emoji")
        self.emoji.clicked.connect(self.pick_emoji)
        self._emoji_set = bool(me.get("status_emoji"))
        row.addWidget(self.emoji)
        self.text = QLineEdit(me.get("status_msg", ""))
        self.text.setPlaceholderText("What's your status?")
        self.text.setMaxLength(120)
        self.text.setMinimumHeight(38)
        row.addWidget(self.text, 1)
        clear = IconButton("close", "Clear status", 36, 14, round_=False)
        clear.clicked.connect(self.clear_status)
        row.addWidget(clear)
        self.lay.addLayout(row)

        presets = QGridLayout()
        presets.setSpacing(6)
        for i, (emo, text, after) in enumerate(STATUS_PRESETS):
            b = QPushButton(f"{emo}  {text}")
            b.setStyleSheet("text-align: left; font-weight: 400; padding: 7px 10px;")
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _=False, e=emo, t=text, a=after: self.use_preset(e, t, a))
            presets.addWidget(b, i // 2, i % 2)
        self.lay.addLayout(presets)

        form = QFormLayout()
        self.after = QComboBox()
        for key, label in CLEAR_AFTER:
            self.after.addItem(label, key)
        form.addRow("Clear after", self.after)
        if me.get("status_until"):
            until = datetime.datetime.fromtimestamp(me["status_until"])
            note = QLabel(f"Your current status clears on {until:%a %d %b at %H:%M}.")
            T.polish(note, muted=True)
            form.addRow("", note)
        self.lay.addLayout(form)
        self.lay.addWidget(_buttons(self, "Save status"))

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
        m.exec(self.emoji.mapToGlobal(self.emoji.rect().bottomLeft()))

    def _set_emoji(self, e):
        self.emoji.setText(e)
        self._emoji_set = True

    def use_preset(self, emoji, text, after):
        self._set_emoji(emoji)
        self.text.setText(text)
        self.after.setCurrentIndex(max(0, self.after.findData(after)))

    def clear_status(self):
        self.emoji.setText("🙂")
        self._emoji_set = False
        self.text.clear()
        self.after.setCurrentIndex(0)

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
        if reason and ctx.conn.password:
            self.old.setText(ctx.conn.password)        # they just typed it to sign in
        form.addRow("Current password", self.old)
        form.addRow("New password", self.new)
        form.addRow("Repeat new password", self.new2)
        self.lay.addLayout(form)
        rules = QLabel("Pick something you'll remember but others won't guess (your studio may ask for a "
                       "minimum length).")
        rules.setWordWrap(True)
        T.polish(rules, muted=True)
        self.lay.addWidget(rules)
        bb = _buttons(self, "Change")
        if reason:
            bb.button(QDialogButtonBox.Cancel).setText("Sign out")
        self.lay.addWidget(bb)
        (self.new if reason else self.old).setFocus()

    def accept(self):
        if self.new.text() != self.new2.text():
            QMessageBox.warning(self, "Change password", "The new passwords do not match.")
            return

        def done(reply):
            if reply.get("ok"):
                if self.ctx.config["remember"]:
                    self.ctx.config.set_password(self.new.text())
                    self.ctx.config.save()
                self.ctx.conn.password = self.new.text()
                QMessageBox.information(self, "Change password", "Your password was changed.")
                super(ChangePasswordDialog, self).accept()
            else:
                QMessageBox.warning(self, "Change password", reply.get("error", "Failed"))
        self.ctx.conn.request("change_password", done, old=self.old.text(), new=self.new.text())


def section(form, title, first=False):
    """A small heading row that groups the settings below it."""
    head = QLabel(title.upper())
    head.setStyleSheet(f"color: {T.MUTED}; font-size: 8pt; font-weight: 800; letter-spacing: 1px;"
                       f" padding-top: {0 if first else 10}px;")
    form.addRow(head)


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
            b.setToolTip(key.capitalize())
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
        self.sounds = QCheckBox("Play a sound for new messages")
        self.sounds.setChecked(cfg["sounds"])
        self.close_to_tray = QCheckBox("Keep running in the tray when the window is closed")
        self.close_to_tray.setChecked(cfg["close_to_tray"])
        self.autostart = QCheckBox("Start LAN Messenger when Windows starts")
        self.autostart.setChecked(cfg["start_with_windows"])
        self.allow_buzz = QCheckBox("Let people buzz me (shakes this window and rings, even on Do not disturb)")
        self.allow_buzz.setChecked(cfg["allow_buzz"])
        self.away = QSpinBox()
        self.away.setRange(0, 240)
        self.away.setSuffix(" minutes")
        self.away.setSpecialValueText("Never")
        self.away.setValue(int(cfg["auto_away_minutes"]))
        section(form, "Appearance", first=True)
        form.addRow("Theme", self.theme)
        form.addRow("", self.festivals)
        form.addRow("Accent colour", swatches)
        section(form, "Notifications")
        form.addRow("", self.notifications)
        form.addRow("", self.sounds)
        form.addRow("", self.allow_buzz)
        section(form, "Files")
        form.addRow("Download folder", row)
        section(form, "Startup & presence")
        form.addRow("", self.close_to_tray)
        form.addRow("", self.autostart)
        form.addRow("Set me Away after idle", self.away)
        section(form, "Account & connection")
        self.lay.addLayout(form)

        secure = (f"<span style='color:{T.ACCENT}'>&#128274; Encrypted connection</span><br>"
                  f"Server fingerprint: <span style='font-family:Consolas; font-size:8pt'>"
                  f"{esc(ctx.conn.fingerprint[:47])}<br>{esc(ctx.conn.fingerprint[48:])}</span>"
                  if ctx.conn.fingerprint else
                  f"<span style='color:{T.DANGER}'>Connection is NOT encrypted</span>")
        policy = ("<br><span style='color:#ff9955'>Administrators can review conversations "
                  "(studio policy).</span>" if getattr(ctx.store, "review_notice", False) else "")
        from common.version import APP_VERSION
        info = QLabel(f"Connected to <b>{esc(ctx.store.server_name)}</b> at {esc(ctx.conn.host)}:{ctx.conn.port}"
                      f" as <b>{esc(ctx.store.me.get('username', ''))}</b><br>{secure}{policy}"
                      f"<br><span style='color:{T.FAINT}'>LAN Messenger {APP_VERSION}</span>")
        info.setTextInteractionFlags(Qt.TextSelectableByMouse)
        T.polish(info, muted=True)
        self.lay.addWidget(info)
        pw = QPushButton(" Change password")
        pw.setIcon(icon("key", T.TEXT, 16))
        pw.clicked.connect(lambda: ChangePasswordDialog(ctx).exec())
        self.lay.addWidget(pw, 0, Qt.AlignLeft)
        self.lay.addWidget(_buttons(self))

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
        cfg["sounds"] = self.sounds.isChecked()
        cfg["close_to_tray"] = self.close_to_tray.isChecked()
        cfg["auto_away_minutes"] = self.away.value()
        cfg["allow_buzz"] = self.allow_buzz.isChecked()
        if cfg["start_with_windows"] != self.autostart.isChecked():
            cfg["start_with_windows"] = self.autostart.isChecked()
            from client.config import set_autostart
            try:
                set_autostart(cfg["start_with_windows"])
            except OSError as e:
                QMessageBox.warning(self, "Settings", f"Could not change Windows startup: {e}")
        look_changed = ((cfg["theme"], cfg["accent"], cfg["festival_themes"])
                        != (self.theme.currentData(), self.accent, self.festivals.isChecked()))
        cfg["theme"], cfg["accent"] = self.theme.currentData(), self.accent
        cfg["festival_themes"] = self.festivals.isChecked()
        cfg.save()
        super().accept()
        if look_changed and QMessageBox.question(
                self.ctx, "New look", "Restart LAN Messenger now to apply the new theme?") == QMessageBox.Yes:
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
        self.lay.addLayout(form)
        self.body = QPlainTextEdit()
        self.body.setMinimumHeight(180)
        self.body.setPlaceholderText("Write the announcement...")
        self.lay.addWidget(self.body, 1)
        self.lay.addWidget(_buttons(self, "Send"))

    def accept(self):
        if not self.body.toPlainText().strip():
            return

        def done(reply):
            if reply.get("ok"):
                super(ComposeAnnouncementDialog, self).accept()
            else:
                QMessageBox.warning(self, "Announcement", reply.get("error", "Failed"))
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
        self.setMinimumSize(460, 280)
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
        when = datetime.datetime.fromtimestamp(ann["ts"]).strftime("%d %b %Y, %H:%M")
        target = ann.get("target_label") or ann.get("department") or ""
        to = f" to {target}" if target and target != "Everyone" else ""
        meta = QLabel(f"From <b>{esc(sender)}</b>{esc(to)}  ·  {when}")
        T.polish(meta, muted=True)
        col.addWidget(title)
        col.addWidget(meta)
        top.addLayout(col, 1)
        lay.addLayout(top)
        body = QLabel(linkify(ann["body"]))
        body.setWordWrap(True)
        body.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.LinksAccessibleByMouse)
        body.linkActivated.connect(open_link)
        body.setStyleSheet(f"background: {T.PANEL}; border-radius: 12px; padding: 14px; font-size: 10.5pt;")
        body.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        lay.addWidget(body, 1)
        ok = QPushButton("Got it")
        T.polish(ok, primary=True)
        ok.clicked.connect(self.close)
        lay.addWidget(ok, 0, Qt.AlignRight)

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
        preview = plain(QLabel(stickers.summary(msg)[:160]))
        preview.setWordWrap(True)
        preview.setStyleSheet(f"background: {T.PANEL}; border-left: 3px solid {T.ACCENT}; border-radius: 6px;"
                              f" padding: 8px; color: {T.MUTED};")
        self.lay.addWidget(preview)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search people and rooms...")
        self.search.textChanged.connect(self._filter)
        self.lay.addWidget(self.search)
        self.list = QListWidget()
        recent = sorted((c for c in store.convs.values() if c.last and store.conv_exists(c.conv)),
                        key=lambda c: c.last_ts, reverse=True)
        seen = set()
        targets = [c.conv for c in recent]
        targets += [P.room_conv(r) for r in sorted(store.rooms, key=lambda r: store.rooms[r]["name"].lower())]
        targets += [P.direct_conv(u) for u in sorted(store.users, key=lambda u: store.users[u]["name"].lower())]
        for conv in targets:
            if conv in seen or conv == msg["conv"]:
                continue
            seen.add(conv)
            it = QListWidgetItem(icon("hash" if conv.startswith("r:") else "user", T.MUTED, 16), store.title(conv))
            it.setData(Qt.UserRole, conv)
            self.list.addItem(it)
        self.list.itemDoubleClicked.connect(lambda _: self.accept())
        self.lay.addWidget(self.list, 1)
        self.lay.addWidget(_buttons(self, "Forward"))

    def _filter(self, q):
        q = q.lower()
        for i in range(self.list.count()):
            it = self.list.item(i)
            it.setHidden(bool(q) and q not in it.text().lower())

    def target(self):
        it = self.list.currentItem()
        return it.data(Qt.UserRole) if it else None


class ReadReceiptsDialog(Dialog):
    """Who has / hasn't read an announcement."""

    def __init__(self, parent, title, reads):
        super().__init__(parent, "Read by", 420)
        head = plain(QLabel(title))
        head.setStyleSheet("font-weight: 700; font-size: 11pt;")
        self.lay.addWidget(head)
        n_read, n_all = len(reads["read"]), len(reads["read"]) + len(reads["unread"])
        self.lay.addWidget(QLabel(f"<span style='color:{T.ACCENT}'>Read by {n_read} of {n_all}</span>"))
        lst = QListWidget()
        for p in reads["read"]:
            lst.addItem(QListWidgetItem(icon("check", T.ACCENT, 14), p["name"]))
        for p in reads["unread"]:
            it = QListWidgetItem(icon("close", T.FAINT, 14), p["name"] + "   (not read yet)")
            lst.addItem(it)
        lst.setMinimumHeight(300)
        self.lay.addWidget(lst, 1)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        self.lay.addWidget(close, 0, Qt.AlignRight)


class SearchDialog(Dialog):
    open_conv = Signal(str)

    def __init__(self, ctx):
        super().__init__(ctx, "Search messages", 560)
        self.ctx = ctx
        self.setMinimumHeight(480)
        row = QHBoxLayout()
        self.query = QLineEdit()
        self.query.setPlaceholderText("Search text or file names in all your chats...")
        self.query.returnPressed.connect(self.search)
        go = QPushButton("Search")
        T.polish(go, primary=True)
        go.clicked.connect(self.search)
        row.addWidget(self.query, 1)
        row.addWidget(go)
        self.lay.addLayout(row)
        self.status = plain(QLabel())
        T.polish(self.status, muted=True)
        self.lay.addWidget(self.status)
        self.list = QListWidget()
        self.list.setWordWrap(True)
        self.list.itemActivated.connect(self._open)
        self.list.itemDoubleClicked.connect(self._open)
        self.lay.addWidget(self.list, 1)

    def search(self):
        q = self.query.text().strip()
        if len(q) < 2:
            return
        self.status.setText("Searching...")

        def done(reply):
            self.list.clear()
            if not reply.get("ok"):
                self.status.setText(reply.get("error", "Search failed"))
                return
            msgs = reply["messages"]
            self.status.setText(f"{len(msgs)} result(s)" + (" (showing the latest 100)" if len(msgs) >= 100 else ""))
            store = self.ctx.store
            for m in msgs:
                where = store.title(m["conv"]) if store.conv_exists(m["conv"]) else "?"
                text = stickers.summary(m)
                it = QListWidgetItem(f"{store.user_name(m['sender_id'])}  →  {where}   ·  {fmt_list_time(m['ts'])}\n"
                                     f"{text[:300]}")
                it.setData(Qt.UserRole, m["conv"])
                self.list.addItem(it)
        self.ctx.conn.request("search", done, query=q)

    def _open(self, it):
        conv = it.data(Qt.UserRole)
        if self.ctx.store.conv_exists(conv):
            self.ctx.open_conv(conv)
            self.accept()
