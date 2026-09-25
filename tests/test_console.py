"""The server console (admin GUI): every page must open and refresh, with the server running and stopped."""

import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from server.core import ServerCore  # noqa: E402

PORT = 15850


class ConsoleTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(sys.argv)
        QMessageBox.warning = QMessageBox.critical = QMessageBox.information = staticmethod(lambda *a, **k: None)

    def setUp(self):
        from server.admin_gui import ServerWindow
        from server.console_api import LocalApi
        self.tmp = tempfile.mkdtemp()
        self.core = ServerCore(self.tmp)
        self.core.config.update(tcp_port=PORT, discovery_port=PORT + 1)
        self.win = ServerWindow(LocalApi(self.core))

    def tearDown(self):
        self.core.stop()
        self.win.quitting = True
        self.win.close()
        self.win.deleteLater()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def refresh_all(self):
        for title, _icon, page in self.win.pages:
            with self.subTest(page=title):
                page.refresh()          # an exception here fails the test (the real app would crash)

    def test_pages_with_server_stopped(self):
        self.refresh_all()

    def test_pages_with_server_running(self):
        self.core.start()
        self.core.call(self.core.admin_create_user, must_change=False, username="ann", password="Artist2026",
                       display_name="Ann Rao")
        self.refresh_all()
        self.core.call(self.core.chat_backup_now)            # dashboard shows the last chat backup
        self.core.call(self.core.backup_now)
        self.refresh_all()
        self.core.last_chat_backup = {"time": 0, "ok": False, "error": "disk full", "folder": "x"}
        self.refresh_all()

    def test_a_window_error_does_not_stop_the_server(self):
        import logging
        import sys as _sys
        from PySide6.QtCore import QTimer
        from common import crash
        from server import admin_gui
        self.core.start()
        records = []
        handler = logging.Handler()
        handler.emit = records.append
        logging.getLogger("crash").addHandler(handler)
        old_hook, old_refresh = _sys.excepthook, admin_gui.DashboardPage.refresh
        admin_gui.DashboardPage.refresh = lambda page: 1 + "boom"          # a bug like the 1.4.0 one
        try:
            crash.install("Test", "")
            self.win.show()
            QTimer.singleShot(0, self.win.refresh_current)                   # what main() does now
            for _ in range(20):
                self.app.processEvents()
            self.assertTrue(self.core.running)                              # the server kept going
            self.assertTrue(any("boom" in str(r.msg) + str(r.args) or "unsupported operand" in r.getMessage()
                                for r in records), [r.getMessage()[:200] for r in records])
        finally:
            admin_gui.DashboardPage.refresh = old_refresh
            _sys.excepthook = old_hook
            logging.getLogger("crash").removeHandler(handler)
            for w in self.app.topLevelWidgets():
                if w.windowTitle() == "Test":
                    w.close()


class RemoteConsoleTest(unittest.TestCase):
    """The same pages when the server runs as a Windows service and the console connects to it."""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(sys.argv)
        QMessageBox.warning = QMessageBox.critical = QMessageBox.information = staticmethod(lambda *a, **k: None)

    def setUp(self):
        from server.admin_gui import ServerWindow
        from server.console_api import RemoteApi
        self.tmp = tempfile.mkdtemp()
        self.old_appdata = os.environ.get("APPDATA")
        os.environ["APPDATA"] = self.tmp                      # console pins go to a temp folder
        self.core = ServerCore(os.path.join(self.tmp, "data"))
        self.core.config.update(tcp_port=PORT + 10, discovery_port=PORT + 11)
        self.core.start()
        self.core.call(self.core.admin_create_user, must_change=False, username="boss", password="Artist2026",
                       display_name="Boss", is_admin=1)
        self.api = RemoteApi("127.0.0.1", PORT + 10)
        self.assertEqual(self.api.connect("boss", "Artist2026"), "")
        self.win = ServerWindow(self.api)

    def tearDown(self):
        self.win.quitting = True
        self.win.close()
        self.win.deleteLater()
        self.api.close()
        self.core.stop()
        if self.old_appdata is None:
            os.environ.pop("APPDATA", None)
        else:
            os.environ["APPDATA"] = self.old_appdata
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_pages_over_the_network(self):
        for title, _icon, page in self.win.pages:
            with self.subTest(page=title):
                page.refresh()
        self.win.show()
        for i in range(len(self.win.pages)):
            self.win.show_page(i)
        self.win.update_state()


if __name__ == "__main__":
    unittest.main()
