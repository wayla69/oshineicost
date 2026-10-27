# -*- coding: utf-8 -*-
"""Supply / feedback audit 2026-10 — the promises of the fixes:

  1. a month-end count is submitted (typed or imported) only when it is dated after the branch's last submitted count and
     not in the future (Bangkok day);
  2. a branch that withdraws a claim raised in the receive form gets the claimed goods back as a 'claim' receipt (the same
     booking a rejection makes); withdrawing a later claim books nothing;
  3. the receipt of a received order cannot be cancelled on its own;
  4. a dish-cost import refuses a 0 (or negative) cost per row in the preview and never writes it;
  5. receiving an order needs a quantity for every line the warehouse shipped;
  6. a branch-confined login sees and changes only its own branch on the fetcher-facing feedback endpoints, and is refused
     the chain-wide ones.
"""
import sqlite3
from datetime import date

import pytest

from bom_center import claims as CL
from bom_center import dish_cost as DC
from bom_center import orders as OR
from bom_center import stock as ST
from bom_center import variance as VA
from test_bom_center_claims import CODE, _received_qty_base  # noqa: F401
from test_bom_center_issue_reports import CRAB, SALMON  # noqa: F401
from test_bom_center_receive_claims import _receipt_lines, _receive, _sent  # noqa: F401
from test_bom_center_s4b import con  # noqa: F401
from test_pl1_polish import dc_con  # noqa: F401
from test_feedback_scope import PW, _clean, _fresh, _login, _voices, auth_on  # noqa: F401

pytestmark = pytest.mark.usefixtures("recipe_mode")   # the order fixtures (test_bom_center_s9._submitted) plan from recipes


def _count(con, day, **kw):  # noqa: F811
    return ST.save(con, 1, [{"code": CRAB, "qty": 3000, "unit": "กรัม"}, {"code": SALMON, "qty": 0, "unit": "กรัม"}], "udon",
                   submit=True, count_date=day, require_all=False, **kw)


# ── 1 · the count date ───────────────────────────────────────────────────────
def test_a_count_must_be_after_the_last_and_not_in_the_future(con, monkeypatch):  # noqa: F811
    monkeypatch.setattr(ST, "bkk_today", lambda: date(2026, 10, 10))
    with pytest.raises(ValueError, match="เป็นวันในอนาคต"):
        _count(con, "2026-10-11")
    con.rollback()
    assert _count(con, "2026-10-01")["status"] == "submitted"
    con.commit()
    for day in ("2026-10-01", "2026-09-20"):                      # the same day, an earlier day
        with pytest.raises(ValueError, match=r"ต้องอยู่หลังวันที่นับครั้งล่าสุดที่ส่งแล้ว \(1/10/2026\)"):
            _count(con, day)
        con.rollback()
    ST.discard_draft(con, 1, "udon")
    con.commit()
    assert _count(con, "2026-10-10")["kind"] == "end"             # today is fine


def test_a_saved_draft_with_any_date_is_still_a_draft(con, monkeypatch):  # noqa: F811
    monkeypatch.setattr(ST, "bkk_today", lambda: date(2026, 10, 10))
    out = ST.save(con, 1, [{"code": CRAB, "qty": 1, "unit": "กรัม"}], "udon", submit=False, count_date="2026-12-01")
    assert out["status"] == "draft"                                # only submitting checks the date


def test_an_imported_count_follows_the_same_date_rule(con, monkeypatch):  # noqa: F811
    monkeypatch.setattr(ST, "bkk_today", lambda: date(2026, 10, 10))
    lines = [{"code": CRAB, "qty": 3000, "unit": "กรัม", "note": None}]
    with pytest.raises(ValueError, match="เป็นวันในอนาคต"):
        ST.import_count(con, 1, "2026-10-20", lines, "udon", dry_run=True)
    _count(con, "2026-10-05")
    con.commit()
    with pytest.raises(ValueError, match="ต้องอยู่หลังวันที่นับครั้งล่าสุดที่ส่งแล้ว"):
        ST.import_count(con, 1, "2026-10-03", lines, "udon", dry_run=True)


