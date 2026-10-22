"""Claims inside the receive form (owner 2026-10-09, PR-B).

The promises:
  * the receipt of an order cannot be saved without the branch's acknowledgement that it checked condition
    and quantity (enforced in orders.receive, stored on the order);
  * receiving LESS than the warehouse sent needs a reason per line ("other" needs a note); the receipt is
    booked at the received quantity and the gap becomes ONE claim (origin 'receive') in the same save;
  * receiving more than was sent is allowed and only reported;
  * approving a receive-time claim writes no negative receipt (the stock is already short); the part that is
    not approved — all of it on a rejection — is added back as a positive 'claim' receipt that the branch cannot
    delete, because the branch is treated as having received those goods;
  * a later claim (origin 'after') behaves as before, and only it uses up the received quantity;
  * a receive-time claim never exceeds the gap between what was sent and what was received;
  * over HTTP: no acknowledgement → 400, with it → 200 and the claim id; the order detail, the hub queue and the
    hub order carry the claim.
"""
import json

import pytest

import auth
from bom_center import claims as CL
from bom_center import hub as HB
from bom_center import orders as OR
from bom_center import variance as VA
from test_bom_center_claims import (CODE, _branch_user, _cleanup, _login, _received_qty_base,  # noqa: F401
                                    auth_on)
from test_bom_center_s4b import con  # noqa: F401
from test_bom_center_s9 import _submitted  # noqa: F401


def _sent(con):  # noqa: F811
    """A submitted and processed order for branch 1: (order_id, quantity sent)."""
    oid, _sug, q = _submitted(con, confirm_factor=3.0, reason="event")      # well above the suggestion
    HB.process(con, oid, [], "wh")
    con.commit()
    assert q >= 3, "the fixture must send at least 3 units so that a short receipt and a later claim both fit"
    return oid, q


def _receive(con, oid, got, reason="short", note=None, ack=True, claim_note=None, **kw):  # noqa: F811
    ln = {"code": CODE, "received_units": got}
    if reason:
        ln["reason"] = reason
    if note:
        ln["note"] = note
    out = OR.receive(con, 1, oid, [ln], "udon", receipt_date="2026-10-07", ack=ack, claim_note=claim_note, **kw)
    con.commit()                                                       # the router's _write commits a successful call
    return out


def _receipt_lines(con, claim_id):  # noqa: F811
    return con.execute("SELECT l.qty, l.qty_base, r.source, r.ref, r.receipt_date FROM bc_receipt r"
                       " JOIN bc_receipt_line l USING(receipt_id) WHERE r.ref=?", (f"CLAIM-{claim_id}",)).fetchall()


def test_the_acknowledgement_is_required_and_stored(con):  # noqa: F811
    oid, q = _sent(con)
    with pytest.raises(ValueError) as e:
        _receive(con, oid, q, reason=None, ack=False)
    assert "ทำเครื่องหมายยืนยันการตรวจสินค้า" in str(e.value)
    con.rollback()
    assert OR.detail(con, oid)["status"] == "processed"               # nothing was saved
    out = _receive(con, oid, q, reason=None)
    assert out["status"] == "received" and out["claim_id"] is None and out["claim_lines"] == 0 and out["over"] == []
    d = OR._order_row(con, oid)
    assert d["ack_by"] == "udon" and d["ack_at"] and d["ack_at"] == d["received_at"]
    ev = con.execute("SELECT new_value FROM bc_audit WHERE action='order_receive' AND row_key=?", (str(oid),)).fetchone()[0]
    assert "'ack': True" in ev or '"ack": true' in ev.lower()


def test_a_short_receipt_needs_a_reason(con):  # noqa: F811
    oid, q = _sent(con)
    for kw in (dict(reason=None), dict(reason="made-up"), dict(reason="other")):
        with pytest.raises(ValueError):
            _receive(con, oid, q - 2, **kw)
        con.rollback()
    with pytest.raises(ValueError) as e:
        _receive(con, oid, q - 2, reason=None)
    assert "เลือกสาเหตุของส่วนต่าง" in str(e.value)
    con.rollback()
    with pytest.raises(ValueError) as e:
        _receive(con, oid, q - 2, reason="other")
    assert "สาเหตุ “อื่น ๆ” ต้องระบุคำอธิบาย" in str(e.value)
    con.rollback()
    assert OR.detail(con, oid)["status"] == "processed"
    ok = _receive(con, oid, q - 2, reason="other", note="กล่องหาย")       # "other" with a note is accepted
    assert ok["claim_id"]


