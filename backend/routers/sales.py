"""Sales domain router — /api/sales/*, /api/overall/*, /api/summary, moved
VERBATIM out of main.py (P1 structure refactor, step 3). No logic changes;
URLs unchanged. Same host-module pattern as routers/reports.py.
"""
# Shared infrastructure and helpers come from core.py by name — the old
# `globals().update(getattr(main, …))` bootstrap is gone (Phase 2, 2026-09).
from fastapi import Query
from typing import Optional
from rules import EASY_TIER_CODES_SQL as _EASY_TIER_CODES_SQL, VAT_DIVISOR, closed_day_clause, net_revenue_row, needs_cost_sql, net_revenue_sum, scope_filter
from core import (
    _FOOD_CATEGORY_FILTER, _cogs_case, _effective_cost_subquery, _group_filter, _iso_week_sql,
    _month_range_filter, _partial_months, _txn_fallback_counts, _unified_cogs_expr, _ym_parts,
    ex_vat, get_db, ttl_cache,
)

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


def _mtd_window(con):
    """Equal-period (MTD) comparison window when the latest month is incomplete.
    Returns None when the latest month is complete (use normal monthly MoM).
    STANDING RULE: never compare a partial month against a full month."""
    import calendar as _cal
    row = con.execute("SELECT MAX(day) m FROM sales_sku_daily").fetchone()
    dmax = row["m"] if row else None
    if not dmax:
        return None
    y, m, d = (int(x) for x in dmax.split("-"))
    if d >= _cal.monthrange(y, m)[1]:
        return None
    pidx = y * 12 + (m - 1) - 1; py, pm = pidx // 12, pidx % 12 + 1
    ped = min(d, _cal.monthrange(py, pm)[1])
    return {"cutoff_day": d, "latest_month": f"{y:04d}-{m:02d}", "prev_month": f"{py:04d}-{pm:02d}",
            "cur_s": f"{y:04d}-{m:02d}-01", "cur_e": dmax,
            "prev_s": f"{py:04d}-{pm:02d}-01", "prev_e": f"{py:04d}-{pm:02d}-{ped:02d}"}


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
        mom_basis = "month"
        mtd_cut = None
        ids = [r["sku_id"] for r in rows]
        if latest and ids:
            ph  = ",".join(f":id{i}" for i in range(len(ids)))
            idp = {f"id{i}": v for i, v in enumerate(ids)}
            mom_params = {**idp, "lm": latest, "pm": prev or "__none__"}
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
                  AND sm.period_month IN (:lm, :pm) {branch_clause}
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

        # MTD override (STANDING RULE): if the latest month is incomplete, recompute
        # MoM from sales_sku_daily over the SAME day-range of both months.
        mtd = _mtd_window(con)
        if mtd and latest == mtd["latest_month"] and ids:
            mom_basis = "mtd"; mtd_cut = mtd["cutoff_day"]
            phd = ",".join(f":mid{i}" for i in range(len(ids)))
            dparams = {f"mid{i}": v for i, v in enumerate(ids)}
            dparams.update({"cs": mtd["cur_s"], "ce": mtd["cur_e"], "ps": mtd["prev_s"], "pe": mtd["prev_e"]})
            dwhere = [f"d.sku_id IN ({phd})", "(d.day BETWEEN :cs AND :ce OR d.day BETWEEN :ps AND :pe)"]
            if branch:
                dwhere.append("b.name = :dbranch"); dparams["dbranch"] = branch
            gf: list = []; _group_filter(gf, dparams, group)
            dwhere += gf
            drows = con.execute(f"""
                SELECT d.sku_id,
                  SUM(CASE WHEN d.day BETWEEN :cs AND :ce THEN d.qty ELSE 0 END) cq,
                  SUM(CASE WHEN d.day BETWEEN :ps AND :pe THEN d.qty ELSE 0 END) pq,
                  SUM(CASE WHEN d.day BETWEEN :cs AND :ce THEN d.subtotal_ex_vat ELSE 0 END) cr,
                  SUM(CASE WHEN d.day BETWEEN :ps AND :pe THEN d.subtotal_ex_vat ELSE 0 END) pr
                FROM v_sales_sku_daily d JOIN branches b ON b.branch_id = d.branch_id
                WHERE {' AND '.join(dwhere)} GROUP BY d.sku_id""", dparams).fetchall()
            mom_map = {}; rev_mom_map = {}
            for dr in drows:
                mom_map[dr["sku_id"]]     = ((dr["cq"] - dr["pq"]) / dr["pq"] * 100) if dr["pq"] else None
                rev_mom_map[dr["sku_id"]] = ((dr["cr"] - dr["pr"]) / dr["pr"] * 100) if dr["pr"] else None
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
        # Chain mirrors _unified_cogs_expr: cost-management (non-zero) → BoM → alias → 0.
        cogs = (f"CASE WHEN (SELECT sk_t.sku_code FROM skus sk_t WHERE sk_t.sku_id=d.sku_id) "
                f"IN {_EASY_TIER_CODES_SQL} THEN 0 ELSE d.qty * COALESCE("
                f"(SELECT sec.recipe_cost FROM sku_effective_cost sec WHERE sec.sku_id=d.sku_id AND sec.recipe_cost>0), "
                f"(SELECT SUM(bi.ingredient_cost) FROM bom_ingredients bi WHERE bi.sku_id=d.sku_id), "
                f"(SELECT SUM(bi2.ingredient_cost) FROM bom_ingredients bi2 "
                f"JOIN bom_sku_alias a ON a.resolved_sku_id=bi2.sku_id "
                f"JOIN skus s_al ON s_al.sku_id=d.sku_id WHERE a.bom_sku_code=s_al.sku_code), "
                f"0) END")
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
    with get_db() as con:
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
    with get_db() as con:
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
            JOIN sales_transaction t ON t.receipt_no = sbc.receipt_no
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
    gran:   str           = Query("day"),     # day | week | month
    trend:  int           = Query(8, ge=2, le=26),
):
    """Per-branch sales for a day/week/month with same-period MoM & YoY deltas,
    revenue/head, a trailing trend, and anomaly flags. REVENUE ONLY (ex-VAT
    goods = food+drink+other) — no cost here; COGS stays on BoM elsewhere."""
    import datetime as _dt, calendar as _cal
    if gran not in ("day", "week", "month"):
        gran = "day"
    base = ["t.source='Easy'", "b.name!='ALL'"]
    params: dict = {}
    scope_filter(base, params, branch, group)
    where = " AND ".join(base)

    mtd_cut_day = None   # set below when the latest month is partial → MTD-fair month compare
    def bounds(d):
        if gran == "day":
            return d, d
        if gran == "week":
            s = d - _dt.timedelta(days=d.weekday()); return s, s + _dt.timedelta(days=6)
        s = d.replace(day=1)
        last = _cal.monthrange(s.year, s.month)[1]
        # When the latest month is still partial, clamp EVERY month period (current /
        # prev / YoY) to the same elapsed days so month-vs-month is MTD-vs-MTD fair.
        end_day = min(last, mtd_cut_day) if mtd_cut_day else last
        return s, s.replace(day=end_day)

    def minus_month(d, n=1):
        idx = d.year * 12 + (d.month - 1) - n
        y, m = idx // 12, idx % 12 + 1
        return _dt.date(y, m, min(d.day, _cal.monthrange(y, m)[1]))

    def comp(fdate, back):       # back = 'month' | 'year'
        if gran == "day":
            if back == "month":
                return minus_month(fdate, 1)
            try:    return fdate.replace(year=fdate.year - 1)
            except ValueError: return fdate.replace(year=fdate.year - 1, day=28)
        if gran == "week":
            return fdate - _dt.timedelta(days=28 if back == "month" else 364)
        if back == "month":
            return minus_month(fdate.replace(day=1), 1)
        return fdate.replace(year=fdate.year - 1, day=1)

    def pct(c, p):
        return round((c - p) / p * 100, 1) if p else None

    with get_db() as con:
        # The default focus is the latest FINISHED day (rules.closed_day_clause): from
        # the noon sync the newest day is today with a few hours of bills, and a
        # day/MTD tile built on it read as -99.9% (owner 2026-10-02). An explicit
        # `date` still reaches today.
        maxd = con.execute(
            f"SELECT MAX(date(t.sold_at)) FROM sales_transaction t "
            f"JOIN branches b ON b.branch_id=t.branch_id WHERE {where} AND {closed_day_clause('t.sold_at')}", params).fetchone()[0]
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
        fy, fm, fd = (int(x) for x in focus.split("-"))
        fdate = _dt.date(fy, fm, fd)
        # MTD-fair month compare: if the focus month is the latest, still-partial month,
        # clamp every month period to the same elapsed days (set the closure's cutoff).
        if gran == "month":
            my, mm, mdd = (int(x) for x in maxd[:10].split("-"))
            if (fy, fm) == (my, mm) and mdd < _cal.monthrange(my, mm)[1]:
                mtd_cut_day = mdd

        def fetch(d):
            s, e = bounds(d)
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
            return {r["branch"]: {"rev": r["rev"] or 0, "cust": r["cust"] or 0, "bills": r["bills"]} for r in rows}, (s, e)

        cur, (cs, ce) = fetch(fdate)
        pm,  (pms, pme) = fetch(comp(fdate, "month"))
        py,  (pys, pye) = fetch(comp(fdate, "year"))

        # Guard partial coverage: if a comparison period starts before our data
        # begins, it's incomplete → drop it (avoids bogus deltas like YoY +472%
        # vs the partial first month). Same idea as the benchmark guard.
        mind = con.execute(
            f"SELECT MIN(date(t.sold_at)) FROM sales_transaction t "
            f"JOIN branches b ON b.branch_id=t.branch_id WHERE {where}", params).fetchone()[0] or "9999-99"
        if pms.isoformat() < mind:
            pm = {}
        if pys.isoformat() < mind:
            py = {}

        # Weekday-fair MoM (mirrors the Management report §3): a raw same-elapsed-
        # days compare misleads early in the month (1–2 Jul = Wed+Thu vs 1–2 Jun =
        # holiday Mon+Tue read as "-44%"), so while the focus month is partial the
        # previous-month side of every MoM number (per-branch and total, revenue
        # and customers) is rebuilt from that month's per-weekday daily averages
        # summed over the same elapsed dates. YoY keeps the plain same-days clamp.
        mom_basis = "same_days" if mtd_cut_day else "month"
        dow_tot = None
        if mtd_cut_day and pm:
            elapsed_wd = [int((cs + _dt.timedelta(days=i)).strftime("%w"))
                          for i in range(mtd_cut_day)]

            def dow_base(rows_):
                avg = {r["wd"]: ((r["rev"] or 0) / r["days"], (r["cust"] or 0) / r["days"])
                       for r in rows_ if r["days"]}
                if not avg:
                    return None
                fb = (sum(v[0] for v in avg.values()) / len(avg),
                      sum(v[1] for v in avg.values()) / len(avg))
                return {"rev":  round(sum(avg.get(w, fb)[0] for w in elapsed_wd), 2),
                        "cust": round(sum(avg.get(w, fb)[1] for w in elapsed_wd), 1),
                        "bills": 0}

            dow_sel = f"""CAST(strftime('%w', t.sold_at) AS INT) wd,
                       {net_revenue_sum('t')} rev, SUM(t.customer_count) cust,
                       COUNT(DISTINCT date(t.sold_at)) days
                FROM sales_transaction t JOIN branches b ON b.branch_id=t.branch_id"""
            dow_prm = {**params, "pmm": pms.strftime("%Y-%m")}
            by_branch: dict = {}
            for r in con.execute(f"""
                SELECT b.name branch, {dow_sel}
                WHERE {where} AND strftime('%Y-%m', t.sold_at)=:pmm
                GROUP BY b.name, wd""", dow_prm).fetchall():
                by_branch.setdefault(r["branch"], []).append(r)
            pm_dow = {nm: base for nm, rs in by_branch.items() if (base := dow_base(rs))}
            # chain-level baseline over the whole scope (NOT the sum of per-branch
            # baselines) so the total MoM ties out with the Management report §3.
            dow_tot = dow_base(con.execute(f"""
                SELECT {dow_sel}
                WHERE {where} AND strftime('%Y-%m', t.sold_at)=:pmm
                GROUP BY wd""", dow_prm).fetchall())
            if pm_dow and dow_tot:
                pm, mom_basis = pm_dow, "dow"
            else:
                dow_tot = None

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
            })
        out.sort(key=lambda r: -r["revenue"])

        def tot(d): return sum(x["rev"] for x in d.values()), sum(x["cust"] for x in d.values())
        crv, ccu = tot(cur); prv, pcu = tot(pm); yrv, ycu = tot(py)
        if dow_tot:   # total MoM against the scope-level weekday baseline
            prv, pcu = dow_tot["rev"], dow_tot["cust"]
        cur_rph  = crv / ccu if ccu else None
        prev_rph = prv / pcu if pcu else None
        yoy_rph  = yrv / ycu if ycu else None
        total = {"revenue": round(crv, 2), "customers": ccu,
                 "rev_per_head": round(crv / ccu, 1) if ccu else None,
                 "mom_delta": round(crv - prv, 2), "mom_pct": pct(crv, prv),
                 "yoy_delta": round(crv - yrv, 2), "yoy_pct": pct(crv, yrv),
                 # customer-count + revenue/head deltas over the SAME MTD-fair periods as revenue
                 "cust_mom_pct": pct(ccu, pcu), "cust_yoy_pct": pct(ccu, ycu),
                 "rph_mom_pct": pct(cur_rph, prev_rph) if cur_rph is not None else None,
                 "rph_yoy_pct": pct(cur_rph, yoy_rph) if cur_rph is not None else None}

        # trailing trend (chain total rev), `trend` periods ending at focus
        tr = []; d = fdate
        for _ in range(trend):
            data, (s, e) = fetch(d)
            tr.append({"start": s.isoformat(), "end": e.isoformat(),
                       "revenue": round(sum(x["rev"] for x in data.values()), 2)})
            d = d - _dt.timedelta(days=1) if gran == "day" else \
                d - _dt.timedelta(days=7) if gran == "week" else minus_month(d.replace(day=1), 1)
        tr.reverse()

        # anomaly: chain total vs trailing average + big per-branch MoM movers
        prior = [x["revenue"] for x in tr[:-1]]
        avg = sum(prior) / len(prior) if prior else 0
        anomalies = []
        ap = pct(crv, avg)
        if ap is not None and abs(ap) >= 20:
            anomalies.append({"scope": "chain", "pct": ap,
                              "msg": f"ยอดรวม{'สูง' if ap > 0 else 'ต่ำ'}กว่าค่าเฉลี่ย {abs(ap):.0f}%"})
        for r in out:
            if r["mom_pct"] is not None and abs(r["mom_pct"]) >= 30:
                anomalies.append({"scope": r["branch"], "pct": r["mom_pct"],
                                  "msg": f"{r['branch']} {'พุ่ง' if r['mom_pct'] > 0 else 'ตก'} {abs(r['mom_pct']):.0f}% vs ช่วงก่อน"})

    return {"gran": gran, "focus": focus,
            "cur_range": [cs.isoformat(), ce.isoformat()],
            "prev_range": [pms.isoformat(), pme.isoformat()],
            "yoy_range": [pys.isoformat(), pye.isoformat()],
            "mtd": mtd_cut_day is not None, "mtd_cutoff_day": mtd_cut_day,
            "mom_basis": mom_basis,
            "branches": out, "total": total, "trend": tr, "anomalies": anomalies}




