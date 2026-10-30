"""PR-LK (2026-10): 30 branches at the cutoff hour without "database is locked", and a faster order page.

* the load script counts server 5xx and lock errors (a lock error reaches the client as a bare 500, so the body check alone read 0);
* schema.ensure runs its DDL once per connection, not on every request;
* the plan cache (forecast.suggest_cached) and the stock-estimate memo: when they hit, when they must not;
* the slip caches are dropped per branch, and a draft slip drops nothing;
* the bom-center writes go through one gate, and the order save plans before taking it;
* the phone fixes on แนวโน้ม (an unused default axis, pickers that were never filled).
"""
import importlib.util
import inspect
import sqlite3
import sys
import threading
import time
from datetime import date
from pathlib import Path

import pytest

from bom_center import forecast as F
from bom_center import orders as OR
from bom_center import schema as SC
from bom_center import stock as ST
from test_bom_center_batch2 import _fn, _node, needs_node

ROOT = Path(__file__).resolve().parent.parent


def _src(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


def _load_script():
    spec = importlib.util.spec_from_file_location("lk_load_branches", ROOT / "scripts" / "load_branches.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["lk_load_branches"] = mod
    spec.loader.exec_module(mod)
    return mod


# ── the load script ──────────────────────────────────────────────────────────────────────────────────────────────
def test_a_bare_500_is_counted_as_a_server_error_and_the_server_log_lock_count_is_the_lock_total():
    L = _load_script()
    st = L.Stats()
    st.add("POST order/save (submit)", 500, 1.0, "Internal Server Error")          # what the client sees of a lock error
    st.add("GET order/plan", 200, 0.1)
    assert len(st.flags["locked"]) == 0                                              # the body says nothing about a lock ...
    assert len(st.flags["5xx"]) == 1
    e = L.server_errors(st.flags, {"locked": 2, "tracebacks": 3})                    # ... the server's own log does
    assert e["http_5xx"] == 1 and e["locked_in_server_log"] == 2 and e["lock_errors"] == 2 and e["total"] == 3
    assert e["tracebacks_in_server_log"] == 3


def test_a_clean_run_has_no_server_errors_and_a_lock_in_a_body_counts_once():
    L = _load_script()
    st = L.Stats()
    st.add("GET todo", 200, 0.1)
    assert L.server_errors(st.flags, None)["total"] == 0
    st.add("POST x", 500, 0.1, "database is locked")
    e = L.server_errors(st.flags, {"locked": 1, "tracebacks": 1})
    assert e["locked_in_bodies"] == 1 and e["lock_errors"] == 1 and e["http_5xx"] == 1 and e["total"] == 2


def test_the_script_reports_server_errors_and_can_fail_the_run():
    src = _src("scripts/load_branches.py")
    assert "SERVER ERRORS:" in src and '"server_errors": errs' in src
    assert "--strict" in src and "sys.exit(1)" in src


# ── schema.ensure ────────────────────────────────────────────────────────────────────────────────────────────────
def test_ensure_runs_its_ddl_once_per_connection_and_again_when_the_schema_moved(tmp_path, monkeypatch):
    db = tmp_path / "lk.db"
    con = sqlite3.connect(str(db))
    calls = []
    real = SC._ensure_schema
    monkeypatch.setattr(SC, "_ensure_schema", lambda c: (calls.append(1), real(c))[1])
    SC.ensure(con, commit=True)
    assert len(calls) == 1
    SC.ensure(con, commit=True)
    SC.ensure(con)
    assert len(calls) == 1                                                           # skipped: the schema is the one it just built
    con.execute("CREATE TABLE lk_other(x)")                                          # anyone changes the schema ...
    con.commit()
    SC.ensure(con)
    assert len(calls) == 2                                                           # ... and the next call checks again
    con.execute("DROP TABLE bc_audit")                                               # a table lost behind its back is rebuilt
    con.commit()
    SC.ensure(con, commit=True)
    assert len(calls) == 3
    assert con.execute("SELECT 1 FROM sqlite_master WHERE name='bc_audit'").fetchone()
    con.close()


def test_ensure_never_skips_an_in_memory_database_and_a_rolled_back_create_is_redone(tmp_path, monkeypatch):
    calls = []
    real = SC._ensure_schema
    monkeypatch.setattr(SC, "_ensure_schema", lambda c: (calls.append(1), real(c))[1])
    mem = sqlite3.connect(":memory:")
    SC.ensure(mem)
    SC.ensure(mem)
    assert len(calls) == 2
    mem.close()
    calls.clear()
    con = sqlite3.connect(str(tmp_path / "rb.db"))
    con.execute("BEGIN")
    SC.ensure(con)                                                                   # no commit: the caller rolls it back (a dry run)
    con.rollback()
    assert not con.execute("SELECT 1 FROM sqlite_master WHERE name='bc_order'").fetchone()
    SC.ensure(con, commit=True)
    assert len(calls) == 2 and con.execute("SELECT 1 FROM sqlite_master WHERE name='bc_order'").fetchone()
    con.close()


# ── the plan cache ───────────────────────────────────────────────────────────────────────────────────────────────
@pytest.fixture
def pc(tmp_path, monkeypatch):
    """A file database with the two tables suggest_cached reads, the cache switched on, and a counting stand-in for suggest()."""
    db = tmp_path / "pc.db"
    con = sqlite3.connect(str(db))
    con.execute("CREATE TABLE branches(branch_id INTEGER, name TEXT, branch_type TEXT, active INTEGER)")
    con.executemany("INSERT INTO branches VALUES(?,?,?,1)", [(1, "A", "master"), (2, "B", "master"), (3, "C", "franchise")])
    con.execute("CREATE TABLE bc_order(order_id INTEGER PRIMARY KEY, branch_id INT, status TEXT, submitted_at TEXT, processed_at TEXT,"
                " received_at TEXT)")
    con.commit()
    monkeypatch.setattr(F, "USAGE_CACHE_TTL", 600)
    monkeypatch.setattr(F, "PLAN_CACHE_TTL", 120)
    F.clear_usage_cache()
    calls = []

    def fake(c, branch_id, rnd, order_day, **kw):
        calls.append(branch_id)
        out = {"rows": [{"code": "X", "need": float(len(calls))}], "branch_id": branch_id}
        if branch_id == 3:                                     # the franchise branch is planned from the benchmark (A and B's slips)
            out["ref_mode"] = {"active": True, "benchmark": True}
        return out
    monkeypatch.setattr(F, "suggest", fake)
    yield con, calls
    con.close()
    F.clear_usage_cache()


def _plan(con, bid=1, stock=None, **kw):
    return F.suggest_cached(con, bid, "1", date(2026, 10, 24), as_of=date(2026, 10, 22), stock=stock if stock is not None else {"qty": {"X": 1.0}},
                            uplift_pct=kw.pop("uplift_pct", 0.0), delivery=date(2026, 10, 28), ref_branch=None,
                            expected_sales=kw.pop("expected_sales", None))


def test_the_same_request_is_answered_from_the_cache_and_each_caller_gets_its_own_copy(pc):
    con, calls = pc
    a = _plan(con)
    b = _plan(con)
    assert len(calls) == 1 and a == b
    a["rows"][0]["need"] = -1                                                       # the plan changes its copy in place
    assert _plan(con)["rows"][0]["need"] == b["rows"][0]["need"] and len(calls) == 1


def test_anything_the_request_or_its_inputs_changed_is_a_miss(pc):
    con, calls = pc
    _plan(con)
    _plan(con, uplift_pct=10.0)
    _plan(con, expected_sales=1000.0)
    _plan(con, stock={"qty": {"X": 2.0}})                                            # the stock position moved (a count, a slip, a receipt)
    _plan(con, bid=2)
    assert len(calls) == 5
    con.execute("INSERT INTO bc_order VALUES(1, 1, 'processed', '2026-10-20T01:00:00', '2026-10-21T01:00:00', NULL)")
    con.commit()
    _plan(con)                                                                       # a shipment the branch learns from (short ship)
    assert len(calls) == 6
    con.execute("INSERT INTO bc_order VALUES(2, 1, 'confirmed', NULL, NULL, NULL)")  # a DRAFT order being saved changes nothing it reads
    con.commit()
    _plan(con)
    assert len(calls) == 6


def test_a_connection_with_a_transaction_open_neither_reads_nor_fills_the_cache(pc):
    con, calls = pc
    con.execute("INSERT INTO bc_order VALUES(9, 1, 'confirmed', NULL, NULL, NULL)")             # uncommitted: an import preview
    _plan(con)
    _plan(con)
    assert len(calls) == 2
    con.commit()
    _plan(con)
    _plan(con)
    assert len(calls) == 3


def test_a_slip_drops_only_its_own_branch_and_the_company_branches_it_feeds(pc):
    con, calls = pc
    _plan(con, bid=3)                                                                # a franchise branch: reads A and B (the benchmark)
    _plan(con, bid=1)
    assert len(calls) == 2
    F.clear_slip_cache(3, benchmark=False)                                           # C confirmed a slip: C is stale, A is not
    _plan(con, bid=1)
    assert len(calls) == 2
    _plan(con, bid=3)
    assert len(calls) == 3
    F.clear_slip_cache(2)                                                            # B (a company branch) confirmed: everyone's benchmark moved
    _plan(con, bid=3)
    assert len(calls) == 4


def test_clearing_the_usage_cache_and_the_core_flush_drop_every_stored_plan(pc):
    import core
    con, calls = pc
    _plan(con)
    F.clear_usage_cache()
    _plan(con)
    assert len(calls) == 2
    core.invalidate_cache("bc_")                                                     # any bom-center write moves core's generation
    _plan(con)
    assert len(calls) == 3


def test_the_cache_is_off_when_the_usage_cache_is_off_and_after_its_ttl(pc, monkeypatch):
    con, calls = pc
    monkeypatch.setattr(F, "USAGE_CACHE_TTL", 0)
    _plan(con)
    _plan(con)
    assert len(calls) == 2
    monkeypatch.setattr(F, "USAGE_CACHE_TTL", 600)
    monkeypatch.setattr(F, "PLAN_CACHE_TTL", 0.05)
    _plan(con)
    _plan(con)
    assert len(calls) == 3
    time.sleep(0.08)
    _plan(con)
    assert len(calls) == 3                                                           # past the TTL: the precomputed store (no TTL) answers
    from bom_center import precompute as PC
    PC.clear()
    time.sleep(0.08)
    _plan(con)
    assert len(calls) == 4


def test_an_in_memory_database_is_never_cached():
    con = sqlite3.connect(":memory:")
    assert F._gen_base(con) is None
    assert F.memo_get(con, 1, "x", ())[0] is None
    con.close()


def test_the_stock_estimate_is_kept_per_branch_and_dropped_with_its_slips(pc, monkeypatch):
    con, calls = pc
    n = []
    monkeypatch.setattr(ST, "_estimated_from_slips_uncached", lambda c, b, d: (n.append(1), (["2026-10-20"], {"X": 1.5}))[1])
    for _ in range(3):
        got, est = ST._estimated_from_slips(con, 1, ["2026-10-20"])
    assert n == [1] and got == ["2026-10-20"] and est == {"X": 1.5}
    est["X"] = 99                                                                    # the caller's copy
    assert ST._estimated_from_slips(con, 1, ["2026-10-20"])[1] == {"X": 1.5}
    ST._estimated_from_slips(con, 2, ["2026-10-20"])
    assert len(n) == 2
    F.clear_slip_cache(1, benchmark=False)
    ST._estimated_from_slips(con, 1, ["2026-10-20"])
    ST._estimated_from_slips(con, 2, ["2026-10-20"])
    assert len(n) == 3                                                               # only branch 1 was recomputed


def test_clear_slip_cache_with_no_branch_still_clears_everything(pc):
    con, calls = pc
    F._slip_cache[("p", 1, "a", "b", "slips", 0, 0)] = (time.monotonic(), ({}, [], {}))
    F._slip_cache[("p", 2, "a", "b", "slips", 0, 0)] = (time.monotonic(), ({}, [], {}))
    F.clear_slip_cache(1, benchmark=False)
    assert [k[1] for k in F._slip_cache] == [2]
    F.clear_slip_cache()
    assert not F._slip_cache


# ── the slip endpoints ───────────────────────────────────────────────────────────────────────────────────────────
def test_a_draft_slip_clears_no_cache_and_a_confirmed_one_only_its_branch():
    from routers import bom_center as RB
    save = inspect.getsource(RB.bc_issue_save)
    assert 'out.get("status") == "confirmed"' in save and "_fc.clear_slip_cache(bid)" in save
    assert "clear_slip_cache(bid, benchmark=False)" in inspect.getsource(RB.bc_issue_blank)       # a read: no benchmark flush
    assert "_fc.clear_slip_cache(bid)" in inspect.getsource(RB.bc_issue_append)
    assert "_fc.clear_slip_cache()" in inspect.getsource(RB.bc_issue_reopen)                      # the branch is not known here: everything
    assert "_fc.clear_slip_cache()" in inspect.getsource(RB.bc_issue_import)                      # the file import: everything, as before


# ── the write gate ───────────────────────────────────────────────────────────────────────────────────────────────
class _Con:
    commits = rollbacks = 0

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


def test_bom_center_writes_run_one_at_a_time_and_the_gate_is_released_after_an_error():
    from fastapi import HTTPException
    from routers import bom_center as RB
    inside, peak, lock = [0], [0], threading.Lock()

    def work():
        with lock:
            inside[0] += 1
            peak[0] = max(peak[0], inside[0])
        time.sleep(0.02)
        with lock:
            inside[0] -= 1
        return "ok"
    con = _Con()
    ts = [threading.Thread(target=lambda: RB._write(con, work)) for _ in range(8)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert peak[0] == 1 and con.commits == 8
    with pytest.raises(HTTPException):
        RB._write(con, lambda: (_ for _ in ()).throw(ValueError("x")))
    with pytest.raises(RuntimeError):
        RB._write(con, lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    assert con.rollbacks == 2
    done = []
    t = threading.Thread(target=lambda: done.append(RB._write(con, lambda: 1)))
    t.start()
    t.join(2)
    assert done == [1]                                                               # not left locked by the errors
    assert RB._write(con, lambda: RB._write(con, lambda: 7)) == 7                    # re-entrant


def test_the_order_save_plans_before_the_gate_and_saves_inside_it():
    from routers import bom_center as RB
    src = inspect.getsource(RB.bc_order_save)
    assert src.index("_or.prepare_flow(") < src.index("def go():") < src.index("_or.save(") < src.index("_write(con, go)")
    assert "save_flow" not in src
    flow = inspect.getsource(OR.save_flow)
    assert "prepare_flow(" in flow and "return save(" in flow                                       # the other callers (file import) unchanged


# ── the phone fixes on แนวโน้ม ───────────────────────────────────────────────────────────────────────────────────
CORE, TABS = _src("js/01-core.js"), _src("js/03-tabs-main.js")


@needs_node
def test_a_phone_chart_with_its_own_value_axes_gets_no_extra_default_axis():
    tune = _fn(CORE, "_phoneChartTune")
    pre = "function _x(){}\nconst __wrap={style:{height:'240px'}, parentElement:null};\n" + tune
    cfg = ("{type:'line', data:{datasets:[{},{}]}, options:{scales:{x:{}, yRev:{position:'left'}, yCust:{position:'right'}}}}")
    out = _node(pre, f"(()=>{{const c={cfg}; _phoneChartTune(c,{{parentElement:__wrap}}); return Object.keys(c.options.scales);}})()")
    assert out == ["x", "yRev", "yCust"]                                                        # was [..., 'y'] - 0 / 0.5 / 1.0 beside the plot
    out = _node(pre, "(()=>{const c={type:'bar', data:{datasets:[{}]}, options:{}}; _phoneChartTune(c,{parentElement:__wrap}); return Object.keys(c.options.scales).sort();})()")
    assert out == ["x", "y"]                                                                    # a chart that names none still gets both
    out = _node(pre, "(()=>{const c={type:'bar', data:{datasets:[{}]}, options:{scales:{x:{}}}}; _phoneChartTune(c,{parentElement:__wrap}); return Object.keys(c.options.scales).sort();})()")
    assert out == ["x", "y"]


@needs_node
def test_the_trend_page_fills_its_month_pickers_itself_and_loads_with_the_defaults():
    pre = f"""
const els = {{cmpMonth: {{value: '', options: [], innerHTML: '', appendChild(o){{ this.options.push(o); if (!this.value) this._first = o.value; }},
              set v(x){{}} }}, cmpVs: {{value: '', options: [], innerHTML: '', appendChild(o){{ this.options.push(o); }} }}}};
for (const k in els) {{ Object.defineProperty(els[k], 'value', {{ get(){{ return this._v || ''; }}, set(x){{ this._v = x; }} }}); }}
global.document = {{ getElementById: id => els[id] || null, createElement: () => ({{}}) }};
let allMonths = ['2026-08', '2026-09', '2026-10'];
let calls = [];
function getBranch() {{ return ''; }}
const API = '';
async function api(u) {{ calls.push(u); throw new Error('stop'); }}
function fmtMonth(m) {{ return m; }}
{_fn(TABS, "populateCmpDropdowns")}
{_fn(TABS, "loadTrend")}
"""
    expr = "(async()=>{ try { await loadTrend(); } catch (e) {} return {m: els.cmpMonth.value, v: els.cmpVs.value, first: calls[0] || ''}; })()"
    out = _run_async(pre, expr)
    assert out["m"] == "2026-10" and out["v"] == "2026-09"                                       # latest month vs the one before
    assert "month=2026-10" in out["first"] and "vs=2026-09" in out["first"]


def _run_async(prelude, expr):
    import json
    import subprocess
    from test_bom_center_batch2 import NODE
    code = prelude + f"\n{expr}.then(r => console.log(JSON.stringify(r)));"
    out = subprocess.run([NODE, "-e", code], capture_output=True, text=True, encoding="utf-8", check=True).stdout
    return json.loads(out)


def test_the_desktop_boot_fills_the_pickers_of_the_fragments_it_just_loaded():
    boot = TABS[TABS.index("function ensureDesktopBooted"):TABS.index("function ensureDesktopBooted") + 1500]
    assert boot.index("await loadTabFragments();") < boot.index("populateCmpDropdowns()") < boot.index("applyPermissions()")


# ── the price book, the company history, the fast JSON ───────────────────────────────────────────────────────────
def test_the_price_book_answers_exactly_what_the_sql_lookup_answers_and_sees_a_changed_table(tmp_path, monkeypatch):
    from bom_center import prices as PR
    con = sqlite3.connect(str(tmp_path / "pb.db"))
    con.execute("CREATE TABLE bc_ingredient_price(ingredient_code TEXT, month TEXT, price_per_base REAL, source TEXT,"
                " price_age_months INTEGER DEFAULT 0, imported_at TEXT, PRIMARY KEY(ingredient_code, month))")
    con.executemany("INSERT INTO bc_ingredient_price(ingredient_code, month, price_per_base, source, price_age_months) VALUES(?,?,?,?,?)",
                    [("A", "2026-07", 1.0, "purchase_avg", 0), ("A", "2026-09", 2.0, "carry_forward", 2),
                     ("B", "2026-10", 5.0, "list_ref", 0), ("B", "2026-11", 7.0, "purchase_avg", 0), ("C", "2026-12", 9.0, "purchase_avg", 0)])
    con.commit()
    cases = [(c, m) for c in ("A", "B", "C", "Z") for m in ("2026-06", "2026-07", "2026-08", "2026-10", "2026-11", "2027-01")]
    monkeypatch.setattr(F, "USAGE_CACHE_TTL", 0)
    want = {k: PR.price_for(con, *k) for k in cases}                                   # caching off: the SQL path
    monkeypatch.setattr(F, "USAGE_CACHE_TTL", 600)
    monkeypatch.setattr(F, "PLAN_CACHE_TTL", 120)
    F.clear_usage_cache()
    assert PR._price_book(con) is not None
    assert {k: PR.price_for(con, *k) for k in cases} == want
    con.execute("UPDATE bc_ingredient_price SET price_per_base=3.0 WHERE ingredient_code='A' AND month='2026-09'")
    assert PR.price_for(con, "A", "2026-09")[0] == 3.0                                 # a transaction is open: it sees its own row, no book
    con.commit()
    for ent in PR._BOOK.values():
        ent[1] = 0.0                                                                   # the once-a-second fingerprint check is due
    assert PR.price_for(con, "A", "2026-09")[0] == 3.0                                 # ... and the book is rebuilt
    con.close()
    F.clear_usage_cache()


def test_the_company_history_is_kept_between_calls_and_dropped_with_a_branchs_slips(pc, monkeypatch):
    from bom_center import issue_reports as IR
    con, calls = pc
    n = []
    monkeypatch.setattr(IR, "_hist", lambda c, ids, lo, hi: (n.append(1), {i: {"x": i} for i in ids})[1])
    lo, hi = date(2026, 8, 1), date(2026, 10, 20)
    for _ in range(3):
        IR._company_hist(con, lo, hi, {})
    assert len(n) == 1
    F.clear_slip_cache(3, benchmark=False)                                               # a franchise branch is not in the median
    IR._company_hist(con, lo, hi, {})
    assert len(n) == 1
    F.clear_slip_cache(1)                                                                # a company branch confirmed a slip
    IR._company_hist(con, lo, hi, {})
    assert len(n) == 2


def test_fast_json_renders_plain_payloads_and_leaves_the_rest_to_fastapi():
    from routers import bom_center as RB
    r = RB._fast_json({"a": [1, 2.5, None, "ก"]})
    assert r.body == '{"a":[1,2.5,null,"ก"]}'.encode("utf-8")
    d = {"when": date(2026, 10, 23)}
    assert RB._fast_json(d) is d
    n = {"x": float("nan")}
    assert RB._fast_json(n) is n


def test_the_change_bars_write_a_fall_beside_the_zero_line_not_past_its_tip():
    page = TABS[TABS.index("async function loadTrend"):]
    bar = page[page.index("chartCmpBar"):page.index("// Daily chart")]
    assert "align:  'right'," in bar and "'left'" not in bar          # a negative value's label ran into the category names on a phone
