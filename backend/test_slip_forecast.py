# -*- coding: utf-8 -*-
"""PR-S3 (owner 2026-10-22, docs/STOCK_FIRST_PLAN.md): in stock mode the order suggestion is forecast from the CONFIRMED issue slips.

The promises:
  * usage_history_slips: {code: {day: used + waste}} in base units from confirmed slips only (a draft is nobody's figure), the days
    that have a confirmed slip, the stats of the source; a line the units cannot convert is skipped and counted;
  * demand_source is the one selector: mode `full` -> the recipes exactly as before; mode `stock` -> the slips from 14 confirmed days
    in the window, below that NOTHING (never a fall back to the recipes);
  * O1 (ก): no branch has 14 days -> every row has no suggestion (`suggested` None) and carries the system stock and the last order;
    O1 (ข): another branch has -> the benchmark of the slip branches per ฿100,000 x the branch's own "ปกติ" sales, until it reaches 14;
    a branch without enough slips is no reference;
  * a missing day of the system stock is the branch's mean of that weekday over the 4 weeks before it (stock mode), the BoM of the
    sales (full mode);
  * the per-฿100,000 report, the plan and the backtest follow the selector; every slip write endpoint drops the slip cache.
"""
import sqlite3
from datetime import date, datetime, timedelta

import pytest

from bom_center import benchmark as BM
from bom_center import close_settings as CS
from bom_center import forecast as FC
from bom_center import orders as OR
from bom_center import per_sales as PS
from bom_center import stock as ST
from bom_center.clock import BKK
from test_bom_center_s4b import PATTERN, con  # noqa: F401  (branch 1: crab 45 g on sku 10, salmon 20 g on sku 11, 1 Sep - 5 Oct 2026)

CRAB, SALMON = "13-01-009", "02-01-001"
WED = date(2026, 10, 7)
NOW = datetime(2026, 10, 5, 8, 0, tzinfo=BKK)
AS_OF = date(2026, 10, 5)
D0 = date(2026, 9, 1)


@pytest.fixture(autouse=True)
def _flat_season(monkeypatch):
    monkeypatch.setattr(FC, "season_factor", lambda *a, **k: {"factor": 1.0, "mode": "off", "reason": ""})


def _days(a, b):
    return [a + timedelta(days=i) for i in range((b - a).days + 1)]


def _branch(c, bid, name):
    c.execute("INSERT OR REPLACE INTO branches(branch_id, name, branch_type, active) VALUES(?,?,'master',1)", (bid, name))


def _sell(c, bid, a=D0, b=AS_OF, q10=10, q11=10):
    """q10 plates of sku 10 (฿100) and q11 of sku 11 (฿200) every day: ฿3,000 a day at 10 + 10."""
    for d in _days(a, b):
        c.execute("INSERT INTO sales_sku_daily VALUES(?,?,10,?,?)", (bid, d.isoformat(), q10, q10 * 100))
        c.execute("INSERT INTO sales_sku_daily VALUES(?,?,11,?,?)", (bid, d.isoformat(), q11, q11 * 200))


def _slip(c, bid, day, lines, status="confirmed"):
    """A slip written straight into the tables: lines = {code: (used_base, waste_base)}; None in a base column = unconverted."""
    iso = day.isoformat() if isinstance(day, date) else day
    cur = c.execute("INSERT INTO bc_issue_slip(branch_id, slip_date, status, created_by, created_at, updated_at, confirmed_by,"
                    " confirmed_at) VALUES(?,?,?,'t','2026-10-01','2026-10-01',?,?)",
                    (bid, iso, status, "t" if status == "confirmed" else None, "2026-10-01" if status == "confirmed" else None))
    for code, (ub, wb) in lines.items():
        c.execute("INSERT INTO bc_issue_line(slip_id, ingredient_code, qty_used, qty_waste, unit, qty_used_base, qty_waste_base)"
                  " VALUES(?,?,?,?, 'กรัม', ?, ?)", (cur.lastrowid, code, 1 if ub != 0 else 0, 1 if wb else 0, ub, wb))
    return cur.lastrowid


