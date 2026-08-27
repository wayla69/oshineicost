"""Buffet domain router — all 22 /api/buffet/* endpoints, moved VERBATIM out
of main.py (P1 structure refactor, step 5). No logic changes; URLs unchanged.
Same host-module pattern as routers/reports.py.
"""
import sys as _sys

from fastapi import APIRouter

router = APIRouter()

M = _sys.modules.get("backend.main") or _sys.modules["main"]
_SHARED = (
    '_ols_regression',
    '_ym_parts',
    'HTTPException',
    'Optional',
    'Query',
    'StreamingResponse',
    '_ALL_TIER_CODES',
    '_EASY_TIER_CODES_SQL',
    '_FOOD_CATEGORY_FILTER',
    '_cogs_case',
    '_costed_rev_case',
    '_effective_cost_subquery',
    '_group_filter',
    '_io',
    '_month_range_filter',
    '_next_period_month',
    '_unified_cogs_expr',
    'get_db',
    'ttl_cache',
)
globals().update({_n: getattr(M, _n) for _n in _SHARED})




@router.get("/api/buffet/child")
@ttl_cache
def buffet_child(branch: Optional[str] = Query(None)):
    """Child-buffet (BUFFETCHILD) summary per branch: latest-month covers,
    revenue, % of buffet covers, MoM vs previous month, plus a chain-wide
    monthly trend for the YTD line. (There is no senior/age discount data, and
    'child' is a buffet TIER, not a tagged discount — this is the closest
    available signal.)"""
    bfilt = ["b.name != 'ALL'"]
    bparams: dict = {}
    if branch:
        bfilt.append("b.name = :branch")
        bparams["branch"] = branch
    where = "WHERE " + " AND ".join(bfilt + ["sk.sku_code IN ('BUFFETCHILD','BUFFET')"])
    with get_db() as con:
        rows = con.execute(f"""
            SELECT b.name AS branch, sm.period_month AS m, sk.sku_code AS code,
                   ROUND(SUM(sm.qty), 0)             AS qty,
                   ROUND(SUM(sm.subtotal_ex_vat), 2) AS rev
            FROM sales_sku_monthly sm
            JOIN branches b ON b.branch_id = sm.branch_id
            JOIN skus sk    ON sk.sku_id   = sm.sku_id
            {where}
            GROUP BY b.name, sm.period_month, sk.sku_code
        """, bparams).fetchall()

    from collections import defaultdict
    # per branch -> month -> {child_qty, child_rev, adult_qty}
    bm: dict = defaultdict(lambda: defaultdict(lambda: {"cq": 0.0, "cr": 0.0, "aq": 0.0}))
    trend_m: dict = defaultdict(lambda: {"covers": 0.0, "revenue": 0.0})
    months: set = set()
    for r in rows:
        months.add(r["m"])
        cell = bm[r["branch"]][r["m"]]
        if r["code"] == "BUFFETCHILD":
            cell["cq"] += r["qty"] or 0; cell["cr"] += r["rev"] or 0
            tm = trend_m[r["m"]]; tm["covers"] += r["qty"] or 0; tm["revenue"] += r["rev"] or 0
        else:
            cell["aq"] += r["qty"] or 0

    months_sorted = sorted(months)
    latest = months_sorted[-1] if months_sorted else None
    prev   = months_sorted[-2] if len(months_sorted) > 1 else None

    by_branch = []
    for br, mm in bm.items():
        last = mm.get(latest, {"cq": 0, "cr": 0, "aq": 0}) if latest else {"cq": 0, "cr": 0, "aq": 0}
        pv   = mm.get(prev,   {"cq": 0, "cr": 0, "aq": 0}) if prev   else {"cq": 0, "cr": 0, "aq": 0}
        covers = last["cq"]; prev_covers = pv["cq"]
        tot_buffet = last["cq"] + last["aq"]
        by_branch.append({
            "branch":        br,
            "covers":        round(covers),
            "revenue":       round(last["cr"], 2),
            "prev_covers":   round(prev_covers),
            "pct_of_buffet": round(last["cq"] / tot_buffet * 100, 1) if tot_buffet else None,
            "mom_pct":       round((covers - prev_covers) / prev_covers * 100, 1) if prev_covers > 0 else None,
        })
    by_branch.sort(key=lambda x: -x["covers"])

    trend = [{"month": m, "covers": round(trend_m[m]["covers"]),
              "revenue": round(trend_m[m]["revenue"], 2)} for m in months_sorted]
    return {"latest_month": latest, "prev_month": prev,
            "by_branch": by_branch, "trend": trend}




# ---------------------------------------------------------------------------
# /api/buffet/tier-mix
# ---------------------------------------------------------------------------
@router.get("/api/buffet/tier-mix")
@ttl_cache
def buffet_tier_mix(
    branch:     Optional[str] = Query(None),
    month_from: Optional[str] = Query(None),
    month_to:   Optional[str] = Query(None),
    month:      Optional[str] = Query(None),
):
    """Revenue and GP breakdown by buffet tier per month."""
    filters: list = [
        "c.name = 'BUFFET'"
    ]
    params: dict = {}

    if branch:
        filters.append("b.name = :branch")
        params["branch"] = branch

    _month_range_filter(filters, params, month_from, month_to, month, col="sm.period_month")

    where = "WHERE " + " AND ".join(filters) if filters else ""

    sql = f"""
        SELECT
            sm.period_month,
            sk.sku_code,
            sk.description,
            SUM(sm.qty)               AS qty,
            SUM(sm.subtotal_ex_vat)     AS revenue,
            SUM(sm.subtotal_ex_vat)   AS revenue_ex_vat,
            SUM({_cogs_case(ssm='sm', sk='sk', sc='sc')}) AS cogs_total,
            SUM(CASE WHEN {_effective_cost_subquery(ssm='sm', sc='sc')} IS NOT NULL
                     THEN sm.qty ELSE 0 END)                AS costed_qty
        FROM sales_sku_monthly sm
        JOIN branches b   ON b.branch_id = sm.branch_id
        JOIN skus sk      ON sk.sku_id   = sm.sku_id
        JOIN categories c ON c.category_id = sk.category_id
        LEFT JOIN sku_cost sc ON sc.sku_id = sm.sku_id
        {where}
        GROUP BY sm.period_month, sk.sku_code, sk.description
        ORDER BY sm.period_month, revenue DESC
    """

    with get_db() as con:
        rows = con.execute(sql, params).fetchall()

    # Build per-period detail + summary
    from collections import defaultdict
    period_rows: dict = defaultdict(list)
    period_totals: dict = defaultdict(lambda: {"qty": 0, "revenue": 0.0, "revenue_ex_vat": 0.0, "cogs": 0.0, "gp": None, "has_cost": True})

    result_rows = []
    for r in rows:
        qty          = r["qty"] or 0
        revenue      = round(r["revenue"] or 0.0, 2)
        rev_ex       = round(r["revenue_ex_vat"] or 0.0, 2)
        cogs_total   = r["cogs_total"]
        costed_qty   = r["costed_qty"] or 0
        has_cost     = cogs_total is not None and costed_qty > 0
        unit_cost    = (cogs_total / costed_qty) if has_cost else None
        cogs         = round(cogs_total, 2) if has_cost else None
        gp           = round(rev_ex - cogs, 2)   if cogs is not None else None
        gp_per_cover = round(gp / qty, 2)         if (gp is not None and qty) else None

        row_dict = {
            "period_month":  r["period_month"],
            "sku_code":      r["sku_code"],
            "description":   r["description"],
            "qty":           qty,
            "revenue":       revenue,
            "revenue_ex_vat": rev_ex,
            "unit_cost":     unit_cost,
            "cogs":          cogs,
            "gp":            gp,
            "gp_per_cover":  gp_per_cover,
        }
        result_rows.append(row_dict)

        pt = period_totals[r["period_month"]]
        pt["qty"]          += qty
        pt["revenue"]      += revenue
        pt["revenue_ex_vat"] += rev_ex
        if cogs is not None:
            pt["cogs"] += cogs
        else:
            pt["has_cost"] = False

    totals_by_period = []
    for pm, pt in sorted(period_totals.items()):
        cogs_total = round(pt["cogs"], 2) if pt["has_cost"] else None
        gp_total   = round(pt["revenue_ex_vat"] - cogs_total, 2) if cogs_total is not None else None
        totals_by_period.append({
            "period_month":  pm,
            "qty":           pt["qty"],
            "revenue":       round(pt["revenue"], 2),
            "revenue_ex_vat": round(pt["revenue_ex_vat"], 2),
            "cogs":          cogs_total,
            "gp":            gp_total,
        })

    return {"rows": result_rows, "totals_by_period": totals_by_period}


# ---------------------------------------------------------------------------
# /api/buffet/cover-economics
# ---------------------------------------------------------------------------
@router.get("/api/buffet/cover-economics")
@ttl_cache
def buffet_cover_economics(
    branch:     Optional[str] = Query(None),
    month_from: Optional[str] = Query(None),
    month_to:   Optional[str] = Query(None),
    month:      Optional[str] = Query(None),
    date_from:  Optional[str] = Query(None),
    date_to:    Optional[str] = Query(None),
):
    """Party-size distribution, hourly covers, and table-zone stats for buffet transactions."""
    tx_filters: list = [
        "t.customer_count BETWEEN 1 AND 20",
        "t.source = 'Easy'",
    ]
    tx_params: dict = {}

    if branch:
        tx_filters.append("b.name = :branch")
        tx_params["branch"] = branch

    # Date-range filter on sold_at
    if date_from or month_from or month:
        df = date_from or (month_from or month) + "-01"
        tx_filters.append("DATE(t.sold_at) >= :date_from")
        tx_params["date_from"] = df
    if date_to or month_to or month:
        # Use end-of-month if only month given
        dt_val = date_to
        if not dt_val:
            m = month_to or month
            import calendar as _cal
            y, mo = int(m[:4]), int(m[5:7])
            last_day = _cal.monthrange(y, mo)[1]
            dt_val = f"{m}-{last_day:02d}"
        tx_filters.append("DATE(t.sold_at) <= :date_to")
        tx_params["date_to"] = dt_val

    tx_where = "WHERE " + " AND ".join(tx_filters) if tx_filters else ""

    with get_db() as con:
        # 1. Party size distribution
        party_rows = con.execute(
            f"""
            SELECT
                t.customer_count                   AS party_size,
                COUNT(*)                           AS tx_count,
                SUM(t.total_inc_vat) / 1.07        AS revenue,
                AVG(t.total_inc_vat / 1.07 / t.customer_count) AS avg_revenue_per_cover,
                AVG(t.total_inc_vat / 1.07)        AS avg_ticket
            FROM sales_transaction t
            JOIN branches b ON b.branch_id = t.branch_id
            {tx_where}
            GROUP BY t.customer_count
            ORDER BY t.customer_count
            """,
            tx_params,
        ).fetchall()

        # 2. Hourly covers
        hourly_rows = con.execute(
            f"""
            SELECT
                CAST(strftime('%H', t.sold_at) AS INTEGER) AS hour,
                SUM(t.customer_count)                      AS total_covers,
                COUNT(*)                                   AS tx_count,
                AVG(t.customer_count)                      AS avg_covers_per_tx,
                SUM(t.total_inc_vat) / 1.07                AS revenue
            FROM sales_transaction t
            JOIN branches b ON b.branch_id = t.branch_id
            {tx_where}
            GROUP BY hour
            ORDER BY hour
            """,
            tx_params,
        ).fetchall()

        # 3. Table zone stats
        zone_rows = con.execute(
            f"""
            SELECT
                CASE
                    WHEN t.table_no LIKE 'T%' THEN 'Dine-in'
                    WHEN t.table_no LIKE 'G%' THEN 'VIP'
                    WHEN t.table_no LIKE 'C%' THEN 'Counter'
                    WHEN t.table_no LIKE 'Take%' THEN 'Takeaway'
                    ELSE 'Other'
                END AS zone,
                COUNT(*)              AS tx_count,
                SUM(t.customer_count) AS total_covers,
                SUM(t.total_inc_vat) / 1.07 AS revenue,
                AVG(t.total_inc_vat / 1.07) AS avg_ticket,
                AVG(t.customer_count) AS avg_covers
            FROM sales_transaction t
            JOIN branches b ON b.branch_id = t.branch_id
            {tx_where}
            GROUP BY zone
            ORDER BY revenue DESC
            """,
            tx_params,
        ).fetchall()

        # 4. Summary totals
        summary_row = con.execute(
            f"""
            SELECT
                AVG(t.customer_count)               AS avg_party_size,
                SUM(t.total_inc_vat) / 1.07 / NULLIF(SUM(t.customer_count), 0) AS avg_revenue_per_cover,
                SUM(t.customer_count)               AS total_covers,
                COUNT(*)                            AS total_tx
            FROM sales_transaction t
            JOIN branches b ON b.branch_id = t.branch_id
            {tx_where}
            """,
            tx_params,
        ).fetchone()

        # 5. Child cover estimate from sales_sku_monthly
        child_filters: list = []
        child_params: dict = {}
        if branch:
            child_filters.append("b.name = :branch")
            child_params["branch"] = branch
        _month_range_filter(child_filters, child_params, month_from, month_to, month, col="sm.period_month")
        child_filters.append(
            "(sk.sku_code LIKE 'B04%' OR sk.sku_code LIKE 'B05%' OR sk.sku_code LIKE 'B12%' "
            "OR sk.sku_code LIKE 'BUFFETCHILD%' "
            "OR LOWER(sk.description) LIKE LOWER('%child%') "
            "OR LOWER(sk.description) LIKE LOWER('%เด็ก%'))"
        )
        child_where = "WHERE " + " AND ".join(child_filters) if child_filters else ""
        child_row = con.execute(
            f"""
            SELECT SUM(sm.qty) AS child_qty
            FROM sales_sku_monthly sm
            JOIN branches b ON b.branch_id = sm.branch_id
            JOIN skus sk    ON sk.sku_id   = sm.sku_id
            {child_where}
            """,
            child_params,
        ).fetchone()

    party_size_dist = [
        {
            "party_size":            r["party_size"],
            "tx_count":              r["tx_count"],
            "revenue":               round(r["revenue"] or 0.0, 2),
            "avg_revenue_per_cover": round(r["avg_revenue_per_cover"] or 0.0, 2),
            "avg_ticket":            round(r["avg_ticket"] or 0.0, 2),
        }
        for r in party_rows
    ]

    hourly_covers = [
        {
            "hour":           r["hour"],
            "total_covers":   r["total_covers"] or 0,
            "tx_count":       r["tx_count"] or 0,
            "avg_covers_per_tx": round(r["avg_covers_per_tx"] or 0.0, 2),
            "revenue":        round(r["revenue"] or 0.0, 2),
        }
        for r in hourly_rows
    ]

    table_zone_stats = [
        {
            "zone":         r["zone"],
            "tx_count":     r["tx_count"] or 0,
            "total_covers": r["total_covers"] or 0,
            "revenue":      round(r["revenue"] or 0.0, 2),
            "avg_ticket":   round(r["avg_ticket"] or 0.0, 2),
            "avg_covers":   round(r["avg_covers"] or 0.0, 2),
        }
        for r in zone_rows
    ]

    summary = {
        "avg_party_size":        round(summary_row["avg_party_size"] or 0.0, 2),
        "avg_revenue_per_cover": round(summary_row["avg_revenue_per_cover"] or 0.0, 2),
        "total_covers":          summary_row["total_covers"] or 0,
        "total_tx":              summary_row["total_tx"] or 0,
        "child_cover_estimate":  child_row["child_qty"] or 0,
    }

    return {
        "party_size_dist":  party_size_dist,
        "hourly_covers":    hourly_covers,
        "table_zone_stats": table_zone_stats,
        "summary":          summary,
    }


