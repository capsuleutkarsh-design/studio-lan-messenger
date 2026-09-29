"""Server console: start/stop the server and administer users, rooms, etc."""

import csv
import datetime
import functools
import logging
import os
import re
import socket
import time

from PySide6.QtCore import QDir, QObject, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QFont, QIcon
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QButtonGroup, QCheckBox, QComboBox, QDialog,
    QDialogButtonBox, QFileDialog, QFormLayout, QFrame, QGridLayout, QHBoxLayout,
    QHeaderView, QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow,
    QMenu, QMessageBox, QPlainTextEdit, QPushButton, QScrollArea, QSpinBox, QStackedWidget,
    QSystemTrayIcon, QTableWidget, QTableWidgetItem, QTabWidget, QTreeWidget, QTreeWidgetItem, QVBoxLayout,
    QWidget,
)

from common import theme as T
from common.fmt import ELLIPSIS, SEP, fmt_date, fmt_when
from common.icons import add_show_password, asset, icon, pixmap
from common import protocol as P
from common.protocol import human_size
from server.core import ServerCore, is_console_account, local_ips, startup_error_text


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


def console_style():
    """Minimal, soft look for the console: borderless rounded cards, airy tables, quiet headers."""
    hair = T.mix(T.BORDER, T.PANEL, 0.45)
    return f"""
QTableWidget, QTreeWidget, QListWidget {{ background: {T.PANEL}; border: none; border-radius: 18px; padding: 6px 8px;
    alternate-background-color: {T.PANEL}; }}
QTableWidget::item {{ padding: 0 10px; border: none; }}
QTreeWidget::item {{ padding: 8px 10px; border: none; }}
QTreeWidget::indicator, QTableWidget::indicator, QListWidget::indicator {{ width: 16px; height: 16px;
    border-radius: 5px; background: {T.SURFACE}; border: 1px solid {T.CONTROL_EDGE}; }}
QTreeWidget::indicator:checked, QTableWidget::indicator:checked, QListWidget::indicator:checked {{
    background: {T.ACCENT}; border: 1px solid {T.ACCENT}; image: url("{T.check_image()}"); }}
QTableWidget::item:selected, QTreeWidget::item:selected, QListWidget::item:selected {{
    background: {T.ACCENT_SOFT}; color: {T.TEXT}; border-radius: 10px; }}
QHeaderView {{ background: {T.PANEL}; border: none; }}
QHeaderView::section {{ background: {T.PANEL}; border: none; border-bottom: 1px solid {hair}; padding: 12px 10px 10px 10px;
    color: {T.MUTED}; font-size: 8.5pt; font-weight: 600; }}
QPushButton {{ border-radius: 12px; border: 1px solid {hair}; }}
QPushButton[primary="true"] {{ border: none; }}
QPushButton[danger="true"]:disabled {{ color: {T.FAINT}; }}
QLineEdit, QSpinBox, QComboBox, QPlainTextEdit, QTextEdit, QDateTimeEdit {{ border-radius: 12px; border: 1px solid {hair}; }}
QTabWidget::pane {{ border: none; }}
QTabBar::tab {{ background: transparent; border: none; padding: 8px 14px; color: {T.MUTED}; font-weight: 600; }}
QTabBar::tab:selected {{ color: {T.TEXT}; border-bottom: 2px solid {T.ACCENT}; }}
"""


def btn(text, icon_name=None, primary=False, danger=False):
    b = QPushButton(text)
    if icon_name:
        ic = icon(icon_name, T.ACCENT_TEXT if primary else (T.DANGER if danger else T.TEXT), 16)
        ic.addPixmap(pixmap(icon_name, T.FAINT, 16), QIcon.Disabled, QIcon.Off)    # unavailable, not broken
        b.setIcon(ic)
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
    t.setAlternatingRowColors(False)
    t.setShowGrid(False)
    t.setFocusPolicy(Qt.NoFocus)
    t.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
    t.horizontalHeader().setStretchLastSection(True)
    t.horizontalHeader().setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
    t.horizontalHeader().setHighlightSections(False)
    t.verticalHeader().setDefaultSectionSize(44)
    t.setWordWrap(False)                     # one line per row: long text ends in '…' (the tooltip has it all)
    t.setTextElideMode(Qt.ElideRight)
    return t


def _row_key(table, row):
    it = table.item(row, 0)
    if it is None:
        return None
    data = it.data(Qt.UserRole)
    return data if data is not None else it.text()


def _begin_fill(table):
    """Before a page rebuilds a table: remember the selected row and the scroll position, and stop the columns
    from re-measuring themselves after every cell (with 'resize to contents' that made a refresh take
    rows x cells work - seconds for a studio, every few seconds)."""
    rows = table.selectionModel().selectedRows()
    header = table.horizontalHeader()
    state = (_row_key(table, rows[0].row()) if rows else None, table.verticalScrollBar().value(),
             [header.sectionResizeMode(i) for i in range(header.count())], table.isSortingEnabled())
    table.setUpdatesEnabled(False)
    table.setSortingEnabled(False)
    header.setSectionResizeMode(QHeaderView.Interactive)
    return state


def _end_fill(table, state):
    key, scroll, modes, sorting = state
    header = table.horizontalHeader()
    for i, mode in enumerate(modes[:header.count()]):
        header.setSectionResizeMode(i, mode)                # measured once, now
    table.setSortingEnabled(sorting)
    if key is not None:
        rows = table.selectionModel().selectedRows()
        if not rows or _row_key(table, rows[0].row()) != key:
            for r in range(table.rowCount()):
                if _row_key(table, r) == key:
                    table.selectRow(r)
                    break
    table.verticalScrollBar().setValue(scroll)
    table.setUpdatesEnabled(True)


def keeps_tables(refresh):
    """Wraps a page's refresh: its tables fill in one go and keep the admin's place (selection, scroll)."""
    @functools.wraps(refresh)
    def wrapper(self, *args, **kwargs):
        states = []
        for table in self.findChildren(QTableWidget):
            try:
                states.append((table, _begin_fill(table)))
            except RuntimeError:
                pass
        try:
            return refresh(self, *args, **kwargs)
        finally:
            for table, state in states:
                try:
                    _end_fill(table, state)
                except RuntimeError:        # the table was deleted meanwhile
                    pass
    return wrapper


NUM = Qt.AlignRight | Qt.AlignVCenter          # counts and sizes: right-aligned, so the digits line up


def cell(text, data=None, color=None, align=None, tip=None):
    it = QTableWidgetItem(str(text))
    if data is not None:
        it.setData(Qt.UserRole, data)
    if color:
        it.setForeground(QColor(color))
    if align is not None:
        it.setTextAlignment(align)
    if tip is None and len(str(text)) > 48:        # may be cut to fit the column: the whole text on hover
        tip = str(text)
    if tip:
        it.setToolTip(tip)
    return it


def num(value, data=None, color=None):
    """A count, right-aligned: '1,204'."""
    return cell(f"{value:,}" if isinstance(value, (int, float)) else value, data, color, NUM)


def size_cell(n):
    """A size, right-aligned: '85.1 KB'. Nothing at all is a faint dash, not '0 B'."""
    return cell(human_size(n) if n else "—", color=None if n else T.FAINT, align=NUM)


def align_columns(table, columns, align=NUM):
    """Headers of number columns sit over their numbers."""
    for c in columns:
        item = table.horizontalHeaderItem(c)
        if item:
            item.setTextAlignment(align)


def numbers_last(table):
    """A table whose last column is a number: no stretching, so that number stays next to the others instead of
    at the far right edge."""
    table.horizontalHeader().setStretchLastSection(False)


def hide_empty_columns(table, columns):
    """Optional columns that nobody filled in (Section, PC) take no room and don't look like missing data."""
    for c in columns:
        table.setColumnHidden(c, not any(table.item(r, c) and table.item(r, c).text().strip()
                                         for r in range(table.rowCount())))


def bind_selection(view, buttons, then=None):
    """Buttons that act on the selected row are enabled only while a row is selected (they used to look
    clickable and do nothing). `then` runs too, e.g. to relabel a button for the selected row."""
    def sync():
        try:
            chosen = bool(view.selectedItems())
        except RuntimeError:                       # the page is being deleted
            return
        for b in buttons:
            b.setEnabled(chosen)
        if then:
            then()
    view.itemSelectionChanged.connect(sync)
    sync()
    return sync


def confirm(parent, title, text, action, danger=True, detail=""):
    """Ask before a risky action. The button says what happens ('Delete', 'Disable') and Cancel is the default,
    so a stray Enter never confirms something that can't be undone. Plain text: names can't format it."""
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Warning if danger else QMessageBox.Question)
    box.setWindowTitle(title)
    box.setTextFormat(Qt.PlainText)
    box.setText(text)
    if detail:
        box.setInformativeText(detail)
    yes = box.addButton(action, QMessageBox.AcceptRole)
    cancel = box.addButton(QMessageBox.Cancel)
    T.polish(yes, danger=danger, primary=not danger)
    box.setDefaultButton(cancel)
    box.setEscapeButton(cancel)
    box.exec()
    return box.clickedButton() is yes


def ask_text(parent, title, label, text="", ok="Save"):
    """A one-line question whose main button says what it does, in the main-button style."""
    dlg = QInputDialog(parent)
    dlg.setWindowTitle(title)
    dlg.setInputMode(QInputDialog.TextInput)
    dlg.setLabelText(label)
    dlg.setTextValue(text)
    dlg.setOkButtonText(ok)
    for box in dlg.findChildren(QDialogButtonBox):
        if box.button(QDialogButtonBox.Ok):
            T.polish(box.button(QDialogButtonBox.Ok), primary=True)
    if not dlg.exec():
        return "", False
    return dlg.textValue(), True


def hint_label(text):
    """A small readable note under a form field."""
    lbl = QLabel(text)
    lbl.setWordWrap(True)
    lbl.setStyleSheet(f"color: {T.META}; font-size: {T.pt(T.FONT_S)};")
    return lbl


def add_row_with_hint(form, label, field, text):
    """A form row with a small note right under its field (as a row of its own, the note sat as far from its
    field as from the next one). Returns the note."""
    box = QWidget()
    v = QVBoxLayout(box)
    v.setContentsMargins(0, 0, 0, 0)
    v.setSpacing(3)
    v.addWidget(field)
    hint = hint_label(text)
    v.addWidget(hint)
    v.addStretch(1)
    side = QWidget()                            # the label stays level with the field, not with field + note
    s = QVBoxLayout(side)
    s.setContentsMargins(0, 0, 0, 0)
    lbl = QLabel(label)
    lbl.setFixedHeight(max(20, field.sizeHint().height()))
    lbl.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
    s.addWidget(lbl)
    s.addStretch(1)
    form.addRow(side, box)
    return hint


def on_server_pc(api):
    """True when the console runs on the server PC itself (its folders are this PC's folders)."""
    return not api.remote or getattr(api, "host", "").lower() in (
        "127.0.0.1", "localhost", "::1", socket.gethostname().lower())


def version_text(v):
    """A Quillo version for people: '1.11.1', 'older than 1.6.2' (those didn't say), 'no version seen yet'."""
    if not v:
        return "no version seen yet"
    if str(v).startswith("older"):
        return "older than 1.6.2"
    return str(v)


