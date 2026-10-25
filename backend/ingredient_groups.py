# -*- coding: utf-8 -*-
"""High Value Product Monitor (owner 2026-10-25): the cost per head of eight high-value ingredients, every branch.

ONE config: GROUPS (slug -> Thai label, LIKE patterns on skus.description, excludes). A dish belongs to a group when its
name matches one of the group's patterns and none of its excludes; package codes (rules.is_tier_code) never count. A dish
whose name matches several groups (a mixed sashimi set) counts in EACH of them at its full dish cost (`mixed`).

Figures, per branch x group, on the windows of periods.windows (cur / prev to date / ly):
    plates          SUM(qty) of the daily / monthly dish tables (v_sales_sku_*: the service-charge lines are out)
    cost            SUM(qty x the dish cost the app uses for COGS) - the ONE engine (core._unified_cogs_expr, the same
                    month-table / day-table plan as period_cost: a whole month reads the month table, a part month the
                    day table; a part month before the day table has no figure and a reason)
    uncosted_plates plates of dishes with no cost (counted in plates, not in cost; cost_per_plate leaves them out)
    customers       SUM(sales_transaction.customer_count), source 'Easy', the window's (closed) days - all customers
    cost_per_head = cost / customers · plates_per_head = plates / customers · cost_per_plate = cost / costed plates
    share_of_food_cost = cost / the branch's whole food cost (the same engine over every dish)
"""
import math
from typing import Dict, Iterable, List, Optional, Tuple

import periods as P
import period_cost as PC

try:
    from core import _unified_cogs_expr
    from rules import costed_sql, is_tier_code, needs_cost_sql
except ImportError:  # pragma: no cover
    from .core import _unified_cogs_expr
    from .rules import costed_sql, is_tier_code, needs_cost_sql

# slug, Thai label, LIKE patterns, excludes. Verified on the menu list (2026-10-25, PR body): กุ้งหวาน is bought as
# "กุ้งหวานซาชิมิ อาเจนติน่า" and sold as "กุ้งแดงอาร์เจนติน่า" - no menu name says กุ้งหวาน; โอโทโร่ / เนกิโทโร่ is tuna belly;
# อูนากิ is a spelling of อูนางิ; ปลาเนื้อขาว (a white fish), เนื้อปู and เนื้อปลา are not beef.
GROUPS: Tuple[Tuple[str, str, Tuple[str, ...], Tuple[str, ...]], ...] = (
    ("salmon", "แซลมอน", ("%แซลมอน%", "%แซลม่อน%"), ()),
    ("tuna", "ทูน่า", ("%ทูน่า%", "%มากุโระ%", "%โทโร่%"), ()),
    ("hamachi", "ฮามาจิ", ("%ฮามาจิ%",), ()),
    ("amaebi", "กุ้งหวาน", ("%กุ้งหวาน%", "%อามะเอบิ%", "%กุ้งแดงอาร์เจนติน่า%", "%กุ้งอาร์เจนติน่า%", "%กุ้งอาเจนติน่า%"), ()),
    ("scallop", "หอยเชลล์", ("%หอยเชลล์%", "%หอยเชล%", "%โฮตาเตะ%"), ()),
    ("eel", "ปลาไหล", ("%ปลาไหล%", "%อูนางิ%", "%อูนากิ%"), ()),
    ("oyster", "หอยนางรม", ("%หอยนางรม%",), ()),
    ("beef", "เนื้อ", ("%เนื้อ%", "%วากิว%", "%ลิ้นวัว%"), ("%ปลาเนื้อ%", "%เนื้อปู%", "%เนื้อปลา%")),
)
SLUGS = tuple(g[0] for g in GROUPS)
LABELS = {g[0]: g[1] for g in GROUPS}


def _like(text: str, pat: str) -> bool:
    """SQL LIKE for the patterns above ('%x%', 'x%', '%x', 'x'): Thai has no case, so a plain substring test."""
    core = pat.strip("%")
    if pat.startswith("%") and pat.endswith("%"):
        return core in text
    if pat.endswith("%"):
        return text.startswith(core)
    if pat.startswith("%"):
        return text.endswith(core)
    return text == core


def match(description: Optional[str], code: Optional[str] = None) -> List[str]:
    """The groups a dish belongs to (in GROUPS order); [] for a package code."""
    if code is not None and is_tier_code(str(code).upper()):
        return []
    t = description or ""
    return [slug for slug, _, pats, excl in GROUPS
            if any(_like(t, p) for p in pats) and not any(_like(t, x) for x in excl)]


