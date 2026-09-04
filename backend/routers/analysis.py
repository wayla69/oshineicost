"""Analysis domain router — every /api/analysis/* endpoint, moved VERBATIM out
of main.py (P1 structure refactor, step 2). No logic changes; URLs unchanged.
Same host-module pattern as routers/reports.py (see that docstring): shared
infra still lives in main.py and is pulled off the already-initialized module
below; local/tests import it as `main`, prod uvicorn as `backend.main`.
"""
import sys as _sys

from fastapi import APIRouter

router = APIRouter()

M = _sys.modules.get("backend.main") or _sys.modules["main"]
_SHARED = (
    'HTTPException',
    'Optional',
    'Query',
    '_EASY_TIER_CODES_SQL',
    '_FOOD_CATEGORY_FILTER',
    '_apply_period_filter',
    '_build_sku_effective_cost',
    '_cogs_case',
    '_eff_month_range',
    '_effective_cost_subquery',
    '_group_filter',
    '_iso_week_sql',
    '_json',
    '_month_range_filter',
    '_ols_regression',
    '_partial_months',
    '_unified_cogs_expr',
    '_ym_parts',
    'ex_vat',
    'get_db',
    'ttl_cache',
)
globals().update({_n: getattr(M, _n) for _n in _SHARED})




@router.get("/api/analysis/discount-reasons")
def discount_reasons(month_from: Optional[str] = Query(None), month_to: Optional[str] = Query(None),
                     month: Optional[str] = Query(None), branch: Optional[str] = Query(None),
                     group: Optional[str] = Query(None)):
    """Discount breakdown by REASON/TYPE (Sale.DiscountReasonId → DiscountSaleReason),
    over a month range. reason_id 0 = 'ไม่ระบุ' (cashier discounted without picking a
    reason) — usually the biggest leak. Staged by sync into pos_discount_month +
    pos_discount_reason; returns empty/staged=False on a DB synced before they existed."""
    base = ["b.name!='ALL'"]; p: dict = {}
    if branch:
        base.append("b.name=:branch"); p["branch"] = branch
    _group_filter(base, p, group)
    _month_range_filter(base, p, month_from, month_to, month, col="dm.period_month")
    with get_db() as con:
        if not con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='pos_discount_month'").fetchone():
            return {"reasons": [], "total_bills": 0, "total_amount": 0, "untyped_pct": None, "staged": False}
        rows = con.execute(f"""
            SELECT COALESCE(dm.reason_id,0) reason_id,
                   COALESCE(dr.reason_desc, CASE WHEN COALESCE(dm.reason_id,0)=0 THEN 'ไม่ระบุ (ไม่เลือกเหตุผล)'
                                                 ELSE 'รหัส '||dm.reason_id END) reason,
                   SUM(dm.bills) bills, ROUND(SUM(dm.amount)) amount
            FROM pos_discount_month dm JOIN branches b ON b.branch_id=dm.branch_id
            LEFT JOIN pos_discount_reason dr ON dr.reason_id=dm.reason_id
            WHERE {' AND '.join(base)}
            GROUP BY COALESCE(dm.reason_id,0), reason
            ORDER BY amount DESC""", p).fetchall()
        reasons = [dict(r) for r in rows]
        tot_amt = sum(r["amount"] or 0 for r in reasons)
        tot_bills = sum(r["bills"] or 0 for r in reasons)
        for r in reasons:
            r["pct"] = round(100 * (r["amount"] or 0) / tot_amt, 1) if tot_amt else 0
        untyped = next((r for r in reasons if r["reason_id"] == 0), None)
        return {"reasons": reasons, "total_bills": tot_bills, "total_amount": tot_amt,
                "untyped_pct": (round(100 * (untyped["amount"] or 0) / tot_amt, 1) if (untyped and tot_amt) else 0),
                "staged": True}


@router.get("/api/analysis/coupon-series")
def coupon_series_breakdown(month_from: Optional[str] = Query(None), month_to: Optional[str] = Query(None),
                            month: Optional[str] = Query(None), branch: Optional[str] = Query(None),
                            group: Optional[str] = Query(None)):
    """Coupon/promotion breakdown by SERIES, with the human-readable series name
    (CouponSeries master → coupon_series table). pos_coupon_month holds usage per
    branch × month × coupon_series_id; this joins the title so staff see WHICH promo
    (เด็กสูง / ผู้สูงอายุ / วันเกิด / VIP / ผู้ถือหุ้น …) drives the most discount."""
    base = ["b.name!='ALL'"]; p: dict = {}
    if branch:
        base.append("b.name=:branch"); p["branch"] = branch
    _group_filter(base, p, group)
    _month_range_filter(base, p, month_from, month_to, month, col="cm.period_month")
    with get_db() as con:
        if not con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='pos_coupon_month'").fetchone():
            return {"series": [], "total_uses": 0, "total_amount": 0, "series_count": 0, "staged": False}
        has_cs = con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='coupon_series'").fetchone()
        name_c = "COALESCE(NULLIF(TRIM(cs.name),''),'Series '||cm.coupon_series_id)" if has_cs else "'Series '||cm.coupon_series_id"
        code_c = "cs.code" if has_cs else "NULL"
        actv_c = "COALESCE(cs.active,0)" if has_cs else "0"
        join   = "LEFT JOIN coupon_series cs ON cs.coupon_series_id=cm.coupon_series_id" if has_cs else ""
        rows = con.execute(f"""
            SELECT cm.coupon_series_id id, {name_c} name, {code_c} code, {actv_c} active,
                   SUM(cm.used_count) uses, ROUND(SUM(cm.discount_amount)) amount
            FROM pos_coupon_month cm JOIN branches b ON b.branch_id=cm.branch_id {join}
            WHERE {' AND '.join(base)}
            GROUP BY cm.coupon_series_id
            ORDER BY amount DESC""", p).fetchall()
        series = [dict(r) for r in rows]
        tot_amt = sum(r["amount"] or 0 for r in series)
        tot_uses = sum(r["uses"] or 0 for r in series)
        for r in series:
            r["pct"] = round(100 * (r["amount"] or 0) / tot_amt, 1) if tot_amt else 0
            r["avg"] = round((r["amount"] or 0) / r["uses"]) if r["uses"] else 0

        # ── MoM per series: latest month in scope vs the month before it ──
        # (branch/group filtered, but NOT range-clipped, so prev month is available
        #  even when a single month is selected). amount column stays the range total.
        bbase = ["b.name!='ALL'"]; bp: dict = {}
        if branch:
            bbase.append("b.name=:branch"); bp["branch"] = branch
        _group_filter(bbase, bp, group)
        cur_m = month or month_to or con.execute(
            f"SELECT MAX(cm.period_month) m FROM pos_coupon_month cm JOIN branches b ON b.branch_id=cm.branch_id "
            f"WHERE {' AND '.join(bbase)}", bp).fetchone()["m"]
        mom_label = None
        if cur_m:
            yy, mm = int(cur_m[:4]), int(cur_m[5:7]); mm -= 1
            if mm == 0: yy -= 1; mm = 12
            prev_m = f"{yy:04d}-{mm:02d}"
            mom_label = {"cur": cur_m, "prev": prev_m}
            cur_amt, prev_amt = {}, {}
            for r in con.execute(
                f"SELECT cm.coupon_series_id id, cm.period_month ym, SUM(cm.discount_amount) amt "
                f"FROM pos_coupon_month cm JOIN branches b ON b.branch_id=cm.branch_id "
                f"WHERE {' AND '.join(bbase)} AND cm.period_month IN (:cur,:prev) "
                f"GROUP BY cm.coupon_series_id, cm.period_month",
                {**bp, "cur": cur_m, "prev": prev_m}):
                (cur_amt if r["ym"] == cur_m else prev_amt)[r["id"]] = float(r["amt"] or 0)
            for r in series:
                c, pv = cur_amt.get(r["id"], 0.0), prev_amt.get(r["id"], 0.0)
                r["cur_amount"] = round(c)
                r["mom_pct"] = round((c - pv) / pv * 100, 1) if pv > 0 else (None if c == 0 else None)
        return {"series": series, "total_uses": tot_uses, "total_amount": tot_amt,
                "series_count": len(series), "mom": mom_label, "staged": True}