def _slips(c, bid, a, b, crab=(450.0, 50.0)):
    for d in _days(a, b):
        _slip(c, bid, d, {CRAB: crab})


@pytest.fixture
def chain(con):  # noqa: F811
    """Branch 1 (the forecast fixture, sales to 5 Oct) plus two company branches 3 and 4 with the same sales; stock mode (no key)."""
    con.execute("UPDATE sales_sku_daily SET subtotal_ex_vat = qty * 100 WHERE sku_id=10")
    con.execute("UPDATE sales_sku_daily SET subtotal_ex_vat = qty * 200 WHERE sku_id=11")
    for bid in (3, 4):
        _branch(con, bid, f"Co{bid}")
        _sell(con, bid)
    con.commit()
    BM.set_benchmark(con, "t", [1, 3, 4])
    assert CS.mode(con) == "stock"
    return con


# ── 1 · the usage from the slips ─────────────────────────────────────────────────────────────
def test_slip_usage_is_used_plus_waste_of_confirmed_slips_only(chain):
    c = chain
    _slip(c, 3, "2026-09-10", {CRAB: (450.0, 50.0), SALMON: (None, None)})            # a salmon line the units cannot convert
    _slip(c, 3, "2026-09-11", {CRAB: (300.0, 0.0)})
    _slip(c, 3, "2026-09-12", {CRAB: (999.0, 1.0)}, status="draft")                    # a draft counts for nothing
    _slip(c, 3, "2026-08-31", {CRAB: (5.0, 0.0)})                                       # outside the window
    use, days, st = FC.usage_history_slips(c, 3, D0, AS_OF)
    assert days == ["2026-09-10", "2026-09-11"]
    assert use[CRAB] == {"2026-09-10": 500.0, "2026-09-11": 300.0}                      # used + waste, no yield gross-up
    assert SALMON not in use                                                             # skipped, and counted below
    assert st == {"source": "slips", "confirmed_days": 2, "window_days": 35, "missed": [], "missed_lines": 0, "via": {},
                  "unconverted_lines": 1}
    assert FC.usage_history_slips(c, 4, D0, AS_OF)[:2] == ({}, [])                      # another branch has none of it


# ── 2 · the selector ─────────────────────────────────────────────────────────────────────────
def test_selector_follows_the_mode_and_the_14_day_threshold(chain):
    c = chain
    _slips(c, 3, D0, D0 + timedelta(days=12))                                           # 13 confirmed days
    use, days, st = FC.demand_source(c, 3, D0, AS_OF, "2026-10")
    assert use == {} and len(days) == 13                                                # stock mode, 13 < 14: nothing, never the recipes
    assert st["source"] == "none" and st["confirmed_days"] == 13 and st["min_days"] == 14 and st["window_days"] == 35
    assert st["missed"] == [] and st["missed_lines"] == 0 and st["via"] == {}
    _slip(c, 3, D0 + timedelta(days=13), {CRAB: (450.0, 50.0)})                         # the 14th day
    use, days, st = FC.demand_source(c, 3, D0, AS_OF, "2026-10")
    assert st["source"] == "slips" and st["confirmed_days"] == 14 and len(days) == 14 and use[CRAB]["2026-09-14"] == 500.0
    assert FC.demand_source(c, 1, D0, AS_OF, "2026-10")[2]["source"] == "none"          # branch 1 sold for 35 days and has no slip
    CS.set_mode(c, "t", "full")                                                          # full: the recipes, as before
    use, days, st = FC.demand_source(c, 1, D0, AS_OF, "2026-10")
    ref = FC.usage_history(c, 1, D0, AS_OF, "2026-10")
    assert st["source"] == "recipes" and (use, days) == (ref[0], ref[1]) and len(days) == 35
    assert use[CRAB]["2026-09-01"] > 0


