# -*- coding: utf-8 -*-
"""One period pattern for every sales and cost figure (owner 2026-10-24, docs/PERIOD_PATTERN.md).

Pure: dates in, dates out (only `anchor(con)` reads the database). The JS mirror is
`periodRange` in js/01-core.js; backend/test_periods.py runs both on one date table.

Kinds
    day    one day                      prev = the same weekday a week earlier · ly = 364 days back (same weekday)
    week   Monday-Sunday                prev = the week before, same weekdays    · ly = 364 days back (same weekdays)
    month  calendar month               prev = days 1..n of last month (clamped) · ly = days 1..n of the month a year back
    year   calendar year                prev = ly = 1 Jan..the same date last year
    range  date_from..date_to           prev = the same number of days just before · ly = the same dates a year back

The anchor is the last CLOSED day (rules.latest_settled_day): the day still trading never enters a window.
A period that runs past the anchor is to-date: current = its first day..the anchor (n days) and both bases are the
first n days of theirs. A finished period compares whole with whole (a finished month with the whole previous month).
"""
import calendar
import math
from datetime import date, timedelta
from typing import Dict, Optional, Tuple, Union

_D = Union[str, date]
KINDS = ("day", "week", "month", "year", "range")
_MON_TH = ["ม.ค.", "ก.พ.", "มี.ค.", "เม.ย.", "พ.ค.", "มิ.ย.", "ก.ค.", "ส.ค.", "ก.ย.", "ต.ค.", "พ.ย.", "ธ.ค."]


def _d(x: _D) -> date:
    return x if isinstance(x, date) else date.fromisoformat(str(x)[:10])


def _mlen(y: int, m: int) -> int:
    return calendar.monthrange(y, m)[1]


def _year_back(d: date) -> date:
    """The same calendar date a year earlier; 29 Feb -> 28 Feb."""
    return date(d.year - 1, d.month, min(d.day, _mlen(d.year - 1, d.month)))


def _th(d: date, year: bool = True) -> str:
    return f"{d.day} {_MON_TH[d.month - 1]}" + (f" {d.year + 543}" if year else "")


def label_th(a: _D, b: _D) -> str:
    """'23 ต.ค. 2569' · '1–23 ต.ค. 2569' · '28 ก.ย.–3 ต.ค. 2569' · '29 ธ.ค. 2568–4 ม.ค. 2569' (Buddhist year)."""
    a, b = _d(a), _d(b)
    if a == b:
        return _th(b)
    if (a.year, a.month) == (b.year, b.month):
        return f"{a.day}–{_th(b)}"
    if a.year == b.year:
        return f"{_th(a, False)}–{_th(b)}"
    return f"{_th(a)}–{_th(b)}"


def _bounds(kind: str, pick: date) -> Tuple[date, date]:
    """The whole calendar period of `kind` that holds `pick`."""
    if kind == "day":
        return pick, pick
    if kind == "week":
        s = pick - timedelta(days=pick.weekday())
        return s, s + timedelta(days=6)
    if kind == "month":
        return date(pick.year, pick.month, 1), date(pick.year, pick.month, _mlen(pick.year, pick.month))
    if kind == "year":
        return date(pick.year, 1, 1), date(pick.year, 12, 31)
    raise ValueError(f"kind must be one of {KINDS}")


def resolve(kind: str, anchor: _D, today: Optional[_D] = None,
            date_from: Optional[_D] = None, date_to: Optional[_D] = None) -> Dict:
    """The windows of one figure.

    kind       day | week | month | year | range ('mtd' is read as month)
    anchor     the last closed day (data never runs past it)
    today      when given, the anchor is moved back to the day before it (the day still trading is never in a window)
    date_from  day/week/month/year: any day of the period wanted (default: the period that holds the anchor);
               range: the first day
    date_to    range only: the last day (clamped to the anchor)

    -> {kind, cur, prev, ly: (from_iso, to_iso), days, complete, label_th, prev_label_th, ly_label_th}
    Raises ValueError for an unknown kind or a period that starts after the anchor."""
    if kind == "mtd":
        kind = "month"
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {KINDS}")
    a = _d(anchor)
    if today is not None:
        a = min(a, _d(today) - timedelta(days=1))
    if kind == "range":
        if date_from is None or date_to is None:
            raise ValueError("range needs date_from and date_to")
        ps, pe = _d(date_from), _d(date_to)
        if ps > pe:
            raise ValueError("date_from is after date_to")
    else:
        ps, pe = _bounds(kind, _d(date_from) if date_from is not None else a)
    if ps > a:
        raise ValueError("the period starts after the anchor")
    complete = pe <= a
    cs, ce = ps, min(pe, a)
    n = (ce - cs).days + 1
    length = (pe - ps).days + 1
    if kind in ("day", "week"):
        prev = (cs - timedelta(days=7), ce - timedelta(days=7))
        ly = (cs - timedelta(days=364), ce - timedelta(days=364))
    elif kind == "month":
        py, pm = (cs.year, cs.month - 1) if cs.month > 1 else (cs.year - 1, 12)
        p0 = date(py, pm, 1)
        prev = (p0, date(py, pm, _mlen(py, pm) if complete else min(n, _mlen(py, pm))))
        l0 = date(cs.year - 1, cs.month, 1)
        ly = (l0, date(l0.year, l0.month, _mlen(l0.year, l0.month) if complete else min(n, _mlen(l0.year, l0.month))))
    elif kind == "year":
        prev = ly = (date(cs.year - 1, 1, 1), _year_back(ce))
    else:  # range: the first n days of the equal-length window just before
        p0 = ps - timedelta(days=length)
        prev = (p0, p0 + timedelta(days=n - 1))
        ly = (_year_back(cs), _year_back(ce))
    iso = lambda r: (r[0].isoformat(), r[1].isoformat())
    return {"kind": kind, "cur": iso((cs, ce)), "prev": iso(prev), "ly": iso(ly), "days": n, "complete": complete,
            "label_th": label_th(cs, ce), "prev_label_th": label_th(*prev), "ly_label_th": label_th(*ly)}


def _r1(x: float) -> float:
    """1 decimal, a half rounded up - the same as the JS mirror's Math.round(x * 10) / 10."""
    return math.floor(x * 10 + 0.5) / 10


def pct(cur, base) -> Optional[float]:
    """% change, 1 decimal. None when either side is missing or the base is not above zero (no fake %)."""
    if cur is None or base is None:
        return None
    try:
        cur, base = float(cur), float(base)
    except (TypeError, ValueError):
        return None
    if base <= 0:
        return None
    return _r1((cur - base) / base * 100.0)


def pp(cur, base) -> Optional[float]:
    """Change of a ratio in percentage points (จุด), 1 decimal. None when either side is missing."""
    if cur is None or base is None:
        return None
    try:
        return _r1(float(cur) - float(base))
    except (TypeError, ValueError):
        return None


def anchor(con, where: str = "t.source='Easy' AND b.name!='ALL'", params: Optional[dict] = None) -> Optional[str]:
    """The last closed day with its bills in (rules.latest_settled_day); None without data."""
    try:
        from .rules import latest_settled_day
    except ImportError:
        from rules import latest_settled_day
    return latest_settled_day(con, where, params).get("day")
