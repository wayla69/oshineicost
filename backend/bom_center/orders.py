# -*- coding: utf-8 -*-
"""Phase 2 · Sprint S8 — the branch order: suggestion net of stock, confirm/edit, submit,
receive (docs/BOM_CENTER_PLAN.md §6 step 5, §7.1-3/6/7, §7.2-3, §7.5).

Stock the forecast deducts, per ingredient:

    on hand now      = last count + received since − BoM use since (sales up to the last data day)
    before the cover = what the days between now and the first covered day will use
    on order         = submitted / processed orders not yet received
    projected        = on hand now − before the cover + on order
    order            = forecast for the cover + safety − projected   → whole packs, never negative

An item the branch did not count is not deducted (the line says so); a branch with no
count at all orders on the forecast alone, and the order records that ("ไม่หักสต๊อก")
— counting is encouraged, never required (no-kill / adoption rule).

Order life: suggested (draft) → submitted (to Sales Support) → processed (Sales Support,
S9) → received (the branch, which also books a goods receipt for the variance) — or
cancelled. A submitted order can be reopened until Sales Support processes it.
"""
import math
from datetime import date, datetime, timedelta, timezone

from .clock import today as bkk_today
from .master import audit

DEVIATION_PCT = 20.0          # a confirmed quantity further than this from the suggestion needs a reason
REASONS = (("stock_more", "มีของเหลือมากกว่าที่ระบบเห็น"), ("stock_less", "ของเหลือน้อยกว่าที่ระบบเห็น"),
           ("event", "มีงาน / โปรโมชัน / ลูกค้าจองกลุ่ม"), ("space", "ที่เก็บไม่พอ"),
           ("quality", "ของรอบก่อนเสีย / คุณภาพไม่ดี"), ("pack", "ขนาดแพ็ค / ขั้นต่ำซัพพลายเออร์"),
           ("other", "อื่น ๆ"))
REASON_TH = dict(REASONS)
OPEN = ("submitted", "processed")


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ── stock the forecast deducts ───────────────────────────────────────────────
def stock_position(con, branch_id: int, as_of: date, order_day: date) -> dict:
    """{"basis": {count_id, date} | None, "qty": {code: on hand now}, "on_order": {code: base qty}}"""
    from .forecast import usage_history
    r = con.execute("SELECT count_id, count_date FROM bc_stock_count WHERE branch_id=?"
                    " AND COALESCE(status,'submitted')='submitted' AND count_date<=?"
                    " ORDER BY count_date DESC, count_id DESC LIMIT 1", (branch_id, order_day.isoformat())).fetchone()
    out = {"basis": None, "qty": {}, "on_order": on_order(con, branch_id, order_day)}
    if not r:
        return out
    cid, cdate = r
    out["basis"] = {"count_id": cid, "date": cdate}
    qty = {c: q for c, q in con.execute(
        "SELECT ingredient_code, qty_base FROM bc_stock_count_line WHERE count_id=? AND qty_base IS NOT NULL", (cid,))}
    for c, q in con.execute(
            "SELECT l.ingredient_code, SUM(l.qty_base) FROM bc_receipt r JOIN bc_receipt_line l USING(receipt_id)"
            " WHERE r.branch_id=? AND r.receipt_date>=? AND r.receipt_date<=? GROUP BY l.ingredient_code",
            (branch_id, cdate, order_day.isoformat())):
        if c in qty:
            qty[c] += q or 0.0
    d0 = date.fromisoformat(cdate)
    if as_of >= d0:                                         # sales since the count (the count is before that day's trading)
        use, _, _ = usage_history(con, branch_id, d0, as_of, f"{as_of.year:04d}-{as_of.month:02d}")
        for c in qty:
            qty[c] -= sum(use.get(c, {}).values())
    out["qty"] = qty
    return out


def on_order(con, branch_id: int, before: date) -> dict:
    """Base quantities on open orders placed before `before` — on their way, not yet received."""
    per = {c: p for c, p in con.execute("SELECT code, qty_per_recv FROM bc_ingredient")}
    out = {}
    for c, conf, proc, unit in con.execute(
            "SELECT l.ingredient_code, l.confirmed_units, l.processed_units, l.order_unit FROM bc_order o"
            " JOIN bc_order_line l USING(order_id) WHERE o.branch_id=? AND o.status IN ('submitted','processed')"
            " AND o.cycle_date<?", (branch_id, before.isoformat())):
        u = proc if proc is not None else conf
        if not u:
            continue
        f = per.get(c) if unit and unit != "base" else 1.0
        out[c] = out.get(c, 0.0) + u * (f or 1.0)
    return out


