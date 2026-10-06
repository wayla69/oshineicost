# -*- coding: utf-8 -*-
"""Phase 2 · Sprint S6 — branch stock counts (docs/BOM_CENTER_PLAN.md §7.1-1/3, §7.2-2).

A branch counts what is on its shelves, zone by zone (แช่แข็ง / แช่เย็น / ของแห้ง /
บาร์น้ำ), in the unit the kitchen handles (packs, bottles …) — the system converts
to the base unit the recipes use. The very first count is the BEGIN stock; after
that every submitted count is the END of one period and, automatically, the BEGIN
of the next (no one types the begin stock twice).

What a branch counts: the monitored items (ใบคลังบังคับสั่ง) plus every ingredient
its sales actually used over the last four weeks — read through the same recipe
model the forecast uses, so prepared items with a sub-recipe appear as the raw
ingredients inside them. A-class (by ฿ used) and monitored items are "required".

A count is saved as a draft (the phone may lose signal half-way) and submitted
once. Submitted counts are never edited; a recount is a new count.
"""
from datetime import date, datetime, timedelta, timezone

from .clock import today as bkk_today
from .master import audit
from .units import Converter

ZONES = (("frozen", "แช่แข็ง"), ("chilled", "แช่เย็น"), ("dry", "ของแห้ง"), ("bar", "บาร์น้ำ"), ("other", "อื่น ๆ"))
ZONE_TH = dict(ZONES)
LOOKBACK_DAYS = 28


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def guess_zone(code: str, name: str, family: str, kind: str) -> str:
    """A first guess until the centre sets bc_ingredient.storage_zone."""
    n = (name or "").lower()
    c = code or ""
    if "แช่แข็ง" in n or "frozen" in n:
        return "frozen"
    if (family or "") == "DK" or c.startswith(("DK", "BV")):
        return "bar"
    # the accounting code family says more than the name (DATAP, real data 2026-10)
    if c.startswith(("03-", "13-01", "13-02", "13-03")):        # meat / seafood / processed — kept frozen
        return "frozen"
    if c.startswith(("02-", "13-04", "15-", "CK-", "HK-", "F-", "V-", "VY", "FV")) \
            or (family or "") in ("V", "02", "VY") or any(w in n for w in ("สด", "fresh", "ผัก", "ไข่")):
        return "chilled"                                          # fresh fish, cheese, kitchen sauces, prepared, veg
    if c.startswith(("01-", "10-", "11-", "S-")) or (family or "") in ("S", "11", "01") or kind == "consumable":
        return "dry"
    return "other"


def count_unit(base: str, recv: str, per) -> tuple:
    """(unit the kitchen counts in, base units in one of it). Packs when the pack size is known."""
    if per and recv:
        return recv, float(per)
    return base or "", 1.0


def _branch_name(con, branch_id: int) -> str:
    r = con.execute("SELECT name FROM branches WHERE branch_id=?", (branch_id,)).fetchone()
    if not r:
        raise ValueError("ไม่พบสาขา")
    return r[0]


def last_submitted(con, branch_id: int):
    r = con.execute("SELECT count_id, count_date, kind, submitted_at, counted_by FROM bc_stock_count"
                    " WHERE branch_id=? AND COALESCE(status,'submitted')='submitted'"
                    " ORDER BY count_date DESC, count_id DESC LIMIT 1", (branch_id,)).fetchone()
    return dict(zip(("count_id", "count_date", "kind", "submitted_at", "counted_by"), r)) if r else None


def draft_of(con, branch_id: int):
    r = con.execute("SELECT count_id, count_date, kind, updated_at, counted_by FROM bc_stock_count"
                    " WHERE branch_id=? AND status='draft' ORDER BY count_id DESC LIMIT 1", (branch_id,)).fetchone()
    return dict(zip(("count_id", "count_date", "kind", "updated_at", "counted_by"), r)) if r else None


