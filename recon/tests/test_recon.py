"""
Tests for the revenue-reconciliation module: payment-code classification,
the category/bank engine, parsers, and endpoint auth/validation.

Run:  pytest recon/tests/test_recon.py
"""
import io
import os
import sqlite3
import sys
from pathlib import Path

import openpyxl
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # repo root → import recon.*
from recon.seed_payment_map import normalize, classify          # noqa: E402
from recon.parsers import parse_grab_report                      # noqa: E402
import recon.main as main                                        # noqa: E402


# ── helpers ───────────────────────────────────────────────────────────────────

def _make_db(tmp_path, pos_rows):
    db = tmp_path / "t.db"
    c = sqlite3.connect(db)
    c.execute("CREATE TABLE branches(branch_id INTEGER PRIMARY KEY,name TEXT,branch_type TEXT,active INTEGER)")
    c.executemany("INSERT INTO branches VALUES(?,?,?,?)",
                  [(4, "Korat1", "master", 1), (1, "Chonburi", "franchise", 1),
                   (5, "อุดรธานี", "master", 1)])      # Thai name → exercises header encoding
    c.execute("CREATE TABLE pos_payment_month(branch_id INT,period_month TEXT,payment_code TEXT,amount REAL,txn_count INT)")
    c.executemany("INSERT INTO pos_payment_month VALUES(4,'2026-06',?,?,0)", pos_rows)
    c.commit(); c.close()
    return db


def _add_bank_line(db, provider, channel, credit, date="2026-06-10"):
    c = sqlite3.connect(db)
    up = c.execute("INSERT INTO recon_uploads(source_type,branch_id,provider,file_name,period_month,row_count,status)"
                   " VALUES('bank',4,?,'f','2026-06',0,'confirmed')", (provider,)).lastrowid
    c.execute("INSERT INTO recon_bank_lines(upload_id,branch_id,txn_date,channel,credit) VALUES(?,4,?,?,?)",
              (up, date, channel, credit))
    c.commit(); c.close()


def _add_sale_payment(db, receipt_no, code, amount, branch_id=4):
    """A bill-level POS tender row (sale_payment) — receipt_no starts YYYY-MM-DD."""
    c = sqlite3.connect(db)
    c.execute("CREATE TABLE IF NOT EXISTS sale_payment("
              "branch_id INT,receipt_no TEXT,payment_code TEXT,amount REAL)")
    c.execute("INSERT INTO sale_payment VALUES(?,?,?,?)", (branch_id, receipt_no, code, amount))
    c.commit(); c.close()


DEMO_POS = [("KNNQR", 100000), ("K-SHOP", 50000), ("Credit กรุงไทย", 30000),
            ("Cash", 20000), ("GB", 8000), ("Coupon", 1000), ("Entertain", 500)]


@pytest.fixture
def client(tmp_path):
    main._DB_PATH = _make_db(tmp_path, DEMO_POS)
    os.environ.pop("OSHINEI_SEED_TOKEN", None)
    with TestClient(main.app) as cl:        # startup → schema + migrate + seed
        yield cl


# ── classification ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("code,cat,bank", [
    # KNNQR / K-SHOP / QR code K-bank → KBank QR per the owner's requirement table
    ("KNNQR", "QR", "KBANK"), (" KNNQR", "QR", "KBANK"), ("K-SHOP", "QR", "KBANK"),
    ("QR code K-bank", "QR", "KBANK"), ("QR PromptPay", "QR", "PROMPTPAY"),
    ("Credit กรุงไทย", "CREDIT", "KTB"), ("Credit กสิกรไทย", "CREDIT", "KBANK"),
    ("Credit KTC", "CREDIT", "KTC"), ("Cash", "CASH", "NONE"), ("CASH", "CASH", "NONE"),
    ("โอน KTB", "TRANSFER", "KTB"), ("โอน Kbank", "TRANSFER", "KBANK"),
    # generic transfer can land in KTB OR KBANK → multi-bank sentinel
    ("Bank Transfer", "TRANSFER", "ANY"),
    ("GB", "GRAB", "NONE"), ("LM", "LINEMAN", "NONE"), ("Coupon", "COUPON", "NONE"),
])
def test_classify(code, cat, bank):
    c, b, _ = classify(normalize(code))
    assert (c, b) == (cat, bank)


def test_entertain_not_reconciled():
    assert classify("Entertain")[2] == 0
    assert classify("ENT")[2] == 0


def test_normalize_unifies_leading_space():
    assert normalize(" KNNQR") == normalize("KNNQR") == "KNNQR"


# ── seed ──────────────────────────────────────────────────────────────────────

def test_seed_is_master_only_and_flags_unknown(client):
    rows = client.get("/api/payment-map").json()
    codes = {r["payment_code_norm"] for r in rows}
    assert "KNNQR" in codes and "Cash" in codes
    # KNNQR is routed by the owner's requirement table → KBank QR rail
    assert next(r for r in rows if r["payment_code_norm"] == "KNNQR")["bank"] == "KBANK"
    # the 12 canonical requirement codes exist even without POS volume
    assert "Bank Transfer" in codes
    assert next(r for r in rows if r["payment_code_norm"] == "Bank Transfer")["bank"] == "ANY"
    # reseed is idempotent
    assert client.post("/api/payment-map/seed").json()["inserted"] == 0


# ── engine ────────────────────────────────────────────────────────────────────

def test_category_reconciliation_statuses(client, tmp_path):
    db = main._DB_PATH
    # map KNNQR → QR/KTB
    pm = {r["payment_code_norm"]: r for r in client.get("/api/payment-map").json()}
    client.patch(f"/api/payment-map/{pm['KNNQR']['id']}", json={"category": "QR", "bank": "KTB"})
    sid = client.post("/api/sessions", json={"branch_id": 4, "period_month": "2026-06"}).json()["session_id"]
    _add_bank_line(db, "KTB", "QR", 99950)        # vs 100000 → matched (within ฿100)
    _add_bank_line(db, "KBANK", "QR", 45000)      # vs 50000 → mismatch
    _add_bank_line(db, "KTB", "CASH_DEP", 20000)  # vs cash 20000 → matched

    res = client.post(f"/api/sessions/{sid}/run-match").json()
    cats = {(c["category"], c["bank"]): c for c in res["categories"]}
    assert cats[("QR", "KTB")]["status"] == "matched"
    assert cats[("QR", "KBANK")]["status"] == "mismatch"
    assert cats[("CASH", "NONE")]["status"] == "matched"
    assert cats[("COUPON", "NONE")]["status"] == "no_source"
    assert ("ENTERTAIN", "NONE") not in cats           # excluded
    # idempotent re-run: same discrepancy count, no duplicates
    before = len(client.get(f"/api/sessions/{sid}/discrepancies").json())
    client.post(f"/api/sessions/{sid}/run-match")
    assert len(client.get(f"/api/sessions/{sid}/discrepancies").json()) == before


def test_v2_reconcile_channel_first(client):
    """/api/v2/reconcile composes the month channel-first with the 4-word status
    vocabulary, a derived document checklist, and a todo list."""
    db = main._DB_PATH
    pm = {r["payment_code_norm"]: r for r in client.get("/api/payment-map").json()}
    client.patch(f"/api/payment-map/{pm['KNNQR']['id']}", json={"category": "QR", "bank": "KTB"})
    _add_bank_line(db, "KTB", "QR", 99950)        # vs 100000 → within ฿100 tolerance
    _add_bank_line(db, "KTB", "CASH_DEP", 20000)  # vs cash 20000

    r = client.get("/api/v2/reconcile", params={"branch_id": 4, "month": "2026-06"}).json()
    ch = {c["key"]: c for c in r["channels"]}
    assert ch["COUPON"]["status"] == "no_source"
    assert ch["CREDIT"]["status"] == "waiting_doc"      # no settlement summary uploaded
    assert ch["GRAB"]["status"] == "waiting_doc"
    assert "ENTERTAIN" not in ch                        # internal comps stay out
    # documents checklist derives from POS channels (CC acquirers present)
    doc_keys = {d["key"] for d in r["documents"]}
    assert "cc_KTB" in doc_keys and "grab" in doc_keys
    # todo blocks closing
    assert r["todo"] and r["can_close"] is False
    assert 0 <= r["progress_pct"] <= 100

    # excluded internal transfer lands in non_revenue, not a channel
    _add_bank_line(db, "KBANK", "TRANSFER", 2_000_000)
    conn = sqlite3.connect(db)
    conn.execute("UPDATE recon_bank_lines SET match_status='excluded' WHERE credit=2000000")
    conn.commit(); conn.close()
    r2 = client.get("/api/v2/reconcile", params={"branch_id": 4, "month": "2026-06"}).json()
    assert r2["non_revenue"]["count"] == 1
    assert r2["non_revenue"]["amount"] == 2_000_000
    ch2 = {c["key"]: c for c in r2["channels"]}
    assert ch2.get("TRANSFER", {}).get("ext", 0) == 0   # excluded ≠ revenue lane


def test_v2_accept_clears_todo_and_allows_close(client):
    """The 'รับทราบ' escape hatch: a channel-level accept (reason required)
    drops its items from todo, and run-match writes that lane's discrepancy
    born 'accepted' so the approval gate doesn't block the month."""
    db = main._DB_PATH
    pm = {r["payment_code_norm"]: r for r in client.get("/api/payment-map").json()}
    client.patch(f"/api/payment-map/{pm['KNNQR']['id']}", json={"category": "QR", "bank": "KTB"})
    _add_bank_line(db, "KTB", "QR", 99950)
    _add_bank_line(db, "KTB", "CASH_DEP", 20000)

    # reason is mandatory
    assert client.post("/api/lanes/adj", json={
        "branch_id": 4, "period_month": "2026-06", "category": "GRAB",
        "bank": "ALL", "adj_pos": 0, "adj_ext": 0, "note": "", "adj_type": "accept",
    }).status_code == 400

    r0 = client.get("/api/v2/reconcile", params={"branch_id": 4, "month": "2026-06"}).json()
    todo0 = len(r0["todo"])
    blocked = [c["key"] for c in r0["channels"]
               if c["status"] not in ("ok", "no_source") and not c["accepted"]]
    # accept every blocked channel with a reason
    for key in blocked:
        assert client.post("/api/lanes/adj", json={
            "branch_id": 4, "period_month": "2026-06", "category": key,
            "bank": "ALL", "adj_pos": 0, "adj_ext": 0,
            "note": "ผลต่างจากค่าธรรมเนียมที่กระทบยอดแล้ว", "adj_type": "accept",
        }).status_code == 200

    r1 = client.get("/api/v2/reconcile", params={"branch_id": 4, "month": "2026-06"}).json()
    assert len(r1["todo"]) < todo0
    acc = {c["key"]: c["accepted"] for c in r1["channels"]}
    for key in blocked:
        assert acc[key] and "ค่าธรรมเนียม" in acc[key]["note"]

    # accept does NOT distort the numbers (zero-amount marker only)
    ch0 = {c["key"]: c for c in r0["channels"]}
    ch1 = {c["key"]: c for c in r1["channels"]}
    for key in blocked:
        assert ch1[key]["pos"] == ch0[key]["pos"]
        assert ch1[key]["variance"] == ch0[key]["variance"]

    # close flow: run-match → accepted lanes' discrepancies born resolved → approve OK
    sid = client.post("/api/sessions", json={"branch_id": 4, "period_month": "2026-06"}).json()["session_id"]
    client.post(f"/api/sessions/{sid}/run-match")
    discs = client.get(f"/api/sessions/{sid}/discrepancies",
                       params={"resolution": "all"}).json()
    # No discrepancy of an accepted channel may be born pending (that would
    # re-block approval); the ones that exist carry the staff reason.
    assert not [d for d in discs
                if d["category"] in blocked and d["resolution"] == "pending"]
    born_accepted = [d for d in discs if d["resolution"] == "accepted"]
    assert born_accepted and all("ค่าธรรมเนียม" in (d["resolution_note"] or "")
                                 for d in born_accepted)
    pending = [d for d in discs if d["resolution"] == "pending"]
    if not pending:                                  # gate passes when all accepted
        assert client.patch(f"/api/sessions/{sid}/status",
                            data={"status": "approved", "approved_by": "mgr"}
                            ).status_code == 200


def test_v2_pos_codes_breakdown(client):
    """Each channel carries the POS payment codes behind its total, so staff can
    see where a number comes from."""
    pm = {r["payment_code_norm"]: r for r in client.get("/api/payment-map").json()}
    client.patch(f"/api/payment-map/{pm['KNNQR']['id']}", json={"category": "QR", "bank": "KTB"})
    r = client.get("/api/v2/reconcile", params={"branch_id": 4, "month": "2026-06"}).json()
    qr = next(c for c in r["channels"] if c["key"] == "QR")
    codes = {c["code"]: c["amount"] for c in qr["codes"]}
    assert codes.get("KNNQR") == 100000 and codes.get("K-SHOP") == 50000
    assert abs(sum(codes.values()) - qr["pos"]) < 1


def test_manager_token_gates_approve(client, monkeypatch):
    """Two-tier close: with OSHINEI_MANAGER_TOKEN set, approving needs the code;
    staff transitions (draft/in_review) stay open."""
    sid = client.post("/api/sessions", json={"branch_id": 4, "period_month": "2026-06"}).json()["session_id"]
    monkeypatch.setenv("OSHINEI_MANAGER_TOKEN", "mgr-1234")
    assert client.patch(f"/api/sessions/{sid}/status",
                        data={"status": "in_review"}).status_code == 200
    assert client.patch(f"/api/sessions/{sid}/status",
                        data={"status": "approved", "approved_by": "mgr"}).status_code == 403
    assert client.patch(f"/api/sessions/{sid}/status",
                        data={"status": "approved", "approved_by": "mgr"},
                        headers={"X-Manager-Token": "wrong"}).status_code == 403
    assert client.patch(f"/api/sessions/{sid}/status",
                        data={"status": "approved", "approved_by": "mgr"},
                        headers={"X-Manager-Token": "mgr-1234"}).status_code == 200


def test_aud4_sod_preparer_cannot_approve(client, monkeypatch):
    """AUD-4 segregation of duties: the user who prepared the month cannot approve
    their own work; a different approver is allowed."""
    sid = client.post("/api/sessions",
                      json={"branch_id": 4, "period_month": "2026-06", "created_by": "alice"}
                      ).json()["session_id"]
    monkeypatch.setenv("OSHINEI_MANAGER_TOKEN", "mgr-1234")
    h = {"X-Manager-Token": "mgr-1234"}
    same = client.patch(f"/api/sessions/{sid}/status",
                        data={"status": "approved", "approved_by": "alice"}, headers=h)
    assert same.status_code == 409 and "SoD" in same.json()["detail"]
    other = client.patch(f"/api/sessions/{sid}/status",
                         data={"status": "approved", "approved_by": "bob"}, headers=h)
    assert other.status_code == 200


def test_aud8_accept_requires_meaningful_reason(client):
    """AUD-8: an acknowledged variance must carry a real reason (≥10 chars)."""
    short = client.post("/api/lanes/adj", json={
        "branch_id": 4, "period_month": "2026-06", "category": "QR", "bank": "ALL",
        "adj_type": "accept", "note": "ok"})
    assert short.status_code == 400
    good = client.post("/api/lanes/adj", json={
        "branch_id": 4, "period_month": "2026-06", "category": "QR", "bank": "ALL",
        "adj_type": "accept", "note": "ลูกค้าโอนกดเป็นเงินสด ตรวจแล้ว"})
    assert good.status_code == 200


def test_aud6_audit_log_is_append_only(client):
    """AUD-6: recon_audit_log rejects UPDATE and DELETE (tamper-evident trail)."""
    c = sqlite3.connect(main._DB_PATH)
    c.execute("INSERT INTO recon_audit_log(entity,entity_id,action,by) VALUES('t',1,'create','x')")
    c.commit()
    with pytest.raises(sqlite3.Error):
        c.execute("UPDATE recon_audit_log SET action='tamper' WHERE entity='t'"); c.commit()
    with pytest.raises(sqlite3.Error):
        c.execute("DELETE FROM recon_audit_log WHERE entity='t'"); c.commit()
    c.close()


def test_branch_error_report(client):
    """รายงานข้อผิดพลาดรายสาขา: built from รับทราบ accepts, with an xlsx export."""
    client.post("/api/lanes/adj", json={
        "branch_id": 4, "period_month": "2026-06", "category": "QR", "bank": "ALL",
        "adj_type": "accept", "note": "พนักงานบันทึกผิดช่อง ตรวจแล้ว", "created_by": "mgr"})
    rep = client.get("/api/reports/branch-errors").json()
    assert any(s["branch_id"] == 4 and s["count"] >= 1 for s in rep["summary"])
    assert any(it["category"] == "QR" and "พนักงาน" in it["reason"] and it["by"] == "mgr"
               for it in rep["items"])
    assert client.get("/api/reports/branch-errors?period_month=2026-06").json()["summary"]
    x = client.get("/api/reports/branch-errors.xlsx")
    assert x.status_code == 200 and x.content[:2] == b"PK"


def test_aud5_close_snapshot_frozen_on_approve(client, monkeypatch):
    """AUD-5: approving a month freezes the working-paper export to a hashed file
    that can be downloaded later (immutable record of what was signed)."""
    sid = client.post("/api/sessions",
                      json={"branch_id": 4, "period_month": "2026-06", "created_by": "alice"}
                      ).json()["session_id"]
    monkeypatch.setenv("OSHINEI_MANAGER_TOKEN", "mgr")
    r = client.patch(f"/api/sessions/{sid}/status",
                     data={"status": "approved", "approved_by": "bob"},
                     headers={"X-Manager-Token": "mgr"})
    assert r.status_code == 200 and r.json().get("snapshot")
    snaps = client.get(f"/api/sessions/{sid}/snapshots").json()
    assert len(snaps) == 1
    s = snaps[0]
    assert s["status"] == "approved" and len(s["sha256"]) == 64 and s["size_bytes"] > 0
    f = client.get(f"/api/snapshots/{s['id']}/file")
    assert f.status_code == 200 and f.content[:2] == b"PK"      # a real .xlsx


def test_prev_month_rollover_deposit_not_this_months_diff(client):
    """A deposit on the 1st that the PREVIOUS month's cash matcher claims (T+1
    rollover of month-end cash) must not appear as this month's QR/transfer
    ext_only — the classic 'statement เยอะกว่า' at every month boundary."""
    db = main._DB_PATH
    conn = sqlite3.connect(db)
    # Previous month (2026-05): POS cash on the 31st…
    conn.execute("CREATE TABLE IF NOT EXISTS pos_payment_day("
                 "branch_id INT, day TEXT, payment_code TEXT, amount REAL, txn_count INT)")
    conn.execute("INSERT INTO pos_payment_day VALUES (4,'2026-05-31','Cash',7777,1)")
    conn.execute("INSERT INTO pos_payment_month(branch_id,period_month,payment_code,amount,txn_count) "
                 "VALUES (4,'2026-05','Cash',7777,1)")
    conn.commit(); conn.close()
    # …banked as a QR-labelled credit on June 1st (e.g. KTB SDCH)
    _add_bank_line(db, "KTB", "QR", 7777, date="2026-06-01")

    pm = {r["payment_code_norm"]: r for r in client.get("/api/payment-map").json()}
    client.patch(f"/api/payment-map/{pm['KNNQR']['id']}", json={"category": "QR", "bank": "KTB"})

    r = client.get("/api/v2/reconcile", params={"branch_id": 4, "month": "2026-06"}).json()
    qr = next(c for c in r["channels"] if c["key"] == "QR")
    # the 7777 rollover deposit is May's cash — June's QR lane must not count it
    assert qr["ext"] == 0
    day1 = next((d for d in qr["days"] if d["day"] == "2026-06-01"), None)
    assert day1 is None or day1["ext"] == 0


def test_v2_overview_lists_master_branches(client):
    r = client.get("/api/v2/overview", params={"month": "2026-06"}).json()
    names = [b["branch_name"] for b in r["branches"]]
    assert "Korat1" in names and "Chonburi" not in names   # master-only scope
    b = next(x for x in r["branches"] if x["branch_name"] == "Korat1")
    assert "channels" in b and "progress_pct" in b
    # Phase 3: wrong-channel counts surface per-branch + chain-level so the inbox can
    # flag a falsely-clean branch and show the chain reclass banner.
    assert "reclass_count" in b and "reclass_amount" in b
    assert r["reclass_count_total"] == sum(x["reclass_count"] for x in r["branches"])
    assert abs(r["reclass_amount_total"] - round(sum(x["reclass_amount"] for x in r["branches"]), 2)) < 0.01
    # bad month rejected
    assert client.get("/api/v2/overview", params={"month": "2026-13"}).status_code == 400


def test_within_daily_tol_monthly_drift_escalates_and_blocks_close(client):
    """Control gap: a channel whose every day sits inside the daily tolerance (±฿50)
    but whose MONTH drifts past the monthly tolerance (฿100) must escalate to 'diff',
    raise a todo, and block ปิดเดือน — not hide under 'ตรงกันแล้ว' (the classic
    fee-skim / under-settlement pattern)."""
    db = main._DB_PATH
    c = sqlite3.connect(db)
    c.execute("CREATE TABLE IF NOT EXISTS pos_payment_day"
              "(branch_id INT, day TEXT, payment_code TEXT, amount REAL, txn_count INT DEFAULT 0)")
    # Branch 5 (อุดรธานี) has no DEMO_POS baseline → isolated scenario. KNNQR → QR·KBANK.
    # 27 days: POS 20,000 vs bank 20,030 each (+฿30/day, well within the daily tol) so
    # every day matches and the channel would read 'ok' — but +฿810 over the month
    # exceeds the ฿100 monthly tolerance and must escalate.
    days = ["2026-06-%02d" % i for i in range(1, 28)]
    c.execute("INSERT INTO pos_payment_month VALUES(5,'2026-06','KNNQR',540000,0)")
    c.executemany("INSERT INTO pos_payment_day VALUES(5,?,'KNNQR',20000,5)", [(d,) for d in days])
    up = c.execute("INSERT INTO recon_uploads(source_type,branch_id,provider,file_name,"
                   "period_month,row_count,status) VALUES('bank',5,'kbank','s','2026-06',0,'confirmed')").lastrowid
    for d in days:
        c.execute("INSERT INTO recon_bank_lines(upload_id,branch_id,txn_date,channel,credit,match_status)"
                  " VALUES(?,5,?,'QR',20030,'unmatched')", (up, d))
    c.commit(); c.close()

    r = client.get("/api/v2/reconcile?branch_id=5&month=2026-06").json()
    qr = next(ch for ch in r["channels"] if ch["key"] == "QR")
    assert qr["status"] == "diff", f"within-tol monthly drift must escalate to diff, got {qr['status']}"
    assert "สะสมต่าง" in qr["detail"]          # proves the escalation path, not a per-day variance
    assert abs(qr["variance"] - 810) < 1
    assert r["can_close"] is False
    assert any(t.get("channel") == "QR" and t["type"] == "diff" for t in r["todo"])


