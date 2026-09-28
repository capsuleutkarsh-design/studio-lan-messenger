"""The licence credits (UT Community Licence 2.0, section 5) and the startup check that enforces them."""

import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QLabel, QVBoxLayout, QWidget  # noqa: E402

from common import licence  # noqa: E402
from common.icons import license_label  # noqa: E402
from common.version import LICENSE_LINE  # noqa: E402


class LicenceFilesTest(unittest.TestCase):
    def test_the_real_files_pass(self):
        self.assertEqual(licence.file_problems(ROOT), [])

    def test_line_endings_do_not_matter(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(ROOT, "LICENSE.md"), encoding="utf-8") as f:
                text = f.read()
            with open(os.path.join(tmp, "LICENSE.md"), "w", encoding="utf-8", newline="\r\n") as f:
                f.write(text)
            shutil.copy(os.path.join(ROOT, "THIRD_PARTY_NOTICES.md"), tmp)
            self.assertEqual(licence.file_problems(tmp), [])

    def test_changed_or_missing_files_are_caught(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(len(licence.file_problems(tmp)), 2)
            with open(os.path.join(ROOT, "LICENSE.md"), encoding="utf-8") as f:
                text = f.read()
            with open(os.path.join(tmp, "LICENSE.md"), "w", encoding="utf-8") as f:
                f.write(text.replace("No selling", "Selling"))
            with open(os.path.join(tmp, "THIRD_PARTY_NOTICES.md"), "w", encoding="utf-8") as f:
                f.write("# Notices\n")
            self.assertEqual(licence.file_problems(tmp), ["LICENSE.md has been changed",
                                                         "THIRD_PARTY_NOTICES.md has been changed"])

    def test_the_licence_names_the_same_credit_line(self):
        with open(os.path.join(ROOT, "LICENSE.md"), encoding="utf-8") as f:
            self.assertIn(licence.CREDIT_TEXT, f.read())


class CreditLineTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(sys.argv)

    def window(self, label):
        w = QWidget()
        QVBoxLayout(w).addWidget(label)
        return w

    def test_footer_passes(self):
        self.assertEqual(licence.window_problems(self.window(license_label())), [])

    def test_hidden_or_changed_footer_fails(self):
        hidden = license_label()
        w = self.window(hidden)
        hidden.hide()
        self.assertTrue(licence.window_problems(w))
        self.assertTrue(licence.window_problems(self.window(QLabel(LICENSE_LINE.replace("Utkarsh Tripathi", "X")))))

    def test_real_windows_carry_the_footer(self):
        from client.config import ClientConfig
        from client.ui.login import LoginWindow
        tmp = tempfile.mkdtemp()
        os.environ["APPDATA"] = os.path.join(tmp, "appdata")
        os.environ["LOCALAPPDATA"] = os.path.join(tmp, "local")
        try:
            self.assertEqual(licence.window_problems(LoginWindow(ClientConfig())), [])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
