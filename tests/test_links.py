"""Shot & path links in messages, and user names shown safely (client/ui/widgets.py)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication([])
from client.ui import widgets as W  # noqa: E402


def found(text):
    return [m.group("quoted") or m.group("url") or m.group("path") for m in W._LINK_RE.finditer(text)]


class LinkTest(unittest.TestCase):
    def test_paths_are_found(self):
        self.assertEqual(found(r"check \\fs01\proj\FAL\FAL_030\comp\v012 please"),
                         [r"\\fs01\proj\FAL\FAL_030\comp\v012"])
        self.assertEqual(found("nuke: //fs01/proj/FAL/FAL_030.####.exr ok"), ["//fs01/proj/FAL/FAL_030.####.exr"])
        self.assertEqual(found(r"Z:/plates/FAL_030.%04d.dpx and Z:\proj\x.nk"),
                         ["Z:/plates/FAL_030.%04d.dpx", r"Z:\proj\x.nk"])
        self.assertEqual(found(r'spaces "\\fs01\My Project\shot 01" done'), [r"\\fs01\My Project\shot 01"])
        self.assertEqual(found("web https://example.com/a//b"), ["https://example.com/a//b"])
        self.assertEqual(found("ratio 16:9, a/b, v2:final"), [])

    def test_link_never_runs_a_program(self):
        html = W.linkify(r"run \\fs01\tools\setup.exe")
        self.assertIn(W.PATH_SCHEME, html)                 # opens the path menu, not the file
        self.assertNotIn("file:", html)
        self.assertIn(".exe", W._RUNNABLE | {".exe"})

    def test_sequences_open_their_folder(self):
        self.assertEqual(W.path_target("//fs01/proj/FAL/FAL_030.####.exr"), (r"\\fs01\proj\FAL", True))
        self.assertEqual(W.path_target("Z:/proj/x.%04d.dpx"), (r"Z:\proj", True))
        self.assertEqual(W.path_target(r"Z:\proj\x.nk"), (r"Z:\proj\x.nk", False))
        self.assertEqual(W.windows_path("Z:/proj/"), r"Z:\proj")

    def test_names_shown_safely(self):
        self.assertEqual(W.rich_safe('<img src="x">\nhi'), "<qt>&lt;img src=\"x\"&gt;<br>hi</qt>")
        self.assertEqual(W.first_name("  "), "them")
        self.assertEqual(W.first_name("Priya Nair"), "Priya")


if __name__ == "__main__":
    unittest.main()
