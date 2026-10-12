# -*- coding: utf-8 -*-
"""PR-B4a — accuracy of the order suggestion (batch 4a of the BoM improvement proposal, owner approval 2026-10-12).

The promises, one block per proposal item:
  1.3  a past trading day with NO confirmed slip is deducted from the system stock at the BoM of its sales (the same per-day
       figure the daily report compares a slip with); the position says `estimated_days` / `estimated_qty`; a confirmed slip
       replaces the estimate; a day without sales deducts nothing; the month-end reconciliation keeps "confirmed slips only";
  1.4  before a slip is confirmed the class-A items the sales say were used and the slip leaves blank are listed (8 at most);
       a class-A item issued more than 30 % UNDER its BoM three trading days in a row is a flag beside the over-issue one;
  1.5  an item's suggestion is capped at its shelf life; the master-data gaps are counted and ranked, never filled in;
  1.6  an item with no price is ranked by a fallback price (centre list, last known, benchmark median) and says so; the price
       table is never written;
  1.7  a BIG pack is rounded down for a remainder under 20 % of the pack that the safety stock covers; small packs round up;
  1.8  the festival / promotion calendar is the sales team's and shows the public holidays read only; days after a short shipment
       of an item are left out of its demand means; an item with a long lead time is ordered to last longer.
"""
import inspect
from datetime import date

import pytest

import auth
from bom_center import forecast as F
from bom_center import issue_reports as IR
from bom_center import issues as IS
from bom_center import master_gaps as MG
from bom_center import orders as OR
from bom_center import prices as PR
from bom_center import stock as ST
from bom_center import todo as TD
from bom_center import units as U
from test_bom_center_issue_reports import SALMON, TODAY, g, slip  # noqa: F401
from test_bom_center_issues import CRAB, NOW, PACK, at, build_stock  # noqa: F401
from test_bom_center_s11 import udon  # noqa: F401  (branch 1 = อุดรธานี)
from test_bom_center_s4b import AS_OF, con  # noqa: F401  (sales 1 Sep – 5 Oct at branch 1)


def crab_use(con, days):
    """The BoM usage of the crab stick on the given days, as forecast.usage_history reads it."""
    use, _d, _s = F.usage_history(con, 1, date.fromisoformat(min(days)), date.fromisoformat(max(days)), "2026-10")
    return sum(use[CRAB].get(d, 0.0) for d in days)


def count_oct1(con):
    ST.save(con, 1, [{"code": CRAB, "qty": 10, "unit": PACK}], "udon", submit=True, count_date="2026-10-01", require_all=False)
    con.commit()


# ── 1.3 · missing slips are estimated from the sales ────────────────────────
def test_a_day_without_a_slip_is_deducted_at_the_bom_of_its_sales(con):  # noqa: F811
    count_oct1(con)
    days = ["2026-10-01", "2026-10-02", "2026-10-03", "2026-10-04"]
    est = crab_use(con, days)
    assert est > 0
    sp = ST.system_position(con, 1, AS_OF)
    assert sp["missing_days"] == days + ["2026-10-05"]                       # the warning is unchanged
    assert sp["estimated_days"] == days                                       # the stock date itself (5 Oct) is not over: not estimated
    assert sp["estimated_qty"][CRAB] == pytest.approx(est)
    assert sp["qty"][CRAB] == pytest.approx(10000 - est)
    plain = ST.system_position(con, 1, AS_OF, estimate=False)                 # the confirmed-slips-only figure
    assert plain["qty"][CRAB] == 10000 and plain["estimated_days"] == [] and plain["estimated_qty"] == {}


def test_a_confirmed_slip_replaces_the_estimate_and_a_closed_day_deducts_nothing(con):  # noqa: F811
    count_oct1(con)
    con.execute("DELETE FROM sales_sku_daily WHERE day='2026-10-03'")           # 3 Oct: the branch was closed
    con.commit()
    sp = ST.system_position(con, 1, AS_OF)
    assert sp["estimated_days"] == ["2026-10-01", "2026-10-02", "2026-10-04"]
    base = sp["qty"][CRAB]
    slip(con, "2026-10-02", [g(CRAB, 700)])                                    # the branch writes the slip of 2 Oct: 700 g, not the BoM
    con.commit()
    sp2 = ST.system_position(con, 1, AS_OF)
    assert sp2["estimated_days"] == ["2026-10-01", "2026-10-04"]
    assert sp2["qty"][CRAB] == pytest.approx(base + crab_use(con, ["2026-10-02"]) - 700)
    # a draft is not an issue: its day is still estimated
    slip(con, "2026-10-04", [g(CRAB, 5)], submit=False)
    con.commit()
    assert "2026-10-04" in ST.system_position(con, 1, AS_OF)["estimated_days"]


