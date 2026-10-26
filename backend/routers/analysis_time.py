"""Time of day & flow — the /api/analysis hourly / dow / daypart / heatmap / branch-peak /
table-turnover family. Split out of routers/analysis.py on 2026-09-05, code moved verbatim."""
from fastapi import Query
from typing import Optional
from rules import closed_branches, drop_closed, net_revenue_row, net_revenue_sum
from core import _group_filter, _iso_week_sql, _month_range_filter, get_db, ttl_cache
try:
    from .analysis import _build_period_filters
except ImportError:
    from analysis import _build_period_filters

from fastapi import APIRouter
router = APIRouter()


# ---------------------------------------------------------------------------
# /api/analysis/hourly   -  revenue/tx breakdown by hour of day
# ---------------------------------------------------------------------------
@router.get("/api/analysis/hourly")
@ttl_cache
def analysis_hourly(
    month:      Optional[str] = Query(None),
    month_from: Optional[str] = Query(None),
    month_to:   Optional[str] = Query(None),
    date_from:  Optional[str] = Query(None),
    date_to:    Optional[str] = Query(None),
    branch:     Optional[str] = Query(None),
):
    """Revenue, tx count, avg ticket by hour (0-23). Fills missing hours with zeros."""
    filters = ["b.name != 'ALL'", "st.source = 'Easy'"]
    params: dict = {}
    if branch:
        filters.append("b.name = :branch")
        params["branch"] = branch
    if date_from or date_to:
        if date_from:
            filters.append("DATE(st.sold_at) >= :date_from")
            params["date_from"] = date_from
        if date_to:
            filters.append("DATE(st.sold_at) <= :date_to")
            params["date_to"] = date_to
    else:
        _month_range_filter(
            filters, params, month_from, month_to, month,
            col="strftime('%Y-%m', st.sold_at)",
        )
    where = "WHERE " + " AND ".join(filters)
    with get_db() as con:
        rows = con.execute(
            f"""
            SELECT CAST(strftime('%H', st.sold_at) AS INTEGER) AS hour,
                   COUNT(*)                                     AS tx_count,
                   COALESCE(SUM(st.customer_count), 0)         AS customer_count,
                   ROUND({net_revenue_sum('st')}, 2)             AS revenue,
                   ROUND(AVG{net_revenue_row('st')}, 2)             AS avg_ticket
            FROM sales_transaction st
            JOIN branches b ON b.branch_id = st.branch_id
            {where}
            GROUP BY hour
            ORDER BY hour
            """,
            params,
        ).fetchall()
    hour_map = {r["hour"]: dict(r) for r in rows}
    return [
        hour_map.get(h, {"hour": h, "tx_count": 0, "customer_count": 0,
                         "revenue": 0.0, "avg_ticket": 0.0})
        for h in range(24)
    ]


