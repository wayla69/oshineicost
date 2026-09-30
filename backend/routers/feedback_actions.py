# -*- coding: utf-8 -*-
"""ปิดวงจร — from a voice to a fix to proof (E1 of the expansion plan).
The tab shows what guests complain about and where. This module is the record
of what was done about it: a theme on a branch becomes an action with an owner
and a due date; the theme is measured over the WINDOW_DAYS before the action
(baseline) and again over the WINDOW_DAYS after it is done, and the action is
verified better / same / worse on "negative voices per 100" — so a fix is a
claim until the guests confirm it. The weekly digest (Telegram every Monday
from the fetcher, and §12 of the Management Report) tells everyone whether
the voices moved.
  GET  /api/feedback/actions            the list (+ the after-window measure, verified when complete)
  POST /api/feedback/actions            create {title, theme, branch, owner, due, detail, from_id, by}
  POST /api/feedback/actions/{id}       update {status open|doing|done|dropped, owner, due, note, title, detail}
  GET  /api/feedback/digest             the last `days` in numbers against the `days` before
Table feedback_action — carried across the daily DB push by the "feedback" prefix.
"""
import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from feedback_host import branch_rows, get_db, require_write_token, scope_branch, who as _who
from routers.feedback import _ROW_COLS, _aggregate, _ensure as _ensure_feedback, _thai_now, _thai_today

router = APIRouter()

WINDOW_DAYS = 28
NO_BRANCH = "(ไม่ระบุสาขา)"
STATUSES = ("open", "doing", "done", "dropped")
RESULTS = ("better", "same", "worse", "no_data")

DDL = """
CREATE TABLE IF NOT EXISTS feedback_action(
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at     TEXT,
  created_by     TEXT,
  branch_name    TEXT,
  theme          TEXT,
  title          TEXT NOT NULL,
  detail         TEXT,
  owner          TEXT,
  due            TEXT,
  status         TEXT DEFAULT 'open',
  done_at        TEXT,
  note           TEXT,
  from_id        INTEGER,
  baseline_from  TEXT,
  baseline_to    TEXT,
  baseline_total INTEGER,
  baseline_n     INTEGER,
  baseline_neg   INTEGER,
  after_from     TEXT,
  after_to       TEXT,
  after_total    INTEGER,
  after_n        INTEGER,
  after_neg      INTEGER,
  result         TEXT,
  verified_at    TEXT,
  updated_at     TEXT
);
"""


def _today() -> str:            # seam for the tests
    return _thai_today()


def _ensure(con):
    _ensure_feedback(con)
    con.executescript(DDL)


def _d(s: str) -> datetime.date:
    return datetime.date.fromisoformat(s[:10])


def _shift(s: str, days: int) -> str:
    return (_d(s) + datetime.timedelta(days=days)).isoformat()


def _measure(con, branch: Optional[str], theme: Optional[str], d_from: str, d_to: str) -> dict:
    """Voices in scope over a window: all of them, those carrying the theme,
    and the negative ones carrying the theme."""
    w, p = ["rated_at >= ?", "rated_at <= ?"], [d_from, d_to]
    if branch:
        w.append("branch_name = ?"); p.append(branch)
    total = con.execute(f"SELECT COUNT(*) FROM customer_feedback WHERE {' AND '.join(w)}", p).fetchone()[0]
    if theme:
        w.append("themes LIKE ?"); p.append(f'%"{theme}"%')
    n, neg = con.execute(f"SELECT COUNT(*), SUM(CASE WHEN sentiment='neg' THEN 1 ELSE 0 END) "
                         f"FROM customer_feedback WHERE {' AND '.join(w)}", p).fetchone()
    return {"from": d_from, "to": d_to, "total": int(total or 0), "n": int(n or 0), "neg": int(neg or 0)}


def _rate(m: dict):
    """Negative voices with the theme per 100 voices in scope."""
    return round(100.0 * m["neg"] / m["total"], 1) if m and m.get("total") else None


def _verdict(before: dict, after: dict) -> str:
    if not after.get("total"):
        return "no_data"
    b, a = _rate(before), _rate(after)
    if b is None:
        return "worse" if after["neg"] else "same"
    if a <= b * 0.8:
        return "better"
    if a >= b * 1.2 and a > b + 1:
        return "worse"
    return "same"


