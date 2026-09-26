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


def check_departments_page(test, win, api):
    """The Departments page shows departments with their sections; ticking Chat room turns the room on."""
    from PySide6.QtCore import Qt
    from server.admin_gui import DepartmentsPage
    page = next(p for _t, _i, p in win.pages if isinstance(p, DepartmentsPage))
    page.refresh()
    test.assertTrue(page.tree.isHidden())                      # empty state first
    test.assertFalse(page.empty.isHidden())
    comp = api.call("admin_save_department", None, "Compositing")
    api.call("admin_save_department", None, "Roto", comp)
    api.call("admin_create_user", must_change=False, username="roto1", password="Artist2026",
             department="Compositing", section="Roto")
    page.refresh()
    test.assertEqual(page.tree.topLevelItemCount(), 1)
    top = page.tree.topLevelItem(0)
    test.assertEqual((top.text(0), top.text(1), top.childCount()), ("Compositing", "1", 1))
    top.setCheckState(2, Qt.Checked)                           # what a click does
    QApplication.processEvents()
    test.assertTrue(next(d for d in api.call("admin_departments") if d["id"] == comp)["has_room"])
    test.assertTrue(any(r["name"] == "Compositing" and r["auto"] for r in api.call("admin_rooms")))
    page.refresh()
    test.assertEqual(page.tree.topLevelItem(0).checkState(2), Qt.Checked)
    # the user dialog only offers these departments, and sections of the chosen one
    from server.admin_gui import UserDialog
    users = api.call("admin_users")
    user = next(u for u in users if u["username"] == "roto1")
    dlg = UserDialog(win, user, users, [], api.call("admin_departments"))
    test.assertFalse(dlg.department.isEditable())
    test.assertEqual((dlg.values()["department"], dlg.values()["section"]), ("Compositing", "Roto"))
    dlg.department.setCurrentIndex(0)                          # (none): no sections to pick
    test.assertEqual((dlg.values()["department"], dlg.values()["section"], dlg.section.count()), ("", "", 1))
    dlg.deleteLater()


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

    def test_departments_page(self):
        self.core.start()
        check_departments_page(self, self.win, self.win.api)
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

    def test_departments_page_over_the_network(self):
        check_departments_page(self, self.win, self.api)
        for title, _icon, page in self.win.pages:
            with self.subTest(page=title):
                page.refresh()


if __name__ == "__main__":
    unittest.main()
