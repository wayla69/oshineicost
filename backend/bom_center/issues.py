# -*- coding: utf-8 -*-
"""ใบตัดเบิก — the branch's daily issue slip (management change 2026-10-09).

Instead of counting the shelf before every order, a branch records each day what the kitchen
USED (เบิกใช้) and what was thrown away (ของเสีย / ทิ้ง, with a reason), per item, and confirms
the slip. The system stock after a day's issue is

    last month-end count + goods received − confirmed slips

and it is the basis of the order suggestion (orders.plan). The month-end count (ต้นงวด /
ปลายงวด) stays, to compare the slips with what really moved.

Rules (shared by every PR of the series):
  * one slip per branch per day (UNIQUE branch_id, slip_date); a draft is saved as often as
    the branch likes, then CONFIRMED once;
  * a slip for a day that is over is accepted but marked `late` — set on the FIRST confirm
    only (confirmed Bangkok date > slip date); a reopen / reconfirm never changes it;
  * no slip for a future day, none older than 60 days. A slip dated before the latest month-end count
    stays allowed (an error found after the count must be fixable, and the reconciliation of that
    earlier period needs it); it does not change the current system stock, the count being its basis.
    The only lock on a past period is Accounting's: once it has posted the ใบสรุปตัดเบิก of a period,
    a slip dated inside it can be neither confirmed nor reopened (variance.assert_not_posted);
  * a confirmed slip is locked. Sales Support reopens it with a reason; the branch edits and
    confirms again. Every create / confirm / reconfirm / reopen (and the first save after a
    reopen) is written to bc_issue_log and to the audit trail;
  * "issued" in any report means CONFIRMED slips only.
"""
from datetime import date, datetime, timedelta, timezone

from .clock import BKK, now_bkk
from .master import audit
from .units import Converter

WASTE_REASONS = (("waste", "ของเสีย / เน่าเสีย"), ("expired", "หมดอายุ"), ("spill", "หก / ตก / แตก"),
                 ("prep_loss", "ตัดแต่งเกินสูตร"), ("other", "อื่น ๆ (ระบุ)"))
WASTE_REASON_TH = dict(WASTE_REASONS)
STATUS_TH = {"confirmed": "ยืนยันแล้ว", "late": "ยืนยันล่าช้า", "draft": "ร่าง", "missing": "ยังไม่บันทึก"}
MAX_BACKDATE_DAYS = 60

DDL = (
    """CREATE TABLE IF NOT EXISTS bc_issue_slip(
         slip_id INTEGER PRIMARY KEY AUTOINCREMENT, branch_id INTEGER NOT NULL, slip_date TEXT NOT NULL,
         status TEXT NOT NULL DEFAULT 'draft' CHECK(status IN ('draft','confirmed')),
         created_by TEXT, created_at TEXT, updated_at TEXT,
         confirmed_by TEXT, confirmed_at TEXT, late INTEGER NOT NULL DEFAULT 0, confirm_count INTEGER NOT NULL DEFAULT 0,
         reopened_by TEXT, reopened_at TEXT, reopen_note TEXT, note TEXT, UNIQUE(branch_id, slip_date))""",
    "CREATE INDEX IF NOT EXISTS ix_bc_issue_slip_branch ON bc_issue_slip(branch_id, slip_date)",
    """CREATE TABLE IF NOT EXISTS bc_issue_line(
         slip_id INTEGER NOT NULL REFERENCES bc_issue_slip(slip_id), ingredient_code TEXT NOT NULL,
         qty_used REAL NOT NULL DEFAULT 0, qty_waste REAL NOT NULL DEFAULT 0, unit TEXT,
         qty_used_base REAL, qty_waste_base REAL, waste_reason TEXT, note TEXT,
         PRIMARY KEY (slip_id, ingredient_code))""",
    """CREATE TABLE IF NOT EXISTS bc_issue_log(
         log_id INTEGER PRIMARY KEY AUTOINCREMENT, slip_id INTEGER NOT NULL, branch_id INTEGER NOT NULL,
         slip_date TEXT NOT NULL, at TEXT NOT NULL, who TEXT,
         action TEXT NOT NULL CHECK(action IN ('create','save','confirm','reconfirm','reopen')),
         late INTEGER NOT NULL DEFAULT 0, lines INTEGER, note TEXT)""",
    "CREATE INDEX IF NOT EXISTS ix_bc_issue_log_branch ON bc_issue_log(branch_id, slip_date)",
)

