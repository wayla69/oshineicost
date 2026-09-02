# -*- coding: utf-8 -*-
"""build_menu_fact.py — build the branch x day x entity fact tables that power the
in-app "🧪 วิเคราะห์วัตถุดิบ (Menu Impact)" tab (/api/reports/menu-impact).

Entity = one menu SKU ("s:<sku_id>") or a predefined ingredient group ("g:<slug>",
e.g. all squid menus). For every buffet bill (has a BUFFET/BUFFETCHILD line) we
aggregate, per branch x day x entity, the WITH-set statistics:
    bills, pax, qty, ent_cogs (qty x BoM cost), ent_hc (only members with unit
    cost >= HC_COST), plus full-bill sums over those bills: cogs / net revenue /
    high-cost cogs — and per branch x day totals for the WITHOUT-side.
The endpoint then derives with-vs-without per-head comparisons for ANY date
range and ANY entity without touching MSSQL.

Source is the live Easy POS MSSQL (same creds/.env as sync_mssql.py). COGS uses
sku_effective_cost excluding buffet TIER packages (see gp_formula_rule / TIER_CODES
in main.py — packages would double-count). Bills are Finalize+Active+not-Return.

Run (backfill or refresh):
    python build_menu_fact.py --from 2026-01-01 --to 2026-07-01
    python build_menu_fact.py               # default: rebuild last 40 days
Intended cadence: run after each sync_mssql.py / before a data refresh so the
tab stays current (tables live in oshinei.db and survive the sync's file copy).
"""
import os, sys, time, sqlite3, argparse, datetime as dt
from pathlib import Path

HERE = Path(__file__).resolve().parent
DB = HERE / "oshinei.db"

def load_env():
    f = HERE / ".env"
    if f.exists():
        for line in f.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())
load_env()

from ingest_may26_update import BRANCH_ALIAS

SALE_FILTER = "s.IsFinalize=1 AND s.Active=1 AND ISNULL(s.IsReturn,0)=0"

# Per-LINE qty sanity guard — keep in lockstep with sync_mssql.MAX_SANE_LINE_QTY
# (this file is standalone-runnable, hence the duplicated constant, same as
# SALE_FILTER above). A fat-fingered POS line (ระยอง 2026-08-03: qty=1e9 on one
# sashimi line) otherwise floods menu_fact_* with billions of phantom qty×cost.
MAX_SANE_LINE_QTY = 1000
QTY1 = (f"(CASE WHEN ABS(CAST(si.Qty AS float)) > {MAX_SANE_LINE_QTY} "
        f"THEN 0 ELSE CAST(si.Qty AS float) END)")   # SaleItem alias `si`
QTY2 = QTY1.replace("si.", "si2.")                    # SaleItem alias `si2`
TIER_CODES = ('BUFFET', 'BUFFET350', 'BUFFET500', 'BUFFET590', 'BUFFET799',
              'BUFFETCHILD', 'BUFFETCHILDFREE', 'B01', 'B02', 'B07', 'B11')
HC_COST = 30.0   # unit BoM cost >= this = "เมนูต้นทุนสูง" (high-cost consumption metric)
VAT = 1.07
# Buffet price tiers. The adult BUFFET line's ExtendedPrice is the menu price with
# BOTH the 5% service charge and the 7% VAT stripped out — the POS stores the base and
# adds SC then VAT on top. Bills whose adult unit price matches none of the canonical
# prices (line discounts, promo pricing) fall in 'promo'; no adult cover = 'child'.
#
# The Grand Menu that went live on 2026-09-01 quotes NET prices instead: ฿789 on the
# menu is what the guest pays all-in, so its ExtendedPrice is 789 / 1.05 / 1.07 =
# 702.27. Matching a net price list against the stored base put 99% of 1-Sep bills in
# 'promo' (1,265 of 1,277 bills). So each era gets its own list, matched on its own
# basis, and the label stays the number printed on that menu.
PRICE_TIERS = (350, 590, 649, 689, 799, 999)   # until 2026-08-31 — menu price BEFORE SC + VAT
TIER_TOL = 3.0   # ฿ tolerance around a canonical price

NEW_MENU_FROM = "2026-09-01"                   # Grand Menu 2026-09 [[grand_menu_2026_09]]
PRICE_TIERS_NEW = (459, 629, 789, 999, 1299)   # from 2026-09-01 — NET price (SC + VAT inside)
TIER_TOL_NET = 4.0   # ฿ tolerance, on the net price (the ±3 base tolerance grossed up)
SC_MULT = 1.05       # service charge, when the bill carries one