def uptime_text(seconds):
    """'up 5 min', 'up 3 h 20 min', 'up 2 days 4 h'."""
    mins = max(0, int(seconds // 60))
    if mins < 60:
        return f"up {mins} min"
    if mins < 1440:
        h, m = divmod(mins, 60)
        return f"up {h} h" + (f" {m} min" if m else "")
    days, h = mins // 1440, mins // 60 % 24
    return f"up {days} day{'s' if days != 1 else ''}" + (f" {h} h" if h else "")


def safe_text(text):
    """Names typed by users, for message boxes (which would otherwise render '<b>...' as formatting)."""
    import html
    return "<qt>" + html.escape(str(text), quote=False).replace("\n", "<br>") + "</qt>"


def keep_files_text(days, default_days):
    """'Server default (3 days)', 'Forever', '30 days'."""
    def n(d):
        return "forever" if not d else f"{d:g} day{'s' if d != 1 else ''}"
    if days is None:
        return f"Server default ({n(default_days)})"
    return n(days).capitalize()


def fmt_time(ts):
    """'Today 16:05', 'Tue 29 Sep, 16:05', '3 Mar 2025, 16:05': a moment, written like everywhere in Quillo."""
    if not ts:
        return "Never"
    return fmt_when(ts)


def iso_time(ts):
    """'2026-09-29 16:05' for CSV files (a spreadsheet sorts it)."""
    return datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M") if ts else ""


class Page(QWidget):
    # pages that show live numbers refresh every few seconds; the others when opened, when something changes,
    # and once a minute
    LIVE = False

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        if "refresh" in cls.__dict__:
            cls.refresh = keeps_tables(cls.__dict__["refresh"])

    def __init__(self, title, subtitle="", scroll=False):
        super().__init__()
        self.lay = QVBoxLayout(self)
        self.scroll_area = None
        if scroll:
            # the whole page scrolls on a short screen (a laptop, a half-height window) instead of squeezing
            self.lay.setContentsMargins(0, 0, 0, 0)
            area = self.scroll_area = QScrollArea()
            area.setWidgetResizable(True)
            area.setFrameShape(QFrame.NoFrame)
            area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            body = QWidget()
            T.bg_pane(body)
            area.setWidget(body)
            T.bg_pane(area.viewport())
            self.lay.addWidget(area)
            self.lay = QVBoxLayout(body)
        self.lay.setContentsMargins(40, 32, 40, 28)
        self.lay.setSpacing(16)
        head = QLabel(title)
        head.setStyleSheet("font-size: 19pt; font-weight: 600;")
        self.lay.addWidget(head)
        if subtitle:
            sub = QLabel(subtitle)
            sub.setStyleSheet(f"color: {T.MUTED}; font-size: 9.5pt;")
            sub.setWordWrap(True)
            self.lay.addWidget(sub)
            self.lay.addSpacing(4)

    def refresh(self):
        pass

    def showEvent(self, e):
        super().showEvent(e)
        T.tidy_forms(self)


# ============================================================== dashboard
class StatCard(QFrame):
    def __init__(self, title, icon_name):
        super().__init__()
        self.setObjectName("stat")
        self.setStyleSheet(f"#stat {{ background: {T.PANEL}; border-radius: 18px; }}")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 18, 22, 18)
        lay.setSpacing(6)
        top = QHBoxLayout()
        cap = QLabel(title)
        cap.setStyleSheet(f"color: {T.MUTED}; font-size: 9pt; background: transparent;")
        top.addWidget(cap, 1)
        ic = QLabel()
        ic.setFixedSize(30, 30)
        ic.setAlignment(Qt.AlignCenter)
        ic.setStyleSheet(f"background: {T.ACCENT_SOFT}; border-radius: 15px;")
        ic.setPixmap(icon(icon_name, T.ACCENT, 16).pixmap(16, 16))
        top.addWidget(ic)
        lay.addLayout(top)
        self.value = QLabel("-")
        self.value.setStyleSheet("font-size: 22pt; font-weight: 600; background: transparent;")
        lay.addWidget(self.value)


class DashboardPage(Page):
    LIVE = True

    def __init__(self, win):
        super().__init__("Dashboard", "Install this server on one always-on PC. Clients on the LAN "
                                      "find it automatically, or can connect to one of the addresses below.",
                         scroll=True)
        self.win = win
        grid = QGridLayout()
        grid.setSpacing(16)
        self.cards = {}
        for i, (key, title, ic) in enumerate([
                ("online", "Users online", "users"), ("users", "Accounts", "user"),
                ("rooms", "Chat rooms", "hash"), ("messages", "Messages stored", "chat"),
                ("files", "Files stored", "file"), ("files_bytes", "Storage used", "hdd")]):
            card = StatCard(title, ic)
            self.cards[key] = card
            grid.addWidget(card, i // 3, i % 3)
        self.lay.addLayout(grid)

        self.lay.addSpacing(4)
        self.warnings = QLabel()
        self.warnings.setWordWrap(True)
        self.warnings.setTextFormat(Qt.RichText)            # the ⚠ and line breaks, not "&#9888;&nbsp;"
        self.warnings.setStyleSheet(f"background: {T.WARN_BG}; color: {T.WARN_TEXT};"
                                    f" border: 1px solid {T.mix(T.WARN_TEXT, T.WARN_BG, 0.25)};"
                                    f" border-radius: 14px; padding: 12px 16px;")
        self.warnings.hide()
        self.lay.addWidget(self.warnings)
        box = QFrame()
        box.setObjectName("details")
        box.setStyleSheet(f"#details {{ background: {T.PANEL}; border-radius: 18px; }}")
        bl = QVBoxLayout(box)
        bl.setContentsMargins(24, 20, 24, 20)
        bl.setSpacing(14)
        self.headline = QLabel()
        self.headline.setStyleSheet("background: transparent;")
        bl.addWidget(self.headline)
        self.details = QGridLayout()
        self.details.setHorizontalSpacing(28)
        self.details.setVerticalSpacing(10)
        self.details.setColumnStretch(1, 1)
        bl.addLayout(self.details)
        self.info = box                       # kept for callers that look for it
        self.lay.addWidget(box)
        self.lay.addStretch(1)

    def refresh(self):
        api = self.win.api
        info = api.info()
        running = api.running
        state = (f"<span style='color:{T.ACCENT}'>&#9679; Running</span>" if running
                 else f"<span style='color:{T.DANGER}'>&#9679; Stopped</span>")
        uptime = ""
        if running and info.get("started_at"):
            uptime = f" &nbsp;·&nbsp; {uptime_text(time.time() - info['started_at'])}"
        self._show_details(info, running, state, uptime)
        if not running:
            for c in self.cards.values():
                c.value.setText("–")
            return
        stats = api.call("admin_stats")
        for key, card in self.cards.items():
            v = stats.get(key, 0)
            card.value.setText(human_size(v) if key == "files_bytes" else f"{v:,}")

    def _show_details(self, info, running, state, uptime, _backup=None):
        import html
        esc = lambda t: html.escape(str(t or ""), quote=False)                       # noqa: E731
        muted = lambda t: f"<span style='color:{T.META}'>{t}</span>"                  # noqa: E731
        failed = lambda t: f"<span style='color:{T.DANGER}'>{t}</span>"               # noqa: E731
        ok = f"<span style='color:{T.ACCENT}'>OK</span> &nbsp;"
        nightly = f"every night at {int(info.get('backup_hour', 2)):02d}:00"
        stopped = muted("Not while the server is stopped")
        self.headline.setText(f"<span style='font-size:12.5pt; font-weight:600'>{esc(info['server_name'])}</span>"
                              f" &nbsp; {state}<span style='color:{T.META}'>{uptime} &nbsp;·&nbsp; "
                              f"version {info.get('version', '')}</span>")
        warn = []
        if running and info.get("discovery_error"):
            warn.append(f"Automatic discovery is off: {esc(info['discovery_error'])}. Clients must type this "
                        "server's address, or change the discovery port in Settings.")
        if running and info.get("storage_error"):
            warn.append(f"{esc(info['storage_error'])}. Chat works; file uploads are refused until the folder is "
                        "reachable (check the share, or change it in Settings).")
        b = info.get("last_backup")
        if b and b.get("ok"):
            backup = f"{ok}{fmt_time(b['time'])} &nbsp;·&nbsp; {human_size(b.get('size', 0))}"
        elif b:
            backup = failed(f"Failed {fmt_time(b['time'])} — {esc(b.get('error', ''))}")
        elif not running:
            backup = stopped
        elif info.get("backup_enabled", True):
            backup = muted(f"None since the server started — the next one runs {nightly}")
        else:
            backup = muted("Off — turn it on in Settings &gt; Backups")
        cb = info.get("last_chat_backup")
        if cb and cb.get("ok"):
            chat_backup = f"{ok}{fmt_time(cb['time'])} &nbsp;·&nbsp; {cb['messages']:,} new messages"
        elif cb:
            chat_backup = failed(f"Failed — {esc(cb.get('error', ''))}")
        elif not running:
            chat_backup = stopped
        elif info.get("chat_log_enabled", True):
            chat_backup = muted(f"Not yet — it runs {nightly}")
        else:
            chat_backup = muted("Off — turn it on in Settings &gt; Chat backup &amp; history")
        sc = info.get("last_safe_copy")
        if running and not info.get("safe_copy_dir"):
            warn.append("There is no central folder: chats, backups and the user list are only on this PC. If this "
                        "PC fails, they are lost. Choose a folder on the file server in Settings &gt; "
                        "Central folder.")
        ul = info.get("last_user_list") or {}
        if ul.get("ok"):
            user_list = f"{ok}{fmt_time(ul['time'])} &nbsp;·&nbsp; {ul.get('people', 0):,} people"
        elif ul:
            user_list = failed(f"Failed — {esc(ul.get('error', ''))}")
        else:
            user_list = stopped if not running else muted("Not yet — it is written within a few minutes")
        if not info.get("safe_copy_dir"):
            safe = muted("Off — choose a central folder in Settings")
        elif not sc:
            safe = muted("Within 5 minutes" if running else "Not while the server is stopped")
        elif sc.get("ok"):
            safe = f"{ok}{fmt_time(sc['time'])}"
        else:
            safe = failed(f"Failed {fmt_time(sc['time'])} — {esc(sc.get('error', ''))}")
        if running and sc and not sc.get("ok"):
            warn.append(f"The safe copy in {esc(sc.get('folder', ''))} failed ({esc(sc.get('error', ''))}). Chat "
                        "works; check the share — without it, a reinstall could not bring the data back.")
        self.warnings.setText("<br>".join(f"&#9888;&nbsp; {w}" for w in warn))
        self.warnings.setVisible(bool(warn))
        encryption = (f"<span style='color:{T.ACCENT}'>On</span>" if info.get("tls") else
                      failed("Off") if running else stopped)
        central = (esc(info["safe_copy_dir"]) if info.get("safe_copy_dir") else
                   muted("Not set — choose one in Settings &gt; Central folder"))
        rows = [("Address", f"<b>{', '.join(info['ips'])}</b>"),
                ("Ports", f"{info['tcp_port']} chat &amp; files &nbsp;·&nbsp; {info['discovery_port']} discovery"),
                ("Encryption", encryption), ("Last backup", backup), ("Last chat backup", chat_backup),
                ("Safe copy", safe), ("User list", user_list), ("Central folder", central),
                ("Data folder", esc(info["data_dir"])), ("File storage", esc(info["storage_dir"]))]
        if info.get("fingerprint"):
            rows.append(("Fingerprint", f"<span style='font-family:Consolas; font-size:8pt; color:{T.META}'>"
                                        f"{info['fingerprint']}</span>"))
        while self.details.count():
            w = self.details.takeAt(0).widget()
            if w:
                w.deleteLater()
        for r, (key, value) in enumerate(rows):
            k = QLabel(key)
            k.setStyleSheet(f"color: {T.MUTED}; background: transparent;")
            v = QLabel(value)
            v.setTextFormat(Qt.RichText)
            v.setTextInteractionFlags(Qt.TextSelectableByMouse)
            v.setWordWrap(True)
            v.setStyleSheet("background: transparent;")
            self.details.addWidget(k, r, 0, Qt.AlignTop)
            self.details.addWidget(v, r, 1)


# ================================================================== users
class UserDialog(QDialog):
    def __init__(self, parent, user=None, users=(), roles=(), depts=()):
        super().__init__(parent)
        self.setWindowTitle("Edit user" if user else "New user")
        self.setMinimumWidth(460)
        self.users = [u for u in users if not u["disabled"]]
        self.depts = list(depts)
        form = QFormLayout(self)
        form.setSpacing(10)
        self.username = QLineEdit(user["username"] if user else "")
        self.name = QLineEdit(user["display_name"] if user else "")
        # only departments/sections made on the Departments page can be chosen (no typing)
        self.department = QComboBox()
        self.department.addItem("No department", "")
        for d in sorted((d for d in self.depts if d["parent_id"] is None), key=lambda d: d["name"].lower()):
            self.department.addItem(d["name"], d["name"])
        self.section = QComboBox()
        current = user["department"] if user else ""
        if current and self._find(self.department, current) < 0:
            self.department.addItem(current, current)      # old value not on the list: show it, don't hide it
        self.department.setCurrentIndex(max(0, self._find(self.department, current)))
        self.department.currentIndexChanged.connect(lambda _i: self._fill_sections())
        self._fill_sections(user["section"] if user else "")
        self.designation = QComboBox()
        self.designation.addItem("No designation", None)
        for r in roles:
            self.designation.addItem(r["name"], r["id"])
        self.designation.setCurrentIndex(max(0, self.designation.findData(user["role_id"] if user else None)))
        self.manager = QComboBox()
        self.manager.addItem("Nobody", None)
        for u in sorted(self.users, key=lambda u: u["display_name"].lower()):
            if not user or u["id"] != user["id"]:
                extra = " · ".join(x for x in (u.get("designation"), u["department"]) if x)
                self.manager.addItem(f"{u['display_name']}" + (f"  —  {extra}" if extra else ""), u["id"])
        self.manager.setCurrentIndex(max(0, self.manager.findData(user["manager_id"] if user else None)))
        self.title = QLineEdit(user["title"] if user else "")
        self.title.setPlaceholderText("Optional, e.g. Senior Compositor")
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.Password)
        add_show_password(self.password)
        self.password.setPlaceholderText("Leave empty to keep the current one" if user else "Their first password")
        self.is_admin = QCheckBox("Administrator (full rights in the client)")
        if user:
            self.is_admin.setChecked(bool(user["is_admin"]))
        form.addRow("Username", self.username)
        form.addRow("Display name", self.name)
        add_row_with_hint(form, "Department", self.department, "Departments and sections are added on the "
                                                                "Departments page")
        form.addRow("Section", self.section)
        form.addRow("Designation", self.designation)
        form.addRow("Reports to", self.manager)
        add_row_with_hint(form, "Job title", self.title, "Shown only for people without a designation; people "
                                                         "can search for it")

        def date_text(value):                # stored '--MM-DD' / 'YYYY-MM-DD' -> shown '26-09' / '26-09-1990'
            parts = [p for p in (value or "").split("-") if p]
            return "-".join(reversed(parts))
        self.employee_id = QLineEdit(user.get("employee_id", "") if user else "")
        self.employee_id.setPlaceholderText("Optional HR / payroll code")
        self.birthday = QLineEdit(date_text(user.get("birthday", "")) if user else "")
        self.birthday.setPlaceholderText("DD-MM")
        self.joined = QLineEdit(date_text(user.get("joined_on", "")) if user else "")
        self.joined.setPlaceholderText("DD-MM-YYYY")
        form.addRow("Employee ID", self.employee_id)
        add_row_with_hint(form, "Birthday", self.birthday, "DD-MM or DD-MM-YYYY · others see only the day and month")
        add_row_with_hint(form, "Joining date", self.joined, "For work anniversaries")
        form.addRow("Password", self.password)
        form.addRow("", self.is_admin)
        bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        T.polish(bb.button(QDialogButtonBox.Save), primary=True)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        form.addRow(bb)

    @staticmethod
    def _find(combo, name):
        """Index of an entry by name, ignoring capitals (-1 if missing)."""
        return next((i for i in range(combo.count()) if (combo.itemData(i) or "").lower() == name.lower()), -1)

    def _fill_sections(self, keep=None):
        """Sections of the chosen department only."""
        keep = (self.section.currentData() or "") if keep is None else keep
        dept_name = (self.department.currentData() or "").lower()
        dept = next((d for d in self.depts if d["parent_id"] is None and d["name"].lower() == dept_name), None)
        self.section.clear()
        self.section.addItem("No section", "")
        for s in sorted((s for s in self.depts if dept and s["parent_id"] == dept["id"]),
                        key=lambda s: s["name"].lower()):
            self.section.addItem(s["name"], s["name"])
        if keep and self._find(self.section, keep) < 0 and dept_name and not dept:
            self.section.addItem(keep, keep)                # old value of an old department: still shown
        self.section.setCurrentIndex(max(0, self._find(self.section, keep)))

    def values(self):
        return {"username": self.username.text().strip(), "display_name": self.name.text().strip(),
                "department": self.department.currentData() or "",
                "section": self.section.currentData() or "",
                "role_id": self.designation.currentData(), "manager_id": self.manager.currentData(),
                "title": self.title.text().strip(), "is_admin": int(self.is_admin.isChecked()),
                "password": self.password.text(), "employee_id": self.employee_id.text().strip(),
                "birthday": self.birthday.text().strip(), "joined_on": self.joined.text().strip()}


class UsersPage(Page):
    def __init__(self, win):
        super().__init__("Users", "Create an account for every artist. People sign in to the app with "
                                  "their username and password. Departments group people in the contact list.")
        self.win = win
        bar = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Filter users" + ELLIPSIS)
        self.search.addAction(icon("search", T.FAINT, 16), QLineEdit.LeadingPosition)
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self.apply_filter)
        bar.addWidget(self.search, 1)
        add = btn("Add user", "plus", primary=True)
        add.clicked.connect(self.add_user)
        tpl = btn("Excel template", "download")
        tpl.setToolTip("An Excel file with everyone already in it, dropdowns for department, section,\n"
                       "designation and reports to — fill it in, then Import.")
        tpl.clicked.connect(self.export_excel)
        bar.addWidget(tpl)
        imp = btn("Import", "upload")
        imp.setToolTip("Import the filled Excel (or a CSV): new usernames are created, existing people updated.\n"
                       "You see what will change before anything is saved.")
        imp.clicked.connect(self.import_file)
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
        self.edit_btn = btn("Edit", "edit")
        self.reset_btn = btn("Reset password" + ELLIPSIS, "key")
        self.toggle_btn = btn("Disable" + ELLIPSIS, "power")
        self.delete_btn = btn("Delete" + ELLIPSIS, "trash", danger=True)
        for b, fn in ((self.edit_btn, self.edit_user), (self.reset_btn, self.reset_password),
                      (self.toggle_btn, self.toggle_disabled), (self.delete_btn, self.delete_user)):
            b.clicked.connect(fn)
            row.addWidget(b)
        row.addStretch(1)
        self.lay.addLayout(row)
        self.users = []
        bind_selection(self.table, (self.edit_btn, self.reset_btn, self.toggle_btn, self.delete_btn),
                       self._sync_buttons)

    def _is_me(self, u):
        """The account this console is signed in with (on another PC): it can't disable or delete itself."""
        me = (getattr(self.win.api, "username", "") or "").lower() if self.win.api.remote else ""
        return bool(u and me) and u["username"].lower() == me

    def _sync_buttons(self):
        """'Enable' or 'Disable' - whichever a click does to the selected person."""
        u = self.selected_user()
        off = bool(u and u["disabled"])
        self.toggle_btn.setText("Enable" if off else "Disable" + ELLIPSIS)
        self.toggle_btn.setIcon(icon("check" if off else "power", T.TEXT, 16))
        me = self._is_me(u)
        for b in (self.toggle_btn, self.delete_btn):
            b.setEnabled(bool(u) and not me)
            b.setToolTip("This console is signed in with this account" if me else "")

    def refresh(self):
        if not self.win.api.running:
            return
        selected = self.selected()
        self.users = self.win.api.call("admin_users")
        self.table.setRowCount(len(self.users))
        for r, u in enumerate(self.users):
            st = u["status"]
            designation = (f"{u['designation']}  (admin)" if u["designation"] else "Admin") if u["is_admin"] \
                else u["designation"]
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
            self.table.setItem(r, 7, cell("Online now" if u["sessions"] else fmt_time(u["last_seen"]),
                                          color=None if u["sessions"] or u["last_seen"] else T.META))
            if u["id"] == selected:
                self.table.selectRow(r)
        hide_empty_columns(self.table, [3])                # Section, when no department has sections
        self.apply_filter()
        self._sync_buttons()

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
        u = self.selected_user()
        if not u:
            return
        me = self._is_me(u)
        m = QMenu(self)
        m.addAction("Edit", self.edit_user)
        m.addAction("Reset password" + ELLIPSIS, self.reset_password)
        if not me:
            m.addAction("Enable" if u["disabled"] else "Disable" + ELLIPSIS, self.toggle_disabled)
        if u["sessions"] and not me:
            m.addAction("Disconnect" + ELLIPSIS, self.disconnect)
        m.addAction("Remove profile photo" + ELLIPSIS, self.remove_photo)
        if not me:
            m.addSeparator()
            m.addAction(icon("trash", T.DANGER, 16), "Delete" + ELLIPSIS, self.delete_user)
        m.exec(self.table.viewport().mapToGlobal(pos))

    def disconnect(self):
        u = self.selected_user()
        if u and confirm(self, "Disconnect", f"Disconnect {u['display_name'] or u['username']}?", "Disconnect",
                         detail="Quillo signs them out on every PC now. They can sign in again."):
            self.win.api.call("admin_kick", u["id"])
            QTimer.singleShot(300, self.refresh)

    def roles(self):
        return self.win.api.call("admin_roles")

    def add_user(self):
        dlg = UserDialog(self, users=self.users, roles=self.roles(), depts=self.win.api.call("admin_departments"))
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
        dlg = UserDialog(self, u, self.users, self.roles(), self.win.api.call("admin_departments"))
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
        dlg.setMinimumWidth(440)
        form = QFormLayout(dlg)
        form.setSpacing(10)
        pw = QLineEdit()
        pw.setEchoMode(QLineEdit.Password)
        add_show_password(pw)
        pw.setPlaceholderText("A temporary password")
        must = QCheckBox("They must choose a new password at their next sign-in")
        try:
            must.setChecked(bool(self.win.api.config().get("force_password_change", False)))
        except (ValueError, ConnectionError):
            must.setChecked(False)
        name = u["display_name"] or u["username"]
        form.addRow("New password", pw)
        form.addRow("", hint_label(f"{name} is signed out now and signs in with this password."))
        form.addRow("", must)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("Reset password")
        T.polish(bb.button(QDialogButtonBox.Ok), primary=True)
        bb.button(QDialogButtonBox.Ok).setEnabled(False)
        pw.textChanged.connect(lambda t: bb.button(QDialogButtonBox.Ok).setEnabled(bool(t)))
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        form.addRow(bb)
        while dlg.exec() and pw.text():
            try:
                self.win.api.call("admin_update_user", u["id"], password=pw.text(), must_change=must.isChecked())
                QMessageBox.information(self, "Password changed", f"The password of {name} was changed.")
                return
            except ValueError as e:
                QMessageBox.warning(self, "Cannot change password", str(e))

    def toggle_disabled(self):
        u = self.selected_user()
        if not u or self._is_me(u):
            return
        if not u["disabled"] and not confirm(
                self, "Disable account", f"Disable {u['display_name'] or u['username']}?", "Disable",
                detail="They are signed out now and cannot sign in until the account is enabled again. "
                       "Their messages stay."):
            return
        self.win.api.call("admin_update_user", u["id"], disabled=int(not u["disabled"]))
        self.refresh()

    def remove_photo(self):
        u = self.selected_user()
        if u and confirm(self, "Remove profile photo",
                         f"Remove the profile photo of {u['display_name'] or u['username']}?", "Remove photo",
                         detail="This is recorded in the audit log."):
            self.win.api.call("admin_remove_avatar", u["id"])

    def delete_user(self):
        u = self.selected_user()
        if not u or self._is_me(u):
            return
        if confirm(self, "Delete user", f"Delete {u['display_name'] or u['username']} ({u['username']})?", "Delete",
                   detail="Their old messages stay in the history, but they can no longer sign in. "
                          "This cannot be undone."):
            self.win.api.call("admin_delete_user", u["id"])
            self.refresh()

    def export_excel(self):
        from server import excel_users
        path, _ = QFileDialog.getSaveFileName(self, "Save the people list", os.path.join(
            os.path.expanduser("~"), "Desktop", f"Quillo people {datetime.date.today():%Y-%m-%d}.xlsx"),
            "Excel workbook (*.xlsx)")
        if not path:
            return
        api = self.win.api
        try:
            excel_users.write_template(path, api.call("admin_users"), api.call("admin_departments"),
                                       api.call("admin_roles"))
        except OSError as e:
            QMessageBox.warning(self, "Excel template", f"Could not save the file: {e}\n\nIs it open in Excel?")
            return
        if QMessageBox.question(self, "Excel template", "Saved. Open it in Excel now?") == QMessageBox.Yes:
            os.startfile(path)

    def import_file(self):
        from server import excel_users
        path, _ = QFileDialog.getOpenFileName(self, "Import people", os.path.join(os.path.expanduser("~"), "Desktop"),
                                              "People list (*.xlsx *.csv)")
        if not path:
            return
        try:
            rows = excel_users.read_rows(path)
        except (OSError, ValueError, KeyError) as e:
            QMessageBox.warning(self, "Import", f"Could not read {os.path.basename(path)}:\n{e}")
            return
        for r in rows:
            r.pop("_row", None)
        api = self.win.api
        preview = api.call("admin_import_users", rows, dry_run=True)
        lines = [f"<b>{len(preview['created'])}</b> new people,  <b>{len(preview['updated'])}</b> changed,  "
                 f"{preview['unchanged']} unchanged."]
        if preview["new_departments"]:
            lines.append("New departments / sections: " + safe_text(", ".join(preview["new_departments"]))[4:-5])
        if preview["created"]:
            lines.append("New: " + safe_text(", ".join(preview["created"][:30])
                                             + (" " + ELLIPSIS if len(preview["created"]) > 30 else ""))[4:-5])
        if preview["updated"]:
            lines.append("Changed: " + safe_text(", ".join(preview["updated"][:30])
                                                 + (" " + ELLIPSIS if len(preview["updated"]) > 30 else ""))[4:-5])
        if preview["errors"]:
            lines.append(f"<span style='color:{T.DANGER}'>These rows will be skipped:</span><br>"
                         + safe_text("\n".join(preview["errors"][:15]))[4:-5])
        if not preview["created"] and not preview["updated"] and not preview["new_departments"]:
            QMessageBox.information(self, "Import", "<qt>" + "<br><br>".join(lines) + "<br><br>Nothing to change.</qt>")
            return
        if QMessageBox.question(self, "Import people", "<qt>" + "<br><br>".join(lines) +
                                "<br><br>Save these changes?</qt>") != QMessageBox.Yes:
            return
        result = api.call("admin_import_users", rows)
        self.refresh()
        msg = f"Created {len(result['created'])}, updated {len(result['updated'])}."
        if result["errors"]:
            msg += "\n\nSkipped:\n" + "\n".join(result["errors"][:20])
        if result["passwords"]:
            msg += (f"\n\n{len(result['passwords'])} new people got a random first password. "
                    "Save the list now to hand them out (they change it at first sign-in).")
            QMessageBox.information(self, "Import finished", msg)
            self.save_passwords(result["passwords"])
        else:
            QMessageBox.information(self, "Import finished", msg)

    def save_passwords(self, passwords):
        from server import excel_users
        while True:
            path, _ = QFileDialog.getSaveFileName(self, "Save the first passwords", os.path.join(
                os.path.expanduser("~"), "Desktop", f"Quillo first passwords {datetime.date.today():%Y-%m-%d}.xlsx"),
                "Excel workbook (*.xlsx)")
            if path:
                try:
                    excel_users.write_passwords(path, passwords, self.win.api.label)
                    os.startfile(path)
                    return
                except OSError as e:
                    QMessageBox.warning(self, "First passwords", f"Could not save: {e}")
                    continue
            text = "\n".join(f"{p['name']}\t{p['username']}\t{p['password']}" for p in passwords)
            QApplication.clipboard().setText(text)
            QMessageBox.information(self, "First passwords", "Not saved as a file — the list is on the clipboard "
                                                             "now. Paste it somewhere safe.")
            return