_COLS = ("slip_id", "branch_id", "slip_date", "status", "created_by", "created_at", "updated_at", "confirmed_by",
         "confirmed_at", "late", "confirm_count", "reopened_by", "reopened_at", "reopen_note", "note")


# ── small helpers ────────────────────────────────────────────────────────────
def _day(now: datetime = None) -> date:
    """The branch's calendar day (Bangkok) at `now`."""
    return (now or now_bkk()).astimezone(BKK).date()


def _stamp(now: datetime = None) -> str:
    """UTC ISO timestamp, written the way orders.save writes submitted_at."""
    return (now.astimezone(timezone.utc) if now else datetime.now(timezone.utc)).isoformat(timespec="seconds")


def _iso(d) -> str:
    return d.isoformat() if isinstance(d, date) else str(d)


def _row(con, branch_id: int, slip_date) -> dict:
    r = con.execute(f"SELECT {', '.join(_COLS)} FROM bc_issue_slip WHERE branch_id=? AND slip_date=?",
                    (branch_id, _iso(slip_date))).fetchone()
    return dict(zip(_COLS, r)) if r else None


def _row_by_id(con, slip_id: int) -> dict:
    r = con.execute(f"SELECT {', '.join(_COLS)} FROM bc_issue_slip WHERE slip_id=?", (slip_id,)).fetchone()
    return dict(zip(_COLS, r)) if r else None


def _log(con, row: dict, who: str, action: str, late: int = 0, lines: int = None, note: str = None, now: datetime = None):
    con.execute("INSERT INTO bc_issue_log(slip_id, branch_id, slip_date, at, who, action, late, lines, note)"
                " VALUES(?,?,?,?,?,?,?,?,?)",
                (row["slip_id"], row["branch_id"], row["slip_date"], _stamp(now), who, action, int(late or 0), lines, note))


def _status_dict(day: str, row: dict, lines: int = 0) -> dict:
    if not row:
        return {"date": day, "slip_id": None, "status": "missing", "confirmed_at": None, "confirmed_by": None,
                "late": 0, "lines": 0, "confirm_count": 0}
    if row["status"] == "confirmed":
        st = "late" if row["late"] else "confirmed"
    else:
        st = "draft"
    return {"date": day, "slip_id": row["slip_id"], "status": st, "confirmed_at": row["confirmed_at"],
            "confirmed_by": row["confirmed_by"], "late": int(row["late"] or 0), "lines": lines,
            "confirm_count": int(row["confirm_count"] or 0)}


def is_confirmed(st: dict) -> bool:
    """The slip of that day is confirmed (on time or late)."""
    return bool(st) and st.get("status") in ("confirmed", "late")


def status_of(con, branch_id: int, day) -> dict:
    """{date, slip_id, status 'confirmed'|'late'|'draft'|'missing', confirmed_at, confirmed_by, late, lines, confirm_count}"""
    d = _iso(day)
    row = _row(con, branch_id, d)
    n = con.execute("SELECT COUNT(*) FROM bc_issue_line WHERE slip_id=?", (row["slip_id"],)).fetchone()[0] if row else 0
    return _status_dict(d, row, n)


def status_days(con, branch_id: int, d0, d1) -> list:
    """One status dict per calendar day from d0 to d1 inclusive (a day without a slip is 'missing')."""
    a, b = date.fromisoformat(_iso(d0)), date.fromisoformat(_iso(d1))
    rows = {r[2]: dict(zip(_COLS, r)) for r in con.execute(
        f"SELECT {', '.join(_COLS)} FROM bc_issue_slip WHERE branch_id=? AND slip_date BETWEEN ? AND ?",
        (branch_id, a.isoformat(), b.isoformat()))}
    lines = {sid: n for sid, n in con.execute(
        "SELECT l.slip_id, COUNT(*) FROM bc_issue_line l JOIN bc_issue_slip s USING(slip_id)"
        " WHERE s.branch_id=? AND s.slip_date BETWEEN ? AND ? GROUP BY l.slip_id", (branch_id, a.isoformat(), b.isoformat()))}
    out, d = [], a
    while d <= b:
        iso = d.isoformat()
        row = rows.get(iso)
        out.append(_status_dict(iso, row, lines.get(row["slip_id"], 0) if row else 0))
        d += timedelta(days=1)
    return out


