# -*- coding: utf-8 -*-
"""bom_center.forecast — Forecast Engine v1 (plan §6, Sprint S4b).

What a branch should order on an order day, per ingredient:

  1. USAGE HISTORY — each day of the last N weeks: dishes sold (sales_sku_daily) ×
     recipe, in the ingredient's base unit, grossed up by yield (÷ (1 − loss %))
     so it is what has to be BOUGHT. Recipe = the released monthly version in
     force when that version covers the dish (Grand Menu buffet, from Sep 2026),
     else the legacy recipe (bom_ingredients, direct or via bom_sku_alias).
  2. DAILY FORECAST — weekday-fair baseline (mean of the same weekday over the
     window, the rule the MTD reports already use) × trend (last 14 days ÷ the
     14 before, clamped 0.7–1.3) × season/festival factor (the order plan's
     existing calendar + last-year logic).
  3. COVER PERIOD — from the delivery calendar the sales team fills per branch
     (owner §10-7): two regular rounds a week and a weekly fresh-salmon round.
     An order must be submitted before 13:15 two days before its delivery day
     (a Tuesday delivery: Saturday 13:15 — cutoff_for). A regular delivery must
     last until the next regular delivery; salmon a week.
  4. SAFETY STOCK — z × σ(daily usage around its weekday mean) × √cover days,
     z by ABC class on spend (A 1.65 ≈ 95 %, B 1.28 ≈ 90 %, C 1.04 ≈ 85 %);
     a fresh item never holds more than its shelf life.
  5. SUGGESTION — forecast + safety − on hand (0 until branches count stock in
     Phase 2) − on order, rounded UP to whole packs and the minimum order.

A run can be saved (bc_forecast_run/_line): Phase 2's branch order screen and
the compliance report judge against that frozen number, never a recomputation.
backtest() replays the last weeks to measure the forecast error (MAPE) by class.
"""
import json
import math
from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone

from .clock import BKK, now_bkk
from .units import Converter, to_order_units

Z = {"A": 1.65, "B": 1.28, "C": 1.04}
# The calendar is keyed by the DELIVERY weekday (SQLite %w: Sun = 0); the cutoff follows from it.
DEFAULT_CAL = {"1": {"delivery_weekday": 3},                       # Wed → cutoff Mon 13:15
               "2": {"delivery_weekday": 6},                       # Sat → cutoff Thu 13:15
               "salmon": {"delivery_weekday": 1}}                  # Mon → cutoff Sat 13:15
WD_TH = ["อาทิตย์", "จันทร์", "อังคาร", "พุธ", "พฤหัส", "ศุกร์", "เสาร์"]
CUTOFF_TIME = time(13, 15)          # owner 2026-10-09: submit before 13:15 …
CUTOFF_DAYS_BEFORE = 2              # … two days before the delivery day; a Tuesday delivery by Saturday
ROUND_TH = {"1": "รอบ 1", "2": "รอบ 2", "salmon": "แซลมอนสด"}
SALMON_DEFAULT = ("02-01-001",)                                     # fresh salmon (owner: weekly pre-order)


def _loaded(name: str):
    """An app module as the running app loaded it. Production starts `backend.main`,
    so the module is `backend.<name>`; importing `<name>` again would load a second
    copy, which core.ttl_cache refuses (duplicate cached function) — and the caller's
    try/except then silently dropped the feature (season factor, forced list)."""
    import importlib
    import sys
    for full in (name, "backend." + name):
        if full in sys.modules:
            return sys.modules[full]
    return importlib.import_module(name)


def _wd(d: date) -> int:
    """SQLite %w: Sunday = 0."""
    return (d.weekday() + 1) % 7


def _next_on_or_after(d: date, wd: int) -> date:
    return d + timedelta(days=(wd - _wd(d)) % 7)


def cutoff_time() -> time:
    """The one seam for the cutoff clock time (13:15, Bangkok)."""
    return CUTOFF_TIME