def sku_groups(con) -> Dict[int, List[str]]:
    """sku_id -> groups, for every dish that belongs to at least one."""
    out = {}
    for sid, code, desc in con.execute("SELECT sku_id, sku_code, description FROM skus"):
        g = match(desc, code)
        if g:
            out[sid] = g
    return out


def _rows(con, s: str, e: str, branch, group, floor) -> Tuple[Optional[Dict], Optional[str]]:
    """Per branch x dish on [s, e]: {(branch, sku_id): [plates, cost, costed_plates]} and per branch the whole food cost
    and customers -> ({"dish": ..., "food": {branch: cogs}, "cust": {branch: n}}, None) or (None, reason)."""
    pl, why = PC.plan(s, e, floor)
    if pl is None:
        return None, why
    bw, bp = PC._bscope(branch, group)
    cg, ok, need = _unified_cogs_expr("ssm"), costed_sql("ssm"), needs_cost_sql("sk.sku_code")
    # the unit cost depends on the dish and its month only, so the rows are summed per branch x dish x month first and
    # costed once each (qty x unit cost is linear: the same total as costing every day's row)
    sel = f"""SELECT b.name AS branch, ssm.sku_id, SUM(ssm.qty) plates, SUM({cg}) cost,
                     SUM(CASE WHEN {ok} THEN ssm.qty ELSE 0 END) costed, MAX(CASE WHEN {need} THEN 1 ELSE 0 END) need"""
    dish: Dict[Tuple[str, int], List[float]] = {}
    food: Dict[str, float] = {}

    def add(rows):
        for r in rows:
            food[r["branch"]] = food.get(r["branch"], 0.0) + float(r["cost"] or 0)
            if not r["need"]:
                continue
            a = dish.setdefault((r["branch"], r["sku_id"]), [0.0, 0.0, 0.0])
            a[0] += float(r["plates"] or 0)
            a[1] += float(r["cost"] or 0)
            a[2] += float(r["costed"] or 0)

    if pl["months"]:
        ph = ",".join(f":_m{i}" for i in range(len(pl["months"])))
        mp = {f"_m{i}": m for i, m in enumerate(pl["months"])}
        add(con.execute(f"""{sel}
              FROM (SELECT branch_id, sku_id, period_month, source, SUM(qty) qty, SUM(subtotal_ex_vat) subtotal_ex_vat
                    FROM v_sales_sku_monthly WHERE period_month IN ({ph}) GROUP BY branch_id, sku_id, period_month, source) ssm
              JOIN branches b ON b.branch_id = ssm.branch_id JOIN skus sk ON sk.sku_id = ssm.sku_id
              WHERE {bw} GROUP BY b.name, ssm.sku_id""", {**bp, **mp}).fetchall())
    for a, b in pl["days"]:
        add(con.execute(f"""{sel}
              FROM (SELECT branch_id, sku_id, substr(day, 1, 7) AS period_month, 'Easy' AS source,
                           SUM(qty) qty, SUM(subtotal_ex_vat) subtotal_ex_vat
                    FROM v_sales_sku_daily WHERE day BETWEEN :_s AND :_e GROUP BY branch_id, sku_id) ssm
              JOIN branches b ON b.branch_id = ssm.branch_id JOIN skus sk ON sk.sku_id = ssm.sku_id
              WHERE {bw} GROUP BY b.name, ssm.sku_id""", {**bp, "_s": a, "_e": b}).fetchall())
    cust = {r["branch"]: (r["c"] or 0) for r in con.execute(
        f"""SELECT b.name AS branch, SUM(t.customer_count) c FROM sales_transaction t JOIN branches b ON b.branch_id = t.branch_id
            WHERE t.source = 'Easy' AND {bw} AND date(t.sold_at) BETWEEN :_s AND :_e GROUP BY b.name""",
        {**bp, "_s": s[:10], "_e": e[:10]}).fetchall()}
    return {"dish": dish, "food": food, "cust": cust}, None


def _r(x, n=2):
    return None if x is None else round(x, n)


def _fig(plates: float, cost: float, costed: float, cust: float, food: float) -> Dict:
    return {"plates": round(plates, 1), "cost": round(cost, 2), "uncosted_plates": round(max(plates - costed, 0.0), 1),
            "_cust": cust, "_costed": costed,
            "cost_per_head": _r(cost / cust) if cust else None,
            "plates_per_head": _r(plates / cust, 3) if cust else None,
            "cost_per_plate": _r(cost / costed) if costed else None,
            "share_of_food_cost": _r(cost / food * 100, 1) if food > 0 else None}


CHG = ("cost_per_head", "plates_per_head", "cost_per_plate")


def _chg(cur: Optional[Dict], base: Optional[Dict]) -> Dict:
    return {k: (P.pct((cur or {}).get(k), (base or {}).get(k)) if cur and base else None) for k in CHG}


