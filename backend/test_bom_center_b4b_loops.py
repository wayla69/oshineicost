# -*- coding: utf-8 -*-
"""PR-B4b (2.6, 3.5, 2.5) - the reasons make tasks, the backlog is ranked by impact, the orders are measured.

  2.6  formula_excess: the same item over the recipe in 2 branches in a month, or in one branch 2 months in a row  ->  a task for the kitchen
       and the analyst; miscount: 3 times in a branch in a month  ->  Operations; stock_less: 3 order rounds of one item in a month  ->  Sales
       Support. A task closes by an acknowledgement (append-only) or when the cause stops;
  3.5  the data-quality backlog is ranked by the plates each item touches (the same per-dish expansion as the forecast's `missed`);
  2.5  per branch and delivery: days of cover left when the next delivery arrives, and the ordered items that ran out before it.
"""
from datetime import date

import pytest

import auth
from bom_center import backlog as BL
from bom_center import feedback as FB
from bom_center import outcome as OC
from bom_center import pilot as PL
from bom_center import stock as ST
from bom_center import todo as TD
from test_bom_center_issue_reports import SALMON, g, slip  # noqa: F401
from test_bom_center_issues import CRAB  # noqa: F401
from test_bom_center_s4b import con  # noqa: F401  (sales 1 Sep – 5 Oct at branch 1)

TODAY = date(2026, 10, 31)           # the two months looked at: October and September


def two_branches(con):
    con.execute("INSERT OR IGNORE INTO branches(branch_id, name) VALUES(2, 'ขอนแก่น')")
    con.commit()


def nm(con, bid):
    return con.execute("SELECT name FROM branches WHERE branch_id=?", (bid,)).fetchone()[0]


def end_count(con, branch, day):
    """A submitted 'end' count dated `day`; its period is priced in the month of the day before."""
    cur = con.execute("INSERT INTO bc_stock_count(branch_id, count_date, kind, status, submitted_at) VALUES(?,?, 'end', 'submitted', ?)",
                      (branch, day, day + "T08:00:00+00:00"))
    return cur.lastrowid


def reason(con, count_id, branch, code, why):
    con.execute("INSERT INTO bc_variance_reason(end_count_id, ingredient_code, branch_id, reason_code, who, at) VALUES(?,?,?,?,?,?)",
                (count_id, code, branch, why, "udon", "2026-10-01T09:00:00"))


def keys(tasks):
    return {t["key"] for t in tasks}


# ── 2.6 · the reasons feed back ─────────────────────────────────────────────
def test_the_same_item_over_the_recipe_in_two_branches_is_a_task_for_the_kitchen(con):  # noqa: F811
    two_branches(con)
    reason(con, end_count(con, 1, "2026-10-01"), 1, CRAB, "overportion")             # September, branch 1
    assert FB.evaluate(con, TODAY) == []                                              # one branch, one month: nothing yet
    reason(con, end_count(con, 2, "2026-10-01"), 2, CRAB, "overportion")             # September, branch 2
    con.commit()
    (t,) = FB.evaluate(con, TODAY)
    assert t["rule"] == "formula_excess" and t["subject"] == CRAB and t["month"] == "2026-09" and t["key"] == f"formula_excess:{CRAB}:2026-09"
    assert t["title"] == f"ตรวจสูตร: ปูอัด ({' · '.join(sorted([nm(con, 1), nm(con, 2)]))})" and t["item"] == CRAB
    assert t["owner_modules"] == ["bom.recipes", "bom.center"] and t["owner_th"] == "ครัว / ฝ่ายวิเคราะห์"
    assert {b["branch_id"] for b in t["branches"]} == {1, 2}


def test_trimming_above_the_recipe_is_read_from_the_slips_and_counts_as_the_same_cause(con):  # noqa: F811
    two_branches(con)
    reason(con, end_count(con, 2, "2026-10-01"), 2, CRAB, "overportion")             # branch 2 explained it in September ...
    con.commit()
    for d in ("2026-09-02", "2026-09-03"):                                            # ... branch 1 wrote prep_loss on two slips only: not a pattern
        slip(con, d, [g(CRAB, 100, 20, waste_reason="prep_loss")])
    assert FB.evaluate(con, TODAY) == []
    slip(con, "2026-09-04", [g(CRAB, 100, 20, waste_reason="prep_loss")])             # the third day: branch 1 has it too
    t = FB.evaluate(con, TODAY)
    assert [x["rule"] for x in t] == ["formula_excess"] and {b["branch_id"] for b in t[0]["branches"]} == {1, 2}