def cutoff_weekday(delivery_wd: int) -> int:
    """SQLite %w in and out: the weekday the order must be in by; a Sunday cutoff moves to Saturday."""
    d = (int(delivery_wd) - CUTOFF_DAYS_BEFORE) % 7
    return 6 if d == 0 else d


def cutoff_for(delivery: date, cutoff_time_: time = None) -> datetime:
    """The moment an order for `delivery` must be submitted by (Bangkok): two days before, 13:15."""
    d = delivery - timedelta(days=CUTOFF_DAYS_BEFORE)
    if d.weekday() == 6:                                  # Sunday → the Saturday before (Tue delivery → Sat 13:15)
        d -= timedelta(days=1)
    return datetime.combine(d, cutoff_time_ or cutoff_time(), tzinfo=BKK)


def cutoff_th(dt: datetime) -> str:
    return f"วัน{WD_TH[_wd(dt.date())]} {dt:%H:%M}"


def day_th(d: date) -> str:
    return f"วัน{WD_TH[_wd(d)]} {d.day}/{d.month}"


def _now_for(today: date = None, now: datetime = None) -> datetime:
    """A test (or a caller that knows the date only) passes `today`: the clock then reads 08:00 that morning."""
    if now:
        return now
    return datetime.combine(today, time(8, 0), tzinfo=BKK) if today else now_bkk()


def calendar(con, branch_id: int) -> dict:
    """{round: {delivery_weekday, order_weekday, lead_days, cutoff_th, filled, filled_by}} with defaults.

    The sales team fills the DELIVERY weekday; `order_weekday` is the cutoff weekday and `lead_days`
    the days between them, both derived. A legacy row (delivery_weekday NULL) reads as
    order_weekday + lead_days."""
    have = {r[0]: {"order_weekday": r[1], "delivery_weekday": r[2], "lead_days": r[3], "filled_by": r[4]}
            for r in con.execute("SELECT round, order_weekday, delivery_weekday, lead_days, filled_by"
                                 " FROM bc_order_calendar WHERE branch_id=?", (branch_id,))}
    out = {}
    for rnd, dflt in DEFAULT_CAL.items():
        c = have.get(rnd)
        if c and c["delivery_weekday"] is not None:
            dw = int(c["delivery_weekday"])
        elif c and c["order_weekday"] is not None:
            dw = (int(c["order_weekday"]) + int(c["lead_days"] if c["lead_days"] is not None else 1)) % 7
        else:
            dw = dflt["delivery_weekday"]
        cw = cutoff_weekday(dw)
        out[rnd] = {"delivery_weekday": dw, "order_weekday": cw, "lead_days": (dw - cw) % 7,
                    "cutoff_th": f"วัน{WD_TH[cw]} {cutoff_time():%H:%M}",
                    "filled": bool(c), "filled_by": c["filled_by"] if c else None}
    return out


def cover_for_delivery(con, branch_id: int, rnd: str, delivery: date) -> tuple:
    """(cutoff_date, delivery, cover_from, cover_to) — inclusive dates the order must last."""
    cal = calendar(con, branch_id)
    cut = cutoff_for(delivery).date()
    if rnd == "salmon":
        return cut, delivery, delivery, delivery + timedelta(days=6)
    other = "2" if rnd == "1" else "1"
    nxt = _next_on_or_after(delivery + timedelta(days=1), int(cal[other]["delivery_weekday"]))
    return cut, delivery, delivery, nxt - timedelta(days=1)


def cover_period(con, branch_id: int, rnd: str, day: date) -> tuple:
    """Deprecated wrapper: the window still open on `day` — the first delivery of the round whose
    cutoff date is `day` or later. (cutoff_date, delivery, cover_from, cover_to)."""
    cal = calendar(con, branch_id)
    dlv = _next_on_or_after(day, int(cal[rnd]["delivery_weekday"]))
    while cutoff_for(dlv).date() < day:
        dlv += timedelta(days=7)
    return cover_for_delivery(con, branch_id, rnd, dlv)