def test_full_mode_reads_the_recipes_whatever_the_slips_say(chain):
    c = chain
    CS.set_mode(c, "t", "full")
    _slips(c, 1, D0, AS_OF, crab=(1.0, 0.0))
    out = FC.suggest(c, 1, "1", date(2026, 10, 6), season="off", as_of=AS_OF)
    crab = {r["code"]: r for r in out["rows"]}[CRAB]
    assert crab["forecast"] == pytest.approx(42 * 45, rel=1e-6)                         # the s4b figure: sales x recipe
    assert out["source"] == {"kind": "recipes", "confirmed_days": None, "window_days": 35, "min_days": 14}
    assert out["fallback"] is None


# ── 3 · the suggestion ───────────────────────────────────────────────────────────────────────
def test_a_branch_with_enough_slips_is_forecast_from_them(chain):
    c = chain
    for d in _days(D0, AS_OF):                                                           # 600 g on weekdays, 1,000 g on Saturday/Sunday
        _slip(c, 1, d, {CRAB: ((400.0 if d.weekday() < 5 else 900.0), 100.0)})
    out = FC.suggest(c, 1, "1", date(2026, 10, 6), season="off", as_of=AS_OF)
    assert out["source"] == {"kind": "slips", "confirmed_days": 35, "window_days": 35, "min_days": 14} and out["fallback"] is None
    crab = {r["code"]: r for r in out["rows"]}[CRAB]
    assert crab["forecast"] == pytest.approx(500 + 500 + 500, rel=1e-6)                 # Wed 7, Thu 8, Fri 9: 400 + 100 each
    assert SALMON not in {r["code"] for r in out["rows"]}                               # the slips name only the crab stick
    assert crab["suggested_units"] == 2.0 and crab["per_sales"]["per_100k"] > 0
    assert out["trading_days"] == 35


def test_o1_a_no_branch_has_enough_slips_so_no_suggestion(chain):
    c = chain
    _slips(c, 1, D0, D0 + timedelta(days=6))                                             # 7 of 14 days
    c.execute("INSERT INTO bc_order(branch_id, cycle_date, round, status, delivery_date) VALUES(1,'2026-09-28','1','submitted','2026-09-30')")
    oid = c.execute("SELECT MAX(order_id) FROM bc_order").fetchone()[0]
    c.execute("INSERT INTO bc_order_line(order_id, ingredient_code, suggested_units, confirmed_units, order_unit)"
              " VALUES(?,?,5,4,'pack')", (oid, CRAB))
    ST.save(c, 1, [{"code": CRAB, "qty": 3, "unit": "กรัม"}], "u", submit=True, count_date="2026-10-01", require_all=False)
    c.commit()
    p = OR.plan(c, 1, "1", WED, now=NOW)
    assert p["source"] == {"kind": "none", "confirmed_days": 7, "window_days": 35, "min_days": 14}
    assert p["fallback"] == {"mode": "none", "confirmed_days": 7, "min_days": 14,
                             "message": "ใบตัดเบิกยังไม่พอ (7/14 วัน) · กรอกยอดสั่งเอง"}
    assert p["ref_mode"] is None and p["sales_normal"] is None
    crab = {r["code"]: r for r in p["rows"]}[CRAB]
    assert crab["suggested"] is None and crab["last_order_qty"] == 4 and crab["system_qty"] is not None
    assert crab["order_units"] == 0 and crab["order_units_base"] is None
    assert all(r["suggested"] is None for r in p["rows"] + p["local_rows"])
    assert "กรุณา" not in p["fallback"]["message"] and "ระบบจะ" not in p["fallback"]["message"]


