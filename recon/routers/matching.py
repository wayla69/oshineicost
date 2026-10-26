"""Matching: the v2 channel-first reconcile, manual matches, channel moves,
drills, suggestions, payout matching, the overview and lane adjustments.

Moved out of recon/main.py in Phase 7c (2026-09) by the AST: these endpoints and
the helpers only they use, unchanged. URLs are unchanged.
"""
from fastapi import APIRouter
from ..core import _DAY_RE
from ..core import _MASTER_ONLY
from ..core import _PERIOD_RE
from ..core import _POS_DAY_UNAVAILABLE
from ..core import _actor
from ..core import _assert_period_open
from ..core import _assert_bank_line_open
from ..core import _assert_bank_line_release_ok
from ..core import _audit
from ..core import _validate_master_branch
from ..core import _validate_period
from ..core import _has_table
from ..core import get_db
from ..core import require_write_token
from ..matcher import _BANK_LABEL
from ..matcher import category_reconciliation
from ..matcher import lane_ext_lines
from ..matcher import lane_lines
from ..matcher import lane_pos_days
from ..matcher import lane_lag_days
from ..matcher import map_bank_suggestions
from ..matcher import payout_matching
from ..matcher import payout_reconciliation
from ..matcher._shared import _CATEGORY_SOURCE
from ..matcher import pos_codes_scope
from ..matcher import pos_lane_lines
from ..matcher import suggest_reclassifications
from ..reconcile_v2 import reconcile_month
from ..reconcile_v2 import reconcile_overview
from fastapi import Depends
from fastapi import HTTPException
from fastapi import Request
from pydantic import BaseModel
import json
from datetime import date as _date
from datetime import timedelta as _timedelta

router = APIRouter()


@router.get("/api/v2/overview")
def v2_overview(month: str):
    """All master branches for one month: progress, per-channel variance, status."""
    _validate_period(month)
    with get_db() as conn:
        return reconcile_overview(conn, month)


@router.get("/api/v2/reconcile")
def v2_reconcile(branch_id: int, month: str):
    """One branch, one month, channel-first: documents checklist, channel cards
    with per-day detail, non-revenue (internal transfers), setup questions, todo."""
    _validate_period(month)
    with get_db() as conn:
        _validate_master_branch(conn, branch_id)
        return reconcile_month(conn, branch_id, month)


@router.get("/api/v2/lane")
def v2_lane(branch_id: int, month: str, category: str, bank: str, day: str | None = None):
    """Line-level drill for v2 (same data as the session lane endpoint, keyed by
    branch+month directly so the UI needs no session to browse)."""
    _validate_period(month)
    with get_db() as conn:
        _validate_master_branch(conn, branch_id)
        lines = lane_lines(conn, branch_id, month, category, bank, day)
        return {"category": category, "bank": bank, "day": day,
                "count": len(lines), "total": round(sum(l["amount"] for l in lines), 2),
                "lines": lines}


@router.get("/api/v2/pos-lines")
def v2_pos_lines(branch_id: int, month: str, category: str,
                 bank: str | None = None, day: str | None = None,
                 code: str | None = None):
    """Bill-level POS drill: the individual receipts (sale_payment) behind a
    channel's POS total for a day/month, grouped by payment code. Ties out to the
    POS number shown on the channel/day card — the POS side of the day drill."""
    _validate_period(month)
    with get_db() as conn:
        _validate_master_branch(conn, branch_id)
        return pos_lane_lines(conn, branch_id, month, category, bank, day, code)


@router.get("/api/drill")
def drill(branch_id: int, category: str | None = None, bank: str | None = None,
          day: str | None = None, period_month: str | None = None):
    """Day-scoped (or month-scoped) drill into one branch's payment codes and the
    matching statement lines, so staff can see which codes are mapped vs unmapped
    and act on the unmapped. `category`/`bank` narrow to a single lane."""
    if day and not _DAY_RE.match(day):
        raise HTTPException(400, "day must be in 'YYYY-MM-DD' format.")
    if not day and not (period_month and _PERIOD_RE.match(period_month)):
        raise HTTPException(400, "pass a valid day (YYYY-MM-DD) or period_month (YYYY-MM).")
    with get_db() as conn:
        _validate_master_branch(conn, branch_id)
        codes = pos_codes_scope(conn, branch_id, day=day, period_month=period_month)
        for c in codes:
            c["in_lane"] = bool(category) and c["category"] == category \
                and (not bank or bank in ("*", "") or c["bank"] == bank)
        lane_codes = [c for c in codes if c["in_lane"]] if category else codes
        attention  = [c for c in codes if c["needs_map"]]

        ext_lines = []
        if category:
            ext_lines = lane_lines(conn, branch_id, (day or period_month)[:7],
                                   category, bank or "NONE", day)

        pos_total = round(sum(c["amount"] for c in lane_codes if c["reconcile"]), 2)
        ext_total = round(sum(l["amount"] for l in ext_lines), 2)
        bname = conn.execute("SELECT name FROM branches WHERE branch_id=?",
                             (branch_id,)).fetchone()["name"]
        return {
            "branch_id": branch_id, "branch_name": bname,
            "day": day, "period_month": period_month,
            "category": category, "bank": bank,
            "codes": codes, "lane_codes": lane_codes, "attention": attention,
            "ext_lines": ext_lines,
            "pos_total": pos_total, "ext_total": ext_total,
            "variance": round(ext_total - pos_total, 2),
        }