def next_deliveries(con, branch_id: int, today: date, horizon_days: int = 10, now: datetime = None) -> list:
    """The coming deliveries of every round (from tomorrow), each with its cutoff and whether it is
    still open, sorted by delivery day then round."""
    now = _now_for(today, now)
    cal = calendar(con, branch_id)
    out = []
    for rnd in ("1", "2", "salmon"):
        d = _next_on_or_after(today + timedelta(days=1), int(cal[rnd]["delivery_weekday"]))
        while d <= today + timedelta(days=horizon_days):
            cut = cutoff_for(d)
            cyc, _dl, cf, ct = cover_for_delivery(con, branch_id, rnd, d)
            left = (cut - now).total_seconds() / 3600.0
            out.append({"round": rnd, "round_th": ROUND_TH[rnd], "delivery": d.isoformat(), "delivery_th": day_th(d),
                        "cycle_date": cyc.isoformat(), "cutoff_at": cut.isoformat(), "cutoff_th": cutoff_th(cut),
                        "open": now <= cut, "late": now > cut, "hours_left": round(left, 1),
                        "cover_from": cf.isoformat(), "cover_to": ct.isoformat()})
            d += timedelta(days=7)
    out.sort(key=lambda x: (x["delivery"], x["round"]))
    return out


def pick_delivery(items: list) -> dict:
    """The delivery a branch should be working on: the first one still open, else the first."""
    return next((x for x in items if x["open"]), items[0] if items else None)


# ── recipes per dish, in base purchase units ─────────────────────────────────
def _per_dish(con, month: str) -> tuple:
    """({sku_id: [(ingredient_code, purchase_qty_per_plate)]}, stats)."""
    from .engine import release_in_force
    conv = Converter(con)
    loss = dict(con.execute("SELECT code, loss_pct FROM bc_ingredient"))
    aliases = dict(con.execute("SELECT alias_code, ingredient_code FROM bc_ingredient_alias"))
    sku_id = dict(con.execute("SELECT sku_code, sku_id FROM skus"))
    out, stats = {}, {"release_skus": 0, "legacy_skus": 0, "unconverted_lines": 0, "exploded_lines": 0}
    bad = {}                                              # sku_id -> [(code, unit, why)] lines that cannot be counted
    # Phase 1.5: a prepared item with a sub-recipe is ordered as the raw ingredients in it —
    # planning reads the kitchen's CURRENT sub-recipes (costs read the ones frozen into a release)
    from .prep import book as _prep_book, explode
    pbook, emem, via = _prep_book(con), {}, {}
    from .central import links as _links
    link = _links(con)                                    # S11: a recipe's older code -> the code the centre sends

    def add(sid, code, qty, unit, line_loss=None, zero=False):
        if zero or not code or not qty:
            return
        code = code if code in conv.master else aliases.get(code, code)
        qb, how = conv.to_base(code, qty, unit)
        if qb is None:
            stats["unconverted_lines"] += 1
            bad.setdefault(sid, []).append((code, unit, how))
            return
        lp = line_loss if line_loss is not None else (loss.get(code) or 0.0)
        q = qb / (1.0 - min(lp, 99.0) / 100.0)
        if code in pbook:
            raw, gaps = explode(pbook, conv, code, _memo=emem)
            if raw:
                stats["exploded_lines"] += 1
                for rc, per in raw.items():
                    rc = link.get(rc, rc)
                    rl = min(loss.get(rc) or 0.0, 99.0)
                    out.setdefault(sid, []).append((rc, q * per / (1.0 - rl / 100.0)))
                    via.setdefault(rc, set()).add(code)
                if gaps:                                  # part of the sub-recipe cannot be counted
                    bad.setdefault(sid, []).append((code, unit, "สูตรย่อยไม่สมบูรณ์: " + ", ".join(map(str, gaps[:3]))))
                return
        out.setdefault(sid, []).append((link.get(code, code), q))

    rid = release_in_force(con, month)
    covered = set()
    if rid and con.execute("SELECT kind FROM bc_release WHERE release_id=?", (rid,)).fetchone()[0] == "monthly":
        for code, ic, qty, unit, lp, z in con.execute(
                "SELECT sku_code, ingredient_code, qty, unit, loss_pct, zero_by_design FROM bc_recipe_line"
                " WHERE release_id=?", (rid,)):
            sid = sku_id.get(code)
            if sid is not None:
                covered.add(sid)
                add(sid, ic, qty, unit, lp, z)
        stats["release_skus"] = len(covered)
        stats["release_id"] = rid
    legacy = con.execute("""
        SELECT m.sale_sku, bi.ingredient_code, bi.qty_per_serving, bi.unit, COALESCE(bi.ingredient_name,'')
          FROM (SELECT sku_id AS sale_sku, sku_id AS bom_sku FROM (SELECT DISTINCT sku_id FROM bom_ingredients)
                UNION ALL
                SELECT s.sku_id, a.resolved_sku_id FROM bom_sku_alias a JOIN skus s ON s.sku_code = a.bom_sku_code
                 WHERE s.sku_id NOT IN (SELECT DISTINCT sku_id FROM bom_ingredients)) m
          JOIN bom_ingredients bi ON bi.sku_id = m.bom_sku""").fetchall()
    from .codes import remaps
    remap = remaps(con)                                   # analyst: "this old code really means X" (planning only)
    seen = set()
    for sid, ic, qty, unit, name in legacy:
        if sid in covered:
            continue
        seen.add(sid)
        add(sid, remap.get(((ic or "").strip(), name), ic), qty, unit)
    stats["remapped_codes"] = len(remap)
    stats["linked_codes"] = len(link)
    stats["legacy_skus"] = len(seen)
    stats["_bad"] = bad
    stats["via"] = {k: sorted(v) for k, v in via.items()}  # raw code -> the prepared items it came through
    return out, stats


