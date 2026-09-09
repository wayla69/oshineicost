"""Menu domain router — /api/menu/* (engineering / action-board /
opportunities / ai-ideas / lifecycle) + the _menu_*_compute helpers, moved
VERBATIM out of main.py (P1 structure refactor, step 4). No logic changes;
URLs unchanged. Same host-module pattern as routers/reports.py.
"""
# Shared infrastructure and helpers come from core.py by name — the old
# `globals().update(getattr(main, …))` bootstrap is gone (Phase 2, 2026-09).
from fastapi import Depends, Query
from pydantic import BaseModel
from typing import Optional
from core import (
    _FOOD_CATEGORY_FILTER, _cogs_case, _effective_cost_subquery, _month_range_filter, get_db,
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
# Lifecycle slope/stage for a set of SKUs over a month window — the SAME formula
# as /api/menu/lifecycle (linear-regression slope on non-zero months), factored
# so the Action Board can tag each dish growing/declining/dead without a second
# full lifecycle pass. Returns {sku_code: {"stage":…, "slope_pct":…}}.
# ---------------------------------------------------------------------------
def _menu_lifecycle_stages(con, branch=None, month_from=None, month_to=None):
    filters = []
    params: dict = {}
    if branch:
        filters.append("b.name = :branch")
        params["branch"] = branch
    _month_range_filter(filters, params, month_from, month_to, col="sm.period_month")
    where = ("WHERE " + " AND ".join(filters)) if filters else ""
    rows = con.execute(f"""
        SELECT sk.sku_code AS code, sm.period_month AS m, SUM(sm.qty) AS qty
        FROM v_sales_sku_monthly sm
        JOIN skus     sk ON sk.sku_id    = sm.sku_id
        JOIN branches b  ON b.branch_id  = sm.branch_id
        {where}
        GROUP BY sk.sku_code, sm.period_month
        ORDER BY sk.sku_code, sm.period_month
    """, params).fetchall()

    months = sorted({r["m"] for r in rows})
    from collections import defaultdict
    by_sku: dict = defaultdict(dict)
    for r in rows:
        by_sku[r["code"]][r["m"]] = r["qty"] or 0

    out: dict = {}
    for code, monthly in by_sku.items():
        series = [monthly.get(m, 0) for m in months]
        n = len(series)
        recent_2 = series[-2:] if n >= 2 else series
        nz_start = next((i for i, v in enumerate(series) if v > 0), None)
        is_dead = all(v == 0 for v in recent_2) and any(v > 0 for v in series)
        is_new  = nz_start is not None and nz_start >= n - 2
        nonzero = [(i, v) for i, v in enumerate(series) if v > 0]
        if len(nonzero) >= 3:
            xs = [p[0] for p in nonzero]; ys = [p[1] for p in nonzero]
            xm = sum(xs) / len(xs); ym = sum(ys) / len(ys)
            num = sum((x - xm) * (y - ym) for x, y in zip(xs, ys))
            den = sum((x - xm) ** 2 for x in xs) or 1
            slope_pct = round((num / den) / ym * 100, 1) if ym else 0
        else:
            slope_pct = 0
        if is_dead:        stage = "dead"
        elif is_new:       stage = "new"
        elif slope_pct > 10:  stage = "growing"
        elif slope_pct < -10: stage = "declining"
        else:              stage = "stable"
        out[code] = {"stage": stage, "slope_pct": slope_pct}
    return out


@router.get("/api/menu/action-board")
@ttl_cache
def menu_action_board(
    month: Optional[str] = Query(None),
    month_from: Optional[str] = Query(None),
    month_to: Optional[str] = Query(None),
    branch: Optional[str] = Query(None),
    category: Optional[str] = Query(None),
):
    """DECISION layer over Menu Engineering + lifecycle: sorts every food dish into
    one action bucket so a GM knows what to DO, not just what the numbers are.

      🚀 feature     — Stars (popular & profitable) → ดันต่อ / จัดโปรโมชัน
      🔍 market      — Puzzles (profitable but under-ordered) → โปรโมต/วางตำแหน่งใหม่
      💰 fix_margin  — Plowhorses (popular but margin บาง) → ขึ้นราคา/ลดต้นทุน
      ✂️ review_cut  — Dogs that are ALSO declining/dead & low-volume → ทบทวน/พิจารณาตัด
      ℹ️ needs_cost  — no cost data → ยังจัดอันดับด้วยมาร์จิ้นไม่ได้ (honest, not hidden)

    Reuses _menu_engineering_compute (identical quadrant/margin) + _menu_lifecycle_stages
    (identical slope/stage), so figures tie out to the Menu Engineering & Lifecycle tabs.
    review_cut is a FLAGGED shortlist with a reason — it never deletes anything."""
    eng = _menu_engineering_compute(month, month_from, month_to, branch, category)
    items = eng.get("items", [])
    with get_db() as con:
        stages = _menu_lifecycle_stages(con, branch, month_from or month, month_to or month)

    buckets: dict = {"feature": [], "market": [], "fix_margin": [], "review_cut": [], "needs_cost": []}
    for it in items:
        code = it["sku_code"]
        lc = stages.get(code, {"stage": "stable", "slope_pct": 0})
        stage = lc["stage"]; slope_pct = lc["slope_pct"]
        quad = it.get("quadrant")
        mpct = it.get("margin_pct")
        pop  = it.get("popularity_pct") or 0
        rev  = it.get("revenue") or 0

        row = {
            "sku_code": code, "description": it["description"], "category": it["category"],
            "qty": it["qty"], "revenue": rev,
            "margin_pct": mpct, "popularity_pct": pop,
            "quadrant": quad, "stage": stage, "slope_pct": slope_pct,
        }

        if not it.get("has_cost") or quad is None:
            row["action"] = "needs_cost"
            row["priority_score"] = rev
            row["reason"] = "ยังไม่มีข้อมูลต้นทุน — เพิ่ม BoM/ต้นทุนเพื่อจัดอันดับด้วยมาร์จิ้น"
            buckets["needs_cost"].append(row)
            continue

        if quad == "star":
            action = "feature"
            row["reason"] = f"ขายดี + กำไรดี (มาร์จิ้น {mpct}% · ส่วนแบ่ง {round(pop,2)}%)" + (" และกำลังโต" if stage == "growing" else "") + " → ดันต่อ/จัดโปรโมชัน"
        elif quad == "puzzle":
            # high margin, low popularity → push volume; a growing puzzle is the best promo bet
            action = "feature" if stage == "growing" else "market"
            if action == "feature":
                row["reason"] = f"กำไรดีและกำลังโต (มาร์จิ้น {mpct}%, +{slope_pct}%/เดือน) → ดันให้เป็นดาว"
            else:
                row["reason"] = f"กำไรดีแต่ยังขายน้อย (มาร์จิ้น {mpct}% · ส่วนแบ่ง {round(pop,2)}%) → โปรโมต/วางตำแหน่งเมนู/อัปเซล"
        elif quad == "plowhorse":
            action = "fix_margin"
            row["reason"] = f"ขายดีแต่กำไรบาง (มาร์จิ้น {mpct}% · ส่วนแบ่ง {round(pop,2)}%) → ขึ้นราคาเล็กน้อยหรือลดต้นทุน"
        else:  # dog
            if stage in ("declining", "dead"):
                action = "review_cut"
                row["reason"] = f"ขายน้อย กำไรบาง และ{'ตายแล้ว' if stage=='dead' else 'กำลังลดลง'} (มาร์จิ้น {mpct}% · ส่วนแบ่ง {round(pop,2)}%) → ทบทวน/พิจารณาตัด"
            else:
                # dog but still stable/new — keep watching, not a cut candidate yet
                action = "market"
                row["reason"] = f"ขายน้อยและกำไรบาง (มาร์จิ้น {mpct}%) → ลองโปรโมต/ปรับเมนูก่อนตัดสินใจตัด"
        row["action"] = action
        # priority: feature/market rank by revenue (biggest upside first);
        # fix_margin by revenue at risk; review_cut by lowest revenue (least to lose)
        row["priority_score"] = rev
        buckets[action].append(row)

    for k, lst in buckets.items():
        reverse = (k != "review_cut")
        lst.sort(key=lambda r: r.get("priority_score") or 0, reverse=reverse)

    return {
        "buckets": buckets,
        "counts": {k: len(v) for k, v in buckets.items()},
        "median_popularity_pct": eng.get("median_popularity_pct", 0),
        "median_margin": eng.get("median_margin", 0),
        "total_skus": eng.get("total_skus", 0),
        "costed_skus": eng.get("costed_skus", 0),
        "month_range": [month_from or month, month_to or month],
    }


# ===========================================================================
# Menu Opportunities — data-driven "what could we add / revive / expand" signals.
# Deliberately COGS/margin-free (qty · revenue · share · trend only) so it stays
# under the menu.analysis module and is visible to Marketing / Innovation roles.
# ===========================================================================
def _opp_slope_pct(series):
    """Linear-regression %/period over non-zero points — same shape as lifecycle."""
    nonzero = [(i, v) for i, v in enumerate(series) if v > 0]
    if len(nonzero) < 3:
        return 0.0
    xs = [p[0] for p in nonzero]; ys = [p[1] for p in nonzero]
    xm = sum(xs) / len(xs); ym = sum(ys) / len(ys)
    num = sum((x - xm) * (y - ym) for x, y in zip(xs, ys))
    den = sum((x - xm) ** 2 for x in xs) or 1
    return round((num / den) / ym * 100, 1) if ym else 0.0


def _opp_median(vals):
    s = sorted(vals)
    n = len(s)
    if not n:
        return 0
    m = n // 2
    return (s[m - 1] + s[m]) / 2 if n % 2 == 0 else s[m]


def _menu_opportunities_compute(branch=None, month=None, month_from=None, month_to=None):
    """Three honest, sales-grounded opportunity signals (no bundle guesses — true
    item bundles need per-receipt lines we don't have broadly; branch-month
    co-occurrence is too coarse, so it's intentionally omitted):
      cross_branch_gaps  — chain winners a branch under-sells vs its peers
      category_whitespace — categories with demand concentrated in few SKUs / trending
      revive             — dead dishes that once sold well → relaunch candidates
    """
    base = ["b.name != 'ALL'", "COALESCE(b.active,1) = 1", "COALESCE(sk.active,1) = 1",
            "ssm.source = 'Easy'", _FOOD_CATEGORY_FILTER]
    params: dict = {}
    _month_range_filter(base, params, month_from, month_to, month, col="ssm.period_month")
    where = "WHERE " + " AND ".join(base)

    with get_db() as con:
        rows = con.execute(f"""
            SELECT b.name AS branch, sk.sku_code AS code,
                   COALESCE(sk.description, sk.sku_code) AS descr,
                   COALESCE(c.name, 'Uncategorised') AS category,
                   ssm.period_month AS m,
                   SUM(ssm.qty) AS qty, SUM(ssm.subtotal_ex_vat) AS rev
            FROM v_sales_sku_monthly ssm
            JOIN skus       sk ON sk.sku_id     = ssm.sku_id
            JOIN branches   b  ON b.branch_id   = ssm.branch_id
            JOIN categories c  ON c.category_id = sk.category_id
            {where}
            GROUP BY b.name, sk.sku_code, ssm.period_month
        """, params).fetchall()

    if not rows:
        return {"cross_branch_gaps": [], "category_whitespace": [], "revive": [],
                "month_range": [month_from or month, month_to or month]}

    months = sorted({r["m"] for r in rows})
    descr = {}; cat = {}
    sku_branch_qty: dict = {}           # code -> {branch: qty}
    branch_food_qty: dict = {}          # branch -> total food qty
    sku_month_qty: dict = {}            # code -> {month: qty}  (chain-wide)
    cat_month_qty: dict = {}            # category -> {month: qty}
    cat_rev: dict = {}                  # category -> revenue
    cat_skus: dict = {}                 # category -> set(code)
    for r in rows:
        code = r["code"]; br = r["branch"]; q = r["qty"] or 0; rv = r["rev"] or 0
        descr.setdefault(code, r["descr"]); cat.setdefault(code, r["category"])
        sku_branch_qty.setdefault(code, {}); sku_branch_qty[code][br] = sku_branch_qty[code].get(br, 0) + q
        branch_food_qty[br] = branch_food_qty.get(br, 0) + q
        sku_month_qty.setdefault(code, {}); sku_month_qty[code][r["m"]] = sku_month_qty[code].get(r["m"], 0) + q
        cname = r["category"]
        cat_month_qty.setdefault(cname, {}); cat_month_qty[cname][r["m"]] = cat_month_qty[cname].get(r["m"], 0) + q
        cat_rev[cname] = cat_rev.get(cname, 0) + rv
        cat_skus.setdefault(cname, set()).add(code)

    active_branches = [b for b, t in branch_food_qty.items() if t > 0]

    # ── 1. Cross-branch gaps ────────────────────────────────────────────────
    chain_qty = {c: sum(bq.values()) for c, bq in sku_branch_qty.items()}
    # proven chain winners: sold in ≥5 branches, ranked by chain volume
    winners = sorted(
        [c for c, bq in sku_branch_qty.items() if len([1 for v in bq.values() if v > 0]) >= 5],
        key=lambda c: chain_qty[c], reverse=True)[:40]
    gaps = []
    target_branches = [branch] if branch else active_branches
    for code in winners:
        bq = sku_branch_qty[code]
        peer_shares = [bq[b] / branch_food_qty[b] for b in active_branches
                       if branch_food_qty.get(b) and bq.get(b, 0) > 0]
        if len(peer_shares) < 3:
            continue
        med_share = _opp_median(peer_shares)
        if med_share <= 0:
            continue
        for b in target_branches:
            tot = branch_food_qty.get(b) or 0
            if tot <= 0:
                continue
            share = bq.get(b, 0) / tot
            if share < 0.4 * med_share:          # selling at <40% of peer norm
                potential = (med_share - share) * tot
                gaps.append({
                    "branch": b, "sku_code": code, "description": descr[code],
                    "category": cat[code],
                    "branch_qty": round(bq.get(b, 0)), "chain_qty": round(chain_qty[code]),
                    "peer_branches": len(peer_shares),
                    "potential_qty": round(potential),
                    "note": ("ยังไม่ขายเลย" if bq.get(b, 0) == 0 else "ขายต่ำกว่าค่ากลางสาขาอื่นมาก"),
                })
    gaps.sort(key=lambda g: g["potential_qty"], reverse=True)
    gaps = gaps[:30]

    # ── 2. Category whitespace ──────────────────────────────────────────────
    whitespace = []
    for cname, skus in cat_skus.items():
        qty = sum(sku_month_qty[c2].get(m, 0) for c2 in skus for m in months)
        n_skus = len(skus)
        rev = cat_rev.get(cname, 0)
        series = [cat_month_qty[cname].get(m, 0) for m in months]
        slope = _opp_slope_pct(series)
        rev_per_sku = round(rev / n_skus) if n_skus else 0
        qty_per_sku = round(qty / n_skus) if n_skus else 0
        whitespace.append({
            "category": cname, "n_skus": n_skus,
            "qty": round(qty), "revenue": round(rev),
            "qty_per_sku": qty_per_sku, "revenue_per_sku": rev_per_sku,
            "slope_pct": slope,
        })
    if whitespace:
        med_rps = _opp_median([w["revenue_per_sku"] for w in whitespace])
        for w in whitespace:
            if w["revenue_per_sku"] > med_rps and w["slope_pct"] >= 0:
                w["label"] = "expand"; w["reason"] = "ดีมานด์กระจุกในไม่กี่เมนู + ทรงตัว/โต → เพิ่มความหลากหลายได้"
            elif w["slope_pct"] < -10:
                w["label"] = "trim"; w["reason"] = "หมวดกำลังหดตัว → ทบทวนเมนูที่อ่อน"
            else:
                w["label"] = "watch"; w["reason"] = "เฝ้าดูแนวโน้ม"
        whitespace.sort(key=lambda w: (0 if w["label"] == "expand" else 1 if w["label"] == "trim" else 2,
                                       -w["revenue_per_sku"]))

    # ── 3. Revive dead winners ──────────────────────────────────────────────
    revive = []
    for code, mq in sku_month_qty.items():
        series = [mq.get(m, 0) for m in months]
        n = len(series)
        recent_2 = series[-2:] if n >= 2 else series
        if not (all(v == 0 for v in recent_2) and any(v > 0 for v in series)):
            continue
        alive = [v for v in series if v > 0]
        avg_alive = sum(alive) / len(alive) if alive else 0
        last_idx = max((i for i, v in enumerate(series) if v > 0), default=None)
        revive.append({
            "sku_code": code, "description": descr[code], "category": cat[code],
            "avg_monthly_when_alive": round(avg_alive),
            "total_qty": round(sum(series)),
            "last_active_month": months[last_idx] if last_idx is not None else None,
        })
    revive.sort(key=lambda r: r["avg_monthly_when_alive"], reverse=True)
    revive = revive[:20]

    return {"cross_branch_gaps": gaps, "category_whitespace": whitespace, "revive": revive,
            "month_range": [month_from or month, month_to or month]}


@router.get("/api/menu/opportunities")
@ttl_cache
def menu_opportunities(
    branch: Optional[str] = Query(None),
    month: Optional[str] = Query(None),
    month_from: Optional[str] = Query(None),
    month_to: Optional[str] = Query(None),
):
    """Data-driven menu opportunities (no COGS/margin — see _menu_opportunities_compute)."""
    return _menu_opportunities_compute(branch, month, month_from, month_to)


class MenuIdeasBody(BaseModel):
    branch:     Optional[str] = None
    month:      Optional[str] = None
    month_from: Optional[str] = None
    month_to:   Optional[str] = None


@router.post("/api/menu/ai-ideas", dependencies=[Depends(require_write_token)])
def menu_ai_ideas(body: MenuIdeasBody):
    """Draft NEW dish / promo CONCEPTS in Thai from the data-driven opportunity
    signals (cross-branch gaps, category whitespace, revive candidates). Uses real
    Claude when ANTHROPIC_API_KEY is set, otherwise a
    deterministic data-derived fallback (no crash, no SDK). Concepts are creative
    and NOT proven by sales — the UI labels them as such."""
    import os, json
    opp = _menu_opportunities_compute(body.branch, body.month, body.month_from, body.month_to)
    gaps = opp.get("cross_branch_gaps", [])[:8]
    ws   = [w for w in opp.get("category_whitespace", []) if w.get("label") == "expand"][:5]
    rev  = opp.get("revive", [])[:6]

    # Always-available deterministic fallback — builds usable ideas straight from data.
    def deterministic():
        ideas = []
        for w in ws[:2]:
            ideas.append({
                "title": f"เพิ่มเมนูใหม่ในหมวด {w['category']}",
                "rationale": f"หมวดนี้ทำรายได้/เมนูสูง ({w['revenue_per_sku']:,} บาท/เมนู) จากเพียง {w['n_skus']} เมนู — มีที่ว่างให้เติมความหลากหลาย",
                "builds_on": "category_whitespace",
            })
        for g in gaps[:2]:
            ideas.append({
                "title": f"ดัน {g['description']} ที่สาขา {g['branch']}",
                "rationale": f"สาขาอื่นขายดี (รวม {g['chain_qty']:,} ชิ้น) แต่สาขานี้{g['note']} — โอกาสเพิ่ม ~{g['potential_qty']:,} ชิ้น",
                "builds_on": "cross_branch_gaps",
            })
        for r in rev[:2]:
            ideas.append({
                "title": f"รื้อฟื้น/รีแบรนด์ {r['description']}",
                "rationale": f"เคยขายเฉลี่ย {r['avg_monthly_when_alive']:,} ชิ้น/เดือน แต่หยุดขายตั้งแต่ {r['last_active_month']} — กลับมาทำใหม่หรือพัฒนาเวอร์ชันใหม่",
                "builds_on": "revive",
            })
        if not ideas:
            ideas.append({"title": "ยังไม่มีสัญญาณข้อมูลเด่นในช่วงนี้",
                          "rationale": "ลองขยายช่วงเดือนหรือเลือกทุกสาขาเพื่อหาโอกาสเพิ่มเติม",
                          "builds_on": "none"})
        return ideas

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return {"ideas": deterministic(), "generated_by": "fallback"}

    signals = {"cross_branch_gaps": gaps, "category_whitespace": ws, "revive": rev}
    try:
        import urllib.request
        prompt = (
            "You are a menu-innovation advisor for a Thai buffet/à-la-carte restaurant chain. "
            "Based ONLY on these data signals, propose 4-6 concrete NEW dish or promotion ideas. "
            "Reply in THAI as a JSON array of objects with keys title, rationale, builds_on "
            "(builds_on = one of cross_branch_gaps / category_whitespace / revive). "
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
# GET /api/menu/lifecycle   🟢9
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

        from datetime import date as _date
        import datetime as _dtt
        if month_from and month_to:
            cutoff_month = month_from
            max_month    = month_to
        elif month_from:
            cutoff_month = month_from
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
