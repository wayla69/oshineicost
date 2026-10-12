# -*- coding: utf-8 -*-
"""bom_center.pack_reports — "ขนาดบรรจุไม่ตรง" (PR-U2).

While receiving, a branch sees a pack that is not the size the register says ("1 กล่อง = 10 แพ็ค" but the box holds 12).
It reports the real size from the receive form. The report

  * never blocks or changes the receipt — the goods are received as they always were;
  * is checked at once with units.check_ladder on the item's ladder WITH the change applied, so the branch is told in
    plain words when the pair cannot be a ladder (a loop, a 4th step, a parent that is not in the ladder);
  * waits for the analyst, who applies it (the step is replaced through master.set_ladder, so every reader follows and the
    pack size of the receiving unit follows too) or rejects it with a reason the branch can read on its receive history.

Several branches reporting the same real size are one decision: reports are grouped by (item, unit, reported size).
"""
from datetime import datetime, timezone

from .master import _ladder_rows, audit, set_ladder
from .units import Converter, check_ladder, fmt_num, metric_units, norm, unit_word

STATUS_TH = {"pending": "รอฝ่ายวิเคราะห์ตรวจ", "applied": "ใช้ค่าที่แจ้งแล้ว", "rejected": "ไม่ใช้"}


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _num(v):
    try:
        return float(str(v).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


_EASIER_PARENT = {"กรัม": "กิโลกรัม", "มิลลิลิตร": "ลิตร"}      # what a person weighs or measures a pack in


def default_parent(base: str, ladder: list, unit: str) -> str:
    """The unit a pack-size report is stated in at first (PR-W): the NEXT UNIT DOWN of the item's ladder - a box of packs says
    "1 กล่อง = ? แพ็ค" - else กิโลกรัม for a กรัม item (ลิตร for มิลลิลิตร), never the bare base unit unless the item has nothing
    easier. `ladder` = Converter.ladder(code). A unit that is in the ladder uses its own parent when that is a ladder unit; a unit
    that is not (the receiving unit กล่อง of an item whose ladder has only แพ็ค) uses the top step of the ladder."""
    names = {r["unit"] for r in ladder}
    easier = _EASIER_PARENT.get(base) or base
    mine = next((r for r in ladder if r["unit"] == unit), None)
    if mine is not None:
        return mine["parent_unit"] if mine["parent_unit"] in names else easier
    below = [r for r in ladder if r["unit"] != unit]
    return max(below, key=lambda r: (r["level"], r["unit"]))["unit"] if below else easier


def item_info(con, code: str, conv: Converter = None) -> dict:
    """What the receive form needs to offer the report of one item: the units it can be about (each with the unit the report is
    stated in at first, `default_parent`), the parents it can be stated in, and what the register says now ('1 กล่อง = 10 แพ็ค')."""
    conv = conv or Converter(con)
    m = con.execute("SELECT name_th, base_unit, recv_unit, qty_per_recv FROM bc_ingredient WHERE code=?", (code,)).fetchone()
    if not m:
        raise ValueError(f"ไม่พบรหัส {code} ในทะเบียน")
    name, base_raw, recv, per = m
    base = norm(base_raw)
    lad = conv.ladder(code)
    recv_n = unit_word(recv)                     # the register's label may carry its contents ("กล่อง (10 แพ็ค)"): the unit is its word
    label = str(recv or "").strip()
    units = []
    for r in lad:
        units.append({"unit": r["unit"], "qty": r["qty_in_parent"], "parent": r["parent_unit"],
                      "current": f"1 {r['unit']} = {fmt_num(r['qty_in_parent'])} {r['parent_unit']}",
                      "label": label if r["unit"] == recv_n and label != recv_n else None})
    if recv_n and recv_n != base and recv_n not in {u["unit"] for u in units} and recv_n not in metric_units(base):
        cur = (f"1 {recv_n} = {fmt_num(per)} {base}" if per else "ยังไม่ระบุขนาดในทะเบียน")
        units.append({"unit": recv_n, "qty": per, "parent": base, "current": cur, "label": label if label != recv_n else None})
    for u in units:
        u["default_parent"] = default_parent(base, lad, u["unit"])
    parents = [base] + sorted(metric_units(base)) + [r["unit"] for r in lad]
    return {"code": code, "name": name, "base_unit": base, "recv_unit": recv_n or None, "recv_label": str(recv or "").strip() or None,
            "units": units, "parents": parents}


def _proposed(con, code: str, unit: str, qty: float, parent: str) -> tuple:
    """The item's ladder with `unit` stated as `qty` `parent` -> (rows, current_qty, current_parent, base, problems)."""
    m = con.execute("SELECT base_unit, recv_unit, qty_per_recv FROM bc_ingredient WHERE code=?", (code,)).fetchone()
    base = norm(m[0])
    rows = _ladder_rows(con, code)
    cur_q, cur_p, hit = None, None, False
    for r in rows:
        if norm(r["unit"]) == unit:
            cur_q, cur_p = r["qty_in_parent"], norm(r["parent_unit"]) or base
            r["qty_in_parent"], r["parent_unit"] = qty, None if parent == base else parent
            hit = True
    if not hit:
        if unit and unit == unit_word(m[1]) and m[2]:
            cur_q, cur_p = float(m[2]), base
        rows.append({"unit": unit, "parent_unit": None if parent == base else parent, "qty_in_parent": qty, "conv_kind": "exact",
                     "range_min": None, "range_max": None, "label": None})
    problems = check_ladder(base, [{**r, "parent_unit": r.get("parent_unit")} for r in rows])
    return rows, cur_q, cur_p, base, problems


def submit(con, branch_id: int, order_id: int, code: str, unit: str, qty, parent: str, note: str, who: str) -> dict:
    """A branch reports the real size of a pack received on `order_id`. Writes ONE bc_pack_report row; nothing else changes."""
    code, u, par = (code or "").strip(), norm(unit), norm(parent)
    q = _num(qty)
    o = con.execute("SELECT branch_id FROM bc_order WHERE order_id=?", (order_id,)).fetchone()
    if not o:
        raise ValueError("ไม่พบใบสั่งซื้อ")
    if int(o[0]) != int(branch_id):
        raise PermissionError("ใบสั่งซื้อนี้ไม่ใช่ของสาขานี้")
    if not con.execute("SELECT 1 FROM bc_order_line WHERE order_id=? AND ingredient_code=?", (order_id, code)).fetchone():
        raise ValueError(f"{code} ไม่อยู่ในใบสั่งซื้อนี้")
    if not u:
        raise ValueError("เลือกหน่วยที่ขนาดไม่ตรง")
    if q is None or q <= 0:
        raise ValueError("จำนวนที่มีจริงต้องมากกว่า 0")
    if not par:
        raise ValueError("เลือกหน่วยที่ใช้เทียบ")
    if u == par:
        raise ValueError("หน่วยที่แจ้งกับหน่วยที่ใช้เทียบต้องเป็นคนละหน่วย")
    info = item_info(con, code)
    if u not in {x["unit"] for x in info["units"]}:
        raise ValueError(f"{u}: ไม่ใช่หน่วยบรรจุของรายการนี้ · เลือกจากรายการที่แสดง")
    if par not in info["parents"]:
        raise ValueError(f"{par}: ใช้เทียบไม่ได้ · เลือกจากรายการที่แสดง")
    rows, cur_q, cur_p, base, problems = _proposed(con, code, u, q, par)
    if problems:
        raise ValueError("; ".join(problems[:2]))
    note = (note or "").strip() or None
    cur = con.execute(
        "INSERT INTO bc_pack_report(branch_id, order_id, ingredient_code, unit, reported_qty, reported_parent, current_qty,"
        " current_parent, note, status, created_by, created_at) VALUES(?,?,?,?,?,?,?,?,?, 'pending', ?, ?)",
        (branch_id, order_id, code, u, q, par, cur_q, cur_p, note, who, now()))
    audit(con, who, "unit_pack_report", "bc_pack_report", str(cur.lastrowid), None,
          {"order_id": order_id, "code": code, "unit": u, "qty": q, "parent": par, "current": [cur_q, cur_p]})
    return {"report_id": cur.lastrowid, "message": "✓ แจ้งขนาดบรรจุแล้ว ฝ่ายวิเคราะห์จะตรวจและปรับทะเบียน ถ่ายรูปฉลากส่งทางไลน์ด้วย"}


def _group_key(r) -> tuple:
    return (r["ingredient_code"], r["unit"], round(float(r["reported_qty"]), 6), r["reported_parent"] or "")


def listing(con, status: str = "pending", limit: int = 200) -> list:
    """The reports as the analyst reads them: grouped by (item, unit, reported size)."""
    names = dict(con.execute("SELECT code, name_th FROM bc_ingredient"))
    branches = dict(con.execute("SELECT branch_id, name FROM branches"))
    cols = ("report_id", "branch_id", "order_id", "ingredient_code", "unit", "reported_qty", "reported_parent", "current_qty",
            "current_parent", "note", "status", "created_by", "created_at", "decided_by", "decided_at", "decision_note")
    sql = f"SELECT {', '.join(cols)} FROM bc_pack_report"
    args = []
    if status and status != "all":
        sql += " WHERE status=?"
        args.append(status)
    rows = [dict(zip(cols, r)) for r in con.execute(sql + " ORDER BY report_id DESC LIMIT ?", args + [limit])]
    groups = {}
    for r in rows:
        g = groups.setdefault(_group_key(r) + (r["status"],), {
            "ingredient_code": r["ingredient_code"], "name": names.get(r["ingredient_code"]), "unit": r["unit"],
            "reported_qty": r["reported_qty"], "reported_parent": r["reported_parent"], "current_qty": r["current_qty"],
            "current_parent": r["current_parent"], "status": r["status"], "report_ids": [], "branches": [], "notes": [],
            "first_at": r["created_at"], "last_at": r["created_at"], "decided_by": r["decided_by"], "decision_note": r["decision_note"]})
        g["report_ids"].append(r["report_id"])
        g["first_at"] = min(g["first_at"], r["created_at"] or g["first_at"])
        g["last_at"] = max(g["last_at"], r["created_at"] or g["last_at"])
        b = branches.get(r["branch_id"], str(r["branch_id"]))
        g["branches"].append({"branch_id": r["branch_id"], "branch": b, "date": (r["created_at"] or "")[:10], "order_id": r["order_id"]})
        if r["note"]:
            g["notes"].append({"branch": b, "note": r["note"]})
    out = list(groups.values())
    for g in out:
        g["n_reports"] = len(g["report_ids"])
        g["n_branches"] = len({b["branch_id"] for b in g["branches"]})
        g["system_text"] = (f"1 {g['unit']} = {fmt_num(g['current_qty'])} {g['current_parent']}"
                            if g["current_qty"] is not None else "ยังไม่ระบุในทะเบียน")
        g["reported_text"] = f"1 {g['unit']} = {fmt_num(g['reported_qty'])} {g['reported_parent']}"
    out.sort(key=lambda g: (-g["n_branches"], g["name"] or ""))
    return out


def pending_count(con) -> int:
    """How many decisions the analyst has to make (groups, not rows)."""
    return len({(r[0], r[1], round(float(r[2]), 6), r[3] or "") for r in con.execute(
        "SELECT ingredient_code, unit, reported_qty, reported_parent FROM bc_pack_report WHERE status='pending'")})


def decide(con, report_ids: list, decision: str, note: str, who: str) -> dict:
    """The analyst's answer to one group. 'applied' replaces the step in the item's ladder (master.set_ladder: audit
    `unit_pack_report_apply`, the pack size of the receiving unit follows) and marks every pending report of the group;
    'rejected' needs a note, which the branch reads on its receive history. Caller commits."""
    if decision not in ("applied", "rejected"):
        raise ValueError("ตัดสินใจไม่ถูกต้อง · ใช้ค่านี้ หรือ ไม่ใช้")
    ids = sorted({int(i) for i in report_ids or []})
    if not ids:
        raise ValueError("เลือกรายการที่ต้องการตัดสินใจ")
    note = (note or "").strip() or None
    if decision == "rejected" and not note:
        raise ValueError("ไม่ใช้ค่าที่แจ้งต้องระบุเหตุผล เพื่อให้สาขาอ่านได้")
    rows = [dict(zip(("report_id", "ingredient_code", "unit", "reported_qty", "reported_parent", "status"), r)) for r in con.execute(
        f"SELECT report_id, ingredient_code, unit, reported_qty, reported_parent, status FROM bc_pack_report"
        f" WHERE report_id IN ({','.join('?' * len(ids))})", ids)]
    if len(rows) != len(ids):
        raise ValueError("ไม่พบรายการที่แจ้ง")
    if any(r["status"] != "pending" for r in rows):
        raise ValueError("รายการนี้มีผู้ตัดสินใจไปแล้ว")
    keys = {_group_key(r) for r in rows}
    if len(keys) != 1:
        raise ValueError("ตัดสินใจได้ครั้งละหนึ่งกลุ่มที่แจ้งค่าเดียวกัน")
    code, unit, qty, parent = rows[0]["ingredient_code"], rows[0]["unit"], rows[0]["reported_qty"], rows[0]["reported_parent"]
    siblings = [r[0] for r in con.execute(
        "SELECT report_id FROM bc_pack_report WHERE status='pending' AND ingredient_code=? AND unit=? AND reported_parent IS ?"
        " AND ABS(reported_qty-?)<1e-6", (code, unit, parent, qty))]
    all_ids = sorted(set(ids) | set(siblings))
    result = {"decision": decision, "code": code, "unit": unit, "reports": len(all_ids)}
    if decision == "applied":
        rows_new, cur_q, cur_p, base, problems = _proposed(con, code, unit, qty, parent)
        if problems:
            raise ValueError("; ".join(problems[:2]) + " · ปรับบันไดหน่วยของรายการนี้ก่อน")
        res = set_ladder(con, code, rows_new, who)
        audit(con, who, "unit_pack_report_apply", "bc_unit_rule", f"{code}|{unit}", [cur_q, cur_p],
              {"qty": qty, "parent": parent, "reports": all_ids, "qty_per_recv": res.get("qty_per_recv_change")})
        result["qty_per_recv_change"] = res.get("qty_per_recv_change")
        result["message"] = f"✓ ปรับทะเบียนเป็น 1 {unit} = {fmt_num(qty)} {parent} แล้ว"
    else:
        audit(con, who, "unit_pack_report_reject", "bc_pack_report", ",".join(map(str, all_ids)), None, {"note": note})
        result["message"] = "✓ บันทึกว่าไม่ใช้ค่าที่แจ้งแล้ว"
    con.execute(f"UPDATE bc_pack_report SET status=?, decided_by=?, decided_at=?, decision_note=?"
                f" WHERE report_id IN ({','.join('?' * len(all_ids))})",
                ("applied" if decision == "applied" else "rejected", who, now(), note, *all_ids))
    return result


def for_orders(con, branch_id: int = None, order_ids=None) -> dict:
    """{order_id: [{ingredient_code, name, unit, text, status, status_th, decision_note}]} — what the branch reads on the
    receive history ('แจ้งขนาดบรรจุ: ไม่ใช้ — {note}')."""
    names = dict(con.execute("SELECT code, name_th FROM bc_ingredient"))
    sql = ("SELECT order_id, ingredient_code, unit, reported_qty, reported_parent, status, decision_note, note, report_id"
           " FROM bc_pack_report WHERE order_id IS NOT NULL")
    args = []
    if branch_id is not None:
        sql += " AND branch_id=?"
        args.append(branch_id)
    if order_ids is not None:
        ids = list(order_ids)
        if not ids:
            return {}
        sql += f" AND order_id IN ({','.join('?' * len(ids))})"
        args += ids
    out = {}
    for oid, code, unit, q, par, st, dn, note, rid in con.execute(sql + " ORDER BY report_id", args):
        out.setdefault(oid, []).append({
            "report_id": rid, "ingredient_code": code, "name": names.get(code), "unit": unit, "status": st,
            "status_th": STATUS_TH.get(st, st), "decision_note": dn, "note": note,
            "text": f"1 {unit} = {fmt_num(q)} {par}"})
    return out

