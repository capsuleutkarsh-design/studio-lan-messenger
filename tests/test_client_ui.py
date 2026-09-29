"""The real client against a real server: screens that rebuild themselves must not pile up old widgets."""

import os
import shutil
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox, QWidget  # noqa: E402

from common import theme  # noqa: E402
from server.core import ServerCore  # noqa: E402

PORT = 15950


def settle(app, seconds=0.3):
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)     # run pending deleteLater()


class HomeScreenTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        os.environ["APPDATA"] = os.path.join(cls.tmp, "appdata")
        os.environ["LOCALAPPDATA"] = os.path.join(cls.tmp, "local")
        cls.app = QApplication.instance() or QApplication(sys.argv)
        cls.app.setQuitOnLastWindowClosed(False)
        # no modal boxes in an unattended run (they would wait for a click forever)
        QMessageBox.exec = lambda self, *a: QMessageBox.Ok
        for name in ("information", "warning", "critical", "question"):
            setattr(QMessageBox, name, staticmethod(lambda *a, **k: QMessageBox.Yes))
        cls.core = ServerCore(os.path.join(cls.tmp, "server"))
        cls.core.config.update(tcp_port=PORT, discovery_port=PORT + 1)
        cls.core.start()
        cls.core.call(cls.core.admin_create_user, must_change=False, username="ann", password="Artist2026",
                      display_name="Ann Rao")
        import client.main as cm
        import client.ui.main_window as mw
        mw.play_sound = lambda kind="notify": None
        cls.ctl = cm.App()
        cls.ctl.do_login("127.0.0.1", PORT, "ann", "Artist2026", False)
        end = time.time() + 15
        while cls.ctl.main is None and time.time() < end:
            settle(cls.app, 0.1)
        assert cls.ctl.main is not None, "client did not sign in"
        cls.ctl.main.resize(1200, 700)
        cls.ctl.main.stack.setCurrentWidget(cls.ctl.main.home)
        settle(cls.app, 0.5)

    @classmethod
    def tearDownClass(cls):
        cls.ctl.main.quitting = True
        cls.core.stop()
        theme.FESTIVAL = None
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def widgets(self):
        return len(self.ctl.main.home.findChildren(QWidget))

    def rebuild_many(self):
        home = self.ctl.main.home
        home.rebuild()
        settle(self.app)
        first = self.widgets()
        for _ in range(5):
            home.rebuild()
            settle(self.app)
        return first, self.widgets()

    def test_main_window_carries_the_credit_line(self):
        from common import licence
        self.assertEqual(licence.window_problems(self.ctl.main), [])

    def test_rebuild_does_not_pile_up_widgets(self):
        theme.FESTIVAL = None
        first, after = self.rebuild_many()
        self.assertEqual(first, after, "old home-screen widgets were left behind")

    def test_rebuild_with_festival_banner(self):
        theme.FESTIVAL = theme.FESTIVALS["christmas"]
        try:
            first, after = self.rebuild_many()
            self.assertEqual(first, after)
            from client.ui.festive import FestiveBanner
            self.assertEqual(len(self.ctl.main.home.findChildren(FestiveBanner)), 1)   # one banner, kept
        finally:
            theme.FESTIVAL = None
            self.ctl.main.home.rebuild()
            settle(self.app)

    def test_home_fits_the_window(self):
        """Nothing on the home page may be wider than the window (there is no sideways scrolling)."""
        main, home = self.ctl.main, self.ctl.main.home
        try:
            for width in (1600, 1280, 1000, 820):
                main.resize(width, 760)
                settle(self.app, 0.4)
                home.rebuild()
                settle(self.app, 0.4)
                need, have = home.col.minimumSizeHint().width(), home.area.viewport().width() - 64
                self.assertLessEqual(need, have, f"home needs {need}px but has {have}px at window width {width}")
        finally:
            main.resize(1200, 700)

    def test_calendar_card_moves_between_months(self):
        """‹ › and the mouse wheel page the Calendar card's month; Today comes back; each month is fetched."""
        import datetime
        from PySide6.QtCore import QPoint, QPointF, Qt
        from PySide6.QtGui import QWheelEvent
        home = self.ctl.main.home
        home._cal_step(0)
        home.rebuild()
        settle(self.app, 0.4)
        mini, label, back = home._cal_widgets
        this = datetime.date.today().replace(day=1)
        self.assertEqual(mini.month, this)
        self.assertTrue(back.isHidden(), "Today shows only away from this month")

        home._cal_step(1)
        nxt = (this + datetime.timedelta(days=32)).replace(day=1)
        self.assertEqual(mini.month, nxt)
        self.assertEqual(label.text(), f"{nxt:%B %Y}")
        self.assertFalse(back.isHidden())
        end = time.time() + 5
        while nxt not in home._cal_cache and time.time() < end:
            settle(self.app, 0.1)
        self.assertIn(nxt, home._cal_cache, "the month's entries were not fetched")

        # a wheel notch up goes back one month; a touchpad's small steps add up to one notch
        def wheel(dy):
            mini.wheelEvent(QWheelEvent(QPointF(10, 10), QPointF(10, 10), QPoint(0, 0), QPoint(0, dy),
                                        Qt.NoButton, Qt.NoModifier, Qt.NoScrollPhase, False))
        wheel(120)
        self.assertEqual(mini.month, this)
        for _ in range(3):
            wheel(-40)
        self.assertEqual(mini.month, nxt)

        home.rebuild()                          # the Home page rebuilds every minute: the month stays
        settle(self.app, 0.4)
        self.assertEqual(home._cal_widgets[0].month, nxt)
        home._cal_step(0)
        self.assertEqual(home._cal_widgets[0].month, this)


class RestartTest(unittest.TestCase):
    def test_restart_comes_back_on_screen(self):
        """Quillo started with Windows carries --minimized; a restart after a theme change must not keep it."""
        from unittest import mock
        from client.ui.main_window import MainWindow

        class Fake:
            quitting = False

            def quit(self):
                self.quitting = True
        started = []
        with mock.patch.object(sys, "argv", ["LANMessenger.exe", "--minimized"]), \
                mock.patch.object(sys, "frozen", True, create=True), \
                mock.patch("PySide6.QtCore.QProcess.startDetached", lambda exe, args: started.append(args)):
            MainWindow.restart(Fake())
        self.assertEqual(started, [[]])


if __name__ == "__main__":
    unittest.main()
