"""Reminders and scheduled messages: time picker, reminder pop-up, schedule dialog."""

import datetime
import time

from PySide6.QtCore import QDateTime, Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup, QComboBox, QDateTimeEdit, QDialog, QDialogButtonBox, QFrame, QGridLayout, QHBoxLayout, QLabel,
    QLineEdit, QMenu, QPlainTextEdit, QPushButton, QSpinBox, QVBoxLayout, QWidget,
)

from common import theme as T
from common.icons import icon, pixmap
from client.ui.widgets import (
    ELLIPSIS, ElidedLabel, clip, day_word, fmt_date, fmt_time, fmt_when, menu_text, plain,
)


# ------------------------------------------------------------------ times
def _at(day, hour, minute=0):
    return datetime.datetime.combine(day, datetime.time(hour, minute)).timestamp()


def presets(now=None):
    """[(label, timestamp)] for quick picks, soonest first."""
    now = now or datetime.datetime.now()
    t = now.timestamp()
    today, tomorrow = now.date(), now.date() + datetime.timedelta(days=1)
    out = [("In 10 minutes", t + 10 * 60), ("In 20 minutes", t + 20 * 60), ("In 1 hour", t + 3600),
           ("In 3 hours", t + 3 * 3600)]
    if now.hour < 8:                        # after midnight: "tomorrow" would be the day after the coming morning
        out.append(("This morning, 09:00", _at(today, 9)))
    if now.hour < 17:
        out.append(("This evening, 18:00", _at(today, 18)))
    out.append(("Tomorrow, 09:00", _at(tomorrow, 9)))
    monday = today + datetime.timedelta(days=(7 - today.weekday()) or 7)
    if monday != tomorrow:                  # on a Sunday that is the same pick twice
        out.append(("Next Monday, 09:00", _at(monday, 9)))
    return out


def weekday_picks(days=10):
    """The next working days at 09:00 (from the day after tomorrow): 'Mon 29 Sep', ..."""
    today = datetime.date.today()
    out = []
    for i in range(2, 2 + days):
        d = today + datetime.timedelta(days=i)
        if d.weekday() < 5:
            out.append((fmt_date(d), _at(d, 9)))
    return out


def fmt_due(ts, seconds=False, inline=False):
    """'Today 18:00', 'Tomorrow 09:00', 'Mon 29 Sep, 09:00' (fmt_when).

    seconds=True: '… 18:00:30' when the time isn't on a whole minute (the time picker's check line only; a fired
    reminder or a list shows minutes). inline=True: for the middle of a sentence - 'tomorrow at 09:00',
    'Mon 29 Sep at 09:00' ("Reminder set for tomorrow at 09:00")."""
    d = datetime.datetime.fromtimestamp(ts)
    clock = f"{d:%H:%M:%S}" if seconds and d.second else fmt_time(d)
    if inline:
        word = day_word(d)
        return f"{word.lower() if word else fmt_date(d)} at {clock}"
    if seconds and d.second:
        word = day_word(d)
        return f"{word} {clock}" if word else f"{fmt_date(d)}, {clock}"
    return fmt_when(d)


def when_menu(parent, title, on_pick, custom_title="Pick a date & time" + ELLIPSIS):
    """Menu of preset times plus 'pick a date & time'; calls on_pick(timestamp)."""
    m = QMenu(menu_text(title), parent)
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
    # a caller may still pass the text already escaped ('&&'): don't double it again
    label = custom_title if "&&" in custom_title else menu_text(custom_title)
    m.addAction(icon("calendar", T.TEXT, 16), label.replace("...", ELLIPSIS), custom)
    return m