_EXT_TABLES = {"recon_bank_lines", "recon_cc_lines", "recon_grab_lines", "recon_lineman_lines"}


# table → (date column, amount column) — the same amount lane_ext_lines shows staff,
# so a manual match books what the document says, never what the client sent.
_EXT_COLS = {
    "recon_bank_lines":    ("txn_date",        "credit"),
    "recon_cc_lines":      ("settlement_date", "gross_amount"),
    "recon_grab_lines":    ("order_date",      "food_amount"),
    "recon_lineman_lines": ("order_date",      "food_amount"),
}


class ManualMatch(BaseModel):
    branch_id:    int
    category:     str
    bank:         str
    period_month: str
    pos_days:     list[dict] = []   # [{day, amount}]
    ext:          list[dict] = []   # [{table, id, amount}]
    note:         str = ""
    matched_by:   str = ""


@router.get("/api/payout-matching")
def payout_matching_api(branch_id: int, period_month: str):
    """Per-provider net-day ↔ bank-credit grouping (handles combined-day payouts).
    The 3rd leg of the chain: POS↔report gross, report net↔bank payout, here
    traced per actual transfer."""
    _validate_period(period_month)
    with get_db() as conn:
        _validate_master_branch(conn, branch_id)
        return payout_matching(conn, branch_id, period_month)


@router.get("/api/map-suggestions")
def get_map_suggestions(branch_id: int, period_month: str):
    """Bank suggestions for needs-map POS codes, derived from the uploaded
    documents themselves (daily-series match against statement-lane residuals)."""
    _validate_period(period_month)
    with get_db() as conn:
        return {"suggestions": map_bank_suggestions(conn, branch_id, period_month)}


@router.get("/api/suggestions")
def suggestions(branch_id: int, period_month: str):
    """Wrong-channel reclassification candidates: same bank, equal amount, ±1 day.
    Each item is a ready-to-apply /api/manual payload (POST it to accept)."""
    _validate_period(period_month)
    with get_db() as conn:
        _validate_master_branch(conn, branch_id)
        return {"suggestions": suggest_reclassifications(conn, branch_id, period_month)}


@router.get("/api/manual")
def manual_list(branch_id: int, category: str, bank: str, period_month: str):
    _validate_period(period_month)
    with get_db() as conn:
        _validate_master_branch(conn, branch_id)
        has_pos = _has_table(conn, "pos_payment_day")      # written by the dashboard sync
        pos = lane_pos_days(conn, branch_id, period_month, category, bank) if has_pos else []
        ext = lane_ext_lines(conn, branch_id, period_month, category, bank)
        # The lane's settlement window into next month (day 31 banked on the 1st):
        # those lines can be ticked too, flagged so the drawer can say so.
        win_end = _next_month_cutoff(period_month, lane_lag_days(conn, category, bank))
        ext += [{**l, "next_month": True}
                for l in lane_ext_lines(conn, branch_id, _next_month(period_month), category, bank)
                if str(l["date"])[:10] < win_end]

        matches, consumed = [], {}
        for m in conn.execute(
            "SELECT * FROM recon_manual_match WHERE branch_id=? AND period_month=? "
            "AND category=? AND bank=? ORDER BY matched_at DESC",
            (branch_id, period_month, category, bank)):
            pd = json.loads(m["pos_days"] or "[]")
            for x in pd:
                consumed[x["day"]] = consumed.get(x["day"], 0.0) + float(x.get("amount") or 0)
            lines = [dict(r) for r in conn.execute(
                "SELECT ext_table, ext_id, amount FROM recon_manual_match_line WHERE match_id=?",
                (m["match_id"],))]
            matches.append({**dict(m), "pos_days": pd, "lines": lines})

        for d in pos:
            d["consumed"]  = round(consumed.get(d["day"], 0.0), 2)
            d["remaining"] = round(d["total"] - d["consumed"], 2)

        return {"branch_id": branch_id, "category": category, "bank": bank,
                "period_month": period_month, "pos_days": pos, "ext_lines": ext,
                "matches": matches, "pos_available": has_pos}


