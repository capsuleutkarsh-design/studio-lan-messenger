"""1.12.0 UI refinement - the chat: one-line labels that end in '…', menus that open where they should, the
thread panel, the composer, the empty chat and more - against a real server."""

import os
import shutil
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QTextCursor  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from common import protocol as P  # noqa: E402
from common import theme as T  # noqa: E402
from server.core import ServerCore  # noqa: E402
from tests import test_server as base  # noqa: E402
from tests.test_client_v19 import settle, wait_until  # noqa: E402

PORT = 17150


class Other(base.Client):
    """Someone else, on a plain connection to the same server."""

    def __init__(self, username, password="Artist2026"):
        base.PORT, old = PORT, base.PORT
        try:
            super().__init__(username, password)
        finally:
            base.PORT = old


class Transfer:
    """Just enough of a transfer for the status line."""

    def __init__(self, done, size, speed):
        self.done, self.size, self.speed = done, size, speed


class UiV112ChatTest(unittest.TestCase):
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
        cls.ann = mk("ann", "Ann Rao")
        cls.ben, cls.cat = mk("ben", "Ben Das"), mk("cat", "Cat Iyer")
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
        cls.ben_c = Other("ben")
        cls.room = cls.ben_c.request("create_room", name="Comp & Roto", members=[cls.ann, cls.cat])["room_id"]
        cls.room_conv = P.room_conv(cls.room)
        assert wait_until(cls.app, lambda: cls.room in cls.main.store.rooms), "the room did not arrive"

    @classmethod
    def tearDownClass(cls):
        cls.ben_c.close()
        cls.main.popup_stack.close_all()
        cls.main.quitting = True
        cls.core.stop()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def msg(self, body="Hello", sender=None, conv=None, **kw):
        m = {"id": 900000 + int(time.time() * 1000) % 99999, "conv": conv or P.direct_conv(self.ben),
             "sender_id": self.ben if sender is None else sender, "kind": "text", "ts": time.time(), "body": body}
        m.update(kw)
        return m

    def open(self, conv):
        self.main.open_conv(conv)
        settle(self.app, 0.3)
        return self.main.chat

    # ------------------------------------------------------------ small helpers
    def test_transfer_status_says_percent_and_time_left(self):
        from client.ui.chat_view import transfer_status
        text = transfer_status(Transfer(211 * 2 ** 20, 3 * 2 ** 30, 221 * 2 ** 20))
        self.assertIn(" of ", text)
        self.assertIn("6%", text)
        self.assertIn("left", text)
        self.assertNotIn("  ", text, "one ' · ' between the parts")

    def test_times_inside_sentences(self):
        from client.ui.chat_view import pick_time_text, thread_when, when_inline
        now = time.time()
        self.assertTrue(when_inline(now + 86400).startswith("tomorrow at "))
        self.assertTrue(thread_when(now - 86400).startswith("yesterday, "))
        self.assertEqual(pick_time_text(), "Pick a date && time…", "a real '&', not a shortcut marker")

    # ------------------------------------------------------------ composer
    def test_send_button_follows_the_box_after_switching_chats(self):
        chat = self.open(P.direct_conv(self.ben))
        chat.input.setPlainText("half-typed")
        self.assertIn(T.ACCENT, chat.b_send.styleSheet())
        chat = self.open(P.direct_conv(self.cat))             # an empty box
        self.assertEqual(chat.input.toPlainText(), "")
        self.assertNotIn(f"background: {T.ACCENT};", chat.b_send.styleSheet(), "nothing to send: not ready")
        chat = self.open(P.direct_conv(self.ben))             # the draft comes back, and so does 'ready'
        self.assertEqual(chat.input.toPlainText(), "half-typed")
        self.assertIn(f"background: {T.ACCENT};", chat.b_send.styleSheet())
        chat.input.clear()

    def test_mention_popup_sits_above_the_reply_bar_and_fits_its_rows(self):
        chat = self.open(self.room_conv)
        chat.start_reply(self.msg("Edges look soft", conv=self.room_conv))
        settle(self.app, 0.1)
        chat.input.setPlainText("@be")
        chat.input.moveCursor(QTextCursor.End)
        chat._update_mention_popup()
        pop = chat.mention_popup
        self.assertTrue(wait_until(self.app, pop.isVisible, 2))
        bar_top = chat.action_bar.mapToGlobal(chat.action_bar.rect().topLeft()).y()
        self.assertLessEqual(pop.geometry().bottom(), bar_top, "'Replying to …' stays visible")
        rows = sum(pop.sizeHintForRow(i) for i in range(pop.count()))
        self.assertGreaterEqual(pop.height(), rows, "the last row is not cut off")
        self.assertIn("Ben Das · @ben", [pop.item(i).text() for i in range(pop.count())])
        pop.hide()
        chat.cancel_action()
        chat.input.clear()

    def test_zoom_reaches_the_composer(self):
        chat = self.open(P.direct_conv(self.ben))
        chat.zoom(0)
        before = chat.input.font().pointSizeF()
        chat.zoom(1)
        settle(self.app, 0.1)
        self.assertGreater(chat.input.font().pointSizeF(), before)
        chat.zoom(0)

    def test_offline_note_says_sign_in(self):
        chat = self.open(P.direct_conv(self.cat))
        if chat.offline_note.isVisible() or chat.offline_note.text():
            self.assertIn("sign in", chat.offline_note.text())
            self.assertNotIn("log in", chat.offline_note.text())

    def test_scheduled_bar_reads_as_a_sentence(self):
        chat = self.open(P.direct_conv(self.ben))
        store = self.main.store
        old = store.scheduled
        store.scheduled = [{"id": 1, "conv": chat.conv, "state": "pending", "due_at": time.time() + 86400,
                            "text": "Renders are up & running", "sticker": ""}]
        try:
            chat._update_scheduled_bar()
            text = chat.sched_bar.text()
            self.assertIn("scheduled for tomorrow at ", text)
            self.assertTrue(text.endswith("· View"), text)
        finally:
            store.scheduled = old
            chat._update_scheduled_bar()

    def test_screenshot_button_uses_the_screenshot_icon_and_hides_in_compact(self):
        chat = self.main.chat
        self.assertEqual(chat.b_shot.icon_name, "screenshot")
        chat.set_compact(True)
        try:
            self.assertTrue(chat.b_shot.isHidden())
        finally:
            chat.set_compact(False)
        self.assertFalse(chat.b_shot.isHidden())

    # ------------------------------------------------------------ header, pins, empty chat
    def test_header_title_is_cut_with_an_ellipsis(self):
        from client.ui.widgets import ElidedLabel
        chat = self.open(self.room_conv)
        self.assertIsInstance(chat.title, ElidedLabel)
        self.assertIsInstance(chat.subtitle, ElidedLabel)
        chat.title.resize(40, chat.title.height())
        self.assertTrue(chat.title.is_elided())
        self.assertTrue(chat.title.shown_text().endswith("…"))
        self.assertEqual(chat.title.text(), "Comp & Roto", "text() keeps the whole name")

    def test_typing_shows_in_the_subtitle(self):
        chat = self.open(self.room_conv)
        chat._on_typing_event(chat.conv, self.ben)
        self.assertEqual(chat.subtitle.text(), "Ben is typing…")
        self.assertIn(T.ACCENT, chat.subtitle.styleSheet())
        chat.typing_users.clear()
        chat._show_subtitle()
        self.assertIn("members", chat.subtitle.text())

    def test_empty_chat_shows_a_centred_note(self):
        chat = self.open(P.direct_conv(self.cat))
        c = self.main.store.conversation(chat.conv)
        self.assertTrue(wait_until(self.app, lambda: c.complete, 5))
        chat.render_all()
        if not c.messages:
            self.assertTrue(chat.empty.isVisible())
            self.assertEqual(chat.empty.title.text(), "No messages yet")
            self.assertIn("Say hi to Cat", chat.empty.text.text())
            self.assertFalse(chat.loading.isVisible())

    # ------------------------------------------------------------ messages
    def test_path_card_cuts_the_middle_of_the_location(self):
        from client.ui.chat_view import MessageRow, PathCard
        from client.ui.widgets import ElidedLabel
        path = r"\\nas01\ak74\shots\AK74_0450\comp\renders\v013"
        row = MessageRow(self.main, self.msg(path), False, True, True, False)
        card = row.findChildren(PathCard)[0]
        self.assertIsInstance(card.where, ElidedLabel)
        card.where.resize(80, 20)
        card.where._refresh()                      # hidden widgets get no resize event
        shown = card.where.shown_text()
        self.assertIn("…", shown)
        self.assertTrue(shown.startswith("\\\\"), "the share root stays")
        self.assertEqual(card.where.text(), r"\\nas01\ak74\shots\AK74_0450\comp\renders")
        row.set_max_width(220)
        self.assertLessEqual(card.minimumWidth(), 220 - 24, "a narrow bubble narrows the card")
        row.set_max_width(600)
        self.assertEqual(card.minimumWidth(), PathCard.MIN_WIDTH)

    def test_file_card_fits_a_narrow_bubble(self):
        from client.ui.chat_view import FileCard, MessageRow
        info = {"id": "f4242", "name": "AK74_0450_v013_wip.png", "size": 15000}
        row = MessageRow(self.main, self.msg("", kind="file", file=info), False, True, True, False)
        card = row.findChildren(FileCard)[0]
        row.set_max_width(230)
        self.assertLessEqual(card.minimumWidth(), 230 - 24)
        self.assertNotIn("  ·  ", card.meta.text())

    def test_reply_quote_says_you_and_keeps_the_whole_line(self):
        from client.ui.chat_view import MessageRow, ReplyQuote
        long = "Hi team, client notes for Reel 3 are in\n• AK74_0450 — hair edges look crunchy " * 4
        reply = {"id": 1, "sender_id": self.ann, "sender_name": "Ann Rao", "snippet": long}
        row = MessageRow(self.main, self.msg("Starting on it", reply=reply), False, True, True, False)
        q = row.findChildren(ReplyQuote)[0]
        self.assertEqual(q.who.text(), "You")
        self.assertNotIn("\n", q.snippet.text())
        self.assertNotIn("  ", q.snippet.text())
        q.snippet.resize(120, 20)
        q.snippet._refresh()
        self.assertTrue(q.snippet.shown_text().endswith("…"))

    def test_no_read_ticks_in_my_space(self):
        from client.ui.chat_view import MessageRow
        mine = P.direct_conv(self.ann)
        row = MessageRow(self.main, self.msg("note to self", sender=self.ann, conv=mine, read=True),
                         True, True, True, False)
        self.assertNotIn("<img", row.meta.text())
        other = MessageRow(self.main, self.msg("hi", sender=self.ann, read=True), True, True, True, False)
        self.assertTrue("<img" in other.meta.text() or "✓" in other.meta.text())

    def test_mentions_do_not_look_like_my_own_bubbles(self):
        from client.ui.chat_view import MessageRow
        row = MessageRow(self.main, self.msg("@ann please take this", conv=self.room_conv), False, True, True, True)
        row.mention = True
        style = row._bubble_style()
        self.assertIn(T.mix(T.ACCENT, T.BUBBLE_OTHER, 0.07), style)
        self.assertNotIn(T.BUBBLE_ME, style)

    def test_thread_counter_reads_last_reply(self):
        from PySide6.QtWidgets import QPushButton
        from client.ui.chat_view import MessageRow
        m = self.msg("root", thread_count=3, thread_last=time.time() - 86400)
        row = MessageRow(self.main, m, False, True, True, False)
        texts = [b.text() for b in row.findChildren(QPushButton)]
        self.assertTrue(any("3 replies · last reply yesterday, " in t for t in texts), texts)

    def test_snippet_alone_in_a_bubble_has_no_card_of_its_own(self):
        from client.ui.chat_view import MessageRow, SnippetCard
        body = "\n".join(f"line {i}" for i in range(40))
        row = MessageRow(self.main, self.msg(body), False, True, True, False)
        card = row.findChildren(SnippetCard)[0]
        self.assertIn("transparent", card.styleSheet())
        reply = {"id": 1, "sender_id": self.ben, "sender_name": "Ben Das", "snippet": "x"}
        row2 = MessageRow(self.main, self.msg(body, reply=reply), False, True, True, False)
        self.assertNotIn("transparent", row2.findChildren(SnippetCard)[0].styleSheet())
        icons = [b.icon_name for b in card.findChildren(type(self.main.chat.b_search))]
        self.assertIn("copy", icons)

    def test_sticker_name_lines_up_with_other_names(self):
        from client.ui.chat_view import MessageRow
        from client.ui.widgets import ElidedLabel
        row = MessageRow(self.main, self.msg("pack/x.webp", kind="sticker", conv=self.room_conv),
                         False, True, True, True)
        names = [w for w in row.findChildren(ElidedLabel) if w.text() == "Ben Das"]
        self.assertTrue(names and "padding-left: 12px" in names[0].styleSheet())

    def test_message_menu_is_grouped_and_worded(self):
        from client.ui.chat_view import MessageRow
        m = self.msg("AK74_0450 is ready", sender=self.ann, conv=self.room_conv)
        row = MessageRow(self.main, m, True, True, True, True)
        menu = row.build_menu()
        acts = menu.actions()
        texts = [a.text() for a in acts]
        self.assertIn("React…", texts)
        self.assertIn("Pin message", texts)
        self.assertNotIn("Pin to the top", texts)
        self.assertGreaterEqual(sum(a.isSeparator() for a in acts), 3, "answer · keep · copy · delete")
        self.assertTrue(texts[-1].startswith("Delete for everyone"))
        self.assertFalse(any("..." in t for t in texts))

    def test_admin_delete_wording(self):
        from client.ui.chat_view import MessageRow
        store = self.main.store
        was = store.me.get("is_admin")
        store.me["is_admin"] = True
        try:
            row = MessageRow(self.main, self.msg("spam", conv=self.room_conv), False, True, True, True)
            texts = [a.text() for a in row.build_menu().actions()]
            self.assertIn("Delete for everyone (as admin)", texts)
        finally:
            store.me["is_admin"] = was

    def test_delete_asks_with_cancel_as_the_default(self):
        seen = {}

        def fake_exec(box, *a):
            seen["default"] = box.defaultButton().text()
            seen["buttons"] = [b.text() for b in box.buttons()]
            return 0
        old = QMessageBox.exec
        QMessageBox.exec = fake_exec
        try:
            self.main.chat.delete_message(self.msg("x", sender=self.ann))
        finally:
            QMessageBox.exec = old
        self.assertEqual(seen["default"], "Cancel", "a stray Enter must not delete")
        self.assertIn("Delete for everyone", seen["buttons"])

    def test_seen_by_uses_the_read_receipts_list(self):
        from client.ui import dialogs
        shown = []
        old = dialogs.ReadReceiptsDialog.exec
        dialogs.ReadReceiptsDialog.exec = lambda dlg: shown.append(dlg) or 0
        chat = self.open(self.room_conv)
        replies = []
        chat.ctx.conn.request = lambda op, cb, **kw: replies.append(cb)
        try:
            chat.show_seen_by(self.msg("hello", sender=self.ann, conv=self.room_conv))
            replies[0]({"ok": True, "read": [self.ben], "total": 2,
                        "names": {str(self.ben): "Ben Das", str(self.cat): "Cat Iyer"}})
        finally:
            del chat.ctx.conn.request
            dialogs.ReadReceiptsDialog.exec = old
        self.assertEqual(len(shown), 1)
        self.assertEqual(shown[0].windowTitle(), "Seen by")

    # ------------------------------------------------------------ uploads
    def test_upload_row_style_is_scoped(self):
        from PySide6.QtWidgets import QFrame
        chat = self.open(P.direct_conv(self.ben))
        strip = chat.uploads

        class T_:
            kind, conv, name, active, state, error = "upload", chat.conv, "comp_4k.exr", True, "sending", ""
            done, size, speed = 10, 100, 5

            def cancel(self):
                pass
        t = T_()
        strip._changed(t)
        try:
            row = strip.findChildren(QFrame, "uploadRow")[0]
            self.assertTrue(row.styleSheet().startswith("#uploadRow"), "the bar keeps its own track")
            self.assertIn("comp_4k.exr</b> · ", row.label.text())
            self.assertIn("10%", row.label.text())
        finally:
            strip._drop(t)

    # ------------------------------------------------------------ bubbles in a narrow chat
    def test_narrow_chat_gives_bubbles_a_bigger_share(self):
        chat = self.open(P.direct_conv(self.ben))
        chat.scroll.viewport().resize(360, 400)
        w = chat._bubble_width()
        self.assertGreater(w, int((360 - 40) * 0.72))
        m = chat.mlay.contentsMargins()
        self.assertLessEqual(w, 360 - m.left() - m.right())

    def test_long_system_line_wraps_inside_a_narrow_chat(self):
        from client.ui.chat_view import SystemLine
        chat = self.open(self.room_conv)
        line = SystemLine({"id": 1, "ts": time.time(), "kind": "system",
                           "body": 'Farhan Qureshi scheduled "AK74 dailies" · Thu 1 Oct, 14:30 in Screening Room 2'})
        chat.scroll.viewport().resize(330, 400)
        room = chat._line_width()
        chat._set_width(line)
        self.assertLessEqual(line.lbl.minimumWidth(), room, "the list never gets wider than the chat")

    def test_new_messages_line_skips_old_system_notes(self):
        chat = self.main.chat
        saved = chat.unread_count, chat.first_unread_id, chat.divider_count, chat._anchor

        class Conv:
            complete = True
        msgs = [self.msg("Administrator created the room", sender=0, kind="system", id=1),
                self.msg("Timesheets are due", id=2), self.msg("Room 2 is booked", id=3)]
        try:
            chat.unread_count, chat.first_unread_id, chat.divider_count = 3, None, 0
            chat._find_first_unread(Conv(), msgs)
            self.assertEqual(chat.first_unread_id, 2, "above the first unread message, not the old note")
            self.assertEqual(chat.divider_count, 2)
        finally:
            chat.unread_count, chat.first_unread_id, chat.divider_count, chat._anchor = saved

    def test_message_menu_opens_upward_near_the_bottom(self):
        from PySide6.QtCore import QPoint
        from client.ui.chat_view import MessageRow, menu_point
        row = MessageRow(self.main, self.msg("hello", sender=self.ann, conv=self.room_conv), True, True, True, True)
        menu = row.build_menu()
        win = self.main.frameGeometry()
        at = QPoint(win.left() + 300, win.bottom() - 20)
        pos = menu_point(menu, at, self.main)
        self.assertLessEqual(pos.y() + menu.sizeHint().height(), win.bottom(), "Delete stays inside the window")

    # ------------------------------------------------------------ thread panel
    def test_thread_panel(self):
        main = self.main
        panel = main.thread_panel
        self.assertTrue(panel.MIN_WIDTH <= panel.minimumWidth() <= panel.WIDTH)
        self.assertEqual(panel.maximumWidth(), panel.WIDTH)
        conv = self.room_conv
        chat = self.open(conv)
        chat.input.setPlainText("Thread root for FAL_030")
        chat.send_text()
        store = main.store
        self.assertTrue(wait_until(self.app, lambda: any(m["body"] == "Thread root for FAL_030"
                                                         for m in store.conversation(conv).messages.values())))
        root = next(m for m in store.conversation(conv).messages.values() if m["body"] == "Thread root for FAL_030")
        main.open_thread(conv, root["id"])
        self.assertTrue(wait_until(self.app, lambda: root["id"] in panel.rows))
        self.assertEqual(panel.count_label.text(), "No replies yet")
        self.assertFalse(panel.b_send.isEnabled(), "nothing typed: Send is off")
        panel.input.setPlainText("Edge looks soft")
        self.assertTrue(panel.b_send.isEnabled())
        panel.send()
        self.assertTrue(wait_until(self.app, lambda: len(panel.rows) == 2))
        self.assertEqual(panel.count_label.text(), "1 reply")
        settle(self.app, 0.2)
        vw = panel.scroll.viewport().width()
        for row in panel.rows.values():
            self.assertLessEqual(row.bubble.maximumWidth(), vw - 2 * panel.SIDE - 44, "a margin on the right")
        # @ suggestions in a room's thread
        panel.input.setPlainText("@ca")
        panel.input.moveCursor(QTextCursor.End)
        panel._update_mention_popup()
        self.assertTrue(wait_until(self.app, panel.mention_popup.isVisible, 2))
        panel.mention_popup.hide()
        panel.input.clear()
        panel.close_thread()


if __name__ == "__main__":
    unittest.main()
