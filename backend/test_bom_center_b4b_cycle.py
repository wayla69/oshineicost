# -*- coding: utf-8 -*-
"""PR-B4b (3.4) - cycle counts and the partial month close.

The class-A items of a branch are counted every week; everything else at the end of a quarter; every other month closes on a PARTIAL end
count (the latest weekly counts of the class-A items + the system stock for the rest, labelled "คงเหลือตามระบบ (ไม่ได้นับ)"). The
weekly counts live in their own tables (the CHECK on bc_stock_count cannot be widened in place), so no existing period moves; the partial
end count IS an ordinary 'end' count whose lines carry `basis`, so periods, variance, the reconciliation, the accounting summary and the
posted-period lock read it unchanged. One setting (bc_cycle_counts) returns every month to the full count.
"""
from datetime import date

import pytest

from bom_center import close_settings as CS
from bom_center import issue_reports as IR
from bom_center import stock as ST
from bom_center import todo as TD
from bom_center import variance as VA
from test_bom_center_issue_reports import SALMON, g, slip  # noqa: F401
from test_bom_center_issues import CRAB  # noqa: F401
from test_bom_center_s4b import con  # noqa: F401  (sales 1 Sep – 5 Oct at branch 1)

OCT1 = "2026-10-01"
NOV2 = date(2026, 11, 2)


def start_counts(con, qty=100000):
    """A full begin count on 1 Oct (crab stick and salmon 100 kg, the vegetable 100 g), slips for every selling day 1-5 Oct."""
    ST.save(con, 1, [{"code": CRAB, "qty": qty, "unit": "กรัม"}, {"code": SALMON, "qty": qty, "unit": "กรัม"}, {"code": "V-007", "qty": 100, "unit": "กรัม"}],
            "udon", submit=True, count_date=OCT1, require_all=False)
    for d in range(1, 6):
        slip(con, f"2026-10-0{d}", [g(CRAB, 450), g(SALMON, 200)])
    con.commit()


def class_a(con):
    return [it["code"] for z in ST.sheet(con, 1, mode="cycle")["zones"] for it in z["items"]]


def cycle_count(con, day, qty_by_code):
    lines = [{"code": c, "qty": q, "unit": "กรัม"} for c, q in qty_by_code.items()]
    r = ST.cycle_save(con, 1, lines, "udon", submit=True, count_date=day)
    con.commit()
    return r


def test_the_plan_says_which_count_is_due(con):  # noqa: F811
    p = ST.count_plan(con, 1, date(2026, 10, 12))
    assert p["first"] is True and p["mode"] == "full" and p["cycle_counts"] is True and p["partial_month"] is None
    start_counts(con)
    p = ST.count_plan(con, 1, date(2026, 10, 12))                       # a full count dated in this quarter exists: the weekly count is due
    assert (p["first"], p["mode"], p["full_due"], p["cycle_due"], p["partial_month"]) == (False, "cycle", False, True, None)
    cycle_count(con, "2026-10-12", {c: 90000 for c in class_a(con)})
    p = ST.count_plan(con, 1, date(2026, 10, 14))
    assert p["cycle_due"] is False and p["cycle_last"] == "2026-10-12"                # counted this week (Monday 12 Oct)
    assert ST.count_plan(con, 1, date(2026, 10, 19))["cycle_due"] is True              # the next Monday: due again
    # a new quarter: the full count is due for 14 days, then it is overdue and the weekly count goes on
    p = ST.count_plan(con, 1, date(2027, 1, 4))
    assert p["full_due"] is True and p["mode"] == "full" and p["quarter_start"] == "2027-01-01"
    p = ST.count_plan(con, 1, date(2027, 1, 20))
    assert p["full_due"] is False and p["full_overdue"] is True and p["mode"] == "cycle"
    # the month after a non-quarter month can be closed on a partial count (October closes on 1 November)
    p = ST.count_plan(con, 1, date(2026, 11, 3))
    assert p["partial_month"] == "2026-10" and p["partial_date"] == "2026-11-01"
    assert ST.count_plan(con, 1, date(2026, 10, 3))["partial_month"] is None            # September is a quarter end: a full count
    assert ST.count_plan(con, 1, date(2026, 11, 28))["partial_month"] is None           # too late to be "the close"
    # the switch off: the old rule, no flags
    CS.save(con, "analyst1", cycle=False)
    p = ST.count_plan(con, 1, date(2026, 11, 3))
    assert p["cycle_counts"] is False and p["mode"] == "full" and p["partial_month"] is None and p["cycle_due"] is False


