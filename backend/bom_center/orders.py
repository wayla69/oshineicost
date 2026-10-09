# -*- coding: utf-8 -*-
"""Phase 2 · Sprint S8 — the branch order: suggestion net of stock, confirm/edit, submit,
receive (docs/BOM_CENTER_PLAN.md §6 step 5, §7.1-3/6/7, §7.2-3, §7.5).

Stock the forecast deducts, per ingredient:

    on hand now      = last count + received since − BoM use since (sales up to the last data day)
    before the cover = what the days between now and the first covered day will use
    on order         = submitted / processed orders not yet received
    projected        = on hand now − before the cover + on order
    order            = forecast for the cover + safety − projected   → whole packs, never negative

PR-A (2026-10-09): counting IS part of ordering. The branch counts what is on its shelf
in the same page (a 'spot' count linked to the order, `save_flow`); an item that was not
counted cannot be ordered, and the order must be submitted before 13:15 two days before
its delivery day (`forecast.cutoff_for`). A later submit is accepted with a warning:
the order is flagged `late` (the outside warehouse charges a late fee). `cycle_date` is
the CUTOFF date; `delivery_date` / `cutoff_at` / `late` sit beside it.

Order life: suggested (draft) → submitted (to Sales Support) → processed (Sales Support,
S9) → received (the branch, which also books a goods receipt for the variance) — or
cancelled. A submitted order can be reopened until Sales Support processes it.
"""
import math
from datetime import date, datetime, timedelta, timezone

from .clock import today as bkk_today
from .master import audit

DEVIATION_PCT = 20.0          # a confirmed quantity further than this from the suggestion needs a reason
REASONS = (("stock_more", "สินค้าคงเหลือมากกว่าที่ระบบแสดง"), ("stock_less", "สินค้าคงเหลือน้อยกว่าที่ระบบแสดง"),
           ("event", "มีงาน / โปรโมชัน / ลูกค้าจองกลุ่ม"), ("space", "พื้นที่จัดเก็บไม่เพียงพอ"),
           ("quality", "สินค้ารอบก่อนเสีย / คุณภาพไม่ดี"), ("pack", "ขนาดแพ็ค / ขั้นต่ำซัพพลายเออร์"),
           ("other", "อื่น ๆ"))
REASON_TH = dict(REASONS)
# PR-19: the branch's buffer — its own expectation for the cover period, one per order
BUFFER_REASONS = (("event", "มีงาน / กิจกรรมในพื้นที่"), ("promotion", "โปรโมชัน"), ("booking", "ลูกค้าจองกลุ่ม"),
                  ("holiday", "วันหยุดยาว / เทศกาล"), ("quiet", "คาดว่าลูกค้าจะน้อยลง"), ("other", "อื่น ๆ (ระบุ)"))
BUFFER_REASON_TH = dict(BUFFER_REASONS)
BUFFER_MIN, BUFFER_MAX = -50.0, 100.0


def buffer_clean(buffer) -> tuple:
    """(pct, reason, note) validated: 0 clears; anything else needs a listed reason."""
    if not buffer:
        return 0.0, None, None
    try:
        pct = float(buffer.get("pct") or 0)
    except (TypeError, ValueError, AttributeError):
        raise ValueError("buffer ต้องเป็นตัวเลข")
    if not (BUFFER_MIN <= pct <= BUFFER_MAX):
        raise ValueError(f"buffer ต้องอยู่ระหว่าง {BUFFER_MIN:g}% ถึง +{BUFFER_MAX:g}%")
    if pct == 0:
        return 0.0, None, None
    reason = (buffer.get("reason") or "").strip() or None
    note = (buffer.get("note") or "").strip() or None
    if reason not in BUFFER_REASON_TH:
        raise ValueError("กรุณาเลือกเหตุผลที่คาดว่ายอดขายจะต่างจากปกติ")
    if reason == "other" and not note:
        raise ValueError("เหตุผล “อื่น ๆ” ต้องระบุคำอธิบาย")
    return pct, reason, note
OPEN = ("submitted", "processed")


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


_ts = now                     # `now` is also the name of a clock argument in save/plan/save_flow


