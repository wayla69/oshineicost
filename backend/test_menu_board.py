# -*- coding: utf-8 -*-
"""DV7 (owner 2026-10-25, phone table review #10) · ปรับเมนู (/api/menu/action-board) and โอกาสเมนู (/api/menu/opportunities) around the
buffet cost per head (backend/menu_board.py).

Fixture: six branches (BranchB is a franchise), 100 customers a day each (10 bills x 10 covers), 1 Apr - 30 Jun 2026, both dish tables.
Plates per customer, before June (April, May) -> June:
    (B) ปลาแซลมอนย่าง ฿20   0.5 -> 1.0 (BranchF 2.0)       the eat list: +฿240,000 on 1-30 Jun vs 2-31 May
    (B) กุ้ง ฿10             1.0 -> 0.6                       the down list: -฿72,000
    (B) ซุปมิโซะ ฿2          0.2 -> 0.4, two codes (A-C / D-F) one dish: +฿7,200
    (B) ปานิปูริ (no cost)   - -> 0.3                         uncosted
    (A) ข้าวหน้าแซลมอน ฿40, ฿150 a plate   0.08 -> 0.1 at A-E, never at BranchF: à la carte GP and the gap
Buffet fact tables: every branch's customers split 50 / 50 into the ฿789 and ฿999 tiers, every dish eaten alike in both."""
import os
os.environ["OSHINEI_NO_SWAP"] = "1"
import datetime as dt
import json
import sqlite3
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

HERE = Path(__file__).parent.resolve()
sys.path.insert(0, str(HERE))
import main  # noqa: E402
import conftest as _cf  # noqa: E402
import menu_board as MB  # noqa: E402

BR = {1: "BranchA", 2: "BranchB", 3: "BranchC", 4: "BranchD", 5: "BranchE", 6: "BranchF"}
FIRST, JUNE, LAST = dt.date(2026, 4, 1), dt.date(2026, 6, 1), dt.date(2026, 6, 30)
CUST = 100                     # a day, per branch
# sku_id, code, description, unit cost, rate before June, rate in June (BranchF's June rate when it differs), revenue a plate
DISHES = [
    (10, "B1010", "(B) ปลาแซลมอนย่าง", 20.0, 0.5, 1.0, {6: 2.0}, 0.0),
    (11, "B1011", "(B) กุ้ง", 10.0, 1.0, 0.6, {}, 0.0),
    (12, "B1012", "(B) ซุปมิโซะ", 2.0, 0.2, 0.4, {}, 0.0),      # branches 1-3
    (13, "B1013", "(B) ซุปมิโซะ", 2.0, 0.2, 0.4, {}, 0.0),      # branches 4-6: the same dish under another code
    (15, "B1015", "(B) ปานิปูริ", 0.0, 0.0, 0.3, {}, 0.0),
    (20, "A1020", "(A) ข้าวหน้าแซลมอน", 40.0, 0.08, 0.1, {6: 0.0}, 150.0),
]
RANGE = {"kind": "range", "date_from": "2026-06-01", "date_to": "2026-06-30"}     # base = the 30 days before: 2-31 May


def _rate(d, bid, day):
    sid, _c, _n, _u, before, june, over, _p = d
    if sid == 12 and bid > 3 or sid == 13 and bid <= 3:
        return 0.0
    if sid == 20 and bid == 6:
        return 0.0
    if day >= JUNE:
        return over.get(bid, june)
    return before