def test_the_same_branch_two_months_in_a_row_is_enough(con):  # noqa: F811
    reason(con, end_count(con, 1, "2026-10-01"), 1, CRAB, "overportion")             # September
    con.commit()
    assert FB.evaluate(con, TODAY) == []
    reason(con, end_count(con, 1, "2026-11-01"), 1, CRAB, "overportion")             # October
    con.commit()
    (t,) = FB.evaluate(con, TODAY)
    assert t["month"] == "2026-10" and [b["branch_id"] for b in t["branches"]] == [1]
    # two months that are not next to each other are not "in a row"
    assert FB.evaluate(con, date(2026, 12, 15)) == []                                 # the window is November and October: only one of them


def test_three_miscounts_in_a_branch_in_a_month_is_a_task_for_operations(con):  # noqa: F811
    c = end_count(con, 1, "2026-10-01")
    for code in (CRAB, SALMON):
        reason(con, c, 1, code, "miscount")
    con.commit()
    assert FB.evaluate(con, TODAY) == []
    reason(con, c, 1, "V-007", "miscount")
    con.commit()
    (t,) = FB.evaluate(con, TODAY)
    assert t["rule"] == "miscount" and t["subject"] == "1" and t["title"] == f"ทบทวนการตรวจนับ: {nm(con, 1)}" and t["count"] == 3
    assert t["owner_modules"] == ["ops.report"] and t["owner_th"] == "ฝ่ายปฏิบัติการ"


def order_with_reason(con, cycle, why, item=CRAB, branch=1):
    oid = con.execute("INSERT INTO bc_order(branch_id, cycle_date, round, status) VALUES(?,?, '1', 'received')", (branch, cycle)).lastrowid
    con.execute("INSERT INTO bc_order_line(order_id, ingredient_code, suggested_units, confirmed_units, reason_code) VALUES(?,?,5,2,?)", (oid, item, why))
    return oid


def test_ordering_less_because_the_shelf_had_more_three_rounds_is_a_task_for_sales_support(con):  # noqa: F811
    for d in ("2026-10-05", "2026-10-12"):
        order_with_reason(con, d, "stock_less")
    order_with_reason(con, "2026-10-19", "stock_more")                                # another reason: not counted
    con.commit()
    assert FB.evaluate(con, TODAY) == []
    order_with_reason(con, "2026-10-26", "stock_less")
    con.commit()
    (t,) = FB.evaluate(con, TODAY)
    assert t["rule"] == "stock_less" and t["subject"] == f"1|{CRAB}" and t["month"] == "2026-10" and t["title"] == f"ตรวจคงเหลือตามระบบ: {nm(con, 1)} · ปูอัด"
    assert t["owner_modules"] == ["supply.ordering"] and t["owner_th"] == "ประสานงานขาย" and t["count"] == 3


def test_a_task_closes_by_an_acknowledgement_or_when_the_cause_stops(con):  # noqa: F811
    c = end_count(con, 1, "2026-10-01")
    for code in (CRAB, SALMON, "V-007"):
        reason(con, c, 1, code, "miscount")
    con.commit()
    (t,) = FB.evaluate(con, TODAY)
    with pytest.raises(ValueError, match="ไม่พบงาน"):
        FB.acknowledge(con, "miscount", "9", "2026-09", "ops1")                       # no such task
    with pytest.raises(ValueError, match="ไม่พบชนิด"):
        FB.acknowledge(con, "nonsense", "1", "2026-09", "ops1")
    r = FB.acknowledge(con, "miscount", t["subject"], t["month"], "ops1", "  อบรมแล้ว  ")
    con.commit()
    assert FB.evaluate(con, TODAY) == [] and [x["acked"] for x in FB.evaluate(con, TODAY, include_acked=True)] == [True]
    row = con.execute("SELECT rule, subject, month, acked_by, note FROM bc_feedback_ack WHERE ack_id=?", (r["ack_id"],)).fetchone()
    assert row == ("miscount", "1", "2026-09", "ops1", "อบรมแล้ว")
    with pytest.raises(ValueError, match="ไม่พบงาน"):
        FB.acknowledge(con, "miscount", t["subject"], t["month"], "ops1")             # already read: not open any more
    assert con.execute("SELECT COUNT(*) FROM bc_feedback_ack").fetchone()[0] == 1
    # the condition clears: the task is gone without an acknowledgement; and a new month is a new task
    con.execute("DELETE FROM bc_variance_reason WHERE ingredient_code='V-007'")
    c2 = end_count(con, 1, "2026-11-01")
    for code in (CRAB, SALMON, "V-007"):
        reason(con, c2, 1, code, "miscount")
    con.commit()
    (t2,) = FB.evaluate(con, TODAY)
    assert t2["month"] == "2026-10" and t2["rule"] == "miscount"


