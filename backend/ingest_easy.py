"""
ingest_easy.py
==============
Parses Easy POS report files and loads them into backend/oshinei.db (SQLite).

Supported formats:
  ES1_*.xlsx  — Easy POS xlsx exports (current format, one branch per file)
  Easy_*.txt  — Type A monthly SKU reports (legacy)
  Easy *_*.txt — Type B per-transaction reports (legacy)

Run from any directory:
    python backend/ingest_easy.py

Re-running is idempotent — it drops and recreates the DB from scratch.
"""

import sqlite3
import re
import sys
import io
from pathlib import Path
from datetime import datetime

import openpyxl

# Force UTF-8 stdout so Thai characters print correctly on Windows.
# Guarded: only when the streams expose a real binary .buffer (a console). Under
# pytest/imported-as-library, stdout is a capture object without .buffer — touching
# it there detaches pytest's capture and crashes teardown, so we skip it.
#
# reconfigure() IN PLACE — never wrap sys.stdout in a fresh TextIOWrapper. A new
# wrapper starts block-buffered (8 KB), which silently undoes `python -u` for
# every importer: sync_mssql (via reingest_raw_2026) printed nothing to the
# Task Scheduler window for 20-50 min at a time, the window looked hung, and
# closing it killed the sync (0xC000013A, 6 runs in Aug–Sep 2026). The harness
# that developed this never saw it because PYTHONIOENCODING=utf-8 there makes
# the guard skip; the scheduler's console is cp1252, so it did not.
for _name in ("stdout", "stderr"):
    _s = getattr(sys, _name, None)
    _buf = getattr(_s, "buffer", None)
    if _buf is not None and getattr(_s, "encoding", "").lower() not in ("utf-8", "utf8"):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, AttributeError):
            try:   # a stream without reconfigure(): wrap, but keep it line-buffered
                setattr(sys, _name, io.TextIOWrapper(_buf, encoding="utf-8",
                                                     errors="replace", line_buffering=True))
            except (ValueError, AttributeError):
                pass

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
HERE       = Path(__file__).parent.resolve()
DB_PATH    = HERE / "oshinei.db"
SCHEMA_SQL = HERE / "schema.sql"
DATA_DIR   = HERE.parent / "Data for Oshinei"
SOURCE     = "Easy"

# ---------------------------------------------------------------------------
# Thai helpers
# ---------------------------------------------------------------------------
THAI_MONTHS = {
    "มกราคม": 1, "กุมภาพันธ์": 2, "มีนาคม": 3, "เมษายน": 4,
    "พฤษภาคม": 5, "มิถุนายน": 6, "กรกฎาคม": 7, "สิงหาคม": 8,
    "กันยายน": 9, "ตุลาคม": 10, "พฤศจิกายน": 11, "ธันวาคม": 12,
}
ENG_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "june": 6, "jul": 7, "july": 7, "aug": 8, "sep": 9,
    "oct": 10, "nov": 11, "dec": 12,
}

# Known branch names (Thai) — covers all Easy POS branches seen in 23 ES_ files.
# Add new branches here as they come online.
KNOWN_BRANCHES = {
    "โคราช1", "โคราช2", "เชียงใหม่", "สุรินทร์",
    "อุดรธานี", "อุบลราชธานี", "ขอนแก่น",
    "OSHINEI ชลบุรี", "Oshinei ระยอง", "ขอนแก่น (1)",
    "ฉะเชิงเทรา", "เชียงราย", "นครปฐม(Oshinei)", "นครพนม",
    "นครสวรรค์(oshinei)", "บุรีรัมย์ (1)", "พัทยา", "พิษณุโลก",
    "เพชรบูรณ์ (1)", "มหาสารคาม", "มุกดาหาร1", "ร้อยเอ็ด(oshinei)",
    "รังสิต", "ลพบุรี (1)", "ศรีสะเกษ", "สกลนคร",
    "สุพรรณบุรี", "ร้าน Oshinei สาขา อยุธยา",
}

# Explicit NON-branch section headers (look like branch names but are
# menu category / sub-brand markers). User confirmed 2026-05-28 that
# "Oshinei หม้อและน้ำซุป" is a sub-brand menu section, not a real branch.
NON_BRANCH_HEADERS = {
    "Oshinei หม้อและน้ำซุป",
    "Oshinei ALCOHOL", "Oshinei DRINKING", "Oshinei SOFT DRINK",
    "Oshinei BEVERAGE", "Oshinei DESSERT",
}


def is_branch_marker(row, max_data_cols: int = 25) -> str:
    """If row looks like a stand-alone branch marker, return the name; else ''."""
    if not row or not row[0]:
        return ""
    name = str(row[0]).strip()
    if not name or len(name) > 30:
        return ""
    # All other cells empty
    if any(row[i] is not None for i in range(1, min(len(row), max_data_cols))):
        return ""
    # Explicit non-branch headers always reject
    if name in NON_BRANCH_HEADERS:
        return ""
    if name in KNOWN_BRANCHES:
        return name
    # Fallback heuristic: contains Thai chars, no digits at start, not a footer label
    if name[0].isdigit():
        return ""
    if name in ("Transection", "เลขที่การขาย", "Payment", "Promotion", "Special"):
        return ""
    # Common category-name false positives (start with 'Oshinei' but are menu sections)
    if name.startswith("Oshinei ") and name not in KNOWN_BRANCHES:
        return ""
    if any('฀' <= c <= '๿' for c in name):
        return name
    return ""

# Lines that are category/section headers in Type A (not branch names)
CATEGORY_HEADER_RE = re.compile(
    r"^(BUFFET|Oshinei|Payment|Promotion|Special|Take Away|Service|Voucher|"
    r"UBN|YAKI|\(YAKI\))",
    re.IGNORECASE,
)

# Lines that are clearly footer / transaction summary labels
FOOTER_LABELS = {
    "Transection", "มูลค่ารายการที่สั่ง", "ยอดรวมการคืน", "ส่วนลด",
    "มูลค่ารายการที่ยกเลิก", "ยอดรวมลบการขาย", "ยอดขายจริง",
    "ยอดขายจากการชำระหนี้", "ยอดขายจากบัตรของขวัญ", "OC",
    "เอ็นเตอร์เทน", "มูลค่ารายการที่รับเข้า", "ค่าใช้จ่าย",
    "จำนวนลูกค้าทั้งหมด", "จำนวนการขาย", "จำนวนพิมพ์ใบเรียกเก็บเงิน",
    "จำนวนพิมพ์ใบเสร็จ", "รวมทั้งหมด",
}

# ---------------------------------------------------------------------------
# Number parsing
# ---------------------------------------------------------------------------
def parse_num(s: str) -> float:
    """Strip commas, handle leading '.' (e.g. '.00'), return float or 0."""
    s = s.strip().replace(",", "")
    if s in ("", "-"):
        return 0.0
    if s.startswith("."):
        s = "0" + s
    try:
        return float(s)
    except ValueError:
        return 0.0


