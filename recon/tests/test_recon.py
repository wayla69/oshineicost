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
                  [(4, "Korat1", "master", 1), (1, "Chonburi", "franchise", 1)])
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
    ("KNNQR", "QR", "UNKNOWN"), (" KNNQR", "QR", "UNKNOWN"), ("K-SHOP", "QR", "KBANK"),
    ("QR code K-bank", "QR", "KBANK"), ("QR PromptPay", "QR", "PROMPTPAY"),
    ("Credit กรุงไทย", "CREDIT", "KTB"), ("Credit กสิกรไทย", "CREDIT", "KBANK"),
    ("Credit KTC", "CREDIT", "KTC"), ("Cash", "CASH", "NONE"), ("CASH", "CASH", "NONE"),
    ("โอน KTB", "TRANSFER", "KTB"), ("โอน Kbank", "TRANSFER", "KBANK"),
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
    assert next(r for r in rows if r["payment_code_norm"] == "KNNQR")["bank"] == "UNKNOWN"
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
    db = _make_db(tmp_path, [("KNNQR", 5000)])     # seeds as QR/UNKNOWN
    c = sqlite3.connect(db)
    c.execute("CREATE TABLE pos_payment_day(branch_id INT,day TEXT,payment_code TEXT,amount REAL,txn_count INT)")
    c.execute("INSERT INTO pos_payment_day VALUES(4,'2026-06-01','KNNQR',5000,0)")
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


def test_write_token_gate(client):
    main.os.environ["OSHINEI_SEED_TOKEN"] = "tok"
    try:
        assert client.post("/api/bank-accounts", json={"branch_id": 4, "bank": "KTB", "account_no": "1"}).status_code == 403
        ok = client.post("/api/bank-accounts", headers={"X-Upload-Token": "tok"},
                         json={"branch_id": 4, "bank": "KTB", "account_no": "1"})
        assert ok.status_code == 200
    finally:
        main.os.environ.pop("OSHINEI_SEED_TOKEN", None)