# ============================================================ departments
class DepartmentsPage(Page):
    """The studio's departments and their sections, and which of them get a chat room."""

    EMPTY = ("Create your departments here, then pick them for each person on the Users page. "
             "Tick Chat room to give a department or section its own room.")

    def __init__(self, win):
        super().__init__("Departments", "Departments and their sections. People are put in them on the Users "
                                        "page. Tick Chat room to give a department its own room that adds and "
                                        "removes people by itself. Unticking keeps the room and its history as a "
                                        "normal room.")
        self.win = win
        bar = QHBoxLayout()
        bar.addStretch(1)
        add_sect = btn("Add section", "plus")
        add_sect.clicked.connect(self.add_section)
        add_dept = btn("Add department", "plus", primary=True)
        add_dept.clicked.connect(self.add_department)
        bar.addWidget(add_sect)
        bar.addWidget(add_dept)
        self.lay.addLayout(bar)
        self.empty = QLabel(self.EMPTY)
        self.empty.setWordWrap(True)
        self.empty.setAlignment(Qt.AlignCenter)
        self.empty.setStyleSheet(f"background: {T.PANEL}; border-radius: 14px; padding: 28px; color: {T.MUTED};")
        self.empty.hide()
        self.lay.addWidget(self.empty)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Name", "People", "Chat room"])
        self.tree.header().setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.tree.headerItem().setTextAlignment(1, NUM)
        self.tree.setRootIsDecorated(False)          # only when there are sections to open (see refresh)
        self.tree.setAlternatingRowColors(True)
        self.tree.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self.tree.header().setStretchLastSection(False)
        self.tree.itemChanged.connect(self._item_changed)
        self.tree.itemDoubleClicked.connect(lambda *_: self.rename())
        self.lay.addWidget(self.tree, 1)
        row = QHBoxLayout()
        r = btn("Rename" + ELLIPSIS, "edit")
        r.clicked.connect(self.rename)
        d = btn("Delete" + ELLIPSIS, "trash", danger=True)
        d.clicked.connect(self.delete)
        row.addWidget(r)
        row.addWidget(d)
        row.addStretch(1)
        self.lay.addLayout(row)
        self.depts = []
        self._filling = False
        bind_selection(self.tree, (r, d))

    def refresh(self):
        if not self.win.api.running:
            return
        selected = self.selected()
        self.depts = self.win.api.call("admin_departments")
        self._filling = True            # setting check states below must not call the server
        try:
            self.tree.clear()
            items = {}
            by_name = sorted(self.depts, key=lambda d: d["name"].lower())
            for d in (d for d in by_name if d["parent_id"] is None):
                items[d["id"]] = self._item(self.tree, d)
            for s in (d for d in by_name if d["parent_id"] is not None):
                if s["parent_id"] in items:
                    items[s["id"]] = self._item(items[s["parent_id"]], s)
            self.tree.expandAll()
            self.tree.setRootIsDecorated(any(x["parent_id"] is not None for x in self.depts))
            if selected and selected["id"] in items:
                self.tree.setCurrentItem(items[selected["id"]])
        finally:
            self._filling = False
        self.empty.setVisible(not self.depts)
        self.tree.setVisible(bool(self.depts))

    @staticmethod
    def _item(parent, d):
        it = QTreeWidgetItem(parent, [d["name"], str(d["people"]), ""])
        it.setData(0, Qt.UserRole, d["id"])
        it.setTextAlignment(1, NUM)
        it.setForeground(1, QColor(T.META))
        it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
        it.setCheckState(2, Qt.Checked if d["has_room"] else Qt.Unchecked)
        return it

    def selected(self):
        it = self.tree.currentItem()
        if not it:
            return None
        return next((d for d in self.depts if d["id"] == it.data(0, Qt.UserRole)), None)

    def _call(self, title, fn, *args):
        """Run an admin function; show the server's refusal (e.g. people still in it) as a message."""
        try:
            self.win.api.call(fn, *args)
        except ValueError as e:
            QMessageBox.warning(self, title, str(e))
        # refresh after this click has been handled: the tree is rebuilt, the clicked item would vanish under Qt
        QTimer.singleShot(0, self.win.refresh_current)

    def _item_changed(self, it, column):
        if self._filling or column != 2:
            return
        self._call("Chat room", "admin_set_department_room", it.data(0, Qt.UserRole),
                   it.checkState(2) == Qt.Checked)

    def add_department(self):
        name, ok = ask_text(self, "Add department", "Department name (e.g. Compositing, Lighting, FX):",
                            ok="Add department")
        if ok and name.strip():
            self._call("Cannot add department", "admin_save_department", None, name.strip())

    def add_section(self):
        d = self.selected()
        if d and d["parent_id"] is not None:          # a section is selected: add next to it
            d = next((x for x in self.depts if x["id"] == d["parent_id"]), None)
        if not d:
            QMessageBox.information(self, "Add section", "Select the department the section belongs to first.")
            return
        name, ok = ask_text(self, "Add section", f"New section of {d['name']} (e.g. Roto, Paint, Prep):",
                            ok="Add section")
        if ok and name.strip():
            self._call("Cannot add section", "admin_save_department", None, name.strip(), d["id"])

    def rename(self):
        d = self.selected()
        if not d:
            return
        name, ok = ask_text(self, "Rename", "New name (everyone in it moves along, and so does its chat room):",
                            d["name"], ok="Rename")
        if ok and name.strip() and name.strip() != d["name"]:
            self._call("Cannot rename", "admin_save_department", d["id"], name.strip())

    def delete(self):
        d = self.selected()
        if not d:
            return
        what = "department" if d["parent_id"] is None else "section"
        extra = " and its sections" if what == "department" and any(
            x["parent_id"] == d["id"] for x in self.depts) else ""
        room = "Its chat room stays as a normal room (delete it on the Rooms page if not needed)." \
            if d["has_room"] else ""
        if confirm(self, f"Delete {what}", f"Delete the {what} ‘{d['name']}’{extra}?", "Delete", detail=room):
            self._call(f"Cannot delete {what}", "admin_delete_department", d["id"])


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
        self.count = QLabel()
        self.count.setStyleSheet(f"color: {T.META};")
        head.addWidget(self.count)
        head.addStretch(1)
        for text, state in (("Select all", Qt.Checked), ("Clear", Qt.Unchecked)):
            b = QPushButton(text)
            b.setCursor(Qt.PointingHandCursor)
            T.polish(b, chip=True)
            b.clicked.connect(lambda _=False, s=state: self._check_all(s))
            head.addWidget(b)
        lay.addLayout(head)
        self.filter = QLineEdit()
        self.filter.setPlaceholderText("Filter by name or department")
        self.filter.addAction(icon("search", T.FAINT, 16), QLineEdit.LeadingPosition)
        self.filter.setClearButtonEnabled(True)
        self.filter.textChanged.connect(self._filter)
        lay.addWidget(self.filter)
        self.list = QListWidget()
        members = set(room["members"]) if room else set()
        for u in sorted(users, key=lambda u: u["display_name"].lower()):
            if u["disabled"] or (is_console_account(u) and u["id"] not in members):
                continue                    # the console's own account is not a person to add
            dept = f"  ·  {u['department']}" if u["department"] else ""
            it = QListWidgetItem(f"{u['display_name']}{dept}")
            it.setData(Qt.UserRole, u["id"])
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Checked if u["id"] in members else Qt.Unchecked)
            self.list.addItem(it)
        self.list.itemChanged.connect(lambda _it: self._count())
        lay.addWidget(self.list, 1)
        bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        T.polish(bb.button(QDialogButtonBox.Save), primary=True)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self._count()

    def _count(self):
        n = len(self.members())
        self.count.setText(f"·  {n} selected")

    def _filter(self, text):
        q = text.strip().lower()
        for i in range(self.list.count()):
            it = self.list.item(i)
            it.setHidden(bool(q) and q not in it.text().lower())

    def _check_all(self, state):
        """Select all / Clear: the people shown (after a filter, only those)."""
        for i in range(self.list.count()):
            if not self.list.item(i).isHidden():
                self.list.item(i).setCheckState(state)

    def members(self):
        return [self.list.item(i).data(Qt.UserRole) for i in range(self.list.count())
                if self.list.item(i).checkState() == Qt.Checked]