class LateSubmit(ValueError):
    """The order is being submitted after its cutoff: the branch must confirm the late fee."""

    def __init__(self, cutoff_at: str, cutoff_th: str):
        self.cutoff_at, self.cutoff_th = cutoff_at, cutoff_th
        super().__init__(f"เลยเวลาตัดรอบแล้ว ({cutoff_th}) หากส่งใบสั่งซื้อนี้ จะมีค่าปรับล่าช้าที่ต้องชำระให้คลังสินค้าภายนอก "
                         "กรุณายืนยันการส่งล่าช้า")


def backfill(con) -> None:
    """Idempotent, run by schema.ensure(): the delivery weekday of calendar rows saved before PR-A, and the
    delivery day / cutoff of orders created before it (`late` stays 0 — nobody was judged by the cutoff)."""
    from .forecast import _next_on_or_after, calendar, cutoff_for
    # reads first: a no-op must not open a write transaction on the shared per-thread connection
    legacy_cal = con.execute("SELECT 1 FROM bc_order_calendar WHERE delivery_weekday IS NULL LIMIT 1").fetchone()
    todo = con.execute("SELECT order_id, branch_id, round, cycle_date FROM bc_order WHERE delivery_date IS NULL").fetchall()
    if not legacy_cal and not todo:
        return
    outer = con.in_transaction                            # someone else's work in flight: leave the commit to them
    if legacy_cal:
        con.execute("UPDATE bc_order_calendar SET delivery_weekday=(order_weekday+COALESCE(lead_days,1))%7"
                    " WHERE delivery_weekday IS NULL")
    cals = {}
    for oid, bid, rnd, cyc in todo:
        try:
            cal = cals.get(bid) or cals.setdefault(bid, calendar(con, bid))
            dlv = _next_on_or_after(date.fromisoformat(cyc) + timedelta(days=1), int(cal[rnd]["delivery_weekday"]))
        except (KeyError, TypeError, ValueError):
            continue
        con.execute("UPDATE bc_order SET delivery_date=?, cutoff_at=? WHERE order_id=?",
                    (dlv.isoformat(), cutoff_for(dlv).isoformat(), oid))
    if not outer:
        con.commit()


# ── stock the forecast deducts ───────────────────────────────────────────────
def stock_position(con, branch_id: int, as_of: date, order_day: date, count_id: int = None,
                   until: date = None) -> dict:
    """{"basis": {count_id, date} | None, "qty": {code: on hand now}, "on_order": {code: base qty}}

    The basis is `count_id` when given (the count this order is made from, draft or submitted), else the
    latest submitted count of ANY kind (month-end or a spot count) dated on or before `until` (default
    the order day)."""
    from .forecast import usage_history
    if count_id:
        r = con.execute("SELECT count_id, count_date FROM bc_stock_count WHERE count_id=? AND branch_id=?",
                        (count_id, branch_id)).fetchone()
    else:
        r = con.execute("SELECT count_id, count_date FROM bc_stock_count WHERE branch_id=?"
                        " AND COALESCE(status,'submitted')='submitted' AND count_date<=?"
                        " ORDER BY count_date DESC, count_id DESC LIMIT 1",
                        (branch_id, (until or order_day).isoformat())).fetchone()
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
            "submitted_at", "processed_by", "processed_at", "po_ref", "received_at", "note", "stock_count_id", "value",
            "buffer_pct", "buffer_reason", "buffer_note", "delivery_date", "cutoff_at", "late", "ack_at", "ack_by")
    r = con.execute(f"SELECT {', '.join(cols)} FROM bc_order WHERE order_id=?", (order_id,)).fetchone()
    return dict(zip(cols, r)) if r else None


def find(con, branch_id: int, rnd: str, cycle_date: str):
    r = con.execute("SELECT order_id FROM bc_order WHERE branch_id=? AND round=? AND cycle_date=?"
                    " AND status<>'cancelled' ORDER BY order_id DESC LIMIT 1", (branch_id, rnd, cycle_date)).fetchone()
    return r[0] if r else None


def detail(con, order_id: int) -> dict:
    o = _order_row(con, order_id)
    if not o:
        raise ValueError("ไม่พบใบสั่งซื้อ")
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
    o["buffer_reason_th"] = BUFFER_REASON_TH.get(o.get("buffer_reason")) if o.get("buffer_reason") else None
    from .hub import delivery_of
    o["delivery"] = delivery_of(con, o["branch_id"], o["round"], o["cycle_date"], o.get("delivery_date"))
    o["late"] = int(o.get("late") or 0)
    return o