# ── the order ────────────────────────────────────────────────────────────────
def _order_row(con, order_id: int) -> dict:
    cols = ("order_id", "branch_id", "cycle_date", "round", "run_id", "status", "confirmed_by", "confirmed_at",
            "submitted_at", "processed_by", "processed_at", "po_ref", "received_at", "note", "stock_count_id", "value")
    r = con.execute(f"SELECT {', '.join(cols)} FROM bc_order WHERE order_id=?", (order_id,)).fetchone()
    return dict(zip(cols, r)) if r else None


def find(con, branch_id: int, rnd: str, cycle_date: str):
    r = con.execute("SELECT order_id FROM bc_order WHERE branch_id=? AND round=? AND cycle_date=?"
                    " AND status<>'cancelled' ORDER BY order_id DESC LIMIT 1", (branch_id, rnd, cycle_date)).fetchone()
    return r[0] if r else None


def detail(con, order_id: int) -> dict:
    o = _order_row(con, order_id)
    if not o:
        raise ValueError("ไม่พบออเดอร์")
    names = {c: (n, b) for c, n, b in con.execute("SELECT code, name_th, base_unit FROM bc_ingredient")}
    from .hub import SS_REASON_TH
    o["lines"] = [{"code": c, "name": (names.get(c) or (c,))[0], "suggested_units": s, "confirmed_units": cf,
                   "processed_units": p, "received_units": rc, "order_unit": u, "reason_code": rs,
                   "reason_th": REASON_TH.get(rs) if rs else None, "note": n,
                   "ss_reason": sr, "ss_reason_th": SS_REASON_TH.get(sr) if sr else None, "ss_note": sn}
                  for c, s, cf, p, rc, u, rs, n, sr, sn in con.execute(
                      "SELECT ingredient_code, suggested_units, confirmed_units, processed_units, received_units,"
                      " order_unit, reason_code, note, ss_reason, ss_note FROM bc_order_line WHERE order_id=?"
                      " ORDER BY ingredient_code", (order_id,))]
    o["status_th"] = STATUS_TH.get(o["status"], o["status"])
    return o


STATUS_TH = {"suggested": "ร่าง (ยังไม่ส่ง)", "confirmed": "ร่าง (ยังไม่ส่ง)", "submitted": "ส่งแล้ว — รอส่วนกลาง",
             "processed": "ส่วนกลางดำเนินการแล้ว", "received": "รับของแล้ว", "cancelled": "ยกเลิก"}


def _needs_reason(sugg, conf) -> bool:
    s, c = float(sugg or 0), float(conf or 0)
    if s == c:
        return False
    if s == 0:
        return c > 0
    return abs(c - s) > max(1.0, s * DEVIATION_PCT / 100.0)