def parse_int(s: str) -> int:
    s = s.strip().replace(",", "")
    try:
        return int(float(s))
    except ValueError:
        return 0


# ---------------------------------------------------------------------------
# Date parsing
# ---------------------------------------------------------------------------
def be_to_ce_year(be_year: int) -> int:
    return be_year - 543


def parse_thai_date(s: str) -> str:
    """
    Parse strings like '23 พฤษภาคม 2568 13:11:57' or '23 พฤษภาคม 2568'
    into ISO datetime/date string.
    """
    s = s.strip()
    # Pattern: DD MonthThai YYYY HH:MM:SS
    m = re.match(
        r"(\d{1,2})\s+([^\s]+)\s+(\d{4})(?:\s+(\d{2}:\d{2}:\d{2}))?", s
    )
    if not m:
        return s
    day   = int(m.group(1))
    month = THAI_MONTHS.get(m.group(2), 0)
    year  = be_to_ce_year(int(m.group(3)))
    time  = m.group(4) or "00:00:00"
    return f"{year:04d}-{month:02d}-{day:02d}T{time}"


def parse_thai_date_slash(s: str) -> str:
    """
    Parse Thai date in slash format like '1/4/2569 12:24:01' (D/M/YYYY HH:MM:SS).
    """
    s = s.strip()
    m = re.match(r"(\d{1,2})/(\d{1,2})/(\d{4})(?:\s+(\d{2}:\d{2}:\d{2}))?", s)
    if not m:
        return parse_thai_date(s)  # fall back
    day   = int(m.group(1))
    month = int(m.group(2))
    year  = be_to_ce_year(int(m.group(3)))
    time  = m.group(4) or "00:00:00"
    return f"{year:04d}-{month:02d}-{day:02d}T{time}"


def parse_date_any(s: str) -> str:
    """Try Thai-word format first, then slash format."""
    s = s.strip()
    if any(m in s for m in THAI_MONTHS):
        return parse_thai_date(s)
    return parse_thai_date_slash(s)


def period_from_thai_date(s: str) -> str:
    """Return 'YYYY-MM' from a Thai date string."""
    iso = parse_date_any(s)
    return iso[:7]  # 'YYYY-MM'


def period_from_filename_a(stem: str) -> str:
    """
    Type A: Easy_<Month> <YY>  e.g. 'Easy_May 25', 'Easy_Apr 26'
    YY < 69 → 2500+YY+543... but since Easy files use Thai BE internally,
    filename YY is Gregorian: 25 → 2025, 26 → 2026.
    """
    m = re.match(r"Easy_(\w+)\s+(\d{2})$", stem, re.IGNORECASE)
    if not m:
        return ""
    month_str = m.group(1).lower()
    yy = int(m.group(2))
    year = 2000 + yy
    month = ENG_MONTHS.get(month_str, 0)
    if month == 0:
        return ""
    return f"{year:04d}-{month:02d}"


def period_from_filename_b(stem: str) -> str:
    """
    Type B: Easy <Month>_<YY>  e.g. 'Easy May_68', 'Easy Apr_69'
    YY is Thai BE year last two digits: 68 → BE 2568 → CE 2025.
    """
    m = re.match(r"Easy\s+(\w+)_(\d{2})$", stem, re.IGNORECASE)
    if not m:
        return ""
    month_str = m.group(1).lower()
    yy = int(m.group(2))
    be_year = 2500 + yy
    year = be_to_ce_year(be_year)
    month = ENG_MONTHS.get(month_str, 0)
    if month == 0:
        return ""
    return f"{year:04d}-{month:02d}"


def period_from_filename_es1(stem: str) -> str:
    """
    ES1_<Month> <YY>  e.g. 'ES1_May 25', 'ES1_July  25', 'ES1_Apr 26'
    YY is Gregorian year last two digits: 25 → 2025, 26 → 2026.
    """
    m = re.match(r"ES1_(\w+)\s+(\d{2})$", stem.strip(), re.IGNORECASE)
    if not m:
        return ""
    month_str = m.group(1).lower()
    yy = int(m.group(2))
    year = 2000 + yy
    month = ENG_MONTHS.get(month_str, 0)
    if month == 0:
        return ""
    return f"{year:04d}-{month:02d}"