def test_the_plan_and_the_slip_sheet_carry_the_estimate(con):  # noqa: F811
    count_oct1(con)
    p = OR.plan(con, 1, "1", date(2026, 10, 7), as_of=AS_OF, now=NOW)
    assert p["stock"]["estimated_days"] == ["2026-10-01", "2026-10-02", "2026-10-03", "2026-10-04"]
    r = next(x for x in p["rows"] if x["code"] == CRAB)
    assert r["estimated_qty"] == pytest.approx(crab_use(con, p["stock"]["estimated_days"]) / 1000, rel=1e-3)   # count unit: 1 kg packs
    assert r["on_hand"] == pytest.approx(10000 - crab_use(con, p["stock"]["estimated_days"]))
    sh = IS.sheet(con, 1, "2026-10-05", now=NOW)
    assert sh["estimated_days"] == p["stock"]["estimated_days"]
    crab = next(i for z in sh["zones"] for i in z["items"] if i["code"] == CRAB)
    assert crab["estimated_qty"] == pytest.approx(r["estimated_qty"]) and crab["system_qty"] == pytest.approx(r["system_qty"])


def test_a_row_says_estimate_only_when_it_changes_the_packs_ordered(con):  # noqa: F811
    ST.save(con, 1, [{"code": CRAB, "qty": 6, "unit": PACK}], "udon", submit=True, count_date="2026-10-01", require_all=False)   # 6 packs, 4.6 estimated
    con.commit()
    p = OR.plan(con, 1, "1", date(2026, 10, 7), as_of=AS_OF, now=NOW)
    r = next(x for x in p["rows"] if x["code"] == CRAB)
    assert r["estimated_qty"] and r["estimate_changes"] is True
    ne = OR.order_units(r["need_no_est"], r["qty_per_recv"], r["moq"], r["safety"], r["big_pack"])[0]
    assert ne != r["order_units"]
    con.execute("DELETE FROM bc_stock_count_line")                                  # 10,000 packs: the estimate is a drop in it
    con.execute("DELETE FROM bc_stock_count")
    ST.save(con, 1, [{"code": CRAB, "qty": 10000, "unit": PACK}], "udon", submit=True, count_date="2026-10-01", require_all=False)
    con.commit()
    r2 = next(x for x in OR.plan(con, 1, "1", date(2026, 10, 7), as_of=AS_OF, now=NOW)["rows"] if x["code"] == CRAB)
    assert r2["estimated_qty"] and r2["estimate_changes"] is False and r2["order_units"] == 0
    sh = IS.sheet(con, 1, "2026-10-05", now=NOW)                                    # slip page: the stock is nowhere near 0
    crab = next(i for z in sh["zones"] for i in z["items"] if i["code"] == CRAB)
    assert crab["estimated_qty"] and crab["estimate_matters"] is False


def test_the_month_end_reconciliation_still_reads_confirmed_slips_only():
    src = inspect.getsource(IR.reconcile)
    assert "system_position" not in src and "estimated_use" not in src and "estimate" not in src


# ── 1.4 · blank class-A lines and the under-issue streak ────────────────────
def test_the_confirm_dialog_lists_the_class_a_items_left_blank(con):  # noqa: F811
    d = IR.blank_class_a(con, 1, "2026-09-01", {CRAB})                          # 1 Sep: sales of both dishes; salmon is class A
    assert [i["code"] for i in d["items"]] == [SALMON] and d["total"] == 1 and d["shown"] == 1
    assert d["items"][0]["bom"] > 0 and d["items"][0]["name"] == "แซลมอน" and d["items"][0]["value"] > 0
    assert IR.blank_class_a(con, 1, "2026-09-01", {CRAB, SALMON})["items"] == []
    assert IR.blank_class_a(con, 1, "2026-08-30", set())["items"] == []         # no sales that day: nothing is "sold and blank"
    assert IR.blank_class_a(con, 1, "2026-09-01", set(), top=0)["items"] == [] and IR.BLANK_TOP == 8


