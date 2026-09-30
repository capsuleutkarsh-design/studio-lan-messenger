"""1.12.0 calendar refinements against a real server: small windows, the '+ New' menu, default times, keeping a
meeting's length, deadlines as a moment, answers to meetings, labelled birthdays / anniversaries / holidays,
readable kind colours, the leave and holiday dialogs."""

import datetime as dt
import os
import shutil
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent, QPoint, QTime  # noqa: E402
from PySide6.QtWidgets import QApplication, QMenu, QMessageBox  # noqa: E402

from server.core import ServerCore  # noqa: E402
from tests import test_server as base  # noqa: E402

PORT = 17180


def settle(app, seconds=0.3):
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)


class Other(base.Client):
    """Someone else, on a plain connection to the same server."""

    def __init__(self, username, password="Artist2026"):
        base.PORT, old = PORT, base.PORT
        try:
            super().__init__(username, password)
        finally:
            base.PORT = old


class CalendarV112Test(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        os.environ["APPDATA"] = os.path.join(cls.tmp, "appdata")
        os.environ["LOCALAPPDATA"] = os.path.join(cls.tmp, "local")
        cls.app = QApplication.instance() or QApplication(sys.argv)
        cls.app.setQuitOnLastWindowClosed(False)
        QMessageBox.exec = lambda self, *a: QMessageBox.Ok
        cls.answer = QMessageBox.Yes
        cls.asked = []

        def ask(*a, **k):
            cls.asked.append(a[2] if len(a) > 2 else "")
            return cls.answer
        for name in ("information", "warning", "critical"):
            setattr(QMessageBox, name, staticmethod(lambda *a, **k: QMessageBox.Ok))
        QMessageBox.question = staticmethod(ask)
        cls.core = c = ServerCore(os.path.join(cls.tmp, "server"))
        c.config.update(tcp_port=PORT, discovery_port=PORT + 1)
        c.start()
        c.call(c.admin_save_department, name="Compositing")
        cls.today = today = dt.date.today()
        mk = lambda u, n, **kw: c.call(c.admin_create_user, must_change=False, username=u,  # noqa: E731
                                       password="Artist2026", display_name=n, department="Compositing", **kw)
        cls.ann = mk("ann", "Ann Rao", designation="Compositing Lead")
        cls.ben = mk("ben", "Ben Kapoor", birthday=(today + dt.timedelta(days=1)).strftime("%d-%m"))
        cls.cat = mk("cat", "Cat Iyer",
                     joined_on=(today + dt.timedelta(days=1)).replace(year=today.year - 5).strftime("%d-%m-%Y"))
        cls.room = c.call(c.admin_save_room, None, "FAL Delivery", "", [cls.ann, cls.ben, cls.cat])
        import client.main as cm
        import client.ui.main_window as mw
        mw.play_sound = lambda kind="notify": None
        cls.ctl = cm.App()
        cls.ctl.do_login("127.0.0.1", PORT, "ann", "Artist2026", False)
        end = time.time() + 15
        while cls.ctl.main is None and time.time() < end:
            settle(cls.app, 0.1)
        assert cls.ctl.main is not None, "client did not sign in"
        cls.w = cls.ctl.main
        if getattr(cls.w, "_tour", None) is not None:
            cls.w._tour.accept()
        cls.w.resize(1300, 820)
        cls.w.show()
        settle(cls.app, 0.5)
        cls.w.rail_clicked("calendar")
        cls.cal = cls.w.calendar
        settle(cls.app, 0.5)

    @classmethod
    def tearDownClass(cls):
        cls.w.quitting = True
        cls.core.stop()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def wait(self, check, seconds=5):
        end = time.time() + seconds
        while time.time() < end:
            settle(self.app, 0.1)
            if check():
                return True
        return False

    def setUp(self):
        type(self).answer = QMessageBox.Yes
        self.asked.clear()

    # ------------------------------------------------------------ small windows
    def test_small_window_folds_the_day_panel_and_keeps_every_control(self):
        cal, w = self.cal, self.w
        cal.set_view("month")
        w.resize(1300, 820)
        settle(self.app, 0.4)
        self.assertTrue(cal.side.isVisible())
        self.assertTrue(all(b.isVisible() for b in cal.view_group.buttons()))
        self.assertEqual(cal.new_btn.text().strip(), "New")
        w.resize(1024, 640)                        # the page is about 620 px wide next to the chat list
        settle(self.app, 0.4)
        self.assertLess(cal.width(), 760)
        self.assertFalse(cal.side.isVisible(), "the day panel folds away on a narrow page")
        self.assertTrue(cal.view_btn.isVisible())
        self.assertFalse(any(b.isVisible() for b in cal.view_group.buttons()))
        self.assertEqual(cal.new_btn.text(), "")
        self.assertTrue(cal.new_btn.toolTip())
        self.assertEqual(cal.title.text(), f"{cal.anchor:%b %Y}")            # 'Sep 2026'
        # every layer chip is whole and inside the page (they wrap instead of being cut)
        for b in cal.layer_buttons.values():
            self.assertTrue(b.isVisible())
            self.assertGreaterEqual(b.width(), b.sizeHint().width())
            right = b.mapTo(cal, QPoint(b.width(), 0)).x()
            self.assertLessEqual(right, cal.width())
        self.assertLessEqual(cal.month.minimumWidth(), 300)
        # Agenda has no day panel (it lists the days itself)
        w.resize(1300, 820)
        settle(self.app, 0.4)
        cal.set_view("agenda")
        settle(self.app, 0.2)
        self.assertFalse(cal.side.isVisible())
        cal.set_view("day")
        settle(self.app, 0.2)
        self.assertFalse(cal.side.isVisible(), "Day view shows the day already")
        cal.set_view("month")
        settle(self.app, 0.2)
        self.assertTrue(cal.side.isVisible())
        self.assertTrue(all(not b.icon().isNull() for b in cal.layer_buttons.values()))

    def test_titles_have_no_zero_padded_days(self):
        cal = self.cal
        year = self.today.year
        cal.anchor = cal.selected = dt.date(year, 10, 1)
        cal.set_view("week", fetch=False)
        cal._title()
        monday = dt.date(year, 10, 1) - dt.timedelta(days=dt.date(year, 10, 1).weekday())
        sunday = monday + dt.timedelta(days=6)
        self.assertTrue(cal.title.text().endswith(f"{sunday.day} {sunday:%b}"))     # this year: no year
        self.assertNotIn(" 0", cal.title.text())
        cal.set_view("day", fetch=False)
        cal._title()
        self.assertEqual(cal.title.text(), f"{dt.date(year, 10, 1):%A} 1 October")
        cal.anchor = cal.selected = dt.date(year + 1, 1, 5)
        cal._title()
        self.assertTrue(cal.title.text().endswith(str(year + 1)))                     # another year: with it
        cal.go_today()
        cal.set_view("month")

    # ------------------------------------------------------------ the '+ New' menu
    def test_new_menu_opens_under_the_button_right_aligned(self):
        import client.ui.calendar_page as cp
        seen = {}

        class Menu(QMenu):
            def exec(self, pos=None, *a):
                seen["pos"], seen["size"] = pos, self.sizeHint()
                seen["labels"] = [x.text() for x in self.actions() if x.text()]
        old = cp.QMenu
        cp.QMenu = Menu
        try:
            self.cal.new_menu()
        finally:
            cp.QMenu = old
        btn = self.cal.new_btn
        right = btn.mapToGlobal(QPoint(btn.width(), 0)).x()
        self.assertLessEqual(seen["pos"].x() + seen["size"].width(), right)
        self.assertGreater(seen["pos"].y(), btn.mapToGlobal(QPoint(0, 0)).y())
        self.assertIn("Personal event…", seen["labels"])
        self.assertFalse(any("..." in t for t in seen["labels"]))

    # ------------------------------------------------------------ default times
    def test_new_items_do_not_start_in_the_past(self):
        from client.ui.calendar_page import CalendarPage
        d = dt.date(2026, 9, 29)
        start = CalendarPage._default_start
        self.assertEqual(start(d, now=dt.datetime(2026, 9, 29, 8, 0)), dt.datetime(2026, 9, 29, 9, 0))
        self.assertEqual(start(d, now=dt.datetime(2026, 9, 29, 15, 10)), dt.datetime(2026, 9, 29, 15, 30))
        self.assertEqual(start(d, now=dt.datetime(2026, 9, 29, 15, 40)), dt.datetime(2026, 9, 29, 16, 0))
        self.assertEqual(start(d, now=dt.datetime(2026, 9, 29, 23, 50)), dt.datetime(2026, 9, 29, 23, 30))
        self.assertEqual(start(d, now=dt.datetime(2026, 9, 28, 15, 0)), dt.datetime(2026, 9, 29, 9, 0))
        self.assertEqual(start(d, 18, now=dt.datetime(2026, 9, 29, 12, 0)), dt.datetime(2026, 9, 29, 18, 0))
        self.assertEqual(start(d, 18, now=dt.datetime(2026, 9, 29, 19, 5)), dt.datetime(2026, 9, 29, 19, 30))
        # a dialog opened without a time uses the same rule, not 'now + 1 hour' (02:00 at night)
        from client.ui.calendar_dialogs import EventDialog
        dlg = EventDialog(self.w, "meeting")
        want = start(dt.date.today())
        self.assertEqual(dlg.t_start.time().toPython().replace(second=0), want.time())
        dlg.close()

    # ------------------------------------------------------------ the event dialog
    def test_moving_the_start_keeps_the_length(self):
        from client.ui.calendar_dialogs import EventDialog
        dlg = EventDialog(self.w, "meeting", start=dt.datetime.combine(self.today, dt.time(10)))
        self.assertEqual((dlg.t_start.time(), dlg.t_end.time()), (QTime(10, 0), QTime(10, 30)))
        dlg.t_start.setTime(QTime(14, 0))
        self.assertEqual(dlg.t_end.time(), QTime(14, 30))
        s, e = dlg._times()
        self.assertEqual(e - s, dt.timedelta(minutes=30))
        self.assertFalse(dlg._overnight())
        dlg.close()

    def test_an_end_before_the_start_is_not_saved_silently(self):
        from client.ui.calendar_dialogs import EventDialog
        dlg = EventDialog(self.w, "event", start=dt.datetime.combine(self.today, dt.time(14)))
        dlg.show()
        dlg.title.setText("Review")
        dlg.t_end.setTime(QTime(10, 30))
        settle(self.app, 0.1)
        self.assertTrue(dlg._overnight())
        self.assertTrue(dlg.when_hint.isVisible())
        self.assertIn("before it starts", dlg.when_hint.text())
        type(self).answer = QMessageBox.No
        dlg._save()
        self.assertTrue(self.asked, "it asks before saving a 20-hour item")
        settle(self.app, 0.5)
        self.assertEqual(dlg.result(), 0)
        # a night shift (from 18:00 on) is fine: just a note that it ends the next day
        dlg.t_start.setTime(QTime(22, 0))
        dlg.t_end.setTime(QTime(2, 0))
        settle(self.app, 0.1)
        self.assertIn("next day", dlg.when_hint.text())
        self.asked.clear()
        dlg._save()
        self.assertFalse(self.asked)
        self.assertTrue(self.wait(lambda: dlg.result() == 1))

    def test_save_needs_a_title_and_placeholders_are_even(self):
        from client.ui.calendar_dialogs import EventDialog
        dlg = EventDialog(self.w, "meeting")
        self.assertFalse(dlg.save_btn.isEnabled())
        dlg.title.setText("   ")
        self.assertFalse(dlg.save_btn.isEnabled())
        dlg.title.setText("Dailies")
        self.assertTrue(dlg.save_btn.isEnabled())
        self.assertEqual(dlg.location.placeholderText(), "Room, link or desk (optional)")
        self.assertEqual(dlg.date.displayFormat(), "ddd d MMM yyyy")
        # the invite count follows the ticks
        self.assertIn("Nobody", dlg.scope_hint.text())
        lst = dlg.picker.list
        from PySide6.QtCore import Qt
        lst.item(0).setCheckState(Qt.Checked)
        self.assertIn("1 person invited", dlg.scope_hint.text())
        dlg.close()

    def test_a_deadline_is_a_moment(self):
        from client.ui.calendar_dialogs import EventDialog
        friday = self.today + dt.timedelta(days=3)
        dlg = EventDialog(self.w, "deadline", start=dt.datetime.combine(friday, dt.time(18)), room_id=self.room)
        dlg.show()
        settle(self.app, 0.1)
        self.assertFalse(dlg.t_end.isVisible())
        self.assertFalse(dlg.dash.isVisible())
        form = dlg._form
        from PySide6.QtWidgets import QFormLayout
        labels = [form.itemAt(r, QFormLayout.LabelRole).widget().text() for r in range(form.rowCount())
                  if form.itemAt(r, QFormLayout.LabelRole) and form.itemAt(r, QFormLayout.LabelRole).widget()]
        self.assertIn("Due", labels)
        self.assertIn("Who it's for", labels)
        s, e = dlg._times()
        self.assertEqual(e - s, dt.timedelta(minutes=15))
        dlg.title.setText("Reel 3 DI")
        dlg._save()
        self.assertTrue(self.wait(lambda: dlg.result() == 1))
        # in the week it is a short marker at the due time, not an hour-long block
        cal = self.cal
        cal.go_to(friday, "week")
        self.assertTrue(self.wait(lambda: any(e.name == "Reel 3 DI" for e in cal.entries())))
        cal.week.grid.resize(900, cal.week.grid.minimumHeight())
        cal.week.grid.repaint()
        blocks = [r for r, e in cal.week.grid._hits if e.name == "Reel 3 DI" and not e.all_day]
        self.assertTrue(blocks)
        self.assertLessEqual(blocks[0].height(), cal.week.grid.MARK + 1)
        cal.go_today()
        cal.set_view("month")

    # ------------------------------------------------------------ meeting details
    def test_meeting_details_show_my_answer(self):
        from client.ui.calendar_dialogs import ItemDialog
        ben = Other("ben")
        start = dt.datetime.combine(self.today + dt.timedelta(days=1), dt.time(11))
        r = ben.request("cal_save", event=dict(kind="meeting", title="Comp team weekly", scope="people",
                                               people=[self.ann], start=start.timestamp(),
                                               end=(start + dt.timedelta(minutes=45)).timestamp()))
        self.assertTrue(r.get("ok"), r)
        cal = self.cal
        cal.go_to(start.date(), "month")

        def entry():
            return next((e for e in cal.entries() if e.name == "Comp team weekly"), None)
        self.assertTrue(self.wait(lambda: entry() is not None))
        dlg = ItemDialog(self.w, entry())
        self.assertEqual(set(dlg.rsvp_buttons), {"yes", "maybe", "no"})
        self.assertFalse(any(b.isChecked() for b in dlg.rsvp_buttons.values()))
        self.assertTrue(all(b.property("chip") for b in dlg.rsvp_buttons.values()))
        self.assertFalse(any(b.autoDefault() for b in dlg.rsvp_buttons.values()), "Enter must not answer")
        self.assertNotIn("  ", dlg.when.text())
        self.assertIn("11:00 – 11:45", dlg.when.text())
        dlg.rsvp_buttons["yes"].click()
        self.assertTrue(self.wait(lambda: dlg.result() == 1))
        cal.fetch()
        self.assertTrue(self.wait(lambda: entry() is not None and entry().item.get("my_rsvp") == "yes"))
        dlg = ItemDialog(self.w, entry())
        self.assertTrue(dlg.rsvp_buttons["yes"].isChecked())
        self.assertTrue(dlg.rsvp_buttons["yes"].text().startswith("✓"))
        self.assertFalse(dlg.rsvp_buttons["maybe"].text().startswith("✓"))
        dlg.close()
        cal.go_today()

    # ------------------------------------------------------------ what entries say
    def test_birthdays_anniversaries_and_holidays_say_what_they_are(self):
        from client.ui.calendar_views import entries_from
        tomorrow = (self.today + dt.timedelta(days=1)).isoformat()
        data = {"items": [], "leave": [],
                "holidays": [{"day": tomorrow, "name": "Gandhi Jayanti", "confirm": False}],
                "people_days": [{"day": tomorrow, "kind": "birthday", "user_id": 2, "name": "Priya Sharma"},
                                {"day": tomorrow, "kind": "anniversary", "user_id": 3, "name": "Farhan Qureshi",
                                 "years": 5},
                                {"day": tomorrow, "kind": "birthday", "user_id": 1, "name": "Me"}]}
        es = {e.name: e for e in entries_from(data, {"holiday", "birthday", "anniversary"}, me_id=1)}
        self.assertIn("Priya Sharma's birthday", es)
        self.assertIn("Farhan Qureshi – 5 years at the studio", es)
        self.assertIn("Your birthday", es)
        self.assertEqual(es["Gandhi Jayanti"].icon, "sun")
        self.assertEqual(es["Priya Sharma's birthday"].icon, "cake")
        self.assertEqual(es["Farhan Qureshi – 5 years at the studio"].icon, "gift")
        self.assertEqual(es["Priya Sharma's birthday"].title, "🎂 Priya Sharma's birthday")   # plain-text lists

    def test_kind_colours_are_apart_and_readable(self):
        from PySide6.QtGui import QColor
        from common import theme as T
        from client.ui.calendar_views import KINDS, kind_color, kind_text_color
        hues = {k: QColor(kind_color(k)).hslHue() for k in KINDS}
        for a in KINDS:
            for b in KINDS:
                if a < b:
                    d = abs(hues[a] - hues[b])
                    self.assertGreaterEqual(min(d, 360 - d), 18, f"{a} and {b} look alike")
        for k in KINDS:
            self.assertGreaterEqual(T.contrast(kind_text_color(k), T.PANEL), 4.5)
            self.assertGreaterEqual(T.contrast(kind_text_color(k), T.BG), 4.5)
        self.assertEqual(KINDS["event"][0], "Personal")          # the same word as '+ New › Personal event'

    # ------------------------------------------------------------ the views
    def test_month_rows_and_more_opens_the_day(self):
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest
        from client.ui.calendar_views import Entry, MonthView, month_rows
        self.assertEqual(month_rows(dt.date(2026, 9, 1)), 5)
        self.assertEqual(month_rows(dt.date(2026, 8, 1)), 6)
        self.assertEqual(month_rows(dt.date(2027, 2, 1)), 4)
        mv = MonthView()
        mv.resize(700, 420)
        day = dt.datetime(2026, 9, 16, 9)
        entries = [Entry(i, "meeting", f"M{i}", day + dt.timedelta(minutes=i), day + dt.timedelta(minutes=i + 30),
                         False) for i in range(8)]
        mv.set_data(dt.date(2026, 9, 1), entries, set())
        mv.grab()                                  # paints it (and fills its click targets)
        self.assertEqual(len([h for h in mv._hits if h[2] is None]), 35)       # 5 weeks, not 6
        more = [r for r, d, t in mv._hits if t == MonthView.MORE]
        self.assertTrue(more, "a busy day shows '+N more'")
        opened = []
        mv.day_activated.connect(opened.append)
        QTest.mouseClick(mv, Qt.LeftButton, pos=more[0].center().toPoint())
        self.assertEqual(opened, [day.date()])
        mv.close()

    def test_a_busy_month_scrolls_instead_of_squeezing(self):
        from PySide6.QtCore import QPointF
        from client.ui.calendar_views import Entry, MonthScroll, MonthView
        mv = MonthView()
        area = MonthScroll(mv)
        area.resize(700, 640)
        area.show()
        settle(self.app, 0.1)
        mv.set_data(dt.date(2026, 9, 1), [], set())
        settle(self.app, 0.1)
        self.assertLessEqual(mv.height(), area.viewport().height() + 2, "a quiet month fits: nothing to scroll")
        day = dt.datetime(2026, 9, 16, 9)
        entries = [Entry(i, "meeting", f"M{i}", day + dt.timedelta(minutes=i), day + dt.timedelta(minutes=i + 30),
                         False) for i in range(5)]
        mv.set_data(dt.date(2026, 9, 1), entries, set())
        settle(self.app, 0.1)
        self.assertGreater(mv.height(), area.viewport().height(), "a busy week grows and the month scrolls")
        mv.grab()
        self.assertFalse([t for r, d, t in mv._hits if t == MonthView.MORE], "five items all show: no '+N more'")
        area.verticalScrollBar().setValue(area.verticalScrollBar().maximum())
        settle(self.app, 0.1)
        top = mv.visibleRegion().boundingRect().top()
        self.assertGreater(top, 0)
        self.assertEqual(mv._hit(QPointF(50, top + 10)), (None, None), "the day names stay on top")
        area.close()

    def test_week_day_names_open_the_day(self):
        cal = self.cal
        cal.set_view("week")
        settle(self.app, 0.3)
        day = self.today + dt.timedelta(days=1)
        cal.week.grid.day_clicked.emit(day)
        self.assertEqual(cal.view, "day")
        self.assertEqual(cal.selected, day)
        cal.go_today()
        cal.set_view("month")

    def test_two_line_names_in_narrow_blocks(self):
        from PySide6.QtGui import QFontMetrics
        from client.ui.calendar_views import _font, _lines
        fm = QFontMetrics(_font(8.5, True))
        lines = _lines(fm, "Comp review AK74_0450 and 0525 with the client", 60, 2)
        self.assertEqual(len(lines), 2)
        self.assertTrue(lines[-1].endswith("…"))
        self.assertTrue(all(fm.horizontalAdvance(x) <= 60 for x in lines))
        self.assertEqual(_lines(fm, "Dailies", 200, 2), ["Dailies"])

    def test_side_panel_rows_fit_the_panel(self):
        cal = self.cal
        cal.go_today()
        cal.set_view("month")
        long_title = "Comp review — AK74_0450 & 0525 with the whole lighting team"
        from client.ui.calendar_dialogs import EventDialog
        dlg = EventDialog(self.w, "event", start=cal._default_start(self.today))
        dlg.title.setText(long_title)
        dlg._save()
        self.assertTrue(self.wait(lambda: dlg.result() == 1))
        cal.fetch()
        self.assertTrue(self.wait(lambda: any(e.name == long_title for e in cal.entries())))
        cal._fill_side()
        settle(self.app, 0.3)
        from PySide6.QtCore import Qt
        self.assertEqual(cal.side_area.horizontalScrollBarPolicy(), Qt.ScrollBarAlwaysOff)
        rows = [cal.side_list.itemAt(i).widget() for i in range(cal.side_list.count())
                if cal.side_list.itemAt(i).widget()]
        self.assertTrue(rows)
        for b in rows:
            self.assertLessEqual(b.width(), cal.side_area.viewport().width())
        self.assertTrue(any(long_title in b.toolTip() for b in rows))

    # ------------------------------------------------------------ leave and holidays
    def test_leave_dialog(self):
        from PySide6.QtWidgets import QFormLayout
        from client.ui.calendar_dialogs import LeaveDialog
        dlg = LeaveDialog(self.w, dt.date(self.today.year, 10, 1))
        dlg.show()
        settle(self.app, 0.2)
        self.assertIs(dlg.focusWidget(), dlg.reason)
        forms = dlg.findChildren(QFormLayout)
        labels = [f.itemAt(r, QFormLayout.LabelRole).widget().text() for f in forms for r in range(f.rowCount())
                  if f.itemAt(r, QFormLayout.LabelRole) and f.itemAt(r, QFormLayout.LabelRole).widget()]
        for want in ("First day", "Last day", "Reason", "Auto-reply"):
            self.assertIn(want, labels)
        self.assertNotIn(" 0", dlg.auto_text.toPlainText())
        dlg.close()

    def test_holidays_dialog(self):
        from PySide6.QtCore import Qt
        from client.ui.calendar_dialogs import HolidaysDialog
        dlg = HolidaysDialog(self.w, self.today.year)
        dlg.show()
        self.assertTrue(self.wait(lambda: dlg.table.rowCount() > 0))
        self.assertEqual(dlg.table.columnCount(), 5)
        self.assertEqual(dlg.table.horizontalHeaderItem(3).text(), "Kind")               # as in the console
        self.assertTrue(dlg.table.item(0, 3).text())
        from PySide6.QtWidgets import QPushButton
        texts = [b.text().strip() for b in dlg.findChildren(QPushButton)]
        self.assertIn("Import .ics…", texts)
        self.assertFalse(any("..." in t for t in texts))
        self.assertTrue(dlg.table.horizontalHeader().defaultAlignment() & Qt.AlignLeft)
        self.assertFalse(dlg.edit_btn.isEnabled())
        self.assertFalse(dlg.delete_btn.isEnabled())
        dlg.table.selectRow(0)
        self.assertTrue(dlg.edit_btn.isEnabled())
        self.assertNotRegex(dlg.table.item(0, 1).text(), r" 0\d")
        self.assertIsNot(dlg.focusWidget(), dlg.year_box)
        dlg.close()


if __name__ == "__main__":
    unittest.main()
