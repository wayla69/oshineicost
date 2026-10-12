# -*- coding: utf-8 -*-
"""bom_center.master — the ingredient master (plan §4.2 M1).

One row per ingredient code in bc_ingredient, built from what the app already
knows, then maintained on screen or by importing a sheet:

  seed_from_db()   ingredient_order_unit (pack sizes + list prices from the
                   supplier sheet DATAP), every code the recipes use, and the
                   warehouse forced-order list (monitored / central). INSERTS
                   new codes and FILLS EMPTY fields only — never overwrites a
                   value a person set. Safe to re-run.
  import_rows()    a sheet in the DATAP layout (ITEM CODE(ACC), PRODUCT DETAILS,
                   UNIT RECEIVED, FULL PRICE, FULL QUANTITY, LOSS (%), DATE …) or
                   the center's own template. An import is a deliberate act, so
                   the values it carries DO overwrite.
  update_rows()    edits from the screen.

The supplier's list price (FULL PRICE / FULL QUANTITY) is kept as a REFERENCE
price (bc_ingredient_price.source='list_ref') — the cost the center prices
recipes with is the accounting system's actual purchase average (§10-1).
"""
import re
from datetime import datetime, timezone

from .units import Converter, norm

SEED = "seed"


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def family_of(code: str) -> str:
    """The code system an ingredient code belongs to (plan §1.2 #6)."""
    c = str(code or "").strip()
    if re.match(r"^\d{2}-\d{2}-\d{3}$", c):
        return "ACC"
    m = re.match(r"^([A-Za-z]+)-", c)
    if m:
        return m.group(1).upper()
    if re.match(r"^\d{4}-\d{2}$", c):
        return "NNNN"
    if c.startswith("วัสดุสิ้นเปลือง"):
        return "CONS"
    return "OTHER"


def kind_of(code: str, name: str = "") -> str:
    """raw / prepared (made in the restaurant from raw: CK, HK, DK) / consumable."""
    fam = family_of(code)
    if fam in ("CK", "HK", "DK"):
        return "prepared"
    if fam == "CONS" or "วัสดุสิ้นเปลือง" in (name or "") or "หลอด" in (name or ""):
        return "consumable"
    return "raw"


def audit(con, who, action, table, key, old=None, new=None):
    con.execute("INSERT INTO bc_audit(at, who, action, table_name, row_key, old_value, new_value)"
                " VALUES(?,?,?,?,?,?,?)", (now(), who, action, table, key,
                                            None if old is None else str(old)[:2000],
                                            None if new is None else str(new)[:2000]))


