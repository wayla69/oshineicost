"""
Remediate sku_cost where unit_cost was loaded as the (inc-VAT) selling price.

See docs/COST_DATA_AUDIT_2026-06-01.md. ~90% of costed SKUs have
unit_cost ≈ ex-VAT-price × 1.07 (median ratio 1.070), i.e. the selling price was
stored as cost, producing ~107% food cost by construction.

This tool, for every Easy-sold non-tier SKU whose
    unit_cost / (ex-VAT price)  >=  --threshold   (default 1.0: cost >= price, impossible)
does one of:
    1. has a BoM recipe cost (SUM bom_ingredients.ingredient_cost > 0)
         -> UPDATE sku_cost.unit_cost = bom_cost          (recoverable)
    2. no BoM cost, and --assume-pct P given
         -> UPDATE sku_cost.unit_cost = ex-VAT price * P   (explicit placeholder)
    3. no BoM cost, no --assume-pct
         -> DELETE the sku_cost row                        (SKU becomes 'uncosted';
                                                            sku_cost.unit_cost is NOT NULL,
                                                            so uncosted = no row)
Every change is written to cost_change_log. Tier SKUs are never touched.

DRY-RUN by default — prints a full report and writes NOTHING.
Pass --apply to perform it (backs up the DB first, runs in one transaction).

Usage:
  python fix_sku_cost_2026.py                 # dry-run, BoM-or-uncosted
  python fix_sku_cost_2026.py --assume-pct 0.32   # dry-run, BoM-or-32%-of-price
  python fix_sku_cost_2026.py --apply         # perform it
"""
from __future__ import annotations

import argparse
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).parent.resolve()
DEFAULT_DB = HERE / "oshinei.db"

# Buffet tier SKUs — their unit_cost is intentionally ignored by _cogs_case; do not touch.
TIER_CODES = ("BUFFET", "BUFFET350", "BUFFET500", "BUFFET590", "BUFFET799", "BUFFETCHILD")

CHANGE_LOG_DDL = """
CREATE TABLE IF NOT EXISTS cost_change_log (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  changed_at   TEXT DEFAULT CURRENT_TIMESTAMP,
  source       TEXT,
  sku_id       INTEGER,
  period_month TEXT,
  old_cost     REAL,
  new_cost     REAL,
  delta        REAL,
  user_note    TEXT
);
"""

SOURCE_TAG = "fix_sku_cost_2026-06-01"


def collect(con: sqlite3.Connection, threshold: float):
    """Return list of candidate dicts (SKUs whose cost >= threshold x ex-VAT price)."""
    tier_ph = ",".join("?" for _ in TIER_CODES)
    rows = con.execute(f"""
        WITH agg AS (
          SELECT sk.sku_id, sk.sku_code, sc.unit_cost AS uc,
                 SUM(ssm.qty)             AS qty,
                 SUM(ssm.subtotal_ex_vat) AS rev_ex
          FROM sales_sku_monthly ssm
          JOIN skus sk     ON sk.sku_id = ssm.sku_id
          JOIN sku_cost sc ON sc.sku_id = ssm.sku_id
          WHERE ssm.source = 'Easy'
            AND sk.sku_code NOT IN ({tier_ph})
          GROUP BY sk.sku_id
          HAVING SUM(ssm.qty) > 0 AND SUM(ssm.subtotal_ex_vat) > 0
        )
        SELECT a.sku_id, a.sku_code, a.uc, a.qty, a.rev_ex,
               a.rev_ex / a.qty AS price_ex,
               (SELECT SUM(ingredient_cost) FROM bom_ingredients b WHERE b.sku_id = a.sku_id) AS bom_cost
        FROM agg a
        WHERE a.uc / (a.rev_ex / a.qty) >= ?
        ORDER BY a.rev_ex DESC
    """, (*TIER_CODES, threshold)).fetchall()

    out = []
    for r in rows:
        price_ex = r["price_ex"]
        bom = r["bom_cost"] if (r["bom_cost"] and r["bom_cost"] > 0) else None
        out.append({
            "sku_id": r["sku_id"], "sku_code": r["sku_code"],
            "old_cost": r["uc"], "price_ex": price_ex, "rev_ex": r["rev_ex"],
            "bom_cost": bom,
        })
    return out