CAUSES = ("customers", "plates_per_head", "cost_per_plate")
CAUSE_TH = {"customers": "ลูกค้า", "plates_per_head": "กินต่อหัว", "cost_per_plate": "ต้นทุนต่อจาน"}


def _logmean(a: float, b: float) -> float:
    """The logarithmic mean L(a, b) = (a - b) / (ln a - ln b); L(a, a) = a."""
    if abs(a - b) <= 1e-9 * max(abs(a), abs(b), 1.0):
        return a
    return (a - b) / (math.log(a) - math.log(b))


def split(cur: Optional[Dict], base: Optional[Dict]) -> Dict:
    """Why the group cost moved (owner 2026-10-25): cost = customers x plates / head x cost / plate, so its change splits into
    three baht parts that add up to the whole change (LMDI: part_i = L(cost_now, cost_base) x ln(x_i now / x_i base)).
    Plates here are the costed plates (the same plates cost / plate is worked on), so the identity is exact.
    -> {total, total_pct, customers, plates_per_head, cost_per_plate, main} or {.., reason} without a base."""
    none = {"total": None, "total_pct": None, "customers": None, "plates_per_head": None, "cost_per_plate": None, "main": None}
    if not cur or not base:
        return dict(none, reason="ไม่มีข้อมูลช่วงที่เทียบ")
    c1, c0 = float(cur.get("cost") or 0), float(base.get("cost") or 0)
    n1, n0 = float(cur.get("_cust") or 0), float(base.get("_cust") or 0)
    q1, q0 = float(cur.get("_costed") or 0), float(base.get("_costed") or 0)
    if min(c1, c0, n1, n0, q1, q0) <= 0:
        out = dict(none, reason="ช่วงใดช่วงหนึ่งไม่มีจานที่มีต้นทุน หรือไม่มีลูกค้า")
        if c0 > 0 or c1 > 0:
            out["total"] = round(c1 - c0, 2)
            out["total_pct"] = P.pct(c1, c0)
        return out
    x1 = {"customers": n1, "plates_per_head": q1 / n1, "cost_per_plate": c1 / q1}
    x0 = {"customers": n0, "plates_per_head": q0 / n0, "cost_per_plate": c0 / q0}
    lm = _logmean(c1, c0)
    parts = {k: lm * math.log(x1[k] / x0[k]) for k in CAUSES}
    total = c1 - c0
    main = max(CAUSES, key=lambda k: abs(parts[k])) if any(abs(v) > 0.005 for v in parts.values()) else None
    return {"total": round(total, 2), "total_pct": P.pct(c1, c0), **{k: round(v, 2) for k, v in parts.items()}, "main": main}


def _public_cell(c: Optional[Dict]) -> Optional[Dict]:
    return {k: v for k, v in c.items() if not k.startswith("_")} if c else c


def _sum_cells(data: Dict, groups: Dict[int, List[str]], names: Iterable[str]) -> Dict:
    """{slug: [plates, cost, costed]} over the branches `names`, and the menus behind them."""
    names = set(names)
    acc = {s: [0.0, 0.0, 0.0] for s in SLUGS}
    for (br, sid), (q, c, k) in data["dish"].items():
        if br not in names:
            continue
        for slug in groups.get(sid, ()):
            a = acc[slug]
            a[0] += q; a[1] += c; a[2] += k
    return acc


def _scope_figs(data: Optional[Dict], groups, names) -> Optional[Dict]:
    """The figures of one scope (one branch, or the chain = every branch) on one window; None without a cost source."""
    if data is None:
        return None
    names = list(names)
    cust = sum(data["cust"].get(n, 0) for n in names)
    food = sum(data["food"].get(n, 0.0) for n in names)
    acc = _sum_cells(data, groups, names)
    return {"customers": cust, "food_cost": round(food, 2),
            "food_cost_per_head": _r(food / cust) if cust else None,
            "cells": {s: _fig(*acc[s], cust, food) for s in SLUGS}}


