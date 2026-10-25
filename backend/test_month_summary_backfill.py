"""Regression test for month_summary.backfill — the root-cause fix for
live-synced-only months having 0 counts/gross/discounts in sales_month_summary.

Scenario mirrors production: the live sync writes sales_transaction but not
sales_month_summary, so a month that was only live-synced has transactions but
no summary row. backfill() must fill ONLY the missing month, roll it up exactly
like ingest_easy.load_es_typeb, leave the Excel-ingested month untouched, and be
idempotent.
"""
import sqlite3

import month_summary


def _db():
    con = sqlite3.connect(":memory:")
    con.execute("""
        CREATE TABLE sales_transaction(
            branch_id INT, receipt_no TEXT, sold_at TEXT, customer_count INT,
            food_subtotal REAL, drink_subtotal REAL, other_subtotal REAL,
            sp_discount REAL, discount REAL, service_charge REAL,
            total_inc_vat REAL, source TEXT)
    """)
    con.execute("""
        CREATE TABLE sales_month_summary(
            branch_id INT, period_month TEXT, gross_sales REAL, refunds REAL,
            discounts REAL, voided REAL, net_sales REAL, cash REAL,
            credit_card REAL, transfer REAL, qr REAL, coupon REAL,
            customer_count INTEGER, transaction_count INTEGER, source TEXT)
    """)
    return con


def _add_tx(con, bid, month, receipts):
    for i, (cust, food, drink, other, sp, disc, inc) in enumerate(receipts):
        con.execute(
            "INSERT INTO sales_transaction VALUES(?,?,?,?,?,?,?,?,?,?,?, 'Easy')",
            (bid, f"{month}-{bid}-{i}", f"{month}-05T12:00:00", cust,
             food, drink, other, sp, disc, 0.0, inc))


def test_backfill_fills_only_missing_month_and_is_exact():
    con = _db()
    # FULL month (Excel-ingested): has BOTH transactions and a summary row whose
    # numbers deliberately DIFFER from a txn roll-up (the Excel path is authoritative).
    _add_tx(con, 1, "2026-05", [(4, 900, 100, 0, 0, 20, 1070.0),
                                (2, 500, 0, 0, 0, 0, 535.0)])
    con.execute("INSERT INTO sales_month_summary(branch_id,period_month,gross_sales,"
                "refunds,discounts,voided,net_sales,customer_count,transaction_count,source)"
                " VALUES(1,'2026-05', 9999, 0, 111, 0, 8888, 999, 42, 'Easy')")
    # LIVE-SYNCED-ONLY month: transactions but NO summary row.
    _add_tx(con, 1, "2026-06", [(4, 900, 100, 0, 0, 20, 1070.0),
                                (3, 800, 50, 10, 5, 30, 921.0)])
    _add_tx(con, 2, "2026-06", [(6, 1200, 0, 0, 0, 0, 1284.0)])
    con.commit()

    before_may = con.execute(
        "SELECT gross_sales,discounts,net_sales,customer_count,transaction_count "
        "FROM sales_month_summary WHERE period_month='2026-05'").fetchone()

    n = month_summary.backfill(con); con.commit()
    assert n == 2, "one row per (branch, month) missing: branch1+branch2 for 2026-06"

    # Excel month left EXACTLY as it was (never overwritten).
    after_may = con.execute(
        "SELECT gross_sales,discounts,net_sales,customer_count,transaction_count "
        "FROM sales_month_summary WHERE period_month='2026-05'").fetchone()
    assert after_may == before_may == (9999, 111, 8888, 999, 42)

    # Live-synced month rolled up EXACTLY like ingest_easy.load_es_typeb.
    b1 = con.execute("SELECT gross_sales,discounts,net_sales,customer_count,transaction_count "
                     "FROM sales_month_summary WHERE period_month='2026-06' AND branch_id=1").fetchone()
    # gross = (900+100+0)+(800+50+10)=1860 ; disc=(0+20)+(5+30)=55
    # net = (1070+921)/1.07 = 1860.75 ; cust=7 ; tx=2
    assert b1[0] == 1860.0
    assert b1[1] == 55.0
    assert b1[2] == round((1070.0 + 921.0) / 1.07, 2)
    assert b1[3] == 7
    assert b1[4] == 2
    b2 = con.execute("SELECT gross_sales,net_sales,customer_count,transaction_count "
                     "FROM sales_month_summary WHERE period_month='2026-06' AND branch_id=2").fetchone()
    assert b2 == (1200.0, round(1284.0 / 1.07, 2), 6, 1)

    # Tender columns stay 0 (not carried on sales_transaction).
    tender = con.execute("SELECT cash,credit_card,transfer,qr,coupon,refunds,voided "
                         "FROM sales_month_summary WHERE period_month='2026-06' AND branch_id=1").fetchone()
    assert tender == (0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)


