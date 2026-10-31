"""Reports domain router — every /api/reports/* endpoint, moved VERBATIM out
of main.py (P1 structure refactor, step 1). No logic changes; URLs unchanged.

Shared infra (get_db, ttl_cache, _cogs_case, _group_filter, …) still lives in
main.py: this module is imported at the BOTTOM of main.py, when that module is
fully initialized, and pulls the shared names off it below. A later phase can
hoist the infra into a core.py and flip the import direction. main.py is
imported as top-level `main` locally/in tests but as `backend.main` under the
production uvicorn (`backend.main:app`) — resolved via sys.modules so we never
double-import the app.
"""
# Shared infrastructure and helpers come from core.py by name — the old
# `globals().update(getattr(main, …))` bootstrap is gone (Phase 2, 2026-09).
from fastapi import HTTPException, Query, Request, Response
from fastapi.responses import StreamingResponse
from typing import Optional
from downloads import attachment
import json as _json
import io as _io
from rules import VAT_DIVISOR, closed_branches, discount_base_row, drop_closed, hidden_amount, discount_excess, discount_policy_pct, effective_unit_cost_sql, food_cost_status_th, food_cost_target, needs_cost_sql, net_revenue_row, net_revenue_sum, raw_unit_cost_sql, scope_filter, tier_code_sql
from rules import GRAND_MENU_FROM, bill_item_branch_sql, TIER_EXTRA, month_projection, tier_codes_between, tier_label
from targets import attainment, target_revenue_total, targets_for_month
from core import (HEAVY_TTL,
    HERE,
    _FOOD_CATEGORY_FILTER, _build_sku_effective_cost, _cogs_case, _iso_week_sql, _unified_cogs_expr,
    get_db, ttl_cache,
)

from fastapi import APIRouter
router = APIRouter()


# ── Top-60 menu cost-per-head · MASTER branches (overview → per-branch → variance) ──
# Powers the "Top 60 ต้นทุน/หัว · สาขา Master" report tab. COGS via the LIVE
# _unified_cogs_expr (same source of truth as the rest of the app), so the in-app
# tab, the printable HTML, and the Excel all tie out. Master-only by design — the
# whole point is cross-branch benchmarking against the combined overview.
_CH_PREMIUM_KW = [
    "แซลมอน", "ทุเรียน", "หอยเชลล์", "กุ้ง", "ปู", "ทูน่า", "อูนิ", "ไข่ปลา",
    "ไข่หอยเม่น", "คาเวียร์", "ปลาดิบ", "ซาชิมิ", "ดอง", "เนื้อ", "วากิว",
    "โอโทโร่", "ชูโทโร่", "อิคุระ", "หอยนางรม", "สเต็ก", "ออส", "ปลาไหล", "อูนางิ",
]


def _ch_is_premium(desc: str) -> bool:
    d = desc or ""
    return any(k in d for k in _CH_PREMIUM_KW)


# scope → branch_type filter (active branches only). 'all' = master + franchise.
_CH_SCOPES = {
    "master":    "branch_type='master' AND active=1",
    "franchise": "branch_type='franchise' AND active=1",
    "all":       "active=1",
}
_CH_SCOPE_LABEL = {"master": "Master", "franchise": "แฟรนไชส์", "all": "ทุกสาขา"}


def _cost_head_scopes(con, month: str, scope: str = "master"):
    """Build (overview, [branch,...]) scope dicts for `month`, generator-shaped so they
    can feed the standalone report's build_html/write_xlsx unchanged — but computed with
    the LIVE _unified_cogs_expr. `scope` ∈ master|franchise|all selects which branches.
    Returns (None, []) when no branch in scope has data.

    cost-per-head of a menu = (menu monthly COGS) ÷ (scope covers = Σ customer_count).
    Buffet TIER rows are 0-cost inside the expr (consumption is on the B0xxx item rows),
    so they drop out of the costed menu list but their package revenue is added back into
    the food-cost-% denominator (Σ subtotal_ex_vat over ALL rows)."""
    cogs = _unified_cogs_expr("ssm")
    where = _CH_SCOPES.get(scope, _CH_SCOPES["master"])
    masters = [(r["branch_id"], r["name"]) for r in con.execute(
        f"SELECT branch_id, name FROM branches WHERE {where} ORDER BY branch_id").fetchall()]
    masters = [(bid, nm) for bid, nm in masters if (con.execute(
        "SELECT COALESCE(SUM(customer_count),0) FROM sales_month_summary "
        "WHERE period_month=? AND branch_id=?", (month, bid)).fetchone()[0] or 0) > 0]
    if not masters:
        return None, []

    def covers(ids):
        ph = ",".join("?" * len(ids))
        return int(con.execute(
            f"SELECT COALESCE(SUM(customer_count),0) FROM sales_month_summary "
            f"WHERE period_month=? AND branch_id IN ({ph})", [month, *ids]).fetchone()[0] or 0)

    def revenue(ids):
        ph = ",".join("?" * len(ids))
        return float(con.execute(
            f"SELECT COALESCE(SUM(subtotal_ex_vat),0) FROM v_sales_sku_monthly "
            f"WHERE period_month=? AND branch_id IN ({ph})", [month, *ids]).fetchone()[0] or 0)

    def menus(ids):
        ph = ",".join("?" * len(ids))
        rows = con.execute(
            f"""SELECT ssm.sku_id AS sku_id, sk.sku_code AS code, sk.description AS desc,
                       ROUND(SUM(ssm.qty),0) AS qty, ROUND(SUM({cogs}),2) AS cogs
                FROM v_sales_sku_monthly ssm
                JOIN skus sk ON sk.sku_id = ssm.sku_id
                WHERE ssm.period_month=? AND ssm.branch_id IN ({ph})
                GROUP BY ssm.sku_id HAVING SUM({cogs}) > 0
                ORDER BY cogs DESC""", [month, *ids]).fetchall()
        return [dict(r) for r in rows]

    def scope(name, ids):
        cv = covers(ids); ms = menus(ids)
        tc = sum(m["cogs"] for m in ms); tr = revenue(ids)
        for m in ms:
            m["cph"] = (m["cogs"] / cv) if cv else 0.0
            m["qph"] = (m["qty"] / cv) if cv else 0.0
            m["share"] = (m["cogs"] / tc) if tc else 0.0
            m["rev"] = 0.0
        return {"name": name, "branch_ids": ids, "covers": cv, "menus": ms,
                "total_cogs": tc, "total_rev": tr,
                "cph_total": (tc / cv) if cv else 0.0,
                "food_pct": (tc / tr * 100) if tr else 0.0,
                "by_sku": {m["sku_id"]: m for m in ms}}

    all_ids = [bid for bid, _ in masters]
    overview = scope("ภาพรวมทุกสาขา", all_ids)
    branches = [scope(nm, [bid]) for bid, nm in masters]
    return overview, branches


def _short_label(desc, n=28):
    """Truncate a dish name on a word boundary, marking it with '…' when it
    actually happens — a bare desc[:n] slice cut names mid-word with no
    indicator ("แซลมอน ซาชิมิ (1"), which read as a data error rather than
    a shortened label."""
    if len(desc) <= n:
        return desc
    cut = desc[:n].rsplit(" ", 1)[0] or desc[:n]
    return cut.rstrip() + "…"


def _cost_head_deviation(scope, overview):
    """Per-menu contribution to (branch cph − overview cph), most-positive / most-negative."""
    ov = overview["by_sku"]
    union = set(scope["by_sku"]) | set(ov)
    items = []
    for sid in union:
        b = scope["by_sku"].get(sid); o = ov.get(sid)
        src = b or o
        b_cph = b["cph"] if b else 0.0
        o_cph = o["cph"] if o else 0.0
        items.append({"code": src["code"], "desc": src["desc"],
                      "prem": _ch_is_premium(src["desc"]),
                      "b_cph": b_cph, "o_cph": o_cph, "d": b_cph - o_cph})
    items.sort(key=lambda x: x["d"])
    lows = [x for x in items if x["d"] < 0][:8]
    highs = [x for x in items if x["d"] > 0][-8:][::-1]
    return highs, lows


def _load_top60_gen():
    """Lazy-import the standalone report generator (presentation only: build_html /
    write_xlsx). Kept out of module import so the JSON tab endpoint never depends on it."""
    import importlib, sys as _sys
    bd = str(HERE)
    if bd not in _sys.path:
        _sys.path.insert(0, bd)
    return importlib.import_module("gen_top60_cost_per_head_report")


_CH_TH_MONTHS = ["", "มกราคม", "กุมภาพันธ์", "มีนาคม", "เมษายน", "พฤษภาคม", "มิถุนายน",
                 "กรกฎาคม", "สิงหาคม", "กันยายน", "ตุลาคม", "พฤศจิกายน", "ธันวาคม"]


def _ch_gen_dt():
    import datetime as _dt
    d = _dt.date.today()
    return f"{d.day} {_CH_TH_MONTHS[d.month]} {d.year + 543}"


def _ch_resolve_month(con, month):
    """Default to the latest month with active-branch covers, EXCLUDING the current
    (in-progress) calendar month.

    Not plain MAX(period_month): now that sales_month_summary is backfilled for
    live-synced months, a handful of sales on the 1st create a summary row for the
    brand-new month — defaulting to it would open the report on a near-empty
    sliver. Exclude the current Thai calendar month (UTC+7) so the report opens on
    the newest month with a meaningful full picture. Scope-agnostic."""
    if month:
        return month
    # Current calendar month in Thai time (POS local); '+7 hours' aligns UTC 'now'.
    cur_ym = con.execute("SELECT strftime('%Y-%m','now','+7 hours')").fetchone()[0]
    r = con.execute(
        "SELECT MAX(s.period_month) FROM sales_month_summary s "
        "JOIN branches b ON b.branch_id = s.branch_id "
        "WHERE b.active=1 AND s.customer_count > 0 AND s.period_month < ?",
        (cur_ym,)).fetchone()
    if r and r[0]:
        return r[0]
    r2 = con.execute("SELECT MAX(period_month) FROM sales_sku_monthly "
                     "WHERE period_month < ?", (cur_ym,)).fetchone()
    if r2 and r2[0]:
        return r2[0]
    # Fallback: nothing before the current month → latest available anywhere.
    r3 = con.execute("SELECT MAX(period_month) FROM sales_sku_monthly").fetchone()
    return r3[0] if r3 else None


# ---------------------------------------------------------------------------
# Menu Impact (วิเคราะห์วัตถุดิบ) — with/without per-head analysis of one menu
# SKU or ingredient group over a chosen date range. Powered by the pre-aggregated
# menu_fact_day / menu_fact_total tables (built from live MSSQL bill-level data
# by backend/build_menu_fact.py — run it after each sync to keep coverage fresh).
# Stratified by branch x day so branch mix / calendar mix can't fake the effect.
# ---------------------------------------------------------------------------
_MI_SCOPE_LABEL = {"master": "Master (เจ้าของเอง)", "franchise": "แฟรนไชส์", "all": "ทั้งหมด"}


def _mi_branch_ids(con, scope: str):
    if scope in ("master", "franchise"):
        return [r[0] for r in con.execute(
            "SELECT branch_id FROM branches WHERE branch_type=?", (scope,))]
    return [r[0] for r in con.execute("SELECT branch_id FROM branches")]