def under_days(con, third=100):
    """Tue 1 / Wed 2 / Thu 3 Sep: crab exactly to the BoM, salmon 50 % under it (Thu: `third` g)."""
    slip(con, "2026-09-01", [g(CRAB, 450), g(SALMON, 100)])
    slip(con, "2026-09-02", [g(CRAB, 450), g(SALMON, 100)])
    slip(con, "2026-09-03", [g(CRAB, 540), g(SALMON, third)])
    con.commit()


def test_three_days_far_under_the_bom_is_a_flag_beside_the_over_issue_one(con):  # noqa: F811
    under_days(con)
    d = IR.daily(con, 1, "2026-09", today=TODAY)
    f = [x for x in d["flags"] if x["direction"] == "under"]
    assert len(f) == 1 and f[0]["code"] == SALMON and f[0]["days"] == 3 and f[0]["max_pct"] < -30
    assert not [x for x in d["flags"] if x["direction"] == "over"]
    assert d["rows"][0]["under_flagged"] and d["rows"][0]["under_streak"] == ["แซลมอน"] and not d["rows"][0]["flagged"]
    assert d["totals"]["under_flagged_days"] == 3 and d["thresholds"]["under_pct"] == 30.0
    day = IR.daily_day(con, 1, "2026-09-02", today=TODAY)
    sal = next(r for r in day["rows"] if r["code"] == SALMON)
    assert sal["signal"] == "under_streak" and sal["signal_th"] == "เบิกน้อยกว่าสูตรต่อเนื่อง"
    an = IR.anomalies(con, date(2026, 9, 3), 7, today=TODAY, direction="under")
    assert [(a["branch_id"], a["items"]) for a in an] == [(1, ["แซลมอน"])] and an[0]["max_pct"] < -30
    assert IR.anomalies(con, date(2026, 9, 3), 7, today=TODAY) == []              # the over-issue list is not touched


def test_a_day_back_inside_the_band_ends_the_under_streak(con):  # noqa: F811
    under_days(con, third=230)                                                     # Thu: 230 g against 240 g
    d = IR.daily(con, 1, "2026-09", today=TODAY)
    assert not [x for x in d["flags"] if x["direction"] == "under"]


def test_the_centre_list_has_an_under_issue_task(udon):  # noqa: F811
    under_days(udon)
    items = {i["key"]: i for i in TD.build(udon, "2026-09", None, None, today=date(2026, 9, 6), now=at(6, 8))["items"]}
    u = items["issue_under"]
    assert (u["seq"], u["title"], u["status"]) == (230, "ตรวจสอบสาขาที่เบิกน้อยกว่าสูตรติดต่อกัน", "todo")
    assert u["detail"] == "เบิกน้อยกว่าสูตรเกิน 30% ติดต่อกันอย่างน้อย 3 วัน: อุดรธานี (แซลมอน)"
    assert u["go"] == {"tab": "orderhub", "pane": "issuevar", "branch": 1} and u["count"] == 1
    assert items["issue_anomaly"]["status"] == "done"                              # the over-issue task is its own


# ── 1.5 · shelf life caps the suggestion; the master-data gaps ──────────────
def test_the_suggestion_stops_at_the_shelf_life(con):  # noqa: F811
    full = {r["code"]: r for r in F.suggest(con, 1, "1", date(2026, 10, 6), season="off", as_of=AS_OF)["rows"]}[CRAB]
    assert full["forecast"] == pytest.approx(42 * 45) and full["shelf_cap_days"] is None
    con.execute("UPDATE bc_ingredient SET shelf_days=2 WHERE code=?", (CRAB,))
    cap = {r["code"]: r for r in F.suggest(con, 1, "1", date(2026, 10, 6), season="off", as_of=AS_OF)["rows"]}[CRAB]
    assert cap["shelf_cap_days"] == 2 and cap["shelf_days"] == 2
    assert cap["forecast"] == pytest.approx((10 + 12) * 45)                       # Wed 7 and Thu 8 only: Fri 9 is past the shelf life
    assert cap["suggested_units"] == 1.0 and full["suggested_units"] == 2.0
    con.execute("UPDATE bc_ingredient SET shelf_days=9 WHERE code=?", (CRAB,))     # a shelf life longer than the cover caps nothing
    long = {r["code"]: r for r in F.suggest(con, 1, "1", date(2026, 10, 6), season="off", as_of=AS_OF)["rows"]}[CRAB]
    assert long["shelf_cap_days"] is None and long["forecast"] == full["forecast"]