# ---------------------------------------------------------------------------
# ES1 xlsx parser (current format)
# ---------------------------------------------------------------------------
def parse_es1_xlsx(path: Path, period_hint: str) -> dict:
    """
    Parse an ES1_*.xlsx file (Easy POS Type A monthly SKU export).

    A single ES1 file can contain MANY branches — the branch on row 8 is just
    the first one. Each subsequent branch starts when a stand-alone branch
    name row appears in col 0.

    Returns:
      {'segments': [{'branch', 'period', 'skus': [...]}, ...],
       'grand_summary': {...},   # chain-wide totals (the 'Transection' footer)
       'period': str}
    """
    wb = openpyxl.load_workbook(str(path), read_only=True, data_only=True)
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))
    wb.close()

    def cell_str(r, idx) -> str:
        if len(r) <= idx or r[idx] is None:
            return ""
        return str(r[idx]).strip()

    def cell_num(r, idx) -> float:
        if len(r) <= idx or r[idx] is None:
            return 0.0
        v = r[idx]
        if isinstance(v, (int, float)):
            return float(v)
        try:
            return float(str(v).strip().replace(",", ""))
        except (ValueError, TypeError):
            return 0.0

    # Derive period from row 5, col 4 (Thai date like '01 ธันวาคม 2568 08:30').
    # The in-file date is authoritative, but a SILENT override of the filename hint
    # has caused mis-dated ingests before — so surface any disagreement loudly.
    period = period_hint
    if len(rows) > 5 and rows[5] and len(rows[5]) > 4 and rows[5][4]:
        derived = period_from_thai_date(str(rows[5][4]).strip())
        if derived:
            if period_hint and derived != period_hint:
                print(f"  [WARN] period mismatch in {path.name}: filename={period_hint} "
                      f"but in-file date={derived} -> using {derived}. VERIFY this file.",
                      file=sys.stderr)
            period = derived

    segments: list[dict] = []
    current_branch = ""
    current_skus:    list = []
    current_category = ""
    grand_summary:   dict = {}
    in_footer = False

    def flush():
        if current_branch and current_skus:
            segments.append({
                "branch": current_branch,
                "period": period,
                "skus":   list(current_skus),
            })

    # Data starts at Excel row 9 = Python index 8 (first branch marker, e.g.
    # "OSHINEI ชลบุรี"). The column-header row "รหัสสินค้า" comes at row 10 = idx 9
    # and is skipped explicitly below. Previously this was `rows[9:]` which
    # silently dropped the FIRST branch section of every file — caused ชลบุรี
    # to have 0 sales_sku_monthly rows despite 21K transactions ingested.
    for r in rows[8:]:
        if not r:
            continue

        col0   = r[0] if len(r) > 0 else None
        col5   = r[5] if len(r) > 5 else None
        label0 = str(col0).strip() if col0 is not None else ""

        # --- Footer mode triggered by 'Transection' ---
        if label0 == "Transection":
            flush()
            current_branch = ""
            current_skus   = []
            in_footer = True
            continue

        if in_footer:
            val = r[7] if len(r) > 7 else None
            fval = float(val) if isinstance(val, (int, float)) else 0.0
            if label0 == "มูลค่ารายการที่สั่ง":
                grand_summary["gross_sales"] = fval
            elif label0 == "ยอดรวมการคืน":
                grand_summary["refunds"] = fval
            elif label0 == "ส่วนลด":
                grand_summary["discounts"] = fval
            elif label0 == "มูลค่ารายการที่ยกเลิก":
                grand_summary["voided"] = fval
            elif label0 == "ยอดขายจริง":
                grand_summary["net_sales"] = fval
            elif label0 == "เงินสด":
                grand_summary["cash"] = fval
            elif label0 == "บัตรเครดิต":
                grand_summary["credit_card"] = fval
            elif label0 == "คูปอง":
                grand_summary["coupon"] = fval
            elif label0 == "เงินโอน":
                grand_summary["transfer"] = fval
            elif "QR" in label0 or "K SHOP" in label0 or "promptpay" in label0.lower():
                grand_summary["qr"] = grand_summary.get("qr", 0.0) + fval
            elif label0 == "จำนวนลูกค้าทั้งหมด":
                grand_summary["customer_count"] = int(fval)
            elif label0 == "จำนวนการขาย":
                grand_summary["transaction_count"] = int(fval)
            continue

        # Skip column header rows
        if label0 == "รหัสสินค้า":
            continue

        # Subtotal row: col0 is None — skip
        if col0 is None:
            continue

        # Branch marker?  (col0 = branch name, all other cells None)
        bname = is_branch_marker(r)
        if bname:
            flush()
            current_branch   = bname
            current_skus     = []
            current_category = ""
            continue

        # Category header: col0 has value, col5 is None (and not a branch)
        if col5 is None:
            current_category = label0
            continue

        # SKU data row: col0 = sku_code, col5 = description
        if current_branch and label0 and re.search(r"[A-Za-z0-9]", label0):
            current_skus.append((
                label0,                                        # sku_code
                cell_str(r, 5),                                # description
                cell_str(r, 11),                               # unit
                cell_num(r, 8),                                # qty
                cell_num(r, 14),                               # subtotal_ex_vat
                cell_num(r, 16),                               # tax1
                cell_num(r, 17) + cell_num(r, 20),             # tax2+tax3
                cell_num(r, 22),                               # total_inc_vat
                current_category,
            ))

    flush()
    return {"segments": segments, "grand_summary": grand_summary, "period": period}


# ---------------------------------------------------------------------------
# 23 ES_*.xlsx — Type B per-transaction Easy POS export
# ---------------------------------------------------------------------------
def parse_es_typeb_xlsx(path: Path, period_hint: str) -> dict:
    """
    Parse a '23 ES_*.xlsx' file (Easy POS per-transaction xlsx export).

    The file contains many branches; each branch is followed by its own
    transaction rows. Column layout (col index):
      0  receipt_no   2  sold_at (datetime)   6  cashier   11 table_no
      14 cust_count   15 food_subtotal   17 drink_subtotal   19 other_subtotal
      23 tip   24 sp_discount   26 discount   27 service_charge
      29 total_exc_tax   31 tax   33 total_inc_vat
      34 cash   36 credit_card   37 outstanding   39 entertain   40 other_pay
      42 grand_total

    Returns:
      {'segments': [{'branch', 'period', 'transactions': [...]}, ...]}
    """
    wb = openpyxl.load_workbook(str(path), read_only=True, data_only=True)
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))
    wb.close()

    def cell_num(r, idx) -> float:
        if len(r) <= idx or r[idx] is None:
            return 0.0
        v = r[idx]
        if isinstance(v, (int, float)):
            return float(v)
        try:
            return float(str(v).strip().replace(",", ""))
        except (ValueError, TypeError):
            return 0.0

    def cell_int(r, idx) -> int:
        try:
            return int(cell_num(r, idx))
        except (ValueError, TypeError):
            return 0

    def cell_str(r, idx) -> str:
        if len(r) <= idx or r[idx] is None:
            return ""
        return str(r[idx]).strip()

    # Derive period from row 6 ('ตั้งแต่:' label, value at col 1 with Thai date)
    period = period_hint
    if len(rows) > 6 and rows[6]:
        for v in rows[6]:
            if v is None:
                continue
            s = str(v).strip()
            if any(m in s for m in THAI_MONTHS):
                derived = period_from_thai_date(s)
                if derived:
                    if period_hint and derived != period_hint:
                        print(f"  [WARN] period mismatch in {path.name}: filename={period_hint} "
                              f"but in-file date={derived} -> using {derived}. VERIFY this file.",
                              file=sys.stderr)
                    period = derived
                    break

    segments: list[dict] = []
    current_branch = ""
    current_txns:    list = []
    in_footer = False  # set when 'ค่าใช้จ่าย' or 'บัตรของขวัญ' section starts

    def flush():
        if current_branch and current_txns:
            segments.append({
                "branch":       current_branch,
                "period":       period,
                "transactions": list(current_txns),
            })

    for r in rows:
        if not r:
            continue

        col0   = r[0] if len(r) > 0 else None
        label0 = str(col0).strip() if col0 is not None else ""

        # Footer triggers
        if label0 in ("ค่าใช้จ่าย", "บัตรของขวัญ"):
            flush()
            current_branch = ""
            current_txns   = []
            in_footer = True
            continue
        if in_footer:
            # In footer we ignore everything until end-of-file
            continue

        # Skip non-branch labels & headers
        if not label0:
            continue
        if label0 in ("เลขที่การขาย",) or "ตั้งแต่" in label0 or "ถึง:" in label0:
            continue
        if label0.startswith("จำนวนบิล") or label0.startswith("รวม"):
            continue

        # Branch marker?
        bname = is_branch_marker(r, max_data_cols=15)
        if bname:
            flush()
            current_branch = bname
            current_txns   = []
            continue

        # Transaction data row: needs receipt_no (col 0) AND date (col 2)
        sold_at = r[2] if len(r) > 2 else None
        if not current_branch or sold_at is None:
            continue
        # Receipt no must look like a real receipt (alphanumeric with digits)
        if not re.search(r"\d", label0):
            continue

        # Convert sold_at to ISO string
        if isinstance(sold_at, datetime):
            sold_iso = sold_at.strftime("%Y-%m-%dT%H:%M:%S")
        else:
            sold_iso = str(sold_at).strip()

        current_txns.append((
            label0,                                            # receipt_no        t[0]
            sold_iso,                                          #                   t[1]
            cell_str(r, 6),                                    # cashier           t[2]
            cell_str(r, 11),                                   # table_no          t[3]
            cell_int(r, 14),                                   # customer_count    t[4]
            cell_num(r, 15),                                   # food              t[5]
            cell_num(r, 17),                                   # drink             t[6]
            cell_num(r, 19),                                   # other             t[7]
            cell_num(r, 23),                                   # tip               t[8]
            cell_num(r, 24),                                   # sp_discount       t[9]
            cell_num(r, 26),                                   # discount          t[10]
            cell_num(r, 27),                                   # service_charge    t[11]
            cell_num(r, 33),                                   # total_inc_vat     t[12]
            cell_num(r, 29),                                   # total_exc_tax     t[13]
            cell_num(r, 34),                                   # pay_cash (เงินสด) t[14]
            cell_num(r, 36),                                   # pay_credit_card   t[15]
            cell_num(r, 39),                                   # pay_entertain     t[16]
            cell_num(r, 40),                                   # pay_other         t[17] — verify: QR/transfer?
        ))

    flush()
    return {"segments": segments, "period": period}


