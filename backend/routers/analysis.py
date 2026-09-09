"""Analysis domain router — the overview / period / forecast / customer family of
/api/analysis/*: daily, gross profit, compare, persona, branches, period compare,
anomalies, health, yoy, forecast, cohort, affinity, percentiles, spend per head,
food-cost trend, buffet mix. Moved VERBATIM out of main.py (P1, step 2); since
2026-09-05 the discounts family lives in routers/analysis_discounts.py and the
time-of-day / flow family in routers/analysis_time.py (each with its own router,
registered by main.py right after this one). URLs unchanged.
"""
# Shared infrastructure and helpers come from core.py by name — the old
# `globals().update(getattr(main, …))` bootstrap is gone (Phase 2, 2026-09).
from fastapi import HTTPException, Query
from typing import Dict, List, Optional
from rules import avg_ticket_sql, net_revenue_row, net_revenue_sum
from rules import (GRAND_MENU_FROM, PRICE_TIERS_GRAND, base_to_net, is_grand_menu,
                   tier_of_price)
from core import (
    _FOOD_CATEGORY_FILTER,
    _cogs_case,
    _effective_cost_subquery,
    _group_filter,
    _month_range_filter,
    _ols_regression,
    _partial_months,
    _unified_cogs_expr,
    _ym_parts,
    ex_vat,
    get_db,
    ttl_cache,
)

from fastapi import APIRouter
router = APIRouter()


# ---------------------------------------------------------------------------
# /api/analysis/daily   -  day-by-day sales for a given month
# ---------------------------------------------------------------------------
@router.get("/api/analysis/daily")
def analysis_daily(
    month: str = Query(..., description="YYYY-MM"),
    branch: Optional[str] = Query(None),
):
    """
    Daily revenue, tx count, and customer count for each calendar day
    in the given month (Easy transaction-level data).
    """
    filters = ["b.name != 'ALL'", "strftime('%Y-%m', st.sold_at) = :month"]
    params: dict = {"month": month}
    if branch:
        filters.append("b.name = :branch")
        params["branch"] = branch

    where = "WHERE " + " AND ".join(filters)

    with get_db() as con:
        rows = con.execute(
            f"""
            SELECT strftime('%Y-%m-%d', st.sold_at) AS day,
                   COUNT(*)                          AS tx_count,
                   SUM(st.customer_count)            AS customer_count,
                   ROUND({net_revenue_sum('st')}, 2)  AS revenue
            FROM sales_transaction st
            JOIN branches b ON b.branch_id = st.branch_id
            {where}
            GROUP BY day
            ORDER BY day
            """,
            params,
        ).fetchall()

    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# /api/analysis/gross_profit   -  GP (Revenue − COGS) at daily/weekly/monthly
# ---------------------------------------------------------------------------
@router.get("/api/analysis/gross_profit")
@ttl_cache
def analysis_gross_profit(
    branch:      Optional[str] = Query(None),
    granularity: str           = Query("daily"),   # daily | weekly | monthly
    month:       Optional[str] = Query(None),
    month_from:  Optional[str] = Query(None),
    month_to:    Optional[str] = Query(None),
    date_from:   Optional[str] = Query(None),
    date_to:     Optional[str] = Query(None),
):
    """
    Gross profit = Revenue (ex-VAT) − COGS (qty × sku_cost.unit_cost).

    Monthly: computed directly from sales_sku_monthly + sku_cost.
    Daily/Weekly: COGS is prorated by daily revenue share within each
    (branch, month) — exact at month level, distributed by sales mix per day.
    """
    granularity = (granularity or "daily").lower()

    # ---- monthly: direct, no proration ------------------------------------
    if granularity == "monthly":
        filters = ["b.name != 'ALL'"]
        params: dict = {}
        if branch:
            filters.append("b.name = :branch")
            params["branch"] = branch
        _month_range_filter(filters, params, month_from, month_to, month, col="sm.period_month")
        where = "WHERE " + " AND ".join(filters)
        with get_db() as con:
            rows = con.execute(
                f"""
                SELECT sm.period_month AS period,
                       ROUND(SUM(sm.subtotal_ex_vat), 2)                          AS revenue,
                       ROUND(SUM({_cogs_case(ssm='sm', sk='sk', sc='sc')}), 2)     AS cogs,
                       SUM(CASE WHEN {_effective_cost_subquery(ssm='sm', sc='sc')} IS NULL THEN sm.subtotal_ex_vat ELSE 0 END)
                                                                                   AS uncosted_rev
                FROM v_sales_sku_monthly sm
                JOIN branches b ON b.branch_id = sm.branch_id
                JOIN skus sk ON sk.sku_id = sm.sku_id
                LEFT JOIN sku_cost sc ON sc.sku_id = sm.sku_id
                {where}
                GROUP BY sm.period_month
                ORDER BY sm.period_month
                """,
                params,
            ).fetchall()
        out = []
        for r in rows:
            rev  = r["revenue"] or 0.0
            cogs = r["cogs"] or 0.0
            gp   = round(rev - cogs, 2)
            pct  = round(gp / rev * 100, 1) if rev else 0.0
            out.append({
                "period":       r["period"],
                "revenue":      rev,
                "cogs":         cogs,
                "gross_profit": gp,
                "margin_pct":   pct,
                "uncosted_rev": round(r["uncosted_rev"] or 0.0, 2),
            })
        return out

    # ---- daily / weekly: prorate COGS by daily revenue share --------------
    # Step 1: per-(branch, month) revenue + cogs from sales_sku_monthly
    bm_filters = ["b.name != 'ALL'"]
    bm_params: dict = {}
    if branch:
        bm_filters.append("b.name = :branch")
        bm_params["branch"] = branch
    # If date range supplied, derive month range from it; otherwise use month_*
    mf = month_from or month or (date_from[:7] if date_from else None)
    mt = month_to   or month or (date_to[:7]   if date_to   else None)
    if mf:
        bm_filters.append("sm.period_month >= :mf")
        bm_params["mf"] = mf
    if mt:
        bm_filters.append("sm.period_month <= :mt")
        bm_params["mt"] = mt
    bm_where = "WHERE " + " AND ".join(bm_filters)

    # Step 2: per-day revenue from sales_transaction (Easy only — only source with per-tx data)
    tx_filters = ["b.name != 'ALL'", "st.source = 'Easy'"]
    tx_params: dict = {}
    if branch:
        tx_filters.append("b.name = :branch")
        tx_params["branch"] = branch
    if date_from:
        tx_filters.append("DATE(st.sold_at) >= :date_from")
        tx_params["date_from"] = date_from
    if date_to:
        tx_filters.append("DATE(st.sold_at) <= :date_to")
        tx_params["date_to"] = date_to
    if mf and not date_from:
        tx_filters.append("strftime('%Y-%m', st.sold_at) >= :mf")
        tx_params["mf"] = mf
    if mt and not date_to:
        tx_filters.append("strftime('%Y-%m', st.sold_at) <= :mt")
        tx_params["mt"] = mt
    tx_where = "WHERE " + " AND ".join(tx_filters)

    with get_db() as con:
        bm_rows = con.execute(
            f"""
            SELECT b.name AS branch, sm.period_month AS m,
                   SUM(sm.subtotal_ex_vat) AS rev_ex,
                   SUM(sm.total_inc_vat)   AS rev_inc,
                   SUM({_cogs_case(ssm='sm', sk='sk', sc='sc')}) AS cogs
            FROM v_sales_sku_monthly sm
            JOIN branches b ON b.branch_id = sm.branch_id
            JOIN skus sk ON sk.sku_id = sm.sku_id
            LEFT JOIN sku_cost sc ON sc.sku_id = sm.sku_id
            {bm_where}
            GROUP BY b.name, sm.period_month
            """,
            bm_params,
        ).fetchall()

        day_rows = con.execute(
            f"""
            SELECT b.name AS branch,
                   DATE(st.sold_at) AS day,
                   strftime('%Y-%m', st.sold_at) AS m,
                   SUM(st.total_inc_vat) AS rev_inc
            FROM sales_transaction st
            JOIN branches b ON b.branch_id = st.branch_id
            {tx_where}
            GROUP BY b.name, DATE(st.sold_at)
            ORDER BY day
            """,
            tx_params,
        ).fetchall()

    # Build per-(branch, month) totals
    bm_map: Dict = {}
    for r in bm_rows:
        bm_map[(r["branch"], r["m"])] = {
            "rev_ex":  r["rev_ex"]  or 0.0,
            "rev_inc": r["rev_inc"] or 0.0,
            "cogs":    r["cogs"]    or 0.0,
        }

    # Aggregate daily-tx revenue per (branch, month) so we can compute share
    bm_daily_total_inc: Dict = {}
    for r in day_rows:
        k = (r["branch"], r["m"])
        bm_daily_total_inc[k] = bm_daily_total_inc.get(k, 0.0) + (r["rev_inc"] or 0.0)

    # Build daily output with prorated COGS
    daily_out = []
    for r in day_rows:
        k = (r["branch"], r["m"])
        bm = bm_map.get(k)
        day_rev_inc = r["rev_inc"] or 0.0
        if not bm or bm["rev_inc"] <= 0 or bm_daily_total_inc.get(k, 0) <= 0:
            # No monthly costing info or no monthly revenue — fall back to day_rev_ex with 0 COGS
            day_rev_ex = ex_vat(day_rev_inc)
            daily_out.append({
                "branch":       r["branch"],
                "day":          r["day"],
                "period_month": r["m"],
                "revenue":      round(day_rev_ex, 2),
                "revenue_inc":  round(day_rev_inc, 2),
                "cogs":         None,   # fix C10: None so aggregates use `or 0` guard, not inflate GP
                "gross_profit": None,
                "margin_pct":   None,
            })
            continue
        share = day_rev_inc / bm["rev_inc"]   # fraction of full-month revenue — correct proration (fix: C1)
        day_cogs = bm["cogs"] * share
        # Day revenue ex-VAT: use the same share against monthly ex-VAT (keeps GP consistent)
        day_rev_ex = bm["rev_ex"] * share
        gp = day_rev_ex - day_cogs
        pct = (gp / day_rev_ex * 100) if day_rev_ex else None
        daily_out.append({
            "branch":       r["branch"],
            "day":          r["day"],
            "period_month": r["m"],
            "revenue":      round(day_rev_ex, 2),
            "revenue_inc":  round(day_rev_inc, 2),
            "cogs":         round(day_cogs, 2),
            "gross_profit": round(gp, 2),
            "margin_pct":   round(pct, 1) if pct is not None else None,
        })

    if granularity == "daily":
        # Roll up across branches per day (in case branch=None)
        agg: Dict = {}
        for d in daily_out:
            k = d["day"]
            a = agg.setdefault(k, {"period": k, "revenue": 0.0, "cogs": 0.0, "gross_profit": 0.0})
            a["revenue"]      += d["revenue"]
            a["cogs"]         += d["cogs"]
            a["gross_profit"] += d["gross_profit"]
        out = []
        for k in sorted(agg.keys()):
            a = agg[k]
            pct = round(a["gross_profit"] / a["revenue"] * 100, 1) if a["revenue"] else None
            out.append({
                "period":       a["period"],
                "revenue":      round(a["revenue"], 2),
                "cogs":         round(a["cogs"], 2),
                "gross_profit": round(a["gross_profit"], 2),
                "margin_pct":   pct,
            })
        return out

    # weekly: ISO-8601 week key 'YYYY-Www' (matches _iso_week_sql used by the SQL paths)
    if granularity == "weekly":
        from datetime import date as _date
        agg = {}
        for d in daily_out:
            try:
                y, mo, dd = d["day"].split("-")
                iso = _date(int(y), int(mo), int(dd)).isocalendar()
                wk = f"{iso[0]}-W{iso[1]:02d}"
            except Exception:
                continue
            a = agg.setdefault(wk, {"period": wk, "revenue": 0.0, "cogs": 0.0,
                                    "gross_profit": 0.0, "week_start": d["day"]})
            a["revenue"]      += d["revenue"]
            a["cogs"]         += d["cogs"]
            a["gross_profit"] += d["gross_profit"]
            if d["day"] < a["week_start"]:
                a["week_start"] = d["day"]
        out = []
        for k in sorted(agg.keys()):
            a = agg[k]
            pct = round(a["gross_profit"] / a["revenue"] * 100, 1) if a["revenue"] else None
            out.append({
                "period":       a["period"],
                "week_start":   a["week_start"],
                "revenue":      round(a["revenue"], 2),
                "cogs":         round(a["cogs"], 2),
                "gross_profit": round(a["gross_profit"], 2),
                "margin_pct":   pct,
            })
        return out

    raise HTTPException(400, f"Unknown granularity: {granularity}")


