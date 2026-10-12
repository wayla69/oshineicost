# -*- coding: utf-8 -*-
"""PR-V — ingredient usage per ฿100,000 of sales: a SECOND way to read the same history, beside the dish-based forecast.

  * Sales  = Σ `subtotal_ex_vat` of the sales table the forecast reads (v_sales_sku_daily, else sales_sku_daily) for the
             branch and the days; VAT is never counted.
  * Usage  = the purchase quantities of `forecast.usage_history` (base unit, grossed up by yield) — there is no second
             definition of usage.
  * per_100k = usage ÷ sales × 100,000.
  * Weeks  = whole blocks of 7 days counted back from the last day of the window (so each holds every weekday once, and
             the branch sold on at least MIN_WEEK_DAYS of them);
             `swing_pct` = population stdev ÷ mean of the weekly ratios × 100; `steady` = swing ≤ STEADY_PCT. Fewer than
             two usable weeks → `steady` is None ("ข้อมูลยังไม่พอ").
  * "ปกติ" = what the forecast already assumes for the cover days: the weekday-fair mean of the sales × the same trend
             (last 14 days ÷ the 14 before, clamped 0.7–1.3) × the same season factor (forecast._daily_model).

A new branch (fewer than NEW_BRANCH_DAYS trading days of sales) with a reference branch (app_settings `bc_ref_branch`, JSON
{branch_id: reference branch id}) is ordered from the reference branch's per_100k × the sales the branch expects.
"""
import json
import statistics
import time as _time
from collections import defaultdict
from datetime import date, timedelta

from . import forecast as FC
from .clock import today as bkk_today

MIN_WEEK_DAYS = 5              # a block of 7 days is a "whole week" only when the branch sold on at least this many of them
STEADY_PCT = 15.0              # a weekly ratio that swings more than this (±%) is "ใช้ไม่สม่ำเสมอ"
NEW_BRANCH_DAYS = 14           # fewer trading days of sales than this → the reference branch is used (when one is set)
REF_KEY = "bc_ref_branch"
BUFFER_MIN, BUFFER_MAX = -50.0, 100.0
NEED_SALES_TH = "ยังไม่มีประวัติการขายพอ กรอกยอดขายที่คาดของรอบนี้"


def _iso(d) -> str:
    return d.isoformat() if isinstance(d, date) else str(d)[:10]


def _d(x) -> date:
    return x if isinstance(x, date) else date.fromisoformat(str(x)[:10])


def sales_table(con) -> str:
    return "v_sales_sku_daily" if con.execute("SELECT 1 FROM sqlite_master WHERE name='v_sales_sku_daily'").fetchone() \
        else "sales_sku_daily"


def sales_by_day(con, branch_id: int, d0, d1) -> dict:
    """{iso day: Σ subtotal_ex_vat} of the branch's sales between d0 and d1 (inclusive) — VAT is never in it."""
    return {day: float(v or 0.0) for day, v in con.execute(
        f"SELECT day, SUM(COALESCE(subtotal_ex_vat, 0)) FROM {sales_table(con)} WHERE branch_id=? AND day BETWEEN ? AND ?"
        " GROUP BY day", (branch_id, _iso(d0), _iso(d1)))}


def week_blocks(d0, d1) -> list:
    """Whole 7-day blocks inside [d0, d1], counted back from d1; oldest first, each a list of ISO days."""
    a, b = _d(d0), _d(d1)
    out = []
    end = b
    while end - timedelta(days=6) >= a:
        out.append([(end - timedelta(days=i)).isoformat() for i in range(6, -1, -1)])
        end -= timedelta(days=7)
    return out[::-1]


