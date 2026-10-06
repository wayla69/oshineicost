"""Regression tests for the 2026-10 recon API review (routers: sessions, matching,
config, daily; main's login gate; core's token compare). One test per finding,
named after it, so a re-break points straight at the rule it violates."""
import sqlite3
import sys
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import recon.main as main  # noqa: E402
import recon.core as core  # noqa: E402
import recon.routers.daily as daily_router  # noqa: E402
from test_recon import _make_db, DEMO_POS  # noqa: E402

PM = "2026-06"
EVIL = "<img src=x onerror=alert(1)>"


@pytest.fixture
def client(tmp_path, monkeypatch):
    core._DB_PATH = main._DB_PATH = _make_db(tmp_path, DEMO_POS)
    for k in ("OSHINEI_SEED_TOKEN", "OSHINEI_MANAGER_TOKEN", "RECON_ENFORCE_AUTH"):
        monkeypatch.delenv(k, raising=False)
    with TestClient(main.app) as cl:
        yield cl
        cl.cookies.clear()


def _q(sql, *a):
    k = sqlite3.connect(main._DB_PATH)
    k.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in k.execute(sql, a)]
    finally:
        k.close()


def _x(sql, *a):
    k = sqlite3.connect(main._DB_PATH)
    try:
        cur = k.execute(sql, a)
        k.commit()
        return cur.lastrowid
    finally:
        k.close()


def _pos_day(rows):
    _x("CREATE TABLE IF NOT EXISTS pos_payment_day"
       "(branch_id INT, day TEXT, payment_code TEXT, amount REAL, txn_count INT DEFAULT 0)")
    for bid, day, code, amt in rows:
        _x("INSERT INTO pos_payment_day VALUES(?,?,?,?,1)", bid, day, code, amt)


def _line(credit, date="2026-06-10", branch_id=4, channel="QR", status="unmatched",
          provider="kbank", account_no=None, description=None):
    up = _x("INSERT INTO recon_uploads(source_type,branch_id,provider,file_name,period_month,"
            "row_count,status) VALUES('bank',?,?,'f',?,0,'confirmed')", branch_id, provider, date[:7])
    return _x("INSERT INTO recon_bank_lines(upload_id,branch_id,txn_date,channel,credit,"
              "match_status,account_no,description) VALUES(?,?,?,?,?,?,?,?)",
              up, branch_id, date, channel, credit, status, account_no, description)


def _manual_setup(client, pos=100.0, credit=100.0):
    pm = {r["payment_code_norm"]: r for r in client.get("/api/payment-map").json()}
    client.patch(f"/api/payment-map/{pm['KNNQR']['id']}", json={"category": "QR", "bank": "KBANK"})
    _pos_day([(4, "2026-06-10", "KNNQR", pos)])
    return _line(credit)


def _manual_body(ext, pos_days=None, **kw):
    b = {"branch_id": 4, "category": "QR", "bank": "KBANK", "period_month": PM,
         "pos_days": pos_days or [{"day": "2026-06-10", "amount": 100}],
         "ext": ext, "note": "จับคู่ทดสอบ"}
    b.update(kw)
    return b


def _login(client, username="alice"):
    if core._parse_session is None:
        pytest.skip("backend.auth not importable — no session to test with")
    from backend.auth import make_token
    client.cookies.set(core._SESSION_COOKIE, make_token(1, username, "admin"))


def _audit(entity, entity_id=None, action=None):
    sql, a = "SELECT * FROM recon_audit_log WHERE entity=?", [entity]
    if entity_id is not None:
        sql += " AND entity_id=?"; a.append(entity_id)
    if action:
        sql += " AND action=?"; a.append(action)
    return _q(sql, *a)