def _mi_ready(con) -> bool:
    return bool(con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='menu_fact_total'").fetchone())


@router.get("/api/reports/menu-impact/entities")
@ttl_cache
def menu_impact_entities():
    """Selector list for the tab: ingredient groups + individual menu SKUs that
    actually sold inside the fact coverage, plus coverage/build info."""
    with get_db() as con:
        if not _mi_ready(con):
            return {"ready": False, "groups": [], "skus": [], "coverage": None,
                    "hint": "ยังไม่มีตารางข้อมูล — รัน python backend/build_menu_fact.py ก่อน"}
        lo, hi = con.execute("SELECT MIN(day), MAX(day) FROM menu_fact_total").fetchone()
        state = dict(con.execute("SELECT k, v FROM menu_fact_state").fetchall())
        qty = dict(con.execute("SELECT ent, SUM(qty) FROM menu_fact_day GROUP BY ent").fetchall())
        groups, skus = [], []
        for ent, label, kind, unit_cost, members in con.execute(
                "SELECT ent, label, kind, unit_cost, members FROM menu_fact_meta"):
            q = qty.get(ent) or 0
            if q <= 0:
                continue
            row = {"ent": ent, "label": label, "cost": round(unit_cost or 0, 2),
                   "qty": round(q)}
            if kind == "group":
                row["n_members"] = len([m for m in (members or "").split(",") if m])
                groups.append(row)
            else:
                skus.append(row)
        skus.sort(key=lambda r: -r["qty"])
        return {"ready": True, "coverage": [lo, hi], "last_build": state.get("last_build"),
                # The tab opens on the live menu when the data reaches it: a range
                # starting before the changeover mixes two price lists (see the
                # era guard in price_tier_report). The pickers stay free — this is
                # only where they land on first open.
                "menu_era_from": GRAND_MENU_FROM,
                "default_from": (GRAND_MENU_FROM if hi and hi >= GRAND_MENU_FROM else lo),
                "hc_cost": float(state.get("hc_cost") or 30), "groups": groups, "skus": skus[:400]}


# Every tier code the fact tables can hold, richest first — BOTH menus, because a
# range can end before the Grand Menu or start after it. Which of them a given
# request may actually filter on is narrowed to the era of ITS window by
# _mi_tiers_for (a September request cannot ask for a retired ฿590 tier).
_MI_TIER_ORDER = tier_codes_between(None, None)


def _mi_tiers_for(date_from=None, date_to=None) -> tuple:
    """The tier codes on sale in [date_from, date_to] — what the tier picker on
    the tab offers, and the only values the filter accepts for that window."""
    return tier_codes_between(date_from, date_to)


@router.get("/api/reports/menu-impact/tiers")
def menu_impact_tiers(date_from: Optional[str] = Query(None),
                      date_to: Optional[str] = Query(None)):
    """Tier options for the chosen window: prices actually sold in it, richest
    first, each with its display label. Feeds the tier <select>, so the picker
    stops offering prices the menu retired on 2026-09-01."""
    codes = _mi_tiers_for(date_from, date_to)
    return {"grand_menu_from": GRAND_MENU_FROM,
            "tiers": [{"tier": t, "label": tier_label(t)} for t in codes
                      if t != "child"]}


@router.get("/api/reports/menu-impact")
@ttl_cache
def menu_impact(ent: str = Query(..., description="s:<sku_id> หรือ g:<slug>"),
                date_from: Optional[str] = Query(None), date_to: Optional[str] = Query(None),
                scope: str = Query("all", description="master | franchise | all"),
                tier: str = Query("all", description="all | <price on sale in the window> | promo | child")):
    """เลือกวัตถุดิบ/เมนู + ช่วงเวลา (+Tier ราคาบุฟเฟ่ต์) → เทียบบิลที่มี vs ไม่มี
    (ต่อหัว, คุมสาขา×วัน×tier)."""
    if scope not in _MI_SCOPE_LABEL:
        scope = "all"
    with get_db() as con:
        if not _mi_ready(con):
            return {"ready": False, "hint": "รัน python backend/build_menu_fact.py ก่อนใช้งานแท็บนี้"}
        # tier column arrived in fact-schema v2 — degrade gracefully on a v1 DB
        has_tier = bool(con.execute(
            "SELECT 1 FROM pragma_table_info('menu_fact_total') WHERE name='tier'").fetchone())
        lo, hi = con.execute("SELECT MIN(day), MAX(day) FROM menu_fact_total").fetchone()
        if not lo:
            return {"ready": False, "hint": "ตารางข้อมูลว่าง — รัน build_menu_fact.py"}
        date_from = date_from or lo
        date_to = date_to or hi
        # A tier is only selectable if that price was on sale somewhere in THIS
        # window. A stale ฿590 left in the picker after the window moves into
        # September falls back to "all" instead of silently returning an empty
        # comparison for a price that is no longer sold.
        if tier != "all" and (tier not in _mi_tiers_for(date_from, date_to) or not has_tier):
            tier = "all"
        meta = con.execute("SELECT label, kind, unit_cost, members FROM menu_fact_meta WHERE ent=?",
                           (ent,)).fetchone()
        if not meta:
            return {"ready": True, "error": f"ไม่รู้จัก entity {ent}"}
        bids = _mi_branch_ids(con, scope)
        ph = ",".join("?" * len(bids))
        tier_col = "f.tier" if has_tier else "''"
        tier_col_t = "tier" if has_tier else "''"
        join_tier = "AND t.tier = f.tier" if has_tier else ""
        tier_where_f = "AND f.tier = ?" if tier != "all" else ""
        tier_where_t = "AND tier = ?" if tier != "all" else ""
        tp1 = [tier] if tier != "all" else []
        f = con.execute(
            f"""SELECT f.branch_id, f.day, {tier_col}, f.bills, f.pax, f.qty, f.ent_cogs, f.ent_hc,
                       f.cogs, f.rev, f.hc,
                       t.bills, t.pax, t.cogs, t.rev, t.hc
                FROM menu_fact_day f JOIN menu_fact_total t
                  ON t.branch_id = f.branch_id AND t.day = f.day {join_tier}
                WHERE f.ent = ? AND f.day BETWEEN ? AND ? AND f.branch_id IN ({ph}) {tier_where_f}""",
            [ent, date_from, date_to] + bids + tp1).fetchall()
        tot = con.execute(
            f"""SELECT branch_id, day, {tier_col_t}, bills, pax, cogs, rev, hc FROM menu_fact_total
                WHERE day BETWEEN ? AND ? AND branch_id IN ({ph}) {tier_where_t}""",
            [date_from, date_to] + bids + tp1).fetchall()
        bname = dict(con.execute("SELECT branch_id, name FROM branches").fetchall())

    T = {"bills": 0, "pax": 0.0, "cogs": 0.0, "rev": 0.0, "hc": 0.0}
    tot_tier = {}
    for _b, _d, tr_, nb, px, cg, rv, hc in tot:
        T["bills"] += nb; T["pax"] += px; T["cogs"] += cg; T["rev"] += rv; T["hc"] += hc
        tt = tot_tier.setdefault(tr_, {"bills": 0, "pax": 0.0})
        tt["bills"] += nb; tt["pax"] += px
    if T["pax"] <= 0:
        return {"ready": True, "error": "ไม่มีข้อมูลบิลบุฟเฟ่ต์ในช่วง/ขอบเขตที่เลือก",
                "coverage": [lo, hi]}

    # with-set summary + stratified with/without (stratum = branch x day)
    W = {"bills": 0, "pax": 0.0, "qty": 0.0, "ecogs": 0.0}
    wsum = {k: 0.0 for k in ("cogs_w", "cogs_o", "oth_w", "oth_o", "hc_w", "hc_o",
                             "rev_w", "rev_o", "gp_w", "gp_o")}
    wt = 0.0
    by_branch, by_month, by_day, by_tier = {}, {}, {}, {}
    for r in f:
        (bid, day, tr_, nb, px, q, ec, eh, cg, rv, hc, tb, tp, tc, trv, th) = r
        W["bills"] += nb; W["pax"] += px; W["qty"] += q; W["ecogs"] += ec
        bb = by_branch.setdefault(bid, {"bills": 0, "pax": 0.0, "qty": 0.0, "ecogs": 0.0,
                                        "d_oth": 0.0, "w": 0.0})
        bb["bills"] += nb; bb["pax"] += px; bb["qty"] += q; bb["ecogs"] += ec
        tb_ = by_tier.setdefault(tr_, {"bills": 0, "pax": 0.0, "qty": 0.0, "ecogs": 0.0,
                                       "d_oth": 0.0, "d_gp": 0.0, "w": 0.0})
        tb_["bills"] += nb; tb_["pax"] += px; tb_["qty"] += q; tb_["ecogs"] += ec
        for key, dct in ((day[:7], by_month), (day, by_day)):
            m = dct.setdefault(key, {"bills": 0, "pax": 0.0, "qty": 0.0, "ecogs": 0.0})
            m["bills"] += nb; m["pax"] += px; m["qty"] += q; m["ecogs"] += ec
        opx, onb = tp - px, tb - nb
        if px <= 0 or opx <= 0 or onb <= 0:
            continue
        w = px * opx / (px + opx)
        wt += w
        oc, orv, ohc = tc - cg, trv - rv, th - hc
        wsum["cogs_w"] += w * cg / px;             wsum["cogs_o"] += w * oc / opx
        wsum["oth_w"] += w * (cg - ec) / px;       wsum["oth_o"] += w * oc / opx
        wsum["hc_w"] += w * (hc - eh) / px;        wsum["hc_o"] += w * ohc / opx
        wsum["rev_w"] += w * rv / px;              wsum["rev_o"] += w * orv / opx
        wsum["gp_w"] += w * (rv - cg) / px;        wsum["gp_o"] += w * (orv - oc) / opx
        bb["d_oth"] += w * ((cg - ec) / px - oc / opx); bb["w"] += w
        tb_["d_oth"] += w * ((cg - ec) / px - oc / opx)
        tb_["d_gp"] += w * ((rv - cg) / px - (orv - oc) / opx)
        tb_["w"] += w

    if wt <= 0:
        return {"ready": True, "error": "ข้อมูลไม่พอสำหรับเทียบมี/ไม่มี (ทุกวันมีหรือไม่มีทั้งหมด)",
                "coverage": [lo, hi]}
    for k in wsum:
        wsum[k] /= wt
    # mix-adjusted: high-cost consumption expected if it scaled with total "other" appetite
    exp_hc = wsum["hc_o"] * (wsum["oth_w"] / wsum["oth_o"]) if wsum["oth_o"] > 0 else None
    label, kind, unit_cost, members = meta
    trend_m = [{"ym": k, "pen": v["bills"], "qty": v["qty"], "ecogs": v["ecogs"]}
               for k, v in sorted(by_month.items())]
    tot_m, tot_d = {}, {}
    for b, d, tr_, nb, px, cg, rv, hc in tot:
        for key, dct in ((d[:7], tot_m), (d, tot_d)):
            m = dct.setdefault(key, {"bills": 0, "pax": 0.0})
            m["bills"] += nb; m["pax"] += px
    for row in trend_m:
        tm = tot_m.get(row["ym"], {"bills": 0, "pax": 0.0})
        row["pen"] = round(row["pen"] / tm["bills"], 4) if tm["bills"] else None
        row["qty_head"] = round(row["qty"] / tm["pax"], 3) if tm["pax"] else None
        row["ecogs_head"] = round(row["ecogs"] / tm["pax"], 2) if tm["pax"] else None
        row["qty"] = round(row["qty"]); row["ecogs"] = round(row["ecogs"])
    trend_d = []
    for k in sorted(tot_d):
        v = by_day.get(k, {"bills": 0, "qty": 0.0, "ecogs": 0.0})
        td = tot_d[k]
        trend_d.append({"day": k,
                        "pen": round(v["bills"] / td["bills"], 4) if td["bills"] else None,
                        "qty_head": round(v["qty"] / td["pax"], 3) if td["pax"] else None})
    branches_out = sorted((
        {"branch_id": b, "name": bname.get(b, str(b)), "bills": v["bills"],
         "qty": round(v["qty"]), "ecogs": round(v["ecogs"]),
         "qty_head_takers": round(v["qty"] / v["pax"], 2) if v["pax"] else None,
         "d_other_cogs": round(v["d_oth"] / v["w"], 2) if v["w"] > 0 else None}
        for b, v in by_branch.items()), key=lambda r: -(r["ecogs"] or 0))
    rd = lambda x, nd=2: round(x, nd)
    # per-tier breakdown (only meaningful when looking across all tiers).
    # A window reaching into the Grand Menu era shows the NEW price list only:
    # the retired prices were a different product, and listing both side by side
    # invited reading a ฿799 row as a peer of ฿789. The bills themselves stay in
    # the with/without comparison above (each day is compared against itself, so
    # the changeover never distorts it) — only the tier BREAKDOWN is narrowed.
    tier_order = _MI_TIER_ORDER
    era_tiers_only = has_tier and (date_to or hi) >= GRAND_MENU_FROM
    if era_tiers_only:
        tier_order = tier_codes_between(GRAND_MENU_FROM, date_to or hi)
    tiers_out = []
    if has_tier:
        for tr_ in tier_order:
            v = by_tier.get(tr_)
            tt = tot_tier.get(tr_)
            if not v or not tt or not tt["bills"]:
                continue
            tiers_out.append({
                "tier": tr_, "bills": tt["bills"], "bills_with": v["bills"],
                "pen": rd(v["bills"] / tt["bills"], 4),
                "qty": rd(v["qty"], 0),
                "qty_head_takers": rd(v["qty"] / v["pax"]) if v["pax"] else None,
                "ecogs_head_takers": rd(v["ecogs"] / v["pax"]) if v["pax"] else None,
                "ecogs": rd(v["ecogs"], 0),
                "d_other_cogs": rd(v["d_oth"] / v["w"]) if v["w"] > 0 else None,
                "d_gp": rd(v["d_gp"] / v["w"]) if v["w"] > 0 else None,
            })
    return {
        "ready": True, "ent": ent, "label": label, "kind": kind, "has_tier": has_tier,
        "tier": tier, "tiers": tiers_out, "era_tiers_only": bool(era_tiers_only),
        "menu_era_from": GRAND_MENU_FROM,
        "unit_cost": rd(unit_cost or 0), "scope": scope, "scope_label": _MI_SCOPE_LABEL[scope],
        "members": [m for m in (members or "").split(",") if m] if kind == "group" else None,
        "coverage": [lo, hi], "date_from": date_from, "date_to": date_to,
        "totals": {"bills": T["bills"], "pax": rd(T["pax"], 0), "cogs_head": rd(T["cogs"] / T["pax"]),
                   "rev_head": rd(T["rev"] / T["pax"]), "hc_head": rd(T["hc"] / T["pax"])},
        "summary": {
            "pen_bills": rd(W["bills"] / T["bills"], 4), "bills_with": W["bills"],
            "qty": rd(W["qty"], 0), "ecogs": rd(W["ecogs"], 0),
            "ecogs_share": rd(W["ecogs"] / T["cogs"], 4) if T["cogs"] else None,
            "qty_head_takers": rd(W["qty"] / W["pax"]) if W["pax"] else None,
            "ecogs_head_takers": rd(W["ecogs"] / W["pax"]) if W["pax"] else None,
            "ecogs_head_all": rd(W["ecogs"] / T["pax"]),
        },
        "compare": {   # per-head, stratified branch x day, harmonic-pax weights
            "cogs": {"with": rd(wsum["cogs_w"]), "without": rd(wsum["cogs_o"]),
                     "diff": rd(wsum["cogs_w"] - wsum["cogs_o"])},
            "other_cogs": {"with": rd(wsum["oth_w"]), "without": rd(wsum["oth_o"]),
                           "diff": rd(wsum["oth_w"] - wsum["oth_o"])},
            "hc_other": {"with": rd(wsum["hc_w"]), "without": rd(wsum["hc_o"]),
                         "diff": rd(wsum["hc_w"] - wsum["hc_o"])},
            "rev": {"with": rd(wsum["rev_w"]), "without": rd(wsum["rev_o"]),
                    "diff": rd(wsum["rev_w"] - wsum["rev_o"])},
            "gp": {"with": rd(wsum["gp_w"]), "without": rd(wsum["gp_o"]),
                   "diff": rd(wsum["gp_w"] - wsum["gp_o"])},
            "hc_expected_prop": rd(exp_hc) if exp_hc is not None else None,
            "hc_rel_saving": rd(wsum["hc_w"] - exp_hc) if exp_hc is not None else None,
        },
        "trend_month": trend_m, "trend_day": trend_d, "branches": branches_out,
    }


# ---------------------------------------------------------------------------
# Price-tier analysis (วิเคราะห์ Tier ราคาขายบุฟเฟ่ต์) — section of the tab.
# Reads the tier-dimensioned menu_fact_total / menu_fact_day fact tables
# (schema v2, build_menu_fact.py): every buffet bill carries tier = canonical
# adult-buffet price ex-VAT ('350'…'999'), 'promo' (non-canonical price) or
# 'child' (no adult cover). Compares the chosen range A against the SAME-LENGTH
# window right before it (or the first vs second half of the range when there
# is not enough history before it).
# ---------------------------------------------------------------------------
_PT_TOP_N = 45          # "45 เมนูหลักในต้นทุน"
_PT_TREND_WEEKS = 8     # weekly trend window — 8 ISO weeks, ~2 months of detail
_PT_OLD_ERA = "@old"    # suffix marking a tier priced on the pre-Grand-Menu list
# Buffet price tiers, richest first, from rules.tier_codes_between — the one place
# that knows which prices are on sale when. TWO eras live side by side in this
# unbounded list: the Grand Menu from 2026-09-01 quotes NET prices
# (459/629/789/999/1299 - service charge and VAT already inside), the menu before it
# quoted the price BEFORE both (350/590/649/689/799/999). A tier missing here is
# silently dropped from the report, so the full list is what belongs; the rows a
# given period actually shows are filtered to the tiers with guests on them, which
# is what keeps a retired price off a September report.
_PT_ORDER = tier_codes_between(None, None)
_PT_LABEL = {t: tier_label(t) for t in TIER_EXTRA}
# A comparison whose baseline period falls entirely before GRAND_MENU_FROM while
# the current period is on/after it would silently blend two different menus
# under one label (both eras use "999", say) — guarded in price_tier_report and
# _mgt_tier by turning the comparison off rather than showing a delta that isn't
# really the same product on both sides.
_PT_NEW_MENU_FROM = GRAND_MENU_FROM


def _pt_label(t):
    return tier_label(t)


def _pt_sum_tiers(con, ph, bids, d_from, d_to):
    """{tier: {bills,pax,cogs,rev,hc}} over a window (v2 fact tables)."""
    out = {}
    for t, nb, px, cg, rv, hc in con.execute(
            f"""SELECT tier, SUM(bills), SUM(pax), SUM(cogs), SUM(rev), SUM(hc)
                FROM menu_fact_total
                WHERE day BETWEEN ? AND ? AND branch_id IN ({ph}) GROUP BY tier""",
            [d_from, d_to] + bids):
        out[str(t or "promo")] = {"bills": nb or 0, "pax": px or 0.0, "cogs": cg or 0.0,
                                  "rev": rv or 0.0, "hc": hc or 0.0}
    return out


def _pt_bom_dishes_by_tier(con):
    """{tier: [{sku_code, label, bom_cost}, …]}, richest recipe cost first —
    the dishes a diner on that tier may actually eat, CUMULATIVE over the
    cheaper tiers: a ฿1,299 guest eats the ฿1,299 exclusives *and* everything
    a ฿459 guest eats, so listing only the 15 exclusives made ฿1,299 look like
    the cheapest menu in the house. sku_tier stores the tier a dish UNLOCKS at;
    the accumulation happens here.

    Tier keys are the price as text ("459"); anything non-numeric (promo, the
    pre-Grand-Menu eras) carries no dishes and is left out. Only the
    2026-09-01+ Grand Menu has a tier per SKU, so older menus yield {}."""
    own = {}
    try:
        for tier, code, label, cost in con.execute(
                """SELECT st.tier, s.sku_code, s.description, sec.recipe_cost
                   FROM sku_tier st JOIN skus s USING(sku_id)
                   JOIN sku_effective_cost sec USING(sku_id)"""):
            own.setdefault(str(tier), []).append(
                {"sku_code": code, "label": label or code,
                 "bom_cost": round(cost, 2) if cost is not None else None})
    except Exception:
        return {}                 # sku_tier not created yet (pre-migration DB copy)

    order = []
    for t in own:
        try:
            order.append((float(t), t))
        except (TypeError, ValueError):
            pass                  # not a price tier — nothing to accumulate into
    out, running = {}, []
    for _, t in sorted(order):
        running = running + own[t]
        out[t] = sorted(running, key=lambda r: -(r["bom_cost"] or 0))
    return out


def _pt_bom_cost_by_tier(con, dishes=None):
    """{tier: {bom_avg_cost, bom_dish_count}} — average RECIPE cost over the same
    cumulative dish list the drilldown shows, so the headline figure and the
    table underneath it can never disagree. Pass `dishes` to reuse a list already
    built; independent of what actually sold in any date range."""
    dishes = _pt_bom_dishes_by_tier(con) if dishes is None else dishes
    out = {}
    for t, rows in dishes.items():
        costs = [r["bom_cost"] for r in rows if r["bom_cost"] is not None]
        out[t] = {"bom_avg_cost": round(sum(costs) / len(costs), 2) if costs else None,
                  "bom_dish_count": len(rows)}
    return out


def _pt_metrics(v):
    px = v["pax"] or 0.0
    if px <= 0:
        return {"bills": v["bills"], "pax": 0, "rev_head": None, "cogs_head": None,
                "hc_head": None, "gp_head": None, "food_pct": None, "pax_bill": None}
    return {"bills": v["bills"], "pax": round(px),
            "rev_head": round(v["rev"] / px, 2), "cogs_head": round(v["cogs"] / px, 2),
            "hc_head": round(v["hc"] / px, 2), "gp_head": round((v["rev"] - v["cogs"]) / px, 2),
            "food_pct": round(v["cogs"] / v["rev"] * 100, 1) if v["rev"] else None,
            "pax_bill": round(px / v["bills"], 2) if v["bills"] else None}


def _pt_tier_trends(con, ph, bids, *, month_from, a_to):
    """Monthly + weekly tier trend over menu_fact_total.

    Shared by /api/reports/price-tier and §11 of the management report. Lifted out
    of price_tier_report so the two cannot drift: the era split, the partial-week
    flags and the choice of week window are each subtle enough that a second copy
    would quietly disagree with the first.

    `month_from` is the start of the MONTHLY series — the caller clamps it to the
    menu changeover when the analysis sits on the live menu, so the stacked mix
    chart can't put a retired ฿799 band next to a live ฿789 one. `a_to` ends BOTH
    series at the date the ANALYSIS ends: a June report must show June's weeks, not
    the newest eight in the database — and its monthly series must stop at June
    rather than run on into September, where "999" is a different product at a
    different price and the one line would read as one product drifting.
    """
    from datetime import date
    rd = lambda x, nd=2: round(x, nd) if x is not None else None

    trend = {}
    for ym, t, px, cg, rv in con.execute(
            f"""SELECT substr(day,1,7), tier, SUM(pax), SUM(cogs), SUM(rev)
                FROM menu_fact_total WHERE branch_id IN ({ph}) AND day >= ? AND day <= ?
                GROUP BY substr(day,1,7), tier""",
            bids + [month_from, a_to.isoformat()]):
        m = trend.setdefault(ym, {}).setdefault(str(t or "promo"),
                                                {"pax": 0.0, "cogs": 0.0, "rev": 0.0})
        m["pax"] += px or 0.0; m["cogs"] += cg or 0.0; m["rev"] += rv or 0.0

    # Weekly trend x tier — the last _PT_TREND_WEEKS ISO weeks. Monthly is too
    # coarse to see a tier moving right after a menu change, so the same charts get
    # a weekly granularity. _iso_week_sql is the app-wide week bucket (Mon-Sun,
    # correct across the Jan-1 boundary), so a week here means the same days a week
    # means everywhere else in the app.
    #
    # It deliberately does NOT take the monthly chart's era clamp: clamped to
    # 2026-09-01 it would today hold two partial weeks, which is not a trend. What
    # the clamp protects against is a retired price sitting beside a live one as if
    # they were the same product — handled here instead by splitting the series on
    # the era of the DAY (below) and marking the changeover on the axis.
    wk_expr = _iso_week_sql("day")
    # Split on the era of the DAY, not of the week: "999" exists on BOTH price lists
    # and means different money on each (old ฿999 is quoted before the 5% service
    # charge and 7% VAT, Grand ฿999 has both inside). Drawn as one series it would
    # look like one product drifting. The week containing 1 Sep carries both.
    week_rows = con.execute(
        f"""SELECT {wk_expr} wk, tier, day < ? AS old_era, SUM(pax), SUM(cogs), SUM(rev)
            FROM menu_fact_total WHERE branch_id IN ({ph})
            GROUP BY wk, tier, old_era ORDER BY wk""",
        [_PT_NEW_MENU_FROM] + bids).fetchall()
    # Days present per week. The newest week is nearly always partial (the sync runs
    # a day behind) and a 2-day week plotted next to 7-day ones reads as a collapse
    # in covers that never happened — so each point carries its own day count and
    # the chart marks the short ones.
    wk_days = dict(con.execute(
        f"""SELECT {wk_expr} wk, COUNT(DISTINCT day) FROM menu_fact_total
            WHERE branch_id IN ({ph}) GROUP BY wk""", bids))
    _ec = date.fromisoformat(_PT_NEW_MENU_FROM).isocalendar()
    era_week = f"{_ec[0]}-W{_ec[1]:02d}"

    _aw = a_to.isocalendar()
    a_week = f"{_aw[0]}-W{_aw[1]:02d}"
    all_weeks = sorted(w for w in {r[0] for r in week_rows} if w <= a_week)
    keep_weeks = set(all_weeks[-_PT_TREND_WEEKS:])
    weeks_available = len(all_weeks)
    # Only label an era when the window actually SHOWS both. A report sitting
    # entirely before the changeover is looking at the menu that was current then,
    # and tagging every one of its tiers "(เมนูเก่า)" would be wrong.
    eras = {bool(r[2]) for r in week_rows if r[0] in keep_weeks}
    straddles = len(eras) > 1
    trend_wk = {}
    for wk, t, old_era, px, cg, rv in week_rows:
        if wk not in keep_weeks:
            continue
        key = str(t or "promo") + (_PT_OLD_ERA if (old_era and straddles) else "")
        m = trend_wk.setdefault(wk, {}).setdefault(key, {"pax": 0.0, "cogs": 0.0, "rev": 0.0})
        m["pax"] += px or 0.0; m["cogs"] += cg or 0.0; m["rev"] += rv or 0.0

    def _points(buckets, key, days=None):
        out = []
        for b in sorted(buckets):
            mt = buckets[b]
            tot_px = sum(v["pax"] for v in mt.values())
            pt = {key: b}
            if days is not None:
                pt["days"] = days.get(b, 0)
                pt["partial"] = days.get(b, 0) < 7
            out.append({**pt, "tiers": {
                t: {"pax": rd(v["pax"], 0),
                    "share": rd(v["pax"] / tot_px * 100, 1) if tot_px else None,
                    "cogs_head": rd(v["cogs"] / v["pax"]) if v["pax"] else None,
                    "rev_head": rd(v["rev"] / v["pax"]) if v["pax"] else None}
                for t, v in mt.items()}})
        return out

    trend_month = _points(trend, "ym")
    trend_week = _points(trend_wk, "wk", wk_days)
    wk_seen = {t for pt in trend_week for t, v in pt["tiers"].items() if v["pax"]}
    _ord = {t: i for i, t in enumerate(_PT_ORDER)}
    wk_tier_keys = sorted(wk_seen, key=lambda k: (k.endswith(_PT_OLD_ERA),
                                                  _ord.get(k.replace(_PT_OLD_ERA, ""), 99)))
    wk_tier_labels = {k: _pt_label(k.replace(_PT_OLD_ERA, ""))
                         + (" (เมนูเก่า)" if k.endswith(_PT_OLD_ERA) else "")
                      for k in wk_tier_keys}
    return {
        "trend_month": trend_month,
        "trend_week": trend_week,
        "trend_week_meta": {
            "weeks_shown": len(trend_week), "weeks_wanted": _PT_TREND_WEEKS,
            "tier_keys": wk_tier_keys, "tier_labels": wk_tier_labels,
            "old_suffix": _PT_OLD_ERA, "straddles_menu_change": straddles,
            "weeks_available": weeks_available,
            "from": trend_week[0]["wk"] if trend_week else None,
            "era_week": era_week if straddles else None,
            "era_from": _PT_NEW_MENU_FROM if straddles else None},
    }


# owner 2026-10-31 ("กดแล้วทำงานต่อได้เลย"): a to-do line carries where its work is done - every price-tier line is
# about a tier, so it opens the price-tier page (js/01-core.js pricetier)
_PT_GO = {"kind": "tab", "tab": "pricetier"}


@router.get("/api/reports/price-tier")
@ttl_cache
def price_tier_report(date_from: Optional[str] = Query(None), date_to: Optional[str] = Query(None),
                      scope: str = Query("all", description="master | franchise | all")):
    """วิเคราะห์ Tier ราคาขายบุฟเฟ่ต์: จำนวนคนทาน, Top-30 เมนูต้นทุน, เทรนด์ และข้อเสนอแนะ
    — ช่วงที่เลือก (A) เทียบช่วงก่อนหน้าความยาวเท่ากัน (B) อัตโนมัติ."""
    from datetime import date, timedelta
    if scope not in _MI_SCOPE_LABEL:
        scope = "all"
    with get_db() as con:
        has_tier = _mi_ready(con) and bool(con.execute(
            "SELECT 1 FROM pragma_table_info('menu_fact_total') WHERE name='tier'").fetchone())
        if not has_tier:
            return {"ready": False,
                    "hint": "ยังไม่มีข้อมูล Tier (fact schema v2) — รัน python backend/build_menu_fact.py ก่อน"}
        lo, hi = con.execute("SELECT MIN(day), MAX(day) FROM menu_fact_total").fetchone()
        if not lo:
            return {"ready": False, "hint": "ตารางข้อมูลว่าง — รัน build_menu_fact.py"}
        a_from = max(date_from or lo, lo)
        a_to = min(date_to or hi, hi)
        if a_from > a_to:
            return {"ready": True, "error": "ช่วงวันที่ไม่ถูกต้อง", "coverage": [lo, hi]}
        # ── Grand Menu era guard ────────────────────────────────────────────
        # The whole buffet price list was replaced on 2026-09-01. A window that
        # STRADDLES that date blends two price lists into one analysis: the tier
        # table lists retired prices next to live ones, the Top-N gets a column
        # per dead tier, and the "สิ่งที่ต้องปรับปรุงและพัฒนา" card reads a menu
        # that stopped existing as a finding about the one on sale. That is the
        # default view (the pickers open on the full coverage), so cut the
        # pre-changeover part off and analyse the live menu alone.
        #
        # A window entirely BEFORE the changeover is left alone — that is an
        # explicit look back at the old menu, consistently old on both sides.
        # A window entirely after needs no cut. Comparison against a previous
        # window then resumes on its own once both sides sit after the
        # changeover (October vs September), via comparison_available below.
        era_clamped = False
        if a_from < _PT_NEW_MENU_FROM <= a_to:
            a_from = _PT_NEW_MENU_FROM
            era_clamped = True
        dA_from, dA_to = date.fromisoformat(a_from), date.fromisoformat(a_to)
        span = (dA_to - dA_from).days + 1
        b_to_d = dA_from - timedelta(days=1)
        b_from_d = b_to_d - timedelta(days=span - 1)
        if b_from_d >= date.fromisoformat(lo) and span >= 2:
            mode = "prev"
        else:  # not enough history before A → compare halves of A itself
            mode = "split"
            mid = dA_from + timedelta(days=span // 2)
            b_from_d, b_to_d = dA_from, mid - timedelta(days=1)
            dA_from = mid
            a_from = dA_from.isoformat()
        b_from, b_to = b_from_d.isoformat(), b_to_d.isoformat()
        # A wholesale menu change makes the previous period a different product
        # lineup, not a baseline — comparing September to August "789 → promo"
        # isn't a mix shift, it's a menu that stopped existing. Only compare when
        # both windows sit on the same side of the switch-over.
        comparison_available = not (a_from >= _PT_NEW_MENU_FROM > b_to)

        bids = _mi_branch_ids(con, scope)
        ph = ",".join("?" * len(bids))
        FA = _pt_sum_tiers(con, ph, bids, a_from, a_to)
        FB = _pt_sum_tiers(con, ph, bids, b_from, b_to) if comparison_available else {}
        pax_a = sum(v["pax"] for v in FA.values())
        pax_b = sum(v["pax"] for v in FB.values())
        if pax_a <= 0:
            return {"ready": True, "error": "ไม่มีข้อมูลบิลบุฟเฟ่ต์ในช่วง/ขอบเขตที่เลือก",
                    "coverage": [lo, hi]}
        # A tier with nobody on it THIS period is not a finding, it's a retired
        # price — matches _mgt_tier's rule so the two reports agree.
        tier_keys = [t for t in _PT_ORDER if FA.get(t, {}).get("pax", 0) > 0]

        # per costed menu SKU x tier for both windows (groups g:* excluded — they
        # would double-count their member SKUs in the Top-30 ranking)
        def ent_rows(d1, d2):
            out = {}
            for ent, t, q, ec in con.execute(
                    f"""SELECT ent, tier, SUM(qty), SUM(ent_cogs) FROM menu_fact_day
                        WHERE day BETWEEN ? AND ? AND branch_id IN ({ph}) AND ent LIKE 's:%'
                        GROUP BY ent, tier""",
                    [d1, d2] + bids):
                m = out.setdefault(ent, {}).setdefault(str(t or "promo"), {"qty": 0.0, "ecogs": 0.0})
                m["qty"] += q or 0.0
                m["ecogs"] += ec or 0.0
            return out
        EA, EB = ent_rows(a_from, a_to), ent_rows(b_from, b_to)
        meta = {ent: (label, uc) for ent, label, uc in con.execute(
            "SELECT ent, label, unit_cost FROM menu_fact_meta WHERE kind='sku'")}

        # Monthly + weekly tier trend (shared with the management report §11).
        # The monthly series is clamped to the changeover when the analysis sits
        # on the live menu; the weekly one ends at the week the analysis ends in.
        _tr = _pt_tier_trends(con, ph, bids,
                              month_from=(_PT_NEW_MENU_FROM if a_from >= _PT_NEW_MENU_FROM else lo),
                              a_to=dA_to)
        # sku_tier describes the 2026-09-01+ Grand Menu ONLY, and "999" is a tier on
        # both price lists — hanging Grand Menu dishes off a July ฿999 row would
        # describe a menu that branch was not serving that month. A window that
        # predates the changeover therefore gets no BoM figures at all; the tab and
        # §11 already render that as "มีเฉพาะเมนูใหม่ตั้งแต่ 1 ก.ย. 2569".
        if a_from >= _PT_NEW_MENU_FROM:
            bom_dishes_by_tier = _pt_bom_dishes_by_tier(con)
            bom_by_tier = _pt_bom_cost_by_tier(con, bom_dishes_by_tier)
        else:
            bom_dishes_by_tier, bom_by_tier = {}, {}

        # ── cost coverage per tier: why COGS% here is a FLOOR, not a fact ──
        # A dish with no unit cost books ฿0, so every uncosted plate silently
        # flatters the tier it was eaten on — on 2026-09 that is drinks, dipping
        # sauces and a handful of real premium dishes (มันปู, โอโทโร่สับอูนิ).
        # Operations reads its own food cost off requisitions and sees a much
        # higher number; this is the half of that gap the app can measure.
        # Weighted by PLATES, never revenue: buffet dishes carry ~฿0 line revenue
        # by design, so a revenue-weighted figure always says "fine".
        # The buffet package line and the service charge are cost-free ON PURPOSE
        # (the package would double-count every dish) and are not a gap.
        skip_ents = {f"s:{r[0]}" for r in con.execute(
            f"SELECT sku_id FROM skus WHERE {tier_code_sql('sku_code')} OR sku_code='SERVICECHARGE'")}
        cover = {}
        for ent, per in EA.items():
            if ent in skip_ents:
                continue
            uc = (meta.get(ent) or (None, 0))[1] or 0
            for t, m in per.items():
                c = cover.setdefault(t, {"plates": 0.0, "costed": 0.0, "cogs": 0.0})
                c["plates"] += m["qty"]
                c["cogs"] += m["ecogs"]
                if uc > 0:
                    c["costed"] += m["qty"]

    rd = lambda x, nd=2: round(x, nd) if x is not None else None
    pct = lambda c, p: round((c - p) / p * 100, 1) if p else None

    tiers = []
    for t in tier_keys:
        va = FA.get(t, {"bills": 0, "pax": 0.0, "cogs": 0.0, "rev": 0.0, "hc": 0.0})
        vb = FB.get(t, {"bills": 0, "pax": 0.0, "cogs": 0.0, "rev": 0.0, "hc": 0.0})
        A = _pt_metrics(va)
        A["share"] = rd(va["pax"] / pax_a * 100, 1) if pax_a else None
        B = None
        if comparison_available:
            B = _pt_metrics(vb)
            B["share"] = rd(vb["pax"] / pax_b * 100, 1) if pax_b else None
        bt = bom_by_tier.get(t)
        # …and price this tier's own uncosted plates at its own costed average,
        # so the reader sees roughly where food% would land once the gap is
        # filled. An estimate, labelled as one — never booked anywhere.
        cv = cover.get(t)
        cov_pct = est_food_pct = uncosted = None
        if cv and cv["plates"]:
            cov_pct = rd(cv["costed"] / cv["plates"] * 100, 1)
            uncosted = rd(cv["plates"] - cv["costed"], 0)
            if cv["costed"] and va["rev"]:
                est_food_pct = rd((va["cogs"] + uncosted * (cv["cogs"] / cv["costed"]))
                                  / va["rev"] * 100, 1)
        tiers.append({
            "tier": t, "label": _pt_label(t), "a": A, "b": B,
            "bom_avg_cost": bt["bom_avg_cost"] if bt else None,
            "bom_dish_count": bt["bom_dish_count"] if bt else None,
            "bom_dishes": bom_dishes_by_tier.get(t, []),
            "cost_coverage_pct": cov_pct, "uncosted_plates": uncosted,
            "est_food_pct": est_food_pct,
            "d_pax_pct": pct(va["pax"], vb["pax"]) if comparison_available else None,
            "d_share_pp": (rd((A["share"] or 0) - (B["share"] or 0), 1)
                           if comparison_available and A["share"] is not None and B["share"] is not None else None),
            "d_cogs_head": (rd(A["cogs_head"] - B["cogs_head"])
                            if comparison_available and A["cogs_head"] is not None and B["cogs_head"] is not None else None),
            "d_hc_head": (rd(A["hc_head"] - B["hc_head"])
                          if comparison_available and A["hc_head"] is not None and B["hc_head"] is not None else None),
        })

    # ── Top-30 menus by COGS in A: per-tier consumption/head + Δ vs B ──
    rank = sorted(EA.items(), key=lambda kv: -sum(m["ecogs"] for m in kv[1].values()))[:_PT_TOP_N]
    paxA = {t: FA.get(t, {}).get("pax", 0.0) for t in tier_keys}
    paxB = {t: FB.get(t, {}).get("pax", 0.0) for t in tier_keys}
    top30 = []
    for ent, per in rank:
        label, uc = meta.get(ent, (ent, 0.0))
        qa = sum(m["qty"] for m in per.values()); ca = sum(m["ecogs"] for m in per.values())
        perB = EB.get(ent, {})
        qb = sum(m["qty"] for m in perB.values())
        row = {"ent": ent, "label": label, "unit_cost": rd(uc or 0),
               "qty": rd(qa, 0), "ecogs": rd(ca, 0),
               "qty_head": rd(qa / pax_a, 3) if pax_a else None,
               "cogs_head": rd(ca / pax_a) if pax_a else None,
               "d_qty_head_pct": pct(qa / pax_a if pax_a else 0, qb / pax_b if pax_b else 0),
               "tiers": {}}
        for t in tier_keys:
            qha = per.get(t, {}).get("qty", 0.0) / paxA[t] if paxA[t] else None
            qhb = perB.get(t, {}).get("qty", 0.0) / paxB[t] if paxB[t] else None
            row["tiers"][t] = {"qty_head": rd(qha, 3) if qha is not None else None,
                               "d_pct": pct(qha or 0, qhb) if qhb else None}
        top30.append(row)

    trend_month = _tr["trend_month"]
    trend_week = _tr["trend_week"]
    trend_week_meta = _tr["trend_week_meta"]

    # ── blended per-head + mix effect ──
    cogs_a = sum(v["cogs"] for v in FA.values()); cogs_b = sum(v["cogs"] for v in FB.values())
    rev_a = sum(v["rev"] for v in FA.values()); rev_b = sum(v["rev"] for v in FB.values())
    blend = {"pax_a": rd(pax_a, 0),
             "pax_b": rd(pax_b, 0) if comparison_available else None,
             "d_pax_pct": pct(pax_a, pax_b) if comparison_available else None,
             "rev_head_a": rd(rev_a / pax_a) if pax_a else None,
             "rev_head_b": rd(rev_b / pax_b) if comparison_available and pax_b else None,
             "cogs_head_a": rd(cogs_a / pax_a) if pax_a else None,
             "cogs_head_b": rd(cogs_b / pax_b) if comparison_available and pax_b else None,
             "food_pct_a": rd(cogs_a / rev_a * 100, 1) if rev_a else None,
             "food_pct_b": rd(cogs_b / rev_b * 100, 1) if comparison_available and rev_b else None}
    # mix effect on rev/head: A's tier mix priced at B's per-tier rev/head vs B blended
    mix_effect = None
    if comparison_available and pax_a and pax_b and rev_b:
        at_b_prices = sum((FA[t]["pax"] / pax_a) * (FB[t]["rev"] / FB[t]["pax"])
                          for t in FA if FB.get(t, {}).get("pax", 0) > 0)
        mix_effect = rd(at_b_prices - rev_b / pax_b)

    # ── insights + actions (Thai) ──
    period_word = "ช่วงก่อนหน้า" if mode == "prev" else "ครึ่งแรกของช่วงเดียวกัน"
    fb = lambda x: f"฿{x:,.2f}" if x is not None else "-"
    if not comparison_available:
        insights = [f"ยังไม่มีช่วงก่อนหน้าที่ใช้เมนูเดียวกันสำหรับเปรียบเทียบ (เมนูใหม่เริ่ม {_PT_NEW_MENU_FROM}) "
                    f"— ตัวเลขด้านล่างเป็นข้อมูลช่วง {a_from} ถึง {a_to} เดี่ยวๆ การเปรียบเทียบจะกลับมาอัตโนมัติ "
                    f"เมื่อทั้งสองช่วงอยู่หลังวันที่เมนูเปลี่ยนแล้ว (เช่น ต.ค. เทียบ ก.ย.)"]
        # No "vs previous period" is possible yet, but A's own numbers don't need
        # one — surface the highest food-cost tier as a real, actionable finding
        # rather than leaving this card empty until next month.
        actions = []
        real_now = [x for x in tiers if str(x["tier"]).isdigit() and x["a"]["food_pct"] is not None]
        worst_now = max(real_now, key=lambda x: x["a"]["food_pct"], default=None)
        if worst_now and worst_now["a"]["food_pct"] >= 40:
            actions.append({"go": _PT_GO, "title": f"ทบทวนความคุ้มของ tier {worst_now['label']}",
                            "detail": f"COGS {worst_now['a']['food_pct']}% ของรายได้ "
                                      f"(GP {fb(worst_now['a']['gp_head'])}/หัว) ในช่วง {a_from} ถึง {a_to} — "
                                      f"ยังไม่มีเดือนก่อนเทียบเพื่อดูเทรนด์ แต่ใช้ตัวเลขนี้ตั้งเป็นเป้าคุมต้นทุนของเมนูใหม่ได้เลย"})
        if not actions:
            actions.append({"go": _PT_GO, "title": "เก็บตัวเลขเดือนนี้ไว้เป็นฐาน (baseline)",
                            "detail": f"เมนูใหม่เริ่ม {_PT_NEW_MENU_FROM} — ต้นทุน/หัวและ BoM เฉลี่ย/จานต่อ tier "
                                      f"ด้านล่างคือฐานของเมนูใหม่ พอถึงเดือนถัดไปที่มีข้อมูลเทียบได้ จะวิเคราะห์แนวโน้มให้อัตโนมัติ"})
        return {"ready": True, "scope": scope, "scope_label": _MI_SCOPE_LABEL[scope],
                "coverage": [lo, hi], "mode": mode, "comparison_available": False,
                "era_clamped": era_clamped, "menu_era_from": _PT_NEW_MENU_FROM,
                "a_range": [a_from, a_to], "b_range": [b_from, b_to],
                "blend": blend, "mix_effect": mix_effect,
                "tiers": tiers, "tier_keys": tier_keys,
                "tier_labels": {t: _pt_label(t) for t in tier_keys},
                "top30": top30, "trend_month": trend_month, "trend_week": trend_week,
                "trend_week_meta": trend_week_meta,
                "insights": insights, "actions": actions}
    insights, actions = [], []
    if blend["d_pax_pct"] is not None:
        up = blend["d_pax_pct"] >= 0
        insights.append(f"จำนวนคนทานรวม {blend['pax_a']:,.0f} คน "
                        f"{'เพิ่มขึ้น' if up else 'ลดลง'} {abs(blend['d_pax_pct'])}% เทียบ{period_word} "
                        f"({blend['pax_b']:,.0f} คน)")
    # tier-level pax insights: canonical price tiers only (promo/child mix with
    # price-list changes and would mislead the "กู้ยอด" framing)
    real_tiers = [x for x in tiers if str(x["tier"]).isdigit() and x["d_pax_pct"] is not None]
    if real_tiers:
        gain = max(real_tiers, key=lambda x: x["d_pax_pct"])
        lose = min(real_tiers, key=lambda x: x["d_pax_pct"])
        if gain["d_pax_pct"] > 0:
            insights.append(f"Tier {gain['label']} คนทานเพิ่มแรงสุด +{gain['d_pax_pct']}% "
                            f"(สัดส่วน {gain['b']['share']}% → {gain['a']['share']}%)")
        if lose["d_pax_pct"] < 0:
            insights.append(f"Tier {lose['label']} คนทานลดแรงสุด {lose['d_pax_pct']}% "
                            f"(สัดส่วน {lose['b']['share']}% → {lose['a']['share']}%)")
            if abs(lose["d_pax_pct"]) >= 5:
                actions.append({"go": _PT_GO, "title": f"กู้ยอดลูกค้า Tier {lose['label']}",
                                "detail": f"คนทานลดลง {abs(lose['d_pax_pct'])}% เทียบ{period_word} "
                                          f"(เหลือ {lose['a']['pax']:,} คน) — ตรวจสาขาหลักที่ขาย tier นี้, "
                                          f"จัดโปรวันธรรมดา/ช่วง off-peak เฉพาะ tier, และเช็คว่าราคา/คู่แข่งในพื้นที่เปลี่ยนหรือไม่"})
    promo = next((x for x in tiers if x["tier"] == "promo"), None)
    if promo and promo["d_share_pp"] is not None and abs(promo["d_share_pp"]) >= 2:
        insights.append(f"สัดส่วนบิลราคาโปรโมชัน/นอกราคามาตรฐาน {promo['b']['share']}% → {promo['a']['share']}% "
                        f"({'+' if promo['d_share_pp'] > 0 else ''}{promo['d_share_pp']} pp)")
    if mix_effect is not None and abs(mix_effect) >= 1:
        if mix_effect < 0:
            insights.append(f"ลูกค้าย้ายไป tier ถูกลง — ผลจาก mix อย่างเดียวกดรายได้/หัวลง {fb(-mix_effect)}")
            actions.append({"go": _PT_GO, "title": "ดัน upsell ไป tier ที่สูงขึ้น",
                            "detail": f"สัดส่วนลูกค้าเลื่อนไป tier ราคาต่ำ ทำให้รายได้/หัวหายไป {fb(-mix_effect)} "
                                      f"ทั้งที่ราคาแต่ละ tier ไม่เปลี่ยน — เทรนพนักงานเชียร์อัปเกรด (เมนูเด่นเฉพาะ tier บน, "
                                      f"ป้ายเทียบสิทธิ์ที่โต๊ะ), ตั้งเป้า % อัปเกรดรายสาขา"})
        else:
            insights.append(f"ลูกค้าย้ายขึ้น tier แพงขึ้น — ผลจาก mix อย่างเดียวเพิ่มรายได้/หัว {fb(mix_effect)}")
    for x in real_tiers:
        if x["d_cogs_head"] is not None and x["d_cogs_head"] >= 3:
            drivers = sorted((r for r in top30 if r["tiers"].get(x["tier"], {}).get("d_pct")),
                             key=lambda r: -(r["tiers"][x["tier"]]["d_pct"] or 0))[:3]
            dtxt = ", ".join(f"{r['label']} (+{r['tiers'][x['tier']]['d_pct']}%)" for r in drivers
                             if (r["tiers"][x["tier"]]["d_pct"] or 0) > 0)
            insights.append(f"ต้นทุน/หัว tier {x['label']} เพิ่ม {fb(x['d_cogs_head'])} "
                            f"({fb(x['b']['cogs_head'])} → {fb(x['a']['cogs_head'])})"
                            + (f" — ตัวดัน: {dtxt}" if dtxt else ""))
            actions.append({"go": _PT_GO, "title": f"คุมต้นทุน/หัว tier {x['label']}",
                            "detail": f"ต้นทุนอาหาร/หัวเพิ่ม {fb(x['d_cogs_head'])} เทียบ{period_word}"
                                      + (f" ตัวดันหลัก: {dtxt} — คุมพอร์ชัน/ความถี่เติมของเมนูเหล่านี้, "
                                         f"เช็คของเสียครัว และทบทวนต้นทุนวัตถุดิบกับซัพพลายเออร์" if dtxt else
                                         " — ไล่ดูเมนู Top-30 ของ tier นี้ในตารางด้านล่าง")})
    worst_fp = max((x for x in real_tiers if x["a"]["food_pct"]), key=lambda x: x["a"]["food_pct"], default=None)
    if worst_fp and worst_fp["a"]["food_pct"] >= 40:
        insights.append(f"Tier {worst_fp['label']} COGS สูงสุด {worst_fp['a']['food_pct']}% ของรายได้ "
                        f"(GP/หัว {fb(worst_fp['a']['gp_head'])})")
        actions.append({"go": _PT_GO, "title": f"ทบทวนความคุ้มของ tier {worst_fp['label']}",
                        "detail": f"COGS {worst_fp['a']['food_pct']}% ของรายได้ (GP เหลือ {fb(worst_fp['a']['gp_head'])}/หัว) — "
                                  f"พิจารณาปรับไลน์เมนูเฉพาะ tier (สลับเมนูต้นทุนสูงเป็นเมนูภาพลักษณ์ดีแต่ต้นทุนต่ำกว่า), "
                                  f"หรือทดลองขึ้นราคาแบบ A/B บางสาขา"})
    hc_up = [x for x in real_tiers if x["d_hc_head"] is not None and x["d_hc_head"] >= 2]
    if hc_up:
        t0 = max(hc_up, key=lambda x: x["d_hc_head"])
        insights.append(f"การทานเมนูแพง (ต้นทุน ≥฿30) tier {t0['label']} เพิ่ม {fb(t0['d_hc_head'])}/หัว")
        actions.append({"go": _PT_GO, "title": f"เบรกเมนูต้นทุนสูงใน tier {t0['label']}",
                        "detail": f"เมนูแพง/หัวเพิ่ม {fb(t0['d_hc_head'])} — ใช้เทคนิค menu placement "
                                  f"(วางเมนูข้าว/ของดองไว้ต้น ๆ), ลดขนาดพอร์ชันต่อจาน (เติมได้แต่ชะลอ), "
                                  f"และตรวจว่าสาขาไหนปล่อยเมนูแพงเกินเพื่อน"})
    rising = [r for r in top30 if (r["d_qty_head_pct"] or 0) >= 10 and (r["cogs_head"] or 0) >= 1]
    if rising:
        names = ", ".join(f"{r['label']} (+{r['d_qty_head_pct']}%)" for r in rising[:4])
        insights.append(f"เมนูต้นทุนหลักที่ถูกทานเพิ่มชัดเจน: {names}")
        actions.append({"go": _PT_GO, "title": "จัดการเมนูที่การทานพุ่ง",
                        "detail": f"{names} — ถ้าเป็นเมนูดึงลูกค้า (เช็คจากแท็บวิเคราะห์วัตถุดิบว่า GP/หัวของบิลที่สั่งไม่แย่ลง) "
                                  f"ให้คงไว้แต่ล็อกต้นทุนซื้อ; ถ้าไม่ใช่ ให้ลดความถี่เติม/ขนาดเสิร์ฟ"})
    if not actions:
        actions.append({"go": _PT_GO, "title": "โครงสร้าง tier แข็งแรง",
                        "detail": "ไม่พบสัญญาณผิดปกติเกินเกณฑ์ในช่วงที่เลือก — คงกลยุทธ์ปัจจุบัน "
                                  "และติดตามสัดส่วน tier กับต้นทุน/หัวรายเดือนต่อไป"})

    return {"ready": True, "scope": scope, "scope_label": _MI_SCOPE_LABEL[scope],
            "coverage": [lo, hi], "mode": mode, "comparison_available": True,
            "era_clamped": era_clamped, "menu_era_from": _PT_NEW_MENU_FROM,
            "a_range": [a_from, a_to], "b_range": [b_from, b_to],
            "blend": blend, "mix_effect": mix_effect,
            "tiers": tiers, "tier_keys": tier_keys,
            "tier_labels": {t: _pt_label(t) for t in tier_keys},
            "top30": top30, "trend_month": trend_month, "trend_week": trend_week,
            "trend_week_meta": trend_week_meta,
            "insights": insights, "actions": actions}


@router.get("/api/reports/cost-head-top60")
@ttl_cache
def report_cost_head_top60(month: Optional[str] = Query(None),
                           scope: str = Query("master", description="master | franchise | all")):
    """Top-60 menu cost-per-head for one branch scope, one month: combined overview
    + each branch's FULL Top-60 with Δ/head vs the overview, plus a cross-branch
    ranking and headline. scope = master (default) | franchise | all.
    Companion exports: /api/reports/cost-head-top60.print (HTML) | .xlsx."""
    TOPN = 60
    if scope not in _CH_SCOPES:
        scope = "master"
    with get_db() as con:
        months = [r[0] for r in con.execute(
            "SELECT DISTINCT period_month FROM sales_sku_monthly ORDER BY period_month DESC").fetchall()]
        month = _ch_resolve_month(con, month)
        overview, branches = _cost_head_scopes(con, month, scope)
        if not overview:
            return {"month": month, "months": months, "scope": scope,
                    "scope_label": _CH_SCOPE_LABEL.get(scope, scope),
                    "overview": None, "branches": [], "analysis": None}

        def top_rows(sc, variant):
            ov = overview["by_sku"]; cum = 0.0; out = []
            for i, m in enumerate(sc["menus"][:TOPN], 1):
                cum += m["share"]
                row = {"rank": i, "code": m["code"], "desc": m["desc"],
                       "prem": _ch_is_premium(m["desc"]), "qty": m["qty"],
                       "qph": round(m["qph"], 2), "cogs": round(m["cogs"], 2),
                       "cph": round(m["cph"], 2), "share": round(m["share"] * 100, 1),
                       "cum": round(cum * 100, 1)}
                if variant:
                    o = ov.get(m["sku_id"]); o_cph = o["cph"] if o else None
                    d = m["cph"] - (o_cph or 0.0)
                    row["o_cph"] = round(o_cph, 2) if o_cph is not None else None
                    row["d"] = round(d, 2)
                    row["dp"] = round(d / o_cph * 100, 0) if (o_cph and o_cph > 0) else None
                out.append(row)
            return out

        def scope_head(sc, variant):
            h = {"name": sc["name"], "covers": sc["covers"],
                 "cph_total": round(sc["cph_total"], 2), "food_pct": round(sc["food_pct"], 1),
                 "total_cogs": round(sc["total_cogs"], 2), "total_rev": round(sc["total_rev"], 2),
                 "menu_count": len(sc["menus"]), "menus": top_rows(sc, variant)}
            if variant:
                h["branch_id"] = sc["branch_ids"][0]
                h["gap"] = round(sc["cph_total"] - overview["cph_total"], 2)
                highs, lows = _cost_head_deviation(sc, overview)
                pack = lambda x: {"code": x["code"], "desc": x["desc"], "prem": x["prem"],
                                  "b_cph": round(x["b_cph"], 2), "o_cph": round(x["o_cph"], 2),
                                  "d": round(x["d"], 2)}
                h["highs"] = [pack(x) for x in highs[:6]]
                h["lows"] = [pack(x) for x in lows[:6]]
                h["drivers"] = " · ".join(f'{_short_label(x["desc"])} {x["d"]:+.1f}' for x in highs[:3])
            return h

        ov_out = scope_head(overview, False)
        br_out = [scope_head(b, True) for b in branches]
        ranked = sorted(br_out, key=lambda b: b["cph_total"], reverse=True)
        analysis = {
            "overview_cph": round(overview["cph_total"], 2),
            "ranked": [{"name": b["name"], "covers": b["covers"], "cph_total": b["cph_total"],
                        "gap": b["gap"], "food_pct": b["food_pct"], "total_cogs": b["total_cogs"],
                        "drivers": b["drivers"]} for b in ranked],
        }
        if ranked:
            hot = ranked[0]
            analysis.update({
                "hottest": hot["name"], "hottest_gap": hot["gap"],
                "hottest_covers": hot["covers"],
                "hottest_excess_month": round(hot["gap"] * hot["covers"], 0),
                "hottest_drivers": [{"desc": x["desc"], "d": x["d"]} for x in hot["highs"][:3]],
            })
    return {"month": month, "months": months, "scope": scope,
            "scope_label": _CH_SCOPE_LABEL.get(scope, scope),
            "overview": ov_out, "branches": br_out, "analysis": analysis}


@router.get("/api/reports/cost-head-top60.xlsx")
def report_cost_head_top60_xlsx(month: Optional[str] = Query(None),
                                scope: str = Query("master")):
    """Excel export of the Top-60 cost-per-head report (reuses the generator's write_xlsx,
    fed the live-computed scopes — ties out with the tab)."""
    import io as _io_ch
    from fastapi.responses import StreamingResponse as _SR
    if scope not in _CH_SCOPES:
        scope = "master"
    with get_db() as con:
        month = _ch_resolve_month(con, month)
        overview, branches = _cost_head_scopes(con, month, scope)
    if not overview:
        raise HTTPException(status_code=404, detail="ไม่มีข้อมูลสำหรับเดือนนี้")
    try:
        gen = _load_top60_gen()
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"export module unavailable: {e}")
    buf = _io_ch.BytesIO()
    gen.write_xlsx(buf, month, overview, branches, _ch_gen_dt(),
                   scope_label=_CH_SCOPE_LABEL.get(scope, scope))   # openpyxl save() accepts a file-like
    buf.seek(0)
    return _SR(buf, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
               headers={"Content-Disposition": attachment(f"TOP60_COST_PER_HEAD_{scope}_{month}.xlsx")})


@router.get("/api/reports/cost-head-top60.print")
def report_cost_head_top60_print(month: Optional[str] = Query(None),
                                 scope: str = Query("master")):
    """Full printable HTML report (the standalone generator's output, computed live)."""
    if scope not in _CH_SCOPES:
        scope = "master"
    with get_db() as con:
        month = _ch_resolve_month(con, month)
        overview, branches = _cost_head_scopes(con, month, scope)
    if not overview:
        raise HTTPException(status_code=404, detail="ไม่มีข้อมูลสำหรับเดือนนี้")
    try:
        gen = _load_top60_gen()
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"export module unavailable: {e}")
    html_out = gen.build_html(month, overview, branches, _ch_gen_dt(), "live",
                              scope_label=_CH_SCOPE_LABEL.get(scope, scope))
    return Response(content=html_out, media_type="text/html; charset=utf-8")




