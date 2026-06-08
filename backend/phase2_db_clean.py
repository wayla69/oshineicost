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

# Non-food junk (Payment/Voucher/Discount) is currently EXCLUDED from food
# analytics by main.py's name-keyword filter (_NONFOOD_CATEGORY_KEYWORDS). If we
# re-pointed those SKUs into "Oshinei other" (a name that matches no keyword) they
# would silently start counting as food in the new menu-cost tabs. Route them to a
# fallback whose NAME contains a non-food keyword so the filter keeps excluding
# them. "Payment" is a keyword, so this name stays excluded.
NONFOOD_FALLBACK_CATEGORY = "Payment & non-product (archived)"
# A target category is non-food if its name contains one of these (upper-cased).
NONFOOD_TARGET_KEYWORDS = ("PAYMENT", "VOUCHER", "DISCOUNT")


def _is_nonfood(cat_name: str) -> bool:
    up = (cat_name or "").upper()
    return any(kw in up for kw in NONFOOD_TARGET_KEYWORDS)


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


def get_or_create_category(cur: sqlite3.Cursor, name: str) -> int:
    row = cur.execute(
        "SELECT category_id FROM categories WHERE name = ?", (name,)
    ).fetchone()
    if row:
        return row[0]
    cur.execute("INSERT INTO categories (name) VALUES (?)", (name,))
    fid = cur.lastrowid
    print(f"  created fallback category '{name}' (id={fid})")
    return fid


def total_revenue(con: sqlite3.Connection) -> float:
    return con.execute(
        "SELECT COALESCE(SUM(total_inc_vat), 0) FROM sales_sku_monthly"
    ).fetchone()[0]


def clean_invariants(con: sqlite3.Connection) -> dict:
    """Snapshot the things the clean must NOT change.

    A flat SUM over sales_sku_monthly is useless as a guard because this script
    never writes that table — so it can't detect a bad re-point. These three DO
    move if a SKU is dropped, mis-pointed, or a category is deleted out from under
    live SKUs (incl. an unexpected FK cascade):
      - sku_count          : total SKUs (must be preserved — we only move, never drop)
      - revenue_via_join   : revenue reachable through sales -> skus (falls if any
                             costed/sold SKU is deleted or orphaned)
      - dangling_skus      : SKUs whose category_id points at no existing category
                             (must be 0 after the clean — every SKU lands in a real cat)
    """
    sku_count = con.execute("SELECT COUNT(*) FROM skus").fetchone()[0]
    revenue_via_join = con.execute(
        "SELECT COALESCE(SUM(ssm.total_inc_vat), 0) "
        "FROM sales_sku_monthly ssm JOIN skus sk ON sk.sku_id = ssm.sku_id"
    ).fetchone()[0]
    dangling = con.execute(
        "SELECT COUNT(*) FROM skus sk WHERE sk.category_id IS NOT NULL "
        "AND NOT EXISTS (SELECT 1 FROM categories c WHERE c.category_id = sk.category_id)"
    ).fetchone()[0]
    return {"sku_count": sku_count, "revenue_via_join": revenue_via_join,
            "dangling_skus": dangling}


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
    # Fallback categories are created in autocommit (con.isolation_level is None,
    # set in main()) BEFORE the explicit transaction below — so the INSERT here
    # never leaves an implicit transaction open that would make BEGIN raise
    # "cannot start a transaction within a transaction".
    food_fallback_id = get_or_create_category(cur, FALLBACK_CATEGORY)
    nonfood_fallback_id = None
    if any(_is_nonfood(t["cat_name"]) for t in targets):
        nonfood_fallback_id = get_or_create_category(cur, NONFOOD_FALLBACK_CATEGORY)

    cur.execute("BEGIN")
    try:
        for t in targets:
            cid = t["category_id"]
            name = t["cat_name"]
            # Preserve food/non-food classification: name-excluded junk
            # (Payment/Voucher/Discount) goes to a fallback that the food filter
            # still excludes, so it can't leak into the menu-cost tabs.
            nonfood = _is_nonfood(name)
            dest_id = nonfood_fallback_id if nonfood else food_fallback_id
            dest_name = NONFOOD_FALLBACK_CATEGORY if nonfood else FALLBACK_CATEGORY

            # Re-point any SKUs that live in this category
            if t["sku_count"] > 0:
                cur.execute(
                    "UPDATE skus SET category_id = ? WHERE category_id = ?",
                    (dest_id, cid),
                )
                print(f"  re-pointed {t['sku_count']} SKUs from '{name}' -> '{dest_name}'")

            cur.execute("DELETE FROM categories WHERE category_id = ?", (cid,))
            print(f"  deleted category '{name}' (id={cid})")

        if orphan_count:
            cur.execute(
                "UPDATE skus SET category_id = ? WHERE category_id IS NULL",
                (food_fallback_id,),
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
    # Autocommit: we manage the one mutating transaction explicitly with
    # BEGIN/COMMIT in apply_clean. Without this, sqlite3's default isolation_level
    # opens an implicit transaction on the first INSERT (the fallback-category
    # creation), and the subsequent explicit BEGIN raises "cannot start a
    # transaction within a transaction".
    con.isolation_level = None
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
    inv_before = clean_invariants(con)

    print("Backing up before any mutation …")
    safe_backup(args.db)

    print("\nApplying cleanup …")
    apply_clean(con, targets, orphan_count)

    # ── Verify the things that MUST be preserved (a flat sales sum can't — this
    #    script never writes sales_sku_monthly, so it would always look OK). ──
    inv_after = clean_invariants(con)
    print(f"\nInvariant check:")
    print(f"  SKU count:        {inv_before['sku_count']} -> {inv_after['sku_count']}")
    print(f"  revenue via join: ฿{inv_before['revenue_via_join'] / 1e6:.6f} M "
          f"-> ฿{inv_after['revenue_via_join'] / 1e6:.6f} M")
    print(f"  dangling SKUs:    {inv_after['dangling_skus']} (must be 0)")

    problems = []
    if inv_after["sku_count"] != inv_before["sku_count"]:
        problems.append("SKU count changed — a SKU was dropped (FK cascade?)")
    if abs(inv_after["revenue_via_join"] - inv_before["revenue_via_join"]) > 0.01:
        problems.append("revenue reachable through skus changed — SKUs lost/mis-pointed")
    if inv_after["dangling_skus"] > 0:
        problems.append(f"{inv_after['dangling_skus']} SKUs point at a deleted category")
    if problems:
        print("  WARNING: clean did not preserve invariants — RESTORE FROM BACKUP:")
        for p in problems:
            print(f"    - {p}")
        con.close()
        return 1
    print("  OK — SKUs preserved, every SKU in a real category ✓")

    con.close()
    print("\nPhase 2 complete.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
