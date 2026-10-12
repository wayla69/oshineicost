# -*- coding: utf-8 -*-
"""bom_center.aliases — a new code from another system is an ALIAS of the register item (PR-Y).

The code the recipes use (bc_ingredient.code) never changes. When the accounting system, the warehouse or a supplier gives an
existing item a NEW code, the new code is recorded as an alias of the item, so no recipe needs an edit and every reader that
turns an outside code into a register code (the price import, the central report, the forecast) lands on the same item.

  * bc_ingredient_alias   one row per outside code: ingredient_code, system (acc / warehouse / supplier / other), valid_from,
                          active (0 = retired, never deleted), is_current (the code to USE when writing to that system)
  * bc_code_request       Sales Support reports a new code; the analyst applies it (it becomes an alias, current for its
                          system) or rejects it with a reason. The same approve / reject pattern as the pack-size reports.

Nothing in the register changes without the analyst: an import run by someone who may not decide files a request instead.
"""
import sqlite3
from datetime import date

from .clock import today as bkk_today
from .codes import suggest
from .fmt import day_th
from .master import audit, now
from .units import norm

SYSTEMS = (("acc", "ระบบบัญชี"), ("warehouse", "ระบบคลัง"), ("supplier", "ผู้จำหน่าย"), ("other", "ระบบอื่น"))
SYSTEM_TH = dict(SYSTEMS)
SYSTEM_TH["old-recipe"] = "สูตรเดิม"                       # rows written before PR-Y may carry it
STATUS_TH = {"pending": "รอฝ่ายวิเคราะห์ตรวจ", "applied": "ใช้แล้ว", "rejected": "ไม่ใช้"}
UNKNOWN_WHY = "ไม่มีในทะเบียนวัตถุดิบ"                       # prices.aggregate marks a line whose code nobody knows with this


def _code(v) -> str:
    return str(v if v is not None else "").strip()


def _system(v, default="acc") -> str:
    s = _code(v) or default
    if s not in SYSTEM_TH or s == "old-recipe":
        raise ValueError("ระบบต้องเป็น ระบบบัญชี / ระบบคลัง / ผู้จำหน่าย / ระบบอื่น")
    return s


def _iso(v):
    """A date typed as 2026-10-01 (or 1/10/2026) -> ISO, None when empty; anything else is refused."""
    s = _code(v)
    if not s:
        return None
    try:
        return date.fromisoformat(s[:10]).isoformat()
    except ValueError:
        pass
    try:
        d, m, y = (int(x) for x in s.split("/"))
        return date(y, m, d).isoformat()
    except ValueError:
        raise ValueError("วันที่เริ่มใช้ไม่ถูกต้อง · ใช้รูปแบบ 2026-10-01")


def _name(con, code: str):
    r = con.execute("SELECT name_th FROM bc_ingredient WHERE code=?", (code,)).fetchone()
    return r[0] if r else None


# ── reading ──────────────────────────────────────────────────────────────────
def active_map(con) -> dict:
    """{outside code: register code} of the aliases in use. A retired alias (active=0) no longer resolves."""
    try:
        return dict(con.execute("SELECT alias_code, ingredient_code FROM bc_ingredient_alias WHERE COALESCE(active,1)=1"))
    except sqlite3.OperationalError:                         # a database the migrations have not reached yet: every alias is in use
        return dict(con.execute("SELECT alias_code, ingredient_code FROM bc_ingredient_alias"))


def resolve(con, code, known: set = None, amap: dict = None) -> str:
    """An outside code -> the register code (itself when it is one, else through an active alias, else unchanged)."""
    c = _code(code)
    if known is None:
        known = {r[0] for r in con.execute("SELECT code FROM bc_ingredient")}
    if c in known:
        return c
    return (amap if amap is not None else active_map(con)).get(c, c)


def current_code(con, item: str, system: str = "acc", on=None) -> str:
    """The code to USE for `item` when writing to `system`: the current alias that has started, else the register code."""
    on = (on or bkk_today()).isoformat() if not isinstance(on, str) else on
    ok = [(vf or "", ac) for ac, vf in con.execute(
        "SELECT alias_code, valid_from FROM bc_ingredient_alias WHERE ingredient_code=? AND system=?"
        " AND COALESCE(active,1)=1 AND COALESCE(is_current,0)=1", (item, system)) if not vf or vf <= on]
    return max(ok)[1] if ok else item