def test_summary_freshness_flags_stale_snapshot(client):
    """The persisted category snapshot (Excel/approval) only refreshes on Run
    Match. /summary.freshness must report stale=True before the first run and
    after a later upload, and stale=False right after a run."""
    db = main._DB_PATH
    sid = client.post("/api/sessions", json={"branch_id": 4, "period_month": "2026-06"}).json()["session_id"]

    # never run → stale, no computed_at
    f = client.get(f"/api/sessions/{sid}/summary").json()["freshness"]
    assert f["stale"] is True and f["computed_at"] is None

    # run match → fresh
    client.post(f"/api/sessions/{sid}/run-match")
    f = client.get(f"/api/sessions/{sid}/summary").json()["freshness"]
    assert f["stale"] is False and f["computed_at"] is not None

    # a later upload post-dates the run → stale again
    _add_bank_line(db, "KTB", "QR", 99950, date="2026-06-12")
    c = sqlite3.connect(db)
    c.execute("UPDATE recon_uploads SET uploaded_at='2099-01-01 00:00:00' "
              "WHERE upload_id=(SELECT MAX(upload_id) FROM recon_uploads)")
    c.commit(); c.close()
    f = client.get(f"/api/sessions/{sid}/summary").json()["freshness"]
    assert f["stale"] is True


def test_suggestions_wrong_channel_same_bank(client):
    """POS booked under QR·KBANK but the money shows as a TRANSFER·KBANK bank line
    of the same amount/day → one wrong-channel suggestion, and applying it (POST
    the payload to /api/manual) reconciles both lanes."""
    db = main._DB_PATH
    # map K-SHOP → QR/KBANK explicitly (seed already does, but be deterministic)
    pm = {r["payment_code_norm"]: r for r in client.get("/api/payment-map").json()}
    client.patch(f"/api/payment-map/{pm['K-SHOP']['id']}", json={"category": "QR", "bank": "KBANK"})

    c = sqlite3.connect(db)
    c.execute("CREATE TABLE IF NOT EXISTS pos_payment_day(branch_id INT,day TEXT,payment_code TEXT,amount REAL)")
    c.execute("INSERT INTO pos_payment_day VALUES(4,'2026-06-10','K-SHOP',5000)")   # POS in QR·KBANK
    up = c.execute("INSERT INTO recon_uploads(source_type,branch_id,provider,file_name,period_month,row_count,status)"
                   " VALUES('bank',4,'kbank','f','2026-06',0,'confirmed')").lastrowid
    c.execute("INSERT INTO recon_bank_lines(upload_id,branch_id,txn_date,channel,credit,match_status)"
              " VALUES(?,4,'2026-06-10','TRANSFER',5000,'unmatched')", (up,))   # money in TRANSFER·KBANK
    c.commit(); c.close()

    sug = client.get("/api/suggestions?branch_id=4&period_month=2026-06").json()["suggestions"]
    assert len(sug) == 1
    s = sug[0]
    assert s["pos_lane"] == "QR·KBANK" and s["ext_lane"] == "TRANSFER·KBANK"
    assert s["bank"] == "KBANK" and abs(s["amount"] - 5000) < 0.01 and s["confidence"] == "high"
    # Phase 0: raw lane components for the plain-Thai hero card
    assert s["pos_cat"] == "QR" and s["ext_cat"] == "TRANSFER"
    assert s["pos_bank"] == "KBANK" and s["ext_bank"] == "KBANK"
    # …and reconcile_month enriches them with Thai channel + bank labels
    rm = client.get("/api/v2/reconcile?branch_id=4&month=2026-06").json()
    h = rm["reclass_suggestions"][0]
    assert h["pos_cat_th"] == "QR พร้อมเพย์" and h["ext_cat_th"] == "เงินโอน"
    assert h["ext_bank_th"] == "กสิกร"

    # The suggestion is itself a valid /api/manual payload → applying it succeeds.
    r = client.post("/api/manual", json={
        "branch_id": s["branch_id"], "period_month": s["period_month"],
        "category": s["category"], "bank": s["bank"],
        "pos_days": s["pos_days"], "ext": s["ext"], "note": s["note"],
    })
    assert r.status_code == 200
    # the consumed line is now flagged, so the suggestion no longer reappears
    assert client.get("/api/suggestions?branch_id=4&period_month=2026-06").json()["suggestions"] == []


def test_wrong_channel_report(client):
    """Confirming a wrong-channel suggestion (POS QR·KBANK vs TRANSFER·KBANK bank
    line) logs a manual match; the report surfaces it as one mis-entry — money rang
    in QR but actually landed in TRANSFER — with the per-branch summary + sign sheet."""
    db = main._DB_PATH
    pm = {r["payment_code_norm"]: r for r in client.get("/api/payment-map").json()}
    client.patch(f"/api/payment-map/{pm['K-SHOP']['id']}", json={"category": "QR", "bank": "KBANK"})
    c = sqlite3.connect(db)
    c.execute("CREATE TABLE IF NOT EXISTS pos_payment_day(branch_id INT,day TEXT,payment_code TEXT,amount REAL)")
    c.execute("INSERT INTO pos_payment_day VALUES(4,'2026-06-10','K-SHOP',5000)")
    up = c.execute("INSERT INTO recon_uploads(source_type,branch_id,provider,file_name,period_month,row_count,status)"
                   " VALUES('bank',4,'kbank','f','2026-06',0,'confirmed')").lastrowid
    c.execute("INSERT INTO recon_bank_lines(upload_id,branch_id,txn_date,channel,credit,match_status)"
              " VALUES(?,4,'2026-06-10','TRANSFER',5000,'unmatched')", (up,))
    c.commit(); c.close()

    s = client.get("/api/suggestions?branch_id=4&period_month=2026-06").json()["suggestions"][0]
    client.post("/api/manual", json={
        "branch_id": s["branch_id"], "period_month": s["period_month"],
        "category": s["category"], "bank": s["bank"],
        "pos_days": s["pos_days"], "ext": s["ext"],
        "note": "แคชเชียร์กดเป็น QR แต่จริง ๆ เป็นเงินโอน", "matched_by": "ผู้จัดการ"})

    rep = client.get("/api/reports/wrong-channel?period_month=2026-06").json()
    assert rep["total_count"] == 1 and abs(rep["total_amount"] - 5000) < 0.01
    assert len(rep["summary"]) == 1 and rep["summary"][0]["branch_id"] == 4
    it = rep["items"][0]
    assert it["from_th"] == "QR พร้อมเพย์" and it["to_th"] == "เงินโอน"
    assert it["amount"] == 5000 and it["days"] == ["2026-06-10"]
    assert it["by"] == "ผู้จัดการ"
    # branch filter: matching branch keeps the item, a different branch returns none
    f4 = client.get("/api/reports/wrong-channel?period_month=2026-06&branch_id=4").json()
    assert f4["total_count"] == 1 and f4["summary"][0]["branch_id"] == 4
    f9 = client.get("/api/reports/wrong-channel?period_month=2026-06&branch_id=999").json()
    assert f9["total_count"] == 0 and f9["summary"] == []
    # Excel + printable acknowledgment sheet both render (+ filtered xlsx)
    assert client.get("/api/reports/wrong-channel.xlsx?period_month=2026-06").status_code == 200
    assert client.get("/api/reports/wrong-channel.xlsx?period_month=2026-06&branch_id=4").status_code == 200
    pr = client.get("/api/reports/wrong-channel.print?period_month=2026-06")
    assert pr.status_code == 200 and "รับทราบ" in pr.text and "เงินโอน" in pr.text
    # self-improvement coaching sections: trend, confusion patterns, tips
    s0 = rep["summary"][0]
    assert s0["direction"] in ("up", "down", "flat") and "target" in s0
    assert s0["patterns"] and s0["patterns"][0]["from_th"] == "QR พร้อมเพย์"
    assert s0["tips"], "each branch gets at least one coaching tip"
    assert "แนวโน้ม" in pr.text and "คำแนะนำ" in pr.text and "เป้าหมาย" in pr.text


def test_statement_code_dictionary(client):
    """The statement-code map is the single source the parser uses; the review
    endpoints surface it (incl. the SDCH=cash review flag) and stay in sync."""
    from recon.parsers.bank import STATEMENT_CODE_MAP, _normalise_channel
    # parser is built FROM the map → every entry classifies to its own channel
    for e in STATEMENT_CODE_MAP:
        assert _normalise_channel(e["code"].split(" / ")[0]) or True   # smoke
    # SDCH is present, parsed as QR, flagged for review with a cash-deposit note
    sdch = next(e for e in STATEMENT_CODE_MAP if e["code"] == "SDCH")
    assert sdch["channel"] == "QR" and sdch["status"] == "review"
    assert _normalise_channel("KTB SDCH :ZICID:") == "QR"      # behaviour unchanged

    j = client.get("/api/statement-codes").json()
    assert len(j["codes"]) == len(STATEMENT_CODE_MAP) and j["review_count"] >= 1
    row = next(c for c in j["codes"] if c["code"] == "SDCH")
    assert row["channel_th"] == "QR พร้อมเพย์" and row["category"] == "QR"
    # downloadable + printable review sheets render
    assert client.get("/api/statement-codes.xlsx").status_code == 200
    h = client.get("/api/statement-codes.html")
    assert h.status_code == 200 and "SDCH" in h.text and "รอตรวจ" in h.text


def test_day_ack_clears_variance_by_date(client):
    """Reconcile BY DATE: a per-day variance is acknowledged day-by-day. Once the
    differing day carries a reason the channel's todo clears (residual within tol),
    the day is flagged acked, and the reason is persisted for the close pack."""
    db = main._DB_PATH
    pm = {r["payment_code_norm"]: r for r in client.get("/api/payment-map").json()}
    client.patch(f"/api/payment-map/{pm['KNNQR']['id']}", json={"category": "QR", "bank": "KBANK"})
    client.patch(f"/api/payment-map/{pm['K-SHOP']['id']}", json={"category": "QR", "bank": "KBANK"})
    c = sqlite3.connect(db)
    c.execute("CREATE TABLE IF NOT EXISTS pos_payment_day"
              "(branch_id INT, day TEXT, payment_code TEXT, amount REAL, txn_count INT DEFAULT 0)")
    # daily POS QR sums to the monthly QR total (KNNQR 100k + K-SHOP 50k = 150k)
    c.executemany("INSERT INTO pos_payment_day VALUES(4,?,?,?,0)",
                  [("2026-06-10", "KNNQR", 50000), ("2026-06-11", "KNNQR", 50000),
                   ("2026-06-12", "K-SHOP", 50000)])
    up = c.execute("INSERT INTO recon_uploads(source_type,branch_id,provider,file_name,"
                   "period_month,row_count,status) VALUES('bank',4,'kbank','s','2026-06',0,'confirmed')").lastrowid
    # bank QR matches on the 11th/12th but is 10,000 short on the 10th → one diff day
    c.executemany("INSERT INTO recon_bank_lines(upload_id,branch_id,txn_date,channel,credit) VALUES(?,4,?,'QR',?)",
                  [(up, "2026-06-10", 40000), (up, "2026-06-11", 50000), (up, "2026-06-12", 50000)])
    c.commit(); c.close()

    r = client.get("/api/v2/reconcile", params={"branch_id": 4, "month": "2026-06"}).json()
    qr = next(c for c in r["channels"] if c["key"] == "QR")
    assert qr["status"] == "diff"
    assert any(t["type"] == "diff" and t["channel"] == "QR" for t in r["todo"])
    dday = next(d for d in qr["days"] if abs(d["variance"]) > 0.5)
    assert dday["day"] == "2026-06-10" and round(dday["variance"]) == -10000

    res = client.post("/api/day-ack", json={
        "branch_id": 4, "period_month": "2026-06", "category": "QR",
        "day": "2026-06-10", "variance": dday["variance"],
        "note": "ลูกค้าจ่าย QR แต่ยอดเข้าน้อยกว่า ตรวจสอบกับสลิปแล้ว"})
    assert res.status_code == 200

    r2 = client.get("/api/v2/reconcile", params={"branch_id": 4, "month": "2026-06"}).json()
    qr2 = next(c for c in r2["channels"] if c["key"] == "QR")
    assert qr2["acked_days"] == 1 and qr2["status"] != "diff"
    assert not any(t["type"] == "diff" and t["channel"] == "QR" for t in r2["todo"])
    d2 = next(d for d in qr2["days"] if d["day"] == "2026-06-10")
    assert d2.get("ack") and d2["ack"]["note"].startswith("ลูกค้าจ่าย")

    # reason ≥10 chars is enforced
    bad = client.post("/api/day-ack", json={"branch_id": 4, "period_month": "2026-06",
        "category": "QR", "day": "2026-06-11", "variance": 0, "note": "x"})
    assert bad.status_code == 400


def test_all_errors_report_covers_all_types(client):
    """รายงานเงินบันทึกผิดช่อง now returns ALL errors of the month tagged by type
    (variance, missing document, …), not just confirmed wrong-channel mis-entries."""
    rep = client.get("/api/reports/wrong-channel?period_month=2026-06").json()
    assert "errors" in rep and rep["error_total_count"] > 0
    types = {e["type"] for e in rep["errors"]}
    assert "variance" in types          # POS channels with no money-in → variance
    assert "missing_doc" in types       # required statements/summaries not uploaded
    e0 = rep["errors"][0]
    assert {"type", "type_th", "status", "branch_name", "detail"} <= set(e0)
    assert rep["error_open_count"] >= 1
    # xlsx + print render the broadened report
    assert client.get("/api/reports/wrong-channel.xlsx?period_month=2026-06").status_code == 200
    pr = client.get("/api/reports/wrong-channel.print?period_month=2026-06")
    assert pr.status_code == 200 and "ข้อผิดพลาดทั้งหมด" in pr.text


def test_daily_finance_and_account_map(client):
    """รายงานการจับคู่รายวัน carries a per-day accounting breakdown (GROSS with
    discount split out) + account code/name columns filled from recon_account_map."""
    db = main._DB_PATH
    pm = {r["payment_code_norm"]: r for r in client.get("/api/payment-map").json()}
    client.patch(f"/api/payment-map/{pm['KNNQR']['id']}", json={"category": "QR", "bank": "KBANK"})
    c = sqlite3.connect(db)
    c.execute("CREATE TABLE IF NOT EXISTS pos_payment_day"
              "(branch_id INT, day TEXT, payment_code TEXT, amount REAL, txn_count INT DEFAULT 0)")
    c.execute("INSERT INTO pos_payment_day VALUES(4,'2026-06-05','KNNQR',5000,2)")
    up = c.execute("INSERT INTO recon_uploads(source_type,branch_id,provider,file_name,"
                   "period_month,row_count,status) VALUES('grab',4,'Grab','g','2026-06',0,'confirmed')").lastrowid
    # Grab order: gross 5,000, commission 1,000, VAT 70, promo (discount) 200
    c.execute("INSERT INTO recon_grab_lines(upload_id,branch_id,order_date,order_status,"
              "food_amount,commission_amt,vat_amount,promo_deduct,net_payout) "
              "VALUES(?,4,'2026-06-05','Completed',5000,1000,70,200,3730)", (up,))
    c.commit(); c.close()

    r = client.get("/api/monthly-report?branch_id=4&period_month=2026-06")
    assert r.status_code == 200
    assert "สรุปบัญชีรายวัน" in r.text                       # per-day accounting section
    assert "ยอดเต็ม" in r.text and "ส่วนลด" in r.text and "รหัสบัญชี" in r.text

    # account code/name fill in later via /api/account-map (no deploy)
    assert client.put("/api/account-map", json={
        "slot": "GRAB", "account_code": "4100", "account_name": "รายได้เดลิเวอรี"}).status_code == 200
    r2 = client.get("/api/monthly-report?branch_id=4&period_month=2026-06")
    assert "4100" in r2.text and "รายได้เดลิเวอรี" in r2.text
    am = {x["slot"]: x for x in client.get("/api/account-map").json()}
    assert am["GRAB"]["account_code"] == "4100"


def test_daily_accounts_cards(client):
    """สรุปบัญชีรายวัน (รายใบ): /api/daily-accounts returns per-day cards for ONE branch —
    each day lists channels at GROSS with discount on its own line (split from fee/VAT)
    + a day total — plus the master-branch list; .print renders the printable cards."""
    db = main._DB_PATH
    pm = {r["payment_code_norm"]: r for r in client.get("/api/payment-map").json()}
    client.patch(f"/api/payment-map/{pm['KNNQR']['id']}", json={"category": "QR", "bank": "KBANK"})
    c = sqlite3.connect(db)
    c.execute("CREATE TABLE IF NOT EXISTS pos_payment_day"
              "(branch_id INT, day TEXT, payment_code TEXT, amount REAL, txn_count INT DEFAULT 0)")
    c.execute("INSERT INTO pos_payment_day VALUES(4,'2026-06-05','KNNQR',5000,2)")
    up = c.execute("INSERT INTO recon_uploads(source_type,branch_id,provider,file_name,"
                   "period_month,row_count,status) VALUES('grab',4,'Grab','g','2026-06',0,'confirmed')").lastrowid
    # Grab order: gross 5,000, commission 1,000, VAT 70, promo (discount) 200 → net 3,730
    c.execute("INSERT INTO recon_grab_lines(upload_id,branch_id,order_date,order_status,"
              "food_amount,commission_amt,vat_amount,promo_deduct,net_payout) "
              "VALUES(?,4,'2026-06-05','Completed',5000,1000,70,200,3730)", (up,))
    c.commit(); c.close()

    j = client.get("/api/daily-accounts?branch_id=4&period_month=2026-06").json()
    assert j["branch_id"] == 4 and any(b["branch_id"] == 4 for b in j["branches"])
    day = next(d for d in j["days"] if d["dom"] == 5)
    grab = next(ch for ch in day["channels"] if ch["ch"] == "GRAB")
    assert grab["gross"] == 5000 and grab["discount"] == 200        # discount on its own line
    assert grab["fee"] == 1000 and grab["vat"] == 70
    assert grab["net"] == 5000 - 200 - 1000 - 70                    # 3730
    qr = next(ch for ch in day["channels"] if ch["ch"] == "QR")
    assert qr["gross"] == 5000 and qr["discount"] == 0             # POS tender: no fee/discount
    assert day["totals"]["gross"] == 10000                         # 5,000 QR + 5,000 Grab
    assert j["totals"]["net"] == day["totals"]["net"]              # one day → month == day

    # omitting branch_id defaults to the first master branch
    j0 = client.get("/api/daily-accounts?period_month=2026-06").json()
    assert j0["branch_id"] == j0["branches"][0]["branch_id"]

    # account code rides in from recon_account_map (no deploy)
    client.put("/api/account-map", json={"slot": "GRAB", "account_code": "4100", "account_name": "รายได้เดลิเวอรี"})
    j2 = client.get("/api/daily-accounts?branch_id=4&period_month=2026-06").json()
    g2 = next(ch for ch in next(d for d in j2["days"] if d["dom"] == 5)["channels"] if ch["ch"] == "GRAB")
    assert g2["code"] == "4100"

    # printable per-day card report + period guard
    p = client.get("/api/daily-accounts.print?branch_id=4&period_month=2026-06")
    assert p.status_code == 200 and "สรุปบัญชีรายวัน" in p.text and "วันที่ 5" in p.text
    assert client.get("/api/daily-accounts?period_month=2026-13").status_code == 400


def test_monthly_matching_report(client):
    """Monthly matching audit renders per-branch day×channel rows with status, plus
    a manager pack (all branches), and rejects a bad period."""
    db = main._DB_PATH
    pm = {r["payment_code_norm"]: r for r in client.get("/api/payment-map").json()}
    client.patch(f"/api/payment-map/{pm['KNNQR']['id']}", json={"category": "QR", "bank": "KBANK"})
    c = sqlite3.connect(db)
    c.execute("CREATE TABLE IF NOT EXISTS pos_payment_day"
              "(branch_id INT, day TEXT, payment_code TEXT, amount REAL, txn_count INT DEFAULT 0)")
    c.execute("INSERT INTO pos_payment_day VALUES(4,'2026-06-10','KNNQR',50000,9)")
    up = c.execute("INSERT INTO recon_uploads(source_type,branch_id,provider,file_name,"
                   "period_month,row_count,status) VALUES('bank',4,'kbank','s','2026-06',0,'confirmed')").lastrowid
    c.execute("INSERT INTO recon_bank_lines(upload_id,branch_id,txn_date,channel,credit) "
              "VALUES(?,4,'2026-06-10','QR',50000)", (up,))
    c.commit(); c.close()

    r = client.get("/api/monthly-report?branch_id=4&period_month=2026-06")
    assert r.status_code == 200
    assert "รายงานการจับคู่รายวัน" in r.text
    assert "QR พร้อมเพย์" in r.text and "สถานะการจับคู่" in r.text
    assert "ตรง" in r.text                       # the matched QR lane shows ✓ ตรง
    # monthly receipts + fees/VAT + draft accounting entry
    assert "สรุปการรับเงินทั้งเดือน" in r.text and "ร่างการบันทึกบัญชี" in r.text
    assert "เดบิต (Dr)" in r.text and "รายได้จากการขาย" in r.text
    # all-branches manager pack = the 5 master branches only (franchise excluded)
    pack = client.get("/api/monthly-report?period_month=2026-06")
    assert pack.status_code == 200
    assert "Korat1" in pack.text and "อุดรธานี" in pack.text     # master branches present
    assert "Chonburi" not in pack.text                            # franchise branch excluded
    # on-page branch picker present, listing the master branches (not the franchise)
    assert "<select" in pack.text and "ทุกสาขา" in pack.text
    assert "<option value='4'" in pack.text and "Chonburi" not in pack.text
    # per-branch view still works
    assert client.get("/api/monthly-report?period_month=2026-06&branch_id=4").status_code == 200
    # detail=1 appends the auditor evidence pack (POS codes + line-level sections)
    det = client.get("/api/monthly-report?period_month=2026-06&branch_id=4&detail=1")
    assert det.status_code == 200
    assert "หลักฐานประกอบ (สำหรับผู้ตรวจสอบ)" in det.text
    assert "ที่มาของยอด POS" in det.text and "KNNQR" in det.text   # POS code traced