def usage_history(con, branch_id: int, start: date, end: date, month: str) -> tuple:
    """({ingredient: {day: purchase qty}}, trading days, stats) — what the branch used each day."""
    per, stats = _per_dish(con, month)
    sales = "v_sales_sku_daily" if con.execute("SELECT 1 FROM sqlite_master WHERE name='v_sales_sku_daily'").fetchone() \
        else "sales_sku_daily"
    use = defaultdict(lambda: defaultdict(float))
    days = set()
    bad = stats.pop("_bad", {})
    missed = defaultdict(float)                           # (code, unit, why) -> plates sold whose line was skipped
    for day, sid, qty in con.execute(f"SELECT day, sku_id, SUM(qty) FROM {sales} WHERE branch_id=? AND day BETWEEN ? AND ?"
                                     " GROUP BY day, sku_id", (branch_id, start.isoformat(), end.isoformat())):
        days.add(day)
        for code, pq in per.get(sid, ()):
            use[code][day] += (qty or 0) * pq
        for key in bad.get(sid, ()):
            missed[key] += qty or 0
    # what the forecast cannot count, among dishes this branch actually sold — a unit rule fixes it
    stats["missed"] = [{"code": c, "unit": u, "why": w, "plates": round(p)} for (c, u, w), p in
                       sorted(missed.items(), key=lambda kv: -kv[1])[:40]]
    stats["missed_lines"] = len(missed)
    return use, sorted(days), stats


def era_start(start: date, as_of: date) -> date:
    """History never reaches back across the Grand Menu changeover: before it the
    dishes (and so the ingredient mix) were different, and averaging over both
    menus mis-states what is used now."""
    from .dated import CUTOVER
    cut = date.fromisoformat(CUTOVER)
    return max(start, cut) if as_of >= cut else start