def current_map(con, system: str = "acc", on=None) -> dict:
    """{register code: the code in use for `system`} for the items whose current code differs from the register code."""
    on = (on or bkk_today()).isoformat() if not isinstance(on, str) else on
    best = {}
    for item, ac, vf in con.execute(
            "SELECT ingredient_code, alias_code, valid_from FROM bc_ingredient_alias WHERE system=?"
            " AND COALESCE(active,1)=1 AND COALESCE(is_current,0)=1", (system,)):
        if vf and vf > on:
            continue
        if item not in best or (vf or "") >= best[item][0]:
            best[item] = (vf or "", ac)
    known = {r[0] for r in con.execute("SELECT code FROM bc_ingredient")}
    return {i: a for i, (_v, a) in best.items() if i in known and a != i}


def in_use(con, on=None) -> set:
    """The alias codes that are the code to USE (for their item and system) today."""
    on = (on or bkk_today()).isoformat() if not isinstance(on, str) else on
    best = {}
    for item, system, ac, vf in con.execute(
            "SELECT ingredient_code, system, alias_code, valid_from FROM bc_ingredient_alias"
            " WHERE COALESCE(active,1)=1 AND COALESCE(is_current,0)=1"):
        if vf and vf > on:
            continue
        k = (item, system)
        if k not in best or (vf or "") >= best[k][0]:
            best[k] = (vf or "", ac)
    return {a for _v, a in best.values()}


def aliases_of(con, code: str) -> dict:
    """What the register row shows under "รหัสในระบบอื่น": every alias of the item with its state, and the code in use per system."""
    code = _code(code)
    if not con.execute("SELECT 1 FROM bc_ingredient WHERE code=?", (code,)).fetchone():
        raise ValueError(f"ไม่พบรหัส {code} ในทะเบียน")
    cur_by = {s: current_code(con, code, s) for s, _ in SYSTEMS}
    rows = []
    for ac, system, vf, active, is_cur, note, by, at in con.execute(
            "SELECT alias_code, system, valid_from, COALESCE(active,1), COALESCE(is_current,0), note, created_by, created_at"
            " FROM bc_ingredient_alias WHERE ingredient_code=? ORDER BY COALESCE(active,1) DESC, system, alias_code", (code,)):
        rows.append({"alias": ac, "system": system, "system_th": SYSTEM_TH.get(system, system or ""), "valid_from": vf,
                     "active": bool(active), "is_current": bool(is_cur), "in_use": bool(active) and cur_by.get(system) == ac,
                     "note": note, "created_by": by, "created_at": at})
    return {"code": code, "name": _name(con, code), "aliases": rows,
            "current": [{"system": s, "system_th": t, "code": cur_by[s], "is_alias": cur_by[s] != code} for s, t in SYSTEMS]}


# ── name matching ────────────────────────────────────────────────────────────
_WEIGHT, _VOLUME = {"กรัม", "กิโลกรัม", "ขีด"}, {"มิลลิลิตร", "ลิตร"}


def _family(unit) -> str:
    u = norm(str(unit or "").split(",")[0])
    return "weight" if u in _WEIGHT else "volume" if u in _VOLUME else u


def suggest_items(con, name, code=None, n: int = 3, unit=None) -> list:
    """The register items a name could belong to, best first: [{code, name, score, base_unit, same_unit}]. The name similarity is
    codes.suggest's; an item bought in the same kind of unit (weight, volume, the same piece word) ranks above an equal match."""
    master = {c: (nm, k) for c, nm, k in con.execute("SELECT code, name_th, kind FROM bc_ingredient WHERE COALESCE(active,1)=1")}
    base = dict(con.execute("SELECT code, base_unit FROM bc_ingredient"))
    fam = _family(unit) if unit else None
    out = []
    for h in suggest(master, name or "", exclude=_code(code) or None, n=12):
        same = (_family(base.get(h["code"])) == fam) if fam else None
        out.append({**h, "base_unit": base.get(h["code"]), "same_unit": same,
                    "_rank": h["score"] + (0.05 if same else 0.0)})
    out.sort(key=lambda h: (-h["_rank"], h["code"]))
    for h in out:
        h.pop("_rank")
    return out[:n]


