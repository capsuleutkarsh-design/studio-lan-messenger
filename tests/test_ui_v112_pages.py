"""1.12.0 UI refinement - the pages: Home, Org, News and Files - against a real server where it matters."""

import os
import shutil
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QLabel, QMessageBox, QPushButton  # noqa: E402

from common import protocol as P  # noqa: E402
from server.core import ServerCore  # noqa: E402
from tests import test_server as base  # noqa: E402
from tests.test_client_v19 import settle, wait_until  # noqa: E402

PORT = 17170


class Other(base.Client):
    """Someone else, on a plain connection to the same server."""

    def __init__(self, username, password="Artist2026"):
        base.PORT, old = PORT, base.PORT
        try:
            super().__init__(username, password)
        finally:
            base.PORT = old


def person(uid, name, dept, manager=None, level=0, status="offline"):
    return {"id": uid, "name": name, "department": dept, "section": "", "designation": "Artist",
            "level": level, "manager_id": manager, "status": status}


# a lead with reports from four departments (a wide chart), one of them with a team of its own
WIDE = [person(1, "Rajiv Menon", "Production", level=90),
        person(2, "Deepak Joshi", "Production", 1, level=60), person(3, "Farhan Qureshi", "Production", 2),
        person(4, "Meera Iyer", "Compositing", 1, level=60), person(5, "Priya Sharma", "Compositing", 4),
        person(6, "Arjun Mehta", "Compositing", 4), person(7, "Sneha Kulkarni", "Paint", 1, level=60),
        person(8, "Ananya Bose", "Paint", 7), person(9, "Rohan Desai", "Roto", 1, level=60),
        person(10, "Kavya Reddy", "Roto", 9), person(11, "Vikram Nair", "IT")]


class FakeTransfer:
    def __init__(self, **kw):
        self.kind, self.name, self.size, self.done, self.state = "upload", "plate.exr", 100, 0, "running"
        self.conv, self.speed, self.error, self.hidden, self.path, self.dest_path = "", 0.0, "", False, "", ""
        self.__dict__.update(kw)

    @property
    def active(self):
        return self.state in ("waiting", "running")

    def cancel(self):
        self.state = "cancelled"


