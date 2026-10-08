"""เคลมสินค้า (owner 2026-10-08): a branch reports goods that arrived wrong, with photos; Sales Support decides.

The promises:
  * a claim is made against ONE received order, only for what it delivered; open and approved
    claims together can never exceed the received quantity;
  * 1–6 photos are required and each is checked by its own bytes, not its name;
  * a reason per line ("other" needs a note); a reject needs a reason for the branch;
  * approving takes the approved quantity out of what the branch received — a negative receipt
    dated the day the order arrived, so the usage variance drops goods that were never usable;
  * that adjustment cannot be deleted by the branch;
  * a branch sees and opens only its own claims and photos; Sales Support decides; the
    warehouse and the other roles have no part in it.
"""
import pytest
from fastapi.testclient import TestClient

import auth
from bom_center import claims as CL
from bom_center import hub as HB
from bom_center import orders as OR
from bom_center import variance as VA
from test_bom_center_s4b import con  # noqa: F401
from test_bom_center_s9 import AS_OF, TUE, _submitted  # noqa: F401

JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64 + b"\xff\xd9"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
CODE = "13-01-009"


def _received(con, short=0):  # noqa: F811
    oid, sug, q = _submitted(con)
    HB.process(con, oid, [], "wh")
    OR.receive(con, 1, oid, [{"code": CODE, "received_units": q - short}], "udon", receipt_date="2026-10-07")
    con.commit()
    return oid, q - short


def _received_qty_base(con):  # noqa: F811
    return con.execute("SELECT COALESCE(SUM(l.qty_base),0) FROM bc_receipt r JOIN bc_receipt_line l USING(receipt_id)"
                       " WHERE r.branch_id=1 AND l.ingredient_code=?", (CODE,)).fetchone()[0]


def _claim(con, oid, qty, reason="damaged", note=None, photos=(JPEG,)):  # noqa: F811
    out = CL.create(con, 1, oid, [{"code": CODE, "claim_units": qty, "reason": reason, "note": note}], list(photos), "ผู้แจ้ง", "udon")
    con.commit()                                                       # the router's _write commits a successful call
    return out


def test_claim_then_approve_adjusts_the_received_goods(con):  # noqa: F811
    oid, got = _received(con)
    assert got >= 2
    before = _received_qty_base(con)
    c = _claim(con, oid, 1, note="กล่องบุบ")
    assert c["claim_id"] and c["photos"] == 1
    d = CL.detail(con, c["claim_id"])
    assert d["status"] == "submitted" and d["lines"][0]["reason_th"].startswith("สินค้าชำรุด") and len(d["photos"]) == 1
    out = CL.decide(con, c["claim_id"], "approve", [], "รับเคลมแล้ว", "ss")
    assert out["status"] == "approved" and out["adjusted"] == 1 and out["receipt_date"] == "2026-10-07"
    after = _received_qty_base(con)
    assert after < before                                              # the claimed goods left what was received
    rec = con.execute("SELECT source, ref, receipt_date FROM bc_receipt WHERE receipt_id=?", (out["receipt_id"],)).fetchone()
    assert rec == ("claim", f"CLAIM-{c['claim_id']}", "2026-10-07")
    assert con.execute("SELECT qty, qty_base FROM bc_receipt_line WHERE receipt_id=?", (out["receipt_id"],)).fetchone()[0] == -1
    d = CL.detail(con, c["claim_id"])
    assert d["status"] == "approved" and d["lines"][0]["approved_units"] == 1 and d["adjust_receipt_id"] == out["receipt_id"]


def test_a_partial_approval_only_adjusts_what_was_approved(con):  # noqa: F811
    oid, got = _received(con)
    c = _claim(con, oid, 2)
    before = _received_qty_base(con)
    CL.decide(con, c["claim_id"], "approve", [{"code": CODE, "approved_units": 1}], None, "ss")
    one = before - _received_qty_base(con)
    assert one > 0
    assert CL.detail(con, c["claim_id"])["lines"][0]["approved_units"] == 1
    with pytest.raises(ValueError):                                    # decided once, final
        CL.decide(con, c["claim_id"], "approve", [], None, "ss")


def test_approval_cannot_exceed_the_claim_or_be_empty(con):  # noqa: F811
    oid, _ = _received(con)
    c = _claim(con, oid, 1)
    with pytest.raises(ValueError):
        CL.decide(con, c["claim_id"], "approve", [{"code": CODE, "approved_units": 2}], None, "ss")
    con.rollback()
    with pytest.raises(ValueError):
        CL.decide(con, c["claim_id"], "approve", [{"code": CODE, "approved_units": 0}], None, "ss")
    con.rollback()
    assert CL.detail(con, c["claim_id"])["status"] == "submitted"


