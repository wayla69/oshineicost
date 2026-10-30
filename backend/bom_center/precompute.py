# -*- coding: utf-8 -*-
"""bom_center.precompute — the order plans of every branch, computed ahead of time (owner 2026-10-29: "ทำคำนวณล่วงหน้า").

Why: the cutoff-hour load test (scripts/load_branches.py) showed a cold plan open at p95 7-10 s when 30 branches open their
order page at once. Production is ONE uvicorn process on 2 shared cores and the plan's CPU-bound part (forecast.suggest) runs
one at a time (forecast._plan_slots), so 30 cold plans queue behind each other. Here they are computed in the background,
before anybody asks, and opening the page is a lookup.

THE STORE (in memory - the app runs as one uvicorn worker, start.sh; a second process would only miss and compute live)
    forecast.suggest_cached keeps every suggestion it computes in two places: its short plan cache (120 s, unchanged since LK)
    and this durable store, keyed by everything the suggestion is built from:
      * the request itself (branch, round, dates, buffer, expected sales) and a hash of the stock position it was given
        (counts, receipts, slips and open orders of the branch - so a count, a receipt or a slip of the branch changes the key);
      * the database file, the swap generation (core._db_gen), the data generation (core._data_gen: every full flush, every
        sync swap), forecast's own generation (clear_usage_cache: recipe-side writes);
      * this module's INPUT generation - moved by every prefixed invalidate_cache() that is not known to be harmless (a price,
        a setting, the mode, the benchmark ...; core calls on_flush) - and the BRANCH generation (a write scoped to one branch:
        flush_scope(), the delivery calendar);
      * the branch's own confirmed-slip generation and its order signature (submitted / processed / received orders).
    An entry has no TTL: it is right while its key is. A suggestion that read OTHER branches' slips (the benchmark / a reference
    branch: `ref_mode`, or no demand source of its own) also carries the slip generations it was built on and is a miss as soon as
    any of them moved. Unlike the plan cache, the key does NOT carry core._cache_gen - that moves with every prefixed flush in the
    app (the nav badges of every slip save), which emptied every plan of every branch at the cutoff hour.

THE WORKER (one daemon thread, start())
    * after each data swap (cloud sync 06:00 / 12:00 / 16:00, main._swap_incoming_and_warm), every full flush and every
      chain-wide input change: every ordering branch (debounced OSHINEI_PLAN_PRECOMPUTE_DELAY s, default 5);
    * after a write that changes one branch's inputs (slip, count, receipt, order submit, calendar): that branch only (2 s);
    * at start-up, and once a day after 05:00 Bangkok when no pass has run that day (the keys carry the day).
    For each branch, every delivery whose cutoff has not passed within forecast.next_deliveries' horizon, plus the delivery each
    round opens on (what /order/plan answers without `delivery`), through orders.plan with the page's own defaults - so the stored
    key is exactly the one the page asks for. One plan at a time; before each it waits until no live plan is being computed and
    no bom-center request came for QUIET_S (3) seconds, at most 30 s (live requests go first: the cutoff-hour rush computes its own).

STATUS: status() and app_settings['cloud_job_plan_precompute_status'] (written after every full pass), shown by
/api/admin/cloud-jobs/status and judged by the cloud watchdog (a failed pass is a finding).
"""
import contextlib
import json
import os
import threading
import time as _time
import traceback
from collections import defaultdict
from datetime import date, datetime

from .clock import BKK, now_bkk

STATUS_KEY = "cloud_job_plan_precompute_status"
DELAY_ALL = float(os.environ.get("OSHINEI_PLAN_PRECOMPUTE_DELAY", "5"))
DELAY_BRANCH = 2.0
STORE_MAX = int(os.environ.get("OSHINEI_PLAN_STORE_MAX", "600"))
LIVE_WAIT_MAX = 30.0                    # the worker never waits longer than this for live plans to finish
QUIET_S = float(os.environ.get("OSHINEI_PLAN_PRECOMPUTE_QUIET", "3"))   # ... and starts a plan only after this long without a bom-center request
DAILY_HOUR = 5                          # the first pass of a new Bangkok day runs from 05:00 (the sync's 06:00 pass follows)