def _usage(con, branch_id: int, as_of: date) -> dict:
    """{code: average base units a trading day} over the lookback window."""
    from .forecast import era_start, usage_history
    start = era_start(as_of - timedelta(days=LOOKBACK_DAYS - 1), as_of)
    use, days, _ = usage_history(con, branch_id, start, as_of, f"{as_of.year:04d}-{as_of.month:02d}")
    n = max(len(days), 1)
    return {c: sum(s.values()) / n for c, s in use.items() if sum(s.values()) > 0}


def sheet(con, branch_id: int, as_of: date = None) -> dict:
    """The list a branch counts, grouped by zone, with the last count shown faintly."""
    from .forecast import _abc
    from .prices import price_for
    bname = _branch_name(con, branch_id)
    last = con.execute("SELECT MAX(day) FROM sales_sku_daily WHERE branch_id=?", (branch_id,)).fetchone()[0]
    as_of = as_of or (date.fromisoformat(last) if last else bkk_today())
    month = f"{as_of.year:04d}-{as_of.month:02d}"
    per_day = _usage(con, branch_id, as_of)
    spend = {}
    for c, q in per_day.items():
        p = price_for(con, c, month)
        spend[c] = q * (p[0] if p else 0.0)
    klass = _abc(spend)
    master = {r[0]: r[1:] for r in con.execute(
        "SELECT code, name_th, family, kind, base_unit, recv_unit, qty_per_recv, monitored, storage_zone, count_unit,"
        " active FROM bc_ingredient")}
    from .prep import book
    has_sub = set(book(con))
    codes = {c for c, m in master.items() if m[6] and m[9] and c not in has_sub} | set(per_day)
    # S11: everything the centre supplies sits on a shelf too (tableware and uniforms aside)
    from .central import NON_FOOD, central_map
    codes |= {c for c in central_map(con) if c in master and not c.startswith(NON_FOOD)}
    prev = {}
    lc = last_submitted(con, branch_id)
    if lc:
        prev = {c: (q, u, qb) for c, q, u, qb in con.execute(
            "SELECT ingredient_code, qty_kitchen, unit, qty_base FROM bc_stock_count_line WHERE count_id=?",
            (lc["count_id"],))}
    dr = draft_of(con, branch_id)
    draft = {}
    if dr:
        draft = {c: (q, u) for c, q, u in con.execute(
            "SELECT ingredient_code, qty_kitchen, unit FROM bc_stock_count_line WHERE count_id=?", (dr["count_id"],))}
    zones = {z: [] for z, _ in ZONES}
    conv = Converter(con)
    for c in sorted(codes):
        m = master.get(c)
        if not m:
            continue
        name, fam, kind, base, recv, per, mon, zone, cu, _active = m
        unit, factor = (cu, conv.factor(c, cu)[0]) if cu else count_unit(base, recv, per)
        z = zone or guess_zone(c, name, fam, kind)
        k = klass.get(c, "C")
        pv = prev.get(c)
        zones.setdefault(z, []).append({
            "code": c, "name": name or c, "base_unit": base, "count_unit": unit, "per_count_unit": factor,
            "zone_guessed": not zone, "class": k, "required": bool(mon) or k == "A", "monitored": bool(mon),
            "use_per_day": round(per_day.get(c, 0.0), 3),
            "last_qty": pv[0] if pv else None, "last_unit": pv[1] if pv else None, "last_base": pv[2] if pv else None,
            "draft_qty": draft.get(c, (None,))[0]})
    order = {"A": 0, "B": 1, "C": 2}
    out_z = []
    for z, label in ZONES:
        items = sorted(zones.get(z, []), key=lambda r: (not r["required"], order[r["class"]], r["name"]))
        if items:
            out_z.append({"zone": z, "label": label, "items": items})
    return {"branch_id": branch_id, "branch": bname, "as_of": as_of.isoformat(),
            "next_kind": "end" if lc else "begin", "last_count": lc, "draft": dr, "zones": out_z,
            "items": sum(len(z["items"]) for z in out_z),
            "required": sum(1 for z in out_z for r in z["items"] if r["required"])}


