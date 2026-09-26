"""The safe copy of the server's data in the central folder, and bringing it back after a reinstall."""

import argparse
import os
import shutil
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server import safecopy  # noqa: E402
from server.core import ServerCore  # noqa: E402

PORT = 16650


class SafeCopyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.data = os.path.join(self.tmp, "server PC", "data")
        self.central = os.path.join(self.tmp, "NAS", "Quillo files")     # the shared-files folder elsewhere
        self.core = ServerCore(self.data)
        self.core.config.update(tcp_port=PORT, discovery_port=PORT + 1, storage_dir=self.central)
        self.core.start()
        self.copy = os.path.join(self.central, safecopy.FOLDER_NAME)

    def tearDown(self):
        self.core.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_copy_only_when_changed_and_where(self):
        c = self.core
        self.assertEqual(safecopy.folder(c.config), self.copy)
        self.assertTrue(c.call(c._safe_copy_due))                   # never copied yet
        c.call(c.admin_create_user, must_change=False, username="ann", password="Artist2026", display_name="Ann")
        r = c.call(c.safe_copy_now)
        self.assertTrue(r["ok"], r)
        info = safecopy.read_info(self.copy)
        self.assertEqual(info["users"], 2)                          # admin + ann
        for name in ("messenger.db", "config.json", os.path.join("tls", "server.crt")):
            self.assertTrue(os.path.isfile(os.path.join(self.copy, name)), name)
        c.last_safe_copy["time"] -= 3600                            # long ago, but nothing changed since
        self.assertFalse(c.call(c._safe_copy_due))
        c.call(c.admin_create_user, must_change=False, username="ben", password="Artist2026")
        self.assertTrue(c.call(c._safe_copy_due))
        c.last_safe_copy["time"] = time.time()                      # changed, but copied a minute ago
        self.assertFalse(c.call(c._safe_copy_due))
        self.assertEqual(c.call(c.admin_server_info)["safe_copy_dir"], self.copy)
        # shared files inside the data folder: no automatic copy (same disk); an explicit folder still works
        c.config.update(storage_dir="")
        self.assertEqual(safecopy.folder(c.config), "")
        with self.assertRaises(ValueError):
            c.call(c.safe_copy_now)
        c.config.update(safe_copy_dir=os.path.join(self.tmp, "USB"))
        self.assertTrue(c.call(c.safe_copy_now)["ok"])
        c.config.update(safe_copy_enabled=False)
        self.assertEqual(safecopy.folder(c.config), "")

    def test_reinstall_restores_everything(self):
        from server.main import restore
        c = self.core
        c.call(c.admin_create_user, must_change=False, username="ann", password="Artist2026", display_name="Ann")
        room = c.call(c.admin_save_room, None, "FAL Delivery", "", [])
        fingerprint = c.tls_fingerprint
        c.config.update(server_name="Studio 7")
        c.stop()                                                    # the final copy on stop
        self.assertEqual(safecopy.read_info(self.copy)["server_name"], "Studio 7")
        # a fresh install on another folder (the old data folder is gone)
        new_data = os.path.join(self.tmp, "new PC", "data")
        args = argparse.Namespace(restore=self.copy, storage=self.central, backups=None, logs=None)
        self.assertEqual(restore(new_data, args), 0)
        self.core = ServerCore(new_data)
        self.core.config.update(tcp_port=PORT, discovery_port=PORT + 1)
        self.core.start()
        n = self.core
        self.assertEqual(n.config["server_name"], "Studio 7")
        self.assertEqual(n.config.storage_dir, self.central)
        self.assertIn("ann", [u["username"] for u in n.call(n.admin_users)])
        self.assertIn(room, [r["id"] for r in n.call(n.admin_rooms)])
        self.assertEqual(n.tls_fingerprint, fingerprint)             # the PCs keep trusting it
        # never over a live database
        self.assertEqual(restore(new_data, args), 3)
        with open(os.path.join(new_data, "restore-failed.txt"), encoding="utf-8") as f:
            self.assertIn("already holds a database", f.read())

    def test_a_stuck_server_says_where(self):
        c = self.core

        def slow_step():
            time.sleep(2.5)          # > 1 s stall + up to 1 s between the watchdog's checks
        with self.assertLogs("server", "WARNING") as logs:
            c.call(slow_step)
            end = time.time() + 5
            while not any("stuck" in m for m in logs.output) and time.time() < end:
                time.sleep(0.1)
        text = "\n".join(logs.output)
        self.assertIn("stuck", text)
        self.assertIn("slow_step", text)                        # names the code that held it up

    def test_incomplete_copy_is_not_offered(self):
        c = self.core
        self.assertTrue(c.call(c.safe_copy_now)["ok"])
        os.remove(os.path.join(self.copy, safecopy.INFO))            # e.g. the network dropped mid-copy
        self.assertIsNone(safecopy.read_info(self.copy))
        with self.assertRaises(ValueError):
            safecopy.restore(self.copy, os.path.join(self.tmp, "empty"))


if __name__ == "__main__":
    unittest.main()
