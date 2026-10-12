# -*- coding: utf-8 -*-
"""Phase 2 · Sprint S8 — the branch order: suggestion net of stock, confirm/edit, submit,
receive (docs/BOM_CENTER_PLAN.md §6 step 5, §7.1-3/6/7, §7.2-3, §7.5).

Stock the forecast deducts, per ingredient:

    on hand now      = the system stock after today's issue slip (stock.system_position:
                       last month-end count + goods received − confirmed ใบตัดเบิก)
    before the cover = what the days between now and the first covered day will use
    on order         = submitted / processed orders not yet received
    projected        = on hand now − before the cover + on order
    order            = forecast for the cover + safety − projected   → whole packs, never negative

PR-A (2026-10-09): the order must be submitted before 13:15 two days before its delivery day
(`forecast.cutoff_for`). A later submit is accepted with a warning: the order is flagged
`late` (the outside warehouse charges a late fee). `cycle_date` is the CUTOFF date;
`delivery_date` / `cutoff_at` / `late` sit beside it.

PR-J1 (management change 2026-10-09 evening): the branch no longer counts before ordering; it
confirms the daily issue slip (issues.py) and the order is made from the system stock after that
slip. An order can be submitted only when the slip of the day is confirmed. Items that were not
in the month-end count have no system figure and are ordered from the forecast alone.

Order life: suggested (draft) → submitted (to Sales Support) → processed (Sales Support,
S9) → received (the branch, which also books a goods receipt for the variance) — or
cancelled. A submitted order can be reopened until Sales Support processes it.
"""
import math
from datetime import date, datetime, timedelta, timezone

from .clock import today as bkk_today
from .fmt import item as _item
from .master import audit
from .units import Converter, unit_word

WEIGHT_TOLERANCE_PCT = 3.0    # PR-W: a line received by WEIGHT (a whole fish) that weighs up to this much less than what was sent is no shortage
DEVIATION_PCT = 20.0          # a confirmed quantity further than this from the suggestion needs a reason
REASONS = (("stock_more", "คงเหลือจริงมากกว่าตามระบบ"), ("stock_less", "คงเหลือจริงน้อยกว่าตามระบบ"),
           ("event", "มีงาน / โปรโมชัน / ลูกค้าจองกลุ่ม"), ("space", "พื้นที่จัดเก็บไม่เพียงพอ"),
           ("quality", "สินค้าล็อตก่อนเสีย / คุณภาพไม่ดี"), ("pack", "ขนาดแพ็ค / ขั้นต่ำผู้จำหน่าย"),
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
        raise ValueError("% ที่ปรับต้องเป็นตัวเลข")
    if not (BUFFER_MIN <= pct <= BUFFER_MAX):
        raise ValueError(f"% ที่ปรับต้องอยู่ระหว่าง {BUFFER_MIN:g}% ถึง +{BUFFER_MAX:g}%")
    if pct == 0:
        return 0.0, None, None
    reason = (buffer.get("reason") or "").strip() or None
    note = (buffer.get("note") or "").strip() or None
    if reason not in BUFFER_REASON_TH:
        raise ValueError("เลือกเหตุผลที่คาดว่ายอดขายจะต่างจากปกติ")
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
        super().__init__(f"เลยเวลาตัดรอบ ({cutoff_th}) ส่งตอนนี้มีค่าปรับล่าช้า · ยืนยันส่ง?")


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
def stock_position(con, branch_id: int, as_of: date, order_day: date) -> dict:
    """The system stock behind an order (ใบตัดเบิก, PR-J1), as of `as_of` — the day of the order, after that
    day's issue slip — plus what is already on order before `order_day`.

    -> {"basis": {count_id, date} | None, "qty": {code: base units}, "on_order": {code: base qty},
        "missing_days": [iso], "through": iso, "estimated_days": [iso], "estimated_qty": {code: base qty}}
    (see stock.system_position: a past day with sales and no confirmed slip is deducted at the BoM of its sales - PR-B4a, 1.3)"""
    from .stock import system_position
    out = system_position(con, branch_id, as_of)
    out["on_order"] = on_order(con, branch_id, order_day)
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
            "buffer_pct", "buffer_reason", "buffer_note", "delivery_date", "cutoff_at", "late", "ack_at", "ack_by",
            "accounting_keyed_at", "accounting_keyed_by", "buffer_sales")
    r = con.execute(f"SELECT {', '.join(cols)} FROM bc_order WHERE order_id=?", (order_id,)).fetchone()
    return dict(zip(cols, r)) if r else None