# ---------------------------------------------------------------------------
# /api/buffet/revpash
# ---------------------------------------------------------------------------
@router.get("/api/buffet/revpash")
@ttl_cache
def buffet_revpash(
    branch:     Optional[str] = Query(None),
    month:      Optional[str] = Query(None),
    month_from: Optional[str] = Query(None),
    month_to:   Optional[str] = Query(None),
    seats:      int           = Query(120),
):
    """Revenue per available seat-hour (RevPASH) by hour and day-of-week, dine-in only."""
    filters: list = [
        "(t.table_no LIKE 'T%' OR t.table_no LIKE 'G%')"
    ]
    params: dict = {}

    if branch:
        filters.append("b.name = :branch")
        params["branch"] = branch

    _month_range_filter(
        filters, params, month_from, month_to, month,
        col="strftime('%Y-%m', t.sold_at)",
    )

    where = "WHERE " + " AND ".join(filters) if filters else ""

    with get_db() as con:
        hourly_rows = con.execute(
            f"""
            SELECT
                CAST(strftime('%H', t.sold_at) AS INTEGER) AS hour,
                SUM(t.total_inc_vat) / 1.07                AS revenue,
                SUM(t.customer_count)                      AS covers,
                COUNT(*)                                   AS tx_count
            FROM sales_transaction t
            JOIN branches b ON b.branch_id = t.branch_id
            {where}
            GROUP BY hour
            ORDER BY hour
            """,
            params,
        ).fetchall()

        dow_rows = con.execute(
            f"""
            SELECT
                CAST(strftime('%w', t.sold_at) AS INTEGER) AS dow,
                SUM(t.total_inc_vat) / 1.07                AS revenue,
                SUM(t.customer_count)                      AS covers,
                COUNT(DISTINCT DATE(t.sold_at))            AS day_count
            FROM sales_transaction t
            JOIN branches b ON b.branch_id = t.branch_id
            {where}
            GROUP BY dow
            ORDER BY dow
            """,
            params,
        ).fetchall()

    # Build full 24-hour array (fill zeros for missing hours)
    hourly_map = {r["hour"]: r for r in hourly_rows}
    DOW_LABELS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]

    hourly = []
    for h in range(24):
        r = hourly_map.get(h)
        rev     = round(r["revenue"] or 0.0, 2) if r else 0.0
        covers  = (r["covers"] or 0) if r else 0
        tx_cnt  = (r["tx_count"] or 0) if r else 0
        revpash = round(rev / seats, 4) if seats else 0.0
        hourly.append({
            "hour":    h,
            "revenue": rev,
            "covers":  covers,
            "tx_count": tx_cnt,
            "revpash": revpash,
        })

    dow = []
    for r in dow_rows:
        d         = r["dow"]
        rev       = round(r["revenue"] or 0.0, 2)
        covers    = r["covers"] or 0
        day_count = r["day_count"] or 1
        avg_rev   = round(rev / day_count, 2)
        avg_rph   = round(avg_rev / seats, 4) if seats else 0.0
        dow.append({
            "dow":          d,
            "label":        DOW_LABELS[d],
            "revenue":      rev,
            "covers":       covers,
            "avg_revpash":  avg_rph,
        })

    # Peak / dead hours
    non_zero = [h for h in hourly if h["revpash"] > 0]
    peak_hour = max(non_zero, key=lambda h: h["revpash"])["hour"] if non_zero else None
    dead_hour = min(non_zero, key=lambda h: h["revpash"])["hour"] if non_zero else None

    return {
        "hourly":    hourly,
        "dow":       dow,
        "seats":     seats,
        "peak_hour": peak_hour,
        "dead_hour": dead_hour,
    }


# ---------------------------------------------------------------------------
# /api/buffet/upgrade-simulator
# ---------------------------------------------------------------------------
@router.get("/api/buffet/upgrade-simulator")
@ttl_cache
def buffet_upgrade_simulator(
    branch:     Optional[str] = Query(None),
    month_from: Optional[str] = Query(None),
    month_to:   Optional[str] = Query(None),
    month:      Optional[str] = Query(None),
    shift_pct:  float         = Query(10.0),
):
    """What-if simulator: shift shift_pct% of 590-tier covers to 799-tier."""
    filters: list = ["c.name = 'BUFFET'"]
    params: dict = {}

    if branch:
        filters.append("b.name = :branch")
        params["branch"] = branch

    _month_range_filter(filters, params, month_from, month_to, month, col="sm.period_month")

    where = "WHERE " + " AND ".join(filters) if filters else ""

    sql = f"""
        SELECT
            sk.sku_code,
            sk.description,
            SUM(sm.qty)             AS qty,
            SUM(sm.subtotal_ex_vat)   AS revenue,
            SUM(sm.subtotal_ex_vat) AS revenue_ex_vat,
            SUM({_cogs_case(ssm='sm', sk='sk', sc='sc')}) AS cogs_total,
            SUM(CASE WHEN {_effective_cost_subquery(ssm='sm', sc='sc')} IS NOT NULL
                     THEN sm.qty ELSE 0 END)               AS costed_qty
        FROM sales_sku_monthly sm
        JOIN branches b   ON b.branch_id  = sm.branch_id
        JOIN skus sk      ON sk.sku_id    = sm.sku_id
        JOIN categories c ON c.category_id = sk.category_id
        LEFT JOIN sku_cost sc ON sc.sku_id = sm.sku_id
        {where}
        GROUP BY sk.sku_code, sk.description
        ORDER BY sk.sku_code
    """

    with get_db() as con:
        rows = con.execute(sql, params).fetchall()

    # Map sku_code to tier price label
    def _tier_label(sku_code: str, description: str) -> str:
        code = sku_code.upper()
        desc = description.upper() if description else ""
        if code in ("B04", "B05", "B12") or "BUFFETCHILD" in code or "CHILD" in desc or "เด็ก" in desc:
            return "child"
        if code == "B01" or "350" in code or "350" in desc:
            return "350"
        if code == "B07" or "500" in code or "500" in desc:
            return "500"
        if code == "B02" or "590" in code or "590" in desc:
            return "590"
        if code == "B11" or "799" in code or "799" in desc:
            return "799"
        if code == "B03" or "1150" in code or "1150" in desc:
            return "1150"
        return code  # fallback: use sku_code itself

    # Aggregate by tier
    from collections import defaultdict
    tier_data: dict = defaultdict(lambda: {"qty": 0, "revenue": 0.0, "revenue_ex_vat": 0.0, "cogs": 0.0, "has_cost": True, "unit_cost": None})

    for r in rows:
        label      = _tier_label(r["sku_code"], r["description"])
        qty        = r["qty"] or 0
        revenue    = r["revenue"] or 0.0
        rev_ex     = r["revenue_ex_vat"] or 0.0
        cogs_total = r["cogs_total"]
        costed_qty = r["costed_qty"] or 0
        has_row_cost = cogs_total is not None and costed_qty > 0
        unit_cost  = (cogs_total / costed_qty) if has_row_cost else None

        td = tier_data[label]
        td["qty"]          += qty
        td["revenue"]      += revenue
        td["revenue_ex_vat"] += rev_ex
        if has_row_cost:
            td["cogs"] += cogs_total
            if td["unit_cost"] is None:
                td["unit_cost"] = unit_cost
        else:
            td["has_cost"] = False

    def _build_tier_list(data: dict) -> list:
        result = []
        for name, td in sorted(data.items()):
            qty    = td["qty"]
            rev    = round(td["revenue"], 2)
            rev_ex = round(td["revenue_ex_vat"], 2)
            cogs   = round(td["cogs"], 2) if td["has_cost"] else None
            gp     = round(rev_ex - cogs, 2) if cogs is not None else None
            gpc    = round(gp / qty, 2) if (gp is not None and qty) else None
            result.append({
                "name":         name,
                "qty":          qty,
                "revenue":      rev,
                "gp":           gp,
                "gp_per_cover": gpc,
            })
        return result

    current_tiers = _build_tier_list(tier_data)
    current_gp    = sum(t["gp"] for t in current_tiers if t["gp"] is not None)

    # Simulate: shift shift_pct% of 590 qty → 799
    import copy
    sim_data = copy.deepcopy(tier_data)

    tier_590 = sim_data.get("590")
    tier_799 = sim_data.get("799")

    delta_gp   = 0.0
    delta_note = None

    if tier_590 and tier_799:
        shift_qty = tier_590["qty"] * (shift_pct / 100.0)

        # Revenue impact: shift_qty covers move from 590 tier price to 799 tier price
        avg_rev_590 = tier_590["revenue"] / tier_590["qty"] if tier_590["qty"] else 0
        avg_rev_799 = tier_799["revenue"] / tier_799["qty"] if tier_799["qty"] else 0
        avg_rev_ex_590 = tier_590["revenue_ex_vat"] / tier_590["qty"] if tier_590["qty"] else 0
        avg_rev_ex_799 = tier_799["revenue_ex_vat"] / tier_799["qty"] if tier_799["qty"] else 0

        # Adjust 590 tier
        tier_590["qty"]           -= shift_qty
        tier_590["revenue"]       -= shift_qty * avg_rev_590
        tier_590["revenue_ex_vat"] -= shift_qty * avg_rev_ex_590
        if tier_590["has_cost"] and tier_590["unit_cost"] is not None:
            tier_590["cogs"] -= shift_qty * tier_590["unit_cost"]

        # Adjust 799 tier
        tier_799["qty"]           += shift_qty
        tier_799["revenue"]       += shift_qty * avg_rev_799
        tier_799["revenue_ex_vat"] += shift_qty * avg_rev_ex_799
        if tier_799["has_cost"] and tier_799["unit_cost"] is not None:
            tier_799["cogs"] += shift_qty * tier_799["unit_cost"]
    else:
        delta_note = "590 or 799 tier data not found; simulation unchanged"

    simulated_tiers = _build_tier_list(sim_data)
    simulated_gp    = sum(t["gp"] for t in simulated_tiers if t["gp"] is not None)

    delta_gp  = round(simulated_gp - current_gp, 2)
    delta_pct = round(delta_gp / current_gp * 100, 2) if current_gp else None

    result = {
        "current":   {"tiers": current_tiers,   "total_gp": round(current_gp, 2)},
        "simulated": {"tiers": simulated_tiers,  "total_gp": round(simulated_gp, 2)},
        "delta_gp":  delta_gp,
        "delta_pct": delta_pct,
        "shift_pct": shift_pct,
    }
    if delta_note:
        result["note"] = delta_note
    return result


# ---------------------------------------------------------------------------
# Buffet Analytics Helpers
# ---------------------------------------------------------------------------

_BUFFET_ADULT_CODES = ("BUFFET", "BUFFET350", "BUFFET500", "BUFFET590", "BUFFET799", "B01", "B02", "B07", "B11", "B03")
_BUFFET_CHILD_CODES = ("B04", "B05", "B12", "BUFFETCHILD")
_BUFFET_PREMIUM_CODES = ("BUFFET590", "BUFFET799", "B02", "B11", "B03")
# ---------------------------------------------------------------------------
# GET /api/buffet/gp-calendar
# ---------------------------------------------------------------------------
@router.get("/api/buffet/gp-calendar")
@ttl_cache
def buffet_gp_calendar(
    branch: Optional[str] = Query(None),
    month:  str           = Query(...),
):
    """Daily GP calendar for a given YYYY-MM. Prorates monthly COGS/revenue to daily."""
    params: dict = {"month": month}

    # --- Daily revenue & tx from sales_transaction (all Easy receipts) -----
    # NOTE: do NOT restrict to table_no LIKE 'T%'/'G%' — in this dataset tables
    # are mostly numeric ("1","2","25, 26"…) and that filter dropped ~82% of
    # revenue, leaving the calendar near-empty and margins nonsensical. Using
    # all Easy receipts keeps the revenue base consistent with the all-SKU BoM
    # COGS below (both reconcile to ~120M ex-VAT / 54% food cost for 2026-05).
    day_filters = ["st.source = 'Easy'",
                   "SUBSTR(st.sold_at, 1, 7) = :month"]
    if branch:
        day_filters.append("b.name = :branch")
        params["branch"] = branch

    day_sql = f"""
        SELECT
            DATE(st.sold_at)          AS day,
            SUM(st.total_inc_vat)     AS revenue_inc,
            COUNT(*)                  AS tx_count,
            SUM(st.customer_count)    AS customer_count
        FROM sales_transaction st
        JOIN branches b ON b.branch_id = st.branch_id
        WHERE {' AND '.join(day_filters)}
        GROUP BY DATE(st.sold_at)
        ORDER BY DATE(st.sold_at)
    """

    # --- Monthly BoM COGS from sales_sku_monthly ---------------------------
    # Revenue comes from the actual dine-in receipts above (total_inc_vat ÷1.07),
    # so COGS must be the matching ALL-SKU BoM cost — NOT category='BUFFET'
    # (that bucket holds only the tier SKUs, whose COGS is deliberately 0 since
    #  consumption is logged on the item-level B0xxx rows). Filtering to BUFFET
    #  therefore returned null cogs and an inflated revenue base (~4×). We use
    #  _unified_cogs_expr over every SKU and pro-rate it onto days by revenue.
    mon_filters = ["sm.period_month = :month", "b.name != 'ALL'"]
    if branch:
        mon_filters.append("b.name = :branch")

    mon_sql = f"""
        SELECT SUM({_unified_cogs_expr(ssm='sm')}) AS cogs
        FROM sales_sku_monthly sm
        JOIN branches b ON b.branch_id = sm.branch_id
        WHERE {' AND '.join(mon_filters)}
    """

    with get_db() as con:
        day_rows  = con.execute(day_sql, params).fetchall()
        mon_row   = con.execute(mon_sql, params).fetchone()

    monthly_cogs = (mon_row["cogs"] or None) if mon_row else None

    total_rev_inc = sum(r["revenue_inc"] or 0.0 for r in day_rows)
    total_rev_ex  = round(total_rev_inc / 1.07, 2)

    days_out = []
    for r in day_rows:
        day_rev = r["revenue_inc"] or 0.0
        weight  = day_rev / total_rev_inc if total_rev_inc else 0.0

        day_rev_ex = round(day_rev / 1.07, 2)
        day_cogs   = round(monthly_cogs * weight, 2) if monthly_cogs is not None else None

        if day_cogs is not None and day_rev_ex:
            day_gp     = round(day_rev_ex - day_cogs, 2)
            margin_pct = round(day_gp / day_rev_ex * 100, 2)
        elif day_cogs is not None:
            day_gp, margin_pct = round(-day_cogs, 2), None
        else:
            day_gp, margin_pct = None, None

        days_out.append({
            "date":           r["day"],
            "revenue_inc":    round(day_rev, 2),
            "revenue_ex":     day_rev_ex,
            "cogs":           day_cogs,
            "gp":             day_gp,
            "margin_pct":     margin_pct,
            "tx_count":       r["tx_count"]      or 0,
            "customer_count": r["customer_count"] or 0,
        })

    # Monthly margin
    if monthly_cogs is not None and total_rev_ex:
        monthly_margin_pct = round((total_rev_ex - monthly_cogs) / total_rev_ex * 100, 2)
    else:
        monthly_margin_pct = None

    return {
        "month":               month,
        "days":                days_out,
        "monthly_margin_pct":  monthly_margin_pct,
    }


# ---------------------------------------------------------------------------
# GET /api/buffet/child-trend
# ---------------------------------------------------------------------------
@router.get("/api/buffet/child-trend")
@ttl_cache
def buffet_child_trend(
    branch:     Optional[str] = Query(None),
    month_from: Optional[str] = Query(None),
    month_to:   Optional[str] = Query(None),
):
    """Monthly adult/child pax split and GP% trend for BUFFET category.
    fix C2/C4: adult codes include 'BUFFET'; COGS from all-SKU query (item COGS on B0xxx rows).
    """
    filters: list = []
    params:  dict = {}
    if branch:
        filters.append("b.name = :branch")
        params["branch"] = branch
    _month_range_filter(filters, params, month_from, None, col="sm.period_month")
    _month_range_filter([], params, None, month_to, col="sm.period_month")
    # rebuild to_filter directly
    mf_filter = [f for f in filters]
    if month_to:
        mf_filter.append("sm.period_month <= :month_to")
        params["month_to"] = month_to

    where = ("WHERE " + " AND ".join(mf_filter)) if mf_filter else ""

    # Tier/pax query — BUFFET category for revenue and cover counts
    sql = f"""
        SELECT
            sm.period_month,
            sk.sku_code,
            sk.description,
            c.name          AS category,
            SUM(sm.qty)     AS qty,
            SUM(sm.subtotal_ex_vat) AS rev_ex
        FROM sales_sku_monthly sm
        JOIN branches   b  ON b.branch_id   = sm.branch_id
        JOIN skus       sk ON sk.sku_id     = sm.sku_id
        JOIN categories c  ON c.category_id = sk.category_id
        {where}
        GROUP BY sm.period_month, sk.sku_code, sk.description, c.name
        ORDER BY sm.period_month
    """

    # All-SKU COGS per month — item consumption is on B0xxx rows, not BUFFET category
    cogs_base_filters = [f for f in mf_filter if "period_month" in f or "b.name" in f]
    cogs_where = ("WHERE " + " AND ".join(cogs_base_filters + ["b.name != 'ALL'"])) if cogs_base_filters else "WHERE b.name != 'ALL'"
    cogs_sql = f"""
        SELECT sm.period_month, SUM({_unified_cogs_expr(ssm='sm')}) AS cogs
        FROM sales_sku_monthly sm
        JOIN branches b ON b.branch_id = sm.branch_id
        {cogs_where}
        GROUP BY sm.period_month
    """

    with get_db() as con:
        rows      = con.execute(sql,      params).fetchall()
        cogs_rows = con.execute(cogs_sql, params).fetchall()

    cogs_by_month = {r["period_month"]: (r["cogs"] or 0.0) for r in cogs_rows}

    # Aggregate per month
    from collections import defaultdict
    monthly: dict = defaultdict(lambda: {"adult": 0.0, "child": 0.0, "rev_ex": 0.0})

    for r in rows:
        mo   = r["period_month"]
        code = (r["sku_code"] or "").upper()
        desc = (r["description"] or "").lower()
        qty  = r["qty"] or 0.0
        cat  = r["category"] or ""

        is_child = (code in _BUFFET_CHILD_CODES or
                    "child" in desc or "เด็ก" in desc)
        is_adult = code in _BUFFET_ADULT_CODES

        if is_child:
            monthly[mo]["child"] += qty
        elif is_adult:
            monthly[mo]["adult"] += qty

        if cat == "BUFFET":
            monthly[mo]["rev_ex"] += r["rev_ex"] or 0.0

    result = []
    for mo in sorted(monthly):
        d          = monthly[mo]
        adult_pax  = d["adult"]
        child_pax  = d["child"]
        total_pax  = adult_pax + child_pax
        child_pct  = round(child_pax / total_pax * 100, 2) if total_pax else 0.0
        rev_ex     = d["rev_ex"]
        gp_pct     = None
        month_cogs = cogs_by_month.get(mo)
        if month_cogs is not None and rev_ex:
            gp         = rev_ex - month_cogs
            gp_pct     = round(gp / rev_ex * 100, 2)

        result.append({
            "period_month": mo,
            "adult_pax":    adult_pax,
            "child_pax":    child_pax,
            "child_pct":    child_pct,
            "gp_pct":       gp_pct,
        })

    return result