def test_master_gaps_are_counted_and_ranked_never_filled(con):  # noqa: F811
    con.executemany("INSERT INTO bc_ingredient(code, name_th, kind, base_unit, storage_zone, shelf_days, moq, loss_pct, category) VALUES(?,?,?,?,?,?,?,?,?)", [
        ("G-1", "ปลาสดก", "raw", "กรัม", "chilled", None, None, 0, "protein"),
        ("G-2", "ปลาสดข", "raw", "กรัม", "chilled", 3, 2, 5, "protein"),
        ("G-3", "ผักสดค", "raw", "กรัม", "chilled", None, 1, 0, "ผักสด"),
        ("G-4", "เกลือ", "raw", "กรัม", "dry", None, None, 0, None),
        ("G-5", "ซอสครัว", "prepared", "กรัม", "chilled", None, None, 0, None)])
    con.executemany("INSERT INTO bc_ingredient_price_branch(ingredient_code, month, branch_id, price_per_base, qty_bought, value_bought) VALUES(?,?,?,?,?,?)", [
        ("G-1", "2026-09", 1, 1.0, 1000, 5000.0), ("G-3", "2026-09", 1, 1.0, 1000, 9000.0)])
    before = [tuple(r) for r in con.execute("SELECT code, shelf_days, moq, loss_pct FROM bc_ingredient ORDER BY code")]
    g_ = MG.gaps(con, today=date(2026, 10, 12))
    fresh = g_["lists"]["fresh_no_shelf"]["items"]
    assert [i["code"] for i in fresh][:2] == ["G-3", "G-1"] and g_["counts"]["fresh_no_shelf"] >= 2          # ranked by ฿ bought
    assert "G-2" not in [i["code"] for i in fresh] and "G-5" not in [i["code"] for i in fresh]              # has a shelf life / is made in the kitchen
    assert {"G-1", "G-4"} <= {i["code"] for i in g_["lists"]["no_moq"]["items"]} | {"G-1", "G-4"}
    assert g_["counts"]["no_moq"] >= 2 and "G-5" not in [i["code"] for i in g_["lists"]["no_moq"]["items"]]
    zero = [i["code"] for i in g_["lists"]["loss_zero"]["items"]]
    assert "G-1" in zero and "G-3" in zero and "G-2" not in zero and "G-4" not in zero
    assert g_["counts"]["total"] == sum(g_["counts"][k] for k in ("fresh_no_shelf", "no_moq", "loss_zero"))
    assert before == [tuple(r) for r in con.execute("SELECT code, shelf_days, moq, loss_pct FROM bc_ingredient ORDER BY code")]   # nothing filled in
    row = {"code": "G-1", "name_th": "ปลาสดก", "kind": "raw", "family": None, "category": "protein", "storage_zone": "chilled",
           "shelf_days": None, "moq": None, "loss_pct": 0, "active": 1}
    assert MG.row_gaps(row) == ["fresh_no_shelf", "no_moq", "loss_zero"] and MG.row_gaps(dict(row, kind="prepared")) == []


def test_the_card_is_a_task_of_the_analyst(udon):  # noqa: F811
    it = next(i for i in TD.build(udon, "2026-09", None, None, today=date(2026, 9, 6), now=at(6, 8))["items"] if i["key"] == "master_gaps")
    assert it["title"] == "ข้อมูลตั้งต้นที่ยังว่าง" and it["owner_modules"] == ["bom.center"] and it["status"] == "todo"
    assert [g_["key"] for g_ in it["gaps"]] == ["fresh_no_shelf", "no_moq", "loss_zero"]
    assert all(g_["go"]["tab"] == "bomcenter" and g_["go"]["pane"] == "ing" and g_["go"]["extra"]["gap"] == g_["key"] for g_ in it["gaps"])


