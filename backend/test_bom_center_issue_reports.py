# -*- coding: utf-8 -*-
"""PR-J3 — the reports that set the daily issue slips (ใบตัดเบิก) against the other measures of the same stock.

The promises:
  * the BoM of a day is cached (bcd_demand_day + _meta) and rebuilt only when the sales of that day or the
    release in force changed, or drop_bom_days() was called; all stale days cost ONE usage_history call, and the
    cache is committed only when the caller had no transaction open;
  * daily: per trading day the CONFIRMED issue (used + waste, ฿) against the BoM (฿); a draft day has no
    comparison; a class-A item (or the total) more than 20 % over the BoM on 3 consecutive trading days is one flag;
  * reconcile: system stock = begin + received + claims − confirmed issues; diff = count − system; status by the
    class threshold; an item missing from either count is 'not_counted'; a reason clears needs_reason;
  * reading is open to the supply roles, Ops and Accounting; a branch login is pinned to its own branch;
  * bcd_demand_day_meta is derived: a DB swap does not carry it across.
"""
import io
import sqlite3
from datetime import date, datetime

import pytest
from openpyxl import load_workbook
from conftest import dl_name

import auth
from bom_center import forecast as F
from bom_center import issue_reports as IR
from bom_center import issues as IS
from bom_center import recipes as R
from bom_center import release as RL
from bom_center import snapshot_v0 as V0
from bom_center import stock as ST
from bom_center import variance as VA
from bom_center.clock import BKK
from test_bom_center_issue_summary import TOKEN, _login, _restore, _seed, _snap, auth_on  # noqa: F401
from test_bom_center_s4b import con  # noqa: F401  (sales 1 Sep – 5 Oct at branch 1: Tue/Wed 10 plates, Thu 12 …)

CRAB, SALMON, VEG = "13-01-009", "02-01-001", "V-007"
TODAY = date(2026, 10, 6)


def at(d, h=21, month=9):
    return datetime(2026, month, d, h, 0, tzinfo=BKK)


def slip(con, day, lines, submit=True):
    d = date.fromisoformat(day)
    return IS.save(con, 1, day, lines, "udon", submit=submit, now=at(d.day, 21, d.month))


def g(code, used=0, waste=0, **kw):
    return {"code": code, "qty_used": used, "qty_waste": waste, "unit": "กรัม", **kw}


@pytest.fixture
def counted_calls(monkeypatch):
    """Every walk over the sales (usage_history is uncached in tests) as (start, end)."""
    calls = []
    real = F._usage_history_uncached
    monkeypatch.setattr(F, "_usage_history_uncached", lambda c, b, s, e, m: (calls.append((s.isoformat(), e.isoformat())),
                                                                              real(c, b, s, e, m))[1])
    return calls


def n_cached(con, bid=1):
    return (con.execute("SELECT COUNT(*) FROM bcd_demand_day_meta WHERE branch_id=?", (bid,)).fetchone()[0],
            con.execute("SELECT COUNT(*) FROM bcd_demand_day WHERE branch_id=?", (bid,)).fetchone()[0])


# ── 1 · the BoM day cache ────────────────────────────────────────────────────
def test_the_bom_of_a_day_is_built_once_and_read_back_from_the_cache(con, counted_calls):  # noqa: F811
    a = IR.bom_days(con, 1, "2026-09-01", "2026-09-30", "2026-09")
    assert len(counted_calls) == 1 and counted_calls[0] == ("2026-09-01", "2026-09-30")
    assert sorted(a) == [f"2026-09-{d:02d}" for d in range(1, 31)]
    assert a["2026-09-01"] == {CRAB: pytest.approx(450.0), SALMON: pytest.approx(200.0)}        # Tue: 10 plates (45 g / 20 g)
    assert a["2026-09-03"] == {CRAB: pytest.approx(540.0), SALMON: pytest.approx(240.0)}        # Thu: 12 plates
    assert n_cached(con) == (30, 60) and not con.in_transaction                                # committed: it started outside
    b = IR.bom_days(con, 1, "2026-09-01", "2026-09-30", "2026-09")
    assert b == a and len(counted_calls) == 1                                                  # read back, no new walk
    sub = IR.bom_days(con, 1, "2026-09-10", "2026-09-12", "2026-09")
    assert sorted(sub) == ["2026-09-10", "2026-09-11", "2026-09-12"] and len(counted_calls) == 1
    assert IR.bom_days(con, 1, "2026-09-30", "2026-09-01", "2026-09") == {}
    assert IR.bom_days(con, 2, "2026-09-01", "2026-09-30", "2026-09") == {}                    # another branch: no sales