def test_backfill_is_idempotent():
    con = _db()
    _add_tx(con, 1, "2026-06", [(4, 900, 100, 0, 0, 20, 1070.0)])
    con.commit()
    assert month_summary.backfill(con) == 1
    con.commit()
    assert month_summary.backfill(con) == 0, "second run must be a no-op"
    con.commit()
    assert con.execute("SELECT COUNT(*) FROM sales_month_summary").fetchone()[0] == 1


def test_backfill_refreshes_current_month(bkk_frozen):
    """The in-progress month must be rebuilt each run so it keeps pace with the
    day's sales — a stale summary row for it is replaced, not skipped."""
    con = _db()
    cur_ym = con.execute("SELECT strftime('%Y-%m','now','+7 hours')").fetchone()[0]
    # A STALE summary row for the current month (as if written by an earlier sync
    # that only saw the 1st of the month), plus the now-fuller transaction set.
    con.execute("INSERT INTO sales_month_summary(branch_id,period_month,gross_sales,"
                "refunds,discounts,voided,net_sales,customer_count,transaction_count,source)"
                f" VALUES(1,'{cur_ym}', 1, 0, 0, 0, 1, 1, 1, 'Easy')")
    _add_tx(con, 1, cur_ym, [(4, 900, 100, 0, 0, 20, 1070.0),
                             (3, 800, 50, 10, 5, 30, 921.0)])
    con.commit()

    month_summary.backfill(con); con.commit()

    row = con.execute("SELECT gross_sales,discounts,net_sales,customer_count,transaction_count "
                      f"FROM sales_month_summary WHERE period_month='{cur_ym}'").fetchall()
    assert len(row) == 1, "the stale row is replaced, not duplicated"
    g, d, net, cust, tx = row[0]
    assert (g, d, cust, tx) == (1860.0, 55.0, 7, 2)          # refreshed to the full txn set
    assert net == round((1070.0 + 921.0) / 1.07, 2)


def test_backfill_ignores_non_easy_source():
    con = _db()
    con.execute("INSERT INTO sales_transaction VALUES(1,'r','2026-06-05T12:00:00',4,"
                "900,100,0,0,20,0, 1070.0, 'CM')")
    con.commit()
    assert month_summary.backfill(con) == 0, "only Easy transactions are rolled up"
    assert con.execute("SELECT COUNT(*) FROM sales_month_summary").fetchone()[0] == 0


def test_backfill_catches_up_a_frozen_closed_month():
    """A live row frozen by the last sync inside its month (fewer bills than the
    month really had) is rebuilt; an Excel row that is not behind is left alone.
    August 2026 sat 18.8% short on bills until this existed."""
    con = _db()
    _add_tx(con, 1, "2026-08", [(4, 900, 100, 0, 0, 20, 1070.0)] * 200)
    con.execute("INSERT INTO sales_month_summary(branch_id,period_month,gross_sales,"
                "refunds,discounts,voided,net_sales,customer_count,transaction_count,source)"
                " VALUES(1,'2026-08', 1, 0, 0, 0, 1, 650, 162, 'Easy')")      # frozen: 162 of 200 bills
    _add_tx(con, 2, "2026-05", [(2, 500, 0, 0, 0, 0, 535.0)] * 100)
    con.execute("INSERT INTO sales_month_summary(branch_id,period_month,gross_sales,"
                "refunds,discounts,voided,net_sales,customer_count,transaction_count,source)"
                " VALUES(2,'2026-05', 9999, 0, 0, 0, 8888, 199, 100, 'Easy')")  # Excel, in step
    con.commit()
    month_summary.backfill(con); con.commit()
    aug = con.execute("SELECT customer_count, transaction_count FROM sales_month_summary"
                      " WHERE branch_id=1 AND period_month='2026-08'").fetchall()
    assert aug == [(800, 200)]
    may = con.execute("SELECT gross_sales, net_sales, customer_count FROM sales_month_summary"
                      " WHERE branch_id=2 AND period_month='2026-05'").fetchone()
    assert may == (9999, 8888, 199)


def test_backfill_rebuilds_the_previous_month(bkk_frozen):
    """Bills for the last day of a month land after the rollover; the previous
    month is rebuilt every run so they are counted."""
    con = _db()
    prev_ym = con.execute("SELECT strftime('%Y-%m','now','+7 hours','start of month','-1 month')").fetchone()[0]
    _add_tx(con, 1, prev_ym, [(4, 900, 100, 0, 0, 20, 1070.0)] * 100)
    con.execute("INSERT INTO sales_month_summary(branch_id,period_month,gross_sales,"
                "refunds,discounts,voided,net_sales,customer_count,transaction_count,source)"
                f" VALUES(1,'{prev_ym}', 1, 0, 0, 0, 1, 398, 100, 'Easy')")     # in step on bills, stale on covers
    con.commit()
    month_summary.backfill(con); con.commit()
    assert con.execute("SELECT customer_count FROM sales_month_summary"
                       f" WHERE period_month='{prev_ym}'").fetchone()[0] == 400
