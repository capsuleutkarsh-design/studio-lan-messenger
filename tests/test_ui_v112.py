"""1.12.0 UI groundwork: shared text/date helpers, readable colours, the type scale, ElidedLabel, round menus,
outline icons."""

import datetime
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QHBoxLayout, QMenu, QWidget  # noqa: E402

from common import fmt  # noqa: E402
from common import theme as T  # noqa: E402

NOW = datetime.datetime(2026, 9, 29, 15, 0)          # a Tuesday


class TextTest(unittest.TestCase):
    def test_clip(self):
        self.assertEqual(fmt.clip("short", 10), "short")
        self.assertEqual(fmt.clip("Screening room is booked for the client review", 20), "Screening room is…")
        self.assertLessEqual(len(fmt.clip("Screening room is booked for the client review", 20)), 20)
        self.assertEqual(fmt.clip("abcdefghijklmnopqrstuvwxyz", 10), "abcdefghi…")     # no space: hard cut
        self.assertEqual(fmt.clip("one\ntwo   three", 50), "one two three")
        self.assertEqual(fmt.clip("one\ntwo", 50, one_line=False), "one\ntwo")
        self.assertEqual(fmt.clip("Done, then more", 8), "Done…")         # no dangling comma
        self.assertEqual(fmt.clip("", 5), "")
        self.assertEqual(fmt.clip(None, 5), "")
        self.assertEqual(fmt.clip("abc", 1), "…")
        self.assertTrue(fmt.clip("x" * 100, 30).endswith(fmt.ELLIPSIS))
        self.assertNotIn("...", fmt.clip("word " * 40, 30))

    def test_menu_text(self):
        self.assertEqual(fmt.menu_text("Photo & status"), "Photo && status")
        self.assertEqual(fmt.menu_text("R&D"), "R&&D")
        self.assertEqual(fmt.menu_text(None), "")


class DateTest(unittest.TestCase):
    def test_dates_have_no_zero_padding(self):
        d = datetime.date(2026, 10, 6)
        self.assertEqual(fmt.fmt_date(d, now=NOW), "Tue 6 Oct")
        self.assertEqual(fmt.fmt_date(d, weekday=False, now=NOW), "6 Oct")
        self.assertEqual(fmt.fmt_date(d, long=True, now=NOW), "Tuesday 6 October")
        self.assertEqual(fmt.fmt_date(datetime.date(2025, 9, 29), now=NOW), "Mon 29 Sep 2025")
        self.assertEqual(fmt.fmt_date(d, year=True, now=NOW), "Tue 6 Oct 2026")
        ts = datetime.datetime(2026, 10, 6, 9, 5).timestamp()
        self.assertEqual(fmt.fmt_date(ts, now=NOW), "Tue 6 Oct")

    def test_times(self):
        self.assertEqual(fmt.fmt_time(datetime.datetime(2026, 9, 29, 9, 5)), "09:05")
        self.assertEqual(fmt.fmt_when(datetime.datetime(2026, 9, 29, 16, 0), now=NOW), "Today 16:00")
        self.assertEqual(fmt.fmt_when(datetime.datetime(2026, 9, 28, 18, 0), now=NOW), "Yesterday 18:00")
        self.assertEqual(fmt.fmt_when(datetime.datetime(2026, 9, 30, 9, 30), now=NOW), "Tomorrow 09:30")
        self.assertEqual(fmt.fmt_when(datetime.datetime(2026, 9, 24, 18, 0), now=NOW), "Thu 24 Sep, 18:00")
        self.assertEqual(fmt.fmt_when(datetime.datetime(2025, 9, 24, 18, 0), now=NOW), "24 Sep 2025, 18:00")
        self.assertEqual(fmt.fmt_when(datetime.datetime(2026, 9, 29, 16, 0), now=NOW, relative=False),
                         "Tue 29 Sep, 16:00")
        self.assertEqual(fmt.day_word(datetime.date(2026, 9, 30), now=NOW), "Tomorrow")
        self.assertIsNone(fmt.day_word(datetime.date(2026, 10, 3), now=NOW))

    def test_ranges(self):
        a, b = datetime.date(2026, 9, 28), datetime.date(2026, 10, 4)
        self.assertEqual(fmt.fmt_range(a, b, now=NOW), "28 Sep – 4 Oct")
        self.assertEqual(fmt.fmt_range(a, datetime.date(2026, 9, 30), now=NOW), "28–30 Sep")
        self.assertEqual(fmt.fmt_range(a, a, now=NOW), "28 Sep")
        self.assertEqual(fmt.fmt_range(a, b, year=True, now=NOW), "28 Sep – 4 Oct 2026")
        self.assertEqual(fmt.fmt_range(a, b, weekday=True, now=NOW), "Mon 28 Sep – Sun 4 Oct")
        self.assertEqual(fmt.fmt_range(datetime.date(2026, 12, 30), datetime.date(2027, 1, 2), now=NOW),
                         "30 Dec 2026 – 2 Jan 2027")
        self.assertEqual(fmt.fmt_time_range(datetime.datetime(2026, 9, 29, 16), datetime.datetime(2026, 9, 29, 17, 30)),
                         "16:00 – 17:30")

    def test_client_helpers_use_the_same_style(self):
        from client.ui.widgets import fmt_day, fmt_list_time
        old = datetime.datetime(2025, 3, 4, 10, 0).timestamp()
        self.assertEqual(fmt_list_time(old), "4 Mar 2025")
        self.assertEqual(fmt_day(old), "Tuesday 4 March 2025")
        self.assertEqual(fmt_day(datetime.datetime.now().timestamp()), "Today")