class RoomsPage(Page):
    def __init__(self, win):
        super().__init__("Rooms", "Group chats for teams and projects. Automatic rooms keep their members "
                                  "in step by themselves — turn them on with Chat room on the Departments page "
                                  "(\"All Studio\" is in Settings).")
        self.win = win
        bar = QHBoxLayout()
        bar.addStretch(1)
        add = btn("New room", "plus", primary=True)
        add.clicked.connect(lambda: self.edit_room(None))
        bar.addWidget(add)
        self.lay.addLayout(bar)
        self.table = make_table(["Room", "Type", "Members", "Keep shared files", "Topic"])
        self.table.doubleClicked.connect(lambda: self.edit_room(self.selected()))
        self.lay.addWidget(self.table, 1)
        row = QHBoxLayout()
        e = btn("Edit", "edit")
        e.clicked.connect(lambda: self.edit_room(self.selected()))
        k = btn("Keep files" + ELLIPSIS, "time")
        k.setToolTip("How long this room's shared files stay on the server")
        k.clicked.connect(lambda: self.keep_files(self.selected()))
        d = btn("Delete" + ELLIPSIS, "trash", danger=True)
        d.clicked.connect(self.delete_room)
        row.addWidget(e)
        row.addWidget(k)
        row.addWidget(d)
        row.addStretch(1)
        self.lay.addLayout(row)
        self.rooms = []
        bind_selection(self.table, (e, k, d))

    def refresh(self):
        if not self.win.api.running:
            return
        self.rooms = self.win.api.call("admin_rooms")
        names = {u["id"]: u["display_name"] for u in self.win.api.call("admin_users")}
        self.default_days = float(self.win.api.call("admin_config").get("file_retention_days") or 0)
        self.table.setRowCount(len(self.rooms))
        for r, room in enumerate(self.rooms):
            self.table.setItem(r, 0, cell(room["name"], room["id"]))
            self.table.setItem(r, 1, cell("Automatic" if room["auto"] else "Manual",
                                          color=T.ACCENT if room["auto"] else T.META))
            n = len(room["members"])
            member_names = sorted((names.get(m, "?") for m in room["members"]), key=str.lower)
            tip = "\n".join(member_names[:40]) + (f"\nand {n - 40} more" if n > 40 else "")
            self.table.setItem(r, 2, cell(f"{n} {'person' if n == 1 else 'people'}", tip=tip or "Nobody yet"))
            days = room.get("file_retention_days")
            self.table.setItem(r, 3, cell(keep_files_text(days, self.default_days),
                                          color=T.META if days is None else None))
            self.table.setItem(r, 4, cell(room["topic"]))

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

    def keep_files(self, room):
        if not room:
            QMessageBox.information(self, "Keep files", "Select a room first.")
            return
        dlg = QDialog(self)
        dlg.setWindowTitle("Keep shared files")
        lay = QVBoxLayout(dlg)
        lay.setContentsMargins(22, 20, 22, 16)
        lay.setSpacing(10)
        head = QLabel(room["name"])
        head.setTextFormat(Qt.PlainText)
        head.setStyleSheet("font-size: 12pt; font-weight: 600;")
        lay.addWidget(head)
        note = QLabel("Files shared in this room are deleted from the server after this time. The messages stay; "
                      "the file shows as expired. Direct chats and other rooms follow the server default.")
        note.setWordWrap(True)
        note.setStyleSheet(f"color: {T.META};")
        lay.addWidget(note)
        choice = QComboBox()
        choice.addItem(keep_files_text(None, self.default_days), "default")
        choice.addItem("Forever (never delete)", "forever")
        choice.addItem("A number of days", "days")
        days = QSpinBox()
        days.setRange(1, 3650)
        days.setSuffix(" days")
        current = room.get("file_retention_days")
        choice.setCurrentIndex(0 if current is None else 1 if current == 0 else 2)
        days.setValue(int(current) if current else 90)
        days.setEnabled(choice.currentData() == "days")
        choice.currentIndexChanged.connect(lambda _i: days.setEnabled(choice.currentData() == "days"))
        lay.addWidget(choice)
        lay.addWidget(days)
        bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        T.polish(bb.button(QDialogButtonBox.Save), primary=True)
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        lay.addWidget(bb)
        if not dlg.exec():
            return
        value = {"default": None, "forever": 0}.get(choice.currentData(), days.value())
        try:
            self.win.api.call("admin_set_room_retention", room["id"], value)
        except ValueError as e:
            QMessageBox.warning(self, "Keep files", str(e))
        self.refresh()

    def delete_room(self):
        room = self.selected()
        if room and room["auto"]:
            QMessageBox.information(self, "Automatic room", "Untick Chat room for this department or section on "
                                    "the Departments page first (\"All Studio\": in Settings). The room then "
                                    "becomes a normal room that you can delete here.")
            return
        if room and confirm(self, "Delete room", f"Delete the room ‘{room['name']}’?", "Delete",
                            detail="It disappears for everyone in it, with its messages."):
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
        self.setMinimumWidth(500)
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
        self.always_visible = QCheckBox("Always visible to everyone (HR, IT" + ELLIPSIS + ")")
        self.always_visible.setToolTip("For example HR, IT and Management: people who can't see everyone still "
                                       "see them")
        for box, key, default in ((self.create_rooms, "create_rooms", True), (self.manage_users, "manage_users", False),
                                  (self.see_all, "see_all", True), (self.always_visible, "always_visible", False)):
            box.setChecked(bool(role[key]) if role else default)
        form.addRow("Designation", self.name)
        add_row_with_hint(form, "Level", self.level, "Higher levels are listed first in the directory and the "
                                                     "org chart")
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
                                 "Manage accounts", "Sees", "Always visible", "People"])
        align_columns(self.table, [1, 7])
        numbers_last(self.table)
        self.table.horizontalHeaderItem(5).setToolTip("Who they see in the app: everyone, or a limited list")
        self.table.doubleClicked.connect(lambda: self.edit(self.selected()))
        self.lay.addWidget(self.table, 1)
        row = QHBoxLayout()
        e = btn("Edit", "edit")
        e.clicked.connect(lambda: self.edit(self.selected()))
        d = btn("Delete" + ELLIPSIS, "trash", danger=True)
        d.clicked.connect(self.delete)
        row.addWidget(e)
        row.addWidget(d)
        row.addStretch(1)
        self.lay.addLayout(row)
        self.roles = []
        bind_selection(self.table, (e, d))

    def refresh(self):
        if not self.win.api.running:
            return
        self.roles = self.win.api.call("admin_roles")
        labels = dict(ANNOUNCE_CHOICES)
        yes = lambda v: cell("Yes" if v else "No", color=T.ACCENT if v else T.FAINT)  # noqa: E731
        limited = ("Their own department, their reporting line, people in their rooms\n"
                   "and 'always visible' people")
        self.table.setRowCount(len(self.roles))
        for r, role in enumerate(self.roles):
            self.table.setItem(r, 0, cell(role["name"], role["id"]))
            self.table.setItem(r, 1, num(role["level"]))
            self.table.setItem(r, 2, cell(labels.get(role["announce"], role["announce"]).split(" (")[0],
                                          color=T.FAINT if role["announce"] == "none" else None))
            self.table.setItem(r, 3, yes(role["create_rooms"]))
            self.table.setItem(r, 4, yes(role["manage_users"]))
            self.table.setItem(r, 5, cell("Everyone", tip="Everyone in the studio") if role["see_all"] else
                               cell("Limited", tip=limited))
            self.table.setItem(r, 6, yes(role["always_visible"]))
            self.table.setItem(r, 7, num(role["users"]))

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
        n = role["users"] if role else 0
        if role and confirm(self, "Delete designation", f"Delete the designation ‘{role['name']}’?", "Delete",
                            detail=f"{n} {'person' if n == 1 else 'people'} will have no designation until you "
                                   "pick a new one." if n else ""):
            self.win.api.call("admin_delete_role", role["id"])
            self.refresh()


# ================================================================ org chart
class OrgPage(Page):
    def __init__(self, win):
        super().__init__("Org chart", "Who is where, and who reports to whom. Set department, section, "
                                      "designation and 'Reports to' on the Users page.")
        from common import orgviews
        self.win = win
        # inside the console, people without a manager are fixed on the Users page (not 'in the console')
        hint = "set 'Reports to' on the Users page"
        try:
            self.browser = orgviews.OrgBrowser(action_text="Edit", view="chart", loner_hint=hint)
        except TypeError:                       # an org chart without that option keeps its own words
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
    LIVE = True

    def __init__(self, win):
        super().__init__("Online now", "Connected client sessions. Someone signed in on two PCs shows twice.")
        self.win = win
        self.table = make_table(["User", "Username", "IP address", "Status", "Version", "Connected since"])
        self.lay.addWidget(self.table, 1)
        row = QHBoxLayout()
        k = btn("Disconnect" + ELLIPSIS, "power", danger=True)
        k.setToolTip("Sign the selected person out on every PC")
        k.clicked.connect(self.kick)
        row.addWidget(k)
        row.addStretch(1)
        self.lay.addLayout(row)
        bind_selection(self.table, (k,))

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
            self.table.setItem(r, 4, cell(version_text(s.get("version") or "older"),
                                          color=None if s.get("version") else T.META))
            self.table.setItem(r, 5, cell(fmt_time(s["since"])))

    def kick(self):
        items = self.table.selectedItems()
        if not items:
            return
        first = self.table.item(items[0].row(), 0)
        uid, name = first.data(Qt.UserRole), first.text()
        if confirm(self, "Disconnect", f"Disconnect {name}?", "Disconnect",
                   detail="Quillo signs them out on every PC now. They can sign in again."):
            self.win.api.call("admin_kick", uid)
            QTimer.singleShot(300, self.refresh)


# ========================================================== announcements
class AnnouncePage(Page):
    def __init__(self, win):
        super().__init__("Announcements", "Send a message that pops up on the chosen people's PCs and stays on top "
                                          "until they acknowledge it (people who are offline see it when they sign "
                                          "in).")
        self.win = win
        form = QFormLayout()
        form.setSpacing(10)
        self.title = QLineEdit()
        self.title.setPlaceholderText("e.g. Server maintenance tonight")
        self.dept = QComboBox()
        self.dept.currentIndexChanged.connect(lambda _i: self._update_reach())
        form.addRow("Title", self.title)
        self.reach = add_row_with_hint(form, "Send to", self.dept, "")
        self.lay.addLayout(form)
        self.body = QPlainTextEdit()
        self.body.setPlaceholderText("Write the announcement" + ELLIPSIS)
        self.body.setMaximumHeight(140)
        self.body.textChanged.connect(self._update_send)
        self.lay.addWidget(self.body)
        row = QHBoxLayout()
        row.addStretch(1)
        self.send_btn = btn("Send announcement", "megaphone", primary=True)
        self.send_btn.clicked.connect(self.send)
        row.addWidget(self.send_btn)
        self.lay.addLayout(row)
        sent_row = QHBoxLayout()
        sent = QLabel("Sent announcements")
        T.polish(sent, muted=True)
        sent_row.addWidget(sent, 1)
        self.reads_btn = btn("Who has read it", "check_all")
        self.reads_btn.clicked.connect(self.show_reads)
        sent_row.addWidget(self.reads_btn)
        self.lay.addLayout(sent_row)
        self.table = make_table(["When", "From", "Title", "To", "Read"])
        align_columns(self.table, [4])
        numbers_last(self.table)
        self.table.doubleClicked.connect(self.show_reads)
        self.lay.addWidget(self.table, 1)
        self.anns = []
        self.people = []
        bind_selection(self.table, (self.reads_btn,))
        self._update_send()

    def _update_send(self):
        """Send works only with something to send (an empty body used to do nothing, silently)."""
        has_text = bool(self.body.toPlainText().strip())
        self.send_btn.setEnabled(has_text)
        self.send_btn.setToolTip("" if has_text else "Write the announcement first")

    def _recipients(self):
        kind, dept, sect = self.dept.currentData() or ("all", "", "")
        return [u for u in self.people if kind == "all" or (u["department"].lower() == dept.lower() and (
            kind == "department" or u["section"].lower() == sect.lower()))]

    def _update_reach(self):
        n = len(self._recipients())
        self.reach.setText(f"{n} {'person gets' if n == 1 else 'people get'} a pop-up they must acknowledge")

    def show_reads(self):
        items = self.table.selectedItems()
        if not items:
            return
        ann_id = self.table.item(items[0].row(), 0).data(Qt.UserRole)
        ann = next((a for a in self.anns if a["id"] == ann_id), None)
        if not ann:
            return
        reads = self.win.api.call("admin_announcement_reads", ann["id"])
        dlg = QDialog(self)
        dlg.setWindowTitle("Who has read it")
        dlg.setMinimumSize(420, 460)
        lay = QVBoxLayout(dlg)
        lay.setContentsMargins(20, 18, 20, 14)
        lay.setSpacing(8)
        head = QLabel(ann["title"])
        head.setTextFormat(Qt.PlainText)
        head.setWordWrap(True)
        head.setStyleSheet(f"font-size: {T.pt(T.FONT_L)}; font-weight: 600;")
        lay.addWidget(head)
        n_read, n_unread = len(reads["read"]), len(reads["unread"])
        done = n_read and not n_unread
        summary = QLabel(f"Read by {n_read} of {n_read + n_unread}")
        summary.setStyleSheet(f"color: {T.ACCENT if done else T.META};")
        lay.addWidget(summary)
        lst = QListWidget()
        for title, people, ic in ((f"Read ({n_read})", reads["read"], icon("check", T.ACCENT, 14)),
                                  (f"Not read yet ({n_unread})", reads["unread"], QIcon())):
            if not people:
                continue
            section = QListWidgetItem(title.upper())
            section.setFlags(Qt.NoItemFlags)
            section.setForeground(QColor(T.META))
            font = section.font()
            font.setPointSizeF(T.FONT_XS)
            font.setBold(True)
            section.setFont(font)
            lst.addItem(section)
            for p in people:
                lst.addItem(QListWidgetItem(ic, p["name"]))
        lay.addWidget(lst, 1)
        bb = QDialogButtonBox(QDialogButtonBox.Close)
        bb.rejected.connect(dlg.reject)
        lay.addWidget(bb)
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
            self.table.setItem(r, 3, cell("Everyone in the studio" if a.get("target_kind") == "all"
                                          else a["target_label"]))
            done = a["read_count"] >= a["total"] and a["total"]
            self.table.setItem(r, 4, cell(f"{a['read_count']} / {a['total']}", color=T.ACCENT if done else None,
                                          align=NUM))
        current = self.dept.currentData()
        self.dept.blockSignals(True)
        self.dept.clear()
        self.dept.addItem("Everyone in the studio", ("all", "", ""))
        users = self.people = [u for u in self.win.api.call("admin_users") if not u["disabled"]
                               and not is_console_account(u) and u["username"] != "pipeline-bot"]
        depts = sorted({u["department"] for u in users if u["department"]}, key=str.lower)
        for d in depts:
            self.dept.addItem(f"Department: {d}", ("department", d, ""))
            for s in sorted({u["section"] for u in users if u["section"] and u["department"] == d}, key=str.lower):
                self.dept.addItem(f"      Section: {d} · {s}", ("section", d, s))
        idx = self.dept.findData(current)
        self.dept.setCurrentIndex(max(idx, 0))
        self.dept.blockSignals(False)
        self._update_reach()

    def send(self):
        if not self.body.toPlainText().strip():
            return
        kind, dept, sect = self.dept.currentData() or ("all", "", "")
        if kind == "all":
            n = len(self._recipients())
            if not confirm(self, "Send announcement", f"Send this to everyone in the studio ({n} people)?", "Send",
                           danger=False, detail="It pops up on every PC and stays on top until it is "
                                                "acknowledged. It can't be taken back."):
                return
        try:
            self.win.api.call("admin_announce", self.title.text(), self.body.toPlainText(), kind, dept, sect)
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "Not sent", str(e))
            return
        self.title.clear()
        self.body.clear()
        QMessageBox.information(self, "Sent", "Announcement sent.")
        self.refresh()