# ---------------------------------------------------------------------------
# GET /api/buffet/drink-attach-by-branch
# ---------------------------------------------------------------------------
@router.get("/api/buffet/drink-attach-by-branch")
@ttl_cache
def buffet_drink_attach_by_branch(
    month:      Optional[str] = Query(None),
    month_from: Optional[str] = Query(None),
    month_to:   Optional[str] = Query(None),
):
    """Drink attachment rates per branch from Easy POS transactions."""
    filters: list = ["st.source = 'Easy'", "b.name != 'ALL'"]
    params:  dict = {}
    _month_range_filter(filters, params, month_from, month_to, month,
                        col="SUBSTR(st.sold_at,1,7)")

    sql = f"""
        SELECT
            b.name                                  AS branch,
            COUNT(*)                                AS total_bills,
            SUM(CASE WHEN st.drink_subtotal > 0 THEN 1 ELSE 0 END) AS bills_with_drink,
            AVG(CASE WHEN st.drink_subtotal > 0 THEN st.drink_subtotal ELSE NULL END) AS avg_drink_per_bill,
            SUM(st.drink_subtotal)                  AS total_drink_revenue,
            SUM(st.total_inc_vat) / 1.07                   AS total_revenue
        FROM sales_transaction st
        JOIN branches b ON b.branch_id = st.branch_id
        WHERE {' AND '.join(filters)}
        GROUP BY b.name
        ORDER BY bills_with_drink * 1.0 / NULLIF(COUNT(*), 0) DESC
    """

    with get_db() as con:
        rows = con.execute(sql, params).fetchall()

    result = []
    for r in rows:
        total_bills       = r["total_bills"]        or 0
        bills_with_drink  = r["bills_with_drink"]   or 0
        attach_rate_pct   = round(bills_with_drink / total_bills * 100, 2) if total_bills else 0.0
        total_rev         = r["total_revenue"]       or 0.0
        total_drink       = r["total_drink_revenue"] or 0.0
        drink_rev_pct     = round(total_drink / total_rev * 100, 2) if total_rev else 0.0

        result.append({
            "branch":               r["branch"],
            "total_bills":          total_bills,
            "bills_with_drink":     bills_with_drink,
            "attach_rate_pct":      attach_rate_pct,
            "avg_drink_per_bill":   round(r["avg_drink_per_bill"] or 0.0, 2),
            "drink_revenue_pct":    drink_rev_pct,
            "total_drink_revenue":  round(total_drink, 2),
        })

    return result


# ---------------------------------------------------------------------------
# GET /api/buffet/breakeven
# ---------------------------------------------------------------------------
@router.get("/api/buffet/breakeven")
@ttl_cache
def buffet_breakeven(
    branch:               Optional[str]   = Query(None),
    month:                Optional[str]   = Query(None),
    month_from:           Optional[str]   = Query(None),
    month_to:             Optional[str]   = Query(None),
    monthly_fixed_cost:   float           = Query(0.0),
):
    """Breakeven analysis: covers needed to cover fixed costs given avg GP/cover."""
    # fix C2: revenue + pax from BUFFET tier SKUs; COGS from ALL SKUs (tier COGS is on B0xxx item rows)
    sku_filters:  list = ["c.name = 'BUFFET'", "b.name != 'ALL'"]
    cogs_filters: list = ["b.name != 'ALL'"]
    sku_params:   dict = {}
    if branch:
        sku_filters.append("b.name = :branch")
        cogs_filters.append("b.name = :branch")
        sku_params["branch"] = branch
    _month_range_filter(sku_filters, sku_params, month_from, month_to, month)
    _month_range_filter(cogs_filters, sku_params, month_from, month_to, month)

    sku_sql = f"""
        SELECT
            SUM(sm.qty)             AS total_pax,
            SUM(sm.subtotal_ex_vat) AS rev_ex
        FROM sales_sku_monthly sm
        JOIN branches   b  ON b.branch_id   = sm.branch_id
        JOIN skus       sk ON sk.sku_id     = sm.sku_id
        JOIN categories c  ON c.category_id = sk.category_id
        WHERE {' AND '.join(sku_filters)}
    """

    cogs_sql = f"""
        SELECT SUM({_unified_cogs_expr(ssm='sm')}) AS total_cogs
        FROM sales_sku_monthly sm
        JOIN branches b ON b.branch_id = sm.branch_id
        WHERE {' AND '.join(cogs_filters)}
    """

    tx_filters: list = ["st.source = 'Easy'"]
    tx_params:  dict = {}
    if branch:
        tx_filters.append("b.name = :branch")
        tx_params["branch"] = branch
    _month_range_filter(tx_filters, tx_params, month_from, month_to, month,
                        col="SUBSTR(st.sold_at,1,7)")

    tx_sql = f"""
        SELECT
            SUM(st.customer_count) AS total_covers,
            COUNT(DISTINCT SUBSTR(st.sold_at,1,10)) AS active_days
        FROM sales_transaction st
        JOIN branches b ON b.branch_id = st.branch_id
        WHERE {' AND '.join(tx_filters)} AND b.name != 'ALL'
    """

    with get_db() as con:
        sku_row  = con.execute(sku_sql,  sku_params).fetchone()
        cogs_row = con.execute(cogs_sql, sku_params).fetchone()
        tx_row   = con.execute(tx_sql,   tx_params).fetchone()

    total_pax  = (sku_row["total_pax"] or 0) if sku_row else 0
    rev_ex     = (sku_row["rev_ex"]    or 0.0) if sku_row else 0.0
    total_cogs = (cogs_row["total_cogs"]) if cogs_row else None

    avg_rev_per_cover = round(rev_ex / total_pax, 2) if total_pax else 0.0

    if total_cogs is not None and total_pax:
        gp_total         = rev_ex - total_cogs
        avg_gp_per_cover = round(gp_total / total_pax, 2)
    else:
        avg_gp_per_cover = None

    active_days       = (tx_row["active_days"]  or 1)  if tx_row else 1
    total_covers      = (tx_row["total_covers"] or 0)  if tx_row else 0
    actual_avg_covers = round(total_covers / active_days, 2) if active_days else 0.0

    if monthly_fixed_cost > 0 and avg_gp_per_cover:
        required_per_month = round(monthly_fixed_cost / avg_gp_per_cover, 2)
        required_per_day   = round(required_per_month / 30, 2)
        breakeven_util     = round(actual_avg_covers / required_per_day * 100, 2) if required_per_day else None
    else:
        required_per_month = None
        required_per_day   = None
        breakeven_util     = None

    return {
        "avg_gp_per_cover":           avg_gp_per_cover,
        "avg_revenue_per_cover":       avg_rev_per_cover,
        "monthly_fixed_cost":          monthly_fixed_cost,
        "required_covers_per_month":   required_per_month,
        "required_covers_per_day":     required_per_day,
        "actual_avg_covers_per_day":   actual_avg_covers,
        "breakeven_utilization_pct":   breakeven_util,
    }


# ---------------------------------------------------------------------------
# GET /api/buffet/forecast
# ---------------------------------------------------------------------------
@ttl_cache
def buffet_forecast(
    branch:     Optional[str] = Query(None),
    periods:    int           = Query(3, ge=1, le=12),
    month_from: Optional[str] = Query(None),
    month_to:   Optional[str] = Query(None),
):
    """Linear-regression forecast of buffet pax and revenue for future months."""
    filters: list = ["c.name = 'BUFFET'", "b.name != 'ALL'"]
    params:  dict = {}
    if branch:
        filters.append("b.name = :branch")
        params["branch"] = branch
    _month_range_filter(filters, params, month_from, month_to)

    sql = f"""
        SELECT
            sm.period_month,
            SUM(sm.qty)           AS pax,
            SUM(sm.subtotal_ex_vat) AS revenue
        FROM sales_sku_monthly sm
        JOIN branches   b  ON b.branch_id   = sm.branch_id
        JOIN skus       sk ON sk.sku_id     = sm.sku_id
        JOIN categories c  ON c.category_id = sk.category_id
        WHERE {' AND '.join(filters)}
        GROUP BY sm.period_month
        ORDER BY sm.period_month
    """

    with get_db() as con:
        rows = con.execute(sql, params).fetchall()

    if len(rows) < 3:
        raise HTTPException(400, "At least 3 historical data points are required for forecasting.")

    historical = [{"period": r["period_month"], "pax": r["pax"] or 0.0, "revenue": r["revenue"] or 0.0}
                  for r in rows]

    xs  = list(range(len(historical)))
    pax_ys  = [h["pax"]     for h in historical]
    rev_ys  = [h["revenue"] for h in historical]

    pax_slope,  pax_intercept,  pax_r2  = _ols_regression(xs, pax_ys)
    rev_slope,  rev_intercept,  rev_r2  = _ols_regression(xs, rev_ys)

    r_squared = round((pax_r2 + rev_r2) / 2, 4)

    # Trend from slope relative to mean
    pax_mean = sum(pax_ys) / len(pax_ys) if pax_ys else 1
    rel_slope = pax_slope / pax_mean if pax_mean else 0
    if rel_slope > 0.01:
        trend = "up"
    elif rel_slope < -0.01:
        trend = "down"
    else:
        trend = "flat"

    # Monthly growth rate (avg MoM %)
    mom_rates = []
    for i in range(1, len(pax_ys)):
        prev = pax_ys[i - 1]
        if prev:
            mom_rates.append((pax_ys[i] - prev) / prev * 100)
    monthly_growth_rate = round(sum(mom_rates) / len(mom_rates), 2) if mom_rates else 0.0

    confidence = "high" if r_squared > 0.7 else ("med" if r_squared > 0.4 else "low")

    last_period = historical[-1]["period"]
    forecast_out = []
    for i in range(1, periods + 1):
        xi  = len(historical) - 1 + i
        fpax  = max(0.0, pax_slope * xi + pax_intercept)
        frev  = max(0.0, rev_slope * xi + rev_intercept)
        fperiod = _next_period_month(last_period, i)
        forecast_out.append({
            "period":     fperiod,
            "pax":        round(fpax, 2),
            "revenue":    round(frev, 2),
            "confidence": confidence,
        })

    return {
        "historical":          historical,
        "forecast":            forecast_out,
        "r_squared":           r_squared,
        "trend":               trend,
        "monthly_growth_rate": monthly_growth_rate,
    }




