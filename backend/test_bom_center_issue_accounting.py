# -*- coding: utf-8 -*-
"""PR-J4 — slip management, the lateness log, the confirmed-slip export for Accounting, the posted-period lock.

The promises:
  * slip_rows: the CONFIRMED slips as lines, base units, priced at the purchase price of the slip's own month,
    the waste reason in words; a draft or a reopened slip is not in it;
  * log: per branch and day — on time / late / draft / missing, when and by whom, reconfirms, reopens and why,
    late purchase orders, claims; the summary per branch; days before the branch started and today are not judged;
  * once Accounting has posted a ใบสรุปตัดเบิก, a slip dated inside that period cannot be confirmed or reopened
    (the end count's own day belongs to the next period); un-posting lifts it; the PR-F receipt lock still reads
    the same;
  * the ใบสรุปตัดเบิก JSON carries the slip coverage (a warning, not a lock); compliance carries the log summary;
    หน้างาน and the morning reminder name the branches that did not confirm yesterday and those that run over the BoM;
  * Accounting may export, never list-and-reopen; a branch login is pinned to its own branch.
"""
import io
import sqlite3
from datetime import date

import pytest
from openpyxl import load_workbook

import auth
from bom_center import hub as HUB
from bom_center import issue_reports as IR
from bom_center import issues as IS
from bom_center import pilot as PL
from bom_center import reminders as RM
from bom_center import stock as ST
from bom_center import todo as TD
from bom_center import variance as VA
from test_bom_center_issue_reports import (CRAB, SALMON, _sql_slip, at, g, period_with_slips, slip,  # noqa: F401
                                           three_salmon_days)
from test_bom_center_issue_summary import TOKEN, _login, _restore, _seed, _snap, auth_on  # noqa: F401
from test_bom_center_s11 import udon  # noqa: F401  (branch 1 = อุดรธานี)
from test_bom_center_s4b import con  # noqa: F401  (sales 1 Sep – 5 Oct at branch 1)


# ── 1 · slip_rows ────────────────────────────────────────────────────────────
def test_slip_rows_are_the_confirmed_lines_priced_at_the_slips_month(con):  # noqa: F811
    slip(con, "2026-09-04", [g(CRAB, 50), g(SALMON, 20, 30, waste_reason="spill")])
    slip(con, "2026-09-05", [g(CRAB, 10), g(SALMON, 0, 5, waste_reason="other", note="ตกพื้น")])
    slip(con, "2026-09-06", [g(CRAB, 999)], submit=False)                                  # a draft: not issued
    slip(con, "2026-10-02", [g(CRAB, 70)])                                                # another month
    con.commit()
    rows = IR.slip_rows(con, month="2026-09")
    assert [(r["date"], r["code"]) for r in rows] == [("2026-09-04", SALMON), ("2026-09-04", CRAB),
                                                       ("2026-09-05", SALMON), ("2026-09-05", CRAB)]
    assert "2026-09-06" not in {r["date"] for r in rows} and "2026-10-02" not in {r["date"] for r in rows}
    by = {(r["date"], r["code"]): r for r in rows}
    crab = by[("2026-09-04", CRAB)]
    assert (crab["qty_used"], crab["qty_waste"], crab["unit_price"], crab["used_value"], crab["waste_value"]) == (50, 0, 0.15, 7.5, 0.0)
    assert crab["branch"] == con.execute("SELECT name FROM branches WHERE branch_id=1").fetchone()[0] and crab["late"] == 0
    assert crab["confirmed_at"] == "2026-09-04 21:00" and crab["confirmed_by"] == "udon" and crab["base_unit"] == "กรัม"
    sal = by[("2026-09-04", SALMON)]
    assert (sal["qty_used"], sal["qty_waste"], sal["waste_reason_th"]) == (20, 30, "หก / ตก / แตก")
    assert sal["used_value"] == pytest.approx(20 * 0.3714, abs=0.01) and sal["waste_value"] == pytest.approx(30 * 0.3714, abs=0.01)
    oth = by[("2026-09-05", SALMON)]
    assert oth["waste_reason"] == "other" and oth["note"] == "ตกพื้น" and oth["waste_reason_th"] == "อื่น ๆ (ระบุ)"
    # one slip, a date range, another branch
    sid = IS.status_of(con, 1, "2026-09-05")["slip_id"]
    assert {r["slip_id"] for r in IR.slip_rows(con, slip_id=sid)} == {sid}
    assert {r["date"] for r in IR.slip_rows(con, d0="2026-09-05", d1="2026-10-31")} == {"2026-09-05", "2026-10-02"}
    assert IR.slip_rows(con, month="2026-09", branch_id=2) == []
    # a reopened slip leaves the export until it is confirmed again
    IS.reopen(con, sid, "ss", "นับผิด", now=at(6, 9))
    assert "2026-09-05" not in {r["date"] for r in IR.slip_rows(con, month="2026-09")}
    assert IR.slip_rows(con, slip_id=sid) == []
    # a late slip is marked
    IS.save(con, 1, "2026-09-05", [], "udon", submit=True, now=at(7, 9))
    late = [r for r in IR.slip_rows(con, month="2026-09") if r["date"] == "2026-09-05"]
    assert late and all(r["late"] == 0 for r in late)                                    # the FIRST confirm was on the day


