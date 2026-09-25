"""Server console: start/stop the server and administer users, rooms, etc."""

import csv
import datetime
import logging
import time

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QFont
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QButtonGroup, QCheckBox, QComboBox, QDialog,
    QDialogButtonBox, QFileDialog, QFormLayout, QFrame, QGridLayout, QHBoxLayout,
    QHeaderView, QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow,
    QMenu, QMessageBox, QPlainTextEdit, QPushButton, QSpinBox, QStackedWidget,
    QSystemTrayIcon, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from common import theme as T
from common.icons import asset, icon
from common.protocol import human_size
from server.core import ServerCore, local_ips, startup_error_text


class _LogBridge(QObject, logging.Handler):
    line = Signal(str)

    def __init__(self):
        QObject.__init__(self)
        logging.Handler.__init__(self)
        self.setFormatter(logging.Formatter("%(asctime)s  %(levelname)-7s %(message)s", "%H:%M:%S"))

    def emit(self, record):  # noqa: A003 - logging.Handler API
        self.line.emit(self.format(record))


class _CoreEvents(QObject):
    changed = Signal(str)


def btn(text, icon_name=None, primary=False, danger=False):
    b = QPushButton(text)
    if icon_name:
        b.setIcon(icon(icon_name, T.ACCENT_TEXT if primary else (T.DANGER if danger else T.TEXT), 16))
    b.setCursor(Qt.PointingHandCursor)
    T.polish(b, primary=primary, danger=danger)
    return b


def make_table(headers):
    t = QTableWidget(0, len(headers))
    t.setHorizontalHeaderLabels(headers)
    t.verticalHeader().hide()
    t.setSelectionBehavior(QAbstractItemView.SelectRows)
    t.setSelectionMode(QAbstractItemView.SingleSelection)
    t.setEditTriggers(QAbstractItemView.NoEditTriggers)
    t.setAlternatingRowColors(True)
    t.setShowGrid(False)
    t.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
    t.horizontalHeader().setStretchLastSection(True)
    t.verticalHeader().setDefaultSectionSize(34)
    return t


def cell(text, data=None, color=None):
    it = QTableWidgetItem(str(text))
    if data is not None:
        it.setData(Qt.UserRole, data)
    if color:
        it.setForeground(QColor(color))
    return it


def fmt_time(ts):
    if not ts:
        return "never"
    return datetime.datetime.fromtimestamp(ts).strftime("%d %b %Y %H:%M")


class Page(QWidget):
    def __init__(self, title, subtitle=""):
        super().__init__()
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(28, 24, 28, 24)
        self.lay.setSpacing(14)
        head = QLabel(title)
        T.polish(head, heading=True)
        self.lay.addWidget(head)
        if subtitle:
            sub = QLabel(subtitle)
            T.polish(sub, muted=True)
            sub.setWordWrap(True)
            self.lay.addWidget(sub)

    def refresh(self):
        pass


# ============================================================== dashboard
class StatCard(QFrame):
    def __init__(self, title, icon_name):
        super().__init__()
        self.setStyleSheet(f"QFrame {{ background: {T.PANEL}; border-radius: 14px; }}")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(18, 16, 18, 16)
        ic = QLabel()
        ic.setPixmap(icon(icon_name, T.ACCENT, 26).pixmap(26, 26))
        lay.addWidget(ic)
        col = QVBoxLayout()
        self.value = QLabel("-")
        self.value.setStyleSheet("font-size: 20pt; font-weight: 700;")
        cap = QLabel(title)
        T.polish(cap, muted=True)
        col.addWidget(self.value)
        col.addWidget(cap)
        lay.addLayout(col, 1)


