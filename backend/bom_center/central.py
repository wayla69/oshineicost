# -*- coding: utf-8 -*-
"""Phase 2 · S11 — the central list: what a branch orders from the centre.

The centre's monthly "sales to branches" report (ยอดสั่งสินค้า ทุกสาขา) lists every
item the centre supplies, in the centre's sale unit and price, and how much each
branch took that month. It drives three things:

  catalogue   the items on the list are ordered from the centre (sourcing
              'central', in the centre's sale unit); everything else a branch
              buys from local suppliers (sourcing 'local')
  deliveries  each branch's monthly quantities become one goods-received note
              per branch (ref CENTRAL-YYYY-MM), so a month can be closed
  prices      what a branch pays the centre per sale unit (kept per month)

The report's unit column reads "<sale unit><inner qty>/<inner unit>", e.g.
"กล10.00/PK" = a box of 10 packs, "KG1.00/KG" = by the kilo. A quantity like
"2/39.00" = 2 sale units and 39 inner units. Nothing is guessed: an item whose
sale unit cannot be put into base units is listed for a person to answer.
"""
import io
import re
from datetime import date, datetime, timedelta, timezone

from .master import _upsert, audit, kind_of
from .units import Converter, norm

DDL = """CREATE TABLE IF NOT EXISTS bc_central_item(
    code TEXT PRIMARY KEY,
    name TEXT,
    unit_label TEXT,                   -- the report's "หน่วยนับ" as written (กล10.00/PK)
    sale_unit TEXT,                    -- the report's "หน่วยขาย" (Box / KG / PK …)
    inner_qty REAL, inner_unit TEXT,   -- 10 / PK
    base_per_sale REAL,                -- base units in one sale unit (NULL = a person must say)
    factor_how TEXT,
    price_per_sale REAL, price_month TEXT,
    active INTEGER NOT NULL DEFAULT 1,
    source_ref TEXT, updated_at TEXT, updated_by TEXT)"""
# the recipes still name some items by an older code than the one the centre sends
# (03-01-288 tuna in the recipes, 03-01-074 on the delivery): what is used is counted
# under the code that arrives — for ordering and the month close only, never for cost
LINK_DDL = """CREATE TABLE IF NOT EXISTS bc_code_link(
    old_code TEXT PRIMARY KEY, new_code TEXT NOT NULL, note TEXT, who TEXT, at TEXT)"""

THAI_MONTHS = {"มกราคม": 1, "กุมภาพันธ์": 2, "มีนาคม": 3, "เมษายน": 4, "พฤษภาคม": 5, "มิถุนายน": 6,
               "กรกฎาคม": 7, "สิงหาคม": 8, "กันยายน": 9, "ตุลาคม": 10, "พฤศจิกายน": 11, "ธันวาคม": 12,
               "ม.ค.": 1, "ก.พ.": 2, "มี.ค.": 3, "เม.ย.": 4, "พ.ค.": 5, "มิ.ย.": 6,
               "ก.ค.": 7, "ส.ค.": 8, "ก.ย.": 9, "ต.ค.": 10, "พ.ย.": 11, "ธ.ค.": 12}
# the report's abbreviations -> the unit words the converter knows
INNER_TH = {"KG": "กิโลกรัม", "PK": "แพ็ค", "แก": "แกลลอน", "BT": "ขวด", "กล": "กล่อง", "กส": "กระสอบ",
            "ถง": "ถุง", "ชน": "ชิ้น", "อน": "ชิ้น", "ใบ": "ใบ", "ชด": "ชุด", "ครั้ง": "ครั้ง"}
SALE_TH = {"box": "กล่อง", "bag": "ถุง", "sack": "กระสอบ", "gal.": "แกลลอน", "gal": "แกลลอน", "bt": "ขวด",
           "pk": "แพ็ค", "kg": "กิโลกรัม", "pcs.": "ชิ้น", "pcs": "ชิ้น", "set": "ชุด", "ใบ": "ใบ"}
