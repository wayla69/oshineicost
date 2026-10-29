# -*- coding: utf-8 -*-
"""The precomputed order plans (bom_center/precompute.py, owner 2026-10-29 "ทำคำนวณล่วงหน้า").

The promises:
  * a plan served from the precomputed store is byte-for-byte the plan computed live (several branches, every round);
  * the pass computes exactly what the branch page asks for: every open delivery and the one each round opens on;
  * a write scoped to one branch (a receipt, a count, the calendar) drops that branch only; a chain-wide input (a price, a setting)
    drops all; the nav-badge flush every slip save makes drops nothing (it used to empty every plan at the cutoff hour);
  * a pass records its status (and a failure) where the cloud-jobs page and the watchdog read it.
"""
import contextlib
import inspect
import json
from datetime import date
from datetime import datetime, timedelta

import pytest

import core
from bom_center import forecast as F
from bom_center import orders as OR
from bom_center import precompute as PC
from bom_center.clock import BKK
from test_bom_center_s4b import PATTERN, con  # noqa: F401  (sales Sep-Oct 2026 at branch 1)

pytestmark = pytest.mark.usefixtures("recipe_mode")

NOW = datetime(2026, 10, 5, 8, 0, tzinfo=BKK)          # Monday morning: round 1 (Wed) still open until 13:15


@pytest.fixture
def pre(con, monkeypatch):  # noqa: F811
    """Two branches with sales (branch 2 sells twice as much), the caches on, a counting suggest()."""
    from datetime import date
    d0 = date(2026, 9, 1)
    rows = []
    for i in range(35):
        d = d0 + timedelta(days=i)
        q = PATTERN[(d.weekday() + 1) % 7] * 2
        rows += [(2, d.isoformat(), 10, q, 0.0), (2, d.isoformat(), 11, q, 0.0)]
    con.executemany("INSERT INTO sales_sku_daily VALUES(?,?,?,?,?)", rows)
    con.commit()
    monkeypatch.setattr(F, "USAGE_CACHE_TTL", 600)
    monkeypatch.setattr(F, "PLAN_CACHE_TTL", 120)
    F.clear_usage_cache()
    calls = []
    real = F.suggest

    def counting(*a, **kw):
        calls.append(a[1])
        return real(*a, **kw)
    monkeypatch.setattr(F, "suggest", counting)

    @contextlib.contextmanager
    def gdb():
        yield con
    yield con, calls, gdb
    F.clear_usage_cache()


def _live_cache_off():
    with F._plan_lock:
        F._plan_cache.clear()          # the 120 s plan cache: so what answers is the precomputed store


def test_the_pass_computes_every_ordering_branch_and_the_store_answers(pre):
    con, calls, gdb = pre
    rec = PC.run_pass(None, trigger="test", get_db=gdb, now=NOW)
    assert rec["result"] == "ok" and rec["branches"] == 2 and rec["plans"] >= 6 and not rec["failures"]
    n = len(calls)
    assert n == rec["plans"]                                     # one suggestion per (branch, round, delivery)
    _live_cache_off()
    for bid in (1, 2):
        for rnd, dlv in PC.targets(con, bid, NOW):
            OR.plan(con, bid, rnd, dlv, now=NOW)
    assert len(calls) == n                                       # every one of them a lookup


def test_a_precomputed_plan_is_byte_identical_to_the_live_one(pre, monkeypatch):
    con, calls, gdb = pre
    PC.run_pass(None, get_db=gdb, now=NOW)
    pairs = [(bid, rnd, dlv) for bid in (1, 2) for rnd, dlv in PC.targets(con, bid, NOW)]
    assert {p[1] for p in pairs} == {"1", "2", "salmon"}
    _live_cache_off()
    n = len(calls)
    stored = [json.dumps(OR.plan(con, *p, now=NOW), ensure_ascii=False) for p in pairs]
    assert len(calls) == n
    monkeypatch.setattr(F, "USAGE_CACHE_TTL", 0)                 # every cache off: computed from scratch
    live = [json.dumps(OR.plan(con, *p, now=NOW), ensure_ascii=False) for p in pairs]
    assert len(calls) == n + len(pairs)
    assert stored == live
    assert any(json.loads(s)["rows"] for s in stored)            # real plans, not empty ones


def test_the_targets_are_what_the_page_asks_for(pre):
    con, _, _ = pre
    tg = PC.targets(con, 1, NOW)
    items = F.next_deliveries(con, 1, NOW.date(), now=NOW)
    page = F.pick_delivery(items)                                    # /order/plan with neither round nor delivery
    assert (page["round"], page["delivery"]) in [(r, d.isoformat()) for r, d in tg]
    for rnd in ("1", "2", "salmon"):                                 # /order/plan?round=
        p = F.pick_delivery([x for x in items if x["round"] == rnd])
        assert (p["round"], p["delivery"]) in [(r, d.isoformat()) for r, d in tg]
    picks = {(F.pick_delivery([x for x in items if x["round"] == r]) or {}).get("delivery") for r in ("1", "2", "salmon")}
    for x in items:                                                  # beyond those picks, only open deliveries are computed
        if (x["round"], date.fromisoformat(x["delivery"])) in tg and x["delivery"] not in picks:
            assert x["open"]
    assert len(tg) == len(set(tg))


def _hits(con, calls, bid, rnd, dlv):
    n = len(calls)
    OR.plan(con, bid, rnd, dlv, now=NOW)
    return len(calls) == n


