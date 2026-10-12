"""PR-M4 — the branch's variance page asks for a reason only on high-value lines (owner Q3, 2026-10-10).

The promises:
  * a flagged line (outside its class threshold) is asked for a reason when it is class A OR among the top 20 flagged
    lines by |฿ variance|; every other flagged line is listed without a form;
  * `ask_reason` is on every row, `totals.ask_reason` counts them, and `needs_reason` is a line asked and not yet
    explained — so the หน้างาน task clears once the asked lines are explained, whatever the others show;
  * a line inside its threshold is never asked, and a reason set on a line clears `needs_reason` but not `ask_reason`;
  * the period rows, the month-end reconciliation and the daily drill-down carry the unit the item is counted in
    (`count_unit`, `per_count_unit`) so that the page reads kilograms / packs, not grams;
  * the head numbers (used ฿, recipe ฿ for the same lines, the gap) agree: gap = used − recipe.
"""
import pytest

from bom_center import issue_reports as IR
from bom_center import variance as VA
from test_bom_center_s4b import con  # noqa: F401
from test_bom_center_s7 import _count, _rows, db  # noqa: F401  (a period with the crab stick 30 % over the recipes)


def _row(code, status, klass, gap_value):
    return {"code": code, "status": status, "class": klass, "gap_value": gap_value}


def test_ask_reason_codes_class_a_and_the_top_twenty():
    rows = [_row(f"B{i:02d}", "over", "B", float(i)) for i in range(1, 31)]            # 30 flagged class-B lines, ฿1 … ฿30
    rows += [_row("A-small", "under", "A", -0.5)]                                      # class A is always asked, however small
    rows += [_row("C-ok", "ok", "C", 99999.0), _row("A-ok", "ok", "A", 5.0)]           # inside the threshold: never asked
    rows += [_row("B-nan", "over", "B", None)]                                         # unpriced: has no ฿ to rank
    ask = VA.ask_reason_codes(rows)
    assert ask == {f"B{i:02d}" for i in range(11, 31)} | {"A-small"}                   # the 20 biggest B lines + the class A one
    assert len(ask) == 21 and "C-ok" not in ask and "A-ok" not in ask and "B-nan" not in ask
    assert VA.ask_reason_codes(rows, top=3) == {"B28", "B29", "B30", "A-small"}
    # an under-use counts by its size, not its sign
    neg = [_row("N1", "under", "C", -50.0), _row("P1", "over", "C", 10.0)]
    assert VA.ask_reason_codes(neg, top=1) == {"N1"}
    # the other flagged statuses are asked too (more on the shelf than began; used but not in a recipe)
    st = [_row("X", "negative", "C", -3.0), _row("Y", "no_bom", "C", 2.0), _row("Z", "not_counted", "A", None)]
    assert VA.ask_reason_codes(st) == {"X", "Y"}


def test_the_period_marks_the_lines_to_explain(db):  # noqa: F811
    db, end = db
    db.execute("INSERT OR REPLACE INTO app_settings(key, value) VALUES('bc_reason_min_baht', '0')")      # batch 4b (3.3): the old rule - no baht floor
    p = VA.period(db, 1)
    r = _rows(p)
    crab, salmon = r["13-01-009"], r["02-01-001"]
    assert crab["status"] == "over" and crab["ask_reason"] is True and crab["needs_reason"] is True
    assert salmon["status"] == "ok" and salmon["ask_reason"] is False and salmon["needs_reason"] is False
    assert r["V-007"]["ask_reason"] is False                                           # counted at the begin only
    assert p["ask_reason"] == 1 and p["totals"]["ask_reason"] == 1 and p["totals"]["ask_reason_open"] == 1
    assert p["needs_reason"] == 1 and p["flagged"] == 1 and p["totals"]["other_flagged"] == 0
    # explaining the line clears the task, not the fact that it was asked
    VA.set_reason(db, 1, end, "13-01-009", "overportion", None, "udon")
    p2 = VA.period(db, 1)
    crab2 = _rows(p2)["13-01-009"]
    assert crab2["ask_reason"] is True and crab2["needs_reason"] is False
    assert p2["ask_reason"] == 1 and p2["totals"]["ask_reason_open"] == 0 and p2["needs_reason"] == 0
    # the summary used for the lists carries the same counts
    s = VA.periods(db, 1)[0]
    assert s["ask_reason"] == 1 and s["totals"]["ask_reason"] == 1 and s["needs_reason"] == 0


def test_the_head_numbers_agree(db):  # noqa: F811
    db, _ = db
    p = VA.period(db, 1)
    rows = [x for x in p["rows"] if x["status"] != "not_counted" and x["actual"] is not None and x["price_per_base"] is not None]
    assert p["actual_value"] == pytest.approx(sum(x["actual"] * x["price_per_base"] for x in rows), abs=0.01)
    assert p["bom_value_counted"] == pytest.approx(sum(x["bom"] * x["price_per_base"] for x in rows), abs=0.01)
    assert p["gap_value"] == pytest.approx(p["actual_value"] - p["bom_value_counted"], abs=0.02)
    assert p["actual_value"] > p["bom_value_counted"] > 0                              # the crab stick was used 30 % over


def test_rows_carry_the_count_unit(db):  # noqa: F811
    db, end = db
    crab = _rows(VA.period(db, 1))["13-01-009"]
    assert crab["base_unit"] == "กรัม" and crab["count_unit"] == "แพ็ค 1 กก." and crab["per_count_unit"] == 1000
    assert crab["actual"] / crab["per_count_unit"] == pytest.approx(crab["actual"] / 1000)
    # an item with no pack size is counted in its base unit, factor 1
    other = _rows(VA.period(db, 1))["V-007"]
    assert other["count_unit"] == other["base_unit"] and other["per_count_unit"] == 1.0
    # the month-end reconciliation and the day drill-down carry them too
    rc = IR.reconcile(db, 1, end)
    assert rc["rows"] and all("count_unit" in x and x["per_count_unit"] > 0 for x in rc["rows"])
    day = IR.daily_day(db, 1, "2026-09-10")
    assert all("count_unit" in x and x["per_count_unit"] > 0 for x in day["rows"])
