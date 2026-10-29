# -*- coding: utf-8 -*-
"""DV1 · the wrong and conflicting numbers found by the phone table review (docs/reviews/PHONE_TABLE_REVIEW_2026-10-25.md §5).

One regression test per number:
  1. returning menus count plates over the divisor's window ............ test_menu_spike_period.py (section 10)
  2. แซลมอนดองซีอิ๊ว "+฿11.39/หัว" vs วิเคราะห์เมนู "−0.1 %": the change splits into price + plates per head
  3. food cost per head 336 vs 383 on one tab: gp.cost_per_head = the cost GP subtracts / covers (= the bridge)
  4. month projection 126 % vs 124 %: one rule (rules.month_projection) for /api/forecast and the management summary
  5. branch % of target: on the month-end projection; a part month last year is no basis (นครพนม 3792 %); the chain's %
     like for like (targets.attainment)
  6. เป้าลดต้นทุน: a category's FC% only over priced dishes (5531 %); an ingredient's type = the one carrying its cost
     (Beef filed as seasoning)
  7. ช่วงเวลา: the average bill no longer reads "2k" in every cell
  8. บิลยกเลิก: a reason code the POS list does not hold (รหัส -1) is flagged abnormal
"""
import json
import sqlite3
import subprocess
import tempfile
from contextlib import contextmanager
from pathlib import Path

import pytest

import rules
from core import invalidate_cache
import targets as T
from routers import cost_bom, home_extra
from routers.reports import _cost_riser_action, _cph_split
from test_bom_center_batch2 import NODE, needs_node

REPO = Path(__file__).resolve().parent.parent


def _fake_db(monkeypatch, module, con):
    @contextmanager
    def fake():
        yield con
    monkeypatch.setattr(module, "get_db", fake)


# ── 2. the dish whose cost per head rose: price vs plates per head ─────────────────────────────────────────────────────
def test_cost_per_head_change_splits_into_price_and_plates():
    # the review's dish, 1–19 Oct vs 1–19 Sep: 65,226 plates at ฿44.28 (the Oct price list) over 78,665 covers, against
    # ~69,600 plates at ฿30.54 over 83,985 covers - plates per head flat, the cost per plate +45 %
    cur_q, cur_c, cov_c = 65226, 65226 * 44.28, 78665
    prv_q, prv_c, cov_p = 69600, 69600 * 30.54, 83985
    s = _cph_split(cur_q, cur_c, prv_q, prv_c, cov_c, cov_p)
    delta = cur_c / cov_c - prv_c / cov_p
    assert s["usage_delta"] + s["price_delta"] == pytest.approx(delta, abs=0.02)
    assert s["price_delta"] > 10 and abs(s["usage_delta"]) < 0.5
    # usage_delta = what วิเคราะห์เมนู compares: last period's plates at today's cost per plate
    assert s["usage_delta"] == pytest.approx(cur_c / cov_c - prv_q * (cur_c / cur_q) / cov_p, abs=0.01)
    m0 = {"menu": "แซลมอนดองซีอิ๊ว", "delta": round(delta, 2), "cost_per_head": round(cur_c / cov_c, 2), **s}
    t = _cost_riser_action(m0, "เดือน", True)
    # PR-PD no. 18: the split names the dish's COST per plate (ต้นทุนต่อจาน), never a selling price
    assert "ต้นทุนต่อจาน +฿" in t and "กินต่อหัว" in t and t.endswith("ตรวจสอบราคาต่อจาน / ราคาวัตถุดิบ")
    # plates per head up, cost per plate flat: portioning
    s2 = _cph_split(120, 120 * 30.0, 100, 100 * 30.0, 100, 100)
    assert s2 == {"usage_delta": 6.0, "price_delta": 0.0}
    assert _cost_riser_action({"menu": "x", "delta": 6.0, "cost_per_head": 36.0, **s2}, "เดือน", True).endswith("ตรวจสอบการตักเสิร์ฟ")
    assert _cph_split(0, 0, 10, 300, 100, 100) == {} and _cph_split(10, 300, 10, 300, 100, 0) == {}


