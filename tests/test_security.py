"""Password rules, forced password change, audit log, backups and the admin API."""

import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.core import ServerCore  # noqa: E402
from tests import test_server as base  # noqa: E402

PORT = 15350


class Client(base.Client):
    def __init__(self, username, password="Artist2026"):
        base.PORT, old = PORT, base.PORT
        try:
            super().__init__(username, password)
        finally:
            base.PORT = old


class SecurityTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.core = c = ServerCore(cls.tmp)
        c.config.update(tcp_port=PORT, discovery_port=PORT + 1)
        c.start()
        cls.artist = c.call(c.admin_create_user, must_change=False, username="artist", password="Artist2026")

    @classmethod
    def tearDownClass(cls):
        cls.core.stop()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_default_admin_must_change_password(self):
        a = Client("admin", "admin")
        self.assertEqual(a.login["op"], "login_ok")
        self.assertTrue(a.login.get("must_change_password"))                               # everyone knows admin/admin
        self.assertFalse(a.request("send", conv=f"u:{self.artist}", text="hi")["ok"])      # blocked until changed
        self.assertFalse(a.request("change_password", old="admin", new="ADMIN")["ok"])     # not 'admin' again
        self.assertFalse(a.request("change_password", old="admin", new="ab1")["ok"])        # too short
        r = a.request("change_password", old="admin", new="Studio2026")
        self.assertTrue(r["ok"], r)
        self.assertTrue(a.request("send", conv=f"u:{self.artist}", text="hi")["ok"])
        a.close()
        b = Client("admin", "Studio2026")
        self.assertFalse(b.login.get("must_change_password"))
        b.close()

    def test_simple_passwords_by_default(self):
        c = self.core
        with self.assertRaises(ValueError):
            c.call(c.admin_create_user, username="short", password="abc")                 # 4 characters minimum
        c.call(c.admin_create_user, username="easy", password="1234")                     # allowed now
        n = Client("easy", "1234")
        self.assertEqual(n.login["op"], "login_ok")
        self.assertFalse(n.login.get("must_change_password"))                             # keeps the admin's password
        n.close()

    def test_stricter_rules_when_switched_on(self):
        c = self.core
        c.config.update(password_require_mix=True, password_block_weak=True, force_password_change=True,
                        min_password_length=6)
        try:
            for pw in ("123456", "abcdefgh", "strict1"):       # common / no digit / same as the username
                with self.assertRaises(ValueError):
                    c.call(c.admin_create_user, username="strict1" if pw == "strict1" else "strict", password=pw)
            c.call(c.admin_create_user, username="newbie", password="Welcome2026")
            n = Client("newbie", "Welcome2026")
            self.assertTrue(n.login.get("must_change_password"))
            n.close()
        finally:
            c.config.update(password_require_mix=False, password_block_weak=False, force_password_change=False,
                            min_password_length=4)

    def test_old_default_password_settings_are_relaxed_on_upgrade(self):
        import json
        import tempfile
        from server.config import ServerConfig
        d = tempfile.mkdtemp()
        with open(os.path.join(d, "config.json"), "w") as f:
            json.dump({"min_password_length": 6, "password_require_mix": True}, f)
        cfg = ServerConfig(d)
        self.assertEqual(cfg["min_password_length"], 4)
        self.assertIs(cfg["password_require_mix"], False)
        with open(os.path.join(d, "config.json"), "w") as f:                          # an admin's own choice
            json.dump({"min_password_length": 10, "password_require_mix": True, "settings_version": 2}, f)
        cfg = ServerConfig(d)
        self.assertEqual(cfg["min_password_length"], 10)
        self.assertIs(cfg["password_require_mix"], True)

    def test_audit_log(self):
        c = self.core
        uid = c.call(c.admin_create_user, must_change=False, username="audited", password="Audit2026")
        c.call(c.admin_save_department, None, "FX")
        c.call(c.admin_update_user, uid, department="FX")
        c.call(c.admin_update_user, uid, disabled=1)
        actions = [(e["action"], e["target"]) for e in c.call(c.admin_audit, "audited")]
        self.assertIn(("user created", "audited"), actions)
        self.assertIn(("user changed", "audited"), actions)
        self.assertIn(("account disabled", "audited"), actions)

    def test_backup(self):
        c = self.core
        r = c.call(c.backup_now)
        self.assertTrue(r["ok"], r)
        self.assertTrue(os.path.getsize(r["path"]) > 0)
        import sqlite3
        con = sqlite3.connect(r["path"])
        self.assertGreater(con.execute("SELECT COUNT(*) FROM users").fetchone()[0], 0)
        con.close()

    def test_chat_review_is_audited_and_can_be_disabled(self):
        c = self.core
        other = c.call(c.admin_create_user, must_change=False, username="rev2", password="Review2026")
        a = Client("artist")
        self.assertTrue(a.login.get("review_notice"))
        a.request("send", conv=f"u:{other}", text="private note")
        convs = c.call(c.admin_review_conversations, self.artist)
        key = next(x["key"] for x in convs if "rev2" in x["title"])
        msgs = c.call(c.admin_review_history, key)
        self.assertEqual(msgs[-1]["body"], "private note")
        self.assertTrue(any(e["action"] == "chat reviewed" for e in c.call(c.admin_audit)))
        c.config.update(admin_review_enabled=False)
        try:
            with self.assertRaises(ValueError):
                c.call(c.admin_review_history, key)
        finally:
            c.config.update(admin_review_enabled=True)
        a.close()

    def test_report(self):
        c = self.core
        a = Client("artist")
        a.request("send", conv=f"u:{self.artist}", text="note to self for the report")
        r = c.call(c.admin_report, 7)
        me = next(p for p in r["people"] if p["username"] == "artist")
        self.assertGreaterEqual(me["messages"], 1)
        self.assertGreaterEqual(r["total_messages"], 1)
        self.assertTrue(r["daily"])
        a.close()

    def test_client_update(self):
        c = self.core
        folder = c.call(c.admin_updates)["folder"]
        a = Client("artist")
        with open(os.path.join(folder, "LANMessenger-Client-Setup-9.9.1.exe"), "wb") as f:
            f.write(b"MZ-fake-installer")
        with open(os.path.join(folder, "Quillo-Client-Setup-9.10.0.exe"), "wb") as f:     # the name since 1.6.0
            f.write(b"MZ-newer-installer")
        c.call(c._check_updates)
        push = a.wait_for("update_available")["update"]
        self.assertEqual(push["version"], "9.10.0")            # numeric compare, not text
        s = base.tls_connect(PORT)
        s.sendall(__import__("common.protocol", fromlist=["x"]).encode(
            {"op": "download", "token": a.login["token"], "file_id": "client-update"}))
        f = s.makefile("rb")
        hdr = __import__("json").loads(f.readline())
        self.assertEqual((hdr["ok"], hdr["name"]), (True, "Quillo-Client-Setup-9.10.0.exe"))
        self.assertEqual(f.read(hdr["size"]), b"MZ-newer-installer")
        s.close()
        a.close()

    def test_admin_api_requires_admin(self):
        a = Client("artist")
        self.assertFalse(a.request("admin_call", fn="admin_users")["ok"])
        a.close()
        c = self.core
        c.call(c.admin_create_user, must_change=False, username="boss", password="Boss2026x", is_admin=1)
        b = Client("boss", "Boss2026x")
        r = b.request("admin_call", fn="admin_users")
        self.assertTrue(r["ok"], r)
        self.assertTrue(any(u["username"] == "artist" for u in r["result"]))
        self.assertFalse(b.request("admin_call", fn="stop")["ok"])            # not on the allow-list
        r = b.request("admin_call", fn="admin_create_user", kwargs={"username": "viaapi", "password": "Api2026xx"})
        self.assertTrue(r["ok"], r)
        entry = c.call(c.admin_audit, "viaapi")[0]
        self.assertEqual(entry["actor"], "boss")
        b.close()


if __name__ == "__main__":
    unittest.main()
