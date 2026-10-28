# -*- coding: utf-8 -*-
"""PR-S4 (owner 2026-10-22, docs/STOCK_FIRST_PLAN.md O4): with the recipes off (mode `stock`) the daily issue variance compares a branch
with ITSELF and with the OTHER branches, not with a recipe.

The promises:
  * ที่สาขาใช้ปกติ: the mean issued quantity of the item on the same weekday in the 4 weeks before the day (only days with a confirmed
    slip; a confirmed day without the item counts as 0), scaled by the sales of the day / the mean sales of those days; fewer than 2
    such days -> None; no sales of the day -> the mean as it is;
  * เฉลี่ยทุกสาขา: the median, over the OTHER active company branches (never a franchise branch, never the branch itself) with at
    least 14 confirmed slip days in the 28 days before, of the issued per 100,000 baht of sales, x the sales of the day; fewer than 2
    such branches -> None;
  * a day/item is over when issued > comparator x 1.2 for EITHER comparator, under when < x 0.8; 3 trading days in a row is a flag; a
    flag carries `basis` normal | median | both; the item class is by the issued baht of the month;
  * daily / daily_day / daily_items / Excel / anomalies / blank_class_a read these in stock mode and never call bom_days; mode `full` is
    what it was; the to-do items and the reminders say ปกติ instead of สูตร;
  * the branch variance page offers no รายงวด tab in stock mode and a deep link to it opens รายวัน.
"""
import io
import json
import re
import shutil
import sqlite3
import subprocess
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest
from openpyxl import load_workbook

from bom_center import close_settings as CS
from bom_center import forecast as FC
from bom_center import issue_reports as IR
from bom_center import reminders as RM
from bom_center import todo as TD
from bom_center.clock import BKK
from test_bom_center_s11 import udon  # noqa: F401  (branch 1 = อุดรธานี)
from test_bom_center_s4b import con  # noqa: F401  (sales 1 Sep - 5 Oct at branch 1)
from test_slip_forecast import CRAB, SALMON, _branch, _days, _sell, _slip

SEP1, SEP30 = date(2026, 9, 1), date(2026, 9, 30)
TODAY = date(2026, 10, 6)
SAL = "แซลมอน"


def _feed(c, bid, a, b, salmon=260.0, crab=450.0):
    for d in _days(a, b):
        _slip(c, bid, d, {CRAB: (crab, 0.0), SALMON: (salmon, 0.0)})


def _salmon_on(c, bid, a, b, grams):
    """Replace the salmon quantity of the slips of a..b."""
    c.execute("UPDATE bc_issue_line SET qty_used_base=?, qty_used=? WHERE ingredient_code=? AND slip_id IN"
              " (SELECT slip_id FROM bc_issue_slip WHERE branch_id=? AND slip_date BETWEEN ? AND ?)",
              (grams, grams, SALMON, bid, a.isoformat(), b.isoformat()))


@pytest.fixture
def world(udon):  # noqa: F811
    """Stock mode (the default), flat sales of ฿3,000 a day at branches 1 (อุดรธานี), 3 and 4 (company branches), every branch issuing
    450 g crab and 260 g salmon a day on the confirmed slips of 1-30 Sep; branch 1 is the one the tests bend."""
    c = udon
    c.execute("DELETE FROM sales_sku_daily WHERE branch_id=1")
    _sell(c, 1)
    for bid in (3, 4):
        _branch(c, bid, f"Co{bid}")
        _sell(c, bid)
    for bid in (1, 3, 4):
        _feed(c, bid, SEP1, SEP30)
    c.commit()
    assert CS.mode(c) == "stock"
    return c


def _rows(c, month="2026-09"):
    return {r["date"]: r for r in IR.daily(c, 1, month, today=TODAY)["rows"]}


def _flags(c, direction="over"):
    return [f for f in IR.daily(c, 1, "2026-09", today=TODAY)["flags"] if f["direction"] == direction]


# ── 1 · the two comparators ──────────────────────────────────────────────────────────────────
def _h(days, iss=None, sales=None):
    return {"days": set(days), "iss": iss or {}, "sales": sales or {}}


def test_normal_is_the_mean_of_the_same_weekday_scaled_by_the_sales():
    wed = date(2026, 9, 30)                                       # a Wednesday: 23, 16, 9 and 2 Sep are the four before it
    h = _h(["2026-09-23", "2026-09-16", "2026-09-09"],
           iss={"2026-09-23": {"X": 100.0, "Y": 30.0}, "2026-09-16": {"X": 200.0}, "2026-09-09": {"X": 300.0}},
           sales={"2026-09-23": 1000.0, "2026-09-16": 2000.0, "2026-09-09": 3000.0, "2026-09-30": 4000.0})
    n = IR.normal_of(h, wed)
    assert n["X"] == pytest.approx(200.0 * 4000 / 2000)           # mean 200, the day sold 2x the mean of those days
    assert n["Y"] == pytest.approx(10.0 * 2)                      # a confirmed day without the item counts as 0
    h["sales"].pop("2026-09-30")
    assert IR.normal_of(h, wed)["X"] == pytest.approx(200.0)      # no sales of the day: the mean as it is
    h["sales"]["2026-09-30"] = 0.0
    assert IR.normal_of(h, wed)["X"] == pytest.approx(200.0)      # sales 0: the same