def save(con, branch_id: int, rnd: str, cycle_date: str, suggestion: dict, lines: list, who: str,
         submit: bool = False, note: str = None) -> dict:
    """Create (from the suggestion) or update the cycle's order; optionally submit it.

    The suggested quantities are frozen when the order is first created — later
    edits change only what the branch confirms."""
    oid = find(con, branch_id, rnd, cycle_date)
    if oid:
        o = _order_row(con, oid)
        if o["status"] not in ("suggested", "confirmed"):
            raise ValueError(f"ออเดอร์นี้สถานะ “{STATUS_TH.get(o['status'])}” — แก้ไม่ได้"
                             + (" (กดเปิดแก้ก่อน)" if o["status"] == "submitted" else ""))
    else:
        basis = (suggestion.get("stock") or {}).get("basis") or {}
        cur = con.execute("INSERT INTO bc_order(branch_id, cycle_date, round, run_id, status, note, stock_count_id)"
                          " VALUES(?,?,?,?, 'suggested', ?, ?)",
                          (branch_id, cycle_date, rnd, suggestion.get("run_id"), note, basis.get("count_id")))
        oid = cur.lastrowid
        con.executemany("INSERT INTO bc_order_line(order_id, ingredient_code, suggested_units, confirmed_units,"
                        " order_unit) VALUES(?,?,?,?,?)",
                        [(oid, r["code"], r["order_units"], r["order_units"], r["order_unit"])
                         for r in suggestion["rows"]])
    have = {c: s for c, s in con.execute("SELECT ingredient_code, suggested_units FROM bc_order_line WHERE order_id=?", (oid,))}
    unit_of = {r["code"]: r["order_unit"] for r in suggestion.get("rows", [])}
    for ln in lines or []:
        c = (ln.get("code") or "").strip()
        if not c:
            continue
        q = ln.get("confirmed_units")
        if q is not None:
            try:
                q = float(q)
            except (TypeError, ValueError):
                raise ValueError(f"{c}: จำนวนต้องเป็นตัวเลข")
            if q < 0:
                raise ValueError(f"{c}: จำนวนติดลบไม่ได้")
        rs = (ln.get("reason_code") or "").strip() or None
        if rs and rs not in REASON_TH:
            raise ValueError(f"{c}: เหตุผลไม่อยู่ในรายการ")
        if c not in have:                                   # an item the suggestion did not include
            if not con.execute("SELECT 1 FROM bc_ingredient WHERE code=?", (c,)).fetchone():
                raise ValueError(f"ไม่พบรหัส {c} ในทะเบียน")
            con.execute("INSERT INTO bc_order_line(order_id, ingredient_code, suggested_units, confirmed_units,"
                        " order_unit, reason_code, note) VALUES(?,?,0,?,?,?,?)",
                        (oid, c, q, unit_of.get(c) or ln.get("order_unit") or "base", rs, ln.get("note")))
            have[c] = 0
        else:
            con.execute("UPDATE bc_order_line SET confirmed_units=?, reason_code=?, note=? WHERE order_id=?"
                        " AND ingredient_code=?", (q, rs, ln.get("note"), oid, c))
    if note is not None:
        con.execute("UPDATE bc_order SET note=? WHERE order_id=?", (note, oid))
    missing = []
    for c, s, cf, rs, n in con.execute("SELECT ingredient_code, suggested_units, confirmed_units, reason_code, note"
                                       " FROM bc_order_line WHERE order_id=?", (oid,)):
        if _needs_reason(s, cf) and not rs:
            missing.append(c)
        if rs == "other" and not (n or "").strip():
            missing.append(c)
    value = _value(con, oid, cycle_date)
    con.execute("UPDATE bc_order SET value=?, confirmed_by=?, confirmed_at=? WHERE order_id=?", (value, who, now(), oid))
    out = {"order_id": oid, "status": "suggested", "needs_reason": sorted(set(missing)), "value": value}
    if submit:
        if missing:
            raise ValueError(f"ยอดที่แก้ต่างจากที่แนะนำเกิน {DEVIATION_PCT:g}% ต้องเลือกเหตุผล: " + ", ".join(sorted(set(missing))[:5]))
        if not con.execute("SELECT 1 FROM bc_order_line WHERE order_id=? AND confirmed_units>0", (oid,)).fetchone():
            raise ValueError("ออเดอร์ว่าง — ไม่มีรายการที่สั่ง")
        con.execute("UPDATE bc_order SET status='submitted', submitted_at=? WHERE order_id=?", (now(), oid))
        audit(con, who, "order_submit", "bc_order", str(oid), None, {"branch_id": branch_id, "round": rnd,
                                                                     "cycle_date": cycle_date, "value": value})
        out["status"] = "submitted"
    return out


def _value(con, order_id: int, cycle_date: str) -> float:
    from .prices import price_for
    month = cycle_date[:7]
    per = {c: p for c, p in con.execute("SELECT code, qty_per_recv FROM bc_ingredient")}
    v = 0.0
    for c, q, u in con.execute("SELECT ingredient_code, confirmed_units, order_unit FROM bc_order_line WHERE order_id=?",
                               (order_id,)):
        p = price_for(con, c, month)
        if p and q:
            v += q * ((per.get(c) or 1.0) if u and u != "base" else 1.0) * p[0]
    return round(v, 2)


def reopen(con, branch_id: int, order_id: int, who: str):
    o = _order_row(con, order_id)
    if not o or o["branch_id"] != branch_id:
        raise ValueError("ไม่พบออเดอร์ของสาขานี้")
    if o["status"] != "submitted":
        raise ValueError(f"เปิดแก้ได้เฉพาะออเดอร์ที่ส่งแล้วแต่ส่วนกลางยังไม่ดำเนินการ (ตอนนี้: {STATUS_TH.get(o['status'])})")
    con.execute("UPDATE bc_order SET status='suggested', submitted_at=NULL WHERE order_id=?", (order_id,))
    audit(con, who, "order_reopen", "bc_order", str(order_id))


def cancel(con, branch_id: int, order_id: int, who: str):
    o = _order_row(con, order_id)
    if not o or o["branch_id"] != branch_id:
        raise ValueError("ไม่พบออเดอร์ของสาขานี้")
    if o["status"] not in ("suggested", "confirmed", "submitted"):
        raise ValueError("ยกเลิกไม่ได้ — ส่วนกลางดำเนินการแล้ว")
    con.execute("UPDATE bc_order SET status='cancelled' WHERE order_id=?", (order_id,))
    audit(con, who, "order_cancel", "bc_order", str(order_id))