# ── 1. stored XSS in the daily report ─────────────────────────────────────────
def test_daily_report_escapes_stored_text(client, monkeypatch):
    lid = _manual_setup(client)
    _x("UPDATE recon_bank_lines SET account_no=?, channel=? WHERE id=?", EVIL, "QR" + EVIL, lid)
    up = _x("INSERT INTO recon_uploads(source_type,branch_id,provider,file_name,period_month,"
            "row_count,status) VALUES('credit_card',4,'KTB','f',?,0,'confirmed')", PM)
    _x("INSERT INTO recon_cc_lines(upload_id,branch_id,settlement_date,gross_amount,net_amount,"
       "acquirer,merchant_id,terminal_id) VALUES(?,4,'2026-06-10',50,49,?,?,?)", up, EVIL, EVIL, EVIL)
    mid = _x("INSERT INTO recon_manual_match(branch_id,category,bank,period_month,pos_amount,"
             "ext_amount,variance,pos_days,note,matched_by) VALUES(4,'QR',?,?,1,1,0,"
             "'[{\"day\":\"2026-06-10\",\"amount\":1}]',?,?)", EVIL, PM, EVIL, EVIL)
    assert mid
    monkeypatch.setattr(daily_router, "daily_board", lambda conn, day: {
        "column_meta": {"QR": {"label": EVIL}},
        "branches": [{"branch_id": 4, "branch_name": EVIL, "variance": 0, "pos_total": 0,
                      "ext_total": 0, "issues": EVIL,
                      "cells": {"QR": {"pos": 1, "ext": 1, "manual": 0, "variance": 0,
                                       "status": EVIL}}}]})
    h = client.get("/api/daily-report", params={"day": "2026-06-10", "branch_id": 4})
    assert h.status_code == 200
    assert "<img src=x" not in h.text and "&lt;img src=x" in h.text


# ── 2. /api/manual validates every ticked line server-side ────────────────────
def test_manual_rejects_foreign_locked_excluded_and_duplicate_lines(client):
    lid = _manual_setup(client)
    other_branch = _line(100, branch_id=5)
    other_month = _line(100, date="2026-07-01")
    excluded = _line(100, status="excluded")
    t = "recon_bank_lines"
    for eid, code in ((other_branch, 400), (other_month, 400), (excluded, 409), (999999, 404)):
        r = client.post("/api/manual", json=_manual_body([{"table": t, "id": eid, "amount": 100}]))
        assert r.status_code == code, (eid, r.text)
    r = client.post("/api/manual", json=_manual_body(
        [{"table": t, "id": lid, "amount": 50}, {"table": t, "id": lid, "amount": 50}]))
    assert r.status_code == 400
    assert not _q("SELECT * FROM recon_manual_match")             # nothing half-written
    assert _q("SELECT match_status FROM recon_bank_lines WHERE id=?", excluded)[0]["match_status"] == "excluded"


def test_manual_takes_amount_from_the_line_and_blocks_a_second_booking(client):
    lid = _manual_setup(client)
    body = _manual_body([{"table": "recon_bank_lines", "id": lid, "amount": 5000}])
    r = client.post("/api/manual", json=body)
    assert r.status_code == 200 and r.json()["ext_amount"] == 100       # the DB credit, not 5000
    assert _q("SELECT amount FROM recon_manual_match_line")[0]["amount"] == 100
    again = client.post("/api/manual", json=body)                      # double-click
    assert again.status_code == 409
    assert len(_q("SELECT * FROM recon_manual_match")) == 1


def test_manual_rejects_bad_or_foreign_pos_days(client):
    lid = _manual_setup(client)
    ext = [{"table": "recon_bank_lines", "id": lid, "amount": 100}]
    for day in ("2026-06-10<script>", "2026-07-01", "2026-06-99", None):
        r = client.post("/api/manual", json=_manual_body(ext, [{"day": day, "amount": 10}]))
        assert r.status_code == 400, day


