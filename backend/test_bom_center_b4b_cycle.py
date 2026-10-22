# -*- coding: utf-8 -*-
"""PR-B4b (3.4) - the OPTIONAL weekly cycle count, and (since the owner's decision of 2026-10-13, "ปิดเดือนต้องนับ 100%") the
withdrawal of the partial month close.

The class-A items of a branch MAY be counted every week (switch bc_cycle_counts, off by default): a mid-month check kept in its own tables
that feeds the system stock and never closes a period. Every month-end close is a full count of every item: stock.partial_close refuses
every month. The rows an earlier build wrote (a partial end count whose lines carry `basis`) stay as they were and are read as before,
labelled "รายการเดิม".
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
    """A full begin count on 1 Oct (crab stick and salmon 100 kg, the vegetable 100 g), slips for every selling day 1-5 Oct. The optional
    weekly count is switched on (it is off by default)."""
    CS.save(con, "analyst1", cycle=True)
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
    assert p["first"] is True and p["mode"] == "full" and p["cycle_counts"] is False and p["partial_month"] is None      # off by default
    start_counts(con)
    p = ST.count_plan(con, 1, date(2026, 10, 12))                       # the month-end count is always the full count; the weekly one is an extra
    assert (p["first"], p["mode"], p["full_due"], p["cycle_due"], p["partial_month"]) == (False, "full", False, True, None)
    cycle_count(con, "2026-10-12", {c: 90000 for c in class_a(con)})
    p = ST.count_plan(con, 1, date(2026, 10, 14))
    assert p["cycle_due"] is False and p["cycle_last"] == "2026-10-12"                # counted this week (Monday 12 Oct)
    assert ST.count_plan(con, 1, date(2026, 10, 19))["cycle_due"] is True              # the next Monday: due again
    # no quarter rule and no month that "can be closed on a partial count", on any day
    for d in (date(2027, 1, 4), date(2027, 1, 20), date(2026, 11, 3), date(2026, 10, 3), date(2026, 11, 28)):
        p = ST.count_plan(con, 1, d)
        assert p["mode"] == "full" and p["full_due"] is False and p["full_overdue"] is False and p["partial_month"] is None
    CS.save(con, "analyst1", cycle=False)
    p = ST.count_plan(con, 1, date(2026, 11, 3))
    assert p["cycle_counts"] is False and p["mode"] == "full" and p["partial_month"] is None and p["cycle_due"] is False


def test_the_cycle_sheet_is_the_class_a_list_and_needs_all_of_it(con):  # noqa: F811
    start_counts(con)
    full = ST.sheet(con, 1)
    cyc = ST.sheet(con, 1, mode="cycle")
    items = [it for z in cyc["zones"] for it in z["items"]]
    assert cyc["mode"] == "cycle" and cyc["next_kind"] == "cycle" and items and all(it["class"] == "A" and it["required"] for it in items)
    assert len(items) < full["items"] and cyc["required"] == len(items) and cyc["plan"]["mode"] == "full"
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
    with pytest.raises(ValueError, match="ปิดอยู่"):                                    # off by default
        ST.cycle_save(con, 1, [{"code": CRAB, "qty": 1, "unit": "กรัม"}], "udon")
    CS.save(con, "analyst1", cycle=True)
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


def legacy_partial_close(con, month_end="2026-10-31", date_="2026-11-01"):
    """A partial end count exactly as the withdrawn stock.partial_close wrote it (what an earlier build left in the database)."""
    a = class_a(con)
    sp = ST.system_position(con, 1, date.fromisoformat(month_end), issues_through=date.fromisoformat(month_end), estimate=False)
    cid = con.execute("INSERT INTO bc_stock_count(branch_id, count_date, kind, counted_by, note, created_at, status, updated_at, submitted_at, partial)"
                      " VALUES(1, ?, 'end', 'udon', ?, '2026-11-01T01:00:00+00:00', 'submitted', '2026-11-01T01:00:00+00:00', '2026-11-01T01:00:00+00:00', 1)",
                      (date_, "ของในระบบ (ไม่ได้นับ)")).lastrowid
    for c, q in sorted(sp["qty"].items()):
        con.execute("INSERT INTO bc_stock_count_line(count_id, ingredient_code, qty_kitchen, unit, qty_base, basis) VALUES(?,?,?,?,?,?)",
                    (cid, c, max(q, 0.0), "กรัม", max(q, 0.0), "cycle" if c in a else "system"))
    con.commit()
    return cid


def test_a_partial_close_is_refused_for_every_month(con):  # noqa: F811
    """Owner 2026-10-13: ปิดเดือนต้องนับ 100%. No month, date, switch or flag lets a partial end count be written."""
    start_counts(con)
    cycle_count(con, "2026-10-04", {c: 70000 for c in class_a(con)})
    for month, today in (("2026-10", NOV2), ("2026-09", NOV2), ("2026-10", date(2026, 10, 30)), ("2026-13", NOV2)):
        with pytest.raises(ValueError, match="ปิดเดือนต้องนับของครบทุกรายการ"):
            ST.partial_close(con, 1, month, "udon", today=today)
    with pytest.raises(ValueError, match="ปิดเดือนต้องนับของครบทุกรายการ"):
        ST.partial_close(con, 1, "2026-10", "udon", today=NOV2, accept_missing_slips=True)
    CS.save(con, "analyst1", cycle=False)
    with pytest.raises(ValueError, match="ปิดเดือนต้องนับของครบทุกรายการ"):
        ST.partial_close(con, 1, "2026-10", "udon", today=NOV2)
    assert con.execute("SELECT COUNT(*) FROM bc_stock_count WHERE partial=1").fetchone()[0] == 0


def test_an_old_partial_close_is_still_read_and_labelled(con):  # noqa: F811
    start_counts(con)
    a = class_a(con)
    cycle_count(con, "2026-10-04", {c: 70000 for c in a})
    cid = legacy_partial_close(con)
    # the period (1 Oct - 1 Nov) reads like any other
    assert [(b["date"], e["date"]) for b, e in ST.periods(con, 1)] == [(OCT1, "2026-11-01")]
    p = VA.period(con, 1, cid)
    assert p["partial"] is True and p["system_basis"] >= 1 and p["end_date"] == "2026-11-01"
    sys_rows = [x for x in p["rows"] if x.get("system_basis")]
    assert sys_rows and all(x["basis_th"] == "ของในระบบ (ไม่ได้นับ)" and x["ask_reason"] is False for x in sys_rows)
    s = VA.issue_summary(con, 1, cid)
    assert s["period"]["partial"] is True and "รายการเดิม" in s["period"]["partial_th"] and s["totals"]["system_basis"] >= 1
    rc = IR.reconcile(con, 1, cid)
    assert rc["partial"] is True and rc["totals"]["system_basis"] >= 1
    # the posted-period lock still works on it
    VA.confirm_receipts(con, 1, cid, "udon")
    VA.set_posted(con, cid, True, "acc1")
    con.commit()
    with pytest.raises(ValueError, match="บัญชีบันทึก"):
        VA.add_receipt(con, 1, "2026-10-20", [{"code": CRAB, "qty": 1, "unit": "กรัม"}], "udon")
    # หน้างาน: the close step is done and says the period was closed the old way
    steps = {x["key"]: x for x in TD.build(con, branch_id=1, month="2026-10", today=NOV2)["close"]["steps"]}
    assert steps["end_count"]["status"] == "done" and "รายการเดิม" in steps["end_count"]["branches"][0]["detail"]


def test_the_count_that_closes_a_period_is_a_full_count_of_every_item(con):  # noqa: F811
    """Owner 2026-10-13: a count becomes นับครั้งแรก / นับสิ้นเดือน only when EVERY item of the sheet has a value (0 is a value, blank is not)."""
    start_counts(con)
    everything = [it for z in ST.sheet(con, 1)["zones"] for it in z["items"]]
    assert len(everything) >= 2
    lines = [{"code": it["code"], "qty": 0, "unit": it["count_unit"]} for it in everything]
    with pytest.raises(ValueError) as e:
        ST.save(con, 1, lines[:-1], "udon", submit=True, count_date="2026-11-01")
    assert str(e.value).startswith("ยังไม่ได้นับ 1 รายการ ปิดเดือนต้องนับของครบทุกรายการ (เช่น ") and everything[-1]["name"] in str(e.value)
    con.rollback()
    assert ST.draft_of(con, 1) is None
    ST.save(con, 1, lines[:-1], "udon", submit=False, count_date="2026-11-01")           # a draft may be saved at any time
    out = ST.save(con, 1, lines, "udon", submit=True, count_date="2026-11-01")           # 0 is a value
    assert out["status"] == "submitted" and out["kind"] == "end"


def test_the_close_stepper_and_the_branch_list_follow_the_cycle(con):  # noqa: F811
    start_counts(con)
    d = TD.build(con, branch_id=1, today=date(2026, 10, 14))
    keys = {i["key"].split(":")[0]: i for i in d["items"]}
    assert keys["cycle_count"]["title"] == "นับเสริมรายสัปดาห์ (ไม่ใช่การปิดเดือน)" and keys["cycle_count"]["status"] == "todo"
    assert keys["cycle_count"]["owner_modules"] == ["branch.stock"] and "full_count" not in keys and "partial_close" not in keys
    cycle_count(con, "2026-10-12", {c: 70000 for c in class_a(con)})
    assert {i["key"].split(":")[0]: i for i in TD.build(con, branch_id=1, today=date(2026, 10, 14))["items"]}["cycle_count"]["status"] == "done"
    # a new quarter and the month after a non-quarter month: still no full-count task of a quarter and no partial-close task
    for day in (date(2027, 1, 3), NOV2):
        keys = {i["key"].split(":")[0] for i in TD.build(con, branch_id=1, today=day)["items"]}
        assert not {"full_count", "partial_close"} & keys
    # every month's end-count step is the full count
    for month in ("2026-10", "2026-09"):
        d = TD.build(con, branch_id=1, month=month, today=NOV2)
        steps = {s["key"]: s for s in d["close"]["steps"]}
        assert steps["end_count"]["title"].startswith("ผลนับสิ้นเดือน (นับครบทุกรายการ)")
    CS.save(con, "analyst1", cycle=False)
    d = TD.build(con, branch_id=1, month="2026-10", today=NOV2)
    assert not {"cycle_count", "full_count", "partial_close"} & {i["key"].split(":")[0] for i in d["items"]}


import pytest as _pytest_s3  # noqa: E402
pytestmark = _pytest_s3.mark.usefixtures("recipe_mode")   # PR-S3: this module builds its forecast, plans and count sheets from recipes (mode full)
