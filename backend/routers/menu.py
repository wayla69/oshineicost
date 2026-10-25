"""Menu domain router — /api/menu/* (engineering / action-board /
opportunities / ai-ideas / lifecycle) + the _menu_*_compute helpers, moved
out of main.py (P1 structure refactor, step 4). Same host-module pattern as
routers/reports.py. DV7 (2026-10-25): action-board and opportunities are
backend/menu_board.py (buffet cost per head, the top bar's period).
"""
# Shared infrastructure and helpers come from core.py by name — the old
# `globals().update(getattr(main, …))` bootstrap is gone (Phase 2, 2026-09).
from fastapi import Depends, Query, Request
from pydantic import BaseModel
from typing import Optional
from core import (
    _cogs_case, _effective_cost_subquery, _month_range_filter, get_db,
    require_write_token, ttl_cache,
)

from fastapi import APIRouter
router = APIRouter()




# ---------------------------------------------------------------------------
# /api/menu/engineering  — Kasavana-Smith matrix
# ---------------------------------------------------------------------------
def _menu_engineering_compute(
    month=None, month_from=None, month_to=None,
    branch=None, category=None, include_buffet_drink=False,
):
    """Core of /api/menu/engineering — returns the items + medians dict (UNCACHED,
    so it's callable in-process). The cached endpoint below is a thin wrapper, and
    /api/menu/action-board reuses this so its quadrant/margin numbers are guaranteed
    identical to the Menu Engineering tab (no duplicated classification, no drift)."""
    filters = ["b.name != 'ALL'"]
    params: dict = {}
    if branch:
        filters.append("b.name = :branch")
        params["branch"] = branch
    _month_range_filter(filters, params, month_from, month_to, month,
                        col="ssm.period_month")
    if category:
        filters.append("COALESCE(cat.name, 'Uncategorised') = :category")
        params["category"] = category
    # Default: exclude non-food categories so menu engineering reflects à la carte food only.
    # Exception: keep ice-cream items (look for ICE / ไอติม / ไอศกรีม / GELATO in description)
    # even if they sit inside a DESSERT category — user wants ice cream in food analysis.
    EXCLUDED_CATS = (
        'BUFFET', 'Oshinei SOFT DRINK', 'Oshinei ALCOHOL',
        'Oshinei DESSERT', 'DESSERT', 'Payment', 'Other',
        'Oshinei DELIVERY', 'Special DELIVERY',
    )
    if not include_buffet_drink and not category:
        ph = ",".join(f":_excl_{i}" for i in range(len(EXCLUDED_CATS)))
        filters.append(
            f"(COALESCE(cat.name,'Uncategorised') NOT IN ({ph}) "
            f" OR UPPER(COALESCE(s.description,'')) LIKE '%ICE CREAM%' "
            f" OR UPPER(COALESCE(s.description,'')) LIKE '%ICECREAM%' "
            f" OR UPPER(COALESCE(s.description,'')) LIKE '%GELATO%' "
            f" OR UPPER(COALESCE(s.description,'')) LIKE '%SOFT SERVE%' "
            f" OR COALESCE(s.description,'') LIKE '%ไอติม%' "
            f" OR COALESCE(s.description,'') LIKE '%ไอศกรีม%' "
            f" OR COALESCE(s.description,'') LIKE '%ไอ-ติม%')"
        )
        for i, c in enumerate(EXCLUDED_CATS):
            params[f"_excl_{i}"] = c
    # "(B) …" dishes are eaten as part of the buffet — their revenue lives in the
    # package price, not against this SKU, so subtotal_ex_vat is near-zero while
    # cogs_total (summed over every refill, sometimes hundreds of thousands) is
    # real. Dividing by that near-zero denominator produced margin_pct in the
    # millions of percent (e.g. -21,713,531%) and could misclassify the dish as
    # a "cut this" dog/plowhorse. The EXCLUDED_CATS filter above only drops the
    # BUFFET-category package SKU itself, not these — filter on the naming
    # convention every other buffet-aware query in this codebase already uses.
    filters.append("COALESCE(s.description,'') NOT LIKE '(B)%'")
    where = "WHERE " + " AND ".join(filters) if filters else ""

    with get_db() as con:
        rows = con.execute(f"""
            SELECT
                s.sku_code,
                COALESCE(s.description, s.sku_code)          AS description,
                COALESCE(cat.name, 'Uncategorised')           AS category,
                SUM(ssm.qty)                                  AS qty,
                ROUND(SUM(ssm.subtotal_ex_vat), 2)            AS subtotal_ex_vat,
                ROUND(SUM(ssm.subtotal_ex_vat), 2)            AS revenue,
                ROUND(SUM(ssm.total_inc_vat), 2)              AS pos_total_inc_vat_diagnostic,
                SUM({_cogs_case(ssm='ssm', sk='s', sc='sc')}) AS cogs_total,
                SUM(CASE WHEN {_effective_cost_subquery(ssm='ssm', sc='sc')} IS NOT NULL
                         THEN ssm.qty ELSE 0 END)             AS costed_qty
            FROM v_sales_sku_monthly ssm
            JOIN skus s ON s.sku_id = ssm.sku_id
            JOIN branches b ON b.branch_id = ssm.branch_id
            LEFT JOIN categories cat ON cat.category_id = s.category_id
            LEFT JOIN sku_cost sc ON sc.sku_id = s.sku_id
            {where}
            GROUP BY s.sku_code, s.description, cat.name
        """, params).fetchall()

    if not rows:
        return {"items": [], "median_popularity_pct": 0, "median_margin": 0,
                "total_skus": 0, "costed_skus": 0}

    total_qty = sum(r["qty"] or 0 for r in rows) or 1

    items = []
    for r in rows:
        qty = r["qty"] or 0
        revenue = r["revenue"] or 0
        subtotal_ex = r["subtotal_ex_vat"] or 0
        cogs = r["cogs_total"]
        costed_qty = r["costed_qty"] or 0
        has_cost = cogs is not None and costed_qty > 0
        popularity_pct = round(qty / total_qty * 100, 4) if total_qty else 0
        margin_per_unit = round((subtotal_ex - cogs) / qty, 2) if has_cost and qty and cogs is not None else None
        margin_pct = round((subtotal_ex - cogs) / subtotal_ex * 100, 2) if has_cost and subtotal_ex and cogs is not None else None
        items.append({
            "sku_code": r["sku_code"],
            "description": r["description"],
            "category": r["category"],
            "qty": qty,
            "revenue": revenue,
            "margin_per_unit": margin_per_unit,
            "margin_pct": margin_pct,
            "popularity_pct": popularity_pct,
            "quadrant": None,
            "has_cost": has_cost,
        })

    costed = [i for i in items if i["has_cost"] and i["margin_per_unit"] is not None]
    total_skus = len(items)
    costed_skus = len(costed)

    if costed:
        pops_sorted = sorted(i["popularity_pct"] for i in costed)
        margs_sorted = sorted(i["margin_per_unit"] for i in costed)
        n = len(costed)

        def _median(lst):
            m = n // 2
            return (lst[m - 1] + lst[m]) / 2 if n % 2 == 0 else lst[m]

        med_pop = _median(pops_sorted)
        med_marg = _median(margs_sorted)

        for i in items:
            if not i["has_cost"] or i["margin_per_unit"] is None:
                continue
            high_pop = i["popularity_pct"] > med_pop
            high_marg = i["margin_per_unit"] > med_marg
            if high_pop and high_marg:
                i["quadrant"] = "star"
            elif high_pop and not high_marg:
                i["quadrant"] = "plowhorse"
            elif not high_pop and high_marg:
                i["quadrant"] = "puzzle"
            else:
                i["quadrant"] = "dog"
    else:
        med_pop = 0
        med_marg = 0

    return {
        "items": items,
        "median_popularity_pct": round(med_pop, 4),
        "median_margin": round(med_marg, 2),
        "total_skus": total_skus,
        "costed_skus": costed_skus,
    }