# ── 2 · the lateness log ─────────────────────────────────────────────────────
def log_fixture(con):
    """The branch started with the 1 Sep count · 4 Sep on time (reopened, then confirmed again) · 6 Sep on time ·
    7 Sep a draft · 8 Sep confirmed the next day (late) · one late purchase order on 5 Sep · one claim on 6 Sep."""
    ST.save(con, 1, [{"code": c, "qty": q, "unit": "กรัม"} for c, q in ((CRAB, 10), (SALMON, 0))], "udon", submit=True,
            count_date="2026-09-01")
    slip(con, "2026-09-04", [g(CRAB, 5)])
    slip(con, "2026-09-06", [g(CRAB, 5)])
    slip(con, "2026-09-07", [g(CRAB, 9)], submit=False)
    slip(con, "2026-09-08", [g(CRAB, 3)], submit=False)
    IS.save(con, 1, "2026-09-08", [], "udon", submit=True, now=at(9, 21))
    sid = IS.status_of(con, 1, "2026-09-04")["slip_id"]
    IS.reopen(con, sid, "ss", "ใส่จำนวนผิด", now=at(5, 9))
    IS.save(con, 1, "2026-09-04", [g(CRAB, 5)], "udon", submit=True, now=at(5, 10))
    con.execute("INSERT INTO bc_order(branch_id, cycle_date, round, status, late) VALUES(1,'2026-09-05','1','submitted',1)")
    con.execute("INSERT INTO bc_order(branch_id, cycle_date, round, status, late) VALUES(1,'2026-09-12','1','cancelled',1)")
    con.execute("INSERT INTO bc_claim(branch_id, order_id, status, created_by, created_at) VALUES(1,1,'submitted','udon',"
                "'2026-09-06T03:00:00+00:00')")
    con.commit()
    return sid


