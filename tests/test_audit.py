"""Regression tests for the issues found in the code audit (v1.4.0)."""

import glob
import json
import os
import shutil
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from common import protocol as P  # noqa: E402
from server import archive  # noqa: E402
from server.config import ServerConfig  # noqa: E402
from server.core import ServerCore  # noqa: E402
from tests import test_server as base  # noqa: E402

PORT = 15750


class Client(base.Client):
    def __init__(self, username, password="Artist2026"):
        base.PORT, old = PORT, base.PORT
        try:
            super().__init__(username, password)
        finally:
            base.PORT = old


class AuditTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.core = c = ServerCore(self.tmp)
        c.config.update(tcp_port=PORT, discovery_port=PORT + 1, message_retention_days=90)
        c.start()
        mk = lambda u, n: c.call(c.admin_create_user, must_change=False, username=u,  # noqa: E731
                                 password="Artist2026", display_name=n)
        self.a, self.b = mk("ann", "Ann Rao"), mk("ben", "Ben Das")

    def tearDown(self):
        self.core.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_db(self, fn):
        return self.core.call(fn)

    # ------------------------------------------------------------ planner
    def test_broken_scheduled_row_does_not_block_reminders(self):
        db = self.core.db
        due = time.time() + 30
        bad = self.run_db(lambda: db.add_scheduled(self.a, "u:99999999999999999999", "hi", "", due))
        rid = self.run_db(lambda: db.add_reminder(self.b, "", None, "Stand-up", due))
        self.core.call(self.core.run_due, time.time() + 60)
        self.assertEqual(self.run_db(lambda: db.get_scheduled(bad))["state"], "failed")
        self.assertEqual(self.run_db(lambda: db.get_reminder(rid))["state"], 1)

    def test_schedule_checks_target_and_lateness(self):
        a = Client("ann")
        self.assertFalse(a.request("schedule_add", conv="u:424242", text="x", due_at=time.time() + 60)["ok"])
        ok = a.request("schedule_add", conv=f"u:{self.b}", text="Meeting at 9", due_at=time.time() + 60)
        self.assertTrue(ok["ok"], ok)
        self.core.call(self.core.run_due, time.time() + 13 * 3600)       # server was off all night
        row = self.run_db(lambda: self.core.db.get_scheduled(ok["scheduled"]["id"]))
        self.assertEqual(row["state"], "failed")
        self.assertIn("server was off", row["error"])

    def test_done_reminder_cannot_be_revived(self):
        a = Client("ann")
        r = a.request("reminder_add", text="x", due_at=time.time() + 60)["reminder"]
        a.request("reminder_done", id=r["id"])
        self.assertFalse(a.request("reminder_snooze", id=r["id"], minutes=5)["ok"])

    # ------------------------------------------------------ settings, backup
    def test_config_values_are_type_checked(self):
        d = tempfile.mkdtemp()
        try:
            with open(os.path.join(d, "config.json"), "w") as f:
                json.dump({"max_file_mb": None, "backup_hour": "2am", "tls_enabled": "yes", "tcp_port": "5150"}, f)
            cfg = ServerConfig(d)
            self.assertEqual(cfg["max_file_mb"], 20480)       # default kept
            self.assertEqual(cfg["backup_hour"], 2)
            self.assertIs(cfg["tls_enabled"], True)
            self.assertEqual(cfg["tcp_port"], 5150)
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_failed_backup_is_reported_not_raised(self):
        blocker = os.path.join(self.tmp, "not_a_folder")
        open(blocker, "w").close()
        self.core.config.update(backup_dir=blocker)
        result = self.core.call(self.core.backup_now)
        self.assertFalse(result["ok"])

    # ------------------------------------------------------------- archive
    def test_edits_after_export_are_not_lost(self):
        a = Client("ann")
        m = a.request("send", conv=f"u:{self.b}", text="Delivery on Friday")["message"]
        self.core.call(self.core.chat_backup_now)
        a.request("edit", id=m["id"], text="Delivery moved to Monday")
        self.run_db(lambda: self.core.db._exec("UPDATE messages SET created_at=? WHERE id=?",
                                               time.time() - 120 * 86400, m["id"]))
        self.run_db(lambda: self.core.db.set_meta(archive.META_LAST_RUN, 0))
        result = self.core.call(self.core.chat_backup_now)
        self.assertEqual(result["removed"], 1)
        text = ""
        for path in glob.glob(os.path.join(result["folder"], "*", "*")):
            with open(path, encoding="utf-8") as f:
                text += f.read()
        self.assertIn("Delivery on Friday", text)
        self.assertIn("[final version", text)
        self.assertIn("Delivery moved to Monday", text)

    def test_export_in_batches(self):
        a = Client("ann")
        for i in range(3):
            a.request("send", conv=f"u:{self.b}", text=f"m{i}")
        db = self.core.db
        first = self.core.call(lambda: archive.export_chat_logs(db, self.core.config, batches=0))
        self.assertTrue(first["more"])
        rest = self.core.call(lambda: archive.export_chat_logs(db, self.core.config, batches=1))
        self.assertFalse(rest["more"])
        self.assertEqual(rest["messages"], 3)

    # --------------------------------------------------------- messages
    def test_unread_ignores_deleted_and_read_is_clamped(self):
        a = Client("ann")
        m = a.request("send", conv=f"u:{self.b}", text="oops")["message"]
        a.request("delete_message", id=m["id"])
        b = Client("ben")
        recent = {r["conv"]: r["unread"] for r in b.login["recent"]}
        self.assertEqual(recent.get(f"u:{self.a}", 0), 0)
        keep = a.request("send", conv=f"u:{self.b}", text="real one")["message"]
        b.request("mark_read", conv=f"u:{self.a}", up_to=10 ** 12)
        receipt = a.wait_for("receipt")
        while receipt["type"] != "read":
            receipt = a.wait_for("receipt")
        self.assertEqual(receipt["up_to"], keep["id"])

    def test_password_change_signs_out_other_pcs(self):
        pc1, pc2 = Client("ann"), Client("ann")
        r = pc1.request("change_password", old="Artist2026", new="Brand-New-2026")
        self.assertTrue(r["ok"], r)
        self.assertEqual(pc2.wait_for("kicked")["op"], "kicked")

    def test_change_password_is_throttled(self):
        a = Client("ann")
        for _ in range(5):
            a.request("change_password", old="wrong", new="Whatever-2026")
        r = a.request("change_password", old="Artist2026", new="Whatever-2026")
        self.assertFalse(r["ok"])
        self.assertIn("Too many", r["error"])

    def test_orphan_files_are_purged(self):
        db = self.core.db
        path = os.path.join(self.tmp, "orphan.bin")
        with open(path, "wb") as f:
            f.write(b"x" * 10)

        def add():
            db.add_file("f" * 32, "left.bin", 10, self.a, path)
            db.complete_file("f" * 32)
            db._exec("UPDATE files SET created_at=? WHERE id=?", time.time() - 3 * 86400, "f" * 32)
        self.run_db(add)
        self.core.call(self.core.purge_files)
        self.assertFalse(os.path.exists(path))
        self.assertEqual(self.run_db(lambda: db.get_file("f" * 32))["purged"], 1)

    # --------------------------------------------------------- network
    def test_huge_first_line_is_refused(self):
        s = base.tls_connect(PORT)
        s.settimeout(10)
        try:
            s.sendall(b"x" * (200 * 1024))
            data = s.recv(100)
        except OSError:
            data = b""
        finally:
            s.close()
        self.assertEqual(data, b"")

    def test_bad_conversation_ids(self):
        with self.assertRaises(ValueError):
            P.parse_conv("u:99999999999999999999")

    def test_console_pins_the_server(self):
        from server import console_api
        old = os.environ.get("APPDATA")
        os.environ["APPDATA"] = self.tmp
        try:
            self.core.call(self.core.admin_update_user, self.a, is_admin=1)
            api = console_api.RemoteApi("127.0.0.1", PORT)
            self.assertEqual(api.connect("ann", "Artist2026"), "")
            api.close()
            console_api.save_pin(f"127.0.0.1:{PORT}", "AA:BB")             # pretend another server was here
            api = console_api.RemoteApi("127.0.0.1", PORT)
            err = api.connect("ann", "Artist2026")
            self.assertIn("identity", err)
            self.assertIsNotNone(api.pin_mismatch)
            self.assertEqual(api.connect("ann", "Artist2026", trust=api.pin_mismatch[1]), "")
            api.close()
        finally:
            if old is None:
                os.environ.pop("APPDATA", None)
            else:
                os.environ["APPDATA"] = old


    def test_odd_requests_get_clean_answers(self):
        """Requests found by fuzzing that used to end in a server error or a dropped connection."""
        a = Client("ann")
        a.sock.sendall(P.encode({"op": ["x"], "rid": 900}))
        reply = a.wait_for("reply")
        self.assertFalse(reply["ok"])
        self.assertTrue(a.request("ping")["ok"])                        # the session survived
        self.assertFalse(a.request("reminder_add", text="x", due_at=float("nan"))["ok"])
        self.assertTrue(a.request("search", query="A" * 1_000_000)["ok"])
        self.assertIsNotNone(self.core.call(lambda: self.core.db.get_user(self.a)))


