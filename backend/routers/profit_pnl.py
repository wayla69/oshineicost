"""Profit & P&L domain router — /api/profit/*, /api/pnl/*, /api/gp/*,
/api/finance/*, moved VERBATIM out of main.py (P1 structure refactor,
step 8). No logic changes; URLs unchanged. Same host-module pattern as
routers/reports.py.
"""
# Shared infrastructure and helpers come from core.py by name — the old
# `globals().update(getattr(main, …))` bootstrap is gone (Phase 2, 2026-09).
from fastapi import Query
from typing import Optional
import json as _json
from rules import avg_ticket_sql, closed_branches, drop_closed, month_window, coverage_from_sums, coverage_sums_sql, food_cost_target, net_revenue_row
from core import (
    _bom_cogs_expr, _bom_costed_rev_expr, _cogs_case, _costed_rev_case, _group_filter,
    _ingredient_family, _iso_week_sql, _month_range_filter, _profit_filters, _tx_filters,
    _unified_cogs_expr, _variant_filter, get_db, ttl_cache,
)
try:
    from .sales import _month_add
except ImportError:
    from sales import _month_add

from fastapi import APIRouter
router = APIRouter()




@router.get("/api/profit/buffet_per_bill")
@ttl_cache
def buffet_per_bill(
    branch:      Optional[str] = Query(None),
    month_from:  Optional[str] = Query(None),
    month_to:    Optional[str] = Query(None),
    granularity: str           = Query("monthly", description="daily | weekly | monthly | quarterly"),
):
    """Per-bill profit deep-dive for the buffet business.

    Joins sales_transaction with the buffet SKU costs via inferred tier.
    For each bill: revenue ex-VAT, estimated buffet BoM cost, profit, margin %.
    Aggregated by the requested granularity.
    """
    if granularity == "daily":
        period_expr = "DATE(st.sold_at)"
    elif granularity == "weekly":
        period_expr = _iso_week_sql('st.sold_at')
    elif granularity == "quarterly":
        period_expr = ("substr(strftime('%Y-%m', st.sold_at), 1, 4) || '-Q' || "
                       "((CAST(substr(strftime('%Y-%m', st.sold_at), 6, 2) AS INTEGER)-1)/3+1)")
    else:
        period_expr = "strftime('%Y-%m', st.sold_at)"

    filters = ["st.source = 'Easy'", "b.name != 'ALL'", "st.customer_count > 0", "st.total_inc_vat > 0"]
    params: dict = {}
    if branch:
        filters.append("b.name = :branch")
        params["branch"] = branch
    _month_range_filter(filters, params, month_from, month_to, None,
                        col="strftime('%Y-%m', st.sold_at)")
    where = "WHERE " + " AND ".join(filters)

    # 2026-06-02: per-bill cost now uses the REAL buffet food-cost ratio per month
    # (unified COGS of buffet items ÷ buffet revenue), NOT a fabricated tier-price
    # assumption. The old code looked up BUFFET350/500/590/799 unit_cost (which is
    # empty) and fell back to a flat 45%-margin guess, so margins were fictional.
    sm_filters = ["b.name != 'ALL'", "sk.business_type IN ('buffet','buffet_tier')"]
    sm_params: dict = {}
    if branch:
        sm_filters.append("b.name = :branch")
        sm_params["branch"] = branch
    _month_range_filter(sm_filters, sm_params, month_from, month_to, None, col="sm.period_month")
    sm_where = "WHERE " + " AND ".join(sm_filters)
    with get_db() as con:
        ratio_by_month = {}
        _rrev_tot = _rcogs_tot = 0.0
        for r in con.execute(f"""
            SELECT sm.period_month AS m,
                   SUM(CASE WHEN sk.business_type='buffet_tier'
                            THEN sm.subtotal_ex_vat ELSE 0 END) AS rev_ex,
                   SUM({_unified_cogs_expr('sm', 'sk')})        AS cogs
            FROM v_sales_sku_monthly sm
            JOIN skus sk     ON sk.sku_id   = sm.sku_id
            JOIN branches b  ON b.branch_id = sm.branch_id
            {sm_where}
            GROUP BY sm.period_month
        """, sm_params).fetchall():
            rev = float(r["rev_ex"] or 0); cg = float(r["cogs"] or 0)
            if rev > 0:
                ratio_by_month[r["m"]] = cg / rev
                _rrev_tot += rev; _rcogs_tot += cg
        overall_ratio = (_rcogs_tot / _rrev_tot) if _rrev_tot else 0.60

        # Pull all eligible buffet bills (per-head spend used only for the spend-bracket mix).
        # Per head = the bill's NET revenue ÷ covers. Until 2026-10 it was food_subtotal ÷ covers,
        # but the buffet charge sits in other_subtotal: food_subtotal is the à-la-carte part only,
        # so ~5 % of bills passed the 200-1500 band (Sep 2026: 2,743 of 48,715) and the
        # dominant bracket described à-la-carte add-ons, not what a guest paid.
        rows = con.execute(f"""
            SELECT b.name AS branch,
                   {period_expr} AS period,
                   strftime('%Y-%m', st.sold_at)                  AS ym,
                   st.customer_count                              AS pax,
                   {net_revenue_row('st')} / st.customer_count    AS per_head,
                   st.total_inc_vat                               AS rev_inc,
                   {net_revenue_row('st')}                        AS rev_ex,
                   st.discount + st.sp_discount                   AS discount
            FROM sales_transaction st
            JOIN branches b ON b.branch_id = st.branch_id
            {where}
              AND ({net_revenue_row('st')} / st.customer_count) BETWEEN 200 AND 1500
        """, params).fetchall()

    # Spend-bracket label from real per-head spend (NOT the dead tier SKU codes)
    def tier_of(per_head):
        if per_head < 450:  return ("≤450",    450)
        if per_head < 600:  return ("450-600", 600)
        if per_head < 720:  return ("600-720", 720)
        return (">720", 800)

    from collections import defaultdict
    agg = defaultdict(lambda: {"bills": 0, "pax": 0, "revenue_ex": 0.0, "revenue_inc": 0.0,
                               "cost": 0.0, "discount": 0.0,
                               "tier_mix": defaultdict(int)})
    overall = {"bills": 0, "pax": 0, "revenue_ex": 0.0, "cost": 0.0,
               "tier_mix": defaultdict(int), "tier_cost_mix": defaultdict(float)}

    for r in rows:
        tier_code, tier_price = tier_of(r["per_head"])
        pax = r["pax"] or 0
        rev_ex_bill = r["rev_ex"] or 0
        # Real cost = bill revenue × that month's buffet food-cost ratio
        cost = rev_ex_bill * ratio_by_month.get(r["ym"], overall_ratio)
        a = agg[r["period"]]
        a["bills"]       += 1
        a["pax"]         += pax
        a["revenue_ex"]  += r["rev_ex"] or 0
        a["revenue_inc"] += r["rev_inc"] or 0
        a["cost"]        += cost
        a["discount"]    += r["discount"] or 0
        a["tier_mix"][tier_code] += pax
        overall["bills"]       += 1
        overall["pax"]         += pax
        overall["revenue_ex"]  += r["rev_ex"] or 0
        overall["cost"]        += cost
        overall["tier_mix"][tier_code] += pax
        overall["tier_cost_mix"][tier_code] += cost

    out_rows = []
    for period in sorted(agg.keys()):
        a = agg[period]
        profit = a["revenue_ex"] - a["cost"]
        margin = (profit / a["revenue_ex"] * 100) if a["revenue_ex"] else 0
        avg_per_bill_rev    = a["revenue_ex"] / a["bills"] if a["bills"] else 0
        avg_per_bill_cost   = a["cost"]       / a["bills"] if a["bills"] else 0
        avg_per_bill_profit = profit          / a["bills"] if a["bills"] else 0
        # Dominant tier this period
        dom = max(a["tier_mix"].items(), key=lambda kv: kv[1], default=(None, 0))
        out_rows.append({
            "period":              period,
            "bills":               a["bills"],
            "pax":                 a["pax"],
            "revenue_ex":          round(a["revenue_ex"], 2),
            "revenue_inc":         round(a["revenue_inc"], 2),
            "cost":                round(a["cost"], 2),
            "profit":              round(profit, 2),
            "margin_pct":          round(margin, 2),
            "avg_per_bill_rev":    round(avg_per_bill_rev, 2),
            "avg_per_bill_cost":   round(avg_per_bill_cost, 2),
            "avg_per_bill_profit": round(avg_per_bill_profit, 2),
            "dominant_tier":       dom[0],
            "tier_mix":            dict(a["tier_mix"]),
        })

    profit_overall = overall["revenue_ex"] - overall["cost"]
    return {
        "granularity": granularity,
        "rows":        out_rows,
        "overall": {
            "bills":              overall["bills"],
            "pax":                overall["pax"],
            "revenue_ex":         round(overall["revenue_ex"], 2),
            "cost":               round(overall["cost"], 2),
            "profit":             round(profit_overall, 2),
            "margin_pct":         round(profit_overall / overall["revenue_ex"] * 100, 2) if overall["revenue_ex"] else 0,
            "avg_per_bill_rev":   round(overall["revenue_ex"] / overall["bills"], 2) if overall["bills"] else 0,
            "avg_per_bill_cost":  round(overall["cost"]       / overall["bills"], 2) if overall["bills"] else 0,
            "avg_per_bill_profit":round(profit_overall        / overall["bills"], 2) if overall["bills"] else 0,
            "tier_mix":           {k: int(v) for k, v in overall["tier_mix"].items()},
            "tier_cost_mix":      {k: round(v, 0) for k, v in overall["tier_cost_mix"].items()},
        },
    }


