"""Understand typed times for reminders and scheduled messages: "mon 9:30", "tomorrow 2pm", "in 2h", "28 sep 10:00".

No Qt here, so it is easy to test. parse_when() returns a Unix timestamp in the future, or None.
"""

import datetime
import re

DAYS = {"mon": 0, "monday": 0, "tue": 1, "tues": 1, "tuesday": 1, "wed": 2, "weds": 2, "wednesday": 2,
        "thu": 3, "thur": 3, "thurs": 3, "thursday": 3, "fri": 4, "friday": 4, "sat": 5, "saturday": 5,
        "sun": 6, "sunday": 6}
MONTHS = {m: i for i, m in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov",
                                      "dec"), 1)}
UNITS = {"s": 1, "sec": 1, "secs": 1, "second": 1, "seconds": 1, "m": 60, "min": 60, "mins": 60, "minute": 60, "minutes": 60, "h": 3600, "hr": 3600, "hrs": 3600,
         "hour": 3600, "hours": 3600, "d": 86400, "day": 86400, "days": 86400, "w": 604800, "week": 604800,
         "weeks": 604800}
DEFAULT_TIME = (9, 0)            # a day without a time means the start of the working day

_TIME = re.compile(r"\b(?:at\s+)?(\d{1,2})(?:[:.](\d{2}))?(?::(\d{2}))?\s*(am|pm)?\b"
                   r"|\b(noon|midday|evening|morning|tonight)\b")
_IN = re.compile(r"^in\s+(\d+(?:\.\d+)?)\s*([a-z]+)$")
_DATE_DM = re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+([a-z]{3})[a-z]*(?:\s+(\d{4}))?\b")        # 28 sep [2026]
_DATE_MD = re.compile(r"\b([a-z]{3})[a-z]*\s+(\d{1,2})(?:st|nd|rd|th)?(?:\s+(\d{4}))?\b")        # sep 28 [2026]
_DATE_NUM = re.compile(r"\b(\d{1,2})[/.-](\d{1,2})(?:[/.-](\d{2,4}))?\b")                        # 28/09[/2026]
_WORDS = {"noon": (12, 0), "midday": (12, 0), "morning": (9, 0), "evening": (18, 0), "tonight": (20, 0)}


def parse_when(text, now=None):
    """Timestamp for a typed time, or None if it can't be understood (or is not in the future)."""
    now = now or datetime.datetime.now()
    t = " ".join(str(text or "").lower().replace(",", " ").split())
    if not t:
        return None
    m = _IN.match(t)
    if m:
        unit = UNITS.get(m.group(2))
        return (now + datetime.timedelta(seconds=float(m.group(1)) * unit)).timestamp() if unit else None

    day, rest, explicit_day, calendar = None, t, False, False
    if "today" in rest:
        day, rest, explicit_day, calendar = now.date(), rest.replace("today", " "), True, True
    elif "tomorrow" in rest or "tmrw" in rest:
        day = now.date() + datetime.timedelta(days=1)
        rest, explicit_day = rest.replace("tomorrow", " ").replace("tmrw", " "), True
    else:
        m = _DATE_DM.search(rest) or _DATE_MD.search(rest)
        if m and m.re is _DATE_DM and m.group(2) in MONTHS:
            day = _date(int(m.group(3) or 0), MONTHS[m.group(2)], int(m.group(1)), now)
            rest, explicit_day, calendar = rest.replace(m.group(0), " "), True, True
        elif m and m.re is _DATE_MD and m.group(1) in MONTHS:
            day = _date(int(m.group(3) or 0), MONTHS[m.group(1)], int(m.group(2)), now)
            rest, explicit_day, calendar = rest.replace(m.group(0), " "), True, True
        else:
            m = _DATE_NUM.search(rest)
            if m and not re.search(r"\d[:.]\d{2}\s*$", m.group(0)):   # "9.30" is a time, not 9 March
                year = int(m.group(3) or 0)
                day = _date(year + 2000 if 0 < year < 100 else year, int(m.group(2)), int(m.group(1)), now)
                rest, explicit_day, calendar = rest.replace(m.group(0), " "), True, True
        if not explicit_day:
            for word in re.findall(r"[a-z]+", rest):
                if word in DAYS:
                    ahead = (DAYS[word] - now.weekday()) % 7
                    if not ahead and re.search(rf"\bnext\s+{word}\b", rest):
                        ahead = 7                # "next monday" said on a Monday
                    day = now.date() + datetime.timedelta(days=ahead)
                    rest, explicit_day = re.sub(rf"\b(next\s+)?{word}\b", " ", rest), True
                    break
    if explicit_day and day is None:
        return None                          # "31 feb" and similar

    hm = None
    m = _TIME.search(rest)
    if m:
        if m.group(5):
            hm = _WORDS[m.group(5)]
        else:
            h, mi, sec, ampm = int(m.group(1)), int(m.group(2) or 0), int(m.group(3) or 0), m.group(4)
            if ampm == "pm" and h < 12:
                h += 12
            elif ampm == "am" and h == 12:
                h = 0
            if h > 23 or mi > 59 or sec > 59:
                return None
            hm = (h, mi, sec)
        rest = rest.replace(m.group(0), " ")
    if re.sub(r"\b(at|on|next|this)\b", " ", rest).strip():
        return None                          # words we did not understand: better to say so than guess
    if day is None and hm is None:
        return None
    if day is None:                          # only a time: today, or tomorrow if that time has passed
        day = now.date()
        when = datetime.datetime.combine(day, datetime.time(*hm))
        if when <= now:
            when += datetime.timedelta(days=1)
        return when.timestamp()
    when = datetime.datetime.combine(day, datetime.time(*(hm or DEFAULT_TIME)))
    if when <= now and day.weekday() == now.weekday() and "next" not in t and not calendar:
        when += datetime.timedelta(days=7)   # "mon 9:30" said on a Monday at 10:00 means next Monday
    return when.timestamp() if when > now else None


def _date(year, month, day, now):
    """A calendar date; without a year, the next time that date comes round."""
    try:
        d = datetime.date(year or now.year, month, day)
    except ValueError:
        return None
    if not year and d < now.date():
        try:
            d = datetime.date(now.year + 1, month, day)
        except ValueError:
            return None
    return d