@router.get("/api/menu/engineering")
@ttl_cache
def menu_engineering(
    month: Optional[str] = Query(None),
    month_from: Optional[str] = Query(None),
    month_to: Optional[str] = Query(None),
    branch: Optional[str] = Query(None),
    category: Optional[str] = Query(None),
    include_buffet_drink: bool = Query(False, description="If false (default), exclude BUFFET/drink/dessert/payment so analysis focuses on actual food items"),
):
    return _menu_engineering_compute(month, month_from, month_to, branch, category, include_buffet_drink)


# ---------------------------------------------------------------------------
# DV7 (owner 2026-10-25, phone table review top-10 #10) · ปรับเมนู / โอกาสเมนู for the buffet: backend/menu_board.py.
# Both follow the top bar (kind / pick / date_from / date_to as gPeriodQuery sends, branch, group); an old client's
# month / month_from / month_to is read as a range of whole months.
# ---------------------------------------------------------------------------
_MB_COST_MODULES = {"cost.analysis", "profit.pnl"}       # the menu-spike card's rule: a role holding neither gets no cost key


@router.get("/api/menu/action-board")
@ttl_cache
def menu_action_board(
    kind: Optional[str] = Query(None), pick: Optional[str] = Query(None),
    date_from: Optional[str] = Query(None), date_to: Optional[str] = Query(None),
    branch: Optional[str] = Query(None), group: Optional[str] = Query(None),
    month: Optional[str] = Query(None), month_from: Optional[str] = Query(None), month_to: Optional[str] = Query(None),
):
    """ปรับเมนู: the buffet dishes that moved the cost per head, in baht (price / eat / down), with the branches and price tiers
    that pushed each, the cost and revenue per head of the scope, and à la carte (revenue, GP) - see menu_board.board.
    cost.analysis only (auth.MODULE_RULES)."""
    import menu_board as MB
    with get_db() as con:
        w = MB.window(con, kind, pick, date_from, date_to, branch, group, month, month_from, month_to)
        return MB.board(con, w, branch, group)