# ── checks ───────────────────────────────────────────────────────────────────
def check_new_code(con, new_code: str, item: str) -> str:
    """The new code must be free: not a register code, not an alias of another item. Returns it cleaned, or raises with the reason."""
    c = _code(new_code)
    if not c or c in ("#N/A", "None"):
        raise ValueError("ระบุรหัสใหม่")
    if len(c) > 40:
        raise ValueError("รหัสใหม่ยาวเกินไป (ไม่เกิน 40 ตัวอักษร)")
    if c == item:
        raise ValueError("รหัสใหม่ต้องไม่ซ้ำกับรหัสในทะเบียนของรายการนี้")
    nm = _name(con, c)
    if nm is not None or con.execute("SELECT 1 FROM bc_ingredient WHERE code=?", (c,)).fetchone():
        raise ValueError(f"รหัส {c} เป็นรหัสของ {nm or 'วัตถุดิบอื่น'} ในทะเบียนอยู่แล้ว")
    r = con.execute("SELECT ingredient_code, COALESCE(active,1) FROM bc_ingredient_alias WHERE alias_code=?", (c,)).fetchone()
    if r and r[0] != item:
        on = "เป็นรหัสในระบบอื่นของ" if r[1] else "เคยเป็นรหัสในระบบอื่นของ (เลิกใช้แล้ว)"
        raise ValueError(f"รหัส {c} {on} {_name(con, r[0]) or r[0]} อยู่แล้ว")
    if r and r[0] == item and r[1]:
        raise ValueError(f"รหัส {c} เป็นรหัสในระบบอื่นของรายการนี้อยู่แล้ว")
    return c


# ── writing (the caller commits) ─────────────────────────────────────────────
def add_alias(con, item: str, alias_code: str, system: str, valid_from, note, who: str, make_current: bool = True,
              created_by: str = None) -> dict:
    """Record `alias_code` as an outside code of the register item (or bring a retired one of the same item back). The recipes
    are not touched. `make_current` makes it the code to use for `system`."""
    item = _code(item)
    if not con.execute("SELECT 1 FROM bc_ingredient WHERE code=?", (item,)).fetchone():
        raise ValueError(f"ไม่พบรหัส {item} ในทะเบียน")
    code = check_new_code(con, alias_code, item)
    system = _system(system)
    vf = _iso(valid_from)
    note = _code(note) or None
    old = con.execute("SELECT 1 FROM bc_ingredient_alias WHERE alias_code=?", (code,)).fetchone()
    t = now()
    if old:
        con.execute("UPDATE bc_ingredient_alias SET active=1, system=?, valid_from=?, note=COALESCE(?, note) WHERE alias_code=?",
                    (system, vf, note, code))
    else:
        con.execute("INSERT INTO bc_ingredient_alias(alias_code, ingredient_code, system, note, valid_from, active, is_current,"
                    " created_by, created_at) VALUES(?,?,?,?,?,1,0,?,?)", (code, item, system, note, vf, created_by or who, t))
    audit(con, who, "code_alias_add", "bc_ingredient_alias", code, None,
          {"item": item, "system": system, "valid_from": vf, "reactivated": bool(old)})
    if make_current:
        set_current(con, code, who)
    return {"alias": code, "item": item, "system": system, "valid_from": vf, "reactivated": bool(old)}


def set_current(con, alias_code: str, who: str) -> dict:
    """Make an alias the code to USE for its system. The one in use before stops being current, unless the new code starts on a
    later date (then it stays current until that day). Caller commits."""
    code = _code(alias_code)
    r = con.execute("SELECT ingredient_code, system, valid_from, COALESCE(active,1) FROM bc_ingredient_alias WHERE alias_code=?",
                    (code,)).fetchone()
    if not r:
        raise ValueError(f"ไม่พบรหัสในระบบอื่น {code}")
    item, system, vf, active = r
    if not active:
        raise ValueError(f"รหัส {code} เลิกใช้แล้ว · เพิ่มรหัสนี้ใหม่ก่อนตั้งเป็นรหัสปัจจุบัน")
    if not vf or vf <= bkk_today().isoformat():
        con.execute("UPDATE bc_ingredient_alias SET is_current=0 WHERE ingredient_code=? AND system=? AND alias_code<>?",
                    (item, system, code))
    con.execute("UPDATE bc_ingredient_alias SET is_current=1 WHERE alias_code=?", (code,))
    audit(con, who, "code_alias_current", "bc_ingredient_alias", code, None, {"item": item, "system": system})
    return {"alias": code, "item": item, "system": system}


