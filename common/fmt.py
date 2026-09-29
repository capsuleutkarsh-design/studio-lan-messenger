"""How text, dates and times are written in the client and the console - one style everywhere.

Dates never pad the day ('Tue 6 Oct', not 'Tue 06 Oct'), the year shows only when it isn't this year, times are
24-hour 'HH:MM', a cut ends in the one-character ellipsis ('…', never '...'), and parts of a line are joined with
SEP (' · ').

Every date function takes a timestamp (seconds), a datetime or a date.
"""

import datetime

ELLIPSIS = "…"
SEP = " · "            # between the parts of one line: 'Compositing · Reports to Rajiv'
DASH = " – "           # ranges: '16:00 – 17:00', '28 Sep – 4 Oct'


# ------------------------------------------------------------------ text
def clip(text, n, one_line=True):
    """text cut to at most n characters, on a word boundary where there is one near the end, ending in '…'.

    one_line: newlines and runs of spaces become single spaces first (previews, titles, menu labels)."""
    s = " ".join(str(text or "").split()) if one_line else str(text or "")
    if len(s) <= n:
        return s
    if n <= 1:
        return ELLIPSIS[:max(n, 0)]
    cut = s[:n - 1]
    space = cut.rfind(" ")
    if space >= (n - 1) * 0.6:                  # a word boundary in the last 40%: cut there, not mid-word
        cut = cut[:space]
    return cut.rstrip(" ,;:.-–—·") + ELLIPSIS


def menu_text(text):
    """A label for a menu item, button, check box or tab: Qt reads '&' as a keyboard-shortcut marker ('Photo &
    status' shows as 'Photo _status'), so a real ampersand is doubled. Use it for any label that can hold '&' -
    fixed words and names typed by people (rooms, files, people)."""
    return str(text or "").replace("&", "&&")


# ------------------------------------------------------------------ dates and times
def _dt(x):
    """A datetime from a timestamp, datetime, date (midnight) or Qt QDate/QDateTime."""
    if isinstance(x, datetime.datetime):
        return x
    if isinstance(x, datetime.date):
        return datetime.datetime.combine(x, datetime.time())
    if hasattr(x, "toPython"):                  # QDate / QDateTime
        return _dt(x.toPython())
    return datetime.datetime.fromtimestamp(float(x))


def _today(now=None):
    return _dt(now).date() if now is not None else datetime.date.today()


def day_word(x, now=None):
    """'Today', 'Yesterday' or 'Tomorrow' for those days, else None."""
    delta = (_dt(x).date() - _today(now)).days
    return {0: "Today", -1: "Yesterday", 1: "Tomorrow"}.get(delta)


def fmt_date(x, weekday=True, year=None, long=False, now=None):
    """'Tue 29 Sep' - weekday=False: '29 Sep' - long=True: 'Tuesday 29 September'.

    year: None adds it only when it isn't this year ('Mon 29 Sep 2025'), True always, False never."""
    d = _dt(x)
    if year is None:
        year = d.year != _today(now).year
    month = f"{d:%B}" if long else f"{d:%b}"
    text = f"{d.day} {month}" + (f" {d.year}" if year else "")
    if weekday:
        text = (f"{d:%A} " if long else f"{d:%a} ") + text
    return text


def fmt_time(x):
    """'16:00' (24-hour, like the rest of the app)."""
    return f"{_dt(x):%H:%M}"


def fmt_when(x, now=None, relative=True, weekday=True):
    """A moment for people: 'Today 16:00', 'Yesterday 18:00', 'Tomorrow 09:30', 'Tue 29 Sep, 18:00',
    '29 Sep 2025, 18:00' (another year: no weekday). relative=False never says Today/Yesterday/Tomorrow."""
    d = _dt(x)
    word = day_word(d, now) if relative else None
    if word:
        return f"{word} {fmt_time(d)}"
    other_year = d.year != _today(now).year
    return f"{fmt_date(d, weekday=weekday and not other_year, now=now)}, {fmt_time(d)}"


def fmt_range(a, b, weekday=False, year=None, now=None):
    """Two days as one range: '28 Sep – 4 Oct', '28–30 Sep', 'Mon 28 Sep – Fri 2 Oct'; one day: '29 Sep'.

    year: as in fmt_date (None: only when a day isn't in this year); it is written once, at the end."""
    da, db = _dt(a).date(), _dt(b).date()
    if year is None:
        this = _today(now).year
        year = da.year != this or db.year != this
    if da == db:
        return fmt_date(da, weekday=weekday, year=year)
    tail = f" {db.year}" if year else ""
    if da.year != db.year:                      # across new year: both years, or it reads wrong
        return f"{fmt_date(da, weekday, year=True)}{DASH}{fmt_date(db, weekday, year=True)}"
    if da.month == db.month and not weekday:
        return f"{da.day}–{db.day} {db:%b}{tail}"
    return f"{fmt_date(da, weekday, year=False)}{DASH}{fmt_date(db, weekday, year=False)}{tail}"


def fmt_time_range(a, b):
    """'16:00 – 17:30'."""
    return f"{fmt_time(a)}{DASH}{fmt_time(b)}"