class DashboardPage(Page):
    def __init__(self, win):
        super().__init__("Dashboard", "Install this server on one always-on PC. Clients on the LAN "
                                      "find it automatically, or can connect to one of the addresses below.")
        self.win = win
        grid = QGridLayout()
        grid.setSpacing(12)
        self.cards = {}
        for i, (key, title, ic) in enumerate([
                ("online", "Users online", "users"), ("users", "Accounts", "user"),
                ("rooms", "Chat rooms", "hash"), ("messages", "Messages stored", "chat"),
                ("files", "Files stored", "file"), ("files_bytes", "Storage used", "folder")]):
            card = StatCard(title, ic)
            self.cards[key] = card
            grid.addWidget(card, i // 3, i % 3)
        self.lay.addLayout(grid)

        self.info = QLabel()
        self.info.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.info.setStyleSheet(f"background: {T.PANEL}; border-radius: 14px; padding: 18px; line-height: 150%;")
        self.info.setWordWrap(True)
        self.lay.addWidget(self.info)
        self.lay.addStretch(1)

    def refresh(self):
        api = self.win.api
        info = api.info()
        running = api.running
        state = (f"<span style='color:{T.ACCENT}'>&#9679; Running</span>" if running
                 else f"<span style='color:{T.DANGER}'>&#9679; Stopped</span>")
        uptime = ""
        if running and info.get("started_at"):
            mins = int((time.time() - info["started_at"]) // 60)
            uptime = f" &nbsp;·&nbsp; up {mins // 1440}d {mins // 60 % 24}h {mins % 60}m"
        b = info.get("last_backup")
        if b and b.get("ok"):
            backup = f"<span style='color:{T.ACCENT}'>OK</span> {fmt_time(b['time'])} ({human_size(b.get('size', 0))})"
        elif b:
            backup = f"<span style='color:{T.DANGER}'>FAILED {fmt_time(b['time'])}: {b.get('error', '')}</span>"
        else:
            backup = "none since the server started"
        self.info.setText(
            f"<b style='font-size:12pt'>{info['server_name']}</b> &nbsp; {state}{uptime}"
            f" &nbsp;·&nbsp; <span style='color:{T.MUTED}'>version {info.get('version', '')}</span><br><br>"
            f"<span style='color:{T.MUTED}'>Server address(es):</span> <b>{', '.join(info['ips'])}</b><br>"
            f"<span style='color:{T.MUTED}'>Chat &amp; file port (TCP):</span> <b>{info['tcp_port']}</b>"
            f" &nbsp;·&nbsp; <span style='color:{T.MUTED}'>Discovery port (UDP):</span> <b>{info['discovery_port']}</b><br>"
            f"<span style='color:{T.MUTED}'>Data folder:</span> {info['data_dir']}<br>"
            f"<span style='color:{T.MUTED}'>File storage:</span> {info['storage_dir']}<br>"
            f"<span style='color:{T.MUTED}'>Last backup:</span> {backup}<br>"
            + (f"<span style='color:{T.MUTED}'>Encryption:</span> <span style='color:{T.ACCENT}'>TLS on</span>"
               f" &nbsp;·&nbsp; <span style='color:{T.MUTED}'>fingerprint</span> "
               f"<span style='font-family:Consolas; font-size:8pt'>{info.get('fingerprint', '')}</span>"
               if info.get("tls") else
               f"<span style='color:{T.MUTED}'>Encryption:</span> "
               f"<span style='color:{T.DANGER}'>{'OFF' if running else '-'}</span>"))
        if not running:
            for c in self.cards.values():
                c.value.setText("-")
            return
        stats = api.call("admin_stats")
        for key, card in self.cards.items():
            v = stats.get(key, 0)
            card.value.setText(human_size(v) if key == "files_bytes" else f"{v:,}")


# ================================================================== users
class UserDialog(QDialog):
    def __init__(self, parent, user=None, users=(), roles=()):
        super().__init__(parent)
        self.setWindowTitle("Edit user" if user else "New user")
        self.setMinimumWidth(460)
        self.users = [u for u in users if not u["disabled"]]
        form = QFormLayout(self)
        form.setSpacing(10)
        self.username = QLineEdit(user["username"] if user else "")
        self.name = QLineEdit(user["display_name"] if user else "")
        self.department = QComboBox()
        self.department.setEditable(True)
        self.department.addItems(sorted({u["department"] for u in self.users if u["department"]}, key=str.lower))
        self.department.setCurrentText(user["department"] if user else "")
        self.department.lineEdit().setPlaceholderText("e.g. Compositing, Lighting, FX")
        self.section = QComboBox()
        self.section.setEditable(True)
        self.department.currentTextChanged.connect(self._fill_sections)
        self._fill_sections(self.department.currentText())
        self.section.setCurrentText(user["section"] if user else "")
        self.section.lineEdit().setPlaceholderText("optional, e.g. Roto, Paint, Prep")
        self.designation = QComboBox()
        self.designation.addItem("(none)", None)
        for r in roles:
            self.designation.addItem(r["name"], r["id"])
        self.designation.setCurrentIndex(max(0, self.designation.findData(user["role_id"] if user else None)))
        self.manager = QComboBox()
        self.manager.addItem("(nobody)", None)
        for u in sorted(self.users, key=lambda u: u["display_name"].lower()):
            if not user or u["id"] != user["id"]:
                extra = " · ".join(x for x in (u.get("designation"), u["department"]) if x)
                self.manager.addItem(f"{u['display_name']}" + (f"  —  {extra}" if extra else ""), u["id"])
        self.manager.setCurrentIndex(max(0, self.manager.findData(user["manager_id"] if user else None)))
        self.title = QLineEdit(user["title"] if user else "")
        self.title.setPlaceholderText("optional, e.g. Compositor, Matchmove Artist")
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.Password)
        self.password.setPlaceholderText("leave empty to keep" if user else "at least 4 characters")
        self.is_admin = QCheckBox("Administrator (full rights in the client)")
        if user:
            self.is_admin.setChecked(bool(user["is_admin"]))
        form.addRow("Username", self.username)
        form.addRow("Display name", self.name)
        form.addRow("Department", self.department)
        form.addRow("Section", self.section)
        form.addRow("Designation", self.designation)
        form.addRow("Reports to", self.manager)
        form.addRow("Job title", self.title)
        form.addRow("Password", self.password)
        form.addRow("", self.is_admin)
        bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        T.polish(bb.button(QDialogButtonBox.Save), primary=True)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        form.addRow(bb)

    def _fill_sections(self, dept):
        current = self.section.currentText()
        self.section.clear()
        self.section.addItems(sorted({u["section"] for u in self.users if u["section"]
                                      and u["department"].lower() == dept.strip().lower()}, key=str.lower))
        self.section.setCurrentText(current)

    def values(self):
        return {"username": self.username.text().strip(), "display_name": self.name.text().strip(),
                "department": self.department.currentText().strip(),
                "section": self.section.currentText().strip(),
                "role_id": self.designation.currentData(), "manager_id": self.manager.currentData(),
                "title": self.title.text().strip(), "is_admin": int(self.is_admin.isChecked()),
                "password": self.password.text()}


class UsersPage(Page):
    def __init__(self, win):
        super().__init__("Users", "Create an account for every artist. Users log in to the client with "
                                  "their username and password. Departments group people in the contact list.")
        self.win = win
        bar = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Filter users...")
        self.search.textChanged.connect(self.apply_filter)
        bar.addWidget(self.search, 1)
        add = btn("Add user", "plus", primary=True)
        add.clicked.connect(self.add_user)
        imp = btn("Import CSV", "upload")
        imp.clicked.connect(self.import_csv)
        imp.setToolTip("CSV columns: username, password, display_name, department, section,\n"
                       "designation, reports_to (username of the lead/supervisor), title")
        bar.addWidget(imp)
        bar.addWidget(add)
        self.lay.addLayout(bar)

        self.table = make_table(["Username", "Display name", "Department", "Section", "Designation",
                                 "Reports to", "Status", "Last seen"])
        self.table.doubleClicked.connect(self.edit_user)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self.context_menu)
        self.lay.addWidget(self.table, 1)

        row = QHBoxLayout()
        for text, ic, fn, danger in [("Edit", "edit", self.edit_user, False),
                                     ("Reset password", "key", self.reset_password, False),
                                     ("Enable / Disable", "power", self.toggle_disabled, False),
                                     ("Delete", "trash", self.delete_user, True)]:
            b = btn(text, ic, danger=danger)
            b.clicked.connect(fn)
            row.addWidget(b)
        row.addStretch(1)
        self.lay.addLayout(row)
        self.users = []

    def refresh(self):
        if not self.win.api.running:
            return
        selected = self.selected()
        self.users = self.win.api.call("admin_users")
        self.table.setRowCount(len(self.users))
        for r, u in enumerate(self.users):
            st = u["status"]
            designation = u["designation"] + ("  (admin)" if u["is_admin"] else "")
            self.table.setItem(r, 0, cell(u["username"], u["id"]))
            self.table.setItem(r, 1, cell(u["display_name"]))
            self.table.setItem(r, 2, cell(u["department"]))
            self.table.setItem(r, 3, cell(u["section"]))
            self.table.setItem(r, 4, cell(designation.strip(), color=T.ACCENT if u["is_admin"] else None))
            self.table.setItem(r, 5, cell(u["manager_name"]))
            label = "Disabled" if st == "disabled" else T.STATUS_LABELS.get(st, st)
            if u["sessions"] and st == "offline":
                label = "Invisible"
            self.table.setItem(r, 6, cell("● " + label, color=T.DANGER if st == "disabled"
                                          else T.STATUS_COLORS.get(st, T.MUTED)))
            self.table.setItem(r, 7, cell("now" if u["sessions"] else fmt_time(u["last_seen"])))
            if u["id"] == selected:
                self.table.selectRow(r)
        self.apply_filter()

    def apply_filter(self):
        q = self.search.text().lower()
        for r, u in enumerate(self.users):
            hay = " ".join(str(u[k]) for k in ("username", "display_name", "department", "section",
                                                "designation", "manager_name", "title")).lower()
            self.table.setRowHidden(r, bool(q) and q not in hay)

    def selected(self):
        items = self.table.selectedItems()
        return self.table.item(items[0].row(), 0).data(Qt.UserRole) if items else None

    def selected_user(self):
        uid = self.selected()
        return next((u for u in self.users if u["id"] == uid), None)

    def context_menu(self, pos):
        if not self.selected():
            return
        m = QMenu(self)
        m.addAction("Edit", self.edit_user)
        m.addAction("Reset password", self.reset_password)
        m.addAction("Enable / Disable", self.toggle_disabled)
        m.addAction("Disconnect", lambda: self.win.api.call("admin_kick", self.selected()))
        m.addAction("Remove profile photo", self.remove_photo)
        m.addSeparator()
        m.addAction("Delete", self.delete_user)
        m.exec(self.table.viewport().mapToGlobal(pos))

    def roles(self):
        return self.win.api.call("admin_roles")

    def add_user(self):
        dlg = UserDialog(self, users=self.users, roles=self.roles())
        while dlg.exec():
            v = dlg.values()
            try:
                self.win.api.call("admin_create_user", **v)
                break
            except ValueError as e:
                QMessageBox.warning(self, "Cannot create user", str(e))
        self.refresh()

    def edit_user(self):
        u = self.selected_user()
        if not u:
            return
        dlg = UserDialog(self, u, self.users, self.roles())
        while dlg.exec():
            v = dlg.values()
            password = v.pop("password") or None
            try:
                self.win.api.call("admin_update_user", u["id"], password=password, **v)
                break
            except ValueError as e:
                QMessageBox.warning(self, "Cannot save user", str(e))
        self.refresh()

    def reset_password(self):
        u = self.selected_user()
        if not u:
            return
        dlg = QDialog(self)
        dlg.setWindowTitle("Reset password")
        form = QFormLayout(dlg)
        pw = QLineEdit()
        pw.setEchoMode(QLineEdit.Password)
        pw.setPlaceholderText("temporary password")
        must = QCheckBox("User must choose a new password at next sign-in")
        must.setChecked(True)
        form.addRow(f"New password for {u['username']}", pw)
        form.addRow("", must)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        T.polish(bb.button(QDialogButtonBox.Ok), primary=True)
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        form.addRow(bb)
        while dlg.exec() and pw.text():
            try:
                self.win.api.call("admin_update_user", u["id"], password=pw.text(), must_change=must.isChecked())
                QMessageBox.information(self, "Password changed", "The password was changed.")
                return
            except ValueError as e:
                QMessageBox.warning(self, "Cannot change password", str(e))

    def toggle_disabled(self):
        u = self.selected_user()
        if u:
            self.win.api.call("admin_update_user", u["id"], disabled=int(not u["disabled"]))
            self.refresh()

    def remove_photo(self):
        u = self.selected_user()
        if u and QMessageBox.question(self, "Remove profile photo",
                                      f"Remove the profile photo of '{u['username']}'? (recorded in the audit log)"
                                      ) == QMessageBox.Yes:
            self.win.api.call("admin_remove_avatar", u["id"])

    def delete_user(self):
        u = self.selected_user()
        if not u:
            return
        if QMessageBox.question(self, "Delete user", f"Delete '{u['username']}'? Their old messages stay "
                                "in the history, but they can no longer log in.") == QMessageBox.Yes:
            self.win.api.call("admin_delete_user", u["id"])
            self.refresh()

    def import_csv(self):
        path, _ = QFileDialog.getOpenFileName(self, "Import users", "", "CSV files (*.csv)")
        if not path:
            return
        created, errors = 0, []
        with open(path, newline="", encoding="utf-8-sig") as f:
            rows = [{k.strip().lower(): (v or "").strip() for k, v in row.items() if k}
                    for row in csv.DictReader(f)]
        api = self.win.api
        # pass 1: create everyone; pass 2: link "reports_to" (leads may appear later in the file)
        for row in rows:
            try:
                api.call("admin_create_user",
                          username=row.get("username", ""), password=row.get("password", ""),
                          display_name=row.get("display_name", ""), department=row.get("department", ""),
                          section=row.get("section", ""), designation=row.get("designation", ""),
                          title=row.get("title", ""))
                created += 1
            except ValueError as e:
                errors.append(f"{row.get('username', '?')}: {e}")
        for row in rows:
            if row.get("reports_to"):
                user = api.call("admin_user_by_name", row.get("username", ""))
                try:
                    if user:
                        api.call("admin_update_user", user["id"], reports_to=row["reports_to"])
                except ValueError as e:
                    errors.append(f"{row.get('username', '?')}: {e}")
        self.refresh()
        msg = f"Created {created} users."
        if errors:
            msg += "\n\nSkipped:\n" + "\n".join(errors[:20])
        QMessageBox.information(self, "Import finished", msg)


