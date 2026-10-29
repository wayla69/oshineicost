# -*- coding: utf-8 -*-
"""bom_center.reschedule — เลื่อนรอบส่ง: ONE delivery of a round moves to another day (owner 2026-10-29).

Sales Support (ประสานงานขาย) sets each branch's weekly delivery days (bc_order_calendar). Sometimes one delivery has to move -
this Friday's round goes on Saturday. A reschedule names (branch, round, the REGULAR delivery date) → the new date, with a
required reason; several branches can be recorded at once (one truck serves several branches). The weekly calendar is not
changed. It can be edited or cancelled (a cancel needs a reason too); every change is audited (bc_audit).

What follows from a reschedule:
  * the cycle keeps its identity: the order is still keyed by the REGULAR delivery date (bc_order.delivery_date, cycle_date) and
    the cutoff stays the regular one (13:15 two days before the regular day) - a later delivery never makes an order late;
  * the plan follows the new date: cover_for_delivery (forecast.py) starts the cover on the ACTUAL delivery day and ends it the day
    before the next ACTUAL delivery, so the order before a postponed delivery covers the extra day (delivery_date_for below);
  * an order of that branch + round + regular date carries `delay` ({from, to, reason, by, at}) - an extra field, the order's
    lifecycle status is untouched, so nothing that filters on status changes; the screens show the chip "ล่าช้า";
  * the branch is told at once: a Web Push to the branch login(s) (kind "supply") and a notice on its order page and หน้างาน.

Only a LATER date is accepted (1..MAX_SHIFT_DAYS days after the regular one): moving a delivery earlier would also move the
cutoff, which every screen and the late fee read from the regular day.
"""
import sqlite3
from datetime import date, datetime, timedelta

from .master import audit, now

REASONS = (("transport", "รถขนส่งไม่พร้อม"), ("supplier_late", "ของจากผู้ขายมาช้า"), ("holiday", "วันหยุด"), ("other", "อื่น ๆ"))
REASON_TH = dict(REASONS)
NOTE_MAX = 300
MAX_SHIFT_DAYS = 6
SUPPLY_TAG = "oshinei-supply-moved"
PUSH_SEND = None                       # tests: a send function; the push then runs inline (no thread, no VAPID check)

DDL = (
    """CREATE TABLE IF NOT EXISTS bc_round_reschedule(
         id INTEGER PRIMARY KEY AUTOINCREMENT,
         batch_id TEXT,                              -- the rows recorded together (several branches of one truck)
         branch_id INTEGER NOT NULL,
         round TEXT NOT NULL CHECK(round IN ('1','2','salmon')),
         orig_date TEXT NOT NULL,                    -- the regular delivery day of the calendar (the cycle's key)
         new_date TEXT NOT NULL,                     -- the day the delivery actually comes
         reason_code TEXT NOT NULL, reason_note TEXT,
         status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','cancelled')),
         created_by TEXT, created_at TEXT, updated_by TEXT, updated_at TEXT,
         cancel_reason TEXT, cancelled_by TEXT, cancelled_at TEXT)""",
    "CREATE UNIQUE INDEX IF NOT EXISTS ux_bc_round_reschedule_active ON bc_round_reschedule(branch_id, round, orig_date)"
    " WHERE status='active'",
    "CREATE INDEX IF NOT EXISTS ix_bc_round_reschedule_new ON bc_round_reschedule(new_date)",
)
_COLS = ("id", "batch_id", "branch_id", "round", "orig_date", "new_date", "reason_code", "reason_note", "status",
         "created_by", "created_at", "updated_by", "updated_at", "cancel_reason", "cancelled_by", "cancelled_at")


def _d(v) -> date:
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    return date.fromisoformat(str(v)[:10])


def _row(r) -> dict:
    return dict(zip(_COLS, r))


def _active(con, branch_id: int) -> dict:
    """{(round, orig_date iso): row} of the branch's active reschedules; {} on a database without the table."""
    try:
        rows = con.execute(f"SELECT {', '.join(_COLS)} FROM bc_round_reschedule WHERE branch_id=? AND status='active'",
                           (int(branch_id),)).fetchall()
    except sqlite3.OperationalError:
        return {}
    return {(r[3], r[4]): _row(r) for r in rows}


