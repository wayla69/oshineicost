# -*- coding: utf-8 -*-
"""GET /api/nav/badges (PR-D4, owner 2026-10-21) - the task counts behind the red numbers of the department menu.

Every signed-in role may read it (auth.PUBLIC_PREFIXES): it returns only the counts of the caller's own tasks, built from the items of
หน้างาน (bom_center/nav_badges.py), and an empty `pages` for a role that cannot read หน้างาน. A login pinned to a branch gets that
branch's counts only. Cached per user for 60 s with core.ttl_cache; a bom-center write drops it (the cached function's name starts with `bc_`).
"""
import json

from fastapi import APIRouter, Request

import auth as _auth
from core import get_db, ttl_cache
from bom_center import nav_badges as _nb
from bom_center.schema import ensure as _ensure
from . import franchise_msgs as _fm
from . import feedback as _fb
from bom_center import dept_hero as _dh

router = APIRouter()


@ttl_cache(ttl=60, stale=0)
def bc_nav_badges_for(user: str, role: str, branch: str, mods: str):
    """The cache key is (user, role, branch, modules): the answer is a pure function of them and of the database."""
    modules = set(filter(None, mods.split(",")))
    with get_db() as con:
        _ensure(con)
        if not _auth.path_allowed("GET", "/api/bom-center/todo", modules):
            return {"pages": {}, "total": 0, "at": _nb.now_bkk().isoformat(timespec="seconds")}
        return _nb.build(con, role or None, modules, branch or None)


def _identity(request: Request):
    """(user, role, modules) of the signed-in account - the live role, as main.enforce_auth reads it. Auth off: the manager's view."""
    if not _auth.auth_enabled():
        return "", "", set(_auth.MODULES)
    payload = _auth.parse_token(request.cookies.get(_auth.SESSION_COOKIE, "")) or {}
    user = str(payload.get("u") or "")
    try:
        with get_db() as con:
            row = _auth.get_user(con, user)
    except Exception:
        row = None
    if not row or not row["active"]:
        return user, "", set()
    return user, row["role"], _auth.modules_for_role(row["role"])


@router.get("/api/nav/badges")
def nav_badges(request: Request):
    user, role, modules = _identity(request)
    branch = _auth.scope_branch(request) or ""
    out = bc_nav_badges_for(user=user, role=role, branch=branch, mods=",".join(sorted(modules)))
    # PR-F4: the unread franchise message threads, beside the tasks (not cached: one COUNT, and a reply must show at once).
    # {"franchise": n} for the owner's หน้าแรก, {"fmsg": n} for head office's inbox tile. `pages` / `total` stay the open TASKS
    # (bom_center/nav_badges.mine_open, the figure of หน้างาน): a message is not a task, so it never adds to them.
    try:
        with get_db() as con:
            extra = _fm.badge_counts(con, role if _auth.auth_enabled() else "management", modules, branch or None)
    except Exception:       # pragma: no cover - a badge never breaks the menu
        extra = {}
    # 2026-10-31 (phone review item 4): การตลาด has no หน้างาน; its department hero is the queue of เสียงลูกค้า (feedback.reply_counts,
    # the page's own rule). Not cached: one indexed scan of the unanswered rows, and an answer must leave the count at once.
    hero = None
    if _auth.auth_enabled() and role == "marketing" and "customer.feedback" in modules:
        try:
            with get_db() as con:
                n, late = _fb.reply_counts(con)
            hero = _dh.marketing(n, late, _fb.SLA_DAYS)
        except Exception:   # pragma: no cover - the hero never breaks the menu
            hero = None
    if not extra and not hero:
        return out
    if not isinstance(out, dict):           # core.ttl_cache hands back the cached JSON bytes as a Response
        out = json.loads(bytes(out.body).decode("utf-8"))
    out = dict(out)
    if extra:
        out["messages"] = extra
    if hero:
        out["hero"] = hero
    return out