# ---------------------------------------------------------------------------
# /api/analysis/compare   -  KPI comparison between two periods
# ---------------------------------------------------------------------------
@router.get("/api/analysis/compare")
@ttl_cache
def analysis_compare(
    month: str = Query(..., description="Primary period YYYY-MM"),
    vs: str = Query(..., description="Comparison period YYYY-MM"),
    branch: Optional[str] = Query(None),
):
    """
    Returns KPI objects for `month` and `vs` side by side:
    revenue, revenue_ex_vat, tx_count, customer_count, avg_ticket,
    gross_sales, net_sales, discounts.
    Pulls from sales_sku_monthly + sales_month_summary (Easy).
    """
    import calendar as _cal
    def fetch_kpis(period: str, con, mtd_cut=None) -> dict:
        branch_filter = "AND b.name = :branch" if branch else ""
        p: dict = {"period": period}
        if branch:
            p["branch"] = branch

        if mtd_cut:
            # MTD-fair: compare the SAME elapsed days, all from sales_transaction
            # (day-capable) so a partial current month isn't matched to a full month.
            p["s"] = f"{period}-01"; p["e"] = f"{period}-{mtd_cut:02d}"
            r = con.execute(f"""
                SELECT ROUND({net_revenue_sum('st')}, 2) revenue, COUNT(*) tx_count,
                       SUM(st.customer_count) customer_count, ROUND({avg_ticket_sql('st')}, 2) avg_ticket
                FROM sales_transaction st JOIN branches b ON b.branch_id=st.branch_id
                WHERE b.name!='ALL' AND st.source='Easy'
                  AND date(st.sold_at) BETWEEN :s AND :e {branch_filter}""", p).fetchone()
            _rev = (r["revenue"] or 0) if r else 0
            return {"period": period, "revenue": _rev, "revenue_ex": _rev,
                    "tx_count": (r["tx_count"] or 0) if r else 0,
                    "customer_count": (r["customer_count"] or 0) if r else 0,
                    "avg_ticket": (r["avg_ticket"] or 0) if r else 0,
                    "gross_sales": 0, "net_sales": _rev, "discounts": 0}

        rev = con.execute(
            f"""
            SELECT ROUND(SUM(sm.subtotal_ex_vat), 2)   AS revenue,
                   ROUND(SUM(sm.subtotal_ex_vat), 2)  AS revenue_ex_vat
            FROM v_sales_sku_monthly sm
            JOIN branches b ON b.branch_id = sm.branch_id
            WHERE b.name != 'ALL' AND sm.period_month = :period
              {branch_filter}
            """,
            p,
        ).fetchone()

        tx = con.execute(
            f"""
            SELECT COUNT(*) AS tx_count,
                   SUM(customer_count) AS customer_count,
                   ROUND({avg_ticket_sql('')}, 2) AS avg_ticket
            FROM sales_transaction st
            JOIN branches b ON b.branch_id = st.branch_id
            WHERE b.name != 'ALL' AND strftime('%Y-%m', st.sold_at) = :period
              {branch_filter}
            """,
            p,
        ).fetchone()

        sms = con.execute(
            f"""
            SELECT ROUND(SUM(gross_sales), 2) AS gross_sales,
                   ROUND(SUM(net_sales), 2)   AS net_sales,
                   ROUND(SUM(discounts), 2)   AS discounts,
                   SUM(transaction_count)     AS tx_count_sms,
                   SUM(customer_count)        AS customer_count_sms
            FROM sales_month_summary sms
            JOIN branches b ON b.branch_id = sms.branch_id
            WHERE b.name != 'ALL' AND sms.period_month = :period
              {branch_filter}
            """,
            p,
        ).fetchone()

        # Use SKU/transaction data when available, else fall back to the monthly summary
        revenue     = (rev["revenue"]        or 0) if rev else 0
        revenue_ex  = (rev["revenue_ex_vat"] or 0) if rev else 0
        tx_count    = (tx["tx_count"]        or 0) if tx else 0
        cust_count  = (tx["customer_count"]  or 0) if tx else 0
        avg_ticket  = (tx["avg_ticket"]      or 0) if tx else 0

        if revenue == 0 and sms:
            revenue    = sms["net_sales"]     or 0
            revenue_ex = sms["net_sales"]     or 0
        if tx_count == 0 and sms:
            tx_count   = sms["tx_count_sms"]  or 0
            cust_count = sms["customer_count_sms"] or 0
            avg_ticket = round(revenue / tx_count, 2) if tx_count else 0

        return {
            "period":       period,
            "revenue":      revenue,
            "revenue_ex":   revenue_ex,
            "tx_count":     tx_count,
            "customer_count": cust_count,
            "avg_ticket":   avg_ticket,
            "gross_sales":  (sms["gross_sales"] or 0) if sms else 0,
            "net_sales":    (sms["net_sales"]   or 0) if sms else 0,
            "discounts":    (sms["discounts"]   or 0) if sms else 0,
        }

    def pct_change(a, b):
        if b == 0:
            return None
        return round((a - b) / b * 100, 1)

    with get_db() as con:
        # MTD-fair when the PRIMARY month is the latest, still-partial month.
        maxd = con.execute("SELECT MAX(date(sold_at)) FROM sales_transaction WHERE source='Easy'").fetchone()[0]
        mtd_cut = None
        if maxd:
            my, mm, mdd = (int(x) for x in maxd.split("-"))
            try:
                py, pm = (int(x) for x in month.split("-")[:2])
                if (py, pm) == (my, mm) and mdd < _cal.monthrange(my, mm)[1]:
                    mtd_cut = mdd
            except Exception:
                mtd_cut = None
        current = fetch_kpis(month, con, mtd_cut)
        prior   = fetch_kpis(vs, con, mtd_cut)

    return {
        "current": current,
        "prior":   prior,
        "mtd": mtd_cut is not None, "mtd_cutoff_day": mtd_cut,
        "delta": {
            "revenue":        pct_change(current["revenue"],        prior["revenue"]),
            "tx_count":       pct_change(current["tx_count"],       prior["tx_count"]),
            "customer_count": pct_change(current["customer_count"], prior["customer_count"]),
            "avg_ticket":     pct_change(current["avg_ticket"],     prior["avg_ticket"]),
        },
    }


# ---------------------------------------------------------------------------
# /api/analysis/persona   -  customer behaviour stats
# ---------------------------------------------------------------------------
@router.get("/api/analysis/persona")
@ttl_cache
def analysis_persona(
    month:      Optional[str] = Query(None),
    month_from: Optional[str] = Query(None),
    month_to:   Optional[str] = Query(None),
    branch:     Optional[str] = Query(None),
    group:      Optional[str] = Query(None),
):
    """
    Customer behaviour from sales_transaction:
    - Party size distribution (1,2,3,4,5,6+)
    - Avg spend per head by party size
    - Food vs drink vs other split (% of subtotals)
    - Busy hours (0-23 tx count)
    """
    filters = ["b.name != 'ALL'"]
    params: dict = {}
    if branch:
        filters.append("b.name = :branch")
        params["branch"] = branch
    _group_filter(filters, params, group)
    _month_range_filter(filters, params, month_from, month_to, month, col="strftime('%Y-%m', st.sold_at)")

    where = "WHERE " + " AND ".join(filters)

    with get_db() as con:
        # Party size distribution
        party_rows = con.execute(
            f"""
            SELECT CASE
                     WHEN customer_count <= 0 THEN 1
                     WHEN customer_count >= 6 THEN 6
                     ELSE customer_count
                   END AS party_size,
                   COUNT(*)                          AS tx_count,
                   ROUND(AVG({net_revenue_row('')}), 2)     AS avg_bill,
                   ROUND(AVG({net_revenue_row('')} / CASE WHEN customer_count <= 0 THEN 1
                                                   ELSE customer_count END), 2) AS avg_per_head
            FROM sales_transaction st
            JOIN branches b ON b.branch_id = st.branch_id
            {where}
            GROUP BY party_size
            ORDER BY party_size
            """,
            params,
        ).fetchall()

        # Food / drink / other split
        split_row = con.execute(
            f"""
            SELECT ROUND(SUM(food_subtotal), 2)  AS food,
                   ROUND(SUM(drink_subtotal), 2) AS drink,
                   ROUND(SUM(other_subtotal), 2) AS other
            FROM sales_transaction st
            JOIN branches b ON b.branch_id = st.branch_id
            {where}
            """,
            params,
        ).fetchone()

        # Busy hours
        hour_rows = con.execute(
            f"""
            SELECT CAST(strftime('%H', st.sold_at) AS INTEGER) AS hour,
                   COUNT(*) AS tx_count,
                   SUM(customer_count) AS customer_count
            FROM sales_transaction st
            JOIN branches b ON b.branch_id = st.branch_id
            {where}
            GROUP BY hour
            ORDER BY hour
            """,
            params,
        ).fetchall()

        # Overall stats
        overall = con.execute(
            f"""
            SELECT COUNT(*)                          AS tx_count,
                   ROUND(AVG(customer_count), 2)    AS avg_party_size,
                   ROUND(AVG({net_revenue_row('')}), 2)     AS avg_ticket,
                   ROUND({net_revenue_sum('st')} /
                         NULLIF(SUM(customer_count), 0), 2) AS avg_per_head
            FROM sales_transaction st
            JOIN branches b ON b.branch_id = st.branch_id
            {where}
            """,
            params,
        ).fetchone()

    hours_map = {h: 0 for h in range(24)}
    for r in hour_rows:
        hours_map[r["hour"]] = r["tx_count"]

    food  = (split_row["food"]  or 0) if split_row else 0
    drink = (split_row["drink"] or 0) if split_row else 0
    other = (split_row["other"] or 0) if split_row else 0
    total_split = food + drink + other or 1

    return {
        "overall": {
            "tx_count":       (overall["tx_count"]      or 0) if overall else 0,
            "avg_party_size": (overall["avg_party_size"] or 0) if overall else 0,
            "avg_ticket":     (overall["avg_ticket"]     or 0) if overall else 0,
            "avg_per_head":   (overall["avg_per_head"]   or 0) if overall else 0,
        },
        "party_sizes": [
            {
                "size":         r["party_size"],
                "label":        f"{r['party_size']}+" if r["party_size"] >= 6 else str(r["party_size"]),
                "tx_count":     r["tx_count"],
                "avg_bill":     r["avg_bill"],
                "avg_per_head": r["avg_per_head"],
            }
            for r in party_rows
        ],
        "food_drink_split": {
            "food":  round(food  / total_split * 100, 1),
            "drink": round(drink / total_split * 100, 1),
            "other": round(other / total_split * 100, 1),
            "food_abs": food, "drink_abs": drink, "other_abs": other,
        },
        "busy_hours": [{"hour": h, "tx_count": hours_map[h]} for h in range(24)],
    }