def find(con, branch_id: int, rnd: str, cycle_date: str):
    r = con.execute("SELECT order_id FROM bc_order WHERE branch_id=? AND round=? AND cycle_date=?"
                    " AND status<>'cancelled' ORDER BY order_id DESC LIMIT 1", (branch_id, rnd, cycle_date)).fetchone()
    return r[0] if r else None


def detail(con, order_id: int, receive: bool = False) -> dict:
    """The order with its lines. `receive=True` (the branch's receive form, PR-U2) adds to every line what the form needs:
    which lines ask for a piece count (`pieces_unit`, `pieces_range`, `pieces_avg`, `pieces_parent_unit`, `pieces_parent_factor`,
    `base_per_order_unit`), the units a wrong pack size can be reported on (`pack`), and the order's pack-size reports."""
    o = _order_row(con, order_id)
    if not o:
        raise ValueError("ไม่พบใบสั่งซื้อ")
    names = {c: (n, b) for c, n, b in con.execute("SELECT code, name_th, base_unit FROM bc_ingredient")}
    recv_lbl = {c: r for c, r in con.execute("SELECT code, recv_unit FROM bc_ingredient")}
    from .hub import SS_REASON_TH
    o["lines"] = [{"code": c, "name": (names.get(c) or (c,))[0], "suggested_units": s, "confirmed_units": cf,
                   "processed_units": p, "received_units": rc, "order_unit": u, "reason_code": rs,
                   "reason_th": REASON_TH.get(rs) if rs else None, "note": n,
                   "ss_reason": sr, "ss_reason_th": SS_REASON_TH.get(sr) if sr else None, "ss_note": sn,
                   "received_pieces": rp}
                  for c, s, cf, p, rc, u, rs, n, sr, sn, rp in con.execute(
                      "SELECT ingredient_code, suggested_units, confirmed_units, processed_units, received_units,"
                      " order_unit, reason_code, note, ss_reason, ss_note, received_pieces FROM bc_order_line WHERE order_id=?"
                      " ORDER BY ingredient_code", (order_id,))]
    for ln in o["lines"]:                      # PR-W: the unit WORD every quantity of the line is written in (never the whole label)
        ln["unit_label"], ln["unit_word"] = unit_of_line(recv_lbl.get(ln["code"]), (names.get(ln["code"]) or (None, None))[1],
                                                         ln["order_unit"])
    if receive:
        _receive_fields(con, o)
    o["status_th"] = STATUS_TH.get(o["status"], o["status"])
    o["buffer_reason_th"] = BUFFER_REASON_TH.get(o.get("buffer_reason")) if o.get("buffer_reason") else None
    from .hub import delivery_of
    o["delivery"] = delivery_of(con, o["branch_id"], o["round"], o["cycle_date"], o.get("delivery_date"))
    o["late"] = int(o.get("late") or 0)
    from .hub import with_values
    with_values(con, [o])                  # value / value_ordered / value_sent / value_label — see hub.with_values
    return o


def unit_of_line(recv_label, base_unit, order_unit) -> tuple:
    """(label, word) of the unit an order line is counted in: the item's receiving unit for a pack line ("กล่อง (10 แพ็ค)" ->
    word "กล่อง"; a pack line of an item with no receiving unit says "แพ็ค"), the base unit for a base line."""
    if order_unit == "base":
        b = str(base_unit or "").strip()
        return b or None, unit_word(b)
    lbl = str(recv_label or "").strip()
    return lbl or None, unit_word(lbl) or "แพ็ค"


def _receive_fields(con, o: dict) -> None:
    from . import pack_reports as _pk
    conv = Converter(con)
    per = {c: p for c, p in con.execute("SELECT code, qty_per_recv FROM bc_ingredient")}
    o["weight_tolerance_pct"] = WEIGHT_TOLERANCE_PCT
    for ln in o["lines"]:
        c = ln["code"]
        step = conv.weight_avg_step(c)
        if step:
            bpo = (per.get(c) or 1.0) if ln["order_unit"] != "base" else 1.0
            sent = ln["processed_units"] if ln["processed_units"] is not None else ln["confirmed_units"]
            ln.update(pieces_unit=step["unit"], pieces_range=[step["rmin"], step["rmax"]] if step["rmin"] is not None else None,
                      pieces_avg=step["qty"], pieces_parent_unit=step["parent"], pieces_parent_factor=step["parent_factor"],
                      base_per_order_unit=bpo,
                      # PR-W: the weight the warehouse sent, in the unit the average is stated in (กิโลกรัม): what the branch weighs against
                      sent_weight=round(float(sent or 0) * bpo / step["parent_factor"], 6))
        else:
            ln["pieces_unit"] = None
        try:
            ln["pack"] = _pk.item_info(con, c, conv)
        except ValueError:
            ln["pack"] = None
    o["pack_reports"] = _pk.for_orders(con, order_ids=[o["order_id"]]).get(o["order_id"], [])


