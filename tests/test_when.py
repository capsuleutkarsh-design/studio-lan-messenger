"""Typed times for reminders and scheduled messages (client/when.py)."""
import datetime
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from client.when import parse_when  # noqa: E402

NOW = datetime.datetime(2026, 9, 26, 14, 30)        # a Saturday afternoon


def at(text):
    ts = parse_when(text, NOW)
    return datetime.datetime.fromtimestamp(ts) if ts else None


class WhenTest(unittest.TestCase):
    def test_weekdays(self):
        self.assertEqual(at("mon 9:30"), datetime.datetime(2026, 9, 28, 9, 30))
        self.assertEqual(at("Monday 9.30am"), datetime.datetime(2026, 9, 28, 9, 30))
        self.assertEqual(at("next friday 5pm"), datetime.datetime(2026, 10, 2, 17, 0))
        self.assertEqual(at("wed"), datetime.datetime(2026, 9, 30, 9, 0))           # no time: 09:00
        self.assertEqual(at("sat 10:00"), datetime.datetime(2026, 10, 3, 10, 0))    # passed today: next week
        self.assertEqual(at("sat 18:00"), datetime.datetime(2026, 9, 26, 18, 0))    # later today
        self.assertEqual(at("next sat 18:00"), datetime.datetime(2026, 10, 3, 18, 0))

    def test_relative(self):
        self.assertEqual(at("in 2h"), NOW + datetime.timedelta(hours=2))
        self.assertEqual(at("in 20 minutes"), NOW + datetime.timedelta(minutes=20))
        self.assertEqual(at("in 3 days"), NOW + datetime.timedelta(days=3))
        self.assertEqual(at("tomorrow 2pm"), datetime.datetime(2026, 9, 27, 14, 0))
        self.assertEqual(at("tomorrow"), datetime.datetime(2026, 9, 27, 9, 0))
        self.assertEqual(at("today 18:00"), datetime.datetime(2026, 9, 26, 18, 0))
        self.assertEqual(at("tonight"), datetime.datetime(2026, 9, 26, 20, 0))

    def test_dates_and_times(self):
        self.assertEqual(at("28 sep 10:00"), datetime.datetime(2026, 9, 28, 10, 0))
        self.assertEqual(at("oct 2 at 11am"), datetime.datetime(2026, 10, 2, 11, 0))
        self.assertEqual(at("5/10 16:15"), datetime.datetime(2026, 10, 5, 16, 15))     # day/month
        self.assertEqual(at("1 jan"), datetime.datetime(2027, 1, 1, 9, 0))           # next time it comes round
        self.assertEqual(at("16:00"), datetime.datetime(2026, 9, 26, 16, 0))
        self.assertEqual(at("9:00"), datetime.datetime(2026, 9, 27, 9, 0))           # passed: tomorrow
        self.assertEqual(at("12am"), datetime.datetime(2026, 9, 27, 0, 0))

    def test_rejects_what_it_does_not_understand(self):
        for text in ("", "someday", "mon 25:00", "31 feb", "today 9:00", "in 2 fortnights", "banana 9:00"):
            self.assertIsNone(at(text), text)


if __name__ == "__main__":
    unittest.main()