def active_for(con, branch_id: int, rnd: str, regular) -> dict:
    """The active reschedule of one delivery (branch, round, regular date), or None."""
    if regular is None:
        return None
    try:
        r = con.execute(f"SELECT {', '.join(_COLS)} FROM bc_round_reschedule WHERE branch_id=? AND round=? AND orig_date=?"
                        " AND status='active'", (int(branch_id), str(rnd), _d(regular).isoformat())).fetchone()
    except (sqlite3.OperationalError, ValueError, TypeError):
        return None
    return _row(r) if r else None


def actual(con, branch_id: int, rnd: str, regular) -> date:
    """The day a regular delivery actually comes: the new date of its active reschedule, else the regular day itself."""
    r = active_for(con, branch_id, rnd, regular)
    return _d(r["new_date"]) if r else _d(regular)


def regular_of(con, branch_id: int, rnd: str, cycle_date) -> date:
    """The regular delivery day of a cycle: the first delivery weekday of the round after its cutoff date."""
    from .forecast import _next_on_or_after, calendar
    dw = int(calendar(con, branch_id)[rnd]["delivery_weekday"])
    return _next_on_or_after(_d(cycle_date) + timedelta(days=1), dw)


def delivery_date_for(con, branch_id: int, rnd: str, cycle_date) -> date:
    """THE delivery date of a cycle (branch, round, cutoff date), the one-off reschedule applied: what the plan covers from."""
    return actual(con, branch_id, rnd, regular_of(con, branch_id, rnd, cycle_date))


def delay_of(con, branch_id: int, rnd: str, regular, row: dict = None) -> dict:
    """The `delay` field of an order / a delivery: {from, to, reason, …} when its delivery was moved, else None."""
    r = row or active_for(con, branch_id, rnd, regular)
    if not r:
        return None
    return _delay(con, r)


def _delay(con, r: dict) -> dict:
    from .fmt import at_th, day_short_th, who
    reason = REASON_TH.get(r["reason_code"], r["reason_code"])
    note = (r.get("reason_note") or "").strip()
    text = note if r["reason_code"] == "other" and note else (f"{reason} · {note}" if note else reason)
    at = r.get("updated_at") or r.get("created_at")
    by = r.get("updated_by") or r.get("created_by")
    return {"id": r["id"], "from": r["orig_date"], "to": r["new_date"], "from_th": day_short_th(r["orig_date"]),
            "to_th": day_short_th(r["new_date"]), "reason_code": r["reason_code"], "reason": text, "by": by,
            "by_th": who(con, by) if by else "-", "at": at, "at_th": at_th(at) if at else "-",
            "label": "ล่าช้า"}


def add_delays(con, rows: list, key: str = "delivery") -> list:
    """Every order dict of `rows` (branch_id, round, `key` = its regular delivery day) gets `delay` (None when not moved)."""
    cache: dict = {}
    for o in rows:
        bid = o.get("branch_id")
        if bid is None:
            o["delay"] = None
            continue
        m = cache.get(bid)
        if m is None:
            m = cache[bid] = _active(con, bid)
        r = m.get((str(o.get("round")), str(o.get(key) or "")[:10]))
        o["delay"] = _delay(con, r) if r else None
    return rows


# ── writes ───────────────────────────────────────────────────────────────────
def _clean_reason(code, note, what: str = "เหตุผล") -> tuple:
    code = (code or "").strip()
    if code not in REASON_TH:
        raise ValueError(f"เลือก{what}")
    note = " ".join(str(note or "").split())[:NOTE_MAX]
    if code == "other" and not note:
        raise ValueError("เหตุผล “อื่น ๆ” ต้องระบุรายละเอียด")
    return code, note or None


def _check_dates(orig: date, new: date) -> None:
    if new <= orig:
        raise ValueError("วันส่งใหม่ต้องอยู่หลังวันส่งตามปกติ")
    if (new - orig).days > MAX_SHIFT_DAYS:
        raise ValueError(f"เลื่อนได้ไม่เกิน {MAX_SHIFT_DAYS} วันจากวันส่งตามปกติ")


def _branch_names(con, ids) -> dict:
    ids = [int(i) for i in ids]
    if not ids:
        return {}
    return {r[0]: r[1] for r in con.execute(f"SELECT branch_id, name FROM branches WHERE branch_id IN ({','.join('?' * len(ids))})", ids)}


def _audit_text(r: dict, name: str = "") -> str:
    from .forecast import ROUND_TH
    return (f"{name} {ROUND_TH.get(r['round'], r['round'])} {r['orig_date']} -> {r['new_date']} · "
            f"{REASON_TH.get(r['reason_code'], r['reason_code'])}" + (f" · {r['reason_note']}" if r.get("reason_note") else "")).strip()