def save(con, branch_id: int, lines: list, who: str, submit: bool = False, count_date: str = None,
         note: str = None) -> dict:
    """Write the branch's draft (creating it) and optionally submit it."""
    _branch_name(con, branch_id)
    conv = Converter(con)
    dr = draft_of(con, branch_id)
    kind = "end" if last_submitted(con, branch_id) else "begin"
    cdate = count_date or bkk_today().isoformat()
    try:
        date.fromisoformat(cdate)
    except ValueError:
        raise ValueError("วันที่นับต้องเป็น YYYY-MM-DD")
    if dr:
        cid = dr["count_id"]
        con.execute("UPDATE bc_stock_count SET updated_at=?, counted_by=?, kind=?, count_date=?,"
                    " note=COALESCE(?, note) WHERE count_id=?", (now(), who, kind, cdate, note, cid))
    else:
        cur = con.execute("INSERT INTO bc_stock_count(branch_id, count_date, kind, counted_by, note, created_at,"
                          " status, updated_at) VALUES(?,?,?,?,?,?, 'draft', ?)",
                          (branch_id, cdate, kind, who, note, now(), now()))
        cid = cur.lastrowid
    bad = []
    for ln in lines or []:
        code = (ln.get("code") or ln.get("ingredient_code") or "").strip()
        if not code:
            continue
        q = ln.get("qty")
        if q is None or q == "":
            con.execute("DELETE FROM bc_stock_count_line WHERE count_id=? AND ingredient_code=?", (cid, code))
            continue
        try:
            q = float(q)
        except (TypeError, ValueError):
            raise ValueError(f"{code}: จำนวนต้องเป็นตัวเลข")
        if q < 0:
            raise ValueError(f"{code}: จำนวนติดลบไม่ได้")
        unit = (ln.get("unit") or "").strip() or conv.base_unit(code)
        qb, how = conv.to_base(code, q, unit)
        if qb is None:
            bad.append(f"{code}: {how}")
        con.execute("INSERT INTO bc_stock_count_line(count_id, ingredient_code, qty_kitchen, unit, qty_base, note)"
                    " VALUES(?,?,?,?,?,?) ON CONFLICT(count_id, ingredient_code) DO UPDATE SET"
                    " qty_kitchen=excluded.qty_kitchen, unit=excluded.unit, qty_base=excluded.qty_base,"
                    " note=excluded.note", (cid, code, q, unit, qb, (ln.get("note") or None)))
    n = con.execute("SELECT COUNT(*) FROM bc_stock_count_line WHERE count_id=?", (cid,)).fetchone()[0]
    out = {"count_id": cid, "kind": kind, "lines": n, "status": "draft", "unconverted": bad}
    if submit:
        if not n:
            raise ValueError("ยังไม่ได้นับรายการใดเลย")
        # every line of the draft, not only the ones sent now, must have reached the base unit
        bad = [f"{c}: แปลง {u} ไม่ได้" for c, u in con.execute(
            "SELECT ingredient_code, unit FROM bc_stock_count_line WHERE count_id=? AND qty_base IS NULL", (cid,))]
        if bad:
            raise ValueError("แปลงหน่วยไม่ได้: " + "; ".join(bad[:3]) + " — แจ้งส่วนกลางเพิ่มกฎหน่วย")
        con.execute("UPDATE bc_stock_count SET status='submitted', submitted_at=? WHERE count_id=?", (now(), cid))
        audit(con, who, "stock_submit", "bc_stock_count", str(cid), None,
              {"branch_id": branch_id, "kind": kind, "date": cdate, "lines": n})
        out["status"] = "submitted"
    return out


