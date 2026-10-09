# -*- coding: utf-8 -*-
"""Prepare a SCRATCH copy of the database for scripts/load_branches.py.

The live database has 28 active branches. A load test for 30 (and 60, for headroom) needs more, so this adds
synthetic branches (branch_id 901+, names "สาขาทดสอบ 01 ..."), each one a clone of a real branch's recent
sales (sales_sku_daily in full, sales_transaction for the last 90 days) so the order plan has real rows,
and gives each the same delivery calendar as the pilot branch.

Never run it against the live file:  py -3.12 scripts/load_branches_setup.py --db <scratch.db> --total 60
It only INSERTs; running it twice is harmless (existing synthetic branches are left alone)."""
import argparse
import sqlite3
import sys

PILOT_BRANCH = 25                 # อุดรธานี: its calendar rows are copied to every synthetic branch
SYNTH_FROM = 901


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--db", required=True, help="path of the SCRATCH database copy")
    ap.add_argument("--total", type=int, default=60, help="active branches wanted in all (real + synthetic)")
    ap.add_argument("--tx-days", type=int, default=90, help="days of sales_transaction to clone per synthetic branch")
    a = ap.parse_args()
    if "oshinei.db" in a.db.lower().replace("\\", "/").rsplit("/", 1)[-1]:
        print("refusing: that looks like the live database file name", file=sys.stderr)
        return 2
    con = sqlite3.connect(a.db)
    real = [r[0] for r in con.execute(
        "SELECT branch_id FROM branches WHERE branch_id < ? AND COALESCE(active,1)=1 AND name <> 'ALL' "
        "AND branch_id IN (SELECT DISTINCT branch_id FROM sales_sku_daily) ORDER BY branch_id", (SYNTH_FROM,))]
    have = [r[0] for r in con.execute("SELECT branch_id FROM branches WHERE branch_id >= ? ORDER BY branch_id", (SYNTH_FROM,))]
    need = max(0, a.total - len(real) - len(have))
    print(f"real active branches with sales: {len(real)}; synthetic already there: {len(have)}; adding {need}")
    cal = con.execute("SELECT round, order_weekday, delivery_weekday, lead_days FROM bc_order_calendar WHERE branch_id=?",
                      (PILOT_BRANCH,)).fetchall()
    last_tx = con.execute("SELECT MAX(date(sold_at)) FROM sales_transaction").fetchone()[0]
    for j in range(need):
        bid = SYNTH_FROM + len(have) + j
        src = real[(len(have) + j) % len(real)]
        con.execute("INSERT INTO branches(branch_id, name, branch_type, active) VALUES (?,?,?,1)",
                    (bid, f"สาขาทดสอบ {bid - SYNTH_FROM + 1:02d}", "franchise"))
        con.execute("INSERT INTO sales_sku_daily(branch_id, day, sku_id, qty, subtotal_ex_vat) "
                    "SELECT ?, day, sku_id, qty, subtotal_ex_vat FROM sales_sku_daily WHERE branch_id=?", (bid, src))
        con.execute("INSERT OR IGNORE INTO sales_transaction(branch_id, receipt_no, sold_at, cashier, table_no, customer_count,"
                    " food_subtotal, drink_subtotal, other_subtotal, tip, sp_discount, discount, service_charge, total_inc_vat,"
                    " source, discount_reason_id, coupon_series_id, print_times, dwell_min) "
                    "SELECT ?, receipt_no, sold_at, cashier, table_no, customer_count, food_subtotal, drink_subtotal,"
                    " other_subtotal, tip, sp_discount, discount, service_charge, total_inc_vat, source, discount_reason_id,"
                    " coupon_series_id, print_times, dwell_min FROM sales_transaction"
                    " WHERE branch_id=? AND date(sold_at) >= date(?, ?)", (bid, src, last_tx, f"-{a.tx_days} days"))
        for rnd, ow, dw, ld in cal:
            con.execute("INSERT OR REPLACE INTO bc_order_calendar(branch_id, round, order_weekday, delivery_weekday, lead_days,"
                        " filled_by, updated_at) VALUES (?,?,?,?,?,?,?)",
                        (bid, rnd, ow, dw, ld, "load_setup", "2026-10-09T00:00:00"))
        con.commit()
    n = con.execute("SELECT COUNT(*) FROM branches WHERE COALESCE(active,1)=1 AND name <> 'ALL'").fetchone()[0]
    print(f"active branches now: {n}")
    con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