# ================================================================== rooms
class RoomDialog(QDialog):
    def __init__(self, parent, users, room=None):
        super().__init__(parent)
        self.setWindowTitle("Edit room" if room else "New room")
        self.setMinimumSize(420, 520)
        lay = QVBoxLayout(self)
        form = QFormLayout()
        self.name = QLineEdit(room["name"] if room else "")
        self.name.setPlaceholderText("e.g. Compositing, Project X, All Studio")
        self.topic = QLineEdit(room["topic"] if room else "")
        form.addRow("Name", self.name)
        form.addRow("Topic", self.topic)
        lay.addLayout(form)
        head = QHBoxLayout()
        head.addWidget(QLabel("Members"))
        head.addStretch(1)
        all_btn = btn("Select all")
        all_btn.clicked.connect(lambda: self._check_all(Qt.Checked))
        none_btn = btn("None")
        none_btn.clicked.connect(lambda: self._check_all(Qt.Unchecked))
        head.addWidget(all_btn)
        head.addWidget(none_btn)
        lay.addLayout(head)
        self.list = QListWidget()
        members = set(room["members"]) if room else set()
        for u in users:
            if u["disabled"]:
                continue
            dept = f"  ·  {u['department']}" if u["department"] else ""
            it = QListWidgetItem(f"{u['display_name']}{dept}")
            it.setData(Qt.UserRole, u["id"])
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Checked if u["id"] in members else Qt.Unchecked)
            self.list.addItem(it)
        lay.addWidget(self.list, 1)
        bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        T.polish(bb.button(QDialogButtonBox.Save), primary=True)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def _check_all(self, state):
        for i in range(self.list.count()):
            self.list.item(i).setCheckState(state)

    def members(self):
        return [self.list.item(i).data(Qt.UserRole) for i in range(self.list.count())
                if self.list.item(i).checkState() == Qt.Checked]


class RoomsPage(Page):
    def __init__(self, win):
        super().__init__("Chat rooms", "Group chats for teams and projects. Automatic rooms (one per "
                                       "department / section) keep their members in sync by themselves — "
                                       "turn them on or off in Settings.")
        self.win = win
        bar = QHBoxLayout()
        bar.addStretch(1)
        add = btn("New room", "plus", primary=True)
        add.clicked.connect(lambda: self.edit_room(None))
        bar.addWidget(add)
        self.lay.addLayout(bar)
        self.table = make_table(["Room", "Type", "Topic", "Members"])
        self.table.doubleClicked.connect(lambda: self.edit_room(self.selected()))
        self.lay.addWidget(self.table, 1)
        row = QHBoxLayout()
        e = btn("Edit", "edit")
        e.clicked.connect(lambda: self.edit_room(self.selected()))
        d = btn("Delete", "trash", danger=True)
        d.clicked.connect(self.delete_room)
        row.addWidget(e)
        row.addWidget(d)
        row.addStretch(1)
        self.lay.addLayout(row)
        self.rooms = []

    def refresh(self):
        if not self.win.api.running:
            return
        self.rooms = self.win.api.call("admin_rooms")
        names = {u["id"]: u["display_name"] for u in self.win.api.call("admin_users")}
        self.table.setRowCount(len(self.rooms))
        for r, room in enumerate(self.rooms):
            self.table.setItem(r, 0, cell(room["name"], room["id"]))
            self.table.setItem(r, 1, cell("Automatic" if room["auto"] else "Manual",
                                          color=T.ACCENT if room["auto"] else T.MUTED))
            self.table.setItem(r, 2, cell(room["topic"]))
            member_names = ", ".join(sorted(names.get(m, "?") for m in room["members"]))
            self.table.setItem(r, 3, cell(f"{len(room['members'])}  —  {member_names}"))

    def selected(self):
        items = self.table.selectedItems()
        if not items:
            return None
        rid = self.table.item(items[0].row(), 0).data(Qt.UserRole)
        return next((r for r in self.rooms if r["id"] == rid), None)

    def edit_room(self, room):
        if room and room["auto"]:
            QMessageBox.information(self, "Automatic room", "This room follows each user's department and "
                                    "section. Change those on the Users page instead.")
            return
        users = self.win.api.call("admin_users")
        dlg = RoomDialog(self, users, room)
        while dlg.exec():
            try:
                self.win.api.call("admin_save_room", room["id"] if room else None,
                                   dlg.name.text(), dlg.topic.text(), dlg.members())
                break
            except ValueError as e:
                QMessageBox.warning(self, "Cannot save room", str(e))
        self.refresh()

    def delete_room(self):
        room = self.selected()
        if room and room["auto"]:
            QMessageBox.information(self, "Automatic room", "Automatic rooms come back while the department "
                                    "or section exists. Turn automatic rooms off in Settings instead.")
            return
        if room and QMessageBox.question(self, "Delete room",
                                         f"Delete room '{room['name']}'?") == QMessageBox.Yes:
            self.win.api.call("admin_delete_room", room["id"])
            self.refresh()


# ============================================================ designations
ANNOUNCE_CHOICES = [("none", "Cannot send announcements"), ("team", "Their own team (people reporting to them)"),
                    ("section", "Their own section"), ("department", "Their own department"),
                    ("all", "Everyone in the studio")]


class RoleDialog(QDialog):
    def __init__(self, parent, role=None):
        super().__init__(parent)
        self.setWindowTitle("Edit designation" if role else "New designation")
        self.setMinimumWidth(460)
        form = QFormLayout(self)
        form.setSpacing(10)
        self.name = QLineEdit(role["name"] if role else "")
        self.name.setPlaceholderText("e.g. Lead, Supervisor, HR")
        self.level = QSpinBox()
        self.level.setRange(0, 1000)
        self.level.setValue(role["level"] if role else 30)
        self.level.setToolTip("Higher levels are listed first in the directory")
        self.announce = QComboBox()
        for key, text in ANNOUNCE_CHOICES:
            self.announce.addItem(text, key)
        self.announce.setCurrentIndex(max(0, self.announce.findData(role["announce"] if role else "none")))
        self.create_rooms = QCheckBox("Can create chat rooms")
        self.manage_users = QCheckBox("Can reset passwords and disable accounts (HR / IT)")
        self.see_all = QCheckBox("Can see everyone in the studio")
        self.see_all.setToolTip("If off, they only see their own department, their reporting line,\n"
                                "people in their rooms and 'always visible' people")
        self.always_visible = QCheckBox("Always visible to everyone (e.g. HR, IT, Management)")
        for box, key, default in ((self.create_rooms, "create_rooms", True), (self.manage_users, "manage_users", False),
                                  (self.see_all, "see_all", True), (self.always_visible, "always_visible", False)):
            box.setChecked(bool(role[key]) if role else default)
        form.addRow("Designation", self.name)
        form.addRow("Rank level", self.level)
        form.addRow("Announcements to", self.announce)
        form.addRow("", self.create_rooms)
        form.addRow("", self.manage_users)
        form.addRow("", self.see_all)
        form.addRow("", self.always_visible)
        bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        T.polish(bb.button(QDialogButtonBox.Save), primary=True)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        form.addRow(bb)

    def values(self):
        return {"name": self.name.text(), "level": self.level.value(), "announce": self.announce.currentData(),
                "create_rooms": int(self.create_rooms.isChecked()),
                "manage_users": int(self.manage_users.isChecked()),
                "see_all": int(self.see_all.isChecked()), "always_visible": int(self.always_visible.isChecked())}