# ---------------------------------------------------------------------------
# /api/analysis/dow   -  day-of-week distribution
# ---------------------------------------------------------------------------
@router.get("/api/analysis/dow")
@ttl_cache
def analysis_dow(
    month:       Optional[str] = Query(None),
    month_from:  Optional[str] = Query(None),
    month_to:    Optional[str] = Query(None),
    branch:      Optional[str] = Query(None),
    group:       Optional[str] = Query(None),
    granularity: str           = Query("daily"),  # daily|weekly|monthly
):
    """Revenue and tx count grouped by day-of-week (0=Sun â€¦ 6=Sat)."""
    # The no-branch case (the Analysis Report default) drops the branches JOIN and
    # the b.name!='ALL' guard so the query is served index-only by idx_tx_ym_dow_cov.
    # Safe: sales_transaction holds no 'ALL'-named branch, so the guard excludes
    # nothing (verified). With a branch filter we keep the JOIN for b.name=:branch.
    filters: list = []
    params: dict = {}
    join = ""
    if branch or group:
        join = "JOIN branches b ON b.branch_id = st.branch_id"
    if branch:
        filters.append("b.name = :branch")
        params["branch"] = branch
    _group_filter(filters, params, group)
    _month_range_filter(filters, params, month_from, month_to, month, col="strftime('%Y-%m', st.sold_at)")

    where = ("WHERE " + " AND ".join(filters)) if filters else ""

    with get_db() as con:
        rows = con.execute(
            f"""
            SELECT CAST(strftime('%w', st.sold_at) AS INTEGER) AS dow,
                   COUNT(*)                                     AS tx_count,
                   SUM(st.customer_count)                       AS customer_count,
                   ROUND({net_revenue_sum('st')}, 2)             AS revenue,
                   ROUND(AVG{net_revenue_row('st')}, 2)             AS avg_ticket,
                   COUNT(DISTINCT DATE(st.sold_at))             AS day_count
            FROM sales_transaction st
            {join}
            {where}
            GROUP BY dow
            ORDER BY dow
            """,
            params,
        ).fetchall()

    # For weekly/monthly: compute total periods in the selected range for normalisation
    total_periods = None
    if granularity in ("weekly", "monthly"):
        _pexpr = (_iso_week_sql('st.sold_at') if granularity == "weekly"
                  else "strftime('%Y-%m', st.sold_at)")
        with get_db() as con2:
            pr = con2.execute(
                f"SELECT COUNT(DISTINCT {_pexpr}) AS cnt "
                f"FROM sales_transaction st {join} {where}",
                params
            ).fetchone()
        total_periods = max(pr["cnt"] if pr else 1, 1)

    DOW = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]
    result = {d: {"dow": d, "label": DOW[d], "tx_count": 0, "customer_count": 0,
                  "revenue": 0.0, "avg_ticket": 0.0, "day_count": 0,
                  "avg_revenue": 0.0, "avg_tx": 0.0} for d in range(7)}
    for r in rows:
        if granularity == "daily":
            dc = max(r["day_count"] or 1, 1)
        else:
            dc = total_periods
        result[r["dow"]] = {
            "dow": r["dow"], "label": DOW[r["dow"]],
            "tx_count": r["tx_count"], "customer_count": r["customer_count"],
            "revenue": r["revenue"], "avg_ticket": r["avg_ticket"],
            "day_count":   r["day_count"] or 0,
            "avg_revenue": round((r["revenue"] or 0) / dc, 2),
            "avg_tx":      round((r["tx_count"] or 0) / dc, 2),
        }
    return list(result.values())


# ---------------------------------------------------------------------------
# /api/analysis/heatmap_branch   -  branch  x  period heatmap
# ---------------------------------------------------------------------------
@router.get("/api/analysis/heatmap_branch")
@ttl_cache
def heatmap_branch(
    month_from:  Optional[str] = Query(None),
    month_to:    Optional[str] = Query(None),
    date_from:   Optional[str] = Query(None),
    date_to:     Optional[str] = Query(None),
    granularity: str           = Query("weekly"),  # daily|weekly|monthly
):
    """Branch × period revenue grid (ex-VAT).
    Monthly: Easy SKU-level (sales_sku_monthly). Daily/Weekly: Easy transactions."""

    # ── MONTHLY: Easy SKU-level ────────────────────────────────────────────────
    if granularity == "monthly":
        filters_easy = ["b.name != 'ALL'", "sm.source = 'Easy'"]
        params_easy: dict = {}
        _month_range_filter(filters_easy, params_easy, month_from, month_to, None, col="sm.period_month")
        where_easy = "WHERE " + " AND ".join(filters_easy)

        with get_db() as con:
            easy_rows = con.execute(
                f"""
                SELECT b.name AS branch,
                       sm.period_month AS period,
                       ROUND(SUM(sm.subtotal_ex_vat), 2) AS revenue
                FROM v_sales_sku_monthly sm
                JOIN branches b ON b.branch_id = sm.branch_id
                {where_easy}
                GROUP BY b.name, sm.period_month
                """,
                params_easy,
            ).fetchall()
            closed = closed_branches(con, [_heat_span(month_from, month_to, date_from, date_to)])
        return drop_closed([{"branch": r["branch"], "period": r["period"], "revenue": r["revenue"] or 0}
                            for r in sorted(easy_rows, key=lambda x: (x["branch"], x["period"]))], closed)

    # ── DAILY / WEEKLY: Easy transaction-level ─────────────────────────────────
    period_expr = "DATE(st.sold_at)" if granularity == "daily" else _iso_week_sql('st.sold_at')
    filters, params = _build_period_filters(None, month_from, month_to, date_from, date_to)
    where = "WHERE " + " AND ".join(filters)

    with get_db() as con:
        rows = con.execute(
            f"""
            SELECT b.name AS branch,
                   {period_expr} AS period,
                   ROUND({net_revenue_sum('st')}, 2) AS revenue,
                   COUNT(*) AS tx_count
            FROM sales_transaction st
            JOIN branches b ON b.branch_id = st.branch_id
            {where}
            GROUP BY b.name, period
            ORDER BY b.name, period
            """,
            params,
        ).fetchall()
        closed = closed_branches(con, [_heat_span(month_from, month_to, date_from, date_to)])
    return drop_closed([dict(r) for r in rows], closed)