@router.get("/api/profit/summary")
@ttl_cache
def profit_summary(
    branch:     Optional[str] = Query(None),
    group:      Optional[str] = Query(None),
    month_from: Optional[str] = Query(None),
    month_to:   Optional[str] = Query(None),
    month:      Optional[str] = Query(None),
):
    """Aggregate profit metrics for the period (revenue, COGS, GP, margin, per-bill)."""
    where, params = _profit_filters(branch, group, month_from, month_to, month)
    with get_db() as con:
        row = con.execute(f"""
            SELECT
              ROUND(SUM(ssm.subtotal_ex_vat), 2)               AS revenue,
              ROUND(SUM(ssm.subtotal_ex_vat), 2)               AS revenue_ex_vat,
              ROUND(SUM(ssm.total_inc_vat), 2)                 AS pos_total_inc_vat_diagnostic,
              ROUND(SUM({_bom_cogs_expr()}), 2)                AS cogs,
              {coverage_sums_sql('ssm')},
              SUM(ssm.qty) AS qty_sold
            FROM v_sales_sku_monthly ssm
            JOIN branches b ON b.branch_id = ssm.branch_id
            JOIN skus     sk ON sk.sku_id  = ssm.sku_id
            {where}
        """, params).fetchone()

    revenue        = row["revenue"]                or 0   # ex-VAT (Rule 1)
    revenue_ex     = row["revenue_ex_vat"]         or 0
    cogs           = row["cogs"]                   or 0   # BoM recipe COGS (real)
    gross_profit   = revenue_ex - cogs
    # Margin over TOTAL revenue (honest) — not just costed revenue, which inflated
    # GP to ~72%. With BoM coverage ~70-92%, this reflects the real ~42% margin.
    margin_pct     = round(gross_profit / revenue_ex * 100, 2) if revenue_ex > 0 else None
    # Coverage by PLATES on the app-wide definition (rules.needs_cost_sql /
    # costed_sql) — revenue alone says "fine" for a buffet whose dishes carry ฿0.
    cov            = coverage_from_sums(row)
    coverage_pct   = cov["plates_pct"] if cov["plates_pct"] is not None else 0

    # tx count from sales_transaction (bills only — total_inc_vat is diagnostic, not revenue)
    tx_where, tx_params = _tx_filters(branch, group, month_from, month_to, month)
    with get_db() as con:
        tx_row = con.execute(f"""
            SELECT COUNT(*) AS tx_count,
                   ROUND(SUM(st.total_inc_vat), 2) AS pos_total_inc_vat_diagnostic
            FROM sales_transaction st
            JOIN branches b ON b.branch_id = st.branch_id
            {tx_where}
        """, tx_params).fetchone()
    tx_count = tx_row["tx_count"] or 0

    profit_per_bill = round(gross_profit / tx_count, 2) if tx_count > 0 else 0
    # avg_ticket: revenue (ex-VAT) per bill — switched from inc-VAT total per Rule 1
    avg_ticket      = round(revenue      / tx_count, 2) if tx_count > 0 else 0

    # Monthly trend (Rule 2) — pair the cumulative totals with per-period breakdown
    with get_db() as con:
        trend_rows = con.execute(f"""
            SELECT ssm.period_month                              AS period_month,
              ROUND(SUM(ssm.subtotal_ex_vat), 2)                 AS revenue,
              ROUND(SUM({_bom_cogs_expr()}), 2)                  AS cogs,
              ROUND(SUM({_bom_costed_rev_expr()}), 2)            AS costed_revenue_ex_vat
            FROM v_sales_sku_monthly ssm
            JOIN branches b ON b.branch_id = ssm.branch_id
            JOIN skus     sk ON sk.sku_id  = ssm.sku_id
            {where}
            GROUP BY ssm.period_month
            ORDER BY ssm.period_month
        """, params).fetchall()
    monthly_trend = []
    for tr in trend_rows:
        d = dict(tr)
        rev = d["revenue"] or 0
        gp = rev - (d["cogs"] or 0)   # GP over total revenue (BoM COGS)
        d["gross_profit"] = round(gp, 2)
        d["margin_pct"]   = round(gp / rev * 100, 2) if rev > 0 else None
        monthly_trend.append(d)

    return {
        "revenue":           revenue,
        "revenue_ex_vat":    revenue_ex,
        "cogs":              cogs,
        "gross_profit":      round(gross_profit, 2),
        "margin_pct":        margin_pct,
        "tx_count":          tx_count,
        "avg_ticket":        avg_ticket,
        "profit_per_bill":   profit_per_bill,
        "cost_coverage_pct": coverage_pct,
        "cost_coverage_revenue_pct": cov["revenue_pct"],
        "uncosted_plates":   cov["uncosted_plates"],
        "qty_sold":          row["qty_sold"] or 0,
        "monthly_trend":     monthly_trend,
        "pos_total_inc_vat_diagnostic": row["pos_total_inc_vat_diagnostic"] or 0,
    }


@router.get("/api/profit/trend")
@ttl_cache
def profit_trend(
    branch:     Optional[str] = Query(None),
    group:      Optional[str] = Query(None),
    month_from: Optional[str] = Query(None),
    month_to:   Optional[str] = Query(None),
):
    """Monthly trend of revenue, COGS, gross profit, margin."""
    where, params = _profit_filters(branch, group, month_from, month_to, None)
    with get_db() as con:
        rows = con.execute(f"""
            SELECT ssm.period_month,
              ROUND(SUM(ssm.subtotal_ex_vat), 2)   AS revenue_ex_vat,
              ROUND(SUM({_bom_cogs_expr()}), 2)    AS cogs,
              ROUND(SUM({_bom_costed_rev_expr()}), 2)  AS costed_revenue_ex_vat
            FROM v_sales_sku_monthly ssm
            JOIN branches b ON b.branch_id = ssm.branch_id
            JOIN skus     sk ON sk.sku_id  = ssm.sku_id
            {where}
            GROUP BY ssm.period_month
            ORDER BY ssm.period_month
        """, params).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        rev = d["revenue_ex_vat"] or 0
        gp = rev - (d["cogs"] or 0)   # BoM COGS, margin over total revenue
        d["gross_profit"] = round(gp, 2)
        d["margin_pct"]   = round(gp / rev * 100, 2) if rev > 0 else None
        out.append(d)
    return out


@router.get("/api/profit/by-branch")
@ttl_cache
def profit_by_branch(
    group:      Optional[str] = Query(None),
    month_from: Optional[str] = Query(None),
    month_to:   Optional[str] = Query(None),
    month:      Optional[str] = Query(None),
):
    """Profit per branch."""
    where, params = _profit_filters(None, group, month_from, month_to, month)
    with get_db() as con:
        rows = con.execute(f"""
            SELECT b.branch_id, b.name AS branch,
              ROUND(SUM(ssm.subtotal_ex_vat), 2)   AS revenue_ex_vat,
              ROUND(SUM({_bom_cogs_expr()}), 2)    AS cogs,
              ROUND(SUM({_bom_costed_rev_expr()}), 2)  AS costed_revenue_ex_vat,
              SUM(ssm.qty) AS qty
            FROM v_sales_sku_monthly ssm
            JOIN branches b ON b.branch_id = ssm.branch_id
            JOIN skus     sk ON sk.sku_id  = ssm.sku_id
            {where}
            GROUP BY b.branch_id
            ORDER BY revenue_ex_vat DESC
        """, params).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        rev = d["revenue_ex_vat"] or 0
        gp = rev - (d["cogs"] or 0)   # BoM COGS, margin over total revenue
        d["gross_profit"] = round(gp, 2)
        d["margin_pct"]   = round(gp / rev * 100, 2) if rev > 0 else None
        out.append(d)
    return out