# Gross-margin distribution bands: (idx, label, lo, hi). lo/hi are GM% bounds
# (lo inclusive, hi exclusive). idx -1 = loss bucket. Shared by the dist build
# and the drill-down endpoint so labels/ranges never drift.
def _build_gm_buckets():
    out = [(-1, "ขาดทุน (<0%)", None, 0.0)]
    for i in range(10):                       # 0–5 … 45–50 (5% bands)
        lo = i * 5; out.append((i, f"{lo}–{lo+5}%", float(lo), float(lo + 5)))
    for j in range(5):                        # 50–60 … 90–100 (10% bands)
        lo = 50 + j * 10; out.append((10 + j, f"{lo}–{lo+10}%", float(lo), float(lo + 10)))
    return out
_PNL_GM_BUCKETS = _build_gm_buckets()


def _pnl_win(con, gran, date, tw, tp, maxd):
    """PR-PP3 · the per-bill window on the one period pattern: the period of `date` (default: of the last closed day),
    to date through the scope's last closed day when it is not finished (periods.windows). The per-bill COGS table is a
    rolling window, so the anchor is never after its last day."""
    import periods as _P
    a = _P.anchor(con, tw, tp)
    a = min(a, maxd) if a and maxd else (a or maxd)
    return _P.windows(con, gran, tw, tp, pick=(date or a), anchor_day=a)


@router.get("/api/reports/pnl-bill-drill")
def pnl_bill_drill(idx: int = Query(...), date: Optional[str] = Query(None),
                   branch: Optional[str] = Query(None), group: Optional[str] = Query(None),
                   gran: str = Query("day"), limit: int = Query(500)):
    """Drill-down: bills whose gross margin falls in distribution band `idx`
    (see _PNL_GM_BUCKETS). COGS via BoM only."""
    import datetime as _dt
    if gran not in ("day", "week", "month"):
        gran = "day"
    band = next((b for b in _PNL_GM_BUCKETS if b[0] == idx), None)
    if band is None:
        return {"idx": idx, "bills": [], "stat": {}}
    _, label, lo, hi = band
    tfilt = ["t.source='Easy'", "b.name!='ALL'"]; tp: dict = {}
    scope_filter(tfilt, tp, branch, group)
    tw = " AND ".join(tfilt)
    with get_db() as con:
        maxd = con.execute("SELECT MAX(day) FROM sale_bill_cogs").fetchone()[0]
        if not maxd:
            return {"idx": idx, "label": label, "bills": [], "stat": {}}
        w = _pnl_win(con, gran, date, tw, tp, maxd)
        if not w:
            return {"idx": idx, "label": label, "bills": [], "stat": {}}
        s, e = (_dt.date.fromisoformat(x) for x in w["cur"])
        JN = ("FROM sales_transaction t JOIN sale_bill_cogs c ON c.receipt_no=t.receipt_no AND c.branch_id=t.branch_id "
              "JOIN branches b ON b.branch_id=t.branch_id "
              f"WHERE {tw} AND date(t.sold_at) BETWEEN :s AND :e AND t.total_inc_vat>0")
        params = {**tp, "s": s.isoformat(), "e": e.isoformat()}
        gm = f"({net_revenue_row('t')} - c.cogs_bom)/({net_revenue_row('t')})*100"
        cond = f"{gm} < 0" if idx == -1 else f"{gm} >= {lo} AND {gm} < {hi}"
        bills = [{"receipt": x["receipt_no"], "branch": x["branch"], "rev": x["rev"], "cogs": x["cogs"],
                  "disc": x["disc"], "cust": x["cust"], "gp_pct": x["gpp"]}
                 for x in con.execute(f"""SELECT t.receipt_no, b.name branch, ROUND({net_revenue_row('t')}) rev,
                   ROUND(c.cogs_bom) cogs, ROUND(t.discount) disc, t.customer_count cust,
                   ROUND({gm},1) gpp {JN} AND {cond} ORDER BY gpp ASC LIMIT :lim""",
                   {**params, "lim": limit}).fetchall()]
        st = con.execute(f"""SELECT COUNT(*) n, ROUND(SUM({net_revenue_row('t')})) rev,
                   ROUND(SUM(c.cogs_bom)) cogs, SUM(t.customer_count) cust, ROUND(SUM(t.discount)) disc,
                   ROUND(AVG({gm}),1) avg_gm {JN} AND {cond}""", params).fetchone()
        rev = (st["rev"] or 0) if st else 0; cogs = (st["cogs"] or 0) if st else 0
        stat = {"n": (st["n"] or 0) if st else 0, "rev": rev, "cogs": cogs, "gp": round(rev - cogs),
                "cust": (st["cust"] or 0) if st else 0, "disc": (st["disc"] or 0) if st else 0,
                "avg_gm": (st["avg_gm"]) if st else None}
    return {"idx": idx, "label": label, "lo": lo, "hi": hi, "range": [s.isoformat(), e.isoformat()],
            "bills": bills, "capped": len(bills) >= limit, "stat": stat}


@router.get("/api/reports/pnl-bill")
def pnl_bill(date: Optional[str] = Query(None), branch: Optional[str] = Query(None),
             group: Optional[str] = Query(None), gran: str = Query("day")):
    """Per-bill P&L for a day/week/month: revenue − BoM COGS = gross profit, gp/bill,
    gp/head, gp% distribution, and worst (lowest-margin / heavy-discount) bills.
    COGS via our BoM only (sale_bill_cogs). Limited to the costed rolling window.
    PR-PP3: the period is to date through the last closed day when it is not finished (periods.windows), and the
    summary compares with the same days of the period before / the year before where the rolling window holds them
    (summary_prev / summary_ly; otherwise None with the reason in `reasons`)."""
    import datetime as _dt
    import periods as _P
    if gran not in ("day", "week", "month"):
        gran = "day"
    tfilt = ["t.source='Easy'", "b.name!='ALL'"]; tp: dict = {}
    scope_filter(tfilt, tp, branch, group)
    tw = " AND ".join(tfilt)

    with get_db() as con:
        try:
            _w = con.execute("SELECT MIN(day) lo, MAX(day) hi FROM sale_bill_cogs").fetchone()
            maxd = _w["hi"] if _w else None
            mind = _w["lo"] if _w else None
        except Exception:
            return {"focus": None, "note": "no per-bill cost data", "summary": {}, "dist": [], "worst": []}
        if not maxd:
            return {"focus": None, "summary": {}, "dist": [], "worst": []}
        w = _pnl_win(con, gran, date, tw, tp, maxd)
        if not w:
            return {"focus": None, "summary": {}, "dist": [], "worst": []}
        s, e = (_dt.date.fromisoformat(x) for x in w["cur"])
        focus = w["cur"][1]
        JN = (f"FROM sales_transaction t JOIN sale_bill_cogs c ON c.receipt_no=t.receipt_no AND c.branch_id=t.branch_id "
              f"JOIN branches b ON b.branch_id=t.branch_id "
              f"WHERE {tw} AND date(t.sold_at) BETWEEN :s AND :e AND t.total_inc_vat>0")
        params = {**tp, "s": s.isoformat(), "e": e.isoformat()}

        def summ(a, b):
            r = con.execute(f"SELECT COUNT(*) bills, ROUND(SUM({net_revenue_row('t')}),2) rev, "
                            f"ROUND(SUM(c.cogs_bom),2) cogs, SUM(t.customer_count) cust, "
                            f"ROUND(SUM(t.discount),2) disc {JN}", {**tp, "s": a, "e": b}).fetchone()
            bills = r["bills"] or 0; rev = r["rev"] or 0; cogs = r["cogs"] or 0; cust = r["cust"] or 0
            gp = round(rev - cogs, 2); gpp = round(gp / rev * 100, 1) if rev else None
            return {"bills": bills, "revenue": rev, "cogs": cogs, "gross_profit": gp, "gp_pct": gpp,
                    "discount": r["disc"] or 0, "customers": cust,
                    "gp_per_bill": round(gp / bills) if bills else None,
                    "gp_per_head": round(gp / cust) if cust else None}
        summary = summ(*w["cur"])
        reasons = _P.reasons(w)
        bases = {}
        for k in ("prev", "ly"):
            if k in reasons:
                bases[k] = None
            elif mind and w[k][0] < mind:            # the per-bill COGS table is a rolling window
                bases[k] = None
                reasons[k] = f"ต้นทุนรายบิลมีตั้งแต่ {_P.label_th(mind, mind)}"
            else:
                bases[k] = summ(*w[k])
        # Fine-grained gross-margin distribution: 5% bands across 0–50%, 10% above.
        # bkt = -1 loss · 0..9 = 0–5..45–50 · 10..14 = 50–60..90–100.
        gm_expr = f"({net_revenue_row('t')} - c.cogs_bom)/({net_revenue_row('t')})*100"
        rows = con.execute(f"""SELECT bkt, COUNT(*) n, ROUND(SUM(rev)) rev, ROUND(SUM(cogs)) cogs,
                   ROUND(SUM(rev-cogs)) gp, SUM(cust) cust FROM (
                     SELECT rev, cogs, cust,
                       CASE WHEN gm<0 THEN -1 WHEN gm<50 THEN CAST(gm/5 AS INT)
                            ELSE 10+MIN(4,CAST((gm-50)/10 AS INT)) END bkt
                     FROM (SELECT {net_revenue_row('t')} rev, c.cogs_bom cogs, t.customer_count cust,
                           {gm_expr} gm {JN}) x) y GROUP BY bkt""", params).fetchall()
        bmap = {r["bkt"]: r for r in rows}
        dist = []
        for idx, label, lo, hi in _PNL_GM_BUCKETS:
            r = bmap.get(idx)
            dist.append({"idx": idx, "label": label, "lo": lo, "hi": hi,
                         "n": (r["n"] if r else 0), "rev": (r["rev"] or 0) if r else 0,
                         "cogs": (r["cogs"] or 0) if r else 0, "gp": (r["gp"] or 0) if r else 0,
                         "cust": (r["cust"] or 0) if r else 0})
        def rows_for(extra_where, limit):
            return [{"receipt": x["receipt_no"], "branch": x["branch"], "rev": x["rev"], "cogs": x["cogs"],
                     "disc": x["disc"], "cust": x["cust"], "gp_pct": x["gpp"], "dwell_min": x["dwell_min"]}
                    for x in con.execute(f"""SELECT t.receipt_no, b.name branch, ROUND({net_revenue_row('t')}) rev,
                      ROUND(c.cogs_bom) cogs, ROUND(t.discount) disc, t.customer_count cust, t.dwell_min,
                      ROUND(({net_revenue_row('t')} - c.cogs_bom)/({net_revenue_row('t')})*100,1) gpp
                      {JN}{extra_where} ORDER BY gpp ASC {limit}""", params).fetchall()]
        worst = rows_for("", "LIMIT 12")
        # Per-branch margin aggregate (replaces the noisy 12-worst-bills list):
        # avg GM%, GP/bill, bill count and loss-bill count — sorted worst margin first.
        by_branch = []
        for x in con.execute(f"""
            SELECT b.name branch, COUNT(*) bills,
                   ROUND(SUM({net_revenue_row('t')})) rev,
                   ROUND(SUM(c.cogs_bom)) cogs,
                   ROUND(SUM({net_revenue_row('t')} - c.cogs_bom)) gp,
                   SUM(CASE WHEN c.cogs_bom > {net_revenue_row('t')} THEN 1 ELSE 0 END) loss_bills,
                   SUM(t.customer_count) cust
            {JN} GROUP BY b.name""", params).fetchall():
            bb_rev = x["rev"] or 0; bb_gp = x["gp"] or 0; bb_bills = x["bills"] or 0
            by_branch.append({
                "branch": x["branch"], "bills": bb_bills, "rev": bb_rev, "cogs": x["cogs"] or 0,
                "gp": bb_gp, "gm_pct": round(bb_gp / bb_rev * 100, 1) if bb_rev else None,
                "gp_per_bill": round(bb_gp / bb_bills) if bb_bills else None,
                "loss_bills": x["loss_bills"] or 0, "cust": x["cust"] or 0,
            })
        by_branch.sort(key=lambda r: (r["gm_pct"] if r["gm_pct"] is not None else 999))
        # ALL loss-making bills (COGS > revenue, i.e. negative gross profit). Capped
        # high for safety; loss_count/loss_total report the true totals regardless.
        loss = rows_for(f" AND c.cogs_bom > {net_revenue_row('t')}", "LIMIT 2000")
        ls = con.execute(f"""SELECT COUNT(*) n, ROUND(SUM(c.cogs_bom - {net_revenue_row('t')})) loss
                             {JN} AND c.cogs_bom > {net_revenue_row('t')}""", params).fetchone()
    # Per-bill COGS is a ROLLING window (sale_bill_cogs, ~31 days). Outside it the
    # tab rendered an all-zero table with no explanation — numerically honest but
    # indistinguishable from "the page is broken", which is exactly how it was
    # reported. Ship the window so the UI can say why instead of showing nothing.
    per = _P.public(w)
    if reasons:
        per["reasons"] = reasons
    return {"focus": focus, "gran": gran, "range": [s.isoformat(), e.isoformat()], "period": per,
            "summary": summary, "summary_prev": bases.get("prev"), "summary_ly": bases.get("ly"),
            "dist": dist, "worst": worst, "by_branch": by_branch,
            "loss": loss, "loss_count": (ls["n"] or 0) if ls else 0,
            "loss_total": (ls["loss"] or 0) if ls else 0, "loss_capped": len(loss) >= 2000,
            "data_window": [mind, maxd],
            "out_of_window": bool(mind and e.isoformat() < mind) or bool(maxd and s.isoformat() > maxd)}


@router.get("/api/reports/pnl-bill-items")
def pnl_bill_items(receipt: str = Query(..., description="receipt_no of the bill to drill into"),
                   branch: Optional[str] = Query(None, description="branch name: receipt_no repeats across branches")):
    """Menu lines for a single (loss-making) bill: qty, per-unit cost, ex-VAT revenue,
    COGS and per-line gross profit — so you can see exactly what the customer ordered
    that drove COGS above revenue. Source: sale_bill_item (qty/rev staged by the sync
    for LOSS bills only). COGS is recomputed LIVE per line as qty × the unit cost in force
    for the month the bill was sold (rules.effective_unit_cost_sql, the P&L's own
    lookup: a released BoM Center cost from the Grand Menu cut-over, the old chain
    before it), not the snapshot frozen at sync time. `synced` is False until the next sync builds the table."""
    with get_db() as con:
        synced = con.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='sale_bill_item'").fetchone() is not None
        header = None
        # Header comes from sales_transaction (always present) so the drill works for
        # ANY bill — discounted, loss, or normal — not only the loss bills that have a
        # sale_bill_cogs snapshot. cogs_bom is LEFT-joined (NULL when outside the
        # rolling per-bill-COGS window); when menu lines exist it is overridden below
        # with the live line sum.
        hdr = con.execute(
            f"""SELECT t.receipt_no, t.branch_id, b.name branch, t.sold_at, t.customer_count cust,
                      ROUND({net_revenue_row('t')}) rev, ROUND(c.cogs_bom) cogs,
                      ROUND(COALESCE(t.discount,0) + COALESCE(t.sp_discount,0)) disc
               FROM sales_transaction t
               LEFT JOIN sale_bill_cogs c ON c.receipt_no=t.receipt_no AND c.branch_id=t.branch_id
               JOIN branches b ON b.branch_id=t.branch_id
               WHERE t.receipt_no=:r AND (:br IS NULL OR b.name=:br)
               ORDER BY t.branch_id LIMIT 1""",
            {"r": receipt, "br": branch}).fetchone()
        if hdr:
            header = {"receipt": hdr["receipt_no"], "branch": hdr["branch"], "sold_at": hdr["sold_at"],
                      "cust": hdr["cust"] or 0, "rev": hdr["rev"] or 0, "cogs": hdr["cogs"] or 0,
                      "disc": hdr["disc"] or 0}
        items = []
        if synced:
            _build_sku_effective_cost(con, force=False)   # ensure the lookup exists
            # the bill's lines are priced for the month the bill was SOLD, through the
            # P&L's own effective_unit_cost_sql (released cost from the Grand Menu
            # cut-over on, the old chain before it) - not at today's cost
            _bid = hdr["branch_id"] if hdr else None     # the bill the header shows (receipt_no repeats across branches)
            _bill_month = ("(SELECT substr(t0.sold_at, 1, 7) FROM sales_transaction t0 WHERE t0.receipt_no = :r"
                           " AND (:bid IS NULL OR t0.branch_id = :bid) LIMIT 1)")
            _imatch = bill_item_branch_sql(con, "i", "t1").replace("t1.branch_id", ":bid") if _bid is not None else ""
            _uc = (f"CASE WHEN {tier_code_sql('s.sku_code')} THEN 0 "
                   f"ELSE {effective_unit_cost_sql('i', _bill_month)} END")
            items = [{"menu": (x["description"] or x["sku_code"] or "—").replace("(B) ", "").replace("(A) ", ""),
                      "sku_code": x["sku_code"], "qty": x["qty"] or 0,
                      "unit_cost": round(x["uc"] or 0, 2), "rev": x["rev"] or 0,
                      "cogs": x["cogs"] or 0, "margin": round((x["rev"] or 0) - (x["cogs"] or 0))}
                     for x in con.execute(
                        f"""SELECT s.description, s.sku_code, ROUND(SUM(i.qty),2) qty,
                                  ROUND(SUM(i.rev)) rev,
                                  {_uc} uc,
                                  ROUND(SUM(i.qty) * {_uc}) cogs
                           FROM sale_bill_item i JOIN skus s ON s.sku_id=i.sku_id
                           WHERE i.receipt_no=:r{_imatch}
                           GROUP BY i.sku_id ORDER BY cogs DESC, rev DESC""",
                        {"r": receipt, "bid": _bid}).fetchall()]
            if header and items:
                # header COGS comes from sale_bill_cogs (sync snapshot) — override with
                # the live recomputation so the summary tiles match the line table.
                header["cogs"] = round(sum(x["cogs"] for x in items))
    return {"receipt": receipt, "synced": synced, "header": header, "items": items}


def _bom_unit_cost_sql(s: str = "s") -> str:
    """Per-serving effective unit cost for a SKU (alias `s` = skus). sku_cost
    (cost-management table, non-zero) → recipe → alias → 0; BUFFET tier = 0.
    Same priority as _unified_cogs_expr (cost-management first, 2026-06-11)."""
    return f"CASE WHEN {tier_code_sql(s + '.sku_code')} THEN 0 ELSE {raw_unit_cost_sql(s)} END"




_DTYPE_LABEL = {0: "ทานที่ร้าน", 1: "ทานที่ร้าน", 2: "เดลิเวอรี", 3: "กลับบ้าน", 4: "อื่น ๆ"}

@router.get("/api/reports/ops")
def reports_ops(month: Optional[str] = Query(None), branch: Optional[str] = Query(None),
                date_from: Optional[str] = Query(None, description="YYYY-MM-DD; when given the report aggregates DAILY tables over [date_from, date_to]"),
                date_to: Optional[str] = Query(None, description="YYYY-MM-DD; range end (defaults to date_from when only one is given)")):
    """Ops/sales-side report: payment mix, channel mix, void, table turnover, promo
    — from the staged pos_* tables. Revenue/qty only (no cost).

    Scope follows the caller's period filter: when a date range (date_from/date_to) is
    supplied the report aggregates the DAILY tables over [date_from, date_to]
    (pos_payment_day / pos_coupon_day / pos_table_turn_day by day, plus pos_void_day /
    pos_channel_day); otherwise it uses the MONTHLY tables for `month` (latest month by
    default — unchanged behaviour). The daily Void / Channel tables are newer than the
    others, so they degrade to empty until the next sync stages them (Payment / Promo /
    Table per-day work immediately — their daily tables already populate every sync)."""
    daily = bool(date_from or date_to)
    with get_db() as con:
        bid = None
        if branch:
            row = con.execute("SELECT branch_id FROM branches WHERE name=?", (branch,)).fetchone()
            bid = row["branch_id"] if row else -1
        def bf(col="branch_id"):
            return (f" AND {col}=:bid", {"bid": bid}) if bid is not None else ("", {})
        bfc, bp = bf()
        # Which staged tables exist — daily Void/Channel are added by a later sync, so a
        # daily request degrades those sections to empty until then (not a 500).
        tabs = {r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name IN "
            "('pos_payment_day','pos_channel_day','pos_void_day','pos_coupon_day','pos_table_turn_day')")}

        if daily:
            d1 = date_from or date_to
            d2 = date_to or date_from
            dscope = {"d1": d1, "d2": d2}
            def dwhere(alias=""):                      # date predicate (col `day` on *_day tables)
                a = f"{alias}." if alias else ""
                return f"{a}day BETWEEN :d1 AND :d2"
            pay_tbl, ch_tbl, void_tbl, cpn_tbl = "pos_payment_day", "pos_channel_day", "pos_void_day", "pos_coupon_day"
            scope = {"month": None, "date_from": d1, "date_to": d2, "daily": True}
        else:
            mrow = con.execute("SELECT MAX(period_month) m FROM pos_payment_month").fetchone()
            mon = month or (mrow["m"] if mrow else None)
            if not mon:
                return {"month": None, "date_from": None, "date_to": None, "daily": False,
                        "payment": [], "channel": [], "void": [],
                        "void_total": {"value": 0, "qty": 0, "bom_cost": 0}, "table": {}, "promo": []}
            dscope = {"m": mon}
            def dwhere(alias=""):                      # date predicate (col `period_month`)
                a = f"{alias}." if alias else ""
                return f"{a}period_month=:m"
            pay_tbl, ch_tbl, void_tbl, cpn_tbl = "pos_payment_month", "pos_channel_month", "pos_void", "pos_coupon_month"
            scope = {"month": mon, "date_from": None, "date_to": None, "daily": False}

        prm = {**dscope, **bp}
        # In daily mode a *_day table may not be staged yet → degrade that section to empty.
        def _ok(tbl):
            return (not daily) or (tbl in tabs)

        payment = [dict(r) for r in con.execute(
            f"SELECT payment_code code, ROUND(SUM(amount)) amount, SUM(txn_count) n FROM {pay_tbl} "
            f"WHERE {dwhere()}{bfc} GROUP BY payment_code ORDER BY amount DESC", prm).fetchall()] if _ok(pay_tbl) else []

        channel = []
        if _ok(ch_tbl):
            # pos_channel_day/_month is a POS roll-up with no service-charge column,
            # so this one revenue figure stays ex-VAT only. Disclosed, not silently mixed.
            ch = con.execute(f"SELECT delivery_type dtype, SUM(bills) bills, SUM(customers) cust, ROUND(SUM(total_inc_vat/{VAT_DIVISOR})) rev "
                             f"FROM {ch_tbl} WHERE {dwhere()}{bfc} GROUP BY delivery_type", prm).fetchall()
            channel = [{"label": _DTYPE_LABEL.get(r["dtype"], "อื่น ๆ"), "bills": r["bills"], "customers": r["cust"], "revenue": r["rev"] or 0} for r in ch]

        _uc = _bom_unit_cost_sql("s")
        void, vt = [], None
        if _ok(void_tbl):
            void = [dict(r) for r in con.execute(
                f"SELECT COALESCE(s.description,s.sku_code) menu, ROUND(SUM(v.qty)) qty, ROUND(SUM(v.ext_price)) value, "
                f"ROUND(SUM(v.qty * ({_uc}))) bom_cost "
                f"FROM {void_tbl} v JOIN skus s ON s.sku_id=v.sku_id WHERE {dwhere('v')}{bfc} "
                f"GROUP BY v.sku_id ORDER BY bom_cost DESC LIMIT 12", prm).fetchall()]
            vt = con.execute(
                f"SELECT ROUND(SUM(v.ext_price)) total, ROUND(SUM(v.qty)) qty, ROUND(SUM(v.qty * ({_uc}))) bom_cost "
                f"FROM {void_tbl} v JOIN skus s ON s.sku_id=v.sku_id WHERE {dwhere('v')}{bfc}", prm).fetchone()

        # table turnover (always the daily table; range = day BETWEEN, month = substr match)
        tbf, tbp = bf()
        table = {"avg_turn_min": None, "tabs": 0, "customers": 0}
        if "pos_table_turn_day" in tabs:
            tcond = "day BETWEEN :d1 AND :d2" if daily else "substr(day,1,7)=:m"
            tt = con.execute(f"SELECT ROUND(AVG(avg_turn_min),0) avg_min, SUM(tabs) tabs, SUM(customers) cust "
                             f"FROM pos_table_turn_day WHERE {tcond}{tbf} AND avg_turn_min>0", {**dscope, **tbp}).fetchone()
            table = {"avg_turn_min": tt["avg_min"], "tabs": tt["tabs"] or 0, "customers": tt["cust"] or 0}

        promo = []
        if _ok(cpn_tbl):
            _has_cs = con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='coupon_series'").fetchone()
            _cs_join = "LEFT JOIN coupon_series cs ON cs.coupon_series_id=cm.coupon_series_id" if _has_cs else ""
            _cs_name = "COALESCE(NULLIF(TRIM(cs.name),''),'Series '||cm.coupon_series_id)" if _has_cs else "'Series '||cm.coupon_series_id"
            _cs_code = "cs.code" if _has_cs else "NULL"
            promo = [dict(r) for r in con.execute(
                f"SELECT cm.coupon_series_id series, {_cs_name} name, {_cs_code} code, "
                f"SUM(cm.used_count) uses, ROUND(SUM(cm.discount_amount)) discount "
                f"FROM {cpn_tbl} cm {_cs_join} WHERE {dwhere('cm')}{bfc} "
                f"GROUP BY cm.coupon_series_id ORDER BY discount DESC LIMIT 12", prm).fetchall()]
    return {**scope, "payment": payment, "channel": channel, "void": void,
            "void_total": {"value": (vt["total"] or 0) if vt else 0, "qty": (vt["qty"] or 0) if vt else 0,
                           "bom_cost": (vt["bom_cost"] or 0) if vt else 0},
            "table": table, "promo": promo,
            # daily Void/Channel tables aren't staged yet → tell the UI to show a "after next
            # sync" hint instead of a bare "no data" for those two sections in day/range mode.
            "void_pending": bool(daily and "pos_void_day" not in tabs),
            "channel_pending": bool(daily and "pos_channel_day" not in tabs)}




# ---------------------------------------------------------------------------
# /api/reports/management — Daily, Weekly & Monthly Management Summary (printable)
# ---------------------------------------------------------------------------
def _mgt_month_bounds(_dt, _cal, sel, mtd_month, lc, latest_day):
    """Windows of the monthly management report for month `sel` ('YYYY-MM'): the month
    itself, the previous calendar month and the same month last year. The in-progress
    month (`sel == mtd_month`) runs 1..`lc` (the latest complete day) and both comparison
    windows are clamped to the same elapsed days. Shared by the report and the phone
    month-by-month series, so the two never draw a month differently.
    → (cs, ce, ps, pe, ys, ye, partial)"""
    from periods import resolve                # the one period pattern (docs/PERIOD_PATTERN.md)
    sy, sm = int(sel[:4]), int(sel[5:7])
    full_end = _dt.date(sy, sm, _cal.monthrange(sy, sm)[1])
    # a closed month is drawn whole (anchor = its last day); the in-progress one to date (anchor = lc)
    w = resolve("month", min(full_end, lc) if sel == mtd_month else full_end, date_from=f"{sel}-01")
    cs, ce, ps, pe, ys, ye = (_dt.date.fromisoformat(x) for x in (*w["cur"], *w["prev"], *w["ly"]))
    partial = ce > latest_day or sel == mtd_month
    return cs, ce, ps, pe, ys, ye, partial


