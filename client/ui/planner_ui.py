"""Reminders and scheduled messages: time picker, reminder pop-up, schedule dialog."""

import datetime
import time

from PySide6.QtCore import QDateTime, Qt, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QComboBox, QDateTimeEdit, QDialog, QDialogButtonBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QMenu,
    QPlainTextEdit, QPushButton, QSpinBox, QVBoxLayout,
)

from common import theme as T
from common.icons import icon, pixmap
from client.ui.widgets import plain


# ------------------------------------------------------------------ times
def _at(day, hour, minute=0):
    return datetime.datetime.combine(day, datetime.time(hour, minute)).timestamp()


def presets():
    """[(label, timestamp)] for quick picks, soonest first."""
    now = datetime.datetime.now()
    today, tomorrow = now.date(), now.date() + datetime.timedelta(days=1)
    out = [("In 1 minute", time.time() + 60), ("In 5 minutes", time.time() + 5 * 60),
           ("In 20 minutes", time.time() + 20 * 60), ("In 1 hour", time.time() + 3600),
           ("In 3 hours", time.time() + 3 * 3600)]
    if now.hour < 17:
        out.append(("This evening, 18:00", _at(today, 18)))
    out.append(("Tomorrow, 09:00", _at(tomorrow, 9)))
    monday = today + datetime.timedelta(days=(7 - today.weekday()) or 7)
    out.append(("Next Monday, 09:00", _at(monday, 9)))
    return out


def weekday_picks(days=10):
    """The next working days at 09:00 (from the day after tomorrow): 'Mon 29 Sep', ..."""
    today = datetime.date.today()
    out = []
    for i in range(2, 2 + days):
        d = today + datetime.timedelta(days=i)
        if d.weekday() < 5:
            out.append((f"{d:%a %d %b}", _at(d, 9)))
    return out


def fmt_due(ts):
    """'Today 18:00', 'Tomorrow 09:00', 'Mon 29 Sep 09:00'."""
    d = datetime.datetime.fromtimestamp(ts)
    today = datetime.date.today()
    if d.date() == today:
        return f"Today {d:%H:%M:%S}" if d.second else f"Today {d:%H:%M}"
    if d.date() == today + datetime.timedelta(days=1):
        return f"Tomorrow {d:%H:%M}"
    if d.date().year == today.year:
        return f"{d:%a %d %b %H:%M}"
    return f"{d:%d %b %Y %H:%M}"


def when_menu(parent, title, on_pick, custom_title="Pick a date & time..."):
    """Menu of preset times plus 'pick a date & time'; calls on_pick(timestamp)."""
    m = QMenu(title, parent)
    m.setIcon(icon("clock", T.TEXT, 16))
    for label, ts in presets():
        m.addAction(label, lambda ts=ts: on_pick(ts))
    later = m.addMenu("Another day, 09:00")
    for label, ts in weekday_picks():
        later.addAction(label, lambda ts=ts: on_pick(ts))
    m.addSeparator()

    def custom():
        ts = TimeDialog.ask(parent, title)
        if ts:
            on_pick(ts)
    m.addAction(icon("clock", T.TEXT, 16), custom_title, custom)
    return m


