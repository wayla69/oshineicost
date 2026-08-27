# -*- coding: utf-8 -*-
"""
seed_order_units.py — fill ingredient_order_unit (consumption unit → ORDER unit).

The order plan counts consumption in the recipe's base unit (กรัม / มิลลิลิตร /
ชิ้น), but nobody orders 6,516,040 กรัม of salmon — they order whole packs. This
seeds the pack size for each ingredient from the supplier sheet, so the plan can
round UP to whole ordering units.

Source: BoM/GrandMenu_2026-09/ingredient_master.csv (the DATAP sheet), where
    unit       = the base unit the recipe counts in
    full_qty   = base units in ONE purchased pack   (25,000 กรัม = a 25 kg sack)
    full_price = the price of that pack
so full_qty IS the conversion factor, and full_price is the order-unit price.

Ingredients with no row here fall back at read time to กรัม→กก. / มิลลิลิตร→ลิตร
/ countable→itself, and the API flags them pack_known=false so ops can see what
still needs a real pack size.

DRY-RUN by default. --apply writes (and backs up first).

Usage:
  python backend/seed_order_units.py
  python backend/seed_order_units.py --apply
  python backend/seed_order_units.py --apply --overwrite   # also replace manual rows
"""
from __future__ import annotations

import argparse
import csv
import re
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).parent.resolve()
ROOT = HERE.parent
DEFAULT_DB = HERE / "oshinei.db"
DEFAULT_SRC = ROOT / "BoM" / "GrandMenu_2026-09" / "ingredient_master.csv"

DDL = """
CREATE TABLE IF NOT EXISTS ingredient_order_unit (
  ingredient_code TEXT PRIMARY KEY,
  base_unit       TEXT NOT NULL,
  order_unit      TEXT NOT NULL,
  qty_per_order   REAL NOT NULL,
  price_per_order REAL,
  moq             REAL DEFAULT 1,
  round_step      REAL DEFAULT 1,
  note            TEXT,
  source          TEXT,
  updated_at      TEXT DEFAULT CURRENT_TIMESTAMP
)"""

# Pack wording lifted from the product name when it is there — "5KG/PK",
# "20PCS(20PK/BOX)", "1KG/P" — purely for the human-readable label; the maths
# always uses full_qty.
_PACK_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(KG|G|ML|L|PCS|PK|P|BOX|SACK)\b", re.I)


def fnum(v) -> float:
    try:
        return float(str(v).replace(",", "").strip() or 0)
    except ValueError:
        return 0.0


def order_label(name: str, base_unit: str, full_qty: float) -> str:
    """Human label for one purchased pack, e.g. 'ถุง 25 กก.' / 'แพ็ค 400 ชิ้น'."""
    if full_qty <= 1:
        return base_unit or "หน่วย"
    if base_unit == "กรัม":
        return f"แพ็ค {full_qty / 1000:g} กก." if full_qty >= 1000 else f"แพ็ค {full_qty:g} กรัม"
    if base_unit == "มิลลิลิตร":
        return f"แพ็ค {full_qty / 1000:g} ลิตร" if full_qty >= 1000 else f"แพ็ค {full_qty:g} มล."
    return f"แพ็ค {full_qty:g} {base_unit or 'หน่วย'}"