def _heat_span(month_from, month_to, date_from, date_to):
    """The heat map's span as one closed-branch window (rules.closed_branches): the days, else the months; no filter = all time."""
    a = (date_from or "")[:10] or (month_from or "")[:7]
    b = (date_to or "")[:10] or (month_to or "")[:7]
    return (a or "0000-01", b or "9999-12")


# ============================================================================
# HOURLY analysis with operating-hours filter (uses closed_hours table)
# ============================================================================
@router.get("/api/analysis/hourly_open")
@ttl_cache
def analysis_hourly_open(
    month:       Optional[str] = Query(None),
    month_from:  Optional[str] = Query(None),
    month_to:    Optional[str] = Query(None),
    branch:      Optional[str] = Query(None),
    group:       Optional[str] = Query(None),
    granularity: str           = Query("daily"),  # daily|weekly|monthly
):
    """Like /api/analysis/hourly but each hour is annotated with `is_open`,
    derived from the closed_hours table. Hours marked closed are still returned
    (count may be 0) so the chart can show them greyed out."""
    # Inline the hourly query (can't call FastAPI endpoints as plain functions).
    # No-branch default drops the branches JOIN + b.name!='ALL' guard (keeping the
    # source='Easy' filter) so the query uses the existing idx_tx_src_hour instead
    # of full-scanning. JOIN kept only when a branch is selected. Safe: no
    # 'ALL'-named branch rows exist (verified).
    filters = ["st.source = 'Easy'"]
    params: dict = {}
    join = ""
    if branch or group:
        join = "JOIN branches b ON b.branch_id = st.branch_id"
    if branch:
        filters.append("b.name = :branch")
        params["branch"] = branch
    _group_filter(filters, params, group)
    _month_range_filter(filters, params, month_from, month_to, month,
                        col="strftime('%Y-%m', st.sold_at)")
    where = "WHERE " + " AND ".join(filters)
    with get_db() as con:
        rows = con.execute(f"""
            SELECT CAST(strftime('%H', st.sold_at) AS INTEGER) AS hour,
                   COUNT(*)                                    AS tx_count,
                   COALESCE(SUM(st.customer_count), 0)         AS customer_count,
                   ROUND({net_revenue_sum('st')}, 2)             AS revenue,
                   ROUND(AVG{net_revenue_row('st')}, 2)             AS avg_ticket
            FROM sales_transaction st
            {join}
            {where}
            GROUP BY hour ORDER BY hour
        """, params).fetchall()
        # Count distinct periods for average computation
        if granularity == "weekly":
            _period_expr = _iso_week_sql('st.sold_at')
        elif granularity == "monthly":
            _period_expr = "strftime('%Y-%m', st.sold_at)"
        else:
            _period_expr = "DATE(st.sold_at)"
        day_row = con.execute(f"""
            SELECT COUNT(DISTINCT {_period_expr}) AS cnt
            FROM sales_transaction st
            {join}
            {where}
        """, params).fetchone()
        total_days = max(day_row["cnt"] if day_row else 1, 1)

        hour_map = {r["hour"]: dict(r) for r in rows}
        raw = [hour_map.get(h, {"hour": h, "tx_count": 0, "customer_count": 0,
                                "revenue": 0.0, "avg_ticket": 0.0}) for h in range(24)]

        if branch:
            ch_rows = con.execute("""
                SELECT ch.hour_from, ch.hour_to, ch.is_closed
                FROM closed_hours ch
                JOIN branches b ON b.branch_id = ch.branch_id
                WHERE b.name = :branch
            """, {"branch": branch}).fetchall()
        else:
            ch_rows = con.execute(
                "SELECT hour_from, hour_to, is_closed FROM closed_hours"
            ).fetchall()

    if ch_rows:
        # OPEN-WINDOW model: each (branch, dow) row gives an open window
        # [hour_from..hour_to] unless is_closed (whole day shut). The chart's
        # is_open is the UNION of open windows across the rows in scope.
        open_set = set()
        any_open = False
        for r in ch_rows:
            if r["is_closed"]:
                continue  # this (branch, dow) is fully closed → contributes no open hours
            hf = r["hour_from"] if r["hour_from"] is not None else 10
            ht = r["hour_to"]   if r["hour_to"]   is not None else 21
            if hf <= ht:
                for h in range(hf, ht + 1): open_set.add(h)
            else:  # window wraps past midnight
                for h in range(hf, 24):    open_set.add(h)
                for h in range(0, ht + 1): open_set.add(h)
            any_open = True
        open_hours = open_set if any_open else set(range(10, 22))
    else:
        open_hours = set(range(10, 22))  # default 10:00–21:00 (open hours 10..21)
    for h in raw:
        h["is_open"] = h["hour"] in open_hours
        h["avg_daily_revenue"] = round((h["revenue"] or 0) / total_days, 2)
        h["avg_daily_tx"]      = round((h["tx_count"] or 0) / total_days, 2)
        h["total_days"]        = total_days
    return raw