def test_only_the_days_whose_sales_changed_are_rebuilt_in_one_call(con, counted_calls):  # noqa: F811
    IR.bom_days(con, 1, "2026-09-01", "2026-09-30", "2026-09")
    con.execute("UPDATE sales_sku_daily SET qty = qty + 10 WHERE branch_id=1 AND sku_id=10 AND day IN ('2026-09-03', '2026-09-20')")
    con.commit()
    out = IR.bom_days(con, 1, "2026-09-01", "2026-09-30", "2026-09")
    assert counted_calls[1:] == [("2026-09-03", "2026-09-20")]                                 # ONE call over the stale span
    assert out["2026-09-03"][CRAB] == pytest.approx(540 + 450)                                  # 10 more crab plates
    assert out["2026-09-04"][CRAB] == pytest.approx(20 * 45)                                    # a fresh day is untouched
    IR.bom_days(con, 1, "2026-09-01", "2026-09-30", "2026-09")
    assert len(counted_calls) == 2
    con.execute("DELETE FROM sales_sku_daily WHERE branch_id=1 AND day='2026-09-15'")           # sales removed: the day goes
    con.commit()
    out = IR.bom_days(con, 1, "2026-09-01", "2026-09-30", "2026-09")
    assert "2026-09-15" not in out and n_cached(con)[0] == 29 and len(counted_calls) == 2


def test_a_new_release_rebuilds_and_drop_bom_days_forgets(con, counted_calls):  # noqa: F811
    con.execute("INSERT INTO sku_cost(sku_id, unit_cost) VALUES(10, 5.0)")
    V0.build_v0(con)
    first = IR.bom_days(con, 1, "2026-10-01", "2026-10-05", "2026-10")
    assert len(counted_calls) == 1 and first["2026-10-01"][CRAB] == pytest.approx(12 * 45)       # Thu 1 Oct: 12 plates
    rid = R.get_or_create_draft(con, "fc", "2026-10", "2026-07", "a")
    R.save_lines(con, rid, "OTHBFSSM00020", [{"ingredient_code": CRAB, "qty": 60, "unit": "กรัม"}], "a")
    for r in RL.run_checks(con, rid)["rows"]:
        if r["check_code"] == "C05":
            RL.accept(con, rid, "C05", r["ref"], "ok", "a")
    RL.do_release(con, rid, "a", backdate_reason="test", today=date(2026, 10, 6))
    after = IR.bom_days(con, 1, "2026-10-01", "2026-10-05", "2026-10")
    assert len(counted_calls) == 2                                                              # the release in force changed
    assert after["2026-10-01"][CRAB] == pytest.approx(first["2026-10-01"][CRAB] * 60 / 45)      # 60 g from the release, not 45 g
    assert {r[0] for r in con.execute("SELECT release_id FROM bcd_demand_day_meta WHERE branch_id=1")} == {rid}
    IR.bom_days(con, 1, "2026-10-01", "2026-10-05", "2026-10")
    assert len(counted_calls) == 2
    # drop_bom_days: one branch, then everything
    con.execute("INSERT INTO bcd_demand_day_meta(branch_id, day, release_id, sales_rows, sales_qty) VALUES(2, '2026-10-01', NULL, 1, 1)")
    con.execute("INSERT INTO bcd_demand_day(branch_id, day, ingredient_code, qty_base) VALUES(2, '2026-10-01', 'X', 1)")
    IR.drop_bom_days(con, 2)
    assert n_cached(con, 2) == (0, 0) and n_cached(con, 1)[0] == 5
    IR.drop_bom_days(con)
    assert n_cached(con, 1) == (0, 0)
    IR.bom_days(con, 1, "2026-10-01", "2026-10-05", "2026-10")
    assert len(counted_calls) == 3


def test_the_cache_commits_only_when_the_caller_had_no_transaction_open(con, counted_calls):  # noqa: F811
    con.commit()
    IR.bom_days(con, 1, "2026-09-01", "2026-09-02", "2026-09", commit=False)
    assert con.in_transaction                                                                   # left to the caller
    con.rollback()
    assert n_cached(con) == (0, 0)
    con.execute("INSERT INTO bc_audit(at, who, action) VALUES('x', 'y', 'z')")                  # the caller's own work in flight
    IR.bom_days(con, 1, "2026-09-01", "2026-09-02", "2026-09")
    assert con.in_transaction                                                                   # not committed under it
    con.rollback()
    assert n_cached(con) == (0, 0) and con.execute("SELECT COUNT(*) FROM bc_audit WHERE action='z'").fetchone()[0] == 0
    IR.bom_days(con, 1, "2026-09-01", "2026-09-02", "2026-09")
    assert not con.in_transaction and n_cached(con)[0] == 2