def test_normal_needs_two_days_with_a_confirmed_slip():
    wed = date(2026, 9, 30)
    assert IR.normal_of(_h(["2026-09-23"], iss={"2026-09-23": {"X": 5.0}}), wed) is None
    assert IR.normal_of(_h(["2026-09-23", "2026-09-19"]), wed) is None           # 19 Sep is not the same weekday
    assert IR.normal_of(_h(["2026-09-02", "2026-09-23"], iss={"2026-09-02": {"X": 5.0}}), wed) == {"X": 2.5}
    assert IR.normal_of(_h(["2026-08-05", "2026-09-23"]), wed) == {}             # 8 weeks back (H8) is inside the window
    assert IR.normal_of(_h(["2026-07-29", "2026-09-23"]), wed) is None          # 9 weeks back is outside the window


def _branch_hist(per_day, sales=1000.0, days=14, start=date(2026, 9, 1)):
    ds = [(start + timedelta(days=i)).isoformat() for i in range(days)]
    return _h(ds, iss={d: {"X": per_day} for d in ds}, sales={d: sales for d in ds})


def test_median_is_the_median_of_the_other_branches_per_100k():
    day = date(2026, 9, 29)
    a, b, c3 = _branch_hist(100.0), _branch_hist(200.0), _branch_hist(300.0)     # 10,000 / 20,000 / 30,000 per ฿100,000
    assert IR.median_of([a, b, c3], day, 3000.0)["X"] == pytest.approx(20000 * 3000 / 1e5)
    assert IR.median_of([a, b], day, 3000.0)["X"] == pytest.approx(15000 * 3000 / 1e5)
    assert IR.median_of([a], day, 3000.0) is None                                 # fewer than 2 branches
    assert IR.median_of([a, b], day, None) is None and IR.median_of([a, b], day, 0.0) is None


def test_a_branch_without_14_slip_days_is_not_a_source_of_the_median():
    day = date(2026, 9, 29)
    a, b, short = _branch_hist(100.0), _branch_hist(200.0), _branch_hist(900.0, days=13)     # 13 confirmed days: source `none`
    assert IR.median_of([a, b, short], day, 1000.0)["X"] == pytest.approx(15000 * 1000 / 1e5)
    assert IR.median_of([a, short], day, 1000.0) is None
    old = _branch_hist(900.0, start=date(2026, 7, 1))                             # 14 days, all before the 28-day window
    assert IR.median_of([a, b, old], day, 1000.0)["X"] == pytest.approx(15000 * 1000 / 1e5)


def test_the_median_leaves_out_the_branch_itself_franchise_and_inactive_branches(world):
    c = world
    c.execute("INSERT INTO branches(branch_id, name, branch_type, active) VALUES(6, 'Fr6', 'franchise', 1)")
    _sell(c, 6)
    _feed(c, 6, SEP1, SEP30, salmon=9000.0)
    c.execute("INSERT INTO branches(branch_id, name, branch_type, active) VALUES(7, 'Off7', 'master', 0)")
    _sell(c, 7)
    _feed(c, 7, SEP1, SEP30, salmon=9000.0)
    _salmon_on(c, 1, SEP1, SEP30, 9000.0)                                         # the branch itself is far above the others
    c.commit()
    d = IR.daily_day(c, 1, "2026-09-29", today=TODAY)
    sal = next(r for r in d["rows"] if r["code"] == SALMON)
    assert sal["median"] == pytest.approx(260.0)                                  # branches 3 and 4 only
    assert sal["median_pct"] == pytest.approx(100 * (9000 - 260) / 260, abs=0.1)