def plan(candidates, assume_pct):
    """Annotate each candidate with action + new_cost. Returns the list."""
    for c in candidates:
        if c["bom_cost"] is not None:
            c["action"] = "bom"
            c["new_cost"] = round(c["bom_cost"], 4)
        elif assume_pct is not None:
            c["action"] = "assume"
            c["new_cost"] = round(c["price_ex"] * assume_pct, 4)
        else:
            c["action"] = "uncost"          # delete row
            c["new_cost"] = None
    return candidates


def report(candidates, threshold, assume_pct):
    by = {"bom": [], "assume": [], "uncost": []}
    for c in candidates:
        by[c["action"]].append(c)
    tot_rev = sum(c["rev_ex"] for c in candidates)
    print(f"\n=== sku_cost remediation plan (threshold ratio >= {threshold}) ===")
    print(f"  candidates (cost >= {threshold} x ex-VAT price): {len(candidates)}")
    print(f"  -> set to BoM recipe cost:        {len(by['bom']):4d}")
    if assume_pct is not None:
        print(f"  -> set to {assume_pct:.0%} of ex-VAT price: {len(by['assume']):4d}")
    print(f"  -> set UNCOSTED (delete row):     {len(by['uncost']):4d}")
    print(f"  revenue under remediation:        {tot_rev/1e6:.1f} M THB")

    print("\n  Sample (top 12 by revenue):")
    print(f'    {"sku":<9}{"old_cost":>9}{"price_ex":>9}{"action":>8}{"new_cost":>10}')
    for c in candidates[:12]:
        nc = "—" if c["new_cost"] is None else f'{c["new_cost"]:.1f}'
        print(f'    {c["sku_code"]:<9}{c["old_cost"]:>9.1f}{c["price_ex"]:>9.1f}{c["action"]:>8}{nc:>10}')


def apply(con: sqlite3.Connection, db_path: Path, candidates):
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = db_path.with_name(f"{db_path.name}.backup_pre_costfix_{ts}")
    shutil.copy2(db_path, backup)
    print(f"\n  backed up -> {backup.name}")

    con.execute(CHANGE_LOG_DDL)
    cur = con.cursor()
    cur.execute("BEGIN")
    upd = dele = 0
    try:
        for c in candidates:
            old = c["old_cost"]
            if c["action"] in ("bom", "assume"):
                cur.execute("UPDATE sku_cost SET unit_cost=?, updated_at=CURRENT_TIMESTAMP WHERE sku_id=?",
                            (c["new_cost"], c["sku_id"]))
                upd += 1
            else:  # uncost -> delete the row
                cur.execute("DELETE FROM sku_cost WHERE sku_id=?", (c["sku_id"],))
                dele += 1
            new = c["new_cost"]
            cur.execute(
                "INSERT INTO cost_change_log(source, sku_id, period_month, old_cost, new_cost, delta, user_note) "
                "VALUES(?,?,?,?,?,?,?)",
                (SOURCE_TAG, c["sku_id"], None, old, new,
                 (None if new is None else round(new - old, 4)),
                 f"price-as-cost remediation via {c['action']}"),
            )
        con.commit()
    except Exception:
        con.rollback()
        raise
    print(f"  applied: {upd} updated, {dele} set uncosted. Logged {upd+dele} rows to cost_change_log.")
    print("  Recommended next: run VACUUM + ANALYZE, then re-check /api/cost/analysis overall_margin.")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="Perform the change (default: dry-run)")
    ap.add_argument("--threshold", type=float, default=1.0,
                    help="Target SKUs where unit_cost/ex-VAT-price >= this (default 1.0)")
    ap.add_argument("--assume-pct", type=float, default=None,
                    help="For SKUs without a BoM cost, set cost = ex-VAT price * this (e.g. 0.32). "
                         "Omit to set them uncosted instead.")
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    args = ap.parse_args()

    if args.assume_pct is not None and not (0 < args.assume_pct < 1):
        ap.error("--assume-pct must be between 0 and 1 (e.g. 0.32)")

    con = sqlite3.connect(str(args.db))
    con.row_factory = sqlite3.Row
    candidates = plan(collect(con, args.threshold), args.assume_pct)
    report(candidates, args.threshold, args.assume_pct)

    if not candidates:
        print("\nNothing to do.")
        return
    if args.apply:
        apply(con, args.db, candidates)
    else:
        print("\n(DRY-RUN — nothing written. Re-run with --apply to perform, "
              "optionally with --assume-pct P.)")
    con.close()


if __name__ == "__main__":
    main()