# ---------------------------------------------------------------------------
# /api/analysis/branches   -  cross-branch KPI comparison
# ---------------------------------------------------------------------------
@router.get("/api/analysis/branches")
@ttl_cache
def analysis_branches(
    month:      Optional[str] = Query(None),
    month_from: Optional[str] = Query(None),
    month_to:   Optional[str] = Query(None),
    group:      Optional[str] = Query(None),
):
    """KPIs for every branch side-by-side (Easy transaction-level data)."""
    filters_tx = ["b.name != 'ALL'"]
    params_tx: dict = {}
    _group_filter(filters_tx, params_tx, group)
    _month_range_filter(filters_tx, params_tx, month_from, month_to, month, col="strftime('%Y-%m', st.sold_at)")
    where_tx = "WHERE " + " AND ".join(filters_tx)

    with get_db() as con:
        easy_rows = con.execute(
            f"""
            SELECT b.name AS branch,
                   COUNT(*)                                           AS tx_count,
                   SUM(st.customer_count)                            AS customer_count,
                   ROUND({net_revenue_sum('st')}, 2)                  AS revenue,
                   SUM(CASE WHEN st.discount > 0 THEN 1 ELSE 0 END) AS discount_tx,
                   ROUND(SUM(st.discount), 2)                        AS total_discount
            FROM sales_transaction st
            JOIN branches b ON b.branch_id = st.branch_id
            {where_tx}
            GROUP BY b.name
            """,
            params_tx,
        ).fetchall()

    merged: dict = {}
    for r in easy_rows:
        merged[r["branch"]] = {
            "branch": r["branch"],
            "tx_count":       r["tx_count"] or 0,
            "customer_count": r["customer_count"] or 0,
            "revenue":        r["revenue"] or 0,
            "discount_tx":    r["discount_tx"] or 0,
            "total_discount": r["total_discount"] or 0,
        }

    # Compute derived metrics
    out = []
    for m in merged.values():
        avg_ticket   = round(m["revenue"] / m["tx_count"], 2) if m["tx_count"] else 0
        discount_rate = round(100.0 * m["discount_tx"] / m["tx_count"], 1) if m["tx_count"] else 0
        out.append({
            **m,
            "avg_ticket":   avg_ticket,
            "discount_rate": discount_rate,
        })
    out.sort(key=lambda r: -r["revenue"])
    return out


# ===========================================================================
# NEW ADVANCED FEATURES  -  period compare, heatmap, anomalies, health, search
# ===========================================================================

def _build_period_filters(branch, month_from, month_to, date_from, date_to,
                          col_month="strftime('%Y-%m', st.sold_at)",
                          col_date="DATE(st.sold_at)"):
    """Helper: build SQL WHERE clauses from period range params (supports both month and date)."""
    filters = ["b.name != 'ALL'"]
    params: dict = {}
    if branch:
        filters.append("b.name = :branch")
        params["branch"] = branch
    if date_from:
        filters.append(f"{col_date} >= :date_from")
        params["date_from"] = date_from
    if date_to:
        filters.append(f"{col_date} <= :date_to")
        params["date_to"] = date_to
    if month_from and not date_from:
        filters.append(f"{col_month} >= :month_from")
        params["month_from"] = month_from
    if month_to and not date_to:
        filters.append(f"{col_month} <= :month_to")
        params["month_to"] = month_to
    return filters, params


def _fetch_period_kpis(con, branch, month_from, month_to, date_from, date_to):
    """Aggregate KPIs from sales_transaction over an arbitrary period."""
    filters, params = _build_period_filters(branch, month_from, month_to, date_from, date_to)
    where = "WHERE " + " AND ".join(filters)
    row = con.execute(
        f"""
        SELECT COUNT(*)                                  AS tx_count,
               COALESCE(SUM(st.customer_count), 0)      AS customer_count,
               ROUND(COALESCE({net_revenue_sum('st')}, 0), 2) AS revenue,
               ROUND(COALESCE(AVG{net_revenue_row('st')}, 0), 2) AS avg_ticket,
               ROUND(COALESCE(SUM(st.discount), 0), 2)  AS discount,
               ROUND(COALESCE(SUM(st.food_subtotal), 0), 2)  AS food,
               ROUND(COALESCE(SUM(st.drink_subtotal), 0), 2) AS drink
        FROM sales_transaction st
        JOIN branches b ON b.branch_id = st.branch_id
        {where}
        """,
        params,
    ).fetchone()

    # Top branches in this period
    branches_top = con.execute(
        f"""
        SELECT b.name AS branch,
               ROUND({net_revenue_sum('st')}, 2) AS revenue,
               COUNT(*) AS tx_count
        FROM sales_transaction st
        JOIN branches b ON b.branch_id = st.branch_id
        {where}
        GROUP BY b.name
        ORDER BY revenue DESC
        LIMIT 10
        """,
        params,
    ).fetchall()

    # Top SKUs (uses sales_sku_monthly w/ month range only) — FOOD-ONLY so the
    # report's "Top SKUs" lists real dishes, not the deprecated BUFFET aggregate
    # or service charge (see _FOOD_CATEGORY_FILTER; needs the categories join).
    sku_filters = ["b.name != 'ALL'", _FOOD_CATEGORY_FILTER]
    sku_params: dict = {}
    if branch:
        sku_filters.append("b.name = :branch")
        sku_params["branch"] = branch
    # Convert date_from/to to month range if necessary
    mf = month_from or (date_from[:7] if date_from else None)
    mt = month_to   or (date_to[:7]   if date_to   else None)
    if mf:
        sku_filters.append("sm.period_month >= :mf")
        sku_params["mf"] = mf
    if mt:
        sku_filters.append("sm.period_month <= :mt")
        sku_params["mt"] = mt
    sku_where = "WHERE " + " AND ".join(sku_filters)
    skus_top = con.execute(
        f"""
        SELECT sk.sku_code, COALESCE(sk.description, sk.sku_code) AS description,
               ROUND(SUM(sm.subtotal_ex_vat), 2) AS revenue,
               ROUND(SUM(sm.qty), 0)            AS qty
        FROM v_sales_sku_monthly sm
        JOIN branches b ON b.branch_id = sm.branch_id
        JOIN skus sk    ON sk.sku_id    = sm.sku_id
        JOIN categories c ON c.category_id = sk.category_id
        {sku_where}
        GROUP BY sk.sku_id
        ORDER BY revenue DESC
        LIMIT 10
        """,
        sku_params,
    ).fetchall()

    return {
        "revenue":        (row["revenue"]        or 0) if row else 0,
        "tx_count":       (row["tx_count"]       or 0) if row else 0,
        "customer_count": (row["customer_count"] or 0) if row else 0,
        "avg_ticket":     (row["avg_ticket"]     or 0) if row else 0,
        "discount":       (row["discount"]       or 0) if row else 0,
        "food":           (row["food"]           or 0) if row else 0,
        "drink":          (row["drink"]          or 0) if row else 0,
        "top_branches":   [dict(b) for b in branches_top],
        "top_skus":       [dict(s) for s in skus_top],
    }


# ---------------------------------------------------------------------------
# /api/analysis/period_compare   -  A vs B range comparison
# ---------------------------------------------------------------------------
@router.get("/api/analysis/period_compare")
@ttl_cache
def period_compare(
    a_month_from: Optional[str] = Query(None),
    a_month_to:   Optional[str] = Query(None),
    a_date_from:  Optional[str] = Query(None),
    a_date_to:    Optional[str] = Query(None),
    b_month_from: Optional[str] = Query(None),
    b_month_to:   Optional[str] = Query(None),
    b_date_from:  Optional[str] = Query(None),
    b_date_to:    Optional[str] = Query(None),
    c_month_from: Optional[str] = Query(None),
    c_month_to:   Optional[str] = Query(None),
    c_date_from:  Optional[str] = Query(None),
    c_date_to:    Optional[str] = Query(None),
    d_month_from: Optional[str] = Query(None),
    d_month_to:   Optional[str] = Query(None),
    d_date_from:  Optional[str] = Query(None),
    d_date_to:    Optional[str] = Query(None),
    branch:       Optional[str] = Query(None),
):
    """Compare up to 4 arbitrary periods A/B/C/D. Returns KPIs, deltas, top branches/SKUs."""
    def pct(a, b):
        if not b: return None
        return round((a - b) / b * 100, 1)

    def _has(mf, mt, df, dt):
        return any([mf, mt, df, dt])

    def _get_rev(period_data, name):
        if not period_data: return 0
        return next((x["revenue"] for x in period_data["top_branches"] if x["branch"] == name), 0)

    with get_db() as con:
        a = _fetch_period_kpis(con, branch, a_month_from, a_month_to, a_date_from, a_date_to)
        b = _fetch_period_kpis(con, branch, b_month_from, b_month_to, b_date_from, b_date_to)
        c = _fetch_period_kpis(con, branch, c_month_from, c_month_to, c_date_from, c_date_to) \
            if _has(c_month_from, c_month_to, c_date_from, c_date_to) else None
        d = _fetch_period_kpis(con, branch, d_month_from, d_month_to, d_date_from, d_date_to) \
            if _has(d_month_from, d_month_to, d_date_from, d_date_to) else None

    # Collect all branch names across active periods
    # sorted: a set iterates in hash order, which differs between processes
    # (string hashing is randomised) — the golden harness found rows swapping.
    all_names = sorted(set(
        [br["branch"] for br in a["top_branches"]] +
        [br["branch"] for br in b["top_branches"]] +
        ([br["branch"] for br in c["top_branches"]] if c else []) +
        ([br["branch"] for br in d["top_branches"]] if d else [])
    ))

    rank_a = {br["branch"]: i + 1 for i, br in enumerate(a["top_branches"])}
    rank_b = {br["branch"]: i + 1 for i, br in enumerate(b["top_branches"])}
    rank_c = {br["branch"]: i + 1 for i, br in enumerate(c["top_branches"])} if c else {}
    rank_d = {br["branch"]: i + 1 for i, br in enumerate(d["top_branches"])} if d else {}

    branch_changes = []
    for name in all_names:
        rev_a = _get_rev(a, name)
        rev_b = _get_rev(b, name)
        rev_c = _get_rev(c, name)
        rev_d = _get_rev(d, name)
        branch_changes.append({
            "branch":     name,
            "revenue_a":  rev_a, "revenue_b": rev_b,
            "revenue_c":  rev_c, "revenue_d": rev_d,
            "delta_b_pct": pct(rev_a, rev_b),
            "delta_c_pct": pct(rev_a, rev_c) if c else None,
            "delta_d_pct": pct(rev_a, rev_d) if d else None,
            "rank_a": rank_a.get(name), "rank_b": rank_b.get(name),
            "rank_c": rank_c.get(name), "rank_d": rank_d.get(name),
            "rank_shift": (rank_b.get(name, 99) - rank_a.get(name, 99)),
        })
    branch_changes.sort(key=lambda x: -(x["revenue_a"] or 0))

    return {
        "a": {**a, "label": _period_label(a_month_from, a_month_to, a_date_from, a_date_to)},
        "b": {**b, "label": _period_label(b_month_from, b_month_to, b_date_from, b_date_to)},
        "c": {**c, "label": _period_label(c_month_from, c_month_to, c_date_from, c_date_to)} if c else None,
        "d": {**d, "label": _period_label(d_month_from, d_month_to, d_date_from, d_date_to)} if d else None,
        "delta": {
            "revenue":        pct(a["revenue"],        b["revenue"]),
            "tx_count":       pct(a["tx_count"],       b["tx_count"]),
            "customer_count": pct(a["customer_count"], b["customer_count"]),
            "avg_ticket":     pct(a["avg_ticket"],     b["avg_ticket"]),
            "discount":       pct(a["discount"],       b["discount"]),
        },
        "branch_changes": branch_changes,
    }