@router.get("/api/analysis/hour_detail")
@ttl_cache
def analysis_hour_detail(
    hour:       int           = Query(..., ge=0, le=23),
    month:      Optional[str] = Query(None),
    month_from: Optional[str] = Query(None),
    month_to:   Optional[str] = Query(None),
    branch:     Optional[str] = Query(None),
):
    """Drill-down for one hour-of-day: revenue/tx/customers split by branch and by
    day-of-week, powering the Time & Flow peak-hour cards. Revenue is ex-VAT."""
    filters = ["b.name != 'ALL'", "st.source = 'Easy'",
               "CAST(strftime('%H', st.sold_at) AS INTEGER) = :hour"]
    params: dict = {"hour": hour}
    if branch:
        filters.append("b.name = :branch")
        params["branch"] = branch
    _month_range_filter(filters, params, month_from, month_to, month,
                        col="strftime('%Y-%m', st.sold_at)")
    where = "WHERE " + " AND ".join(filters)
    with get_db() as con:
        by_branch = con.execute(f"""
            SELECT b.name AS branch, COUNT(*) AS tx_count,
                   COALESCE(SUM(st.customer_count), 0) AS customer_count,
                   ROUND({net_revenue_sum('st')}, 2) AS revenue
            FROM sales_transaction st JOIN branches b ON b.branch_id = st.branch_id
            {where} GROUP BY b.name ORDER BY revenue DESC""", params).fetchall()
        by_dow = con.execute(f"""
            SELECT CAST(strftime('%w', st.sold_at) AS INTEGER) AS dow,
                   COUNT(*) AS tx_count,
                   ROUND({net_revenue_sum('st')}, 2) AS revenue
            FROM sales_transaction st JOIN branches b ON b.branch_id = st.branch_id
            {where} GROUP BY dow ORDER BY dow""", params).fetchall()
        dd = con.execute(f"""SELECT COUNT(DISTINCT DATE(st.sold_at)) AS c
            FROM sales_transaction st JOIN branches b ON b.branch_id = st.branch_id
            {where}""", params).fetchone()
    total_days = max(dd["c"] if dd else 1, 1)
    grand = sum(r["revenue"] or 0 for r in by_branch)
    return {
        "hour":        hour,
        "total_days":  total_days,
        "grand_revenue": round(grand, 2),
        "by_branch":   [dict(r) for r in by_branch],
        "by_dow":      [dict(r) for r in by_dow],
    }