def missing_days(con, branch_id: int, since, through) -> list:
    """ISO dates from `since` to `through` (inclusive) that have no confirmed slip."""
    a, b = date.fromisoformat(_iso(since)), date.fromisoformat(_iso(through))
    if b < a:
        return []
    return [s["date"] for s in status_days(con, branch_id, a, b) if not is_confirmed(s)]


# ── the sheet a branch fills ─────────────────────────────────────────────────
def _date_or_error(slip_date) -> date:
    try:
        return date.fromisoformat(_iso(slip_date))
    except (TypeError, ValueError):
        raise ValueError("วันที่ใบตัดเบิกต้องอยู่ในรูปแบบ YYYY-MM-DD")


def sheet(con, branch_id: int, slip_date, now: datetime = None) -> dict:
    """The list a branch issues from on one day: every item it keeps (the month-end count list), the system
    stock before that day's issue, the 7-day average, and the slip's own lines."""
    from .stock import sheet as stock_sheet, system_position
    d = _date_or_error(slip_date)
    today = _day(now)
    base = stock_sheet(con, branch_id, d)
    pos = system_position(con, branch_id, d, issues_through=d - timedelta(days=1))
    row = _row(con, branch_id, d)
    lines = {}
    if row:
        for c, qu, qw, unit, qub, qwb, rs, nt in con.execute(
                "SELECT ingredient_code, qty_used, qty_waste, unit, qty_used_base, qty_waste_base, waste_reason, note"
                " FROM bc_issue_line WHERE slip_id=?", (row["slip_id"],)):
            lines[c] = {"code": c, "qty_used": qu, "qty_waste": qw, "unit": unit, "qty_used_base": qub,
                        "qty_waste_base": qwb, "waste_reason": rs, "note": nt}
    # average confirmed use over the 7 days before, in base units
    w0 = (d - timedelta(days=7)).isoformat()
    w1 = (d - timedelta(days=1)).isoformat()
    sums, days_n = {}, 0
    for c, q in con.execute(
            "SELECT l.ingredient_code, SUM(COALESCE(l.qty_used_base, 0)) FROM bc_issue_line l JOIN bc_issue_slip s USING(slip_id)"
            " WHERE s.branch_id=? AND s.status='confirmed' AND s.slip_date BETWEEN ? AND ? GROUP BY l.ingredient_code",
            (branch_id, w0, w1)):
        sums[c] = q or 0.0
    days_n = con.execute("SELECT COUNT(*) FROM bc_issue_slip WHERE branch_id=? AND status='confirmed' AND slip_date"
                         " BETWEEN ? AND ?", (branch_id, w0, w1)).fetchone()[0]

    def in_count(code, qb, factor):
        return None if qb is None else round(qb / (factor or 1.0), 6)
    for z in base["zones"]:
        for it in z["items"]:
            f = it["per_count_unit"] or 1.0
            sq = pos["qty"].get(it["code"])
            it["system_qty"] = in_count(it["code"], sq, f)
            it["avg_used_7d"] = round(sums[it["code"]] / days_n / f, 6) if days_n and it["code"] in sums else None
            ln = lines.get(it["code"])
            if ln:
                same = (ln["unit"] or "") == (it["count_unit"] or "")
                it["used"] = ln["qty_used"] if same else in_count(it["code"], ln["qty_used_base"], f)
                it["waste"] = ln["qty_waste"] if same else in_count(it["code"], ln["qty_waste_base"], f)
                it["waste_reason"], it["line_note"] = ln["waste_reason"], ln["note"]
            else:
                it["used"] = it["waste"] = it["waste_reason"] = it["line_note"] = None
    st = status_of(con, branch_id, d)
    out = {k: base[k] for k in ("branch_id", "branch", "zones", "items", "required")}
    out.update({"date": d.isoformat(), "today": today.isoformat(), "status": st, "status_th": STATUS_TH[st["status"]],
                "slip": row and {k: row[k] for k in ("slip_id", "note", "reopened_by", "reopened_at", "reopen_note")},
                "editable": st["status"] not in ("confirmed", "late"),
                "backdated": d < today, "basis": pos["basis"], "missing_days": pos["missing_days"],
                "earliest": (today - timedelta(days=MAX_BACKDATE_DAYS)).isoformat(),
                "lines": list(lines.values()),
                "reasons": [{"code": c, "label": t} for c, t in WASTE_REASONS]})
    return out