# -----------------------------------------------------------------------------
# GP by Branch x Month - BoM rule (Easy):
#   COGS = SUM(qty*unit_cost) EXCLUDING tier SKUs (BUFFET350/500/590/799/CHILD)
#   Revenue = SUM(subtotal_ex_vat)
# -----------------------------------------------------------------------------
@router.get("/api/gp/by-branch-month")
@ttl_cache
def gp_by_branch_month(
    month_from: Optional[str] = Query(None),
    month_to:   Optional[str] = Query(None),
    month:      Optional[str] = Query(None),
    variant:    Optional[str] = Query(None, description="A | B | all — filter à la carte vs buffet SKUs"),
    group:      Optional[str] = Query(None),
    granularity: str          = Query("month", description="day | week | month — bucket size for per-branch trend"),
):
    """GP per branch x month using the BoM rule (Easy data).

    `granularity` ∈ {day, week, month}: when day/week, each branch row also
    gains a `trend` array of pro-rated buckets (monthly COGS pro-rated by
    daily Easy-tx revenue, same approach as /api/buffet/gp)."""
    if granularity not in ("day", "week", "month"):
        granularity = "month"
    filters = ["b.name != 'ALL'"]
    params: dict = {}
    _group_filter(filters, params, group)
    _month_range_filter(filters, params, month_from, month_to, month, col="ssm.period_month")
    var_clause = _variant_filter(variant)
    if var_clause:
        filters.append(var_clause)
    where = "WHERE " + " AND ".join(filters)

    # COGS from the BoM RECIPE model (real ingredient cost), consistent with the
    # Profit tab, Branch P&L, and BoM Cost tab. Was _cogs_case (sku_cost), which
    # gave inflated GP; switched to BoM 2026-06-01 so GP รายสาขา matches reality.
    cogs_expr = f"SUM({_bom_cogs_expr('ssm')})"

    with get_db() as con:
        rows = con.execute(f"""
            SELECT b.branch_id, b.name AS branch,
                   ssm.period_month AS month,
                   ssm.source       AS source,
                   ROUND(SUM(ssm.subtotal_ex_vat), 2) AS revenue,
                   ROUND({cogs_expr}, 2)              AS cogs
              FROM v_sales_sku_monthly ssm
              JOIN branches b ON b.branch_id = ssm.branch_id
              JOIN skus     sk ON sk.sku_id  = ssm.sku_id
            {where}
            GROUP BY b.branch_id, ssm.period_month, ssm.source
            ORDER BY b.name, ssm.period_month
        """, params).fetchall()

        target = food_cost_target(con)   # ONE target (owner 2026-10-25), Settings → ค่าเริ่มต้น
        # owner 2026-10-26: a branch with no sale in the months asked for is not a row (rules.closed_branches; was an inline
        # active=1 filter that also took a closed branch's sales out of the chain total); no months = all time
        closed_ids = closed_branches(con, [month_window(month, month_from, month_to)], ids=True)

    by_branch: dict = {}
    for r in rows:
        d = dict(r)
        bid = d["branch_id"]
        b = by_branch.setdefault(bid, {
            "branch": d["branch"], "branch_id": bid, "monthly": [],
            "total_revenue": 0.0, "total_cogs": 0.0,
        })
        rev  = float(d["revenue"] or 0)
        cogs = float(d["cogs"]    or 0)
        gp   = rev - cogs
        gp_pct = (gp / rev * 100) if rev > 0 else None
        fc_pct = (cogs / rev * 100) if rev > 0 else None
        b["monthly"].append({
            "month":         d["month"],
            "source":        d["source"],
            "revenue":       round(rev, 2),
            "cogs":          round(cogs, 2),
            "gp":            round(gp, 2),
            "gp_pct":        round(gp_pct, 2) if gp_pct is not None else None,
            "food_cost_pct": round(fc_pct, 2) if fc_pct is not None else None,
        })
        b["total_revenue"] += rev
        b["total_cogs"]    += cogs

    out_rows = []
    grand_rev = grand_cogs = 0.0
    for b in by_branch.values():
        tr = b["total_revenue"]; tc = b["total_cogs"]; tg = tr - tc
        b["total_revenue"]       = round(tr, 2)
        b["total_cogs"]          = round(tc, 2)
        b["total_gp"]            = round(tg, 2)
        b["total_gp_pct"]        = round(tg / tr * 100, 2) if tr > 0 else None
        b["total_food_cost_pct"] = round(tc / tr * 100, 2) if tr > 0 else None
        grand_rev  += tr
        grand_cogs += tc
        out_rows.append(b)

    out_rows.sort(key=lambda x: x["total_revenue"], reverse=True)
    out_rows = drop_closed(out_rows, closed_ids, key="branch_id")

    grand_gp = grand_rev - grand_cogs

    # ── Granularity-aware per-branch trend (day/week buckets) ────────────
    if granularity in ("day", "week") and out_rows:
        from datetime import date as _date_cls
        def _wkb(date_str: str) -> str:
            d = _date_cls.fromisoformat(date_str)
            iso = d.isocalendar()
            return f"{iso[0]}-W{iso[1]:02d}"

        # Daily tx revenue per branch over the period
        tx_filters = ["b.name != 'ALL'", "st.source = 'Easy'"]
        tx_params: dict = {}
        _group_filter(tx_filters, tx_params, group)
        _month_range_filter(tx_filters, tx_params, month_from, month_to, month,
                            col="SUBSTR(st.sold_at,1,7)")
        tx_where = "WHERE " + " AND ".join(tx_filters)
        with get_db() as con:
            daily_rows = con.execute(f"""
                SELECT b.branch_id,
                       DATE(st.sold_at)        AS d,
                       SUBSTR(st.sold_at,1,7)  AS m,
                       SUM(COALESCE(st.food_subtotal,0)
                         + COALESCE(st.drink_subtotal,0)
                         + COALESCE(st.other_subtotal,0)) AS rev_tx
                  FROM sales_transaction st
                  JOIN branches b ON b.branch_id = st.branch_id
                {tx_where}
                GROUP BY b.branch_id, DATE(st.sold_at)
            """, tx_params).fetchall()

        # Index: monthly tx rev per (branch, month)
        m_tx_rev: dict = {}
        for r in daily_rows:
            key = (r["branch_id"], r["m"])
            m_tx_rev[key] = m_tx_rev.get(key, 0.0) + float(r["rev_tx"] or 0)

        # Per branch: bucket → {rev, cogs}
        for b in out_rows:
            bid = b["branch_id"]
            monthly_map = {m["month"]: m for m in b["monthly"]}
            buckets: dict = {}
            for r in daily_rows:
                if r["branch_id"] != bid:
                    continue
                mo = r["m"]
                mrow = monthly_map.get(mo)
                if not mrow:
                    continue
                m_total = m_tx_rev.get((bid, mo), 0.0)
                if m_total <= 0:
                    continue
                frac = float(r["rev_tx"] or 0) / m_total
                d_cogs = float(mrow["cogs"] or 0)    * frac
                d_rev  = float(mrow["revenue"] or 0) * frac
                k = r["d"] if granularity == "day" else _wkb(r["d"])
                bb = buckets.setdefault(k, {"rev": 0.0, "cogs": 0.0})
                bb["rev"]  += d_rev
                bb["cogs"] += d_cogs
            trend = []
            for k in sorted(buckets.keys()):
                v   = buckets[k]
                gpv = v["rev"] - v["cogs"]
                trend.append({
                    "bucket":        k,
                    "revenue":       round(v["rev"],  2),
                    "cogs":          round(v["cogs"], 2),
                    "gp":            round(gpv, 2),
                    "gp_pct":        round(gpv / v["rev"] * 100, 2) if v["rev"] > 0 else None,
                    "food_cost_pct": round(v["cogs"] / v["rev"] * 100, 2) if v["rev"] > 0 else None,
                    "estimate": True,   # PR-PP3: pro-rated from the month by the day's bill revenue (ประมาณการ)
                })
            b["trend"] = trend

    return {
        "rows": out_rows,
        "granularity": granularity,
        "food_cost_target": target,
        "grand_total": {
            "revenue":       round(grand_rev, 2),
            "cogs":          round(grand_cogs, 2),
            "gp":            round(grand_gp, 2),
            "gp_pct":        round(grand_gp / grand_rev * 100, 2) if grand_rev > 0 else None,
            "food_cost_pct": round(grand_cogs / grand_rev * 100, 2) if grand_rev > 0 else None,
        },
    }