def test_the_router_drops_the_bom_days_next_to_every_usage_cache_clear():
    import inspect
    from routers import bom_center as RB
    for fn in (RB.bc_release_do, RB.bc_release_retire, RB.bc_release_new_draft, RB.bc_release_submit_review,
               RB.bc_release_send_back, RB.bc_recipe_save, RB.bc_recipes_map, RB.bc_recipes_import_flat,
               RB.bc_recipes_import_cost_rw, RB.bc_code_review_decide, RB.bc_units_save, RB.bc_ingredients_save,
               RB.bc_ingredients_seed, RB.bc_ingredients_import, RB.bc_prep_save, RB.bc_prep_import_semi,
               RB.bc_prep_import_flat):
        src = inspect.getsource(fn)
        assert "clear_usage_cache()" in src and "_drop_bom_days()" in src, fn.__name__
    links = [fn for name, fn in inspect.getmembers(RB, inspect.isfunction)
             if "set_link(" in inspect.getsource(fn) and "clear_usage_cache()" in inspect.getsource(fn)]
    assert links and all("_drop_bom_days()" in inspect.getsource(fn) for fn in links)
    for fn in (RB.bc_order_save, RB.bc_issue_save, RB.bc_order_receive):                         # the rush: never a flush
        assert "_drop_bom_days" not in inspect.getsource(fn), fn.__name__


# ── 2 · the daily issue vs the BoM ───────────────────────────────────────────
def three_salmon_days(con, third_submitted=True):
    """Tue 1 / Wed 2 / Thu 3 Sep: crab issued exactly to the BoM, salmon 30 % over it."""
    slip(con, "2026-09-01", [g(CRAB, 450), g(SALMON, 260)])
    slip(con, "2026-09-02", [g(CRAB, 450), g(SALMON, 260)])
    slip(con, "2026-09-03", [g(CRAB, 540), g(SALMON, 312)], submit=third_submitted)
    con.commit()


def test_daily_numbers_and_the_three_day_flag(con):  # noqa: F811
    three_salmon_days(con)
    d = IR.daily(con, 1, "2026-09", today=TODAY)
    assert d["branch"]["branch_id"] == 1 and d["month"] == "2026-09" and len(d["rows"]) == 30        # every trading day of September
    r1 = d["rows"][0]
    assert (r1["date"], r1["weekday_th"], r1["slip_status"], r1["slip_status_th"]) == ("2026-09-01", "อังคาร", "on_time", "ยืนยันตรงวัน")
    assert r1["used_value"] == pytest.approx(450 * 0.15 + 260 * 0.3714, abs=0.01) and r1["waste_value"] == 0
    assert r1["bom_value"] == pytest.approx(450 * 0.15 + 200 * 0.3714, abs=0.01)                          # 141.78
    assert r1["issued_value"] == pytest.approx(164.06, abs=0.01) and r1["var_value"] == pytest.approx(22.28, abs=0.01)
    assert r1["var_pct"] == 15.7                                                                    # the total stays under 20 %
    assert [o["code"] for o in r1["over_items"]] == [SALMON] and r1["over_items"][0]["var_pct"] == 30.0
    r3 = d["rows"][2]
    assert r3["bom_value"] == pytest.approx(540 * 0.15 + 240 * 0.3714, abs=0.01) and r3["var_pct"] == 15.7    # Thu: 12 plates
    assert r1["flagged"] and d["rows"][2]["flagged"] and r1["streak"] == ["แซลมอน"]
    assert len(d["flags"]) == 1
    f = d["flags"][0]
    assert (f["code"], f["kind"], f["days"], f["max_pct"], f["from"], f["to"]) == (SALMON, "item", 3, 30.0, "2026-09-01", "2026-09-03")
    r4 = d["rows"][3]                                                                               # Fri 4 Sep: no slip yet
    assert r4["slip_status"] == "missing" and r4["issued_value"] is None and r4["var_pct"] is None and r4["bom_value"] > 0
    t = d["totals"]
    assert (t["trading_days"], t["confirmed_days"], t["missing_days"], t["draft_days"], t["late_days"]) == (30, 3, 27, 0, 0)
    assert t["issued_value"] == pytest.approx(sum(r["issued_value"] for r in d["rows"][:3]), abs=0.01)
    assert t["var_pct"] == pytest.approx(15.7, abs=0.1) and t["flagged_days"] == 3
    # the day's items
    day = IR.daily_day(con, 1, "2026-09-01", today=TODAY)
    assert day["slip"]["key"] == "on_time" and day["weekday_th"] == "อังคาร" and [f["code"] for f in day["flags"]] == [SALMON]
    assert [x["code"] for x in day["rows"]] == [SALMON, CRAB]                                      # the flagged item first
    sal, crab = day["rows"]
    assert (sal["class"], sal["used"], sal["bom"], sal["var_qty"], sal["var_pct"], sal["signal"]) == ("A", 260, 200, 60, 30.0, "streak")
    assert sal["var_value"] == pytest.approx(60 * 0.3714, abs=0.01) and sal["signal_th"] == "เบิกเกินต่อเนื่อง"
    assert (crab["class"], crab["var_pct"], crab["signal"]) == ("C", 0.0, None)
    assert IR.daily_day(con, 1, "2026-09-04", today=TODAY)["rows"][0]["issued"] is None           # a day without a slip: BoM only
    assert len(IR.daily_items(con, 1, "2026-09", today=TODAY)) == 3 * 2 + 27 * 2