STATUS_TH = {"suggested": "ร่าง (ยังไม่ส่ง)", "confirmed": "ร่าง (ยังไม่ส่ง)", "submitted": "ส่งแล้ว รอส่วนกลางดำเนินการ",
             "processed": "ส่วนกลางดำเนินการแล้ว", "received": "รับสินค้าแล้ว", "cancelled": "ยกเลิก"}


def _needs_reason(sugg, conf) -> bool:
    s, c = float(sugg or 0), float(conf or 0)
    if s == c:
        return False
    if s == 0:
        return c > 0
    return abs(c - s) > max(1.0, s * DEVIATION_PCT / 100.0)


def _cutoff_dt(con, o: dict) -> datetime:
    """The moment an order had to be in by: stored, else derived from its delivery day."""
    from .forecast import cutoff_for
    if o.get("cutoff_at"):
        return datetime.fromisoformat(o["cutoff_at"])
    from .hub import delivery_of
    return cutoff_for(date.fromisoformat(delivery_of(con, o["branch_id"], o["round"], o["cycle_date"],
                                                     o.get("delivery_date"))))


def save(con, branch_id: int, rnd: str, cycle_date: str, suggestion: dict, lines: list, who: str,
         submit: bool = False, note: str = None, buffer: dict = None, ack_late: bool = False,
         delivery: str = None, stock_count_id: int = None, now: datetime = None) -> dict:
    """Create (from the suggestion) or update the cycle's order; optionally submit it.

    The suggested quantities are taken from the suggestion when the order is first created and
    follow it (the branch's count) while the order is a draft; from the moment it is submitted they
    are frozen — later edits change only what the branch confirms. `buffer` (PR-19, None = keep) is
    the branch's expectation {pct, reason, note}: the suggestion stays the engine's
    base number, the confirmed quantities start from the buffered one, and the
    deviation rule judges against base × (1 + pct/100).

    PR-A: `cycle_date` is the cutoff date; `delivery` the delivery day. Every ordered line must
    have a counted on-hand in the linked count (`stock_count_id`) before the order can be
    submitted; a submit after the cutoff raises LateSubmit unless `ack_late`, and is flagged `late`."""
    from .central import central_map
    from .forecast import _now_for, cutoff_for, cutoff_th
    cmap = central_map(con)
    delivery = delivery or suggestion.get("delivery")
    cut_iso = suggestion.get("cutoff_at") or (cutoff_for(date.fromisoformat(delivery)).isoformat() if delivery else None)
    oid = find(con, branch_id, rnd, cycle_date)
    if oid:
        o = _order_row(con, oid)
        if o["status"] not in ("suggested", "confirmed"):
            raise ValueError(f"ใบสั่งซื้อนี้มีสถานะ “{STATUS_TH.get(o['status'])}” จึงแก้ไขไม่ได้"
                             + (" กรุณากดเปิดแก้ไขก่อน" if o["status"] == "submitted" else ""))
        con.execute("UPDATE bc_order SET delivery_date=COALESCE(?, delivery_date), cutoff_at=COALESCE(?, cutoff_at),"
                    " stock_count_id=COALESCE(?, stock_count_id) WHERE order_id=?",
                    (delivery, cut_iso, stock_count_id, oid))
        # while the order is a draft its suggestion follows the count the branch types (the first save
        # is often made before the shelf is counted); it is frozen from the moment of submit
        known = {c for c, in con.execute("SELECT ingredient_code FROM bc_order_line WHERE order_id=?", (oid,))}
        for r in suggestion.get("rows") or []:
            base = r.get("order_units_base", r["order_units"])
            if r["code"] in known:
                con.execute("UPDATE bc_order_line SET suggested_units=? WHERE order_id=? AND ingredient_code=?",
                            (base, oid, r["code"]))
            else:
                con.execute("INSERT INTO bc_order_line(order_id, ingredient_code, suggested_units, confirmed_units,"
                            " order_unit) VALUES(?,?,?,?,?)", (oid, r["code"], base, r["order_units"], r["order_unit"]))
    else:
        basis = (suggestion.get("stock") or {}).get("basis") or {}
        bk = con.execute("SELECT kind FROM bc_stock_count WHERE count_id=?", (basis.get("count_id"),)).fetchone() \
            if basis.get("count_id") else None
        cur = con.execute("INSERT INTO bc_order(branch_id, cycle_date, round, run_id, status, note, stock_count_id,"
                          " delivery_date, cutoff_at) VALUES(?,?,?,?, 'suggested', ?, ?, ?, ?)",
                          (branch_id, cycle_date, rnd, suggestion.get("run_id"), note,
                           # the count it was planned from — but another order's spot count is never shared
                           stock_count_id or (basis.get("count_id") if bk and bk[0] != "spot" else None),
                           delivery, cut_iso))
        oid = cur.lastrowid
        con.executemany("INSERT INTO bc_order_line(order_id, ingredient_code, suggested_units, confirmed_units,"
                        " order_unit) VALUES(?,?,?,?,?)",
                        [(oid, r["code"], r.get("order_units_base", r["order_units"]), r["order_units"], r["order_unit"])
                         for r in suggestion["rows"]])
    if buffer is not None:
        pct, reason, bnote = buffer_clean(buffer)
        con.execute("UPDATE bc_order SET buffer_pct=?, buffer_reason=?, buffer_note=? WHERE order_id=?",
                    (pct or None, reason, bnote, oid))
    up = 1.0 + float(con.execute("SELECT COALESCE(buffer_pct, 0) FROM bc_order WHERE order_id=?", (oid,)).fetchone()[0]) / 100.0
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
                raise ValueError(f"{c}: จำนวนต้องไม่ติดลบ")
        rs = (ln.get("reason_code") or "").strip() or None
        if rs and rs not in REASON_TH:
            raise ValueError(f"{c}: เหตุผลไม่อยู่ในรายการ")
        if c not in have:                                   # an item the suggestion did not include
            if not con.execute("SELECT 1 FROM bc_ingredient WHERE code=?", (c,)).fetchone():
                raise ValueError(f"ไม่พบรหัส {c} ในทะเบียน")
            if cmap and c not in cmap:
                raise ValueError(f"{c} ไม่อยู่ในบัญชีสินค้าส่วนกลาง สาขาต้องจัดซื้อในพื้นที่")
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
        if _needs_reason((s or 0) * up, cf) and not rs:        # the buffer explains its own share
            missing.append(c)
        if rs == "other" and not (n or "").strip():
            missing.append(c)
    value = _value(con, oid, cycle_date)
    con.execute("UPDATE bc_order SET value=?, confirmed_by=?, confirmed_at=? WHERE order_id=?", (value, who, _ts(), oid))
    o2 = _order_row(con, oid)
    # every ordered line needs a counted on-hand in the linked count — nothing is ordered blind
    counted = {c for c, in con.execute("SELECT ingredient_code FROM bc_stock_count_line WHERE count_id=?"
                                       " AND qty_base IS NOT NULL", (o2["stock_count_id"],))} \
        if o2["stock_count_id"] else set()
    uncounted = sorted(c for c, in con.execute("SELECT ingredient_code FROM bc_order_line WHERE order_id=?"
                                               " AND COALESCE(confirmed_units,0)>0", (oid,)) if c not in counted)
    cut = _cutoff_dt(con, o2)
    out = {"order_id": oid, "status": "suggested", "needs_reason": sorted(set(missing)), "value": value,
           "late": int(o2["late"] or 0), "cutoff_at": cut.isoformat(), "delivery": o2["delivery_date"] or delivery,
           "uncounted": uncounted}
    if submit:
        if missing:
            raise ValueError(f"จำนวนที่แก้ไขต่างจากที่แนะนำเกิน {DEVIATION_PCT:g}% กรุณาเลือกเหตุผล: " + ", ".join(sorted(set(missing))[:5]))
        if not con.execute("SELECT 1 FROM bc_order_line WHERE order_id=? AND confirmed_units>0", (oid,)).fetchone():
            raise ValueError("ใบสั่งซื้อยังไม่มีรายการสินค้า")
        if uncounted:
            raise ValueError(f"ยังไม่ได้นับยอดคงเหลือของรายการที่สั่ง {len(uncounted)} รายการ: " + ", ".join(uncounted[:10])
                             + " กรุณากรอกช่อง “คงเหลือ” ก่อนส่ง")
        late = _now_for(None, now) > cut
        if late and not ack_late:
            raise LateSubmit(cut.isoformat(), cutoff_th(cut))
        con.execute("UPDATE bc_order SET status='submitted', submitted_at=?, late=? WHERE order_id=?",
                    (now.astimezone(timezone.utc).isoformat(timespec="seconds") if now else _ts(),
                     1 if late else 0, oid))
        audit(con, who, "order_submit", "bc_order", str(oid), None, {"branch_id": branch_id, "round": rnd,
                                                                     "cycle_date": cycle_date, "value": value,
                                                                     "delivery": out["delivery"], "cutoff_at": cut.isoformat(),
                                                                     "late": 1 if late else 0,
                                                                     "count_id": o2["stock_count_id"]})
        out.update(status="submitted", late=1 if late else 0)
    return out