# ── 2 · flags: over / under, basis, streaks, class A ─────────────────────────────────────────
def test_over_on_both_comparators_for_three_days_is_one_flag_with_basis_both(world):
    c = world
    _salmon_on(c, 1, date(2026, 9, 24), date(2026, 9, 26), 325.0)               # +25 % on 24-26 Sep (a Thu, Fri, Sat)
    c.commit()
    fl = _flags(c)
    assert [(f["code"], f["kind"], f["days"], f["from"], f["to"], f["basis"], f["max_pct"]) for f in fl] == \
        [(SALMON, "item", 3, "2026-09-24", "2026-09-26", "both", 25.0)]
    assert fl[0]["name"] == SAL and fl[0]["basis_th"] == "ที่สาขาใช้ปกติและเฉลี่ยทุกสาขา"
    rows = _rows(c)
    r = rows["2026-09-25"]
    assert r["flagged"] and r["streak"] == [SAL] and [o["code"] for o in r["over_items"]] == [SALMON]
    assert r["over_items"][0]["basis"] == "both" and r["over_items"][0]["var_pct"] == 25.0
    assert not rows["2026-09-27"]["flagged"] and not rows["2026-09-23"]["flagged"]
    day = IR.daily_day(c, 1, "2026-09-25", today=TODAY)
    sal = next(x for x in day["rows"] if x["code"] == SALMON)
    assert (sal["signal"], sal["basis"], sal["signal_th"]) == ("streak", "both", "เบิกเกินที่สาขาใช้ปกติและเฉลี่ยทุกสาขาต่อเนื่อง")
    assert (sal["normal"], sal["median"], sal["issued"], sal["var_qty"], sal["var_pct"], sal["median_pct"]) == (260, 260, 325, 65, 25.0, 25.0)
    assert day["rows"][0]["code"] == SALMON                                       # the flagged item first
    assert day["stock"] is True and day["flags"][0]["basis"] == "both"


def test_only_the_median_can_trigger_basis_median(world):
    c = world
    _salmon_on(c, 1, SEP1, SEP30, 325.0)                                          # the branch always issues 25 % more than the others
    c.commit()
    fl = _flags(c)
    sal = [f for f in fl if f["code"] == SALMON]
    assert len(sal) == 1 and sal[0]["basis"] == "median" and sal[0]["from"] == "2026-09-15"      # the median exists from 15 Sep (14 days of the others)
    r = _rows(c)["2026-09-20"]
    assert r["var_pct"] == 0.0 and r["median_var_pct"] == pytest.approx(14.7, abs=0.1)   # against its own normal the branch is "normal"
    sal_it = next(x for x in IR.daily_day(c, 1, "2026-09-20", today=TODAY)["rows"] if x["code"] == SALMON)
    assert sal_it["var_pct"] == 0.0 and sal_it["median_pct"] == 25.0
    assert (sal_it["signal"], sal_it["basis"], sal_it["signal_th"]) == ("streak", "median", "เบิกเกินเฉลี่ยทุกสาขาต่อเนื่อง")


def test_only_the_normal_can_trigger_basis_normal(world):
    c = world
    _salmon_on(c, 3, SEP1, SEP30, 325.0)
    _salmon_on(c, 4, SEP1, SEP30, 325.0)                                          # the others issue 325: the median is 325
    _salmon_on(c, 1, date(2026, 9, 24), date(2026, 9, 26), 325.0)                 # the branch jumps from 260 to 325
    c.commit()
    sal = [f for f in _flags(c) if f["code"] == SALMON]
    assert len(sal) == 1 and (sal[0]["basis"], sal[0]["days"]) == ("normal", 3)
    it = next(x for x in IR.daily_day(c, 1, "2026-09-24", today=TODAY)["rows"] if x["code"] == SALMON)
    assert (it["signal_th"], it["median_pct"], it["var_pct"]) == ("เบิกเกินที่สาขาใช้ปกติต่อเนื่อง", 0.0, 25.0)


def test_two_days_in_a_row_are_no_flag_and_the_gap_ends_a_streak(world):
    c = world
    _salmon_on(c, 1, date(2026, 9, 24), date(2026, 9, 25), 325.0)
    c.commit()
    assert _flags(c) == [] and IR.anomalies(c, date(2026, 9, 27), 7, today=TODAY) == []
    c.execute("DELETE FROM bc_issue_slip WHERE branch_id=1 AND slip_date='2026-09-26'")      # a day with no slip ends a run
    _salmon_on(c, 1, date(2026, 9, 27), date(2026, 9, 28), 325.0)
    c.commit()
    assert _flags(c) == []


def test_exactly_20_percent_is_not_over(world):
    c = world
    _salmon_on(c, 1, date(2026, 9, 24), date(2026, 9, 26), 312.0)                 # 260 x 1.2
    c.commit()
    assert _flags(c) == []


def test_under_for_three_days_is_an_under_streak_with_its_basis(world):
    c = world
    _salmon_on(c, 1, date(2026, 9, 24), date(2026, 9, 26), 195.0)                 # -25 %
    c.commit()
    fl = _flags(c, "under")
    assert [(f["code"], f["days"], f["basis"], f["max_pct"]) for f in fl] == [(SALMON, 3, "both", -25.0)]
    r = _rows(c)["2026-09-25"]
    assert r["under_flagged"] and r["under_streak"] == [SAL] and not r["flagged"]
    it = next(x for x in IR.daily_day(c, 1, "2026-09-25", today=TODAY)["rows"] if x["code"] == SALMON)
    assert (it["signal"], it["signal_th"]) == ("under_streak", "เบิกต่ำกว่าที่สาขาใช้ปกติและเฉลี่ยทุกสาขาต่อเนื่อง")
    out = IR.anomalies(c, date(2026, 9, 27), 7, today=TODAY, direction="under")
    assert [(a["branch_id"], a["items"], a["max_pct"]) for a in out] == [(1, [SAL], -25.0)]
    assert IR.anomalies(c, date(2026, 9, 27), 7, today=TODAY) == []


