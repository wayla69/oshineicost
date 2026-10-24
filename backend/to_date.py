# -*- coding: utf-8 -*-
"""The to-date period rule (owner 2026-10-24) as the anomaly box (PR-OB) reads it. Since PR-PP1 this is a thin alias over
backend/periods.py - the ONE period module (docs/PERIOD_PATTERN.md) - so the two can never draw a period differently.

`to_date_ranges(anchor, kind)` -> {
    "kind", "anchor", "complete" (the anchor is the period's last day),
    "current":   (start, end)   the period that holds the anchor, from its first day to the anchor,
    "previous":  (start, end)   the period before, the same number of days (clamped to its own end: 31 Oct -> 30 Sep),
    "last_year": (start, end)   the same period a year earlier, the same days,
    "day": n                    the anchor's day of the period (1-based),
}
kind = week | month | year. Weeks run Monday to Sunday; the week of last year is 52 weeks earlier (the same weekdays)."""
from datetime import date
from typing import Dict, Union

from periods import resolve

_D = Union[str, date]


def to_date_ranges(anchor: _D, kind: str = "month") -> Dict:
    if kind not in ("week", "month", "year"):
        raise ValueError("kind must be week, month or year")
    a = anchor.isoformat() if isinstance(anchor, date) else str(anchor)[:10]
    r = resolve(kind, a)
    return {"kind": kind, "anchor": a, "complete": r["complete"], "day": r["days"],
            "current": r["cur"], "previous": r["prev"], "last_year": r["ly"]}
