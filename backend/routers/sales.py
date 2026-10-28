"""Sales domain router — /api/sales/*, /api/overall/*, /api/summary, moved
VERBATIM out of main.py (P1 structure refactor, step 3). No logic changes;
URLs unchanged. Same host-module pattern as routers/reports.py.
"""
# Shared infrastructure and helpers come from core.py by name — the old
# `globals().update(getattr(main, …))` bootstrap is gone (Phase 2, 2026-09).
from fastapi import Query, Request
from typing import Optional
from rules import VAT_DIVISOR, closed_branches, closed_day_clause, drop_closed, hidden_amount, effective_unit_cost_sql, net_revenue_row, needs_cost_sql, net_revenue_sum, non_food_sql, scope_filter, tier_code_sql
from core import (
    _FOOD_CATEGORY_FILTER, _cogs_case, _effective_cost_subquery, _group_filter, _iso_week_sql,
    _month_range_filter, _partial_months, _txn_fallback_counts, _unified_cogs_expr, _ym_parts,
    get_db, ttl_cache,
)

import threading as _threading

from fastapi import APIRouter
router = APIRouter()




@router.get("/api/sales/monthly")
@ttl_cache
def sales_monthly(
    branch:      Optional[str] = Query(None),
    group:       Optional[str] = Query(None),
    source:      Optional[str] = Query(None),
    month:       Optional[str] = Query(None),
    month_from:  Optional[str] = Query(None),
    month_to:    Optional[str] = Query(None),
    date_from:   Optional[str] = Query(None),   # YYYY-MM-DD  for daily/weekly
    date_to:     Optional[str] = Query(None),   # YYYY-MM-DD  for daily/weekly
    granularity: str           = Query("monthly"),  # daily|weekly|monthly|quarterly
):
    """
    Revenue trend at the requested granularity.
    daily/weekly  â†' sales_transaction  (date_from / date_to)
    monthly       â†' sales_sku_monthly  (month_from / month_to)
    quarterly     â†' sales_sku_monthly aggregated to quarters
    """
    # â"€â"€ daily â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€
    if granularity == "daily":
        filters = ["b.name != 'ALL'", "st.source = 'Easy'"]
        params: dict = {}
        if branch:
            filters.append("b.name = :branch")
            params["branch"] = branch
        if date_from:
            filters.append("DATE(st.sold_at) >= :date_from")
            params["date_from"] = date_from
        if date_to:
            filters.append("DATE(st.sold_at) <= :date_to")
            params["date_to"] = date_to
        where = "WHERE " + " AND ".join(filters)
        with get_db() as con:
            rows = con.execute(
                f"""
                SELECT DATE(st.sold_at)              AS period_month,
                       ROUND({net_revenue_sum('st')},2) AS revenue,
                       COUNT(*)                       AS tx_count,
                       SUM(st.customer_count)         AS customer_count
                FROM sales_transaction st
                JOIN branches b ON b.branch_id = st.branch_id
                {where}
                GROUP BY DATE(st.sold_at)
                ORDER BY period_month
                """,
                params,
            ).fetchall()
        return [{"period_month": r["period_month"], "revenue": r["revenue"],
                 "tx_count": r["tx_count"], "customer_count": r["customer_count"],
                 "source": "Easy"} for r in rows]

    # â"€â"€ weekly â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€
    if granularity == "weekly":
        filters = ["b.name != 'ALL'", "st.source = 'Easy'"]
        params = {}
        if branch:
            filters.append("b.name = :branch")
            params["branch"] = branch
        if date_from:
            filters.append("DATE(st.sold_at) >= :date_from")
            params["date_from"] = date_from
        if date_to:
            filters.append("DATE(st.sold_at) <= :date_to")
            params["date_to"] = date_to
        where = "WHERE " + " AND ".join(filters)
        _wk = _iso_week_sql('st.sold_at')
        with get_db() as con:
            rows = con.execute(
                f"""
                SELECT {_wk}                            AS period_month,
                       MIN(DATE(st.sold_at))            AS week_start,
                       ROUND({net_revenue_sum('st')}, 2)  AS revenue,
                       COUNT(*)                         AS tx_count,
                       SUM(st.customer_count)           AS customer_count
                FROM sales_transaction st
                JOIN branches b ON b.branch_id = st.branch_id
                {where}
                GROUP BY {_wk}
                ORDER BY period_month
                """,
                params,
            ).fetchall()
        return [{"period_month": r["period_month"], "week_start": r["week_start"],
                 "revenue": r["revenue"], "tx_count": r["tx_count"],
                 "customer_count": r["customer_count"], "source": "Easy"} for r in rows]

    # â"€â"€ quarterly â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€
    if granularity == "quarterly":
        filters = ["b.name != 'ALL'"]
        params = {}
        if branch:
            filters.append("b.name = :branch")
            params["branch"] = branch
        _month_range_filter(filters, params, month_from, month_to, month, col="sm.period_month")
        where = "WHERE " + " AND ".join(filters)
        with get_db() as con:
            rows = con.execute(
                f"""
                SELECT qtr AS period_month,
                       ROUND(SUM(rev_ex_vat), 2)   AS revenue,
                       ROUND(SUM(rev_ex_vat), 2)   AS revenue_ex_vat,
                       ROUND(SUM(rev_inc_vat), 2)  AS pos_total_inc_vat_diagnostic
                FROM (
                    SELECT
                        substr(sm.period_month,1,4) || '-Q' ||
                        ((CAST(substr(sm.period_month,6,2) AS INTEGER)-1)/3+1) AS qtr,
                        sm.total_inc_vat   AS rev_inc_vat,
                        sm.subtotal_ex_vat AS rev_ex_vat
                    FROM v_sales_sku_monthly sm
                    JOIN branches b ON b.branch_id = sm.branch_id
                    {where}
                ) sub
                GROUP BY qtr
                ORDER BY qtr
                """,
                params,
            ).fetchall()
        return [{"period_month": r["period_month"], "revenue": r["revenue"],
                 "revenue_ex_vat": r["revenue_ex_vat"], "source": "Easy"} for r in rows]

    # -- yearly (YoY comparison: returns (year, month_num) rows, NO month range filter) --
    if granularity == "yearly":
        filters_y = ["b.name != 'ALL'"]
        params_y: dict = {}
        if branch:
            filters_y.append("b.name = :branch")
            params_y["branch"] = branch
        _group_filter(filters_y, params_y, group)
        where_y = "WHERE " + " AND ".join(filters_y)
        with get_db() as con:
            rows = con.execute(
                f"""
                SELECT sm.period_month                       AS period_month,
                       substr(sm.period_month, 1, 4)          AS year,
                       substr(sm.period_month, 6, 2)          AS month_num,
                       ROUND(SUM(sm.subtotal_ex_vat), 2)      AS revenue,
                       ROUND(SUM(sm.subtotal_ex_vat), 2)      AS revenue_ex_vat,
                       ROUND(SUM(sm.total_inc_vat), 2)        AS pos_total_inc_vat_diagnostic
                FROM v_sales_sku_monthly sm
                JOIN branches b ON b.branch_id = sm.branch_id
                {where_y}
                GROUP BY sm.period_month
                ORDER BY sm.period_month
                """,
                params_y,
            ).fetchall()
            # Tx + customer counts from the pre-aggregated month summary.
            # SUM(transaction_count)/SUM(customer_count) per period_month is
            # identical to COUNT(*)/SUM(customer_count) over raw sales_transaction
            # (verified across every branch + filter), but reads ~26 rows/month
            # instead of sorting 570k tx rows in a temp b-tree (~320ms -> <1ms).
            tx_rows_y = con.execute(
                f"""
                SELECT sm.period_month            AS period_month,
                       SUM(sm.transaction_count)  AS tx_count,
                       SUM(sm.customer_count)     AS customer_count
                FROM sales_month_summary sm
                JOIN branches b ON b.branch_id = sm.branch_id
                {where_y}
                GROUP BY sm.period_month
                """,
                params_y,
            ).fetchall()
            tx_map_y = {r["period_month"]: dict(r) for r in tx_rows_y}
            missing_y = [r["period_month"] for r in rows
                         if not (tx_map_y.get(r["period_month"], {}).get("tx_count"))]
            tx_map_y.update(_txn_fallback_counts(con, missing_y, branch, group, None))
        out = []
        for r in rows:
            tx = tx_map_y.get(r["period_month"], {})
            out.append({
                "period_month":   r["period_month"],
                "year":           r["year"],
                "month_num":      r["month_num"],
                "revenue":        r["revenue"],
                "revenue_ex_vat": r["revenue_ex_vat"],
                "tx_count":       tx.get("tx_count", 0),
                "customer_count": tx.get("customer_count", 0),
                "source":         "Easy",
            })
        return out

    # -- monthly (default) --
    with get_db() as con:
        filters_sku: list = ["b.name != 'ALL'"]
        params_sku: dict = {}
        if branch:
            filters_sku.append("b.name = :branch")
            params_sku["branch"] = branch
        _group_filter(filters_sku, params_sku, group)
        if source:
            filters_sku.append("sm.source = :source")
            params_sku["source"] = source
        _month_range_filter(filters_sku, params_sku, month_from, month_to, month, col="sm.period_month")
        where_sku = "WHERE " + " AND ".join(filters_sku)

        # Revenue from Easy SKU data
        sku_rows = con.execute(
            f"""
            SELECT sm.period_month,
                   ROUND(SUM(sm.subtotal_ex_vat), 2)  AS revenue,
                   ROUND(SUM(sm.subtotal_ex_vat), 2)  AS revenue_ex_vat,
                   ROUND(SUM(sm.total_inc_vat), 2)    AS pos_total_inc_vat_diagnostic
            FROM v_sales_sku_monthly sm
            JOIN branches b ON b.branch_id = sm.branch_id
            {where_sku}
            GROUP BY sm.period_month
            ORDER BY sm.period_month
            """,
            params_sku,
        ).fetchall()
        # Transaction counts from sales_transaction (Easy data)
        filters_tx: list = ["b.name != 'ALL'"]
        params_tx: dict = {}
        if branch:
            filters_tx.append("b.name = :branch")
            params_tx["branch"] = branch
        _group_filter(filters_tx, params_tx, group)
        if source:
            filters_tx.append("sm.source = :source")
            params_tx["source"] = source
        _month_range_filter(filters_tx, params_tx, month_from, month_to, month, col="sm.period_month")
        where_tx = "WHERE " + " AND ".join(filters_tx)

        # Counts from the pre-aggregated month summary — identical result to
        # COUNT(*)/SUM(customer_count) over raw sales_transaction (verified per
        # branch + filter), without the 570k-row temp-btree sort (~320ms -> <1ms).
        tx_rows = con.execute(
            f"""
            SELECT sm.period_month            AS period_month,
                   SUM(sm.transaction_count)  AS tx_count,
                   SUM(sm.customer_count)     AS customer_count
            FROM sales_month_summary sm
            JOIN branches b ON b.branch_id = sm.branch_id
            {where_tx}
            GROUP BY sm.period_month
            ORDER BY period_month
            """,
            params_tx,
        ).fetchall()

        tx_map = {r["period_month"]: dict(r) for r in tx_rows}
        missing = [mo for mo in {r["period_month"] for r in sku_rows}
                   if not (tx_map.get(mo, {}).get("tx_count"))]
        tx_map.update(_txn_fallback_counts(con, missing, branch, group, source))

    all_months = sorted(r["period_month"] for r in sku_rows)
    sku_map = {r["period_month"]: dict(r) for r in sku_rows}

    result = []
    for mo in all_months:
        sku = sku_map.get(mo, {})
        tx  = tx_map.get(mo, {})
        total_tx   = tx.get("tx_count")       or 0
        total_cust = tx.get("customer_count") or 0
        result.append({
            "period_month":   mo,
            "revenue":        sku.get("revenue"),
            "revenue_ex_vat": sku.get("revenue_ex_vat"),
            "tx_count":       total_tx if total_tx else None,
            "customer_count": total_cust if total_cust else None,
            "source":         "Easy",
        })

    return result


# ---------------------------------------------------------------------------
# /api/sales/category   -  revenue by category for a month/range
# ---------------------------------------------------------------------------
@router.get("/api/sales/category")
def sales_by_category(
    month:      Optional[str] = Query(None, description="YYYY-MM; omit for all-time"),
    month_from: Optional[str] = Query(None),
    month_to:   Optional[str] = Query(None),
    branch:     Optional[str] = Query(None),
    group:      Optional[str] = Query(None),
    source:     Optional[str] = Query(None),
):
    """Revenue breakdown by category."""
    filters = ["b.name != 'ALL'"]
    params: dict = {}

    if branch:
        filters.append("b.name = :branch")
        params["branch"] = branch
    _group_filter(filters, params, group)
    _month_range_filter(filters, params, month_from, month_to, month, col="sm.period_month")
    if source:
        filters.append("sm.source = :source")
        params["source"] = source

    where = "WHERE " + " AND ".join(filters)

    with get_db() as con:
        rows = con.execute(
            f"""
            SELECT c.name AS category,
                   ROUND(SUM(sm.subtotal_ex_vat), 2) AS revenue,
                   ROUND(SUM(sm.qty), 2)            AS qty
            FROM v_sales_sku_monthly sm
            JOIN branches b    ON b.branch_id    = sm.branch_id
            JOIN skus sk       ON sk.sku_id       = sm.sku_id
            JOIN categories c  ON c.category_id   = sk.category_id
            {where}
            GROUP BY c.name
            ORDER BY revenue DESC
            """,
            params,
        ).fetchall()

    return [{"category": r["category"], "revenue": r["revenue"], "qty": r["qty"]} for r in rows]


# ---------------------------------------------------------------------------
# /api/sales/skus   -  top SKUs
# ---------------------------------------------------------------------------
@router.get("/api/sales/skus")
def top_skus(
    month:      Optional[str] = Query(None),
    month_from: Optional[str] = Query(None),
    month_to:   Optional[str] = Query(None),
    branch:     Optional[str] = Query(None),
    group:      Optional[str] = Query(None),
    source:     Optional[str] = Query(None),
    category:   Optional[str] = Query(None),
    sort:       str           = Query("revenue", description="'revenue' or 'qty'"),
    limit:      int           = Query(20, ge=1, le=200),
):
    """Top SKUs by revenue/qty, with cost and margin if sku_cost is populated.

    FOOD-ONLY menu ranking (2026-06-01): classified by CATEGORY (robust, vs the
    earlier fragile A#/B# code-prefix rule). Excludes drinks/alcohol, non-product
    lines (buffet tier, payment, delivery, service, promotion, voucher, discount,
    deposit, take-away, catering, condiment), dessert/ice-cream, and per-branch
    'Special <branch>' menus — leaving the core Oshinei food dishes. See
    _FOOD_CATEGORY_FILTER.
    """
    filters = ["b.name != 'ALL'", "COALESCE(b.active,1) = 1", "COALESCE(sk.active,1) = 1"]
    params: dict = {}

    filters.append(_FOOD_CATEGORY_FILTER)   # real food dishes only

    if branch:
        filters.append("b.name = :branch")
        params["branch"] = branch
    _group_filter(filters, params, group)
    _month_range_filter(filters, params, month_from, month_to, month, col="sm.period_month")
    if source:
        filters.append("sm.source = :source")
        params["source"] = source
    if category:
        filters.append("c.name = :category")
        params["category"] = category

    where = "WHERE " + " AND ".join(filters)
    params["limit"] = limit

    with get_db() as con:
        rows = con.execute(
            f"""
            SELECT sk.sku_code,
                   sk.description,
                   c.name                            AS category,
                   sk.unit,
                   ROUND(SUM(sm.qty), 2)             AS qty,
                   ROUND(SUM(sm.subtotal_ex_vat), 2)   AS revenue,
                   ROUND(SUM(sm.subtotal_ex_vat), 2) AS revenue_ex_vat,
                   SUM({_cogs_case(ssm='sm', sk='sk', sc='sc')}) AS cogs_total,
                   SUM(CASE WHEN {_effective_cost_subquery(ssm='sm', sc='sc')} IS NOT NULL
                            THEN sm.qty ELSE 0 END)              AS costed_qty
            FROM v_sales_sku_monthly sm
            JOIN branches b    ON b.branch_id   = sm.branch_id
            JOIN skus sk       ON sk.sku_id      = sm.sku_id
            JOIN categories c  ON c.category_id  = sk.category_id
            LEFT JOIN sku_cost sc ON sc.sku_id   = sm.sku_id
            {where}
            GROUP BY sk.sku_id
            ORDER BY {"qty" if sort == "qty" else "revenue"} DESC
            LIMIT :limit
            """,
            params,
        ).fetchall()

    result = []
    for r in rows:
        cogs_total = r["cogs_total"]
        costed_qty = r["costed_qty"] or 0
        qty       = r["qty"] or 0.0
        rev_ex    = r["revenue_ex_vat"] or 0.0
        has_cost  = cogs_total is not None and costed_qty > 0
        cost      = (cogs_total / costed_qty) if has_cost else None
        cogs      = round(cogs_total, 2) if has_cost else None
        margin    = round(rev_ex - cogs, 2) if cogs is not None else None
        margin_pct = round(margin / rev_ex * 100, 1) if (margin is not None and rev_ex) else None
        result.append({
            "sku_code":    r["sku_code"],
            "description": r["description"],
            "category":    r["category"],
            "unit":        r["unit"],
            "qty":         qty,
            "revenue":     r["revenue"],
            "unit_cost":   cost,
            "cogs":        cogs,
            "margin":      margin,
            "margin_pct":  margin_pct,
        })
    return result


def _mtd_window(con, branch: Optional[str] = None, group: Optional[str] = None):
    """Equal-period (MTD) comparison window when the latest month is incomplete.
    Returns None when the latest month is complete (use normal monthly MoM).
    STANDING RULE: never compare a partial month against a full month.
    The windows come from periods.resolve('month', ...) - the one period pattern.
    PR-PP2: the latest day is the last CLOSED one (the day still trading never enters a to-date window) of the
    branch / group asked for (a branch whose data stops earlier is cut on its own last day). Adds the year-ago window."""
    from periods import resolve
    f, p = ["1=1"], {}
    if branch or group:
        f, p = [], {}
        scope_filter(f, p, branch, group)
    row = con.execute(f"SELECT MAX(d.day) m FROM sales_sku_daily d JOIN branches b ON b.branch_id = d.branch_id "
                      f"WHERE {' AND '.join(f)} AND {closed_day_clause('d.day')}", p).fetchone() if (branch or group) else \
        con.execute(f"SELECT MAX(day) m FROM sales_sku_daily WHERE {closed_day_clause('day')}").fetchone()
    dmax = row["m"] if row else None
    if not dmax:
        return None
    r = resolve("month", dmax)
    if r["complete"]:
        return None
    (cs, ce), (ps, pe), (ys, ye) = r["cur"], r["prev"], r["ly"]
    return {"cutoff_day": r["days"], "latest_month": cs[:7], "prev_month": ps[:7],
            "cur_s": cs, "cur_e": ce, "prev_s": ps, "prev_e": pe, "ly_s": ys, "ly_e": ye}


