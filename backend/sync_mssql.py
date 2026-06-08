"""
sync_mssql.py — pull live sales from the Easy POS MS SQL Server into the app's
SQLite, preserving the BoM/cost/settings tables (Option A: near-real-time sync).

Design (decided 2026-06-04):
  * Source  : MS SQL Server  HQ_Oshinei  (Sale, SaleItem, Branch, Items, ItemCategory)
  * Target  : backend/oshinei.db  — only the SALES tables are replaced:
                sales_transaction, sales_sku_monthly
              Everything else (bom_ingredients, sku_cost, app_settings,
              branch_targets, closed_hours, cost_*, skus, categories, branches…)
              is COPIED from the current DB and kept — COGS stays on BoM.
  * Mapping  : reuse the existing skus/branches by NATURAL KEY
                - sku   : MSSQL Items.ItemCode  == sqlite skus.sku_code
                - branch: MSSQL Branch.BranchName --BRANCH_ALIAS--> sqlite branches.name
              → zero convention drift (descriptions, (A)/(B) prefixes,
                business_type all come from the existing skus rows).
  * Output   : writes backend/oshinei.db.incoming then RECONCILES against the
              current DB. It does NOT swap — the app swaps `.incoming` at startup
              (see _maybe_swap_incoming_db in main.py) / Refresh Online Data.bat.

Creds come from backend/.env (gitignored). Run:
    python sync_mssql.py                # full sync → .incoming + reconcile
    python sync_mssql.py --since 2026-05-01
Aggregation is done server-side (GROUP BY) so we never pull the 21M SaleItem rows.
"""
import os, sys, shutil, sqlite3, argparse
from pathlib import Path

HERE = Path(__file__).resolve().parent
DB        = HERE / "oshinei.db"
INCOMING  = HERE / "oshinei.db.incoming"

# ── load .env (no external dep) ──────────────────────────────────────────────
def load_env():
    f = HERE / ".env"
    if f.exists():
        for line in f.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())
load_env()

from ingest_may26_update import BRANCH_ALIAS   # MSSQL/full name → short DB name

SALE_FILTER = "s.IsFinalize=1 AND s.Active=1 AND ISNULL(s.IsReturn,0)=0"

def connect_mssql():
    import pymssql
    # Fail loud + clear if creds are missing, instead of a cryptic KeyError deep
    # in the sync (e.g. when backend/.env is absent on a fresh checkout).
    required = ("OSH_SVR", "OSH_PORT", "OSH_USER", "OSH_PWD", "OSH_DB")
    missing = [k for k in required if not os.environ.get(k)]
    if missing:
        sys.exit(f"MSSQL credentials missing: {', '.join(missing)}. "
                 f"Set them in backend/.env (see .env.example) or the environment.")
    # NOTE: default charset (UTF-8) — required so nvarchar Thai (BranchName,
    # CategoryName) reads correctly. charset='CP874' empties nvarchar columns.
    return pymssql.connect(
        server=os.environ["OSH_SVR"], port=os.environ["OSH_PORT"],
        user=os.environ["OSH_USER"], password=os.environ["OSH_PWD"],
        database=os.environ["OSH_DB"], login_timeout=15, timeout=600)

def short_name(mssql_name: str) -> str:
    n = (mssql_name or "").strip()
    return BRANCH_ALIAS.get(n, n)

# ── drink / other category classification (food = everything else) ───────────
def bucket_for_cat(name: str) -> str:
    u = (name or "").upper()
    if any(k in u for k in ("SOFT DRINK", "DRINK", "ALCOHOL", "BEVERAGE", "เครื่องดื่ม")):
        return "drink"
    if any(k in u for k in ("BUFFET", "แพ็คเกจ", "PACKAGE", "SERVICE", "DELIVERY",
                            "SPECIAL", "PROMOTION", "PAYMENT", "MEMBER", "GIFT",
                            "THIRDPARTY", "TEMP", "เงินโอน")):
        return "other"
    return "food"

