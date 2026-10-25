# -*- coding: utf-8 -*-
"""DV7 (owner 2026-10-25, phone table review top-10 #10) · ปรับเมนู and โอกาสเมนู rebuilt around the buffet cost per head.

The two pages answer on the windows of the top bar's period (periods.windows: the period to date and the same days of the
period before) in its branch / branch-group scope:

  ปรับเมนู   GET /api/menu/action-board (cost.analysis)
             Which buffet dishes moved the cost per head, in baht, and why (กินต่อหัว · ราคาต่อจาน); the branches and the
             buffet price tiers that pushed each one; the revenue per head beside it. À la carte as a sub-section (revenue, GP).
  โอกาสเมนู  GET /api/menu/opportunities (menu.analysis)
             The branches whose buffet customers eat a dish above the other branches' median for the same price tier (plates;
             baht only for a role holding cost.analysis / profit.pnl), and the à-la-carte dishes a branch under-sells against the
             chain (revenue in baht).

Nothing here is a second definition. The figures come from
  ingredient_groups._rows    per branch x dish: plates, cost, costed plates (THE COGS engine, core._unified_cogs_expr, on the
                             month / day plan of period_cost), the branch's food cost and customers (sales_transaction, 'Easy')
  ingredient_groups.split    why a cost moved: customers x plates per head x cost per plate (LMDI: the parts add up)
  period_cost.plan/_bscope   the month / day sources of a window and its branch scope (the dish revenue below)
  routers.sales._spike_kind  buffet ((B) / (BF)) or à la carte - the menu-spike card's rule; _spike_name the shown name
  core._FOOD_CATEGORY_FILTER the dine-in food dishes of the menu pages (à la carte: no drinks, desserts, takeaway packs, fees)
  menu_fact_day / _total     buffet plates and customers per price tier (the per-bill fact tables), for the tier lines
So the dishes' cost per head adds up to the food cost per head of period_cost.cost_sum (tested).

A dish = one menu NAME: the same dish sold under several codes (a new branch's code series) is one row (review principle 3).
The per-head impact of a dish = the change of its cost minus the part more or fewer customers explain = the กินต่อหัว part +
the ราคาต่อจาน part of split. A dish not sold in the base ('nobase'), or not now ('gone'), or costed in one window only, is
measured on its plates per head at the cost per plate it has.
Materiality floor (review principle 2): max(฿1,000, 0.1 % of the scope's food cost) for a cost; the same on the scope's net
revenue for a revenue; 0.1 % of the scope's buffet plates for plates.
"""
import calendar
from typing import Dict, List, Optional, Tuple

import periods as P
import period_cost as PC
import ingredient_groups as IG

try:
    from rules import tier_label, needs_cost_sql
except ImportError:  # pragma: no cover
    from .rules import tier_label, needs_cost_sql

FLOOR_BAHT = 1000.0
FLOOR_SHARE = 0.001          # 0.1 %
LIMIT = 60                   # rows per list in the answer (the page shows 5, "ดูทั้งหมด (N)" opens the rest)
SHOW = 5
OVER_X = 1.2                 # a branch eats a buffet dish "above the chain" from 1.2x what the other branches' median gives ...
MIN_SELLERS = 5              # ... a median over at least 5 branches that sell it (per tier)
GAP_X = 0.4                  # an à-la-carte dish under-sold below 0.4x the chain's median plates per head (the old rule)
LOOKBACK_MONTHS = 6          # revive / categories: the whole months up to the period's end
BRANCHES_SHOWN = 3
TIERS_SHOWN = 2
PER = 100                    # plates per 100 customers
TIER_MAX_DAYS = 92           # the tier-fair comparison reads the fact tables of the window: up to 92 days (the menu-spike card's cap)
BUCKETS = ("price", "eat", "down")


def _router(name: str):
    """A router module as the app loaded it (backend.routers.x under uvicorn, routers.x in the tests): importing it a second
    time under the other name would register its cached endpoints twice."""
    import importlib
    import sys
    for k in (f"backend.routers.{name}", f"routers.{name}"):
        if k in sys.modules:
            return sys.modules[k]
    return importlib.import_module(f"routers.{name}")


def _spike():
    S = _router("sales")                  # one rule for buffet vs à la carte and the shown name (the menu-spike card's)
    return S._spike_kind, S._spike_name


def _r(x, n=2):
    return None if x is None else round(x, n)


def floor_of(base: float) -> float:
    return max(FLOOR_BAHT, FLOOR_SHARE * (base or 0.0))


def _median(vals: List[float]) -> float:
    s = sorted(vals)
    n = len(s)
    if not n:
        return 0.0
    m = n // 2
    return (s[m - 1] + s[m]) / 2 if n % 2 == 0 else s[m]


def _cum(rows: List[Dict], key: str, total: float) -> None:
    run = 0.0
    for r in rows:
        v = r.get(key) or 0.0
        run += v
        r["share"] = round(v / total * 100, 1) if total else None
        r["cum_share"] = round(run / total * 100, 1) if total else None


