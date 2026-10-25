# -*- coding: utf-8 -*-
"""PR-PP3 · the cost figures of one period pattern (docs/PERIOD_PATTERN.md): COGS, GP, food-cost %, cost per head on the
windows of periods.windows (week · month · year, to date when the period is not finished).

ONE engine (the app-wide COGS expression, core._unified_cogs_expr) over two sources:
    whole calendar months   v_sales_sku_monthly  (the month table: the same rows the P&L and GP pages read)
    any other stretch       v_sales_sku_daily    (day-level dish cost; the sync keeps a rolling window, `cost_floor`)
A window is split into its months: a whole month reads the month table, a part month the day table. A part month that
starts before the day table has no cost source: the window gets no figure and a reason ("—" with the reason in "?").
Revenue for GP and food-cost % is the same table's ex-VAT dish revenue (GP = revenue − COGS on one base); covers come
from the bills of the same days (sales_transaction)."""
import calendar
from datetime import date, timedelta
from typing import Dict, List, Optional, Tuple

import periods as P

try:
    from core import _unified_cogs_expr
    from rules import coverage_sums_sql
except ImportError:  # pragma: no cover
    from .core import _unified_cogs_expr
    from .rules import coverage_sums_sql


def cost_floor(con) -> Optional[str]:
    """The first day of the day-level dish table (None when it is empty)."""
    try:
        return con.execute("SELECT MIN(day) FROM sales_sku_daily").fetchone()[0]
    except Exception:
        return None


def _mlen(y: int, m: int) -> int:
    return calendar.monthrange(y, m)[1]


def segments(s: str, e: str) -> List[Tuple[str, str, bool]]:
    """[s, e] cut at month ends -> [(from, to, whole_month)]."""
    a, b = date.fromisoformat(s[:10]), date.fromisoformat(e[:10])
    out = []
    while a <= b:
        me = date(a.year, a.month, _mlen(a.year, a.month))
        to = min(me, b)
        out.append((a.isoformat(), to.isoformat(), a.day == 1 and to == me))
        a = to + timedelta(days=1)
    return out


def floor_reason(floor: Optional[str]) -> str:
    return f"ต้นทุนรายวันมีตั้งแต่ {P.label_th(floor, floor)}" if floor else "ยังไม่มีต้นทุนรายวัน"


def plan(s: str, e: str, floor: Optional[str]) -> Tuple[Optional[Dict], Optional[str]]:
    """The sources of [s, e]: ({months: [...], days: [(from, to)], src}, None) or (None, reason)."""
    months, days = [], []
    for a, b, whole in segments(s, e):
        if whole:
            months.append(a[:7])
        elif floor and a >= floor:
            days.append((a, b))
        else:
            return None, floor_reason(floor)
    src = "month" if months and not days else "day" if days and not months else "mix"
    return {"months": months, "days": days, "src": src}, None


def _bscope(branch: Optional[str], group: Optional[str], alias: str = "b") -> Tuple[str, dict]:
    f, p = [f"{alias}.name != 'ALL'"], {}
    if branch:
        f.append(f"{alias}.name = :branch")
        p["branch"] = branch
    if group:
        f.append(f"COALESCE({alias}.branch_type,'master') = :branch_group")
        p["branch_group"] = group
    return " AND ".join(f), p