def load_es_typeb(con: sqlite3.Connection, result: dict, filename: str) -> tuple[int, int]:
    """
    Insert per-transaction rows into sales_transaction and roll them up into
    sales_month_summary (source='Easy', authoritative for any branch present).
    Returns (tx_rows, summary_rows).
    """
    cur = con.cursor()
    tx_rows = 0
    summ_rows = 0

    for seg in result["segments"]:
        branch = seg["branch"]
        period = seg["period"]
        txns   = seg["transactions"]
        if not branch or not period or not txns:
            continue

        branch_id = get_or_create(cur, "branches", "branch_id", "name", branch)

        # Wipe prior Easy transactions for this branch+month
        cur.execute(
            """DELETE FROM sales_transaction
               WHERE branch_id=? AND source=?
                 AND strftime('%Y-%m', sold_at)=?""",
            (branch_id, SOURCE, period),
        )

        # Aggregates for sales_month_summary
        agg_gross = 0.0
        agg_disc  = 0.0
        agg_svc   = 0.0
        agg_cust  = 0
        n_tx      = 0
        agg_cash  = 0.0
        agg_cc    = 0.0
        agg_other = 0.0   # col 40 — see VERIFY note below

        for t in txns:
            (receipt_no, sold_at, cashier, table_no, cust,
             food, drink, other, tip, sp_disc, discount, svc, total_iv,
             total_ex, pay_cash, pay_cc, pay_entertain, pay_other) = t

            cur.execute(
                """INSERT OR REPLACE INTO sales_transaction
                   (branch_id, receipt_no, sold_at, cashier, table_no,
                    customer_count, food_subtotal, drink_subtotal, other_subtotal,
                    tip, sp_discount, discount, service_charge, total_inc_vat, source)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (branch_id, receipt_no, sold_at, cashier, table_no,
                 cust, food, drink, other, tip, sp_disc, discount, svc,
                 total_iv, SOURCE),
            )
            tx_rows += 1
            agg_gross += food + drink + other
            agg_disc  += sp_disc + discount
            agg_svc   += svc
            agg_cust  += cust
            n_tx      += 1
            agg_cash  += pay_cash
            agg_cc    += pay_cc
            agg_other += pay_other

        # Upsert sales_month_summary
        cur.execute(
            "DELETE FROM sales_month_summary WHERE branch_id=? AND period_month=? AND source=?",
            (branch_id, period, SOURCE),
        )
        # net_sales must be VAT-EXCLUSIVE (hard rule: VAT is never revenue).
        # Use total_exc_tax (col 29, t[13]); fall back to inc-VAT/1.07 only if
        # the ex-VAT column is missing/zero for a row. The service charge (t[11])
        # sits INSIDE the ex-VAT total and is not revenue — subtracted, exactly as
        # month_summary.BACKFILL_SQL does for live-synced months (rules.net_revenue).
        net_sales = sum((t[13] if t[13] else t[12] / 1.07) - (t[11] or 0) for t in txns)
        # VERIFY: col 40 (pay_other / other_pay) is mapped to qr below.
        # If it contains bank transfer or a mix, split accordingly after inspection.
        cur.execute(
            """INSERT INTO sales_month_summary
               (branch_id, period_month, gross_sales, refunds, discounts,
                voided, net_sales, cash, credit_card, transfer, qr, coupon,
                customer_count, transaction_count, source)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (branch_id, period,
             round(agg_gross, 2), 0.0,
             round(agg_disc, 2), 0.0,
             round(net_sales, 2),
             round(agg_cash, 2), round(agg_cc, 2), 0.0, round(agg_other, 2), 0.0,
             agg_cust, n_tx, SOURCE),
        )
        summ_rows += 1

        print(f"  [ES-B] {filename} | {branch:<24} {period} | tx={n_tx:>6} cust={agg_cust:>7} gross={agg_gross:>14,.0f} "
              f"cash={agg_cash:>12,.0f} cc={agg_cc:>12,.0f} other(col40)={agg_other:>12,.0f}")

    con.commit()
    return tx_rows, summ_rows


def load_es1(con: sqlite3.Connection, result: dict, filename: str) -> int:
    """
    Insert ES1 xlsx SKU data into sales_sku_monthly (one row per branch+SKU).
    Does NOT touch sales_month_summary — the file's grand_summary is chain-wide,
    not per-branch. Per-branch monthly totals come from 23 ES_*.xlsx instead.
    """
    cur = con.cursor()
    total_sku_rows = 0

    for seg in result["segments"]:
        branch = seg["branch"]
        period = seg["period"]
        skus   = seg["skus"]

        if not branch or not skus:
            continue

        branch_id = get_or_create(cur, "branches", "branch_id", "name", branch)
        cur.execute(
            "DELETE FROM sales_sku_monthly WHERE branch_id=? AND period_month=? AND source=?",
            (branch_id, period, SOURCE),
        )

        agg: dict[str, list] = {}
        for row in skus:
            sku_code, desc, unit, qty, sub_ex, tax1, tax23, total_iv, cat = row
            if sku_code not in agg:
                agg[sku_code] = [sku_code, desc, unit, cat, 0.0, 0.0, 0.0, 0.0]
            agg[sku_code][4] += qty
            agg[sku_code][5] += sub_ex
            agg[sku_code][6] += tax1 + tax23
            agg[sku_code][7] += total_iv

        for vals in agg.values():
            sku_code, desc, unit, cat, qty, sub_ex, tax_tot, total_iv = vals
            cat_id = get_or_create(cur, "categories", "category_id", "name", cat or "Unknown")
            sku_id = get_or_create_sku(cur, sku_code, desc, cat_id, unit)
            cur.execute(
                """INSERT OR REPLACE INTO sales_sku_monthly
                   (branch_id, period_month, sku_id, qty, subtotal_ex_vat,
                    tax_total, total_inc_vat, source)
                   VALUES(?,?,?,?,?,?,?,?)""",
                (branch_id, period, sku_id, qty, sub_ex, tax_tot, total_iv, SOURCE),
            )
            total_sku_rows += 1

        print(f"  [ES1] {filename} | {branch:<24} {period} | sku_rows={len(agg)}")

    con.commit()
    return total_sku_rows


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------
def read_file(path: Path) -> list[str]:
    """Read cp874-encoded file, split on newlines, strip \\r."""
    raw = path.read_bytes()
    text = raw.decode("cp874", errors="replace")
    return [line.rstrip("\r") for line in text.split("\n")]


