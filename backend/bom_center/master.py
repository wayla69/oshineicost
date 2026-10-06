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

from .units import norm

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


_FIELDS = ("name_th", "name_en", "family", "kind", "category", "supplier", "base_unit", "recv_unit",
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
                _set_ref_price(con, code, ref_month, _num(price) / _num(per), "ราคาตามใบเสนอ (DATAP)", who, stats)

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
    "supplier":   ("tradingcompany", "supplier", "ผู้ขาย", "ซัพพลายเออร์"),
    "base_unit":  ("unitreceived", "หน่วยฐาน", "หน่วยนับ", "base_unit", "baseunit"),
    "recv_unit":  ("หน่วยรับ", "หน่วยสั่ง", "recv_unit", "recvunit", "orderunit"),
    "qty_per_recv": ("fullquantity", "ขนาดบรรจุ", "ปริมาณต่อหน่วยรับ", "qty_per_recv", "qtyperrecv"),
    "full_price": ("fullprice", "ราคาต่อหน่วยรับ", "ราคาเต็ม", "price_per_recv"),
    "loss_pct":   ("loss(%)", "loss%", "loss", "สูญเสีย%", "สูญเสีย(%)", "loss_pct"),
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
            "ตั้งใจ0": "zero_by_design", "ตั้งใจเป็น0": "zero_by_design"}


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


def import_rows(con, rows: list, who: str, default_month: str = None) -> dict:
    """Apply parsed sheet rows (dicts keyed by IMPORT_ALIASES). Caller commits."""
    stats = {"inserted": 0, "updated": 0, "unchanged": 0, "ref_prices": 0, "skipped": []}
    for i, r in enumerate(rows):
        code = str(r.get("code") or "").strip()
        if not code or code in ("#N/A", "None"):
            stats["skipped"].append({"row": i + 1, "why": "ไม่มีรหัส"})
            continue
        vals = {"name_th": (str(r["name"]).strip() if r.get("name") else None),
                "category": r.get("category") or None, "supplier": r.get("supplier") or None,
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
            stats["skipped"].append({"row": i + 1, "code": code, "why": "LOSS % ต้องอยู่ระหว่าง 0–99"})
            continue
        _upsert(con, code, vals, who, True, stats)
        price, per = _num(r.get("full_price")), vals["qty_per_recv"]
        month = _month_of(r.get("date")) or default_month
        if price and per:
            _set_ref_price(con, code, month, price / per, "ราคาตามใบเสนอ (นำเข้าทะเบียน)", who, stats)
    return stats


def update_rows(con, rows: list, who: str) -> dict:
    """Edits from the screen: [{code, field: value, …}]. Caller commits."""
    stats = {"inserted": 0, "updated": 0, "unchanged": 0}
    for r in rows:
        code = str(r.get("code") or "").strip()
        if not code:
            continue
        vals = {k: r[k] for k in _FIELDS if k in r}
        if "base_unit" in vals:
            vals["base_unit"] = norm(vals["base_unit"]) or None
        for k in ("qty_per_recv", "loss_pct", "moq", "lead_days", "shelf_days"):
            if k in vals:
                vals[k] = _num(vals[k])
        if vals.get("loss_pct") is not None and not (0 <= vals["loss_pct"] < 100):
            raise ValueError(f"{code}: LOSS % ต้องอยู่ระหว่าง 0–99")
        if vals.get("qty_per_recv") is not None and vals["qty_per_recv"] <= 0:
            raise ValueError(f"{code}: ขนาดบรรจุต้องมากกว่า 0")
        _upsert(con, code, vals, who, True, stats)
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
                        "note": f"เพิ่มจากรายงานบัญชี (ซื้อเป็น {unit}) — ตรวจหน่วยฐานและขนาดบรรจุ"}, who, False, stats)
    if stats["inserted"]:
        audit(con, who, "add_from_purchase", "bc_ingredient", code, None, base)
    return base


def set_unit_rule(con, code: str, unit: str, factor: float, label: str, who: str):
    u = norm(unit)
    if not code or not u:
        raise ValueError("ต้องระบุรหัสและหน่วย")
    if factor is None:
        old = con.execute("SELECT factor_to_base FROM bc_unit_rule WHERE ingredient_code=? AND unit=?", (code, u)).fetchone()
        con.execute("DELETE FROM bc_unit_rule WHERE ingredient_code=? AND unit=?", (code, u))
        audit(con, who, "delete", "bc_unit_rule", f"{code}|{u}", old[0] if old else None, None)
        return
    if factor <= 0:
        raise ValueError("ตัวคูณต้องมากกว่า 0")
    con.execute("INSERT OR REPLACE INTO bc_unit_rule(ingredient_code, unit, factor_to_base, label) VALUES(?,?,?,?)",
                (code, u, float(factor), label or None))
    audit(con, who, "set", "bc_unit_rule", f"{code}|{u}", None, factor)