@ttl_cache
def _menu_opportunities_cached(kind, pick, date_from, date_to, branch, group, month, month_from, month_to, cost_view):
    import menu_board as MB
    with get_db() as con:
        w = MB.window(con, kind, pick, date_from, date_to, branch, group, month, month_from, month_to)
        return MB.opportunities(con, w, branch, group, cost_view=cost_view)


def _menu_opportunities_compute(branch=None, month=None, month_from=None, month_to=None, kind=None, pick=None,
                                date_from=None, date_to=None, group=None, cost_view=False):
    """In-process (the AI ideas): the opportunities without a cost key."""
    import menu_board as MB
    with get_db() as con:
        w = MB.window(con, kind, pick, date_from, date_to, branch, group, month, month_from, month_to)
        return MB.opportunities(con, w, branch, group, cost_view=cost_view)


@router.get("/api/menu/opportunities")
def menu_opportunities(
    request: Request = None,
    kind: Optional[str] = Query(None), pick: Optional[str] = Query(None),
    date_from: Optional[str] = Query(None), date_to: Optional[str] = Query(None),
    branch: Optional[str] = Query(None), group: Optional[str] = Query(None),
    month: Optional[str] = Query(None), month_from: Optional[str] = Query(None), month_to: Optional[str] = Query(None),
):
    """โอกาสเมนู: the branches that eat a buffet dish above the other branches' median for the same price tier (plates; baht
    for a role holding cost.analysis / profit.pnl), the à-la-carte dishes a branch under-sells (revenue in baht), the à-la-carte
    dishes that stopped and the categories - see menu_board.opportunities. menu.analysis (auth.MODULE_RULES); without a cost
    module not one cost key is computed or returned. A direct call without a request is the plates view."""
    cost_view = False
    if request is not None:
        import auth as _auth
        cost_view = bool(_auth.request_modules(request) & _MB_COST_MODULES)
    q = lambda v: v if isinstance(v, str) else None          # a direct call leaves the Query defaults
    return _menu_opportunities_cached(q(kind), q(pick), q(date_from), q(date_to), q(branch), q(group),
                                      q(month), q(month_from), q(month_to), cost_view)