# Prefixed flushes that never change what a plan reads. Anything else moves the input generation (unknown = assume it matters).
SAFE_PREFIXES = frozenset({"bc_nav_badges_for"})

_lock = threading.Lock()
_input_gen = 0
_branch_gen: dict = defaultdict(int)
_store: dict = {}                       # dkey -> (stored at, pickled suggestion, branch_id, company snapshot | None)
_tls = threading.local()               # .scope: branch ids a flush is limited to · .bg: this thread is the worker
_live = 0                               # live (request) plan computations in flight
_last_live = 0.0                        # monotonic time of the last bom-center request (mark_live)
_counters = {"hits": 0, "misses": 0, "stored": 0, "dropped": 0}

_cv = threading.Condition()
_pending_all = None                     # reason of a pending full pass (None = none pending)
_pending: dict = {}                     # branch_id -> reason
_due = 0.0                              # monotonic time the pending work may start
_thread = None
_enabled = False
_installed = set()
_status: dict = {"running": False, "result": None}
_last_full_day = None


# ── the core hook ─────────────────────────────────────────────────────────────────────────────────────────────────
def install(core_mod) -> None:
    """Register on_flush with core.invalidate_cache (idempotent)."""
    hooks = getattr(core_mod, "_flush_hooks", None)
    if hooks is None or id(core_mod) in _installed:
        return
    if on_flush not in hooks:
        hooks.append(on_flush)
    _installed.add(id(core_mod))


@contextlib.contextmanager
def flush_scope(*branch_ids):
    """invalidate_cache() calls inside this block change the inputs of these branches only (none = of no plan)."""
    prev = getattr(_tls, "scope", None)
    _tls.scope = tuple(int(b) for b in branch_ids if b is not None)
    try:
        yield
    finally:
        _tls.scope = prev


def on_flush(prefixes) -> None:
    """core.invalidate_cache(*prefixes) happened. A full flush moved core._data_gen (already in the key): drop and recompute all.
    A prefixed one moves the input generation unless every prefix is harmless; inside flush_scope() only those branches move."""
    if not prefixes:
        _drop(None)
        schedule(None, "data")
        return
    if all(p in SAFE_PREFIXES for p in prefixes):
        return
    scope = getattr(_tls, "scope", None)
    if scope is None:
        bump(None, "input")
    else:
        for b in scope:
            bump(b, "branch")


def bump(branch_id=None, reason: str = "input") -> None:
    """A plan input changed: chain-wide (None) or for one branch. Old entries become unreachable and are dropped; a recompute
    is scheduled."""
    global _input_gen
    with _lock:
        if branch_id is None:
            _input_gen += 1
        else:
            _branch_gen[int(branch_id)] += 1
    affected = _drop(branch_id)
    if branch_id is None:
        schedule(None, reason)
    else:
        for b in affected | {int(branch_id)}:
            schedule(b, reason)


def gens_moved(branch_id=None) -> None:
    """forecast._bump_gens: its own generations moved (clear_usage_cache / clear_slip_cache) - the keys already changed;
    drop what can no longer be reached and schedule the recompute."""
    affected = _drop(branch_id)
    if branch_id is None:
        schedule(None, "recipes")
    else:
        for b in affected | {int(branch_id)}:
            schedule(b, "slips")


def _drop(branch_id) -> set:
    """Remove the entries of a branch (None = all). Returns the OTHER branches whose entries read other branches' slips (they are
    misses now too when `branch_id`'s slips moved, and are recomputed with it)."""
    with _lock:
        if branch_id is None:
            n = len(_store)
            _store.clear()
            _counters["dropped"] += n
            return set()
        b = int(branch_id)
        others = {v[2] for v in _store.values() if v[3] is not None and v[2] != b}
        for k in [k for k, v in _store.items() if v[2] == b]:
            _store.pop(k, None)
            _counters["dropped"] += 1
        return others