def test_the_todo_lists_the_tasks_with_their_owners_and_links(con):  # noqa: F811
    two_branches(con)
    reason(con, end_count(con, 1, "2026-10-01"), 1, CRAB, "overportion")
    reason(con, end_count(con, 2, "2026-10-01"), 2, CRAB, "overportion")
    c = end_count(con, 1, "2026-11-01")
    for code in (CRAB, SALMON, "V-007"):
        reason(con, c, 1, code, "miscount")
    for d in ("2026-10-05", "2026-10-12", "2026-10-19"):
        order_with_reason(con, d, "stock_less")
    con.commit()
    items = {i["key"]: i for i in TD.build(con, today=TODAY)["items"] if i["key"].startswith("feedback:")}
    assert set(items) == {f"feedback:formula_excess:{CRAB}:2026-09", "feedback:miscount:1:2026-10", f"feedback:stock_less:1|{CRAB}:2026-10"}
    fx = items[f"feedback:formula_excess:{CRAB}:2026-09"]
    assert fx["title"].startswith("ตรวจสูตร: ปูอัด") and fx["go"] == {"tab": "bomcenter", "pane": "rcheck", "month": "2026-09", "extra": {"item": CRAB}}
    assert fx["owner_modules"] == ["bom.recipes", "bom.center"] and fx["status"] == "todo" and fx["ack_modules"] == ["bom.recipes", "bom.center"]
    assert items["feedback:miscount:1:2026-10"]["owner_th"] == "ฝ่ายปฏิบัติการ" and items["feedback:miscount:1:2026-10"]["go"]["tab"] == "orderhub"
    assert items[f"feedback:stock_less:1|{CRAB}:2026-10"]["owner_th"] == "ประสานงานขาย"
    # a branch (or the centre looking at ONE branch, as the reminders do) never receives them
    assert not [i for i in TD.build(con, branch_id=1, today=TODAY)["items"] if i["key"].startswith("feedback:")]
    # an acknowledged task is not listed
    FB.acknowledge(con, "miscount", "1", "2026-10", "ops1")
    con.commit()
    assert "feedback:miscount:1:2026-10" not in {i["key"] for i in TD.build(con, today=TODAY)["items"]}


# ── 3.5 · the backlog ranked by impact ──────────────────────────────────────
def window_plates(con, sku_id):
    return con.execute("SELECT SUM(qty) FROM sales_sku_daily WHERE sku_id=? AND day BETWEEN '2026-09-06' AND '2026-10-05'", (sku_id,)).fetchone()[0]


