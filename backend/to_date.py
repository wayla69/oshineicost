# -*- coding: utf-8 -*-
"""The to-date period rule (owner 2026-10-24): every sales figure follows ONE pattern - week / month / year - and a period not
finished is compared to-date: the same number of days of the previous period AND of the same period last year.

Pure (dates in, dates out), so every endpoint can share it. `to_date_ranges(anchor, kind)` -> {
    "kind", "anchor", "complete" (the anchor is the period's last day),
    "current":   (start, end)   the period that holds the anchor, from its first day to the anchor,
    "previous":  (start, end)   the period before, the same number of days (clamped to its own end: 31 Oct -> 30 Sep),
    "last_year": (start, end)   the same period a year earlier, the same days,
    "day": n                    the anchor's day of the period (1-based),
}
Weeks run Monday to Sunday; the week of last year is the week 52 weeks earlier (the same weekdays). Dates are ISO strings in and out."""
import calendar
from datetime import date, timedelta
from typing import Dict, Tuple, Union

_D = Union[str, date]


def _d(x: _D) -> date:
    return x if isinstance(x, date) else date.fromisoformat(str(x)[:10])


def _month_range(y: int, m: int, day: int) -> Tuple[date, date]:
    last = calendar.monthrange(y, m)[1]
    return date(y, m, 1), date(y, m, min(day, last))


def to_date_ranges(anchor: _D, kind: str = "month") -> Dict:
    a = _d(anchor)
    if kind == "month":
        cur = (date(a.year, a.month, 1), a)
        py, pm = (a.year, a.month - 1) if a.month > 1 else (a.year - 1, 12)
        complete = a.day == calendar.monthrange(a.year, a.month)[1]
        # a finished month is compared whole with whole months; an unfinished one day-for-day
        prev = _month_range(py, pm, 31 if complete else a.day)
        ly = _month_range(a.year - 1, a.month, 31 if complete else a.day)
        day = a.day
    elif kind == "week":
        start = a - timedelta(days=a.weekday())
        cur = (start, a)
        complete = a.weekday() == 6
        prev = (start - timedelta(days=7), a - timedelta(days=7))
        ly = (start - timedelta(days=364), a - timedelta(days=364))
        day = a.weekday() + 1
    elif kind == "year":
        cur = (date(a.year, 1, 1), a)
        complete = (a.month, a.day) == (12, 31)
        ly_end = date(a.year - 1, a.month, min(a.day, calendar.monthrange(a.year - 1, a.month)[1]))
        prev = ly = (date(a.year - 1, 1, 1), ly_end)
        day = a.timetuple().tm_yday
    else:
        raise ValueError("kind must be week, month or year")
    iso = lambda r: (r[0].isoformat(), r[1].isoformat())
    return {"kind": kind, "anchor": a.isoformat(), "complete": complete, "day": day,
            "current": iso(cur), "previous": iso(prev), "last_year": iso(ly)}