@router.get("/api/buffet/performance")
@ttl_cache
def buffet_performance(
    branch:     Optional[str] = Query(None),
    group:      Optional[str] = Query(None),
    month_from: Optional[str] = Query(None),
    month_to:   Optional[str] = Query(None),
    food_cost_target_lo: float = Query(55.0),
    food_cost_target_hi: float = Query(70.0),
):
    """Comprehensive Buffet Performance metrics for the core all-you-can-eat business.

    Returns:
    - kpis: pax, bills, avg_pax_per_bill, premium_tier_pct, food_cost_pct, over_consumption_pct, beverage_attach_pct
    - tier_trend: per-month tier mix (BUFFET350/500/590/799 + CHILD)
    - food_cost_trend: monthly food_cost % vs target band
    - seat_util: per-branch daily pax vs network peak (proxy)
    - turnover:   per-branch bills/table/day (turnover proxy)
    - waste:      per-month over-consumption rate from OT0012/0013/0016
    - beverage_attach: per-month beverage revenue % of buffet bills
    - daypart_tier: lunch vs dinner tier mix
    - cohort:     per-branch performance bucketed by months-since-opening
    - pricing_rec: current weighted tier price vs revenue-optimal
    """
    # Optional master|franchise scope. grp_sql = literal fragment for the two
    # roll-up queries below that use no bound params (group is allow-listed first).
    if group not in ("master", "franchise"):
        group = None
    grp_sql = f" AND COALESCE(b.branch_type,'master') = '{group}'" if group else ""

    filters_sku = ["b.name != 'ALL'"]
    params_sku: dict = {}
    if branch:
        filters_sku.append("b.name = :branch")
        params_sku["branch"] = branch
    _group_filter(filters_sku, params_sku, group)
    _month_range_filter(filters_sku, params_sku, month_from, month_to, None, col="sm.period_month")
    where_sku = "WHERE " + " AND ".join(filters_sku)

    filters_tx = ["st.source='Easy'", "b.name != 'ALL'"]
    params_tx: dict = {}
    if branch:
        filters_tx.append("b.name = :branch")
        params_tx["branch"] = branch
    _group_filter(filters_tx, params_tx, group)
    _month_range_filter(filters_tx, params_tx, month_from, month_to, None, col="strftime('%Y-%m', st.sold_at)")
    where_tx = "WHERE " + " AND ".join(filters_tx)

    BUFFET_TIERS = ('BUFFET350', 'BUFFET500', 'BUFFET590', 'BUFFET799', 'BUFFETCHILD')
    WASTE_CODES  = ('OT0012', 'OT0013', 'OT0014', 'OT0015', 'OT0016')

    # Build named-param dicts for IN clauses so we can mix safely with named month params
    tier_named  = {f"bt{i}": v for i, v in enumerate(BUFFET_TIERS)}
    tier_ph     = ", ".join(f":bt{i}" for i in range(len(BUFFET_TIERS)))
    waste_named = {f"wc{i}": v for i, v in enumerate(WASTE_CODES)}
    waste_ph    = ", ".join(f":wc{i}" for i in range(len(WASTE_CODES)))

    with get_db() as con:
        # ── 1. Tier mix trend (monthly pax + revenue per tier) ────────────────
        tier_rows = con.execute(f"""
            SELECT sm.period_month AS m, sk.sku_code AS tier,
                   SUM(sm.qty)             AS pax,
                   SUM(sm.total_inc_vat)   AS rev_inc,
                   SUM(sm.subtotal_ex_vat) AS rev_ex
            FROM sales_sku_monthly sm
            JOIN skus sk      ON sk.sku_id    = sm.sku_id
            JOIN branches b   ON b.branch_id  = sm.branch_id
            {where_sku}
              AND sk.sku_code IN ({tier_ph})
            GROUP BY sm.period_month, sk.sku_code
            ORDER BY sm.period_month, sk.sku_code
        """, {**params_sku, **tier_named}).fetchall()

        # ── 2. Tier unit costs (for cost%) ────────────────────────────────────
        tier_cost = {}
        for r in con.execute(f"""
            SELECT sk.sku_code, sc.unit_cost
            FROM sku_cost sc JOIN skus sk ON sk.sku_id = sc.sku_id
            WHERE sk.sku_code IN ({tier_ph})
        """, tier_named).fetchall():
            tier_cost[r["sku_code"]] = r["unit_cost"]

        # Monthly tier-cost overrides (Phase 8): {(period_month, sku_code): unit_cost}
        tier_cost_monthly: dict = {}
        for r in con.execute(f"""
            SELECT scm.period_month AS m, sk.sku_code AS code, scm.unit_cost AS unit_cost
            FROM sku_cost_monthly scm JOIN skus sk ON sk.sku_id = scm.sku_id
            WHERE sk.sku_code IN ({tier_ph})
        """, tier_named).fetchall():
            tier_cost_monthly[(r["m"], r["code"])] = r["unit_cost"]

        # ── 3. Per-branch daily pax (for seat utilisation proxy) ──────────────
        daily_pax_rows = con.execute(f"""
            SELECT b.name AS branch,
                   DATE(st.sold_at) AS d,
                   SUM(st.customer_count) AS pax,
                   COUNT(*) AS bills,
                   COUNT(DISTINCT st.table_no) AS tables
            FROM sales_transaction st
            JOIN branches b ON b.branch_id = st.branch_id
            {where_tx}
              AND st.customer_count > 0
            GROUP BY b.name, DATE(st.sold_at)
        """, params_tx).fetchall()

        # ── 4. Waste / over-consumption (OT codes — kg fines for buffet leftovers) ────
        waste_rows = con.execute(f"""
            SELECT sm.period_month AS m,
                   SUM(sm.qty) AS qty,
                   SUM(sm.subtotal_ex_vat) AS rev
            FROM sales_sku_monthly sm
            JOIN skus sk ON sk.sku_id = sm.sku_id
            JOIN branches b ON b.branch_id = sm.branch_id
            {where_sku}
              AND sk.sku_code IN ({waste_ph})
            GROUP BY sm.period_month
        """, {**params_sku, **waste_named}).fetchall()

        # Buffet pax per month (for waste rate denominator)
        buffet_pax_rows = con.execute(f"""
            SELECT sm.period_month AS m, SUM(sm.qty) AS pax
            FROM sales_sku_monthly sm
            JOIN skus sk ON sk.sku_id = sm.sku_id
            JOIN branches b ON b.branch_id = sm.branch_id
            {where_sku}
              AND sk.sku_code IN ({tier_ph})
            GROUP BY sm.period_month
        """, {**params_sku, **tier_named}).fetchall()
        pax_by_month = {r["m"]: r["pax"] or 0 for r in buffet_pax_rows}

        # ── 5. Beverage attach rate (from drink_subtotal / total) ─────────────
        bev_rows = con.execute(f"""
            SELECT strftime('%Y-%m', st.sold_at) AS m,
                   SUM(st.drink_subtotal) AS drink_rev,
                   SUM(st.food_subtotal)  AS food_rev,
                   SUM(st.total_inc_vat) / 1.07 AS total_rev,  -- ex-VAT, consistent with drink/food subtotals
                   COUNT(*)               AS bills,
                   SUM(CASE WHEN st.drink_subtotal > 0 THEN 1 ELSE 0 END) AS bills_with_drink
            FROM sales_transaction st
            JOIN branches b ON b.branch_id = st.branch_id
            {where_tx}
            GROUP BY strftime('%Y-%m', st.sold_at)
            ORDER BY m
        """, params_tx).fetchall()

        # ── 6. Daypart tier mix (lunch vs dinner) ─────────────────────────────
        daypart_rows = con.execute(f"""
            SELECT CASE
                     WHEN CAST(strftime('%H', st.sold_at) AS INT) BETWEEN 11 AND 14 THEN 'lunch'
                     WHEN CAST(strftime('%H', st.sold_at) AS INT) BETWEEN 17 AND 22 THEN 'dinner'
                     ELSE 'other' END AS daypart,
                   CASE
                     WHEN (st.food_subtotal/st.customer_count) < 450 THEN 'BUFFET350'
                     WHEN (st.food_subtotal/st.customer_count) < 600 THEN 'BUFFET500'
                     WHEN (st.food_subtotal/st.customer_count) < 720 THEN 'BUFFET590'
                     ELSE 'BUFFET799' END AS tier,
                   SUM(st.customer_count) AS pax
            FROM sales_transaction st
            JOIN branches b ON b.branch_id = st.branch_id
            {where_tx}
              AND st.customer_count > 0 AND st.food_subtotal > 0
              AND (st.food_subtotal / st.customer_count) BETWEEN 380 AND 1000
            GROUP BY daypart, tier
        """, params_tx).fetchall()

        # ── 7. Branch opening month (cohort: months-since-open) ───────────────
        branch_open = con.execute(f"""
            SELECT b.name AS branch, MIN(sm.period_month) AS first_month
            FROM sales_sku_monthly sm
            JOIN branches b ON b.branch_id = sm.branch_id
            WHERE b.name != 'ALL'{grp_sql}
            GROUP BY b.name
        """).fetchall()
        branch_first = {r["branch"]: r["first_month"] for r in branch_open}

        # Per-branch monthly buffet revenue (for cohort curve)
        bcohort_rows = con.execute(f"""
            SELECT b.name AS branch, sm.period_month AS m,
                   SUM(sm.subtotal_ex_vat) AS rev,
                   SUM(sm.qty) AS pax
            FROM sales_sku_monthly sm
            JOIN skus sk ON sk.sku_id = sm.sku_id
            JOIN branches b ON b.branch_id = sm.branch_id
            WHERE b.name != 'ALL'{grp_sql}
              AND sk.sku_code IN ({tier_ph})
            GROUP BY b.name, sm.period_month
            ORDER BY b.name, sm.period_month
        """, tier_named).fetchall()

        # ── Real buffet food-cost trend (2026-06-02 fix) ──────────────────────
        # The old food_cost_trend multiplied tier pax × tier unit_cost, but the
        # per-tier rows (BUFFET350/500/…) carry no data — all adult buffet is on
        # code 'BUFFET', child on 'BUFFETCHILD', and the actual food consumed is
        # logged on the item-level rows (business_type='buffet'). So buffet food
        # cost = unified COGS of buffet items, revenue = the buffet_tier rows.
        bf_cost_rows = con.execute(f"""
            SELECT sm.period_month AS m,
                   SUM(CASE WHEN sk.business_type = 'buffet_tier'
                            THEN sm.subtotal_ex_vat ELSE 0 END)  AS rev_ex,
                   SUM({_unified_cogs_expr('sm', 'sk')})         AS cogs
            FROM sales_sku_monthly sm
            JOIN skus     sk ON sk.sku_id   = sm.sku_id
            JOIN branches b  ON b.branch_id = sm.branch_id
            {where_sku}
              AND sk.business_type IN ('buffet', 'buffet_tier')
            GROUP BY sm.period_month
            ORDER BY sm.period_month
        """, params_sku).fetchall()

    bf_cost_by_month = {r["m"]: (float(r["rev_ex"] or 0), float(r["cogs"] or 0))
                        for r in bf_cost_rows}

    # ── Adult vs Child split (real codes: BUFFET = adult, BUFFETCHILD = child) ─
    with get_db() as con:
        ac_rows = con.execute(f"""
            SELECT sk.sku_code AS code, SUM(sm.qty) AS qty,
                   SUM(sm.subtotal_ex_vat) AS rev
            FROM sales_sku_monthly sm
            JOIN skus sk     ON sk.sku_id   = sm.sku_id
            JOIN branches b  ON b.branch_id = sm.branch_id
            {where_sku}
              AND sk.sku_code IN ('BUFFET', 'BUFFETCHILD')
            GROUP BY sk.sku_code
        """, params_sku).fetchall()
    adult_pax = next((float(r["qty"] or 0) for r in ac_rows if r["code"] == 'BUFFET'), 0.0)
    child_pax = next((float(r["qty"] or 0) for r in ac_rows if r["code"] == 'BUFFETCHILD'), 0.0)
    adult_rev = next((float(r["rev"] or 0) for r in ac_rows if r["code"] == 'BUFFET'), 0.0)
    child_rev = next((float(r["rev"] or 0) for r in ac_rows if r["code"] == 'BUFFETCHILD'), 0.0)
    buffet_billed_pax = adult_pax + child_pax  # buffet covers sold (tier rows)
    child_pct = (child_pax / buffet_billed_pax * 100) if buffet_billed_pax else 0

    # ── Aggregate tier_trend (pax + cost%) ──────────────────────────────────
    from collections import defaultdict
    tier_trend = defaultdict(lambda: defaultdict(float))
    tier_rev   = defaultdict(lambda: defaultdict(float))
    for r in tier_rows:
        tier_trend[r["m"]][r["tier"]] = r["pax"] or 0
        tier_rev  [r["m"]][r["tier"]] = r["rev_ex"] or 0
    months = sorted(tier_trend.keys())

    # Tier list for chart
    tier_list = ['BUFFET350', 'BUFFET500', 'BUFFET590', 'BUFFET799', 'BUFFETCHILD']
    tier_trend_out = [{
        "period": mo,
        **{t: tier_trend[mo].get(t, 0) for t in tier_list},
        "total_pax": sum(tier_trend[mo].values()),
    } for mo in months]

    # ── Food cost trend (Buffet only — real unified COGS) ───────────────────
    # 2026-06-02: replaced the tier-pax × tier-unit-cost calc (which produced 0
    # because per-tier rows are empty) with the real buffet COGS computed above.
    fc_months = sorted(bf_cost_by_month.keys()) or months
    food_cost_trend = []
    for mo in fc_months:
        rev_ex, cost = bf_cost_by_month.get(mo, (0.0, 0.0))
        pct = (cost / rev_ex * 100) if rev_ex else None
        food_cost_trend.append({
            "period": mo, "revenue_ex": round(rev_ex, 0), "cost": round(cost, 0),
            "cost_pct": round(pct, 1) if pct is not None else None,
        })

    # ── Seat utilization proxy: per-branch avg pax/day vs network peak ──────
    branch_pax = defaultdict(list)
    branch_bills = defaultdict(list)
    branch_tables = defaultdict(list)
    for r in daily_pax_rows:
        branch_pax  [r["branch"]].append(r["pax"]   or 0)
        branch_bills[r["branch"]].append(r["bills"] or 0)
        branch_tables[r["branch"]].append(r["tables"] or 0)
    # Network peak = 90th percentile of daily pax across all branches
    all_daily_pax = sorted(p for vals in branch_pax.values() for p in vals)
    peak_pax = all_daily_pax[int(len(all_daily_pax) * 0.9)] if all_daily_pax else 1
    seat_util = []
    for br, vals in branch_pax.items():
        avg_pax = sum(vals) / len(vals) if vals else 0
        max_pax = max(vals) if vals else 0
        utilization = (avg_pax / peak_pax * 100) if peak_pax else 0
        seat_util.append({
            "branch": br,
            "avg_pax_per_day": round(avg_pax, 1),
            "max_pax_per_day": max_pax,
            "utilization_pct": round(utilization, 1),
        })
    seat_util.sort(key=lambda x: -x["utilization_pct"])

    # ── Turnover proxy: bills per table per day ─────────────────────────────
    turnover = []
    for br in branch_bills:
        bills_avg  = (sum(branch_bills[br])  / len(branch_bills[br]))  if branch_bills[br]  else 0
        tables_avg = (sum(branch_tables[br]) / len(branch_tables[br])) if branch_tables[br] else 0
        ratio = (bills_avg / tables_avg) if tables_avg > 0 else 0
        turnover.append({
            "branch":           br,
            "bills_per_day":    round(bills_avg, 1),
            "tables_per_day":   round(tables_avg, 1),
            "bills_per_table":  round(ratio, 2),
        })
    turnover.sort(key=lambda x: -x["bills_per_table"])

    # ── Waste / over-consumption rate ───────────────────────────────────────
    waste_trend = []
    for r in waste_rows:
        mo = r["m"]
        pax = pax_by_month.get(mo, 0) or 0
        rate = (r["qty"] / pax * 100) if pax > 0 else 0
        waste_trend.append({
            "period":   mo,
            "qty":      r["qty"] or 0,
            "revenue":  r["rev"] or 0,
            "rate_pct": round(rate, 2),  # # of fine charges per 100 pax
        })
    # Fill in missing months with 0
    waste_periods = {w["period"] for w in waste_trend}
    for mo in months:
        if mo not in waste_periods:
            waste_trend.append({"period": mo, "qty": 0, "revenue": 0, "rate_pct": 0})
    waste_trend.sort(key=lambda x: x["period"])

    # ── Beverage attach ─────────────────────────────────────────────────────
    bev_trend = []
    for r in bev_rows:
        total = r["total_rev"] or 0
        drink = r["drink_rev"] or 0
        attach_bills = (r["bills_with_drink"] or 0) / (r["bills"] or 1) * 100
        attach_rev   = (drink / total * 100) if total else 0
        bev_trend.append({
            "period":         r["m"],
            "drink_revenue":  round(drink, 0),
            "total_revenue":  round(total, 0),
            "attach_pct":     round(attach_rev, 1),
            "bills_attach_pct": round(attach_bills, 1),
        })

    # ── Daypart tier mix ────────────────────────────────────────────────────
    daypart_mix = defaultdict(lambda: defaultdict(int))
    for r in daypart_rows:
        daypart_mix[r["daypart"]][r["tier"]] += r["pax"] or 0
    daypart_out = []
    for dp in ('lunch', 'dinner', 'other'):
        tot = sum(daypart_mix[dp].values()) or 1
        daypart_out.append({
            "daypart": dp,
            "total_pax": sum(daypart_mix[dp].values()),
            **{t: round(daypart_mix[dp].get(t, 0) / tot * 100, 1) for t in tier_list},
        })

    # ── Branch cohort: months-since-open vs revenue/pax ─────────────────────
    from datetime import datetime as _dt
    def months_diff(a, b):
        try:
            da = _dt.strptime(a, "%Y-%m"); db = _dt.strptime(b, "%Y-%m")
            return (db.year - da.year) * 12 + (db.month - da.month)
        except Exception:
            return None

    cohort = defaultdict(list)  # months_since_open -> [revenues]
    for r in bcohort_rows:
        first = branch_first.get(r["branch"])
        if not first: continue
        m_diff = months_diff(first, r["m"])
        if m_diff is None or m_diff < 0 or m_diff > 24: continue
        cohort[m_diff].append({"revenue": r["rev"] or 0, "pax": r["pax"] or 0})
    cohort_curve = []
    for m_since in sorted(cohort.keys()):
        vals = cohort[m_since]
        cohort_curve.append({
            "months_since_open": m_since,
            "avg_revenue":  round(sum(v["revenue"] for v in vals) / len(vals), 0),
            "avg_pax":      round(sum(v["pax"] for v in vals) / len(vals), 0),
            "branch_count": len(vals),
        })

    # ── Pricing recommendation ──────────────────────────────────────────────
    TIER_PRICES = {'BUFFET350': 350, 'BUFFET500': 500, 'BUFFET590': 590, 'BUFFET799': 799, 'BUFFETCHILD': 295}
    # fix E6: use actual food-cost ratio from BoM data instead of raw sku_cost
    # (sku_cost.unit_cost ≈ selling price for tier SKUs, not ingredient cost)
    _fc_rev_total  = sum(v[0] for v in bf_cost_by_month.values())
    _fc_cogs_total = sum(v[1] for v in bf_cost_by_month.values())
    _food_cost_ratio = (_fc_cogs_total / _fc_rev_total) if _fc_rev_total > 0 else 0.40
    # Overall actual tier mix (across all months)
    actual_pax = {t: 0 for t in tier_list}
    for mo_data in tier_trend.values():
        for t, p in mo_data.items():
            actual_pax[t] += p
    total_pax = sum(actual_pax.values()) or 1
    actual_mix_pct = {t: actual_pax[t] / total_pax * 100 for t in tier_list}
    # Revenue contribution per tier (cost from real food-cost ratio, not sku_cost)
    tier_revenue_actual = {}
    tier_margin_actual = {}
    for t in tier_list:
        rev  = TIER_PRICES[t] * actual_pax[t]
        cost = TIER_PRICES[t] * _food_cost_ratio * actual_pax[t]
        tier_revenue_actual[t] = rev
        tier_margin_actual[t]  = rev - cost
    actual_total_rev    = sum(tier_revenue_actual.values())
    actual_total_margin = sum(tier_margin_actual.values())
    actual_margin_pct   = (actual_total_margin / actual_total_rev * 100) if actual_total_rev else 0

    # Optimal mix: shift 10% of 350/500 pax to 590, and 5% to 799 (conservative up-sell scenario)
    target_pax = dict(actual_pax)
    shift_350 = actual_pax.get('BUFFET350', 0) * 0.30  # 30% of 350 customers upgrade
    shift_500 = actual_pax.get('BUFFET500', 0) * 0.30
    target_pax['BUFFET350'] -= shift_350
    target_pax['BUFFET500'] -= shift_500
    target_pax['BUFFET590'] += shift_350 * 0.7 + shift_500 * 0.7
    target_pax['BUFFET799'] += shift_350 * 0.3 + shift_500 * 0.3
    target_revenue = sum(TIER_PRICES[t] * target_pax[t] for t in tier_list)
    target_cost    = sum(TIER_PRICES[t] * _food_cost_ratio * target_pax[t] for t in tier_list)
    target_margin  = target_revenue - target_cost
    target_margin_pct = (target_margin / target_revenue * 100) if target_revenue else 0
    margin_gain = target_margin - actual_total_margin
    revenue_gain = target_revenue - actual_total_rev

    pricing_rec = {
        "current_mix": {t: round(actual_mix_pct[t], 1) for t in tier_list},
        "target_mix":  {t: round(target_pax[t] / total_pax * 100, 1) for t in tier_list},
        "current_revenue": round(actual_total_rev, 0),
        "current_margin":  round(actual_total_margin, 0),
        "current_margin_pct": round(actual_margin_pct, 2),
        "target_revenue":  round(target_revenue, 0),
        "target_margin":   round(target_margin, 0),
        "target_margin_pct": round(target_margin_pct, 2),
        "revenue_gain":    round(revenue_gain, 0),
        "margin_gain":     round(margin_gain, 0),
        "assumption":      "30% ของลูกค้า tier 350/500 ขยับขึ้นไป 590 (70%) และ 799 (30%)",
    }

    # ── KPI summary ─────────────────────────────────────────────────────────
    total_buffet_pax = sum(actual_pax.values())
    premium_pax = actual_pax.get('BUFFET590', 0) + actual_pax.get('BUFFET799', 0)
    premium_pct = (premium_pax / total_buffet_pax * 100) if total_buffet_pax else 0
    # Per-tier % split (350/500/590/799/CHILD individually)
    tier_pct_split = {
        t: round(actual_pax.get(t, 0) / total_buffet_pax * 100, 1) if total_buffet_pax else 0
        for t in ('BUFFET350', 'BUFFET500', 'BUFFET590', 'BUFFET799', 'BUFFETCHILD')
    }
    waste_total_qty = sum(w["qty"] for w in waste_trend)
    waste_rate_overall = (waste_total_qty / total_buffet_pax * 100) if total_buffet_pax else 0
    bev_total = sum(b["drink_revenue"] for b in bev_trend)
    total_billed = sum(b["total_revenue"] for b in bev_trend)
    bev_attach_overall = (bev_total / total_billed * 100) if total_billed else 0
    # Bill-level attach: % of bills that ordered a drink (the actionable headline,
    # vs revenue-share which buffet dominates). From raw bev_rows counts.
    _bev_bills = sum((r["bills"] or 0) for r in bev_rows)
    _bev_bills_drink = sum((r["bills_with_drink"] or 0) for r in bev_rows)
    bev_bill_attach_overall = (_bev_bills_drink / _bev_bills * 100) if _bev_bills else 0
    food_cost_overall = sum(f["cost"] for f in food_cost_trend)
    rev_ex_overall    = sum(f["revenue_ex"] for f in food_cost_trend)
    food_cost_pct_overall = (food_cost_overall / rev_ex_overall * 100) if rev_ex_overall else 0
    total_bills = sum(sum(b) for b in branch_bills.values())

    # ── Phase 12: Combined whole-restaurant numbers (buffet + à la carte,
    # all SKUs, applies source-aware COGS rule). This is what the user wants
    # to see in the KPI hero row — one overall picture, not split by variant.
    combined_filters = ["b.name != 'ALL'"]
    combined_params: dict = {}
    if branch:
        combined_filters.append("b.name = :branch")
        combined_params["branch"] = branch
    _group_filter(combined_filters, combined_params, group)
    _month_range_filter(combined_filters, combined_params, month_from, month_to, None, col="ssm.period_month")
    combined_where = "WHERE " + " AND ".join(combined_filters)
    with get_db() as con:
        crow = con.execute(f"""
            SELECT
              COALESCE(SUM(ssm.subtotal_ex_vat), 0)        AS rev_ex,
              COALESCE(SUM(ssm.total_inc_vat),  0)        AS rev_inc,
              COALESCE(SUM({_cogs_case('ssm','sk','sc')}), 0)        AS cogs,
              COALESCE(SUM({_costed_rev_case('ssm','sk','sc')}), 0)  AS costed_rev_ex
              FROM sales_sku_monthly ssm
              JOIN branches b   ON b.branch_id = ssm.branch_id
              JOIN skus     sk  ON sk.sku_id   = ssm.sku_id
              LEFT JOIN sku_cost sc ON sc.sku_id = ssm.sku_id
            {combined_where}
        """, combined_params).fetchone()
    c_rev_ex      = float(crow["rev_ex"] or 0)
    c_rev_inc     = float(crow["rev_inc"] or 0)
    c_cogs        = float(crow["cogs"] or 0)
    c_costed_rev  = float(crow["costed_rev_ex"] or 0)   # coverage diagnostic only
    c_gp          = c_rev_ex - c_cogs
    c_gp_pct      = (c_gp   / c_rev_ex * 100) if c_rev_ex > 0 else None
    c_food_pct    = (c_cogs / c_rev_ex * 100) if c_rev_ex > 0 else None

    # Real pax = customer_count from sales_transaction (the trustworthy headcount),
    # not the tier-row qty which only counts buffet covers sold.
    real_total_pax = sum((r["pax"] or 0) for r in daily_pax_rows)
    waste_rate_real = (waste_total_qty / buffet_billed_pax * 100) if buffet_billed_pax else 0

    kpis = {
        "total_pax":             int(real_total_pax),
        "total_bills":           int(total_bills),
        "avg_pax_per_bill":      round(real_total_pax / total_bills, 2) if total_bills else 0,
        # Adult vs Child (real buffet codes) — replaces the empty tier split
        "adult_pax":             int(adult_pax),
        "child_pax":             int(child_pax),
        "child_pct":             round(child_pct, 1),
        "buffet_food_cost_pct":  round(food_cost_pct_overall, 1),  # buffet-only, real COGS
        "premium_tier_pct":      round(premium_pct, 1),
        "premium_tier_pct_target": round(
            (pricing_rec["target_mix"].get("BUFFET590", 0)
             + pricing_rec["target_mix"].get("BUFFET799", 0)),
            1,
        ),
        "food_cost_pct":         round(food_cost_pct_overall, 1),
        "food_cost_target_lo":   food_cost_target_lo,
        "food_cost_target_hi":   food_cost_target_hi,
        "over_consumption_pct":  round(waste_rate_real, 2),
        "beverage_attach_pct":   round(bev_attach_overall, 1),
        "beverage_bill_attach_pct": round(bev_bill_attach_overall, 1),
        "branch_count":          len(branch_pax),
        # ── Combined whole-restaurant (Phase 12) ──────────────────────────
        "combined_revenue_ex_vat":  round(c_rev_ex, 2),
        "combined_pos_total_inc_vat_diagnostic": round(c_rev_inc, 2),
        "combined_cogs":            round(c_cogs, 2),
        "combined_gross_profit":    round(c_gp, 2),
        "combined_gp_pct":          round(c_gp_pct, 1)   if c_gp_pct   is not None else None,
        "combined_food_cost_pct":   round(c_food_pct, 1) if c_food_pct is not None else None,
    }

    return {
        "kpis":            kpis,
        "tier_pct_split":  tier_pct_split,
        "tier_trend":      tier_trend_out,
        "food_cost_trend": food_cost_trend,
        "seat_util":       seat_util,
        "turnover":        turnover,
        "waste":           waste_trend,
        "beverage":        bev_trend,
        "daypart_tier":    daypart_out,
        "cohort":          cohort_curve,
        "pricing_rec":     pricing_rec,
    }