def history(con, branch_id: int, limit: int = 20) -> list:
    rows = con.execute(
        "SELECT c.count_id, c.count_date, c.kind, c.status, c.counted_by, c.submitted_at, COUNT(l.ingredient_code)"
        " FROM bc_stock_count c LEFT JOIN bc_stock_count_line l USING(count_id) WHERE c.branch_id=?"
        " GROUP BY c.count_id ORDER BY c.count_date DESC, c.count_id DESC LIMIT ?", (branch_id, limit)).fetchall()
    return [dict(zip(("count_id", "count_date", "kind", "status", "counted_by", "submitted_at", "lines"), r))
            for r in rows]


def detail(con, count_id: int) -> dict:
    r = con.execute("SELECT count_id, branch_id, count_date, kind, status, counted_by, submitted_at, note"
                    " FROM bc_stock_count WHERE count_id=?", (count_id,)).fetchone()
    if not r:
        raise ValueError("ไม่พบการนับนี้")
    out = dict(zip(("count_id", "branch_id", "count_date", "kind", "status", "counted_by", "submitted_at", "note"), r))
    names = dict(con.execute("SELECT code, name_th FROM bc_ingredient"))
    out["lines"] = [{"code": c, "name": names.get(c), "qty": q, "unit": u, "qty_base": qb, "note": nt}
                    for c, q, u, qb, nt in con.execute(
                        "SELECT ingredient_code, qty_kitchen, unit, qty_base, note FROM bc_stock_count_line"
                        " WHERE count_id=? ORDER BY ingredient_code", (count_id,))]
    return out


def discard_draft(con, branch_id: int, who: str) -> bool:
    dr = draft_of(con, branch_id)
    if not dr:
        return False
    con.execute("DELETE FROM bc_stock_count_line WHERE count_id=?", (dr["count_id"],))
    con.execute("DELETE FROM bc_stock_count WHERE count_id=?", (dr["count_id"],))
    audit(con, who, "stock_discard", "bc_stock_count", str(dr["count_id"]), None, None)
    return True


def periods(con, branch_id: int) -> list:
    """Consecutive submitted counts: [(begin_count, end_count)] — the periods S7 measures."""
    cs = con.execute("SELECT count_id, count_date FROM bc_stock_count WHERE branch_id=?"
                     " AND COALESCE(status,'submitted')='submitted' ORDER BY count_date, count_id",
                     (branch_id,)).fetchall()
    return [({"count_id": a[0], "date": a[1]}, {"count_id": b[0], "date": b[1]}) for a, b in zip(cs, cs[1:])]


def set_layout(con, rows: list, who: str) -> int:
    """The centre sets where an item is kept and the unit it is counted in."""
    n = 0
    zones = {z for z, _ in ZONES}
    conv = Converter(con)
    for r in rows or []:
        code = (r.get("code") or "").strip()
        if not con.execute("SELECT 1 FROM bc_ingredient WHERE code=?", (code,)).fetchone():
            raise ValueError(f"ไม่พบรหัส {code}")
        z = (r.get("zone") or "").strip() or None
        if z and z not in zones:
            raise ValueError(f"{code}: โซนต้องเป็น {', '.join(sorted(zones))}")
        cu = (r.get("count_unit") or "").strip() or None
        if cu and conv.factor(code, cu)[0] is None:
            raise ValueError(f"{code}: ยังแปลง {cu} เป็นหน่วยฐานไม่ได้ — เพิ่มกฎหน่วยก่อน")
        con.execute("UPDATE bc_ingredient SET storage_zone=?, count_unit=? WHERE code=?", (z, cu, code))
        audit(con, who, "stock_layout", "bc_ingredient", code, None, {"zone": z, "count_unit": cu})
        n += 1
    return n


# ── S11: a count from a spreadsheet (a month closed after the fact) ──────────
def template_rows(con, branch_id: int) -> list:
    """The branch's count list as rows for the count template."""
    sh = sheet(con, branch_id)
    return [{"zone": z["label"], "code": it["code"], "name": it["name"], "count_unit": it["count_unit"],
             "per_count_unit": it["per_count_unit"], "base_unit": it["base_unit"], "class": it["class"],
             "required": it["required"]} for z in sh["zones"] for it in z["items"]]