def _mgt_window(con, *, DOW, MON, _cal, _dt, date, dlabel, month, mtd, params, period, th, week, where, year=None):
    # names this section hands on; None where a period path never assigns one
    ce = None
    comp_label = None
    cs = None
    days_in_period = None
    focus = None
    has_prev = None
    has_yoy = None
    is_mtd = False
    label = None
    latest_day = None
    maxd = None
    mind = None
    months = None
    partial = None
    pe = None
    ps = None
    ref_anchor = None
    selected_month = None
    selected_week = None
    selected_year = None
    weeks = None
    years = None
    to_date = False
    ye = None
    ys = None
    from periods import resolve                # the one period pattern (docs/PERIOD_PATTERN.md)
    _w = None
    rng = con.execute(
        f"SELECT MIN(date(st.sold_at)) lo, MAX(date(st.sold_at)) hi "
        f"FROM sales_transaction st JOIN branches b ON b.branch_id=st.branch_id "
        f"WHERE {where}", params).fetchone()
    maxd = rng["hi"] if rng else None
    mind = (rng["lo"] if rng else None) or "9999-99-99"
    if not maxd:
        return {"available": False, "period": period}
    # Never DEFAULT the daily report onto the in-progress current day: its handful
    # of early sales read as a chain-wide ~-100% collapse across every section.
    # With no explicit date, anchor on the latest COMPLETE day (strictly before
    # Thai 'today', UTC+7). An explicitly-requested today is honoured but flagged
    # partial below. Mirrors the weekly guard, which never picks the open week.
    today_th = con.execute("SELECT date('now','+7 hours')").fetchone()[0]
    if date:
        focus = date[:10]
    elif period == "daily":
        # 2026-10-31: the daily report's day is the brief's and the scorecard's - the last finished day whose bills have
        # arrived (rules.latest_settled_day steps back over a day the POS has not posted yet), so the three name one day
        from rules import latest_settled_day
        focus = (latest_settled_day(con, where=where.replace("st.", "t."), params=params)["day"] or maxd)[:10]
    else:
        _cd = con.execute(
            f"SELECT MAX(date(st.sold_at)) d FROM sales_transaction st "
            f"JOIN branches b ON b.branch_id=st.branch_id "
            f"WHERE {where} AND date(st.sold_at) < :t",
            {**params, "t": today_th}).fetchone()
        focus = ((_cd["d"] if _cd and _cd["d"] else maxd))[:10]
    fy, fm, fd = (int(x) for x in focus.split("-"))
    fdate = _dt.date(fy, fm, fd)

    # ---- period bounds + WoW-fair comparison window -------------------
    latest_day = _dt.date.fromisoformat(maxd)
    # PR-PP2: a week / month / year is FINISHED only through the latest COMPLETE day (never the day still trading -
    # MAX(sold_at) is today after the noon sync); one not finished is to date through that day (periods.resolve)
    _lc = min(latest_day, _dt.date.fromisoformat(today_th) - _dt.timedelta(days=1))
    weeks: list = []
    selected_week = None
    months: list = []
    selected_month = None
    years: list = []
    if period == "daily":
        _w = resolve("day", fdate)                # periods: prev = same weekday last week, ly = 364 days back
        cs = ce = fdate
        ps, pe = (_dt.date.fromisoformat(x) for x in _w["prev"])
        days_in_period = 1
        partial = (focus == today_th)   # in-progress current day (explicit date=today)
        ref_anchor = fdate
        if th:
            label = f"{DOW[fdate.weekday()]} {dlabel(fdate)}"
            comp_label = "วันเดียวกันสัปดาห์ก่อน"
        else:
            label = f"{DOW[fdate.weekday()]}, {dlabel(fdate)}"
            comp_label = "same weekday last week"
    elif period == "monthly":
        # COMPLETE calendar months only (mirrors the weekly full-week rule): a
        # month is selectable once its last day is <= the latest data day —
        # never the in-progress month, whose partial totals would read as a
        # chain-wide collapse against a full prior month. Most recent first.
        mrows = con.execute(
            f"SELECT DISTINCT strftime('%Y-%m', st.sold_at) m FROM sales_transaction st "
            f"JOIN branches b ON b.branch_id=st.branch_id WHERE {where}", params).fetchall()
        for m in sorted((r["m"] for r in mrows if r["m"]), reverse=True):
            my, mm = int(m[:4]), int(m[5:7])
            if _dt.date(my, mm, _cal.monthrange(my, mm)[1]) <= _lc:
                months.append({"month": m, "label": f"{MON[mm-1]} {my}"})
            if len(months) >= 24:
                break
        # Default = latest complete month; fall back to the latest data month
        # (flagged partial) only when no month has completed yet.
        sel = months[0]["month"] if months else maxd[:7]
        # mtd=1 (the phone, 2026-10-01): the in-progress month is selectable too,
        # as MONTH-TO-DATE through the latest COMPLETE day, and every comparison
        # is clamped to the same elapsed days (previous month 1..d, same month
        # last year 1..d) so a part month is never judged against a full one.
        # The printed report never asks for it: a pack is for a closed month.
        # PR-PP2: the month in progress is always offered (it was the phone's mtd=1 only), month to date through the
        # latest complete day, every comparison clamped to the same elapsed days; the default stays the latest
        # COMPLETE month (a printed pack is for a closed month) unless mtd=1 asks for it.
        mtd_month = None
        _lcm = _lc.strftime("%Y-%m")
        if not any(x["month"] == _lcm for x in months) and _lc.isoformat() >= mind:
            mtd_month = _lcm
            months.insert(0, {"month": mtd_month, "mtd": True, "through": _lc.isoformat(),
                              "label": f"{MON[_lc.month-1]} {_lc.year}" + (" (ถึงวันที่ " + str(_lc.day) + ")" if th else f" (to day {_lc.day})")})
            if mtd or not any(not x.get("mtd") for x in months):
                sel = mtd_month
        if month:
            m10 = month[:7]
            if any(x["month"] == m10 for x in months):
                sel = m10
        sy, sm = int(sel[:4]), int(sel[5:7])
        cs, ce, ps, pe, _ys_m, _ye_m, partial = _mgt_month_bounds(
            _dt, _cal, sel, mtd_month, _lc, latest_day)
        to_date = bool(sel == mtd_month)
        days_in_period = (ce - cs).days + 1
        ref_anchor = ce
        selected_month = sel
        is_mtd = bool(sel == mtd_month)
        if th:
            label = f"เดือน {MON[sm-1]} {sy}" + (f" (1–{ce.day})" if sel == mtd_month else "")
            comp_label = "เดือนก่อนหน้า" + (f" (วันที่ 1–{ce.day})" if sel == mtd_month else "")
        else:
            label = f"{MON[sm-1]} {sy}" + (f" (1–{ce.day})" if sel == mtd_month else "")
            comp_label = "previous month" + (f" (days 1–{ce.day})" if sel == mtd_month else "")
    elif period == "yearly":
        # PR-PP2: the calendar year - the year in progress to date through the latest complete day (1 Jan .. that
        # day) against 1 Jan .. the same date a year back; a finished year whole against the whole year before.
        yrows = con.execute(
            f"SELECT DISTINCT strftime('%Y', st.sold_at) y FROM sales_transaction st "
            f"JOIN branches b ON b.branch_id=st.branch_id WHERE {where}", params).fetchall()
        for y in sorted((r["y"] for r in yrows if r["y"]), reverse=True):
            done = _dt.date(int(y), 12, 31) <= _lc
            if not done and int(y) != _lc.year:
                continue
            years.append({"year": y, "to_date": not done, "through": None if done else _lc.isoformat(),
                          "label": (f"ปี {y}" if th else f"Year {y}") + ("" if done else (f" (1 {MON[0]}–{_lc.day} {MON[_lc.month-1]})"))})
        sel_y = years[0]["year"] if years else str(_lc.year)
        if year and any(x["year"] == str(year)[:4] for x in years):
            sel_y = str(year)[:4]
        _w = resolve("year", _lc if int(sel_y) == _lc.year else _dt.date(int(sel_y), 12, 31), date_from=f"{sel_y}-01-01")
        cs, ce, ps, pe = (_dt.date.fromisoformat(x) for x in (*_w["cur"], *_w["prev"]))
        days_in_period = _w["days"]
        partial = not _w["complete"]
        to_date = partial
        ref_anchor = ce
        selected_year = sel_y
        if th:
            label = f"ปี {sel_y}" + (f" (1 {MON[0]}–{dlabel(ce)})" if partial else "")
            comp_label = "ปีก่อน" + (" (ช่วงวันเดียวกัน)" if partial else "")
        else:
            label = f"Year {sel_y}" + (f" (1 {MON[0]} – {dlabel(ce)})" if partial else "")
            comp_label = "previous year" + (" (same days)" if partial else "")
    else:
        # FULL weeks only (Mon–Sun). First, the selectable COMPLETED weeks that
        # have data — most recent first, cap 26 — so the picker and the default
        # both come from real, finished weeks (never the current partial week).
        # PR-PP2: plus the week in progress, to date (Mon .. the latest complete day), listed first.
        mon_expr = "date(st.sold_at, '-' || ((CAST(strftime('%w', st.sold_at) AS INT)+6)%7) || ' days')"
        monrows = con.execute(
            f"SELECT DISTINCT {mon_expr} mon FROM sales_transaction st "
            f"JOIN branches b ON b.branch_id=st.branch_id WHERE {where}", params).fetchall()
        for m in sorted((r["mon"] for r in monrows if r["mon"]), reverse=True):
            md = _dt.date.fromisoformat(m)
            sun = md + _dt.timedelta(days=6)
            if sun <= _lc:
                weeks.append({"start": m, "end": sun.isoformat(),
                              "label": f"{dlabel(md)} – {dlabel(sun)}"})
            if len(weeks) >= 26:
                break
        # Default = most recent completed week WITH data; fall back to the calendar
        # week before the latest day if there is no fully-completed week yet.
        cur_mon = _lc - _dt.timedelta(days=_lc.weekday())
        default_mon = (_dt.date.fromisoformat(weeks[0]["start"]) if weeks
                       else cur_mon - _dt.timedelta(days=7))
        wtd = (cur_mon + _dt.timedelta(days=6)) > _lc and cur_mon.isoformat() >= mind
        if wtd:
            weeks.insert(0, {"start": cur_mon.isoformat(), "end": _lc.isoformat(), "to_date": True,
                             "label": f"{dlabel(cur_mon)} – {dlabel(_lc)}" + (" (ถึงวันที่)" if th else " (to date)")})
        sel_mon = default_mon
        if week:
            try:
                wd = _dt.date.fromisoformat(week[:10])
                cand = wd - _dt.timedelta(days=wd.weekday())          # snap to that week's Monday
                if (cand + _dt.timedelta(days=6)) <= _lc or (wtd and cand == cur_mon):
                    sel_mon = cand                                    # a finished week, or the week to date
            except Exception:
                pass
        if wtd and sel_mon == cur_mon:
            _w = resolve("week", _lc, date_from=cur_mon)            # Mon .. the latest complete day, same weekdays back
        else:
            _w = resolve("week", sel_mon + _dt.timedelta(days=6))   # a closed week, whole (periods)
        cs, ce, ps, pe = (_dt.date.fromisoformat(x) for x in (*_w["cur"], *_w["prev"]))
        days_in_period = _w["days"]
        partial = not _w["complete"]
        to_date = partial
        ref_anchor = ce
        selected_week = cs.isoformat()
        if th:
            label = f"สัปดาห์ {dlabel(cs)} – {dlabel(ce)}" + (" (ถึงวันที่)" if partial else " (จ.–อา.)")
            comp_label = "ช่วงเดียวกันสัปดาห์ก่อน" if partial else "สัปดาห์ก่อนหน้า (จ.–อา.)"
        else:
            label = f"Week of {dlabel(cs)} – {dlabel(ce)}" + (" (to date)" if partial else " (Mon–Sun)")
            comp_label = "the same days last week" if partial else "previous week (Mon–Sun)"
    has_prev = ps.isoformat() >= mind
    # YoY window — 364 days back (exactly 52 weeks) for BOTH daily and weekly, so the
    # weekday stays aligned. Restaurant revenue is strongly weekday-seasonal, so a
    # same-calendar-date YoY would drift (e.g. Sat→Fri) and distort the comparison.
    # Dropped if it predates the data. Monthly compares the SAME CALENDAR month
    # last year (full month vs full month — the standard for monthly reporting;
    # weekday drift washes out over a whole month).
    if period == "monthly":
        ys, ye = _ys_m, _ye_m
    else:
        ys, ye = (_dt.date.fromisoformat(x) for x in _w["ly"])
    has_yoy = ys.isoformat() >= mind
    return {"ce": ce, "comp_label": comp_label, "cs": cs, "days_in_period": days_in_period, "focus": focus, "has_prev": has_prev, "has_yoy": has_yoy, "is_mtd": is_mtd, "label": label, "latest_day": latest_day, "maxd": maxd, "mind": mind, "months": months, "partial": partial, "pe": pe, "ps": ps, "ref_anchor": ref_anchor, "selected_month": selected_month, "selected_week": selected_week, "selected_year": selected_year, "to_date": to_date, "weeks": weeks, "years": years, "ye": ye, "ys": ys}


def _mgt_agg(con, where, params, s, e):
    """Headline totals for the bill-level window [s, e]. The one place the management
    report's revenue / covers / bills / discount are summed: the report headline and the
    phone month-by-month series (`/api/reports/management/months`) both call it, so a
    month reads the same on either."""
    r = con.execute(f"""
        SELECT ROUND({net_revenue_sum('st')},2) rev,
               COALESCE(SUM(st.customer_count),0)  cust,
               COUNT(*)                            bills,
               ROUND(COALESCE(SUM(st.discount),0),2) disc
        FROM sales_transaction st JOIN branches b ON b.branch_id=st.branch_id
        WHERE {where} AND date(st.sold_at) BETWEEN :s AND :e
    """, {**params, "s": s.isoformat(), "e": e.isoformat()}).fetchone()
    rev = r["rev"] or 0; cust = r["cust"] or 0; bills = r["bills"] or 0
    return {"revenue": rev, "customers": cust, "bills": bills, "discount": r["disc"] or 0,
            "rev_per_head": round(rev/cust, 1) if cust else None,
            "avg_ticket":   round(rev/bills, 1) if bills else None}


def _mgt_dow_baseline(con, where, params, prev_month, month_first, n_days, by_branch=False):
    """The weekday-fair baseline of a PART month (the report's §3): `prev_month`'s per-weekday
    daily averages summed over the `n_days` elapsed dates of the month starting `month_first`
    (a weekday that month never had takes the mean of the weekday averages it did have).
    Early in the month a raw same-days compare reads a holiday Monday as a collapse; this does
    not. ONE definition for every month-to-date comparison on the phone and in the report:
    the headline chips, the branch league, the month-by-month series.
    → {revenue, customers, bills} (or {branch: {...}} with `by_branch`), None when the month
    has no sales."""
    import datetime as _dt
    rows = con.execute(f"""
        SELECT {"b.name branch, " if by_branch else ""}CAST(strftime('%w', st.sold_at) AS INT) wd,
               {net_revenue_sum('st')} rev,
               COALESCE(SUM(st.customer_count),0) cust,
               COUNT(*) bills,
               COUNT(DISTINCT date(st.sold_at)) days
        FROM sales_transaction st JOIN branches b ON b.branch_id=st.branch_id
        WHERE {where} AND strftime('%Y-%m', st.sold_at)=:month
        GROUP BY {"b.name, wd" if by_branch else "wd"}""", {**params, "month": prev_month}).fetchall()
    wds = [int((month_first + _dt.timedelta(days=i)).strftime("%w")) for i in range(n_days)]

    def build(rs):
        avg = {r["wd"]: ((r["rev"] or 0) / r["days"], (r["cust"] or 0) / r["days"], (r["bills"] or 0) / r["days"])
               for r in rs if r["days"]}
        if not avg or not n_days:
            return None
        fb = tuple(sum(v[i] for v in avg.values()) / len(avg) for i in range(3))
        return {"revenue": round(sum(avg.get(x, fb)[0] for x in wds), 2),
                "customers": round(sum(avg.get(x, fb)[1] for x in wds), 1),
                "bills": round(sum(avg.get(x, fb)[2] for x in wds), 1)}

    if not by_branch:
        return build(rows)
    grouped: dict = {}
    for r in rows:
        grouped.setdefault(r["branch"], []).append(r)
    return {nm: b for nm, rs in grouped.items() if (b := build(rs))}


def _mgt_headline(con, *, ce, cs, has_prev, has_yoy, is_mtd=False, params, pct, pe, ps, where, ye, ys):
    # names this section hands on; None where a period path never assigns one
    cur = None
    headline = None
    prev = None
    # ---- headline totals for an arbitrary [s,e] range -----------------
    def agg(s, e):
        return _mgt_agg(con, where, params, s, e)

    cur  = agg(cs, ce)
    prev = agg(ps, pe) if has_prev else None
    headline = dict(cur)
    # share of PRE-discount sales (revenue + discount) — the same base the Discounts
    # tab uses, so the two pages agree; then the ceiling and the baht beyond it.
    _base = (cur["revenue"] or 0) + (cur["discount"] or 0)
    headline["discount_pct"] = round(cur["discount"]/_base*100, 1) if _base else None
    headline["discount_policy_pct"] = discount_policy_pct(con)
    headline["discount_excess"] = discount_excess(cur["discount"], _base, headline["discount_policy_pct"])
    if prev:
        headline["prev"] = prev
        headline["rev_wow_pct"]   = pct(cur["revenue"], prev["revenue"])
        headline["cust_wow_pct"]  = pct(cur["customers"], prev["customers"])
        headline["bills_wow_pct"] = pct(cur["bills"], prev["bills"])
        headline["rph_wow_pct"]   = pct(cur["rev_per_head"] or 0, prev["rev_per_head"]) if prev["rev_per_head"] else None
        headline["ticket_wow_pct"]= pct(cur["avg_ticket"] or 0, prev["avg_ticket"]) if prev["avg_ticket"] else None
        if is_mtd:
            # PR-PP2 (owner 2026-10-24, docs/PERIOD_PATTERN.md): a month in progress is compared to date on the
            # calendar - days 1..n against days 1..n of last month (the windows above, periods.resolve). The
            # weekday-fair baseline stays on the report's §3 pace only (pace.mom_basis = 'dow').
            headline["mom_basis"] = "to_date"
    yoy = agg(ys, ye) if has_yoy else None
    if yoy:
        headline["yoy"] = yoy
        headline["rev_yoy_pct"]  = pct(cur["revenue"], yoy["revenue"])
        headline["cust_yoy_pct"] = pct(cur["customers"], yoy["customers"])
        headline["rph_yoy_pct"]  = pct(cur["rev_per_head"] or 0, yoy["rev_per_head"]) if yoy["rev_per_head"] else None
    return {"cur": cur, "headline": headline, "prev": prev}


def _mgt_branches(con, *, branch, ce, cs, group, has_prev, has_yoy, is_mtd=False, params, pct, pe, period, ps, where, ye, ys):
    # names this section hands on; None where a period path never assigns one
    branches = None
    # ---- branch league table -----------------------------------------
    def by_branch(s, e):
        rows = con.execute(f"""
            SELECT b.name branch,
                   ROUND({net_revenue_sum('st')},2) rev,
                   COALESCE(SUM(st.customer_count),0)  cust,
                   COUNT(*)                            bills
            FROM sales_transaction st JOIN branches b ON b.branch_id=st.branch_id
            WHERE {where} AND date(st.sold_at) BETWEEN :s AND :e
            GROUP BY b.name""", {**params, "s": s.isoformat(), "e": e.isoformat()}).fetchall()
        return {r["branch"]: r for r in rows}
    cur_b  = by_branch(cs, ce)
    prev_b = by_branch(ps, pe) if has_prev else {}
    # Owner request 2026-10-01: the league answers "which branch is behind", not
    # only "is the chain behind" — so each row carries its own YoY (the same
    # year-ago window as the headline) and, on the monthly report, its own
    # target (targets.py: typed figure, else its actual of the same month last
    # year) with the achievement %. Daily/weekly rows have no target: targets
    # are monthly figures.
    yoy_b = by_branch(ys, ye) if has_yoy else {}
    tgt_b = ({t["branch"]: t for t in targets_for_month(con, cs.strftime("%Y-%m"), branch, group)}
             if period == "monthly" else {})
    tot_rev = sum((r["rev"] or 0) for r in cur_b.values()) or 0
    # PR-PP2: a month in progress compares each branch to date on the calendar, as the headline does
    dow_b = None
    branches = []
    for nm, r in cur_b.items():
        rev = r["rev"] or 0; cust = r["cust"] or 0
        p = prev_b.get(nm)
        y = yoy_b.get(nm)
        t = tgt_b.get(nm)
        has_t = bool(t and t["source"] != "none" and t["revenue_target"])
        branches.append({
            "branch": nm, "revenue": rev, "customers": cust, "bills": r["bills"],
            "rev_per_head": round(rev/cust, 1) if cust else None,
            "share_pct": round(rev/tot_rev*100, 1) if tot_rev else None,
            "wow_pct": ((pct(rev, dow_b[nm]["revenue"]) if nm in dow_b else None) if dow_b is not None
                        else (pct(rev, p["rev"] or 0) if p else None)),
            # DV6 (phone table review 2026-10-25, §3.4): the baht behind wow_pct - the same windows (the comparison period,
            # to date in a month in progress), so the phone ranks the branches by the money, not the %
            "prev_revenue": (p["rev"] or 0) if p else None,
            "rev_delta": round(rev - (p["rev"] or 0)) if p else None,
            "yoy_pct": pct(rev, y["rev"] or 0) if (y and (y["rev"] or 0) > 0) else None,
            "target": round(t["revenue_target"]) if has_t else None,
            "target_source": (t["source"] if t else None) if period == "monthly" else None,
            "vs_target_pct": round(rev / t["revenue_target"] * 100, 1) if has_t else None,
        })
    branches.sort(key=lambda x: -x["revenue"])
    # owner 2026-10-26: a branch with no sale here nor in the period before is not a league row (rules.closed_branches; the
    # year-ago period does not count); ly_hidden = the year-ago sales of the branches left out (the footnote)
    _closed = closed_branches(con, [(cs, ce), (ps, pe) if has_prev else None], keep=branch)
    ly_hidden = hidden_amount({nm: r["rev"] for nm, r in yoy_b.items()}, _closed)
    branches = drop_closed(branches, _closed)
    prev_rank_map = {nm: i + 1 for i, (nm, _) in
                     enumerate(sorted(prev_b.items(), key=lambda kv: -(kv[1]["rev"] or 0)))}
    for i, b in enumerate(branches):
        b["rank"] = i + 1
        pr = prev_rank_map.get(b["branch"])
        b["rank_change"] = (pr - (i + 1)) if pr else None  # positive = moved up
    return {"branches": branches, **({"branches_ly_hidden": ly_hidden} if ly_hidden else {})}


def _mgt_daily_normal(con, ctx) -> None:
    """The daily report's headline and branch league on THE daily baseline (owner 2026-10-31): "ปกติ" = the median of the
    same weekday over the 8 weeks before (rules.daily_baseline - the chain the phone hero and the Daily Brief quote, each
    branch the scorecard's own figure). In place on ctx: headline.prev becomes the normal (revenue, customers, bills, per
    head, per bill) and the *_wow_pct fields compare with it; each league row's wow_pct / prev_revenue / rev_delta likewise,
    plus normal_weeks and below_normal. Under 2 usable weeks there is no comparison (null). comp_label names the normal;
    prev_label names what the sections below the league still compare with (the GP bridge, menus, dayparts, payments:
    the same weekday last week - a menu or a tender has no 8-week normal)."""
    from rules import baseline_dates, baseline_from_days, baseline_label, chain_baseline, daily_series
    focus, where, params, pct, th = ctx["focus"], ctx["where"], ctx["params"], ctx["pct"], ctx["th"]
    per = daily_series(con, where, params, [focus] + baseline_dates(focus), alias="st")
    ch = chain_baseline(con, per, focus, keep=ctx.get("branch"))
    H, N = ctx["headline"], ch["normal"]
    if ch["has_verdict"]:
        H["prev"] = {"revenue": N["revenue"], "customers": N["customers"], "bills": N["bills"],
                     "rev_per_head": N["rev_per_head"], "avg_ticket": N["avg_ticket"]}
        H["rev_wow_pct"] = pct(H["revenue"], N["revenue"])
        H["cust_wow_pct"] = pct(H["customers"], N["customers"])
        H["bills_wow_pct"] = pct(H["bills"], N["bills"])
        H["rph_wow_pct"] = pct(H["rev_per_head"] or 0, N["rev_per_head"]) if N["rev_per_head"] else None
        H["ticket_wow_pct"] = pct(H["avg_ticket"] or 0, N["avg_ticket"]) if N["avg_ticket"] else None
    else:
        H.pop("prev", None)
        for k in ("rev_wow_pct", "cust_wow_pct", "bills_wow_pct", "rph_wow_pct", "ticket_wow_pct"):
            H[k] = None
    H["basis"] = "normal"
    H["normal_weeks"] = N["n_weeks"]
    H["normal_dates"] = N["dates"]
    for b in ctx.get("branches") or []:
        bl = baseline_from_days(per.get(b["branch"], {}), focus)
        b["wow_pct"] = bl["vs_normal_pct"]
        b["prev_revenue"] = bl["normal"]["revenue"] if bl["has_verdict"] else None
        b["rev_delta"] = bl["vs_normal_baht"]
        b["normal_weeks"] = bl["normal"]["n_weeks"]
        b["below_normal"] = bl["below_normal"]
    n = N["n_weeks"]
    if th:
        ctx["comp_label"] = baseline_label(n).replace("เทียบ", "", 1) if n >= 2 else "ปกติ (ยังไม่มีฐาน)"
        ctx["prev_label"] = "วันเดียวกันสัปดาห์ก่อน"
    else:
        ctx["comp_label"] = ("normal (median of the same weekday, 8 weeks)" if n >= 8 else
                             f"normal (median of {n} weeks)" if n >= 2 else "normal (no base yet)")
        ctx["prev_label"] = "same weekday last week"


def _cph_split(q_cur, cost_cur, q_prev, cost_prev, covers_cur, covers_prev) -> dict:
    """DV1 (review §5 no. 2) · a dish's change in cost per head = price_delta (its cost per plate, at last period's plates
    per head) + usage_delta (its plates per head, at today's cost per plate). usage_delta is what วิเคราะห์เมนู compares
    (it holds the cost constant), so "+฿11.39" here and "-0.1 %" there are the same dish: price +฿11.40, plates -฿0.01.
    {} when there is nothing to split (no covers or no plates now)."""
    if not (covers_cur and covers_prev and q_cur > 0):
        return {}
    u_cur = cost_cur / q_cur
    u_prev = cost_prev / q_prev if q_prev > 0 else u_cur
    return {"usage_delta": round((q_cur / covers_cur - q_prev / covers_prev) * u_cur, 2),
            "price_delta": round(q_prev / covers_prev * (u_cur - u_prev), 2)}


def _cost_riser_action(m0: dict, pword: str, th: bool) -> str:
    """The to-do line for the dish whose cost per head rose most, naming the cause (DV1, review §5 no. 2): the cost per
    plate (check the price list) or the plates per head (check portioning)."""
    pdl, udl = m0.get("price_delta"), m0.get("usage_delta")
    if pdl is not None and udl is not None:
        import ingredient_groups as IG                       # the one cause rule: the larger part in the net's direction
        by_price = IG.net_driver({"price": pdl, "usage": udl}, pdl + udl) == "price"

        def sg(v):
            return ("+" if v > 0 else "−" if v < 0 else "") + f"฿{abs(v):.2f}"
        # PR-PD (owner 2026-10-29, no. 18): "ราคาต่อจาน ฿0.00" read as a selling price of zero - the figure is the dish's
        # cost per plate; an unchanged one is said in words, so the line names the one cause that moved
        split_th = (f": ต้นทุนต่อจานเท่าเดิม · กินต่อหัว {sg(udl)}" if abs(pdl) < 0.005 else f": ต้นทุนต่อจาน {sg(pdl)} · กินต่อหัว {sg(udl)}")
        split_en = (f": cost per plate unchanged · plates per head {sg(udl)}" if abs(pdl) < 0.005 else f": cost per plate {sg(pdl)} · plates per head {sg(udl)}")
        act_th = "ตรวจสอบราคาต่อจาน / ราคาวัตถุดิบ" if by_price else "ตรวจสอบการตักเสิร์ฟ"
        act_en = "check the dish cost / ingredient prices" if by_price else "check portioning"
    else:
        split_th = split_en = ""
        act_th, act_en = "ตรวจสอบการตักเสิร์ฟ / ต้นทุนวัตถุดิบ", "check portioning / ingredient cost"
    if th:
        return (f"{m0['menu']} ต้นทุนต่อหัวเพิ่มขึ้น ฿{m0['delta']:.2f} เทียบ{pword}ก่อน "
                f"(฿{m0['cost_per_head']:.2f}/หัว){split_th} — {act_th}")
    return (f"{m0['menu']} food cost/head up ฿{m0['delta']:.2f} vs last {pword} "
            f"(฿{m0['cost_per_head']:.2f}/head){split_en} — {act_en}.")


def _mgt_target_scope(con, ctx) -> None:
    """DV1 (phone table review 2026-10-25, §5 no. 5) · targets on one basis, in place on ctx (monthly report only).
    A month in progress measures each branch on its month-end projection, as the chain does: its sales to date x the
    chain's projection factor (pace.projected / to date, weekday-fair), so the branches' projections sum to the chain's
    and "18 branches under 90 %" no longer divides 19 days of sales by a whole month's target (mtd_vs_target_pct keeps
    the to-date ratio). The chain's % of target is like for like (targets.attainment): the branches with a target that
    sold this month - pace.target / proj_vs_target_pct follow it, pace.target_scope says what was left out."""
    pace, branches, cs = ctx.get("pace"), ctx.get("branches") or [], ctx.get("cs")
    if ctx.get("period") != "monthly" or not pace or not cs or pace.get("month") != cs.strftime("%Y-%m"):
        return
    mtd = pace.get("mtd_revenue")
    live = not ctx.get("month_complete") and pace.get("projected") and mtd
    f = pace["projected"] / mtd if live else 1.0
    if live:
        for b in branches:
            if b.get("target"):
                b["mtd_vs_target_pct"] = b["vs_target_pct"]
                b["projected"] = round((b["revenue"] or 0) * f)
                b["vs_target_pct"] = round(b["projected"] / b["target"] * 100, 1)
                b["target_basis"] = "projected"
    # DV6 (review §3.4): the baht a branch is away from its target on the same basis as vs_target_pct (the month-end
    # projection while the month is open, the month's sales once it is complete) - the phone's branch list ranks by it
    for b in branches:
        if b.get("target"):
            b["target_gap"] = round((b["projected"] if live else (b["revenue"] or 0)) - b["target"])
    att = attainment(con, pace["month"], {b["branch"]: b["revenue"] for b in branches}, f, ctx.get("branch"), ctx.get("group"))
    pace["target"] = att["target"]
    pace["proj_vs_target_pct"] = att["pct"]
    pace["target_scope"] = att
    ctx["target"] = att["target"]
    ctx["target_projected"] = att["projected"]
    # PR-PD (owner 2026-10-29, phone decisions no. 2) · the instruction behind the gap: with N days left, the branches that
    # have a target must sell ฿X a day to reach it, against the ฿Y a day they have been selling - the same like-for-like
    # scope as pace.target (the branches with a target), so the two figures and the % of target agree.
    de, dt = pace.get("days_elapsed") or 0, pace.get("days_total") or 0
    left = max(0, dt - de)
    mtd_t = sum((b.get("revenue") or 0) for b in branches if b.get("target"))
    if live and att.get("target") and left > 0 and de > 0:
        pace["run_rate"] = {"days_left": left, "need_per_day": round((att["target"] - mtd_t) / left),
                            "normal_per_day": round(mtd_t / de), "mtd": round(mtd_t),
                            "branches": sum(1 for b in branches if b.get("target"))}


def _mgt_leak(con, *, ce, cs, has_prev, params, pct, pe, ps, where):
    # names this section hands on; None where a period path never assigns one
    cash_heavy = None
    heavy = None
    leak = None
    reprint = None
    # ---- money-leak radar over the period (+ last-period comparison) ----
    # leak_total/cash_heavy_total take an explicit [s,e] window so the SAME signal
    # can be measured for the current period AND the comparison period (last week /
    # same weekday last week) — the §6 cards then show a WoW delta.
    lk = discount_base_row('st')
    def leak_total(extra, s, e):
        r = con.execute(f"""
            SELECT COUNT(*) bills, ROUND(COALESCE(SUM(st.discount),0)) baht
            FROM sales_transaction st JOIN branches b ON b.branch_id=st.branch_id
            WHERE {where} AND date(st.sold_at) BETWEEN :s AND :e {extra}""",
            {**params, "s": s.isoformat(), "e": e.isoformat()}).fetchone()
        return {"bills": r["bills"] or 0, "baht": r["baht"] or 0}
    def leak_branches(extra, by="baht"):
        rows = con.execute(f"""
            SELECT b.name branch, COUNT(*) bills, ROUND(COALESCE(SUM(st.discount),0)) baht
            FROM sales_transaction st JOIN branches b ON b.branch_id=st.branch_id
            WHERE {where} AND date(st.sold_at) BETWEEN :s AND :e {extra}
            GROUP BY b.name ORDER BY {by} DESC LIMIT 3""",
            {**params, "s": cs.isoformat(), "e": ce.isoformat()}).fetchall()
        return [dict(r) for r in rows]
    # cash-heavy: of the period's heavy-discount bills (a SMALL set), how many were
    # paid mostly in CASH. Drive FROM the heavy bills and pick each bill's dominant
    # tender with an indexed (branch_id, receipt_no, amount) lookup — NOT a window
    # over the entire sale_payment table (that scan made this the slowest query in
    # the report). Guards a missing sale_payment.
    def cash_heavy_total(s, e, with_branches=False):
        try:
            dom_code = ("(SELECT CASE WHEN UPPER(sp.payment_code)='CASH' THEN 'Cash' ELSE sp.payment_code END "
                        "FROM sale_payment sp WHERE sp.branch_id=h.branch_id AND sp.receipt_no=h.receipt_no "
                        "ORDER BY sp.amount DESC LIMIT 1)")
            heavy_sub = (f"SELECT st.branch_id, st.receipt_no, st.discount, b.name AS branch, "
                         f"CAST(strftime('%H', st.sold_at) AS INTEGER) AS hr "
                         f"FROM sales_transaction st JOIN branches b ON b.branch_id=st.branch_id "
                         f"WHERE {where} AND date(st.sold_at) BETWEEN :s AND :e "
                         f"AND st.discount>0 AND {lk}>0 AND st.discount*1.0/{lk} >= 0.30")
            lpe = {**params, "s": s.isoformat(), "e": e.isoformat()}
            cr = con.execute(f"""
                SELECT COUNT(*) bills, ROUND(COALESCE(SUM(discount),0)) baht
                FROM (SELECT discount, {dom_code} dc FROM ({heavy_sub}) h)
                WHERE dc='Cash'""", lpe).fetchone()
            out = {"bills": cr["bills"] or 0, "baht": cr["baht"] or 0}
            if with_branches:
                cb = con.execute(f"""
                    SELECT branch, COUNT(*) bills, ROUND(COALESCE(SUM(discount),0)) baht
                    FROM (SELECT branch, discount, {dom_code} dc FROM ({heavy_sub}) h)
                    WHERE dc='Cash' GROUP BY branch ORDER BY baht DESC LIMIT 3""", lpe).fetchall()
                out["by_branch"] = [dict(r) for r in cb]
                # PR-PD (owner 2026-10-29, no. 11) · where to audit today: the branch x shift (กลางวัน before 15:00, เย็น from
                # 15:00 - the shared cashier login allows no finer cut) that holds the most of this baht, with its share
                ts = con.execute(f"""
                    SELECT branch, CASE WHEN hr < 15 THEN 'กลางวัน' ELSE 'เย็น' END shift,
                           COUNT(*) bills, ROUND(COALESCE(SUM(discount),0)) baht
                    FROM (SELECT branch, discount, hr, {dom_code} dc FROM ({heavy_sub}) h)
                    WHERE dc='Cash' GROUP BY branch, shift ORDER BY baht DESC LIMIT 1""", lpe).fetchone()
                if ts and out["baht"]:
                    out["top"] = {"branch": ts["branch"], "shift": ts["shift"], "bills": ts["bills"] or 0, "baht": ts["baht"] or 0,
                                  "share_pct": round((ts["baht"] or 0) / out["baht"] * 100)}
            return out
        except Exception:
            return {"bills": 0, "baht": 0, "by_branch": [], "unavailable": True}
    # WoW helper — attaches prev figures + % deltas onto a leak card.
    def _leak_wow(cur_o, prev_o):
        return {"prev_bills": prev_o["bills"], "prev_baht": prev_o["baht"],
                "bills_wow": pct(cur_o["bills"], prev_o["bills"]),
                "baht_wow":  pct(cur_o["baht"],  prev_o["baht"])}

    heavy_extra   = f"AND st.discount>0 AND {lk}>0 AND st.discount*1.0/{lk} >= 0.30"
    reprint_extra = "AND st.discount>0 AND COALESCE(st.print_times,0)>1"
    zero_extra    = f"AND st.discount>0 AND {lk} <= 0"
    all_extra     = "AND st.discount>0"
    heavy   = {**leak_total(heavy_extra, cs, ce),   "by_branch": leak_branches(heavy_extra)}
    reprint = {**leak_total(reprint_extra, cs, ce), "by_branch": leak_branches(reprint_extra, "bills")}
    zero    = {**leak_total(zero_extra, cs, ce),    "by_branch": leak_branches(zero_extra)}
    cash_heavy = cash_heavy_total(cs, ce, with_branches=True)
    ltot       = leak_total(all_extra, cs, ce)
    if has_prev:
        heavy.update(  _leak_wow(heavy,   leak_total(heavy_extra,   ps, pe)))
        reprint.update(_leak_wow(reprint, leak_total(reprint_extra, ps, pe)))
        zero.update(   _leak_wow(zero,    leak_total(zero_extra,    ps, pe)))
        ltot.update(   _leak_wow(ltot,    leak_total(all_extra,     ps, pe)))
        ch_prev = cash_heavy_total(ps, pe)
        if not cash_heavy.get("unavailable") and not ch_prev.get("unavailable"):
            cash_heavy.update(_leak_wow(cash_heavy, ch_prev))
    # ALL discounted bills in the period (every branch in scope) — the reconciling
    # total so the radar's abnormal-only subsets read against the full figure and
    # tie out with the Discounts menu (both = SUM(Sale.Discount) over the same scope).
    leak = {"heavy_discount": heavy, "cash_heavy": cash_heavy,
            "reprinted": reprint, "zero_gross": zero, "total": ltot}
    return {"cash_heavy": cash_heavy, "heavy": heavy, "leak": leak, "reprint": reprint}