def _row(con, r: dict, today: str) -> dict:
    """The stored row plus what the reader needs: overdue, the baseline rate,
    the after-window (partial while it is still running; stored and verified
    once the window has passed)."""
    out = dict(r)
    out["overdue"] = bool(r.get("due")) and r.get("status") in ("open", "doing") and r["due"] < today
    before = {"total": r.get("baseline_total") or 0, "n": r.get("baseline_n") or 0, "neg": r.get("baseline_neg") or 0}
    out["baseline_rate"] = _rate(before)
    out["after"] = None
    out["after_rate"] = None
    out["after_days_left"] = None
    if r.get("status") == "done" and r.get("done_at"):
        a_from, a_to = _shift(r["done_at"], 1), _shift(r["done_at"], WINDOW_DAYS)
        complete = today > a_to
        if r.get("result") and r.get("verified_at"):
            after = {"from": r["after_from"], "to": r["after_to"], "total": r["after_total"] or 0,
                     "n": r["after_n"] or 0, "neg": r["after_neg"] or 0}
        else:
            after = _measure(con, r.get("branch_name"), r.get("theme"), a_from, min(a_to, today))
            if complete:
                out["result"] = _verdict(before, after)
                out["verified_at"] = today
                con.execute("UPDATE feedback_action SET after_from=?, after_to=?, after_total=?, after_n=?, after_neg=?, "
                            "result=?, verified_at=? WHERE id=?",
                            (after["from"], after["to"], after["total"], after["n"], after["neg"], out["result"], today, r["id"]))
            else:
                out["after_days_left"] = (_d(a_to) - _d(today)).days
        out["after"] = after
        out["after_rate"] = _rate(after)
    return out


def _list(con, today: str, status=None, branch=None, theme=None) -> list:
    w, p = ["1=1"], []
    if status:
        w.append("status = ?"); p.append(status)
    if branch:
        w.append("branch_name = ?"); p.append(branch)
    if theme:
        w.append("theme = ?"); p.append(theme)
    rows = [dict(r) for r in con.execute(
        f"SELECT * FROM feedback_action WHERE {' AND '.join(w)} "
        f"ORDER BY CASE status WHEN 'doing' THEN 0 WHEN 'open' THEN 1 WHEN 'done' THEN 2 ELSE 3 END, "
        f"COALESCE(due, '9999') ASC, id DESC", p)]
    return [_row(con, r, today) for r in rows]


@router.get("/api/feedback/actions")
def feedback_actions(request: Request, status: Optional[str] = Query(None), branch: Optional[str] = Query(None),
                     theme: Optional[str] = Query(None)):
    today = _today()
    branch = scope_branch(request) or branch
    with get_db() as con:
        _ensure(con)
        items = _list(con, today, status, branch, theme)
        con.commit()
    counts = {"open": 0, "doing": 0, "done": 0, "dropped": 0, "overdue": 0, "better": 0, "same": 0, "worse": 0, "no_data": 0}
    for a in items:
        counts[a["status"]] = counts.get(a["status"], 0) + 1
        if a["overdue"]:
            counts["overdue"] += 1
        if a.get("result"):
            counts[a["result"]] = counts.get(a["result"], 0) + 1
    return {"actions": items, "counts": counts, "today": today, "window_days": WINDOW_DAYS}


@router.post("/api/feedback/actions", dependencies=[Depends(require_write_token)])
async def feedback_action_create(request: Request):
    body = await request.json()
    title = str(body.get("title") or "").strip()[:200]
    if not title:
        raise HTTPException(400, "ใส่ชื่องานก่อน")
    branch = str(body.get("branch") or "").strip()[:80] or None
    if branch == NO_BRANCH:                  # the tab's placeholder for voices without a branch
        branch = None
    branch = scope_branch(request) or branch
    theme = str(body.get("theme") or "").strip()[:80] or None
    today = _today()
    with get_db() as con:
        _ensure(con)
        base = _measure(con, branch, theme, _shift(today, -WINDOW_DAYS), _shift(today, -1))
        cur = con.execute(
            "INSERT INTO feedback_action(created_at, created_by, branch_name, theme, title, detail, owner, due, status, from_id, "
            "baseline_from, baseline_to, baseline_total, baseline_n, baseline_neg, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (today, _who(request, body), branch, theme, title, str(body.get("detail") or "")[:2000] or None,
             str(body.get("owner") or "").strip()[:60] or None, str(body.get("due") or "")[:10] or None, "open",
             int(body["from_id"]) if str(body.get("from_id") or "").isdigit() else None,
             base["from"], base["to"], base["total"], base["n"], base["neg"], _thai_now()))
        con.commit()
        row = dict(con.execute("SELECT * FROM feedback_action WHERE id=?", (cur.lastrowid,)).fetchone())
        return _row(con, row, today)