def use_register_code(con, item: str, system: str, who: str) -> dict:
    """The register code itself is the code to use for `system` again (no alias is current). Caller commits."""
    item, system = _code(item), _system(system)
    if not con.execute("SELECT 1 FROM bc_ingredient WHERE code=?", (item,)).fetchone():
        raise ValueError(f"ไม่พบรหัส {item} ในทะเบียน")
    n = con.execute("UPDATE bc_ingredient_alias SET is_current=0 WHERE ingredient_code=? AND system=? AND COALESCE(is_current,0)=1",
                    (item, system)).rowcount
    audit(con, who, "code_alias_current", "bc_ingredient_alias", f"{item}|{system}", None, {"item": item, "system": system, "alias": None})
    return {"item": item, "system": system, "cleared": n}


def retire(con, alias_code: str, who: str) -> dict:
    """Stop using an alias (active=0). The row stays: it is history, and it can be brought back. Caller commits."""
    code = _code(alias_code)
    r = con.execute("SELECT ingredient_code, system, COALESCE(active,1) FROM bc_ingredient_alias WHERE alias_code=?", (code,)).fetchone()
    if not r:
        raise ValueError(f"ไม่พบรหัสในระบบอื่น {code}")
    if not r[2]:
        raise ValueError(f"รหัส {code} เลิกใช้แล้ว")
    con.execute("UPDATE bc_ingredient_alias SET active=0, is_current=0 WHERE alias_code=?", (code,))
    audit(con, who, "code_alias_retire", "bc_ingredient_alias", code, {"item": r[0], "system": r[1]}, None)
    return {"alias": code, "item": r[0], "system": r[1]}


# ── requests: Sales Support asks, the analyst decides ────────────────────────
def request(con, new_code, system, ingredient_code, who: str, old_code=None, name=None, valid_from=None, note=None) -> dict:
    """Sales Support: this register item has a new code in `system`. Writes ONE pending bc_code_request; the register is untouched.
    The same request twice is the same request."""
    item = _code(ingredient_code)
    if not item:
        raise ValueError("เลือกวัตถุดิบที่ใช้รหัสใหม่นี้")
    if not con.execute("SELECT 1 FROM bc_ingredient WHERE code=?", (item,)).fetchone():
        raise ValueError(f"ไม่พบรหัส {item} ในทะเบียน")
    system = _system(system)
    code = check_new_code(con, new_code, item)
    vf = _iso(valid_from)
    old = _code(old_code) or current_code(con, item, system)
    if old == code:
        raise ValueError("รหัสเดิมกับรหัสใหม่ต้องเป็นคนละรหัส")
    mine = {item} | {r[0] for r in con.execute("SELECT alias_code FROM bc_ingredient_alias WHERE ingredient_code=?", (item,))}
    if old not in mine:
        raise ValueError(f"รหัสเดิม {old} ไม่ใช่รหัสของ {_name(con, item) or item} · ตรวจรหัสเดิมหรือเลือกวัตถุดิบใหม่")
    dup = con.execute("SELECT request_id, ingredient_code FROM bc_code_request WHERE status='pending' AND new_code=?", (code,)).fetchone()
    if dup:
        if dup[1] == item:
            return {"request_id": dup[0], "duplicate": True, "message": "✓ มีคำขอรหัสนี้รออยู่แล้ว ฝ่ายวิเคราะห์จะตรวจ"}
        raise ValueError(f"มีคำขอรหัส {code} สำหรับ {_name(con, dup[1]) or dup[1]} รออยู่แล้ว · ให้ฝ่ายวิเคราะห์ตัดสินใจก่อน")
    cur = con.execute(
        "INSERT INTO bc_code_request(old_code, new_code, system, name, ingredient_code, valid_from, note, status, requested_by,"
        " requested_at) VALUES(?,?,?,?,?,?,?, 'pending', ?, ?)",
        (old, code, system, _code(name) or None, item, vf, _code(note) or None, who, now()))
    audit(con, who, "code_request", "bc_code_request", str(cur.lastrowid), None,
          {"old": old, "new": code, "item": item, "system": system, "valid_from": vf})
    return {"request_id": cur.lastrowid, "duplicate": False,
            "message": "✓ แจ้งเปลี่ยนรหัสแล้ว ฝ่ายวิเคราะห์จะตรวจและบันทึกเป็นรหัสในระบบอื่นของวัตถุดิบนี้"}


