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
from fastapi import HTTPException, Query, Response
from fastapi.responses import StreamingResponse
from typing import Optional
import json as _json
import io as _io
from rules import ALL_TIER_CODES_SQL as _ALL_TIER_CODES_SQL, EASY_TIER_CODES_SQL as _EASY_TIER_CODES_SQL, VAT_DIVISOR, discount_base_row, discount_excess, discount_policy_pct, effective_unit_cost_sql, net_revenue_row, net_revenue_sum, raw_unit_cost_sql, scope_filter, tier_code_sql
from rules import GRAND_MENU_FROM, TIER_EXTRA, tier_codes_between, tier_label
from targets import target_revenue_total
from core import (
    HERE,
    _FOOD_CATEGORY_FILTER, _build_sku_effective_cost, _cogs_case, _iso_week_sql, _unified_cogs_expr,
    get_db, ttl_cache,
)

from fastapi import APIRouter
router = APIRouter()


# ── Top-60 menu cost-per-head · MASTER branches (overview → per-branch → variance) ──
# Powers the "💸 Top 60 ต้นทุน/หัว · สาขา Master" report tab. COGS via the LIVE
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
# 🧪 Menu Impact (วิเคราะห์วัตถุดิบ) — with/without per-head analysis of one menu
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
    the 🧪 tab offers, and the only values the filter accepts for that window."""
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
# 🏷️ Price-tier analysis (วิเคราะห์ Tier ราคาขายบุฟเฟ่ต์) — section of the 🧪 tab.
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


def _pt_bom_cost_by_tier(con):
    """{tier: {bom_avg_cost, bom_dish_count}} — average RECIPE cost of the dishes
    assigned to each tier (sku_tier x sku_effective_cost), independent of what
    actually sold in any date range. Only the 2026-09-01+ Grand Menu carries a
    tier per SKU (à la carte and the pre-Sep menu have none), so older tiers
    simply have no entry here."""
    out = {}
    try:
        for tier, avg_cost, n in con.execute(
                """SELECT st.tier, AVG(sec.recipe_cost), COUNT(*)
                   FROM sku_tier st JOIN sku_effective_cost sec USING(sku_id)
                   GROUP BY st.tier"""):
            out[str(tier)] = {"bom_avg_cost": round(avg_cost, 2) if avg_cost is not None else None,
                              "bom_dish_count": n}
    except Exception:
        pass                      # sku_tier not created yet (pre-migration DB copy)
    return out


def _pt_bom_dishes_by_tier(con):
    """{tier: [{sku_code, label, bom_cost}, …]}, richest recipe cost first —
    every dish assigned to that tier with its OWN cost, so a reader can see
    which dishes pull a tier's average up or down instead of only the blend.
    Same source/scope as _pt_bom_cost_by_tier (sku_tier x sku_effective_cost)."""
    out = {}
    try:
        for tier, code, label, cost in con.execute(
                """SELECT st.tier, s.sku_code, s.description, sec.recipe_cost
                   FROM sku_tier st JOIN skus s USING(sku_id)
                   JOIN sku_effective_cost sec USING(sku_id)
                   ORDER BY st.tier, sec.recipe_cost DESC"""):
            out.setdefault(str(tier), []).append(
                {"sku_code": code, "label": label or code,
                 "bom_cost": round(cost, 2) if cost is not None else None})
    except Exception:
        pass                      # sku_tier not created yet (pre-migration DB copy)
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

        # Monthly trend x tier over the full coverage (scope-filtered) — but when
        # the analysis sits on the live menu, the trend starts at the changeover
        # too, so the stacked mix chart can't put a retired ฿799 band next to a
        # live ฿789 one.
        trend_from = _PT_NEW_MENU_FROM if a_from >= _PT_NEW_MENU_FROM else lo
        trend = {}
        for ym, t, px, cg, rv in con.execute(
                f"""SELECT substr(day,1,7), tier, SUM(pax), SUM(cogs), SUM(rev)
                    FROM menu_fact_total WHERE branch_id IN ({ph}) AND day >= ?
                    GROUP BY substr(day,1,7), tier""", bids + [trend_from]):
            m = trend.setdefault(ym, {}).setdefault(str(t or "promo"),
                                                    {"pax": 0.0, "cogs": 0.0, "rev": 0.0})
            m["pax"] += px or 0.0; m["cogs"] += cg or 0.0; m["rev"] += rv or 0.0

        # Weekly trend x tier — the last _PT_TREND_WEEKS ISO weeks of the coverage.
        # Monthly is too coarse to see a tier moving right after a menu change (the
        # Grand Menu is only ~5 weeks old), so the same two charts get a weekly
        # granularity. _iso_week_sql is the app-wide week bucket (Mon-Sun, correct
        # across the Jan-1 boundary) — the same one /api/sales/daily and the P&L use,
        # so a week here means the same days a week means everywhere else.
        wk_expr = _iso_week_sql("day")
        # The weekly trend deliberately does NOT take the monthly chart's era clamp.
        # Clamped to 2026-09-01 it would today hold two partial weeks, which is not
        # a trend — and 8 weeks of weekly detail is the whole point of this chart.
        # What the clamp protects against is a retired ฿799 band sitting next to a
        # live ฿789 one as if they were the same product; here the changeover is
        # instead marked on the axis (era_from below) so a price list that was
        # renamed can never be misread as guests moving between tiers. The monthly
        # chart keeps the clamp, so the conservative view is still one click away.
        # Split by ERA OF THE DAY, not of the week: "999" exists on BOTH price lists
        # and means different money on each (old ฿999 is quoted before the 5% service
        # charge and 7% VAT, Grand ฿999 has both inside). Drawn as one series it would
        # look like one product drifting, so pre-changeover tiers get the _PT_OLD_ERA
        # suffix and become their own series that simply stops at the changeover. The
        # week containing 1 Sep legitimately carries both.
        week_rows = con.execute(
            f"""SELECT {wk_expr} wk, tier, day < ? AS old_era, SUM(pax), SUM(cogs), SUM(rev)
                FROM menu_fact_total WHERE branch_id IN ({ph})
                GROUP BY wk, tier, old_era ORDER BY wk""",
            [_PT_NEW_MENU_FROM] + bids).fetchall()
        # Days actually present per week. The newest week is nearly always partial
        # (the sync runs a day behind) and the oldest is cut by trend_from, and a
        # 2-day week plotted next to 7-day ones reads as a collapse in covers that
        # never happened — so each point carries its own day count and the chart
        # marks the short ones instead of letting them look like a trend.
        wk_days = dict(con.execute(
            f"""SELECT {wk_expr} wk, COUNT(DISTINCT day) FROM menu_fact_total
                WHERE branch_id IN ({ph}) GROUP BY wk""", bids))
        # ISO week holding the menu changeover — the frontend draws the marker there.
        # Computed in Python: _iso_week_sql() names its column twice, so feeding it
        # a '?' placeholder would need the date bound twice.
        _ec = date.fromisoformat(_PT_NEW_MENU_FROM).isocalendar()
        era_week = f"{_ec[0]}-W{_ec[1]:02d}"
        # The window ENDS at the week the analysis ends in, not at the end of the
        # coverage: a June report must show June's weeks, not the newest eight weeks
        # in the database. ISO labels are zero-padded, so a string compare orders
        # them correctly.
        _aw = dA_to.isocalendar()
        a_week = f"{_aw[0]}-W{_aw[1]:02d}"
        all_weeks = sorted(w for w in {r[0] for r in week_rows} if w <= a_week)
        keep_weeks = set(all_weeks[-_PT_TREND_WEEKS:])
        weeks_available = len(all_weeks)
        # Only label an era when the window actually SHOWS both. A report sitting
        # entirely before the changeover is looking at the menu that was current
        # then — tagging every one of its tiers "(เมนูเก่า)" would be wrong, and a
        # window entirely after it has nothing to distinguish. The suffix exists
        # to separate two price lists on one axis, so it earns its place only when
        # two price lists are on that axis.
        eras = {bool(r[2]) for r in week_rows if r[0] in keep_weeks}
        straddles = len(eras) > 1
        trend_wk = {}
        for wk, t, old_era, px, cg, rv in week_rows:
            if wk not in keep_weeks:
                continue
            key = str(t or "promo") + (_PT_OLD_ERA if (old_era and straddles) else "")
            m = trend_wk.setdefault(wk, {}).setdefault(key,
                                                       {"pax": 0.0, "cogs": 0.0, "rev": 0.0})
            m["pax"] += px or 0.0; m["cogs"] += cg or 0.0; m["rev"] += rv or 0.0

        bom_by_tier = _pt_bom_cost_by_tier(con)
        bom_dishes_by_tier = _pt_bom_dishes_by_tier(con)

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
        tiers.append({
            "tier": t, "label": _pt_label(t), "a": A, "b": B,
            "bom_avg_cost": bt["bom_avg_cost"] if bt else None,
            "bom_dish_count": bt["bom_dish_count"] if bt else None,
            "bom_dishes": bom_dishes_by_tier.get(t, []),
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

    # ── trend payloads: same shape monthly and weekly, one shaper ──
    def _trend_points(buckets, key, days=None):
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

    trend_month = _trend_points(trend, "ym")
    trend_week = _trend_points(trend_wk, "wk", wk_days)
    # How many weeks the era guard actually left us: on the Grand Menu the weekly
    # trend starts at the changeover, so it fills up to _PT_TREND_WEEKS over time
    # rather than reaching back into a price list that is no longer sold.
    wk_seen = {t for pt in trend_week for t, v in pt["tiers"].items() if v["pax"]}
    _ord = {t: i for i, t in enumerate(_PT_ORDER)}
    wk_tier_keys = sorted(wk_seen, key=lambda k: (k.endswith(_PT_OLD_ERA),
                                                  _ord.get(k.replace(_PT_OLD_ERA, ""), 99)))
    wk_tier_labels = {k: _pt_label(k.replace(_PT_OLD_ERA, ""))
                      + (" (เมนูเก่า)" if k.endswith(_PT_OLD_ERA) else "")
                      for k in wk_tier_keys}
    trend_week_meta = {"weeks_shown": len(trend_week), "weeks_wanted": _PT_TREND_WEEKS,
                       "tier_keys": wk_tier_keys, "tier_labels": wk_tier_labels,
                       "old_suffix": _PT_OLD_ERA, "straddles_menu_change": straddles,
                       "weeks_available": weeks_available,
                       "from": trend_week[0]["wk"] if trend_week else None,
                       "era_week": era_week if straddles else None,
                       "era_from": _PT_NEW_MENU_FROM if straddles else None}

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
                    f"— ตัวเลขด้านล่างเป็นข้อมูลช่วง {a_from} ถึง {a_to} เดี่ยวๆ ระบบจะกลับมาเปรียบเทียบอัตโนมัติ "
                    f"เมื่อทั้งสองช่วงอยู่หลังวันที่เมนูเปลี่ยนแล้ว (เช่น ต.ค. เทียบ ก.ย.)"]
        # No "vs previous period" is possible yet, but A's own numbers don't need
        # one — surface the highest food-cost tier as a real, actionable finding
        # rather than leaving this card empty until next month.
        actions = []
        real_now = [x for x in tiers if str(x["tier"]).isdigit() and x["a"]["food_pct"] is not None]
        worst_now = max(real_now, key=lambda x: x["a"]["food_pct"], default=None)
        if worst_now and worst_now["a"]["food_pct"] >= 40:
            actions.append({"title": f"ทบทวนความคุ้มของ tier {worst_now['label']}",
                            "detail": f"food cost {worst_now['a']['food_pct']}% ของรายได้ "
                                      f"(GP {fb(worst_now['a']['gp_head'])}/หัว) ในช่วง {a_from} ถึง {a_to} — "
                                      f"ยังไม่มีเดือนก่อนเทียบเพื่อดูเทรนด์ แต่ใช้ตัวเลขนี้ตั้งเป็นเป้าคุมต้นทุนของเมนูใหม่ได้เลย"})
        if not actions:
            actions.append({"title": "เก็บตัวเลขเดือนนี้ไว้เป็นฐาน (baseline)",
                            "detail": f"เมนูใหม่เริ่ม {_PT_NEW_MENU_FROM} — ต้นทุน/หัวและ BoM เฉลี่ย/จานต่อ tier "
                                      f"ด้านล่างคือฐานของเมนูใหม่ พอถึงเดือนถัดไปที่มีข้อมูลเทียบได้ ระบบจะวิเคราะห์แนวโน้มให้อัตโนมัติ"})
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
                actions.append({"title": f"กู้ยอดลูกค้า Tier {lose['label']}",
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
            actions.append({"title": "ดัน upsell ไป tier ที่สูงขึ้น",
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
            actions.append({"title": f"คุมต้นทุน/หัว tier {x['label']}",
                            "detail": f"ต้นทุนอาหาร/หัวเพิ่ม {fb(x['d_cogs_head'])} เทียบ{period_word}"
                                      + (f" ตัวดันหลัก: {dtxt} — คุมพอร์ชัน/ความถี่เติมของเมนูเหล่านี้, "
                                         f"เช็คของเสียครัว และทบทวนต้นทุนวัตถุดิบกับซัพพลายเออร์" if dtxt else
                                         " — ไล่ดูเมนู Top-30 ของ tier นี้ในตารางด้านล่าง")})
    worst_fp = max((x for x in real_tiers if x["a"]["food_pct"]), key=lambda x: x["a"]["food_pct"], default=None)
    if worst_fp and worst_fp["a"]["food_pct"] >= 40:
        insights.append(f"Tier {worst_fp['label']} food cost สูงสุด {worst_fp['a']['food_pct']}% "
                        f"(GP/หัว {fb(worst_fp['a']['gp_head'])})")
        actions.append({"title": f"ทบทวนความคุ้มของ tier {worst_fp['label']}",
                        "detail": f"food cost {worst_fp['a']['food_pct']}% ของรายได้ (GP เหลือ {fb(worst_fp['a']['gp_head'])}/หัว) — "
                                  f"พิจารณาปรับไลน์เมนูเฉพาะ tier (สลับเมนูต้นทุนสูงเป็นเมนูภาพลักษณ์ดีแต่ต้นทุนต่ำกว่า), "
                                  f"หรือทดลองขึ้นราคาแบบ A/B บางสาขา"})
    hc_up = [x for x in real_tiers if x["d_hc_head"] is not None and x["d_hc_head"] >= 2]
    if hc_up:
        t0 = max(hc_up, key=lambda x: x["d_hc_head"])
        insights.append(f"การทานเมนูแพง (ต้นทุน ≥฿30) tier {t0['label']} เพิ่ม {fb(t0['d_hc_head'])}/หัว")
        actions.append({"title": f"เบรกเมนูต้นทุนสูงใน tier {t0['label']}",
                        "detail": f"เมนูแพง/หัวเพิ่ม {fb(t0['d_hc_head'])} — ใช้เทคนิค menu placement "
                                  f"(วางเมนูข้าว/ของดองไว้ต้น ๆ), ลดขนาดพอร์ชันต่อจาน (เติมได้แต่ชะลอ), "
                                  f"และตรวจว่าสาขาไหนปล่อยเมนูแพงเกินเพื่อน"})
    rising = [r for r in top30 if (r["d_qty_head_pct"] or 0) >= 10 and (r["cogs_head"] or 0) >= 1]
    if rising:
        names = ", ".join(f"{r['label']} (+{r['d_qty_head_pct']}%)" for r in rising[:4])
        insights.append(f"เมนูต้นทุนหลักที่ถูกทานเพิ่มชัดเจน: {names}")
        actions.append({"title": "จัดการเมนูที่การทานพุ่ง",
                        "detail": f"{names} — ถ้าเป็นเมนูดึงลูกค้า (เช็คจากแท็บวิเคราะห์วัตถุดิบว่า GP/หัวของบิลที่สั่งไม่แย่ลง) "
                                  f"ให้คงไว้แต่ล็อกต้นทุนซื้อ; ถ้าไม่ใช่ ให้ลดความถี่เติม/ขนาดเสิร์ฟ"})
    if not actions:
        actions.append({"title": "โครงสร้าง tier แข็งแรง",
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
               headers={"Content-Disposition": f"attachment; filename=TOP60_COST_PER_HEAD_{scope}_{month}.xlsx"})


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


def _pnl_bounds(d, gran):
    import datetime as _dt
    if gran == "day": return d, d
    if gran == "week":
        sd = d - _dt.timedelta(days=d.weekday()); return sd, sd + _dt.timedelta(days=6)
    sd = d.replace(day=1)
    nxt = sd.replace(year=sd.year + 1, month=1) if sd.month == 12 else sd.replace(month=sd.month + 1)
    return sd, nxt - _dt.timedelta(days=1)


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
        focus = (date or maxd)[:10]
        fy, fm, fd = (int(x) for x in focus.split("-"))
        s, e = _pnl_bounds(_dt.date(fy, fm, fd), gran)
        JN = ("FROM sales_transaction t JOIN sale_bill_cogs c ON c.receipt_no=t.receipt_no "
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
    COGS via our BoM only (sale_bill_cogs). Limited to the costed rolling window."""
    import datetime as _dt
    if gran not in ("day", "week", "month"):
        gran = "day"
    tfilt = ["t.source='Easy'", "b.name!='ALL'"]; tp: dict = {}
    scope_filter(tfilt, tp, branch, group)
    tw = " AND ".join(tfilt)

    def bounds(d):
        if gran == "day": return d, d
        if gran == "week":
            s = d - _dt.timedelta(days=d.weekday()); return s, s + _dt.timedelta(days=6)
        s = d.replace(day=1)
        nxt = s.replace(year=s.year + 1, month=1) if s.month == 12 else s.replace(month=s.month + 1)
        return s, nxt - _dt.timedelta(days=1)

    with get_db() as con:
        try:
            _w = con.execute("SELECT MIN(day) lo, MAX(day) hi FROM sale_bill_cogs").fetchone()
            maxd = _w["hi"] if _w else None
            mind = _w["lo"] if _w else None
        except Exception:
            return {"focus": None, "note": "no per-bill cost data", "summary": {}, "dist": [], "worst": []}
        if not maxd:
            return {"focus": None, "summary": {}, "dist": [], "worst": []}
        focus = (date or maxd)[:10]
        fy, fm, fd = (int(x) for x in focus.split("-")); fdate = _dt.date(fy, fm, fd)
        s, e = bounds(fdate)
        JN = (f"FROM sales_transaction t JOIN sale_bill_cogs c ON c.receipt_no=t.receipt_no "
              f"JOIN branches b ON b.branch_id=t.branch_id "
              f"WHERE {tw} AND date(t.sold_at) BETWEEN :s AND :e AND t.total_inc_vat>0")
        params = {**tp, "s": s.isoformat(), "e": e.isoformat()}
        r = con.execute(f"SELECT COUNT(*) bills, ROUND(SUM({net_revenue_row('t')}),2) rev, "
                        f"ROUND(SUM(c.cogs_bom),2) cogs, SUM(t.customer_count) cust, "
                        f"ROUND(SUM(t.discount),2) disc {JN}", params).fetchone()
        bills = r["bills"] or 0; rev = r["rev"] or 0; cogs = r["cogs"] or 0; cust = r["cust"] or 0
        gp = round(rev - cogs, 2); gpp = round(gp / rev * 100, 1) if rev else None
        summary = {"bills": bills, "revenue": rev, "cogs": cogs, "gross_profit": gp, "gp_pct": gpp,
                   "discount": r["disc"] or 0, "customers": cust,
                   "gp_per_bill": round(gp / bills) if bills else None,
                   "gp_per_head": round(gp / cust) if cust else None}
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
    return {"focus": focus, "gran": gran, "range": [s.isoformat(), e.isoformat()],
            "summary": summary, "dist": dist, "worst": worst, "by_branch": by_branch,
            "loss": loss, "loss_count": (ls["n"] or 0) if ls else 0,
            "loss_total": (ls["loss"] or 0) if ls else 0, "loss_capped": len(loss) >= 2000,
            "data_window": [mind, maxd],
            "out_of_window": bool(mind and e.isoformat() < mind) or bool(maxd and s.isoformat() > maxd)}