def _period_label(mf, mt, df, dt):
    if df or dt:
        return f"{df or '...'} â†' {dt or '...'}"
    if mf or mt:
        return f"{mf or '...'} â†' {mt or '...'}"
    return "All time"


# ---------------------------------------------------------------------------
# /api/analysis/anomalies   -  z-score outliers
# ---------------------------------------------------------------------------
@router.get("/api/analysis/anomalies")
@ttl_cache
def analysis_anomalies(
    month_from: Optional[str] = Query(None),
    month_to:   Optional[str] = Query(None),
    branch:     Optional[str] = Query(None),
    group:      Optional[str] = Query(None),
    threshold:  float         = Query(1.5, description="z-score threshold"),
):
    """
    Actionable month-on-month insights. Returns:
    - revenue spike / dip: latest month vs prior month (>= 5% MoM)
    - fastest-growing / biggest-dropping branches month-over-month
    - BoM cost % spike / dip month-over-month
    All comparisons are like-for-like consecutive months (house rule), never
    against an all-period average. Uses Easy transaction-level data.
    """
    insights: List[dict] = []

    # Restrict every sub-query to a branch group (master|franchise) when asked.
    # Two roll-up queries below are hard-coded WHEREs with no bound params, so we
    # also build a literal fragment; group is validated to a fixed allow-list first,
    # so the interpolation is injection-safe.
    if group not in ("master", "franchise"):
        group = None
    grp_sql = f" AND COALESCE(b.branch_type,'master') = '{group}'" if group else ""

    # 1. Monthly revenue z-score: Easy transaction-level
    filters_tx = ["b.name != 'ALL'"]
    params: dict = {}
    if branch:
        filters_tx.append("b.name = :branch")
        params["branch"] = branch
    _group_filter(filters_tx, params, group)
    _month_range_filter(filters_tx, params, month_from, month_to, None,
                        col="strftime('%Y-%m', st.sold_at)")
    where_tx = "WHERE " + " AND ".join(filters_tx)

    # Fair comparison (same logic as the latest-month hero): if the most recent
    # month is incomplete, compare month-to-date vs the SAME day-range of the
    # prior month instead of a partial-vs-full month. We do this by clamping ALL
    # months to day ≤ cutoff; only the last two are ever surfaced below.
    import calendar as _cal
    mtd_note = ""
    with get_db() as con:
        partial_months = _partial_months(con)
        # Base the MTD clamp on the latest FULLY-synced month, not a trailing sliver:
        # a complete latest month is then compared full-vs-full (real declines still
        # surface) while a partial current month is excluded from the series entirely.
        if partial_months:
            _mp = {f"_pm{i}": m for i, m in enumerate(partial_months)}
            _ph = ",".join(f":{k}" for k in _mp)
            maxday = con.execute(
                f"SELECT MAX(date(sold_at)) FROM sales_transaction WHERE source='Easy' "
                f"AND strftime('%Y-%m', sold_at) NOT IN ({_ph})", _mp).fetchone()[0]
        else:
            maxday = con.execute(
                "SELECT MAX(date(sold_at)) FROM sales_transaction WHERE source='Easy'").fetchone()[0]
        day_clause = ""
        if maxday:
            _y, _m, _d = (int(x) for x in maxday.split("-"))
            if _d < _cal.monthrange(_y, _m)[1]:   # latest month is partial
                day_clause = f" AND CAST(strftime('%d', st.sold_at) AS INT) <= {_d}"
                mtd_note = f" (เทียบ 1–{_d} วันเท่ากัน · MTD)"

        easy_monthly = con.execute(
            f"""
            SELECT strftime('%Y-%m', st.sold_at) AS month,
                   ROUND({net_revenue_sum('st')}, 2) AS revenue
            FROM sales_transaction st
            JOIN branches b ON b.branch_id = st.branch_id
            {where_tx}{day_clause}
            GROUP BY month
            ORDER BY month
            """,
            params,
        ).fetchall()

        # Branch MoM growth — Easy tx
        bgrowth_easy = con.execute(
            f"""
            SELECT b.name AS branch,
                   strftime('%Y-%m', st.sold_at) AS month,
                   ROUND({net_revenue_sum('st')}, 2) AS revenue
            FROM sales_transaction st
            JOIN branches b ON b.branch_id = st.branch_id
            WHERE b.name != 'ALL'{grp_sql}{day_clause}
            GROUP BY b.name, month
            ORDER BY b.name, month
            """,
        ).fetchall()

    # Monthly revenue totals (Easy)
    rev_by_month: dict = {}
    for r in easy_monthly:
        rev_by_month[r["month"]] = (rev_by_month.get(r["month"]) or 0) + (r["revenue"] or 0)
    monthly_combined = [{"month": m, "revenue": v} for m, v in sorted(rev_by_month.items())]

    # Branch growth rows (Easy) — drop not-yet-synced trailing months so a partial
    # current month cannot fake a -100% MoM for branches that have not synced yet.
    bgrowth_map: dict = {}
    for r in bgrowth_easy:
        if r["month"] in partial_months:
            continue
        bgrowth_map.setdefault(r["branch"], {})[r["month"]] = (r["revenue"] or 0)
    bgrowth = [
        {"branch": br, "month": mo, "revenue": rev}
        for br, months in bgrowth_map.items()
        for mo, rev in sorted(months.items())
    ]
    monthly = [m for m in monthly_combined if m["month"] not in partial_months]

    # Revenue MoM — use threshold param as sensitivity (fix D10)
    _thr = threshold
    if len(monthly) >= 2:
        last = monthly[-1]
        prev = monthly[-2]
        last_rev = last["revenue"] or 0
        prev_rev = prev["revenue"] or 0
        if prev_rev > 0:
            mom_pct = (last_rev - prev_rev) / prev_rev * 100
            if mom_pct >= _thr:
                insights.append({
                    "type": "spike", "severity": "good",
                    "metric": "revenue", "period": last["month"],
                    "value": last_rev, "mom_pct": round(mom_pct, 1),
                    "message": f"📈 รายได้เดือน {last['month']} โต {mom_pct:+.1f}% จากเดือน {prev['month']} — {last_rev:,.0f} (ก่อนหน้า {prev_rev:,.0f}){mtd_note}",
                })
            elif mom_pct <= -_thr:
                insights.append({
                    "type": "dip", "severity": "bad",
                    "metric": "revenue", "period": last["month"],
                    "value": last_rev, "mom_pct": round(mom_pct, 1),
                    "message": f"📉 รายได้เดือน {last['month']} ลด {mom_pct:.1f}% จากเดือน {prev['month']} — {last_rev:,.0f} (ก่อนหน้า {prev_rev:,.0f}){mtd_note}",
                })

    # Branch MoM growth
    by_branch: dict = {}
    for r in bgrowth:
        by_branch.setdefault(r["branch"], []).append(r)
    growths = []
    for name, recs in by_branch.items():
        if len(recs) >= 2:
            last = recs[-1]["revenue"] or 0
            prev = recs[-2]["revenue"] or 0
            if prev > 0:
                growths.append({
                    "branch": name, "growth_pct": round((last - prev) / prev * 100, 1),
                    "last_revenue": last, "prev_revenue": prev,
                    "last_month": recs[-1]["month"], "prev_month": recs[-2]["month"],
                })
    growths.sort(key=lambda x: -x["growth_pct"])
    if growths:
        top = growths[0]
        if top["growth_pct"] > _thr:
            insights.append({
                "type": "growth", "severity": "good", "metric": "branch_mom",
                "branch": top["branch"], "value": top["growth_pct"],
                "message": f"🚀 เติบโตเร็วสุด: {top['branch']} +{top['growth_pct']:.1f}% MoM ({top['last_revenue']:,.0f}){mtd_note}",
            })
        worst = growths[-1]
        if worst["growth_pct"] < -_thr:
            insights.append({
                "type": "drop", "severity": "bad", "metric": "branch_mom",
                "branch": worst["branch"], "value": worst["growth_pct"],
                "message": f"⚠️ ลดมากสุด: {worst['branch']} {worst['growth_pct']:.1f}% MoM ({worst['last_revenue']:,.0f}){mtd_note}",
            })

    # BoM cost % spike / dip detection (MoM delta >= 5 pp)
    # Phase 18d: migrated to _cogs_case helper so monthly cost overrides + tier
    # exclusion (incl. plain BUFFET) are unified with the rest of the app.
    try:
        with get_db() as con2:
            cost_monthly_rows = con2.execute(f"""
                SELECT s.period_month AS month,
                       ROUND(SUM({_cogs_case('s','sk','c')}), 2) AS bom_cost,
                       ROUND(SUM(s.subtotal_ex_vat), 2)          AS revenue
                FROM v_sales_sku_monthly s
                JOIN skus sk     ON sk.sku_id = s.sku_id
                LEFT JOIN sku_cost c ON c.sku_id = s.sku_id
                JOIN branches b  ON b.branch_id = s.branch_id
                WHERE b.name != 'ALL'{grp_sql}
                GROUP BY s.period_month
                ORDER BY s.period_month
            """).fetchall()
        cost_series = [
            (r["month"], r["bom_cost"], r["revenue"])
            for r in cost_monthly_rows
            if r["bom_cost"] and r["revenue"] and r["revenue"] > 0
            and r["month"] not in partial_months
        ]
        for i in range(1, len(cost_series)):
            m, bc, rev   = cost_series[i]
            _, pbc, prev = cost_series[i - 1]
            curr_pct = bc  / rev  * 100
            prev_pct = pbc / prev * 100
            delta = curr_pct - prev_pct
            if delta >= _thr:
                insights.append({
                    "type": "cost_spike", "severity": "bad",
                    "metric": "bom_cost_pct", "period": m,
                    "value": round(curr_pct, 1),
                    "message": f"⚠️ ต้นทุน BoM พุ่งในเดือน {m} — {curr_pct:.1f}% of revenue (+{delta:.1f}pp จากเดือนก่อน)",
                })
            elif delta <= -_thr:
                insights.append({
                    "type": "cost_drop", "severity": "good",
                    "metric": "bom_cost_pct", "period": m,
                    "value": round(curr_pct, 1),
                    "message": f"✅ ต้นทุน BoM ลดในเดือน {m} — {curr_pct:.1f}% of revenue ({delta:.1f}pp จากเดือนก่อน)",
                })
    except Exception as e:
        # one bad month must not blank every insight — say which one broke
        print(f"[anomalies] BoM cost-trend insight skipped: {e}")

    return {"insights": insights, "branch_growth": growths[:10]}