# ── the ratio ────────────────────────────────────────────────────────────────
def ratio_from(use: dict, sales_day: dict, d0, d1) -> dict:
    """The ratio from a usage history already in hand ({code: {day: qty}}) and the sales per day.

    -> {sales, days, weeks, items: {code: {usage, per_100k, weekly, swing_pct, steady}}}; `weekly` holds one ratio per
    whole week (None for a week without sales), oldest first. An item with no usage in the window is left out."""
    a, b = _iso(d0), _iso(d1)
    in_win = {d: v for d, v in sales_day.items() if a <= d <= b}
    sales = sum(in_win.values())
    blocks = week_blocks(d0, d1)
    where = {}
    for i, blk in enumerate(blocks):
        for d in blk:
            where[d] = i
    wk_sales = [sum(in_win.get(d, 0.0) for d in blk) if sum(1 for d in blk if in_win.get(d, 0.0) > 0) >= MIN_WEEK_DAYS else 0.0
                for blk in blocks]                                     # a week the branch barely traded is no week (a new branch)
    out = {"sales": round(sales, 2), "days": sum(1 for v in in_win.values() if v > 0), "weeks": sum(1 for w in wk_sales if w > 0), "items": {}}
    if sales <= 0:
        return out
    for code, series in use.items():
        q = 0.0
        wq = [0.0] * len(blocks)
        for d, v in series.items():
            if a <= d <= b:
                q += v
                i = where.get(d)
                if i is not None:
                    wq[i] += v
        if q <= 0:
            continue
        weekly = [(wq[i] / wk_sales[i] * 1e5) if wk_sales[i] > 0 else None for i in range(len(blocks))]
        usable = [w for w in weekly if w is not None]
        swing = None
        if len(usable) >= 2:
            mean = statistics.fmean(usable)
            swing = (statistics.pstdev(usable) / mean * 100.0) if mean > 0 else None
        out["items"][code] = {"usage": q, "per_100k": q / sales * 1e5, "weekly": weekly,
                              "swing_pct": None if swing is None else round(swing, 1),
                              "steady": None if swing is None else swing <= STEADY_PCT}
    return out


def ratio(con, branch_id: int, d0, d1, month: str) -> dict:
    """{sales, days, items: {code: {per_100k, unit, weekly, swing_pct, steady, ...}}} for the branch over [d0, d1].
    `unit` is the ingredient's base unit; the usage is forecast.usage_history's (cached)."""
    use, _days, _stats = FC.usage_history(con, branch_id, _d(d0), _d(d1), month)
    out = ratio_from(use, sales_by_day(con, branch_id, d0, d1), d0, d1)
    units = dict(con.execute("SELECT code, base_unit FROM bc_ingredient"))
    for code, it in out["items"].items():
        it["unit"] = units.get(code)
    return out


# ── "ปกติ": the sales the forecast assumes for the cover days ───────────────
def normal_from(series: dict, days: list, as_of, cover: list, factor: float = 1.0) -> dict:
    """The weekday-fair mean of `series` (day → ฿) × trend × season over the cover days — forecast._daily_model's rule."""
    m = FC._daily_model(series, days, _d(as_of))
    per_day = [m["mean_wd"].get(FC._wd(d), m["overall"]) * m["trend"] * factor for d in cover]
    return {"total": sum(per_day), "trend": m["trend"], "factor": factor, "per_day": per_day}


def normal_sales(con, branch_id: int, cover_from, cover_to, weeks: int = 6, as_of=None, factor=None) -> dict:
    """The sales ("ปกติ") expected over the cover days. `factor` None = the season factor the forecast would use.
    -> {total, trend, factor, days: [{date, normal}], history_from, history_to, trading_days}; total is None without history."""
    cf, ct = _d(cover_from), _d(cover_to)
    cover = [cf + timedelta(days=i) for i in range((ct - cf).days + 1)]
    last = con.execute(f"SELECT MAX(day) FROM {sales_table(con)} WHERE branch_id=?", (branch_id,)).fetchone()[0]
    out = {"total": None, "trend": 1.0, "factor": 1.0, "days": [], "trading_days": 0}
    if not last:
        return out
    as_of = _d(as_of) if as_of else min(_d(last), cf - timedelta(days=1))
    start = FC.era_start(as_of - timedelta(days=7 * weeks - 1), as_of)
    series = sales_by_day(con, branch_id, start, as_of)
    days = sorted(series)
    if factor is None:
        factor = float(FC.season_factor(con, branch_id, cf, ct).get("factor") or 1.0)
    if not days:
        return out
    n = normal_from(series, days, as_of, cover, factor)
    out.update(total=round(n["total"], 2), trend=round(n["trend"], 3), factor=factor, trading_days=len(days),
               history_from=start.isoformat(), history_to=as_of.isoformat(),
               days=[{"date": d.isoformat(), "normal": round(v, 2)} for d, v in zip(cover, n["per_day"])])
    return out