def test_the_total_has_its_own_streak(world):
    c = world
    _salmon_on(c, 1, date(2026, 9, 24), date(2026, 9, 26), 520.0)                 # +100 % of the salmon: the day total is over too
    c.commit()
    tot = [f for f in _flags(c) if f["kind"] == "total"]
    assert len(tot) == 1 and tot[0]["days"] == 3 and tot[0]["basis"] == "both" and tot[0]["name"] == "รวมทุกรายการ"


def test_class_a_is_by_the_baht_issued_in_the_month(world):
    c = world
    day = IR.daily_day(c, 1, "2026-09-25", today=TODAY)
    cls = {r["code"]: r["class"] for r in day["rows"]}
    assert cls[SALMON] == "A" and cls[CRAB] == "C"                                # 96.6 vs 67.5 baht a day: salmon makes 59 % of the ฿
    # an item that is not class A raises no item flag however far over it runs: crab (class C) issued 78 % over its normal for 3 days
    c.execute("UPDATE bc_issue_line SET qty_used_base=800, qty_used=800 WHERE ingredient_code=? AND slip_id IN"
              " (SELECT slip_id FROM bc_issue_slip WHERE branch_id=1 AND slip_date BETWEEN '2026-09-24' AND '2026-09-26')", (CRAB,))
    c.commit()
    crab = next(x for x in IR.daily_day(c, 1, "2026-09-25", today=TODAY)["rows"] if x["code"] == CRAB)
    assert crab["class"] == "C" and crab["var_pct"] == pytest.approx(77.8, abs=0.1) and crab["signal"] is None
    assert {f["code"] for f in _flags(c) if f["kind"] == "item"} == set()
    assert _rows(c)["2026-09-25"]["over_items"] == []


def test_the_normal_follows_the_sales_of_the_day(world):
    c = world
    c.execute("UPDATE sales_sku_daily SET qty=20, subtotal_ex_vat=CASE sku_id WHEN 10 THEN 2000 ELSE 4000 END"
              " WHERE branch_id=1 AND day='2026-09-29'")                          # ฿6,000 on a Tuesday that normally sells ฿3,000
    _salmon_on(c, 1, date(2026, 9, 29), date(2026, 9, 29), 520.0)
    c.execute("UPDATE bc_issue_line SET qty_used_base=900, qty_used=900 WHERE ingredient_code=? AND slip_id IN"
              " (SELECT slip_id FROM bc_issue_slip WHERE branch_id=1 AND slip_date='2026-09-29')", (CRAB,))
    c.commit()
    d = IR.daily_day(c, 1, "2026-09-29", today=TODAY)
    sal = next(r for r in d["rows"] if r["code"] == SALMON)
    assert (sal["normal"], sal["median"], sal["var_pct"], sal["median_pct"]) == (520, 520, 0.0, 0.0)
    assert d["day"]["var_pct"] == 0.0 and d["day"]["median_var_pct"] == 0.0 and not d["day"]["flagged"]


def test_early_days_have_no_comparator_and_say_so(world):
    c = world
    rows = _rows(c)
    r1, r8, r15 = rows["2026-09-01"], rows["2026-09-08"], rows["2026-09-15"]
    assert r1["normal_value"] is None and r1["median_value"] is None and r1["judged"] is False and r1["var_pct"] is None
    assert r1["no_formula"] is True and r1["no_formula_th"] == "ยังไม่มีค่าปกติให้เทียบ"
    assert r8["normal_value"] is None                                             # one earlier Tuesday only
    assert r15["normal_value"] == pytest.approx(450 * 0.15 + 260 * 0.3714, abs=0.01) and r15["judged"] and r15["var_pct"] == 0.0
    assert r15["median_value"] == pytest.approx(r15["normal_value"], abs=0.01) and r15["median_var_pct"] == 0.0
    assert rows["2026-09-14"]["median_value"] is None and rows["2026-09-15"]["median_value"] is not None     # 14 days of the others from 15 Sep
    t = IR.daily(c, 1, "2026-09", today=TODAY)["totals"]
    assert t["trading_days"] == 30 and t["confirmed_days"] == 30 and t["var_pct"] == 0.0 and t["median_var_pct"] == 0.0
    assert IR.daily(c, 1, "2026-09", today=TODAY)["thresholds"] == {"over_pct": 20.0, "streak_days": 3, "under_pct": 20.0}