def test_the_log_names_how_each_day_was_handled(con):  # noqa: F811
    sid = log_fixture(con)
    lg = IR.log(con, "2026-09-01", "2026-09-10", today=date(2026, 9, 11))
    rows = {r["date"]: r for r in lg["rows"]}
    assert [r["date"] for r in lg["rows"]] == sorted(rows, reverse=True) and len(rows) == 10          # every trading day
    d4 = rows["2026-09-04"]
    assert (d4["status"], d4["status_th"], d4["slip_id"], d4["lines"]) == ("on_time", "ยืนยันตรงวัน", sid, 1)
    assert (d4["reopens"], d4["reconfirms"], d4["confirmed_at"], d4["confirmed_by"]) == (1, 1, "2026-09-05 10:00", "udon")
    assert d4["reopen_notes"][0]["note"] == "ใส่จำนวนผิด" and d4["reopen_notes"][0]["who"] == "ss"
    assert d4["note"] == "2026-09-05 09:00 ss: ใส่จำนวนผิด" and d4["weekday_th"] == "ศุกร์"
    assert rows["2026-09-06"]["status"] == "on_time" and rows["2026-09-06"]["reopens"] == 0 and rows["2026-09-06"]["claims"] == 1
    assert rows["2026-09-07"]["status"] == "draft" and rows["2026-09-07"]["confirmed_at"] is None
    d8 = rows["2026-09-08"]
    assert (d8["status"], d8["status_th"], d8["late"], d8["status_note"]) == ("late", "ล่าช้า", 1, "ยืนยันหลังวันที่ของใบ")
    assert d8["confirmed_at"] == "2026-09-09 21:00"
    assert rows["2026-09-05"]["status"] == "missing" and rows["2026-09-05"]["late_orders"] == 1
    assert rows["2026-09-10"]["status"] == "missing"
    s = lg["summary"][0]
    assert (s["branch_id"], s["trading_days"], s["on_time"], s["late"], s["draft"], s["missing"]) == (1, 10, 2, 1, 1, 6)
    assert (s["on_time_pct"], s["coverage_pct"]) == (66.7, 30.0)             # 2 of the 3 confirmed; 3 of the 10 trading days
    assert (s["reopens"], s["reconfirms"], s["late_orders"], s["claims"]) == (1, 1, 1, 1)
    assert lg["totals"]["late"] == 1 and lg["status_th"]["closed"] == "ไม่มียอดขาย"
    # today is still open; days before the branch started are not judged
    lg2 = IR.log(con, "2026-09-01", "2026-09-10", today=date(2026, 9, 10))
    assert "2026-09-10" not in {r["date"] for r in lg2["rows"]} and lg2["summary"][0]["trading_days"] == 9
    lg3 = IR.log(con, "2026-08-20", "2026-09-02", branch_id=1, today=date(2026, 9, 11))
    assert [r["date"] for r in lg3["rows"]] == ["2026-09-02", "2026-09-01"]
    # one branch, then the month
    assert IR.log(con, "2026-09-01", "2026-09-10", branch_id=2, today=date(2026, 9, 11))["rows"] == []
    m = IR.log_monthly(con, "2026-09", today=date(2026, 9, 11))
    assert m["month"] == "2026-09" and m["from"] == "2026-09-01" and m["to"] == "2026-09-30"
    assert m["summary"][0]["trading_days"] == 10 and m["summary"][0]["on_time"] == 2          # clipped at today
    # a cancelled late order is not counted; compliance carries the same summary
    assert sum(r["late_orders"] for r in lg["rows"]) == 1
    c = HUB.compliance(con, "2026-09-01", "2026-09-10")
    assert c["issues"] and c["issues"][0]["branch_id"] == 1 and c["issues"][0]["on_time"] >= 0


def test_a_day_without_sales_or_slip_is_closed_and_listed_only_for_its_events(con):  # noqa: F811
    period_with_slips(con)
    con.execute("DELETE FROM sales_sku_daily WHERE branch_id=1 AND day='2026-09-15'")
    con.execute("INSERT INTO bc_claim(branch_id, order_id, status, created_by, created_at) VALUES(1,1,'submitted','udon',"
                "'2026-09-15T03:00:00+00:00')")
    con.commit()
    lg = IR.log(con, "2026-09-14", "2026-09-16", today=date(2026, 9, 20))
    by = {r["date"]: r for r in lg["rows"]}
    assert by["2026-09-15"]["status"] == "closed" and by["2026-09-15"]["status_th"] == "ไม่มียอดขาย" and by["2026-09-15"]["claims"] == 1
    assert lg["summary"][0]["trading_days"] == 2 and lg["summary"][0]["claims"] == 1       # the closed day is not a trading day