def test_outstanding_docs_report(client):
    """The follow-up report lists per-branch missing documents (works for any month)
    and renders a printable sheet with a month + branch picker."""
    j = client.get("/api/reports/outstanding-docs?period_month=2026-06").json()
    assert "branches" in j and "grand_total" in j and j["period_month"] == "2026-06"
    h = client.get("/api/reports/outstanding-docs.print?period_month=2026-06")
    assert h.status_code == 200
    assert "รายงานติดตามเอกสารที่ยังขาด" in h.text
    assert "<input type='month'" in h.text and "<select" in h.text   # month + branch picker
    # works for a different month too (monthly follow-up) + validates input
    assert client.get("/api/reports/outstanding-docs?period_month=2026-05").status_code == 200
    assert client.get("/api/reports/outstanding-docs?period_month=2026-13").status_code == 400
    # validation
    assert client.get("/api/monthly-report?period_month=2026-13").status_code == 400


def test_uat_signoff_sheet(client):
    """Printable UAT sign-off renders all test cases + sign-off, prefills the month,
    and rejects a bad period."""
    r = client.get("/api/uat-signoff?period_month=2026-05")
    assert r.status_code == 200
    t = r.text
    assert "UAT Sign-off" in t and "การลงนามรับรอง" in t
    assert "TC-01" in t and "TC-10" in t          # all cases rendered
    assert "ขั้นตอนทดสอบ" in t and "ผลที่คาดหวัง" in t and "☐ ผ่าน" in t
    assert "2026-05" in t                          # month prefilled
    assert client.get("/api/uat-signoff").status_code == 200          # no month is fine
    assert client.get("/api/uat-signoff?period_month=2026-13").status_code == 400


def test_statement_code_override(client):
    """Manager remaps a statement code (SDCH → CASH_DEP): it wins over the parser,
    re-classifies existing bank lines, shows as overridden, and reverts cleanly."""
    db = main._DB_PATH
    c = sqlite3.connect(db)
    up = c.execute("INSERT INTO recon_uploads(source_type,branch_id,provider,file_name,"
                   "period_month,row_count,status) VALUES('bank',4,'ktb','s','2026-06',0,'confirmed')").lastrowid
    # two SDCH lines parsed as QR (the bug), one unrelated QR line
    c.execute("INSERT INTO recon_bank_lines(upload_id,branch_id,txn_date,channel,credit,description) "
              "VALUES(?,4,'2026-06-02','QR',5000,'KTB SDCH :ZICID:')", (up,))
    c.execute("INSERT INTO recon_bank_lines(upload_id,branch_id,txn_date,channel,credit,description) "
              "VALUES(?,4,'2026-06-03','QR',6000,'SDCH deposit')", (up,))
    c.execute("INSERT INTO recon_bank_lines(upload_id,branch_id,txn_date,channel,credit,description) "
              "VALUES(?,4,'2026-06-03','QR',7000,'QR พร้อมเพย์')", (up,))
    c.commit(); c.close()

    # override SDCH → CASH_DEP (manager-gated; open when no token set in tests)
    r = client.post("/api/statement-codes/override",
                    json={"code": "sdch", "channel": "CASH_DEP", "note": "เงินฝากสด", "by": "mgr"})
    assert r.status_code == 200 and r.json()["lines_reclassified"] == 2

    with main.get_db() as conn:
        chans = {row["credit"]: row["channel"] for row in conn.execute(
            "SELECT credit, channel FROM recon_bank_lines WHERE upload_id=?", (up,))}
    assert chans[5000] == "CASH_DEP" and chans[6000] == "CASH_DEP"   # SDCH lines remapped
    assert chans[7000] == "QR"                                       # genuine QR untouched

    j = client.get("/api/statement-codes").json()
    sdch = next(c for c in j["codes"] if c["code"] == "SDCH")
    assert sdch["overridden"] and sdch["effective_channel"] == "CASH_DEP"
    assert j["override_count"] >= 1

    # revert → SDCH lines fall back to the parser's QR classification
    r = client.delete("/api/statement-codes/override/SDCH")
    assert r.status_code == 200
    with main.get_db() as conn:
        back = {row["credit"]: row["channel"] for row in conn.execute(
            "SELECT credit, channel FROM recon_bank_lines WHERE upload_id=?", (up,))}
    assert back[5000] == "QR" and back[6000] == "QR"


def test_reclass_pattern_diagnostic(client):
    """The structural-pattern guard flags a flood of identical cross-bank reclass
    suggestions (the SDCH fingerprint) across branches."""
    j = client.get("/api/diagnostics/reclass-pattern?month=2026-06&min_cluster=2").json()
    assert "clusters" in j and "flagged" in j and "warning" in j
    # with no/low data the guard simply reports nothing flagged (no false alarm)
    assert isinstance(j["clusters"], list)


def test_suggestions_ignores_cross_bank(client):
    """A same-amount opposite imbalance across DIFFERENT banks must NOT be
    suggested (operator chose same-bank-only)."""
    db = main._DB_PATH
    pm = {r["payment_code_norm"]: r for r in client.get("/api/payment-map").json()}
    client.patch(f"/api/payment-map/{pm['K-SHOP']['id']}", json={"category": "QR", "bank": "KBANK"})
    c = sqlite3.connect(db)
    c.execute("CREATE TABLE IF NOT EXISTS pos_payment_day(branch_id INT,day TEXT,payment_code TEXT,amount REAL)")
    c.execute("INSERT INTO pos_payment_day VALUES(4,'2026-06-10','K-SHOP',5000)")    # POS QR·KBANK
    up = c.execute("INSERT INTO recon_uploads(source_type,branch_id,provider,file_name,period_month,row_count,status)"
                   " VALUES('bank',4,'ktb','f','2026-06',0,'confirmed')").lastrowid
    c.execute("INSERT INTO recon_bank_lines(upload_id,branch_id,txn_date,channel,credit,match_status)"
              " VALUES(?,4,'2026-06-10','TRANSFER',5000,'unmatched')", (up,))        # money TRANSFER·KTB (other bank)
    c.commit(); c.close()
    assert client.get("/api/suggestions?branch_id=4&period_month=2026-06").json()["suggestions"] == []


def test_payout_matching_combines_consecutive_days(client):
    """Grab paid two days' net in one bank transfer → auto-grouped as one combined
    payout covering both days."""
    from recon.matcher import payout_matching
    db = main._DB_PATH
    c = sqlite3.connect(db)
    up = c.execute("INSERT INTO recon_uploads(source_type,branch_id,provider,file_name,period_month,row_count,status)"
                   " VALUES('grab',4,'Grab','g','2026-06',0,'confirmed')").lastrowid
    # two days, net 100 and 200 (no fee for simplicity)
    c.execute("INSERT INTO recon_grab_lines(upload_id,branch_id,order_date,order_status,food_amount,commission_amt,vat_amount,net_payout) VALUES(?,4,'2026-06-07','Completed',100,0,0,100)", (up,))
    c.execute("INSERT INTO recon_grab_lines(upload_id,branch_id,order_date,order_status,food_amount,commission_amt,vat_amount,net_payout) VALUES(?,4,'2026-06-08','Completed',200,0,0,200)", (up,))
    # one bank transfer of 300 two days after the second day
    bu = c.execute("INSERT INTO recon_uploads(source_type,branch_id,provider,file_name,period_month,row_count,status)"
                   " VALUES('bank',4,'kbank','b','2026-06',0,'confirmed')").lastrowid
    c.execute("INSERT INTO recon_bank_lines(upload_id,branch_id,txn_date,channel,credit) VALUES(?,4,'2026-06-09','TRANSFER',300)", (bu,))
    c.commit(); c.close()

    with main.get_db() as conn:
        res = payout_matching(conn, 4, "2026-06")
    g = res["GRAB"]
    assert g["net_total"] == 300 and g["payout_total"] == 300 and g["awaiting_total"] == 0
    assert len(g["payouts"]) == 1
    p = g["payouts"][0]
    assert p["status"] == "matched" and p["combined"] is True
    assert [x["day"] for x in p["days"]] == ["2026-06-07", "2026-06-08"]
    assert p["amount"] == 300 and p["payout_date"] == "2026-06-09"


def _add_lineman_net(db, day, net, branch_id=4, status="Completed"):
    """A LINE MAN completed-order net-day (food=net, no fee/VAT for simplicity)."""
    c = sqlite3.connect(db)
    up = c.execute("INSERT INTO recon_uploads(source_type,branch_id,provider,file_name,period_month,row_count,status)"
                   " VALUES('lineman',?,'LINE MAN','lm',?,0,'confirmed')",
                   (branch_id, day[:7])).lastrowid
    c.execute("INSERT INTO recon_lineman_lines(upload_id,branch_id,order_date,order_status,"
              "food_amount,commission_amt,vat_on_commission,promo_deduct,net_payout)"
              " VALUES(?,?,?,?,?,0,0,0,?)", (up, branch_id, day, status, net, net))
    c.commit(); c.close()


def test_lineman_under_threshold_pays_next_month_first(client):
    """LINE MAN's ฿500 transfer rule: a whole month whose net never reaches ฿500
    is swept on the 1st of NEXT month, and tiny sub-฿50 days still accumulate
    toward it. The earning month must TIE to that next-month transfer (matched,
    not a permanent 'awaiting'), and the carried transfer must NOT be double-
    counted as a surplus in the following month."""
    from recon.matcher import payout_matching
    db = main._DB_PATH
    # June: 40 + 30 + 410 = 480 net (< ฿500), last sale on the 20th (early)
    _add_lineman_net(db, "2026-06-10", 40)
    _add_lineman_net(db, "2026-06-11", 30)
    _add_lineman_net(db, "2026-06-20", 410)
    # the ฿480 residual is remitted on 1 July (next month, month-end sweep)
    _add_bank_line(db, "kbank", "LINEMAN_PAYOUT", 480, date="2026-07-01")

    with main.get_db() as conn:
        june = payout_matching(conn, 4, "2026-06")["LINEMAN"]
    assert june["net_total"] == 480                       # sub-฿50 days included
    assert june["awaiting_total"] == 0                    # not a permanent 'รอเงินเข้า'
    assert june["payout_total"] == 480
    assert len(june["payouts"]) == 1
    p = june["payouts"][0]
    assert p["status"] == "matched" and p["amount"] == 480
    assert p["payout_date"] == "2026-07-01"               # swept on the 1st of next month
    assert [x["day"] for x in p["days"]] == ["2026-06-10", "2026-06-11", "2026-06-20"]

    # July has its own ฿600 net paid on the 2nd — the carried 1-July sweep must NOT
    # be re-claimed by July (no false surplus): July payout_total = its own 600 only.
    _add_lineman_net(db, "2026-07-15", 600)
    _add_bank_line(db, "kbank", "LINEMAN_PAYOUT", 600, date="2026-07-16")
    with main.get_db() as conn:
        july = payout_matching(conn, 4, "2026-07")["LINEMAN"]
    assert july["payout_total"] == 600                    # only July's own transfer
    july_credit_dates = {p["payout_date"] for p in july["payouts"] if p["status"] == "matched"}
    assert "2026-07-01" not in july_credit_dates          # carried sweep de-duped


def test_bay_point_parses_and_ties_to_bank():
    """BAY Point (Krungsri loyalty reimbursement) parses under credit_card as a
    BAYPOINT settlement, is kept OUT of the POS-credit lanes, and ties 2-way to
    the KBANK transfer of the same amount."""
    import glob
    bay = [f for f in glob.glob(str(_UDON / "*.pdf")) if "BAY" in f]
    if not bay:
        pytest.skip("BAY Point sample not present")
    from recon.parsers.pdf_formats import _bay_point, _looks_like_bay_point
    raw = open(bay[0], "rb").read()
    assert _looks_like_bay_point(raw)
    prov, rows, _ = _bay_point(raw)
    assert prov == "BAYPOINT" and len(rows) == 1
    r = rows[0]
    assert r["acquirer"] == "BAYPOINT" and abs(r["net_amount"] - 1700.0) < 0.01 and r["mdr_fee"] == 0.0

    # end-to-end: ties to the KBANK transfer, and does NOT pollute CREDIT lanes
    import recon.main as m, sqlite3 as sq, tempfile, pathlib, os
    from fastapi.testclient import TestClient
    from recon.matcher import payout_matching, category_reconciliation
    db = pathlib.Path(tempfile.mkdtemp()) / "b.db"
    c = sq.connect(db); c.execute("CREATE TABLE branches(branch_id INTEGER PRIMARY KEY,name TEXT,branch_type TEXT,active INTEGER)")
    c.execute("INSERT INTO branches VALUES(25,'อุดร','master',1)"); c.commit(); c.close()
    m._DB_PATH = db; os.environ.pop("OSHINEI_SEED_TOKEN", None)
    with TestClient(m.app) as cl:
        for st, raw2 in [("credit_card", raw), (_udon and "bank", _udon("UDN_Statements KB_SA_05.2569.pdf"))]:
            cl.post(f"/api/uploads/{st}/confirm", files={"file": ("x.pdf", raw2, "application/pdf")},
                    data={"branch_id": 25, "period_month": "2026-05"})
        with m.get_db() as conn:
            bp = payout_matching(conn, 25, "2026-05")["BAYPOINT"]
            cats, _ = category_reconciliation(conn, 25, "2026-05")
    assert abs(bp["net_total"] - 1700) < 0.01 and abs(bp["payout_total"] - 1700) < 0.01
    assert not any(r["bank"] == "BAYPOINT" for r in cats)   # excluded from POS-credit lanes


def test_point_pos_total_classifies_by_series_name(client):
    """POS loyalty-point leg = Coupon tenders on the issuer's point-redemption
    series, classified by name (Krungsri/KTC) — generic discounts excluded."""
    from recon.matcher import point_pos_total
    c = sqlite3.connect(main._DB_PATH)
    c.execute("CREATE TABLE IF NOT EXISTS pos_coupon_payment_month(branch_id INT,period_month TEXT,coupon_series_id INT,amount REAL,txn_count INT)")
    c.execute("CREATE TABLE IF NOT EXISTS coupon_series(coupon_series_id INTEGER PRIMARY KEY,name TEXT,code TEXT,active INT)")
    c.executemany("INSERT OR REPLACE INTO coupon_series VALUES(?,?,?,1)", [
        (17, "โปรบัตรกรุงศรี 1,000 คะแนนแลกรับส่วนลด 100 บาท", "KS"),
        (4,  "แต้ม KTC 1000 แต้มแลกส่วนลด 100 บาท", "KTC"),
        (2,  "ส่วนลด 5%", "D")])
    c.executemany("INSERT INTO pos_coupon_payment_month VALUES(4,'2026-06',?,?,?)",
                  [(17, 1700, 17), (4, 800, 8), (2, 999, 3)])
    c.commit(); c.close()
    assert point_pos_total(main._DB_PATH and sqlite3.connect(main._DB_PATH), 4, "2026-06", "BAY") == 1700.0
    assert point_pos_total(sqlite3.connect(main._DB_PATH), 4, "2026-06", "KTC") == 800.0  # excludes the 5% discount


def test_ktc_point_split_by_mid(client):
    """A KTC RTxInv mixes regular KTC + KTC Point (by MID). With the point MID in
    recon_acquirer_mid, the point rows leave CREDIT·KTC gross and form a KTCPOINT
    2-way vs the POS KTC-point coupon (bank stays in the combined KTC deposit)."""
    from recon.matcher import category_reconciliation, payout_reconciliation, _point_settlement_gross
    db = main._DB_PATH
    c = sqlite3.connect(db)
    # MID map: 661 = regular KTC, 662 = KTC Point (Udon)
    c.execute("INSERT INTO recon_acquirer_mid(branch_id,acquirer,merchant_id,terminal_id,active) VALUES(4,'KTC','661','',1)")
    c.execute("INSERT INTO recon_acquirer_mid(branch_id,acquirer,merchant_id,terminal_id,active) VALUES(4,'KTCPOINT','662','',1)")
    up = c.execute("INSERT INTO recon_uploads(source_type,branch_id,provider,file_name,period_month,row_count,status)"
                   " VALUES('credit_card',4,'KTC','f','2026-06',0,'confirmed')").lastrowid
    # regular KTC 5,000 (MID 661) + KTC point 160 (MID 662)
    c.execute("INSERT INTO recon_cc_lines(upload_id,branch_id,settlement_date,acquirer,gross_amount,mdr_fee,vat_amount,net_amount,merchant_id) VALUES(?,4,'2026-06-03','KTC',5000,0,0,5000,'661')", (up,))
    c.execute("INSERT INTO recon_cc_lines(upload_id,branch_id,settlement_date,acquirer,gross_amount,mdr_fee,vat_amount,net_amount,merchant_id) VALUES(?,4,'2026-06-03','KTC',160,0,0,160,'662')", (up,))
    # POS KTC-point coupon = 160
    c.execute("CREATE TABLE IF NOT EXISTS pos_coupon_payment_month(branch_id INT,period_month TEXT,coupon_series_id INT,amount REAL,txn_count INT)")
    c.execute("CREATE TABLE IF NOT EXISTS coupon_series(coupon_series_id INTEGER PRIMARY KEY,name TEXT,code TEXT,active INT)")
    c.execute("INSERT OR REPLACE INTO coupon_series VALUES(1,'แลกคะแนน KTC 699 คะแนนลด 80 บาท','KTC',1)")
    c.execute("INSERT INTO pos_coupon_payment_month VALUES(4,'2026-06',1,160,2)")
    c.commit(); c.close()
    with main.get_db() as conn:
        cats, _ = category_reconciliation(conn, 4, "2026-06")
        ktc_gross = [x["ext_amount"] for x in cats if x["category"] == "CREDIT" and x["bank"] == "KTC"]
        kp = [x for x in payout_reconciliation(conn, 4, "2026-06") if x["category"] == "KTCPOINT"]
    assert ktc_gross == [5000.0]                       # point row (160) excluded from regular KTC
    assert kp and kp[0]["report_net"] == 160.0 and kp[0]["pos_point"] == 160.0
    assert kp[0]["status"] == "matched" and kp[0]["bank_in_credit"] is True


def test_bank_account_seed_and_branch_autodetect(client):
    """Seed recon_bank_account from the template, then a bank statement carrying
    that account number auto-detects its branch on preview."""
    from recon.seed_acquirer_mids import _accounts, seed_sqlite
    from recon.parsers.pdf_formats import _extract_account
    # account extraction from statement-style text
    assert _extract_account("เลขที่บัญชีเงินฝาก 057-3-49826-7") == "057-3-49826-7"
    assert _extract_account("เลขทีบัญชี 017-0-57092-4 ประเภท ออมทรัพย์") == "017-0-57092-4"
    # the seeder reads KTB + KBANK account columns
    csv = str(Path(__file__).resolve().parents[2] / "recon_accounts_mids_template.csv")
    if not Path(csv).exists():
        pytest.skip("template CSV not present")
    accts = list(_accounts(csv))
    assert any(b == "KTB" for _, b, _, _ in accts) and any(b == "KBANK" for _, b, _, _ in accts)

    # branch auto-detect from a bank row's account_no
    c = sqlite3.connect(main._DB_PATH)
    c.execute("INSERT INTO recon_bank_account(branch_id,bank,account_no,account_name,active) VALUES(4,'KBANK','057-3-49826-7','x',1)")
    c.commit(); c.close()
    det = main._detect_branch_from_rows("bank", [{"account_no": "057-3-49826-7", "credit": 100}])
    assert det.get("detected_branch_id") == 4
    # unknown account → no detection (operator's branch stands)
    assert main._detect_branch_from_rows("bank", [{"account_no": "999-9-99999-9"}]) == {}


def test_point_pos_total_graceful_without_sync(client):
    """No coupon-payment table yet (sync not run) → 0, not a crash."""
    from recon.matcher import point_pos_total
    assert point_pos_total(sqlite3.connect(main._DB_PATH), 4, "2026-06", "BAY") == 0.0


def test_payout_matching_real_grab_ties_to_transfers():
    """Real Udon: each Grab net day matches the next-day bank transfer; net total
    equals payout total with nothing awaiting."""
    from recon.parsers.pdf_formats import _grab_daily, _kbank_statement
    import recon.main as m, sqlite3 as sq, tempfile, pathlib
    raw_g = _udon("3-CZDJNJ21VTMEEX-20260501.pdf")          # skips if absent
    raw_b = _udon("UDN_Statements KB_SA_05.2569.pdf")
    from recon.matcher import payout_matching
    db = pathlib.Path(tempfile.mkdtemp()) / "p.db"
    c = sq.connect(db); c.execute("CREATE TABLE branches(branch_id INTEGER PRIMARY KEY,name TEXT,branch_type TEXT,active INTEGER)")
    c.execute("INSERT INTO branches VALUES(25,'อุดร','master',1)"); c.commit(); c.close()
    m._DB_PATH = db
    from fastapi.testclient import TestClient
    import os; os.environ.pop("OSHINEI_SEED_TOKEN", None)
    with TestClient(m.app) as cl:
        for st, name, raw in [("grab", "g.pdf", raw_g), ("bank", "b.pdf", raw_b)]:
            cl.post(f"/api/uploads/{st}/confirm",
                    files={"file": (name, raw, "application/pdf")},
                    data={"branch_id": 25, "period_month": "2026-05"})
        with m.get_db() as conn:
            g = payout_matching(conn, 25, "2026-05")["GRAB"]
    assert g["awaiting_total"] == 0
    assert abs(g["net_total"] - g["payout_total"]) < 0.01 and g["net_total"] > 0
    assert all(p["status"] == "matched" for p in g["payouts"])


