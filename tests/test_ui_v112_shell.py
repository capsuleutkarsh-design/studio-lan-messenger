"""1.12.0 UI refinement of the shell - the main window, chat list, rail, pop-ups and reminders - against a real
server: Ctrl+K finds everyone, a short or narrow window gives way instead of overlapping, pop-ups and reminder
cards share one column, clearer menus, and reminder times written the one app-wide way."""

import datetime
import os
import shutil
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, QSize, Qt  # noqa: E402
from PySide6.QtGui import QKeyEvent  # noqa: E402
from PySide6.QtWidgets import QApplication, QMenu, QMessageBox  # noqa: E402

from common import protocol as P  # noqa: E402
from server.core import ServerCore  # noqa: E402
from tests import test_server as base  # noqa: E402
from tests.test_client_v19 import settle, wait_until  # noqa: E402

PORT = 17160


class Other(base.Client):
    """Someone else, on a plain connection to the same server."""

    def __init__(self, username, password="Artist2026"):
        base.PORT, old = PORT, base.PORT
        try:
            super().__init__(username, password)
        finally:
            base.PORT = old


class Menus:
    """Menus opened inside the block are recorded instead of shown (QMenu.exec would wait for a click)."""

    def __enter__(self):
        self.opened = []
        self._init = QMenu.__init__
        opened = self.opened

        def init(menu, *a, **k):
            self._init(menu, *a, **k)
            menu.exec = lambda *a, **k: opened.append(menu)
        QMenu.__init__ = init
        return self

    def __exit__(self, *exc):
        QMenu.__init__ = self._init

    @staticmethod
    def texts(menu):
        return [a.text() for a in menu.actions() if not a.isSeparator()]