def test_reject_needs_a_reason_and_changes_no_stock(con):  # noqa: F811
    oid, _ = _received(con)
    c = _claim(con, oid, 1)
    before = _received_qty_base(con)
    with pytest.raises(ValueError):
        CL.decide(con, c["claim_id"], "reject", [], "  ", "ss")
    con.rollback()
    out = CL.decide(con, c["claim_id"], "reject", [], "ไม่พบความเสียหายในรูป", "ss")
    assert out["status"] == "rejected" and _received_qty_base(con) == before
    d = CL.detail(con, c["claim_id"])
    assert d["status_th"] == "ไม่อนุมัติ" and d["decision_note"] == "ไม่พบความเสียหายในรูป" and d["decided_by"] == "ss"


def test_claims_cannot_exceed_what_was_received(con):  # noqa: F811
    oid, got = _received(con)
    with pytest.raises(ValueError):
        _claim(con, oid, got + 1)
    con.rollback()
    first = _claim(con, oid, got)                                      # the whole delivery, claimed
    assert CL.claimable(con, 1, oid)["lines"][0]["remaining_units"] == 0
    with pytest.raises(ValueError):                                    # nothing left for a second claim
        _claim(con, oid, 1)
    con.rollback()
    CL.cancel(con, first["claim_id"], "udon")                          # cancelled → the quantity is free again
    assert CL.claimable(con, 1, oid)["lines"][0]["remaining_units"] == got
    _claim(con, oid, 1)


def test_validations(con):  # noqa: F811
    oid, got = _received(con)
    bad = [
        dict(photos=()),                                               # no photo
        dict(photos=(b"not an image",)),                               # not an image, whatever the name
        dict(photos=(JPEG,) * (CL.MAX_PHOTOS + 1)),                    # too many
        dict(photos=(JPEG + b"\x00" * CL.MAX_PHOTO_BYTES,)),           # too big
        dict(reason="other"),                                          # "other" without a note
        dict(reason="made-up"),
    ]
    for kw in bad:
        with pytest.raises(ValueError):
            _claim(con, oid, 1, **kw)
        con.rollback()
    with pytest.raises(ValueError):                                    # zero lines
        CL.create(con, 1, oid, [{"code": CODE, "claim_units": 0, "reason": "damaged"}], [JPEG], None, "udon")
    con.rollback()
    with pytest.raises(ValueError):                                    # a code the order never carried
        CL.create(con, 1, oid, [{"code": "02-01-001", "claim_units": 1, "reason": "damaged"}], [JPEG], None, "udon")
    con.rollback()
    assert CL.sniff_image(PNG) == "image/png" and CL.sniff_image(b"RIFF\x00\x00\x00\x00WEBPVP8 ") == "image/webp"
    _claim(con, oid, 1, photos=(PNG,))                                 # png is fine


def test_only_a_received_order_of_that_branch_can_be_claimed(con):  # noqa: F811
    oid, _sug, _q = _submitted(con)                                    # sent, not received
    with pytest.raises(ValueError):
        CL.claimable(con, 1, oid)
    with pytest.raises(ValueError):
        CL.claimable(con, 2, oid)                                      # another branch's order
    con.rollback()


def test_the_branch_cannot_delete_the_adjustment(con):  # noqa: F811
    oid, _ = _received(con)
    c = _claim(con, oid, 1)
    out = CL.decide(con, c["claim_id"], "approve", [], None, "ss")
    with pytest.raises(ValueError):
        VA.delete_receipt(con, 1, out["receipt_id"], "udon")


def test_a_cancelled_claim_cannot_be_decided(con):  # noqa: F811
    oid, _ = _received(con)
    c = _claim(con, oid, 1)
    CL.cancel(con, c["claim_id"], "udon")
    with pytest.raises(ValueError):
        CL.decide(con, c["claim_id"], "approve", [], None, "ss")
    with pytest.raises(ValueError):
        CL.cancel(con, c["claim_id"], "udon")