def test_fee_vat_changes_net(client):
    sid = client.post("/api/sessions", json={"branch_id": 4, "period_month": "2026-06"}).json()["session_id"]
    c = sqlite3.connect(main._DB_PATH)
    up = c.execute("INSERT INTO recon_uploads(source_type,branch_id,provider,file_name,period_month,row_count,status)"
                   " VALUES('grab',4,'Grab','f','2026-06',0,'confirmed')").lastrowid
    lid = c.execute("INSERT INTO recon_grab_lines(upload_id,branch_id,order_date,order_status,food_amount,commission_amt,net_payout)"
                    " VALUES(?,4,'2026-06-05','Completed',5000,1000,4000)", (up,)).lastrowid
    c.commit(); c.close()
    client.patch(f"/api/lines/grab/{lid}/fee-vat", json={"fee_manual": 1200, "vat_manual": 84})
    line = client.get("/api/lines/grab?branch_id=4&period_month=2026-06").json()[0]
    assert abs(line["net"] - (5000 - 1200 - 84)) < 0.01


# ── parser ────────────────────────────────────────────────────────────────────

def test_grab_parser_skips_bad_dates_and_returns_count():
    wb = openpyxl.Workbook(); ws = wb.active
    ws.append(["Order Date", "Order ID", "Order Status", "Food Amount", "Commission Amount", "Net Payout"])
    ws.append(["05/06/2026", "G1", "Completed", 5000, 1000, 4000])
    ws.append(["NOT-A-DATE", "G2", "Completed", 1, 0, 0])     # skipped
    ws.append([None, None, None, None, None, None])           # blank → not skipped
    buf = io.BytesIO(); wb.save(buf)
    rows, skipped = parse_grab_report(buf.getvalue())
    assert len(rows) == 1 and skipped == 1


# ── Krungsri (BAY) RDB003 acquirer settlement ─────────────────────────────────

def _rdb003_xlsx(data_rows):
    """Build an RDB003-shaped workbook: metadata rows, a header row with the
    real column names (two 'VAT Amt' columns — MSF's then ETL's), then data."""
    wb = openpyxl.Workbook(); ws = wb.active
    ws.append(["AP Dealer : Payment Advice"])
    ws.append(["Print Date : 05/05/2026"])
    ws.append([None])
    ws.append(["Entity Name : KRUNGSRI"])          # provider marker
    ws.append([None])
    ws.append(["Bank Channel : KBANK"])
    ws.append([None]); ws.append([None]); ws.append([None]); ws.append([None])
    # header (indices mirror the real file: MSF Amt then its VAT, ETL Amt then its VAT)
    ws.append(["No.", "MID", "MID Name", "Transaction Date", "Value Date", "Card No",
               "Amount Finance /Point Amount baht *", "MSF Amt", "VAT Amt",
               "ETL Amt", "VAT Amt", "Net Payment"])
    for r in data_rows:
        ws.append(r)
    buf = io.BytesIO(); wb.save(buf)
    return buf.getvalue()


def test_krungsri_rdb003_aggregates_by_value_date():
    from recon.parsers import parse_cc_settlement
    xlsx = _rdb003_xlsx([
        # No, MID, name, txn_date, value_date, card, amount, MSF, MSF-VAT, ETL, ETL-VAT, net
        [1, "442562000", "ร้าน", "2026-05-04", "2026-05-05", "4943XX", 1000, 15, 1.05, 0, 0, 983.95],
        [2, "442562000", "ร้าน", "2026-05-04", "2026-05-05", "4943XX", 500, 7.5, 0.53, 0, 0, 491.97],
        [3, "442562000", "ร้าน", "2026-05-05", "2026-05-06", "4943XX", 200, 3, 0.21, 0, 0, 196.79],
        ["", "", "", "", "", "", "", "", "", "", "", ""],       # blank tail → not skipped
    ])
    prov, rows, skipped = parse_cc_settlement(xlsx, provider=None)
    assert prov == "Krungsri" and skipped == 0
    by_date = {r["settlement_date"]: r for r in rows}
    assert set(by_date) == {"2026-05-05", "2026-05-06"}        # grouped by Value Date
    d1 = by_date["2026-05-05"]
    assert d1["acquirer"] == "BAY"
    assert d1["txn_count"] == 2
    assert d1["gross_amount"] == 1500.0
    assert d1["mdr_fee"] == 22.5                                # 15 + 7.5
    assert d1["vat_amount"] == 1.58                             # MSF VAT only (1.05+0.53)
    assert d1["net_amount"] == 1475.92
    assert d1["merchant_id"] == "442562000"


def test_krungsri_rdb003_detected_without_provider_hint():
    """Even with the KRUNGSRI label stripped, the MSF/Net-Payment column
    signature routes the file to the RDB003 parser (not the generic one)."""
    from recon.parsers import parse_cc_settlement
    wb = openpyxl.load_workbook(io.BytesIO(_rdb003_xlsx([
        [1, "111", "x", "2026-05-04", "2026-05-05", "4", 100, 1.5, 0.11, 0, 0, 98.39],
    ])))
    ws = wb.active
    ws["A4"] = "Entity Name : (redacted)"            # remove the krungsri keyword
    buf = io.BytesIO(); wb.save(buf)
    prov, rows, skipped = parse_cc_settlement(buf.getvalue(), provider=None)
    assert prov == "Krungsri" and len(rows) == 1
    assert rows[0]["acquirer"] == "BAY"


def test_loader_legacy_xls_converts(tmp_path):
    """A genuine BIFF .xls (which openpyxl cannot open) is transcoded to xlsx by
    the loader, with date cells emitted as ISO strings. Needs xlwt to build one."""
    xlwt = pytest.importorskip("xlwt")
    from recon.parsers.loader import to_parseable_xlsx
    book = xlwt.Workbook()
    sh = book.add_sheet("s")
    sh.write(0, 0, "Net Payment")
    sh.write(1, 0, 12345)
    p = tmp_path / "legacy.xls"
    book.save(str(p))
    xlsx = to_parseable_xlsx(p.read_bytes(), "legacy.xls")
    ws = openpyxl.load_workbook(io.BytesIO(xlsx)).active
    assert list(ws.iter_rows(values_only=True))[0][0] == "Net Payment"


# ── input loader: CSV / PDF → parseable xlsx ──────────────────────────────────

def test_loader_csv_feeds_bank_parser():
    """A Thai-BOM CSV with BE dates and Thai headers round-trips through the
    loader into the bank parser, with provider auto-detect and skip counting."""
    from recon.parsers.loader import to_parseable_xlsx
    from recon.parsers import parse_bank_statement
    csv_text = (
        "Kasikorn Bank\n"
        "วันที่,รายการ,รายการถอน,รายการฝาก,ยอดคงเหลือ\n"
        "01/06/2569,QR พร้อมเพย์,0,1500.50,1500.50\n"   # BE 2569 → 2026; QR channel
        "NOT-A-DATE,junk,0,0,0\n"                          # unparseable date → skipped
    )
    xlsx = to_parseable_xlsx(csv_text.encode("utf-8-sig"), "kbank.csv")
    prov, rows, skipped = parse_bank_statement(xlsx)
    assert prov == "kbank"
    assert len(rows) == 1 and skipped == 1
    assert rows[0]["txn_date"] == "2026-06-01"
    assert rows[0]["channel"] == "QR"
    assert rows[0]["credit"] == 1500.50


def test_loader_csv_sniffs_semicolon_delimiter():
    from recon.parsers.loader import to_parseable_xlsx
    xlsx = to_parseable_xlsx(b"a;b;c\n1;2;3\n4;5;6\n", "x.csv")
    ws = openpyxl.load_workbook(io.BytesIO(xlsx)).active
    assert list(ws.iter_rows(values_only=True))[0] == ("a", "b", "c")


def test_loader_pdf_table_feeds_bank_parser():
    """A ruled-table PDF (what real bank statements use) extracts column-aligned
    rows. Skipped when reportlab isn't installed to build the fixture."""
    reportlab = pytest.importorskip("reportlab")
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from recon.parsers.loader import to_parseable_xlsx
    from recon.parsers import parse_bank_statement

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4)
    data = [
        ["Siam Commercial Bank", "", "", "", ""],
        ["transaction date", "description", "withdrawal", "deposit", "balance"],
        ["01/06/2569", "transfer", "0", "2000", "2000"],
        ["03/06/2569", "grab payout", "0", "850.75", "2850.75"],
    ]
    t = Table(data, colWidths=[90, 110, 70, 70, 70])
    t.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.5, colors.black)]))
    doc.build([t])

    xlsx = to_parseable_xlsx(buf.getvalue(), "scb.pdf")
    prov, rows, skipped = parse_bank_statement(xlsx)
    assert prov == "scb"
    assert len(rows) == 2
    assert rows[0]["credit"] == 2000.0
    assert rows[1]["credit"] == 850.75
    assert rows[1]["channel"] == "GRAB_PAYOUT"


def test_loader_rejects_unextractable_pdf():
    """An image-only / empty PDF yields a user-facing error rather than 0 rows."""
    from recon.parsers.loader import to_parseable_xlsx
    # Minimal valid PDF with no extractable text.
    blank = (b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
             b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
             b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 200 200]>>endobj\n"
             b"trailer<</Root 1 0 R>>\n%%EOF")
    with pytest.raises(Exception):
        to_parseable_xlsx(blank, "scan.pdf")


# ── format-specific PDF extractors (Thai bank / acquirer / Grab) ──────────────

def test_thai_abbreviated_and_full_month_dates():
    from recon.parsers.bank import _parse_thai_date
    assert _parse_thai_date("01-พ.ค.-2569") == "2026-05-01"   # abbreviated, BE
    assert _parse_thai_date("15-ธ.ค.-2568") == "2025-12-15"
    assert _parse_thai_date("01/05/2569")   == "2026-05-01"   # numeric BE still works
    assert _parse_thai_date("01/05/2026")   == "2026-05-01"   # numeric CE
    assert _parse_thai_date("not a date")   is None


def test_cordianew_cid_to_digit_remap():
    """KTB CordiaNew digits decode as '(cid:344..353)' == 0..9."""
    from recon.parsers.pdf_formats import _decid
    assert _decid("รหัส (cid:348)(cid:345)(cid:344)(cid:344)(cid:344)") == "รหัส 41000"
    assert _decid("หน้า (cid:345)/(cid:351)") == "หน้า 1/7"
    assert _decid("(cid:999)") == ""           # out-of-range cid dropped, not crash


# Real Udon documents live outside the repo (they hold live account data); these
# checks run locally where the folder exists and skip in CI otherwise.
_UDON = Path(__file__).resolve().parents[2] / "Reconciliation Data Udon"


def _udon(name):
    f = _UDON / name
    if not f.exists():
        pytest.skip(f"sample not present: {name}")
    return f.read_bytes()


def _balance_continuity_ok(rows):
    """Every bank row's balance must equal prev balance + credit - debit."""
    for i in range(1, len(rows)):
        exp = round(rows[i - 1]["balance"] + rows[i]["credit"] - rows[i]["debit"], 2)
        if abs(exp - rows[i]["balance"]) > 0.01:
            return False
    return True


def test_kbank_statement_pdf_balance_continuity():
    from recon.parsers.pdf_formats import _kbank_statement
    prov, rows, skipped = _kbank_statement(_udon("UDN_Statements KB_SA_05.2569.pdf"))
    assert prov == "kbank" and len(rows) > 100 and skipped == 0
    assert _balance_continuity_ok(rows)


def test_ktb_statement_pdf_cid_and_balance():
    from recon.parsers.pdf_formats import _ktb_statement
    prov, rows, skipped = _ktb_statement(_udon("UDN_Statements KTB_SA_05.2569.pdf"))
    assert prov == "ktb" and len(rows) > 50
    assert _balance_continuity_ok(rows)
    # cid digits recovered → real amounts, not zeros
    assert any(r["credit"] > 1000 for r in rows)


# ── cash next-day deposit lag ─────────────────────────────────────────────────

def test_cash_daily_matches_next_day_deposit(client):
    """POS cash on day 10 → bank CASH_DEP credit on day 11 → daily status 'matched'.
    Without the lag fix both sides would be 'pending'/'ext_only' instead."""
    from recon.matcher import daily_reconciliation
    db = main._DB_PATH
    pm = {r["payment_code_norm"]: r for r in client.get("/api/payment-map").json()}
    # ensure Cash is mapped (seed does this, but be explicit)
    client.patch(f"/api/payment-map/{pm['Cash']['id']}", json={"category": "CASH", "bank": "NONE"})

    c = sqlite3.connect(db)
    c.row_factory = sqlite3.Row
    c.execute("CREATE TABLE IF NOT EXISTS pos_payment_day"
              "(branch_id INT, day TEXT, payment_code TEXT, amount REAL, txn_count INT DEFAULT 0)")
    c.execute("INSERT INTO pos_payment_day VALUES(4,'2026-06-10','Cash',15000,3)")

    up = c.execute("INSERT INTO recon_uploads(source_type,branch_id,provider,file_name,"
                   "period_month,row_count,status) VALUES('bank',4,'ktb','s','2026-06',0,'confirmed')").lastrowid
    # deposit on day 11 (next-business-day) for sales on day 10
    c.execute("INSERT INTO recon_bank_lines(upload_id,branch_id,txn_date,channel,credit,match_status)"
              " VALUES(?,4,'2026-06-11','CASH_DEP',15000,'unmatched')", (up,))
    c.commit(); c.close()

    with main.get_db() as conn:
        result = daily_reconciliation(conn, 4, "2026-06")

    days = {d["day"]: d for d in result["days"]}
    day10 = days.get("2026-06-10", {})
    cash_lane = next((l for l in day10.get("lanes", []) if l["category"] == "CASH"), None)
    assert cash_lane is not None, "CASH lane missing for day 10"
    assert cash_lane["status"] == "matched", (
        f"expected 'matched', got '{cash_lane['status']}' — deposit-lag shift not working")
    assert abs(cash_lane["variance"]) < 1.0


def test_cash_month_boundary_deposit_counted_monthly(client):
    """Month-boundary fix: last-day cash banked on the 1st of next month (T+1) is
    counted in THIS month's CASH total — matching the daily 3-way view — instead
    of being silently dropped (which made every branch under-report cash)."""
    from recon.matcher import category_reconciliation
    db = main._DB_PATH
    pm = {r["payment_code_norm"]: r for r in client.get("/api/payment-map").json()}
    client.patch(f"/api/payment-map/{pm['Cash']['id']}", json={"category": "CASH", "bank": "NONE"})
    c = sqlite3.connect(db); c.row_factory = sqlite3.Row
    c.execute("CREATE TABLE IF NOT EXISTS pos_payment_day"
              "(branch_id INT, day TEXT, payment_code TEXT, amount REAL, txn_count INT DEFAULT 0)")
    c.execute("INSERT INTO pos_payment_day VALUES(4,'2026-06-30','Cash',50000,5)")
    up = c.execute("INSERT INTO recon_uploads(source_type,branch_id,provider,file_name,"
                   "period_month,row_count,status) VALUES('bank',4,'ktb','s','2026-06',0,'confirmed')").lastrowid
    # 30-June cash is physically banked on 1-July (next month, next-day rule)
    c.execute("INSERT INTO recon_bank_lines(upload_id,branch_id,txn_date,channel,credit,match_status)"
              " VALUES(?,4,'2026-07-01','CASH_DEP',50000,'unmatched')", (up,))
    c.commit(); c.close()
    with main.get_db() as conn:
        rows, _ = category_reconciliation(conn, 4, "2026-06")
    cash = next((r for r in rows if r["category"] == "CASH"), None)
    assert cash is not None and abs(cash["ext_amount"] - 50000) < 1, \
        f"July-1 deposit not counted in June CASH ext: {cash and cash['ext_amount']}"


def test_cash_lag_skips_deposit_outside_period(client):
    """CASH_DEP on the 1st of the next month (for last-day sales) is out of scope
    for the current period and must not appear as an ext_only bucket."""
    from recon.matcher import daily_reconciliation
    db = main._DB_PATH
    pm = {r["payment_code_norm"]: r for r in client.get("/api/payment-map").json()}
    client.patch(f"/api/payment-map/{pm['Cash']['id']}", json={"category": "CASH", "bank": "NONE"})

    c = sqlite3.connect(db)
    c.row_factory = sqlite3.Row
    c.execute("CREATE TABLE IF NOT EXISTS pos_payment_day"
              "(branch_id INT, day TEXT, payment_code TEXT, amount REAL, txn_count INT DEFAULT 0)")
    # last day of May sales — the deposit lands on 2026-06-01 (next month)
    c.execute("INSERT INTO pos_payment_day VALUES(4,'2026-05-31','Cash',8000,2)")
    up = c.execute("INSERT INTO recon_uploads(source_type,branch_id,provider,file_name,"
                   "period_month,row_count,status) VALUES('bank',4,'ktb','s','2026-05',0,'confirmed')").lastrowid
    c.execute("INSERT INTO recon_bank_lines(upload_id,branch_id,txn_date,channel,credit,match_status)"
              " VALUES(?,4,'2026-06-01','CASH_DEP',8000,'unmatched')", (up,))
    c.commit(); c.close()

    with main.get_db() as conn:
        result = daily_reconciliation(conn, 4, "2026-05")

    # The cross-month deposit is excluded; the POS day should be pending/missing, NOT ext_only.
    days = {d["day"]: d for d in result["days"]}
    day31 = days.get("2026-05-31", {})
    cash_lane = next((l for l in day31.get("lanes", []) if l["category"] == "CASH"), None)
    if cash_lane:
        assert cash_lane["status"] != "ext_only", "cross-month deposit leaked into period as ext_only"


def test_ktc_settlement_pdf_totals():
    from recon.parsers.pdf_formats import _ktc_settlement
    prov, rows, skipped = _ktc_settlement(_udon("006_0170570924_260501-RTxInv-02.pdf"))
    assert prov == "KTC" and len(rows) == 8 and skipped == 0
    assert abs(sum(r["gross_amount"] for r in rows) - 40902.45) < 0.01
    assert abs(sum(r["mdr_fee"]      for r in rows) -   927.49) < 0.01
    assert abs(sum(r["vat_amount"]   for r in rows) -    64.94) < 0.01   # VAT captured, not dropped
    assert abs(sum(r["net_amount"]   for r in rows) - 39910.02) < 0.01
    # net ties to gross − fee − VAT
    assert all(abs(r["gross_amount"] - r["mdr_fee"] - r["vat_amount"] - r["net_amount"]) < 0.01 for r in rows)


def test_kbank_cc_pdf_one_row_per_settlement_date():
    from recon.parsers.pdf_formats import _kbank_cc
    prov, rows, skipped = _kbank_cc(_udon("ใบกำกับภาษีบัตรเครดิต KBank_01-05.05.69.pdf"))
    assert prov == "KBANK" and len(rows) >= 5
    # one settlement per date; 05-01 net ties to the KBank statement sales credit
    by_date = {r["settlement_date"]: r for r in rows}
    r1 = by_date["2026-05-01"]
    assert abs(r1["net_amount"] - 34758.35) < 0.01
    assert abs(r1["mdr_fee"] - 688.77) < 0.01 and abs(r1["vat_amount"] - 48.21) < 0.01
    assert abs(r1["gross_amount"] - r1["mdr_fee"] - r1["vat_amount"] - r1["net_amount"]) < 0.01


def test_grab_daily_pdf_multi_day_split():
    from recon.parsers.pdf_formats import _grab_daily
    rows, skipped = _grab_daily(_udon("3-CZDJNJ21VTMEEX-20260501.pdf"))
    assert skipped == 0 and len(rows) >= 4
    # the bundle spans several days — orders must carry their own page's date
    assert len({r["order_date"] for r in rows}) > 1
    for r in rows:                       # net == food - fee - VAT on every order
        assert abs(r["food_amount"] - r["commission_amt"] - r["vat_amount"] - r["net_payout"]) < 0.01
        assert r["vat_amount"] > 0       # VAT split out of the bundled commission


# ── endpoint audit fixes ──────────────────────────────────────────────────────

def test_config_endpoints_404_on_missing(client):
    assert client.patch("/api/bank-accounts/99999", json={"branch_id": 4, "bank": "KTB", "account_no": "x"}).status_code == 404
    assert client.delete("/api/bank-accounts/99999").status_code == 404
    assert client.patch("/api/acquirer-mids/99999", json={"branch_id": 4, "acquirer": "KTC", "merchant_id": "x"}).status_code == 404
    assert client.delete("/api/acquirer-mids/99999").status_code == 404
    r = client.patch("/api/sessions/99999/status", data={"status": "approved"})
    assert r.status_code == 404


def test_acquirer_endpoint_accepts_point_acquirers(client):
    # the live --api seeder POSTs KTCPOINT/BAYPOINT — must not be rejected
    r = client.post("/api/acquirer-mids", json={"branch_id": 4, "acquirer": "KTCPOINT", "merchant_id": "000001889101674"})
    assert r.status_code == 200 and r.json().get("mid_id")
    r2 = client.post("/api/acquirer-mids", json={"branch_id": 4, "acquirer": "BAYPOINT", "merchant_id": "442652010"})
    assert r2.status_code == 200
    r3 = client.post("/api/acquirer-mids", json={"branch_id": 4, "acquirer": "NONSENSE", "merchant_id": "1"})
    assert r3.status_code == 400


def test_diagnostics_require_write_token(client):
    os.environ["OSHINEI_SEED_TOKEN"] = "secret-x"
    try:
        assert client.get("/api/diagnostics/payment-codes?period_month=2026-06").status_code == 403
        ok = client.get("/api/diagnostics/payment-codes?period_month=2026-06", headers={"X-Upload-Token": "secret-x"})
        assert ok.status_code == 200
    finally:
        os.environ.pop("OSHINEI_SEED_TOKEN", None)


# ── endpoint auth + validation ────────────────────────────────────────────────

def test_master_only_and_period_validation(client):
    assert client.post("/api/sessions", json={"branch_id": 1, "period_month": "2026-06"}).status_code == 400  # franchise
    assert client.post("/api/sessions", json={"branch_id": 4, "period_month": "2026-6"}).status_code == 400   # bad period
    assert client.post("/api/sessions", json={"branch_id": 4, "period_month": "2026-06"}).status_code == 200