# ── 3 · the posted-period lock ───────────────────────────────────────────────
def test_a_posted_period_locks_the_slips_inside_it(con):  # noqa: F811
    end = period_with_slips(con)
    sid4 = IS.status_of(con, 1, "2026-09-04")["slip_id"]
    VA.confirm_receipts(con, 1, end, "udon")
    VA.set_posted(con, end, True, "accounting1")
    con.commit()
    lock = r"อยู่ในงวดที่บัญชีบันทึกใบสรุปตัดเบิกแล้ว \(1/9/2026 ถึง 1/10/2026\) จึงแก้ไขไม่ได้ · แจ้งบัญชีให้ยกเลิกการบันทึกก่อน"
    with pytest.raises(ValueError, match=r"ใบตัดเบิกวันที่ 4/9/2026 " + lock):
        IS.reopen(con, sid4, "ss", "แก้ไข", now=at(2, 9, 10))
    assert IS.status_of(con, 1, "2026-09-04")["status"] == "confirmed"                  # untouched
    with pytest.raises(ValueError, match=r"ใบตัดเบิกวันที่ 7/9/2026 " + lock):          # the draft cannot be confirmed
        IS.save(con, 1, "2026-09-07", [], "udon", submit=True, now=at(2, 21, 10))
    assert IS.status_of(con, 1, "2026-09-07")["status"] == "draft"
    # the end count's own day belongs to the next period
    ok = IS.save(con, 1, "2026-10-01", [g(CRAB, 1)], "udon", submit=True, now=at(2, 21, 10))
    assert ok["status"] == "confirmed"
    # the PR-F receipt lock still reads the same, with the same cause
    with pytest.raises(ValueError, match=r"ใบรับสินค้าวันที่ 20/9/2026 อยู่ในงวดที่บัญชีบันทึกใบสรุปตัดเบิกแล้ว"):
        VA.add_receipt(con, 1, "2026-09-20", [{"code": CRAB, "qty": 1, "unit": "กรัม"}], "udon")
    VA.add_receipt(con, 1, "2026-10-02", [{"code": CRAB, "qty": 1, "unit": "กรัม"}], "udon")
    with pytest.raises(ValueError, match="อยู่ในงวดที่บัญชีบันทึกใบสรุปตัดเบิกแล้ว"):
        VA.assert_not_posted(con, 1, "2026-09-30", "เอกสาร")
    VA.assert_not_posted(con, 1, "2026-10-01")
    VA.assert_not_posted(con, 2, "2026-09-30")                                          # another branch
    # un-posting lifts the lock
    VA.set_posted(con, end, False, "accounting1")
    out = IS.reopen(con, sid4, "ss", "แก้ไข", now=at(2, 9, 10))
    assert out["status"] == "draft"
    log = [r[0] for r in con.execute("SELECT action FROM bc_issue_log WHERE slip_id=? ORDER BY log_id", (sid4,))]
    assert log == ["create", "confirm", "reopen"]


def test_the_issue_summary_json_carries_the_slip_coverage(con):  # noqa: F811
    end = period_with_slips(con)
    s = VA.issue_summary(con, 1, end)
    assert s["slips"] == IR.slip_coverage(con, 1, date(2026, 9, 1), date(2026, 10, 1))
    assert (s["slips"]["trading_days"], s["slips"]["confirmed_days"], s["slips"]["draft_days"]) == (30, 2, ["2026-09-07"])
    assert s["status"]["receipts_confirmed"] is False                                    # a warning, never a lock


