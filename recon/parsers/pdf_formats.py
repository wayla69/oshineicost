"""
Format-specific PDF extractors for Thai bank / acquirer / delivery documents.

The generic loader (loader.py) turns any PDF into a cell grid, which the column-
sniffing domain parsers can read when a document is a simple one-row-per-txn
table. The real Udon documents are not: bank statements carry a single signed
amount column (direction implied by the running balance), acquirer settlements
are summary sheets whose date lives in a header field, KTB embeds a CordiaNew
font subset whose digits decode to '(cid:NNN)', and Grab ships a narrative daily
report. Each needs bespoke handling, collected here.

Each extractor returns rows shaped for the matching recon_* line table (the same
dicts the openpyxl parsers produce); main.py fills upload_id / branch_id.

Entry point:
    detect_and_parse(source_type, raw) -> (provider, rows, skipped) | None
returns None when the PDF is not a recognised special format, so the caller can
fall back to the generic grid loader.
"""

from __future__ import annotations

import io
import re
from typing import Any

from .bank import _parse_thai_date, _normalise_channel, _to_float


# ── CordiaNew cid → digit remap ───────────────────────────────────────────────
# KTB statements embed a CordiaNew subset with no ToUnicode map, so pdfplumber
# renders digits as the literal glyph id "(cid:NNN)". The subset is consistent:
# cid 344..353 == digits 0..9 (verified against the postal code "41000" and the
# "page 1/7" footer). Thai letters in the same font DO carry a unicode mapping,
# so only the digit run needs fixing.
_CID_RE = re.compile(r"\(cid:(\d+)\)")


def _decid(s: str) -> str:
    """Replace '(cid:344..353)' with the digit it encodes; drop any other cid."""
    def repl(m: re.Match) -> str:
        n = int(m.group(1))
        return str(n - 344) if 344 <= n <= 353 else ""
    return _CID_RE.sub(repl, s or "")


def _open(raw: bytes):
    import pdfplumber
    return pdfplumber.open(io.BytesIO(raw))


# Bank account number on a statement header, e.g. "เลขที่บัญชีเงินฝาก 057-3-49826-7"
# / (KTB) "เลขทีบัญชี 017-0-57092-4". Used to auto-attribute the upload to a branch
# via recon_bank_account.
_ACCT_RE = re.compile(r"บัญชี[^\d]{0,14}(\d{3}-\d-\d{4,6}-\d)")


def _extract_account(txt: str) -> str:
    m = _ACCT_RE.search(txt or "")
    return m.group(1) if m else ""


def _full_text(raw: bytes, decid: bool = False) -> str:
    with _open(raw) as pdf:
        parts = [(p.extract_text() or "") for p in pdf.pages]
    txt = "\n".join(parts)
    return _decid(txt) if decid else txt


# ── Card-type detection (shared by acquirer settlements) ─────────────────────

def _detect_card_type(s: str) -> str:
    u = (s or "").upper()
    if "JCB" in u:
        return "JCB"
    if "UNION" in u or "UPI" in u:
        return "UNIONPAY"
    if "AMEX" in u or "AMERICAN" in u:
        return "AMEX"
    if " MC" in u or "MASTER" in u or "MC " in u or u.endswith("MC"):
        return "MASTERCARD"
    if "VISA" in u or " VS" in u or "VS " in u:
        return "VISA"
    return ""


# ══════════════════════════════════════════════════════════════════════════════
# #5  KBank savings-account statement  →  recon_bank_lines
# ══════════════════════════════════════════════════════════════════════════════
# Layout (one table spanning N pages):
#   col0 = "<thai-date> <description>"   e.g. "01-พ.ค.-2569 รับโอนเงิน"
#   col1 = amount (unsigned)
#   col2 = running balance
#   col3.. = branch / channel / time codes
# The opening row "<date> ยอดยกมา" seeds the balance. Direction (debit vs credit)
# is inferred from the balance delta, which is self-checking against col1.

# Data lines look like:
#   "01-พ.ค.-2569 รับโอนเงิน 814.41 2,441,610.56 0898 NBGW3501 014555"
#   "01-พ.ค.-2569 ยอดยกมา 2,440,796.15"                       (opening balance)
# extract_tables() finds no ruled grid, so we parse the text lines directly:
# the description precedes the first money token; movement rows carry two money
# tokens (amount, then running balance); trailing tokens are branch/user/time.
_KB_DATE_PREFIX = re.compile(r"^\s*(\d{1,2}[-/\s][ก-๙.]+[-/\s]\d{4})\s+(.*)$", re.S)
_MONEY_RE       = re.compile(r"\d{1,3}(?:,\d{3})*\.\d{2}")