def _value(con, order_id: int, cycle_date: str) -> float:
    from .central import central_map, unit_price
    month = cycle_date[:7]
    cmap = central_map(con)
    per = {c: p for c, p in con.execute("SELECT code, qty_per_recv FROM bc_ingredient")}
    v = 0.0
    for c, q, u in con.execute("SELECT ingredient_code, confirmed_units, order_unit FROM bc_order_line WHERE order_id=?",
                               (order_id,)):
        p = unit_price(con, c, month, cmap)[0]
        if p and q:
            v += q * ((per.get(c) or 1.0) if u and u != "base" else 1.0) * p
    return round(v, 2)


def reopen(con, branch_id: int, order_id: int, who: str):
    o = _order_row(con, order_id)
    if not o or o["branch_id"] != branch_id:
        raise ValueError("ไม่พบใบสั่งซื้อของสาขานี้")
    if o["status"] != "submitted":
        raise ValueError(f"เปิดแก้ไขได้เฉพาะใบสั่งซื้อที่ส่งแล้วและส่วนกลางยังไม่ดำเนินการ (สถานะปัจจุบัน: {STATUS_TH.get(o['status'])})")
    if o["stock_count_id"]:                                 # the spot count goes back to a draft with its order
        from .stock import reopen_spot
        reopen_spot(con, o["stock_count_id"], who)
    con.execute("UPDATE bc_order SET status='suggested', submitted_at=NULL, late=0 WHERE order_id=?", (order_id,))
    audit(con, who, "order_reopen", "bc_order", str(order_id))