class UiV112ShellTest(unittest.TestCase):
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
        cls.ben = mk("ben", "Ben Das")
        cls.cat = mk("cat", "Catriona Iyer")      # nobody has messaged her
        cls.core.call(cls.core.admin_update_user, cls.ann, manager_id=cls.lead)
        import client.main as cm
        import client.ui.main_window as mw
        mw.play_sound = lambda kind="notify": None
        cls.ctl = cm.App()
        cls.ctl.do_login("127.0.0.1", PORT, "ann", "Artist2026", False)
        assert wait_until(cls.app, lambda: cls.ctl.main is not None, 15), "client did not sign in"
        cls.main = cls.ctl.main
        cls.main.show()
        cls.main.resize(1300, 820)
        settle(cls.app, 1.2)
        if getattr(cls.main, "_tour", None) is not None:
            cls.main._tour.accept()                 # the welcome tour of a first sign-in
        cls.benc = Other("ben")
        cls.room = cls.benc.request("create_room", name="Paint & Roto", topic="", members=[cls.ann])["room_id"]
        cls.root = cls.benc.request("send", conv=P.room_conv(cls.room), text="Plates are in for FAL_030")
        cls.benc.request("send", conv=P.direct_conv(cls.ann), text="Dailies at 4?")
        assert wait_until(cls.app, lambda: cls.main.store.conversation(P.direct_conv(cls.ben)).last is not None)
        settle(cls.app, 0.5)

    @classmethod
    def tearDownClass(cls):
        cls.main.popup_stack.close_all()
        for card in list(cls.main.reminder_cards.values()):
            card.close()
        cls.main.quitting = True
        cls.core.stop()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def setUp(self):
        self.main.resize(1300, 820)
        settle(self.app, 0.2)

    # ------------------------------------------------------------ Ctrl+K finds everyone
    def test_ctrl_k_finds_people_you_have_never_messaged(self):
        main, sb = self.main, self.main.sidebar
        main.focus_search()
        self.assertEqual(sb.page, "chats")
        self.assertIn("people", sb.search.placeholderText().lower())
        sb.search.setText("catri")
        settle(self.app, 0.1)
        conv = P.direct_conv(self.cat)
        self.assertIn(conv, sb.lists["chats"].items, "someone with no chat yet is found too")
        self.assertTrue(sb.lists["chats"].items[conv].subtitle, "with who they are, not an empty line")
        sb.search.setText("paint")
        settle(self.app, 0.1)
        self.assertIn(P.room_conv(self.room), sb.lists["chats"].items)
        sb.search.setText("catri")
        sb.open_first()                                    # Enter in the search box
        settle(self.app, 0.2)
        self.assertEqual(main.chat.conv, conv)
        self.assertEqual(sb.search.text(), "", "the search is cleared after jumping")

    def test_arrow_keys_pick_a_row_and_esc_clears(self):
        sb = self.main.sidebar
        self.main.rail_clicked("chats")
        sb.search.setText("a")
        settle(self.app, 0.1)
        rows = list(sb.lists["chats"].items)
        self.assertGreaterEqual(len(rows), 2)
        for key in (Qt.Key_Down, Qt.Key_Down):
            QApplication.sendEvent(sb.search, QKeyEvent(QKeyEvent.KeyPress, key, Qt.NoModifier))
        self.assertEqual(sb._kb, rows[1])
        self.assertTrue(sb.lists["chats"].items[rows[1]]._hover, "the picked row is lit")
        QApplication.sendEvent(sb.search, QKeyEvent(QKeyEvent.KeyPress, Qt.Key_Escape, Qt.NoModifier))
        self.assertEqual(sb.search.text(), "")

    def test_search_box_says_what_each_page_searches(self):
        sb = self.main.sidebar
        for page, word in (("contacts", "people"), ("rooms", "rooms"), ("chats", "chats")):
            self.main.rail_clicked(page)
            self.assertIn(word, sb.search.placeholderText().lower())
        self.main.rail_clicked("chats")

    def test_my_space_has_no_presence_dot(self):
        sb = self.main.sidebar
        self.main.rail_clicked("chats")
        sb.search.clear()
        sb.set_filter("all")
        mine = sb.lists["chats"].items.get(P.direct_conv(self.main.store.my_id))
        self.assertIsNotNone(mine, (sb.search.text(), sb.filter, list(sb.lists["chats"].items)))
        self.assertIsNone(mine.status)

    def test_mark_all_read_says_how_many(self):
        sb, said = self.main.sidebar, []
        sb.toast.connect(said.append)
        try:
            self.main.store.conversation(P.direct_conv(self.ben)).unread = 2
            sb._read_all()
            self.assertTrue(said and said[-1].startswith("Marked 1 chat"), said)
        finally:
            sb.toast.disconnect(said.append)

    # ------------------------------------------------------------ a small window gives way
    def test_short_windows_fold_the_rail_instead_of_overlapping(self):
        main = self.main
        for w, h in ((1300, 900), (1024, 700), (1024, 640), (960, 600)):
            main.resize(w, h)
            settle(self.app, 0.3)
            self.assertLessEqual(main._rail_need(main.rail_level), main.rail_frame.height(), (w, h))
        self.assertTrue(all(b.compact for b in main.rail.values()), "no labels on a 600 px window")
        main.resize(1300, 900)
        settle(self.app, 0.3)
        self.assertEqual(main.rail_level, 0)
        self.assertFalse(any(b.compact for b in main.rail.values()))
        self.assertFalse(main.b_search.isHidden())

    def test_a_thread_on_a_narrow_window_moves_the_list_aside(self):
        main, conv = self.main, P.room_conv(self.room)
        main.resize(960, 600)
        settle(self.app, 0.3)
        main.open_thread(conv, self.root["message"]["id"])
        settle(self.app, 0.4)
        self.assertTrue(main.sidebar.isHidden(), "three columns don't fit in 960 px")
        self.assertGreaterEqual(main.stack.width(), main.CHAT_MIN - 1)
        main.thread_panel.close_thread()
        settle(self.app, 0.4)
        self.assertFalse(main.sidebar.isHidden(), "back when the thread closes")
        main.resize(1300, 820)
        settle(self.app, 0.3)
        self.assertEqual(main.sidebar.width(), main._sidebar_want, "the chosen width comes back")

    # ------------------------------------------------------------ pages, rail, badges
    def test_other_pages_leave_no_chat_highlighted(self):
        main, conv = self.main, P.direct_conv(self.ben)
        main.open_conv(conv)
        self.assertEqual(main.sidebar.active_conv, conv)
        for key in ("directory", "announcements", "transfers", "calendar"):
            main.rail_clicked(key)
            self.assertIsNone(main.sidebar.active_conv, key)
        main.rail_clicked("chats")
        self.assertEqual(main.sidebar.active_conv, conv, "the open chat is marked again")

    def test_rail_names_and_calm_transfer_badge(self):
        main = self.main
        self.assertEqual(main.rail["myspace"].label, "My space")
        self.assertIn("News", main.rail["announcements"].toolTip())
        self.assertEqual(main.b_pin.icon_name, "on_top")
        main._update_transfers_badge()
        self.assertEqual(main.rail["transfers"].badge_kind, "neutral")

    # ------------------------------------------------------------ menus
    def test_chat_menu_is_grouped_and_offers_mark_as_read(self):
        main, conv = self.main, P.direct_conv(self.ben)
        main.store.conversation(conv).unread = 1
        with Menus() as menus:
            main.conv_menu(conv, QPoint(10, 10))
        texts = Menus.texts(menus.opened[-1])
        self.assertIn("Mark as read", texts)
        self.assertIn("Pin", texts)
        self.assertFalse([t for t in texts if "..." in t], texts)
        self.assertGreaterEqual(sum(a.isSeparator() for a in menus.opened[-1].actions()), 2)
        main.store.mark_read(conv)

    def test_me_menu_shows_my_status_and_escapes_ampersands(self):
        main = self.main
        with Menus() as menus:
            main.me_menu()
        m = menus.opened[-1]
        texts = Menus.texts(m)
        current = [a for a in m.actions() if a.text().endswith("\t✓")]
        self.assertEqual(len(current), 1)
        self.assertTrue(current[0].font().bold())
        self.assertIn("Profile photo && status…", texts)
        self.assertIn("Keyboard shortcuts\tCtrl+/", texts)
        self.assertFalse(m.actions()[0].text(), "the header is a card, not a greyed-out item")

    def test_menus_open_upwards_beside_the_avatar(self):
        main = self.main
        size = QSize(200, 300)
        pos = main._beside(main.me_btn, size)
        bottom = main.me_btn.mapToGlobal(QPoint(0, main.me_btn.height())).y()
        area = main.me_btn.screen().availableGeometry()
        self.assertEqual(pos.y() + size.height(), min(bottom, area.bottom() + 1))
        self.assertGreater(pos.x(), main.me_btn.mapToGlobal(QPoint(0, 0)).x())

    def test_focus_bar_says_who_gets_through(self):
        main, store = self.main, self.main.store
        f = dict(store.prefs.get("focus") or {})
        f["people"] = [self.cat, self.ben]
        store.set_pref("focus", f)
        phrase = main._focus_reach()
        self.assertEqual(phrase, "@mentions, Lea Kapoor (your lead) and 2 people you chose")
        main.start_focus(time.time() + 600)
        self.assertIn(phrase, main.focus_label.text())
        main.end_focus()
        f["people"] = []
        store.set_pref("focus", f)

    # ------------------------------------------------------------ toast, offline
    def test_toast_sits_over_the_chat_above_the_composer(self):
        main = self.main
        main.open_conv(P.direct_conv(self.ben))
        settle(self.app, 0.2)
        main.toast("Saved")
        g = main.toast_label.geometry()
        composer_top = main.chat.composer.mapTo(main, QPoint(0, 0)).y()
        self.assertLess(g.bottom(), composer_top)
        stack = main.stack.geometry()
        centre = main.stack.mapTo(main, QPoint(stack.width() // 2, 0)).x()
        self.assertLessEqual(abs(g.center().x() - centre), 2, "centred on the chat, not the window")
        main.toast("word " * 200)
        self.assertLessEqual(main.toast_label.width(), main.stack.width() - 40, "a long one wraps")

    def test_connection_lost_hides_stale_presence(self):
        main, sb = self.main, self.main.sidebar
        main.rail_clicked("chats")
        try:
            main._on_connection_lost("Disconnected from server")
            self.assertTrue(sb.offline)
            self.assertIn("reconnecting", main.banner.text())
            self.assertNotIn("Disconnected", main.banner.text(), "the raw reason is the tooltip")
            self.assertIn("Disconnected", main.banner.toolTip())
            item = sb.lists["chats"].items[P.direct_conv(self.ben)]
            self.assertIsNone(item.status)
        finally:
            main.banner.hide()
            main._was_offline = False
            sb.set_offline(False)
        self.assertIsNotNone(sb.lists["chats"].items[P.direct_conv(self.ben)].status)

    # ------------------------------------------------------------ pop-ups and reminders
    def test_pop_ups_and_reminders_share_one_column(self):
        main = self.main
        main.config["notifications"] = main.config["quick_reply"] = True
        main.popup_stack.close_all()
        settle(self.app, 0.1)
        room = P.room_conv(self.room)
        title = "Ben Das mentioned you in Paint & Roto and a very long room name that cannot fit"
        msg = {"id": 9001, "conv": room, "sender_id": self.ben, "kind": "text", "body": "x", "ts": time.time()}
        main.notify(title, "one " * 60, room, msg)
        main.notify(title, "two", room, msg)
        main._show_reminder({"id": 77, "due_at": time.time() - 11, "text": "Send the comp", "conv": ""},
                            sound=False)
        settle(self.app, 0.3)
        try:
            pop = next(p for p in main.popup_stack.popups if p.conv == room)
            self.assertEqual(pop.title.text(), title)
            self.assertTrue(pop.title.is_elided())
            self.assertFalse(pop.more.isHidden())
            self.assertEqual(pop.more.text(), "+1 more")
            self.assertEqual(pop.input.placeholderText(), "Reply in Paint & Roto…")
            self.assertLessEqual(pop.text.text().count("\n"), 1, "two lines at most")
            card = main.reminder_cards[77]
            self.assertFalse(pop.geometry().intersects(card.geometry()), "never on top of each other")
            self.assertLess(pop.geometry().top(), card.geometry().top(), "the reminder stays at the bottom")
            self.assertNotRegex(card.when.text(), r"\d\d:\d\d:\d\d", "no seconds on a fired reminder")
        finally:
            main.popup_stack.close_all()
            main.reminder_cards[77].close()
            settle(self.app, 0.2)


class PlannerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(sys.argv)

    def test_reminder_times_read_like_the_rest_of_the_app(self):
        from client.ui.planner_ui import fmt_due, weekday_picks
        today = datetime.date.today()
        at = datetime.datetime.combine(today, datetime.time(18, 0, 30)).timestamp()
        self.assertEqual(fmt_due(at), "Today 18:00")
        self.assertEqual(fmt_due(at, seconds=True), "Today 18:00:30")
        self.assertEqual(fmt_due(at, inline=True), "today at 18:00")
        later = datetime.datetime.combine(today + datetime.timedelta(days=5), datetime.time(9, 0))
        self.assertNotRegex(fmt_due(later.timestamp()), r"\b0\d [A-Z]", "days are not zero-padded")
        self.assertTrue(all(not label.split()[1].startswith("0") for label, _ in weekday_picks()))

    def test_quick_picks(self):
        from client.ui.planner_ui import presets
        night = presets(datetime.datetime(2026, 9, 30, 0, 40))
        labels = [label for label, _ in night]
        self.assertEqual(labels[0], "In 10 minutes")
        self.assertNotIn("In 1 minute", labels)
        self.assertIn("This morning, 09:00", labels, "after midnight the coming morning is offered")
        times = [ts for _, ts in night]
        self.assertEqual(times, sorted(times), "soonest first")
        sunday = [label for label, _ in presets(datetime.datetime(2026, 10, 4, 12, 0))]
        self.assertNotIn("Next Monday, 09:00", sunday, "on Sunday that is Tomorrow, 09:00")
        self.assertNotIn("This morning, 09:00", sunday)

    def test_pick_a_date_menu_item_shows_its_ampersand(self):
        from client.ui.planner_ui import when_menu
        m = when_menu(None, "Remind me", lambda ts: None)
        self.assertEqual(m.actions()[-1].text(), "Pick a date && time…")
        m = when_menu(None, "Send later", lambda ts: None, "Pick a date & time...")
        self.assertEqual(m.actions()[-1].text(), "Pick a date && time…")

    def test_time_dialog_quick_picks_stay_chosen(self):
        from client.ui.planner_ui import TimeDialog
        d = TimeDialog(None, "New reminder")
        chip = d.picks.buttons()[1]
        chip.click()
        self.assertTrue(chip.isChecked())
        self.assertTrue(chip.property("chip"))
        self.assertTrue(d.typed_hint.text().startswith("✓ "))
        self.assertNotRegex(d.typed_hint.text(), r"\d\d:\d\d:\d\d")
        d.typed.setText("tomorrow 2pm")
        self.assertIsNone(d.picks.checkedButton(), "typing replaces the quick pick")
        self.assertNotIn(",  ", d.typed.placeholderText())
        d.close()


if __name__ == "__main__":
    unittest.main()
