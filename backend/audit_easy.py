"""
audit_easy.py — READ-ONLY scan of Easy POS xlsx files vs. current DB.

Outputs a coverage matrix (branch x month) for both:
  - ES1_*.xlsx  -> sales_sku_monthly
  - 23 ES_*.xlsx -> sales_transaction
"""
import sys, io, json, re
from pathlib import Path
import sqlite3

import ingest_easy as ie  # this module re-wraps stdout itself

HERE = Path(__file__).parent.resolve()
DATA_DIR = HERE.parent / "Data for Oshinei"
DB_PATH = HERE / "oshinei.db"

def main():
    es1_files = sorted(DATA_DIR.glob("ES1_*.xlsx"))
    esb_files = sorted(DATA_DIR.glob("23 ES_*.xlsx"))
    print(f"ES1 files: {len(es1_files)}   23 ES_ files: {len(esb_files)}")

    # ---- Scan ES1 files (SKU monthly) ----
    es1_scan = {}  # (branch, period) -> sku_row_count
    es1_file_totals = {}  # filename -> sku_row_total
    es1_periods = set()
    es1_branches = set()
    for path in es1_files:
        stem = path.stem
        hint = ie.period_from_filename_es1(stem)
        try:
            r = ie.parse_es1_xlsx(path, hint)
        except Exception as e:
            print(f"  PARSE ERROR {path.name}: {e}")
            continue
        per = r.get('period') or hint
        n_total = 0
        for seg in r['segments']:
            b = seg['branch']; p = seg['period'] or per
            n = len(seg['skus'])
            es1_scan[(b,p)] = es1_scan.get((b,p),0) + n
            es1_periods.add(p); es1_branches.add(b)
            n_total += n
        es1_file_totals[path.name] = n_total
        print(f"  ES1 {path.name:<30} period={per} segments={len(r['segments'])} sku_rows={n_total}")

    # ---- Scan 23 ES_ files (transactions) ----
    esb_scan = {}  # (branch, period) -> tx_count
    esb_file_totals = {}
    esb_periods = set()
    esb_branches = set()
    for path in esb_files:
        stem = path.stem
        m = re.match(r"23\s+ES_(\w+)\s+(\d{2})$", stem, re.IGNORECASE)
        hint = ""
        if m:
            mo = ie.ENG_MONTHS.get(m.group(1).lower(),0)
            yy = int(m.group(2))
            if mo: hint = f"{2000+yy:04d}-{mo:02d}"
        try:
            r = ie.parse_es_typeb_xlsx(path, hint)
        except Exception as e:
            print(f"  PARSE ERROR {path.name}: {e}")
            continue
        per = r.get('period') or hint
        n_total = 0
        for seg in r['segments']:
            b = seg['branch']; p = seg['period'] or per
            n = len(seg['transactions'])
            esb_scan[(b,p)] = esb_scan.get((b,p),0) + n
            esb_periods.add(p); esb_branches.add(b)
            n_total += n
        esb_file_totals[path.name] = n_total
        print(f"  ES_B {path.name:<30} period={per} segments={len(r['segments'])} tx={n_total}")

    # ---- DB content ----
    con = sqlite3.connect(str(DB_PATH))
    cur = con.cursor()
    cur.execute("""SELECT b.name, ssm.period_month, COUNT(*)
                   FROM sales_sku_monthly ssm
                   JOIN branches b ON b.branch_id=ssm.branch_id
                   WHERE ssm.source='Easy'
                   GROUP BY b.name, ssm.period_month""")
    db_es1 = {(r[0],r[1]): r[2] for r in cur.fetchall()}

    cur.execute("""SELECT b.name, strftime('%Y-%m', st.sold_at), COUNT(*)
                   FROM sales_transaction st
                   JOIN branches b ON b.branch_id=st.branch_id
                   WHERE st.source='Easy'
                   GROUP BY b.name, strftime('%Y-%m', st.sold_at)""")
    db_esb = {(r[0],r[1]): r[2] for r in cur.fetchall()}

    db_branches = set(r[0] for r in cur.execute("SELECT name FROM branches WHERE active=1"))

    # ---- Coverage matrix ES1 ----
    all_branches = sorted(es1_branches | esb_branches | db_branches)
    all_periods = sorted(es1_periods | esb_periods)
    print("\n\n===== ES1 (sku_monthly) COVERAGE: file_rows / db_rows =====")
    print(f"{'BRANCH':<28} " + " ".join(f"{p[-5:]:>9}" for p in all_periods))
    es1_gaps = []
    for b in all_branches:
        cells = []
        for p in all_periods:
            f = es1_scan.get((b,p), 0)
            d = db_es1.get((b,p), 0)
            cell = f"{f}/{d}"
            if f and d == 0:
                cell = f"*{cell}*"
                es1_gaps.append((b,p,f,d,'missing'))
            elif f and d < f*0.8:
                cell = f"!{cell}!"
                es1_gaps.append((b,p,f,d,'under'))
            elif d > f and f > 0 and d > f*1.2:
                es1_gaps.append((b,p,f,d,'extra'))
            cells.append(f"{cell:>9}")
        print(f"{b[:28]:<28} " + " ".join(cells))

    print("\n===== 23 ES_ (transactions) COVERAGE: file_tx / db_tx =====")
    print(f"{'BRANCH':<28} " + " ".join(f"{p[-5:]:>9}" for p in all_periods))
    esb_gaps = []
    for b in all_branches:
        cells = []
        for p in all_periods:
            f = esb_scan.get((b,p), 0)
            d = db_esb.get((b,p), 0)
            cell = f"{f}/{d}"
            if f and d == 0:
                cell = f"*{cell}*"
                esb_gaps.append((b,p,f,d,'missing'))
            elif f and d < f*0.8:
                cell = f"!{cell}!"
                esb_gaps.append((b,p,f,d,'under'))
            elif d > f and f > 0 and d > f*1.2:
                esb_gaps.append((b,p,f,d,'extra'))
            cells.append(f"{cell:>9}")
        print(f"{b[:28]:<28} " + " ".join(cells))

    # ---- Summary lists ----
    print("\n===== BRANCHES IN FILES BUT NOT IN DB =====")
    file_branches = es1_branches | esb_branches
    for b in sorted(file_branches - db_branches):
        print(f"  {b}")
    print("\n===== ACTIVE DB BRANCHES NOT IN ANY FILE =====")
    for b in sorted(db_branches - file_branches):
        print(f"  {b}")

    print("\n===== ES1 GAPS (file>db) =====")
    for g in es1_gaps:
        print(f"  {g[0]:<28} {g[1]}  file={g[2]:>5}  db={g[3]:>5}  ({g[4]})")
    print(f"\nTotal ES1 gaps: {len(es1_gaps)}")

    print("\n===== ES_B GAPS (file>db) =====")
    for g in esb_gaps:
        print(f"  {g[0]:<28} {g[1]}  file={g[2]:>6}  db={g[3]:>6}  ({g[4]})")
    print(f"\nTotal ES_B gaps: {len(esb_gaps)}")

    # File-level totals vs DB
    file_es1_total = sum(es1_scan.values())
    file_esb_total = sum(esb_scan.values())
    db_es1_total = sum(db_es1.values())
    db_esb_total = sum(db_esb.values())
    print("\n===== TOTALS =====")
    print(f"  ES1 file sum: {file_es1_total:>8}   db sum: {db_es1_total:>8}   diff: {file_es1_total-db_es1_total:+}")
    print(f"  ESB file sum: {file_esb_total:>8}   db sum: {db_esb_total:>8}   diff: {file_esb_total-db_esb_total:+}")
    print(f"  ES1 branches in files: {len(es1_branches)}    ESB branches in files: {len(esb_branches)}    DB active: {len(db_branches)}")

    # Save JSON snapshot
    out = {
        "es1_scan": {f"{k[0]}||{k[1]}": v for k,v in es1_scan.items()},
        "esb_scan": {f"{k[0]}||{k[1]}": v for k,v in esb_scan.items()},
        "db_es1":   {f"{k[0]}||{k[1]}": v for k,v in db_es1.items()},
        "db_esb":   {f"{k[0]}||{k[1]}": v for k,v in db_esb.items()},
        "es1_gaps": es1_gaps, "esb_gaps": esb_gaps,
        "es1_file_totals": es1_file_totals, "esb_file_totals": esb_file_totals,
        "file_branches": sorted(file_branches), "db_branches": sorted(db_branches),
        "all_periods": all_periods,
    }
    (HERE / "audit_easy_report.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f"\nWrote {HERE / 'audit_easy_report.json'}")

if __name__ == "__main__":
    main()