@router.get("/api/profit/per-bill-distribution")
@ttl_cache
def profit_per_bill_distribution(
    branch:     Optional[str] = Query(None),
    group:      Optional[str] = Query(None),
    month_from: Optional[str] = Query(None),
    month_to:   Optional[str] = Query(None),
    month:      Optional[str] = Query(None),
):
    """Approximate distribution of profit per bill.
    Since transactions don't link to SKUs, we apply the period's overall
    costed-margin to each transaction's food+drink+other subtotal."""
    # NOTE: do NOT call profit_summary() here — it's @ttl_cache-wrapped and returns
    # a Response (JSON bytes), not a dict, so subscripting it 500s. Compute the
    # costed-margin directly from the same source.
    _ps_where, _ps_params = _profit_filters(branch, group, month_from, month_to, month)
    with get_db() as con:
        _ps = con.execute(f"""
            SELECT ROUND(SUM({_bom_cogs_expr()}), 2)         AS cogs,
                   ROUND(SUM(ssm.subtotal_ex_vat), 2)        AS revenue
            FROM v_sales_sku_monthly ssm
            JOIN branches b ON b.branch_id = ssm.branch_id
            JOIN skus     sk ON sk.sku_id  = ssm.sku_id
            {_ps_where}
        """, _ps_params).fetchone()
    _rev   = (_ps["revenue"] or 0) if _ps else 0
    _cogs  = (_ps["cogs"] or 0) if _ps else 0
    margin_pct  = round((_rev - _cogs) / _rev * 100, 2) if _rev > 0 else 0  # BoM, over total rev

    if margin_pct == 0:
        return {
            "buckets": [], "total_tx": 0, "avg_profit": 0,
            "min_profit": 0, "max_profit": 0, "margin_pct_used": 0,
            "note": "No costed SKUs in period. Upload SKU costs to enable.",
        }

    tx_where, tx_params = _tx_filters(branch, group, month_from, month_to, month)
    m_frac = margin_pct / 100.0
    with get_db() as con:
        r = con.execute(f"""
            WITH bills AS (
              SELECT (COALESCE(st.food_subtotal,0)
                    + COALESCE(st.drink_subtotal,0)
                    + COALESCE(st.other_subtotal,0)) * {m_frac} AS est_profit
              FROM sales_transaction st
              JOIN branches b ON b.branch_id = st.branch_id
              {tx_where}
            )
            SELECT
              SUM(CASE WHEN est_profit <= 0    THEN 1 ELSE 0 END) AS b_neg,
              SUM(CASE WHEN est_profit > 0    AND est_profit <= 100  THEN 1 ELSE 0 END) AS b_0_100,
              SUM(CASE WHEN est_profit > 100  AND est_profit <= 300  THEN 1 ELSE 0 END) AS b_100_300,
              SUM(CASE WHEN est_profit > 300  AND est_profit <= 500  THEN 1 ELSE 0 END) AS b_300_500,
              SUM(CASE WHEN est_profit > 500  AND est_profit <= 1000 THEN 1 ELSE 0 END) AS b_500_1000,
              SUM(CASE WHEN est_profit > 1000 THEN 1 ELSE 0 END) AS b_1000_plus,
              COUNT(*) AS total,
              ROUND(AVG(est_profit), 2) AS avg_profit,
              ROUND(MIN(est_profit), 2) AS min_profit,
              ROUND(MAX(est_profit), 2) AS max_profit
            FROM bills
        """, tx_params).fetchone()

    return {
        "buckets": [
            {"label": "<=0",      "count": r["b_neg"]        or 0},
            {"label": "1-100",    "count": r["b_0_100"]      or 0},
            {"label": "101-300",  "count": r["b_100_300"]    or 0},
            {"label": "301-500",  "count": r["b_300_500"]    or 0},
            {"label": "501-1000", "count": r["b_500_1000"]   or 0},
            {"label": "1000+",    "count": r["b_1000_plus"]  or 0},
        ],
        "total_tx":         r["total"]      or 0,
        "avg_profit":       r["avg_profit"] or 0,
        "min_profit":       r["min_profit"] or 0,
        "max_profit":       r["max_profit"] or 0,
        "margin_pct_used":  margin_pct,
    }


# ============================================================================
# DEEP P&L / PER-BILL ANALYSIS
# ============================================================================

def _pnl_sku_query(where: str, params: dict) -> str:
    return f"""
        SELECT ssm.period_month,
          ROUND(SUM(ssm.subtotal_ex_vat), 2)  AS revenue,
          ROUND(SUM(ssm.subtotal_ex_vat), 2)  AS revenue_ex_vat,
          ROUND(SUM(ssm.total_inc_vat), 2)    AS pos_total_inc_vat_diagnostic,
          ROUND(SUM({_cogs_case()}), 2)        AS cogs,
          ROUND(SUM({_costed_rev_case()}), 2)  AS costed_rev_ex
        FROM v_sales_sku_monthly ssm
        JOIN branches b ON b.branch_id = ssm.branch_id
        JOIN skus     sk ON sk.sku_id  = ssm.sku_id
        LEFT JOIN sku_cost sc ON sc.sku_id = ssm.sku_id
        {where}
        GROUP BY ssm.period_month
        ORDER BY ssm.period_month
    """


def _add_gp_fields(rows_out: list) -> list:
    """In-place: compute gross_profit, margin_pct, mom_pct.

    GP uses the FULL ex-VAT revenue base (revenue_ex_vat) so GP == the row's
    displayed revenue - COGS and reconciles across every P&L / Profit / BoM tab
    (2026-07 base-unification; previously used costed_rev_ex, which understated
    GP vs the Profit tab by the uncosted-revenue share). costed_rev_ex is kept
    on the row purely as a cost-coverage diagnostic.
    """
    prev_gp = None
    for d in rows_out:
        rev = (d.get("revenue_ex_vat") if d.get("revenue_ex_vat") is not None
               else d.get("revenue")) or 0
        gp = rev - (d.get("cogs") or 0)
        d["gross_profit"] = round(gp, 2)
        d["margin_pct"] = round(gp / rev * 100, 2) if rev > 0 else None
        d["mom_pct"] = round((gp - prev_gp) / prev_gp * 100, 1) if (prev_gp and prev_gp > 0) else None
        prev_gp = gp if gp else prev_gp
    return rows_out


def _to_date_mom(rows_out: list, branch=None, group=None) -> list:
    """PR-PP3 · the one period pattern on the P&L series: every row says how its mom_pct was taken (`mom_basis`
    'full' = a finished month against the whole month before). The month still in progress (the month of the last closed
    day, not at its end) is set against the same days of last month instead - GP to date from the day-level dish cost
    (period_cost) - 'to_date', with `period` (the windows) and `gp_ly_pct` (the same days a year back). Without a cost
    source for a base the % is None ("—") and `reasons` says why. It was a part month's GP against a whole month's."""
    import periods as _P
    import period_cost as _PC
    for d in rows_out:
        d["mom_basis"] = "full"
    if not rows_out:
        return rows_out
    f = ["t.source='Easy'", "b.name!='ALL'"]; prm: dict = {}
    if branch:
        f.append("b.name=:branch"); prm["branch"] = branch
    if group:
        f.append("COALESCE(b.branch_type,'master') = :branch_group"); prm["branch_group"] = group
    with get_db() as con:
        w = _P.windows(con, "month", " AND ".join(f), prm)
        if not w or w["complete"]:
            return rows_out
        last = next((d for d in rows_out if d.get("period_month") == w["cur"][0][:7]), None)
        if not last:
            return rows_out
        c = _PC.compare(con, w, branch, group)
    cur, prev, ly = c.get("cur"), c.get("prev"), c.get("ly")
    last.update({"mom_basis": "to_date", "period": c.get("period"), "reasons": {k: v for k, v in c["reasons"].items() if v},
                 "gp_to_date": cur["gp"] if cur else None,
                 "mom_pct": _P.pct(cur["gp"], prev["gp"]) if (cur and prev) else None,
                 "gp_ly_pct": _P.pct(cur["gp"], ly["gp"]) if (cur and ly) else None,
                 "margin_prev_pp": c["change"]["prev"]["gp_pct"], "margin_ly_pp": c["change"]["ly"]["gp_pct"]})
    return rows_out


