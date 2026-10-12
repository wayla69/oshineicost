# -*- coding: utf-8 -*-
"""PR-X — ตรวจสูตรและหน่วยด้วยยอดซื้อจริง: the recipes and unit rules read against what the company branches really bought.

For one month, for every ingredient: what the benchmark branches BOUGHT per ฿100,000 of sales (the price file's per-branch
purchases, base unit) against what the RECIPES say they used (forecast.usage_history over the same days, the one definition
of usage). Both are the median over the benchmark branches that have the item (benchmark.median_of).

  * Sales   = Σ `subtotal_ex_vat` of the sales table the forecast reads (ex VAT, ex service charge) over the days of the month
              that have daily sales. The purchase file covers the WHOLE month, so a branch whose daily sales cover only some of
              its days has its purchases scaled by (days with daily sales ÷ days in the month), and the page says so.
  * A branch-month with a submitted begin AND end count reads the purchases with the stock change (bought + begin - end) and is
    judged by the normal variance threshold of the item's class; every other branch by the temporary buffer (benchmark.buffer_pct).
  * Status:  ok (within the band) · more (bought more than the recipe) · less (bought less) · x10 (the two differ by ten times or
    more: check the unit) · no_recipe (bought, in no recipe) · no_buy (in a recipe, never bought: check the code).

It changes nothing: each row points at the ladder of the ingredient or at the recipes that use it.
"""
import threading
import time as _time

from . import benchmark as BM
from . import forecast as FC
from . import per_sales as PS
from .prices import price_for

X10 = 10.0
STATUS_ORDER = ("x10", "no_recipe", "no_buy", "more", "less", "ok")
STATUS_TH = {"ok": "ในเกณฑ์", "more": "ซื้อมากกว่าสูตร", "less": "ซื้อน้อยกว่าสูตร",
             "x10": "ต่างกันเกิน 10 เท่า · ตรวจหน่วย", "no_recipe": "มีการซื้อ แต่ไม่มีในสูตร",
             "no_buy": "มีในสูตร แต่ไม่มีการซื้อ · ตรวจรหัส"}
SHEET = "ตรวจสูตรด้วยยอดซื้อ"

_cache: dict = {}
_build_lock = threading.Lock()       # single-flight: a burst of callers on a cold cache builds the page once


def clear_cache() -> None:
    _cache.clear()


def months(con) -> list:
    """[{month, branches, complete}] newest first: the months the price file holds per-branch purchases of, with how many of the
    benchmark branches have rows in it. `complete` = the month is over."""
    ids = BM.branch_ids(con)
    if not ids:
        return []
    q = ",".join("?" * len(ids))
    now = f"{PS.bkk_today().year:04d}-{PS.bkk_today().month:02d}"
    return [{"month": m, "branches": n, "complete": m < now} for m, n in con.execute(
        f"SELECT month, COUNT(DISTINCT branch_id) FROM bc_ingredient_price_branch WHERE branch_id IN ({q})"
        " GROUP BY month ORDER BY month DESC", ids)]


def default_month(con):
    """The newest finished month with purchases of at least MIN_BRANCHES benchmark branches, else the newest month with any."""
    ms = months(con)
    for m in ms:
        if m["complete"] and m["branches"] >= BM.MIN_BRANCHES:
            return m["month"]
    return ms[0]["month"] if ms else None


def _signature(con, month: str, ids) -> tuple:
    # a re-imported price file keeps the same row count: its values are part of the signature; a count keeps its id when
    # it is submitted (NULL status = submitted): the submitted state and time are part of it too
    a = con.execute("SELECT COUNT(*), COALESCE(SUM(qty_bought), 0), COALESCE(SUM(price_per_base), 0), COALESCE(SUM(value_bought), 0)"
                    " FROM bc_ingredient_price_branch WHERE month=?", (month,)).fetchone()
    b = con.execute("SELECT COUNT(*), COALESCE(MAX(count_id), 0), COALESCE(SUM(COALESCE(status, 'submitted') = 'submitted'), 0),"
                    " COALESCE(MAX(submitted_at), '') FROM bc_stock_count").fetchone()
    c = con.execute("SELECT COUNT(*), COALESCE(SUM(price_per_base), 0) FROM bc_ingredient_price WHERE month=?", (month,)).fetchone()
    return (tuple(a), tuple(b), tuple(c))