STATUS_TH = {"suggested": "ร่าง (ยังไม่ส่ง)", "confirmed": "ร่าง (ยังไม่ส่ง)", "submitted": "ส่งแล้ว (รอคลังจัดส่ง)",
             "processed": "คลังจัดส่งแล้ว", "received": "รับสินค้าแล้ว", "cancelled": "ยกเลิก"}


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
         delivery: str = None, now: datetime = None) -> dict:
    """Create (from the suggestion) or update the cycle's order; optionally submit it.

    The suggested quantities are taken from the suggestion when the order is first created and
    follow it (the system stock) while the order is a draft; from the moment it is submitted they
    are frozen — later edits change only what the branch confirms. `buffer` (PR-19, None = keep) is
    the branch's expectation {pct, reason, note}: the suggestion stays the engine's
    base number, the confirmed quantities start from the buffered one, and the
    deviation rule judges against base × (1 + pct/100).

    PR-A: `cycle_date` is the cutoff date; `delivery` the delivery day; a submit after the cutoff
    raises LateSubmit unless `ack_late`, and is flagged `late`. PR-J1: the order is made from the system
    stock (`stock_count_id` keeps the month-end count it starts from) and can be submitted only when the
    branch has CONFIRMED its issue slip of the day.

    PR-V: `buffer` may carry `sales` — the sales (฿, ex VAT) the branch expects for the cover period, kept on the order
    (`buffer_sales`). For a new branch planned from its reference branch it is the figure the rows were built on.

    This is where the order's write transaction begins: whatever is read before (the plan) holds no lock."""
    from .central import central_map
    from .forecast import _now_for, cutoff_for, cutoff_th
    from . import issues as _is
    cmap = central_map(con)
    if (suggestion.get("ref_mode") or {}).get("needs_sales"):          # a new branch cannot order without saying what it expects to sell
        raise ValueError(suggestion["ref_mode"]["message"])
    delivery = delivery or suggestion.get("delivery")
    cut_iso = suggestion.get("cutoff_at") or (cutoff_for(date.fromisoformat(delivery)).isoformat() if delivery else None)
    basis_id = ((suggestion.get("stock") or {}).get("basis") or {}).get("count_id")
    oid = find(con, branch_id, rnd, cycle_date)
    if oid:
        o = _order_row(con, oid)
        if o["status"] not in ("suggested", "confirmed"):
            raise ValueError(f"ใบสั่งซื้อนี้มีสถานะ “{STATUS_TH.get(o['status'])}” จึงแก้ไขไม่ได้"
                             + (" · กด “เปิดแก้ไข” ก่อน" if o["status"] == "submitted" else ""))
        con.execute("UPDATE bc_order SET delivery_date=COALESCE(?, delivery_date), cutoff_at=COALESCE(?, cutoff_at),"
                    " stock_count_id=COALESCE(?, stock_count_id) WHERE order_id=?",
                    (delivery, cut_iso, basis_id, oid))
        # while the order is a draft its suggestion follows the system stock; it is frozen from the moment of submit
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
        cur = con.execute("INSERT INTO bc_order(branch_id, cycle_date, round, run_id, status, note, stock_count_id,"
                          " delivery_date, cutoff_at) VALUES(?,?,?,?, 'suggested', ?, ?, ?, ?)",
                          (branch_id, cycle_date, rnd, suggestion.get("run_id"), note, basis_id,   # the month-end count it starts from
                           delivery, cut_iso))
        oid = cur.lastrowid
        con.executemany("INSERT INTO bc_order_line(order_id, ingredient_code, suggested_units, confirmed_units,"
                        " order_unit) VALUES(?,?,?,?,?)",
                        [(oid, r["code"], r.get("order_units_base", r["order_units"]), r["order_units"], r["order_unit"])
                         for r in suggestion["rows"]])
    if buffer is not None:
        pct, reason, bnote = buffer_clean(buffer)
        from .per_sales import sales_clean
        con.execute("UPDATE bc_order SET buffer_pct=?, buffer_reason=?, buffer_note=?, buffer_sales=? WHERE order_id=?",
                    (pct or None, reason, bnote, sales_clean((buffer or {}).get("sales")), oid))
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
                raise ValueError(f"{_item(con, c)}: จำนวนต้องเป็นตัวเลข")
            if q < 0:
                raise ValueError(f"{_item(con, c)}: จำนวนต้องไม่ติดลบ")
        rs = (ln.get("reason_code") or "").strip() or None
        if rs and rs not in REASON_TH:
            raise ValueError(f"{_item(con, c)}: เหตุผลไม่อยู่ในรายการ")
        if c not in have:                                   # an item the suggestion did not include
            if not con.execute("SELECT 1 FROM bc_ingredient WHERE code=?", (c,)).fetchone():
                raise ValueError(f"ไม่พบรหัส {c} ในทะเบียน")
            if cmap and c not in cmap:
                raise ValueError(f"{_item(con, c)} ไม่อยู่ในบัญชีสินค้าส่วนกลาง · สาขาซื้อเอง")
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
    cut = _cutoff_dt(con, o2)
    slip_today = _is.status_of(con, branch_id, _now_for(None, now).date())
    out = {"order_id": oid, "status": "suggested", "needs_reason": sorted(set(missing)), "value": value,
           "value_ordered": value, "value_label": _hub_label(),
           "late": int(o2["late"] or 0), "cutoff_at": cut.isoformat(), "delivery": o2["delivery_date"] or delivery,
           "slip_today": slip_today}
    if submit:
        if missing:
            raise ValueError(f"จำนวนที่แก้ไขต่างจากที่แนะนำเกิน {DEVIATION_PCT:g}% · เลือกเหตุผล: " + ", ".join(sorted(set(missing))[:5]))
        if not con.execute("SELECT 1 FROM bc_order_line WHERE order_id=? AND confirmed_units>0", (oid,)).fetchone():
            raise ValueError("ใบสั่งซื้อยังไม่มีรายการสินค้า")
        if not _is.is_confirmed(slip_today):                # ใบตัดเบิก: the order is made from the stock AFTER today's issue
            raise ValueError("ต้องยืนยันใบตัดเบิกของวันนี้ก่อนส่งใบสั่งซื้อ")
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
                                                                     "count_id": o2["stock_count_id"],
                                                                     "slip_id": slip_today["slip_id"]})
        out.update(status="submitted", late=1 if late else 0)
    return out