@router.post("/api/feedback/actions/{aid}", dependencies=[Depends(require_write_token)])
async def feedback_action_update(aid: int, request: Request):
    body = await request.json()
    today = _today()
    with get_db() as con:
        _ensure(con)
        row = con.execute("SELECT * FROM feedback_action WHERE id=?", (aid,)).fetchone()
        if not row:
            raise HTTPException(404, "ไม่พบงาน")
        scope = scope_branch(request)
        if scope and (row["branch_name"] or "") != scope:
            raise HTTPException(403, "งานนี้อยู่นอกสาขาของคุณ")
        sets, vals = ["updated_at=?"], [_thai_now()]
        for k in ("title", "detail", "owner", "due", "note"):
            if k in body:
                v = str(body.get(k) or "").strip()
                sets.append(f"{k}=?"); vals.append((v[:10] if k == "due" else v[:2000]) or None)
        if "status" in body:
            st = str(body.get("status") or "")
            if st not in STATUSES:
                raise HTTPException(400, "status")
            sets.append("status=?"); vals.append(st)
            if st == "done":
                if row["status"] != "done":
                    sets.append("done_at=?"); vals.append(str(body.get("done_at") or today)[:10])
            else:                               # reopened / dropped: the after-window is void
                sets += ["done_at=NULL", "after_from=NULL", "after_to=NULL", "after_total=NULL", "after_n=NULL",
                         "after_neg=NULL", "result=NULL", "verified_at=NULL"]
        if "title" in body and not (str(body.get("title") or "").strip()):
            raise HTTPException(400, "ใส่ชื่องานก่อน")
        vals.append(aid)
        con.execute(f"UPDATE feedback_action SET {', '.join(sets)} WHERE id=?", vals)
        con.commit()
        row = dict(con.execute("SELECT * FROM feedback_action WHERE id=?", (aid,)).fetchone())
        out = _row(con, row, today)
        con.commit()
        return out


# ── the digest: a window in numbers, against the window before ───────────────
def _branch_names(con, group: Optional[str]) -> Optional[list]:
    if not group:
        return None
    return [b["name"] for b in branch_rows(con) if (b.get("branch_type") or "") == group]


def _rows_between(con, d_from: str, d_to: str, branch=None, names=None) -> list:
    w, p = ["rated_at >= ?", "rated_at <= ?"], [d_from, d_to]
    if branch:
        w.append("branch_name = ?"); p.append(branch)
    elif names is not None:
        if not names:
            return []
        w.append(f"branch_name IN ({','.join('?' * len(names))})"); p.extend(names)
    return [dict(r) for r in con.execute(f"SELECT {_ROW_COLS} FROM customer_feedback WHERE {' AND '.join(w)}", p)]