class RolesPage(Page):
    def __init__(self, win):
        super().__init__("Designations", "Each user gets one designation. It is shown next to their name and "
                                         "decides what they may do. Administrators can always do everything.")
        self.win = win
        bar = QHBoxLayout()
        bar.addStretch(1)
        add = btn("New designation", "plus", primary=True)
        add.clicked.connect(lambda: self.edit(None))
        bar.addWidget(add)
        self.lay.addLayout(bar)
        self.table = make_table(["Designation", "Level", "Announcements to", "Create rooms",
                                 "Manage accounts", "Sees", "Always visible", "Users"])
        self.table.doubleClicked.connect(lambda: self.edit(self.selected()))
        self.lay.addWidget(self.table, 1)
        row = QHBoxLayout()
        e = btn("Edit", "edit")
        e.clicked.connect(lambda: self.edit(self.selected()))
        d = btn("Delete", "trash", danger=True)
        d.clicked.connect(self.delete)
        row.addWidget(e)
        row.addWidget(d)
        row.addStretch(1)
        self.lay.addLayout(row)
        self.roles = []

    def refresh(self):
        if not self.win.api.running:
            return
        self.roles = self.win.api.call("admin_roles")
        labels = dict(ANNOUNCE_CHOICES)
        yes = lambda v: cell("Yes" if v else "—", color=T.ACCENT if v else T.FAINT)  # noqa: E731
        self.table.setRowCount(len(self.roles))
        for r, role in enumerate(self.roles):
            self.table.setItem(r, 0, cell(role["name"], role["id"]))
            self.table.setItem(r, 1, cell(role["level"]))
            self.table.setItem(r, 2, cell(labels.get(role["announce"], role["announce"]).split(" (")[0],
                                          color=T.ACCENT if role["announce"] != "none" else T.FAINT))
            self.table.setItem(r, 3, yes(role["create_rooms"]))
            self.table.setItem(r, 4, yes(role["manage_users"]))
            self.table.setItem(r, 5, cell("Everyone" if role["see_all"] else "Own department"))
            self.table.setItem(r, 6, yes(role["always_visible"]))
            self.table.setItem(r, 7, cell(role["users"]))

    def selected(self):
        items = self.table.selectedItems()
        if not items:
            return None
        rid = self.table.item(items[0].row(), 0).data(Qt.UserRole)
        return next((r for r in self.roles if r["id"] == rid), None)

    def edit(self, role):
        dlg = RoleDialog(self, role)
        while dlg.exec():
            try:
                self.win.api.call("admin_save_role", role["id"] if role else None, **dlg.values())
                break
            except ValueError as e:
                QMessageBox.warning(self, "Cannot save designation", str(e))
        self.refresh()

    def delete(self):
        role = self.selected()
        if role and QMessageBox.question(
                self, "Delete designation", f"Delete '{role['name']}'? {role['users']} user(s) will have no "
                "designation until you pick a new one.") == QMessageBox.Yes:
            self.win.api.call("admin_delete_role", role["id"])
            self.refresh()


# ================================================================ org chart
class OrgPage(Page):
    def __init__(self, win):
        super().__init__("Org chart", "Who is where, and who reports to whom. Set department, section, "
                                      "designation and 'Reports to' on the Users page.")
        from common import orgviews
        self.win = win
        self.browser = orgviews.OrgBrowser(action_text="Edit", view="chart")
        self.browser.open_person.connect(self.edit_user)
        self.browser.person_menu.connect(lambda uid, pos: self.edit_user(uid))
        self.lay.addWidget(self.browser, 1)

    def refresh(self):
        if self.win.api.running:
            self.browser.set_people(self.win.api.call("admin_org"))

    def edit_user(self, uid):
        if uid:
            page = self.win.users_page
            page.refresh()
            for r in range(page.table.rowCount()):
                if page.table.item(r, 0).data(Qt.UserRole) == uid:
                    page.table.selectRow(r)
            page.edit_user()
            self.refresh()


# ================================================================= online
class OnlinePage(Page):
    def __init__(self, win):
        super().__init__("Online now", "Connected client sessions. A user logged in on two PCs shows twice.")
        self.win = win
        self.table = make_table(["User", "Username", "IP address", "Status", "Connected since"])
        self.lay.addWidget(self.table, 1)
        row = QHBoxLayout()
        k = btn("Disconnect user", "power", danger=True)
        k.clicked.connect(self.kick)
        row.addWidget(k)
        row.addStretch(1)
        self.lay.addLayout(row)

    def refresh(self):
        if not self.win.api.running:
            self.table.setRowCount(0)
            return
        sessions = self.win.api.call("admin_sessions")
        self.table.setRowCount(len(sessions))
        for r, s in enumerate(sessions):
            self.table.setItem(r, 0, cell(s["name"], s["user_id"]))
            self.table.setItem(r, 1, cell(s["username"]))
            self.table.setItem(r, 2, cell(s["ip"]))
            self.table.setItem(r, 3, cell("● " + T.STATUS_LABELS[s["status"]],
                                          color=T.STATUS_COLORS[s["status"]]))
            self.table.setItem(r, 4, cell(fmt_time(s["since"])))

    def kick(self):
        items = self.table.selectedItems()
        if items:
            uid = self.table.item(items[0].row(), 0).data(Qt.UserRole)
            self.win.api.call("admin_kick", uid)
            QTimer.singleShot(300, self.refresh)


# ========================================================== announcements
class AnnouncePage(Page):
    def __init__(self, win):
        super().__init__("Announcement", "Send a message that pops up on every connected client "
                                         "(offline users see it when they log in).")
        self.win = win
        form = QFormLayout()
        form.setSpacing(10)
        self.title = QLineEdit()
        self.title.setPlaceholderText("e.g. Server maintenance tonight")
        self.dept = QComboBox()
        form.addRow("Title", self.title)
        form.addRow("Send to", self.dept)
        self.lay.addLayout(form)
        self.body = QPlainTextEdit()
        self.body.setPlaceholderText("Write the announcement...")
        self.body.setMaximumHeight(140)
        self.lay.addWidget(self.body)
        row = QHBoxLayout()
        row.addStretch(1)
        send = btn("Send announcement", "megaphone", primary=True)
        send.clicked.connect(self.send)
        row.addWidget(send)
        self.lay.addLayout(row)
        sent = QLabel("Sent announcements — double-click one to see who has read it")
        T.polish(sent, muted=True)
        self.lay.addWidget(sent)
        self.table = make_table(["When", "From", "Title", "To", "Read"])
        self.table.doubleClicked.connect(self.show_reads)
        self.lay.addWidget(self.table, 1)
        self.anns = []

    def show_reads(self):
        items = self.table.selectedItems()
        if not items:
            return
        ann = self.anns[items[0].row()]
        reads = self.win.api.call("admin_announcement_reads", ann["id"])
        dlg = QDialog(self)
        dlg.setWindowTitle("Read by")
        dlg.setMinimumSize(420, 460)
        lay = QVBoxLayout(dlg)
        n_read, n_all = len(reads["read"]), len(reads["read"]) + len(reads["unread"])
        lay.addWidget(QLabel(f"<b>{ann['title']}</b><br><span style='color:{T.ACCENT}'>Read by {n_read} of {n_all}</span>"))
        lst = QListWidget()
        for p in reads["read"]:
            lst.addItem(QListWidgetItem(icon("check", T.ACCENT, 14), p["name"]))
        for p in reads["unread"]:
            lst.addItem(QListWidgetItem(icon("close", T.FAINT, 14), p["name"] + "   (not read yet)"))
        lay.addWidget(lst, 1)
        dlg.exec()

    def refresh(self):
        if not self.win.api.running:
            return
        self.anns = self.win.api.call("admin_announcements", 200)
        self.table.setRowCount(len(self.anns))
        for r, a in enumerate(self.anns):
            self.table.setItem(r, 0, cell(fmt_time(a["ts"]), a["id"]))
            self.table.setItem(r, 1, cell(a["sender_name"]))
            self.table.setItem(r, 2, cell(a["title"]))
            self.table.setItem(r, 3, cell(a["target_label"]))
            done = a["read_count"] >= a["total"] and a["total"]
            self.table.setItem(r, 4, cell(f"{a['read_count']} / {a['total']}", color=T.ACCENT if done else None))
        current = self.dept.currentData()
        self.dept.clear()
        self.dept.addItem("Everyone", ("all", "", ""))
        users = [u for u in self.win.api.call("admin_users") if not u["disabled"]]
        depts = sorted({u["department"] for u in users if u["department"]}, key=str.lower)
        for d in depts:
            self.dept.addItem(f"Department: {d}", ("department", d, ""))
            for s in sorted({u["section"] for u in users if u["section"] and u["department"] == d}, key=str.lower):
                self.dept.addItem(f"      Section: {d} · {s}", ("section", d, s))
        idx = self.dept.findData(current)
        self.dept.setCurrentIndex(max(idx, 0))

    def send(self):
        if not self.body.toPlainText().strip():
            return
        try:
            kind, dept, sect = self.dept.currentData()
            self.win.api.call("admin_announce", self.title.text(), self.body.toPlainText(), kind, dept, sect)
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "Not sent", str(e))
            return
        self.title.clear()
        self.body.clear()
        QMessageBox.information(self, "Sent", "Announcement sent.")
        self.refresh()


