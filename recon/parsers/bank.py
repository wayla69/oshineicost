"""
Bank statement parsers for Thai banks.

Each bank exports Excel in a slightly different layout.
Parser returns a list of dicts matching recon_bank_lines columns.

Supported providers:
  - KBank  (Kasikorn Bank)
  - SCB    (Siam Commercial Bank)
  - BBL    (Bangkok Bank)
  - KTB    (Krungthai Bank)
  - TTB    (TMBThanachart Bank)
"""

from __future__ import annotations
import re
from datetime import datetime, date
from typing import Any
import openpyxl


# ── Channel normalisation ──────────────────────────────────────────────────────

# ── Statement-code dictionary (SINGLE SOURCE OF TRUTH) ─────────────────────────
# Every bank-statement abbreviation / keyword the parser recognises, what it MEANS,
# and which channel it is classified as. This table BOTH drives `_normalise_channel`
# (so the parser can never drift from the documentation) AND is surfaced to the team
# for review at /api/statement-codes[.html/.xlsx]. Order = first-match priority.
#
# status: "confirmed" = meaning verified · "review" = needs the team to confirm
#   (esp. bank-specific codes whose plain meaning isn't obvious from the description).
#
# fields: pattern (regex, case-insensitive) · channel · code (display) · kind
#   ('code'=bank abbreviation / 'keyword'=generic word) · bank · meaning_th · status · note
STATEMENT_CODE_MAP: list[dict] = [
    {"pattern": r"promptpay|พร้อมเพย์|QR|QR CODE", "channel": "QR",
     "code": "QR / PromptPay / พร้อมเพย์", "kind": "keyword", "bank": "*",
     "meaning_th": "รับชำระผ่าน QR พร้อมเพย์", "status": "confirmed", "note": ""},
    {"pattern": r"SDCH", "channel": "QR",
     "code": "SDCH", "kind": "code", "bank": "KTB",
     "meaning_th": "นำเงินสดไปฝากที่เคาน์เตอร์กรุงไทย (Same-Day Clearing House)",
     "status": "review",
     "note": "พาร์สเป็น QR แต่ความหมายจริง = เงินฝากสด (เจ้าของยืนยัน 2026-06-15). "
             "ตัวจับคู่เงินสดเคลมคืนเป็นเงินฝาก T+1 อยู่แล้ว — ทีมช่วยยืนยันว่าทุกบรรทัด SDCH เป็นเงินฝากสดจริง"},
    {"pattern": r"โอน|transfer|trf|สั่งจ่าย", "channel": "TRANSFER",
     "code": "โอน / Transfer / TRF", "kind": "keyword", "bank": "*",
     "meaning_th": "เงินโอนเข้าบัญชี", "status": "confirmed", "note": ""},
    {"pattern": r"IORSDT", "channel": "TRANSFER",
     "code": "IORSDT", "kind": "code", "bank": "KTB",
     "meaning_th": "รับโอนระหว่างธนาคาร (interbank credit) ผ่าน KTB", "status": "review",
     "note": "บางบรรทัดเป็นโอนภายใน/ไม่ใช่รายได้ — ทีมยืนยันว่าเป็นยอดขายหรือไม่"},
    {"pattern": r"NBSDT", "channel": "TRANSFER",
     "code": "NBSDT", "kind": "code", "bank": "KTB",
     "meaning_th": "รับโอนระหว่างธนาคารผ่าน KTB", "status": "review",
     "note": "เช่นเดียวกับ IORSDT — บางส่วนอาจไม่ใช่รายได้ ทีมช่วยตรวจ"},
    {"pattern": r"ฝาก|deposit|cash dep", "channel": "CASH_DEP",
     "code": "ฝาก / Deposit / Cash Dep", "kind": "keyword", "bank": "*",
     "meaning_th": "ฝากเงินสดเข้าบัญชี", "status": "confirmed", "note": ""},
    {"pattern": r"ค่าธรรมเนียม|fee|charge", "channel": "FEE",
     "code": "ค่าธรรมเนียม / Fee / Charge", "kind": "keyword", "bank": "*",
     "meaning_th": "ค่าธรรมเนียมธนาคาร (หักออก ไม่ใช่รายได้)", "status": "confirmed", "note": ""},
    {"pattern": r"edcsettle|credit card settle|SD Single credit", "channel": "CC_SETTLE",
     "code": "EDC Settle / SD Single credit", "kind": "keyword", "bank": "*",
     "meaning_th": "เงินโอนยอดบัตรเครดิตจากเครื่อง EDC / acquirer", "status": "confirmed", "note": ""},
    {"pattern": r"SDCK", "channel": "CC_SETTLE",
     "code": "SDCK", "kind": "code", "bank": "KTB",
     "meaning_th": "เคลียริงยอดบัตรเครดิตผ่าน KTB (SDCK :ZICID:)", "status": "review",
     "note": "คนละตัวกับ SDCH (เงินสด) — ทีมยืนยันว่าเป็นยอดบัตรเครดิตจริง"},
    {"pattern": r"รับเงินจากการขาย", "channel": "CC_SETTLE",
     "code": "รับเงินจากการขาย", "kind": "keyword", "bank": "KTB",
     "meaning_th": "KTB EDC settlement (ข้อความ 'รับเงินจากการขาย')", "status": "confirmed", "note": ""},
    {"pattern": r"alipay|wechat|อาลีเพย์", "channel": "QR",
     "code": "Alipay / WeChat", "kind": "keyword", "bank": "*",
     "meaning_th": "กระเป๋าเงินดิจิทัลต่างชาติ (จัดอยู่กลุ่ม QR)", "status": "confirmed", "note": ""},
    {"pattern": r"grab|แกร็บ", "channel": "GRAB_PAYOUT",
     "code": "Grab", "kind": "keyword", "bank": "*",
     "meaning_th": "เงินโอนยอดขาย Grab", "status": "confirmed", "note": ""},
    {"pattern": r"line man|lineman|ไลน์แมน", "channel": "LINEMAN_PAYOUT",
     "code": "Line Man", "kind": "keyword", "bank": "*",
     "meaning_th": "เงินโอนยอดขาย Line Man", "status": "confirmed", "note": ""},
]