# ── 3 · no recipe is read in stock mode, full mode is what it was ─────────────────────────────
def test_bom_days_and_the_recipe_history_are_never_called_in_stock_mode(world, monkeypatch):
    c = world

    def boom(*a, **k):
        raise AssertionError("the recipes were read in stock mode")
    monkeypatch.setattr(IR, "bom_days", boom)
    monkeypatch.setattr(FC, "usage_history", boom)
    _salmon_on(c, 1, date(2026, 9, 24), date(2026, 9, 26), 325.0)
    c.commit()
    assert IR.daily(c, 1, "2026-09", today=TODAY)["stock"] is True
    assert IR.daily_day(c, 1, "2026-09-25", today=TODAY)["rows"]
    assert IR.daily_items(c, 1, "2026-09", today=TODAY)
    assert IR.anomalies(c, date(2026, 9, 27), 7, today=TODAY)
    assert IR.blank_class_a(c, 1, date(2026, 9, 30), {CRAB}, today=TODAY)["items"]


def test_full_mode_still_compares_with_the_recipes(world, monkeypatch):
    c = world
    CS.set_mode(c, "t", "full")
    calls = []
    real = IR.bom_days
    monkeypatch.setattr(IR, "bom_days", lambda *a, **k: (calls.append(1), real(*a, **k))[1])
    d = IR.daily(c, 1, "2026-09", today=TODAY)
    assert calls and "stock" not in d and "normal_value" not in d["rows"][0] and "bom_value" in d["rows"][0]
    assert set(d["thresholds"]) == {"over_pct", "streak_days", "under_pct"} and d["thresholds"]["under_pct"] == 30.0
    assert "bom" in IR.daily_day(c, 1, "2026-09-25", today=TODAY)["rows"][0]
    assert "stock" not in IR.daily_day(c, 1, "2026-09-25", today=TODAY)


# ── 4 · blank class A ────────────────────────────────────────────────────────────────────────
def test_blank_class_a_lists_the_expected_class_a_items_left_off_the_slip(world):
    c = world
    out = IR.blank_class_a(c, 1, date(2026, 9, 30), {CRAB}, today=TODAY)
    assert out["stock"] is True and out["total"] == 1 and out["shown"] == 1
    it = out["items"][0]
    assert (it["code"], it["name"], it["normal"], it["base_unit"]) == (SALMON, SAL, 260.0, it["base_unit"])
    assert it["value"] == pytest.approx(260 * 0.3714, abs=0.01)
    assert IR.blank_class_a(c, 1, date(2026, 9, 30), {CRAB, SALMON}, today=TODAY)["items"] == []
    assert IR.blank_class_a(c, 1, date(2026, 9, 8), set(), today=TODAY)["items"] == []         # one earlier Tuesday: no normal yet


# ── 5 · the headers and the workbook ─────────────────────────────────────────────────────────
def test_the_stock_headers_and_the_full_headers():
    from routers.bom_center import DAILY_HEADERS, DAILY_HEADERS_STOCK, DAILY_ITEM_HEADERS, DAILY_ITEM_HEADERS_STOCK
    assert DAILY_HEADERS == ["วันที่", "วัน", "ใบตัดเบิก", "มูลค่าเบิกใช้", "มูลค่าของเสีย", "รวมเบิก (มูลค่า)", "ตามสูตร (มูลค่า)",
                             "ส่วนต่าง (มูลค่า)", "ส่วนต่าง %", "รายการของแพงที่เบิกเกินกำหนด", "เบิกเกินต่อเนื่อง"]
    assert DAILY_ITEM_HEADERS[8] == "ตามสูตร" and len(DAILY_ITEM_HEADERS) == 14
    assert "ตามสูตร (มูลค่า)" not in DAILY_HEADERS_STOCK and "ตามสูตร" not in DAILY_ITEM_HEADERS_STOCK
    assert DAILY_HEADERS_STOCK[6:11] == ["ที่สาขาใช้ปกติ (มูลค่า)", "เฉลี่ยทุกสาขา (มูลค่า)", "ส่วนต่าง (มูลค่า)", "ส่วนต่าง %", "ต่างจากเฉลี่ยทุกสาขา %"]
    assert DAILY_ITEM_HEADERS_STOCK[8:10] == ["ที่สาขาใช้ปกติ", "เฉลี่ยทุกสาขา"] and "ต่างจากเฉลี่ยทุกสาขา %" in DAILY_ITEM_HEADERS_STOCK
    assert len(DAILY_HEADERS_STOCK) == 13 and len(DAILY_ITEM_HEADERS_STOCK) == 16