# ---------------------------------------------------------------------------
# GET /api/buffet/gp
# Gross Profit from BoM — revenue vs actual item-level cost for the period
# ---------------------------------------------------------------------------
@router.get("/api/buffet/gp")
@ttl_cache
def buffet_gp(
    branch:      Optional[str] = Query(None),
    month_from:  Optional[str] = Query(None),
    month_to:    Optional[str] = Query(None),
    granularity: str           = Query("month"),
):
    """Buffet Gross Profit from BoM cost.

    Revenue = sum(subtotal_ex_vat) from sales_sku_monthly.
    BoM Cost = sum(qty * unit_cost) for SKUs that have cost data.
    For week/day granularity, BoM cost is pro-rated by daily revenue fraction
    (since SKU data is monthly-only — no day-level SKU breakdown available).

    Returns: period_total, series (bucketed), by_branch, top_cost_skus, waterfall.
    """
    if granularity not in ("day", "week", "month"):
        granularity = "month"

    DRINK_CATS = ("Oshinei ALCOHOL", "Oshinei DRINKING", "Oshinei SOFT DRINK",
                  "(YAKI)SOFT DRINK")

    # ── Build SKU-level filters (period_month column) ─────────────────────
    sku_filters: list = ["b.name != 'ALL'"]
    sku_params:  dict = {}
    if branch:
        sku_filters.append("b.name = :branch")
        sku_params["branch"] = branch
    _month_range_filter(sku_filters, sku_params, month_from, month_to, None,
                        col="sm.period_month")
    where_sku = "WHERE " + " AND ".join(sku_filters)

    # ── Build transaction-level filters ───────────────────────────────────
    tx_filters: list = ["b.name != 'ALL'", "st.source = 'Easy'"]
    tx_params:  dict = {}
    if branch:
        tx_filters.append("b.name = :branch")
        tx_params["branch"] = branch
    _month_range_filter(tx_filters, tx_params, month_from, month_to, None,
                        col="SUBSTR(st.sold_at,1,7)")
    where_tx = "WHERE " + " AND ".join(tx_filters)

    with get_db() as con:
        # BoM cost rule (Phase 0 fix):
        #   Exclude Easy tier SKUs (BUFFET350/500/590/799/CHILD) — their cost is
        #   captured at item level (B0xxx rows logged with qty>0, revenue=0).
        #   Including tier unit_cost would double-count.
        bom_cost_sql = _cogs_case(ssm="sm")
        # Uncovered = item SKU without cost (Easy tier SKUs are intentionally
        # treated as "covered via items" so they don't count as uncovered).
        uncovered_sql = (
            f"CASE WHEN sm.source = 'Easy' AND sk.sku_code IN {_EASY_TIER_CODES_SQL} THEN NULL "
            f"     WHEN sc.unit_cost IS NULL THEN sm.sku_id ELSE NULL END"
        )

        # ── 1. Monthly BoM cost by (branch, period_month, category_group) ─
        monthly_bom_rows = con.execute(f"""
            SELECT
                b.name           AS branch,
                sm.period_month  AS m,
                c.name           AS cat,
                sk.sku_code      AS sku_code,
                SUM(sm.qty)      AS qty,
                SUM(sm.subtotal_ex_vat) AS rev,
                SUM({bom_cost_sql}) AS bom_cost,
                COUNT(DISTINCT {uncovered_sql}) AS uncovered
            FROM sales_sku_monthly sm
            JOIN branches   b  ON b.branch_id   = sm.branch_id
            JOIN skus       sk ON sk.sku_id      = sm.sku_id
            JOIN categories c  ON c.category_id  = sk.category_id
            LEFT JOIN sku_cost sc ON sc.sku_id   = sm.sku_id
            {where_sku}
            GROUP BY b.name, sm.period_month, c.name, sk.sku_code
            ORDER BY b.name, sm.period_month
        """, sku_params).fetchall()

        # ── 2. Daily revenue from transactions (for day/week pro-rate) ────
        daily_tx_rows = con.execute(f"""
            SELECT
                b.name                           AS branch,
                DATE(st.sold_at)                 AS d,
                SUBSTR(st.sold_at, 1, 7)         AS m,
                SUM(st.customer_count)           AS covers,
                SUM(COALESCE(st.food_subtotal,0)
                  + COALESCE(st.drink_subtotal,0)
                  + COALESCE(st.other_subtotal,0)) AS rev_tx
            FROM sales_transaction st
            JOIN branches b ON b.branch_id = st.branch_id
            {where_tx}
            GROUP BY b.name, DATE(st.sold_at)
        """, tx_params).fetchall()

    # ── 3. Aggregate monthly totals (branch × month) ──────────────────────
    from collections import defaultdict

    # monthly_agg[branch][month] = {rev, bom_cost, bom_buffet, bom_food, bom_bev, rev_buffet, rev_food, rev_bev, uncovered, sku details}
    monthly_agg: dict = defaultdict(lambda: defaultdict(lambda: {
        "rev": 0.0, "bom_cost": 0.0,
        "bom_buffet": 0.0, "bom_food": 0.0, "bom_bev": 0.0,
        "rev_buffet": 0.0, "rev_food": 0.0, "rev_bev": 0.0,
        "uncovered": 0,
    }))
    # sku-level detail for top_cost_skus
    sku_agg: dict = defaultdict(lambda: {
        "description": "", "category": "", "qty": 0.0,
        "bom_cost": 0.0, "rev": 0.0,
    })

    for r in monthly_bom_rows:
        br, m, cat = r["branch"], r["m"], r["cat"]
        a = monthly_agg[br][m]
        a["rev"]      += r["rev"] or 0.0
        a["bom_cost"] += r["bom_cost"] or 0.0
        a["uncovered"] = max(a["uncovered"], r["uncovered"] or 0)

        is_buffet_tier = r["sku_code"] and r["sku_code"].startswith("BUFFET")
        is_drink = cat in DRINK_CATS

        if is_buffet_tier:
            a["bom_buffet"] += r["bom_cost"] or 0.0
            a["rev_buffet"] += r["rev"] or 0.0
        elif is_drink:
            a["bom_bev"] += r["bom_cost"] or 0.0
            a["rev_bev"] += r["rev"] or 0.0
        else:
            a["bom_food"] += r["bom_cost"] or 0.0
            a["rev_food"] += r["rev"] or 0.0

        sc = sku_agg[r["sku_code"]]
        sc["description"] = r["sku_code"]  # placeholder; description not in this query
        sc["category"]    = cat
        sc["qty"]         += r["qty"] or 0.0
        sc["bom_cost"]    += r["bom_cost"] or 0.0
        sc["rev"]         += r["rev"] or 0.0

    # ── 4. daily_tx index: [branch][month][date] ──────────────────────────
    daily_tx: dict = defaultdict(lambda: defaultdict(dict))
    monthly_rev_tx: dict = defaultdict(lambda: defaultdict(float))
    monthly_covers_tx: dict = defaultdict(lambda: defaultdict(int))

    for r in daily_tx_rows:
        daily_tx[r["branch"]][r["m"]][r["d"]] = r["rev_tx"] or 0.0
        monthly_rev_tx[r["branch"]][r["m"]]  += r["rev_tx"] or 0.0
        monthly_covers_tx[r["branch"]][r["m"]] += r["covers"] or 0

    # ── 5. Build series ───────────────────────────────────────────────────
    def week_bucket(date_str: str) -> str:
        from datetime import date as dt
        d = dt.fromisoformat(date_str)
        iso = d.isocalendar()
        return f"{iso[0]}-W{iso[1]:02d}"

    if granularity == "month":
        bucket_agg: dict = defaultdict(lambda: {"rev": 0.0, "bom_cost": 0.0, "covers": 0})
        for br in monthly_agg:
            for m, a in monthly_agg[br].items():
                b = bucket_agg[m]
                b["rev"]      += a["rev"]
                b["bom_cost"] += a["bom_cost"]
                b["covers"]   += monthly_covers_tx[br][m]
        series = []
        for bucket in sorted(bucket_agg.keys()):
            b = bucket_agg[bucket]
            gp = round(b["rev"] - b["bom_cost"], 2)
            series.append({
                "bucket":   bucket,
                "revenue":  round(b["rev"], 2),
                "bom_cost": round(b["bom_cost"], 2),
                "gp":       gp,
                "margin_pct": round(gp / b["rev"] * 100, 2) if b["rev"] else None,
                "covers":   b["covers"],
            })
    else:
        # Day or week: pro-rate monthly BoM by daily revenue fraction
        # fix F7: pre-index covers to avoid O(days × tx_rows) linear rescan
        daily_covers_idx = {(r2["branch"], r2["d"]): (r2["covers"] or 0) for r2 in daily_tx_rows}
        daily_bucket: dict = defaultdict(lambda: {"rev": 0.0, "bom_cost": 0.0, "covers": 0})
        for br, months in daily_tx.items():
            for m, days in months.items():
                m_rev_tx = monthly_rev_tx[br][m]
                m_bom    = sum(monthly_agg[br][mm]["bom_cost"] for mm in monthly_agg[br] if mm == m)
                for d, d_rev_tx in days.items():
                    fraction   = (d_rev_tx / m_rev_tx) if m_rev_tx else 0.0
                    d_bom_cost = m_bom * fraction
                    bucket = d if granularity == "day" else week_bucket(d)
                    db = daily_bucket[bucket]
                    db["rev"]      += d_rev_tx
                    db["bom_cost"] += d_bom_cost
                    db["covers"]   += daily_covers_idx.get((br, d), 0)
        series = []
        for bucket in sorted(daily_bucket.keys()):
            b = daily_bucket[bucket]
            gp = round(b["rev"] - b["bom_cost"], 2)
            series.append({
                "bucket":     bucket,
                "revenue":    round(b["rev"], 2),
                "bom_cost":   round(b["bom_cost"], 2),
                "gp":         gp,
                "margin_pct": round(gp / b["rev"] * 100, 2) if b["rev"] else None,
                "covers":     b["covers"],
            })

    # ── 6. Period totals (all branches, whole selected period) ───────────
    period_rev      = sum(s["revenue"]  for s in series)
    period_bom      = sum(s["bom_cost"] for s in series)
    period_covers   = sum(s["covers"]   for s in series) if granularity == "month" else \
                      sum(monthly_covers_tx[br][m] for br in monthly_covers_tx for m in monthly_covers_tx[br])
    period_gp       = round(period_rev - period_bom, 2)
    period_margin   = round(period_gp / period_rev * 100, 2) if period_rev else None
    cost_per_cover  = round(period_bom / period_covers, 2) if period_covers else None

    # ── 7. By branch (whole period) ───────────────────────────────────────
    branch_totals: dict = defaultdict(lambda: {
        "rev": 0.0, "bom_cost": 0.0, "covers": 0
    })
    for br in monthly_agg:
        for m, a in monthly_agg[br].items():
            bt = branch_totals[br]
            bt["rev"]      += a["rev"]
            bt["bom_cost"] += a["bom_cost"]
            bt["covers"]   += monthly_covers_tx[br][m]

    network_avg_margin = period_margin or 0.0
    by_branch = []
    for br, bt in sorted(branch_totals.items(), key=lambda x: x[1]["rev"], reverse=True):
        gp_br   = round(bt["rev"] - bt["bom_cost"], 2)
        margin  = round(gp_br / bt["rev"] * 100, 2) if bt["rev"] else None
        gp_cov  = round(gp_br / bt["covers"], 2) if bt["covers"] else None
        diff_pp = round(margin - network_avg_margin, 2) if margin is not None else None
        by_branch.append({
            "branch":       br,
            "covers":       bt["covers"],
            "revenue":      round(bt["rev"], 2),
            "bom_cost":     round(bt["bom_cost"], 2),
            "gp":           gp_br,
            "gp_per_cover": gp_cov,
            "margin_pct":   margin,
            "vs_network_pp": diff_pp,
        })

    # ── 8. Top 20 cost-driver SKUs (exclude tier seats) ──────────────────
    total_bom = period_bom or 1.0
    top_cost_skus = sorted(
        [{"sku_code": k, **v} for k, v in sku_agg.items()
         if v["bom_cost"] > 0 and k not in _ALL_TIER_CODES and not k.startswith("BUFFET")],
        key=lambda x: x["bom_cost"], reverse=True
    )[:20]
    for s in top_cost_skus:
        s["bom_cost"]  = round(s["bom_cost"], 2)
        s["rev"]       = round(s["rev"], 2)
        s["qty"]       = round(s["qty"], 2)
        s["cost_pct"]  = round(s["bom_cost"] / total_bom * 100, 2)

    # ── 9. Waterfall (whole period, all branches) ─────────────────────────
    total_rev_buffet = sum(monthly_agg[br][m]["rev_buffet"]  for br in monthly_agg for m in monthly_agg[br])
    total_rev_food   = sum(monthly_agg[br][m]["rev_food"]    for br in monthly_agg for m in monthly_agg[br])
    total_rev_bev    = sum(monthly_agg[br][m]["rev_bev"]     for br in monthly_agg for m in monthly_agg[br])
    total_bom_buffet = sum(monthly_agg[br][m]["bom_buffet"]  for br in monthly_agg for m in monthly_agg[br])
    total_bom_food   = sum(monthly_agg[br][m]["bom_food"]    for br in monthly_agg for m in monthly_agg[br])
    total_bom_bev    = sum(monthly_agg[br][m]["bom_bev"]     for br in monthly_agg for m in monthly_agg[br])

    waterfall = {
        "revenue":       round(period_rev, 2),
        "rev_buffet":    round(total_rev_buffet, 2),
        "rev_food":      round(total_rev_food, 2),
        "rev_bev":       round(total_rev_bev, 2),
        "bom_buffet":    round(total_bom_buffet, 2),
        "bom_food":      round(total_bom_food, 2),
        "bom_bev":       round(total_bom_bev, 2),
        "bom_total":     round(period_bom, 2),
        "gp":            round(period_gp, 2),
    }

    return {
        "period_total": {
            "revenue":       round(period_rev, 2),
            "bom_cost":      round(period_bom, 2),
            "gp":            period_gp,
            "margin_pct":    period_margin,
            "covers":        period_covers,
            "cost_per_cover": cost_per_cover,
        },
        "granularity": granularity,
        "series":       series,
        "by_branch":    by_branch,
        "top_cost_skus": top_cost_skus,
        "waterfall":    waterfall,
    }