def test_o1_a_rows_are_saved_without_a_suggestion_and_need_no_reason(chain):
    c = chain
    _slips(c, 1, D0, D0 + timedelta(days=6))
    _slip(c, 1, "2026-10-05", {CRAB: (100.0, 0.0)})
    c.execute("INSERT OR REPLACE INTO bc_ingredient(code, name_th, kind, base_unit, recv_unit, qty_per_recv, supplier, monitored, active)"
              " VALUES(?, 'ปูอัด', 'raw', 'กรัม', 'แพ็ค 1 กก.', 1000, 'x', 1, 1)", (CRAB,))
    out = OR.save_flow(c, 1, "1", WED, [{"code": CRAB, "confirmed_units": 7}], "u", submit=True, now=NOW)
    assert out["needs_reason"] == [] and out["status"] == "submitted"                   # nothing to deviate from
    line = {l["code"]: l for l in OR.detail(c, out["order_id"])["lines"]}[CRAB]
    assert line["suggested_units"] is None and line["confirmed_units"] == 7


def test_o1_b_another_branch_has_enough_so_the_benchmark_times_own_normal_sales(chain):
    c = chain
    _slips(c, 3, D0, AS_OF, crab=(450.0, 50.0))                                          # 500 g on ฿3,000 a day = 16,667 g per ฿100,000
    _slips(c, 4, D0, AS_OF, crab=(900.0, 100.0))                                         # 1,000 g -> 33,333 g
    _slips(c, 1, D0, D0 + timedelta(days=6))                                             # branch 1: 7 days
    p = OR.plan(c, 1, "1", WED, now=NOW)
    assert p["source"]["kind"] == "none" and p["source"]["confirmed_days"] == 7
    assert p["fallback"] == {"mode": "benchmark", "confirmed_days": 7, "min_days": 14, "branches": 2,
                             "message": "จำนวนที่ควรสั่งจากค่ากลาง 2 สาขา (ใบตัดเบิกของสาขา 7/14 วัน)"}
    rm = p["ref_mode"]
    assert rm["active"] and rm["benchmark"] and rm["needs_sales"] is False and rm["own_days"] == 7 and rm["min_days"] == 14
    cover = _days(date.fromisoformat(p["cover_from"]), date.fromisoformat(p["cover_to"]))
    sd = PS.sales_by_day(c, 1, D0, AS_OF)
    own = PS.normal_from(sd, sorted(sd), AS_OF, cover, 1.0)["total"]                    # the branch's own "ปกติ" for the cover days
    assert rm["expected_sales"] == pytest.approx(own, abs=0.01) and own > 0
    median = (500 / 3000 * 1e5 + 1000 / 3000 * 1e5) / 2                                  # 25,000 g per ฿100,000
    crab = {r["code"]: r for r in p["rows"]}[CRAB]
    assert crab["forecast"] == pytest.approx(median * rm["expected_sales"] / 1e5, rel=1e-3)
    assert crab["ref_label"] == "คำนวณจากค่ากลางสาขาของบริษัท" and crab["safety"] == 0
    # typed sales win over the default
    typed = {r["code"]: r for r in OR.plan(c, 1, "1", WED, now=NOW, expected_sales=100000)["rows"]}[CRAB]
    assert typed["forecast"] == pytest.approx(median, rel=1e-3)


def test_o1_b_a_single_slip_branch_is_enough_and_a_none_branch_is_not_a_reference(chain):
    c = chain
    _slips(c, 3, D0, AS_OF, crab=(450.0, 50.0))
    _slips(c, 4, D0, D0 + timedelta(days=9), crab=(900.0, 100.0))                        # 10 days: branch 4 is no reference
    b = PS.benchmark_basis(c, 1, "2026-10", weeks=6, cover=[WED], factor=1.0)
    assert b["ok"] and b["branches"] == ["Co3"]                                          # only the slip branch counts
    assert b["items"][CRAB]["per_100k"] == pytest.approx(500 / 3000 * 1e5)               # the median of one figure
    assert PS.reference_basis(c, 4, "2026-10")["ok"] is False                            # a "none" branch lends nothing
    assert PS.reference_basis(c, 3, "2026-10")["ok"] is True


