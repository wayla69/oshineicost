# -*- coding: utf-8 -*-
"""ใบสรุปตัดเบิก — the per-period usage summary Accounting downloads and marks as posted (PR-F).

The promises:
  * per item  used = begin + received + claims − end, valued at the purchase price of the month the period
    ends in; an item counted at one end only keeps its line with no usage; an item at zero everywhere with
    nothing received is listed as excluded; a spot count (count-and-order) never makes a period;
  * the refactor of variance.period() changes none of its numbers;
  * Accounting can mark a CONFIRMED period as posted (audited, reversible); once posted the receipts of the
    period cannot be un-confirmed and no receipt dated inside it can be added; re-confirming keeps the mark;
  * หน้างาน gets a step "ดาวน์โหลดใบสรุปตัดเบิกและบันทึกเข้าระบบบัญชี" owned by Accounting (the monthly summary is step 9);
  * Accounting (cost.analysis) reaches only the issue-summary routes, not the variance or stock writes;
  * the workbook has the agreed headers, a totals row, and an all-branches workbook with one sheet per
    confirmed period.
"""
import io
import sqlite3
from datetime import date

import pytest
from fastapi.testclient import TestClient
from openpyxl import load_workbook

import auth
from bom_center import stock as ST
from bom_center import todo as TD
from bom_center import variance as VA
from test_bom_center_s4b import con  # noqa: F401  (crab stick / salmon / V-007 priced 2026-07 list_ref, branch 1)

TOKEN = {"X-Upload-Token": "owner-token-xyz"}


def _count(db, day, lines, bid=1):
    return ST.save(db, bid, [{"code": c, "qty": q, "unit": "กรัม"} for c, q in lines.items()], "udon",
                   submit=True, count_date=day, require_all=False)["count_id"]


def _period(db, bid=1):
    """begin 1 Sep (crab 10, salmon 0, V-007 100) · +5 crab 10 Sep · claim −1 crab 12 Sep · end 1 Oct (crab 6, salmon 0)."""
    _count(db, "2026-09-01", {"13-01-009": 10, "02-01-001": 0, "V-007": 100}, bid)
    VA.add_receipt(db, bid, "2026-09-10", [{"code": "13-01-009", "qty": 5, "unit": "กรัม"}], "udon")
    r = VA.add_receipt(db, bid, "2026-09-12", [{"code": "13-01-009", "qty": 1, "unit": "กรัม"}], "udon",
                       ref="CLAIM-1", source="claim")
    db.execute("UPDATE bc_receipt_line SET qty=-qty, qty_base=-qty_base WHERE receipt_id=?", (r["receipt_id"],))
    end = _count(db, "2026-10-01", {"13-01-009": 6, "02-01-001": 0}, bid)
    db.commit()
    return end


# ── the builder ──────────────────────────────────────────────────────────────
def test_the_summary_numbers_prices_and_totals(con):  # noqa: F811
    end = _period(con)
    s = VA.issue_summary(con, 1, end)
    assert s["period"] == {"begin_date": "2026-09-01", "end_date": "2026-10-01", "begin_count_id": s["period"]["begin_count_id"],
                           "end_count_id": end, "month": "2026-09", "days": 30, "partial": False, "partial_th": None}
    assert s["branch"]["branch_id"] == 1 and s["branch"]["name"]
    lines = {ln["code"]: ln for ln in s["lines"]}
    crab = lines["13-01-009"]
    assert (crab["begin"], crab["received"], crab["claims"], crab["end"], crab["used"]) == (10, 5, -1, 6, 8)
    assert crab["unit_price"] == pytest.approx(0.15) and crab["price_source"] == "ใบเสนอราคา (2026-07)"
    assert crab["value"] == pytest.approx(1.2) and crab["status"] == "ok" and crab["has_reason"] is False
    assert crab["name"] == "ปูอัด" and crab["base_unit"] == "กรัม"
    veg = lines["V-007"]                                       # counted at the start only
    assert veg["status"] == "not_counted" and veg["used"] is None and veg["value"] is None
    assert veg["note"] == "ไม่พบในผลตรวจนับปลายงวด" and veg["begin"] == 100 and veg["end"] is None
    assert "02-01-001" not in lines and [x["code"] for x in s["excluded"]] == ["02-01-001"]
    t = s["totals"]
    assert t["lines"] == 2 and t["not_counted"] == 1 and t["unpriced"] == 0
    assert t["received_value"] == pytest.approx(0.75) and t["claims_value"] == pytest.approx(-0.15)
    assert t["used_value"] == pytest.approx(1.2)
    assert s["status"]["receipts_confirmed"] is False and s["status"]["accounting_posted_at"] is None
    assert s["status"]["receipts"] == 2