# ---------------------------------------------------------------------------
# GET /api/buffet/menu-cost-efficiency   (Phase 6, 2026-06-02)
# Per-dish: servings, % of total servings, BoM cost, % of total cost,
# cost/serving. The key buffet question: which dishes are served a LOT and cost a
# LOT (→ re-engineer recipe / portion to cut cost), vs served a lot but cheap
# (→ promote). Per-branch capable.
# ---------------------------------------------------------------------------
@router.get("/api/buffet/menu-cost-efficiency")
@ttl_cache
def buffet_menu_cost_efficiency(
    branch:     Optional[str] = Query(None),
    month:      Optional[str] = Query(None),
    month_from: Optional[str] = Query(None),
    month_to:   Optional[str] = Query(None),
    limit:      int           = Query(80, ge=10, le=300),
):
    """For every food dish: servings (qty), cost (unified COGS), and each as a %
    of the total — so you can target HIGH-volume × HIGH-cost dishes for cost
    reduction. Buffet tier rows excluded (cost lives on the item dishes)."""
    filters = ["b.name != 'ALL'", "ssm.source = 'Easy'", _FOOD_CATEGORY_FILTER]
    params: dict = {}
    if branch:
        filters.append("b.name = :branch"); params["branch"] = branch
    _month_range_filter(filters, params, month_from, month_to, month, col="ssm.period_month")
    where = "WHERE " + " AND ".join(filters)
    with get_db() as con:
        rows = con.execute(f"""
            SELECT sk.sku_code, COALESCE(sk.description, sk.sku_code) AS description,
                   c.name AS category,
                   SUM(ssm.qty)               AS servings,
                   SUM({_unified_cogs_expr('ssm')}) AS cost,
                   SUM(ssm.subtotal_ex_vat)   AS revenue,
                   CASE
                     WHEN EXISTS (SELECT 1 FROM bom_ingredients bi WHERE bi.sku_id = sk.sku_id) THEN 'bom'
                     -- alias-resolved recipe, matching _unified_cogs_expr (see menu-cost-coverage)
                     WHEN EXISTS (SELECT 1 FROM bom_sku_alias a JOIN bom_ingredients bi2
                                    ON bi2.sku_id = a.resolved_sku_id
                                  WHERE a.bom_sku_code = sk.sku_code) THEN 'bom'
                     WHEN EXISTS (SELECT 1 FROM sku_cost sc WHERE sc.sku_id = sk.sku_id) THEN 'sku_cost'
                     ELSE 'none'
                   END AS cost_source
            FROM sales_sku_monthly ssm
            JOIN branches b   ON b.branch_id   = ssm.branch_id
            JOIN skus sk      ON sk.sku_id      = ssm.sku_id
            JOIN categories c ON c.category_id  = sk.category_id
            {where}
            GROUP BY sk.sku_id
            HAVING SUM(ssm.qty) > 0
        """, params).fetchall()
    tot_serv = sum((r["servings"] or 0) for r in rows) or 1
    tot_cost = sum((r["cost"] or 0) for r in rows) or 1
    items = []
    for r in rows:
        serv = r["servings"] or 0; cost = r["cost"] or 0
        serv_pct = serv / tot_serv * 100
        cost_pct = cost / tot_cost * 100
        items.append({
            "sku_code": r["sku_code"], "description": r["description"], "category": r["category"],
            "servings": round(serv, 0), "serving_pct": round(serv_pct, 2),
            "cost": round(cost, 2), "cost_pct": round(cost_pct, 2),
            "cost_per_serving": round(cost / serv, 2) if serv else 0,
            "revenue": round(r["revenue"] or 0, 2),
            # priority = how much this dish drives cost relative to volume.
            # high cost_pct AND high serving_pct = top re-engineering target.
            "reduce_priority": round(cost_pct, 2),
            "is_high_volume": serv_pct >= (100 / max(1, len(rows)) * 2),  # >2× avg share
            "cost_source": r["cost_source"],
        })
    items.sort(key=lambda x: -x["cost_pct"])
    return {
        "branch": branch or "ทุกสาขา",
        "total_servings": round(tot_serv, 0),
        "total_cost": round(tot_cost, 2),
        "items": items[:limit],
        "n_dishes": len(rows),
    }


# ---------------------------------------------------------------------------
# GET /api/buffet/menu-cost-trend   (Phase 6, 2026-06-09)
# Monthly cost-share trend for the top-N food dishes (by total BoM/COGS cost):
# each dish's cost expressed as a % of total dish cost in that period_month, so
# dishes whose cost share is creeping up can be spotted and re-engineered.
# Powers the "ต้นทุนรายเดือน (top-8)" line chart on the Menu Efficiency tab.
# ---------------------------------------------------------------------------
@router.get("/api/buffet/menu-cost-trend")
@ttl_cache
def buffet_menu_cost_trend(
    branch:     Optional[str] = Query(None),
    month_from: Optional[str] = Query(None),
    month_to:   Optional[str] = Query(None),
    top_n:      int           = Query(8, ge=1, le=30),
):
    """Per-dish monthly cost share (cost / total dish cost that month, %).
    Returns the sorted month axis plus the top-N dishes by total cost across
    the range. Buffet tier rows excluded (cost lives on the item dishes)."""
    filters = ["b.name != 'ALL'", "ssm.source = 'Easy'", _FOOD_CATEGORY_FILTER]
    params: dict = {}
    if branch:
        filters.append("b.name = :branch"); params["branch"] = branch
    _month_range_filter(filters, params, month_from, month_to, None, col="ssm.period_month")
    where = "WHERE " + " AND ".join(filters)
    with get_db() as con:
        rows = con.execute(f"""
            SELECT sk.sku_code, COALESCE(sk.description, sk.sku_code) AS description,
                   ssm.period_month AS month,
                   SUM(ssm.qty)                     AS servings,
                   SUM({_unified_cogs_expr('ssm')}) AS cost
            FROM sales_sku_monthly ssm
            JOIN branches b   ON b.branch_id   = ssm.branch_id
            JOIN skus sk      ON sk.sku_id      = ssm.sku_id
            JOIN categories c ON c.category_id  = sk.category_id
            {where}
            GROUP BY sk.sku_id, ssm.period_month
            HAVING SUM(ssm.qty) > 0
        """, params).fetchall()
    # total dish cost per month → each dish's cost share
    month_tot: dict = {}
    for r in rows:
        month_tot[r["month"]] = month_tot.get(r["month"], 0) + (r["cost"] or 0)
    dishes: dict = {}
    for r in rows:
        d = dishes.setdefault(r["sku_code"], {
            "sku_code": r["sku_code"], "description": r["description"],
            "total_cost": 0.0, "monthly": [],
        })
        cost = r["cost"] or 0
        tot  = month_tot.get(r["month"]) or 1
        d["total_cost"] += cost
        d["monthly"].append({
            "month": r["month"], "cost": round(cost, 2),
            "cost_pct": round(cost / tot * 100, 2),
        })
    months = sorted(month_tot.keys())
    top = sorted(dishes.values(), key=lambda x: -x["total_cost"])[:top_n]
    for d in top:
        d["monthly"].sort(key=lambda x: x["month"])
        d["total_cost"] = round(d["total_cost"], 2)
    return {
        "branch": branch or "ทุกสาขา",
        "months": months,
        "dishes": top,
        "n_dishes": len(dishes),
    }


# ---------------------------------------------------------------------------
# GET /api/buffet/menu-cost-by-branch   (Phase 6, 2026-06-09)
# For ONE dish (sku_code): per-branch servings, cost/serving, cost share and
# food-cost rate — so a single dish's portioning/cost can be benchmarked across
# branches. Drill-down from the Menu Efficiency table.
# ---------------------------------------------------------------------------
@router.get("/api/buffet/menu-cost-by-branch")
@ttl_cache
def buffet_menu_cost_by_branch(
    sku_code:   str           = Query(...),
    month_from: Optional[str] = Query(None),
    month_to:   Optional[str] = Query(None),
):
    """Per-branch cost efficiency for a single dish (cost/serving, share, FC%)."""
    filters = ["b.name != 'ALL'", "ssm.source = 'Easy'", "sk.sku_code = :sku"]
    params: dict = {"sku": sku_code}
    _month_range_filter(filters, params, month_from, month_to, None, col="ssm.period_month")
    where = "WHERE " + " AND ".join(filters)
    with get_db() as con:
        rows = con.execute(f"""
            SELECT b.name AS branch,
                   SUM(ssm.qty)                     AS servings,
                   SUM({_unified_cogs_expr('ssm')}) AS cost,
                   SUM(ssm.subtotal_ex_vat)         AS revenue
            FROM sales_sku_monthly ssm
            JOIN branches b   ON b.branch_id   = ssm.branch_id
            JOIN skus sk      ON sk.sku_id      = ssm.sku_id
            {where}
            GROUP BY b.branch_id
            HAVING SUM(ssm.qty) > 0
        """, params).fetchall()
    tot_cost = sum((r["cost"] or 0) for r in rows) or 1
    branches = []
    for r in rows:
        serv = r["servings"] or 0; cost = r["cost"] or 0; rev = r["revenue"] or 0
        branches.append({
            "branch": r["branch"],
            "servings": round(serv, 0),
            "cost": round(cost, 2),
            "cost_per_serving": round(cost / serv, 2) if serv else 0,
            "cost_pct_of_total": round(cost / tot_cost * 100, 2),
            "food_cost_rate": round(cost / rev * 100, 2) if rev else None,
        })
    branches.sort(key=lambda x: x["cost_per_serving"])
    return {"sku_code": sku_code, "branches": branches, "n_branches": len(branches)}


# ---------------------------------------------------------------------------
# GET /api/buffet/menu-cost-coverage   (Phase 6, 2026-06-08)
# Recipe-coverage stats: how many dishes have BoM, flat sku_cost, or no cost
# data at all. Helps identify engineering potential (add recipes) and blind
# spots (no cost data at all). Returns summary + actionable top-10 lists.
# ---------------------------------------------------------------------------
@router.get("/api/buffet/menu-cost-coverage")
@ttl_cache
def buffet_menu_cost_coverage(
    branch:     Optional[str] = Query(None),
    month_from: Optional[str] = Query(None),
    month_to:   Optional[str] = Query(None),
):
    """Recipe coverage: for every food SKU with sales, classify into bom /
    sku_cost / none buckets, then return summary stats and top-10 action lists."""
    filters = ["b.name != 'ALL'", "ssm.source = 'Easy'", _FOOD_CATEGORY_FILTER]
    params: dict = {}
    if branch:
        filters.append("b.name = :branch"); params["branch"] = branch
    _month_range_filter(filters, params, month_from, month_to, None, col="ssm.period_month")
    where = "WHERE " + " AND ".join(filters)

    with get_db() as con:
        rows = con.execute(f"""
            SELECT sk.sku_id, sk.sku_code,
                   COALESCE(sk.description, sk.sku_code) AS description,
                   c.name AS category,
                   SUM(ssm.qty)             AS servings,
                   SUM({_unified_cogs_expr('ssm')}) AS cost,
                   SUM(ssm.subtotal_ex_vat) AS revenue,
                   CASE
                     WHEN EXISTS (SELECT 1 FROM bom_ingredients bi WHERE bi.sku_id = sk.sku_id) THEN 'bom'
                     -- alias-resolved recipe (bom_sku_alias → bom_ingredients): _unified_cogs_expr
                     -- costs these as real BoM, so the classifier must too or the coverage
                     -- donut labels a costed SKU 'none' while it contributes COGS.
                     WHEN EXISTS (SELECT 1 FROM bom_sku_alias a JOIN bom_ingredients bi2
                                    ON bi2.sku_id = a.resolved_sku_id
                                  WHERE a.bom_sku_code = sk.sku_code) THEN 'bom'
                     WHEN EXISTS (SELECT 1 FROM sku_cost sc2 WHERE sc2.sku_id = sk.sku_id) THEN 'sku_cost'
                     ELSE 'none'
                   END AS bucket
            FROM sales_sku_monthly ssm
            JOIN branches b   ON b.branch_id  = ssm.branch_id
            JOIN skus sk      ON sk.sku_id     = ssm.sku_id
            JOIN categories c ON c.category_id = sk.category_id
            {where}
            GROUP BY sk.sku_id
            HAVING SUM(ssm.qty) > 0
        """, params).fetchall()

    summary: dict = {
        "bom":      {"count": 0, "cost": 0.0, "revenue": 0.0},
        "sku_cost": {"count": 0, "cost": 0.0, "revenue": 0.0},
        "none":     {"count": 0, "cost": 0.0, "revenue": 0.0},
    }
    high_cost_no_bom: list = []   # sku_cost bucket, high cost — CAN be improved
    high_revenue_uncosted: list = []  # none bucket, high revenue — blind spots

    for r in rows:
        bkt  = r["bucket"]
        cost = float(r["cost"] or 0)
        rev  = float(r["revenue"] or 0)
        if bkt not in summary:
            bkt = "none"
        summary[bkt]["count"]   += 1
        summary[bkt]["cost"]    += cost
        summary[bkt]["revenue"] += rev
        item = {
            "sku_code": r["sku_code"],
            "description": r["description"],
            "category": r["category"],
            "servings": round(float(r["servings"] or 0), 0),
            "cost": round(cost, 2),
            "revenue": round(rev, 2),
            "bucket": bkt,
        }
        if bkt == "sku_cost":
            high_cost_no_bom.append(item)
        elif bkt == "none":
            high_revenue_uncosted.append(item)

    # Sort and cap at 10
    high_cost_no_bom.sort(key=lambda x: -x["cost"])
    high_revenue_uncosted.sort(key=lambda x: -x["revenue"])
    high_cost_no_bom      = high_cost_no_bom[:10]
    high_revenue_uncosted = high_revenue_uncosted[:10]

    # Add cost_pct to summary buckets
    tot_cost = sum(v["cost"] for v in summary.values()) or 1
    for bkt, s in summary.items():
        s["cost_pct"] = round(s["cost"] / tot_cost * 100, 1)

    return {
        "summary":              summary,
        "high_cost_no_bom":     high_cost_no_bom,
        "high_revenue_uncosted": high_revenue_uncosted,
    }