@router.get("/api/sales/menu-spike")
def menu_spike(date: Optional[str] = Query(None), branch: Optional[str] = Query(None),
               group: Optional[str] = Query(None), limit: int = Query(300, ge=1, le=1000),
               threshold: float = Query(1.5, ge=1.0)):
    """Menus whose QTY on a given day spiked vs their trailing 21-day average, per
    branch (answers '#5: which menu was abnormally high that day'). Shows ALL items
    above `threshold`× (default 1.5×). Qty-only from sales_sku_daily — NO cost (iron rule)."""
    import datetime as _dt
    with get_db() as con:
        try:
            maxd = con.execute("SELECT MAX(day) FROM sales_sku_daily").fetchone()[0]
        except Exception:
            return {"date": None, "spikes": [], "note": "no daily sku data"}
        if not maxd:
            return {"date": None, "spikes": []}
        focus = (date or maxd)[:10]
        fy, fm, fd = (int(x) for x in focus.split("-"))
        wstart = (_dt.date(fy, fm, fd) - _dt.timedelta(days=21)).isoformat()
        bfilt = ""; params = {"focus": focus, "ws": wstart}
        if branch:
            bfilt += " AND b.name=:branch"; params["branch"] = branch
        if group:
            bfilt += " AND COALESCE(b.branch_type,'master')=:grp"; params["grp"] = group
        rows = con.execute(f"""
            WITH today AS (
              SELECT d.branch_id, d.sku_id, SUM(d.qty) q
              FROM v_sales_sku_daily d JOIN branches b ON b.branch_id=d.branch_id
              WHERE d.day=:focus {bfilt} GROUP BY d.branch_id, d.sku_id),
            hist AS (
              SELECT branch_id, sku_id, AVG(qty) avgq, COUNT(*) n
              FROM v_sales_sku_daily WHERE day>=:ws AND day<:focus GROUP BY branch_id, sku_id)
            SELECT b.name branch, s.description, s.sku_code, t.q today,
                   COALESCE(h.avgq,0) avgq, COALESCE(h.n,0) n
            FROM today t JOIN branches b ON b.branch_id=t.branch_id
            JOIN skus s ON s.sku_id=t.sku_id
            LEFT JOIN hist h ON h.branch_id=t.branch_id AND h.sku_id=t.sku_id
            WHERE t.q>0""", params).fetchall()
        spikes = []
        for r in rows:
            today = r["today"] or 0; avg = r["avgq"] or 0; n = r["n"] or 0
            name = (r["description"] or r["sku_code"] or "").replace("(B) ", "").replace("(A) ", "")
            if avg > 0 and n >= 5:
                x = today / avg
                # show ALL items above the threshold (default 1.5×); tiny floor (≥5
                # plates) keeps trivial 1→2 jumps out of the list.
                if x >= threshold and today >= 5:
                    spikes.append({"branch": r["branch"], "menu": name, "today": round(today),
                                   "avg": round(avg, 1), "x": round(x, 1), "pct": round((x - 1) * 100),
                                   "excess": round(today - avg), "is_new": False})
            elif n < 5 and today >= 20:
                spikes.append({"branch": r["branch"], "menu": name, "today": round(today),
                               "avg": 0, "x": None, "pct": None, "excess": round(today), "is_new": True})
        spikes.sort(key=lambda s: -s["excess"])
    return {"date": focus, "window_days": 21, "threshold": threshold,
            "count": len(spikes), "spikes": spikes[:limit]}