def read_count_file(data: bytes) -> list:
    """[{code, qty, unit, note}] from the count template (or any sheet with รหัส + จำนวน columns)."""
    import io
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    grid = [list(r) for r in wb.worksheets[0].iter_rows(values_only=True)]
    hi = next((i for i, r in enumerate(grid[:10]) if "รหัส" in [str(c or "").strip() for c in r]
               and any(str(c or "").strip().startswith("จำนวน") for c in r)), None)
    if hi is None:
        raise ValueError("ไม่พบหัวตาราง (ต้องมีคอลัมน์ “รหัส” และ “จำนวน”) กรุณาใช้แบบฟอร์มตรวจนับของระบบ")
    head = [str(c or "").strip() for c in grid[hi]]
    ci = head.index("รหัส")
    cq = next(i for i, h in enumerate(head) if h.startswith("จำนวน"))
    cu = next((i for i, h in enumerate(head) if h.startswith("หน่วยนับ")), None)
    cn = next((i for i, h in enumerate(head) if h.startswith("หมายเหตุ")), None)

    def cell(r, i):
        return r[i] if i is not None and i < len(r) else None
    out = []
    for r in grid[hi + 1:]:
        code = str(cell(r, ci) or "").strip()
        q = cell(r, cq)
        if not code or q is None or str(q).strip() == "":
            continue
        try:
            qf = float(str(q).replace(",", ""))
        except ValueError:
            raise ValueError(f"{code}: จำนวน “{q}” ไม่ใช่ตัวเลข")
        out.append({"code": code, "qty": qf, "unit": str(cell(r, cu) or "").strip(),
                    "note": str(cell(r, cn) or "").strip() or None})
    if not out:
        raise ValueError("ไม่พบจำนวนที่ตรวจนับในไฟล์")
    return out


def import_count(con, branch_id: int, count_date: str, lines: list, who: str, dry_run: bool = True) -> dict:
    """Save a whole count from a file and submit it, dated the day it was counted."""
    try:
        date.fromisoformat(count_date)
    except (TypeError, ValueError):
        raise ValueError("วันที่นับต้องเป็น YYYY-MM-DD")
    if draft_of(con, branch_id):
        raise ValueError("สาขานี้มีร่างผลตรวจนับค้างอยู่ กรุณาส่งหรือลบร่างก่อนนำเข้า")
    if con.execute("SELECT 1 FROM bc_stock_count WHERE branch_id=? AND count_date=? AND"
                   " COALESCE(status,'submitted')='submitted'", (branch_id, count_date)).fetchone():
        raise ValueError(f"วันที่ {count_date} มีผลตรวจนับที่ส่งแล้ว จึงนำเข้าซ้ำไม่ได้")
    conv = Converter(con)
    known = {c for c, in con.execute("SELECT code FROM bc_ingredient")}
    unknown = sorted({ln["code"] for ln in lines if ln["code"] not in known})
    bad = [f"{ln['code']}: แปลง {ln['unit'] or '-'} ไม่ได้" for ln in lines if ln["code"] in known
           and conv.to_base(ln["code"], ln["qty"], ln["unit"] or conv.base_unit(ln["code"]))[0] is None]
    out = {"branch_id": branch_id, "count_date": count_date, "lines": len(lines) - len(unknown),
           "unknown": unknown, "unconverted": bad, "dry_run": dry_run, "ok": not unknown and not bad}
    if out["ok"] and not dry_run:
        r = save(con, branch_id, lines, who, submit=True, count_date=count_date, note="นำเข้าจากแบบฟอร์มตรวจนับ")
        out.update(count_id=r["count_id"], kind=r["kind"], status=r["status"])
    return out
