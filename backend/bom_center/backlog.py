# -*- coding: utf-8 -*-
"""Batch 4b (3.5) - the analyst's data-quality backlog, ranked by what each item touches.

Six kinds of open data work already existed as separate counts (units 10 times off, a missing base unit or pack size, a legacy code whose
name does not match, a prepared item with no sub-recipe, release checks that fail, recipe lines that cannot be converted). A count says
"67 of these"; it does not say which one costs the most. This ranks every item by

    plates   the plates the chain sold in the last 30 days of sales whose recipe goes through the item (the forecast's own per-dish
             expansion, forecast._per_dish - the same one that makes stats.missed - times the plates of each dish), and
    value    ฿ of the item used through those plates at the price in force (the price book's reference price when it has none),

plates first, ฿ second, inside a tier (TIER below: plates dropped from the suggestion, then plates on a wrong basis, then data to confirm). The top of
the list is therefore what makes the order suggestion wrong for the most plates: ข้าวสวยญี่ปุ่น (CK-001) with an incomplete sub-recipe comes out
first while it is incomplete. Nothing here changes data: each item links to the page that fixes it.
"""
import threading
import time as _time
from collections import defaultdict
from datetime import date, timedelta

from . import forecast as FC

TOP = 10
WINDOW_DAYS = 30
KIND_TH = {"unit_missing": "แปลงหน่วยในสูตรไม่ได้", "subrecipe_incomplete": "สูตรย่อยไม่ครบ", "subrecipe_missing": "ยังไม่มีสูตรย่อย",
           "x10": "หน่วยต่างจากยอดซื้อเกิน 10 เท่า", "master_gap": "ไม่มีหน่วยฐานหรือขนาดบรรจุ",
           "legacy_name": "รหัสเดิมชื่อไม่ตรงทะเบียน", "check": "รายการตรวจที่ไม่ผ่าน"}
KIND_ORDER = list(KIND_TH)
# What the plates mean for the order suggestion decides the tier; inside a tier the plates (then ฿) rank:
#   0  the plates are NOT counted in the order at all (a recipe line the forecast skips: forecast `stats.missed`);
#   1  the plates are counted on a wrong basis (an item ordered as itself, a unit ten times off, a failing release check);
#   2  data to confirm (a legacy name, a missing base unit or pack size) - the plates are touched, whether they are wrong is not known yet.
TIER = {"subrecipe_incomplete": 0, "unit_missing": 0, "subrecipe_missing": 1, "x10": 1, "check": 1, "master_gap": 2, "legacy_name": 2}
TIER_TH = ("จานที่ไม่ถูกนับในยอดสั่ง", "จานที่คำนวณด้วยฐานที่ผิด", "ข้อมูลที่ต้องยืนยัน")
_SUB_INCOMPLETE = "สูตรย่อยไม่สมบูรณ์"

_cache: dict = {}
_lock = threading.Lock()


def clear_cache() -> None:
    _cache.clear()


def _sales_table(con) -> str:
    return "v_sales_sku_daily" if con.execute("SELECT 1 FROM sqlite_master WHERE name='v_sales_sku_daily'").fetchone() else "sales_sku_daily"


def impact_th(plates, value) -> str:
    """"กระทบ 1,234 จาน / ฿5,600 ต่อเดือน" - what the list shows beside every item."""
    p = f"{int(round(plates or 0)):,} จาน"
    v = f" / ฿{int(round(value)):,}" if value else ""
    return f"กระทบ {p}{v} ต่อเดือน"


def _impact(con, d1: date):
    """(plates by ingredient code, ฿ by code, plates of each unconvertible line {(code, unit, why): plates}, plates by sku code, the window)."""
    d0 = d1 - timedelta(days=WINDOW_DAYS - 1)
    month = f"{d1.year:04d}-{d1.month:02d}"
    sold = {sid: q or 0.0 for sid, q in con.execute(
        f"SELECT sku_id, SUM(qty) FROM {_sales_table(con)} WHERE day BETWEEN ? AND ? GROUP BY sku_id", (d0.isoformat(), d1.isoformat()))}
    per, stats = FC._per_dish(con, month)
    bad = stats.pop("_bad", {})
    plates, used, bad_plates = defaultdict(float), defaultdict(float), defaultdict(float)
    for sid, qty in sold.items():
        seen = set()
        for code, pq in per.get(sid, ()):
            used[code] += qty * pq
            if code not in seen:
                seen.add(code)
                plates[code] += qty
        for key in set(bad.get(sid, ())):
            bad_plates[key] += qty
    from .prices import PriceBook
    book = PriceBook(con, month)
    value = {c: u * (book.ref(c) or 0.0) for c, u in used.items()}
    sku_code = dict(con.execute("SELECT sku_code, sku_id FROM skus"))
    by_sku = {code: sold.get(sid, 0.0) for code, sid in sku_code.items() if sid in sold}
    return plates, value, bad_plates, by_sku, {"from": d0.isoformat(), "to": d1.isoformat(), "days": WINDOW_DAYS, "month": month}


