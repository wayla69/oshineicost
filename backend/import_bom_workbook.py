"""
import_bom_workbook.py — load BoM recipes from the consolidated workbook
    Data for Oshinei/Data Cleasing/OSHINEI_BOM_Workbook.xlsx

Why: this single workbook has 252 fully-costed menus (vs 205 currently in the DB)
and raises BoM coverage of qty-sold from ~21% → ~70%. 251/252 menu codes match an
existing skus.sku_code exactly.

Sheet used: 'การคำนวณต้นทุน' (cost calculation), a flat menu-grouped table:
  - a MENU HEADER row:  col0 = menu code (e.g. B0106), col1 = menu name
  - then INGREDIENT rows: col0 = seq number, then:
      col1 รหัสสินค้า (ingredient_code)   -> ingredient_code
      col2 ชื่อวัตถุดิบ (name)            -> ingredient_name
      col3 หน่วย (unit)                  -> unit
      col4 ปริมาณใช้ต่อจาน (qty/plate)    -> qty_per_serving
      col7 ราคา/หน่วย (price per unit)    -> price_per_unit
      col8 ต้นทุน (cost)                  -> ingredient_cost
  ingredient_type is INFERRED via ingest_bom.classify_ingredient (same vocab the
  app's consumption/grouping features expect: protein/carb/veg/sauce/oil/seasoning/other).

SAFETY:
  * Only touches bom_ingredients (replace-per-SKU). Does NOT touch sku_cost,
    sales, or anything else.
  * DRY-RUN by default — parses, validates, reports. Pass --apply to write.
  * --apply makes a consistent backup (sqlite backup API) first.
  * Unmatched menu codes are reported and SKIPPED, never invented.

Usage:
  python import_bom_workbook.py            # dry-run
  python import_bom_workbook.py --apply     # backup + import
"""
from __future__ import annotations

import argparse
import re
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

import openpyxl

import ingest_bom  # reuse classify_ingredient

HERE = Path(__file__).parent.resolve()
DB_PATH = HERE / "oshinei.db"
WB_PATH = HERE.parent / "Data for Oshinei" / "Data Cleasing" / "OSHINEI_BOM_Workbook.xlsx"
SHEET = "การคำนวณต้นทุน"
SOURCE_TAG = "OSHINEI_BOM_Workbook.xlsx"

# Menu-header rows store code+name merged in col0, e.g.
#   'B0106  |  (B) EBI TEMPURA (5 Pcs)  |  (B) กุ้ง...'
# so we extract the leading code token. Codes look like B0106 / A00011 / Sp0002.
MENU_CODE_RE = re.compile(r"^([A-Za-z]{1,3}\d[\w]*)")
SEQ_RE       = re.compile(r"^\d+$")


def _menu_code(cell0: str):
    """Return the leading menu code from a header cell, or None."""
    head = cell0.split("|")[0].strip()
    m = MENU_CODE_RE.match(head)
    return m.group(1) if m else None


def _f(v) -> float:
    if v is None:
        return 0.0
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(str(v).replace(",", "").strip() or 0)
    except ValueError:
        return 0.0


def _s(v) -> str:
    return str(v).strip() if v is not None else ""


def parse_workbook(path: Path) -> dict:
    """-> {menu_code: [ {code,name,unit,qty,ppu,cost}, ... ]}"""
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb[SHEET]
    recipes: dict = defaultdict(list)
    cur = None
    for i, row in enumerate(ws.iter_rows(values_only=True)):
        if i == 0:
            continue
        c0 = _s(row[0])
        if not c0:
            continue
        mcode = _menu_code(c0)
        if mcode:                            # menu header
            cur = mcode
            recipes.setdefault(cur, [])
        elif SEQ_RE.match(c0) and cur:       # ingredient row
            code = _s(row[1])
            name = _s(row[2])
            if not code and not name:
                continue
            recipes[cur].append({
                "code": code,
                "name": name or "(unnamed)",
                "unit": _s(row[3]),
                "qty":  _f(row[4]),
                "ppu":  _f(row[7]),
                "cost": _f(row[8]),
            })
    wb.close()
    return recipes


