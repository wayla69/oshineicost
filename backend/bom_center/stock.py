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
import json
import re
from datetime import date, datetime, timedelta, timezone

from .clock import today as bkk_today
from .fmt import day_th as _fmt_day
from .master import audit
from .units import Converter

# PR-K: the storage zones are the centre's to edit (app_settings key bc_zones = JSON [{code, label}]).
# A code is stable (items point at it), a label may be renamed, the list order is the order a branch counts in.
DEFAULT_ZONES = (("frozen", "แช่แข็ง"), ("chilled", "แช่เย็น"), ("dry", "ของแห้ง"), ("bar", "บาร์น้ำ"), ("other", "อื่น ๆ"))
ZONES_KEY = "bc_zones"
FALLBACK_ZONE = "other"                   # where a guess lands when its zone no longer exists; cannot be deleted
LOOKBACK_DAYS = 28
_ZONE_CODE = re.compile(r"^[a-z][a-z0-9_]{0,23}$")


def branches_with_data(con, month: str) -> set:
    """PR-M5: the ids of the branches that have anything of the module in a month (YYYY-MM): a daily slip, an order, a
    stock count or a goods receipt dated in it. The centre's branch selects list these and no other branch."""
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month or ""):
        raise ValueError("เดือนต้องอยู่ในรูปแบบ YYYY-MM")
    like = month + "-%"
    ids = set()
    for table, col in (("bc_issue_slip", "slip_date"), ("bc_order", "cycle_date"),
                       ("bc_stock_count", "count_date"), ("bc_receipt", "receipt_date")):
        ids |= {r[0] for r in con.execute(f"SELECT DISTINCT branch_id FROM {table} WHERE {col} LIKE ?", (like,))}
    return ids


def zones(con) -> tuple:
    """The configured zones as ((code, label), …) in count order; the 5 defaults when nothing valid is stored."""
    try:
        r = con.execute("SELECT value FROM app_settings WHERE key=?", (ZONES_KEY,)).fetchone()
        raw = json.loads(r[0]) if r and r[0] else None
    except Exception:
        raw = None
    out, seen = [], set()
    for z in raw if isinstance(raw, list) else []:
        code = str((z or {}).get("code") or "").strip()
        label = str((z or {}).get("label") or "").strip()
        if code and label and code not in seen:
            seen.add(code)
            out.append((code, label))
    return tuple(out) or DEFAULT_ZONES


def zone_labels(con) -> dict:
    return dict(zones(con))


def zone_usage(con) -> dict:
    """{code: how many ingredients the centre has put in that zone}."""
    return {z: n for z, n in con.execute(
        "SELECT storage_zone, COUNT(*) FROM bc_ingredient WHERE COALESCE(storage_zone,'')<>'' GROUP BY storage_zone")}