@router.get("/api/pnl/monthly")
@ttl_cache
def pnl_monthly(
    branch:     Optional[str] = Query(None),
    group:      Optional[str] = Query(None),
    month_from: Optional[str] = Query(None),
    month_to:   Optional[str] = Query(None),
):
    """Monthly P&L trend: revenue, COGS, GP, margin%, tx_count, profit_per_bill."""
    where, params       = _profit_filters(branch, group, month_from, month_to, None)
    tx_where, tx_params = _tx_filters(branch, group, month_from, month_to, None)

    with get_db() as con:
        sku_rows = con.execute(_pnl_sku_query(where, params), params).fetchall()
        tx_rows  = con.execute(f"""
            SELECT strftime('%Y-%m', st.sold_at) AS period_month,
                   COUNT(*) AS tx_count,
                   SUM(st.customer_count) AS customer_count,
                   ROUND({avg_ticket_sql('st')}, 2) AS avg_ticket
            FROM sales_transaction st
            JOIN branches b ON b.branch_id = st.branch_id
            {tx_where}
            GROUP BY period_month
        """, tx_params).fetchall()

    tx_map = {r["period_month"]: dict(r) for r in tx_rows}
    out = _add_gp_fields([dict(r) for r in sku_rows])
    _to_date_mom(out, branch, group)
    for d in out:
        tx = tx_map.get(d["period_month"], {})
        tx_count = tx.get("tx_count") or 0
        d["tx_count"]       = tx_count
        d["customer_count"] = tx.get("customer_count") or 0
        d["avg_ticket"]     = tx.get("avg_ticket") or 0
        gp = d["gross_profit"] or 0
        d["profit_per_bill"] = round(gp / tx_count, 2) if tx_count > 0 else None
    return out


@router.get("/api/pnl/branches")
@ttl_cache
def pnl_branches_deep(
    group:      Optional[str] = Query(None),
    month_from: Optional[str] = Query(None),
    month_to:   Optional[str] = Query(None),
):
    """All-branch P&L comparison with per-bill metrics."""
    where, params = _profit_filters(None, group, month_from, month_to, None)

    tx_filters = ["b.name != 'ALL'", "st.source = 'Easy'", "COALESCE(b.active,1) = 1"]
    tx_params: dict = {}
    if group:
        tx_filters.append("COALESCE(b.branch_type,'master') = :group")
        tx_params["group"] = group
    _month_range_filter(tx_filters, tx_params, month_from, month_to, None,
                        col="strftime('%Y-%m', st.sold_at)")
    tx_where = "WHERE " + " AND ".join(tx_filters)

    with get_db() as con:
        sku_rows = con.execute(f"""
            SELECT b.name AS branch,
              ROUND(SUM(ssm.subtotal_ex_vat), 2)   AS revenue,
              ROUND(SUM(ssm.subtotal_ex_vat), 2)   AS revenue_ex_vat,
              ROUND(SUM(ssm.total_inc_vat), 2)     AS pos_total_inc_vat_diagnostic,
              ROUND(SUM({_cogs_case()}), 2)        AS cogs,
              ROUND(SUM({_costed_rev_case()}), 2)  AS costed_rev_ex,
              COUNT(DISTINCT CASE WHEN sc.unit_cost IS NOT NULL THEN ssm.sku_id END) AS costed_skus,
              COUNT(DISTINCT ssm.sku_id) AS total_skus
            FROM v_sales_sku_monthly ssm
            JOIN branches b ON b.branch_id = ssm.branch_id
            JOIN skus     sk ON sk.sku_id  = ssm.sku_id
            LEFT JOIN sku_cost sc ON sc.sku_id = ssm.sku_id
            {where}
            GROUP BY b.name
            ORDER BY SUM(ssm.subtotal_ex_vat) DESC
        """, params).fetchall()

        tx_rows = con.execute(f"""
            SELECT b.name AS branch,
                   COUNT(*) AS tx_count,
                   SUM(st.customer_count) AS customer_count,
                   ROUND({avg_ticket_sql('st')}, 2) AS avg_ticket
            FROM sales_transaction st
            JOIN branches b ON b.branch_id = st.branch_id
            {tx_where}
            GROUP BY b.name
        """, tx_params).fetchall()
        closed = closed_branches(con, [month_window(None, month_from, month_to)])   # owner 2026-10-26

    tx_map = {r["branch"]: dict(r) for r in tx_rows}
    out = []
    sku_rows = drop_closed(sku_rows, closed)
    for r in sku_rows:
        d = dict(r)
        rev = (d.get("revenue_ex_vat") or 0)
        gp = rev - (d.get("cogs") or 0)
        d["gross_profit"] = round(gp, 2)
        d["margin_pct"] = round(gp / rev * 100, 2) if rev > 0 else None
        tx = tx_map.get(d["branch"], {})
        tx_count = tx.get("tx_count") or 0
        d["tx_count"]   = tx_count
        d["avg_ticket"] = tx.get("avg_ticket") or 0
        d["profit_per_bill"] = round(gp / tx_count, 2) if tx_count > 0 else None
        out.append(d)

    ranked = sorted(out, key=lambda x: -(x["gross_profit"] or 0))
    for i, r in enumerate(ranked):
        r["gp_rank"] = i + 1
    return out