def test_a_short_receipt_is_booked_at_the_received_quantity_and_claims_the_gap(con):  # noqa: F811
    oid, q = _sent(con)
    out = _receive(con, oid, q - 2, claim_note="ส่งรูปทางไลน์แล้ว")
    assert out["claim_id"] and out["claim_lines"] == 1 and out["over"] == []
    unit = con.execute("SELECT qty_per_recv FROM bc_ingredient WHERE code=?", (CODE,)).fetchone()[0] or 1
    rec = con.execute("SELECT qty, qty_base FROM bc_receipt_line WHERE receipt_id=?", (out["receipt_id"],)).fetchone()
    assert rec[0] == q - 2 and rec[1] == pytest.approx((q - 2) * unit)
    d = CL.detail(con, out["claim_id"])
    assert d["origin"] == "receive" and d["origin_th"] == "แจ้งขณะรับสินค้า" and d["status"] == "submitted"
    assert d["note"] == "ส่งรูปทางไลน์แล้ว" and d["photos"] == [] and d["stock_adjusted"] is False
    ln = d["lines"][0]
    assert (ln["code"], ln["claim_units"], ln["reason"], ln["sent_units"], ln["received_units"]) == (CODE, 2, "short", q, q - 2)
    assert ln["reason_th"] == "ส่งไม่ครบ / จำนวนขาด"
    assert CL.REASONS[0][0] == "short"
    assert CL.claimable(con, 1, oid)["lines"][0]["remaining_units"] == q - 2      # receive-time claims do not use it up
    assert CL.claimable(con, 1, oid)["lines"][0]["sent_units"] == q
    ev = con.execute("SELECT new_value FROM bc_audit WHERE action='order_receive' AND row_key=?", (str(oid),)).fetchone()[0]
    assert str(out["claim_id"]) in ev
    assert [c["claim_id"] for c in CL.for_order(con, oid)] == [out["claim_id"]]
    assert CL.for_order(con, oid)[0]["origin"] == "receive"
    q1 = HB.queue(con, "received")[0]
    assert q1["claims"] == 1 and q1["claims_pending"] == 1


def test_a_full_receipt_makes_no_claim_and_more_than_sent_is_reported(con):  # noqa: F811
    oid, q = _sent(con)
    out = _receive(con, oid, q + 1, reason=None)
    assert out["claim_id"] is None and out["claim_lines"] == 0
    assert out["over"] == [{"code": CODE, "sent_units": q, "received_units": q + 1, "over_units": 1}]
    assert con.execute("SELECT COUNT(*) FROM bc_claim WHERE order_id=?", (oid,)).fetchone()[0] == 0
    row = con.execute("SELECT qty FROM bc_receipt_line WHERE receipt_id=?", (out["receipt_id"],)).fetchone()
    assert row[0] == q + 1                                             # the receipt records what arrived
    ev = con.execute("SELECT new_value FROM bc_audit WHERE action='order_receive' AND row_key=?", (str(oid),)).fetchone()[0]
    assert "over_units" in ev


def test_approving_a_receive_claim_touches_no_stock(con):  # noqa: F811
    oid, q = _sent(con)
    cid = _receive(con, oid, q - 2)["claim_id"]
    before = _received_qty_base(con)
    out = CL.decide(con, cid, "approve", [], "รับเคลมแล้ว", "ss")
    con.commit()
    assert out["status"] == "approved" and out["origin"] == "receive"
    assert out["adjusted"] == 0 and out["restored"] == 0 and out["receipt_id"] is None
    assert _received_qty_base(con) == before and _receipt_lines(con, cid) == []
    d = CL.detail(con, cid)
    assert d["status"] == "approved" and d["adjust_receipt_id"] is None and d["lines"][0]["approved_units"] == 2


def test_the_unapproved_part_is_added_back_and_cannot_be_deleted(con):  # noqa: F811
    oid, q = _sent(con)
    cid = _receive(con, oid, q - 2)["claim_id"]
    before = _received_qty_base(con)
    out = CL.decide(con, cid, "approve", [{"code": CODE, "approved_units": 1}], None, "ss")
    con.commit()
    assert out["adjusted"] == 0 and out["restored"] == 1 and out["receipt_id"] and out["receipt_date"] == "2026-10-07"
    rows = _receipt_lines(con, cid)
    assert len(rows) == 1 and rows[0][0] == 1 and rows[0][1] > 0 and rows[0][2:] == ("claim", f"CLAIM-{cid}", "2026-10-07")
    assert _received_qty_base(con) == pytest.approx(before + rows[0][1])
    assert CL.detail(con, cid)["adjust_receipt_id"] == out["receipt_id"]
    with pytest.raises(ValueError):                                    # the branch cannot take it back out
        VA.delete_receipt(con, 1, out["receipt_id"], "udon", "ทดสอบ")