def _num(v):
    if v is None or v == "":
        return None
    try:
        return float(str(v).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


_FIELDS = ("name_th", "name_en", "family", "kind", "category", "base_unit", "recv_unit",
           "qty_per_recv", "loss_pct", "moq", "lead_days", "shelf_days", "monitored",
           "sourcing_default", "active", "note")


def _upsert(con, code: str, vals: dict, who: str, overwrite: bool, stats: dict):
    """Insert a new code, or update an existing one (fill-empty or overwrite)."""
    vals = {k: v for k, v in vals.items() if k in _FIELDS}
    row = con.execute(f"SELECT {', '.join(_FIELDS)} FROM bc_ingredient WHERE code=?", (code,)).fetchone()
    if row is None:
        # empty values are left out so the column defaults apply (loss_pct NOT NULL DEFAULT 0 …)
        vals = {k: v for k, v in vals.items() if v is not None and v != ""}
        vals.setdefault("family", family_of(code))
        vals.setdefault("kind", kind_of(code, vals.get("name_th") or ""))
        cols = ["code"] + list(vals) + ["updated_at", "updated_by"]
        con.execute(f"INSERT INTO bc_ingredient({', '.join(cols)}) VALUES({', '.join('?' * len(cols))})",
                    [code] + list(vals.values()) + [now(), who])
        stats["inserted"] += 1
        return
    cur = dict(zip(_FIELDS, row))
    change = {}
    for k, v in vals.items():
        if v is None or v == "":
            continue
        if overwrite:
            if cur.get(k) != v:
                change[k] = v
        elif cur.get(k) in (None, ""):
            change[k] = v
    if not change:
        stats["unchanged"] += 1
        return
    sets = ", ".join(f"{k}=?" for k in change) + ", updated_at=?, updated_by=?"
    con.execute(f"UPDATE bc_ingredient SET {sets} WHERE code=?", list(change.values()) + [now(), who, code])
    if overwrite:
        audit(con, who, "update", "bc_ingredient", code, {k: cur.get(k) for k in change}, change)
    stats["updated"] += 1


def _set_ref_price(con, code, month, price_per_base, note, who, stats):
    """Store a supplier list price as a reference row — never over an actual."""
    if not price_per_base or price_per_base <= 0 or not month:
        return
    cur = con.execute("SELECT source FROM bc_ingredient_price WHERE ingredient_code=? AND month=?",
                      (code, month)).fetchone()
    if cur and cur[0] not in ("list_ref",):
        return
    con.execute("INSERT OR REPLACE INTO bc_ingredient_price(ingredient_code, month, price_per_base, source,"
                " price_age_months, imported_at, approved_by, note) VALUES(?,?,?,'list_ref',0,?,?,?)",
                (code, month, round(price_per_base, 6), now(), who, note))
    stats["ref_prices"] += 1


def seed_from_db(con, forced: dict = None, ref_month: str = "2026-07", who: str = SEED) -> dict:
    """Build/extend the master from the tables the app already has. Returns stats."""
    stats = {"inserted": 0, "updated": 0, "unchanged": 0, "aliases": 0, "ref_prices": 0,
             "multi_unit_codes": []}
    has = lambda t: con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (t,)).fetchone()  # noqa: E731

    # 1. pack sizes + supplier list prices (DATAP via seed_order_units.py)
    if has("ingredient_order_unit"):
        for code, base, ounit, per, price, moq, note in con.execute(
                "SELECT ingredient_code, base_unit, order_unit, qty_per_order, price_per_order, moq, note"
                " FROM ingredient_order_unit"):
            code = (code or "").strip()
            if not code:
                continue
            _upsert(con, code, {"name_th": note or None, "base_unit": norm(base) or None,
                                "recv_unit": ounit or None, "qty_per_recv": _num(per),
                                "moq": _num(moq)}, who, False, stats)
            if _num(price) and _num(per):
                _set_ref_price(con, code, ref_month, _num(price) / _num(per), "ราคาตามใบเสนอ (ทะเบียนเดิม)", who, stats)

    # 2. every code a recipe uses (name, unit, type) — one unit per code or flag it
    units = {}
    if has("bom_ingredients"):
        for code, name, unit, itype, n in con.execute(
                "SELECT ingredient_code, MAX(ingredient_name), unit, MAX(ingredient_type), COUNT(*)"
                " FROM bom_ingredients WHERE COALESCE(ingredient_code,'') <> '' AND ingredient_code <> '#N/A'"
                " GROUP BY ingredient_code, unit ORDER BY COUNT(*) DESC"):
            units.setdefault(code.strip(), []).append((norm(unit), name, itype, n))
        for code, opts in units.items():
            unit, name, itype, _ = opts[0]                   # the unit most lines use
            if len({u for u, *_ in opts}) > 1:
                stats["multi_unit_codes"].append({"code": code, "units": [u for u, *_ in opts]})
            cat = itype if itype in ("protein", "carb", "veg", "sauce", "oil", "seasoning", "other") else None
            _upsert(con, code, {"name_th": name, "base_unit": unit or None, "category": cat}, who, False, stats)

    # 3. the warehouse forced-order list: monitored + central; its recipe codes become aliases
    if forced and forced.get("items"):
        for it in forced["items"]:
            code = it["code"]
            _upsert(con, code, {"name_th": it.get("name") or None, "monitored": 1,
                                "sourcing_default": "central"}, who, False, stats)
            con.execute("UPDATE bc_ingredient SET monitored=1 WHERE code=? AND COALESCE(updated_by,?)=?",
                        (code, who, who))
            for rc in it.get("recipe_codes") or []:
                if rc and rc != code:
                    con.execute("INSERT OR IGNORE INTO bc_ingredient_alias(alias_code, ingredient_code, system, note)"
                                " VALUES(?,?, 'warehouse', ?)", (code, rc, "ใบคลังบังคับสั่ง: รหัสคลัง → รหัสสูตร"))
                    stats["aliases"] += 1
    return stats