class RobustnessTest(unittest.TestCase):
    """Things in the environment that used to stop the server (found by fault injection)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def make(self, port, **cfg):
        core = ServerCore(os.path.join(self.tmp, str(port)))
        core.config.update(tcp_port=port, discovery_port=port + 1, **cfg)
        return core

    def test_missing_storage_folder_does_not_stop_the_server(self):
        core = self.make(PORT + 20, storage_dir=r"Q:\no\such\drive")
        core.start()
        try:
            self.assertTrue(core.running)
            self.assertIn("not available", core.call(core.admin_server_info)["storage_error"])
        finally:
            core.stop()

    def test_empty_database_file_is_not_replaced(self):
        core = self.make(PORT + 22)
        open(core.config.db_path, "wb").close()
        with self.assertRaises(Exception):
            core.start()
        self.assertEqual(os.path.getsize(core.config.db_path), 0)

    def test_failed_start_releases_the_port(self):
        import socket as sk
        blocker = sk.socket()
        blocker.bind(("0.0.0.0", PORT + 25))          # the discovery port is taken: fine, TCP is free
        core = self.make(PORT + 24)
        busy = sk.socket()
        busy.bind(("0.0.0.0", PORT + 24))
        busy.listen()
        with self.assertRaises(OSError):
            core.start()
        busy.close()
        core.start()                                  # works now: nothing was left open by the failed try
        try:
            self.assertTrue(core.running)
        finally:
            core.stop()
            blocker.close()

    def test_settings_typos_keep_safe_values(self):
        d = os.path.join(self.tmp, "cfg")
        os.makedirs(d)
        with open(os.path.join(d, "config.json"), "w") as f:
            f.write('{"tls_enabled": "maybe", "tcp_port": 99999, "max_file_mb": Infinity}')
        cfg = ServerConfig(d)
        self.assertIs(cfg["tls_enabled"], True)
        self.assertEqual(cfg["tcp_port"], 5150)
        self.assertEqual(cfg["max_file_mb"], 20480)

    def test_backup_with_clock_in_the_past_keeps_the_new_copy(self):
        core = self.make(PORT + 26, backup_keep=2)
        core.start()
        try:
            folder = core.config.backup_dir
            os.makedirs(folder, exist_ok=True)
            for name in ("messenger_2030-01-01_0200.db", "messenger_2030-01-02_0200.db"):
                with open(os.path.join(folder, name), "wb") as f:
                    f.write(b"old")
            result = core.call(core.backup_now)
            self.assertTrue(result["ok"], result)
            self.assertTrue(os.path.exists(result["path"]))
            self.assertEqual(len([f for f in os.listdir(folder) if f.startswith("messenger_")]), 2)
        finally:
            core.stop()


if __name__ == "__main__":
    unittest.main()
