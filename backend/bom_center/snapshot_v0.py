# -*- coding: utf-8 -*-
"""snapshot_v0.py — freeze today's cost engine into the BoM Calculation Center
as Release v0 (plan §5.6 step 1–2), and prove it reproduces the engine.

WHY A v0 AT ALL: every report in the app multiplies sales by sku_effective_cost,
which core._build_sku_effective_cost derives from sku_cost / bom_ingredients /
bom_sku_alias with a fixed precedence. The center replaces that chain with
versioned releases. Before any new engine may feed the reports, there must be
a release that gives EXACTLY the numbers the reports show today — then the
switch moves nothing, and every later release is compared against v0 instead
of against a memory of what the numbers used to be.

WHAT v0 CONTAINS (one bc_recipe row per costed SKU, kind = the rule that won):
  zero    buffet package codes (sku_cost holds their PRICE, never a cost)
  flat    sku_cost.unit_cost non-zero — and the SKU's recipe lines are copied
          too, so the analyst can see the recipe the flat cost was hiding
  recipe  own bom_ingredients lines; line_cost = ingredient_cost (exact)
  alias   bom_sku_alias → another SKU's lines
  source  cost_provenance (confirmed / recipe-file / estimate / …) when the
          loaders tagged it, else the table the number came from

PARITY: bcd_cost_cache(v0) is compared SKU by SKU with the legacy engine
recomputed from the same tables (rules.raw_unit_cost_sql — the very SQL the
legacy builder uses) and, for information, with the stored sku_effective_cost.
The script exits non-zero if any SKU differs by more than ฿0.005.

DRY-RUN BY DEFAULT: everything runs in one transaction and is rolled back;
--apply takes a consistent sqlite backup (<db>.pre_bc_v0_<stamp>) and commits.

Usage (from the repo root or backend/):
    python backend/bom_center/snapshot_v0.py                     # dry-run on backend/oshinei.db
    python backend/bom_center/snapshot_v0.py --db path/to.db     # dry-run elsewhere
    python backend/bom_center/snapshot_v0.py --apply             # backup + write v0
"""
import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
BACKEND = HERE.parent
if __package__:
    from .schema import BASELINE_MONTH, ensure
    from .engine import rebuild_cost_cache
else:                                               # run as a script
    sys.path.insert(0, str(BACKEND))
    from bom_center.schema import BASELINE_MONTH, ensure            # noqa: E402
    from bom_center.engine import rebuild_cost_cache                # noqa: E402
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))
import rules                                                        # noqa: E402

TOLERANCE = 0.005
LABEL = "v0"

_ALIAS_DDL = (
    "CREATE TABLE IF NOT EXISTS bom_sku_alias(bom_sku_code TEXT PRIMARY KEY, "
    "resolved_sku_id INTEGER NOT NULL, match_method TEXT, confidence REAL, notes TEXT)")


def _has_table(con, name: str) -> bool:
    return con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def legacy_unit_costs(con) -> dict:
    """{sku_code: unit_cost} exactly as core._build_sku_effective_cost would
    compute it now (tier codes 0, else rules.raw_unit_cost_sql)."""
    con.execute(_ALIAS_DDL)
    sql = (f"SELECT s.sku_code, CASE WHEN {rules.tier_code_sql('s.sku_code')} THEN 0 "
           f"ELSE {rules.raw_unit_cost_sql('s')} END FROM skus s")
    return {code: float(v or 0.0) for code, v in con.execute(sql)}