def _next_month(period_month: str) -> str:
    y, m = int(period_month[:4]), int(period_month[5:7])
    return f"{y + 1}-01" if m == 12 else f"{y}-{m + 1:02d}"


def _next_month_cutoff(period_month: str, lag_days: int) -> str:
    """First day AFTER a lane's window: the 1st of next month + lag_days."""
    y, m = int(period_month[:4]), int(period_month[5:7])
    first = _date(y + 1, 1, 1) if m == 12 else _date(y, m + 1, 1)
    return (first + _timedelta(days=max(0, int(lag_days or 0)))).isoformat()


@router.post("/api/manual", dependencies=[Depends(require_write_token)])
def manual_create(body: ManualMatch, request: Request):
    _validate_period(body.period_month)
    if not body.pos_days or not body.ext:
        raise HTTPException(400, "Tick at least one POS day and one statement line.")
    # Every manual match carries a reason for the audit trail; one-tap matches (e.g. the
    # reclass suggestions) pass their own note, and anything still blank is stamped with
    # a system reason rather than rejected (keeps the one-click flow working).
    note = (body.note or "").strip() or "(จับคู่ด้วยมือ — ตามรายการที่ตรงกัน)"
    actor = _actor(request, body.matched_by)
    # POS days: a real day of THIS month (the string is echoed into print reports),
    # summed per day — two entries for one day must not each pass the guard alone.
    pos_days, want = [], {}
    for x in body.pos_days:
        day = x.get("day")
        if not (isinstance(day, str) and _DAY_RE.match(day) and day.startswith(body.period_month)):
            raise HTTPException(400, f"วันที่ POS ต้องเป็นวันในเดือน {body.period_month} (YYYY-MM-DD)")
        try:
            amt = float(x.get("amount") or 0)
        except (TypeError, ValueError):
            raise HTTPException(400, f"amount for {day} must be a number.")
        if amt <= 0:
            raise HTTPException(400, f"amount for {day} must be positive.")
        pos_days.append({"day": day, "amount": amt})
        want[day] = want.get(day, 0.0) + amt
    refs: list = []
    for e in body.ext:
        tbl = e.get("table")
        if tbl not in _EXT_COLS:
            raise HTTPException(400, f"bad ext table {tbl!r}")
        try:
            eid = int(e.get("id"))
        except (TypeError, ValueError):
            raise HTTPException(400, f"bad ext id {e.get('id')!r}")
        if (tbl, eid) in refs:
            raise HTTPException(400, "เลือกรายการเอกสารเดียวกันซ้ำในคู่เดียว")
        refs.append((tbl, eid))
    pos_amount = round(sum(want.values()), 2)
    with get_db() as conn:
        _validate_master_branch(conn, body.branch_id)
        if not _has_table(conn, "pos_payment_day"):
            raise HTTPException(409, _POS_DAY_UNAVAILABLE)
        # Check-then-insert under one write lock: a double-click or a second tab must
        # not both read "remaining" / "line free" and then both book.
        conn.execute("BEGIN IMMEDIATE")
        _assert_period_open(conn, body.branch_id, body.period_month)

        # Partial allocation guard: a day can't be allocated beyond what's left.
        totals = {d["day"]: d["total"] for d in
                  lane_pos_days(conn, body.branch_id, body.period_month, body.category, body.bank)}
        already: dict = {}
        for m in conn.execute(
            "SELECT pos_days FROM recon_manual_match WHERE branch_id=? AND period_month=? "
            "AND category=? AND bank=?",
            (body.branch_id, body.period_month, body.category, body.bank)):
            for x in json.loads(m["pos_days"] or "[]"):
                already[x["day"]] = already.get(x["day"], 0.0) + float(x.get("amount") or 0)
        for day, amt in want.items():
            remaining = round(totals.get(day, 0.0) - already.get(day, 0.0), 2)
            if amt > remaining + 0.5:
                raise HTTPException(
                    409, f"{day}: amount {amt:.0f} exceeds remaining {remaining:.0f}.")
        # Each ticked line: this branch, this month (or the lane's settlement window
        # into next month: day 31's cash banked on the 1st), free to book — amount
        # from the DB. The line's own month must be open, and a bank credit a signed-
        # off previous month rests on can't be re-booked here.
        win_end = _next_month_cutoff(body.period_month,
                                     lane_lag_days(conn, body.category, body.bank))
        lock_cache: dict = {}
        lines = []
        for tbl, eid in refs:
            dcol, acol = _EXT_COLS[tbl]
            r = conn.execute(f"SELECT branch_id, {dcol} AS d, {acol} AS amt, match_status "
                             f"FROM {tbl} WHERE id=?", (eid,)).fetchone()
            if not r:
                raise HTTPException(404, f"ไม่พบรายการเอกสาร {tbl}#{eid}")
            d = str(r["d"] or "")[:10]
            if r["branch_id"] != body.branch_id or not (
                    d[:7] == body.period_month or (d[:7] > body.period_month and d < win_end)):
                raise HTTPException(400, f"รายการเอกสาร #{eid} ไม่ใช่ของสาขา/เดือนนี้")
            if tbl == "recon_bank_lines":
                _assert_bank_line_open(conn, body.branch_id, eid, d, lock_cache)
            elif d[:7] != body.period_month:
                _assert_period_open(conn, body.branch_id, d[:7])
            if r["match_status"] == "manual":
                raise HTTPException(409, f"รายการเอกสาร #{eid} ถูกจับคู่ด้วยมือไปแล้ว — ยกเลิกคู่เดิมก่อน")
            if r["match_status"] == "excluded":
                raise HTTPException(409, f"รายการเอกสาร #{eid} ถูกตัดออก (ไม่ใช่รายได้) — คืนค่าก่อนจึงจับคู่ได้")
            lines.append((tbl, eid, round(float(r["amt"] or 0), 2)))
        ext_amount = round(sum(a for _, _, a in lines), 2)
        cur = conn.execute("""
            INSERT INTO recon_manual_match
                (branch_id, category, bank, period_month, pos_amount, ext_amount,
                 variance, pos_days, note, matched_by)
            VALUES (?,?,?,?,?,?,?,?,?,?)
        """, (body.branch_id, body.category, body.bank, body.period_month,
              pos_amount, ext_amount, round(ext_amount - pos_amount, 2),
              json.dumps(pos_days), note, actor))
        mid = cur.lastrowid
        for tbl, eid, amt in lines:
            conn.execute("INSERT INTO recon_manual_match_line (match_id, ext_table, ext_id, amount) "
                         "VALUES (?,?,?,?)", (mid, tbl, eid, amt))
            conn.execute(f"UPDATE {tbl} SET match_status='manual' WHERE id=?", (eid,))
        _audit(conn, "manual_match", mid, "create", actor,
               [{"field": "match", "old": None,
                 "new": f"POS {pos_amount:.0f} ↔ EXT {ext_amount:.0f} (var {ext_amount-pos_amount:.0f})"}])
        conn.commit()
    return {"match_id": mid, "pos_amount": pos_amount, "ext_amount": ext_amount,
            "variance": round(ext_amount - pos_amount, 2)}