def test_two_days_a_draft_or_a_gap_make_no_flag(con):  # noqa: F811
    slip(con, "2026-09-01", [g(CRAB, 450), g(SALMON, 260)])
    slip(con, "2026-09-02", [g(CRAB, 450), g(SALMON, 260)])
    assert IR.daily(con, 1, "2026-09", today=TODAY)["flags"] == []                                    # two days only
    slip(con, "2026-09-03", [g(CRAB, 540), g(SALMON, 312)], submit=False)                            # the third is a DRAFT
    con.commit()
    d = IR.daily(con, 1, "2026-09", today=TODAY)
    assert d["flags"] == [] and d["rows"][2]["slip_status"] == "draft" and d["rows"][2]["issued_value"] is None
    assert d["totals"]["draft_days"] == 1 and d["totals"]["confirmed_days"] == 2
    slip(con, "2026-09-03", [g(CRAB, 540), g(SALMON, 312)])                                           # confirmed: now it is a flag
    con.commit()
    assert [f["days"] for f in IR.daily(con, 1, "2026-09", today=TODAY)["flags"]] == [3]
    # a reopened slip is a draft again and is not counted
    sid = IS.status_of(con, 1, "2026-09-02")["slip_id"]
    IS.reopen(con, sid, "ss", "คีย์ผิด", now=at(4, 9))
    con.commit()
    assert IR.daily(con, 1, "2026-09", today=TODAY)["flags"] == []


def test_a_late_slip_and_the_open_day(con):  # noqa: F811
    IS.save(con, 1, "2026-09-02", [g(CRAB, 450)], "udon", submit=True, now=at(4, 9))                  # two days late
    con.commit()
    d = IR.daily(con, 1, "2026-09", today=TODAY)
    r = d["rows"][1]
    assert r["slip_status"] == "late" and r["slip_status_th"] == "ล่าช้า" and r["late"] == 1 and d["totals"]["late_days"] == 1
    # the current month: today is listed only once it has a slip, a day with sales but no slip stays "ไม่มีใบ"
    oct_ = IR.daily(con, 1, "2026-10", today=date(2026, 10, 4))
    assert [r["date"] for r in oct_["rows"]] == ["2026-10-01", "2026-10-02", "2026-10-03"]
    IS.save(con, 1, "2026-10-04", [g(CRAB, 100)], "udon", now=datetime(2026, 10, 4, 9, 0, tzinfo=BKK))   # a draft started today
    con.commit()
    assert [r["date"] for r in IR.daily(con, 1, "2026-10", today=date(2026, 10, 4))["rows"]][-1] == "2026-10-04"
    assert IR.daily(con, 1, "2026-11", today=TODAY)["rows"] == []