# ============================================================ chat review
class ReviewPage(Page):
    def __init__(self, win):
        super().__init__("Chat review", "For policy / HR investigations. Every conversation you open here is "
                                        "recorded in the audit log, and users are told at sign-in that chats may "
                                        "be reviewed. Switch it off in Settings > Privacy.")
        self.win = win
        from PySide6.QtWidgets import QSplitter
        bar = QHBoxLayout()
        self.user = QComboBox()
        self.user.setMinimumWidth(320)
        self.user.currentIndexChanged.connect(lambda _: self.load_convs())
        bar.addWidget(QLabel("Person"))
        bar.addWidget(self.user, 1)
        self.export_btn = btn("Export conversation", "upload")
        self.export_btn.clicked.connect(self.export)
        bar.addWidget(self.export_btn)
        self.lay.addLayout(bar)
        split = QSplitter()
        self.convs = QListWidget()
        self.convs.setMaximumWidth(340)
        self.convs.currentItemChanged.connect(lambda *_: self.load_messages())
        self.view = QPlainTextEdit()
        self.view.setReadOnly(True)
        self.view.setFont(QFont("Segoe UI", 10))
        split.addWidget(self.convs)
        split.addWidget(self.view)
        split.setStretchFactor(1, 1)
        self.lay.addWidget(split, 1)
        self.messages = []
        self._users_loaded = False

    def refresh(self):
        if not self.win.api.running or self._users_loaded:
            return
        current = self.user.currentData()
        self.user.blockSignals(True)
        self.user.clear()
        self.user.addItem("— choose a person —", None)
        for u in sorted(self.win.api.call("admin_users"), key=lambda u: u["display_name"].lower()):
            extra = " · ".join(x for x in (u["department"], u["designation"]) if x)
            self.user.addItem(f"{u['display_name']}  ({u['username']})" + (f"   {extra}" if extra else ""), u["id"])
        self.user.setCurrentIndex(max(0, self.user.findData(current)))
        self.user.blockSignals(False)
        self._users_loaded = True

    def showEvent(self, e):
        self._users_loaded = False
        super().showEvent(e)

    def load_convs(self):
        self.convs.clear()
        self.view.clear()
        uid = self.user.currentData()
        if not uid:
            return
        try:
            for c in self.win.api.call("admin_review_conversations", uid):
                it = QListWidgetItem(f"{c['title']}\n{c['messages']} messages · last {fmt_time(c['last'])}")
                it.setData(Qt.UserRole, c["key"])
                self.convs.addItem(it)
        except ValueError as e:
            QMessageBox.information(self, "Chat review", str(e))

    def load_messages(self):
        it = self.convs.currentItem()
        if not it:
            return
        try:
            self.messages = self.win.api.call("admin_review_history", it.data(Qt.UserRole), None, 1000)
        except ValueError as e:
            QMessageBox.information(self, "Chat review", str(e))
            return
        lines = []
        for m in self.messages:
            when = datetime.datetime.fromtimestamp(m["ts"]).strftime("%d %b %Y %H:%M")
            body = {"sticker": f"[sticker: {m['body']}]", "poll": f"[poll] {m['body']}"}.get(m.get("kind"), m["body"])
            if m["file"]:
                body = (body + "  " if body else "") + f"[file: {m['file']}]"
            if m["edited"]:
                body += "  (edited)"
            lines.append(f"[{when}] {m['sender']}: {body}")
        self.view.setPlainText("\n".join(lines) or "(no messages)")

    def export(self):
        it = self.convs.currentItem()
        if not it or not self.messages:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export conversation", "conversation.txt", "Text files (*.txt)")
        if path:
            with open(path, "w", encoding="utf-8") as f:
                f.write(f"{it.text().splitlines()[0]}\nExported {fmt_time(time.time())}\n\n")
                f.write(self.view.toPlainText())


# ================================================================ reports
class ReportsPage(Page):
    def __init__(self, win):
        super().__init__("Reports", "Who is using the messenger and how much — per department, person and room.")
        self.win = win
        from PySide6.QtWidgets import QTabWidget
        bar = QHBoxLayout()
        self.period = QComboBox()
        for d in (7, 30, 90, 365):
            self.period.addItem(f"Last {d} days", d)
        self.period.setCurrentIndex(1)
        self.period.currentIndexChanged.connect(lambda _: self.refresh(force=True))
        bar.addWidget(self.period)
        bar.addStretch(1)
        exp = btn("Export CSV", "upload")
        exp.clicked.connect(self.export)
        bar.addWidget(exp)
        self.lay.addLayout(bar)
        self.summary = QLabel()
        self.summary.setStyleSheet(f"background: {T.PANEL}; border-radius: 12px; padding: 14px;")
        self.lay.addWidget(self.summary)
        self.chart = _DailyChart()
        self.lay.addWidget(self.chart)
        self.tabs = QTabWidget()
        self.t_depts = make_table(["Department", "Users", "Active", "Messages", "Files sent", "Uploaded"])
        self.t_people = make_table(["Person", "Department", "Section", "Messages", "Files", "Uploaded",
                                    "Stored now", "Last seen"])
        self.t_rooms = make_table(["Room", "Messages"])
        self.tabs.addTab(self.t_depts, "Departments")
        self.tabs.addTab(self.t_people, "People")
        self.tabs.addTab(self.t_rooms, "Busiest rooms")
        self.lay.addWidget(self.tabs, 1)
        self.data = None
        self._loaded_for = None

    def refresh(self, force=False):
        if not self.win.api.running:
            return
        days = self.period.currentData()
        if not force and self._loaded_for == days:
            return                       # reports are not re-read every 5 seconds
        self._loaded_for = days
        r = self.data = self.win.api.call("admin_report", days)
        self.summary.setText(
            f"<b style='font-size:12pt'>{r['total_messages']:,}</b> messages &nbsp;·&nbsp; "
            f"<b style='font-size:12pt'>{r['total_files']:,}</b> files ({human_size(r['total_uploaded'])}) &nbsp;·&nbsp; "
            f"<b style='font-size:12pt'>{r['active_users']}</b> of {r['users']} people active "
            f"<span style='color:{T.MUTED}'>in the last {r['days']} days</span>")
        self.chart.set_data(r["daily"])
        self._fill(self.t_depts, [[d["department"], d["users"], d["active"], d["messages"], d["files"],
                                   human_size(d["uploaded"])] for d in r["departments"]])
        self._fill(self.t_people, [[p["name"] + ("  (disabled)" if p["disabled"] else ""), p["department"],
                                    p["section"], p["messages"], p["files"], human_size(p["uploaded"]),
                                    human_size(p["stored"]), fmt_time(p["last_seen"])] for p in r["people"]])
        self._fill(self.t_rooms, [[x["room"], x["messages"]] for x in r["rooms"]])

    @staticmethod
    def _fill(table, rows):
        table.setRowCount(len(rows))
        for i, row in enumerate(rows):
            for j, v in enumerate(row):
                table.setItem(i, j, cell(v))

    def export(self):
        if not self.data:
            return
        table = self.tabs.currentWidget()
        name = self.tabs.tabText(self.tabs.currentIndex()).lower().replace(" ", "_")
        path, _ = QFileDialog.getSaveFileName(self, "Export report", f"report_{name}.csv", "CSV files (*.csv)")
        if path:
            with open(path, "w", newline="", encoding="utf-8-sig") as f:
                w = csv.writer(f)
                w.writerow([table.horizontalHeaderItem(c).text() for c in range(table.columnCount())])
                for r in range(table.rowCount()):
                    w.writerow([table.item(r, c).text() if table.item(r, c) else "" for c in range(table.columnCount())])


class _DailyChart(QWidget):
    """Small bar chart of messages per day."""

    def __init__(self):
        super().__init__()
        self.setFixedHeight(110)
        self.data = []

    def set_data(self, daily):
        self.data = daily
        self.update()

    def paintEvent(self, _):
        from PySide6.QtGui import QPainter
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        if not self.data:
            p.setPen(QColor(T.FAINT))
            p.drawText(self.rect(), Qt.AlignCenter, "No messages in this period")
            return
        top = max(d["messages"] for d in self.data) or 1
        n = len(self.data)
        bw = max(2.0, (w - 20) / n)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(T.ACCENT))
        for i, d in enumerate(self.data):
            bh = (h - 30) * d["messages"] / top
            p.drawRoundedRect(int(10 + i * bw + 1), int(h - 18 - bh), max(1, int(bw - 2)), int(bh), 2, 2)
        p.setPen(QColor(T.FAINT))
        p.drawText(10, h - 2, self.data[0]["day"])
        p.drawText(w - 80, h - 2, self.data[-1]["day"])
        p.drawText(10, 12, f"max {top} / day")