def _looks_like_kbank_statement(raw: bytes) -> bool:
    txt = _full_text(raw)
    return ("รายการเดินบัญชี" in txt
            and "ยอดยกมา" in txt
            and ("กสิกร" in txt or "KBANK" in txt.upper() or "Thai QR Payment" in txt))


def _kbank_statement(raw: bytes) -> tuple[str, list[dict], int]:
    rows: list[dict] = []
    skipped = 0
    prev_balance: float | None = None

    txt = _full_text(raw)
    acct = _extract_account(txt)
    for line in txt.splitlines():
        m = _KB_DATE_PREFIX.match(line)
        if not m:
            continue
        txn_date = _parse_thai_date(m.group(1))
        if not txn_date:
            continue
        rest = m.group(2)
        money = list(_MONEY_RE.finditer(rest))
        if not money:
            continue
        desc = re.sub(r"\s+", " ", rest[: money[0].start()]).strip()

        # Opening balance row → seed prev_balance, not a movement.
        if "ยอดยกมา" in desc or "ยกมา" in desc:
            prev_balance = _to_float(money[0].group())
            continue
        if len(money) < 2:
            skipped += 1
            continue

        amount  = _to_float(money[0].group())
        balance = _to_float(money[1].group())
        debit = credit = 0.0
        if prev_balance is None:
            (credit, debit) = (amount, 0.0) if _is_credit_keyword(desc) else (0.0, amount)
        elif round(balance - prev_balance, 2) >= 0:
            credit = amount
        else:
            debit = amount
        prev_balance = balance

        rows.append({
            "txn_date":    txn_date,
            "value_date":  txn_date,
            "description": desc,
            "channel":     _kbank_channel(desc),
            "debit":       debit,
            "credit":      credit,
            "balance":     balance,
            "ref_no":      "",
            "account_no":  acct,
        })
    return "kbank", rows, skipped


def _is_credit_keyword(desc: str) -> bool:
    return any(k in desc for k in ("รับ", "ฝาก", "ขาย", "โอนเข้า", "ดอกเบี้ย"))


# ══════════════════════════════════════════════════════════════════════════════
# #3  KTC / Krungthai Card settlement (รายงานสรุปการแจ้งเข้าบัญชี)  → recon_cc_lines
# ══════════════════════════════════════════════════════════════════════════════
# One settlement date (header field), one row per card TYPE:
#   "<merchant_no> <TYPE...> <gross> <commission> <vat> <net>"
# net == gross - commission - vat (verified). We store net_amount = the real
# CREDIT AMT (what hits the bank) and mdr_fee = commission; the VAT is the small
# residual and is not separately columned in recon_cc_lines.
_KTC_SETTLE_DATE = re.compile(r"SETTLEMENT DATE\s*\)?\s*(\d{1,2}/\d{1,2}/\d{4})")
_KTC_TYPE_LINE   = re.compile(r"^(\d{6,})\s+(.+?)\s+([\d,]+\.\d{2})\s+([\d,]+\.\d{2})\s+([\d,]+\.\d{2})\s+([\d,]+\.\d{2})\s*$")


def _looks_like_ktc_settlement(raw: bytes) -> bool:
    txt = _full_text(raw)
    return ("CREDIT AMT" in txt or "เข้าบัญชี" in txt) and (
        "บัตรกรุงไทย" in txt or "Krungthai Card" in txt or "SETTLEMENT DATE" in txt)


def _ktc_settlement(raw: bytes) -> tuple[str, list[dict], int]:
    txt = _full_text(raw)
    m = _KTC_SETTLE_DATE.search(txt)
    settle = _parse_thai_date(m.group(1)) if m else None
    rows, skipped = [], 0
    for line in txt.splitlines():
        lm = _KTC_TYPE_LINE.match(line.strip())
        if not lm:
            continue
        merchant_no, typ, gross, fee, vat, net = lm.groups()
        if not settle:
            skipped += 1
            continue
        rows.append({
            "settlement_date": settle,
            "card_type":  _detect_card_type(typ),
            "txn_count":  0,
            "gross_amount":  _to_float(gross),
            "refund_amount": 0.0,
            "mdr_fee":       _to_float(fee),     # commission ex-VAT
            "vat_amount":    _to_float(vat),     # VAT on the commission
            "net_amount":    _to_float(net),     # gross − fee − VAT (what the bank credits)
            "acquirer":   "KTC",
            "terminal_id": "",
            "merchant_id": merchant_no,
            "batch_no":   "",
            "notes":      typ.strip(),
        })
    return "KTC", rows, skipped