# ── 3 / 4 / 5. the management summary on the fixture ──────────────────────────────────────────────────────────────────
def test_one_cost_per_head_and_one_projection(client):
    m = client.get("/api/reports/management", params={"period": "monthly", "mtd": "1"}).json()
    g, p = m["gp"], m["pace"]
    # 3: the tile's figure is the cost GP subtracts over the covers - GP/head + cost/head = revenue/head
    assert g["cost_per_head"] == round(g["food_cost"] / m["headline"]["customers"], 1)
    assert g["cost_per_head"] + g["gp_per_head"] == pytest.approx(m["headline"]["rev_per_head"], abs=0.2)
    if g.get("bridge"):
        assert g["cost_per_head"] == pytest.approx(g["bridge"]["cost_head_cur"], abs=0.1)
    # 4: /api/forecast and the management summary project the month the same way
    fc = client.get("/api/forecast").json()
    if fc.get("available") and not p.get("month_complete"):
        assert fc["projected"] == pytest.approx(p["projected"], abs=0.5)
        assert fc["proj_vs_target_pct"] == p["proj_vs_target_pct"]


def test_branch_target_on_the_projection_and_the_chain_like_for_like(client):
    m0 = client.get("/api/reports/management", params={"period": "monthly", "mtd": "1"}).json()
    p0 = m0["pace"]
    if p0.get("month_complete") or not p0.get("projected"):
        pytest.skip("the fixture's latest month is complete")
    month = p0["month"]
    br = m0["branches"][0]["branch"]
    r = client.put("/api/targets", json=[{"branch": br, "period_month": month, "revenue_target": 4000, "customer_target": 1}])
    assert r.status_code == 200
    invalidate_cache(rebuild_cost=False)                 # the summary is a cached response
    try:
        m = client.get("/api/reports/management", params={"period": "monthly", "mtd": "1"}).json()
        p = m["pace"]
        b = next(x for x in m["branches"] if x["branch"] == br)
        f = p["projected"] / p["mtd_revenue"]
        assert b["target_basis"] == "projected" and b["projected"] == round(b["revenue"] * f)
        assert b["vs_target_pct"] == round(b["projected"] / 4000 * 100, 1)
        assert b["mtd_vs_target_pct"] == round(b["revenue"] / 4000 * 100, 1)
        # the chain: the one branch with a target, never the sales of the branches without one
        assert p["target"] == 4000 and p["proj_vs_target_pct"] == round(b["projected"] / 4000 * 100, 1)
        assert p["target_scope"]["branches"] == 1 and p["target_scope"]["no_target_branches"] == len(m["branches"]) - 1
        fc = client.get("/api/forecast").json()
        if fc.get("available"):
            assert fc["target"] == 4000 and fc["proj_vs_target_pct"] == pytest.approx(p["proj_vs_target_pct"], abs=0.2)
    finally:
        client.put("/api/targets", json=[{"branch": br, "period_month": month, "revenue_target": 0, "customer_target": 0}])
        invalidate_cache(rebuild_cost=False)


def _tdb():
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.executescript("""
    CREATE TABLE branches(branch_id INTEGER PRIMARY KEY, name TEXT, branch_type TEXT, active INTEGER DEFAULT 1);
    INSERT INTO branches VALUES (1,'Full','master',1),(2,'Late','master',1),(3,'Week2','master',1),(4,'New','master',1);
    CREATE TABLE branch_targets(id INTEGER PRIMARY KEY AUTOINCREMENT, branch_id INTEGER, period_month TEXT,
                                revenue_target REAL DEFAULT 0, customer_target INTEGER DEFAULT 0, UNIQUE(branch_id, period_month));
    CREATE TABLE sales_transaction(branch_id INTEGER, sold_at TEXT, total_inc_vat REAL, service_charge REAL, customer_count INTEGER, source TEXT);
    """)
    rows = [(1, f"2025-10-{d:02d} 12:00:00", 1070.0, 0.0, 2, "Easy") for d in range(1, 32)]
    rows += [(2, "2025-10-31 12:00:00", 10700.0, 0.0, 9, "Easy")]                        # opened on the last day
    rows += [(3, f"2025-10-{d:02d} 12:00:00", 1070.0, 0.0, 2, "Easy") for d in range(8, 32)]   # opened on the 8th
    con.executemany("INSERT INTO sales_transaction VALUES (?,?,?,?,?,?)", rows)
    return con


