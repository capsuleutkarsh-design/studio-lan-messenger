"""India's public and festival holidays for any year, for the studio calendar's holiday list.

* Fixed dates (Republic Day, Independence Day, Gandhi Jayanti, Christmas ...) are exact.
* Good Friday comes from the Easter date (exact).
* Hindu festivals follow the lunar calendar like a panchang: new and full moons (Meeus, "Astronomical
  Algorithms", ch. 49), the Sun's sidereal sign (Lahiri ayanamsa) to name the amanta months (a month without
  a sankranti is adhik and skipped), and the tithi that is current at the festival's time of day (IST).
* Islamic holidays use the tabular Hijri calendar, shifted one day as India usually sights the moon a day
  after the calculation.
Festival dates can differ by a day from a local almanac or the moon sighting, so they carry confirm=True:
the admin checks and ticks them in the console.

holidays(year) -> [{"date": "YYYY-MM-DD", "name": ..., "kind": "national"|"festival"|"other", "confirm": bool}]
"""

import datetime
import math

IST = 5.5 / 24                   # days
SYNODIC = 29.530588861
MONTHS = ["Chaitra", "Vaishakha", "Jyeshtha", "Ashadha", "Shravana", "Bhadrapada", "Ashvin", "Kartika",
          "Margashirsha", "Pausha", "Magha", "Phalguna"]

# (name, amanta month, paksha 's'/'k', tithi 1-15, hour of day (IST) it is kept at, days to add)
LUNAR = [
    ("Maha Shivaratri", "Magha", "k", 14, 23.5, 0),
    ("Holi", "Phalguna", "s", 15, 19.0, 1),             # the day after Holika Dahan (full moon evening)
    ("Gudi Padwa / Ugadi", "Chaitra", "s", 1, 6.5, 0),
    ("Ram Navami", "Chaitra", "s", 9, 12.0, 0),
    ("Mahavir Jayanti", "Chaitra", "s", 13, 6.5, 0),
    ("Buddha Purnima", "Vaishakha", "s", 15, 6.5, 0),
    ("Raksha Bandhan", "Shravana", "s", 15, 6.5, 0),        # purnima at sunrise
    ("Janmashtami", "Shravana", "k", 8, 23.5, 0),
    ("Ganesh Chaturthi", "Bhadrapada", "s", 4, 12.0, 0),
    ("Dussehra", "Ashvin", "s", 10, 14.0, 0),
    ("Diwali (Lakshmi Puja)", "Ashvin", "k", 15, 19.0, 0),
    ("Govardhan Puja", "Kartika", "s", 1, 6.5, 0),
    ("Bhai Dooj", "Kartika", "s", 2, 13.0, 0),
    ("Guru Nanak Jayanti", "Kartika", "s", 15, 6.5, 0),
]
# (name, hijri month, day)
# (name, hijri month, day, days after the tabular date that India usually keeps it)
ISLAMIC = [("Eid al-Fitr", 10, 1, 1), ("Eid al-Adha (Bakrid)", 12, 10, 0), ("Muharram", 1, 10, 0),
           ("Milad-un-Nabi", 3, 12, 0)]
FIXED = [  # (month, day, name, kind)
    (1, 1, "New Year's Day", "other"), (1, 26, "Republic Day", "national"), (4, 14, "Ambedkar Jayanti", "other"),
    (5, 1, "Maharashtra Day / May Day", "other"), (8, 15, "Independence Day", "national"),
    (10, 2, "Gandhi Jayanti", "national"), (12, 25, "Christmas", "other"),
]


# ------------------------------------------------------------------ time
def jd_from_date(d: datetime.datetime):
    return d.toordinal() + 1721424.5 + (d.hour + d.minute / 60 + d.second / 3600) / 24


def date_from_jd(jd):
    """Julian day (UT) -> datetime (UT)."""
    days = jd - 1721424.5
    base = datetime.datetime.fromordinal(int(math.floor(days)))
    return base + datetime.timedelta(days=days - math.floor(days))


def _sin(deg):
    return math.sin(math.radians(deg))


