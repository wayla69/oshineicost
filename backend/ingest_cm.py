"""
ingest_cm.py
============
Parses CM POS exports for Oshinei chain (legacy POS system):

  23 CM_*.xlsx   Daily Shop Summary (all 24 shops, full Bill/Cust/payment)
                  → sales_month_summary (source='CM POS')
  CM1_*.xlsx     Per-item daily report (all 24 shops, SKU detail)
                  → sales_sku_monthly  (source='CM')

  CM POS_*.xlsx — older 6-shop subset; superseded by 23 CM_ (skipped)
  CM_*.xlsx     — exact duplicate of CM POS_; skipped

Easy POS data (loaded by ingest_easy.py) takes precedence in any month where
both sources exist. Branches that have transitioned to Easy are unified to
canonical Easy names via CM_TO_CANONICAL_BRANCH.

Run from any directory:
    python backend/ingest_cm.py

Re-running is idempotent.
"""

import sqlite3
import sys
import io
import re
from pathlib import Path
from datetime import datetime

import openpyxl

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
HERE           = Path(__file__).parent.resolve()
DB_PATH        = HERE / "oshinei.db"
DATA_DIR       = HERE.parent / "Data for Oshinei"
SOURCE_SUMMARY = "CM POS"
SOURCE_SKU     = "CM"

# ---------------------------------------------------------------------------
# Branch name unification — CM POS (English) → Easy (canonical)
# Each branch that has transitioned to Easy POS keeps a single identity in the
# database so the timeline stays continuous across the system change.
# Easy data takes precedence in any month where both sources exist.
# ---------------------------------------------------------------------------
CM_TO_CANONICAL_BRANCH = {
    "OSHINEI UBON":          "อุบลราชธานี",
    "OSHINEI KORAT2":        "โคราช2",
    "OSHINEI SURIN":         "สุรินทร์",
    "OSHINEI KORAT":         "โคราช1",
    "OSHINEI UDON":          "อุดรธานี",
    "OSHINEI CHONBURI":      "OSHINEI ชลบุรี",
    "OSHINEI RAYONG":        "Oshinei ระยอง",
    "OSHINEI PHETCHABUN":    "เพชรบูรณ์ (1)",
    "OSHINEI PHITSANULOK":   "พิษณุโลก",
    "OSHINEI BURIRAM":       "บุรีรัมย์ (1)",
    "OSHINEI NAKHON PHANOM": "นครพนม",
    "OSHINEI KHON KAEN":     "ขอนแก่น (1)",
    "OSHINEI SAKON NAKHON":  "สกลนคร",
    "OSHINEI CHACHOENGSAO":  "ฉะเชิงเทรา",
    "OSHINEI MAHA SARAKHAM": "มหาสารคาม",
    "OSHINEI MUKDAHAN":      "มุกดาหาร1",
    "OSHINEI LOPBURI":       "ลพบุรี (1)",
    "OSHINEI NAKHON SAWAN":  "นครสวรรค์(oshinei)",
    "OSHINEI NAKHON PATHOM": "นครปฐม(Oshinei)",
    "OSHINEI CHIANG MAI":    "เชียงใหม่",
    "OSHINEI ROI-ET":        "ร้อยเอ็ด(oshinei)",
    "OSHINEI SUPHANBURI":    "สุพรรณบุรี",
    "OSHINEI SISAKET":       "ศรีสะเกษ",
    "Oshinei Ayutthaya":     "ร้าน Oshinei สาขา อยุธยา",
}


def canonical_branch(name: str) -> str:
    return CM_TO_CANONICAL_BRANCH.get(name, name)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def safe_float(v) -> float:
    if v is None:
        return 0.0
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(str(v).strip().replace(",", ""))
    except (ValueError, TypeError):
        return 0.0