NOT_GOODS = ("41-", "42-")             # delivery fees, other income: on the report, not things to stock
NON_FOOD = ("14-", "16-", "17-")       # tableware, uniforms, stationery: ordered, never in a recipe
PREPARED = ("CK-", "HK-", "DK-")       # made in a kitchen from other items (sub-recipes)
REF = "CENTRAL-{month}"


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def ensure(con):
    con.execute(DDL)
    con.execute(LINK_DDL)


# ── reading the report ───────────────────────────────────────────────────────
def _month_of(title: str):
    t = str(title or "")
    for k, m in sorted(THAI_MONTHS.items(), key=lambda kv: -len(kv[0])):
        if k in t:
            y = re.search(r"(25\d\d|20\d\d)", t)
            if y:
                yy = int(y.group(1))
                return f"{yy - 543 if yy > 2400 else yy:04d}-{m:02d}"
    m = re.search(r"(20\d\d)-(\d\d)", t)
    return f"{m.group(1)}-{m.group(2)}" if m else None


def parse_label(label) -> tuple:
    """'กล10.00/PK' -> ('กล', 10.0, 'PK'); 'ครั้ง' -> ('ครั้ง', 1.0, 'ครั้ง')."""
    s = str(label or "").strip()
    m = re.match(r"^\s*(\D+?)\s*([\d.,]+)\s*/\s*(.+?)\s*$", s)
    if not m:
        return (s or None), 1.0, (s or None)
    return m.group(1).strip(), float(m.group(2).replace(",", "")), m.group(3).strip()