@router.get("/api/sales/cost-per-head")
def cost_per_head(date: Optional[str] = Query(None), branch: Optional[str] = Query(None),
                  group: Optional[str] = Query(None), gran: str = Query("day")):
    """#10 — BoM cost per head per branch, with previous-period comparison.
    COGS via our BoM only (_unified_cogs_expr). Daily grain from sales_sku_daily
    (rolling window), customers from sales_transaction."""
    import datetime as _dt, calendar as _cal
    if gran not in ("day", "week", "month"):
        gran = "day"
    cogs_expr = _unified_cogs_expr("ssm")

    def bounds(d):
        if gran == "day": return d, d
        if gran == "week":
            s = d - _dt.timedelta(days=d.weekday()); return s, s + _dt.timedelta(days=6)
        s = d.replace(day=1)
        nxt = s.replace(year=s.year + 1, month=1) if s.month == 12 else s.replace(month=s.month + 1)
        return s, nxt - _dt.timedelta(days=1)

    def prev_focus(d):
        if gran == "day":  return d - _dt.timedelta(days=1)
        if gran == "week": return d - _dt.timedelta(days=7)
        idx = d.year * 12 + (d.month - 1) - 1
        y, m = idx // 12, idx % 12 + 1
        return _dt.date(y, m, min(d.day, _cal.monthrange(y, m)[1]))

    tfilt = ["t.source='Easy'", "b.name!='ALL'"]; tp: dict = {}
    scope_filter(tfilt, tp, branch, group)
    twhere = " AND ".join(tfilt)
    dbf = ""  # branch filter for the daily-sku side (by name via join)
    if branch: dbf = " AND b.name=:branch"
    # fix A1: build group condition for the COGS/revenue query so it matches the customer query scope
    _gcond_list: list = []; _gcond_params: dict = {}
    _group_filter(_gcond_list, _gcond_params, group)
    gcond = (" AND " + " AND ".join(_gcond_list)) if _gcond_list else ""

    with get_db() as con:
        maxd = con.execute("SELECT MAX(day) FROM sales_sku_daily").fetchone()[0]
        if not maxd:
            return {"gran": gran, "focus": None, "branches": [], "total": {}}
        focus = (date or maxd)[:10]
        fy, fm, fd = (int(x) for x in focus.split("-"))
        fdate = _dt.date(fy, fm, fd)
        # sales_sku_daily is a rolling window. A period that starts before it used
        # to read cost ฿0 against real covers — "฿0.0/head" instead of "no data".
        # A whole month falls back to the monthly table (same COGS expression, as
        # the management report does); a day or week before the window has no
        # cost source at all and says so (cost None -> "—").
        daily_min = con.execute("SELECT MIN(day) FROM sales_sku_daily").fetchone()[0] or "9999-99-99"

        def fetch(d):
            s, e = bounds(d)
            if s.isoformat() >= daily_min:
                src = ("(SELECT branch_id, sku_id, qty, subtotal_ex_vat, 'Easy' AS source,"
                       " substr(day, 1, 7) AS period_month"
                       " FROM v_sales_sku_daily WHERE day BETWEEN :s AND :e)")
                sp_ = {"s": s.isoformat(), "e": e.isoformat()}
            elif gran == "month":
                src = ("(SELECT branch_id, sku_id, qty, subtotal_ex_vat, 'Easy' AS source, period_month"
                       " FROM v_sales_sku_monthly WHERE period_month = :m)")
                sp_ = {"m": s.strftime("%Y-%m")}
            else:
                src = None
            cust = {r["branch"]: r["cust"] or 0 for r in con.execute(
                f"""SELECT b.name branch, SUM(t.customer_count) cust
                    FROM sales_transaction t JOIN branches b ON b.branch_id=t.branch_id
                    WHERE {twhere} AND date(t.sold_at) BETWEEN :s AND :e
                    GROUP BY b.name""", {**tp, "s": s.isoformat(), "e": e.isoformat()}).fetchall()}
            if src is None:
                return cust, None, None, (s, e)
            cogs, rev = {}, {}
            for r in con.execute(
                f"""SELECT b.name branch, ROUND(SUM({cogs_expr}),2) cogs,
                           ROUND(SUM(ssm.subtotal_ex_vat),2) rev
                    FROM {src} ssm
                    JOIN branches b ON b.branch_id=ssm.branch_id
                    WHERE b.name!='ALL'{dbf}{gcond} GROUP BY b.name""",
                {**sp_, **({"branch": branch} if branch else {}), **_gcond_params}).fetchall():
                cogs[r["branch"]] = r["cogs"] or 0
                rev[r["branch"]]  = r["rev"] or 0
            return cust, cogs, rev, (s, e)

        cust, cogs, rev, (cs, ce) = fetch(fdate)
        pcust, pcogs, _prev_rev, (ps, pe) = fetch(prev_focus(fdate))
        cost_known, prev_known = cogs is not None, pcogs is not None
        cogs, rev, pcogs = cogs or {}, rev or {}, pcogs or {}

        def pct(c, p): return round((c - p) / p * 100, 1) if p else None
        out = []
        for nm in sorted(set(cust) | set(cogs)):
            cu, co, re = cust.get(nm, 0), cogs.get(nm, 0), rev.get(nm, 0)
            pcu, pco = pcust.get(nm, 0), pcogs.get(nm, 0)
            ch  = round(co / cu, 1) if (cu and cost_known) else None    # cost / head
            pch = round(pco / pcu, 1) if (pcu and prev_known) else None # prev cost / head
            rph = round(re / cu, 1) if (cu and cost_known) else None    # revenue / head (spend)
            cost_pct = round(co / re * 100, 1) if re else None   # food-cost ratio
            gph = round((re - co) / cu, 1) if (cu and cost_known) else None   # GP / head
            out.append({"branch": nm, "customers": cu, "cogs": round(co, 2),
                        "revenue": round(re, 2), "cost_per_head": ch, "prev_cost_per_head": pch,
                        "delta_pct": pct(ch or 0, pch or 0) if (pch and ch is not None) else None,
                        "rev_per_head": rph, "cost_pct": cost_pct, "gp_per_head": gph})
        out.sort(key=lambda r: -(r["cost_pct"] if r["cost_pct"] is not None else -1))
        tcu = sum(cust.values()); tco = sum(cogs.values()); tre = sum(rev.values())
        ptcu = sum(pcust.values()); ptco = sum(pcogs.values())
        tch = round(tco / tcu, 1) if (tcu and cost_known) else None
        ptch = round(ptco / ptcu, 1) if (ptcu and prev_known) else None
        total = {"customers": tcu, "cogs": round(tco, 2) if cost_known else None,
                 "revenue": round(tre, 2) if cost_known else None,
                 "cost_per_head": tch, "prev_cost_per_head": ptch,
                 "delta_pct": pct(tch or 0, ptch or 0) if (ptch and tch is not None) else None,
                 "rev_per_head": round(tre / tcu, 1) if (tcu and cost_known) else None,
                 "cost_pct": round(tco / tre * 100, 1) if tre else None,
                 "gp_per_head": round((tre - tco) / tcu, 1) if (tcu and cost_known) else None}
    return {"gran": gran, "focus": focus, "cost_known": cost_known, "cost_from": daily_min,
            "cur_range": [cs.isoformat(), ce.isoformat()],
            "prev_range": [ps.isoformat(), pe.isoformat()], "branches": out, "total": total}


@router.get("/api/overall/latest-month-mtd")
def latest_month_mtd(branch: Optional[str] = Query(None), group: Optional[str] = Query(None)):
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
        maxday = con.execute("SELECT MAX(date(sold_at)) FROM sales_transaction WHERE source='Easy'").fetchone()[0]
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
        rev_ex  = ex_vat(rev_inc)          # VAT is never revenue
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

    cur_v  = _dish_vals(cur_from, cur_to)
    prev_v = _dish_vals(prev_from, prev_to)

    movers = []
    for code in set(cur_v) | set(prev_v):
        now = cur_v.get(code, {}).get("val", 0)
        was = prev_v.get(code, {}).get("val", 0)
        meta = cur_v.get(code) or prev_v.get(code)
        delta = round(now - was, 2)
        if delta == 0:
            continue
        pct = round(delta / was * 100, 1) if was > 0 else None
        movers.append({
            "code": code, "name": meta["name"], "category": meta["category"],
            "now": round(now, 2), "was": round(was, 2),
            "delta": delta, "delta_pct": pct, "is_new": was == 0 and now > 0,
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
        "gainers":   gainers,
        "decliners": decliners,
    }