def parse_date_period(v) -> str:
    """Convert Date Period value (DD/MM/YYYY string or datetime) → 'YYYY-MM'."""
    if v is None:
        return ""
    if isinstance(v, datetime):
        return f"{v.year:04d}-{v.month:02d}"
    s = str(v).strip()
    m = re.match(r"(\d{1,2})/(\d{1,2})/(\d{4})", s)
    if m:
        return f"{m.group(3)}-{int(m.group(2)):02d}"
    return ""


def get_or_create_branch(cur: sqlite3.Cursor, name: str) -> int:
    cur.execute("SELECT branch_id FROM branches WHERE name=?", (name,))
    row = cur.fetchone()
    if row:
        return row[0]
    cur.execute("INSERT INTO branches(name) VALUES(?)", (name,))
    return cur.lastrowid


def get_or_create_category(cur: sqlite3.Cursor, name: str) -> int:
    cur.execute("SELECT category_id FROM categories WHERE name=?", (name,))
    row = cur.fetchone()
    if row:
        return row[0]
    cur.execute("INSERT INTO categories(name) VALUES(?)", (name,))
    return cur.lastrowid


def get_or_create_sku(cur: sqlite3.Cursor, sku_code: str,
                      description: str, category_id: int) -> int:
    cur.execute("SELECT sku_id FROM skus WHERE sku_code=?", (sku_code,))
    row = cur.fetchone()
    if row:
        return row[0]
    cur.execute(
        "INSERT INTO skus(sku_code, description, category_id) VALUES(?,?,?)",
        (sku_code, description or None, category_id),
    )
    return cur.lastrowid


# ---------------------------------------------------------------------------
# Parser — CM POS_*.xlsx (Daily Shop Summary, 6 legacy shops)
# ---------------------------------------------------------------------------
def parse_cm_pos_file(path: Path) -> dict:
    """
    Returns {(shop_name, period_month): {
        gross_sales, discounts, net_sales, service_charge,
        cash, credit_card, transfer, qr,
        customer_count, transaction_count
    }}
    Aggregated from daily per-mode rows.
    """
    wb = openpyxl.load_workbook(str(path), read_only=True, data_only=True)
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))
    wb.close()

    # Find header row by locating 'setShopCode' marker
    hdr_idx = None
    col_map: dict[str, int] = {}
    for i, r in enumerate(rows):
        if r and str(r[0]).strip() == "setShopCode":
            hdr_idx = i
            col_map = {str(v).strip(): j for j, v in enumerate(r) if v is not None}
            break
    if hdr_idx is None:
        raise ValueError(f"Header 'setShopCode' not found in {path.name}")

    ci_name  = col_map.get("setShopName",   1)
    ci_date  = col_map.get("Date Period",    2)
    ci_bill  = col_map.get("Bill Count",     4)
    ci_cust  = col_map.get("Cust Count",     5)
    ci_gross = col_map.get("Gross Sales",    6)
    ci_disc  = col_map.get("Discount",       7)
    ci_net   = col_map.get("Net Sales",     10)
    ci_svc   = col_map.get("Service Charge",11)
    ci_cash  = col_map.get("CASH",          18)

    def zero_month():
        return {"gross_sales": 0.0, "discounts": 0.0, "net_sales": 0.0,
                "service_charge": 0.0, "cash": 0.0, "credit_card": 0.0,
                "transfer": 0.0, "qr": 0.0,
                "customer_count": 0, "transaction_count": 0}

    agg: dict[tuple, dict] = {}

    for r in rows[hdr_idx + 1:]:
        if not r or r[0] is None:
            continue
        shop_name = str(r[ci_name]).strip() if r[ci_name] else ""
        if not shop_name:
            continue
        period = parse_date_period(r[ci_date])
        if not period:
            continue

        key = (shop_name, period)
        if key not in agg:
            agg[key] = zero_month()
        m = agg[key]
        m["gross_sales"]       += safe_float(r[ci_gross])
        m["discounts"]         += safe_float(r[ci_disc])
        m["net_sales"]         += safe_float(r[ci_net])
        m["service_charge"]    += safe_float(r[ci_svc])
        m["cash"]              += safe_float(r[ci_cash])
        m["customer_count"]    += int(safe_float(r[ci_cust]))
        m["transaction_count"] += int(safe_float(r[ci_bill]))

        # Aggregate card / transfer / QR columns by header name
        for col_name, idx in col_map.items():
            if idx >= len(r) or r[idx] is None:
                continue
            v = safe_float(r[idx])
            if v == 0:
                continue
            nl = col_name.lower()
            if any(k in nl for k in ("card", "visa", "master", "jcb", "bay",
                                     "union", "k+ shop")):
                m["credit_card"] += v
            elif any(k in nl for k in ("โอน", "transfer")):
                m["transfer"] += v
            elif any(k in nl for k in ("qr", "promptpay", "prompt")):
                m["qr"] += v

    return agg