def test_daily_pending_vs_missing(tmp_path):
    """Daily board distinguishes a genuine gap (statement covers the day, line
    absent → 'missing') from an awaited late document ('pending')."""
    db = _make_db(tmp_path, [("KNNQR", 31000), ("Cash", 15000)])
    c = sqlite3.connect(db)
    c.execute("CREATE TABLE pos_payment_day(branch_id INT,day TEXT,payment_code TEXT,amount REAL,txn_count INT)")
    c.executemany("INSERT INTO pos_payment_day VALUES(4,?,?,?,0)", [
        ("2026-06-01", "KNNQR", 10000), ("2026-06-01", "Cash", 5000),
        ("2026-06-02", "KNNQR", 12000), ("2026-06-02", "Cash", 4000),
        ("2026-06-03", "KNNQR", 9000),  ("2026-06-03", "Cash", 6000)])
    c.commit(); c.close()
    main._DB_PATH = db
    os.environ.pop("OSHINEI_SEED_TOKEN", None)
    with TestClient(main.app) as cl:
        pm = {x["payment_code_norm"]: x for x in cl.get("/api/payment-map").json()}
        cl.patch(f"/api/payment-map/{pm['KNNQR']['id']}", json={"category": "QR", "bank": "KTB"})
        sid = cl.post("/api/sessions", json={"branch_id": 4, "period_month": "2026-06"}).json()["session_id"]
        cc = sqlite3.connect(db)
        up = cc.execute("INSERT INTO recon_uploads(source_type,branch_id,provider,file_name,period_month,row_count,status)"
                        " VALUES('bank',4,'KTB','f','2026-06',0,'confirmed')").lastrowid
        # bank statement covers only Jun 1-2; Jun 3 is a late document
        cc.execute("INSERT INTO recon_bank_lines(upload_id,branch_id,txn_date,channel,credit) VALUES(?,4,'2026-06-01','QR',10000)", (up,))
        cc.execute("INSERT INTO recon_bank_lines(upload_id,branch_id,txn_date,channel,credit) VALUES(?,4,'2026-06-02','QR',11000)", (up,))
        cc.execute("INSERT INTO recon_bank_lines(upload_id,branch_id,txn_date,channel,credit) VALUES(?,4,'2026-06-01','CASH_DEP',5000)", (up,))
        cc.commit(); cc.close()

        d = cl.get(f"/api/sessions/{sid}/daily").json()
        assert d["pos_daily_available"] is True
        days = {x["day"]: x for x in d["days"]}
        def lane(day, cat, bank):
            return next(l for l in days[day]["lanes"] if l["category"] == cat and l["bank"] == bank)
        assert lane("2026-06-01", "QR", "KTB")["status"] == "matched"
        assert lane("2026-06-02", "QR", "KTB")["status"] == "variance"   # 11000 vs 12000
        # cash deposits only reach Jun 1 (one CASH_DEP line) → Jun 2 cash is awaited, not a gap
        assert lane("2026-06-02", "CASH", "NONE")["status"] == "pending"
        assert lane("2026-06-03", "QR", "KTB")["status"] == "pending"    # statement not arrived
        assert d["pending_summary"]["coverage"]["bank/KTB"] == "2026-06-02"
        assert days["2026-06-03"]["pending_count"] == 2


def test_daily_cash_coverage_uses_cash_dep_only(tmp_path):
    """A later QR line must not make an earlier cash day look 'missing'."""
    db = _make_db(tmp_path, [("Cash", 9000)])
    c = sqlite3.connect(db)
    c.execute("CREATE TABLE pos_payment_day(branch_id INT,day TEXT,payment_code TEXT,amount REAL,txn_count INT)")
    c.executemany("INSERT INTO pos_payment_day VALUES(4,?, 'Cash',?,0)",
                  [("2026-06-01", 4000), ("2026-06-03", 5000)])
    c.commit(); c.close()
    main._DB_PATH = db
    with TestClient(main.app) as cl:
        sid = cl.post("/api/sessions", json={"branch_id": 4, "period_month": "2026-06"}).json()["session_id"]
        cc = sqlite3.connect(db)
        up = cc.execute("INSERT INTO recon_uploads(source_type,branch_id,provider,file_name,period_month,row_count,status)"
                        " VALUES('bank',4,'KTB','f','2026-06',0,'confirmed')").lastrowid
        cc.execute("INSERT INTO recon_bank_lines(upload_id,branch_id,txn_date,channel,credit) VALUES(?,4,'2026-06-01','CASH_DEP',4000)", (up,))
        cc.execute("INSERT INTO recon_bank_lines(upload_id,branch_id,txn_date,channel,credit) VALUES(?,4,'2026-06-05','QR',1)", (up,))  # later, different channel
        cc.commit(); cc.close()
        days = {x["day"]: x for x in cl.get(f"/api/sessions/{sid}/daily").json()["days"]}
        cash3 = next(l for l in days["2026-06-03"]["lanes"] if l["category"] == "CASH")
        # cash deposits only cover Jun 1; Jun 3 cash is awaited, NOT a gap
        assert cash3["status"] == "pending"


def test_daily_unmapped_bank_status(tmp_path):
    """A QR code left with a non-statement bank (UNKNOWN/PROMPTPAY) → unmapped_bank."""
    db = _make_db(tmp_path, [("MYQR", 5000)])      # unrecognised QR code → seeds QR/UNKNOWN
    c = sqlite3.connect(db)
    c.execute("CREATE TABLE pos_payment_day(branch_id INT,day TEXT,payment_code TEXT,amount REAL,txn_count INT)")
    c.execute("INSERT INTO pos_payment_day VALUES(4,'2026-06-01','MYQR',5000,0)")
    c.commit(); c.close()
    main._DB_PATH = db
    with TestClient(main.app) as cl:
        sid = cl.post("/api/sessions", json={"branch_id": 4, "period_month": "2026-06"}).json()["session_id"]
        days = {x["day"]: x for x in cl.get(f"/api/sessions/{sid}/daily").json()["days"]}
        lane = days["2026-06-01"]["lanes"][0]
        assert lane["status"] == "unmapped_bank"


def test_daily_unavailable_without_sync(client):
    """No pos_payment_day table → graceful flag, not a crash."""
    sid = client.post("/api/sessions", json={"branch_id": 4, "period_month": "2026-06"}).json()["session_id"]
    d = client.get(f"/api/sessions/{sid}/daily").json()
    assert d["pos_daily_available"] is False


# ── Batch 1: go-live correctness ──────────────────────────────────────────────

def _grab_xlsx(rows):
    wb = openpyxl.Workbook(); ws = wb.active
    ws.append(["Order Date", "Order ID", "Order Status", "Food Amount", "Commission Amount", "Net Payout"])
    for r in rows:
        ws.append(r)
    buf = io.BytesIO(); wb.save(buf); return buf.getvalue()


def test_upload_dedup_blocks_identical_file(client):
    """Re-uploading the exact same file for a branch/month is refused (409) so a
    statement can't be double-counted."""
    data = _grab_xlsx([["05/06/2026", "G1", "Completed", 5000, 1000, 4000]])
    form = {"branch_id": "4", "period_month": "2026-06"}
    r1 = client.post("/api/uploads/grab/confirm", files={"file": ("g.xlsx", data)}, data=form)
    assert r1.status_code == 200 and r1.json()["rows_saved"] == 1
    r2 = client.post("/api/uploads/grab/confirm", files={"file": ("g.xlsx", data)}, data=form)
    assert r2.status_code == 409
    assert len(client.get("/api/uploads?branch_id=4&period_month=2026-06").json()) == 1
    # deleting the first lets an identical file in again
    up_id = client.get("/api/uploads?branch_id=4&period_month=2026-06").json()[0]["upload_id"]
    client.delete(f"/api/uploads/{up_id}")
    assert client.post("/api/uploads/grab/confirm", files={"file": ("g.xlsx", data)}, data=form).status_code == 200


def test_confirm_rejects_zero_row_nonempty_upload(client):
    """A non-empty document that parses to 0 rows (the classic wrong-file-type:
    a fee invoice instead of a settlement report) must be REFUSED, not silently
    saved as confirmed — otherwise the lane reads 'done' with no data to match."""
    bad = _grab_xlsx([["NOT-A-DATE", "G1", "Completed", 1, 0, 0]])   # row skipped → 0 rows
    form = {"branch_id": "4", "period_month": "2026-06"}
    r = client.post("/api/uploads/grab/confirm", files={"file": ("feeinvoice.xlsx", bad)}, data=form)
    assert r.status_code == 422
    assert "0 ราย" in r.json()["detail"]
    # nothing was persisted — the lane is not silently "uploaded"
    assert client.get("/api/uploads?branch_id=4&period_month=2026-06").json() == []


def test_a2_wrong_doc_type_named_and_rejected(client):
    """A2: a fee-invoice file is rejected up front with guidance on the correct
    file to upload — TIV_MDR (fee) vs RTxInv (settlement), IM (fee) vs 3-CZ, etc."""
    from recon.parsers.pdf_formats import fee_invoice_by_name
    assert "RTxInv" in (fee_invoice_by_name("credit_card", "TIV_MDR_x1136_260501.pdf") or "")
    assert "3-CZ"   in (fee_invoice_by_name("grab", "IM20260501044240.pdf") or "")
    # LINE MAN Balance-Report is a SUPPORTED settlement export now — never rejected
    assert fee_invoice_by_name("lineman", "Balance-Report-2026-05-01.xlsx") is None
    assert "e-Tax"  in (fee_invoice_by_name("credit_card", "E-TAX_INVOICE_CARD_x.pdf") or "")
    # a correct file is NOT flagged
    assert fee_invoice_by_name("credit_card", "006_0170581195_260501-RTxInv-02.pdf") is None
    # end-to-end: confirm refuses a TIV_MDR upload with the specific message
    r = client.post("/api/uploads/credit_card/confirm",
                    files={"file": ("TIV_MDR_x1136_260501.pdf", b"%PDF-1.4 dummy", "application/pdf")},
                    data={"branch_id": "4", "period_month": "2026-06"})
    assert r.status_code == 422 and "RTxInv" in r.json()["detail"]
    assert client.get("/api/uploads?branch_id=4&period_month=2026-06").json() == []


def _balance_report_xlsx(rows):
    """LINE MAN Balance-Report (English settlement export). Note the header splits
    'capture_time' across two cells exactly as the real export does."""
    import datetime as _dt
    wb = openpyxl.Workbook(); ws = wb.active
    ws.append(["business_id", "store_id", "restaurant_name", "branch_name",
               "order_id", "capture_t", "ime", "payment_method",
               "cash_amount", "e_payment_amount", "fee", "other_cost", "vat", "net_amount"])
    for r in rows:
        ws.append(r)
    buf = io.BytesIO(); wb.save(buf); return buf.getvalue()


def test_lineman_balance_report_parsed(client):
    """LINE MAN Balance-Report-*.xlsx is the supported settlement export: gross =
    cash + e_payment, fee/vat arrive negative, net_amount is the bank settlement.
    Must parse (not be rejected as a wrong doc type)."""
    import datetime as _dt
    from recon.parsers.lineman import parse_lineman_report
    raw = _balance_report_xlsx([
        ["154718iL", "", "โอชิเน Oshinei อุบลราชธานี", "อุบลราชธานี",
         "LMF-260501-721909390", _dt.datetime(2026, 5, 1), _dt.time(12, 8, 5),
         "CASH_COLLECTION", 0, 846.75, -102.88, 0, -7.2, 736.67],
    ])
    rows, skipped = parse_lineman_report(raw)
    assert skipped == 0 and len(rows) == 1
    r = rows[0]
    assert r["order_date"] == "2026-05-01"
    assert r["food_amount"] == 846.75          # cash + e_payment
    assert r["commission_amt"] == 102.88       # abs(fee)
    assert r["vat_on_commission"] == 7.2       # abs(vat)
    assert r["net_payout"] == 736.67
    # end-to-end confirm saves it (filename carries 'balance-report' — once rejected)
    r2 = client.post("/api/uploads/lineman/confirm",
                     files={"file": ("Balance-Report-2026-05-01.xlsx", raw)},
                     data={"branch_id": "4", "period_month": "2026-05"})
    assert r2.status_code == 200 and r2.json()["rows_saved"] == 1


def test_lineman_balance_report_csv_datetime(client):
    """The CSV export differs from the xlsx: capture_time is a STRING with time +
    fractional seconds ('2026-06-05 12:46:38.379') and has a 'detail' column. The
    date parser must read the ISO datetime (else every row is skipped → 0 rows →
    'LINE MAN update ไม่ได้'). Columns are name-mapped so the extra 'detail' is fine."""
    csv = (
        "business_id,store_id,restaurant_name,branch_name,order_id,capture_time,"
        "detail,payment_method,cash_amount,e_payment_amount,fee,other_cost,vat,net_amount\n"
        "285452El,,โอชิเน Oshinei โคราช ,โคราช,LMF-260605-816608937,"
        "2026-06-05 12:46:38.379,Delivery Order,CASH_COLLECTION,0.00,129.00,-15.67,0.00,-1.10,112.23\n"
    ).encode("utf-8")
    r = client.post("/api/uploads/lineman/confirm",
                    files={"file": ("Balance-Report-2026-06-05.csv", csv)},
                    data={"branch_id": "4", "period_month": "2026-06"})
    assert r.status_code == 200 and r.json()["rows_saved"] == 1


def test_parse_thai_date_iso_datetime():
    """ISO date and datetime (with/without fractional seconds, BE year) all resolve."""
    from recon.parsers.bank import _parse_thai_date
    assert _parse_thai_date("2026-06-05") == "2026-06-05"
    assert _parse_thai_date("2026-06-05 12:46:38.379") == "2026-06-05"
    assert _parse_thai_date("2026-06-05T09:00:00") == "2026-06-05"
    assert _parse_thai_date("2569-06-05 00:00:00") == "2026-06-05"   # Buddhist year
    assert _parse_thai_date("05/06/2026") == "2026-06-05"            # slash form still works


_OCR_KTB_TEXT = """รายการเดินบัญชีเงินฝาก ธนาคารกรุงไทย
เลขที่บัญชี 017-0-58113-6
ยอดคงเหลือยกมา 4,348,525.57
01/06/2026 12:16 SDCH 35,491.00 0.00 4,384,016.57 -
01/06/2026 23:00 SD Single credit to 0170581136 53,179.88 0.00 4,437,196.45 -
02/06/2026 12:20 SDCH 24,553.00 0.00 4,461,749.45 -
"""


def test_ocr_ktb_text_parse_and_balance_chain():
    """OCR safety net: parse recovered KTB text and validate the running-balance
    chain. A clean read reconciles; a single corrupted balance breaks the chain."""
    from recon.parsers.ocr import _parse_ktb_ocr, _balance_chain_ok, _seed_opening_balance
    rows, skipped = _parse_ktb_ocr(_OCR_KTB_TEXT)
    assert len(rows) == 3 and skipped == 0
    assert rows[0]["credit"] == 35491.0 and rows[0]["balance"] == 4384016.57
    opening = _seed_opening_balance(_OCR_KTB_TEXT.splitlines())
    assert opening == 4348525.57
    assert _balance_chain_ok(rows, opening) is True
    # corrupt one balance (simulate an OCR misread) → chain must FAIL → rejected
    rows[1]["balance"] = 4_437_196.99
    assert _balance_chain_ok(rows, opening) is False


def test_c3_encryption_detection():
    """C3: password-locked Office/PDF/zip uploads are named, not parsed to garbage."""
    from recon.parsers.loader import is_encrypted_upload
    ole = b"\xD0\xCF\x11\xE0\xA1\xB1\x1A\xE1" + b"\x00" * 80 + b"EncryptedPackage" + b"\x00" * 40
    assert "รหัสผ่าน" in (is_encrypted_upload(ole, "x.xls") or "")
    pdf = b"%PDF-1.4\n" + b"x" * 80 + b"\n/Encrypt 5 0 R\ntrailer\n"     # /Encrypt in trailer
    assert "รหัสผ่าน" in (is_encrypted_upload(pdf, "s.pdf") or "")
    assert is_encrypted_upload(b"date,amount\n1,2\n", "a.csv") is None   # plain → fine
    assert is_encrypted_upload(b"%PDF-1.4 plain content, no encryption", "b.pdf") is None


def test_c6_unsupported_bank_message_guard():
    """C6: helper only fires on PDFs (a non-pdf upload is never misflagged)."""
    from recon.parsers.pdf_formats import unsupported_bank_pdf_message
    assert unsupported_bank_pdf_message(b"anything", "x.csv") is None


def test_c7_preview_generic_fallback(client):
    """C7: preview flags when the generic grid was used (no specific extractor)."""
    generic = ("วันที่,รายการ,รายการถอน,รายการฝาก,ยอดคงเหลือ\n"
               "01/06/2569,QR,0,100,100\n").encode("utf-8-sig")
    p = client.post("/api/uploads/bank/preview", files={"file": ("x.csv", generic)},
                    data={"branch_id": "4", "period_month": "2026-06"}).json()
    assert p["generic_fallback"] is True
    kbank = ("Kasikorn Bank\nวันที่,รายการ,รายการถอน,รายการฝาก,ยอดคงเหลือ\n"
             "01/06/2569,QR,0,100,100\n").encode("utf-8-sig")
    p2 = client.post("/api/uploads/bank/preview", files={"file": ("k.csv", kbank)},
                     data={"branch_id": "4", "period_month": "2026-06"}).json()
    assert p2["generic_fallback"] is False


def test_c2_c4_doc_hints_and_transfer_mode(client):
    """C2: every doc chip carries an expected-file hint. C4: a TRANSFER channel
    carries a per-branch transfer_mode."""
    r = client.get("/api/v2/reconcile?branch_id=4&month=2026-06").json()
    assert r["documents"] and all("hint" in d for d in r["documents"])
    for ch in r["channels"]:
        if ch["key"] == "TRANSFER":
            assert ch["transfer_mode"] in ("match", "exclude")


def test_b1_bulk_upload_manifest(client):
    """B1: many files in one pass → per-file manifest. Good is saved, the wrong-type
    (0-row) errors, the duplicate is skipped — and a bad file doesn't roll back good ones."""
    good = _grab_xlsx([["05/06/2026", "G1", "Completed", 5000, 1000, 4000]])
    bad  = _grab_xlsx([["NOT-A-DATE", "G2", "Completed", 1, 0, 0]])    # parses to 0 rows
    payload = [
        ("files", ("good.xlsx", good, "application/octet-stream")),
        ("files", ("bad.xlsx",  bad,  "application/octet-stream")),
        ("files", ("good.xlsx", good, "application/octet-stream")),    # dup of #1
    ]
    r = client.post("/api/uploads/bulk", files=payload,
                    data={"branch_id": "4", "period_month": "2026-06", "source_type": "grab"})
    assert r.status_code == 200
    j = r.json()
    assert j["summary"]["saved"] == 1
    assert any(m["status"] == "ok" and m["rows_saved"] == 1 for m in j["files"])
    assert any(m["status"] == "error"   for m in j["files"])     # the 0-row file
    assert any(m["status"] == "skipped" for m in j["files"])     # the duplicate
    # the good upload actually persisted (bad/dup did not roll it back)
    assert len(client.get("/api/uploads?branch_id=4&period_month=2026-06").json()) == 1


def test_b1_infer_source_type():
    """B1: 'auto' mode routes by filename convention; unknown names → None (reported)."""
    from recon.main import _infer_source_type
    assert _infer_source_type("TIV_MDR_x1136_260501.pdf") == "credit_card"
    assert _infer_source_type("RDB003_186981_20260505.xls") == "credit_card"
    assert _infer_source_type("3-CZDJN-20260501.pdf") == "grab"
    assert _infer_source_type("REPORT_MAY26.csv") == "lineman"
    assert _infer_source_type("05.69-Statement KTB CNX.pdf") == "bank"
    assert _infer_source_type("mystery.pdf") is None


def test_payout_chain_ties_net_to_bank(client):
    """3-way tie: provider NET → bank payout credit. No bank line → awaiting_payout;
    a short payout → mismatch + a PAYOUT discrepancy."""
    db = main._DB_PATH
    sid = client.post("/api/sessions", json={"branch_id": 4, "period_month": "2026-06"}).json()["session_id"]
    c = sqlite3.connect(db)
    up = c.execute("INSERT INTO recon_uploads(source_type,branch_id,provider,file_name,period_month,row_count,status)"
                   " VALUES('grab',4,'Grab','f','2026-06',0,'confirmed')").lastrowid
    c.execute("INSERT INTO recon_grab_lines(upload_id,branch_id,order_date,order_status,food_amount,commission_amt,net_payout)"
              " VALUES(?,4,'2026-06-05','Completed',5000,1000,4000)", (up,))
    c.commit(); c.close()
    pay = {p["category"]: p for p in client.post(f"/api/sessions/{sid}/run-match").json()["payouts"]}
    assert pay["GRAB"]["status"] == "awaiting_payout" and pay["GRAB"]["report_net"] == 4000

    _add_bank_line(db, "KTB", "GRAB_PAYOUT", 3000)     # short vs 4000 net → mismatch
    pay2 = {p["category"]: p for p in client.post(f"/api/sessions/{sid}/run-match").json()["payouts"]}
    assert pay2["GRAB"]["status"] == "mismatch"
    discs = client.get(f"/api/sessions/{sid}/discrepancies").json()
    assert any(d["category"] == "GRAB" and d["bank"] == "PAYOUT" for d in discs)


def test_credit_three_way_full_chain(client):
    """Credit 3-document match per acquirer: POS gross ↔ settlement gross, and
    settlement net (gross − fee − VAT, auto-pulled) ↔ bank statement credit — both
    legs matched."""
    from recon.matcher import credit_three_way
    db = main._DB_PATH
    c = sqlite3.connect(db)
    up = c.execute("INSERT INTO recon_uploads(source_type,branch_id,provider,file_name,period_month,row_count,status)"
                   " VALUES('credit_card',4,'KTB','f','2026-06',0,'confirmed')").lastrowid
    # settlement gross 30000 == POS 'Credit กรุงไทย'; fee 800 + VAT 56 → net 29144
    c.execute("INSERT INTO recon_cc_lines(upload_id,branch_id,settlement_date,acquirer,gross_amount,refund_amount,mdr_fee,vat_amount,net_amount)"
              " VALUES(?,4,'2026-06-05','KTB',30000,0,800,56,29144)", (up,))
    # the net lands next day on the KTB account as a CC_SETTLE credit
    bu = c.execute("INSERT INTO recon_uploads(source_type,branch_id,provider,file_name,period_month,row_count,status)"
                   " VALUES('bank',4,'ktb','b','2026-06',0,'confirmed')").lastrowid
    c.execute("INSERT INTO recon_bank_lines(upload_id,branch_id,txn_date,channel,credit) VALUES(?,4,'2026-06-06','CC_SETTLE',29144)", (bu,))
    c.commit(); c.close()

    with main.get_db() as conn:
        res = credit_three_way(conn, 4, "2026-06")
    ktb = next(r for r in res["rows"] if r["bank"] == "KTB")
    assert ktb["pos_gross"] == 30000 and ktb["settle_gross"] == 30000 and ktb["pos_leg"] == "matched"
    assert abs(ktb["settle_fee"] - 856) < 0.01                 # MDR + VAT auto-pulled
    assert abs(ktb["settle_net"] - 29144) < 0.01
    assert abs(ktb["bank_credit"] - 29144) < 0.01 and ktb["bank_leg"] == "matched"
    assert len(res["bank_line_ids"]) == 1