# ================================================================ updates
class UpdatesPage(Page):
    def __init__(self, win):
        super().__init__("Client updates", "Publish a new client version: build it (build\\build.bat), then copy "
                                           "LANMessenger-Client-Setup-x.y.z.exe into the updates folder below. "
                                           "Signed-in clients are told within a minute and can install it "
                                           "(Windows asks for an administrator password on PCs where the user "
                                           "is not an admin). For silent roll-outs use your deployment tool.")
        self.win = win
        self.info = QLabel()
        self.info.setWordWrap(True)
        self.info.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.info.setStyleSheet(f"background: {T.PANEL}; border-radius: 12px; padding: 16px;")
        self.lay.addWidget(self.info)
        row = QHBoxLayout()
        self.open_btn = btn("Open updates folder", "folder")
        self.open_btn.clicked.connect(self.open_folder)
        row.addWidget(self.open_btn)
        row.addStretch(1)
        self.lay.addLayout(row)
        self.lay.addStretch(1)
        self.folder = ""

    def refresh(self):
        if not self.win.api.running:
            return
        from common.version import APP_VERSION
        u = self.win.api.call("admin_updates")
        self.folder = u["folder"]
        latest = u["latest"]
        offer = (f"<span style='color:{T.ACCENT}'>Offering version <b>{latest['version']}</b></span> "
                 f"({latest['name']}, {human_size(latest['size'])})" if latest else
                 f"<span style='color:{T.MUTED}'>No client update in the folder.</span>")
        self.info.setText(f"Updates folder (on the server PC):<br><b>{self.folder}</b><br><br>{offer}<br>"
                          f"<span style='color:{T.MUTED}'>This console is version {APP_VERSION}.</span>")
        self.open_btn.setEnabled(not self.win.api.remote)

    def open_folder(self):
        import os
        os.makedirs(self.folder, exist_ok=True)
        os.startfile(self.folder)


# =============================================================== settings
class SettingsPage(Page):
    def __init__(self, win):
        super().__init__("Settings", "Port and storage changes need a server restart.")
        self.win = win
        from PySide6.QtWidgets import QScrollArea
        area = QScrollArea()
        area.setWidgetResizable(True)
        body = QWidget()
        T.bg_pane(body)
        self.form = form = QFormLayout(body)
        form.setSpacing(10)
        form.setContentsMargins(0, 0, 12, 0)
        area.setWidget(body)
        self.lay.addWidget(area, 1)

        def section(text):
            lbl = QLabel(text)
            lbl.setStyleSheet(f"color: {T.ACCENT}; font-weight: 700; padding-top: 14px;")
            form.addRow(lbl)

        def spin(lo, hi, suffix="", special=None):
            w = QSpinBox()
            w.setRange(lo, hi)
            if suffix:
                w.setSuffix(suffix)
            if special:
                w.setSpecialValueText(special)
            return w

        section("General")
        self.name = QLineEdit()
        self.tcp = spin(1024, 65535)
        self.udp = spin(1024, 65535)
        form.addRow("Server name", self.name)
        form.addRow("TCP port (chat + files)", self.tcp)
        form.addRow("UDP port (discovery)", self.udp)

        section("Files")
        storage_row = QHBoxLayout()
        self.storage = QLineEdit()
        self.browse_btn = btn("Browse", "folder")
        self.browse_btn.clicked.connect(lambda: self._browse(self.storage, "File storage folder"))
        storage_row.addWidget(self.storage, 1)
        storage_row.addWidget(self.browse_btn)
        self.max_mb = spin(1, 1024 * 1024, " MB")
        self.retention = spin(0, 3650, " days", "Keep forever")
        form.addRow("File storage folder", storage_row)
        form.addRow("Max file size", self.max_mb)
        form.addRow("Delete shared files after", self.retention)
        self.unclaimed = spin(0, 3650, " days", "Never")
        form.addRow("Delete files nobody downloaded after", self.unclaimed)

        section("Automatic rooms")
        self.auto_dept = QCheckBox("A room for every department (members follow each user's department)")
        self.auto_sect = QCheckBox("A room for every section inside a department")
        self.auto_all = QCheckBox("An \"All Studio\" room with everyone")
        form.addRow("", self.auto_dept)
        form.addRow("", self.auto_sect)
        form.addRow("", self.auto_all)

        section("Passwords")
        self.pw_len = spin(4, 64, " characters")
        self.pw_mix = QCheckBox("Must contain letters and numbers")
        self.pw_age = spin(0, 3650, " days", "Never")
        form.addRow("Minimum length", self.pw_len)
        form.addRow("", self.pw_mix)
        form.addRow("Ask for a new password every", self.pw_age)

        section("Backups")
        self.bk_enabled = QCheckBox("Back up the database automatically every day")
        bk_row = QHBoxLayout()
        self.bk_dir = QLineEdit()
        self.bk_browse = btn("Browse", "folder")
        self.bk_browse.clicked.connect(lambda: self._browse(self.bk_dir, "Backup folder"))
        bk_row.addWidget(self.bk_dir, 1)
        bk_row.addWidget(self.bk_browse)
        self.bk_hour = spin(0, 23, ":00")
        self.bk_keep = spin(1, 365, " backups")
        now_row = QHBoxLayout()
        self.bk_now = btn("Back up now", "download")
        self.bk_now.clicked.connect(self.backup_now)
        self.bk_status = QLabel()
        T.polish(self.bk_status, muted=True)
        now_row.addWidget(self.bk_now)
        now_row.addWidget(self.bk_status, 1)
        form.addRow("", self.bk_enabled)
        form.addRow("Backup folder", bk_row)
        form.addRow("Time of day", self.bk_hour)
        form.addRow("Keep the last", self.bk_keep)
        form.addRow("", now_row)
        hint = QLabel("Tip: point the backup folder at another disk or a network share. Shared files are not "
                      "part of the backup; include the file storage folder in your normal server backup.")
        hint.setWordWrap(True)
        T.polish(hint, muted=True)
        form.addRow("", hint)

        section("Pipeline API (render farm, scripts)")
        self.api_enabled = QCheckBox("Allow scripts to send messages (needs a server restart)")
        self.api_port = spin(1024, 65535)
        key_row = QHBoxLayout()
        self.api_key = QLineEdit()
        self.api_key.setReadOnly(True)
        self.api_key.setPlaceholderText("no key yet")
        gen = btn("New key", "key")
        gen.clicked.connect(self._new_key)
        key_row.addWidget(self.api_key, 1)
        key_row.addWidget(gen)
        self.api_bot = QLineEdit()
        self.api_example = QLabel()
        self.api_example.setWordWrap(True)
        self.api_example.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.api_example.setStyleSheet(f"font-family: Consolas; font-size: 8.5pt; color: {T.MUTED};"
                                       f" background: {T.PANEL}; border-radius: 8px; padding: 8px;")
        form.addRow("", self.api_enabled)
        form.addRow("HTTP port", self.api_port)
        form.addRow("API key", key_row)
        form.addRow("Messages appear from", self.api_bot)
        form.addRow("Example", self.api_example)

        section("Privacy")
        self.review = QCheckBox("Administrators may review any conversation from this console "
                                "(users are told at sign-in)")
        form.addRow("", self.review)

        row = QHBoxLayout()
        row.addStretch(1)
        save = btn("Save settings", "check", primary=True)
        save.clicked.connect(self.save)
        row.addWidget(save)
        self.lay.addLayout(row)
        self.cfg = {}

    def refresh(self):
        try:
            cfg = self.cfg = self.win.api.config()
        except (ValueError, ConnectionError):
            return
        self.name.setText(cfg["server_name"])
        self.tcp.setValue(int(cfg["tcp_port"]))
        self.udp.setValue(int(cfg["discovery_port"]))
        self.storage.setText(cfg["storage_dir"])
        self.storage.setPlaceholderText(cfg["_storage_dir"])
        self.max_mb.setValue(int(cfg["max_file_mb"]))
        self.retention.setValue(int(cfg["file_retention_days"]))
        self.unclaimed.setValue(int(cfg.get("unclaimed_file_days", 0)))
        self.api_enabled.setChecked(bool(cfg.get("api_enabled")))
        self.api_port.setValue(int(cfg.get("api_port", 5152)))
        self.api_key.setText(cfg.get("api_key", ""))
        self.api_bot.setText(cfg.get("api_bot_name", "Pipeline Bot"))
        self._update_example()
        self.auto_dept.setChecked(bool(cfg["auto_department_rooms"]))
        self.auto_sect.setChecked(bool(cfg["auto_section_rooms"]))
        self.auto_all.setChecked(bool(cfg["auto_all_room"]))
        self.pw_len.setValue(int(cfg["min_password_length"]))
        self.pw_mix.setChecked(bool(cfg["password_require_mix"]))
        self.pw_age.setValue(int(cfg["password_max_age_days"]))
        self.bk_enabled.setChecked(bool(cfg["backup_enabled"]))
        self.bk_dir.setText(cfg["backup_dir"])
        self.bk_dir.setPlaceholderText(cfg["_backup_dir"])
        self.bk_hour.setValue(int(cfg["backup_hour"]))
        self.bk_keep.setValue(int(cfg["backup_keep"]))
        self.review.setChecked(bool(cfg["admin_review_enabled"]))
        remote = self.win.api.remote
        self.browse_btn.setEnabled(not remote)       # folders are on the server PC
        self.bk_browse.setEnabled(not remote)
        self.bk_now.setEnabled(self.win.api.running)

    def _new_key(self):
        import secrets
        if self.api_key.text() and QMessageBox.question(
                self, "New API key", "Replace the current key? Scripts using the old key will stop working.")                 != QMessageBox.Yes:
            return
        self.api_key.setText(secrets.token_urlsafe(24))
        self._update_example()

    def _update_example(self):
        info_ip = "SERVER-IP"
        try:
            info_ip = self.win.api.info()["ips"][0]
        except Exception:  # noqa: BLE001
            pass
        key = self.api_key.text() or "YOUR-KEY"
        self.api_example.setText(
            f'curl -X POST http://{info_ip}:{self.api_port.value()}/api/message '
            f'-H "Authorization: Bearer {key}" -H "Content-Type: application/json" '
            f'-d "{{\\"to\\": \\"#Comp Team\\", \\"text\\": \\"FAL_020 v014 rendered\\"}}"<br><br>'
            f'to: a username (alice) or #room name')
        self.api_example.setTextFormat(Qt.RichText)

    def _browse(self, edit, title):
        d = QFileDialog.getExistingDirectory(self, title, edit.text() or edit.placeholderText())
        if d:
            edit.setText(d)

    def backup_now(self):
        try:
            r = self.win.api.call("backup_now")
        except (ValueError, ConnectionError) as e:
            QMessageBox.warning(self, "Backup", str(e))
            return
        if r and r.get("ok"):
            self.bk_status.setText(f"Saved {r['path']} ({human_size(r.get('size', 0))})")
        else:
            QMessageBox.warning(self, "Backup failed", (r or {}).get("error", "Unknown error"))

    def save(self):
        cfg = self.cfg
        values = dict(
            server_name=self.name.text().strip() or "Studio Messenger", tcp_port=self.tcp.value(),
            discovery_port=self.udp.value(), storage_dir=self.storage.text().strip(),
            max_file_mb=self.max_mb.value(), file_retention_days=self.retention.value(),
            auto_department_rooms=self.auto_dept.isChecked(), auto_section_rooms=self.auto_sect.isChecked(),
            auto_all_room=self.auto_all.isChecked(), min_password_length=self.pw_len.value(),
            password_require_mix=self.pw_mix.isChecked(), password_max_age_days=self.pw_age.value(),
            backup_enabled=self.bk_enabled.isChecked(), backup_dir=self.bk_dir.text().strip(),
            backup_hour=self.bk_hour.value(), backup_keep=self.bk_keep.value(),
            admin_review_enabled=self.review.isChecked(), unclaimed_file_days=self.unclaimed.value(),
            api_enabled=self.api_enabled.isChecked(), api_port=self.api_port.value(),
            api_key=self.api_key.text().strip(), api_bot_name=self.api_bot.text().strip() or "Pipeline Bot")
        if values["api_enabled"] and not values["api_key"]:
            QMessageBox.warning(self, "Pipeline API", "Create an API key first (\"New key\").")
            return
        restart = any(values[k] != cfg.get(k) for k in ("tcp_port", "discovery_port", "storage_dir",
                                                        "api_enabled", "api_port"))
        try:
            self.cfg = self.win.api.update_config(**values)
        except (ValueError, ConnectionError) as e:
            QMessageBox.warning(self, "Settings", str(e))
            return
        if restart and self.win.api.running and not self.win.api.remote and QMessageBox.question(
                self, "Restart server", "Restart the server now to apply the changes? "
                "Connected clients will reconnect automatically.") == QMessageBox.Yes:
            self.win.restart_server()
        elif restart and self.win.api.remote:
            QMessageBox.information(self, "Saved", "Settings saved. Restart the server service to apply the "
                                    "port/storage changes.")
        else:
            QMessageBox.information(self, "Saved", "Settings saved.")