def cancel(con, branch_id: int, order_id: int, who: str):
    o = _order_row(con, order_id)
    if not o or o["branch_id"] != branch_id:
        raise ValueError("ไม่พบใบสั่งซื้อของสาขานี้")
    if o["status"] not in ("suggested", "confirmed", "submitted"):
        raise ValueError("ยกเลิกไม่ได้ เนื่องจากส่วนกลางดำเนินการแล้ว")
    if o["stock_count_id"]:                                 # a spot count still in draft goes with the order
        from .stock import delete_spot_draft
        if delete_spot_draft(con, o["stock_count_id"], who):
            con.execute("UPDATE bc_order SET stock_count_id=NULL WHERE order_id=?", (order_id,))
    con.execute("UPDATE bc_order SET status='cancelled' WHERE order_id=?", (order_id,))
    audit(con, who, "order_cancel", "bc_order", str(order_id))


def receive(con, branch_id: int, order_id: int, lines: list, who: str, receipt_date: str = None,
            ack: bool = False, claim_note: str = None) -> dict:
    """The branch confirms what arrived; this also books a goods receipt for the variance (S7).

    PR-B (owner 2026-10-09): the receipt is booked at what was RECEIVED, and where that is less than what the
    warehouse sent (processed_units, else confirmed_units) the line carries a reason and the gap becomes one
    claim (origin 'receive') in the same save. The branch must acknowledge that it checked the condition and
    the quantity (`ack`) — enforced here, so no caller can skip it. More than was sent is allowed and
    reported in `over`."""
    from .variance import add_receipt
    from . import claims as _cl
    o = _order_row(con, order_id)
    if not o or o["branch_id"] != branch_id:
        raise ValueError("ไม่พบใบสั่งซื้อของสาขานี้")
    if o["status"] not in ("submitted", "processed"):
        raise ValueError(f"รับสินค้าได้เมื่อส่งใบสั่งซื้อแล้วเท่านั้น (สถานะปัจจุบัน: {STATUS_TH.get(o['status'])})")
    if not ack:
        raise ValueError("กรุณายืนยันว่าสาขาได้ตรวจสอบสภาพสินค้าและจำนวนครบถ้วนแล้ว ก่อนบันทึกรับสินค้า")
    got = {}
    for ln in lines or []:
        c = (ln.get("code") or "").strip()
        try:
            q = float(ln.get("received_units") or 0)
        except (TypeError, ValueError):
            raise ValueError(f"{c}: จำนวนต้องเป็นตัวเลข")
        if q < 0:
            raise ValueError(f"{c}: จำนวนต้องไม่ติดลบ")
        got[c] = (q, (ln.get("reason") or "").strip(), (ln.get("note") or "").strip() or None)
    units, sent = {}, {}
    for c, u, pu, cf in con.execute("SELECT ingredient_code, order_unit, processed_units, confirmed_units"
                                    " FROM bc_order_line WHERE order_id=?", (order_id,)):
        units[c] = u
        sent[c] = float(pu if pu is not None else (cf or 0))
    recv = {c: r for c, r in con.execute("SELECT code, recv_unit FROM bc_ingredient")}
    base = {c: b for c, b in con.execute("SELECT code, base_unit FROM bc_ingredient")}
    rlines, short, over = [], [], []
    for c, (q, reason, note) in got.items():
        if c not in units:
            raise ValueError(f"{c} ไม่อยู่ในใบสั่งซื้อนี้")
        gap = sent[c] - q
        if gap > 1e-9:
            if reason not in _cl.REASON_TH:
                raise ValueError(f"{c}: รับจริงน้อยกว่าที่คลังส่ง ({sent[c]:g}) กรุณาเลือกสาเหตุของส่วนต่าง")
            if reason == "other" and not note:
                raise ValueError(f"{c}: สาเหตุ “อื่น ๆ” ต้องระบุคำอธิบาย")
            short.append({"code": c, "claim_units": round(gap, 6), "reason": reason, "note": note,
                          "sent_units": sent[c], "received_units": q})
        elif gap < -1e-9:
            over.append({"code": c, "sent_units": sent[c], "received_units": q, "over_units": round(-gap, 6)})
    for c, (q, _r, _n) in got.items():
        con.execute("UPDATE bc_order_line SET received_units=? WHERE order_id=? AND ingredient_code=?", (q, order_id, c))
        if q > 0:
            u = units[c]
            rlines.append({"code": c, "qty": q, "unit": (recv.get(c) if u and u != "base" else base.get(c))})
    rd = receipt_date or bkk_today().isoformat()
    rec = add_receipt(con, branch_id, rd, rlines, who, ref=f"ORDER-{order_id}", source="order") if rlines else None
    stamp = now()
    con.execute("UPDATE bc_order SET status='received', received_at=?, ack_at=?, ack_by=? WHERE order_id=?",
                (stamp, stamp, who, order_id))
    cl = _cl.create_from_receive(con, branch_id, order_id, short, claim_note, who) if short else None
    audit(con, who, "order_receive", "bc_order", str(order_id), None,
          {"lines": len(rlines), "short": len(short), "over": over, "claim_id": cl and cl["claim_id"], "ack": True})
    return {"order_id": order_id, "status": "received", "receipt_id": rec and rec["receipt_id"],
            "claim_id": cl and cl["claim_id"], "claim_lines": cl["lines"] if cl else 0, "over": over}