# ── roles ────────────────────────────────────────────────────────────────────
def test_who_may_do_what():
    br, ss, wh, ops, ana = (auth.ROLE_MODULES[r] for r in ("branch", "salessupport", "warehouse", "operation", "analyst"))
    assert auth.path_allowed("POST", "/api/bom-center/claims", br)
    assert auth.path_allowed("GET", "/api/bom-center/claims/1/photo/1", br)
    assert not auth.path_allowed("POST", "/api/bom-center/claim-act/decide", br)      # the branch never approves its own claim
    assert auth.path_allowed("POST", "/api/bom-center/claim-act/decide", ss)
    assert auth.path_allowed("GET", "/api/bom-center/claims", ss)
    assert not auth.path_allowed("POST", "/api/bom-center/claim-act/decide", ops)
    assert not auth.path_allowed("POST", "/api/bom-center/claim-act/decide", ana)
    assert auth.path_allowed("GET", "/api/bom-center/claims", ops)                    # Ops reads
    assert not auth.path_allowed("POST", "/api/bom-center/claims", ops)
    assert not auth.path_allowed("GET", "/api/bom-center/claims", wh)
    assert not auth.path_allowed("POST", "/api/bom-center/claim-act/decide", wh)
    assert auth.path_allowed("POST", "/api/bom-center/claim-act/decide", auth.ROLE_MODULES["management"])


def test_the_todo_counts_open_claims(con):  # noqa: F811
    from bom_center import todo as TD
    oid, _ = _received(con)
    assert {i["key"]: i for i in TD.build(con)["items"]}["claims_pending"]["status"] == "done"
    c = _claim(con, oid, 1)
    it = {i["key"]: i for i in TD.build(con)["items"]}["claims_pending"]
    assert it["status"] == "todo" and it["count"] == 1 and it["owner_modules"] == ["supply.ordering"]
    CL.decide(con, c["claim_id"], "reject", [], "ไม่อนุมัติ", "ss")
    assert {i["key"]: i for i in TD.build(con)["items"]}["claims_pending"]["status"] == "done"


# ── HTTP: scoping, multipart, photos ──────────────────────────────────────────
def _seed_received_order(branch_id):
    import core
    from bom_center.schema import ensure
    with core.get_db() as c:
        ensure(c)
        c.execute("INSERT OR IGNORE INTO bc_ingredient(code, name_th, base_unit, recv_unit, qty_per_recv) VALUES(?,?,?,?,?)",
                  ("T-CL1", "สินค้าทดสอบเคลม", "กรัม", "แพ็ค", 1000))
        oid = c.execute("INSERT INTO bc_order(branch_id, cycle_date, round, status, received_at) VALUES(?,?,?,?,?)",
                        (branch_id, "2026-10-06", "1", "received", "2026-10-07T08:00:00+00:00")).lastrowid
        c.execute("INSERT INTO bc_order_line(order_id, ingredient_code, confirmed_units, processed_units, received_units, order_unit)"
                  " VALUES(?,?,?,?,?,?)", (oid, "T-CL1", 5, 5, 5, "แพ็ค"))
        rid = c.execute("INSERT INTO bc_receipt(branch_id, receipt_date, source, ref, entered_by, created_at) VALUES(?,?,?,?,?,?)",
                        (branch_id, "2026-10-07", "order", f"ORDER-{oid}", "t", "2026-10-07")).lastrowid
        c.execute("INSERT INTO bc_receipt_line(receipt_id, ingredient_code, qty, unit, qty_base) VALUES(?,?,?,?,?)",
                  (rid, "T-CL1", 5, "แพ็ค", 5000))
        c.commit()
    return oid


def _cleanup(oid):
    """The app DB is shared by the whole session: leave no order, receipt or claim behind for the next test file."""
    import core
    with core.get_db() as c:
        cl = [r[0] for r in c.execute("SELECT claim_id FROM bc_claim WHERE order_id=?", (oid,))]
        for t in ("bc_claim_photo", "bc_claim_line"):
            c.executemany(f"DELETE FROM {t} WHERE claim_id=?", [(i,) for i in cl])
        c.execute("DELETE FROM bc_claim WHERE order_id=?", (oid,))
        refs = [f"ORDER-{oid}"] + [f"CLAIM-{i}" for i in cl]
        for rid, in c.execute(f"SELECT receipt_id FROM bc_receipt WHERE ref IN ({','.join('?' * len(refs))})", refs).fetchall():
            c.execute("DELETE FROM bc_receipt_line WHERE receipt_id=?", (rid,))
            c.execute("DELETE FROM bc_receipt WHERE receipt_id=?", (rid,))
        c.execute("DELETE FROM bc_order_line WHERE order_id=?", (oid,))
        c.execute("DELETE FROM bc_order WHERE order_id=?", (oid,))
        c.execute("DELETE FROM bc_ingredient WHERE code='T-CL1'")
        c.commit()


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


def _branch_user(name, branch):
    mg = _login("management1")
    r = mg.post("/api/auth/users", headers={"X-Upload-Token": "owner-token-xyz"},
                json={"username": name, "password": "Temp@12345", "role": "branch", "branch": branch})
    assert r.status_code in (200, 409), r.text
    return _login(name, "Temp@12345")