@router.get("/api/pnl/branch-detail")
@ttl_cache
def pnl_branch_detail(
    branch:     str            = Query(...),
    month_from: Optional[str] = Query(None),
    month_to:   Optional[str] = Query(None),
):
    """Deep dive for one branch: monthly P&L, top 30 SKUs by GP, bill revenue stats."""
    sku_filters = ["b.name != 'ALL'", "COALESCE(b.active,1) = 1", "b.name = :branch"]
    sku_params: dict = {"branch": branch}
    _month_range_filter(sku_filters, sku_params, month_from, month_to, None, col="ssm.period_month")
    sku_where = "WHERE " + " AND ".join(sku_filters)

    tx_filters = ["b.name = :branch", "st.source = 'Easy'"]
    tx_params: dict = {"branch": branch}
    _month_range_filter(tx_filters, tx_params, month_from, month_to, None,
                        col="strftime('%Y-%m', st.sold_at)")
    tx_where = "WHERE " + " AND ".join(tx_filters)

    with get_db() as con:
        monthly_rows = con.execute(_pnl_sku_query(sku_where, sku_params), sku_params).fetchall()

        # Top 60 dishes by BoM COST (real recipe cost), with each dish's share of
        # the branch's total COGS — so you can see what drives the branch's cost.
        top_skus = con.execute(f"""
            SELECT sk.sku_code,
                   COALESCE(sk.description, sk.sku_code) AS description,
                   c.name AS category,
                   ROUND(SUM(ssm.qty), 0)                AS qty,
                   ROUND(SUM(ssm.subtotal_ex_vat), 2)    AS revenue,
                   ROUND(SUM(ssm.subtotal_ex_vat), 2)    AS revenue_ex_vat,
                   sc.unit_cost,
                   ROUND(SUM({_bom_cogs_expr()}), 2)        AS cogs,
                   ROUND(SUM(ssm.subtotal_ex_vat) - SUM({_bom_cogs_expr()}), 2) AS gp,
                   CASE WHEN SUM(ssm.subtotal_ex_vat) > 0
                        THEN ROUND(100.0*(SUM(ssm.subtotal_ex_vat) - SUM({_bom_cogs_expr()}))
                                   / SUM(ssm.subtotal_ex_vat), 1) ELSE NULL END AS margin_pct
            FROM v_sales_sku_monthly ssm
            JOIN branches b   ON b.branch_id   = ssm.branch_id
            JOIN skus sk      ON sk.sku_id      = ssm.sku_id
            JOIN categories c ON c.category_id  = sk.category_id
            LEFT JOIN sku_cost sc ON sc.sku_id  = ssm.sku_id
            {sku_where}
            GROUP BY sk.sku_id
            HAVING SUM(ssm.qty) > 0 AND SUM({_bom_cogs_expr()}) > 0
            ORDER BY SUM({_bom_cogs_expr()}) DESC
            LIMIT 60
        """, sku_params).fetchall()

        bill_vals = con.execute(f"""
            SELECT ROUND(st.total_inc_vat, 2) AS val
            FROM sales_transaction st
            JOIN branches b ON b.branch_id = st.branch_id
            {tx_where}
            ORDER BY val
        """, tx_params).fetchall()

        tx_monthly = con.execute(f"""
            SELECT strftime('%Y-%m', st.sold_at) AS period_month,
                   COUNT(*) AS tx_count,
                   ROUND(AVG{net_revenue_row('st')}, 2) AS avg_ticket,
                   SUM(st.customer_count) AS customer_count
            FROM sales_transaction st
            JOIN branches b ON b.branch_id = st.branch_id
            {tx_where}
            GROUP BY period_month
        """, tx_params).fetchall()

    # Build monthly with GP and TX data
    monthly_out = _add_gp_fields([dict(r) for r in monthly_rows])
    tx_mo_map = {r["period_month"]: dict(r) for r in tx_monthly}
    for d in monthly_out:
        tx = tx_mo_map.get(d["period_month"], {})
        tx_count = tx.get("tx_count") or 0
        d["tx_count"]       = tx_count
        d["avg_ticket"]     = tx.get("avg_ticket") or 0
        d["customer_count"] = tx.get("customer_count") or 0
        gp = d["gross_profit"] or 0
        d["profit_per_bill"] = round(gp / tx_count, 2) if tx_count > 0 else None

    # Bill revenue distribution and percentiles
    vals = sorted([r["val"] for r in bill_vals if r["val"] is not None])
    n = len(vals)

    def pct_val(p):
        if n == 0: return 0
        idx = max(0, min(n - 1, int(n * p)))
        return round(vals[idx], 2)

    BUCKETS = [(0, 100), (100, 200), (200, 300), (300, 500), (500, 750), (750, 1000), (1000, 1500), (1500, None)]
    bill_buckets = []
    for lo, hi in BUCKETS:
        count = sum(1 for v in vals if v >= lo and (hi is None or v < hi))
        label = f"{lo}-{hi}" if hi else f"{lo}+"
        bill_buckets.append({"label": label, "count": count})

    # Compute blended margin from monthly data
    total_rev        = sum((d.get("revenue_ex_vat") or 0) for d in monthly_out)
    total_cogs       = sum((d.get("cogs") or 0) for d in monthly_out)
    total_gp         = total_rev - total_cogs
    margin_frac      = (total_gp / total_rev) if total_rev > 0 else 0

    profit_buckets = []
    PBUCKETS = [(None, 0), (0, 50), (50, 100), (100, 200), (200, 400), (400, 700), (700, None)]
    if margin_frac > 0 and n > 0:
        profit_vals = sorted([v * margin_frac for v in vals])
        for lo, hi in PBUCKETS:
            count = sum(1 for v in profit_vals if
                        (lo is None or v >= lo) and (hi is None or v < hi))
            label = "<0" if lo is None else (f"{lo}+" if hi is None else f"{lo}-{hi}")
            profit_buckets.append({"label": label, "count": count})

    bill_stats = {
        "tx_count":         n,
        "avg":              round(sum(vals) / n, 2) if n else 0,
        "min":              vals[0] if n else 0,
        "max":              vals[-1] if n else 0,
        "p10":              pct_val(0.1),
        "p25":              pct_val(0.25),
        "p50":              pct_val(0.5),
        "p75":              pct_val(0.75),
        "p90":              pct_val(0.9),
        "buckets":          bill_buckets,
        "profit_buckets":   profit_buckets,
        "margin_pct_used":  round(margin_frac * 100, 1),
        "avg_profit":       round(sum(vals) * margin_frac / n, 2) if n else 0,
    }

    # Each dish's share of the branch's TOTAL BoM cost (across ALL dishes, not
    # just the top 60) so the % is honest.
    with get_db() as con:
        _tc = con.execute(f"""
            SELECT ROUND(SUM({_bom_cogs_expr()}), 2) AS total_cogs
            FROM v_sales_sku_monthly ssm
            JOIN branches b ON b.branch_id = ssm.branch_id
            JOIN skus sk    ON sk.sku_id   = ssm.sku_id
            {sku_where}
        """, sku_params).fetchone()

        # Per-branch INGREDIENT consumption (which raw materials drive this
        # branch's cost) + per-branch CATEGORY cost mix — the "go deeper" asks.
        ing_rows = con.execute(f"""
            SELECT bi.ingredient_name AS name, bi.ingredient_type AS type,
                   SUM(ssm.qty * bi.ingredient_cost) AS cost
            FROM v_sales_sku_monthly ssm
            JOIN branches b ON b.branch_id = ssm.branch_id
            JOIN skus sk    ON sk.sku_id   = ssm.sku_id
            JOIN bom_ingredients bi ON bi.sku_id = ssm.sku_id
            {sku_where}
            GROUP BY bi.ingredient_name
        """, sku_params).fetchall()

        cat_rows = con.execute(f"""
            SELECT c.name AS category,
                   ROUND(SUM(ssm.subtotal_ex_vat), 2) AS revenue,
                   ROUND(SUM({_bom_cogs_expr()}), 2)  AS cogs
            FROM v_sales_sku_monthly ssm
            JOIN branches b   ON b.branch_id   = ssm.branch_id
            JOIN skus sk      ON sk.sku_id      = ssm.sku_id
            JOIN categories c ON c.category_id  = sk.category_id
            {sku_where}
            GROUP BY c.name
        """, sku_params).fetchall()

    branch_total_cogs = (_tc["total_cogs"] or 0) if _tc else 0
    top_list = [dict(r) for r in top_skus]
    for d in top_list:
        d["cost_pct"] = round((d["cogs"] or 0) / branch_total_cogs * 100, 1) if branch_total_cogs > 0 else 0

    # Group ingredients into families (salmon variants → Salmon), top 15 by cost.
    fam: dict = {}
    for r in ing_rows:
        label = _ingredient_family(r["name"])
        fam[label] = fam.get(label, 0.0) + (r["cost"] or 0)
    ing_total = sum(fam.values()) or 1
    top_ingredients = [
        {"name": k, "cost": round(v, 2), "pct": round(v / ing_total * 100, 1)}
        for k, v in sorted(fam.items(), key=lambda x: -x[1])
    ][:15]

    # Category cost mix (top by cost), each as % of branch COGS.
    cat_mix = []
    for r in sorted(cat_rows, key=lambda x: -(x["cogs"] or 0)):
        cogs_v = r["cogs"] or 0
        if cogs_v <= 0:
            continue
        cat_mix.append({
            "category":  r["category"],
            "revenue":   r["revenue"] or 0,
            "cogs":      cogs_v,
            "cost_pct":  round(cogs_v / branch_total_cogs * 100, 1) if branch_total_cogs > 0 else 0,
        })

    return {
        "branch":   branch,
        "monthly":  monthly_out,
        "top_skus": top_list,
        "branch_total_cogs": branch_total_cogs,
        "top_ingredients":   top_ingredients,
        "category_mix":      cat_mix,
        "bill_stats": bill_stats,
    }


