"""Shared roll-up that populates sales_month_summary for live-synced months.

Why this exists
---------------
`sales_month_summary` (gross_sales, refunds, discounts, voided, net_sales,
cash/credit_card/transfer/qr/coupon, customer_count, transaction_count) is
written by the manual Excel ingest (ingest_easy.load_es_typeb), but the LIVE
sync (sync_mssql.py) writes only sales_transaction / sales_sku_monthly. So a
month that was ever only live-synced (e.g. the current month) has real
transactions but ZERO summary rows — breaking every reader that trusts the
summary for counts/gross/discounts: the Top-60 cost-per-head report (tab +
/api/reports/cost-head-top60[.xlsx/.print] + gen_top60_cost_per_head_report.py),
its default-month resolver, and /api/analysis/compare's fetch_kpis.

Both call sites use this one roll-up so the table is always populated:
  • app startup (main._month_summary_backfill) — self-heals the currently-live
    DB on the next deploy, no sync needed, and keeps the invariant forever.
  • the sync (sync_mssql, right before commit) — makes every freshly-built
    .incoming correct before it is swapped in (covers the hot-swap path too).

Conservative by design
----------------------
Fills ONLY (branch, month) pairs missing from the summary — existing
Excel-ingested rows are NEVER overwritten. The Excel path uses the true ex-VAT
column (col 29) and a transaction roll-up differs by a few covers, so the Excel
rows stay authoritative for the months they cover.

The ONE exception is the current (in-progress) Thai calendar month: it is
deleted and rebuilt on every run so its summary keeps pace with the day's
accumulating sales (a plain missing-only fill would freeze it at whatever the
first sync of the month saw). The current month is always a live-synced month —
never an Excel-ingested one (Excel covers only historical months) — so this
never disturbs the manually-ingested history.

Closed months must not freeze either (fixed 2026-10-01)
------------------------------------------------------
Rebuilding only the CURRENT month froze each month at the last sync that ran
inside it; bills that land after the month rolls over (the last evening, a late
or failed sync) were never added. By 2026-10-01 July read 7%, August 18.8% and
September 2.9% short on bills — every monthly bill/customer count, spend per
head and the Top-60 cost/head (฿494 vs ฿401 for August) were off. So a run also
rebuilds:
  • the PREVIOUS Thai month, always — it is live-synced and its last day's bills
    arrive after the rollover;
  • any (branch, month) whose summary carries FEWER bills than sales_transaction
    by more than 1% — a frozen live row. An Excel row is never 1% behind the
    bills (2025-05..2026-06 agree to a few bills), so it stays untouched.

Column definitions mirror ingest_easy.load_es_typeb EXACTLY so the two
ingestion paths stay identical (until 2026-09-05 the Excel path did not subtract
the service charge from net_sales — rows ingested before then carry it; a
re-ingest of those months rewrites them under the shared rule):
  gross_sales       = Σ(food+drink+other)      (ex-VAT subtotals)
  discounts         = Σ(sp_discount+discount)
  net_sales         = Σ(total_inc_vat)/1.07 − Σ(service_charge)
                      (VAT is never revenue; the 5% service charge sits INSIDE the
                       ex-VAT subtotal, so it is subtracted after the VAT divide)
  customer_count    = Σ(customer_count)
  transaction_count = COUNT(*)
  refunds / voided  = 0.0                       (as the Excel path sets them)
Tender columns (cash/credit_card/transfer/qr/coupon) stay 0.0 — the per-tender
split is not carried on sales_transaction; pos_payment_month is the
authoritative synced tender source and readers use it directly.
"""

BACKFILL_SQL = """
INSERT INTO sales_month_summary
    (branch_id, period_month, gross_sales, refunds, discounts,
     voided, net_sales, cash, credit_card, transfer, qr, coupon,
     customer_count, transaction_count, source)
SELECT st.branch_id,
       strftime('%Y-%m', st.sold_at)                                        AS period_month,
       ROUND(SUM(st.food_subtotal + st.drink_subtotal + st.other_subtotal), 2),
       0.0,
       ROUND(SUM(st.sp_discount + st.discount), 2),
       0.0,
       ROUND((SUM(st.total_inc_vat)/1.07 - SUM(COALESCE(st.service_charge,0))), 2),
       0.0, 0.0, 0.0, 0.0, 0.0,
       SUM(st.customer_count),
       COUNT(*),
       'Easy'
FROM sales_transaction st
WHERE st.source = 'Easy'
  AND NOT EXISTS (
      SELECT 1 FROM sales_month_summary sms
      WHERE sms.branch_id = st.branch_id
        AND sms.period_month = strftime('%Y-%m', st.sold_at)
  )
GROUP BY st.branch_id, strftime('%Y-%m', st.sold_at)
"""


# a (branch, month) summary that fell more than 1% behind its own bills
STALE_SQL = """
SELECT sms.branch_id, sms.period_month
FROM sales_month_summary sms
JOIN (SELECT branch_id, strftime('%Y-%m', sold_at) AS m, COUNT(*) AS tx
        FROM sales_transaction WHERE source = 'Easy'
       GROUP BY branch_id, strftime('%Y-%m', sold_at)) st
  ON st.branch_id = sms.branch_id AND st.m = sms.period_month
WHERE sms.source = 'Easy' AND COALESCE(sms.transaction_count, 0) < st.tx * 0.99
"""


def backfill(con) -> int:
    """Run the roll-up on an open sqlite3 connection. Returns rows (re)written.
    The caller commits.

    Drops the current and previous Thai months' Easy summary, and any (branch,
    month) that fell more than 1% behind its bills, then fills every missing
    (branch, month). Idempotent in effect: a re-run rewrites the same two months
    to the same values and touches nothing else once every month has caught up."""
    cur_ym, prev_ym = con.execute(
        "SELECT strftime('%Y-%m','now','+7 hours'),"
        " strftime('%Y-%m','now','+7 hours','start of month','-1 month')").fetchone()
    con.execute("DELETE FROM sales_month_summary WHERE source='Easy' AND period_month IN (?, ?)",
                (cur_ym, prev_ym))
    stale = con.execute(STALE_SQL).fetchall()
    con.executemany("DELETE FROM sales_month_summary WHERE source='Easy'"
                    " AND branch_id=? AND period_month=?", stale)
    return con.execute(BACKFILL_SQL).rowcount