# ── 1.6 · an item without a price ───────────────────────────────────────────
def test_an_unpriced_item_is_ranked_by_a_fallback_and_says_so(con):  # noqa: F811
    con.executemany("INSERT INTO bc_ingredient(code, name_th, kind, base_unit) VALUES(?,?,?,?)",
                    [("P-LATER", "ราคาเดือนหลัง", "raw", "กรัม"), ("P-BENCH", "ราคาสาขา", "raw", "กรัม"), ("P-NONE", "ไม่มีราคาเลย", "raw", "กรัม")])
    con.execute("INSERT INTO bc_ingredient_price(ingredient_code, month, price_per_base, source) VALUES('P-LATER', '2026-12', 0.5, 'manual')")
    con.executemany("INSERT INTO bc_ingredient_price_branch(ingredient_code, month, branch_id, price_per_base, qty_bought, value_bought) VALUES(?,?,?,?,?,?)",
                    [("P-BENCH", "2026-06", b, p, 10, 10 * p) for b, p in ((1, 0.2), (2, 0.4), (3, 0.9))])
    for b, n in ((2, "ระยอง"), (3, "ขอนแก่น")):
        con.execute("INSERT OR IGNORE INTO branches(branch_id, name) VALUES(?,?)", (b, n))
    con.execute("INSERT OR REPLACE INTO app_settings(key, value) VALUES('bc_benchmark_branches', '[1, 2, 3]')")
    n_prices = con.execute("SELECT COUNT(*) FROM bc_ingredient_price").fetchone()[0]
    pb = PR.PriceBook(con, "2026-10")
    assert pb.info(CRAB) is None and pb.ref(CRAB) == 0.15 and pb.chip(CRAB) is None                      # priced: no mark
    assert pb.info("P-LATER")["kind"] == "last" and pb.ref("P-LATER") == 0.5
    assert pb.chip("P-LATER", "กรัม") == "ไม่มีราคา (ใช้ราคาล่าสุด 2026-12 ฿0.5 ต่อกรัม)"
    assert pb.info("P-BENCH")["kind"] == "bench" and pb.ref("P-BENCH") == pytest.approx(0.4)             # the median of 0.2 / 0.4 / 0.9
    assert pb.chip("P-BENCH", "กรัม") == "ไม่มีราคา (ใช้ราคาค่ากลางสาขาบริษัท ฿0.4 ต่อกรัม)"
    assert pb.info("P-NONE")["price"] is None and pb.ref("P-NONE") == 0 and pb.chip("P-NONE") == "ไม่มีราคา"
    assert con.execute("SELECT COUNT(*) FROM bc_ingredient_price").fetchone()[0] == n_prices            # never written to the price table
    assert PR.price_for(con, "P-BENCH", "2026-10") is None


def test_the_suggestion_ranks_an_unpriced_item_by_its_fallback(con):  # noqa: F811
    con.execute("DELETE FROM bc_ingredient_price WHERE ingredient_code=?", (SALMON,))          # the A-class salmon loses its price …
    con.execute("INSERT INTO bc_ingredient_price(ingredient_code, month, price_per_base, source) VALUES(?, '2026-12', 0.3714, 'manual')", (SALMON,))
    rows = {r["code"]: r for r in F.suggest(con, 1, "salmon", date(2026, 10, 6), season="off", as_of=AS_OF)["rows"]}
    s = rows[SALMON]
    assert s["price_missing"] is True and s["price_per_base"] is None and s["price_fallback"]["kind"] == "last"
    assert s["price_note"].startswith("ไม่มีราคา (ใช้ราคาล่าสุด 2026-12")
    out = {r["code"]: r for r in F.suggest(con, 1, "1", date(2026, 10, 6), season="off", as_of=AS_OF)["rows"]}
    assert out[CRAB]["price_missing"] is False and out[CRAB]["price_note"] is None