def parse_qty(v, inner_qty: float):
    """A report cell -> sale units (None = no delivery). '2/39.00' = 2 sale units + 39 inner units."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v) if v else None
    s = str(v).strip()
    if not s or s.upper().startswith("#N/A") or s in ("-", "0"):
        return None
    m = re.match(r"^([\d.,]+)\s*/\s*([\d.,]+)$", s)
    if m:
        whole, part = float(m.group(1).replace(",", "")), float(m.group(2).replace(",", ""))
        return whole + (part / inner_qty if inner_qty else 0.0)
    try:
        f = float(s.replace(",", ""))
        return f or None
    except ValueError:
        return None


def _match_branch(con, label: str):
    lab = re.sub(r"\s+", "", str(label or ""))
    if not lab:
        return None
    hits = [(bid, n) for bid, n in con.execute("SELECT branch_id, name FROM branches WHERE COALESCE(active,1)=1")
            if re.sub(r"\s+", "", n).startswith(lab) or lab.startswith(re.sub(r"\s+", "", n))]
    return hits[0] if len(hits) == 1 else None


def parse_report(con, data: bytes, month: str = None) -> dict:
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    ws = wb.worksheets[0]
    grid = [list(r) for r in ws.iter_rows(values_only=True)]
    hi = next((i for i, r in enumerate(grid[:15]) if any(str(c or "").strip() == "รหัสสินค้า" for c in r)), None)
    if hi is None:
        raise ValueError("ไม่พบหัวตาราง “รหัสสินค้า” — ใช้รายงานยอดสั่งสินค้าทุกสาขาของส่วนกลาง")
    head = [str(c or "").strip() for c in grid[hi]]

    def col(*names):
        for n in names:
            if n in head:
                return head.index(n)
        return None
    c_code, c_name, c_unit = col("รหัสสินค้า"), col("รายละเอียด", "ชื่อสินค้า"), col("หน่วยนับ")
    c_price, c_sale, c_factor = col("ราคาขาย"), col("หน่วยขาย"), col("1 หน่วยขาย = หน่วยฐาน")
    if None in (c_code, c_unit, c_sale):
        raise ValueError("ต้องมีคอลัมน์ รหัสสินค้า · หน่วยนับ · หน่วยขาย")
    sub = grid[hi + 1] if hi + 1 < len(grid) else []
    branches = []
    for j in range(c_unit + 1, len(head)):
        h = head[j]
        if not re.match(r"^[A-Za-z]{0,3}\d{2,6}$", h):     # a branch column is headed by its code (C1001)
            if branches:
                break
            continue
        label = str(sub[j] or "").strip() if j < len(sub) else ""
        hit = _match_branch(con, label) if label else None
        branches.append({"col": j, "code": h, "label": label or h,
                         "branch_id": hit[0] if hit else None, "branch": hit[1] if hit else None})
    items = []
    seen = set()
    for r in grid[hi + 1:]:
        code = str(r[c_code] or "").strip() if c_code < len(r) else ""
        if not re.match(r"^[0-9A-Z]{1,3}-[\w-]+$", code) or code in seen:
            continue
        seen.add(code)
        outer, n, inner = parse_label(r[c_unit])
        price = r[c_price] if c_price is not None else None
        try:
            price = float(price) if price not in (None, "", "#N/A") else None
        except (TypeError, ValueError):
            price = None
        fac = None
        if c_factor is not None and c_factor < len(r):
            try:
                fac = float(r[c_factor]) if r[c_factor] not in (None, "") else None
            except (TypeError, ValueError):
                fac = None
        items.append({"code": code, "name": str(r[c_name] or "").strip() if c_name is not None else "",
                      "unit_label": str(r[c_unit] or "").strip(), "outer": outer, "inner_qty": n, "inner": inner,
                      "sale_unit": str(r[c_sale] or "").strip() or outer, "price_per_sale": price or None,
                      "factor_given": fac,
                      "qty": {b["code"]: parse_qty(r[b["col"]], n) for b in branches if b["col"] < len(r)}})
    return {"sheet": ws.title, "month": month or _month_of(ws.title), "branches": branches, "items": items}


# ── sale unit -> base units ──────────────────────────────────────────────────
def sale_th(it) -> str:
    return SALE_TH.get(str(it["sale_unit"] or "").lower(), it["sale_unit"] or "")


def recv_label(it) -> str:
    """What the master shows as the order unit: 'กล่อง (10 แพ็ค)', 'กิโลกรัม'."""
    s = sale_th(it)
    inner = INNER_TH.get(it["inner"] or "", it["inner"] or "")
    if it["inner_qty"] and (it["inner_qty"] != 1 or norm(inner) != norm(s)):
        return f"{s} ({it['inner_qty']:g} {inner})"
    return s


# "1KG/PK", "500G/PK", "(1Kg./Pk.)", "3L/Bot", "5LT/GAL", "(10KG/BOX)" in an item's name
_HINT = re.compile(r"(\d+(?:\.\d+)?)\s*(kg|g|gr|l|lt|lit|ml)\.?\s*/\s*(pk|pack|bot|btl|bt|gal|gallon|bag|box|ctn|pcs)\b",
                   re.IGNORECASE)
_HINT_AMT = {"kg": ("กรัม", 1000.0), "g": ("กรัม", 1.0), "gr": ("กรัม", 1.0),
             "l": ("มิลลิลิตร", 1000.0), "lt": ("มิลลิลิตร", 1000.0), "lit": ("มิลลิลิตร", 1000.0),
             "ml": ("มิลลิลิตร", 1.0)}
_HINT_PER = {"pk": "PK", "pack": "PK", "bot": "BT", "btl": "BT", "bt": "BT", "gal": "แก", "gallon": "แก",
             "bag": "ถง", "box": "กล", "ctn": "กล", "pcs": "ชน"}


def name_hint(name: str, base: str) -> dict:
    """{inner abbreviation: base units in one} read from the name, in the item's base unit only."""
    out = {}
    for amt, u, per in _HINT.findall(str(name or "")):
        bu, mult = _HINT_AMT[u.lower()]
        if norm(base) == bu:
            out.setdefault(_HINT_PER[per.lower()], float(amt) * mult)
    return out