# ── windows and the readers ─────────────────────────────────────────────────────────────────────────────────────────────
def window(con, kind: Optional[str], pick=None, date_from=None, date_to=None, branch=None, group=None,
           month=None, month_from=None, month_to=None) -> Optional[Dict]:
    """periods.windows of the top bar's period in the scope (kind / pick / date_from / date_to, what gPeriodQuery sends). An old
    client's month / month_from / month_to (no kind) is a range of whole months."""
    where, params = _router("period_api")._scope(branch, group)
    k = P.kind_of(kind, "") if kind else ""
    if not k:
        span = P.span_of(month_from or month, month_to or month)
        if span:
            return P.windows(con, "range", where, params, date_from=span[0], date_to=span[1])
        k = "month"
    return P.windows(con, k, where, params, pick=pick, date_from=date_from, date_to=date_to)


def _dish_revenue(con, s: str, e: str, branch, group, floor) -> Optional[Dict[Tuple[str, int], float]]:
    """{(branch, sku_id): ex-VAT revenue} on [s, e], on the cost's month / day plan (period_cost.plan): over every dish of a
    branch it is period_cost.cost_sum's revenue."""
    pl, _why = PC.plan(s, e, floor)
    if pl is None:
        return None
    bw, bp = PC._bscope(branch, group)
    out: Dict[Tuple[str, int], float] = {}
    cur = con.cursor()
    cur.row_factory = None

    def add(rows):
        for br, sid, rv in rows:
            out[(br, sid)] = out.get((br, sid), 0.0) + float(rv or 0)
    if pl["months"]:
        ph = ",".join(f":_m{i}" for i in range(len(pl["months"])))
        mp = {f"_m{i}": m for i, m in enumerate(pl["months"])}
        add(cur.execute(f"""SELECT b.name, ssm.sku_id, SUM(ssm.subtotal_ex_vat) FROM v_sales_sku_monthly ssm
                            JOIN branches b ON b.branch_id = ssm.branch_id
                            WHERE {bw} AND ssm.period_month IN ({ph}) GROUP BY b.name, ssm.sku_id""", {**bp, **mp}).fetchall())
    for a, b in pl["days"]:
        add(cur.execute(f"""SELECT b.name, d.sku_id, SUM(d.subtotal_ex_vat) FROM v_sales_sku_daily d
                            JOIN branches b ON b.branch_id = d.branch_id
                            WHERE {bw} AND d.day BETWEEN :_s AND :_e GROUP BY b.name, d.sku_id""", {**bp, "_s": a, "_e": b}).fetchall())
    return out


def _skus(con, ids) -> Dict[int, Dict]:
    """sku_id -> {code, name, kind ('buffet' | 'alc'), food (the dine-in food scope of core._FOOD_CATEGORY_FILTER)}."""
    from core import _FOOD_CATEGORY_FILTER
    kind_of, name_of = _spike()
    ids = sorted(set(ids))
    out: Dict[int, Dict] = {}
    cur = con.cursor()
    cur.row_factory = None
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        for sid, code, desc, food in cur.execute(
                f"SELECT sk.sku_id, sk.sku_code, sk.description, CASE WHEN {_FOOD_CATEGORY_FILTER} THEN 1 ELSE 0 END "
                f"FROM skus sk LEFT JOIN categories c ON c.category_id = sk.category_id "
                f"WHERE sk.sku_id IN ({','.join('?' * len(chunk))})", chunk):
            nm, kd = name_of(desc, code).strip() or (code or ""), kind_of(desc)
            # a dish = one name of one kind: "(B) ซาชิมิแซลมอน" and "(A) ซาชิมิแซลมอน" share the shown name, never a row
            out[sid] = {"code": code or "", "name": nm, "kind": kd, "food": bool(food), "key": f"{kd}|{nm}"}
    return out


def _by_name(data: Optional[Dict], info: Dict[int, Dict]) -> Tuple[Dict[str, List[float]], Dict[str, Dict[str, List[float]]]]:
    """ingredient_groups._rows' dish map summed per dish (kind|name key): ({key: [plates, cost, costed]}, {key: {branch: [...]}})."""
    tot: Dict[str, List[float]] = {}
    per: Dict[str, Dict[str, List[float]]] = {}
    for (br, sid), (q, c, k) in (data or {}).get("dish", {}).items():
        nm = info[sid]["key"]
        a = tot.setdefault(nm, [0.0, 0.0, 0.0])
        a[0] += q; a[1] += c; a[2] += k
        b = per.setdefault(nm, {}).setdefault(br, [0.0, 0.0, 0.0])
        b[0] += q; b[1] += c; b[2] += k
    return tot, per


def _fact_span(con) -> Tuple[Optional[str], Optional[str]]:
    try:
        lo, hi = con.execute("SELECT MIN(day), MAX(day) FROM menu_fact_total").fetchone()
        return (lo[:10] if lo else None), (hi[:10] if hi else None)
    except Exception:
        return None, None