# ---------------------------------------------------------------------------
# GET /api/buffet/portion-velocity   🟡3
# qty / cover per SKU per month — detect over-ordering trends
# ---------------------------------------------------------------------------
@router.get("/api/buffet/portion-velocity")
@ttl_cache
def buffet_portion_velocity(
    branch:     Optional[str] = Query(None),
    month_from: Optional[str] = Query(None),
    month_to:   Optional[str] = Query(None),
    top_n:      int           = Query(50, ge=5, le=100),
):
    """Per-SKU qty/cover trend. Covers from transactions; qty from SKU monthly."""
    TIER_CODES = ('BUFFET','BUFFET350','BUFFET500','BUFFET590','BUFFET799','BUFFETCHILD',
                  'B01','B02','B07','B11')
    tier_named = {f"tc{i}": v for i, v in enumerate(TIER_CODES)}
    tier_ph    = ", ".join(f":tc{i}" for i in range(len(TIER_CODES)))

    sku_filters: list = ["b.name != 'ALL'", f"sk.sku_code NOT IN ({tier_ph})"]
    sku_params:  dict = {**tier_named}
    if branch:
        sku_filters.append("b.name = :branch")
        sku_params["branch"] = branch
    _month_range_filter(sku_filters, sku_params, month_from, month_to, None,
                        col="sm.period_month")
    where_sku = "WHERE " + " AND ".join(sku_filters)

    tx_filters: list = ["b.name != 'ALL'", "st.source='Easy'"]
    tx_params:  dict = {}
    if branch:
        tx_filters.append("b.name = :branch")
        tx_params["branch"] = branch
    _month_range_filter(tx_filters, tx_params, month_from, month_to, None,
                        col="SUBSTR(st.sold_at,1,7)")
    where_tx = "WHERE " + " AND ".join(tx_filters)

    with get_db() as con:
        sku_rows = con.execute(f"""
            SELECT sm.period_month AS m,
                   sk.sku_code, COALESCE(sk.description, sk.sku_code) AS description,
                   c.name AS category,
                   SUM(sm.qty) AS qty
            FROM sales_sku_monthly sm
            JOIN branches   b  ON b.branch_id   = sm.branch_id
            JOIN skus       sk ON sk.sku_id      = sm.sku_id
            JOIN categories c  ON c.category_id  = sk.category_id
            {where_sku}
            GROUP BY sm.period_month, sk.sku_code
            ORDER BY sm.period_month, qty DESC
        """, sku_params).fetchall()

        tx_rows = con.execute(f"""
            SELECT SUBSTR(st.sold_at,1,7) AS m,
                   SUM(st.customer_count) AS covers
            FROM sales_transaction st
            JOIN branches b ON b.branch_id = st.branch_id
            {where_tx}
              AND st.customer_count > 0
            GROUP BY SUBSTR(st.sold_at,1,7)
        """, tx_params).fetchall()

    covers_by_month = {r["m"]: (r["covers"] or 1) for r in tx_rows}
    months_sorted   = sorted(covers_by_month.keys())

    from collections import defaultdict
    sku_monthly: dict = defaultdict(lambda: defaultdict(float))
    sku_meta: dict = {}
    for r in sku_rows:
        sku_monthly[r["sku_code"]][r["m"]] = r["qty"] or 0
        sku_meta[r["sku_code"]] = {"description": r["description"], "category": r["category"]}

    # avg qty/cover across all months
    sku_avg: dict = {}
    for code, monthly in sku_monthly.items():
        ratios = [(monthly[m] / covers_by_month[m]) for m in monthly if m in covers_by_month and covers_by_month[m]]
        sku_avg[code] = sum(ratios) / len(ratios) if ratios else 0

    top_skus = sorted(sku_avg.keys(), key=lambda x: -sku_avg[x])[:top_n]

    result = []
    for code in top_skus:
        monthly = sku_monthly[code]
        series  = []
        for m in months_sorted:
            covers = covers_by_month.get(m, 0)
            qty    = monthly.get(m, 0)
            series.append({
                "month":    m,
                "qty":      round(qty, 1),
                "covers":   covers,
                "ratio":    round(qty / covers, 4) if covers else 0,
            })
        # trend: slope of last 3 months vs first 3 months
        vals = [s["ratio"] for s in series if s["ratio"] > 0]
        if len(vals) >= 4:
            mid     = len(vals) // 2
            avg_old = sum(vals[:mid]) / mid
            avg_new = sum(vals[mid:]) / (len(vals) - mid)
            trend_pct = round((avg_new - avg_old) / avg_old * 100, 1) if avg_old else 0
        else:
            trend_pct = 0

        result.append({
            "sku_code":    code,
            "description": sku_meta[code]["description"],
            "category":    sku_meta[code]["category"],
            "avg_ratio":   round(sku_avg[code], 4),
            "trend_pct":   trend_pct,
            "series":      series,
        })

    return {"months": months_sorted, "skus": result}


# ---------------------------------------------------------------------------
# GET /api/buffet/plate-cost-dist   🟡4
# Pro-rate monthly BoM cost to per-bill, return distribution
# ---------------------------------------------------------------------------
@router.get("/api/buffet/plate-cost-dist")
@ttl_cache
def buffet_plate_cost_dist(
    branch:     Optional[str] = Query(None),
    month_from: Optional[str] = Query(None),
    month_to:   Optional[str] = Query(None),
):
    """Estimate cost per bill by pro-rating monthly BoM cost by daily bill revenue."""
    TIER_CODES = ('BUFFET','BUFFET350','BUFFET500','BUFFET590','BUFFET799','BUFFETCHILD',
                  'B01','B02','B07','B11')
    tier_named = {f"tc{i}": v for i, v in enumerate(TIER_CODES)}
    tier_ph    = ", ".join(f":tc{i}" for i in range(len(TIER_CODES)))

    sku_filters: list = ["b.name != 'ALL'"]
    sku_params: dict  = {**tier_named}
    if branch:
        sku_filters.append("b.name = :branch")
        sku_params["branch"] = branch
    _month_range_filter(sku_filters, sku_params, month_from, month_to, None, col="sm.period_month")
    where_sku = "WHERE " + " AND ".join(sku_filters)

    tx_filters: list = ["b.name != 'ALL'", "st.source='Easy'", "st.customer_count > 0"]
    tx_params: dict  = {}
    if branch:
        tx_filters.append("b.name = :branch")
        tx_params["branch"] = branch
    _month_range_filter(tx_filters, tx_params, month_from, month_to, None,
                        col="SUBSTR(st.sold_at,1,7)")
    where_tx = "WHERE " + " AND ".join(tx_filters)

    with get_db() as con:
        # Monthly BoM cost per branch
        bom_rows = con.execute(f"""
            SELECT b.name AS branch, sm.period_month AS m,
                   SUM(CASE WHEN sk.sku_code NOT IN ({tier_ph})
                            THEN {_cogs_case(ssm='sm', sk='sk', sc='sc')} ELSE 0 END) AS bom_cost
            FROM sales_sku_monthly sm
            JOIN branches   b  ON b.branch_id  = sm.branch_id
            JOIN skus       sk ON sk.sku_id     = sm.sku_id
            LEFT JOIN sku_cost sc ON sc.sku_id  = sm.sku_id
            {where_sku}
            GROUP BY b.name, sm.period_month
        """, sku_params).fetchall()

        # Per-bill data
        bill_rows = con.execute(f"""
            SELECT b.name AS branch,
                   SUBSTR(st.sold_at,1,7) AS m,
                   st.receipt_no,
                   st.customer_count,
                   COALESCE(st.food_subtotal,0)+COALESCE(st.drink_subtotal,0)+
                   COALESCE(st.other_subtotal,0) AS bill_rev
            FROM sales_transaction st
            JOIN branches b ON b.branch_id = st.branch_id
            {where_tx}
        """, tx_params).fetchall()

        # Monthly revenue per branch from transactions
        month_rev_rows = con.execute(f"""
            SELECT b.name AS branch, SUBSTR(st.sold_at,1,7) AS m,
                   SUM(COALESCE(st.food_subtotal,0)+COALESCE(st.drink_subtotal,0)+
                       COALESCE(st.other_subtotal,0)) AS monthly_rev
            FROM sales_transaction st
            JOIN branches b ON b.branch_id = st.branch_id
            {where_tx}
            GROUP BY b.name, SUBSTR(st.sold_at,1,7)
        """, tx_params).fetchall()

    from collections import defaultdict
    bom_map   = {(r["branch"], r["m"]): r["bom_cost"] or 0 for r in bom_rows}
    mrev_map  = {(r["branch"], r["m"]): r["monthly_rev"] or 1 for r in month_rev_rows}

    bill_costs = []
    for r in bill_rows:
        key     = (r["branch"], r["m"])
        bom     = bom_map.get(key, 0)
        mrev    = mrev_map.get(key, 1)
        covers  = r["customer_count"] or 1
        bill_rv = r["bill_rev"] or 0
        fraction = bill_rv / mrev if mrev > 0 else 0
        cost_per_cover = (bom * fraction) / covers if covers else 0
        bill_costs.append(cost_per_cover)

    if not bill_costs:
        return {"distribution": [], "percentiles": {}, "mean": 0, "count": 0}

    bill_costs.sort()
    n = len(bill_costs)

    def pct(lst, p):
        idx = max(0, min(len(lst) - 1, int(p / 100 * len(lst))))
        return round(lst[idx], 2)

    buckets = [
        ("<฿100",    0,   100),
        ("฿100-200", 100, 200),
        ("฿200-300", 200, 300),
        ("฿300-400", 300, 400),
        ("฿400-500", 400, 500),
        ("฿500-600", 500, 600),
        ("฿600+",    600, float("inf")),
    ]
    distribution = []
    for label, lo, hi in buckets:
        count = sum(1 for c in bill_costs if lo <= c < hi)
        distribution.append({
            "label": label, "count": count,
            "pct": round(count / n * 100, 1),
            "lo": lo, "hi": hi if hi != float("inf") else None,
        })

    return {
        "distribution": distribution,
        "percentiles": {
            "p25": pct(bill_costs, 25),
            "p50": pct(bill_costs, 50),
            "p75": pct(bill_costs, 75),
            "p90": pct(bill_costs, 90),
            "p95": pct(bill_costs, 95),
        },
        "mean":  round(sum(bill_costs) / n, 2),
        "count": n,
    }


@router.get("/api/buffet/heavy-eater-tail")
def buffet_heavy_eater_tail(branch: Optional[str] = Query(None), group: Optional[str] = Query(None)):
    """The defining AYCE question — REAL per-cover margin (what they paid − what they
    actually ate), not revenue pro-rata. Joins sale_bill_cogs (per-bill BoM COGS)
    to the bill's ex-VAT revenue + pax to surface the net-negative 'heavy eater'
    tail and segment it by daypart / day-of-week / party-size / branch.
    Window = the costed sale_bill_cogs range (~31 days)."""
    filt = ["t.source='Easy'", "b.name!='ALL'", "t.total_inc_vat>0", "t.customer_count>0"]
    p: dict = {}
    if branch: filt.append("b.name=:branch"); p["branch"] = branch
    _group_filter(filt, p, group)
    JN = ("FROM sales_transaction t JOIN sale_bill_cogs c ON c.receipt_no=t.receipt_no "
          "JOIN branches b ON b.branch_id=t.branch_id WHERE " + " AND ".join(filt))
    rev = "t.total_inc_vat/1.07"                # ex-VAT paid
    mh  = f"({rev} - c.cogs_bom)/t.customer_count"   # margin per head
    with get_db() as con:
        win = con.execute("SELECT MIN(day) mn, MAX(day) mx FROM sale_bill_cogs").fetchone()
        s = con.execute(f"""SELECT COUNT(*) bills, SUM(t.customer_count) covers,
            ROUND(SUM({rev})) rev, ROUND(SUM(c.cogs_bom)) cogs,
            SUM(CASE WHEN c.cogs_bom>{rev} THEN 1 ELSE 0 END) loss_bills,
            SUM(CASE WHEN c.cogs_bom>{rev} THEN t.customer_count ELSE 0 END) loss_covers,
            ROUND(SUM(CASE WHEN c.cogs_bom>{rev} THEN c.cogs_bom-{rev} ELSE 0 END)) loss_amount
            {JN}""", p).fetchone()
        bills = s["bills"] or 0; covers = s["covers"] or 0
        revv = s["rev"] or 0; cogs = s["cogs"] or 0
        summary = {"bills": bills, "covers": covers, "rev": revv, "cogs": cogs,
                   "gp": round(revv - cogs), "gp_per_head": round((revv - cogs)/covers, 1) if covers else None,
                   "loss_bills": s["loss_bills"] or 0, "loss_covers": s["loss_covers"] or 0,
                   "loss_amount": s["loss_amount"] or 0,
                   "loss_bill_pct": round((s["loss_bills"] or 0)/bills*100, 1) if bills else 0,
                   "loss_cover_pct": round((s["loss_covers"] or 0)/covers*100, 1) if covers else 0}
        # margin-per-head distribution (the tail lives in the negative buckets)
        dist = [dict(r) for r in con.execute(f"""
            SELECT CASE WHEN {mh}<-100 THEN 0 WHEN {mh}<0 THEN 1 WHEN {mh}<100 THEN 2
                        WHEN {mh}<300 THEN 3 WHEN {mh}<500 THEN 4 ELSE 5 END bkt,
                   COUNT(*) bills, SUM(t.customer_count) covers {JN} GROUP BY bkt ORDER BY bkt""", p).fetchall()]
        _DLBL = {0:"ขาดทุนหนัก (<-100)",1:"ขาดทุน (-100..0)",2:"บางมาก (0-100)",3:"100-300",4:"300-500",5:"สุขภาพดี (500+)"}
        distribution = [{"bkt": d["bkt"], "label": _DLBL[d["bkt"]], "bills": d["bills"], "covers": d["covers"]} for d in dist]
        def seg(expr, label_map=None):
            rows = [dict(r) for r in con.execute(f"""
                SELECT {expr} k, COUNT(*) bills, SUM(t.customer_count) covers,
                    SUM(CASE WHEN c.cogs_bom>{rev} THEN 1 ELSE 0 END) loss_bills,
                    SUM(CASE WHEN c.cogs_bom>{rev} THEN t.customer_count ELSE 0 END) loss_covers,
                    ROUND(AVG({mh}),1) avg_margin_head,
                    ROUND(SUM(CASE WHEN c.cogs_bom>{rev} THEN c.cogs_bom-{rev} ELSE 0 END)) loss_amount
                {JN} GROUP BY k""", p).fetchall()]
            for r in rows:
                r["loss_cover_pct"] = round((r["loss_covers"] or 0)/r["covers"]*100, 1) if r["covers"] else 0
                if label_map: r["label"] = label_map.get(r["k"], str(r["k"]))
            return rows
        by_daypart = seg("CASE WHEN CAST(substr(t.sold_at,12,2) AS INT)<16 THEN 'กลางวัน (lunch)' ELSE 'เย็น (dinner)' END")
        by_dow = seg("CAST(strftime('%w', t.sold_at) AS INT)",
                     {0:"อา",1:"จ",2:"อ",3:"พ",4:"พฤ",5:"ศ",6:"ส"})
        by_party = seg("CASE WHEN t.customer_count=1 THEN '1' WHEN t.customer_count=2 THEN '2' "
                       "WHEN t.customer_count<=4 THEN '3-4' WHEN t.customer_count<=6 THEN '5-6' ELSE '7+' END")
        by_branch = seg("b.name")
        for r in by_branch:
            r["branch"] = r.pop("k")
        by_branch.sort(key=lambda r: -(r["loss_cover_pct"] or 0))
    return {"window": [win["mn"], win["mx"]] if win else [None, None], "summary": summary,
            "distribution": distribution, "by_daypart": by_daypart, "by_dow": by_dow,
            "by_party_size": by_party, "by_branch": by_branch}