# Built from the dictionary above so the parser and the review doc never diverge.
_CHANNEL_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(e["pattern"], re.I), e["channel"]) for e in STATEMENT_CODE_MAP
]


def _normalise_channel(description: str) -> str:
    if not description:
        return "OTHER"
    for pattern, channel in _CHANNEL_PATTERNS:
        if pattern.search(description):
            return channel
    return "OTHER"


# Thai abbreviated month names as they appear in KBank/KTB statements
# (e.g. "01-พ.ค.-2569"). Both the dotted and un-dotted forms are accepted.
_THAI_MONTHS = {
    "ม.ค.": 1, "ก.พ.": 2, "มี.ค.": 3, "เม.ย.": 4, "พ.ค.": 5, "มิ.ย.": 6,
    "ก.ค.": 7, "ส.ค.": 8, "ก.ย.": 9, "ต.ค.": 10, "พ.ย.": 11, "ธ.ค.": 12,
}
_THAI_DATE_RE = re.compile(r"^\s*(\d{1,2})\s*[-/\s]\s*([ก-๙.]+?)\s*[-/\s]\s*(\d{4}|\d{2})(?!\d)")
_NUM_DATE_RE  = re.compile(r"^(\d{1,2})[/-](\d{1,2})[/-](\d{4}|\d{2})$")


def _ce_year(year_text: str) -> int:
    """Gregorian year from a 4- or 2-digit, CE or Thai Buddhist-era year.
    4-digit: > 2500 is BE (2569 → 2026). 2-digit: ≥ 58 reads as BE 25YY
    (69 → 2569 → 2026; covers 2015+), below 58 as CE 20YY (26 → 2026) — the
    same rule as bulk_recon_upload.normalize_year. Statements here are dated
    2015-2057, so a 2-digit CE year ≥ 58 (2058+) is never expected."""
    y = int(year_text)
    if len(year_text) == 2:
        return 2500 + y - 543 if y >= 58 else 2000 + y
    return y - 543 if y > 2500 else y


