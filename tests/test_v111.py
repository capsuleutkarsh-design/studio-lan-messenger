"""1.11.0: shot status, the leave auto-reply, saved messages, the update tracker, "not seen lately",
EXR / MOV previews made by the server - and the client side of next-unread, mark-all-read, drafts,
"In a meeting", drawing on a picture and comparing two versions."""

import base64
import datetime
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from common import protocol as P  # noqa: E402
from server.core import ServerCore  # noqa: E402
from tests import test_server as base  # noqa: E402

PORT = 17350


class Client(base.Client):
    def __init__(self, username, password="Artist2026", version=None):
        base.PORT, old = PORT, base.PORT
        try:
            self.sock = base.tls_connect(PORT)
            self.file = self.sock.makefile("rb")
            self.rid = 0
            self.pending = []
            login = {"op": "login", "username": username, "password": password}
            if version:
                login.update(version=version, pc=f"{username.upper()}-PC")
            self.sock.sendall(P.encode(login))
            self.login = self.read()
        finally:
            base.PORT = old


def ffmpeg():
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:                                   # noqa: BLE001
        return None


class ServerV111Test(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.core = c = ServerCore(self.tmp)
        c.config.update(tcp_port=PORT, discovery_port=PORT + 1)
        c.start()
        mk = lambda u, n: c.call(c.admin_create_user, must_change=False, username=u,  # noqa: E731
                                 password="Artist2026", display_name=n)
        self.a, self.b, self.cat = mk("ann", "Ann Rao"), mk("ben", "Ben Das"), mk("cat", "Cat Iyer")

    def tearDown(self):
        self.core.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ------------------------------------------------------------ shot status
    def test_shot_status(self):
        a, b = Client("ann"), Client("ben")
        room = a.request("create_room", name="Comp", members=[self.b])["room_id"]
        r = a.request("shot_status_set", shot="FAL_030", status="approved", conv=f"r:{room}")
        self.assertTrue(r["ok"], r)
        push = b.wait_for("shot_status")
        self.assertEqual((push["shot"], push["status"]), ("FAL_030", "approved"))
        notes = [m["message"] for m in b.pending if m["op"] == "message"]
        while not any("FAL_030" in n["body"] for n in notes):
            notes.append(b.wait_for("message")["message"])
        note = next(n for n in notes if "FAL_030" in n["body"])
        self.assertEqual(note["kind"], "system")
        self.assertIn("FAL_030 to ✅ Approved", note["body"])
        b.request("shot_status_set", shot="FAL_030", status="changes")
        hist = a.request("shot_history", shot="fal_030")["history"]
        self.assertEqual([h["status"] for h in hist], ["changes", "approved"])
        self.assertEqual(hist[0]["name"], "Ben Das")
        boot = self.core.call(self.core.bootstrap, self.cat, "t")
        self.assertIn(["FAL_030", "changes", self.b], [s[:3] for s in boot["shots"]])
        self.assertFalse(a.request("shot_status_set", shot="FAL_030", status="done")["ok"])
        self.assertFalse(a.request("shot_status_set", shot="<b>", status="wip")["ok"])
        a.close()
        b.close()

    # ------------------------------------------------------------ leave
    def test_leave_auto_reply_once_a_day(self):
        a, b = Client("ann"), Client("ben")
        today = datetime.date.today().isoformat()
        r = b.request("cal_leave_add", first_day=today, last_day=today, auto_reply="Back on Monday, ask Lea")
        self.assertTrue(r["ok"], r)
        a.request("send", conv=f"u:{self.b}", text="Are the plates in?")
        reply = a.wait_for("message")["message"]
        self.assertEqual(reply["kind"], "system")
        self.assertIn("Back on Monday, ask Lea", reply["body"])
        a.request("send", conv=f"u:{self.b}", text="Also, the grain?")
        time.sleep(0.4)
        history = a.request("history", conv=f"u:{self.b}")["messages"]
        self.assertEqual(sum(1 for m in history if "Automatic reply" in m["body"]), 1, "once a day")
        unread = {(k, t): n for k, t, _l, n in self.core.call(self.core.db.recent_conversations, self.a)}
        self.assertEqual(unread.get(("u", self.b), 0), 0, "the automatic reply is not an unread message")
        a.close()
        b.close()

    # ------------------------------------------------------------ saved for later
    def test_saved_list_may_be_long(self):
        a = Client("ann")
        items = [{"id": i, "conv": f"u:{self.b}", "sender_id": self.b, "snippet": "x" * 150, "ts": 0}
                 for i in range(200)]
        self.assertTrue(a.request("set_pref", key="saved", value=items)["ok"])
        self.assertFalse(a.request("set_pref", key="focus", value={"x": "y" * 5000})["ok"])
        a.close()

    # ------------------------------------------------------------ the update tracker
    def test_update_tracker(self):
        updates = os.path.join(self.tmp, "updates")
        os.makedirs(updates, exist_ok=True)
        with open(os.path.join(updates, "Quillo-Client-Setup-1.11.0.exe"), "wb") as f:
            f.write(b"x" * 10)
        self.core.call(self.core._check_updates)
        old, new = Client("ann", version="1.9.0"), Client("ben", version="1.11.0")
        u = self.core.call(self.core.admin_updates)
        people = {p["username"]: p for p in u["people"]}
        self.assertEqual(people["ann"]["version"], "1.9.0")
        self.assertEqual(people["ann"]["pc"], "ANN-PC")
        self.assertTrue(people["ben"]["online"])
        self.assertEqual(self.core.call(self.core.admin_remind_update)["reminded"], 1, "only the old PC")
        self.assertTrue(old.wait_for("update_available").get("reminder"))
        old.close()
        new.close()
        time.sleep(0.3)
        u = self.core.call(self.core.admin_updates)
        self.assertEqual({p["username"]: p["version"] for p in u["people"]}["ann"], "1.9.0",
                         "remembered after signing out")

    def test_report_lists_people_not_seen_for_a_month(self):
        Client("ann").close()
        self.core.call(lambda: self.core.db._exec("UPDATE users SET last_seen=? WHERE id=?",
                                                  time.time() - 40 * 86400, self.b))
        r = self.core.call(self.core.admin_report, 30)
        names = [p["name"] for p in r["inactive"]]
        self.assertIn("Ben Das", names)
        self.assertIn("Cat Iyer", names, "never signed in")
        self.assertNotIn("Ann Rao", names)

    # ------------------------------------------------------------ previews made by the server
    @unittest.skipUnless(ffmpeg(), "imageio-ffmpeg is not installed")
    def test_exr_and_mov_previews(self):
        a = Client("ann")
        exe = ffmpeg()
        clip = os.path.join(self.tmp, "FAL_030_v012.mov")
        exr = os.path.join(self.tmp, "FAL_030_v012.1001.exr")
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        subprocess.run([exe, "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=640x360:rate=24:duration=2",
                        "-pix_fmt", "yuv420p", clip], check=True, creationflags=flags)
        subprocess.run([exe, "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=640x360", "-frames:v", "1",
                        "-pix_fmt", "gbrpf32le", exr], check=True, creationflags=flags)
        for path in (clip, exr):
            data = open(path, "rb").read()
            s = base.tls_connect(PORT)
            s.sendall(P.encode({"op": "upload", "token": a.login["token"], "name": os.path.basename(path),
                                "size": len(data)}))
            f = s.makefile("rb")
            import json
            hdr = json.loads(f.readline())
            s.sendall(data)
            json.loads(f.readline())
            s.close()
            a.request("send", conv=f"u:{self.b}", file_id=hdr["file_id"])
            r = a.request("thumb", file_id=hdr["file_id"], size=320)
            self.assertTrue(r["ok"], (path, r))
            jpg = base64.b64decode(r["data"])
            self.assertTrue(jpg.startswith(b"\xff\xd8"), "a JPEG")
            again = a.request("thumb", file_id=hdr["file_id"], size=320)
            self.assertEqual(again["data"], r["data"], "made once, then kept")
        b = Client("cat")
        self.assertFalse(b.request("thumb", file_id=hdr["file_id"])["ok"], "only people who can see the file")
        a.close()
        b.close()


class ClientV111Test(unittest.TestCase):
    """The client side, against a real server."""

    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication, QMessageBox
        from tests.test_client_v19 import settle, wait_until
        cls.settle, cls.wait_until = staticmethod(settle), staticmethod(wait_until)
        cls.tmp = tempfile.mkdtemp()
        os.environ["APPDATA"] = os.path.join(cls.tmp, "appdata")
        os.environ["LOCALAPPDATA"] = os.path.join(cls.tmp, "local")
        cls.app = QApplication.instance() or QApplication(sys.argv)
        cls.app.setQuitOnLastWindowClosed(False)
        QMessageBox.exec = lambda self, *a: QMessageBox.Ok
        for name in ("information", "warning", "critical", "question"):
            setattr(QMessageBox, name, staticmethod(lambda *a, **k: QMessageBox.Yes))
        cls.core = ServerCore(os.path.join(cls.tmp, "server"))
        cls.core.config.update(tcp_port=PORT + 10, discovery_port=PORT + 11)
        cls.core.start()
        mk = lambda u, n: cls.core.call(cls.core.admin_create_user, must_change=False,  # noqa: E731
                                        username=u, password="Artist2026", display_name=n)
        cls.ann, cls.ben, cls.cat = mk("ann", "Ann Rao"), mk("ben", "Ben Das"), mk("cat", "Cat Iyer")
        import client.main as cm
        import client.ui.main_window as mw
        mw.play_sound = lambda kind="notify": None
        cls.ctl = cm.App()
        cls.ctl.do_login("127.0.0.1", PORT + 10, "ann", "Artist2026", False)
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

    def others_write(self):
        base.PORT, old = PORT + 10, base.PORT
        try:
            b, c = base.Client("ben"), base.Client("cat")
        finally:
            base.PORT = old
        b.request("send", conv=f"u:{self.ann}", text="from Ben")
        c.request("send", conv=f"u:{self.ann}", text="from Cat")
        b.close()
        c.close()

    def test_next_unread_and_mark_all_read(self):
        main, store = self.main, self.main.store
        main.open_conv(P.direct_conv(self.ann))
        self.others_write()
        self.assertTrue(self.wait_until(self.app, lambda: store.total_unread() >= 2))
        main.next_unread()
        first = main.chat.conv
        self.assertIn(first, (P.direct_conv(self.ben), P.direct_conv(self.cat)))
        main.open_conv(P.direct_conv(self.ann))
        before = store.total_unread()
        self.others_write()
        self.assertTrue(self.wait_until(self.app, lambda: store.total_unread() >= before + 2))
        self.assertTrue(main.sidebar.b_read_all.isVisibleTo(main.sidebar))
        store.mark_all_read()
        self.assertEqual(store.total_unread(), 0)
        self.settle(self.app, 0.2)
        self.assertFalse(main.sidebar.b_read_all.isVisibleTo(main.sidebar))

    def test_a_draft_shows_in_the_list_and_survives_a_restart(self):
        main = self.main
        conv = P.direct_conv(self.cat)
        main.open_conv(conv)
        main.chat.input.setPlainText("half-typed note about FAL_030")
        main.open_conv(P.direct_conv(self.ann))
        self.settle(self.app, 0.3)
        main.sidebar.rebuild()
        item = main.sidebar.lists["chats"].items.get(conv)
        self.assertIsNotNone(item, "a chat with a draft is in the list")
        self.assertTrue(item.draft)
        main._save_drafts()
        saved = main.config["drafts"][main._drafts_key()]
        self.assertEqual(saved[conv], "half-typed note about FAL_030")
        main.store.conversation(conv).draft = ""          # as after a restart
        main._load_drafts()
        self.assertEqual(main.store.conversation(conv).draft, "half-typed note about FAL_030")
        main.open_conv(conv)
        main.chat.input.clear()
        main.open_conv(P.direct_conv(self.ann))

    def test_save_for_later(self):
        main, store = self.main, self.main.store
        msg = {"id": 4242, "conv": P.direct_conv(self.ben), "sender_id": self.ben, "kind": "text",
               "body": "Client notes are on the NAS", "ts": time.time()}
        main.save_for_later(msg, True)
        self.assertTrue(store.is_saved(4242))
        self.assertTrue(self.wait_until(self.app, lambda: any(
            x["id"] == 4242 for x in self.core.call(self.core.db.prefs, self.ann).get("saved", []))))
        from client.ui.dialogs import SavedDialog
        dlg = SavedDialog(main)
        self.assertEqual(dlg.list.count(), 1)
        self.assertIn("Client notes", dlg.list.item(0).text())
        dlg.list.setCurrentRow(0)
        dlg._remove()
        self.assertFalse(store.is_saved(4242))
        dlg.close()

    def test_a_shot_shows_its_status(self):
        from client.ui.widgets import linkify, set_shot_pattern
        main, store = self.main, self.main.store
        set_shot_pattern(P.SHOT_PATTERN_DEFAULT)
        main.set_shot_status("FAL_040", "review")
        self.assertTrue(self.wait_until(self.app, lambda: (store.shot_status("FAL_040") or (None,))[0] == "review"))
        self.assertIn("🔍", linkify("FAL_040 is ready"))
        self.assertNotIn("🔍", linkify("FAL_050 is not"))
        from client.ui.dialogs import SearchDialog
        dlg = SearchDialog(main, "FAL_040")
        self.assertIn("Ready for review", dlg.shot_label.text())
        dlg.close()

    def test_in_a_meeting_while_one_runs(self):
        main = self.main
        sent = []
        real = main.conn.send
        main.conn.send = lambda op, **kw: sent.append((op, kw))
        try:
            now = time.time()
            data = {"items": [{"kind": "meeting", "all_day": False, "my_rsvp": "yes", "start": now - 60,
                               "end": now + 1800, "id": "m1", "title": "Dailies"}]}
            main.store.me["status_msg"] = ""
            main._auto_meeting = None
            main._meeting_status(data, now)
            self.assertEqual(sent[-1][1]["status_msg"], "In a meeting")
            self.assertEqual(sent[-1][1]["status_until"], now + 1800)
            main.store.me["status_msg"] = "In a meeting"
            main._meeting_status({"items": []}, now + 60)              # it was deleted: cleared straight away
            self.assertEqual(sent[-1][1]["status_msg"], "")
            sent.clear()
            main.store.me["status_msg"] = "Rendering - don't touch my PC"
            main._meeting_status(data, now)
            self.assertEqual(sent, [], "a status I set myself is never replaced")
        finally:
            main.conn.send = real
            main.store.me["status_msg"] = ""
            main._auto_meeting = None

    def test_draw_on_a_picture(self):
        from PySide6.QtCore import QPointF
        from PySide6.QtGui import QColor, QImage
        from client.ui.annotate import AnnotateDialog, _Mark
        img = QImage(400, 300, QImage.Format_RGB32)
        img.fill(QColor("#203040"))
        dlg = AnnotateDialog(self.main, img)
        dlg.canvas.marks.append(_Mark("arrow", "#ff3b4f", 4, [(20, 20), (200, 150)]))
        dlg.canvas.marks.append(_Mark("circle", "#ffcc33", 4, [(250, 50), (350, 200)]))
        dlg.canvas.marks.append(_Mark("text", "#ffffff", 4, [(30, 240)], "edge soft here"))
        dlg.resize(700, 500)
        self.assertFalse(dlg.canvas.grab().isNull(), "the canvas draws (it once hid QWidget.width)")
        out = dlg.image()
        self.assertEqual((out.width(), out.height()), (400, 300))
        self.assertNotEqual(out.pixelColor(110, 85).name(), "#203040", "the arrow is drawn into the picture")
        dlg.canvas.undo()
        self.assertEqual(len(dlg.canvas.marks), 2)
        path = dlg.save("Notes on FAL_030")
        self.assertTrue(path and os.path.exists(path))
        dlg.close()
        _ = QPointF

    def test_compare_two_versions(self):
        from PySide6.QtGui import QColor, QPixmap
        from client.ui.gallery import ImageViewer
        conv = P.direct_conv(self.ben)
        msgs = [{"id": 800000 + i, "conv": conv, "sender_id": self.ben, "kind": "file", "body": "", "ts": time.time(),
                 "file": {"id": f"cmp{i}", "name": f"FAL_030_v01{i}.jpg", "size": 1000}} for i in range(2)]
        viewer = ImageViewer(self.main, msgs, msgs[0])
        colors = ("#aa2222", "#22aa22")
        viewer._load = lambda m: (lambda pm: (pm.fill(QColor(colors[msgs.index(m)])), pm)[1])(QPixmap(1280, 720))
        viewer.go(0)
        viewer.toggle_compare()
        self.assertEqual(viewer.view.mode, "wipe")
        self.assertEqual(viewer.view.labels, ("FAL_030_v010", "FAL_030_v011"))
        viewer.resize(800, 600)
        viewer.view.resize(640, 360)
        shot = viewer.view.grab().toImage()
        left, right = shot.pixelColor(100, 180).name(), shot.pixelColor(560, 180).name()
        self.assertEqual((left, right), colors, "the old version left of the line, the new one right")
        viewer.side.setChecked(True)
        self.assertEqual(viewer.view.mode, "side")
        viewer.toggle_compare()
        self.assertEqual(viewer.view.mode, "")
        viewer.close()

    def test_form_labels_sit_level_with_their_fields(self):
        from PySide6.QtWidgets import QFormLayout
        from client.ui.dialogs import ChangePasswordDialog
        dlg = ChangePasswordDialog(self.main)
        dlg.show()
        self.settle(self.app, 0.2)
        form = dlg.findChildren(QFormLayout)[0]
        label = form.itemAt(0, QFormLayout.LabelRole).widget()
        field = form.itemAt(0, QFormLayout.FieldRole)
        self.assertEqual(label.minimumHeight(), field.sizeHint().height())
        dlg.close()


if __name__ == "__main__":
    unittest.main()
