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

    # The suggestion is itself a valid /api/manual payload → applying it succeeds.
    r = client.post("/api/manual", json={
        "branch_id": s["branch_id"], "period_month": s["period_month"],
        "category": s["category"], "bank": s["bank"],
        "pos_days": s["pos_days"], "ext": s["ext"], "note": s["note"],
    })
    assert r.status_code == 200
    # the consumed line is now flagged, so the suggestion no longer reappears
    assert client.get("/api/suggestions?branch_id=4&period_month=2026-06").json()["suggestions"] == []


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