def _parse_thai_date(raw: Any) -> str | None:
    """Convert Thai date strings to YYYY-MM-DD. Handles numeric forms
    (DD/MM/YYYY, DD-MM-YYYY or 2-digit YY, BE or CE), Thai abbreviated months
    (e.g. '01-พ.ค.-2569' / '01-พ.ค.-69') and Excel date cells (BE year too)."""
    if isinstance(raw, (datetime, date)):
        d = raw.date() if isinstance(raw, datetime) else raw
        if d.year > 2500:                       # Excel cell typed with a BE year
            try:
                d = d.replace(year=d.year - 543)
            except ValueError:                  # 29 Feb of a non-leap CE year
                return None
        return d.isoformat()
    if not raw:
        return None
    s = str(raw).strip()
    # ISO date or datetime, e.g. "2026-06-05" / "2026-06-05 12:46:38.379"
    # (LINE MAN Balance-Report CSV writes capture_time as a string with time +
    # fractional seconds). fromisoformat accepts the space/'T' separator and ms.
    try:
        d = datetime.fromisoformat(s.replace("T", " "))
        if d.year > 2500:                       # Thai Buddhist Era
            d = d.replace(year=d.year - 543)
        return d.strftime("%Y-%m-%d")
    except ValueError:
        pass
    # Day-first numeric form: DD/MM/YYYY, DD-MM-YYYY, DD/MM/YY (BE or CE year)
    m = _NUM_DATE_RE.match(s)
    if m:
        try:
            return date(_ce_year(m.group(3)), int(m.group(2)), int(m.group(1))).strftime("%Y-%m-%d")
        except ValueError:
            return None
    try:                                        # loose ISO, e.g. "2026-5-1"
        d = datetime.strptime(s, "%Y-%m-%d")
        if d.year > 2500:
            d = d.replace(year=d.year - 543)
        return d.strftime("%Y-%m-%d")
    except ValueError:
        pass
    # Thai abbreviated-month form, e.g. "01-พ.ค.-2569" / "01-พ.ค.-69"
    m = _THAI_DATE_RE.match(s)
    if m:
        day, mon_th = m.group(1), m.group(2).strip()
        mon = _THAI_MONTHS.get(mon_th)
        if mon:
            try:
                return date(_ce_year(m.group(3)), mon, int(day)).strftime("%Y-%m-%d")
            except ValueError:
                return None
    return None


# Currency marks and thousands separators (incl. space / NBSP) inside an amount.
_AMOUNT_NOISE = re.compile(r"[\s,฿]|THB|บาท", re.I)        # \s also covers NBSP


def _parse_amount(val: Any) -> float | None:
    """Amount cell → float. Accepts '1,234.50', '(1,234.50)' and '1,234.50-'
    (negative), '฿1,000.00', '1 000.00'. Blank / '-' → 0.0. Returns None when
    the cell holds text that is not an amount, so a caller can count it."""
    if val is None or val == "":
        return 0.0
    if isinstance(val, (int, float)) and not isinstance(val, bool):
        return float(val)
    s = _AMOUNT_NOISE.sub("", str(val))
    if s in ("", "-"):
        return 0.0
    neg = False
    if s.startswith("(") and s.endswith(")"):
        neg, s = True, s[1:-1]
    elif s.endswith("-"):
        neg, s = True, s[:-1]
    try:
        f = float(s)
    except (ValueError, TypeError):
        return None
    return -f if neg else f


def _to_float(val: Any) -> float:
    v = _parse_amount(val)
    return 0.0 if v is None else v


def _cell(row, col: dict, key: str, default: Any = None) -> Any:
    """The value of field `key` in `row`, or `default` when that column was not
    found in the header (never silently column 0) or the row is short."""
    j = col.get(key)
    if j is None or j >= len(row):
        return default
    return row[j]


