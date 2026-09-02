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

Column definitions mirror ingest_easy.load_es_typeb EXACTLY so the two
ingestion paths stay identical:
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


def backfill(con) -> int:
    """Run the roll-up on an open sqlite3 connection. Returns rows inserted.
    The caller commits.

    Drops the current Thai calendar month's Easy summary first so it is rebuilt
    fresh (it is never an Excel month), then fills every other missing month.
    Idempotent in effect: re-running only ever refreshes the current month to the
    same values and adds nothing else once every past month has a row."""
    cur_ym = con.execute("SELECT strftime('%Y-%m','now','+7 hours')").fetchone()[0]
    con.execute("DELETE FROM sales_month_summary WHERE source='Easy' AND period_month=?",
                (cur_ym,))
    return con.execute(BACKFILL_SQL).rowcount