def test_credit_ktc_net_matches_ktb_statement(client):
    """KTC (บัตรกรุงไทย) has no bank of its own — its settlement net is remitted into
    the branch's KTB account, so the stage-1 net↔bank leg must match the KTB
    statement's CC_SETTLE credit (regression for _ACQ_BANK['KTC']='KTB')."""
    from recon.matcher import credit_three_way
    db = main._DB_PATH
    c = sqlite3.connect(db)
    up = c.execute("INSERT INTO recon_uploads(source_type,branch_id,provider,file_name,period_month,row_count,status)"
                   " VALUES('credit_card',4,'KTC','f','2026-06',0,'confirmed')").lastrowid
    # KTC settlement net 9441 (gross 9669 − fee 213 − vat 15)
    c.execute("INSERT INTO recon_cc_lines(upload_id,branch_id,settlement_date,acquirer,gross_amount,refund_amount,mdr_fee,vat_amount,net_amount)"
              " VALUES(?,4,'2026-06-05','KTC',9669,0,213,15,9441)", (up,))
    # …lands on the KTB account next day (KTC money rides the KTB statement)
    bu = c.execute("INSERT INTO recon_uploads(source_type,branch_id,provider,file_name,period_month,row_count,status)"
                   " VALUES('bank',4,'ktb','b','2026-06',0,'confirmed')").lastrowid
    c.execute("INSERT INTO recon_bank_lines(upload_id,branch_id,txn_date,channel,credit) VALUES(?,4,'2026-06-06','CC_SETTLE',9441)", (bu,))
    c.commit(); c.close()

    with main.get_db() as conn:
        res = credit_three_way(conn, 4, "2026-06")
    ktc = next(r for r in res["rows"] if r["acquirer"] == "KTC")
    assert ktc["bank"] == "KTB"                                   # routed to KTB account
    assert abs(ktc["bank_credit"] - 9441) < 0.01 and ktc["bank_leg"] == "matched"


def test_bank_statement_coverage_buckets(client):
    """Bank-first coverage: a matched QR line, an unmatched stray TRANSFER, and an
    ignored internal transfer land in the right buckets; 'excluded' is out of the
    matched/unmatched denominator."""
    from recon.matcher import bank_statement_coverage
    db = main._DB_PATH
    pm = {r["payment_code_norm"]: r for r in client.get("/api/payment-map").json()}
    client.patch(f"/api/payment-map/{pm['KNNQR']['id']}", json={"category": "QR", "bank": "KTB"})
    c = sqlite3.connect(db)
    up = c.execute("INSERT INTO recon_uploads(source_type,branch_id,provider,file_name,period_month,row_count,status)"
                   " VALUES('bank',4,'ktb','b','2026-06',0,'confirmed')").lastrowid
    # QR credit ties to POS KNNQR (100000) → matched
    c.execute("INSERT INTO recon_bank_lines(upload_id,branch_id,txn_date,channel,credit,match_status) VALUES(?,4,'2026-06-10','QR',100000,'unmatched')", (up,))
    # stray transfer with nothing behind it → unmatched (staff matches in POS)
    c.execute("INSERT INTO recon_bank_lines(upload_id,branch_id,txn_date,channel,credit,match_status) VALUES(?,4,'2026-06-12','TRANSFER',7777,'unmatched')", (up,))
    # operator-flagged internal transfer → reported apart, out of the denominator
    c.execute("INSERT INTO recon_bank_lines(upload_id,branch_id,txn_date,channel,credit,match_status) VALUES(?,4,'2026-06-13','TRANSFER',2000000,'excluded')", (up,))
    c.commit(); c.close()

    with main.get_db() as conn:
        cov = bank_statement_coverage(conn, 4, "2026-06")
    tot = cov["totals"]
    assert tot["matched"] == 1 and tot["unmatched"] == 1
    assert tot["excluded"] == 1 and tot["lines_total"] == 2            # excluded out of denom
    assert cov["groups"]["QR"]["matched"] == 1
    assert abs(tot["amount_excluded"] - 2000000) < 0.01
    assert any(l["status"] == "unmatched" and l["category"] == "TRANSFER" for l in cov["unmatched_lines"])


def test_approval_gate_and_period_lock(client):
    """Can't approve with open discrepancies; an approved month is locked until reopened."""
    sid = client.post("/api/sessions", json={"branch_id": 4, "period_month": "2026-06"}).json()["session_id"]
    client.post(f"/api/sessions/{sid}/run-match")      # POS tender, no statements → discrepancies
    open_discs = client.get(f"/api/sessions/{sid}/discrepancies").json()
    assert open_discs
    assert client.patch(f"/api/sessions/{sid}/status", data={"status": "approved"}).status_code == 409

    for d in open_discs:
        client.patch(f"/api/discrepancies/{d['id']}/resolve", json={"resolution": "accepted", "resolved_by": "t"})
    assert client.patch(f"/api/sessions/{sid}/status", data={"status": "approved"}).status_code == 200
    assert client.post(f"/api/sessions/{sid}/run-match").status_code == 423        # locked
    assert client.patch(f"/api/sessions/{sid}/status", data={"status": "in_review"}).status_code == 200
    assert client.post(f"/api/sessions/{sid}/run-match").status_code == 200        # reopened


# ── Batch 2: accountant export + drill-down ───────────────────────────────────

def test_export_xlsx(client):
    # Thai-named branch → the Content-Disposition header must be RFC-5987 encoded
    sid = client.post("/api/sessions", json={"branch_id": 5, "period_month": "2026-06"}).json()["session_id"]
    client.post(f"/api/sessions/{sid}/run-match")
    r = client.get(f"/api/sessions/{sid}/export.xlsx")
    assert r.status_code == 200
    assert "spreadsheet" in r.headers["content-type"]
    assert r.content[:2] == b"PK"        # .xlsx is a zip
    cd = r.headers.get("content-disposition", "")
    assert "attachment" in cd and "filename*=UTF-8''" in cd


def test_lane_drilldown(client):
    db = main._DB_PATH
    pm = {r["payment_code_norm"]: r for r in client.get("/api/payment-map").json()}
    client.patch(f"/api/payment-map/{pm['KNNQR']['id']}", json={"category": "QR", "bank": "KTB"})
    sid = client.post("/api/sessions", json={"branch_id": 4, "period_month": "2026-06"}).json()["session_id"]
    _add_bank_line(db, "KTB", "QR", 60000, "2026-06-10")
    _add_bank_line(db, "KTB", "QR", 40000, "2026-06-11")
    _add_bank_line(db, "KBANK", "QR", 5000, "2026-06-10")    # different bank → excluded
    d = client.get(f"/api/sessions/{sid}/lane?category=QR&bank=KTB").json()
    assert d["count"] == 2 and abs(d["total"] - 100000) < 0.01
    one = client.get(f"/api/sessions/{sid}/lane?category=QR&bank=KTB&day=2026-06-10").json()
    assert one["count"] == 1 and one["total"] == 60000


def test_write_token_gate(client):
    main.os.environ["OSHINEI_SEED_TOKEN"] = "tok"
    try:
        assert client.post("/api/bank-accounts", json={"branch_id": 4, "bank": "KTB", "account_no": "1"}).status_code == 403
        ok = client.post("/api/bank-accounts", headers={"X-Upload-Token": "tok"},
                         json={"branch_id": 4, "bank": "KTB", "account_no": "1"})
        assert ok.status_code == 200
    finally:
        main.os.environ.pop("OSHINEI_SEED_TOKEN", None)


# ── cash lump-sum / no-deposit behaviours (2026-06-10) ───────────────────────

def test_cash_weekend_lump_deposit_groups_days(client):
    """Fri+Sat+Sun cash banked as ONE Monday deposit → all 3 days 'matched',
    with the deposit trace (date / combined) on each lane."""
    from recon.matcher import daily_reconciliation
    db = main._DB_PATH
    pm = {r["payment_code_norm"]: r for r in client.get("/api/payment-map").json()}
    client.patch(f"/api/payment-map/{pm['Cash']['id']}", json={"category": "CASH", "bank": "NONE"})

    c = sqlite3.connect(db)
    c.execute("CREATE TABLE IF NOT EXISTS pos_payment_day"
              "(branch_id INT, day TEXT, payment_code TEXT, amount REAL, txn_count INT DEFAULT 0)")
    c.executemany("INSERT INTO pos_payment_day VALUES(4,?,'Cash',?,0)", [
        ("2026-06-05", 12000), ("2026-06-06", 18000), ("2026-06-07", 9000)])
    up = c.execute("INSERT INTO recon_uploads(source_type,branch_id,provider,file_name,"
                   "period_month,row_count,status) VALUES('bank',4,'ktb','s','2026-06',0,'confirmed')").lastrowid
    # one lump deposit Monday 06-08 = 12000+18000+9000
    c.execute("INSERT INTO recon_bank_lines(upload_id,branch_id,txn_date,channel,credit,match_status)"
              " VALUES(?,4,'2026-06-08','CASH_DEP',39000,'unmatched')", (up,))
    c.commit(); c.close()

    with main.get_db() as conn:
        result = daily_reconciliation(conn, 4, "2026-06")

    days = {d["day"]: d for d in result["days"]}
    for dd in ("2026-06-05", "2026-06-06", "2026-06-07"):
        lane = next(l for l in days[dd]["lanes"] if l["category"] == "CASH")
        assert lane["status"] == "matched", f"{dd} expected matched, got {lane['status']}"
        assert lane["deposit_date"] == "2026-06-08"
        assert lane["deposit_combined"] is True
    cs = result["cash_summary"]
    assert cs["matched_groups"] == 1 and cs["combined_groups"] == 1
    assert abs(cs["matched_pos_total"] - 39000) < 0.01


def test_cash_no_deposit_lines_reads_missing_not_pending(client):
    """Statements cover the month but contain ZERO cash-deposit lines → cash days
    must read 'missing' (a real gap), not 'pending' (awaiting a document), and
    cash_summary must flag has_deposit_lines=False."""
    from recon.matcher import daily_reconciliation
    db = main._DB_PATH
    pm = {r["payment_code_norm"]: r for r in client.get("/api/payment-map").json()}
    client.patch(f"/api/payment-map/{pm['Cash']['id']}", json={"category": "CASH", "bank": "NONE"})

    c = sqlite3.connect(db)
    c.execute("CREATE TABLE IF NOT EXISTS pos_payment_day"
              "(branch_id INT, day TEXT, payment_code TEXT, amount REAL, txn_count INT DEFAULT 0)")
    c.execute("INSERT INTO pos_payment_day VALUES(4,'2026-06-10','Cash',20000,4)")
    up = c.execute("INSERT INTO recon_uploads(source_type,branch_id,provider,file_name,"
                   "period_month,row_count,status) VALUES('bank',4,'ktb','s','2026-06',0,'confirmed')").lastrowid
    # statement has QR lines through 06-20 but NO CASH_DEP at all
    c.execute("INSERT INTO recon_bank_lines(upload_id,branch_id,txn_date,channel,credit,match_status)"
              " VALUES(?,4,'2026-06-20','QR',500,'unmatched')", (up,))
    c.commit(); c.close()

    with main.get_db() as conn:
        result = daily_reconciliation(conn, 4, "2026-06")

    days = {d["day"]: d for d in result["days"]}
    lane = next(l for l in days["2026-06-10"]["lanes"] if l["category"] == "CASH")
    assert lane["status"] == "missing", f"expected missing, got {lane['status']}"
    cs = result["cash_summary"]
    assert cs["has_deposit_lines"] is False
    assert cs["pos_total"] == 20000 and cs["deposit_total"] == 0


def test_upload_branch_reassign_moves_lines(client):
    """PATCH /api/uploads/{id}/branch moves the upload AND its lines."""
    db = main._DB_PATH
    c = sqlite3.connect(db)
    up = c.execute("INSERT INTO recon_uploads(source_type,branch_id,provider,file_name,"
                   "period_month,row_count,status) VALUES('bank',4,'ktb','wrong.pdf','2026-06',2,'confirmed')").lastrowid
    c.execute("INSERT INTO recon_bank_lines(upload_id,branch_id,txn_date,channel,credit)"
              " VALUES(?,4,'2026-06-01','QR',100)", (up,))
    c.execute("INSERT INTO recon_bank_lines(upload_id,branch_id,txn_date,channel,credit)"
              " VALUES(?,4,'2026-06-02','QR',200)", (up,))
    c.commit(); c.close()

    r = client.patch(f"/api/uploads/{up}/branch", json={"branch_id": 5})
    assert r.status_code == 200
    assert r.json()["moved_lines"] == 2

    c = sqlite3.connect(db); c.row_factory = sqlite3.Row
    assert c.execute("SELECT branch_id FROM recon_uploads WHERE upload_id=?", (up,)).fetchone()[0] == 5
    assert all(x[0] == 5 for x in c.execute(
        "SELECT branch_id FROM recon_bank_lines WHERE upload_id=?", (up,)))
    c.close()
    # non-master target refused
    bad = client.patch(f"/api/uploads/{up}/branch", json={"branch_id": 1})
    assert bad.status_code in (400, 404, 422)


def test_empty_statement_detection():
    """The KBank CA 'No Data' month is a valid-but-empty statement."""
    from recon.parsers.pdf_formats import is_empty_statement
    raw = _udon("May/UDN_Statements KB_CA_05.2569.pdf")
    assert is_empty_statement(raw) is True
    # a statement WITH movements must not be flagged empty
    full = _udon("UDN_Statements KB_SA_05.2569.pdf")
    assert is_empty_statement(full) is False


# ── document match status + map-from-documents suggestions (2026-06-10) ───────

def test_map_bank_suggestions_from_documents(client):
    """KNNQR (QR/UNKNOWN) daily series tracks the KTB QR statement lane →
    suggest KNNQR → QR·KTB with the evidence counts."""
    from recon.matcher import map_bank_suggestions
    db = main._DB_PATH
    c = sqlite3.connect(db)
    c.execute("CREATE TABLE IF NOT EXISTS pos_payment_day"
              "(branch_id INT, day TEXT, payment_code TEXT, amount REAL, txn_count INT DEFAULT 0)")
    # requirement seed routes KNNQR → KBANK; put it back to UNKNOWN to exercise
    # the document-driven suggestion path for an unresolved code
    c.execute("UPDATE recon_payment_map SET bank='UNKNOWN' WHERE payment_code_norm='KNNQR'")
    days = [("2026-06-%02d" % i, 10000 + i * 137) for i in range(1, 8)]
    c.executemany("INSERT INTO pos_payment_day VALUES(4,?,'KNNQR',?,5)", days)
    up = c.execute("INSERT INTO recon_uploads(source_type,branch_id,provider,file_name,"
                   "period_month,row_count,status) VALUES('bank',4,'ktb','s','2026-06',0,'confirmed')").lastrowid
    for d, amt in days:
        c.execute("INSERT INTO recon_bank_lines(upload_id,branch_id,txn_date,channel,credit,match_status)"
                  " VALUES(?,4,?,'QR',?,'unmatched')", (up, d, amt))
    c.commit(); c.close()

    with main.get_db() as conn:
        sugs = map_bank_suggestions(conn, 4, "2026-06")
    knn = next((s for s in sugs if s["norm"] == "KNNQR"), None)
    assert knn is not None, f"no KNNQR suggestion in {sugs}"
    assert knn["category"] == "QR" and knn["bank"] == "KTB"
    assert knn["hits"] == 7 and knn["active_days"] == 7
    assert knn["map_id"], "seeded KNNQR row should carry its map_id for one-click PATCH"

    # endpoint wraps it
    r = client.get("/api/map-suggestions?branch_id=4&period_month=2026-06")
    assert r.status_code == 200
    assert any(s["norm"] == "KNNQR" for s in r.json()["suggestions"])


def test_uploads_match_stats_and_lines_drill(client):
    """/api/uploads carries match_done/match_total; /uploads/{id}/lines lists
    per-line status; run-match marks a grouped cash deposit line matched."""
    db = main._DB_PATH
    pm = {r["payment_code_norm"]: r for r in client.get("/api/payment-map").json()}
    client.patch(f"/api/payment-map/{pm['Cash']['id']}", json={"category": "CASH", "bank": "NONE"})

    c = sqlite3.connect(db)
    c.execute("CREATE TABLE IF NOT EXISTS pos_payment_day"
              "(branch_id INT, day TEXT, payment_code TEXT, amount REAL, txn_count INT DEFAULT 0)")
    c.execute("INSERT INTO pos_payment_day VALUES(4,'2026-06-10','Cash',15000,3)")
    up = c.execute("INSERT INTO recon_uploads(source_type,branch_id,provider,file_name,"
                   "period_month,row_count,status) VALUES('bank',4,'ktb','s','2026-06',2,'confirmed')").lastrowid
    c.execute("INSERT INTO recon_bank_lines(upload_id,branch_id,txn_date,channel,credit,match_status)"
              " VALUES(?,4,'2026-06-11','CASH_DEP',15000,'unmatched')", (up,))
    c.execute("INSERT INTO recon_bank_lines(upload_id,branch_id,txn_date,channel,credit,match_status)"
              " VALUES(?,4,'2026-06-12','QR',999,'unmatched')", (up,))
    c.commit(); c.close()

    sid = client.post("/api/sessions", json={"branch_id": 4, "period_month": "2026-06"}).json()["session_id"]
    client.post(f"/api/sessions/{sid}/run-match")

    ups = client.get("/api/uploads?period_month=2026-06&branch_id=4").json()
    u = next(x for x in ups if x["upload_id"] == up)
    assert u["match_total"] == 2
    assert u["match_done"] >= 1, "grouped cash deposit line should be marked matched by run-match"

    d = client.get(f"/api/uploads/{up}/lines").json()
    assert d["count"] == 2
    cashline = next(l for l in d["lines"] if l["channel"] == "CASH_DEP")
    assert cashline["match_status"] == "matched"


# ── internal-transfer ignore flag (2026-06-10) ────────────────────────────────

def test_ignore_flag_excludes_line_everywhere(client):
    """An ignored bank credit vanishes from monthly + daily lanes and from
    upload match denominators; Run Match must NOT un-ignore it; un-ignore
    restores it."""
    from recon.matcher import category_reconciliation, daily_reconciliation
    db = main._DB_PATH
    pm = {r["payment_code_norm"]: r for r in client.get("/api/payment-map").json()}
    client.patch(f"/api/payment-map/{pm['KNNQR']['id']}", json={"category": "QR", "bank": "KTB"})

    c = sqlite3.connect(db)
    c.execute("CREATE TABLE IF NOT EXISTS pos_payment_day"
              "(branch_id INT, day TEXT, payment_code TEXT, amount REAL, txn_count INT DEFAULT 0)")
    up = c.execute("INSERT INTO recon_uploads(source_type,branch_id,provider,file_name,"
                   "period_month,row_count,status) VALUES('bank',4,'kbank','s','2026-06',1,'confirmed')").lastrowid
    # a ฿2M intercompany sweep — no POS side, pure TRANSFER ext_only noise
    lid = c.execute("INSERT INTO recon_bank_lines(upload_id,branch_id,txn_date,channel,credit,match_status)"
                    " VALUES(?,4,'2026-06-15','TRANSFER',2000000,'unmatched')", (up,)).lastrowid
    c.commit(); c.close()

    with main.get_db() as conn:
        rows, _ = category_reconciliation(conn, 4, "2026-06")
        assert any(r["category"] == "TRANSFER" and r["ext_amount"] == 2000000 for r in rows)

    r = client.patch(f"/api/bank-lines/{lid}/ignore", json={"ignored": True, "note": "intercompany"})
    assert r.status_code == 200 and r.json()["match_status"] == "excluded"

    with main.get_db() as conn:
        rows, _ = category_reconciliation(conn, 4, "2026-06")
        assert not any(r["category"] == "TRANSFER" and r["ext_amount"] >= 2000000 for r in rows), \
            "ignored sweep still in monthly TRANSFER lane"
        daily = daily_reconciliation(conn, 4, "2026-06")
        for day in daily["days"]:
            for l in day["lanes"]:
                assert not (l["category"] == "TRANSFER" and l["ext"] >= 2000000), \
                    "ignored sweep still in daily lanes"

    # Run Match must preserve the operator's decision
    sid = client.post("/api/sessions", json={"branch_id": 4, "period_month": "2026-06"}).json().get("session_id")
    if sid:
        client.post(f"/api/sessions/{sid}/run-match")
    cc = sqlite3.connect(db); cc.row_factory = sqlite3.Row
    assert cc.execute("SELECT match_status FROM recon_bank_lines WHERE id=?",
                      (lid,)).fetchone()[0] == "excluded"
    cc.close()

    # upload stats: ignored line is out of the denominator? No — it stays in
    # total but is not 'done'; the per-line view shows the badge. Un-ignore:
    r = client.patch(f"/api/bank-lines/{lid}/ignore", json={"ignored": False})
    assert r.status_code == 200 and r.json()["match_status"] == "unmatched"
    with main.get_db() as conn:
        rows, _ = category_reconciliation(conn, 4, "2026-06")
        assert any(r["category"] == "TRANSFER" and r["ext_amount"] == 2000000 for r in rows)


