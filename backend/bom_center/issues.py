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
  * PR-M7: a slip CONFIRMED TODAY takes more lines without a reopen (append): the quantities are added to the lines
    it has, the slip stays confirmed and `late` stays as it was, and each append is one row of bc_issue_log;
  * "issued" in any report means CONFIRMED slips only.
"""
import math
from datetime import date, datetime, timedelta, timezone

from .clock import BKK, now_bkk
from .fmt import day_th as _fmt_day
from .fmt import item as _item
from .fmt import no_unit as _no_unit
from .fmt import reasons as _reasons
from .master import audit
from .units import Converter

WASTE_REASONS = _reasons("waste", "expired", "spill", "prep_loss", "other")      # fmt.REASON_LABEL: the words variance uses too
WASTE_REASON_TH = dict(WASTE_REASONS)
STATUS_TH = {"confirmed": "ยืนยันแล้ว", "late": "ยืนยันล่าช้า", "draft": "ร่าง", "missing": "ยังไม่บันทึก"}
MAX_BACKDATE_DAYS = 60
RECENT_DAYS = 14               # an item issued on a CONFIRMED slip in this many days before the day is "recent" (listed first)

# PR-M7: the CHECK list of `action` gained 'append'. A table created before it keeps the old list (CREATE … IF NOT EXISTS leaves it
# alone), so schema.ensure() calls widen_log() which rebuilds it once, rows kept.
LOG_DDL = """CREATE TABLE IF NOT EXISTS bc_issue_log(
         log_id INTEGER PRIMARY KEY AUTOINCREMENT, slip_id INTEGER NOT NULL, branch_id INTEGER NOT NULL,
         slip_date TEXT NOT NULL, at TEXT NOT NULL, who TEXT,
         action TEXT NOT NULL CHECK(action IN ('create','save','confirm','reconfirm','reopen','append')),
         late INTEGER NOT NULL DEFAULT 0, lines INTEGER, note TEXT)"""
LOG_INDEX_DDL = "CREATE INDEX IF NOT EXISTS ix_bc_issue_log_branch ON bc_issue_log(branch_id, slip_date)"
_LOG_COLS = "log_id, slip_id, branch_id, slip_date, at, who, action, late, lines, note"


def widen_log(con) -> bool:
    """Rebuild bc_issue_log with the CHECK list that includes 'append' when the stored DDL lacks it. Idempotent; every row
    is copied (log_id kept, so the AUTOINCREMENT sequence carries on). Returns True when it rebuilt the table."""
    r = con.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='bc_issue_log'").fetchone()
    if not r or "'append'" in (r[0] or ""):
        return False
    con.execute("DROP TABLE IF EXISTS bc_issue_log_new")
    con.execute(LOG_DDL.replace("IF NOT EXISTS bc_issue_log(", "bc_issue_log_new(", 1))
    con.execute(f"INSERT INTO bc_issue_log_new({_LOG_COLS}) SELECT {_LOG_COLS} FROM bc_issue_log")
    con.execute("DROP TABLE bc_issue_log")
    con.execute("ALTER TABLE bc_issue_log_new RENAME TO bc_issue_log")
    con.execute(LOG_INDEX_DDL)
    return True


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
    LOG_DDL,
    LOG_INDEX_DDL,
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


def _status_dict(day: str, row: dict, lines: int = 0, appends: int = 0) -> dict:
    if not row:
        return {"date": day, "slip_id": None, "status": "missing", "confirmed_at": None, "confirmed_by": None,
                "late": 0, "lines": 0, "confirm_count": 0, "appends": 0}
    if row["status"] == "confirmed":
        st = "late" if row["late"] else "confirmed"
    else:
        st = "draft"
    return {"date": day, "slip_id": row["slip_id"], "status": st, "confirmed_at": row["confirmed_at"],
            "confirmed_by": row["confirmed_by"], "late": int(row["late"] or 0), "lines": lines,
            "confirm_count": int(row["confirm_count"] or 0), "appends": int(appends or 0)}


def is_confirmed(st: dict) -> bool:
    """The slip of that day is confirmed (on time or late)."""
    return bool(st) and st.get("status") in ("confirmed", "late")


def _appends_of(con, slip_id: int) -> int:
    """How many times lines were added to the slip after it was confirmed (PR-M7)."""
    return con.execute("SELECT COUNT(*) FROM bc_issue_log WHERE slip_id=? AND action='append'", (slip_id,)).fetchone()[0]


def status_of(con, branch_id: int, day) -> dict:
    """{date, slip_id, status 'confirmed'|'late'|'draft'|'missing', confirmed_at, confirmed_by, late, lines, confirm_count, appends}"""
    d = _iso(day)
    row = _row(con, branch_id, d)
    n = con.execute("SELECT COUNT(*) FROM bc_issue_line WHERE slip_id=?", (row["slip_id"],)).fetchone()[0] if row else 0
    return _status_dict(d, row, n, _appends_of(con, row["slip_id"]) if row else 0)


def status_days(con, branch_id: int, d0, d1) -> list:
    """One status dict per calendar day from d0 to d1 inclusive (a day without a slip is 'missing')."""
    a, b = date.fromisoformat(_iso(d0)), date.fromisoformat(_iso(d1))
    rows = {r[2]: dict(zip(_COLS, r)) for r in con.execute(
        f"SELECT {', '.join(_COLS)} FROM bc_issue_slip WHERE branch_id=? AND slip_date BETWEEN ? AND ?",
        (branch_id, a.isoformat(), b.isoformat()))}
    lines = {sid: n for sid, n in con.execute(
        "SELECT l.slip_id, COUNT(*) FROM bc_issue_line l JOIN bc_issue_slip s USING(slip_id)"
        " WHERE s.branch_id=? AND s.slip_date BETWEEN ? AND ? GROUP BY l.slip_id", (branch_id, a.isoformat(), b.isoformat()))}
    apps = {sid: n for sid, n in con.execute(
        "SELECT g.slip_id, COUNT(*) FROM bc_issue_log g JOIN bc_issue_slip s USING(slip_id)"
        " WHERE s.branch_id=? AND s.slip_date BETWEEN ? AND ? AND g.action='append' GROUP BY g.slip_id",
        (branch_id, a.isoformat(), b.isoformat()))}
    out, d = [], a
    while d <= b:
        iso = d.isoformat()
        row = rows.get(iso)
        out.append(_status_dict(iso, row, lines.get(row["slip_id"], 0) if row else 0, apps.get(row["slip_id"], 0) if row else 0))
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


def default_list(it: dict) -> bool:
    """3.2 (owner decision 2026-10-12): the slip a branch opens with lists only the items it is still working with — one issued on a
    confirmed slip or used by the recipes within the last RECENT_DAYS days (`recent`, `used_recent`), one with system stock above 0,
    one that must be counted (`required`, ต้องนับ), and any item this slip already has a quantity for (a saved draft is never hidden).
    The rest is behind "แสดงทั้งหมด"; a search always looks at every item."""
    return bool(it.get("recent") or it.get("used_recent") or it.get("required") or (it.get("system_qty") or 0) > 0
                or (it.get("used") or 0) > 0 or (it.get("waste") or 0) > 0)


def sheet(con, branch_id: int, slip_date, now: datetime = None) -> dict:
    """The list a branch issues from on one day: every item it keeps (the month-end count list), the system
    stock before that day's issue, the 7-day average, and the slip's own lines.

    PR-M3: each item also carries `recent` (a confirmed slip of the last RECENT_DAYS days before this day has a line
    for it) and `prev_used` / `prev_waste` (the quantity on the latest such confirmed slip, in the count unit, None
    when 0); inside a zone the recent items come first. `prev_date` names that slip. PR-M7: `prev_waste_reason` and
    `prev_note` are what that slip said about its waste (they let "เบิกเหมือนเมื่อวาน" copy the waste with its reason)."""
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
    # PR-M3: what the branch really issues comes first inside a zone (it fills the same few lines every day), and
    # the previous confirmed slip's quantity is the hint inside an empty box ("เมื่อวาน 3")
    r0 = (d - timedelta(days=RECENT_DAYS)).isoformat()
    recent = {c for c, in con.execute(
        "SELECT DISTINCT l.ingredient_code FROM bc_issue_line l JOIN bc_issue_slip s USING(slip_id)"
        " WHERE s.branch_id=? AND s.status='confirmed' AND s.slip_date BETWEEN ? AND ?"
        " AND (l.qty_used>0 OR l.qty_waste>0)", (branch_id, r0, w1))}
    pv = con.execute("SELECT slip_id, slip_date FROM bc_issue_slip WHERE branch_id=? AND status='confirmed'"
                     " AND slip_date BETWEEN ? AND ? ORDER BY slip_date DESC LIMIT 1", (branch_id, r0, w1)).fetchone()
    prev_lines = {}
    if pv:
        for c, qu, qw, unit, qub, qwb, rs, nt in con.execute(
                "SELECT ingredient_code, qty_used, qty_waste, unit, qty_used_base, qty_waste_base, waste_reason, note"
                " FROM bc_issue_line WHERE slip_id=?", (pv[0],)):
            prev_lines[c] = (qu, qw, unit, qub, qwb, rs, nt)
    klass_order = {"A": 0, "B": 1, "C": 2}
    for z in base["zones"]:
        for it in z["items"]:
            f = it["per_count_unit"] or 1.0
            sq = pos["qty"].get(it["code"])
            it["system_qty"] = in_count(it["code"], sq, f)
            eq = pos["estimated_qty"].get(it["code"])               # PR-B4a (1.3): the part of the figure that is the BoM of sales, not a slip
            it["estimated_qty"] = in_count(it["code"], eq, f) if eq else None
            it["avg_used_7d"] = round(sums[it["code"]] / days_n / f, 6) if days_n and it["code"] in sums else None
            ln = lines.get(it["code"])
            if ln:
                same = (ln["unit"] or "") == (it["count_unit"] or "")
                it["used"] = ln["qty_used"] if same else in_count(it["code"], ln["qty_used_base"], f)
                it["waste"] = ln["qty_waste"] if same else in_count(it["code"], ln["qty_waste_base"], f)
                it["waste_reason"], it["line_note"] = ln["waste_reason"], ln["note"]
            else:
                it["used"] = it["waste"] = it["waste_reason"] = it["line_note"] = None
            it["recent"] = it["code"] in recent
            it["in_default"] = default_list(it)                  # 3.2: on the short slip a branch opens with
            pl = prev_lines.get(it["code"])
            it["prev_used"] = it["prev_waste"] = it["prev_waste_reason"] = it["prev_note"] = None
            if pl:
                same = (pl[2] or "") == (it["count_unit"] or "")
                pu = pl[0] if same else in_count(it["code"], pl[3], f)
                pw = pl[1] if same else in_count(it["code"], pl[4], f)
                it["prev_used"] = pu if pu and pu > 0 else None
                it["prev_waste"] = pw if pw and pw > 0 else None
                if it["prev_waste"]:
                    it["prev_waste_reason"], it["prev_note"] = pl[5], pl[6]
        # usage first, then the order the count list already uses (required, then class A/B/C, then the name)
        z["items"].sort(key=lambda r: (not r["recent"], not r["required"], klass_order.get(r["class"], 3), r["name"]))
    st = status_of(con, branch_id, d)
    out = {k: base[k] for k in ("branch_id", "branch", "zones", "items", "required")}
    out["items_default"] = sum(1 for z in base["zones"] for it in z["items"] if it["in_default"])
    out.update({"date": d.isoformat(), "today": today.isoformat(), "status": st, "status_th": STATUS_TH[st["status"]],
                "slip": row and {k: row[k] for k in ("slip_id", "note", "reopened_by", "reopened_at", "reopen_note")},
                "editable": st["status"] not in ("confirmed", "late"),
                "can_append": st["status"] in ("confirmed", "late") and d == today,       # PR-M7: เบิกเพิ่ม is open on today's confirmed slip
                "backdated": d < today, "prev_date": pv[1] if pv else None, "basis": pos["basis"], "missing_days": pos["missing_days"],
                "estimated_days": pos["estimated_days"],
                "earliest": (today - timedelta(days=MAX_BACKDATE_DAYS)).isoformat(),
                "lines": list(lines.values()),
                "reasons": [{"code": c, "label": t} for c, t in WASTE_REASONS]})
    return out


# ── writing ──────────────────────────────────────────────────────────────────
def _clean_lines(con, lines: list, whole_up: bool = False):
    """The lines a branch sent, checked and converted to base units: ([(code, None | (used, waste, unit, used_base,
    waste_base, reason, note))], [unconvertible messages]). A line with both quantities 0 is (code, None).
    `whole_up` (เบิกเพิ่ม): every quantity is rounded UP to a whole number in the unit it was entered in."""
    from .stock import count_units
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
                raise ValueError(f"{_item(con, code)}: จำนวนต้องเป็นตัวเลข")
            if q < 0:
                raise ValueError(f"{_item(con, code)}: จำนวนต้องไม่ติดลบ")
            if whole_up:
                q = float(math.ceil(round(q, 6)))              # 3.47 → 4 · 3.0000001 → 3 (float dust is not a fraction)
            vals.append(q)
        used, waste = vals
        reason = (ln.get("waste_reason") or "").strip() or None
        lnote = (ln.get("note") or "").strip() or None
        if used == 0 and waste == 0:
            clean.append((code, None))
            continue
        if waste > 0:
            if reason not in WASTE_REASON_TH:
                raise ValueError(f"{_item(con, code)}: เลือกสาเหตุของของเสีย")
            if reason == "other" and not lnote:
                raise ValueError(f"{_item(con, code)}: สาเหตุ “อื่น ๆ” ต้องระบุคำอธิบาย")
        else:
            reason = None
        unit = (ln.get("unit") or "").strip() or (cu.get(code) or ("",))[0] or conv.base_unit(code)
        ub, how = conv.to_base(code, used, unit)
        wb, how2 = conv.to_base(code, waste, unit)
        if ub is None or wb is None:
            bad.append(_no_unit(con, code, unit))
        clean.append((code, (used, waste, unit, ub, wb, reason, lnote)))
    return clean, bad


def save(con, branch_id: int, slip_date, lines: list, who: str, submit: bool = False, note: str = None,
         now: datetime = None) -> dict:
    """Write the day's draft (creating it) and optionally confirm it. Lines sent are upserted (both quantities
    zero removes the line); lines not sent stay."""
    from .stock import _branch_name
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
        raise ValueError(f"ใบตัดเบิกวันที่ {_fmt_day(iso)} ยืนยันแล้ว · ต้องการแก้ แจ้งประสานงานขายให้เปิดแก้ไข")
    clean, bad = _clean_lines(con, lines)
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
        unconv = [_no_unit(con, c, u) for c, u in con.execute(
            "SELECT ingredient_code, unit FROM bc_issue_line WHERE slip_id=? AND (qty_used_base IS NULL"
            " OR qty_waste_base IS NULL)", (sid,))]
        if unconv:
            raise ValueError("บันทึกไม่ได้: " + "; ".join(unconv[:3]) + " · แจ้งฝ่ายวิเคราะห์")
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


def append(con, branch_id: int, slip_date, lines: list, who: str, now: datetime = None) -> dict:
    """PR-M7 · เบิกเพิ่ม: more lines on the slip of TODAY that is already confirmed, without a reopen. Each quantity is ADDED
    to the line the slip has for that item (or the line is inserted); waste still needs its reason. The slip stays
    confirmed and `late` is not touched. One bc_issue_log row ('append', lines = lines added) and one audit row."""
    from .stock import _branch_name
    from .variance import assert_not_posted
    _branch_name(con, branch_id)
    d = _date_or_error(slip_date)
    iso = d.isoformat()
    row = _row(con, branch_id, iso)
    if not row or row["status"] != "confirmed" or d != _day(now):
        raise ValueError("เบิกเพิ่มได้เฉพาะใบตัดเบิกของวันนี้ที่ยืนยันแล้ว")
    assert_not_posted(con, branch_id, iso, "ใบตัดเบิก")
    clean, bad = _clean_lines(con, lines, whole_up=True)
    if bad:
        raise ValueError("บันทึกไม่ได้: " + "; ".join(bad[:3]) + " · แจ้งฝ่ายวิเคราะห์")
    todo =[(c, v) for c, v in clean if v is not None]
    if not todo:
        raise ValueError("ใส่จำนวนที่เบิกเพิ่มอย่างน้อย 1 รายการ")
    sid = row["slip_id"]
    conv = Converter(con)
    n_used = n_waste = 0
    for code, (used, waste, unit, ub, wb, reason, lnote) in todo:
        cur = con.execute("SELECT qty_used, qty_waste, unit, qty_used_base, qty_waste_base, waste_reason, note FROM bc_issue_line"
                          " WHERE slip_id=? AND ingredient_code=?", (sid, code)).fetchone()
        if not cur:
            con.execute("INSERT INTO bc_issue_line(slip_id, ingredient_code, qty_used, qty_waste, unit, qty_used_base,"
                        " qty_waste_base, waste_reason, note) VALUES(?,?,?,?,?,?,?,?,?)",
                        (sid, code, used, waste, unit, ub, wb, reason, lnote))
        else:
            qu, qw, cunit, qub, qwb, crs, cnt = cur
            if (cunit or "") != (unit or ""):                  # the new quantity in the unit the line already has
                f, _how = conv.to_base(code, 1, cunit or conv.base_unit(code))
                if not f:
                    raise ValueError("บันทึกไม่ได้: " + _no_unit(con, code, cunit) + " · แจ้งฝ่ายวิเคราะห์")
                used, waste = ub / f, wb / f
            qu, qw = round((qu or 0) + used, 6), round((qw or 0) + waste, 6)
            qub, qwb = round((qub or 0) + ub, 6), round((qwb or 0) + wb, 6)
            new_rs, new_nt = crs, cnt
            if waste > 0:
                if not (cur[1] or 0) > 0:                      # the line had no waste before: this reason is its reason
                    new_rs, new_nt = reason, lnote or cnt
                elif reason != crs or lnote:                   # a second reason on the same item stays on the record in the note
                    extra = f"เบิกเพิ่ม: {WASTE_REASON_TH.get(reason, reason)}" + (f" – {lnote}" if lnote else "")
                    new_nt = f"{cnt} · {extra}" if cnt else extra
            elif lnote:
                new_nt = f"{cnt} · เบิกเพิ่ม: {lnote}" if cnt else f"เบิกเพิ่ม: {lnote}"
            con.execute("UPDATE bc_issue_line SET qty_used=?, qty_waste=?, qty_used_base=?, qty_waste_base=?, waste_reason=?, note=?"
                        " WHERE slip_id=? AND ingredient_code=?", (qu, qw, qub, qwb, new_rs, new_nt, sid, code))
        n_used += 1 if used > 0 else 0
        n_waste += 1 if waste > 0 else 0
    con.execute("UPDATE bc_issue_slip SET updated_at=? WHERE slip_id=?", (_stamp(now), sid))
    row = _row_by_id(con, sid)
    summary = f"เบิกใช้ {n_used} รายการ · ของเสีย {n_waste} รายการ"
    _log(con, row, who, "append", late=row["late"], lines=len(todo), note=summary, now=now)
    audit(con, who, "issue_append", "bc_issue_slip", str(sid), None,
          {"branch_id": branch_id, "date": iso, "lines": len(todo), "codes": [c for c, _ in todo][:20]})
    st = status_of(con, branch_id, iso)
    return {"slip_id": sid, "date": iso, "status": st["status"], "added": len(todo), "lines": st["lines"],
            "appends": st["appends"], "late": int(row["late"] or 0), "slip": st}


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
        raise ValueError("ระบุเหตุผลที่เปิดแก้ไขใบตัดเบิก")
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
        raise ValueError("คำขอไม่ถูกต้อง")
    d1 = date.fromisoformat(_iso(date_to)) if date_to else now_bkk().date()
    d0 = date.fromisoformat(_iso(date_from)) if date_from else d1 - timedelta(days=30)
    sql = ("SELECT " + ", ".join("s." + c for c in _COLS) + ", b.name,"
           " (SELECT COUNT(*) FROM bc_issue_line l WHERE l.slip_id=s.slip_id),"
           " (SELECT COUNT(*) FROM bc_issue_line l WHERE l.slip_id=s.slip_id AND l.qty_waste>0),"
           " (SELECT COUNT(*) FROM bc_issue_log g WHERE g.slip_id=s.slip_id AND g.action='append')"
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
                    "reopened_at": row["reopened_at"], "appends": r[len(_COLS) + 3]})
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
    out["appends"] = sum(1 for x in out["log"] if x["action"] == "append")      # PR-M7: lines added to the confirmed slip of its day
    return out