# ── sheet import (DATAP layout or the center's own template) ─────────────────
IMPORT_ALIASES = {
    "code":       ("itemcode(acc)", "itemcodeacc", "รหัส", "รหัสวัตถุดิบ", "รหัสสินค้า", "code"),
    "name":       ("productdetails", "ชื่อวัตถุดิบ", "ชื่อสินค้า", "รายละเอียด", "name", "name_th"),
    "category":   ("category", "หมวด", "หมวดหมู่"),
    "base_unit":  ("unitreceived", "หน่วยฐาน", "หน่วยนับ", "base_unit", "baseunit"),
    "recv_unit":  ("หน่วยรับ", "หน่วยสั่ง", "recv_unit", "recvunit", "orderunit"),
    "qty_per_recv": ("fullquantity", "ขนาดบรรจุ", "ปริมาณต่อหน่วยรับ", "qty_per_recv", "qtyperrecv"),
    "full_price": ("fullprice", "ราคาต่อหน่วยรับ", "ราคาเต็ม", "price_per_recv"),
    "loss_pct":   ("loss(%)", "loss%", "loss", "สูญเสีย%", "สูญเสีย(%)", "loss_pct"),
    "zone":       ("โซนจัดเก็บ", "โซน", "storage_zone", "zone"),
    "count_unit": ("หน่วยที่นับ", "count_unit", "countunit"),
    "kind":       ("ชนิด", "kind"),
    "monitored":  ("บังคับสั่ง", "monitored"),
    "sourcing":   ("ช่องทางสั่ง", "sourcing", "sourcing_default"),
    "lead_days":  ("leadtime", "lead_days", "ระยะส่ง(วัน)"),
    "shelf_days": ("อายุเก็บ(วัน)", "shelf_days", "shelflife"),
    "moq":        ("ขั้นต่ำ", "moq"),
    "date":       ("date", "วันที่"),
}
IMPORT_OPTIONAL = tuple(k for k in IMPORT_ALIASES if k not in ("code", "name"))
_KIND_TH = {"ดิบ": "raw", "วัตถุดิบ": "raw", "กึ่งสำเร็จ": "prepared", "วัสดุสิ้นเปลือง": "consumable",
            "ตั้งใจ0": "zero_by_design", "ตั้งใจเป็น0": "zero_by_design", "ตั้งใจเป็น 0": "zero_by_design"}


def _month_of(v):
    if v is None or v == "":
        return None
    if hasattr(v, "year"):
        y = v.year - 543 if v.year > 2400 else v.year
        return f"{y:04d}-{v.month:02d}"
    m = re.match(r"^(\d{4})-(\d{1,2})", str(v).strip())
    if m:
        y = int(m.group(1))
        y = y - 543 if y > 2400 else y
        return f"{y:04d}-{int(m.group(2)):02d}"
    return None


def _apply_layout(con, code: str, r: dict, who: str, pending: list, clear: bool) -> bool:
    """PR-K: where an item is kept (storage_zone, given as a zone code or its label) and the unit a branch counts
    it in (count_unit). A key absent from `r` is left alone; an empty value clears it only when `clear` (the screen),
    never from a sheet cell left blank. The count unit is checked once the whole batch is written (`pending`)."""
    from .stock import zone_code_for
    sets, vals, new = [], [], {}
    if r.get("zone") is not None or r.get("storage_zone") is not None:
        raw = r.get("zone") if r.get("zone") is not None else r.get("storage_zone")
        z = zone_code_for(con, raw) if str(raw or "").strip() else ""
        if z or clear:
            sets.append("storage_zone=?")
            vals.append(z or None)
            new["zone"] = z or None
    raw = r.get("count_unit")
    if raw is not None:
        cu = str(raw).strip()
        if cu or clear:
            sets.append("count_unit=?")
            vals.append(cu or None)
            new["count_unit"] = cu or None
            if cu:
                pending.append((code, cu))
    if not sets:
        return False
    old = con.execute("SELECT storage_zone, count_unit FROM bc_ingredient WHERE code=?", (code,)).fetchone()
    cur = {"zone": old[0], "count_unit": old[1]} if old else {}
    if all(cur.get(k) == v for k, v in new.items()):
        return False
    con.execute(f"UPDATE bc_ingredient SET {', '.join(sets)}, updated_at=?, updated_by=? WHERE code=?",
                vals + [now(), who, code])
    audit(con, who, "stock_layout", "bc_ingredient", code, old and {"zone": old[0], "count_unit": old[1]}, new)
    return True