def test_the_cycle_sheet_is_the_class_a_list_and_needs_all_of_it(con):  # noqa: F811
    start_counts(con)
    full = ST.sheet(con, 1)
    cyc = ST.sheet(con, 1, mode="cycle")
    items = [it for z in cyc["zones"] for it in z["items"]]
    assert cyc["mode"] == "cycle" and cyc["next_kind"] == "cycle" and items and all(it["class"] == "A" and it["required"] for it in items)
    assert len(items) < full["items"] and cyc["required"] == len(items) and cyc["plan"]["mode"] == "cycle"
    assert full["mode"] == "full" and full["next_kind"] == "end"
    lines = [{"code": items[0]["code"], "qty": 5, "unit": "กรัม"}]
    if len(items) > 1:
        with pytest.raises(ValueError, match="ที่ต้องนับ"):
            ST.cycle_save(con, 1, lines, "udon", submit=True, count_date="2026-10-06")
    r = ST.cycle_save(con, 1, [{"code": it["code"], "qty": 5, "unit": "กรัม"} for it in items], "udon", submit=False, count_date="2026-10-06")
    assert r["status"] == "draft" and r["kind"] == "cycle"
    assert ST.sheet(con, 1, mode="cycle")["draft"]["count_id"] == r["count_id"]
    assert ST.sheet(con, 1)["draft"] is None                                            # the month-end draft is a different thing
    r = ST.cycle_save(con, 1, [{"code": it["code"], "qty": 6, "unit": "กรัม"} for it in items], "udon", submit=True, count_date="2026-10-06")
    assert r["status"] == "submitted" and ST.cycle_draft_of(con, 1) is None
    hist = ST.cycle_history(con, 1)
    assert [h["kind"] for h in hist] == ["cycle"] and hist[0]["lines"] == len(items) and hist[0]["status"] == "submitted"
    assert ST.sheet(con, 1, mode="cycle")["last_count"]["kind"] == "cycle"
    # nothing of it reached the month-end tables
    assert con.execute("SELECT COUNT(*) FROM bc_stock_count WHERE count_date='2026-10-06'").fetchone()[0] == 0
    assert len(ST.periods(con, 1)) == 0
    with pytest.raises(ValueError, match="อนาคต"):
        ST.cycle_save(con, 1, lines, "udon", count_date="2099-01-01")
    ST.cycle_discard(con, 1, "udon")


def test_no_cycle_count_before_the_first_full_count_and_none_when_the_switch_is_off(con):  # noqa: F811
    with pytest.raises(ValueError, match="ครั้งแรก"):
        ST.cycle_save(con, 1, [{"code": CRAB, "qty": 1, "unit": "กรัม"}], "udon")
    start_counts(con)
    CS.save(con, "analyst1", cycle=False)
    with pytest.raises(ValueError, match="ปิดอยู่"):
        ST.cycle_save(con, 1, [{"code": CRAB, "qty": 1, "unit": "กรัม"}], "udon")
    with pytest.raises(ValueError, match="ปิดอยู่"):
        ST.sheet(con, 1, mode="cycle")


def test_a_weekly_count_is_the_newer_basis_of_its_item(con):  # noqa: F811
    start_counts(con)
    VA.add_receipt(con, 1, "2026-10-04", [{"code": SALMON, "qty": 1000, "unit": "กรัม"}], "udon")
    con.commit()
    a = class_a(con)
    assert SALMON in a
    before = ST.system_position(con, 1, date(2026, 10, 5))
    assert before["qty"][SALMON] == pytest.approx(100000 + 1000 - 5 * 200) and before["cycle"] == {}
    cycle_count(con, "2026-10-03", {c: 80000 for c in a})                               # the shelf had 80 kg on the morning of 3 Oct
    after = ST.system_position(con, 1, date(2026, 10, 5))
    assert after["cycle"][SALMON] == "2026-10-03"
    assert after["qty"][SALMON] == pytest.approx(80000 + 1000 - 3 * 200)                # 80 kg + the 4 Oct delivery - slips of 3, 4, 5 Oct
    if CRAB not in a:
        assert after["qty"][CRAB] == before["qty"][CRAB]                                # an item not on the weekly list keeps its month-end basis
    CS.save(con, "analyst1", cycle=False)                                               # the switch off returns to the month-end figure
    assert ST.system_position(con, 1, date(2026, 10, 5))["qty"][SALMON] == before["qty"][SALMON]