# ── 2 · cancelling a receive-time claim ──────────────────────────────────────
def test_cancelling_a_receive_time_claim_gives_the_goods_back(con):  # noqa: F811
    oid, q = _sent(con)
    out = _receive(con, oid, q - 2)
    cid = out["claim_id"]
    before = _received_qty_base(con)
    res = CL.cancel(con, cid, "udon")
    con.commit()
    assert res["status"] == "cancelled" and res["origin"] == "receive" and res["restored"] == 1 and res["receipt_id"]
    rows = _receipt_lines(con, cid)
    assert len(rows) == 1 and rows[0][0] == 2 and rows[0][2:] == ("claim", f"CLAIM-{cid}", "2026-10-07")
    assert _received_qty_base(con) == pytest.approx(before + rows[0][1])
    assert CL.detail(con, cid)["adjust_receipt_id"] == res["receipt_id"]
    with pytest.raises(ValueError):                                # the branch cannot take it back out
        VA.delete_receipt(con, 1, res["receipt_id"], "udon", "ทดสอบ")


def test_cancelling_a_later_claim_books_nothing(con):  # noqa: F811
    oid, q = _sent(con)
    _receive(con, oid, q, reason=None)
    c = CL.create(con, 1, oid, [{"code": CODE, "claim_units": 1, "reason": "damaged"}], [], None, "udon")
    con.commit()
    before = _received_qty_base(con)
    res = CL.cancel(con, c["claim_id"], "udon")
    assert res["restored"] == 0 and res["receipt_id"] is None and _receipt_lines(con, c["claim_id"]) == []
    assert _received_qty_base(con) == pytest.approx(before)


# ── 3 · the receipt of an order ──────────────────────────────────────────────
def test_the_receipt_of_a_received_order_cannot_be_cancelled(con):  # noqa: F811
    oid, q = _sent(con)
    out = _receive(con, oid, q, reason=None)
    with pytest.raises(ValueError, match="บันทึกจากการรับของตามใบสั่งซื้อ จึงยกเลิกไม่ได้"):
        VA.delete_receipt(con, 1, out["receipt_id"], "udon", "บันทึกผิด")
    assert con.execute("SELECT COUNT(*) FROM bc_receipt WHERE receipt_id=?", (out["receipt_id"],)).fetchone()[0] == 1
    own = VA.add_receipt(con, 1, "2026-10-08", [{"code": CODE, "qty": 1, "unit": "แพ็ค 1 กก."}], "udon")
    assert VA.delete_receipt(con, 1, own["receipt_id"], "udon", "บันทึกซ้ำ")      # a receipt the branch typed still can


# ── 4 · a 0 dish cost in an import ───────────────────────────────────────────
def test_an_import_refuses_a_zero_or_negative_cost_per_row(dc_con):  # noqa: F811
    c = dc_con
    rows = [{"code": "DCT-A", "name": "", "cost": 0, "note": ""}, {"code": "DCT-B", "name": "", "cost": -3, "note": ""},
            {"code": "DCT-D", "name": "", "cost": 9, "note": ""}]
    pre = DC.preview(c, "2026-10", rows)
    bad = {b["code"]: b["why"] for b in pre["bad_values"]}
    assert set(bad) == {"DCT-A", "DCT-B"} and "มากกว่า 0 บาท" in bad["DCT-A"]
    assert pre["imported"] == 1 and pre["zero_costs"] == []
    DC.commit(c, "2026-10", rows, "analyst1", confirm_zero=True)
    got = {r[0] for r in c.execute("SELECT s.sku_code FROM bc_sku_cost_list l JOIN skus s USING(sku_id)")}
    assert got == {"DCT-D"}
    # the last of a repeated code wins: a 0 after a real cost removes the row instead of keeping the earlier value
    pre = DC.preview(c, "2026-11", [{"code": "DCT-A", "cost": 5}, {"code": "DCT-A", "cost": 0}])
    assert pre["imported"] == 0 and [b["code"] for b in pre["bad_values"]] == ["DCT-A"]