def test_manual_delete_restores_excluded_and_keeps_shared_lines(client):
    _manual_setup(client)
    shared = _line(30)
    # legacy rows from before the validation: one line in two matches, one excluded line
    # that a match flipped to 'manual' (its exclusion is on the audit trail).
    was_excl = _line(20, status="excluded")
    _x("INSERT INTO recon_audit_log(ts,entity,entity_id,action,field,old_val,new_val,by)"
       " VALUES('2026-06-11T00:00:00','bank_line',?,'update','match_status','unmatched','excluded','x')",
       was_excl)
    m1 = _x("INSERT INTO recon_manual_match(branch_id,category,bank,period_month,pos_amount,"
            "ext_amount,variance,pos_days,note,matched_by) VALUES(4,'QR','KBANK',?,50,50,0,"
            "'[{\"day\":\"2026-06-10\",\"amount\":50}]','legacy','x')", PM)
    m2 = _x("INSERT INTO recon_manual_match(branch_id,category,bank,period_month,pos_amount,"
            "ext_amount,variance,pos_days,note,matched_by) VALUES(4,'QR','KBANK',?,30,30,0,"
            "'[{\"day\":\"2026-06-10\",\"amount\":30}]','legacy','x')", PM)
    for mid, eid in ((m1, shared), (m1, was_excl), (m2, shared)):
        _x("INSERT INTO recon_manual_match_line(match_id,ext_table,ext_id,amount) VALUES(?,?,?,1)",
           mid, "recon_bank_lines", eid)
    _x("UPDATE recon_bank_lines SET match_status='manual' WHERE id IN (?,?)", shared, was_excl)
    _login(client, "deleter")
    assert client.delete(f"/api/manual/{m1}").status_code == 200
    st = {r["id"]: r["match_status"] for r in _q("SELECT id, match_status FROM recon_bank_lines")}
    assert st[shared] == "manual"            # m2 still books it
    assert st[was_excl] == "excluded"        # back to what it was, not 'unmatched'
    a = _audit("manual_match", m1, "delete")
    assert a and all(r["by"] == "deleter" for r in a)
    fields = {r["field"]: r["old_val"] for r in a}
    assert fields.get("branch_id") == "4" and fields.get("period_month") == PM
    assert client.delete(f"/api/manual/{m2}").status_code == 200
    assert _q("SELECT match_status FROM recon_bank_lines WHERE id=?", shared)[0]["match_status"] == "unmatched"


# ── 3. one day twice in one request can't over-allocate ───────────────────────
def test_manual_sums_pos_days_per_day_within_the_request(client):
    lid = _manual_setup(client)
    r = client.post("/api/manual", json=_manual_body(
        [{"table": "recon_bank_lines", "id": lid, "amount": 100}],
        [{"day": "2026-06-10", "amount": 100}, {"day": "2026-06-10", "amount": 100}]))
    assert r.status_code == 409


# ── 4. a double-clicked channel move is stored once ───────────────────────────
def test_channel_move_rejects_identical_double_submit(client):
    b = {"branch_id": 4, "period_month": PM, "from_category": "CASH", "to_category": "QR",
         "to_bank": "KBANK", "amount": 500, "note": "กดผิดช่องทางจริง ๆ"}
    assert client.post("/api/channel-move", json=b).status_code == 200
    assert client.post("/api/channel-move", json=b).status_code == 409
    assert client.post("/api/channel-move", json={**b, "amount": 400}).status_code == 200
    assert len(_q("SELECT * FROM recon_channel_move")) == 2


# ── 5/6/7. sign-off transitions ───────────────────────────────────────────────
def _session(client, created_by="alice", pm=PM):
    return client.post("/api/sessions", json={"branch_id": 4, "period_month": pm,
                                              "created_by": created_by}).json()["session_id"]


def test_close_requires_an_approved_month(client):
    sid = _session(client)
    assert client.patch(f"/api/sessions/{sid}/status",
                        data={"status": "approved", "approved_by": "alice"}).status_code == 409
    r = client.patch(f"/api/sessions/{sid}/status", data={"status": "closed", "approved_by": "alice"})
    assert r.status_code == 409                                    # no back door past SoD
    assert _q("SELECT status FROM recon_sessions")[0]["status"] == "draft"
    assert client.patch(f"/api/sessions/{sid}/status",
                        data={"status": "approved", "approved_by": "bob"}).status_code == 200
    assert client.patch(f"/api/sessions/{sid}/status",
                        data={"status": "closed", "approved_by": "carol"}).status_code == 200
    row = _q("SELECT status, approved_by FROM recon_sessions")[0]
    assert row == {"status": "closed", "approved_by": "bob"}       # the approver stands


def test_reapproval_is_refused_and_keeps_the_approver(client):
    sid = _session(client)
    assert client.patch(f"/api/sessions/{sid}/status",
                        data={"status": "approved", "approved_by": "bob"}).status_code == 200
    r = client.patch(f"/api/sessions/{sid}/status", data={"status": "approved", "approved_by": "carol"})
    assert r.status_code == 409
    assert _q("SELECT approved_by FROM recon_sessions")[0]["approved_by"] == "bob"
    assert len(_q("SELECT * FROM recon_close_snapshot WHERE session_id=?", sid)) == 1


