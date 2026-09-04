# -*- coding: utf-8 -*-
"""
load_grand_menu_bom.py — load the prepared Grand Menu CSVs into the database.

Reads what backend/prep_grand_menu_bom.py produced in BoM/GrandMenu_2026-09/:
    menu_master.csv   -> skus (+ optionally sku_cost)
    bom_lines.csv     -> bom_ingredients   (replace-per-SKU)
then rebuilds sku_effective_cost.

DRY-RUN BY DEFAULT. Nothing is written without --apply, and --apply always takes a
consistent sqlite backup first.

WHY pre-create the skus rows: sync_mssql.py (~line 344) lazily inserts a STUB sku
with description = sku_code for any POS item it has never seen. If the first
September sync runs before this loader, all 382 new menus land as raw codes with no
Thai name, no category and no business_type — the same mess the หาดใหญ่ THBF*/THAL*
series caused. Running this first means the sync finds them already correct.

COST PRECEDENCE (mirrors _build_sku_effective_cost in backend/main.py ~10490):
    sku_cost.unit_cost (non-zero)  >  direct BoM sum  >  alias BoM sum  >  0
so a non-zero sku_cost row HIDES the recipe. That is why --sku-cost defaults to
"gaps": seed sku_cost only where the recipe cannot carry the cost itself — no
ingredient lines, lines that sum to zero, or an ops cost_override in
menu_decisions.csv (e.g. ยำไทยปลาซาบะ ฿16, whose sheet formula returns 0).

SAFETY
  * Touches only: skus, sku_cost, bom_ingredients, sku_effective_cost.
  * Never deletes an existing sku, never touches sales or the old menu's BoM
    (pre-September history keeps using the old codes).
  * An existing sku's description is rewritten only when it is a stub
    (description == sku_code) or --overwrite-desc is passed.

Usage:
  python backend/load_grand_menu_bom.py                     # dry-run plan
  python backend/load_grand_menu_bom.py --apply             # backup + load
  python backend/load_grand_menu_bom.py --apply --sku-cost none
  python backend/load_grand_menu_bom.py --db /path/other.db --dir <csv dir>
"""
from __future__ import annotations

import argparse
import csv
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

from rules import raw_unit_cost_sql   # the same cost chain the app and the sync use

HERE = Path(__file__).parent.resolve()
ROOT = HERE.parent
DEFAULT_DB = HERE / "oshinei.db"
DEFAULT_DIR = ROOT / "BoM" / "GrandMenu_2026-09"
SOURCE_TAG = "GrandMenu_2026-09"

# Code segment (chars 5:8 of OTHBFxxxNNNNN) -> categories.name.
# Learned from the existing TH*/OTH* code space; anything unlisted falls back to
# the majority category of existing SKUs with the same segment, then 'Oshinei other'.
SEG_CATEGORY = {
    "APP": "Oshinei APPETIZER",
    "CRE": "Oshinei ICE-CREAM",      # soft cream; (B) variant remapped below
    "DON": "Oshinei DON",
    "FRI": "Oshinei APPETIZER",
    "GNT": "Oshinei GRILL",
    "HAR": "Oshinei SUSHI",          # hand roll
    "KSY": "Oshinei GRILL",          # kushi yaki
    "NOD": "Oshinei SOBA",
    "RIC": "Oshinei APPETIZER",
    "ROL": "Oshinei ROLL",
    "SAL": "Oshinei SALAD",
    "SHB": "Oshinei SHABU",
    "SKS": "Oshinei GRILL",          # salmon kabuto
    "SOU": "Oshinei SIDE DISH",
    "SSM": "Oshinei SASHIMI",
    "SUS": "Oshinei SUSHI",
    "TEM": "Oshinei AGE MONOMIRI",   # tempura
    "YAM": "Oshinei YUM",
}


