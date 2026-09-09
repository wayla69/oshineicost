#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Close the Grand Menu (1 Sep 2026) cost gap — the POS items the recipe
workbook never covered.

The workbook (Final BOM Grand Menu.xlsx) costed 386 dish codes; the POS sells
~600 OTH* codes. The rest are sync-made stubs with no category and no cost:
drinks, condiments, buffet penalty fees, delivery / takeaway promo sets, and
"special" dishes (SPC) the kitchen sells outside the workbook. Every September
bill line on them books ฿0 food cost.

What this does, in `--apply` order (everything tagged, everything undoable):
  1. CATEGORY  drinks → SOFT DRINK / ALCOHOL, condiments → CONDIMENT, fees →
     SERVICE. Those categories are non-food for every COGS and coverage read
     (core._NONFOOD_CATEGORY_KEYWORDS), exactly how the old menu treated them.
  2. SAME DISH  a stub whose cleaned name equals a costed old-menu dish (or a
     hand-confirmed near name, ALIASES below) gets that dish's effective cost
     as a sku_cost row — the cost-management table the engine reads first.
     Sets of two dishes sum their parts.
  3. BY WEIGHT  delivery / takeaway salmon sets get salmon weight × the
     ingredient master's net price per gram (40 % trim loss) — ESTIMATE.
  4. MULTIPLE   "12 คำ" sushi boxes = 12 × the matching per-piece dish — ESTIMATE.
  5. rebuild sku_effective_cost, print coverage before / after.
Everything else goes to the worklist CSV for the kitchen: those need a recipe
in the workbook (then prep_grand_menu_bom.py + ingest_bom.py as before).

    python backend/close_grand_menu_gap.py                      # report only
    python backend/close_grand_menu_gap.py --apply              # write + rebuild + apply-log
    python backend/close_grand_menu_gap.py --undo <apply-log>   # put everything back