def test_concurrent_approvals_only_one_wins(client):
    sid = _session(client)
    barrier, codes = threading.Barrier(2), []

    def go(who):
        barrier.wait()
        codes.append(client.patch(f"/api/sessions/{sid}/status",
                                  data={"status": "approved", "approved_by": who}).status_code)
    ts = [threading.Thread(target=go, args=(w,)) for w in ("bob", "carol")]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert sorted(codes) == [200, 409]
    assert len(_audit("session", sid)) == 1


# ── 8. statement-code override leaves locked months alone; LIKE is literal ────
def test_statement_override_skips_locked_months_and_escapes_like(client):
    open_l = _line(500, date="2026-06-05", channel="CASH_DEP", description="SDCH 123")
    closed_l = _line(500, date="2026-07-05", channel="CASH_DEP", description="SDCH 456")
    _x("INSERT INTO recon_sessions(branch_id,period_month,status,created_by,approved_by)"
       " VALUES(4,'2026-07','closed','a','b')")
    r = client.post("/api/statement-codes/override", json={"code": "SDCH", "channel": "QR"})
    assert r.status_code == 200 and r.json()["lines_reclassified"] == 1
    ch = {x["id"]: x["channel"] for x in _q("SELECT id, channel FROM recon_bank_lines")}
    assert ch[open_l] == "QR" and ch[closed_l] == "CASH_DEP"
    r = client.post("/api/statement-codes/override", json={"code": "_", "channel": "OTHER"})
    assert r.json()["lines_reclassified"] == 0                     # '_' is not a wildcard


# ── 9. payment-map PATCH is partial ───────────────────────────────────────────
def test_payment_map_patch_keeps_fields_not_sent(client):
    pm = {r["payment_code_norm"]: r for r in client.get("/api/payment-map").json()}
    e = pm["Entertain"]
    client.patch(f"/api/payment-map/{e['id']}", json={"category": e["category"], "bank": e["bank"],
                                                      "reconcile": 0, "notes": "keep me"})
    client.patch(f"/api/payment-map/{e['id']}", json={"category": "OTHER", "bank": "NONE"})
    after = {r["payment_code_norm"]: r for r in client.get("/api/payment-map").json()}["Entertain"]
    assert (after["category"], after["reconcile"], after["notes"]) == ("OTHER", 0, "keep me")
    assert client.patch("/api/payment-map/999999", json={"category": "QR"}).status_code == 404
    assert client.patch(f"/api/payment-map/{e['id']}", json={"bank": "NOPE"}).status_code == 400


# ── 10. audit gaps + identity ─────────────────────────────────────────────────
def test_deletes_and_config_changes_are_audited_with_scope(client):
    _login(client, "auditee")
    a = client.post("/api/lanes/adj", json={"branch_id": 4, "period_month": PM, "category": "QR",
                                            "bank": "KBANK", "note": "accepted for testing purposes",
                                            "adj_type": "accept"}).json()
    client.delete(f"/api/lanes/adj/{a['id']}")
    d = client.post("/api/day-ack", json={"branch_id": 4, "period_month": PM, "category": "QR",
                                          "day": "2026-06-05", "note": "acknowledged for test reasons"})
    assert d.status_code == 200, d.text
    client.delete(f"/api/day-ack/{d.json()['id']}")
    mv = client.post("/api/channel-move", json={"branch_id": 4, "period_month": PM,
        "from_category": "CASH", "to_category": "QR", "to_bank": "KBANK", "amount": 100,
        "note": "กดผิดช่องทางจริง ๆ"}).json()
    client.delete(f"/api/channel-move/{mv['id']}")
    for ent, eid in (("lane_adj", a["id"]), ("day_ack", d.json()["id"]), ("channel_move", mv["id"])):
        rows = _audit(ent, eid, "delete")
        assert rows and all(r["by"] == "auditee" for r in rows), ent
        f = {r["field"]: r["old_val"] for r in rows}
        assert f.get("branch_id") == "4" and f.get("period_month") == PM, ent

    pm = {r["payment_code_norm"]: r for r in client.get("/api/payment-map").json()}
    client.delete(f"/api/payment-map/{pm['Entertain']['id']}")
    rows = _audit("payment_map", pm["Entertain"]["id"], "delete")
    assert rows and any(r["old_val"] == "Entertain" for r in rows)

    acc = client.post("/api/bank-accounts", json={"branch_id": 4, "bank": "KTB",
                                                   "account_no": "111"}).json()["account_id"]
    client.patch(f"/api/bank-accounts/{acc}", json={"branch_id": 4, "bank": "KTB", "account_no": "222"})
    client.delete(f"/api/bank-accounts/{acc}")
    assert {r["action"] for r in _audit("bank_account", acc)} == {"create", "update", "delete"}
    mid = client.post("/api/acquirer-mids", json={"branch_id": 4, "acquirer": "KTB",
                                                   "merchant_id": "M1"}).json()["mid_id"]
    client.patch(f"/api/acquirer-mids/{mid}", json={"branch_id": 4, "acquirer": "KTB", "merchant_id": "M2"})
    client.delete(f"/api/acquirer-mids/{mid}")
    assert {r["action"] for r in _audit("acquirer_mid", mid)} == {"create", "update", "delete"}
    client.put("/api/account-map", json={"slot": "QR", "account_code": "1100", "updated_by": "x"})
    rows = _audit("account_map")
    assert rows and all(r["by"] == "auditee" for r in rows)