def test_http_flow_with_photos_and_branch_scoping(client, app_db, auth_on):
    oid = _seed_received_order(1)                                      # BranchA's order
    try:
        _http_flow(oid)
    finally:
        _cleanup(oid)


def _http_flow(oid):
    import json
    a = _branch_user("brn_claim_a", "BranchA")
    b = _branch_user("brn_claim_b", "BranchB")
    payload = json.dumps({"order_id": oid, "lines": [{"code": "T-CL1", "claim_units": 2, "reason": "damaged", "note": "บุบ"}], "note": "ถ่ายตอนรับ"})
    r = a.post("/api/bom-center/claims", data={"payload": payload}, files=[("photos", ("a.jpg", JPEG, "image/jpeg")), ("photos", ("b.png", PNG, "image/png"))])
    assert r.status_code == 200, r.text                                # no token, no unlock: a branch login
    cid = r.json()["claim_id"]
    assert r.json()["photos"] == 2
    bad = a.post("/api/bom-center/claims", data={"payload": payload}, files=[("photos", ("x.jpg", b"GIF89a...", "image/jpeg"))])
    assert bad.status_code == 400 and "รูป" in bad.json()["detail"]    # the bytes decide, not the file name
    noph = a.post("/api/bom-center/claims", data={"payload": payload})
    assert noph.status_code == 400
    # the owner branch sees it, the other branch does not
    assert [c["claim_id"] for c in a.get("/api/bom-center/claims").json()["claims"]] == [cid]
    assert b.get("/api/bom-center/claims").json()["claims"] == []
    assert a.get(f"/api/bom-center/claims/{cid}").status_code == 200
    ph = a.get(f"/api/bom-center/claims/{cid}/photo/{a.get(f'/api/bom-center/claims/{cid}').json()['photos'][0]['photo_id']}")
    assert ph.status_code == 200 and ph.headers["content-type"] == "image/jpeg" and ph.headers["x-content-type-options"] == "nosniff"
    pid = a.get(f"/api/bom-center/claims/{cid}").json()["photos"][0]["photo_id"]
    for url in (f"/api/bom-center/claims/{cid}", f"/api/bom-center/claims/{cid}/photo/{pid}"):
        r2 = b.get(url)
        assert r2.status_code == 403 and r2.headers.get("X-Denied") == "role", url
    assert b.post(f"/api/bom-center/claims/{cid}/cancel").status_code == 403
    # the branch cannot decide its own claim
    assert a.post("/api/bom-center/claim-act/decide", json={"claim_id": cid, "decision": "approve"}).headers.get("X-Denied") == "role"
    # Sales Support decides — with the owner token, like every central write
    from bom_center.schema import ensure  # noqa: F401
    mg = _login("management1")
    no_tok = mg.post("/api/bom-center/claim-act/decide", json={"claim_id": cid, "decision": "approve", "lines": [{"code": "T-CL1", "approved_units": 1}]})
    assert no_tok.status_code == 403 and no_tok.headers.get("X-Denied") == "token"
    ok = mg.post("/api/bom-center/claim-act/decide", headers={"X-Upload-Token": "owner-token-xyz"},
                 json={"claim_id": cid, "decision": "approve", "lines": [{"code": "T-CL1", "approved_units": 1}], "note": "ตกลง"})
    assert ok.status_code == 200 and ok.json()["adjusted"] == 1, ok.text
    d = a.get(f"/api/bom-center/claims/{cid}").json()
    assert d["status"] == "approved" and d["lines"][0]["approved_units"] == 1
    # the branch's receipt list shows the adjustment, and it cannot be deleted
    recs = a.get("/api/bom-center/stock/receipts").json()["receipts"]
    adj = next(x for x in recs if x["source"] == "claim")
    assert adj["lines"][0]["qty"] == -1 and adj["ref"] == f"CLAIM-{cid}"
    dl = a.delete(f"/api/bom-center/stock/receipt/{adj['receipt_id']}")
    assert dl.status_code == 400
    # a fully claimed order leaves nothing to claim; a warehouse login has no access at all
    wh = _login_warehouse()
    assert wh.get("/api/bom-center/claims").status_code == 403
    assert wh.get("/api/bom-center/hub/queue").status_code == 200


def _login_warehouse():
    mg = _login("management1")
    r = mg.post("/api/auth/users", headers={"X-Upload-Token": "owner-token-xyz"}, json={"username": "wh_claim", "role": "warehouse"})
    assert r.status_code in (200, 409), r.text
    return _login("wh_claim")