@router.get("/api/sales/top_ordered")
@ttl_cache   # 1.2 s cold - was recomputed on every request
def top_ordered(
    month:      Optional[str] = Query(None),
    month_from: Optional[str] = Query(None),
    month_to:   Optional[str] = Query(None),
    branch:     Optional[str] = Query(None),
    group:      Optional[str] = Query(None),
    sort:       str           = Query("qty", description="'qty' or 'revenue'"),
    limit:      int           = Query(15, ge=1, le=100),
):
    """Most-ordered food SKUs with share-of-total quantity and month-over-month
    change. Share = SKU qty ÷ total food qty in the selected window. MoM compares
    the two most recent months present (≤ month_to) so it stays meaningful even
    when the ranking window is a single month or a wide range.

    sort = qty | revenue | cogs. A COST ranking (sort=cogs) covers everything that
    carries a cost (rules.needs_cost_sql — desserts, drinks and delivery dishes
    included), so its total is the COGS of the P&L and ต้นทุน/หัว and it ranks the
    same lines as the Top-60 cost/head page, BoM รายสาขา and the P&L drill. The
    qty/revenue rankings stay dine-in food (_FOOD_CATEGORY_FILTER). 2026-10-01."""
    order = {"qty": "qty", "cogs": "cogs", "revenue": "revenue"}.get(sort, "revenue")
    by_cost = order == "cogs"
    scope_sql = needs_cost_sql() if by_cost else _FOOD_CATEGORY_FILTER
    cat_join = "LEFT JOIN" if by_cost else "JOIN"
    base = ["b.name != 'ALL'", "COALESCE(b.active,1) = 1", "COALESCE(sk.active,1) = 1",
            scope_sql]
    params: dict = {}
    if branch:
        base.append("b.name = :branch")
        params["branch"] = branch
    _group_filter(base, params, group)
    _month_range_filter(base, params, month_from, month_to, month, col="sm.period_month")
    where = "WHERE " + " AND ".join(base)
    cogs_expr = _unified_cogs_expr("sm")   # per-row BoM COGS (qty × recipe cost; tier=0)

    with get_db() as con:
        rows = con.execute(f"""
            SELECT sk.sku_id, sk.sku_code, sk.description, c.name AS category,
                   ROUND(SUM(sm.qty), 2)             AS qty,
                   ROUND(SUM(sm.subtotal_ex_vat), 2) AS revenue,
                   ROUND(SUM({cogs_expr}), 2)        AS cogs
            FROM v_sales_sku_monthly sm
            JOIN branches b    ON b.branch_id   = sm.branch_id
            JOIN skus sk       ON sk.sku_id     = sm.sku_id
            {cat_join} categories c ON c.category_id = sk.category_id
            {where}
            GROUP BY sk.sku_id
            {"HAVING SUM(" + cogs_expr + ") > 0" if by_cost else ""}
            ORDER BY {order} DESC
            LIMIT :limit
        """, {**params, "limit": limit}).fetchall()

        total_row = con.execute(f"""
            SELECT ROUND(SUM(sm.qty), 2) AS total_qty,
                   ROUND(SUM(sm.subtotal_ex_vat), 2) AS total_rev,
                   ROUND(SUM({cogs_expr}), 2) AS total_cogs
            FROM v_sales_sku_monthly sm
            JOIN branches b    ON b.branch_id   = sm.branch_id
            JOIN skus sk       ON sk.sku_id     = sm.sku_id
            {cat_join} categories c ON c.category_id = sk.category_id
            {where}
        """, params).fetchone()

        # Total covers (heads) in the same window — for cost-per-head normalization.
        # All paying customers, not food-only: cost/head spreads a dish's ingredient
        # cost across every cover served that period.
        cov_filt = ["b.name != 'ALL'", "t.source = 'Easy'", "COALESCE(b.active,1) = 1"]
        cov_params: dict = {}
        if branch:
            cov_filt.append("b.name = :cbranch"); cov_params["cbranch"] = branch
        _group_filter(cov_filt, cov_params, group)
        _cf = month_from or month; _ct = month_to or month
        if _cf:
            cov_filt.append("strftime('%Y-%m',t.sold_at) >= :cmf"); cov_params["cmf"] = _cf
        if _ct:
            cov_filt.append("strftime('%Y-%m',t.sold_at) <= :cmt"); cov_params["cmt"] = _ct
        cov_row = con.execute(
            f"""SELECT SUM(t.customer_count) AS covers
                FROM sales_transaction t JOIN branches b ON b.branch_id = t.branch_id
                WHERE {' AND '.join(cov_filt)}""", cov_params).fetchone()
        total_covers = (cov_row["covers"] or 0) if cov_row else 0

        # MoM: two most recent months present in the (branch/group) scope, ≤ month_to
        mfilt = ["b.name != 'ALL'", _FOOD_CATEGORY_FILTER]
        mparams: dict = {}
        if branch:
            mfilt.append("b.name = :branch")
            mparams["branch"] = branch
        _group_filter(mfilt, mparams, group)
        cutoff = month_to or month
        if cutoff:
            mfilt.append("sm.period_month <= :cutoff")
            mparams["cutoff"] = cutoff
        months = con.execute(f"""
            SELECT DISTINCT sm.period_month AS m
            FROM v_sales_sku_monthly sm
            JOIN branches b    ON b.branch_id   = sm.branch_id
            JOIN skus sk       ON sk.sku_id     = sm.sku_id
            JOIN categories c  ON c.category_id = sk.category_id
            WHERE {' AND '.join(mfilt)}
            ORDER BY m DESC LIMIT 2
        """, mparams).fetchall()
        latest = months[0]["m"] if months else None
        prev   = months[1]["m"] if len(months) > 1 else None

        mom_map: dict = {}
        rev_mom_map: dict = {}
        yoy_map: dict = {}
        mom_basis = "month"
        mtd_cut = None
        ids = [r["sku_id"] for r in rows]
        if latest and ids:
            ph  = ",".join(f":id{i}" for i in range(len(ids)))
            idp = {f"id{i}": v for i, v in enumerate(ids)}
            ly_m = _month_add(latest, -12)
            mom_params = {**idp, "lm": latest, "pm": prev or "__none__", "ym": ly_m}
            branch_clause = ""
            if branch:
                branch_clause = "AND b.name = :branch"
                mom_params["branch"] = branch
            # the ranking honours group= (master|franchise); MoM must too, or a
            # franchise view compared franchise ranks against chain-wide MoM
            _gl: list = []
            _group_filter(_gl, mom_params, group)
            if _gl:
                branch_clause += " AND " + " AND ".join(_gl)
            mrows = con.execute(f"""
                SELECT sm.sku_id, sm.period_month AS m,
                       SUM(sm.qty) AS q, SUM(sm.subtotal_ex_vat) AS rev
                FROM v_sales_sku_monthly sm
                JOIN branches b ON b.branch_id = sm.branch_id
                WHERE sm.sku_id IN ({ph})
                  AND sm.period_month IN (:lm, :pm, :ym) {branch_clause}
                GROUP BY sm.sku_id, sm.period_month
            """, mom_params).fetchall()
            tmp: dict = {}; tmpR: dict = {}
            for mr in mrows:
                tmp.setdefault(mr["sku_id"], {})[mr["m"]]  = mr["q"]   or 0
                tmpR.setdefault(mr["sku_id"], {})[mr["m"]] = mr["rev"] or 0
            for sid, mm in tmp.items():
                cur = mm.get(latest, 0)
                pr  = mm.get(prev) if prev else None
                mom_map[sid] = ((cur - pr) / pr * 100) if (pr and pr > 0) else None
            for sid, mm in tmpR.items():
                cur = mm.get(latest, 0)
                pr  = mm.get(prev) if prev else None
                rev_mom_map[sid] = ((cur - pr) / pr * 100) if (pr and pr > 0) else None
            # PR-PP2: เทียบปีก่อน - the same month a year back (whole vs whole; the month in progress is redone below)
            for sid, mm in tmp.items():
                yq = mm.get(ly_m)
                yoy_map[sid] = ((mm.get(latest, 0) - yq) / yq * 100) if (yq and yq > 0) else None

        # MTD override (STANDING RULE): if the latest month is incomplete, recompute
        # MoM from sales_sku_daily over the SAME day-range of both months.
        mtd = _mtd_window(con, branch, group)       # PR-PP2: this scope's last closed day
        if mtd and latest == mtd["latest_month"] and ids:
            mom_basis = "mtd"; mtd_cut = mtd["cutoff_day"]
            phd = ",".join(f":mid{i}" for i in range(len(ids)))
            dparams = {f"mid{i}": v for i, v in enumerate(ids)}
            dparams.update({"cs": mtd["cur_s"], "ce": mtd["cur_e"], "ps": mtd["prev_s"], "pe": mtd["prev_e"],
                            "ys": mtd["ly_s"], "ye": mtd["ly_e"]})
            dwhere = [f"d.sku_id IN ({phd})", "(d.day BETWEEN :cs AND :ce OR d.day BETWEEN :ps AND :pe OR d.day BETWEEN :ys AND :ye)"]
            _dfirst = con.execute("SELECT MIN(day) FROM sales_sku_daily").fetchone()[0]
            _ly_ok = bool(_dfirst) and _dfirst <= mtd["ly_s"]          # the daily table reaches the year-ago days
            if branch:
                dwhere.append("b.name = :dbranch"); dparams["dbranch"] = branch
            gf: list = []; _group_filter(gf, dparams, group)
            dwhere += gf
            drows = con.execute(f"""
                SELECT d.sku_id,
                  SUM(CASE WHEN d.day BETWEEN :cs AND :ce THEN d.qty ELSE 0 END) cq,
                  SUM(CASE WHEN d.day BETWEEN :ps AND :pe THEN d.qty ELSE 0 END) pq,
                  SUM(CASE WHEN d.day BETWEEN :cs AND :ce THEN d.subtotal_ex_vat ELSE 0 END) cr,
                  SUM(CASE WHEN d.day BETWEEN :ps AND :pe THEN d.subtotal_ex_vat ELSE 0 END) pr,
                  SUM(CASE WHEN d.day BETWEEN :ys AND :ye THEN d.qty ELSE 0 END) yq
                FROM v_sales_sku_daily d JOIN branches b ON b.branch_id = d.branch_id
                WHERE {' AND '.join(dwhere)} GROUP BY d.sku_id""", dparams).fetchall()
            mom_map = {}; rev_mom_map = {}; yoy_map = {}
            for dr in drows:
                mom_map[dr["sku_id"]]     = ((dr["cq"] - dr["pq"]) / dr["pq"] * 100) if dr["pq"] else None
                rev_mom_map[dr["sku_id"]] = ((dr["cr"] - dr["pr"]) / dr["pr"] * 100) if dr["pr"] else None
                yoy_map[dr["sku_id"]]     = ((dr["cq"] - dr["yq"]) / dr["yq"] * 100) if (_ly_ok and dr["yq"]) else None
            latest = mtd["latest_month"]; prev = mtd["prev_month"]

        # Covers over the SAME comparison windows used for MoM (MTD-fair when the latest
        # month is partial) — lets the client show how each menu's cost-per-head changed,
        # reflecting BOTH the dish's volume change and the traffic (covers) change.
        def _covers_window(ds, de):
            cf = ["b.name != 'ALL'", "t.source = 'Easy'", "date(t.sold_at) BETWEEN :wf AND :wt"]
            cp = {"wf": ds, "wt": de}
            if branch:
                cf.append("b.name = :wbranch"); cp["wbranch"] = branch
            _group_filter(cf, cp, group)
            row = con.execute(f"SELECT SUM(t.customer_count) c FROM sales_transaction t "
                              f"JOIN branches b ON b.branch_id = t.branch_id "
                              f"WHERE {' AND '.join(cf)}", cp).fetchone()
            return (row["c"] or 0) if row else 0
        cmp_covers_cur = cmp_covers_prev = 0
        if mom_basis == "mtd" and mtd:
            cmp_covers_cur  = _covers_window(mtd["cur_s"],  mtd["cur_e"])
            cmp_covers_prev = _covers_window(mtd["prev_s"], mtd["prev_e"])
        elif latest and prev:
            cmp_covers_cur  = _covers_window(latest + "-01", latest + "-31")
            cmp_covers_prev = _covers_window(prev   + "-01", prev   + "-31")

        # Sparkline: per-SKU monthly qty series (YTD / last 12 months ≤ cutoff)
        spark_map: dict = {}
        spark_months: list = []
        if ids:
            allm = con.execute(f"""
                SELECT DISTINCT sm.period_month AS m
                FROM v_sales_sku_monthly sm
                JOIN branches b   ON b.branch_id   = sm.branch_id
                JOIN skus sk      ON sk.sku_id      = sm.sku_id
                JOIN categories c ON c.category_id  = sk.category_id
                WHERE {' AND '.join(mfilt)}
                ORDER BY m
            """, mparams).fetchall()
            spark_months = [r["m"] for r in allm][-12:]
            if spark_months:
                ph2 = ",".join(f":sid{i}" for i in range(len(ids)))
                mph = ",".join(f":spm{i}" for i in range(len(spark_months)))
                sp_params = {f"sid{i}": v for i, v in enumerate(ids)}
                sp_params.update({f"spm{i}": v for i, v in enumerate(spark_months)})
                sbranch = ""
                if branch:
                    sbranch = "AND b.name = :sbranch"; sp_params["sbranch"] = branch
                _gs: list = []
                _group_filter(_gs, sp_params, group)        # same scope as the ranking
                if _gs:
                    sbranch += " AND " + " AND ".join(_gs)
                srows = con.execute(f"""
                    SELECT sm.sku_id, sm.period_month AS m, SUM(sm.qty) AS q
                    FROM v_sales_sku_monthly sm
                    JOIN branches b ON b.branch_id = sm.branch_id
                    WHERE sm.sku_id IN ({ph2})
                      AND sm.period_month IN ({mph}) {sbranch}
                    GROUP BY sm.sku_id, sm.period_month
                """, sp_params).fetchall()
                tmpS: dict = {}
                for sr in srows:
                    tmpS.setdefault(sr["sku_id"], {})[sr["m"]] = round(sr["q"] or 0, 2)
                for sid, mm in tmpS.items():
                    spark_map[sid] = [mm.get(m, 0) for m in spark_months]

    total_qty  = (total_row["total_qty"]  or 0) if total_row else 0
    total_rev  = (total_row["total_rev"]  or 0) if total_row else 0
    total_cogs = (total_row["total_cogs"] or 0) if total_row else 0
    items = []
    for r in rows:
        q = r["qty"] or 0.0
        rev = r["revenue"] or 0.0
        cogs = r["cogs"] or 0.0
        mom = mom_map.get(r["sku_id"])
        rmom = rev_mom_map.get(r["sku_id"])
        items.append({
            "sku_code":      r["sku_code"],
            "description":   r["description"],
            "category":      r["category"],
            "qty":           q,
            "revenue":       rev,
            "cogs":          cogs,
            "qty_share_pct": round(q / total_qty * 100, 1) if total_qty else None,
            "qty_mom_pct":   round(mom, 1) if mom is not None else None,
            "qty_yoy_pct":   round(yoy_map[r["sku_id"]], 1) if yoy_map.get(r["sku_id"]) is not None else None,
            "rev_share_pct": round(rev / total_rev * 100, 1) if total_rev else None,
            "rev_mom_pct":   round(rmom, 1) if rmom is not None else None,
            "cogs_share_pct": round(cogs / total_cogs * 100, 1) if total_cogs else None,
            "spark":         spark_map.get(r["sku_id"]) or [],
        })
    return {"total_qty": total_qty, "total_rev": total_rev, "total_cogs": total_cogs,
            "total_covers": total_covers, "scope": "costed" if by_cost else "food",
            "cmp_covers_cur": cmp_covers_cur, "cmp_covers_prev": cmp_covers_prev,
            "latest_month": latest, "prev_month": prev,
            "mom_basis": mom_basis, "mtd_cutoff_day": mtd_cut,
            # PR-PP2: the compared windows (the "?" of the page)
            "cmp_range": ([mtd["cur_s"], mtd["cur_e"]] if mom_basis == "mtd" else None),
            "cmp_prev_range": ([mtd["prev_s"], mtd["prev_e"]] if mom_basis == "mtd" else None),
            "cmp_ly_range": ([mtd["ly_s"], mtd["ly_e"]] if mom_basis == "mtd" else None),
            "spark_months": spark_months, "items": items}


# Shared aggregator for the A/B-compare endpoints: groups a sales window (month
# range via sm, OR day range via sales_sku_daily) by an arbitrary GROUP BY column.
def _menucmp_agg(con, gb_select, gb_col, extra_where, p_base, mfrom, mto, dfrom, dto):
    base = list(extra_where); p = dict(p_base)
    if dfrom or dto:
        if dfrom: base.append("d.day>=:df"); p["df"] = dfrom
        if dto:   base.append("d.day<=:dt"); p["dt"] = dto
        # sales_sku_daily has no `source` column (it's Easy-only), so use a daily-specific
        # COGS expr that drops the source check but keeps buffet-tier=0.
        # The unit cost is the P&L's own (rules.effective_unit_cost_sql) looked up for the
        # month of the day sold, so from the Grand Menu cut-over a released cost reaches
        # the day-range compare as it does the month-range one; earlier months run the
        # old chain untouched.
        cogs = (f"CASE WHEN {tier_code_sql('(SELECT sk_t.sku_code FROM skus sk_t WHERE sk_t.sku_id=d.sku_id)')} "
                f"THEN 0 ELSE d.qty * {effective_unit_cost_sql('d', 'substr(d.day, 1, 7)')} END")
        sql = (f"SELECT {gb_select}, ROUND(SUM(d.qty),2) qty, ROUND(SUM(d.subtotal_ex_vat),2) rev, "
               f"ROUND(SUM({cogs}),2) cogs FROM v_sales_sku_daily d "
               f"JOIN branches b ON b.branch_id=d.branch_id JOIN skus sk ON sk.sku_id=d.sku_id "
               f"JOIN categories c ON c.category_id=sk.category_id "
               f"WHERE {' AND '.join(base)} GROUP BY {gb_col}")
    else:
        if mfrom: base.append("sm.period_month>=:mf"); p["mf"] = mfrom
        if mto:   base.append("sm.period_month<=:mt"); p["mt"] = mto
        cogs = _unified_cogs_expr("sm")
        sql = (f"SELECT {gb_select}, ROUND(SUM(sm.qty),2) qty, ROUND(SUM(sm.subtotal_ex_vat),2) rev, "
               f"ROUND(SUM({cogs}),2) cogs FROM v_sales_sku_monthly sm "
               f"JOIN branches b ON b.branch_id=sm.branch_id JOIN skus sk ON sk.sku_id=sm.sku_id "
               f"JOIN categories c ON c.category_id=sk.category_id "
               f"WHERE {' AND '.join(base)} GROUP BY {gb_col}")
    return [dict(r) for r in con.execute(sql, p).fetchall()]


# Below this min/max ratio, one side is a near-zero residual next to the other
# (e.g. a retired SKU code that keeps posting a handful of leftover transactions
# for a few days after a menu-cutover rename) — dividing by it produces a
# nonsensical, direction-flipping delta (a real collapse reads as a huge "▲
# increase") rather than a real swing. Treated the same as the pv==0 "vanished"
# case below (→ None, rendered as "—"). A relative ratio (vs. an absolute unit
# cutoff) is used because this helper compares qty, revenue and cogs alike,
# which sit at very different absolute scales.
_CMP_RESIDUAL_RATIO = 0.01


def _cmp_pct(c, pv):
    if not pv:
        return None
    if c and min(abs(c), abs(pv)) / max(abs(c), abs(pv)) < _CMP_RESIDUAL_RATIO:
        return None
    return round((c - pv) / pv * 100, 1)


def _covers_by_branch(con, mfrom, mto, dfrom, dto, group):
    """{branch: total covers} over a month range OR a day range — for per-head views."""
    base = ["b.name!='ALL'", "t.source='Easy'"]; p: dict = {}
    _group_filter(base, p, group)
    if dfrom or dto:
        if dfrom: base.append("date(t.sold_at)>=:cf"); p["cf"] = dfrom
        if dto:   base.append("date(t.sold_at)<=:ct"); p["ct"] = dto
    else:
        if mfrom: base.append("strftime('%Y-%m',t.sold_at)>=:cmf"); p["cmf"] = mfrom
        if mto:   base.append("strftime('%Y-%m',t.sold_at)<=:cmt"); p["cmt"] = mto
    rows = con.execute(f"SELECT b.name br, SUM(t.customer_count) c "
                       f"FROM sales_transaction t JOIN branches b ON b.branch_id=t.branch_id "
                       f"WHERE {' AND '.join(base)} GROUP BY b.name", p).fetchall()
    return {r["br"]: (r["c"] or 0) for r in rows}


def _covers_total(con, mfrom, mto, dfrom, dto, branch, group):
    """Total Easy covers over a month OR day range for the given branch/group
    scope — the denominator for per-head (ต่อหัว) cost on the compare table."""
    base = ["b.name!='ALL'", "t.source='Easy'"]; p: dict = {}
    if branch:
        base.append("b.name=:cbr"); p["cbr"] = branch
    _group_filter(base, p, group)
    if dfrom or dto:
        if dfrom: base.append("date(t.sold_at)>=:cf"); p["cf"] = dfrom
        if dto:   base.append("date(t.sold_at)<=:ct"); p["ct"] = dto
    else:
        if mfrom: base.append("strftime('%Y-%m',t.sold_at)>=:cmf"); p["cmf"] = mfrom
        if mto:   base.append("strftime('%Y-%m',t.sold_at)<=:cmt"); p["cmt"] = mto
    row = con.execute(f"SELECT SUM(t.customer_count) c FROM sales_transaction t "
                      f"JOIN branches b ON b.branch_id=t.branch_id "
                      f"WHERE {' AND '.join(base)}", p).fetchone()
    return row["c"] or 0