def test_streaks_need_consecutive_days():
    S = IR._streaks
    assert S([("a", 25), ("b", 30), ("c", 21)]) == [{"days": 3, "from": "a", "to": "c", "max_pct": 30, "last_pct": 21}]
    assert S([("a", 25), ("b", 30)]) == [] and S([("a", 25), ("b", None), ("c", 30), ("d", 40)]) == []
    assert S([("a", 25), ("b", 30), ("c", 20.0), ("d", 40)]) == []                                  # 20 % itself is not over
    assert [r["days"] for r in S([("a", 25), ("b", 30), ("c", 21), ("d", 22), ("e", 5), ("f", 30), ("g", 30), ("h", 30)])] == [4, 3]


def test_anomalies_name_the_branch_and_the_items(con):  # noqa: F811
    three_salmon_days(con)
    out = IR.anomalies(con, date(2026, 9, 3), lookback=7)
    assert len(out) == 1 and out[0]["branch_id"] == 1 and out[0]["items"] == ["แซลมอน"] and out[0]["max_pct"] == 30.0
    assert IR.anomalies(con, date(2026, 9, 2), lookback=7) == []                                     # two days so far
    assert IR.anomalies(con, date(2026, 9, 20), lookback=7) == []                                    # the streak is older than the window


# ── 3 · system stock vs the physical count ───────────────────────────────────
def period_with_slips(con):
    """begin 1 Sep (crab 10, salmon 0, V-007 100) · +5 crab 10 Sep · claim −1 crab 12 Sep · used 5 (4 Sep) ·
    wasted 1 (6 Sep) · a DRAFT using 9 (7 Sep) · end 1 Oct (crab 6, salmon 0)."""
    ST.save(con, 1, [{"code": c, "qty": q, "unit": "กรัม"} for c, q in ((CRAB, 10), (SALMON, 0), (VEG, 100))], "udon",
            submit=True, count_date="2026-09-01")
    VA.add_receipt(con, 1, "2026-09-10", [{"code": CRAB, "qty": 5, "unit": "กรัม"}], "udon")
    r = VA.add_receipt(con, 1, "2026-09-12", [{"code": CRAB, "qty": 1, "unit": "กรัม"}], "udon", ref="CLAIM-1", source="claim")
    con.execute("UPDATE bc_receipt_line SET qty=-qty, qty_base=-qty_base WHERE receipt_id=?", (r["receipt_id"],))
    slip(con, "2026-09-04", [g(CRAB, 5)])
    slip(con, "2026-09-06", [g(CRAB, 0, 1, waste_reason="spill")])
    slip(con, "2026-09-07", [g(CRAB, 9)], submit=False)
    end = ST.save(con, 1, [{"code": c, "qty": q, "unit": "กรัม"} for c, q in ((CRAB, 6), (SALMON, 0))], "udon",
                  submit=True, count_date="2026-10-01")["count_id"]
    con.commit()
    return end


def test_reconcile_numbers_status_reason_and_totals(con):  # noqa: F811
    end = period_with_slips(con)
    r = IR.reconcile(con, 1, end)
    assert r["period"]["begin_date"] == "2026-09-01" and r["period"]["end_date"] == "2026-10-01" and r["period"]["month"] == "2026-09"
    assert r["branch"]["branch_id"] == 1 and r["status"]["receipts_confirmed"] is False
    assert [x["code"] for x in r["rows"]] == [CRAB, VEG]                                              # under first, then not counted
    assert [e["code"] for e in r["excluded"]] == [SALMON]                                              # counted at zero, nothing moved
    crab, veg = r["rows"]
    assert (crab["begin"], crab["received"], crab["claims"], crab["used"], crab["waste"]) == (10, 5, -1, 5, 1)
    assert (crab["system"], crab["count"], crab["diff"], crab["diff_pct"]) == (8, 6, -2, -25.0)        # 10 + 5 − 1 − 5 − 1 = 8
    assert crab["class"] == "C" and crab["status"] == "under"       # the only item issued: its share is 100 %, class C (±20 %) and crab["status_th"] == "นับได้น้อยกว่าระบบ"
    assert crab["diff_value"] == pytest.approx(-0.3) and crab["price_per_base"] == pytest.approx(0.15)
    assert crab["movement"] == 8 and crab["movement_gap"] == 2                                          # the counts say 8 left; slips say 6
    # batch 4b (3.3): 27 selling days of the period have no confirmed slip - the system stock cannot be judged, so the line is labelled, not asked
    assert crab["data_incomplete"] is True and crab["incomplete_why"] == "slips" and crab["needs_reason"] is False and "reason" not in crab
    assert (veg["status"], veg["status_th"], veg["count"], veg["system"], veg["diff"]) == ("not_counted", "ตรวจนับไม่ครบ", None, 100, None)
    assert veg["needs_reason"] is False
    t = r["totals"]
    assert (t["lines"], t["flagged"], t["needs_reason"], t["not_counted"], t["unpriced"]) == (2, 1, 0, 1, 0)
    assert t["system_value"] == pytest.approx(1.2) and t["count_value"] == pytest.approx(0.9)
    assert t["diff_value"] == pytest.approx(-0.3) and t["under_value"] == pytest.approx(-0.3) and t["over_value"] == 0
    sl = r["slips"]
    assert (sl["trading_days"], sl["confirmed_days"], sl["draft_days"]) == (30, 2, ["2026-09-07"])      # the draft is not issued
    assert len(sl["missing_days"]) == 27 and "2026-09-07" not in sl["missing_days"]
    # a reason clears needs_reason (the status is about the numbers and stays)
    VA.set_reason(con, 1, end, CRAB, "miscount", None, "udon")
    con.commit()
    r2 = IR.reconcile(con, 1, end)
    crab2 = r2["rows"][0]
    assert crab2["reason"] == "miscount" and crab2["reason_th"] == "ตรวจนับผิด" and crab2["needs_reason"] is False
    assert crab2["status"] == "under" and r2["totals"]["needs_reason"] == 0 and r2["totals"]["flagged"] == 1
    # the confirmation state comes from bc_variance_period
    VA.confirm_receipts(con, 1, end, "udon")
    con.commit()
    assert IR.reconcile(con, 1, end)["status"]["receipts_confirmed_by"] == "udon"
    with pytest.raises(ValueError, match="ไม่พบงวดนี้ของสาขา"):
        IR.reconcile(con, 1, end + 99)
    with pytest.raises(ValueError):
        IR.reconcile(con, 2, end)                                                                        # another branch's period