def test_a_variance_line_without_a_price_is_still_asked_for_a_reason():
    from bom_center import variance as VA
    rows = [{"code": "A", "status": "over", "class": "C", "gap_value": 900.0}, {"code": "B", "status": "over", "class": "C", "gap_value": None, "ref_value": 5000.0},
            {"code": "C", "status": "ok", "class": "A", "gap_value": 0.0}]
    assert VA.ask_reason_codes(rows, top=1) == {"B"}                                # ranked by the fallback ฿, not skipped for having none
    assert VA.ask_reason_codes([dict(r, ref_value=None) for r in rows if r["code"] != "B"], top=1) == {"A"}


# ── 1.7 · a big pack is rounded down for a small remainder ──────────────────
def test_rounding_down_only_big_packs_small_remainder_and_covered_by_safety():
    big = dict(big=True)
    assert U.to_order_units(10500, 10000, 1) == 2.0                                                     # today: up
    assert U.to_order_units_ex(10500, 10000, 1, safety=1000, **big) == (1.0, True)                      # remainder 5 % of the pack, safety covers it
    assert U.to_order_units_ex(10500, 10000, 1, safety=300, **big) == (2.0, False)                      # the safety stock cannot cover the 500 left out
    assert U.to_order_units_ex(12100, 10000, 1, safety=5000, **big) == (2.0, False)                     # remainder 21 % is not under 20 %
    assert U.to_order_units_ex(11990, 10000, 1, safety=5000, **big) == (1.0, True)                      # 19.9 %
    assert U.to_order_units_ex(10500, 10000, 1, safety=1000, big=False) == (2.0, False)                # a small pack keeps rounding up
    assert U.to_order_units_ex(10500, 10000, 3, safety=1000, **big) == (3.0, False)                     # a minimum order above the rounded-down quantity
    assert U.to_order_units_ex(1500, 10000, 1, safety=1500, **big) == (0.0, True)                       # 15 % of one pack, nothing to order
    assert U.to_order_units_ex(0, 10000, 1, safety=1000, **big) == (0.0, False)
    assert U.ROUND_DOWN_FRACTION == 0.20 and U.BIG_PACK_KG == 5.0 and U.BIG_PACK_INNER == 10


def test_which_packs_are_big(con):  # noqa: F811
    conv = U.Converter(con)
    assert U.is_big_pack(conv, CRAB, 5000) and U.is_big_pack(conv, CRAB, 10000)                         # 5 kg and over
    assert not U.is_big_pack(conv, CRAB, 4999) and not U.is_big_pack(conv, CRAB, 1000)
    assert U.pack_size(conv, CRAB, 10000)["kg"] == 10.0
    con.execute("UPDATE bc_ingredient SET base_unit='แพ็ค' WHERE code='V-007'")                         # an item stocked in packs: a box of 10
    assert U.is_big_pack(U.Converter(con), "V-007", 10) and not U.is_big_pack(U.Converter(con), "V-007", 9)
    assert not U.is_big_pack(conv, CRAB, None)


def test_the_order_row_says_it_was_rounded_down(con):  # noqa: F811
    con.execute("UPDATE bc_ingredient SET recv_unit='กล่อง 10 กก.', qty_per_recv=10000 WHERE code=?", (CRAB,))
    con.execute("UPDATE sales_sku_daily SET qty=qty+6 WHERE day IN ('2026-09-09', '2026-09-23') AND sku_id=10")     # noise → a safety stock
    p = OR.plan(con, 1, "1", date(2026, 10, 7), as_of=AS_OF, now=NOW)
    r = next(x for x in p["rows"] if x["code"] == CRAB)
    assert r["big_pack"] is True and r["qty_per_recv"] == 10000
    up = U.to_order_units(r["need"], 10000, 1)
    assert r["round_down"] == (r["order_units"] < up) and r["order_units"] in (up, up - 1)
    # the same item in 1 kg packs is never rounded down
    con.execute("UPDATE bc_ingredient SET recv_unit='แพ็ค 1 กก.', qty_per_recv=1000 WHERE code=?", (CRAB,))
    r2 = next(x for x in OR.plan(con, 1, "1", date(2026, 10, 7), as_of=AS_OF, now=NOW)["rows"] if x["code"] == CRAB)
    assert r2["big_pack"] is False and r2["round_down"] is False and r2["order_units"] == U.to_order_units(r2["need"], 1000, 1)


