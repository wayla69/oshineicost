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

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="2025-01-01", help="sync sales on/after this date")
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

    mc.execute(f"""
        SELECT s.BranchId AS bid, s.SaleId AS sid, s.SaleNum AS num, s.SaleDate AS dt,
               s.NumOfCust AS cust, CAST(s.Discount AS float) AS disc,
               CAST(ISNULL(s.ServiceChargeAmount,0) AS float) AS svc,
               CAST(ISNULL(s.TipsAmount,0) AS float) AS tip, CAST(s.Total AS float) AS total
        FROM Sale s WHERE {SALE_FILTER} AND s.SaleDate >= %s
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
        txr.append((bid, receipt, sold, "", "", r["cust"] or 0,
                    round(f, 2), round(d, 2), round(o, 2), round(r["tip"] or 0, 2),
                    0.0, round(r["disc"] or 0, 2), round(r["svc"] or 0, 2), round(r["total"] or 0, 2), "Easy"))
    sq.execute("DELETE FROM sales_transaction")
    sq.executemany("""INSERT INTO sales_transaction
        (branch_id,receipt_no,sold_at,cashier,table_no,customer_count,food_subtotal,drink_subtotal,
         other_subtotal,tip,sp_discount,discount,service_charge,total_inc_vat,source)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", txr)
    print(f"      inserted {len(txr):,} transactions")

    print("[4b/6] stage report tables (void/staff/payment/table-turn/delivery/coupon/kitchen)")
    stage_reports(sq, mc, args.since, bid_map, item_map, name2bid)

    sq.commit()
    ms.close()

    print("[5/6] reconcile .incoming vs current DB")
    ok = reconcile(sq)
    sq.close()
    if not ok:
        print("[6/6] ⚠ RECONCILE FAILED — .incoming built but NOT safe to push. Investigate.")
        sys.exit(3)
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


def reconcile(sq):
    cur = sqlite3.connect(str(DB)); cur.row_factory = sqlite3.Row
    def one(con, q, p=()):
        return con.execute(q, p).fetchone()[0]
    checks = []
    # Compare on the latest month the CURRENT db has (a month both share) — the
    # synced db also has the live current month which current lacks (that's the point).
    lm = cur.execute("SELECT MAX(period_month) m FROM sales_sku_monthly").fetchone()["m"]
    syn_max = sq.execute("SELECT MAX(period_month) m FROM sales_sku_monthly").fetchone()["m"]
    print(f"    (current latest={lm} · synced latest={syn_max} — comparing common month {lm})")
    new_rev = one(sq, "SELECT ROUND(SUM(subtotal_ex_vat),0) FROM sales_sku_monthly WHERE period_month=?", (lm,)) or 0
    old_rev = one(cur, "SELECT ROUND(SUM(subtotal_ex_vat),0) FROM sales_sku_monthly WHERE period_month=?", (lm,)) or 0
    checks.append((f"chain rev {lm} (ex-VAT)", old_rev, new_rev))
    # bills latest month
    new_b = one(sq, "SELECT COUNT(*) FROM sales_transaction WHERE substr(sold_at,1,7)=?", (lm,))
    old_b = one(cur, "SELECT COUNT(*) FROM sales_transaction WHERE substr(sold_at,1,7)=?", (lm,))
    checks.append((f"bills {lm}", old_b, new_b))
    # Ubon Apr 2026 receipts ex-VAT
    for con, tag in ((cur, "old"), (sq, "new")):
        pass
    ub_new = one(sq, "SELECT ROUND(SUM(total_inc_vat)/1.07,0) FROM sales_transaction t JOIN branches b ON b.branch_id=t.branch_id WHERE b.name='อุบลราชธานี' AND substr(t.sold_at,1,7)='2026-04'") or 0
    ub_old = one(cur, "SELECT ROUND(SUM(total_inc_vat)/1.07,0) FROM sales_transaction t JOIN branches b ON b.branch_id=t.branch_id WHERE b.name='อุบลราชธานี' AND substr(t.sold_at,1,7)='2026-04'") or 0
    checks.append(("Ubon Apr2026 ex-VAT (receipts)", ub_old, ub_new))
    # buffet (B) revenue should be ~0
    buf_new = one(sq, "SELECT ROUND(SUM(subtotal_ex_vat),0) FROM sales_sku_monthly m JOIN skus s ON s.sku_id=m.sku_id WHERE s.description LIKE '(B)%' AND m.period_month=?", (lm,)) or 0
    checks.append(("buffet (B) rev (should ≈0)", 0, buf_new))
    # critical anchors that gate a prod push (must be within 2%)
    critical = {f"chain rev {lm} (ex-VAT)", "Ubon Apr2026 ex-VAT (receipts)", f"bills {lm}"}
    ok = True
    print("    " + "-"*78)
    print(f"    {'check':38} {'current':>16} {'synced':>16}  Δ%")
    for name, old, new in checks:
        d = (new-old)/old*100 if old else (0 if not new else 999)
        passed = abs(d) < 2.0 or (old == 0 and abs(new) < old_rev*0.002)
        if name in critical and not passed:
            ok = False
        print(f"    {name:38} {old:>16,.0f} {new:>16,.0f}  {d:+6.2f}%{'  ✓' if passed else '  ⚠'}")
    print("    " + "-"*78)
    cur.close()
    return ok

if __name__ == "__main__":
    main()