def receive(con, branch_id: int, order_id: int, lines: list, who: str, receipt_date: str = None) -> dict:
    """The branch confirms what arrived; this also books a goods receipt for the variance (S7)."""
    from .variance import add_receipt
    o = _order_row(con, order_id)
    if not o or o["branch_id"] != branch_id:
        raise ValueError("ไม่พบออเดอร์ของสาขานี้")
    if o["status"] not in ("submitted", "processed"):
        raise ValueError(f"รับของได้เมื่อออเดอร์ส่งแล้ว (ตอนนี้: {STATUS_TH.get(o['status'])})")
    got = {}
    for ln in lines or []:
        c = (ln.get("code") or "").strip()
        try:
            q = float(ln.get("received_units") or 0)
        except (TypeError, ValueError):
            raise ValueError(f"{c}: จำนวนต้องเป็นตัวเลข")
        if q < 0:
            raise ValueError(f"{c}: จำนวนติดลบไม่ได้")
        got[c] = q
    units = {c: u for c, u in con.execute("SELECT ingredient_code, order_unit FROM bc_order_line WHERE order_id=?",
                                          (order_id,))}
    recv = {c: r for c, r in con.execute("SELECT code, recv_unit FROM bc_ingredient")}
    base = {c: b for c, b in con.execute("SELECT code, base_unit FROM bc_ingredient")}
    rlines = []
    for c, q in got.items():
        if c not in units:
            raise ValueError(f"{c} ไม่อยู่ในออเดอร์นี้")
        con.execute("UPDATE bc_order_line SET received_units=? WHERE order_id=? AND ingredient_code=?", (q, order_id, c))
        if q > 0:
            u = units[c]
            rlines.append({"code": c, "qty": q, "unit": (recv.get(c) if u and u != "base" else base.get(c))})
    rd = receipt_date or bkk_today().isoformat()
    rec = add_receipt(con, branch_id, rd, rlines, who, ref=f"ORDER-{order_id}", source="order") if rlines else None
    con.execute("UPDATE bc_order SET status='received', received_at=? WHERE order_id=?", (now(), order_id))
    audit(con, who, "order_receive", "bc_order", str(order_id), None, {"lines": len(rlines)})
    return {"order_id": order_id, "status": "received", "receipt_id": rec and rec["receipt_id"]}


def history(con, branch_id: int, limit: int = 20) -> list:
    rows = con.execute("SELECT order_id, cycle_date, round, status, value, submitted_at, received_at FROM bc_order"
                       " WHERE branch_id=? ORDER BY cycle_date DESC, order_id DESC LIMIT ?", (branch_id, limit)).fetchall()
    return [dict(zip(("order_id", "cycle_date", "round", "status", "value", "submitted_at", "received_at"), r),
                 status_th=STATUS_TH.get(r[3], r[3])) for r in rows]


def order_units(need: float, per, moq) -> tuple:
    """(units, unit) — whole packs when the pack size is known, else base units rounded up."""
    from .units import to_order_units
    if need <= 0:
        return 0.0, ("pack" if per else "base")
    if per:
        return float(to_order_units(need, per, moq or 1)), "pack"
    return float(math.ceil(need)), "base"


def plan(con, branch_id: int, rnd: str, order_day: date, as_of: date = None) -> dict:
    """The suggestion net of stock, ready to become an order."""
    from .forecast import suggest
    last = con.execute("SELECT MAX(day) FROM sales_sku_daily WHERE branch_id=?", (branch_id,)).fetchone()[0]
    as_of = as_of or (date.fromisoformat(last) if last else order_day - timedelta(days=1))
    stock = stock_position(con, branch_id, as_of, order_day)
    s = suggest(con, branch_id, rnd, order_day, as_of=as_of, stock=stock)
    s["stock"] = {"basis": stock["basis"], "deducted": bool(stock["basis"])}
    for r in s["rows"]:
        units, kind = order_units(r["need"], r.get("qty_per_recv"), r.get("moq"))
        r["order_units"] = units
        r["order_unit"] = kind
    oid = find(con, branch_id, rnd, s["order_day"])
    s["order"] = detail(con, oid) if oid else None
    s["reasons"] = [{"code": k, "label": v} for k, v in REASONS]
    s["deviation_pct"] = DEVIATION_PCT
    return s