def test_o1_b_moves_to_the_slips_at_14_days(chain):
    c = chain
    _slips(c, 3, D0, AS_OF)
    _slips(c, 4, D0, AS_OF)
    _slips(c, 1, D0, D0 + timedelta(days=12))
    assert OR.plan(c, 1, "1", WED, now=NOW)["source"]["kind"] == "none"
    _slip(c, 1, D0 + timedelta(days=13), {CRAB: (450.0, 50.0)})
    p = OR.plan(c, 1, "1", WED, now=NOW)
    assert p["source"] == {"kind": "slips", "confirmed_days": 14, "window_days": 35, "min_days": 14}
    assert p["fallback"] is None and p["ref_mode"] is None
    assert all("suggested" not in r for r in p["rows"])                                  # a real suggestion, not the empty rows of O1 (ก)


# ── 4 · the system stock ─────────────────────────────────────────────────────────────────────
def _counted(c, qty=10000):
    ST.save(c, 1, [{"code": CRAB, "qty": qty, "unit": "กรัม"}], "u", submit=True, count_date="2026-09-29", require_all=False)


def test_missing_day_is_the_mean_of_the_same_weekday_in_stock_mode(chain):
    c = chain
    # Mondays 8 and 15 Sep, 22 Sep: 400, 600, 800; Monday 29 Sep is the count day, Monday 6 Oct is the missing day
    for d, q in (("2026-09-08", 400.0), ("2026-09-15", 600.0), ("2026-09-22", 800.0)):
        _slip(c, 1, d, {CRAB: (q, 0.0)})
    _slip(c, 1, "2026-09-16", {CRAB: (9999.0, 0.0)})                                     # another weekday: not in the mean
    _slip(c, 1, "2026-09-29", {CRAB: (50.0, 0.0)})
    days, est = ST.estimated_use(c, 1, ["2026-10-06"])
    # 4 weeks before Mon 6 Oct: 29 Sep (50), 22 Sep (800), 15 Sep (600), 8 Sep (400) -> mean 462.5
    assert days == ["2026-10-06"] and est[CRAB] == pytest.approx((50 + 800 + 600 + 400) / 4)


def test_a_day_with_no_same_weekday_slip_deducts_nothing(chain):
    c = chain
    _slip(c, 1, "2026-09-16", {CRAB: (700.0, 0.0)})                                      # a Tuesday
    days, est = ST.estimated_use(c, 1, ["2026-10-06"])                                   # a Monday: none of the 4 weeks before it
    assert days == [] and est == {}
    days, est = ST.estimated_use(c, 1, ["2026-09-23"])                                   # a Tuesday: 16 Sep is in range
    assert days == ["2026-09-23"] and est == {CRAB: 700.0}


def test_a_confirmed_day_without_the_item_counts_as_zero_in_the_mean(chain):
    c = chain
    _slip(c, 1, "2026-09-08", {CRAB: (600.0, 0.0)})
    _slip(c, 1, "2026-09-15", {SALMON: (100.0, 0.0)})
    days, est = ST.estimated_use(c, 1, ["2026-09-22"])
    assert est == {CRAB: 300.0, SALMON: 50.0}                                            # two confirmed Mondays, each item over both


def test_full_mode_still_estimates_a_missing_day_from_the_recipes(chain):
    c = chain
    CS.set_mode(c, "t", "full")
    days, est = ST.estimated_use(c, 1, ["2026-10-05"])
    assert days == ["2026-10-05"] and est[CRAB] > 0                                      # the BoM of the sales of that day


def test_system_position_reports_what_the_estimate_is_made_of(chain):
    c = chain
    _counted(c)
    _slip(c, 1, "2026-09-22", {CRAB: (800.0, 0.0)})
    pos = ST.system_position(c, 1, date(2026, 10, 6))
    assert "2026-10-06" not in pos["estimated_days"] and "2026-09-30" in pos["missing_days"]
    p = OR.plan(c, 1, "1", WED, now=NOW)
    assert p["stock"]["estimate_basis"] == "slips"
    CS.set_mode(c, "t", "full")
    assert OR.plan(c, 1, "1", WED, now=NOW)["stock"]["estimate_basis"] == "sales"