def _status_before_manual(conn, tbl: str, eid: int) -> str:
    """What a line returns to when its manual match is undone: 'unmatched', unless the
    audit trail shows it was excluded (not revenue) when it was ticked — possible only
    for matches booked before /api/manual refused excluded lines."""
    if tbl == "recon_bank_lines":
        last = conn.execute(
            "SELECT new_val FROM recon_audit_log WHERE entity='bank_line' AND entity_id=? "
            "AND field='match_status' ORDER BY id DESC LIMIT 1", (eid,)).fetchone()
        if last and last["new_val"] == "excluded":
            return "excluded"
    return "unmatched"


@router.delete("/api/manual/{match_id}", dependencies=[Depends(require_write_token)])
def manual_delete(match_id: int, request: Request):
    with get_db() as conn:
        m = conn.execute("SELECT * FROM recon_manual_match WHERE match_id=?",
                         (match_id,)).fetchone()
        if not m:
            raise HTTPException(404)
        _assert_period_open(conn, m["branch_id"], m["period_month"])
        released = []
        for ln in conn.execute("SELECT ext_table, ext_id FROM recon_manual_match_line WHERE match_id=?",
                               (match_id,)).fetchall():
            tbl, eid = ln["ext_table"], ln["ext_id"]
            if tbl not in _EXT_TABLES:
                continue
            if conn.execute("SELECT 1 FROM recon_manual_match_line WHERE ext_table=? AND ext_id=? "
                            "AND match_id<>?", (tbl, eid, match_id)).fetchone():
                continue                         # another match still books this line
            dcol = _EXT_COLS[tbl][0]
            lr = conn.execute(f"SELECT {dcol} AS d FROM {tbl} WHERE id=?", (eid,)).fetchone()
            d = str(lr["d"] or "")[:10] if lr else ""
            if d[:7] and d[:7] != m["period_month"]:
                _assert_period_open(conn, m["branch_id"], d[:7])   # a next-month line
            conn.execute(f"UPDATE {tbl} SET match_status=? WHERE id=? AND match_status='manual'",
                         (_status_before_manual(conn, tbl, eid), eid))
            if tbl == "recon_bank_lines":
                released.append((eid, d))
        # Back in the matchers: a signed-off previous month must not start claiming
        # it (its figures would move). Refused → nothing above is committed.
        for eid, d in released:
            _assert_bank_line_release_ok(conn, m["branch_id"], eid, d)
        conn.execute("DELETE FROM recon_manual_match_line WHERE match_id=?", (match_id,))
        conn.execute("DELETE FROM recon_manual_match WHERE match_id=?", (match_id,))
        # The row is gone after this — the audit row carries its scope and content.
        _audit(conn, "manual_match", match_id, "delete", _actor(request), [
            {"field": "match", "old": f"POS {m['pos_amount'] or 0:.0f} ↔ EXT {m['ext_amount'] or 0:.0f}",
             "new": None},
            {"field": "branch_id", "old": m["branch_id"], "new": None},
            {"field": "period_month", "old": m["period_month"], "new": None},
            {"field": "lane", "old": f"{m['category']}·{m['bank']}", "new": None},
            {"field": "pos_days", "old": m["pos_days"], "new": None},
            {"field": "note", "old": m["note"], "new": None}])
        conn.commit()
    return {"deleted": match_id}