@router.get("/api/reports/pnl-bill-items")
def pnl_bill_items(receipt: str = Query(..., description="receipt_no of the bill to drill into")):
    """Menu lines for a single (loss-making) bill: qty, per-unit cost, ex-VAT revenue,
    COGS and per-line gross profit — so you can see exactly what the customer ordered
    that drove COGS above revenue. Source: sale_bill_item (qty/rev staged by the sync
    for LOSS bills only). COGS is recomputed LIVE per line as qty × sku_effective_cost
    (cost-management table first) so the drill always reflects current costs, not the
    snapshot frozen at sync time. `synced` is False until the next sync builds the table."""
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
            f"""SELECT t.receipt_no, b.name branch, t.sold_at, t.customer_count cust,
                      ROUND({net_revenue_row('t')}) rev, ROUND(c.cogs_bom) cogs,
                      ROUND(COALESCE(t.discount,0) + COALESCE(t.sp_discount,0)) disc
               FROM sales_transaction t
               LEFT JOIN sale_bill_cogs c ON c.receipt_no=t.receipt_no
               JOIN branches b ON b.branch_id=t.branch_id WHERE t.receipt_no=:r""",
            {"r": receipt}).fetchone()
        if hdr:
            header = {"receipt": hdr["receipt_no"], "branch": hdr["branch"], "sold_at": hdr["sold_at"],
                      "cust": hdr["cust"] or 0, "rev": hdr["rev"] or 0, "cogs": hdr["cogs"] or 0,
                      "disc": hdr["disc"] or 0}
        items = []
        if synced:
            _build_sku_effective_cost(con, force=False)   # ensure the lookup exists
            items = [{"menu": (x["description"] or x["sku_code"] or "—").replace("(B) ", "").replace("(A) ", ""),
                      "sku_code": x["sku_code"], "qty": x["qty"] or 0,
                      "unit_cost": round(x["uc"] or 0, 2), "rev": x["rev"] or 0,
                      "cogs": x["cogs"] or 0, "margin": round((x["rev"] or 0) - (x["cogs"] or 0))}
                     for x in con.execute(
                        f"""SELECT s.description, s.sku_code, ROUND(SUM(i.qty),2) qty,
                                  ROUND(SUM(i.rev)) rev,
                                  CASE WHEN s.sku_code IN {_EASY_TIER_CODES_SQL} THEN 0
                                       ELSE COALESCE((SELECT sec.recipe_cost FROM sku_effective_cost sec
                                            WHERE sec.sku_id=i.sku_id), 0) END uc,
                                  ROUND(SUM(i.qty) * CASE WHEN s.sku_code IN {_EASY_TIER_CODES_SQL} THEN 0
                                       ELSE COALESCE((SELECT sec.recipe_cost FROM sku_effective_cost sec
                                            WHERE sec.sku_id=i.sku_id), 0) END) cogs
                           FROM sale_bill_item i JOIN skus s ON s.sku_id=i.sku_id
                           WHERE i.receipt_no=:r GROUP BY i.sku_id ORDER BY cogs DESC, rev DESC""",
                        {"r": receipt}).fetchall()]
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
def _mgt_window(con, *, DOW, MON, _cal, _dt, date, dlabel, month, params, period, th, week, where):
    # names this section hands on; None where a period path never assigns one
    ce = None
    comp_label = None
    cs = None
    days_in_period = None
    focus = None
    has_prev = None
    has_yoy = None
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
    weeks = None
    ye = None
    ys = None
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
    weeks: list = []
    selected_week = None
    months: list = []
    selected_month = None
    if period == "daily":
        cs = ce = fdate
        ps = pe = fdate - _dt.timedelta(days=7)
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
            if _dt.date(my, mm, _cal.monthrange(my, mm)[1]) <= latest_day:
                months.append({"month": m, "label": f"{MON[mm-1]} {my}"})
            if len(months) >= 24:
                break
        # Default = latest complete month; fall back to the latest data month
        # (flagged partial) only when no month has completed yet.
        sel = months[0]["month"] if months else maxd[:7]
        if month:
            m10 = month[:7]
            if any(x["month"] == m10 for x in months):
                sel = m10
        sy, sm = int(sel[:4]), int(sel[5:7])
        cs = _dt.date(sy, sm, 1)
        ce = _dt.date(sy, sm, _cal.monthrange(sy, sm)[1])
        days_in_period = (ce - cs).days + 1
        partial = ce > latest_day
        ref_anchor = ce
        pm_l = cs - _dt.timedelta(days=1)          # previous calendar month
        ps = pm_l.replace(day=1)
        pe = pm_l
        selected_month = sel
        if th:
            label = f"เดือน {MON[sm-1]} {sy}"
            comp_label = "เดือนก่อนหน้า"
        else:
            label = f"{MON[sm-1]} {sy}"
            comp_label = "previous month"
    else:
        # FULL weeks only (Mon–Sun). First, the selectable COMPLETED weeks that
        # have data — most recent first, cap 26 — so the picker and the default
        # both come from real, finished weeks (never the current partial week).
        mon_expr = "date(st.sold_at, '-' || ((CAST(strftime('%w', st.sold_at) AS INT)+6)%7) || ' days')"
        monrows = con.execute(
            f"SELECT DISTINCT {mon_expr} mon FROM sales_transaction st "
            f"JOIN branches b ON b.branch_id=st.branch_id WHERE {where}", params).fetchall()
        for m in sorted((r["mon"] for r in monrows if r["mon"]), reverse=True):
            md = _dt.date.fromisoformat(m)
            sun = md + _dt.timedelta(days=6)
            if sun <= latest_day:
                weeks.append({"start": m, "end": sun.isoformat(),
                              "label": f"{dlabel(md)} – {dlabel(sun)}"})
            if len(weeks) >= 26:
                break
        # Default = most recent completed week WITH data; fall back to the calendar
        # week before the latest day if there is no fully-completed week yet.
        cur_mon = latest_day - _dt.timedelta(days=latest_day.weekday())
        default_mon = (_dt.date.fromisoformat(weeks[0]["start"]) if weeks
                       else cur_mon - _dt.timedelta(days=7))
        sel_mon = default_mon
        if week:
            try:
                wd = _dt.date.fromisoformat(week[:10])
                cand = wd - _dt.timedelta(days=wd.weekday())          # snap to that week's Monday
                if (cand + _dt.timedelta(days=6)) <= latest_day:      # never an incomplete week
                    sel_mon = cand
            except Exception:
                pass
        cs = sel_mon
        ce = sel_mon + _dt.timedelta(days=6)
        days_in_period = 7
        partial = False
        ref_anchor = ce
        ps = cs - _dt.timedelta(days=7)
        pe = cs - _dt.timedelta(days=1)
        selected_week = cs.isoformat()
        if th:
            label = f"สัปดาห์ {dlabel(cs)} – {dlabel(ce)} (จ.–อา.)"
            comp_label = "สัปดาห์ก่อนหน้า (จ.–อา.)"
        else:
            label = f"Week of {dlabel(cs)} – {dlabel(ce)} (Mon–Sun)"
            comp_label = "previous week (Mon–Sun)"
    has_prev = ps.isoformat() >= mind
    # YoY window — 364 days back (exactly 52 weeks) for BOTH daily and weekly, so the
    # weekday stays aligned. Restaurant revenue is strongly weekday-seasonal, so a
    # same-calendar-date YoY would drift (e.g. Sat→Fri) and distort the comparison.
    # Dropped if it predates the data. Monthly compares the SAME CALENDAR month
    # last year (full month vs full month — the standard for monthly reporting;
    # weekday drift washes out over a whole month).
    if period == "daily":
        ys = ye = fdate - _dt.timedelta(days=364)
    elif period == "monthly":
        ys = cs.replace(year=cs.year - 1)
        ye = ys.replace(day=_cal.monthrange(ys.year, ys.month)[1])
    else:
        ys = cs - _dt.timedelta(days=364); ye = ce - _dt.timedelta(days=364)
    has_yoy = ys.isoformat() >= mind
    return {"ce": ce, "comp_label": comp_label, "cs": cs, "days_in_period": days_in_period, "focus": focus, "has_prev": has_prev, "has_yoy": has_yoy, "label": label, "latest_day": latest_day, "maxd": maxd, "mind": mind, "months": months, "partial": partial, "pe": pe, "ps": ps, "ref_anchor": ref_anchor, "selected_month": selected_month, "selected_week": selected_week, "weeks": weeks, "ye": ye, "ys": ys}