def _hub_label() -> str:
    from .hub import VALUE_ORDERED_TH
    return VALUE_ORDERED_TH


def _value(con, order_id: int, cycle_date: str) -> float:
    """What the branch ordered, in baht: the one pricing function of hub.py (the order value has no copy)."""
    from .hub import _value as _hub_value
    return _hub_value(con, order_id, "confirmed_units", cycle_date[:7])


def reopen(con, branch_id: int, order_id: int, who: str):
    o = _order_row(con, order_id)
    if not o or o["branch_id"] != branch_id:
        raise ValueError("ไม่พบใบสั่งซื้อของสาขานี้")
    if o["status"] != "submitted":
        raise ValueError(f"เปิดแก้ไขได้เฉพาะใบสั่งซื้อที่ส่งแล้วและคลังยังไม่ยืนยันยอดส่ง (สถานะปัจจุบัน: {STATUS_TH.get(o['status'])})")
    con.execute("UPDATE bc_order SET status='suggested', submitted_at=NULL, late=0 WHERE order_id=?", (order_id,))
    audit(con, who, "order_reopen", "bc_order", str(order_id))


def cancel(con, branch_id: int, order_id: int, who: str):
    o = _order_row(con, order_id)
    if not o or o["branch_id"] != branch_id:
        raise ValueError("ไม่พบใบสั่งซื้อของสาขานี้")
    if o["status"] not in ("suggested", "confirmed", "submitted"):
        raise ValueError("ยกเลิกไม่ได้ เนื่องจากคลังจัดส่งแล้ว")
    con.execute("UPDATE bc_order SET status='cancelled' WHERE order_id=?", (order_id,))
    audit(con, who, "order_cancel", "bc_order", str(order_id))