# ── writing ──────────────────────────────────────────────────────────────────
def save(con, branch_id: int, slip_date, lines: list, who: str, submit: bool = False, note: str = None,
         now: datetime = None) -> dict:
    """Write the day's draft (creating it) and optionally confirm it. Lines sent are upserted (both quantities
    zero removes the line); lines not sent stay."""
    from .stock import _branch_name, count_units
    _branch_name(con, branch_id)
    d = _date_or_error(slip_date)
    today = _day(now)
    if d > today:
        raise ValueError("ใบตัดเบิกต้องไม่เป็นวันล่วงหน้า")
    if (today - d).days > MAX_BACKDATE_DAYS:
        raise ValueError(f"บันทึกใบตัดเบิกย้อนหลังได้ไม่เกิน {MAX_BACKDATE_DAYS} วัน")
    if submit:                                               # confirming changes a posted period's numbers
        from .variance import assert_not_posted
        assert_not_posted(con, branch_id, d.isoformat(), "ใบตัดเบิก")
    iso = d.isoformat()
    row = _row(con, branch_id, iso)
    if row and row["status"] == "confirmed":
        raise ValueError(f"ใบตัดเบิกวันที่ {iso} ยืนยันแล้ว หากต้องแก้ไข กรุณาแจ้งประสานงานขายให้เปิดแก้ไข")
    conv = Converter(con)
    cu = count_units(con, {(ln.get("code") or ln.get("ingredient_code") or "").strip() for ln in lines or []})
    clean, bad = [], []
    for ln in lines or []:
        code = (ln.get("code") or ln.get("ingredient_code") or "").strip()
        if not code:
            continue
        vals = []
        for key in ("qty_used", "qty_waste"):
            v = ln.get(key)
            if v is None or v == "":
                vals.append(0.0)
                continue
            try:
                q = float(v)
            except (TypeError, ValueError):
                raise ValueError(f"{code}: จำนวนต้องเป็นตัวเลข")
            if q < 0:
                raise ValueError(f"{code}: จำนวนต้องไม่ติดลบ")
            vals.append(q)
        used, waste = vals
        reason = (ln.get("waste_reason") or "").strip() or None
        lnote = (ln.get("note") or "").strip() or None
        if used == 0 and waste == 0:
            clean.append((code, None))
            continue
        if waste > 0:
            if reason not in WASTE_REASON_TH:
                raise ValueError(f"{code}: กรุณาเลือกสาเหตุของของเสีย")
            if reason == "other" and not lnote:
                raise ValueError(f"{code}: สาเหตุ “อื่น ๆ” ต้องระบุคำอธิบาย")
        else:
            reason = None
        unit = (ln.get("unit") or "").strip() or (cu.get(code) or ("",))[0] or conv.base_unit(code)
        ub, how = conv.to_base(code, used, unit)
        wb, how2 = conv.to_base(code, waste, unit)
        if ub is None or wb is None:
            bad.append(f"{code}: {how if ub is None else how2}")
        clean.append((code, (used, waste, unit, ub, wb, reason, lnote)))
    created = False
    if not row:
        cur = con.execute("INSERT INTO bc_issue_slip(branch_id, slip_date, status, created_by, created_at, updated_at)"
                          " VALUES(?,?, 'draft', ?,?,?)", (branch_id, iso, who, _stamp(now), _stamp(now)))
        row = _row_by_id(con, cur.lastrowid)
        created = True
    sid = row["slip_id"]
    for code, v in clean:
        if v is None:
            con.execute("DELETE FROM bc_issue_line WHERE slip_id=? AND ingredient_code=?", (sid, code))
            continue
        con.execute("INSERT INTO bc_issue_line(slip_id, ingredient_code, qty_used, qty_waste, unit, qty_used_base,"
                    " qty_waste_base, waste_reason, note) VALUES(?,?,?,?,?,?,?,?,?)"
                    " ON CONFLICT(slip_id, ingredient_code) DO UPDATE SET qty_used=excluded.qty_used,"
                    " qty_waste=excluded.qty_waste, unit=excluded.unit, qty_used_base=excluded.qty_used_base,"
                    " qty_waste_base=excluded.qty_waste_base, waste_reason=excluded.waste_reason, note=excluded.note",
                    (sid, code) + v)
    con.execute("UPDATE bc_issue_slip SET updated_at=?, note=COALESCE(?, note) WHERE slip_id=?", (_stamp(now), note, sid))
    n = con.execute("SELECT COUNT(*) FROM bc_issue_line WHERE slip_id=?", (sid,)).fetchone()[0]
    if created:
        _log(con, row, who, "create", lines=n, now=now)
    else:
        last = con.execute("SELECT action FROM bc_issue_log WHERE slip_id=? ORDER BY log_id DESC LIMIT 1", (sid,)).fetchone()
        if last and last[0] == "reopen":                     # the first save after a reopen is on the record
            _log(con, row, who, "save", late=row["late"], lines=n, now=now)
    out = {"slip_id": sid, "date": iso, "status": "draft", "lines": n, "late": int(row["late"] or 0),
           "confirm_count": int(row["confirm_count"] or 0), "unconverted": bad}
    if submit:
        if not n:
            raise ValueError("ยังไม่มีรายการในใบตัดเบิก")
        unconv = [f"{c}: แปลง {u or '-'} ไม่ได้" for c, u in con.execute(
            "SELECT ingredient_code, unit FROM bc_issue_line WHERE slip_id=? AND (qty_used_base IS NULL"
            " OR qty_waste_base IS NULL)", (sid,))]
        if unconv:
            raise ValueError("แปลงหน่วยไม่ได้: " + "; ".join(unconv[:3]) + " กรุณาแจ้งส่วนกลางให้เพิ่มกฎหน่วย")
        first = int(row["confirm_count"] or 0) == 0
        late = 1 if (first and today > d) else int(row["late"] or 0)
        con.execute("UPDATE bc_issue_slip SET status='confirmed', confirmed_by=?, confirmed_at=?, late=?,"
                    " confirm_count=confirm_count+1, updated_at=? WHERE slip_id=?",
                    (who, _stamp(now), late, _stamp(now), sid))
        action = "confirm" if first else "reconfirm"
        row = _row_by_id(con, sid)
        _log(con, row, who, action, late=late, lines=n, now=now)
        audit(con, who, f"issue_{action}", "bc_issue_slip", str(sid), None,
              {"branch_id": branch_id, "date": iso, "lines": n, "late": late})
        out.update(status="confirmed", late=late, confirm_count=row["confirm_count"])
    out["slip"] = status_of(con, branch_id, iso)
    return out