def apply_local_swap():
    """Swap the reconciled .incoming into the live local oshinei.db.

    Mirrors _maybe_swap_incoming_db() in main.py: drop stale WAL/SHM, then
    atomically replace. If the running app still holds oshinei.db open (Windows
    file lock), the replace fails — we leave .incoming in place so the app's
    own startup swap picks it up on the next restart. Returns True if swapped.
    """
    if not INCOMING.exists():
        print("      [apply-local] no .incoming to apply"); return False
    for suf in ("-wal", "-shm"):
        try: os.remove(str(DB) + suf)
        except OSError: pass
    try:
        os.replace(str(INCOMING), str(DB))   # atomic on same filesystem
        print(f"      [apply-local] ✓ swapped → {DB.name} (local app now serves fresh data after reconnect)")
        return True
    except OSError as e:
        print(f"      [apply-local] ⚠ could not swap now ({e}); .incoming kept — "
              "restart the local app to apply (startup swap will pick it up).")
        return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="2025-01-01", help="sync sales on/after this date")
    ap.add_argument("--apply-local", action="store_true",
                    help="after reconcile, swap .incoming into the live local oshinei.db")
    args = ap.parse_args()

    if not DB.exists():
        sys.exit(f"current DB not found: {DB}")

    print(f"[1/6] copy {DB.name} → {INCOMING.name} (preserves BoM/cost/settings)")
    if INCOMING.exists():
        INCOMING.unlink()
    shutil.copy2(DB, INCOMING)

    sq = sqlite3.connect(str(INCOMING)); sq.row_factory = sqlite3.Row
    # natural-key maps from the EXISTING (preserved) tables
    name2bid = {r["name"]: r["branch_id"] for r in sq.execute("SELECT branch_id,name FROM branches")}
    code2sid = {r["sku_code"]: r["sku_id"] for r in sq.execute("SELECT sku_id,sku_code FROM skus")}
    cat_by_name = {r["name"]: r["category_id"] for r in sq.execute("SELECT category_id,name FROM categories")}
    max_sid = sq.execute("SELECT COALESCE(MAX(sku_id),0) m FROM skus").fetchone()["m"]
    max_cid = sq.execute("SELECT COALESCE(MAX(category_id),0) m FROM categories").fetchone()["m"]

    print("[2/6] connect MSSQL + load Branch / Items / ItemCategory")
    ms = connect_mssql(); mc = ms.cursor(as_dict=True)
    mc.execute("SELECT BranchId, BranchName FROM Branch")
    bid_map = {}                       # MSSQL BranchId → sqlite branch_id
    unmatched_branches = []
    for r in mc.fetchall():
        sn = short_name(r["BranchName"])
        if sn in name2bid:
            bid_map[r["BranchId"]] = name2bid[sn]
        else:
            unmatched_branches.append(r["BranchName"])

    mc.execute("SELECT CategoryId, CategoryName FROM ItemCategory")
    cat_bucket = {}                    # MSSQL CategoryId → bucket
    for r in mc.fetchall():
        cat_bucket[r["CategoryId"]] = bucket_for_cat(r["CategoryName"])

    mc.execute("SELECT ItemId, ItemCode, CategoryId FROM Items")
    itemid2code = {r["ItemId"]: (r["ItemCode"] or "").strip() for r in mc.fetchall()}
    item_map = {iid: code2sid[c] for iid, c in itemid2code.items() if c and c in code2sid}
    new_skus = 0                       # stubs added lazily (only for items that actually have sales)

    print(f"      branches mapped={len(bid_map)} (unmatched/skipped={len(unmatched_branches)}: {unmatched_branches[:6]})")
    print(f"      items pre-mapped={len(item_map)} of {len(itemid2code)}")

    print("[3/6] aggregate sales_sku_monthly (server-side GROUP BY)")
    mc.execute(f"""
        SELECT si.BranchId AS bid, si.ItemId AS iid,
               CONVERT(char(7), s.SaleDate, 126) AS ym,
               SUM(CAST(si.Qty AS float))            AS qty,
               SUM(CAST(si.ExtendedPrice AS float))  AS sub_exvat,
               SUM(CAST(ISNULL(si.Tax,0) AS float))  AS tax
        FROM SaleItem si
        JOIN Sale s ON s.SaleId=si.SaleId AND s.BranchId=si.BranchId
        WHERE {SALE_FILTER} AND ISNULL(si.Active,1)=1 AND s.SaleDate >= %s
        GROUP BY si.BranchId, si.ItemId, CONVERT(char(7), s.SaleDate, 126)
    """, (args.since,))
    skum = []
    skipped_rev = 0.0
    for r in mc.fetchall():
        bid = bid_map.get(r["bid"])
        if bid is None:
            skipped_rev += (r["sub_exvat"] or 0); continue
        sid = item_map.get(r["iid"])
        if sid is None:                              # lazy stub: item with sales but not in skus
            code = itemid2code.get(r["iid"])
            if not code:
                skipped_rev += (r["sub_exvat"] or 0); continue
            max_sid += 1
            sq.execute("INSERT INTO skus(sku_id,sku_code,description,category_id,active,business_type) "
                       "VALUES (?,?,?,?,1,?)", (max_sid, code, code, None, None))
            code2sid[code] = max_sid; item_map[r["iid"]] = max_sid; sid = max_sid; new_skus += 1
        sub = round(r["sub_exvat"] or 0, 2); tax = round(r["tax"] or 0, 2)
        skum.append((bid, r["ym"], sid, round(r["qty"] or 0, 2), sub, tax, round(sub + tax, 2), "Easy"))
    sq.execute("DELETE FROM sales_sku_monthly")
    sq.executemany("INSERT INTO sales_sku_monthly(branch_id,period_month,sku_id,qty,subtotal_ex_vat,tax_total,total_inc_vat,source) "
                   "VALUES (?,?,?,?,?,?,?,?)", skum)
    print(f"      inserted {len(skum):,} rows · new stub skus={new_skus} · skipped revenue (unmapped branch)=฿{skipped_rev:,.0f}")

    print("[4/6] build sales_transaction (header + food/drink/other split)")
    # per-sale food/drink/other ex-VAT split via conditional SUM on SaleItem.CategoryId
    drink_ids = [c for c, b in cat_bucket.items() if b == "drink"]
    other_ids = [c for c, b in cat_bucket.items() if b == "other"]
    def inlist(ids): return "(" + ",".join(str(int(i)) for i in ids) + ")" if ids else "(-1)"
    mc.execute(f"""
        SELECT si.SaleId AS sid, si.BranchId AS bid,
          SUM(CASE WHEN si.CategoryId IN {inlist(drink_ids)} THEN CAST(si.ExtendedPrice AS float) ELSE 0 END) AS drink,
          SUM(CASE WHEN si.CategoryId IN {inlist(other_ids)} THEN CAST(si.ExtendedPrice AS float) ELSE 0 END) AS other,
          SUM(CASE WHEN si.CategoryId NOT IN {inlist(drink_ids + other_ids)} THEN CAST(si.ExtendedPrice AS float) ELSE 0 END) AS food
        FROM SaleItem si JOIN Sale s ON s.SaleId=si.SaleId AND s.BranchId=si.BranchId
        WHERE {SALE_FILTER} AND ISNULL(si.Active,1)=1 AND s.SaleDate >= %s
        GROUP BY si.SaleId, si.BranchId
    """, (args.since,))
    split = {(r["bid"], r["sid"]): (r["food"] or 0, r["drink"] or 0, r["other"] or 0) for r in mc.fetchall()}

    # coupon series applied per sale (keep the largest-discount coupon per bill)
    mc.execute(f"""SELECT c.SaleId AS sid, c.BranchId AS bid, c.CouponSeriesId AS cs,
                 SUM(CAST(ISNULL(c.DiscountAmount,0) AS float)) AS amt
                 FROM CouponCardOutsideSystemUsed c
                 JOIN Sale s ON s.SaleId=c.SaleId AND s.BranchId=c.BranchId
                 WHERE {SALE_FILTER} AND s.SaleDate >= %s
                 GROUP BY c.SaleId, c.BranchId, c.CouponSeriesId""", (args.since,))
    sale_coupon = {}   # (bid,sid) -> coupon_series_id with the biggest discount
    for r in mc.fetchall():
        k = (r["bid"], r["sid"]); cur = sale_coupon.get(k)
        if cur is None or (r["amt"] or 0) > cur[1]:
            sale_coupon[k] = (r["cs"], r["amt"] or 0)

    mc.execute(f"""
        SELECT s.BranchId AS bid, s.SaleId AS sid, s.SaleNum AS num, s.SaleDate AS dt,
               s.NumOfCust AS cust, CAST(s.Discount AS float) AS disc, s.DiscountReasonId AS rid,
               CAST(ISNULL(s.ServiceChargeAmount,0) AS float) AS svc,
               CAST(ISNULL(s.TipsAmount,0) AS float) AS tip, CAST(s.Total AS float) AS total,
               LTRIM(RTRIM(COALESCE(u.Username,''))) AS cashier
        FROM Sale s LEFT JOIN Users u ON u.Id = s.UserId
        WHERE {SALE_FILTER} AND s.SaleDate >= %s
    """, (args.since,))
    txr = []
    for r in mc.fetchall():
        bid = bid_map.get(r["bid"])
        if bid is None:
            continue
        f, d, o = split.get((r["bid"], r["sid"]), (0, 0, 0))
        dt = r["dt"]
        sold = dt.strftime("%Y-%m-%d %H:%M:%S") if hasattr(dt, "strftime") else str(dt)
        receipt = f"{sold[:10]}-{r['num']}-{r['sid']}"
        cs = sale_coupon.get((r["bid"], r["sid"]))
        txr.append((bid, receipt, sold, r["cashier"] or "", "", r["cust"] or 0,
                    round(f, 2), round(d, 2), round(o, 2), round(r["tip"] or 0, 2),
                    0.0, round(r["disc"] or 0, 2), round(r["svc"] or 0, 2), round(r["total"] or 0, 2), "Easy",
                    (r["rid"] or None), (cs[0] if cs else None)))
    sq.execute("DELETE FROM sales_transaction")
    sq.executemany("""INSERT INTO sales_transaction
        (branch_id,receipt_no,sold_at,cashier,table_no,customer_count,food_subtotal,drink_subtotal,
         other_subtotal,tip,sp_discount,discount,service_charge,total_inc_vat,source,
         discount_reason_id,coupon_series_id)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", txr)
    print(f"      inserted {len(txr):,} transactions")

    print("[4b/6] stage report tables (void/staff/payment/table-turn/delivery/coupon/kitchen)")
    stage_reports(sq, mc, args.since, bid_map, item_map, name2bid)

    sq.commit()
    ms.close()

    # Optimize after the bulk rebuild: refresh query-planner stats + compact the
    # file (house rule: VACUUM+ANALYZE after a bulk mutate). Autocommit so VACUUM
    # is not attempted inside a transaction.
    print("[4c/6] optimize (ANALYZE + VACUUM)")
    sq.isolation_level = None
    sq.execute("ANALYZE")
    sq.execute("VACUUM")
    sq.isolation_level = ""

    print("[5/6] reconcile .incoming vs current DB")
    ok = reconcile(sq)
    sq.close()
    if not ok:
        print("[6/6] ⚠ RECONCILE FAILED — .incoming built but NOT safe to push. Investigate.")
        sys.exit(3)
    if args.apply_local:
        print("[6/6] reconciled → applying to local oshinei.db")
        apply_local_swap()
    else:
        print("[6/6] done → oshinei.db.incoming ready & reconciled. Push with:")
        print("       python refresh_fly_db.py --db oshinei.db.incoming")

def stage_reports(sq, mc, since, bid_map, item_map, name2bid):
    """Stage POS report data into pos_* tables — server-side aggregates only.
    IRON RULE: NO POS cost is staged. We keep quantities / revenue / counts /
    timings; all COGS valuation is done later from our BoM."""
    F = SALE_FILTER

    def run(sql, params=None):
        mc.execute(sql, params or (since,)); return mc.fetchall()

    # ── pos_void: cancelled/void items (qty + sale price; cost via BoM later) ──
    sq.executescript("DROP TABLE IF EXISTS pos_void; CREATE TABLE pos_void("
                     "branch_id INT, period_month TEXT, sku_id INT, reason_id INT, qty REAL, ext_price REAL);")
    rows = run(f"""SELECT BranchId bid, ItemId iid, CONVERT(char(7),CancelDate,126) ym, ReasonId rid,
                 SUM(CAST(Qty AS float)) q, SUM(CAST(ExtendedPrice AS float)) ext
                 FROM CancelSaleItem WHERE CancelDate >= %s GROUP BY BranchId, ItemId, CONVERT(char(7),CancelDate,126), ReasonId""")
    sq.executemany("INSERT INTO pos_void VALUES(?,?,?,?,?,?)",
        [(bid_map.get(r["bid"]), r["ym"], item_map.get(r["iid"]), r["rid"], round(r["q"] or 0,2), round(r["ext"] or 0,2))
         for r in rows if bid_map.get(r["bid"])])
    print(f"        pos_void: {len(rows)} groups")

    # ── pos_staff_month: items served per staff (upsell/performance) ──
    sq.executescript("DROP TABLE IF EXISTS pos_staff_month; CREATE TABLE pos_staff_month("
                     "branch_id INT, period_month TEXT, staff_id INT, staff_name TEXT, item_qty REAL);")
    rows = run(f"""SELECT st.BranchId bid, CONVERT(char(7),s.SaleDate,126) ym, st.StaffId sid, st.StaffName nm,
                 SUM(CAST(st.Qty AS float)) q
                 FROM SaleItemStaff st JOIN Sale s ON s.SaleId=st.SaleId AND s.BranchId=st.BranchId
                 WHERE {F} AND s.SaleDate >= %s GROUP BY st.BranchId, CONVERT(char(7),s.SaleDate,126), st.StaffId, st.StaffName""")
    sq.executemany("INSERT INTO pos_staff_month VALUES(?,?,?,?,?)",
        [(bid_map.get(r["bid"]), r["ym"], r["sid"], r["nm"], round(r["q"] or 0,2)) for r in rows if bid_map.get(r["bid"])])
    print(f"        pos_staff_month: {len(rows)} groups")

    # ── pos_payment_month: payment mix ──
    sq.executescript("DROP TABLE IF EXISTS pos_payment_month; CREATE TABLE pos_payment_month("
                     "branch_id INT, period_month TEXT, payment_code TEXT, amount REAL, txn_count INT);")
    rows = run(f"""SELECT p.BranchId bid, CONVERT(char(7),s.SaleDate,126) ym, p.PaymentCode pc,
                 SUM(CAST(p.Amount AS float)) amt, COUNT(*) n
                 FROM SalePayment p JOIN Sale s ON s.SaleId=p.SaleId AND s.BranchId=p.BranchId
                 WHERE {F} AND s.SaleDate >= %s AND ISNULL(p.Active,1)=1 GROUP BY p.BranchId, CONVERT(char(7),s.SaleDate,126), p.PaymentCode""")
    sq.executemany("INSERT INTO pos_payment_month VALUES(?,?,?,?,?)",
        [(bid_map.get(r["bid"]), r["ym"], r["pc"], round(r["amt"] or 0,2), r["n"]) for r in rows if bid_map.get(r["bid"])])
    print(f"        pos_payment_month: {len(rows)} groups")

    # ── pos_payment_day: daily payment mix (powers daily reconciliation) ──
    # Same query as pos_payment_month but grouped by day (char(10)) instead of
    # month (char(7)). Lets the recon app reconcile POS tender vs bank/CC/Grab/
    # Lineman per day. Volume ~ branches × days × codes (small).
    sq.executescript("DROP TABLE IF EXISTS pos_payment_day; CREATE TABLE pos_payment_day("
                     "branch_id INT, day TEXT, payment_code TEXT, amount REAL, txn_count INT);")
    rows = run(f"""SELECT p.BranchId bid, CONVERT(char(10),s.SaleDate,126) d, p.PaymentCode pc,
                 SUM(CAST(p.Amount AS float)) amt, COUNT(*) n
                 FROM SalePayment p JOIN Sale s ON s.SaleId=p.SaleId AND s.BranchId=p.BranchId
                 WHERE {F} AND s.SaleDate >= %s AND ISNULL(p.Active,1)=1 GROUP BY p.BranchId, CONVERT(char(10),s.SaleDate,126), p.PaymentCode""")
    sq.executemany("INSERT INTO pos_payment_day VALUES(?,?,?,?,?)",
        [(bid_map.get(r["bid"]), r["d"], r["pc"], round(r["amt"] or 0,2), r["n"]) for r in rows if bid_map.get(r["bid"])])
    sq.execute("CREATE INDEX IF NOT EXISTS ix_ppd ON pos_payment_day(branch_id, day)")
    print(f"        pos_payment_day: {len(rows)} groups")

    # ── pos_table_turn_day: turnover / dwell ──
    sq.executescript("DROP TABLE IF EXISTS pos_table_turn_day; CREATE TABLE pos_table_turn_day("
                     "branch_id INT, day TEXT, tabs INT, customers INT, avg_turn_min REAL);")
    rows = run(f"""SELECT BranchId bid, CONVERT(char(10),StartTime,126) d, COUNT(*) tabs, SUM(NumOfCust) cust,
                 CAST(AVG(CASE WHEN EndTime>StartTime THEN DATEDIFF(minute,StartTime,EndTime)*1.0 END) AS float) avgmin
                 FROM SaleTab WHERE ISNULL(IsCancel,0)=0 AND StartTime >= %s
                 GROUP BY BranchId, CONVERT(char(10),StartTime,126)""")
    sq.executemany("INSERT INTO pos_table_turn_day VALUES(?,?,?,?,?)",
        [(bid_map.get(r["bid"]), r["d"], r["tabs"], r["cust"] or 0, round(r["avgmin"] or 0,1)) for r in rows if bid_map.get(r["bid"])])
    print(f"        pos_table_turn_day: {len(rows)} groups")

    # ── pos_channel_month: dine-in vs delivery vs takeaway (from Sale, revenue side) ──
    # (SaleByDeliveryType uses varchar Thai branch names that mojibake under UTF-8,
    #  so we derive channel from Sale.DeliveryType / OrderChannel instead — int codes,
    #  branch via BranchId. Map codes→labels in the report layer.)
    sq.executescript("DROP TABLE IF EXISTS pos_channel_month; CREATE TABLE pos_channel_month("
                     "branch_id INT, period_month TEXT, delivery_type INT, order_channel INT, bills INT, customers INT, total_inc_vat REAL);")
    rows = run(f"""SELECT s.BranchId bid, CONVERT(char(7),s.SaleDate,126) ym,
                 ISNULL(s.DeliveryType,0) dt, ISNULL(s.OrderChannel,0) oc,
                 COUNT(*) bills, SUM(s.NumOfCust) cust, SUM(CAST(s.Total AS float)) total
                 FROM Sale s WHERE {F} AND s.SaleDate >= %s
                 GROUP BY s.BranchId, CONVERT(char(7),s.SaleDate,126), ISNULL(s.DeliveryType,0), ISNULL(s.OrderChannel,0)""")
    sq.executemany("INSERT INTO pos_channel_month VALUES(?,?,?,?,?,?,?)",
        [(bid_map.get(r["bid"]), r["ym"], r["dt"], r["oc"], r["bills"], r["cust"] or 0, round(r["total"] or 0,2))
         for r in rows if bid_map.get(r["bid"])])
    print(f"        pos_channel_month: {len(rows)} groups")

    # ── pos_coupon_month: promo / coupon usage ──
    sq.executescript("DROP TABLE IF EXISTS pos_coupon_month; CREATE TABLE pos_coupon_month("
                     "branch_id INT, period_month TEXT, coupon_series_id INT, used_count INT, discount_amount REAL);")
    rows = run(f"""SELECT BranchId bid, CONVERT(char(7),UsedDate,126) ym, CouponSeriesId cs,
                 COUNT(*) n, SUM(CAST(DiscountAmount AS float)) disc
                 FROM CouponCardOutsideSystemUsed WHERE UsedDate >= %s GROUP BY BranchId, CONVERT(char(7),UsedDate,126), CouponSeriesId""")
    sq.executemany("INSERT INTO pos_coupon_month VALUES(?,?,?,?,?)",
        [(bid_map.get(r["bid"]), r["ym"], r["cs"], r["n"], round(r["disc"] or 0,2)) for r in rows if bid_map.get(r["bid"])])
    print(f"        pos_coupon_month: {len(rows)} groups")

    # ── pos_coupon_day: same usage but per DAY (powers the by-day coupon drill) ──
    sq.executescript("DROP TABLE IF EXISTS pos_coupon_day; CREATE TABLE pos_coupon_day("
                     "branch_id INT, day TEXT, coupon_series_id INT, used_count INT, discount_amount REAL);")
    drows = run(f"""SELECT BranchId bid, CONVERT(char(10),UsedDate,126) d, CouponSeriesId cs,
                 COUNT(*) n, SUM(CAST(DiscountAmount AS float)) disc
                 FROM CouponCardOutsideSystemUsed WHERE UsedDate >= %s GROUP BY BranchId, CONVERT(char(10),UsedDate,126), CouponSeriesId""")
    sq.executemany("INSERT INTO pos_coupon_day VALUES(?,?,?,?,?)",
        [(bid_map.get(r["bid"]), r["d"], r["cs"], r["n"], round(r["disc"] or 0,2)) for r in drows if bid_map.get(r["bid"])])
    sq.execute("CREATE INDEX IF NOT EXISTS idx_pcd ON pos_coupon_day(coupon_series_id, day)")
    sq.execute("CREATE INDEX IF NOT EXISTS idx_cpd ON pos_coupon_day(day, branch_id)")
    print(f"        pos_coupon_day: {len(drows)} groups")

    # ── coupon_series: master names for the series ids above (recon drill labels) ──
    sq.executescript("DROP TABLE IF EXISTS coupon_series; CREATE TABLE coupon_series("
                     "coupon_series_id INT PRIMARY KEY, name TEXT, code TEXT, active INT);")
    mc.execute("SELECT CouponSeriesId, CouponSeriesName, CouponSeriesCode, Active FROM CouponSeries")
    csrows = mc.fetchall()
    sq.executemany("INSERT OR REPLACE INTO coupon_series VALUES(?,?,?,?)",
        [(r["CouponSeriesId"], (r["CouponSeriesName"] or "").strip(),
          (r["CouponSeriesCode"] or "").strip(), int(r["Active"] or 0)) for r in csrows])
    print(f"        coupon_series: {len(csrows)} series names")

    # ── pos_kitchen_month: cook/ticket time ──
    sq.executescript("DROP TABLE IF EXISTS pos_kitchen_month; CREATE TABLE pos_kitchen_month("
                     "branch_id INT, period_month TEXT, avg_cook_sec REAL, cooked_count INT);")
    rows = run(f"""SELECT si.BranchId bid, CONVERT(char(7),s.SaleDate,126) ym,
                 CAST(AVG(CASE WHEN si.CookedTime IS NOT NULL AND si.CookedTime>si.InsertDate THEN DATEDIFF(second,si.InsertDate,si.CookedTime)*1.0 END) AS float) avgsec,
                 SUM(CASE WHEN si.CookedTime IS NOT NULL THEN 1 ELSE 0 END) cooked
                 FROM SaleItem si JOIN Sale s ON s.SaleId=si.SaleId AND s.BranchId=si.BranchId
                 WHERE {F} AND s.SaleDate >= %s GROUP BY si.BranchId, CONVERT(char(7),s.SaleDate,126)""")
    sq.executemany("INSERT INTO pos_kitchen_month VALUES(?,?,?,?)",
        [(bid_map.get(r["bid"]), r["ym"], round(r["avgsec"] or 0,1), r["cooked"] or 0) for r in rows if bid_map.get(r["bid"])])
    print(f"        pos_kitchen_month: {len(rows)} groups")

    # ── sales_sku_daily: per-branch per-sku DAILY qty/revenue (rolling 120 days) ──
    #    Foundation for daily menu-spike anomaly (#5) and daily cost/head (#10,
    #    COGS via our BoM only). Bounded window keeps the DB push light.
    import datetime as _dt
    mc.execute("SELECT CONVERT(char(10), MAX(SaleDate), 126) AS mx FROM Sale")
    mx = mc.fetchone()["mx"]
    dly_since = since
    if mx:
        y, m, d = (int(x) for x in mx.split("-"))
        dly_since = (_dt.date(y, m, d) - _dt.timedelta(days=120)).isoformat()
    sq.executescript("DROP TABLE IF EXISTS sales_sku_daily; CREATE TABLE sales_sku_daily("
                     "branch_id INT, day TEXT, sku_id INT, qty REAL, subtotal_ex_vat REAL);")
    mc.execute(f"""
        SELECT si.BranchId bid, si.ItemId iid, CONVERT(char(10), s.SaleDate, 126) d,
               SUM(CAST(si.Qty AS float)) qty, SUM(CAST(si.ExtendedPrice AS float)) rev
        FROM SaleItem si JOIN Sale s ON s.SaleId=si.SaleId AND s.BranchId=si.BranchId
        WHERE {F} AND ISNULL(si.Active,1)=1 AND s.SaleDate >= %s
        GROUP BY si.BranchId, si.ItemId, CONVERT(char(10), s.SaleDate, 126)
    """, (dly_since,))
    drows = mc.fetchall()
    sq.executemany("INSERT INTO sales_sku_daily VALUES(?,?,?,?,?)",
        [(bid_map.get(r["bid"]), r["d"], item_map.get(r["iid"]), round(r["qty"] or 0,2), round(r["rev"] or 0,2))
         for r in drows if bid_map.get(r["bid"]) and item_map.get(r["iid"])])
    sq.execute("CREATE INDEX IF NOT EXISTS ix_ssd ON sales_sku_daily(branch_id, day, sku_id)")
    print(f"        sales_sku_daily: {len(drows)} rows (since {dly_since})")

    # ── sale_bill_cogs: per-BILL BoM COGS (rolling 31 days) → exact P&L per bill ──
    #    Per-sku BoM unit cost from OUR data (recipe → sku_cost → BUFFET tier=0;
    #    IRON RULE: BoM only). Heavy pull (bill×item) but stored per-bill (lean).
    bill_since = dly_since
    if mx:
        y, m, d = (int(x) for x in mx.split("-"))
        bill_since = (_dt.date(y, m, d) - _dt.timedelta(days=31)).isoformat()
    costmap = {}
    for r in sq.execute("""
        SELECT s.sku_id, s.sku_code,
          COALESCE(
            (SELECT SUM(bi.ingredient_cost) FROM bom_ingredients bi WHERE bi.sku_id=s.sku_id),
            (SELECT SUM(bi2.ingredient_cost) FROM bom_ingredients bi2
               JOIN bom_sku_alias a ON a.resolved_sku_id=bi2.sku_id WHERE a.bom_sku_code=s.sku_code),
            (SELECT sc.unit_cost FROM sku_cost sc WHERE sc.sku_id=s.sku_id), 0) AS c
        FROM skus s""").fetchall():
        costmap[r["sku_id"]] = 0.0 if (r["sku_code"] or "").startswith("BUFFET") else (r["c"] or 0.0)
    sq.executescript("DROP TABLE IF EXISTS sale_bill_cogs; CREATE TABLE sale_bill_cogs("
                     "receipt_no TEXT PRIMARY KEY, branch_id INT, day TEXT, cogs_bom REAL);")
    mc.execute(f"""
        SELECT s.SaleId sid, s.BranchId bid, s.SaleNum num, CONVERT(char(10),s.SaleDate,126) d,
               si.ItemId iid, SUM(CAST(si.Qty AS float)) qty,
               SUM(CAST(si.ExtendedPrice AS float)) rev
        FROM SaleItem si JOIN Sale s ON s.SaleId=si.SaleId AND s.BranchId=si.BranchId
        WHERE {F} AND ISNULL(si.Active,1)=1 AND s.SaleDate >= %s
        GROUP BY s.SaleId, s.BranchId, s.SaleNum, CONVERT(char(10),s.SaleDate,126), si.ItemId
    """, (bill_since,))
    bills = {}
    lines = []   # (receipt_no, sku_id, qty, rev, bom_cost) — per-menu-line detail, kept for the loss-bill drill
    for r in mc.fetchall():
        bid = bid_map.get(r["bid"])
        if bid is None:
            continue
        sid_local = item_map.get(r["iid"])
        c = costmap.get(sid_local, 0.0)
        qty = r["qty"] or 0
        rcpt = f"{r['d']}-{r['num']}-{r['sid']}"
        b = bills.get(rcpt)
        if b is None:
            bills[rcpt] = [bid, r["d"], qty * c]
        else:
            b[2] += qty * c
        if sid_local is not None:
            lines.append((rcpt, sid_local, round(qty, 2), round(r["rev"] or 0, 2), round(qty * c, 4)))
    sq.executemany("INSERT OR REPLACE INTO sale_bill_cogs VALUES(?,?,?,?)",
        [(rc, v[0], v[1], round(v[2], 2)) for rc, v in bills.items()])
    sq.execute("CREATE INDEX IF NOT EXISTS ix_sbc ON sale_bill_cogs(branch_id, day)")
    print(f"        sale_bill_cogs: {len(bills)} bills (since {bill_since})")

    # ── sale_bill_item: per-receipt MENU LINES for LOSS bills only (COGS > revenue) ──
    #    Powers the "บิลขาดทุน" menu drill-down. Scoped to loss bills (~3% of bills)
    #    to keep the DB light — NOT a full per-bill line table. Loss test uses the
    #    SAME revenue basis as the per-bill P&L endpoint (total_inc_vat/1.07 from the
    #    sales_transaction just staged above) so the set matches the loss table exactly.
    #    rev = SUM(ExtendedPrice) treated ex-VAT (consistent with sales_sku_daily);
    #    bom_cost = qty × per-SKU BoM unit cost (BUFFET tier = 0; IRON RULE: BoM only).
    revmap = {row["receipt_no"]: (row["t"] or 0) / 1.07 for row in
              sq.execute("SELECT receipt_no, total_inc_vat t FROM sales_transaction WHERE total_inc_vat>0")}
    loss_set = {rc for rc, v in bills.items() if v[2] > revmap.get(rc, float("inf"))}
    sq.executescript("DROP TABLE IF EXISTS sale_bill_item; CREATE TABLE sale_bill_item("
                     "receipt_no TEXT, sku_id INT, qty REAL, rev REAL, bom_cost REAL);")
    loss_lines = [ln for ln in lines if ln[0] in loss_set]
    sq.executemany("INSERT INTO sale_bill_item VALUES(?,?,?,?,?)", loss_lines)
    sq.execute("CREATE INDEX IF NOT EXISTS ix_sbi ON sale_bill_item(receipt_no)")
    print(f"        sale_bill_item: {len(loss_lines)} lines across {len(loss_set)} loss bills")

    # ── Extra POS-deep-dive tables (separate "Extra" report group) ──
    stage_extra(sq, mc, since, bid_map, item_map)


def _th(s):
    """Decode TIS-620 (CP874) varchar that pymssql returns mis-decoded as latin1
    under the default UTF-8 connection. Returns the original string on failure."""
    if not s:
        return s
    try:
        return s.encode("latin1").decode("cp874")
    except Exception:
        return s


def stage_extra(sq, mc, since, bid_map, item_map):
    """Extra analytics from previously-untapped POS tables (separate UI group).
    IRON RULE: still NO POS cost — quantities / counts / amounts / over-short only."""
    def run(sql, params=None):
        mc.execute(sql, params if params is not None else (since,)); return mc.fetchall()

    # ── pos_cashcount_day: cash over/short + ops summary per branch per day ──
    sq.executescript("DROP TABLE IF EXISTS pos_cashcount_day; CREATE TABLE pos_cashcount_day("
                     "branch_id INT, day TEXT, over_short REAL, total_sales REAL, pax INT, "
                     "invoices INT, discount REAL, cancel REAL);")
    rows = run("""SELECT BranchId bid, CONVERT(char(10),SalesDate,126) d,
                 SUM(CAST(OverShortAmount AS float)) os, SUM(CAST(TotalSales AS float)) ts,
                 SUM(CountPax) pax, SUM(CountInvoice) inv, SUM(CAST(TotalDiscount AS float)) disc,
                 SUM(CAST(TotalCancel AS float)) cancel
                 FROM CashCount WHERE ISNULL(Active,1)=1 AND SalesDate >= %s
                 GROUP BY BranchId, CONVERT(char(10),SalesDate,126)""")
    sq.executemany("INSERT INTO pos_cashcount_day VALUES(?,?,?,?,?,?,?,?)",
        [(bid_map.get(r["bid"]), r["d"], round(r["os"] or 0,2), round(r["ts"] or 0,2), r["pax"] or 0,
          r["inv"] or 0, round(r["disc"] or 0,2), round(r["cancel"] or 0,2))
         for r in rows if bid_map.get(r["bid"])])
    sq.execute("CREATE INDEX IF NOT EXISTS ix_cc ON pos_cashcount_day(branch_id, day)")
    print(f"        pos_cashcount_day: {len(rows)} groups")

    # ── pos_customer_type_month: walk-in vs Grab vs Line Man vs takeaway vs credit ──
    sq.executescript("DROP TABLE IF EXISTS pos_customer_type_month; CREATE TABLE pos_customer_type_month("
                     "branch_id INT, period_month TEXT, type_name TEXT, customers INT, net REAL);")
    rows = run("""SELECT b.BranchId bid, CONVERT(char(7),sct.SaleDate,126) ym, sct.CustomerTypeName tn,
                 SUM(sct.NumOfCustomer) cust, SUM(CAST(sct.NetAmnt AS float)) net
                 FROM SaleByCustomerType sct JOIN Branch b ON b.BranchCode=sct.BranchCode
                 WHERE sct.SaleDate >= %s
                 GROUP BY b.BranchId, CONVERT(char(7),sct.SaleDate,126), sct.CustomerTypeName""")
    sq.executemany("INSERT INTO pos_customer_type_month VALUES(?,?,?,?,?)",
        [(bid_map.get(r["bid"]), r["ym"], _th(r["tn"]), r["cust"] or 0, round(r["net"] or 0,2))
         for r in rows if bid_map.get(r["bid"])])
    print(f"        pos_customer_type_month: {len(rows)} groups")

    # ── pos_modifier: top special requests per menu per branch (all-time, free choices) ──
    sq.executescript("DROP TABLE IF EXISTS pos_modifier; CREATE TABLE pos_modifier("
                     "branch_id INT, sku_id INT, choice_name TEXT, n INT, qty REAL);")
    rows = run("""SELECT BranchId bid, ItemId iid, ChoiceName cn, COUNT(*) n,
                 SUM(CAST(ISNULL(ChoiceQty,1) AS float)) q
                 FROM SaleItemModifier WHERE ChoiceName IS NOT NULL
                 GROUP BY BranchId, ItemId, ChoiceName HAVING COUNT(*) >= 20""", ())
    sq.executemany("INSERT INTO pos_modifier VALUES(?,?,?,?,?)",
        [(bid_map.get(r["bid"]), item_map.get(r["iid"]), r["cn"], r["n"], round(r["q"] or 0,2))
         for r in rows if bid_map.get(r["bid"])])
    sq.execute("CREATE INDEX IF NOT EXISTS ix_mod ON pos_modifier(branch_id)")
    print(f"        pos_modifier: {len(rows)} groups")

    # ── pos_price_change: price-override log (governance) — rolling since ──
    sq.executescript("DROP TABLE IF EXISTS pos_price_change; CREATE TABLE pos_price_change("
                     "branch_id INT, change_date TEXT, sku_id INT, item_desc TEXT, "
                     "default_price REAL, new_price REAL, user_name TEXT);")
    rows = run("""SELECT BranchId bid, CONVERT(char(19),ChangeDate,126) cd, ItemId iid, ItemDesc descr,
                 CAST(DefaultPrice AS float) dp, CAST(NewPrice AS float) np, UserName un
                 FROM ChangePriceLog WHERE ChangeDate >= %s""")
    sq.executemany("INSERT INTO pos_price_change VALUES(?,?,?,?,?,?,?)",
        [(bid_map.get(r["bid"]), r["cd"], item_map.get(r["iid"]), r["descr"],
          round(r["dp"] or 0,2), round(r["np"] or 0,2), r["un"])
         for r in rows if bid_map.get(r["bid"])])
    print(f"        pos_price_change: {len(rows)} rows")

    # ── pos_zone: table zones — #tables + seats per zone per branch (FBTable master) ──
    sq.executescript("DROP TABLE IF EXISTS pos_zone; CREATE TABLE pos_zone("
                     "branch_id INT, zone_id INT, tables INT, seats INT);")
    rows = run("""SELECT BranchId bid, ISNULL(ZoneId,0) zid, COUNT(*) tabs, SUM(ISNULL(NumberOfSeat,0)) seats
                 FROM FBTable WHERE ISNULL(Active,1)=1 GROUP BY BranchId, ISNULL(ZoneId,0)""", ())
    sq.executemany("INSERT INTO pos_zone VALUES(?,?,?,?)",
        [(bid_map.get(r["bid"]), r["zid"], r["tabs"], r["seats"] or 0) for r in rows if bid_map.get(r["bid"])])
    print(f"        pos_zone: {len(rows)} groups")

    # ── pos_cancel_reason: delete/void reason lookup (decoded Thai) ──
    sq.executescript("DROP TABLE IF EXISTS pos_cancel_reason; CREATE TABLE pos_cancel_reason("
                     "reason_id INT PRIMARY KEY, reason_desc TEXT);")
    rrows = run("SELECT ReasonId rid, ReasonDesc rd FROM DeleteSaleReason", ())
    sq.executemany("INSERT OR REPLACE INTO pos_cancel_reason VALUES(?,?)",
        [(r["rid"], _th(r["rd"])) for r in rrows])
    print(f"        pos_cancel_reason: {len(rrows)} rows")

    # ── pos_cancel_bill: whole BILLS voided/deleted (Sale.Active=0) per branch/month/reason.
    #    Distinct from pos_void (which is cancelled ITEMS within otherwise-valid bills). ──
    sq.executescript("DROP TABLE IF EXISTS pos_cancel_bill; CREATE TABLE pos_cancel_bill("
                     "branch_id INT, period_month TEXT, reason_id INT, bills INT, amount REAL);")
    rows = run("""SELECT BranchId bid, CONVERT(char(7),SaleDate,126) ym, ISNULL(DeleteReasonId,0) rid,
                 COUNT(*) n, SUM(CAST(ISNULL(Total,0) AS float)) amt
                 FROM Sale WHERE ISNULL(Active,1)=0 AND SaleDate >= %s
                 GROUP BY BranchId, CONVERT(char(7),SaleDate,126), ISNULL(DeleteReasonId,0)""")
    sq.executemany("INSERT INTO pos_cancel_bill VALUES(?,?,?,?,?)",
        [(bid_map.get(r["bid"]), r["ym"], r["rid"], r["n"], round(r["amt"] or 0,2))
         for r in rows if bid_map.get(r["bid"])])
    print(f"        pos_cancel_bill: {len(rows)} groups")

    # ── pos_discount_reason: discount-reason lookup (Sale.DiscountReasonId -> name) ──
    #    e.g. "ส่วนลด 10%", "ส่วนลดด่วน". ReasonId 0 = no reason set on the bill.
    sq.executescript("DROP TABLE IF EXISTS pos_discount_reason; CREATE TABLE pos_discount_reason("
                     "reason_id INT PRIMARY KEY, reason_desc TEXT, amount_percent REAL);")
    drrows = run("SELECT ReasonId rid, ReasonDesc rd, CAST(ISNULL(AmountPercent,0) AS float) pct FROM DiscountSaleReason", ())
    sq.executemany("INSERT OR REPLACE INTO pos_discount_reason VALUES(?,?,?)",
        [(r["rid"], _th(r["rd"]), r["pct"] or 0) for r in drrows])
    print(f"        pos_discount_reason: {len(drrows)} rows")

    # ── pos_discount_month: discounted BILLS per branch/month/reason (Sale.Discount>0).
    #    Surfaces "why was it discounted" — reason_id 0 = untyped (no reason chosen). ──
    sq.executescript("DROP TABLE IF EXISTS pos_discount_month; CREATE TABLE pos_discount_month("
                     "branch_id INT, period_month TEXT, reason_id INT, bills INT, amount REAL);")
    rows = run(f"""SELECT s.BranchId bid, CONVERT(char(7),s.SaleDate,126) ym, ISNULL(s.DiscountReasonId,0) rid,
                 COUNT(*) n, SUM(CAST(s.Discount AS float)) amt
                 FROM Sale s WHERE {SALE_FILTER} AND s.Discount>0 AND s.SaleDate >= %s
                 GROUP BY s.BranchId, CONVERT(char(7),s.SaleDate,126), ISNULL(s.DiscountReasonId,0)""")
    sq.executemany("INSERT INTO pos_discount_month VALUES(?,?,?,?,?)",
        [(bid_map.get(r["bid"]), r["ym"], r["rid"], r["n"], round(r["amt"] or 0,2))
         for r in rows if bid_map.get(r["bid"])])
    print(f"        pos_discount_month: {len(rows)} groups")

    # ── pos_cancel_bill_detail: INDIVIDUAL voided bills (TOP 20000 most recent) so the
    #    UI can drill per-bill to inspect each error. Bounded for size. ──
    # Sale has no dedicated "delete user" column (only DeleteReasonId/Notes/Date), so the
    # best "who" for a voided bill is the login user who owned it: Sale.UserId -> Users.Username.
    # NOTE: individual (per-person) attribution is NOT possible in this POS — the Staff
    # master table is empty and SaleItemStaff.StaffName is blank for all rows, so cancel_user
    # (a shared login/role account) is the only "who" available.
    sq.executescript("DROP TABLE IF EXISTS pos_cancel_bill_detail; CREATE TABLE pos_cancel_bill_detail("
                     "branch_id INT, period_month TEXT, sale_date TEXT, receipt_no TEXT, reason_id INT, amount REAL, cancel_user TEXT);")
    drows = run("""SELECT TOP 20000 s.BranchId bid, CONVERT(char(19),s.SaleDate,126) dt, s.SaleNum num,
                 ISNULL(s.DeleteReasonId,0) rid, CAST(ISNULL(s.Total,0) AS float) amt,
                 LTRIM(RTRIM(COALESCE(u.Username,''))) duser
                 FROM Sale s LEFT JOIN Users u ON u.Id = s.UserId
                 WHERE ISNULL(s.Active,1)=0 AND s.SaleDate >= %s ORDER BY s.SaleDate DESC""")
    sq.executemany("INSERT INTO pos_cancel_bill_detail VALUES(?,?,?,?,?,?,?)",
        [(bid_map.get(r["bid"]), (r["dt"] or '')[:7], r["dt"], str(r["num"]), r["rid"], round(r["amt"] or 0,2),
          (r["duser"] or None))
         for r in drows if bid_map.get(r["bid"])])
    sq.execute("CREATE INDEX IF NOT EXISTS idx_cbd_branch ON pos_cancel_bill_detail(branch_id, period_month)")
    print(f"        pos_cancel_bill_detail: {len(drows)} bills")


def reconcile(sq):
    cur = sqlite3.connect(str(DB)); cur.row_factory = sqlite3.Row
    def one(con, q, p=()):
        return con.execute(q, p).fetchone()[0]
    checks = []   # (name, old, new, mode) — mode: 'strict' = |Δ|<2%; 'nodrop' = Δ ≥ -2% (growth OK)
    # The synced db's latest month is the LIVE current month, which legitimately
    # GROWS between syncs as new days finalize. So we can't strict-compare it —
    # an incremental daily sync would always trip a 2% drift gate mid-month.
    # Strategy:
    #   • SETTLED months (anything before the live month): must match within 2%
    #     in both DBs → catches corruption / history loss.
    #   • LIVE month: only guard against a DROP (data loss); growth is expected.
    syn_max = sq.execute("SELECT MAX(period_month) m FROM sales_sku_monthly").fetchone()["m"]
    cur_max = cur.execute("SELECT MAX(period_month) m FROM sales_sku_monthly").fetchone()["m"]
    # latest SETTLED month = newest month, in the current DB, strictly before the live month
    anchor = cur.execute(
        "SELECT MAX(period_month) m FROM sales_sku_monthly WHERE period_month < ?", (syn_max,)
    ).fetchone()["m"]
    print(f"    (current latest={cur_max} · synced latest={syn_max} · settled-anchor={anchor})")

    # LIVE month — growth expected, only block a drop
    lv_new = one(sq, "SELECT ROUND(SUM(subtotal_ex_vat),0) FROM sales_sku_monthly WHERE period_month=?", (syn_max,)) or 0
    lv_old = one(cur, "SELECT ROUND(SUM(subtotal_ex_vat),0) FROM sales_sku_monthly WHERE period_month=?", (syn_max,)) or 0
    checks.append((f"LIVE rev {syn_max} (ex-VAT, growth OK)", lv_old, lv_new, 'nodrop'))
    lvb_new = one(sq, "SELECT COUNT(*) FROM sales_transaction WHERE substr(sold_at,1,7)=?", (syn_max,))
    lvb_old = one(cur, "SELECT COUNT(*) FROM sales_transaction WHERE substr(sold_at,1,7)=?", (syn_max,))
    checks.append((f"LIVE bills {syn_max} (growth OK)", lvb_old, lvb_new, 'nodrop'))

    # SETTLED month anchor — strict integrity check (should be identical run-to-run)
    if anchor:
        a_new = one(sq, "SELECT ROUND(SUM(subtotal_ex_vat),0) FROM sales_sku_monthly WHERE period_month=?", (anchor,)) or 0
        a_old = one(cur, "SELECT ROUND(SUM(subtotal_ex_vat),0) FROM sales_sku_monthly WHERE period_month=?", (anchor,)) or 0
        checks.append((f"SETTLED rev {anchor} (ex-VAT)", a_old, a_new, 'strict'))
        ab_new = one(sq, "SELECT COUNT(*) FROM sales_transaction WHERE substr(sold_at,1,7)=?", (anchor,))
        ab_old = one(cur, "SELECT COUNT(*) FROM sales_transaction WHERE substr(sold_at,1,7)=?", (anchor,))
        checks.append((f"SETTLED bills {anchor}", ab_old, ab_new, 'strict'))

    # Ubon Apr 2026 receipts ex-VAT — fixed historical anchor (must never move)
    ub_new = one(sq, "SELECT ROUND(SUM(total_inc_vat)/1.07,0) FROM sales_transaction t JOIN branches b ON b.branch_id=t.branch_id WHERE b.name='อุบลราชธานี' AND substr(t.sold_at,1,7)='2026-04'") or 0
    ub_old = one(cur, "SELECT ROUND(SUM(total_inc_vat)/1.07,0) FROM sales_transaction t JOIN branches b ON b.branch_id=t.branch_id WHERE b.name='อุบลราชธานี' AND substr(t.sold_at,1,7)='2026-04'") or 0
    checks.append(("Ubon Apr2026 ex-VAT (receipts)", ub_old, ub_new, 'strict'))
    # buffet (B) revenue should be ~0 (informational)
    buf_new = one(sq, "SELECT ROUND(SUM(subtotal_ex_vat),0) FROM sales_sku_monthly m JOIN skus s ON s.sku_id=m.sku_id WHERE s.description LIKE '(B)%' AND m.period_month=?", (syn_max,)) or 0
    checks.append(("buffet (B) rev (should ≈0)", 0, buf_new, 'info'))

    ok = True
    print("    " + "-"*82)
    print(f"    {'check':42} {'current':>15} {'synced':>15}  Δ%")
    for name, old, new, mode in checks:
        d = (new-old)/old*100 if old else (0 if not new else 999)
        if mode == 'strict':
            passed = abs(d) < 2.0
        elif mode == 'nodrop':
            passed = d >= -2.0          # growth is fine; only a real drop fails
        else:  # info — buffet near-zero, never gates
            passed = (abs(new) < (lv_old or 1) * 0.002)
        if mode in ('strict', 'nodrop') and not passed:
            ok = False
        print(f"    {name:42} {old:>15,.0f} {new:>15,.0f}  {d:+6.2f}%{'  ✓' if passed else '  ⚠'}")
    print("    " + "-"*82)
    cur.close()
    return ok

if __name__ == "__main__":
    main()