def _check_count_units(con, pending: list) -> list:
    """-> [(code, unit)] whose count unit cannot be put into the base unit (after the batch is written)."""
    if not pending:
        return []
    conv = Converter(con)
    return [(c, u) for c, u in pending if conv.factor(c, u)[0] is None]


def import_rows(con, rows: list, who: str, default_month: str = None) -> dict:
    """Apply parsed sheet rows (dicts keyed by IMPORT_ALIASES). Caller commits."""
    stats = {"inserted": 0, "updated": 0, "unchanged": 0, "ref_prices": 0, "skipped": []}
    pending, undo = [], {}
    before = {c: (z, u) for c, z, u in con.execute("SELECT code, storage_zone, count_unit FROM bc_ingredient")}
    if not default_month:
        # a quote sheet dates most rows (DATAP: 23-07-2569); the rows it leaves undated belong
        # to the same quote — without this they silently lost their price (18 of 317 in DATAP)
        dated = [m for m in (_month_of(r.get("date")) for r in rows) if m]
        default_month = max(set(dated), key=dated.count) if dated else None
        stats["undated_month"] = default_month
    for i, r in enumerate(rows):
        code = str(r.get("code") or "").strip()
        if not code or code in ("#N/A", "None"):
            stats["skipped"].append({"row": i + 1, "why": "ไม่มีรหัส"})
            continue
        vals = {"name_th": (str(r["name"]).strip() if r.get("name") else None),
                "category": r.get("category") or None,
                "base_unit": norm(r.get("base_unit")) or None, "recv_unit": r.get("recv_unit") or None,
                "qty_per_recv": _num(r.get("qty_per_recv")), "loss_pct": _num(r.get("loss_pct")),
                "lead_days": _num(r.get("lead_days")), "shelf_days": _num(r.get("shelf_days")),
                "moq": _num(r.get("moq"))}
        k = str(r.get("kind") or "").replace(" ", "")
        if k:
            vals["kind"] = _KIND_TH.get(k, k if k in ("raw", "prepared", "consumable", "zero_by_design") else None)
        mon = str(r.get("monitored") or "").strip().lower()
        if mon:
            vals["monitored"] = 1 if mon in ("1", "y", "yes", "true", "ใช่", "✓", "x") else 0
        src = str(r.get("sourcing") or "").strip().lower()
        if src:
            vals["sourcing_default"] = "local" if src in ("local", "สาขาซื้อเอง", "สาขา") else "central"
        if vals["loss_pct"] is not None and not (0 <= vals["loss_pct"] < 100):
            stats["skipped"].append({"row": i + 1, "code": code, "why": "สูญเสีย % ต้องอยู่ระหว่าง 0–99"})
            continue
        try:
            if str(r.get("zone") or "").strip():
                from .stock import zone_code_for
                zone_code_for(con, r["zone"])              # an unknown zone skips the row, nothing of it is written
        except ValueError as e:
            stats["skipped"].append({"row": i + 1, "code": code, "why": str(e)})
            continue
        _upsert(con, code, vals, who, True, stats)
        undo.setdefault(code, before.get(code))
        _apply_layout(con, code, r, who, pending, False)
        price, per = _num(r.get("full_price")), vals["qty_per_recv"]
        month = _month_of(r.get("date")) or default_month
        if price and per:
            _set_ref_price(con, code, month, price / per, "ราคาตามใบเสนอ (นำเข้าทะเบียน)", who, stats)
    for c, u in _check_count_units(con, pending):          # a count unit that cannot be converted: keep the old one
        z0, u0 = undo.get(c) or (None, None)
        con.execute("UPDATE bc_ingredient SET count_unit=? WHERE code=?", (u0, c))
        stats["skipped"].append({"code": c, "why": f"หน่วยที่นับ “{u}” แปลงเป็นหน่วยฐานไม่ได้ · เพิ่มกฎหน่วยก่อน"})
    return stats


