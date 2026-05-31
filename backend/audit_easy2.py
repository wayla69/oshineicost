"""
audit_easy2.py — refined audit. Aggregate ES1 SKUs by sku_code per (branch,month)
to match how the DB stores them, then compare counts AND revenue.
"""
import sys, io, json, re
from pathlib import Path
import sqlite3

import ingest_easy as ie

HERE = Path(__file__).parent.resolve()
DATA_DIR = HERE.parent / "Data for Oshinei"
DB_PATH = HERE / "oshinei.db"

def main():
    es1_files = sorted(DATA_DIR.glob("ES1_*.xlsx"))
    # (branch, period) -> {'unique_skus': int, 'qty_sum':, 'sub_ex': , 'total_iv': }
    es1_agg = {}
    for path in es1_files:
        stem = path.stem
        hint = ie.period_from_filename_es1(stem)
        try:
            r = ie.parse_es1_xlsx(path, hint)
        except Exception as e:
            print(f"PARSE ERROR {path.name}: {e}"); continue
        for seg in r['segments']:
            b = seg['branch']; p = seg['period'] or hint
            agg = {}
            for row in seg['skus']:
                sku_code, desc, unit, qty, sub_ex, tax1, tax23, total_iv, cat = row
                if sku_code not in agg:
                    agg[sku_code] = [0.0, 0.0, 0.0]
                agg[sku_code][0] += qty
                agg[sku_code][1] += sub_ex
                agg[sku_code][2] += total_iv
            key = (b, p)
            cur = es1_agg.get(key, [0, 0.0, 0.0, 0.0])
            cur[0] += len(agg)
            cur[1] += sum(v[0] for v in agg.values())
            cur[2] += sum(v[1] for v in agg.values())
            cur[3] += sum(v[2] for v in agg.values())
            es1_agg[key] = cur

    con = sqlite3.connect(str(DB_PATH))
    cur = con.cursor()
    cur.execute("""SELECT b.name, ssm.period_month, COUNT(*), SUM(ssm.qty),
                          SUM(ssm.subtotal_ex_vat), SUM(ssm.total_inc_vat)
                   FROM sales_sku_monthly ssm
                   JOIN branches b ON b.branch_id=ssm.branch_id
                   WHERE ssm.source='Easy'
                   GROUP BY b.name, ssm.period_month""")
    db_es1 = {(r[0],r[1]): [r[2], r[3] or 0, r[4] or 0, r[5] or 0] for r in cur.fetchall()}

    all_keys = sorted(set(es1_agg) | set(db_es1))
    gaps = []
    print(f"{'BRANCH':<28} {'PERIOD':<8} {'f_sku':>6} {'d_sku':>6} {'f_rev':>14} {'d_rev':>14}  diff")
    for k in all_keys:
        f = es1_agg.get(k, [0,0,0,0])
        d = db_es1.get(k, [0,0,0,0])
        f_sku, f_qty, f_sub, f_tot = f
        d_sku, d_qty, d_sub, d_tot = d
        diff_rev = f_sub - d_sub
        flag = ""
        if f_sku and d_sku == 0:
            flag = " MISSING"
            gaps.append((k, f, d, "missing"))
        elif f_sku and abs(f_sku - d_sku) > max(2, 0.05*f_sku):
            flag = " ROW_DIFF"
            gaps.append((k, f, d, "row_diff"))
        elif f_sub and abs(diff_rev) > max(100, 0.02*abs(f_sub)):
            flag = " REV_DIFF"
            gaps.append((k, f, d, "rev_diff"))
        if flag or f_sku != d_sku:
            print(f"{k[0][:28]:<28} {k[1]:<8} {f_sku:>6} {d_sku:>6} {f_sub:>14,.0f} {d_sub:>14,.0f} {diff_rev:>+12,.0f}{flag}")

    print(f"\nTotal gaps: {len(gaps)}")
    # Total revenue compare
    f_total_rev = sum(v[2] for v in es1_agg.values())
    d_total_rev = sum(v[2] for v in db_es1.values())
    print(f"Total subtotal_ex_vat — files: {f_total_rev:,.0f}   db: {d_total_rev:,.0f}   diff: {f_total_rev-d_total_rev:+,.0f}")

    # Save
    (HERE / "audit_easy_report2.json").write_text(json.dumps({
        "es1_agg": {f"{k[0]}||{k[1]}": v for k,v in es1_agg.items()},
        "db_es1":  {f"{k[0]}||{k[1]}": v for k,v in db_es1.items()},
        "gaps": [(f"{k[0]}||{k[1]}", f, d, why) for k,f,d,why in gaps],
    }, ensure_ascii=False, indent=2), encoding='utf-8')

if __name__ == "__main__":
    main()