def compute_digest(con, days: int = 7, today: str = None, branch: str = None, group: str = None,
                   start: str = None, end: str = None, prev_start: str = None, prev_end: str = None) -> dict:
    """The voices of [start..end] (default: the last `days` days ending today)
    against the window before, plus what is waiting now and how the fix loop
    went. Used by the weekly Telegram, the Management Report §12 and the API."""
    _ensure(con)
    today = today or _today()
    end = end or today
    start = start or _shift(end, -(days - 1))
    span = (_d(end) - _d(start)).days + 1
    if prev_start is None or prev_end is None:
        prev_end, prev_start = _shift(start, -1), _shift(start, -span)
    names = _branch_names(con, group) if (group and not branch) else None
    cur = _aggregate(_rows_between(con, start, end, branch, names), today)
    prev = _aggregate(_rows_between(con, prev_start, prev_end, branch, names), today)
    now = _aggregate(_rows_between(con, _shift(today, -90), today, branch, names), today)   # what waits right now
    prev_themes = {t["theme"]: t["n"] for t in prev["themes"]}
    themes = [{"theme": t["theme"], "n": t["n"], "neg": t["neg"], "prev_n": prev_themes.get(t["theme"], 0)}
              for t in cur["themes"][:6]]
    eligible = [b for b in cur["branches"] if b["n"] >= 3]
    watch = [{"branch": b["branch"], "n": b["n"], "neg": b["neg"], "neg_pct": b["neg_pct"], "top_theme": b.get("top_theme") or ""}
             for b in sorted(eligible, key=lambda b: (-b["neg_pct"], -b["neg"])) if b["neg"]][:3]
    praised = [{"branch": b["branch"], "n": b["n"], "avg_rating": b["avg_rating"]}
               for b in sorted([b for b in eligible if b["avg_rating"] is not None], key=lambda b: -b["avg_rating"])][:3]
    # who answers how fast: per branch, the answerable voices of the window and the median days to the answer
    by_branch = {}
    for r in _rows_between(con, start, end, branch, names):
        by_branch.setdefault(r.get("branch_name") or "", []).append(r)
    response_by_branch = []
    for bname, rs in by_branch.items():
        resp = _aggregate(rs, today)["response"]
        if bname and resp.get("answerable"):
            response_by_branch.append({"branch": bname, "answerable": resp["answerable"], "answered": resp["answered"],
                                       "rate": resp["rate"], "median_days": resp.get("median_days")})
    response_by_branch.sort(key=lambda x: ((x["rate"] or 0), -(x["median_days"] or 0), x["branch"]))
    survey_rows = [r for r in _rows_between(con, start, end, branch, names) if (r.get("kind") or "review") == "survey"]
    survey_rated = [r["rating"] for r in survey_rows if r.get("rating") is not None]
    survey_prev = sum(1 for r in _rows_between(con, prev_start, prev_end, branch, names) if (r.get("kind") or "review") == "survey")
    # the fix loop, in scope
    aw, ap = ["1=1"], []
    if branch:
        aw.append("branch_name = ?"); ap.append(branch)
    elif names is not None:
        aw.append(f"(branch_name IS NULL OR branch_name IN ({','.join('?' * len(names))}))" if names else "branch_name IS NULL"); ap.extend(names)
    acts = [_row(con, dict(r), today) for r in con.execute(f"SELECT * FROM feedback_action WHERE {' AND '.join(aw)}", ap)]
    a_out = {"open": 0, "doing": 0, "overdue": 0, "done": 0, "better": 0, "same": 0, "worse": 0, "no_data": 0, "items": []}
    for a in acts:
        if a["status"] in ("open", "doing"):
            a_out[a["status"]] += 1
            if a["overdue"]:
                a_out["overdue"] += 1
        if a["status"] == "done" and a.get("done_at") and start <= a["done_at"] <= end:
            a_out["done"] += 1
        if a.get("result") and a.get("verified_at") and start <= a["verified_at"] <= end:
            a_out[a["result"]] += 1
        if a["status"] == "done" and (a.get("result") or (a.get("done_at") and start <= a["done_at"] <= end)):
            a_out["items"].append({"id": a["id"], "title": a["title"], "branch": a.get("branch_name") or "", "theme": a.get("theme") or "",
                                   "owner": a.get("owner") or "", "result": a.get("result"), "before_rate": a["baseline_rate"],
                                   "after_rate": a["after_rate"], "days_left": a["after_days_left"]})
    a_out["items"] = a_out["items"][:8]
    return {"range": [start, end], "prev_range": [prev_start, prev_end], "days": span,
            "voices": cur["total"], "prev_voices": prev["total"],
            "neg_pct": cur["neg_pct"], "prev_neg_pct": prev["neg_pct"] if prev["total"] else None,
            "avg_rating": cur["avg_rating"], "prev_avg_rating": prev["avg_rating"],
            "positives": cur["sentiment"].get("pos", 0), "negatives": cur["sentiment"].get("neg", 0),
            "response": cur["response"], "prev_response_rate": prev["response"]["rate"],
            "unreplied": now["unreplied"], "sla_breached": now["sla_breached"], "flagged": now["flagged"],
            "severe": cur.get("severe", 0), "staff": cur.get("staff", [])[:6],
            "themes": themes, "branches_watch": watch, "branches_praised": praised,
            "response_by_branch": response_by_branch[:8],
            "survey": {"n": len(survey_rows), "prev_n": survey_prev,
                       "avg_rating": round(sum(survey_rated) / len(survey_rated), 2) if survey_rated else None},
            "actions": a_out}


@router.get("/api/feedback/digest")
def feedback_digest(request: Request, days: int = Query(7, ge=1, le=92), branch: Optional[str] = Query(None),
                    group: Optional[str] = Query(None), today: Optional[str] = Query(None)):
    """`today` pins the window (YYYY-MM-DD) — for reproducible reads; default = the Thai calendar day."""
    scope = scope_branch(request)
    if scope:
        branch, group = scope, None
    with get_db() as con:
        out = compute_digest(con, days=days, branch=branch, group=group, today=(today or "")[:10] or None)
        con.commit()
    return out


__all__ = ["router", "compute_digest", "WINDOW_DAYS", "STATUSES", "RESULTS"]