# ================================================================ reports
class ReportsPage(Page):
    def __init__(self, win):
        super().__init__("Reports", "Who is using the messenger and how much — per department, person and room.",
                         scroll=True)
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
        exp = btn("Export CSV", "download")
        exp.setToolTip("Save the table on the open tab as a CSV file (Excel opens it)")
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
        for t in (self.t_depts, self.t_rooms):
            numbers_last(t)
        align_columns(self.t_depts, [1, 2, 3, 4, 5])
        align_columns(self.t_people, [3, 4, 5, 6])
        align_columns(self.t_rooms, [1])
        self.tabs.addTab(self.t_depts, "Departments")
        self.tabs.addTab(self.t_people, "People")
        self.tabs.addTab(self.t_rooms, "Busiest rooms")
        self.t_idle = make_table(["Person", "Department", "Last signed in"])
        self.tabs.addTab(self.t_idle, "Not seen in 30 days")
        self.tabs.setMinimumHeight(300)                 # the page scrolls on a short screen, not the tables
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
        self.chart.set_data(self.every_day(r["daily"], r["days"]))
        self._fill(self.t_depts, [[d["department"], d["users"], d["active"], d["messages"], d["files"],
                                   size_cell(d["uploaded"])] for d in r["departments"]])
        self._fill(self.t_people, [[p["name"] + ("  (disabled)" if p["disabled"] else ""), p["department"],
                                    p["section"], p["messages"], p["files"], size_cell(p["uploaded"]),
                                    size_cell(p["stored"]), fmt_time(p["last_seen"])] for p in r["people"]])
        self._fill(self.t_rooms, [[x["room"], x["messages"]] for x in r["rooms"]])
        idle = r.get("inactive", [])
        self._fill(self.t_idle, [[p["name"], p["department"], fmt_time(p["last_seen"])] for p in idle])
        self.tabs.setTabText(self.tabs.indexOf(self.t_idle),
                             f"Not seen in 30 days ({len(idle)})" if idle else "Not seen in 30 days")

    @staticmethod
    def every_day(daily, days, today=None):
        """One entry per day of the period, 0 for the quiet days: the chart shows the whole period, not just
        the days that had messages."""
        counts = {d["day"]: d["messages"] for d in daily}
        today = today or datetime.date.today()
        start = today - datetime.timedelta(days=max(1, int(days)) - 1)
        for day in counts:
            try:
                start = min(start, datetime.date.fromisoformat(day))
            except ValueError:
                pass
        out, day = [], start
        while day <= today:
            out.append({"day": day.isoformat(), "messages": counts.get(day.isoformat(), 0)})
            day += datetime.timedelta(days=1)
        return out

    @staticmethod
    def _fill(table, rows):
        table.setRowCount(len(rows))
        for i, row in enumerate(rows):
            for j, v in enumerate(row):
                table.setItem(i, j, v if isinstance(v, QTableWidgetItem) else
                              num(v) if isinstance(v, (int, float)) else cell(v))

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
        if not any(d["messages"] for d in self.data):
            p.setPen(QColor(T.META))
            p.drawText(self.rect(), Qt.AlignCenter, "No messages in this period")
            return
        top = max(d["messages"] for d in self.data)
        n = len(self.data)
        head, foot = 22, 20                              # room for "Busiest day ..." and the dates
        bw = min(34.0, max(2.0, (w - 20) / n))           # a few days: slim bars, not one wall of colour
        left = 10 + ((w - 20) - bw * n) / 2
        base = h - foot
        p.setPen(QColor(T.HAIR))
        p.drawLine(10, int(base), w - 10, int(base))
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(T.ACCENT))
        for i, d in enumerate(self.data):
            bh = (base - head) * d["messages"] / top
            if bh:
                p.drawRoundedRect(int(left + i * bw + 1), int(base - bh), max(1, int(bw - 3)), max(1, int(bh)), 3, 3)
        day = lambda d: datetime.date.fromisoformat(d["day"])          # noqa: E731
        busiest = max(self.data, key=lambda d: d["messages"])
        p.setPen(QColor(T.META))
        p.drawText(10, 12, f"Busiest day: {fmt_date(day(busiest), weekday=False)}{SEP}"
                           f"{top:,} message{'s' if top != 1 else ''}")
        # the dates sit under the first and the last bar
        p.drawText(QRectF(left, h - 16, 160, 14), Qt.AlignLeft, fmt_date(day(self.data[0]), weekday=False))
        if n > 1:
            right = left + n * bw
            p.drawText(QRectF(right - 160, h - 16, 160, 14), Qt.AlignRight,
                       fmt_date(day(self.data[-1]), weekday=False))


# ================================================================ holidays
class HolidaysPage(Page):
    """The studio holiday list: tick the days the studio is closed; add, change, import, more years."""

    def __init__(self, win):
        super().__init__("Holidays", "Ticked days are holidays on everyone's calendar. India's public and festival "
                                     "holidays are listed up to 2035 — tick the ones your studio keeps. Festival "
                                     "dates follow the lunar calendar: the ones marked 'Check the date' may be a "
                                     "day off from your almanac. HR can also change this list in the app.")
        self.win = win
        bar = QHBoxLayout()
        bar.addWidget(QLabel("Year"))
        self.year = QSpinBox()
        self.year.setRange(2000, 2100)
        self.year.setValue(datetime.date.today().year)
        self.year.valueChanged.connect(self.refresh)
        bar.addWidget(self.year)
        bar.addStretch(1)
        # the same buttons, in the same order, as the Holidays dialog in the app
        for text, ic, fn, primary in (("Add a day", "plus", lambda: self.edit(None), True),
                                      ("Import .ics" + ELLIPSIS, "upload", self.import_ics, False),
                                      ("Add India's list" + ELLIPSIS, "calendar", self.add_year, False)):
            b = btn(text, ic, primary=primary)
            b.clicked.connect(fn)
            bar.addWidget(b)
        self.lay.addLayout(bar)
        self.table = make_table(["Studio closed", "Date", "Holiday", "Kind", "Note"])
        self.table.itemChanged.connect(self._ticked)
        self.table.doubleClicked.connect(lambda: self.edit(self.selected()))
        self.lay.addWidget(self.table, 1)
        row = QHBoxLayout()
        e = btn("Edit", "edit")
        e.clicked.connect(lambda: self.edit(self.selected()))
        d = btn("Delete" + ELLIPSIS, "trash", danger=True)
        d.clicked.connect(self.delete)
        row.addWidget(e)
        row.addWidget(d)
        row.addStretch(1)
        self.lay.addLayout(row)
        self.rows = []
        self._loading = False
        bind_selection(self.table, (e, d))

    def refresh(self, *_):
        if not self.win.api.running:
            return
        self.rows = self.win.api.call("admin_holidays", self.year.value())
        self._loading = True
        note_color = T.readable_on(T.WARN_TEXT, T.PANEL)
        self.table.setRowCount(len(self.rows))
        for r, h in enumerate(self.rows):
            tick = QTableWidgetItem("")
            tick.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            tick.setCheckState(Qt.Checked if h["observed"] else Qt.Unchecked)
            tick.setData(Qt.UserRole, h["id"])
            self.table.setItem(r, 0, tick)
            day = datetime.date.fromisoformat(h["day"])
            self.table.setItem(r, 1, cell(fmt_date(day, year=False)))       # the year is in the Year box
            self.table.setItem(r, 2, cell(h["name"]))
            self.table.setItem(r, 3, cell({"national": "National", "festival": "Festival", "studio": "Studio",
                                           "other": "Public"}.get(h["kind"], h["kind"]), color=T.META))
            self.table.setItem(r, 4, cell("Check the date" if h["confirm"] else "", color=note_color,
                                          tip="A festival on the lunar calendar: it may be a day off from your "
                                              "almanac" if h["confirm"] else ""))
        hide_empty_columns(self.table, [4])
        self._loading = False

    def selected(self):
        rows = self.table.selectionModel().selectedRows()
        return self.rows[rows[0].row()] if rows else None

    def _ticked(self, it):
        if self._loading or it.column() != 0:
            return
        self.win.api.call("admin_holiday_observe", [it.data(Qt.UserRole)], it.checkState() == Qt.Checked)

    def edit(self, h):
        from PySide6.QtCore import QDate
        from PySide6.QtWidgets import QDateEdit
        dlg = QDialog(self)
        dlg.setWindowTitle("Edit holiday" if h else "Add a holiday")
        form = QFormLayout(dlg)
        day = QDateEdit()
        d = datetime.date.fromisoformat(h["day"]) if h else datetime.date(self.year.value(), 1, 1)
        day.setDate(QDate(d.year, d.month, d.day))
        day.setCalendarPopup(True)
        day.setDisplayFormat("ddd d MMM yyyy")
        name = QLineEdit(h["name"] if h else "")
        name.setPlaceholderText("e.g. Studio anniversary")
        form.addRow("Date", day)
        form.addRow("Name", name)
        bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        T.polish(bb.button(QDialogButtonBox.Save), primary=True)
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        form.addRow(bb)
        while dlg.exec():
            q = day.date()
            try:
                self.win.api.call("admin_holiday_save", h["id"] if h else None,
                                  datetime.date(q.year(), q.month(), q.day()).isoformat(), name.text(), True)
                break
            except ValueError as e:
                QMessageBox.warning(self, "Holiday", str(e))
        self.refresh()

    def delete(self):
        h = self.selected()
        if h and confirm(self, "Delete holiday", f"Delete {h['name']} "
                         f"({fmt_date(datetime.date.fromisoformat(h['day']), year=True)})?", "Delete"):
            self.win.api.call("admin_holiday_delete", h["id"])
            self.refresh()

    def add_year(self):
        year, ok = QInputDialog.getInt(self, "Add a year", "Add India's holiday list for the year:",
                                       max(self.year.value() + 1, 2036), 2000, 2100)
        if ok:
            added = self.win.api.call("admin_holiday_add_year", year)
            QMessageBox.information(self, "Holidays", f"Added {added} days for {year}. Tick the ones your studio keeps.")
            self.year.setValue(year)

    def import_ics(self):
        path, _ = QFileDialog.getOpenFileName(self, "Import holidays (.ics)", "", "Calendar files (*.ics)")
        if not path:
            return
        try:
            with open(path, encoding="utf-8-sig", errors="replace") as f:
                text = f.read()
            added = self.win.api.call("admin_holidays_import", text)
        except (OSError, ValueError) as e:
            QMessageBox.warning(self, "Import", str(e))
            return
        QMessageBox.information(self, "Import", f"Added {added} holidays (all ticked).")
        self.refresh()


class StoragePage(Page):
    """Where the file storage goes: per person, per chat, the largest files - and a clean-up."""

    def __init__(self, win):
        super().__init__("Storage", "Shared files kept on the server. Old files are deleted automatically "
                                    "(Settings > Files); a room can keep its files longer or shorter (Rooms > "
                                    "Keep files). The messages stay — the file shows as expired.", scroll=True)
        self.win = win
        grid = QGridLayout()
        grid.setSpacing(16)
        self.cards = {}
        for i, (key, title, ic) in enumerate((("used", "Storage used", "hdd"), ("files", "Files stored", "file"),
                                              ("free", "Free on the disk", "server"),
                                              ("oldest", "Oldest file", "time"))):
            self.cards[key] = StatCard(title, ic)
            grid.addWidget(self.cards[key], 0, i)
        self.lay.addLayout(grid)
        self.policy = QLabel()
        self.policy.setWordWrap(True)
        self.policy.setStyleSheet(f"color: {T.META};")
        self.lay.addWidget(self.policy)
        tabs = QTabWidget()
        tabs.setMinimumHeight(320)                       # the page scrolls on a short screen, not the tables
        self.by_user = make_table(["Person", "Files", "Size"])
        self.by_room = make_table(["Chat", "Keep files", "Files", "Size"])
        self.largest = make_table(["File", "Size", "Sent by", "Where", "Date", "Downloads"])
        align_columns(self.by_user, [1, 2])
        for t in (self.by_user, self.by_room, self.largest):
            numbers_last(t)
        align_columns(self.by_room, [2, 3])
        align_columns(self.largest, [1, 5])
        for table, title in ((self.by_user, "By person"), (self.by_room, "By chat"),
                             (self.largest, "Largest files")):
            tabs.addTab(table, title)
        self.lay.addWidget(tabs, 1)
        row = QHBoxLayout()
        row.addWidget(QLabel("Delete every file older than"))
        self.days = QSpinBox()
        self.days.setRange(1, 3650)
        self.days.setValue(30)
        self.days.setSuffix(" days")
        self.days_touched = False                        # the admin's own number is kept from then on
        self.days.valueChanged.connect(lambda _v: setattr(self, "days_touched", True))
        row.addWidget(self.days)
        clean = btn("Clean up now" + ELLIPSIS, "trash", danger=True)
        clean.clicked.connect(self.clean_up)
        row.addWidget(clean)
        row.addStretch(1)
        self.lay.addLayout(row)

    def refresh(self):
        if not self.win.api.running:
            return
        s = self.win.api.call("admin_storage")
        self.cards["used"].value.setText(human_size(s["total_bytes"]))
        self.cards["files"].value.setText(f"{s['total_files']:,}")
        disk = s.get("disk")
        self.cards["free"].value.setText(f"{human_size(disk['free'])}" if disk else "-")
        self.cards["free"].setToolTip(f"of {human_size(disk['total'])} on the disk with {s['storage_dir']}"
                                      if disk else s["storage_dir"])
        self.cards["oldest"].value.setText(fmt_date(s["oldest"], weekday=False) if s.get("oldest") else "–")
        default = s["default_days"]
        text = (f"Files are deleted automatically after {default:g} day{'s' if default != 1 else ''}" if default
                else "Files are kept until you delete them (no automatic clean-up)")
        if s.get("unclaimed_days"):
            text += f"; files nobody downloaded after {s['unclaimed_days']:g} days"
        self.policy.setText(text + ".")
        if default and not self.days_touched:           # the clean-up starts from the rule that applies
            self.days.blockSignals(True)
            self.days.setValue(max(1, int(round(default))))
            self.days.blockSignals(False)
        self._fill(self.by_user, [(u["name"] or u["username"], num(u["files"]), size_cell(u["bytes"]))
                                  for u in s["by_user"]])
        rows = [(r["name"], keep_files_text(r["retention"], default), r["files"], r["bytes"]) for r in s["by_room"]]
        if s["direct"]["files"]:
            rows.append(("Direct chats", keep_files_text(None, default), s["direct"]["files"], s["direct"]["bytes"]))
        rows.sort(key=lambda r: -r[3])
        self._fill(self.by_room, [(n, k, num(f), size_cell(b)) for n, k, f, b in rows])
        self._fill(self.largest, [(f["name"], size_cell(f["size"]), f["sender"] or "?", f["room"] or "Direct chat",
                                   fmt_time(f["created_at"]), num(f["downloads"])) for f in s["largest"]])

    @staticmethod
    def _fill(table, rows):
        table.setRowCount(len(rows))
        for r, values in enumerate(rows):
            for c, v in enumerate(values):
                table.setItem(r, c, v if isinstance(v, QTableWidgetItem) else cell(v))

    def clean_up(self):
        days = self.days.value()
        try:                                    # how many would go, for the question (older servers can't say)
            preview = self.win.api.call("admin_cleanup_files", days, dry_run=True)
        except Exception:  # noqa: BLE001
            preview = None
        if preview is not None and not preview.get("removed"):
            QMessageBox.information(self, "Clean up files", f"No shared file is older than {days} days. "
                                                            "Nothing to delete.")
            return
        what = (f"{preview['removed']:,} shared file{'s' if preview['removed'] != 1 else ''} "
                f"({human_size(preview['bytes'])})" if preview else "every shared file")
        if not confirm(self, "Clean up files", f"Delete {what} older than {days} days from the server now?",
                       "Delete files", detail="The messages stay; the files show as expired and can't be "
                                              "downloaded any more. This cannot be undone."):
            return
        try:
            r = self.win.api.call("admin_cleanup_files", days)
        except ValueError as e:
            QMessageBox.warning(self, "Clean up files", str(e))
            return
        QMessageBox.information(self, "Clean up files",
                                f"Deleted {r['removed']} files and freed {human_size(r['bytes'])}.")
        self.refresh()