def read_csv(path: Path) -> list:
    if not path.exists():
        sys.exit("missing input: %s\nRun backend/prep_grand_menu_bom.py first." % path)
    with path.open(encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def fnum(v) -> float:
    try:
        return float(str(v).strip() or 0)
    except ValueError:
        return 0.0


def safe_backup(db_path: Path) -> Path:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    dst = db_path.with_name("%s.pre_grandmenu_%s" % (db_path.name, ts))
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


def resolve_categories(con, menus: list) -> dict:
    """sku_code -> category_id. Named map first, then majority of existing peers."""
    cats = {name: cid for cid, name in con.execute("SELECT category_id, name FROM categories")}
    other = cats.get("Oshinei other")

    learned = defaultdict(Counter)
    for code, cid in con.execute(
        "SELECT sku_code, category_id FROM skus "
        "WHERE category_id IS NOT NULL AND length(sku_code) >= 12"
    ):
        if code.startswith("TH"):
            learned[code[4:7]][cid] += 1
        elif code.startswith("OTH"):
            learned[code[5:8]][cid] += 1

    out = {}
    for m in menus:
        code, seg = m["sku_code"], m["sku_code"][5:8]
        name = SEG_CATEGORY.get(seg)
        if seg == "CRE" and m["variant"] == "B" and "Oshinei ICE-CREAM(B)" in cats:
            name = "Oshinei ICE-CREAM(B)"
        cid = cats.get(name) if name else None
        if cid is None and learned.get(seg):
            cid = learned[seg].most_common(1)[0][0]
        out[code] = cid if cid is not None else other
    return out


def rebuild_effective_cost(con) -> int:
    """Same COALESCE chain as _build_sku_effective_cost (backend/main.py ~10490).

    Duplicated on purpose: importing main.py would spin up the whole FastAPI app.
    Compute-then-replace so a failure never leaves the table all-zero.
    """
    rows = con.execute(
        f"""
        SELECT s.sku_id, {raw_unit_cost_sql('s')} AS rc
        FROM skus s
        """
    ).fetchall()
    if not rows:
        return 0
    con.execute("DELETE FROM sku_effective_cost")
    con.executemany("INSERT INTO sku_effective_cost(sku_id, recipe_cost) VALUES (?, ?)",
                    [(r[0], r[1]) for r in rows])
    return len(rows)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=str(DEFAULT_DIR), help="folder with the prepared CSVs")
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--apply", action="store_true", help="write to the DB (default: dry-run)")
    ap.add_argument("--sku-cost", choices=("gaps", "all", "none"), default="gaps",
                    help="gaps (default) = seed sku_cost only where the recipe cannot carry the cost (no lines, zero sum, or a cost_override); "
                         "all = seed every menu (HIDES the BoM); none = never touch sku_cost")
    ap.add_argument("--overwrite-desc", action="store_true",
                    help="rewrite description even when the existing sku is not a stub")
    a = ap.parse_args()

    src = Path(a.dir)
    db_path = Path(a.db)
    if not db_path.exists():
        sys.exit("DB not found: %s" % db_path)

    menus = read_csv(src / "menu_master.csv")
    lines = read_csv(src / "bom_lines.csv")
    by_sku = defaultdict(list)
    for ln in lines:
        by_sku[ln["sku_code"]].append(ln)

    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    existing = {r["sku_code"]: r for r in con.execute(
        "SELECT sku_id, sku_code, description, category_id, business_type FROM skus")}
    cat_of = resolve_categories(con, menus)

    to_insert, to_update, unchanged = [], [], []
    for m in menus:
        code = m["sku_code"]
        row = existing.get(code)
        if row is None:
            to_insert.append(m)
        else:
            is_stub = (row["description"] or "").strip() in ("", code)
            if is_stub or a.overwrite_desc or row["business_type"] is None \
               or row["category_id"] is None:
                to_update.append((m, row, "stub" if is_stub else "fill-blanks"))
            else:
                unchanged.append(code)

    # "gaps" = the recipe cannot carry the cost on its own: no lines at all, lines that
    # sum to zero (the workbook's formula failed), or an ops cost_override to honour.
    cost_rows = []
    for m in menus:
        override = fnum(m.get("cost_override"))
        c = override or fnum(m["cost_per_serve"])
        recipe_sum = sum(fnum(x["ingredient_cost"]) for x in by_sku.get(m["sku_code"], []))
        if c <= 0:
            continue
        needs_seed = recipe_sum <= 0 or override > 0
        if a.sku_cost == "all" or (a.sku_cost == "gaps" and needs_seed):
            cost_rows.append((m["sku_code"], round(c, 4), bool(override)))

    no_recipe = [m["sku_code"] for m in menus if not by_sku.get(m["sku_code"])]
    tie_off = []
    for m in menus:
        got = sum(fnum(x["ingredient_cost"]) for x in by_sku.get(m["sku_code"], []))
        want = fnum(m["cost_per_serve"])
        if by_sku.get(m["sku_code"]) and abs(got - want) > 0.01:
            tie_off.append((m["sku_code"], got, want))

    print("DB        : %s" % db_path)
    print("CSV dir   : %s" % src)
    print("Mode      : %s" % ("APPLY" if a.apply else "DRY-RUN (no writes)"))
    print("sku_cost  : %s\n" % a.sku_cost)
    print("skus            : %d new, %d updated, %d left alone"
          % (len(to_insert), len(to_update), len(unchanged)))
    print("bom_ingredients : %d lines across %d menus (replace-per-SKU)"
          % (len(lines), len(by_sku)))
    print("sku_cost        : %d rows would be written" % len(cost_rows))
    if no_recipe:
        print("menus with NO recipe lines: %d  %s"
              % (len(no_recipe), ", ".join(no_recipe[:6])))
    dup_seq = [k for k, v in Counter((r["sku_code"], r["seq"]) for r in lines).items() if v > 1]
    if dup_seq:
        print("duplicate ลำดับ in source (seq renumbered 1..N): %d menus  %s"
              % (len(dup_seq), ", ".join(c for c, _ in dup_seq[:6])))
    if tie_off:
        print("recipe sum != stated cost/serve: %d menus (loaded as-is, BoM wins)"
              % len(tie_off))
        for code, got, want in tie_off[:5]:
            print("    %-16s lines %.2f vs stated %.2f" % (code, got, want))

    cat_tally = Counter(cat_of[m["sku_code"]] for m in menus)
    names = {cid: n for cid, n in con.execute("SELECT category_id, name FROM categories")}
    print("\ncategory mapping: " + ", ".join(
        "%s=%d" % (names.get(cid, "?"), n) for cid, n in cat_tally.most_common()))
    if to_insert:
        print("\nsample new skus:")
        for m in to_insert[:5]:
            print("    %-16s %-9s %s" % (m["sku_code"],
                                         "buffet" if m["variant"] == "B" else "alacarte",
                                         m["description"][:48]))

    if not a.apply:
        con.close()
        print("\nDRY-RUN complete — nothing written. Re-run with --apply to load.")
        return 0

    con.close()
    bk = safe_backup(db_path)
    print("\nSafe backup -> %s" % bk.name)

    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    try:
        con.execute("BEGIN")
        max_sid = con.execute("SELECT COALESCE(MAX(sku_id), 0) FROM skus").fetchone()[0]
        code2sid = {r["sku_code"]: r["sku_id"] for r in
                    con.execute("SELECT sku_id, sku_code FROM skus")}

        for m in to_insert:
            max_sid += 1
            con.execute(
                "INSERT INTO skus(sku_id, sku_code, description, category_id, unit, "
                "active, business_type) VALUES (?,?,?,?,NULL,1,?)",
                (max_sid, m["sku_code"], m["description"], cat_of[m["sku_code"]],
                 "buffet" if m["variant"] == "B" else "alacarte"))
            code2sid[m["sku_code"]] = max_sid

        for m, row, _why in to_update:
            con.execute(
                "UPDATE skus SET description = ?, category_id = COALESCE(category_id, ?), "
                "business_type = COALESCE(business_type, ?), active = 1 WHERE sku_id = ?",
                (m["description"], cat_of[m["sku_code"]],
                 "buffet" if m["variant"] == "B" else "alacarte", row["sku_id"]))

        n_lines = 0
        for code, rows in by_sku.items():
            sid = code2sid.get(code)
            if sid is None:
                continue
            con.execute("DELETE FROM bom_ingredients WHERE sku_id = ?", (sid,))
            # Renumber seq 1..N: bom_ingredients is keyed on (sku_id, seq) and four
            # menus repeat a ลำดับ number in the source sheet — trusting it would
            # silently drop those lines through INSERT OR REPLACE.
            payload = [(sid, i, r["ingredient_code"], r["ingredient_name"],
                        fnum(r["qty_per_serving"]), r["unit"], fnum(r["price_per_unit"]),
                        fnum(r["ingredient_cost"]), r["ingredient_type"], SOURCE_TAG)
                       for i, r in enumerate(
                           sorted(rows, key=lambda x: int(float(x["seq"]))), start=1)]
            con.executemany(
                "INSERT OR REPLACE INTO bom_ingredients "
                "(sku_id, seq, ingredient_code, ingredient_name, qty_per_serving, unit, "
                " price_per_unit, ingredient_cost, ingredient_type, source_file) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)", payload)
            n_lines += len(payload)

        n_cost = 0
        today = datetime.now().strftime("%Y-%m-%d")
        for code, cost, _is_override in cost_rows:
            sid = code2sid.get(code)
            if sid is None:
                continue
            con.execute(
                "INSERT INTO sku_cost(sku_id, unit_cost, effective_from) VALUES (?,?,?) "
                "ON CONFLICT(sku_id) DO UPDATE SET unit_cost = excluded.unit_cost, "
                "effective_from = excluded.effective_from, "
                "updated_at = CURRENT_TIMESTAMP",
                (sid, cost, today))
            n_cost += 1

        n_eff = rebuild_effective_cost(con)
        con.commit()
    except Exception:
        con.rollback()
        con.close()
        print("FAILED — rolled back. Restore from %s if needed." % bk.name, file=sys.stderr)
        raise

    print("skus            : +%d inserted, %d updated" % (len(to_insert), len(to_update)))
    print("bom_ingredients : %d lines across %d menus" % (n_lines, len(by_sku)))
    print("sku_cost        : %d rows" % n_cost)
    print("sku_effective_cost rebuilt: %d SKUs" % n_eff)

    # verification: effective cost of the loaded menus must equal the stated cost/serve
    codes = tuple(m["sku_code"] for m in menus)
    q = ("SELECT s.sku_code, e.recipe_cost FROM sku_effective_cost e "
         "JOIN skus s USING(sku_id) WHERE s.sku_code IN (%s)"
         % ",".join("?" * len(codes)))
    eff = {r["sku_code"]: r["recipe_cost"] for r in con.execute(q, codes)}
    bad = [(m["sku_code"], eff.get(m["sku_code"], 0), fnum(m["cost_per_serve"]))
           for m in menus
           if abs(eff.get(m["sku_code"], 0) - fnum(m["cost_per_serve"])) > 0.01]
    zero = [c for c in codes if not eff.get(c)]
    print("\nverify: %d/%d menus have effective cost == stated cost/serve"
          % (len(menus) - len(bad), len(menus)))
    for code, got, want in bad[:8]:
        print("    %-16s effective %.2f vs stated %.2f" % (code, got, want))
    if zero:
        print("    zero-cost menus: %d  %s" % (len(zero), ", ".join(zero[:6])))
    con.close()
    print("\nDone. Old-menu SKUs and their BoM were NOT touched — pre-September "
          "history still costs on the old codes.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