def test_an_unknown_period_is_refused(con):  # noqa: F811
    end = _period(con)
    with pytest.raises(ValueError, match="ไม่พบงวดนี้ของสาขา"):
        VA.issue_summary(con, 1, end + 99)
    with pytest.raises(ValueError):
        VA.issue_summary(con, 2, end)                          # another branch's period


def test_variance_period_is_unchanged_by_the_refactor(con):  # noqa: F811
    _period(con)
    p = VA.period(con, 1)
    crab = {r["code"]: r for r in p["rows"]}["13-01-009"]
    assert crab["received"] == 4 and crab["actual"] == 8 and p["price_month"] == "2026-09" and p["receipts"] == 2


def test_a_slip_makes_no_period(con):  # noqa: F811
    from datetime import datetime
    from bom_center import issues as IS
    from bom_center.clock import BKK
    end = _period(con)
    IS.save(con, 1, "2026-10-02", [{"code": "13-01-009", "qty_used": 7, "unit": "กรัม"}], "udon", submit=True,
            now=datetime(2026, 10, 2, 20, 0, tzinfo=BKK))               # a confirmed daily issue slip is not a count
    con.commit()
    assert [(b["date"], e["date"]) for b, e in ST.periods(con, 1)] == [("2026-09-01", "2026-10-01")]
    assert [p["end_count_id"] for p in VA.issue_periods(con, 1)[0]["periods"]] == [end]


def test_issue_periods_lists_state_without_prices(con):  # noqa: F811
    end = _period(con)
    out = VA.issue_periods(con)
    assert len(out) == 1 and out[0]["branch_id"] == 1
    p = out[0]["periods"][0]
    assert p["end_count_id"] == end and p["month"] == "2026-09" and p["receipts_confirmed"] is False
    assert p["accounting_posted_at"] is None and "unit_price" not in p and "lines" not in p
    assert VA.issue_periods(con, 2) == []