def history(con, branch_id: int, limit: int = 20, statuses=None) -> list:
    """The branch's orders, newest first, each with its delivery day and line count;
    `statuses` narrows (OPEN = sent or processed, the ones still to be received)."""
    from .hub import delivery_of
    sql = ("SELECT o.order_id, o.cycle_date, o.round, o.status, o.value, o.submitted_at, o.received_at, o.processed_at,"
           " (SELECT COUNT(*) FROM bc_order_line l WHERE l.order_id=o.order_id AND COALESCE(l.confirmed_units,0)>0),"
           " o.buffer_pct, o.delivery_date, o.cutoff_at, COALESCE(o.late,0)"
           " FROM bc_order o WHERE o.branch_id=?")
    args = [branch_id]
    if statuses:
        sql += f" AND o.status IN ({','.join('?' * len(statuses))})"
        args += list(statuses)
    rows = con.execute(sql + " ORDER BY o.cycle_date DESC, o.order_id DESC LIMIT ?", args + [limit]).fetchall()
    out = []
    for r in rows:
        d = dict(zip(("order_id", "cycle_date", "round", "status", "value", "submitted_at", "received_at", "processed_at",
                      "lines", "buffer_pct", "delivery_date", "cutoff_at", "late"), r))
        d["status_th"] = STATUS_TH.get(d["status"], d["status"])
        d["delivery"] = delivery_of(con, branch_id, d["round"], d["cycle_date"], d.pop("delivery_date"))
        out.append(d)
    return out


