"""Shared pytest fixtures: a hermetic, fully-seeded temp SQLite DB + a FastAPI
TestClient pointed at it. No live oshinei.db, no MSSQL, no network.

Importing `main` is made side-effect-free by OSHINEI_NO_SWAP=1 (skips the
startup DB swap). The client fixture builds a tiny but coherent dataset that
exercises the real endpoints (monthly + daily + per-bill + staged pos_* tables).
"""
import os
os.environ["OSHINEI_NO_SWAP"] = "1"   # MUST be set before `import main`

import sqlite3
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).parent.resolve()
sys.path.insert(0, str(HERE))


# Latest day is mid-month → the MTD (equal-period) path is exercised.
LATEST_MONTH, PREV_MONTH = "2026-06", "2026-05"
# A third, monthly-only month so ≥3-point series exist (forecast endpoints).
EARLY_MONTH = "2026-04"
DAYS = ["01", "02", "03"]   # both months have 1..3; June is partial (cutoff=3)


def _build_db(path: str):
    con = sqlite3.connect(path)
    con.executescript((HERE / "schema.sql").read_text(encoding="utf-8"))
    # Columns the real prod DB has but the trimmed schema.sql may lack. These are added
    # at runtime by the sync / init_db migrations (sales_transaction.dwell_min etc.); the
    # fixture mirrors them so endpoints that reference the columns directly still resolve.
    for ddl in ("ALTER TABLE skus ADD COLUMN business_type TEXT",
                "ALTER TABLE skus ADD COLUMN active INTEGER DEFAULT 1",
                "ALTER TABLE sales_transaction ADD COLUMN dwell_min INTEGER",
                "ALTER TABLE sales_transaction ADD COLUMN discount_reason_id INTEGER",
                "ALTER TABLE sales_transaction ADD COLUMN coupon_series_id INTEGER"):
        try: con.execute(ddl)
        except sqlite3.OperationalError: pass
    # Tables created at runtime by sync (not in schema.sql).
    con.executescript("""
      CREATE TABLE IF NOT EXISTS bom_sku_alias(bom_sku_code TEXT PRIMARY KEY,
                                               resolved_sku_id INTEGER NOT NULL REFERENCES skus(sku_id),
                                               match_method TEXT, confidence REAL, notes TEXT);
      CREATE TABLE IF NOT EXISTS sale_payment(branch_id INT, receipt_no TEXT, payment_code TEXT, amount REAL);
      CREATE TABLE IF NOT EXISTS sales_sku_daily(branch_id INT, day TEXT, sku_id INT, qty REAL, subtotal_ex_vat REAL);
      CREATE TABLE IF NOT EXISTS sale_bill_cogs(receipt_no TEXT PRIMARY KEY, branch_id INT, day TEXT, cogs_bom REAL);
      CREATE TABLE IF NOT EXISTS pos_payment_month(branch_id INT, period_month TEXT, payment_code TEXT, amount REAL, txn_count INT);
      CREATE TABLE IF NOT EXISTS pos_channel_month(branch_id INT, period_month TEXT, delivery_type INT, order_channel INT, bills INT, customers INT, total_inc_vat REAL);
      CREATE TABLE IF NOT EXISTS pos_void(branch_id INT, period_month TEXT, sku_id INT, reason_id INT, qty REAL, ext_price REAL);
      CREATE TABLE IF NOT EXISTS pos_table_turn_day(branch_id INT, day TEXT, tabs INT, customers INT, avg_turn_min REAL);
      CREATE TABLE IF NOT EXISTS pos_coupon_month(branch_id INT, period_month TEXT, coupon_series_id INT, used_count INT, discount_amount REAL);
      CREATE TABLE IF NOT EXISTS pos_cashcount_day(branch_id INT, day TEXT, over_short REAL, total_sales REAL, pax INT, invoices INT, discount REAL, cancel REAL);
      CREATE TABLE IF NOT EXISTS pos_customer_type_month(branch_id INT, period_month TEXT, type_name TEXT, customers INT, net REAL);
      CREATE TABLE IF NOT EXISTS pos_modifier(branch_id INT, sku_id INT, choice_name TEXT, n INT, qty REAL);
      CREATE TABLE IF NOT EXISTS pos_price_change(branch_id INT, change_date TEXT, sku_id INT, item_desc TEXT, default_price REAL, new_price REAL, user_name TEXT);
      CREATE TABLE IF NOT EXISTS pos_zone(branch_id INT, zone_id INT, tables INT, seats INT);
      CREATE TABLE IF NOT EXISTS pos_cancel_reason(reason_id INTEGER PRIMARY KEY, reason_desc TEXT);
      CREATE TABLE IF NOT EXISTS pos_cancel_bill(branch_id INT, period_month TEXT, reason_id INT, bills INT, amount REAL);
      CREATE TABLE IF NOT EXISTS cost_scenario(id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, description TEXT,
                                               created_at TEXT DEFAULT (datetime('now')), created_by TEXT, is_archived INTEGER DEFAULT 0);
      CREATE TABLE IF NOT EXISTS cost_scenario_item(scenario_id INTEGER, sku_id INTEGER, unit_cost REAL);
    """)
    cx = con.cursor()
    cx.executemany("INSERT INTO branches(branch_id,name,branch_type,active) VALUES(?,?,?,?)",
                   [(1, "BranchA", "master", 1), (2, "BranchB", "franchise", 1)])
    cx.executemany("INSERT INTO categories(category_id,name) VALUES(?,?)",
                   [(1, "BUFFET"), (2, "A La Carte"), (3, "Sashimi")])
    # Mirrors production: the BUFFET category holds only the tier; buffet component
    # dishes (B) sit in real FOOD categories (e.g. Sashimi). menu-compare is
    # buffet-only, so the (A) à-la-carte item is excluded there by design.
    cx.executemany("INSERT INTO skus(sku_id,sku_code,description,category_id,unit,business_type,active) VALUES(?,?,?,?,?,?,1)",
                   [(1, "BUFFET", "ค่าบุฟเฟ่ต์", 1, "pax", "buffet_tier"),
                    (2, "B0001", "(B) แซลมอน", 3, "plate", "buffet"),
                    (3, "A0001", "(A) สเต๊ก", 2, "plate", "alacarte")])
    cx.executemany("INSERT INTO sku_cost(sku_id,unit_cost) VALUES(?,?)",
                   [(1, 99.0), (2, 8.0), (3, 10.0)])   # tier 99 must be ignored
    # monthly sales: both months, both branches. Tier carries revenue (cost 0),
    # B item carries cost (rev 0), A item carries rev + cost.
    rows = []
    for mo, scale in ((EARLY_MONTH, 1.0), (PREV_MONTH, 1.0), (LATEST_MONTH, 0.8)):  # June lower → MoM down
        for bid in (1, 2):
            rows += [
                (bid, mo, 1, 100 * scale, 10000 * scale),   # BUFFET tier: rev 10000, cost 0
                (bid, mo, 2, 500 * scale, 0.0),             # B item: cost 500*8, rev 0
                (bid, mo, 3, 50 * scale, 5000 * scale),     # A item: rev 5000, cost 50*10
            ]
    for bid, mo, sid, qty, ex in rows:
        inc = round(ex * 1.07, 2)
        cx.execute("INSERT INTO sales_sku_monthly(branch_id,period_month,sku_id,qty,subtotal_ex_vat,tax_total,total_inc_vat,source)"
                   " VALUES(?,?,?,?,?,?,?, 'Easy')", (bid, mo, sid, qty, ex, round(inc - ex, 2), inc))
    # monthly summary (covers) — backs cost-per-head style endpoints (covers = customer_count)
    con.execute("CREATE TABLE IF NOT EXISTS sales_month_summary(branch_id INT, period_month TEXT, "
                "gross_sales REAL, refunds REAL, discounts REAL, voided REAL, net_sales REAL, cash REAL, "
                "credit_card REAL, transfer REAL, qr REAL, coupon REAL, customer_count INTEGER, "
                "transaction_count INTEGER, source TEXT)")
    for mo, scale in ((EARLY_MONTH, 1.0), (PREV_MONTH, 1.0), (LATEST_MONTH, 0.8)):
        for bid in (1, 2):
            cx.execute("INSERT INTO sales_month_summary(branch_id,period_month,net_sales,customer_count,"
                       "transaction_count,source) VALUES(?,?,?,?,?, 'Easy')",
                       (bid, mo, 15000 * scale, int(round(100 * scale)), int(round(25 * scale))))
    # daily sku (recent window) + transactions + per-bill cogs
    for mo in (PREV_MONTH, LATEST_MONTH):
        for d in DAYS:
            day = f"{mo}-{d}"
            for bid in (1, 2):
                cx.execute("INSERT INTO sales_sku_daily VALUES(?,?,?,?,?)", (bid, day, 2, 50, 0.0))
                cx.execute("INSERT INTO sales_sku_daily VALUES(?,?,?,?,?)", (bid, day, 3, 5, 500.0))
                rcpt = f"{day}-{bid}001-{bid}"
                cx.execute("INSERT INTO sales_transaction(branch_id,receipt_no,sold_at,customer_count,"
                           "food_subtotal,drink_subtotal,other_subtotal,discount,service_charge,total_inc_vat,source)"
                           " VALUES(?,?,?,?,?,?,?,?,?,?, 'Easy')",
                           (bid, rcpt, f"{day}T12:00:00", 4, 900, 100, 0, 20, 50, 1070.0))
                cx.execute("INSERT INTO sale_bill_cogs VALUES(?,?,?,?)", (rcpt, bid, day, 520.0))
                # every bill settles in cash — sale_payment joins 1:1 on receipt_no (mirrors sync)
                cx.execute("INSERT INTO sale_payment VALUES(?,?,?,?)", (bid, rcpt, "Cash", 1070.0))
    # staged pos_* (latest month)
    cx.execute("INSERT INTO pos_payment_month VALUES(1,?, 'Credit', 50000, 120)", (LATEST_MONTH,))
    cx.execute("INSERT INTO pos_payment_month VALUES(1,?, 'Cash', 30000, 90)", (LATEST_MONTH,))
    cx.execute("INSERT INTO pos_channel_month VALUES(1,?,1,0, 200, 500, 80000)", (LATEST_MONTH,))
    cx.execute("INSERT INTO pos_channel_month VALUES(1,?,2,0, 10, 12, 4000)", (LATEST_MONTH,))
    cx.execute("INSERT INTO pos_void VALUES(1,?,2,1, 11, 0.0)", (LATEST_MONTH,))
    cx.execute("INSERT INTO pos_void VALUES(1,?,3,7, 3, 300.0)", (LATEST_MONTH,))
    for d in DAYS:
        cx.execute("INSERT INTO pos_table_turn_day VALUES(1,?, 30, 80, 90)", (f"{LATEST_MONTH}-{d}",))
        cx.execute("INSERT INTO pos_cashcount_day VALUES(1,?, ?, 100000, 250, 60, 500, 200)",
                   (f"{LATEST_MONTH}-{d}", -1500 if d == "01" else 0))
    cx.execute("INSERT INTO pos_coupon_month VALUES(1,?, 5, 40, 4000)", (LATEST_MONTH,))
    # Daily Payment/Promo tables exist in prod (every sync) → power the by-day ops report.
    # Void/Channel DAILY tables are added by a LATER sync, so the fixture omits them on
    # purpose → a date-range request exercises the graceful-degrade (*_pending) path.
    cx.execute("CREATE TABLE IF NOT EXISTS pos_payment_day(branch_id INT, day TEXT, payment_code TEXT, amount REAL, txn_count INT)")
    cx.execute("CREATE TABLE IF NOT EXISTS pos_coupon_day(branch_id INT, day TEXT, coupon_series_id INT, used_count INT, discount_amount REAL)")
    for d in DAYS:
        day = f"{LATEST_MONTH}-{d}"
        cx.execute("INSERT INTO pos_payment_day VALUES(1,?, 'Cash', 10000, 30)", (day,))
        cx.execute("INSERT INTO pos_payment_day VALUES(1,?, 'Credit', 5000, 12)", (day,))
        cx.execute("INSERT INTO pos_coupon_day VALUES(1,?, 5, 4, 400)", (day,))
    cx.execute("INSERT INTO pos_customer_type_month VALUES(1,?, 'ลูกค้าทั่วไป', 5000, 400000)", (LATEST_MONTH,))
    cx.execute("INSERT INTO pos_customer_type_month VALUES(1,?, 'Grab', 50, 12000)", (LATEST_MONTH,))
    cx.execute("INSERT INTO pos_modifier VALUES(1, 3, 'รับไข่', 300, 300)")
    cx.execute("INSERT INTO pos_modifier VALUES(1, 3, 'เผ็ดน้อย', 120, 120)")
    cx.execute("INSERT INTO pos_price_change VALUES(1, ?, 3, '(A) สเต๊ก', 200, 180, 'DA')", (f"{LATEST_MONTH}-02T10:00:00",))
    cx.execute("INSERT INTO pos_zone VALUES(1, 1, 20, 0)")
    cx.execute("INSERT INTO pos_cancel_reason VALUES(2, 'คีย์ข้อมูลผิด')")
    cx.execute("INSERT INTO pos_cancel_bill VALUES(1,?, 2, 5, 6000)", (LATEST_MONTH,))
    con.commit(); con.close()


@pytest.fixture(scope="session")
def app_db(tmp_path_factory):
    """Build the temp DB once and point main at it."""
    db = tmp_path_factory.mktemp("oshinei") / "test.db"
    _build_db(str(db))
    import main
    main.DB_PATH = db
    if hasattr(main, "_tls"):
        main._tls.con = None
    if hasattr(main, "invalidate_cache"):
        try: main.invalidate_cache()
        except Exception: pass
    return db


@pytest.fixture(scope="session")
def client(app_db):
    import main
    from fastapi.testclient import TestClient
    with TestClient(main.app) as c:
        yield c