@router.get("/api/analysis/coupon-series/drill")
def coupon_series_drill(series_id: int, by: str = Query("branch"),
                        month_from: Optional[str] = Query(None), month_to: Optional[str] = Query(None),
                        month: Optional[str] = Query(None), branch: Optional[str] = Query(None),
                        group: Optional[str] = Query(None)):
    """Drill one coupon series → usage by BRANCH (from pos_coupon_month) or by DAY
    (from pos_coupon_day, populated by the sync). Honours the same month/branch
    filters as the parent table."""
    by = "day" if by == "day" else "branch"
    with get_db() as con:
        nm = con.execute("SELECT name, code FROM coupon_series WHERE coupon_series_id=?", (series_id,)).fetchone() \
             if con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='coupon_series'").fetchone() else None
        name = (nm["name"] if nm and (nm["name"] or "").strip() else f"Series {series_id}")
        if by == "day":
            if not con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='pos_coupon_day'").fetchone():
                return {"by": "day", "series_id": series_id, "name": name, "rows": [], "available": False}
            base = ["b.name!='ALL'", "cd.coupon_series_id=:sid"]; p = {"sid": series_id}
            if branch: base.append("b.name=:branch"); p["branch"] = branch
            _group_filter(base, p, group)
            _month_range_filter(base, p, month_from, month_to, month, col="substr(cd.day,1,7)")
            rows = [dict(r) for r in con.execute(
                f"SELECT cd.day label, SUM(cd.used_count) uses, ROUND(SUM(cd.discount_amount)) amount "
                f"FROM pos_coupon_day cd JOIN branches b ON b.branch_id=cd.branch_id "
                f"WHERE {' AND '.join(base)} GROUP BY cd.day ORDER BY cd.day", p)]
            return {"by": "day", "series_id": series_id, "name": name, "rows": rows, "available": True}
        # by branch
        base = ["b.name!='ALL'", "cm.coupon_series_id=:sid"]; p = {"sid": series_id}
        if branch: base.append("b.name=:branch"); p["branch"] = branch
        _group_filter(base, p, group)
        _month_range_filter(base, p, month_from, month_to, month, col="cm.period_month")
        rows = [dict(r) for r in con.execute(
            f"SELECT b.name label, SUM(cm.used_count) uses, ROUND(SUM(cm.discount_amount)) amount "
            f"FROM pos_coupon_month cm JOIN branches b ON b.branch_id=cm.branch_id "
            f"WHERE {' AND '.join(base)} GROUP BY b.name ORDER BY amount DESC", p)]
        return {"by": "branch", "series_id": series_id, "name": name, "rows": rows, "available": True}




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
                   ROUND((SUM(st.total_inc_vat)/1.07 - SUM(COALESCE(st.service_charge,0))), 2)  AS revenue
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
                   ROUND((SUM(st.total_inc_vat)/1.07 - SUM(COALESCE(st.service_charge,0))), 2)             AS revenue,
                   ROUND(AVG(st.total_inc_vat/1.07 - COALESCE(st.service_charge,0)), 2)             AS avg_ticket
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
                   ROUND((SUM(st.total_inc_vat)/1.07 - SUM(COALESCE(st.service_charge,0))), 2)             AS revenue,
                   ROUND(AVG(st.total_inc_vat/1.07 - COALESCE(st.service_charge,0)), 2)             AS avg_ticket,
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
                SELECT ROUND((SUM(st.total_inc_vat)/1.07 - SUM(COALESCE(st.service_charge,0))), 2) revenue, COUNT(*) tx_count,
                       SUM(st.customer_count) customer_count, ROUND((AVG(st.total_inc_vat)/1.07 - AVG(COALESCE(st.service_charge,0))), 2) avg_ticket
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
                   ROUND((AVG(total_inc_vat)/1.07 - AVG(COALESCE(service_charge,0))), 2) AS avg_ticket
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
                   ROUND(AVG((total_inc_vat/1.07 - COALESCE(service_charge,0))), 2)     AS avg_bill,
                   ROUND(AVG((total_inc_vat/1.07 - COALESCE(service_charge,0)) / CASE WHEN customer_count <= 0 THEN 1
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
                   ROUND(AVG((total_inc_vat/1.07 - COALESCE(service_charge,0))), 2)     AS avg_ticket,
                   ROUND((SUM(total_inc_vat)/1.07 - SUM(COALESCE(service_charge,0))) /
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
                   ROUND((SUM(st.total_inc_vat)/1.07 - SUM(COALESCE(st.service_charge,0))), 2)                  AS revenue,
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


# ---------------------------------------------------------------------------
# /api/analysis/cashiers   -  cashier leaderboard
# ---------------------------------------------------------------------------
@router.get("/api/analysis/cashiers")
def analysis_cashiers(
    month:      Optional[str] = Query(None),
    month_from: Optional[str] = Query(None),
    month_to:   Optional[str] = Query(None),
    branch:     Optional[str] = Query(None),
    by_branch:  bool          = Query(False),
    limit:      int           = Query(30, ge=1, le=100),
):
    """Cashier performance ranked by revenue.

    by_branch=False (default): one row per cashier, aggregated across all branches
    they keyed in (primary_branch + branch_count summarise the spread).
    by_branch=True: one row per (cashier, branch) pair — so a shared POS account like
    'Manager' becomes a separate row for each branch instead of being merged.
    """
    filters = ["b.name != 'ALL'", "TRIM(COALESCE(st.cashier,'')) != ''"]
    params: dict = {}
    if branch:
        filters.append("b.name = :branch")
        params["branch"] = branch
    _month_range_filter(filters, params, month_from, month_to, month, col="strftime('%Y-%m', st.sold_at)")
    where = "WHERE " + " AND ".join(filters)
    params["limit"] = limit

    group_by   = "st.cashier, b.name" if by_branch else "st.cashier"
    branch_sel = "b.name AS branch," if by_branch else ""

    with get_db() as con:
        # Per-(cashier, branch) revenue so we can find the primary branch in Python
        br_rows = con.execute(
            f"""
            SELECT st.cashier, b.name AS branch,
                   ROUND((SUM(st.total_inc_vat)/1.07 - SUM(COALESCE(st.service_charge,0))), 2) AS rev
            FROM sales_transaction st
            JOIN branches b ON b.branch_id = st.branch_id
            {where}
            GROUP BY st.cashier, b.name
            """, {k: v for k, v in params.items() if k != "limit"}
        ).fetchall()
        from collections import defaultdict
        per_cashier = defaultdict(list)
        for r in br_rows:
            per_cashier[r["cashier"]].append({"branch": r["branch"], "rev": r["rev"] or 0})

        rows = con.execute(
            f"""
            SELECT st.cashier,
                   {branch_sel}
                   COUNT(*)                                           AS tx_count,
                   SUM(st.customer_count)                            AS customer_count,
                   ROUND((SUM(st.total_inc_vat)/1.07 - SUM(COALESCE(st.service_charge,0))), 2)                  AS revenue,
                   ROUND(AVG(st.total_inc_vat/1.07 - COALESCE(st.service_charge,0)), 2)                  AS avg_ticket,
                   SUM(CASE WHEN st.discount > 0 THEN 1 ELSE 0 END) AS discount_tx,
                   ROUND(SUM(st.discount), 2)                        AS total_discount,
                   ROUND(100.0 * SUM(CASE WHEN st.discount > 0 THEN 1 ELSE 0 END)
                         / COUNT(*), 1)                              AS discount_rate
            FROM sales_transaction st
            JOIN branches b ON b.branch_id = st.branch_id
            {where}
            GROUP BY {group_by}
            ORDER BY revenue DESC
            LIMIT :limit
            """,
            params,
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        if by_branch:
            # one branch per row already
            d["primary_branch"] = d.get("branch")
            d["branch_count"]   = 1
            d["branches"]       = [d.get("branch")] if d.get("branch") else []
        else:
            brs = per_cashier.get(d["cashier"], [])
            brs.sort(key=lambda x: -x["rev"])
            d["primary_branch"] = brs[0]["branch"] if brs else None
            d["branch_count"]   = len(brs)
            d["branches"]       = [b["branch"] for b in brs]
        out.append(d)
    return out


# ---------------------------------------------------------------------------
# /api/analysis/discounts   -  discount monitor
# ---------------------------------------------------------------------------
@router.get("/api/analysis/discounts")
def analysis_discounts(
    month:      Optional[str] = Query(None),
    month_from: Optional[str] = Query(None),
    month_to:   Optional[str] = Query(None),
    branch:     Optional[str] = Query(None),
    group:      Optional[str] = Query(None),
    date_from:  Optional[str] = Query(None),
    date_to:    Optional[str] = Query(None),
):
    """Discount analysis: overall summary, by branch, trend (all months), top cashiers."""
    filters = ["b.name != 'ALL'"]
    params: dict = {}
    if branch:
        filters.append("b.name = :branch")
        params["branch"] = branch
    _group_filter(filters, params, group)
    _apply_period_filter(filters, params, date_from, date_to, month_from, month_to, month)
    where = "WHERE " + " AND ".join(filters)

    # Trend always spans all months  -  ignore the month param
    trend_filters = ["b.name != 'ALL'"]
    trend_params: dict = {}
    if branch:
        trend_filters.append("b.name = :branch")
        trend_params["branch"] = branch
    _group_filter(trend_filters, trend_params, group)
    trend_where = "WHERE " + " AND ".join(trend_filters)

    with get_db() as con:
        overall = con.execute(
            f"""
            SELECT COUNT(*) AS total_tx,
                   SUM(CASE WHEN discount > 0 THEN 1 ELSE 0 END) AS discount_tx,
                   ROUND(SUM(discount), 2)                        AS total_discount,
                   ROUND(AVG(CASE WHEN discount > 0 THEN discount END), 0) AS avg_discount,
                   ROUND(SUM(((total_inc_vat/1.07 - COALESCE(service_charge,0))) + discount), 2) AS gross_sales,
                   SUM(CASE WHEN discount > 0 AND COALESCE(print_times,0) > 1 THEN 1 ELSE 0 END) AS reprinted_disc_bills,
                   MAX(COALESCE(print_times, 0)) AS max_print_times
            FROM sales_transaction st
            JOIN branches b ON b.branch_id = st.branch_id
            {where}
            """,
            params,
        ).fetchone()

        by_branch = con.execute(
            f"""
            SELECT b.name AS branch,
                   COUNT(*) AS total_tx,
                   SUM(CASE WHEN discount > 0 THEN 1 ELSE 0 END) AS discount_tx,
                   ROUND(100.0 * SUM(CASE WHEN discount > 0 THEN 1 ELSE 0 END)
                         / COUNT(*), 1) AS rate,
                   ROUND(SUM(discount), 2) AS total_discount,
                   ROUND(AVG(CASE WHEN discount > 0 THEN discount END), 0) AS avg_discount,
                   ROUND(100.0 * SUM(discount)
                         / NULLIF(SUM(((total_inc_vat/1.07 - COALESCE(service_charge,0))) + discount),0), 1) AS disc_pct_of_sales
            FROM sales_transaction st
            JOIN branches b ON b.branch_id = st.branch_id
            {where}
            GROUP BY b.name
            ORDER BY total_discount DESC
            """,
            params,
        ).fetchall()

        trend = con.execute(
            f"""
            SELECT strftime('%Y-%m', st.sold_at) AS period_month,
                   COUNT(*) AS total_tx,
                   SUM(CASE WHEN discount > 0 THEN 1 ELSE 0 END) AS discount_tx,
                   ROUND(100.0 * SUM(CASE WHEN discount > 0 THEN 1 ELSE 0 END)
                         / COUNT(*), 1) AS rate,
                   ROUND(SUM(discount), 2) AS total_discount
            FROM sales_transaction st
            JOIN branches b ON b.branch_id = st.branch_id
            {trend_where}
            GROUP BY period_month
            ORDER BY period_month
            """,
            trend_params,
        ).fetchall()

        top_cashiers = con.execute(
            f"""
            SELECT st.cashier,
                   b.name AS branch,
                   COUNT(*) AS total_tx,
                   SUM(CASE WHEN discount > 0 THEN 1 ELSE 0 END) AS discount_tx,
                   ROUND(100.0 * SUM(CASE WHEN discount > 0 THEN 1 ELSE 0 END)
                         / COUNT(*), 1) AS rate,
                   ROUND(SUM(discount), 2) AS total_discount,
                   ROUND(AVG(CASE WHEN discount > 0 THEN discount END), 0) AS avg_discount,
                   ROUND(100.0 * SUM(discount)
                         / NULLIF(SUM(((total_inc_vat/1.07 - COALESCE(service_charge,0))) + discount),0), 1) AS disc_pct_of_sales,
                   SUM(CASE WHEN discount > 0 AND COALESCE(print_times,0) > 1 THEN 1 ELSE 0 END) AS reprinted_disc_bills,
                   MAX(COALESCE(print_times, 0)) AS max_print_times
            FROM sales_transaction st
            JOIN branches b ON b.branch_id = st.branch_id
            {where}
            GROUP BY st.cashier, b.name
            HAVING SUM(discount) > 0
            ORDER BY b.name, total_discount DESC
            """,
            params,
        ).fetchall()

        # Discount PATTERN groups — bucket each discounted bill by discount / gross ratio
        pattern_groups = con.execute(
            f"""
            WITH t AS (
              SELECT discount AS d,
                     (((total_inc_vat/1.07 - COALESCE(service_charge,0))) + discount) AS gross
              FROM sales_transaction st JOIN branches b ON b.branch_id = st.branch_id
              {where} AND discount > 0
            )
            SELECT CASE
                     WHEN gross <= 0          THEN 'ไม่มียอดบิล (เฉพาะส่วนลด)'
                     WHEN d >= gross          THEN '100% (ฟรี/เท่ายอดบิล)'
                     WHEN d*1.0/gross >= 0.5  THEN '50% ขึ้นไป'
                     WHEN d*1.0/gross >= 0.2  THEN '20–50%'
                     WHEN d*1.0/gross >= 0.1  THEN '10–20%'
                     ELSE 'ต่ำกว่า 10%' END AS grp,
                   COUNT(*) AS bills,
                   ROUND(SUM(d), 0) AS baht
            FROM t GROUP BY grp ORDER BY baht DESC
            """,
            params,
        ).fetchall()

        # Most common fixed discount amounts (likely coupons / standard promos)
        common_amounts = con.execute(
            f"""
            SELECT discount AS amount, COUNT(*) AS bills, ROUND(SUM(discount),0) AS baht
            FROM sales_transaction st JOIN branches b ON b.branch_id = st.branch_id
            {where} AND discount > 0
            GROUP BY discount ORDER BY bills DESC LIMIT 15
            """,
            params,
        ).fetchall()

    # Flag outlier cashiers: discount % of sales >= 2× the chain median (min 4%)
    cashiers = [dict(r) for r in top_cashiers]
    pcts = sorted(c["disc_pct_of_sales"] for c in cashiers if c.get("disc_pct_of_sales") is not None)
    median = pcts[len(pcts)//2] if pcts else 0
    flag_at = max(median * 2, 4.0)
    for c in cashiers:
        c["is_outlier"] = (c.get("disc_pct_of_sales") or 0) >= flag_at

    # Enrich overall with the rate fields the by-branch rows already carry, so the
    # summary KPIs have both: % of bills discounted, and discount as % of sales.
    ov = dict(overall) if overall else {}
    if ov:
        _tt = ov.get("total_tx") or 0
        _gs = ov.get("gross_sales") or 0
        ov["discount_rate"] = round(100.0 * (ov.get("discount_tx") or 0) / _tt, 1) if _tt else 0
        ov["disc_pct_of_sales"] = round(100.0 * (ov.get("total_discount") or 0) / _gs, 2) if _gs else 0
    return {
        "overall":      ov,
        "by_branch":    [dict(r) for r in by_branch],
        "trend":        [dict(r) for r in trend],
        "top_cashiers": cashiers,
        "pattern_groups": [dict(r) for r in pattern_groups],
        "common_amounts": [dict(r) for r in common_amounts],
        "outlier_threshold_pct": round(flag_at, 1),
    }


# ---------------------------------------------------------------------------
# /api/analysis/discount_bills  -  individual bills (receipts) with a discount
# ---------------------------------------------------------------------------
@router.get("/api/analysis/discount_bills")
def analysis_discount_bills(
    month:        Optional[str]   = Query(None),
    month_from:   Optional[str]   = Query(None),
    month_to:     Optional[str]   = Query(None),
    branch:       Optional[str]   = Query(None),
    cashier:      Optional[str]   = Query(None),
    min_discount: float           = Query(0.0, ge=0),
    amount:       Optional[float] = Query(None),
    min_pct:      Optional[float] = Query(None),
    max_pct:      Optional[float] = Query(None),
    gross_zero:   bool            = Query(False),
    payment:        Optional[str]   = Query(None, description="filter by dominant payment code (e.g. Cash, Credit)"),
    reprinted_only: bool            = Query(False, description="only bills printed more than once"),
    sort:           str             = Query("discount", pattern="^(discount|pct|date|total|reprints)$"),
    limit:          int             = Query(100, ge=1, le=1000),
    date_from:      Optional[str]   = Query(None),
    date_to:        Optional[str]   = Query(None),
):
    """Receipt-level drill-down of discounted bills, newest/biggest first.

    Lets a manager see the actual bills behind an abnormally high discount total —
    receipt no, date/time, cashier, table, bill value and the discount given, plus
    the discount as a % of the bill's gross. Also returns a summary (count, total,
    average, max, and the p95 threshold) so "abnormally high" can be flagged.

    Drill filters used by the Discounts tab:
      amount=N      → bills with exactly that discount value (common fixed amounts).
      min_pct/max_pct → discount as % of bill gross within [min,max) (pattern buckets).
      gross_zero=1  → bills whose gross is <= 0 (discount-only / zeroed bills).
    """
    gross_expr = "(st.food_subtotal + st.drink_subtotal + st.other_subtotal)"
    # TRUE pre-discount bill value (ex-VAT, ex-service). The three *_subtotal
    # columns only capture à-la-carte lines — for a buffet bill the per-head
    # buffet charge is NOT in them, so discount / subtotal overshot wildly
    # (221 bills read >100%, e.g. discount 2,962 on an 825 subtotal but an
    #  8,851 bill). Reconstruct the real base from what was actually paid
    # (total_inc_vat, minus service, de-VATed) plus the discount added back.
    # For à-la-carte bills this reproduces the subtotal to within ~0.3%.
    base_expr = "((st.total_inc_vat - COALESCE(st.service_charge,0)) / 1.07 + st.discount)"
    filters = ["b.name != 'ALL'", "st.discount > 0"]
    params: dict = {}
    if branch:
        filters.append("b.name = :branch")
        params["branch"] = branch
    if cashier:
        filters.append("st.cashier = :cashier")
        params["cashier"] = cashier
    if min_discount > 0:
        filters.append("st.discount >= :min_discount")
        params["min_discount"] = min_discount
    if amount is not None:
        filters.append("st.discount = :amount")
        params["amount"] = amount
    if reprinted_only:
        filters.append("COALESCE(st.print_times, 0) > 1")
    if gross_zero:
        filters.append(f"{gross_expr} <= 0")
    else:
        # percentage-of-bill bucket (only meaningful when gross > 0)
        if min_pct is not None:
            filters.append(f"{base_expr} > 0 AND 100.0 * st.discount / {base_expr} >= :min_pct")
            params["min_pct"] = min_pct
        if max_pct is not None:
            filters.append(f"{base_expr} > 0 AND 100.0 * st.discount / {base_expr} < :max_pct")
            params["max_pct"] = max_pct
    _apply_period_filter(filters, params, date_from, date_to, month_from, month_to, month)
    where = "WHERE " + " AND ".join(filters)

    order = {
        "discount": "st.discount DESC",
        "pct":      "disc_pct DESC",
        "date":     "st.sold_at DESC",
        "total":    "st.total_inc_vat DESC",
        "reprints": "COALESCE(st.print_times, 0) DESC, st.discount DESC",
    }[sort]
    params["limit"] = limit

    with get_db() as con:
        # Per-bill discount reason + coupon name + payment method (synced via
        # sync_mssql). Tables/columns may be absent on a DB synced before they
        # existed → degrade gracefully (NULL / 0).
        tabs = {r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name IN "
            "('pos_discount_reason','coupon_series','sale_payment','sale_bill_item','sale_bill_cogs')")}
        stcols = {r[1] for r in con.execute("PRAGMA table_info(sales_transaction)")}
        has_type = {"discount_reason_id", "coupon_series_id"} <= stcols
        join_r = " LEFT JOIN pos_discount_reason dr ON dr.reason_id = st.discount_reason_id" if ("pos_discount_reason" in tabs and has_type) else ""
        join_c = " LEFT JOIN coupon_series cs ON cs.coupon_series_id = st.coupon_series_id" if ("coupon_series" in tabs and has_type) else ""
        sel_r = "dr.reason_desc" if join_r else "NULL"
        sel_c = "cs.name" if join_c else "NULL"
        # Per-bill BoM COGS → powers the กำไร/ขาดทุน (P&L) column in the table and seeds
        # the per-bill P&L drill. SAME source & priority as /reports/pnl-bill-items: the
        # LIVE sum of sale_bill_item menu lines (qty × effective unit cost, BUFFET tier=0)
        # when those lines exist (ALL discount bills, full synced history), else the
        # rolling 31-day sale_bill_cogs snapshot. NULL when neither is present (pre-sync)
        # → the column shows "—". Note: net revenue is total_inc_vat/1.07 − service_charge (Sale.Total is
        # ALREADY post-discount), so the client renders P&L = net − COGS — the same
        # definition as บิลขาดทุน GP, NOT net − discount.
        if "sale_bill_item" in tabs:
            _build_sku_effective_cost(con, force=False)   # ensure the unit-cost lookup exists
            _live_cogs = (f"(SELECT SUM(i.qty * CASE WHEN s2.sku_code IN {_EASY_TIER_CODES_SQL} THEN 0 "
                          f"ELSE COALESCE((SELECT sec.recipe_cost FROM sku_effective_cost sec "
                          f"WHERE sec.sku_id=i.sku_id),0) END) "
                          f"FROM sale_bill_item i JOIN skus s2 ON s2.sku_id=i.sku_id "
                          f"WHERE i.receipt_no=st.receipt_no)")
        else:
            _live_cogs = "NULL"
        _snap_cogs = ("(SELECT c.cogs_bom FROM sale_bill_cogs c WHERE c.receipt_no=st.receipt_no)"
                      if "sale_bill_cogs" in tabs else "NULL")
        sel_cogs = (f"ROUND(COALESCE({_live_cogs}, {_snap_cogs}))"
                    if (_live_cogs != "NULL" or _snap_cogs != "NULL") else "NULL")
        # Payment method per receipt. A bill can split across codes → show the
        # DOMINANT (largest-amount) code; payment_n = how many distinct codes.
        has_pay = "sale_payment" in tabs
        _pc = "CASE WHEN UPPER(sp.payment_code)='CASH' THEN 'Cash' ELSE sp.payment_code END"
        sel_pay  = (f"(SELECT {_pc} FROM sale_payment sp WHERE sp.branch_id=st.branch_id "
                    "AND sp.receipt_no=st.receipt_no ORDER BY sp.amount DESC LIMIT 1)" if has_pay else "NULL")
        sel_payn = (f"(SELECT COUNT(DISTINCT {_pc}) FROM sale_payment sp WHERE sp.branch_id=st.branch_id "
                    "AND sp.receipt_no=st.receipt_no)" if has_pay else "0")
        # payment-method filter (by dominant code). Keep `where_base` (scope WITHOUT
        # the payment filter) for the dropdown option list below.
        where_base = where
        if payment and has_pay:
            filters.append(f"{sel_pay} = :payment")
            params["payment"] = payment
            where = "WHERE " + " AND ".join(filters)
        rows = con.execute(
            f"""
            SELECT st.receipt_no, st.sold_at, st.cashier, st.table_no,
                   st.customer_count,
                   b.name AS branch,
                   ROUND(st.total_inc_vat, 2)                    AS total_inc_vat,
                   ROUND(st.discount, 2)                         AS discount,
                   ROUND(st.sp_discount, 2)                      AS sp_discount,
                   (st.food_subtotal + st.drink_subtotal + st.other_subtotal) AS gross,
                   ROUND({base_expr}, 2)                         AS bill_base,
                   ROUND(100.0 * st.discount / NULLIF({base_expr}, 0), 1) AS disc_pct,
                   {sel_r} AS reason_desc, {sel_c} AS coupon_name,
                   {sel_pay} AS payment_code, {sel_payn} AS payment_n,
                   COALESCE(st.print_times, 0) AS print_times,
                   {sel_cogs} AS cogs_bom
            FROM sales_transaction st
            JOIN branches b ON b.branch_id = st.branch_id
            {join_r}{join_c}
            {where}
            ORDER BY {order}
            LIMIT :limit
            """,
            params,
        ).fetchall()

        summary = con.execute(
            f"""
            SELECT COUNT(*) AS bills,
                   ROUND(SUM(st.discount), 2) AS total_discount,
                   ROUND(AVG(st.discount), 2) AS avg_discount,
                   ROUND(MAX(st.discount), 2) AS max_discount,
                   SUM(CASE WHEN COALESCE(st.print_times,0) > 1 THEN 1 ELSE 0 END) AS reprinted_bills,
                   MAX(COALESCE(st.print_times, 0)) AS max_print_times
            FROM sales_transaction st
            JOIN branches b ON b.branch_id = st.branch_id
            {where}
            """,
            params,
        ).fetchone()

        # Distinct payment codes available in this scope (ignoring the payment
        # filter) → powers the "filter by วิธีจ่าย" dropdown.
        payment_codes = []
        if has_pay:
            base_params = {k: v for k, v in params.items() if k != "payment"}
            # dominant tender per bill via indexed lookup over only the scope's bills —
            # not a ROW_NUMBER() window over the whole sale_payment table.
            dom_code = ("(SELECT CASE WHEN UPPER(sp.payment_code)='CASH' THEN 'Cash' ELSE sp.payment_code END "
                        "FROM sale_payment sp WHERE sp.branch_id=st.branch_id AND sp.receipt_no=st.receipt_no "
                        "ORDER BY sp.amount DESC LIMIT 1)")
            pc_rows = con.execute(
                f"""
                SELECT code, COUNT(*) AS n FROM (
                  SELECT {dom_code} AS code
                  FROM sales_transaction st JOIN branches b ON b.branch_id = st.branch_id
                  {where_base}
                ) WHERE code IS NOT NULL
                GROUP BY code ORDER BY n DESC
                """,
                base_params,
            ).fetchall()
            payment_codes = [r["code"] for r in pc_rows if r["code"]]

    # p95 of discount amount — anything above is "abnormally high" for this scope.
    discs = sorted((r["discount"] or 0) for r in rows)
    p95 = discs[min(len(discs) - 1, int(len(discs) * 0.95))] if discs else 0
    return {
        "bills": [dict(r) for r in rows],
        "summary": {
            **(dict(summary) if summary else {}),
            "p95_discount": round(p95, 2),
            "returned": len(rows),
        },
        "payment_codes": payment_codes,
        "payment": payment,
    }


# ---------------------------------------------------------------------------
# /api/analysis/discount_composition  -  per-branch discount split by type
# ---------------------------------------------------------------------------
@router.get("/api/analysis/discount_composition")
@ttl_cache
def analysis_discount_composition(
    month:      Optional[str] = Query(None),
    month_from: Optional[str] = Query(None),
    month_to:   Optional[str] = Query(None),
    branch:     Optional[str] = Query(None),
    group:      Optional[str] = Query(None),
):
    """Per-branch discount composition by discount-as-%-of-bill bucket, for a period.

    Lets §9 of the report show the *nature* of each branch's discounting (mostly
    small markdowns vs heavy 50%+/free bills) and compare it month-over-month.
    Buckets mirror the discounts-tab pattern groups. Amounts are baht of discount.
    """
    # fix D2: use reconstructed pre-discount base to avoid overstating disc% on buffet bills
    gross = "((st.total_inc_vat - COALESCE(st.service_charge,0)) / 1.07 + st.discount)"
    bucket = f"""CASE
        WHEN {gross} <= 0            THEN 'zero_gross'
        WHEN st.discount >= {gross}  THEN 'free'
        WHEN st.discount*1.0/{gross} >= 0.5 THEN 'ge50'
        WHEN st.discount*1.0/{gross} >= 0.2 THEN 'p20_50'
        WHEN st.discount*1.0/{gross} >= 0.1 THEN 'p10_20'
        ELSE 'lt10' END"""
    filters = ["b.name != 'ALL'", "st.discount > 0"]
    params: dict = {}
    if branch:
        filters.append("b.name = :branch")
        params["branch"] = branch
    _group_filter(filters, params, group)
    _month_range_filter(filters, params, month_from, month_to, month, col="strftime('%Y-%m', st.sold_at)")
    where = "WHERE " + " AND ".join(filters)

    with get_db() as con:
        rows = con.execute(f"""
            SELECT b.name AS branch, {bucket} AS bucket,
                   COUNT(*) AS bills, ROUND(SUM(st.discount), 2) AS baht
            FROM sales_transaction st JOIN branches b ON b.branch_id = st.branch_id
            {where}
            GROUP BY b.name, bucket
        """, params).fetchall()

    keys = ["free", "ge50", "p20_50", "p10_20", "lt10", "zero_gross"]
    per: dict = {}
    for r in rows:
        d = per.setdefault(r["branch"], {
            "branch": r["branch"], "total_discount": 0.0, "bills": 0,
            "buckets": {k: 0.0 for k in keys}, "bucket_bills": {k: 0 for k in keys},
        })
        d["buckets"][r["bucket"]] = r["baht"] or 0
        d["bucket_bills"][r["bucket"]] = r["bills"] or 0
        d["total_discount"] += r["baht"] or 0
        d["bills"] += r["bills"] or 0
    out = sorted(per.values(), key=lambda x: -x["total_discount"])
    for d in out:
        d["total_discount"] = round(d["total_discount"], 2)
        # dominant bucket by baht
        d["dominant"] = max(d["buckets"], key=lambda k: d["buckets"][k]) if d["total_discount"] else None
    return {"bucket_keys": keys, "branches": out}


# ---------------------------------------------------------------------------
# /api/analysis/discount_leakage  -  per-branch discount KPI boxes + leakage summary
# ---------------------------------------------------------------------------
@router.get("/api/analysis/discount_leakage")
@ttl_cache
def analysis_discount_leakage(
    month:      Optional[str] = Query(None),
    month_from: Optional[str] = Query(None),
    month_to:   Optional[str] = Query(None),
    date_from:  Optional[str] = Query(None),
    date_to:    Optional[str] = Query(None),
):
    """Per-branch discount KPI boxes for the 'Discounts & Leakage' view, plus a
    cross-branch comparison and an 'abnormal usage' summary.

    Always spans ALL branches (this view IS a cross-branch comparison), honouring
    only the period filter. disc% uses the RECONSTRUCTED pre-discount base
    (total_inc_vat/1.07 − service + discount) — the same base as discount_bills,
    NOT food+drink+other_subtotal which undercounts buffet bills and overstates
    disc% (see discount_bills for the rationale). So this %, by design, will not
    match the legacy /api/analysis/discounts disc_pct_of_sales.
    """
    base_expr = "((st.total_inc_vat - COALESCE(st.service_charge,0)) / 1.07 + st.discount)"
    filters = ["b.name != 'ALL'"]
    params: dict = {}
    _apply_period_filter(filters, params, date_from, date_to, month_from, month_to, month)
    where = "WHERE " + " AND ".join(filters)
    eff_mf, eff_mt = _eff_month_range(date_from, date_to, month_from, month_to, month)

    with get_db() as con:
        rows = con.execute(f"""
            SELECT b.name AS branch,
                   COUNT(*) AS total_tx,
                   SUM(CASE WHEN st.discount > 0 THEN 1 ELSE 0 END) AS discount_tx,
                   ROUND(SUM(st.discount), 2) AS total_discount,
                   ROUND(AVG(CASE WHEN st.discount > 0 THEN st.discount END), 0) AS avg_discount,
                   ROUND(SUM({base_expr}), 2) AS gross_base
            FROM sales_transaction st JOIN branches b ON b.branch_id = st.branch_id
            {where}
            GROUP BY b.name
        """, params).fetchall()

        # Per-branch top discount REASON (Sale.DiscountReasonId → DiscountSaleReason),
        # staged into pos_discount_month by sync. Absent on older DBs → skip gracefully.
        reasons_staged = bool(con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='pos_discount_month'").fetchone())
        reason_rows = []
        if reasons_staged:
            rfilters = ["b.name != 'ALL'"]; rparams: dict = {}
            _month_range_filter(rfilters, rparams, eff_mf, eff_mt, col="dm.period_month")
            reason_rows = con.execute(f"""
                SELECT b.name AS branch, COALESCE(dm.reason_id,0) AS reason_id,
                       COALESCE(dr.reason_desc,
                                CASE WHEN COALESCE(dm.reason_id,0)=0 THEN 'ไม่ระบุ (ไม่เลือกเหตุผล)'
                                     ELSE 'รหัส '||dm.reason_id END) AS reason,
                       ROUND(SUM(dm.amount)) AS amount, SUM(dm.bills) AS bills
                FROM pos_discount_month dm JOIN branches b ON b.branch_id = dm.branch_id
                LEFT JOIN pos_discount_reason dr ON dr.reason_id = dm.reason_id
                WHERE {' AND '.join(rfilters)}
                GROUP BY b.name, COALESCE(dm.reason_id,0), reason
            """, rparams).fetchall()

    # Build per-branch reason map: top reason (by amount) + 'untyped' share (the
    # biggest controllable leak — cashier discounted without picking a reason).
    by_reason: dict = {}
    for r in reason_rows:
        by_reason.setdefault(r["branch"], []).append(dict(r))
    reason_top: dict = {}
    for br, lst in by_reason.items():
        tot = sum(x["amount"] or 0 for x in lst) or 0
        top = max(lst, key=lambda x: x["amount"] or 0) if lst else None
        untyped = next((x for x in lst if x["reason_id"] == 0), None)
        reason_top[br] = {
            "top_reason":        (top["reason"] if top else None),
            "top_reason_amount": (top["amount"] if top else 0),
            "top_reason_pct":    (round(100 * (top["amount"] or 0) / tot, 1) if (top and tot) else 0),
            "untyped_pct":       (round(100 * (untyped["amount"] or 0) / tot, 1) if (untyped and tot) else 0),
        }

    branches = []
    for r in rows:
        d = dict(r)
        gb = d.get("gross_base") or 0
        d["disc_pct"]      = round(100.0 * (d.get("total_discount") or 0) / gb, 2) if gb > 0 else 0
        d["discount_rate"] = round(100.0 * (d.get("discount_tx") or 0) / d["total_tx"], 1) if d["total_tx"] else 0
        d.update(reason_top.get(d["branch"], {
            "top_reason": None, "top_reason_amount": 0, "top_reason_pct": 0, "untyped_pct": 0}))
        branches.append(d)

    # Chain baseline (weighted): total discount / total pre-discount base, all branches.
    chain_disc = sum(b.get("total_discount") or 0 for b in branches)
    chain_base = sum(b.get("gross_base") or 0 for b in branches)
    chain_tx   = sum(b.get("total_tx") or 0 for b in branches)
    chain_dtx  = sum(b.get("discount_tx") or 0 for b in branches)
    chain_pct  = round(100.0 * chain_disc / chain_base, 2) if chain_base > 0 else 0

    # Abnormal = disc% clearly above the typical branch: >= 1.5× the branch median
    # (floor 3% so nothing is flagged when everyone discounts lightly) AND above the
    # chain average. Threshold is returned so the UI can show the rule.
    pcts = sorted(b["disc_pct"] for b in branches if (b.get("gross_base") or 0) > 0)
    median = pcts[len(pcts)//2] if pcts else 0
    threshold = round(max(median * 1.5, 3.0), 2)
    for b in branches:
        b["vs_avg_pts"]     = round(b["disc_pct"] - chain_pct, 2)
        b["ratio_vs_chain"] = round(b["disc_pct"] / chain_pct, 2) if chain_pct > 0 else None
        b["is_abnormal"]    = (b.get("gross_base") or 0) > 0 and b["disc_pct"] >= threshold and b["disc_pct"] > chain_pct
    branches.sort(key=lambda x: -x["disc_pct"])

    abnormal = [b for b in branches if b["is_abnormal"]]
    if abnormal:
        parts = [f"{b['branch']} {b['disc_pct']:.2f}%"
                 + (f" (×{b['ratio_vs_chain']:.2f} ของค่าเฉลี่ย)" if b["ratio_vs_chain"] else "")
                 + (f", ส่วนใหญ่: {b['top_reason']}" if b.get("top_reason") else "")
                 for b in abnormal[:6]]
        headline = (f"🚩 {len(abnormal)} สาขาใช้ส่วนลดสูงผิดปกติ (เกิน {threshold:.2f}% ของยอดก่อนลด · "
                    f"ค่าเฉลี่ยทุกสาขา {chain_pct:.2f}%): " + " · ".join(parts))
    else:
        headline = (f"✓ ไม่พบสาขาที่ใช้ส่วนลดผิดปกติ — ทุกสาขาต่ำกว่าเกณฑ์ {threshold:.2f}% "
                    f"(ค่าเฉลี่ยทุกสาขา {chain_pct:.2f}% ของยอดก่อนลด)")

    return {
        "chain": {"total_discount": round(chain_disc, 2), "gross_base": round(chain_base, 2),
                  "disc_pct": chain_pct, "total_tx": chain_tx, "discount_tx": chain_dtx,
                  "discount_rate": round(100.0 * chain_dtx / chain_tx, 1) if chain_tx else 0,
                  "median_branch_pct": round(median, 2), "threshold_pct": threshold,
                  "branch_count": len(branches)},
        "branches": branches,
        "abnormal_branches": [b["branch"] for b in abnormal],
        "summary": headline,
        "reasons_staged": reasons_staged,
    }


# ---------------------------------------------------------------------------
# /api/analysis/payment_discount  -  payment method × discount cross-analysis
# ---------------------------------------------------------------------------
@router.get("/api/analysis/payment_discount")
@ttl_cache
def analysis_payment_discount(
    month:      Optional[str] = Query(None),
    month_from: Optional[str] = Query(None),
    month_to:   Optional[str] = Query(None),
    date_from:  Optional[str] = Query(None),
    date_to:    Optional[str] = Query(None),
):
    """Payment-method × discount cross-analysis for the Discounts tab:
      • mix       — payment-code split on discounted bills (by dominant code)
      • heaviest  — which payment type gets the deepest discounts (avg disc% of bill)
      • cash_heavy — the leakage flag: cash-paid bills discounted >=20% of bill,
                     by branch (cash + big markdown = highest shrinkage risk)
    disc% uses the reconstructed pre-discount base. Payment code is Cash-normalized
    at query level (POS has 'CASH'/'Cash' dups). Needs the per-bill sale_payment
    table (sync_mssql) → returns available=False if it isn't synced yet.
    """
    base = "((st.total_inc_vat - COALESCE(st.service_charge,0))/1.07 + st.discount)"
    HEAVY = 0.20   # discount >= 20% of the (pre-discount) bill = "heavy"
    filters = ["b.name != 'ALL'", "st.discount > 0"]
    params: dict = {}
    _apply_period_filter(filters, params, date_from, date_to, month_from, month_to, month)
    where = "WHERE " + " AND ".join(filters)

    with get_db() as con:
        if not con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='sale_payment'").fetchone():
            return {"available": False, "mix": [], "heaviest": [], "cash_heavy": {},
                    "heavy_pct": int(HEAVY * 100)}
        # Dominant (largest-amount) payment code per discounted receipt, Cash-normalised.
        # Computed per period bill via an indexed (branch_id, receipt_no, amount) lookup
        # in a derived table — NOT a ROW_NUMBER() window over the entire sale_payment
        # table (that scan made this endpoint ~700ms). bill_src yields one row per
        # discounted bill in scope with its dominant tender; both aggregates reuse it.
        dom_code = ("(SELECT CASE WHEN UPPER(sp.payment_code)='CASH' THEN 'Cash' ELSE sp.payment_code END "
                    "FROM sale_payment sp WHERE sp.branch_id=st.branch_id AND sp.receipt_no=st.receipt_no "
                    "ORDER BY sp.amount DESC LIMIT 1)")
        bill_src = (f"SELECT b.name AS branch, st.discount AS discount, {base} AS basev, {dom_code} AS code "
                    f"FROM sales_transaction st JOIN branches b ON b.branch_id=st.branch_id {where}")
        rows = con.execute(f"""
            SELECT code, COUNT(*) AS bills, ROUND(SUM(discount)) AS discount,
                   ROUND(100.0 * SUM(discount) / NULLIF(SUM(basev), 0), 1) AS disc_pct,
                   ROUND(AVG(discount)) AS avg_discount
            FROM ({bill_src})
            WHERE code IS NOT NULL
            GROUP BY code ORDER BY bills DESC""", params).fetchall()
        mix = [dict(r) for r in rows]

        # cash + heavy discount, by branch (the leakage flag)
        ch_rows = con.execute(f"""
            SELECT branch, COUNT(*) AS bills, ROUND(SUM(discount)) AS discount
            FROM ({bill_src})
            WHERE code='Cash' AND basev > 0 AND discount * 1.0 / basev >= {HEAVY}
            GROUP BY branch ORDER BY discount DESC""", params).fetchall()
        cash_heavy_branches = [dict(r) for r in ch_rows]

    total_bills = sum(m["bills"] for m in mix)
    total_disc  = sum(m["discount"] or 0 for m in mix)
    # heaviest payment types (>=50 bills) by disc% of bill
    heaviest = sorted([m for m in mix if m["bills"] >= 50],
                      key=lambda m: -(m["disc_pct"] or 0))[:6]
    ch_bills = sum(b["bills"] for b in cash_heavy_branches)
    ch_disc  = sum(b["discount"] or 0 for b in cash_heavy_branches)
    top = cash_heavy_branches[0] if cash_heavy_branches else None
    return {
        "available": True,
        "heavy_pct": int(HEAVY * 100),
        "total_discount_bills": total_bills,
        "total_discount": total_disc,
        "mix": mix[:14],
        "heaviest": heaviest,
        "cash_heavy": {
            "bills": ch_bills, "discount": ch_disc,
            "share_of_discount": round(100.0 * ch_disc / total_disc, 1) if total_disc else 0,
            "branches": cash_heavy_branches[:10],
            "top_branch": (top["branch"] if top else None),
            "top_branch_discount": (top["discount"] if top else 0),
        },
    }


# ---------------------------------------------------------------------------
# /api/analysis/discount_alerts  -  consolidated discount-risk alert engine
# ---------------------------------------------------------------------------
@router.get("/api/analysis/discount_alerts")
@ttl_cache
def analysis_discount_alerts(
    month:      Optional[str] = Query(None),
    month_from: Optional[str] = Query(None),
    month_to:   Optional[str] = Query(None),
    date_from:  Optional[str] = Query(None),
    date_to:    Optional[str] = Query(None),
):
    """Prioritized discount-risk flags so leakage/fraud is seen without drilling:
      • cash_heavy — branches with big cash + heavy-discount (≥20%) totals
      • untyped    — branches discounting mostly without a reason (control gap)
      • cashier    — cashiers whose disc% is far above the chain median
      • repeat     — same cashier + exact discount amount + cash, repeated (manual-discount abuse)
      • late       — discounts concentrated after 22:00 by branch
    Reconstructed pre-discount base; cash signals need sale_payment (degrade gracefully).
    Each flag carries a `drill` so the UI can open the matching discount_bills view.
    """
    base = "((st.total_inc_vat - COALESCE(st.service_charge,0))/1.07 + st.discount)"
    TH = {"cash_pct": 20, "cash_min": 50000, "untyped_pct": 80, "untyped_min": 50000,  # DEFAULTS
          "cashier_mult": 2.0, "cashier_floor": 5.0, "cashier_min_disc": 30000,
          "repeat_min": 30, "late_from": 22, "late_min_bills": 40, "late_min_share": 25.0,
          "void_min": 20000}
    filters = ["b.name != 'ALL'", "st.discount > 0"]
    params: dict = {}
    _apply_period_filter(filters, params, date_from, date_to, month_from, month_to, month)
    where = "WHERE " + " AND ".join(filters)
    eff_mf, eff_mt = _eff_month_range(date_from, date_to, month_from, month_to, month)
    flags = []
    def thb(x): return f"{(x or 0):,.0f}"
    with get_db() as con:
        # Configurable overrides (Settings key 'discount_alert_thresholds', JSON).
        # SECURITY: these values are interpolated into SQL → accept only known keys
        # and coerce to numbers; never let a raw string through.
        try:
            row = con.execute("SELECT value FROM app_settings WHERE key='discount_alert_thresholds'").fetchone()
            if row and row[0]:
                stored = _json.loads(row[0])
                for _k in TH:
                    if _k in stored:
                        try: TH[_k] = float(stored[_k])
                        except (TypeError, ValueError): pass
        except Exception:
            pass
        has_pay = bool(con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='sale_payment'").fetchone())
        dom = ("WITH dom AS (SELECT branch_id, receipt_no, "
               "CASE WHEN UPPER(payment_code)='CASH' THEN 'Cash' ELSE payment_code END code, "
               "ROW_NUMBER() OVER (PARTITION BY branch_id,receipt_no ORDER BY amount DESC) rn FROM sale_payment)")

        # 1) cash + heavy discount, by branch
        if has_pay:
            for r in con.execute(f"""{dom}
                SELECT b.name br, COUNT(*) bills, ROUND(SUM(st.discount)) disc
                FROM sales_transaction st JOIN branches b ON b.branch_id=st.branch_id
                JOIN dom d ON d.branch_id=st.branch_id AND d.receipt_no=st.receipt_no AND d.rn=1
                {where} AND d.code='Cash' AND {base}>0 AND st.discount*1.0/{base} >= {TH['cash_pct']/100.0}
                GROUP BY b.name HAVING SUM(st.discount) >= {TH['cash_min']} ORDER BY disc DESC LIMIT 6""", params):
                flags.append({"type": "cash_heavy", "severity": "high" if (r["disc"] or 0) >= 200000 else "med",
                    "branch": r["br"], "title": "เงินสด + ลดหนัก",
                    "text": f"{r['br']}: บิลเงินสดที่ลด ≥{TH['cash_pct']}% — {r['bills']:,} บิล · ฿{thb(r['disc'])}",
                    "amount": r["disc"], "drill": {"branch": r["br"], "payment": "Cash"}})

        # 2) untyped-reason share high, by branch (from pos_discount_month)
        if con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='pos_discount_month'").fetchone():
            rf = ["b.name!='ALL'"]; rp: dict = {}
            _month_range_filter(rf, rp, eff_mf, eff_mt, col="dm.period_month")
            for r in con.execute(f"""
                SELECT b.name br, ROUND(SUM(dm.amount)) tot,
                       ROUND(SUM(CASE WHEN COALESCE(dm.reason_id,0)=0 THEN dm.amount ELSE 0 END)) unty
                FROM pos_discount_month dm JOIN branches b ON b.branch_id=dm.branch_id
                WHERE {' AND '.join(rf)} GROUP BY b.name""", rp):
                tot, unty = (r["tot"] or 0), (r["unty"] or 0)
                if tot >= TH["untyped_min"] and unty >= TH["untyped_min"] and 100.0*unty/tot >= TH["untyped_pct"]:
                    pct = round(100.0*unty/tot, 1)
                    flags.append({"type": "untyped", "severity": "med", "branch": r["br"], "title": "ลดไม่ระบุเหตุผล",
                        "text": f"{r['br']}: ลดโดยไม่เลือกเหตุผล {pct}% (฿{thb(unty)}) — คุมนโยบายไม่ได้",
                        "amount": unty, "drill": {"branch": r["br"]}})

        # 3) cashier disc% outliers (>= 2× branch-cashier median, floor 5%)
        crows = con.execute(f"""
            SELECT st.cashier ca, b.name br, COUNT(*) bills, ROUND(SUM(st.discount)) disc,
                   ROUND(100.0*SUM(st.discount)/NULLIF(SUM({base}),0),1) pct
            FROM sales_transaction st JOIN branches b ON b.branch_id=st.branch_id
            {where} AND st.cashier<>'' GROUP BY st.cashier, b.name HAVING SUM(st.discount) >= {TH['cashier_min_disc']}""",
            params).fetchall()
        pcts = sorted([r["pct"] for r in crows if r["pct"] is not None])
        med = pcts[len(pcts)//2] if pcts else 0
        thr = max(med*TH["cashier_mult"], TH["cashier_floor"])
        ncash = 0
        for r in sorted(crows, key=lambda x: -(x["pct"] or 0)):
            if (r["pct"] or 0) >= thr and ncash < 6:
                ncash += 1
                flags.append({"type": "cashier", "severity": "high" if (r["pct"] or 0) >= thr*1.5 else "med",
                    "branch": r["br"], "cashier": r["ca"], "title": "แคชเชียร์ลดเกินเกณฑ์",
                    "text": f"{r['ca']} ({r['br']}): ลด {r['pct']}% ของยอด (เกณฑ์ {thr:.1f}%) · ฿{thb(r['disc'])}",
                    "amount": r["disc"], "drill": {"branch": r["br"], "cashier": r["ca"]}})

        # 4) repeat exact-amount cash discounts by the same cashier (manual-discount abuse)
        if has_pay:
            for r in con.execute(f"""{dom}
                SELECT b.name br, st.cashier ca, st.discount amt, COUNT(*) n, ROUND(SUM(st.discount)) tot
                FROM sales_transaction st JOIN branches b ON b.branch_id=st.branch_id
                JOIN dom d ON d.branch_id=st.branch_id AND d.receipt_no=st.receipt_no AND d.rn=1
                {where} AND d.code='Cash' AND st.cashier<>''
                GROUP BY b.name, st.cashier, st.discount HAVING COUNT(*) >= {TH['repeat_min']}
                ORDER BY n DESC LIMIT 5""", params):
                flags.append({"type": "repeat", "severity": "high" if r["n"] >= 80 else "med",
                    "branch": r["br"], "cashier": r["ca"], "title": "ลดเงินสดค่าเดิมซ้ำ",
                    "text": f"{r['ca']} ({r['br']}): ลดเงินสด ฿{thb(r['amt'])} ซ้ำ {r['n']:,} ครั้ง (฿{thb(r['tot'])})",
                    "amount": r["tot"], "drill": {"branch": r["br"], "cashier": r["ca"], "payment": "Cash", "amount": r["amt"]}})

        # 5) late-hour discount clustering, by branch
        for r in con.execute(f"""
            SELECT b.name br,
                   SUM(CASE WHEN CAST(strftime('%H',st.sold_at) AS INT) >= {TH['late_from']} THEN 1 ELSE 0 END) late_bills,
                   COUNT(*) bills,
                   ROUND(SUM(CASE WHEN CAST(strftime('%H',st.sold_at) AS INT) >= {TH['late_from']} THEN st.discount ELSE 0 END)) late_disc
            FROM sales_transaction st JOIN branches b ON b.branch_id=st.branch_id
            {where} GROUP BY b.name""", params):
            lb, tb = (r["late_bills"] or 0), (r["bills"] or 1)
            share = 100.0*lb/tb
            if lb >= TH["late_min_bills"] and share >= TH["late_min_share"]:
                flags.append({"type": "late", "severity": "med", "branch": r["br"], "title": "ลดช่วงดึก",
                    "text": f"{r['br']}: ลดหลัง {TH['late_from']}:00 — {lb:,} บิล ({share:.0f}% ของบิลลด) · ฿{thb(r['late_disc'])}",
                    "amount": r["late_disc"], "drill": {"branch": r["br"]}})

        # 6) baseline drift — a branch's latest month disc% vs its OWN trailing-3 avg
        # (full history, ignores the period filter; catches a branch trending worse).
        bd: dict = {}
        for r in con.execute(f"""
            SELECT b.name br, strftime('%Y-%m', st.sold_at) m,
                   ROUND(100.0*SUM(st.discount)/NULLIF(SUM({base}),0),2) pct, ROUND(SUM(st.discount)) disc
            FROM sales_transaction st JOIN branches b ON b.branch_id=st.branch_id
            WHERE b.name!='ALL' GROUP BY b.name, m HAVING SUM({base}) > 0"""):
            bd.setdefault(r["br"], []).append((r["m"], r["pct"] or 0, r["disc"] or 0))
        for br_name, series in bd.items():
            series.sort()
            if len(series) < 4:
                continue
            latest = series[-1]
            tavg = sum(x[1] for x in series[-4:-1]) / 3.0   # 3 months before the latest
            if tavg > 0 and latest[1] >= 1.5*tavg and latest[1] >= 3.0 and latest[2] >= 50000:
                flags.append({"type": "drift", "severity": "med", "branch": br_name, "title": "ส่วนลดพุ่งผิดปกติ",
                    "text": f"{br_name}: เดือน {latest[0]} ลด {latest[1]:.1f}% ของยอด (ปกติ ~{tavg:.1f}%) — พุ่ง {latest[1]/tavg:.1f}× เทียบตัวเอง",
                    "amount": latest[2], "drill": {"branch": br_name, "month_from": latest[0], "month_to": latest[0]}})

        # 7) discount applied then bill VOIDED — manipulation risk (needs the
        # discount column on pos_cancel_bill_detail, added 2026-06-09 → guard).
        try:
            ccols = {rr[1] for rr in con.execute("PRAGMA table_info(pos_cancel_bill_detail)")}
        except Exception:
            ccols = set()
        if "discount" in ccols:
            vf = ["b.name!='ALL'"]; vp: dict = {}
            _month_range_filter(vf, vp, eff_mf, eff_mt, col="cd.period_month")
            for r in con.execute(f"""
                SELECT b.name br, COUNT(*) n, ROUND(SUM(cd.discount)) disc
                FROM pos_cancel_bill_detail cd JOIN branches b ON b.branch_id=cd.branch_id
                WHERE {' AND '.join(vf)} AND cd.discount > 0
                GROUP BY b.name HAVING SUM(cd.discount) >= {TH['void_min']} ORDER BY disc DESC LIMIT 6""", vp):
                flags.append({"type": "void", "severity": "high" if (r["disc"] or 0) >= 50000 else "med",
                    "branch": r["br"], "title": "ลดแล้วยกเลิกบิล",
                    "text": f"{r['br']}: บิล 'ลดแล้วถูกยกเลิก' {r['n']:,} ใบ · ส่วนลดรวม ฿{thb(r['disc'])} — เสี่ยงแก้ไข/ลบบิล"})
                    # (no drill: voided bills aren't in sales_transaction; review via cancel-bills detail)

    rank = {"high": 0, "med": 1}
    flags.sort(key=lambda f: (rank.get(f["severity"], 2), -(f.get("amount") or 0)))
    return {
        "flags": flags, "thresholds": TH, "has_payment": has_pay,
        "counts": {"high": sum(1 for f in flags if f["severity"] == "high"),
                   "med":  sum(1 for f in flags if f["severity"] == "med"), "total": len(flags)},
    }


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
               ROUND(COALESCE((SUM(st.total_inc_vat)/1.07 - SUM(COALESCE(st.service_charge,0))), 0), 2) AS revenue,
               ROUND(COALESCE(AVG(st.total_inc_vat/1.07 - COALESCE(st.service_charge,0)), 0), 2) AS avg_ticket,
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
               ROUND((SUM(st.total_inc_vat)/1.07 - SUM(COALESCE(st.service_charge,0))), 2) AS revenue,
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
    all_names = set(
        [br["branch"] for br in a["top_branches"]] +
        [br["branch"] for br in b["top_branches"]] +
        ([br["branch"] for br in c["top_branches"]] if c else []) +
        ([br["branch"] for br in d["top_branches"]] if d else [])
    )

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
        return [{"branch": r["branch"], "period": r["period"], "revenue": r["revenue"] or 0}
                for r in sorted(easy_rows, key=lambda x: (x["branch"], x["period"]))]

    # ── DAILY / WEEKLY: Easy transaction-level ─────────────────────────────────
    period_expr = "DATE(st.sold_at)" if granularity == "daily" else _iso_week_sql('st.sold_at')
    filters, params = _build_period_filters(None, month_from, month_to, date_from, date_to)
    where = "WHERE " + " AND ".join(filters)

    with get_db() as con:
        rows = con.execute(
            f"""
            SELECT b.name AS branch,
                   {period_expr} AS period,
                   ROUND((SUM(st.total_inc_vat)/1.07 - SUM(COALESCE(st.service_charge,0))), 2) AS revenue,
                   COUNT(*) AS tx_count
            FROM sales_transaction st
            JOIN branches b ON b.branch_id = st.branch_id
            {where}
            GROUP BY b.name, period
            ORDER BY b.name, period
            """,
            params,
        ).fetchall()
    return [dict(r) for r in rows]


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
                   ROUND((SUM(st.total_inc_vat)/1.07 - SUM(COALESCE(st.service_charge,0))), 2) AS revenue
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
                   ROUND((SUM(st.total_inc_vat)/1.07 - SUM(COALESCE(st.service_charge,0))), 2) AS revenue
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
    except Exception:
        pass

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
                   ROUND((SUM(st.total_inc_vat)/1.07 - SUM(COALESCE(st.service_charge,0))), 2) AS revenue
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
                           ROUND((SUM(st.total_inc_vat)/1.07 - SUM(COALESCE(st.service_charge,0))), 2) rev
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
            SELECT DATE(st.sold_at) AS day, (SUM(st.total_inc_vat)/1.07 - SUM(COALESCE(st.service_charge,0))) AS rev
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
            f"""SELECT b.name AS branch, (SUM(st.total_inc_vat)/1.07 - SUM(COALESCE(st.service_charge,0))) AS mtd
                FROM sales_transaction st JOIN branches b ON b.branch_id = st.branch_id
                {where} AND strftime('%Y-%m', st.sold_at) = :period
                GROUP BY b.name""", params_cur,
        ).fetchall()
        params_pri = dict(params); params_pri["period"] = prior
        pri = con.execute(
            f"""SELECT b.name AS branch, (SUM(st.total_inc_vat)/1.07 - SUM(COALESCE(st.service_charge,0))) AS rev
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
                   ROUND((SUM(st.total_inc_vat)/1.07 - SUM(COALESCE(st.service_charge,0))),2) AS revenue,
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
                   ROUND((SUM(st.total_inc_vat)/1.07 - SUM(COALESCE(st.service_charge,0))), 2)        AS revenue,
                   ROUND(AVG(st.total_inc_vat/1.07 - COALESCE(st.service_charge,0)), 2)        AS avg_ticket
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
                   ROUND((SUM(st.total_inc_vat)/1.07 - SUM(COALESCE(st.service_charge,0))), 2)             AS revenue,
                   ROUND(AVG(st.total_inc_vat/1.07 - COALESCE(st.service_charge,0)), 2)             AS avg_ticket
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
                   ROUND((SUM(st.total_inc_vat)/1.07 - SUM(COALESCE(st.service_charge,0))), 2) AS revenue
            FROM sales_transaction st JOIN branches b ON b.branch_id = st.branch_id
            {where} GROUP BY b.name ORDER BY revenue DESC""", params).fetchall()
        by_dow = con.execute(f"""
            SELECT CAST(strftime('%w', st.sold_at) AS INTEGER) AS dow,
                   COUNT(*) AS tx_count,
                   ROUND((SUM(st.total_inc_vat)/1.07 - SUM(COALESCE(st.service_charge,0))), 2) AS revenue
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
            SELECT ((st.total_inc_vat/1.07 - COALESCE(st.service_charge,0))) / st.customer_count AS sph,
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
                   ROUND(AVG(((st.total_inc_vat/1.07 - COALESCE(st.service_charge,0))) / st.customer_count), 2) AS avg_sph
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
                ROUND((SUM(st.total_inc_vat)/1.07 - SUM(COALESCE(st.service_charge,0))), 2)               AS revenue,
                ROUND(AVG(st.total_inc_vat/1.07 - COALESCE(st.service_charge,0)), 2)               AS avg_ticket,
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
                ROUND((SUM(st.total_inc_vat)/1.07 - SUM(COALESCE(st.service_charge,0))), 2) AS revenue,
                ROUND(AVG(st.total_inc_vat/1.07 - COALESCE(st.service_charge,0)), 2) AS avg_ticket,
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
    Buffet price-tier mix from SKU sales.
    Detects tiers by matching '350','500','590','799' in category or sku_code or description.
    Also flags a la carte (อลาคาท / Ala Carte) separately.
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

    TIERS = ['350', '500', '590', '799']
    ALACARTE_KEYWORDS = ['อลาคาท', 'ala carte', 'a la carte', 'alacarte', 'อา ลา คาร์ท']
    # Buffet "charge" SKUs = a per-cover price (vs the B0xxx food-item rows that
    # are logged at 0 baht). In this data the whole adult buffet is booked under
    # one generic 'BUFFET' code, so we recover the tier from the realised price.
    CHARGE_CODES = {'BUFFET', 'BUFFET350', 'BUFFET500', 'BUFFET590', 'BUFFET799',
                    'B01', 'B02', 'B07', 'B11'}

    def price_tier(unit_price_ex_vat):
        """Map a per-head ex-VAT price to the nearest legacy tier (midpoints
        425/545/695 between 350·500·590·799)."""
        up = unit_price_ex_vat or 0
        if up < 425: return '350'
        if up < 545: return '500'
        if up < 695: return '590'
        return '799'

    def classify(cat, sku, desc, qty, rev):
        combined = f"{cat} {sku} {desc}".lower()
        for kw in ALACARTE_KEYWORDS:
            if kw.lower() in combined:
                return 'à la carte'
        if sku == 'BUFFETCHILD' or ('เด็ก' in combined and 'บุฟเฟ' in combined):
            return 'เด็ก (Child)'
        for t in TIERS:                       # explicit tier in code/desc wins
            if t in combined:
                return t
        is_buffet_charge = (sku in CHARGE_CODES) or ('บุฟเฟ' in combined) or ('buffet' in combined)
        if is_buffet_charge:
            if qty and rev and rev > 0:        # priced buffet cover → bucket by price
                return price_tier(rev / qty)
            return None                        # 0-baht buffet food item → not a price level
        # Non-buffet regular menu / à-la-carte food: not a buffet price level —
        # exclude so this chart is purely the buffet price-tier mix (covers & ฿).
        return None

    # Aggregate
    totals: dict = {}   # tier -> {qty, revenue}
    monthly: dict = {}  # period_month -> tier -> revenue

    for r in rows:
        tier = classify(r["category"], r["sku_code"], r["description"],
                        r["qty"] or 0, r["revenue"] or 0)
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

    return {"summary": summary, "trend": trend, "tiers": all_tiers, "months": all_months}


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
                   ROUND((SUM(st.total_inc_vat)/1.07 - SUM(COALESCE(st.service_charge,0))), 2)      AS day_revenue,
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
                   (SUM(st.total_inc_vat)/1.07 - SUM(COALESCE(st.service_charge,0)))   AS revenue
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


# ---------------------------------------------------------------------------
# GET /api/buffet/gp/export   🟢7
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
    /api/analysis/table_turnover, used by the ⏰ Time & Flow tab)."""
    if view == "dwell":
        return table_turnover(month=month, month_from=month_from,
                              month_to=month_to, branch=branch)
    return table_turnover_branches(branch=branch, month=month,
                                   month_from=month_from, month_to=month_to)