def test_a_part_month_last_year_is_no_target():
    rows = {r["branch"]: r for r in T.targets_for_month(_tdb(), "2026-10")}
    assert rows["Full"]["source"] == "last_year" and rows["Full"]["revenue_target"] == 31000.0
    # นครพนม: one day of last October (฿39K) made a target of one day - 3792 % "of target"
    late = rows["Late"]
    assert (late["source"], late["revenue_target"], late["basis_partial"], late["basis_revenue"]) == ("none", 0.0, True, 10000.0)
    assert rows["Week2"]["source"] == "last_year" and rows["Week2"]["basis_partial"] is False      # within 7 days of the chain
    assert rows["New"]["source"] == "none" and rows["New"]["basis_partial"] is False
    assert T.target_revenue_total(_tdb(), "2026-10") == 31000.0 + 24000.0


def test_attainment_is_like_for_like():
    con = _tdb()
    a = T.attainment(con, "2026-10", {"Full": 15500.0, "Week2": 12000.0, "Late": 9000.0, "New": 5000.0, "Gone": 0.0}, 2.0)
    # Full + Week2 have targets (31,000 + 24,000); Late (part-month basis) and New have none; a branch with no sales is left out
    assert (a["target"], a["projected"], a["branches"]) == (55000.0, 55000.0, 2)
    assert a["pct"] == 100.0 and (a["no_target_branches"], a["no_target_revenue"]) == (2, 28000.0)
    # a target whose branch sold nothing this month (closed) is not in the chain's target
    assert T.attainment(con, "2026-10", {"Full": 15500.0}, 2.0)["target"] == 31000.0
    assert T.attainment(con, "2026-10", {"New": 1.0})["pct"] is None


def test_month_projection_is_weekday_fair():
    con = sqlite3.connect(":memory:")
    con.executescript("""
    CREATE TABLE branches(branch_id INTEGER PRIMARY KEY, name TEXT, branch_type TEXT);
    INSERT INTO branches VALUES (1,'A','master');
    CREATE TABLE sales_transaction(branch_id INTEGER, sold_at TEXT, total_inc_vat REAL, service_charge REAL, customer_count INTEGER, source TEXT);
    """)
    import datetime as dt
    d = dt.date(2026, 9, 1)
    while d.month == 9:                    # September: ฿200 (net) on Sat/Sun, ฿100 on weekdays
        con.execute("INSERT INTO sales_transaction VALUES (1, ?, ?, 0, 1, 'Easy')",
                    (f"{d} 12:00:00", (200.0 if d.weekday() >= 5 else 100.0) * 1.07))
        d += dt.timedelta(days=1)
    w = "st.source='Easy' AND b.name!='ALL'"
    # 1–4 Oct 2026 = Thu, Fri, Sat, Sun: normal = 100 + 100 + 200 + 200 = 600; sold 600 -> the month = the normal October
    oct_normal = sum(200.0 if dt.date(2026, 10, i).weekday() >= 5 else 100.0 for i in range(1, 32))
    mp = rules.month_projection(con, w, {}, dt.date(2026, 10, 1), 4, 31, 600.0)
    assert mp["method"] == "dow" and mp["projected"] == pytest.approx(oct_normal, abs=0.01)
    assert mp["projected"] != round(600.0 / 4 * 31, 2)                    # the flat run-rate overstates a weekend start
    # the whole month elapsed, or no previous month: flat
    assert rules.month_projection(con, w, {}, dt.date(2026, 10, 1), 31, 31, 4000.0)["method"] == "flat"
    assert rules.month_projection(con, w, {}, dt.date(2026, 12, 1), 4, 31, 600.0) == {
        "projected": 4650.0, "method": "flat", "dow_avg": {}, "dow_fallback": 0, "baseline_elapsed": None}