# Ingredient groups: slug -> (thai label, SQL LIKE patterns on skus.description,
# extra exact sku_codes). Only (B)-prefixed buffet menus + whitelisted codes join
# a group, so ราคาขาย/takeaway variants don't pollute the bill-set.
GROUPS = {
    "squid":   ("ปลาหมึก (ทุกเมนู)",            ["%หมึก%"], []),
    "jelly":   ("แมงกะพรุนหัวกระสุน",           ["%กะพรุน%"], []),
    "durian":  ("ทุเรียน",                      ["%ทุเรียน%"], ["KKN018", "KKN019", "DUR-999"]),
    "salmon":  ("แซลมอน (ทุกเมนู)",             ["%แซลมอน%", "%แซลม่อน%"], []),
    "shrimp":  ("กุ้ง (ทุกเมนู)",               ["%กุ้ง%"], []),
    "oyster":  ("หอยนางรม/หอยเชลล์",            ["%หอยนางรม%", "%หอยเชลล์%"], []),
    "beef":    ("เนื้อวัว/วากิว",               ["%เนื้อ%", "%วากิว%", "%ลิ้นวัว%"], []),
    "dong":    ("ของดอง (แซลมอน/กุ้ง/ซาบะ ดอง)", ["%ดอง%"], []),
    "tuna":    ("ทูน่า/ฮามาจิ ซาชิมิ",           ["%ทูน่า%", "%ฮามาจิ%"], []),
    "rice":    ("เมนูข้าว (ข้าวปั้น/ห่อสาหร่าย/ข้าวหน้า)", ["%ข้าวปั้น%", "%ข้าวห่อ%", "%ข้าวหน้า%", "%ข้าวผัด%"], []),
}
BEEF_EXCLUDE = "%ปลาเนื้อ%"   # 'ปลาเนื้อขาว' is a fish, not beef


def connect_mssql():
    import pymssql
    required = ("OSH_SVR", "OSH_PORT", "OSH_USER", "OSH_PWD", "OSH_DB")
    missing = [k for k in required if not os.environ.get(k)]
    if missing:
        sys.exit(f"MSSQL credentials missing: {', '.join(missing)} (backend/.env)")
    return pymssql.connect(
        server=os.environ["OSH_SVR"], port=os.environ["OSH_PORT"],
        user=os.environ["OSH_USER"], password=os.environ["OSH_PWD"],
        database=os.environ["OSH_DB"], login_timeout=20, timeout=1800)


def month_windows(d_from: str, d_to: str):
    """[(lo, hi)) calendar-month chunks covering [d_from, d_to)."""
    lo = dt.date.fromisoformat(d_from)
    end = dt.date.fromisoformat(d_to)
    out = []
    while lo < end:
        nxt = (lo.replace(day=1) + dt.timedelta(days=32)).replace(day=1)
        out.append((lo.isoformat(), min(nxt, end).isoformat()))
        lo = min(nxt, end)
    return out