def _mgt_all_cost(con, sw, sp, s, e, source):
    """COGS of EVERYTHING sold in [s, e] (drinks, desserts and delivery included, packages
    at 0), from the day-granular or the month-granular SKU roll-up. Gross profit subtracts
    this. The report's §4 and the phone month-by-month series both read it here.

    The cost is `_unified_cogs_expr` - the SAME expression the P&L, Profit, GP-by-branch and
    every other COGS page multiplies by, so a month's GP% cannot differ between pages. In
    particular it looks up the RELEASED cost of a month from the Grand Menu cut-over on
    (rules.dated_cost_sql): the report used to multiply by the undated cost, so September
    2026 read 43.3 % here and 40.24 % on the P&L page. The day roll-up has no `source` or
    `period_month` column, so it is wrapped to supply both (it is Easy-only)."""
    if source == "daily":
        return con.execute(f"""
            SELECT SUM({_unified_cogs_expr('d', 'sk')})
            FROM (SELECT *, 'Easy' AS source, substr(day, 1, 7) AS period_month
                  FROM v_sales_sku_daily) d
            JOIN branches b ON b.branch_id=d.branch_id
            WHERE {sw} AND d.day BETWEEN :s AND :e""",
            {**sp, "s": s.isoformat(), "e": e.isoformat()}).fetchone()[0] or 0.0
    return con.execute(f"""
        SELECT SUM({_unified_cogs_expr('d', 'sk')}) FROM v_sales_sku_monthly d
        JOIN branches b ON b.branch_id=d.branch_id
        WHERE {sw} AND d.period_month BETWEEN :s AND :e""",
        {**sp, "s": s.strftime("%Y-%m"), "e": e.strftime("%Y-%m")}).fetchone()[0] or 0.0


def _mgt_menu(con, *, _cal, _dt, branch, ce, cs, cur, group, has_prev, has_yoy, leak, params, pct, pe, prev, ps, where, ye, ys):
    # names this section hands on; None where a period path never assigns one
    cmp_ok = None
    prev_cost_available = None
    tot_yoy_cost = None
    cost_available = None
    cost_src = None
    cost_up = None
    daily_min = None
    food_sub = None
    menu_out = None
    sp = None
    sw = None
    tot_cur_cost = None
    tot_prev_cost = None
    # Per-MASTER-branch discount follow-up: amount + % of that branch's ex-VAT sales
    # + bill count, for the period. Master (company-owned) stores are the owner's own
    # money leak — franchise discounts are the franchisee's cost — so this is the line
    # to action on. Honours the report's branch/period scope; always master-only.
    mb_rows = con.execute(f"""
        SELECT b.name branch,
               ROUND({net_revenue_sum('st')}) sales,
               ROUND(COALESCE(SUM(CASE WHEN st.discount>0 THEN st.discount END),0)) disc,
               SUM(CASE WHEN st.discount>0 THEN 1 ELSE 0 END) bills
        FROM sales_transaction st JOIN branches b ON b.branch_id=st.branch_id
        WHERE {where} AND date(st.sold_at) BETWEEN :s AND :e AND COALESCE(b.branch_type,'master')='master'
        GROUP BY b.name HAVING SUM(st.total_inc_vat) > 0
        ORDER BY disc DESC""", {**params, "s": cs.isoformat(), "e": ce.isoformat()}).fetchall()
    leak["by_master_branch"] = [
        {"branch": r["branch"], "sales": r["sales"] or 0, "disc": r["disc"] or 0,
         "bills": r["bills"] or 0,
         "pct": round((r["disc"] or 0) / r["sales"] * 100, 1) if r["sales"] else None}
        for r in mb_rows]

    # ---- menu: food COST PER HEAD by dish + biggest per-head cost movers ----
    # Same idea as วิเคราะห์เมนู / the Daily-Brief menu-movers, but on the COST side:
    #   cost/head = (dish COGS over the period) / (covers in scope), current vs the
    #   comparison period. COGS uses the app's effective unit cost (sku_effective_cost
    #   → non-zero sku_cost fallback) — the same basis as _unified_cogs_expr.
    food_sub = (f"(SELECT sk.sku_id FROM skus sk JOIN categories c "
                f"ON c.category_id=sk.category_id WHERE {_FOOD_CATEGORY_FILTER})")
    sb = ["b.name!='ALL'"]; sp: dict = {}
    scope_filter(sb, sp, branch, group)
    sw = " AND ".join(sb)
    # the dish list prices each sale at the cost in force for ITS month (released cost from
    # the Grand Menu cut-over on) - the same lookup _mgt_all_cost / the P&L use
    _eff_cost_d = effective_unit_cost_sql('d', "substr(d.day, 1, 7)")      # day roll-up
    _eff_cost_mo = effective_unit_cost_sql('d', 'd.period_month')          # month roll-up
    # sales_sku_daily is a ROLLING window (currently starts 2026-03-22) while
    # sales_sku_monthly carries full history. Reading daily blindly made every
    # pre-coverage month report ฿0 food cost — i.e. GP 100% — instead of saying
    # "no data". Resolve the source per window: daily when covered, the monthly
    # table when the window is exactly whole calendar months (the monthly
    # report's case — same tier-safe cost expr, so the numbers tie out), else
    # unavailable so the UI can show "—" rather than a fake zero.
    _dmin_row = con.execute("SELECT MIN(day) d FROM v_sales_sku_daily").fetchone()
    daily_min = (_dmin_row["d"] if _dmin_row else None) or "9999-99-99"

    def _whole_months(s, e):
        return s.day == 1 and e == _dt.date(e.year, e.month,
                                            _cal.monthrange(e.year, e.month)[1])

    def menu_cost_map(s, e):
        """→ (rows_by_sku_id, source, all_cost) where source ∈ daily|monthly|unavailable.

        rows are the dine-in FOOD dishes (the cost/head list); all_cost is the COGS of
        EVERYTHING sold in the window (drinks, desserts, delivery included, packages
        at 0), which is what gross profit must subtract — the revenue it comes off is
        every bill. Until 2026-10-01 GP subtracted the food dishes only, so GP% read
        high and disagreed with the same report's food cost %."""
        if s.isoformat() >= daily_min:
            all_cost = _mgt_all_cost(con, sw, sp, s, e, "daily")
            rows = con.execute(f"""
                SELECT d.sku_id, s.description, s.sku_code,
                       SUM(d.qty) qty, SUM(d.qty * {_eff_cost_d}) cost
                FROM v_sales_sku_daily d JOIN branches b ON b.branch_id=d.branch_id
                JOIN skus s ON s.sku_id=d.sku_id
                WHERE {sw} AND d.day BETWEEN :s AND :e AND d.sku_id IN {food_sub}
                GROUP BY d.sku_id""",
                {**sp, "s": s.isoformat(), "e": e.isoformat()}).fetchall()
            return {r["sku_id"]: r for r in rows}, "daily", all_cost
        if _whole_months(s, e):
            all_cost = _mgt_all_cost(con, sw, sp, s, e, "monthly")
            rows = con.execute(f"""
                SELECT d.sku_id, s.description, s.sku_code,
                       SUM(d.qty) qty, SUM(d.qty * {_eff_cost_mo}) cost
                FROM v_sales_sku_monthly d JOIN branches b ON b.branch_id=d.branch_id
                JOIN skus s ON s.sku_id=d.sku_id
                WHERE {sw} AND d.period_month BETWEEN :s AND :e
                  AND d.sku_id IN {food_sub}
                GROUP BY d.sku_id""",
                {**sp, "s": s.strftime("%Y-%m"), "e": e.strftime("%Y-%m")}).fetchall()
            return {r["sku_id"]: r for r in rows}, "monthly", all_cost
        return {}, "unavailable", 0.0
    def clean(nm):
        return (nm or "").replace("(A) ", "").replace("(B) ", "").strip()
    covers_cur  = cur["customers"] or 0
    covers_prev = (prev["customers"] or 0) if prev else 0
    try:
        cur_m,  cost_src, all_cur = menu_cost_map(cs, ce)
        prev_m, prev_src, all_prev = menu_cost_map(ps, pe) if has_prev else ({}, None, 0.0)
    except Exception:
        cur_m, prev_m, cost_src, prev_src, all_cur, all_prev = {}, {}, "unavailable", None, 0.0, 0.0
    cost_available = cost_src != "unavailable"
    # the comparison window's cost source — a window before the cost tables
    # (prev_src 'unavailable') returns ฿0, which must never read as "no cost"
    prev_cost_available = bool(has_prev and prev_src and prev_src != "unavailable")
    # year-ago window's total COGS for the GP bridge (§4): whole months come from
    # sales_sku_monthly, so the monthly report can bridge YoY; a daily/weekly
    # window 364 days back predates sales_sku_daily and stays None
    tot_yoy_cost = None
    if has_yoy:
        try:
            _, yoy_src, all_yoy = menu_cost_map(ys, ye)
            if yoy_src != "unavailable":
                tot_yoy_cost = all_yoy
        except Exception:
            tot_yoy_cost = None
    def cph(cost, covers): return round(cost / covers, 2) if covers else None
    cmp_ok = has_prev and covers_prev > 0      # only show deltas vs a period that had covers
    menu_rows = []
    for sid, r in cur_m.items():
        cost_cur  = r["cost"] or 0
        cost_prev = (prev_m[sid]["cost"] if sid in prev_m else 0) or 0
        c_cur  = cph(cost_cur, covers_cur)
        c_prev = cph(cost_prev, covers_prev)
        if not c_cur and not c_prev:
            continue
        # DV1 (review §5 no. 2): the change splits into the cost per plate (each month's dish cost) and the plates per head
        # at today's cost - the latter is what วิเคราะห์เมนู compares (it holds the cost constant), so both pages tie out
        split = (_cph_split(r["qty"] or 0, cost_cur, (prev_m[sid]["qty"] if sid in prev_m else 0) or 0, cost_prev,
                            covers_cur, covers_prev) if cmp_ok else {})
        menu_rows.append({
            **split,
            "menu": clean(r["description"] or r["sku_code"]),
            "qty": round(r["qty"] or 0), "cost": round(cost_cur),
            "cost_per_head": c_cur,
            "cost_per_head_prev": c_prev if cmp_ok else None,
            "delta": round((c_cur or 0) - (c_prev or 0), 2) if cmp_ok else None,
            "pct": pct(c_cur or 0, c_prev or 0) if cmp_ok else None,
        })
    # dishes that carried cost last period but none now → cost/head fell to 0
    if cmp_ok:
        for sid, r in prev_m.items():
            if sid in cur_m:
                continue
            c_prev = cph(r["cost"] or 0, covers_prev)
            if not c_prev:
                continue
            menu_rows.append({
                "menu": clean(r["description"] or r["sku_code"]),
                "qty": 0, "cost": 0, "cost_per_head": 0.0,
                "cost_per_head_prev": c_prev,
                "delta": round(-c_prev, 2), "pct": -100.0,
            })
    # 36 per column (owner 2026-10-02: "เพิ่มให้เต็ม … เท่ากันทุกเรื่อง"; 12 covered only
    # 53% of Sep 2026 food cost/head, 36 covers ~74%) — fills one landscape sheet
    # with single-line rows (print: names ellipsise, portion suffix shortened)
    MENU_TOP_N = 36
    cost_top   = sorted([m for m in menu_rows if (m["cost_per_head"] or 0) > 0],
                        key=lambda x: -(x["cost_per_head"] or 0))[:MENU_TOP_N]
    cost_up    = sorted([m for m in menu_rows if m["delta"] and m["delta"] > 0],
                        key=lambda x: -x["delta"])[:MENU_TOP_N]
    cost_down  = sorted([m for m in menu_rows if m["delta"] and m["delta"] < 0],
                        key=lambda x: x["delta"])[:MENU_TOP_N]
    food_cur  = sum((r["cost"] or 0) for r in cur_m.values())
    # how much of the period's food cost the listed dishes carry — printed on the
    # section so the reader knows the list is the head of a long tail, not all of it
    dishes_costed = sum(1 for r in cur_m.values() if (r["cost"] or 0) > 0)
    top_cost = sum((m["cost"] or 0) for m in cost_top)
    top_share_pct = round(top_cost / food_cur * 100, 1) if food_cur else None
    food_prev = sum((r["cost"] or 0) for r in prev_m.values()) if has_prev else 0
    total_cph      = cph(food_cur, covers_cur)
    total_cph_prev = cph(food_prev, covers_prev) if cmp_ok else None
    # gross profit subtracts the cost of everything sold (see menu_cost_map)
    tot_cur_cost  = all_cur
    tot_prev_cost = all_prev if has_prev else 0
    menu_out = {
        "basis": "cost_per_head",
        "covers": covers_cur, "covers_prev": covers_prev if has_prev else None,
        "total_cph": total_cph, "total_cph_prev": total_cph_prev,
        "total_cph_delta": (round((total_cph or 0) - (total_cph_prev or 0), 2)
                            if (total_cph is not None and total_cph_prev is not None) else None),
        "top": cost_top, "risers": cost_up, "savers": cost_down,
        "top_n": MENU_TOP_N, "top_share_pct": top_share_pct, "dishes_costed": dishes_costed,
        # which table the dish costs came from — 'unavailable' means the window
        # predates sales_sku_daily and isn't whole calendar months
        "cost_basis": cost_src, "cost_from": daily_min,
    }
    return {"clean": clean, "cmp_ok": cmp_ok, "cost_available": cost_available, "cost_src": cost_src, "cost_up": cost_up, "daily_min": daily_min, "food_sub": food_sub, "menu_out": menu_out, "prev_cost_available": prev_cost_available, "sp": sp, "sw": sw, "tot_cur_cost": tot_cur_cost, "tot_prev_cost": tot_prev_cost, "tot_yoy_cost": tot_yoy_cost}


def _mgt_tier_rows(*, TC, TP, has_prev, pax_c, pax_p, pct, th):
    # names this section hands on; None where a path never assigns one
    rows_t = None
    tier_out = None
    TLBL = ({"promo": "โปรฯ/ราคาอื่น", "child": "เด็กล้วน/อื่นๆ"} if th else
            {"promo": "Promo/other price", "child": "Child-only/other"})
    rows_t = []
    for t in _PT_ORDER:
        vc0 = TC.get(t); vp = TP.get(t)
        # A tier with nobody on it in THIS period is not a finding,
        # it is a retired price. The menu changed on 2026-09-01, so a
        # September report was carrying all six old tiers as empty
        # rows purely because the comparison week still had guests on
        # them — six lines of "—" for prices that no longer exist.
        # Symmetric: an August report drops the new tiers the same way.
        if not vc0 or not (vc0.get("pax") or 0):
            continue
        vc = vc0 or {"bills": 0, "pax": 0.0, "cogs": 0.0, "rev": 0.0}
        pxc = vc["pax"]; pxp = (vp or {}).get("pax", 0.0)
        rows_t.append({
            "tier": t, "label": TLBL.get(t, f"฿{int(t):,}" if t.isdigit() else t),
            "pax": round(pxc), "pax_prev": round(pxp) if has_prev else None,
            "d_pax_pct": pct(pxc, pxp) if (has_prev and pxp) else None,
            "share": round(pxc / pax_c * 100, 1),
            "share_prev": round(pxp / pax_p * 100, 1) if pax_p else None,
            "rev_head": round(vc["rev"] / pxc, 2) if pxc else None,
            "cogs_head": round(vc["cogs"] / pxc, 2) if pxc else None,
            "cogs_head_prev": round(vp["cogs"] / pxp, 2) if (vp and pxp) else None,
            "gp_head": round((vc["rev"] - vc["cogs"]) / pxc, 2) if pxc else None,
            "food_pct": round(vc["cogs"] / vc["rev"] * 100, 1) if vc["rev"] else None,
        })
    # mix effect on rev/head: current tier mix priced at the comparison
    # period's per-tier rev/head, vs its blended rev/head — isolates
    # "customers moved tier" from "tiers themselves changed".
    mix_eff = None
    rev_p = sum(v["rev"] for v in TP.values())
    if pax_c and pax_p and rev_p:
        # Only tiers that existed in BOTH periods can carry the
        # comparison; the weights are re-based on those. When the menu
        # itself was replaced (2026-09-01) barely any tier overlaps, so
        # the weights summed to ~0.21 of the mix and the shortfall was
        # reported as a ฿-520/head "customers moved to cheaper tiers"
        # — an artefact of the missing 79%, not a shift in behaviour.
        # Below near-full coverage there is no mix story to tell.
        shared = [t for t in TC if TP.get(t, {}).get("pax", 0) > 0]
        cover = sum(TC[t]["pax"] for t in shared) / pax_c
        if cover >= 0.9:
            at_prev = sum((TC[t]["pax"] / pax_c) * (TP[t]["rev"] / TP[t]["pax"])
                          for t in shared)
            mix_eff = round(at_prev - rev_p / pax_p, 2)
    tier_out = {"rows": rows_t, "pax": round(pax_c),
                "pax_prev": round(pax_p) if has_prev else None,
                "mix_effect": mix_eff}
    return {"rows_t": rows_t, "tier_out": tier_out}


def _mgt_tier_promo(*, ce, con, cs, rows_t, t_bids, t_ph, tier_out):
    # WHY the promo bucket is what it is. It is the one row on the
    # table whose label explains nothing, and it is not small — so
    # break it into the three things it actually contains, and name
    # the branches behind the two that are not ordinary discounts.
    #   free   whole bill comped
    #   mixed  a free / half-price head riding along with paying ones,
    #          which drags the bill average between two tiers
    #   disc   one uniform price that is not a tier = a real discount
    try:
        pz_kind, pz_price, pz_branch = {}, {}, {}
        for ent, brn, nb3, px3 in con.execute(
                f"""SELECT d.ent, b.name, SUM(d.bills), SUM(d.pax)
                                FROM menu_fact_day d JOIN branches b ON b.branch_id = d.branch_id
                                WHERE d.day BETWEEN ? AND ? AND d.branch_id IN ({t_ph})
                                  AND d.ent LIKE 'pz:%'
                                GROUP BY d.ent, b.name""",
                [cs.isoformat(), ce.isoformat()] + t_bids):
            parts = str(ent).split(":")
            if len(parts) < 3:
                continue
            kind, unit = parts[1], parts[2]
            k = pz_kind.setdefault(kind, {"bills": 0, "pax": 0.0})
            k["bills"] += nb3 or 0; k["pax"] += px3 or 0.0
            if kind == "disc":
                q = pz_price.setdefault(unit, {"bills": 0, "pax": 0.0})
                q["bills"] += nb3 or 0; q["pax"] += px3 or 0.0
            else:
                bb = pz_branch.setdefault(kind, {}).setdefault(brn, {"bills": 0, "pax": 0.0})
                bb["bills"] += nb3 or 0; bb["pax"] += px3 or 0.0
        if pz_kind:
            # only tiers actually SOLD in this period: both menus'
            # price lists live in _PT_ORDER, so a September ฿395 would
            # otherwise be matched against the retired ฿350 tier and
            # reported as a 12.9% surcharge instead of a discount.
            std = [int(x["tier"]) for x in rows_t
                   if str(x["tier"]).isdigit() and (x["pax"] or 0) > 0]

            def _near(u):
                """nearest standard tier + what % of it this price is,
                            so a bare ฿710 reads as '10% off ฿789'."""
                if not std or not u:
                    return None, None
                n = min(std, key=lambda t: abs(u - t))
                return n, round(u / n * 100, 1)

            disc = []
            for unit, v in sorted(pz_price.items(), key=lambda kv: -kv[1]["pax"])[:6]:
                u = int(unit)
                n, pctof = _near(u)
                disc.append({"unit": u, "of_tier": n, "pct_of_tier": pctof,
                             "off_pct": round(100 - pctof, 1) if pctof else None,
                             "bills": v["bills"], "pax": round(v["pax"])})
            tier_out["promo"] = {
                "kinds": {k: {"bills": v["bills"], "pax": round(v["pax"])}
                          for k, v in pz_kind.items()},
                "discounts": disc,
                "branches": {k: [{"branch": bn, "bills": bv["bills"],
                                  "pax": round(bv["pax"])}
                                 for bn, bv in sorted(v.items(),
                                                      key=lambda kv: -kv[1]["pax"])[:5]]
                             for k, v in pz_branch.items()},
            }
    except Exception as e:
        print(f"[mgt] §11 promo breakdown unavailable: {e}")
    return {}


def _mgt_tier_trend(*, ce, con, cs, rows_t, t_bids, t_ph, tier_out):
    """§11's trend charts — the same monthly/weekly series the tab draws.

    The tier TABLE only ever shows one period, so a tier drifting month after
    month is invisible in it; the reader has to leave the report to see whether
    today's number is a blip or a trend. Same helper as /api/reports/price-tier,
    so the two cannot disagree about what a week is or where the menu changed.

    Best-effort: a report is still worth reading without its charts, so a failure
    here leaves §11 exactly as it was rather than taking the section down.
    """
    if not rows_t:
        return {}
    try:
        lo = con.execute("SELECT MIN(day) FROM menu_fact_total").fetchone()[0]
        if not lo:
            return {}
        month_from = _PT_NEW_MENU_FROM if cs.isoformat() >= _PT_NEW_MENU_FROM else lo
        tr = _pt_tier_trends(con, t_ph, t_bids, month_from=month_from, a_to=ce)
        tier_out["trend_month"] = tr["trend_month"]
        tier_out["trend_week"] = tr["trend_week"]
        tier_out["trend_week_meta"] = tr["trend_week_meta"]
    except Exception as e:
        print(f"[mgt] §11 trend unavailable: {e}")
    return {}


def _mgt_tier_drivers(*, TC, ce, con, cs, rows_t, t_bids, t_ph, tier_out):
    # cost drivers per mainstream tier: which ingredient groups /
    # single menus push each tier's cost/head up — turns the Food%
    # column into an actionable "because guests eat X". Computes a
    # payload for EVERY mainstream tier (share>=5%) so the UI can
    # switch/compare; `default_tier` = worst food%. Reads the
    # tier-dimensioned menu_fact_day (ent LIKE 'g:%' = ingredient
    # group, 's:%' = single menu). Optional: wrapped so a driver
    # failure never nukes the tier table above.
    try:
        # Every tier that actually sold gets a driver breakdown. The 5%
        # share floor used to sit here, which meant the small tiers —
        # often the most interesting ones, ฿1,299 included — simply had
        # no breakdown and no hint that one was missing. The floor now
        # only decides which tier opens by DEFAULT (below), so a 45-cover
        # tier can be inspected without being allowed to set the headline.
        cand = [r for r in rows_t if r["tier"].isdigit()
                and r["food_pct"] is not None and r["pax"]]
        if cand:
            lblmap = {e: (l or e) for e, l in con.execute(
                "SELECT ent, label FROM menu_fact_meta")}

            # 2026-10-29 (speed): one query per tier for both kinds, memoised - the reference tier's groups are the same
            # rows as its own build read; was 14 queries (0.5 s) for 5 tiers, now 5
            _drv_memo: dict = {}

            def _drv(kind_like, tier, pax):
                if not pax:
                    return {}
                if tier not in _drv_memo:
                    both = {"g:%": {}, "s:%": {}}
                    for ent, cg, q in con.execute(
                            f"""SELECT ent, SUM(ent_cogs), SUM(qty)
                                            FROM menu_fact_day
                                            WHERE day BETWEEN ? AND ? AND branch_id IN ({t_ph})
                                              AND tier=? AND (ent LIKE 'g:%' OR ent LIKE 's:%') GROUP BY ent""",
                            [cs.isoformat(), ce.isoformat()] + t_bids + [tier]):
                        both["g:%" if ent.startswith("g:") else "s:%"][ent] = ((cg or 0.0) / pax, (q or 0.0) / pax * 100)
                    _drv_memo[tier] = both
                return _drv_memo[tier][kind_like]

            def _build(frow):
                ft = frow["tier"]; ftpax = TC[ft]["pax"]
                fch = frow.get("cogs_head") or 0.0
                lower = [x for x in cand if int(x["tier"]) < int(ft)]
                ref = max(lower, key=lambda x: int(x["tier"])) if lower else None
                rt = ref["tier"] if ref else None
                rtpax = TC[rt]["pax"] if rt else 0.0
                gF = _drv("g:%", ft, ftpax)
                gR = _drv("g:%", rt, rtpax) if rt else {}
                sh = lambda ch: (round(ch / fch * 100, 1) if fch else None)
                grp_all = [{"label": lblmap.get(e, e), "cogs_head": round(v[0], 1),
                            "share_pct": sh(v[0]),
                            "vs_ref": (round(v[0] - gR.get(e, (0.0, 0.0))[0], 1) if rt else None)}
                           for e, v in sorted(gF.items(), key=lambda kv: -kv[1][0])[:24]
                           if v[0] >= 1]
                grp = grp_all[:6]          # the printed six; the screen may show groups_all (owner 2026-10-02)
                mF = _drv("s:%", ft, ftpax)
                ranked = sorted(mF.items(), key=lambda kv: -kv[1][0])
                mnu = [{"label": lblmap.get(e, e), "cogs_head": round(v[0], 1),
                        "share_pct": sh(v[0]), "qty_100": round(v[1], 1)}
                       for e, v in ranked[:6]
                       if v[0] >= 1]
                # Appendix B (2026-10-01, owner): the tier's Top 60 menus by cost/head
                top60 = [{"label": lblmap.get(e, e), "cogs_head": round(v[0], 2),
                          "share_pct": sh(v[0]), "qty_100": round(v[1], 1)}
                         for e, v in ranked[:60] if v[0] > 0]
                if not (grp or mnu):
                    return None
                return {"focus_tier": ft, "focus_label": frow["label"],
                        "focus_food_pct": frow["food_pct"],
                        "focus_cogs_head": round(fch, 1),
                        "ref_tier": rt, "ref_label": (ref["label"] if ref else None),
                        # carried so the reader can weigh the numbers: a
                        # cost/head drawn from 45 covers swings hard.
                        "focus_pax": round(frow["pax"] or 0),
                        "focus_share": frow["share"],
                        "groups": grp, "groups_all": grp_all, "menus": mnu, "menus_top60": top60}

            by_tier = {}
            for frow in sorted(cand, key=lambda r: int(r["tier"])):
                payload = _build(frow)
                if payload:
                    by_tier[frow["tier"]] = payload
            if by_tier:
                # The tier that OPENS is still picked from the mainstream
                # ones — the headline "biggest lever" should not be a tier
                # with a handful of covers and a noisy cost/head. Falls
                # back to the full set when no tier clears the floor.
                _pool = [r for r in cand
                         if r["tier"] in by_tier and (r["share"] or 0) >= 5] \
                        or [r for r in cand if r["tier"] in by_tier]
                worst = max(_pool, key=lambda r: r["food_pct"])
                tier_out["drivers"] = {
                    "default_tier": worst["tier"],
                    "tier_order": sorted(by_tier, key=lambda t: int(t)),
                    "by_tier": by_tier}
    except Exception as e:
        print(f"[mgt] §11 cost drivers unavailable: {e}")
    return {}


def _mgt_tier(con, *, ce, cs, has_prev, pct, pe, ps, sp, sw, th):
    # ---- buffet price-tier split (tier-dimensioned menu_fact_*; optional) ----
    # Same current-vs-comparison windows as the headline. None (section hidden)
    # when the fact tables are missing/old schema or the window predates their
    # coverage. pax here counts BUFFET bills only (menu_fact universe), so the
    # total can sit slightly under the headline covers.
    if has_prev and cs.isoformat() >= _PT_NEW_MENU_FROM > pe.isoformat():
        # The comparison period predates the Grand Menu switch-over while the
        # current one is on/after it — a different menu, not a baseline (both
        # eras even share the "999" label for two different products). Drop
        # the comparison rather than show a delta that isn't the same dish.
        has_prev = False
    tier_out = None
    try:
        if con.execute("SELECT 1 FROM pragma_table_info('menu_fact_total') "
                       "WHERE name='tier'").fetchone():
            t_bids = [r[0] for r in con.execute(
                f"SELECT b.branch_id FROM branches b WHERE {sw}", sp)]
            t_ph = ",".join("?" * len(t_bids))

            def tier_sum(s, e):
                out = {}
                for t, nb2, px, cg, rv in con.execute(
                        f"""SELECT tier, SUM(bills), SUM(pax), SUM(cogs), SUM(rev)
                            FROM menu_fact_total
                            WHERE day BETWEEN ? AND ? AND branch_id IN ({t_ph})
                            GROUP BY tier""",
                        [s.isoformat(), e.isoformat()] + t_bids):
                    out[str(t or "promo")] = {"bills": nb2 or 0, "pax": px or 0.0,
                                              "cogs": cg or 0.0, "rev": rv or 0.0}
                return out
            TC = tier_sum(cs, ce)
            TP = tier_sum(ps, pe) if has_prev else {}
            pax_c = sum(v["pax"] for v in TC.values())
            pax_p = sum(v["pax"] for v in TP.values())
            if pax_c > 0:
                # Each stage is its own function (_mgt_tier_*, Phase 8b 2026-09); they run in
                # order and hand what they compute forward through `ctx`. The statements
                # are the ones this block used to hold inline; the goldens pin the output.
                ctx = {"TC": TC, "TP": TP, "ce": ce, "con": con, "cs": cs, "has_prev": has_prev, "pax_c": pax_c, "pax_p": pax_p, "pct": pct, "t_bids": t_bids, "t_ph": t_ph, "th": th, "tier_out": tier_out}
                ctx.update(_mgt_tier_rows(TC=ctx.get("TC"), TP=ctx.get("TP"), has_prev=ctx.get("has_prev"), pax_c=ctx.get("pax_c"), pax_p=ctx.get("pax_p"), pct=ctx.get("pct"), th=ctx.get("th")))
                ctx.update(_mgt_tier_promo(ce=ctx.get("ce"), con=ctx.get("con"), cs=ctx.get("cs"), rows_t=ctx.get("rows_t"), t_bids=ctx.get("t_bids"), t_ph=ctx.get("t_ph"), tier_out=ctx.get("tier_out")))
                ctx.update(_mgt_tier_drivers(TC=ctx.get("TC"), ce=ctx.get("ce"), con=ctx.get("con"), cs=ctx.get("cs"), rows_t=ctx.get("rows_t"), t_bids=ctx.get("t_bids"), t_ph=ctx.get("t_ph"), tier_out=ctx.get("tier_out")))
                ctx.update(_mgt_tier_trend(ce=ctx.get("ce"), con=ctx.get("con"), cs=ctx.get("cs"), rows_t=ctx.get("rows_t"), t_bids=ctx.get("t_bids"), t_ph=ctx.get("t_ph"), tier_out=ctx.get("tier_out")))
                tier_out = ctx["tier_out"]
    except Exception:
        tier_out = None
    return {"tier_out": tier_out}


def _gp_bridge(c, p, cost_c, cost_p):
    """Decompose GP(c) − GP(p) into three exact, additive effects (price/volume/cost
    variance — the standard management-accounting bridge):

        volume     = (covers_c − covers_p) × GP/head_p      more or fewer guests at last period's margin
        spend_head = (spend/head_c − spend/head_p) × covers_c   what the guests of THIS period paid, per head
        cost_head  = −(cost/head_c − cost/head_p) × covers_c    what feeding them cost, per head

    volume + spend_head + cost_head == GP_c − GP_p to the baht (no residual), so
    the printed waterfall always closes. None when either side has no covers or
    no cost figure — a ฿0 cost would make a missing cost table look like a saving."""
    if not (c and p) or cost_c is None or cost_p is None:
        return None
    n_c, n_p = c.get("customers") or 0, p.get("customers") or 0
    if not (n_c and n_p):
        return None
    rev_c, rev_p = c.get("revenue") or 0, p.get("revenue") or 0
    s_c, s_p = rev_c / n_c, rev_p / n_p
    k_c, k_p = cost_c / n_c, cost_p / n_p
    g_c, g_p = s_c - k_c, s_p - k_p
    gp_c, gp_p = rev_c - cost_c, rev_p - cost_p
    return {
        "gp_prev": round(gp_p), "gp_cur": round(gp_c), "delta": round(gp_c - gp_p),
        "volume": round((n_c - n_p) * g_p),
        "spend_head": round((s_c - s_p) * n_c),
        # the residual of the rounding lands here so the three printed bars
        # always sum to the printed delta (the exact value is -(k_c - k_p) * n_c)
        "cost_head": round(gp_c - gp_p) - round((n_c - n_p) * g_p) - round((s_c - s_p) * n_c),
        "covers_prev": n_p, "covers_cur": n_c,
        "spend_head_prev": round(s_p, 1), "spend_head_cur": round(s_c, 1),
        "cost_head_prev": round(k_p, 1), "cost_head_cur": round(k_c, 1),
        "gp_head_prev": round(g_p, 1), "gp_head_cur": round(g_c, 1),
        "gp_pct_prev": round(gp_p / rev_p * 100, 1) if rev_p else None,
        "gp_pct_cur": round(gp_c / rev_c * 100, 1) if rev_c else None,
    }