def _blank(val: Any) -> bool:
    """True for cells that carry no data (so a failed date parse on them is not
    a 'skipped' row, just an empty/spacer line)."""
    return val is None or (isinstance(val, str) and not val.strip()) or val == ""


# ── KBank ──────────────────────────────────────────────────────────────────────

def _parse_kbank(ws: openpyxl.worksheet.worksheet.Worksheet) -> list[dict]:
    """
    KBank export (K PLUS / KBank Biz Portal) layout:
      Row 1-7: header info (branch name, account, period)
      Row 8: column headers  →  วันที่ | รายการ | รายการถอน | รายการฝาก | ยอดคงเหลือ
      Row 9+: data rows
    Column mapping may vary; we detect by header text.
    """
    rows = list(ws.iter_rows(values_only=True))
    header_row = None
    col = {}

    for i, row in enumerate(rows):
        texts = [str(c).strip().lower() if c else "" for c in row]
        if "วันที่" in texts or "date" in texts:
            header_row = i
            for j, t in enumerate(texts):
                if "วันที่" in t or t == "date":          col["date"] = j
                elif "รายการถอน" in t or "debit" in t:   col["debit"] = j
                elif "รายการฝาก" in t or "credit" in t:  col["credit"] = j
                elif "ยอดคงเหลือ" in t or "balance" in t: col["balance"] = j
                elif "รายการ" in t or "description" in t: col.setdefault("desc", j)
                elif "เลขที่" in t or "ref" in t:        col.setdefault("ref", j)
            break

    if header_row is None:
        raise ValueError("KBank: could not find header row (วันที่ column missing)")

    results, skipped = [], 0
    for row in rows[header_row + 1 :]:
        raw_date = row[col.get("date", 0)] if col.get("date") is not None else None
        txn_date = _parse_thai_date(raw_date)
        if not txn_date:
            if not _blank(raw_date):
                skipped += 1
            continue
        debit  = _to_float(row[col["debit"]])  if "debit"  in col else 0.0
        credit = _to_float(row[col["credit"]]) if "credit" in col else 0.0
        desc   = str(row[col["desc"]]).strip()  if "desc"  in col else ""
        ref    = str(row[col.get("ref", 0)]).strip() if col.get("ref") is not None else ""
        results.append({
            "txn_date":    txn_date,
            "value_date":  txn_date,
            "description": desc,
            "channel":     _normalise_channel(desc),
            "debit":       debit,
            "credit":      credit,
            "balance":     _to_float(row[col["balance"]]) if "balance" in col else None,
            "ref_no":      ref,
        })
    return results, skipped


# ── SCB ───────────────────────────────────────────────────────────────────────

def _parse_scb(ws: openpyxl.worksheet.worksheet.Worksheet) -> list[dict]:
    """
    SCB Easy Net / SCB Business Net export.
    Layout similar to KBank but may have Thai-only headers.
    Column order: Transaction Date | Description | Withdrawal | Deposit | Balance
    """
    return _parse_generic(ws, {
        "date":    ["transaction date", "วันที่ทำรายการ", "วันที่"],
        "desc":    ["description", "รายการ", "รายละเอียด"],
        "debit":   ["withdrawal", "ถอน", "รายการถอน"],
        "credit":  ["deposit", "ฝาก", "รายการฝาก"],
        "balance": ["balance", "ยอดคงเหลือ"],
        "ref":     ["reference", "เลขที่อ้างอิง", "ref"],
    })


# ── BBL ───────────────────────────────────────────────────────────────────────

def _parse_bbl(ws: openpyxl.worksheet.worksheet.Worksheet) -> list[dict]:
    """Bangkok Bank Biz iBanking export."""
    return _parse_generic(ws, {
        "date":    ["date", "วันที่"],
        "desc":    ["description", "รายการ"],
        "debit":   ["debit", "ถอน"],
        "credit":  ["credit", "ฝาก"],
        "balance": ["balance", "คงเหลือ"],
        "ref":     ["cheque no", "เช็คเลขที่", "ref"],
    })


