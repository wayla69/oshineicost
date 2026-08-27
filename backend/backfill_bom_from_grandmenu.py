# -*- coding: utf-8 -*-
"""
backfill_bom_from_grandmenu.py — give old-menu SKUs an ingredient breakdown by
borrowing the recipe of the SAME DISH in the new Grand Menu.

The problem: 57 SKUs covering ~1.7M plates (หอยเชลล์ต้ม ฮอกไกโด alone is 341k
plates / 28 days) are costed by a FLAT sku_cost with no bom_ingredients rows.
Their COGS is right, but the ingredient order plan has nothing to aggregate, so
they silently drop out of it — that is the bulk of the ~25% coverage gap.

The fix: the new Grand Menu (loaded 2026-08-27) carries a real recipe for most
of the same dishes. Match old SKU → new SKU by dish name, copy the ingredient
lines, and SCALE them so the recipe totals exactly the SKU's existing unit cost.

Why scale: _build_sku_effective_cost prefers a non-zero sku_cost over the BoM
sum, so COGS does not move either way — but if the BoM said something different
from sku_cost, the order plan would order against one number while the P&L costs
against another. Scaling keeps the two tied.

Matching is name-based, so it is DRY-RUN by default and prints every pair for
review. --apply writes bom_ingredients only (never sku_cost, never sales) and
backs up first.

Usage:
  python backend/backfill_bom_from_grandmenu.py                 # review matches
  python backend/backfill_bom_from_grandmenu.py --min-qty 500   # only what sells
  python backend/backfill_bom_from_grandmenu.py --apply
"""
from __future__ import annotations

import argparse
import re
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).parent.resolve()
DEFAULT_DB = HERE / "oshinei.db"
SOURCE_TAG = "backfill:GrandMenu_2026-09"

# "(B) หอยเชลล์ต้ม ฮอกไกโด (1 ชิ้น)" → "หอยเชลล์ต้มฮอกไกโด"
_PREFIX_RE = re.compile(r"^\((?:A|B|BF|AL|EX)\)\s*", re.I)
_PAREN_RE = re.compile(r"\([^)]*\)")
_NOISE_RE = re.compile(r"(buffet|ต่อ\s*1\s*เสิร์ฟ|\d+\s*(ชิ้น|ตัว|ลูก|ถ้วย|จาน|ฟอง|แท่ง|กระทะ|เซต|ไม้|แผ่น))",
                       re.I)


def norm_dish(name: str) -> str:
    s = _PREFIX_RE.sub("", (name or "").strip())
    s = _PAREN_RE.sub(" ", s)
    s = _NOISE_RE.sub(" ", s)
    s = re.sub(r"[\s​·,\-–_/]+", "", s)
    return s.lower()