# ── 5 · the report, the backtest, the ratio ──────────────────────────────────────────────────
def test_the_per_100k_report_follows_the_selector(chain):
    c = chain
    _slips(c, 3, D0, AS_OF, crab=(450.0, 50.0))
    _slips(c, 4, D0, AS_OF, crab=(900.0, 100.0))
    r = PS.report(c, 4, branch_ids=[1, 3, 4], today=date(2026, 10, 6))
    by = {b["branch_id"]: b for b in r["branches"]}
    assert by[3]["source"]["kind"] == "slips" and by[1]["source"]["kind"] == "none"
    crab3 = {i["code"]: i for i in by[3]["items"]}[CRAB]
    assert crab3["per_100k"] == pytest.approx(500 / 3000 * 1e5 / 1000, rel=1e-4)                  # shown in kg (the unit it is counted in)
    assert by[1]["items"] == [] and by[1]["sales"] == 0.0                                # no slips, no ratio: not zero usage over real sales
    assert r["chain"]["sales"] == pytest.approx(by[3]["sales"] + by[4]["sales"])         # the chain is the slip branches only
    assert r["benchmark"]["n"] == 2
    CS.set_mode(c, "t", "full")
    PS.clear_cache()
    f = PS.report(c, 4, branch_ids=[1, 3, 4], today=date(2026, 10, 6))
    assert {b["branch_id"]: b for b in f["branches"]}[1]["source"]["kind"] == "recipes"
    assert {b["branch_id"]: b for b in f["branches"]}[1]["items"]                        # the recipes give branch 1 a usage again


def test_the_ratio_divides_the_slips_by_the_sales_of_the_slip_days(chain):
    c = chain
    for d in _days(D0, D0 + timedelta(days=20)):                                         # 21 confirmed days of 35
        _slip(c, 3, d, {CRAB: (500.0, 0.0)})
    r = PS.ratio(c, 3, D0, AS_OF, "2026-10")
    assert r["source"]["kind"] == "slips"
    assert r["sales"] == 21 * 3000 and r["items"][CRAB]["per_100k"] == pytest.approx(500 / 3000 * 1e5)


def test_backtest_reads_the_slips_and_says_when_there_are_none(chain):
    c = chain
    _slips(c, 3, D0, AS_OF, crab=(450.0, 50.0))
    bt = FC.backtest(c, 3, weeks=2, history_weeks=3, as_of=AS_OF)
    assert bt["available"] and bt["source"]["kind"] == "slips" and bt["mape"] == pytest.approx(0.0, abs=1e-6)
    none = FC.backtest(c, 1, weeks=2, history_weeks=3, as_of=AS_OF)
    assert none["available"] is False and none["source"]["kind"] == "none"


def test_stock_count_sheet_usage_reads_the_selector(chain):
    c = chain
    assert ST._usage(c, 1, AS_OF) == {}                                                  # stock mode, no slips: no recipe usage
    _slips(c, 3, D0, AS_OF, crab=(450.0, 50.0))
    assert ST._usage(c, 3, AS_OF)[CRAB] == pytest.approx(500.0)
    CS.set_mode(c, "t", "full")
    assert ST._usage(c, 1, AS_OF)[CRAB] > 0


# ── 6 · the cache ────────────────────────────────────────────────────────────────────────────
def test_slip_usage_is_cached_per_file_and_cleared_with_the_usage_cache(chain, monkeypatch, tmp_path):
    c = chain
    monkeypatch.setattr(FC, "USAGE_CACHE_TTL", 600)
    FC.clear_usage_cache()
    _slips(c, 3, D0, AS_OF)
    c.commit()
    path = next(r[2] for r in c.execute("PRAGMA database_list") if r[1] == "main")
    if not path:
        pytest.skip("in-memory database: never cached")
    FC.usage_history_slips(c, 3, D0, AS_OF)
    assert len(FC._slip_cache) == 1
    _slip(c, 3, "2026-08-30", {CRAB: (1.0, 0.0)})                                        # outside the window: a different key anyway
    FC.usage_history_slips(c, 3, D0, AS_OF)
    assert len(FC._slip_cache) == 1                                                      # the same key: served from the cache
    FC.clear_usage_cache()
    assert FC._slip_cache == {}
    FC.usage_history_slips(c, 3, D0, AS_OF)
    FC.clear_slip_cache()
    assert FC._slip_cache == {}