def _pieces(name: str, unit: str, raw) -> float:
    """The pieces counted for a line received by weight: a whole number >= 1 (L1)."""
    msg = f"{name}: กรอกจำนวน{unit}ที่รับ (จำนวนเต็ม)"
    try:
        p = float(str(raw).replace(",", "").strip()) if raw is not None and str(raw).strip() != "" else None
    except (TypeError, ValueError):
        p = None
    if p is None or p != int(p) or p < 1:
        raise ValueError(msg)
    return float(int(p))


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
        raise ValueError("ทำเครื่องหมายยืนยันการตรวจสินค้าก่อนบันทึก")
    got = {}
    for ln in lines or []:
        c = (ln.get("code") or "").strip()
        if ln.get("received_units") is None and ln.get("weight") is None:
            raise ValueError(f"{_item(con, c)}: ต้องระบุจำนวนที่รับ")
        try:
            q = float(ln.get("received_units") or 0)
        except (TypeError, ValueError):
            raise ValueError(f"{_item(con, c)}: จำนวนต้องเป็นตัวเลข")
        if q < 0:
            raise ValueError(f"{_item(con, c)}: จำนวนต้องไม่ติดลบ")
        got[c] = (q, (ln.get("reason") or "").strip(), (ln.get("note") or "").strip() or None, ln.get("pieces"), ln.get("weight"))
    units, sent = {}, {}
    for c, u, pu, cf in con.execute("SELECT ingredient_code, order_unit, processed_units, confirmed_units"
                                    " FROM bc_order_line WHERE order_id=?", (order_id,)):
        units[c] = u
        sent[c] = float(pu if pu is not None else (cf or 0))
    recv = {c: r for c, r in con.execute("SELECT code, recv_unit FROM bc_ingredient")}
    base = {c: b for c, b in con.execute("SELECT code, base_unit FROM bc_ingredient")}
    names = {c: n for c, n in con.execute("SELECT code, name_th FROM bc_ingredient")}
    conv = Converter(con)
    pieces = {}
    rlines, short, over = [], [], []
    per = {c: p for c, p in con.execute("SELECT code, qty_per_recv FROM bc_ingredient")}
    tolerated = []
    for c, (q, reason, note, pcs_raw, wt_raw) in list(got.items()):
        if c not in units:
            raise ValueError(f"{_item(con, c)} ไม่อยู่ในใบสั่งซื้อนี้")
        step = conv.weight_avg_step(c)
        within = False
        if wt_raw is not None and str(wt_raw).strip() != "":
            # PR-W: a line received by weight - the branch weighs what arrived; that weight IS the received quantity (in the
            # line's order unit). A shortfall of up to WEIGHT_TOLERANCE_PCT of the weight sent is not a shortage.
            nm = names.get(c) or c
            if not step:
                raise ValueError(f"{nm}: รายการนี้ไม่ต้องกรอกน้ำหนักที่ชั่งได้")
            try:
                w = float(str(wt_raw).replace(",", "").strip())
            except (TypeError, ValueError):
                raise ValueError(f"{nm}: น้ำหนักที่ชั่งได้ต้องเป็นตัวเลข")
            if w < 0:
                raise ValueError(f"{nm}: น้ำหนักที่ชั่งได้ต้องไม่ติดลบ")
            bpo = (per.get(c) or 1.0) if units[c] != "base" else 1.0
            q = round(w * step["parent_factor"] / bpo, 6)
            got[c] = (q, reason, note, pcs_raw, wt_raw)
            gap_pct = (sent[c] - q) / sent[c] * 100.0 if sent[c] > 0 else 0.0
            within = 0 < gap_pct <= WEIGHT_TOLERANCE_PCT + 1e-9
            if within:
                tolerated.append({"code": c, "sent_units": sent[c], "received_units": q, "short_pct": round(gap_pct, 2)})
        if step and q > 0:                    # PR-U2 (L1): goods sold by weight whose pieces differ — the branch counts the pieces
            pieces[c] = _pieces(names.get(c) or c, step["unit"], pcs_raw)
        gap = sent[c] - q
        if within:
            continue                          # PR-W: booked at the weight received, no reason and no claim
        if gap > 1e-9:
            if reason not in _cl.REASON_TH:
                raise ValueError(f"{_item(con, c)}: รับจริงน้อยกว่าที่คลังส่ง ({sent[c]:g}) · เลือกสาเหตุของส่วนต่าง")
            if reason == "other" and not note:
                raise ValueError(f"{_item(con, c)}: สาเหตุ “อื่น ๆ” ต้องระบุคำอธิบาย")
            short.append({"code": c, "claim_units": round(gap, 6), "reason": reason, "note": note,
                          "sent_units": sent[c], "received_units": q})
        elif gap < -1e-9:
            over.append({"code": c, "sent_units": sent[c], "received_units": q, "over_units": round(-gap, 6)})
    for c, (q, _r, _n, _p, _w) in got.items():
        con.execute("UPDATE bc_order_line SET received_units=?, received_pieces=? WHERE order_id=? AND ingredient_code=?",
                    (q, pieces.get(c), order_id, c))
        if q > 0:
            u = units[c]
            rlines.append({"code": c, "qty": q, "unit": (recv.get(c) if u and u != "base" else base.get(c)),
                           "pieces": pieces.get(c)})
    rd = receipt_date or bkk_today().isoformat()
    rec = add_receipt(con, branch_id, rd, rlines, who, ref=f"ORDER-{order_id}", source="order") if rlines else None
    stamp = now()
    con.execute("UPDATE bc_order SET status='received', received_at=?, ack_at=?, ack_by=? WHERE order_id=?",
                (stamp, stamp, who, order_id))
    cl = _cl.create_from_receive(con, branch_id, order_id, short, claim_note, who) if short else None
    audit(con, who, "order_receive", "bc_order", str(order_id), None,
          {"lines": len(rlines), "short": len(short), "over": over, "within_tolerance": tolerated,
           "claim_id": cl and cl["claim_id"], "ack": True})
    return {"order_id": order_id, "status": "received", "receipt_id": rec and rec["receipt_id"],
            "claim_id": cl and cl["claim_id"], "claim_lines": cl["lines"] if cl else 0, "over": over,
            "within_tolerance": tolerated}


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
    from .hub import with_values
    return with_values(con, out)