def _fact(con, a: str, b: str, branch, group, sids: Optional[List[int]] = None):
    """Buffet plates and customers per branch x price tier on [a, b] from the fact tables:
    ({(branch, tier): pax}, {(branch, tier, sku_id): plates}) - plates for `sids` only when given."""
    bw, bp = PC._bscope(branch, group)
    cur = con.cursor()
    cur.row_factory = None
    pax = {(br, str(t)): float(p or 0) for br, t, p in cur.execute(
        f"SELECT b.name, m.tier, SUM(m.pax) FROM menu_fact_total m JOIN branches b ON b.branch_id = m.branch_id "
        f"WHERE {bw} AND m.day BETWEEN :_s AND :_e GROUP BY b.name, m.tier", {**bp, "_s": a, "_e": b})}
    qty: Dict[Tuple[str, str, int], float] = {}
    if sids is not None:
        if not sids:
            return pax, qty
        ents = {f"_e{i}": f"s:{s}" for i, s in enumerate(sids)}
        sql = (f"WITH e(ent) AS (VALUES {','.join(f'(:{k})' for k in ents)}) SELECT b.name, m.tier, m.ent, SUM(m.qty) FROM e "
               f"JOIN menu_fact_day m ON m.ent = e.ent AND m.day BETWEEN :_s AND :_e JOIN branches b ON b.branch_id = m.branch_id "
               f"WHERE {bw} GROUP BY b.name, m.tier, m.ent")
        args = {**bp, **ents, "_s": a, "_e": b}
    else:
        sql = (f"SELECT b.name, m.tier, m.ent, SUM(m.qty) FROM menu_fact_day m JOIN branches b ON b.branch_id = m.branch_id "
               f"WHERE {bw} AND m.day BETWEEN :_s AND :_e AND m.ent LIKE 's:%' GROUP BY b.name, m.tier, m.ent")
        args = {**bp, "_s": a, "_e": b}
    for br, t, ent, q in cur.execute(sql, args):
        if q:
            qty[(br, str(t), int(str(ent)[2:]))] = float(q)
    return pax, qty


# ── the per-head impact of one dish ────────────────────────────────────────────────────────────────────────────────────
def impact(q1: float, c1: float, k1: float, n1: float, q0: float, c0: float, k0: float, n0: float) -> Optional[Dict]:
    """The baht a dish added to (or took from) the current window's cost through its cost PER HEAD.
    plates q, cost c, costed plates k, customers n; 1 = now, 0 = the base.
    -> {impact, cause, eat, price} (eat / price = the กินต่อหัว / ราคาต่อจาน parts; price None when unknown) or None.
    cause: 'eat' | 'price' (the larger part in the impact's direction), 'nobase' (not sold in the base), 'gone' (not sold now)."""
    if n1 <= 0 or n0 <= 0:
        return None
    cpp1 = c1 / k1 if k1 > 0 else None
    cpp0 = c0 / k0 if k0 > 0 else None
    if c1 > 0 and c0 > 0 and k1 > 0 and k0 > 0:
        s = IG.split({"cost": c1, "_cust": n1, "_costed": k1}, {"cost": c0, "_cust": n0, "_costed": k0})
        eat, price = float(s["plates_per_head"] or 0), float(s["cost_per_plate"] or 0)
        tot = eat + price
        cause = ("eat" if eat >= price else "price") if tot >= 0 else ("eat" if eat <= price else "price")
        return {"impact": tot, "cause": cause, "eat": eat, "price": price}
    if q0 <= 0 < q1:
        return {"impact": c1, "cause": "nobase", "eat": c1, "price": None} if cpp1 else None
    if q1 <= 0 < q0:
        v = -c0 * n1 / n0
        return {"impact": v, "cause": "gone", "eat": v, "price": None} if cpp0 else None
    cpp = cpp1 if cpp1 is not None else cpp0          # sold in both, costed in one only: plates per head at the cost it has
    if cpp is None or q1 <= 0 or q0 <= 0:
        return None
    v = (q1 / n1 - q0 / n0) * n1 * cpp
    return {"impact": v, "cause": "eat", "eat": v, "price": None}