# ══════════════════════════════════════════════════════════════════════════════
# #7  KBank credit-card settlement (ใบกำกับภาษี, 1 summary / page)  → recon_cc_lines
# ══════════════════════════════════════════════════════════════════════════════
# Each page is one settlement: issued date "DD/MM/YYYY <docno>E<...>" + a single
# TOTAL line "บัตรเครดิต/เดบิต <count> <gross> <fee> <vat> <net>".
_KB_CC_DATE  = re.compile(r"(\d{2}/\d{2}/\d{4})\s+\d{6}E\d+")
_KB_CC_TOTAL = re.compile(r"บัตรเครดิต\S*\s+(\d+)\s+([\d,]+\.\d{2})\s+([\d,]+\.\d{2})\s+([\d,]+\.\d{2})\s+([\d,]+\.\d{2})")
_KB_CC_MID   = re.compile(r"รหัสร้านค้า\s+(\d+)")


def _looks_like_kbank_cc(raw: bytes) -> bool:
    txt = _full_text(raw)
    return ("K-BIZ" in txt or "FEE/COMMISION" in txt) and "บัตรเครดิต" in txt


def _kbank_cc(raw: bytes) -> tuple[str, list[dict], int]:
    rows, skipped = [], 0
    with _open(raw) as pdf:
        for page in pdf.pages:
            txt = page.extract_text() or ""
            dm = _KB_CC_DATE.search(txt)
            tm = _KB_CC_TOTAL.search(txt)
            if not (dm and tm):
                if tm:
                    skipped += 1
                continue
            settle = _parse_thai_date(dm.group(1))
            mid = _KB_CC_MID.search(txt)
            count, gross, fee, vat, net = tm.groups()
            rows.append({
                "settlement_date": settle,
                "card_type":  "",                       # KBank reports credit+debit combined
                "txn_count":  int(count),
                "gross_amount":  _to_float(gross),
                "refund_amount": 0.0,
                "mdr_fee":       _to_float(fee),     # commission ex-VAT
                "vat_amount":    _to_float(vat),     # VAT on the commission
                "net_amount":    _to_float(net),
                "acquirer":   "KBANK",
                "terminal_id": "",
                "merchant_id": mid.group(1) if mid else "",
                "batch_no":   "",
            })
    return "KBANK", rows, skipped


def _kbank_channel(desc: str) -> str:
    if "QR" in desc.upper() or "Thai QR" in desc:
        return "QR"
    if "รับเงินจากการขาย" in desc:
        return "CC_SETTLE"
    if "ค่าธรรมเนียม" in desc:
        return "FEE"
    if "โอน" in desc:
        return "TRANSFER"
    return _normalise_channel(desc)


# ══════════════════════════════════════════════════════════════════════════════
# BAY Point — Krungsri loyalty-point reimbursement (Payment Advice)  → recon_cc_lines
# ══════════════════════════════════════════════════════════════════════════════
# Customers redeem Krungsri points (booked as a Coupon/discount on POS, not a
# tender), and Krungsri reimburses the merchant — one payment run (Media Date)
# crediting the bank. So there is no POS-tender leg: we store it as an acquirer
# 'BAYPOINT' settlement that reconciles 2-way against the bank deposit.
_BAY_MEDIA  = re.compile(r"Media Date\s*:\s*(\d{2}/\d{2}/\d{4})")
_BAY_SUMMARY = re.compile(r"Summary[^\n]*?:\s*\d+\s+([\d,]+\.\d{2})\s+(\d+)\s+([\d,]+\.\d{2})")
_BAY_MID    = re.compile(r"by MID\s*:\s*(\d+)")


def _looks_like_bay_point(raw: bytes) -> bool:
    txt = _full_text(raw)
    return "KRUNGSRIAYUDHYA" in txt and ("Payment Advice" in txt or "Payment Point" in txt)