def load_cm_pos(con: sqlite3.Connection, agg: dict, filename: str) -> int:
    cur = con.cursor()

    cur.execute(
        """SELECT b.name, MIN(sms.period_month) FROM sales_month_summary sms
           JOIN branches b ON b.branch_id = sms.branch_id
           WHERE sms.source='Easy' GROUP BY b.name"""
    )
    easy_starts: dict[str, str] = dict(cur.fetchall())

    inserted = 0
    skipped  = 0
    for (shop_name, period), m in agg.items():
        canon = canonical_branch(shop_name)
        if (s := easy_starts.get(canon)) and period >= s:
            skipped += 1
            continue
        branch_id = get_or_create_branch(cur, canon)
        cur.execute(
            "DELETE FROM sales_month_summary WHERE branch_id=? AND period_month=? AND source=?",
            (branch_id, period, SOURCE_SUMMARY),
        )
        cur.execute(
            """INSERT INTO sales_month_summary
               (branch_id, period_month, gross_sales, refunds, discounts,
                voided, net_sales, cash, credit_card, transfer, qr, coupon,
                customer_count, transaction_count, source)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (branch_id, period,
             round(m["gross_sales"], 2), 0.0,
             round(m["discounts"], 2), 0.0,
             round(m["net_sales"], 2),
             round(m["cash"], 2),
             round(m["credit_card"], 2),
             round(m["transfer"], 2),
             round(m["qr"], 2),
             0.0,
             m["customer_count"],
             m["transaction_count"],
             SOURCE_SUMMARY),
        )
        inserted += 1
    con.commit()
    suffix = f" (skipped {skipped} owned by Easy)" if skipped else ""
    print(f"  {filename}: {inserted} branch-months{suffix}")
    return inserted


# ---------------------------------------------------------------------------
# Parser — CM1_*.xlsx (Item Report, all 24 shops)
# ---------------------------------------------------------------------------
def parse_cm1_file(path: Path):
    """
    Parse a CM1_*.xlsx file.
    Returns (month_agg, sku_agg):
      month_agg: {(shop_name, period_month): {gross_sales, discounts, net_sales, service_charge}}
      sku_agg:   {(shop_name, period_month, item_code): {desc, qty, base_sales, vat, total_sales}}
    """
    wb = openpyxl.load_workbook(str(path), read_only=True, data_only=True)
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))
    wb.close()

    # Find header row by locating 'Shop Code' in col 0
    hdr_idx = None
    col_map = {}
    for i, r in enumerate(rows):
        if r and str(r[0]).strip() == "Shop Code":
            hdr_idx = i
            col_map = {str(v).strip(): j for j, v in enumerate(r) if v is not None}
            break

    if hdr_idx is None:
        raise ValueError(f"Header row not found in {path.name}")

    ci_name  = col_map.get("Shop Name",      1)
    ci_date  = col_map.get("Date Period",     2)
    ci_item  = col_map.get("Item Code",       4)
    ci_desc1 = col_map.get("Description1",    5)
    ci_qty   = col_map.get("Total Qty",      10)
    ci_gross = col_map.get("Gross Sales",    12)
    ci_disc  = col_map.get("Discount",       13)
    ci_base  = col_map.get("Base Sales",     14)
    ci_svc   = col_map.get("Service Charge", 15)
    ci_vat   = col_map.get("Vat",            16)
    ci_net   = col_map.get("Net Sales",      17)
    ci_total = col_map.get("Total Sales",    18)

    def zero_month():
        return {"gross_sales": 0.0, "discounts": 0.0, "net_sales": 0.0, "service_charge": 0.0}

    def zero_sku(desc):
        return {"desc": desc, "qty": 0.0, "base_sales": 0.0, "vat": 0.0, "total_sales": 0.0}

    month_agg: dict[tuple, dict] = {}
    sku_agg:   dict[tuple, dict] = {}

    for r in rows[hdr_idx + 1:]:
        if not r or r[1] is None:
            continue

        shop_name = str(r[ci_name]).strip() if r[ci_name] else ""
        if not shop_name:
            continue

        period = parse_date_period(r[ci_date])
        if not period:
            continue

        # Monthly summary aggregation
        mk = (shop_name, period)
        if mk not in month_agg:
            month_agg[mk] = zero_month()
        m = month_agg[mk]
        m["gross_sales"]    += safe_float(r[ci_gross])
        m["discounts"]      += safe_float(r[ci_disc])
        m["net_sales"]      += safe_float(r[ci_net])
        m["service_charge"] += safe_float(r[ci_svc])

        # SKU aggregation
        item_code = str(r[ci_item]).strip() if r[ci_item] else ""
        if item_code and re.search(r"[A-Za-z0-9]", item_code):
            desc1 = str(r[ci_desc1]).strip() if r[ci_desc1] else ""
            sk = (shop_name, period, item_code)
            if sk not in sku_agg:
                sku_agg[sk] = zero_sku(desc1)
            s = sku_agg[sk]
            s["qty"]         += safe_float(r[ci_qty])
            s["base_sales"]  += safe_float(r[ci_base])
            s["vat"]         += safe_float(r[ci_vat])
            s["total_sales"] += safe_float(r[ci_total])

    return month_agg, sku_agg


# ---------------------------------------------------------------------------
# DB loader
# ---------------------------------------------------------------------------
def load_cm1(con: sqlite3.Connection, month_agg: dict, sku_agg: dict,
             filename: str) -> int:
    cur = con.cursor()
    unknown_cat_id = get_or_create_category(cur, "Unknown")

    # Map of canonical_branch_name → earliest Easy period (yyyy-mm).
    # CM POS data is suppressed for any (branch, month) on/after this date so
    # Easy POS owns the transition month and everything after.
    cur.execute(
        """SELECT b.name, MIN(sms.period_month)
           FROM sales_month_summary sms
           JOIN branches b ON b.branch_id = sms.branch_id
           WHERE sms.source='Easy'
           GROUP BY b.name"""
    )
    easy_starts: dict[str, str] = dict(cur.fetchall())

    def easy_owns(branch_name: str, period: str) -> bool:
        start = easy_starts.get(branch_name)
        return start is not None and period >= start

    inserted_months = 0
    skipped_months  = 0

    # sales_month_summary — fallback only. CM POS_*.xlsx has already populated
    # the 6 legacy shops with full data (Bill/Cust/payment). Don't overwrite.
    for (shop_name, period), m in month_agg.items():
        canon = canonical_branch(shop_name)
        if easy_owns(canon, period):
            skipped_months += 1
            continue
        branch_id = get_or_create_branch(cur, canon)
        cur.execute(
            "SELECT 1 FROM sales_month_summary WHERE branch_id=? AND period_month=? AND source=?",
            (branch_id, period, SOURCE_SUMMARY),
        )
        if cur.fetchone():
            continue  # CM POS_ already has full row
        cur.execute(
            """INSERT INTO sales_month_summary
               (branch_id, period_month, gross_sales, refunds, discounts,
                voided, net_sales, cash, credit_card, transfer, qr, coupon,
                customer_count, transaction_count, source)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (branch_id, period,
             round(m["gross_sales"], 2), 0.0,
             round(m["discounts"], 2), 0.0,
             round(m["net_sales"], 2),
             0.0, 0.0, 0.0, 0.0, 0.0, 0, 0,
             SOURCE_SUMMARY),
        )
        inserted_months += 1

    # sales_sku_monthly
    for (shop_name, period, item_code), s in sku_agg.items():
        canon = canonical_branch(shop_name)
        if easy_owns(canon, period):
            continue
        branch_id = get_or_create_branch(cur, canon)
        sku_id = get_or_create_sku(cur, item_code, s["desc"], unknown_cat_id)
        cur.execute(
            "DELETE FROM sales_sku_monthly WHERE branch_id=? AND period_month=? AND sku_id=? AND source=?",
            (branch_id, period, sku_id, SOURCE_SKU),
        )
        cur.execute(
            """INSERT INTO sales_sku_monthly
               (branch_id, period_month, sku_id, qty, subtotal_ex_vat,
                tax_total, total_inc_vat, source)
               VALUES(?,?,?,?,?,?,?,?)""",
            (branch_id, period, sku_id,
             round(s["qty"], 2),
             round(s["base_sales"], 2),
             round(s["vat"], 2),
             round(s["total_sales"], 2),
             SOURCE_SKU),
        )

    con.commit()
    suffix = f" (skipped {skipped_months} months owned by Easy)" if skipped_months else ""
    print(f"  {filename}: {inserted_months} branch-months, {len(sku_agg)} sku-months{suffix}")
    return inserted_months


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    if not DB_PATH.exists():
        print(f"DB not found: {DB_PATH}")
        print("Run ingest_easy.py first to create the DB.")
        sys.exit(1)

    con = sqlite3.connect(str(DB_PATH))

    # ---- Step 1: 23 CM_*.xlsx  (Daily Shop Summary, all 24 shops, full Bill/Cust/payment)
    pos_files = sorted(DATA_DIR.glob("23 CM_*.xlsx"))
    print(f"Found {len(pos_files)} '23 CM_' files (daily shop summary, 24 shops)\n")
    pos_rows = 0
    for path in pos_files:
        print(f"Parsing {path.name}...")
        try:
            agg = parse_cm_pos_file(path)
            pos_rows += load_cm_pos(con, agg, path.name)
        except Exception as exc:
            print(f"  ERROR: {exc}")

    # ---- Step 2: CM1_*.xlsx  (Item Report, all 24 shops, SKU detail)
    cm1_files = sorted(DATA_DIR.glob("CM1_*.xlsx"))
    print(f"\nFound {len(cm1_files)} CM1 files (item report)\n")
    cm1_rows = 0
    for path in cm1_files:
        print(f"Parsing {path.name}...")
        try:
            month_agg, sku_agg = parse_cm1_file(path)
            cm1_rows += load_cm1(con, month_agg, sku_agg, path.name)
        except Exception as exc:
            print(f"  ERROR: {exc}")

    print(f"\nCM POS_ summary rows : {pos_rows}")
    print(f"CM1 fallback rows    : {cm1_rows}")

    # Summary report
    cur = con.cursor()
    cur.execute(
        """SELECT b.name, sms.period_month,
                  round(sms.gross_sales,0), round(sms.net_sales,0)
           FROM sales_month_summary sms
           JOIN branches b ON b.branch_id = sms.branch_id
           WHERE sms.source='CM POS'
           ORDER BY b.name, sms.period_month"""
    )
    print(f"\n{'Branch':<30} {'Period':<9} {'Gross':>14} {'Net':>14}")
    print("-" * 70)
    for r in cur.fetchall():
        print(f"{r[0]:<30} {r[1]:<9} {r[2]:>14,.0f} {r[3]:>14,.0f}")

    con.close()
    print("\nDone.")


if __name__ == "__main__":
    main()