def update_rows(con, rows: list, who: str) -> dict:
    """Edits from the screen: [{code, field: value, …}]. Caller commits."""
    stats = {"inserted": 0, "updated": 0, "unchanged": 0}
    pending = []
    for r in rows:
        code = str(r.get("code") or "").strip()
        if not code:
            continue
        vals = {k: r[k] for k in _FIELDS if k in r}
        if "name_th" in vals:                       # a name typed on the screen: never empty, never only digits and signs ("-3")
            name = str(vals["name_th"] or "").strip()
            if not name:
                raise ValueError(f"{code}: ชื่อต้องไม่ว่าง")
            if not re.search(r"[A-Za-zก-๙]", name):
                raise ValueError(f"{code}: ชื่อต้องมีตัวอักษร")
            vals["name_th"] = name
        if "base_unit" in vals:
            vals["base_unit"] = norm(vals["base_unit"]) or None
        for k in ("qty_per_recv", "loss_pct", "moq", "lead_days", "shelf_days"):
            if k in vals:
                vals[k] = _num(vals[k])
        if vals.get("loss_pct") is not None and not (0 <= vals["loss_pct"] < 100):
            raise ValueError(f"{code}: สูญเสีย % ต้องอยู่ระหว่าง 0–99")
        if vals.get("qty_per_recv") is not None and vals["qty_per_recv"] <= 0:
            raise ValueError(f"{code}: ขนาดบรรจุต้องมากกว่า 0")
        _upsert(con, code, vals, who, True, stats)
        _apply_layout(con, code, r, who, pending, True)
    bad = _check_count_units(con, pending)
    if bad:
        raise ValueError("; ".join(f"{c}: ยังแปลง {u} เป็นหน่วยฐานไม่ได้ · เพิ่มกฎหน่วยก่อน" for c, u in bad[:3]))
    return stats


def add_from_purchase(con, code: str, name: str, unit: str, who: str) -> str:
    """A code the accounting report issues but no recipe or supplier sheet knows
    (durian FV00043, cleaning stock …): register it so its price is kept. The base
    unit is inferred from the unit it was bought in — weight -> กรัม, volume ->
    มิลลิลิตร, otherwise the unit itself (1 ฟอง = 1 ฟอง) — and the row says so,
    so the analyst checks it before any recipe uses it."""
    u = norm(str(unit or "").split(",")[0])
    if u in ("กิโลกรัม", "กรัม", "ขีด"):
        base = "กรัม"
    elif u in ("ลิตร", "มิลลิลิตร"):
        base = "มิลลิลิตร"
    else:
        base = u or None
    stats = {"inserted": 0, "updated": 0, "unchanged": 0}
    _upsert(con, code, {"name_th": (str(name).strip() if name else None), "base_unit": base,
                        "note": f"เพิ่มจากรายงานบัญชี (ซื้อเป็น {unit}) · ตรวจหน่วยฐานและขนาดบรรจุ"}, who, False, stats)
    if stats["inserted"]:
        audit(con, who, "add_from_purchase", "bc_ingredient", code, None, base)
    return base


def _ladder_rows(con, code: str) -> list:
    """The stored ladder of one item in the shape check_ladder / set_ladder take."""
    return [{"unit": u, "parent_unit": p, "qty_in_parent": q if q is not None else f, "conv_kind": k or "exact",
             "range_min": a, "range_max": b, "label": lb}
            for u, f, p, q, k, a, b, lb in con.execute(
                "SELECT unit, factor_to_base, parent_unit, qty_in_parent, conv_kind, range_min, range_max, label"
                " FROM bc_unit_rule WHERE ingredient_code=? ORDER BY rowid", (code,))]


def _norm_ladder_row(r: dict) -> dict:
    """One ladder row cleaned (units normalised, numbers parsed). An average with no number takes the midpoint of its range."""
    from .units import kind_of
    kind = kind_of(r.get("conv_kind"))
    parent = norm(r.get("parent_unit")) or None
    q, a, b = _num(r.get("qty_in_parent")), _num(r.get("range_min")), _num(r.get("range_max"))
    if q is None and kind == "avg" and a is not None and b is not None:
        q = (a + b) / 2.0
    return {"unit": norm(r.get("unit")), "parent_unit": parent, "qty_in_parent": q, "conv_kind": kind or r.get("conv_kind"),
            "range_min": a, "range_max": b, "label": (str(r.get("label")).strip() or None) if r.get("label") else None}