def _factor(conv, master: dict, it) -> tuple:
    """(base per sale unit or None, how, base unit to set when the master has none).

    Order: a factor written in the file > the inner unit by an exact route (base /
    unit rule / kg->g) > a pack size written in the name > the master's own pack."""
    if it.get("factor_given"):
        return float(it["factor_given"]), "ใส่เองในไฟล์", None
    n = it["inner_qty"] or 1.0
    inner = INNER_TH.get(it["inner"] or "", it["inner"] or "")
    m = master.get(it["code"])
    base, recv, per = m if m else (None, None, None)
    if not base:                                          # a new code, or one the master has no base for
        if norm(inner) == "กิโลกรัม":
            return n * 1000.0, f"{n:g} กก. = {n * 1000:g} กรัม (ตั้งหน่วยฐานเป็นกรัม)", "กรัม"
        hint = name_hint(it["name"], "กรัม")
        if hint.get(it["inner"]):
            f = n * hint[it["inner"]]
            return f, f"{n:g} × {inner} {hint[it['inner']]:g} กรัม (จากชื่อสินค้า — ตั้งหน่วยฐานเป็นกรัม)", "กรัม"
        if m is not None and per:
            return None, "ทะเบียนยังไม่ระบุหน่วยฐาน", None
        return n, f"{n:g} {inner} (ตั้ง{inner}เป็นหน่วยฐาน)", inner or None
    u = norm(inner)
    # the inner unit through the exact routes only — a generic pack word must NOT
    # fall back to the master's pack, which for a boxed item is the whole box
    if u == norm(base):
        return n, f"{n:g} {base}", None
    rule = conv.rules.get((it["code"], u))
    if rule:
        return n * rule, f"{n:g} × {inner} ({rule:g} {base})", None
    from .units import _METRIC
    if (u, norm(base)) in _METRIC:
        f = _METRIC[(u, norm(base))]
        return n * f, f"{n:g} {inner} = {n * f:g} {base}", None
    hint = name_hint(it["name"], base)
    if hint.get(it["inner"]):
        f = n * hint[it["inner"]]
        return f, f"{n:g} × {inner} {hint[it['inner']]:g} {base} (จากชื่อสินค้า)", None
    if hint.get("กล") and it["outer"] == "กล":
        return hint["กล"], f"กล่องละ {hint['กล']:g} {base} (จากชื่อสินค้า)", None
    if per:                                               # the master's own pack, which a person should glance at
        warn = f" — ตรวจ: 1 {sale_th(it)} มี {n:g} {inner}" if n != 1 else ""
        return float(per), f"จากทะเบียน: 1 {recv or 'แพ็ค'} = {per:g} {base}{warn}", None
    return None, f"ยังไม่รู้ว่า 1 {sale_th(it)} = กี่ {base}", None


def plan_catalogue(con, parsed: dict) -> dict:
    conv = Converter(con)
    master = {c: (b, r, p) for c, b, r, p in con.execute(
        "SELECT code, base_unit, recv_unit, qty_per_recv FROM bc_ingredient")}
    names = dict(con.execute("SELECT code, name_th FROM bc_ingredient"))
    rows = []
    for it in parsed["items"]:
        if it["code"].startswith(NOT_GOODS):
            rows.append({**_brief(it), "status": "skip", "factor_how": "ไม่ใช่สินค้า (ค่าส่ง/รายได้อื่น)"})
            continue
        f, how, new_base = _factor(conv, master, it)
        m = master.get(it["code"])
        row = {**_brief(it), "is_new": m is None, "master_name": names.get(it["code"]),
               "base_unit": (m[0] if m and m[0] else new_base), "set_base": bool(new_base),
               "base_per_sale": f, "factor_how": how,
               "recv_label": recv_label(it), "old_recv": m[1] if m else None, "old_per": m[2] if m else None,
               "kind": kind_of(it["code"], it["name"]) if m is None else None,
               "price_per_base": round(it["price_per_sale"] / f, 4) if f and it["price_per_sale"] else None,
               "status": "ok" if f else "needs_factor"}
        if m and m[2] and f and abs(m[2] - f) > 0.005 * max(m[2], f):
            row["pack_change"] = True
        rows.append(row)
    goods = [r for r in rows if r["status"] != "skip"]
    in_list = {r["code"] for r in goods}
    local = [c for c, in con.execute("SELECT code FROM bc_ingredient WHERE COALESCE(active,1)=1")
             if c not in in_list]
    return {"rows": rows,
            "counts": {"items": len(goods), "new": sum(1 for r in goods if r["is_new"]),
                       "needs_factor": sum(1 for r in goods if r["status"] == "needs_factor"),
                       "pack_change": sum(1 for r in goods if r.get("pack_change")),
                       "from_master": sum(1 for r in goods if (r["factor_how"] or "").startswith("จากทะเบียน")),
                       "no_price": sum(1 for r in goods if not r["price_per_sale"]),
                       "to_local": len(local), "skipped": len(rows) - len(goods)}}