def test_rejecting_a_receive_claim_adds_the_goods_back(con):  # noqa: F811
    oid, q = _sent(con)
    cid = _receive(con, oid, q - 2)["claim_id"]
    before = _received_qty_base(con)
    with pytest.raises(ValueError):                                    # a rejection still needs its reason
        CL.decide(con, cid, "reject", [], " ", "ss")
    con.rollback()
    out = CL.decide(con, cid, "reject", [], "สินค้าครบตามใบส่ง", "ss")
    con.commit()
    assert out["status"] == "rejected" and out["origin"] == "receive" and out["adjusted"] == 0
    assert out["restored"] == 1 and out["receipt_id"]
    rows = _receipt_lines(con, cid)
    assert len(rows) == 1 and rows[0][0] == 2 and rows[0][2] == "claim"        # all of the claimed 2 comes back
    assert _received_qty_base(con) == pytest.approx(before + rows[0][1])
    with pytest.raises(ValueError):
        VA.delete_receipt(con, 1, out["receipt_id"], "udon", "ทดสอบ")


def test_rejecting_a_later_claim_still_changes_no_stock(con):  # noqa: F811
    oid, q = _sent(con)
    _receive(con, oid, q, reason=None)
    later = CL.create(con, 1, oid, [{"code": CODE, "claim_units": 1, "reason": "damaged"}], [], None, "udon")
    before = _received_qty_base(con)
    out = CL.decide(con, later["claim_id"], "reject", [], "ไม่พบความเสียหาย", "ss")
    assert out["restored"] == 0 and out["receipt_id"] is None and _received_qty_base(con) == before


def test_a_later_claim_after_a_short_receipt_still_adjusts_negatively(con):  # noqa: F811
    oid, q = _sent(con)
    _receive(con, oid, q - 2)
    can = CL.claimable(con, 1, oid)["lines"][0]
    assert can["remaining_units"] == q - 2
    later = CL.create(con, 1, oid, [{"code": CODE, "claim_units": 1, "reason": "damaged"}], [], None, "udon")
    con.commit()
    assert later["origin"] == "after"
    assert CL.claimable(con, 1, oid)["lines"][0]["remaining_units"] == q - 2 - 1
    with pytest.raises(ValueError):                                    # only q−2−1 is left, whatever the receive claim took
        CL.create(con, 1, oid, [{"code": CODE, "claim_units": q - 2, "reason": "damaged"}], [], None, "udon")
    con.rollback()
    before = _received_qty_base(con)
    out = CL.decide(con, later["claim_id"], "approve", [], None, "ss")
    assert out["origin"] == "after" and out["adjusted"] == 1 and out["restored"] == 0 and out["receipt_id"]
    assert _received_qty_base(con) < before
    assert con.execute("SELECT qty FROM bc_receipt_line WHERE receipt_id=?", (out["receipt_id"],)).fetchone()[0] == -1
    assert CL.detail(con, later["claim_id"])["stock_adjusted"] is True


def test_a_receive_claim_cannot_exceed_the_gap(con):  # noqa: F811
    oid, q = _sent(con)
    _receive(con, oid, q - 2)
    line = {"code": CODE, "claim_units": 3, "reason": "short", "sent_units": q, "received_units": q - 2}
    with pytest.raises(ValueError) as e:
        CL.create_from_receive(con, 1, oid, [line], None, "udon")
    assert "เคลมขณะรับสินค้าได้ไม่เกินส่วนต่างระหว่างที่คลังส่งกับที่รับจริง (2)" in str(e.value)
    con.rollback()
    with pytest.raises(ValueError):                                    # a line with no gap at all
        CL.create_from_receive(con, 1, oid, [dict(line, claim_units=1, sent_units=4, received_units=4)], None, "udon")
    con.rollback()
    with pytest.raises(ValueError):                                    # nothing to claim
        CL.create_from_receive(con, 1, oid, [], None, "udon")
    con.rollback()


def test_the_todo_text_points_at_line(con):  # noqa: F811
    from bom_center import todo as TD
    oid, q = _sent(con)
    _receive(con, oid, q - 2)
    it = {i["key"]: i for i in TD.build(con)["items"]}["claims_pending"]
    assert it["status"] == "todo" and it["count"] == 1
    assert "รอพิจารณา 1 เรื่อง" in it["detail"]


# ── HTTP ──────────────────────────────────────────────────────────────────────
def _seed_processed_order(branch_id, sent=5):
    import core
    from bom_center.schema import ensure
    with core.get_db() as c:
        ensure(c)
        c.execute("INSERT OR IGNORE INTO bc_ingredient(code, name_th, base_unit, recv_unit, qty_per_recv) VALUES(?,?,?,?,?)",
                  ("T-CL1", "สินค้าทดสอบเคลม", "กรัม", "แพ็ค", 1000))
        oid = c.execute("INSERT INTO bc_order(branch_id, cycle_date, round, status, submitted_at, processed_at) VALUES(?,?,?,?,?,?)",
                        (branch_id, "2026-10-06", "1", "processed", "2026-10-06T02:00:00+00:00",
                         "2026-10-06T06:00:00+00:00")).lastrowid
        c.execute("INSERT INTO bc_order_line(order_id, ingredient_code, confirmed_units, processed_units, order_unit)"
                  " VALUES(?,?,?,?,?)", (oid, "T-CL1", sent, sent, "แพ็ค"))
        c.commit()
    return oid