class AuditPage(Page):
    def __init__(self, win):
        super().__init__("Audit log", "Every administrative action: who did what, and when. "
                                      "Sign-in lockouts and backup failures are recorded too.")
        self.win = win
        bar = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Filter by person, action or detail, then press Enter...")
        self.search.returnPressed.connect(self.refresh)
        export = btn("Export CSV", "upload")
        export.clicked.connect(self.export)
        bar.addWidget(self.search, 1)
        bar.addWidget(export)
        self.lay.addLayout(bar)
        self.table = make_table(["When", "Who", "Action", "Target", "Details"])
        self.lay.addWidget(self.table, 1)
        self.rows = []

    def refresh(self):
        if not self.win.api.running:
            return
        self.rows = self.win.api.call("admin_audit", self.search.text().strip(), 2000)
        self.table.setRowCount(len(self.rows))
        for r, e in enumerate(self.rows):
            danger = any(w in e["action"] for w in ("deleted", "disabled", "locked", "failed", "review"))
            self.table.setItem(r, 0, cell(fmt_time(e["ts"])))
            self.table.setItem(r, 1, cell(e["actor"]))
            self.table.setItem(r, 2, cell(e["action"], color=T.DANGER if danger else None))
            self.table.setItem(r, 3, cell(e["target"]))
            self.table.setItem(r, 4, cell(e["details"]))

    def export(self):
        path, _ = QFileDialog.getSaveFileName(self, "Export audit log", "audit_log.csv", "CSV files (*.csv)")
        if path:
            with open(path, "w", newline="", encoding="utf-8-sig") as f:
                w = csv.writer(f)
                w.writerow(["time", "who", "action", "target", "details"])
                for e in self.rows:
                    w.writerow([fmt_time(e["ts"]), e["actor"], e["action"], e["target"], e["details"]])


class LogPage(Page):
    def __init__(self, win):
        super().__init__("Server log")
        self.win = win
        self.view = QPlainTextEdit()
        self.view.setReadOnly(True)
        self.view.setMaximumBlockCount(5000)
        self.view.setFont(QFont("Consolas", 9))
        self.lay.addWidget(self.view, 1)

    def append(self, line):
        self.view.appendPlainText(line)

    def refresh(self):
        if self.win.api.remote and self.win.api.running:     # remote server: fetch its log file
            try:
                lines = self.win.api.call("admin_log_tail", 500)
            except (ValueError, ConnectionError):
                return
            self.view.setPlainText("".join(lines))
            self.view.verticalScrollBar().setValue(self.view.verticalScrollBar().maximum())


# ============================================================ main window
class ConsoleLoginDialog(QDialog):
    """Sign in to a server that is already running (service or another PC)."""

    def __init__(self, host="127.0.0.1", port=5150, note=""):
        super().__init__()
        self.setWindowTitle("LAN Messenger Server console")
        self.setWindowIcon(ServerWindow._app_icon())
        self.setMinimumWidth(420)
        self.api = None
        form = QFormLayout(self)
        form.setSpacing(10)
        head = QLabel(f"<b style='font-size:12pt'>Connect to the server</b><br>"
                      f"<span style='color:{T.MUTED}'>{note or 'Sign in with an administrator account.'}</span>")
        head.setWordWrap(True)
        form.addRow(head)
        self.host = QLineEdit(host)
        self.port = QSpinBox()
        self.port.setRange(1, 65535)
        self.port.setValue(int(port))
        self.user = QLineEdit("admin")
        self.pw = QLineEdit()
        self.pw.setEchoMode(QLineEdit.Password)
        form.addRow("Server", self.host)
        form.addRow("Port", self.port)
        form.addRow("Admin username", self.user)
        form.addRow("Password", self.pw)
        self.error = QLabel()
        self.error.setWordWrap(True)
        self.error.setStyleSheet(f"color: {T.DANGER};")
        form.addRow(self.error)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("Connect")
        T.polish(bb.button(QDialogButtonBox.Ok), primary=True)
        bb.accepted.connect(self.try_connect)
        bb.rejected.connect(self.reject)
        form.addRow(bb)
        self.pw.setFocus()

    def showEvent(self, e):
        super().showEvent(e)
        T.dark_title_bar(self)

    def try_connect(self):
        from server.console_api import RemoteApi
        api = RemoteApi(self.host.text().strip() or "127.0.0.1", self.port.value())
        self.error.setText("Connecting...")
        QApplication.processEvents()
        err = api.connect(self.user.text().strip(), self.pw.text())
        if err:
            self.error.setText(err)
            return
        if api.must_change and not change_password_dialog(self, api):
            api.close()
            self.error.setText("You must choose a new password before using the console.")
            return
        self.api = api
        self.accept()


def change_password_dialog(parent, api) -> bool:
    """Forced password change for a console admin (remote mode). True when changed."""
    dlg = QDialog(parent)
    dlg.setWindowTitle("Choose a new password")
    form = QFormLayout(dlg)
    msg = QLabel(api.must_change)
    msg.setWordWrap(True)
    form.addRow(msg)
    old, new, new2 = QLineEdit(), QLineEdit(), QLineEdit()
    for e in (old, new, new2):
        e.setEchoMode(QLineEdit.Password)
    form.addRow("Current password", old)
    form.addRow("New password", new)
    form.addRow("Repeat new password", new2)
    bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
    T.polish(bb.button(QDialogButtonBox.Ok), primary=True)
    bb.accepted.connect(dlg.accept)
    bb.rejected.connect(dlg.reject)
    form.addRow(bb)
    while dlg.exec():
        if new.text() != new2.text():
            QMessageBox.warning(parent, "New password", "The new passwords do not match.")
            continue
        try:
            api.change_password(old.text(), new.text())
            return True
        except (ValueError, ConnectionError) as e:
            QMessageBox.warning(parent, "New password", str(e))
    return False