# ---------------------------------------------------------------------------
# /api/analysis/daypart_grid  — Hourly × DOW 2D matrix
# ---------------------------------------------------------------------------
@router.get("/api/analysis/daypart_grid")
@ttl_cache
def analysis_daypart_grid(
    month: Optional[str] = Query(None),
    month_from: Optional[str] = Query(None),
    month_to: Optional[str] = Query(None),
    branch: Optional[str] = Query(None),
    metric: str = Query("revenue"),  # revenue|tx_count|customer_count|avg_ticket
):
    filters = ["st.source = 'Easy'", "b.name != 'ALL'"]
    params: dict = {}
    if branch:
        filters.append("b.name = :branch")
        params["branch"] = branch
    _month_range_filter(filters, params, month_from, month_to, month,
                        col="strftime('%Y-%m', st.sold_at)")
    where = "WHERE " + " AND ".join(filters)

    with get_db() as con:
        rows = con.execute(f"""
            SELECT
                CAST(strftime('%w', st.sold_at) AS INTEGER)  AS dow,
                CAST(strftime('%H', st.sold_at) AS INTEGER)  AS hour,
                COUNT(*)                                      AS tx_count,
                COALESCE(SUM(st.customer_count), 0)           AS customer_count,
                ROUND({net_revenue_sum('st')}, 2)               AS revenue,
                ROUND(AVG{net_revenue_row('st')}, 2)               AS avg_ticket,
                COUNT(DISTINCT DATE(st.sold_at))              AS day_count
            FROM sales_transaction st
            JOIN branches b ON b.branch_id = st.branch_id
            {where}
            GROUP BY dow, hour
        """, params).fetchall()

    cells = []
    max_value = 0.0
    for r in rows:
        if metric == "revenue":
            val = (r["revenue"] or 0) / max(r["day_count"] or 1, 1)
        elif metric == "tx_count":
            val = (r["tx_count"] or 0) / max(r["day_count"] or 1, 1)
        elif metric == "customer_count":
            val = (r["customer_count"] or 0) / max(r["day_count"] or 1, 1)
        else:  # avg_ticket
            val = r["avg_ticket"] or 0
        val = round(val, 2)
        cells.append({"dow": r["dow"], "hour": r["hour"], "value": val})
        if val > max_value:
            max_value = val

    peak = max(cells, key=lambda c: c["value"]) if cells else None

    return {
        "cells": cells,
        "peak": peak,
        "max_value": round(max_value, 2),
        "labels": {
            "dow": ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"],
            "hours": list(range(24)),
        },
    }


# ---------------------------------------------------------------------------
# /api/analysis/branch_peak  — Per-branch peak hour/dow summary for Time & Flow
# ---------------------------------------------------------------------------
@router.get("/api/analysis/branch_peak")
@ttl_cache
def analysis_branch_peak(
    month: Optional[str] = Query(None),
    month_from: Optional[str] = Query(None),
    month_to: Optional[str] = Query(None),
    metric: str = Query("revenue"),
):
    """Returns per-branch peak hour, peak DOW, and hourly+DOW distribution for Time & Flow tab."""
    filters = ["st.source='Easy'", "b.name != 'ALL'"]
    params: dict = {}
    _month_range_filter(filters, params, month_from, month_to, month,
                        col="strftime('%Y-%m', st.sold_at)")
    where = "WHERE " + " AND ".join(filters)

    with get_db() as con:
        rows = con.execute(f"""
            SELECT
                b.name AS branch,
                CAST(strftime('%H', st.sold_at) AS INTEGER)  AS hour,
                CAST(strftime('%w', st.sold_at) AS INTEGER)  AS dow,
                COUNT(*) AS tx_count,
                COALESCE(SUM(st.customer_count), 0) AS customer_count,
                ROUND({net_revenue_sum('st')}, 2) AS revenue,
                ROUND(AVG{net_revenue_row('st')}, 2) AS avg_ticket,
                COUNT(DISTINCT DATE(st.sold_at)) AS day_count
            FROM sales_transaction st
            JOIN branches b ON b.branch_id = st.branch_id
            {where}
            GROUP BY b.name, hour, dow
        """, params).fetchall()

    if not rows:
        return {"branches": []}

    DOW_LABELS = ["อา", "จ", "อ", "พ", "พฤ", "ศ", "ส"]

    def get_val(r):
        if metric == "tx_count":      return (r["tx_count"] or 0) / max(r["day_count"] or 1, 1)
        if metric == "customer_count":return (r["customer_count"] or 0) / max(r["day_count"] or 1, 1)
        if metric == "avg_ticket":    return r["avg_ticket"] or 0
        return (r["revenue"] or 0) / max(r["day_count"] or 1, 1)

    # Group by branch
    from collections import defaultdict
    branch_data: dict = defaultdict(list)
    for r in rows:
        branch_data[r["branch"]].append({"hour": r["hour"], "dow": r["dow"], "val": get_val(r)})

    result = []
    for br, cells in branch_data.items():
        # Peak hour (collapse over all DOWs)
        hour_agg: dict = defaultdict(float)
        dow_agg: dict = defaultdict(float)
        for c in cells:
            hour_agg[c["hour"]] += c["val"]
            dow_agg[c["dow"]]   += c["val"]
        peak_hour = max(hour_agg, key=hour_agg.__getitem__) if hour_agg else None
        peak_dow  = max(dow_agg,  key=dow_agg.__getitem__)  if dow_agg  else None

        # Hourly array (sum val across DOWs, 0-23)
        hourly = [round(hour_agg.get(h, 0), 1) for h in range(24)]
        # DOW array (0=Sun..6=Sat)
        dow_arr = [round(dow_agg.get(d, 0), 1) for d in range(7)]
        max_val = max(hourly) if hourly else 1

        result.append({
            "branch": br,
            "peak_hour": peak_hour,
            "peak_dow": peak_dow,
            "peak_dow_label": DOW_LABELS[peak_dow] if peak_dow is not None else None,
            "hourly": hourly,
            "dow": dow_arr,
            "max_val": max_val,
        })

    result.sort(key=lambda x: x["branch"])
    return {"branches": result, "metric": metric}