def pct_from_sales(expected, normal) -> dict:
    """The buffer % that turns the "ปกติ" sales into the sales the branch expects: (expected ÷ normal − 1) × 100, held to the
    buffer's own range. -> {pct, clamped, message}; pct is None when there is no normal to compare with."""
    try:
        e, n = float(expected), float(normal)
    except (TypeError, ValueError):
        return {"pct": None, "clamped": False, "message": "ยอดขายที่คาดต้องเป็นตัวเลข"}
    if e < 0:
        return {"pct": None, "clamped": False, "message": "ยอดขายที่คาดต้องไม่ติดลบ"}
    if n <= 0:
        return {"pct": None, "clamped": False, "message": "ยังไม่มียอดขายปกติของช่วงนี้ให้เทียบ"}
    raw = (e / n - 1.0) * 100.0
    pct = max(BUFFER_MIN, min(BUFFER_MAX, raw))
    clamped = abs(pct - raw) > 1e-9
    return {"pct": round(pct, 1), "clamped": clamped,
            "message": (f"ยอดขายที่คาดต่างจากปกติ {raw:+.0f}% เกินช่วงที่ปรับได้ ({BUFFER_MIN:+.0f}% ถึง {BUFFER_MAX:+.0f}%) "
                        f"ระบบใช้ {pct:+.0f}% แทน") if clamped else ""}


def sales_clean(raw):
    """The sales the branch typed (฿): None for blank / 0, else a positive number."""
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return None
    try:
        v = float(str(raw).replace(",", "").strip())
    except (TypeError, ValueError):
        raise ValueError("ยอดขายที่คาดต้องเป็นตัวเลข")
    if v < 0:
        raise ValueError("ยอดขายที่คาดต้องไม่ติดลบ")
    return v or None


# ── the reference branch of a new branch ────────────────────────────────────
def reference_map(con) -> dict:
    """{branch_id: reference branch_id} from app_settings (a bad value reads as none)."""
    try:
        r = con.execute("SELECT value FROM app_settings WHERE key=?", (REF_KEY,)).fetchone()
    except Exception:
        return {}
    if not r or not r[0]:
        return {}
    try:
        raw = json.loads(r[0])
    except ValueError:
        return {}
    out = {}
    for k, v in (raw.items() if isinstance(raw, dict) else []):
        try:
            out[int(k)] = int(v)
        except (TypeError, ValueError):
            continue
    return out


def reference_for(con, branch_id: int):
    rid = reference_map(con).get(int(branch_id))
    return rid if rid and rid != int(branch_id) else None


def set_reference(con, who: str, branch_id: int, ref_id) -> dict:
    """Set (or clear, ref_id None) the reference branch of a branch: app_settings `bc_ref_branch`, audited."""
    from .master import audit
    if not con.execute("SELECT 1 FROM branches WHERE branch_id=? AND name<>'ALL'", (branch_id,)).fetchone():
        raise ValueError("ไม่พบสาขา")
    cur = reference_map(con)
    old = cur.get(int(branch_id))
    if ref_id in (None, "", 0):
        cur.pop(int(branch_id), None)
        ref_id = None
    else:
        ref_id = int(ref_id)
        if ref_id == int(branch_id):
            raise ValueError("สาขาอ้างอิงต้องเป็นสาขาอื่น")
        if not con.execute("SELECT 1 FROM branches WHERE branch_id=? AND name<>'ALL' AND COALESCE(active,1)=1",
                           (ref_id,)).fetchone():
            raise ValueError("ไม่พบสาขาอ้างอิง")
        cur[int(branch_id)] = ref_id
    con.execute("CREATE TABLE IF NOT EXISTS app_settings(key TEXT PRIMARY KEY, value TEXT NOT NULL DEFAULT '')")
    con.execute("INSERT OR REPLACE INTO app_settings(key, value) VALUES(?, ?)",
                (REF_KEY, json.dumps({str(k): v for k, v in sorted(cur.items())}, ensure_ascii=False)))
    audit(con, who, "ref_branch_set", "app_settings", f"{REF_KEY}|{branch_id}", old, ref_id)
    return {"branch_id": int(branch_id), "ref_branch_id": ref_id}