def _brief(it) -> dict:
    return {k: it[k] for k in ("code", "name", "unit_label", "sale_unit", "inner_qty", "inner", "price_per_sale")}


def apply_catalogue(con, parsed: dict, who: str, set_local_others: bool = True) -> dict:
    """Write the list: the central items, their order unit in the master, and sourcing for every code."""
    ensure(con)
    pl = plan_catalogue(con, parsed)
    stats = {"inserted": 0, "updated": 0, "unchanged": 0}
    month = parsed.get("month")
    in_list = set()
    for r in pl["rows"]:
        if r["status"] == "skip":
            continue
        in_list.add(r["code"])
        f = r["base_per_sale"]
        vals = {"sourcing_default": "central", "active": 1}
        if r["is_new"]:
            vals.update(name_th=r["name"], kind="consumable" if r["code"].startswith(NON_FOOD) else r["kind"])
        if r["set_base"]:
            vals["base_unit"] = r["base_unit"]
        if f:
            vals.update(recv_unit=r["recv_label"], qty_per_recv=f)
        _upsert(con, r["code"], vals, who, True, stats)
        # a box of N packs: one pack is box / N, so "แพ็ค" in a count or a receipt is right too
        if f and (r["inner"] or "") == "PK" and (r["inner_qty"] or 1) > 1:
            if not con.execute("SELECT 1 FROM bc_unit_rule WHERE ingredient_code=? AND unit='แพ็ค'",
                               (r["code"],)).fetchone():
                con.execute("INSERT INTO bc_unit_rule(ingredient_code, unit, factor_to_base, label) VALUES(?,?,?,?)",
                            (r["code"], "แพ็ค", f / r["inner_qty"], f"1/{r['inner_qty']:g} ของ {r['recv_label']} (รายการส่วนกลาง)"))
        con.execute("INSERT INTO bc_central_item(code, name, unit_label, sale_unit, inner_qty, inner_unit, base_per_sale,"
                    " factor_how, price_per_sale, price_month, active, source_ref, updated_at, updated_by)"
                    " VALUES(?,?,?,?,?,?,?,?,?,?,1,?,?,?) ON CONFLICT(code) DO UPDATE SET name=excluded.name,"
                    " unit_label=excluded.unit_label, sale_unit=excluded.sale_unit, inner_qty=excluded.inner_qty,"
                    " inner_unit=excluded.inner_unit, base_per_sale=excluded.base_per_sale, factor_how=excluded.factor_how,"
                    " price_per_sale=COALESCE(excluded.price_per_sale, bc_central_item.price_per_sale),"
                    " price_month=CASE WHEN excluded.price_per_sale IS NULL THEN bc_central_item.price_month"
                    " ELSE excluded.price_month END, active=1, source_ref=excluded.source_ref,"
                    " updated_at=excluded.updated_at, updated_by=excluded.updated_by",
                    (r["code"], r["name"], r["unit_label"], r["sale_unit"], r["inner_qty"], r["inner"], f,
                     r["factor_how"], r["price_per_sale"], month, parsed.get("sheet"), now(), who))
    off = con.execute(f"UPDATE bc_central_item SET active=0, updated_at=?, updated_by=? WHERE active=1 AND code NOT IN"
                      f" ({','.join('?' * len(in_list)) or 'NULL'})", [now(), who] + sorted(in_list)).rowcount
    to_local = 0
    if set_local_others and in_list:
        to_local = con.execute(f"UPDATE bc_ingredient SET sourcing_default='local', updated_at=?, updated_by=?"
                               f" WHERE sourcing_default<>'local' AND code NOT IN ({','.join('?' * len(in_list))})",
                               [now(), who] + sorted(in_list)).rowcount
    audit(con, who, "central_list", "bc_central_item", month or "", None,
          {"items": len(in_list), "new": stats["inserted"], "to_local": to_local, "dropped": off})
    return {**pl["counts"], "saved": len(in_list), "inserted": stats["inserted"], "set_local": to_local,
            "dropped": off, "month": month}