# ------------------------------------------------------------------ moon phases (Meeus 49)
def moon_phase(k):
    """JDE of the new moon (k integer) or full moon (k + 0.5)."""
    t = k / 1236.85
    jde = (2451550.09766 + SYNODIC * k + 0.00015437 * t ** 2 - 0.000000150 * t ** 3 + 0.00000000073 * t ** 4)
    e = 1 - 0.002516 * t - 0.0000074 * t ** 2
    m = 2.5534 + 29.10535670 * k - 0.0000014 * t ** 2 - 0.00000011 * t ** 3
    mp = 201.5643 + 385.81693528 * k + 0.0107582 * t ** 2 + 0.00001238 * t ** 3 - 0.000000058 * t ** 4
    f = 160.7108 + 390.67050284 * k - 0.0016118 * t ** 2 - 0.00000227 * t ** 3 + 0.000000011 * t ** 4
    om = 124.7746 - 1.56375588 * k + 0.0020672 * t ** 2 + 0.00000215 * t ** 3
    full = abs(k % 1 - 0.5) < 1e-6
    c = ([-0.40614, 0.17302, 0.01614, 0.01043, 0.00734, -0.00515, 0.00209] if full else
         [-0.40720, 0.17241, 0.01608, 0.01039, 0.00739, -0.00514, 0.00208])
    jde += (c[0] * _sin(mp) + c[1] * e * _sin(m) + c[2] * _sin(2 * mp) + c[3] * _sin(2 * f)
            + c[4] * e * _sin(mp - m) + c[5] * e * _sin(mp + m) + c[6] * e * e * _sin(2 * m)
            - 0.00111 * _sin(mp - 2 * f) - 0.00057 * _sin(mp + 2 * f) + 0.00056 * e * _sin(2 * mp + m)
            - 0.00042 * _sin(3 * mp) + 0.00042 * e * _sin(m + 2 * f) + 0.00038 * e * _sin(m - 2 * f)
            - 0.00024 * e * _sin(2 * mp - m) - 0.00017 * _sin(om) - 0.00007 * _sin(mp + 2 * m)
            + 0.00004 * _sin(2 * mp - 2 * f) + 0.00004 * _sin(3 * m) + 0.00003 * _sin(mp + m - 2 * f)
            + 0.00003 * _sin(2 * mp + 2 * f) - 0.00003 * _sin(mp + m + 2 * f) + 0.00003 * _sin(mp - m + 2 * f)
            - 0.00002 * _sin(mp - m - 2 * f) - 0.00002 * _sin(3 * mp + m) + 0.00002 * _sin(4 * mp))
    return jde - 69.0 / 86400                        # TT -> UT (delta T, about 69 s these years)


# ------------------------------------------------------------------ the sun's sidereal longitude
def sun_sidereal(jd):
    t = (jd - 2451545.0) / 36525
    l0 = 280.46646 + 36000.76983 * t + 0.0003032 * t * t
    m = 357.52911 + 35999.05029 * t - 0.0001537 * t * t
    c = ((1.914602 - 0.004817 * t - 0.000014 * t * t) * _sin(m) + (0.019993 - 0.000101 * t) * _sin(2 * m)
         + 0.000289 * _sin(3 * m))
    om = 125.04 - 1934.136 * t
    apparent = l0 + c - 0.00569 - 0.00478 * _sin(om)
    ayanamsa = 23.853 + 0.013969 * (jd - 2451545.0) / 365.25          # Lahiri
    return (apparent - ayanamsa) % 360


def sankranti(jd_from, sign):
    """JD when the sun (sidereal) enters `sign` (0 = Mesha ... 9 = Makara), searching forward from jd_from."""
    target = sign * 30.0
    lo = jd_from
    while (sun_sidereal(lo + 1) - target) % 360 > (sun_sidereal(lo) - target) % 360:
        lo += 1
        if lo - jd_from > 400:
            raise RuntimeError("no sankranti found")
    hi = lo + 1
    for _ in range(40):
        mid = (lo + hi) / 2
        if (sun_sidereal(mid) - target) % 360 > 180:
            lo = mid
        else:
            hi = mid
    return hi