def _daily_model(series: dict, days: list, as_of: date) -> dict:
    """weekday means, trend, residual σ for one ingredient over the trading days."""
    by_wd = defaultdict(list)
    for d in days:
        by_wd[_wd(date.fromisoformat(d))].append(series.get(d, 0.0))
    mean_wd = {wd: sum(v) / len(v) for wd, v in by_wd.items() if v}
    overall = (sum(series.get(d, 0.0) for d in days) / len(days)) if days else 0.0
    last = [series.get(d, 0.0) for d in days if d > (as_of - timedelta(days=14)).isoformat()]
    prev = [series.get(d, 0.0) for d in days
            if (as_of - timedelta(days=28)).isoformat() < d <= (as_of - timedelta(days=14)).isoformat()]
    trend = 1.0
    if last and prev and sum(prev) > 0:
        trend = max(0.7, min(1.3, (sum(last) / len(last)) / (sum(prev) / len(prev))))
    resid = [series.get(d, 0.0) - mean_wd.get(_wd(date.fromisoformat(d)), overall) for d in days]
    sigma = math.sqrt(sum(r * r for r in resid) / (len(resid) - 1)) if len(resid) > 1 else 0.0
    return {"mean_wd": mean_wd, "overall": overall, "trend": trend, "sigma": sigma}


def _abc(values: dict) -> dict:
    total = sum(values.values()) or 1.0
    out, run = {}, 0.0
    for code, v in sorted(values.items(), key=lambda kv: -kv[1]):
        run += v
        out[code] = "A" if run / total <= 0.80 else ("B" if run / total <= 0.95 else "C")
    return out