def _row(r) -> dict:
    return dict(zip(("request_id", "old_code", "new_code", "system", "name", "ingredient_code", "valid_from", "note", "status",
                     "requested_by", "requested_at", "decided_by", "decided_at", "decision_note"), r))


def listing(con, status: str = "pending", limit: int = 200) -> list:
    sql = ("SELECT request_id, old_code, new_code, system, name, ingredient_code, valid_from, note, status, requested_by,"
           " requested_at, decided_by, decided_at, decision_note FROM bc_code_request")
    args = []
    if status and status != "all":
        sql += " WHERE status=?"
        args.append(status)
    names = dict(con.execute("SELECT code, name_th FROM bc_ingredient"))
    out = []
    for r in con.execute(sql + " ORDER BY request_id DESC LIMIT ?", args + [limit]):
        d = _row(r)
        d.update(item_name=names.get(d["ingredient_code"]), system_th=SYSTEM_TH.get(d["system"], d["system"]),
                 status_th=STATUS_TH.get(d["status"], d["status"]),
                 valid_from_th=day_th(d["valid_from"]) if d["valid_from"] else None)
        out.append(d)
    return out


def pending_count(con) -> int:
    return con.execute("SELECT COUNT(*) FROM bc_code_request WHERE status='pending'").fetchone()[0]


def decide(con, request_id: int, decision: str, note, who: str) -> dict:
    """The analyst's answer. 'applied' records the new code as an alias, current for its system (the one in use before stops being
    current), and audits it; 'rejected' needs a reason Sales Support can read. Caller commits."""
    if decision not in ("applied", "rejected"):
        raise ValueError("ตัดสินใจไม่ถูกต้อง · ใช้ หรือ ไม่ใช้")
    r = con.execute("SELECT request_id, old_code, new_code, system, ingredient_code, valid_from, note, status, requested_by"
                    " FROM bc_code_request WHERE request_id=?", (int(request_id),)).fetchone()
    if not r:
        raise ValueError("ไม่พบคำขอ")
    rid, old, new, system, item, vf, rnote, status, by = r
    if status != "pending":
        raise ValueError("คำขอนี้มีผู้ตัดสินใจไปแล้ว")
    note = _code(note) or None
    if decision == "rejected" and not note:
        raise ValueError("ไม่ใช้ค่าที่แจ้งต้องระบุเหตุผล เพื่อให้ประสานงานขายอ่านได้")
    out = {"decision": decision, "request_id": rid, "code": item, "alias": new}
    if decision == "applied":
        a = add_alias(con, item, new, system, vf, rnote, who, make_current=True, created_by=by)
        audit(con, who, "code_request_apply", "bc_code_request", str(rid), old, new)
        out.update(alias=a["alias"], message=f"✓ บันทึก {new} เป็นรหัสใหม่ของ {_name(con, item) or item} แล้ว · สูตรไม่ต้องแก้")
    else:
        audit(con, who, "code_request_reject", "bc_code_request", str(rid), None, {"note": note})
        out["message"] = "✓ บันทึกว่าไม่ใช้รหัสที่แจ้งแล้ว"
    con.execute("UPDATE bc_code_request SET status=?, decided_by=?, decided_at=?, decision_note=? WHERE request_id=?",
                (decision, who, now(), note, rid))
    return out


# ── the unknown codes of an import ───────────────────────────────────────────
def unknown_in(con, rows: list, code_key="code", name_key="name", unit_key="unit") -> list:
    """The codes of an import file that are neither register codes nor aliases: [{code, name, unit, lines}] in file order."""
    known = {r[0] for r in con.execute("SELECT code FROM bc_ingredient")}
    amap = active_map(con)
    seen, out = {}, []
    for ln in rows:
        c = _code(ln.get(code_key))
        if not c or c in ("#N/A", "None") or c in known or c in amap:
            continue
        if c not in seen:
            seen[c] = {"code": c, "name": ln.get(name_key), "unit": ln.get(unit_key), "lines": 0}
            out.append(seen[c])
        seen[c]["lines"] += 1
        seen[c]["name"] = seen[c]["name"] or ln.get(name_key)
    return out


def with_suggestions(con, unknown: list, n: int = 3, limit: int = 100) -> list:
    """Each unknown code with its best register matches (the first `limit` only: matching is by name and costs a pass over the register)."""
    out = []
    for i, u in enumerate(unknown):
        d = dict(u)
        d["suggestions"] = suggest_items(con, u.get("name"), code=u["code"], n=n, unit=u.get("unit")) if i < limit else []
        out.append(d)
    return out