@ttl_cache
def table_turnover(
    month:      Optional[str] = Query(None),
    month_from: Optional[str] = Query(None),
    month_to:   Optional[str] = Query(None),
    branch:     Optional[str] = Query(None),
):
    """
    Estimate table turnover and dwell time from sales_transaction.
    - turns_per_day: avg number of bills per table per operating day
    - est_dwell_min: median gap (minutes) between consecutive bills at the same table on the same day
    - peak_hour distribution of first bills
    """
    filters = ["b.name != 'ALL'", "TRIM(COALESCE(st.table_no,'')) != ''", "st.source = 'Easy'"]
    params: dict = {}
    if branch:
        filters.append("b.name = :branch")
        params["branch"] = branch
    _month_range_filter(filters, params, month_from, month_to, month,
                        col="strftime('%Y-%m', st.sold_at)")
    where = "WHERE " + " AND ".join(filters)

    with get_db() as con:
        # Bills per table per day per branch
        day_rows = con.execute(
            f"""
            SELECT b.name AS branch,
                   st.table_no,
                   DATE(st.sold_at)                     AS day,
                   COUNT(*)                              AS bill_count,
                   MIN(st.sold_at)                       AS first_bill,
                   MAX(st.sold_at)                       AS last_bill,
                   ROUND({net_revenue_sum('st')}, 2)      AS day_revenue,
                   SUM(st.customer_count)               AS day_pax
            FROM sales_transaction st
            JOIN branches b ON b.branch_id = st.branch_id
            {where}
            GROUP BY b.name, st.table_no, DATE(st.sold_at)
            ORDER BY b.name, day
            """,
            params,
        ).fetchall()

    if not day_rows:
        return {"available": False}

    from datetime import datetime as _dt

    # Per-branch aggregations
    branch_stats: dict = {}
    all_gaps: list = []

    for r in day_rows:
        br = r["branch"]
        bs = branch_stats.setdefault(br, {
            "total_table_days": 0, "total_bills": 0,
            "total_revenue": 0.0, "total_pax": 0,
            "gaps_min": [], "turns_list": [],
        })
        bs["total_table_days"] += 1
        bs["total_bills"]      += r["bill_count"]
        bs["total_revenue"]    += r["day_revenue"] or 0
        bs["total_pax"]        += r["day_pax"] or 0
        bs["turns_list"].append(r["bill_count"])

        # Gap estimate: if >=2 bills, (last_bill - first_bill) / (bills - 1) = avg gap
        if r["bill_count"] >= 2:
            try:
                t1 = _dt.fromisoformat(r["first_bill"])
                t2 = _dt.fromisoformat(r["last_bill"])
                span_min = (t2 - t1).total_seconds() / 60
                avg_gap  = span_min / (r["bill_count"] - 1)
                if 0 < avg_gap < 600:  # ignore gaps > 10 hours (data noise)
                    bs["gaps_min"].append(avg_gap)
                    all_gaps.append(avg_gap)
            except Exception:
                pass

    def _median(lst):
        if not lst: return None
        s = sorted(lst)
        n = len(s)
        return round(s[n // 2] if n % 2 else (s[n//2-1]+s[n//2])/2, 1)

    result_branches = []
    for br, bs in sorted(branch_stats.items(), key=lambda x: -x[1]["total_revenue"]):
        tds  = bs["total_table_days"] or 1
        result_branches.append({
            "branch":            br,
            "avg_turns_per_day": round(bs["total_bills"] / tds, 2),
            "est_dwell_min":     _median(bs["gaps_min"]),
            "avg_revenue_per_table_day": round(bs["total_revenue"] / tds, 2),
            "avg_pax_per_table_day":     round(bs["total_pax"] / tds, 2),
        })

    overall_turns  = round(sum(r["avg_turns_per_day"] for r in result_branches) / max(len(result_branches), 1), 2)
    overall_dwell  = _median(all_gaps)

    return {
        "available":     True,
        "overall": {
            "avg_turns_per_day": overall_turns,
            "est_dwell_min":     overall_dwell,
        },
        "by_branch": result_branches,
    }


# ---------------------------------------------------------------------------
# GET /api/analysis/daypart-economics  (Analysis #4)
# ---------------------------------------------------------------------------
@router.get("/api/analysis/daypart-economics")
@ttl_cache
def daypart_economics(
    branch:     Optional[str] = Query(None),
    month:      Optional[str] = Query(None),
    month_from: Optional[str] = Query(None),
    month_to:   Optional[str] = Query(None),
):
    """Per-daypart economics from transactions: ex-VAT revenue, covers, spend/cover,
    food/drink/other mix and drink attach. Includes an ESTIMATED gross profit using
    transparent component margins (drinks carry more margin than food) — true
    COGS-by-daypart isn't possible (transactions have no SKU-level detail)."""
    DRINK_GP, FOODOTHER_GP = 0.70, 0.45   # assumed contribution rates (documented)
    filters = ["b.name != 'ALL'", "st.source='Easy'"]
    params: dict = {}
    if branch:
        filters.append("b.name = :branch"); params["branch"] = branch
    _month_range_filter(filters, params, month_from, month_to, month, col="SUBSTR(st.sold_at,1,7)")
    where = "WHERE " + " AND ".join(filters)
    # Finer daypart buckets (5 instead of 3) for more detail.
    bucket = ("CASE "
              "WHEN CAST(strftime('%H', st.sold_at) AS INT) < 11 THEN 'morning' "
              "WHEN CAST(strftime('%H', st.sold_at) AS INT) BETWEEN 11 AND 14 THEN 'lunch' "
              "WHEN CAST(strftime('%H', st.sold_at) AS INT) BETWEEN 15 AND 17 THEN 'afternoon' "
              "WHEN CAST(strftime('%H', st.sold_at) AS INT) BETWEEN 18 AND 21 THEN 'dinner' "
              "ELSE 'late' END")
    with get_db() as con:
        rows = con.execute(f"""
            SELECT {bucket} AS daypart,
                   COUNT(*)                       AS bills,
                   SUM(st.customer_count)         AS covers,
                   SUM(st.food_subtotal)          AS food,
                   SUM(st.drink_subtotal)         AS drink,
                   SUM(st.other_subtotal)         AS other,
                   {net_revenue_sum('st')}   AS revenue
            FROM sales_transaction st
            JOIN branches b ON b.branch_id = st.branch_id
            {where}
            GROUP BY daypart
        """, params).fetchall()
    labels = {"morning": "เช้า (ก่อน 11)", "lunch": "กลางวัน (11-14)",
              "afternoon": "บ่าย (15-17)", "dinner": "เย็น (18-21)", "late": "ดึก (22+)"}
    total_rev = sum((r["revenue"] or 0) for r in rows) or 1
    out = []
    for r in rows:
        food = r["food"] or 0; drink = r["drink"] or 0; other = r["other"] or 0
        rev = r["revenue"] or 0; covers = r["covers"] or 0; bills = r["bills"] or 0
        est_gp = drink * DRINK_GP + (food + other) * FOODOTHER_GP
        out.append({
            "daypart":       r["daypart"],
            "label":         labels.get(r["daypart"], r["daypart"]),
            "bills":         bills,
            "covers":        covers,
            "revenue":       round(rev, 2),
            "revenue_pct":   round(rev / total_rev * 100, 1),
            "rev_per_cover": round(rev / covers, 2) if covers else None,
            "avg_bill":      round(rev / bills, 2) if bills else None,
            "covers_per_bill": round(covers / bills, 2) if bills else None,
            "drink_pct":     round(drink / (food + drink + other) * 100, 1) if (food + drink + other) else 0.0,
            "mix": {
                "food":  round(food, 2), "drink": round(drink, 2), "other": round(other, 2),
            },
            "est_gross_profit": round(est_gp, 2),
            "est_gp_margin_pct": round(est_gp / rev * 100, 1) if rev else None,
        })
    order = {"morning": 0, "lunch": 1, "afternoon": 2, "dinner": 3, "late": 4}
    out.sort(key=lambda x: order.get(x["daypart"], 9))
    return {"dayparts": out,
            "assumptions": {"drink_gp": DRINK_GP, "food_other_gp": FOODOTHER_GP,
                            "note": "est_gross_profit is an estimate using fixed component rates"}}


# ---------------------------------------------------------------------------
# GET /api/analysis/table-turnover  (Analysis #7)
# ---------------------------------------------------------------------------
@ttl_cache
def table_turnover_branches(   # distinct __name__ — was 'table_turnover', which collided
    branch:     Optional[str] = Query(None),   # in ttl_cache with /api/analysis/table_turnover
    month:      Optional[str] = Query(None),    # and served its wrong-shaped cached response
    month_from: Optional[str] = Query(None),    # (the More-tab "no data" bug).
    month_to:   Optional[str] = Query(None),
):
    """Per-branch table turnover from the POS turn-time feed (pos_table_turn_day).
    Source change: the MSSQL sync leaves sales_transaction.table_no empty, so the
    old bill-gap method returned nothing; pos_table_turn_day carries the real
    avg_turn_min + table-occasions + covers per branch/day."""
    filters = ["b.name != 'ALL'"]
    params: dict = {}
    if branch:
        filters.append("b.name = :branch"); params["branch"] = branch
    _month_range_filter(filters, params, month_from, month_to, month, col="SUBSTR(x.day,1,7)")
    where = "WHERE " + " AND ".join(filters)
    with get_db() as con:
        rows = con.execute(f"""
            SELECT b.name AS branch,
                   AVG(x.avg_turn_min)                              AS avg_turn_min,
                   SUM(x.tabs)                                       AS turns,
                   SUM(x.customers) * 1.0 / NULLIF(SUM(x.tabs), 0)   AS avg_covers
            FROM pos_table_turn_day x
            JOIN branches b ON b.branch_id = x.branch_id
            {where}
            GROUP BY b.name
        """, params).fetchall()
    out = []
    for r in sorted(rows, key=lambda x: (x["avg_turn_min"] or 9e9)):
        out.append({
            "branch":        r["branch"],
            "avg_turn_min":  round(r["avg_turn_min"], 1) if r["avg_turn_min"] is not None else None,
            "turns":         int(r["turns"]) if r["turns"] is not None else 0,
            "avg_covers_per_turn": round(r["avg_covers"], 1) if r["avg_covers"] is not None else None,
        })
    return {"branches": out, "note": "จาก POS turn-time feed (pos_table_turn_day) · นาที/รอบ เฉลี่ย, รอบ = table-occasions รวม"}


# ---------------------------------------------------------------------------
# GET /api/buffet/gp/export   ●7
# Download GP analysis as formatted Excel
# ---------------------------------------------------------------------------




@router.get("/api/analysis/table-turnover")
def table_turnover_unified(
    view:       str           = Query("branches", pattern="^(branches|dwell)$"),
    branch:     Optional[str] = Query(None),
    month:      Optional[str] = Query(None),
    month_from: Optional[str] = Query(None),
    month_to:   Optional[str] = Query(None),
):
    """view=branches → per-branch POS turn-time feed (default; same shape the
    path always returned). view=dwell → bill-gap dwell/turns estimate (the old
    /api/analysis/table_turnover, used by the Time & Flow tab)."""
    if view == "dwell":
        return table_turnover(month=month, month_from=month_from,
                              month_to=month_to, branch=branch)
    return table_turnover_branches(branch=branch, month=month,
                                   month_from=month_from, month_to=month_to)