# ---------------------------------------------------------------------------
# /api/analysis/health   -  composite Health Score per branch
# ---------------------------------------------------------------------------
@router.get("/api/analysis/health")
def analysis_health(
    month_from: Optional[str] = Query(None),
    month_to:   Optional[str] = Query(None),
    group:      Optional[str] = Query(None),
):
    """
    Composite health score per branch (0-100):
      = 0.4 * revenue_score    (normalized vs leader)
      + 0.3 * growth_score     (last vs prior month, capped ±50%)
      + 0.2 * consistency_score (1 - coefficient of variation)
      + 0.1 * momentum_score   (slope of last 3 periods)
    Uses Easy transaction-level data (sales_transaction).
    """
    filters_tx = ["b.name != 'ALL'"]
    params_tx: dict = {}
    _group_filter(filters_tx, params_tx, group)
    # Health is a TRAILING metric (MoM growth, consistency, momentum all need a
    # multi-month series). Ignore month_from so a single-month / "This Month"
    # filter doesn't collapse the series to length 1 (which made MoM read 0.0%
    # and zeroed the score). We still honour month_to as an "as-of" cutoff.
    _month_range_filter(filters_tx, params_tx, None, month_to, None,
                        col="strftime('%Y-%m', st.sold_at)")
    where_tx = "WHERE " + " AND ".join(filters_tx)

    with get_db() as con:
        easy_rows = con.execute(
            f"""
            SELECT b.name AS branch,
                   strftime('%Y-%m', st.sold_at) AS month,
                   ROUND({net_revenue_sum('st')}, 2) AS revenue
            FROM sales_transaction st
            JOIN branches b ON b.branch_id = st.branch_id
            {where_tx}
            GROUP BY b.name, month
            ORDER BY b.name, month
            """,
            params_tx,
        ).fetchall()

    branch_month_h: dict = {}
    for r in easy_rows:
        branch_month_h.setdefault(r["branch"], {})[r["month"]] = \
            branch_month_h.get(r["branch"], {}).get(r["month"], 0) + (r["revenue"] or 0)

    by_branch: dict = {
        br: [months[m] for m in sorted(months)]
        for br, months in branch_month_h.items()
    }

    if not by_branch:
        return []

    # Score the LATEST MONTH's standing (monthly), not the cumulative total —
    # otherwise a long-open branch wins on accumulation, not current performance.
    max_last = max((v[-1] if v else 0) for v in by_branch.values()) or 1

    out = []
    for branch, revs in by_branch.items():
        total = sum(revs)
        last_rev = revs[-1] if revs else 0
        revenue_score = min(100, last_rev / max_last * 100)

        if len(revs) >= 2 and revs[-2] > 0:
            growth_pct = (revs[-1] - revs[-2]) / revs[-2] * 100
            growth_score = max(0, min(100, 50 + growth_pct))
        else:
            growth_score = 50.0
            growth_pct = 0.0

        if len(revs) >= 2:
            mean = sum(revs) / len(revs)
            var  = sum((v - mean) ** 2 for v in revs) / len(revs)
            sd   = var ** 0.5
            cv   = sd / mean if mean else 1
            consistency_score = max(0, min(100, (1 - cv) * 100))
        else:
            consistency_score = 50.0

        if len(revs) >= 3:
            last3 = revs[-3:]
            slope = (last3[-1] - last3[0]) / (last3[0] or 1) * 100
            momentum_score = max(0, min(100, 50 + slope))
        else:
            momentum_score = 50.0

        composite = (
            0.4 * revenue_score +
            0.3 * growth_score  +
            0.2 * consistency_score +
            0.1 * momentum_score
        )
        out.append({
            "branch": branch,
            "score": round(composite, 1),
            "revenue_score":     round(revenue_score, 1),
            "growth_score":      round(growth_score, 1),
            "consistency_score": round(consistency_score, 1),
            "momentum_score":    round(momentum_score, 1),
            "total_revenue":     round(total, 2),
            "last_month_revenue": revs[-1] if revs else 0,
            "growth_pct":        round(growth_pct, 1),
        })
    out.sort(key=lambda x: -x["score"])
    return out


# ===========================================================================
# ADVANCED ANALYTICS  -  yoy, forecast, cohort, affinity, percentiles, ai
# ===========================================================================

@router.get("/api/analysis/yoy")
@ttl_cache
def analysis_yoy(
    month_from: Optional[str] = Query(None),
    month_to:   Optional[str] = Query(None),
    branch:     Optional[str] = Query(None),
    group:      Optional[str] = Query(None),
):
    """For each month in range, return current revenue + same month last year."""
    if not month_from and not month_to:
        return []
    filters = ["b.name != 'ALL'"]
    params: dict = {}
    if branch:
        filters.append("b.name = :branch")
        params["branch"] = branch
    _group_filter(filters, params, group)
    where_branch = " AND " + " AND ".join(filters[1:]) if len(filters) > 1 else ""

    with get_db() as con:
        # Pull all months in range
        rng = con.execute(
            f"""
            SELECT sm.period_month, ROUND(SUM(sm.subtotal_ex_vat),2) AS revenue
            FROM v_sales_sku_monthly sm
            JOIN branches b ON b.branch_id = sm.branch_id
            WHERE b.name != 'ALL'
              AND sm.period_month BETWEEN :mf AND :mt
              {where_branch}
            GROUP BY sm.period_month
            ORDER BY sm.period_month
            """,
            {**params, "mf": month_from or "0000-00", "mt": month_to or "9999-99"},
        ).fetchall()

        # fix D11: batch prior-year revenue into one query instead of N+1
        cur_months = [r["period_month"] for r in rng]
        ly_months_unique = list({f"{int(m.split('-')[0])-1}-{m.split('-')[1]}" for m in cur_months})
        if ly_months_unique:
            ly_ph = ",".join(f":ly{i}" for i in range(len(ly_months_unique)))
            ly_params = {**params, **{f"ly{i}": m for i, m in enumerate(ly_months_unique)}}
            ly_rows = con.execute(
                f"""SELECT sm.period_month, ROUND(SUM(sm.subtotal_ex_vat),2) AS revenue
                    FROM v_sales_sku_monthly sm
                    JOIN branches b ON b.branch_id = sm.branch_id
                    WHERE b.name != 'ALL' AND sm.period_month IN ({ly_ph}) {where_branch}
                    GROUP BY sm.period_month""",
                ly_params,
            ).fetchall()
            ly_rev_map = {r["period_month"]: (r["revenue"] or 0) for r in ly_rows}
        else:
            ly_rev_map = {}

        # Day-count + trading-branch set per month, for the like-for-like guard
        # below (a month with 9 days and 7 branches cannot be compared to a full
        # month with 26 as if the difference were performance).
        _yoy_cov: dict = {}
        _cov_months = sorted({m for m in cur_months} | set(ly_months_unique))
        if _cov_months:
            cph = ",".join(f":c{i}" for i in range(len(_cov_months)))
            for x in con.execute(f"""
                SELECT strftime('%Y-%m', st.sold_at) m, b.name br,
                       COUNT(DISTINCT date(st.sold_at)) days
                FROM sales_transaction st JOIN branches b ON b.branch_id=st.branch_id
                WHERE b.name != 'ALL' AND strftime('%Y-%m', st.sold_at) IN ({cph})
                  {where_branch}
                GROUP BY m, b.name""",
                {**params, **{f"c{i}": m for i, m in enumerate(_cov_months)}}).fetchall():
                e = _yoy_cov.setdefault(x["m"], {"days": 0, "branches": set()})
                e["branches"].add(x["br"])
                e["days"] = max(e["days"], x["days"] or 0)

        out = []
        for r in rng:
            cur_mo = r["period_month"]
            yr, mo = cur_mo.split("-")
            ly_mo  = f"{int(yr)-1}-{mo}"
            ly_rev = ly_rev_map.get(ly_mo, 0)
            yoy_pct = ((r["revenue"] - ly_rev) / ly_rev * 100) if ly_rev > 0 else None
            # LIKE-FOR-LIKE guard. The chain grew 7 → 27 branches and the first
            # data month (2025-05) holds only 9 days, so a raw month-vs-month YoY
            # reported +1991% — branch roll-out and partial coverage, not growth.
            # `same_store_pct` re-runs the comparison over ONLY the branches that
            # traded in BOTH months and, when either month is partial, only the
            # overlapping day-of-month window. `comparable` is False when the raw
            # figure is structurally misleading, so the UI can lead with the fair
            # number and mark the raw one. Same philosophy as the weekday-fair MoM.
            cov = _yoy_cov.get(cur_mo, {}); lcov = _yoy_cov.get(ly_mo, {})
            cur_days, ly_days = cov.get("days", 0), lcov.get("days", 0)
            cur_brs, ly_brs = cov.get("branches", set()), lcov.get("branches", set())
            both = cur_brs & ly_brs
            ss_pct = None
            if both and ly_days and cur_days:
                d_cut = min(cur_days, ly_days)          # overlapping day window
                ph = ",".join(f":b{i}" for i in range(len(both)))
                ss_p = {f"b{i}": b for i, b in enumerate(sorted(both))}
                ss = con.execute(f"""
                    SELECT strftime('%Y-%m', st.sold_at) m,
                           ROUND({net_revenue_sum('st')}, 2) rev
                    FROM sales_transaction st JOIN branches b ON b.branch_id=st.branch_id
                    WHERE b.name IN ({ph}) AND strftime('%Y-%m', st.sold_at) IN (:cm, :lm)
                      AND CAST(strftime('%d', st.sold_at) AS INT) <= :d
                    GROUP BY m""",
                    {**ss_p, "cm": cur_mo, "lm": ly_mo, "d": d_cut}).fetchall()
                sm = {x["m"]: (x["rev"] or 0) for x in ss}
                if sm.get(ly_mo, 0) > 0:
                    ss_pct = round((sm.get(cur_mo, 0) - sm[ly_mo]) / sm[ly_mo] * 100, 1)
            comparable = bool(both) and ly_days >= 20 and abs(len(cur_brs) - len(ly_brs)) <= 2
            out.append({
                "period_month": cur_mo,
                "revenue":      r["revenue"],
                "ly_period":    ly_mo,
                "ly_revenue":   ly_rev,
                "yoy_pct":      round(yoy_pct, 1) if yoy_pct is not None else None,
                # comparability context
                "comparable":       comparable,
                "same_store_pct":   ss_pct,
                "cur_days":         cur_days,  "ly_days":     ly_days,
                "cur_branches":     len(cur_brs), "ly_branches": len(ly_brs),
                "same_store_count": len(both),
            })
    return out


@ttl_cache
def analysis_forecast(
    branch: Optional[str] = Query(None),
    group:  Optional[str] = Query(None),
):
    """
    Projects revenue for the current month using daily run-rate from transactions
    in the latest month. Returns mtd, days_elapsed, days_total, projected, confidence_lo, confidence_hi.
    """
    filters = ["b.name != 'ALL'", "st.source = 'Easy'"]
    params: dict = {}
    if branch:
        filters.append("b.name = :branch")
        params["branch"] = branch
    _group_filter(filters, params, group)
    where = "WHERE " + " AND ".join(filters)
    with get_db() as con:
        latest = con.execute(
            f"""
            SELECT MAX(DATE(st.sold_at)) AS d
            FROM sales_transaction st JOIN branches b ON b.branch_id = st.branch_id {where}
            """, params
        ).fetchone()
        if not latest or not latest["d"]:
            return {"available": False}
        from datetime import datetime as _dt
        from calendar import monthrange
        d = _dt.strptime(latest["d"], "%Y-%m-%d")
        period = d.strftime("%Y-%m")
        if period in _partial_months(con):
            # current month not fully synced (few branches) -> run-rate is meaningless
            return {"available": False, "reason": "current month not fully synced yet"}
        params["period"] = period
        filters_p = filters + ["strftime('%Y-%m', st.sold_at) = :period"]
        where_p = "WHERE " + " AND ".join(filters_p)
        daily = con.execute(
            f"""
            SELECT DATE(st.sold_at) AS day, {net_revenue_sum('st')} AS rev
            FROM sales_transaction st JOIN branches b ON b.branch_id = st.branch_id {where_p}
            GROUP BY day ORDER BY day
            """, params
        ).fetchall()
    if not daily:
        return {"available": False}
    days_in_month = monthrange(d.year, d.month)[1]
    days_elapsed = d.day
    revs = [r["rev"] or 0 for r in daily]
    mtd = sum(revs)
    avg_daily = mtd / max(1, days_elapsed)
    projected = avg_daily * days_in_month
    # Confidence band Â±1 SD of daily revenue projected over remaining days
    if len(revs) >= 2:
        mean = sum(revs)/len(revs)
        sd = (sum((v-mean)**2 for v in revs)/len(revs)) ** 0.5
        remain = days_in_month - days_elapsed
        band = sd * (remain ** 0.5)
    else:
        band = 0
    return {
        "available":  True,
        "period":     period,
        "days_elapsed": days_elapsed,
        "days_total":   days_in_month,
        "mtd":          round(mtd, 2),
        "avg_daily":    round(avg_daily, 2),
        "projected":    round(projected, 2),
        "lo":           round(projected - band, 2),
        "hi":           round(projected + band, 2),
        "daily":        [{"day": r["day"], "revenue": r["rev"] or 0} for r in daily],
    }


