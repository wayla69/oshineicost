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
from .fmt import item as _item
from .fmt import no_unit as _no_unit
from .master import audit
from .units import Converter, _is_pack_word, unit_word

# PR-K: the storage zones are the centre's to edit (app_settings key bc_zones = JSON [{code, label}]).
# A code is stable (items point at it), a label may be renamed, the list order is the order a branch counts in.
DEFAULT_ZONES = (("frozen", "แช่แข็ง"), ("chilled", "แช่เย็น"), ("dry", "ของแห้ง"), ("bar", "บาร์น้ำ"), ("other", "อื่น ๆ"))
ZONES_KEY = "bc_zones"
FALLBACK_ZONE = "other"                   # where a guess lands when its zone no longer exists; cannot be deleted
LOOKBACK_DAYS = 28
RECENT_USE_DAYS = 14               # 3.2: an item the recipes used within this many days is on the default slip
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


def entry_unit(conv, code: str, base, recv, per, explicit=None) -> tuple:
    """PR-B2 (3.1): (the unit a branch types a quantity in, base units in one of it).

    The analyst's own `count_unit` (set on the stock layout or in the register file) is kept as it is. Without one the entry unit
    is the SMALLEST SEALED unit of the item (units.Converter.sealed): an item received by the box ("กล่อง (10 แพ็ค)") with a
    แพ็ค on its ladder is issued and counted in แพ็ค, a use of 0.3 box a day being 3 packs. An item whose receiving unit is loose
    (กิโลกรัม, ชิ้น) or has no pack size keeps the unit it always had."""
    if explicit:
        return explicit, conv.factor(code, explicit)[0]
    unit, factor = count_unit(base, recv, per)
    w = unit_word(recv)
    if per and recv and w and _is_pack_word(w):
        sealed = conv.sealed(code)
        if sealed:
            u = sealed[0]
            return (u["label"] if u["source"] == "recv" else u["unit"]), u["factor"]
    return unit, factor


def big_unit(conv, code: str, unit: str, factor) -> dict:
    """PR-B2 (3.1): the next sealed unit above the entry unit when it holds a whole number of them — {unit, label, per, factor} for
    a box of 10 packs — else None. The month-end count offers it as a second box (กล่อง + แพ็ค) that sums to base units."""
    w = unit_word(unit)
    if not factor or not w or not _is_pack_word(w):
        return None
    above = [u for u in conv.sealed(code) if u["factor"] > factor * (1 + 1e-9)]
    if not above:
        return None
    u = above[0]
    ratio = u["factor"] / factor
    if round(ratio) < 2 or abs(ratio - round(ratio)) > 1e-6:
        return None
    return {"unit": u["unit"], "label": u["label"], "per": int(round(ratio)), "factor": u["factor"]}


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
            out[c] = entry_unit(conv, c, base, recv, per, cu)
    return out


def _usage(con, branch_id: int, as_of: date, with_recent: bool = False):
    """{code: average base units a trading day} over the lookback window. `with_recent` -> (that, the set of codes the recipes
    used on a day of the last RECENT_USE_DAYS days up to `as_of`): the items a branch is still using (3.2)."""
    from .forecast import era_start, usage_history
    start = era_start(as_of - timedelta(days=LOOKBACK_DAYS - 1), as_of)
    use, days, _ = usage_history(con, branch_id, start, as_of, f"{as_of.year:04d}-{as_of.month:02d}")
    n = max(len(days), 1)
    avg = {c: sum(s.values()) / n for c, s in use.items() if sum(s.values()) > 0}
    if not with_recent:
        return avg
    since = (as_of - timedelta(days=RECENT_USE_DAYS - 1)).isoformat()
    return avg, {c for c, s in use.items() if any(d >= since and (q or 0) > 0 for d, q in s.items())}


