"""@mentions for people and groups (client/mentions.py) and the screenshot picker (client/ui/snip.py)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from client import mentions as M  # noqa: E402

ME = {"id": 1, "username": "ann", "name": "Ann Rao", "department": "Compositing", "section": "Roto / Paint"}
BEN = {"id": 2, "username": "ben.k", "name": "Ben Kapoor", "department": "Compositing", "section": ""}
CAT = {"id": 3, "username": "cat", "name": "Cat Iyer", "department": "Lighting", "section": ""}
DEV = {"id": 4, "username": "dev", "name": "Dev Shah", "department": "Lighting", "section": ""}


class MentionTest(unittest.TestCase):
    def test_who_is_mentioned(self):
        self.assertTrue(M.mentions("hey @ann can you check?", ME))
        self.assertTrue(M.mentions("@ANN.", ME))
        self.assertFalse(M.mentions("hey @anne", ME))
        self.assertFalse(M.mentions("mail ann@studio.com", ME))
        self.assertTrue(M.mentions("@everyone dailies at 5", ME))
        self.assertTrue(M.mentions("@here anyone free?", ME))
        self.assertFalse(M.mentions("@here anyone free?", ME, online=False))
        self.assertTrue(M.mentions("@Compositing please update", ME))
        self.assertTrue(M.mentions("@Roto-Paint new plates", ME))
        self.assertFalse(M.mentions("@Lighting render farm is full", ME))

    def test_suggestions(self):
        room = [ME, BEN, CAT, DEV]
        tokens = [t for t, _label, _kind in M.suggestions("", room, 1)]
        self.assertEqual(tokens[:2], ["everyone", "here"])
        self.assertIn("Compositing", tokens)                     # a team with 2+ people in the room
        self.assertIn("Lighting", tokens)
        self.assertNotIn("Roto-Paint", tokens)                   # only one person: not a group
        self.assertNotIn("ann", tokens)                          # not myself
        self.assertEqual([t for t, *_ in M.suggestions("ben", room, 1)], ["ben.k"])
        self.assertEqual([t for t, *_ in M.suggestions("kap", room, 1)], ["ben.k"])   # by surname
        self.assertEqual(M.suggestions("ev", room, 1)[0][0], "everyone")
        self.assertIn("Lighting", [t for t, *_ in M.suggestions("light", room, 1)])

    def test_highlight(self):
        html = M.mark("hi @ben.k and @ann, @nobody", {"ben.k", "ann"}, {"ann"}, "#0f0", "#010")
        self.assertIn('font-weight:600">@ben.k</span>', html)
        self.assertIn("background:#010", html)                  # me: a chip
        self.assertIn("@nobody", html)
        self.assertNotIn(">@nobody<", html)
        self.assertEqual(M.group_token("Roto / Paint"), "Roto-Paint")


class SnipTest(unittest.TestCase):
    def test_pick_area_and_whole_screen(self):
        from PySide6.QtCore import QPointF, Qt
        from PySide6.QtGui import QColor, QGuiApplication, QMouseEvent, QPixmap
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance() or QApplication([])
        from client.ui.snip import _Overlay
        shot = QPixmap(800, 600)
        shot.fill(QColor("#336699"))
        o = _Overlay(QGuiApplication.primaryScreen(), shot)
        o.resize(800, 600)
        got = []
        o.picked.connect(got.append)

        def mouse(kind, x, y):
            ev = QMouseEvent(kind, QPointF(x, y), QPointF(x, y), Qt.LeftButton, Qt.LeftButton, Qt.NoModifier)
            getattr(o, {QMouseEvent.MouseButtonPress: "mousePressEvent", QMouseEvent.MouseMove: "mouseMoveEvent",
                        QMouseEvent.MouseButtonRelease: "mouseReleaseEvent"}[kind])(ev)
        mouse(QMouseEvent.MouseButtonPress, 100, 50)
        mouse(QMouseEvent.MouseMove, 300, 200)
        mouse(QMouseEvent.MouseButtonRelease, 300, 200)
        self.assertEqual((got[0].width(), got[0].height()), (200, 150))
        mouse(QMouseEvent.MouseButtonPress, 10, 10)                  # a click: the whole screen
        mouse(QMouseEvent.MouseButtonRelease, 11, 11)
        self.assertEqual((got[1].width(), got[1].height()), (800, 600))
        o.close()
        app.processEvents()


if __name__ == "__main__":
    unittest.main()