# ── the store ─────────────────────────────────────────────────────────────────────────────────────────────────────
def gens(branch_id: int) -> tuple:
    with _lock:
        return (_input_gen, _branch_gen[int(branch_id)])


def company_snapshot(slip_gens: dict) -> tuple:
    """Every branch's confirmed-slip generation that ever moved (zeros left out: reading a defaultdict adds them)."""
    return tuple(sorted((int(k), v) for k, v in list(slip_gens.items()) if v))


def get(dkey, branch_id: int, slip_gens: dict):
    """The pickled suggestion stored under `dkey`, or None."""
    with _lock:
        hit = _store.get(dkey)
    if hit is not None and hit[3] is not None and hit[3] != company_snapshot(slip_gens):
        hit = None
    with _lock:
        _counters["hits" if hit is not None else "misses"] += 1
    return None if hit is None else hit[1]


def put(dkey, stored: bytes, branch_id: int, gens_at_start: tuple, company) -> None:
    """Keep a suggestion. Not when an input of it moved while it was being computed (its key is already unreachable)."""
    with _lock:
        if (_input_gen, _branch_gen[int(branch_id)]) != gens_at_start:
            return
        if len(_store) >= STORE_MAX and dkey not in _store:
            for k in sorted(_store, key=lambda k: _store[k][0])[:max(1, STORE_MAX // 4)]:
                _store.pop(k, None)
                _counters["dropped"] += 1
        _store[dkey] = (_time.monotonic(), stored, int(branch_id), company)
        _counters["stored"] += 1


def uses_company(out: dict) -> bool:
    """Did this suggestion read other branches' slips? The benchmark / a reference branch (`ref_mode`), or a branch with no demand
    source of its own (stock mode, too few slips: whether ANY company branch has enough decides what it shows)."""
    return out.get("ref_mode") is not None or (out.get("source") or {}).get("kind") == "none"


@contextlib.contextmanager
def live_computation():
    """Wraps a plan computed for a request (not the worker): the worker waits while any is running."""
    global _live
    bg = getattr(_tls, "bg", False)
    if not bg:
        with _lock:
            _live += 1
    try:
        yield
    finally:
        if not bg:
            with _lock:
                _live -= 1


def store_info() -> dict:
    with _lock:
        n = len(_store)
        size = sum(len(v[1]) for v in _store.values())
        return {"entries": n, "bytes": size, "mb": round(size / 1048576, 1), **_counters}


def clear() -> None:
    """Tests: forget everything (store, generations stay)."""
    with _lock:
        _store.clear()


# ── what to compute ───────────────────────────────────────────────────────────────────────────────────────────────
def ordering_branches(con) -> list:
    """The branches that order: active, not 'ALL', with sales in the last 35 days of the data or a branch login."""
    rows = con.execute(
        "SELECT b.branch_id FROM branches b WHERE b.name <> 'ALL' AND COALESCE(b.active, 1) = 1 AND ("
        " EXISTS (SELECT 1 FROM sales_sku_daily s WHERE s.branch_id = b.branch_id"
        "         AND s.day >= date((SELECT MAX(day) FROM sales_sku_daily), '-35 days'))"
        " OR b.name IN (SELECT branch FROM app_user WHERE branch IS NOT NULL AND COALESCE(active, 1) = 1))"
        " ORDER BY b.branch_id").fetchall() if _has_app_user(con) else con.execute(
        "SELECT b.branch_id FROM branches b WHERE b.name <> 'ALL' AND COALESCE(b.active, 1) = 1 AND"
        " EXISTS (SELECT 1 FROM sales_sku_daily s WHERE s.branch_id = b.branch_id"
        "         AND s.day >= date((SELECT MAX(day) FROM sales_sku_daily), '-35 days')) ORDER BY b.branch_id").fetchall()
    return [int(r[0]) for r in rows]


def _has_app_user(con) -> bool:
    cols = {r[1] for r in con.execute("PRAGMA table_info(app_user)")}
    return "branch" in cols


def targets(con, branch_id: int, now: datetime = None) -> list:
    """[(round, delivery date)] the branch page can ask for now: every delivery still open in next_deliveries' horizon, plus the
    one each round opens on (pick_delivery of that round: its first open delivery, else its first) - what /order/plan answers
    without `delivery`, with or without `round`."""
    from . import forecast as _fc
    now = now or now_bkk()
    items = _fc.next_deliveries(con, branch_id, now.date(), now=now)
    out = []
    for x in items:
        if x["open"]:
            out.append((x["round"], x["delivery"]))
    for rnd in ("1", "2", "salmon"):
        p = _fc.pick_delivery([x for x in items if x["round"] == rnd])
        if p:
            out.append((p["round"], p["delivery"]))
    p = _fc.pick_delivery(items)
    if p:
        out.append((p["round"], p["delivery"]))
    seen, uniq = set(), []
    for t in out:
        if t not in seen:
            seen.add(t)
            uniq.append((t[0], date.fromisoformat(t[1])))
    return uniq


def mark_live() -> None:
    """A bom-center request is being served (routers/bom_center._ensure): the worker waits for QUIET_S without one."""
    global _last_live
    if not getattr(_tls, "bg", False):
        _last_live = _time.monotonic()


def _wait_for_live() -> None:
    """Live requests first: wait while a live plan is computed or a bom-center request came in the last QUIET_S seconds - at most
    LIVE_WAIT_MAX, so a long busy spell still lets one precomputed plan through now and then."""
    t0 = _time.monotonic()
    while _time.monotonic() - t0 < LIVE_WAIT_MAX:
        if _live <= 0 and _time.monotonic() - _last_live >= QUIET_S:
            return
        _time.sleep(0.1)


def run_pass(branch_ids=None, trigger: str = "manual", get_db=None, now: datetime = None, stop_on_full=False) -> dict:
    """Compute the plans of these branches (None = every ordering branch), one at a time, through orders.plan with the page's
    defaults. Synchronous (the worker and the tests call it). Returns the run record; a full pass also updates status()."""
    from . import orders as _or
    from .forecast import _loaded
    get_db = get_db or _loaded("core").get_db
    full = branch_ids is None
    t0 = _time.monotonic()
    start = now_bkk()
    rec = {"running": True, "trigger": trigger, "scope": "all" if full else "branches", "last_start": _iso(start),
           "branches": 0, "plans": 0, "failures": []}
    if full:
        _status.update(rec)
    prev_bg = getattr(_tls, "bg", False)
    _tls.bg = True
    aborted = False
    try:
        if full:
            with get_db() as con:
                branch_ids = ordering_branches(con)
        for bid in branch_ids:
            if stop_on_full and _pending_all is not None:
                aborted = True                         # a chain-wide change arrived: what is left would be stale - start over
                break
            try:
                with get_db() as con:
                    tg = targets(con, bid, now)
            except Exception as e:  # noqa: BLE001 - one branch never stops the pass
                rec["failures"].append({"branch_id": bid, "round": None, "delivery": None, "error": _err(e)})
                continue
            rec["branches"] += 1
            for rnd, dlv in tg:
                _wait_for_live()
                try:
                    with get_db() as con:
                        _or.plan(con, bid, rnd, dlv, now=now)
                        if con.in_transaction:
                            con.rollback()
                    rec["plans"] += 1
                except ValueError:
                    rec["plans"] += 1                  # a plan the page itself refuses (e.g. a stored buffer out of range): nothing to keep
                except Exception as e:  # noqa: BLE001
                    rec["failures"].append({"branch_id": bid, "round": rnd, "delivery": dlv.isoformat(), "error": _err(e)})
    finally:
        _tls.bg = prev_bg
    rec.update(running=False, last_end=_iso(now_bkk()), duration_s=round(_time.monotonic() - t0, 1),
               result="aborted" if aborted else ("failed" if rec["failures"] else "ok"), store=store_info())
    rec["failures"] = rec["failures"][:20]
    if full:
        global _last_full_day
        if not aborted:
            _last_full_day = start.date()
        _status.clear()
        _status.update(rec)
        if not aborted:
            _save_status(get_db, rec)
    return rec


def _err(e) -> str:
    return f"{type(e).__name__}: {e}"[:200]


def _iso(dt) -> str:
    return dt.astimezone(BKK).isoformat(timespec="seconds")


def _save_status(get_db, rec: dict) -> None:
    try:
        with get_db() as con:
            if con.in_transaction:
                con.rollback()
            con.execute("INSERT OR REPLACE INTO app_settings(key, value) VALUES (?, ?)",
                        (STATUS_KEY, json.dumps(rec, ensure_ascii=False)))
            con.commit()
    except Exception as e:  # noqa: BLE001 - the status is best-effort
        print(f"[plan-precompute] could not save the status: {e}")


def status() -> dict:
    """The last full pass (or the one running), the store and what is pending."""
    with _cv:
        pend = {"all": _pending_all, "branches": len(_pending)}
    return {**_status, "enabled": _enabled, "pending": pend, "store": store_info()}


def stored_status(con) -> dict:
    """The status saved by the last finished full pass (what a watchdog in another process reads)."""
    try:
        r = con.execute("SELECT value FROM app_settings WHERE key=?", (STATUS_KEY,)).fetchone()
        return json.loads(r[0]) if r and r[0] else {}
    except Exception:  # noqa: BLE001
        return {}


# ── the worker ────────────────────────────────────────────────────────────────────────────────────────────────────
def schedule(branch_id=None, reason: str = "change", delay: float = None) -> None:
    """Ask the worker for a pass (debounced): every branch (None) or one. No-op while the worker is not started (tests, tools)."""
    global _pending_all, _due
    if not _enabled:
        return
    with _cv:
        if branch_id is None:
            _pending_all = reason if _pending_all is None or reason == "swap" else _pending_all
            _pending.clear()
            d = DELAY_ALL if delay is None else delay
        else:
            if _pending_all is None:
                _pending[int(branch_id)] = reason
            d = DELAY_BRANCH if delay is None else delay
        _due = max(_due, _time.monotonic() + d)
        _cv.notify()


def start(get_db=None, initial_delay: float = 20.0) -> bool:
    """Start the worker thread (main's startup; never under pytest). OSHINEI_PLAN_PRECOMPUTE=0 turns it off."""
    global _thread, _enabled
    if os.environ.get("OSHINEI_PLAN_PRECOMPUTE", "1").strip() in ("0", "false", "off"):
        return False
    if _thread is not None and _thread.is_alive():
        return True
    from .forecast import _loaded
    core = _loaded("core")
    install(core)
    _enabled = True
    _thread = threading.Thread(target=_loop, args=(get_db or core.get_db,), daemon=True, name="plan-precompute")
    _thread.start()
    schedule(None, "startup", delay=initial_delay)
    return True


def _loop(get_db) -> None:
    global _pending_all
    while True:
        with _cv:
            while _pending_all is None and not _pending:
                _cv.wait(timeout=60)
                _daily_check()
            wait = _due - _time.monotonic()
            if wait > 0:
                _cv.wait(timeout=wait)
                continue
            reason_all, branches = _pending_all, dict(_pending)
            _pending_all = None
            _pending.clear()
        try:
            if reason_all is not None:
                rec = run_pass(None, trigger=reason_all, get_db=get_db, stop_on_full=True)
                print(f"[plan-precompute] {reason_all}: {rec['result']} · {rec['branches']} branches · {rec['plans']} plans"
                      f" · {rec['duration_s']} s · {rec['store']['entries']} stored ({rec['store']['mb']} MB)")
            elif branches:
                run_pass(sorted(branches), trigger="branch", get_db=get_db)
        except Exception:  # noqa: BLE001 - the worker never dies
            traceback.print_exc()


def _daily_check() -> None:
    """Called with _cv held: a new Bangkok day after DAILY_HOUR with no full pass yet that day -> one pass."""
    global _pending_all, _due
    now = now_bkk()
    if now.hour >= DAILY_HOUR and _last_full_day != now.date() and not _status.get("running") and _pending_all is None:
        _pending_all = "daily"
        _due = _time.monotonic()