class MenuIdeasBody(BaseModel):
    branch:     Optional[str] = None
    group:      Optional[str] = None
    kind:       Optional[str] = None
    pick:       Optional[str] = None
    date_from:  Optional[str] = None
    date_to:    Optional[str] = None
    month:      Optional[str] = None
    month_from: Optional[str] = None
    month_to:   Optional[str] = None


@router.post("/api/menu/ai-ideas", dependencies=[Depends(require_write_token)])
def menu_ai_ideas(body: MenuIdeasBody):
    """Draft NEW dish / promo CONCEPTS in Thai from the opportunity signals of the same period and scope (à-la-carte dishes a
    branch under-sells, growing categories, dishes that stopped; never a cost figure). Uses real Claude when ANTHROPIC_API_KEY
    is set, otherwise a deterministic data-derived fallback (no crash, no SDK). Concepts are creative and NOT proven by sales -
    the UI labels them as such."""
    import os, json
    opp = _menu_opportunities_compute(body.branch, body.month, body.month_from, body.month_to, body.kind, body.pick,
                                      body.date_from, body.date_to, body.group, cost_view=False)
    gaps = [{"description": g["name"], "potential_revenue": round(g["potential_revenue"]), "branches": [b["branch"] for b in g.get("branches") or []],
             "n_branches": g["n_branches"]} for g in opp.get("alc_gaps", {}).get("rows", [])[:8]]
    ws = [w for w in opp.get("category_whitespace", []) if w.get("label") == "expand"][:5]
    rev = [{"description": r["name"], "revenue_per_month": round(r["revenue_per_month"]), "last_month": r["last_month"]}
           for r in opp.get("revive", [])[:6]]

    # Always-available deterministic fallback — builds usable ideas straight from data.
    def deterministic():
        ideas = []
        for w in ws[:2]:
            ideas.append({
                "title": f"เพิ่มเมนูใหม่ในหมวด {w['category']}",
                "rationale": f"หมวดนี้มีรายได้ต่อเมนู {w['revenue_per_sku']:,.0f} บาท จาก {w['n_skus']} เมนู และรายได้ไม่ลดลง",
                "builds_on": "category_whitespace",
            })
        for g in gaps[:2]:
            ideas.append({
                "title": f"เพิ่มยอด {g['description']}",
                "rationale": f"{g['n_branches']} สาขาขายต่ำกว่าค่ากลางของเครือ รายได้ที่เพิ่มได้ประมาณ {g['potential_revenue']:,} บาท",
                "builds_on": "alc_gaps",
            })
        for r in rev[:2]:
            ideas.append({
                "title": f"นำ {r['description']} กลับมาขาย",
                "rationale": f"เคยมีรายได้เฉลี่ย {r['revenue_per_month']:,} บาทต่อเดือน และไม่มียอดขายตั้งแต่หลัง {r['last_month']}",
                "builds_on": "revive",
            })
        if not ideas:
            ideas.append({"title": "ยังไม่มีสัญญาณเด่นในช่วงนี้",
                          "rationale": "เลือกช่วงเวลาที่ยาวขึ้นหรือเลือกทุกสาขา",
                          "builds_on": "none"})
        return ideas

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return {"ideas": deterministic(), "generated_by": "fallback"}

    signals = {"alc_gaps": gaps, "category_whitespace": ws, "revive": rev}
    try:
        import urllib.request
        prompt = (
            "You are a menu-innovation advisor for a Thai buffet/à-la-carte restaurant chain. "
            "Based ONLY on these data signals, propose 4-6 concrete NEW dish or promotion ideas. "
            "Reply in THAI as a JSON array of objects with keys title, rationale, builds_on "
            "(builds_on = one of alc_gaps / category_whitespace / revive). "
            "Each rationale: 1-2 sentences, specific, cite the numbers. Output JSON only, no prose. "
            f"Signals: {json.dumps(signals, ensure_ascii=False)}"
        )
        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages", method="POST",
            headers={"Content-Type": "application/json", "x-api-key": api_key,
                     "anthropic-version": "2023-06-01"},
            data=json.dumps({
                "model": "claude-haiku-4-5-20251001", "max_tokens": 900,
                "messages": [{"role": "user", "content": prompt}],
            }).encode("utf-8"),
        )
        with urllib.request.urlopen(req, timeout=25) as r:
            data = json.loads(r.read().decode("utf-8"))
            text = data.get("content", [{}])[0].get("text", "") or ""
        txt = text.strip()
        if txt.startswith("```"):
            txt = txt.strip("`")
            txt = txt[txt.find("["):]
        ideas = json.loads(txt[txt.find("["): txt.rfind("]") + 1])
        ideas = [i for i in ideas if isinstance(i, dict) and i.get("title")]
        if not ideas:
            return {"ideas": deterministic(), "generated_by": "fallback (empty AI reply)"}
        return {"ideas": ideas, "generated_by": "claude"}
    except Exception as e:
        return {"ideas": deterministic(), "generated_by": f"fallback ({e})"}