def next_round(con, branch_id: int, today: date, now: datetime = None) -> str:
    """The round of the delivery a branch should be working on from `today`: the nearest delivery
    whose cutoff has not passed (else the nearest). What the branch page opens on when nobody picked."""
    from .forecast import next_deliveries, pick_delivery
    return pick_delivery(next_deliveries(con, branch_id, today, now=now))["round"]


def order_units(need: float, per, moq) -> tuple:
    """(units, unit) — whole packs when the pack size is known, else base units rounded up."""
    from .units import to_order_units
    if need <= 0:
        return 0.0, ("pack" if per else "base")
    if per:
        return float(to_order_units(need, per, moq or 1)), "pack"
    return float(math.ceil(need)), "base"


def plan(con, branch_id: int, rnd: str, delivery: date, as_of: date = None, buffer_pct: float = None,
         now: datetime = None, count_id: int = None) -> dict:
    """The suggestion for one delivery, net of the branch's count, ready to become an order.

    `delivery` is the delivery day of the round. `count_id` is the count this order is made from (a
    spot count, draft or submitted); None = the one linked to the cycle's order, else the latest
    submitted count. `buffer_pct` (PR-19): the branch's expectation to apply; None = the cycle's
    order's own, if any. `now` is the clock (tests pass it)."""
    from .forecast import _now_for, cover_for_delivery, cutoff_for, day_th, next_deliveries, suggest
    from .stock import count_row, count_units
    cyc_d, dlv, _cf, _ct = cover_for_delivery(con, branch_id, rnd, delivery)
    cyc = cyc_d.isoformat()
    now_ = _now_for(None, now)
    last = con.execute("SELECT MAX(day) FROM sales_sku_daily WHERE branch_id=?", (branch_id,)).fetchone()[0]
    as_of = as_of or (date.fromisoformat(last) if last else cyc_d - timedelta(days=1))
    oid0 = find(con, branch_id, rnd, cyc)
    o0 = _order_row(con, oid0) if oid0 else None
    cid = count_id
    if not cid and o0 and o0["stock_count_id"]:               # the order's own spot count (a month-end basis is re-picked)
        cr0 = count_row(con, o0["stock_count_id"])
        cid = cr0["count_id"] if cr0 and cr0["kind"] == "spot" else None
    stock = stock_position(con, branch_id, as_of, cyc_d, count_id=cid, until=dlv)
    from .central import central_map, unit_price
    if buffer_pct is None:
        buffer_pct = (o0 or {}).get("buffer_pct")
    pct = float(buffer_pct or 0.0)
    if not (BUFFER_MIN <= pct <= BUFFER_MAX):
        raise ValueError(f"buffer ต้องอยู่ระหว่าง {BUFFER_MIN:g}% ถึง +{BUFFER_MAX:g}%")
    s = suggest(con, branch_id, rnd, cyc_d, as_of=as_of, stock=stock, uplift_pct=pct, delivery=dlv)
    s["stock"] = {"basis": stock["basis"], "deducted": bool(stock["basis"])}
    s["buffer"] = {"pct": pct, "min": BUFFER_MIN, "max": BUFFER_MAX,
                   "reasons": [{"code": k, "label": v} for k, v in BUFFER_REASONS]}
    cut = cutoff_for(dlv)
    s.update(delivery_th=day_th(dlv), late=now_ > cut, open=now_ <= cut,
             hours_left=round((cut - now_).total_seconds() / 3600.0, 1),
             deliveries=next_deliveries(con, branch_id, now_.date(), now=now_))
    # the count this order is made from: what the branch typed for each item, in the unit it counts in
    cnt, cnt_lines = None, {}
    if cid:
        cr = count_row(con, cid)
        if cr and cr["branch_id"] == branch_id and cr["kind"] == "spot":
            cnt_lines = {c: (q, u, qb) for c, q, u, qb in con.execute(
                "SELECT ingredient_code, qty_kitchen, unit, qty_base FROM bc_stock_count_line WHERE count_id=?", (cid,))}
            cnt = {"count_id": cid, "status": cr["status"],
                   "lines": {c: {"qty": q, "unit": u} for c, (q, u, qb) in cnt_lines.items() if q is not None}}
    s["count"] = cnt
    cu = count_units(con, [r["code"] for r in s["rows"]])
    cmap = central_map(con)
    month = s["order_day"][:7]
    for r in s["rows"]:
        unit, factor = cu.get(r["code"], (r.get("base_unit"), 1.0))
        got = cnt_lines.get(r["code"])
        r["count_unit"], r["per_count_unit"] = unit, factor
        r["counted"] = bool(got and got[2] is not None)
        r["count_qty"] = None if not got else (got[0] if (got[1] or unit) == unit or not factor or got[2] is None
                                                else round(got[2] / factor, 4))
        units, kind = order_units(r["need"], r.get("qty_per_recv"), r.get("moq"))
        r["order_units"] = units
        r["order_unit"] = kind
        r["order_units_base"] = order_units(r.get("need_base", r["need"]), r.get("qty_per_recv"), r.get("moq"))[0] if pct else units
        if r["code"] in cmap:
            r["price_per_base"], r["price_source"] = unit_price(con, r["code"], month, cmap)
            r["price_per_sale"] = cmap[r["code"]][1]
    # page 1 is what goes to the centre; page 2 the branch buys from local suppliers itself
    s["central_list"] = bool(cmap)
    s["local_rows"] = [r for r in s["rows"] if r.get("sourcing") == "local"]
    s["rows"] = [r for r in s["rows"] if r.get("sourcing") != "local"]
    oid = find(con, branch_id, rnd, s["order_day"])
    s["order"] = detail(con, oid) if oid else None
    s["reasons"] = [{"code": k, "label": v} for k, v in REASONS]
    s["deviation_pct"] = DEVIATION_PCT
    return s