def items(con) -> dict:
    ensure(con)
    rows = [dict(zip(("code", "name", "unit_label", "sale_unit", "inner_qty", "inner_unit", "base_per_sale",
                      "factor_how", "price_per_sale", "price_month", "base_unit", "recv_unit", "sourcing"), r))
            for r in con.execute(
                "SELECT c.code, COALESCE(i.name_th, c.name), c.unit_label, c.sale_unit, c.inner_qty, c.inner_unit,"
                " c.base_per_sale, c.factor_how, c.price_per_sale, c.price_month, i.base_unit, i.recv_unit,"
                " i.sourcing_default FROM bc_central_item c LEFT JOIN bc_ingredient i USING(code)"
                " WHERE c.active=1 ORDER BY c.code")]
    return {"items": rows, "count": len(rows),
            "needs_factor": sum(1 for r in rows if not r["base_per_sale"]),
            "local": con.execute("SELECT COUNT(*) FROM bc_ingredient WHERE sourcing_default='local'"
                                 " AND COALESCE(active,1)=1").fetchone()[0]}


def central_map(con) -> dict:
    """code -> (base_per_sale, price_per_sale) for the active list ({} before the first import)."""
    if not con.execute("SELECT 1 FROM sqlite_master WHERE name='bc_central_item'").fetchone():
        return {}
    return {c: (f, p) for c, f, p in con.execute(
        "SELECT code, base_per_sale, price_per_sale FROM bc_central_item WHERE active=1")}


# ── a month's deliveries -> goods received ───────────────────────────────────
def _month_end(month: str) -> str:
    y, m = int(month[:4]), int(month[5:7])
    nxt = date(y + (m == 12), 1 if m == 12 else m + 1, 1)
    return (nxt - timedelta(days=1)).isoformat()