def safe_backup(db: Path) -> Path:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    dst = db.with_name(f"{db.name}.pre_bomfill_{ts}")
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
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--min-qty", type=float, default=0,
                    help="only SKUs with at least this much qty sold")
    ap.add_argument("--all-uncosted", action="store_true",
                    help="also fill SKUs that have NO sku_cost (cost then comes from the "
                         "copied recipe — changes COGS, so off by default)")
    ap.add_argument("--min-scale", type=float, default=0.6)
    ap.add_argument("--max-scale", type=float, default=1.6,
                    help="a donor whose recipe cost is far from the SKU's own cost is "
                         "probably a DIFFERENT PORTION, not the same dish scaled — those "
                         "go to a review list instead of being written")
    ap.add_argument("--force-scale", action="store_true",
                    help="write the out-of-band matches too (review them first)")
    ap.add_argument("--single", action="store_true", default=True,
                    help="second pass: 1-line recipe for dishes that ARE one ingredient")
    a = ap.parse_args()

    db = Path(a.db)
    if not db.exists():
        sys.exit(f"DB not found: {db}")
    con = sqlite3.connect(str(db))
    con.row_factory = sqlite3.Row

    # donors: every SKU that HAS a recipe, keyed by normalised dish name.
    # Grand Menu recipes win over old ones — they are the current truth.
    donors: dict = {}
    for r in con.execute("""
        SELECT s.sku_id, s.sku_code, s.description,
               (SELECT COUNT(*) FROM bom_ingredients bi WHERE bi.sku_id = s.sku_id) AS n,
               (SELECT SUM(ingredient_cost) FROM bom_ingredients bi WHERE bi.sku_id = s.sku_id) AS total,
               (SELECT MAX(source_file) FROM bom_ingredients bi WHERE bi.sku_id = s.sku_id) AS src
          FROM skus s
         WHERE (SELECT COUNT(*) FROM bom_ingredients bi WHERE bi.sku_id = s.sku_id) > 0
    """):
        if not r["total"] or r["total"] <= 0:
            continue
        k = norm_dish(r["description"])
        if not k:
            continue
        cur = donors.get(k)
        is_new = (r["src"] or "").startswith("GrandMenu")
        if cur is None or (is_new and not cur["is_new"]):
            donors[k] = {"sku_id": r["sku_id"], "code": r["sku_code"],
                         "desc": r["description"], "n": r["n"],
                         "total": float(r["total"]), "is_new": is_new}

    # targets: sold, no recipe of their own
    targets = con.execute("""
        SELECT s.sku_id, s.sku_code, s.description,
               (SELECT unit_cost FROM sku_cost sc WHERE sc.sku_id = s.sku_id) AS unit_cost,
               COALESCE((SELECT SUM(qty) FROM sales_sku_daily d WHERE d.sku_id = s.sku_id), 0) AS qty
          FROM skus s
         WHERE (SELECT COUNT(*) FROM bom_ingredients bi WHERE bi.sku_id = s.sku_id) = 0
           AND COALESCE((SELECT SUM(qty) FROM sales_sku_daily d WHERE d.sku_id = s.sku_id), 0) > 0
         ORDER BY qty DESC
    """).fetchall()

    plan, unmatched, skipped, offband, singles, suspect = [], [], [], [], [], []
    for t in targets:
        if t["qty"] < a.min_qty:
            continue
        cost = float(t["unit_cost"] or 0)
        if cost <= 0 and not a.all_uncosted:
            skipped.append((t, "ไม่มี sku_cost (ใช้ --all-uncosted ถ้าต้องการ)"))
            continue
        d = donors.get(norm_dish(t["description"]))
        if not d:
            unmatched.append(t)
            continue
        if d["sku_id"] == t["sku_id"]:
            continue
        scale = (cost / d["total"]) if cost > 0 else 1.0
        rec = {"t": t, "d": d, "scale": scale, "cost": cost or d["total"], "kind": "dish"}
        if a.min_scale <= scale <= a.max_scale or a.force_scale:
            plan.append(rec)
        else:
            offband.append(rec)

    # ── pass 2: dishes that ARE a single ingredient ────────────────────────
    # "สาหร่ายแผ่นโนริ(8แผ่น)" is just nori; "แมงกะพรุนหัวกระสุน ดองน้ำมันงา" is
    # mostly jellyfish. Match the dish name against ingredient names already in
    # the BoM and derive the quantity from the SKU's own cost, so the line can
    # only ever be wrong about WHICH ingredient — never about the cost.
    if a.single and unmatched:
        ing = {}
        for r in con.execute("""SELECT ingredient_code, ingredient_name, unit,
                                       AVG(price_per_unit) AS ppu, COUNT(*) AS n
                                  FROM bom_ingredients
                                 WHERE ingredient_code <> '' AND ingredient_code <> '#N/A'
                                   AND price_per_unit > 0
                                 GROUP BY ingredient_code, ingredient_name, unit"""):
            key = re.sub(r"\s+", "", (r["ingredient_name"] or "")).lower()
            if len(key) >= 4 and (key not in ing or r["n"] > ing[key]["n"]):
                ing[key] = dict(code=r["ingredient_code"], name=r["ingredient_name"],
                                unit=r["unit"], ppu=float(r["ppu"]), n=r["n"])
        still = []
        for t in unmatched:
            cost = float(t["unit_cost"] or 0)
            dish = norm_dish(t["description"])
            if cost <= 0 or not dish:
                still.append(t)
                continue
            # longest ingredient name contained in the dish name wins
            hit = max((v for k, v in ing.items() if k in dish), key=lambda v: len(v["name"]),
                      default=None)
            if not hit:
                still.append(t)
                continue
            qty = cost / hit["ppu"]
            rec = {"t": t, "ing": hit, "cost": cost, "kind": "single", "qty": qty}
            # The quantity is derived from sku_cost, so an under-costed SKU yields
            # an absurd portion: P00011 "สาหร่ายแผ่นโนริ(8แผ่น)" comes out at 0.1
            # แผ่น. Writing that would quietly under-order. Flag instead — a portion
            # this small is evidence the COST is wrong, which is worth surfacing.
            m = re.search(r"(\d+)\s*(ชิ้น|แผ่น|ตัว|ลูก|ฟอง)", t["description"] or "")
            implausible = (
                (hit["unit"] in ("กรัม", "มิลลิลิตร") and qty < 2)
                or (hit["unit"] not in ("กรัม", "มิลลิลิตร") and qty < 0.5)
                or (m and hit["unit"] == m.group(2) and qty < float(m.group(1)) * 0.5))
            (suspect if implausible else singles).append(rec)
        unmatched = still

    print(f"DB      : {db}")
    print(f"Mode    : {'APPLY' if a.apply else 'DRY-RUN (no writes)'}")
    print(f"Donors  : {len(donors)} dishes with a recipe\n")
    print(f"SKUs sold without a recipe : {len(targets)}")
    print(f"  matched to a full recipe : {len(plan)}")
    print(f"  single-ingredient dish   : {len(singles)}")
    print(f"  needs review (สเกลผิดปกติ): {len(offband)}")
    print(f"  ต้นทุนเดิมน่าจะผิด (ไม่เขียน): {len(suspect)}")
    print(f"  no match                 : {len(unmatched)}")
    print(f"  skipped (no sku_cost)    : {len(skipped)}")

    if singles:
        print("\nสูตรบรรทัดเดียว (ปริมาณคำนวณจากต้นทุนเดิม จึงตรงกับ COGS เสมอ):")
        for p in sorted(singles, key=lambda x: -float(x["t"]["qty"]))[:15]:
            t2, g = p["t"], p["ing"]
            print(f"  {t2['sku_code']:<14} {(t2['description'] or '')[:34]:<36} qty {t2['qty']:>9,.0f}"
                  f"  ←  {g['code']} {g['name'][:18]:<20} {p['qty']:>8.1f} {g['unit']}")
    if suspect:
        print("\nไม่เขียนให้ — ปริมาณที่คำนวณได้น้อยผิดปกติ แปลว่า sku_cost เดิมน่าจะต่ำเกินจริง:")
        for p2 in sorted(suspect, key=lambda x: -float(x["t"]["qty"]))[:10]:
            t2, g = p2["t"], p2["ing"]
            print(f"  {t2['sku_code']:<14} {(t2['description'] or '')[:36]:<38}"
                  f" ต้นทุน ฿{p2['cost']:>7.2f} → {p2['qty']:.2f} {g['unit']} ของ {g['name'][:16]}")
    if offband:
        print("\nต้องตรวจก่อน — ต้นทุนต่างจากสูตรต้นแบบมาก (น่าจะคนละขนาดเสิร์ฟ):")
        for p in sorted(offband, key=lambda x: -float(x["t"]["qty"]))[:10]:
            print(f"  {p['t']['sku_code']:<14} {(p['t']['description'] or '')[:34]:<36}"
                  f"  ←  {p['d']['code']:<14} ×{p['scale']:.2f}")
    if plan:
        qty_fixed = sum(float(p["t"]["qty"]) for p in plan + singles)
        qty_all = sum(float(t["qty"]) for t in targets)
        print(f"\nqty covered by the matches : {qty_fixed:,.0f} of {qty_all:,.0f} "
              f"({100 * qty_fixed / qty_all:.1f}% of the uncovered volume)")
        print("\nmatches (old SKU  ←  donor recipe):")
        for p in sorted(plan, key=lambda x: -float(x["t"]["qty"]))[:25]:
            t, d = p["t"], p["d"]
            warn = "  ⚠ สเกล" if not (0.4 <= p["scale"] <= 2.5) else ""
            print(f"  {t['sku_code']:<14} {(t['description'] or '')[:34]:<36} qty {t['qty']:>9,.0f}"
                  f"  ←  {d['code']:<14} {d['n']} บรรทัด  ×{p['scale']:.2f}{warn}")
    if unmatched:
        print("\nno matching dish (ยังต้องทำสูตรเอง) — top by volume:")
        for t in unmatched[:15]:
            print(f"  {t['sku_code']:<14} {(t['description'] or '')[:44]:<46} qty {t['qty']:>9,.0f}")

    if not a.apply:
        con.close()
        print("\nDRY-RUN complete — nothing written. ตรวจรายการคู่ที่จับได้ก่อน แล้วรัน --apply")
        return 0

    bk = safe_backup(db)
    print(f"\nSafe backup -> {bk.name}")
    n_lines = 0
    for p in singles:
        t2, g = p["t"], p["ing"]
        con.execute("DELETE FROM bom_ingredients WHERE sku_id=?", (t2["sku_id"],))
        con.execute("""INSERT INTO bom_ingredients
              (sku_id, seq, ingredient_code, ingredient_name, qty_per_serving, unit,
               price_per_unit, ingredient_cost, ingredient_type, source_file)
              VALUES (?,1,?,?,?,?,?,?,?,?)""",
                    (t2["sku_id"], g["code"], g["name"], round(p["qty"], 4), g["unit"],
                     g["ppu"], round(p["cost"], 4),
                     con.execute("SELECT ingredient_type FROM bom_ingredients WHERE "
                                 "ingredient_code=? LIMIT 1", (g["code"],)).fetchone()[0],
                     f"{SOURCE_TAG}<single>"))
        n_lines += 1
    for p in plan:
        t, d, scale = p["t"], p["d"], p["scale"]
        src = con.execute("""SELECT seq, ingredient_code, ingredient_name, qty_per_serving,
                                    unit, price_per_unit, ingredient_cost, ingredient_type
                               FROM bom_ingredients WHERE sku_id=? ORDER BY seq""",
                          (d["sku_id"],)).fetchall()
        con.execute("DELETE FROM bom_ingredients WHERE sku_id=?", (t["sku_id"],))
        rows = []
        for i, r in enumerate(src, start=1):
            rows.append((t["sku_id"], i, r["ingredient_code"], r["ingredient_name"],
                         round(float(r["qty_per_serving"] or 0) * scale, 6), r["unit"],
                         r["price_per_unit"],
                         round(float(r["ingredient_cost"] or 0) * scale, 6),
                         r["ingredient_type"], f"{SOURCE_TAG}<{d['code']}>"))
        con.executemany("""INSERT OR REPLACE INTO bom_ingredients
              (sku_id, seq, ingredient_code, ingredient_name, qty_per_serving, unit,
               price_per_unit, ingredient_cost, ingredient_type, source_file)
              VALUES (?,?,?,?,?,?,?,?,?,?)""", rows)
        n_lines += len(rows)
    con.commit()
    total_skus = con.execute("SELECT COUNT(DISTINCT sku_id) FROM bom_ingredients").fetchone()[0]
    con.close()
    print(f"filled {len(plan) + len(singles)} SKUs with {n_lines} ingredient lines")
    print(f"bom_ingredients now covers {total_skus} SKUs")
    print("COGS is unchanged — sku_cost still wins in _build_sku_effective_cost; "
          "the recipes were scaled to match it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