# ── ปรับเมนู ─────────────────────────────────────────────────────────────────────────────────────────────────────────────
def board(con, w: Optional[Dict], branch=None, group=None, limit: int = LIMIT) -> Dict:
    """The answer of ปรับเมนู for one periods.windows result (see the module doc)."""
    out = {"period": P.public(w), "branch": branch or None, "group": group or None, "headline": None, "floor": None,
           "buffet": {"buckets": {k: [] for k in BUCKETS}, "counts": {k: 0 for k in BUCKETS},
                      "totals": {k: 0.0 for k in BUCKETS}, "net": None, "uncosted": [], "uncosted_count": 0},
           "alc": {"rows": [], "count": 0, "revenue": 0.0, "gp": None, "revenue_share": None, "floor_baht": None,
                   "uncosted": [], "uncosted_count": 0},
           "tier_note": None, "reasons": {}}
    if not w:
        out["reasons"] = {"cur": "ไม่มียอดขายในช่วงนี้"}
        return out
    floor_day = PC.cost_floor(con)
    reasons = dict(P.reasons(w))
    cur, why = IG._rows(con, *w["cur"], branch, group, floor_day)
    if cur is None:
        out["reasons"] = {**reasons, "cur": why}
        return out
    prev = None
    if w.get("has_prev"):
        prev, r = IG._rows(con, *w["prev"], branch, group, floor_day)
        if r:
            reasons["prev"] = reasons.get("prev") or r
    rev1 = _dish_revenue(con, *w["cur"], branch, group, floor_day) or {}
    rev0 = (_dish_revenue(con, *w["prev"], branch, group, floor_day) or {}) if prev else {}

    n1 = float(sum(cur["cust"].values()))
    food1 = float(sum(cur["food"].values()))
    revenue1 = float(sum(rev1.values()))
    n0 = float(sum(prev["cust"].values())) if prev else 0.0
    food0 = float(sum(prev["food"].values())) if prev else 0.0
    revenue0 = float(sum(rev0.values())) if prev else 0.0

    def ph(x, n):
        return round(x / n, 2) if n else None

    def figs(n, food, rev):
        return {"customers": n, "food_cost": round(food, 2), "revenue": round(rev, 2), "cost_per_head": ph(food, n),
                "rev_per_head": ph(rev, n), "food_cost_pct": round(food / rev * 100, 2) if rev > 0 else None}
    head = dict(figs(n1, food1, revenue1), prev=figs(n0, food0, revenue0) if prev else None)
    if prev and n0 and n1:
        head["cost_per_head_change"] = round(food1 / n1 - food0 / n0, 2)
        head["rev_per_head_change"] = round(revenue1 / n1 - revenue0 / n0, 2)
        head["per_head_effect"] = round((food1 / n1 - food0 / n0) * n1, 2)      # ฿: the change of the cost per head x customers now
    out["headline"] = head
    fl = floor_of(food1)
    out["floor"] = {"baht": round(fl, 2), "share_pct": FLOOR_SHARE * 100, "min_baht": FLOOR_BAHT}

    info = _skus(con, {sid for _b, sid in list(cur["dish"]) + list((prev or {}).get("dish", {}))} | {sid for _b, sid in rev1})
    t1, p1 = _by_name(cur, info)
    t0, p0 = _by_name(prev, info)
    kinds: Dict[str, Dict] = {}
    for sid, x in info.items():
        k = kinds.setdefault(x["key"], {"kind": x["kind"], "food": x["food"], "name": x["name"], "codes": [], "sids": []})
        k["codes"].append(x["code"]); k["sids"].append(sid)
    rv1: Dict[str, float] = {}
    rv0: Dict[str, float] = {}
    for (_b, s), v in rev1.items():
        if s in info:
            rv1[info[s]["key"]] = rv1.get(info[s]["key"], 0.0) + v
    for (_b, s), v in rev0.items():
        if s in info:
            rv0[info[s]["key"]] = rv0.get(info[s]["key"], 0.0) + v

    buckets: Dict[str, List[Dict]] = {k: [] for k in BUCKETS}
    unc_buf: List[Dict] = []
    alc: List[Dict] = []
    unc_alc: List[Dict] = []
    alc_rev = 0.0
    for nm in sorted(set(t1) | set(t0)):
        meta = kinds[nm]
        code = sorted(meta["codes"])[0]
        q1, c1, k1 = t1.get(nm, [0.0, 0.0, 0.0])
        q0, c0, k0 = t0.get(nm, [0.0, 0.0, 0.0])
        if meta["kind"] != "buffet":
            if not meta["food"] or q1 <= 0:
                continue
            r1 = rv1.get(nm, 0.0)
            alc_rev += r1
            if k1 < q1 - 1e-6:
                unc_alc.append({"sku_code": code, "name": meta["name"], "plates": round(q1, 1), "revenue": round(r1, 2),
                                "uncosted_plates": round(q1 - k1, 1)})
                continue
            gp1 = r1 - c1
            gp0 = (rv0.get(nm, 0.0) - c0) if (prev and q0 > 0 and k0 >= q0 - 1e-6) else None
            alc.append({"sku_code": code, "name": meta["name"], "plates": round(q1, 1), "revenue": round(r1, 2), "cost": round(c1, 2),
                        "gp": round(gp1, 2), "gp_pct": round(gp1 / r1 * 100, 1) if r1 > 0 else None,
                        "prev_gp": _r(gp0), "gp_change": _r(gp1 - gp0) if gp0 is not None else None})
            continue
        if q1 > 0 and k1 < q1 - 1e-6:
            unc_buf.append({"sku_code": code, "name": meta["name"], "plates": round(q1, 1), "uncosted_plates": round(q1 - k1, 1),
                            "plates_per_100": round(q1 / n1 * PER, 2) if n1 else None})
        if not prev:
            continue
        im = impact(q1, c1, k1, n1, q0, c0, k0, n0)
        if im is None or abs(im["impact"]) < 0.5:
            continue
        v = im["impact"]
        brs = []
        for br in set(p1.get(nm, {})) | set(p0.get(nm, {})):
            b1 = p1.get(nm, {}).get(br, [0.0, 0.0, 0.0])
            b0 = p0.get(nm, {}).get(br, [0.0, 0.0, 0.0])
            bi = impact(b1[0], b1[1], b1[2], float(cur["cust"].get(br, 0)), b0[0], b0[1], b0[2], float(prev["cust"].get(br, 0)))
            if bi and (bi["impact"] > 0.5 if v > 0 else bi["impact"] < -0.5):
                brs.append({"branch": br, "impact": round(bi["impact"], 2)})
        brs.sort(key=lambda x: (-abs(x["impact"]), x["branch"]))
        buckets["down" if v < 0 else ("price" if im["cause"] == "price" else "eat")].append({
            "sku_code": code, "name": meta["name"], "impact": round(v, 2), "cause": im["cause"], "eat": _r(im["eat"]), "price": _r(im["price"]),
            "impact_share": round(v / food1 * 100, 2) if food1 > 0 else None,
            "cost": round(c1, 2), "prev_cost": round(c0, 2), "cost_per_head": ph(c1, n1), "prev_cost_per_head": ph(c0, n0),
            "plates_per_100": round(q1 / n1 * PER, 2) if n1 else None, "prev_plates_per_100": round(q0 / n0 * PER, 2) if n0 else None,
            "cost_per_plate": _r(c1 / k1) if k1 else None, "prev_cost_per_plate": _r(c0 / k0) if k0 else None,
            "above_floor": abs(v) >= fl,
            "branches": [] if branch else brs[:BRANCHES_SHOWN], "branches_more": 0 if branch else max(len(brs) - BRANCHES_SHOWN, 0),
            "_sids": meta["sids"], "_cpp": (c1 / k1) if k1 else ((c0 / k0) if k0 else 0.0)})
    for k in BUCKETS:
        lst = buckets[k]
        lst.sort(key=lambda r: (r["impact"] if k == "down" else -r["impact"], r["name"]))
        out["buffet"]["totals"][k] = round(sum(r["impact"] for r in lst), 2)
        out["buffet"]["counts"][k] = len(lst)
        _cum(lst, "impact", out["buffet"]["totals"][k])
        buckets[k] = lst[:limit]
    # the buffet price tiers behind the plates: the dishes on screen whose cause is the plates (a price change is no tier's)
    tl = [r for k in BUCKETS for r in buckets[k][:15] if r["cause"] != "price"]
    tnote = None
    if tl:
        lo, hi = _fact_span(con)
        if not lo or lo > w["prev"][0] or hi < w["cur"][1]:
            tnote = f"ข้อมูลแยกระดับราคามีถึง {P.label_th(hi, hi)}" if hi else "ยังไม่มีข้อมูลแยกระดับราคา"
        else:
            sids = sorted({s for r in tl for s in r["_sids"]})
            pc, qc = _fact(con, *w["cur"], branch, group, sids)
            pp, qp = _fact(con, *w["prev"], branch, group, sids)

            def agg(pax, qty):
                tp: Dict[str, float] = {}
                tq: Dict[Tuple[str, int], float] = {}
                for (_b, t), v in pax.items():
                    tp[t] = tp.get(t, 0.0) + v
                for (_b, t, s), v in qty.items():
                    tq[(t, s)] = tq.get((t, s), 0.0) + v
                return tp, tq
            tp1, tq1 = agg(pc, qc)
            tp0, tq0 = agg(pp, qp)
            for r in tl:
                rows = []
                for t, x1 in tp1.items():
                    x0 = tp0.get(t, 0.0)
                    if x1 <= 0 or x0 <= 0:
                        continue
                    a1 = sum(tq1.get((t, s), 0.0) for s in r["_sids"])
                    a0 = sum(tq0.get((t, s), 0.0) for s in r["_sids"])
                    v = (a1 / x1 - a0 / x0) * x1 * r["_cpp"]
                    if (v > 0.5) if r["impact"] > 0 else (v < -0.5):
                        rows.append({"tier": t, "label": tier_label(t), "impact": round(v, 2)})
                rows.sort(key=lambda x: -abs(x["impact"]))
                r["tiers"] = rows[:TIERS_SHOWN]
    for k in BUCKETS:
        for r in buckets[k]:
            r.pop("_sids", None); r.pop("_cpp", None)
    out["buffet"]["buckets"] = buckets
    out["buffet"]["net"] = round(sum(out["buffet"]["totals"].values()), 2) if prev else None
    unc_buf.sort(key=lambda r: (-r["uncosted_plates"], r["name"]))
    out["buffet"]["uncosted"] = unc_buf[:limit]
    out["buffet"]["uncosted_count"] = len(unc_buf)
    alc.sort(key=lambda r: (-r["gp"], r["name"]))
    gp_tot = sum(r["gp"] for r in alc)
    _cum(alc, "gp", gp_tot)
    afl = floor_of(revenue1)
    for r in alc:
        r["above_floor"] = abs(r["gp"]) >= afl
    unc_alc.sort(key=lambda r: (-r["revenue"], r["name"]))
    out["alc"] = {"rows": alc[:limit], "count": len(alc), "revenue": round(alc_rev, 2), "gp": round(gp_tot, 2),
                  "revenue_share": round(alc_rev / revenue1 * 100, 2) if revenue1 > 0 else None, "floor_baht": round(afl, 2),
                  "uncosted": unc_alc[:limit], "uncosted_count": len(unc_alc)}
    out["tier_note"] = tnote
    out["period"] = dict(P.public(w) or {}, reasons={k: v for k, v in reasons.items() if k in ("prev", "ly") and v})
    out["reasons"] = {k: v for k, v in reasons.items() if v}
    return out


