"""The calendar in the real client against a real server: create a meeting through the dialog, see it on the
calendar and on Home, switch views, open a room's calendar."""

import datetime as dt
import os
import shutil
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from server.core import ServerCore  # noqa: E402

PORT = 16550


def settle(app, seconds=0.3):
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)


class CalendarUiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        os.environ["APPDATA"] = os.path.join(cls.tmp, "appdata")
        os.environ["LOCALAPPDATA"] = os.path.join(cls.tmp, "local")
        cls.app = QApplication.instance() or QApplication(sys.argv)
        cls.app.setQuitOnLastWindowClosed(False)
        QMessageBox.exec = lambda self, *a: QMessageBox.Ok
        for name in ("information", "warning", "critical", "question"):
            setattr(QMessageBox, name, staticmethod(lambda *a, **k: QMessageBox.Yes))
        cls.core = c = ServerCore(os.path.join(cls.tmp, "server"))
        c.config.update(tcp_port=PORT, discovery_port=PORT + 1, admin_review_enabled=False)
        c.start()
        c.call(c.admin_save_department, name="Compositing")
        cls.ann = c.call(c.admin_create_user, must_change=False, username="ann", password="Artist2026",
                         display_name="Ann Rao", department="Compositing", designation="Compositing Lead")
        cls.ben = c.call(c.admin_create_user, must_change=False, username="ben", password="Artist2026",
                         display_name="Ben Kapoor", department="Compositing")
        cls.room = c.call(c.admin_save_room, None, "FAL Delivery", "", [cls.ann, cls.ben])
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
        cls.w.resize(1280, 800)
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

    def test_create_meeting_and_see_it(self):
        from client.ui.calendar_dialogs import EventDialog
        w = self.w
        tomorrow = dt.date.today() + dt.timedelta(days=1)
        w.open_calendar(tomorrow)
        self.assertIs(w.stack.currentWidget(), w.calendar)
        dlg = EventDialog(w, "meeting", start=dt.datetime.combine(tomorrow, dt.time(17)), room_id=self.room)
        dlg.title.setText("Dailies")
        dlg.repeat.setCurrentIndex(2)                                        # every working day
        self.assertEqual(dlg.scope.currentData(), ("room", str(self.room)))  # the room's calendar pre-picks it
        dlg._save()
        self.assertTrue(self.wait(lambda: dlg.result() == 1))
        self.assertTrue(self.wait(lambda: any(e.title == "Dailies" for e in w.calendar.entries())))
        # the room got a line about it, and Home's calendar knows it
        self.assertTrue(self.wait(lambda: any("Dailies" in (e.title or "") for e in w.calendar.entries())))
        for view in ("week", "day", "agenda", "month"):
            w.calendar.set_view(view)
            settle(self.app, 0.3)
        w._refresh_upcoming()
        self.assertTrue(self.wait(lambda: any(i["title"] == "Dailies" for i in (w.store.calendar or {}).get("items", []))))
        # a room's own calendar shows its items only
        w.open_calendar(room_id=self.room)
        settle(self.app, 0.5)
        self.assertTrue(self.wait(lambda: w.calendar.entries() and all(
            e.kind == "holiday" or (e.item and e.item.get("scope") == "room") for e in w.calendar.entries())))
        w.calendar.set_room(None)

    def test_personal_note_and_leave(self):
        from client.ui.calendar_dialogs import EventDialog, LeaveDialog
        w = self.w
        today = dt.date.today()
        dlg = EventDialog(w, "note", start=dt.datetime.combine(today, dt.time(9)))
        dlg.title.setText("Renew licence")
        self.assertTrue(dlg.all_day.isChecked())                           # notes are for the day
        self.assertIn(("dept", "Compositing"), [dlg.scope.itemData(i) for i in range(dlg.scope.count())])
        dlg._save()
        self.assertTrue(self.wait(lambda: dlg.result() == 1))
        leave = LeaveDialog(w, today + dt.timedelta(days=20))
        leave._save()
        self.assertTrue(self.wait(lambda: leave.result() == 1))
        w.open_calendar(today)
        self.assertTrue(self.wait(lambda: any(e.title == "📝 Renew licence" for e in w.calendar.entries())))
        w.open_calendar(today + dt.timedelta(days=20))
        self.assertTrue(self.wait(lambda: any(e.kind == "leave" for e in w.calendar.entries())))


if __name__ == "__main__":
    unittest.main()