def sheet(con, branch_id: int, as_of: date = None, mode: str = "full") -> dict:
    """The list a branch counts, grouped by zone, with the last count shown faintly.

    Batch 4b (3.4): `mode="cycle"` is the weekly count - only the branch's class-A items (the same classes as below), all of them
    required, the draft and "ครั้งก่อน" read from the branch's cycle counts (the month-end count when it has none yet)."""
    from .forecast import _abc
    cyc = mode == "cycle"
    if cyc and not cycle_on(con):
        raise ValueError("การตรวจนับหมุนเวียนปิดอยู่ · ตรวจนับครบทุกรายการ")
    from .prices import price_for
    bname = _branch_name(con, branch_id)
    last = con.execute("SELECT MAX(day) FROM sales_sku_daily WHERE branch_id=?", (branch_id,)).fetchone()[0]
    as_of = as_of or (date.fromisoformat(last) if last else bkk_today())
    month = f"{as_of.year:04d}-{as_of.month:02d}"
    per_day, used_recent = _usage(con, branch_id, as_of, with_recent=True)
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
    lcy = cycle_last(con, branch_id) if cyc else None
    if lcy:
        prev = {c: (q, u, qb) for c, q, u, qb in con.execute(
            "SELECT ingredient_code, qty_kitchen, unit, qty_base FROM bc_cycle_count_line WHERE cycle_id=?", (lcy["count_id"],))}
    elif lc:
        prev = {c: (q, u, qb) for c, q, u, qb in con.execute(
            "SELECT ingredient_code, qty_kitchen, unit, qty_base FROM bc_stock_count_line WHERE count_id=?",
            (lc["count_id"],))}
    dr = cycle_draft_of(con, branch_id) if cyc else draft_of(con, branch_id)
    draft = {}
    if dr:
        draft = {c: (q, u, qb) for c, q, u, qb in con.execute(
            "SELECT ingredient_code, qty_kitchen, unit, qty_base FROM bc_cycle_count_line WHERE cycle_id=?" if cyc else
            "SELECT ingredient_code, qty_kitchen, unit, qty_base FROM bc_stock_count_line WHERE count_id=?", (dr["count_id"],))}
    zcfg = zones(con)
    valid = [z for z, _ in zcfg]
    by_zone = {z: [] for z in valid}
    conv = Converter(con)
    for c in sorted(codes):
        m = master.get(c)
        if not m:
            continue
        name, fam, kind, base, recv, per, mon, zone, cu, _active = m
        unit, factor = entry_unit(conv, c, base, recv, per, cu)
        z = zone if zone in by_zone else guess_zone(c, name, fam, kind, valid)
        k = klass.get(c, "C")
        if cyc and k != "A":                      # the weekly count is the class-A list only
            continue
        pv = prev.get(c)
        last_q, last_u = (pv[0], pv[1]) if pv else (None, None)
        # "ครั้งก่อน" is read in the unit the item is counted in now: a previous count typed in another unit is converted
        if pv and pv[2] is not None and factor and (last_u or unit) != unit:
            last_q, last_u = round(pv[2] / factor, 3), unit
        dq = draft.get(c)                         # a draft typed in another unit (before the entry unit changed) is read in this one
        if dq and dq[2] is not None and factor and (dq[1] or unit) != unit:
            dq = (round(dq[2] / factor, 4),)
        by_zone[z].append({
            "code": c, "name": name or c, "base_unit": base, "count_unit": unit, "per_count_unit": factor,
            "unit_big": big_unit(conv, c, unit, factor),                    # 3.1: a second box (กล่อง) on the month-end count
            "zone_guessed": not zone, "class": k, "required": bool(mon) or k == "A" or cyc, "monitored": bool(mon),
            "use_per_day": round(per_day.get(c, 0.0), 3), "used_recent": c in used_recent,
            "pieces_unit": (conv.weight_avg_step(c) or {}).get("unit"),     # PR-U2: a manual receipt may carry the pieces counted
            "last_qty": last_q, "last_unit": last_u, "last_base": pv[2] if pv else None,
            "draft_qty": dq[0] if dq else None})
    order = {"A": 0, "B": 1, "C": 2}
    out_z = []
    for z, label in zcfg:
        items = sorted(by_zone.get(z, []), key=lambda r: (not r["required"], order[r["class"]], r["name"]))
        if items:
            out_z.append({"zone": z, "label": label, "items": items})
    return {"branch_id": branch_id, "branch": bname, "as_of": as_of.isoformat(), "mode": "cycle" if cyc else "full",
            "next_kind": "cycle" if cyc else ("end" if lc else "begin"),
            "last_count": (dict(lcy, kind="cycle") if lcy else lc) if cyc else lc, "draft": dr, "zones": out_z,
            "items": sum(len(z["items"]) for z in out_z),
            "required": sum(1 for z in out_z for r in z["items"] if r["required"]), "plan": count_plan(con, branch_id)}


def uncounted_required(con, branch_id: int, count_id: int) -> list:
    """The required (บังคับ) items of the branch's sheet that the count `count_id` has no quantity for: [(code, name)]."""
    have = {c for (c,) in con.execute("SELECT ingredient_code FROM bc_stock_count_line WHERE count_id=?", (count_id,))}
    return [(it["code"], it["name"]) for z in sheet(con, branch_id)["zones"] for it in z["items"]
            if it["required"] and it["code"] not in have]