@pytest.fixture(scope="module")
def mb(tmp_path_factory):
    db = tmp_path_factory.mktemp("mb") / "test.db"
    _cf._build_db(str(db))
    con = sqlite3.connect(str(db))
    for t in ("sales_transaction", "sales_sku_daily", "sales_sku_monthly", "sales_month_summary", "sale_bill_cogs"):
        con.execute(f"DELETE FROM {t}")
    con.executemany("INSERT INTO branches(branch_id,name,branch_type,active) VALUES(?,?,'master',1)", [(i, BR[i]) for i in (3, 4, 5, 6)])
    con.executemany("INSERT INTO skus(sku_id,sku_code,description,category_id,unit,business_type,active) VALUES(?,?,?,3,'plate','buffet',1)",
                    [(d[0], d[1], d[2]) for d in DISHES])
    con.executemany("INSERT INTO sku_cost(sku_id,unit_cost) VALUES(?,?)", [(d[0], d[3]) for d in DISHES if d[3]])
    con.executescript("""
      CREATE TABLE menu_fact_total(branch_id INT, day TEXT, tier TEXT, bills INT, pax REAL, cogs REAL, rev REAL, hc REAL, PRIMARY KEY(branch_id, day, tier));
      CREATE TABLE menu_fact_day(branch_id INT, day TEXT, tier TEXT, ent TEXT, bills INT, pax REAL, qty REAL, ent_cogs REAL, ent_hc REAL,
                                 cogs REAL, rev REAL, hc REAL, PRIMARY KEY(branch_id, day, tier, ent));""")
    month: dict = {}
    d = FIRST
    while d <= LAST:
        ds = d.isoformat()
        for bid in BR:
            con.executemany(
                "INSERT INTO sales_transaction(branch_id,receipt_no,sold_at,customer_count,food_subtotal,drink_subtotal,"
                "other_subtotal,sp_discount,discount,service_charge,total_inc_vat,source) VALUES(?,?,?,?,?,?,?,?,?,?,?,'Easy')",
                [(bid, f"{ds}-{bid}-{k}", f"{ds}T12:{k:02d}:00", CUST // 10, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0) for k in range(10)])
            for t in ("789", "999"):
                con.execute("INSERT INTO menu_fact_total VALUES(?,?,?,5,?,0,0,0)", (bid, ds, t, CUST / 2))
            for dish in DISHES:
                q = _rate(dish, bid, d) * CUST
                if q <= 0:
                    continue
                rv = q * dish[7]
                con.execute("INSERT INTO sales_sku_daily VALUES(?,?,?,?,?)", (bid, ds, dish[0], q, rv))
                k = (bid, ds[:7], dish[0])
                a = month.setdefault(k, [0.0, 0.0])
                a[0] += q; a[1] += rv
                if dish[2].startswith("(B)"):
                    for t in ("789", "999"):
                        con.execute("INSERT INTO menu_fact_day VALUES(?,?,?,?,5,?,?,0,0,0,0,0)", (bid, ds, t, f"s:{dish[0]}", CUST / 2, q / 2))
        d += dt.timedelta(days=1)
    for (bid, ym, sid), (q, rv) in month.items():
        con.execute("INSERT INTO sales_sku_monthly(branch_id,period_month,sku_id,qty,subtotal_ex_vat,tax_total,total_inc_vat,source)"
                    " VALUES(?,?,?,?,?,0,?,'Easy')", (bid, ym, sid, q, rv, rv * 1.07))
    con.commit(); con.close()
    import core
    prev = core.DB_PATH
    core.DB_PATH = db
    main.DB_PATH = db
    main._tls.con = None
    try: main.invalidate_cache()
    except Exception: pass
    try:
        with TestClient(main.app) as c:
            yield c
    finally:
        core.DB_PATH = prev
        main.DB_PATH = prev
        main._tls.con = None
        try: main.invalidate_cache()
        except Exception: pass


def _rows(d, k):
    return {r["name"]: r for r in d["buffet"]["buckets"][k]}


# ── ปรับเมนู ─────────────────────────────────────────────────────────────────────────────────────────────────────────────
def test_the_board_follows_the_period_and_compares_the_same_number_of_days(mb):
    d = mb.get("/api/menu/action-board", params=RANGE).json()
    assert d["period"]["cur"] == ["2026-06-01", "2026-06-30"] and d["period"]["prev"] == ["2026-05-02", "2026-05-31"]
    h = d["headline"]
    assert h["customers"] == 6 * CUST * 30 and h["prev"]["customers"] == 6 * CUST * 30
    m = mb.get("/api/menu/action-board", params={"kind": "month", "pick": "2026-05-15"}).json()
    assert m["period"]["cur"] == ["2026-05-01", "2026-05-31"] and m["headline"]["customers"] == 6 * CUST * 31
    # an old client's month range is read as the whole months
    o = mb.get("/api/menu/action-board", params={"month_from": "2026-05", "month_to": "2026-05"}).json()
    assert o["period"]["cur"] == ["2026-05-01", "2026-05-31"]


def test_the_dishes_add_up_to_the_food_cost_per_head_of_period_cost(mb):
    import core, period_cost as PC
    d = mb.get("/api/menu/action-board", params=RANGE).json()
    with core.get_db() as con:
        cs, _ = PC.cost_sum(con, "2026-06-01", "2026-06-30", None, None, PC.cost_floor(con))
        ps, _ = PC.cost_sum(con, "2026-05-02", "2026-05-31", None, None, PC.cost_floor(con))
    h = d["headline"]
    assert h["food_cost"] == pytest.approx(cs["cogs"]) and h["revenue"] == pytest.approx(cs["revenue"])
    assert h["cost_per_head"] == pytest.approx(cs["cost_per_head"], abs=0.06)
    assert h["prev"]["food_cost"] == pytest.approx(ps["cogs"])
    # equal customers in both windows: the buffet dishes' impacts add up to the change of their cost
    assert d["buffet"]["net"] == pytest.approx(240000 - 72000 + 7200)
    assert h["per_head_effect"] == pytest.approx(h["food_cost"] - h["prev"]["food_cost"])
    assert h["per_head_effect"] == pytest.approx(d["buffet"]["net"] + (1500 - 1200) * 40)     # + the à-la-carte dish's cost


def test_eat_down_and_one_row_per_dish_name(mb):
    d = mb.get("/api/menu/action-board", params=RANGE).json()
    eat, down = _rows(d, "eat"), _rows(d, "down")
    g = eat["ปลาแซลมอนย่าง"]
    assert g["impact"] == pytest.approx(240000) and g["cause"] == "eat" and g["price"] == pytest.approx(0, abs=0.01)
    assert g["cost_per_head"] == pytest.approx(420000 / 18000, abs=0.01) and g["prev_cost_per_head"] == pytest.approx(10.0)
    assert g["plates_per_100"] == pytest.approx(21000 / 18000 * 100, abs=0.01) and g["prev_plates_per_100"] == pytest.approx(50.0)
    # the branches that pushed it: BranchF +฿90,000 first, then two of the five at +฿30,000, three more
    assert g["branches"][0] == {"branch": "BranchF", "impact": pytest.approx(90000)}
    assert [b["impact"] for b in g["branches"][1:]] == [pytest.approx(30000)] * 2 and g["branches_more"] == 3
    # the price tiers: both tiers ate alike, +฿120,000 each
    assert sorted(t["label"] for t in g["tiers"]) == ["฿789", "฿999"] and all(t["impact"] == pytest.approx(120000) for t in g["tiers"])
    assert down["กุ้ง"]["impact"] == pytest.approx(-72000) and down["กุ้ง"]["cause"] == "eat"
    # two codes, one dish (review principle 3)
    assert eat["ซุปมิโซะ"]["impact"] == pytest.approx(7200)
    assert sum(1 for k in ("price", "eat", "down") for r in d["buffet"]["buckets"][k] if r["name"] == "ซุปมิโซะ") == 1
    # sorted by baht, a cumulative share, the floor of 0.1 % of food cost (at least ฿1,000)
    assert [r["impact"] for r in d["buffet"]["buckets"]["eat"]] == sorted((r["impact"] for r in d["buffet"]["buckets"]["eat"]), reverse=True)
    assert d["buffet"]["buckets"]["eat"][-1]["cum_share"] == pytest.approx(100.0)
    assert d["floor"]["baht"] == pytest.approx(max(1000.0, 0.001 * d["headline"]["food_cost"]))
    assert all(r["above_floor"] for r in d["buffet"]["buckets"]["eat"])
    # no dish carries a lifecycle stage ("ใหม่"), a quadrant or a margin label any more
    blob = json.dumps(d, ensure_ascii=False)
    for k in ('"stage"', '"quadrant"', '"new"', "ใหม่"):
        assert k not in blob


def test_uncosted_dishes_are_listed_not_ranked(mb):
    d = mb.get("/api/menu/action-board", params=RANGE).json()
    names = {r["name"] for k in ("price", "eat", "down") for r in d["buffet"]["buckets"][k]}
    assert "ปานิปูริ" not in names
    u = {r["name"]: r for r in d["buffet"]["uncosted"]}
    assert u["ปานิปูริ"]["uncosted_plates"] == pytest.approx(0.3 * 18000)


def test_a_la_carte_revenue_and_gp(mb):
    a = mb.get("/api/menu/action-board", params=RANGE).json()["alc"]
    r = {x["name"]: x for x in a["rows"]}["ข้าวหน้าแซลมอน"]
    assert r["revenue"] == pytest.approx(1500 * 150) and r["cost"] == pytest.approx(1500 * 40) and r["gp"] == pytest.approx(1500 * 110)
    assert r["gp_change"] == pytest.approx((1500 - 1200) * 110) and r["gp_pct"] == pytest.approx(73.3, abs=0.1)
    assert a["revenue"] == pytest.approx(225000) and a["revenue_share"] == pytest.approx(100.0)


def test_the_board_follows_the_branch_and_the_group(mb):
    f = mb.get("/api/menu/action-board", params={**RANGE, "branch": "BranchF"}).json()
    assert f["branch"] == "BranchF" and f["headline"]["customers"] == CUST * 30
    g = _rows(f, "eat")["ปลาแซลมอนย่าง"]
    assert g["impact"] == pytest.approx(90000) and g["branches"] == [] and g["branches_more"] == 0
    m = mb.get("/api/menu/action-board", params={**RANGE, "group": "master"}).json()
    assert m["headline"]["customers"] == 5 * CUST * 30
    assert _rows(m, "eat")["ปลาแซลมอนย่าง"]["impact"] == pytest.approx(30000 * 4 + 90000)


def test_the_impact_of_a_dish():
    # a dearer plate, the same plates per head: the price part
    r = MB.impact(100, 1200, 100, 1000, 100, 1000, 100, 1000)
    assert r["cause"] == "price" and r["impact"] == pytest.approx(200) and r["eat"] == pytest.approx(0, abs=1e-6)
    # twice the customers, the same plates per head: nothing per head
    r = MB.impact(200, 2000, 200, 2000, 100, 1000, 100, 1000)
    assert r["impact"] == pytest.approx(0, abs=1e-6)
    # not sold in the base: its whole cost; not sold now: its base cost at today's customers
    assert MB.impact(50, 500, 50, 1000, 0, 0, 0, 1000) == {"impact": 500, "cause": "nobase", "eat": 500, "price": None}
    assert MB.impact(0, 0, 0, 2000, 50, 500, 50, 1000)["impact"] == pytest.approx(-1000)
    # costed in one window only: plates per head at the cost it has
    assert MB.impact(150, 1500, 150, 1000, 100, 0, 0, 1000)["impact"] == pytest.approx(500)
    assert MB.impact(1, 0, 0, 1000, 1, 0, 0, 1000) is None


# ── โอกาสเมนู ────────────────────────────────────────────────────────────────────────────────────────────────────────────
def test_opportunities_tier_fair_over_eating_in_baht_for_a_cost_role(mb):
    d = mb.get("/api/menu/opportunities", params=RANGE).json()
    bo = d["buffet_over"]
    assert d["cost_view"] is True and bo["basis"] == "tier"
    r = {x["name"]: x for x in bo["rows"]}["ปลาแซลมอนย่าง"]
    assert r["excess_plates"] == pytest.approx(3000) and r["excess_cost"] == pytest.approx(60000) and r["n_over"] == 1
    assert r["branches"][0]["branch"] == "BranchF" and r["branches"][0]["x"] == pytest.approx(2.0)
    assert bo["rows"][0]["name"] == "ปลาแซลมอนย่าง" and bo["rows"][0]["cum_share"] == pytest.approx(100.0)


def test_opportunities_a_la_carte_gap_in_revenue(mb):
    g = mb.get("/api/menu/opportunities", params=RANGE).json()["alc_gaps"]
    r = {x["name"]: x for x in g["rows"]}["ข้าวหน้าแซลมอน"]
    assert r["potential_revenue"] == pytest.approx(0.1 * CUST * 30 * 150) and r["price"] == pytest.approx(150)
    assert r["branches"] == [{"branch": "BranchF", "potential_plates": pytest.approx(300), "potential_revenue": pytest.approx(45000),
                              "rate": 0.0, "not_sold": True}]
    assert r["above_floor"] is True


def test_opportunities_follow_the_branch_with_the_chain_median(mb):
    d = mb.get("/api/menu/opportunities", params={**RANGE, "branch": "BranchF"}).json()
    r = {x["name"]: x for x in d["buffet_over"]["rows"]}["ปลาแซลมอนย่าง"]
    assert "branches" not in r and r["x"] == pytest.approx(2.0) and r["rate"] == pytest.approx(200.0) and r["normal_rate"] == pytest.approx(100.0)
    assert r["tier"]["excess_plates"] == pytest.approx(1500)
    assert {x["name"] for x in d["alc_gaps"]["rows"]} == {"ข้าวหน้าแซลมอน"} and d["alc_gaps"]["rows"][0]["not_sold"] is True
    a = mb.get("/api/menu/opportunities", params={**RANGE, "branch": "BranchA"}).json()
    assert a["buffet_over"]["rows"] == [] and a["alc_gaps"]["rows"] == []
    p = mb.get("/api/menu/opportunities", params={"kind": "range", "date_from": "2026-05-01", "date_to": "2026-05-31"}).json()
    assert p["period"]["cur"] == ["2026-05-01", "2026-05-31"] and p["buffet_over"]["rows"] == []     # nobody ate above the others in May


def test_opportunities_without_a_cost_module_carry_no_cost(mb, monkeypatch):
    import auth
    monkeypatch.setattr(auth, "request_modules", lambda request: {"menu.analysis"})
    d = mb.get("/api/menu/opportunities", params={**RANGE, "branch": "BranchE"}).json()
    assert d["cost_view"] is False
    def keys(x):
        if isinstance(x, dict):
            for k, v in x.items():
                yield k
                yield from keys(v)
        elif isinstance(x, list):
            for v in x:
                yield from keys(v)
    leaked = {k for k in keys(d) if "cost" in k and k != "cost_view"}
    assert not leaked, leaked
    p = mb.get("/api/menu/opportunities", params=RANGE).json()
    assert p["buffet_over"]["rows"][0]["excess_plates"] == pytest.approx(3000) and "excess_cost" not in p["buffet_over"]["rows"][0]


def test_a_long_window_compares_all_customers():
    w = {"cur": ("2026-01-01", "2026-06-30"), "days": 181, "complete": True}
    assert MB.TIER_MAX_DAYS < w["days"]
    assert MB._months_back("2026-06-30", True, 6) == ["2026-01", "2026-02", "2026-03", "2026-04", "2026-05", "2026-06"]
    assert MB._months_back("2026-06-19", False, 3) == ["2026-03", "2026-04", "2026-05"]


def test_the_ai_ideas_read_the_same_period(mb):
    import routers.menu as RM
    os.environ.pop("ANTHROPIC_API_KEY", None)
    out = RM.menu_ai_ideas(RM.MenuIdeasBody(**RANGE))
    assert out["generated_by"] == "fallback" and out["ideas"]