def _bay_point(raw: bytes) -> tuple[str, list[dict], int]:
    txt = _full_text(raw)
    md = _BAY_MEDIA.search(txt)
    sm = _BAY_SUMMARY.search(txt)
    mid = _BAY_MID.search(txt)
    if not (md and sm):
        return "BAYPOINT", [], 0
    settle = _parse_thai_date(md.group(1))
    total  = _to_float(sm.group(3))
    rows = [{
        "settlement_date": settle,
        "card_type":  "POINT",
        "txn_count":  int(sm.group(2)),
        "gross_amount":  total,
        "refund_amount": 0.0,
        "mdr_fee":       0.0,            # full reimbursement, no fee
        "vat_amount":    0.0,
        "net_amount":    total,
        "acquirer":   "BAYPOINT",
        "terminal_id": "",
        "merchant_id": mid.group(1) if mid else "",
        "batch_no":   "",
    }]
    return "BAYPOINT", rows, 0


# ══════════════════════════════════════════════════════════════════════════════
# #6  KTB savings-account statement (CordiaNew cid digits)  → recon_bank_lines
# ══════════════════════════════════════════════════════════════════════════════
# After cid→digit repair the layout is:
#   "DD/MM/YYYY HH:MM <desc> <amount> <tax> <balance> -"
# Single signed amount column (direction via balance delta); the middle 0.00 is
# withholding tax, the trailing token is the channel ('-' when none).
_KTB_LINE = re.compile(r"^(\d{2}/\d{2}/\d{4})\s+\d{1,2}:\d{2}\s+(.*)$")


def _looks_like_ktb_statement(raw: bytes) -> bool:
    txt = _full_text(raw, decid=True)
    return ("รายการเดินบัญชี" in txt
            and "ยอดคงเหลือยกมา" in txt
            and ("กรุงไทย" in txt or "krungthai" in txt.lower()))


def _ktb_statement(raw: bytes) -> tuple[str, list[dict], int]:
    rows, skipped = [], 0
    prev_balance: float | None = None
    txt = _full_text(raw, decid=True)
    acct = _extract_account(txt)

    # Seed opening balance from "ยอดคงเหลือยกมา <balance>".
    for line in txt.splitlines():
        if "ยอดคงเหลือยกมา" in line:
            mm = list(_MONEY_RE.finditer(line))
            if mm:
                prev_balance = _to_float(mm[-1].group())
            break

    for line in txt.splitlines():
        m = _KTB_LINE.match(line.strip())
        if not m:
            continue
        txn_date = _parse_thai_date(m.group(1))
        if not txn_date:
            continue
        rest = m.group(2)
        money = list(_MONEY_RE.finditer(rest))
        if len(money) < 2:           # need at least amount + balance
            skipped += 1
            continue
        desc    = re.sub(r"\s+", " ", rest[: money[0].start()]).strip()
        amount  = _to_float(money[0].group())
        balance = _to_float(money[-1].group())     # last money token is the balance
        debit = credit = 0.0
        if prev_balance is None:
            credit = amount
        elif round(balance - prev_balance, 2) >= 0:
            credit = amount
        else:
            debit = amount
        prev_balance = balance
        rows.append({
            "txn_date":    txn_date,
            "value_date":  txn_date,
            "description": desc,
            "channel":     _normalise_channel(desc) if desc else "OTHER",
            "debit":       debit,
            "credit":      credit,
            "balance":     balance,
            "ref_no":      "",
            "account_no":  acct,
        })
    return "ktb", rows, skipped


# ══════════════════════════════════════════════════════════════════════════════
# #2  Grab daily business report (รายงานธุรกิจรายวัน)  → recon_grab_lines
# ══════════════════════════════════════════════════════════════════════════════
# One file per day. Header carries the date ("1 พฤษภาคม 2026") and outlet; each
# order is "<time> <GF-id> <food> <vat> <svc> <promo> <commission> <disc> <net>"
# where svc may render as a bare '-'. We anchor food at the first money token and
# net at the last; commission is the (negative) Grab deduction.
_THAI_FULL_MONTHS = {
    "มกราคม": 1, "กุมภาพันธ์": 2, "มีนาคม": 3, "เมษายน": 4, "พฤษภาคม": 5,
    "มิถุนายน": 6, "กรกฎาคม": 7, "สิงหาคม": 8, "กันยายน": 9, "ตุลาคม": 10,
    "พฤศจิกายน": 11, "ธันวาคม": 12,
}
_GRAB_HEADER_DATE = re.compile(r"(\d{1,2})\s+([ก-๙]+)\s+(\d{4})")
_GRAB_ORDER       = re.compile(r"^(\d{1,2}:\d{2}\s*[AP]M)\s+(G[FM]-\S+)\s+(.*)$")
_SIGNED_MONEY     = re.compile(r"-?\d{1,3}(?:,\d{3})*\.\d{2}")