# ── โอกาสเมนู ────────────────────────────────────────────────────────────────────────────────────────────────────────────
def _months_back(end_iso: str, complete: bool, n: int) -> List[str]:
    """The n whole months up to the period's end (its own month counts only when the period ends on the month's last day)."""
    y, m = int(end_iso[:4]), int(end_iso[5:7])
    if not (complete and int(end_iso[8:10]) == calendar.monthrange(y, m)[1]):
        y, m = (y, m - 1) if m > 1 else (y - 1, 12)
    out = []
    for _ in range(n):
        out.append(f"{y:04d}-{m:02d}")
        y, m = (y, m - 1) if m > 1 else (y - 1, 12)
    return sorted(out)


def _slope_pct(series: List[float]) -> float:
    nz = [(i, v) for i, v in enumerate(series) if v > 0]
    if len(nz) < 3:
        return 0.0
    xs = [p[0] for p in nz]; ys = [p[1] for p in nz]
    xm = sum(xs) / len(xs); ym = sum(ys) / len(ys)
    num = sum((x - xm) * (y - ym) for x, y in zip(xs, ys))
    den = sum((x - xm) ** 2 for x in xs) or 1
    return round((num / den) / ym * 100, 1) if ym else 0.0


def _history(con, months: List[str], branch, group) -> Tuple[List[Dict], List[Dict]]:
    """À-la-carte food dishes over whole months (v_sales_sku_monthly): revive (sold before, nothing in the last 2 months; revenue
    per month while it sold) and the categories (revenue per dish, trend). -> (revive, categories)."""
    if not months:
        return [], []
    from core import _FOOD_CATEGORY_FILTER
    kind_of, name_of = _spike()
    bw, bp = PC._bscope(branch, group)
    ph = ",".join(f":_m{i}" for i in range(len(months)))
    mp = {f"_m{i}": m for i, m in enumerate(months)}
    rows = con.execute(f"""SELECT sk.sku_code code, sk.description descr, COALESCE(c.name, 'Uncategorised') cat,
                                  ssm.period_month m, SUM(ssm.qty) q, SUM(ssm.subtotal_ex_vat) rv
                           FROM v_sales_sku_monthly ssm JOIN branches b ON b.branch_id = ssm.branch_id
                           JOIN skus sk ON sk.sku_id = ssm.sku_id LEFT JOIN categories c ON c.category_id = sk.category_id
                           WHERE {bw} AND ssm.period_month IN ({ph}) AND {needs_cost_sql('sk.sku_code')} AND {_FOOD_CATEGORY_FILTER}
                           GROUP BY sk.sku_id, ssm.period_month""", {**bp, **mp}).fetchall()
    dish: Dict[str, Dict] = {}
    for r in rows:
        if kind_of(r["descr"]) != "alc":
            continue
        nm = name_of(r["descr"], r["code"]).strip() or r["code"]
        d = dish.setdefault(nm, {"code": r["code"], "cat": r["cat"], "q": {}, "rv": {}})
        d["q"][r["m"]] = d["q"].get(r["m"], 0.0) + float(r["q"] or 0)
        d["rv"][r["m"]] = d["rv"].get(r["m"], 0.0) + float(r["rv"] or 0)
    revive = []
    cats: Dict[str, Dict] = {}
    for nm, d in dish.items():
        qs = [d["q"].get(m, 0.0) for m in months]
        rvs = [d["rv"].get(m, 0.0) for m in months]
        if sum(qs) > 0:
            c = cats.setdefault(d["cat"], {"category": d["cat"], "n_skus": 0, "revenue": 0.0, "series": [0.0] * len(months)})
            c["n_skus"] += 1
            c["revenue"] += sum(rvs)
            c["series"] = [a + b for a, b in zip(c["series"], rvs)]
        if len(months) >= 3 and all(v <= 0 for v in qs[-2:]) and any(v > 0 for v in qs):
            alive = [i for i, v in enumerate(qs) if v > 0]
            revive.append({"sku_code": d["code"], "name": nm, "category": d["cat"],
                           "revenue_per_month": round(sum(rvs[i] for i in alive) / len(alive), 2),
                           "plates_per_month": round(sum(qs[i] for i in alive) / len(alive), 1), "last_month": months[alive[-1]]})
    revive.sort(key=lambda r: (-r["revenue_per_month"], r["name"]))
    out = [{"category": c["category"], "n_skus": c["n_skus"], "revenue": round(c["revenue"], 2),
            "revenue_per_sku": round(c["revenue"] / c["n_skus"], 2), "slope_pct": _slope_pct(c["series"])} for c in cats.values()]
    med = _median([c["revenue_per_sku"] for c in out])
    for c in out:
        if c["revenue_per_sku"] > med and c["slope_pct"] >= 0:
            c["label"], c["reason"] = "expand", "รายได้ต่อเมนูสูงและไม่ลดลง"
        elif c["slope_pct"] < -10:
            c["label"], c["reason"] = "trim", "รายได้ของหมวดลดลง"
        else:
            c["label"], c["reason"] = "watch", "ทรงตัว"
    out.sort(key=lambda c: (-c["revenue"], c["category"]))
    return revive, out