def plan_ladder(con, code: str, rows: list) -> dict:
    """What set_ladder WOULD write for one item (nothing is written): {code, name, base_unit, ladder, problems,
    changes, qty_per_recv_change}. `ladder` carries the recomputed factors, so a preview shows 1 ตัว ≈ 4,500 กรัม."""
    from .units import MAX_STEPS, check_ladder, climb, chain_text
    m = con.execute("SELECT name_th, base_unit, recv_unit, qty_per_recv FROM bc_ingredient WHERE code=?", (code,)).fetchone()
    if not m:
        return {"code": code, "name": None, "base_unit": None, "ladder": [], "problems": [f"ไม่พบรหัส {code} ในทะเบียน"],
                "changes": [], "qty_per_recv_change": None}
    name, base_raw, recv, per = m
    base = norm(base_raw)
    new = [_norm_ladder_row(r) for r in rows or []]
    problems = check_ladder(base, new)
    out = {"code": code, "name": name, "base_unit": base, "ladder": [], "problems": problems, "changes": [],
           "qty_per_recv_change": None, "max_steps": MAX_STEPS}
    if problems:
        return out
    table = {r["unit"]: {"parent": r["parent_unit"], "qty": r["qty_in_parent"], "kind": r["conv_kind"], "rmin": r["range_min"],
                         "rmax": r["range_max"], "factor": r["qty_in_parent"]} for r in new}
    for r in new:
        f, approx, parts, why = climb(base, table, r["unit"])
        if f is None:
            problems.append(why)
            continue
        out["ladder"].append({**r, "parent_unit": r["parent_unit"] or base, "factor_to_base": f, "approx": bool(approx),
                              "level": sum(1 for p in parts if not p.get("metric")), "how": chain_text(parts)})
    if problems:
        return out
    old = {r["unit"]: r for r in _ladder_rows(con, code)}
    for r in out["ladder"]:
        o = old.get(r["unit"])
        if o is None:
            out["changes"].append({"unit": r["unit"], "change": "add"})
        elif any((o.get(k) or None) != (r.get(k) or None) for k in ("qty_in_parent", "conv_kind", "range_min", "range_max")) \
                or (norm(o.get("parent_unit")) or base) != r["parent_unit"]:
            out["changes"].append({"unit": r["unit"], "change": "edit"})
    for u in old:
        if u not in table:
            out["changes"].append({"unit": u, "change": "remove"})
    recv_n = norm(recv)
    hit = next((r for r in out["ladder"] if r["unit"] == recv_n), None)
    if hit and (per is None or abs(float(per) - hit["factor_to_base"]) > 1e-9):
        out["qty_per_recv_change"] = {"recv_unit": recv, "from": per, "to": hit["factor_to_base"], "approx": hit["approx"]}
    return out