def choices_from(raw) -> dict:
    """The JSON a screen sends -> {code: {'as': 'alias'|'new', 'item': register code}}. None means "no choices made" (the
    old behaviour of the caller); an empty object is a strict run in which nothing was chosen."""
    if raw in (None, ""):
        return None
    import json
    try:
        d = json.loads(raw) if isinstance(raw, str) else raw
    except ValueError:
        raise ValueError("คำขอไม่ถูกต้อง")
    if not isinstance(d, dict):
        raise ValueError("คำขอไม่ถูกต้อง")
    out = {}
    for code, ch in d.items():
        if not isinstance(ch, dict) or ch.get("as") not in ("alias", "new"):
            raise ValueError(f"{code}: เลือกว่าเป็นรหัสใหม่ของวัตถุดิบเดิม หรือเป็นวัตถุดิบใหม่")
        if ch["as"] == "alias" and not _code(ch.get("item")):
            raise ValueError(f"{code}: เลือกวัตถุดิบเดิมที่ใช้รหัสใหม่นี้")
        out[_code(code)] = {"as": ch["as"], "item": _code(ch.get("item"))}
    return out


def apply_choices(con, unknown: list, choices: dict, who: str, can_alias: bool, system: str = "acc", note: str = None,
                  add_new=None) -> dict:
    """The person's answer for each unknown code of an import. An alias is written at once (current for `system`) when the
    importer may decide; otherwise it becomes a pending request for the analyst. 'new' calls `add_new(code, row)` when the caller
    registers new items itself. -> {aliased, requested, new, open} lists of codes; `open` = codes with no answer."""
    out = {"aliased": [], "requested": [], "new": [], "open": []}
    for u in unknown:
        ch = (choices or {}).get(u["code"])
        if not ch:
            out["open"].append(u["code"])
        elif ch["as"] == "new":
            if add_new:
                add_new(u["code"], u)
            out["new"].append(u["code"])
        elif can_alias:
            add_alias(con, ch["item"], u["code"], system, None, note or "จากไฟล์นำเข้า", who, make_current=True)
            out["aliased"].append(u["code"])
        else:
            request(con, u["code"], system, ch["item"], who, name=u.get("name"), note=note or "จากไฟล์นำเข้า")
            out["requested"].append(u["code"])
    return out


# ── the code column of an export ─────────────────────────────────────────────
REGISTER_HEADER = "รหัสในทะเบียน"
RECODE_NOTE = " · คอลัมน์ รหัส เป็นรหัสที่ใช้ในระบบบัญชี ส่วนรหัสในทะเบียนอยู่คอลัมน์สุดท้ายของตาราง"


def _code_column(ws):
    return next((c.column for c in ws[2] if c.value == "รหัส"), None) if ws.max_row >= 2 else None


def recode_workbook(wb, cur: dict) -> int:
    """Files for the accounting system: in every worksheet whose header row (row 2) has a column "รหัส", show the code the
    accounting system uses now (`cur` = current_map(con, 'acc')) and keep the register code in a last column "รหัสในทะเบียน".
    A file in which no item has a new code is left exactly as it was (no extra column).
    -> how many cells show a different code than the register's."""
    from copy import copy
    if not cur or not any(isinstance(ws.cell(row=r, column=_code_column(ws)).value, str)
                          and ws.cell(row=r, column=_code_column(ws)).value in cur
                          for ws in wb.worksheets if _code_column(ws) for r in range(3, ws.max_row + 1)):
        return 0
    changed = 0
    for ws in wb.worksheets:
        col = _code_column(ws)
        if not col:
            continue
        new = ws.max_column + 1
        head, last = ws.cell(row=2, column=new), ws.cell(row=2, column=new - 1)
        head.value = REGISTER_HEADER
        if last.has_style:
            head.font, head.fill, head.alignment, head.border = copy(last.font), copy(last.fill), copy(last.alignment), copy(last.border)
        for r in range(3, ws.max_row + 1):
            v = ws.cell(row=r, column=col).value
            if isinstance(v, str) and v.strip():
                ws.cell(row=r, column=new).value = v
                if v in cur:
                    ws.cell(row=r, column=col).value = cur[v]
                    changed += 1
        ws.column_dimensions[head.column_letter].width = 18
        if isinstance(ws["A1"].value, str):
            ws["A1"].value += RECODE_NOTE
    return changed
