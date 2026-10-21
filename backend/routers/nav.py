# -*- coding: utf-8 -*-
"""GET /api/nav/badges (PR-D4, owner 2026-10-21) - the task counts behind the red numbers of the department menu.

Every signed-in role may read it (auth.PUBLIC_PREFIXES): it returns only the counts of the caller's own tasks, built from the items of
หน้างาน (bom_center/nav_badges.py), and an empty `pages` for a role that cannot read หน้างาน. A login pinned to a branch gets that
branch's counts only. Cached per user for 60 s with core.ttl_cache; a bom-center write drops it (the cached function's name starts with `bc_`).
"""
from fastapi import APIRouter, Request

import auth as _auth
from core import get_db, ttl_cache
from bom_center import nav_badges as _nb
from bom_center.schema import ensure as _ensure

router = APIRouter()


@ttl_cache(ttl=60, stale=0)
def bc_nav_badges_for(user: str, role: str, branch: str, mods: str):
    """The cache key is (user, role, branch, modules): the answer is a pure function of them and of the database."""
    modules = set(filter(None, mods.split(",")))
    with get_db() as con:
        _ensure(con)
        if not _auth.path_allowed("GET", "/api/bom-center/todo", modules):
            return {"pages": {}, "at": _nb.now_bkk().isoformat(timespec="seconds")}
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
    return bc_nav_badges_for(user=user, role=role, branch=branch, mods=",".join(sorted(modules)))