# ── 6. เป้าลดต้นทุน: the ingredient's type ───────────────────────────────────────────────────────────────────────────────
def test_an_ingredient_takes_the_type_carrying_its_cost(monkeypatch):
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.executescript("""
    CREATE TABLE branches(branch_id INTEGER PRIMARY KEY, name TEXT);
    INSERT INTO branches VALUES (1,'A');
    CREATE TABLE v_sales_sku_monthly(branch_id INT, sku_id INT, period_month TEXT, qty REAL);
    CREATE TABLE bom_ingredients(sku_id INT, ingredient_code TEXT, ingredient_name TEXT, ingredient_type TEXT, unit TEXT,
                                 qty_per_serving REAL, ingredient_cost REAL);
    INSERT INTO v_sales_sku_monthly VALUES (1, 10, '2031-01', 100), (1, 11, '2031-01', 5), (1, 12, '2031-01', 50);
    -- the same wagyu in two recipes: typed protein where most of it is used, 'seasoning' in a small one
    INSERT INTO bom_ingredients VALUES (10, 'W1', 'เนื้อวากิวสไลด์', 'protein', 'g', 50, 40),
                                       (11, 'W1', 'เนื้อวากิวสไลด์', 'seasoning', 'g', 5, 4),
                                       (12, 'B2', 'เนื้อสันใน', 'sauce', 'g', 10, 2);
    """)
    _fake_db(monkeypatch, cost_bom, con)
    d = json.loads(cost_bom.bom_consumption_analysis(month_from="2031-01", month_to="2031-01", branch=None, limit=50,
                                                     group_family=False).body)
    rows = {r["name"]: r for r in (d.get("ingredients") or d.get("rows"))}
    assert rows["เนื้อวากิวสไลด์"]["type"] == "protein"           # MAX(type) read 'seasoning'
    g = json.loads(cost_bom.bom_consumption_analysis(month_from="2031-01", month_to="2031-01", branch=None, limit=50,
                                                     group_family=True).body)
    beef = next(r for r in (g.get("ingredients") or g.get("rows")) if r["name"].startswith("Beef"))
    assert beef["type"] == "protein" and beef["total_cost"] == 4000 + 20 + 100


# ── 8. บิลยกเลิก: an unknown reason code ─────────────────────────────────────────────────────────────────────────────────
def test_an_unknown_cancel_code_is_flagged(monkeypatch):
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.executescript("""
    CREATE TABLE branches(branch_id INTEGER PRIMARY KEY, name TEXT, branch_type TEXT);
    INSERT INTO branches VALUES (1,'A','master');
    CREATE TABLE pos_cancel_reason(reason_id INTEGER PRIMARY KEY, reason_desc TEXT);
    INSERT INTO pos_cancel_reason VALUES (2, 'คีย์ข้อมูลผิด');
    CREATE TABLE pos_cancel_bill(branch_id INT, period_month TEXT, reason_id INT, bills INT, amount REAL);
    INSERT INTO pos_cancel_bill VALUES (1,'2031-02',-1,1,242150),(1,'2031-02',0,551,0),(1,'2031-02',2,128,255813);
    CREATE TABLE sales_transaction(branch_id INTEGER, sold_at TEXT, source TEXT);
    """)
    _fake_db(monkeypatch, home_extra, con)
    d = home_extra.extra_cancel_bills(month="2031-02", branch=None, group=None)
    by = {r["reason"]: r["abnormal"] for r in d["reasons"]}
    assert by == {"รหัส -1": True, "ไม่ระบุเหตุผล": False, "คีย์ข้อมูลผิด": False}


# ── 6 / 7. the pure page helpers ────────────────────────────────────────────────────────────────────────────────────────
def _js_block(rel, tag):
    src = (REPO / rel).read_text(encoding="utf-8")
    return src[src.index(f"// <{tag}>"):src.index(f"// </{tag}>")]


def _node(js):
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
        f.write(js)
    try:
        return json.loads(subprocess.run([NODE, f.name], capture_output=True, text=True, encoding="utf-8", check=True).stdout)
    finally:
        Path(f.name).unlink()


@needs_node
def test_heat_map_cells_and_fc_pct():
    js = _js_block("js/09-analysis-tabs.js", "tf-fmt-cell") + _js_block("js/08-guest-buffet.js", "mce-fc") + """
    console.log(JSON.stringify({
      bills: [1820, 2140, 1499, 999].map(v => tfFmtCell(v, true)),
      money: [12500, 1250000, 0].map(v => tfFmtCell(v, true)),
      count: [1820, 40].map(v => tfFmtCell(v, false)),
      fc: [mceFcPct(40, 100), mceFcPct(5531, 100), mceFcPct(10, 0), mceFcPct(999, 100)],
    }));"""
    out = _node(js)
    assert out["bills"] == ["1.8k", "2.1k", "1.5k", "999"]          # was "2k" in every cell
    assert out["money"] == ["13k", "1.3M", ""]
    assert out["count"] == ["1.8k", "40"]
    assert out["fc"] == [40, None, None, 999]                        # 5531 % (buffet dishes in the category) reads "—"