def _sum_parts(con, conv, code: str, parts: list, bad: list) -> tuple:
    """(quantity in the entry unit, that unit, base units) of a count typed in several boxes — [{qty, unit}, …]. An empty box is 0;
    no box with a number at all -> (None, None, None). Base units are the SUM of each box converted on its own, so 2 กล่อง and
    3 แพ็ค are exact whatever the entry unit is. A box whose unit cannot be converted is listed in `bad` -> (0, None, None)."""
    total_b, any_part = 0.0, False
    for p in parts:
        raw = (p or {}).get("qty")
        if raw is None or str(raw).strip() == "":
            continue
        try:
            q = float(raw)
        except (TypeError, ValueError):
            raise ValueError(f"{_item(con, code)}: จำนวนต้องเป็นตัวเลข")
        if q < 0:
            raise ValueError(f"{_item(con, code)}: จำนวนต้องไม่ติดลบ")
        u = (p.get("unit") or "").strip() or conv.base_unit(code)
        pb, _how = conv.to_base(code, q, u)
        if pb is None:
            bad.append(_no_unit(con, code, u))
            return 0, None, None
        total_b += pb
        any_part = True
    if not any_part:
        return None, None, None
    m = con.execute("SELECT base_unit, recv_unit, qty_per_recv, count_unit FROM bc_ingredient WHERE code=?", (code,)).fetchone()
    unit, factor = entry_unit(conv, code, *m) if m else (conv.base_unit(code), 1.0)
    return (total_b / factor if factor else total_b), (unit or conv.base_unit(code)), total_b


def _write_lines(con, conv, line_table: str, id_col: str, cid: int, lines: list, bad: list) -> None:
    """Write the typed lines of a count draft (a month-end count or a cycle count - the same columns): a quantity in the entry unit, or
    the boxes of a two-step item summed in base units (3.1); an empty line clears the row; a unit that cannot reach the base unit goes
    to `bad` and the row is kept with no base quantity so submit refuses it."""
    for ln in lines or []:
        code = (ln.get("code") or ln.get("ingredient_code") or "").strip()
        if not code:
            continue
        parts = ln.get("parts")
        if parts:                                  # 3.1: "2 กล่อง + 3 แพ็ค" - each part to base units, summed; kept in the entry unit
            q, unit, qb = _sum_parts(con, conv, code, parts, bad)
            if unit is None:
                if q is None and not bad:          # no part carries a number: the line is cleared like an empty single box
                    con.execute(f"DELETE FROM {line_table} WHERE {id_col}=? AND ingredient_code=?", (cid, code))
                continue
        else:
            q = ln.get("qty")
            if q is None or q == "":
                con.execute(f"DELETE FROM {line_table} WHERE {id_col}=? AND ingredient_code=?", (cid, code))
                continue
            try:
                q = float(q)
            except (TypeError, ValueError):
                raise ValueError(f"{_item(con, code)}: จำนวนต้องเป็นตัวเลข")
            if q < 0:
                raise ValueError(f"{_item(con, code)}: จำนวนต้องไม่ติดลบ")
            unit = (ln.get("unit") or "").strip() or conv.base_unit(code)
            qb, how = conv.to_base(code, q, unit)
            if qb is None:
                bad.append(_no_unit(con, code, unit))
        con.execute(f"INSERT INTO {line_table}({id_col}, ingredient_code, qty_kitchen, unit, qty_base, note)"
                    f" VALUES(?,?,?,?,?,?) ON CONFLICT({id_col}, ingredient_code) DO UPDATE SET"
                    " qty_kitchen=excluded.qty_kitchen, unit=excluded.unit, qty_base=excluded.qty_base,"
                    " note=excluded.note", (cid, code, q, unit, qb, (ln.get("note") or None)))


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
    _write_lines(con, conv, "bc_stock_count_line", "count_id", cid, lines, bad)
    n = con.execute("SELECT COUNT(*) FROM bc_stock_count_line WHERE count_id=?", (cid,)).fetchone()[0]
    out = {"count_id": cid, "kind": kind, "lines": n, "status": "draft", "unconverted": bad}
    if submit:
        if not n:
            raise ValueError("ยังไม่มีรายการที่ตรวจนับ")
        if require_all:
            miss = uncounted_required(con, branch_id, cid)
            if miss:
                raise ValueError(f"ยังมีรายการที่ต้องนับ {len(miss)} รายการ ที่ยังไม่ได้นับ (เช่น {', '.join(n for _, n in miss[:3])}) นับให้ครบก่อนส่ง")
        # every line of the draft, not only the ones sent now, must have reached the base unit
        bad = [_no_unit(con, c, u) for c, u in con.execute(
            "SELECT ingredient_code, unit FROM bc_stock_count_line WHERE count_id=? AND qty_base IS NULL", (cid,))]
        if bad:
            raise ValueError("บันทึกไม่ได้: " + "; ".join(bad[:3]) + " · แจ้งฝ่ายวิเคราะห์")
        con.execute("UPDATE bc_stock_count SET status='submitted', submitted_at=? WHERE count_id=?", (now(), cid))
        audit(con, who, "stock_submit", "bc_stock_count", str(cid), None,
              {"branch_id": branch_id, "kind": kind, "date": cdate, "lines": n})
        out["status"] = "submitted"
    return out