@router.get("/api/sales/menu-compare")
def menu_compare(
    a_from: Optional[str] = Query(None), a_to: Optional[str] = Query(None),
    b_from: Optional[str] = Query(None), b_to: Optional[str] = Query(None),
    a_date_from: Optional[str] = Query(None), a_date_to: Optional[str] = Query(None),
    b_date_from: Optional[str] = Query(None), b_date_to: Optional[str] = Query(None),
    branch: Optional[str] = Query(None), group: Optional[str] = Query(None),
    limit:  int           = Query(60, ge=1, le=300),
):
    """Top-N menu items compared across two arbitrary periods A and B — each a
    MONTH range (*_from/*_to, YYYY-MM) or a DAY range (*_date_from/*_date_to,
    YYYY-MM-DD). Per-SKU qty/rev/cogs for each period + deltas, ranked by
    combined volume, with a totals row over the shown rows."""
    # Buffet-only analysis: à-la-carte SKUs (description prefixed "(A)") are excluded
    # here on purpose — buffet is the core business and à-la-carte distorts the mix.
    ew = ["b.name!='ALL'", "COALESCE(sk.active,1)=1", _FOOD_CATEGORY_FILTER,
          "COALESCE(sk.description,'') NOT LIKE '(A)%'"]
    pb: dict = {}
    if branch:
        ew.append("b.name=:br"); pb["br"] = branch
    _group_filter(ew, pb, group)
    sel = "sk.sku_id sku_id, sk.sku_code sku_code, sk.description description, c.name category"
    import periods as _P
    with get_db() as con:
        # PR-PP2 guard (periods.guard): both periods end by the last closed day; a period running past it is to date
        # and the other is cut to its own first n days - never a part month against a whole one
        _g = _P.guard({x: sp for x, sp in (("a", _P.span_of(a_from, a_to, a_date_from, a_date_to)),
                                           ("b", _P.span_of(b_from, b_to, b_date_from, b_date_to))) if sp},
                      _P.anchor(con), None)
        if "a" in _g["windows"]:
            a_from = a_to = None; a_date_from, a_date_to = _g["windows"]["a"]
        if "b" in _g["windows"]:
            b_from = b_to = None; b_date_from, b_date_to = _g["windows"]["b"]
        A = {r["sku_id"]: r for r in _menucmp_agg(con, sel, "sk.sku_id", ew, pb, a_from, a_to, a_date_from, a_date_to)}
        B = {r["sku_id"]: r for r in _menucmp_agg(con, sel, "sk.sku_id", ew, pb, b_from, b_to, b_date_from, b_date_to)}
        covers_a = _covers_total(con, a_from, a_to, a_date_from, a_date_to, branch, group)
        covers_b = _covers_total(con, b_from, b_to, b_date_from, b_date_to, branch, group)
    items = []
    for sid in set(A) | set(B):
        a = A.get(sid); b = B.get(sid); meta = a or b
        qa = (a or {}).get("qty", 0) or 0; qb = (b or {}).get("qty", 0) or 0
        ra = (a or {}).get("rev", 0) or 0; rb = (b or {}).get("rev", 0) or 0
        items.append({
            "sku_id": sid, "sku_code": meta["sku_code"], "description": meta["description"],
            "category": meta.get("category"),
            "qty_a": qa, "qty_b": qb, "qty_delta_pct": _cmp_pct(qa, qb),
            "rev_a": ra, "rev_b": rb, "rev_delta_pct": _cmp_pct(ra, rb),
            "cogs_a": (a or {}).get("cogs", 0) or 0, "cogs_b": (b or {}).get("cogs", 0) or 0,
            "only_in_a": b is None, "only_in_b": a is None,
        })
    items.sort(key=lambda r: -((r["qty_a"] or 0) + (r["qty_b"] or 0)))
    items = items[:limit]
    s = lambda k: round(sum(i[k] for i in items), 2)
    totals = {"qty_a": s("qty_a"), "qty_b": s("qty_b"), "rev_a": s("rev_a"), "rev_b": s("rev_b"),
              "cogs_a": s("cogs_a"), "cogs_b": s("cogs_b"),
              "covers_a": covers_a, "covers_b": covers_b,
              "qty_delta_pct": _cmp_pct(s("qty_a"), s("qty_b")), "rev_delta_pct": _cmp_pct(s("rev_a"), s("rev_b"))}
    return {"a_range": [a_from or a_date_from, a_to or a_date_to],
            "b_range": [b_from or b_date_from, b_to or b_date_to],
            "to_date": _g["to_date"], "n_days": _g["n_days"],      # PR-PP2: the periods were cut to the same days
            "covers_a": covers_a, "covers_b": covers_b,
            "n": len(items), "items": items, "totals": totals}


@router.get("/api/sales/menu-compare/drill")
def menu_compare_drill(
    sku_code: str = Query(...),
    a_from: Optional[str] = Query(None), a_to: Optional[str] = Query(None),
    b_from: Optional[str] = Query(None), b_to: Optional[str] = Query(None),
    a_date_from: Optional[str] = Query(None), a_date_to: Optional[str] = Query(None),
    b_date_from: Optional[str] = Query(None), b_date_to: Optional[str] = Query(None),
    group: Optional[str] = Query(None),
):
    """Dive-in for ONE menu from the compare tab: per-branch A/B breakdown + a
    full monthly trend (qty/rev/cogs). BoM detail is fetched separately from
    /api/bom-cost/recipe/{sku_code}."""
    ew = ["b.name!='ALL'", "sk.sku_code=:sc"]; pb = {"sc": sku_code}
    _group_filter(ew, pb, group)
    import periods as _P
    with get_db() as con:
        _g = _P.guard({x: sp for x, sp in (("a", _P.span_of(a_from, a_to, a_date_from, a_date_to)),
                                           ("b", _P.span_of(b_from, b_to, b_date_from, b_date_to))) if sp},
                      _P.anchor(con), None)                       # PR-PP2: the same windows as the compare table
        if "a" in _g["windows"]:
            a_from = a_to = None; a_date_from, a_date_to = _g["windows"]["a"]
        if "b" in _g["windows"]:
            b_from = b_to = None; b_date_from, b_date_to = _g["windows"]["b"]
        A = {r["br"]: r for r in _menucmp_agg(con, "b.name br", "b.name", ew, pb, a_from, a_to, a_date_from, a_date_to)}
        B = {r["br"]: r for r in _menucmp_agg(con, "b.name br", "b.name", ew, pb, b_from, b_to, b_date_from, b_date_to)}
        covA = _covers_by_branch(con, a_from, a_to, a_date_from, a_date_to, group)
        covB = _covers_by_branch(con, b_from, b_to, b_date_from, b_date_to, group)
        cogs_m = _unified_cogs_expr("sm")
        trend = [dict(r) for r in con.execute(
            f"""SELECT sm.period_month month, ROUND(SUM(sm.qty),2) qty,
                       ROUND(SUM(sm.subtotal_ex_vat),2) rev, ROUND(SUM({cogs_m}),2) cogs
                FROM v_sales_sku_monthly sm JOIN branches b ON b.branch_id=sm.branch_id
                JOIN skus sk ON sk.sku_id=sm.sku_id
                WHERE {' AND '.join(ew)} GROUP BY sm.period_month ORDER BY sm.period_month""", pb).fetchall()]
    branches = []
    for nm in set(A) | set(B):
        a = A.get(nm); b = B.get(nm)
        qa = (a or {}).get("qty", 0) or 0; qb = (b or {}).get("qty", 0) or 0
        branches.append({"branch": nm, "qty_a": qa, "qty_b": qb, "qty_delta_pct": _cmp_pct(qa, qb),
                         "rev_a": (a or {}).get("rev", 0) or 0, "rev_b": (b or {}).get("rev", 0) or 0,
                         "cogs_a": (a or {}).get("cogs", 0) or 0, "cogs_b": (b or {}).get("cogs", 0) or 0,
                         "covers_a": covA.get(nm, 0), "covers_b": covB.get(nm, 0)})
    branches.sort(key=lambda r: -((r["qty_a"] or 0) + (r["qty_b"] or 0)))
    return {"sku_code": sku_code, "branches": branches, "trend": trend}