def set_zones(con, rows: list, who: str) -> tuple:
    """Replace the zone list (add / rename / delete / reorder). Codes of existing zones are kept; a new zone
    may omit its code (one is made). A zone that still holds ingredients cannot be dropped."""
    if not isinstance(rows, list) or not rows:
        raise ValueError("ต้องมีโซนจัดเก็บอย่างน้อย 1 โซน")
    old = dict(zones(con))
    new, seen, labels = [], set(), set()
    for i, r in enumerate(rows, 1):
        label = re.sub(r"\s+", " ", str((r or {}).get("label") or "")).strip()
        code = str((r or {}).get("code") or "").strip().lower()
        if not label:
            raise ValueError(f"โซนลำดับที่ {i}: ระบุชื่อโซน")
        if len(label) > 40:
            raise ValueError(f"โซน “{label[:20]}…”: ชื่อยาวเกิน 40 ตัวอักษร")
        if label in labels:
            raise ValueError(f"ชื่อโซน “{label}” ซ้ำกัน")
        labels.add(label)
        if not code:
            n = 1
            used = set(old) | {c for c, _ in new} | {str((x or {}).get("code") or "").strip().lower() for x in rows}
            while f"zone{n}" in used:
                n += 1
            code = f"zone{n}"
        if not _ZONE_CODE.match(code):
            raise ValueError(f"รหัสโซน “{code}” ใช้ได้เฉพาะ a-z 0-9 _ และขึ้นต้นด้วยตัวอักษร")
        if code in seen:
            raise ValueError(f"รหัสโซน “{code}” ซ้ำกัน")
        seen.add(code)
        new.append((code, label))
    use = zone_usage(con)
    for code, label in old.items():
        if code not in seen:
            if code == FALLBACK_ZONE:
                raise ValueError(f"ลบโซน “{label}” ไม่ได้ เพราะเป็นโซนสำรอง เปลี่ยนชื่อได้เท่านั้น")
            if use.get(code):
                raise ValueError(f"ลบโซน “{label}” ไม่ได้ เพราะยังมีวัตถุดิบ {use[code]} รายการ "
                                 "· เปลี่ยนชื่อโซน หรือย้ายวัตถุดิบไปโซนอื่นก่อน")
    dump = lambda zs: json.dumps([{"code": c, "label": t} for c, t in zs], ensure_ascii=False)  # noqa: E731
    con.execute("CREATE TABLE IF NOT EXISTS app_settings(key TEXT PRIMARY KEY, value TEXT NOT NULL DEFAULT '')")
    con.execute("INSERT OR REPLACE INTO app_settings(key, value) VALUES(?, ?)", (ZONES_KEY, dump(new)))
    audit(con, who, "zones_set", "app_settings", ZONES_KEY, dump(old.items()), dump(new))
    return tuple(new)


def zone_code_for(con, value) -> str:
    """A zone code from a code or a label (the master sheet carries the label). '' = none; ValueError when unknown."""
    v = str(value or "").strip()
    if not v:
        return ""
    zs = zones(con)
    for c, t in zs:
        if v.lower() == c or v == t:
            return c
    raise ValueError(f"ไม่พบโซนจัดเก็บ “{v}” (โซนที่มี: " + ", ".join(t for _, t in zs) + ")")


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def guess_zone(code: str, name: str, family: str, kind: str, valid=None) -> str:
    """A first guess until the centre sets bc_ingredient.storage_zone. `valid` = the configured zone codes:
    a guess naming a zone the centre removed lands in the fallback zone (else the last one)."""
    z = _guess(code, name, family, kind)
    if valid is None or z in valid:
        return z
    return FALLBACK_ZONE if FALLBACK_ZONE in valid else list(valid)[-1]


def _guess(code: str, name: str, family: str, kind: str) -> str:
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


MONTH_KINDS = ("begin", "end")       # the month-end count. A 'spot' count (PR-A, the count made while ordering) was
                                     # retired in PR-J1: its rows and the CHECK stay, nothing writes or reads one any more


def _in(kinds) -> str:
    return "(" + ",".join(f"'{k}'" for k in kinds) + ")"


def last_submitted(con, branch_id: int, kinds=MONTH_KINDS):
    r = con.execute("SELECT count_id, count_date, kind, submitted_at, counted_by FROM bc_stock_count"
                    f" WHERE branch_id=? AND COALESCE(status,'submitted')='submitted' AND kind IN {_in(kinds)}"
                    " ORDER BY count_date DESC, count_id DESC LIMIT 1", (branch_id,)).fetchone()
    return dict(zip(("count_id", "count_date", "kind", "submitted_at", "counted_by"), r)) if r else None


def draft_of(con, branch_id: int, kinds=MONTH_KINDS):
    r = con.execute("SELECT count_id, count_date, kind, updated_at, counted_by FROM bc_stock_count"
                    f" WHERE branch_id=? AND status='draft' AND kind IN {_in(kinds)}"
                    " ORDER BY count_id DESC LIMIT 1", (branch_id,)).fetchone()
    return dict(zip(("count_id", "count_date", "kind", "updated_at", "counted_by"), r)) if r else None


def count_row(con, count_id: int):
    r = con.execute("SELECT count_id, branch_id, count_date, kind, COALESCE(status,'submitted') FROM bc_stock_count"
                    " WHERE count_id=?", (count_id,)).fetchone()
    return dict(zip(("count_id", "branch_id", "count_date", "kind", "status"), r)) if r else None