def _merge(cur: Optional[Dict], prev: Optional[Dict], ly: Optional[Dict]) -> Dict:
    """One row: the current figures with prev / ly of each and the change in %."""
    out = {"customers": (cur or {}).get("customers"), "food_cost": (cur or {}).get("food_cost"),
           "food_cost_per_head": (cur or {}).get("food_cost_per_head"),
           "prev_food_cost_per_head": (prev or {}).get("food_cost_per_head"),
           "ly_food_cost_per_head": (ly or {}).get("food_cost_per_head"),
           "food_change": {"prev": P.pct((cur or {}).get("food_cost_per_head"), (prev or {}).get("food_cost_per_head")),
                           "ly": P.pct((cur or {}).get("food_cost_per_head"), (ly or {}).get("food_cost_per_head"))},
           "cells": {}}
    for s in SLUGS:
        c = (cur or {}).get("cells", {}).get(s)
        p = (prev or {}).get("cells", {}).get(s)
        y = (ly or {}).get("cells", {}).get(s)
        cell = _public_cell(c) if c else {k: None for k in ("plates", "cost", "uncosted_plates", "cost_per_head",
                                                            "plates_per_head", "cost_per_plate", "share_of_food_cost")}
        keep = CHG + ("share_of_food_cost", "plates", "uncosted_plates", "cost")
        cell["prev"] = {k: p.get(k) for k in keep} if p else None
        cell["ly"] = {k: y.get(k) for k in keep} if y else None
        cell["change"] = {"prev": _chg(c, p), "ly": _chg(c, y)}
        # why the group cost moved: ลูกค้า · กินต่อหัว · ต้นทุนต่อจาน in baht, adding up to the change (split)
        cell["cause"] = {"prev": split(c, p), "ly": split(c, y)}
        out["cells"][s] = cell
    return out


def _windows_data(con, w, branch, group):
    floor = PC.cost_floor(con)
    reasons = dict(P.reasons(w))
    cur, reasons["cur"] = _rows(con, *w["cur"], branch, group, floor)
    prev = ly = None
    if w.get("has_prev"):
        prev, r = _rows(con, *w["prev"], branch, group, floor)
        reasons["prev"] = reasons.get("prev") or r
    if w.get("has_ly"):
        ly, r = _rows(con, *w["ly"], branch, group, floor)
        reasons["ly"] = reasons.get("ly") or r
    return floor, cur, prev, ly, reasons


def compute(con, w: Dict, branch: Optional[str] = None, group: Optional[str] = None) -> Dict:
    """The page's answer for one periods.windows result: {period, cost_floor, groups, chain, branches, reasons}."""
    floor, cur, prev, ly, reasons = _windows_data(con, w, branch, group)
    groups = sku_groups(con)
    per = P.public(w) or {}
    per["reasons"] = {k: v for k, v in reasons.items() if k in ("prev", "ly") and v}
    names = sorted(set((cur or {}).get("cust", {})) | set((cur or {}).get("food", {})))
    chain = _merge(*(_scope_figs(d, groups, names) for d in (cur, prev, ly)))
    branches = []
    for n in names:
        row = _merge(*(_scope_figs(d, groups, [n]) for d in (cur, prev, ly)))
        if not row["customers"] and not row["food_cost"]:
            continue
        row["branch"] = n
        branches.append(row)
    branches.sort(key=lambda r: -((r["cells"]["salmon"]["cost_per_head"]) or 0))
    mixed = sum(1 for g in groups.values() if len(g) > 1)
    return {"period": per, "cost_floor": floor, "groups": [{"slug": s, "label": LABELS[s]} for s in SLUGS],
            "chain": chain, "branches": branches, "reasons": {k: v for k, v in reasons.items() if v},
            "mixed_menus": mixed}


def menus(con, w: Dict, branch: str, slug: str, group: Optional[str] = None) -> Dict:
    """The dishes of group `slug` at one branch on the current window (and their prev plates): the drill of a row."""
    floor, cur, prev, _ly, reasons = _windows_data(con, dict(w, has_ly=False), branch, group)
    groups = sku_groups(con)
    ids = [sid for sid, g in groups.items() if slug in g]
    info = {r[0]: (r[1], r[2]) for r in con.execute(
        f"SELECT sku_id, sku_code, description FROM skus WHERE sku_id IN ({','.join('?' * len(ids)) or 'NULL'})", ids)}
    out = []
    for sid in ids:
        c = (cur or {}).get("dish", {}).get((branch, sid))
        p = (prev or {}).get("dish", {}).get((branch, sid))
        if not c and not p:
            continue
        q, cost, costed = c or (0.0, 0.0, 0.0)
        code, desc = info.get(sid, ("", ""))
        out.append({"sku_code": code, "name": desc, "plates": round(q, 1), "cost": round(cost, 2),
                    "cost_per_plate": _r(cost / costed) if costed else None, "uncosted": bool(q and costed < q),
                    "prev_plates": round(p[0], 1) if p else None, "mixed": len(groups.get(sid, ())) > 1,
                    "groups": groups.get(sid, [])})
    out.sort(key=lambda m: (-m["cost"], -m["plates"]))
    per = P.public(w) or {}
    return {"period": per, "branch": branch, "slug": slug, "label": LABELS.get(slug, slug), "menus": out,
            "reasons": {k: v for k, v in reasons.items() if v}, "cost_floor": floor}