def _insert_month(path, bid):
    c = sqlite3.connect(str(path))
    snap_days = [(SEP1 + timedelta(days=i)).isoformat() for i in range(20)]
    c.executemany("INSERT INTO sales_sku_daily VALUES(?,?,?,?,?)", [(bid, d, 99901, 10, 3000.0) for d in snap_days])
    ids = []
    for d in snap_days:
        cur = c.execute("INSERT INTO bc_issue_slip(branch_id, slip_date, status, created_by, created_at, updated_at, confirmed_by,"
                        " confirmed_at, confirm_count) VALUES(?,?, 'confirmed','t',?,?, 't',?,1)", (bid, d, d, d, d + "T14:00:00+00:00"))
        ids.append(cur.lastrowid)
        c.execute("INSERT INTO bc_issue_line(slip_id, ingredient_code, qty_used, qty_waste, unit, qty_used_base, qty_waste_base)"
                  " VALUES(?, 'S4-X', 5, 0, 'กรัม', 5, 0)", (cur.lastrowid,))
    c.commit()
    return c, snap_days, ids


def test_the_workbook_and_the_json_follow_the_mode(client, app_db):
    import main
    from routers.bom_center import DAILY_HEADERS, DAILY_HEADERS_STOCK, DAILY_ITEM_HEADERS, DAILY_ITEM_HEADERS_STOCK
    client.get("/api/bom-center/hub/queue")                                       # creates the bc_* tables
    probe = sqlite3.connect(str(main.DB_PATH))
    bid = probe.execute("SELECT branch_id FROM branches WHERE name<>'ALL' ORDER BY branch_id LIMIT 1").fetchone()[0]
    probe.close()
    c, days, ids = _insert_month(main.DB_PATH, bid)
    try:
        r = client.get(f"/api/bom-center/issue-reports/daily.xlsx?branch_id={bid}&month=2026-09")
        assert r.status_code == 200
        wb = load_workbook(io.BytesIO(r.content))
        assert wb.sheetnames == ["ส่วนต่างตัดเบิกรายวัน", "รายการต่อวัน"]
        rows = list(wb["ส่วนต่างตัดเบิกรายวัน"].iter_rows(values_only=True))
        assert list(rows[1]) == DAILY_HEADERS_STOCK and "ที่สาขาใช้ปกติ" in rows[0][0] and "เฉลี่ยทุกสาขา" in rows[0][0]
        assert list(next(wb["รายการต่อวัน"].iter_rows(min_row=2, max_row=2, values_only=True))) == DAILY_ITEM_HEADERS_STOCK
        j = client.get(f"/api/bom-center/issue-reports/daily?branch_id={bid}&month=2026-09").json()
        assert j["stock"] is True and "normal_value" in j["rows"][0]
        assert client.get(f"/api/bom-center/issue-reports/daily/day?branch_id={bid}&day=2026-09-20").json()["stock"] is True
        c.execute("INSERT OR REPLACE INTO app_settings(key, value) VALUES('bc_mode', 'full')")
        c.commit()
        r = client.get(f"/api/bom-center/issue-reports/daily.xlsx?branch_id={bid}&month=2026-09")
        wb = load_workbook(io.BytesIO(r.content))
        assert list(next(wb["ส่วนต่างตัดเบิกรายวัน"].iter_rows(min_row=2, max_row=2, values_only=True))) == DAILY_HEADERS
        assert list(next(wb["รายการต่อวัน"].iter_rows(min_row=2, max_row=2, values_only=True))) == DAILY_ITEM_HEADERS
        assert "stock" not in client.get(f"/api/bom-center/issue-reports/daily?branch_id={bid}&month=2026-09").json()
    finally:
        c.execute("DELETE FROM app_settings WHERE key='bc_mode'")
        c.execute("DELETE FROM sales_sku_daily WHERE sku_id=99901")
        c.executemany("DELETE FROM bc_issue_line WHERE slip_id=?", [(i,) for i in ids])
        c.executemany("DELETE FROM bc_issue_slip WHERE slip_id=?", [(i,) for i in ids])
        c.commit()
        c.close()
        FC.clear_usage_cache()


def test_the_excel_marks_the_basis_of_a_streak(world):
    from routers.bom_center import _daily_xlsx_stock
    from openpyxl import Workbook
    c = world
    _salmon_on(c, 1, date(2026, 9, 24), date(2026, 9, 26), 325.0)
    c.commit()
    d = IR.daily(c, 1, "2026-09", today=TODAY)
    items = IR.daily_items(c, 1, "2026-09", today=TODAY)
    wb = Workbook()
    resp = _daily_xlsx_stock(wb, wb.active, d, items, "2026-09")
    assert resp.status_code == 200
    ws = wb["ส่วนต่างตัดเบิกรายวัน"]
    row = next(r for r in ws.iter_rows(min_row=3, values_only=True) if r[0] == "2026-09-25")
    assert row[12] == f"{SAL}: เบิกเกินที่สาขาใช้ปกติและเฉลี่ยทุกสาขาต่อเนื่อง" and row[11] == f"{SAL} (+25.0%)"
    sig = [r[15] for r in wb["รายการต่อวัน"].iter_rows(min_row=3, values_only=True) if r[0] == "2026-09-25" and r[1] == SALMON]
    assert sig == ["เบิกเกินที่สาขาใช้ปกติและเฉลี่ยทุกสาขาต่อเนื่อง"]