def test_the_backlog_is_ranked_by_the_plates_each_item_touches(con):  # noqa: F811
    # CK-001, a prepared rice whose sub-recipe has a line that cannot be converted, is in every plate of dish 11 - which sells twice as much;
    # a recipe line of dish 10 in a unit nothing converts (litres of crab stick) is skipped too; CK-002 has no sub-recipe at all (dish 10)
    con.execute("INSERT INTO bom_ingredients(sku_id, seq, ingredient_code, ingredient_name, qty_per_serving, unit, price_per_unit, ingredient_cost,"
                " ingredient_type) VALUES(10, 2, '13-01-009', 'ปูอัด', 1, 'ลิตร', 0, 0, 'x')")
    con.executemany("INSERT INTO bc_ingredient(code, name_th, kind, base_unit) VALUES(?, ?, 'prepared', 'กรัม')",
                    [("CK-001", "ข้าวสวยญี่ปุ่น"), ("CK-002", "น้ำส้มสายชูปรุงรส")])
    con.execute("INSERT INTO bom_ingredients(sku_id, seq, ingredient_code, ingredient_name, qty_per_serving, unit, price_per_unit, ingredient_cost,"
                " ingredient_type) VALUES(11, 2, 'CK-001', 'ข้าวสวยญี่ปุ่น', 100, 'กรัม', 0, 0, 'x')")
    con.execute("INSERT INTO bom_ingredients(sku_id, seq, ingredient_code, ingredient_name, qty_per_serving, unit, price_per_unit, ingredient_cost,"
                " ingredient_type) VALUES(10, 3, 'CK-002', 'น้ำส้ม', 10, 'กรัม', 0, 0, 'x')")
    from bom_center import prep as P
    P.save(con, "CK-001", 1000, "กรัม", [{"ingredient_code": CRAB, "qty": 500, "unit": "กรัม"}, {"ingredient_code": SALMON, "qty": 2, "unit": "ถ้วย"}], "kitchen")
    con.execute("UPDATE sales_sku_daily SET qty = qty * 2 WHERE sku_id=11")
    con.commit()
    b = BL.build(con)
    assert b["window"] == {"from": "2026-09-06", "to": "2026-10-05", "days": 30, "month": "2026-10"}
    top = b["top"]
    assert [d["kind"] for d in top[:3]] == ["subrecipe_incomplete", "unit_missing", "subrecipe_missing"]
    assert [d["code"] for d in top[:3]] == ["CK-001", CRAB, "CK-002"] and top[0]["name"] == "ข้าวสวยญี่ปุ่น"
    assert [d["tier"] for d in top[:3]] == [0, 0, 1] and top[0]["tier_th"] == "จานที่ไม่ถูกนับในยอดสั่ง"
    assert top[0]["plates"] == round(window_plates(con, 11)) and top[1]["plates"] == round(window_plates(con, 10)) == top[2]["plates"]
    assert top[0]["plates"] > top[1]["plates"] > 0
    assert top[0]["impact_th"].startswith(f"กระทบ {top[0]['plates']:,} จาน") and top[0]["impact_th"].endswith("ต่อเดือน")
    assert top[0]["go"]["pane"] == "prep" and top[1]["go"]["pane"] == "conv" and "ขาด:" in top[0]["detail"]
    # the order is the tier, then the plates, then the baht
    keys = [(d["tier"], -d["plates"], -d["value"]) for d in b["items"]]
    assert keys == sorted(keys)
    assert b["counts"]["subrecipe_incomplete"] == 1 and b["counts"]["unit_missing"] == 1 and b["counts"]["subrecipe_missing"] == 1 and b["total"] == len(b["items"])
    assert len(BL.build(con, top=1)["top"]) == 1 and len(BL.build(con)["top"]) <= BL.TOP == 10


def test_an_item_nobody_sold_through_is_not_ranked_and_the_todo_carries_the_top_ten(con):  # noqa: F811
    con.execute("INSERT INTO bc_ingredient(code, name_th, kind, base_unit) VALUES('CK-009', 'ซอสที่ไม่มีเมนูใช้', 'prepared', 'กรัม')")
    con.execute("INSERT INTO bom_ingredients(sku_id, seq, ingredient_code, ingredient_name, qty_per_serving, unit, price_per_unit, ingredient_cost,"
                " ingredient_type) VALUES(11, 2, 'CK-001', 'ข้าวสวย', 100, 'กรัม', 0, 0, 'x')")
    con.commit()
    b = BL.build(con)
    assert "CK-009" not in {d["code"] for d in b["items"]}                         # no dish uses it: it touches no plate
    it = next(i for i in TD.build(con, today=date(2026, 10, 6))["items"] if i["key"] == "backlog")
    assert it["owner_modules"] == ["bom.center"] and it["backlog"] == BL.build(con)["top"] and it["backlog_total"] == b["total"]
    assert it["title"].startswith("งานแก้ข้อมูลค้างเรียงตามผลกระทบ")
    assert not [i for i in TD.build(con, branch_id=1, today=date(2026, 10, 6))["items"] if i["key"] == "backlog"]


# ── 2.5 · the order outcome ─────────────────────────────────────────────────
def place_order(con, cycle, delivery, items, branch=1, rnd="1"):
    oid = con.execute("INSERT INTO bc_order(branch_id, cycle_date, round, status, delivery_date) VALUES(?,?,?, 'received', ?)",
                      (branch, cycle, rnd, delivery)).lastrowid
    for c in items:
        con.execute("INSERT INTO bc_order_line(order_id, ingredient_code, suggested_units, confirmed_units) VALUES(?,?,2,2)", (oid, c))
    con.commit()
    return oid