def suggest(con, branch_id: int, rnd: str, order_day: date, weeks: int = 6, season: str = "auto",
            as_of: date = None, stock: dict = None, uplift_pct: float = 0.0, delivery: date = None) -> dict:
    """The suggestion for one branch, one round, one delivery (nothing saved). `delivery` names
    the delivery day; without it `order_day` is read as "the window open on that day".

    `uplift_pct` (PR-19): the branch's own expectation for the cover period — +20 scales
    the forecast of every item by 1.2 before safety and stock; every row also carries
    the unscaled `forecast_base` / `need_base` so the page can show "ปกติ".

    `stock` (orders.stock_position, Phase 2): on hand at the last count, net of what
    arrived and what the sales used since, plus what is on order — deducted per item
    after projecting the use between now and the first covered day."""
    from .prices import price_for
    if delivery:
        order_day, dlv, c_from, c_to = cover_for_delivery(con, branch_id, rnd, delivery)
    else:
        order_day, dlv, c_from, c_to = cover_period(con, branch_id, rnd, order_day)
    last_data = con.execute("SELECT MAX(day) FROM sales_sku_daily WHERE branch_id=?", (branch_id,)).fetchone()[0]
    as_of = as_of or (date.fromisoformat(last_data) if last_data else order_day - timedelta(days=1))
    start = era_start(as_of - timedelta(days=7 * weeks - 1), as_of)
    month = f"{order_day.year:04d}-{order_day.month:02d}"
    use, days, stats = usage_history(con, branch_id, start, as_of, month)
    bname = (con.execute("SELECT name FROM branches WHERE branch_id=?", (branch_id,)).fetchone() or [None])[0]
    sf = {"factor": 1.0, "mode": "off", "reason": ""}
    if season == "auto":
        try:
            sf = _loaded("routers.ingredients")._ing_season_resolve(con, bname, c_from.isoformat(),
                                                                     c_to.isoformat(), "auto")
        except Exception:
            pass
    factor = float(sf.get("factor") or 1.0)
    up = 1.0 + float(uplift_pct or 0.0) / 100.0            # the branch's buffer on top of the season factor
    master = {r[0]: r[1:] for r in con.execute(
        "SELECT code, name_th, base_unit, recv_unit, qty_per_recv, moq, shelf_days, kind, monitored,"
        " sourcing_default FROM bc_ingredient")}
    has_round_col = "order_round" in {r[1] for r in con.execute("PRAGMA table_info(bc_ingredient)")}
    salmon = {r[0] for r in con.execute("SELECT code FROM bc_ingredient WHERE order_round='salmon'")} \
        if has_round_col else set()
    salmon = salmon or set(SALMON_DEFAULT)
    from .central import central_map
    cmap = central_map(con)                               # S11: the centre's list decides what is ordered from it
    cover = [c_from + timedelta(days=i) for i in range((c_to - c_from).days + 1)]
    # the days between the stock figure and the first covered day still eat into it
    basis = ((stock or {}).get("basis") or {}).get("date")
    pre_from = max(as_of + timedelta(days=1), date.fromisoformat(basis)) if basis else as_of + timedelta(days=1)
    pre_days = [pre_from + timedelta(days=i) for i in range(max((c_from - pre_from).days, 0))]
    # spend over the window for ABC
    spend = {}
    for code, s in use.items():
        p = price_for(con, code, month)
        spend[code] = sum(s.values()) * (p[0] if p else 0.0)
    klass = _abc(spend)
    rows = []
    for code, s in use.items():
        if (code in salmon) != (rnd == "salmon"):
            continue
        m = _daily_model(s, days, as_of)
        if m["overall"] <= 0:
            continue
        per_day_base = [m["mean_wd"].get(_wd(d), m["overall"]) * m["trend"] * factor for d in cover]
        fc_base = sum(per_day_base)
        fc = fc_base * up
        name, base, recv, per, moq, shelf, kind, mon, src = master.get(code, (None,) * 9)
        k = klass.get(code, "C")
        days_n = len(cover)
        if shelf:
            days_n = min(days_n, int(shelf))
        safety = Z[k] * m["sigma"] * math.sqrt(max(days_n, 1))
        need = fc + safety
        need_base = fc_base + safety
        oh = (stock or {}).get("qty", {}).get(code)
        onord = (stock or {}).get("on_order", {}).get(code, 0.0)
        pre = projected = None
        if oh is not None:                                  # counted: deduct what will still be there
            pre = sum(m["mean_wd"].get(_wd(d), m["overall"]) * m["trend"] * factor for d in pre_days)
            projected = oh - pre + onord
            need = max(0.0, need - max(projected, 0.0))
            need_base = max(0.0, need_base - max(projected, 0.0))
        elif onord:
            need = max(0.0, need - onord)
            need_base = max(0.0, need_base - onord)
        units = to_order_units(need, per, moq or 1) if per else None
        p = price_for(con, code, month)
        ppb = p[0] if p else None
        rows.append({"code": code, "name": name or code, "base_unit": base, "class": k, "kind": kind,
                     "monitored": bool(mon),
                     "sourcing": ("central" if code in cmap else "local") if cmap else (src or "central"),
                     "per_day": round(fc / len(cover), 3) if cover else 0, "forecast": round(fc, 2),
                     "forecast_base": round(fc_base, 2), "need_base": round(need_base, 2),
                     "safety": round(safety, 2), "need": round(need, 2), "trend": round(m["trend"], 3),
                     "on_hand": None if oh is None else round(oh, 2), "on_order": round(onord, 2),
                     "pre_cover_use": None if pre is None else round(pre, 2),
                     "projected": None if projected is None else round(projected, 2), "moq": moq,
                     "sigma": round(m["sigma"], 3), "recv_unit": recv, "qty_per_recv": per,
                     "suggested_units": units, "pack_known": bool(per),
                     "price_per_base": ppb, "via": stats.get("via", {}).get(code, []),
                     "value": round((units * per if units else need) * ppb, 2) if ppb else None})
    rows.sort(key=lambda r: -(r["value"] or 0))
    stats.pop("via", None)                                 # carried on each row instead
    cal = calendar(con, branch_id)[rnd]
    cut = cutoff_for(dlv)
    return {"branch_id": branch_id, "branch": bname, "round": rnd, "order_day": order_day.isoformat(),
            "cycle_date": order_day.isoformat(), "cutoff_at": cut.isoformat(), "cutoff_th": cutoff_th(cut),
            "order_weekday": WD_TH[_wd(order_day)], "delivery": dlv.isoformat(),
            "cover_from": c_from.isoformat(), "cover_to": c_to.isoformat(), "cover_days": len(cover),
            "calendar_filled": cal["filled"], "history_from": start.isoformat(), "history_to": as_of.isoformat(),
            "trading_days": len(days), "season": sf, "stats": stats, "uplift_pct": round(float(uplift_pct or 0.0), 2),
            "on_hand_included": bool((stock or {}).get("basis")),
            "rows": rows, "value_total": round(sum(r["value"] or 0 for r in rows), 2),
            "salmon_codes": sorted(salmon)}