# ── 6 · the to-do list and the reminders say ปกติ ─────────────────────────────────────────────
def _items(c, d, now):
    return {i["key"]: i for i in TD.build(c, "2026-09", None, None, today=d, now=now)["items"]}


def test_the_to_do_items_and_the_reminders_say_normal_not_recipe(world):
    c = world
    _salmon_on(c, 1, date(2026, 9, 24), date(2026, 9, 26), 325.0)
    c.commit()
    now = datetime(2026, 9, 28, 8, 0, tzinfo=BKK)
    an = _items(c, date(2026, 9, 28), now)["issue_anomaly"]
    assert (an["seq"], an["title"], an["status"], an["urgent"]) == (229, "ตรวจสอบสาขาที่เบิกเกินปกติติดต่อกัน", "todo", True)
    assert an["detail"] == f"เบิกเกินที่สาขาใช้ปกติหรือเฉลี่ยทุกสาขามากกว่า 20% ติดต่อกันอย่างน้อย 3 วัน: อุดรธานี ({SAL})"
    assert an["go"] == {"tab": "orderhub", "pane": "issuevar", "branch": 1} and an["count"] == 1
    text = RM.morning(c, today=date(2026, 9, 28))["text"]
    assert f"<b>ฝ่ายปฏิบัติการ</b>: เบิกเกินที่สาขาใช้ปกติหรือเฉลี่ยทุกสาขามากกว่า 20% ติดต่อกันอย่างน้อย 3 วัน: อุดรธานี ({SAL})" in text
    assert "สูตร" not in an["title"] + an["detail"] + text.split("ฝ่ายปฏิบัติการ", 1)[1]
    push = RM.centre_morning(c, today=date(2026, 9, 28), now=now)
    assert push["operation"]["lines"] == [f"เบิกเกินปกติติดต่อกัน 1 สาขา: อุดรธานี"]
    later = _items(c, date(2026, 10, 10), datetime(2026, 10, 10, 8, 0, tzinfo=BKK))["issue_anomaly"]
    assert later["status"] == "done" and later["detail"] == "ไม่พบการเบิกเกินปกติติดต่อกันใน 7 วันล่าสุด"


def test_the_under_issue_item_says_normal_too(world):
    c = world
    _salmon_on(c, 1, date(2026, 9, 24), date(2026, 9, 26), 195.0)
    c.commit()
    low = _items(c, date(2026, 9, 28), datetime(2026, 9, 28, 8, 0, tzinfo=BKK))["issue_under"]
    assert low["title"] == "ตรวจสอบสาขาที่เบิกต่ำกว่าปกติติดต่อกัน" and low["status"] == "todo"
    assert low["detail"] == f"เบิกต่ำกว่าที่สาขาใช้ปกติหรือเฉลี่ยทุกสาขาเกิน 20% ติดต่อกันอย่างน้อย 3 วัน: อุดรธานี ({SAL})"


def test_full_mode_keeps_the_recipe_words_in_the_to_do_list(world):
    c = world
    CS.set_mode(c, "t", "full")
    now = datetime(2026, 9, 28, 8, 0, tzinfo=BKK)
    it = _items(c, date(2026, 9, 28), now)
    assert it["issue_anomaly"]["title"] == "ตรวจสอบสาขาที่เบิกเกินสูตรติดต่อกัน"
    assert it["issue_under"]["title"] == "ตรวจสอบสาขาที่เบิกน้อยกว่าสูตรติดต่อกัน"


# ── 7 · the branch variance page: no รายงวด tab in stock mode ─────────────────────────────────
@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_branch_variance_tabs_drop_period_in_stock_mode():
    src = (Path(__file__).resolve().parent.parent / "js" / "14-branch.js").read_text(encoding="utf-8")
    vt = re.search(r"^const _BRN_VT = .*?;$", src, re.M).group(0)
    fn = re.search(r"^function _brnVtList\(\) \{.*?\}$", src, re.M).group(0)
    tab = re.search(r"^function brnVarTab\(k, quiet\) \{\n(.*?\n)\}", src, re.M | re.S).group(1)
    assert "if (BC_MODE === 'stock' && k === 'period') k = 'daily';" in tab
    code = f"""{vt}\n{fn}\nlet BC_MODE = 'stock';\nconst a = _brnVtList().map(x => x[0]);\nBC_MODE = 'full';\nconst b = _brnVtList().map(x => x[0]);
console.log(JSON.stringify([a, b]));"""
    out = subprocess.run(["node", "-e", code], capture_output=True, text=True, check=True).stdout.strip()
    assert out == '[["daily","recon"],["period","daily","recon"]]'


