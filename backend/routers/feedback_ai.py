# -*- coding: utf-8 -*-
"""AI เชิงลึก — E5 of the expansion plan: the weekly narrative and "ask the
data" over the voices. Both work without ANTHROPIC_API_KEY (a rule-based Thai
narrative; a keyword search) and get Claude when the key is set; every answer
says which engine wrote it.
  GET  /api/feedback/narrative?days=7[&branch=][&today=]   3 short Thai paragraphs + 3 actions
  POST /api/feedback/ask {question, days, branch}           an answer with the voices it cites
Branch-scoped logins are confined to their branch here too.
"""
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request

import feedback_nlp as N
from feedback_host import get_db, scope_branch
from routers.feedback_actions import _branch_names, _rows_between, _shift, compute_digest

router = APIRouter()

ASK_MAX_ROWS = 150


@router.get("/api/feedback/narrative")
def feedback_narrative(request: Request, days: int = Query(7, ge=1, le=92), branch: Optional[str] = Query(None),
                       group: Optional[str] = Query(None), today: Optional[str] = Query(None)):
    scope = scope_branch(request)
    if scope:
        branch, group = scope, None
    with get_db() as con:
        d = compute_digest(con, days=days, branch=branch, group=group, today=(today or "")[:10] or None)
        con.commit()
    text, engine = N.narrative(d)
    return {"range": d["range"], "branch": branch, "engine": engine, **text}


@router.post("/api/feedback/ask")
async def feedback_ask(request: Request):
    body = await request.json()
    q = str(body.get("question") or "").strip()[:300]
    if not q:
        raise HTTPException(400, "พิมพ์คำถามก่อน")
    try:
        days = max(1, min(int(body.get("days") or 30), 366))
    except (TypeError, ValueError):
        days = 30
    branch = str(body.get("branch") or "").strip() or None
    group = str(body.get("group") or "").strip() or None
    scope = scope_branch(request)
    if scope:
        branch, group = scope, None
    with get_db() as con:
        d = compute_digest(con, days=days, branch=branch, group=group)
        names = _branch_names(con, group) if (group and not branch) else None
        rows = _rows_between(con, _shift(d["range"][1], -(days - 1)), d["range"][1], branch, names)
        con.commit()
    rows.sort(key=lambda r: (r.get("rated_at") or ""), reverse=True)
    out, engine = N.ask(q, rows[:ASK_MAX_ROWS * 4], d)
    return {"question": q, "range": d["range"], "branch": branch, "engine": engine, **out}