def test_scoped_and_chain_wide_flushes(pre):
    con, calls, gdb = pre
    PC.run_pass(None, get_db=gdb, now=NOW)
    t1, t2 = PC.targets(con, 1, NOW)[0], PC.targets(con, 2, NOW)[0]
    _live_cache_off()
    core.invalidate_cache("bc_nav_badges_for")                       # every slip save: changes no plan
    assert _hits(con, calls, 1, *t1) and _hits(con, calls, 2, *t2)
    with PC.flush_scope(1):                                          # a receipt / count of branch 1
        core.invalidate_cache("bc_")
    _live_cache_off()
    assert not _hits(con, calls, 1, *t1)
    assert _hits(con, calls, 2, *t2)
    with PC.flush_scope():                                           # the hub's "keyed" mark: no plan reads it
        core.invalidate_cache("bc_")
    _live_cache_off()
    assert _hits(con, calls, 1, *t1) and _hits(con, calls, 2, *t2)
    core.invalidate_cache("bc_")                                     # a price, a setting, the mode ...: everyone
    _live_cache_off()
    assert not _hits(con, calls, 1, *t1) and not _hits(con, calls, 2, *t2)
    PC.bump(2, "calendar")                                           # the delivery calendar of branch 2
    _live_cache_off()
    assert _hits(con, calls, 1, *t1) and not _hits(con, calls, 2, *t2)
    F.clear_slip_cache(1, benchmark=False)                           # branch 1 confirmed a slip
    _live_cache_off()
    assert not _hits(con, calls, 1, *t1) and _hits(con, calls, 2, *t2)
    core.invalidate_cache()                                          # a data swap / full flush
    _live_cache_off()
    assert not _hits(con, calls, 1, *t1) and not _hits(con, calls, 2, *t2)


def test_a_plan_built_on_other_branches_slips_is_a_miss_when_they_move(pre, monkeypatch):
    con, calls, gdb = pre
    t2 = PC.targets(con, 2, NOW)[0]
    real = F.suggest

    def bench(*a, **kw):
        out = real(*a, **kw)
        if a[1] == 2:
            out["ref_mode"] = {"active": True, "benchmark": True}
        return out
    monkeypatch.setattr(F, "suggest", bench)
    OR.plan(con, 2, *t2, now=NOW)
    _live_cache_off()
    assert _hits_plain(con, 2, t2, monkeypatch)
    F.clear_slip_cache(1, benchmark=False)                           # another branch's slips moved
    _live_cache_off()
    assert not _hits_plain(con, 2, t2, monkeypatch)


def _hits_plain(con, bid, t, monkeypatch):
    before = PC.store_info()["hits"]
    OR.plan(con, bid, *t, now=NOW)
    return PC.store_info()["hits"] > before


def test_a_write_during_the_computation_is_not_stored(pre, monkeypatch):
    con, calls, gdb = pre
    t1 = PC.targets(con, 1, NOW)[0]
    real = F.suggest

    def racing(*a, **kw):
        out = real(*a, **kw)
        PC.bump(None, "input")                                       # a price changed while the plan was computed
        return out
    monkeypatch.setattr(F, "suggest", racing)
    OR.plan(con, 1, *t1, now=NOW)
    assert PC.store_info()["entries"] == 0


def test_a_failed_plan_is_recorded_and_saved_for_the_watchdog(pre, monkeypatch):
    con, calls, gdb = pre
    real = OR.plan

    def broken(c, bid, *a, **kw):
        if bid == 2:
            raise RuntimeError("boom")
        return real(c, bid, *a, **kw)
    monkeypatch.setattr(OR, "plan", broken)
    rec = PC.run_pass(None, trigger="test", get_db=gdb, now=NOW)
    assert rec["result"] == "failed" and rec["failures"] and all(f["branch_id"] == 2 for f in rec["failures"])
    assert "boom" in rec["failures"][0]["error"]
    saved = PC.stored_status(con)
    assert saved["result"] == "failed" and saved["branches"] == 2 and saved["trigger"] == "test"
    st = PC.status()
    assert st["result"] == "failed" and st["running"] is False and "store" in st and "pending" in st


def test_the_cloud_jobs_overview_and_the_watchdog_read_it(pre, monkeypatch):
    import cloud_jobs as CJ
    con, calls, gdb = pre
    monkeypatch.setattr(OR, "plan", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    PC.run_pass(None, get_db=gdb, now=NOW)

    @contextlib.contextmanager
    def db():
        yield con
    view = CJ.plan_precompute_view(db)
    assert view["label"] and view["result"] == "failed"
    assert CJ.read_status("plan_precompute", db)["result"] == "failed"


def test_schedule_is_a_no_op_until_the_worker_starts():
    assert PC._enabled is False                                      # main starts it; never under pytest
    PC.schedule(None, "x")
    assert PC._pending_all is None and not PC._pending


def test_the_write_endpoints_scope_their_flush_and_ask_for_a_recompute():
    from routers import bom_center as RB
    for fn in (RB.bc_order_receive, RB.bc_receive_import, RB.bc_stock_save, RB.bc_stock_count_import, RB.bc_claim_create):
        assert "_pc.flush_scope(bid)" in inspect.getsource(fn), fn.__name__
    assert "_pc.flush_scope()" in inspect.getsource(RB.bc_hub_keyed)
    assert '_pc.bump(' in inspect.getsource(RB.bc_order_calendar_save)
    assert '_pc.bump(' in inspect.getsource(RB.bc_order_calendar_import)
    assert '_pc.schedule(bid, "order")' in inspect.getsource(RB.bc_order_save)
    assert '_pc.schedule(row[0], "order")' in inspect.getsource(RB.bc_hub_process)
    assert PC.on_flush in core._flush_hooks