def _grab_header_date(txt: str) -> str | None:
    for line in txt.splitlines():
        m = _GRAB_HEADER_DATE.search(line)
        if m and m.group(2) in _THAI_FULL_MONTHS:
            day, mon, year = int(m.group(1)), _THAI_FULL_MONTHS[m.group(2)], int(m.group(3))
            if year > 2500:
                year -= 543
            try:
                from datetime import date as _date
                return _date(year, mon, day).strftime("%Y-%m-%d")
            except ValueError:
                return None
    return None


def _looks_like_grab_daily(raw: bytes) -> bool:
    txt = _full_text(raw)
    return "รายงานธุรกิจรายวัน" in txt and ("Grab" in txt or "คางชําระ Grab" in txt)


def _grab_daily(raw: bytes) -> tuple[list[dict], int]:
    # A single export may bundle several days: each day starts on a page carrying
    # a header date; later pages without one continue the same day. Walk pages in
    # order and stamp orders with the most recent header date.
    rows, skipped = [], 0
    merchant = ""
    current_date: str | None = None

    with _open(raw) as pdf:
        for page in pdf.pages:
            txt = page.extract_text() or ""
            d = _grab_header_date(txt)
            if d:
                current_date = d
            for line in txt.splitlines():
                if not merchant and ("(โอชิเน)" in line or line.strip().startswith("OSHINEI")):
                    merchant = line.strip()
                m = _GRAB_ORDER.match(line.strip())
                if not m:
                    continue
                if not current_date:
                    skipped += 1
                    continue
                order_id = m.group(2)
                nums = [_to_float(t) for t in _SIGNED_MONEY.findall(m.group(3))]
                if len(nums) < 2:
                    skipped += 1
                    continue
                # Grab's "ค่าคอมมิชชันและภาษีทั้งหมด" bundles the 7% VAT in. Split it
                # out so fee/VAT report separately (matches the IM tax invoice exactly:
                # comm_incl_vat / 1.07 = fee, remainder = VAT). net_payout is unchanged.
                comm_incl_vat = abs(sum(n for n in nums if n < 0))
                fee = round(comm_incl_vat / 1.07, 2)
                rows.append({
                    "order_date":      current_date,
                    "order_id":        order_id,
                    "merchant_name":   merchant,
                    "order_type":      "GrabFood" if order_id.startswith("GF") else "GrabMart",
                    "order_status":    "Completed",
                    "food_amount":     nums[0],
                    "commission_amt":  fee,                              # ex-VAT
                    "vat_amount":      round(comm_incl_vat - fee, 2),    # the 7% VAT
                    "promo_deduct":    0.0,
                    "net_payout":      nums[-1],
                })
    return rows, skipped


# ══════════════════════════════════════════════════════════════════════════════
# Dispatcher
# ══════════════════════════════════════════════════════════════════════════════

def is_empty_statement(raw: bytes) -> bool:
    """True when a PDF is a structurally valid statement with zero movements
    (e.g. a KBank current-account month with 'No Data'). Lets the UI say
    'เอกสารไม่มีรายการ' instead of presenting 0 rows as a parser failure."""
    try:
        txt = _full_text(raw)
    except Exception:
        return False
    if not txt.strip():
        return False
    return ("No Data" in txt or "ไม่มีรายการ" in txt or "ไม่พบรายการ" in txt)


# ── Wrong-document-type detection (A2) ────────────────────────────────────────
# Staff frequently upload a FEE / TAX-INVOICE document instead of the settlement
# or transaction report a lane needs (KTC TIV_MDR instead of *-RTxInv-*, Grab IM*
# invoice instead of the 3-CZ* report, KBANK E-TAX_INVOICE, Lineman Balance-Report).
# Those parse to 0 rows and used to save silently — name the mistake so the
# operator can self-correct without an engineer.

_FEE_INVOICE_BY_NAME = (
    ("tiv_mdr",        "นี่คือใบกำกับภาษีค่าธรรมเนียม (MDR) ของ KTC ไม่ใช่รายงาน settlement — "
                       "กรุณาอัปโหลดไฟล์ชื่อ *-RTxInv-*.pdf"),
    ("e-tax_invoice",  "นี่คือใบกำกับภาษีอิเล็กทรอนิกส์ (e-Tax invoice) ค่าธรรมเนียมบัตร "
                       "ไม่ใช่รายงาน settlement ของผู้รับบัตร"),
    ("e_tax_invoice",  "นี่คือใบกำกับภาษีอิเล็กทรอนิกส์ (e-Tax invoice) ค่าธรรมเนียมบัตร "
                       "ไม่ใช่รายงาน settlement ของผู้รับบัตร"),
    ("balance-report", "นี่คือ Balance Report ไม่ใช่รายงานยอดขาย Line Man — "
                       "กรุณาอัปโหลดไฟล์ REPORT_*.csv หรือ REPORT_*.pdf"),
)