@ttl_cache
def analysis_forecast_branches(group: Optional[str] = Query(None)):
    """Per-branch month-end revenue projection (run-rate) for the latest month,
    plus the prior month's actual for trend.

    Methodology: projected = MTD / days_elapsed * days_in_month, where days_elapsed
    is the calendar day-of-month of the latest sale date (closed days count as zero
    revenue, the honest run-rate). Every branch shares the same days_elapsed divisor,
    so the per-branch projections sum exactly to the total. Revenue is ex-VAT
    (inc-VAT / 1.07). mom_pct compares the projected month-end vs the prior full month.
    """
    filters = ["b.name != 'ALL'", "st.source = 'Easy'"]
    params: dict = {}
    _group_filter(filters, params, group)
    where = "WHERE " + " AND ".join(filters)
    with get_db() as con:
        latest = con.execute(
            f"""SELECT MAX(DATE(st.sold_at)) AS d
                FROM sales_transaction st JOIN branches b ON b.branch_id = st.branch_id {where}""",
            params,
        ).fetchone()
        if not latest or not latest["d"]:
            return {"available": False}
        from datetime import datetime as _dt
        from calendar import monthrange
        d = _dt.strptime(latest["d"], "%Y-%m-%d")
        period = d.strftime("%Y-%m")
        days_in_month = monthrange(d.year, d.month)[1]
        days_elapsed = d.day
        py, pm = (d.year - 1, 12) if d.month == 1 else (d.year, d.month - 1)
        prior = f"{py:04d}-{pm:02d}"

        params_cur = dict(params); params_cur["period"] = period
        cur = con.execute(
            f"""SELECT b.name AS branch, {net_revenue_sum('st')} AS mtd
                FROM sales_transaction st JOIN branches b ON b.branch_id = st.branch_id
                {where} AND strftime('%Y-%m', st.sold_at) = :period
                GROUP BY b.name""", params_cur,
        ).fetchall()
        params_pri = dict(params); params_pri["period"] = prior
        pri = con.execute(
            f"""SELECT b.name AS branch, {net_revenue_sum('st')} AS rev
                FROM sales_transaction st JOIN branches b ON b.branch_id = st.branch_id
                {where} AND strftime('%Y-%m', st.sold_at) = :period
                GROUP BY b.name""", params_pri,
        ).fetchall()
        # All real branches + their last sale date — to flag inactive ones (no sales
        # in the forecast month) so the 26-vs-27 gap is explained, not silently dropped.
        # fix D13: move st.source='Easy' to JOIN ON so inactive branches aren't dropped
        allbr_branch_filters = [f for f in filters if "st.source" not in f]
        allbr_where = ("WHERE " + " AND ".join(allbr_branch_filters)) if allbr_branch_filters else ""
        allbr = con.execute(
            f"""SELECT b.name AS branch, MAX(DATE(st.sold_at)) AS last_sale
                FROM branches b LEFT JOIN sales_transaction st
                    ON st.branch_id = b.branch_id AND st.source = 'Easy'
                {allbr_where} GROUP BY b.name""", params,
        ).fetchall()

    active_names = {r["branch"] for r in cur}
    inactive = [{"branch": r["branch"], "last_sale": r["last_sale"]}
                for r in allbr if r["branch"] not in active_names]
    prior_map = {r["branch"]: (r["rev"] or 0) for r in pri}
    factor = days_in_month / max(1, days_elapsed)
    out = []
    for r in cur:
        mtd = r["mtd"] or 0
        projected = mtd * factor
        prior_rev = prior_map.get(r["branch"])
        mom_pct = round((projected - prior_rev) / prior_rev * 100, 1) if prior_rev else None
        out.append({
            "branch":       r["branch"],
            "mtd":          round(mtd, 2),
            "projected":    round(projected, 2),
            "prior_actual": round(prior_rev, 2) if prior_rev is not None else None,
            "mom_pct":      mom_pct,
        })
    out.sort(key=lambda x: -x["projected"])
    total_mtd   = sum(x["mtd"] for x in out)
    total_proj  = sum(x["projected"] for x in out)
    total_prior = sum(prior_map.values())
    return {
        "available":    True,
        "period":       period,
        "prior_period": prior,
        "days_elapsed": days_elapsed,
        "days_total":   days_in_month,
        "branches":     out,
        "total_branches":    len(allbr),
        "active_branches":   len(out),
        "inactive_branches": inactive,
        "total": {
            "mtd":          round(total_mtd, 2),
            "projected":    round(total_proj, 2),
            "prior_actual": round(total_prior, 2) if total_prior else None,
            "mom_pct":      round((total_proj - total_prior) / total_prior * 100, 1) if total_prior else None,
        },
    }


@router.get("/api/analysis/cohort")
@ttl_cache
def analysis_cohort(
    branch: Optional[str] = Query(None),
    group:  Optional[str] = Query(None),
):
    """
    Monthly customer-volume signal (no unique customer IDs in restaurant POS data,
    so per-month covers stand in as a soft retention proxy). Per month: bills,
    customers, revenue, ACTIVE BRANCH COUNT and the growth-fair derivatives
    customers/branch + revenue/customer. A month whose day coverage is short of
    the calendar (first month starts 23 May 2025; the sync-cutoff current month)
    is flagged `partial` — MoM/YoY are only computed between two FULL months so
    a 19-day July is never read as a collapse, and `vs_avg` averages full months
    only. `seasonality` regroups the same rows by CALENDAR month × year
    (customers/branch basis) for the month-of-year mini view.
    """
    filters = ["b.name != 'ALL'"]
    params: dict = {}
    if branch:
        filters.append("b.name = :branch")
        params["branch"] = branch
    _group_filter(filters, params, group)
    where = "WHERE " + " AND ".join(filters)
    with get_db() as con:
        rows = con.execute(
            f"""
            SELECT strftime('%Y-%m', st.sold_at) AS month,
                   COUNT(DISTINCT st.receipt_no) AS tx_count,
                   SUM(st.customer_count)        AS customer_count,
                   ROUND({net_revenue_sum('st')},2) AS revenue,
                   COUNT(DISTINCT st.branch_id)  AS branches,
                   COUNT(DISTINCT date(st.sold_at)) AS days_covered
            FROM sales_transaction st
            JOIN branches b ON b.branch_id = st.branch_id
            {where}
            GROUP BY month
            ORDER BY month
            """, params,
        ).fetchall()
    months = [r["month"] for r in rows]
    if not months:
        return {"cohorts": [], "matrix": [], "seasonality": []}
    import calendar as _cal
    recs = []
    for r in rows:
        y, m = int(r["month"][:4]), int(r["month"][5:7])
        dim = _cal.monthrange(y, m)[1]
        cust = r["customer_count"] or 0
        rev = r["revenue"] or 0
        br = r["branches"] or 0
        days = r["days_covered"] or 0
        recs.append({
            "cohort": r["month"], "tx": r["tx_count"],
            "customers": cust, "revenue": rev, "branches": br,
            "cust_per_branch": round(cust / br) if br else None,
            "rev_per_cust": round(rev / cust) if cust else None,
            "days_covered": days, "days_in_month": dim,
            "partial": days < dim,
        })
    by_month = {x["cohort"]: x for x in recs}
    full = [x for x in recs if not x["partial"]]
    _avg_pool = full or recs
    avg_cust = sum(x["customers"] for x in _avg_pool) / len(_avg_pool) or 1

    def _pct(c, p):
        return round((c - p) / p * 100, 1) if p else None

    for i, x in enumerate(recs):
        prev = recs[i - 1] if i else None
        x["mom_pct"] = (_pct(x["customers"], prev["customers"])
                        if prev and not x["partial"] and not prev["partial"] else None)
        yo = by_month.get(f"{int(x['cohort'][:4]) - 1}{x['cohort'][4:]}")
        x["yoy_pct"] = (_pct(x["customers"], yo["customers"])
                        if yo and not x["partial"] and not yo["partial"] else None)
        x["vs_avg"] = round(x["customers"] / avg_cust * 100, 1)
    # month-of-year × year regrouping (customers/branch = fair across the branch
    # roll-out; raw customers kept for tooltips). Powers the seasonality mini view.
    seas: dict = {}
    for x in recs:
        seas.setdefault(int(x["cohort"][5:7]), {})[x["cohort"][:4]] = {
            "cust": x["customers"], "cpb": x["cust_per_branch"], "partial": x["partial"]}
    return {
        "note": ("POS ไม่มีรหัสลูกค้ารายคน จึงใช้จำนวนหัวรายเดือนเป็นสัญญาณ retention อย่างอ่อน — "
                 "อ่านที่คอลัมน์ MoM/YoY และ ลูกค้า/สาขา เป็นหลัก (ตัดผลจากการเปิดสาขาเพิ่ม) · "
                 "เดือนที่ติดป้าย \"ไม่ครบเดือน\" ไม่ถูกนำไปเทียบ MoM/YoY"),
        "cohorts": months,
        "matrix":  recs,
        "seasonality": [{"m": mm, "years": ys} for mm, ys in sorted(seas.items())],
    }


