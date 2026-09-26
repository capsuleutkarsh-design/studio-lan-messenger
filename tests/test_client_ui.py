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
        cls.core.config.update(tcp_port=PORT, discovery_port=PORT + 1, admin_review_enabled=False)
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


if __name__ == "__main__":
    unittest.main()