def test_reconcile_within_the_class_threshold_is_ok_and_a_surplus_is_over(con):  # noqa: F811
    ST.save(con, 1, [{"code": CRAB, "qty": 100, "unit": "กรัม"}, {"code": VEG, "qty": 100, "unit": "กรัม"},
                     {"code": SALMON, "qty": 0, "unit": "กรัม"}], "udon", submit=True, count_date="2026-09-01")
    slip(con, "2026-09-04", [g(CRAB, 40), g(VEG, 10)])
    end = ST.save(con, 1, [{"code": CRAB, "qty": 57, "unit": "กรัม"}, {"code": VEG, "qty": 120, "unit": "กรัม"},
                           {"code": SALMON, "qty": 0, "unit": "กรัม"}], "udon", submit=True, count_date="2026-10-01")["count_id"]
    con.commit()
    rows = {x["code"]: x for x in IR.reconcile(con, 1, end)["rows"]}
    assert rows[CRAB]["class"] == "B" and rows[VEG]["class"] == "C"                                  # by the baht issued: 6.00 vs 0.40
    assert (rows[CRAB]["system"], rows[CRAB]["diff"], rows[CRAB]["diff_pct"], rows[CRAB]["status"]) == (60, -3, -5.0, "ok")   # inside B's ±10 %
    assert (rows[VEG]["system"], rows[VEG]["diff"], rows[VEG]["diff_pct"], rows[VEG]["status"]) == (90, 30, 33.3, "over")
    assert rows[VEG]["status_th"] == "นับได้มากกว่าระบบ" and rows[CRAB]["needs_reason"] is False
    assert rows[VEG]["data_incomplete"] is True and rows[VEG]["needs_reason"] is False      # batch 4b (3.3): one slip in a month of selling days


# ── 4 · who may read ─────────────────────────────────────────────────────────
def test_who_may_read_the_issue_reports():
    R_ = auth.ROLE_MODULES
    can = lambda p, role, m="GET": auth.path_allowed(m, p, R_[role])  # noqa: E731
    for path in ("/api/bom-center/issue-reports/reconcile", "/api/bom-center/issue-reports/reconcile.xlsx",
                 "/api/bom-center/issue-reports/daily", "/api/bom-center/issue-reports/daily/day",
                 "/api/bom-center/issue-reports/daily.xlsx"):
        for role in ("branch", "salessupport", "analyst", "operation", "accounting"):
            assert can(path, role), (role, path)
        assert not can(path, "warehouse") and not can(path, "marketing"), path
    assert not can("/api/bom-center/issue-reports/daily", "branch", "POST")                             # read-only