def get_or_create(cur: sqlite3.Cursor, table: str, id_col: str,
                  name_col: str, name: str) -> int:
    cur.execute(f"SELECT {id_col} FROM {table} WHERE {name_col}=?", (name,))
    row = cur.fetchone()
    if row:
        return row[0]
    cur.execute(f"INSERT INTO {table}({name_col}) VALUES(?)", (name,))
    return cur.lastrowid


def get_or_create_sku(cur: sqlite3.Cursor, sku_code: str,
                      description: str, category_id: int, unit: str) -> int:
    cur.execute("SELECT sku_id FROM skus WHERE sku_code=?", (sku_code,))
    row = cur.fetchone()
    if row:
        return row[0]
    cur.execute(
        "INSERT INTO skus(sku_code, description, category_id, unit) VALUES(?,?,?,?)",
        (sku_code, description or None, category_id, unit or None),
    )
    return cur.lastrowid


# ---------------------------------------------------------------------------
# Type A parser — Monthly SKU report
# ---------------------------------------------------------------------------
def is_branch_line(line: str) -> bool:
    """True if the line is a branch marker (short, no tabs, known branch)."""
    stripped = line.strip()
    if "\t" in line:
        return False
    return stripped in KNOWN_BRANCHES


def is_category_line(line: str) -> bool:
    """True if the line is a category section header."""
    stripped = line.strip()
    if "\t" in line:
        return False
    if not stripped:
        return False
    if stripped in KNOWN_BRANCHES:
        return False
    if CATEGORY_HEADER_RE.match(stripped):
        return True
    # Other known categories
    known_extra = {
        "BUFFET", "Payment", "Promotion", "Special", "Take Away",
        "Service", "Voucher", "UBN BADMINTON",
    }
    return stripped in known_extra or stripped.startswith("(YAKI)")


def parse_type_a(path: Path, period_hint: str) -> dict:
    """
    Parse a Type A file.
    Returns {
        'segments': [{'branch': str, 'period': str, 'skus': [...], 'summary': {...}}],
        'grand_summary': {...}
    }
    Each sku row: (sku_code, description, unit, qty, subtotal_ex_vat, tax1, tax2, tax3, total_inc_vat)
    """
    lines = read_file(path)

    # --- Parse header to get period ---
    period = period_hint
    from_date_str = ""
    for i, line in enumerate(lines[:8]):
        parts = line.split("\t")
        label = parts[0].strip()
        if label in ("จาก",):
            from_date_str = parts[1].strip() if len(parts) > 1 else ""
            if from_date_str:
                period = period_from_thai_date(from_date_str)
            break

    # --- Walk lines to build per-branch segments ---
    segments = []
    current_branch = None
    current_category = None
    current_skus = []

    # We collect all footer data from the LAST occurrence (grand totals section)
    grand_summary = {}

    # Positions of branch starts
    # Structure: header (0-6), then [branch_name_line, col_header, data..., รวมทั้งหมด, next_branch...]
    # After last รวมทั้งหมด comes the Transection summary block

    in_footer = False  # once we see "Transection"

    def flush_segment():
        if current_branch and current_skus:
            segments.append({
                "branch": current_branch,
                "period": period,
                "skus": list(current_skus),
            })

    for line in lines:
        stripped = line.strip()

        # Skip empty
        if not stripped:
            continue

        # Check for Transection marker — start of grand footer
        parts = line.split("\t")
        label0 = parts[0].strip()

        if label0 == "Transection":
            in_footer = True
            flush_segment()
            current_branch = None
            current_skus = []
            continue

        if in_footer:
            # Parse grand summary fields
            val1 = parts[1].strip() if len(parts) > 1 else ""
            val_right = parts[-1].strip() if parts else ""

            if label0 == "มูลค่ารายการที่สั่ง":
                grand_summary["gross_sales"] = parse_num(val1)
            elif label0 == "ยอดรวมการคืน":
                grand_summary["refunds"] = parse_num(val1)
            elif label0 == "ส่วนลด":
                grand_summary["discounts"] = parse_num(val1)
            elif label0 == "มูลค่ารายการที่ยกเลิก":
                grand_summary["voided"] = parse_num(val1)
            elif label0 == "ยอดขายจริง":
                grand_summary["net_sales"] = parse_num(val1)
            elif label0 == "เงินสด":
                grand_summary["cash"] = parse_num(val_right)
            elif label0 == "บัตรเครดิต":
                grand_summary["credit_card"] = parse_num(val_right)
            elif label0 == "คูปอง":
                grand_summary["coupon"] = parse_num(val_right)
            elif label0 == "เงินโอน":
                grand_summary["transfer"] = parse_num(val_right)
            elif label0 in ("QR code KTC", "QR code K-bank", "QR PromptPay",
                            "QR Link POS", "K SHOP"):
                grand_summary["qr"] = grand_summary.get("qr", 0.0) + parse_num(val_right)
            elif label0 == "จำนวนลูกค้าทั้งหมด":
                grand_summary["customer_count"] = parse_int(val1)
            elif label0 == "จำนวนการขาย":
                grand_summary["transaction_count"] = parse_int(val1)
            continue

        # --- Branch line ---
        if is_branch_line(line):
            flush_segment()
            current_branch = stripped
            current_skus = []
            current_category = None
            continue

        # --- Skip column header line ---
        if label0 in ("รหัสสินค้า",):
            continue

        # --- Category section header ---
        if is_category_line(line):
            current_category = stripped
            continue

        # --- รวมทั้งหมด — per-branch cumulative total, skip data-wise ---
        if label0 == "รวมทั้งหมด":
            continue

        # --- Subtotal row (empty sku_code, has numbers) ---
        if label0 == "" and len(parts) >= 9:
            # Could be category subtotal row — skip
            continue

        # --- SKU data row: sku_code in col0, description in col1 ---
        if current_branch and label0 and label0 not in FOOTER_LABELS:
            # Must have at least 9 tab-separated fields
            if len(parts) < 9:
                continue
            sku_code    = label0
            description = parts[1].strip() if len(parts) > 1 else ""
            qty_str     = parts[2].strip() if len(parts) > 2 else ""
            unit        = parts[3].strip() if len(parts) > 3 else ""
            sub_ex_vat  = parts[4].strip() if len(parts) > 4 else ""
            tax1        = parts[5].strip() if len(parts) > 5 else ""
            tax2        = parts[6].strip() if len(parts) > 6 else ""
            tax3        = parts[7].strip() if len(parts) > 7 else ""
            total_iv    = parts[8].strip() if len(parts) > 8 else ""

            qty_val = parse_num(qty_str)
            # Only add if looks like a real SKU (sku_code has alphanumeric)
            if re.search(r"[A-Za-z0-9]", sku_code):
                current_skus.append((
                    sku_code,
                    description,
                    unit,
                    qty_val,
                    parse_num(sub_ex_vat),
                    parse_num(tax1),
                    parse_num(tax2) + parse_num(tax3),  # tax2+tax3
                    parse_num(total_iv),
                    current_category or "",
                ))

    # In case the file ended without Transection marker
    if not in_footer:
        flush_segment()

    return {
        "segments": segments,
        "grand_summary": grand_summary,
        "period": period,
    }


