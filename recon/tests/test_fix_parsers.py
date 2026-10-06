"""Regression tests for the parser / upload-endpoint fixes (fix/recon-parsers).

Each test pins one confirmed bug from the 2026-10 read-only review:
period locks + duplicates keyed on the rows' own months, the generic column
finder, provider case, bank auto-detect, commission rate vs amount, missing
columns reading column 0, owner-password PDFs, locked 'No Data' statements,
amount/date formats, attachment download/delete, and upload delete/reassign.
"""
import datetime as _dt
import io
import os
import sqlite3
import sys
from pathlib import Path

import openpyxl
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # repo root → import recon.*
import recon.main as main                                        # noqa: E402
import recon.core as core                                        # noqa: E402
import recon.routers.uploads as uploads                          # noqa: E402
from recon.parsers.bank import (parse_bank_statement, _to_float,  # noqa: E402
                                _parse_amount, _parse_thai_date)
from recon.parsers.credit_card import parse_cc_settlement        # noqa: E402
from recon.parsers.grab import parse_grab_report                 # noqa: E402
from recon.parsers.lineman import parse_lineman_report           # noqa: E402
from recon.parsers.loader import unlock_pdf, is_encrypted_upload  # noqa: E402


# ── helpers ───────────────────────────────────────────────────────────────────

def _xlsx(rows):
    wb = openpyxl.Workbook()
    ws = wb.active
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _make_db(tmp_path):
    db = tmp_path / "t.db"
    c = sqlite3.connect(db)
    c.execute("CREATE TABLE branches(branch_id INTEGER PRIMARY KEY,name TEXT,branch_type TEXT,active INTEGER)")
    c.executemany("INSERT INTO branches VALUES(?,?,?,?)",
                  [(4, "Korat1", "master", 1), (5, "อุดรธานี", "master", 1)])
    c.execute("CREATE TABLE pos_payment_month(branch_id INT,period_month TEXT,payment_code TEXT,amount REAL,txn_count INT)")
    c.commit(); c.close()
    return db


@pytest.fixture
def client(tmp_path, monkeypatch):
    core._DB_PATH = main._DB_PATH = _make_db(tmp_path)
    monkeypatch.setattr(uploads, "_ATTACH_DIR", tmp_path / "att")
    os.environ.pop("OSHINEI_SEED_TOKEN", None)
    with TestClient(main.app) as cl:
        yield cl