def covers(con, branch_id: int, rnd: str, order_day: date, weeks: int = 6, delivery: date = None) -> dict:
    """PR-19: the customers the cover period normally brings, day by day — the weekday
    mean of the last `weeks` weeks (the forecast's own baseline rule) and the same
    dates a year earlier — so a branch can turn "we expect N customers" into a buffer %."""
    if delivery:
        order_day, dlv, c_from, c_to = cover_for_delivery(con, branch_id, rnd, delivery)
    else:
        order_day, dlv, c_from, c_to = cover_period(con, branch_id, rnd, order_day)
    cover = [c_from + timedelta(days=i) for i in range((c_to - c_from).days + 1)]
    out = {"branch_id": branch_id, "round": rnd, "order_day": order_day.isoformat(), "delivery": dlv.isoformat(),
           "cover_from": c_from.isoformat(), "cover_to": c_to.isoformat(), "available": False, "days": [],
           "baseline_total": 0, "last_year_total": None}
    if not con.execute("SELECT 1 FROM sqlite_master WHERE name='sales_transaction'").fetchone():
        return out
    last = con.execute("SELECT MAX(date(sold_at)) FROM sales_transaction WHERE branch_id=?", (branch_id,)).fetchone()[0]
    if not last:
        return out
    as_of = min(date.fromisoformat(last), order_day - timedelta(days=1))
    start = as_of - timedelta(days=7 * weeks - 1)
    by_wd, n_wd = defaultdict(float), defaultdict(int)
    for d, pax in con.execute("SELECT date(sold_at), SUM(COALESCE(customer_count, 0)) FROM sales_transaction"
                              " WHERE branch_id=? AND date(sold_at) BETWEEN ? AND ? GROUP BY date(sold_at)",
                              (branch_id, start.isoformat(), as_of.isoformat())):
        wd = _wd(date.fromisoformat(d))
        by_wd[wd] += float(pax or 0)
        n_wd[wd] += 1
    if not n_wd:
        return out
    overall = sum(by_wd.values()) / max(sum(n_wd.values()), 1)
    ly = {d: float(p or 0) for d, p in con.execute(
        "SELECT date(sold_at), SUM(COALESCE(customer_count, 0)) FROM sales_transaction WHERE branch_id=?"
        " AND date(sold_at) BETWEEN date(?, '-1 year') AND date(?, '-1 year') GROUP BY date(sold_at)",
        (branch_id, c_from.isoformat(), c_to.isoformat()))}
    days = []
    for d in cover:
        wd = _wd(d)
        base = by_wd[wd] / n_wd[wd] if n_wd.get(wd) else overall
        lyd = con.execute("SELECT date(?, '-1 year')", (d.isoformat(),)).fetchone()[0]
        days.append({"date": d.isoformat(), "weekday": WD_TH[wd], "baseline_pax": round(base),
                     "last_year_pax": round(ly[lyd]) if lyd in ly else None})
    out.update({"available": True, "days": days, "history_from": start.isoformat(), "history_to": as_of.isoformat(),
                "baseline_total": sum(x["baseline_pax"] for x in days),
                "last_year_total": (sum(x["last_year_pax"] for x in days if x["last_year_pax"] is not None)
                                    if any(x["last_year_pax"] is not None for x in days) else None)})
    return out