# ================================================================ updates
class UpdatesPage(Page):
    def __init__(self, win):
        super().__init__("Updates", "Publish a new Quillo version to every PC: choose the "
                                    "Quillo-Client-Setup-x.y.z.exe. Signed-in PCs are told straight away and "
                                    "install it with one click (PCs installed \"just for me\" need no "
                                    "administrator; others ask for one). For silent roll-outs use your "
                                    "deployment tool.", scroll=True)
        self.win = win
        self.info = QLabel()
        self.info.setWordWrap(True)
        self.info.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.info.setStyleSheet(f"background: {T.PANEL}; border-radius: 12px; padding: 16px;")
        self.lay.addWidget(self.info)
        row = QHBoxLayout()
        self.publish_btn = btn("Publish client update" + ELLIPSIS, "upload", primary=True)
        self.publish_btn.clicked.connect(self.publish)
        row.addWidget(self.publish_btn)
        self.open_btn = btn("Open updates folder", "folder")
        self.open_btn.clicked.connect(self.open_folder)
        row.addWidget(self.open_btn)
        row.addStretch(1)
        self.server_btn = btn("Update this server" + ELLIPSIS, "server")
        self.server_btn.setToolTip("Run a new Quillo-Server-Setup on this PC (chats, files and settings are kept)")
        self.server_btn.clicked.connect(self.update_server)
        row.addWidget(self.server_btn)
        self.lay.addLayout(row)
        self.versions = make_table(["Version", "PCs signed in now"])
        align_columns(self.versions, [1])
        numbers_last(self.versions)
        self.versions.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)   # sized to its rows (see refresh)
        self.lay.addWidget(self.versions)
        people_row = QHBoxLayout()
        self.people_label = QLabel()
        self.people_label.setTextFormat(Qt.RichText)
        people_row.addWidget(self.people_label, 1)
        self.only_old = QCheckBox("Only who still needs the update")
        self.only_old.toggled.connect(lambda _on: self.refresh())
        people_row.addWidget(self.only_old)
        self.remind_btn = btn("Remind them", "bell", primary=True)
        self.remind_btn.clicked.connect(self.remind)
        people_row.addWidget(self.remind_btn)
        self.lay.addLayout(people_row)
        self.people = make_table(["Person", "Department", "Quillo", "PC", "Last signed in", "Status"])
        self.people.setMinimumHeight(300)                # the page scrolls on a short screen, not this table
        self.lay.addWidget(self.people, 1)
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
                 f"<span style='color:{T.META}'>No client update in the folder.</span>")
        self.info.setText(f"Updates folder (on the server PC):<br><b>{self.folder}</b><br><br>{offer}<br>"
                          f"<span style='color:{T.META}'>This server and console are version {APP_VERSION}.</span>")
        local = on_server_pc(self.win.api)             # the files live on the server PC
        for b in (self.open_btn, self.publish_btn, self.server_btn):
            b.setEnabled(local)
        self.open_btn.setToolTip("" if local else "Only on the server PC itself")
        self.publish_btn.setToolTip("" if local else "Only on the server PC itself")
        self.server_btn.setToolTip("Run a new Quillo-Server-Setup on this PC (chats, files and settings are kept)"
                                   if local else "Only on the server PC itself")
        # 'behind' is measured against the newest Quillo there is: the offered update, or else this server
        newest = latest["version"] if latest else APP_VERSION
        key = lambda v: tuple(int(x) for x in v.split(".")) if v and v.replace(".", "").isdigit() else ()  # noqa: E731
        behind = lambda v: bool(v) and key(v) < key(newest)                                             # noqa: E731
        rows = sorted(u.get("versions", {}).items(), key=lambda kv: key(kv[0]), reverse=True)
        self.versions.setRowCount(len(rows))
        for r, (ver, count) in enumerate(rows):
            old = behind(ver)
            self.versions.setItem(r, 0, cell(version_text(ver) + ("  (update available)" if old and latest else ""),
                                             color=T.DANGER if old and latest else T.META if old else None))
            self.versions.setItem(r, 1, num(count))
        self.versions.setVisible(bool(rows))
        header = self.versions.horizontalHeader().sizeHint().height()
        self.versions.setFixedHeight(header + len(rows) * self.versions.verticalHeader().defaultSectionSize() + 16)
        # everyone, with the Quillo they last signed in with
        people = u.get("people", [])
        old = [p for p in people if behind(p["version"])]
        current = [p for p in people if p["version"] and not behind(p["version"])]
        unknown = len(people) - len(old) - len(current)
        text = (f"<b style='font-size:11pt'>{len(current)}</b> of {len(people)} "
                f"{'person' if len(people) == 1 else 'people'} {'is' if len(current) == 1 else 'are'} on {newest}")
        if old and latest:
            text += f" &nbsp;·&nbsp; <span style='color:{T.DANGER}'><b>{len(old)}</b> still need the update</span>"
        elif old:
            text += f" &nbsp;·&nbsp; <span style='color:{T.META}'>{len(old)} on an older version</span>"
        if unknown:
            text += f" &nbsp;·&nbsp; <span style='color:{T.META}'>{unknown} not seen yet</span>"
        self.people_label.setText(text)
        can_remind = bool(latest) and any(p["online"] for p in old)
        self.remind_btn.setEnabled(can_remind)
        self.remind_btn.setToolTip(
            "Shows the update bar again on every signed-in PC that is still on an older Quillo" if can_remind else
            "Publish a client update first" if not latest else
            "Nobody who needs the update is signed in right now")
        shown = old if self.only_old.isChecked() else people
        shown = sorted(shown, key=lambda p: (0 if behind(p["version"]) else 2 if p["version"] else 1,
                                             p["name"].lower()))
        self.people.setRowCount(len(shown))
        for r, p in enumerate(shown):
            unknown = not p["version"]
            late = behind(p["version"])
            self.people.setItem(r, 0, cell(p["name"]))
            self.people.setItem(r, 1, cell(p["department"]))
            self.people.setItem(r, 2, cell(version_text(p["version"]), color=T.FAINT if unknown else
                                           T.DANGER if late and latest else T.META if late else None))
            self.people.setItem(r, 3, cell(p["pc"]))
            self.people.setItem(r, 4, cell("Online now" if p["online"] else fmt_time(p["seen"])))
            status = ("—" if unknown else "Needs the update" if late and latest else
                      "Older than this server" if late else "✓ Up to date")
            self.people.setItem(r, 5, cell(status, color=T.FAINT if unknown else T.DANGER if late and latest
                                           else T.META))
        hide_empty_columns(self.people, [3])             # PC: only Quillo 1.9 and later say which PC

    def remind(self):
        try:
            r = self.win.api.call("admin_remind_update")
        except ValueError as e:
            QMessageBox.warning(self, "Remind them", str(e))
            return
        n = r.get("reminded", 0)
        QMessageBox.information(self, "Remind them",
                                f"The update bar is shown again on {n} PC{'s' if n != 1 else ''}." if n else
                                "Nobody who needs the update is signed in right now. They see the update "
                                "the next time they sign in.")

    def publish(self):
        import os
        import re
        import shutil
        path, _ = QFileDialog.getOpenFileName(self, "Choose the new client installer", "",
                                              "Quillo client setup (*Client-Setup-*.exe)")
        if not path:
            return
        name = os.path.basename(path)
        if not re.fullmatch(r"(Quillo|LANMessenger)-Client-Setup-\d+(\.\d+)*\.exe", name, re.I):
            QMessageBox.warning(self, "Publish update", "Choose a file named Quillo-Client-Setup-x.y.z.exe "
                                                        "(from build\\output).")
            return
        os.makedirs(self.folder, exist_ok=True)
        try:
            shutil.copy2(path, os.path.join(self.folder, name))
        except OSError as e:
            QMessageBox.warning(self, "Publish update", f"Could not copy the installer: {e}\n\nWith the "
                                "background service the updates folder is for administrators only — start the "
                                "console with 'Run as administrator', or copy the file there yourself.")
            return
        info = self.win.api.call("admin_check_updates")
        QMessageBox.information(self, "Publish update",
                                f"Version {info['version'] if info else '?'} is now offered to every PC." if info
                                else "The file was copied, but it is not newer than what is offered already.")
        self.refresh()

    def update_server(self):
        import os
        import subprocess
        path, _ = QFileDialog.getOpenFileName(self, "Choose the new server installer", "",
                                              "Quillo server setup (*Server-Setup-*.exe)")
        if not path:
            return
        if not confirm(self, "Update this server", f"Run {os.path.basename(path)} now?", "Run the installer",
                       danger=False, detail="The server stops for a minute while it is updated; people are "
                                            "reconnected by themselves. Chats, files and settings are kept. "
                                            "This console closes."):
            return
        try:
            subprocess.Popen([path], close_fds=True)
        except OSError as e:
            QMessageBox.warning(self, "Update this server", f"Could not start the installer: {e}")
            return
        self.win.quit_for_update()

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
        body.setMaximumWidth(820)
        self.form = form = QFormLayout(body)
        form.setSpacing(12)
        form.setHorizontalSpacing(24)
        form.setContentsMargins(0, 0, 12, 0)
        area.setWidget(body)
        self.lay.addWidget(area, 1)

        def section(text):
            lbl = QLabel(text.upper())
            lbl.setStyleSheet(f"color: {T.MUTED}; font-size: 8pt; font-weight: 700; letter-spacing: 1px;"
                              f" padding-top: 18px;")
            lbl.setIndent(0)                    # a styled label gets an automatic indent: 3 px off the labels
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
        self.max_mb_hint = QLabel()
        self.max_mb_hint.setStyleSheet(f"color: {T.META};")
        self.max_mb.valueChanged.connect(self._show_max_size)
        max_row = QHBoxLayout()
        max_row.addWidget(self.max_mb)
        max_row.addWidget(self.max_mb_hint)
        max_row.addStretch(1)
        self.retention = spin(0, 3650, " days", "Keep forever")
        form.addRow("File storage folder", storage_row)
        form.addRow("Max file size", max_row)
        form.addRow("Delete shared files after", self.retention)
        self.unclaimed = spin(0, 3650, " days", "Never")
        form.addRow("Delete files nobody downloaded after", self.unclaimed)

        log_row = QHBoxLayout()
        self.log_dir = QLineEdit()
        self.log_browse = btn("Browse", "folder")
        self.log_browse.clicked.connect(lambda: self._browse(self.log_dir, "Log folder"))
        log_row.addWidget(self.log_dir, 1)
        log_row.addWidget(self.log_browse)
        form.addRow("Log folder", log_row)

        section("Automatic rooms")
        self.auto_all = QCheckBox("An \"All Studio\" room with everyone")
        form.addRow("", self.auto_all)
        dept_hint = QLabel("Rooms for departments and sections: tick Chat room on the Departments page.")
        T.polish(dept_hint, muted=True)
        form.addRow("", dept_hint)

        section("Passwords")
        self.pw_len = spin(4, 64, " characters")
        self.pw_mix = QCheckBox("Must contain letters and numbers")
        self.pw_weak = QCheckBox("Refuse easy passwords (123456, password, the username...)")
        self.pw_force = QCheckBox("New users and password resets: people must choose their own password at "
                                  "first sign-in")
        self.pw_age = spin(0, 3650, " days", "Never")
        form.addRow("Minimum length", self.pw_len)
        form.addRow("", self.pw_mix)
        form.addRow("", self.pw_weak)
        form.addRow("", self.pw_force)
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

        section("Central folder (for a new server PC)")
        self.sc_enabled = QCheckBox("Keep everything a new server PC needs in a central folder: accounts, chats, "
                                    "settings, the certificate, the chat backup, the user list and the database "
                                    "backups")
        sc_row = QHBoxLayout()
        self.sc_dir = QLineEdit()
        self.sc_browse = btn("Browse", "folder")
        self.sc_browse.clicked.connect(lambda: self._browse(self.sc_dir, "Safe copy folder"))
        sc_row.addWidget(self.sc_dir, 1)
        sc_row.addWidget(self.sc_browse)
        self.sc_minutes = spin(1, 1440, " minutes")
        sc_now = QHBoxLayout()
        self.sc_now = btn("Copy now", "download")
        self.sc_now.clicked.connect(self.safe_copy_now)
        self.sc_status = QLabel()
        self.sc_status.setWordWrap(True)
        T.polish(self.sc_status, muted=True)
        sc_now.addWidget(self.sc_now)
        sc_now.addWidget(self.sc_status, 1)
        form.addRow("", self.sc_enabled)
        form.addRow("Central folder", sc_row)
        form.addRow("Update it every", self.sc_minutes)
        form.addRow("", sc_now)
        sc_hint = QLabel("Empty = 'Quillo server data' inside the shared files folder (when that is on the file "
                         "server or another disk). The copy of the database is updated when something changed and "
                         "when the server stops; the chat backup (Chat backup\\Rooms, Chat backup\\People), the "
                         "user list and the daily database backups go in there too. On a new install, setup finds "
                         "it and offers to restore everything.")
        sc_hint.setWordWrap(True)
        T.polish(sc_hint, muted=True)
        form.addRow("", sc_hint)

        section("Chat backup & history")
        self.cl_enabled = QCheckBox("Write a readable chat backup every night (text files: a folder per room and "
                                    "per person, one file per month)")
        cl_row = QHBoxLayout()
        self.cl_dir = QLineEdit()
        self.cl_browse = btn("Browse", "folder")
        self.cl_browse.clicked.connect(lambda: self._browse(self.cl_dir, "Chat backup folder"))
        cl_row.addWidget(self.cl_dir, 1)
        cl_row.addWidget(self.cl_browse)
        self.msg_days = spin(0, 3650, " days", "Forever")
        cl_now = QHBoxLayout()
        self.cl_now = btn("Back up chats now", "download")
        self.cl_now.clicked.connect(self.chat_backup_now)
        self.cl_status = QLabel()
        self.cl_status.setWordWrap(True)
        T.polish(self.cl_status, muted=True)
        cl_now.addWidget(self.cl_now)
        cl_now.addWidget(self.cl_status, 1)
        form.addRow("", self.cl_enabled)
        form.addRow("Chat backup folder", cl_row)
        form.addRow("Keep messages in the app for", self.msg_days)
        form.addRow("", cl_now)
        cl_hint = QLabel("Empty folder = 'Chat backup' in the central folder. Messages stay in the app for good "
                         "unless you choose a number of days; older ones then live only in the chat backup (a "
                         "message is only removed after it has been written there). The backup runs at the "
                         "database backup time above.")
        cl_hint.setWordWrap(True)
        T.polish(cl_hint, muted=True)
        form.addRow("", cl_hint)

        section("Pipeline API (render farm, scripts)")
        self.api_enabled = QCheckBox("Allow scripts to send messages (needs a server restart)")
        self.api_port = spin(1024, 65535)
        key_row = QHBoxLayout()
        self.api_key = QLineEdit()
        self.api_key.setReadOnly(True)
        self.api_key.setPlaceholderText("No key yet")
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
        self.buzz = QCheckBox("Allow Buzz (shakes the other person's window and rings, even when they are busy)")
        form.addRow("", self.buzz)
        self.trusted = QLineEdit()
        self.trusted.setPlaceholderText("e.g.  fileserver, nas01, render-store")
        form.addRow("Studio file servers", self.trusted)
        trusted_hint = QLabel("Links in chats to these computers open with one click. A link to any other computer "
                              "asks first: opening it lets that computer see the person's Windows sign-in.")
        trusted_hint.setWordWrap(True)
        T.polish(trusted_hint, muted=True)
        form.addRow("", trusted_hint)
        self.rename = QCheckBox("People can change their own display name (in their Profile)")
        form.addRow("", self.rename)

        section("Chats")
        self.shots = QLineEdit()
        self.shots.setPlaceholderText("Empty = off")
        self.shots.setFont(QFont("Consolas", 9))
        shot_row = QHBoxLayout()
        shot_row.addWidget(self.shots, 1)
        shot_reset = QPushButton("Default")
        shot_reset.setToolTip("Catches FAL_030, FAL_030_0010 and SEQ010_SH0020")
        shot_reset.clicked.connect(lambda: self.shots.setText(P.SHOT_PATTERN_DEFAULT))
        shot_row.addWidget(shot_reset)
        form.addRow("Shot names", shot_row)
        self.shot_test = QLineEdit()
        self.shot_test.setPlaceholderText("Try it: type a message, e.g.  FAL_030 comp v12 is up")
        self.shot_result = QLabel()
        T.polish(self.shot_result, muted=True)
        self.shots.textChanged.connect(self._test_shots)
        self.shot_test.textChanged.connect(self._test_shots)
        form.addRow("", self.shot_test)
        form.addRow("", self.shot_result)
        shot_hint = QLabel("Shot names in messages become links: a click shows everything said about that shot, in "
                           "every chat the person can see. A regular expression, for studios whose shots are named "
                           "differently.")
        shot_hint.setWordWrap(True)
        T.polish(shot_hint, muted=True)
        form.addRow("", shot_hint)

        row = QHBoxLayout()
        row.addStretch(1)
        self.unsaved = QLabel("Unsaved changes")
        self.unsaved.setStyleSheet(f"color: {T.readable_on(T.WARN_TEXT, T.BG)}; font-weight: 600;")
        row.addWidget(self.unsaved)
        self.discard_btn = btn("Discard changes", "undo")
        self.discard_btn.setToolTip("Show the saved settings again")
        self.discard_btn.clicked.connect(lambda: self.refresh(force=True))
        row.addWidget(self.discard_btn)
        self.save_btn = btn("Save settings", "check", primary=True)
        self.save_btn.clicked.connect(self.save)
        row.addWidget(self.save_btn)
        self.lay.addLayout(row)
        self.cfg = {}
        # typing in the form marks it changed; the once-a-minute refresh then leaves it alone
        self._dirty = False
        for w in body.findChildren(QLineEdit):
            if w is not self.shot_test:
                w.textChanged.connect(self._changed)
        for w in body.findChildren(QSpinBox):
            w.valueChanged.connect(self._changed)
        for w in body.findChildren(QCheckBox):
            w.toggled.connect(self._changed)
        self._set_dirty(False)

    def _changed(self, *_):
        if not self._filling:
            self._set_dirty(True)

    _filling = False

    def _set_dirty(self, on):
        self._dirty = bool(on)
        self.unsaved.setVisible(self._dirty)
        self.discard_btn.setVisible(self._dirty)
        self.save_btn.setText("Save changes" if self._dirty else "Save settings")

    def _show_max_size(self, mb):
        gb = f"{mb / 1024:.1f}".rstrip("0").rstrip(".")
        self.max_mb_hint.setText(f"= {gb} GB" if mb >= 1024 else "")

    @staticmethod
    def _default_path(edit, path, off_text=""):
        """An empty folder field says which folder is used (it looked disabled with just a dim path)."""
        edit.setPlaceholderText(f"Default: {path}" if path else off_text)
        edit.setToolTip(f"Empty = {path}" if path else "")

    def refresh(self, force=False):
        """Show the saved settings. While the admin has unsaved changes it does nothing (the console refreshes
        every minute, which wiped a half-typed form) unless force=True (Discard changes, after Save)."""
        self._sync_buttons()
        if self._dirty and not force:
            return
        try:
            cfg = self.cfg = self.win.api.config()
        except (ValueError, ConnectionError):
            return
        self._filling = True
        try:
            self._fill(cfg)
        finally:
            self._filling = False
        self._set_dirty(False)

    def _fill(self, cfg):
        self.name.setText(cfg["server_name"])
        self.tcp.setValue(int(cfg["tcp_port"]))
        self.udp.setValue(int(cfg["discovery_port"]))
        self.storage.setText(cfg["storage_dir"])
        self._default_path(self.storage, cfg["_storage_dir"])
        self.log_dir.setText(cfg.get("log_dir", ""))
        self._default_path(self.log_dir, cfg.get("_log_dir", ""))
        self.max_mb.setValue(int(cfg["max_file_mb"]))
        self._show_max_size(self.max_mb.value())
        self.retention.setValue(int(cfg["file_retention_days"]))
        self.unclaimed.setValue(int(cfg.get("unclaimed_file_days", 0)))
        self.api_enabled.setChecked(bool(cfg.get("api_enabled")))
        self.api_port.setValue(int(cfg.get("api_port", 5152)))
        self.api_key.setText(cfg.get("api_key", ""))
        self.api_bot.setText(cfg.get("api_bot_name", "Pipeline Bot"))
        self._update_example()
        self.auto_all.setChecked(bool(cfg["auto_all_room"]))
        self.pw_len.setValue(int(cfg["min_password_length"]))
        self.pw_mix.setChecked(bool(cfg["password_require_mix"]))
        self.pw_weak.setChecked(bool(cfg.get("password_block_weak", False)))
        self.pw_force.setChecked(bool(cfg.get("force_password_change", False)))
        self.pw_age.setValue(int(cfg["password_max_age_days"]))
        self.sc_enabled.setChecked(bool(cfg.get("safe_copy_enabled", True)))
        self.sc_dir.setText(cfg.get("safe_copy_dir", ""))
        self._default_path(self.sc_dir, cfg.get("_safe_copy_dir") or "",
                           "Off — the shared files are in the data folder; choose a folder on another disk or share")
        self.sc_minutes.setValue(int(cfg.get("safe_copy_minutes", 5)))
        self.bk_enabled.setChecked(bool(cfg["backup_enabled"]))
        self.bk_dir.setText(cfg["backup_dir"])
        self._default_path(self.bk_dir, cfg["_backup_dir"])
        self.bk_hour.setValue(int(cfg["backup_hour"]))
        self.bk_keep.setValue(int(cfg["backup_keep"]))
        self.buzz.setChecked(bool(cfg.get("buzz_enabled", True)))
        self.rename.setChecked(bool(cfg.get("allow_name_change", True)))
        self.cl_enabled.setChecked(bool(cfg.get("chat_log_enabled", True)))
        self.cl_dir.setText(cfg.get("chat_log_dir", ""))
        self._default_path(self.cl_dir, cfg.get("_chat_log_dir", ""))
        self.msg_days.setValue(int(cfg.get("message_retention_days", 0)))
        self.trusted.setText(cfg.get("trusted_link_hosts", ""))
        self.shots.setText(cfg.get("shot_code_pattern", ""))

    def _sync_buttons(self):
        # Browse shows THIS PC's folders: fine unless the console manages a server on another PC
        remote = not on_server_pc(self.win.api)
        self.cl_browse.setEnabled(not remote)
        self.cl_now.setEnabled(self.win.api.running)
        self.browse_btn.setEnabled(not remote)       # folders are on the server PC
        self.log_browse.setEnabled(not remote)
        self.bk_browse.setEnabled(not remote)
        self.bk_now.setEnabled(self.win.api.running)

    def _new_key(self):
        import secrets
        if self.api_key.text() and not confirm(self, "New API key", "Replace the current key?", "Replace key",
                                               detail="Scripts using the old key stop working."):
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
            edit.setText(QDir.toNativeSeparators(d))        # //nas/share -> \\nas\share

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

    def safe_copy_now(self):
        try:
            r = self.win.api.call("safe_copy_now")
        except (ValueError, ConnectionError) as e:
            QMessageBox.warning(self, "Safe copy", str(e))
            return
        if r and r.get("ok"):
            self.sc_status.setText(f"Saved in {r['folder']} ({human_size(r.get('size', 0))})")
        else:
            QMessageBox.warning(self, "Safe copy failed", (r or {}).get("error", "Unknown error"))

    def chat_backup_now(self):
        try:
            r = self.win.api.call("chat_backup_now")
        except (ValueError, ConnectionError) as e:
            QMessageBox.warning(self, "Chat backup", str(e))
            return
        if r and r.get("ok"):
            removed = f"; {r['removed']} old message(s) moved out of the app" if r.get("removed") else ""
            self.cl_status.setText(f"{r['messages']} new message(s) written to {r['folder']}{removed}")
        else:
            QMessageBox.warning(self, "Chat backup failed", (r or {}).get("error", "Unknown error"))

    def _test_shots(self):
        rx = P.shot_regex(self.shots.text().strip())
        if self.shots.text().strip() and not rx:
            self.shot_result.setText("Not a valid pattern")
        elif not self.shots.text().strip():
            self.shot_result.setText("Shot links are off")
        else:
            found = rx.findall(self.shot_test.text()) if self.shot_test.text() else []
            found = [f if isinstance(f, str) else f[0] for f in found]
            self.shot_result.setText(("Links: " + ", ".join(found)) if found else
                                     ("No shot name found" if self.shot_test.text() else ""))

    def save(self):
        cfg = self.cfg
        values = dict(
            server_name=self.name.text().strip() or "Studio Messenger", tcp_port=self.tcp.value(),
            discovery_port=self.udp.value(), storage_dir=self.storage.text().strip(),
            log_dir=self.log_dir.text().strip(),
            max_file_mb=self.max_mb.value(), file_retention_days=self.retention.value(),
            auto_all_room=self.auto_all.isChecked(), min_password_length=self.pw_len.value(),
            password_require_mix=self.pw_mix.isChecked(), password_max_age_days=self.pw_age.value(),
            password_block_weak=self.pw_weak.isChecked(), force_password_change=self.pw_force.isChecked(),
            backup_enabled=self.bk_enabled.isChecked(), backup_dir=self.bk_dir.text().strip(),
            backup_hour=self.bk_hour.value(), backup_keep=self.bk_keep.value(),
            safe_copy_enabled=self.sc_enabled.isChecked(), safe_copy_dir=self.sc_dir.text().strip(),
            safe_copy_minutes=self.sc_minutes.value(),
            unclaimed_file_days=self.unclaimed.value(),
            api_enabled=self.api_enabled.isChecked(), api_port=self.api_port.value(),
            api_key=self.api_key.text().strip(), api_bot_name=self.api_bot.text().strip() or "Pipeline Bot",
            chat_log_enabled=self.cl_enabled.isChecked(), chat_log_dir=self.cl_dir.text().strip(),
            message_retention_days=self.msg_days.value(), buzz_enabled=self.buzz.isChecked(),
            allow_name_change=self.rename.isChecked(), trusted_link_hosts=self.trusted.text().strip(),
            shot_code_pattern=self.shots.text().strip())
        if values["shot_code_pattern"] and not P.shot_regex(values["shot_code_pattern"]):
            QMessageBox.warning(self, "Shot names", "The shot name pattern is not a valid regular expression. "
                                "Click Default, or leave it empty to switch shot links off.")
            return
        if (values["message_retention_days"] and not values["chat_log_enabled"]
                and QMessageBox.question(self, "Chat history",
                                         "The nightly chat backup is off, so messages older than "
                                         f"{values['message_retention_days']} days will stay in the app until it "
                                         "is switched on (nothing is ever deleted without being backed up "
                                         "first).\n\nSave anyway?") != QMessageBox.Yes):
            return
        if values["api_enabled"] and not values["api_key"]:
            QMessageBox.warning(self, "Pipeline API", "Create an API key first (\"New key\").")
            return
        restart = any(values[k] != cfg.get(k) for k in ("tcp_port", "discovery_port", "storage_dir", "log_dir",
                                                        "api_enabled", "api_port"))
        try:
            self.cfg = self.win.api.update_config(**values)
        except (ValueError, ConnectionError) as e:
            QMessageBox.warning(self, "Settings", str(e))
            return
        self._set_dirty(False)
        self.refresh(force=True)
        if restart and self.win.api.running and not self.win.api.remote and QMessageBox.question(
                self, "Restart server", "Restart the server now to apply the changes? "
                "Connected clients will reconnect automatically.") == QMessageBox.Yes:
            self.win.restart_server()
        elif restart and self.win.api.remote:
            QMessageBox.information(self, "Saved", "Settings saved. Restart the server service to apply the "
                                    "port and storage changes.")
        else:
            QMessageBox.information(self, "Saved", "Settings saved.")