def _mgt_gp(con, *, cmp_ok, cost_available, cost_src, cur, daily_min, headline, prev, prev_cost_available, tot_cur_cost, tot_prev_cost, tot_yoy_cost):
    # names this section hands on; None where a period path never assigns one
    gp = None
    # ---- gross profit for THIS period (revenue − period food COGS, BoM basis) ----
    # With no cost source for the window, GP is UNKNOWN — never revenue−0, which
    # rendered as a triumphant "100% margin, ฿0 food cost" on pre-2026-03-22
    # monthly reports. Emit nulls + cost_basis so the UI prints "—" and says why.
    if not cost_available:
        gp = {"revenue": cur["revenue"], "food_cost": None, "gp": None, "gp_pct": None,
              "gp_per_head": None, "gp_pct_prev": None, "gp_pct_delta_pp": None,
              "cost_basis": "unavailable", "cost_from": daily_min}
    else:
        gp_cur = round(cur["revenue"] - tot_cur_cost, 2)
        gp_pct = round(gp_cur / cur["revenue"] * 100, 1) if cur["revenue"] else None
        gp_prev_pct = (round((prev["revenue"] - tot_prev_cost) / prev["revenue"] * 100, 1)
                       if (cmp_ok and prev and prev["revenue"] and prev_cost_available) else None)
        gp = {
            "revenue": cur["revenue"], "food_cost": round(tot_cur_cost),
            "gp": gp_cur, "gp_pct": gp_pct,
            "gp_per_head": round(gp_cur / cur["customers"], 1) if cur["customers"] else None,
            # DV1 (phone table review 2026-10-25, §5 no. 3): THE food cost per head = the cost GP subtracts / covers - the
            # bridge's cost_head and food cost % use the same cost, so the phone tile no longer shows the food-dish-only
            # menu.total_cph (336) beside the bridge's 383
            "cost_per_head": round(tot_cur_cost / cur["customers"], 1) if cur["customers"] else None,
            "cost_per_head_prev": (round(tot_prev_cost / prev["customers"], 1)
                                   if (cmp_ok and prev and prev["customers"] and prev_cost_available) else None),
            "gp_pct_prev": gp_prev_pct,
            "gp_pct_delta_pp": round(gp_pct - gp_prev_pct, 1) if (gp_pct is not None and gp_prev_pct is not None) else None,
            "cost_basis": cost_src,
            # §4 GP bridge (owner request 2026-10-01): vs the comparison period and,
            # when the year-ago window has cost data (monthly), vs last year
            "bridge": _gp_bridge(cur, prev, tot_cur_cost, tot_prev_cost) if (cmp_ok and prev_cost_available) else None,
            "bridge_yoy": _gp_bridge(cur, (headline or {}).get("yoy"), tot_cur_cost, tot_yoy_cost),
        }
    return {"gp": gp}


def _mgt_pace(con, *, _cal, branch, clean, food_sub, group, latest_day, maxd, params, ref_anchor, where):
    # names this section hands on; None where a period path never assigns one
    cur_month = None
    days_elapsed = None
    days_total = None
    food_cost_pct = None
    month_complete = None
    mp = None
    mtd_rev = None
    mw = None
    pace = None
    projected = None
    target = None
    # ---- food cost % (BoM) + month pace, anchored on the period's month ----
    cur_month = ref_anchor.strftime("%Y-%m")
    # §3 covers the month UP TO THIS REPORT'S DATE, not up to whatever the
    # sync last loaded. The daily report deliberately anchors on the last
    # COMPLETE day, so while today is still trading the month holds one day
    # more than the report describes — a report dated the 3rd was counting
    # "4 days so far" and averaging three full days plus a sliver over four.
    # That understated the daily run rate, the month projection built on it,
    # and the weekday-fair MoM baseline, which is sized by days_elapsed.
    # For a past month ref_anchor is the month end, so this bounds nothing.
    mtd_cut = ref_anchor.isoformat()
    # True when the month rollup would reach past the report: the COGS below
    # must then come from the day-granular source, or food-cost % divides a
    # 4-day cost by 3 days of revenue.
    mtd_partial = (cur_month == maxd[:7] and ref_anchor < latest_day)
    mb = ["b.name!='ALL'", "s.period_month=:month"]; mp: dict = {"month": cur_month}
    scope_filter(mb, mp, branch, group)
    mw = " AND ".join(mb)
    bom_row = None if mtd_partial else con.execute(f"""
        SELECT ROUND(SUM({_cogs_case('s','sk','c')}),2) cost
        FROM v_sales_sku_monthly s JOIN skus sk ON sk.sku_id=s.sku_id
        LEFT JOIN sku_cost c ON c.sku_id=s.sku_id
        JOIN branches b ON b.branch_id=s.branch_id
        WHERE {mw}""", mp).fetchone()
    # sales_sku_monthly is not populated for the in-progress (live-synced-only)
    # month, so the month food-cost % would go blank for a current-month report.
    # Fall back to sales_sku_daily (same tier-safe COGS expr; a wrapped subquery
    # supplies the `source` column the expr expects). [[sync_no_month_summary]]
    if not (bom_row and bom_row["cost"]):
        mw_daily = (mw.replace("s.period_month=:month",
                               "strftime('%Y-%m', s.day)=:month")
                    + " AND s.day <= :mtd_cut")
        bom_row = con.execute(f"""
            SELECT ROUND(SUM({_cogs_case('s','sk','c')}),2) cost
            FROM (SELECT *, 'Easy' AS source, substr(day, 1, 7) AS period_month FROM v_sales_sku_daily) s
            JOIN skus sk ON sk.sku_id=s.sku_id
            LEFT JOIN sku_cost c ON c.sku_id=s.sku_id
            JOIN branches b ON b.branch_id=s.branch_id
            WHERE {mw_daily}""", {**mp, "mtd_cut": mtd_cut}).fetchone()
    mtd_row = con.execute(f"""
        SELECT ROUND({net_revenue_sum('st')},2) rev,
               COUNT(DISTINCT date(st.sold_at))    days
        FROM sales_transaction st JOIN branches b ON b.branch_id=st.branch_id
        WHERE {where} AND strftime('%Y-%m', st.sold_at)=:month
          AND date(st.sold_at) <= :mtd_cut""",
        {**params, "month": cur_month, "mtd_cut": mtd_cut}).fetchone()
    mtd_rev  = mtd_row["rev"] or 0
    mtd_days = mtd_row["days"] or 0
    bom_cost = bom_row["cost"] if bom_row else None
    food_cost_pct = round(bom_cost/mtd_rev*100, 1) if bom_cost and mtd_rev else None

    # ---- cost-data COVERAGE for that month (why food-cost% is a FLOOR) ------
    # A dish with no unit cost contributes ฿0 COGS, so the headline food-cost %
    # is understated by exactly the dishes we haven't costed yet — silently.
    # Coverage must be weighted by PLATES, not revenue: buffet dishes carry ~฿0
    # line revenue by design, so a revenue-weighted number says "fine" while a
    # tenth of the plates served are free-of-charge in the P&L.
    # `est_food_cost_pct` prices the uncosted plates at the average cost of the
    # plates we DO cost — a first-order estimate, flagged as such, never booked.
    # same effective-cost expression, aliased for sales_sku_monthly (`s.`)
    _eff_cost_m = effective_unit_cost_sql('s', 's.period_month')
    cost_coverage = None
    try:
        # the app-wide coverage scope (rules.needs_cost_sql — what home, P&L and the
        # coverage tab count), and the PLATE-weighted average cost of the costed
        # plates. Until 2026-10-01 this counted dine-in food only (uncosted drinks,
        # desserts and delivery never estimated) and took a plain average of unit
        # costs across SKUs, so its estimate disagreed with the home page's.
        cc = con.execute(f"""
            SELECT ROUND(SUM(s.qty)) plates,
                   ROUND(SUM(CASE WHEN {_eff_cost_m} > 0 THEN s.qty ELSE 0 END)) plates_costed,
                   ROUND(SUM(CASE WHEN {_eff_cost_m} > 0 THEN s.qty * {_eff_cost_m} ELSE 0 END)
                         / NULLIF(SUM(CASE WHEN {_eff_cost_m} > 0 THEN s.qty ELSE 0 END), 0), 2) avg_unit_cost,
                   COUNT(DISTINCT s.sku_id) skus,
                   COUNT(DISTINCT CASE WHEN {_eff_cost_m} = 0 THEN s.sku_id END) skus_uncosted
            FROM v_sales_sku_monthly s JOIN skus sk ON sk.sku_id=s.sku_id
            JOIN branches b ON b.branch_id=s.branch_id
            WHERE {mw} AND s.qty > 0 AND {needs_cost_sql()}""", mp).fetchone()
        plates = (cc["plates"] or 0) if cc else 0
        if plates:
            costed = cc["plates_costed"] or 0
            gap    = plates - costed
            avg_uc = cc["avg_unit_cost"] or 0
            est_missing = round(gap * avg_uc)
            cost_coverage = {
                "plates": plates, "plates_costed": costed,
                "coverage_pct": round(costed / plates * 100, 1),
                "uncosted_plates": gap,
                "skus": cc["skus"], "skus_uncosted": cc["skus_uncosted"],
                "est_missing_cost": est_missing,
                "est_food_cost_pct": (round((bom_cost + est_missing) / mtd_rev * 100, 1)
                                      if (bom_cost and mtd_rev) else None),
            }
            # worklist: the biggest uncosted dishes BY PLATES (revenue ranks
            # buffet dishes at ~0, which is why they were never chased)
            cost_coverage["top_uncosted"] = [
                {"menu": clean(r["description"] or r["sku_code"]),
                 "sku_code": r["sku_code"], "plates": round(r["qty"] or 0)}
                for r in con.execute(f"""
                    SELECT sk.sku_code, sk.description, SUM(s.qty) qty
                    FROM v_sales_sku_monthly s JOIN skus sk ON sk.sku_id=s.sku_id
                    JOIN branches b ON b.branch_id=s.branch_id
                    WHERE {mw} AND s.qty > 0 AND {needs_cost_sql()} AND {_eff_cost_m} = 0
                    GROUP BY sk.sku_id ORDER BY qty DESC LIMIT 8""", mp).fetchall()]
    except Exception:
        cost_coverage = None

    days_total = _cal.monthrange(ref_anchor.year, ref_anchor.month)[1]
    # MTD up to the latest data for the current month; a full (past) month otherwise.
    # Not latest_day.day: that is the last day with DATA, which on a daily
    # report is the still-open day AFTER the one being reported.
    days_elapsed = (min(ref_anchor.day, latest_day.day)
                    if cur_month == maxd[:7] else days_total)
    month_complete = days_elapsed >= days_total
    projected = round(mtd_rev/days_elapsed*days_total, 2) if (mtd_rev and days_elapsed) else None
    # typed targets, else each branch's actual of the same month last year (targets.py)
    target = target_revenue_total(con, cur_month, branch, group)
    pace = {
        "month": cur_month, "mtd_revenue": mtd_rev, "mtd_days": mtd_days,
        "days_elapsed": days_elapsed, "days_total": days_total, "month_complete": month_complete,
        "projected": projected, "target": target or None,
        "proj_vs_target_pct": round(projected/target*100, 1) if (projected and target) else None,
        "food_cost_pct": food_cost_pct, "bom_cost_mtd": bom_cost,
        "cost_coverage": cost_coverage,
    }
    return {"cur_month": cur_month, "days_elapsed": days_elapsed, "days_total": days_total, "food_cost_pct": food_cost_pct, "month_complete": month_complete, "mp": mp, "mtd_rev": mtd_rev, "mw": mw, "pace": pace, "projected": projected, "target": target}


def _mgt_mom(con, *, MON, _dt, cur_month, days_elapsed, days_total, latest_day, month_complete, mp, mtd_rev, mw, pace, params, pct, ref_anchor, target, where):
    # names this section hands on; None where a period path never assigns one
    projected = None
    # `prev_month`: previous calendar month — full total, the SAME-elapsed-days MTD
    # (fair MTD-vs-MTD when the current month is partial) and its BoM food-cost %.
    pm_first   = ref_anchor.replace(day=1) - _dt.timedelta(days=1)
    prev_month = pm_first.strftime("%Y-%m")
    pm_full = con.execute(f"""
        SELECT ROUND({net_revenue_sum('st')},2) rev
        FROM sales_transaction st JOIN branches b ON b.branch_id=st.branch_id
        WHERE {where} AND strftime('%Y-%m', st.sold_at)=:month""",
        {**params, "month": prev_month}).fetchone()
    pm_mtd = con.execute(f"""
        SELECT ROUND({net_revenue_sum('st')},2) rev
        FROM sales_transaction st JOIN branches b ON b.branch_id=st.branch_id
        WHERE {where} AND strftime('%Y-%m', st.sold_at)=:month
          AND CAST(strftime('%d', st.sold_at) AS INT) <= :d""",
        {**params, "month": prev_month, "d": days_elapsed}).fetchone()
    pm_bom = con.execute(f"""
        SELECT ROUND(SUM({_cogs_case('s','sk','c')}),2) cost
        FROM v_sales_sku_monthly s JOIN skus sk ON sk.sku_id=s.sku_id
        LEFT JOIN sku_cost c ON c.sku_id=s.sku_id
        JOIN branches b ON b.branch_id=s.branch_id
        WHERE {mw}""", {**mp, "month": prev_month}).fetchone()
    pm_rev_full = (pm_full["rev"] or 0) if pm_full else 0
    pm_rev_mtd  = (pm_mtd["rev"] or 0) if pm_mtd else 0
    pm_cost     = pm_bom["cost"] if pm_bom else None
    # Per-weekday average revenue of the previous month. A raw same-elapsed-days
    # MoM misleads early in the month (1-2 Jul = Wed+Thu vs 1-2 Jun = holiday
    # Mon+Tue read as "-44%"), and the flat mtd/days*days_total projection
    # undershoots until the first weekend is in — so while the month is partial,
    # both the MoM baseline and the projection are weighted by weekday instead.
    # DV1: the one projection rule (rules.month_projection) - /api/forecast reads the same
    _mproj = month_projection(con, where, params, ref_anchor.replace(day=1), days_elapsed, days_total, mtd_rev)
    dow_avg, dow_fallback = _mproj["dow_avg"], _mproj["dow_fallback"]

    # ---- §3 weekly bars (this month) + last-month comparison --------------
    # `weekly`: ex-VAT revenue per Mon–Sun week WITHIN the period's month (each bar =
    # that week's in-month revenue), for a small weekly trend bar in §3. Each bar's
    # comparison (`fair_prev`) is last month's weekday-matched total for the EXACT
    # calendar days summed into that bar — not a flat full-week average — so an
    # in-progress week (e.g. only Wed–Sun so far) is never judged against a 7-day norm.
    mon_e3 = "date(st.sold_at, '-' || ((CAST(strftime('%w', st.sold_at) AS INT)+6)%7) || ' days')"
    wk_rows = con.execute(f"""
        SELECT {mon_e3} k, ROUND({net_revenue_sum('st')}) rev
        FROM sales_transaction st JOIN branches b ON b.branch_id=st.branch_id
        WHERE {where} AND strftime('%Y-%m', st.sold_at)=:month
        GROUP BY k ORDER BY k""", {**params, "month": cur_month}).fetchall()
    month_first = ref_anchor.replace(day=1)
    month_last  = ref_anchor.replace(day=days_total)
    pace["weekly"] = []
    for r in wk_rows:
        wk_start = _dt.date.fromisoformat(r["k"])
        d0 = max(wk_start, month_first)
        d1 = min(wk_start + _dt.timedelta(days=6), month_last, latest_day)
        n_days = max(0, (d1 - d0).days + 1)
        fair_prev = sum(dow_avg.get(int((d0 + _dt.timedelta(days=i)).strftime("%w")), dow_fallback)
                        for i in range(n_days))
        pace["weekly"].append({
            "label": f"{wk_start.day} {MON[wk_start.month-1]}",
            "start": r["k"], "revenue": r["rev"] or 0,
            "fair_prev": round(fair_prev, 2), "days": n_days,
            "complete": n_days >= 7,
        })
    mom_baseline, mom_basis = pm_rev_mtd, "same_days"
    if not month_complete and dow_avg and days_elapsed:
        _wsum = lambda n: sum(
            dow_avg.get(int((month_first + _dt.timedelta(days=i)).strftime("%w")), dow_fallback)
            for i in range(n))
        w_elapsed = _wsum(days_elapsed)
        mom_baseline, mom_basis = round(w_elapsed, 2), "dow"
        if mtd_rev and w_elapsed and _mproj["method"] == "dow":
            pace["projected"] = projected = _mproj["projected"]
            pace["proj_vs_target_pct"] = round(projected/target*100, 1) if target else None
            pace["proj_method"] = "dow"
    pace["prev_month"] = {
        "month": prev_month, "revenue_full": pm_rev_full, "revenue_mtd": pm_rev_mtd,
        "baseline_mtd": mom_baseline,
        "food_cost_pct": round(pm_cost/pm_rev_full*100, 1) if (pm_cost and pm_rev_full) else None,
    }
    pace["mom_pct"]   = pct(mtd_rev, mom_baseline)  # MTD vs weekday-fair baseline (same-elapsed days once month is complete)
    pace["mom_basis"] = mom_basis
    pace["food_cost_pct_prev"] = pace["prev_month"]["food_cost_pct"]
    return {"projected": projected}


def _mgt_trend(con, *, MON, _dt, ce, cs, dlabel, params, period, where):
    # names this section hands on; None where a period path never assigns one
    peak = None
    trend = None
    # ---- trailing trend (8 periods) — ONE grouped query, not 8 round-trips ----
    trend = []
    if period == "daily":
        start = cs - _dt.timedelta(days=7)
        rows = con.execute(f"""
            SELECT date(st.sold_at) k, ROUND({net_revenue_sum('st')},2) rev
            FROM sales_transaction st JOIN branches b ON b.branch_id=st.branch_id
            WHERE {where} AND date(st.sold_at) BETWEEN :s AND :e
            GROUP BY k""", {**params, "s": start.isoformat(), "e": cs.isoformat()}).fetchall()
        rev_by = {r["k"]: r["rev"] or 0 for r in rows}
        for i in range(7, -1, -1):
            d = cs - _dt.timedelta(days=i)
            trend.append({"label": dlabel(d), "start": d.isoformat(),
                          "revenue": rev_by.get(d.isoformat(), 0)})
    elif period in ("monthly", "yearly"):
        mlist = []                       # 8 calendar months ending at the selected one (yearly: at its last month)
        _m0 = ce if period == "yearly" else cs
        for i in range(7, -1, -1):
            mi = (_m0.month - 1 - i) % 12 + 1
            yi = _m0.year + (_m0.month - 1 - i - (mi - 1)) // 12
            mlist.append((yi, mi))
        start = _dt.date(mlist[0][0], mlist[0][1], 1)
        rows = con.execute(f"""
            SELECT strftime('%Y-%m', st.sold_at) k, ROUND({net_revenue_sum('st')},2) rev
            FROM sales_transaction st JOIN branches b ON b.branch_id=st.branch_id
            WHERE {where} AND date(st.sold_at) BETWEEN :s AND :e
            GROUP BY k""", {**params, "s": start.isoformat(), "e": ce.isoformat()}).fetchall()
        rev_by = {r["k"]: r["rev"] or 0 for r in rows}
        for yi, mi in mlist:
            k = f"{yi:04d}-{mi:02d}"
            trend.append({"label": f"{MON[mi-1]} {yi % 100:02d}", "start": f"{k}-01",
                          "revenue": rev_by.get(k, 0)})
    else:
        start = cs - _dt.timedelta(days=49)      # 8 full Mon–Sun weeks incl. the current one
        mon_e = "date(st.sold_at, '-' || ((CAST(strftime('%w', st.sold_at) AS INT)+6)%7) || ' days')"
        rows = con.execute(f"""
            SELECT {mon_e} k, ROUND({net_revenue_sum('st')},2) rev
            FROM sales_transaction st JOIN branches b ON b.branch_id=st.branch_id
            WHERE {where} AND date(st.sold_at) BETWEEN :s AND :e
            GROUP BY k""", {**params, "s": start.isoformat(), "e": ce.isoformat()}).fetchall()
        rev_by = {r["k"]: r["rev"] or 0 for r in rows}
        for i in range(7, -1, -1):
            d = cs - _dt.timedelta(days=7 * i)
            sun = d + _dt.timedelta(days=6)   # label with week-end Sunday (matches report header)
            trend.append({"label": f"{sun.day} {MON[sun.month-1]}", "start": d.isoformat(),
                          "revenue": rev_by.get(d.isoformat(), 0)})

    # ---- peak hours & dayparts (revenue + covers by hour over the period) ----
    hr_rows = con.execute(f"""
        SELECT CAST(strftime('%H', st.sold_at) AS INT) hr,
               ROUND({net_revenue_sum('st')},2) rev,
               COALESCE(SUM(st.customer_count),0) cust, COUNT(*) bills
        FROM sales_transaction st JOIN branches b ON b.branch_id=st.branch_id
        WHERE {where} AND date(st.sold_at) BETWEEN :s AND :e
        GROUP BY hr ORDER BY hr""", {**params, "s": cs.isoformat(), "e": ce.isoformat()}).fetchall()
    hourly = [{"hour": r["hr"], "revenue": r["rev"] or 0, "covers": r["cust"] or 0, "bills": r["bills"]} for r in hr_rows]
    def _daypart(hh):
        if 11 <= hh <= 14: return "Lunch"
        if 17 <= hh <= 22: return "Dinner"
        return "Other"
    dp: dict = {}
    for r in hourly:
        d2 = dp.setdefault(_daypart(r["hour"]), {"name": _daypart(r["hour"]), "revenue": 0, "covers": 0})
        d2["revenue"] += r["revenue"]; d2["covers"] += r["covers"]
    tot_hr_rev = sum(r["revenue"] for r in hourly) or 0
    dayparts = []
    for k in ("Lunch", "Dinner", "Other"):
        if k in dp:
            d2 = dp[k]
            d2["pct"] = round(d2["revenue"]/tot_hr_rev*100, 1) if tot_hr_rev else None
            d2["revenue"] = round(d2["revenue"]); dayparts.append(d2)
    ph = max(hourly, key=lambda r: r["revenue"], default=None)
    peak = {"hourly": hourly, "dayparts": dayparts, "total_rev": round(tot_hr_rev),
            "peak_hour": ({"hour": ph["hour"], "revenue": round(ph["revenue"])} if ph and ph["revenue"] else None)}
    return {"_daypart": _daypart, "peak": peak, "trend": trend}