@router.get("/api/buffet/turn-efficiency")
def buffet_turn_efficiency(branch: Optional[str] = Query(None), group: Optional[str] = Query(None)):
    """Table throughput efficiency — the OTHER half of buffet profit (a table tied up
    long earns nothing for the next party). Joins real dwell (pos_table_turn_day.
    avg_turn_min) + covers/table to real GP/cover, to rank branches by GP generated
    per table-MINUTE occupied, and flag the worst combo: long dwell + low GP/cover.
    Uses tables (real) — seats are not populated in pos_zone, so we report per-table."""
    filt = ["b.name!='ALL'"]; p: dict = {}
    if branch: filt.append("b.name=:branch"); p["branch"] = branch
    _group_filter(filt, p, group)
    tw = " AND ".join(filt)
    with get_db() as con:
        mx = con.execute("SELECT MAX(day) FROM pos_table_turn_day").fetchone()[0]
        if not mx:
            return {"window": None, "branches": [], "chain": {}}
        import datetime as _dt
        y, m, d = (int(x) for x in mx.split("-")); since = (_dt.date(y, m, d) - _dt.timedelta(days=30)).isoformat()
        rows = [dict(r) for r in con.execute(f"""
            WITH tt AS (
              SELECT x.branch_id,
                     AVG(x.avg_turn_min) turn_min,
                     ROUND(AVG(x.customers)) cov_day,
                     ROUND(AVG(x.tabs)) tabs_day,
                     AVG(CAST(x.customers AS float)/NULLIF(x.tabs,0)) cov_per_tab
              FROM pos_table_turn_day x JOIN branches b ON b.branch_id=x.branch_id
              WHERE {tw} AND x.day>=:since GROUP BY x.branch_id),
            gp AS (
              SELECT t.branch_id, SUM(t.total_inc_vat/1.07 - c.cogs_bom) gp, SUM(t.customer_count) cov
              FROM sales_transaction t JOIN sale_bill_cogs c ON c.receipt_no=t.receipt_no
              JOIN branches b ON b.branch_id=t.branch_id WHERE {tw} AND t.customer_count>0 GROUP BY t.branch_id)
            SELECT b.name branch, ROUND(tt.turn_min,1) turn_min, tt.cov_day, tt.tabs_day,
                   ROUND(tt.cov_per_tab,2) cov_per_tab,
                   ROUND(gp.gp/NULLIF(gp.cov,0)) gp_cover
            FROM tt JOIN branches b ON b.branch_id=tt.branch_id LEFT JOIN gp ON gp.branch_id=tt.branch_id
        """, {**p, "since": since}).fetchall()]
        for r in rows:
            gpc = r.get("gp_cover") or 0; cpt = r.get("cov_per_tab") or 0; tm = r.get("turn_min") or 0
            r["gp_per_table_occasion"] = round(gpc * cpt) if (gpc and cpt) else None
            r["gp_per_table_min"] = round(gpc * cpt / tm, 1) if (gpc and cpt and tm) else None
        valid = [r for r in rows if r.get("gp_per_table_min")]
        n = len(valid) or 1
        chain = {
            "turn_min": round(sum(r["turn_min"] for r in valid) / n, 1) if valid else None,
            "gp_cover": round(sum((r["gp_cover"] or 0) for r in valid) / n) if valid else None,
            "gp_per_table_min": round(sum(r["gp_per_table_min"] for r in valid) / n, 1) if valid else None,
        }
        # flag: dwell above chain avg AND gp/cover below chain avg = inefficient
        for r in rows:
            r["inefficient"] = bool(chain["turn_min"] and chain["gp_cover"]
                                    and (r.get("turn_min") or 0) > chain["turn_min"]
                                    and (r.get("gp_cover") or 0) < chain["gp_cover"])
        rows.sort(key=lambda r: -(r.get("gp_per_table_min") or -1))
    return {"window": [since, mx], "branches": rows, "chain": chain}


# ---------------------------------------------------------------------------
# GET /api/buffet/cost-sensitivity   🟡5
# Top cost-driver SKUs for JS-side simulation
# ---------------------------------------------------------------------------
@router.get("/api/buffet/cost-sensitivity")
@ttl_cache
def buffet_cost_sensitivity(
    branch:     Optional[str] = Query(None),
    month_from: Optional[str] = Query(None),
    month_to:   Optional[str] = Query(None),
    top_n:      int           = Query(50, ge=5, le=100),
):
    """Returns top SKUs by BoM cost with enough data for client-side sensitivity simulation."""
    TIER_CODES = ('BUFFET','BUFFET350','BUFFET500','BUFFET590','BUFFET799','BUFFETCHILD',
                  'B01','B02','B07','B11')
    tier_named = {f"tc{i}": v for i, v in enumerate(TIER_CODES)}
    tier_ph    = ", ".join(f":tc{i}" for i in range(len(TIER_CODES)))

    filters: list = ["b.name != 'ALL'", f"sk.sku_code NOT IN ({tier_ph})"]
    params: dict  = {**tier_named}
    if branch:
        filters.append("b.name = :branch")
        params["branch"] = branch
    _month_range_filter(filters, params, month_from, month_to, None, col="sm.period_month")
    where = "WHERE " + " AND ".join(filters)

    with get_db() as con:
        rows = con.execute(f"""
            SELECT sk.sku_code,
                   COALESCE(sk.description, sk.sku_code) AS description,
                   c.name AS category,
                   sc.unit_cost,
                   SUM(sm.qty)                                            AS total_qty,
                   SUM({_cogs_case(ssm='sm', sk='sk', sc='sc')})          AS total_bom_cost,
                   SUM(sm.subtotal_ex_vat)                                AS total_rev
            FROM sales_sku_monthly sm
            JOIN branches   b  ON b.branch_id  = sm.branch_id
            JOIN skus       sk ON sk.sku_id     = sm.sku_id
            JOIN categories c  ON c.category_id = sk.category_id
            LEFT JOIN sku_cost sc ON sc.sku_id  = sm.sku_id
            {where}
              AND {_effective_cost_subquery(ssm='sm', sc='sc')} IS NOT NULL
              AND {_effective_cost_subquery(ssm='sm', sc='sc')} > 0
            GROUP BY sk.sku_code, sc.unit_cost
            ORDER BY total_bom_cost DESC
            LIMIT :top_n
        """, {**params, "top_n": top_n}).fetchall()

        # Total revenue for the period (to compute margin impact)
        rev_row = con.execute(f"""
            SELECT SUM(sm.subtotal_ex_vat) AS total_rev
            FROM sales_sku_monthly sm
            JOIN branches b ON b.branch_id = sm.branch_id
            JOIN skus sk ON sk.sku_id = sm.sku_id
            {where}
        """, params).fetchone()

    total_rev = (rev_row["total_rev"] or 0) if rev_row else 0
    total_bom = sum(r["total_bom_cost"] or 0 for r in rows)

    skus = [{
        "sku_code":      r["sku_code"],
        "description":   r["description"],
        "category":      r["category"],
        "unit_cost":     r["unit_cost"],
        "total_qty":     round(r["total_qty"] or 0, 1),
        "total_bom_cost": round(r["total_bom_cost"] or 0, 2),
        "total_rev":     round(r["total_rev"] or 0, 2),
        "cost_pct":      round((r["total_bom_cost"] or 0) / total_bom * 100, 2) if total_bom else 0,
    } for r in rows]

    return {
        "skus":          skus,
        "total_revenue": round(total_rev, 2),
        "total_bom":     round(total_bom, 2),
        "base_gp":       round(total_rev - total_bom, 2),
    }


# ---------------------------------------------------------------------------
# GET + POST /api/branches/fixed-costs   🟡6
# Per-branch monthly fixed costs (stored in app_settings as JSON)
# ---------------------------------------------------------------------------
import json as _json




# ---------------------------------------------------------------------------
# GET /api/buffet/pnl   🟡6 (part 2)
# Net P&L = GP - Fixed Costs per branch
# ---------------------------------------------------------------------------
@router.get("/api/buffet/pnl")
@ttl_cache
def buffet_pnl(
    branch:     Optional[str] = Query(None),
    month_from: Optional[str] = Query(None),
    month_to:   Optional[str] = Query(None),
):
    """Net P&L per branch = Gross Profit (BoM) − monthly fixed costs."""
    # Reuse the same GP logic — aggregate by branch
    TIER_CODES = ('BUFFET','BUFFET350','BUFFET500','BUFFET590','BUFFET799','BUFFETCHILD',
                  'B01','B02','B07','B11')
    tier_named = {f"tc{i}": v for i, v in enumerate(TIER_CODES)}
    tier_ph    = ", ".join(f":tc{i}" for i in range(len(TIER_CODES)))

    sku_filters: list = ["b.name != 'ALL'"]
    sku_params: dict  = {**tier_named}
    if branch:
        sku_filters.append("b.name = :branch")
        sku_params["branch"] = branch
    _month_range_filter(sku_filters, sku_params, month_from, month_to, None, col="sm.period_month")
    where_sku = "WHERE " + " AND ".join(sku_filters)

    tx_filters: list = ["b.name != 'ALL'", "st.source='Easy'"]
    tx_params: dict  = {}
    if branch:
        tx_filters.append("b.name = :branch")
        tx_params["branch"] = branch
    _month_range_filter(tx_filters, tx_params, month_from, month_to, None,
                        col="SUBSTR(st.sold_at,1,7)")
    where_tx = "WHERE " + " AND ".join(tx_filters)

    with get_db() as con:
        gp_rows = con.execute(f"""
            SELECT b.name AS branch,
                   COUNT(DISTINCT sm.period_month)                          AS months,
                   SUM(sm.subtotal_ex_vat)                                  AS revenue,
                   SUM(CASE WHEN sk.sku_code NOT IN ({tier_ph})
                            THEN {_cogs_case(ssm='sm', sk='sk', sc='sc')} ELSE 0 END) AS bom_cost
            FROM sales_sku_monthly sm
            JOIN branches   b  ON b.branch_id   = sm.branch_id
            JOIN skus       sk ON sk.sku_id      = sm.sku_id
            LEFT JOIN sku_cost sc ON sc.sku_id   = sm.sku_id
            {where_sku}
            GROUP BY b.name
        """, sku_params).fetchall()

        covers_rows = con.execute(f"""
            SELECT b.name AS branch, SUM(st.customer_count) AS covers
            FROM sales_transaction st
            JOIN branches b ON b.branch_id = st.branch_id
            {where_tx} AND st.customer_count > 0
            GROUP BY b.name
        """, tx_params).fetchall()

        fc_row = con.execute(
            "SELECT value FROM app_settings WHERE key='branch_fixed_costs'"
        ).fetchone()

    fixed_costs = {}
    if fc_row:
        try:
            fixed_costs = _json.loads(fc_row["value"])
        except Exception:
            pass

    covers_map = {r["branch"]: (r["covers"] or 0) for r in covers_rows}

    result = []
    for r in sorted(gp_rows, key=lambda x: -(x["revenue"] or 0)):
        br          = r["branch"]
        rev         = round(r["revenue"] or 0, 2)
        bom         = round(r["bom_cost"] or 0, 2)
        gp          = round(rev - bom, 2)
        months      = r["months"] or 1
        fc_monthly  = fixed_costs.get(br, 0.0)
        fc_period   = round(fc_monthly * months, 2)
        net_profit  = round(gp - fc_period, 2)
        covers      = covers_map.get(br, 0)
        np_per_cov  = round(net_profit / covers, 2) if covers else None
        gp_margin   = round(gp / rev * 100, 2) if rev else None
        np_margin   = round(net_profit / rev * 100, 2) if rev else None

        result.append({
            "branch":          br,
            "months":          months,
            "revenue":         rev,
            "bom_cost":        bom,
            "gross_profit":    gp,
            "gp_margin_pct":   gp_margin,
            "fixed_cost_period": fc_period,
            "fixed_cost_monthly": fc_monthly,
            "net_profit":      net_profit,
            "np_margin_pct":   np_margin,
            "covers":          covers,
            "np_per_cover":    np_per_cov,
        })

    return {"branches": result}



@router.get("/api/buffet/gp/export")
def buffet_gp_export(
    branch:      Optional[str] = Query(None),
    month_from:  Optional[str] = Query(None),
    month_to:    Optional[str] = Query(None),
    granularity: str           = Query("month"),
):
    """Export buffet GP analysis to Excel (.xlsx)."""
    try:
        import openpyxl
        from openpyxl.styles import Font, PatternFill, Alignment, numbers
        from openpyxl.utils import get_column_letter
    except ImportError:
        raise HTTPException(status_code=500, detail="openpyxl not installed")

    # Fetch data using same logic as /api/buffet/gp
    params = {}
    if branch:      params["branch"]     = branch
    if month_from:  params["month_from"] = month_from
    if month_to:    params["month_to"]   = month_to
    params["granularity"] = granularity

    # buffet_gp is @ttl_cache-wrapped → a direct (in-process) call returns a JSON
    # Response, NOT a dict. Parse its body. (Falls back to dict if ever un-cached.)
    gp_resp = buffet_gp(
        branch=branch, month_from=month_from,
        month_to=month_to, granularity=granularity,
    )
    gp_data = _json.loads(gp_resp.body) if hasattr(gp_resp, "body") else gp_resp

    wb  = openpyxl.Workbook()
    HDR = Font(bold=True, color="FFFFFF")
    FILL_H = PatternFill("solid", fgColor="1F4E79")
    FILL_GP = PatternFill("solid", fgColor="E8F5E9")
    NUM_FMT  = '#,##0'
    PCT_FMT  = '0.0%'
    CENTER   = Alignment(horizontal="center")

    def make_header(ws, headers, fill=FILL_H):
        ws.append(headers)
        for cell in ws[1]:
            cell.font = HDR
            cell.fill = fill
            cell.alignment = CENTER

    def autofit(ws):
        for col in ws.columns:
            max_len = max((len(str(cell.value or "")) for cell in col), default=8)
            ws.column_dimensions[get_column_letter(col[0].column)].width = min(max_len + 2, 40)

    # Sheet 1: Period Summary
    ws1 = wb.active
    ws1.title = "Period Summary"
    pt = gp_data.get("period_total", {})
    ws1.append(["Metric", "Value"])
    for cell in ws1[1]: cell.font = HDR; cell.fill = FILL_H
    ws1.append(["Revenue (ex-VAT)", pt.get("revenue") or 0])
    ws1.append(["BoM Cost",         pt.get("bom_cost") or 0])
    ws1.append(["Gross Profit",     pt.get("gp") or 0])
    ws1.append(["GP Margin %",      (pt.get("margin_pct") or 0) / 100])
    ws1.append(["Covers",           pt.get("covers") or 0])
    ws1.append(["Cost/Cover",       pt.get("cost_per_cover") or 0])
    ws1["B5"].number_format = PCT_FMT
    autofit(ws1)

    # Sheet 2: Time Series
    ws2 = wb.create_sheet("Time Series")
    make_header(ws2, ["Bucket", "Revenue", "BoM Cost", "Gross Profit", "Margin %", "Covers"])
    for s in gp_data.get("series", []):
        ws2.append([
            s["bucket"], s["revenue"], s["bom_cost"], s["gp"],
            (s["margin_pct"] or 0) / 100, s["covers"],
        ])
    for row in ws2.iter_rows(min_row=2):
        for cell in row[1:4]: cell.number_format = NUM_FMT
        row[4].number_format = PCT_FMT
    autofit(ws2)

    # Sheet 3: By Branch
    ws3 = wb.create_sheet("By Branch")
    make_header(ws3, ["Branch", "Covers", "Revenue", "BoM Cost", "GP", "GP/Cover", "Margin %", "vs Network"])
    for b in gp_data.get("by_branch", []):
        ws3.append([
            b["branch"], b["covers"], b["revenue"], b["bom_cost"],
            b["gp"], b["gp_per_cover"], (b["margin_pct"] or 0) / 100,
            (b["vs_network_pp"] or 0) / 100,
        ])
    for row in ws3.iter_rows(min_row=2):
        for cell in row[2:6]: cell.number_format = NUM_FMT
        row[6].number_format = PCT_FMT
        row[7].number_format = PCT_FMT
    autofit(ws3)

    # Sheet 4: Top Cost SKUs
    ws4 = wb.create_sheet("Top Cost SKUs")
    make_header(ws4, ["SKU Code", "Category", "Qty", "BoM Cost", "% of Total Cost"])
    for s in gp_data.get("top_cost_skus", []):
        ws4.append([s["sku_code"], s["category"], s["qty"], s["bom_cost"], s["cost_pct"] / 100])
    for row in ws4.iter_rows(min_row=2):
        row[3].number_format = NUM_FMT
        row[4].number_format = PCT_FMT
    autofit(ws4)

    buf = _io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    filename = f"buffet_gp_{month_from or 'all'}_{month_to or 'all'}.xlsx"
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )
