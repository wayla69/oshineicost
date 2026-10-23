# -*- coding: utf-8 -*-
"""PR-B4b - variance reasons, feedback loops, data-quality ranking, order outcome and cycle counts
(batch 4b of the BoM improvement proposal, owner approval 2026-10-12; decisions 2.6, 3.3, 3.4 as proposed).

The promises, one block per proposal item:
  3.3  a reason is asked only when a line is outside its class threshold AND at least `bc_reason_min_baht` baht off (default 500); a line
       at -100 % (used 0 against a recipe), a period with no receipt, a reconciliation period with a selling day that has no confirmed slip
       are labelled "ข้อมูลไม่ครบ" and not asked; the same rule counts `needs_reason` in the variance period, the reconciliation, หน้างาน;
  3.4  the class-A items MAY be counted every week (cycle counts, their own tables, off by default): an extra check that never closes a
       period. The partial month close of the first draft was withdrawn on 2026-10-13 (owner: month-end close = a count of every item);
  2.6  the reasons make tasks: the same item over the recipe in 2 branches or 2 months, a branch that miscounts 3 times, an item ordered
       less 3 rounds because the shelf had more; a task closes by an acknowledgement (append-only) or when the cause stops;
  3.5  the data-quality backlog is ranked by the plates each item touches, the top 10 on หน้างาน;
  2.5  the order outcome: days of cover left when the next delivery arrives and the ordered items that ran out before it.
"""
import inspect
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
from test_bom_center_s7 import _count, _rows, db  # noqa: F401  (a period with the crab stick 30 % over the recipes, ฿267)


def setting(con, key, value):
    con.execute("CREATE TABLE IF NOT EXISTS app_settings(key TEXT PRIMARY KEY, value TEXT NOT NULL DEFAULT '')")
    con.execute("INSERT OR REPLACE INTO app_settings(key, value) VALUES(?,?)", (key, str(value)))
    con.commit()


def _row(code, status, klass, gap_value, **kw):
    return dict({"code": code, "status": status, "class": klass, "gap_value": gap_value}, **kw)


# ── 3.3 · fewer variance reasons ────────────────────────────────────────────
def test_the_baht_floor_decides_who_is_asked_and_zero_keeps_the_old_rule():
    rows = [_row("A-small", "over", "A", 120.0), _row("A-big", "over", "A", 900.0), _row("B-mid", "under", "B", -600.0),
            _row("C-tiny", "over", "C", 40.0), _row("B-none", "over", "B", None)]
    assert VA.ask_reason_codes(rows, min_baht=500) == {"A-big", "B-mid"}               # class A is not asked below the floor either
    assert VA.ask_reason_codes(rows, min_baht=0) == {"A-small", "A-big", "B-mid", "C-tiny"}      # no floor: the rule of PR-M4
    assert VA.ask_reason_codes(rows) == VA.ask_reason_codes(rows, min_baht=0)
    assert VA.ask_reason_codes(rows, top=1, min_baht=100) == {"A-small", "A-big"}      # the top-N cap still applies after the floor
    ref = [_row("U", "over", "B", None, ref_value=-800.0)]                              # an unpriced item is ranked by its fallback ฿
    assert VA.ask_reason_codes(ref, min_baht=500) == {"U"}


def test_a_line_that_cannot_be_judged_is_labelled_and_never_asked():
    rows = [_row("I", "under", "A", -900.0, data_incomplete=True), _row("S", "over", "A", 900.0, system_basis=True),
            _row("OK", "over", "A", 900.0)]
    assert VA.ask_reason_codes(rows, min_baht=500) == {"OK"}
    assert VA.INCOMPLETE_TH == "ข้อมูลไม่ครบ" and VA.SYSTEM_TH == "ของที่ควรเหลือ (ไม่ได้นับ)"


def test_the_setting_is_read_by_the_period_and_defaults_to_500(db):  # noqa: F811
    db, end = db
    assert CS.reason_min_baht(db) == 500.0
    p = VA.period(db, 1)                                   # the crab stick is 30 % over: ฿267 - under the default floor
    crab = _rows(p)["13-01-009"]
    assert crab["status"] == "over" and 0 < crab["gap_value"] < 500
    assert crab["ask_reason"] is False and crab["needs_reason"] is False and p["needs_reason"] == 0 and p["reason_min_baht"] == 500.0
    assert p["flagged"] == 1 and p["totals"]["other_flagged"] == 1                     # still flagged, listed without a form
    setting(db, "bc_reason_min_baht", 100)
    p = VA.period(db, 1)
    assert _rows(p)["13-01-009"]["ask_reason"] is True and p["needs_reason"] == 1
    setting(db, "bc_reason_min_baht", 0)
    assert VA.period(db, 1)["needs_reason"] == 1
    setting(db, "bc_reason_min_baht", 5000)
    assert VA.period(db, 1)["needs_reason"] == 0
    # junk or out-of-range text falls back to the default instead of failing a page
    setting(db, "bc_reason_min_baht", "abc")
    assert CS.reason_min_baht(db) == 500.0
    setting(db, "bc_reason_min_baht", 999999)
    assert CS.reason_min_baht(db) == 500.0


def test_the_setting_is_checked_when_saved_and_audited(con):  # noqa: F811
    with pytest.raises(ValueError):
        CS.save(con, "analyst1", reason_min=-1)
    with pytest.raises(ValueError):
        CS.save(con, "analyst1", reason_min=100001)
    with pytest.raises(ValueError):
        CS.save(con, "analyst1", reason_min="x")
    with pytest.raises(ValueError):
        CS.save(con, "analyst1", cycle="yes")
    out = CS.save(con, "analyst1", reason_min=750, cycle=False)
    assert out["reason_min_baht"] == 750 and out["cycle_counts"] is False and CS.cycle_counts_on(con) is False
    assert CS.save(con, "analyst1", cycle=True)["cycle_counts"] is True and CS.reason_min_baht(con) == 750
    acts = [r[0] for r in con.execute("SELECT action FROM bc_audit WHERE action IN ('reason_min_baht','cycle_counts') ORDER BY id")]
    assert acts == ["reason_min_baht", "cycle_counts", "cycle_counts"]


def test_used_zero_against_a_recipe_is_data_not_complete(con):  # noqa: F811
    setting(con, "bc_reason_min_baht", 0)
    _count(con, "2026-09-08", {"13-01-009": 10000, "02-01-001": 5000})
    VA.add_receipt(con, 1, "2026-09-08", [{"code": "13-01-009", "qty": 1, "unit": "แพ็ค 1 กก."}], "udon")
    end = _count(con, "2026-09-15", {"13-01-009": 11000, "02-01-001": 5000})            # begin + received = end: used 0; salmon the same
    con.commit()
    p = VA.period(con, 1, end)
    r = _rows(p)
    for code in ("13-01-009", "02-01-001"):
        assert r[code]["status"] == "under" and r[code]["gap_pct"] == -100.0
        assert r[code]["data_incomplete"] is True and r[code]["incomplete_th"] == "ข้อมูลไม่ครบ" and r[code]["incomplete_why"] == "used_zero"
        assert r[code]["ask_reason"] is False and r[code]["needs_reason"] is False
    assert p["needs_reason"] == 0 and p["data_incomplete"] == 2 and p["totals"]["incomplete"] == 2 and p["flagged"] == 2


def test_a_period_with_no_receipt_is_labelled(con):  # noqa: F811
    setting(con, "bc_reason_min_baht", 0)
    _count(con, "2026-09-08", {"13-01-009": 10000, "02-01-001": 5000})
    end = _count(con, "2026-09-15", {"13-01-009": 1000, "02-01-001": 4000})            # used far more than the recipes, no delivery recorded
    con.commit()
    p = VA.period(con, 1, end)
    crab = _rows(p)["13-01-009"]
    assert crab["status"] == "over" and crab["data_incomplete"] is True and crab["incomplete_why"] == "receipts" and crab["needs_reason"] is False
    VA.add_receipt(con, 1, "2026-09-10", [{"code": "13-01-009", "qty": 1, "unit": "แพ็ค 1 กก."}], "udon")
    con.commit()
    crab = _rows(VA.period(con, 1, end))["13-01-009"]
    assert not crab.get("data_incomplete") and crab["needs_reason"] is True


def week_of_slips(con, upto="2026-09-14", crab=450, salmon=100):
    d = date.fromisoformat("2026-09-08")
    while d <= date.fromisoformat(upto):
        slip(con, d.isoformat(), [g(CRAB, crab), g(SALMON, salmon)])
        d = date.fromordinal(d.toordinal() + 1)


def recon_period(con, with_slips):
    ST.save(con, 1, [{"code": CRAB, "qty": 100000, "unit": "กรัม"}, {"code": SALMON, "qty": 100000, "unit": "กรัม"}], "udon",
            submit=True, count_date="2026-09-08", require_all=False)
    VA.add_receipt(con, 1, "2026-09-09", [{"code": CRAB, "qty": 1000, "unit": "กรัม"}], "udon")
    if with_slips:
        week_of_slips(con)
    end = ST.save(con, 1, [{"code": CRAB, "qty": 60000, "unit": "กรัม"}, {"code": SALMON, "qty": 99000, "unit": "กรัม"}], "udon",
                  submit=True, count_date="2026-09-15", require_all=False)["count_id"]
    con.commit()
    return end


def test_reconcile_asks_only_a_complete_period_and_a_line_over_the_floor(con):  # noqa: F811
    setting(con, "bc_reason_min_baht", 0)
    end = recon_period(con, with_slips=False)                                             # no slip at all: the system stock is meaningless
    r = IR.reconcile(con, 1, end)
    crab = next(x for x in r["rows"] if x["code"] == CRAB)
    assert crab["status"] == "under" and crab["data_incomplete"] is True and crab["incomplete_why"] == "slips"
    assert crab["ask_reason"] is False and crab["needs_reason"] is False and r["totals"]["needs_reason"] == 0 and r["totals"]["incomplete"] >= 1
    assert r["incomplete_why"] == "slips" and r["reason_min_baht"] == 0


def test_reconcile_with_every_slip_asks_by_the_floor(con):  # noqa: F811
    end = recon_period(con, with_slips=True)
    setting(con, "bc_reason_min_baht", 0)
    r = IR.reconcile(con, 1, end)
    crab = next(x for x in r["rows"] if x["code"] == CRAB)
    assert r["slips"]["missing_days"] == [] and crab["status"] == "under" and not crab.get("data_incomplete")
    assert crab["ask_reason"] is True and crab["needs_reason"] is True
    big = abs(crab["diff_value"])
    setting(con, "bc_reason_min_baht", int(big) + 100)
    crab = next(x for x in IR.reconcile(con, 1, end)["rows"] if x["code"] == CRAB)
    assert crab["status"] == "under" and crab["ask_reason"] is False and crab["needs_reason"] is False
    setting(con, "bc_reason_min_baht", int(big) - 1)
    assert next(x for x in IR.reconcile(con, 1, end)["rows"] if x["code"] == CRAB)["needs_reason"] is True


def test_the_todo_counts_the_same_reasons_as_the_period(db):  # noqa: F811
    db, end = db
    setting(db, "bc_reason_min_baht", 0)
    items = {i["key"].split(":")[0]: i for i in TD.build(db, branch_id=1, today=date(2026, 9, 16))["items"]}
    assert items["variance_open"]["count"] == VA.period(db, 1, summary_only=True)["needs_reason"] + 1       # + the receipts not confirmed
    setting(db, "bc_reason_min_baht", 5000)
    items = {i["key"].split(":")[0]: i for i in TD.build(db, branch_id=1, today=date(2026, 9, 16))["items"]}
    assert items["variance_open"]["count"] == 1                                         # nothing to explain: only the confirmation is left
    assert VA.issue_summary(db, 1, end)["status"]["reasons_done"] is True


def test_every_counter_of_needs_reason_goes_through_the_period_or_the_reconciliation():
    # the close steps, หน้างาน and the monthly summary read period()["needs_reason"]; the reconciliation counts its own rows
    src = inspect.getsource(TD) + inspect.getsource(IR)
    assert 'v["needs_reason"]' in src and 'row["needs_reason"] = row["ask_reason"] and not rs' in src