# ── posting and the lock ─────────────────────────────────────────────────────
def test_posting_needs_a_confirmed_period_and_locks_it(con):  # noqa: F811
    end = _period(con)
    with pytest.raises(ValueError, match="ต้องยืนยันรับสินค้าครบของงวดนี้ก่อนจึงบันทึกเข้าระบบบัญชีได้"):
        VA.set_posted(con, end, True, "acc")
    with pytest.raises(ValueError, match="ไม่พบงวดนี้ของสาขา"):
        VA.set_posted(con, end, True, "acc", branch_id=2)      # another branch
    with pytest.raises(ValueError, match="ไม่พบงวดนี้ของสาขา"):
        VA.set_posted(con, end + 99, True, "acc")
    VA.confirm_receipts(con, 1, end, "udon")
    out = VA.set_posted(con, end, True, "acc", branch_id=1)
    con.commit()                                               # the later rollback must keep the mark
    assert out["posted"] is True and out["accounting_posted_by"] == "acc" and out["accounting_posted_at"]
    s = VA.issue_summary(con, 1, end)
    assert s["status"]["receipts_confirmed"] and s["status"]["accounting_posted_by"] == "acc"
    assert [r[0] for r in con.execute("SELECT action FROM bc_audit WHERE action LIKE 'issue_%' ORDER BY id")] == ["issue_posted"]
    # the receipts cannot be un-confirmed while posted
    with pytest.raises(ValueError, match="ยกเลิกการยืนยันไม่ได้"):
        VA.confirm_receipts(con, 1, end, "udon", undo=True)
    # no receipt dated inside the period; one outside it is fine
    with pytest.raises(ValueError, match=r"อยู่ในงวดที่บัญชีบันทึกใบสรุปตัดเบิกแล้ว \(1/9/2026 ถึง 1/10/2026\)"):
        VA.add_receipt(con, 1, "2026-09-20", [{"code": "13-01-009", "qty": 1, "unit": "กรัม"}], "udon")
    con.rollback()
    VA.add_receipt(con, 1, "2026-10-01", [{"code": "13-01-009", "qty": 1, "unit": "กรัม"}], "udon")   # the end day: next period
    # re-confirming keeps the posted mark (an UPSERT, not a replace)
    VA.confirm_receipts(con, 1, end, "someone-else")
    row = con.execute("SELECT receipts_confirmed_by, accounting_posted_by FROM bc_variance_period WHERE end_count_id=?",
                      (end,)).fetchone()
    assert row == ("someone-else", "acc")
    # un-posting re-opens both
    out = VA.set_posted(con, end, False, "acc")
    assert out["posted"] is False and out["accounting_posted_at"] is None
    VA.add_receipt(con, 1, "2026-09-20", [{"code": "13-01-009", "qty": 1, "unit": "กรัม"}], "udon")
    VA.confirm_receipts(con, 1, end, "udon", undo=True)
    assert con.execute("SELECT COUNT(*) FROM bc_variance_period").fetchone()[0] == 0
    assert [r[0] for r in con.execute("SELECT action FROM bc_audit WHERE action LIKE 'issue_%' ORDER BY id")] == \
        ["issue_posted", "issue_unposted"]


# ── หน้างาน ──────────────────────────────────────────────────────────────────
def test_the_close_step_goes_blocked_todo_done(con):  # noqa: F811
    end = _period(con)
    today = date(2026, 10, 3)

    def step():
        d = TD.build(con, month="2026-09", today=today)
        return {s["key"]: s for s in d["close"]["steps"]}, d["close"]["steps"]
    steps, ordered = step()
    s = steps["issue_posted"]
    assert s["seq"] == 8 and s["status"] == "blocked" and s["owner_th"] == "บัญชี"
    assert s["title"] == "ดาวน์โหลดใบสรุปตัดเบิกและบันทึกเข้าระบบบัญชี"
    assert s["owner_modules"] == ["cost.analysis", "bom.center"]
    assert s["branches"][0]["detail"] == "รอยืนยันรับสินค้าครบก่อน"
    assert s["go"] == {"tab": "bomcenter", "pane": "issue", "month": "2026-09", "branch": 1}
    assert steps["monthly_summary"]["seq"] == 9 and [x["seq"] for x in ordered] == list(range(1, 10))
    VA.confirm_receipts(con, 1, end, "udon")
    con.commit()
    steps, _ = step()
    assert steps["issue_posted"]["status"] == "todo"
    assert steps["issue_posted"]["branches"][0]["detail"] == "ยืนยันรับสินค้าครบแล้ว รอบัญชีดาวน์โหลดและบันทึก"
    VA.set_posted(con, end, True, "acc")
    con.commit()
    steps, _ = step()
    assert steps["issue_posted"]["status"] == "done"
    assert steps["issue_posted"]["branches"][0]["detail"].startswith("บันทึกแล้ว ") and "/20" in steps["issue_posted"]["branches"][0]["detail"] and "โดย acc" in steps["issue_posted"]["detail"]