def system_position(con, branch_id: int, as_of: date, issues_through: date = None, estimate: bool = True) -> dict:
    """The stock the SYSTEM says is on the shelf (ใบตัดเบิก, PR-J1):

        latest submitted month-end count dated on or before `as_of`
        + goods received from the count date up to `as_of` (all sources)
        - CONFIRMED slips (used + waste, base units) from the count date up to `issues_through` (default `as_of`)

    The month-end count is made before trading, so the receipts and the slip OF the count date are in.
    Only codes that were counted get a figure — the others are unknown and the forecast stands alone.
    `missing_days` are the days from the count date to `issues_through` with no confirmed slip.

    PR-B4a (1.3): a missing day on which the branch SOLD something (and that is over: before `as_of`) is no longer left
    out - the BoM usage of its sales (issue_reports.bom_days, the same per-day figure the daily report compares a slip
    with) is deducted instead, so the stock is not higher than the real one for the days nobody wrote a slip. The day is
    listed in `estimated_days` and the quantity per item in `estimated_qty`; once the branch confirms the slip of that day it
    is a confirmed day and the slip replaces the estimate. A day without sales (closed) deducts nothing. `estimate=False`
    gives the confirmed-slips-only figure.

    -> {"basis": {count_id, date} | None, "qty": {code: base units}, "missing_days": [iso], "through": iso,
        "estimated_days": [iso], "estimated_qty": {code: base units}}"""
    from .issues import missing_days
    through = issues_through or as_of
    out = {"basis": None, "qty": {}, "missing_days": [], "through": as_of.isoformat(), "estimated_days": [],
           "estimated_qty": {}, "cycle": {}}
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
    if estimate and out["missing_days"]:
        days, est = estimated_use(con, branch_id, [d for d in out["missing_days"] if d < as_of.isoformat()])
        out["estimated_days"] = days
        for c, q in est.items():
            if c in qty:                                   # an uncounted item has no figure to lower
                qty[c] -= q
                out["estimated_qty"][c] = q
    for c, (q, cd, e) in _cycle_rebase(con, branch_id, cid, cdate, as_of, through, out["estimated_days"], estimate).items():
        qty[c] = q                                         # batch 4b (3.4): the latest weekly count of a class-A item replaces the month-end figure
        if e:
            out["estimated_qty"][c] = e
        else:
            out["estimated_qty"].pop(c, None)
        out["cycle"][c] = cd
    return out


def estimated_use(con, branch_id: int, days: list) -> tuple:
    """PR-B4a (1.3): ({days that had sales}, {code: base units}) - the BoM usage of the given days' sales, summed. The per-day
    figure is issue_reports.bom_days (cached in bcd_demand_day, rebuilt when the sales or the release changed); a day without
    sales is not in it, so a closed day deducts nothing."""
    if not days:
        return [], {}
    from .issue_reports import bom_days
    by_month = {}
    for d in sorted(days):
        by_month.setdefault(d[:7], []).append(d)
    got, est = [], {}
    for month, ds in by_month.items():
        bom = bom_days(con, branch_id, ds[0], ds[-1], month)
        for d in ds:
            if d in bom:
                got.append(d)
                for c, q in bom[d].items():
                    est[c] = est.get(c, 0.0) + (q or 0.0)
    return got, est