def record(con, who: str, branch_ids: list, rnd: str, orig_date, new_date, reason_code: str, reason_note: str = None,
           today: date = None) -> list:
    """Record one moved delivery for one or several branches. Every branch must deliver that round on `orig_date` by its
    calendar, and none may already have an active reschedule of it (edit that one instead). -> the new rows."""
    from .forecast import DEFAULT_CAL, ROUND_TH, WD_TH, _wd, calendar
    from .clock import today as _today
    if rnd not in DEFAULT_CAL:
        raise ValueError("เลือกรอบส่ง")
    try:
        orig, new = _d(orig_date), _d(new_date)
    except (TypeError, ValueError):
        raise ValueError("วันที่ไม่ถูกต้อง")
    _check_dates(orig, new)
    today = today or _today()
    if orig < today:
        raise ValueError("วันส่งตามปกติผ่านไปแล้ว เลื่อนไม่ได้")
    code, note = _clean_reason(reason_code, reason_note)
    ids = sorted({int(b) for b in (branch_ids or [])})
    if not ids:
        raise ValueError("เลือกสาขาอย่างน้อย 1 สาขา")
    names = _branch_names(con, ids)
    missing = [str(i) for i in ids if i not in names]
    if missing:
        raise ValueError("ไม่พบสาขาที่เลือก")
    wrong = [names[b] for b in ids if int(calendar(con, b)[rnd]["delivery_weekday"]) != _wd(orig)]
    if wrong:
        raise ValueError(f"{ROUND_TH[rnd]} ของ {', '.join(wrong)} ไม่ได้ส่งวัน{WD_TH[_wd(orig)]} · เลือกวันส่งตามปกติของรอบนี้")
    dup = [names[b] for b in ids if active_for(con, b, rnd, orig)]
    if dup:
        raise ValueError(f"{', '.join(dup)} มีการเลื่อน{ROUND_TH[rnd]} วันที่ {orig.day}/{orig.month} อยู่แล้ว · แก้ไขรายการเดิม")
    at = now()
    batch = f"{at}|{who}"
    out = []
    for b in ids:
        cur = con.execute("INSERT INTO bc_round_reschedule(batch_id, branch_id, round, orig_date, new_date, reason_code, reason_note,"
                          " status, created_by, created_at) VALUES(?,?,?,?,?,?,?,'active',?,?)",
                          (batch, b, rnd, orig.isoformat(), new.isoformat(), code, note, who, at))
        r = get(con, cur.lastrowid)
        audit(con, who, "round_reschedule", "bc_round_reschedule", f"{b}|{rnd}|{orig.isoformat()}", None, _audit_text(r, names[b]))
        out.append(r)
    return out


def get(con, rid: int) -> dict:
    r = con.execute(f"SELECT {', '.join(_COLS)} FROM bc_round_reschedule WHERE id=?", (int(rid),)).fetchone()
    return _row(r) if r else None


def edit(con, who: str, rid: int, new_date, reason_code: str, reason_note: str = None) -> dict:
    """Change the new date and / or the reason of an active reschedule (the reason is required again)."""
    r = get(con, rid)
    if not r:
        raise ValueError("ไม่พบรายการเลื่อนรอบส่งของ")
    if r["status"] != "active":
        raise ValueError("รายการนี้ยกเลิกแล้ว แก้ไขไม่ได้")
    try:
        new = _d(new_date)
    except (TypeError, ValueError):
        raise ValueError("วันที่ไม่ถูกต้อง")
    _check_dates(_d(r["orig_date"]), new)
    code, note = _clean_reason(reason_code, reason_note)
    if (new.isoformat(), code, note) == (r["new_date"], r["reason_code"], r["reason_note"]):
        raise ValueError("ไม่มีอะไรเปลี่ยน")
    name = _branch_names(con, [r["branch_id"]]).get(r["branch_id"], "")
    con.execute("UPDATE bc_round_reschedule SET new_date=?, reason_code=?, reason_note=?, updated_by=?, updated_at=? WHERE id=?",
                (new.isoformat(), code, note, who, now(), int(rid)))
    out = get(con, rid)
    audit(con, who, "round_reschedule_edit", "bc_round_reschedule", f"{r['branch_id']}|{r['round']}|{r['orig_date']}",
          _audit_text(r, name), _audit_text(out, name))
    return out