def previous(con, branch_id: int, rnd: str, cycle_date: str) -> dict:
    """1.9: the last order of the SAME round the branch actually sent (submitted, processed or received) before this cycle — what the
    confirm dialog compares the order to: "88 รายการ · ฿281,292 · รอบก่อน ฿262,700 (+7%)". None when the round has no earlier order.

    -> {round, round_th, delivery, delivery_th, cycle_date, value (the value ordered), n (lines ordered), lines: [{code, name,
    units, unit_word, value}]}; every line is priced for the month of `cycle_date`, the month of the order being checked, so a
    difference between two lines is a difference of quantity."""
    from .forecast import ROUND_TH
    from .fmt import day_th
    from .hub import _base, _pricer, delivery_of
    r = con.execute("SELECT order_id, delivery_date FROM bc_order WHERE branch_id=? AND round=? AND cycle_date<?"
                    " AND status IN ('submitted','processed','received') ORDER BY cycle_date DESC, order_id DESC LIMIT 1",
                    (branch_id, rnd, cycle_date)).fetchone()
    if not r:
        return None
    o = detail(con, r[0])
    per, price = _pricer(con)
    month = str(cycle_date)[:7]
    lines = []
    for ln in o["lines"]:
        u = float(ln["confirmed_units"] or 0)
        if u <= 0:
            continue
        p = price(ln["code"], month)
        lines.append({"code": ln["code"], "name": ln["name"], "units": u, "unit_word": ln["unit_word"],
                      "value": round(_base(u, ln["order_unit"], per.get(ln["code"])) * p, 2) if p else 0.0})
    d = delivery_of(con, branch_id, rnd, o["cycle_date"], o.get("delivery_date"))
    return {"round": rnd, "round_th": ROUND_TH.get(rnd, rnd), "delivery": d, "delivery_th": day_th(d), "cycle_date": o["cycle_date"],
            "value": o["value_ordered"], "n": len(lines), "lines": lines}


def next_round(con, branch_id: int, today: date, now: datetime = None) -> str:
    """The round of the delivery a branch should be working on from `today`: the nearest delivery
    whose cutoff has not passed (else the nearest). What the branch page opens on when nobody picked."""
    from .forecast import next_deliveries, pick_delivery
    return pick_delivery(next_deliveries(con, branch_id, today, now=now))["round"]


def order_units(need: float, per, moq, safety: float = 0.0, big: bool = False) -> tuple:
    """(units, unit) — whole packs when the pack size is known, else base units rounded up.
    A BIG pack (`big`) is rounded down for a small remainder the safety stock covers: see order_units_ex."""
    return order_units_ex(need, per, moq, safety, big)[:2]


def order_units_ex(need: float, per, moq, safety: float = 0.0, big: bool = False) -> tuple:
    """(units, unit, rounded_down) — PR-B4a (1.7): units.to_order_units_ex for a pack of known size (a remainder under
    units.ROUND_DOWN_FRACTION of a big pack that the safety stock covers is not ordered), base units rounded up otherwise."""
    from .units import to_order_units_ex
    if need <= 0:
        return 0.0, ("pack" if per else "base"), False
    if per:
        u, down = to_order_units_ex(need, per, moq or 1, safety=safety, big=big)
        return float(u), "pack", down
    return float(math.ceil(need)), "base", False