# ── 4 · หน้างาน and the morning reminder ─────────────────────────────────────
def test_the_centre_follows_up_missing_slips_and_over_issue(udon):  # noqa: F811
    c = udon
    three_salmon_days(c)                                                                 # salmon 30 % over the BoM on 1-3 Sep
    items = lambda d, now: {i["key"]: i for i in TD.build(c, "2026-09", None, None, today=d, now=now)["items"]}  # noqa: E731
    cen = items(date(2026, 9, 6), at(6, 8))
    miss = cen["issue_missing"]
    assert miss["go"] == {"tab": "orderhub", "pane": "issues", "extra": {"day": "2026-09-05"}}
    assert miss["status"] == "todo" and "อุดรธานี" in miss["detail"]
    an = cen["issue_anomaly"]
    assert (an["seq"], an["title"], an["status"], an["urgent"]) == (229, "ตรวจสอบสาขาที่เบิกเกินสูตรติดต่อกัน", "todo", True)
    assert an["owner_modules"] == ["supply.ordering", "ops.report"] and an["owner_th"] == "ประสานงานขาย / ฝ่ายปฏิบัติการ"
    assert an["detail"] == "เบิกเกินสูตรมากกว่า 20% ติดต่อกันอย่างน้อย 3 วัน: อุดรธานี (แซลมอน)"
    assert an["go"] == {"tab": "orderhub", "pane": "issuevar", "branch": 1} and an["count"] == 1
    # the morning message names both
    text = RM.morning(c, today=date(2026, 9, 6))["text"]
    assert "<b>ประสานงานขาย</b>: ใบตัดเบิกของเมื่อวานยังไม่ยืนยัน 1 สาขา · อุดรธานี" in text
    assert "<b>ฝ่ายปฏิบัติการ</b>: เบิกเกินสูตรมากกว่า 20% ติดต่อกันอย่างน้อย 3 วัน: อุดรธานี (แซลมอน)" in text
    # a week later the streak is out of the window: done, and no Ops line
    later = items(date(2026, 9, 20), at(20, 8))["issue_anomaly"]
    assert later["status"] == "done" and later["detail"] == "ไม่พบการเบิกเกินสูตรติดต่อกันใน 7 วันล่าสุด" and later["go"]["pane"] == "issuevar"
    assert "<b>Ops</b>" not in RM.morning(c, today=date(2026, 9, 20))["text"]


def test_the_scorecard_keeps_one_definition():
    assert set(PL.TARGETS) == {"issue_coverage", "issue_on_time", "reason_coverage", "mape_a"}


# ── 5 · who may do what ──────────────────────────────────────────────────────
def test_who_may_export_and_who_may_reopen():
    R_ = auth.ROLE_MODULES
    can = lambda p, role, m="GET": auth.path_allowed(m, p, R_[role])  # noqa: E731
    for path in ("/api/bom-center/issues/export.xlsx", "/api/bom-center/issues/export/12.xlsx"):
        for role in ("accounting", "salessupport", "analyst", "operation", "branch"):
            assert can(path, role), (role, path)
        assert not can(path, "warehouse") and not can(path, "marketing"), path
        assert not can(path, "accounting", "POST")
    for path in ("/api/bom-center/issue-reports/log", "/api/bom-center/issue-reports/log/monthly",
                 "/api/bom-center/issue-reports/log.xlsx"):
        for role in ("accounting", "salessupport", "operation", "branch"):
            assert can(path, role), (role, path)
        assert not can(path, "warehouse"), path
    reopen = "/api/bom-center/issues/reopen"
    assert can(reopen, "salessupport", "POST") and can(reopen, "analyst", "POST")
    for role in ("accounting", "operation", "branch", "warehouse"):
        assert not can(reopen, role, "POST"), role
    assert not can("/api/bom-center/issues/save", "accounting", "POST")
    assert can("/api/bom-center/issue-summary/posted", "accounting", "POST")