def _mgt_table(con, *, _daypart, branch, ce, cs, group, has_prev, params, pe, peak, period, ps, where):
    # names this section hands on; None where a period path never assigns one
    table = None
    # last-period comparison — daypart revenue, scope total and the peak hour, so §8
    # can show a WoW delta against the same weekday last week / the previous week.
    if has_prev:
        ph_rows = con.execute(f"""
            SELECT CAST(strftime('%H', st.sold_at) AS INT) hr,
                   ROUND({net_revenue_sum('st')},2) rev
            FROM sales_transaction st JOIN branches b ON b.branch_id=st.branch_id
            WHERE {where} AND date(st.sold_at) BETWEEN :s AND :e
            GROUP BY hr""", {**params, "s": ps.isoformat(), "e": pe.isoformat()}).fetchall()
        pdp: dict = {}
        for r in ph_rows:
            nm = _daypart(r["hr"]); pdp[nm] = pdp.get(nm, 0) + (r["rev"] or 0)
        pphr = max(ph_rows, key=lambda r: r["rev"] or 0, default=None)
        peak["prev"] = {
            "dayparts":  {k: round(v) for k, v in pdp.items()},
            "total_rev": round(sum((r["rev"] or 0) for r in ph_rows)),
            "peak_hour": ({"hour": pphr["hr"], "revenue": round(pphr["rev"])} if pphr and pphr["rev"] else None),
        }
    if period != "daily":      # which day of the week drove the week
        dow_rows = con.execute(f"""
            SELECT CAST(strftime('%w', st.sold_at) AS INT) w,
                   ROUND({net_revenue_sum('st')},2) rev, COALESCE(SUM(st.customer_count),0) cust,
                   COUNT(DISTINCT date(st.sold_at)) days
            FROM sales_transaction st JOIN branches b ON b.branch_id=st.branch_id
            WHERE {where} AND date(st.sold_at) BETWEEN :s AND :e
            GROUP BY w""", {**params, "s": cs.isoformat(), "e": ce.isoformat()}).fetchall()
        dm = {r["w"]: r for r in dow_rows}
        by_day = []
        for i, nm in enumerate(["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]):
            r = dm.get((i + 1) % 7)      # %w: 0=Sun..6=Sat → Mon-first
            days = (r["days"] or 0) if r else 0
            # per trading day, like วิเคราะห์ตามวัน (/api/analysis/dow): a month holds
            # four or five of each weekday, and totals made the five-times days look
            # ~25% bigger (Sep 2026: Tue/Wed totals over Mon/Thu, per day all equal)
            by_day.append({"dow": nm, "revenue": round(r["rev"] or 0) if r else 0,
                           "covers": (r["cust"] or 0) if r else 0, "days": days,
                           "avg_revenue": round((r["rev"] or 0) / days) if days else 0,
                           "avg_covers": round((r["cust"] or 0) / days, 1) if days else 0})
        bd = max(by_day, key=lambda d: d["avg_revenue"], default=None)
        peak["by_day"] = by_day
        peak["best_day"] = bd["dow"] if bd and bd["avg_revenue"] else None
        # PR-PD (owner 2026-10-29, no. 5) · the weekday that fell: each weekday's revenue per trading day against the same
        # weekday of the comparison period; weak_day = the largest fall of 10 % or more (two or more such days each side),
        # the one a weekday promotion or a roster change answers - not the weakest weekday, which is always a Monday
        if has_prev:
            pdw = con.execute(f"""
                SELECT CAST(strftime('%w', st.sold_at) AS INT) w, ROUND({net_revenue_sum('st')},2) rev,
                       COUNT(DISTINCT date(st.sold_at)) days
                FROM sales_transaction st JOIN branches b ON b.branch_id=st.branch_id
                WHERE {where} AND date(st.sold_at) BETWEEN :s AND :e
                GROUP BY w""", {**params, "s": ps.isoformat(), "e": pe.isoformat()}).fetchall()
            pm = {r["w"]: r for r in pdw}
            weak = None
            for i, d in enumerate(by_day):
                r = pm.get((i + 1) % 7)
                pdays = (r["days"] or 0) if r else 0
                d["prev_avg_revenue"] = round((r["rev"] or 0) / pdays) if pdays else None
                d["delta_pct"] = (round((d["avg_revenue"] / d["prev_avg_revenue"] - 1) * 100, 1)
                                  if d["prev_avg_revenue"] and d["days"] >= 2 and pdays >= 2 else None)
                if d["delta_pct"] is not None and d["delta_pct"] <= -10 and (weak is None or d["delta_pct"] < weak["delta_pct"]):
                    weak = {"dow": d["dow"], "delta_pct": d["delta_pct"], "avg_revenue": d["avg_revenue"], "prev_avg_revenue": d["prev_avg_revenue"]}
            peak["weak_day"] = weak

    # ---- table turnover & occupancy (pos_table_turn_day, day-grain) ----
    table = {"available": False}
    try:
        if con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='pos_table_turn_day'").fetchone():
            tt_base = ["b.name!='ALL'"]; tparams: dict = {"s": cs.isoformat(), "e": ce.isoformat()}
            scope_filter(tt_base, tparams, branch, group)
            tw2 = " AND ".join(tt_base)
            tot = con.execute(f"""
                SELECT COALESCE(SUM(t.tabs),0) tabs, COALESCE(SUM(t.customers),0) cust,
                       ROUND(AVG(NULLIF(t.avg_turn_min,0))) avg_turn
                FROM pos_table_turn_day t JOIN branches b ON b.branch_id=t.branch_id
                WHERE {tw2} AND t.day BETWEEN :s AND :e""", tparams).fetchone()
            brows = con.execute(f"""
                SELECT b.name branch, COALESCE(SUM(t.tabs),0) tabs, COALESCE(SUM(t.customers),0) cust,
                       ROUND(AVG(NULLIF(t.avg_turn_min,0))) avg_turn
                FROM pos_table_turn_day t JOIN branches b ON b.branch_id=t.branch_id
                WHERE {tw2} AND t.day BETWEEN :s AND :e
                GROUP BY b.name HAVING SUM(t.tabs)>0
                ORDER BY SUM(t.customers)*1.0/SUM(t.tabs) DESC""", tparams).fetchall()
            tabs = tot["tabs"] or 0; tcust = tot["cust"] or 0
            if tabs:
                table = {
                    "available": True, "tables": tabs, "covers": tcust,
                    "covers_per_table": round(tcust/tabs, 2), "avg_turn_min": tot["avg_turn"],
                    "by_branch": [{"branch": r["branch"], "tables": r["tabs"], "covers": r["cust"],
                                   "covers_per_table": round((r["cust"] or 0)/r["tabs"], 2) if r["tabs"] else None,
                                   "avg_turn_min": r["avg_turn"]} for r in brows][:12],
                }
                if has_prev:      # last-period turnover for the §9 WoW deltas
                    pt = con.execute(f"""
                        SELECT COALESCE(SUM(t.tabs),0) tabs, COALESCE(SUM(t.customers),0) cust,
                               ROUND(AVG(NULLIF(t.avg_turn_min,0))) avg_turn
                        FROM pos_table_turn_day t JOIN branches b ON b.branch_id=t.branch_id
                        WHERE {tw2} AND t.day BETWEEN :ps AND :pe""",
                        {**tparams, "ps": ps.isoformat(), "pe": pe.isoformat()}).fetchone()
                    ptabs = pt["tabs"] or 0; pcust = pt["cust"] or 0
                    table["prev"] = {
                        "tables": ptabs, "covers": pcust,
                        "covers_per_table": round(pcust/ptabs, 2) if ptabs else None,
                        "avg_turn_min": pt["avg_turn"],
                    }
    except Exception:
        table = {"available": False}
    return {"table": table}


def _mgt_payment(con, *, branch, ce, cs, group, has_prev, pe, ps):
    # names this section hands on; None where a period path never assigns one
    payment = None
    # ---- payment channels for the scope (how the branches got paid) ------
    # Tender mix over the period [cs, ce] from pos_payment_day: the scope total by
    # payment code (`channels`, each with % of scope total — defines the §10 matrix
    # column order) AND a per-branch breakdown (`by_branch`, each branch's own channel
    # mix). Powers §10, rendered for the MASTER group broken out per branch in the
    # all-branches split view. Daily table → degrades to unavailable if a pre-sync DB
    # lacks it.
    payment = {"available": False, "channels": [], "by_branch": [], "total": 0}
    try:
        if con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='pos_payment_day'").fetchone():
            pp_base = ["b.name!='ALL'"]; pparams: dict = {"s": cs.isoformat(), "e": ce.isoformat()}
            scope_filter(pp_base, pparams, branch, group)
            pw = " AND ".join(pp_base)
            prows = con.execute(f"""
                SELECT b.name branch, pp.payment_code code, ROUND(SUM(pp.amount)) amount, SUM(pp.txn_count) n
                FROM pos_payment_day pp JOIN branches b ON b.branch_id=pp.branch_id
                WHERE {pw} AND pp.day BETWEEN :s AND :e
                GROUP BY b.name, pp.payment_code""", pparams).fetchall()
            ptot = sum((r["amount"] or 0) for r in prows)
            if ptot > 0:
                agg: dict = {}; bdata: dict = {}
                for r in prows:
                    code = r["code"] or "—"; amt = r["amount"] or 0
                    agg[code] = agg.get(code, 0) + amt
                    bd = bdata.setdefault(r["branch"], {"branch": r["branch"], "total": 0, "ch": {}})
                    bd["ch"][code] = {"amount": round(amt), "txns": r["n"] or 0}
                    bd["total"] += amt
                channels = [{"code": c, "amount": round(a), "pct": round(a / ptot * 100, 1)}
                            for c, a in sorted(agg.items(), key=lambda kv: -kv[1])]
                by_branch = []
                for bd in bdata.values():
                    bt = bd["total"] or 1
                    chans = {c: {"amount": v["amount"], "txns": v["txns"], "pct": round(v["amount"] / bt * 100, 1)}
                             for c, v in bd["ch"].items()}
                    by_branch.append({"branch": bd["branch"], "total": round(bd["total"]), "channels": chans})
                by_branch.sort(key=lambda x: -x["total"])
                payment = {"available": True, "total": round(ptot), "channels": channels, "by_branch": by_branch}
                if has_prev:      # last-period tender mix for the §10 WoW deltas
                    pprev = con.execute(f"""
                        SELECT pp.payment_code code, ROUND(SUM(pp.amount)) amount
                        FROM pos_payment_day pp JOIN branches b ON b.branch_id=pp.branch_id
                        WHERE {pw} AND pp.day BETWEEN :ps AND :pe
                        GROUP BY pp.payment_code""",
                        {**pparams, "ps": ps.isoformat(), "pe": pe.isoformat()}).fetchall()
                    pby = {(r["code"] or "—"): (r["amount"] or 0) for r in pprev}
                    ptot_prev = sum(pby.values())
                    payment["prev"] = {
                        "total": round(ptot_prev),
                        "by_code": {k: round(v) for k, v in pby.items()},
                        "channels": {c["code"]: {"amount": round(pby.get(c["code"], 0)),
                                                 "pct": round(pby.get(c["code"], 0) / ptot_prev * 100, 1) if ptot_prev else None}
                                     for c in channels},
                    }
    except Exception:
        payment = {"available": False, "channels": [], "by_branch": [], "total": 0}
    return {"payment": payment}


@ttl_cache(ttl=HEAVY_TTL, stale=HEAVY_TTL, daily=True)   # lives until the next data swap or Thai midnight (core.HEAVY_TTL)
def report_management(
    period: str = Query("daily"),            # 'daily' | 'weekly' | 'monthly' | 'yearly' (PR-PP2)
    date:   Optional[str] = Query(None),     # focus day (default = latest day in data)
    week:   Optional[str] = Query(None),     # weekly: any date in the wanted FULL week (default = last completed week)
    month:  Optional[str] = Query(None),     # monthly: 'YYYY-MM' (default = last COMPLETE month)
    branch: Optional[str] = Query(None),
    group:  Optional[str] = Query(None),
    lang:   str = Query("en"),               # 'en' | 'th' — language for labels, comp_label & action items
    mtd:    Optional[str] = Query(None),     # monthly: '1' opens on the in-progress month, month-to-date (the phone)
    year:   Optional[str] = Query(None),     # yearly: 'YYYY' (default = the year of the latest complete day, to date)
):
    """One-glance management summary for a day, a week, a month or a year (PR-PP2: the week / month / year in
    progress is offered to date through the latest complete day, compared with the same days of the period before
    and of the year before; the default week and month stay the latest COMPLETE ones).

    Mirrors the proven idioms used across the dashboard so the numbers tie out:
      · revenue = SUM(total_inc_vat)/1.07 − SUM(service_charge)  (ex-VAT AND ex-service-charge)
      · current-vs-prior is WoW-fair (same weekday last week / full prior week);
        monthly compares against the previous calendar month + same month last year
      · food-cost % is the BoM figure for the period's month (_cogs_case — tier-safe)
      · money-leak signals reuse the Daily-Brief leak definitions (heavy/cash/reprint/zero)
    Weekly reports cover only FULL Mon–Sun weeks; monthly reports only COMPLETE
    calendar months. The response carries the selectable periods (`weeks`/`months`)
    and the chosen one (`selected_week`/`selected_month`), so the same simple
    presentation serves the Daily, Weekly AND Monthly report."""
    import datetime as _dt, calendar as _cal
    if period not in ("daily", "weekly", "monthly", "yearly"):
        period = "daily"
    th = (lang == "th")          # localise labels / comp_label / action items
    if th:
        MON = ["ม.ค.","ก.พ.","มี.ค.","เม.ย.","พ.ค.","มิ.ย.","ก.ค.","ส.ค.","ก.ย.","ต.ค.","พ.ย.","ธ.ค."]
        DOW = ["วันจันทร์","วันอังคาร","วันพุธ","วันพฤหัสบดี","วันศุกร์","วันเสาร์","วันอาทิตย์"]
    else:
        MON = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"]
        DOW = ["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday"]
    def dlabel(d): return f"{d.day} {MON[d.month-1]} {d.year}"

    base = ["st.source='Easy'", "b.name!='ALL'"]
    params: dict = {}
    scope_filter(base, params, branch, group)
    where = " AND ".join(base)

    def pct(c, p):
        return round((c - p) / p * 100, 1) if p else None

    # Each section of the report is its own function (Phase 3, 2026-09). They run
    # in order on one connection and pass what they compute forward through
    # `ctx`; a section that finds no data returns the "unavailable" payload,
    # which is passed straight back to the caller. The sections are the same
    # statements this function used to hold inline, so their output is
    # unchanged — backend/test_golden.py pins that for all three periods.
    ctx = {"DOW": DOW, "MON": MON, "_cal": _cal, "_dt": _dt, "base": base, "branch": branch, "date": date, "dlabel": dlabel, "group": group, "lang": lang, "month": month, "mtd": (mtd in ("1", "true", "yes")), "params": params, "pct": pct, "period": period, "th": th, "week": week, "where": where, "year": year}
    with get_db() as con:
        out = _mgt_window(con, DOW=ctx.get("DOW"), MON=ctx.get("MON"), _cal=ctx.get("_cal"), _dt=ctx.get("_dt"), date=ctx.get("date"), dlabel=ctx.get("dlabel"), month=ctx.get("month"), mtd=ctx.get("mtd"), params=ctx.get("params"), period=ctx.get("period"), th=ctx.get("th"), week=ctx.get("week"), where=ctx.get("where"), year=ctx.get("year"))
        if out.get("available") is False:
            return out
        ctx.update(out)
        out = _mgt_headline(con, ce=ctx.get("ce"), cs=ctx.get("cs"), has_prev=ctx.get("has_prev"), has_yoy=ctx.get("has_yoy"), is_mtd=ctx.get("is_mtd"), params=ctx.get("params"), pct=ctx.get("pct"), pe=ctx.get("pe"), ps=ctx.get("ps"), where=ctx.get("where"), ye=ctx.get("ye"), ys=ctx.get("ys"))
        ctx.update(out)
        out = _mgt_branches(con, branch=ctx.get("branch"), ce=ctx.get("ce"), cs=ctx.get("cs"), group=ctx.get("group"), has_prev=ctx.get("has_prev"), has_yoy=ctx.get("has_yoy"), is_mtd=ctx.get("is_mtd"), params=ctx.get("params"), pct=ctx.get("pct"), pe=ctx.get("pe"), period=ctx.get("period"), ps=ctx.get("ps"), where=ctx.get("where"), ye=ctx.get("ye"), ys=ctx.get("ys"))
        ctx.update(out)
        if period == "daily":
            _mgt_daily_normal(con, ctx)          # the one daily baseline (rules.daily_baseline)
        out = _mgt_leak(con, ce=ctx.get("ce"), cs=ctx.get("cs"), has_prev=ctx.get("has_prev"), params=ctx.get("params"), pct=ctx.get("pct"), pe=ctx.get("pe"), ps=ctx.get("ps"), where=ctx.get("where"))
        ctx.update(out)
        out = _mgt_menu(con, _cal=ctx.get("_cal"), _dt=ctx.get("_dt"), branch=ctx.get("branch"), ce=ctx.get("ce"), cs=ctx.get("cs"), cur=ctx.get("cur"), group=ctx.get("group"), has_prev=ctx.get("has_prev"), has_yoy=ctx.get("has_yoy"), leak=ctx.get("leak"), params=ctx.get("params"), pct=ctx.get("pct"), pe=ctx.get("pe"), prev=ctx.get("prev"), ps=ctx.get("ps"), where=ctx.get("where"), ye=ctx.get("ye"), ys=ctx.get("ys"))
        ctx.update(out)
        out = _mgt_tier(con, ce=ctx.get("ce"), cs=ctx.get("cs"), has_prev=ctx.get("has_prev"), pct=ctx.get("pct"), pe=ctx.get("pe"), ps=ctx.get("ps"), sp=ctx.get("sp"), sw=ctx.get("sw"), th=ctx.get("th"))
        ctx.update(out)
        out = _mgt_gp(con, cmp_ok=ctx.get("cmp_ok"), cost_available=ctx.get("cost_available"), cost_src=ctx.get("cost_src"), cur=ctx.get("cur"), daily_min=ctx.get("daily_min"), headline=ctx.get("headline"), prev=ctx.get("prev"), prev_cost_available=ctx.get("prev_cost_available"), tot_cur_cost=ctx.get("tot_cur_cost"), tot_prev_cost=ctx.get("tot_prev_cost"), tot_yoy_cost=ctx.get("tot_yoy_cost"))
        ctx.update(out)
        out = _mgt_pace(con, _cal=ctx.get("_cal"), branch=ctx.get("branch"), clean=ctx.get("clean"), food_sub=ctx.get("food_sub"), group=ctx.get("group"), latest_day=ctx.get("latest_day"), maxd=ctx.get("maxd"), params=ctx.get("params"), ref_anchor=ctx.get("ref_anchor"), where=ctx.get("where"))
        ctx.update(out)
        out = _mgt_mom(con, MON=ctx.get("MON"), _dt=ctx.get("_dt"), cur_month=ctx.get("cur_month"), days_elapsed=ctx.get("days_elapsed"), days_total=ctx.get("days_total"), latest_day=ctx.get("latest_day"), month_complete=ctx.get("month_complete"), mp=ctx.get("mp"), mtd_rev=ctx.get("mtd_rev"), mw=ctx.get("mw"), pace=ctx.get("pace"), params=ctx.get("params"), pct=ctx.get("pct"), ref_anchor=ctx.get("ref_anchor"), target=ctx.get("target"), where=ctx.get("where"))
        ctx.update(out)
        out = _mgt_trend(con, MON=ctx.get("MON"), _dt=ctx.get("_dt"), ce=ctx.get("ce"), cs=ctx.get("cs"), dlabel=ctx.get("dlabel"), params=ctx.get("params"), period=ctx.get("period"), where=ctx.get("where"))
        ctx.update(out)
        out = _mgt_table(con, _daypart=ctx.get("_daypart"), branch=ctx.get("branch"), ce=ctx.get("ce"), cs=ctx.get("cs"), group=ctx.get("group"), has_prev=ctx.get("has_prev"), params=ctx.get("params"), pe=ctx.get("pe"), peak=ctx.get("peak"), period=ctx.get("period"), ps=ctx.get("ps"), where=ctx.get("where"))
        ctx.update(out)
        out = _mgt_payment(con, branch=ctx.get("branch"), ce=ctx.get("ce"), cs=ctx.get("cs"), group=ctx.get("group"), has_prev=ctx.get("has_prev"), pe=ctx.get("pe"), ps=ctx.get("ps"))
        ctx.update(out)
        _mgt_target_scope(con, ctx)
        ctx["food_cost_target"] = food_cost_target(con)   # ONE target (owner 2026-10-25), Settings → ค่าเริ่มต้น
    branches = ctx["branches"]
    cash_heavy = ctx["cash_heavy"]
    ce = ctx["ce"]
    comp_label = ctx["comp_label"]
    cost_up = ctx["cost_up"]
    cs = ctx["cs"]
    days_in_period = ctx["days_in_period"]
    focus = ctx["focus"]
    food_cost_pct = ctx["food_cost_pct"]
    gp = ctx["gp"]
    has_prev = ctx["has_prev"]
    has_yoy = ctx["has_yoy"]
    headline = ctx["headline"]
    heavy = ctx["heavy"]
    label = ctx["label"]
    leak = ctx["leak"]
    maxd = ctx["maxd"]
    menu_out = ctx["menu_out"]
    mind = ctx["mind"]
    month_complete = ctx["month_complete"]
    months = ctx["months"]
    pace = ctx["pace"]
    partial = ctx["partial"]
    payment = ctx["payment"]
    pe = ctx["pe"]
    peak = ctx["peak"]
    projected = ctx["projected"]
    ps = ctx["ps"]
    reprint = ctx["reprint"]
    selected_month = ctx["selected_month"]
    selected_week = ctx["selected_week"]
    table = ctx["table"]
    target = ctx["target"]
    tier_out = ctx["tier_out"]
    trend = ctx["trend"]
    weeks = ctx["weeks"]
    ye = ctx["ye"]
    ys = ctx["ys"]


    # ---- auto-generated action items (localised, severity-ordered) -------
    # owner 2026-10-31 ("กดแล้วทำงานต่อได้เลย"): every line carries `go`, where its work is done, for the phone's ต้องทำ
    # (js/09c-mobile.js msActionGo): {"kind": "branch", "branch"} = the branch sheet · {"kind": "ms", "view", "focus"?, "branch"?,
    # "menu"?} = a detail screen of the phone summary (a key of _PH_DETAIL); focus / branch / menu = what it highlights there ·
    # {"kind": "tab", "tab"} = a page of the app
    pword = (("วัน" if period == "daily" else "เดือน" if period == "monthly" else "ปี" if period == "yearly" else "สัปดาห์") if th
             else ("day" if period == "daily" else "month" if period == "monthly" else "year" if period == "yearly" else "week"))
    actions = []
    # DV6 (phone table review 2026-10-25, top-10 #9): every line carries the baht it is about - `baht` signed (< 0 = sales
    # lost against the comparison, money given away or cost added; > 0 = the opposite) and `baht_label`, what the figure is.
    # Within one severity the lines run by the size of that baht.
    _vs = (f"เทียบ{pword}ก่อน" if th else f"vs last {pword}")
    if period == "daily":                    # the daily report compares with the normal (rules.daily_baseline)
        _vs = ("เทียบปกติ" if th else "vs normal")
    _hp = headline.get("prev")
    _disc_l = ("ส่วนลดของบิลเหล่านี้" if th else "discount on these bills")
    _tgt_l = ("ห่างเป้า" if th else "short of target")
    if _hp and headline.get("rev_wow_pct") is not None and headline["rev_wow_pct"] <= -10:
        actions.append({"sev": "high", "baht": round((headline.get("revenue") or 0) - (_hp.get("revenue") or 0)), "baht_label": _vs,
            "go": {"kind": "ms", "view": "branches"},
            "text": (f"ยอดขายรวมทั้งเครือลดลง {abs(headline['rev_wow_pct']):.0f}% {_vs} — ตรวจสอบสาเหตุ"
                     if th else
                     f"Chain net sales down {abs(headline['rev_wow_pct']):.0f}% {_vs} — investigate drivers.")})
    for r in branches:
        if r["wow_pct"] is not None and r["wow_pct"] <= -25 and r["revenue"] > 0:
            actions.append({"sev": "high", "baht": r.get("rev_delta"), "baht_label": _vs,
                "go": {"kind": "branch", "branch": r["branch"]},
                "text": (f"{r['branch']} ยอดขายลดลง {abs(r['wow_pct']):.0f}% {_vs} (฿{r['revenue']:,.0f})"
                         if th else
                         f"{r['branch']} net sales down {abs(r['wow_pct']):.0f}% {_vs} (฿{r['revenue']:,.0f}).")})
    if cash_heavy.get("bills"):
        _top = cash_heavy.get("top") or {}
        _where_th = (f" · ส่วนใหญ่ {_top['branch']} กะ{_top['shift']} ({_top['share_pct']}%)" if _top.get("branch") else "")
        _where_en = (f" · mostly {_top['branch']}, {_top['shift']} shift ({_top['share_pct']}%)" if _top.get("branch") else "")
        actions.append({"sev": "high", "baht": -round(cash_heavy.get("baht") or 0), "baht_label": _disc_l,
            "go": {"kind": "ms", "view": "discounts", "focus": "cash_heavy", "branch": _top.get("branch") or None},
            "text": (f"{cash_heavy['bills']} บิลส่วนลดที่จ่ายด้วยเงินสดเป็นหลัก (฿{cash_heavy['baht']:,.0f}){_where_th} — ตรวจสอบความเสี่ยงเงินขาด"
                     if th else
                     f"{cash_heavy['bills']} cash-heavy discounted bills (฿{cash_heavy['baht']:,.0f}){_where_en} — review for skim risk.")})
    if heavy["bills"]:
        actions.append({"sev": "med", "baht": -round(heavy.get("baht") or 0), "baht_label": _disc_l,
            "go": {"kind": "ms", "view": "discounts", "focus": "heavy"},
            "text": (f"{heavy['bills']} บิลให้ส่วนลด ≥30% (฿{heavy['baht']:,.0f}) ใน{pword}นี้"
                     if th else
                     f"{heavy['bills']} bills discounted ≥30% (฿{heavy['baht']:,.0f}) this {pword}.")})
    if reprint["bills"]:
        actions.append({"sev": "med", "baht": -round(reprint.get("baht") or 0), "baht_label": _disc_l,
            "go": {"kind": "ms", "view": "discounts", "focus": "reprint"},
            "text": (f"{reprint['bills']} บิลส่วนลดถูกพิมพ์ซ้ำมากกว่า 1 ครั้ง — อาจมีการพิมพ์ซ้ำผิดปกติ"
                     if th else
                     f"{reprint['bills']} discounted bills reprinted >1× — possible re-print abuse.")})
    fc_target = ctx["food_cost_target"]
    if food_cost_pct is not None and food_cost_pct > fc_target:
        actions.append({"sev": "med", "go": {"kind": "ms", "view": "profit"},
            "text": (f"ต้นทุนอาหาร {food_cost_pct:.1f}% — เป้า {fc_target:g}% · {food_cost_status_th(food_cost_pct, fc_target)}"
                     if th else
                     f"Food cost {food_cost_pct:.1f}% — {food_cost_pct - fc_target:.1f} pts above the {fc_target:g}% target.")})
    _ts = (pace or {}).get("target_scope") or {}
    if ctx.get("target_projected") is not None:          # DV1: like for like - the branches that have a target
        projected = ctx["target_projected"]
    if projected and target and projected < target * 0.95:
        gap = (1 - projected/target) * 100
        if th:
            word = "ยอดรวมทั้งเดือน" if month_complete else "ประมาณการสิ้นเดือน"
            if _ts.get("no_target_branches"):
                word += f" {_ts['branches']} สาขาที่มีเป้า"
            actions.append({"sev": "med", "baht": round(projected - target), "baht_label": _tgt_l, "go": {"kind": "ms", "view": "target"},
                "text": f"{word} ฿{projected:,.0f} ต่ำกว่าเป้า ฿{target:,.0f} อยู่ {gap:.0f}%"})
        else:
            word = "Month total" if month_complete else "Month-end projection"
            if _ts.get("no_target_branches"):
                word += f" of the {_ts['branches']} branches with a target"
            actions.append({"sev": "med", "baht": round(projected - target), "baht_label": _tgt_l, "go": {"kind": "ms", "view": "target"},
                "text": f"{word} ฿{projected:,.0f} is {gap:.0f}% below target ฿{target:,.0f}."})
    _behind = [r for r in branches if r.get("vs_target_pct") is not None and r["vs_target_pct"] < 90]
    if _behind:
        _behind.sort(key=lambda r: r["vs_target_pct"])
        _names = ", ".join(f"{r['branch']} {r['vs_target_pct']:.0f}%" for r in _behind[:4])
        _more = (f" +{len(_behind) - 4}" if len(_behind) > 4 else "")
        _proj = any(r.get("target_basis") == "projected" for r in _behind)
        _gap = sum(r["target_gap"] if r.get("target_gap") is not None else
                   round((r.get("projected") or r["revenue"] or 0) - r["target"]) for r in _behind if r.get("target"))
        actions.append({"sev": "med", "baht": _gap, "go": {"kind": "ms", "view": "branches", "focus": "target"},
                        "baht_label": (f"ห่างเป้ารวม {len(_behind)} สาขา" if th else f"short of target, {len(_behind)} branches"),
            "text": (f"{len(_behind)} สาขาต่ำกว่า 90% ของเป้าเดือนนี้{' (ประมาณการสิ้นเดือน)' if _proj else ''}: {_names}{_more}"
                     if th else
                     f"{len(_behind)} branches under 90% of this month's target{' (month-end projection)' if _proj else ''}: {_names}{_more}.")})
    if has_prev and cost_up and (cost_up[0].get("delta") or 0) >= 0.5:
        m0 = cost_up[0]
        # DV1 (review §5 no. 2): the line names the cause - the cost per plate or the plates per head
        _cov = (menu_out or {}).get("covers") or 0
        actions.append({"sev": "low", "baht": -round((m0.get("delta") or 0) * _cov) if _cov else None,
                        "baht_label": ("ต้นทุนที่เพิ่มของเมนูนี้" if th else "cost added by this dish"),
                        "go": {"kind": "ms", "view": "menu", "menu": m0.get("menu")},
                        "text": _cost_riser_action(m0, pword, th)})
    if gp.get("gp_pct_delta_pp") is not None and gp["gp_pct_delta_pp"] <= -3:
        actions.append({"sev": "med", "baht": round(gp["gp_pct_delta_pp"] / 100.0 * (gp.get("revenue") or 0)), "go": {"kind": "ms", "view": "profit"},
                        "baht_label": ("ผลต่อกำไรจากอัตรากำไรที่ลด" if th else "gross profit lost to the lower margin"),
            "text": (f"อัตรากำไรขั้นต้นลดลง {abs(gp['gp_pct_delta_pp']):.1f}pp เทียบ{pword}ก่อน — ตอนนี้ {gp['gp_pct']:.0f}%"
                     if th else
                     f"Gross margin down {abs(gp['gp_pct_delta_pp']):.1f}pp vs last {pword} — now {gp['gp_pct']:.0f}%.")})
    # the food-cost line (its wording lives in its own block): the baht above the ONE target (rules.food_cost_target) -
    # (food cost % - target) x the sales the % is taken on (pace.mtd_revenue: food_cost_pct = bom cost / those sales)
    _fc_rev = (pace or {}).get("mtd_revenue")
    for a in actions:
        if "baht" not in a and a["text"].startswith(("ต้นทุนอาหาร", "Food cost")):
            a["baht"] = (-round((food_cost_pct - fc_target) / 100.0 * _fc_rev)
                         if food_cost_pct is not None and _fc_rev else None)
            a["baht_label"] = (f"ต้นทุนเกินเป้า {fc_target:g}%" if th else f"cost above the {fc_target:g}% target")
    sev_rank = {"high": 0, "med": 1, "low": 2}
    actions.sort(key=lambda a: (sev_rank.get(a["sev"], 9), -abs(a.get("baht") or 0)))
    actions = actions[:7]
    if not actions:
        actions = [{"sev": "low", "go": {"kind": "ms", "view": "month"}, "text": ("ไม่พบประเด็นเร่งด่วนในช่วงนี้" if th else "No critical issues flagged for this period.")}]

    # §12 — what the guests said over the same window (customer_feedback), the
    # window before, what waits now, and whether the fix loop held. Never lets
    # a feedback problem take the whole report down.
    voice = None
    try:
        from routers.feedback_actions import compute_digest as _voice_digest
        with get_db() as _vcon:
            voice = _voice_digest(_vcon, start=cs.isoformat(), end=ce.isoformat(),
                                  prev_start=(ps.isoformat() if has_prev else None), prev_end=(pe.isoformat() if has_prev else None),
                                  branch=branch, group=group)
            _vcon.commit()
    except Exception as e:      # pragma: no cover — degraded, not broken
        print(f"[mgt] §12 customer voice unavailable: {e}")

    # §13A — stock-count usage variance (BoM Center Phase 2), complete months only and only
    # once a branch counts. Never lets a variance problem take the whole report down.
    stock_variance = None
    if period == "monthly" and not partial:
        try:
            from bom_center.hub import monthly as _sv_monthly
            from bom_center.schema import ensure as _bc_ensure
            with get_db() as _scon:
                _bc_ensure(_scon)
                _sv = _sv_monthly(_scon, cs.isoformat()[:7])
                if branch:
                    _sv["branches"] = [b for b in _sv["branches"] if b["branch"] == branch]
                if _sv["branches"]:
                    stock_variance = _sv
        except Exception as e:      # pragma: no cover — degraded, not broken
            print(f"[mgt] §13A stock variance unavailable: {e}")

    # PR-PW (owner 2026-10-29, no. 7-10, 12-14) · ตรวจวันนี้: the phone's second block (monthly only; every item guarded)
    watch = None
    if period == "monthly":
        try:
            from routers import mgt_watch as _mw
            with get_db() as _wcon:              # the sections' connection is closed by here (as §12 / §13A do)
                watch = _mw.build(_wcon, cs=cs, ce=ce, ps=ps if has_prev else None, pe=pe if has_prev else None, has_prev=has_prev,
                                  where=where, params=params, branch=branch, group=group, th=th)
        except Exception as e:      # pragma: no cover — degraded, not broken
            print(f"[mgt] watch unavailable: {e}")

    return {
        "available": True,
        "period": period,
        "lang": ("th" if th else "en"),
        "label": label,
        "watch": watch,
        "comp_label": comp_label,
        **({"prev_label": ctx["prev_label"]} if ctx.get("prev_label") else {}),   # daily: what the sections under the league compare with
        "focus": focus,
        "range": [cs.isoformat(), ce.isoformat()],
        "comp_range": [ps.isoformat(), pe.isoformat()] if has_prev else None,
        "yoy_range": [ys.isoformat(), ye.isoformat()] if has_yoy else None,
        "partial": partial,
        "selected_week": selected_week,
        "weeks": weeks,
        "selected_month": selected_month,
        "months": months,
        "selected_year": ctx.get("selected_year"),
        "years": ctx.get("years") or [],
        "to_date": bool(ctx.get("to_date")),        # PR-PP2: the period is not finished - to date, same days compared
        "days_in_period": days_in_period,
        "latest_day": maxd,
        "min_day": mind,
        "branch_scope": branch or ("Group: " + group if group else "All branches"),
        "has_prev": has_prev,
        "has_yoy": has_yoy,
        "headline": headline,
        "branches": branches,
        # owner 2026-10-26: the year-ago chain total's sales of branches the league hides (the footnote; absent = none)
        **({"branches_ly_hidden": ctx["branches_ly_hidden"]} if ctx.get("branches_ly_hidden") else {}),
        "gp": gp,
        "food_cost_target": ctx["food_cost_target"],
        "leak": leak,
        "menu": menu_out,
        "pace": pace,
        "peak": peak,
        "table": table,
        "payment": payment,
        "tier": tier_out,
        "trend": trend,
        "actions": actions,
        "feedback": voice,
        "stock_variance": stock_variance,
    }


# PL3: a role with neither cost.analysis nor profit.pnl (marketing, sales support, operation, warehouse...) gets the
# management summary WITHOUT cost, gross profit or food-cost figures: the server removes them from the payload, so a
# phone page that forgets to hide a tile still cannot show them. The cached report is shared; this works on a copy.
_MGT_COST_MODULES = {"cost.analysis", "profit.pnl"}
# the action sentences that are cost / margin figures (Thai and English wording of report_management)
_MGT_COST_ACTION = __import__("re").compile(r"ต้นทุนอาหาร|ต้นทุนต่อหัว|อัตรากำไรขั้นต้น|[Ff]ood cost|[Gg]ross margin")


def strip_management_cost(d: dict) -> dict:
    """Return a copy of a /api/reports/management payload with every cost / margin figure removed."""
    out = dict(d)
    gp = out.get("gp")
    if isinstance(gp, dict):
        out["gp"] = {"revenue": gp.get("revenue"), "hidden": True}
    menu = out.get("menu")
    if isinstance(menu, dict):
        m = {k: v for k, v in menu.items() if k in ("basis", "covers", "covers_prev", "top_n")}
        m.update({"top": [], "risers": [], "savers": [], "hidden": True})
        out["menu"] = m
    pace = out.get("pace")
    if isinstance(pace, dict):
        p2 = {k: v for k, v in pace.items() if k not in ("food_cost_pct", "bom_cost_mtd", "cost_coverage", "food_cost_pct_prev")}
        if isinstance(p2.get("prev_month"), dict):
            p2["prev_month"] = {k: v for k, v in p2["prev_month"].items() if k != "food_cost_pct"}
        out["pace"] = p2
    tier = out.get("tier")
    if isinstance(tier, dict):
        t2 = {k: v for k, v in tier.items() if k != "drivers"}
        t2["rows"] = [{k: v for k, v in r.items() if k not in ("cogs_head", "cogs_head_prev", "gp_head", "food_pct")}
                      for r in (tier.get("rows") or []) if isinstance(r, dict)]
        out["tier"] = t2
    out["actions"] = [a for a in (out.get("actions") or []) if not (isinstance(a, dict) and _MGT_COST_ACTION.search(a.get("text") or ""))] or \
        [{"sev": "low", "go": {"kind": "ms", "view": "month"}, "text": ("ไม่พบประเด็นเร่งด่วนในช่วงนี้" if out.get("lang") == "th" else "No critical issues flagged for this period.")}]
    out.pop("stock_variance", None)
    return out


@router.get("/api/reports/management")
def report_management_route(
    request: Request,
    period: str = Query("daily"),
    date:   Optional[str] = Query(None),
    week:   Optional[str] = Query(None),
    month:  Optional[str] = Query(None),
    branch: Optional[str] = Query(None),
    group:  Optional[str] = Query(None),
    lang:   str = Query("en"),
    mtd:    Optional[str] = Query(None),
    year:   Optional[str] = Query(None),
):
    """The management summary for the signed-in role: the cached report, minus the cost figures for a
    role that holds neither cost.analysis nor profit.pnl (see strip_management_cost)."""
    import auth as _auth
    resp = report_management(period=period, date=date, week=week, month=month, branch=branch, group=group, lang=lang, mtd=mtd, year=year)
    if _auth.request_modules(request) & _MGT_COST_MODULES:
        return resp
    try:
        data = _json.loads(resp.body)
    except Exception:
        return resp
    return Response(content=_json.dumps(strip_management_cost(data), ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
                    media_type="application/json")


# ---------------------------------------------------------------------------
# /api/reports/management/months — the phone's month-by-month view (รายเดือน)
# ---------------------------------------------------------------------------
# The last N closed months plus the month in progress (month-to-date), each with the
# figures the management report prints for that month — revenue, covers, spend/head, GP —
# and its change on the previous month (and on the same month last year where the year-ago
# window is complete). ONE call instead of one report per month. It adds no formula: the
# windows come from _mgt_month_bounds, the sums from _mgt_agg and the cost from
# _mgt_all_cost, the same functions the report uses, and the month-to-date comparison is the
# report's weekday-fair baseline (§3). backend/test_mgt_months.py pins every figure against
# /api/reports/management for the same month and scope.
_MGT_YOY_FROM = "2026-05-15"   # the report's year-on-year guard: a year-ago window is complete only from mid-May 2026


@ttl_cache(ttl=HEAVY_TTL, stale=HEAVY_TTL, daily=True)
def report_management_months(
    branch: Optional[str] = Query(None),
    group:  Optional[str] = Query(None),
    lang:   str = Query("th"),               # 'th' | 'en' — month labels
    closed: int = Query(12, ge=2, le=23),    # how many CLOSED months before the one in progress
):
    """Month-by-month table for the phone: `closed` complete months, oldest first, then the
    in-progress month (month-to-date through the latest complete day) when there is one.
    The in-progress month is compared on the report's weekday-fair baseline (`mom.basis`
    = 'dow'), never against a whole month."""
    import datetime as _dt, calendar as _cal
    th = (lang == "th")
    MON = (["ม.ค.","ก.พ.","มี.ค.","เม.ย.","พ.ค.","มิ.ย.","ก.ค.","ส.ค.","ก.ย.","ต.ค.","พ.ย.","ธ.ค."] if th
           else ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"])

    base = ["st.source='Easy'", "b.name!='ALL'"]
    params: dict = {}
    scope_filter(base, params, branch, group)
    where = " AND ".join(base)
    sb = ["b.name!='ALL'"]; sp: dict = {}
    scope_filter(sb, sp, branch, group)
    sw = " AND ".join(sb)

    def pct(c, p):
        return round((c - p) / p * 100, 1) if p else None

    with get_db() as con:
        w = _mgt_window(con, DOW=[], MON=MON, _cal=_cal, _dt=_dt, date=None, dlabel=lambda d: "",
                        month=None, mtd=True, params=params, period="monthly", th=th, week=None, where=where)
        if w.get("available") is False or not w.get("months"):
            return {"available": False}
        latest_day, mind, maxd = w["latest_day"], w["mind"], w["maxd"]
        picked = list(w["months"])
        mtd_entry = picked[0] if picked and picked[0].get("mtd") else None
        mtd_month = mtd_entry["month"] if mtd_entry else None
        lc = _dt.date.fromisoformat(mtd_entry["through"]) if mtd_entry else None
        sel_months = [m["month"] for m in picked if not m.get("mtd")][:closed]
        sel_months.reverse()                                    # oldest → newest
        if mtd_month:
            sel_months.append(mtd_month)

        # ---- cost source, resolved per window exactly as the report does (§4) ----
        _dmin = con.execute("SELECT MIN(day) d FROM v_sales_sku_daily").fetchone()
        daily_min = (_dmin["d"] if _dmin else None) or "9999-99-99"

        def whole_months(s, e):
            return s.day == 1 and e == _dt.date(e.year, e.month, _cal.monthrange(e.year, e.month)[1])

        _agg_memo: dict = {}
        _cost_memo: dict = {}

        def agg(s, e):
            k = (s, e)
            if k not in _agg_memo:
                _agg_memo[k] = _mgt_agg(con, where, params, s, e)
            return _agg_memo[k]

        def win_cost(s, e):
            """→ (cost, source). A window with no cost source — or a source that holds no
            cost at all — has no GP: never revenue minus ฿0 (a fake 100 % margin)."""
            k = (s, e)
            if k not in _cost_memo:
                out = (0.0, "unavailable")
                try:
                    if s.isoformat() >= daily_min:
                        out = (_mgt_all_cost(con, sw, sp, s, e, "daily"), "daily")
                    elif whole_months(s, e):
                        out = (_mgt_all_cost(con, sw, sp, s, e, "monthly"), "monthly")
                except Exception:
                    out = (0.0, "unavailable")
                if out[1] != "unavailable" and not out[0]:
                    out = (0.0, "unavailable")
                _cost_memo[k] = out
            return _cost_memo[k]

        def gp_pct_of(rev, s, e):
            cost, src = win_cost(s, e)
            if src == "unavailable" or not rev:
                return None, None, None, src
            gp = round(rev - cost, 2)
            return round(gp / rev * 100, 1), gp, round(cost), src

        rows_out = []
        for sel in sel_months:
            cs, ce, ps, pe, ys, ye, partial = _mgt_month_bounds(_dt, _cal, sel, mtd_month, lc, latest_day)
            is_mtd = (sel == mtd_month)
            has_prev = ps.isoformat() >= mind
            has_yoy = ys.isoformat() >= mind
            cur = agg(cs, ce)
            prev = agg(ps, pe) if has_prev else None

            # ---- the change on the previous month ----
            mom = {"revenue": None, "customers": None, "rev_per_head": None, "gp_pp": None,
                   "basis": "month", "range": [ps.isoformat(), pe.isoformat()] if has_prev else None}
            if prev:
                if not is_mtd:
                    mom["revenue"] = pct(cur["revenue"], prev["revenue"])
                    mom["customers"] = pct(cur["customers"], prev["customers"])
                    mom["rev_per_head"] = (pct(cur["rev_per_head"] or 0, prev["rev_per_head"])
                                           if prev["rev_per_head"] else None)
                else:
                    # PR-PP2: the month in progress to date on the calendar (days 1..n of last month), as the report
                    # headline and the daily-sales tile; the weekday-fair baseline stays on the report's §3 pace
                    mom["basis"] = "to_date"
                    b_rev, b_cust = prev["revenue"], prev["customers"]
                    b_rph = prev["rev_per_head"]
                    c_rph = cur["rev_per_head"] or 0
                    mom["revenue"] = pct(cur["revenue"], b_rev)
                    mom["customers"] = pct(cur["customers"], b_cust)
                    mom["rev_per_head"] = pct(c_rph, b_rph) if b_rph else None

            # ---- year on year (only where the year-ago window is complete) ----
            yoy = None
            if has_yoy and ce.isoformat() >= _MGT_YOY_FROM:
                y = agg(ys, ye)
                yoy = {"revenue": pct(cur["revenue"], y["revenue"]),
                       "customers": pct(cur["customers"], y["customers"]),
                       "rev_per_head": (pct(cur["rev_per_head"] or 0, y["rev_per_head"])
                                        if y["rev_per_head"] else None),
                       "range": [ys.isoformat(), ye.isoformat()]}

            # ---- gross profit, the report's §4 basis ----
            gp_pct, gp, food_cost, cost_src = gp_pct_of(cur["revenue"], cs, ce)
            if gp_pct is not None and prev and prev["customers"] > 0 and prev["revenue"]:
                pp_prev, _g, _c, _s = gp_pct_of(prev["revenue"], ps, pe)
                if pp_prev is not None:
                    mom["gp_pp"] = round(gp_pct - pp_prev, 1)

            rows_out.append({
                "month": sel,
                "label": f"{MON[cs.month - 1]} {cs.year}",
                "partial": bool(partial), "mtd": is_mtd,
                "through": ce.isoformat() if is_mtd else None,
                "days": (ce - cs).days + 1,
                "range": [cs.isoformat(), ce.isoformat()],
                "revenue": cur["revenue"], "customers": cur["customers"], "bills": cur["bills"],
                "rev_per_head": cur["rev_per_head"],
                "gp_pct": gp_pct, "gp": gp, "food_cost": food_cost, "cost_basis": cost_src,
                "mom": mom, "yoy": yoy,
            })
    return {
        "available": True,
        "lang": ("th" if th else "en"),
        "branch_scope": branch or ("Group: " + group if group else "All branches"),
        "latest_day": maxd, "min_day": mind,
        "mtd": bool(mtd_month), "through": lc.isoformat() if lc else None,
        "yoy_from": _MGT_YOY_FROM,
        "months": rows_out,
    }


def strip_months_cost(d: dict) -> dict:
    """PL3: the month-by-month series without gross profit / food cost, for a role that holds neither cost module."""
    out = dict(d)
    rows = []
    for r in out.get("months") or []:
        r = {k: v for k, v in r.items() if k not in ("gp_pct", "gp", "food_cost", "cost_basis")}
        if isinstance(r.get("mom"), dict):
            r["mom"] = {k: v for k, v in r["mom"].items() if k != "gp_pp"}
        rows.append(r)
    out["months"] = rows
    return out


@router.get("/api/reports/management/months")
def report_management_months_route(
    request: Request,
    branch: Optional[str] = Query(None),
    group:  Optional[str] = Query(None),
    lang:   str = Query("th"),
    closed: int = Query(12, ge=2, le=23),
):
    """The month-by-month series for the signed-in role (cost figures removed when the role holds no cost module)."""
    import auth as _auth
    resp = report_management_months(branch=branch, group=group, lang=lang, closed=closed)
    if _auth.request_modules(request) & _MGT_COST_MODULES:
        return resp
    try:
        data = _json.loads(resp.body)
    except Exception:
        return resp
    return Response(content=_json.dumps(strip_months_cost(data), ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
                    media_type="application/json")


# ---------------------------------------------------------------------------
# /api/reports/management.xlsx — the same Management Summary as a formatted .xlsx
# ---------------------------------------------------------------------------
# Glossary for the workbook's Definitions sheet. Kept in lockstep with the printed
# appendix (T.defs in js/02-mgt-report.js) — change one, change the other.
_MGT_DEFS_EN = [
    ("Net sales", "Bill total including VAT / 1.07. VAT is never counted as revenue."),
    ("Gross profit (GP)", "Net sales minus food COGS. Labour, rent and utilities are NOT deducted here."),
    ("Covers", "Guests served (pax on the bill). Spend / head = net sales / covers."),
    ("Avg ticket", "Net sales / number of bills — one bill can carry several covers."),
    ("Food cost %", "Recipe (BoM) food cost / net sales for the period's month. Dishes with no costed "
                    "recipe book 0 THB, so the figure is a FLOOR; plate coverage is disclosed in the report."),
    ("Comparison basis", "Daily = the same weekday last week; weekly = the previous full Mon-Sun week; "
                         "monthly = the previous calendar month, weekday-matched while the month is still partial."),
    ("Projection", "Day-of-week weighted run-rate — elapsed days extended by last month's weekday shape, "
                   "not a flat daily average."),
    ("Heavy discount", "A bill discounted 30% or more of its pre-discount value."),
    ("Cash-heavy discount", "A discounted bill settled mostly in cash — the classic skim signature."),
    ("Reprinted bill", "A discounted bill printed more than once."),
    ("Zero-gross bill", "A bill left at 0 THB after discount (free / discount-only)."),
    ("Price tier", "The adult buffet menu price on the bill, ex-VAT. 'promo' = a non-standard price."),
    ("YoY", "Suppressed before mid-May 2026 — sales history starts ~May 2025, so the year-ago window "
            "is incomplete."),
    ("Basis of preparation", "Unaudited management information generated from the POS sales database. "
                             "Tier, discount and cost-per-head analysis covers buffet bills."),
]
_MGT_DEFS_TH = [
    ("ยอดขายสุทธิ", "ยอดบิลรวม VAT ÷ 1.07 — ไม่นับ VAT เป็นรายได้"),
    ("กำไรขั้นต้น (GP)", "ยอดขายสุทธิ − ต้นทุนอาหาร ยังไม่หักค่าแรง ค่าเช่า ค่าสาธารณูปโภค"),
    ("จำนวนหัว", "ลูกค้าที่รับบริการ (pax บนบิล) · ยอดต่อหัว = ยอดขายสุทธิ ÷ จำนวนหัว"),
    ("ยอดเฉลี่ย/บิล", "ยอดขายสุทธิ ÷ จำนวนบิล — หนึ่งบิลอาจมีหลายหัว"),
    ("ต้นทุนอาหาร %", "ต้นทุนตามสูตร BoM ÷ ยอดขายสุทธิของเดือนนั้น · เมนูที่ยังไม่มีต้นทุนคิดเป็น ฿0 "
                      "ตัวเลขจึงเป็นขั้นต่ำ"),
    ("ฐานการเปรียบเทียบ", "รายวัน = ปกติ (ค่ากลางของวันเดียวกันใน 8 สัปดาห์ก่อน ไม่นับวันที่ไม่มียอดขาย) · รายสัปดาห์ = สัปดาห์ จ.–อา. ก่อนหน้า · "
                          "รายเดือน = เดือนก่อน (ถ่วงตามวันในสัปดาห์เมื่อเดือนยังไม่ครบ)"),
    ("ประมาณการ", "อัตราเฉลี่ยถ่วงตามวันในสัปดาห์ — ต่อยอดจากวันที่ผ่านมาโดยอิงรูปแบบวันของเดือนก่อน"),
    ("ส่วนลดหนัก", "บิลที่ให้ส่วนลดตั้งแต่ 30% ของมูลค่าก่อนลด"),
    ("ส่วนลดจ่ายเงินสด", "บิลส่วนลดที่ชำระด้วยเงินสดเป็นหลัก — รูปแบบความเสี่ยงเงินขาด"),
    ("บิลพิมพ์ซ้ำ", "บิลส่วนลดที่ถูกพิมพ์มากกว่า 1 ครั้ง"),
    ("บิลยอดศูนย์", "บิลที่เหลือ ฿0 หลังหักส่วนลด (แจกฟรี / ส่วนลดล้วน)"),
    ("Tier ราคา", "ราคาบุฟเฟ่ต์ผู้ใหญ่บนบิล (ไม่รวม VAT) · โปรฯ = ราคานอกมาตรฐาน"),
    ("YoY", "ซ่อนก่อนกลาง พ.ค. 2026 — ข้อมูลเริ่ม ~พ.ค. 2025 ช่วงปีก่อนจึงยังไม่ครบ"),
    ("เกณฑ์การจัดทำ", "ข้อมูลเพื่อการบริหาร ยังไม่ผ่านการตรวจสอบบัญชี จัดทำจากฐานข้อมูลการขาย POS · "
                     "การวิเคราะห์ tier ส่วนลด และต้นทุนต่อหัว นับเฉพาะบิลบุฟเฟ่ต์"),
]
def _xlsx_setup(*, Alignment, Font, PatternFill, get_column_letter, j, openpyxl):
    # names this section hands on; None where a path never assigns one
    BOLD = None
    H = None
    NUM = None
    NUM2 = None
    P = None
    PCT = None
    pdelta = None
    wb = None
    wb = openpyxl.Workbook()
    HDR    = Font(bold=True, color="FFFFFF")
    BOLD   = Font(bold=True)
    FILL_H = PatternFill("solid", fgColor="0F766E")
    NUM, NUM2, PCT = '#,##0', '#,##0.00', '0.0%'
    CENTER = Alignment(horizontal="center")
    pdelta = lambda v: (v / 100.0) if v is not None else None

    def header(ws, cols):
        ws.append(cols)
        for c in ws[ws.max_row]:
            c.font = HDR; c.fill = FILL_H; c.alignment = CENTER

    def autofit(ws):
        for col in ws.columns:
            mx = max((len(str(c.value or "")) for c in col), default=8)
            ws.column_dimensions[get_column_letter(col[0].column)].width = min(mx + 2, 52)

    H = j.get("headline", {}) or {}
    P = j.get("pace", {}) or {}
    return {"BOLD": BOLD, "H": H, "NUM": NUM, "NUM2": NUM2, "P": P, "PCT": PCT, "autofit": autofit, "header": header, "pdelta": pdelta, "wb": wb}


def _xlsx_summary(*, Font, H, NUM, P, PCT, autofit, header, j, pdelta, wb):
    # Sheet 1 — Summary
    ws = wb.active; ws.title = "Summary"
    ws.append(["Oshinei — Management Summary Report"]); ws["A1"].font = Font(bold=True, size=14)
    ws.append(["Report", {"daily": "Daily", "monthly": "Monthly"}.get(j["period"], "Weekly"), j.get("label", "")])
    ws.append(["Scope", j.get("branch_scope", "")])
    ws.append(["Compared with", j.get("comp_label", "")])
    ws.append(["Latest data day", j.get("latest_day", "")])
    ws.append([])
    G = j.get("gp", {}) or {}
    header(ws, ["Headline metric", "Value", "vs prev %", "YoY %"])
    for name, val, wow, yoy in [
        ("Net Sales (ex-VAT)", H.get("revenue"),     H.get("rev_wow_pct"),    H.get("rev_yoy_pct")),
        ("Gross Profit",       G.get("gp"),          None,                    None),
        ("Customers (covers)", H.get("customers"),    H.get("cust_wow_pct"),   H.get("cust_yoy_pct")),
        ("Spend / Head",       H.get("rev_per_head"), H.get("rph_wow_pct"),    H.get("rph_yoy_pct")),
        ("Avg Ticket",         H.get("avg_ticket"),   H.get("ticket_wow_pct"), None),
        ("Bills",              H.get("bills"),        H.get("bills_wow_pct"),  None),
        ("Discounts",          H.get("discount"),     None,                    None),
    ]:
        ws.append([name, val, pdelta(wow), pdelta(yoy)])
        ws.cell(ws.max_row, 2).number_format = NUM
        if wow is not None: ws.cell(ws.max_row, 3).number_format = PCT
        if yoy is not None: ws.cell(ws.max_row, 4).number_format = PCT
    ws.append([])
    header(ws, ["Pacing & cost (" + str(P.get("month", "")) + ")", "Value"])
    complete = P.get("month_complete")
    for name, val, fmt in [
        (("Month net sales" if complete else "MTD net sales"), P.get("mtd_revenue"), NUM),
        (("Month total" if complete else "Projected month-end"), P.get("projected"), NUM),
        ("Revenue target", P.get("target"), NUM),
        ("Projection vs target", pdelta(P.get("proj_vs_target_pct")), PCT),
        ("Food cost % (BoM, MTD)", pdelta(P.get("food_cost_pct")), PCT),
        ("Gross margin % (this period)", pdelta(G.get("gp_pct")), PCT),
    ]:
        ws.append([name, val]); ws.cell(ws.max_row, 2).number_format = fmt
    for key, title in (("bridge", "GP bridge vs " + str(j.get("comp_label", "prev"))), ("bridge_yoy", "GP bridge vs same period last year")):
        B = G.get(key)
        if not B:
            continue
        ws.append([])
        header(ws, [title, "THB"])
        for name, val in [("GP previous", B["gp_prev"]), ("Volume (covers) effect", B["volume"]),
                          ("Spend/head effect", B["spend_head"]), ("Cost/head effect", B["cost_head"]),
                          ("GP this period", B["gp_cur"])]:
            ws.append([name, val]); ws.cell(ws.max_row, 2).number_format = NUM
    autofit(ws)
    return {}


def _xlsx_branches(*, NUM, PCT, autofit, header, j, pdelta, wb):
    # Sheet 2 — Branch League
    ws = wb.create_sheet("Branch League")
    header(ws, ["Branch", "Net Sales", "Covers", "Spend/Head", "Share %", "vs prev %", "YoY %", "Target", "% of target"])
    for b in j.get("branches", []):
        ws.append([b["branch"], b["revenue"], b["customers"], b["rev_per_head"],
                   pdelta(b["share_pct"]), pdelta(b["wow_pct"]), pdelta(b.get("yoy_pct")),
                   b.get("target"), pdelta(b.get("vs_target_pct"))])
        r = ws.max_row
        for c in (2, 3, 4, 8): ws.cell(r, c).number_format = NUM
        for c in (5, 6, 7, 9): ws.cell(r, c).number_format = PCT
    autofit(ws)
    return {}


def _xlsx_leak(*, NUM, autofit, header, j, wb):
    # Sheet 3 — Money-Leak Radar
    ws = wb.create_sheet("Money-Leak Radar")
    header(ws, ["Signal", "Bills", "Baht", "Top branches"])
    L = j.get("leak", {}) or {}
    for key, lbl in [("heavy_discount", "Heavy discounts (>=30%)"),
                     ("cash_heavy", "Cash-heavy discounts"),
                     ("reprinted", "Reprinted bills"),
                     ("zero_gross", "Zero-gross bills")]:
        o = L.get(key, {}) or {}
        tops = ", ".join(x.get("branch", "") for x in (o.get("by_branch") or [])[:3])
        ws.append([lbl, o.get("bills", 0), o.get("baht", 0), tops])
        ws.cell(ws.max_row, 2).number_format = NUM; ws.cell(ws.max_row, 3).number_format = NUM
    autofit(ws)
    return {}


def _xlsx_menu(*, BOLD, NUM2, PCT, autofit, header, j, pdelta, wb):
    # Sheet 4 — Menu Cost per Head
    ws = wb.create_sheet("Menu Cost per Head")
    M = j.get("menu", {}) or {}
    ws.append([f"Total food cost / head: {M.get('total_cph')}  (over {M.get('covers')} covers)"])
    ws["A1"].font = BOLD
    ws.append([])
    def menu_block(title, rows, signed):
        header(ws, [title, "Cost/head", "Prev -> now", "%"] if signed else [title, "Cost/head", "Qty", ""])
        for m in rows:
            if signed:
                ws.append([m["menu"], m["delta"],
                           f"{m.get('cost_per_head_prev')} -> {m.get('cost_per_head')}", pdelta(m.get("pct"))])
                ws.cell(ws.max_row, 2).number_format = NUM2; ws.cell(ws.max_row, 4).number_format = PCT
            else:
                ws.append([m["menu"], m.get("cost_per_head"), m.get("qty"), ""])
                ws.cell(ws.max_row, 2).number_format = NUM2
        ws.append([])
    menu_block("Highest cost / head", M.get("top", []), False)
    menu_block("Cost / head RISING (margin risk)", M.get("risers", []), True)
    menu_block("Cost / head FALLING", M.get("savers", []), True)
    autofit(ws)
    return {}


def _xlsx_trend(*, NUM, autofit, header, j, wb):
    # Sheet 5 — Trend
    ws = wb.create_sheet("Trend")
    header(ws, ["Period start", "Label", "Net Sales"])
    for t in j.get("trend", []):
        ws.append([t.get("start"), t.get("label"), t.get("revenue")])
        ws.cell(ws.max_row, 3).number_format = NUM
    autofit(ws)
    return {}


def _xlsx_peak(*, NUM, PCT, autofit, header, j, pdelta, wb):
    # Sheet 6 — Peak Hours & Dayparts
    PK = j.get("peak", {}) or {}
    ws = wb.create_sheet("Peak & Dayparts")
    header(ws, ["Daypart", "Revenue", "% of revenue", "Covers"])
    for d in PK.get("dayparts", []):
        ws.append([d.get("name"), d.get("revenue"), pdelta(d.get("pct")), d.get("covers")])
        ws.cell(ws.max_row, 2).number_format = NUM; ws.cell(ws.max_row, 3).number_format = PCT
    ws.append([])
    if PK.get("peak_hour"):
        ws.append(["Busiest hour", f"{PK['peak_hour']['hour']:02d}:00", PK["peak_hour"]["revenue"]])
        ws.cell(ws.max_row, 3).number_format = NUM
    if PK.get("best_day"):
        ws.append(["Busiest day", PK["best_day"]])
    ws.append([])
    if PK.get("by_day"):
        header(ws, ["Day of week", "Revenue", "Covers"])
        for d in PK["by_day"]:
            ws.append([d["dow"], d["revenue"], d["covers"]]); ws.cell(ws.max_row, 2).number_format = NUM
    else:
        header(ws, ["Hour", "Revenue", "Covers", "Bills"])
        for hrow in PK.get("hourly", []):
            ws.append([f"{hrow['hour']:02d}:00", hrow["revenue"], hrow["covers"], hrow["bills"]])
            ws.cell(ws.max_row, 2).number_format = NUM
    autofit(ws)
    return {}


def _xlsx_table(*, autofit, header, j, wb):
    # Sheet 7 — Table Turnover (only when available)
    TB = j.get("table", {}) or {}
    if TB.get("available"):
        ws = wb.create_sheet("Table Turnover")
        ws.append(["Covers / table", TB.get("covers_per_table")])
        ws.append(["Avg turn (min)", TB.get("avg_turn_min")])
        ws.append(["Tables used", TB.get("tables")])
        ws.append(["Total covers", TB.get("covers")])
        ws.append([])
        header(ws, ["Branch", "Tables", "Covers", "Covers/Table", "Avg turn (min)"])
        for b in TB.get("by_branch", []):
            ws.append([b["branch"], b["tables"], b["covers"], b["covers_per_table"], b["avg_turn_min"]])
        autofit(ws)
    return {}


def _xlsx_tier(*, BOLD, NUM, NUM2, PCT, autofit, header, j, pdelta, wb):
    # Sheet — Buffet Price Tiers (only when the tier fact tables cover the window)
    TIER = j.get("tier") or {}
    if TIER.get("rows"):
        ws = wb.create_sheet("Price Tiers")
        ws.append([f"Buffet guests by price tier: {TIER.get('pax')}"
                   + (f"  (prev {TIER.get('pax_prev')})" if TIER.get("pax_prev") is not None else "")])
        ws["A1"].font = BOLD
        if TIER.get("mix_effect") is not None:
            ws.append([f"Tier-mix effect on spend/head vs comparison period: {TIER['mix_effect']:+.2f} THB"])
        ws.append([])
        header(ws, ["Tier", "Guests", "Guests prev", "vs prev %", "Share %",
                    "Rev/Head", "Cost/Head", "Cost/Head prev", "GP/Head", "COGS %"])
        for r in TIER["rows"]:
            ws.append([r["label"], r["pax"], r.get("pax_prev"), pdelta(r.get("d_pax_pct")),
                       pdelta(r.get("share")), r.get("rev_head"), r.get("cogs_head"),
                       r.get("cogs_head_prev"), r.get("gp_head"), pdelta(r.get("food_pct"))])
            rw = ws.max_row
            for c in (2, 3): ws.cell(rw, c).number_format = NUM
            for c in (6, 7, 8, 9): ws.cell(rw, c).number_format = NUM2
            for c in (4, 5, 10): ws.cell(rw, c).number_format = PCT
        DRV = TIER.get("drivers") or {}
        DPAY = (DRV.get("by_tier") or {}).get(DRV.get("default_tier"))
        if DPAY:
            def _short_menu(s):
                s = (s or "").strip()
                if s.startswith("(B) "):
                    s = s[4:]
                if s.lower().endswith("(buffet)"):
                    s = s[:-len("(buffet)")].rstrip()
                return s
            _frac = lambda p: (p / 100.0 if p is not None else None)
            ws.append([])
            ws.append([f"Why {DPAY.get('focus_label', '')} runs the highest food %"
                       + (f" ({DPAY['focus_food_pct']:.1f}%)" if DPAY.get("focus_food_pct") is not None else "")
                       + " — cost drivers / head"])
            ws.cell(ws.max_row, 1).font = BOLD
            has_vs = bool(DPAY.get("ref_label"))
            header(ws, ["Ingredient group", "Cost/Head", "% of tier cost"]
                   + ([f"vs {DPAY['ref_label']}"] if has_vs else []))
            for g in DPAY.get("groups", []):
                ws.append([g["label"], g.get("cogs_head"), _frac(g.get("share_pct"))]
                          + ([g.get("vs_ref")] if has_vs else []))
                ws.cell(ws.max_row, 2).number_format = NUM2
                ws.cell(ws.max_row, 3).number_format = PCT
                if has_vs:
                    ws.cell(ws.max_row, 4).number_format = NUM2
            ws.append([])
            header(ws, ["Top menu item", "Cost/Head", "% of tier cost", "Qty/100 guests"])
            for m in DPAY.get("menus", []):
                ws.append([_short_menu(m["label"]), m.get("cogs_head"),
                           _frac(m.get("share_pct")), m.get("qty_100")])
                ws.cell(ws.max_row, 2).number_format = NUM2
                ws.cell(ws.max_row, 3).number_format = PCT
                ws.cell(ws.max_row, 4).number_format = NUM
        autofit(ws)
    return {}


def _xlsx_payment(*, BOLD, NUM, PCT, autofit, header, j, pdelta, wb):
    # Sheet — Payment Channels (§10 of the printed report; the workbook used to
    # stop at price tiers, so the tender mix was the one section you could read on
    # paper but not in Excel).
    PAY = j.get("payment") or {}
    if PAY.get("available") and PAY.get("channels"):
        ws = wb.create_sheet("Payment Channels")
        ws.append([f"Tender mix — total {PAY.get('total'):,}"]); ws["A1"].font = BOLD
        ws.append([])
        prev_by = ((PAY.get("prev") or {}).get("channels") or {})
        has_pv = bool(prev_by)
        header(ws, ["Channel", "Amount", "Share %"] + (["Prev amount", "Prev share %"] if has_pv else []))
        for c in PAY["channels"]:
            row = [c["code"], c.get("amount"), pdelta(c.get("pct"))]
            if has_pv:
                pv = prev_by.get(c["code"]) or {}
                row += [pv.get("amount"), pdelta(pv.get("pct"))]
            ws.append(row)
            ws.cell(ws.max_row, 2).number_format = NUM
            ws.cell(ws.max_row, 3).number_format = PCT
            if has_pv:
                ws.cell(ws.max_row, 4).number_format = NUM
                ws.cell(ws.max_row, 5).number_format = PCT
        # branch x channel matrix — the same grid the report prints
        codes = [c["code"] for c in PAY["channels"]]
        if PAY.get("by_branch"):
            ws.append([])
            header(ws, ["Branch"] + codes + ["Total"])
            for b in PAY["by_branch"]:
                chans = b.get("channels") or {}
                ws.append([b["branch"]] + [(chans.get(c) or {}).get("amount") for c in codes] + [b.get("total")])
                for i in range(2, len(codes) + 3):
                    ws.cell(ws.max_row, i).number_format = NUM
        autofit(ws)
    return {}


def _xlsx_voice(*, BOLD, NUM, PCT, autofit, header, j, th, wb):
    # Sheet — Customer Voice (§12): the window's voices against the window before,
    # the themes, the branches to watch, and the fix loop with its verdicts.
    v = j.get("feedback")
    if not v:
        return {}
    ws = wb.create_sheet("Customer Voice")
    ws.append(["Customer voice" if not th else "เสียงลูกค้า", f"{v['range'][0]} → {v['range'][1]}",
               ("vs " if not th else "เทียบ ") + f"{v['prev_range'][0]} → {v['prev_range'][1]}"])
    ws["A1"].font = BOLD
    ws.append([])
    header(ws, ["Metric" if not th else "ตัวชี้วัด", "This period" if not th else "ช่วงนี้", "Previous" if not th else "ช่วงก่อน"])
    resp = v.get("response") or {}
    for k, a, b in [("Voices" if not th else "เสียง", v.get("voices"), v.get("prev_voices")),
                    ("Negative %" if not th else "เชิงลบ %", v.get("neg_pct"), v.get("prev_neg_pct")),
                    ("Avg rating" if not th else "คะแนนเฉลี่ย", v.get("avg_rating"), v.get("prev_avg_rating")),
                    ("Answered %" if not th else "ตอบกลับ %", resp.get("rate"), v.get("prev_response_rate")),
                    ("Awaiting reply now" if not th else "รอสาขาตอบตอนนี้", v.get("unreplied"), None),
                    ("Past SLA now" if not th else "เกิน SLA ตอนนี้", v.get("sla_breached"), None),
                    ("QR survey answers" if not th else "คำตอบแบบสอบถาม QR", (v.get("survey") or {}).get("n"), (v.get("survey") or {}).get("prev_n"))]:
        ws.append([k, a, b])
    ws.append([])
    header(ws, ["Theme" if not th else "หัวข้อ", "Voices" if not th else "เสียง", "Negative" if not th else "ลบ", "Previous" if not th else "ช่วงก่อน"])
    for t in v.get("themes") or []:
        ws.append([t["theme"], t["n"], t["neg"], t.get("prev_n")])
    ws.append([])
    header(ws, ["Branch to watch" if not th else "สาขาที่ต้องดู", "Voices" if not th else "เสียง", "Negative" if not th else "ลบ", "Negative %" if not th else "ลบ %", "Main theme" if not th else "หัวข้อหลัก"])
    for b in v.get("branches_watch") or []:
        ws.append([b["branch"], b["n"], b["neg"], b["neg_pct"], b.get("top_theme") or ""])
    if v.get("response_by_branch"):
        ws.append([])
        header(ws, ["Answered by branch" if not th else "ตอบกลับรายสาขา", "Answerable" if not th else "ตอบได้", "Answered" if not th else "ตอบแล้ว",
                    "Rate %" if not th else "อัตรา %", "Median days" if not th else "เวลากลาง (วัน)"])
        for x in v["response_by_branch"]:
            ws.append([x["branch"], x["answerable"], x["answered"], x.get("rate"), x.get("median_days")])
    a = v.get("actions") or {}
    ws.append([])
    header(ws, ["Fix loop" if not th else "งานแก้", "Open" if not th else "ค้าง", "Overdue" if not th else "เกินกำหนด", "Done" if not th else "เสร็จ",
                "Better" if not th else "ดีขึ้น", "Same" if not th else "เท่าเดิม", "Worse" if not th else "แย่ลง"])
    ws.append(["", (a.get("open") or 0) + (a.get("doing") or 0), a.get("overdue") or 0, a.get("done") or 0,
               a.get("better") or 0, a.get("same") or 0, a.get("worse") or 0])
    for it in a.get("items") or []:
        ws.append([it["title"], it.get("branch") or "", it.get("theme") or "", it.get("result") or "",
                   it.get("before_rate"), it.get("after_rate")])
    autofit(ws)
    return {}


def _xlsx_definitions(*, Alignment, BOLD, Font, P, header, j, th, wb):
    # Sheet — Definitions & Method. Same glossary the printed appendix carries, so a
    # workbook that leaves the building can still be read without the PDF next to it.
    ws = wb.create_sheet("Definitions")
    ws.append(["Report information" if not th else "ข้อมูลรายงาน"]); ws["A1"].font = Font(bold=True, size=12)
    for k, v in [
        ("Report" if not th else "ประเภทรายงาน",
         ({"daily": "รายวัน", "monthly": "รายเดือน"}.get(j["period"], "รายสัปดาห์") if th
          else {"daily": "Daily", "monthly": "Monthly"}.get(j["period"], "Weekly"))
         + " · " + str(j.get("label", ""))),
        ("Period covered" if not th else "ช่วงข้อมูล", " → ".join(j.get("range") or [])),
        ("Compared with" if not th else "เทียบกับ", j.get("comp_label", "")),
        ("Scope" if not th else "ขอบเขต", j.get("branch_scope", "")),
        ("Days in period" if not th else "จำนวนวัน", j.get("days_in_period")),
        ("Data through" if not th else "ข้อมูลถึงวันที่", j.get("latest_day", "")),
        ("Partial period" if not th else "ช่วงยังไม่สิ้นสุด", "yes" if j.get("partial") else "no"),
        ("Revenue basis" if not th else "ฐานรายได้",
         "Net sales ex-VAT (bill total / 1.07)" if not th else "ยอดขายสุทธิไม่รวม VAT (ยอดบิล ÷ 1.07)"),
        ("Cost basis" if not th else "ฐานต้นทุน",
         ("BoM recipe food cost" if not th else "ต้นทุนอาหารตามสูตร BoM") + " · " + str(P.get("month") or "")),
    ]:
        ws.append([k, v]); ws.cell(ws.max_row, 1).font = BOLD
    ws.append([])
    header(ws, ["Term", "Definition"] if not th else ["คำศัพท์", "นิยาม"])
    for term, definition in (_MGT_DEFS_TH if th else _MGT_DEFS_EN):
        ws.append([term, definition])
        ws.cell(ws.max_row, 1).font = BOLD
        ws.cell(ws.max_row, 2).alignment = Alignment(wrap_text=True, vertical="top")
    ws.column_dimensions["A"].width = 26
    ws.column_dimensions["B"].width = 92
    return {}


def _xlsx_actions(*, autofit, header, j, wb):
    # Sheet — Action Items
    ws = wb.create_sheet("Action Items")
    header(ws, ["Severity", "Action"])
    for a in j.get("actions", []):
        ws.append([a.get("sev", ""), a.get("text", "")])
    autofit(ws)
    return {}


def _xlsx_save(*, j, wb):
    # names this section hands on; None where a path never assigns one
    buf = None
    fname = None
    __import__('xl_safe').guard_workbook(wb); buf = _io.BytesIO(); wb.save(buf); buf.seek(0)
    tag = j.get("selected_month") or j.get("selected_week") or j.get("focus") or "report"
    fname = f"management_{j['period']}_{tag}.xlsx"
    return {"buf": buf, "fname": fname}


@router.get("/api/reports/management.xlsx")
def report_management_xlsx(
    period: str = Query("daily"),
    date:   Optional[str] = Query(None),
    week:   Optional[str] = Query(None),
    month:  Optional[str] = Query(None),
    branch: Optional[str] = Query(None),
    group:  Optional[str] = Query(None),
    lang:   str = Query("en"),               # match the on-screen language
    year:   Optional[str] = Query(None),     # PR-PP2: yearly
    mtd:    Optional[str] = Query(None),
):
    """Download the Daily/Weekly/Monthly/Yearly Management Summary as a multi-sheet Excel
    file. Reuses report_management() so the numbers match the on-screen report."""
    try:
        import openpyxl
        from openpyxl.styles import Font, PatternFill, Alignment
        from openpyxl.utils import get_column_letter
    except ImportError:
        raise HTTPException(status_code=500, detail="openpyxl not installed")
    # report_management is @ttl_cache-wrapped → returns a Response(bytes); parse its body.
    resp = report_management(period=period, date=date, week=week, month=month,
                             branch=branch, group=group, lang=lang, mtd=mtd, year=year)
    j = _json.loads(resp.body) if hasattr(resp, "body") else resp
    if not j.get("available"):
        raise HTTPException(status_code=404, detail="No sales data for this period")
    th = (j.get("lang") == "th")          # the Definitions sheet follows the report language

    # Each stage is its own function (_xlsx_*, Phase 8b 2026-09); they run in
    # order and hand what they compute forward through `ctx`. The statements
    # are the ones this block used to hold inline; the goldens pin the output.
    ctx = {"Alignment": Alignment, "Font": Font, "PatternFill": PatternFill, "get_column_letter": get_column_letter, "j": j, "openpyxl": openpyxl, "th": th}
    ctx.update(_xlsx_setup(Alignment=ctx.get("Alignment"), Font=ctx.get("Font"), PatternFill=ctx.get("PatternFill"), get_column_letter=ctx.get("get_column_letter"), j=ctx.get("j"), openpyxl=ctx.get("openpyxl")))
    ctx.update(_xlsx_summary(Font=ctx.get("Font"), H=ctx.get("H"), NUM=ctx.get("NUM"), P=ctx.get("P"), PCT=ctx.get("PCT"), autofit=ctx.get("autofit"), header=ctx.get("header"), j=ctx.get("j"), pdelta=ctx.get("pdelta"), wb=ctx.get("wb")))
    ctx.update(_xlsx_branches(NUM=ctx.get("NUM"), PCT=ctx.get("PCT"), autofit=ctx.get("autofit"), header=ctx.get("header"), j=ctx.get("j"), pdelta=ctx.get("pdelta"), wb=ctx.get("wb")))
    ctx.update(_xlsx_leak(NUM=ctx.get("NUM"), autofit=ctx.get("autofit"), header=ctx.get("header"), j=ctx.get("j"), wb=ctx.get("wb")))
    ctx.update(_xlsx_menu(BOLD=ctx.get("BOLD"), NUM2=ctx.get("NUM2"), PCT=ctx.get("PCT"), autofit=ctx.get("autofit"), header=ctx.get("header"), j=ctx.get("j"), pdelta=ctx.get("pdelta"), wb=ctx.get("wb")))
    ctx.update(_xlsx_trend(NUM=ctx.get("NUM"), autofit=ctx.get("autofit"), header=ctx.get("header"), j=ctx.get("j"), wb=ctx.get("wb")))
    ctx.update(_xlsx_peak(NUM=ctx.get("NUM"), PCT=ctx.get("PCT"), autofit=ctx.get("autofit"), header=ctx.get("header"), j=ctx.get("j"), pdelta=ctx.get("pdelta"), wb=ctx.get("wb")))
    ctx.update(_xlsx_table(autofit=ctx.get("autofit"), header=ctx.get("header"), j=ctx.get("j"), wb=ctx.get("wb")))
    ctx.update(_xlsx_tier(BOLD=ctx.get("BOLD"), NUM=ctx.get("NUM"), NUM2=ctx.get("NUM2"), PCT=ctx.get("PCT"), autofit=ctx.get("autofit"), header=ctx.get("header"), j=ctx.get("j"), pdelta=ctx.get("pdelta"), wb=ctx.get("wb")))
    ctx.update(_xlsx_payment(BOLD=ctx.get("BOLD"), NUM=ctx.get("NUM"), PCT=ctx.get("PCT"), autofit=ctx.get("autofit"), header=ctx.get("header"), j=ctx.get("j"), pdelta=ctx.get("pdelta"), wb=ctx.get("wb")))
    ctx.update(_xlsx_voice(BOLD=ctx.get("BOLD"), NUM=ctx.get("NUM"), PCT=ctx.get("PCT"), autofit=ctx.get("autofit"), header=ctx.get("header"), j=ctx.get("j"), th=ctx.get("th"), wb=ctx.get("wb")))
    ctx.update(_xlsx_definitions(Alignment=ctx.get("Alignment"), BOLD=ctx.get("BOLD"), Font=ctx.get("Font"), P=ctx.get("P"), header=ctx.get("header"), j=ctx.get("j"), th=ctx.get("th"), wb=ctx.get("wb")))
    ctx.update(_xlsx_actions(autofit=ctx.get("autofit"), header=ctx.get("header"), j=ctx.get("j"), wb=ctx.get("wb")))
    ctx.update(_xlsx_save(j=ctx.get("j"), wb=ctx.get("wb")))
    buf = ctx["buf"]
    fname = ctx["fname"]
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename={fname}"},   # ASCII by construction (management_<period>_<tag>)
    )