def reopen(con, slip_id: int, who: str, note: str, now: datetime = None) -> dict:
    """Sales Support opens a confirmed slip for the branch to edit; `late` and the confirm count stay."""
    row = _row_by_id(con, slip_id)
    if not row:
        raise ValueError("ไม่พบใบตัดเบิก")
    if row["status"] != "confirmed":
        raise ValueError("เปิดแก้ไขได้เฉพาะใบตัดเบิกที่ยืนยันแล้ว")
    from .variance import assert_not_posted
    assert_not_posted(con, row["branch_id"], row["slip_date"], "ใบตัดเบิก")     # Accounting has keyed this period
    note = (note or "").strip()
    if not note:
        raise ValueError("กรุณาระบุเหตุผลในการเปิดแก้ไขใบตัดเบิก")
    con.execute("UPDATE bc_issue_slip SET status='draft', reopened_by=?, reopened_at=?, reopen_note=?, updated_at=?"
                " WHERE slip_id=?", (who, _stamp(now), note, _stamp(now), slip_id))
    n = con.execute("SELECT COUNT(*) FROM bc_issue_line WHERE slip_id=?", (slip_id,)).fetchone()[0]
    _log(con, row, who, "reopen", late=row["late"], lines=n, note=note, now=now)
    audit(con, who, "issue_reopen", "bc_issue_slip", str(slip_id), None,
          {"branch_id": row["branch_id"], "date": row["slip_date"], "note": note[:200]})
    return {"slip_id": slip_id, "branch_id": row["branch_id"], "date": row["slip_date"], "status": "draft"}