# ── 5 · over HTTP ────────────────────────────────────────────────────────────
def _sql_slip(c, bid, day, code, used, status="confirmed", late=0):
    c.execute("INSERT INTO bc_issue_slip(branch_id, slip_date, status, created_by, created_at, confirmed_by, confirmed_at,"
              " late, confirm_count) VALUES(?,?,?,?,?,?,?,?,?)",
              (bid, day, status, "t", day, "t" if status == "confirmed" else None, f"{day}T14:00:00+00:00", late,
               1 if status == "confirmed" else 0))
    sid = c.execute("SELECT MAX(slip_id) FROM bc_issue_slip").fetchone()[0]
    c.execute("INSERT INTO bc_issue_line(slip_id, ingredient_code, qty_used, qty_waste, unit, qty_used_base, qty_waste_base)"
              " VALUES(?,?,?,0,'กรัม',?,0)", (sid, code, used, used))


def test_http_reports_headers_workbooks_and_branch_pinning(client, app_db, auth_on):  # noqa: F811
    import main
    from routers.bom_center import DAILY_HEADERS, DAILY_ITEM_HEADERS, RECON_HEADERS
    client.get("/api/bom-center/hub/queue")                                                           # creates the bc_* tables
    snap = _snap(main.DB_PATH)
    ids = _seed(main.DB_PATH)                                    # branch 1 + 2: counts 1 Sep / 1 Oct, +5 T-ISS1 on 10 Sep at branch 1
    c = sqlite3.connect(str(main.DB_PATH))
    c.execute("INSERT INTO bom_ingredients(sku_id, seq, ingredient_code, ingredient_name, qty_per_serving, unit, price_per_unit,"
              " ingredient_cost, ingredient_type) VALUES(2, 1, 'T-ISS1', 'ของทดสอบ ก', 0.5, 'กรัม', 0, 0, 'x')")
    c.executemany("INSERT INTO sales_sku_daily VALUES(1, ?, 2, 10, 0)", [("2026-09-07",), ("2026-09-08",)])
    _sql_slip(c, 1, "2026-09-07", "T-ISS1", 5.5)
    _sql_slip(c, 1, "2026-09-08", "T-ISS1", 9, status="draft")
    c.commit()
    c.close()
    e1, e2 = ids[1], ids[2]
    try:
        acc, mg, ss = _login("accounting1"), _login("management1"), _login("salessupport1")
        mg.post("/api/auth/users", headers=TOKEN, json={"username": "br_ir_test", "password": "Temp@12345",
                                                         "role": "branch", "branch": "BranchA"})
        br = _login("br_ir_test", "Temp@12345")
        # reconcile
        j = acc.get(f"/api/bom-center/issue-reports/reconcile?end_count_id={e1}&branch_id=1").json()
        t1 = next(x for x in j["rows"] if x["code"] == "T-ISS1")
        assert (t1["system"], t1["count"], t1["diff"], t1["status"]) == (9.5, 6, -3.5, "under") and t1["diff_pct"] == -36.8
        assert j["slips"]["trading_days"] == 2 and j["slips"]["confirmed_days"] == 1 and j["slips"]["draft_days"] == ["2026-09-08"]
        assert acc.get(f"/api/bom-center/issue-reports/reconcile?end_count_id={e1}").status_code == 400    # the centre names a branch
        assert acc.get(f"/api/bom-center/issue-reports/reconcile?end_count_id={e1 + 999}&branch_id=1").status_code == 404
        assert br.get(f"/api/bom-center/issue-reports/reconcile?end_count_id={e1}").status_code == 200       # pinned to its own
        assert br.get(f"/api/bom-center/issue-reports/reconcile?end_count_id={e2}&branch_id=2").status_code == 403
        # the reason goes through the variance route the branch already uses, and clears the need
        r = br.post("/api/bom-center/variance/reason", headers=TOKEN, json={"end_count_id": e1, "code": "T-ISS1", "reason": "miscount"})
        assert r.status_code == 200, r.text
        j = br.get(f"/api/bom-center/issue-reports/reconcile?end_count_id={e1}").json()
        assert j["totals"]["needs_reason"] == 0 and next(x for x in j["rows"] if x["code"] == "T-ISS1")["reason_th"] == "ตรวจนับผิด"
        r = acc.get(f"/api/bom-center/issue-reports/reconcile.xlsx?end_count_id={e1}&branch_id=1")
        assert r.status_code == 200 and dl_name(r).startswith("กระทบยอด_") and dl_name(r).endswith("_2026-09.xlsx")
        ws = load_workbook(io.BytesIO(r.content)).worksheets[0]
        rows = list(ws.iter_rows(values_only=True))
        assert ws.title == "กระทบยอด" and list(rows[1]) == RECON_HEADERS and len(RECON_HEADERS) == 19
        assert rows[0][0].startswith("รายงานสินค้าคงเหลือตามตัดเบิกเทียบกับสินค้านับจริง สาขาBranchA งวด 2026-09-01 ถึง 2026-10-01 (เดือน 2026-09)")
        assert "ใบตัดเบิกยืนยันแล้ว 1/2 วัน" in rows[0][0]
        row = next(x for x in rows[2:] if x[0] == "T-ISS1")
        assert row[4:15] == (10, 5, 0, 5.5, 0, 9.5, 6, -3.5, -8.75, -36.8, "นับได้น้อยกว่าระบบ") and row[15] == "ตรวจนับผิด"
        # daily
        j = acc.get("/api/bom-center/issue-reports/daily?branch_id=1&month=2026-09").json()
        assert [r["date"] for r in j["rows"]] == ["2026-09-07", "2026-09-08"]
        d7, d8 = j["rows"]
        assert (d7["bom_value"], d7["issued_value"], d7["var_value"], d7["var_pct"], d7["slip_status"]) == (12.5, 13.75, 1.25, 10.0, "on_time")
        assert d8["slip_status"] == "draft" and d8["issued_value"] is None and d8["var_pct"] is None
        assert j["flags"] == [] and j["totals"]["confirmed_days"] == 1
        assert acc.get("/api/bom-center/issue-reports/daily?month=2026-09").status_code == 400            # the centre names a branch
        assert acc.get("/api/bom-center/issue-reports/daily?branch_id=1&month=bad").status_code == 400
        assert br.get("/api/bom-center/issue-reports/daily?month=2026-09").json()["branch"]["name"] == "BranchA"
        assert br.get("/api/bom-center/issue-reports/daily?branch_id=2&month=2026-09").status_code == 403
        dd = ss.get("/api/bom-center/issue-reports/daily/day?branch_id=1&day=2026-09-07").json()
        assert dd["slip"]["key"] == "on_time" and dd["rows"][0]["code"] == "T-ISS1" and dd["rows"][0]["var_pct"] == 10.0
        assert ss.get("/api/bom-center/issue-reports/daily/day?branch_id=1&day=7-9").status_code == 400
        r = ss.get("/api/bom-center/issue-reports/daily.xlsx?branch_id=1&month=2026-09")
        assert r.status_code == 200 and dl_name(r).startswith("ผลต่างตัดเบิกรายวัน_") and dl_name(r).endswith("_2026-09.xlsx")
        wb = load_workbook(io.BytesIO(r.content))
        assert wb.sheetnames == ["ผลต่างตัดเบิกรายวัน", "รายการต่อวัน"]
        assert list(next(wb["ผลต่างตัดเบิกรายวัน"].iter_rows(min_row=2, max_row=2, values_only=True))) == DAILY_HEADERS and len(DAILY_HEADERS) == 11
        assert list(next(wb["รายการต่อวัน"].iter_rows(min_row=2, max_row=2, values_only=True))) == DAILY_ITEM_HEADERS and len(DAILY_ITEM_HEADERS) == 14
        first = list(wb["ผลต่างตัดเบิกรายวัน"].iter_rows(min_row=3, max_row=3, values_only=True))[0]
        assert first[:9] == ("2026-09-07", "จันทร์", "ยืนยันตรงวัน", 13.75, 0, 13.75, 12.5, 1.25, 10.0)
        # the cache was written by the reads, and a release-side write clears it
        cc = sqlite3.connect(str(main.DB_PATH))
        assert cc.execute("SELECT COUNT(*) FROM bcd_demand_day_meta WHERE branch_id=1").fetchone()[0] == 2
        cc.close()
        assert mg.post("/api/bom-center/units", headers=TOKEN, json=[]).status_code == 200
        cc = sqlite3.connect(str(main.DB_PATH))
        assert cc.execute("SELECT COUNT(*) FROM bcd_demand_day_meta").fetchone()[0] == 0
        cc.close()
    finally:
        c = sqlite3.connect(str(main.DB_PATH))
        c.execute("DELETE FROM sales_sku_daily WHERE day LIKE '2026-09-%'")
        c.execute("DELETE FROM bom_ingredients WHERE ingredient_code='T-ISS1'")
        c.commit()
        c.close()
        _restore(main.DB_PATH, snap)