class LaneAdj(BaseModel):
    branch_id:    int
    period_month: str
    category:     str
    bank:         str = "NONE"
    adj_pos:      float = 0.0
    adj_ext:      float = 0.0
    note:         str | None = None
    adj_type:     str = "other"
    created_by:   str | None = None


@router.get("/api/lanes/adj")
def lane_adj_list(branch_id: int, period_month: str):
    _validate_period(period_month)
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM recon_lane_adj WHERE branch_id=? AND period_month=? "
            "ORDER BY category, bank",
            (branch_id, period_month)).fetchall()
        return [dict(r) for r in rows]


@router.post("/api/lanes/adj", dependencies=[Depends(require_write_token)])
def lane_adj_upsert(body: LaneAdj, request: Request):
    _validate_period(body.period_month)
    if body.adj_type not in ("off_pos", "deposit_note", "other", "accept"):
        raise HTTPException(400, "adj_type must be off_pos | deposit_note | other | accept")
    # AUD-8 (widened 2026-06-19): EVERY lane adjustment is a manual override of the
    # reconciliation and must carry a MEANINGFUL reason (auditors sample these), not a
    # one-character placeholder — not just adj_type='accept'.
    if len((body.note or "").strip()) < 10:
        raise HTTPException(400, "การปรับปรุง lane ต้องระบุเหตุผลที่ชัดเจน (อย่างน้อย 10 ตัวอักษร)")
    actor = _actor(request, body.created_by)
    with get_db() as conn:
        _validate_master_branch(conn, body.branch_id)
        _assert_period_open(conn, body.branch_id, body.period_month)
        existing = conn.execute(
            "SELECT id FROM recon_lane_adj WHERE branch_id=? AND period_month=? "
            "AND category=? AND bank=?",
            (body.branch_id, body.period_month, body.category, body.bank)).fetchone()
        if existing:
            conn.execute(
                "UPDATE recon_lane_adj SET adj_pos=?, adj_ext=?, note=?, adj_type=?, "
                "created_by=?, created_at=datetime('now') WHERE id=?",
                (body.adj_pos, body.adj_ext, body.note, body.adj_type,
                 actor, existing["id"]))
            row_id = existing["id"]
        else:
            cur = conn.execute(
                "INSERT INTO recon_lane_adj "
                "(branch_id,period_month,category,bank,adj_pos,adj_ext,note,adj_type,created_by) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (body.branch_id, body.period_month, body.category, body.bank,
                 body.adj_pos, body.adj_ext, body.note, body.adj_type, actor))
            row_id = cur.lastrowid
        _audit(conn, "lane_adj", row_id, "update" if existing else "create", actor,
               [{"field": body.adj_type, "old": None,
                 "new": f"{body.category}·{body.bank} POS+{body.adj_pos:.0f}/EXT+{body.adj_ext:.0f}"}])
        conn.commit()
    return {"id": row_id, "adj_pos": body.adj_pos, "adj_ext": body.adj_ext}