# ---------------------------------------------------------------------------
# GET /api/pnl/branch-month-detail  — drill a branch+month from the P&L table
#   • P&L summary for that month (rev / COGS / GP)
#   • BoM cost broken down by dish (each dish's % of the month's COGS)
#   • top ingredients (families) driving cost
#   • MoM: which dishes rose/fell most in SERVINGS vs the previous month
# ---------------------------------------------------------------------------
@router.get("/api/pnl/branch-month-detail")
@ttl_cache
def pnl_branch_month_detail(
    branch: str = Query(...),
    month:  str = Query(..., description="YYYY-MM"),
    limit:  int = Query(40, ge=5, le=200),
):
    prev = _month_add(month, -1)
    cogs_x = _unified_cogs_expr("ssm")
    # PR-PP3 · the one period pattern: the month in progress is to date through the branch's last closed day and is set
    # against the same days of last month (and of the month a year back) from the day-level dish table - it was set
    # against the WHOLE last month. A finished month is whole vs whole from the month table, as before.
    import periods as _P
    import period_cost as _PC
    with get_db() as con:
        _w = _P.windows(con, "month", "t.source='Easy' AND b.name=:branch", {"branch": branch}, pick=f"{month[:7]}-01")
        _floor = _PC.cost_floor(con)
    if _w and _w["cur"][0][:7] != month[:7]:
        _w = None                                    # the month asked for is not the anchor's period: keep the month table

    def _dishes_src(s_: str, e_: str):
        """the month table for a whole month, the day table for a part month (None without a source)"""
        pl, _why = _PC.plan(s_, e_, _floor)
        if pl is None:
            return None, {}
        if pl["src"] == "month":
            return ("v_sales_sku_monthly ssm", "ssm.period_month IN (" + ",".join(f"'{m}'" for m in pl["months"]) + ")"), {}
        return (("(SELECT branch_id, sku_id, qty, subtotal_ex_vat, 'Easy' AS source, substr(day, 1, 7) AS period_month "
                 "FROM v_sales_sku_daily WHERE day BETWEEN :_s AND :_e) ssm"), "1=1"), {"_s": s_, "_e": e_}

    def _dishes_win(s_: str, e_: str) -> Optional[dict]:
        src, extra = _dishes_src(s_, e_)
        if src is None:
            return None
        with get_db() as con:
            rows = con.execute(f"""
                SELECT sk.sku_code,
                       COALESCE(sk.description, sk.sku_code) AS description,
                       c.name AS category,
                       SUM(ssm.qty)             AS servings,
                       SUM({cogs_x})            AS cogs,
                       SUM(ssm.subtotal_ex_vat) AS revenue
                FROM {src[0]}
                JOIN branches b   ON b.branch_id   = ssm.branch_id
                JOIN skus sk      ON sk.sku_id      = ssm.sku_id
                JOIN categories c ON c.category_id  = sk.category_id
                WHERE b.name = :branch AND {src[1]}
                GROUP BY sk.sku_id
                HAVING SUM(ssm.qty) > 0
            """, {"branch": branch, **extra}).fetchall()
        return {r["sku_code"]: dict(r) for r in rows}

    def _dishes(mon: str) -> dict:
        import calendar as _cal
        y, m = int(mon[:4]), int(mon[5:7])
        return _dishes_win(f"{mon[:7]}-01", f"{mon[:7]}-{_cal.monthrange(y, m)[1]:02d}") or {}

    reasons: dict = {}
    if _w and not _w["complete"]:
        cur = _dishes_win(*_w["cur"]) or {}
        pre = _dishes_win(*_w["prev"]) if _w["has_prev"] else None
        if pre is None:
            reasons["prev"] = _P.reasons(_w).get("prev") or _PC.floor_reason(_floor)
            pre = {}
        _ly = _dishes_win(*_w["ly"]) if _w["has_ly"] else None
        if _ly is None:
            reasons["ly"] = _P.reasons(_w).get("ly") or _PC.floor_reason(_floor)
    else:
        cur = _dishes(month)
        pre = _dishes(prev)
        _ly = _dishes(_month_add(month, -12)) or None

    tot_cogs = sum((d["cogs"] or 0) for d in cur.values())
    tot_rev  = sum((d["revenue"] or 0) for d in cur.values())

    # BoM cost breakdown by dish (this month), each as % of month COGS
    cost_breakdown = sorted(
        ({"sku_code": k, "description": d["description"], "category": d["category"],
          "servings": round(d["servings"] or 0, 0), "cogs": round(d["cogs"] or 0, 2),
          "cost_pct": round((d["cogs"] or 0) / tot_cogs * 100, 1) if tot_cogs > 0 else 0}
         for k, d in cur.items()),
        key=lambda x: -x["cogs"]
    )[:limit]

    # Ingredient families this month
    fam: dict = {}
    with get_db() as con:
        ing = con.execute("""
            SELECT bi.ingredient_name AS name, SUM(ssm.qty*bi.ingredient_cost) AS cost
            FROM v_sales_sku_monthly ssm
            JOIN branches b ON b.branch_id = ssm.branch_id
            JOIN bom_ingredients bi ON bi.sku_id = ssm.sku_id
            WHERE b.name = :branch AND ssm.period_month = :m
            GROUP BY bi.ingredient_name
        """, {"branch": branch, "m": month}).fetchall()
    for r in ing:
        lbl = _ingredient_family(r["name"]); fam[lbl] = fam.get(lbl, 0.0) + (r["cost"] or 0)
    ing_tot = sum(fam.values()) or 1
    top_ingredients = [{"name": k, "cost": round(v, 2), "pct": round(v/ing_tot*100, 1)}
                       for k, v in sorted(fam.items(), key=lambda x: -x[1])][:12]

    # MoM rising/falling dishes by servings
    movers = []
    for code in set(cur) | set(pre):
        now = (cur.get(code, {}).get("servings") or 0)
        was = (pre.get(code, {}).get("servings") or 0)
        if now == was:
            continue
        meta = cur.get(code) or pre.get(code)
        delta = now - was
        pct = round(delta / was * 100, 1) if was > 0 else None
        movers.append({"sku_code": code, "description": meta["description"],
                       "now": round(now, 0), "was": round(was, 0), "delta": round(delta, 0),
                       "delta_pct": pct, "is_new": was == 0 and now > 0})
    movers.sort(key=lambda m: abs(m["delta"]), reverse=True)
    rising   = sorted([m for m in movers if m["delta"] > 0], key=lambda m: -m["delta"])[:10]
    falling  = sorted([m for m in movers if m["delta"] < 0], key=lambda m: m["delta"])[:10]

    prev_cogs = sum((d["cogs"] or 0) for d in pre.values())
    prev_rev  = sum((d["revenue"] or 0) for d in pre.values())
    ly_cogs = sum((d["cogs"] or 0) for d in (_ly or {}).values()) if _ly else None
    ly_rev = sum((d["revenue"] or 0) for d in (_ly or {}).values()) if _ly else None
    _gm = lambda r_, c_: round((r_ - c_) / r_ * 100, 1) if r_ else None   # noqa: E731
    _per = None
    if _w and not _w["complete"]:
        _per = _P.public(_w)
        if reasons:
            _per["reasons"] = reasons
    return {
        "branch": branch, "month": month, "prev_month": prev, "period": _per,
        "to_date": bool(_w and not _w["complete"]),
        "summary": {
            "revenue": round(tot_rev, 2), "cogs": round(tot_cogs, 2),
            "gross_profit": round(tot_rev - tot_cogs, 2),
            "gp_margin_pct": round((tot_rev - tot_cogs)/tot_rev*100, 1) if tot_rev > 0 else None,
            "food_cost_pct": round(tot_cogs/tot_rev*100, 1) if tot_rev > 0 else None,
            "prev_revenue": round(prev_rev, 2), "prev_cogs": round(prev_cogs, 2),
            "rev_delta_pct": round((tot_rev-prev_rev)/prev_rev*100, 1) if prev_rev > 0 else None,
            "ly_revenue": round(ly_rev, 2) if ly_rev else None,
            "rev_ly_delta_pct": round((tot_rev-ly_rev)/ly_rev*100, 1) if ly_rev else None,
            "gp_margin_prev_pp": (round(_gm(tot_rev, tot_cogs) - _gm(prev_rev, prev_cogs), 1)
                                  if tot_rev and prev_rev else None),
            "gp_margin_ly_pp": (round(_gm(tot_rev, tot_cogs) - _gm(ly_rev, ly_cogs), 1) if tot_rev and ly_rev else None),
            "dish_count": len(cur),
        },
        "cost_breakdown": cost_breakdown,
        "top_ingredients": top_ingredients,
        "rising_dishes": rising,
        "falling_dishes": falling,
    }




@router.get("/api/finance/prime-cost")
def finance_prime_cost(month: Optional[str] = Query(None), group: Optional[str] = Query(None)):
    """Prime cost = food% + labor% per branch — the two biggest CONTROLLABLE costs.
    Food COGS is real (unified BoM); labor + fixed come from Settings inputs. Shows
    nothing for labor until entered (food% is always real)."""
    cogs_expr = _unified_cogs_expr("sm")
    filt = ["b.name!='ALL'"]; p: dict = {}
    if group: _group_filter(filt, p, group)
    tw = " AND ".join(filt)
    with get_db() as con:
        mon = month or con.execute(
            "SELECT MAX(period_month) FROM sales_sku_monthly WHERE source='Easy'").fetchone()[0]
        rows = {r["branch"]: dict(r) for r in con.execute(f"""
            SELECT b.name branch, ROUND(SUM(sm.subtotal_ex_vat)) rev, ROUND(SUM({cogs_expr})) food_cogs
            FROM v_sales_sku_monthly sm JOIN branches b ON b.branch_id=sm.branch_id
            WHERE {tw} AND sm.period_month=:m GROUP BY b.name""", {**p, "m": mon}).fetchall()}
        def _load(k):
            r = con.execute("SELECT value FROM app_settings WHERE key=:k", {"k": k}).fetchone()
            if r:
                try: return _json.loads(r["value"])
                except Exception: return {}
            return {}
        labor = _load('branch_labor_costs'); fixed = _load('branch_fixed_costs')
    out = []
    for nm, r in rows.items():
        rev = r["rev"] or 0; fc = r["food_cogs"] or 0
        lb = labor.get(nm, 0) or 0; fx = fixed.get(nm, 0) or 0
        has_lb = lb > 0
        op = rev - fc - lb - fx
        out.append({
            "branch": nm, "rev": rev, "food_cogs": fc,
            "food_pct": round(fc / rev * 100, 1) if rev else None,
            "labor": lb, "labor_pct": round(lb / rev * 100, 1) if (rev and lb) else None,
            "prime_pct": round((fc + lb) / rev * 100, 1) if (rev and lb) else None,
            "fixed": fx, "fixed_pct": round(fx / rev * 100, 1) if (rev and fx) else None,
            "operating": round(op) if (lb or fx) else None,
            "operating_pct": round(op / rev * 100, 1) if (rev and (lb or fx)) else None,
            "has_labor": has_lb,
        })
    out.sort(key=lambda r: -(r["prime_pct"] if r["prime_pct"] is not None else (r["food_pct"] or -1)))
    trev = sum(r["rev"] for r in out); tfood = sum(r["food_cogs"] for r in out)
    tlabor = sum(r["labor"] for r in out); tfixed = sum(r["fixed"] for r in out)
    chain = {"rev": trev, "food_cogs": tfood, "labor": tlabor, "fixed": tfixed,
             "food_pct": round(tfood / trev * 100, 1) if trev else None,
             "labor_pct": round(tlabor / trev * 100, 1) if (trev and tlabor) else None,
             "prime_pct": round((tfood + tlabor) / trev * 100, 1) if (trev and tlabor) else None,
             "branches_with_labor": sum(1 for r in out if r["has_labor"]), "branch_count": len(out)}
    # AYCE buffet prime-cost band (food ~50-55% + labor ~15-22%): good <70, watch 70-78, high >78
    return {"month": mon, "branches": out, "chain": chain, "band": {"good": 70, "high": 78}}