# ── KTB ───────────────────────────────────────────────────────────────────────

def _parse_ktb(ws: openpyxl.worksheet.worksheet.Worksheet) -> list[dict]:
    """Krungthai Bank statement (PDF or xlsx).

    Column layout from KTB online banking:
      วันที่/เวลา | รายการ | รายละเอียด | หมายเลขเช็ค | ถอนเงิน/ฝากเงิน | ภาษี | ยอดคงเหลือ | ช่องทาง

    The ถอนเงิน/ฝากเงิน column is combined — positive values are credits
    (deposits), negative ones withdrawals; it maps to BOTH debit and credit and
    _parse_generic splits it by sign. desc → รายการ captures statement codes
    (SDCH / IORSDT / NBSDT) needed for channel normalisation.
    """
    return _parse_generic(ws, {
        "date":    ["วันที่", "date"],
        "desc":    ["รายการ", "description", "รายละเอียด"],
        "debit":   ["ถอนเงิน/ฝากเงิน", "ถอนเงิน", "withdrawal", "debit"],
        "credit":  ["ถอนเงิน/ฝากเงิน", "ฝากเงิน", "ฝาก", "deposit", "credit"],
        "balance": ["ยอดคงเหลือ", "balance"],
        "ref":     ["หมายเลขเช็ค", "cheque", "ref"],
    })


# ── Generic fallback ──────────────────────────────────────────────────────────

def _parse_generic(
    ws: openpyxl.worksheet.worksheet.Worksheet,
    col_keywords: dict[str, list[str]],
) -> list[dict]:
    rows = list(ws.iter_rows(values_only=True))
    col: dict[str, int] = {}
    header_row = None
    first_date_row = None          # (index, columns) of the first row naming a date

    # Columns are detected within ONE candidate row (title/metadata rows above
    # the header must not claim columns), and a row is the header only when it
    # names both a date and a credit column. Fallback: the first row naming a
    # date (the old rule) so a credit-less layout still parses.
    for i, row in enumerate(rows):
        texts = [str(c).strip().lower() if c else "" for c in row]
        found: dict[str, int] = {}
        for field, keywords in col_keywords.items():
            for j, t in enumerate(texts):
                if t and any(k in t for k in keywords):
                    found[field] = j
                    break
        if "date" in found and "credit" in found:
            col, header_row = found, i
            break
        if "date" in found and first_date_row is None:
            first_date_row = (i, found)
    if header_row is None and first_date_row is not None:
        header_row, col = first_date_row

    if header_row is None or "date" not in col:
        raise ValueError("Generic bank parser: could not find date column")

    # One signed amount column (KTB ถอนเงิน/ฝากเงิน) → split by sign.
    signed = "debit" in col and col.get("debit") == col.get("credit")

    results, skipped = [], 0
    for row in rows[header_row + 1 :]:
        raw_date = _cell(row, col, "date")
        txn_date = _parse_thai_date(raw_date)
        if not txn_date:
            if not _blank(raw_date):
                skipped += 1
            continue
        desc = str(_cell(row, col, "desc", "")).strip() if "desc" in col else ""
        if signed:
            v = _to_float(_cell(row, col, "credit"))
            debit, credit = (-v if v < 0 else 0.0), (v if v > 0 else 0.0)
        else:
            debit  = _to_float(_cell(row, col, "debit"))
            credit = _to_float(_cell(row, col, "credit"))
        results.append({
            "txn_date":    txn_date,
            "value_date":  txn_date,
            "description": desc,
            "channel":     _normalise_channel(desc),
            "debit":       debit,
            "credit":      credit,
            "balance":     _to_float(_cell(row, col, "balance")) if "balance" in col else None,
            "ref_no":      str(_cell(row, col, "ref", "")).strip() if "ref" in col else "",
        })
    return results, skipped


# ── Public API ────────────────────────────────────────────────────────────────

