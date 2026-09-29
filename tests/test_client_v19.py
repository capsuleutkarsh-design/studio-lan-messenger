"""1.9.0 on the client: the outbox, the thread panel, safer links - against a real server."""

import os
import shutil
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent, QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from server.core import ServerCore  # noqa: E402

PORT = 16950


def settle(app, seconds=0.3):
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)


def wait_until(app, check, seconds=8):
    end = time.time() + seconds
    while time.time() < end:
        if check():
            return True
        settle(app, 0.05)
    return check()


class ClientV19Test(unittest.TestCase):
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
        cls.core.config.update(tcp_port=PORT, discovery_port=PORT + 1, trusted_link_hosts="fileserver, nas01")
        cls.core.start()
        mk = lambda u, n: cls.core.call(cls.core.admin_create_user, must_change=False,  # noqa: E731
                                        username=u, password="Artist2026", display_name=n)
        cls.ann, cls.ben = mk("ann", "Ann Rao"), mk("ben", "Ben Das")
        import client.main as cm
        import client.ui.main_window as mw
        mw.play_sound = lambda kind="notify": None
        cls.ctl = cm.App()
        cls.ctl.do_login("127.0.0.1", PORT, "ann", "Artist2026", False)
        assert wait_until(cls.app, lambda: cls.ctl.main is not None, 15), "client did not sign in"
        cls.main = cls.ctl.main
        cls.main.resize(1300, 800)
        settle(cls.app, 0.5)

    @classmethod
    def tearDownClass(cls):
        cls.main.quitting = True
        cls.core.stop()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def history(self, conv):
        from server.db import direct_key
        kind, target = conv.split(":")
        key = direct_key(self.ann, int(target)) if kind == "u" else conv
        return [m["body"] for m in self.core.call(self.core.db.history, key, None, 100)]

    # ------------------------------------------------------------ outbox
    def test_outbox_sends_and_never_twice(self):
        box = self.main.outbox
        conv = f"u:{self.ben}"
        item = box.add(conv, text="first through the outbox")
        self.assertTrue(wait_until(self.app, lambda: not box.pending(conv)))
        self.assertEqual(self.history(conv).count("first through the outbox"), 1)
        # the answer got lost: the same message is sent again - and stored once
        box.items.append(dict(item))
        box.flush()
        self.assertTrue(wait_until(self.app, lambda: not box.pending(conv)))
        self.assertEqual(self.history(conv).count("first through the outbox"), 1)

    def test_outbox_waits_while_offline_and_survives_a_restart(self):
        from client.outbox import Outbox
        box = self.main.outbox
        conv = f"u:{self.ben}"

        class Offline:                                # the server is away
            online = False
            host, port, username = box.conn.host, box.conn.port, box.conn.username
            logged_in = box.conn.logged_in

            def request(self, *a, **k):
                raise AssertionError("nothing may be sent while offline")
        real = box.conn
        box.conn = Offline()
        try:
            box.add(conv, text="typed while the server was down")
            self.assertEqual(len(box.pending(conv)), 1)
        finally:
            box.conn = real
        # Quillo restarts: a new outbox reads the waiting message back from disk
        again = Outbox(real, self.main.store)
        again._on_logged_in()
        self.assertTrue(wait_until(self.app, lambda: not again.pending(conv)))
        self.assertIn("typed while the server was down", self.history(conv))
        box._load()                                   # both read the same file: now empty
        self.assertEqual(box.pending(conv), [])

    def test_a_refused_message_goes_back_to_its_own_chat(self):
        main = self.main
        other = f"u:{self.ben}"
        main.open_conv(other)
        settle(self.app, 0.3)
        main.chat.input.setPlainText("")
        refused = {"client_id": "x", "conv": f"u:{self.ann}", "text": "for my own space"}
        main.store.conversation(refused["conv"]).draft = ""
        main._outbox_refused(refused, "Something was wrong")
        self.assertEqual(main.chat.input.toPlainText(), "", "not into the chat that is open")
        self.assertEqual(main.store.conversation(refused["conv"]).draft, "for my own space")

    # ------------------------------------------------------------ threads
    def test_thread_panel(self):
        main = self.main
        conv = f"u:{self.ben}"
        main.open_conv(conv)
        settle(self.app, 0.3)
        main.chat.input.setPlainText("FAL_030 v12 is up")
        main.chat.send_text()
        self.assertTrue(wait_until(self.app, lambda: "FAL_030 v12 is up" in self.history(conv)))
        root = next(m for m in main.store.conversation(conv).messages.values() if m["body"] == "FAL_030 v12 is up")
        main.open_thread(conv, root["id"])
        self.assertTrue(wait_until(self.app, lambda: root["id"] in main.thread_panel.rows))
        main.thread_panel.input.setPlainText("Edge looks soft")
        main.thread_panel.send()
        self.assertTrue(wait_until(self.app, lambda: len(main.thread_panel.rows) == 2))
        self.assertNotIn("Edge looks soft", self.history(conv), "a thread reply stays out of the chat")
        # the chat shows "1 reply" under the first message
        self.assertTrue(wait_until(self.app, lambda: main.store.conversation(conv).messages[root["id"]]
                                   .get("thread_count") == 1))
        from PySide6.QtWidgets import QPushButton
        texts = [b.text() for b in main.chat.findChildren(QPushButton)]
        self.assertTrue(wait_until(self.app, lambda: any("1 reply" in b.text()
                                                         for b in main.chat.findChildren(QPushButton))), texts)
        main.open_conv(f"u:{self.ann}")                  # another chat: the thread closes
        settle(self.app, 0.2)
        self.assertFalse(main.thread_panel.isVisible())

    # ------------------------------------------------------------ uploads
    def test_an_upload_carries_on_after_the_connection_drops(self):
        path = os.path.join(self.tmp, "plate_v012.exr")
        data = os.urandom(6 * 1024 * 1024 + 321)
        with open(path, "wb") as f:
            f.write(data)
        t = self.main.transfers.upload(path, f"u:{self.ben}")
        dropped = []

        def drop(tt):                     # the network drops at the first progress, part-way through
            if not dropped and 0 < tt.done < tt.size and tt.state == "running":
                dropped.append((tt.done, tt.file_id))
                # as a real drop does: an event of its own, not inside the socket's "bytes sent" callback
                QTimer.singleShot(0, lambda: tt._fail("The remote host closed the connection"))
        t.progress.connect(drop)
        self.assertTrue(wait_until(self.app, lambda: bool(dropped), 20), "the upload never got going")
        self.assertEqual(t.resumes, 1)
        self.assertTrue(wait_until(self.app, lambda: t.state == "done", 30), t.error)
        self.assertEqual(t.file_id, dropped[0][1], "it carried on with the same upload")
        f = self.core.call(self.core.db.get_file, t.file_id)
        with open(f["path"], "rb") as fh:
            self.assertEqual(fh.read(), data)
        self.assertTrue(wait_until(self.app, lambda: any(
            (m.get("file") or {}).get("id") == t.file_id
            for m in self.main.store.conversation(f"u:{self.ben}").messages.values())))

    # ------------------------------------------------------------ found in the 1.9.0 review
    def test_signing_out_keeps_the_message_waiting(self):
        box = self.main.outbox
        item = {"client_id": "keep-me", "conv": f"u:{self.ben}", "text": "sent as I signed out", "created": 0}
        box.items.append(item)
        refused = []
        box.refused.connect(lambda i, e: refused.append(e))
        box._answered(item, {"ok": False, "error": "Signed out"})
        self.assertIn(item, box.items, "kept for the next sign-in of this account")
        self.assertEqual(refused, [])
        box.items.remove(item)
        box._save()

    def test_an_edited_thread_reply_reaches_the_thread_not_the_chat(self):
        store = self.main.store
        seen, updated = [], []
        store.thread_message.connect(lambda m, live: seen.append(m["id"]))
        store.message_updated.connect(lambda m: updated.append(m["id"]))
        store.handle_event({"op": "message_update", "message": {
            "id": 999999, "conv": f"u:{self.ben}", "sender_id": self.ben, "kind": "text", "body": "edited",
            "ts": time.time(), "thread_root": 1}})
        self.assertEqual(seen, [999999])
        self.assertEqual(updated, [])

    def test_a_file_changed_between_tries_is_sent_whole(self):
        from client.transfers import Upload
        path = os.path.join(self.tmp, "render.dpx")
        with open(path, "wb") as f:
            f.write(b"a" * 1000)
        up = Upload(path, f"u:{self.ben}")
        up.file_id, up.stamp = "abc", (1000, 1.0)            # the first try saw another version
        up.start(self.main.conn)
        self.assertIsNone(up.file_id, "not glued onto the old upload")
        up.cancel()

    # ------------------------------------------------------------ links
    def test_links_to_other_computers_ask_first(self):
        from client.ui import widgets
        self.assertEqual(widgets.unc_host(r"\\fileserver\proj\FAL_030"), "fileserver")
        self.assertEqual(widgets.unc_host("Z:/plates/FAL_030"), "")
        self.assertTrue(widgets.link_host_trusted("fileserver"))
        self.assertTrue(widgets.link_host_trusted("nas01.studio.local"))
        self.assertTrue(widgets.link_host_trusted(""))                 # a drive letter / this PC
        self.assertFalse(widgets.link_host_trusted("evil-pc"))
        asked = []
        real = widgets._confirm_host
        widgets._confirm_host = lambda host, parent=None: asked.append(host) or False
        try:
            widgets.path_menu(r"\\evil-pc\share\x.exr")
        finally:
            widgets._confirm_host = real
        self.assertEqual(asked, ["evil-pc"])

    def test_programs_are_never_started_from_a_chat(self):
        from client.ui import widgets
        opened, shown = [], []
        real_open, real_show = widgets.open_path, widgets.show_in_folder
        widgets.open_path, widgets.show_in_folder = opened.append, shown.append
        try:
            widgets.open_file(r"C:\Downloads\invoice.pdf.hta")
            widgets.open_file(r"C:\Downloads\plate.exr")
        finally:
            widgets.open_path, widgets.show_in_folder = real_open, real_show
        self.assertEqual(shown, [r"C:\Downloads\invoice.pdf.hta"])
        self.assertEqual(opened, [r"C:\Downloads\plate.exr"])


if __name__ == "__main__":
    unittest.main()
