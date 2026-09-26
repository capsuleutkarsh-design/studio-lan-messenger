"""Repeating calendar events: expand a rule into the occurrences that fall in a date range.

A rule is a dict (stored as JSON with the event):
    {"freq": "daily" | "workdays" | "weekly" | "monthly",
     "interval": 1,                 # every N days / weeks / months
     "days": [0, 2],                # weekly: Monday=0 ... Sunday=6 (default: the start's weekday)
     "until": "2026-12-31",         # optional last date (inclusive)
     "count": 10}                   # optional number of occurrences
"workdays" follows the studio's working week (Monday-Saturday unless told otherwise).
Monthly repeats on the same day of the month; a month without that day (31st) is skipped.
No Qt, no server: used by both, easy to test.
"""

import datetime

FREQS = ("daily", "workdays", "weekly", "monthly")
MAX_OCCURRENCES = 2000          # a safety net for rules without an end


def expand(start, end, rule, range_start, range_end, workdays=(0, 1, 2, 3, 4, 5), skip=()):
    """Occurrences [(start, end)] of an event (datetimes) that overlap [range_start, range_end).

    start/end: the first occurrence. rule: None for a single event. skip: occurrence starts to leave out
    (cancelled or moved ones - the caller adds moved ones itself)."""
    duration = end - start
    skip = set(skip)
    if not rule or rule.get("freq") not in FREQS:
        return [(start, end)] if start < range_end and end > range_start and start not in skip else []
    interval = max(1, int(rule.get("interval") or 1))
    until = rule.get("until")
    until = datetime.datetime.combine(datetime.date.fromisoformat(until), datetime.time.max) if until else None
    count = int(rule["count"]) if rule.get("count") else None
    out, n = [], 0
    for occ in _starts(start, rule, interval, workdays):
        if until and occ > until:
            break
        n += 1
        if count and n > count:
            break
        if occ >= range_end or n > MAX_OCCURRENCES:
            break
        if occ + duration > range_start and occ not in skip:
            out.append((occ, occ + duration))
    return out


def _starts(start, rule, interval, workdays):
    freq = rule["freq"]
    if freq == "daily":
        d = start
        while True:
            yield d
            d += datetime.timedelta(days=interval)
    elif freq == "workdays":
        d = start
        while True:
            if d.weekday() in workdays:
                yield d
            d += datetime.timedelta(days=1)
    elif freq == "weekly":
        days = sorted({int(x) for x in (rule.get("days") or [start.weekday()]) if 0 <= int(x) <= 6})
        week = start - datetime.timedelta(days=start.weekday())           # Monday of the first week
        while True:
            for wd in days:
                d = week + datetime.timedelta(days=wd)
                if d >= start:
                    yield d
            week += datetime.timedelta(weeks=interval)
    elif freq == "monthly":
        month = start.year * 12 + start.month - 1
        while True:
            y, m = divmod(month, 12)
            try:
                yield start.replace(year=y, month=m + 1)
            except ValueError:
                pass                                                    # no 31st in this month
            month += interval


def describe(rule, start):
    """'Every working day', 'Weekly on Mon, Wed', 'Every 2 weeks on Fri', 'Monthly on the 15th', 'Daily'."""
    if not rule or rule.get("freq") not in FREQS:
        return ""
    n = max(1, int(rule.get("interval") or 1))
    freq = rule["freq"]
    if freq == "workdays":
        text = "Every working day"
    elif freq == "daily":
        text = "Daily" if n == 1 else f"Every {n} days"
    elif freq == "weekly":
        names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        days = ", ".join(names[int(d)] for d in sorted(rule.get("days") or [start.weekday()]))
        text = f"Weekly on {days}" if n == 1 else f"Every {n} weeks on {days}"
    else:
        day = start.day
        suffix = "th" if 11 <= day <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")
        text = f"Monthly on the {day}{suffix}" if n == 1 else f"Every {n} months on the {day}{suffix}"
    if rule.get("until"):
        text += f", until {datetime.date.fromisoformat(rule['until']):%d %b %Y}"
    elif rule.get("count"):
        text += f", {rule['count']} times"
    return text


def clean(rule):
    """A rule from a client, checked (None = no repeat). Raises ValueError for nonsense."""
    if not rule:
        return None
    if not isinstance(rule, dict) or rule.get("freq") not in FREQS:
        raise ValueError("Unknown repeat")
    out = {"freq": rule["freq"], "interval": max(1, min(int(rule.get("interval") or 1), 52))}
    if rule["freq"] == "weekly":
        days = sorted({int(d) for d in rule.get("days") or [] if 0 <= int(d) <= 6})
        if days:
            out["days"] = days
    if rule.get("until"):
        out["until"] = datetime.date.fromisoformat(str(rule["until"])[:10]).isoformat()
    elif rule.get("count"):
        out["count"] = max(1, min(int(rule["count"]), 1000))
    return out