# Generic fallback keywords (unknown provider, and TTB which has no own layout).
_GENERIC_KEYWORDS = {
    "date":    ["date", "วันที่"],
    "desc":    ["description", "รายการ"],
    "debit":   ["debit", "withdrawal", "ถอน"],
    "credit":  ["credit", "deposit", "ฝาก"],
    "balance": ["balance", "คงเหลือ"],
    "ref":     ["ref", "reference"],
}

_PROVIDER_PARSERS = {
    "kbank": _parse_kbank,
    "scb":   _parse_scb,
    "bbl":   _parse_bbl,
    "ktb":   _parse_ktb,
    "ttb":   lambda ws: _parse_generic(ws, _GENERIC_KEYWORDS),
}

_PROVIDER_KEYWORDS = {
    "kbank": ["kasikorn", "กสิกร", "kbank"],
    "scb":   ["siam commercial", "ไทยพาณิชย์", "scb"],
    "bbl":   ["bangkok bank", "ธนาคารกรุงเทพ", "bbl"],   # not bare 'กรุงเทพ' (KTB's HQ address)
    "ktb":   ["krungthai", "กรุงไทย", "ktb"],
    "ttb":   ["tmbthana", "tmb", "ttb"],
}

_HDR_DATE_KWS   = ("วันที่", "date")
_HDR_AMOUNT_KWS = ("ฝาก", "deposit", "credit", "ถอน", "withdraw", "debit",
                   "คงเหลือ", "balance", "amount", "จำนวนเงิน")


def _header_block_text(ws, max_rows: int = 30) -> str | None:
    """Lower-cased text of the rows ABOVE the column-header row (title, bank
    name, account block) — never the transactions, whose descriptions name
    other banks ('โอนจาก KBANK'). None when no header row is recognised."""
    block = []
    for row in ws.iter_rows(min_row=1, max_row=max_rows, values_only=True):
        texts = [str(c).strip().lower() for c in row if c is not None and str(c).strip()]
        date_cells = {j for j, t in enumerate(texts) if any(k in t for k in _HDR_DATE_KWS)}
        amt_cells  = {j for j, t in enumerate(texts) if any(k in t for k in _HDR_AMOUNT_KWS)}
        if date_cells and amt_cells - date_cells:
            return " ".join(block)
        block.append(" ".join(texts))
    return None


def _detect_provider(wb: openpyxl.Workbook) -> str:
    """Score bank-name keywords in the active sheet's header block; when no
    header row is recognised, fall back to the first 10 rows of all sheets."""
    text = _header_block_text(wb.active)
    if text is None:
        text = ""
        for ws in wb.worksheets:
            for row in ws.iter_rows(min_row=1, max_row=10, values_only=True):
                text += " ".join(str(c).lower() for c in row if c) + " "
    scores = {p: sum(text.count(k) for k in kws) for p, kws in _PROVIDER_KEYWORDS.items()}
    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else "generic"


def parse_bank_statement(
    file_bytes: bytes,
    provider: str | None = None,
) -> tuple[str, list[dict], int]:
    """
    Parse a bank statement Excel file.

    Returns (detected_provider, rows, skipped) where each row is a dict matching
    recon_bank_lines columns (excluding upload_id / match fields) and skipped is
    the count of data rows dropped for an unparseable date.
    """
    import io
    wb = openpyxl.load_workbook(io.BytesIO(file_bytes), read_only=True, data_only=True)
    detected = provider or _detect_provider(wb)
    ws = wb.active

    # The UI sends 'KBANK' / 'KTB'; the parser table is keyed lower-case. The
    # returned (stored) provider stays exactly as given.
    parser = _PROVIDER_PARSERS.get(str(detected).strip().lower())
    if parser is None:
        # generic fallback with sensible defaults
        rows, skipped = _parse_generic(ws, _GENERIC_KEYWORDS)
    else:
        try:
            rows, skipped = parser(ws)
        except ValueError:
            # A layout the bank-specific parser doesn't know (header not found):
            # the generic parser is what these uploads always got before the
            # provider-case fix made the specific parsers reachable.
            rows, skipped = _parse_generic(ws, _GENERIC_KEYWORDS)

    return detected, rows, skipped