class ServerWindow(QMainWindow):
    """The console. `api` is a LocalApi (this process runs the server) or a RemoteApi."""

    def __init__(self, api, start_minimized=False):
        super().__init__()
        self.api = api
        self.core = getattr(api, "core", None)          # only in local mode
        self.quitting = False
        self.setWindowTitle("LAN Messenger Server" + (f" — {api.label}" if api.remote else ""))
        self.setWindowIcon(self._app_icon())
        self.resize(1140, 740)
        self.setMinimumSize(920, 580)

        root = QWidget()
        root.setObjectName("root")
        self.setCentralWidget(root)
        lay = QHBoxLayout(root)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        # left navigation
        nav = QFrame()
        nav.setFixedWidth(220)
        nav.setObjectName("nav")
        nav.setStyleSheet(f"#nav {{ background: {T.PANEL}; border-right: 1px solid {T.BORDER}; }}")
        nl = QVBoxLayout(nav)
        nl.setContentsMargins(14, 18, 14, 14)
        nl.setSpacing(4)
        brand_row = QHBoxLayout()
        brand_row.setSpacing(10)
        from common.icons import logo_widget
        brand_row.addWidget(logo_widget(40))
        brand = QLabel(f"<span style='color:{T.ACCENT}; font-size:13pt; font-weight:800'>LAN</span>"
                       f"<span style='font-size:13pt; font-weight:800'> Messenger</span><br>"
                       f"<span style='color:{T.MUTED}'>Server console</span>")
        brand_row.addWidget(brand, 1)
        nl.addLayout(brand_row)
        nl.addSpacing(14)

        self.stack = QStackedWidget()
        self.users_page = UsersPage(self)
        self.log_page = LogPage(self)
        self.pages = [
            ("Dashboard", "dashboard", DashboardPage(self)),
            ("Users", "users", self.users_page),
            ("Designations", "badge", RolesPage(self)),
            ("Org chart", "org", OrgPage(self)),
            ("Rooms", "hash", RoomsPage(self)),
            ("Online now", "signal", OnlinePage(self)),
            ("Announcement", "megaphone", AnnouncePage(self)),
            ("Reports", "chart", ReportsPage(self)),
            ("Chat review", "search", ReviewPage(self)),
            ("Audit log", "list", AuditPage(self)),
            ("Client updates", "download", UpdatesPage(self)),
            ("Settings", "settings", SettingsPage(self)),
            ("Server log", "file", self.log_page),
        ]
        self.nav_group = QButtonGroup(self)
        for i, (title, ic, page) in enumerate(self.pages):
            b = QPushButton("  " + title)
            b.setIcon(icon(ic, T.MUTED, 18, active_color=T.ACCENT))
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.setStyleSheet(f"""
                QPushButton {{ text-align: left; background: transparent; color: {T.MUTED}; border: none;
                               padding: 9px 12px; border-radius: 10px; font-weight: 600; }}
                QPushButton:hover {{ background: {T.SURFACE}; color: {T.TEXT}; }}
                QPushButton:checked {{ background: {T.ACCENT_SOFT}; color: {T.TEXT}; }}""")
            self.nav_group.addButton(b, i)
            nl.addWidget(b)
            self.stack.addWidget(page)
        self.nav_group.idClicked.connect(self.show_page)
        nl.addStretch(1)

        self.state_label = QLabel()
        self.state_label.setWordWrap(True)
        nl.addWidget(self.state_label)
        self.toggle_btn = btn("Stop server", "power")
        self.toggle_btn.clicked.connect(self.toggle_server)
        nl.addWidget(self.toggle_btn)
        self.toggle_btn.setVisible(not api.remote)

        lay.addWidget(nav)
        lay.addWidget(self.stack, 1)

        if not api.remote:
            # logging -> log page; core events -> refresh (queued from the server thread)
            self.log_bridge = _LogBridge()
            self.log_bridge.line.connect(self.log_page.append)
            logging.getLogger().addHandler(self.log_bridge)
            self.events = _CoreEvents()
            self.events.changed.connect(self._on_core_event)
            self.core.listeners.append(self.events.changed.emit)
        else:
            self.keepalive = QTimer(self, interval=30000, timeout=self._keepalive)
            self.keepalive.start()

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh_current)
        self.timer.start(5000)

        self._make_tray()
        self.nav_group.button(0).setChecked(True)
        self.update_state()
        self.show_page(0)
        if not start_minimized:
            self.show()
            T.dark_title_bar(self)

    @staticmethod
    def _app_icon():
        from PySide6.QtGui import QIcon
        return QIcon(asset("app.ico"))

    def _make_tray(self):
        self.tray = QSystemTrayIcon(self._app_icon(), self)
        self.tray.setToolTip("LAN Messenger Server" + (" console" if self.api.remote else ""))
        m = QMenu()
        m.addAction("Open console", self.show_normal)
        m.addSeparator()
        m.addAction("Close console" if self.api.remote else "Quit server", self.quit)
        self.tray.setContextMenu(m)
        self.tray.activated.connect(lambda reason: self.show_normal()
                                    if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick) else None)
        if not self.api.remote:
            self.tray.show()

    def show_normal(self):
        self.show()
        self.setWindowState(self.windowState() & ~Qt.WindowMinimized)
        self.raise_()
        self.activateWindow()
        T.dark_title_bar(self)

    def show_page(self, i):
        self.stack.setCurrentIndex(i)
        self.refresh_current()

    def refresh_current(self):
        if self.isVisible():
            try:
                self.stack.currentWidget().refresh()
            except RuntimeError:
                pass   # server stopped between check and call
            except ConnectionError as e:
                self._connection_lost(str(e))
            except ValueError as e:
                QMessageBox.warning(self, "Server", str(e))
        self.update_state()

    def _on_core_event(self, event):
        page = self.stack.currentWidget()
        if isinstance(page, (OnlinePage, DashboardPage, UsersPage)):
            self.refresh_current()

    def _keepalive(self):
        if self.api.running and not self.api.ping():
            self._connection_lost("no answer")

    def _connection_lost(self, reason):
        self.update_state()
        self.timer.stop()
        dlg = ConsoleLoginDialog(self.api.host, self.api.port,
                                 f"The connection to the server was lost ({reason}). Sign in again.")
        dlg.user.setText(self.api.username or "admin")
        if dlg.exec():
            self.api = dlg.api
            self.timer.start(5000)
            self.refresh_current()
        else:
            self.quitting = True
            QApplication.quit()

    def update_state(self):
        if self.api.remote:
            if self.api.running:
                self.state_label.setText(f"<span style='color:{T.ACCENT}'>● Connected</span><br>"
                                         f"<span style='color:{T.MUTED}'>{self.api.label}<br>"
                                         f"as {self.api.username}</span>")
            else:
                self.state_label.setText(f"<span style='color:{T.DANGER}'>● Not connected</span>")
            return
        if self.core.running:
            self.state_label.setText(f"<span style='color:{T.ACCENT}'>● Running</span><br>"
                                     f"<span style='color:{T.MUTED}'>{', '.join(local_ips())}"
                                     f" : {self.core.config['tcp_port']}</span>")
            self.toggle_btn.setText("Stop server")
        else:
            self.state_label.setText(f"<span style='color:{T.DANGER}'>● Stopped</span>")
            self.toggle_btn.setText("Start server")

    def toggle_server(self):
        if self.core.running:
            if QMessageBox.question(self, "Stop server", "Stop the server? All users will be "
                                    "disconnected.") != QMessageBox.Yes:
                return
            self.core.stop()
        else:
            self.start_server()
        self.update_state()
        self.refresh_current()

    def start_server(self):
        try:
            self.core.start()
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "Server could not start", startup_error_text(e, self.core.config))
        self.update_state()

    def restart_server(self):
        self.core.stop()
        self.start_server()
        self.refresh_current()

    def closeEvent(self, event):
        if self.quitting or self.api.remote:
            event.accept()
            self.api.close()
            QApplication.quit()
            return
        event.ignore()
        self.hide()
        self.tray.showMessage("LAN Messenger Server", "The server keeps running in the background. "
                              "Right-click the tray icon to quit.", QSystemTrayIcon.Information, 3000)

    def quit(self):
        if self.api.remote:
            self.close()
            return
        if QMessageBox.question(self, "Quit server", "Stop the server and quit? All users will be "
                                "disconnected.") != QMessageBox.Yes:
            return
        self.quitting = True
        self.core.stop()
        self.tray.hide()
        QApplication.quit()