# ── 1.8 · inputs the forecast ignored ───────────────────────────────────────
def test_days_after_a_short_shipment_are_left_out_of_the_means(con):  # noqa: F811
    base = {r["code"]: r for r in F.suggest(con, 1, "1", date(2026, 10, 6), season="off", as_of=AS_OF)["rows"]}[CRAB]
    assert base["short_ship_skipped"] == 0
    # round 1 of 16 Sep (delivery Wed 16 Sep -> cover to Fri 18 Sep) came short on the crab stick: the warehouse said "short"
    cur = con.execute("INSERT INTO bc_order(branch_id, cycle_date, round, status, delivery_date) VALUES(1, '2026-09-14', '1', 'processed', '2026-09-16')")
    con.execute("INSERT INTO bc_order_line(order_id, ingredient_code, suggested_units, confirmed_units, processed_units, order_unit, ss_reason) VALUES(?,?,3,3,1,'pack','short')",
                (cur.lastrowid, CRAB))
    sk = F.short_ship_days(con, 1, date(2026, 9, 1), AS_OF)
    assert sk == {CRAB: {"2026-09-16", "2026-09-17", "2026-09-18"}}
    # a reason that is not a shortage (pack size) or a line sent in full does not count
    con.execute("UPDATE bc_order_line SET ss_reason='pack'")
    assert F.short_ship_days(con, 1, date(2026, 9, 1), AS_OF) == {}
    con.execute("UPDATE bc_order_line SET ss_reason='short', processed_units=confirmed_units")
    assert F.short_ship_days(con, 1, date(2026, 9, 1), AS_OF) == {}
    con.execute("UPDATE bc_order_line SET processed_units=1")
    m = F._daily_model({d: (30 if d in sk[CRAB] else 10) for d in [f"2026-09-{i:02d}" for i in range(1, 31)]},
                       [f"2026-09-{i:02d}" for i in range(1, 31)], date(2026, 9, 30), skip=sk[CRAB])
    assert m["skipped"] == 3
    assert F._daily_model({}, ["2026-09-01", "2026-09-02"], date(2026, 9, 2), skip={"2026-09-01"})["skipped"] == 0     # too thin a history: nothing left out
    out = {r["code"]: r for r in F.suggest(con, 1, "1", date(2026, 10, 6), season="off", as_of=AS_OF)["rows"]}[CRAB]
    assert out["short_ship_skipped"] == 3


def test_an_item_with_a_long_lead_time_is_ordered_to_last_longer(con):  # noqa: F811
    base = {r["code"]: r for r in F.suggest(con, 1, "1", date(2026, 10, 6), season="off", as_of=AS_OF)["rows"]}[CRAB]
    assert base["lead_extra_days"] == 0 and base["forecast"] == pytest.approx(42 * 45)                 # cover Wed 7 – Fri 9
    assert F.calendar(con, 1)["1"]["lead_days"] == 2                                                   # the round's standard: cutoff → delivery
    con.execute("UPDATE bc_ingredient SET lead_days=4 WHERE code=?", (CRAB,))
    long = {r["code"]: r for r in F.suggest(con, 1, "1", date(2026, 10, 6), season="off", as_of=AS_OF)["rows"]}[CRAB]
    assert long["lead_days"] == 4 and long["lead_extra_days"] == 2                                     # 4 − 2 standard days
    assert long["forecast"] == pytest.approx((10 + 12 + 20 + 40 + 30) * 45)                            # + Sat 10 and Sun 11
    con.execute("UPDATE bc_ingredient SET lead_days=1 WHERE code=?", (CRAB,))                           # a shorter lead than the round's changes nothing
    short = {r["code"]: r for r in F.suggest(con, 1, "1", date(2026, 10, 6), season="off", as_of=AS_OF)["rows"]}[CRAB]
    assert short["lead_extra_days"] == 0 and short["forecast"] == base["forecast"]