def history(con, branch_id: int, limit: int = 20, kinds=None) -> list:
    where = f" AND c.kind IN {_in(kinds)}" if kinds else ""
    rows = con.execute(
        "SELECT c.count_id, c.count_date, c.kind, c.status, c.counted_by, c.submitted_at, COUNT(l.ingredient_code), COALESCE(c.partial, 0)"
        f" FROM bc_stock_count c LEFT JOIN bc_stock_count_line l USING(count_id) WHERE c.branch_id=?{where}"
        " GROUP BY c.count_id ORDER BY c.count_date DESC, c.count_id DESC LIMIT ?", (branch_id, limit)).fetchall()
    return [dict(zip(("count_id", "count_date", "kind", "status", "counted_by", "submitted_at", "lines", "partial"), r))
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


# ── batch 4b (3.4): cycle counts and the partial month close ─────────────────────────────────
# The owner's decision (2026-10-12, as proposed): the class-A items of a branch are counted every week, everything else at the end of
# a quarter; every other month closes on a PARTIAL end count - the latest weekly counts of the class-A items plus the system stock
# (confirmed slips) for the rest, labelled "คงเหลือตามระบบ (ไม่ได้นับ)". The switch bc_cycle_counts (ตั้งค่า) returns every month to
# the full count with no code change. A partial end count is an ordinary 'end' count (so periods, variance, the reconciliation, the
# accounting summary and the posted-period lock read it unchanged) whose lines carry `basis`.
QUARTER_END_MONTHS = (3, 6, 9, 12)
FULL_WINDOW_DAYS = 14               # the full count of a new quarter is "due" this many days from the 1st
PARTIAL_WINDOW_DAYS = 25            # a month can be closed on a partial count until this many days into the next month
PARTIAL_TOLERANCE_DAYS = 2          # a month-end count dated up to two days after the 1st is that month's close (todo.CLOSE_TOLERANCE_DAYS)
PARTIAL_NOTE = "คงเหลือตามระบบ (ไม่ได้นับ) ยกเว้นรายการกลุ่มมูลค่าสูงที่นับหมุนเวียน"


def cycle_on(con) -> bool:
    from .close_settings import cycle_counts_on
    return cycle_counts_on(con)


def is_quarter_end(month: str) -> bool:
    """True for a month whose close needs the full count: March, June, September, December ('YYYY-MM')."""
    return int(month[5:7]) in QUARTER_END_MONTHS


def month_first(month: str) -> date:
    return date(int(month[:4]), int(month[5:7]), 1)


def next_month_first(month: str) -> date:
    y, m = int(month[:4]), int(month[5:7])
    return date(y + (m == 12), 1 if m == 12 else m + 1, 1)


def prev_month(d: date) -> str:
    y, m = d.year - (d.month == 1), 12 if d.month == 1 else d.month - 1
    return f"{y:04d}-{m:02d}"


def cycle_last(con, branch_id: int):
    r = con.execute("SELECT cycle_id, count_date, submitted_at, counted_by FROM bc_cycle_count WHERE branch_id=? AND status='submitted'"
                    " ORDER BY count_date DESC, cycle_id DESC LIMIT 1", (branch_id,)).fetchone()
    return dict(zip(("count_id", "count_date", "submitted_at", "counted_by"), r)) if r else None


def cycle_draft_of(con, branch_id: int):
    r = con.execute("SELECT cycle_id, count_date, updated_at, counted_by FROM bc_cycle_count WHERE branch_id=? AND status='draft'"
                    " ORDER BY cycle_id DESC LIMIT 1", (branch_id,)).fetchone()
    return dict(zip(("count_id", "count_date", "updated_at", "counted_by"), r)) if r else None


def count_plan(con, branch_id: int, today: date = None) -> dict:
    """What the branch counts NOW (the count page opens on `mode`):

      full   - the first count of the branch, or the quarter-end count (the first FULL_WINDOW_DAYS days of Jan / Apr / Jul / Oct, until a
               full count dated in the new quarter is submitted), or every month while the switch is off;
      cycle  - otherwise the weekly count of the class-A items.

    `partial_month` = the month that can be closed now on a partial end count (not a quarter-end month, no month-end count dated at
    its end yet), else None. `cycle_due` = no weekly count yet in the current week (Monday to Sunday)."""
    today = today or bkk_today()
    on = cycle_on(con)
    lc = last_submitted(con, branch_id)
    ws = today - timedelta(days=today.weekday())
    q0 = date(today.year, ((today.month - 1) // 3) * 3 + 1, 1)
    plan = {"cycle_counts": on, "mode": "full", "first": lc is None, "full_due": False, "full_overdue": False,
            "quarter_start": q0.isoformat(), "partial_month": None, "partial_date": None, "cycle_due": False,
            "cycle_last": None, "week_start": ws.isoformat()}
    if not on or lc is None:
        return plan
    cl = cycle_last(con, branch_id)
    plan["cycle_last"] = cl["count_date"] if cl else None
    full_done = con.execute(f"SELECT 1 FROM bc_stock_count WHERE branch_id=? AND COALESCE(status,'submitted')='submitted' AND kind IN {_in(MONTH_KINDS)}"
                            " AND COALESCE(partial,0)=0 AND count_date>=? AND count_date<=?", (branch_id, q0.isoformat(), today.isoformat())).fetchone()
    if not full_done:
        if (today - q0).days <= FULL_WINDOW_DAYS:
            plan["full_due"], plan["mode"] = True, "full"
        else:
            plan["full_overdue"] = True
    if not plan["full_due"]:
        plan["mode"] = "cycle"
        plan["cycle_due"] = not con.execute("SELECT 1 FROM bc_cycle_count WHERE branch_id=? AND status='submitted' AND count_date>=?",
                                            (branch_id, ws.isoformat())).fetchone()
    d0 = date(today.year, today.month, 1)
    m = prev_month(today)
    if (today - d0).days <= PARTIAL_WINDOW_DAYS and not is_quarter_end(m):
        has_close = con.execute(f"SELECT 1 FROM bc_stock_count WHERE branch_id=? AND COALESCE(status,'submitted')='submitted' AND kind IN {_in(MONTH_KINDS)}"
                                " AND count_date>=?", (branch_id, d0.isoformat())).fetchone()
        has_basis = con.execute(f"SELECT 1 FROM bc_stock_count WHERE branch_id=? AND COALESCE(status,'submitted')='submitted' AND kind IN {_in(MONTH_KINDS)}"
                                " AND count_date<?", (branch_id, d0.isoformat())).fetchone()
        if not has_close and has_basis:
            plan["partial_month"], plan["partial_date"] = m, d0.isoformat()
    return plan


def cycle_save(con, branch_id: int, lines: list, who: str, submit: bool = False, count_date: str = None,
               note: str = None, require_all: bool = True) -> dict:
    """The weekly count of the class-A items: a draft saved as often as the branch likes, submitted once and never edited (a recount is a
    new count). It does not make a period; the system stock takes the figure as the newer basis of that item (system_position) and the
    next partial month close carries it (partial_close)."""
    _branch_name(con, branch_id)
    if not cycle_on(con):
        raise ValueError("การตรวจนับหมุนเวียนปิดอยู่ · ตรวจนับครบทุกรายการ")
    if last_submitted(con, branch_id) is None:
        raise ValueError("ต้องตรวจนับครบทุกรายการเป็นต้นงวดครั้งแรกก่อน จึงตรวจนับหมุนเวียนได้")
    conv = Converter(con)
    cdate = count_date or bkk_today().isoformat()
    try:
        d = date.fromisoformat(cdate)
    except ValueError:
        raise ValueError("วันที่ตรวจนับต้องอยู่ในรูปแบบ YYYY-MM-DD")
    if d > bkk_today():
        raise ValueError("วันที่ตรวจนับต้องไม่เป็นวันในอนาคต")
    dr = cycle_draft_of(con, branch_id)
    if dr:
        cid = dr["count_id"]
        con.execute("UPDATE bc_cycle_count SET updated_at=?, counted_by=?, count_date=?, note=COALESCE(?, note) WHERE cycle_id=?",
                    (now(), who, cdate, note, cid))
    else:
        cid = con.execute("INSERT INTO bc_cycle_count(branch_id, count_date, status, counted_by, note, created_at, updated_at)"
                          " VALUES(?,?, 'draft', ?,?,?,?)", (branch_id, cdate, who, note, now(), now())).lastrowid
    bad = []
    _write_lines(con, conv, "bc_cycle_count_line", "cycle_id", cid, lines, bad)
    n = con.execute("SELECT COUNT(*) FROM bc_cycle_count_line WHERE cycle_id=?", (cid,)).fetchone()[0]
    out = {"count_id": cid, "kind": "cycle", "lines": n, "status": "draft", "unconverted": bad}
    if submit:
        if not n:
            raise ValueError("ยังไม่มีรายการที่ตรวจนับ")
        if require_all:
            have = {c for (c,) in con.execute("SELECT ingredient_code FROM bc_cycle_count_line WHERE cycle_id=? AND qty_base IS NOT NULL", (cid,))}
            miss = [it["name"] for z in sheet(con, branch_id, mode="cycle")["zones"] for it in z["items"] if it["code"] not in have]
            if miss:
                raise ValueError(f"ยังมีรายการที่ต้องนับ {len(miss)} รายการ ที่ยังไม่ได้นับ (เช่น {', '.join(miss[:3])}) นับให้ครบก่อนส่ง")
        bad = [_no_unit(con, c, u) for c, u in con.execute(
            "SELECT ingredient_code, unit FROM bc_cycle_count_line WHERE cycle_id=? AND qty_base IS NULL", (cid,))]
        if bad:
            raise ValueError("บันทึกไม่ได้: " + "; ".join(bad[:3]) + " · แจ้งฝ่ายวิเคราะห์")
        con.execute("UPDATE bc_cycle_count SET status='submitted', submitted_at=? WHERE cycle_id=?", (now(), cid))
        audit(con, who, "cycle_submit", "bc_cycle_count", str(cid), None, {"branch_id": branch_id, "date": cdate, "lines": n})
        out["status"] = "submitted"
    return out


def cycle_discard(con, branch_id: int, who: str) -> bool:
    dr = cycle_draft_of(con, branch_id)
    if not dr:
        return False
    con.execute("DELETE FROM bc_cycle_count_line WHERE cycle_id=?", (dr["count_id"],))
    con.execute("DELETE FROM bc_cycle_count WHERE cycle_id=?", (dr["count_id"],))
    audit(con, who, "cycle_discard", "bc_cycle_count", str(dr["count_id"]), None, None)
    return True


def cycle_history(con, branch_id: int, limit: int = 20) -> list:
    rows = con.execute(
        "SELECT c.cycle_id, c.count_date, c.status, c.counted_by, c.submitted_at, COUNT(l.ingredient_code)"
        " FROM bc_cycle_count c LEFT JOIN bc_cycle_count_line l USING(cycle_id) WHERE c.branch_id=?"
        " GROUP BY c.cycle_id ORDER BY c.count_date DESC, c.cycle_id DESC LIMIT ?", (branch_id, limit)).fetchall()
    return [dict(zip(("count_id", "count_date", "status", "counted_by", "submitted_at", "lines"), r), kind="cycle") for r in rows]


def _cycle_rebase(con, branch_id: int, basis_id: int, cdate: str, as_of: date, through: date, estimated_days: list, estimate: bool) -> dict:
    """{code: (system quantity, date of the weekly count, estimated use)} for the items counted by a weekly count since the month-end
    basis: the latest weekly figure + goods received from its date to `as_of` - confirmed slips from its date to `through` - (with
    `estimate`) the BoM use of the days since that have sales and no slip. Same rules as the month-end basis, started later."""
    if not cycle_on(con):
        return {}
    rows = con.execute("SELECT c.count_date, c.cycle_id, l.ingredient_code, l.qty_base FROM bc_cycle_count c JOIN bc_cycle_count_line l USING(cycle_id)"
                       " WHERE c.branch_id=? AND c.status='submitted' AND c.count_date>=? AND c.count_date<=? AND l.qty_base IS NOT NULL"
                       " ORDER BY c.count_date, c.cycle_id", (branch_id, cdate, as_of.isoformat())).fetchall()
    if not rows:
        return {}
    counted_on_basis = {c for (c,) in con.execute("SELECT ingredient_code FROM bc_stock_count_line WHERE count_id=? AND basis IS NULL", (basis_id,))}
    latest = {}
    for cd, _cid, c, q in rows:
        if cd == cdate and c in counted_on_basis:           # a full count of the same day is the count
            continue
        latest[c] = (cd, q)
    by_date = {}
    for c, (cd, q) in latest.items():
        by_date.setdefault(cd, {})[c] = q
    out = {}
    for cd, items in by_date.items():
        codes = list(items)
        marks = ",".join("?" * len(codes))
        rec = {c: q or 0.0 for c, q in con.execute(
            "SELECT l.ingredient_code, SUM(l.qty_base) FROM bc_receipt r JOIN bc_receipt_line l USING(receipt_id)"
            f" WHERE r.branch_id=? AND r.receipt_date>=? AND r.receipt_date<=? AND l.ingredient_code IN ({marks}) GROUP BY l.ingredient_code",
            (branch_id, cd, as_of.isoformat(), *codes))}
        iss = {c: q or 0.0 for c, q in con.execute(
            "SELECT l.ingredient_code, SUM(COALESCE(l.qty_used_base, 0) + COALESCE(l.qty_waste_base, 0))"
            " FROM bc_issue_slip s JOIN bc_issue_line l USING(slip_id) WHERE s.branch_id=? AND s.status='confirmed'"
            f" AND s.slip_date>=? AND s.slip_date<=? AND l.ingredient_code IN ({marks}) GROUP BY l.ingredient_code",
            (branch_id, cd, through.isoformat(), *codes))}
        est = {}
        if estimate and estimated_days:
            ds = [d for d in estimated_days if d >= cd]
            if ds:
                est = estimated_use(con, branch_id, ds)[1]
        for c, q in items.items():
            e = est.get(c, 0.0)
            out[c] = (q + rec.get(c, 0.0) - iss.get(c, 0.0) - e, cd, e)
    return out


def partial_close(con, branch_id: int, month: str, who: str, today: date = None, accept_missing_slips: bool = False) -> dict:
    """Close `month` (YYYY-MM) of a branch on a PARTIAL end count dated the 1st of the next month: every item of the system stock
    at the end of the last day of the month (confirmed slips only, as the month-end reconciliation and the accounting summary read
    them), the class-A items from their latest weekly count carried forward. Idempotent: a month-end count already dated within
    two days of the 1st is returned as it is. Refused for a quarter-end month (a full count), while the switch is off, before the
    1st, with no earlier count, and - unless `accept_missing_slips` - when a selling day of the period has no confirmed slip, since
    the items not counted would then show less use than there was.

    -> {"count_id", "date", "created", "lines", "counted", "system", "missing_slip_days"}"""
    today = today or bkk_today()
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month or ""):
        raise ValueError("เดือนต้องอยู่ในรูปแบบ YYYY-MM")
    if not cycle_on(con):
        raise ValueError("การตรวจนับหมุนเวียนปิดอยู่ · ปิดงวดด้วยการตรวจนับครบทุกรายการ")
    if is_quarter_end(month):
        raise ValueError(f"เดือน{_month_th(month)}เป็นสิ้นไตรมาส ต้องตรวจนับครบทุกรายการ")
    d = next_month_first(month)
    if d > today:
        raise ValueError(f"ปิดงวดได้ตั้งแต่วันที่ {_fmt_day(d.isoformat())}")
    _branch_name(con, branch_id)
    near = con.execute(f"SELECT count_id, count_date, COALESCE(partial,0) FROM bc_stock_count WHERE branch_id=? AND COALESCE(status,'submitted')='submitted'"
                       f" AND kind IN {_in(MONTH_KINDS)} AND count_date>=? AND count_date<=? ORDER BY count_date, count_id LIMIT 1",
                       (branch_id, d.isoformat(), (d + timedelta(days=PARTIAL_TOLERANCE_DAYS)).isoformat())).fetchone()
    if near:
        return {"count_id": near[0], "date": near[1], "created": False, "partial": bool(near[2]), "lines": None}
    if con.execute(f"SELECT 1 FROM bc_stock_count WHERE branch_id=? AND COALESCE(status,'submitted')='submitted' AND kind IN {_in(MONTH_KINDS)}"
                   " AND count_date>?", (branch_id, d.isoformat())).fetchone():
        raise ValueError("มีผลตรวจนับหลังวันที่ปิดงวดแล้ว จึงปิดงวดย้อนหลังด้วยการนับหมุนเวียนไม่ได้")
    last = d - timedelta(days=1)
    sp = system_position(con, branch_id, last, issues_through=last, estimate=False)
    if not sp["basis"]:
        raise ValueError("ยังไม่มีผลตรวจนับครั้งแรกของสาขานี้ ต้องตรวจนับครบทุกรายการก่อน")
    from .issue_reports import slip_coverage
    cov = slip_coverage(con, branch_id, sp["basis"]["date"], d.isoformat())
    gaps = cov["missing_days"] + cov["draft_days"]
    if gaps and not accept_missing_slips:
        raise ValueError(f"ยังมีวันขายที่ไม่มีใบตัดเบิกที่ยืนยัน {len(gaps)} วัน (เช่น {_fmt_day(gaps[0])}) รายการที่ไม่ได้นับจะแสดงการใช้น้อยกว่าจริง "
                         "ให้สาขายืนยันใบตัดเบิกก่อน หรือยืนยันปิดงวดทั้งที่ยังขาดใบ")
    cycle = sp.get("cycle") or {}
    cur = con.execute("INSERT INTO bc_stock_count(branch_id, count_date, kind, counted_by, note, created_at, status, updated_at, submitted_at, partial)"
                      " VALUES(?,?,'end',?,?,?, 'submitted', ?,?, 1)",
                      (branch_id, d.isoformat(), who, PARTIAL_NOTE, now(), now(), now()))
    cid = cur.lastrowid
    conv = Converter(con)
    rows = []
    for c, q in sorted(sp["qty"].items()):
        rows.append((cid, c, max(float(q), 0.0), conv.base_unit(c), max(float(q), 0.0), "cycle" if c in cycle else "system"))
    con.executemany("INSERT INTO bc_stock_count_line(count_id, ingredient_code, qty_kitchen, unit, qty_base, basis) VALUES(?,?,?,?,?,?)", rows)
    n_cycle = sum(1 for r in rows if r[5] == "cycle")
    audit(con, who, "partial_close", "bc_stock_count", str(cid), None,
          {"branch_id": branch_id, "month": month, "date": d.isoformat(), "lines": len(rows), "counted": n_cycle, "missing_slip_days": len(gaps)})
    return {"count_id": cid, "date": d.isoformat(), "created": True, "partial": True, "lines": len(rows), "counted": n_cycle,
            "system": len(rows) - n_cycle, "missing_slip_days": gaps}


def _month_th(month: str) -> str:
    from .fmt import month_th
    return month_th(month)


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
