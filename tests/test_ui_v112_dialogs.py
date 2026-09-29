"""1.12.0 dialogs: sign-in, the tour, settings, profile, password, rooms, forward, poll, saved, search, read
receipts, the picture viewer, drawing on a picture and screenshots - against a real server."""

import os
import shutil
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QColor, QImage, QPixmap  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QDialogButtonBox, QMessageBox, QScrollArea  # noqa: E402

from common import protocol as P  # noqa: E402
from common import theme as T  # noqa: E402
from server.core import ServerCore  # noqa: E402
from tests import test_server as base  # noqa: E402
from tests.test_client_v19 import settle, wait_until  # noqa: E402

PORT = 17190


class Other(base.Client):
    """Someone else, on a plain connection to the same server."""

    def __init__(self, username, password="Artist2026"):
        base.PORT, old = PORT, base.PORT
        try:
            super().__init__(username, password)
        finally:
            base.PORT = old


class DialogsV112Test(unittest.TestCase):
    answer = QMessageBox.Yes             # what the patched QMessageBox.question says

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        os.environ["APPDATA"] = os.path.join(cls.tmp, "appdata")
        os.environ["LOCALAPPDATA"] = os.path.join(cls.tmp, "local")
        cls.app = QApplication.instance() or QApplication(sys.argv)
        cls.app.setQuitOnLastWindowClosed(False)
        QMessageBox.exec = lambda self, *a: QMessageBox.Ok
        for name in ("information", "warning", "critical"):
            setattr(QMessageBox, name, staticmethod(lambda *a, **k: QMessageBox.Ok))
        QMessageBox.question = staticmethod(lambda *a, **k: DialogsV112Test.answer)
        cls.core = ServerCore(os.path.join(cls.tmp, "server"))
        cls.core.config.update(tcp_port=PORT, discovery_port=PORT + 1)
        cls.core.start()
        mk = lambda u, n, **kw: cls.core.call(cls.core.admin_create_user, must_change=False,  # noqa: E731
                                              username=u, password="Artist2026", display_name=n, **kw)
        cls.ann = mk("ann", "Ann Rao")
        cls.ben, cls.cat, cls.dan = mk("ben", "Ben Das"), mk("cat", "Cat Iyer"), mk("dan", "Dan Roy")
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
            cls.main._tour.accept()
        settle(cls.app, 0.2)

    @classmethod
    def tearDownClass(cls):
        cls.main.popup_stack.close_all()
        cls.main.quitting = True
        cls.core.stop()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def setUp(self):
        DialogsV112Test.answer = QMessageBox.Yes

    def room(self, name="R&D Comp", owner=None):
        """A room with ben and cat in it; ann owns it unless owner says otherwise."""
        ben = Other("ben") if owner == "ben" else None
        try:
            if ben:
                rid = ben.request("create_room", name=name, members=[self.ann, self.cat])["room_id"]
            else:
                replies = []
                self.main.conn.request("create_room", replies.append, name=name, topic="",
                                       members=[self.ben, self.cat])
                self.assertTrue(wait_until(self.app, lambda: bool(replies)))
                rid = replies[0]["room_id"]
        finally:
            if ben:
                ben.close()
        self.assertTrue(wait_until(self.app, lambda: rid in self.main.store.rooms))
        return self.main.store.rooms[rid]

    # ------------------------------------------------------------ '&' in labels
    def test_ampersands_show_as_ampersands(self):
        from client.ui.dialogs import RoomInfoDialog
        from client.ui.help import WelcomeTour
        dlg = RoomInfoDialog(self.main, self.room())
        self.assertEqual(dlg.b_save.text(), "Save name && topic", "Qt shows && as one '&', not an underscore")
        self.assertFalse(dlg.b_save.isEnabled(), "nothing to save until something changes")
        dlg.topic.setText("Comp for AK74")
        self.assertTrue(dlg.b_save.isEnabled())
        dlg.close()
        tour = WelcomeTour(self.main)
        from PySide6.QtWidgets import QPushButton
        labels = [b.text() for b in tour.findChildren(QPushButton)]
        self.assertIn("Set my photo && status", labels)
        tour.close()

    # ------------------------------------------------------------ member picker
    def test_member_picker_box_and_name_both_tick(self):
        from client.ui.dialogs import NewRoomDialog
        dlg = NewRoomDialog(self.main, self.main.store)
        dlg.resize(460, 520)
        dlg.show()
        settle(self.app, 0.3)
        lst = dlg.picker.list
        it = lst.item(0)
        rect = lst.visualItemRect(it)
        self.assertEqual(it.checkState(), Qt.Unchecked)
        QTest.mouseClick(lst.viewport(), Qt.LeftButton, Qt.NoModifier, QPoint(rect.left() + 16, rect.center().y()))
        settle(self.app, 0.1)
        self.assertEqual(it.checkState(), Qt.Checked, "a click on the square ticks it (it used to do nothing)")
        QTest.mouseClick(lst.viewport(), Qt.LeftButton, Qt.NoModifier, QPoint(rect.left() + 120, rect.center().y()))
        settle(self.app, 0.1)
        self.assertEqual(it.checkState(), Qt.Unchecked, "a click on the name toggles it too")
        QTest.mouseClick(lst.viewport(), Qt.LeftButton, Qt.NoModifier, QPoint(rect.left() + 120, rect.center().y()))
        settle(self.app, 0.1)
        self.assertEqual(dlg.picker.count(), 1)
        self.assertIn("1 SELECTED", dlg.people.text(), "the heading counts the ticks")
        self.assertNotIn("  ", it.text(), "one ' · ' separator, no double spaces")
        dlg.close()

    # ------------------------------------------------------------ rooms
    def test_removing_members_asks_first(self):
        from client.ui.dialogs import RoomInfoDialog
        room = self.room("Roto")
        sent = []
        dlg = RoomInfoDialog(self.main, room)
        dlg._update = lambda **kw: sent.append(kw)
        self.assertFalse(dlg.b_remove.isEnabled(), "nothing selected: nothing to remove")
        row = next(i for i in range(dlg.list.count()) if dlg.list.item(i).data(Qt.UserRole) == self.ben)
        dlg.list.item(row).setSelected(True)
        self.assertTrue(dlg.b_remove.isEnabled())
        DialogsV112Test.answer = QMessageBox.No
        dlg.remove()
        self.assertEqual(sent, [], "said No: nobody is removed")
        DialogsV112Test.answer = QMessageBox.Yes
        dlg.remove()
        self.assertEqual(sent, [{"remove": [self.ben]}])
        texts = [dlg.list.item(i).text() for i in range(dlg.list.count())]
        self.assertTrue(any("Offline" in t or "Online" in t for t in texts), "status in words, not only colour")

    def test_read_only_room_fields_stay_readable(self):
        from client.ui.dialogs import RoomInfoDialog
        dlg = RoomInfoDialog(self.main, self.room("Lighting", owner="ben"))
        self.assertTrue(dlg.name.isEnabled(), "not greyed out (FAINT text) - read-only instead")
        self.assertTrue(dlg.name.isReadOnly())
        self.assertFalse(hasattr(dlg, "b_remove"))
        dlg.close()

    # ------------------------------------------------------------ settings
    def test_settings_fit_a_laptop_screen(self):
        from client.ui.dialogs import SettingsDialog
        cfg = self.main.config
        old = cfg["auto_away_minutes"]
        cfg["auto_away_minutes"] = 45                     # not one of the choices: still offered
        try:
            dlg = SettingsDialog(self.main)
            self.assertIsInstance(dlg.scroll, QScrollArea)
            avail = (dlg.screen() or self.app.primaryScreen()).availableGeometry().height()
            self.assertLessEqual(dlg.height(), int(avail * 0.85) + 1)
            self.assertEqual(dlg.away.currentData(), 45)
            dlg.away.setCurrentIndex(dlg.away.findData(15))
            dlg.accept()
            self.assertEqual(cfg["auto_away_minutes"], 15)
            self.assertEqual(dlg.away.itemText(0), "Never")
        finally:
            cfg["auto_away_minutes"] = old
            cfg.save()

    # ------------------------------------------------------------ password
    def test_password_is_checked_as_you_type(self):
        from client.ui.dialogs import ChangePasswordDialog
        dlg = ChangePasswordDialog(self.main)
        ok = dlg.bb.button(QDialogButtonBox.Ok)
        self.assertFalse(ok.isEnabled(), "nothing typed yet")
        self.assertEqual(ok.text(), "Change password")
        dlg.old.setText("Artist2026")
        dlg.new.setText("NewPass2026")
        dlg.new2.setText("NewPa")
        self.assertTrue(dlg.error.isHidden(), "no complaint while the second one is still being typed")
        self.assertFalse(ok.isEnabled())
        dlg.new2.setText("NewPaX")
        self.assertFalse(dlg.error.isHidden())
        self.assertIn("don't match", dlg.error.text())
        dlg.new2.setText("NewPass2026")
        self.assertTrue(dlg.error.isHidden())
        self.assertTrue(ok.isEnabled())
        dlg.old.setText("wrong-one")
        dlg.accept()
        self.assertTrue(wait_until(self.app, lambda: not dlg.error.isHidden()), "the server's answer shows inline")
        self.assertTrue(ok.isEnabled(), "and they can try again")
        dlg.close()

    # ------------------------------------------------------------ forward
    def test_forward_needs_a_visible_choice(self):
        from client.ui.dialogs import ForwardDialog
        msg = {"id": 1, "conv": P.direct_conv(self.dan), "kind": "text", "body": "x" * 400, "sender_id": self.dan}
        dlg = ForwardDialog(self.main, msg)
        ok = dlg.bb.button(QDialogButtonBox.Ok)
        self.assertFalse(ok.isEnabled(), "nothing chosen yet")
        self.assertIsNone(dlg.target())
        dlg.search.setText("cat")
        self.assertTrue(ok.isEnabled(), "typing a name picks the top match")
        self.assertEqual(dlg.target(), P.direct_conv(self.cat))
        self.assertIn("Cat Iyer", ok.text())
        dlg.search.setText("nobody-called-this")
        self.assertIsNone(dlg.target(), "a chat hidden by the search is never the target")
        self.assertFalse(ok.isEnabled())
        dlg.close()

    # ------------------------------------------------------------ poll
    def test_poll_answers_can_be_removed_and_it_says_what_is_missing(self):
        from client.ui.dialogs import PollDialog
        dlg = PollDialog(self.main)
        self.assertFalse(dlg.missing.isHidden())
        self.assertIn("question", dlg.missing.text())
        self.assertEqual(len(dlg.answers), 3)
        dlg.remove_answer(dlg.answer_rows[2][0])
        self.assertEqual(len(dlg.answers), 2)
        dlg.remove_answer(dlg.answer_rows[1][0])
        self.assertEqual(len(dlg.answers), 2, "a poll keeps two answers")
        dlg.question.setText("Lunch?")
        dlg.answers[0].setText("Pizza")
        dlg.answers[1].setText("Dosa")
        self.assertTrue(dlg.missing.isHidden())
        self.assertTrue(dlg.bb.button(QDialogButtonBox.Ok).isEnabled())
        self.assertEqual(dlg.values()["options"], ["Pizza", "Dosa"])
        dlg.close()

    # ------------------------------------------------------------ profile
    def test_the_emoji_shown_is_the_one_saved(self):
        from client.ui.dialogs import STATUS_PRESETS, ProfileDialog
        dlg = ProfileDialog(self.main)
        dlg.clear_status()
        self.assertEqual(dlg.emoji.text(), "", "no emoji picked: a muted placeholder, not a smiley that isn't saved")
        emo, text, after = STATUS_PRESETS[1]
        dlg.use_preset(emo, text, after)
        checked = [t for b, _e, t in dlg.preset_buttons if b.isChecked()]
        self.assertEqual(checked, [text], "the chosen preset shows as chosen")
        dlg.text.setText(text + " please")
        dlg.text.textEdited.emit(dlg.text.text())
        self.assertFalse(any(b.isChecked() for b, _e, _t in dlg.preset_buttons))
        dlg.close()

    # ------------------------------------------------------------ search and saved
    def test_search_rows_say_who_wrote_where(self):
        from client.ui.dialogs import RICH_ROLE, SearchDialog, who_where
        store = self.main.store
        ben = Other("ben")
        ben.request("send", conv=f"u:{self.ann}", text="Soft edges on FAL_030 v12, can you check?")
        ben.close()
        self.assertEqual(who_where(store, {"sender_id": self.ben, "conv": P.direct_conv(self.ben)}),
                         ("Ben Das", "to you"), "never 'Ben Das → Ben Das'")
        self.assertEqual(who_where(store, {"sender_id": self.ann, "conv": P.direct_conv(self.ben)}),
                         ("You", "to Ben Das"))
        self.assertEqual(SearchDialog.count_text(1), "1 result")
        self.assertEqual(SearchDialog.count_text(27, more=True), "27 results so far")
        self.assertIn("No messages match “zz”", SearchDialog.count_text(0, "zz"))
        dlg = SearchDialog(self.main)
        dlg.query.setText("soft edges")
        dlg.search()
        self.assertTrue(wait_until(self.app, lambda: dlg.list.count() >= 1))
        rich = dlg.list.item(0).data(RICH_ROLE)
        self.assertIn("<b>Soft</b> <b>edges</b>", rich, "the searched words are bold")
        self.assertIn("to you", dlg.list.item(0).text())
        self.assertEqual(dlg.status.text(), "1 result")
        dlg.close()

    def test_saved_remove_needs_a_selection(self):
        from client.ui.dialogs import SavedDialog
        main = self.main
        msg = {"id": 5151, "conv": P.direct_conv(self.ben), "sender_id": self.ben, "kind": "text",
               "body": "Client notes are on the NAS", "ts": time.time()}
        main.save_for_later(msg, True)
        dlg = SavedDialog(main)
        self.assertFalse(dlg.remove.isEnabled(), "nothing selected: Remove does nothing, so it is off")
        dlg.list.setCurrentRow(0)
        self.assertTrue(dlg.remove.isEnabled())
        dlg._remove()
        self.assertFalse(main.store.is_saved(5151))
        dlg.close()

    def test_read_receipts_in_two_groups(self):
        from client.ui.dialogs import ReadReceiptsDialog
        reads = {"read": [{"name": "Ben Das"}], "unread": [{"name": "Cat Iyer"}, {"name": "Dan Roy"}]}
        dlg = ReadReceiptsDialog(self.main, "Seen?", reads, window_title="Seen by", verb="Seen")
        texts = [dlg.list.item(i).text() for i in range(dlg.list.count())]
        self.assertEqual(texts, ["SEEN · 1", "Ben Das", "NOT SEEN YET · 2", "Cat Iyer", "Dan Roy"])
        self.assertEqual(dlg.windowTitle(), "Seen by")
        dlg.close()

    def test_announcement_needs_a_body(self):
        from client.ui.dialogs import ComposeAnnouncementDialog
        dlg = ComposeAnnouncementDialog(self.main)
        ok = dlg.bb.button(QDialogButtonBox.Ok)
        self.assertFalse(ok.isEnabled())
        dlg.body.setPlainText("Dailies at 4")
        self.assertTrue(ok.isEnabled())
        dlg.close()

    # ------------------------------------------------------------ sign-in
    def test_wrong_password_goes_back_to_the_box(self):
        from client.config import ClientConfig
        from client.ui.login import LoginWindow, brand_gradient
        w = LoginWindow(ClientConfig())
        w.show()
        settle(self.app, 0.2)
        w.set_error("Invalid username or password")
        self.assertTrue(w.password.property("error"))
        self.assertTrue(w.password.hasFocus())
        w.password.textEdited.emit("x")
        self.assertEqual(w.status.text(), "", "typing again clears the message")
        self.assertFalse(w.password.property("error"))
        w.discovery.finished.emit()
        w.close()
        for accent in T.ACCENTS:
            T.apply("light", accent)
            start, end = brand_gradient(T.ACCENT, T.ACCENT_TEXT)
            self.assertGreaterEqual(T.contrast(T.ACCENT_TEXT, start), 4.5, accent)
            self.assertGreaterEqual(T.contrast(T.ACCENT_TEXT, end), 4.5, accent)
        T.apply(self.main.config["theme"], self.main.config["accent"])

    # ------------------------------------------------------------ tour and shortcuts
    def test_tour_dots_stay_put(self):
        from client.ui.help import WelcomeTour
        tour = WelcomeTour(self.main)
        tour.show()
        spots = []
        for i in range(5):
            tour.go(i)
            settle(self.app, 0.05)
            spots.append(tour.dots[0].mapTo(tour, QPoint(0, 0)))
        self.assertEqual(len(set((p.x(), p.y()) for p in spots)), 1, spots)
        tour.close()

    def test_shortcuts_one_chip_per_key(self):
        from client.ui.help import SHORTCUTS, ShortcutsDialog, key_list, key_parts
        keys = [k for _h, items in SHORTCUTS for entry, _w in items for k in key_list(entry)]
        for k in ("Ctrl+K", "Ctrl++", "Ctrl+0", "F1", "C", "Home", "End"):
            self.assertIn(k, keys)
        self.assertFalse([k for k in keys if "  " in k or " in " in k], "no prose inside a key chip")
        self.assertEqual(key_parts("Alt+Shift+↓"), ["Alt", "Shift", "↓"])
        self.assertEqual(key_parts("Ctrl++"), ["Ctrl", "+"])
        self.assertEqual(key_parts("@name"), ["@name"])
        ShortcutsDialog(self.main).close()

    # ------------------------------------------------------------ pictures
    def test_viewer_arrows_and_hover(self):
        from client.ui.gallery import ImageViewer
        conv = P.direct_conv(self.ben)
        msgs = [{"id": 810000 + i, "conv": conv, "sender_id": self.ben, "kind": "file", "body": "",
                 "ts": time.time(), "file": {"id": f"v{i}", "name": f"FAL_030_v0{i}.jpg", "size": 1000}}
                for i in range(3)]
        v = ImageViewer(self.main, msgs, msgs[0])
        v._load = lambda m: None
        v.go(0)
        self.assertTrue(v.prev.isHidden() and not v.next.isHidden(), "no back arrow on the first picture")
        v.go(2)
        self.assertTrue(v.next.isHidden() and not v.prev.isHidden())
        self.assertIn("rgba(255,255,255,0.10)", v.prev.styleSheet(), "a dark hover, whatever the theme")
        self.assertNotIn("...", v.caption.text())
        v.close()

    def test_drawing_stays_on_the_picture_and_scales(self):
        from client.ui.annotate import AnnotateDialog, _Mark, mark_scale
        big = QImage(4096, 2160, QImage.Format_RGB32)
        big.fill(QColor("#203040"))
        dlg = AnnotateDialog(self.main, big)
        dlg.resize(900, 600)
        dlg.show()
        settle(self.app, 0.2)
        c = dlg.canvas
        self.assertEqual(c._to_image(QPointF(-50, -50)), (0.0, 0.0), "a drag into the margin stops at the edge")
        x, y = c._to_image(QPointF(c.width() + 80, c.height() + 80))
        self.assertEqual((x, y), (4096.0, 2160.0))
        self.assertGreater(mark_scale(big), 2.5)
        self.assertFalse(dlg.undo.isEnabled(), "nothing to undo yet")
        c.marks.append(_Mark("pen", "#ff3b4f", 4, [(10, 10), (40, 40)]))
        c.changed.emit()
        self.assertTrue(dlg.undo.isEnabled())
        DialogsV112Test.answer = QMessageBox.Cancel
        dlg.reject()
        self.assertTrue(dlg.isVisible(), "Cancel with marks asks first; saying Cancel keeps the drawing")
        DialogsV112Test.answer = QMessageBox.Discard
        dlg.reject()
        self.assertFalse(dlg.isVisible())

    def test_screenshot_copy_says_so(self):
        from client.ui.snip import ScreenshotDialog
        shown = []
        real = self.main.toast
        self.main.toast = lambda text, *a: shown.append(text)
        try:
            img = QImage(300, 200, QImage.Format_RGB32)
            img.fill(QColor(T.BG))
            dlg = ScreenshotDialog(self.main, img, "Ben Das")
            pm = dlg.preview.pixmap()
            self.assertEqual((pm.width(), pm.height()), (302, 202), "a 1 px edge around the picture")
            dlg._copy()
            self.assertTrue(shown and "copied" in shown[0])
        finally:
            self.main.toast = real
        _ = QPixmap


if __name__ == "__main__":
    unittest.main()