def _mgt_headline(con, *, ce, cs, has_prev, has_yoy, params, pct, pe, ps, where, ye, ys):
    # names this section hands on; None where a period path never assigns one
    cur = None
    headline = None
    prev = None
    # ---- headline totals for an arbitrary [s,e] range -----------------
    def agg(s, e):
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
    yoy = agg(ys, ye) if has_yoy else None
    if yoy:
        headline["yoy"] = yoy
        headline["rev_yoy_pct"]  = pct(cur["revenue"], yoy["revenue"])
        headline["cust_yoy_pct"] = pct(cur["customers"], yoy["customers"])
        headline["rph_yoy_pct"]  = pct(cur["rev_per_head"] or 0, yoy["rev_per_head"]) if yoy["rev_per_head"] else None
    return {"cur": cur, "headline": headline, "prev": prev}


def _mgt_branches(con, *, ce, cs, has_prev, params, pct, pe, ps, where):
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
    tot_rev = sum((r["rev"] or 0) for r in cur_b.values()) or 0
    branches = []
    for nm, r in cur_b.items():
        rev = r["rev"] or 0; cust = r["cust"] or 0
        p = prev_b.get(nm)
        branches.append({
            "branch": nm, "revenue": rev, "customers": cust, "bills": r["bills"],
            "rev_per_head": round(rev/cust, 1) if cust else None,
            "share_pct": round(rev/tot_rev*100, 1) if tot_rev else None,
            "wow_pct": pct(rev, p["rev"] or 0) if p else None,
        })
    branches.sort(key=lambda x: -x["revenue"])
    prev_rank_map = {nm: i + 1 for i, (nm, _) in
                     enumerate(sorted(prev_b.items(), key=lambda kv: -(kv[1]["rev"] or 0)))}
    for i, b in enumerate(branches):
        b["rank"] = i + 1
        pr = prev_rank_map.get(b["branch"])
        b["rank_change"] = (pr - (i + 1)) if pr else None  # positive = moved up
    return {"branches": branches}


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
            heavy_sub = (f"SELECT st.branch_id, st.receipt_no, st.discount, b.name AS branch "
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


def _mgt_menu(con, *, _cal, _dt, branch, ce, cs, cur, group, has_prev, leak, params, pct, pe, prev, ps, where):
    # names this section hands on; None where a period path never assigns one
    cmp_ok = None
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
    _eff_cost = effective_unit_cost_sql('d')
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
        """→ (rows_by_sku_id, source) where source ∈ daily|monthly|unavailable."""
        if s.isoformat() >= daily_min:
            rows = con.execute(f"""
                SELECT d.sku_id, s.description, s.sku_code,
                       SUM(d.qty) qty, SUM(d.qty * {_eff_cost}) cost
                FROM v_sales_sku_daily d JOIN branches b ON b.branch_id=d.branch_id
                JOIN skus s ON s.sku_id=d.sku_id
                WHERE {sw} AND d.day BETWEEN :s AND :e AND d.sku_id IN {food_sub}
                GROUP BY d.sku_id""",
                {**sp, "s": s.isoformat(), "e": e.isoformat()}).fetchall()
            return {r["sku_id"]: r for r in rows}, "daily"
        if _whole_months(s, e):
            rows = con.execute(f"""
                SELECT d.sku_id, s.description, s.sku_code,
                       SUM(d.qty) qty, SUM(d.qty * {_eff_cost}) cost
                FROM v_sales_sku_monthly d JOIN branches b ON b.branch_id=d.branch_id
                JOIN skus s ON s.sku_id=d.sku_id
                WHERE {sw} AND d.period_month BETWEEN :s AND :e
                  AND d.sku_id IN {food_sub}
                GROUP BY d.sku_id""",
                {**sp, "s": s.strftime("%Y-%m"), "e": e.strftime("%Y-%m")}).fetchall()
            return {r["sku_id"]: r for r in rows}, "monthly"
        return {}, "unavailable"
    def clean(nm):
        return (nm or "").replace("(A) ", "").replace("(B) ", "").strip()
    covers_cur  = cur["customers"] or 0
    covers_prev = (prev["customers"] or 0) if prev else 0
    try:
        cur_m,  cost_src = menu_cost_map(cs, ce)
        prev_m, _        = menu_cost_map(ps, pe) if has_prev else ({}, None)
    except Exception:
        cur_m, prev_m, cost_src = {}, {}, "unavailable"
    cost_available = cost_src != "unavailable"
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
        menu_rows.append({
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
    cost_top   = sorted([m for m in menu_rows if (m["cost_per_head"] or 0) > 0],
                        key=lambda x: -(x["cost_per_head"] or 0))[:12]
    cost_up    = sorted([m for m in menu_rows if m["delta"] and m["delta"] > 0],
                        key=lambda x: -x["delta"])[:12]
    cost_down  = sorted([m for m in menu_rows if m["delta"] and m["delta"] < 0],
                        key=lambda x: x["delta"])[:12]
    tot_cur_cost  = sum((r["cost"] or 0) for r in cur_m.values())
    tot_prev_cost = sum((r["cost"] or 0) for r in prev_m.values()) if has_prev else 0
    total_cph      = cph(tot_cur_cost, covers_cur)
    total_cph_prev = cph(tot_prev_cost, covers_prev) if cmp_ok else None
    menu_out = {
        "basis": "cost_per_head",
        "covers": covers_cur, "covers_prev": covers_prev if has_prev else None,
        "total_cph": total_cph, "total_cph_prev": total_cph_prev,
        "total_cph_delta": (round((total_cph or 0) - (total_cph_prev or 0), 2)
                            if (total_cph is not None and total_cph_prev is not None) else None),
        "top": cost_top, "risers": cost_up, "savers": cost_down,
        # which table the dish costs came from — 'unavailable' means the window
        # predates sales_sku_daily and isn't whole calendar months
        "cost_basis": cost_src, "cost_from": daily_min,
    }
    return {"clean": clean, "cmp_ok": cmp_ok, "cost_available": cost_available, "cost_src": cost_src, "cost_up": cost_up, "daily_min": daily_min, "food_sub": food_sub, "menu_out": menu_out, "sp": sp, "sw": sw, "tot_cur_cost": tot_cur_cost, "tot_prev_cost": tot_prev_cost}


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

            def _drv(kind_like, tier, pax):
                d = {}
                if not pax:
                    return d
                for ent, cg, q in con.execute(
                        f"""SELECT ent, SUM(ent_cogs), SUM(qty)
                                        FROM menu_fact_day
                                        WHERE day BETWEEN ? AND ? AND branch_id IN ({t_ph})
                                          AND tier=? AND ent LIKE ? GROUP BY ent""",
                        [cs.isoformat(), ce.isoformat()] + t_bids + [tier, kind_like]):
                    d[ent] = ((cg or 0.0) / pax, (q or 0.0) / pax * 100)
                return d

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
                grp = [{"label": lblmap.get(e, e), "cogs_head": round(v[0], 1),
                        "share_pct": sh(v[0]),
                        "vs_ref": (round(v[0] - gR.get(e, (0.0, 0.0))[0], 1) if rt else None)}
                       for e, v in sorted(gF.items(), key=lambda kv: -kv[1][0])[:6]
                       if v[0] >= 1]
                mF = _drv("s:%", ft, ftpax)
                mnu = [{"label": lblmap.get(e, e), "cogs_head": round(v[0], 1),
                        "share_pct": sh(v[0]), "qty_100": round(v[1], 1)}
                       for e, v in sorted(mF.items(), key=lambda kv: -kv[1][0])[:6]
                       if v[0] >= 1]
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
                        "groups": grp, "menus": mnu}

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
                tier_out = ctx["tier_out"]
    except Exception:
        tier_out = None
    return {"tier_out": tier_out}


def _mgt_gp(con, *, cmp_ok, cost_available, cost_src, cur, daily_min, prev, tot_cur_cost, tot_prev_cost):
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
                       if (cmp_ok and prev and prev["revenue"]) else None)
        gp = {
            "revenue": cur["revenue"], "food_cost": round(tot_cur_cost),
            "gp": gp_cur, "gp_pct": gp_pct,
            "gp_per_head": round(gp_cur / cur["customers"], 1) if cur["customers"] else None,
            "gp_pct_prev": gp_prev_pct,
            "gp_pct_delta_pp": round(gp_pct - gp_prev_pct, 1) if (gp_pct is not None and gp_prev_pct is not None) else None,
            "cost_basis": cost_src,
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
            FROM (SELECT *, 'Easy' AS source FROM v_sales_sku_daily) s
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
    _eff_cost_m = effective_unit_cost_sql('s')
    cost_coverage = None
    try:
        cc = con.execute(f"""
            SELECT ROUND(SUM(s.qty)) plates,
                   ROUND(SUM(CASE WHEN {_eff_cost_m} > 0 THEN s.qty ELSE 0 END)) plates_costed,
                   ROUND(AVG(CASE WHEN {_eff_cost_m} > 0 THEN {_eff_cost_m} END), 2) avg_unit_cost,
                   COUNT(DISTINCT s.sku_id) skus,
                   COUNT(DISTINCT CASE WHEN {_eff_cost_m} = 0 THEN s.sku_id END) skus_uncosted
            FROM v_sales_sku_monthly s JOIN skus sk ON sk.sku_id=s.sku_id
            JOIN branches b ON b.branch_id=s.branch_id
            WHERE {mw} AND sk.sku_code NOT IN {_ALL_TIER_CODES_SQL}
              AND s.sku_id IN {food_sub}""", mp).fetchone()
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
                    WHERE {mw} AND sk.sku_code NOT IN {_ALL_TIER_CODES_SQL}
                      AND s.sku_id IN {food_sub} AND {_eff_cost_m} = 0
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
    pm_dow = con.execute(f"""
        SELECT CAST(strftime('%w', st.sold_at) AS INT) wd,
               {net_revenue_sum('st')} rev,
               COUNT(DISTINCT date(st.sold_at)) days
        FROM sales_transaction st JOIN branches b ON b.branch_id=st.branch_id
        WHERE {where} AND strftime('%Y-%m', st.sold_at)=:month
        GROUP BY wd""", {**params, "month": prev_month}).fetchall()
    dow_avg = {r["wd"]: (r["rev"] or 0) / r["days"] for r in pm_dow if r["days"]}
    dow_fallback = sum(dow_avg.values()) / len(dow_avg) if dow_avg else 0

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
        if mtd_rev and w_elapsed:
            pace["projected"] = projected = round(mtd_rev / w_elapsed * _wsum(days_total), 2)
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
    elif period == "monthly":
        mlist = []                       # 8 calendar months ending at the selected one
        for i in range(7, -1, -1):
            mi = (cs.month - 1 - i) % 12 + 1
            yi = cs.year + (cs.month - 1 - i - (mi - 1)) // 12
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
                   ROUND({net_revenue_sum('st')},2) rev, COALESCE(SUM(st.customer_count),0) cust
            FROM sales_transaction st JOIN branches b ON b.branch_id=st.branch_id
            WHERE {where} AND date(st.sold_at) BETWEEN :s AND :e
            GROUP BY w""", {**params, "s": cs.isoformat(), "e": ce.isoformat()}).fetchall()
        dm = {r["w"]: r for r in dow_rows}
        by_day = []
        for i, nm in enumerate(["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]):
            r = dm.get((i + 1) % 7)      # %w: 0=Sun..6=Sat → Mon-first
            by_day.append({"dow": nm, "revenue": round(r["rev"] or 0) if r else 0,
                           "covers": (r["cust"] or 0) if r else 0})
        bd = max(by_day, key=lambda d: d["revenue"], default=None)
        peak["by_day"] = by_day
        peak["best_day"] = bd["dow"] if bd and bd["revenue"] else None

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


@router.get("/api/reports/management")
@ttl_cache
def report_management(
    period: str = Query("daily"),            # 'daily' | 'weekly' | 'monthly'
    date:   Optional[str] = Query(None),     # focus day (default = latest day in data)
    week:   Optional[str] = Query(None),     # weekly: any date in the wanted FULL week (default = last completed week)
    month:  Optional[str] = Query(None),     # monthly: 'YYYY-MM' (default = last COMPLETE month)
    branch: Optional[str] = Query(None),
    group:  Optional[str] = Query(None),
    lang:   str = Query("en"),               # 'en' | 'th' — language for labels, comp_label & action items
):
    """One-glance management summary for a day, a COMPLETE week or a COMPLETE month.

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
    if period not in ("daily", "weekly", "monthly"):
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
    ctx = {"DOW": DOW, "MON": MON, "_cal": _cal, "_dt": _dt, "base": base, "branch": branch, "date": date, "dlabel": dlabel, "group": group, "lang": lang, "month": month, "params": params, "pct": pct, "period": period, "th": th, "week": week, "where": where}
    with get_db() as con:
        out = _mgt_window(con, DOW=ctx.get("DOW"), MON=ctx.get("MON"), _cal=ctx.get("_cal"), _dt=ctx.get("_dt"), date=ctx.get("date"), dlabel=ctx.get("dlabel"), month=ctx.get("month"), params=ctx.get("params"), period=ctx.get("period"), th=ctx.get("th"), week=ctx.get("week"), where=ctx.get("where"))
        if out.get("available") is False:
            return out
        ctx.update(out)
        out = _mgt_headline(con, ce=ctx.get("ce"), cs=ctx.get("cs"), has_prev=ctx.get("has_prev"), has_yoy=ctx.get("has_yoy"), params=ctx.get("params"), pct=ctx.get("pct"), pe=ctx.get("pe"), ps=ctx.get("ps"), where=ctx.get("where"), ye=ctx.get("ye"), ys=ctx.get("ys"))
        ctx.update(out)
        out = _mgt_branches(con, ce=ctx.get("ce"), cs=ctx.get("cs"), has_prev=ctx.get("has_prev"), params=ctx.get("params"), pct=ctx.get("pct"), pe=ctx.get("pe"), ps=ctx.get("ps"), where=ctx.get("where"))
        ctx.update(out)
        out = _mgt_leak(con, ce=ctx.get("ce"), cs=ctx.get("cs"), has_prev=ctx.get("has_prev"), params=ctx.get("params"), pct=ctx.get("pct"), pe=ctx.get("pe"), ps=ctx.get("ps"), where=ctx.get("where"))
        ctx.update(out)
        out = _mgt_menu(con, _cal=ctx.get("_cal"), _dt=ctx.get("_dt"), branch=ctx.get("branch"), ce=ctx.get("ce"), cs=ctx.get("cs"), cur=ctx.get("cur"), group=ctx.get("group"), has_prev=ctx.get("has_prev"), leak=ctx.get("leak"), params=ctx.get("params"), pct=ctx.get("pct"), pe=ctx.get("pe"), prev=ctx.get("prev"), ps=ctx.get("ps"), where=ctx.get("where"))
        ctx.update(out)
        out = _mgt_tier(con, ce=ctx.get("ce"), cs=ctx.get("cs"), has_prev=ctx.get("has_prev"), pct=ctx.get("pct"), pe=ctx.get("pe"), ps=ctx.get("ps"), sp=ctx.get("sp"), sw=ctx.get("sw"), th=ctx.get("th"))
        ctx.update(out)
        out = _mgt_gp(con, cmp_ok=ctx.get("cmp_ok"), cost_available=ctx.get("cost_available"), cost_src=ctx.get("cost_src"), cur=ctx.get("cur"), daily_min=ctx.get("daily_min"), prev=ctx.get("prev"), tot_cur_cost=ctx.get("tot_cur_cost"), tot_prev_cost=ctx.get("tot_prev_cost"))
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
    prev = ctx["prev"]
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
    pword = (("วัน" if period == "daily" else "เดือน" if period == "monthly" else "สัปดาห์") if th
             else ("day" if period == "daily" else "month" if period == "monthly" else "week"))
    actions = []
    if prev and headline.get("rev_wow_pct") is not None and headline["rev_wow_pct"] <= -10:
        actions.append({"sev": "high",
            "text": (f"ยอดขายรวมทั้งเครือลดลง {abs(headline['rev_wow_pct']):.0f}% เทียบ{pword}ก่อน — ตรวจสอบสาเหตุ"
                     if th else
                     f"Chain net sales down {abs(headline['rev_wow_pct']):.0f}% vs last {pword} — investigate drivers.")})
    for r in branches:
        if r["wow_pct"] is not None and r["wow_pct"] <= -25 and r["revenue"] > 0:
            actions.append({"sev": "high",
                "text": (f"{r['branch']} ยอดขายลดลง {abs(r['wow_pct']):.0f}% เทียบ{pword}ก่อน (฿{r['revenue']:,.0f})"
                         if th else
                         f"{r['branch']} net sales down {abs(r['wow_pct']):.0f}% vs last {pword} (฿{r['revenue']:,.0f}).")})
    if cash_heavy.get("bills"):
        actions.append({"sev": "high",
            "text": (f"{cash_heavy['bills']} บิลส่วนลดที่จ่ายด้วยเงินสดเป็นหลัก (฿{cash_heavy['baht']:,.0f}) — ตรวจสอบความเสี่ยงเงินขาด"
                     if th else
                     f"{cash_heavy['bills']} cash-heavy discounted bills (฿{cash_heavy['baht']:,.0f}) — review for skim risk.")})
    if heavy["bills"]:
        actions.append({"sev": "med",
            "text": (f"{heavy['bills']} บิลให้ส่วนลด ≥30% (฿{heavy['baht']:,.0f}) ใน{pword}นี้"
                     if th else
                     f"{heavy['bills']} bills discounted ≥30% (฿{heavy['baht']:,.0f}) this {pword}.")})
    if reprint["bills"]:
        actions.append({"sev": "med",
            "text": (f"{reprint['bills']} บิลส่วนลดถูกพิมพ์ซ้ำมากกว่า 1 ครั้ง — อาจมีการพิมพ์ซ้ำผิดปกติ"
                     if th else
                     f"{reprint['bills']} discounted bills reprinted >1× — possible re-print abuse.")})
    if food_cost_pct is not None and food_cost_pct >= 53:
        actions.append({"sev": "med",
            "text": (f"ต้นทุนอาหาร {food_cost_pct:.0f}% — สูงกว่าเป้า ~50%"
                     if th else
                     f"Food cost {food_cost_pct:.0f}% — above the ~50% target.")})
    if projected and target and projected < target * 0.95:
        gap = (1 - projected/target) * 100
        if th:
            word = "ยอดรวมทั้งเดือน" if month_complete else "ประมาณการสิ้นเดือน"
            actions.append({"sev": "med",
                "text": f"{word} ฿{projected:,.0f} ต่ำกว่าเป้า ฿{target:,.0f} อยู่ {gap:.0f}%"})
        else:
            word = "Month total" if month_complete else "Month-end projection"
            actions.append({"sev": "med",
                "text": f"{word} ฿{projected:,.0f} is {gap:.0f}% below target ฿{target:,.0f}."})
    if has_prev and cost_up and (cost_up[0].get("delta") or 0) >= 0.5:
        m0 = cost_up[0]
        actions.append({"sev": "low",
            "text": (f"{m0['menu']} ต้นทุนต่อหัวเพิ่มขึ้น ฿{m0['delta']:.2f} เทียบ{pword}ก่อน "
                     f"(฿{m0['cost_per_head']:.2f}/หัว) — ตรวจสอบการตักเสิร์ฟ / ต้นทุนวัตถุดิบ"
                     if th else
                     f"{m0['menu']} food cost/head up ฿{m0['delta']:.2f} vs last {pword} "
                     f"(฿{m0['cost_per_head']:.2f}/head) — check portioning / ingredient cost.")})
    if gp.get("gp_pct_delta_pp") is not None and gp["gp_pct_delta_pp"] <= -3:
        actions.append({"sev": "med",
            "text": (f"อัตรากำไรขั้นต้นลดลง {abs(gp['gp_pct_delta_pp']):.1f}pp เทียบ{pword}ก่อน — ตอนนี้ {gp['gp_pct']:.0f}%"
                     if th else
                     f"Gross margin down {abs(gp['gp_pct_delta_pp']):.1f}pp vs last {pword} — now {gp['gp_pct']:.0f}%.")})
    sev_rank = {"high": 0, "med": 1, "low": 2}
    actions.sort(key=lambda a: sev_rank.get(a["sev"], 9))
    actions = actions[:7]
    if not actions:
        actions = [{"sev": "low", "text": ("ไม่พบประเด็นเร่งด่วนในช่วงนี้" if th else "No critical issues flagged for this period.")}]

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

    return {
        "available": True,
        "period": period,
        "lang": ("th" if th else "en"),
        "label": label,
        "comp_label": comp_label,
        "focus": focus,
        "range": [cs.isoformat(), ce.isoformat()],
        "comp_range": [ps.isoformat(), pe.isoformat()] if has_prev else None,
        "yoy_range": [ys.isoformat(), ye.isoformat()] if has_yoy else None,
        "partial": partial,
        "selected_week": selected_week,
        "weeks": weeks,
        "selected_month": selected_month,
        "months": months,
        "days_in_period": days_in_period,
        "latest_day": maxd,
        "min_day": mind,
        "branch_scope": branch or ("Group: " + group if group else "All branches"),
        "has_prev": has_prev,
        "has_yoy": has_yoy,
        "headline": headline,
        "branches": branches,
        "gp": gp,
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
    }


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
    ("ฐานการเปรียบเทียบ", "รายวัน = วันเดียวกันของสัปดาห์ก่อน · รายสัปดาห์ = สัปดาห์ จ.–อา. ก่อนหน้า · "
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
    autofit(ws)
    return {}


def _xlsx_branches(*, NUM, PCT, autofit, header, j, pdelta, wb):
    # Sheet 2 — Branch League
    ws = wb.create_sheet("Branch League")
    header(ws, ["Branch", "Net Sales", "Covers", "Spend/Head", "Share %", "vs prev %"])
    for b in j.get("branches", []):
        ws.append([b["branch"], b["revenue"], b["customers"], b["rev_per_head"],
                   pdelta(b["share_pct"]), pdelta(b["wow_pct"])])
        r = ws.max_row
        for c in (2, 3, 4): ws.cell(r, c).number_format = NUM
        ws.cell(r, 5).number_format = PCT; ws.cell(r, 6).number_format = PCT
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
                    "Rev/Head", "Cost/Head", "Cost/Head prev", "GP/Head", "Food %"])
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
    buf = _io.BytesIO(); wb.save(buf); buf.seek(0)
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
):
    """Download the Daily/Weekly/Monthly Management Summary as a multi-sheet Excel
    file. Reuses report_management() so the numbers match the on-screen report."""
    try:
        import openpyxl
        from openpyxl.styles import Font, PatternFill, Alignment
        from openpyxl.utils import get_column_letter
    except ImportError:
        raise HTTPException(status_code=500, detail="openpyxl not installed")
    # report_management is @ttl_cache-wrapped → returns a Response(bytes); parse its body.
    resp = report_management(period=period, date=date, week=week, month=month,
                             branch=branch, group=group, lang=lang)
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
        headers={"Content-Disposition": f"attachment; filename={fname}"},
    )
