"""
reingest_raw_2026.py — re-ingest Easy POS data from the CLEANSED RAW exports in
    Data for Oshinei/Data Cleasing/raw/

Why a dedicated script (not reingest_easy_safe.py):
  The raw files are FLAT, single-header tables — a different format from the
  multi-section production .xlsx the main parser expects. The production parser
  reads garbage period values from them and loads 0 rows. Also:
    * Sale_No is a PER-DAY counter that RESETS daily, so the same Sale_No recurs
      across days. INSERT OR REPLACE on receipt_no alone would collapse ~70% of
      real transactions. We build a unique key = period|branch|date|sale_no|seq.
    * ES1 has duplicate (branch, product_code) rows → aggregated before insert.

SAFETY:
  * PRESERVES curated cost tables (sku_cost, sku_cost_monthly, bom_ingredients,
    bom_sku_alias, branch_targets, cost_scenario*) — never touches them.
  * Only wipes + rebuilds Easy SALES rows (sales_sku_monthly / sales_transaction
    / sales_month_summary where source='Easy').
  * DRY-RUN by default. Pass --apply to write. --apply makes a safe, consistent
    backup (sqlite backup API, WAL-merged) before any change.

File formats
  ES1_<Mon> <YY>_raw.xlsx   (monthly SKU): Period, Branch, Product_Code,
      Product_Description, Quantity, Net_Amount, Tax1, Tax2, Tax3, Gross_Amount
  23 ES_<Mon> <YY>_raw.xlsx (transactions): Period, Branch, Sale_No, Sale_Date,
      Cashier, Table_Room, No_Customers, Credit_Food, Drinks, Food, Tip, SP_Disc,
      Discount, Service_Charge, Total_Exc_Tax, Tax, Total_Inc_Tax, Cash,
      Credit_Card, Outstanding, Entertainment, Others, Total_Debit

Usage:
  python reingest_raw_2026.py                 # dry-run (parse + report, writes nothing)
  python reingest_raw_2026.py --apply         # backup, then rebuild Easy sales
"""
from __future__ import annotations

import argparse
import re
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

import openpyxl

import ingest_easy as ie  # reuse ENG_MONTHS, get_or_create, get_or_create_sku

HERE     = Path(__file__).parent.resolve()
DB_PATH  = HERE / "oshinei.db"
RAW_DIR  = HERE.parent / "Data for Oshinei" / "Data Cleasing" / "raw"
SOURCE   = "Easy"

CURATED = ("sku_cost", "sku_cost_monthly", "bom_ingredients", "bom_sku_alias",
           "branch_targets", "cost_scenario", "cost_scenario_item")


def period_of(stem: str) -> str:
    """'ES1_Jun 25_raw' / '23 ES_June 25_raw' / 'ES1_Aug  25_raw' -> 'YYYY-MM'."""
    s = stem.replace("_raw", "").replace("_", " ")
    m = re.search(
        r"(jan|feb|mar|apr|may|jun|june|jul|july|aug|sep|oct|nov|dec)\w*\s+(\d{2})",
        s, re.I,
    )
    if not m:
        return ""
    mo = ie.ENG_MONTHS.get(m.group(1).lower(), 0)
    yy = int(m.group(2))
    return f"{2000 + yy:04d}-{mo:02d}" if mo else ""


def _num(v) -> float:
    if v is None:
        return 0.0
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(str(v).replace(",", "").strip() or 0)
    except ValueError:
        return 0.0


def _rows(path: Path):
    """Yield data rows (header skipped) from the first worksheet."""
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb.active
    first = True
    for row in ws.iter_rows(values_only=True):
        if first:
            first = False
            continue
        yield row
    wb.close()


# ---------------------------------------------------------------------------
# Parse
# ---------------------------------------------------------------------------
def parse_es1(path: Path, period: str) -> dict:
    """-> {branch: {product_code: [desc, qty, net_ex, tax, gross_inc]}} aggregated."""
    by_branch: dict = defaultdict(lambda: defaultdict(lambda: [None, 0.0, 0.0, 0.0, 0.0]))
    for r in _rows(path):
        branch = (r[1] or "").strip() if r[1] else ""
        code   = (r[2] or "").strip() if r[2] else ""
        if not branch or not code:
            continue
        rec = by_branch[branch][code]
        rec[0] = (r[3] or "").strip() if r[3] else (rec[0] or code)  # description
        rec[1] += _num(r[4])                       # quantity
        rec[2] += _num(r[5])                       # net (ex-VAT)
        rec[3] += _num(r[6]) + _num(r[7]) + _num(r[8])  # tax1+2+3
        rec[4] += _num(r[9])                       # gross (inc-VAT)
    return {"period": period, "branches": by_branch}