def build_v0(con, label: str = LABEL, created_by: str = "snapshot_v0") -> dict:
    """Write (or replace) the baseline release and return the stats + parity
    result. Does NOT commit — the caller decides (dry-run rolls back)."""
    ensure(con)
    con.execute(_ALIAS_DDL)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")

    skus = con.execute("SELECT sku_id, sku_code FROM skus").fetchall()
    code_of = {sid: code for sid, code in skus}
    flat = {sid: float(v) for sid, v in con.execute(
        "SELECT sku_id, unit_cost FROM sku_cost WHERE COALESCE(unit_cost, 0) <> 0")}
    lines = {}
    for r in con.execute(
            "SELECT sku_id, seq, ingredient_code, ingredient_name, qty_per_serving, unit, "
            "price_per_unit, ingredient_cost, ingredient_type, source_file "
            "FROM bom_ingredients ORDER BY sku_id, seq"):
        lines.setdefault(r[0], []).append(r)
    alias = {code: sid for code, sid in con.execute("SELECT bom_sku_code, resolved_sku_id FROM bom_sku_alias")}
    prov = {}
    if _has_table(con, "cost_provenance"):
        prov = dict(con.execute("SELECT sku_id, source FROM cost_provenance"))

    # the release row: replace an existing baseline of this label in full
    row = con.execute("SELECT release_id FROM bc_release WHERE label=?", (label,)).fetchone()
    if row:
        rid = row[0]
        con.execute("DELETE FROM bc_recipe_line WHERE release_id=?", (rid,))
        con.execute("DELETE FROM bc_recipe WHERE release_id=?", (rid,))
        con.execute("DELETE FROM bc_release_check WHERE release_id=?", (rid,))
        con.execute("UPDATE bc_release SET created_by=?, created_at=?, released_by=?, released_at=?, "
                    "notes=? WHERE release_id=?",
                    (created_by, now, created_by, now, "baseline rebuilt from the legacy tables", rid))
    else:
        con.execute(
            "INSERT INTO bc_release(label, kind, effective_month, status, scope, created_by, created_at, "
            "released_by, released_at, notes) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (label, "baseline", BASELINE_MONTH, "released", "all", created_by, now, created_by, now,
             "baseline: today's engine (sku_cost > own recipe > alias recipe > 0; packages 0)"))
        rid = con.execute("SELECT release_id FROM bc_release WHERE label=?", (label,)).fetchone()[0]

    kinds = {"zero": 0, "flat": 0, "recipe": 0, "alias": 0, "none": 0}
    recipe_rows, line_rows = [], []
    for sid, code in skus:
        own = lines.get(sid)
        if rules.is_tier_code(code):
            kind, flat_cost, alias_to, source = "zero", None, None, "tier-package"
        elif sid in flat:
            kind, flat_cost, alias_to = "flat", flat[sid], None
            source = prov.get(sid) or "sku_cost"
        elif own:
            kind, flat_cost, alias_to = "recipe", None, None
            source = prov.get(sid) or "bom_ingredients"
        elif code in alias and lines.get(alias[code]):
            kind, flat_cost, alias_to = "alias", None, code_of.get(alias[code])
            source = "bom_sku_alias"
        else:
            kinds["none"] += 1
            continue
        kinds[kind] += 1
        files = sorted({(ln[9] or "") for ln in (own or [])} - {""})
        recipe_rows.append((rid, code, kind, flat_cost, alias_to, source,
                            ("; ".join(files) if files else None)))
        for ln in own or []:
            _, seq, icode, iname, qty, unit, price, cost, itype, _src = ln
            line_rows.append((rid, code, int(seq), icode, iname, qty, unit, itype,
                              1 if not (cost or 0) else 0, price, cost))
    con.executemany(
        "INSERT INTO bc_recipe(release_id, sku_code, kind, flat_cost, alias_sku_code, source, note) "
        "VALUES(?,?,?,?,?,?,?)", recipe_rows)
    con.executemany(
        "INSERT INTO bc_recipe_line(release_id, sku_code, seq, ingredient_code, ingredient_name, qty, unit, "
        "ingredient_type, zero_by_design, price_per_unit, line_cost) VALUES(?,?,?,?,?,?,?,?,?,?,?)", line_rows)
    cache_rows = rebuild_cost_cache(con, rid)

    # parity — against the engine recomputed now (authoritative) …
    legacy = legacy_unit_costs(con)
    new = dict(con.execute("SELECT sku_code, unit_cost FROM bcd_cost_cache WHERE release_id=?", (rid,)))
    mismatches, max_diff = [], 0.0
    for code, lv in legacy.items():
        nv = float(new.get(code, 0.0))
        d = abs(nv - lv)
        max_diff = max(max_diff, d)
        if d > TOLERANCE:
            mismatches.append({"sku_code": code, "legacy": round(lv, 4), "v0": round(nv, 4)})
    # … and, for information, against the STORED table the reports read today
    stored_diffs = None
    if _has_table(con, "sku_effective_cost"):
        stored = {code: float(v or 0.0) for code, v in con.execute(
            "SELECT s.sku_code, e.recipe_cost FROM sku_effective_cost e JOIN skus s USING(sku_id)")}
        stored_diffs = sum(1 for c, v in stored.items() if abs(float(new.get(c, 0.0)) - v) > TOLERANCE)

    return {"release_id": rid, "label": label, "skus": len(skus), "kinds": kinds,
            "recipes": len(recipe_rows), "lines": len(line_rows), "cache_rows": cache_rows,
            "mismatches": mismatches, "max_abs_diff": round(max_diff, 6),
            "stored_table_diffs": stored_diffs}


def safe_backup(db_path: Path) -> Path:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    dst = db_path.with_name(f"{db_path.name}.pre_bc_v0_{ts}")
    src = sqlite3.connect(str(db_path))
    try:
        out = sqlite3.connect(str(dst))
        try:
            src.backup(out)
        finally:
            out.close()
    finally:
        src.close()
    return dst


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--db", default=str(BACKEND / "oshinei.db"))
    ap.add_argument("--apply", action="store_true", help="back up, then commit the snapshot")
    ap.add_argument("--label", default=LABEL)
    ap.add_argument("--json", action="store_true", help="print the stats as JSON")
    a = ap.parse_args()
    db = Path(a.db)
    if not db.exists():
        print(f"no such database: {db}")
        return 2
    if a.apply:
        print(f"backup -> {safe_backup(db).name}")
    con = sqlite3.connect(str(db))
    try:
        stats = build_v0(con, label=a.label)
        ok = not stats["mismatches"]
        if a.apply and ok:
            con.commit()
            mode = "APPLIED"
        else:
            con.rollback()
            mode = "dry-run (rolled back)" if ok else "REFUSED (parity failed) — nothing written"
    finally:
        con.close()
    if a.json:
        print(json.dumps({"mode": mode, **stats}, ensure_ascii=False, indent=1))
    else:
        k = stats["kinds"]
        print(f"[{mode}] release {stats['label']} id={stats['release_id']}: {stats['skus']} skus -> "
              f"zero {k['zero']} / flat {k['flat']} / recipe {k['recipe']} / alias {k['alias']} / "
              f"uncosted {k['none']}; {stats['lines']} lines; cache {stats['cache_rows']} rows")
        print(f"parity vs legacy engine: max |diff| = {stats['max_abs_diff']} "
              f"({len(stats['mismatches'])} SKUs over {TOLERANCE})"
              + (f"; vs stored sku_effective_cost: {stats['stored_table_diffs']} differ"
                 if stats["stored_table_diffs"] is not None else ""))
        for m in stats["mismatches"][:20]:
            print("  ", m)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