def safe_backup(db_path: Path) -> Path:
    from datetime import datetime
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    dst = db_path.with_name(f"{db_path.name}.pre_bomimport_{ts}")
    src = sqlite3.connect(str(db_path))
    try:
        src.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        out = sqlite3.connect(str(dst))
        with out:
            src.backup(out)
        out.close()
    finally:
        src.close()
    return dst


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="Write to DB (default: dry-run).")
    args = ap.parse_args()

    if not WB_PATH.exists():
        print(f"Workbook not found: {WB_PATH}", file=sys.stderr)
        return 1

    recipes = parse_workbook(WB_PATH)
    recipes = {k: v for k, v in recipes.items() if v}   # drop empty menus
    print(f"Workbook: {WB_PATH.name}")
    print(f"Parsed   : {len(recipes)} menus, {sum(len(v) for v in recipes.values())} ingredient rows")
    print(f"Mode     : {'APPLY' if args.apply else 'DRY-RUN (no writes)'}\n")

    con = sqlite3.connect(str(DB_PATH))
    con.row_factory = sqlite3.Row
    cur = con.cursor()

    # Resolve menu codes -> sku_id
    code_to_id = {r["sku_code"]: r["sku_id"] for r in cur.execute("SELECT sku_id, sku_code FROM skus")}
    matched = {c: code_to_id[c] for c in recipes if c in code_to_id}
    unmatched = [c for c in recipes if c not in code_to_id]

    # Coverage: how much qty-sold do the matched recipes cover?
    sold = cur.execute("""
        SELECT sk.sku_code AS code, SUM(ssm.qty) AS qty
        FROM sales_sku_monthly ssm JOIN skus sk ON sk.sku_id = ssm.sku_id
        WHERE ssm.source='Easy' GROUP BY sk.sku_id HAVING SUM(ssm.qty) > 0
    """).fetchall()
    tot_qty = sum(r["qty"] or 0 for r in sold)
    new_cov = sum((r["qty"] or 0) for r in sold if r["code"] in matched)

    print(f"Matched menu codes : {len(matched)}/{len(recipes)}")
    _um = ", ".join(unmatched[:8]).encode("ascii", "replace").decode("ascii")
    print(f"Unmatched (skipped): {len(unmatched)}" + (f"  e.g. {_um}" if unmatched else ""))
    print(f"Qty-sold coverage of matched recipes: {100*new_cov/tot_qty:.1f}% of all Easy qty\n")

    # Type classification preview
    type_counts: dict = defaultdict(int)
    zero_cost = 0
    for c, ings in recipes.items():
        if c not in matched:
            continue
        for ing in ings:
            t = ingest_bom.classify_ingredient(ing["code"], ing["name"])
            type_counts[t] += 1
            if ing["cost"] <= 0:
                zero_cost += 1
    print("ingredient_type distribution (matched menus): "
          + ", ".join(f"{k}={v}" for k, v in sorted(type_counts.items(), key=lambda x: -x[1])))
    print(f"ingredient rows with zero/blank cost: {zero_cost}\n")

    if not args.apply:
        con.close()
        print("DRY-RUN complete — nothing written. Re-run with --apply to import.")
        return 0

    bk = safe_backup(DB_PATH)
    print(f"Safe backup -> {bk.name}")

    inserted = 0
    for code, sku_id in matched.items():
        con.execute("DELETE FROM bom_ingredients WHERE sku_id = ?", (sku_id,))
        rows = []
        for idx, ing in enumerate(recipes[code], start=1):
            t = ingest_bom.classify_ingredient(ing["code"], ing["name"])
            rows.append((sku_id, idx, ing["code"], ing["name"], ing["qty"], ing["unit"],
                         ing["ppu"], ing["cost"], t, SOURCE_TAG))
        con.executemany(
            "INSERT OR REPLACE INTO bom_ingredients "
            "(sku_id, seq, ingredient_code, ingredient_name, qty_per_serving, unit, "
            " price_per_unit, ingredient_cost, ingredient_type, source_file) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            rows,
        )
        inserted += len(rows)
    con.commit()

    total_skus = con.execute("SELECT COUNT(DISTINCT sku_id) FROM bom_ingredients").fetchone()[0]
    total_rows = con.execute("SELECT COUNT(*) FROM bom_ingredients").fetchone()[0]
    con.execute("ANALYZE")
    con.commit()
    con.close()
    print(f"\nImported {inserted} ingredient rows across {len(matched)} menus.")
    print(f"bom_ingredients now: {total_rows} rows across {total_skus} SKUs.")
    print("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
