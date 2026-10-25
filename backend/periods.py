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


# ── PR-PP2 · the request side: one call per endpoint ───────────────────────────────────────────────────────────
_GRAN = {"daily": "day", "weekly": "week", "monthly": "month", "yearly": "year", "mtd": "month", "custom": "range"}


def kind_of(g: Optional[str], default: str = "month") -> str:
    """'daily' / 'weekly' / 'monthly' / 'yearly' / 'mtd' / a kind -> the kind; anything else -> `default`."""
    k = _GRAN.get(str(g or "").lower(), str(g or "").lower())
    return k if k in KINDS else default


def covered(win, first_day: Optional[str]) -> bool:
    """A base window counts only when the data reaches back to its first day (a window that starts before the
    first day with bills is a part window: its % would be made up)."""
    return bool(first_day) and bool(win) and str(win[0]) >= str(first_day)[:10]


def shift(kind: str, d: _D, n: int) -> str:
    """The first day of the period `n` steps from the one that holds `d` (n < 0 = back). day / week / month / year."""
    s, _ = _bounds(kind, _d(d))
    if kind == "day":
        return (s + timedelta(days=n)).isoformat()
    if kind == "week":
        return (s + timedelta(days=7 * n)).isoformat()
    if kind == "month":
        i = s.year * 12 + s.month - 1 + n
        return date(i // 12, i % 12 + 1, 1).isoformat()
    return date(s.year + n, 1, 1).isoformat()


def windows(con, kind: str, where: str = "t.source='Easy' AND b.name!='ALL'", params: Optional[dict] = None,
            pick: Optional[_D] = None, date_from: Optional[_D] = None, date_to: Optional[_D] = None,
            anchor_day: Optional[_D] = None) -> Optional[Dict]:
    """resolve() on the data of one scope: the anchor is the scope's last closed day (or `anchor_day`), `pick` any
    day of the period wanted (day / week / month / year), date_from..date_to the range. Adds
    {anchor, first_day, has_prev, has_ly}: a base that starts before the scope's first day with bills is not compared.
    A pick after the anchor falls back to the anchor's own period. None when the scope has no data.
    `where` uses the aliases t (sales_transaction) and b (branches)."""
    a = str(anchor_day)[:10] if anchor_day else anchor(con, where, params)
    if not a:
        return None
    first = con.execute(f"SELECT MIN(date(t.sold_at)) FROM sales_transaction t JOIN branches b ON b.branch_id=t.branch_id "
                        f"WHERE {where}", params or {}).fetchone()[0]
    k = kind_of(kind)
    try:
        if k == "range":
            r = resolve("range", a, date_from=date_from, date_to=date_to)
        else:
            r = resolve(k, a, date_from=pick if pick else None)
    except ValueError:
        r = resolve(k if k != "range" else "month", a)
    r.update({"anchor": a, "first_day": first, "has_prev": covered(r["prev"], first), "has_ly": covered(r["ly"], first)})
    return r


def span_of(month_from: Optional[str] = None, month_to: Optional[str] = None,
            date_from: Optional[str] = None, date_to: Optional[str] = None) -> Optional[Tuple[str, str]]:
    """A free A/B period as days: a day range wins; a month range runs from its first month's 1st to its last
    month's last day. None when it is open-ended."""
    if date_from or date_to:
        s, e = (date_from or "")[:10], (date_to or "")[:10]
    elif month_from or month_to:
        s = f"{(month_from or month_to)[:7]}-01"
        m2 = (month_to or month_from)[:7]
        e = f"{m2}-{_mlen(int(m2[:4]), int(m2[5:7])):02d}"
    else:
        return None
    return (s, e) if s and e else None


def guard(spans: Dict[str, Tuple[str, str]], anchor_day: Optional[str], first_day: Optional[str],
          lead: Tuple[str, ...] = ("a", "b")) -> Dict:
    """The free-period compare pages (trend A/B, compare A-D, menu A/B) under the one pattern: every period ends by
    the anchor (the last closed day); when the first of `lead` that runs past it is cut to date (n days), every other
    period is cut to its own first n days - a part period is never set against a whole one. A period that starts
    before `first_day` is a part base: no %.
    -> {windows: {x: (from, to)} for the periods that were cut, to_date, n_days, no_base: [x, ...]}"""
    out: Dict = {"windows": {}, "to_date": False, "n_days": None, "no_base": []}
    if not anchor_day:
        return out
    a = str(anchor_day)[:10]
    sp = {x: [s, e] for x, (s, e) in spans.items() if s and e}
    days = lambda s, e: (_d(e) - _d(s)).days + 1   # noqa: E731
    first_lead = next((x for x in lead if x in sp and sp[x][1] > a >= sp[x][0]), None)
    for x in sp:
        if sp[x][1] > a:
            sp[x][1] = a
            out["windows"][x] = tuple(sp[x])
    if first_lead:
        n = days(*sp[first_lead])
        out.update(to_date=True, n_days=n)
        for x, (s, e) in sp.items():
            if s <= e and days(s, e) > n:
                sp[x][1] = (_d(s) + timedelta(days=n - 1)).isoformat()
                out["windows"][x] = tuple(sp[x])
    out["no_base"] = [x for x in sp if x not in lead[:1] and not covered(sp[x], first_day)]
    return out


_PUBLIC = ("kind", "cur", "prev", "ly", "days", "complete", "label_th", "prev_label_th", "ly_label_th",
           "anchor", "has_prev", "has_ly")


def reasons(w: Optional[Dict]) -> Dict[str, str]:
    """PR-PP3 · why a base of `w` is not compared (the "?" says it instead of a bare "—"): the base starts before the
    first day with bills. A year names the month the data starts ('ข้อมูลปีก่อนเริ่ม พ.ค. 2568' - live data starts
    2025-05, so a year to date has no whole year-ago base until 2027); other kinds the first day."""
    out: Dict[str, str] = {}
    if not w or not w.get("first_day"):
        return out
    f = _d(w["first_day"])
    txt = (f"ข้อมูลปีก่อนเริ่ม {_MON_TH[f.month - 1]} {f.year + 543}" if w.get("kind") == "year"
           else f"ข้อมูลเริ่ม {_th(f)}")
    for k in ("prev", "ly"):
        if w.get(f"has_{k}") is False:
            out[k] = txt
    return out


def public(w: Optional[Dict]) -> Optional[Dict]:
    """The `period` object an endpoint returns (the JS periodCmpHtml reads it as its `range`); `reasons` says why a
    base is not compared (periodQ shows it)."""
    if not w:
        return None
    out = {k: (list(w[k]) if isinstance(w.get(k), tuple) else w.get(k)) for k in _PUBLIC if k in w}
    r = reasons(w)
    if r:
        out["reasons"] = r
    return out


# ── owner 2026-10-25 · W vs W: the Monday–Sunday weeks of the year, numbered ISO-style (W1 = the week with the year's first Thursday) ──
def week_span_th(a: _D, b: _D, year: bool = False) -> str:
    """'19–25 ต.ค.' · '29 ธ.ค.–4 ม.ค.' (+ ' 69' with year=True: the 2-digit Buddhist year of the last day)."""
    a, b = _d(a), _d(b)
    yy = f" {(b.year + 543) % 100:02d}" if year else ""
    if a == b:
        return f"{a.day} {_MON_TH[a.month - 1]}{yy}"
    if (a.year, a.month) == (b.year, b.month):
        return f"{a.day}–{b.day} {_MON_TH[b.month - 1]}{yy}"
    return f"{a.day} {_MON_TH[a.month - 1]}–{b.day} {_MON_TH[b.month - 1]}{yy}"


def iso_weeks(first: Optional[_D], last: Optional[_D]) -> list:
    """The Monday–Sunday weeks whose Monday is on or after `first` (the first day with day-level data: a week that starts before
    it has no whole data) and on or before `last` (the last closed day), newest first. A week still running (its Sunday after
    `last`) is to date: closed_to = last, partial = True. The label names the ISO week number and its days
    ('W43 · 19–25 ต.ค.', 'W43 · 19–24 ต.ค. (ถึงวันที่ปิด)'); a week of another ISO year than `last`'s adds its year
    ('W52 · 22–28 ธ.ค. 68'). -> [{key, year, week, from, to, closed_to, days, partial, label}]"""
    if not first or not last:
        return []
    f, la = _d(first), _d(last)
    mon = la - timedelta(days=la.weekday())
    ly = la.isocalendar()[0]
    out = []
    while mon >= f:
        sun = mon + timedelta(days=6)
        iy, iw = mon.isocalendar()[0], mon.isocalendar()[1]
        ct = min(sun, la)
        part = sun > la
        lab = f"W{iw} · {week_span_th(mon, ct, year=iy != ly)}" + (" (ถึงวันที่ปิด)" if part else "")
        out.append({"key": f"{iy}-W{iw:02d}", "year": iy, "week": iw, "from": mon.isoformat(), "to": sun.isoformat(),
                    "closed_to": ct.isoformat(), "days": (ct - mon).days + 1, "partial": part, "label": lab})
        mon -= timedelta(days=7)
    return out