def cost_sum(con, s: str, e: str, branch: Optional[str] = None, group: Optional[str] = None,
             floor: Optional[str] = None, by_branch: bool = False):
    """The cost figures of [s, e] -> (figures | {branch: figures}, reason). figures = {revenue, cogs, gp, gp_pct,
    food_cost_pct, customers, cost_per_head, gp_per_head, rev_per_head, src}. (None, reason) without a cost source."""
    pl, why = plan(s, e, floor)
    if pl is None:
        return None, why
    bw, bp = _bscope(branch, group)
    cg = _unified_cogs_expr("ssm")
    grp, sel = ("GROUP BY b.name", "b.name AS branch,") if by_branch else ("", "")
    acc: Dict[str, Dict[str, float]] = {}

    cov = coverage_sums_sql("ssm", "sk.sku_code")

    def add(rows):
        for r in rows:
            k = r["branch"] if by_branch else "_"
            a = acc.setdefault(k, {"revenue": 0.0, "cogs": 0.0, "plates": 0.0, "plates_costed": 0.0})
            a["revenue"] += float(r["rev"] or 0)
            a["cogs"] += float(r["cogs"] or 0)
            a["plates"] += float(r["cov_plates"] or 0)
            a["plates_costed"] += float(r["cov_plates_costed"] or 0)

    if pl["months"]:
        ph = ",".join(f":_m{i}" for i in range(len(pl["months"])))
        mp = {f"_m{i}": m for i, m in enumerate(pl["months"])}
        add(con.execute(f"""SELECT {sel} SUM(ssm.subtotal_ex_vat) rev, SUM({cg}) cogs, {cov}
                            FROM v_sales_sku_monthly ssm JOIN branches b ON b.branch_id = ssm.branch_id
                            JOIN skus sk ON sk.sku_id = ssm.sku_id
                            WHERE {bw} AND ssm.period_month IN ({ph}) {grp}""", {**bp, **mp}).fetchall())
    for i, (a, b) in enumerate(pl["days"]):
        add(con.execute(f"""SELECT {sel} SUM(ssm.subtotal_ex_vat) rev, SUM({cg}) cogs, {cov}
                            FROM (SELECT branch_id, sku_id, qty, subtotal_ex_vat, 'Easy' AS source,
                                         substr(day, 1, 7) AS period_month
                                  FROM v_sales_sku_daily WHERE day BETWEEN :_s AND :_e) ssm
                            JOIN branches b ON b.branch_id = ssm.branch_id
                            JOIN skus sk ON sk.sku_id = ssm.sku_id
                            WHERE {bw} {grp}""", {**bp, "_s": a, "_e": b}).fetchall())
    tw, tp = _bscope(branch, group)
    cust = {(r["branch"] if by_branch else "_"): (r["c"] or 0) for r in con.execute(
        f"""SELECT {sel} SUM(t.customer_count) c FROM sales_transaction t JOIN branches b ON b.branch_id = t.branch_id
            WHERE t.source = 'Easy' AND {tw} AND date(t.sold_at) BETWEEN :_s AND :_e {grp}""",
        {**tp, "_s": s[:10], "_e": e[:10]}).fetchall()}

    def one(a, cu):
        rev, cogs = round(a["revenue"], 2), round(a["cogs"], 2)
        gp = round(rev - cogs, 2)
        return {"revenue": rev, "cogs": cogs, "gp": gp,
                "gp_pct": round(gp / rev * 100, 2) if rev > 0 else None,
                "food_cost_pct": round(cogs / rev * 100, 2) if rev > 0 else None,
                "customers": cu, "cost_per_head": round(cogs / cu, 1) if cu else None,
                "gp_per_head": round(gp / cu, 1) if cu else None,
                "rev_per_head": round(rev / cu, 1) if cu else None,
                # the share of the plates that should carry a cost and do (rules.coverage_sums_sql, the app-wide definition)
                "cost_coverage_pct": round(a["plates_costed"] / a["plates"] * 100, 1) if a["plates"] else None,
                "src": pl["src"]}
    if by_branch:
        return {k: one(v, cust.get(k, 0)) for k, v in acc.items() if v["revenue"] or v["cogs"]}, None
    a = acc.get("_")
    if not a or not (a["revenue"] or a["cogs"]):
        return None, "ไม่มียอดขายในช่วงนี้"
    return one(a, cust.get("_", 0)), None


# the figures of a cost page and how each compares: (key, unit, up_good) - baht and per head in %, ratios in จุด
FIGS = (("revenue", "%", True), ("cogs", "%", False), ("gp", "%", True), ("gp_pct", "pp", True),
        ("food_cost_pct", "pp", False), ("cost_per_head", "%", False), ("gp_per_head", "%", True),
        ("cost_coverage_pct", "pp", True))


def change(cur: Optional[Dict], base: Optional[Dict]) -> Dict:
    out = {}
    for k, unit, _ in FIGS:
        c, b = (cur or {}).get(k), (base or {}).get(k)
        out[k] = (P.pp(c, b) if unit == "pp" else P.pct(c, b)) if (cur and base) else None
    return out


def compare(con, w: Dict, branch: Optional[str] = None, group: Optional[str] = None, by_branch: bool = False) -> Dict:
    """cur / prev / ly cost figures of one periods.windows result, each base with its reason when it has no figure.
    -> {period, cost_floor, cur, prev, ly, change: {prev: {...}, ly: {...}}, reasons: {cur, prev, ly}, branches}"""
    floor = cost_floor(con)
    reasons = dict(P.reasons(w))
    cur, reasons["cur"] = cost_sum(con, *w["cur"], branch, group, floor)
    prev = ly = None
    if w.get("has_prev"):
        prev, r = cost_sum(con, *w["prev"], branch, group, floor)
        reasons["prev"] = reasons.get("prev") or r
    if w.get("has_ly"):
        ly, r = cost_sum(con, *w["ly"], branch, group, floor)
        reasons["ly"] = reasons.get("ly") or r
    branches = []
    if by_branch and cur:
        bc = cost_sum(con, *w["cur"], branch, group, floor, by_branch=True)[0] or {}
        bpv = (cost_sum(con, *w["prev"], branch, group, floor, by_branch=True)[0] or {}) if prev else {}
        bly = (cost_sum(con, *w["ly"], branch, group, floor, by_branch=True)[0] or {}) if ly else {}
        for nm, c in sorted(bc.items(), key=lambda kv: -(kv[1]["revenue"] or 0)):
            p_, y_ = bpv.get(nm), bly.get(nm)
            branches.append({"branch": nm, "cur": c, "prev": p_, "ly": y_,
                             "change": {"prev": change(c, p_), "ly": change(c, y_)}})
    per = P.public(w) or {}
    per["reasons"] = {k: v for k, v in reasons.items() if k in ("prev", "ly") and v}
    return {"period": per, "cost_floor": floor, "cur": cur, "prev": prev, "ly": ly,
            "change": {"prev": change(cur, prev), "ly": change(cur, ly)},
            "reasons": reasons, "branches": branches}