# ---------------------------------------------------------------------------
# Type B parser — Per-transaction report
# ---------------------------------------------------------------------------
def parse_type_b(path: Path, period_hint: str) -> dict:
    """
    Parse a Type B file.
    Returns {
        'segments': [{'branch': str, 'period': str, 'transactions': [...]}],
    }
    Each transaction:
      (receipt_no, sold_at_iso, cashier, table_no, customer_count,
       food, drink, other, tip, sp_disc, discount, service_charge, total_inc_vat)
    """
    lines = read_file(path)

    period = period_hint

    # Find period from header
    for line in lines[:6]:
        parts = line.split("\t")
        if parts[0].strip() == "ตั้งแต่:":
            from_str = parts[1].strip() if len(parts) > 1 else ""
            if from_str:
                period = period_from_thai_date(from_str) or period
            break

    segments = []
    current_branch = None
    current_txns = []

    def flush():
        if current_branch and current_txns:
            segments.append({
                "branch": current_branch,
                "period": period,
                "transactions": list(current_txns),
            })

    # Branch summary line: starts with "จำนวนบิลสาขา" or "จำนวนบิลทั้งหมด"
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue

        parts = line.split("\t")
        label0 = parts[0].strip()

        # --- Branch marker ---
        if stripped in KNOWN_BRANCHES and "\t" not in line:
            flush()
            current_branch = stripped
            current_txns = []
            continue

        # Skip header/footer lines
        if label0 in (
            "เลขที่การขาย", "", "ตั้งแต่:", "Oshine",
            "ค่าใช้จ่าย", "สาขา:", "บัตรของขวัญ",
            "ยอดรวมค่าใช้จ่าย",
        ):
            continue
        if label0.startswith("จำนวนบิล"):
            # Branch summary — skip (we use the actual rows)
            continue
        if label0.startswith("รวม") or label0 == "Total of":
            continue
        # Skip date-only lines at footer
        if re.match(r"^\d{1,2}\s+[^\s]+\s+\d{4}\s*$", label0):
            continue

        # Skip column-header lines
        if "วันที่ขาย" in label0 or "แคชเชียร์" in label0:
            continue

        # Skip sub-header (food/drink/other splits)
        if label0 == "" and len(parts) > 1 and "อาหาร" in line:
            continue

        if not current_branch:
            continue

        # --- Transaction data row ---
        # Columns: receipt_no, date, cashier, table, customers,
        #   food, drink, other, tip, sp_disc, discount, service_charge,
        #   total_exc_tax, tax, total_inc_vat, cash, credit, ...
        if not re.search(r"[0-9]", label0):
            continue
        # receipt_no is the first field; must look like a receipt
        receipt_no = label0
        if len(parts) < 15:
            continue

        # Skip branch-summary rows: these have a bare count in col0 and
        # the branch name or "รวม" in col2 (cashier column), with empty date.
        date_str     = parts[1].strip()
        cashier_col  = parts[2].strip() if len(parts) > 2 else ""

        # Summary rows: numeric-only label, empty date, branch/total in cashier col
        if not date_str and label0.isdigit():
            continue
        if label0.isdigit() and cashier_col in (KNOWN_BRANCHES | {"รวม", "Total of"}):
            continue

        # Skip any row where the date is empty (catches remaining summary rows)
        if not date_str:
            continue
        cashier      = parts[2].strip()
        table_no     = parts[3].strip()
        cust_str     = parts[4].strip()
        food         = parts[5].strip()
        drink        = parts[6].strip()
        other        = parts[7].strip()
        tip          = parts[8].strip()
        sp_disc      = parts[9].strip()
        discount     = parts[10].strip()
        service_chg  = parts[11].strip()
        # col 12 = total_exc_tax, col 13 = tax, col 14 = total_inc_vat
        total_iv     = parts[14].strip() if len(parts) > 14 else ""

        # Parse date
        try:
            sold_at = parse_date_any(date_str)
        except Exception:
            sold_at = date_str

        current_txns.append((
            receipt_no,
            sold_at,
            cashier,
            table_no,
            parse_int(cust_str),
            parse_num(food),
            parse_num(drink),
            parse_num(other),
            parse_num(tip),
            parse_num(sp_disc),
            parse_num(discount),
            parse_num(service_chg),
            parse_num(total_iv),
        ))

    flush()
    return {"segments": segments, "period": period}


# ---------------------------------------------------------------------------
# DB loader
# ---------------------------------------------------------------------------
def init_db(db_path: Path, schema_path: Path) -> sqlite3.Connection:
    """Drop & recreate the DB from schema.

    SAFETY: this destroys ALL data including manually-curated cost tables
    (sku_cost, sku_cost_monthly, bom_ingredients, bom_sku_alias,
    branch_targets, cost_scenario*). It refuses to run if those curated
    tables hold data unless OSHINEI_FORCE_REBUILD=1 is set, and always
    timestamps a backup before unlinking. For routine re-ingests use
    reingest_easy_safe.py instead.
    """
    import os
    import shutil

    if db_path.exists():
        # Guard: do not silently wipe curated cost data.
        curated = ("sku_cost", "sku_cost_monthly", "bom_ingredients",
                   "bom_sku_alias", "branch_targets")
        try:
            chk = sqlite3.connect(str(db_path))
            populated = []
            for tbl in curated:
                try:
                    n = chk.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0]
                    if n:
                        populated.append(f"{tbl}={n}")
                except sqlite3.Error:
                    pass  # table may not exist on a fresh DB
            chk.close()
        except sqlite3.Error:
            populated = []

        if populated and os.environ.get("OSHINEI_FORCE_REBUILD") != "1":
            raise RuntimeError(
                "init_db refused: curated tables hold data ("
                + ", ".join(populated)
                + "). A full rebuild would destroy them. Use "
                "reingest_easy_safe.py, or set OSHINEI_FORCE_REBUILD=1 to override."
            )

        # Always back up before destroying.
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup = db_path.with_name(f"{db_path.name}.backup_pre_init_db_{ts}")
        shutil.copy2(db_path, backup)
        print(f"  [init_db] backed up existing DB -> {backup.name}")
        db_path.unlink()

    con = sqlite3.connect(str(db_path))
    con.executescript(schema_path.read_text(encoding="utf-8"))
    con.commit()
    return con