class ColourTest(unittest.TestCase):
    def tearDown(self):
        T.apply()

    def test_meta_text_is_readable_everywhere(self):
        for theme in ("midnight", "light", "classic", "independence", "republic", "christmas"):
            for accent in ("quillo", "violet", "lime", "orange"):
                T.apply(theme, accent)
                for bg in (T.PANEL, T.BG, T.SURFACE):
                    self.assertGreaterEqual(T.contrast(T.META, bg), 4.5, (theme, accent, bg))
                    self.assertGreaterEqual(T.contrast(T.MUTED, bg), 4.5, (theme, accent, bg))
                self.assertGreaterEqual(T.contrast(T.STATUS_COLORS["offline"], T.PANEL), 3, theme)
                if not T.DARK:
                    self.assertGreaterEqual(T.contrast(T.ACCENT, T.BG), 4.5, (theme, accent))
                    self.assertGreaterEqual(T.contrast(T.CONTROL_EDGE, T.SURFACE), 3, (theme, accent))

    def test_faint_is_unchanged(self):
        """FAINT colours the licence line, which must stay as it is."""
        T.apply("light", "quillo")
        self.assertEqual(T.FAINT, "#8b93a5")
        T.apply("midnight", "quillo")
        self.assertEqual(T.FAINT, "#67739a")

    def test_selected_row_differs_from_hover_on_light(self):
        T.apply("light", "quillo")
        self.assertGreater(T.contrast(T.ACCENT_SOFT, T.SURFACE), 1.1)

    def test_avatar_colours(self):
        self.assertEqual(len(T.AVATAR_COLORS), 12)
        self.assertEqual(len(set(T.AVATAR_COLORS)), 12)
        for c in T.AVATAR_COLORS:
            self.assertGreaterEqual(T.contrast("#ffffff", c), 3.3, c)

    def test_name_colours_and_readable_on(self):
        for theme in ("midnight", "light"):
            T.apply(theme, "quillo")
            for key in ("Priya Sharma", "Rohan Desai", "AK74 Project", "x"):
                c = T.name_color(key)
                self.assertGreaterEqual(T.contrast(c, T.PANEL), 4.5, (theme, key))
                self.assertGreaterEqual(T.contrast(c, T.BUBBLE_OTHER), 4.5, (theme, key))
        self.assertEqual(T.readable_on("#000000", "#ffffff"), "#000000")      # already fine: unchanged
        self.assertGreaterEqual(T.contrast(T.readable_on("#f2a73b", "#ffffff"), "#ffffff"), 4.5)
        self.assertGreaterEqual(T.contrast(T.readable_on("#303030", "#101010", 3), "#101010"), 3)
        self.assertEqual(T.ink_on("#ffffff"), "#10131a")
        self.assertEqual(T.ink_on("#10131a"), "#ffffff")
        self.assertAlmostEqual(T.contrast("#000000", "#ffffff"), 21, places=3)

    def test_badges(self):
        T.apply("midnight", "quillo")
        self.assertEqual(T.badge_colors("alert"), (T.DANGER, "#ffffff"))
        self.assertEqual(T.badge_colors("neutral"), (T.ACCENT, T.ACCENT_TEXT))
        self.assertEqual(T.badge_colors(), T.badge_colors("neutral"))
        self.assertEqual(T.badge_colors("muted")[0], T.SURFACE_HOVER)

    def test_scale(self):
        self.assertEqual(T.pt(T.FONT_S), "9pt")
        self.assertEqual(T.pt(T.FONT_BODY), "10.5pt")
        self.assertEqual(T.pt(10, zoom=1.2), "12pt")
        self.assertEqual(T.px(T.FONT_M), 13)
        self.assertEqual(T.px(T.FONT_S), 12)
        self.assertTrue(T.FONT_XS < T.FONT_S < T.FONT_M < T.FONT_BODY < T.FONT_L < T.FONT_XL < T.FONT_XXL)
        self.assertTrue(T.RADIUS_S < T.RADIUS_M < T.RADIUS_CONTROL < T.RADIUS_L < T.RADIUS_XL)

    def test_stylesheet_uses_the_new_edges(self):
        T.apply("light", "quillo")
        self.assertIn(f"QMenu {{ background: {T.PANEL}; border: 1px solid {T.FLOAT_BORDER}", T.STYLESHEET)
        self.assertIn(f"border: 1px solid {T.CONTROL_EDGE}", T.STYLESHEET)
        self.assertIn("QPushButton:checked", T.STYLESHEET)


class WidgetTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(sys.argv)

    def tearDown(self):
        T.apply()

    def test_elided_label(self):
        from client.ui.widgets import ElidedLabel
        host = QWidget()
        lay = QHBoxLayout(host)
        lay.setContentsMargins(0, 0, 0, 0)
        text = "Compositing Supervisor · Compositing · Reports to Rajiv Menon"
        lbl = ElidedLabel(text)
        lay.addWidget(lbl, 1)
        host.resize(900, 30)
        host.show()
        self.app.processEvents()
        self.assertEqual(lbl.text(), text)
        self.assertFalse(lbl.is_elided())
        self.assertEqual(lbl.toolTip(), "")
        host.resize(120, 30)
        self.app.processEvents()
        self.assertTrue(lbl.is_elided())
        self.assertTrue(lbl.shown_text().endswith("…"))
        self.assertEqual(lbl.text(), text)                    # the whole text is kept
        self.assertIn("Reports to Rajiv Menon", lbl.toolTip())
        self.assertLess(lbl.minimumSizeHint().width(), 60)    # it can shrink ...
        self.assertGreater(lbl.sizeHint().width(), 200)       # ... but asks for the whole text
        lbl.setToolTip("Own tip")
        self.assertEqual(lbl.toolTip(), "Own tip")
        lbl.setToolTip("")
        lbl.setText("<b>Bob</b>")                             # plain text, never rich text
        self.assertEqual(lbl.textFormat(), Qt.PlainText)
        host.resize(900, 30)
        self.app.processEvents()
        self.assertEqual(lbl.shown_text(), "<b>Bob</b>")
        mid = ElidedLabel(r"\\nas01\ak74\shots\AK74_0450\comp\v014\AK74_0450_comp_v014.nk", mode=Qt.ElideMiddle)
        mid.resize(160, 20)
        mid._refresh()
        self.assertIn("…", mid.shown_text())
        self.assertTrue(mid.shown_text().endswith(".nk"))
        host.deleteLater()

    def test_rail_button_badges_and_compact(self):
        from client.ui.widgets import RailButton
        b = RailButton("chat", "Chats", "Chats")
        self.assertEqual(b.height(), RailButton.H)
        b.set_compact(True)
        self.assertEqual(b.height(), RailButton.H_COMPACT)
        b.set_compact(False)
        self.assertEqual(b.height(), RailButton.H)
        b.set_badge(3, kind="neutral")
        self.assertEqual((b.badge, b.badge_kind), (3, "neutral"))
        b.set_badge(2)
        self.assertEqual(b.badge_kind, "alert")
        b.resize(b.size())
        b.grab()                                              # paints without errors

    def test_icon_button_hover_colours(self):
        from client.ui.widgets import IconButton
        b = IconButton("close", hover_bg="rgba(255,255,255,0.10)")
        self.assertIn("rgba(255,255,255,0.10)", b.styleSheet())

    def test_popup_pos_stays_on_screen(self):
        from PySide6.QtCore import QSize
        from client.ui.widgets import popup_pos
        host = QWidget()
        host.resize(400, 300)
        host.move(0, 0)
        host.show()
        self.app.processEvents()
        area = host.screen().availableGeometry()
        pos = popup_pos(host, QSize(200, 100), above=True)            # no room above the window: below it
        self.assertGreaterEqual(pos.y(), area.top())
        self.assertGreaterEqual(pos.x(), area.left())
        pos = popup_pos(host, QSize(200, 100), align_right=True)
        self.assertEqual(pos.x() + 200, host.mapToGlobal(QPoint(0, 0)).x() + 400)
        host.deleteLater()

    def test_ui_font(self):
        from client.ui.widgets import ui_font
        f = ui_font(T.FONT_S, bold=True)
        self.assertEqual(f.pixelSize(), 12)
        self.assertTrue(f.bold())

    def test_menus_get_round_corners(self):
        T.apply("light", "quillo")                            # an app exists: installs the round-menu style
        self.app.setStyleSheet(T.STYLESHEET)
        m = QMenu()
        m.addAction(fmt.menu_text("Photo & status"))
        m.addAction("Mute")
        m.popup(QPoint(20, 20))
        self.app.processEvents()
        self.assertTrue(m.testAttribute(Qt.WA_TranslucentBackground))
        self.assertTrue(m.windowFlags() & Qt.NoDropShadowWindowHint)
        img = m.grab().toImage()
        self.assertEqual(img.pixelColor(0, 0).alpha(), 0)     # the corner outside the curve is see-through
        mid = img.pixelColor(img.width() // 2, img.height() // 2)
        self.assertEqual(mid.alpha(), 255)
        m.hide()
        custom = QWidget(None, Qt.Popup)
        custom.setProperty("roundPopup", True)
        custom.setStyleSheet("background: white; border-radius: 14px;")
        custom.ensurePolished()
        self.assertTrue(custom.testAttribute(Qt.WA_TranslucentBackground))
        T.round_popups(self.app)                              # repeat calls are harmless
        self.app.setStyleSheet("")


class IconTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(sys.argv)

    def test_outline_family_covers_the_old_files(self):
        from common import icons
        for name in ("search", "attachment", "more_options", "send", "settings", "emoticons", "close"):
            self.assertIn(name, icons._OUTLINE)
        for name in ("screenshot", "copy", "recent", "time", "check_all", "bookmark", "plus", "bell_off",
                     "on_top", "calendar", "event", "sun", "palm", "cake", "gift", "note", "hdd", "moon",
                     "attach", "more", "gear", "history"):
            self.assertIn(name, icons.names())
            self.assertFalse(icons.pixmap(name, "#ff0000", 16).toImage().isNull())
        a = icons.pixmap("attach", "#ff0000", 16).toImage()
        b = icons.pixmap("attachment", "#ff0000", 16).toImage()
        self.assertEqual(a, b)


if __name__ == "__main__":
    unittest.main()