# ── 6 · over HTTP ────────────────────────────────────────────────────────────
def test_http_exports_log_and_the_reopen_lock(client, app_db, auth_on):  # noqa: F811
    import main
    from routers.bom_center import (LOG_HEADERS, LOG_SUMMARY_HEADERS, RECON_HEADERS, SLIP_EXPORT_HEADERS,
                                    SLIP_SUMMARY_HEADERS)
    assert len(SLIP_EXPORT_HEADERS) == 14 and len(LOG_HEADERS) == 11 and len(LOG_SUMMARY_HEADERS) == 12
    client.get("/api/bom-center/hub/queue")                                              # creates the bc_* tables
    snap = _snap(main.DB_PATH)
    try:
        ids = _seed(main.DB_PATH)                              # branch 1 + 2: counts 1 Sep / 1 Oct, +5 T-ISS1 on 10 Sep at branch 1
        c = sqlite3.connect(str(main.DB_PATH))
        c.execute("INSERT OR REPLACE INTO bom_ingredients(sku_id, seq, ingredient_code, ingredient_name, qty_per_serving, unit, price_per_unit,"
                  " ingredient_cost, ingredient_type) VALUES(2, 1, 'T-ISS1', 'ของทดสอบ ก', 0.5, 'กรัม', 0, 0, 'x')")
        c.executemany("INSERT OR REPLACE INTO sales_sku_daily VALUES(1, ?, 2, 10, 0)", [("2026-09-07",), ("2026-09-08",)])
        _sql_slip(c, 1, "2026-09-07", "T-ISS1", 5.5)
        _sql_slip(c, 1, "2026-09-08", "T-ISS1", 9, status="draft")
        c.execute("UPDATE bc_issue_line SET qty_waste=2, qty_waste_base=2, waste_reason='expired' WHERE ingredient_code='T-ISS1'"
                  " AND slip_id=(SELECT slip_id FROM bc_issue_slip WHERE slip_date='2026-09-07')")
        c.commit()
        sid = c.execute("SELECT slip_id FROM bc_issue_slip WHERE slip_date='2026-09-07'").fetchone()[0]
        draft = c.execute("SELECT slip_id FROM bc_issue_slip WHERE slip_date='2026-09-08'").fetchone()[0]
        c.close()
        e1 = ids[1]
        acc, mg, ss, op = _login("accounting1"), _login("management1"), _login("salessupport1"), _login("operation1")
        mg.post("/api/auth/users", headers=TOKEN, json={"username": "br_acc_test", "password": "Temp@12345",
                                                         "role": "branch", "branch": "BranchA"})
        br = _login("br_acc_test", "Temp@12345")
        # the month's workbook: confirmed lines only, the exact headers, the waste reason in words, a clean table
        r = acc.get("/api/bom-center/issues/export.xlsx?month=2026-09")
        assert r.status_code == 200 and "bom_center_issue_slips_2026-09.xlsx" in r.headers["content-disposition"]
        wb = load_workbook(io.BytesIO(r.content))
        assert wb.sheetnames == ["ใบตัดเบิกที่ยืนยัน", "สรุปต่อสาขา"]
        rows = list(wb["ใบตัดเบิกที่ยืนยัน"].iter_rows(values_only=True))
        assert list(rows[1]) == SLIP_EXPORT_HEADERS and len(rows) == 3                       # note, headers, ONE line (the draft is out)
        assert rows[2][:9] == ("2026-09-07", "BranchA", "T-ISS1", "ของทดสอบ ก", "กรัม", 5.5, 2, "หมดอายุ", 2.5)
        assert rows[2][9:12] == (13.75, 5.0, sid) and rows[2][12] and rows[2][13] in (None, "")
        summ = list(wb["สรุปต่อสาขา"].iter_rows(values_only=True))
        assert list(summ[1]) == SLIP_SUMMARY_HEADERS and summ[2][:7] == ("BranchA", 1, 0, 1, 13.75, 5.0, 18.75)
        assert summ[3][0] == "รวม"
        r = acc.get("/api/bom-center/issues/export.xlsx?month=2026-08")
        assert r.status_code == 400 and "ยังไม่มีใบตัดเบิกที่ยืนยันแล้วในเดือน 2026-08" in r.json()["detail"]
        assert acc.get("/api/bom-center/issues/export.xlsx?month=bad").status_code == 400
        assert acc.get("/api/bom-center/issues/export.xlsx").status_code == 400
        # branch pinning
        assert br.get("/api/bom-center/issues/export.xlsx?month=2026-09").status_code == 200
        assert br.get("/api/bom-center/issues/export.xlsx?month=2026-09&branch_id=2").status_code == 403
        assert br.get("/api/bom-center/issues/export.xlsx?month=2026-09&branch_id=1").status_code == 200
        r = op.get("/api/bom-center/issues/export.xlsx?month=2026-09&branch_id=2")
        assert r.status_code == 400                                                         # nothing confirmed at branch 2
        assert _login("warehouse1").get("/api/bom-center/issues/export.xlsx?month=2026-09").status_code == 403
        # one slip
        r = ss.get(f"/api/bom-center/issues/export/{sid}.xlsx")
        assert r.status_code == 200 and f"bom_center_issue_slip_{sid}_2026-09-07.xlsx" in r.headers["content-disposition"]
        one = load_workbook(io.BytesIO(r.content))
        assert one.sheetnames == ["ใบตัดเบิก"] and len(list(one.worksheets[0].iter_rows())) == 3
        r = ss.get(f"/api/bom-center/issues/export/{draft}.xlsx")
        assert r.status_code == 400 and "ใบตัดเบิกนี้ยังไม่ยืนยัน จึงยังส่งออกไม่ได้" in r.json()["detail"]
        assert ss.get("/api/bom-center/issues/export/99999.xlsx").status_code == 404
        # the log
        j = acc.get("/api/bom-center/issue-reports/log?date_from=2026-09-01&date_to=2026-09-10&branch_id=1").json()
        assert [(x["date"], x["status"]) for x in j["rows"]] == [("2026-09-08", "draft"), ("2026-09-07", "on_time")]
        assert j["summary"][0]["on_time"] == 1 and j["summary"][0]["draft"] == 1 and j["summary"][0]["coverage_pct"] == 50.0
        assert acc.get("/api/bom-center/issue-reports/log?date_from=2026-01-01&date_to=2026-09-10").status_code == 400   # > 92 days
        assert acc.get("/api/bom-center/issue-reports/log?date_from=bad").status_code == 400
        assert br.get("/api/bom-center/issue-reports/log?date_from=2026-09-01&date_to=2026-09-10&branch_id=2").status_code == 403
        assert [s["branch"] for s in br.get("/api/bom-center/issue-reports/log/monthly?month=2026-09").json()["summary"]] == ["BranchA"]
        assert acc.get("/api/bom-center/issue-reports/log/monthly?month=bad").status_code == 400
        r = ss.get("/api/bom-center/issue-reports/log.xlsx?month=2026-09&branch_id=1")
        assert r.status_code == 200 and "bom_center_issue_log_b1_2026-09.xlsx" in r.headers["content-disposition"]
        wb = load_workbook(io.BytesIO(r.content))
        assert wb.sheetnames == ["บันทึกความล่าช้า", "สรุปต่อสาขา"]
        lr = list(wb["บันทึกความล่าช้า"].iter_rows(values_only=True))
        assert list(lr[1]) == LOG_HEADERS and [x[0] for x in lr[2:]] == ["2026-09-07", "2026-09-08"]       # oldest first
        assert lr[2][2] == "ยืนยันตรงวัน" and lr[3][2] == "ยังไม่ยืนยัน" and lr[3][10] == "มีร่างแล้ว ยังไม่ยืนยัน"
        sr = list(wb["สรุปต่อสาขา"].iter_rows(values_only=True))
        assert list(sr[1]) == LOG_SUMMARY_HEADERS and sr[2][:8] == ("BranchA", 2, 1, 0, 1, 0, 100.0, 50.0) and sr[3][0] == "รวม"
        r = ss.get("/api/bom-center/issue-reports/log.xlsx?date_from=2026-09-01&date_to=2026-09-10")
        assert r.status_code == 200 and "bom_center_issue_log_2026-09-01_2026-09-10.xlsx" in r.headers["content-disposition"]
        # compliance.xlsx carries the same summary as a second sheet
        r = op.get("/api/bom-center/hub/compliance.xlsx?date_from=2026-09-01&date_to=2026-09-10")
        assert r.status_code == 200
        wb = load_workbook(io.BytesIO(r.content))
        assert wb.sheetnames == ["ตรวจสอบการสั่ง", "ใบตัดเบิก"]
        cr = list(wb["ใบตัดเบิก"].iter_rows(values_only=True))
        assert list(cr[1]) == LOG_SUMMARY_HEADERS and cr[2][:6] == ("BranchA", 2, 1, 0, 1, 0)
        assert any(x["branch"] == "BranchA" for x in op.get("/api/bom-center/hub/compliance?date_from=2026-09-01&date_to=2026-09-10").json()["issues"])
        # the ใบสรุปตัดเบิก workbook: three sheets, the daily slips of the period first
        assert mg.post("/api/bom-center/variance/confirm-receipts?branch_id=1", json={"end_count_id": e1}, headers=TOKEN).status_code == 200
        r = acc.get(f"/api/bom-center/issue-summary.xlsx?end_count_id={e1}&branch_id=1")
        assert r.status_code == 200
        wb = load_workbook(io.BytesIO(r.content))
        assert wb.sheetnames == ["ใบตัดเบิกรายวัน", "กระทบยอด", "สรุปเคลื่อนไหว"]
        first = list(wb["ใบตัดเบิกรายวัน"].iter_rows(values_only=True))
        assert list(first[1]) == SLIP_EXPORT_HEADERS and [x[2] for x in first[2:]] == ["T-ISS1"] and "ยืนยันแล้ว 1/2 วันขาย" in first[0][0]
        assert list(next(wb["กระทบยอด"].iter_rows(min_row=2, max_row=2, values_only=True))) == RECON_HEADERS
        j = acc.get(f"/api/bom-center/issue-summary?end_count_id={e1}&branch_id=1").json()
        assert j["slips"]["trading_days"] == 2 and j["slips"]["confirmed_days"] == 1 and j["slips"]["draft_days"] == ["2026-09-08"]
        # the lock: reopen is refused once Accounting has posted the period, and works again after un-posting
        r = ss.post("/api/bom-center/issues/reopen", json={"slip_id": sid, "note": "แก้ไข"}, headers=TOKEN)
        assert r.status_code == 200 and r.json()["status"] == "draft"                       # not posted yet: allowed
        _sql_slip_confirm = sqlite3.connect(str(main.DB_PATH))
        _sql_slip_confirm.execute("UPDATE bc_issue_slip SET status='confirmed' WHERE slip_id=?", (sid,))
        _sql_slip_confirm.commit()
        _sql_slip_confirm.close()
        assert acc.post("/api/bom-center/issue-summary/posted", json={"end_count_id": e1, "posted": True}, headers=TOKEN).status_code == 200
        r = ss.post("/api/bom-center/issues/reopen", json={"slip_id": sid, "note": "แก้ไขอีก"}, headers=TOKEN)
        assert r.status_code == 400 and "อยู่ในงวดที่บัญชีบันทึกใบสรุปตัดเบิกแล้ว (1/9/2026 ถึง 1/10/2026)" in r.json()["detail"]
        assert ss.get(f"/api/bom-center/issues/{sid}").json()["status"] == "confirmed"
        r = acc.post("/api/bom-center/issues/reopen", json={"slip_id": sid, "note": "x"}, headers=TOKEN)
        assert r.status_code == 403 and r.headers.get("X-Denied") == "role"                  # Accounting never reopens
        assert acc.post("/api/bom-center/issue-summary/posted", json={"end_count_id": e1, "posted": False}, headers=TOKEN).status_code == 200
        r = ss.post("/api/bom-center/issues/reopen", json={"slip_id": sid, "note": "แก้ไขอีก"}, headers=TOKEN)
        assert r.status_code == 200
        # the reopens are in the log with their reasons
        j = ss.get("/api/bom-center/issue-reports/log?date_from=2026-09-01&date_to=2026-09-10&branch_id=1").json()
        d7 = next(x for x in j["rows"] if x["date"] == "2026-09-07")
        assert d7["reopens"] == 2 and [n["note"] for n in d7["reopen_notes"]] == ["แก้ไข", "แก้ไขอีก"]
    finally:
        c = sqlite3.connect(str(main.DB_PATH))                       # the rows outside bc_* that this test added
        c.execute("DELETE FROM bom_ingredients WHERE ingredient_code='T-ISS1'")
        c.execute("DELETE FROM sales_sku_daily WHERE branch_id=1 AND sku_id=2 AND day IN ('2026-09-07', '2026-09-08')")
        c.commit()
        c.close()
        _restore(main.DB_PATH, snap)