# ── the permission matrix ────────────────────────────────────────────────────
def test_the_permission_matrix():
    pa = auth.path_allowed
    m = auth.modules_for_role
    paths = ["/api/bom-center/issue-summary", "/api/bom-center/issue-summary.xlsx",
             "/api/bom-center/issue-summary-all.xlsx", "/api/bom-center/issue-summary/periods"]
    posted = "/api/bom-center/issue-summary/posted"
    acc = m("accounting")
    assert all(pa("GET", p, acc) for p in paths) and pa("POST", posted, acc)
    for denied in ("/api/bom-center/variance/period", "/api/bom-center/variance/overview"):
        assert not pa("GET", denied, acc)
    assert not pa("POST", "/api/bom-center/stock/receipt", acc)
    assert not pa("POST", "/api/bom-center/variance/confirm-receipts", acc)
    for role in ("operation", "salessupport", "branch"):
        assert all(pa("GET", p, m(role)) for p in paths), role
        assert not pa("POST", posted, m(role)), role
    assert not any(pa("GET", p, m("warehouse")) for p in paths + [posted])
    assert pa("POST", posted, m("analyst")) and pa("POST", posted, m("management"))


# ── over HTTP ────────────────────────────────────────────────────────────────
def _snap(db_path):
    c = sqlite3.connect(str(db_path))
    tables = [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'bc%'")]
    snap = {t: c.execute(f"SELECT * FROM {t}").fetchall() for t in tables}
    c.close()
    return snap


def _restore(db_path, snap):
    c = sqlite3.connect(str(db_path))
    for t, rows in snap.items():
        c.execute(f"DELETE FROM {t}")
        if rows:
            c.executemany(f"INSERT INTO {t} VALUES({','.join('?' * len(rows[0]))})", rows)
    c.commit()
    c.close()


@pytest.fixture
def auth_on(monkeypatch):
    monkeypatch.setenv("OSHINEI_AUTH", "1")
    monkeypatch.setenv("OSHINEI_AUTH_SECRET", "test-secret-stable")
    monkeypatch.setenv("OSHINEI_SEED_TOKEN", "owner-token-xyz")
    yield


def _login(user, pw=auth.DEFAULT_PASSWORD):
    import main
    c = TestClient(main.app)
    assert c.post("/api/auth/login", json={"username": user, "password": pw}).status_code == 200
    return c


def _seed(db_path):
    """Branch 1 (BranchA): a confirmed-able period in September; branch 2 (BranchB): an unconfirmed one."""
    c = sqlite3.connect(str(db_path))
    c.executemany("INSERT OR REPLACE INTO bc_ingredient(code, name_th, kind, base_unit, supplier, family, active)"
                  " VALUES(?,?,?,?,?,?,1)", [("T-ISS1", "ของทดสอบ ก", "raw", "กรัม", "ผู้จำหน่าย ก", "F"),
                                              ("T-ISS2", "ของทดสอบ ข", "raw", "กรัม", "ผู้จำหน่าย ข", "F")])
    c.executemany("INSERT OR REPLACE INTO bc_ingredient_price(ingredient_code, month, price_per_base, source) VALUES(?,?,?,?)",
                  [("T-ISS1", "2026-09", 2.5, "purchase_avg"), ("T-ISS2", "2026-07", 1.0, "list_ref")])
    ids = {}
    for bid in (1, 2):
        ST.save(c, bid, [{"code": "T-ISS1", "qty": 10, "unit": "กรัม"}, {"code": "T-ISS2", "qty": 4, "unit": "กรัม"}],
                "udon", submit=True, count_date="2026-09-01", require_all=False)
        if bid == 1:
            VA.add_receipt(c, 1, "2026-09-10", [{"code": "T-ISS1", "qty": 5, "unit": "กรัม"}], "udon")
        ids[bid] = ST.save(c, bid, [{"code": "T-ISS1", "qty": 6, "unit": "กรัม"}, {"code": "T-ISS2", "qty": 4, "unit": "กรัม"}],
                           "udon", submit=True, count_date="2026-10-01", require_all=False)["count_id"]
    c.commit()
    c.close()
    return ids


def test_http_flow_download_post_lock_and_roles(client, app_db, auth_on):
    import main
    client.get("/api/bom-center/hub/queue")                                         # creates the bc_* tables
    snap = _snap(main.DB_PATH)
    ids = _seed(main.DB_PATH)
    e1, e2 = ids[1], ids[2]
    try:
        acc, mg, ss = _login("accounting1"), _login("management1"), _login("salessupport1")
        mg.post("/api/auth/users", headers=TOKEN, json={"username": "br_iss_test", "password": "Temp@12345",
                                                         "role": "branch", "branch": "BranchA"})
        br = _login("br_iss_test", "Temp@12345")
        q = f"end_count_id={e1}&branch_id=1"
        # a preview is allowed before the receipts are confirmed; the workbook is not
        d = acc.get(f"/api/bom-center/issue-summary?{q}").json()
        assert d["status"]["receipts_confirmed"] is False and d["totals"]["lines"] == 2
        iss1 = {ln["code"]: ln for ln in d["lines"]}["T-ISS1"]
        assert iss1["used"] == 9 and iss1["price_source"] == "ซื้อจริง (2026-09)" and iss1["value"] == pytest.approx(22.5)
        r = acc.get(f"/api/bom-center/issue-summary.xlsx?{q}")
        assert r.status_code == 400 and "งวดนี้ยังไม่ยืนยันรับสินค้าครบ จึงยังจัดทำใบสรุปตัดเบิกไม่ได้" in r.json()["detail"]
        assert acc.get(f"/api/bom-center/issue-summary?end_count_id={e1 + 999}&branch_id=1").status_code == 404
        assert acc.get(f"/api/bom-center/issue-summary?end_count_id={e1}").status_code == 400      # the centre names a branch
        # periods: the centre sees both branches, the branch login its own only
        names = {b["name"] for b in acc.get("/api/bom-center/issue-summary/periods").json()["branches"]}
        assert {"BranchA", "BranchB"} <= names
        assert [b["name"] for b in br.get("/api/bom-center/issue-summary/periods").json()["branches"]] == ["BranchA"]
        assert br.get(f"/api/bom-center/issue-summary?end_count_id={e2}&branch_id=2").status_code == 403
        # posting: the role gate, the owner token, and the confirmation rule
        body = {"end_count_id": e1, "posted": True}
        r = br.post("/api/bom-center/issue-summary/posted", json=body, headers=TOKEN)
        assert r.status_code == 403 and r.headers.get("X-Denied") == "role"
        r = ss.post("/api/bom-center/issue-summary/posted", json=body, headers=TOKEN)
        assert r.status_code == 403 and r.headers.get("X-Denied") == "role"
        r = acc.post("/api/bom-center/issue-summary/posted", json=body)
        assert r.status_code == 403 and r.headers.get("X-Denied") == "token"
        r = acc.post("/api/bom-center/issue-summary/posted", json=body, headers=TOKEN)
        assert r.status_code == 400 and "ต้องยืนยันรับสินค้าครบของงวดนี้ก่อน" in r.json()["detail"]
        # confirm (management), then the workbook
        r = mg.post("/api/bom-center/variance/confirm-receipts?branch_id=1", json={"end_count_id": e1}, headers=TOKEN)
        assert r.status_code == 200, r.text
        r = acc.get(f"/api/bom-center/issue-summary.xlsx?{q}")
        assert r.status_code == 200 and "bom_center_issue_summary_b1_2026-09.xlsx" in r.headers["content-disposition"]
        wb = load_workbook(io.BytesIO(r.content))
        assert wb.sheetnames == ["ใบตัดเบิกรายวัน", "กระทบยอด", "สรุปเคลื่อนไหว"]               # PR-J4: three sheets
        ws = wb["สรุปเคลื่อนไหว"]
        rows = list(ws.iter_rows(values_only=True))
        assert "ใบสรุปตัดเบิกวัตถุดิบ สาขาBranchA งวด 2026-09-01 ถึง 2026-10-01 (เดือน 2026-09)" in rows[0][0]
        from routers.bom_center import ISSUE_HEADERS
        assert list(rows[1]) == ISSUE_HEADERS
        crab = next(r for r in rows[2:] if r[0] == "T-ISS1")
        assert crab[3:10] == (10, 5, 0, 6, 9, 2.5, 22.5) and crab[10] == "ซื้อจริง (2026-09)"
        assert "ผู้จำหน่าย" not in ISSUE_HEADERS and len(ISSUE_HEADERS) == 12 and len(crab) == 12   # PR-K: no supplier column
        assert all("supplier" not in ln for ln in d["lines"])
        assert rows[-1][1] == "รวม" and rows[-1][9] == pytest.approx(22.5)
        # the all-branches workbook: a summary sheet, the daily slips, the confirmed period's two sheets; the other branch is only listed
        r = acc.get("/api/bom-center/issue-summary-all.xlsx?month=2026-09")
        assert r.status_code == 200 and "bom_center_issue_summary_all_2026-09.xlsx" in r.headers["content-disposition"]
        wb = load_workbook(io.BytesIO(r.content))
        assert wb.sheetnames == ["สรุปทุกสาขา", "ใบตัดเบิกรายวัน", "BranchA", "กระทบยอด BranchA"]
        assert all(len(n) <= 31 for n in wb.sheetnames)
        summ = list(wb["สรุปทุกสาขา"].iter_rows(values_only=True))
        assert list(summ[1]) == ["สาขา", "งวดเริ่ม", "งวดสิ้นสุด", "จำนวนรายการ", "มูลค่ารับเข้า", "มูลค่าเคลม/ปรับ",
                                 "มูลค่าตัดเบิก", "ไม่มีราคา", "สถานะบัญชี"]
        by = {r[0]: r for r in summ[2:] if r[0]}
        assert by["BranchA"][8] == "ยังไม่บันทึก" and by["BranchA"][6] == pytest.approx(22.5)
        assert by["BranchB"][8] == "ยังไม่ยืนยันรับสินค้าครบ (ไม่มีแผ่นงาน)"
        assert acc.get("/api/bom-center/issue-summary-all.xlsx?month=bad").status_code == 400
        r = acc.get("/api/bom-center/issue-summary-all.xlsx?month=2026-01")
        assert r.status_code == 400 and "ยังไม่มีงวดที่ยืนยันรับสินค้าครบในเดือน 2026-01" in r.json()["detail"]
        # post it
        r = acc.post("/api/bom-center/issue-summary/posted", json=body, headers=TOKEN)
        assert r.status_code == 200 and r.json()["posted"] is True
        st = acc.get(f"/api/bom-center/issue-summary?{q}").json()["status"]
        assert st["accounting_posted_by"] == "accounting1" and st["accounting_posted_at"]
        p = next(b for b in br.get("/api/bom-center/issue-summary/periods").json()["branches"])["periods"][0]
        assert p["accounting_posted_by"] == "accounting1"
        # the lock: a receipt inside the period and un-confirming are refused with the Thai message
        r = mg.post("/api/bom-center/stock/receipt?branch_id=1", headers=TOKEN,
                    json={"receipt_date": "2026-09-20", "lines": [{"code": "T-ISS1", "qty": 1, "unit": "กรัม"}]})
        assert r.status_code == 400 and "อยู่ในงวดที่บัญชีบันทึกใบสรุปตัดเบิกแล้ว" in r.json()["detail"]
        r = mg.post("/api/bom-center/variance/confirm-receipts?branch_id=1", json={"end_count_id": e1, "undo": True}, headers=TOKEN)
        assert r.status_code == 400 and "ยกเลิกการยืนยันไม่ได้" in r.json()["detail"]
        # accounting itself cannot reach the variance or stock writes
        r = acc.post("/api/bom-center/stock/receipt?branch_id=1", headers=TOKEN,
                     json={"receipt_date": "2026-09-20", "lines": [{"code": "T-ISS1", "qty": 1, "unit": "กรัม"}]})
        assert r.status_code == 403 and r.headers.get("X-Denied") == "role"
        # un-post re-opens
        r = acc.post("/api/bom-center/issue-summary/posted", json={"end_count_id": e1, "posted": False}, headers=TOKEN)
        assert r.status_code == 200 and r.json()["posted"] is False
        r = mg.post("/api/bom-center/stock/receipt?branch_id=1", headers=TOKEN,
                    json={"receipt_date": "2026-09-20", "lines": [{"code": "T-ISS1", "qty": 1, "unit": "กรัม"}]})
        assert r.status_code == 200, r.text
    finally:
        _restore(main.DB_PATH, snap)