def cancel(con, who: str, rid: int, reason: str) -> dict:
    """Cancel an active reschedule: the delivery comes on its regular day again. The reason is required."""
    r = get(con, rid)
    if not r:
        raise ValueError("ไม่พบรายการเลื่อนรอบส่งของ")
    if r["status"] != "active":
        raise ValueError("รายการนี้ยกเลิกแล้ว")
    reason = " ".join(str(reason or "").split())[:NOTE_MAX]
    if not reason:
        raise ValueError("ระบุเหตุผลที่ยกเลิกการเลื่อน")
    name = _branch_names(con, [r["branch_id"]]).get(r["branch_id"], "")
    con.execute("UPDATE bc_round_reschedule SET status='cancelled', cancel_reason=?, cancelled_by=?, cancelled_at=? WHERE id=?",
                (reason, who, now(), int(rid)))
    audit(con, who, "round_reschedule_cancel", "bc_round_reschedule", f"{r['branch_id']}|{r['round']}|{r['orig_date']}",
          _audit_text(r, name), f"ยกเลิก · {reason}")
    return get(con, rid)


# ── reads ────────────────────────────────────────────────────────────────────
def _order_of(con, branch_id: int, rnd: str, orig: str) -> dict:
    """The branch's order of that delivery (not cancelled), {order_id, status, status_th} or None."""
    from .orders import STATUS_TH
    from .forecast import cutoff_for
    cyc = cutoff_for(_d(orig)).date().isoformat()
    r = con.execute("SELECT order_id, status FROM bc_order WHERE branch_id=? AND round=? AND status<>'cancelled'"
                    " AND (delivery_date=? OR (delivery_date IS NULL AND cycle_date=?)) ORDER BY order_id DESC LIMIT 1",
                    (branch_id, rnd, orig, cyc)).fetchone()
    return {"order_id": r[0], "status": r[1], "status_th": STATUS_TH.get(r[1], r[1])} if r else None


def _view(con, r: dict, names: dict) -> dict:
    from .fmt import at_th, day_short_th, who as _who
    from .forecast import ROUND_TH, cutoff_for, cutoff_th
    o = _order_of(con, r["branch_id"], r["round"], r["orig_date"])
    cut = cutoff_for(_d(r["orig_date"]))
    v = dict(r)
    v.update(branch=names.get(r["branch_id"], ""), round_th=ROUND_TH.get(r["round"], r["round"]),
             from_th=day_short_th(r["orig_date"]), to_th=day_short_th(r["new_date"]),
             reason_th=REASON_TH.get(r["reason_code"], r["reason_code"]),
             reason=_delay(con, r)["reason"], cutoff_at=cut.isoformat(), cutoff_th=cutoff_th(cut),
             created_by_th=_who(con, r["created_by"]) if r.get("created_by") else "-",
             announced_at=r.get("updated_at") or r.get("created_at"),
             announced_th=at_th(r.get("updated_at") or r.get("created_at")),
             cancelled_by_th=_who(con, r["cancelled_by"]) if r.get("cancelled_by") else None,
             cancelled_th=at_th(r["cancelled_at"]) if r.get("cancelled_at") else None,
             order=o, ordered=bool(o and o["status"] in ("submitted", "processed", "received")))
    return v


def listing(con, date_from=None, date_to=None, branch_id: int = None) -> list:
    """Every reschedule whose regular OR new date is in the range (Sales Support's list), active and cancelled; newest first."""
    q = f"SELECT {', '.join(_COLS)} FROM bc_round_reschedule WHERE 1=1"
    args = []
    if date_from:
        q += " AND (new_date>=? OR orig_date>=?)"
        args += [str(date_from), str(date_from)]
    if date_to:
        q += " AND (orig_date<=? OR new_date<=?)"
        args += [str(date_to), str(date_to)]
    if branch_id is not None:
        q += " AND branch_id=?"
        args.append(int(branch_id))
    rows = [_row(r) for r in con.execute(q + " ORDER BY orig_date DESC, id DESC LIMIT 500", args)]
    names = _branch_names(con, {r["branch_id"] for r in rows})
    return [_view(con, r, names) for r in rows]