# ── reading back ─────────────────────────────────────────────────────────────
def _display(row: dict) -> str:
    if row["status"] == "confirmed":
        return "late" if row["late"] else "confirmed"
    return "draft"


def listing(con, branch_id: int = None, date_from=None, date_to=None, status: str = None, limit: int = 500) -> list:
    """Slips of one branch (or all), newest first, each with its line counts. `status` filters by what the
    slip shows: confirmed / late / draft (None or 'all' = every slip)."""
    if status and status != "all" and status not in ("confirmed", "late", "draft"):
        raise ValueError("status ต้องเป็น confirmed / late / draft / all")
    d1 = date.fromisoformat(_iso(date_to)) if date_to else now_bkk().date()
    d0 = date.fromisoformat(_iso(date_from)) if date_from else d1 - timedelta(days=30)
    sql = ("SELECT " + ", ".join("s." + c for c in _COLS) + ", b.name,"
           " (SELECT COUNT(*) FROM bc_issue_line l WHERE l.slip_id=s.slip_id),"
           " (SELECT COUNT(*) FROM bc_issue_line l WHERE l.slip_id=s.slip_id AND l.qty_waste>0)"
           " FROM bc_issue_slip s LEFT JOIN branches b ON b.branch_id=s.branch_id WHERE s.slip_date BETWEEN ? AND ?")
    args = [d0.isoformat(), d1.isoformat()]
    if branch_id is not None:
        sql += " AND s.branch_id=?"
        args.append(int(branch_id))
    out = []
    for r in con.execute(sql + " ORDER BY s.slip_date DESC, s.slip_id DESC", args):
        row = dict(zip(_COLS, r[:len(_COLS)]))
        st = _display(row)
        if status and status != "all" and st != status:
            continue
        out.append({"slip_id": row["slip_id"], "branch_id": row["branch_id"], "branch": r[len(_COLS)],
                    "date": row["slip_date"], "status": st, "status_th": STATUS_TH[st], "late": int(row["late"] or 0),
                    "lines": r[len(_COLS) + 1], "waste_lines": r[len(_COLS) + 2], "confirmed_by": row["confirmed_by"],
                    "confirmed_at": row["confirmed_at"], "confirm_count": int(row["confirm_count"] or 0),
                    "reopened_at": row["reopened_at"]})
        if len(out) >= limit:
            break
    return out


def detail(con, slip_id: int) -> dict:
    row = _row_by_id(con, slip_id)
    if not row:
        raise ValueError("ไม่พบใบตัดเบิก")
    st = _display(row)
    out = dict(row)
    out.update(date=row["slip_date"], status_key=st, status_th=STATUS_TH[st],
               branch=(con.execute("SELECT name FROM branches WHERE branch_id=?", (row["branch_id"],)).fetchone() or [None])[0])
    names = dict(con.execute("SELECT code, name_th FROM bc_ingredient"))
    out["lines"] = [{"code": c, "name": names.get(c), "qty_used": qu, "qty_waste": qw, "unit": u, "qty_used_base": qub,
                     "qty_waste_base": qwb, "waste_reason": rs, "waste_reason_th": WASTE_REASON_TH.get(rs) if rs else None,
                     "note": nt}
                    for c, qu, qw, u, qub, qwb, rs, nt in con.execute(
                        "SELECT ingredient_code, qty_used, qty_waste, unit, qty_used_base, qty_waste_base, waste_reason, note"
                        " FROM bc_issue_line WHERE slip_id=? ORDER BY ingredient_code", (slip_id,))]
    out["log"] = [dict(zip(("at", "who", "action", "late", "lines", "note"), r)) for r in con.execute(
        "SELECT at, who, action, late, lines, note FROM bc_issue_log WHERE slip_id=? ORDER BY log_id", (slip_id,))]
    return out