@router.delete("/api/lanes/adj/{adj_id}", dependencies=[Depends(require_write_token)])
def lane_adj_delete(adj_id: int, request: Request):
    with get_db() as conn:
        row = conn.execute("SELECT * FROM recon_lane_adj WHERE id=?", (adj_id,)).fetchone()
        if not row:
            raise HTTPException(404)
        _assert_period_open(conn, row["branch_id"], row["period_month"])
        conn.execute("DELETE FROM recon_lane_adj WHERE id=?", (adj_id,))
        # Un-accepting a lane is an override too — audit what was removed.
        _audit(conn, "lane_adj", adj_id, "delete", _actor(request), [
            {"field": row["adj_type"], "new": None,
             "old": f"{row['category']}·{row['bank']} POS+{row['adj_pos'] or 0:.0f}/EXT+{row['adj_ext'] or 0:.0f}"},
            {"field": "branch_id", "old": row["branch_id"], "new": None},
            {"field": "period_month", "old": row["period_month"], "new": None},
            {"field": "note", "old": row["note"], "new": None}])
        conn.commit()
    return {"deleted": adj_id}



# ── Payout gaps on the branch page (owner 2026-10-06) ─────────────────────────
# Provider NET ↔ bank payout credit mismatches are acknowledged from the branch
# page. The acknowledgement IS the PAYOUT discrepancy row, born 'accepted' at the
# live variance — run-match then leaves it alone (same variance) and approval
# isn't blocked; if the gap moves later it is queued again.
class PayoutAck(BaseModel):
    branch_id:    int
    period_month: str
    category:     str
    note:         str | None = None
    created_by:   str | None = None


@router.post("/api/payout-ack", dependencies=[Depends(require_write_token)])
def payout_ack(body: PayoutAck, request: Request):
    _validate_period(body.period_month)
    note = (body.note or "").strip()
    if len(note) < 10:
        raise HTTPException(400, "การรับทราบยอดโอนต้องระบุเหตุผลที่ชัดเจน (อย่างน้อย 10 ตัวอักษร)")
    actor = _actor(request, body.created_by)
    with get_db() as conn:
        _validate_master_branch(conn, body.branch_id)
        _assert_period_open(conn, body.branch_id, body.period_month)
        ses = conn.execute("SELECT session_id FROM recon_sessions WHERE branch_id=? AND period_month=?",
                           (body.branch_id, body.period_month)).fetchone()
        if not ses:
            raise HTTPException(409, "ยังไม่มีงวดของสาขานี้ — เปิดหน้าสาขาแล้วลองใหม่")
        row = next((r for r in payout_reconciliation(conn, body.branch_id, body.period_month)
                    if r["category"] == body.category), None)
        if not row or row["status"] != "mismatch":
            raise HTTPException(409, "ยอดโอนของช่องทางนี้ไม่ได้ต่างจากรายงานแล้ว — โหลดหน้าใหม่")
        sid = ses["session_id"]
        conn.execute("DELETE FROM recon_discrepancies WHERE session_id=? AND category=? "
                     "AND bank='PAYOUT' AND disc_type='amount_mismatch' AND resolution='pending'",
                     (sid, body.category))
        cur = conn.execute("""
            INSERT INTO recon_discrepancies
                (session_id, disc_type, source_type, category, bank,
                 amount_external, amount_pos, variance,
                 resolution, resolution_note, resolved_by, resolved_at)
            VALUES (?,?,?,?,?,?,?,?,'accepted',?,?,datetime('now'))
        """, (sid, "amount_mismatch", _CATEGORY_SOURCE.get(body.category, "bank"), body.category,
              "PAYOUT", row["bank_payout"], row["report_net"], row["variance"],
              f"รับทราบ: {note}", actor or "staff"))
        _audit(conn, "discrepancy", cur.lastrowid, "create", actor or "staff", [
            {"field": "payout_accept", "old": None,
             "new": f"{body.category} net {float(row['report_net'] or 0):.2f} / "
                    f"bank {float(row['bank_payout'] or 0):.2f} ({float(row['variance'] or 0):+.2f})"},
            {"field": "branch_id", "old": None, "new": body.branch_id},
            {"field": "period_month", "old": None, "new": body.period_month},
            {"field": "note", "old": None, "new": note}])
        conn.commit()
    return {"id": cur.lastrowid, "variance": row["variance"]}