@router.get("/api/analysis/affinity")
@ttl_cache
def analysis_affinity(
    sku_code: Optional[str] = Query(None, description="Anchor SKU  -  find what's bought with it"),
    month_from: Optional[str] = Query(None),
    month_to:   Optional[str] = Query(None),
    branch:     Optional[str] = Query(None),
    limit:      int           = Query(10, ge=1, le=50),
):
    """
    Item affinity via receipt co-occurrence.
    NOTE: sales_transaction has totals only, not line items per receipt.
    Use sales_sku_monthly grouped at (branch, month) as a proxy basket.
    Returns SKUs most likely to appear in the same period+branch as the anchor.
    """
    if not sku_code:
        return {"items": [], "note": "Pass sku_code to anchor"}
    filters = ["b.name != 'ALL'"]
    params: dict = {"anchor": sku_code}
    if branch:
        filters.append("b.name = :branch"); params["branch"] = branch
    _month_range_filter(filters, params, month_from, month_to, None, col="sm.period_month")
    where = "WHERE " + " AND ".join(filters)
    with get_db() as con:
        # Find baskets (branch+month) where anchor sku appeared
        anchor_baskets = con.execute(
            f"""
            SELECT DISTINCT sm.branch_id || '|' || sm.period_month AS basket
            FROM v_sales_sku_monthly sm
            JOIN branches b ON b.branch_id = sm.branch_id
            JOIN skus sk    ON sk.sku_id    = sm.sku_id
            {where} AND sk.sku_code = :anchor
            """, params,
        ).fetchall()
        baskets = {r["basket"] for r in anchor_baskets}
        if not baskets:
            return {"items": [], "anchor": sku_code, "baskets": 0}

        # Find co-occurring SKUs across those baskets
        rows = con.execute(
            f"""
            SELECT sk.sku_code, COALESCE(sk.description, sk.sku_code) AS description,
                   COUNT(DISTINCT sm.branch_id || '|' || sm.period_month) AS co_count,
                   ROUND(SUM(sm.subtotal_ex_vat), 2) AS co_revenue,
                   ROUND(SUM(sm.qty), 0) AS co_qty
            FROM v_sales_sku_monthly sm
            JOIN branches b ON b.branch_id = sm.branch_id
            JOIN skus sk    ON sk.sku_id    = sm.sku_id
            {where} AND sk.sku_code != :anchor
              AND sm.branch_id || '|' || sm.period_month IN ({','.join('?' for _ in baskets)})
            GROUP BY sk.sku_id
            ORDER BY co_count DESC, co_revenue DESC
            LIMIT :limit
            """, {**params, "limit": limit, **{f"_b{i}": v for i, v in enumerate(baskets)}},
        ).fetchall() if False else None

        # The IN clause with named params + dict isn't well-supported in sqlite3  -  use a tuple-based query instead
        basket_list = list(baskets)
        placeholders = ",".join("?" * len(basket_list))
        # Rebuild with positional params
        sql = f"""
            SELECT sk.sku_code, COALESCE(sk.description, sk.sku_code) AS description,
                   COUNT(DISTINCT sm.branch_id || '|' || sm.period_month) AS co_count,
                   ROUND(SUM(sm.subtotal_ex_vat), 2) AS co_revenue,
                   ROUND(SUM(sm.qty), 0) AS co_qty
            FROM v_sales_sku_monthly sm
            JOIN branches b ON b.branch_id = sm.branch_id
            JOIN skus sk    ON sk.sku_id    = sm.sku_id
            WHERE b.name != 'ALL' AND sk.sku_code != ?
              AND sm.branch_id || '|' || sm.period_month IN ({placeholders})
            GROUP BY sk.sku_id
            ORDER BY co_count DESC, co_revenue DESC
            LIMIT ?
        """
        rows = con.execute(sql, [sku_code, *basket_list, limit]).fetchall()
    n_baskets = len(baskets)
    return {
        "anchor":   sku_code,
        "baskets":  n_baskets,
        "items":    [{**dict(r), "lift": round((r["co_count"]/n_baskets)*100, 1)} for r in rows],
    }


@router.get("/api/analysis/percentiles")
@ttl_cache
def analysis_percentiles(
    month_from: Optional[str] = Query(None),
    month_to:   Optional[str] = Query(None),
):
    """For each branch, compute percentile rank in revenue, avg ticket, customers."""
    filters = ["b.name != 'ALL'"]
    params: dict = {}
    _month_range_filter(filters, params, month_from, month_to, None, col="strftime('%Y-%m', st.sold_at)")
    where = "WHERE " + " AND ".join(filters)
    with get_db() as con:
        rows = con.execute(
            f"""
            SELECT b.name AS branch,
                   COUNT(*)                                AS tx,
                   SUM(st.customer_count)                  AS cust,
                   ROUND({net_revenue_sum('st')}, 2)        AS revenue,
                   ROUND(AVG{net_revenue_row('st')}, 2)        AS avg_ticket
            FROM sales_transaction st
            JOIN branches b ON b.branch_id = st.branch_id
            {where}
            GROUP BY b.name
            """, params,
        ).fetchall()
    arr = [dict(r) for r in rows]
    def pctile(values, v):
        if not values: return 0
        ordered = sorted(values)
        rank = sum(1 for x in ordered if x <= v)
        return round(rank / len(ordered) * 100)
    revs   = [r["revenue"]   or 0 for r in arr]
    custs  = [r["cust"]      or 0 for r in arr]
    avgs   = [r["avg_ticket"] or 0 for r in arr]
    for r in arr:
        r["p_revenue"]    = pctile(revs,  r["revenue"]   or 0)
        r["p_customers"]  = pctile(custs, r["cust"]      or 0)
        r["p_avg_ticket"] = pctile(avgs,  r["avg_ticket"] or 0)
    arr.sort(key=lambda x: -(x["revenue"] or 0))
    return arr


# ---------------------------------------------------------------------------
# /api/analysis/sph  — Spend-per-Head distribution
# ---------------------------------------------------------------------------
@router.get("/api/analysis/sph")
@ttl_cache
def analysis_sph(
    month: Optional[str] = Query(None),
    month_from: Optional[str] = Query(None),
    month_to: Optional[str] = Query(None),
    branch: Optional[str] = Query(None),
):
    filters = ["st.customer_count > 0", "st.source = 'Easy'", "b.name != 'ALL'"]
    params: dict = {}
    if branch:
        filters.append("b.name = :branch")
        params["branch"] = branch
    _month_range_filter(filters, params, month_from, month_to, month,
                        col="strftime('%Y-%m', st.sold_at)")
    where = "WHERE " + " AND ".join(filters)

    with get_db() as con:
        rows = con.execute(f"""
            SELECT ({net_revenue_row('st')}) / st.customer_count AS sph,
                   b.name                               AS branch,
                   CAST(strftime('%H', st.sold_at) AS INTEGER) AS hour
            FROM sales_transaction st
            JOIN branches b ON b.branch_id = st.branch_id
            {where}
        """, params).fetchall()

    if not rows:
        return {"by_branch": [], "distribution": [], "overall": {}, "by_daypart": [], "trend": []}

    sphs = [r["sph"] for r in rows if r["sph"] is not None]
    sphs.sort()
    n = len(sphs)

    def median(lst):
        if not lst: return 0
        m = len(lst) // 2
        return (lst[m - 1] + lst[m]) / 2 if len(lst) % 2 == 0 else lst[m]

    def percentile(lst, p):
        if not lst: return 0
        idx = int(p / 100 * len(lst))
        return lst[min(idx, len(lst) - 1)]

    avg_sph = sum(sphs) / n if n else 0

    # By branch
    branch_map: dict = {}
    for r in rows:
        br = r["branch"]
        if br not in branch_map:
            branch_map[br] = []
        if r["sph"] is not None:
            branch_map[br].append(r["sph"])
    by_branch = sorted([
        {
            "branch": br,
            "avg_sph": round(sum(vals) / len(vals), 2) if vals else 0,
            "median_sph": round(median(sorted(vals)), 2) if vals else 0,
            "tx_count": len(vals),
        }
        for br, vals in branch_map.items()
    ], key=lambda x: x["avg_sph"], reverse=True)

    # Spend/head is now ex-VAT AND ex-service-charge, so it sits on the SAME scale as
    # the menu's base price — the old boundaries carried a "+17-25% VAT and service
    # charge" markup that is no longer in the number, which pushed a ~590 guest into
    # the "Buffet 500" band. Bands are stated as plain spend ranges, not tier names:
    # a period can span both menus (old base 350/590/649/689/799/999 and, from
    # 2026-09-01, the net tiers whose base is 408/560/702/889/1156), so naming any one
    # tier would mislabel the other era's guests.
    # Ranges are half-open [lo, hi) — the old ones left hairline gaps (379.99 → 380)
    # that silently dropped ~100 bills out of the chart.
    buckets = [
        ("ต่ำกว่า ฿350",   0,   350,           '#94a3b8'),
        ("฿350–459",       350, 460,           '#eab308'),
        ("฿460–589",       460, 590,           '#10b981'),
        ("฿590–699",       590, 700,           '#4f46e5'),
        ("฿700–889",       700, 890,           '#f97316'),
        ("฿890 ขึ้นไป",     890, float("inf"), '#ef4444'),
    ]
    distribution = []
    for label, lo, hi, color in buckets:
        count = sum(1 for s in sphs if lo <= s < hi)
        pct = round(count / n * 100, 1) if n else 0
        distribution.append({"bucket": label, "count": count, "pct": pct, "color": color, "lo": lo, "hi": hi if hi != float("inf") else None})

    # By daypart
    daypart_map: dict = {"Lunch 11-14": [], "Dinner 17-22": [], "Other": []}
    for r in rows:
        h = r["hour"]
        if 11 <= h <= 14:
            daypart_map["Lunch 11-14"].append(r["sph"])
        elif 17 <= h <= 22:
            daypart_map["Dinner 17-22"].append(r["sph"])
        else:
            daypart_map["Other"].append(r["sph"])
    by_daypart = [
        {
            "daypart": dp,
            "avg_sph": round(sum(vals) / len(vals), 2) if vals else 0,
            "tx_count": len(vals),
        }
        for dp, vals in daypart_map.items()
    ]

    # Monthly trend — must honour the same period filter as the rest of the endpoint.
    filters2 = ["st.customer_count > 0", "st.source = 'Easy'", "b.name != 'ALL'"]
    params2: dict = {}
    if branch:
        filters2.append("b.name = :branch")
        params2["branch"] = branch
    _month_range_filter(filters2, params2, month_from, month_to, month,
                        col="strftime('%Y-%m', st.sold_at)")
    where2 = "WHERE " + " AND ".join(filters2)
    with get_db() as con:
        trend_rows = con.execute(f"""
            SELECT strftime('%Y-%m', st.sold_at) AS period_month,
                   ROUND(AVG(({net_revenue_row('st')}) / st.customer_count), 2) AS avg_sph
            FROM sales_transaction st
            JOIN branches b ON b.branch_id = st.branch_id
            {where2}
            GROUP BY period_month ORDER BY period_month
        """, params2).fetchall()
    trend = [dict(r) for r in trend_rows]

    # Dominant tier + share above 590
    dom_tier = max(distribution, key=lambda d: d["count"]) if distribution else None
    above_590_count = sum(b["count"] for b in distribution if b["lo"] >= 620)
    below_350_count = sum(b["count"] for b in distribution if (b.get("hi") or float("inf")) < 380)
    return {
        "by_branch": by_branch,
        "distribution": distribution,
        "overall": {
            "avg_sph": round(avg_sph, 2),
            "median_sph": round(median(sphs), 2),
            "p10": round(percentile(sphs, 10), 2),
            "p25": round(percentile(sphs, 25), 2),
            "p75": round(percentile(sphs, 75), 2),
            "p90": round(percentile(sphs, 90), 2),
            "total_tx": n,
            "dominant_tier": dom_tier["bucket"] if dom_tier else None,
            "dominant_pct":  round(dom_tier["count"] / n * 100, 1) if dom_tier and n else 0,
            "pct_above_590": round(above_590_count / n * 100, 1) if n else 0,
            "pct_below_350": round(below_350_count / n * 100, 1) if n else 0,
            "sph_min":      round(min(sphs), 2) if sphs else 0,
            "sph_max":      round(max(sphs), 2) if sphs else 0,
        },
        "by_daypart": by_daypart,
        "trend": trend,
    }