def count_units(con, codes) -> dict:
    """{code: (unit it is counted in, base units in one of it)} — the rule `sheet()` shows a branch."""
    codes = set(codes)
    if not codes:
        return {}
    conv = Converter(con)
    out = {}
    for c, base, recv, per, cu in con.execute(
            "SELECT code, base_unit, recv_unit, qty_per_recv, count_unit FROM bc_ingredient"):
        if c in codes:
            out[c] = (cu, conv.factor(c, cu)[0]) if cu else count_unit(base, recv, per)
    return out


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
    zcfg = zones(con)
    valid = [z for z, _ in zcfg]
    by_zone = {z: [] for z in valid}
    conv = Converter(con)
    for c in sorted(codes):
        m = master.get(c)
        if not m:
            continue
        name, fam, kind, base, recv, per, mon, zone, cu, _active = m
        unit, factor = (cu, conv.factor(c, cu)[0]) if cu else count_unit(base, recv, per)
        z = zone if zone in by_zone else guess_zone(c, name, fam, kind, valid)
        k = klass.get(c, "C")
        pv = prev.get(c)
        last_q, last_u = (pv[0], pv[1]) if pv else (None, None)
        # "ครั้งก่อน" is read in the unit the item is counted in now: a previous count typed in another unit is converted
        if pv and pv[2] is not None and factor and (last_u or unit) != unit:
            last_q, last_u = round(pv[2] / factor, 3), unit
        by_zone[z].append({
            "code": c, "name": name or c, "base_unit": base, "count_unit": unit, "per_count_unit": factor,
            "zone_guessed": not zone, "class": k, "required": bool(mon) or k == "A", "monitored": bool(mon),
            "use_per_day": round(per_day.get(c, 0.0), 3),
            "pieces_unit": (conv.weight_avg_step(c) or {}).get("unit"),     # PR-U2: a manual receipt may carry the pieces counted
            "last_qty": last_q, "last_unit": last_u, "last_base": pv[2] if pv else None,
            "draft_qty": draft.get(c, (None,))[0]})
    order = {"A": 0, "B": 1, "C": 2}
    out_z = []
    for z, label in zcfg:
        items = sorted(by_zone.get(z, []), key=lambda r: (not r["required"], order[r["class"]], r["name"]))
        if items:
            out_z.append({"zone": z, "label": label, "items": items})
    return {"branch_id": branch_id, "branch": bname, "as_of": as_of.isoformat(),
            "next_kind": "end" if lc else "begin", "last_count": lc, "draft": dr, "zones": out_z,
            "items": sum(len(z["items"]) for z in out_z),
            "required": sum(1 for z in out_z for r in z["items"] if r["required"])}


def uncounted_required(con, branch_id: int, count_id: int) -> list:
    """The required (บังคับ) items of the branch's sheet that the count `count_id` has no quantity for: [(code, name)]."""
    have = {c for (c,) in con.execute("SELECT ingredient_code FROM bc_stock_count_line WHERE count_id=?", (count_id,))}
    return [(it["code"], it["name"]) for z in sheet(con, branch_id)["zones"] for it in z["items"]
            if it["required"] and it["code"] not in have]