def plan(con, branch_id: int, rnd: str, delivery: date, as_of: date = None, buffer_pct: float = None,
         now: datetime = None, expected_sales: float = None) -> dict:
    """The suggestion for one delivery, net of the SYSTEM stock, ready to become an order.

    `delivery` is the delivery day of the round. The stock is the one the branch's issue slips leave on
    `now`'s day (stock.system_position: last month-end count + goods received − confirmed slips, counted
    items only); `as_of` is only the last day of sales the forecast reads. `buffer_pct` (PR-19): the
    branch's expectation to apply; None = the cycle's order's own, if any. `now` is the clock (tests pass
    it). Reads only — no write transaction is opened here.

    PR-V: `expected_sales` (฿ ex VAT for the cover period, None = the cycle's order's own `buffer_sales`) is used only by a
    new branch (fewer than 14 trading days of sales) that has a reference branch set (per_sales.REF_KEY): its rows are then
    the reference branch's usage per ฿100,000 × this figure, and `ref_mode` says so (`needs_sales` while it is missing)."""
    from .forecast import _now_for, cover_for_delivery, cutoff_for, day_th, next_deliveries, suggest
    from .stock import count_units
    from . import issues as _is
    cyc_d, dlv, _cf, _ct = cover_for_delivery(con, branch_id, rnd, delivery)
    cyc = cyc_d.isoformat()
    now_ = _now_for(None, now)
    stock_day = now_.date()
    last = con.execute("SELECT MAX(day) FROM sales_sku_daily WHERE branch_id=?", (branch_id,)).fetchone()[0]
    as_of = as_of or (date.fromisoformat(last) if last else cyc_d - timedelta(days=1))
    oid0 = find(con, branch_id, rnd, cyc)
    o0 = _order_row(con, oid0) if oid0 else None
    stock = stock_position(con, branch_id, stock_day, cyc_d)
    from .central import central_map, unit_price
    if buffer_pct is None:
        buffer_pct = (o0 or {}).get("buffer_pct")
    pct = float(buffer_pct or 0.0)
    if not (BUFFER_MIN <= pct <= BUFFER_MAX):
        raise ValueError(f"% ที่ปรับต้องอยู่ระหว่าง {BUFFER_MIN:g}% ถึง +{BUFFER_MAX:g}%")
    from . import forecast as _fc
    from . import per_sales as _ps
    if expected_sales is None:
        expected_sales = (o0 or {}).get("buffer_sales")
    with _fc._plan_slots:                  # only the CPU-bound part: a few plans at a time, so a writer keeps the GIL
        s = suggest(con, branch_id, rnd, cyc_d, as_of=as_of, stock=stock, uplift_pct=pct, delivery=dlv,
                    ref_branch=_ps.resolved_reference(con, branch_id), expected_sales=expected_sales)
    if s.get("ref_mode"):                  # a new branch: the expected sales ARE the expectation, no % on top
        pct = 0.0
    s["expected_sales"] = expected_sales
    s["stock"] = {"basis": stock["basis"], "deducted": bool(stock["basis"]), "through": stock["through"],
                  # today's slip is reported on its own (slip_today); the days BEFORE it are the warning
                  "missing_days": [d for d in stock["missing_days"] if d != stock_day.isoformat()],
                  "estimated_days": stock["estimated_days"],           # PR-B4a (1.3): of those, the days deducted at the BoM of the sales
                  "slip_today": _is.status_of(con, branch_id, stock_day)}
    s["buffer"] = {"pct": pct, "min": BUFFER_MIN, "max": BUFFER_MAX,
                   "reasons": [{"code": k, "label": v} for k, v in BUFFER_REASONS]}
    cut = cutoff_for(dlv)
    s.update(delivery_th=day_th(dlv), late=now_ > cut, open=now_ <= cut,
             hours_left=round((cut - now_).total_seconds() / 3600.0, 1),
             deliveries=next_deliveries(con, branch_id, now_.date(), now=now_))
    cu = count_units(con, [r["code"] for r in s["rows"]])
    cmap = central_map(con)
    month = s["order_day"][:7]
    from .units import Converter
    conv = Converter(con)
    for r in list(s["rows"]) + list(s.get("local_rows") or []):
        au = conv.approx_unit(r["code"]) if r["code"] in conv.ladders else None
        if au:                                           # PR-U: "≈ 2.1 ตัว" beside a weight ordered for whole pieces
            r["approx_unit"], r["approx_per_unit"] = au
    for r in s["rows"]:
        unit, factor = cu.get(r["code"], (r.get("base_unit"), 1.0))
        r["count_unit"], r["per_count_unit"] = unit, factor
        sq = stock["qty"].get(r["code"])               # the system stock in the unit the kitchen counts in
        r["system_qty"] = None if sq is None else round(sq / (factor or 1.0), 4)
        r["system_unit"] = unit
        eq = stock["estimated_qty"].get(r["code"])     # PR-B4a (1.3): how much of that figure is the BoM of sales, not a confirmed slip
        r["estimated_qty"] = round(eq / (factor or 1.0), 4) if eq else None
        units, kind, down = order_units_ex(r["need"], r.get("qty_per_recv"), r.get("moq"), r.get("safety") or 0.0, r.get("big_pack"))
        r["order_units"] = units
        r["order_unit"] = kind
        ne = r.get("need_no_est")                          # 1.3 · does the estimate change what is ordered? (packs with it ≠ packs without it)
        r["estimate_changes"] = bool(ne is not None and order_units_ex(ne, r.get("qty_per_recv"), r.get("moq"), r.get("safety") or 0.0,
                                                                        r.get("big_pack"))[0] != units)
        r["round_down"] = down                              # 1.7 · "ปัดลง (เหลือพอ)": a remainder under 20 % of a big pack was left unordered
        r["order_units_base"] = order_units(r.get("need_base", r["need"]), r.get("qty_per_recv"), r.get("moq"),
                                            r.get("safety") or 0.0, r.get("big_pack"))[0] if pct else units
        if r["code"] in cmap:
            r["price_per_base"], r["price_source"] = unit_price(con, r["code"], month, cmap)
            r["price_per_sale"] = cmap[r["code"]][1]
    # page 1 is what goes to the centre; page 2 the branch buys from local suppliers itself
    s["central_list"] = bool(cmap)
    s["local_rows"] = [r for r in s["rows"] if r.get("sourcing") == "local"]
    s["rows"] = [r for r in s["rows"] if r.get("sourcing") != "local"]
    oid = find(con, branch_id, rnd, s["order_day"])
    s["order"] = detail(con, oid) if oid else None
    s["previous"] = previous(con, branch_id, rnd, s["order_day"])           # 1.9: the order the confirm dialog compares this one to
    s["reasons"] = [{"code": k, "label": v} for k, v in REASONS]
    s["deviation_pct"] = DEVIATION_PCT
    return s