class AuditPage(Page):
    def __init__(self, win):
        super().__init__("Audit log", "Every administrative action: who did what, and when. "
                                      "Sign-in lockouts and backup failures are recorded too.")
        self.win = win
        bar = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Filter by person, action or detail")
        self.search.addAction(icon("search", T.FAINT, 16), QLineEdit.LeadingPosition)
        self.search.setClearButtonEnabled(True)
        # the list follows the typing (after a short pause), no Enter needed
        self._filter_timer = QTimer(self, singleShot=True, interval=300, timeout=self.refresh)
        self.search.textChanged.connect(lambda _t: self._filter_timer.start())
        self.search.returnPressed.connect(self.refresh)
        export = btn("Export CSV", "download")
        export.clicked.connect(self.export)
        bar.addWidget(self.search, 1)
        bar.addWidget(export)
        self.lay.addLayout(bar)
        self.table = make_table(["When", "Who", "Action", "Target", "Details"])
        self.lay.addWidget(self.table, 1)
        self.rows = []

    @staticmethod
    def sentence(text):
        """'server console' -> 'Server console', 'user created' -> 'User created' (usernames stay as they are)."""
        text = str(text or "")
        return text[:1].upper() + text[1:] if " " in text else text

    _RAW = re.compile(r"^[a-z_]+=")
    _CHANGE = re.compile(r"^([a-z_]+): (.*) -> (.*)$")
    _LABELS = {"display_name": "Name", "department": "Department", "section": "Section", "role_id": "Designation",
               "manager_id": "Reports to", "title": "Job title", "is_admin": "Administrator",
               "employee_id": "Employee ID", "birthday": "Birthday", "joined_on": "Joining date",
               "can_broadcast": "Can send announcements", "username": "Username"}

    def readable(self, e, lookup):
        """Details written by older servers ('username=ananya, role_id=63, manager_id=11') in words, with names
        instead of database ids. Newer servers write them this way already."""
        details = str(e["details"] or "")
        if e["action"] == "announcement sent" and details == "all":
            return "Everyone in the studio"
        if e["action"] == "user created" and self._RAW.match(details):
            fields = dict(p.partition("=")[::2] for p in re.split(r", (?=[a-z_]+=)", details))
            parts = [fields.get(k, "") for k in ("display_name", "department", "section")]
            parts.append(lookup("role_id", fields.get("role_id")) if fields.get("role_id") else "")
            if fields.get("manager_id"):
                parts.append("reports to " + lookup("manager_id", fields["manager_id"]))
            if fields.get("is_admin") not in (None, "", "0", "False"):
                parts.append("administrator")
            return SEP.join(p for p in parts if p)
        if e["action"] == "user changed" and " -> " in details:
            out = []
            for piece in details.split("; "):
                m = self._CHANGE.match(piece)
                if not m:
                    out.append(piece)
                    continue
                k, old, new = m.groups()
                out.append(f"{self._LABELS.get(k, k)}: {lookup(k, old)} → {lookup(k, new)}")
            return SEP.join(out)
        return details

    def _lookup(self):
        """lookup(field, value): a stored value as a word ('63' as a role -> 'Compositor'); read once per refresh."""
        cache = {}

        def names(kind):
            if kind not in cache:
                try:
                    if kind == "role_id":
                        cache[kind] = {str(r["id"]): r["name"] for r in self.win.api.call("admin_roles")}
                    else:
                        cache[kind] = {str(u["id"]): u["display_name"] for u in self.win.api.call("admin_users")}
                except (ValueError, ConnectionError, RuntimeError):
                    cache[kind] = {}
            return cache[kind]

        def lookup(key, value):
            value = str(value if value is not None else "").strip().strip("'\"")
            if value in ("", "None"):
                return "nobody" if key == "manager_id" else "none"
            if key in ("role_id", "manager_id"):
                return names(key).get(value, f"#{value}")
            return value
        return lookup

    def refresh(self):
        if not self.win.api.running:
            return
        self.rows = self.win.api.call("admin_audit", self.search.text().strip(), 2000)
        lookup = self._lookup()
        self.table.setRowCount(len(self.rows))
        for r, e in enumerate(self.rows):
            danger = any(w in e["action"] for w in ("deleted", "disabled", "locked", "failed", "review"))
            e["shown"] = self.readable(e, lookup)
            self.table.setItem(r, 0, cell(fmt_time(e["ts"]), tip=iso_time(e["ts"])))
            self.table.setItem(r, 1, cell(self.sentence(e["actor"])))
            self.table.setItem(r, 2, cell(self.sentence(e["action"]), color=T.DANGER if danger else None))
            self.table.setItem(r, 3, cell(e["target"]))
            self.table.setItem(r, 4, cell(e["shown"], tip=e["shown"] if len(e["shown"]) > 30 else None))

    def export(self):
        path, _ = QFileDialog.getSaveFileName(self, "Export audit log", "audit_log.csv", "CSV files (*.csv)")
        if path:
            with open(path, "w", newline="", encoding="utf-8-sig") as f:
                w = csv.writer(f)
                w.writerow(["When", "Who", "Action", "Target", "Details"])
                for e in self.rows:
                    w.writerow([iso_time(e["ts"]), self.sentence(e["actor"]), self.sentence(e["action"]), e["target"],
                                e.get("shown", e["details"])])