def reference_basis(con, ref_id: int, month: str, weeks: int = 6, cover: list = None, factor: float = 1.0) -> dict:
    """What a new branch borrows from its reference branch: the ratio over the reference's last `weeks` weeks (its own
    history window, same rule as suggest) and, for the cover days, the reference's "ปกติ" sales as a hint."""
    last = con.execute(f"SELECT MAX(day) FROM {sales_table(con)} WHERE branch_id=?", (ref_id,)).fetchone()[0]
    name = (con.execute("SELECT name FROM branches WHERE branch_id=?", (ref_id,)).fetchone() or [None])[0]
    out = {"branch_id": ref_id, "name": name, "items": {}, "use": {}, "stats": {}, "sales": 0.0, "ok": False,
           "sales_normal": None}
    if not last:
        return out
    as_of = _d(last)
    start = FC.era_start(as_of - timedelta(days=7 * weeks - 1), as_of)
    use, days, stats = FC.usage_history(con, ref_id, start, as_of, month)
    sales_day = sales_by_day(con, ref_id, start, as_of)
    r = ratio_from(use, sales_day, start, as_of)
    out.update(items=r["items"], use=use, stats=stats, sales=r["sales"], ok=bool(r["items"]), history_from=start.isoformat(),
               history_to=as_of.isoformat(), trading_days=len(days))
    if cover and days:
        out["sales_normal"] = round(normal_from(sales_day, days, as_of, cover, factor)["total"], 2)
    return out


# ── the report: the last N weeks against the N weeks before ─────────────────
_report_cache: dict = {}


def clear_cache() -> None:
    _report_cache.clear()


def _closed_end(con, today=None) -> date:
    """The last finished day of sales: the newest day in the table, never the day still trading."""
    last = con.execute(f"SELECT MAX(day) FROM {sales_table(con)}").fetchone()[0]
    t = (today or bkk_today()) - timedelta(days=1)
    return min(_d(last), t) if last else t


def _pct(new, old):
    return None if not old else round((new - old) / old * 100.0, 1)


