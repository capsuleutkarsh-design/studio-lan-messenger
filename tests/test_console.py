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


if __name__ == "__main__":
    unittest.main()