def save_run(con, out: dict, who: str, params: dict) -> int:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    con.execute("INSERT INTO bc_forecast_run(branch_id, cycle_date, round, cover_from, cover_to, method, params,"
                " release_id, created_at, created_by) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (out["branch_id"], out["order_day"], out["round"], out["cover_from"], out["cover_to"],
                 "weekday-fair×trend×season + z·σ·√d (v1)", json.dumps(params, ensure_ascii=False),
                 out["stats"].get("release_id"), now, who))
    run_id = con.execute("SELECT last_insert_rowid()").fetchone()[0]
    con.executemany("INSERT INTO bc_forecast_line(run_id, ingredient_code, forecast_base, safety_base, on_hand_base,"
                    " on_order_base, suggested_units, order_unit, qty_per_order, price_per_base)"
                    " VALUES(?,?,?,?,?,?,?,?,?,?)",
                    [(run_id, r["code"], r["forecast"], r["safety"], 0, 0, r["suggested_units"], r["recv_unit"],
                      r["qty_per_recv"], r["price_per_base"]) for r in out["rows"]])
    return run_id


def backtest(con, branch_id: int, weeks: int = 4, history_weeks: int = 6, as_of: date = None) -> dict:
    """Replay each of the last `weeks` weeks: forecast every day from the data
    before that week only, compare with what was actually used. MAPE weighted by
    actual usage, overall and by ABC class."""
    last_data = con.execute("SELECT MAX(day) FROM sales_sku_daily WHERE branch_id=?", (branch_id,)).fetchone()[0]
    if not last_data:
        return {"available": False}
    as_of = as_of or date.fromisoformat(last_data)
    first = as_of - timedelta(days=7 * (weeks + history_weeks) - 1)
    month = f"{as_of.year:04d}-{as_of.month:02d}"
    use, days, _st = usage_history(con, branch_id, first, as_of, month)
    from .prices import price_for
    spend = {c: sum(s.values()) * ((price_for(con, c, month) or (0,))[0]) for c, s in use.items()}
    klass = _abc(spend)
    err = defaultdict(float)
    act = defaultdict(float)
    for w in range(weeks, 0, -1):
        wk_start = as_of - timedelta(days=7 * w - 1)
        wk_end = wk_start + timedelta(days=6)
        h0 = era_start(wk_start - timedelta(days=7 * history_weeks), wk_start)
        hist_days = [d for d in days if h0.isoformat() <= d < wk_start.isoformat()]
        test_days = [d for d in days if wk_start.isoformat() <= d <= wk_end.isoformat()]
        if not hist_days or not test_days:
            continue
        cutoff = wk_start - timedelta(days=1)
        for code, s in use.items():
            m = _daily_model(s, hist_days, cutoff)
            fc = sum(m["mean_wd"].get(_wd(date.fromisoformat(d)), m["overall"]) * m["trend"] for d in test_days)
            a = sum(s.get(d, 0.0) for d in test_days)
            k = klass.get(code, "C")
            # weekly totals: what a twice-weekly order actually has to get right
            err[k] += abs(fc - a)
            act[k] += a
    by_class = {k: round(100.0 * err[k] / act[k], 1) if act[k] else None for k in ("A", "B", "C")}
    tot_e, tot_a = sum(err.values()), sum(act.values())
    return {"available": True, "branch_id": branch_id, "weeks": weeks, "as_of": as_of.isoformat(),
            "mape_by_class": by_class, "mape": round(100.0 * tot_e / tot_a, 1) if tot_a else None,
            "target_a": 15.0}