# ---------------------------------------------------------------------------
# GET /api/pnl/branch   — full branch P&L waterfall + break-even (Analysis #1)
# ---------------------------------------------------------------------------
@router.get("/api/pnl/branch")
@ttl_cache
def pnl_branch(
    branch:     Optional[str] = Query(None),
    month:      Optional[str] = Query(None),
    month_from: Optional[str] = Query(None),
    month_to:   Optional[str] = Query(None),
):
    """Per-branch P&L: Revenue (ex-VAT) -> COGS (canonical _cogs_case) -> Gross
    Profit -> minus monthly fixed costs (app_settings) -> Operating Profit, plus
    break-even covers and margin of safety. Includes a chain roll-up."""
    filters = ["b.name != 'ALL'"]
    params: dict = {}
    if branch:
        filters.append("b.name = :branch"); params["branch"] = branch
    _month_range_filter(filters, params, month_from, month_to, month, col="ssm.period_month")
    where = "WHERE " + " AND ".join(filters)

    tf = ["b.name != 'ALL'", "st.source='Easy'", "st.customer_count > 0"]
    tp: dict = {}
    if branch:
        tf.append("b.name = :branch"); tp["branch"] = branch
    _month_range_filter(tf, tp, month_from, month_to, month, col="SUBSTR(st.sold_at,1,7)")
    twhere = "WHERE " + " AND ".join(tf)

    with get_db() as con:
        rev_rows = con.execute(f"""
            SELECT b.name AS branch,
                   COUNT(DISTINCT ssm.period_month) AS months,
                   SUM(ssm.subtotal_ex_vat)         AS revenue
            FROM v_sales_sku_monthly ssm
            JOIN branches b ON b.branch_id = ssm.branch_id
            {where}
            GROUP BY b.name
        """, params).fetchall()
        # COGS via the UNIFIED expression (BoM recipe first, sku_cost fallback,
        # tier rows = 0) so this matches every other tab exactly (2026-06-02).
        bom_rows = con.execute(f"""
            SELECT b.name AS branch, SUM({_unified_cogs_expr('ssm')}) AS bom_cost
            FROM v_sales_sku_monthly ssm
            JOIN branches b ON b.branch_id = ssm.branch_id
            {where}
            GROUP BY b.name
        """, params).fetchall()
        cov = con.execute(f"""
            SELECT b.name AS branch, SUM(st.customer_count) AS covers
            FROM sales_transaction st
            JOIN branches b ON b.branch_id = st.branch_id
            {twhere}
            GROUP BY b.name
        """, tp).fetchall()
        fc_row = con.execute(
            "SELECT value FROM app_settings WHERE key='branch_fixed_costs'"
        ).fetchone()
        # PR-PP3: the fixed cost of a month still in progress is pro-rated by its days through the last closed day (it was
        # a whole month's fixed cost against a part month's gross profit)
        import periods as _P
        _anchor = _P.anchor(con)
        _bm: dict = {}
        for _r in con.execute(f"""SELECT b.name AS branch, ssm.period_month AS m FROM v_sales_sku_monthly ssm
                                  JOIN branches b ON b.branch_id = ssm.branch_id {where}
                                  GROUP BY b.name, ssm.period_month""", params).fetchall():
            _bm.setdefault(_r["branch"], []).append(_r["m"])
        closed = closed_branches(con, [month_window(month, month_from, month_to)], keep=branch)   # owner 2026-10-26

    def _month_share(m: str) -> float:
        """the share of month `m` that has closed: 1 for a finished month, days to the anchor ÷ days in the month else"""
        import calendar as _cal
        if not _anchor or _anchor[:7] != m[:7]:
            return 1.0
        n = _cal.monthrange(int(m[:4]), int(m[5:7]))[1]
        return min(1.0, int(_anchor[8:10]) / n)

    fixed = {}
    if fc_row:
        try:
            fixed = _json.loads(fc_row["value"])
        except Exception:
            pass
    covmap = {r["branch"]: (r["covers"] or 0) for r in cov}
    bom_map = {r["branch"]: (r["bom_cost"] or 0.0) for r in bom_rows}

    out = []
    tot = {"revenue": 0.0, "cogs": 0.0, "fixed": 0.0, "covers": 0}
    for r in sorted(rev_rows, key=lambda x: -(x["revenue"] or 0)):
        br      = r["branch"]
        rev     = round(r["revenue"] or 0.0, 2)
        cogs_v  = round(bom_map.get(br, 0.0), 2)
        gp      = round(rev - cogs_v, 2)
        months  = r["months"] or 1
        fcm     = float(fixed.get(br, 0) or 0)
        months_eff = sum(_month_share(m) for m in _bm.get(br, [])) or months
        fcp     = round(fcm * months_eff, 2)
        op      = round(gp - fcp, 2)
        covers  = covmap.get(br, 0)
        gp_per_cover = (gp / covers) if covers else None
        breakeven_covers = round(fcp / gp_per_cover) if (gp_per_cover and gp_per_cover > 0) else None
        mos_pct = round((covers - breakeven_covers) / covers * 100, 1) if (breakeven_covers and covers) else None
        out.append({
            "branch":            br,
            "months":            months,
            "months_prorated":   round(months_eff, 3),   # PR-PP3: the month in progress counts its closed days only
            "revenue":           rev,
            "cogs":              cogs_v,
            "gross_profit":      gp,
            "gp_margin_pct":     round(gp / rev * 100, 1) if rev else None,
            "fixed_cost_period": fcp,
            "fixed_cost_monthly": round(fcm, 2),
            "operating_profit":  op,
            "op_margin_pct":     round(op / rev * 100, 1) if rev else None,
            "covers":            covers,
            "op_per_cover":      round(op / covers, 2) if covers else None,
            "gp_per_cover":      round(gp_per_cover, 2) if gp_per_cover is not None else None,
            "breakeven_covers":  breakeven_covers,
            "margin_of_safety_pct": mos_pct,
        })
        tot["revenue"] += rev; tot["cogs"] += cogs_v; tot["fixed"] += fcp; tot["covers"] += covers

    chain_gp = round(tot["revenue"] - tot["cogs"], 2)
    chain_op = round(chain_gp - tot["fixed"], 2)
    chain = {
        "revenue":          round(tot["revenue"], 2),
        "cogs":             round(tot["cogs"], 2),
        "gross_profit":     chain_gp,
        "gp_margin_pct":    round(chain_gp / tot["revenue"] * 100, 1) if tot["revenue"] else None,
        "fixed_cost_period": round(tot["fixed"], 2),
        "operating_profit": chain_op,
        "op_margin_pct":    round(chain_op / tot["revenue"] * 100, 1) if tot["revenue"] else None,
        "covers":           tot["covers"],
        "fixed_costs_set":  bool(fixed),
    }
    return {"branches": drop_closed(out, closed), "chain": chain}     # the chain keeps a closed branch's sales of the window