# ── 8 · the duty lines of the departments in stock mode ──────────────────────────────────────
def test_stock_mode_uses_the_duty_lines_of_analyst_and_kitchen():
    js = Path(__file__).resolve().parent.parent / "js"
    core = (js / "01-core.js").read_text(encoding="utf-8")
    assert re.search(r"key: 'ana'.*?roles: \['analyst'\], duty_stock: 'ราคา ต้นทุนต่อจาน และหน่วย'", core)
    assert re.search(r"key: 'kit'.*?roles: \['innovation'\], duty_stock: 'ทะเบียนวัตถุดิบ ต้นทุนต่อจาน และเมนู \(ดูอย่างเดียว\)'", core)   # 2026-10-28: the kitchen card has 4 pages
    for f in ("09c-mobile.js", "18-launcher.js"):                 # every place that prints a duty line asks the one helper
        src = (js / f).read_text(encoding="utf-8")
        assert "esc(d.duty)" not in src and "deptDuty(d)" in src, f
    fn = re.search(r"^function deptDuty\(d\) \{.*?\}$", core, re.M).group(0)
    if shutil.which("node"):
        code = (fn + "\nlet BC_MODE = 'stock';\nconst d = {duty: 'a', duty_stock: 'b'}, e = {duty: 'a'};\n"
                "const x = [deptDuty(d), deptDuty(e)];\nBC_MODE = 'full';\nconsole.log(JSON.stringify([x, deptDuty(d)]));")
        out = subprocess.run(["node", "-e", code], capture_output=True, text=True, check=True).stdout.strip()
        assert json.loads(out) == [["b", "a"], "a"]



def test_a_gap_inside_the_threshold_is_neutral_and_beyond_it_is_coloured():
    src = (Path(__file__).resolve().parent.parent / "js" / "13-bom-center.js").read_text(encoding="utf-8")
    fn = re.search(r"^function stkGapHtml\(.*?^\}", src, re.M | re.S).group(0)
    if not shutil.which("node"):
        pytest.skip("node is not installed")
    code = ("const _bmcNum = (v) => String(Math.round(v)); const bmcGapHtml = (v, p, k) => 'COLOURED:' + k;\n" + fn
            + "\nconsole.log(JSON.stringify([stkGapHtml(138, 3.5, 'issue_n'), stkGapHtml(-60, -19, 'issue_m'), stkGapHtml(500, 21, 'issue_n'),"
              " stkGapHtml(1, -25, 'issue_m'), stkGapHtml(1, null, 'issue_n')]));")
    out = json.loads(subprocess.run(["node", "-e", code], capture_output=True, text=True, check=True, encoding="utf-8").stdout)
    assert "ใกล้เคียงปกติ" in out[0] and "var(--text-muted)" in out[0] and "COLOURED" not in out[0]
    assert "ใกล้เคียงเฉลี่ยทุกสาขา" in out[1]
    assert out[2] == "COLOURED:issue_n" and out[3] == "COLOURED:issue_m" and out[4] == "COLOURED:issue_n"


# ── 9 · the month summary page is a recipe page ───────────────────────────────────────────────
def test_the_month_summary_is_hidden_in_stock_mode_and_the_close_ends_at_the_issue_summary(world):
    c = world
    js = (Path(__file__).resolve().parent.parent / "js" / "01-core.js").read_text(encoding="utf-8")
    assert len(re.findall(r"pane: 'month', bom: true, label: 'สรุปผลต่างรายเดือน'", js)) == 2          # the department tile and the chip
    now = datetime(2026, 9, 28, 8, 0, tzinfo=BKK)

    def steps():
        return [s["key"] for s in TD.build(c, "2026-09", None, None, today=date(2026, 9, 28), now=now)["close"]["steps"]]
    seqs = [x["seq"] for x in TD.build(c, "2026-09", None, None, today=date(2026, 9, 28), now=now)["close"]["steps"]]
    assert seqs == list(range(1, len(seqs) + 1))                                   # the steps that remain are numbered 1, 2, 3 ...
    stock = steps()
    assert "monthly_summary" not in stock and "release_in_force" not in stock and stock[-1] == "issue_posted"
    CS.set_mode(c, "t", "full")
    full = steps()
    assert full[-1] == "monthly_summary" and "release_in_force" in full


def test_the_miscount_task_opens_the_issue_summary_in_stock_mode(world):
    c = world
    t = {"rule": "miscount", "month": "2026-09", "branches": [{"branch_id": 1}]}
    assert TD._feedback_go(c, t) == {"tab": "bomcenter", "pane": "issue", "month": "2026-09", "branch": 1}
    CS.set_mode(c, "t", "full")
    assert TD._feedback_go(c, t)["pane"] == "month"
