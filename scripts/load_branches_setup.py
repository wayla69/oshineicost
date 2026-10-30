# -*- coding: utf-8 -*-
"""Prepare a SCRATCH copy of the database for scripts/load_branches.py.

The live database has 28 active branches. A load test for 30 (and 60, for headroom) needs more, so this adds
synthetic branches (branch_id 901+, names "สาขาทดสอบ 01 ..."), each one a clone of a real branch's recent
sales (sales_sku_daily in full, sales_transaction for the last 90 days) so the order plan has real rows,
and gives each the same delivery calendar as the pilot branch.

Never run it against the live file:  py -3.12 scripts/load_branches_setup.py --db <scratch.db> --total 60 --seed-stock 90
It only INSERTs; running it twice is harmless (existing synthetic branches are left alone).

--seed-stock N (2026-10-30): the office PC's database has no ingredient master and no issue slips (that data lives only on
the live site), so in stock mode - the mode the pilot runs in - every plan came back empty and the load test measured
nothing. This adds N test ingredients (codes LT-001 ..., a 1,000 g pack each) and, for every active branch, confirmed issue
slips for the last HISTORY days (default 56 = the plan's 8 weeks), one per day, with a steady usage per ingredient that
differs per branch. Ingredients and slips that already exist are left alone."""
import argparse
import random
import sqlite3
import sys
from datetime import date, datetime, timedelta, timezone

PILOT_BRANCH = 25                 # อุดรธานี: its calendar rows are copied to every synthetic branch
SYNTH_FROM = 901


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--db", required=True, help="path of the SCRATCH database copy")
    ap.add_argument("--total", type=int, default=60, help="active branches wanted in all (real + synthetic)")
    ap.add_argument("--tx-days", type=int, default=90, help="days of sales_transaction to clone per synthetic branch")
    ap.add_argument("--seed-stock", type=int, default=0, metavar="N",
                    help="add N test ingredients and confirmed issue slips for every active branch (stock-mode plans)")
    ap.add_argument("--history-days", type=int, default=56, help="days of confirmed slips per branch for --seed-stock")
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
    if a.seed_stock:
        seed_stock(con, a.seed_stock, a.history_days)
    con.close()
    return 0


def seed_stock(con, n_ing: int, days: int) -> None:
    """Test ingredients + confirmed issue slips for every active branch (see --seed-stock). Needs the bom-center tables, which
    the app creates on start-up: start the scratch server once before, or this stops with a message."""
    have = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'bc_%'")}
    if not {"bc_ingredient", "bc_issue_slip", "bc_issue_line"} <= have:
        raise SystemExit("bom-center tables missing: start the app once on this scratch DB, stop it, then run --seed-stock again")
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    codes = [f"LT-{i + 1:03d}" for i in range(n_ing)]
    for i, c in enumerate(codes):
        con.execute("INSERT OR IGNORE INTO bc_ingredient(code, name_th, family, kind, category, base_unit, recv_unit, qty_per_recv,"
                    " sourcing_default, active, updated_at, updated_by) VALUES (?,?,?,?,?,?,?,?,?,1,?,?)",
                    (c, f"วัตถุดิบทดสอบ {i + 1:03d}", "V", "raw", "ทดสอบ", "กรัม", "กล่อง", 1000.0, "central", now, "load_setup"))
    # Bangkok today; slips up to yesterday (the plan reads the weeks before the order day)
    today = (datetime.now(timezone.utc) + timedelta(hours=7)).date()
    branches = [r[0] for r in con.execute(
        "SELECT branch_id FROM branches WHERE COALESCE(active,1)=1 AND name <> 'ALL' ORDER BY branch_id")]
    made = 0
    for bid in branches:
        rng = random.Random(bid)
        base = {c: rng.uniform(150, 4000) for c in codes}          # grams a day; a branch's own mix
        for k in range(days, 0, -1):
            d = (today - timedelta(days=k)).isoformat()
            cur = con.execute("INSERT OR IGNORE INTO bc_issue_slip(branch_id, slip_date, status, created_by, created_at,"
                              " updated_at, confirmed_by, confirmed_at, confirm_count) VALUES (?,?,'confirmed',?,?,?,?,?,1)",
                              (bid, d, "load_setup", now, now, "load_setup", now))
            if not cur.rowcount:
                continue                                           # that day's slip is already there
            sid = cur.lastrowid
            wk = 1.25 if date.fromisoformat(d).weekday() >= 5 else 1.0   # weekends sell more
            rows = []
            for c in codes:
                q = round(base[c] * wk * rng.uniform(0.8, 1.2), 1)
                w = round(q * 0.03, 1) if rng.random() < 0.2 else 0.0
                rows.append((sid, c, q, w, "กรัม", q, w))
            con.executemany("INSERT INTO bc_issue_line(slip_id, ingredient_code, qty_used, qty_waste, unit, qty_used_base,"
                            " qty_waste_base) VALUES (?,?,?,?,?,?,?)", rows)
            made += 1
        con.commit()
    print(f"seed-stock: {len(codes)} test ingredients; {made} confirmed slips added across {len(branches)} branches")


if __name__ == "__main__":
    sys.exit(main())