def _db():
    c = sqlite3.connect(main._DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def _lock(branch_id, period_month, status="approved"):
    c = _db()
    c.execute("INSERT INTO recon_sessions(branch_id,period_month,status) VALUES(?,?,?)",
              (branch_id, period_month, status))
    c.commit(); c.close()


def _csv(lines, title=""):
    head = (title + "\n") if title else ""
    body = "Date,Description,Withdrawal,Deposit,Balance\n" + "".join(l + "\n" for l in lines)
    return (head + body).encode()


def _confirm(cl, raw, period, name="stmt.csv", branch_id=4, provider="KBANK"):
    return cl.post("/api/uploads/bank/confirm", files={"file": (name, raw, "text/csv")},
                   data={"branch_id": branch_id, "period_month": period, "provider": provider})


MAY = ["01/05/2026,QR receive,,1000.00,2000.00",
       "02/05/2026,QR receive,,500.00,2500.00"]


# ── 1. period lock + duplicates follow the rows' own months ──────────────────

def test_upload_under_wrong_month_is_rejected(client):
    """May's statement picked under June used to bypass May's approved lock and
    double May's revenue (the matcher reads lines by their own txn_date)."""
    _lock(4, "2026-05")
    r = _confirm(client, _csv(MAY), "2026-06")
    assert r.status_code == 422
    assert "2026-05" in r.json()["detail"]
    c = _db()
    assert c.execute("SELECT COUNT(*) FROM recon_bank_lines").fetchone()[0] == 0
    c.close()


def test_card_settlement_paid_next_month_is_accepted_but_lock_checked(client):
    """A per-day card settlement for May 31 sales pays out on June 1 — it is
    uploaded with May's documents, so it is NOT rejected as 'wrong month';
    June (where its line counts) must still be open."""
    cc = _xlsx([["Settle Date", "Card", "Count", "Gross", "Refund", "Fee"],
                ["01/06/2026", "VISA", 2, 500.0, 0.0, 10.0]])
    form = {"branch_id": 4, "period_month": "2026-05"}
    files = {"file": ("settle.xlsx", cc)}
    _lock(4, "2026-06")
    assert client.post("/api/uploads/credit_card/confirm", files=files, data=form).status_code == 423
    c = _db(); c.execute("UPDATE recon_sessions SET status='in_review'"); c.commit(); c.close()
    r = client.post("/api/uploads/credit_card/confirm", files=files, data=form)
    assert r.status_code == 200 and r.json()["rows_saved"] == 1


def test_upload_touching_a_locked_month_is_refused(client):
    """A statement spanning a month boundary is legit, but rows dated in an
    approved month must not slip in under the open month."""
    _lock(4, "2026-05")
    raw = _csv(["31/05/2026,QR receive,,700.00,700.00",
                "01/06/2026,QR receive,,300.00,1000.00"])
    r = _confirm(client, raw, "2026-06")
    assert r.status_code == 423
    assert "2026-05" in r.json()["detail"]
    # open both months → the same spanning statement is accepted
    c = _db(); c.execute("UPDATE recon_sessions SET status='in_review'"); c.commit(); c.close()
    assert _confirm(client, raw, "2026-06").status_code == 200


def test_identical_file_is_a_duplicate_in_any_month(client):
    raw = _csv(["31/05/2026,QR receive,,700.00,700.00",
                "01/06/2026,QR receive,,300.00,1000.00"])
    assert _confirm(client, raw, "2026-05").status_code == 200
    r = _confirm(client, raw, "2026-06")               # same bytes, other month
    assert r.status_code == 409


def test_redownloaded_statement_skips_lines_already_present(client):
    """A re-downloaded statement (different bytes, same movements) must not
    double: lines with the same date/amount/balance are skipped and counted."""
    assert _confirm(client, _csv(MAY), "2026-05").status_code == 200
    longer = _csv(MAY + ["03/05/2026,QR receive,,250.00,2750.00"], title="KBank export v2")
    r = _confirm(client, longer, "2026-05", name="stmt2.csv")
    assert r.status_code == 200, r.text
    assert r.json()["rows_saved"] == 1 and r.json()["duplicate_lines"] == 2
    # every line already present → the whole document is a duplicate
    again = _confirm(client, _csv(MAY, title="KBank export v3"), "2026-05", name="stmt3.csv")
    assert again.status_code == 409
    c = _db()
    assert c.execute("SELECT COUNT(*), SUM(credit) FROM recon_bank_lines").fetchone()[:] == (3, 1750.0)
    c.close()


def test_lines_without_balance_are_never_deduped(client):
    """Without a running balance two genuine same-day same-amount QR receipts are
    indistinguishable from a re-upload — keep them (no false dedup)."""
    head = "Date,Description,Withdrawal,Deposit\n"
    a = (head + "01/05/2026,QR receive,,100.00\n").encode()
    b = (head + "01/05/2026,QR receive,,100.00\n02/05/2026,QR,,5.00\n").encode()
    assert _confirm(client, a, "2026-05", provider="").status_code == 200
    r = _confirm(client, b, "2026-05", name="b.csv", provider="")
    assert r.status_code == 200 and r.json()["rows_saved"] == 2


def _seed_upload(c, branch_id=4, period="2026-06", txn_date="2026-05-30",
                 file_hash=None, account_no=None, balance=None):
    up = c.execute("INSERT INTO recon_uploads(source_type,branch_id,provider,file_name,"
                   "period_month,row_count,file_hash,status) "
                   "VALUES('bank',?,'KBANK','s.pdf',?,1,?,'confirmed')",
                   (branch_id, period, file_hash)).lastrowid
    c.execute("INSERT INTO recon_bank_lines(upload_id,branch_id,account_no,txn_date,channel,credit,balance)"
              " VALUES(?,?,?,?,'QR',100,?)", (up, branch_id, account_no, txn_date, balance))
    c.commit()
    return up


def test_delete_checks_the_lock_of_every_line_month(client):
    c = _db(); up = _seed_upload(c); c.close()
    _lock(4, "2026-05")                                 # lines are in May
    assert client.delete(f"/api/uploads/{up}").status_code == 423


def test_reassign_checks_line_months_duplicates_and_owner(client):
    c = _db()
    up = _seed_upload(c, txn_date="2026-05-30", file_hash="h1")
    c.close()
    _lock(5, "2026-05")                                 # target branch's May is closed
    assert client.patch(f"/api/uploads/{up}/branch", json={"branch_id": 5}).status_code == 423
    c = _db(); c.execute("DELETE FROM recon_sessions"); c.commit()
    # identical file already confirmed for the target branch → duplicate
    _seed_upload(c, branch_id=5, file_hash="h1")
    c.close()
    assert client.patch(f"/api/uploads/{up}/branch", json={"branch_id": 5}).status_code == 409
    # the statement's own account number says it belongs to branch 4
    c = _db()
    up2 = _seed_upload(c, txn_date="2026-06-02", account_no="111-1-11111-1")
    c.execute("INSERT INTO recon_bank_account(branch_id,bank,account_no,account_name,active)"
              " VALUES(4,'KBANK','111-1-11111-1','x',1)")
    c.commit(); c.close()
    r = client.patch(f"/api/uploads/{up2}/branch", json={"branch_id": 5})
    assert r.status_code == 409 and "Korat1" in r.json()["detail"]


# ── 12. delete of an unknown upload ──────────────────────────────────────────

def test_delete_unknown_upload_is_404_and_not_audited(client):
    assert client.delete("/api/uploads/987654").status_code == 404
    c = _db()
    assert c.execute("SELECT COUNT(*) FROM recon_audit_log WHERE entity='upload' "
                     "AND entity_id=987654").fetchone()[0] == 0
    c.close()


# ── 8. a password-locked 'No Data' statement can be confirmed ────────────────

def test_locked_empty_statement_confirms(client, monkeypatch):
    pytest.importorskip("reportlab")
    pikepdf = pytest.importorskip("pikepdf")
    from reportlab.pdfgen import canvas
    buf = io.BytesIO(); cv = canvas.Canvas(buf); cv.drawString(36, 800, "Statement No Data"); cv.save()
    src = pikepdf.open(io.BytesIO(buf.getvalue())); out = io.BytesIO()
    src.save(out, encryption=pikepdf.Encryption(user="0170570924", owner="0170570924"))
    raw = out.getvalue()
    monkeypatch.setenv("RECON_PDF_PASSWORDS", "0170570924")
    form = {"branch_id": 4, "period_month": "2026-05"}
    p = client.post("/api/uploads/bank/preview", files={"file": ("l.pdf", raw, "application/pdf")}, data=form)
    assert p.status_code == 200 and p.json()["empty_statement"] is True
    r = client.post("/api/uploads/bank/confirm", files={"file": ("l.pdf", raw, "application/pdf")}, data=form)
    assert r.status_code == 200 and r.json()["rows_saved"] == 0


# ── 10/11. evidence attachments ──────────────────────────────────────────────

def _attach(cl, name="สลิป ฝาก\"1\".jpg", day="2026-05-01"):
    r = cl.post("/api/attachments", files={"file": (name, b"\xff\xd8xx", "image/jpeg")},
                data={"branch_id": 4, "day": day})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def test_attachment_download_with_thai_filename(client):
    aid = _attach(client)
    g = client.get(f"/api/attachments/{aid}/file")
    assert g.status_code == 200
    cd = g.headers["content-disposition"]
    assert "filename*=UTF-8''" in cd and "\r" not in cd and "\n" not in cd
    assert cd.count('"') == 2                            # the name's own quotes are stripped


def test_attachment_delete_lock_audit_slip_and_404(client):
    assert client.delete("/api/attachments/99999").status_code == 404
    aid = _attach(client)
    c = _db()
    path = uploads._ATTACH_DIR / c.execute(
        "SELECT file_path FROM recon_attachment WHERE id=?", (aid,)).fetchone()[0]
    c.execute("INSERT INTO recon_deposit_slip(branch_id,sale_day,amount,attachment_id) "
              "VALUES(4,'2026-05-01',100,?)", (aid,))
    c.commit(); c.close()
    _lock(4, "2026-05")
    assert client.delete(f"/api/attachments/{aid}").status_code == 423
    assert path.exists()
    c = _db(); c.execute("UPDATE recon_sessions SET status='in_review'"); c.commit(); c.close()
    assert client.delete(f"/api/attachments/{aid}").status_code == 200
    assert not path.exists()
    c = _db()
    assert c.execute("SELECT attachment_id FROM recon_deposit_slip").fetchone()[0] is None
    diffs = {r["field"]: r["old_val"] for r in c.execute(
        "SELECT field, old_val FROM recon_audit_log WHERE entity='attachment' AND entity_id=?", (aid,))}
    assert diffs.get("branch_id") == "4" and diffs.get("day") == "2026-05-01"
    c.close()


# ── 2. generic column finder: one header row, not accumulated ────────────────

def test_generic_title_rows_do_not_hijack_columns():
    x = _xlsx([["ชื่อบัญชี", "บจ. โอชิเน"],
               ["ประเภทบัญชี", "เงินฝากออมทรัพย์"],          # 'ฝาก' in col B: not the credit column
               ["Date", "Description", "Withdrawal", "Deposit", "Balance"],
               ["01/05/2026", "QR รับเงิน", "", "1,000.00", "2,000.00"],
               ["02/05/2026", "ATM", "500.00", "", "1,500.00"]])
    prov, rows, sk = parse_bank_statement(x, "generic")
    assert sk == 0
    assert [(r["description"], r["debit"], r["credit"]) for r in rows] == [
        ("QR รับเงิน", 0.0, 1000.0), ("ATM", 500.0, 0.0)]
    y = _xlsx([["Statement date 31/05/2026"],
               ["Date", "Description", "Withdrawal", "Deposit", "Balance"],
               ["01/05/2026", "QR รับเงิน", "", "1,000.00", "2,000.00"]])
    prov, rows, sk = parse_bank_statement(y, "generic")
    assert sk == 0 and [(r["credit"], r["balance"]) for r in rows] == [(1000.0, 2000.0)]


# ── 3. provider case from the UI ─────────────────────────────────────────────

_KTB_HDR = ["วันที่/เวลา", "รายการ", "รายละเอียด", "หมายเลขเช็ค", "ถอนเงิน/ฝากเงิน", "ภาษี", "ยอดคงเหลือ", "ช่องทาง"]


def test_uppercase_provider_reaches_the_specific_parser():
    ktb = _xlsx([["ธนาคารกรุงไทย"], _KTB_HDR,
                 ["01/05/2569", "SDCH", "x", "", "5,000.00", "0", "15,000.00", "-"],
                 ["01/05/2569", "ถอน ATM", "x", "", "-2,000.00", "0", "13,000.00", "-"]])
    for p in ("KTB", "ktb"):
        prov, rows, _ = parse_bank_statement(ktb, p)
        assert prov == p                                  # stored value unchanged
        assert [(r["debit"], r["credit"]) for r in rows] == [(0.0, 5000.0), (2000.0, 0.0)]
        assert rows[0]["description"] == "SDCH"
    kb = _xlsx([["ธนาคารกสิกรไทย"], ["วันที่", "รายการ", "รายการถอน", "รายการฝาก", "ยอดคงเหลือ", "ช่องทาง"],
                ["01-พ.ค.-2569", "รับโอนเงิน", "", "1,000.00", "2,000.00", "K PLUS"]])
    for p in ("KBANK", "kbank", None):
        prov, rows, _ = parse_bank_statement(kb, p)
        assert [(r["description"], r["credit"]) for r in rows] == [("รับโอนเงิน", 1000.0)]


def test_now_reachable_specific_parsers_still_read_english_amount_headers():
    """Before the case fix every 'KBANK'/'BBL' upload ran the generic parser,
    which read 'Withdrawal'/'Deposit'. The specific parsers must too (or fall
    back to generic) — never save every deposit as 0.0."""
    x = _xlsx([["Date", "Description", "Withdrawal", "Deposit", "Balance"],
               ["01/05/2026", "QR", "", "1,000.00", "1,000.00"],
               ["02/05/2026", "ATM", "200.00", "", "800.00"]])
    for p in ("KBANK", "BBL", "SCB", "KTB"):
        rows = parse_bank_statement(x, p)[1]
        assert [(r["debit"], r["credit"]) for r in rows] == [(0.0, 1000.0), (200.0, 0.0)], p
    odd = _xlsx([["Date", "Description", "Money In", "Balance"],     # no known credit header
                 ["01/05/2026", "QR", "5.00", "5.00"]])
    assert parse_bank_statement(odd, "KBANK")[1][0]["txn_date"] == "2026-05-01"


def test_cc_uppercase_provider_routes_and_is_kept():
    data = _xlsx([["Settlement Date", "Terminal ID", "Merchant ID", "Card Type",
                   "Transaction Count", "Sale Amount", "Refund Amount", "MDR Rate",
                   "MDR Fee", "Net Amount", "Batch"],
                  ["01/05/2569", "T001", "M123", "VISA", 5, 1000.0, 0.0, 0.021, 21.0, 979.0, "B1"]])
    prov, rows, _ = parse_cc_settlement(data, "KBANK")
    assert prov == "KBANK"
    assert rows[0]["merchant_id"] == "M123" and rows[0]["batch_no"] == "B1"


def test_ttb_provider_does_not_crash():
    x = _xlsx([["Date", "Description", "Debit", "Credit", "Balance"],
               ["01/05/2026", "QR", "", "10.00", "10.00"]])
    prov, rows, _ = parse_bank_statement(x, "TTB")
    assert prov == "TTB" and rows[0]["credit"] == 10.0


# ── 4. bank auto-detection reads the header block only ───────────────────────

def test_autodetect_ktb_title_mentioning_bangkok():
    x = _xlsx([["ธนาคารกรุงไทย จำกัด (มหาชน) สำนักงานใหญ่ กรุงเทพฯ"], _KTB_HDR,
               ["01/05/2569", "SDCH", "", "", "5,000.00", "0", "15,000.00", "-"]])
    prov, rows, _ = parse_bank_statement(x, None)
    assert prov == "ktb" and rows[0]["credit"] == 5000.0


def test_autodetect_ignores_bank_names_in_transactions():
    x = _xlsx([["ธนาคารกรุงไทย"], _KTB_HDR,
               ["01/05/2569", "IORSDT", "โอนจาก KBANK", "", "5,000.00", "0", "15,000.00", "-"]])
    prov, rows, _ = parse_bank_statement(x, None)
    assert prov == "ktb"
    y = _xlsx([["Bangkok Bank Public Company Limited"],
               ["Date", "Description", "Debit", "Credit", "Balance"],
               ["01/05/2026", "transfer", "", "10.00", "10.00"]])
    assert parse_bank_statement(y, None)[0] == "bbl"


# ── 5. commission RATE is not the commission AMOUNT ──────────────────────────

def test_grab_rate_column_before_amount():
    g = _xlsx([["วันที่สั่งซื้อ", "หมายเลขคำสั่งซื้อ", "สถานะ", "ราคาอาหาร", "อัตราค่าคอมมิชชั่น", "ค่าคอมมิชชั่น", "ยอดสุทธิ"],
               ["2026-05-01", "GF-1", "Completed", 1000, 0.3, 300, 700]])
    rows, _ = parse_grab_report(g)
    assert (rows[0]["commission_rate"], rows[0]["commission_amt"]) == (0.3, 300.0)


def test_lineman_rate_column_before_amount():
    lm = _xlsx([["Order Date", "Order ID", "Status", "Food Amount", "Commission Rate",
                 "Commission Amount", "VAT", "Net Amount"],
                ["2026-05-01", "LM-1", "Completed", 1000, 30, 300, 21, 679]])
    rows, _ = parse_lineman_report(lm)
    r = rows[0]
    assert (r["commission_rate"], r["commission_amt"], r["vat_on_commission"]) == (30.0, 300.0, 21.0)


# ── 6. a missing column reads as empty, never as column 0 ────────────────────

def test_cc_missing_columns_do_not_read_column_zero():
    cc = _xlsx([["No.", "Settlement Date", "Card Type", "Sale Amount", "MDR Fee", "Net Amount"],
                [7, "01/05/2026", "VISA", 1000, 15, 985]])
    prov, rows, _ = parse_cc_settlement(cc, "KTB")
    r = rows[0]
    assert (r["refund_amount"], r["txn_count"], r["gross_amount"]) == (0.0, 0, 1000.0)
    assert r["terminal_id"] == ""
    km = _xlsx([["KASIKORNBANK"], ["No.", "Settlement Date", "Sale Amount", "Net Amount"],
                [7, "01/05/2026", 1000, 985]])
    prov, rows, _ = parse_cc_settlement(km, "kbank")
    r = rows[0]
    assert (r["refund_amount"], r["mdr_fee"], r["txn_count"], r["merchant_id"]) == (0.0, 0.0, 0, "")
    # no net column → still derived from gross − refund − fee
    gn = _xlsx([["No.", "Settle Date", "Gross", "Fee"], [7, "01/05/2569", 500.0, 10.0]])
    assert parse_cc_settlement(gn)[1][0]["net_amount"] == 490.0


def test_grab_and_lineman_missing_columns_do_not_read_column_zero():
    g = _xlsx([["No.", "Order Date", "Order ID", "Food Amount", "Net Payout"],
               [7, "01/05/2026", "GF-1", 100, 70]])
    r = parse_grab_report(g)[0][0]
    assert (r["commission_amt"], r["promo_deduct"], r["commission_rate"]) == (0.0, 0.0, 0.0)
    assert r["order_type"] == "GrabFood" and r["merchant_name"] == "" and r["payout_date"] is None
    g2 = _xlsx([["No.", "Order Date", "Order ID", "Food Amount", "Commission Amount"],
                [7, "01/05/2026", "GF-1", 100, 30]])
    assert parse_grab_report(g2)[0][0]["net_payout"] == 70.0      # computed fallback kept
    lm = _xlsx([["No.", "วันที่สั่งอาหาร", "หมายเลขคำสั่งซื้อ", "ราคาอาหาร", "ค่าคอมมิชชั่น"],
                [7, "01/05/2026", "LM-1", 100, 30]])
    r = parse_lineman_report(lm)[0][0]
    assert (r["delivery_fee"], r["vat_on_commission"], r["promo_deduct"]) == (0.0, 0.0, 0.0)
    assert r["net_payout"] == 70.0 and r["payout_date"] is None


# ── 7. owner-password-only PDF opens without a password ──────────────────────

def test_unlock_owner_password_only_pdf():
    pikepdf = pytest.importorskip("pikepdf")
    pdf = pikepdf.new(); pdf.add_blank_page()
    b = io.BytesIO()
    pdf.save(b, encryption=pikepdf.Encryption(owner="ownerpw", user="",
                                              allow=pikepdf.Permissions(extract=False)))
    raw = b.getvalue()
    assert is_encrypted_upload(raw, "x.pdf")
    out = unlock_pdf(raw, [])
    assert out is not None and b"/Encrypt" not in out[-3072:]
    # a user-password PDF with no fitting password still fails
    b2 = io.BytesIO()
    pikepdf.new().save(b2, encryption=pikepdf.Encryption(owner="o", user="u"))
    assert unlock_pdf(b2.getvalue(), ["wrong"]) is None


# ── 9. amount formats ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,expected", [
    ("(1,234.50)", -1234.5), ("1,234.50-", -1234.5), ("฿1,000.00", 1000.0),
    ("1 000.00", 1000.0), ("1 000.00", 1000.0), ("-500", -500.0), ("+5,000.00", 5000.0),
    ("-32.10", -32.1), (12.5, 12.5), ("", 0.0), (None, 0.0), ("-", 0.0), ("abc", 0.0),
])
def test_to_float_formats(raw, expected):
    assert _to_float(raw) == expected