def _sales_of(buffer):
    """The sales (฿) a buffer dict carries, or None (a bad value is refused later by save())."""
    try:
        from .per_sales import sales_clean
        return sales_clean((buffer or {}).get("sales"))
    except ValueError:
        return None


def save_flow(con, branch_id: int, rnd: str, delivery: date, lines: list, who: str, submit: bool = False,
              note: str = None, buffer: dict = None, ack_late: bool = False, now: datetime = None,
              as_of: date = None) -> dict:
    """Save (or submit) the branch's order for one delivery: status guard → plan → save.

    The plan is computed BEFORE anything is written. It is the CPU-heavy part (the walk over the sales
    behind the usage history); the load test of 2026-10-09 (docs/LOAD_TEST_30_BRANCHES.md) showed that
    computing it AFTER a first write held the SQLite write lock for seconds and answered "database is
    locked" to about 15 branches saving at once. The write transaction now begins inside `save()`."""
    from .forecast import cover_for_delivery
    cyc = cover_for_delivery(con, branch_id, rnd, delivery)[0].isoformat()
    oid = find(con, branch_id, rnd, cyc)
    o = _order_row(con, oid) if oid else None
    if o and o["status"] not in ("suggested", "confirmed"):
        raise ValueError(f"ใบสั่งซื้อนี้มีสถานะ “{STATUS_TH.get(o['status'])}” จึงแก้ไขไม่ได้"
                         + (" · กด “เปิดแก้ไข” ก่อน" if o["status"] == "submitted" else ""))
    pct = None if buffer is None else (buffer or {}).get("pct")
    p = plan(con, branch_id, rnd, delivery, as_of=as_of, buffer_pct=pct, now=now,
             expected_sales=_sales_of(buffer))
    return save(con, branch_id, rnd, p["order_day"], p, lines, who, submit=submit, note=note, buffer=buffer,
                ack_late=ack_late, delivery=delivery.isoformat(), now=now)