def deliveries(con, parsed: dict, who: str, branch_ids=None, dry_run: bool = True,
               receipt_date: str = None) -> dict:
    """One goods-received note per branch for the month (replacing an earlier import of the same month)."""
    from .variance import _closed_through, add_receipt
    month = parsed.get("month")
    if not month:
        raise ValueError("ไม่รู้ว่าเป็นเดือนไหน — ใส่เดือน (YYYY-MM)")
    rdate = receipt_date or _month_end(month)
    if rdate[:7] != month:
        raise ValueError(f"วันที่รับของ {rdate} ไม่อยู่ในเดือน {month}")
    conv = Converter(con)
    pl = {r["code"]: r for r in plan_catalogue(con, parsed)["rows"]}
    want = {int(b) for b in branch_ids} if branch_ids else None
    out = []
    for b in parsed["branches"]:
        if want is not None and b["branch_id"] not in want:
            continue
        res = {"code": b["code"], "label": b["label"], "branch_id": b["branch_id"], "branch": b["branch"],
               "lines": 0, "value_central": 0.0, "skipped": [], "replaced": 0}
        if not b["branch_id"]:
            res["error"] = "จับคู่สาขาไม่ได้"
            out.append(res)
            continue
        lines = []
        for it in parsed["items"]:
            q = it["qty"].get(b["code"])
            p = pl.get(it["code"])
            if not q or q <= 0 or not p or p["status"] == "skip":
                continue
            f = p["base_per_sale"]
            if not f:
                res["skipped"].append({"code": it["code"], "name": it["name"], "qty": q, "why": p["factor_how"]})
                continue
            lines.append({"code": it["code"], "qty": round(q, 4), "unit": p["recv_label"], "qty_base": q * f,
                          "price_per_sale": it["price_per_sale"]})
            res["value_central"] += q * (it["price_per_sale"] or 0.0)
        res["lines"] = len(lines)
        res["value_central"] = round(res["value_central"], 2)
        ref = REF.format(month=month)
        old = [rid for rid, in con.execute("SELECT receipt_id FROM bc_receipt WHERE branch_id=? AND ref=?",
                                          (b["branch_id"], ref))]
        closed = _closed_through(con, b["branch_id"])
        if old and closed and rdate < closed:
            res["error"] = f"เดือนนี้อยู่ในรอบที่นับปิดแล้ว ({closed}) — แทนที่ไม่ได้"
        res["replaced"] = len(old)
        if not dry_run and lines and not res.get("error"):
            for rid in old:
                con.execute("DELETE FROM bc_receipt_line WHERE receipt_id=?", (rid,))
                con.execute("DELETE FROM bc_receipt WHERE receipt_id=?", (rid,))
            # the order unit must be in the master before the receipt converts it
            for ln in lines:
                if conv.to_base(ln["code"], 1, ln["unit"])[0] is None:
                    raise ValueError(f"{ln['code']}: นำเข้ารายการส่วนกลางก่อน (หน่วย {ln['unit']} ยังไม่อยู่ในทะเบียน)")
            r = add_receipt(con, b["branch_id"], rdate, [{"code": ln["code"], "qty": ln["qty"], "unit": ln["unit"]}
                                                         for ln in lines], who, ref=ref,
                            note=f"ยอดส่งจากส่วนกลาง {month} ({parsed.get('sheet') or ''})", source="central")
            res["receipt_id"] = r["receipt_id"]
        out.append(res)
    return {"month": month, "receipt_date": rdate, "dry_run": dry_run, "branches": out}


def unit_price(con, code: str, month: str, cmap: dict = None):
    """What a branch pays per base unit: the centre's price when the item is on the
    list, else the month's ingredient price. -> (price_per_base or None, source)."""
    cmap = central_map(con) if cmap is None else cmap
    f, p = cmap.get(code, (None, None))
    if f and p:
        return p / f, "central"
    from .prices import price_for
    pr = price_for(con, code, month)
    return (pr[0], pr[1]) if pr else (None, None)


# ── recipe code -> the code the centre sends ─────────────────────────────────
def links(con) -> dict:
    """{old_code: new_code} ({} before the table exists)."""
    if not con.execute("SELECT 1 FROM sqlite_master WHERE name='bc_code_link'").fetchone():
        return {}
    return dict(con.execute("SELECT old_code, new_code FROM bc_code_link"))


def _recipe_codes(con) -> set:
    """Every ingredient code a recipe names: released/draft buffet lines, sub-recipes, the legacy BoM."""
    out = set()
    for sql in ("SELECT DISTINCT ingredient_code FROM bc_recipe_line", "SELECT DISTINCT ingredient_code FROM bc_prep_line",
                "SELECT DISTINCT ingredient_code FROM bom_ingredients"):
        try:
            out |= {r[0] for r in con.execute(sql) if r[0]}
        except Exception:
            pass
    return out


