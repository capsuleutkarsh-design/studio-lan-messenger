"""Calendar building blocks: repeating events (common/recur.py) and India's holidays (common/india_holidays.py)."""
import datetime as dt
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from common import india_holidays as H  # noqa: E402
from common import recur  # noqa: E402

D = dt.datetime


def starts(rule, start, days=30, **kw):
    s = D(*start)
    return [o[0] for o in recur.expand(s, s + dt.timedelta(hours=1), rule, s, s + dt.timedelta(days=days), **kw)]


class RecurTest(unittest.TestCase):
    def test_single_and_daily(self):
        self.assertEqual(len(starts(None, (2026, 9, 28, 17))), 1)
        self.assertEqual(len(starts({"freq": "daily"}, (2026, 9, 28, 17), days=10)), 10)
        self.assertEqual(len(starts({"freq": "daily", "interval": 2}, (2026, 9, 28, 17), days=10)), 5)

    def test_working_days_mon_to_sat(self):
        got = starts({"freq": "workdays"}, (2026, 9, 28, 17), days=14)     # a Monday
        self.assertEqual(len(got), 12)                                      # no Sundays
        self.assertTrue(all(d.weekday() != 6 for d in got))

    def test_weekly_days_until_count(self):
        got = starts({"freq": "weekly", "days": [0, 2, 4]}, (2026, 9, 28, 10), days=14)
        self.assertEqual([d.day for d in got], [28, 30, 2, 5, 7, 9])
        got = starts({"freq": "weekly", "days": [4], "interval": 2}, (2026, 9, 28, 10), days=61)
        self.assertEqual([d.strftime("%m-%d") for d in got], ["10-02", "10-16", "10-30", "11-13", "11-27"])
        self.assertEqual(len(starts({"freq": "daily", "until": "2026-10-02"}, (2026, 9, 28, 9))), 5)
        self.assertEqual(len(starts({"freq": "daily", "count": 3}, (2026, 9, 28, 9))), 3)

    def test_monthly_skips_short_months(self):
        got = starts({"freq": "monthly"}, (2026, 1, 31, 9), days=365)
        self.assertEqual([d.month for d in got], [1, 3, 5, 7, 8, 10, 12])

    def test_skip_and_range(self):
        s = D(2026, 9, 28, 17)
        occ = recur.expand(s, s + dt.timedelta(hours=1), {"freq": "daily"}, D(2026, 10, 1), D(2026, 10, 3),
                           skip=[D(2026, 10, 1, 17)])
        self.assertEqual([o[0].day for o in occ], [2])

    def test_describe_and_clean(self):
        s = D(2026, 9, 28, 17)
        self.assertEqual(recur.describe({"freq": "workdays"}, s), "Every working day")
        self.assertEqual(recur.describe({"freq": "weekly", "days": [0, 2]}, s), "Weekly on Mon, Wed")
        self.assertEqual(recur.describe({"freq": "monthly", "until": "2026-12-31"}, s),
                         "Monthly on the 28th, until 31 Dec 2026")
        self.assertIsNone(recur.clean(None))
        with self.assertRaises(ValueError):
            recur.clean({"freq": "hourly"})
        self.assertEqual(recur.clean({"freq": "weekly", "days": [9, 1, 1], "interval": 99}),
                         {"freq": "weekly", "interval": 52, "days": [1]})


class HolidayTest(unittest.TestCase):
    KNOWN = {  # published dates: the calculation must reproduce them
        "2024-03-25": "Holi", "2024-10-31": "Diwali (Lakshmi Puja)", "2024-10-12": "Dussehra",
        "2024-08-26": "Janmashtami", "2024-09-07": "Ganesh Chaturthi", "2024-08-19": "Raksha Bandhan",
        "2025-03-14": "Holi", "2025-10-20": "Diwali (Lakshmi Puja)", "2025-10-02": "Dussehra",
        "2025-08-16": "Janmashtami", "2025-08-27": "Ganesh Chaturthi", "2025-02-26": "Maha Shivaratri",
        "2026-11-08": "Diwali (Lakshmi Puja)", "2026-10-20": "Dussehra", "2026-09-14": "Ganesh Chaturthi",
        "2026-08-28": "Raksha Bandhan", "2026-02-15": "Maha Shivaratri", "2026-03-19": "Gudi Padwa / Ugadi",
        "2026-05-27": "Eid al-Adha (Bakrid)", "2026-03-21": "Eid al-Fitr", "2026-04-03": "Good Friday",
        "2026-01-14": "Makar Sankranti / Pongal",
    }

    def test_known_dates(self):
        for date, name in self.KNOWN.items():
            got = {h["name"]: h["date"] for h in H.holidays(int(date[:4]))}
            self.assertEqual(got.get(name), date, name)

    def test_every_year_to_2035(self):
        for year in range(2026, 2036):
            hs = H.holidays(year)
            names = [h["name"] for h in hs]
            for must in ("Republic Day", "Independence Day", "Gandhi Jayanti", "Diwali (Lakshmi Puja)", "Holi",
                         "Good Friday", "Christmas", "Dussehra"):
                self.assertIn(must, names, (year, must))
            self.assertTrue(all(h["date"].startswith(str(year)) for h in hs))
            national = {h["name"] for h in hs if h["kind"] == "national"}
            self.assertEqual(national, {"Republic Day", "Independence Day", "Gandhi Jayanti"})
            self.assertFalse(any(h["confirm"] for h in hs if h["kind"] == "national"))
            diwali = dt.date.fromisoformat(next(h["date"] for h in hs if h["name"].startswith("Diwali")))
            self.assertTrue(dt.date(year, 10, 15) <= diwali <= dt.date(year, 11, 15), year)


if __name__ == "__main__":
    unittest.main()