def test_logged_in_user_beats_client_supplied_identity(client):
    sid = _session(client, created_by="prep")
    client.post(f"/api/sessions/{sid}/run-match")
    ds = client.get(f"/api/sessions/{sid}/discrepancies").json()
    assert len(ds) >= 2
    _login(client, "alice")
    client.patch(f"/api/discrepancies/{ds[0]['id']}/resolve",
                 json={"resolution": "accepted", "resolution_note": "ok", "resolved_by": "mallory"})
    client.patch(f"/api/discrepancies/{ds[1]['id']}/assign", data={"assigned_to": "Nok"})
    rows = _q("SELECT resolved_by FROM recon_discrepancies WHERE id=?", ds[0]["id"])
    assert rows[0]["resolved_by"] == "alice"
    assert {r["by"] for r in _audit("discrepancy")} == {"alice"}
    pm = {r["payment_code_norm"]: r for r in client.get("/api/payment-map").json()}
    client.patch(f"/api/payment-map/{pm['Entertain']['id']}", json={"reconcile": 1 - pm["Entertain"]["reconcile"],
                                                                 "updated_by": "mallory"})
    assert {r["by"] for r in _audit("payment_map", pm["Entertain"]["id"])} == {"alice"}
    lid = _line(10)
    client.patch(f"/api/bank-lines/{lid}/ignore", json={"ignored": True, "by": "mallory"})
    assert {r["by"] for r in _audit("bank_line", lid)} == {"alice"}


# ── 11. login gate fails closed and covers sensitive reads ────────────────────
_SENSITIVE_GETS = ("/api/reports/audit-trail?period_month=2026-06", "/api/audit",
                   f"/api/monthly-report?branch_id=4&period_month={PM}",
                   "/api/sessions/1/export.xlsx", "/api/attachments/1/file", "/api/snapshots/1/file",
                   "/api/daily-report?day=2026-06-10")


def test_enforced_login_gates_sensitive_reads(client, monkeypatch):
    if core._parse_session is None:
        pytest.skip("backend.auth not importable — the gate answers 503 (tested below)")
    monkeypatch.setenv("RECON_ENFORCE_AUTH", "1")
    for url in _SENSITIVE_GETS:
        assert client.get(url).status_code == 401, url
    for url in ("/health", "/api/branches", f"/api/v2/overview?month={PM}"):
        assert client.get(url).status_code == 200, url
    _login(client, "alice")
    assert client.get("/api/reports/audit-trail?period_month=2026-06").status_code == 200
    monkeypatch.delenv("RECON_ENFORCE_AUTH")
    client.cookies.clear()
    assert client.get("/api/audit").status_code == 200              # flag off → unchanged


def test_enforced_login_fails_closed_without_auth_module(client, monkeypatch):
    monkeypatch.setenv("RECON_ENFORCE_AUTH", "1")
    monkeypatch.setattr(main, "_parse_session", None)
    assert client.post("/api/sessions", json={"branch_id": 4, "period_month": PM}).status_code == 503
    assert client.get("/api/audit").status_code == 503
    assert client.get("/health").status_code == 200


