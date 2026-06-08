"""
Phase 2 — Database deep-clean (OVERHAUL_PLAN.md)

Cleans three categories of junk from the categories table:

  1. EMPTY categories  — 7 categories that have no sales revenue at all
     (A LA CARTE, DISCOUNT, Payment, Other, Unknown, Voucher, Oshinei หม้อและน้ำซุป)
     Any SKUs in them are re-pointed to the 'Oshinei other' fallback before deletion.

  2. TEST categories   — ~12 tiny categories created during testing
     ((YAKI)*, (Renshin)*, (NEW)*, UBN BADMINTON, etc.)
     Same: SKUs re-pointed to 'Oshinei other', then category deleted.

  3. ORPHAN SKUs       — skus with no category_id (null) are pointed to 'Oshinei other'.

After deletion: VACUUM + ANALYZE.

Revenue MUST stay identical after the clean — the script verifies this automatically.

DRY-RUN by default.  Pass --apply to execute (backs up DB first).

Usage (on the Fly machine):
  cd /app && python backend/phase2_db_clean.py
  cd /app && python backend/phase2_db_clean.py --apply
  cd /app && python backend/phase2_db_clean.py --db /data/oshinei.db
  cd /app && python backend/phase2_db_clean.py --db /data/oshinei.db --apply
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).parent.resolve()
DEFAULT_DB = Path(
    __import__("os").environ.get("OSHINEI_DB", str(HERE / "oshinei.db"))
)

# ── Categories to treat as "empty / junk" and delete ──────────────────────────
EMPTY_CATEGORY_NAMES = {
    "A LA CARTE",
    "DISCOUNT",
    "Payment",
    "Other",
    "Unknown",
    "Voucher",
    "Oshinei หม้อและน้ำซุป",
}

# Prefix patterns for test categories (matched with LIKE 'prefix%')
TEST_CATEGORY_PREFIXES = [
    "(YAKI)",
    "(Renshin)",
    "(NEW)",
    "UBN BADMINTON",
]

FALLBACK_CATEGORY = "Oshinei other"


# ── Helpers ────────────────────────────────────────────────────────────────────

def safe_backup(db_path: Path) -> Path:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    dst = db_path.with_name(f"{db_path.name}.safebak_phase2_{ts}")
    src = sqlite3.connect(str(db_path))
    try:
        src.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        out = sqlite3.connect(str(dst))
        with out:
            src.backup(out)
        out.close()
    finally:
        src.close()
    print(f"  backup -> {dst.name}  ({dst.stat().st_size / 1024 / 1024:.1f} MiB)")
    return dst


def get_or_create_fallback(cur: sqlite3.Cursor) -> int:
    row = cur.execute(
        "SELECT category_id FROM categories WHERE name = ?", (FALLBACK_CATEGORY,)
    ).fetchone()
    if row:
        return row[0]
    cur.execute(
        "INSERT INTO categories (name) VALUES (?)", (FALLBACK_CATEGORY,)
    )
    fid = cur.lastrowid
    print(f"  created fallback category '{FALLBACK_CATEGORY}' (id={fid})")
    return fid


def total_revenue(con: sqlite3.Connection) -> float:
    return con.execute(
        "SELECT COALESCE(SUM(total_inc_vat), 0) FROM sales_sku_monthly"
    ).fetchone()[0]


# ── Analysis ───────────────────────────────────────────────────────────────────

def gather_targets(con: sqlite3.Connection) -> list[dict]:
    """Return one dict per category that should be deleted, with context."""
    # Build LIKE clauses for test prefixes
    like_clauses = " OR ".join(
        f"c.name LIKE ?" for _ in TEST_CATEGORY_PREFIXES
    )
    like_args = [f"{p}%" for p in TEST_CATEGORY_PREFIXES]

    name_placeholders = ",".join("?" for _ in EMPTY_CATEGORY_NAMES)

    query = f"""
        SELECT
            c.category_id,
            c.name                                   AS cat_name,
            COUNT(DISTINCT sk.sku_id)                AS sku_count,
            COALESCE(SUM(ssm.total_inc_vat), 0)      AS revenue,
            COALESCE(SUM(ssm.qty), 0)                AS qty_sold,
            CASE
                WHEN c.name IN ({name_placeholders}) THEN 'empty'
                ELSE 'test'
            END                                      AS reason
        FROM categories c
        LEFT JOIN skus sk      ON sk.category_id  = c.category_id
        LEFT JOIN sales_sku_monthly ssm ON ssm.sku_id = sk.sku_id
        WHERE c.name IN ({name_placeholders})
           OR {like_clauses}
        GROUP BY c.category_id
        ORDER BY reason, revenue DESC
    """
    args = (
        list(EMPTY_CATEGORY_NAMES)   # for IN placeholders (name)
        + list(EMPTY_CATEGORY_NAMES) # for IN placeholders (reason CASE)
        + like_args
    )
    rows = con.execute(query, args).fetchall()
    return [dict(r) for r in rows]


def gather_orphan_skus(con: sqlite3.Connection) -> int:
    return con.execute(
        "SELECT COUNT(*) FROM skus WHERE category_id IS NULL"
    ).fetchone()[0]


# ── Report ─────────────────────────────────────────────────────────────────────

def print_report(targets: list[dict], orphan_count: int, total_rev: float) -> None:
    print("\n" + "=" * 70)
    print("  PHASE 2 DB CLEAN — dry-run report")
    print("=" * 70)
    print(f"\n  Total revenue in DB:  ฿{total_rev / 1e6:.3f} M  (must stay unchanged)\n")

    if not targets:
        print("  No matching categories found — nothing to do.")
    else:
        print(f"  {'ID':>5}  {'Category':<35}  {'Reason':>6}  {'SKUs':>4}  {'Revenue':>12}  {'Qty':>8}")
        print(f"  {'-'*5}  {'-'*35}  {'-'*6}  {'-'*4}  {'-'*12}  {'-'*8}")
        for t in targets:
            print(
                f"  {t['category_id']:>5}  {t['cat_name']:<35}  "
                f"{t['reason']:>6}  {t['sku_count']:>4}  "
                f"฿{t['revenue'] / 1e3:>10.1f}K  {t['qty_sold']:>8.0f}"
            )
        skus_to_move = sum(t["sku_count"] for t in targets)
        rev_in_targets = sum(t["revenue"] for t in targets)
        print(f"\n  Categories to delete: {len(targets)}")
        print(f"  SKUs to re-point to '{FALLBACK_CATEGORY}': {skus_to_move}")
        print(f"  Revenue in these categories: ฿{rev_in_targets / 1e6:.3f} M")
        if rev_in_targets > 0:
            print(f"  NOTE: These categories have revenue — their SKUs will be")
            print(f"        re-pointed (not deleted), so revenue stays intact.")

    if orphan_count:
        print(f"\n  Orphan SKUs (no category): {orphan_count} → will point to '{FALLBACK_CATEGORY}'")

    print()


# ── Apply ──────────────────────────────────────────────────────────────────────

def apply_clean(con: sqlite3.Connection, targets: list[dict], orphan_count: int) -> None:
    cur = con.cursor()
    fallback_id = get_or_create_fallback(cur)

    cur.execute("BEGIN")
    try:
        for t in targets:
            cid = t["category_id"]
            name = t["cat_name"]

            # Re-point any SKUs that live in this category
            if t["sku_count"] > 0:
                cur.execute(
                    "UPDATE skus SET category_id = ? WHERE category_id = ?",
                    (fallback_id, cid),
                )
                print(f"  re-pointed {t['sku_count']} SKUs from '{name}' -> '{FALLBACK_CATEGORY}'")

            cur.execute("DELETE FROM categories WHERE category_id = ?", (cid,))
            print(f"  deleted category '{name}' (id={cid})")

        if orphan_count:
            cur.execute(
                "UPDATE skus SET category_id = ? WHERE category_id IS NULL",
                (fallback_id,),
            )
            print(f"  re-pointed {orphan_count} orphan SKUs -> '{FALLBACK_CATEGORY}'")

        con.commit()
    except Exception:
        con.rollback()
        raise

    print("\n  Running VACUUM + ANALYZE …")
    con.isolation_level = None
    con.execute("VACUUM")
    con.isolation_level = ""
    con.execute("ANALYZE")
    con.execute("PRAGMA optimize")
    con.commit()
    print("  done.")


# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--apply", action="store_true",
        help="Perform the cleanup (default: dry-run only)"
    )
    ap.add_argument(
        "--db", type=Path, default=DEFAULT_DB,
        help=f"Path to oshinei.db (default: {DEFAULT_DB})"
    )
    args = ap.parse_args()

    if not args.db.exists():
        print(f"ERROR: DB not found at {args.db}", file=sys.stderr)
        return 1

    con = sqlite3.connect(str(args.db))
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")

    targets = gather_targets(con)
    orphan_count = gather_orphan_skus(con)
    rev_before = total_revenue(con)

    print_report(targets, orphan_count, rev_before)

    if not targets and not orphan_count:
        print("Nothing to clean up.")
        con.close()
        return 0

    if not args.apply:
        print("(DRY-RUN — nothing written. Re-run with --apply to execute.)\n")
        con.close()
        return 0

    # ── Live run ──
    print("Backing up before any mutation …")
    safe_backup(args.db)

    print("\nApplying cleanup …")
    apply_clean(con, targets, orphan_count)

    rev_after = total_revenue(con)
    print(f"\nRevenue check:")
    print(f"  before: ฿{rev_before / 1e6:.6f} M")
    print(f"  after:  ฿{rev_after  / 1e6:.6f} M")
    if abs(rev_after - rev_before) > 0.01:
        print("  WARNING: revenue changed — investigate before proceeding!")
        con.close()
        return 1
    else:
        print("  OK — revenue unchanged ✓")

    con.close()
    print("\nPhase 2 complete.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
