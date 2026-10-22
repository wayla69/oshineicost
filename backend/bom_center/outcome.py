# -*- coding: utf-8 -*-
"""Batch 4b (2.5) - what the orders actually achieved, beside the forecast error.

The forecast is already measured against itself (forecast.backtest: sales x recipe against sales x recipe). That says nothing about whether the
shelf was empty or full when the next delivery came. This measures it, per delivery of every round the branch ordered for:

    days of cover left   the system stock of each ordered item at the end of the last day the order had to cover (the day before the next
                         delivery of the round arrives), divided by the item's average daily use; the median over the items is the row's figure;
    stock-outs           the ordered items whose system stock was nil or below at that moment - the shelf had run out before the delivery.

The stock is stock.system_position (month-end count, the weekly counts of the class-A items, goods received, confirmed slips and the
estimate for the days with sales and no slip - PR-B4a 1.3): no stock definition of its own. The average daily use is stock._usage, the same
56-day (8-week) figure the count sheet and the order plan use. A delivery whose next delivery has not arrived yet is not judged; a branch with no count
to start from has no figure (the row says so). Per branch and month the rows add up to rounds measured, the median days of cover, the stock-outs
and the rounds that had any.
"""
import statistics
from datetime import date, timedelta

from . import stock as ST
from .clock import today as bkk_today
from .fmt import item as _item

STOCKOUT_NAMES = 5


def _month_bounds(month: str):
    y, m = int(month[:4]), int(month[5:7])
    return date(y, m, 1), date(y + (m == 12), 1 if m == 12 else m + 1, 1) - timedelta(days=1)


def branch_rows(con, branch_id: int, d0: date, d1: date, today: date = None) -> list:
    """One row per submitted order delivered between d0 and d1 (inclusive), oldest first."""
    from .forecast import ROUND_TH, cover_for_delivery
    from .hub import delivery_of
    today = today or bkk_today()
    name = (con.execute("SELECT name FROM branches WHERE branch_id=?", (branch_id,)).fetchone() or [None])[0]
    orders = con.execute("SELECT order_id, round, cycle_date, delivery_date FROM bc_order WHERE branch_id=?"
                         " AND status IN ('submitted','processed','received') AND cycle_date>=? AND cycle_date<=? ORDER BY cycle_date, order_id",
                         (branch_id, (d0 - timedelta(days=7)).isoformat(), d1.isoformat())).fetchall()
    use = None
    rows = []
    for oid, rnd, cyc, ddate in orders:
        dlv = date.fromisoformat(delivery_of(con, branch_id, rnd, cyc, ddate))
        if not (d0 <= dlv <= d1):
            continue
        row = {"branch_id": branch_id, "branch": name, "order_id": oid, "round": rnd, "round_th": ROUND_TH.get(rnd, rnd),
               "delivery": dlv.isoformat(), "measured": False, "cover_days": None, "items": 0, "stockouts": 0, "stockout_items": [],
               "estimated_days": 0, "why": None}
        try:
            _c, _d, _cf, cover_to = cover_for_delivery(con, branch_id, rnd, dlv)
        except Exception:
            row["why"] = "ไม่พบรอบส่งในปฏิทิน"
            rows.append(row)
            continue
        arrive = cover_to + timedelta(days=1)
        row["next_arrival"] = arrive.isoformat()
        if arrive > today:
            row["why"] = "รอบส่งถัดไปยังไม่ถึง"
            rows.append(row)
            continue
        sp = ST.system_position(con, branch_id, cover_to, issues_through=cover_to)
        if not sp["basis"]:
            row["why"] = "ยังไม่มีผลตรวจนับตั้งต้น"
            rows.append(row)
            continue
        if use is None:                                        # one 56-day average per branch and window, not one per order
            use = ST._usage(con, branch_id, min(d1, today - timedelta(days=1)))
        codes = [c for (c,) in con.execute("SELECT ingredient_code FROM bc_order_line WHERE order_id=?", (oid,))]
        cover, out = [], []
        for c in codes:
            q, u = sp["qty"].get(c), use.get(c) or 0.0
            if q is None or u <= 0:                            # not counted, or not used: nothing to measure
                continue
            if q <= 1e-9:
                out.append(c)
            cover.append(max(q, 0.0) / u)
        row.update(measured=bool(cover), items=len(cover), stockouts=len(out), estimated_days=len(sp["estimated_days"]),
                   cover_days=round(statistics.median(cover), 1) if cover else None,
                   stockout_items=[_item(con, c) for c in out[:STOCKOUT_NAMES]])
        if not cover:
            row["why"] = "ไม่มีรายการที่มีผลตรวจนับและมีการใช้"
        rows.append(row)
    return rows


def summarize(rows: list) -> dict:
    ok = [r for r in rows if r["measured"]]
    covers = [r["cover_days"] for r in ok if r["cover_days"] is not None]
    return {"deliveries": len(rows), "measured": len(ok), "items": sum(r["items"] for r in ok),
            "cover_days": round(statistics.median(covers), 1) if covers else None,
            "stockouts": sum(r["stockouts"] for r in ok), "stockout_rounds": sum(1 for r in ok if r["stockouts"]),
            "estimated_days": sum(r["estimated_days"] for r in ok)}


def window(con, branch_id: int, d0: date, d1: date, today: date = None) -> dict:
    rows = branch_rows(con, branch_id, d0, d1, today)
    return dict(summarize(rows), rows=rows)


def month(con, month: str, branch_id: int = None, today: date = None) -> dict:
    """The outcome of every branch that ordered in `month` (or one branch): {month, branches[{branch, ... summarize}], rows, total}."""
    d0, d1 = _month_bounds(month)
    names = dict(con.execute("SELECT branch_id, name FROM branches"))
    ids = [branch_id] if branch_id else [r[0] for r in con.execute(
        "SELECT DISTINCT branch_id FROM bc_order WHERE status IN ('submitted','processed','received') AND cycle_date>=? AND cycle_date<=?",
        ((d0 - timedelta(days=7)).isoformat(), d1.isoformat()))]
    branches, all_rows = [], []
    for bid in sorted(ids, key=lambda b: names.get(b) or ""):
        rows = branch_rows(con, bid, d0, d1, today)
        if not rows:
            continue
        s = summarize(rows)
        s.update(branch_id=bid, branch=names.get(bid))
        branches.append(s)
        all_rows += rows
    branches.sort(key=lambda b: (-(b["stockouts"] or 0), b["cover_days"] if b["cover_days"] is not None else 1e9, b["branch"] or ""))
    return {"month": month, "branches": branches, "rows": all_rows, "total": summarize(all_rows)}