def report(con, weeks: int = 4, branch_ids=None, today=None) -> dict:
    """Per branch the last `weeks` weeks against the `weeks` before (usage per ฿100,000 of sales), items by value, plus the
    chain total (Σ usage ÷ Σ sales). `branch_ids` None = every active branch with sales in the window."""
    weeks = max(1, min(int(weeks), 8))
    end = _closed_end(con, today)
    n = 7 * weeks
    from .dated import CUTOVER
    cut = date.fromisoformat(CUTOVER)
    # history never reaches back across the Grand Menu changeover (era_start): the dishes, so the mix, were different before it
    cur0, cur1 = FC.era_start(end - timedelta(days=n - 1), end), end
    prv1 = end - timedelta(days=n)
    prv0 = FC.era_start(prv1 - timedelta(days=n - 1), prv1) if prv1 >= cut else prv1 + timedelta(days=1)   # empty before the changeover
    month = f"{end.year:04d}-{end.month:02d}"
    key = None
    try:
        path = next((r[2] for r in con.execute("PRAGMA database_list") if r[1] == "main"), "")
        core = FC._loaded("core")
        key = (path, tuple(branch_ids) if branch_ids is not None else None, end.isoformat(), weeks,
               getattr(core, "_data_gen", 0), getattr(core, "_db_gen", 0)) if path and FC.USAGE_CACHE_TTL else None
    except Exception:
        key = None
    if key is not None:
        hit = _report_cache.get(key)
        if hit and _time.monotonic() - hit[0] < FC.USAGE_CACHE_TTL:
            return hit[1]
    from .prices import price_for
    from .stock import count_units
    names = dict(con.execute("SELECT code, name_th FROM bc_ingredient"))
    bnames = {b: nm for b, nm in con.execute("SELECT branch_id, name FROM branches WHERE name<>'ALL'")}
    if branch_ids is None:
        have = {b for (b,) in con.execute(f"SELECT DISTINCT branch_id FROM {sales_table(con)} WHERE day BETWEEN ? AND ?",
                                          (cur0.isoformat(), cur1.isoformat()))}
        ids = [b for b, nm in sorted(bnames.items(), key=lambda kv: kv[1]) if b in have
               and con.execute("SELECT COALESCE(active,1) FROM branches WHERE branch_id=?", (b,)).fetchone()[0]]
    else:
        ids = [b for b in branch_ids if b in bnames]
    prices = {}

    def price(code):
        if code not in prices:
            p = price_for(con, code, month)
            prices[code] = p[0] if p else None
        return prices[code]

    branches, tot = [], {"cur": {"sales": 0.0, "use": defaultdict(float)}, "prv": {"sales": 0.0, "use": defaultdict(float)}}
    for bid in ids:
        with FC._plan_slots:
            use, _days, _st = FC.usage_history(con, bid, min(prv0, cur0), cur1, month)
        sales_day = sales_by_day(con, bid, min(prv0, cur0), cur1)
        cur = ratio_from(use, sales_day, cur0, cur1)
        prv = ratio_from(use, sales_day, prv0, prv1) if prv0 <= prv1 else {"sales": 0.0, "items": {}, "weeks": 0, "days": 0}
        for w, r in (("cur", cur), ("prv", prv)):
            tot[w]["sales"] += r["sales"]
            for c, it in r["items"].items():
                tot[w]["use"][c] += it["usage"]
        branches.append({"branch_id": bid, "branch": bnames[bid], "sales": cur["sales"], "sales_prev": prv["sales"],
                         "days": cur["days"], "weeks_prev": prv["weeks"], "_cur": cur["items"], "_prv": prv["items"]})
    codes = set()
    for b in branches:
        codes |= set(b["_cur"]) | set(b["_prv"])
    cu = count_units(con, codes)

    def line(code, per, prev, swing, steady):
        unit, f = cu.get(code, (None, 1.0))
        f = f or 1.0
        p = price(code)
        return {"code": code, "name": names.get(code) or code, "unit": unit, "per_100k": round(per / f, 4) if per is not None else None,
                "prev_per_100k": None if prev is None else round(prev / f, 4), "change_pct": None if per is None or prev is None else _pct(per, prev),
                "swing_pct": swing, "steady": steady,
                "value_per_100k": round(per * p, 2) if per is not None and p else None}

    for b in branches:
        items = []
        for code in set(b["_cur"]) | set(b["_prv"]):
            c, p = b["_cur"].get(code), b["_prv"].get(code)
            items.append(line(code, c["per_100k"] if c else 0.0, p["per_100k"] if p else None,
                              c["swing_pct"] if c else None, c["steady"] if c else None))
        items.sort(key=lambda x: (-(x["value_per_100k"] or 0), x["code"]))
        b["items"] = items
        b["value_per_100k"] = round(sum(x["value_per_100k"] or 0 for x in items), 2)
        del b["_cur"], b["_prv"]
    chain = None
    if len(ids) > 0 and tot["cur"]["sales"] > 0:
        items = []
        for code in set(tot["cur"]["use"]) | set(tot["prv"]["use"]):
            cur_per = tot["cur"]["use"].get(code, 0.0) / tot["cur"]["sales"] * 1e5
            prv_per = tot["prv"]["use"][code] / tot["prv"]["sales"] * 1e5 if tot["prv"]["sales"] > 0 and code in tot["prv"]["use"] else None
            items.append(line(code, cur_per, prv_per, None, None))
        items.sort(key=lambda x: (-(x["value_per_100k"] or 0), x["code"]))
        chain = {"sales": round(tot["cur"]["sales"], 2), "sales_prev": round(tot["prv"]["sales"], 2), "branches": len(ids),
                 "items": items, "value_per_100k": round(sum(x["value_per_100k"] or 0 for x in items), 2)}
    out = {"weeks": weeks, "end": end.isoformat(), "current": {"from": cur0.isoformat(), "to": cur1.isoformat()},
           "previous": {"from": prv0.isoformat(), "to": prv1.isoformat()} if prv0 <= prv1 else None,
           "steady_pct": STEADY_PCT, "month": month, "branches": branches, "chain": chain}
    if key is not None:
        if len(_report_cache) >= 16:
            _report_cache.pop(min(_report_cache, key=lambda k: _report_cache[k][0]))
        _report_cache[key] = (_time.monotonic(), out)
    return out
