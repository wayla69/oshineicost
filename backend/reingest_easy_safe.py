"""
reingest_easy_safe.py — careful re-ingest of Easy POS data.

Preserves:
  - sku_cost, sku_cost_monthly, bom_ingredients, bom_sku_alias
  - branches, categories, skus (only ADDS new SKUs)
  - all *_archive tables, app_settings, branch_targets, closed_hours

Wipes & rebuilds:
  - sales_sku_monthly  (source='Easy')
  - sales_transaction  (source='Easy')
  - sales_month_summary (source='Easy')
"""
import re, shutil, sqlite3
from pathlib import Path
from datetime import datetime

import ingest_easy as ie

HERE = Path(__file__).parent.resolve()
DB_PATH = HERE / "oshinei.db"
DATA_DIR = HERE.parent / "Data for Oshinei"

def main():
    # ---- Backup ----
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    bk = HERE / f"oshinei.db.backup_pre_phase17a_{ts}"
    shutil.copy2(DB_PATH, bk)
    print(f"Backup -> {bk.name}")
    # Also copy WAL/SHM if present (snapshot-consistent)
    for ext in ('-wal','-shm'):
        f = HERE / f"oshinei.db{ext}"
        if f.exists():
            shutil.copy2(f, HERE / f"oshinei.db.backup_pre_phase17a_{ts}{ext}")

    # ---- Snapshot preservation counts ----
    con = sqlite3.connect(str(DB_PATH))
    con.execute("PRAGMA foreign_keys=OFF")
    cur = con.cursor()
    def snap():
        d = {}
        for tbl in ['sku_cost','sku_cost_monthly','bom_ingredients','bom_sku_alias',
                    'branches','categories','skus']:
            cur.execute(f"SELECT COUNT(*) FROM {tbl}")
            d[tbl] = cur.fetchone()[0]
        cur.execute("SELECT SUM(unit_cost) FROM sku_cost"); d['sku_cost_sum'] = cur.fetchone()[0]
        return d
    pre = snap()
    print("Pre-ingest snapshot:", pre)

    # ---- Wipe Easy rows ----
    cur.execute("DELETE FROM sales_sku_monthly WHERE source='Easy'")
    n1 = cur.rowcount
    cur.execute("DELETE FROM sales_transaction WHERE source='Easy'")
    n2 = cur.rowcount
    cur.execute("DELETE FROM sales_month_summary WHERE source='Easy'")
    n3 = cur.rowcount
    con.commit()
    print(f"Wiped: sku_monthly={n1}  transactions={n2}  month_summary={n3}")

    # ---- 23 ES_*.xlsx (transactions) ----
    esb_files = sorted(DATA_DIR.glob("23 ES_*.xlsx"))
    print(f"\n=== Loading {len(esb_files)} 23 ES_ files (per-transaction) ===")
    total_tx = total_summ = 0
    for path in esb_files:
        stem = path.stem
        m = re.match(r"23\s+ES_(\w+)\s+(\d{2})$", stem, re.IGNORECASE)
        hint = ""
        if m:
            mo = ie.ENG_MONTHS.get(m.group(1).lower(),0); yy=int(m.group(2))
            if mo: hint = f"{2000+yy:04d}-{mo:02d}"
        try:
            r = ie.parse_es_typeb_xlsx(path, hint)
            tn, sn = ie.load_es_typeb(con, r, path.name)
            total_tx += tn; total_summ += sn
        except Exception as e:
            print(f"ERROR {path.name}: {e}")

    # ---- ES1_*.xlsx (sku monthly) ----
    es1_files = sorted(DATA_DIR.glob("ES1_*.xlsx"))
    print(f"\n=== Loading {len(es1_files)} ES1 files (sku monthly) ===")
    total_sku = 0
    for path in es1_files:
        hint = ie.period_from_filename_es1(path.stem)
        try:
            r = ie.parse_es1_xlsx(path, hint)
            n = ie.load_es1(con, r, path.name)
            total_sku += n
        except Exception as e:
            print(f"ERROR {path.name}: {e}")

    con.commit()

    print(f"\nTotals: sku_monthly_inserted={total_sku}  tx_inserted={total_tx}  summary_inserted={total_summ}")

    # ---- Post-snapshot ----
    post = snap()
    print("\nPost-ingest snapshot:", post)
    for k in ['sku_cost','sku_cost_monthly','bom_ingredients','bom_sku_alias','sku_cost_sum']:
        a, b = pre.get(k), post.get(k)
        ok = "OK" if a == b else "*** CHANGED ***"
        print(f"  {k}: {a} -> {b}  {ok}")
    for k in ['branches','categories','skus']:
        a, b = pre.get(k), post.get(k)
        delta = b - a
        print(f"  {k}: {a} -> {b}  (+{delta})")

    # Verify final counts
    cur.execute("SELECT COUNT(*) FROM sales_sku_monthly WHERE source='Easy'"); print('Final sku_monthly Easy:', cur.fetchone()[0])
    cur.execute("SELECT COUNT(*) FROM sales_transaction WHERE source='Easy'"); print('Final transaction Easy:', cur.fetchone()[0])
    cur.execute("SELECT COUNT(*) FROM sales_month_summary WHERE source='Easy'"); print('Final month_summary Easy:', cur.fetchone()[0])
    cur.execute("SELECT SUM(subtotal_ex_vat) FROM sales_sku_monthly WHERE source='Easy'")
    print('Total subtotal_ex_vat:', cur.fetchone()[0])

    con.close()

if __name__ == "__main__":
    main()