def link_view(con) -> dict:
    """The links kept, and suggestions: a code the recipes name that is NOT on the central list,
    paired by name with a central item (the same product under the code the centre sends now)."""
    from .codes import _core, _score, key
    ensure(con)
    names = dict(con.execute("SELECT code, name_th FROM bc_ingredient"))
    cmap = central_map(con)
    kept = links(con)
    used = _recipe_codes(con)
    targets = [c for c in cmap if not c.startswith(NON_FOOD)]
    # a prepared item (CK-/HK-/DK-) is MADE from what the centre sends — a sub-recipe, not the same product
    olds = [c for c in used if c not in cmap and c not in kept and c in names and not c.startswith(PREPARED)]

    def score(a: str, b: str) -> float:
        """Both ways, the weaker: 'ซอสถั่วเหลือง' and 'น้ำมันถั่วเหลือง' share a core word but are not the
        same product; a key of 1-4 letters ('เนื้อ') says nothing by being contained."""
        ka, kb = key(a), key(b)
        if min(len(_core(ka)), len(_core(kb))) < 4:
            from difflib import SequenceMatcher
            return SequenceMatcher(None, a.lower(), b.lower(), autojunk=False).ratio()
        return min(_score(ka, b), _score(kb, a))
    # the centre's own name for an item, as well as the master's (they differ: 03-03-164 is
    # "เนื้อสไลด์" in the master but "เนื้อวัวสันใน Beef (5KG/BAG)" on the centre's report)
    cname = dict(con.execute("SELECT code, name FROM bc_central_item"))
    tnames = {c: {n for n in (names.get(c), cname.get(c)) if n} for c in targets}
    sugg = []
    for o in olds:
        on = names.get(o) or ""
        opts = sorted(((max(score(n, on) for n in tnames[c]), c) for c in targets if tnames[c]), reverse=True)[:2]
        opts = [(s, c) for s, c in opts if s >= 0.65]
        if opts:
            sugg.append({"old_code": o, "old_name": on, "options": [
                {"new_code": c, "new_name": cname.get(c) or names.get(c), "score": round(s, 2)} for s, c in opts]})
    sugg.sort(key=lambda x: (-x["options"][0]["score"], x["old_code"]))
    rows = [dict(zip(("old_code", "new_code", "note", "who", "at"), r)) for r in
            con.execute("SELECT old_code, new_code, note, who, at FROM bc_code_link ORDER BY old_code")]
    for r in rows:
        r["old_name"], r["new_name"] = names.get(r["old_code"]), cname.get(r["new_code"]) or names.get(r["new_code"])
    covered = used | set(kept.values()) | {s["options"][0]["new_code"] for s in sugg}
    return {"links": rows, "suggestions": sugg,
            "orphans": [{"code": c, "name": cname.get(c) or names.get(c)} for c in targets if c not in covered]}


def set_link(con, old_code: str, new_code, who: str, note: str = None):
    """Count what the recipes call `old_code` as `new_code` (None removes the link)."""
    from .master import audit as _audit
    ensure(con)
    old_code = (old_code or "").strip()
    known = {c for c, in con.execute("SELECT code FROM bc_ingredient")}
    if old_code not in known:
        raise ValueError(f"ไม่พบรหัส {old_code}")
    if not new_code:
        con.execute("DELETE FROM bc_code_link WHERE old_code=?", (old_code,))
        _audit(con, who, "code_link_delete", "bc_code_link", old_code)
        return
    new_code = new_code.strip()
    if new_code not in known:
        raise ValueError(f"ไม่พบรหัส {new_code}")
    if new_code == old_code:
        raise ValueError("รหัสเดิมกับรหัสใหม่เป็นรหัสเดียวกัน")
    if con.execute("SELECT 1 FROM bc_code_link WHERE old_code=?", (new_code,)).fetchone():
        raise ValueError(f"{new_code} ถูกโยงไปรหัสอื่นอยู่แล้ว — โยงต่อกันเป็นทอดไม่ได้")
    con.execute("INSERT INTO bc_code_link(old_code, new_code, note, who, at) VALUES(?,?,?,?,?)"
                " ON CONFLICT(old_code) DO UPDATE SET new_code=excluded.new_code, note=excluded.note,"
                " who=excluded.who, at=excluded.at", (old_code, new_code, note, who, now()))
    _audit(con, who, "code_link", "bc_code_link", old_code, None, new_code)