def test_a_non_quarter_month_closes_on_a_partial_count(con):  # noqa: F811
    start_counts(con)
    a = class_a(con)
    cycle_count(con, "2026-10-04", {c: 70000 for c in a})
    r = ST.partial_close(con, 1, "2026-10", "udon", today=NOV2)
    con.commit()
    assert r["created"] is True and r["date"] == "2026-11-01" and r["counted"] == len(a) and r["system"] >= 1 and r["lines"] == r["counted"] + r["system"]
    cnt = con.execute("SELECT kind, status, partial, count_date, counted_by FROM bc_stock_count WHERE count_id=?", (r["count_id"],)).fetchone()
    assert cnt == ("end", "submitted", 1, "2026-11-01", "udon")
    basis = dict(con.execute("SELECT ingredient_code, basis FROM bc_stock_count_line WHERE count_id=?", (r["count_id"],)))
    assert {c for c, b in basis.items() if b == "cycle"} == set(a) and "system" in basis.values()
    # an item that is not on the weekly list is the system stock carried to 31 Oct: begin + received - confirmed slips
    sp = ST.system_position(con, 1, date(2026, 10, 31), issues_through=date(2026, 10, 31), estimate=False)
    qty = dict(con.execute("SELECT ingredient_code, qty_base FROM bc_stock_count_line WHERE count_id=?", (r["count_id"],)))
    for c in basis:
        assert qty[c] == pytest.approx(max(sp["qty"][c], 0.0))
    # idempotent: closing again returns the same count and writes nothing
    again = ST.partial_close(con, 1, "2026-10", "udon", today=NOV2)
    assert again["created"] is False and again["count_id"] == r["count_id"]
    assert con.execute("SELECT COUNT(*) FROM bc_stock_count WHERE kind='end' AND partial=1").fetchone()[0] == 1
    # the period (1 Oct - 1 Nov) reads like any other
    assert [(b["date"], e["date"]) for b, e in ST.periods(con, 1)] == [(OCT1, "2026-11-01")]
    p = VA.period(con, 1, r["count_id"])
    assert p["partial"] is True and p["system_basis"] >= 1 and p["end_date"] == "2026-11-01"
    sys_rows = [x for x in p["rows"] if x.get("system_basis")]
    assert sys_rows and all(x["basis_th"] == "คงเหลือตามระบบ (ไม่ได้นับ)" and x["ask_reason"] is False for x in sys_rows)
    # the accounting summary: a partial period says so, labels each line, and the used quantity of a system line IS the confirmed slips
    s = VA.issue_summary(con, 1, r["count_id"])
    assert s["period"]["partial"] is True and "คงเหลือตามระบบ (ไม่ได้นับ)" in s["period"]["partial_th"] and s["totals"]["system_basis"] >= 1
    sysl = [ln for ln in s["lines"] if ln["end_basis"] == "system"]
    assert sysl and all(ln["basis_th"] == "คงเหลือตามระบบ (ไม่ได้นับ)" for ln in sysl)
    for ln in s["lines"]:
        if ln["code"] == CRAB and CRAB not in a:
            assert ln["used"] == pytest.approx(5 * 450)
    # the reconciliation: a system line cannot differ from itself; a weekly-counted line carries the difference the count found
    rc = IR.reconcile(con, 1, r["count_id"])
    assert rc["partial"] is True and rc["totals"]["system_basis"] >= 1
    for x in rc["rows"]:
        if x.get("system_basis"):
            assert x["diff"] == pytest.approx(0, abs=1e-6) and x["status"] == "ok" and x["basis_th"] == "คงเหลือตามระบบ (ไม่ได้นับ)"
        elif x["code"] in a:
            assert x["end_basis"] == "cycle"
    # the posted-period lock still works on a partial period
    VA.confirm_receipts(con, 1, r["count_id"], "udon")
    VA.set_posted(con, r["count_id"], True, "acc1")
    con.commit()
    with pytest.raises(ValueError, match="บัญชีบันทึก"):
        VA.add_receipt(con, 1, "2026-10-20", [{"code": CRAB, "qty": 1, "unit": "กรัม"}], "udon")
    with pytest.raises(ValueError, match="บัญชีบันทึก"):
        VA.confirm_receipts(con, 1, r["count_id"], "udon", undo=True)
    # the next period starts on that count: its begin figures are the partial ones
    assert VA._count_lines(con, r["count_id"])[CRAB] == pytest.approx(max(sp["qty"][CRAB], 0.0))


def test_a_partial_close_is_refused_where_the_rule_says(con):  # noqa: F811
    start_counts(con)
    cycle_count(con, "2026-10-04", {c: 70000 for c in class_a(con)})
    with pytest.raises(ValueError, match="สิ้นไตรมาส"):
        ST.partial_close(con, 1, "2026-09", "udon", today=NOV2)                           # September is a quarter end
    with pytest.raises(ValueError, match="ปิดงวดได้ตั้งแต่"):
        ST.partial_close(con, 1, "2026-10", "udon", today=date(2026, 10, 30))
    with pytest.raises(ValueError, match="YYYY-MM"):
        ST.partial_close(con, 1, "2026-13", "udon", today=NOV2)
    with pytest.raises(ValueError, match="ไม่พบสาขา"):
        ST.partial_close(con, 99, "2026-10", "udon", today=NOV2)
    # a selling day without a confirmed slip: refused unless the person accepts it (the items not counted would show too little use)
    con.execute("DELETE FROM bc_issue_line WHERE slip_id IN (SELECT slip_id FROM bc_issue_slip WHERE slip_date='2026-10-03')")
    con.execute("DELETE FROM bc_issue_slip WHERE slip_date='2026-10-03'")
    con.commit()
    with pytest.raises(ValueError, match="ใบตัดเบิกที่ยืนยัน"):
        ST.partial_close(con, 1, "2026-10", "udon", today=NOV2)
    r = ST.partial_close(con, 1, "2026-10", "udon", today=NOV2, accept_missing_slips=True)
    assert r["created"] is True and r["missing_slip_days"] == ["2026-10-03"]
    CS.save(con, "analyst1", cycle=False)
    with pytest.raises(ValueError, match="ปิดอยู่"):
        ST.partial_close(con, 1, "2026-10", "udon", today=NOV2)


def test_a_full_count_at_the_close_is_returned_as_the_close(con):  # noqa: F811
    start_counts(con)
    full = ST.save(con, 1, [{"code": CRAB, "qty": 1, "unit": "กรัม"}], "udon", submit=True, count_date="2026-11-01", require_all=False)
    con.commit()
    r = ST.partial_close(con, 1, "2026-10", "udon", today=NOV2)
    assert r["created"] is False and r["count_id"] == full["count_id"] and r["partial"] is False


def test_a_later_count_blocks_a_late_partial_close(con):  # noqa: F811
    start_counts(con)
    ST.save(con, 1, [{"code": CRAB, "qty": 1, "unit": "กรัม"}], "udon", submit=True, count_date="2026-11-20", require_all=False)
    con.commit()
    with pytest.raises(ValueError, match="หลังวันที่ปิดงวด"):
        ST.partial_close(con, 1, "2026-10", "udon", today=date(2026, 11, 25))


def test_the_close_stepper_and_the_branch_list_follow_the_cycle(con):  # noqa: F811
    start_counts(con)
    d = TD.build(con, branch_id=1, today=date(2026, 10, 14))
    keys = {i["key"].split(":")[0]: i for i in d["items"]}
    assert keys["cycle_count"]["title"] == "ตรวจนับหมุนเวียนกลุ่มมูลค่าสูง" and keys["cycle_count"]["status"] == "todo"
    assert keys["cycle_count"]["owner_modules"] == ["branch.stock"] and "full_count" not in keys
    cycle_count(con, "2026-10-12", {c: 70000 for c in class_a(con)})
    assert {i["key"].split(":")[0]: i for i in TD.build(con, branch_id=1, today=date(2026, 10, 14))["items"]}["cycle_count"]["status"] == "done"
    # the quarter turns: the full count is a task of its own
    keys = {i["key"].split(":")[0]: i for i in TD.build(con, branch_id=1, today=date(2027, 1, 3))["items"]}
    assert keys["full_count"]["title"] == "ตรวจนับครบทุกรายการสิ้นไตรมาส" and keys["full_count"]["status"] == "todo" and "cycle_count" not in keys
    # a non-quarter month ends on the partial count: the step says so, then it is done
    d = TD.build(con, branch_id=1, month="2026-10", today=NOV2)
    steps = {s["key"]: s for s in d["close"]["steps"]}
    assert steps["end_count"]["title"].startswith("ปิดงวดด้วยการตรวจนับหมุนเวียน") and steps["end_count"]["status"] == "todo"
    assert "ยังไม่ปิดงวดด้วยการตรวจนับหมุนเวียน" in steps["end_count"]["branches"][0]["detail"]
    assert {i["key"].split(":")[0]: i for i in d["items"]}["partial_close"]["title"].startswith("ปิดงวดเดือน")
    ST.partial_close(con, 1, "2026-10", "udon", today=NOV2)
    con.commit()
    steps = {s["key"]: s for s in TD.build(con, branch_id=1, month="2026-10", today=NOV2)["close"]["steps"]}
    assert steps["end_count"]["status"] == "done" and "คงเหลือตามระบบ" in steps["end_count"]["branches"][0]["detail"]
    # a quarter-end month keeps the full-count wording; so does the old flow with the switch off
    steps = {s["key"]: s for s in TD.build(con, branch_id=1, month="2026-09", today=NOV2)["close"]["steps"]}
    assert steps["end_count"]["title"].startswith("ผลตรวจนับปลายงวด")
    CS.save(con, "analyst1", cycle=False)
    d = TD.build(con, branch_id=1, month="2026-10", today=NOV2)
    assert {s["key"]: s for s in d["close"]["steps"]}["end_count"]["title"].startswith("ผลตรวจนับปลายงวด")
    assert not {"cycle_count", "full_count", "partial_close"} & {i["key"].split(":")[0] for i in d["items"]}