@router.get("/api/sales/heavy-eaters")
def heavy_eaters(
    branch:     Optional[str] = Query(None),
    group:      Optional[str] = Query(None),
    date_from:  Optional[str] = Query(None),
    date_to:    Optional[str] = Query(None),
    min_covers: int           = Query(1, ge=1, le=50),
    limit:      int           = Query(40, ge=1, le=200),
):
    """Per-cover economics & 'heavy-eater' distribution for the AYCE buffet.

    Joins per-bill BoM COGS (sale_bill_cogs) with the bill header
    (sales_transaction → covers + ex-VAT revenue) on receipt_no. For each
    bill: revenue/head, COGS/head and margin/head. Then summarises the
    distribution + the costly tail (heavy eaters = COGS/head ≥ p90) and the
    loss-making bills (margin/head < 0). sale_bill_cogs is a rolling ~31-day
    window, so the date range is bounded to whatever it currently holds."""
    from collections import defaultdict
    base = ["t.source='Easy'", "b.name!='ALL'", "t.customer_count>=:mc", "t.total_inc_vat>0"]
    p: dict = {"mc": min_covers}
    if branch:
        base.append("b.name=:br"); p["br"] = branch
    _group_filter(base, p, group)
    if date_from: base.append("sbc.day>=:df"); p["df"] = date_from
    if date_to:   base.append("sbc.day<=:dt"); p["dt"] = date_to
    with get_db() as con:
        win = con.execute("SELECT MIN(day) lo, MAX(day) hi FROM sale_bill_cogs").fetchone()
        rows = con.execute(f"""
            SELECT b.name br, sbc.day day, sbc.receipt_no rcpt, t.customer_count cov,
                   {net_revenue_row('t')} rev, sbc.cogs_bom cogs
            FROM sale_bill_cogs sbc
            JOIN sales_transaction t ON t.receipt_no = sbc.receipt_no AND t.branch_id = sbc.branch_id
            JOIN branches b ON b.branch_id = sbc.branch_id
            WHERE {' AND '.join(base)}""", p).fetchall()
    bills = []
    for r in rows:
        cov = r["cov"] or 0
        if cov < 1:
            continue
        rev = r["rev"] or 0.0; cogs = r["cogs"] or 0.0
        bills.append({"branch": r["br"], "day": r["day"], "receipt": r["rcpt"],
                      "covers": cov, "rev_head": rev / cov, "cogs_head": cogs / cov,
                      "margin_head": (rev - cogs) / cov, "rev": rev, "cogs": cogs})
    n = len(bills)

    def pctile(sv, q):
        if not sv:
            return 0.0
        i = min(len(sv) - 1, max(0, int(round(q * (len(sv) - 1)))))
        return sv[i]
    ch_sorted = sorted(b["cogs_head"] for b in bills)
    percentiles = {k: round(pctile(ch_sorted, q), 2) for k, q in
                   (("p50", .5), ("p75", .75), ("p90", .9), ("p95", .95), ("p99", .99))}
    p90 = pctile(ch_sorted, .9)
    tot_cov = sum(b["covers"] for b in bills)
    tot_cogs = sum(b["cogs"] for b in bills)
    tot_rev = sum(b["rev"] for b in bills)

    def agg(lst):
        c = sum(x["covers"] for x in lst); cg = sum(x["cogs"] for x in lst); rv = sum(x["rev"] for x in lst)
        return {"bills": len(lst), "covers": c, "cogs": round(cg, 2), "rev": round(rv, 2),
                "avg_cogs_head": round(cg / c, 2) if c else 0, "avg_rev_head": round(rv / c, 2) if c else 0,
                "avg_margin_head": round((rv - cg) / c, 2) if c else 0,
                "pct_bills": round(100 * len(lst) / n, 1) if n else 0,
                "pct_cogs": round(100 * cg / tot_cogs, 1) if tot_cogs else 0}
    heavy = [b for b in bills if p90 > 0 and b["cogs_head"] >= p90]
    loss = [b for b in bills if b["margin_head"] < 0]

    hi = pctile(ch_sorted, .99) or (ch_sorted[-1] if ch_sorted else 0)
    nb = 12; width = (hi / nb) if hi > 0 else 1
    hcounts = [0] * (nb + 1)
    for b in bills:
        idx = min(nb, int(b["cogs_head"] // width)) if width > 0 else 0
        hcounts[idx] += 1
    histogram = [{"lo": round(i * width, 1), "hi": round((i + 1) * width, 1) if i < nb else None,
                  "count": hcounts[i]} for i in range(nb + 1)]

    bb = defaultdict(lambda: {"bills": 0, "heavy": 0, "covers": 0, "cogs": 0.0, "rev": 0.0})
    for b in bills:
        d = bb[b["branch"]]; d["bills"] += 1; d["covers"] += b["covers"]
        d["cogs"] += b["cogs"]; d["rev"] += b["rev"]
        if p90 > 0 and b["cogs_head"] >= p90:
            d["heavy"] += 1
    by_branch = []
    for nm, d in bb.items():
        c = d["covers"]
        by_branch.append({"branch": nm, "bills": d["bills"], "heavy_bills": d["heavy"],
                          "heavy_pct": round(100 * d["heavy"] / d["bills"], 1) if d["bills"] else 0,
                          "cogs_head": round(d["cogs"] / c, 2) if c else 0,
                          "rev_head": round(d["rev"] / c, 2) if c else 0,
                          "margin_head": round((d["rev"] - d["cogs"]) / c, 2) if c else 0})
    by_branch.sort(key=lambda x: -x["heavy_pct"])

    worst = sorted(bills, key=lambda b: b["margin_head"])[:limit]
    examples = [{"branch": b["branch"], "day": b["day"], "receipt": b["receipt"], "covers": b["covers"],
                 "rev_head": round(b["rev_head"], 2), "cogs_head": round(b["cogs_head"], 2),
                 "margin_head": round(b["margin_head"], 2)} for b in worst]

    return {"window": {"lo": win["lo"] if win else None, "hi": win["hi"] if win else None},
            "range": [date_from or (win["lo"] if win else None), date_to or (win["hi"] if win else None)],
            "n_bills": n, "total_covers": tot_cov,
            "avg_cogs_head": round(tot_cogs / tot_cov, 2) if tot_cov else 0,
            "avg_rev_head": round(tot_rev / tot_cov, 2) if tot_cov else 0,
            "avg_margin_head": round((tot_rev - tot_cogs) / tot_cov, 2) if tot_cov else 0,
            "percentiles": percentiles, "heavy_threshold": round(p90, 2),
            "heavy": agg(heavy), "loss": agg(loss),
            "histogram": histogram, "by_branch": by_branch, "examples": examples}


@router.get("/api/sales/daily")
def sales_daily(
    date:   Optional[str] = Query(None),
    branch: Optional[str] = Query(None),
    group:  Optional[str] = Query(None),
    gran:   str           = Query("day"),     # day | week | month | year
    trend:  int           = Query(8, ge=2, le=26),
):
    """Per-branch sales for a day / week / month / year with the two comparisons of the one period
    pattern (periods.resolve, docs/PERIOD_PATTERN.md): เทียบช่วงก่อน + เทียบปีก่อน. A period not finished
    is to-date through the last closed day and both bases are their first n days (week: the same weekdays
    last week, not 28 days back; month: days 1..n of last month; year: 1 Jan..the same date last year).
    revenue/head, a trailing trend, and anomaly flags. REVENUE ONLY (ex-VAT goods = food+drink+other) —
    no cost here; COGS stays on BoM elsewhere."""
    import datetime as _dt
    import periods as _P
    gran = _P.kind_of(gran, "day")
    if gran == "range":
        gran = "day"
    base = ["t.source='Easy'", "b.name!='ALL'"]
    params: dict = {}
    scope_filter(base, params, branch, group)
    where = " AND ".join(base)
    pct = _P.pct

    with get_db() as con:
        # The default focus is the latest FINISHED day with its bills in (periods.anchor =
        # rules.latest_settled_day): from the noon sync the newest day is today with a few hours
        # of bills, and a day/MTD tile built on it read as -99.9% (owner 2026-10-02). An explicit
        # `date` still reaches today.
        maxd = _P.anchor(con, where, params)
        if not maxd and date:
            maxd = date[:10]
        if not maxd:
            return {"gran": gran, "focus": None, "branches": [], "total": {}, "trend": [], "anomalies": []}
        # Default focus must skip a not-yet-fully-synced trailing month, else the
        # latest "month" is a 3-branch sliver -> -100% MoM for the unsynced branches.
        if not date:
            _pm = _partial_months(con)
            if maxd[:7] in _pm:
                _mp = {f"_pm{i}": m for i, m in enumerate(_pm)}
                _ph = ",".join(f":{k}" for k in _mp)
                _rd = con.execute(
                    f"SELECT MAX(date(t.sold_at)) FROM sales_transaction t "
                    f"JOIN branches b ON b.branch_id=t.branch_id WHERE {where} "
                    f"AND strftime('%Y-%m', t.sold_at) NOT IN ({_ph})",
                    {**params, **_mp}).fetchone()[0]
                if _rd:
                    maxd = _rd
        focus = (date or maxd)[:10]
        anchor_day = focus if focus > maxd else maxd        # an explicit later day is honoured (it is asked for)
        w = _P.windows(con, gran, where, params, pick=focus, anchor_day=anchor_day)
        (cs, ce), (pms, pme), (pys, pye) = ((_dt.date.fromisoformat(a), _dt.date.fromisoformat(b))
                                            for a, b in (w["cur"], w["prev"], w["ly"]))

        def fetch(s, e):
            # revenue ex-VAT ex-service-charge = total_inc_vat/1.07 − service_charge (matches
            # accounting & sales_sku_monthly). NOT food+drink+other — those double-count
            # buffet (buffet dishes duplicated in food + the package in other → +~50%).
            rows = con.execute(f"""
                SELECT b.name branch,
                       ROUND({net_revenue_sum('t')},2) rev,
                       SUM(t.customer_count) cust, COUNT(*) bills
                FROM sales_transaction t JOIN branches b ON b.branch_id=t.branch_id
                WHERE {where} AND date(t.sold_at) BETWEEN :s AND :e
                GROUP BY b.name""", {**params, "s": s.isoformat(), "e": e.isoformat()}).fetchall()
            return {r["branch"]: {"rev": r["rev"] or 0, "cust": r["cust"] or 0, "bills": r["bills"]} for r in rows}

        cur = fetch(cs, ce)
        # Guard partial coverage: a base that starts before our data begins is incomplete
        # → dropped (no bogus deltas like YoY +472% vs the partial first month).
        pm = fetch(pms, pme) if w["has_prev"] else {}
        py = fetch(pys, pye) if w["has_ly"] else {}

        out = []
        for nm in sorted(set(cur) | set(pm) | set(py)):
            c = cur.get(nm, {"rev": 0, "cust": 0, "bills": 0})
            p = pm.get(nm); y = py.get(nm)
            crev, ccust = c["rev"], c["cust"]
            out.append({
                "branch": nm, "revenue": crev, "customers": ccust, "bills": c["bills"],
                "rev_per_head": round(crev / ccust, 1) if ccust else None,
                "mom_rev": (p or {}).get("rev"),
                "mom_delta": round(crev - (p or {}).get("rev", 0), 2) if p else None,
                "mom_pct": pct(crev, (p or {}).get("rev", 0)) if p else None,
                "yoy_rev": (y or {}).get("rev"),
                "yoy_delta": round(crev - (y or {}).get("rev", 0), 2) if y else None,
                "yoy_pct": pct(crev, (y or {}).get("rev", 0)) if y else None,
                "prev_customers": (p or {}).get("cust"), "ly_customers": (y or {}).get("cust"),
                "prev_rev_per_head": round(p["rev"] / p["cust"], 1) if p and p["cust"] else None,
                "ly_rev_per_head": round(y["rev"] / y["cust"], 1) if y and y["cust"] else None,
            })
        out.sort(key=lambda r: -r["revenue"])
        # owner 2026-10-26: a branch with no sale here nor in the period before is not a row (rules.closed_branches; the year-ago
        # period does not count); the totals below keep its sales, and the year-ago total's share of it is the footnote
        _closed = closed_branches(con, [w["cur"], w["prev"] if w["has_prev"] else None], keep=branch)
        ly_hidden = hidden_amount({nm: v["rev"] for nm, v in py.items()}, _closed)
        out = drop_closed(out, _closed)

        def tot(d): return (sum(x["rev"] for x in d.values()), sum(x["cust"] for x in d.values()),
                            sum(x["bills"] for x in d.values()))
        crv, ccu, cbi = tot(cur); prv, pcu, pbi = tot(pm); yrv, ycu, ybi = tot(py)
        # per head to 1 decimal before the change, as the management report and its month series do (one answer)
        cur_rph = round(crv / ccu, 1) if ccu else None
        prev_rph = round(prv / pcu, 1) if pcu else None
        yoy_rph = round(yrv / ycu, 1) if ycu else None
        total = {"revenue": round(crv, 2), "customers": ccu, "bills": cbi,
                 "rev_per_head": round(crv / ccu, 1) if ccu else None,
                 "prev": {"revenue": round(prv, 2), "customers": pcu, "bills": pbi,
                          "rev_per_head": round(prev_rph, 1) if prev_rph else None} if pm else None,
                 "ly": {"revenue": round(yrv, 2), "customers": ycu, "bills": ybi,
                        "rev_per_head": round(yoy_rph, 1) if yoy_rph else None} if py else None,
                 "mom_delta": round(crv - prv, 2) if pm else None, "mom_pct": pct(crv, prv) if pm else None,
                 "yoy_delta": round(crv - yrv, 2) if py else None, "yoy_pct": pct(crv, yrv) if py else None,
                 # customer-count + revenue/head deltas over the SAME windows as revenue
                 "cust_mom_pct": pct(ccu, pcu) if pm else None, "cust_yoy_pct": pct(ccu, ycu) if py else None,
                 "rph_mom_pct": pct(cur_rph, prev_rph) if (pm and cur_rph is not None) else None,
                 "rph_yoy_pct": pct(cur_rph, yoy_rph) if (py and cur_rph is not None) else None}
        if ly_hidden:      # the year-ago total holds sales of branches this table hides (closed since): the footnote, no row
            total["ly_hidden"] = ly_hidden

        # trailing trend (chain total rev): `trend` periods ending at the current one (to date); the
        # earlier periods are whole calendar periods
        tr = []
        for i in range(trend - 1, -1, -1):
            s = _dt.date.fromisoformat(_P.shift(gran, cs, -i))
            e = ce if i == 0 else _dt.date.fromisoformat(_P.shift(gran, s, 1)) - _dt.timedelta(days=1)
            if w["first_day"] and e.isoformat() < w["first_day"]:
                continue                                     # before the first day with bills: no bar
            tr.append({"start": s.isoformat(), "end": e.isoformat(),
                       "revenue": round(sum(x["rev"] for x in fetch(s, e).values()), 2),
                       "partial": (i == 0 and not w["complete"])})

        # anomaly: chain total vs trailing average (per day, so a to-date period is fair) + big per-branch movers
        prior = [x["revenue"] / (((_dt.date.fromisoformat(x["end"]) - _dt.date.fromisoformat(x["start"])).days + 1) or 1)
                 for x in tr[:-1] if x["revenue"]]
        avg = sum(prior) / len(prior) if prior else 0
        anomalies = []
        ap = pct(crv / (w["days"] or 1), avg) if avg else None
        if ap is not None and abs(ap) >= 20:
            anomalies.append({"scope": "chain", "pct": ap,
                              "msg": f"ยอดรวมต่อวัน{'สูง' if ap > 0 else 'ต่ำ'}กว่าค่าเฉลี่ย {abs(ap):.0f}%"})
        for r in out:
            if r["mom_pct"] is not None and abs(r["mom_pct"]) >= 30:
                anomalies.append({"scope": r["branch"], "pct": r["mom_pct"],
                                  "msg": f"{r['branch']} {'พุ่ง' if r['mom_pct'] > 0 else 'ตก'} {abs(r['mom_pct']):.0f}% เทียบช่วงก่อน"})

    mtd = (gran == "month" and not w["complete"])
    return {"gran": gran, "focus": focus, "anchor": w["anchor"],
            "cur_range": list(w["cur"]), "prev_range": list(w["prev"]), "yoy_range": list(w["ly"]),
            "has_prev": w["has_prev"], "has_ly": w["has_ly"],
            "period": _P.public(w),
            "mtd": mtd, "mtd_cutoff_day": w["days"] if mtd else None,
            # PR-PP2: the headline % is the calendar to-date rule (the weekday-fair baseline stays on the report §3)
            "mom_basis": "full" if w["complete"] else "to_date",
            "branches": out, "total": total, "trend": tr, "anomalies": anomalies}




_SPIKE_PERIODS = ("day", "week", "mtd", "range")
_SPIKE_MAX_RANGE_DAYS = 92
_SPIKE_BASE_DAYS = 21          # baseline = the 3 weeks before the period (or the day)


def _spike_iso(v: Optional[str], label: str):
    """Parse a YYYY-MM-DD query value; 400 with a formal Thai message otherwise."""
    import datetime as _dt
    from fastapi import HTTPException
    try:
        return _dt.date.fromisoformat((v or "").strip()[:10])
    except ValueError:
        raise HTTPException(400, f"{label}ไม่ถูกต้อง ใช้รูปแบบ ปปปป-ดด-วว (เช่น 2026-10-01)")


def _spike_window(period: str, anchor, date_from: Optional[str], date_to: Optional[str], latest):
    """(start, end) of a non-day spike period. `anchor` and `latest` are dates; the end
    never passes `latest` (the last finished day), so the day still trading stays out."""
    import datetime as _dt
    from fastapi import HTTPException
    if period == "week":
        return anchor - _dt.timedelta(days=anchor.weekday()), anchor
    if period == "mtd":
        return anchor.replace(day=1), anchor
    if not date_from or not date_to:
        raise HTTPException(400, "ช่วงที่กำหนดเองต้องระบุทั้งวันเริ่มต้น (date_from) และวันสิ้นสุด (date_to)")
    s, e = _spike_iso(date_from, "วันเริ่มต้น"), _spike_iso(date_to, "วันสิ้นสุด")
    if s > e:
        raise HTTPException(400, "วันเริ่มต้นต้องไม่อยู่หลังวันสิ้นสุด")
    if (e - s).days + 1 > _SPIKE_MAX_RANGE_DAYS:
        raise HTTPException(400, f"ช่วงวันที่ยาวได้ไม่เกิน {_SPIKE_MAX_RANGE_DAYS} วัน")
    if e > latest:
        raise HTTPException(400, f"วันสิ้นสุดต้องไม่เกินวันล่าสุดที่มีข้อมูลครบ ({latest.isoformat()})")
    return s, e


def _spike_base(base_from, base_to, ps, latest):
    """PR-TD · (start, end) of a chosen comparison period: both dates, start <= end, <= 92 days, finished days only, ending
    before the period starts. HTTP 400 in formal Thai otherwise."""
    from fastapi import HTTPException
    if not base_from or not base_to:
        raise HTTPException(400, "ช่วงที่ใช้เทียบต้องระบุทั้งวันเริ่มต้น (base_from) และวันสิ้นสุด (base_to)")
    s, e = _spike_iso(base_from, "วันเริ่มต้นของช่วงที่ใช้เทียบ"), _spike_iso(base_to, "วันสิ้นสุดของช่วงที่ใช้เทียบ")
    if s > e:
        raise HTTPException(400, "วันเริ่มต้นของช่วงที่ใช้เทียบต้องไม่อยู่หลังวันสิ้นสุด")
    if (e - s).days + 1 > _SPIKE_MAX_RANGE_DAYS:
        raise HTTPException(400, f"ช่วงที่ใช้เทียบยาวได้ไม่เกิน {_SPIKE_MAX_RANGE_DAYS} วัน")
    if e > latest:
        raise HTTPException(400, f"ช่วงที่ใช้เทียบต้องไม่เกินวันล่าสุดที่มีข้อมูลครบ ({latest.isoformat()})")
    if e >= ps:
        raise HTTPException(400, "ช่วงที่ใช้เทียบต้องสิ้นสุดก่อนวันเริ่มต้นของช่วงที่เลือก")
    return s, e


def _jd7(d) -> int:
    """Weekday key of a date, equal to SQLite CAST(julianday(day) AS INTEGER) % 7."""
    return (d.toordinal() + 1721424) % 7


# ── new menus (owner 2026-10-24): a menu is NEW at a branch while anchor - first_sold < 28 days ──
_SPIKE_NEW_DAYS = 28
_SPIKE_TREND_MIN_AGE = 14
_SPIKE_THIN_DAYS = 5            # an existing menu sold on fewer baseline days than this has a "thin" baseline
_SPIKE_THIN_MIN_QTY = 20        # ... and is listed from this many plates (the old new-menu floor), marked thin_base       # the trend compares the first 7 days with the latest 7: needs two full weeks
_SPIKE_GAP_DAYS = 21            # PR-TD: an existing menu that came back after this many days without a sale at the branch ...
_SPIKE_GAP_OPEN = 14            # ... on which the branch itself traded on at least this many days (a closed branch is not a gap) = "กลับมาขาย"
_TH_MON_SPIKE = ("ม.ค.", "ก.พ.", "มี.ค.", "เม.ย.", "พ.ค.", "มิ.ย.", "ก.ค.", "ส.ค.", "ก.ย.", "ต.ค.", "พ.ย.", "ธ.ค.")
_FIRST_SOLD: dict = {"key": None, "map": {}, "full": False, "bids": set()}
_FIRST_SOLD_LOCK = _threading.Lock()


def _first_sold_map(con, bids) -> dict:
    """{(branch_id, sku_id): 'YYYY-MM-DD'} = the first day the SKU sold (qty > 0) at the branch, over all of
    sales_sku_daily (history starts 22 Mar 2026: an older menu reads as that date). The table changes only at a
    sync, so the map is kept per process and keyed on the DB file, the table's rowid span and core._data_gen
    (bumped by the full flush after a sync): the whole-table MIN(day) costs ~0.4 s once, then nothing. One or
    two branches read through the (branch, day) index instead of the whole table. An in-memory DB (tests) is
    never kept."""
    import core as _core
    path = next((r[2] for r in con.execute("PRAGMA database_list").fetchall() if r[1] == "main"), "") or ""
    lo = con.execute("SELECT MIN(rowid) FROM sales_sku_daily").fetchone()[0]
    hi = con.execute("SELECT MAX(rowid) FROM sales_sku_daily").fetchone()[0]
    key = (path, lo, hi, _core._data_gen) if path else None
    bids = set(bids)
    with _FIRST_SOLD_LOCK:
        if key is not None and _FIRST_SOLD["key"] == key and (_FIRST_SOLD["full"] or bids <= _FIRST_SOLD["bids"]):
            return _FIRST_SOLD["map"]
        if key is None or _FIRST_SOLD["key"] != key:
            fresh = {"key": key, "map": {}, "full": False, "bids": set()}
        else:
            fresh = _FIRST_SOLD
        cur = con.cursor()
        cur.row_factory = None
        todo = bids - fresh["bids"]
        if len(todo) <= 2:
            for bid in sorted(todo):
                for sid, d in cur.execute("SELECT sku_id, MIN(day) FROM sales_sku_daily WHERE branch_id=? AND qty > 0 "
                                          "GROUP BY sku_id", (bid,)):
                    fresh["map"][(bid, sid)] = d[:10]
            fresh["bids"] |= todo
        else:
            fresh["map"] = {(bid, sid): d[:10] for bid, sid, d in cur.execute(
                "SELECT branch_id, sku_id, MIN(day) FROM sales_sku_daily NOT INDEXED WHERE qty > 0 GROUP BY branch_id, sku_id")}
            fresh["full"] = True
        if key is not None:
            _FIRST_SOLD.update(fresh)
        return fresh["map"]


# ── PR-ST (owner 2026-10-25) · the per-head standard: plates per 100 customers, not plates ──────────────────────────
# "ถ้าลูกค้าเยอะจะพุ่งเยอะ ถ้าลูกค้าน้อยจะหดแรง ทำมาตรฐาน": a busy week is not a spike. rate = plates / customers x 100.
# Customers ("basis") of a row:
#   buffet  a (B)/(BF) menu: the branch's buffet customers = menu_fact_total pax, every tier (buffet bills only);
#   all     an a-la-carte menu ((A)/(AL)/no prefix): every customer of the branch = SUM(sales_transaction.customer_count),
#           source 'Easy' (what /api/sales/daily counts); also a (B) menu when the fact tables do not cover the window;
#   days    no customer counts at all for the branch: one per trading day, i.e. the plate standard of PR-SP.
# The weekday-fair "ปกติ" keeps its shape: expected plates = sum over weekdays of (baseline plates / baseline customers of
# that weekday) x the period's customers of that weekday; a weekday without baseline customers uses the whole baseline.
_SPIKE_PER = 100
_SPIKE_NB = "nonbuffet"                   # the group of plates sold on bills that are not buffet bills
_SPIKE_NB_LABEL = "ไม่ใช่บุฟเฟ่ต์"
_SPIKE_TIER_CHIPS = 3
_SPIKE_RANK_MIN_X = 1.05                   # PR-RK: the ranked list's noise floor - at least x1.05 per head ...
_SPIKE_RANK_MIN_EXCESS = 3                 # ... and at least 3 plates above the normal (a row of the old 1.5x rule is always listed)
_SPIKE_TOP = 15                            # PR-SC: the card shows this many rows first; "ดูทั้งหมด (N)" opens the rest
_SPIKE_COST_MODULES = {"cost.analysis", "profit.pnl"}   # PL3: a role holding neither gets no cost key at all


class _SpikeCost:
    """PR-SC (owner 2026-10-25) · the food cost behind a spike, from THE cost engine: per SKU and month the unit cost
    COGS multiplies (rules.effective_unit_cost_sql with the month - from the Grand Menu month on the analyst's monthly
    dish-cost list bc_sku_cost_list, else a released BoM Center version, else the recipe / cost-management chain), a
    buffet TIER package code at ฿0 as rules.cogs_expr does (the dishes eaten carry the cost). One pass over the period's
    plates of the scope (branch x SKU x month, v_sales_sku_daily) gives both the scope's food cost (the COGS of the
    same rows and expression as /api/sales/cost-per-head) and each row's month-weighted unit cost. Revenue = the
    scope's net revenue (rules.net_revenue_sum over the Easy bills of the period; the lines' ex-VAT subtotal when the
    DB has no bills table)."""

    def __init__(self, con, ps, pe, bfilt, params):
        import datetime as _dt
        cur = con.cursor()
        cur.row_factory = None
        bjoin = "JOIN branches b ON b.branch_id=d.branch_id" if bfilt else ""
        q = {**params, "ps": ps.isoformat(), "pe": pe.isoformat()}
        self.plates: dict = {}                   # (bid, sid) -> {month: plates}
        self.rev: dict = {}                      # (bid, sid) -> ex-VAT revenue of the period (a paid dish's price side, PR-SK)
        sub = 0.0
        sm: set = set()
        for bid, sid, m, qq, rv in cur.execute(f"""
            SELECT d.branch_id, d.sku_id, substr(d.day, 1, 7), SUM(d.qty), SUM(d.subtotal_ex_vat)
            FROM v_sales_sku_daily d {bjoin} WHERE d.day BETWEEN :ps AND :pe {bfilt}
            GROUP BY d.branch_id, d.sku_id, substr(d.day, 1, 7)""", q):
            self.plates.setdefault((bid, sid), {})[m] = qq or 0.0
            self.rev[(bid, sid)] = self.rev.get((bid, sid), 0.0) + (rv or 0.0)
            sub += rv or 0.0
            sm.add((sid, m))
        self.unit: dict = {}                     # (sid, month) -> ฿ per plate (0 = uncosted, or a package)
        self.pkg: set = set()                    # sku ids that are buffet tier packages: never costed
        self._cur, self.month = cur, pe.isoformat()[:7]
        self._fill(sorted(sm))
        self.food_cost = sum(qq * self.unit.get((sid, m), 0.0)
                             for (_b, sid), mm in self.plates.items() for m, qq in mm.items())
        try:
            nx = (pe + _dt.timedelta(days=1)).isoformat()
            tj = "JOIN branches b ON b.branch_id=t.branch_id" if bfilt else ""
            rv = cur.execute(f"SELECT {net_revenue_sum('t')} FROM sales_transaction t {tj} WHERE t.source='Easy' "
                             f"AND t.sold_at >= :ps AND t.sold_at < :nx {bfilt}", {**q, "nx": nx}).fetchone()[0]
            self.revenue, self.revenue_basis = float(rv or 0.0), "net"
        except Exception:
            self.revenue, self.revenue_basis = sub, "ex_vat_lines"

    def _fill(self, pairs) -> None:
        """self.unit[(sid, month)] for every pair not read yet: the dish cost of THE engine, a tier package at ฿0."""
        from rules import is_tier_code
        pairs = [p for p in pairs if p not in self.unit]
        expr = effective_unit_cost_sql("x", "x.m")
        for i in range(0, len(pairs), 400):
            chunk = pairs[i:i + 400]
            vals = ",".join("(?,?)" for _ in chunk)
            for sid, m, code, uc in self._cur.execute(
                    f"WITH x(sku_id, m) AS (VALUES {vals}) SELECT x.sku_id, x.m, sk.sku_code, {expr} "
                    f"FROM x LEFT JOIN skus sk ON sk.sku_id = x.sku_id", [v for p in chunk for v in p]):
                if is_tier_code(code or ""):
                    self.pkg.add(sid)
                    uc = 0.0
                self.unit[(sid, m)] = float(uc or 0.0)

    def unit_of(self, bid, sid) -> float:
        """The row's unit cost weighted by its plates in each month of the period (a dish cost that changes on the 1st).
        DV3: a row without a plate in the period (sold in the baseline only) = the dish cost of the period's last month."""
        mm = self.plates.get((bid, sid)) or {}
        tq = sum(mm.values())
        if tq <= 0:
            if (sid, self.month) not in self.unit:
                self._fill([(sid, self.month)])
            return self.unit.get((sid, self.month), 0.0)
        return sum(qq * self.unit.get((sid, m), 0.0) for m, qq in mm.items()) / tq

    def share(self, baht):
        """฿ as % of the scope's food cost of the period."""
        return round(baht / self.food_cost * 100, 2) if self.food_cost > 0 else None

    def pp(self, baht):
        """฿ as percentage points of the scope's food-cost % (฿ / net revenue x 100)."""
        return round(baht / self.revenue * 100, 2) if self.revenue > 0 else None


def _spike_kind(desc) -> str:
    """'buffet' for a (B) / (BF) menu, else 'alc' (à la carte and everything without a buffet prefix)."""
    d = (desc or "").lstrip().upper()
    return "buffet" if d.startswith("(B)") or d.startswith("(BF)") else "alc"


def _spike_name(desc, code) -> str:
    return (desc or code or "").replace("(B) ", "").replace("(A) ", "")


def _spike_norm(menu) -> str:
    """PR-SK · the dish key behind a name: two SKU spellings of one dish ("1,000 กรัม" / "1000 กรัม", a double space, a
    trailing space) fold into one row. Punctuation inside the name and every run of whitespace are dropped."""
    return "".join(ch for ch in (menu or "").lower() if ch not in ",.·-_ \t ")


class _SpikeCust:
    """Customers per branch x day for one call: all customers (sales_transaction, source 'Easy'), buffet customers per
    tier (menu_fact_total) and the days the fact tables cover. Missing tables read as no data."""

    def __init__(self, con, bids, d0, d1):
        import datetime as _dt
        self.all: dict = {}
        self.tier: dict = {}
        self.buf: dict = {}
        self.fact_lo = self.fact_hi = None
        cur = con.cursor()
        cur.row_factory = None
        bids = sorted(bids)
        one = "AND branch_id=?" if len(bids) == 1 else ""
        bp = tuple(bids) if one else ()
        try:
            for bid, day, n in cur.execute(
                    f"SELECT branch_id, date(sold_at), SUM(customer_count) FROM sales_transaction WHERE source='Easy' "
                    f"AND date(sold_at) BETWEEN ? AND ? {one} GROUP BY 1, 2", (d0.isoformat(), d1.isoformat(), *bp)):
                if n:
                    self.all.setdefault(bid, {})[_dt.date.fromisoformat(day[:10])] = float(n)
        except Exception:
            self.all = {}
        try:
            lo, hi = cur.execute("SELECT MIN(day), MAX(day) FROM menu_fact_total").fetchone()
            if lo and hi:
                self.fact_lo, self.fact_hi = _dt.date.fromisoformat(lo[:10]), _dt.date.fromisoformat(hi[:10])
                for bid, day, t, px in cur.execute(
                        f"SELECT branch_id, day, tier, SUM(pax) FROM menu_fact_total WHERE day BETWEEN ? AND ? {one} "
                        f"GROUP BY 1, 2, 3", (d0.isoformat(), d1.isoformat(), *bp)):
                    if px:
                        dd = _dt.date.fromisoformat(day[:10])
                        self.tier.setdefault(bid, {}).setdefault(dd, {})[str(t or "promo")] = float(px)
                        b = self.buf.setdefault(bid, {})
                        b[dd] = b.get(dd, 0.0) + float(px)
        except Exception:
            self.tier, self.buf, self.fact_lo, self.fact_hi = {}, {}, None, None

    def covers(self, a, b) -> bool:
        return self.fact_lo is not None and self.fact_lo <= a and self.fact_hi >= b

    def of(self, bid, basis, d) -> float:
        if basis == "days":
            return 1.0
        return (self.buf if basis == "buffet" else self.all).get(bid, {}).get(d, 0.0)

    def total(self, bid, basis, days) -> float:
        return sum(self.of(bid, basis, d) for d in days)


def _spike_basis(cust, kind, bid, wins) -> str:
    """The customers a row is measured on (see above). wins = [(a, b, trading days in a..b)]: each window must hold
    customers of the chosen basis, and a buffet basis needs the fact tables over every window."""
    if kind == "buffet" and all(cust.covers(a, b) for a, b, _ in wins) \
            and all(cust.total(bid, "buffet", ds) > 0 for _, _, ds in wins):
        return "buffet"
    if all(cust.total(bid, "all", ds) > 0 for _, _, ds in wins):
        return "all"
    return "days"


def _spike_new_menus(con, cands, anchor, ps, tdays=None, cust=None) -> list:
    """The rows of the new-menu list. cands = [(bid, branch, menu, code, sid, qty, first_sold date, kind)], every one new
    (anchor - first_sold < 28 days) and sold in the period ps..anchor. tdays = {bid: set(dates the branch traded)}
    covering anchor-27..anchor (read here when None). per_day = period plates / days the branch traded within
    max(ps, first_sold)..anchor; rate = the same plates per 100 customers of those days (PR-ST, basis as the spike part);
    trend = the latest 7 days vs the first 7 days, from age 14: on the rate when the branch has customers, else per day.
    PR-TD: a 9th item = the menu's first day ever when it is an existing menu that came back (_spike_returns): first_sold = the day it
    came back (= returned_on), qty = plates from that day, returning: true, first_sold_ever."""
    import datetime as _dt
    if not cands:
        return []
    ns = anchor - _dt.timedelta(days=_SPIKE_NEW_DAYS - 1)
    cur = con.cursor()
    cur.row_factory = None
    bids = sorted({c[0] for c in cands})
    ph = ",".join("?" * len(bids))
    if tdays is None:
        tdays = {}
        for bid, day in cur.execute(f"SELECT branch_id, day FROM sales_sku_daily WHERE branch_id IN ({ph}) "
                                    f"AND day BETWEEN ? AND ? GROUP BY branch_id, day", (*bids, ns.isoformat(), anchor.isoformat())):
            tdays.setdefault(bid, set()).add(_dt.date.fromisoformat(day[:10]))
    if cust is None:
        cust = _SpikeCust(con, bids, ns, anchor)
    need = [c for c in cands if (anchor - c[6]).days + 1 >= _SPIKE_TREND_MIN_AGE]
    l0 = (anchor - _dt.timedelta(days=6)).isoformat()
    sums: dict = {}                    # (bid, sid) -> [plates of the first 7 days, plates of the latest 7 days]
    if need:
        nb = sorted({c[0] for c in need}); sids = sorted({c[4] for c in need})
        f6 = {(c[0], c[4]): (c[6] + _dt.timedelta(days=6)).isoformat() for c in need}
        f0 = {(c[0], c[4]): c[6].isoformat() for c in need}      # PR-TD: a menu that came back counts from that day only
        # rows only (no GROUP BY), summed here; a chain-wide window reads faster as a plain scan (as the period query)
        bsql = f"AND branch_id IN ({','.join('?' * len(nb))})" if len(nb) <= 2 else ""
        scan = "" if bsql else "NOT INDEXED"
        for bid, sid, day, q in cur.execute(
                f"SELECT branch_id, sku_id, day, qty FROM sales_sku_daily {scan} WHERE day BETWEEN ? AND ? {bsql} "
                f"AND sku_id IN ({','.join('?' * len(sids))})",
                (min(c[6] for c in need).isoformat(), anchor.isoformat(), *(nb if bsql else ()), *sids)):
            e = f6.get((bid, sid))
            if e is None or not q:
                continue
            day = day[:10]
            if day < f0[(bid, sid)]:
                continue
            acc = sums.get((bid, sid))
            if acc is None:
                acc = sums[(bid, sid)] = [0.0, 0.0]
            if day <= e:
                acc[0] += q
            if day >= l0:
                acc[1] += q

    def tdays_in(bid, a, b):
        return [d for d in (tdays.get(bid) or ()) if a <= d <= b]

    out = []
    for cand in cands:
        bid, branch_name, menu, code, sid, pq, first, kind = cand[:8]
        ever = cand[8] if len(cand) > 8 else None          # PR-TD: a menu that came back - first = the day it came back
        age = (anchor - first).days + 1
        win = tdays_in(bid, max(ps, first), anchor)
        n = len(win)
        w1 = tdays_in(bid, first, first + _dt.timedelta(days=6))
        w2 = tdays_in(bid, anchor - _dt.timedelta(days=6), anchor)
        wins = [(max(ps, first), anchor, win)] + ([(first, first + _dt.timedelta(days=6), w1), (anchor - _dt.timedelta(days=6), anchor, w2)]
                                                    if age >= _SPIKE_TREND_MIN_AGE else [])
        basis = _spike_basis(cust, kind, bid, wins)
        c = cust.total(bid, basis, win) if basis != "days" else 0.0
        trend = None
        if age >= _SPIKE_TREND_MIN_AGE:
            acc = sums.get((bid, sid), (0.0, 0.0))
            f7 = acc[0] / len(w1) if w1 else None
            l7 = acc[1] / len(w2) if w2 else None
            if f7 is not None and l7 is not None:
                trend = {"first_per_day": round(f7, 1), "latest_per_day": round(l7, 1),
                         "pct": round((l7 / f7 - 1) * 100) if f7 > 0 else None}
                if basis != "days":
                    c1, c2 = cust.total(bid, basis, w1), cust.total(bid, basis, w2)
                    r1, r2 = acc[0] / c1 * _SPIKE_PER, acc[1] / c2 * _SPIKE_PER
                    trend.update({"first_rate": round(r1, 1), "latest_rate": round(r2, 1),
                                  "pct": round((r2 / r1 - 1) * 100) if r1 > 0 else None})
        out.append({"branch": branch_name, "menu": menu, "sku_code": code, "first_sold": first.isoformat(),
                    "first_sold_th": f"{first.day} {_TH_MON_SPIKE[first.month - 1]}", "age_days": age,
                    "qty": round(pq), "per_day": round(pq / n, 1) if n else None,
                    "kind": kind, "basis": basis, "customers": round(c) if basis != "days" else None,
                    "rate": round(pq / c * _SPIKE_PER, 1) if c > 0 else None, "trend": trend,
                    "_bid": bid, "_sid": sid, "_q": pq})
        if ever is not None:
            out[-1].update({"returning": True, "returned_on": first.isoformat(), "first_sold_ever": ever.isoformat()})
    out.sort(key=lambda r: (-r["qty"], r["menu"], r["branch"]))
    return out


def _spike_tiers(con, rows, cust, tdays, base, ps, pe, bs, be, threshold, pooled=False) -> None:
    """เพิ่มจาก tier (PR-ST): for each listed row, the plates of every buffet tier (menu_fact_day, buffet bills) and the
    rest of its plates ("ไม่ใช่บุฟเฟ่ต์" = sales_sku_daily plates - the fact plates, floored at 0, on non-buffet customers =
    all customers - buffet customers, floored at 1). Per group g, with g's own weekday-fair normal from the same 3 weeks:
    excess_g = plates_g - expected_g, expected_g = sum over weekdays of (baseline plates_g / baseline customers_g) x period
    customers_g (a weekday without baseline customers of g uses g's whole baseline; a group absent from the baseline uses
    the row's own normal per customer). Only the days the fact tables cover: none of the baseline uncovered -> no split
    (tiers None, tier_note); the period covered up to d -> the split of ps..d (tier_to). tier_rest = the row's excess -
    the sum of the groups' excess (customers moving between tiers), when the whole period is covered. Sets the keys
    in place. Rows carry their private keys _bid / _sid / _pq / _exp / _cp.
    PR-TD · pooled (a chosen comparison period, base_from / base_to): expected_g = (base plates_g / base customers_g) x period
    customers_g over the whole base, not per weekday; the base may end days before the period starts (the days between count
    on neither side)."""
    import datetime as _dt
    from rules import tier_label
    if not rows:
        return
    pooled_all = pooled
    hi = cust.fact_hi
    ce = min(pe, hi) if hi else None
    if hi is None or not cust.covers(bs, be) or ce < ps:
        note = ("ยังไม่มีข้อมูลแยก tier" if hi is None
                else f"ข้อมูลแยก tier มีถึง {hi.day} {_TH_MON_SPIKE[hi.month - 1]} ไม่ครอบคลุมช่วงนี้")
        for r in rows:
            r.update({"tiers": None, "tier_note": note})
        return
    pairs = sorted({(r["_bid"], r["_sid"]) for r in rows})
    cur = con.cursor()
    cur.row_factory = None
    fb: dict = {}                    # (bid, sid) -> {tier: [baseline plates per weekday 0..6]}
    fp: dict = {}                    # (bid, sid) -> {tier: period plates (covered days)}
    vals = ",".join("(?,?)" for _ in pairs)
    args = [x for b, s in pairs for x in (b, f"s:{s}")]
    for bid, ent, t, day, q in cur.execute(
            f"WITH p(b, e) AS (VALUES {vals}) SELECT m.branch_id, m.ent, m.tier, m.day, m.qty FROM p "
            f"JOIN menu_fact_day m ON m.branch_id = p.b AND m.ent = p.e AND m.day BETWEEN ? AND ?",
            (*args, bs.isoformat(), ce.isoformat())):
        if not q:
            continue
        k = (bid, int(ent[2:]))
        t = str(t or "promo")
        dd = _dt.date.fromisoformat(day[:10])
        if dd <= be:
            fb.setdefault(k, {}).setdefault(t, [0.0] * 7)[_jd7(dd)] += q
        elif dd >= ps:
            g = fp.setdefault(k, {})
            g[t] = g.get(t, 0.0) + q
    # sales_sku_daily plates of the covered part of the period (the whole period = the row's own plates)
    part: dict = {}
    if ce < pe:
        bl = sorted({b for b, _ in pairs})
        for bid, sid, q in cur.execute(
                f"SELECT branch_id, sku_id, SUM(qty) FROM sales_sku_daily WHERE branch_id IN ({','.join('?' * len(bl))}) "
                f"AND day BETWEEN ? AND ? GROUP BY 1, 2", (*bl, ps.isoformat(), ce.isoformat())):
            part[(bid, sid)] = q or 0.0
    cache: dict = {}

    def cust_w(bid):
        """{group: ([baseline customers per weekday], [period customers per weekday])} of the branch's trading days."""
        if bid in cache:
            return cache[bid]
        out: dict = {}
        has_all = bool(cust.all.get(bid))
        for d in tdays.get(bid) or ():
            if not (bs <= d <= ce) or be < d < ps:
                continue
            side = 0 if d <= be else 1
            ts = cust.tier.get(bid, {}).get(d, {})
            for t, px in ts.items():
                out.setdefault(t, ([0.0] * 7, [0.0] * 7))[side][_jd7(d)] += px
            if has_all:
                nbc = max(0.0, cust.all.get(bid, {}).get(d, 0.0) - sum(ts.values()))
                out.setdefault(_SPIKE_NB, ([0.0] * 7, [0.0] * 7))[side][_jd7(d)] += nbc
        cache[bid] = out
        return out

    for r in rows:
        bid, sid = r["_bid"], r["_sid"]
        if r["basis"] == "days":
            r.update({"tiers": None, "tier_note": "สาขานี้ไม่มีจำนวนลูกค้า"})
            continue
        cw = cust_w(bid)
        tb, tpp = fb.get((bid, sid), {}), fp.get((bid, sid), {})
        e = base.get((bid, sid))
        sku_b = e[:7] if e else [0.0] * 7
        sku_p = r["_pq"] if ce == pe else part.get((bid, sid), 0.0)
        groups = {t: (tb.get(t, [0.0] * 7), tpp.get(t, 0.0)) for t in set(tb) | set(tpp) | {t for t in cw if t != _SPIKE_NB}}
        if _SPIKE_NB in cw:
            nbb = [max(0.0, sku_b[w] - sum(v[0][w] for v in groups.values())) for w in range(7)]
            groups[_SPIKE_NB] = (nbb, max(0.0, sku_p - sum(v[1] for v in groups.values())))
        rf = r["_exp"] / r["_cp"] if r["_cp"] else 0.0       # the row's own normal, plates per customer
        tiers = []
        for t, (pb, pp) in groups.items():
            cb, cp = cw.get(t, ([0.0] * 7, [0.0] * 7))
            sb, sc = sum(pb), sum(cb)
            pooled = sb / sc if sc > 0 else rf
            exp = pooled * sum(cp) if pooled_all else sum((pb[w] / cb[w] if cb[w] > 0 else pooled) * cp[w] for w in range(7))
            c = sum(cp)
            if pp <= 0 and exp <= 0:
                continue
            cd = max(c, 1.0) if t == _SPIKE_NB else c
            x = pp / exp if exp > 0 else None
            tiers.append({"tier": t, "label": _SPIKE_NB_LABEL if t == _SPIKE_NB else tier_label(t),
                          "plates": round(pp), "customers": round(c),
                          "rate": round(pp / cd * _SPIKE_PER, 1) if cd > 0 else None,
                          "normal_rate": round(exp / cd * _SPIKE_PER, 1) if cd > 0 else None,
                          "excess": round(pp - exp, 1), "x": round(x, 1) if x is not None else None,
                          "high": bool(x is not None and x >= threshold and pp >= 5)})
        tiers.sort(key=lambda g: (-g["excess"], g["label"]))
        r["tiers"] = tiers
        if ce < pe:
            r["tier_to"] = ce.isoformat()
        else:
            r["tier_rest"] = round(r["_pq"] - r["_exp"] - sum(g["excess"] for g in tiers), 1)


def _spike_returns(cur, scan, bjoin, bfilt, q, tdays) -> dict:
    """PR-TD (owner 2026-10-25) · existing menus that came back. {(branch_id, sku_id): (returned_on date, plates from that day to the
    period end)} for the latest day r in ns..pe (ns = pe - 27, the new-menu window) on which the SKU sold at the branch after no sale
    there on the _SPIKE_GAP_DAYS (21) days before r (r - 21 .. r - 1), while the branch itself traded on at least _SPIKE_GAP_OPEN (14) of
    them. One pass over pe - 48 .. pe (sale days only, LAG per branch x SKU). The caller keeps only menus first sold before r (an
    existing menu); a brand-new menu is never "returning"."""
    import datetime as _dt
    hits: dict = {}
    for bid, sid, day in cur.execute(f"""
        SELECT bid, sid, MAX(day) FROM (
          SELECT d.branch_id bid, d.sku_id sid, d.day day, LAG(d.day) OVER (PARTITION BY d.branch_id, d.sku_id ORDER BY d.day) prev
          FROM sales_sku_daily d {scan} {bjoin}
          WHERE d.day BETWEEN :rs0 AND :pe AND d.qty > 0 {bfilt})
        WHERE day >= :ns AND (prev IS NULL OR julianday(day) - julianday(prev) >= {_SPIKE_GAP_DAYS + 1})
        GROUP BY bid, sid""", q):
        r = _dt.date.fromisoformat(day[:10])
        g0 = r - _dt.timedelta(days=_SPIKE_GAP_DAYS)
        if sum(1 for d in (tdays.get(bid) or ()) if g0 <= d < r) >= _SPIKE_GAP_OPEN:
            hits[(bid, sid)] = r
    if not hits:
        return {}
    out: dict = {}
    sids = sorted({k[1] for k in hits})
    bids = sorted({k[0] for k in hits})
    lo = min(hits.values()).isoformat()
    for bid, sid, day, qq in cur.execute(
            f"SELECT branch_id, sku_id, day, qty FROM sales_sku_daily WHERE branch_id IN ({','.join('?' * len(bids))}) "
            f"AND day BETWEEN ? AND ? AND qty > 0 AND sku_id IN ({','.join('?' * len(sids))})", (*bids, lo, q["pe"], *sids)):
        r = hits.get((bid, sid))
        if r is not None and day[:10] >= r.isoformat():
            out[(bid, sid)] = (r, out.get((bid, sid), (r, 0.0))[1] + (qq or 0))
    return out


# ── DV3 (phone table review 2026-10-25, top-10 #3) · the card by DISH ─────────────────────────────────────────────────────────────
# The chain view is one row per dish (the menu name - every SKU code and branch summed), its NET extra cost (the branches above their
# normal minus the branches below it; a branch that sold none in the period counts its whole normal as a decrease), the branches that
# push it (สาขาที่ดัน, top 3 with ฿), a floor and the share / cumulative % that say where to stop. One branch in scope = the same rows
# for that branch. New menus are grouped per dish the same way, with their own floor.
_DISH_FLOOR_BAHT = 1000          # a dish row is material from ฿1,000 of net extra cost ...
_DISH_FLOOR_SHARE = 0.1          # ... or from 0.1 % of the scope's food cost of the period
_DISH_FLOOR_PLATES = 20          # a role without cost: from 20 net extra plates
_NEW_FLOOR_PER_DAY = 20          # a new dish is material from 20 plates a day (all its branches) ...
_NEW_FLOOR_BAHT = 1000           # ... or from ฿1,000 of food cost in the period (cost roles)
_DISH_PUSHERS = 3


def _spike_dishes(ents, sc, spk, one_branch, threshold=1.5) -> tuple:
    """ents = [(kind, menu, bid, branch, sid, plates, expected)] = every existing branch x SKU of the scope with a sale in the period or
    in its baseline (new / returning menus and non-food lines excluded). spk = {(bid, sid): the ranked row} (tiers, spike). sc =
    _SpikeCost or None (then not one cost key, PL3). Returns (rows, totals): rows = the dishes above their normal - net extra cost > 0
    (cost view; an uncosted dish above its normal comes last) or net extra plates > 0 - sorted by it, each with its share of the food
    cost (cost_share), the cumulative % of the rows above it (cum_pct, of the summed positive dishes) and material (the floor);
    totals = the up / down / net sums over EVERY dish (the net = increases minus decreases)."""
    acc: dict = {}
    for kind, menu, bid, branch, sid, pq, exp in ents:
        key = (kind, _spike_norm(menu))          # PR-SK: one row per dish, whatever the SKU's spelling
        a = acc.get(key)
        if a is None:
            a = acc[key] = {"kind": kind, "menu": menu, "names": {}, "pq": 0.0, "exp": 0.0, "net": 0.0, "rev": 0.0, "costed": False,
                            "br": {}, "keys": [], "spike": set()}
        a["names"][menu] = a["names"].get(menu, 0.0) + pq
        uc = sc.unit_of(bid, sid) if sc is not None else 0.0
        ex = pq - exp
        a["pq"] += pq
        a["exp"] += exp
        a["net"] += ex * uc
        a["rev"] += sc.rev.get((bid, sid), 0.0) if sc is not None else 0.0
        a["costed"] = a["costed"] or uc > 0
        b = a["br"].setdefault(branch, [0.0, 0.0, 0.0])      # [excess plates, net ฿, plates]
        b[0] += ex
        b[1] += ex * uc
        b[2] += pq
        a["keys"].append((bid, sid))
        r = spk.get((bid, sid))
        if r is not None and r.get("spike"):
            a["spike"].add(branch)
    cost = sc is not None
    tot = {"up_excess": 0.0, "down_excess": 0.0}
    if cost:
        tot.update({"up_cost": 0.0, "down_cost": 0.0})
    rows = []
    for a in acc.values():
        ex = a["pq"] - a["exp"]
        tot["up_excess" if ex > 0 else "down_excess"] += ex
        if cost:
            tot["up_cost" if a["net"] > 0 else "down_cost"] += a["net"]
        byc = cost and a["costed"]
        if not (a["net"] > 0 if byc else ex > 0):
            continue
        x = a["pq"] / a["exp"] if a["exp"] > 0 else None
        # PR-SK: the name = the spelling that sold the most plates; merged = the other spellings folded into the row
        name = max(a["names"].items(), key=lambda kv: (kv[1], kv[0]))[0]
        row = {"menu": name, "kind": a["kind"], "branches": sum(1 for b in a["br"].values() if b[2] > 0),
               "today": round(a["pq"]), "expected": round(a["exp"]), "excess": round(ex),
               "x": round(x, 2) if x is not None else None, "pct": round((x - 1) * 100) if x is not None else None,
               # the พุ่ง chip: the dish's OWN plates >= threshold x its normal (>= 5 plates), not one branch's; spike_branches = those
               "spike": (x >= threshold and a["pq"] >= 5) if x is not None else bool(a["spike"]),
               "spike_branches": len(a["spike"]), "_net": a["net"] if cost else ex, "_ex": ex}
        if len(a["names"]) > 1:
            row["merged"] = sorted(n for n in a["names"] if n != name)
        if not one_branch:
            # PR-SK: a normal under 5 plates over the whole chain is no normal (the one-branch rows carry the ranked row's flag)
            row["thin_base"] = a["exp"] < _SPIKE_THIN_EXPECTED
            i = 1 if byc else 0
            push = sorted(((n, b) for n, b in a["br"].items() if b[i] > 0), key=lambda kv: (-kv[1][i], kv[0]))
            row["pushers"] = [{"branch": n, "excess": round(b[0]), **({"extra_cost": round(b[1], 2)} if cost else {})}
                              for n, b in push[:_DISH_PUSHERS]]
            row["pushers_more"] = max(0, len(push) - _DISH_PUSHERS)
        else:
            # one branch: the branch's own ranked row (its tiers, per-head figures) when the dish is one SKU there
            r = spk.get(a["keys"][0]) if len(a["keys"]) == 1 else None
            if r is not None:
                for k in ("tiers", "tier_note", "tier_to", "tier_rest", "rate", "normal_rate", "basis", "customers", "thin_base"):
                    if k in r:
                        row[k] = r[k]
        if cost:
            row.update({"net_cost": round(a["net"], 2), "cost_share": sc.share(a["net"]), "net_pp": sc.pp(a["net"]),
                        "uncosted": not a["costed"]})
            # PR-SK (owner 2026-10-29) · a PAID dish (à la carte, delivery, takeaway) sells its extra plates: the price side.
            # price = the period's ex-VAT revenue per plate of the dish; extra_revenue = net extra plates x price;
            # extra_gp = extra_revenue - the net extra cost. A buffet dish has no price (its revenue is the package's).
            if a["kind"] == "alc" and a["costed"] and a["pq"] > 0 and a["rev"] > 0:
                price = a["rev"] / a["pq"]
                xr = ex * price
                row.update({"price": round(price, 2), "extra_revenue": round(xr, 2), "extra_gp": round(xr - a["net"], 2)})
        rows.append(row)
    if cost:
        rows.sort(key=lambda r: (r["uncosted"], -r["_net"], -r["_ex"], r["menu"]))
        base = tot["up_cost"]
    else:
        rows.sort(key=lambda r: (-r["_ex"], r["menu"]))
        base = tot["up_excess"]
    run = 0.0
    for r in rows:
        if cost and r["uncosted"]:
            r["cum_pct"], r["material"] = None, False
        else:
            run += r["_net"]
            r["cum_pct"] = round(run / base * 100, 1) if base > 0 else None
            r["material"] = (r["_net"] >= _DISH_FLOOR_BAHT or (r["cost_share"] or 0) >= _DISH_FLOOR_SHARE) if cost \
                else r["_ex"] >= _DISH_FLOOR_PLATES
        r.pop("_net")
        r.pop("_ex")
    out = {k: round(v, 2) for k, v in tot.items()}
    out["net_excess"] = round(tot["up_excess"] + tot["down_excess"], 2)
    if cost:
        net = tot["up_cost"] + tot["down_cost"]
        out.update({"net_cost": round(net, 2), "net_share": sc.share(net), "net_pp": sc.pp(net)})
    return rows, out


_SPIKE_THIN_EXPECTED = 5         # PR-SK: a chain-wide normal under 5 plates marks the dish row thin_base


def _spike_groups(rows, cost: bool) -> list:
    """PR-SK (owner 2026-10-29) · the list rolled up by high-value ingredient (backend/ingredient_groups.GROUPS, the same
    match as the High Value Product Monitor): per group the net extra cost (cost view; the uncosted rows add nothing) or the
    net extra plates of the listed dishes that belong to it, the dishes counted and the group's share of the list's sum
    (a mixed dish counts in each of its groups, so the shares can pass 100 together). Groups with nothing are left out;
    sorted by the figure. The card's one line above the table, each entry a jump to the monitor of that ingredient."""
    import ingredient_groups as _ig
    val = (lambda r: 0.0 if r.get("uncosted") else (r.get("net_cost") or 0.0)) if cost else (lambda r: float(r.get("excess") or 0))
    base = sum(val(r) for r in rows)
    acc: dict = {}
    for r in rows:
        v = val(r)
        if v <= 0:
            continue
        for slug in _ig.match(r["menu"]):
            g = acc.setdefault(slug, [0.0, 0])
            g[0] += v
            g[1] += 1
    key = "net_cost" if cost else "excess"
    out = [{"slug": s, "label": _ig.LABELS[s], key: round(v, 2) if cost else round(v), "dishes": n,
            "share_pct": round(v / base * 100, 1) if base > 0 else None} for s, (v, n) in acc.items()]
    out.sort(key=lambda g: (-g[key], g["label"]))
    return out


def _spike_new_dishes(nms, cost, one_branch) -> list:
    """DV3 · the new-menu rows (_spike_new_menus, with the private _q) grouped per dish: branches selling it, plates, plates a day (summed
    over its branches), plates per 100 customers (over the rows with customers), the food cost of the period (cost roles), how many of
    its branches it came back at (returning), the trend (latest 7 days vs the first 7, plates a day summed over the branches that have
    one), the top 3 branches by plates. Sorted by the period's cost (cost roles; an uncosted dish after the costed ones) or by plates;
    material = >= 20 plates a day or >= ฿1,000 of food cost."""
    acc: dict = {}
    for m in nms:
        a = acc.get((m["kind"], m["menu"]))
        if a is None:
            a = acc[(m["kind"], m["menu"])] = {"menu": m["menu"], "kind": m["kind"], "qty": 0.0, "per_day": 0.0, "rq": 0.0, "cust": 0.0,
                                               "cost": 0.0, "costed": False, "ret": 0, "first": m["first_sold"], "age": 0,
                                               "t1": 0.0, "t2": 0.0, "tn": 0, "br": []}
        a["qty"] += m["_q"]
        a["per_day"] += m["per_day"] or 0.0
        if m.get("customers"):
            a["rq"] += m["_q"]
            a["cust"] += m["customers"]
        if cost:
            a["cost"] += m.get("period_cost") or 0.0
            a["costed"] = a["costed"] or (m.get("unit_cost") or 0) > 0
        a["ret"] += 1 if m.get("returning") else 0
        a["first"] = min(a["first"], m["first_sold"])
        a["age"] = max(a["age"], m["age_days"])
        t = m.get("trend")
        if t and t.get("first_per_day") is not None and t.get("latest_per_day") is not None:
            a["t1"] += t["first_per_day"]
            a["t2"] += t["latest_per_day"]
            a["tn"] += 1
        a["br"].append(m)
    rows = []
    for a in acc.values():
        brs = sorted(a["br"], key=lambda m: (-m["_q"], m["branch"]))
        row = {"menu": a["menu"], "kind": a["kind"], "branches": len(brs), "qty": round(a["qty"]), "per_day": round(a["per_day"], 1),
               "rate": round(a["rq"] / a["cust"] * _SPIKE_PER, 1) if a["cust"] > 0 else None, "returning": a["ret"],
               "first_sold": a["first"], "age_days": a["age"],
               "trend_pct": round((a["t2"] / a["t1"] - 1) * 100) if a["tn"] and a["t1"] > 0 else None,
               "top": [{"branch": m["branch"], "qty": round(m["_q"]), "returning": bool(m.get("returning"))} for m in brs[:_DISH_PUSHERS]],
               "more": max(0, len(brs) - _DISH_PUSHERS)}
        if one_branch and brs:
            m = brs[0]
            row.update({"first_sold_th": m.get("first_sold_th"), **{k: m[k] for k in ("returned_on", "first_sold_ever") if k in m}})
        if cost:
            row.update({"period_cost": round(a["cost"], 2), "uncosted": not a["costed"]})
        row["material"] = a["per_day"] >= _NEW_FLOOR_PER_DAY or (cost and a["cost"] >= _NEW_FLOOR_BAHT)
        rows.append(row)
    if cost:
        rows.sort(key=lambda r: (r["uncosted"], -r["period_cost"], -r["qty"], r["menu"]))
    else:
        rows.sort(key=lambda r: (-r["qty"], r["menu"]))
    return rows


def _menu_spike_period(con, period, focus_arg, date_from, date_to, bfilt, params, limit, threshold, with_cost=False,
                       top=_SPIKE_TOP, base_from=None, base_to=None):
    """Day / week / MTD / custom-range spike, per head and weekday-fair (memory rule 'MTD weekday-fair baseline';
    PR-ST owner 2026-10-25: the day mode too). Per branch x SKU: plates = sum of qty over the period; customers = the
    row's basis over the period's trading days (see _SPIKE_PER above); expected = sum over weekdays of (the SKU's
    baseline plates of that weekday / the branch's baseline customers of that weekday) x the period's customers of that
    weekday, over the 3 weeks before the period (trading days, zeros included; a weekday with no baseline customers uses
    the whole baseline). rate = plates / customers x 100, normal_rate = expected / customers x 100, x = plates / expected.
    PR-RK (owner 2026-10-25, "เรียงผิด"): spikes = the RANKED list of existing menus above their normal - every row with
    excess = plates - expected >= _SPIKE_RANK_MIN_EXCESS (3) and x >= _SPIKE_RANK_MIN_X (1.05), plus every row of the old rule;
    spike: true marks the old rule (x >= threshold, plates >= 5, the SKU sold on >= 5 baseline days). An existing menu sold on
    < 5 baseline days is listed from 20 plates with thin_base: true (and spike: true) when x >= threshold, or when that baseline
    is 0 (x None). spike_count = the rows with spike: true (the card's เมนูพุ่ง tile); count = all ranked rows. A NEW menu
    (anchor = period end, anchor - first_sold < 28 days) is never in it: it goes to new_menus. Non-food lines
    (rules.non_food_sql: packages, fees, penalties, deposits, coupons, service charge) are in no list. Listed rows carry
    the tier split (_spike_tiers). Sorted by excess = plates - expected.

    PR-SC (owner 2026-10-25) · with_cost (a role holding cost.analysis or profit.pnl): each row also carries unit_cost
    (the month-weighted dish cost of _SpikeCost), extra_cost = max(0, excess) x unit_cost, cost_share = extra_cost / the
    scope's food cost of the period x 100 and extra_cost_per_head_pp = extra_cost / its net revenue x 100 (percentage
    points of food-cost %); the list is then sorted by extra_cost, a row without a cost last (uncosted: true, or
    package: true for a buffet tier package code, never costed); new menus carry unit_cost and period_cost = plates x
    unit_cost. Without with_cost not one cost key is in the answer (PL3) and the order stays by excess plates.
    top = how many rows the card shows first (top_* = the sums over those rows).

    PR-TD (owner 2026-10-25) · base_from / base_to = a chosen comparison period instead of "ปกติ" (the 3 weeks before): the normal
    is that period's per-head rate over the whole of it (plates / customers x 100; per tier likewise), so periods of different
    lengths compare; expected = base rate x the period's customers, x = rate / base rate, the thin rule (< 5 days sold) counts the
    base's days. The base must end before the period starts, <= 92 days, finished days only (400 in formal Thai otherwise).
    spikes_plates / plates_count (PR-TD, owner 2026-10-25, "แบบเดิม") = the same rule on raw plates - period plates vs the weekday-fair normal
    plates of the base (the chosen base: its plates per trading day x the period's trading days), >= 1.5x and >= 5 plates (thin from 20),
    existing menus only (new and returning menus excluded the same way), sorted by +จาน; no per-head, tier or cost key; in_per_head (PR-SX) =
    the same branch x SKU is also in spikes with spike: true (PR-RK), plates_only_count = the rows without it. A buffet package line is in no list (PR-SX).
    base = {kind: normal | range, from, to}. summary = the strip of the card: customers of the period and of the base per day
    (customers_vs_base_pct), the tier with the largest summed extra plates over the listed rows (tier_top), the extra plates of
    the first `top` rows (top_excess)."""
    import datetime as _dt
    from rules import TH_TODAY_SQL
    # the last finished day; MAX through the (branch, day) index, not a table scan
    try:
        latest_s = con.execute(
            f"SELECT MAX(day) FROM sales_sku_daily WHERE branch_id IN (SELECT branch_id FROM branches) "
            f"AND day < {TH_TODAY_SQL}").fetchone()[0]
        if period == "day" and focus_arg:
            # the day mode keeps its reach: an explicit date may be the day still trading (as /api/sales/daily)
            latest_s = con.execute("SELECT MAX(day) FROM sales_sku_daily").fetchone()[0] or latest_s
    except Exception:
        return {"date": None, "period": period, "spikes": [], "new_menus": [], "new_count": 0, "note": "no daily sku data"}
    if not latest_s:
        return {"date": None, "period": period, "spikes": [], "new_menus": [], "new_count": 0}
    latest = _dt.date.fromisoformat(latest_s[:10])
    anchor = _spike_iso(focus_arg, "วันที่") if focus_arg else latest
    if anchor > latest:
        anchor = latest
    ps, pe = (anchor, anchor) if period == "day" else _spike_window(period, anchor, date_from, date_to, latest)
    bs, be = ps - _dt.timedelta(days=_SPIKE_BASE_DAYS), ps - _dt.timedelta(days=1)
    pooled = bool(base_from or base_to)
    if pooled:
        bs, be = _spike_base(base_from, base_to, ps, latest)
    ns = pe - _dt.timedelta(days=_SPIKE_NEW_DAYS - 1)       # first_sold on/after ns = a new menu
    rs0 = ns - _dt.timedelta(days=_SPIKE_GAP_DAYS)          # PR-TD: the gap before a return on/after ns starts here at the earliest
    ts0 = min(bs, rs0)
    q = {**params, "ps": ps.isoformat(), "pe": pe.isoformat(), "bs": bs.isoformat(), "be": be.isoformat(),
         "ts0": ts0.isoformat(), "ns": ns.isoformat(), "rs0": rs0.isoformat()}
    # Speed (live-size DB, MTD all branches): a whole-chain window reads most of the
    # table, so a plain scan beats ~200k (branch, day) index lookups; one branch keeps
    # the index. Rows come back as tuples. Weekday key = CAST(julianday(day) AS INT) % 7
    # (cheaper than strftime); Python uses the same key (_jd7). Service-charge lines are
    # dropped as v_sales_sku_daily does (core._REV_VIEWS); the baseline only feeds SKUs
    # found in the period, so it needs no second filter.
    one_branch = "b.name=" in bfilt
    scan = "" if one_branch else "NOT INDEXED"
    bjoin = "JOIN branches b ON b.branch_id=d.branch_id" if bfilt else ""
    cur = con.cursor()
    cur.row_factory = None
    # 1 - the period, one pass: qty per branch x SKU
    prow = cur.execute(f"""
        SELECT a.bid, b.name, s.description, s.sku_code, a.sid, a.q FROM (
          SELECT d.branch_id bid, d.sku_id sid, SUM(d.qty) q
          FROM sales_sku_daily d {scan} {bjoin}
          WHERE d.day BETWEEN :ps AND :pe {bfilt} GROUP BY d.branch_id, d.sku_id) a
        JOIN branches b ON b.branch_id=a.bid JOIN skus s ON s.sku_id=a.sid
        WHERE a.q > 0 AND NOT {non_food_sql("s.sku_code", "s.description")}""", q).fetchall()
    # PR-SX (owner 2026-10-25, "แก้เลย"): a buffet package line (rules.tier_code_sql - BUFFET*, BUFFETCHILD, the legacy tier codes) is the
    # ticket, not a dish: never a spike, a new menu or a raw-plates row, so never in the counts, the strip or the CSV either.
    # PR-RK (owner 2026-10-25): every other non-food line too - fees, penalties ("(OT) ค่าปรับบุฟเฟ่ต์ ..."), deposits, coupons, the
    # service charge - through the one predicate rules.non_food_sql (twin rules.is_non_food_line)
    # 2 - the baseline, one pass: qty and days sold per branch x SKU x weekday
    #     (one row per branch x day x SKU, so SUM(qty > 0) = days sold)
    base: dict = {}
    for bid, sid, w, qq, nn in cur.execute(f"""
        SELECT d.branch_id, d.sku_id, CAST(julianday(d.day) AS INTEGER) % 7 w, SUM(d.qty), SUM(d.qty > 0)
        FROM sales_sku_daily d {scan} {bjoin}
        WHERE d.day BETWEEN :bs AND :be {bfilt}
        GROUP BY d.branch_id, d.sku_id, w""", q):
        e = base.get((bid, sid))
        if e is None:
            e = base[(bid, sid)] = [0.0] * 8          # qty per weekday key 0..6, [7] = days sold
        e[w] += qq or 0
        e[7] += nn or 0
    # 3 - the days each branch traded, both windows and the 28 days of the new-menu rule (covering index)
    tdays: dict = {}
    for bid, day in cur.execute(f"""
        SELECT d.branch_id, d.day FROM sales_sku_daily d {bjoin}
        WHERE d.day BETWEEN :ts0 AND :pe {bfilt} GROUP BY d.branch_id, d.day""", q):
        tdays.setdefault(bid, set()).add(_dt.date.fromisoformat(day[:10]))
    # 4 - customers per branch x day (all / buffet per tier), the same windows
    cust = _SpikeCust(con, set(tdays), ts0, pe)
    days = (pe - ps).days + 1
    tbase = {bid: [d for d in ds if bs <= d <= be] for bid, ds in tdays.items()}
    tper = {bid: [d for d in ds if ps <= d <= pe] for bid, ds in tdays.items()}
    # per branch x basis: weight of each weekday key = the period's customers of that weekday / the baseline's customers
    # of it; weekdays without baseline customers fall back to the whole baseline (their period customers / all baseline
    # customers). With the "days" basis (one per trading day) this is the plate standard of PR-SP exactly.
    wts: dict = {}

    def weights(bid, basis):
        k = (bid, basis)
        if k not in wts:
            cb, cp = [0.0] * 7, [0.0] * 7
            for d in tbase.get(bid, ()):
                cb[_jd7(d)] += cust.of(bid, basis, d)
            for d in tper.get(bid, ()):
                cp[_jd7(d)] += cust.of(bid, basis, d)
            tot = sum(cb)
            if pooled:                    # a chosen base: its per-head rate over the whole of it, not per weekday
                wts[k] = ([sum(cp) / tot if tot else 0.0] * 7, sum(cp))
                return wts[k]
            fb = sum(cp[w] for w in range(7) if not cb[w]) / tot if tot else 0.0
            wts[k] = ([(cp[w] / cb[w] if cb[w] else 0.0) + fb for w in range(7)], sum(cp))
        return wts[k]

    bases: dict = {}

    def basis_of(bid, kind):
        k = (bid, kind)
        if k not in bases:
            bases[k] = _spike_basis(cust, kind, bid, [(bs, be, tbase.get(bid, [])), (ps, pe, tper.get(bid, []))])
        return bases[k]

    spikes = []
    plates: list = []                       # PR-TD · แบบเดิม (raw plates)
    fsm = _first_sold_map(con, {r[0] for r in prow}) if prow else {}
    rets = _spike_returns(cur, scan, bjoin, bfilt, q, tdays) if prow else {}
    newc = []
    dents: list = []                        # DV3 · (kind, menu, bid, branch, sid, plates, expected) of every existing branch x SKU
    for bid, branch_name, desc, code, sid, pq in prow:
        pq = pq or 0
        fs = fsm.get((bid, sid))
        first = _dt.date.fromisoformat(fs) if fs else None
        kind = _spike_kind(desc)
        name = _spike_name(desc, code)
        if first is not None and (pe - first).days < _SPIKE_NEW_DAYS:      # a new menu: its own list, never a spike
            if pq >= 1:
                newc.append((bid, branch_name, name, code, sid, pq, first, kind))
            continue
        back = rets.get((bid, sid))
        if back is not None and first is not None and first < back[0]:
            # PR-TD: an existing menu back on sale after >= 21 days without one: a new menu again from the day it came back, for 28 days
            # DV1 (review 2026-10-25): the plates are counted over the same window as the divisor (days / customers of
            # max(ps, returned_on)..pe) - a return before the period start keeps the period's plates, not the total since the return
            bq = back[1] if back[0] > ps else pq
            if bq >= 1:
                newc.append((bid, branch_name, name, code, sid, bq, back[0], kind, first))
            continue
        e = base.get((bid, sid))
        n = e[7] if e else 0
        # PR-TD (owner 2026-10-25) · แบบเดิม: the same rule on raw plates (the "days" basis = the weekday-fair plate standard of PR-SP,
        # or the chosen base's plates per trading day), its own list at the bottom of the card
        wd, _ = weights(bid, "days")
        pexp = sum(e[w] * wd[w] for w in range(7)) if e else 0.0
        pthin = n < _SPIKE_THIN_DAYS
        if (pexp > 0 and not pthin and pq / pexp >= threshold and pq >= 5) or \
                (pthin and pq >= _SPIKE_THIN_MIN_QTY and (pexp <= 0 or pq / pexp >= threshold)):
            px = pq / pexp if pexp > 0 else None
            prow_ = {"branch": branch_name, "menu": name, "today": round(pq), "per_day": round(pq / days, 1), "avg": round(pexp, 1),
                     "expected": round(pexp), "x": round(px, 1) if px is not None else None,
                     "pct": round((px - 1) * 100) if px is not None else None, "excess": round(pq - pexp), "is_new": False,
                     "kind": kind, "_ex": pq - pexp, "_k": (bid, sid)}
            if pthin:
                prow_["thin_base"] = True
            plates.append(prow_)
        basis = basis_of(bid, kind)
        wt, cp = weights(bid, basis)
        expected = sum(e[w] * wt[w] for w in range(7)) if e else 0.0
        dents.append((kind, name, bid, branch_name, sid, pq, expected))       # DV3: every existing row, up or down
        thin = n < _SPIKE_THIN_DAYS
        spike = True
        if expected > 0 and not thin:
            x = pq / expected
            # PR-RK (owner 2026-10-25): the list ranks every existing menu above its normal (noise floor: +3 plates and x1.05),
            # so a popular menu 10-20 % up per head that adds the most cost is no longer hidden by the 1.5x gate; spike = the old rule
            spike = x >= threshold and pq >= 5
            if not spike and (x < _SPIKE_RANK_MIN_X or pq - expected < _SPIKE_RANK_MIN_EXCESS):
                continue
        elif thin and pq >= _SPIKE_THIN_MIN_QTY:
            # an existing menu that sold on < 5 baseline days: listed from 20 plates (the old floor), "ปกติ" = the
            # weekday-fair baseline that counts the branch's trading days without a sale as zero (0 -> no x)
            x = pq / expected if expected > 0 else None
            if x is not None and x < threshold:            # a baseline > 0: the same 1.5x rule as every spike
                continue
        else:
            continue
        rated = basis != "days" and cp > 0
        row = {"branch": branch_name, "menu": name, "today": round(pq), "per_day": round(pq / days, 1),
               "avg": round(expected, 1), "expected": round(expected),
               "x": round(x, 1) if x is not None else None, "pct": round((x - 1) * 100) if x is not None else None,
               "excess": round(pq - expected), "is_new": False, "spike": spike,
               "kind": kind, "basis": basis, "customers": round(cp) if rated else None,
               "rate": round(pq / cp * _SPIKE_PER, 1) if rated else None,
               "normal_rate": round(expected / cp * _SPIKE_PER, 1) if rated else None,
               "_bid": bid, "_sid": sid, "_pq": pq, "_exp": expected, "_cp": cp if rated else 0.0}
        if thin:
            row["thin_base"] = True
        spikes.append(row)
    # DV3 · an existing menu sold in the baseline but not once in the period is a decrease of its whole normal (the net per dish)
    seen = {(r[0], r[4]) for r in prow}
    extra = [k for k in base if k not in seen]
    if extra:
        from rules import is_non_food_line
        sids = sorted({k[1] for k in extra})
        sk: dict = {}
        for i in range(0, len(sids), 500):
            ch = sids[i:i + 500]
            for sid, code, desc in cur.execute(f"SELECT sku_id, sku_code, description FROM skus WHERE sku_id IN ({','.join('?' * len(ch))})", ch):
                sk[sid] = (code, desc)
        bn = {bid: nm for bid, nm in cur.execute("SELECT branch_id, name FROM branches")}
        fs2 = _first_sold_map(con, {k[0] for k in extra} | {r[0] for r in prow})
        for bid, sid in extra:
            code, desc = sk.get(sid, (None, None))
            if desc is None or is_non_food_line(code, desc):
                continue
            fs = fs2.get((bid, sid))
            if fs and (pe - _dt.date.fromisoformat(fs)).days < _SPIKE_NEW_DAYS:
                continue
            kind = _spike_kind(desc)
            wt, _cp = weights(bid, basis_of(bid, kind))
            e = base[(bid, sid)]
            expected = sum(e[w] * wt[w] for w in range(7))
            if expected > 0:
                dents.append((kind, _spike_name(desc, code), bid, bn.get(bid, ""), sid, 0.0, expected))
    sc = None
    if with_cost:
        try:
            sc = _SpikeCost(con, ps, pe, bfilt, params)
        except Exception as e:                  # a DB without the cost tables: the plates still answer
            print(f"[menu-spike] cost skipped: {e}")
    if sc is not None:
        sc._fill(sorted({(e[4], sc.month) for e in dents if e[5] <= 0}))      # DV3: the dish cost of the baseline-only rows, one pass
        for s in spikes:
            uc = sc.unit_of(s["_bid"], s["_sid"])
            ex = max(0.0, s["_pq"] - s["_exp"]) * uc
            s.update({"unit_cost": round(uc, 2), "extra_cost": round(ex, 2), "cost_share": sc.share(ex),
                      "extra_cost_per_head_pp": sc.pp(ex), "uncosted": uc <= 0 and s["_sid"] not in sc.pkg,
                      "package": s["_sid"] in sc.pkg, "_ex": ex})
        spikes.sort(key=lambda s: (s["unit_cost"] <= 0, -s["_ex"], -(s["_pq"] - s["_exp"]), s["menu"], s["branch"]))
    else:
        spikes.sort(key=lambda s: (-(s["_pq"] - s["_exp"]), s["menu"], s["branch"]))
    shown = spikes[:limit]
    _spike_tiers(con, shown, cust, tdays, base, ps, pe, bs, be, threshold, pooled=pooled)
    top = max(1, int(top or _SPIKE_TOP))
    cost_out: dict = {}
    if sc is not None:
        tx = sum(s["_ex"] for s in spikes[:top])
        cost_out = {"food_cost_total": round(sc.food_cost, 2), "revenue_total": round(sc.revenue, 2),
                    "revenue_basis": sc.revenue_basis, "top_extra_cost": round(tx, 2), "top_cost_share": sc.share(tx),
                    "top_pp": sc.pp(tx), "uncosted_count": sum(1 for s in spikes if s["uncosted"])}
    # PR-SX (owner 2026-10-25) · a raw-plates row whose menu is also a per-head spike (same branch x SKU) carries in_per_head: true; the
    # card hides those by default, so แบบเดิม lists what spiked only because more customers came (plates_only_count = the rest)
    dishes, dtot = _spike_dishes(dents, sc, {(s["_bid"], s["_sid"]): s for s in spikes}, one_branch, threshold)
    phk = {(s["_bid"], s["_sid"]) for s in spikes if s["spike"]}       # PR-RK: "also per head" = in the ranked list AND spike
    for r in plates:
        r["in_per_head"] = r.pop("_k") in phk
    for s in spikes:
        for k in ("_bid", "_sid", "_pq", "_exp", "_cp", "_ex"):
            s.pop(k, None)
    new_menus = _spike_new_menus(con, newc, pe, ps, tdays, cust)
    if sc is not None:
        for m in new_menus:
            uc = sc.unit_of(m["_bid"], m["_sid"])
            m.update({"unit_cost": round(uc, 2), "period_cost": round(m["_q"] * uc, 2),
                      "uncosted": uc <= 0 and m["_sid"] not in sc.pkg, "package": m["_sid"] in sc.pkg})
    new_dishes = _spike_new_dishes(new_menus, sc is not None, one_branch)
    for m in new_menus:
        for k in ("_bid", "_sid", "_q"):
            m.pop(k, None)
    fh = cust.fact_hi
    # PR-TD · the strip: customers per day of the period vs the base, the tier that adds the most plates, the extra plates of the top rows
    cpd = sum(cust.of(b, "all", d) for b, ds in tper.items() for d in ds)
    cbd = sum(cust.of(b, "all", d) for b, ds in tbase.items() for d in ds)
    bdays = (be - bs).days + 1
    tsum: dict = {}
    for r in shown:
        for g in r.get("tiers") or ():
            if g["excess"] > 0:
                tsum[g["label"]] = tsum.get(g["label"], 0.0) + g["excess"]
    tt = max(tsum.items(), key=lambda kv: (kv[1], kv[0])) if tsum else None
    summary = {"customers": round(cpd), "customers_per_day": round(cpd / days, 1) if cpd else None,
               "base_customers_per_day": round(cbd / bdays, 1) if cbd else None,
               "customers_vs_base_pct": round(((cpd / days) / (cbd / bdays) - 1) * 100, 1) if cpd and cbd else None,
               "tier_top": {"label": tt[0], "excess": round(tt[1])} if tt else None,
               "top_excess": round(sum(max(0, s["excess"]) for s in spikes[:top]))}
    return {**cost_out, "sort": "extra_cost" if sc is not None else "excess", "top": top,
            "cost_view": sc is not None, "date": pe.isoformat(), "period": period, "date_from": ps.isoformat(), "date_to": pe.isoformat(),
            "days": days, "baseline_from": bs.isoformat(), "baseline_to": be.isoformat(),
            "base": {"kind": "range" if pooled else "normal", "from": bs.isoformat(), "to": be.isoformat()}, "summary": summary,
            "latest_day": latest.isoformat(),
            "window_days": _SPIKE_BASE_DAYS, "threshold": threshold, "rate_per": _SPIKE_PER,
            "tier_data_to": fh.isoformat() if fh else None,
            "count": len(spikes), "spike_count": sum(1 for s in spikes if s["spike"]), "spikes": shown,
            "plates_count": len(plates), "plates_only_count": sum(1 for r in plates if not r["in_per_head"]), "spikes_plates": [{k: v for k, v in r.items() if k != "_ex"} for r in
                                                           sorted(plates, key=lambda r: (-r["_ex"], r["menu"], r["branch"]))[:limit]],
            "new_days": _SPIKE_NEW_DAYS, "new_count": len(new_menus), "new_menus": new_menus[:limit],
            # DV3 · the card by dish (see _spike_dishes / _spike_new_dishes); view = chain | branch (one branch in scope)
            "view": "branch" if one_branch else "chain", "dish_totals": dtot,
            "dish_floor": ({"baht": _DISH_FLOOR_BAHT, "share_pct": _DISH_FLOOR_SHARE} if sc is not None else {"plates": _DISH_FLOOR_PLATES}),
            "dish_count": len(dishes), "dish_material_count": sum(1 for r in dishes if r["material"]), "dishes": dishes[:limit],
            # PR-SK · the list by high-value ingredient (_spike_groups) and the paid / buffet split of the dish rows
            "dish_groups": _spike_groups(dishes, sc is not None),
            "dish_kind_count": {"buffet": sum(1 for r in dishes if r["kind"] == "buffet"), "alc": sum(1 for r in dishes if r["kind"] != "buffet")},
            "new_dish_floor": {"per_day": _NEW_FLOOR_PER_DAY, **({"baht": _NEW_FLOOR_BAHT} if sc is not None else {})},
            "new_dish_count": len(new_dishes), "new_dish_material_count": sum(1 for r in new_dishes if r["material"]),
            "new_dishes": new_dishes[:limit]}


@router.get("/api/sales/menu-spike")
def menu_spike(request: Request = None, date: Optional[str] = Query(None), branch: Optional[str] = Query(None),
               group: Optional[str] = Query(None), limit: int = Query(300, ge=1, le=1000),
               threshold: float = Query(1.5, ge=1.0), period: str = Query("day"),
               date_from: Optional[str] = Query(None), date_to: Optional[str] = Query(None),
               top: int = Query(_SPIKE_TOP, ge=1, le=1000),
               base_from: Optional[str] = Query(None), base_to: Optional[str] = Query(None)):
    """Menus whose plates PER HEAD rose, per branch (answers '#5: which menu was abnormally high').

    PR-RK (owner 2026-10-25): `spikes` = every existing menu above its normal ranked by extra_cost (cost roles) or by excess
    plates (other roles) - noise floor +3 plates and x1.05 - each row with `spike` = x >= `threshold` (default 1.5) and
    plates >= 5 (the card's "พุ่ง ≥1.5×" chip); `spike_count` = those rows, `count` = all ranked rows. `threshold` is the
    chip's threshold, no longer a gate. Non-food lines (fees, penalties, deposits, coupons, packages) are in no section.

    PR-SC (owner 2026-10-25): for a role holding cost.analysis or profit.pnl the rows carry the food cost of the extra
    plates (unit_cost, extra_cost, cost_share, extra_cost_per_head_pp, uncosted, package) and are sorted by extra_cost;
    the answer carries food_cost_total, revenue_total, revenue_basis, top_extra_cost / top_cost_share / top_pp (the
    first `top` rows, default 15 = what the card shows before "ดูทั้งหมด"), uncosted_count; new menus carry unit_cost
    and period_cost. Any other role gets none of these keys (never computed, PL3) and the order by excess plates.
    cost_view says which of the two answers this is. A direct call without a request (tests, internal) is the cost view.

    PR-ST (owner 2026-10-25): every period is per head (rate = plates per 100 customers; buffet menus on the branch's
    buffet customers, the rest on all its customers) and weekday-fair against the 3 weeks before the period - see
    _menu_spike_period. period=day = `date` (or the last finished day) vs the same weekday of the 3 weeks before;
    week | mtd | range = Monday of the anchor's week -> anchor / the 1st -> anchor / date_from -> date_to (<= 92 days,
    <= the last finished day). Anchor = `date` or the last finished day; the day still trading is never included in a
    period (an explicit day-mode date may be it).

    Two parts (owner 2026-10-24): `spikes` = EXISTING menus only (`is_new` stays, always false, for old clients; an
    existing menu sold on < 5 baseline days is listed from 20 plates with `thin_base: true`); each row carries kind,
    basis, customers, rate, normal_rate and `tiers` = [{tier, label, plates, customers, rate, normal_rate, excess, x,
    high}] (None + tier_note when the fact tables do not cover the window; tier_to when they cover only part of the
    period; tier_rest = the excess no tier explains). `new_menus` = every NEW menu sold in the period (no threshold),
    new = period end - first day sold at the branch < 28 days - see _spike_new_menus (first_sold, age_days, qty,
    per_day, rate, trend).

    PR-TD (owner 2026-10-25): base_from + base_to = compare with that period instead of ปกติ (see _menu_spike_period); every
    answer carries base {kind, from, to} and summary (the card's strip); new menus that are an existing menu back on sale after
    >= 21 days without one carry returning: true, returned_on, first_sold_ever (_spike_returns).

    DV3 (phone table review 2026-10-25): the card draws `dishes` = one row per dish of the scope (view chain: every branch summed,
    with `pushers` = สาขาที่ดัน; view branch: one branch in scope, the branch's per-head row details) on the NET extra cost (increases
    minus decreases; a branch that sold none in the period counts its normal as a decrease), with cost_share, cum_pct and `material`
    (dish_floor: >= ฿1,000 or >= 0.1 % of the food cost; 20 net plates without cost); dish_totals = up / down / net over every dish;
    `new_dishes` = the new menus per dish (new_dish_floor: >= 20 plates a day or >= ฿1,000). `spikes`, `new_menus` and
    `spikes_plates` stay as they were (the CSV)."""
    from fastapi import HTTPException
    if period not in _SPIKE_PERIODS:
        raise HTTPException(400, "period ต้องเป็น day, week, mtd หรือ range")
    with get_db() as con:
        pfilt = ""
        pparams: dict = {}
        if branch:
            pfilt += " AND b.name=:branch"; pparams["branch"] = branch
        if group:
            pfilt += " AND COALESCE(b.branch_type,'master')=:grp"; pparams["grp"] = group
        with_cost = True
        top = top if isinstance(top, int) else _SPIKE_TOP            # a direct call leaves the Query default
        if request is not None:
            import auth as _auth
            with_cost = bool(_auth.request_modules(request) & _SPIKE_COST_MODULES)
        bf = base_from if isinstance(base_from, str) else None          # a direct call leaves the Query default
        bt = base_to if isinstance(base_to, str) else None
        return _menu_spike_period(con, period, date, date_from, date_to, pfilt, pparams, limit, threshold,
                                  with_cost=with_cost, top=top, base_from=bf, base_to=bt)


@router.get("/api/sales/cost-per-head")
def cost_per_head(date: Optional[str] = Query(None), branch: Optional[str] = Query(None),
                  group: Optional[str] = Query(None), gran: str = Query("day"),
                  kind: Optional[str] = Query(None), pick: Optional[str] = Query(None),
                  date_from: Optional[str] = Query(None), date_to: Optional[str] = Query(None)):
    """#10 — BoM cost per head per branch. PR-PP3: on the one period pattern (periods.windows): day · week · month ·
    year (· range), to date through the last closed day when the period is not finished, compared with the same days
    of the period before AND of the year before (prev_* / ly_*). COGS = the one engine (period_cost.cost_sum): a whole
    month reads the month table, a part month the day-level dish cost; a window with no cost source (a day / week
    before the day table) has cost None ("—", reason in `reasons`), never ฿0 against real covers.
    `date` / `pick` = any day of the period wanted (default: the period of the last closed day)."""
    import periods as _P
    import period_cost as _PC
    k = _P.kind_of(kind or gran, "day")
    tfilt = ["t.source='Easy'", "b.name!='ALL'"]; tp: dict = {}
    scope_filter(tfilt, tp, branch, group)
    twhere = " AND ".join(tfilt)
    with get_db() as con:
        floor = _PC.cost_floor(con)
        w = _P.windows(con, k, twhere, tp, pick=(pick or date), date_from=date_from, date_to=date_to)
        if not w:
            return {"gran": k, "focus": None, "branches": [], "total": {}}

        def side(win, ok):
            if not ok:
                return None, None, None, None
            tot, why = _PC.cost_sum(con, *win, branch, group, floor)
            byb = _PC.cost_sum(con, *win, branch, group, floor, by_branch=True)[0] if tot else None
            cust = {r["branch"]: r["c"] or 0 for r in con.execute(
                f"""SELECT b.name branch, SUM(t.customer_count) c FROM sales_transaction t JOIN branches b ON b.branch_id=t.branch_id
                    WHERE {twhere} AND date(t.sold_at) BETWEEN :_s AND :_e GROUP BY b.name""",
                {**tp, "_s": win[0], "_e": win[1]}).fetchall()}
            return tot, byb, cust, why

        cur, bc, cc, why_c = side(w["cur"], True)
        prv, bp, _pcust, why_p = side(w["prev"], w["has_prev"])
        ly, bl, _lcust, why_l = side(w["ly"], w["has_ly"])
    reasons = _P.reasons(w)
    if why_p and "prev" not in reasons: reasons["prev"] = why_p
    if why_l and "ly" not in reasons: reasons["ly"] = why_l
    if why_c: reasons["cur"] = why_c
    cost_known = cur is not None
    pctf = lambda c, b: _P.pct(c, b) if (c is not None and b) else None   # noqa: E731
    ppf = lambda c, b: _P.pp(c, b) if (c is not None and b is not None) else None   # noqa: E731

    def _r1(v):
        return round(v, 1) if v is not None else None

    def row(nm, c, cu, p_, y_):
        c = dict(c or {}); p_ = dict(p_) if p_ else None; y_ = dict(y_) if y_ else None
        for o in (c, p_, y_):                      # the page prints food-cost % with 1 decimal, as before
            if o and "food_cost_pct" in o:
                o["food_cost_pct"] = _r1(o["food_cost_pct"])
        ch = c.get("cost_per_head") if cost_known else None
        pch = (p_ or {}).get("cost_per_head"); lch = (y_ or {}).get("cost_per_head")
        return {"branch": nm, "customers": cu, "cogs": c.get("cogs", 0) if cost_known else 0,
                "revenue": c.get("revenue", 0) if cost_known else 0,
                "cost_per_head": ch, "prev_cost_per_head": pch, "ly_cost_per_head": lch,
                "delta_pct": pctf(ch, pch), "ly_delta_pct": pctf(ch, lch),
                "rev_per_head": c.get("rev_per_head") if cost_known else None,
                "cost_pct": c.get("food_cost_pct") if cost_known else None,
                "prev_cost_pct": (p_ or {}).get("food_cost_pct"), "ly_cost_pct": (y_ or {}).get("food_cost_pct"),
                "cost_pct_prev_pp": ppf(c.get("food_cost_pct") if cost_known else None, (p_ or {}).get("food_cost_pct")),
                "cost_pct_ly_pp": ppf(c.get("food_cost_pct") if cost_known else None, (y_ or {}).get("food_cost_pct")),
                "gp_per_head": c.get("gp_per_head") if cost_known else None}
    out = [row(nm, (bc or {}).get(nm), cc.get(nm, 0), (bp or {}).get(nm), (bl or {}).get(nm))
           for nm in sorted(set(cc) | set(bc or {}))]
    out.sort(key=lambda r: -(r["cost_pct"] if r["cost_pct"] is not None else -1))
    total = row(None, cur, sum(cc.values()), prv, ly)
    total.pop("branch")
    if not cost_known:
        total["cogs"] = total["revenue"] = None
    return {"gran": k, "focus": w["cur"][1], "cost_known": cost_known, "cost_from": floor,
            "period": _P.public(w) and dict(_P.public(w), reasons={x: v for x, v in reasons.items() if x in ("prev", "ly")}),
            "reasons": reasons,
            "cur_range": list(w["cur"]), "prev_range": list(w["prev"]), "ly_range": list(w["ly"]),
            "branches": out, "total": total}


@router.get("/api/overall/latest-month-mtd")
def latest_month_mtd(request: Request, branch: Optional[str] = Query(None), group: Optional[str] = Query(None)):
    """Fair month-to-date comparison for the latest (possibly incomplete) month:
    current month 1→latest-day vs previous month 1→same-day. Returns revenue (ex-VAT),
    customers, discount, and BoM COGS for both periods. COGS via our BoM only (iron rule)."""
    import datetime as _dt, calendar as _cal
    cogs_expr = _unified_cogs_expr("ssm")
    tfilt = ["t.source='Easy'", "b.name!='ALL'"]; tp: dict = {}
    scope_filter(tfilt, tp, branch, group)
    twhere = " AND ".join(tfilt)
    dbf = " AND b.name=:branch" if branch else ""
    # fix A2: propagate group filter into the COGS query so it matches the revenue query scope
    _gcond_list2: list = []; _gcond_params2: dict = {}
    _group_filter(_gcond_list2, _gcond_params2, group)
    gcond2 = (" AND " + " AND ".join(_gcond_list2)) if _gcond_list2 else ""
    dbp = ({"branch": branch} if branch else {})
    dbp.update(_gcond_params2)

    with get_db() as con:
        # PR-PP2: the window ends on the scope's last CLOSED day (periods.anchor) - the day still trading never enters
        # a to-date window (MAX(sold_at) was today after the noon sync)
        import periods as _P
        maxday = _P.anchor(con, twhere, tp)
        if not maxday:
            return {"available": False}
        y, m, d = (int(x) for x in maxday.split("-"))
        last_dom = _cal.monthrange(y, m)[1]
        partial = d < last_dom
        cur_s = _dt.date(y, m, 1); cur_e = _dt.date(y, m, d)
        pidx = y * 12 + (m - 1) - 1; py, pm = pidx // 12, pidx % 12 + 1
        prev_s = _dt.date(py, pm, 1)
        prev_e = _dt.date(py, pm, min(d, _cal.monthrange(py, pm)[1]))

        def agg(s, e):
            r = con.execute(
                f"""SELECT ROUND({net_revenue_sum('t')},2) rev, SUM(t.customer_count) cust,
                    ROUND(SUM(t.discount),2) disc, COUNT(*) bills
                    FROM sales_transaction t JOIN branches b ON b.branch_id=t.branch_id
                    WHERE {twhere} AND date(t.sold_at) BETWEEN :s AND :e""",
                {**tp, "s": s.isoformat(), "e": e.isoformat()}).fetchone()
            co = con.execute(
                f"""SELECT ROUND(SUM({cogs_expr}),2) cogs
                    FROM (SELECT branch_id, sku_id, qty, 'Easy' AS source, substr(day, 1, 7) AS period_month FROM v_sales_sku_daily
                          WHERE day BETWEEN :s AND :e) ssm
                    JOIN branches b ON b.branch_id=ssm.branch_id
                    WHERE b.name!='ALL'{dbf}{gcond2}""",
                {"s": s.isoformat(), "e": e.isoformat(), **dbp}).fetchone()
            rev = r["rev"] or 0; cogs = co["cogs"] or 0
            return {"revenue_ex_vat": rev, "customers": r["cust"] or 0, "discount": r["disc"] or 0,
                    "bills": r["bills"] or 0, "cogs": cogs, "gross_profit": round(rev - cogs, 2)}

        cur = agg(cur_s, cur_e); prev = agg(prev_s, prev_e)
        # COGS window is rolling — flag if prev period predates available daily-sku data
        mindd = con.execute("SELECT MIN(day) FROM v_sales_sku_daily").fetchone()[0]
        cogs_ok = bool(mindd) and mindd <= prev_s.isoformat()
    import auth as _auth
    if not (_auth.request_modules(request) & {"cost.analysis", "profit.pnl"}):
        # PL3: a role with no cost or profit module gets the sales side only - never the cost of goods or the gross profit
        for _side in (cur, prev):
            _side["cogs"] = None; _side["gross_profit"] = None
        cogs_ok = False
    return {"available": True, "partial": partial, "cutoff_day": d, "days_in_month": last_dom,
            "cur_month": f"{y:04d}-{m:02d}", "prev_month": f"{py:04d}-{pm:02d}",
            "cur_range": [cur_s.isoformat(), cur_e.isoformat()],
            "prev_range": [prev_s.isoformat(), prev_e.isoformat()],
            "cur": cur, "prev": prev, "cogs_comparable": cogs_ok}




# ---------------------------------------------------------------------------
# /api/summary   -  single-call KPI card data
# ---------------------------------------------------------------------------
@router.get("/api/summary")
def summary(
    month:      Optional[str] = Query(None),
    month_from: Optional[str] = Query(None),
    month_to:   Optional[str] = Query(None),
    date_from:  Optional[str] = Query(None),   # YYYY-MM-DD — daily/weekly window
    date_to:    Optional[str] = Query(None),   # YYYY-MM-DD — daily/weekly window
    branch:     Optional[str] = Query(None),
    group:      Optional[str] = Query(None),
):
    """
    High-level KPIs for the header cards:
    total revenue, transaction count, avg ticket, customer count.
    If month is omitted, returns all-time totals.

    When date_from/date_to are supplied (daily/weekly granularity), the KPIs are
    computed at day resolution straight from sales_transaction (Easy-only — the
    only day-resolved source). The monthly tables have no sub-month resolution,
    so they cannot answer a Day/Week window.
    """
    # ── date-window mode (daily/weekly) — transaction-level, Easy-only ──────
    if date_from or date_to:
        filters = ["b.name != 'ALL'", "st.source = 'Easy'"]
        params: dict = {}
        if branch:
            filters.append("b.name = :branch")
            params["branch"] = branch
        _group_filter(filters, params, group)
        if date_from:
            filters.append("DATE(st.sold_at) >= :date_from")
            params["date_from"] = date_from
        if date_to:
            filters.append("DATE(st.sold_at) <= :date_to")
            params["date_to"] = date_to
        where = "WHERE " + " AND ".join(filters)
        with get_db() as con:
            r = con.execute(
                f"""
                SELECT SUM(st.total_inc_vat)   AS rev_inc,
                       {net_revenue_sum('st')} AS rev_net,
                       COUNT(*)                AS tx,
                       SUM(st.customer_count)  AS cust
                FROM sales_transaction st
                JOIN branches b ON b.branch_id = st.branch_id
                {where}
                """,
                params,
            ).fetchone()
        rev_inc = r["rev_inc"] or 0.0
        tx      = r["tx"] or 0
        cust    = r["cust"] or 0
        rev_ex  = r["rev_net"] or 0.0      # VAT and the 5 % service charge are never revenue (= month mode)
        return {
            "revenue":         round(rev_ex, 2),
            "revenue_ex_vat":  round(rev_ex, 2),
            "revenue_inc_vat": round(rev_inc, 2),
            "tx_count":        tx,
            "customer_count":  cust,
            "avg_ticket":      round(rev_ex / tx, 2) if tx else 0,
        }

    filters_sku = ["b.name != 'ALL'", "sm.source = 'Easy'"]   # fix B4
    filters_tx  = ["b.name != 'ALL'"]
    params_sku: dict = {}
    params_tx:  dict = {}

    if branch:
        filters_sku.append("b.name = :branch")
        filters_tx.append("b.name = :branch")
        params_sku["branch"] = branch
        params_tx["branch"]  = branch
    _group_filter(filters_sku, params_sku, group)
    _group_filter(filters_tx,  params_tx,  group)
    _month_range_filter(filters_sku, params_sku, month_from, month_to, month, col="sm.period_month")
    _month_range_filter(filters_tx,  params_tx,  month_from, month_to, month, col="strftime('%Y-%m', st.sold_at)")

    where_sku = "WHERE " + " AND ".join(filters_sku)
    where_tx  = "WHERE " + " AND ".join(filters_tx)

    # month_summary filter (Easy — used as a revenue/tx fallback for any
    # branch×month missing from sku_monthly / sales_transaction)
    filters_ms = ["b.name != 'ALL'"]
    params_ms: dict = {}
    if branch:
        filters_ms.append("b.name = :branch")
        params_ms["branch"] = branch
    _group_filter(filters_ms, params_ms, group)
    _month_range_filter(filters_ms, params_ms, month_from, month_to, month, col="sms.period_month")
    where_ms = "WHERE " + " AND ".join(filters_ms)

    with get_db() as con:
        # SKU-level revenue per (branch, month) — primary, preferred when available
        sku_rev_rows = con.execute(
            f"""
            SELECT b.name AS branch, sm.period_month AS m,
                   SUM(sm.total_inc_vat) AS rev_inc,
                   SUM(sm.subtotal_ex_vat) AS rev_ex
            FROM v_sales_sku_monthly sm
            JOIN branches b ON b.branch_id = sm.branch_id
            {where_sku}
            GROUP BY b.name, sm.period_month
            """,
            params_sku,
        ).fetchall()

        # Month summary as FALLBACK for branches/months not in sku_monthly
        ms_rev_rows = con.execute(
            f"""
            SELECT b.name AS branch, sms.period_month AS m,
                   SUM(sms.net_sales) AS net,
                   SUM(sms.transaction_count) AS tx,
                   SUM(sms.customer_count) AS cust
            FROM sales_month_summary sms
            JOIN branches b ON b.branch_id = sms.branch_id
            {where_ms}
            GROUP BY b.name, sms.period_month
            """,
            params_ms,
        ).fetchall()

        # Easy transactions (Easy-only)
        easy_tx_rows = con.execute(
            f"""
            SELECT b.name AS branch, strftime('%Y-%m', st.sold_at) AS m,
                   COUNT(*) AS tx, SUM(customer_count) AS cust,
                   SUM(total_inc_vat) AS rev_inc
            FROM sales_transaction st
            JOIN branches b ON b.branch_id = st.branch_id
            {where_tx}
            GROUP BY b.name, strftime('%Y-%m', st.sold_at)
            """,
            params_tx,
        ).fetchall()

    # Build per-(branch, month) merged map
    sku_map = {(r["branch"], r["m"]): {"rev_inc": r["rev_inc"] or 0, "rev_ex": r["rev_ex"] or 0} for r in sku_rev_rows}
    easy_tx_map = {(r["branch"], r["m"]): {"tx": r["tx"] or 0, "cust": r["cust"] or 0, "rev_inc": r["rev_inc"] or 0} for r in easy_tx_rows}

    # Aggregate totals
    total_rev_inc = 0.0
    total_rev_ex  = 0.0
    total_tx      = 0
    total_cust    = 0

    # Track (branch, month) we've covered to avoid double-count
    covered = set()
    for (br, m), v in sku_map.items():
        total_rev_inc += v["rev_inc"]
        total_rev_ex  += v["rev_ex"]
        covered.add((br, m))

    for r in ms_rev_rows:
        k = (r["branch"], r["m"])
        if k in covered:
            continue  # sku_monthly already counted this branch×month
        # net_sales is already VAT-EXCLUSIVE (ingest hard rule); reconstruct inc-VAT for the inc total (fix: B1)
        _net = r["net"] or 0
        total_rev_ex  += _net
        total_rev_inc += _net * VAT_DIVISOR
        covered.add(k)

    # Tx + customers
    # For each (br, m): use transaction-level Easy counts when available, otherwise
    # fall back to the monthly summary aggregate (some months were only loaded monthly).
    ms_tx_by_key = {}
    for r in ms_rev_rows:
        k = (r["branch"], r["m"])
        ms_tx_by_key.setdefault(k, []).append({"tx": r["tx"] or 0, "cust": r["cust"] or 0})

    all_keys = set(easy_tx_map.keys()) | set(ms_tx_by_key.keys())
    for k in all_keys:
        e = easy_tx_map.get(k)
        if e and e["tx"] > 0:
            total_tx   += e["tx"]
            total_cust += e["cust"]
            continue
        # No transaction-level rows — fall back to the monthly summary aggregate
        for ms in ms_tx_by_key.get(k, []):
            total_tx   += ms["tx"]
            total_cust += ms["cust"]
            break

    # Hard rule: VAT is never revenue. Headline revenue + avg ticket are ex-VAT.
    avg_ticket = round(total_rev_ex / total_tx, 2) if total_tx else 0

    return {
        "revenue":         round(total_rev_ex, 2),   # ex-VAT (headline)
        "revenue_ex_vat":  round(total_rev_ex, 2),   # back-compat alias
        "revenue_inc_vat": round(total_rev_inc, 2),  # POS reconciliation only
        "tx_count":        total_tx,
        "customer_count":  total_cust,
        "avg_ticket":      avg_ticket,                # ex-VAT
    }
# ---------------------------------------------------------------------------
# /api/sales/margin   -  per-category margin for a period
# ---------------------------------------------------------------------------
@router.get("/api/sales/margin")
@ttl_cache
def sales_margin(
    month:      Optional[str] = Query(None),
    month_from: Optional[str] = Query(None),
    month_to:   Optional[str] = Query(None),
    branch:     Optional[str] = Query(None),
):
    """Per-category margin. Requires sku_cost to be populated."""
    filters = ["b.name != 'ALL'", "sm.source = 'Easy'"]   # fix B4
    params: dict = {}

    if branch:
        filters.append("b.name = :branch")
        params["branch"] = branch
    _month_range_filter(filters, params, month_from, month_to, month, col="sm.period_month")

    where = "WHERE " + " AND ".join(filters)

    with get_db() as con:
        rows = con.execute(
            f"""
            SELECT c.name AS category,
                   ROUND(SUM(sm.subtotal_ex_vat), 2)                       AS revenue,
                   ROUND(SUM({_cogs_case(ssm='sm', sk='sk', sc='sc')}), 2) AS cost,
                   COUNT(DISTINCT CASE WHEN {_effective_cost_subquery(ssm='sm', sc='sc')} IS NULL
                                       THEN sk.sku_id END)                  AS uncosted_skus
            FROM v_sales_sku_monthly sm
            JOIN branches b   ON b.branch_id   = sm.branch_id
            JOIN skus sk      ON sk.sku_id      = sm.sku_id
            JOIN categories c ON c.category_id  = sk.category_id
            LEFT JOIN sku_cost sc ON sc.sku_id  = sm.sku_id
            {where}
            GROUP BY c.name
            ORDER BY revenue DESC
            """,
            params,
        ).fetchall()

        uncosted_total = con.execute(
            f"""
            SELECT COUNT(DISTINCT CASE WHEN {_effective_cost_subquery(ssm='sm', sc='sc')} IS NULL THEN sk.sku_id END)
            FROM v_sales_sku_monthly sm
            JOIN branches b   ON b.branch_id   = sm.branch_id
            JOIN skus sk      ON sk.sku_id      = sm.sku_id
            LEFT JOIN sku_cost sc ON sc.sku_id  = sm.sku_id
            {where}
            """,
            params,
        ).fetchone()[0] or 0

    by_category = []
    total_rev = 0.0
    total_cost = 0.0

    for r in rows:
        rev  = r["revenue"] or 0.0
        cost = r["cost"] or 0.0
        margin = round(rev - cost, 2)
        margin_pct = round(margin / rev * 100, 1) if rev else 0.0
        total_rev  += rev
        total_cost += cost
        by_category.append({
            "category":     r["category"],
            "revenue":      rev,
            "cost":         cost,
            "margin":       margin,
            "margin_pct":   margin_pct,
            "uncosted_skus": r["uncosted_skus"] or 0,
        })

    total_rev   = round(total_rev, 2)
    total_cost  = round(total_cost, 2)
    total_margin = round(total_rev - total_cost, 2)
    total_pct    = round(total_margin / total_rev * 100, 1) if total_rev else 0.0

    return {
        "by_category": by_category,
        "total": {
            "revenue":    total_rev,
            "cost":       total_cost,
            "margin":     total_margin,
            "margin_pct": total_pct,
        },
        "uncosted_sku_count": uncosted_total,
    }


def _month_add(ym: str, delta: int) -> str:
    """'YYYY-MM' shifted by `delta` months (delta may be negative)."""
    y, m = _ym_parts(ym)
    idx = (y * 12 + (m - 1)) + delta
    return f"{idx // 12:04d}-{idx % 12 + 1:02d}"


# ---------------------------------------------------------------------------
# /api/sales/dish-movers  -  per-DISH MoM movers (food menu items)
# ---------------------------------------------------------------------------
@router.get("/api/sales/dish-movers")
@ttl_cache
def sales_dish_movers(
    month:      Optional[str] = Query(None),
    month_from: Optional[str] = Query(None),
    month_to:   Optional[str] = Query(None),
    vs_month:   Optional[str] = Query(None, description="explicit comparison month (overrides auto-previous)"),
    branch:     Optional[str] = Query(None),
    metric:     str           = Query("qty", description="'qty' or 'revenue'"),
    limit:      int           = Query(8, ge=1, le=30),
):
    """Which FOOD DISHES rose/fell most vs a comparison period.

    Per-dish (not category) and food-only (uses _FOOD_CATEGORY_FILTER), so it
    surfaces actionable menu movement. Default metric = qty (orders), which is
    buffet-aware (buffet dishes have ~0 à-la-carte revenue). Set metric=revenue
    for à-la-carte revenue movement.

    Comparison period: by default the previous equal-length period (MoM). Pass
    ``vs_month`` to compare the current month against a SPECIFIC chosen month
    instead (the month-vs-month picker on the Top Menu Movers card).
    """
    cur_to   = month_to or month
    cur_from = month_from or month
    if not cur_to:
        with get_db() as con:
            cur_to = con.execute(
                "SELECT MAX(period_month) FROM sales_sku_monthly WHERE source='Easy'"
            ).fetchone()[0]
        cur_from = cur_to
    if not cur_from:
        cur_from = cur_to

    fy, fm = _ym_parts(cur_from); ty, tm = _ym_parts(cur_to)
    span = (ty * 12 + tm) - (fy * 12 + fm)
    if vs_month:
        # Explicit month-vs-month: compare against the chosen month exactly.
        prev_from = prev_to = vs_month
    else:
        prev_to   = _month_add(cur_from, -1)
        prev_from = _month_add(prev_to, -span)

    val_col = "ssm.qty" if metric == "qty" else "ssm.subtotal_ex_vat"

    def _dish_vals(mf: str, mt: str) -> dict:
        filters = ["b.name != 'ALL'", "ssm.source = 'Easy'",
                   "ssm.period_month >= :mf", "ssm.period_month <= :mt",
                   _FOOD_CATEGORY_FILTER]
        params = {"mf": mf, "mt": mt}
        if branch:
            filters.append("b.name = :branch"); params["branch"] = branch
        where = "WHERE " + " AND ".join(filters)
        with get_db() as con:
            rows = con.execute(
                f"""SELECT sk.sku_code AS code,
                           COALESCE(sk.description, sk.sku_code) AS name,
                           c.name AS category,
                           SUM({val_col}) AS val
                    FROM v_sales_sku_monthly ssm
                    JOIN branches b   ON b.branch_id   = ssm.branch_id
                    JOIN skus sk      ON sk.sku_id      = ssm.sku_id
                    JOIN categories c ON c.category_id  = sk.category_id
                    {where}
                    GROUP BY sk.sku_id""",
                params,
            ).fetchall()
        return {r["code"]: {"name": r["name"], "category": r["category"],
                            "val": r["val"] or 0} for r in rows}

    # PR-PP2 · the one period pattern: a window that ends in the month in progress (the month of the last closed day,
    # not yet at its end) is to date, and its bases are their first n days (the previous month 1..n, or the chosen
    # month 1..n; the same days a year back) from the daily table - never a part month against a whole one.
    import calendar as _cal
    import periods as _P
    val_d = "d.qty" if metric == "qty" else "d.subtotal_ex_vat"

    def _dish_days(s: str, e: str) -> dict:
        filters = ["b.name != 'ALL'", "d.day BETWEEN :s AND :e", _FOOD_CATEGORY_FILTER]
        params = {"s": s, "e": e}
        if branch:
            filters.append("b.name = :branch"); params["branch"] = branch
        with get_db() as con:
            rows = con.execute(
                f"""SELECT sk.sku_code AS code, COALESCE(sk.description, sk.sku_code) AS name, c.name AS category,
                           SUM({val_d}) AS val
                    FROM v_sales_sku_daily d
                    JOIN branches b   ON b.branch_id   = d.branch_id
                    JOIN skus sk      ON sk.sku_id      = d.sku_id
                    JOIN categories c ON c.category_id  = sk.category_id
                    WHERE {" AND ".join(filters)} GROUP BY sk.sku_id""", params).fetchall()
        return {r["code"]: {"name": r["name"], "category": r["category"], "val": r["val"] or 0} for r in rows}

    with get_db() as con:
        _sw = "t.source='Easy' AND b.name!='ALL'" + (" AND b.name = :branch" if branch else "")
        _anchor = _P.anchor(con, _sw, {"branch": branch} if branch else None)
        _dfirst = con.execute("SELECT MIN(day) FROM sales_sku_daily").fetchone()[0]
    to_date = None
    ly_v = None
    ly_from = ly_to = None
    if _anchor and cur_to == _anchor[:7] and not _P.resolve("month", _anchor)["complete"]:
        _ty, _tm = _ym_parts(cur_to)
        if vs_month or span == 0:
            w = _P.resolve("month", _anchor, date_from=f"{cur_from}-01")
            cs, ce = w["cur"]
            if vs_month:
                _vy, _vm = _ym_parts(vs_month)
                ps, pe = f"{vs_month}-01", f"{vs_month}-{min(w['days'], _cal.monthrange(_vy, _vm)[1]):02d}"
            else:
                ps, pe = w["prev"]
        else:
            w = _P.resolve("range", _anchor, date_from=f"{cur_from}-01", date_to=f"{cur_to}-{_cal.monthrange(_ty, _tm)[1]:02d}")
            cs, ce = w["cur"]
            ps, pe = w["prev"]
        ly_from, ly_to = w["ly"]
        if _dfirst and ps >= _dfirst:
            to_date = {"cur": [cs, ce], "prev": [ps, pe], "ly": [ly_from, ly_to], "days": w["days"],
                       "label_th": _P.label_th(cs, ce), "prev_label_th": _P.label_th(ps, pe), "ly_label_th": _P.label_th(ly_from, ly_to)}
    if to_date:
        cur_v, prev_v = _dish_days(*to_date["cur"]), _dish_days(*to_date["prev"])
        ly_v = _dish_days(ly_from, ly_to) if (_dfirst and ly_from >= _dfirst) else None
    else:
        cur_v  = _dish_vals(cur_from, cur_to)
        prev_v = _dish_vals(prev_from, prev_to)
        if not vs_month:                       # the same months a year back (whole vs whole)
            ly_from, ly_to = _month_add(cur_from, -12), _month_add(cur_to, -12)
            ly_v = _dish_vals(ly_from, ly_to) or None

    movers = []
    for code in set(cur_v) | set(prev_v):
        now = cur_v.get(code, {}).get("val", 0)
        was = prev_v.get(code, {}).get("val", 0)
        meta = cur_v.get(code) or prev_v.get(code)
        delta = round(now - was, 2)
        if delta == 0:
            continue
        pct = round(delta / was * 100, 1) if was > 0 else None
        ly_was = (ly_v.get(code, {}).get("val", 0) if ly_v is not None else None)
        movers.append({
            "code": code, "name": meta["name"], "category": meta["category"],
            "now": round(now, 2), "was": round(was, 2),
            "delta": delta, "delta_pct": pct, "is_new": was == 0 and now > 0,
            "was_ly": round(ly_was, 2) if ly_was is not None else None,
            "delta_ly_pct": _P.pct(now, ly_was) if ly_was else None,
        })

    movers.sort(key=lambda m: abs(m["delta"]), reverse=True)
    # Independent top gainers / decliners (not a shared abs-delta top-N, which
    # was all gainers for high-volume dishes → empty decliners).
    gainers   = sorted([m for m in movers if m["delta"] > 0], key=lambda m: m["delta"], reverse=True)[:limit]
    decliners = sorted([m for m in movers if m["delta"] < 0], key=lambda m: m["delta"])[:limit]
    return {
        "metric":   metric,
        "current":  {"from": cur_from, "to": cur_to},
        "previous": {"from": prev_from, "to": prev_to},
        "year_ago": {"from": ly_from, "to": ly_to} if ly_from else None,
        "to_date":  to_date,                 # PR-PP2: the day windows when the current month is not finished
        "gainers":   gainers,
        "decliners": decliners,
    }