# ── 5 · every shipped line needs a quantity ──────────────────────────────────
def test_receiving_needs_every_shipped_line(con):  # noqa: F811
    oid, q = _sent(con)
    with pytest.raises(ValueError, match="ยังไม่ได้กรอกจำนวนที่รับ 1 รายการ"):
        OR.receive(con, 1, oid, [], "udon", receipt_date="2026-10-07", ack=True)
    con.rollback()
    assert OR.detail(con, oid)["status"] == "processed"
    out = OR.receive(con, 1, oid, [{"code": CODE, "received_units": 0, "reason": "short"}], "udon",
                     receipt_date="2026-10-07", ack=True)              # 0 is a quantity: the whole line is claimed
    assert out["status"] == "received" and out["claim_lines"] == 1


# ── 6 · feedback scope ───────────────────────────────────────────────────────
def test_fetcher_endpoints_respect_the_branch_scope(client, app_db, auth_on):  # noqa: F811
    import core
    mg = _fresh(); assert _login(mg, "management1").status_code == 200
    assert mg.post("/api/auth/users", json={"username": "mgr_a", "password": PW, "role": "operation", "branch": "BranchA"}).status_code == 200
    _voices(mg, [("BranchA", "2026-09-01", 1, "รอนานมาก"), ("BranchB", "2026-09-03", 1, "ของหมด")])
    ids = {r["branch_name"]: r["id"] for r in mg.get("/api/feedback/items").json()["items"]}
    mine, theirs = ids["BranchA"], ids["BranchB"]
    db = sqlite3.connect(str(core.DB_PATH))
    db.execute("UPDATE customer_feedback SET reply_status='pending', reply_draft='ขออภัย', reply_requested_at='2026-09-05'")
    db.commit(); db.close()

    a = _fresh(); assert _login(a, "mgr_a", PW).status_code == 200
    q = a.get("/api/feedback/reply-queue").json()
    assert [i["branch_name"] for i in q["items"]] == ["BranchA"] and q["counts"]["pending"] == 1
    assert len(mg.get("/api/feedback/reply-queue").json()["items"]) == 2
    assert a.post("/api/feedback/reply-result", json={"id": theirs, "status": "failed", "error": "x"}).status_code == 403
    assert a.post("/api/feedback/reply-result", json={"id": mine, "status": "failed", "error": "x"}).status_code == 200
    assert a.post("/api/feedback/sla-alerts/ack", json={"ids": [mine, theirs]}).json()["acked"] == 1
    db = sqlite3.connect(str(core.DB_PATH))
    stamped = dict(db.execute("SELECT branch_name, sla_alerted_at FROM customer_feedback").fetchall())
    db.close()
    assert stamped["BranchA"] and stamped["BranchB"] is None
    assert a.get("/api/feedback/webhook-events").status_code == 403
    assert a.post("/api/feedback/webhook-events/ack", json={"ids": [1]}).status_code == 403
    assert a.post("/api/feedback/competitors", json={"month": "2026-09", "rows": []}).status_code == 403
    assert a.post("/api/feedback/competitors/settings", params={"branch": "BranchB"}, json={"radius_m": 1000}).status_code == 403
    try:
        r = a.post("/api/feedback/competitors/settings", json={"radius_m": 1500})       # lands on the own branch's override
        assert r.status_code == 200 and r.json()["branches_overridden"] == ["BranchA"]
        assert r.json()["radius_m_default"] != 1500                                       # the chain default is untouched
    finally:
        mg.post("/api/feedback/competitors/settings", params={"branch": "BranchA"}, json={"clear": True})