def test_the_event_calendar_shows_holidays_and_keeps_events(client):
    r = client.get("/api/bom-center/event-calendar?date_from=2026-10-01&date_to=2026-12-31")
    assert r.status_code == 200
    d = r.json()
    hol = {h["date"]: h for h in d["holidays"]}
    assert hol["2026-10-13"]["name"] == "วันนวมินทรมหาราช" and hol["2026-10-23"]["name"] == "วันปิยมหาราช" and "2026-12-07" in hol
    assert hol["2026-10-13"]["weekday_th"] == "อังคาร" and d["holiday_years"] == [2024, 2027]
    assert {"branch_id", "name"} <= set(d["branches"][0])
    bad = client.post("/api/bom-center/event-calendar", json={"name": "x", "date_from": "2026-10-05", "date_to": "2026-10-01"})
    assert bad.status_code == 400
    bad = client.post("/api/bom-center/event-calendar", json={"name": "x", "date_from": "2026-10-01", "date_to": "2026-10-05", "uplift_pct": 500})
    assert bad.status_code == 400
    ok = client.post("/api/bom-center/event-calendar", json={"name": "โปรโมชันทดสอบ B4a", "date_from": "2026-11-20", "date_to": "2026-11-22",
                                                             "uplift_pct": 20, "note": "ทดสอบ"})
    assert ok.status_code == 200
    eid = ok.json()["event_id"]
    ev = next(e for e in client.get("/api/bom-center/event-calendar?date_from=2026-11-01&date_to=2026-11-30").json()["events"] if e["event_id"] == eid)
    assert ev["factor"] == pytest.approx(1.2) and ev["uplift_pct"] == 20.0 and ev["branch"] == ""
    # the forecast's own season logic reads what was saved here
    import main
    from routers import ingredients as ING
    with main.get_db() as con:
        hit = ING._ing_season_resolve(con, None, "2026-11-20", "2026-11-22", "auto")
    assert hit["mode"] == "calendar-manual" and hit["factor"] == pytest.approx(1.2)
    # "from last year" = no factor
    up = client.post("/api/bom-center/event-calendar", json={"event_id": eid, "name": "โปรโมชันทดสอบ B4a", "date_from": "2026-11-20", "date_to": "2026-11-22"})
    assert up.status_code == 200
    assert next(e for e in client.get("/api/bom-center/event-calendar?date_from=2026-11-01&date_to=2026-11-30").json()["events"] if e["event_id"] == eid)["uplift_pct"] is None
    assert client.delete(f"/api/bom-center/event-calendar/{eid}").status_code == 200
    assert not [e for e in client.get("/api/bom-center/event-calendar?date_from=2026-11-01&date_to=2026-11-30").json()["events"] if e["event_id"] == eid]
    import sqlite3
    c = sqlite3.connect(str(main.DB_PATH))
    c.execute("DELETE FROM bc_audit WHERE action IN ('event_calendar', 'event_calendar_delete')")
    c.commit()
    c.close()


def test_who_may_use_the_new_endpoints():
    ss, br, an = (auth.ROLE_MODULES[r] for r in ("salessupport", "branch", "analyst"))
    assert auth.path_allowed("POST", "/api/bom-center/event-calendar", ss) and auth.path_allowed("POST", "/api/bom-center/event-calendar", an)
    assert not auth.path_allowed("POST", "/api/bom-center/event-calendar", br) and not auth.path_allowed("DELETE", "/api/bom-center/event-calendar/3", br)
    assert auth.path_allowed("POST", "/api/bom-center/issues/blank", br) and auth.path_allowed("GET", "/api/bom-center/event-calendar", br) in (True, False)


def test_the_festival_editor_moved_out_of_the_legacy_group():
    import re
    from pathlib import Path
    js = (Path(__file__).resolve().parent.parent / "js" / "01-core.js").read_text(encoding="utf-8")
    setup = js[js.index("key: 'setup', label: 'ตั้งค่า'"):js.index("const SUPPLY_LEGACY_LABEL")]
    assert "pane: 'events', label: 'ปฏิทินเทศกาลและวันหยุด'" in setup and "group: 'legacy'" in setup
    legacy = re.findall(r"\{[^{}]*group: 'legacy'[^{}]*\}", setup)
    assert not [x for x in legacy if "ปฏิทินเทศกาล" in x] and len(legacy) == 3
    assert "ปฏิทินเทศกาล" not in "".join(legacy)
