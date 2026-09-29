"""1.12 UI refinement of the server console: what the admin sees and what the server behind it now does."""

import datetime
import os
import shutil
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from server.core import ServerCore, is_console_account  # noqa: E402
from tests import test_server as base  # noqa: E402

PORT = 17200


class Client(base.Client):
    def __init__(self, username, password="Artist2026"):
        base.PORT, old = PORT, base.PORT
        try:
            super().__init__(username, password)
        finally:
            base.PORT = old


class ServerSideTest(unittest.TestCase):
    """Server changes behind the console: kept protocol-compatible (only extra fields and an extra argument)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.core = c = ServerCore(self.tmp)
        c.config.update(tcp_port=PORT, discovery_port=PORT + 1)
        c.start()
        c.call(c.admin_save_department, None, "Paint")
        self.roles = {r["name"]: r["id"] for r in c.call(c.admin_roles)}
        self.lead = c.call(c.admin_create_user, must_change=False, username="sneha", password="Artist2026",
                           display_name="Sneha Kulkarni", department="Paint")
        self.ann = c.call(c.admin_create_user, must_change=False, username="ananya", password="Artist2026",
                          display_name="Ananya Bose", department="Paint", role_id=self.roles["Paint Artist"],
                          manager_id=self.lead)

    def tearDown(self):
        self.core.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_console_account_is_not_a_person(self):
        c = self.core
        admin = next(u for u in c.call(c.admin_users) if u["username"] == "admin")
        self.assertTrue(is_console_account(admin))
        ann_id = c.call(c.admin_announce, "Fire drill", "At 3 PM", "all")
        reads = c.call(c.admin_announcement_reads, ann_id)
        names = [p["name"] for p in reads["read"] + reads["unread"]]
        self.assertNotIn("Administrator", names, "the console account never reads announcements")
        self.assertEqual(len(names), 2)
        self.assertEqual(c.call(c.admin_announcements)[0]["total"], 2)
        self.assertNotIn("admin", [p["username"] for p in c.call(c.admin_org)])
        self.assertNotIn("admin", [p["username"] for p in c.call(c.admin_report, 30)["people"]])
        # once the account is a person (a department, or signed in to the app) it counts again
        c.call(c.admin_update_user, admin["id"], department="Paint")
        self.assertIn("admin", [p["username"] for p in c.call(c.admin_org)])

    def test_audit_details_are_words_not_ids(self):
        c = self.core
        created = next(e for e in c.call(c.admin_audit, "ananya") if e["action"] == "user created")
        self.assertNotIn("=", created["details"])
        self.assertNotIn("role_id", created["details"])
        self.assertIn("Paint Artist", created["details"])
        self.assertIn("reports to Sneha Kulkarni", created["details"])
        c.call(c.admin_update_user, self.ann, role_id=self.roles["Roto Artist"], manager_id=None)
        changed = next(e for e in c.call(c.admin_audit, "ananya") if e["action"] == "user changed")
        self.assertIn("Designation: Paint Artist → Roto Artist", changed["details"])
        self.assertIn("Reports to: Sneha Kulkarni → nobody", changed["details"])
        c.call(c.admin_update_user, self.ann, disabled=1)
        actions = [e["action"] for e in c.call(c.admin_audit, "ananya")]
        self.assertIn("account disabled", actions)
        c.call(c.admin_save_role, None, name="Paint Lead 2", level=60, announce="department", create_rooms=1,
               manage_users=0, see_all=1, always_visible=0)
        role = next(e for e in c.call(c.admin_audit, "Paint Lead 2") if e["action"] == "designation created")
        self.assertEqual(role["details"], "level 60 · announcements to their department · can create rooms · "
                                          "cannot manage accounts · sees everyone")
        c.call(c.admin_announce, "Hello", "Everyone", "all")
        sent = next(e for e in c.call(c.admin_audit, "Hello") if e["action"] == "announcement sent")
        self.assertEqual(sent["details"], "Everyone in the studio")

    def test_report_names_people_without_a_department(self):
        c = self.core
        c.call(c.admin_create_user, must_change=False, username="nodept", password="Artist2026")
        depts = [d["department"] for d in c.call(c.admin_report, 30)["departments"]]
        self.assertIn("No department", depts)
        self.assertNotIn("(none)", depts)

    def test_cleanup_can_count_first(self):
        c = self.core
        preview = c.call(c.admin_cleanup_files, 3, dry_run=True)
        self.assertEqual(preview, {"removed": 0, "bytes": 0})
        self.assertFalse(any(e["action"] == "files cleaned up" for e in c.call(c.admin_audit)),
                         "counting is not a clean-up")

    def test_sender_and_admins_see_read_counts(self):
        c = self.core
        c.call(c.admin_create_user, must_change=False, username="boss", password="Artist2026",
               display_name="Boss", department="Paint", is_admin=1)
        c.call(c.admin_announce, "Fire drill", "At 3 PM", "all")
        boss, ann = Client("boss"), Client("ananya")
        try:
            mine = boss.login["announcements"][0]
            self.assertEqual((mine["read_count"], mine["total"]), (0, 3))
            self.assertNotIn("total", ann.login["announcements"][0], "only the sender and admins")
        finally:
            boss.close()
            ann.close()

    def test_read_count_goes_up_live_for_the_sender_and_admins(self):
        c = self.core
        c.call(c.admin_create_user, must_change=False, username="boss", password="Artist2026",
               display_name="Boss", department="Paint", is_admin=1)
        ann_id = c.call(c.admin_announce, "Fire drill", "At 3 PM", "all")
        boss, ann = Client("boss"), Client("ananya")
        try:
            ann.request("announcement_read", id=ann_id)
            update = boss.wait_for("announcement_reads")
            self.assertEqual((update["id"], update["read_count"], update["total"]), (ann_id, 1, 3))
        finally:
            boss.close()
            ann.close()

    def test_server_info_says_when_backups_run(self):
        info = self.core.call(self.core.admin_server_info)
        self.assertEqual((info["backup_enabled"], info["backup_hour"], info["chat_log_enabled"]), (True, 2, True))


class ConsolePagesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(sys.argv)
        QMessageBox.warning = QMessageBox.critical = QMessageBox.information = staticmethod(lambda *a, **k: None)

    def setUp(self):
        from server.admin_gui import ServerWindow
        from server.console_api import LocalApi
        self.tmp = tempfile.mkdtemp()
        self.core = c = ServerCore(self.tmp)
        c.config.update(tcp_port=PORT + 10, discovery_port=PORT + 11)
        c.start()
        c.call(c.admin_save_department, None, "Paint")
        self.a = c.call(c.admin_create_user, must_change=False, username="ananya", password="Artist2026",
                        display_name="Ananya Bose", department="Paint")
        self.b = c.call(c.admin_create_user, must_change=False, username="arjun", password="Artist2026",
                        display_name="Arjun Mehta", department="Paint")
        self.win = ServerWindow(LocalApi(c))

    def tearDown(self):
        self.core.stop()
        self.win.quitting = True
        self.win.close()
        self.win.deleteLater()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def page(self, cls_name):
        return next(p for _t, _i, p in self.win.pages if type(p).__name__ == cls_name)

    def test_names_and_icons(self):
        titles = [t for t, _i, _p in self.win.pages]
        self.assertIn("Announcements", titles)
        self.assertNotIn("Announcement", titles)
        icons = {t: i for t, i, _p in self.win.pages}
        self.assertEqual((icons["Holidays"], icons["Storage"]), ("sun", "hdd"))
        self.assertNotEqual(icons["Departments"], icons["Storage"])
        self.assertIsNotNone(self.page("DashboardPage").scroll_area, "Dashboard scrolls on a short screen")
        self.assertIsNotNone(self.page("UpdatesPage").scroll_area)
        self.assertIsNotNone(self.page("StoragePage").scroll_area)

    def test_settings_keep_unsaved_edits(self):
        page = self.page("SettingsPage")
        page.refresh()
        self.assertFalse(page.unsaved.isVisibleTo(page))
        page.name.setText("Half typed")
        self.assertTrue(page._dirty)
        self.assertTrue(page.unsaved.isVisibleTo(page))
        self.assertEqual(page.save_btn.text(), "Save changes")
        page.refresh()                                  # what the once-a-minute tick does
        self.assertEqual(page.name.text(), "Half typed", "the admin's typing survives the refresh")
        page.refresh(force=True)                        # Discard changes
        self.assertEqual(page.name.text(), self.core.config["server_name"])
        self.assertFalse(page._dirty)
        page.name.setText("Nilgiri VFX")
        page.save()
        self.assertEqual(self.core.config["server_name"], "Nilgiri VFX")
        self.assertFalse(page._dirty)
        self.assertEqual(page.save_btn.text(), "Save settings")
        page.max_mb.setValue(20480)
        self.assertEqual(page.max_mb_hint.text(), "= 20 GB")
        self.assertTrue(page.storage.placeholderText().startswith("Default: "))

    def test_updates_page_does_not_call_old_clients_up_to_date(self):
        c = self.core
        c.call(lambda: c.db.set_client(self.a, "1.6.0", "PC-7"))
        c.call(lambda: c.db.set_client(self.b, "99.0.0", "PC-8"))
        page = self.page("UpdatesPage")
        page.refresh()
        rows = {page.people.item(r, 0).text(): [page.people.item(r, k).text() for k in range(6)]
                for r in range(page.people.rowCount())}
        self.assertEqual(rows["Ananya Bose"][5], "Older than this server")
        self.assertEqual(rows["Arjun Mehta"][5], "✓ Up to date")
        self.assertEqual(page.people.horizontalHeaderItem(5).text(), "Status")
        self.assertIn(" is on ", page.people_label.text(), "one person: 'is'")
        self.assertIn("1 on an older version", page.people_label.text())
        self.assertFalse(page.remind_btn.isEnabled())
        self.assertEqual(page.remind_btn.toolTip(), "Publish a client update first")
        self.assertFalse(page.people.isColumnHidden(3))
        c.call(lambda: c.db.set_client(self.a, "1.6.0", ""))
        c.call(lambda: c.db.set_client(self.b, "99.0.0", ""))
        page.refresh()
        self.assertTrue(page.people.isColumnHidden(3), "nobody's PC is known: no empty PC column")

    def test_buttons_follow_the_selection(self):
        page = self.page("UsersPage")
        page.refresh()
        self.assertFalse(page.edit_btn.isEnabled())
        self.assertFalse(page.delete_btn.isEnabled())
        row = next(r for r in range(page.table.rowCount()) if page.table.item(r, 0).text() == "ananya")
        page.table.selectRow(row)
        self.assertTrue(page.edit_btn.isEnabled())
        self.assertTrue(page.toggle_btn.text().startswith("Disable"))
        self.core.call(self.core.admin_update_user, self.a, disabled=1)
        page.refresh()
        self.assertEqual(page.toggle_btn.text(), "Enable")
        self.assertTrue(page.table.isColumnHidden(3), "no sections anywhere: no empty Section column")
        for cls_name in ("RoomsPage", "RolesPage", "OnlinePage", "HolidaysPage"):
            p = self.page(cls_name)
            p.refresh()
            p.table.clearSelection()
            danger = [b for b in p.findChildren(type(page.delete_btn)) if b.text().startswith("Delete")
                      or b.text().startswith("Disconnect")]
            self.assertTrue(danger and not any(b.isEnabled() for b in danger), cls_name)

    def test_risky_actions_ask_first_and_enter_does_not_confirm(self):
        from server import admin_gui
        boxes = []

        def fake_exec(box):
            boxes.append(box)
            return 0
        old = QMessageBox.exec
        QMessageBox.exec = fake_exec
        try:
            self.assertFalse(admin_gui.confirm(None, "Delete user", "Delete Ananya Bose?", "Delete"))
        finally:
            QMessageBox.exec = old
        box = boxes[0]
        self.assertEqual(box.defaultButton(), box.button(QMessageBox.Cancel), "Enter means Cancel")
        self.assertEqual(box.textFormat(), Qt.PlainText)
        self.assertIn("Delete", [b.text() for b in box.buttons()])
        # delete and disable go through it
        page = self.page("UsersPage")
        page.refresh()
        row = next(r for r in range(page.table.rowCount()) if page.table.item(r, 0).text() == "ananya")
        page.table.selectRow(row)
        asked = []
        old_confirm = admin_gui.confirm
        admin_gui.confirm = lambda *a, **k: asked.append(a[1]) or False
        try:
            page.toggle_disabled()
            page.delete_user()
        finally:
            admin_gui.confirm = old_confirm
        self.assertEqual(asked, ["Disable account", "Delete user"])
        user = next(u for u in self.core.call(self.core.admin_users) if u["id"] == self.a)
        self.assertFalse(user["disabled"], "nothing happened without a yes")

    def test_announcements_page(self):
        page = self.page("AnnouncePage")
        page.refresh()
        self.assertFalse(page.send_btn.isEnabled(), "no body: nothing to send")
        page.body.setPlainText("Server maintenance at 6")
        self.assertTrue(page.send_btn.isEnabled())
        self.assertEqual(page.dept.itemText(0), "Everyone in the studio")
        self.assertEqual(page.reach.text(), "2 people get a pop-up they must acknowledge")
        self.assertFalse(page.reads_btn.isEnabled())

    def test_rooms_show_a_count_with_the_names_on_hover(self):
        c = self.core
        c.call(c.admin_save_room, None, "AK74 Project", "", [self.a, self.b])
        page = self.page("RoomsPage")
        page.refresh()
        self.assertEqual(page.table.horizontalHeaderItem(2).text(), "Members")
        item = page.table.item(0, 2)
        self.assertEqual(item.text(), "2 people")
        self.assertEqual(item.toolTip(), "Ananya Bose\nArjun Mehta")

    def test_room_dialog_filter_count_and_no_console_account(self):
        from server.admin_gui import RoomDialog
        users = self.core.call(self.core.admin_users)
        dlg = RoomDialog(self.win, users, {"name": "R", "topic": "", "members": [self.a]})
        texts = [dlg.list.item(i).text() for i in range(dlg.list.count())]
        self.assertFalse(any(t.startswith("Administrator") for t in texts))
        self.assertEqual(texts, sorted(texts, key=str.lower))
        self.assertIn("1 selected", dlg.count.text())
        dlg.filter.setText("arjun")
        dlg._check_all(Qt.Unchecked)                      # 'Clear' only clears the people shown
        self.assertEqual(dlg.members(), [self.a])
        dlg._check_all(Qt.Checked)
        self.assertEqual(sorted(dlg.members()), sorted([self.a, self.b]))
        self.assertIn("2 selected", dlg.count.text())
        dlg.deleteLater()

    def test_reports_chart_shows_every_day(self):
        from server.admin_gui import ReportsPage, _DailyChart
        today = datetime.date(2026, 9, 29)
        days = ReportsPage.every_day([{"day": "2026-09-28", "messages": 16}, {"day": "2026-09-29", "messages": 39}],
                                     30, today)
        self.assertEqual(len(days), 30)
        self.assertEqual((days[0]["day"], days[0]["messages"]), ("2026-08-31", 0))
        self.assertEqual(days[-1], {"day": "2026-09-29", "messages": 39})
        chart = _DailyChart()
        chart.resize(600, 110)
        chart.set_data(days)
        chart.grab()                                    # paints: the labels and the busiest day
        chart.set_data([{"day": "2026-09-29", "messages": 0}])
        chart.grab()

    def test_audit_old_rows_in_words(self):
        page = self.page("AuditPage")
        names = {("role_id", "63"): "Paint Artist", ("manager_id", "11"): "Sneha Kulkarni"}

        def lookup(key, value):
            return names.get((key, str(value).strip("'")), str(value).strip("'") or "none")
        old = {"action": "user created", "details": "username=ananya, display_name=Ananya Bose, "
                                                    "department=Paint, role_id=63, manager_id=11"}
        self.assertEqual(page.readable(old, lookup), "Ananya Bose · Paint · Paint Artist · reports to Sneha Kulkarni")
        self.assertEqual(page.readable({"action": "announcement sent", "details": "all"}, lookup),
                         "Everyone in the studio")
        self.assertEqual(page.readable({"action": "user changed", "details": "role_id: 63 -> 63; title: '' -> 'Lead'"},
                                       lookup), "Designation: Paint Artist → Paint Artist · Job title: none → Lead")
        self.assertEqual(page.sentence("server console"), "Server console")
        self.assertEqual(page.sentence("ananya"), "ananya")
        page.refresh()
        self.assertEqual(page.table.item(0, 1).text()[:1], page.table.item(0, 1).text()[:1].upper())

    def test_storage_clean_up_starts_from_the_rule_that_applies(self):
        self.core.config.update(file_retention_days=3)
        page = self.page("StoragePage")
        page.refresh()
        self.assertEqual(page.days.value(), 3)
        page.days.setValue(10)                          # the admin's own number stays
        page.refresh()
        self.assertEqual(page.days.value(), 10)

    def test_departments_tree_indents_only_with_sections(self):
        page = self.page("DepartmentsPage")
        page.refresh()
        self.assertFalse(page.tree.rootIsDecorated())
        dept = next(d for d in self.core.call(self.core.admin_departments) if d["name"] == "Paint")
        self.core.call(self.core.admin_save_department, None, "Prep", dept["id"])
        page.refresh()
        self.assertTrue(page.tree.rootIsDecorated())

    def test_small_helpers(self):
        from server.admin_gui import uptime_text, version_text
        self.assertEqual(uptime_text(5 * 60), "up 5 min")
        self.assertEqual(uptime_text(3 * 3600 + 20 * 60), "up 3 h 20 min")
        self.assertEqual(uptime_text(2 * 86400), "up 2 days")
        self.assertEqual(version_text(""), "no version seen yet")
        self.assertEqual(version_text("older"), "older than 1.6.2")
        self.assertEqual(version_text("older than 1.6.2"), "older than 1.6.2")
        self.assertEqual(version_text("1.11.1"), "1.11.1")

    def test_every_page_still_refreshes(self):
        for title, _icon, page in self.win.pages:
            with self.subTest(page=title):
                page.refresh()
        self.win.show()
        for i in range(len(self.win.pages)):
            self.win.show_page(i)
        time.sleep(0.05)


if __name__ == "__main__":
    unittest.main()
