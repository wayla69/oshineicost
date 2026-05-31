"""
Oshinei DB maintenance — apply PRAGMAs, create indexes, ANALYZE, VACUUM,
and run EXPLAIN QUERY PLAN on representative queries.

Run:  python backend/db_maintenance.py
      python backend/db_maintenance.py --explain-only
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from pathlib import Path

HERE      = Path(__file__).parent.resolve()
DB_PATH   = HERE / "oshinei.db"
SCHEMA    = HERE / "schema.sql"

PRAGMAS = [
    "PRAGMA journal_mode=WAL",
    "PRAGMA synchronous=NORMAL",
    "PRAGMA cache_size=-65536",
    "PRAGMA temp_store=MEMORY",
    "PRAGMA mmap_size=268435456",
    "PRAGMA foreign_keys=ON",
    "PRAGMA busy_timeout=5000",
]

# Representative queries to EXPLAIN — covers the hottest endpoints.
EXPLAIN_QUERIES = [
    ("monthly trend (sales_sku_monthly + branch filter)", """
        SELECT sm.period_month, SUM(sm.total_inc_vat) AS revenue
        FROM sales_sku_monthly sm
        JOIN branches b ON b.branch_id = sm.branch_id
        WHERE b.name = 'โคราช1' AND sm.period_month BETWEEN '2025-01' AND '2026-12'
        GROUP BY sm.period_month
        ORDER BY sm.period_month
    """),
    ("daily transactions (sales_transaction + branch/source/date)", """
        SELECT DATE(st.sold_at) AS d, SUM(st.total_inc_vat) AS revenue
        FROM sales_transaction st
        JOIN branches b ON b.branch_id = st.branch_id
        WHERE b.name = 'โคราช1' AND st.source='Easy'
          AND DATE(st.sold_at) BETWEEN '2026-01-01' AND '2026-12-31'
        GROUP BY DATE(st.sold_at)
        ORDER BY d
    """),
    ("transactions listing (pagination)", """
        SELECT st.receipt_no, st.sold_at, st.total_inc_vat
        FROM sales_transaction st
        JOIN branches b ON b.branch_id = st.branch_id
        WHERE strftime('%Y-%m', st.sold_at) = '2026-05'
        ORDER BY st.sold_at DESC
        LIMIT 100 OFFSET 0
    """),
    ("category breakdown (sku join)", """
        SELECT c.name, SUM(sm.total_inc_vat) AS revenue
        FROM sales_sku_monthly sm
        JOIN skus s ON s.sku_id = sm.sku_id
        JOIN categories c ON c.category_id = s.category_id
        JOIN branches b ON b.branch_id = sm.branch_id
        WHERE b.name = 'โคราช1' AND sm.period_month = '2026-05'
        GROUP BY c.name
    """),
    ("month summary lookup", """
        SELECT * FROM sales_month_summary sms
        JOIN branches b ON b.branch_id = sms.branch_id
        WHERE b.name = 'โคราช1' AND sms.period_month = '2026-05'
    """),
]


def apply_pragmas(con: sqlite3.Connection) -> None:
    for p in PRAGMAS:
        result = con.execute(p).fetchone()
        print(f"  {p:35s} -> {result[0] if result else 'ok'}")


def apply_schema(con: sqlite3.Connection) -> None:
    if not SCHEMA.exists():
        print(f"  schema.sql not found at {SCHEMA}", file=sys.stderr)
        return
    sql = SCHEMA.read_text(encoding="utf-8")
    con.executescript(sql)
    print(f"  applied {SCHEMA.name} (CREATE IF NOT EXISTS)")


def run_explain(con: sqlite3.Connection) -> None:
    for label, q in EXPLAIN_QUERIES:
        print(f"\n  --- {label} ---")
        try:
            t0 = time.perf_counter()
            plan = con.execute("EXPLAIN QUERY PLAN " + q).fetchall()
            elapsed_plan = (time.perf_counter() - t0) * 1000
            for row in plan:
                # (id, parent, notused, detail)
                print(f"    {row[3]}")
            # Time the actual query
            t0 = time.perf_counter()
            con.execute(q).fetchall()
            elapsed = (time.perf_counter() - t0) * 1000
            print(f"    [plan {elapsed_plan:.1f} ms | exec {elapsed:.1f} ms]")
        except sqlite3.Error as e:
            print(f"    ERROR: {e}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--explain-only", action="store_true",
                    help="Skip VACUUM/ANALYZE; only run EXPLAIN QUERY PLAN.")
    ap.add_argument("--skip-vacuum", action="store_true",
                    help="Skip VACUUM (which can be slow / rewrites DB).")
    args = ap.parse_args()

    if not DB_PATH.exists():
        print(f"DB not found at {DB_PATH}", file=sys.stderr)
        return 1

    size_before = DB_PATH.stat().st_size
    print(f"DB: {DB_PATH}")
    print(f"Size before: {size_before/1024/1024:.2f} MiB\n")

    con = sqlite3.connect(str(DB_PATH))
    try:
        print("[1/5] Applying PRAGMAs")
        apply_pragmas(con)

        if not args.explain_only:
            print("\n[2/5] Applying schema (adds indexes if missing)")
            apply_schema(con)

            print("\n[3/5] ANALYZE + PRAGMA optimize")
            t0 = time.perf_counter()
            con.execute("ANALYZE")
            con.execute("PRAGMA optimize")
            con.commit()
            print(f"  done in {(time.perf_counter()-t0)*1000:.0f} ms")

            if not args.skip_vacuum:
                print("\n[4/5] VACUUM")
                t0 = time.perf_counter()
                # VACUUM cannot run inside a transaction
                con.isolation_level = None
                con.execute("VACUUM")
                con.isolation_level = ""
                print(f"  done in {(time.perf_counter()-t0)*1000:.0f} ms")
            else:
                print("\n[4/5] VACUUM skipped (--skip-vacuum)")

        print("\n[5/5] EXPLAIN QUERY PLAN")
        run_explain(con)
    finally:
        con.close()

    size_after = DB_PATH.stat().st_size
    print(f"\nSize after:  {size_after/1024/1024:.2f} MiB "
          f"(delta {(size_after-size_before)/1024:.1f} KiB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
