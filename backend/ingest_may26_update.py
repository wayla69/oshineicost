"""
ingest_may26_update.py — incremental, period-scoped update of 2026-05 (May 26)
Easy POS sales from the two fresh full-chain raw exports:

    Data for Oshinei/Data Cleasing/raw/
        บิล Sale ทุกสาขา May 26_raw.xlsx   (transactions — 23-col flat table)
        เมนูทุกสาขา May 26_raw.xlsx        (monthly SKU   — 10-col flat table)

Why a dedicated script (NOT reingest_raw_2026.py --apply):
  reingest_raw_2026.py wipes ALL Easy sales then rebuilds from the ES1_/23 ES_
  glob — it would not even see these Thai-named files, and would be destructive.
  This script touches ONLY the 2026-05 period and ONLY the 26 branches present
  in the files (period+branch-scoped DELETE then INSERT — idempotent).

Branch-name safety (the real trap here):
  The POS export uses full names ('OSHINEI ชลบุรี', 'ขอนแก่น (1)', ...) for 11
  branches, but the live DB stores SHORT canonical names ('ชลบุรี', 'ขอนแก่น')
  — they were renamed via the admin UI after the original load, branch_id kept.
  Loading the raw names verbatim would CREATE 11 duplicate branches and fail to
  delete the existing May rows (different branch_id) → double-counting.
  We normalize file names -> canonical via BRANCH_ALIAS, and REFUSE to create
  any branch that doesn't already exist (abort instead).

SAFETY:
  * PRESERVES all curated cost tables (verified unchanged after the run).
  * DRY-RUN by default. --apply makes a consistent sqlite-backup first.

Usage:
  python ingest_may26_update.py            # dry-run (parse + validate, no writes)
  python ingest_may26_update.py --apply    # backup, then update 2026-05 only
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

import ingest_easy as ie
import reingest_raw_2026 as rr

HERE    = Path(__file__).parent.resolve()
DB_PATH = HERE / "oshinei.db"
RAW_DIR = HERE.parent / "Data for Oshinei" / "Data Cleasing" / "raw"
SOURCE  = "Easy"
PERIOD  = "2026-05"

TX_FILE   = "บิล Sale ทุกสาขา May 26_raw.xlsx"   # transactions
MENU_FILE = "เมนูทุกสาขา May 26_raw.xlsx"          # monthly SKU

# POS-export full name -> live-DB canonical short name.
# Only the 11 that differ; the other 15 match exactly and need no entry.
BRANCH_ALIAS = {
    "OSHINEI ชลบุรี":            "ชลบุรี",
    "Oshinei ระยอง":            "ระยอง",
    "ขอนแก่น (1)":              "ขอนแก่น",
    "นครปฐม(Oshinei)":          "นครปฐม",
    "นครสวรรค์(oshinei)":        "นครสวรรค์",
    "บุรีรัมย์ (1)":             "บุรีรัมย์",
    "มุกดาหาร1":                "มุกดาหาร",
    "ร้อยเอ็ด(oshinei)":         "ร้อยเอ็ด",
    "ร้าน Oshinei สาขา อยุธยา":  "อยุธยา",
    "ลพบุรี (1)":               "ลพบุรี",
    "เพชรบูรณ์ (1)":            "เพชรบูรณ์",
}

CURATED = rr.CURATED


def _canon(name: str) -> str:
    return BRANCH_ALIAS.get(name, name)


def _remap_branches(parsed: dict) -> dict:
    """Return parsed with branch keys mapped to canonical DB names (merging dups)."""
    from collections import defaultdict
    new: dict = {}
    for branch, payload in parsed["branches"].items():
        c = _canon(branch)
        if c not in new:
            new[c] = payload
        else:
            # Two raw names collapsed to one canonical — merge.
            if isinstance(payload, list):
                new[c].extend(payload)
            else:  # dict of product_code -> [desc, qty, net, tax, gross]
                for code, rec in payload.items():
                    if code in new[c]:
                        for i in range(1, 5):
                            new[c][code][i] += rec[i]
                        new[c][code][0] = new[c][code][0] or rec[0]
                    else:
                        new[c][code] = rec
    return {"period": parsed["period"], "branches": new}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="Perform the update (default: dry-run).")
    args = ap.parse_args()

    tx_path   = RAW_DIR / TX_FILE
    menu_path = RAW_DIR / MENU_FILE
    for p in (tx_path, menu_path):
        if not p.exists():
            print(f"ABORT — file not found: {p}", file=sys.stderr)
            return 1

    print(f"DB     : {DB_PATH}")
    print(f"Period : {PERIOD}")
    print(f"Mode   : {'APPLY' if args.apply else 'DRY-RUN (no writes)'}\n")

    # Parse + normalize branch names
    tx_parsed   = _remap_branches(rr.parse_es_tx(tx_path, PERIOD))
    menu_parsed = _remap_branches(rr.parse_es1(menu_path, PERIOD))

    con = sqlite3.connect(str(DB_PATH))
    con.execute("PRAGMA foreign_keys=OFF")
    cur = con.cursor()

    db_branches = {r[0] for r in cur.execute("SELECT name FROM branches")}

    # Hard safety gate: every branch we are about to load MUST already exist.
    file_branches = set(tx_parsed["branches"]) | set(menu_parsed["branches"])
    unknown = sorted(b for b in file_branches if b not in db_branches)
    if unknown:
        print("ABORT — these branch names are not in the DB (would create duplicates):",
              file=sys.stderr)
        for b in unknown:
            print(f"   {b!r}", file=sys.stderr)
        print("Add a BRANCH_ALIAS entry mapping each to its canonical DB name.",
              file=sys.stderr)
        con.close()
        return 1

    print(f"Branches in files: {len(file_branches)} (all matched to existing DB branches)\n")

    # Snapshot curated tables
    pre = {t: cur.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in CURATED}
    pre_costsum = cur.execute("SELECT COALESCE(SUM(unit_cost),0) FROM sku_cost").fetchone()[0]

    # Pre-change counts for 2026-05
    def _counts():
        return (
            cur.execute("SELECT COUNT(*) FROM sales_transaction WHERE source=? "
                        "AND strftime('%Y-%m',sold_at)=?", (SOURCE, PERIOD)).fetchone()[0],
            cur.execute("SELECT COUNT(*) FROM sales_sku_monthly WHERE source=? "
                        "AND period_month=?", (SOURCE, PERIOD)).fetchone()[0],
            cur.execute("SELECT COUNT(*) FROM sales_month_summary WHERE source=? "
                        "AND period_month=?", (SOURCE, PERIOD)).fetchone()[0],
        )
    pre_tx, pre_sku, pre_summ = _counts()
    print(f"Existing 2026-05 Easy rows  : tx={pre_tx:,}  sku_monthly={pre_sku:,}  summary={pre_summ}")

    file_tx  = sum(len(v) for v in tx_parsed["branches"].values())
    file_sku = sum(len(v) for v in menu_parsed["branches"].values())
    print(f"Incoming from files         : tx={file_tx:,}  sku_monthly={file_sku:,}  "
          f"branches={len(file_branches)}\n")

    if not args.apply:
        print("DRY-RUN complete — no data written. Re-run with --apply to perform.")
        con.close()
        return 0

    # ---- APPLY ----
    bk = rr.safe_backup(DB_PATH)
    print(f"Safe backup -> {bk.name}\n")

    tx_n, summ_n = rr.load_es_tx(con, tx_parsed, TX_FILE)
    sku_n = rr.load_es1(con, menu_parsed, MENU_FILE)

    # Verify curated untouched
    post = {t: cur.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in CURATED}
    post_costsum = cur.execute("SELECT COALESCE(SUM(unit_cost),0) FROM sku_cost").fetchone()[0]

    print("--- curated tables (must be unchanged) ---")
    ok = True
    for t in CURATED:
        same = pre[t] == post[t]
        ok = ok and same
        print(f"  {t:20} {pre[t]} -> {post[t]}  {'OK' if same else '*** CHANGED ***'}")
    cs_ok = abs(pre_costsum - post_costsum) < 0.01
    ok = ok and cs_ok
    print(f"  sku_cost sum         {pre_costsum:.2f} -> {post_costsum:.2f}  "
          f"{'OK' if cs_ok else '*** CHANGED ***'}")

    # No duplicate branches created
    nbr = cur.execute("SELECT COUNT(*) FROM branches").fetchone()[0]
    print(f"\nBranch count: {len(db_branches)} -> {nbr}  "
          f"{'OK' if nbr == len(db_branches) else '*** CHANGED ***'}")

    post_tx, post_sku, post_summ = _counts()
    print(f"\n2026-05 Easy rows AFTER     : tx={post_tx:,}  sku_monthly={post_sku:,}  summary={post_summ}")
    print(f"Loaded: tx={tx_n:,}  sku_monthly={sku_n:,}  month_summary={summ_n}")

    con.execute("ANALYZE")
    con.commit()
    con.close()

    if not ok or nbr != len(db_branches):
        print("\nWARNING: integrity check failed — review immediately.", file=sys.stderr)
        return 2
    print("\nDone.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