def _hint_style(color):
    return f"color: {color}; font-size: {T.pt(T.FONT_S)}; padding-left: 4px;"


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
        self._own_change = False
        if text is not None:
            lab = plain(QLabel(text_label))
            lab.setStyleSheet("font-weight: 700;")
            lay.addWidget(lab)
            self.text = QPlainTextEdit(text)
            self.text.setFixedHeight(90)
            lay.addWidget(self.text)
        when = QLabel("When")
        when.setStyleSheet("font-weight: 700;")
        lay.addWidget(when)
        # quick picks: chips (they look like buttons, not fields), the chosen one stays lit
        grid = QGridLayout()
        grid.setSpacing(6)
        self.picks = QButtonGroup(self)
        self.picks.setExclusive(True)
        picks = presets()
        for i, (label, ts) in enumerate(picks):
            b = QPushButton(label)
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            T.polish(b, chip=True, tall=True)
            b.clicked.connect(lambda _=False, ts=ts: self._pick(ts))
            self.picks.addButton(b)
            last_alone = i == len(picks) - 1 and len(picks) % 2
            grid.addWidget(b, i // 2, 0 if last_alone else i % 2, 1, 2 if last_alone else 1)
        lay.addLayout(grid)
        self.typed = QLineEdit()
        self.typed.setPlaceholderText("Or type it: mon 9:30, tomorrow 2pm, in 2h")
        self.typed.setMinimumHeight(36)
        self.typed.addAction(icon("time", T.META, 16), QLineEdit.LeadingPosition)
        self.typed.textChanged.connect(self._typed)
        lay.addWidget(self.typed)
        self.typed_hint = plain(QLabel())            # right under the field it describes
        self.typed_hint.setStyleSheet(_hint_style(T.MUTED))
        self.typed_hint.hide()
        lay.addWidget(self.typed_hint)
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
        self.after_n.valueChanged.connect(self._clear_picks)         # the In row is being set up instead
        self.after_unit.currentIndexChanged.connect(self._clear_picks)
        after.addWidget(use)
        after.addStretch(1)
        lay.addLayout(after)
        self.edit = QDateTimeEdit(QDateTime.fromSecsSinceEpoch(int(initial or time.time() + 3600)))
        self.edit.setCalendarPopup(True)
        self.edit.setDisplayFormat("ddd d MMM yyyy, HH:mm")
        self.edit.setMinimumDateTime(QDateTime.currentDateTime())
        self.edit.setMinimumHeight(36)
        self.edit.dateTimeChanged.connect(self._edited)
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

    # ---- the three ways to choose, and the one line that says what was chosen
    def _set_time(self, ts):
        self._own_change = True
        try:
            self.edit.setDateTime(QDateTime.fromSecsSinceEpoch(int(round(ts))))
        finally:
            self._own_change = False

    def _clear_picks(self, *_):
        checked = self.picks.checkedButton()
        if checked:
            self.picks.setExclusive(False)
            checked.setChecked(False)
            self.picks.setExclusive(True)

    def _confirm(self, ts, seconds=True):
        self.typed_hint.setText(f"✓ {fmt_due(ts, seconds=seconds)}")
        self.typed_hint.setStyleSheet(_hint_style(T.ACCENT))
        self.typed_hint.show()

    def _pick(self, ts):
        self._set_time(ts)
        if self.typed.text():
            self.typed.blockSignals(True)
            self.typed.clear()
            self.typed.blockSignals(False)
        self._confirm(ts, seconds=False)            # 'In 20 minutes' means the minute, not the second

    def _edited(self, _dt):
        if not self._own_change:                 # changed by hand in the date field: no quick pick is chosen now
            self._clear_picks()
            self.typed_hint.hide()

    def _use_after(self):
        ts = time.time() + self.after_n.value() * self.after_unit.currentData()
        self._clear_picks()
        self._set_time(ts)
        self._confirm(ts)

    def _typed(self, text):
        from client.when import parse_when
        ts = parse_when(text) if text.strip() else None
        self._clear_picks()
        self.typed_hint.setVisible(bool(text.strip()))
        if ts:
            self._set_time(ts)
            self._confirm(ts)
        else:
            self.typed_hint.setText("Not sure when that is — try 'fri 10:00' or 'in 3 days'")
            self.typed_hint.setStyleSheet(_hint_style(T.MUTED))

    def timestamp(self):
        return max(self.edit.dateTime().toSecsSinceEpoch(), time.time() + 1)

    def message(self):
        return self.text.toPlainText().strip() if self.text else ""

    @classmethod
    def ask(cls, parent, title, initial=None):
        dlg = cls(parent, title, initial)
        return dlg.timestamp() if dlg.exec() else None


# ------------------------------------------------------------ reminder pop-up
class ReminderPopup(QWidget):
    """Card in the bottom-right corner of the screen, above other windows, until Done or Snooze.

    A see-through window around a round card with a shadow, like the message pop-ups; MainWindow stacks both
    in one column (PopupStack.place) so they never cover each other."""
    done = Signal(int)
    snooze = Signal(int, float)            # reminder id, new due time
    open_chat = Signal(str)

    def __init__(self, ctx, reminder):
        from client.ui.popups import float_card, float_window
        super().__init__(None, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.ctx, self.r = ctx, reminder
        outer = float_window(self)
        card = float_card(QFrame(), "remcard", T.ACCENT_FOCUS)
        outer.addWidget(card)
        lay = QVBoxLayout(card)
        lay.setContentsMargins(16, 14, 14, 14)
        lay.setSpacing(T.SPACE_S)
        head = QHBoxLayout()
        head.setSpacing(6)
        ic = QLabel()
        ic.setPixmap(pixmap("clock", T.ACCENT, 18))
        head.addWidget(ic)
        t = QLabel("Reminder")
        t.setStyleSheet(f"color: {T.ACCENT}; font-weight: 800; font-size: {T.pt(T.FONT_M)};")
        head.addWidget(t)
        head.addStretch(1)
        self.when = QLabel(fmt_due(reminder["due_at"]))          # 'Today 23:40' - never seconds here
        self.when.setStyleSheet(f"color: {T.META}; font-size: {T.pt(T.FONT_S)};")
        head.addWidget(self.when)
        lay.addLayout(head)
        if reminder.get("text"):
            text = plain(QLabel(reminder["text"]))
            text.setWordWrap(True)
            text.setStyleSheet(f"font-size: {T.pt(T.FONT_L)}; font-weight: 700;")
            lay.addWidget(text)
        if reminder.get("snippet"):
            q = plain(QLabel(f"{reminder.get('sender_name', '')}: {clip(reminder['snippet'], 160)}"))
            q.setWordWrap(True)
            q.setStyleSheet(f"background: {T.TINT}; border-left: 3px solid {T.ACCENT}; border-radius: {T.RADIUS_S}px;"
                            f" padding: 6px 8px; color: {T.MUTED};")
            lay.addWidget(q)
        conv = reminder.get("conv")
        if conv and ctx.store.conv_exists(conv):
            where = ElidedLabel(f"in {ctx.store.title(conv)}")
            where.setStyleSheet(f"color: {T.META}; font-size: {T.pt(T.FONT_S)};")
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

    def show_at(self, index=0, screen=None):
        """On its own (no PopupStack): stack cards up from the bottom-right corner of the screen."""
        from client.ui.popups import MARGINS, SCREEN_GAP, screen_area
        self.adjustSize()
        area = screen_area(screen)
        if area is not None:                    # the see-through margins already keep the card off the edge
            self.move(area.right() + 1 - max(0, SCREEN_GAP - MARGINS[2]) - self.width(),
                      area.bottom() + 1 - max(0, SCREEN_GAP - MARGINS[3]) - self.height() * (index + 1))
        self.show()
        self.raise_()