class TimeDialog(QDialog):
    """Quick picks + a date/time field."""

    def __init__(self, parent, title, initial=None, text=None, text_label="", ok_text="Set"):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(420)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 20, 22, 18)
        lay.setSpacing(10)
        self.text = None
        if text is not None:
            lab = QLabel(text_label)
            lab.setStyleSheet("font-weight: 700;")
            lay.addWidget(lab)
            self.text = QPlainTextEdit(text)
            self.text.setFixedHeight(90)
            lay.addWidget(self.text)
        when = QLabel("When")
        when.setStyleSheet("font-weight: 700;")
        lay.addWidget(when)
        grid = QGridLayout()
        grid.setSpacing(6)
        for i, (label, ts) in enumerate(presets()):
            b = QPushButton(label)
            b.setCursor(Qt.PointingHandCursor)
            b.setStyleSheet("text-align: left; font-weight: 400; padding: 7px 10px;")
            b.clicked.connect(lambda _=False, ts=ts: self.edit.setDateTime(QDateTime.fromSecsSinceEpoch(int(ts))))
            grid.addWidget(b, i // 2, i % 2)
        lay.addLayout(grid)
        self.typed = QLineEdit()
        self.typed.setPlaceholderText("Or type it: mon 9:30,  tomorrow 2pm,  in 2h")
        self.typed.setMinimumHeight(36)
        self.typed.addAction(icon("clock", T.FAINT, 16), QLineEdit.LeadingPosition)
        self.typed.textChanged.connect(self._typed)
        lay.addWidget(self.typed)
        # "in [ 30 ] [seconds v]" - any delay, to the second
        after = QHBoxLayout()
        after.setSpacing(6)
        after.addWidget(QLabel("In"))
        self.after_n = QSpinBox()
        self.after_n.setRange(1, 9999)
        self.after_n.setValue(30)
        self.after_n.setMinimumHeight(34)
        after.addWidget(self.after_n)
        self.after_unit = QComboBox()
        for label, secs in (("seconds", 1), ("minutes", 60), ("hours", 3600), ("days", 86400)):
            self.after_unit.addItem(label, secs)
        self.after_unit.setCurrentIndex(1)
        self.after_unit.setMinimumHeight(34)
        after.addWidget(self.after_unit)
        use = QPushButton("Use")
        use.clicked.connect(self._use_after)
        after.addWidget(use)
        after.addStretch(1)
        lay.addLayout(after)
        self.typed_hint = QLabel()
        self.typed_hint.setStyleSheet(f"color: {T.MUTED}; font-size: 8.5pt; padding-left: 4px;")
        self.typed_hint.hide()
        lay.addWidget(self.typed_hint)
        self.edit = QDateTimeEdit(QDateTime.fromSecsSinceEpoch(int(initial or time.time() + 3600)))
        self.edit.setCalendarPopup(True)
        self.edit.setDisplayFormat("ddd dd MMM yyyy   HH:mm:ss")
        self.edit.setMinimumDateTime(QDateTime.currentDateTime())
        self.edit.setMinimumHeight(36)
        lay.addWidget(self.edit)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        ok = bb.button(QDialogButtonBox.Ok)
        ok.setText(ok_text)
        T.polish(ok, primary=True)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def showEvent(self, e):
        super().showEvent(e)
        T.dark_title_bar(self)

    def _use_after(self):
        ts = time.time() + self.after_n.value() * self.after_unit.currentData()
        self.edit.setDateTime(QDateTime.fromSecsSinceEpoch(int(round(ts))))
        self.typed_hint.setText(f"✓  {fmt_due(ts)}")
        self.typed_hint.setStyleSheet(f"color: {T.ACCENT}; font-size: 8.5pt; padding-left: 4px;")
        self.typed_hint.show()

    def _typed(self, text):
        from client.when import parse_when
        ts = parse_when(text) if text.strip() else None
        self.typed_hint.setVisible(bool(text.strip()))
        if ts:
            self.edit.setDateTime(QDateTime.fromSecsSinceEpoch(int(ts)))
            self.typed_hint.setText(f"✓  {fmt_due(ts)}")
            self.typed_hint.setStyleSheet(f"color: {T.ACCENT}; font-size: 8.5pt; padding-left: 4px;")
        else:
            self.typed_hint.setText("Not sure when that is - try 'fri 10:00' or 'in 3 days'")
            self.typed_hint.setStyleSheet(f"color: {T.MUTED}; font-size: 8.5pt; padding-left: 4px;")

    def timestamp(self):
        return max(self.edit.dateTime().toSecsSinceEpoch(), time.time() + 1)

    def message(self):
        return self.text.toPlainText().strip() if self.text else ""

    @classmethod
    def ask(cls, parent, title, initial=None):
        dlg = cls(parent, title, initial)
        return dlg.timestamp() if dlg.exec() else None


# ------------------------------------------------------------ reminder pop-up
class ReminderPopup(QFrame):
    """Card in the bottom-right corner of the screen, above other windows, until Done or Snooze."""
    done = Signal(int)
    snooze = Signal(int, float)            # reminder id, new due time
    open_chat = Signal(str)

    def __init__(self, ctx, reminder):
        super().__init__(None, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.ctx, self.r = ctx, reminder
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setObjectName("remcard")
        self.setStyleSheet(f"#remcard {{ background: {T.PANEL}; border: 1px solid {T.ACCENT_FOCUS};"
                           " border-radius: 16px; }")
        self.setFixedWidth(380)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 14, 14, 14)
        lay.setSpacing(8)
        head = QHBoxLayout()
        ic = QLabel()
        ic.setPixmap(pixmap("clock", T.ACCENT, 20))
        head.addWidget(ic)
        t = QLabel("Reminder")
        t.setStyleSheet(f"color: {T.ACCENT}; font-weight: 800; font-size: 10pt;")
        head.addWidget(t)
        when = QLabel(fmt_due(reminder["due_at"]))
        when.setStyleSheet(f"color: {T.MUTED}; font-size: 8.5pt;")
        head.addWidget(when, 1, Qt.AlignRight)
        lay.addLayout(head)
        if reminder.get("text"):
            text = plain(QLabel(reminder["text"]))
            text.setWordWrap(True)
            text.setStyleSheet("font-size: 11pt; font-weight: 700;")
            lay.addWidget(text)
        if reminder.get("snippet"):
            q = plain(QLabel(f"{reminder.get('sender_name', '')}: {reminder['snippet'][:160]}"))
            q.setWordWrap(True)
            q.setStyleSheet(f"background: {T.TINT}; border-left: 3px solid {T.ACCENT}; border-radius: 6px;"
                            f" padding: 6px 8px; color: {T.MUTED};")
            lay.addWidget(q)
        conv = reminder.get("conv")
        if conv and ctx.store.conv_exists(conv):
            where = QLabel(f"in {ctx.store.title(conv)}")
            where.setStyleSheet(f"color: {T.MUTED}; font-size: 8.5pt;")
            lay.addWidget(where)
        row = QHBoxLayout()
        row.setSpacing(6)
        if conv and ctx.store.conv_exists(conv):
            op = QPushButton("Open chat")
            op.clicked.connect(lambda: (self.open_chat.emit(conv), self.done.emit(self.r["id"]), self.close()))
            row.addWidget(op)
        sn = QPushButton("Snooze")
        sm = QMenu(sn)
        for label, secs in (("10 minutes", 600), ("1 hour", 3600), ("3 hours", 3 * 3600)):
            sm.addAction(label, lambda secs=secs: self._snooze(time.time() + secs))
        sm.addAction("Tomorrow, 09:00", lambda: self._snooze(_at(datetime.date.today()
                                                                 + datetime.timedelta(days=1), 9)))
        sn.setMenu(sm)
        row.addWidget(sn)
        row.addStretch(1)
        ok = QPushButton("Done")
        T.polish(ok, primary=True)
        ok.clicked.connect(lambda: (self.done.emit(self.r["id"]), self.close()))
        row.addWidget(ok)
        lay.addLayout(row)

    def _snooze(self, ts):
        self.snooze.emit(self.r["id"], ts)
        self.close()

    def show_at(self, index=0):
        """Stack cards up from the bottom-right corner of the primary screen."""
        self.adjustSize()
        area = QGuiApplication.primaryScreen().availableGeometry()
        self.move(area.right() - self.width() - 16, area.bottom() - (self.height() + 12) * (index + 1) - 4)
        self.show()
        self.raise_()