def save(con, branch_id: int, lines: list, who: str, submit: bool = False, count_date: str = None,
         note: str = None, require_all: bool = True) -> dict:
    """Write the branch's month-end draft (creating it) and optionally submit it: the first count is the
    BEGIN stock, every later one the END of a period and the BEGIN of the next.

    PR-M4 (owner rule "ทุกรายการที่สั่งต้องนับ"): a count is submitted only when every required (บังคับ) item has a
    quantity — `require_all=False` is for a historical count loaded from a file (import_count), never for a branch."""
    _branch_name(con, branch_id)
    conv = Converter(con)
    cdate = count_date or bkk_today().isoformat()
    try:
        date.fromisoformat(cdate)
    except ValueError:
        raise ValueError("วันที่ตรวจนับต้องอยู่ในรูปแบบ YYYY-MM-DD")
    dr = draft_of(con, branch_id)
    kind = "end" if last_submitted(con, branch_id) else "begin"
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
            raise ValueError(f"{code}: จำนวนต้องไม่ติดลบ")
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
            raise ValueError("ยังไม่มีรายการที่ตรวจนับ")
        if require_all:
            miss = uncounted_required(con, branch_id, cid)
            if miss:
                raise ValueError(f"ยังไม่นับรายการบังคับ {len(miss)} รายการ (เช่น {', '.join(n for _, n in miss[:3])}) นับให้ครบก่อนส่ง")
        # every line of the draft, not only the ones sent now, must have reached the base unit
        bad = [f"{c}: แปลง {u} ไม่ได้" for c, u in con.execute(
            "SELECT ingredient_code, unit FROM bc_stock_count_line WHERE count_id=? AND qty_base IS NULL", (cid,))]
        if bad:
            raise ValueError("แปลงหน่วยไม่ได้: " + "; ".join(bad[:3]) + " · แจ้งฝ่ายวิเคราะห์ให้เพิ่มกฎหน่วย")
        con.execute("UPDATE bc_stock_count SET status='submitted', submitted_at=? WHERE count_id=?", (now(), cid))
        audit(con, who, "stock_submit", "bc_stock_count", str(cid), None,
              {"branch_id": branch_id, "kind": kind, "date": cdate, "lines": n})
        out["status"] = "submitted"
    return out


def system_position(con, branch_id: int, as_of: date, issues_through: date = None) -> dict:
    """The stock the SYSTEM says is on the shelf (ใบตัดเบิก, PR-J1):

        latest submitted month-end count dated on or before `as_of`
        + goods received from the count date up to `as_of` (all sources)
        - CONFIRMED slips (used + waste, base units) from the count date up to `issues_through` (default `as_of`)

    The month-end count is made before trading, so the receipts and the slip OF the count date are in.
    Only codes that were counted get a figure — the others are unknown and the forecast stands alone.
    `missing_days` are the days from the count date to `issues_through` with no confirmed slip: the stock
    shown is then higher than the real one.

    -> {"basis": {count_id, date} | None, "qty": {code: base units}, "missing_days": [iso], "through": iso}"""
    from .issues import missing_days
    through = issues_through or as_of
    out = {"basis": None, "qty": {}, "missing_days": [], "through": as_of.isoformat()}
    r = con.execute("SELECT count_id, count_date FROM bc_stock_count WHERE branch_id=?"
                    f" AND COALESCE(status,'submitted')='submitted' AND kind IN {_in(MONTH_KINDS)} AND count_date<=?"
                    " ORDER BY count_date DESC, count_id DESC LIMIT 1", (branch_id, as_of.isoformat())).fetchone()
    if not r:
        return out
    cid, cdate = r
    out["basis"] = {"count_id": cid, "date": cdate}
    qty = {c: q for c, q in con.execute(
        "SELECT ingredient_code, qty_base FROM bc_stock_count_line WHERE count_id=? AND qty_base IS NOT NULL", (cid,))}
    for c, q in con.execute(
            "SELECT l.ingredient_code, SUM(l.qty_base) FROM bc_receipt r JOIN bc_receipt_line l USING(receipt_id)"
            " WHERE r.branch_id=? AND r.receipt_date>=? AND r.receipt_date<=? GROUP BY l.ingredient_code",
            (branch_id, cdate, as_of.isoformat())):
        if c in qty:
            qty[c] += q or 0.0
    for c, q in con.execute(
            "SELECT l.ingredient_code, SUM(COALESCE(l.qty_used_base, 0) + COALESCE(l.qty_waste_base, 0))"
            " FROM bc_issue_slip s JOIN bc_issue_line l USING(slip_id) WHERE s.branch_id=? AND s.status='confirmed'"
            " AND s.slip_date>=? AND s.slip_date<=? GROUP BY l.ingredient_code",
            (branch_id, cdate, through.isoformat())):
        if c in qty:
            qty[c] -= q or 0.0
    out["qty"] = qty
    out["missing_days"] = missing_days(con, branch_id, cdate, through)
    return out