def test_the_outcome_counts_the_cover_left_and_the_items_that_ran_out(con):  # noqa: F811
    # 1 Sep: crab stick 3 kg (gone in four days of trading), salmon 100 kg. The order delivered Wed 16 Sep must last to Fri 18 Sep.
    ST.save(con, 1, [{"code": CRAB, "qty": 3000, "unit": "กรัม"}, {"code": SALMON, "qty": 100000, "unit": "กรัม"}], "udon", submit=True,
            count_date="2026-09-01", require_all=False)
    place_order(con, "2026-09-14", "2026-09-16", [CRAB, SALMON])
    rows = OC.branch_rows(con, 1, date(2026, 9, 1), date(2026, 9, 30), today=date(2026, 10, 20))
    (r,) = rows
    assert r["measured"] is True and r["delivery"] == "2026-09-16" and r["next_arrival"] == "2026-09-19" and r["round_th"] == "รอบ 1"
    assert r["items"] == 2 and r["stockouts"] == 1 and r["stockout_items"] == ["ปูอัด"]
    sp = ST.system_position(con, 1, date(2026, 9, 18), issues_through=date(2026, 9, 18))
    use = ST._usage(con, 1, date(2026, 9, 30))
    want = sorted([0.0, sp["qty"][SALMON] / use[SALMON]])
    assert r["cover_days"] == pytest.approx((want[0] + want[1]) / 2, abs=0.06) and r["estimated_days"] > 0
    s = OC.summarize(rows)
    assert (s["deliveries"], s["measured"], s["stockouts"], s["stockout_rounds"]) == (1, 1, 1, 1)
    m = OC.month(con, "2026-09", today=date(2026, 10, 20))
    assert m["branches"][0]["branch_id"] == 1 and m["branches"][0]["stockouts"] == 1 and m["total"]["stockouts"] == 1 and len(m["rows"]) == 1


def test_a_delivery_is_not_judged_before_the_next_one_arrives_or_without_a_starting_count(con):  # noqa: F811
    place_order(con, "2026-09-14", "2026-09-16", [CRAB])
    (r,) = OC.branch_rows(con, 1, date(2026, 9, 1), date(2026, 9, 30), today=date(2026, 10, 20))
    assert r["measured"] is False and r["why"] == "ยังไม่มีผลตรวจนับตั้งต้น" and r["cover_days"] is None
    (r,) = OC.branch_rows(con, 1, date(2026, 9, 1), date(2026, 9, 30), today=date(2026, 9, 17))        # the next delivery arrives on the 19th
    assert r["measured"] is False and r["why"] == "รอบส่งถัดไปยังไม่ถึง"
    s = OC.summarize([r])
    assert s["measured"] == 0 and s["cover_days"] is None and s["stockouts"] == 0


def test_the_outcome_sits_beside_the_forecast_error_and_in_the_workbook(con):  # noqa: F811
    two_branches(con)
    ST.save(con, 1, [{"code": CRAB, "qty": 3000, "unit": "กรัม"}], "udon", submit=True, count_date="2026-09-01", require_all=False)
    place_order(con, "2026-09-14", "2026-09-16", [CRAB])
    sc = PL.scorecard(con, 1, weeks=4, as_of=date(2026, 10, 5))
    assert sc["metrics"]["stockouts"] == 1 and sc["outcome"]["measured"] == 1 and "cover_days" in sc["metrics"] and "mape_a" in sc["metrics"]
    assert sc["metrics"]["stockouts"] is not None
    # the compliance workbook carries a third sheet; the endpoint answers for any month
    import inspect
    from routers import bom_center as RT
    src = inspect.getsource(RT.bc_hub_compliance_xlsx)
    assert "ผลของการสั่งจริง" in src and "OUTCOME_HEADERS" in src
    assert len(RT.OUTCOME_HEADERS) == 10


def test_who_may_read_and_write_what_batch_4b_added():
    R_ = auth.ROLE_MODULES
    can = lambda role, m, p: auth.path_allowed(m, p, R_[role])                          # noqa: E731
    assert can("analyst", "POST", "/api/bom-center/close-settings") and not can("salessupport", "POST", "/api/bom-center/close-settings")
    assert can("salessupport", "GET", "/api/bom-center/close-settings") and can("operation", "GET", "/api/bom-center/close-settings")
    assert can("branch", "POST", "/api/bom-center/stock/partial-close") and can("salessupport", "POST", "/api/bom-center/stock/partial-close")
    assert not can("operation", "POST", "/api/bom-center/stock/partial-close")
    assert can("analyst", "GET", "/api/bom-center/backlog") and not can("salessupport", "GET", "/api/bom-center/backlog") and not can("branch", "GET", "/api/bom-center/backlog")
    for role in ("analyst", "operation", "salessupport"):
        assert can(role, "GET", "/api/bom-center/feedback") and can(role, "POST", "/api/bom-center/feedback/ack")
    assert not can("branch", "GET", "/api/bom-center/feedback") and not can("accounting", "POST", "/api/bom-center/feedback/ack")
    assert can("branch", "GET", "/api/bom-center/stock/plan") and not can("branch", "GET", "/api/bom-center/hub/outcome")
    assert can("operation", "GET", "/api/bom-center/hub/outcome")