def test_parse_amount_flags_genuine_failures():
    assert _parse_amount("abc") is None
    assert _parse_amount("") == 0.0 and _parse_amount("-") == 0.0
    assert _parse_amount("(10.00)") == -10.0


# ── 13. Buddhist-era dates ────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,expected", [
    (_dt.datetime(2569, 5, 1), "2026-05-01"),
    (_dt.date(2569, 5, 1), "2026-05-01"),
    (_dt.datetime(2026, 5, 1, 10, 30), "2026-05-01"),
    ("01/05/69", "2026-05-01"),          # 2-digit BE (≥ 58 → 25YY)
    ("01-05-69", "2026-05-01"),
    ("01-พ.ค.-69", "2026-05-01"),
    ("01/05/26", "2026-05-01"),          # 2-digit CE (< 58 → 20YY)
    ("01-พ.ค.-2569", "2026-05-01"),
    ("01/05/2569", "2026-05-01"),
    ("05/13/2026", None),
])
def test_parse_thai_date_be_and_two_digit_years(raw, expected):
    assert _parse_thai_date(raw) == expected


def test_excel_datetime_cell_with_be_year_is_converted():
    x = _xlsx([["Date", "Description", "Withdrawal", "Deposit", "Balance"],
               [_dt.datetime(2569, 5, 1), "QR", "", 100, 100]])
    prov, rows, sk = parse_bank_statement(x, "generic")
    assert sk == 0 and rows[0]["txn_date"] == "2026-05-01"