def parse_es_tx(path: Path, period: str) -> dict:
    """-> {branch: [tx-dict, ...]} with a unique receipt key (sale_no resets daily)."""
    by_branch: dict = defaultdict(list)
    seq = 0
    for r in _rows(path):
        branch  = (r[1] or "").strip() if r[1] else ""
        sold_at = (r[3] or "").strip() if r[3] else ""
        if not branch or not sold_at:
            continue
        seq += 1
        sale_no = (str(r[2]).strip() if r[2] is not None else str(seq))
        # Unique receipt: date+sale_no+seq (Sale_No alone repeats daily across days)
        day = sold_at[:10]
        receipt = f"{day}-{sale_no}-{seq}"
        by_branch[branch].append({
            "receipt_no": receipt,
            "sold_at":   sold_at,
            "cashier":   (r[4] or "").strip() if r[4] else "",
            "table_no":  (str(r[5]).strip() if r[5] is not None else ""),
            "cust":      int(_num(r[6])),
            "food":      _num(r[9]),
            "drink":     _num(r[8]),
            "other":     _num(r[21]),  # Others
            "tip":       _num(r[10]),
            "sp_disc":   _num(r[11]),
            "discount":  _num(r[12]),
            "svc":       _num(r[13]),
            "total_ex":  _num(r[14]),  # Total_Exc_Tax
            "total_iv":  _num(r[16]),  # Total_Inc_Tax
        })
    return {"period": period, "branches": by_branch}


# ---------------------------------------------------------------------------
# Load
# ---------------------------------------------------------------------------
def load_es1(con, parsed: dict, fname: str) -> int:
    cur = con.cursor()
    period = parsed["period"]
    n = 0
    for branch, prods in parsed["branches"].items():
        branch_id = ie.get_or_create(cur, "branches", "branch_id", "name", branch)
        cur.execute(
            "DELETE FROM sales_sku_monthly WHERE branch_id=? AND period_month=? AND source=?",
            (branch_id, period, SOURCE),
        )
        for code, (desc, qty, net_ex, tax, gross_iv) in prods.items():
            # Keep existing category/unit for known SKUs; new SKUs get 'Unknown'.
            cur.execute("SELECT sku_id FROM skus WHERE sku_code=?", (code,))
            row = cur.fetchone()
            if row:
                sku_id = row[0]
            else:
                cat_id = ie.get_or_create(cur, "categories", "category_id", "name", "Unknown")
                sku_id = ie.get_or_create_sku(cur, code, desc, cat_id, None)
            cur.execute(
                """INSERT OR REPLACE INTO sales_sku_monthly
                   (branch_id, period_month, sku_id, qty, subtotal_ex_vat,
                    tax_total, total_inc_vat, source)
                   VALUES(?,?,?,?,?,?,?,?)""",
                (branch_id, period, sku_id, qty, round(net_ex, 2),
                 round(tax, 2), round(gross_iv, 2), SOURCE),
            )
            n += 1
    con.commit()
    return n