_GRAB_IM_RE = re.compile(r"^im\d{6,}", re.IGNORECASE)


def fee_invoice_by_name(source_type: str, filename: str) -> str | None:
    """Cheap filename-only check for a known wrong document type. Returns a Thai
    message naming the correct file to upload, or None."""
    name = (filename or "").strip().lower()
    if not name:
        return None
    for needle, msg in _FEE_INVOICE_BY_NAME:
        if needle in name:
            return msg
    if source_type == "grab" and _GRAB_IM_RE.match(name):
        return ("นี่คือใบแจ้งหนี้ค่าธรรมเนียม Grab (IM) ไม่ใช่รายงานธุรกรรม — "
                "กรุณาอัปโหลดรายงานยอดขาย Grab (ไฟล์ขึ้นต้น 3-CZ...)")
    return None


def fee_invoice_by_text(raw: bytes, filename: str = "") -> str | None:
    """Backstop for renamed files: a PDF that reads like a fee tax-invoice with no
    settlement/transaction grid. Meant to run only AFTER a parse already failed or
    returned 0 rows, so it cannot block a document that parsed fine."""
    if not (filename or "").lower().endswith(".pdf"):
        return None
    try:
        txt = _full_text(raw)
    except Exception:
        return None
    low = txt.lower()
    has_tax_invoice = "ใบกำกับภาษี" in txt or "tax invoice" in low
    has_fee = "mdr" in low or "ค่าธรรมเนียม" in txt or "service fee" in low
    has_grid = ("rtxinv" in low or "settlement" in low or "รายการขาย" in txt
                or "order id" in low)
    if has_tax_invoice and has_fee and not has_grid:
        return ("เอกสารนี้ดูเหมือนใบกำกับภาษีค่าธรรมเนียม ไม่ใช่รายงาน settlement/ธุรกรรม — "
                "กรุณาตรวจสอบและอัปโหลดรายงานที่ถูกต้อง")
    return None


def unsupported_bank_pdf_message(raw: bytes, filename: str = "") -> str | None:
    """A bank-statement-shaped PDF from a bank we have no specific extractor for
    (only KBANK/KTB are supported). Meant to run only AFTER a parse failed or
    returned 0 rows, so a statement the generic grid CAN read isn't blocked —
    better a clear 'unsupported bank' than silently garbled debit/credit rows (C6)."""
    if not (filename or "").lower().endswith(".pdf"):
        return None
    try:
        if _looks_like_kbank_statement(raw) or _looks_like_ktb_statement(raw):
            return None                       # supported → not our case
        txt = _full_text(raw)
    except Exception:
        return None
    low = txt.lower()
    looks_stmt = bool(_extract_account(txt)) or "statement" in low \
        or "รายการเดินบัญชี" in txt or "ยอดยกมา" in txt or "เงินเข้า" in txt
    if looks_stmt:
        return ("ยังไม่รองรับ statement ของธนาคารนี้ (รองรับ KBANK/KTB เท่านั้น) — "
                "กรุณาอัปโหลดไฟล์ Excel/CSV ของ statement แทน")
    return None


def detect_and_parse(source_type: str, raw: bytes):
    """Return (provider, rows, skipped) when `raw` is a recognised special PDF for
    this source_type, else None (caller falls back to the generic grid loader)."""
    try:
        if source_type == "bank":
            if _looks_like_kbank_statement(raw):
                return _kbank_statement(raw)
            if _looks_like_ktb_statement(raw):
                return _ktb_statement(raw)
        elif source_type == "credit_card":
            if _looks_like_bay_point(raw):
                return _bay_point(raw)
            if _looks_like_ktc_settlement(raw):
                return _ktc_settlement(raw)
            if _looks_like_kbank_cc(raw):
                return _kbank_cc(raw)
        elif source_type == "grab":
            if _looks_like_grab_daily(raw):
                rows, skipped = _grab_daily(raw)
                return "Grab", rows, skipped
    except Exception:
        # Any extractor failure → let the generic path try.
        return None
    return None