@router.delete("/api/payout-ack/{disc_id}", dependencies=[Depends(require_write_token)])
def payout_ack_delete(disc_id: int, request: Request):
    with get_db() as conn:
        row = conn.execute(
            "SELECT d.*, s.branch_id, s.period_month FROM recon_discrepancies d "
            "JOIN recon_sessions s ON s.session_id=d.session_id "
            "WHERE d.id=? AND d.bank='PAYOUT' AND d.resolution!='pending'", (disc_id,)).fetchone()
        if not row:
            raise HTTPException(404)
        _assert_period_open(conn, row["branch_id"], row["period_month"])
        conn.execute("DELETE FROM recon_discrepancies WHERE id=?", (disc_id,))
        _audit(conn, "discrepancy", disc_id, "delete", _actor(request), [
            {"field": "payout_accept", "new": None,
             "old": f"{row['category']} ({float(row['variance'] or 0):+.2f})"},
            {"field": "branch_id", "old": row["branch_id"], "new": None},
            {"field": "period_month", "old": row["period_month"], "new": None},
            {"field": "note", "old": row["resolution_note"], "new": None}])
        conn.commit()
    return {"deleted": disc_id}

_MOVE_CHANNELS = {"CASH", "QR", "TRANSFER", "CREDIT", "GRAB", "LINEMAN", "COUPON"}


def _norm_move_bank(category: str, bank: str | None) -> str:
    b = (bank or "NONE").strip() or "NONE"
    return "NONE" if category == "CASH" else b   # cash carries no bank dimension


class ChannelMove(BaseModel):
    branch_id:     int
    period_month:  str
    from_category: str
    from_bank:     str = "NONE"
    to_category:   str
    to_bank:       str = "NONE"
    amount:        float
    note:          str | None = None
    created_by:    str | None = None
    day:           str | None = None    # the sale day the mis-entry was on (YYYY-MM-DD)


@router.get("/api/channel-move")
def channel_move_list(branch_id: int, period_month: str):
    _validate_period(period_month)
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM recon_channel_move WHERE branch_id=? AND period_month=? "
            "ORDER BY created_at DESC, id DESC",
            (branch_id, period_month)).fetchall()
        return [dict(r) for r in rows]


@router.post("/api/channel-move", dependencies=[Depends(require_write_token)])
def channel_move_create(body: ChannelMove, request: Request):
    _validate_period(body.period_month)
    # A cross-channel move is a management override of what the POS recorded — it must
    # carry a meaningful reason for the audit trail (auditors sample these).
    if len((body.note or "").strip()) < 10:
        raise HTTPException(400, "การย้ายช่องต้องระบุเหตุผลที่ชัดเจน (อย่างน้อย 10 ตัวอักษร)")
    actor = _actor(request, body.created_by)
    fc, tc = body.from_category, body.to_category
    if fc not in _MOVE_CHANNELS or tc not in _MOVE_CHANNELS:
        raise HTTPException(400, f"channel must be one of {sorted(_MOVE_CHANNELS)}")
    fb, tb = _norm_move_bank(fc, body.from_bank), _norm_move_bank(tc, body.to_bank)
    if (fc, fb) == (tc, tb):
        raise HTTPException(400, "ช่องต้นทางและปลายทางต้องไม่ใช่ช่องเดียวกัน")
    if not (body.amount and body.amount > 0):
        raise HTTPException(400, "จำนวนเงินต้องมากกว่า 0")
    day = (body.day or "").strip() or None
    if day and not (_DAY_RE.match(day) and day.startswith(body.period_month)):
        raise HTTPException(400, f"วันที่ต้องเป็นวันในเดือน {body.period_month} (YYYY-MM-DD)")
    with get_db() as conn:
        _validate_master_branch(conn, body.branch_id)
        conn.execute("BEGIN IMMEDIATE")              # duplicate check + insert as one step
        _assert_period_open(conn, body.branch_id, body.period_month)
        # A double-click posts the same move twice within a second or two; a genuine
        # second move of the very same amount seconds later is not a real case.
        if conn.execute(
                "SELECT 1 FROM recon_channel_move WHERE branch_id=? AND period_month=? "
                "AND from_category=? AND from_bank=? AND to_category=? AND to_bank=? "
                "AND ABS(amount-?)<0.005 AND created_at >= datetime('now','-10 seconds')",
                (body.branch_id, body.period_month, fc, fb, tc, tb, round(body.amount, 2))).fetchone():
            raise HTTPException(409, "บันทึกการย้ายช่องนี้ไปแล้วเมื่อสักครู่ (กดซ้ำ?) — ตรวจรายการก่อนบันทึกอีกครั้ง")
        # Validate against the branch's CURRENT lanes: the source must exist and hold
        # enough POS (over-moving would drive the lane negative — the monthly view drops
        # the row while the daily view keeps it, so the two diverge). The destination must
        # be a real lane or a known bank (else a typo'd bank spawns an orphan pos_only lane).
        rows, _ = category_reconciliation(conn, body.branch_id, body.period_month)
        lanes = {(r["category"], r["bank"]): r["pos_amount"] for r in rows}
        if (fc, fb) not in lanes:
            raise HTTPException(400, "ช่องต้นทางไม่มียอดในเดือนนี้ — เลือกช่องที่มีรายการจริง")
        if body.amount > lanes[(fc, fb)] + 0.5:
            raise HTTPException(400,
                f"ย้ายได้ไม่เกินยอดที่อยู่ในช่องต้นทาง (คงเหลือ {lanes[(fc, fb)]:,.2f})")
        if (tc, tb) not in lanes and tb not in (set(_BANK_LABEL) | {"NONE", "ANY"}):
            raise HTTPException(400, "ช่อง/ธนาคารปลายทางไม่ถูกต้อง")
        cur = conn.execute(
            "INSERT INTO recon_channel_move "
            "(branch_id,period_month,from_category,from_bank,to_category,to_bank,amount,note,created_by,day) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (body.branch_id, body.period_month, fc, fb, tc, tb,
             round(body.amount, 2), body.note, actor, day))
        _audit(conn, "channel_move", cur.lastrowid, "create", actor,
               [{"field": "move", "old": f"{fc}·{fb}", "new": f"{tc}·{tb}"},
                {"field": "amount", "old": None, "new": round(body.amount, 2)}]
               + ([{"field": "day", "old": None, "new": day}] if day else []))
        conn.commit()
        return {"id": cur.lastrowid, "from": [fc, fb], "to": [tc, tb],
                "amount": round(body.amount, 2), "day": day}


