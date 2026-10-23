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
  5. SUGGESTION — forecast + safety − on hand (the system stock after the order day's
     issue slip, orders.stock_position; unknown = not deducted) − on order, rounded
     UP to whole packs and the minimum order.

A run can be saved (bc_forecast_run/_line): Phase 2's branch order screen and
the compliance report judge against that frozen number, never a recomputation.
backtest() replays the last weeks to measure the forecast error (MAPE) by class.
"""
import functools
import json
import math
import os
import threading
import time as _time
from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone

from .clock import BKK, now_bkk
from .prices import PriceBook
from .units import Converter, is_big_pack, to_order_units_ex

HISTORY_WEEKS = 8                    # owner 2026-10-22: every suggestion / normal / estimate reads this many weeks of history (was 4-6)
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


@functools.lru_cache(maxsize=16384)
def _wd_iso(iso: str) -> int:
    """_wd of an ISO date string (cached: the same ~60 history days are asked once per ingredient)."""
    return _wd(date.fromisoformat(iso))


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
    """The cutoff as every screen writes it after "ส่งใบสั่งซื้อภายใน": พฤ. 15/10 เวลา 13:15 (fmt.cutoff_label)."""
    from .fmt import cutoff_label
    return cutoff_label(dt)


def day_th(d: date) -> str:
    return f"วัน{WD_TH[_wd(d)]} {d.day}/{d.month}"


_WD_WORDS = {                                                # SQLite %w: Sunday = 0 … Saturday = 6
    0: ("อาทิตย์", "อา", "sun", "sunday"),
    1: ("จันทร์", "จ", "mon", "monday"),
    2: ("อังคาร", "อ", "tue", "tues", "tuesday"),
    3: ("พุธ", "พ", "wed", "wednesday"),
    4: ("พฤหัสบดี", "พฤหัส", "พฤ", "thu", "thur", "thurs", "thursday"),
    5: ("ศุกร์", "ศ", "fri", "friday"),
    6: ("เสาร์", "ส", "sat", "saturday"),
}
_WD_LOOKUP = {w: k for k, ws in _WD_WORDS.items() for w in ws}


def parse_weekday(v):
    """A delivery-day cell -> 0..6 (SQLite %w, Sunday = 0); None for a blank cell.

    Accepts the Thai names (with or without the leading «วัน», the short «พฤหัส» and the long
    «พฤหัสบดี»), the usual abbreviations, English names, and a number 0-6. Anything else raises ValueError
    so a typo is reported instead of guessed."""
    if v is None:
        return None
    if isinstance(v, bool):
        raise ValueError("วันส่งต้องเป็นชื่อวัน (อาทิตย์–เสาร์) หรือเลข 0–6")
    if isinstance(v, (int, float)):
        if float(v) == int(v) and 0 <= int(v) <= 6:
            return int(v)
        raise ValueError(f"วันส่ง “{v}” ไม่ถูกต้อง ต้องเป็นชื่อวัน (อาทิตย์–เสาร์) หรือเลข 0–6")
    t = str(v).strip()
    if not t:
        return None
    key = t.lower().replace(" ", "").replace(".", "")
    if key.startswith("วัน"):
        key = key[len("วัน"):]
    if key in _WD_LOOKUP:
        return _WD_LOOKUP[key]
    if key.isdigit() and 0 <= int(key) <= 6:
        return int(key)
    raise ValueError(f"วันส่ง “{t}” ไม่ถูกต้อง ต้องเป็นชื่อวัน (อาทิตย์–เสาร์) หรือเลข 0–6")


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
    from .aliases import active_map                       # PR-Y: a retired alias no longer resolves
    aliases = active_map(con)
    sku_id = dict(con.execute("SELECT sku_code, sku_id FROM skus"))
    out, stats = {}, {"release_skus": 0, "legacy_skus": 0, "unconverted_lines": 0, "exploded_lines": 0}
    bad = {}                                              # sku_id -> [(code, unit, why)] lines that cannot be counted
    # Phase 1.5: a prepared item with a sub-recipe is ordered as the raw ingredients in it —
    # planning reads the kitchen's CURRENT sub-recipes (costs read the ones frozen into a release)
    from .prep import book as _prep_book, explode_parts
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
            lossy, flat, gaps = explode_parts(pbook, conv, code, _memo=emem)
            if lossy or flat:
                stats["exploded_lines"] += 1
                for rc, per in lossy.items():
                    rc = link.get(rc, rc)
                    rl = min(loss.get(rc) or 0.0, 99.0)
                    out.setdefault(sid, []).append((rc, q * per / (1.0 - rl / 100.0)))
                    via.setdefault(rc, set()).add(code)
                for rc, per in flat.items():              # a portion line (PR-U): the yield already counts usable pieces only
                    rc = link.get(rc, rc)
                    out.setdefault(sid, []).append((rc, q * per))
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


USAGE_CACHE_TTL = float(os.environ.get("OSHINEI_USAGE_CACHE_TTL", "600"))     # seconds; 0 = off (tests)
_USAGE_CACHE_MAX = 64
PLAN_CONCURRENCY = max(1, int(os.environ.get("OSHINEI_PLAN_CONCURRENCY", "1")))
_plan_slots = threading.BoundedSemaphore(PLAN_CONCURRENCY)
_usage_cache: dict = {}                                    # key -> (stored at, (use, days, stats))
_usage_lock = threading.Lock()


_flight_locks = [threading.Lock() for _ in range(16)]


def _flight(key):
    """A lock to hold while a cache entry is computed, so concurrent misses of the SAME key compute it once (no key = no lock).
    Striped: two different keys that share a lock only wait for each other, never deadlock (nothing else is taken inside)."""
    import contextlib
    return contextlib.nullcontext() if key is None else _flight_locks[hash(key) % len(_flight_locks)]


def clear_usage_cache() -> None:
    with _usage_lock:
        _usage_cache.clear()
    with _slip_lock:
        _slip_cache.clear()
    _bump_gens(None)                                       # LK: the stored plans and stock estimates were built on the same inputs
    try:                                                   # PR-V: the per-฿100,000 report is built on this usage (PR-X: so is the recipe check)
        from . import backlog, per_sales, recipe_check
        per_sales.clear_cache()
        recipe_check.clear_cache()
        backlog.clear_cache()                              # batch 4b (3.5): the ranked data-quality backlog is built on the same usage
    except Exception:
        pass


def _usage_key(con, branch_id, start, end, month):
    """None when the connection has no file behind it (an in-memory database is never cached)."""
    try:
        path = next((r[2] for r in con.execute("PRAGMA database_list") if r[1] == "main"), "")
    except Exception:
        return None
    if not path:
        return None
    core = _loaded("core")
    # _data_gen moves only on a FULL invalidate_cache() (sales / cost / bom data changed); the prefixed flushes of
    # every order / slip / claim save leave the cache alone. Recipe-side writes call clear_usage_cache() explicitly.
    return (path, branch_id, start.isoformat(), end.isoformat(), month,
            getattr(core, "_data_gen", 0), getattr(core, "_db_gen", 0))


def usage_history(con, branch_id: int, start: date, end: date, month: str) -> tuple:
    """({ingredient: {day: purchase qty}}, trading days, stats) — what the branch used each day.

    Cached per process for USAGE_CACHE_TTL seconds (the plan of a branch asks for the same six weeks again
    and again, and the CPU-heavy walk over the sales used to run twice per plan). The key carries the
    database file and core's DATA / swap generations: a full invalidate_cache() (no prefix) or a data swap
    starts afresh, a prefixed one (what every order, slip or claim save calls) does not — usage depends on
    sales, recipes, releases, sub-recipes, code links and unit rules, never on orders or slips. The router
    calls clear_usage_cache() after every write to those recipe-side inputs.
    `days` and `stats` are copied on return because callers pop from them; `use` is read-only for callers.

    PLAN_CONCURRENCY (env OSHINEI_PLAN_CONCURRENCY, default 1 since LK: with the plan cache a miss is short, and one at a time leaves the writers the GIL) caps how many `suggest()` computations run
    at once (`_plan_slots`, taken in orders.plan): the plan is CPU-bound Python, and thirty of them at the
    cutoff hour starve the thread that holds the SQLite write lock of the GIL until busy_timeout expires."""
    ttl = USAGE_CACHE_TTL
    key = _usage_key(con, branch_id, start, end, month) if ttl and ttl > 0 else None
    if key is not None:
        with _usage_lock:
            hit = _usage_cache.get(key)
        if hit and _time.monotonic() - hit[0] < ttl:
            use, days, stats = hit[1]
            return use, list(days), dict(stats)
    with _flight(key):                 # LK: thirty requests that miss together compute it once; the rest wait here and read the cache
        if key is not None:
            with _usage_lock:
                hit = _usage_cache.get(key)
            if hit and _time.monotonic() - hit[0] < ttl:
                use, days, stats = hit[1]
                return use, list(days), dict(stats)
        use, days, stats = _usage_history_uncached(con, branch_id, start, end, month)
        if key is not None:
            with _usage_lock:
                if len(_usage_cache) >= _USAGE_CACHE_MAX:
                    for k in sorted(_usage_cache, key=lambda k: _usage_cache[k][0])[:max(1, _USAGE_CACHE_MAX // 4)]:
                        _usage_cache.pop(k, None)
                _usage_cache[key] = (_time.monotonic(), (use, list(days), dict(stats)))
        return use, days, stats


def _usage_history_uncached(con, branch_id: int, start: date, end: date, month: str) -> tuple:
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


# ── PR-S3 · demand from the confirmed issue slips (stock-first mode) ──────────────────────────────────────────────
SLIP_MIN_DAYS = 14                     # fewer confirmed slip days than this in the window: no recipe fallback in stock mode (O1)
_slip_cache: dict = {}                 # key -> (stored at, (use, days, stats)); its own dict, cleared with clear_usage_cache()
_slip_lock = threading.Lock()


def clear_slip_cache(branch_id: int = None, benchmark: bool = True) -> None:
    """A slip was saved, confirmed, appended to or reopened: what the slips say changed (PR-S3). The per-฿100,000 report is built on
    the same usage, so its cache goes too.

    LK (2026-10): `branch_id` limits the drop to that branch's own entries (its slip usage, its stored plans, its stock estimates) -
    thirty branches confirming their slips at the cutoff hour used to empty the caches of all thirty, every time. The other branches'
    entries stay right: a branch's usage is read from ITS slips only. `benchmark=False` leaves the per-฿100,000 report cache alone
    (a call that changed no confirmed slip - the "blank class A" check). No branch = everything, as before."""
    with _slip_lock:
        if branch_id is None:
            _slip_cache.clear()
        else:
            for k in [k for k in _slip_cache if k[1] == int(branch_id)]:
                _slip_cache.pop(k, None)
    _bump_gens(branch_id)
    if benchmark:
        try:
            from . import per_sales
            per_sales.clear_cache()
        except Exception:
            pass


# ── LK · the plan cache ───────────────────────────────────────────────────────────────────────────────────────────
# One branch opens its order page, picks a delivery chip, confirms its slip, saves the draft and submits: five plans from the same
# inputs but two. The suggestion (the CPU-bound part of a plan) is kept per exact request, and a hit is valid only while nothing it was
# built from has moved. The key carries every input the request itself names (branch, round, dates, buffer, expected sales), a hash of
# the stock position it was given (which holds the counts, receipts, slips and open orders of the branch), the branch's own order
# signature (the short shipments it learns from), the branch's own slip generation and those of the company branches (the benchmark
# reads their slips), and core's generations (any prefixed flush, a full flush, a file swap) and this module's (clear_usage_cache).
# TTL: OSHINEI_PLAN_CACHE_TTL (default 120 s), and never longer than the usage cache's - which the tests switch off.
PLAN_CACHE_TTL = float(os.environ.get("OSHINEI_PLAN_CACHE_TTL", "120"))
_PLAN_CACHE_MAX = 192
_plan_cache: dict = {}                                     # key -> (stored at, pickled suggestion)
_plan_lock = threading.Lock()
_plan_gen = 0                                              # bumped by clear_usage_cache() / clear_slip_cache()
_slip_gens: dict = defaultdict(int)                        # branch_id -> bumped when that branch's confirmed slips changed
_memo: dict = {}                                           # small derived-value memo (stock estimates); same generations
_memo_lock = threading.Lock()


def _bump_gens(branch_id=None) -> None:
    global _plan_gen
    with _plan_lock:
        if branch_id is None:
            _plan_gen += 1
            _plan_cache.clear()
        else:
            _slip_gens[int(branch_id)] += 1
            for k in [k for k in _plan_cache if k[1] == int(branch_id)]:
                _plan_cache.pop(k, None)
    with _memo_lock:
        if branch_id is None:
            _memo.clear()
        else:
            for k in [k for k in _memo if k[1] == int(branch_id)]:
                _memo.pop(k, None)


def plan_cache_ttl() -> float:
    ttl = USAGE_CACHE_TTL
    return 0.0 if not ttl or ttl <= 0 else min(PLAN_CACHE_TTL, ttl)


_PATH_OF: dict = {}                                        # id(connection) -> (connection, file path): a connection never changes file


def _db_path(con) -> str:
    ent = _PATH_OF.get(id(con))
    if ent is not None and ent[0] is con:
        return ent[1]
    path = next((r[2] for r in con.execute("PRAGMA database_list") if r[1] == "main"), "")
    if len(_PATH_OF) >= 256:
        _PATH_OF.clear()
    _PATH_OF[id(con)] = (con, path)
    return path


def _gen_base(con):
    """(file path, core generations, this module's generation) - None for a connection with no file behind it, and for one with a
    transaction open: it may read rows nobody else can see (an import preview) and must not publish them."""
    if con.in_transaction:
        return None
    try:
        path = _db_path(con)
    except Exception:
        return None
    if not path:
        return None
    core = _loaded("core")
    return (path, getattr(core, "_db_gen", 0), getattr(core, "_data_gen", 0), getattr(core, "_cache_gen", 0), _plan_gen)


def memo_get(con, branch_id: int, name: str, args: tuple):
    """(key, value) - a value derived from one branch's confirmed slips (stock.py's estimates), kept while no generation moved;
    value None = miss (hand the key to memo_put). key None = caching is off for this connection."""
    ttl = plan_cache_ttl()
    base = _gen_base(con) if ttl else None
    if base is None:
        return None, None
    key = (name, int(branch_id), args, base, _slip_gens[int(branch_id)])
    with _memo_lock:
        hit = _memo.get(key)
    if hit and _time.monotonic() - hit[0] < ttl:
        return key, hit[1]
    return key, None


def memo_put(key, value) -> None:
    if key is None:
        return
    with _memo_lock:
        if len(_memo) >= 512:
            for k in sorted(_memo, key=lambda k: _memo[k][0])[:128]:
                _memo.pop(k, None)
        _memo[key] = (_time.monotonic(), value)


def _orders_sig(con, branch_id: int) -> tuple:
    """What the suggestion reads of the branch's orders (short shipments, the last sent order): their count, newest id and newest
    stamps. A draft being saved changes none of it, so the autosave does not empty the cache."""
    r = con.execute("SELECT COUNT(*), COALESCE(MAX(order_id), 0), COALESCE(MAX(submitted_at), ''), COALESCE(MAX(processed_at), ''),"
                    " COALESCE(MAX(received_at), '') FROM bc_order WHERE branch_id=? AND status IN ('submitted','processed','received')",
                    (branch_id,)).fetchone()
    return tuple(r)


def suggest_cached(con, branch_id: int, rnd: str, order_day: date, **kw) -> dict:
    """forecast.suggest behind the plan cache (see above), inside the plan slots on a miss. The caller gets its own copy."""
    import pickle
    ttl = plan_cache_ttl()
    base = _gen_base(con) if ttl else None
    key = None
    if base is not None:
        try:
            sig = json.dumps(kw.get("stock"), sort_keys=True, default=str)
        except (TypeError, ValueError):
            sig = None
        if sig is not None:
            company = tuple(_slip_gens[int(i)] for i in company_branch_ids(con))
            key = ("plan", int(branch_id), rnd, order_day.isoformat(), kw.get("weeks"), kw.get("season"),
                   (kw.get("as_of") or date.min).isoformat(), round(float(kw.get("uplift_pct") or 0.0), 6),
                   (kw.get("delivery") or date.min).isoformat(), kw.get("ref_branch"), kw.get("expected_sales"),
                   hash(sig), base, _slip_gens[int(branch_id)], company, _orders_sig(con, branch_id))
            with _plan_lock:
                hit = _plan_cache.get(key)
            if hit and _time.monotonic() - hit[0] < ttl:
                return pickle.loads(hit[1])                  # plain dicts / lists / numbers: a pickle is a faster deep copy
    with _plan_slots:                  # only the CPU-bound part: a few plans at a time, so a writer keeps the GIL
        out = suggest(con, branch_id, rnd, order_day, **kw)
    if key is not None:
        stored = pickle.dumps(out, protocol=pickle.HIGHEST_PROTOCOL)
        with _plan_lock:
            if len(_plan_cache) >= _PLAN_CACHE_MAX:
                for k in sorted(_plan_cache, key=lambda k: _plan_cache[k][0])[:_PLAN_CACHE_MAX // 4]:
                    _plan_cache.pop(k, None)
            _plan_cache[key] = (_time.monotonic(), stored)
    return out


def _slip_days(con, branch_id: int, start: date, end: date) -> list:
    return [d for (d,) in con.execute(
        "SELECT slip_date FROM bc_issue_slip WHERE branch_id=? AND status='confirmed' AND slip_date BETWEEN ? AND ? ORDER BY slip_date",
        (branch_id, start.isoformat(), end.isoformat()))]


def _usage_history_slips_uncached(con, branch_id: int, start: date, end: date) -> tuple:
    days = _slip_days(con, branch_id, start, end)
    use = {}
    unconverted = 0
    for day, code, ub, wb, qu, qw in con.execute(
            "SELECT s.slip_date, l.ingredient_code, l.qty_used_base, l.qty_waste_base, l.qty_used, l.qty_waste"
            " FROM bc_issue_slip s JOIN bc_issue_line l USING(slip_id)"
            " WHERE s.branch_id=? AND s.status='confirmed' AND s.slip_date BETWEEN ? AND ?",
            (branch_id, start.isoformat(), end.isoformat())):
        if ((qu or 0) > 0 and ub is None) or ((qw or 0) > 0 and wb is None):
            unconverted += 1                              # a line the units cannot turn into base units: its NULL part is skipped
        q = (ub or 0.0) + (wb or 0.0)                     # waste is part of what leaves the shelf: no yield gross-up
        if q > 0:
            use.setdefault(code, {})
            use[code][day] = use[code].get(day, 0.0) + q
    stats = {"source": "slips", "confirmed_days": len(days), "window_days": (end - start).days + 1,
             "missed": [], "missed_lines": 0, "via": {}, "unconverted_lines": unconverted}
    return use, days, stats


def usage_history_slips(con, branch_id: int, start: date, end: date) -> tuple:
    """({ingredient: {day: used + waste, base units}}, days with a confirmed slip, stats) - the same shape as usage_history, read from
    the CONFIRMED issue slips (a draft is nobody's figure yet). Cached like usage_history (own dict, same TTL and lock pattern, key =
    database file + core's generations); the slip write endpoints call clear_slip_cache()."""
    ttl = USAGE_CACHE_TTL
    key = _usage_key(con, branch_id, start, end, "slips") if ttl and ttl > 0 else None
    if key is not None:
        with _slip_lock:
            hit = _slip_cache.get(key)
        if hit and _time.monotonic() - hit[0] < ttl:
            use, days, stats = hit[1]
            return use, list(days), dict(stats)
    with _flight(key):                 # LK: one computation for requests that miss together (see usage_history)
        if key is not None:
            with _slip_lock:
                hit = _slip_cache.get(key)
            if hit and _time.monotonic() - hit[0] < ttl:
                use, days, stats = hit[1]
                return use, list(days), dict(stats)
        use, days, stats = _usage_history_slips_uncached(con, branch_id, start, end)
        if key is not None:
            with _slip_lock:
                if len(_slip_cache) >= _USAGE_CACHE_MAX:
                    for k in sorted(_slip_cache, key=lambda k: _slip_cache[k][0])[:max(1, _USAGE_CACHE_MAX // 4)]:
                        _slip_cache.pop(k, None)
                _slip_cache[key] = (_time.monotonic(), (use, list(days), dict(stats)))
        return use, days, stats


def demand_source(con, branch_id: int, start: date, end: date, month: str) -> tuple:
    """PR-S3 · the ONE place that decides what a branch's demand is read from: (use, days, stats) with stats["source"] =
      recipes  mode `full`: forecast.usage_history, as before;
      slips    mode `stock` and at least SLIP_MIN_DAYS confirmed slip days in [start, end]: usage_history_slips;
      none     mode `stock` and fewer: ({}, the days that have a slip, stats) - never a fall back to the recipes (owner O1)."""
    from .close_settings import stock_mode
    if not stock_mode(con):
        use, days, stats = usage_history(con, branch_id, start, end, month)
        stats["source"] = "recipes"
        stats["window_days"] = (end - start).days + 1
        return use, days, stats
    days = _slip_days(con, branch_id, start, end)
    if len(days) >= SLIP_MIN_DAYS:
        return usage_history_slips(con, branch_id, start, end)
    return {}, days, {"source": "none", "confirmed_days": len(days), "min_days": SLIP_MIN_DAYS,
                      "window_days": (end - start).days + 1, "missed": [], "missed_lines": 0, "via": {}}


def company_branch_ids(con, exclude=None) -> list:
    """PR-S3 (owner 2026-10-22): the company branches - `branches.branch_type = 'master'` (the master / franchise split the Monthly Report
    scope uses), active, not the 'ALL' row - without `exclude`. In stock mode every one of them whose demand source is the slips (at
    least SLIP_MIN_DAYS confirmed days) is part of the all-branch median; franchise branches never are."""
    return [b for (b,) in con.execute(
        "SELECT branch_id FROM branches WHERE branch_type='master' AND name<>'ALL' AND COALESCE(active,1)=1 ORDER BY branch_id")
        if exclude is None or int(b) != int(exclude)]


def median_branch_ids(con, exclude=None) -> list:
    """PL1 #7 · THE set behind "เฉลี่ยทุกสาขา" / the benchmark, for the order suggestion, the daily variance and the per-฿100,000 report alike.
    Stock mode: every active company branch (company_branch_ids); each of them counts only while its demand source is the slips (at least
    SLIP_MIN_DAYS confirmed days - demand_source gives `none` below that, so it adds nothing to a median). Full mode: the branches chosen
    on the ค่ากลาง settings page (benchmark.branch_ids), as before."""
    from .close_settings import stock_mode
    if stock_mode(con):
        return company_branch_ids(con, exclude=exclude)
    from . import benchmark as BM
    return [i for i in BM.branch_ids(con) if exclude is None or int(i) != int(exclude)]


def source_label(con, branch_id: int) -> dict:
    """PR-S3 · where the order suggestion of a branch comes from right now, for a list of branches (no usage is walked, only the slip
    days are counted): {kind: recipes | slips | benchmark | none, confirmed_days, window_days, min_days, branches}. In stock mode
    `benchmark` = fewer than SLIP_MIN_DAYS confirmed days of its own and `branches` other company branches that have enough."""
    from .close_settings import stock_mode
    out = {"kind": "recipes", "confirmed_days": None, "window_days": None, "min_days": SLIP_MIN_DAYS, "branches": 0}
    if not stock_mode(con):
        return out
    last = con.execute("SELECT MAX(day) FROM sales_sku_daily WHERE branch_id=?", (branch_id,)).fetchone()[0]
    as_of = date.fromisoformat(last) if last else now_bkk().date() - timedelta(days=1)
    start = era_start(as_of - timedelta(days=7 * HISTORY_WEEKS - 1), as_of)
    n = len(_slip_days(con, branch_id, start, as_of))
    out.update(kind="slips" if n >= SLIP_MIN_DAYS else "none", confirmed_days=n, window_days=(as_of - start).days + 1)
    if out["kind"] == "none":
        k = 0
        for b in median_branch_ids(con, exclude=branch_id):
            bl = con.execute("SELECT MAX(day) FROM sales_sku_daily WHERE branch_id=?", (b,)).fetchone()[0]
            bo = date.fromisoformat(bl) if bl else as_of
            if len(_slip_days(con, b, era_start(bo - timedelta(days=7 * HISTORY_WEEKS - 1), bo), bo)) >= SLIP_MIN_DAYS:
                k += 1
        if k:
            out.update(kind="benchmark", branches=k)
    return out


def source_info(stats: dict) -> dict:
    """The `source` block of an answer: where a branch's demand was read from and how much of the window the slips cover."""
    kind = (stats or {}).get("source", "recipes")
    return {"kind": kind, "confirmed_days": (stats or {}).get("confirmed_days"),
            "window_days": (stats or {}).get("window_days"), "min_days": SLIP_MIN_DAYS}


def era_start(start: date, as_of: date) -> date:
    """History never reaches back across the Grand Menu changeover: before it the
    dishes (and so the ingredient mix) were different, and averaging over both
    menus mis-states what is used now."""
    from .dated import CUTOVER
    cut = date.fromisoformat(CUTOVER)
    return max(start, cut) if as_of >= cut else start


MIN_DAYS_AFTER_SKIP = 7             # PR-B4a (1.8): fewer trading days than this would be left once the short-shipped days are out - keep them all


def _daily_model(series: dict, days: list, as_of: date, skip=None) -> dict:
    """weekday means, trend, residual σ for one ingredient over the trading days.

    PR-B4a (1.8): `skip` = days to leave out of THIS item's figures - the delivery windows in which the warehouse short-shipped it
    (short_ship_days). A day the item could not be had is not a day nobody wanted it. When fewer than MIN_DAYS_AFTER_SKIP days
    would be left, nothing is left out (a thin history is worse than a biased one). `skipped` counts the days left out."""
    skipped = 0
    if skip:
        kept = [d for d in days if d not in skip]
        if len(kept) >= MIN_DAYS_AFTER_SKIP and len(kept) < len(days):
            skipped, days = len(days) - len(kept), kept
    # LK: the weekday of a day is looked up once per day string (cached), the two 14-day thresholds are computed once - this ran
    # per ingredient x per day (30,000 isoformat() calls in one plan)
    by_wd = defaultdict(list)
    vals = [series.get(d, 0.0) for d in days]
    wds = [_wd_iso(d) for d in days]
    for w, v in zip(wds, vals):
        by_wd[w].append(v)
    mean_wd = {wd: sum(v) / len(v) for wd, v in by_wd.items() if v}
    overall = (sum(vals) / len(days)) if days else 0.0
    c14 = (as_of - timedelta(days=14)).isoformat()
    c28 = (as_of - timedelta(days=28)).isoformat()
    last = [v for d, v in zip(days, vals) if d > c14]
    prev = [v for d, v in zip(days, vals) if c28 < d <= c14]
    trend = 1.0
    if last and prev and sum(prev) > 0:
        trend = max(0.7, min(1.3, (sum(last) / len(last)) / (sum(prev) / len(prev))))
    resid = [v - mean_wd.get(w, overall) for w, v in zip(wds, vals)]
    sigma = math.sqrt(sum(r * r for r in resid) / (len(resid) - 1)) if len(resid) > 1 else 0.0
    return {"mean_wd": mean_wd, "overall": overall, "trend": trend, "sigma": sigma, "skipped": skipped}


# 1.8 · the reasons on which the warehouse did NOT send what the branch confirmed because it did not have it (hub.SS_REASONS):
# "short" (supplier short / could not send in full) and "substitute" (another product sent instead). The others - pack size, merged
# with another round, sent from the warehouse's own stock, other - do not say the branch was left without the item.
SHORT_SHIP_REASONS = ("short", "substitute")


def short_ship_days(con, branch_id: int, start: date, end: date) -> dict:
    """PR-B4a (1.8): {ingredient_code: {iso day}} - the days of the delivery window that FOLLOWS a short shipment of the item to this
    branch (processed units below what the branch confirmed, reason in SHORT_SHIP_REASONS), inside [start, end].

    Why this and not the days the stock was 0: the usage the forecast learns from is the BoM of what was SOLD, so a day on which the
    item ran out shows as low use only when the dish could not be sold. The system stock per day would tell it, but only as well as
    the slips behind it are confirmed - and during the roll-out most days are not (the stock figure then runs high or, with the
    estimate of 1.3, is itself an estimate). The warehouse's reason on a short line is a person's statement that the item was not
    sent, so it is the signal used. The window is the cover the short order was meant for (cover_for_delivery): from its delivery
    day to the day before the next regular delivery (a week for the salmon round)."""
    out = {}
    try:
        rows = con.execute(
            "SELECT o.round, o.cycle_date, o.delivery_date, l.ingredient_code FROM bc_order o JOIN bc_order_line l USING(order_id)"
            " WHERE o.branch_id=? AND o.status IN ('processed','received') AND l.ss_reason IN (%s)"
            " AND COALESCE(l.processed_units, 0) < COALESCE(l.confirmed_units, 0)" % ",".join("?" * len(SHORT_SHIP_REASONS)),
            (branch_id,) + SHORT_SHIP_REASONS).fetchall()
    except Exception:
        return out
    if not rows:
        return out
    from .hub import delivery_of
    s0, s1 = start.isoformat(), end.isoformat()
    for rnd, cyc, dd, code in rows:
        try:
            dlv = date.fromisoformat(delivery_of(con, branch_id, rnd, cyc, dd))
            _c, _d, cf, ct = cover_for_delivery(con, branch_id, rnd, dlv)
        except (KeyError, TypeError, ValueError):
            continue
        d = cf
        while d <= ct:
            iso = d.isoformat()
            if s0 <= iso <= s1:
                out.setdefault(code, set()).add(iso)
            d += timedelta(days=1)
    return out


def _abc(values: dict) -> dict:
    total = sum(values.values()) or 1.0
    out, run = {}, 0.0
    for code, v in sorted(values.items(), key=lambda kv: -kv[1]):
        run += v
        out[code] = "A" if run / total <= 0.80 else ("B" if run / total <= 0.95 else "C")
    return out


def season_factor(con, branch_id: int, c_from: date, c_to: date) -> dict:
    """{factor, mode, reason} — the season / festival factor the order plan applies over the cover days
    (the calendar and last-year logic of routers.ingredients); 1.0 when it cannot be resolved."""
    bname = (con.execute("SELECT name FROM branches WHERE branch_id=?", (branch_id,)).fetchone() or [None])[0]
    try:
        return _loaded("routers.ingredients")._ing_season_resolve(con, bname, c_from.isoformat(), c_to.isoformat(), "auto")
    except Exception:
        return {"factor": 1.0, "mode": "off", "reason": ""}


def suggest(con, branch_id: int, rnd: str, order_day: date, weeks: int = HISTORY_WEEKS, season: str = "auto",
            as_of: date = None, stock: dict = None, uplift_pct: float = 0.0, delivery: date = None,
            ref_branch: int = None, expected_sales: float = None) -> dict:
    """The suggestion for one branch, one round, one delivery (nothing saved). `delivery` names
    the delivery day; without it `order_day` is read as "the window open on that day".

    `uplift_pct` (PR-19): the branch's own expectation for the cover period — +20 scales
    the forecast of every item by 1.2 before safety and stock; every row also carries
    the unscaled `forecast_base` / `need_base` so the page can show "ปกติ".

    `stock` (orders.stock_position): the system stock as of `through` (last month-end count + goods
    received − confirmed issue slips), plus what is on order — deducted per item after projecting
    the use between `through` and the first covered day.

    PR-V: every row also carries `per_sales` (its usage per ฿100,000 of the branch's sales over the same history) and the
    answer `sales_normal` (the "ปกติ" sales of the cover days: weekday-fair mean × trend × season). A branch with fewer than
    per_sales.NEW_BRANCH_DAYS trading days of sales AND a `ref_branch` is planned from that branch's usage per ฿100,000 ×
    `expected_sales` (what the branch itself expects to sell over the cover period; trend, season and the buffer % do not
    apply — the figure already is the expectation); without `expected_sales` there are no rows and `ref_mode.needs_sales`."""
    from .prices import price_for
    from . import per_sales as _ps
    if delivery:
        order_day, dlv, c_from, c_to = cover_for_delivery(con, branch_id, rnd, delivery)
    else:
        order_day, dlv, c_from, c_to = cover_period(con, branch_id, rnd, order_day)
    last_data = con.execute("SELECT MAX(day) FROM sales_sku_daily WHERE branch_id=?", (branch_id,)).fetchone()[0]
    as_of = as_of or (date.fromisoformat(last_data) if last_data else order_day - timedelta(days=1))
    start = era_start(as_of - timedelta(days=7 * weeks - 1), as_of)
    month = f"{order_day.year:04d}-{order_day.month:02d}"
    use, days, stats = demand_source(con, branch_id, start, as_of, month)
    src = stats.get("source", "recipes")                   # PR-S3: recipes | slips | none (stock mode, too few confirmed slip days)
    bname = (con.execute("SELECT name FROM branches WHERE branch_id=?", (branch_id,)).fetchone() or [None])[0]
    sf = {"factor": 1.0, "mode": "off", "reason": ""}
    if season == "auto":
        sf = season_factor(con, branch_id, c_from, c_to)
    factor = float(sf.get("factor") or 1.0)
    up = 1.0 + float(uplift_pct or 0.0) / 100.0            # the branch's buffer on top of the season factor
    master = {r[0]: r[1:] for r in con.execute(
        "SELECT code, name_th, base_unit, recv_unit, qty_per_recv, moq, shelf_days, kind, monitored,"
        " sourcing_default, lead_days FROM bc_ingredient")}
    has_round_col = "order_round" in {r[1] for r in con.execute("PRAGMA table_info(bc_ingredient)")}
    salmon = {r[0] for r in con.execute("SELECT code FROM bc_ingredient WHERE order_round='salmon'")} \
        if has_round_col else set()
    salmon = salmon or set(SALMON_DEFAULT)
    from .central import central_map
    cmap = central_map(con)                               # S11: the centre's list decides what is ordered from it
    cover = [c_from + timedelta(days=i) for i in range((c_to - c_from).days + 1)]
    # PR-V · usage per ฿100,000 of sales (own history), and the "ปกติ" sales the forecast assumes for the cover days
    sales_day = _ps.sales_by_day(con, branch_id, start, as_of)
    ps_items = _ps.ratio_from(use, _ps.align_sales(sales_day, days, stats), start, as_of)["items"]
    sales_normal = None
    if days and sales_day:
        n_ = _ps.normal_from(sales_day, days, as_of, cover, factor)["total"]
        sales_normal = round(n_, 2) if n_ > 0 else None
    ref_mode = None
    fallback = None
    is_bench = ref_branch == _ps.BENCH
    if src == "none":
        # PR-S3 · O1: stock mode, fewer than SLIP_MIN_DAYS confirmed slip days. (ข) another branch has enough: the benchmark per
        # ฿100,000 of the slip branches x the sales expected (by default this branch's own "ปกติ" sales of the cover days - it has sales
        # without slips); (ก) none has: no suggestion at all, the branch types its own order.
        n_slip = int(stats.get("confirmed_days") or 0)
        rb = _ps.benchmark_basis(con, branch_id, month, weeks=weeks, cover=cover, factor=factor)
        if rb["ok"]:
            exp = float(expected_sales or 0.0)
            if exp <= 0 and sales_day:
                own = _ps.normal_from(sales_day, sorted(sales_day), as_of, cover, factor)["total"]
                exp = round(own, 2) if own > 0 else 0.0
                expected_sales = exp or expected_sales
            ref_mode = {"active": True, "branch_id": None, "name": rb["name"], "own_days": n_slip, "min_days": SLIP_MIN_DAYS,
                        "expected_sales": exp or None, "ref_sales_normal": rb["sales_normal"], "needs_sales": exp <= 0,
                        "ref_ok": True, "benchmark": True, "label": rb.get("label"),
                        "message": _ps.NEED_SALES_TH if exp <= 0 else ""}
            k = len(rb.get("branches") or [])
            fallback = {"mode": "benchmark", "confirmed_days": n_slip, "min_days": SLIP_MIN_DAYS, "branches": k,
                        "message": f"จำนวนที่ควรสั่งจากเฉลี่ยทุกสาขา {k} สาขา (ใบตัดเบิกของสาขา {n_slip}/{SLIP_MIN_DAYS} วัน)"}
            sales_normal = None
            if exp > 0:
                use, ps_items = rb["use"], rb["items"]
                stats["via"] = (rb["stats"] or {}).get("via", {})
                factor, up = 1.0, 1.0
        else:
            fallback = {"mode": "none", "confirmed_days": n_slip, "min_days": SLIP_MIN_DAYS,
                        "message": f"ใบตัดเบิกยังไม่พอ ({n_slip}/{SLIP_MIN_DAYS} วัน) · กรอกยอดสั่งเอง"}
            sales_normal = None
    elif ref_branch and (is_bench or int(ref_branch) != int(branch_id)) and len(days) < _ps.NEW_BRANCH_DAYS:
        if is_bench:                                       # PR-X: the median of the company branches
            rb = _ps.benchmark_basis(con, branch_id, month, weeks=weeks, cover=cover, factor=factor)
        else:
            rb = _ps.reference_basis(con, int(ref_branch), month, weeks=weeks, cover=cover, factor=factor)
        exp = float(expected_sales or 0.0)
        ref_mode = {"active": True, "branch_id": rb["branch_id"], "name": rb["name"], "own_days": len(days),
                    "min_days": _ps.NEW_BRANCH_DAYS, "expected_sales": exp or None, "ref_sales_normal": rb["sales_normal"],
                    "needs_sales": exp <= 0, "ref_ok": rb["ok"], "benchmark": bool(is_bench),
                    "label": rb.get("label") or f"คำนวณจากสาขาอ้างอิง {rb['name']}",
                    "message": _ps.NEED_SALES_TH if exp <= 0 else (
                        "" if rb["ok"] else (f"{rb['name']} ยังไม่มีประวัติการขายพอ" if is_bench
                                             else f"สาขาอ้างอิง {rb['name']} ยังไม่มีประวัติการขายพอ"))}
        sales_normal = None
        if exp > 0 and rb["ok"]:
            use, ps_items = rb["use"], rb["items"]
            stats["via"] = (rb["stats"] or {}).get("via", {})
            factor, up = 1.0, 1.0
        else:
            use, ps_items = {}, {}
    # the days between the stock figure and the first covered day still eat into it
    # (the system stock is as of `through` — the day of the order, after that day's issue slip)
    through = (stock or {}).get("through")
    basis = ((stock or {}).get("basis") or {}).get("date")
    if through:
        pre_from = date.fromisoformat(through) + timedelta(days=1)
    else:
        pre_from = max(as_of + timedelta(days=1), date.fromisoformat(basis)) if basis else as_of + timedelta(days=1)
    pre_days = [pre_from + timedelta(days=i) for i in range(max((c_from - pre_from).days, 0))]
    # spend over the window for ABC. PR-B4a (1.6): an item with no price is weighed at its last known price, else the benchmark
    # branches' median (prices.PriceBook) - not at 0, which put every unpriced item in class C (a small safety stock, and no reason
    # ever asked). Only the class (and so the safety stock) reads the fallback; the price and ฿ shown stay the real ones.
    pbook = PriceBook(con, month, cmap)
    spend = {}
    for code, s in use.items():
        spend[code] = sum(s.values()) * pbook.ref(code)
    klass = _abc(spend)
    conv = Converter(con)                                  # 1.7 · is this pack a big one?
    skip_map = {} if ref_mode else short_ship_days(con, branch_id, start, as_of)         # 1.8 · days an item was short-shipped
    std_lead = int(calendar(con, branch_id)[rnd]["lead_days"])
    rows = []
    for code, s in use.items():
        if (code in salmon) != (rnd == "salmon"):
            continue
        if ref_mode:                                        # a new branch: the reference branch's usage per ฿100,000 × the expected sales
            if code not in ps_items:
                continue
            m = {"mean_wd": {}, "overall": ps_items[code]["per_100k"] * float(expected_sales) / 1e5 / max(len(cover), 1),
                 "trend": 1.0, "sigma": 0.0}
        else:
            m = _daily_model(s, days, as_of, skip=skip_map.get(code))
        if m["overall"] <= 0:
            continue
        name, base, recv, per, moq, shelf, kind, mon, src, lead = master.get(code, (None,) * 10)
        # PR-B4a (1.8): an item whose own lead time is longer than the round's standard (cutoff → delivery) cannot be topped up by the
        # next cutoff in time: it must last the extra days beyond the next delivery. extra = lead_days − the round's standard lead, when positive.
        extra = 0 if (ref_mode or not lead) else max(0, int(lead) - std_lead)
        cover_i = cover + [c_to + timedelta(days=i) for i in range(1, extra + 1)]
        # PR-B4a (1.5): never order past the shelf life - the cover days beyond it are not ordered (a delivery keeps `shelf` days)
        cap = None
        if shelf and int(shelf) > 0 and len(cover_i) > int(shelf):
            cap = int(shelf)
            cover_i = cover_i[:cap]
        per_day_base = [m["mean_wd"].get(_wd(d), m["overall"]) * m["trend"] * factor for d in cover_i]
        fc_base = sum(per_day_base)
        fc = fc_base * up
        k = klass.get(code, "C")
        days_n = len(cover_i)
        safety = Z[k] * m["sigma"] * math.sqrt(max(days_n, 1))
        need = fc + safety
        need_base = fc_base + safety
        oh = (stock or {}).get("qty", {}).get(code)
        onord = (stock or {}).get("on_order", {}).get(code, 0.0)
        pre = projected = None
        need_no_est = None
        if oh is not None:                                  # counted: deduct what will still be there
            pre = sum(m["mean_wd"].get(_wd(d), m["overall"]) * m["trend"] * factor for d in pre_days)
            projected = oh - pre + onord
            est = (stock or {}).get("estimated_qty", {}).get(code, 0.0)
            if est:                                         # PR-B4a (1.3): the same need without the estimate - orders.plan compares the packs
                need_no_est = max(0.0, fc + safety - max(projected + est, 0.0))
            need = max(0.0, need - max(projected, 0.0))
            need_base = max(0.0, need_base - max(projected, 0.0))
        elif onord:
            need = max(0.0, need - onord)
            need_base = max(0.0, need_base - onord)
        big = bool(per) and is_big_pack(conv, code, per)
        down = False
        if per:                                             # 1.7 · a big pack is not rounded up for a remainder the safety stock covers
            units, down = to_order_units_ex(need, per, moq or 1, safety=safety, big=big)
        else:
            units = None
        p = price_for(con, code, month)
        ppb = p[0] if p else None
        pchip = pbook.chip(code, base)
        rows.append({"code": code, "name": name or code, "base_unit": base, "class": k, "kind": kind,
                     "monitored": bool(mon),
                     "sourcing": ("central" if code in cmap else "local") if cmap else (src or "central"),
                     "per_day": round(fc / len(cover_i), 3) if cover_i else 0, "forecast": round(fc, 2),
                     "forecast_base": round(fc_base, 2), "need_base": round(need_base, 2), "need_no_est": need_no_est,
                     "safety": round(safety, 2), "need": round(need, 2), "trend": round(m["trend"], 3),
                     "on_hand": None if oh is None else round(oh, 2), "on_order": round(onord, 2),
                     "pre_cover_use": None if pre is None else round(pre, 2),
                     "projected": None if projected is None else round(projected, 2), "moq": moq,
                     "sigma": round(m["sigma"], 3), "recv_unit": recv, "qty_per_recv": per,
                     "suggested_units": units, "pack_known": bool(per),
                     "price_per_base": ppb, "via": stats.get("via", {}).get(code, []),
                     "per_sales": ({"per_100k": round(ps_items[code]["per_100k"], 2), "steady": ps_items[code]["steady"],
                                    "swing_pct": ps_items[code]["swing_pct"]} if code in ps_items else None),
                     "ref_label": ref_mode["label"] if ref_mode else None,
                     # PR-B4a: why a figure is not the plain forecast
                     "shelf_days": int(shelf) if shelf else None, "shelf_cap_days": cap,        # 1.5 · "จำกัดตามอายุการเก็บ N วัน"
                     "price_missing": pbook.info(code) is not None, "price_note": pchip,                          # 1.6 · "ไม่มีราคา (ใช้ราคา …)"
                     "price_fallback": pbook.info(code),
                     "big_pack": big, "round_down": down,                                         # 1.7 · "ปัดลง (เหลือพอ)"
                     "lead_days": int(lead) if lead else None, "lead_extra_days": extra,         # 1.8 · the cover is longer by `extra` days
                     "short_ship_skipped": m.get("skipped", 0),                                   # 1.8 · days left out of the means
                     "value": round((units * per if units else need) * ppb, 2) if ppb else None})
    rows.sort(key=lambda r: -(r["value"] or 0))
    if fallback and fallback["mode"] == "none":            # PR-S3 (ก): the list of the branch, no figure to suggest
        rows = _unsuggested_rows(con, branch_id, rnd, order_day, stock, master, salmon, cmap)
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
            "salmon_codes": sorted(salmon), "sales_normal": sales_normal, "ref_mode": ref_mode,
            "source": source_info(stats), "fallback": fallback}


def _unsuggested_rows(con, branch_id, rnd, order_day, stock, master, salmon, cmap) -> list:
    """PR-S3 (ก): the rows of a branch that has no suggestion (stock mode, too few confirmed slips, no other branch to borrow from):
    the items the page lists today - the branch's count sheet (monitored items), the centre's list, what it holds by the last count,
    what its latest sent order of this round carried - each with `suggested` None and `last_order_qty` (that order's quantity)."""
    from .central import NON_FOOD
    from .prep import book as _prep_book
    last = {}
    r = con.execute("SELECT order_id FROM bc_order WHERE branch_id=? AND round=? AND cycle_date<? AND status IN"
                    " ('submitted','processed','received') ORDER BY cycle_date DESC, order_id DESC LIMIT 1",
                    (branch_id, rnd, order_day.isoformat())).fetchone()
    if r:
        last = {c: q for c, q in con.execute("SELECT ingredient_code, confirmed_units FROM bc_order_line WHERE order_id=?"
                                             " AND COALESCE(confirmed_units, 0) > 0", (r[0],))}
    has_sub = set(_prep_book(con))
    codes = {c for c, m in master.items() if m[7] and c not in has_sub}              # monitored (the count sheet)
    codes |= {c for c in cmap if c in master and not c.startswith(NON_FOOD)}
    codes |= {c for c in ((stock or {}).get("qty") or {}) if c in master}
    codes |= {c for c in last if c in master}
    rows = []
    for code in sorted(codes):
        if (code in salmon) != (rnd == "salmon"):
            continue
        name, base, recv, per, moq, shelf, kind, mon, src, lead = master[code]
        rows.append({"code": code, "name": name or code, "base_unit": base, "class": "C", "kind": kind, "monitored": bool(mon),
                     "sourcing": ("central" if code in cmap else "local") if cmap else (src or "central"),
                     "per_day": 0, "forecast": 0.0, "forecast_base": 0.0, "need_base": 0.0, "need_no_est": None, "safety": 0.0,
                     "need": 0.0, "trend": 1.0, "on_hand": None, "on_order": 0.0, "pre_cover_use": None, "projected": None,
                     "moq": moq, "sigma": 0.0, "recv_unit": recv, "qty_per_recv": per, "suggested_units": None,
                     "suggested": None, "last_order_qty": last.get(code), "pack_known": bool(per), "price_per_base": None,
                     "via": [], "per_sales": None, "ref_label": None, "shelf_days": int(shelf) if shelf else None,
                     "shelf_cap_days": None, "price_missing": False, "price_note": None, "price_fallback": None,
                     "big_pack": False, "round_down": False, "lead_days": int(lead) if lead else None, "lead_extra_days": 0,
                     "short_ship_skipped": 0, "value": None})
    return rows


def covers(con, branch_id: int, rnd: str, order_day: date, weeks: int = HISTORY_WEEKS, delivery: date = None) -> dict:
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


def backtest(con, branch_id: int, weeks: int = 4, history_weeks: int = HISTORY_WEEKS, as_of: date = None) -> dict:
    """Replay each of the last `weeks` weeks: forecast every day from the data
    before that week only, compare with what was actually used. MAPE weighted by
    actual usage, overall and by ABC class."""
    last_data = con.execute("SELECT MAX(day) FROM sales_sku_daily WHERE branch_id=?", (branch_id,)).fetchone()[0]
    if not last_data:
        return {"available": False}
    as_of = as_of or date.fromisoformat(last_data)
    first = as_of - timedelta(days=7 * (weeks + history_weeks) - 1)
    month = f"{as_of.year:04d}-{as_of.month:02d}"
    use, days, _st = demand_source(con, branch_id, first, as_of, month)
    if _st.get("source") == "none":                        # PR-S3: stock mode, too few confirmed slips to replay
        return {"available": False, "source": source_info(_st)}
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
            "target_a": 15.0, "source": source_info(_st)}