def test_http_receive_with_a_claim(client, app_db, auth_on):
    oid = _seed_processed_order(1)                                     # BranchA's order, 5 sent
    try:
        _http_flow(oid)
    finally:
        _cleanup(oid)


def _http_flow(oid):
    a = _branch_user("brn_claim_a", "BranchA")
    body = {"lines": [{"code": "T-CL1", "received_units": 3, "reason": "short", "note": None}],
            "receipt_date": "2026-10-07", "claim_note": "ส่งรูปทางไลน์แล้ว"}
    no_ack = a.post(f"/api/bom-center/order/{oid}/receive", json=body)
    assert no_ack.status_code == 400 and "ทำเครื่องหมายยืนยันการตรวจสินค้า" in no_ack.json()["detail"], no_ack.text
    no_reason = a.post(f"/api/bom-center/order/{oid}/receive",
                       json={**body, "ack": True, "lines": [{"code": "T-CL1", "received_units": 3}]})
    assert no_reason.status_code == 400 and "เลือกสาเหตุของส่วนต่าง" in no_reason.json()["detail"]
    assert a.get(f"/api/bom-center/order/{oid}").json()["status"] == "processed"      # both refusals saved nothing
    r = a.post(f"/api/bom-center/order/{oid}/receive", json={**body, "ack": True})
    assert r.status_code == 200, r.text
    cid = r.json()["claim_id"]
    assert cid and r.json()["claim_lines"] == 1 and r.json()["status"] == "received" and r.json()["over"] == []
    d = a.get(f"/api/bom-center/order/{oid}").json()
    assert d["status"] == "received" and d["ack_by"] and d["ack_at"]
    assert {x["code"] for x in d["claim_reasons"]} >= {"short", "damaged", "other"}
    assert d["claim_reasons"][0]["code"] == "short"
    assert [c["claim_id"] for c in d["claims"]] == [cid] and d["claims"][0]["origin"] == "receive"
    assert d["claims"][0]["lines"][0]["claim_units"] == 2
    assert a.get("/api/bom-center/claims").json()["origins"][0] == {"code": "receive", "label": "แจ้งขณะรับสินค้า"}
    mg = _login("management1")
    row = next(x for x in mg.get("/api/bom-center/hub/queue?status=received").json()["orders"] if x["order_id"] == oid)
    assert row["claims"] == 1 and row["claims_pending"] == 1
    ho = mg.get(f"/api/bom-center/hub/order/{oid}").json()
    assert [c["claim_id"] for c in ho["claims"]] == [cid]
    ok = mg.post("/api/bom-center/claim-act/decide", headers={"X-Upload-Token": "owner-token-xyz"},
                 json={"claim_id": cid, "decision": "approve", "lines": [{"code": "T-CL1", "approved_units": 1}]})
    assert ok.status_code == 200 and ok.json()["adjusted"] == 0 and ok.json()["restored"] == 1, ok.text
    row = next(x for x in mg.get("/api/bom-center/hub/queue?status=received").json()["orders"] if x["order_id"] == oid)
    assert row["claims"] == 1 and row["claims_pending"] == 0


def test_http_receive_everything_in_one_call(client, app_db, auth_on):
    """PR-M4 · "รับครบ": the phone sends every line at the quantity the warehouse sent, with the acknowledgement and no
    reason — one call, no claim, the order is received."""
    oid = _seed_processed_order(1, sent=7)
    try:
        a = _branch_user("brn_claim_a", "BranchA")
        d = a.get(f"/api/bom-center/order/{oid}").json()
        lines = [{"code": ln["code"], "received_units": float(ln["processed_units"])} for ln in d["lines"]
                 if float(ln["processed_units"] or 0) > 0]
        assert lines == [{"code": "T-CL1", "received_units": 7.0}]
        r = a.post(f"/api/bom-center/order/{oid}/receive", json={"lines": lines, "receipt_date": "2026-10-07", "ack": True})
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "received" and r.json()["claim_id"] is None and r.json()["claim_lines"] == 0
        done = a.get(f"/api/bom-center/order/{oid}").json()
        assert done["status"] == "received" and done["claims"] == [] and done["lines"][0]["received_units"] == 7
    finally:
        _cleanup(oid)


import pytest as _pytest_s3  # noqa: E402
pytestmark = _pytest_s3.mark.usefixtures("recipe_mode")   # PR-S3: this module builds its forecast, plans and count sheets from recipes (mode full)