def safe_backup(db: Path) -> Path:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    dst = db.with_name(f"{db.name}.pre_orderunits_{ts}")
    src = sqlite3.connect(str(db))
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
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--src", default=str(DEFAULT_SRC))
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--overwrite", action="store_true",
                    help="also replace rows a human edited (source='manual')")
    a = ap.parse_args()

    db, src = Path(a.db), Path(a.src)
    if not db.exists():
        sys.exit(f"DB not found: {db}")
    if not src.exists():
        sys.exit(f"source not found: {src}\nRun backend/prep_grand_menu_bom.py first.")

    with src.open(encoding="utf-8-sig", newline="") as fh:
        supplier = {r["code"].strip(): r for r in csv.DictReader(fh) if r.get("code")}

    con = sqlite3.connect(str(db))
    con.row_factory = sqlite3.Row
    con.execute(DDL)
    existing = {r["ingredient_code"]: dict(r)
                for r in con.execute("SELECT * FROM ingredient_order_unit")}

    # what the recipes actually consume, and in which unit
    used = con.execute("""
        SELECT ingredient_code AS code, MAX(ingredient_name) AS name, unit,
               COUNT(*) AS n_lines
          FROM bom_ingredients
         WHERE ingredient_code <> '' AND ingredient_code <> '#N/A'
         GROUP BY ingredient_code, unit
    """).fetchall()

    # one pack row per code — skip codes the BoM uses in more than one unit,
    # because a single conversion factor would silently be wrong for one of them
    unit_count: dict = {}
    for u in used:
        unit_count[u["code"]] = unit_count.get(u["code"], 0) + 1

    plan, skipped_mixed, no_supplier, unchanged, kept_manual = [], [], [], [], []
    for u in used:
        code, unit = u["code"], u["unit"] or ""
        if unit_count[code] > 1:
            skipped_mixed.append((code, u["name"], unit))
            continue
        s = supplier.get(code)
        if not s:
            no_supplier.append((code, u["name"], unit, u["n_lines"]))
            continue
        full_qty = fnum(s.get("full_qty"))
        if full_qty <= 0:
            no_supplier.append((code, u["name"], unit, u["n_lines"]))
            continue
        s_unit = (s.get("unit") or "").strip()
        if s_unit and unit and s_unit != unit:
            skipped_mixed.append((code, u["name"], f"BoM={unit} vs ผู้ขาย={s_unit}"))
            continue
        row = dict(code=code, base_unit=unit,
                   order_unit=order_label(s.get("name", ""), unit, full_qty),
                   qty_per_order=full_qty, price_per_order=fnum(s.get("full_price")) or None,
                   note=(s.get("name") or "")[:80])
        ex = existing.get(code)
        if ex and ex.get("source") == "manual" and not a.overwrite:
            kept_manual.append(code)
            continue
        if ex and abs(float(ex["qty_per_order"]) - full_qty) < 1e-9 \
                and ex["order_unit"] == row["order_unit"]:
            unchanged.append(code)
            continue
        plan.append(row)

    print(f"DB      : {db}")
    print(f"Source  : {src.name} ({len(supplier)} supplier items)")
    print(f"Mode    : {'APPLY' if a.apply else 'DRY-RUN (no writes)'}\n")
    print(f"ingredients in recipes      : {len(used)}")
    print(f"  pack size to write        : {len(plan)}")
    print(f"  already correct           : {len(unchanged)}")
    print(f"  manual rows kept          : {len(kept_manual)}"
          + ("  (--overwrite to replace)" if kept_manual else ""))
    print(f"  no supplier pack → fallback: {len(no_supplier)}")
    print(f"  skipped, unit conflict     : {len(skipped_mixed)}")
    if plan:
        print("\nsample:")
        for r in plan[:8]:
            price = f"฿{r['price_per_order']:,.0f}" if r["price_per_order"] else "—"
            print(f"  {r['code']:<12} {r['note'][:26]:<28} 1 {r['order_unit']:<16}"
                  f"= {r['qty_per_order']:>9,.0f} {r['base_unit']:<10} {price}")
    if skipped_mixed:
        print("\nunit conflicts (set these by hand via POST /api/ingredients/order-units):")
        for c, n, why in skipped_mixed[:10]:
            print(f"  {c:<12} {(n or '')[:30]:<32} {why}")
    if no_supplier:
        print("\nno supplier pack — will fall back to กก./ลิตร/หน่วยนับ:")
        for c, n, unit, nl in sorted(no_supplier, key=lambda x: -x[3])[:10]:
            print(f"  {c:<12} {(n or '')[:30]:<32} {unit:<12} ({nl} สูตร)")

    if not a.apply:
        con.close()
        print("\nDRY-RUN complete — nothing written. Re-run with --apply.")
        return 0

    bk = safe_backup(db)
    print(f"\nSafe backup -> {bk.name}")
    con.executemany("""
        INSERT INTO ingredient_order_unit
            (ingredient_code, base_unit, order_unit, qty_per_order, price_per_order,
             moq, round_step, note, source, updated_at)
        VALUES (:code,:base_unit,:order_unit,:qty_per_order,:price_per_order,
                1, 1, :note, 'supplier-sheet', CURRENT_TIMESTAMP)
        ON CONFLICT(ingredient_code) DO UPDATE SET
            base_unit=excluded.base_unit, order_unit=excluded.order_unit,
            qty_per_order=excluded.qty_per_order,
            price_per_order=excluded.price_per_order,
            note=excluded.note, source='supplier-sheet',
            updated_at=CURRENT_TIMESTAMP
    """, plan)
    con.commit()
    n = con.execute("SELECT COUNT(*) FROM ingredient_order_unit").fetchone()[0]
    con.close()
    print(f"wrote {len(plan)} rows · ingredient_order_unit now holds {n}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