def load_es_tx(con, parsed: dict, fname: str) -> tuple:
    cur = con.cursor()
    period = parsed["period"]
    tx_n = summ_n = 0
    for branch, txns in parsed["branches"].items():
        if not txns:
            continue
        branch_id = ie.get_or_create(cur, "branches", "branch_id", "name", branch)
        cur.execute(
            "DELETE FROM sales_transaction WHERE branch_id=? AND source=? "
            "AND strftime('%Y-%m', sold_at)=?",
            (branch_id, SOURCE, period),
        )
        agg_gross = agg_disc = agg_svc = 0.0
        agg_cust = n_tx = 0
        net_sales = 0.0
        for t in txns:
            cur.execute(
                """INSERT OR REPLACE INTO sales_transaction
                   (branch_id, receipt_no, sold_at, cashier, table_no,
                    customer_count, food_subtotal, drink_subtotal, other_subtotal,
                    tip, sp_discount, discount, service_charge, total_inc_vat, source)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (branch_id, t["receipt_no"], t["sold_at"], t["cashier"], t["table_no"],
                 t["cust"], t["food"], t["drink"], t["other"], t["tip"],
                 t["sp_disc"], t["discount"], t["svc"], t["total_iv"], SOURCE),
            )
            tx_n += 1
            agg_gross += t["food"] + t["drink"] + t["other"]
            agg_disc  += t["sp_disc"] + t["discount"]
            agg_svc   += t["svc"]
            agg_cust  += t["cust"]
            n_tx      += 1
            # net_sales must be ex-VAT (VAT is never revenue) and ex-service-charge
            # (it sits inside the ex-VAT total) — the same rule as month_summary.BACKFILL_SQL
            net_sales += (t["total_ex"] if t["total_ex"] else t["total_iv"] / 1.07) - (t["svc"] or 0)
        cur.execute(
            "DELETE FROM sales_month_summary WHERE branch_id=? AND period_month=? AND source=?",
            (branch_id, period, SOURCE),
        )
        cur.execute(
            """INSERT INTO sales_month_summary
               (branch_id, period_month, gross_sales, refunds, discounts, voided,
                net_sales, cash, credit_card, transfer, qr, coupon,
                customer_count, transaction_count, source)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (branch_id, period, round(agg_gross, 2), 0.0, round(agg_disc, 2), 0.0,
             round(net_sales, 2), 0.0, 0.0, 0.0, 0.0, 0.0,
             agg_cust, n_tx, SOURCE),
        )
        summ_n += 1
    con.commit()
    return tx_n, summ_n


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def safe_backup(db_path: Path) -> Path:
    from datetime import datetime
    # Date.now-style stamp is fine here — this is a CLI tool, not a workflow.
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    dst = db_path.with_name(f"{db_path.name}.pre_reingest_raw_{ts}")
    src = sqlite3.connect(str(db_path))
    try:
        src.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        out = sqlite3.connect(str(dst))
        with out:
            src.backup(out)
        out.close()
    finally:
        src.close()
    return dst


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="Perform the re-ingest (default: dry-run).")
    args = ap.parse_args()

    if not RAW_DIR.exists():
        print(f"RAW dir not found: {RAW_DIR}", file=sys.stderr)
        return 1

    es1_files = sorted(p for p in RAW_DIR.glob("ES1_*_raw.xlsx"))
    tx_files  = sorted(p for p in RAW_DIR.glob("23 ES_*_raw.xlsx"))
    print(f"RAW dir : {RAW_DIR}")
    print(f"Found    : {len(es1_files)} ES1 (monthly SKU), {len(tx_files)} 23 ES_ (transactions)")
    print(f"Mode     : {'APPLY' if args.apply else 'DRY-RUN (no writes)'}\n")

    # Validate all periods parse before doing anything
    bad = [p.name for p in es1_files + tx_files if not period_of(p.stem)]
    if bad:
        print("ABORT — cannot parse period from: " + ", ".join(bad), file=sys.stderr)
        return 1

    con = sqlite3.connect(str(DB_PATH))
    con.execute("PRAGMA foreign_keys=OFF")

    # Snapshot curated tables so we can prove we didn't touch them
    pre = {t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
           for t in CURATED}
    pre_costsum = con.execute("SELECT COALESCE(SUM(unit_cost),0) FROM sku_cost").fetchone()[0]

    if args.apply:
        bk = safe_backup(DB_PATH)
        print(f"Safe backup -> {bk.name}\n")
        # Wipe Easy sales only
        for tbl in ("sales_sku_monthly", "sales_transaction", "sales_month_summary"):
            con.execute(f"DELETE FROM {tbl} WHERE source=?", (SOURCE,))
        con.commit()

    tot_sku = tot_tx = tot_summ = 0
    errors = []

    for p in tx_files:
        period = period_of(p.stem)
        try:
            parsed = parse_es_tx(p, period)
            if args.apply:
                a, b = load_es_tx(con, parsed, p.name)
            else:
                a = sum(len(v) for v in parsed["branches"].values()); b = len(parsed["branches"])
            tot_tx += a; tot_summ += b
            print(f"  [TX ] {p.name:32} {period}  tx={a:>7,}  branches={len(parsed['branches'])}")
        except Exception as e:
            errors.append(f"{p.name}: {e}")
            print(f"  ERROR {p.name}: {e}")

    for p in es1_files:
        period = period_of(p.stem)
        try:
            parsed = parse_es1(p, period)
            if args.apply:
                a = load_es1(con, parsed, p.name)
            else:
                a = sum(len(v) for v in parsed["branches"].values())
            tot_sku += a
            print(f"  [ES1] {p.name:32} {period}  sku_rows={a:>6,}  branches={len(parsed['branches'])}")
        except Exception as e:
            errors.append(f"{p.name}: {e}")
            print(f"  ERROR {p.name}: {e}")

    # Verify curated tables untouched
    post = {t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in CURATED}
    post_costsum = con.execute("SELECT COALESCE(SUM(unit_cost),0) FROM sku_cost").fetchone()[0]

    print("\n--- curated tables (must be unchanged) ---")
    ok = True
    for t in CURATED:
        flag = "OK" if pre[t] == post[t] else "*** CHANGED ***"
        if pre[t] != post[t]:
            ok = False
        print(f"  {t:20} {pre[t]} -> {post[t]}  {flag}")
    print(f"  sku_cost sum         {pre_costsum:.2f} -> {post_costsum:.2f}  "
          f"{'OK' if abs(pre_costsum-post_costsum)<0.01 else '*** CHANGED ***'}")

    print(f"\nTotals: transactions={tot_tx:,}  month_summary={tot_summ:,}  sku_monthly={tot_sku:,}")
    print(f"Errors: {len(errors)}")

    if args.apply:
        # net sanity
        tot = con.execute(
            "SELECT COALESCE(SUM(subtotal_ex_vat),0) FROM sales_sku_monthly WHERE source='Easy'"
        ).fetchone()[0]
        months = con.execute(
            "SELECT MIN(period_month), MAX(period_month), COUNT(DISTINCT period_month) "
            "FROM sales_sku_monthly WHERE source='Easy'"
        ).fetchone()
        print(f"\nPost-apply: sku_monthly ex-VAT total={tot:,.0f} | months {months[0]}..{months[1]} ({months[2]})")
        con.execute("ANALYZE")
        con.commit()
        if not ok:
            print("\nWARNING: curated tables changed — review immediately.", file=sys.stderr)

    con.close()
    if errors:
        return 2
    print("\nDRY-RUN complete — no data written. Re-run with --apply to perform."
          if not args.apply else "\nDone.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
