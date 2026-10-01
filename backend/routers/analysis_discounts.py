"""Discounts & leakage — the /api/analysis/discount* family: reasons, coupon series (+ drill),
cashiers, the Discounts tab, discount bills, composition, leakage, payment-method discounts,
discount alerts. Split out of routers/analysis.py on 2026-09-05, code moved verbatim; the
helpers they share with the rest of the analysis domain still come from core / rules."""
from fastapi import Query
from typing import Optional
import json as _json
from rules import (
    EASY_TIER_CODES_SQL as _EASY_TIER_CODES_SQL,
    discount_base_row,
    discount_excess,
    discount_policy_pct,
    net_revenue_row,
    net_revenue_sum,
    scope_filter,
)
from core import (
    _apply_period_filter,
    _build_sku_effective_cost,
    _eff_month_range,
    _group_filter,
    _month_range_filter,
    get_db,
    ttl_cache,
)

from fastapi import APIRouter
router = APIRouter()


@router.get("/api/analysis/discount-reasons")
def discount_reasons(month_from: Optional[str] = Query(None), month_to: Optional[str] = Query(None),
                     month: Optional[str] = Query(None), branch: Optional[str] = Query(None),
                     group: Optional[str] = Query(None)):
    """Discount breakdown by REASON/TYPE (Sale.DiscountReasonId → DiscountSaleReason),
    over a month range. reason_id 0 = 'ไม่ระบุ' (cashier discounted without picking a
    reason) — usually the biggest leak. Staged by sync into pos_discount_month +
    pos_discount_reason; returns empty/staged=False on a DB synced before they existed."""
    base = ["b.name!='ALL'"]; p: dict = {}
    scope_filter(base, p, branch, group)
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
    scope_filter(base, p, branch, group)
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
        scope_filter(bbase, bp, branch, group)
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
            scope_filter(base, p, branch, group)
            _month_range_filter(base, p, month_from, month_to, month, col="substr(cd.day,1,7)")
            rows = [dict(r) for r in con.execute(
                f"SELECT cd.day label, SUM(cd.used_count) uses, ROUND(SUM(cd.discount_amount)) amount "
                f"FROM pos_coupon_day cd JOIN branches b ON b.branch_id=cd.branch_id "
                f"WHERE {' AND '.join(base)} GROUP BY cd.day ORDER BY cd.day", p)]
            return {"by": "day", "series_id": series_id, "name": name, "rows": rows, "available": True}
        # by branch
        base = ["b.name!='ALL'", "cm.coupon_series_id=:sid"]; p = {"sid": series_id}
        scope_filter(base, p, branch, group)
        _month_range_filter(base, p, month_from, month_to, month, col="cm.period_month")
        rows = [dict(r) for r in con.execute(
            f"SELECT b.name label, SUM(cm.used_count) uses, ROUND(SUM(cm.discount_amount)) amount "
            f"FROM pos_coupon_month cm JOIN branches b ON b.branch_id=cm.branch_id "
            f"WHERE {' AND '.join(base)} GROUP BY b.name ORDER BY amount DESC", p)]
        return {"by": "branch", "series_id": series_id, "name": name, "rows": rows, "available": True}


# ---------------------------------------------------------------------------
# /api/analysis/cashiers   -  cashier leaderboard
# ---------------------------------------------------------------------------
@router.get("/api/analysis/cashiers")
@ttl_cache   # 1.0 s cold - was recomputed on every request
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
                   ROUND({net_revenue_sum('st')}, 2) AS rev
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
                   ROUND({net_revenue_sum('st')}, 2)                  AS revenue,
                   ROUND(AVG{net_revenue_row('st')}, 2)                  AS avg_ticket,
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
@ttl_cache   # 1.6 s cold; PUT /api/settings already invalidates "analysis_discounts"
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
                   ROUND(SUM(({net_revenue_row('')}) + discount), 2) AS gross_sales,
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
                   ROUND(SUM({discount_base_row('')}), 2) AS gross_base,
                   ROUND(100.0 * SUM(discount)
                         / NULLIF(SUM({discount_base_row('')}),0), 1) AS disc_pct_of_sales
            FROM sales_transaction st
            JOIN branches b ON b.branch_id = st.branch_id
            {where}
            GROUP BY b.name
            ORDER BY total_discount DESC
            """,
            params,
        ).fetchall()
        # the policy ceiling and what each branch gave beyond it (฿, never negative)
        policy = discount_policy_pct(con)
        branches = []
        for r in by_branch:
            row = dict(r)
            row["excess_baht"] = discount_excess(row.get("total_discount") or 0, row.get("gross_base") or 0, policy)
            row["over_policy"] = (row.get("disc_pct_of_sales") or 0) > policy
            branches.append(row)

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
                         / NULLIF(SUM(({net_revenue_row('')}) + discount),0), 1) AS disc_pct_of_sales,
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
                     (({net_revenue_row('')}) + discount) AS gross
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
        ov["policy_pct"] = policy
        ov["excess_baht"] = round(sum(b["excess_baht"] for b in branches), 2)
        ov["branches_over_policy"] = sum(1 for b in branches if b["over_policy"])
    return {
        "overall":      ov,
        "by_branch":    branches,
        "policy_pct":   policy,
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
    base_expr = discount_base_row('st')
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
    gross = discount_base_row('st')
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
    base_expr = discount_base_row('st')
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
        headline = (f"{len(abnormal)} สาขาใช้ส่วนลดสูงผิดปกติ (เกิน {threshold:.2f}% ของยอดก่อนลด · "
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
    base = discount_base_row('st')
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
    base = discount_base_row('st')
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
        except Exception as e:
            print(f"[discount-alerts] stored thresholds unreadable, defaults used: {e}")
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