def set_ladder(con, code: str, rows: list, who: str) -> dict:
    """Replace the WHOLE ladder of one item (PR-U). Validated as a whole (units.check_ladder); every row is stored with
    factor_to_base recomputed through the ladder, so every reader of the old single-step rule keeps working. When the
    item's receiving unit is one of the ladder units, its pack size (qty_per_recv) follows the ladder. Caller commits."""
    code = (code or "").strip()
    if not code:
        raise ValueError("ต้องระบุรหัส")
    plan = plan_ladder(con, code, rows)
    if plan["problems"]:
        raise ValueError("; ".join(plan["problems"][:4]))
    old = _ladder_rows(con, code)
    keep = {r["unit"] for r in plan["ladder"]}
    for r in old:
        if norm(r["unit"]) not in keep:
            con.execute("DELETE FROM bc_unit_rule WHERE ingredient_code=? AND unit=?", (code, r["unit"]))
    prev = {norm(r["unit"]): r for r in old}
    for r in plan["ladder"]:
        label = r.get("label") or (prev.get(r["unit"]) or {}).get("label")
        parent = None if r["parent_unit"] == plan["base_unit"] else r["parent_unit"]
        con.execute(
            "INSERT INTO bc_unit_rule(ingredient_code, unit, factor_to_base, label, parent_unit, qty_in_parent, conv_kind,"
            " range_min, range_max, updated_at, updated_by) VALUES(?,?,?,?,?,?,?,?,?,?,?)"
            " ON CONFLICT(ingredient_code, unit) DO UPDATE SET factor_to_base=excluded.factor_to_base, label=excluded.label,"
            " parent_unit=excluded.parent_unit, qty_in_parent=excluded.qty_in_parent, conv_kind=excluded.conv_kind,"
            " range_min=excluded.range_min, range_max=excluded.range_max, updated_at=excluded.updated_at,"
            " updated_by=excluded.updated_by",
            (code, r["unit"], r["factor_to_base"], label, parent, r["qty_in_parent"], r["conv_kind"],
             r["range_min"], r["range_max"], now(), who))
    ch = plan["qty_per_recv_change"]
    if ch:
        con.execute("UPDATE bc_ingredient SET qty_per_recv=?, updated_at=?, updated_by=? WHERE code=?",
                    (ch["to"], now(), who, code))
    brief = lambda rs: [f"{r['unit']}={r['qty_in_parent']:g} {r.get('parent_unit') or 'หน่วยฐาน'} ({r.get('conv_kind') or 'exact'})"  # noqa: E731
                        for r in rs]
    audit(con, who, "unit_ladder", "bc_unit_rule", code, brief(old),
          {"ladder": brief(plan["ladder"]), "qty_per_recv": ch and [ch["from"], ch["to"]]})
    return {"code": code, "ladder": plan["ladder"], "qty_per_recv_change": ch, "changes": plan["changes"]}


def set_unit_rule(con, code: str, unit: str, factor: float, label: str, who: str, parent_unit: str = None,
                  qty_in_parent: float = None, conv_kind: str = None, range_min: float = None, range_max: float = None):
    """One rule of the ladder: the existing rows of the item + this one (or minus it when `factor` is None), validated as a
    whole. With only `factor` it is the old single step on the base unit. A code not in the master is written as it
    always was (there is no base unit to check against)."""
    u = norm(unit)
    if not code or not u:
        raise ValueError("ต้องระบุรหัสและหน่วย")
    known = con.execute("SELECT 1 FROM bc_ingredient WHERE code=?", (code,)).fetchone()
    if factor is None and qty_in_parent is None:       # delete one rule — unless another unit is stated against it
        kids = [r["unit"] for r in _ladder_rows(con, code) if norm(r["parent_unit"]) == u]
        if kids:
            raise ValueError(f"{u}: ยังมีหน่วย {', '.join(kids)} อ้างอิงหน่วยนี้อยู่ · ลบหรือแก้หน่วยนั้นก่อน")
        old = con.execute("SELECT factor_to_base FROM bc_unit_rule WHERE ingredient_code=? AND unit=?", (code, u)).fetchone()
        con.execute("DELETE FROM bc_unit_rule WHERE ingredient_code=? AND unit=?", (code, u))
        audit(con, who, "delete", "bc_unit_rule", f"{code}|{u}", old[0] if old else None, None)
        return
    q = qty_in_parent if qty_in_parent is not None else factor
    if q is None or q <= 0:
        raise ValueError("ตัวคูณต้องมากกว่า 0")
    if not known:
        con.execute("INSERT OR REPLACE INTO bc_unit_rule(ingredient_code, unit, factor_to_base, label, qty_in_parent, updated_at,"
                    " updated_by) VALUES(?,?,?,?,?,?,?)", (code, u, float(q), label or None, float(q), now(), who))
        audit(con, who, "set", "bc_unit_rule", f"{code}|{u}", None, q)
        return
    rest = [r for r in _ladder_rows(con, code) if norm(r["unit"]) != u]
    rest.append({"unit": u, "parent_unit": parent_unit, "qty_in_parent": q, "conv_kind": conv_kind or "exact",
                 "range_min": range_min, "range_max": range_max, "label": label})
    set_ladder(con, code, rest, who)