def save_flow(con, branch_id: int, rnd: str, delivery: date, lines: list, counts, who: str, submit: bool = False,
              note: str = None, buffer: dict = None, ack_late: bool = False, now: datetime = None,
              as_of: date = None) -> dict:
    """นับและสั่ง in one step: write the branch's count of what is on the shelf (a spot count linked to the
    order), re-plan against it, save the order, and — on submit — submit the order and its count in the
    same transaction. `counts` = [{code, qty, unit}] (a qty of None clears one); None leaves the count alone.
    """
    from .forecast import cover_for_delivery
    from .stock import count_row, save as stock_save
    cyc = cover_for_delivery(con, branch_id, rnd, delivery)[0].isoformat()
    oid = find(con, branch_id, rnd, cyc)
    o = _order_row(con, oid) if oid else None
    if o and o["status"] not in ("suggested", "confirmed"):
        raise ValueError(f"ใบสั่งซื้อนี้มีสถานะ “{STATUS_TH.get(o['status'])}” จึงแก้ไขไม่ได้"
                         + (" กรุณากดเปิดแก้ไขก่อน" if o["status"] == "submitted" else ""))
    cr = count_row(con, o["stock_count_id"]) if o and o["stock_count_id"] else None
    cid = cr["count_id"] if cr and cr["kind"] == "spot" else None
    unconverted = []
    if counts is not None and (cid or counts):
        r = stock_save(con, branch_id, [{"code": c.get("code"), "qty": c.get("qty"), "unit": c.get("unit")}
                                        for c in counts], who, kind="spot", count_id=cid,
                       count_date=now.date().isoformat() if now else None)
        cid, unconverted = r["count_id"], r["unconverted"]
    pct = None if buffer is None else (buffer or {}).get("pct")
    p = plan(con, branch_id, rnd, delivery, as_of=as_of, buffer_pct=pct, now=now, count_id=cid)
    out = save(con, branch_id, rnd, p["order_day"], p, lines, who, submit=submit, note=note, buffer=buffer,
               ack_late=ack_late, delivery=delivery.isoformat(), stock_count_id=cid, now=now)
    if submit and cid:                                       # the count is submitted with its order
        stock_save(con, branch_id, [], who, submit=True, kind="spot", count_id=cid)
    out["count"] = None if not cid else {"count_id": cid, "unconverted": unconverted,
                                         "status": count_row(con, cid)["status"]}
    return out