"""
from __future__ import annotations

import argparse
import csv
import datetime
import json
import re
import sqlite3
import sys
from pathlib import Path

HERE = Path(__file__).parent.resolve()
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

TAG = "grand_gap_2026-09"
EFFECTIVE_FROM = "2026-09-01"
OUT_DIR = ROOT / "BoM" / "GrandMenu_2026-09"
SALMON_CODE, SALMON_NET_PRICE = "02-01-001", round(372.5 / 1000 / (1 - 0.40), 4)   # ฿/g after 40 % loss (ingredient master)

CAT = {"soft_drink": "Oshinei SOFT DRINK", "alcohol": "Oshinei ALCOHOL", "condiment": "Oshinei CONDIMENT", "service": "Oshinei SERVICE"}
ALCOHOL_WORDS = ("เบียร์", "สาเก", "รีเจนซี่", "ทันเรย์", "เหล้า", "ไวน์", "โซจู", "beer", "sake", "wine")
CONDIMENT_WORDS = ("น้ำจิ้ม", "ซอส", "พริก", "มะนาว", "วาซาบิ", "ขิงดอง", "มายองเนส", "น้ำแข็ง", "น้ำเปล่า")
# near names confirmed by eye: new stub code → old costed code(s); a list sums a set
ALIASES = {
    "OTHALSOU00004": ["THBFSOU00002", "THBFSOU00003"],       # ซุปชาบู + ซุปสุกี้ยากี้
    "OTHALCOM00004": ["THALOPE00002"],                       # ถั่วแระญี่ปุ่น + หนังปลาแซลมอนทอดกรอบ
    "OTHBFSPC00091": ["A0032"], "OTHBFSPC00036": ["A0032"], "OTHALSPC00074": ["A0032"],     # ปลาดิบรวม 4 อย่าง
    "OTHBFSPC00030": ["A0023"], "OTHALSPC00024": ["A0023"],  # ปลาแซลมอนย่างซีอิ้ว (+ ซุปมิโซะ ยังไม่มีสูตร)
    "OTHBFSPC00029": ["A0024"], "OTHALSPC00023": ["A0024"],  # ปลาแซลมอนย่างเกลือ (+ ซุปมิโซะ)
    "OTHBFSPC00028": ["THBFGNT00032"],                       # สเต๊กไก่ (+ ซุปมิโซะ)
    "OTHDLSPC00008": ["A0137"],                              # ข้าวหน้าปลาดิบรวมหั่นลูกเต๋า (delivery)
    "OTHALSPC00068": ["A0136"],                              # ข้าวหน้าปลาดิบรวม 5 อย่าง ≈ ข้าวหน้าปลาดิบรวม
    "OTHALSPC00105": ["B0043"],                              # หอยนางรมเสิร์ฟพร้อมน้ำจิ้มซีฟู้ด
    "OTHALSPC00117": ["B0155"],                              # โซบะเย็นเสริฟพร้อมกุ้งเทมปุระ (สะกดต่าง)
    "OTHALSPC00114": ["A0154"],                              # อูด้งร้อนเสริฟพร้อมกุ้งเทมปุระ
}
# weight-based salmon sets: code → grams of salmon
BY_WEIGHT = {"OTHDLPMO00001": 500, "OTHTWPMO00001": 500, "OTHDLPMO00002": 1000, "OTHTWPMO00002": 1000,
             "OTHDLPMO00004": 200, "OTHTWPMO00004": 200, "OTHDLPMO00005": 500, "OTHTWPMO00005": 500}
# per-piece multiples: code → (old per-piece code, pieces)
MULTIPLES = {"OTHDLPMO00011": ("THBFSUS00030", 12),        # SALMON SUSHI 12 คำ ≈ 12 × ซูชิแซลมอนเบิร์น (ราคาชิ้นใกล้เคียง)
             "OTHDLPMO00012": ("THBFSUS00024", 12),        # SALMON SAIKYO ABURI 12 คำ
             "OTHDLPMO00013": ("THBFSUS00030", 12)}        # SALMON ABURI 12 คำ


def norm(desc: str) -> str:
    d = re.sub(r"^\((?:BF|AL|A|B|DL|TW|OT)\)\s*", "", (desc or "").strip())
    d = re.sub(r"\((?:buffet|Buffet|BUFFET)\)", "", d)
    d = re.sub(r"\([^)]*\)", " ", d)
    return re.sub(r"[\s\-_/·,.]+", "", d).lower()


def family(code: str) -> str:
    return code[5:8] if len(code) >= 8 else ""


def variant(code: str) -> str:
    return code[3:5] if len(code) >= 5 else ""


def _now() -> str:
    return datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def load(con) -> dict:
    """Everything the classifier needs, read once."""
    cats = {r["name"]: r["category_id"] for r in con.execute("SELECT category_id, name FROM categories")}
    eff = {r[0]: r[1] for r in con.execute("SELECT sku_id, recipe_cost FROM sku_effective_cost")}
    skus = [dict(r) for r in con.execute("SELECT sku_id, sku_code, description, category_id FROM skus")]
    old_by_norm = {}
    for s in skus:
        if s["sku_code"].startswith("OTH"):
            continue
        c = eff.get(s["sku_id"]) or 0
        if c > 0:
            old_by_norm.setdefault(norm(s["description"]), s | {"eff": c})
    by_code = {s["sku_code"]: s | {"eff": eff.get(s["sku_id"]) or 0} for s in skus}
    qty = {}
    if con.execute("SELECT 1 FROM sqlite_master WHERE name='sale_bill_item'").fetchone():
        for r in con.execute("SELECT i.sku_id, SUM(i.qty) q FROM sale_bill_item i JOIN sales_transaction t ON t.receipt_no=i.receipt_no "
                             "WHERE t.sold_at >= '2026-09-01' GROUP BY i.sku_id"):
            qty[r[0]] = r[1] or 0
    return {"cats": cats, "eff": eff, "by_code": by_code, "old_by_norm": old_by_norm, "qty": qty}


def classify(data: dict) -> list:
    """One decision per uncosted OTH stub: {code, sku_id, description, qty, action, category|cost|note}."""
    out = []
    for code, s in sorted(data["by_code"].items()):
        if not code.startswith("OTH") or (s["eff"] or 0) > 0:
            continue
        d = s["description"] or ""
        dl = d.lower()
        row = {"code": code, "sku_id": s["sku_id"], "description": d, "qty_sep": round(data["qty"].get(s["sku_id"], 0)), "family": family(code),
               "variant": variant(code), "action": "", "category": "", "unit_cost": None, "source": "", "note": ""}
        fam = family(code)
        if variant(code) == "OT" or fam == "OTH":
            row.update(action="category", category=CAT["service"], note="ค่าปรับ/ค่าบริการ ไม่ใช่อาหาร")
        elif fam == "BVR" or "น้ำเปล่า" in d:
            alc = any(w in dl for w in ALCOHOL_WORDS)
            row.update(action="category", category=CAT["alcohol"] if alc else CAT["soft_drink"], note="เครื่องดื่ม — นอกต้นทุนอาหารเหมือนเมนูเดิม")
        elif fam == "APP" or any(w in d for w in CONDIMENT_WORDS) and fam in ("SPC", "APP"):
            row.update(action="category", category=CAT["condiment"], note="เครื่องปรุง/น้ำจิ้ม — นอกต้นทุนอาหารเหมือนเมนูเดิม")
        elif code in ALIASES:
            parts = [data["by_code"].get(c) for c in ALIASES[code]]
            if all(p and p["eff"] > 0 for p in parts):
                row.update(action="cost", unit_cost=round(sum(p["eff"] for p in parts), 4), source="alias:" + "+".join(ALIASES[code]),
                           note="จานเดียวกับเมนูเดิม (ยืนยันชื่อด้วยตา)" + (" — ซุปมิโซะในชุดยังไม่มีสูตร" if "มิโซะ" in d else ""))
            else:
                row.update(action="worklist", note="alias ชี้ไปเมนูที่ไม่มีต้นทุน")
        elif norm(d) in data["old_by_norm"]:
            o = data["old_by_norm"][norm(d)]
            row.update(action="cost", unit_cost=round(o["eff"], 4), source=f"alias:{o['sku_code']}", note=f"ชื่อตรงกับ {o['sku_code']} {o['description']}")
        elif code in BY_WEIGHT:
            g = BY_WEIGHT[code]
            row.update(action="cost", unit_cost=round(g * SALMON_NET_PRICE, 2), source=f"estimate:salmon {g}g x {SALMON_NET_PRICE}/g",
                       note="ESTIMATE น้ำหนักแซลมอน × ราคาสุทธิ/กรัมจาก DATAP (หัก loss 40%)" + (" ไม่รวมน้ำดอง" if "ดอง" in d else ""))
        elif code in MULTIPLES:
            base, n = MULTIPLES[code]
            b = data["by_code"].get(base)
            if b and b["eff"] > 0:
                row.update(action="cost", unit_cost=round(n * b["eff"], 4), source=f"estimate:{n}x{base}", note=f"ESTIMATE {n} ชิ้น × {b['description']}")
            else:
                row.update(action="worklist", note="ฐานต่อชิ้นไม่มีต้นทุน")
        else:
            row.update(action="worklist", note="ต้องการสูตรจากครัว (ไม่อยู่ในสมุดสูตร Grand Menu)")
        out.append(row)
    return out


def nonfood_share_sept(con) -> dict:
    """September bill lines: how many book ฿0 cost, counting only FOOD categories."""
    if not con.execute("SELECT 1 FROM sqlite_master WHERE name='sale_bill_item'").fetchone():
        return {"lines": 0, "zero": 0, "pct": None}
    from core import _NONFOOD_CATEGORY_KEYWORDS
    nf = " AND ".join(f"UPPER(COALESCE(c.name,'')) NOT LIKE '%{kw}%'" for kw in _NONFOOD_CATEGORY_KEYWORDS)
    r = con.execute(f"""SELECT COUNT(*), SUM(CASE WHEN COALESCE(e.recipe_cost,0)=0 THEN 1 ELSE 0 END)
                        FROM sale_bill_item i JOIN sales_transaction t ON t.receipt_no=i.receipt_no
                        JOIN skus s ON s.sku_id=i.sku_id LEFT JOIN categories c ON c.category_id=s.category_id
                        LEFT JOIN sku_effective_cost e ON e.sku_id=s.sku_id
                        WHERE t.sold_at >= '2026-09-01' AND {nf}""").fetchone()
    lines, zero = r[0] or 0, r[1] or 0
    return {"lines": lines, "zero": zero, "pct": round(100.0 * zero / lines, 1) if lines else None}


def rebuild(con):
    import core
    core._build_sku_effective_cost(con, force=True)


def apply(con, decisions: list) -> dict:
    """Write the decisions; return the apply-log (what to undo)."""
    cats = {r["name"]: r["category_id"] for r in con.execute("SELECT category_id, name FROM categories")}
    log = {"tag": TAG, "applied_at": _now(), "categories": [], "sku_cost": []}
    now = _now()
    for d in decisions:
        if d["action"] == "category":
            cid = cats.get(d["category"])
            if cid is None:
                continue
            prev = con.execute("SELECT category_id FROM skus WHERE sku_id=?", (d["sku_id"],)).fetchone()[0]
            con.execute("UPDATE skus SET category_id=? WHERE sku_id=?", (cid, d["sku_id"]))
            log["categories"].append({"sku_id": d["sku_id"], "code": d["code"], "prev": prev, "new": cid})
        elif d["action"] == "cost" and d["unit_cost"]:
            prev = con.execute("SELECT unit_cost, effective_from, updated_at FROM sku_cost WHERE sku_id=?", (d["sku_id"],)).fetchone()
            con.execute("INSERT OR REPLACE INTO sku_cost(sku_id, unit_cost, effective_from, updated_at) VALUES (?,?,?,?)",
                        (d["sku_id"], d["unit_cost"], EFFECTIVE_FROM, now))
            log["sku_cost"].append({"sku_id": d["sku_id"], "code": d["code"], "unit_cost": d["unit_cost"], "source": d["source"],
                                    "prev": list(prev) if prev else None})
    con.commit()
    rebuild(con)
    return log


def undo(con, log: dict) -> dict:
    n_c = n_s = 0
    for c in log.get("categories", []):
        con.execute("UPDATE skus SET category_id=? WHERE sku_id=?", (c["prev"], c["sku_id"])); n_c += 1
    for s in log.get("sku_cost", []):
        if s.get("prev"):
            con.execute("INSERT OR REPLACE INTO sku_cost(sku_id, unit_cost, effective_from, updated_at) VALUES (?,?,?,?)",
                        (s["sku_id"], s["prev"][0], s["prev"][1], s["prev"][2]))
        else:
            con.execute("DELETE FROM sku_cost WHERE sku_id=?", (s["sku_id"],))
        n_s += 1
    con.commit()
    rebuild(con)
    return {"categories": n_c, "sku_cost": n_s}


def write_reports(decisions: list, before: dict, after: dict, out_dir: Path, stamp: str, applied: bool) -> tuple:
    out_dir.mkdir(parents=True, exist_ok=True)
    work = [d for d in decisions if d["action"] == "worklist"]
    work.sort(key=lambda d: -d["qty_sep"])
    wl = out_dir / f"gap_recipes_needed_{stamp}.csv"
    with open(wl, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f); w.writerow(["sku_code", "description", "family", "qty_sep_to_date", "why"])
        for d in work:
            w.writerow([d["code"], d["description"], d["family"], d["qty_sep"], d["note"]])
    by = {}
    for d in decisions:
        by.setdefault(d["action"], []).append(d)
    rp = out_dir / f"gap_report_{stamp}.md"
    lines = [f"# Grand Menu cost gap — {stamp} ({'APPLIED' if applied else 'report only'})", "",
             f"Uncosted OTH stubs: **{len(decisions)}** · September units on them: **{sum(d['qty_sep'] for d in decisions):,}**", "",
             "| action | SKUs | Sept units | meaning |", "|---|---|---|---|"]
    meaning = {"category": "categorised as non-food (drinks / condiments / fees) — outside food cost like the old menu",
               "cost": "given a cost (same dish as an old-menu item, a set of two, salmon by weight, or 12 × a piece — ESTIMATE where noted)",
               "worklist": "needs a recipe from the kitchen → gap_recipes_needed CSV"}
    for a in ("category", "cost", "worklist"):
        ds = by.get(a, [])
        lines.append(f"| {a} | {len(ds)} | {sum(d['qty_sep'] for d in ds):,} | {meaning[a]} |")
    lines += ["", f"September food bill lines with ฿0 cost (food categories only): **{before['pct']}%** → **{after['pct']}%** "
              f"({before['zero']:,}/{before['lines']:,} → {after['zero']:,}/{after['lines']:,})", "", "## Costs written", "",
              "| code | description | Sept units | ฿/serve | source |", "|---|---|---|---|---|"]
    for d in sorted(by.get("cost", []), key=lambda d: -d["qty_sep"]):
        lines.append(f"| {d['code']} | {d['description']} | {d['qty_sep']} | {d['unit_cost']} | {d['source']} — {d['note']} |")
    lines += ["", "## Categorised as non-food", "", "| code | description | Sept units | category |", "|---|---|---|---|"]
    for d in sorted(by.get("category", []), key=lambda d: -d["qty_sep"]):
        lines.append(f"| {d['code']} | {d['description']} | {d['qty_sep']} | {d['category']} |")
    lines += ["", f"## Still need a recipe ({len(work)}) — top 30 by September units", "", "| code | description | Sept units |", "|---|---|---|"]
    for d in work[:30]:
        lines.append(f"| {d['code']} | {d['description']} | {d['qty_sep']} |")
    lines += ["", "To close the rest: add the dishes to Final BOM Grand Menu.xlsx (a BF_/AC_ sheet each), then",
              "`python backend/prep_grand_menu_bom.py` and `python backend/ingest_bom.py` as on 27 Aug.", ""]
    rp.write_text("\n".join(lines), encoding="utf-8")
    return rp, wl


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Close the Grand Menu cost gap")
    ap.add_argument("--db", default=str(HERE / "oshinei.db"))
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--undo", default="", help="apply-log JSON to revert")
    ap.add_argument("--out", default=str(OUT_DIR))
    args = ap.parse_args(argv)
    stamp = datetime.date.today().isoformat()
    con = sqlite3.connect(args.db); con.row_factory = sqlite3.Row
    try:
        if args.undo:
            log = json.loads(Path(args.undo).read_text(encoding="utf-8"))
            r = undo(con, log)
            print(f"✓ undone: {r['categories']} categories, {r['sku_cost']} cost rows; sku_effective_cost rebuilt")
            return 0
        rebuild(con)
        before = nonfood_share_sept(con)
        decisions = classify(load(con))
        counts = {a: sum(1 for d in decisions if d["action"] == a) for a in ("category", "cost", "worklist")}
        print(f"uncosted OTH stubs: {len(decisions)} → category {counts['category']}, cost {counts['cost']}, worklist {counts['worklist']}")
        after = before
        if args.apply:
            log = apply(con, decisions)
            after = nonfood_share_sept(con)
            lp = Path(args.out) / f"gap_apply_{stamp}.json"
            lp.parent.mkdir(parents=True, exist_ok=True)
            lp.write_text(json.dumps(log, ensure_ascii=False, indent=1), encoding="utf-8")
            print(f"✓ applied: {len(log['categories'])} categories, {len(log['sku_cost'])} cost rows · undo with --undo {lp}")
        rp, wl = write_reports(decisions, before, after, Path(args.out), stamp, args.apply)
        print(f"food bill lines with ฿0 cost (Sept): {before['pct']}% → {after['pct']}%")
        print(f"report → {rp}\nworklist → {wl}")
        return 0
    finally:
        con.close()


if __name__ == "__main__":
    sys.exit(main())