def load_type_a(con: sqlite3.Connection, result: dict, filename: str):
    """Insert Type A data into DB."""
    cur = con.cursor()
    total_sku_rows = 0

    for seg in result["segments"]:
        branch  = seg["branch"]
        period  = seg["period"]
        skus    = seg["skus"]

        branch_id = get_or_create(cur, "branches", "branch_id", "name", branch)

        # Delete existing rows for (branch, period, source) before inserting
        cur.execute(
            "DELETE FROM sales_sku_monthly WHERE branch_id=? AND period_month=? AND source=?",
            (branch_id, period, SOURCE),
        )

        # Aggregate skus by (sku_code, category)
        agg: dict[str, list] = {}
        for row in skus:
            sku_code, desc, unit, qty, sub_ex, tax1, tax23, total_iv, cat = row
            key = sku_code
            if key not in agg:
                agg[key] = [sku_code, desc, unit, cat, 0.0, 0.0, 0.0, 0.0]
            agg[key][4] += qty
            agg[key][5] += sub_ex
            agg[key][6] += tax1 + tax23
            agg[key][7] += total_iv

        for key, vals in agg.items():
            sku_code, desc, unit, cat, qty, sub_ex, tax_tot, total_iv = vals
            cat_id  = get_or_create(cur, "categories", "category_id", "name", cat or "Unknown")
            sku_id  = get_or_create_sku(cur, sku_code, desc, cat_id, unit)

            cur.execute(
                """INSERT OR REPLACE INTO sales_sku_monthly
                   (branch_id, period_month, sku_id, qty, subtotal_ex_vat,
                    tax_total, total_inc_vat, source)
                   VALUES(?,?,?,?,?,?,?,?)""",
                (branch_id, period, sku_id, qty, sub_ex, tax_tot, total_iv, SOURCE),
            )
            total_sku_rows += 1

        print(f"  [A] {filename} | branch={branch} | period={period} | sku_rows={len(agg)}")

    # Grand summary — stored once using the total across all branches.
    # We store it under a special "ALL" pseudo-branch or the last branch seen.
    # Since the summary covers the whole file (all branches), store per branch
    # proportionally would be inaccurate. Skip for now unless we have per-branch
    # summary rows.
    gs = result.get("grand_summary", {})
    if gs and result["segments"]:
        # Store under each branch equally only if there's one branch;
        # for multi-branch files the grand summary covers all branches.
        # We store once under an "ALL" branch entry.
        all_branch_id = get_or_create(cur, "branches", "branch_id", "name", "ALL")
        period = result["segments"][0]["period"]
        cur.execute(
            "DELETE FROM sales_month_summary WHERE branch_id=? AND period_month=? AND source=?",
            (all_branch_id, period, SOURCE),
        )
        cur.execute(
            """INSERT OR REPLACE INTO sales_month_summary
               (branch_id, period_month, gross_sales, refunds, discounts,
                voided, net_sales, cash, credit_card, transfer, qr, coupon,
                customer_count, transaction_count, source)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                all_branch_id, period,
                gs.get("gross_sales", 0),
                gs.get("refunds", 0),
                gs.get("discounts", 0),
                gs.get("voided", 0),
                gs.get("net_sales", 0),
                gs.get("cash", 0),
                gs.get("credit_card", 0),
                gs.get("transfer", 0),
                gs.get("qr", 0),
                gs.get("coupon", 0),
                gs.get("customer_count", 0),
                gs.get("transaction_count", 0),
                SOURCE,
            ),
        )

    con.commit()
    return total_sku_rows


def load_type_b(con: sqlite3.Connection, result: dict, filename: str):
    """Insert Type B transaction data into DB."""
    cur = con.cursor()
    total_tx_rows = 0

    for seg in result["segments"]:
        branch  = seg["branch"]
        period  = seg["period"]
        txns    = seg["transactions"]

        branch_id = get_or_create(cur, "branches", "branch_id", "name", branch)

        # Delete existing rows for (branch, period, source)
        cur.execute(
            """DELETE FROM sales_transaction
               WHERE branch_id=? AND source=?
               AND strftime('%Y-%m', sold_at)=?""",
            (branch_id, SOURCE, period),
        )

        for txn in txns:
            (receipt_no, sold_at, cashier, table_no, cust_count,
             food, drink, other, tip, sp_disc, discount, service_chg,
             total_iv) = txn

            cur.execute(
                """INSERT OR REPLACE INTO sales_transaction
                   (branch_id, receipt_no, sold_at, cashier, table_no,
                    customer_count, food_subtotal, drink_subtotal, other_subtotal,
                    tip, sp_discount, discount, service_charge, total_inc_vat, source)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (branch_id, receipt_no, sold_at, cashier, table_no,
                 cust_count, food, drink, other, tip, sp_disc, discount,
                 service_chg, total_iv, SOURCE),
            )
            total_tx_rows += 1

        print(f"  [B] {filename} | branch={branch} | period={period} | tx_rows={len(txns)}")

    con.commit()
    return total_tx_rows


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------
def verify(con: sqlite3.Connection, samples: list[tuple]) -> list[dict]:
    """
    For each (filename, branch) pair in samples:
    - Sum sales_sku_monthly.total_inc_vat
    - Compare to รวมทั้งหมด from file footer (stored in verification_data)
    - Count sales_transaction rows
    - Compare to จำนวนการขาย from file footer
    """
    results = []
    cur = con.cursor()

    for item in samples:
        branch, period, expected_sku_total, expected_tx_count, source_file = item

        cur.execute(
            """SELECT SUM(total_inc_vat)
               FROM sales_sku_monthly ssm
               JOIN branches b ON b.branch_id = ssm.branch_id
               WHERE b.name=? AND ssm.period_month=? AND ssm.source='Easy'""",
            (branch, period),
        )
        row = cur.fetchone()
        actual_sku_total = round(row[0], 2) if row and row[0] else 0.0

        cur.execute(
            """SELECT COUNT(*)
               FROM sales_transaction st
               JOIN branches b ON b.branch_id = st.branch_id
               WHERE b.name=? AND strftime('%Y-%m', st.sold_at)=? AND st.source='Easy'""",
            (branch, period),
        )
        row2 = cur.fetchone()
        actual_tx_count = row2[0] if row2 else 0

        results.append({
            "file": source_file,
            "branch": branch,
            "period": period,
            "sku_total_db":  actual_sku_total,
            "sku_total_file": expected_sku_total,
            "sku_match": abs(actual_sku_total - expected_sku_total) < 1.0 if expected_sku_total else "N/A",
            "tx_count_db":  actual_tx_count,
            "tx_count_file": expected_tx_count,
            "tx_match": actual_tx_count == expected_tx_count if expected_tx_count else "N/A",
        })

    return results