@router.delete("/api/channel-move/{move_id}", dependencies=[Depends(require_write_token)])
def channel_move_delete(move_id: int, request: Request):
    with get_db() as conn:
        row = conn.execute("SELECT * FROM recon_channel_move WHERE id=?", (move_id,)).fetchone()
        if not row:
            raise HTTPException(404)
        _assert_period_open(conn, row["branch_id"], row["period_month"])
        conn.execute("DELETE FROM recon_channel_move WHERE id=?", (move_id,))
        _audit(conn, "channel_move", move_id, "delete", _actor(request), [
            {"field": "move", "new": None,
             "old": f"{row['from_category']}·{row['from_bank']} → {row['to_category']}·{row['to_bank']}"},
            {"field": "amount", "old": row["amount"], "new": None},
            {"field": "branch_id", "old": row["branch_id"], "new": None},
            {"field": "period_month", "old": row["period_month"], "new": None},
            {"field": "note", "old": row["note"], "new": None}])
        conn.commit()
    return {"deleted": move_id}


@router.get("/api/overview")
def overview(period_month: str):
    _validate_period(period_month)
    with get_db() as conn:
        branches = conn.execute(
            f"SELECT branch_id, name FROM branches WHERE {_MASTER_ONLY} ORDER BY name"
        ).fetchall()

        result = []
        for b in branches:
            bid = b["branch_id"]

            # Reconcilable POS tender total vs external statement total (same engine
            # as the session view, so overview and detail agree).
            rows, _ = category_reconciliation(conn, bid, period_month)
            pos_total = round(sum(r["pos_amount"] for r in rows), 2)
            ext_total = round(sum(r["ext_amount"] for r in rows), 2)

            session = conn.execute(
                "SELECT session_id, status FROM recon_sessions WHERE branch_id=? AND period_month=?",
                (bid, period_month)
            ).fetchone()

            # Open issues to follow up: pending discrepancies (if a session exists)
            # + lanes that aren't matched in the live reconciliation.
            open_issues = 0
            if session:
                open_issues = conn.execute(
                    "SELECT COUNT(*) FROM recon_discrepancies WHERE session_id=? AND resolution='pending'",
                    (session["session_id"],)).fetchone()[0]
            unmatched = sum(1 for r in rows if r["status"] != "matched")

            result.append({
                "branch_id":   bid,
                "branch_name": b["name"],
                "pos_total":   pos_total,
                "ext_total":   ext_total,
                "variance":    round(ext_total - pos_total, 2),
                "session_id":  session["session_id"] if session else None,
                "status":      session["status"] if session else "no_session",
                "open_issues": open_issues,
                "unmatched":   unmatched,
            })

    return result