# ── 12. a non-ASCII token is a 403, not a 500 ─────────────────────────────────
def test_non_ascii_tokens_are_refused_cleanly(client, monkeypatch):
    monkeypatch.setenv("OSHINEI_SEED_TOKEN", "abc")
    monkeypatch.setenv("OSHINEI_MANAGER_TOKEN", "mgr")
    bad = "é".encode("latin-1")
    r = client.post("/api/sessions", json={"branch_id": 4, "period_month": PM},
                    headers=[(b"X-Upload-Token", bad)])
    assert r.status_code == 403
    r = client.post("/api/method-config", json={"category": "QR"},
                    headers=[(b"X-Upload-Token", b"abc"), (b"X-Manager-Token", bad)])
    assert r.status_code == 403


# ── 13. unknown ids are 404; day-ack validates day and category ───────────────
def test_unknown_ids_404_and_day_ack_validation(client):
    assert client.patch("/api/discrepancies/999999/resolve",
                        json={"resolution": "accepted"}).status_code == 404
    assert client.patch("/api/discrepancies/999999/assign", data={"assigned_to": "x"}).status_code == 404
    assert client.patch("/api/lines/grab/999999/fee-vat", json={"fee_manual": 1}).status_code == 404
    assert not _audit("discrepancy") and not _audit("line")
    base = {"branch_id": 4, "period_month": PM, "category": "QR", "note": "acknowledged for test reasons"}
    assert client.post("/api/day-ack", json={**base, "day": "2026-06-99"}).status_code == 400
    assert client.post("/api/day-ack", json={**base, "day": "2026-06-05",
                                             "category": "BOGUS"}).status_code == 400


# ── 14. day-ack stores the engine's variance, not the client's ────────────────
def test_day_ack_stores_server_side_variance(client):
    pm = {r["payment_code_norm"]: r for r in client.get("/api/payment-map").json()}
    client.patch(f"/api/payment-map/{pm['KNNQR']['id']}", json={"category": "QR", "bank": "KBANK"})
    client.patch(f"/api/payment-map/{pm['K-SHOP']['id']}", json={"category": "QR", "bank": "KBANK"})
    _pos_day([(4, "2026-06-10", "KNNQR", 50000), (4, "2026-06-11", "KNNQR", 50000),
              (4, "2026-06-12", "K-SHOP", 50000)])
    for d, amt in (("2026-06-10", 40000), ("2026-06-11", 50000), ("2026-06-12", 50000)):
        _line(amt, date=d)
    r = client.post("/api/day-ack", json={"branch_id": 4, "period_month": PM, "category": "QR",
                                          "day": "2026-06-10", "variance": 1,
                                          "note": "ลูกค้าจ่าย QR แต่ยอดเข้าน้อยกว่า"})
    assert r.status_code == 200
    assert _q("SELECT variance FROM recon_day_ack")[0]["variance"] == -10000


# ── 15. bulk resolve reports what it actually changed ─────────────────────────
def test_bulk_resolve_counts_only_rows_it_changed(client):
    s1, s2 = _session(client), _session(client, pm="2026-05")
    client.post(f"/api/sessions/{s1}/run-match")
    _x("INSERT INTO recon_discrepancies(session_id,disc_type,source_type,resolution)"
       " VALUES(?,'amount_mismatch','bank','pending')", s2)
    other = _q("SELECT id FROM recon_discrepancies WHERE session_id=?", s2)[0]["id"]
    mine = [d["id"] for d in client.get(f"/api/sessions/{s1}/discrepancies").json()][:1]
    r = client.post(f"/api/sessions/{s1}/discrepancies/bulk-resolve",
                    json={"ids": mine + [other, 999999], "resolution": "accepted"})
    assert r.json()["updated"] == 1
    assert "× 1 " in _audit("session", s1)[-1]["new_val"]
    assert _q("SELECT resolution FROM recon_discrepancies WHERE id=?", other)[0]["resolution"] == "pending"


# ── 16/17. input validation and missing POS-day table ─────────────────────────
def test_daily_dashboard_rejects_unpadded_day(client):
    assert client.get("/api/daily-dashboard", params={"day": "2026-6-1"}).status_code == 400


def test_manual_without_pos_day_table_and_overview_period(client):
    r = client.get("/api/manual", params={"branch_id": 4, "category": "QR", "bank": "KBANK",
                                          "period_month": PM})
    assert r.status_code == 200 and r.json()["pos_days"] == []
    lid = _line(100)
    r = client.post("/api/manual", json=_manual_body([{"table": "recon_bank_lines", "id": lid, "amount": 100}]))
    assert r.status_code == 409
    assert client.get("/api/overview", params={"period_month": "2026-6"}).status_code == 400