def check(con, month: str = None, today=None) -> dict:
    """The recipe check of one month (default: default_month). Cached like the per-฿100,000 report (same TTL, dropped by
    forecast.clear_usage_cache)."""
    ids = BM.branch_ids(con)
    month = month or default_month(con)
    if not month:
        return {"month": None, "months": [], "rows": [], "branches": [], "summary": {}, "message": "ยังไม่มียอดซื้อรายสาขาในไฟล์ราคา"}
    key = None
    try:
        path = next((r[2] for r in con.execute("PRAGMA database_list") if r[1] == "main"), "")
        core = FC._loaded("core")
        key = (path, month, tuple(ids), BM.buffer_pct(con), _signature(con, month, ids), getattr(core, "_data_gen", 0),
               getattr(core, "_db_gen", 0)) if path and FC.USAGE_CACHE_TTL else None
    except Exception:
        key = None
    if key is not None:
        hit = _cache.get(key)
        if hit and _time.monotonic() - hit[0] < FC.USAGE_CACHE_TTL:
            return hit[1]
    with _build_lock:
        if key is not None:
            hit = _cache.get(key)
            if hit and _time.monotonic() - hit[0] < FC.USAGE_CACHE_TTL:
                return hit[1]
        out = _build(con, month, ids, today)
        if key is not None:
            if len(_cache) >= 8:
                _cache.pop(min(_cache, key=lambda k: _cache[k][0]))
            _cache[key] = (_time.monotonic(), out)
    return out