class UiV112PagesTest(unittest.TestCase):
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
        cls.core = ServerCore(os.path.join(cls.tmp, "server"))
        cls.core.config.update(tcp_port=PORT, discovery_port=PORT + 1)
        cls.core.start()
        mk = lambda u, n, **kw: cls.core.call(cls.core.admin_create_user, must_change=False,  # noqa: E731
                                              username=u, password="Artist2026", display_name=n, **kw)
        cls.lead = mk("lea", "Lea Kapoor")
        cls.ann = mk("ann", "Ann Rao")
        cls.ben, cls.cat = mk("ben", "Ben Das"), mk("cat", "Cat Iyer")
        cls.core.call(cls.core.admin_update_user, cls.ann, manager_id=cls.lead)
        import client.main as cm
        import client.ui.main_window as mw
        mw.play_sound = lambda kind="notify": None
        cls.ctl = cm.App()
        cls.ctl.do_login("127.0.0.1", PORT, "ann", "Artist2026", False)
        assert wait_until(cls.app, lambda: cls.ctl.main is not None, 15), "client did not sign in"
        cls.main = cls.ctl.main
        cls.main.resize(1300, 820)
        settle(cls.app, 1.2)
        if getattr(cls.main, "_tour", None) is not None:
            cls.main._tour.accept()                 # the welcome tour of a first sign-in
        settle(cls.app, 0.2)

    @classmethod
    def tearDownClass(cls):
        cls.main.popup_stack.close_all()
        for p in list(getattr(cls.main, "popups", {}).values()):
            p.close()
        cls.main.quitting = True
        cls.core.stop()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def go_home(self):
        self.main.go_home()
        settle(self.app, 0.4)
        self.main.home.rebuild()
        settle(self.app, 0.3)
        return self.main.home

    # ------------------------------------------------------------ one gutter for every page
    def test_page_header_lines_up_with_the_page(self):
        from common import theme as T
        from client.ui.pages import PageHeader, scroll_column
        header = PageHeader("Files")
        m = header.lay.contentsMargins()
        self.assertEqual((m.left(), m.right()), (T.PAGE_GUTTER, T.PAGE_GUTTER))
        _area, lay = scroll_column()
        m2 = lay.contentsMargins()
        self.assertEqual((m2.left(), m2.right()), (T.PAGE_GUTTER, T.PAGE_GUTTER))

    # ------------------------------------------------------------ Home
    def test_home_dates_and_quick_actions(self):
        import datetime
        home = self.go_home()
        texts = [w.text() for w in home.findChildren(QLabel)]
        d = datetime.date.today()
        self.assertTrue(any(t.startswith(f"{d:%A} {d.day} {d:%B}") for t in texts), "hero date without a padded day")
        self.assertIn("Message anyone · Ctrl+K", texts, "the New chat tile says what it does")
        # the last line of tiles shares the whole width: no empty slot on the right
        tiles = [w for w in home.findChildren(QLabel) if w.text() in ("New chat", "Org chart")]
        self.assertEqual(len(tiles), 2)
        first, last = (t.parentWidget() for t in tiles)
        r1 = first.mapTo(home, QPoint(first.width(), 0)).x()
        rows = {}
        for lbl in home.findChildren(QLabel):
            if lbl.text() in ("New chat", "New room", "Reminder", "Set status", "Org chart", "Announce"):
                tile = lbl.parentWidget()
                top = tile.mapTo(home, QPoint(0, 0)).y()
                rows.setdefault(top, []).append(tile.mapTo(home, QPoint(tile.width(), 0)).x())
        rights = {max(v) for v in rows.values()}
        self.assertEqual(len(rights), 1, f"every line of tiles ends at the same edge: {rows}")
        self.assertTrue(r1 > 0 and last.width() > 0)

    def test_catch_up_fills_up_with_recent_chats(self):
        store = self.main.store
        ben = Other("ben")
        ben.request("send", conv=f"u:{self.ann}", text="an older chat, already read")
        conv = P.direct_conv(self.ben)
        self.assertTrue(wait_until(self.app, lambda: store.conversation(conv).last is not None))
        self.main.open_conv(conv)                            # read it
        settle(self.app, 0.4)
        cat = Other("cat")
        cat.request("send", conv=f"u:{self.ann}", text="Word " * 80)       # one unread chat, a long preview
        cconv = P.direct_conv(self.cat)
        self.assertTrue(wait_until(self.app, lambda: store.conversation(cconv).unread >= 1))
        home = self.go_home()
        from client.ui.widgets import ConvItem
        items = {i.conv: i for i in home.findChildren(ConvItem)}
        self.assertIn(cconv, items)
        self.assertIn(conv, items, "a read chat fills the mostly empty card")
        self.assertIn("Recent", [w.text() for w in home.findChildren(QLabel)])
        self.assertEqual(items[cconv].INSET, 4, "rows line up with the card heading")
        self.assertTrue(items[cconv].subtitle.endswith("…"), "a cut preview ends in an ellipsis")
        self.assertLessEqual(len(items[cconv].subtitle), 121)
        for c in (ben, cat):
            c.close()

    def test_coming_up_rows_have_worded_buttons(self):
        store = self.main.store
        replies = []
        self.main.conn.request("reminder_add", replies.append, text="Send AK74 v014 to Meera", due_at=time.time() + 3600)
        self.assertTrue(wait_until(self.app, lambda: replies and store.reminders))
        home = self.go_home()
        buttons = [b.text() for b in home.findChildren(QPushButton)]
        self.assertIn("Done", buttons)
        self.assertIn("COMING UP", [w.text() for w in home.findChildren(QLabel)])
        rid = replies[0]["reminder"]["id"]
        self.main.conn.request("reminder_done", None, id=rid)
        self.assertTrue(wait_until(self.app, lambda: not any(r["id"] == rid for r in store.reminders)))

    def test_calendar_rows_use_icons_not_emoji(self):
        import datetime
        from client.ui import pages
        from client.ui.calendar_views import Entry, entries_from
        now = datetime.datetime.now()
        day = now.date().isoformat()
        data = {"items": [], "holidays": [{"day": day, "name": "Gandhi Jayanti"}],
                "leave": [{"id": 1, "first_day": day, "last_day": day, "name": "Ananya Bose"}],
                "people_days": [{"kind": "birthday", "user_id": 5, "name": "Priya Sharma", "day": day},
                                {"kind": "anniversary", "user_id": 6, "name": "Farhan", "day": day, "years": 5}]}
        entries = entries_from(data, {"holiday", "leave", "birthday", "anniversary"}, me_id=1)
        icons = {e.kind: pages._entry_icon(e) for e in entries}
        self.assertEqual(icons, {"holiday": "sun", "leave": "palm", "birthday": "cake", "anniversary": "gift"})
        for e in entries:
            self.assertFalse(pages._entry_title(e)[:1] in "🎂🌴📝🎉⏳", pages._entry_title(e))
        meeting = Entry("m", "meeting", "✕ Dailies", now, now, False, {"my_rsvp": "no"})
        self.assertEqual(pages._entry_icon(meeting), "users")
        self.assertEqual(pages._entry_title(meeting), "Dailies · declined")

    # ------------------------------------------------------------ News
    def test_news_keeps_new_ones_marked_while_open(self):
        from client.ui.pages import AnnouncementCard
        store = self.main.store
        ann_id = self.core.call(self.core.admin_announce, "Fire drill", "At 3 PM, use the east stairs", "all")
        self.assertTrue(wait_until(self.app, lambda: any(a["id"] == ann_id for a in store.announcements)))
        for p in list(getattr(self.main, "popups", {}).values()):
            p.hide()
        page = self.main.announcements
        self.assertEqual(page.header.findChildren(QLabel)[0].text(), "News", "named like the rail")
        self.main.rail_clicked("announcements")
        settle(self.app, 0.3)
        page.mark_all_read()                               # what the rail does 1.5 s later
        self.assertTrue(wait_until(self.app, lambda: all(a.get("read") for a in store.announcements)))
        settle(self.app, 0.3)
        cards = [c for c in page.findChildren(AnnouncementCard) if c.isVisible()]
        fresh = [c for c in cards if c.unread]
        self.assertTrue(fresh, "still drawn as new while the page is open")
        self.assertIn("New", [w.text() for w in fresh[0].findChildren(QLabel)])
        self.assertNotIn("border-left", fresh[0].styleSheet())
        body = [w for w in fresh[0].findChildren(QLabel) if "east stairs" in w.text()][0]
        self.assertEqual(body.indent(), 0)
        self.main.rail_clicked("chats")                    # away and back: now it's read
        settle(self.app, 0.2)
        self.main.rail_clicked("announcements")
        settle(self.app, 0.3)
        self.assertFalse(any(c.unread for c in page.findChildren(AnnouncementCard) if c.isVisible()))
        self.main.rail_clicked("chats")

    # ------------------------------------------------------------ Org
    def test_org_page_hint_and_my_card(self):
        page = self.main.directory
        self.main.rail_clicked("directory")
        settle(self.app, 0.5)
        chart = page.browser.chart
        self.assertEqual(chart.loner_hint, "", "an artist can't set 'Reports to': no hint about the console")
        page.browser.set_view("chart", announce=False)
        settle(self.app, 0.3)
        for _rect, text in chart.labels:
            self.assertNotIn("console", text)
        page.browser.set_view("cards", announce=False)
        settle(self.app, 0.3)
        grid = page.browser.grid
        opened = []
        page.browser.edit_me.disconnect()
        page.browser.edit_me.connect(lambda: opened.append(True))
        mine = [r for kind, r, u in grid.items if kind == "card" and u["id"] == self.ann]
        self.assertTrue(mine)
        from PySide6.QtTest import QTest
        QTest.mouseClick(grid, Qt.LeftButton, Qt.NoModifier, grid._button_rect(mine[0]).center())
        self.assertEqual(opened, [True], "my card's button sets my status")
        page.browser.edit_me.disconnect()
        page.browser.edit_me.connect(self.main.edit_status_message)
        self.main.rail_clicked("chats")

    def test_chart_hint_group_counts_and_fit(self):
        from common.orgviews import OrgBrowser, OrgChart
        chart = OrgChart()
        chart.set_people(WIDE)
        self.assertIn("Users page", chart.labels[-1][1], "the console's wording by default")
        counts = {g["group"]: g["count"] for _b, g in chart.group_boxes}
        self.assertEqual(counts, {"Production": 2, "Compositing": 3, "Paint": 2, "Roto": 2},
                         "a department pill counts the whole branch")
        chart = OrgChart(loner_hint="(set 'Reports to' on the Users page)")
        chart.set_people(WIDE)
        self.assertNotIn("((", chart.labels[-1][1])
        chart = OrgChart(loner_hint="")
        chart.set_people(WIDE)
        self.assertNotIn("(", chart.labels[-1][1])

        b = OrgBrowser(view="chart", loner_hint="")
        b.resize(700, 600)
        b.show()
        b.set_people(WIDE)
        settle(self.app, 0.4)
        self.assertLess(b.chart.zoom, 1.0, "a chart wider than the view opens shrunk")
        self.assertGreaterEqual(b.chart.zoom, OrgBrowser.MIN_FIT_ZOOM - 0.001)
        b.chart.user_zoomed = True
        b.chart.set_zoom(1.0)
        b.refresh()
        settle(self.app, 0.3)
        self.assertEqual(b.chart.zoom, 1.0, "a zoom chosen by hand is kept")
        # List: the department column only when it isn't already the group
        b.set_view("list", announce=False)
        self.assertTrue(b.tree.isColumnHidden(2))
        b.list_mode.setCurrentIndex(b.list_mode.findData("reporting"))
        self.assertFalse(b.tree.isColumnHidden(2))
        self.assertTrue(b.search.placeholderText().endswith("…"))
        self.assertGreaterEqual(b.search.minimumWidth(), 240)
        b.close()

    def test_cards_share_the_width(self):
        from common.orgviews import PeopleGrid
        g = PeopleGrid()
        g.resize(845, 400)
        g.set_people(WIDE[:9], me_id=6)
        cards = [r for kind, r, _u in g.items if kind == "card"]
        self.assertTrue(PeopleGrid.CARD_MIN_W <= cards[0].width() <= PeopleGrid.CARD_MAX_W)
        cols, card_w = g._columns(845)
        self.assertEqual(card_w, cards[0].width())
        used = cols * card_w + (cols - 1) * PeopleGrid.GAP
        self.assertLess(845 - 2 * PeopleGrid.MARGIN - used, cols + 1, "the columns fill the row: no empty strip")
        heads = [r for kind, r, _u in g.items if kind == "dept"]
        first_card = min(cards, key=lambda r: r.top())
        self.assertGreaterEqual(first_card.top() - heads[0].bottom(), 10, "heading sits clear of its cards")

    # ------------------------------------------------------------ Files
    def test_transfer_status_and_rows(self):
        from client.ui.pages import TransferRow, transfer_status
        t = FakeTransfer(size=3 * 1024 ** 3, done=211 * 1024 ** 2, speed=200 * 1024 ** 2)
        text = transfer_status(t)
        self.assertIn(" of 3.0 GB · 6% · ", text)
        self.assertTrue(text.endswith(" left"), text)
        row = TransferRow(self.main.transfers, t, self.main.store)
        self.assertTrue(row.meta.text().startswith(text), "the Files page says what the chat's upload strip says")
        row.deleteLater()
        page = self.main.transfers_page
        long = "AK74_0450_comp_" + "very_long_name_" * 12 + "v014.exr"
        done = FakeTransfer(name=long, state="done", size=10, done=10)
        running = FakeTransfer(name="plate.exr", size=10, done=5)
        page.add(running)
        self.assertFalse(page.clear.isEnabled(), "nothing finished: nothing to clear")
        page.add(done)
        self.assertTrue(page.clear.isEnabled())
        self.main.rail_clicked("transfers")
        self.main.resize(1000, 700)
        settle(self.app, 0.3)
        row = page.rows[id(done)]
        self.assertIsInstance(row, TransferRow)
        self.assertLessEqual(row.width(), page.area.viewport().width(), "a long name never widens the row")
        self.assertTrue(row.name.is_elided())
        self.assertTrue(row.b_remove.isVisibleTo(row))
        self.assertFalse(page.rows[id(running)].b_remove.isVisibleTo(page.rows[id(running)]))
        row.b_remove.click()
        settle(self.app, 0.2)
        self.assertNotIn(id(done), page.rows, "one row can be removed on its own")
        running.state = "cancelled"
        page.changed(running)
        page.clear_finished()
        self.assertFalse(page.rows)
        self.main.resize(1300, 820)
        self.main.rail_clicked("chats")


if __name__ == "__main__":
    unittest.main()