# ------------------------------------------------------------------ amanta months
def lunations(year):
    """[(month name or None for adhik, new moon JD, full moon JD, next new moon JD)] around a year."""
    k0 = math.floor((year - 2000) * 12.3685) - 2
    out = []
    for k in range(k0, k0 + 16):
        nm, fm, nxt = moon_phase(k), moon_phase(k + 0.5), moon_phase(k + 1)
        a, b = sun_sidereal(nm), sun_sidereal(nxt)
        signs = [s for s in range(12) if (s * 30 - a) % 360 < (b - a) % 360]
        name = MONTHS[signs[0]] if signs else None           # no sankranti: adhik (extra) month
        out.append((name, nm, fm, nxt))
    return out


# ------------------------------------------------------------------ the moon's longitude (Meeus 47)
_MOON_TERMS = [  # D, M, M', F, coefficient (1e-6 degrees)
    (0, 0, 1, 0, 6288774), (2, 0, -1, 0, 1274027), (2, 0, 0, 0, 658314), (0, 0, 2, 0, 213618),
    (0, 1, 0, 0, -185116), (0, 0, 0, 2, -114332), (2, 0, -2, 0, 58793), (2, -1, -1, 0, 57066),
    (2, 0, 1, 0, 53322), (2, -1, 0, 0, 45758), (0, 1, -1, 0, -40923), (1, 0, 0, 0, -34720),
    (0, 1, 1, 0, -30383), (2, 0, 0, -2, 15327), (0, 0, 1, 2, -12528), (0, 0, 1, -2, 10980),
    (4, 0, -1, 0, 10675), (0, 0, 3, 0, 10034), (4, 0, -2, 0, 8548), (2, 1, -1, 0, -7888),
    (2, 1, 0, 0, -6766), (1, 0, -1, 0, -5163), (1, 1, 0, 0, 4987), (2, -1, 1, 0, 4036),
    (2, 0, 2, 0, 3994), (4, 0, 0, 0, 3861), (2, 0, -3, 0, 3665), (0, 1, -2, 0, -2689),
    (2, 0, -1, 2, -2602), (2, -1, -2, 0, 2390), (1, 0, 1, 0, -2348), (2, -2, 0, 0, 2236),
    (0, 1, 2, 0, -2120), (0, 2, 0, 0, -2069), (2, -2, -1, 0, 2048), (2, 0, 1, -2, -1773),
    (2, 0, 0, 2, -1595), (4, -1, -1, 0, 1215), (0, 0, 2, 2, -1110), (3, 0, -1, 0, -892),
    (2, 1, 1, 0, -810), (4, -1, -2, 0, 759), (0, 2, -1, 0, -713), (2, 2, -1, 0, -700),
    (2, 1, -2, 0, 691), (2, -1, 0, -2, 596), (4, 0, 1, 0, 549), (0, 0, 4, 0, 537),
    (4, -1, 0, 0, 520), (1, 0, -2, 0, -487), (2, 1, 0, -2, -399), (0, 0, 2, -2, -381),
    (1, 1, 1, 0, 351), (3, 0, -2, 0, -340), (4, 0, -3, 0, 330), (2, -1, 2, 0, 327),
    (0, 2, 1, 0, -323), (1, 1, -1, 0, 299), (2, 0, 3, 0, 294),
]


def moon_longitude(jd):
    t = (jd - 2451545.0) / 36525
    lp = 218.3164477 + 481267.88123421 * t - 0.0015786 * t * t + t ** 3 / 538841 - t ** 4 / 65194000
    d = 297.8501921 + 445267.1114034 * t - 0.0018819 * t * t + t ** 3 / 545868 - t ** 4 / 113065000
    m = 357.5291092 + 35999.0502909 * t - 0.0001536 * t * t + t ** 3 / 24490000
    mp = 134.9633964 + 477198.8675055 * t + 0.0087414 * t * t + t ** 3 / 69699 - t ** 4 / 14712000
    f = 93.2720950 + 483202.0175233 * t - 0.0036539 * t * t - t ** 3 / 3526000 + t ** 4 / 863310000
    e = 1 - 0.002516 * t - 0.0000074 * t * t
    total = 0.0
    for cd, cm, cmp, cf, coef in _MOON_TERMS:
        term = coef * _sin(cd * d + cm * m + cmp * mp + cf * f)
        total += term * (e if abs(cm) == 1 else e * e if abs(cm) == 2 else 1)
    a1, a2 = 119.75 + 131.849 * t, 53.09 + 479264.290 * t
    total += 3958 * _sin(a1) + 1962 * _sin(lp - f) + 318 * _sin(a2)
    return (lp + total / 1e6) % 360