def _build(con, month: str, ids: list, today=None) -> dict:
    buf = BM.buffer_pct(con)
    d0, dlast = BM.month_days(month)
    n_month = (dlast - d0).days + 1
    end = PS._closed_end(con, today)
    d1 = min(dlast, end)
    names = {c: (n, u) for c, n, u in con.execute("SELECT code, name_th, base_unit FROM bc_ingredient")}
    bnames = {b["branch_id"]: b["name"] for b in BM.all_branches(con)}
    per_branch, info, skipped = {}, [], []
    for bid in ids:
        bought = {c: q for c, q in con.execute(
            "SELECT ingredient_code, qty_bought FROM bc_ingredient_price_branch WHERE month=? AND branch_id=?"
            " AND COALESCE(qty_bought, 0) > 0", (month, bid))}
        if not bought:
            skipped.append({"branch_id": bid, "branch": bnames.get(bid), "why": "ไม่มียอดซื้อรายสาขาในไฟล์ราคาเดือนนี้"})
            continue
        sales_day = {d: v for d, v in PS.sales_by_day(con, bid, d0, d1).items() if v > 0}
        sales = sum(sales_day.values())
        if sales <= 0:
            skipped.append({"branch_id": bid, "branch": bnames.get(bid), "why": "ไม่มียอดขายรายวันในเดือนนี้"})
            continue
        scale = min(1.0, len(sales_day) / n_month)
        use_b, adjusted, period = BM.adjusted_use(con, bid, month, bought)
        with FC._plan_slots:
            use, _days, _st = FC.usage_history(con, bid, d0, d1, month)
        recipe = {c: sum(s.values()) for c, s in use.items()}
        rows = {}
        for c in set(use_b) | set(recipe):
            rows[c] = {"bought": use_b.get(c, 0.0) * scale / sales * 1e5, "recipe": recipe.get(c, 0.0) / sales * 1e5,
                       "adjusted": c in adjusted}
        per_branch[bid] = rows
        info.append({"branch_id": bid, "branch": bnames.get(bid), "sales": round(sales, 2), "sales_days": len(sales_day),
                     "month_days": n_month, "scale": round(scale, 4), "scaled": scale < 0.9999,
                     "stock_adjusted": bool(period), "adjusted_items": len(adjusted),
                     "begin_date": period["begin"]["date"] if period else None, "end_date": period["end"]["date"] if period else None})
    prices = {}

    def price(code):
        if code not in prices:
            p = price_for(con, code, month)
            prices[code] = p[0] if p else None
        return prices[code]

    codes = set()
    for rows in per_branch.values():
        codes |= set(rows)
    table, skipped_items = [], 0
    for code in codes:
        have = {bid: r[code] for bid, r in per_branch.items() if code in r and (r[code]["bought"] > 0 or r[code]["recipe"] > 0)}
        p = BM.median_of(v["bought"] for v in have.values())
        u = BM.median_of(v["recipe"] for v in have.values())
        if p is None or u is None:
            skipped_items += 1
            continue
        table.append((code, have, p, u))
    klass = FC._abc({c: p * (price(c) or 0.0) for c, _h, p, _u in table})
    rows_out = []
    for code, have, p, u in table:
        k = klass.get(code, "C")
        adj_all = all(v["adjusted"] for v in have.values() if v["bought"] > 0) and any(v["bought"] > 0 for v in have.values())
        tol = BM.tolerance(k, adj_all, buf)
        diff = None
        if p > 0 and u > 0:
            diff = round((p / u - 1.0) * 100.0, 1)
            if p / u >= X10 or p / u <= 1.0 / X10:
                status = "x10"
            elif abs(diff) <= tol:
                status = "ok"
            else:
                status = "more" if diff > 0 else "less"
        elif p > 0:
            status = "no_recipe"
        elif u > 0:
            status = "no_buy"
        else:
            continue
        pr = price(code)
        bl = []
        for bid in per_branch:
            v = have.get(bid)
            bd = None if not v or v["recipe"] <= 0 or v["bought"] <= 0 else round((v["bought"] / v["recipe"] - 1.0) * 100.0, 1)
            bl.append({"branch_id": bid, "branch": bnames.get(bid), "bought": None if not v else round(v["bought"], 3),
                       "recipe": None if not v else round(v["recipe"], 3), "diff_pct": bd,
                       "adjusted": bool(v and v["adjusted"])})
        rows_out.append({"code": code, "name": (names.get(code) or (code, None))[0], "base_unit": (names.get(code) or (None, None))[1],
                         "class": k, "bought": round(p, 3), "recipe": round(u, 3), "diff_pct": diff, "status": status,
                         "status_th": f"ในเกณฑ์ (±{tol:g}%)" if status == "ok" else STATUS_TH[status], "tolerance_pct": tol,
                         "stock_adjusted": adj_all, "n_branches": len(have), "price_per_base": pr,
                         "impact_value": round(abs(p - u) * pr, 2) if pr else None, "branches": bl})
    rows_out.sort(key=lambda r: (-(r["impact_value"] or 0), STATUS_ORDER.index(r["status"]), r["code"]))
    counts = {s: sum(1 for r in rows_out if r["status"] == s) for s in STATUS_ORDER}
    today_ = today or PS.bkk_today()
    partial = dlast >= today_
    return {"month": month, "months": months(con), "from": d0.isoformat(), "to": d1.isoformat(), "month_days": n_month,
            "buffer_pct": buf, "buffer_note": BM.BUFFER_NOTE, "benchmark_label": BM.LABEL,
            "branches": info, "skipped_branches": skipped, "skipped_items": skipped_items,
            "scaled": any(b["scaled"] for b in info), "partial_month": partial,
            "stock_adjusted_branches": sum(1 for b in info if b["stock_adjusted"]),
            "rows": rows_out, "counts": counts, "status_th": STATUS_TH, "x10": X10,
            "summary": {"items": len(rows_out), **counts},
            "enough": len(info) >= BM.MIN_BRANCHES,
            "message": "" if len(info) >= BM.MIN_BRANCHES else
            f"ต้องมียอดซื้อและยอดขายของสาขาที่ใช้เป็นค่ากลางอย่างน้อย {BM.MIN_BRANCHES} สาขาในเดือนนี้"}


def x10_rows(con, today=None):
    """For หน้างาน: the ingredients whose purchases differ from the recipes by ten times or more in the default month
    -> (rows, month), the biggest first; ([], None) when there is none. Built once per cache window (single-flight);
    never raises."""
    try:
        m = default_month(con)
        if not m:
            return [], None
        r = check(con, m, today)
        return [x for x in r["rows"] if x["status"] == "x10"], m
    except Exception:
        return [], None