def test_every_slip_write_endpoint_drops_the_slip_cache(client, monkeypatch):
    import main
    from routers import bom_center as RB
    monkeypatch.setenv("OSHINEI_SEED_TOKEN", "t")
    hdr = {"X-Upload-Token": "t"}
    monkeypatch.setattr(RB, "bkk_today", lambda: date(2026, 10, 5))
    from bom_center import issues as IS
    fixed = datetime(2026, 10, 5, 21, 0, tzinfo=BKK)
    monkeypatch.setattr(IS, "now_bkk", lambda: fixed)
    monkeypatch.setattr(FC, "USAGE_CACHE_TTL", 600)
    client.get("/api/bom-center/hub/queue")                                              # creates the bc_* tables
    c = sqlite3.connect(str(main.DB_PATH))
    bid = c.execute("SELECT branch_id FROM branches WHERE name<>'ALL' ORDER BY branch_id LIMIT 1").fetchone()[0]
    c.execute("INSERT OR REPLACE INTO bc_ingredient(code, name_th, kind, base_unit, recv_unit, qty_per_recv, supplier, active)"
              " VALUES('SLIP-1', 'ของ ก', 'raw', 'กรัม', 'แพ็ค', 1000, 'ผู้จำหน่าย ก', 1)")
    snap = {t: c.execute(f"SELECT * FROM {t}").fetchall() for t in ("bc_issue_slip", "bc_issue_line", "bc_issue_log", "bc_audit")}
    c.commit()

    def warm():
        FC.usage_history_slips(c, bid, date(2026, 9, 1), date(2026, 10, 5))
        assert len(FC._slip_cache) == 1

    try:
        warm()
        r = client.post(f"/api/bom-center/issues/save?branch_id={bid}", headers=hdr,
                        json={"date": "2026-10-05", "lines": [{"code": "SLIP-1", "qty_used": 5, "unit": "กรัม"}], "submit": True})
        assert r.status_code == 200, r.text
        assert FC._slip_cache == {}                                                      # save
        warm()
        r = client.post(f"/api/bom-center/issues/blank?branch_id={bid}", headers=hdr, json={"date": "2026-10-05", "codes": []})
        assert r.status_code == 200, r.text
        assert FC._slip_cache == {}                                                      # blank
        warm()
        r = client.post(f"/api/bom-center/issues/append?branch_id={bid}", headers=hdr,
                        json={"date": "2026-10-05", "lines": [{"code": "SLIP-1", "qty_used": 1, "unit": "กรัม"}]})
        assert r.status_code == 200, r.text
        assert FC._slip_cache == {}                                                      # append
        warm()
        sid = c.execute("SELECT slip_id FROM bc_issue_slip WHERE branch_id=? AND slip_date='2026-10-05'", (bid,)).fetchone()[0]
        r = client.post("/api/bom-center/issues/reopen", headers=hdr, json={"slip_id": sid, "note": "ทดสอบ"})
        assert r.status_code == 200, r.text
        assert FC._slip_cache == {}                                                      # reopen
    finally:
        c.close()
        k = sqlite3.connect(str(main.DB_PATH))
        for t, rows in snap.items():
            k.execute(f"DELETE FROM {t}")
            if rows:
                k.executemany(f"INSERT INTO {t} VALUES({','.join('?' * len(rows[0]))})", rows)
        k.execute("DELETE FROM bc_ingredient WHERE code='SLIP-1'")
        k.commit()
        k.close()
        FC.clear_usage_cache()