def main():
    ap = argparse.ArgumentParser()
    default_from = (dt.date.today() - dt.timedelta(days=40)).isoformat()
    ap.add_argument("--from", dest="d_from", default=default_from)
    ap.add_argument("--to", dest="d_to", default=(dt.date.today() + dt.timedelta(days=1)).isoformat())
    ap.add_argument("--db", default=str(DB))
    args = ap.parse_args()

    sq = sqlite3.connect(args.db)
    # schema v2 (+tier): if an old (no-tier) table exists, drop and force a full
    # rebuild — the daily 40-day window would otherwise leave pre-window days
    # missing from the new schema.
    old = [r[1] for r in sq.execute(
        "SELECT * FROM pragma_table_info('menu_fact_day')")]
    if old and "tier" not in old:
        print("      schema v1→v2 (+tier): dropping menu_fact tables → full rebuild")
        sq.executescript("DROP TABLE IF EXISTS menu_fact_day; DROP TABLE IF EXISTS menu_fact_total;")
        old = []
    # empty table (fresh DB, or an interrupted migration run) → the default 40-day
    # window would silently leave history missing; widen to the analysis epoch.
    if (not old or not sq.execute("SELECT 1 FROM menu_fact_total LIMIT 1").fetchone()) \
            and args.d_from > "2026-01-01":
        args.d_from = "2026-01-01"
        print(f"      empty/new fact table → widened window to {args.d_from} → {args.d_to}")
    sq.executescript("""
      CREATE TABLE IF NOT EXISTS menu_fact_total(
        branch_id INT, day TEXT, tier TEXT, bills INT, pax REAL, cogs REAL, rev REAL, hc REAL,
        PRIMARY KEY(branch_id, day, tier));
      CREATE TABLE IF NOT EXISTS menu_fact_day(
        branch_id INT, day TEXT, tier TEXT, ent TEXT, bills INT, pax REAL, qty REAL,
        ent_cogs REAL, ent_hc REAL, cogs REAL, rev REAL, hc REAL,
        PRIMARY KEY(branch_id, day, tier, ent));
      CREATE INDEX IF NOT EXISTS ix_mfd_ent ON menu_fact_day(ent, day);
      CREATE TABLE IF NOT EXISTS menu_fact_meta(
        ent TEXT PRIMARY KEY, label TEXT, kind TEXT, unit_cost REAL, members TEXT);
      CREATE TABLE IF NOT EXISTS menu_fact_state(k TEXT PRIMARY KEY, v TEXT);
    """)

    # ── local maps: costs (non-tier), branch name → id, sku code → id ──
    cost_by_code = {c: rc for c, rc in sq.execute(
        "SELECT s.sku_code, e.recipe_cost FROM sku_effective_cost e JOIN skus s USING(sku_id) "
        "WHERE e.recipe_cost > 0") if c not in TIER_CODES}
    code2sid = {c: i for i, c in sq.execute("SELECT sku_id, sku_code FROM skus")}
    name2bid = {nm: b for b, nm in sq.execute("SELECT branch_id, name FROM branches")}
    desc_by_code = {c: (d or "") for c, d in sq.execute("SELECT sku_code, description FROM skus")}

    print(f"[1/4] connect MSSQL · window {args.d_from} → {args.d_to}")
    mc = connect_mssql().cursor()
    mc.execute("SELECT BranchId, BranchName FROM Branch")
    bid_map = {}
    for bid, nm in mc.fetchall():
        nm = (nm or "").strip()
        short = BRANCH_ALIAS.get(nm, nm)
        if short in name2bid:
            bid_map[int(bid)] = name2bid[short]

    mc.execute("SELECT ItemId, ItemCode FROM Items")
    iid2code, code2iids = {}, {}
    for iid, code in mc.fetchall():
        code = (code or "").strip()
        if code:
            iid2code[int(iid)] = code
            code2iids.setdefault(code, []).append(int(iid))

    tier_ids = [i for c in TIER_CODES for i in code2iids.get(c, [])]
    cost_vals = [(i, rc) for c, rc in cost_by_code.items() for i in code2iids.get(c, [])]

    # group membership: (B)-prefixed menus (or whitelisted codes) matching a pattern
    def like(s, pat):  # SQL LIKE with only % wildcards
        parts = pat.strip("%").split("%")
        pos = 0
        for p in parts:
            i = s.find(p, pos)
            if i < 0:
                return False
            pos = i + len(p)
        return True
    grp_members = {}
    for slug, (label, pats, extra) in GROUPS.items():
        codes = set(extra)
        for code, d in desc_by_code.items():
            if not d.startswith("(B)"):
                continue
            if slug == "beef" and like(d, BEEF_EXCLUDE):
                continue
            if any(like(d, p) for p in pats):
                codes.add(code)
        grp_members[slug] = sorted(c for c in codes if c in code2iids)
    grp_vals = [(i, slug) for slug, codes in grp_members.items()
                for c in codes for i in code2iids.get(c, [])]

    def inlist(ids): return "(" + ",".join(str(int(i)) for i in ids) + ")"
    cost_sql = ",".join(f"({i},{rc:.6f})" for i, rc in cost_vals)
    grp_sql = ",".join(f"({i},'{slug}')" for i, slug in grp_vals)
    adult_ids = code2iids.get("BUFFET", [])
    # `nf` (computed per bill in bill0) lifts the stored base price back to what the
    # menu quotes: always the 7% VAT, plus the 5% service charge when this bill was
    # actually charged one (delivery/takeaway bills are not, and grossing those up by
    # a service charge they never paid would push every one of them into 'promo').
    when = lambda tiers, expr, tol: " ".join(
        f"WHEN ABS({expr} - {p}) <= {tol} THEN '{p}'" for p in tiers)
    tier_case = ("CASE WHEN ISNULL(aq, 0) <= 0 THEN 'child' "
                 f"WHEN d >= '{NEW_MENU_FROM}' THEN (CASE "
                 + when(PRICE_TIERS_NEW, "aext / aq * nf", TIER_TOL_NET)
                 + " ELSE 'promo' END) "
                 + when(PRICE_TIERS, "aext / aq", TIER_TOL)
                 + " ELSE 'promo' END")

    BILL_CTE = f"""
WITH bill0 AS (
  SELECT s.SaleId sid, s.BranchId bid, CONVERT(char(10), s.SaleDate, 126) d,
    ISNULL(s.NumOfCust, 0) pax, CAST(ISNULL(s.Total, 0) AS float) / {VAT} net,
    CASE WHEN ISNULL(s.ServiceChargeAmount, 0) > 0 THEN {VAT * SC_MULT:.4f} ELSE {VAT} END nf,
    SUM(CASE WHEN si.ItemId IN {inlist(adult_ids)} THEN {QTY1} ELSE 0 END) aq,
    SUM(CASE WHEN si.ItemId IN {inlist(adult_ids)} THEN CAST(si.ExtendedPrice AS float) ELSE 0 END) aext,
    SUM({QTY1} * ISNULL(c.cost, 0)) cogs,
    SUM(CASE WHEN c.cost >= {HC_COST} THEN {QTY1} * c.cost ELSE 0 END) hc
  FROM Sale s
  JOIN SaleItem si ON si.SaleId = s.SaleId AND si.BranchId = s.BranchId
  LEFT JOIN (VALUES {cost_sql}) c(ItemId, cost) ON c.ItemId = si.ItemId
  WHERE {SALE_FILTER} AND ISNULL(si.Active, 1) = 1 AND s.SaleDate >= %s AND s.SaleDate < %s
  GROUP BY s.SaleId, s.BranchId, CONVERT(char(10), s.SaleDate, 126),
           ISNULL(s.NumOfCust, 0), CAST(ISNULL(s.Total, 0) AS float),
           CASE WHEN ISNULL(s.ServiceChargeAmount, 0) > 0 THEN {VAT * SC_MULT:.4f} ELSE {VAT} END
  HAVING SUM(CASE WHEN si.ItemId IN {inlist(tier_ids)} THEN 1 ELSE 0 END) > 0
     AND ISNULL(s.NumOfCust, 0) BETWEEN 1 AND 40
),
bill AS (SELECT sid, bid, d, pax, net, cogs, hc, {tier_case} tier FROM bill0)"""

    Q_TOTAL = BILL_CTE + "SELECT bid, d, tier, COUNT(*), SUM(pax), SUM(cogs), SUM(net), SUM(hc) FROM bill GROUP BY bid, d, tier"
    Q_ITEM = BILL_CTE + f""",
x AS (SELECT si2.SaleId sid, si2.BranchId bid, si2.ItemId iid,
        SUM({QTY2}) q,
        SUM({QTY2} * ISNULL(c2.cost, 0)) qc,
        SUM(CASE WHEN c2.cost >= {HC_COST} THEN {QTY2} * c2.cost ELSE 0 END) qh
      FROM SaleItem si2
      JOIN Sale s ON s.SaleId = si2.SaleId AND s.BranchId = si2.BranchId
      LEFT JOIN (VALUES {cost_sql}) c2(ItemId, cost) ON c2.ItemId = si2.ItemId
      WHERE {SALE_FILTER} AND ISNULL(si2.Active, 1) = 1 AND s.SaleDate >= %s AND s.SaleDate < %s
      GROUP BY si2.SaleId, si2.BranchId, si2.ItemId)
SELECT x.iid, b.bid, b.d, b.tier, COUNT(*), SUM(b.pax), SUM(x.q), SUM(x.qc), SUM(x.qh),
       SUM(b.cogs), SUM(b.net), SUM(b.hc)
FROM bill b JOIN x ON x.sid = b.sid AND x.bid = b.bid
GROUP BY x.iid, b.bid, b.d, b.tier"""
    Q_GROUP = BILL_CTE + f""",
x AS (SELECT si2.SaleId sid, si2.BranchId bid, g.grp,
        SUM({QTY2}) q,
        SUM({QTY2} * ISNULL(c2.cost, 0)) qc,
        SUM(CASE WHEN c2.cost >= {HC_COST} THEN {QTY2} * c2.cost ELSE 0 END) qh
      FROM SaleItem si2
      JOIN (VALUES {grp_sql}) g(ItemId, grp) ON g.ItemId = si2.ItemId
      JOIN Sale s ON s.SaleId = si2.SaleId AND s.BranchId = si2.BranchId
      LEFT JOIN (VALUES {cost_sql}) c2(ItemId, cost) ON c2.ItemId = si2.ItemId
      WHERE {SALE_FILTER} AND ISNULL(si2.Active, 1) = 1 AND s.SaleDate >= %s AND s.SaleDate < %s
      GROUP BY si2.SaleId, si2.BranchId, g.grp)
SELECT x.grp, b.bid, b.d, b.tier, COUNT(*), SUM(b.pax), SUM(x.q), SUM(x.qc), SUM(x.qh),
       SUM(b.cogs), SUM(b.net), SUM(b.hc)
FROM bill b JOIN x ON x.sid = b.sid AND x.bid = b.bid
GROUP BY x.grp, b.bid, b.d, b.tier"""

    def run_retry(q, params, tag):
        nonlocal mc
        for attempt in range(5):
            try:
                t = time.time()
                mc.execute(q, params)
                rows = mc.fetchall()
                print(f"      {tag}: {len(rows):,} rows in {time.time()-t:.0f}s", flush=True)
                return rows
            except Exception as e:
                print(f"      {tag} attempt {attempt+1} FAILED: {e}", flush=True)
                time.sleep(15)
                mc = connect_mssql().cursor()
        sys.exit(f"giving up: {tag}")

    print(f"[2/4] pull {len(month_windows(args.d_from, args.d_to))} month chunk(s) "
          f"(items costed={len(cost_vals)}, groups={len(GROUPS)}, tier ids={len(tier_ids)})")
    tot_rows, ent_rows = [], []
    for lo, hi in month_windows(args.d_from, args.d_to):
        print(f"    {lo} → {hi}")
        for r in run_retry(Q_TOTAL, (lo, hi), "totals"):
            b = bid_map.get(int(r[0]))
            if b:
                tot_rows.append((b, r[1], r[2], int(r[3]), float(r[4] or 0), float(r[5] or 0), float(r[6] or 0), float(r[7] or 0)))
        for r in run_retry(Q_ITEM, (lo, hi) * 2, "per-item"):
            b = bid_map.get(int(r[1]))
            code = iid2code.get(int(r[0]))
            sid = code2sid.get(code) if code else None
            if b and sid:
                ent_rows.append((b, r[2], r[3], f"s:{sid}", int(r[4]), float(r[5] or 0), float(r[6] or 0),
                                 float(r[7] or 0), float(r[8] or 0), float(r[9] or 0), float(r[10] or 0), float(r[11] or 0)))
        for r in run_retry(Q_GROUP, (lo, hi) * 2, "groups"):
            b = bid_map.get(int(r[1]))
            if b:
                ent_rows.append((b, r[2], r[3], f"g:{r[0]}", int(r[4]), float(r[5] or 0), float(r[6] or 0),
                                 float(r[7] or 0), float(r[8] or 0), float(r[9] or 0), float(r[10] or 0), float(r[11] or 0)))

    print(f"[3/4] write SQLite: {len(tot_rows):,} total rows · {len(ent_rows):,} entity rows")
    sq.execute("DELETE FROM menu_fact_total WHERE day >= ? AND day < ?", (args.d_from, args.d_to))
    sq.execute("DELETE FROM menu_fact_day WHERE day >= ? AND day < ?", (args.d_from, args.d_to))
    sq.executemany("INSERT OR REPLACE INTO menu_fact_total VALUES (?,?,?,?,?,?,?,?)", tot_rows)
    sq.executemany("INSERT OR REPLACE INTO menu_fact_day VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", ent_rows)

    print("[4/4] refresh meta")
    sq.execute("DELETE FROM menu_fact_meta")
    meta = []
    for code, sid in code2sid.items():
        d = desc_by_code.get(code) or code
        meta.append((f"s:{sid}", d, "sku", float(cost_by_code.get(code, 0) or 0), code))
    for slug, (label, _p, _e) in GROUPS.items():
        meta.append((f"g:{slug}", label, "group", 0.0, ",".join(grp_members[slug])))
    sq.executemany("INSERT INTO menu_fact_meta VALUES (?,?,?,?,?)", meta)
    now = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    sq.execute("INSERT OR REPLACE INTO menu_fact_state VALUES ('last_build', ?)", (now,))
    sq.execute("INSERT OR REPLACE INTO menu_fact_state VALUES ('hc_cost', ?)", (str(HC_COST),))
    sq.commit()
    lo, hi = sq.execute("SELECT MIN(day), MAX(day) FROM menu_fact_total").fetchone()
    print(f"DONE · coverage {lo} → {hi} · built {now}")


if __name__ == "__main__":
    main()
