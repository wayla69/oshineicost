"""Regression tests for the 2026-10 recon audit (11 verified findings). One block
per finding, numbered as in the PR, so a re-break points at the rule it breaks.
Platform-neutral: temp DB per test, env via monkeypatch, no network."""
import sqlite3
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import recon.main as main  # noqa: E402
import recon.core as core  # noqa: E402
import recon.routers.uploads as uploads  # noqa: E402
from recon.parsers.bank import _parse_thai_date  # noqa: E402
from test_recon import _make_db, DEMO_POS  # noqa: E402
from test_fix_routers import _q, _x, _pos_day, _line, _manual_setup, _manual_body  # noqa: E402
from test_recon_login import env, on, _boss, _add, _as, NEW_PW  # noqa: E402,F401  (fixtures)
from approval_helpers import make_approvable  # noqa: E402

PM = "2026-06"


@pytest.fixture
def client(tmp_path, monkeypatch):
    core._DB_PATH = main._DB_PATH = _make_db(tmp_path, DEMO_POS)
    for k in ("OSHINEI_SEED_TOKEN", "OSHINEI_MANAGER_TOKEN", "RECON_ENFORCE_AUTH", "RECON_LOGIN"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(uploads, "_ATTACH_DIR", tmp_path / "att")
    with TestClient(main.app) as cl:
        yield cl


def _lock(month="2026-06", branch_id=4):
    _x("INSERT INTO recon_sessions(branch_id, period_month, status, created_by) "
       "VALUES (?, ?, 'approved', 'x')", branch_id, month)


def _audit_rows(entity, action=None):
    sql, a = "SELECT * FROM recon_audit_log WHERE entity=?", [entity]
    if action:
        sql += " AND action=?"; a.append(action)
    return _q(sql, *a)


# ── 1. re-importing Grab / LINE MAN / card reports skips stored lines ─────────
def _fake_parse(monkeypatch, rows_by_name):
    def fake(source_type, raw, provider, filename=""):
        return ("X", [dict(r) for r in rows_by_name[filename]], 0, raw)
    monkeypatch.setattr(uploads, "_parse_or_422", fake)


def _confirm(client, st, name, data=b"x"):
    return client.post(f"/api/uploads/{st}/confirm",
                       files={"file": (name, data + name.encode(), "text/csv")},
                       data={"branch_id": "4", "period_month": PM})


@pytest.mark.parametrize("st,table", [("grab", "recon_grab_lines"), ("lineman", "recon_lineman_lines")])
def test_01_platform_reimport_skips_stored_orders(client, monkeypatch, st, table):
    o = lambda oid, d: {"order_date": d, "order_id": oid, "order_status": "Completed", "food_amount": 100}
    _fake_parse(monkeypatch, {"a.csv": [o("A1", "2026-06-10"), o("A2", "2026-06-11")],
                              "b.csv": [o("A1", "2026-06-10"), o("A2", "2026-06-11"), o("A3", "2026-06-12")],
                              "c.csv": [o("A1", "2026-06-10")]})
    assert _confirm(client, st, "a.csv").json()["rows_saved"] == 2
    r = _confirm(client, st, "b.csv").json()                      # re-export + one new order
    assert (r["rows_saved"], r["duplicate_lines"]) == (1, 2)
    assert _confirm(client, st, "c.csv").status_code == 409        # nothing new at all
    assert len(_q(f"SELECT * FROM {table}")) == 3


def test_01_card_reimport_skips_stored_settlements_and_preview_counts(client, monkeypatch):
    c = lambda batch, gross, mid="M1": {"settlement_date": "2026-06-10", "merchant_id": mid,
                                        "batch_no": batch, "card_type": "VISA", "gross_amount": gross}
    _fake_parse(monkeypatch, {"a.csv": [c("B1", 500), c("B2", 700)],
                              "b.csv": [c("B1", 500), c("B2", 700), c("B3", 900)],
                              "n.csv": [{"settlement_date": "2026-06-10", "gross_amount": 500}]})
    assert _confirm(client, "credit_card", "a.csv").json()["rows_saved"] == 2
    pv = client.post("/api/uploads/credit_card/preview",
                     files={"file": ("b.csv", b"zz", "text/csv")},
                     data={"branch_id": "4", "period_month": PM}).json()
    assert pv["duplicate_lines"] == 2
    r = _confirm(client, "credit_card", "b.csv").json()
    assert (r["rows_saved"], r["duplicate_lines"]) == (1, 2)
    # no merchant id and no batch → can't tell two genuine rows apart → kept
    assert _confirm(client, "credit_card", "n.csv").json()["duplicate_lines"] == 0


# ── 2. attachments: server-side type, nosniff, period lock on create ──────────
def test_02_attachment_type_is_server_side_and_nosniff(client):
    r = client.post("/api/attachments", data={"branch_id": "4", "day": "2026-06-10"},
                    files={"file": ("slip.png", b"<script>alert(1)</script>", "text/html")})
    assert r.status_code == 200, r.text
    aid = r.json()["id"]
    assert _q("SELECT mime FROM recon_attachment WHERE id=?", aid)[0]["mime"] == "image/png"
    f = client.get(f"/api/attachments/{aid}/file")
    assert f.headers["content-type"] == "image/png"
    assert f.headers["x-content-type-options"] == "nosniff"
    assert f.headers["content-disposition"].startswith("inline")
    # an OLD row whose stored mime came from the client is served by extension too
    _x("UPDATE recon_attachment SET mime='text/html' WHERE id=?", aid)
    assert client.get(f"/api/attachments/{aid}/file").headers["content-type"] == "image/png"
    # a stored path with an unknown extension downloads, never renders
    (uploads._ATTACH_DIR / "x").mkdir(parents=True, exist_ok=True)
    (uploads._ATTACH_DIR / "x" / "evil.html").write_bytes(b"<h1>x</h1>")
    bad = _x("INSERT INTO recon_attachment(branch_id,day,file_name,mime,file_path) "
             "VALUES (4,'2026-06-10','evil.html','text/html','x/evil.html')")
    g = client.get(f"/api/attachments/{bad}/file")
    assert g.headers["content-type"] == "application/octet-stream"
    assert g.headers["content-disposition"].startswith("attachment")
    assert client.get("/health").headers["x-content-type-options"] == "nosniff"   # global


def test_02_attachment_create_refused_on_a_locked_month(client):
    _lock()
    r = client.post("/api/attachments", data={"branch_id": "4", "day": "2026-06-10"},
                    files={"file": ("slip.png", b"png", "image/png")})
    assert r.status_code == 423
    assert not _q("SELECT * FROM recon_attachment")


# ── 3. a locked month's next-month window: claimed bank lines can't change ─────
def _june_claims_a_july_deposit():
    _pos_day([(4, "2026-06-30", "Cash", 5000), (4, "2026-06-29", "Cash", 3000)])
    claimed = _line(5000, date="2026-07-01", channel="CASH_DEP", provider="ktb")
    from recon.matcher import month_claimed_bank_line_ids
    with main.get_db() as conn:
        assert claimed in month_claimed_bank_line_ids(conn, 4, "2026-06")
    _lock()
    return claimed


def test_03_claimed_next_month_line_cannot_be_ignored_matched_or_deleted(client):
    claimed = _june_claims_a_july_deposit()
    assert client.patch(f"/api/bank-lines/{claimed}/ignore", json={"ignored": True}).status_code == 423
    r = client.post("/api/bank-lines/bulk-ignore", json={"line_ids": [claimed]}).json()
    assert r["skipped"] and not r["ignored"]
    _pos_day([(4, "2026-07-01", "Cash", 5000)])
    r = client.post("/api/manual", json={
        "branch_id": 4, "category": "CASH", "bank": "NONE", "period_month": "2026-07",
        "pos_days": [{"day": "2026-07-01", "amount": 5000}],
        "ext": [{"table": "recon_bank_lines", "id": claimed}], "note": "ทดสอบจับคู่"})
    assert r.status_code == 423, r.text
    up = _q("SELECT upload_id FROM recon_bank_lines WHERE id=?", claimed)[0]["upload_id"]
    assert client.delete(f"/api/uploads/{up}").status_code == 423
    assert _q("SELECT match_status FROM recon_bank_lines WHERE id=?", claimed)[0]["match_status"] == "unmatched"


def test_03_unclaimed_lines_stay_editable_and_restoring_a_claimable_one_is_refused(client):
    _june_claims_a_july_deposit()
    free = _line(777, date="2026-07-01", channel="QR", provider="kbank")
    assert client.patch(f"/api/bank-lines/{free}/ignore", json={"ignored": True}).status_code == 200
    # an excluded 3,000 deposit June WOULD claim (its 29th) if it came back
    hidden = _line(3000, date="2026-07-01", channel="CASH_DEP", provider="ktb", status="excluded")
    r = client.patch(f"/api/bank-lines/{hidden}/ignore", json={"ignored": False})
    assert r.status_code == 423, r.text
    assert _q("SELECT match_status FROM recon_bank_lines WHERE id=?", hidden)[0]["match_status"] == "excluded"


def test_03_global_config_that_moves_a_locked_month_needs_confirmation(client):
    _lock()
    pm = {r["payment_code_norm"]: r for r in client.get("/api/payment-map").json()}
    knn = pm["KNNQR"]["id"]                                       # June POS carries KNNQR
    r = client.patch(f"/api/payment-map/{knn}", json={"bank": "KTB"})
    assert r.status_code == 409 and "ยืนยันเพื่อบันทึก" in r.json()["detail"]
    assert client.patch(f"/api/payment-map/{knn}", json={"notes": "หมายเหตุ"}).status_code == 200
    assert client.patch(f"/api/payment-map/{knn}?confirm_locked=1", json={"bank": "KTB"}).status_code == 200
    assert any(a["field"] == "locked_months_affected" and "4/2026-06" in a["new_val"]
               for a in _audit_rows("payment_map"))
    cash = next(c for c in client.get("/api/method-config").json()
                if c["category"] == "CASH")
    body = {k: cash[k] for k in ("category", "bank", "doc1", "doc2", "method", "tol_abs",
                                 "tol_abs_day", "merge_days", "fee_vat_expected")}
    body["lag_days"] = 5
    assert client.patch(f"/api/method-config/{cash['id']}", json=body).status_code == 409
    assert client.patch(f"/api/method-config/{cash['id']}?confirm_locked=1", json=body).status_code == 200


def test_03_statement_override_skips_the_locked_months_window(client):
    early = _line(500, date="2026-07-02", channel="QR", description="ZZTEST 1")
    late = _line(500, date="2026-07-25", channel="QR", description="ZZTEST 2")
    _lock()
    r = client.post("/api/statement-codes/override", json={"code": "ZZTEST", "channel": "CASH_DEP"})
    assert r.status_code == 200 and r.json()["lines_reclassified"] == 1
    ch = {x["id"]: x["channel"] for x in _q("SELECT id, channel FROM recon_bank_lines")}
    assert (ch[early], ch[late]) == ("QR", "CASH_DEP")


# ── 4. a channel MOVE lands on its own sale day ───────────────────────────────
def test_04_move_with_a_day_shifts_that_day_legacy_keeps_last_day(client):
    from recon.matcher import daily_reconciliation
    _pos_day([(4, "2026-06-10", "Cash", 1000), (4, "2026-06-20", "Cash", 1000),
              (4, "2026-06-10", "KNNQR", 500), (4, "2026-06-20", "KNNQR", 500)])
    base = {"branch_id": 4, "period_month": PM, "from_category": "CASH", "to_category": "QR",
            "to_bank": "KBANK", "amount": 300, "note": "ลูกค้าสแกน QR แต่กดเงินสด"}
    assert client.post("/api/channel-move", json={**base, "day": "2026-07-01"}).status_code == 400
    r = client.post("/api/channel-move", json={**base, "day": "2026-06-10"})
    assert r.status_code == 200 and r.json()["day"] == "2026-06-10"
    _x("INSERT INTO recon_channel_move(branch_id,period_month,from_category,from_bank,"
       "to_category,to_bank,amount,note) VALUES (4,?,'CASH','NONE','QR','KBANK',100,'legacy row')", PM)
    with main.get_db() as conn:
        days = {d["day"]: {(l["category"], l["bank"]): l["pos"] for l in d["lanes"]}
                for d in daily_reconciliation(conn, 4, PM)["days"]}
    assert days["2026-06-10"][("CASH", "NONE")] == 700 and days["2026-06-10"][("QR", "KBANK")] == 800
    assert days["2026-06-20"][("CASH", "NONE")] == 900 and days["2026-06-20"][("QR", "KBANK")] == 600
    assert client.get("/api/channel-move", params={"branch_id": 4, "period_month": PM}).json()[-1]["day"] \
        == "2026-06-10"


# ── 5. audit actor = the signed-in user, never the client's name ──────────────
def test_05_actor_is_the_session_user(on, tmp_path, monkeypatch):
    monkeypatch.setattr(uploads, "_ATTACH_DIR", tmp_path / "att")
    mgr = _boss(on)
    r = mgr.post("/api/deposit-slips", json={"branch_id": 4, "sale_day": "2026-06-10",
                                              "amount": 100, "created_by": "forged"})
    assert r.status_code == 200
    assert _q("SELECT created_by FROM recon_deposit_slip")[0]["created_by"] == "boss"
    assert mgr.delete(f"/api/deposit-slips/{r.json()['id']}").status_code == 200
    assert {a["by"] for a in _audit_rows("deposit_slip")} == {"boss"}
    a = mgr.post("/api/attachments", data={"branch_id": "4", "day": "2026-06-10", "uploaded_by": "forged"},
                 files={"file": ("s.jpg", b"jpg", "image/jpeg")})
    assert a.status_code == 200
    assert _q("SELECT uploaded_by FROM recon_attachment")[0]["uploaded_by"] == "boss"
    cfg = mgr.get("/api/method-config").json()[0]
    body = {k: cfg[k] for k in ("category", "bank", "doc1", "doc2", "method", "tol_abs",
                                "tol_abs_day", "lag_days", "merge_days", "fee_vat_expected")}
    body.update(notes="x", updated_by="forged", merge_days=(cfg["merge_days"] or 1) % 7 + 1)
    assert mgr.patch(f"/api/method-config/{cfg['id']}", json=body).status_code == 200
    assert {a["by"] for a in _audit_rows("method_config", "update")} == {"boss"}
    assert _q("SELECT updated_by FROM recon_method_config WHERE id=?", cfg["id"])[0]["updated_by"] == "boss"


# ── 6. SoD: anyone who worked on the month can't approve it ───────────────────
def test_06_worker_on_the_month_cannot_approve_and_blank_preparer_is_refused(on):
    mgr = _boss(on)
    _, pw2 = _add(mgr, "boss2", "manager")
    m2 = _as("boss2", pw2)
    _, pwa = _add(mgr, "acc1", "accountant")
    acc = _as("acc1", pwa)
    sid = acc.post("/api/sessions", json={"branch_id": 4, "period_month": PM}).json()["session_id"]
    make_approvable(main._DB_PATH, sid)
    assert mgr.post("/api/day-ack", json={"branch_id": 4, "period_month": PM, "category": "QR",
                                          "day": "2026-06-05", "note": "ผู้จัดการรับทราบผลต่างเอง"}
                    ).status_code == 200
    r = mgr.patch(f"/api/sessions/{sid}/status", data={"status": "approved", "password": NEW_PW})
    assert r.status_code == 409 and "SoD" in r.json()["detail"]   # boss acked a day: a preparer
    assert m2.patch(f"/api/sessions/{sid}/status",
                    data={"status": "approved", "password": NEW_PW}).status_code == 200
    # a month nobody identifiable prepared (blank / machine-token creator)
    for bid, who in ((5, None), (4, "token:bulk")):
        _x("INSERT INTO recon_sessions(branch_id, period_month, status, created_by) "
           "VALUES (?, '2026-05', 'draft', ?)", bid, who)
        s = _q("SELECT session_id FROM recon_sessions WHERE branch_id=? AND period_month='2026-05'",
               bid)[0]["session_id"]
        make_approvable(main._DB_PATH, s)
        r = m2.patch(f"/api/sessions/{s}/status", data={"status": "approved", "password": NEW_PW})
        assert r.status_code == 409 and "ไม่มีผู้จัดทำ" in r.json()["detail"], r.text
        assert acc.patch(f"/api/sessions/{s}/status", data={"status": "in_review"}).status_code == 200
        assert m2.patch(f"/api/sessions/{s}/status",
                        data={"status": "approved", "password": NEW_PW}).status_code == 200


# ── 7. the write token alone opens the upload endpoints only ──────────────────
def test_07_session_less_token_reaches_uploads_only(on, monkeypatch):
    monkeypatch.setenv("OSHINEI_SEED_TOKEN", "bulk:tok-7")
    m, h = TestClient(main.app), {"X-Upload-Token": "tok-7"}
    for method, path, kw in (("post", "/api/day-ack", {"json": {}}),
                             ("post", "/api/lanes/adj", {"json": {}}),
                             ("post", "/api/channel-move", {"json": {}}),
                             ("patch", "/api/bank-lines/1/ignore", {"json": {"ignored": True}}),
                             ("post", "/api/payment-map", {"json": {}}),
                             ("post", "/api/attachments", {"data": {}})):
        assert getattr(m, method)(path, headers=h, **kw).status_code == 401, path
    r = m.post("/api/uploads/bulk", headers=h, files=[("files", ("a.txt", b"x", "text/plain"))],
               data={"branch_id": "4", "period_month": PM})
    assert r.status_code == 200 and r.json()["summary"]["skipped"] == 1     # past both gates


# ── 8. approval re-runs a stale match, never signs off old results ────────────
def test_08_stale_results_are_rerun_before_approval(client):
    sid = client.post("/api/sessions", json={"branch_id": 4, "period_month": PM,
                                             "created_by": "prep"}).json()["session_id"]
    assert not _q("SELECT * FROM recon_category_result WHERE session_id=?", sid)
    r = client.patch(f"/api/sessions/{sid}/status", data={"status": "approved", "approved_by": "mgr"})
    assert r.status_code == 409                    # the re-run found the demo month's open items
    assert _q("SELECT * FROM recon_category_result WHERE session_id=?", sid)
    assert _q("SELECT status FROM recon_sessions WHERE session_id=?", sid)[0]["status"] == "draft"


# ── 9. last month's CARD claims are excluded from this month's cash ───────────
def test_09_prev_month_card_settlement_is_not_this_months_cash(client):
    up = _x("INSERT INTO recon_uploads(source_type,branch_id,provider,file_name,period_month,"
            "row_count,status) VALUES('credit_card',4,'kbank','cc',?,1,'confirmed')", PM)
    _x("INSERT INTO recon_cc_lines(upload_id,branch_id,settlement_date,acquirer,gross_amount,"
       "net_amount) VALUES(?,4,'2026-06-30','KBANK',1000,1000)", up)
    # a KTB batch the same day: the payout matcher's single CREDIT stream sums the day
    # (1,500) and finds no such credit — only the per-acquirer card leg claims the 1,000
    _x("INSERT INTO recon_cc_lines(upload_id,branch_id,settlement_date,acquirer,gross_amount,"
       "net_amount) VALUES(?,4,'2026-06-30','KTB',500,500)", up)
    line = _line(1000, date="2026-07-01", channel="TRANSFER", provider="kbank")
    _pos_day([(4, "2026-07-01", "Cash", 1000)])
    from recon.matcher import cash_deposit_matching, credit_three_way
    from recon.matcher.cash import _prev_month_claims
    with main.get_db() as conn:
        assert line in credit_three_way(conn, 4, PM)["bank_line_ids"]    # June's card leg owns it
        assert line in _prev_month_claims(conn, 4, "2026-07")
        assert line not in cash_deposit_matching(conn, 4, "2026-07")["consumed_ids"]


# ── 10. manual match may use a line inside the lane's next-month window ───────
def test_10_manual_match_accepts_next_month_window_lines(client):
    _manual_setup(client)
    inside = _line(100, date="2026-07-01")                         # QR·KBANK lag 1 → up to 07-01
    outside = _line(100, date="2026-07-03")
    lst = client.get("/api/manual", params={"branch_id": 4, "category": "QR", "bank": "KBANK",
                                            "period_month": PM}).json()
    nxt = {l["id"] for l in lst["ext_lines"] if l.get("next_month")}
    assert inside in nxt and outside not in nxt
    t = "recon_bank_lines"
    assert client.post("/api/manual", json=_manual_body([{"table": t, "id": outside}])).status_code == 400
    r = client.post("/api/manual", json=_manual_body([{"table": t, "id": inside}]))
    assert r.status_code == 200, r.text
    # the line's own month is locked → refused (and undo is refused too)
    mid = r.json()["match_id"]
    _lock("2026-07")
    assert client.delete(f"/api/manual/{mid}").status_code == 423


# ── 11. Buddhist-era ISO dates, including 29 Feb ──────────────────────────────
@pytest.mark.parametrize("raw,want", [
    ("2567-02-29", "2024-02-29"), ("2569-06-05 12:46:38.379", "2026-06-05"),
    ("2569-6-5", "2026-06-05"), ("2026-06-05", "2026-06-05"), ("2567-02-30", None),
])
def test_11_buddhist_era_iso_dates(raw, want):
    assert _parse_thai_date(raw) == want