# ---------------------------------------------------------------------------
# Single-file ingest (for API upload)
# ---------------------------------------------------------------------------
def ingest_file(path: Path, con: sqlite3.Connection) -> dict:
    """
    Ingest a single Easy .txt file (Type A or Type B) into an open connection.
    Returns {"branch": str, "period": str, "rows_ingested": int, "file_type": "A"|"B"}.
    Raises ValueError on parse failure.
    """
    stem = path.stem
    # Determine type by filename pattern
    if re.match(r"Easy_\w+\s+\d{2}$", stem, re.IGNORECASE):
        period_hint = period_from_filename_a(stem)
        result = parse_type_a(path, period_hint)
        rows = load_type_a(con, result, path.name)
        segs = result.get("segments", [])
        branch = segs[0]["branch"] if segs else "Unknown"
        period = result.get("period", period_hint)
        return {"branch": branch, "period": period, "rows_ingested": rows, "file_type": "A"}
    elif re.match(r"Easy\s+\w+_\d{2}$", stem, re.IGNORECASE):
        period_hint = period_from_filename_b(stem)
        result = parse_type_b(path, period_hint)
        rows = load_type_b(con, result, path.name)
        segs = result.get("segments", [])
        branch = segs[0]["branch"] if segs else "Unknown"
        period = result.get("period", period_hint)
        return {"branch": branch, "period": period, "rows_ingested": rows, "file_type": "B"}
    else:
        # Try to auto-detect by parsing header
        try:
            period_hint = ""
            result = parse_type_a(path, period_hint)
            if result.get("segments"):
                rows = load_type_a(con, result, path.name)
                segs = result["segments"]
                return {"branch": segs[0]["branch"], "period": result.get("period", ""), "rows_ingested": rows, "file_type": "A"}
        except Exception:
            pass
        try:
            result = parse_type_b(path, "")
            if result.get("segments"):
                rows = load_type_b(con, result, path.name)
                segs = result["segments"]
                return {"branch": segs[0]["branch"], "period": result.get("period", ""), "rows_ingested": rows, "file_type": "B"}
        except Exception:
            pass
        raise ValueError(f"Cannot determine file type for: {path.name}. Expected filename like 'Easy_May 26.txt' or 'Easy May_68.txt'.")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    print(f"Database: {DB_PATH}")
    print(f"Data dir: {DATA_DIR}")
    print("Initialising DB...")
    con = init_db(DB_PATH, SCHEMA_SQL)

    # File discovery
    es_typeb_files = sorted(DATA_DIR.glob("23 ES_*.xlsx"))
    es1_files      = sorted(DATA_DIR.glob("ES1_*.xlsx"))
    type_a_files   = sorted(DATA_DIR.glob("Easy_*.txt"))
    type_b_files   = sorted(DATA_DIR.glob("Easy *_*.txt"))

    print(f"\nFound {len(es_typeb_files)} '23 ES_' (per-transaction), "
          f"{len(es1_files)} ES1 (monthly SKU), "
          f"{len(type_a_files)} legacy Type-A, "
          f"{len(type_b_files)} legacy Type-B\n")

    total_sku  = 0
    total_tx   = 0
    total_summ = 0

    # --- 23 ES_*.xlsx (per-transaction) → sales_transaction + sales_month_summary ---
    if es_typeb_files:
        print("=== 23 ES_ (Per-Transaction xlsx) ===")
        for path in es_typeb_files:
            stem = path.stem
            # e.g. '23 ES_May 25', '23 ES_June 25', '23 ES_July 25'
            m = re.match(r"23\s+ES_(\w+)\s+(\d{2})$", stem, re.IGNORECASE)
            period_hint = ""
            if m:
                month_str = m.group(1).lower()
                yy = int(m.group(2))
                month = ENG_MONTHS.get(month_str, 0)
                if month:
                    period_hint = f"{2000+yy:04d}-{month:02d}"
            try:
                result = parse_es_typeb_xlsx(path, period_hint)
                tx_n, summ_n = load_es_typeb(con, result, path.name)
                total_tx   += tx_n
                total_summ += summ_n
            except Exception as exc:
                print(f"  ERROR {path.name}: {exc}")

    # --- ES1_*.xlsx (monthly SKU) → sales_sku_monthly ---
    if es1_files:
        print("\n=== ES1 (Monthly SKU xlsx) ===")
        for path in es1_files:
            stem = path.stem
            period_hint = period_from_filename_es1(stem)
            try:
                result = parse_es1_xlsx(path, period_hint)
                rows = load_es1(con, result, path.name)
                total_sku += rows
            except Exception as exc:
                print(f"  ERROR {path.name}: {exc}")

    # --- Legacy Type A txt ---
    if type_a_files:
        print("\n=== Legacy Type A (Monthly SKU txt) ===")
        for path in type_a_files:
            stem = path.stem
            period_hint = period_from_filename_a(stem)
            try:
                result = parse_type_a(path, period_hint)
                rows = load_type_a(con, result, path.name)
                total_sku += rows
            except Exception as exc:
                print(f"  ERROR {path.name}: {exc}")

    # --- Legacy Type B txt ---
    if type_b_files:
        print("\n=== Legacy Type B (Per-Transaction txt) ===")
        for path in type_b_files:
            stem = path.stem
            period_hint = period_from_filename_b(stem)
            try:
                result = parse_type_b(path, period_hint)
                rows = load_type_b(con, result, path.name)
                total_tx += rows
            except Exception as exc:
                print(f"  ERROR {path.name}: {exc}")

    print(f"\n{'='*60}")
    print(f"Total SKU monthly rows ingested        : {total_sku}")
    print(f"Total transaction rows ingested        : {total_tx}")
    print(f"Total sales_month_summary rows (Easy)  : {total_summ}")

    # --- DB row counts ---
    cur = con.cursor()
    for tbl in ("branches", "categories", "skus",
                "sales_sku_monthly", "sales_transaction", "sales_month_summary"):
        cur.execute(f"SELECT COUNT(*) FROM {tbl}")
        n = cur.fetchone()[0]
        print(f"  {tbl:<30}: {n} rows")

    print("\nDone.")
    con.close()


if __name__ == "__main__":
    main()
