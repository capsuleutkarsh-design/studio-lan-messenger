"""Read .ics calendar files (exported from Outlook, Google Calendar, Apple Calendar or holiday sites).

parse(text) -> [{"title", "notes", "location", "start": datetime, "end": datetime, "all_day": bool,
                 "rule": recur rule or None}]
Times with a Z (UTC) or a TZID are turned into this PC's local time; floating times are taken as local.
Repeats that the studio calendar can show (daily, weekly, monthly) are kept; yearly ones are expanded into
single events for the next years.
"""

import datetime
import re

_DAYS = {"MO": 0, "TU": 1, "WE": 2, "TH": 3, "FR": 4, "SA": 5, "SU": 6}


def _unfold(text):
    """Join continuation lines (RFC 5545: a line starting with a space continues the previous one)."""
    lines = []
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if raw.startswith((" ", "\t")) and lines:
            lines[-1] += raw[1:]
        elif raw:
            lines.append(raw)
    return lines


def _unescape(value):
    return (value.replace("\\n", "\n").replace("\\N", "\n").replace("\\,", ",").replace("\\;", ";")
            .replace("\\\\", "\\"))


def _when(params, value):
    """(datetime local, all_day)."""
    value = value.strip()
    if "VALUE=DATE" in params.upper() or re.fullmatch(r"\d{8}", value):
        return datetime.datetime.strptime(value[:8], "%Y%m%d"), True
    utc = value.endswith("Z")
    dt = datetime.datetime.strptime(value.rstrip("Z")[:15], "%Y%m%dT%H%M%S")
    if utc:
        dt = dt.replace(tzinfo=datetime.timezone.utc).astimezone().replace(tzinfo=None)
    elif "TZID=" in params.upper():
        try:
            from zoneinfo import ZoneInfo
            tz = ZoneInfo(re.search(r"TZID=([^;:]+)", params, re.I).group(1).strip('"'))
            dt = dt.replace(tzinfo=tz).astimezone().replace(tzinfo=None)
        except Exception:  # noqa: BLE001 - an unknown zone name: keep the clock time as it is
            pass
    return dt, False


def _rule(value, start):
    """RRULE -> our rule dict, 'yearly' (expanded by the caller), or None when it can't be shown."""
    parts = dict(p.split("=", 1) for p in value.split(";") if "=" in p)
    freq = parts.get("FREQ", "").upper()
    rule = {"interval": int(parts.get("INTERVAL", 1) or 1)}
    if "UNTIL" in parts:
        rule["until"] = datetime.datetime.strptime(parts["UNTIL"][:8], "%Y%m%d").date().isoformat()
    elif "COUNT" in parts:
        rule["count"] = int(parts["COUNT"])
    if freq == "DAILY":
        rule["freq"] = "daily"
    elif freq == "WEEKLY":
        rule["freq"] = "weekly"
        days = [_DAYS[d[-2:]] for d in parts.get("BYDAY", "").split(",") if d[-2:] in _DAYS]
        rule["days"] = days or [start.weekday()]
    elif freq == "MONTHLY" and "BYDAY" not in parts:
        rule["freq"] = "monthly"
    elif freq == "YEARLY":
        return "yearly", rule
    else:
        return None, None
    return rule, None


def parse(text, years_ahead=10):
    events = []
    current = None
    for line in _unfold(text):
        if line.upper() == "BEGIN:VEVENT":
            current = {}
            continue
        if line.upper() == "END:VEVENT":
            if current is not None and "start" in current:
                events.extend(_finish(current, years_ahead))
            current = None
            continue
        if current is None or ":" not in line:
            continue
        head, value = line.split(":", 1)
        name, _, params = head.partition(";")
        name = name.upper()
        try:
            if name == "DTSTART":
                current["start"], current["all_day"] = _when(params, value)
            elif name == "DTEND":
                current["end"], _ = _when(params, value)
            elif name == "SUMMARY":
                current["title"] = _unescape(value).strip()
            elif name == "DESCRIPTION":
                current["notes"] = _unescape(value).strip()
            elif name == "LOCATION":
                current["location"] = _unescape(value).strip()
            elif name == "RRULE":
                current["rrule"] = value
            elif name == "STATUS" and value.strip().upper() == "CANCELLED":
                current["cancelled"] = True
        except (ValueError, AttributeError):
            continue
    return events


def _finish(ev, years_ahead):
    if ev.get("cancelled"):
        return []
    start = ev["start"]
    end = ev.get("end") or (start + datetime.timedelta(days=1) if ev.get("all_day") else start + datetime.timedelta(hours=1))
    if end <= start:
        end = start + (datetime.timedelta(days=1) if ev.get("all_day") else datetime.timedelta(hours=1))
    base = {"title": (ev.get("title") or "Untitled")[:120], "notes": ev.get("notes", "")[:2000],
            "location": ev.get("location", "")[:200], "all_day": bool(ev.get("all_day")), "rule": None}
    rule, yearly = _rule(ev["rrule"], start) if ev.get("rrule") else (None, None)
    if rule == "yearly":                         # birthdays, holidays: one event per year
        out = []
        count = yearly.get("count")
        until = datetime.date.fromisoformat(yearly["until"]) if yearly.get("until") else None
        for i in range(0, years_ahead + 1, yearly.get("interval", 1)):
            try:
                s = start.replace(year=start.year + i)
            except ValueError:
                continue                          # 29 February
            if (until and s.date() > until) or (count and len(out) >= count):
                break
            out.append(dict(base, start=s, end=s + (end - start)))
        return out
    return [dict(base, start=start, end=end, rule=rule)]
