"""1.10.0: the look-and-feel release - pinned chats, the "New messages" line, galleries, shot links, path cards,
focus time, answer-from-the-pop-up, a tidier Home - against a real server."""

import os
import shutil
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QLabel, QMessageBox  # noqa: E402

from common import protocol as P  # noqa: E402
from server.core import ServerCore  # noqa: E402
from tests import test_server as base  # noqa: E402
from tests.test_client_v19 import settle, wait_until  # noqa: E402

PORT = 17050


class Other(base.Client):
    """Someone else, on a plain connection to the same server."""

    def __init__(self, username, password="Artist2026"):
        base.PORT, old = PORT, base.PORT
        try:
            super().__init__(username, password)
        finally:
            base.PORT = old


class UiV110Test(unittest.TestCase):
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
        cls.main.quitting = True
        cls.core.stop()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    # ------------------------------------------------------------ settings that follow you to any PC
    def test_the_welcome_tour_is_shown_once(self):
        self.assertTrue(wait_until(self.app, lambda: self.core.call(self.core.db.prefs, self.ann).get("tour_done")),
                        "the tour of the first sign-in is remembered on the server")
        before = getattr(self.main, "_tour", None)
        self.main._maybe_tour()
        self.assertIs(getattr(self.main, "_tour", None), before, "not shown a second time")

    def test_pinned_chats_stay_on_top_and_on_the_server(self):
        main, store = self.main, self.main.store
        conv = P.direct_conv(self.cat)
        main.pin_chat(conv, True)
        self.assertTrue(wait_until(self.app, lambda: conv in (self.core.call(self.core.db.prefs, self.ann)
                                                               .get("pinned_chats") or [])))
        boot = self.core.call(self.core.bootstrap, self.ann, "t")
        self.assertIn(conv, boot["prefs"]["pinned_chats"], "a new PC gets them at sign-in")
        main.rail_clicked("chats")
        main.sidebar.rebuild()
        lst = main.sidebar.lists["chats"]
        labels = [w.text() for w in lst.findChildren(QLabel) if w.text() in ("PINNED", "CHATS")]
        self.assertIn("PINNED", labels)
        self.assertIn(conv, lst.items, "pinned even with no message yet")
        self.assertTrue(lst.items[conv].pinned)
        for c in list(store.pinned_chats()):
            store.set_pinned(c, False)
        self.assertFalse(store.is_pinned(conv))

    def test_only_known_settings_are_kept(self):
        replies = []
        self.main.conn.request("set_pref", replies.append, key="is_admin", value=True)
        self.assertTrue(wait_until(self.app, lambda: bool(replies)))
        self.assertFalse(replies[0]["ok"])

    # ------------------------------------------------------------ the "New messages" line
    def test_new_messages_line_and_jump(self):
        main = self.main
        main.open_conv(P.direct_conv(self.ann))            # looking at something else
        settle(self.app, 0.3)
        ben = Other("ben")
        ben.request("send", conv=f"u:{self.ann}", text="old news")
        conv = P.direct_conv(self.ben)
        self.assertTrue(wait_until(self.app, lambda: main.store.conversation(conv).unread >= 1))
        main.open_conv(conv)
        settle(self.app, 0.5)
        main.open_conv(P.direct_conv(self.ann))
        settle(self.app, 0.3)
        for text in ("FAL_030 v12 is up", "Edges look soft", "Can you check?"):
            ben.request("send", conv=f"u:{self.ann}", text=text)
        self.assertTrue(wait_until(self.app, lambda: main.store.conversation(conv).unread == 3))
        main.open_conv(conv)
        self.assertTrue(wait_until(self.app, lambda: main.chat.divider is not None))
        rows = main.chat.rows
        at = rows.index(main.chat.divider)
        self.assertEqual(rows[at + 1].msg["body"], "FAL_030 v12 is up", "the line sits above the first unread")
        # my answer: the line has done its job
        main.chat.input.setPlainText("On it")
        main.chat.send_text()
        self.assertTrue(wait_until(self.app, lambda: main.chat.divider is None or main.chat.divider.isHidden()))
        ben.close()

    # ------------------------------------------------------------ pictures
    def test_pictures_sent_together_share_a_gallery(self):
        main, store = self.main, self.main.store
        from client.ui.gallery import GalleryRow
        conv = P.direct_conv(self.cat)
        now = time.time()

        def pic(i, **extra):
            return dict({"id": 900000 + i, "conv": conv, "sender_id": self.cat, "kind": "file", "body": "",
                         "ts": now + i, "file": {"id": f"f{i}", "name": f"FAL_030_v{i}.jpg", "size": 1000}}, **extra)
        c = store.conversation(conv)
        for i in range(3):
            c.add(pic(i))
        c.add(pic(3, body="this one has a caption"))
        c.add(pic(4))
        c.complete = c.history_requested = True
        main.open_conv(conv)
        settle(self.app, 0.3)
        galleries = [r for r in main.chat.rows if isinstance(r, GalleryRow)]
        self.assertEqual(len(galleries), 1)
        self.assertEqual([m["id"] for m in galleries[0].msgs], [900000, 900001, 900002])
        self.assertEqual(sum(1 for r in main.chat.rows if getattr(r, "msg", {}).get("id") in (900003, 900004)), 2)
        # the viewer walks through every picture of the chat
        from client.ui.gallery import ImageViewer
        main.chat.open_viewer(galleries[0].msgs[1])
        viewer = next(w for w in self.app.topLevelWidgets() if isinstance(w, ImageViewer) and w.isVisible())
        self.assertEqual((viewer.i, len(viewer.msgs)), (1, 5))
        viewer.go(viewer.i + 1)
        self.assertEqual(viewer.i, 2)
        viewer.close()
        for i in range(5):
            c.messages.pop(900000 + i, None)
        c.last = None

    # ------------------------------------------------------------ shot names and paths
    def test_shot_names_become_links(self):
        from client.ui.widgets import linkify, set_shot_pattern, shot_names, studio_paths
        self.assertEqual(self.main.store.shot_pattern, P.SHOT_PATTERN_DEFAULT)
        set_shot_pattern(P.SHOT_PATTERN_DEFAULT)
        self.assertEqual(shot_names("FAL_030 and FAL_030_0010 and SEQ010_SH0020, not v12 or MAX_FILE"),
                         ["FAL_030", "FAL_030_0010", "SEQ010_SH0020"])
        html = linkify("FAL_030 comp is up")
        self.assertIn('href="quillo-shot:FAL_030"', html)
        path = linkify(r"see \\nas01\show\FAL_030\comp")
        self.assertNotIn("quillo-shot:", path, "a shot inside a path stays part of the path link")
        self.assertEqual(studio_paths(r'go to \\nas01\show\FAL_030\comp and "Z:\my shots\v2", or https://x.y'),
                         [r"\\nas01\show\FAL_030\comp", r"Z:\my shots\v2"])
        opened = []
        import client.ui.widgets as w
        w.set_shot_handler(opened.append)
        w.open_link("quillo-shot:FAL_030")
        self.assertEqual(opened, ["FAL_030"])
        w.set_shot_handler(self.main.show_shot)

    def test_a_bad_pattern_switches_shot_links_off(self):
        self.assertIsNone(P.shot_regex("FAL_[0-9"))
        self.assertIsNone(P.shot_regex(".*"), "a pattern that matches nothing-at-all would link every gap")
        old = self.core.config["shot_code_pattern"]
        self.core.config.update(shot_code_pattern="(")
        try:
            self.assertEqual(self.core.shot_pattern(), "")
        finally:
            self.core.config.update(shot_code_pattern=old)

    def test_a_path_becomes_a_card(self):
        from client.ui.chat_view import MessageRow, PathCard
        msg = {"id": 1, "conv": P.direct_conv(self.ben), "sender_id": self.ben, "kind": "text", "ts": time.time(),
               "body": r"\\nas01\show\FAL_030\comp\v012"}
        row = MessageRow(self.main, msg, False, True, True, False)
        cards = row.findChildren(PathCard)
        self.assertEqual(len(cards), 1)
        self.assertIsNone(row.text, "just a path: the card alone")

    # ------------------------------------------------------------ notifications
    def test_answer_from_the_pop_up(self):
        main = self.main
        main.config["notifications"] = main.config["quick_reply"] = True
        conv = P.direct_conv(self.ben)
        msg = {"id": 5, "conv": conv, "sender_id": self.ben, "kind": "text", "body": "Dailies at 4?",
               "ts": time.time()}
        main.notify("Ben Das", "Dailies at 4?", conv, msg)
        self.assertEqual(len(main.popup_stack.popups), 1)
        pop = main.popup_stack.popups[0]
        pop.input.setText("Yes, see you there")
        pop._send()
        from server.db import direct_key
        self.assertTrue(wait_until(self.app, lambda: "Yes, see you there" in [
            m["body"] for m in self.core.call(self.core.db.history, direct_key(self.ann, self.ben), None, 50)]))
        self.assertTrue(wait_until(self.app, lambda: not main.popup_stack.popups))

    def test_focus_time_lets_only_some_through(self):
        main, store = self.main, self.main.store
        shown = []
        real = main.notify
        main.notify = lambda title, text, target, msg=None: shown.append(msg["sender_id"])
        try:
            main.start_focus(time.time() + 600)
            f = dict(store.prefs.get("focus") or {})
            f["people"] = [self.cat]
            store.set_pref("focus", f)
            self.assertTrue(store.focus_until())
            self.assertFalse(main.focus_bar.isHidden())

            def arrive(sender, body="hi"):
                m = {"id": int(time.time() * 1000) % 10 ** 9, "conv": P.direct_conv(sender), "sender_id": sender,
                     "kind": "text", "body": body, "ts": time.time()}
                main._on_message(m, True)
            arrive(self.ben)                           # waits
            arrive(self.cat)                           # chosen
            arrive(self.lead)                          # my lead
            self.assertEqual(shown, [self.cat, self.lead])
            self.assertEqual(main.focus_missed, 1)
            self.assertTrue(wait_until(self.app, lambda: (self.core.call(self.core.db.prefs, self.ann)
                                                          .get("focus") or {}).get("people") == [self.cat]))
            main.end_focus()
            self.assertFalse(store.focus_until())
            self.assertTrue(main.focus_bar.isHidden())
            arrive(self.ben)
            self.assertEqual(shown[-1], self.ben)
        finally:
            main.notify = real

    # ------------------------------------------------------------ reading comfort
    def test_ctrl_plus_makes_message_text_bigger(self):
        from client.ui import chat_view
        chat = self.main.chat
        chat.zoom(0)
        chat.zoom(1)
        self.assertEqual(chat_view.ZOOM["pct"], 110)
        self.assertEqual(self.main.config["chat_zoom"], 110)
        self.assertEqual(chat_view.text_pt(), "11.6pt")
        chat.zoom(0)
        self.assertEqual(chat_view.ZOOM["pct"], 100)

    def test_the_chat_list_edge_can_be_dragged(self):
        main = self.main
        main._set_sidebar_width(900)
        self.assertEqual(main.sidebar.width(), main.sidebar.MAX_WIDTH)
        self.assertEqual(main.config["sidebar_width"], main.sidebar.MAX_WIDTH)
        main._set_sidebar_width(10)
        self.assertEqual(main.sidebar.width(), main.sidebar.MIN_WIDTH)
        main._set_sidebar_width(330)

    def test_home_leaves_out_empty_cards(self):
        home = self.main.home
        self.main.go_home()
        settle(self.app, 0.4)
        titles = [w.text() for w in home.findChildren(QLabel)]
        self.assertNotIn("ANNOUNCEMENTS", titles, "no announcements: no empty box")
        self.assertIn("CALENDAR", titles)

    def test_shortcuts_sheet_lists_the_new_keys(self):
        from client.ui.help import SHORTCUTS, ShortcutsDialog
        keys = [k for _h, items in SHORTCUTS for k, _w in items]
        for k in ("Ctrl+K", "Ctrl+F", "Ctrl +", "Ctrl+/  or  F1"):
            self.assertIn(k, keys)
        dlg = ShortcutsDialog(self.main)
        dlg.close()


if __name__ == "__main__":
    unittest.main()