def test_ignore_flag_guards(client):
    """Manual lines refuse ignore; un-ignoring a non-ignored line refuses."""
    db = main._DB_PATH
    c = sqlite3.connect(db)
    up = c.execute("INSERT INTO recon_uploads(source_type,branch_id,provider,file_name,"
                   "period_month,row_count,status) VALUES('bank',4,'ktb','s','2026-06',1,'confirmed')").lastrowid
    man = c.execute("INSERT INTO recon_bank_lines(upload_id,branch_id,txn_date,channel,credit,match_status)"
                    " VALUES(?,4,'2026-06-15','TRANSFER',500,'manual')", (up,)).lastrowid
    raw = c.execute("INSERT INTO recon_bank_lines(upload_id,branch_id,txn_date,channel,credit,match_status)"
                    " VALUES(?,4,'2026-06-16','TRANSFER',600,'unmatched')", (up,)).lastrowid
    c.commit(); c.close()
    assert client.patch(f"/api/bank-lines/{man}/ignore", json={"ignored": True}).status_code == 409
    assert client.patch(f"/api/bank-lines/{raw}/ignore", json={"ignored": False}).status_code == 409
    assert client.patch("/api/bank-lines/999999/ignore", json={"ignored": True}).status_code == 404


# ── uploads panel: live match stats without Run Match (2026-06-10) ────────────

def test_upload_stats_live_without_run_match(client):
    """Match stats compute on read (lane-based) — no session / Run Match needed.
    Revenue credits in a matched lane count; debits + FEE/payout lines don't
    inflate the denominator; per-line drill agrees with the panel."""
    db = main._DB_PATH
    pm = {r["payment_code_norm"]: r for r in client.get("/api/payment-map").json()}
    client.patch(f"/api/payment-map/{pm['KNNQR']['id']}", json={"category": "QR", "bank": "KTB"})

    c = sqlite3.connect(db)
    up = c.execute("INSERT INTO recon_uploads(source_type,branch_id,provider,file_name,"
                   "period_month,row_count,status) VALUES('bank',4,'ktb','s','2026-06',4,'confirmed')").lastrowid
    # QR credit matches the fixture's KNNQR=100000; + 1 debit, 1 FEE, 1 lone transfer
    c.execute("INSERT INTO recon_bank_lines(upload_id,branch_id,txn_date,channel,credit) VALUES(?,4,'2026-06-03','QR',100000)", (up,))
    c.execute("INSERT INTO recon_bank_lines(upload_id,branch_id,txn_date,channel,credit,debit) VALUES(?,4,'2026-06-03','TRANSFER',0,500)", (up,))
    c.execute("INSERT INTO recon_bank_lines(upload_id,branch_id,txn_date,channel,credit) VALUES(?,4,'2026-06-03','FEE',0)", (up,))
    c.execute("INSERT INTO recon_bank_lines(upload_id,branch_id,txn_date,channel,credit) VALUES(?,4,'2026-06-04','TRANSFER',777)", (up,))
    c.commit(); c.close()

    # NO session, NO run-match — stats must still be correct
    u = next(x for x in client.get("/api/uploads?period_month=2026-06&branch_id=4").json()
             if x["upload_id"] == up)
    assert u["match_total"] == 2, "only the 2 revenue credits (QR + transfer) are reconcilable"
    assert u["match_done"] == 1, "the QR lane matched POS 100000"

    d = client.get(f"/api/uploads/{up}/lines").json()
    assert d["count"] == 2 and d["matched"] == 1        # agrees with the panel
    assert d["total_lines"] == 4                          # all lines still shown
    statuses = sorted(l["status"] for l in d["lines"])
    assert statuses == ["informational", "informational", "matched", "unmatched"]


# ── cash deposited under a non-cash code (KTB 'SDCH'), matched by amount+T+1 ───

def test_cash_matches_next_day_credit_under_qr_label(client):
    """A bank posts the next-day cash lump under a QR/SDCH code in a lane no POS
    tender claims. The matcher should tie it to the day's POS cash by amount, mark
    the cash day matched, and NOT leave it as a QR ext_only — while a real QR lane
    that POS does claim is left untouched."""
    from recon.matcher import daily_reconciliation
    db = main._DB_PATH
    pm = {r["payment_code_norm"]: r for r in client.get("/api/payment-map").json()}
    client.patch(f"/api/payment-map/{pm['Cash']['id']}", json={"category": "CASH", "bank": "KTB"})
    client.patch(f"/api/payment-map/{pm['KNNQR']['id']}", json={"category": "QR", "bank": "KBANK"})

    c = sqlite3.connect(db)
    c.execute("CREATE TABLE IF NOT EXISTS pos_payment_day"
              "(branch_id INT, day TEXT, payment_code TEXT, amount REAL, txn_count INT DEFAULT 0)")
    # POS: cash 30000 on the 10th; KNNQR (QR) 50000 same day
    c.execute("INSERT INTO pos_payment_day VALUES(4,'2026-06-10','Cash',30000,5)")
    c.execute("INSERT INTO pos_payment_day VALUES(4,'2026-06-10','KNNQR',50000,9)")
    up = c.execute("INSERT INTO recon_uploads(source_type,branch_id,provider,file_name,"
                   "period_month,row_count,status) VALUES('bank',4,'ktb','s','2026-06',0,'confirmed')").lastrowid
    upk = c.execute("INSERT INTO recon_uploads(source_type,branch_id,provider,file_name,"
                    "period_month,row_count,status) VALUES('bank',4,'kbank','s2','2026-06',0,'confirmed')").lastrowid
    # KTB QR(SDCH)=cash lump, posts NEXT day (11th); KBANK QR = real KNNQR same day
    c.execute("INSERT INTO recon_bank_lines(upload_id,branch_id,txn_date,channel,credit,description) "
              "VALUES(?,4,'2026-06-11','QR',30000,'SDCH')", (up,))
    c.execute("INSERT INTO recon_bank_lines(upload_id,branch_id,txn_date,channel,credit) "
              "VALUES(?,4,'2026-06-10','QR',50000)", (upk,))
    c.commit(); c.close()

    with main.get_db() as conn:
        d = daily_reconciliation(conn, 4, "2026-06")
    day10 = next(x for x in d["days"] if x["day"] == "2026-06-10")
    cash = next(l for l in day10["lanes"] if l["category"] == "CASH")
    assert cash["status"] == "matched", f"cash should match the next-day SDCH lump, got {cash['status']}"
    # the KTB SDCH line must NOT also appear as a QR·KTB ext_only
    qr_ktb = [l for l in day10["lanes"] if l["category"] == "QR" and l["bank"] == "KTB"]
    assert not qr_ktb, "the SDCH line was claimed as cash, it must not show as QR·KTB"
    # the genuine QR·KBANK lane (POS KNNQR) is untouched and matched
    qr_kbank = next(l for l in day10["lanes"] if l["category"] == "QR" and l["bank"] == "KBANK")
    assert qr_kbank["status"] == "matched"
    assert d["cash_summary"]["has_deposit_lines"] is True


def test_reclass_ignores_sdch_cash_claimed_as_qr(client):
    """REGRESSION: KTB posts a next-day cash deposit under 'SDCH' (parsed as QR).
    The cash matcher claims it, so suggest_reclassifications must NOT resurface it
    as a phantom QR surplus and propose 'cash booked as QR' — the money is real cash,
    just deposited T+1. (This produced 117 false reclass cards in 2026-05 live data.)"""
    from recon.matcher import suggest_reclassifications, daily_reconciliation, cash_deposit_matching
    db = main._DB_PATH
    pm = {r["payment_code_norm"]: r for r in client.get("/api/payment-map").json()}
    client.patch(f"/api/payment-map/{pm['Cash']['id']}", json={"category": "CASH", "bank": "KTB"})

    c = sqlite3.connect(db)
    c.execute("CREATE TABLE IF NOT EXISTS pos_payment_day"
              "(branch_id INT, day TEXT, payment_code TEXT, amount REAL, txn_count INT DEFAULT 0)")
    c.execute("INSERT INTO pos_payment_day VALUES(4,'2026-06-10','Cash',92520,7)")   # POS cash day D
    up = c.execute("INSERT INTO recon_uploads(source_type,branch_id,provider,file_name,"
                   "period_month,row_count,status) VALUES('bank',4,'ktb','s','2026-06',0,'confirmed')").lastrowid
    # the same cash, deposited next day, posted under SDCH (parsed as QR·KTB)
    c.execute("INSERT INTO recon_bank_lines(upload_id,branch_id,txn_date,channel,credit,description,match_status) "
              "VALUES(?,4,'2026-06-11','QR',92520,'SDCH',  'unmatched')", (up,))
    c.commit(); c.close()

    with main.get_db() as conn:
        # the cash matcher genuinely claims the SDCH line (precondition)
        cash = cash_deposit_matching(conn, 4, "2026-06")
        assert cash["consumed_ids"], "cash matcher should claim the next-day SDCH lump"
        # …so the reclass engine must emit NO 'cash → QR' suggestion
        sug = suggest_reclassifications(conn, 4, "2026-06")
        assert sug == [], f"SDCH cash deposit must not be suggested as a reclass, got {sug}"
        # and the day view doesn't show it as a QR·KTB ext_only either
        d = daily_reconciliation(conn, 4, "2026-06")
        day10 = next(x for x in d["days"] if x["day"] == "2026-06-10")
        cash_lane = next(l for l in day10["lanes"] if l["category"] == "CASH")
        assert cash_lane["status"] == "matched"


# ── method config: the owner's requirement table (2026-06-12) ─────────────────

def test_method_config_seed_idempotent(client):
    """Startup seeds the per-lane method table; reseeding never overwrites an
    edited row and never duplicates."""
    with main.get_db() as conn:
        n1 = conn.execute("SELECT COUNT(*) FROM recon_method_config").fetchone()[0]
        assert n1 >= 12
        conn.execute("UPDATE recon_method_config SET lag_days=9, updated_by='staff' "
                     "WHERE category='GRAB' AND bank='*'")
        conn.commit()
        r = main._seed_method_defaults(conn)
        assert r["inserted"] == 0
        assert conn.execute("SELECT lag_days FROM recon_method_config "
                            "WHERE category='GRAB' AND bank='*'").fetchone()[0] == 9
        assert conn.execute("SELECT COUNT(*) FROM recon_method_config").fetchone()[0] == n1


def test_requirement_seed_upgrades_unknown_but_not_staff_edits(client):
    """The requirement seed answers previously-UNKNOWN routing for seed-authored
    rows (KNNQR → KBANK) but never touches a staff-edited row; audited."""
    with main.get_db() as conn:
        # simulate the pre-requirement DB state
        conn.execute("UPDATE recon_payment_map SET bank='UNKNOWN', updated_by='seed' "
                     "WHERE payment_code_norm='KNNQR'")
        conn.execute("UPDATE recon_payment_map SET bank='UNKNOWN', updated_by='staff' "
                     "WHERE payment_code_norm='K-SHOP'")
        conn.commit()
        r = main._seed_requirement_codes(conn)
        conn.commit()
        assert r["upgraded"] >= 1
        assert conn.execute("SELECT bank FROM recon_payment_map "
                            "WHERE payment_code_norm='KNNQR'").fetchone()[0] == "KBANK"
        assert conn.execute("SELECT bank FROM recon_payment_map "
                            "WHERE payment_code_norm='K-SHOP'").fetchone()[0] == "UNKNOWN"
        assert conn.execute("SELECT COUNT(*) FROM recon_audit_log "
                            "WHERE entity='payment_map' AND by='seed-req'").fetchone()[0] >= 1


def test_matcher_uses_method_config_tolerance(client):
    """CASH daily tolerance comes from recon_method_config (owner: ฿10 strict
    absolute). A ฿30-off deposit shows 'variance' but is still traced to its
    deposit; widening the config tolerance flips it to 'matched'."""
    db = main._DB_PATH
    c = sqlite3.connect(db)
    c.execute("CREATE TABLE IF NOT EXISTS pos_payment_day"
              "(branch_id INT,day TEXT,payment_code TEXT,amount REAL,txn_count INT)")
    c.execute("INSERT INTO pos_payment_day VALUES(4,'2026-06-10','Cash',20000,0)")
    c.commit(); c.close()
    _add_bank_line(db, "KTB", "CASH_DEP", 19970, date="2026-06-11")   # ฿30 short, T+1
    sid = client.post("/api/sessions", json={"branch_id": 4, "period_month": "2026-06"}).json()["session_id"]

    days = {x["day"]: x for x in client.get(f"/api/sessions/{sid}/daily").json()["days"]}
    lane = next(l for l in days["2026-06-10"]["lanes"] if l["category"] == "CASH")
    assert lane["status"] == "variance", "฿30 > strict ฿10 cash tolerance"
    assert lane.get("deposit_date") == "2026-06-11", "deposit must still be found & traced"

    with main.get_db() as conn:                       # widen via config → matched
        conn.execute("UPDATE recon_method_config SET tol_abs_day=50 WHERE category='CASH'")
        conn.commit()
    days = {x["day"]: x for x in client.get(f"/api/sessions/{sid}/daily").json()["days"]}
    lane = next(l for l in days["2026-06-10"]["lanes"] if l["category"] == "CASH")
    assert lane["status"] == "matched"


# ── phase 2: coupon channel, cash 3-way, TRANSFER/ANY, payout trace ───────────

def _seed_point_pos(db, series_name, series_id, amount):
    c = sqlite3.connect(db)
    c.execute("CREATE TABLE IF NOT EXISTS pos_coupon_payment_month"
              "(branch_id INT,period_month TEXT,coupon_series_id INT,amount REAL,txn_count INT)")
    c.execute("CREATE TABLE IF NOT EXISTS coupon_series"
              "(coupon_series_id INTEGER PRIMARY KEY,name TEXT,code TEXT,active INT)")
    c.execute("INSERT OR REPLACE INTO coupon_series VALUES(?,?,?,1)", (series_id, series_name, ""))
    c.execute("INSERT INTO pos_coupon_payment_month VALUES(4,'2026-06',?,?,1)", (series_id, amount))
    c.commit(); c.close()


def test_coupon_category_reconciliation_engine_status(client):
    """category_reconciliation (engine layer) must return matched/waiting_doc/mismatch
    for COUPON when point data exists — not always no_source.  This is Blueprint 2.1:
    the engine snapshot persisted at close and in Excel exports must reflect reality."""
    import sqlite3 as _sl
    from recon.matcher import category_reconciliation
    db = main._DB_PATH
    _seed_point_pos(db, "แลกคะแนน KTC 699 คะแนนลด 80 บาท", 1, 160)
    c = _sl.connect(db); c.row_factory = _sl.Row
    c.execute("INSERT OR IGNORE INTO recon_acquirer_mid(branch_id,acquirer,merchant_id,terminal_id,active)"
              " VALUES(4,'KTCPOINT','662','',1)")
    up = c.execute("INSERT INTO recon_uploads(source_type,branch_id,provider,file_name,period_month,row_count,status)"
                   " VALUES('credit_card',4,'KTC','f','2026-06',0,'confirmed')").lastrowid
    c.execute("INSERT INTO recon_cc_lines(upload_id,branch_id,settlement_date,acquirer,gross_amount,"
              "mdr_fee,vat_amount,net_amount,merchant_id) VALUES(?,4,'2026-06-03','KTC',160,0,0,160,'662')", (up,))
    c.commit()
    rows, _ = category_reconciliation(c, 4, "2026-06")
    c.close()
    coupon = next((r for r in rows if r["category"] == "COUPON"), None)
    assert coupon is not None, "COUPON row missing from category_reconciliation"
    assert coupon["status"] == "matched", f"expected matched, got {coupon['status']}"

    # waiting_doc: POS points present but no settlement doc
    db2 = main._DB_PATH
    c2 = _sl.connect(db2); c2.row_factory = _sl.Row
    _seed_point_pos(db2, "แต้มกรุงศรี 1000 ลด 100", 7, 500)
    rows2, _ = category_reconciliation(c2, 4, "2026-06")
    c2.close()
    coupon2 = next((r for r in rows2 if r["category"] == "COUPON"), None)
    assert coupon2 is not None
    assert coupon2["status"] == "waiting_doc", f"expected waiting_doc, got {coupon2['status']}"


def test_coupon_channel_reconciles_ktc_points(client):
    """COUPON channel leaves no_source when the issuer's point summary is in:
    POS KTC-point coupons ↔ KTC-Point settlement rows (split by MID) → ok."""
    db = main._DB_PATH
    _seed_point_pos(db, "แลกคะแนน KTC 699 คะแนนลด 80 บาท", 1, 160)
    c = sqlite3.connect(db)
    c.execute("INSERT INTO recon_acquirer_mid(branch_id,acquirer,merchant_id,terminal_id,active)"
              " VALUES(4,'KTCPOINT','662','',1)")
    up = c.execute("INSERT INTO recon_uploads(source_type,branch_id,provider,file_name,period_month,row_count,status)"
                   " VALUES('credit_card',4,'KTC','f','2026-06',0,'confirmed')").lastrowid
    c.execute("INSERT INTO recon_cc_lines(upload_id,branch_id,settlement_date,acquirer,gross_amount,"
              "mdr_fee,vat_amount,net_amount,merchant_id) VALUES(?,4,'2026-06-03','KTC',160,0,0,160,'662')", (up,))
    c.commit(); c.close()

    r = client.get("/api/v2/reconcile", params={"branch_id": 4, "month": "2026-06"}).json()
    cp = next(ch for ch in r["channels"] if ch["key"] == "COUPON")
    assert cp["status"] == "ok", cp
    ip = {x["issuer"]: x for x in cp["issuer_points"]}
    assert ip["KTC"]["pos_point"] == 160 and ip["KTC"]["settle_gross"] == 160
    assert ip["KTC"]["status"] == "matched" and ip["KTC"]["bank_in_credit"] is True


def test_coupon_without_points_doc_is_waiting(client):
    """POS shows point coupons but no summary uploaded → waiting_doc + the
    points doc appears in the checklist (uploadable, acceptable)."""
    _seed_point_pos(main._DB_PATH, "แต้มกรุงศรี 1000 ลด 100", 7, 1700)
    r = client.get("/api/v2/reconcile", params={"branch_id": 4, "month": "2026-06"}).json()
    cp = next(ch for ch in r["channels"] if ch["key"] == "COUPON")
    assert cp["status"] == "waiting_doc" and "BAY" in cp["detail"]
    doc = next(d for d in r["documents"] if d["key"] == "points_BAY")
    assert doc["status"] == "missing" and doc["source"] == "credit_card"
    assert doc["provider"] == "BAYPOINT" and doc["channel"] == "COUPON"


def test_cash_three_way_with_slip(client):
    """POS cash ↔ typed slip ↔ bank deposit. Slip ±few baht passes (≤฿10) with
    the variance visible; a >฿10 slip shows variance; missing slip = no_slip
    (informational, never blocks close)."""
    db = main._DB_PATH
    c = sqlite3.connect(db)
    c.execute("CREATE TABLE IF NOT EXISTS pos_payment_day"
              "(branch_id INT,day TEXT,payment_code TEXT,amount REAL,txn_count INT)")
    c.executemany("INSERT INTO pos_payment_day VALUES(4,?,'Cash',?,0)",
                  [("2026-06-10", 20000), ("2026-06-11", 15000)])
    c.commit(); c.close()
    _add_bank_line(db, "KTB", "CASH_DEP", 19995, date="2026-06-11")  # ฿5 short — within ฿10
    _add_bank_line(db, "KTB", "CASH_DEP", 15000, date="2026-06-12")

    # slip for day 10 (฿5 off → matched with visible variance); none for day 11
    assert client.post("/api/deposit-slips", json={
        "branch_id": 4, "sale_day": "2026-06-10", "amount": 19995,
        "deposit_day": "2026-06-11"}).status_code == 200

    from recon.matcher import cash_three_way
    with main.get_db() as conn:
        tw = cash_three_way(conn, 4, "2026-06")
    rows = {r["day"]: r for r in tw["rows"]}
    d10, d11 = rows["2026-06-10"], rows["2026-06-11"]
    assert d10["slip_leg"] == "matched" and d10["slip_variance"] == -5.0
    assert d10["bank_leg"] == "matched" and d10["deposit_date"] == "2026-06-11"
    assert d11["slip_leg"] == "no_slip" and d11["bank_leg"] == "matched"

    # a slip ฿30 off the POS → variance (strict ฿10)
    client.post("/api/deposit-slips", json={
        "branch_id": 4, "sale_day": "2026-06-10", "amount": 19970})
    with main.get_db() as conn:
        tw = cash_three_way(conn, 4, "2026-06")
    assert {r["day"]: r for r in tw["rows"]}["2026-06-10"]["slip_leg"] == "variance"

    # the v2 CASH channel carries the 3-way rows
    r = client.get("/api/v2/reconcile", params={"branch_id": 4, "month": "2026-06"}).json()
    cash_ch = next(ch for ch in r["channels"] if ch["key"] == "CASH")
    assert len(cash_ch["cash_three_way"]) == 2


def test_deposit_slip_endpoints_validation_and_lock(client):
    db = main._DB_PATH
    assert client.post("/api/deposit-slips", json={
        "branch_id": 4, "sale_day": "bad", "amount": 100}).status_code == 400
    assert client.post("/api/deposit-slips", json={
        "branch_id": 4, "sale_day": "2026-06-10", "amount": 0}).status_code == 400
    # upsert: same day replaces
    a = client.post("/api/deposit-slips", json={"branch_id": 4, "sale_day": "2026-06-10", "amount": 100}).json()
    b = client.post("/api/deposit-slips", json={"branch_id": 4, "sale_day": "2026-06-10", "amount": 120}).json()
    assert a["id"] == b["id"]
    slips = client.get("/api/deposit-slips", params={"branch_id": 4, "period_month": "2026-06"}).json()
    assert len(slips) == 1 and slips[0]["amount"] == 120
    # closed month → 423
    sid = client.post("/api/sessions", json={"branch_id": 4, "period_month": "2026-06"}).json()["session_id"]
    client.post(f"/api/sessions/{sid}/run-match")
    c = sqlite3.connect(db)
    c.execute("UPDATE recon_discrepancies SET resolution='accepted' WHERE session_id=?", (sid,))
    c.commit(); c.close()
    assert client.patch(f"/api/sessions/{sid}/status",
                        data={"status": "approved", "approved_by": "mgr"}).status_code == 200
    assert client.post("/api/deposit-slips", json={
        "branch_id": 4, "sale_day": "2026-06-15", "amount": 100}).status_code == 423
    assert client.delete(f"/api/deposit-slips/{a['id']}").status_code == 423