def test_the_plan_endpoint_answers_source_and_fallback(client, monkeypatch):
    import main
    from routers import bom_center as RB
    monkeypatch.setattr(RB, "bkk_today", lambda: date(2026, 10, 5))
    client.get("/api/bom-center/hub/queue")
    c = sqlite3.connect(str(main.DB_PATH))
    bid = c.execute("SELECT branch_id FROM branches WHERE name<>'ALL' ORDER BY branch_id LIMIT 1").fetchone()[0]
    c.close()
    j = client.get(f"/api/bom-center/order/plan?branch_id={bid}&round=1&delivery=2026-10-07")
    assert j.status_code == 200, j.text
    out = j.json()
    assert out["source"]["kind"] == "none" and out["source"]["min_days"] == 14
    assert out["fallback"]["mode"] in ("none", "benchmark") and out["fallback"]["min_days"] == 14
    assert out["stock"]["estimate_basis"] == "slips"


# ── 7 · the all-branch median in stock mode: every company branch on slips, not the configured set ──
def test_a_company_branch_outside_the_benchmark_set_counts_in_stock_mode(chain):
    c = chain
    BM.set_benchmark(c, "t", [1, 3])                                                     # branch 4 is a company branch, not in the set
    _slips(c, 4, D0, D0 + timedelta(days=13), crab=(900.0, 100.0))                       # exactly 14 confirmed days
    b = PS.benchmark_basis(c, 1, "2026-10", weeks=6, cover=[WED], factor=1.0)
    assert b["ok"] and b["branches"] == ["Co4"]
    assert b["items"][CRAB]["per_100k"] == pytest.approx(1000 / 3000 * 1e5)
    assert FC.source_label(c, 1) == {"kind": "benchmark", "confirmed_days": 0, "window_days": 35, "min_days": 14, "branches": 1}
    CS.set_mode(c, "t", "full")                                                          # full mode keeps the configured set
    assert PS.benchmark_basis(c, 1, "2026-10", weeks=6, cover=[WED], factor=1.0)["branches"] == ["Co3"]   # the set [1, 3] (recipes), not Co4


def test_a_franchise_branch_does_not_count(chain):
    c = chain
    c.execute("INSERT INTO branches(branch_id, name, branch_type, active) VALUES(6, 'Fr6', 'franchise', 1)")
    _sell(c, 6)
    c.commit()
    _slips(c, 6, D0, AS_OF)
    assert 6 not in FC.company_branch_ids(c)
    assert PS.benchmark_basis(c, 1, "2026-10", weeks=6, cover=[WED], factor=1.0)["ok"] is False
    p = OR.plan(c, 1, "1", WED, now=NOW)
    assert p["fallback"]["mode"] == "none"                                               # a franchise branch's slips open no (ข)
    _slips(c, 3, D0, AS_OF)
    b = PS.benchmark_basis(c, 1, "2026-10", weeks=6, cover=[WED], factor=1.0)
    assert b["branches"] == ["Co3"]


def test_the_planned_branch_is_left_out_of_its_own_median(chain):
    c = chain
    _slips(c, 3, D0, AS_OF, crab=(450.0, 50.0))                                          # 16,667 g per ฿100,000
    _slips(c, 4, D0, AS_OF, crab=(900.0, 100.0))                                         # 33,333
    b3 = PS.benchmark_basis(c, 3, "2026-10", weeks=6, cover=[WED], factor=1.0)
    assert b3["branches"] == ["Co4"] and b3["items"][CRAB]["per_100k"] == pytest.approx(1000 / 3000 * 1e5)
    b4 = PS.benchmark_basis(c, 4, "2026-10", weeks=6, cover=[WED], factor=1.0)
    assert b4["branches"] == ["Co3"] and b4["items"][CRAB]["per_100k"] == pytest.approx(500 / 3000 * 1e5)
