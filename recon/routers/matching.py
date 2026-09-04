"""Matching: the v2 channel-first reconcile, manual matches, channel moves,
drills, suggestions, payout matching, the overview and lane adjustments.

Moved out of recon/main.py in Phase 7c (2026-09) by the AST: these endpoints and
the helpers only they use, unchanged. URLs are unchanged.
"""
from fastapi import APIRouter
from ..core import _DAY_RE
from ..core import _MASTER_ONLY
from ..core import _PERIOD_RE
from ..core import _actor
from ..core import _assert_period_open
from ..core import _audit
from ..core import _validate_master_branch
from ..core import _validate_period
from ..core import get_db
from ..core import require_write_token
from ..matcher import _BANK_LABEL
from ..matcher import category_reconciliation
from ..matcher import lane_ext_lines
from ..matcher import lane_lines
from ..matcher import lane_pos_days
from ..matcher import map_bank_suggestions
from ..matcher import payout_matching
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
        pos = lane_pos_days(conn, branch_id, period_month, category, bank)
        ext = lane_ext_lines(conn, branch_id, period_month, category, bank)

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
                "matches": matches}


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
    pos_amount = round(sum(float(x.get("amount") or 0) for x in body.pos_days), 2)
    ext_amount = round(sum(float(x.get("amount") or 0) for x in body.ext), 2)
    with get_db() as conn:
        _validate_master_branch(conn, body.branch_id)
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
        for x in body.pos_days:
            day, amt = x.get("day"), float(x.get("amount") or 0)
            if amt <= 0:
                raise HTTPException(400, f"amount for {day} must be positive.")
            remaining = round(totals.get(day, 0.0) - already.get(day, 0.0), 2)
            if amt > remaining + 0.5:
                raise HTTPException(
                    409, f"{day}: amount {amt:.0f} exceeds remaining {remaining:.0f}.")
        cur = conn.execute("""
            INSERT INTO recon_manual_match
                (branch_id, category, bank, period_month, pos_amount, ext_amount,
                 variance, pos_days, note, matched_by)
            VALUES (?,?,?,?,?,?,?,?,?,?)
        """, (body.branch_id, body.category, body.bank, body.period_month,
              pos_amount, ext_amount, round(ext_amount - pos_amount, 2),
              json.dumps(body.pos_days), note, actor))
        mid = cur.lastrowid
        for e in body.ext:
            tbl = e.get("table")
            if tbl not in _EXT_TABLES:
                raise HTTPException(400, f"bad ext table {tbl!r}")
            eid = int(e.get("id"))
            conn.execute("INSERT INTO recon_manual_match_line (match_id, ext_table, ext_id, amount) "
                         "VALUES (?,?,?,?)", (mid, tbl, eid, float(e.get("amount") or 0)))
            conn.execute(f"UPDATE {tbl} SET match_status='manual' WHERE id=?", (eid,))
        _audit(conn, "manual_match", mid, "create", actor,
               [{"field": "match", "old": None,
                 "new": f"POS {pos_amount:.0f} ↔ EXT {ext_amount:.0f} (var {ext_amount-pos_amount:.0f})"}])
        conn.commit()
    return {"match_id": mid, "pos_amount": pos_amount, "ext_amount": ext_amount,
            "variance": round(ext_amount - pos_amount, 2)}


@router.delete("/api/manual/{match_id}", dependencies=[Depends(require_write_token)])
def manual_delete(match_id: int):
    with get_db() as conn:
        m = conn.execute("SELECT branch_id, period_month FROM recon_manual_match WHERE match_id=?",
                         (match_id,)).fetchone()
        if not m:
            raise HTTPException(404)
        _assert_period_open(conn, m["branch_id"], m["period_month"])
        for ln in conn.execute("SELECT ext_table, ext_id FROM recon_manual_match_line WHERE match_id=?",
                               (match_id,)):
            if ln["ext_table"] in _EXT_TABLES:
                conn.execute(f"UPDATE {ln['ext_table']} SET match_status='unmatched' "
                             f"WHERE id=? AND match_status='manual'", (ln["ext_id"],))
        conn.execute("DELETE FROM recon_manual_match_line WHERE match_id=?", (match_id,))
        conn.execute("DELETE FROM recon_manual_match WHERE match_id=?", (match_id,))
        _audit(conn, "manual_match", match_id, "delete", "staff",
               [{"field": "match", "old": "matched", "new": None}])
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
def lane_adj_delete(adj_id: int):
    with get_db() as conn:
        row = conn.execute(
            "SELECT branch_id, period_month FROM recon_lane_adj WHERE id=?", (adj_id,)).fetchone()
        if not row:
            raise HTTPException(404)
        _assert_period_open(conn, row["branch_id"], row["period_month"])
        conn.execute("DELETE FROM recon_lane_adj WHERE id=?", (adj_id,))
        conn.commit()
    return {"deleted": adj_id}


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
    with get_db() as conn:
        _validate_master_branch(conn, body.branch_id)
        _assert_period_open(conn, body.branch_id, body.period_month)
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
            "(branch_id,period_month,from_category,from_bank,to_category,to_bank,amount,note,created_by) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (body.branch_id, body.period_month, fc, fb, tc, tb,
             round(body.amount, 2), body.note, actor))
        _audit(conn, "channel_move", cur.lastrowid, "create", actor,
               [{"field": "move", "old": f"{fc}·{fb}", "new": f"{tc}·{tb}"},
                {"field": "amount", "old": None, "new": round(body.amount, 2)}])
        conn.commit()
        return {"id": cur.lastrowid, "from": [fc, fb], "to": [tc, tb],
                "amount": round(body.amount, 2)}


@router.delete("/api/channel-move/{move_id}", dependencies=[Depends(require_write_token)])
def channel_move_delete(move_id: int):
    with get_db() as conn:
        row = conn.execute(
            "SELECT branch_id, period_month FROM recon_channel_move WHERE id=?", (move_id,)).fetchone()
        if not row:
            raise HTTPException(404)
        _assert_period_open(conn, row["branch_id"], row["period_month"])
        conn.execute("DELETE FROM recon_channel_move WHERE id=?", (move_id,))
        _audit(conn, "channel_move", move_id, "delete", "staff")
        conn.commit()
    return {"deleted": move_id}


@router.get("/api/overview")
def overview(period_month: str):
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