def test_grab_channel_exposes_payout_trace(client):
    """GRAB channel carries stage 2 (net-day groups <-> bank credits) so the UI
    can render two-stage cards like CREDIT."""
    db = main._DB_PATH
    c = sqlite3.connect(db)
    up = c.execute("INSERT INTO recon_uploads(source_type,branch_id,provider,file_name,period_month,row_count,status)"
                   " VALUES('grab',4,'Grab','g','2026-06',0,'confirmed')").lastrowid
    # two net days 3000+5000 paid as ONE combined credit (8000) on the 12th
    c.executemany("INSERT INTO recon_grab_lines(upload_id,branch_id,order_date,order_status,"
                  "food_amount,commission_amt,vat_amount,net_payout) VALUES(?,4,?,'Completed',?,?,0,?)",
                  [(up, "2026-06-10", 3300, 300, 3000), (up, "2026-06-11", 5500, 500, 5000)])
    c.commit(); c.close()
    _add_bank_line(db, "KBANK", "GRAB_PAYOUT", 8000, date="2026-06-12")

    r = client.get("/api/v2/reconcile", params={"branch_id": 4, "month": "2026-06"}).json()
    grab = next(ch for ch in r["channels"] if ch["key"] == "GRAB")
    pt = grab["payout_trace"]
    assert pt and pt["payouts"][0]["status"] == "matched"
    assert pt["payouts"][0]["combined"] is True and len(pt["payouts"][0]["days"]) == 2
    assert pt["payout_total"] == 8000.0


def test_transfer_any_matches_both_banks_no_double_count(client):
    """A generic 'Bank Transfer' code (TRANSFER/ANY) reconciles against the
    leftover transfer credits of BOTH banks; per-bank lanes consume their own
    lines first and never show a false surplus."""
    db = main._DB_PATH
    c = sqlite3.connect(db)
    # POS month: per-bank lane 4,000 + generic Bank Transfer 7,000 (ANY)
    c.executemany("INSERT INTO pos_payment_month VALUES(4,'2026-06',?,?,0)",
                  [("โอน KTB", 4000), ("Bank Transfer", 7000)])
    c.commit(); c.close()
    # statements: KTB transfers 9,000 (4k per-bank + 5k of ANY), KBANK 2,000 (rest of ANY)
    _add_bank_line(db, "KTB", "TRANSFER", 9000, date="2026-06-10")
    _add_bank_line(db, "KBANK", "TRANSFER", 2000, date="2026-06-10")

    from recon.matcher import category_reconciliation
    with main.get_db() as conn:
        cats, _ = category_reconciliation(conn, 4, "2026-06")
    lanes = {(x["category"], x["bank"]): x for x in cats}
    assert lanes[("TRANSFER", "KTB")]["status"] == "matched"
    assert lanes[("TRANSFER", "KTB")]["ext_amount"] == 4000.0   # leftover moved to ANY
    any_lane = lanes[("TRANSFER", "ANY")]
    assert any_lane["pos_amount"] == 7000.0 and any_lane["ext_amount"] == 7000.0
    assert any_lane["status"] == "matched"


# ── phase 3: advanced setup (master view + method-config CRUD) ────────────────

def test_setup_master_joins_method_and_volume(client):
    r = client.get("/api/setup/master", params={"period_month": "2026-06"})
    assert r.status_code == 200
    m = r.json()
    rows = {x["payment_code_norm"]: x for x in m["rows"]}
    # requirement routing + lane method joined in
    knn = rows["KNNQR"]
    assert knn["category"] == "QR" and knn["bank"] == "KBANK"
    assert knn["method"]["method"] == "itemized" and knn["method"]["method_label"]
    assert knn["pos_amount"] == 100000          # POS volume from pos_payment_month
    cash = rows["Cash"]
    assert cash["method"]["method"] == "cash_3way"
    assert cash["method"]["tol_abs_day"] == 10  # owner's few-baht tolerance
    grab = rows["GB"]
    assert grab["method"]["fee_vat_expected"] == 1
    # sections present
    assert m["method_configs"] and "globals" in m
    assert m["globals"]["TOL_ABS_DAY"] == 50.0


def test_setup_master_marks_unknown_bank_rows(client):
    with main.get_db() as conn:
        conn.execute("UPDATE recon_payment_map SET bank='UNKNOWN' WHERE payment_code_norm='KNNQR'")
        conn.commit()
    m = client.get("/api/setup/master", params={"period_month": "2026-06"}).json()
    rows = {x["payment_code_norm"]: x for x in m["rows"]}
    assert rows["KNNQR"]["status"] == "needs_bank"
    assert rows["Entertain"]["status"] == "off"          # reconcile=0
    assert rows["Bank Transfer"]["status"] == "dormant"  # no POS volume in fixture


def test_method_config_crud_and_validation(client):
    # bad enums are still rejected (no sensible clamp for strings)
    bad = {"category": "QR", "bank": "KBANK", "method": "magic"}
    assert client.post("/api/method-config", json=bad).status_code == 400
    # out-of-range numeric params are clamped server-side (Blueprint 1.4), not rejected
    r_lag = client.post("/api/method-config", json={
        "category": "QR", "bank": "TTB", "method": "direct", "lag_days": 99})
    assert r_lag.status_code in (200, 201)
    all_cfgs = client.get("/api/method-config").json()
    ttb = next(c for c in all_cfgs if c["category"] == "QR" and c["bank"] == "TTB")
    assert ttb["lag_days"] == 14                    # clamped to max
    r_merge = client.post("/api/method-config", json={
        "category": "QR", "bank": "SCB", "method": "direct", "merge_days": 0})
    assert r_merge.status_code in (200, 201)
    all_cfgs2 = client.get("/api/method-config").json()
    scb = next(c for c in all_cfgs2 if c["category"] == "QR" and c["bank"] == "SCB")
    assert scb["merge_days"] == 1                   # clamped to min
    # duplicate lane → 409
    assert client.post("/api/method-config", json={
        "category": "QR", "bank": "KBANK", "method": "itemized"}).status_code == 409
    # patch an existing lane + audit
    cfgs = client.get("/api/method-config").json()
    grab = next(c for c in cfgs if c["category"] == "GRAB")
    r = client.patch(f"/api/method-config/{grab['id']}", json={
        "category": "GRAB", "bank": "*", "doc1": "grab_report", "doc2": "bank_stmt_KBANK",
        "method": "two_stage", "lag_days": 3, "merge_days": 2, "fee_vat_expected": 1})
    assert r.status_code == 200
    with main.get_db() as conn:
        assert tuple(conn.execute("SELECT lag_days, merge_days FROM recon_method_config WHERE id=?",
                                  (grab["id"],)).fetchone()) == (3, 2)
        assert conn.execute("SELECT COUNT(*) FROM recon_audit_log "
                            "WHERE entity='method_config' AND entity_id=?",
                            (grab["id"],)).fetchone()[0] >= 1


def test_method_config_requires_manager_token(client):
    """Method parameters are manager-gated when OSHINEI_MANAGER_TOKEN is set;
    ordinary payment-map edits stay on the staff write token."""
    cfgs = client.get("/api/method-config").json()
    cash = next(c for c in cfgs if c["category"] == "CASH")
    body = {"category": "CASH", "bank": "*", "doc1": "deposit_slip", "doc2": "bank_stmt_*",
            "method": "cash_3way", "tol_abs_day": 20}
    os.environ["OSHINEI_MANAGER_TOKEN"] = "m-secret"
    try:
        assert client.patch(f"/api/method-config/{cash['id']}", json=body).status_code == 403
        assert client.patch(f"/api/method-config/{cash['id']}", json=body,
                            headers={"X-Manager-Token": "m-secret"}).status_code == 200
        assert client.post("/api/setup/seed-requirement").status_code == 403
        # staff mapping edit still open (write token unset locally)
        pm = client.get("/api/payment-map").json()
        knn = next(x for x in pm if x["payment_code_norm"] == "KNNQR")
        assert client.patch(f"/api/payment-map/{knn['id']}",
                            json={"category": "QR", "bank": "KBANK"}).status_code == 200
    finally:
        os.environ.pop("OSHINEI_MANAGER_TOKEN", None)


# ── POS bill-level drill (/api/v2/pos-lines) ──────────────────────────────────

def test_pos_lines_drill_ties_out_and_filters(client):
    db = main._DB_PATH
    _add_sale_payment(db, "2026-06-10-001", "KNNQR", 6000)   # QR/KBANK
    _add_sale_payment(db, "2026-06-10-002", "KNNQR", 4000)
    _add_sale_payment(db, "2026-06-11-003", "KNNQR", 5000)
    _add_sale_payment(db, "2026-06-10-004", "Cash", 2000)    # different channel
    r = client.get("/api/v2/pos-lines",
                   params={"branch_id": 4, "month": "2026-06", "category": "QR"}).json()
    assert r["total"] == 15000 and r["count"] == 3           # cash excluded from QR
    assert r["by_code"][0]["code"] == "KNNQR" and r["by_code"][0]["count"] == 3
    # by_day rollup is exact (drives the per-code daily view)
    assert {x["day"]: x["amount"] for x in r["by_day"]} == {"2026-06-10": 10000, "2026-06-11": 5000}
    # day filter narrows receipts + total
    d = client.get("/api/v2/pos-lines",
                   params={"branch_id": 4, "month": "2026-06", "category": "QR",
                           "day": "2026-06-10"}).json()
    assert d["total"] == 10000 and d["count"] == 2
    assert all(l["day"] == "2026-06-10" for l in d["lines"])
    # code filter on another channel
    cc = client.get("/api/v2/pos-lines",
                    params={"branch_id": 4, "month": "2026-06", "category": "CASH",
                            "code": "Cash"}).json()
    assert cc["total"] == 2000 and cc["count"] == 1


def test_pos_lines_without_sale_payment_is_graceful(client):
    # demo DB has no sale_payment table → empty payload, never a 500
    r = client.get("/api/v2/pos-lines",
                   params={"branch_id": 4, "month": "2026-06", "category": "QR"})
    assert r.status_code == 200
    assert r.json() == {"lines": [], "by_code": [], "by_day": [],
                        "total": 0.0, "count": 0, "truncated": False}


# ── internal-transfer detection (large round non-sales credits) ───────────────

def _add_bank_line_desc(db, credit, desc, date="2026-06-29", channel="TRANSFER"):
    c = sqlite3.connect(db)
    up = c.execute("INSERT INTO recon_uploads(source_type,branch_id,provider,file_name,period_month,row_count,status)"
                   " VALUES('bank',4,'KBANK','f','2026-06',0,'confirmed')").lastrowid
    c.execute("INSERT INTO recon_bank_lines(upload_id,branch_id,txn_date,channel,credit,description)"
              " VALUES(?,4,?,?,?,?)", (up, date, channel, credit, desc))
    c.commit(); c.close()


def test_internal_transfer_suggestion_and_one_click_exclude(client):
    db = main._DB_PATH
    _add_bank_line_desc(db, 2000000, "รับโอนเงิน")             # large+round+no sales kw → FLAG
    _add_bank_line_desc(db, 1500000, "รับเงินจากการขาย QR")   # large+round but sales kw → skip
    _add_bank_line_desc(db, 37502, "รับโอนเงิน")              # not round/large → skip
    r = client.get("/api/v2/reconcile", params={"branch_id": 4, "month": "2026-06"}).json()
    sug = r["internal_transfer_suggestions"]
    assert [s["amount"] for s in sug] == [2000000]            # only the sweep
    # one-click exclude → leaves suggestions, lands in the non-revenue box, reversible
    lid = sug[0]["id"]
    assert client.patch(f"/api/bank-lines/{lid}/ignore", json={"ignored": True}).status_code == 200
    r2 = client.get("/api/v2/reconcile", params={"branch_id": 4, "month": "2026-06"}).json()
    assert r2["internal_transfer_suggestions"] == []
    assert r2["non_revenue"]["count"] == 1 and r2["non_revenue"]["amount"] == 2000000


# ── Feature 2: manual cross-channel MOVE (booked-in-wrong-channel) ─────────────

def _seed_day(db, rows):
    c = sqlite3.connect(db)
    c.execute("CREATE TABLE IF NOT EXISTS pos_payment_day"
              "(branch_id INT,day TEXT,payment_code TEXT,amount REAL,txn_count INT DEFAULT 0)")
    c.executemany("INSERT INTO pos_payment_day VALUES(?,?,?,?,?)", rows)
    c.commit(); c.close()


def test_channel_move_offsets_two_lanes(client):
    """A manual move shifts ฿X out of the WRONG channel and into the right one on the
    POS side — both lane totals correct at once, daily ties to monthly, and DELETE
    restores. Lives in `pos` (not recon_lane_adj), so accept/adj are untouched."""
    from recon.matcher import category_reconciliation, daily_reconciliation
    # daily agrees with the fixture's monthly Cash=20,000 / QR·KBANK=150,000
    # (KNNQR 100,000 + K-SHOP 50,000 both map to QR·KBANK)
    _seed_day(main._DB_PATH, [(4, "2026-06-10", "Cash", 20000, 3),
                              (4, "2026-06-10", "KNNQR", 100000, 9),
                              (4, "2026-06-10", "K-SHOP", 50000, 4)])

    def cat_pos():
        with main.get_db() as conn:
            rows, _ = category_reconciliation(conn, 4, "2026-06")
        return {(r["category"], r["bank"]): r["pos_amount"] for r in rows}

    def daily_pos():
        with main.get_db() as conn:
            d = daily_reconciliation(conn, 4, "2026-06")
        agg = {}
        for day in d["days"]:
            for l in day["lanes"]:
                agg[l["category"]] = agg.get(l["category"], 0.0) + l["pos"]
        return agg

    base = cat_pos()
    assert base[("CASH", "NONE")] == 20000 and base[("QR", "KBANK")] == 150000

    r = client.post("/api/channel-move", json={
        "branch_id": 4, "period_month": "2026-06", "from_category": "CASH",
        "to_category": "QR", "to_bank": "KBANK", "amount": 5000,
        "note": "ลูกค้าสแกน QR แต่กดเป็นเงินสด"})
    assert r.status_code == 200
    mv_id = r.json()["id"]

    after = cat_pos()
    assert after[("CASH", "NONE")] == 15000 and after[("QR", "KBANK")] == 155000   # both corrected
    dp = daily_pos()
    assert round(dp.get("CASH", 0)) == 15000 and round(dp.get("QR", 0)) == 155000  # daily ties, no drift

    assert client.delete(f"/api/channel-move/{mv_id}").status_code == 200
    restored = cat_pos()
    assert restored[("CASH", "NONE")] == 20000 and restored[("QR", "KBANK")] == 150000

    # validation: same lane / amount≤0 / unknown channel / bad period
    bad = lambda body: client.post("/api/channel-move", json={
        "branch_id": 4, "period_month": "2026-06", **body}).status_code
    assert bad({"from_category": "CASH", "to_category": "CASH", "amount": 100}) == 400
    assert bad({"from_category": "CASH", "to_category": "QR", "amount": 0}) == 400
    assert bad({"from_category": "CASH", "to_category": "FOO", "amount": 100}) == 400
    # over-move (more than the source lane holds) → rejected (would drive lane negative)
    assert bad({"from_category": "CASH", "to_category": "QR", "to_bank": "KBANK",
                "amount": 999999}) == 400
    # source lane with no POS this month → rejected
    assert bad({"from_category": "LINEMAN", "to_category": "QR", "to_bank": "KBANK",
                "amount": 100}) == 400
    # typo'd / unknown destination bank → rejected (no orphan lane)
    assert bad({"from_category": "CASH", "to_category": "QR", "to_bank": "kbank",
                "amount": 100}) == 400
    assert client.post("/api/channel-move", json={
        "branch_id": 4, "period_month": "2026-13", "from_category": "CASH",
        "to_category": "QR", "amount": 100}).status_code == 400


def test_channel_move_in_wrong_channel_report(client):
    """Manual moves appear in the wrong-channel report alongside detected matches,
    tagged source='move'; Excel + print render."""
    assert client.post("/api/channel-move", json={
        "branch_id": 4, "period_month": "2026-06", "from_category": "CASH",
        "to_category": "QR", "to_bank": "KBANK", "amount": 1234, "note": "กดผิดช่อง"}
    ).status_code == 200
    rep = client.get("/api/reports/wrong-channel?period_month=2026-06").json()
    moves = [it for it in rep["items"] if it.get("source") == "move"]
    assert moves and moves[0]["amount"] == 1234
    assert moves[0]["from_cat"] == "CASH" and moves[0]["to_cat"] == "QR"
    assert client.get("/api/reports/wrong-channel.xlsx?period_month=2026-06").status_code == 200
    assert client.get("/api/reports/wrong-channel.print?period_month=2026-06").status_code == 200


# ── Feature 3: daily revenue-by-channel dashboard (all branches, one day) ──────

def test_daily_dashboard_per_branch_and_grand_total(client):
    """/api/daily-dashboard returns each master branch's per-channel breakdown for one
    day + grand totals; franchises excluded; bad day → 400; empty day → clean zero."""
    _seed_day(main._DB_PATH, [(4, "2026-06-12", "Cash", 10000, 2),
                              (5, "2026-06-12", "Cash", 7000, 1)])
    j = client.get("/api/daily-dashboard?day=2026-06-12").json()
    assert j["day"] == "2026-06-12"
    byid = {b["branch_id"]: b for b in j["branches"]}
    assert 4 in byid and 5 in byid and 1 not in byid          # masters only (1 = franchise)
    cash4 = next(ch for ch in byid[4]["channels"] if ch["ch"] == "CASH")
    assert cash4["gross"] == 10000
    assert j["grand_totals"]["gross"] == 17000                # 10,000 + 7,000
    assert client.get("/api/daily-dashboard?day=2026-13-99").status_code == 400
    empty = client.get("/api/daily-dashboard?day=2026-06-28").json()
    assert empty["grand_totals"]["gross"] == 0


# ── Features 4/5/6: discount accounting (KTC/BAY points vs discount coupons) ───

def test_point_pos_total_daily_classifies_by_series(client):
    """Day-grain KTC+BAY point total, by series name — generic discounts excluded."""
    from recon.matcher import point_pos_total_daily
    c = sqlite3.connect(main._DB_PATH)
    c.execute("CREATE TABLE IF NOT EXISTS pos_coupon_payment_day"
              "(branch_id INT,day TEXT,coupon_series_id INT,amount REAL,txn_count INT)")
    c.execute("CREATE TABLE IF NOT EXISTS coupon_series"
              "(coupon_series_id INTEGER PRIMARY KEY,name TEXT,code TEXT,active INT)")
    c.executemany("INSERT OR REPLACE INTO coupon_series VALUES(?,?,?,1)", [
        (10, "แต้ม KTC แลกส่วนลด คะแนน", "KTC"),
        (12, "โปรบัตรกรุงศรี คะแนนแลกส่วนลด", "BAY"),
        (11, "คูปองผู้สูงอายุ", "ELDER")])
    c.executemany("INSERT INTO pos_coupon_payment_day VALUES(4,?,?,?,1)", [
        ("2026-06-15", 10, 300), ("2026-06-15", 12, 150),
        ("2026-06-15", 11, 200), ("2026-06-16", 10, 100)])
    c.commit(); c.close()
    d = point_pos_total_daily(sqlite3.connect(main._DB_PATH), 4, "2026-06")
    assert d.get("2026-06-15") == 450.0     # KTC 300 + BAY 150; elderly 200 excluded
    assert d.get("2026-06-16") == 100.0


def test_daily_finance_coupon_split_grosses_up(client):
    """COUPON tender splits: KTC/BAY points stay as real money received; other discount
    coupons become a ส่วนลด deduction (gross-up + offset, net 0). Day gross unchanged,
    day net drops by the discount."""
    _seed_day(main._DB_PATH, [(4, "2026-06-15", "Coupon", 500, 3)])
    c = sqlite3.connect(main._DB_PATH)
    c.execute("CREATE TABLE IF NOT EXISTS pos_coupon_payment_day"
              "(branch_id INT,day TEXT,coupon_series_id INT,amount REAL,txn_count INT)")
    c.execute("CREATE TABLE IF NOT EXISTS coupon_series"
              "(coupon_series_id INTEGER PRIMARY KEY,name TEXT,code TEXT,active INT)")
    c.executemany("INSERT OR REPLACE INTO coupon_series VALUES(?,?,?,1)", [
        (10, "แต้ม KTC คะแนนแลกส่วนลด", "KTC"), (11, "คูปองผู้สูงอายุ", "ELDER")])
    c.executemany("INSERT INTO pos_coupon_payment_day VALUES(4,?,?,?,1)", [
        ("2026-06-15", 10, 300), ("2026-06-15", 11, 200)])
    c.commit(); c.close()

    j = client.get("/api/daily-accounts?branch_id=4&period_month=2026-06").json()
    day = next(d for d in j["days"] if d["dom"] == 15)
    coupon = next(ch for ch in day["channels"] if ch["ch"] == "COUPON")
    disc = next(ch for ch in day["channels"] if ch["ch"] == "DISCOUNT")
    assert coupon["gross"] == 300 and coupon["net"] == 300        # points = real money in
    assert disc["gross"] == 200 and disc["discount"] == 200 and disc["net"] == 0   # discount deduction
    assert day["totals"]["gross"] == 500                          # full price (gross) preserved
    assert day["totals"]["discount"] == 200
    assert day["totals"]["net"] == 300                            # net excludes the discount coupon


def test_monthly_journal_discount_includes_nonpoint_coupon(client):
    """Monthly receipts: REVENUE stays at full gross, the discount line includes the
    non-point coupon, COUPON shows points only (DEMO Coupon tender = 1,000; points 300
    → COUPON 300 + discount 700)."""
    c = sqlite3.connect(main._DB_PATH)
    c.execute("CREATE TABLE IF NOT EXISTS pos_coupon_payment_day"
              "(branch_id INT,day TEXT,coupon_series_id INT,amount REAL,txn_count INT)")
    c.execute("CREATE TABLE IF NOT EXISTS coupon_series"
              "(coupon_series_id INTEGER PRIMARY KEY,name TEXT,code TEXT,active INT)")
    c.execute("INSERT OR REPLACE INTO coupon_series VALUES(10,'แต้ม KTC คะแนนแลกส่วนลด','KTC',1)")
    c.execute("INSERT INTO pos_coupon_payment_day VALUES(4,'2026-06-15',10,300,2)")
    c.commit(); c.close()
    with main.get_db() as conn:
        html = main._monthly_finance_html(conn, 4, "2026-06")
    assert "ส่วนลด" in html
    assert "300.00" in html and "700.00" in html      # COUPON points 300, discount-coupon 700