def notices(con, branch_id: int, today: date, now_: datetime = None) -> list:
    """What the branch is told: its active reschedules whose new day is today or later, and a cancellation of the last 3 days
    whose regular day is still to come (the delivery is back on its usual day). Each with the plain words of the notice."""
    from .clock import now_bkk
    now_ = now_ or now_bkk()
    t = today.isoformat()
    try:
        rows = [_row(r) for r in con.execute(
            f"SELECT {', '.join(_COLS)} FROM bc_round_reschedule WHERE branch_id=? AND"
            " ((status='active' AND new_date>=?) OR (status='cancelled' AND orig_date>=? AND cancelled_at>=?))"
            " ORDER BY orig_date, round", (int(branch_id), t, t, (today - timedelta(days=3)).isoformat()))]
    except sqlite3.OperationalError:
        return []
    names = _branch_names(con, [branch_id])
    out = []
    for r in rows:
        v = _view(con, r, names)
        cut = datetime.fromisoformat(v["cutoff_at"])
        v["before_cutoff"] = now_ <= cut
        v.update(notice_lines(v))
        out.append(v)
    return out


def notice_lines(v: dict) -> dict:
    """{title, lines, hint} - the notice in the branch's everyday words (formal, short)."""
    if v["status"] == "cancelled":
        return {"title": f"{v['round_th']} · ยกเลิกการเลื่อนรอบส่งของ",
                "lines": [f"กลับมาส่งตามปกติ {v['from_th']}", f"เหตุผล: {v['cancel_reason'] or '-'}",
                          f"แจ้งเมื่อ {v['cancelled_th'] or '-'}"], "hint": None}
    lines = [f"ปกติส่ง {v['from_th']}", f"เลื่อนเป็น {v['to_th']}", f"เหตุผล: {v['reason']}", f"แจ้งเมื่อ {v['announced_th']}"]
    if v["ordered"]:
        hint = "สาขาส่งใบสั่งซื้อรอบนี้แล้ว · ถ้าของไม่พอใช้ถึงวันส่งใหม่ แจ้งประสานงานขาย"
    elif v.get("before_cutoff", True):
        hint = f"ยอดแนะนำคิดถึงวันส่งใหม่แล้ว · ส่งใบสั่งซื้อภายใน {v['cutoff_th']} ตามปกติ"
    else:
        hint = "ยอดแนะนำคิดถึงวันส่งใหม่แล้ว"
    return {"title": f"{v['round_th']} · เลื่อนรอบส่งของ", "lines": lines, "hint": hint}


# ── push ─────────────────────────────────────────────────────────────────────
def push_payload(con, r: dict, kind: str = "new") -> dict:
    """The notification of one branch's moved delivery: kind new / edit / cancel."""
    from .reminders import supply_url
    names = _branch_names(con, [r["branch_id"]])
    v = _view(con, r, names)
    if kind == "cancel":
        title = f"{v['branch']} · ยกเลิกการเลื่อนรอบส่งของ"
        body = f"{v['round_th']} กลับมาส่งตามปกติ {v['from_th']} · เหตุผล: {v['cancel_reason'] or '-'}"
    else:
        title = f"{v['branch']} · {'แก้ไขการ' if kind == 'edit' else ''}เลื่อนรอบส่งของ"
        body = f"{v['round_th']} ปกติส่ง {v['from_th']} เลื่อนเป็น {v['to_th']} · เหตุผล: {v['reason']}"
    return {"title": title, "body": body, "url": supply_url("branchstock", "order"), "tag": f"{SUPPLY_TAG}-{r['id']}"}


def push_users(con, branch_name: str) -> list:
    """The branch logins of `branch_name` that hold a device."""
    from .forecast import _loaded
    _P = _loaded("routers.push_api")                              # the copy the app loaded (backend.routers.push_api on Fly)
    return sorted(u for u, (role, b) in _P._devices_by_user(con).items() if role == "branch" and b == branch_name)


def send_push(get_db, items: list) -> None:
    """items = [(row, kind)] → one push per branch, to that branch's devices only (kind "supply"). In the background (a push
    service may be slow) unless PUSH_SEND is set; never fails the request."""
    import threading
    from .forecast import _loaded
    _P = _loaded("routers.push_api")                              # the copy the app loaded (backend.routers.push_api on Fly)
    if not items:
        return

    def run(send=None):
        try:
            with get_db() as con:
                _P.ensure(con)
                for r, kind in items:
                    name = _branch_names(con, [r["branch_id"]]).get(r["branch_id"])
                    users = push_users(con, name) if name else []
                    if users:
                        _P.send_to_all(con, push_payload(con, r, kind), send=send, only_users=users, kind="supply")
                        con.commit()
        except Exception as e:  # noqa: BLE001
            print(f"[reschedule] push: {e}")
    if PUSH_SEND is not None:
        run(PUSH_SEND)
        return
    if not _P.vapid_config()["configured"]:
        return
    threading.Thread(target=run, daemon=True, name="round-reschedule-push").start()