class LogPage(Page):
    def __init__(self, win):
        remote = win.api.remote
        super().__init__("Server log", "The last lines of the server's log file, read again when this page opens."
                         if remote else "Live messages from the server since this console started. Older logs "
                                        "are in the log folder.")
        self.win = win
        self.view = QPlainTextEdit()
        self.view.setReadOnly(True)
        self.view.setMaximumBlockCount(5000)
        self.view.setFont(QFont("Consolas", 9))
        self.view.setPlaceholderText("Nothing logged yet")
        self.view.setObjectName("logview")
        self.view.setStyleSheet(f"#logview {{ background: {T.PANEL}; border: none; border-radius: 18px;"
                                f" padding: 10px 12px; }}")
        self.lay.addWidget(self.view, 1)
        row = QHBoxLayout()
        self.open_btn = btn("Open log folder", "folder")
        self.open_btn.clicked.connect(self.open_folder)
        copy = btn("Copy all", "copy")
        copy.setToolTip("Copy every line shown here, e.g. to send it to IT")
        copy.clicked.connect(lambda: QApplication.clipboard().setText(self.view.toPlainText()))
        row.addWidget(self.open_btn)
        row.addWidget(copy)
        row.addStretch(1)
        self.lay.addLayout(row)
        local = on_server_pc(win.api)
        self.open_btn.setEnabled(local)
        self.open_btn.setToolTip("" if local else "Only on the server PC itself")

    def append(self, line):
        self.view.appendPlainText(line)

    def log_folder(self):
        cfg = self.win.api.config()
        return cfg.get("log_dir") or cfg.get("_log_dir") or ""

    def open_folder(self):
        try:
            folder = self.log_folder()
            os.makedirs(folder, exist_ok=True)
            os.startfile(folder)
        except (OSError, ValueError, ConnectionError) as e:
            QMessageBox.warning(self, "Log folder", f"Could not open the log folder: {e}")

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
        self.setWindowTitle("Quillo Server console")
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
        add_show_password(self.pw)
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
        self.error.setText("Connecting" + ELLIPSIS)
        QApplication.processEvents()
        err = api.connect(self.user.text().strip(), self.pw.text())
        if err and api.pin_mismatch:
            old_fp, new_fp = api.pin_mismatch
            box = QMessageBox(self)
            box.setIcon(QMessageBox.Warning)
            box.setWindowTitle("Security warning")
            box.setText(f"The server at {api.label} is not the one this PC connected to before.")
            box.setInformativeText(
                "This is expected ONLY if the messenger server was reinstalled or replaced.\n"
                "Otherwise another computer may be pretending to be the server — don't connect.\n\n"
                f"Remembered: {old_fp[:47]}{ELLIPSIS}\nNow:        {new_fp[:47]}{ELLIPSIS}")
            trust = box.addButton("The server was replaced — trust it", QMessageBox.AcceptRole)
            box.addButton("Don't connect", QMessageBox.RejectRole)
            box.exec()
            if box.clickedButton() is not trust:
                self.error.setText("Not connected: the server's identity could not be confirmed.")
                return
            err = api.connect(self.user.text().strip(), self.pw.text(), trust=new_fp)
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
        add_show_password(e)
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
        self.setWindowTitle("Quillo Server" + (f" — {api.label}" if api.remote else ""))
        self.setWindowIcon(self._app_icon())
        self.resize(1140, 740)
        self.setMinimumSize(920, 580)

        root = QWidget()
        root.setObjectName("root")
        root.setStyleSheet(console_style())
        self.setCentralWidget(root)
        lay = QHBoxLayout(root)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        # left navigation
        nav = QFrame()
        nav.setFixedWidth(220)
        nav.setObjectName("nav")
        nav.setStyleSheet(f"#nav {{ background: {T.PANEL}; }}")
        nl = QVBoxLayout(nav)
        nl.setContentsMargins(14, 18, 14, 12)
        nl.setSpacing(4)
        brand_row = QHBoxLayout()
        brand_row.setSpacing(10)
        from common.icons import logo_widget
        brand_row.addWidget(logo_widget(40))
        brand = QLabel(f"<span style='font-size:12pt; font-weight:600'>Quillo</span><br>"
                       f"<span style='color:{T.MUTED}; font-size:9pt'>Server console</span>")
        brand_row.addWidget(brand, 1)
        nl.addLayout(brand_row)
        nl.addSpacing(8)

        self.stack = QStackedWidget()
        self.users_page = UsersPage(self)
        self.log_page = LogPage(self)
        self.pages = [
            ("Dashboard", "dashboard", DashboardPage(self)),
            ("Users", "users", self.users_page),
            ("Departments", "folder", DepartmentsPage(self)),
            ("Designations", "badge", RolesPage(self)),
            ("Org chart", "org", OrgPage(self)),
            ("Rooms", "hash", RoomsPage(self)),
            ("Holidays", "sun", HolidaysPage(self)),
            ("Online now", "signal", OnlinePage(self)),
            ("Announcements", "megaphone", AnnouncePage(self)),
            ("Reports", "chart", ReportsPage(self)),
            ("Audit log", "list", AuditPage(self)),
            ("Updates", "download", UpdatesPage(self)),
            ("Storage", "hdd", StoragePage(self)),
            ("Settings", "settings", SettingsPage(self)),
            ("Server log", "file", self.log_page),
        ]
        self.nav_group = QButtonGroup(self)
        # the menu scrolls on a short screen instead of squashing its buttons
        nav_list = QWidget()
        nav_list.setStyleSheet("background: transparent;")
        nll = QVBoxLayout(nav_list)
        nll.setContentsMargins(0, 0, 0, 0)
        nll.setSpacing(1)
        nav_scroll = QScrollArea()
        nav_scroll.setWidgetResizable(True)
        nav_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        nav_scroll.setFrameShape(QFrame.NoFrame)
        nav_scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        nav_scroll.setWidget(nav_list)
        groups = [("Overview", ("Dashboard", "Online now", "Reports")),
                  ("People", ("Users", "Departments", "Designations", "Org chart")),
                  ("Messaging", ("Rooms", "Announcements", "Holidays")),
                  ("System", ("Settings", "Storage", "Updates", "Audit log", "Server log"))]
        index = {title: i for i, (title, _ic, _page) in enumerate(self.pages)}
        order = [t for _g, titles in groups for t in titles if t in index]
        order += [t for t, _ic, _p in self.pages if t not in order]        # anything new still shows up
        heading_before = {titles[0]: g for g, titles in groups}
        buttons = {}
        for i, (title, ic, page) in enumerate(self.pages):
            b = QPushButton("  " + title)
            b.setIcon(icon(ic, T.MUTED, 18, active_color=T.ACCENT))
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.setStyleSheet(f"""
                QPushButton {{ text-align: left; background: transparent; color: {T.MUTED}; border: none;
                               padding: 6px 12px; border-radius: 12px; font-weight: 500; }}
                QPushButton:hover {{ background: {T.SURFACE}; color: {T.TEXT}; }}
                QPushButton:checked {{ background: {T.ACCENT_SOFT}; color: {T.TEXT}; font-weight: 600; }}""")
            b.setMinimumHeight(31)
            self.nav_group.addButton(b, i)
            buttons[title] = b
            self.stack.addWidget(page)
        for title in order:
            if title in heading_before:
                head = QLabel(heading_before[title].upper())
                head.setStyleSheet(f"color: {T.FAINT}; font-size: 7.5pt; font-weight: 700; letter-spacing: 1px;"
                                   f" padding: {'2' if title == order[0] else '10'}px 12px 3px 12px;")
                nll.addWidget(head)
            nll.addWidget(buttons[title])
        nll.addStretch(1)
        self.nav_group.idClicked.connect(self.show_page)
        nl.addWidget(nav_scroll, 1)

        status = QFrame()
        status.setObjectName("status")
        status.setStyleSheet(f"#status {{ background: {T.SURFACE}; border-radius: 16px; }}")
        sl = QVBoxLayout(status)
        sl.setContentsMargins(14, 12, 14, 12)
        sl.setSpacing(8)
        self.state_label = QLabel()
        self.state_label.setWordWrap(True)
        self.state_label.setStyleSheet("background: transparent;")
        sl.addWidget(self.state_label)
        self.toggle_btn = btn("Stop server", "power")
        self.toggle_btn.setStyleSheet(f"QPushButton {{ background: {T.PANEL}; border: none; border-radius: 10px;"
                                      f" padding: 6px 10px; font-weight: 600; }}"
                                      f"QPushButton:hover {{ background: {T.SURFACE_HOVER}; }}")
        self.toggle_btn.clicked.connect(self.toggle_server)
        sl.addWidget(self.toggle_btn)
        self.toggle_btn.setVisible(not api.remote)
        nl.addSpacing(8)
        nl.addWidget(status)
        from common.icons import license_label
        nl.addWidget(license_label(align=Qt.AlignLeft, wrap=True))

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
        self.timer.timeout.connect(self._tick)
        self.timer.start(5000)
        self._refreshed = 0.0

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
        self.tray.setToolTip("Quillo Server" + (" console" if self.api.remote else ""))
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
        button = self.nav_group.button(i)
        if button and not button.isChecked():
            button.setChecked(True)
        self.refresh_current()

    def _tick(self):
        """Every 5 s: live pages refresh; the rest once a minute (they also refresh when opened or changed)."""
        page = self.stack.currentWidget()
        if getattr(page, "LIVE", False) or time.time() - self._refreshed >= 60:
            self.refresh_current()
        else:
            self.update_state()

    def refresh_current(self):
        self._refreshed = time.time()
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
        if isinstance(page, (OnlinePage, DashboardPage, UsersPage, DepartmentsPage)):
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
            self.state_label.setText(f"<span style='color:{T.STATUS_COLORS['online']}'>●</span>&nbsp; <b>Running</b><br>"
                                     f"<span style='color:{T.MUTED}; font-size:8.5pt'>{', '.join(local_ips())}"
                                     f" : {self.core.config['tcp_port']}</span>")
            self.toggle_btn.setText("Stop server")
        else:
            self.state_label.setText(f"<span style='color:{T.DANGER}'>●</span>&nbsp; <b>Stopped</b>")
            self.toggle_btn.setText("Start server")

    def toggle_server(self):
        if self.core.running:
            if not confirm(self, "Stop server", "Stop the server?", "Stop server",
                           detail="Everyone is disconnected until it is started again."):
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
        self.tray.showMessage("Quillo Server", "The server keeps running in the background. "
                              "Right-click the tray icon to quit.", QSystemTrayIcon.Information, 3000)

    def quit_for_update(self):
        """A server installer was started: get out of its way (it replaces this program's files)."""
        self.quitting = True
        if not self.api.remote:
            self.core.stop()
        self.tray.hide()
        QApplication.quit()

    def quit(self):
        if self.api.remote:
            self.close()
            return
        if not confirm(self, "Quit server", "Stop the server and quit?", "Quit server",
                       detail="Everyone is disconnected until the server is started again."):
            return
        self.quitting = True
        self.core.stop()
        self.tray.hide()
        QApplication.quit()