def sun_longitude(jd):
    t = (jd - 2451545.0) / 36525
    l0 = 280.46646 + 36000.76983 * t + 0.0003032 * t * t
    m = 357.52911 + 35999.05029 * t - 0.0001537 * t * t
    c = ((1.914602 - 0.004817 * t - 0.000014 * t * t) * _sin(m) + (0.019993 - 0.000101 * t) * _sin(2 * m)
         + 0.000289 * _sin(3 * m))
    return (l0 + c - 0.00569) % 360


def tithi_at(jd):
    """1-30: shukla 1-15 (15 = purnima), krishna 16-30 (30 = amavasya)."""
    elongation = (moon_longitude(jd) - sun_longitude(jd)) % 360
    return int(elongation // 12) + 1


def _festival_day(lun, paksha, tithi, hour):
    """The date (IST) of the lunar month whose `hour` (IST) falls in the tithi; when the tithi starts and ends
    between two such hours (it is 'skipped'), the day it was current for longest."""
    _name, nm, _fm, nxt = lun
    target = tithi if paksha == "s" else 15 + tithi
    first = date_from_jd(nm + IST).date()
    days = int(nxt - nm) + 2
    hits = []
    for i in range(days):
        d = first + datetime.timedelta(days=i)
        moment = jd_from_date(datetime.datetime.combine(d, datetime.time())) + hour / 24 - IST
        if nm <= moment < nxt and tithi_at(moment) == target:
            hits.append(d)
    if hits:
        return hits[0]
    # skipped at that hour: the day that holds most of it (sample every hour)
    count = {}
    jd = nm
    while jd < nxt:
        if tithi_at(jd) == target:
            day = date_from_jd(jd + IST).date()
            count[day] = count.get(day, 0) + 1
        jd += 1 / 24
    return max(count, key=count.get) if count else None


# ------------------------------------------------------------------ islamic (tabular)
def hijri_to_gregorian(y, m, d, india_shift=1):
    jd = (math.floor((11 * y + 3) / 30) + 354 * y + 30 * m - math.floor((m - 1) / 2) + d + 1948440 - 385)
    return date_from_jd(jd - 0.5).date() + datetime.timedelta(days=india_shift)


def easter(year):
    a, b, c = year % 19, year // 100, year % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    lg = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * lg) // 451
    month = (h + lg - 7 * m + 114) // 31
    day = (h + lg - 7 * m + 114) % 31 + 1
    return datetime.date(year, month, day)


# ------------------------------------------------------------------ the list
def holidays(year):
    out = [{"date": datetime.date(year, m, d).isoformat(), "name": n, "kind": k, "confirm": False}
           for m, d, n, k in FIXED]
    out.append({"date": (easter(year) - datetime.timedelta(days=2)).isoformat(), "name": "Good Friday",
                "kind": "other", "confirm": False})
    lun = lunations(year)
    for name, month, paksha, tithi, hour, add in LUNAR:
        for lu in lun:
            if lu[0] != month:
                continue
            day = _festival_day(lu, paksha, tithi, hour)
            if day is None:
                continue
            day += datetime.timedelta(days=add)
            if day.year == year:
                out.append({"date": day.isoformat(), "name": name, "kind": "festival", "confirm": True})
    ms = date_from_jd(sankranti(jd_from_date(datetime.datetime(year, 1, 1)), 9) + IST).date()   # Makara
    out.append({"date": ms.isoformat(), "name": "Makar Sankranti / Pongal", "kind": "festival", "confirm": True})
    hy = int((year - 622) * 33 / 32)
    for name, hm, hd, shift in ISLAMIC:
        for y in (hy - 1, hy, hy + 1, hy + 2):
            day = hijri_to_gregorian(y, hm, hd, shift)
            if day.year == year:
                out.append({"date": day.isoformat(), "name": name, "kind": "festival", "confirm": True})
    seen, unique = set(), []
    for h in sorted(out, key=lambda h: (h["date"], h["name"])):
        if (h["date"], h["name"]) not in seen:
            seen.add((h["date"], h["name"]))
            unique.append(h)
    return unique