def build(con, top: int = TOP) -> dict:
    """The ranked backlog: {window, items (all, ranked), top (the first `top`), counts per kind, total, unimpacted}. Cached like the
    forecast's usage (same TTL); forecast.clear_usage_cache() drops it with the other caches after every write to the register, the units,
    the recipes, the sub-recipes or the code links - the writes that change what the backlog says."""
    last = con.execute(f"SELECT MAX(day) FROM {_sales_table(con)}").fetchone()[0]
    key = None
    try:
        path = next((r[2] for r in con.execute("PRAGMA database_list") if r[1] == "main"), "")
        core = FC._loaded("core")
        key = (path, last, getattr(core, "_data_gen", 0), getattr(core, "_db_gen", 0)) if path and FC.USAGE_CACHE_TTL else None
    except Exception:
        key = None
    if key is not None:
        hit = _cache.get(key)
        if hit and _time.monotonic() - hit[0] < FC.USAGE_CACHE_TTL:
            return _cut(hit[1], top)
    with _lock:
        out = _build(con, last)
        if key is not None:
            if len(_cache) >= 4:
                _cache.pop(min(_cache, key=lambda k: _cache[k][0]))
            _cache[key] = (_time.monotonic(), out)
    return _cut(out, top)


def _cut(out: dict, top: int) -> dict:
    return dict(out, top=out["items"][:max(int(top), 0)])