@router.get("/api/analysis/food_cost_trend")
@ttl_cache
def food_cost_trend(
    branch: Optional[str] = Query(None),
    group:  Optional[str] = Query(None),
    alert_threshold: float = Query(70.0, description="Food cost % alert threshold (actual Oshinei AYCE buffet 55-70%)"),
):
    """Monthly food cost % trend per branch. Returns months, cost%, alert threshold."""
    filters = ["b.name != 'ALL'"]
    params: dict = {}
    if branch:
        filters.append("b.name = :branch")
        params["branch"] = branch
    _group_filter(filters, params, group)
    where = "WHERE " + " AND ".join(filters)

    with get_db() as con:
        rows = con.execute(
            f"""
            SELECT sm.period_month,
                   b.name AS branch,
                   ROUND(SUM(sm.subtotal_ex_vat), 2)                              AS revenue,
                   ROUND(SUM({_cogs_case(ssm='sm', sk='sk', sc='sc')}), 2)         AS cost,
                   ROUND(SUM(sm.qty), 0)                                          AS servings,
                   COUNT(DISTINCT CASE WHEN {_unified_cogs_expr('sm', 'sk')} = 0
                                       THEN sk.sku_id END)                         AS uncosted
            FROM v_sales_sku_monthly sm
            JOIN branches b   ON b.branch_id   = sm.branch_id
            JOIN skus sk      ON sk.sku_id      = sm.sku_id
            LEFT JOIN sku_cost sc ON sc.sku_id  = sm.sku_id
            {where}
            GROUP BY sm.period_month, b.name
            ORDER BY sm.period_month, b.name
            """,
            params,
        ).fetchall()

    # Build per-branch monthly series
    branch_data: dict = {}
    all_months: list = []
    for r in rows:
        mo = r["period_month"]
        if mo not in all_months:
            all_months.append(mo)
        rev  = r["revenue"]  or 0
        cost = r["cost"]     or 0
        srv  = r["servings"] or 0
        pct  = round(cost / rev * 100, 1) if rev > 0 else None
        branch_data.setdefault(r["branch"], {})[mo] = {
            "revenue": rev, "cost": cost, "servings": srv, "cost_pct": pct,
            "uncosted": r["uncosted"] or 0,
        }

    # Overall (network) monthly
    overall: dict = {}
    for mo in all_months:
        tot_rev  = sum(bd.get(mo, {}).get("revenue", 0)  for bd in branch_data.values())
        tot_cost = sum(bd.get(mo, {}).get("cost", 0)     for bd in branch_data.values())
        tot_srv  = sum(bd.get(mo, {}).get("servings", 0) for bd in branch_data.values())
        overall[mo] = {
            "revenue":  tot_rev,
            "cost":     tot_cost,
            "servings": tot_srv,
            "cost_pct": round(tot_cost / tot_rev * 100, 1) if tot_rev > 0 else None,
            "rev_per_serving":  round(tot_rev / tot_srv, 2)  if tot_srv > 0 else None,
            "cost_per_serving": round(tot_cost / tot_srv, 2) if tot_srv > 0 else None,
        }

    # Latest month alert
    alerts = []
    if all_months:
        last_mo = all_months[-1]
        for br, mdata in branch_data.items():
            d = mdata.get(last_mo, {})
            pct = d.get("cost_pct")
            if pct is not None and pct > alert_threshold:
                alerts.append({
                    "branch": br, "period": last_mo,
                    "cost_pct": pct, "threshold": alert_threshold,
                    "message": f"⚠️ {br}: food cost {pct}% exceeds {alert_threshold}% threshold in {last_mo}",
                })

    return {
        "months": all_months,
        "overall": [{"period_month": mo, **v} for mo, v in overall.items()],
        "by_branch": {br: [{"period_month": mo, **mdata.get(mo, {"cost_pct": None})}
                            for mo in all_months]
                      for br, mdata in branch_data.items()},
        "alert_threshold": alert_threshold,
        "alerts": alerts,
    }


@router.get("/api/analysis/buffet_mix")
@ttl_cache
def buffet_mix(
    month:      Optional[str] = Query(None),
    month_from: Optional[str] = Query(None),
    month_to:   Optional[str] = Query(None),
    branch:     Optional[str] = Query(None),
):
    """
    Buffet price-tier mix from SKU sales, per the menu in force in each month.

    Two price lists, never mixed: months up to 2026-08 are bucketed into the old
    350/500/590/799; months from 2026-09 into the Grand Menu's NET prices
    (459/629/789/999/1299), which the old code could not name at all — it read a
    ฿789 cover as the retired ฿799 tier. Also flags a la carte separately.
    Returns tier revenue share, qty share, and trend.
    """
    filters = ["b.name != 'ALL'"]
    params: dict = {}
    if branch:
        filters.append("b.name = :branch")
        params["branch"] = branch
    _month_range_filter(filters, params, month_from, month_to, month, col="sm.period_month")
    where = "WHERE " + " AND ".join(filters)

    with get_db() as con:
        rows = con.execute(
            f"""
            SELECT sm.period_month,
                   b.name AS branch,
                   COALESCE(cat.name, '') AS category,
                   sk.sku_code,
                   COALESCE(sk.description, sk.sku_code) AS description,
                   ROUND(SUM(sm.qty), 2)                 AS qty,
                   ROUND(SUM(sm.subtotal_ex_vat), 2)       AS revenue
            FROM v_sales_sku_monthly sm
            JOIN branches b   ON b.branch_id  = sm.branch_id
            JOIN skus sk      ON sk.sku_id     = sm.sku_id
            LEFT JOIN categories cat ON cat.category_id = sk.category_id
            {where}
            GROUP BY b.name, sm.period_month, cat.name, sk.sku_code, sk.description
            ORDER BY sm.period_month
            """,
            params,
        ).fetchall()

    TIERS_LEGACY = ['350', '500', '590', '799']            # until 2026-08-31
    TIERS_GRAND  = [str(t) for t in PRICE_TIERS_GRAND]     # from 2026-09-01, NET prices
    ALACARTE_KEYWORDS = ['อลาคาท', 'ala carte', 'a la carte', 'alacarte', 'อา ลา คาร์ท']
    # Buffet "charge" SKUs = a per-cover price (vs the B0xxx food-item rows that
    # are logged at 0 baht). In this data the whole adult buffet is booked under
    # one generic 'BUFFET' code, so we recover the tier from the realised price.
    CHARGE_CODES = {'BUFFET', 'BUFFET350', 'BUFFET500', 'BUFFET590', 'BUFFET799',
                    'B01', 'B02', 'B07', 'B11'}

    def price_tier(unit_price_ex_vat, month):
        """Bucket a per-head ex-VAT price into the tier its month's menu sells.

        Before the Grand Menu: nearest legacy tier (midpoints 425/545/695 between
        350·500·590·799). From 2026-09-01: the stored ex-VAT price is grossed back
        up by service charge and VAT (฿702.27 → ฿789) and matched against the NET
        price list, so the tier carries the number actually printed on the menu.
        A September price that matches none of them is a discount ('promo'),
        NOT a resurrected old tier.
        """
        up = unit_price_ex_vat or 0
        if is_grand_menu(month):
            return tier_of_price(base_to_net(up), month)
        if up < 425: return '350'
        if up < 545: return '500'
        if up < 695: return '590'
        return '799'

    def classify(cat, sku, desc, qty, rev, month):
        combined = f"{cat} {sku} {desc}".lower()
        for kw in ALACARTE_KEYWORDS:
            if kw.lower() in combined:
                return 'à la carte'
        if sku == 'BUFFETCHILD' or ('เด็ก' in combined and 'บุฟเฟ' in combined):
            return 'เด็ก (Child)'
        # An explicit tier number in the code/description wins — but only a price
        # that month's menu still sells, so a legacy 'BUFFET590' code appearing in
        # a September row is priced, not taken at face value.
        for t in (TIERS_GRAND if is_grand_menu(month) else TIERS_LEGACY):
            if t in combined:
                return t
        is_buffet_charge = (sku in CHARGE_CODES) or ('บุฟเฟ' in combined) or ('buffet' in combined)
        if is_buffet_charge:
            if qty and rev and rev > 0:        # priced buffet cover → bucket by price
                return price_tier(rev / qty, month)
            return None                        # 0-baht buffet food item → not a price level
        # Non-buffet regular menu / à-la-carte food: not a buffet price level —
        # exclude so this chart is purely the buffet price-tier mix (covers & ฿).
        return None

    # Aggregate
    totals: dict = {}   # tier -> {qty, revenue}
    monthly: dict = {}  # period_month -> tier -> revenue

    for r in rows:
        tier = classify(r["category"], r["sku_code"], r["description"],
                        r["qty"] or 0, r["revenue"] or 0, r["period_month"])
        if tier is None:
            continue
        t = totals.setdefault(tier, {"qty": 0.0, "revenue": 0.0})
        t["qty"]     += r["qty"]     or 0
        t["revenue"] += r["revenue"] or 0
        mo = monthly.setdefault(r["period_month"], {})
        mo[tier] = mo.get(tier, 0.0) + (r["revenue"] or 0)

    total_rev = sum(v["revenue"] for v in totals.values()) or 1
    total_qty = sum(v["qty"]     for v in totals.values()) or 1

    summary = [
        {
            "tier": tier,
            "qty": round(vals["qty"], 1),
            "revenue": round(vals["revenue"], 2),
            "revenue_pct": round(vals["revenue"] / total_rev * 100, 1),
            "qty_pct":     round(vals["qty"]     / total_qty * 100, 1),
        }
        for tier, vals in sorted(totals.items(),
                                  key=lambda x: -x[1]["revenue"])
    ]

    all_months = sorted(monthly.keys())
    all_tiers  = list(totals.keys())
    trend = [
        {"period_month": mo,
         **{t: round(monthly[mo].get(t, 0), 2) for t in all_tiers}}
        for mo in all_months
    ]

    return {"summary": summary, "trend": trend, "tiers": all_tiers, "months": all_months,
            # so the chart can say WHICH menu each month is on, and never draw a
            # retired price as a live one
            "grand_menu_from": GRAND_MENU_FROM,
            "menu_era": {mo: ("grand" if is_grand_menu(mo) else "legacy") for mo in all_months}}


# ---------------------------------------------------------------------------
# GET /api/analysis/forecast  (Analysis #9)  — short-horizon revenue projection
# ---------------------------------------------------------------------------
@ttl_cache
def revenue_forecast(
    branch:  Optional[str] = Query(None),
    horizon: int = Query(3),
):
    """Project ex-VAT revenue for the next `horizon` months from the monthly trend
    via OLS, with a ± band from the residual spread. Branch-specific or chain."""
    horizon = max(1, min(6, horizon))
    # Exclude the in-progress calendar month so OLS is not biased by a partial point (fix: G5)
    filters = ["b.name != 'ALL'",
               "ssm.period_month < strftime('%Y-%m', 'now')"]
    params: dict = {}
    if branch:
        filters.append("b.name = :branch"); params["branch"] = branch
    where = "WHERE " + " AND ".join(filters)
    with get_db() as con:
        rows = con.execute(f"""
            SELECT ssm.period_month AS m, SUM(ssm.subtotal_ex_vat) AS rev
            FROM v_sales_sku_monthly ssm
            JOIN branches b ON b.branch_id = ssm.branch_id
            {where}
            GROUP BY ssm.period_month
            ORDER BY ssm.period_month
        """, params).fetchall()
    hist = [{"period": r["m"], "revenue": round(r["rev"] or 0, 2)} for r in rows]
    if len(hist) < 3:
        return {"history": hist, "forecast": [], "note": "need >=3 months of history"}
    xs = list(range(len(hist)))
    ys = [h["revenue"] for h in hist]
    slope, intercept, r2 = _ols_regression(xs, ys)
    resid = [ys[i] - (slope * xs[i] + intercept) for i in xs]
    sd = (sum(e * e for e in resid) / len(resid)) ** 0.5
    band = 1.5 * sd
    # advance YYYY-MM
    def add_month(ym, k):
        y, mo = _ym_parts(ym); mo += k
        y += (mo - 1) // 12; mo = (mo - 1) % 12 + 1
        return f"{y:04d}-{mo:02d}"
    forecast = []
    for k in range(1, horizon + 1):
        x = len(hist) - 1 + k
        pred = slope * x + intercept
        forecast.append({
            "period": add_month(hist[-1]["period"], k),
            "revenue": round(max(0, pred), 2),
            "lo": round(max(0, pred - band), 2),
            "hi": round(max(0, pred + band), 2),
        })
    return {
        "history": hist, "forecast": forecast,
        "trend": {"slope_per_month": round(slope, 2), "r_squared": round(r2, 3)},
    }