# ---------------------------------------------------------------------------
# GET /api/menu/lifecycle   ●9
# Classify SKUs as New / Growing / Stable / Declining / Dead
# ---------------------------------------------------------------------------
@router.get("/api/menu/lifecycle")
@ttl_cache
def menu_lifecycle(
    branch:     Optional[str] = Query(None),
    months:     int           = Query(6, ge=3, le=12),
    month_from: Optional[str] = Query(None),
    month_to:   Optional[str] = Query(None),
):
    """Classify SKUs by 6-month qty trend into lifecycle stages."""
    with get_db() as con:
        # Last N months of SKU data
        max_month_row = con.execute(
            "SELECT MAX(period_month) AS m FROM sales_sku_monthly"
        ).fetchone()
        if not max_month_row or not max_month_row["m"]:
            return {"skus": [], "summary": {}}
        max_month = max_month_row["m"]
        # PR-PP2: closed months only - the month in progress (its last closed day short of the month end) read as a
        # drop in every dish's trend and as "dead" for a slow seller (a part month at the end of a monthly series)
        import periods as _P
        _a = _P.anchor(con)
        _lcm = (_a[:7] if _P.resolve("month", _a)["complete"] else _P.shift("month", _a, -1)[:7]) if _a else None
        if _lcm and max_month > _lcm:
            max_month = _lcm

        from datetime import date as _date
        import datetime as _dtt
        if month_to and _lcm and month_to[:7] > _lcm:
            month_to = _lcm
        if month_from and month_to:
            cutoff_month = min(month_from, month_to)
            max_month    = month_to
        elif month_from:
            cutoff_month = min(month_from, max_month)
        elif month_to:
            max_month = month_to
            y, mo = int(max_month[:4]), int(max_month[5:7])
            cutoff_dt = _date(y, mo, 1)
            for _ in range(months - 1):
                cutoff_dt = (cutoff_dt.replace(day=1) - _dtt.timedelta(days=1)).replace(day=1)
            cutoff_month = cutoff_dt.strftime("%Y-%m")
        else:
            # compute cutoff month relative to max available
            y, mo = int(max_month[:4]), int(max_month[5:7])
            cutoff_dt = _date(y, mo, 1)
            for _ in range(months - 1):
                cutoff_dt = (cutoff_dt.replace(day=1) - _dtt.timedelta(days=1)).replace(day=1)
            cutoff_month = cutoff_dt.strftime("%Y-%m")

        if branch:
            # Branch-filtered: join branches to apply name filter
            sku_rows = con.execute(
                """
                SELECT sk.sku_code,
                       COALESCE(sk.description, sk.sku_code) AS description,
                       c.name AS category,
                       sm.period_month AS m,
                       SUM(sm.qty)            AS qty,
                       SUM(sm.subtotal_ex_vat) AS rev
                FROM v_sales_sku_monthly sm
                JOIN branches   b  ON b.branch_id  = sm.branch_id
                JOIN skus       sk ON sk.sku_id     = sm.sku_id
                JOIN categories c  ON c.category_id = sk.category_id
                WHERE b.name = :branch
                  AND sm.period_month >= :cutoff AND sm.period_month <= :max_m
                GROUP BY sm.sku_id, sm.period_month
                ORDER BY sm.sku_id, sm.period_month
                """,
                {"branch": branch, "cutoff": cutoff_month, "max_m": max_month},
            ).fetchall()
        else:
            # All branches: skip branches JOIN — uses idx_ssm_sku_period covering index,
            # avoids temp B-TREE by grouping on sm.sku_id (in index key order)
            sku_rows = con.execute(
                """
                SELECT sk.sku_code,
                       COALESCE(sk.description, sk.sku_code) AS description,
                       c.name AS category,
                       sm.period_month AS m,
                       SUM(sm.qty)            AS qty,
                       SUM(sm.subtotal_ex_vat) AS rev
                FROM v_sales_sku_monthly sm
                JOIN skus       sk ON sk.sku_id     = sm.sku_id
                JOIN categories c  ON c.category_id = sk.category_id
                WHERE sm.period_month >= :cutoff AND sm.period_month <= :max_m
                GROUP BY sm.sku_id, sm.period_month
                ORDER BY sm.sku_id, sm.period_month
                """,
                {"cutoff": cutoff_month, "max_m": max_month},
            ).fetchall()

    from collections import defaultdict
    sku_data: dict = defaultdict(lambda: {"description": "", "category": "", "monthly": {}})
    for r in sku_rows:
        sd = sku_data[r["sku_code"]]
        sd["description"] = r["description"]
        sd["category"]    = r["category"]
        sd["monthly"][r["m"]] = r["qty"] or 0

    # Get all months in range
    all_months = []
    cutoff_dt = _date(int(cutoff_month[:4]), int(cutoff_month[5:7]), 1)
    cur = cutoff_dt
    max_dt = _date(int(max_month[:4]), int(max_month[5:7]), 1)
    while cur <= max_dt:
        all_months.append(cur.strftime("%Y-%m"))
        nxt = (cur.replace(day=28) + _dtt.timedelta(days=4)).replace(day=1)
        cur = nxt

    result = []
    for code, sd in sku_data.items():
        qty_series = [sd["monthly"].get(m, 0) for m in all_months]
        n = len(qty_series)

        # Lifecycle classification
        recent_2 = qty_series[-2:] if n >= 2 else qty_series
        all_nonzero_start = next((i for i, v in enumerate(qty_series) if v > 0), None)

        is_dead = all(v == 0 for v in recent_2) and any(v > 0 for v in qty_series)
        is_new  = all_nonzero_start is not None and all_nonzero_start >= n - 2

        # Trend: linear regression slope
        nonzero = [(i, v) for i, v in enumerate(qty_series) if v > 0]
        if len(nonzero) >= 3:
            xs = [p[0] for p in nonzero]
            ys = [p[1] for p in nonzero]
            xm = sum(xs) / len(xs)
            ym = sum(ys) / len(ys)
            num = sum((x - xm) * (y - ym) for x, y in zip(xs, ys))
            den = sum((x - xm) ** 2 for x in xs) or 1
            slope = num / den
            slope_pct = round(slope / ym * 100, 1) if ym else 0
        else:
            slope_pct = 0

        if is_dead:
            stage = "dead"
        elif is_new:
            stage = "new"
        elif slope_pct > 10:
            stage = "growing"
        elif slope_pct < -10:
            stage = "declining"
        else:
            stage = "stable"

        total_qty = sum(qty_series)
        result.append({
            "sku_code":    code,
            "description": sd["description"],
            "category":    sd["category"],
            "stage":       stage,
            "slope_pct":   slope_pct,
            "total_qty":   round(total_qty, 1),
            "avg_monthly": round(total_qty / n, 1) if n else 0,
            "series":      qty_series,
            "months":      all_months,
        })

    summary = {s: sum(1 for r in result if r["stage"] == s)
               for s in ("new", "growing", "stable", "declining", "dead")}
    result.sort(key=lambda x: {"growing": 0, "new": 1, "stable": 2, "declining": 3, "dead": 4}.get(x["stage"], 9))

    return {"skus": result, "summary": summary, "all_months": all_months}