def _over_rows(over_of, scope, cust, cpp_of, cost_view):
    """[(branch, excess plates, actual, expected, tier-or-None)] -> the branch rows of one dish above 1.2x its expectation."""
    rows = []
    for b in scope:
        o = over_of(b)
        if not o:
            continue
        act, exp, top = o
        if exp <= 0 or act < OVER_X * exp:
            continue
        ex = act - exp
        r = {"branch": b, "excess_plates": round(ex, 1), "x": round(act / exp, 2),
             "rate": round(act / cust[b] * PER, 2) if cust.get(b) else None,
             "normal_rate": round(exp / cust[b] * PER, 2) if cust.get(b) else None}
        if top:
            r["tier"] = {"tier": top[0], "label": tier_label(top[0]), "excess_plates": round(top[1], 1)}
        if cost_view:
            cpp = cpp_of(b)
            r["excess_cost"] = round(ex * cpp, 2) if cpp else None
        rows.append(r)
    return rows


def opportunities(con, w: Optional[Dict], branch=None, group=None, cost_view: bool = False, limit: int = LIMIT) -> Dict:
    """The answer of โอกาสเมนู (see the module doc). Without cost_view not one cost key is read or returned."""
    out = {"period": P.public(w), "branch": branch or None, "group": group or None, "cost_view": bool(cost_view),
           "buffet_over": {"rows": [], "count": 0, "total_plates": 0.0, "floor_plates": None, "basis": None, "basis_note": None},
           "alc_gaps": {"rows": [], "count": 0, "total_revenue": 0.0, "floor_baht": None},
           "revive": [], "category_whitespace": [], "months": [], "reasons": {}}
    if not w:
        out["reasons"] = {"cur": "ไม่มียอดขายในช่วงนี้"}
        return out
    floor_day = PC.cost_floor(con)
    months = _months_back(w["cur"][1], bool(w.get("complete")), LOOKBACK_MONTHS)
    out["months"] = months
    revive, cats = _history(con, months, branch, group)
    for r in revive:
        r["above_floor"] = r["revenue_per_month"] >= FLOOR_BAHT
    out["revive"] = revive[:20]
    out["category_whitespace"] = cats
    # the median needs every branch of the scope: read the group (or the chain), list the chosen branch's own rows only
    data, why = IG._rows(con, *w["cur"], None, group, floor_day, with_cost=cost_view)
    if data is None:
        out["reasons"] = {"cur": why}
        return out
    rev = _dish_revenue(con, *w["cur"], None, group, floor_day) or {}
    cust = {b: float(n or 0) for b, n in data["cust"].items()}
    info = _skus(con, {sid for _b, sid in data["dish"]})
    t, per = _by_name(data, info)
    names_kind: Dict[str, Dict] = {}
    for sid, x in info.items():
        k = names_kind.setdefault(x["key"], {"kind": x["kind"], "food": x["food"], "name": x["name"], "codes": [], "sids": []})
        k["codes"].append(x["code"]); k["sids"].append(sid)
    rv: Dict[Tuple[str, str], float] = {}
    for (b, s), v in rev.items():
        if s in info:
            rv[(b, info[s]["key"])] = rv.get((b, info[s]["key"]), 0.0) + v
    scope = [branch] if branch else sorted(b for b in cust if cust[b] > 0)
    food = sum(float(data["food"].get(b, 0.0)) for b in scope) if cost_view else 0.0
    revenue = sum(v for (b, s), v in rev.items() if b in scope)

    # buffet: tier-fair when the fact tables cover the window (the other branches' median plates per customer of the same tier),
    # else per customer of the branch (every customer)
    lo, hi = _fact_span(con)
    long_window = (w.get("days") or 0) > TIER_MAX_DAYS
    tier_ok = bool(lo and hi and lo <= w["cur"][0] and hi >= w["cur"][1]) and not long_window
    fpax: Dict[Tuple[str, str], float] = {}
    fq: Dict[Tuple[str, str, int], float] = {}
    if tier_ok:
        fpax, fq = _fact(con, *w["cur"], None, group)
        tier_ok = bool(fpax)
    basis = "tier" if tier_ok else "all"
    out["buffet_over"]["basis"] = basis
    out["buffet_over"]["basis_note"] = None if tier_ok else (
        f"ช่วงยาวกว่า {TIER_MAX_DAYS} วัน จึงเทียบต่อลูกค้าทั้งหมดของสาขา" if long_window else
        f"ข้อมูลแยกระดับราคามีถึง {P.label_th(hi, hi)} จึงเทียบต่อลูกค้าทั้งหมดของสาขา" if hi else
        "ยังไม่มีข้อมูลแยกระดับราคา จึงเทียบต่อลูกค้าทั้งหมดของสาขา")
    fq_by: Dict[str, Dict[Tuple[str, str], float]] = {}
    if tier_ok:
        for (b, tr, s), v in fq.items():
            if s in info and info[s]["kind"] == "buffet":
                d = fq_by.setdefault(info[s]["key"], {})
                d[(b, tr)] = d.get((b, tr), 0.0) + v
    tiers = sorted({tr for _b, tr in fpax})
    buf_plates = 0.0
    over, gaps = [], []
    for nm, bq in per.items():
        meta = names_kind[nm]
        code = sorted(meta["codes"])[0]
        sellers = {b: v for b, v in bq.items() if v[0] > 0 and cust.get(b, 0) > 0}
        q_all = sum(v[0] for v in sellers.values())
        if meta["kind"] == "buffet":
            buf_plates += sum(v[0] for b, v in bq.items() if b in scope)
            k_all = sum(v[2] for v in sellers.values())
            chain_cpp = (sum(v[1] for v in sellers.values()) / k_all) if (cost_view and k_all > 0) else None

            def cpp_of(b, bq=bq, chain_cpp=chain_cpp):
                v = bq.get(b)
                return (v[1] / v[2]) if (v and v[2] > 0) else chain_cpp
            if tier_ok:
                q = fq_by.get(nm, {})
                meds = {}
                for tr in tiers:
                    rates = [q[(b, tr)] / fpax[(b, tr)] for b in cust if q.get((b, tr), 0) > 0 and fpax.get((b, tr), 0) > 0]
                    if len(rates) >= MIN_SELLERS:
                        meds[tr] = _median(rates)
                if not meds:
                    continue
                n_sell = len({b for (b, _tr), v in q.items() if v > 0})

                def over_of(b, q=q, meds=meds):
                    act = exp = 0.0
                    best = None
                    for tr in tiers:
                        a = q.get((b, tr), 0.0)
                        p = fpax.get((b, tr), 0.0)
                        e = meds[tr] * p if tr in meds else a
                        act += a; exp += e
                        if a - e > 0 and (best is None or a - e > best[1]):
                            best = (tr, a - e)
                    return (act, exp, best) if act > 0 else None
                med_show = None
            else:
                if len(sellers) < MIN_SELLERS:
                    continue
                med = _median([v[0] / cust[b] for b, v in sellers.items()])
                if med <= 0:
                    continue
                n_sell = len(sellers)

                def over_of(b, med=med, bq=bq):
                    v = bq.get(b)
                    return (v[0], med * cust[b], None) if (v and v[0] > 0 and cust.get(b, 0) > 0) else None
                med_show = round(med * PER, 2)
            rows = _over_rows(over_of, scope, cust, cpp_of, cost_view)
            if not rows:
                continue
            key = "excess_cost" if cost_view else "excess_plates"
            rows.sort(key=lambda r: (-(r.get(key) or 0), r["branch"]))
            row = {"sku_code": code, "name": meta["name"], "excess_plates": round(sum(r["excess_plates"] for r in rows), 1),
                   "n_over": len(rows), "n_selling": n_sell, "median_rate": med_show}
            if cost_view:
                row["excess_cost"] = round(sum(r.get("excess_cost") or 0 for r in rows), 2)
            if branch:
                row.update({k: rows[0].get(k) for k in ("rate", "normal_rate", "x", "tier")})
            else:
                row["branches"] = rows[:BRANCHES_SHOWN]
                row["branches_more"] = max(len(rows) - BRANCHES_SHOWN, 0)
            over.append(row)
            continue
        # à la carte (dine-in food dishes)
        if not meta["food"] or len(sellers) < MIN_SELLERS:
            continue
        rv_all = sum(rv.get((b, nm), 0.0) for b in sellers)
        if q_all <= 0 or rv_all <= 0:
            continue
        price = rv_all / q_all
        med = _median([v[0] / cust[b] for b, v in sellers.items()])
        rows = []
        for b in scope:
            if cust.get(b, 0) <= 0:
                continue
            qb = bq.get(b, [0.0])[0]
            rate = qb / cust[b]
            if rate >= GAP_X * med:
                continue
            pot = (med - rate) * cust[b]
            rows.append({"branch": b, "potential_plates": round(pot, 1), "potential_revenue": round(pot * price, 2),
                         "rate": round(rate * PER, 2), "not_sold": qb <= 0})
        if not rows:
            continue
        rows.sort(key=lambda r: (-r["potential_revenue"], r["branch"]))
        row = {"sku_code": code, "name": meta["name"], "price": round(price, 2), "median_rate": round(med * PER, 2),
               "potential_revenue": round(sum(r["potential_revenue"] for r in rows), 2),
               "potential_plates": round(sum(r["potential_plates"] for r in rows), 1), "n_branches": len(rows), "n_selling": len(sellers)}
        if branch:
            row.update({"rate": rows[0]["rate"], "not_sold": rows[0]["not_sold"]})
        else:
            row["branches"] = rows[:BRANCHES_SHOWN]
            row["branches_more"] = max(len(rows) - BRANCHES_SHOWN, 0)
        gaps.append(row)
    okey = "excess_cost" if cost_view else "excess_plates"
    over.sort(key=lambda r: (-(r.get(okey) or 0), r["name"]))
    gaps.sort(key=lambda r: (-r["potential_revenue"], r["name"]))
    fl_plates = max(1.0, FLOOR_SHARE * buf_plates)
    fl_cost = floor_of(food) if cost_view else None
    fl_rev = floor_of(revenue)
    tot_o = sum(r.get(okey) or 0 for r in over)
    tot_g = sum(r["potential_revenue"] for r in gaps)
    _cum(over, okey, tot_o)
    _cum(gaps, "potential_revenue", tot_g)
    for r in over:
        r["above_floor"] = ((r.get("excess_cost") or 0) >= fl_cost) if cost_view else (r["excess_plates"] >= fl_plates)
    for r in gaps:
        r["above_floor"] = r["potential_revenue"] >= fl_rev
    bo = {"rows": over[:limit], "count": len(over), "total_plates": round(sum(r["excess_plates"] for r in over), 1),
          "floor_plates": round(fl_plates, 1), "buffet_plates": round(buf_plates, 1), "basis": basis,
          "basis_note": out["buffet_over"]["basis_note"]}
    if cost_view:
        bo.update({"total_cost": round(tot_o, 2), "floor_baht": round(fl_cost, 2), "food_cost": round(food, 2)})
    out["buffet_over"] = bo
    out["alc_gaps"] = {"rows": gaps[:limit], "count": len(gaps), "total_revenue": round(tot_g, 2), "floor_baht": round(fl_rev, 2),
                       "revenue": round(revenue, 2)}
    out["reasons"] = {k: v for k, v in P.reasons(w).items() if v}
    return out