def _build(con, last) -> dict:
    items = []
    if not last:
        return {"window": None, "items": [], "counts": {k: 0 for k in KIND_TH}, "total": 0, "unimpacted": 0, "kind_th": KIND_TH}
    plates, value, bad_plates, by_sku, window = _impact(con, date.fromisoformat(last))
    names = {c: (n, u) for c, n, u in con.execute("SELECT code, name_th, base_unit FROM bc_ingredient")}
    nm = lambda c: (names.get(c) or (c, None))[0] or c            # noqa: E731
    seen = set()

    def add(kind, code, title, detail, pl, val, go, **extra):
        k = (kind, code)
        if k in seen:
            return
        seen.add(k)
        d = {"kind": kind, "kind_th": KIND_TH[kind], "tier": TIER[kind], "tier_th": TIER_TH[TIER[kind]], "key": f"{kind}:{code}", "code": code, "name": nm(code), "title": title,
             "detail": detail, "plates": round(pl or 0), "value": round(val or 0), "impact_th": impact_th(pl, val), "go": go}
        d.update(extra)
        items.append(d)

    # 1 · recipe lines the forecast cannot count: a unit with no rule, or a sub-recipe that is not complete
    covered_bad = set()
    for (code, unit, why), pl in sorted(bad_plates.items(), key=lambda kv: -kv[1]):
        covered_bad.add(code)
        if str(why).startswith(_SUB_INCOMPLETE):
            add("subrecipe_incomplete", code, f"สูตรย่อยของ {nm(code)} ไม่ครบ", str(why).replace(_SUB_INCOMPLETE + ": ", "ขาด: ") if ": " in str(why) else str(why),
                pl, value.get(code), {"tab": "bomcenter", "pane": "prep", "extra": {"code": code}})
        else:
            add("unit_missing", code, f"ตั้งกฎแปลงหน่วยของ {nm(code)} ({unit or '-'})", str(why or ""),
                pl, value.get(code), {"tab": "bomcenter", "pane": "conv", "extra": {"code": code}}, unit=unit)
    # 2 · a prepared item with no sub-recipe is ordered as itself
    for (code,) in con.execute("SELECT code FROM bc_ingredient WHERE COALESCE(active,1)=1 AND kind='prepared'"
                               " AND code NOT IN (SELECT prepared_code FROM bc_prep_recipe)"):
        if plates.get(code):
            add("subrecipe_missing", code, f"ยังไม่มีสูตรย่อยของ {nm(code)}", "สั่งเป็นตัวสินค้าเอง ไม่ได้แตกเป็นวัตถุดิบ",
                plates[code], value.get(code), {"tab": "bomcenter", "pane": "prep", "extra": {"code": code}})
    # 3 · units that differ from what was bought by 10 times or more
    try:
        from . import recipe_check as RC
        m = RC.default_month(con)
        chk = RC.check(con, m) if m else None
    except Exception:
        chk = None
    if chk and chk.get("rows"):
        sales_sum = sum(b["sales"] for b in chk["branches"]) or 0.0
        for r in chk["rows"]:
            if r["status"] == "x10":
                add("x10", r["code"], f"ตรวจหน่วยของ {r['name']}", f"ซื้อ {r['bought']:,.1f} · ตามสูตร {r['recipe']:,.1f} {r.get('base_unit') or ''} ต่อยอดขาย ฿100,000".replace("  ", " "),
                    plates.get(r["code"]), (r.get("impact_value") or 0.0) * sales_sum / 1e5 or value.get(r["code"]),
                    {"tab": "bomcenter", "pane": "rcheck", "month": m, "extra": {"item": r["code"]}})
    # 4 · no base unit, or a raw item with no pack size (an item already listed above for its unit is not listed twice)
    n_gap = 0
    for code, base, per, kind in con.execute("SELECT code, base_unit, qty_per_recv, kind FROM bc_ingredient WHERE COALESCE(active,1)=1"):
        no_base = not (base or "").strip()
        no_pack = kind == "raw" and (per is None or per <= 0)
        if not (no_base or no_pack):
            continue
        n_gap += 1
        if code in covered_bad or not plates.get(code):
            continue
        add("master_gap", code, f"ระบุ{'หน่วยฐาน' if no_base else 'ขนาดบรรจุ'}ของ {nm(code)}", "ไม่มีหน่วยฐาน" if no_base else "ไม่มีขนาดบรรจุ",
            plates.get(code), value.get(code), {"tab": "bomcenter", "pane": "ing", "extra": {"prob": "ไม่มีหน่วยฐาน" if no_base else "ไม่มีขนาดบรรจุ"}})
    # 5 · a legacy code whose name does not match the register
    try:
        from .codes import review
        for r in review(con, suggestions=False)["rows"]:
            if r["status"] in ("conflict", "missing"):
                add("legacy_name", r["code"], f"รหัสเดิมชื่อไม่ตรง: {r['code']} {r['recipe_name'] or ''}".strip(),
                    "ชื่อในสูตรต่างจากทะเบียน" if r["status"] == "conflict" else "ไม่อยู่ในทะเบียน",
                    plates.get(r["code"]) or 0, value.get(r["code"]), {"tab": "bomcenter", "pane": "overview", "anchor": "bmcIssuesCard"},
                    dishes=r.get("todo_dishes") or r.get("dishes"))
    except Exception:
        pass
    # 6 · release checks that block the draft in progress: the plates of the dish (or of the prepared item) each one is about
    rel = con.execute("SELECT release_id FROM bc_release WHERE kind='monthly' AND status IN ('draft','review') ORDER BY release_id DESC LIMIT 1").fetchone()
    if rel:
        from .release import CHECKS
        agg = {}
        for code, ref, msg in con.execute("SELECT check_code, ref, message FROM bc_release_check WHERE release_id=? AND severity='block'"
                                          " AND resolved_by IS NULL", (rel[0],)):
            base = str(ref).split("#")[0]
            a = agg.setdefault((code, base), {"n": 0, "msg": msg})
            a["n"] += 1
        for (code, base), a in agg.items():
            pl = by_sku.get(base) if base in by_sku else plates.get(base)
            add("check", f"{code}|{base}", f"{CHECKS.get(code, ('', code))[1]}: {nm(base) if base in names else base}",
                f"{a['n']} บรรทัด · {a['msg']}", pl or 0, value.get(base), {"tab": "bomcenter", "pane": "rel", "anchor": "bmcRlChecks"})
    items.sort(key=lambda d: (d["tier"], -d["plates"], -d["value"], KIND_ORDER.index(d["kind"]), d["code"]))
    counts = {k: sum(1 for d in items if d["kind"] == k) for k in KIND_TH}
    return {"window": window, "items": items, "counts": counts, "total": len(items), "kind_th": KIND_TH,
            "unimpacted": max(n_gap - counts["master_gap"], 0), "tier_th": list(TIER_TH),
            "note": "เรียงตามจานที่กระทบ: จานที่ไม่ถูกนับในยอดสั่งก่อน แล้วจานที่คำนวณด้วยฐานที่ผิด แล้วข้อมูลที่ต้องยืนยัน · จานและมูลค่าคิดจากยอดขาย 30 วันล่าสุดของทุกสาขา"}