def history(con, branch_id: int, limit: int = 20, kinds=None) -> list:
    where = f" AND c.kind IN {_in(kinds)}" if kinds else ""
    rows = con.execute(
        "SELECT c.count_id, c.count_date, c.kind, c.status, c.counted_by, c.submitted_at, COUNT(l.ingredient_code)"
        f" FROM bc_stock_count c LEFT JOIN bc_stock_count_line l USING(count_id) WHERE c.branch_id=?{where}"
        " GROUP BY c.count_id ORDER BY c.count_date DESC, c.count_id DESC LIMIT ?", (branch_id, limit)).fetchall()
    return [dict(zip(("count_id", "count_date", "kind", "status", "counted_by", "submitted_at", "lines"), r))
            for r in rows]


def detail(con, count_id: int) -> dict:
    r = con.execute("SELECT count_id, branch_id, count_date, kind, status, counted_by, submitted_at, note"
                    " FROM bc_stock_count WHERE count_id=?", (count_id,)).fetchone()
    if not r:
        raise ValueError("ไม่พบผลตรวจนับนี้")
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
                     f" AND COALESCE(status,'submitted')='submitted' AND kind IN {_in(MONTH_KINDS)}"
                     " ORDER BY count_date, count_id",
                     (branch_id,)).fetchall()
    return [({"count_id": a[0], "date": a[1]}, {"count_id": b[0], "date": b[1]}) for a, b in zip(cs, cs[1:])]


def set_layout(con, rows: list, who: str) -> int:
    """The centre sets where an item is kept and the unit it is counted in."""
    n = 0
    zlabels = zone_labels(con)
    conv = Converter(con)
    for r in rows or []:
        code = (r.get("code") or "").strip()
        if not con.execute("SELECT 1 FROM bc_ingredient WHERE code=?", (code,)).fetchone():
            raise ValueError(f"ไม่พบรหัส {code}")
        z = (r.get("zone") or "").strip() or None
        if z and z not in zlabels:
            raise ValueError(f"{code}: โซนต้องเป็น {', '.join(sorted(zlabels))}")
        cu = (r.get("count_unit") or "").strip() or None
        if cu and conv.factor(code, cu)[0] is None:
            raise ValueError(f"{code}: ยังแปลง {cu} เป็นหน่วยฐานไม่ได้ · เพิ่มกฎหน่วยก่อน")
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
        raise ValueError("ไม่พบหัวตาราง (ต้องมีคอลัมน์ “รหัส” และ “จำนวน”) ใช้แบบฟอร์มตรวจนับจากหน้านี้")
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
        raise ValueError("วันที่ตรวจนับต้องอยู่ในรูปแบบ YYYY-MM-DD")
    if draft_of(con, branch_id):
        raise ValueError("สาขานี้มีร่างผลตรวจนับค้างอยู่ · ส่งหรือลบร่างก่อนนำเข้า")
    if con.execute("SELECT 1 FROM bc_stock_count WHERE branch_id=? AND count_date=? AND"
                   f" COALESCE(status,'submitted')='submitted' AND kind IN {_in(MONTH_KINDS)}",
                   (branch_id, count_date)).fetchone():
        raise ValueError(f"วันที่ {_fmt_day(count_date)} มีผลตรวจนับที่ส่งแล้ว จึงนำเข้าซ้ำไม่ได้")
    conv = Converter(con)
    known = {c for c, in con.execute("SELECT code FROM bc_ingredient")}
    unknown = sorted({ln["code"] for ln in lines if ln["code"] not in known})
    bad = [f"{ln['code']}: แปลง {ln['unit'] or '-'} ไม่ได้" for ln in lines if ln["code"] in known
           and conv.to_base(ln["code"], ln["qty"], ln["unit"] or conv.base_unit(ln["code"]))[0] is None]
    out = {"branch_id": branch_id, "count_date": count_date, "lines": len(lines) - len(unknown),
           "unknown": unknown, "unconverted": bad, "dry_run": dry_run, "ok": not unknown and not bad}
    if out["ok"] and not dry_run:
        r = save(con, branch_id, lines, who, submit=True, count_date=count_date, note="นำเข้าจากแบบฟอร์มตรวจนับ",
                 require_all=False)
        out.update(count_id=r["count_id"], kind=r["kind"], status=r["status"])
    return out
