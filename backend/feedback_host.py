# -*- coding: utf-8 -*-
"""The host contract of the 💬 customer-voice module (E7 of the expansion plan).

Everything the module needs from the application it lives in goes through
this one file, so porting the module to another app means re-implementing
these few names — nothing inside routers/feedback*.py, routers/survey.py,
feedback_nlp.py, feedback_sources/ or fetch_feedback.py imports the host
directly (backend/test_feedback_kit.py enforces that boundary).

    get_db()                  context manager → sqlite3 connection with row_factory = Row
    require_write_token       FastAPI dependency that gates mutating calls (may be a no-op)
    scope_branch(request)     the branch a login is confined to, or None
    who(request, body)        the acting user's name for audit fields ('local' when auth is off)
    branch_rows(con)          [{branch_id, name, branch_type, active}] — the branches the module may name
    menu_names(con)           the dish names the analyser matches mentions against (may be [])
    thai_today() / thai_now() the business calendar day / timestamp (UTC+7 here)
    sales_available(con)      True when the bill tables E2 reads (sales_transaction, sale_bill_item) exist

docs/FEEDBACK_PORTING_KIT.md describes the rest of the wiring (tables to
preserve, login exemptions, RBAC path rule, the tab and its JS).
"""
import datetime
import sqlite3

import auth as _auth
from core import get_db, require_write_token  # noqa: F401  (re-exported for the module)

__all__ = ["get_db", "require_write_token", "scope_branch", "who", "branch_rows", "menu_names",
           "thai_today", "thai_now", "sales_available", "CONTRACT"]

CONTRACT = ("get_db", "require_write_token", "scope_branch", "who", "branch_rows", "menu_names",
            "thai_today", "thai_now", "sales_available")


def scope_branch(request):
    """The branch this login is confined to (E4), or None (the whole chain / auth off)."""
    return _auth.scope_branch(request) if request is not None else None


def who(request, body: dict = None) -> str:
    """The login name when the site enforces login; the client's `by` (or 'local') otherwise."""
    if request is not None and _auth.auth_enabled():
        payload = _auth.parse_token(request.cookies.get(_auth.SESSION_COOKIE, "")) or {}
        if payload.get("u"):
            return str(payload["u"])[:60]
    return str((body or {}).get("by") or "local")[:60]


def branch_rows(con) -> list:
    try:
        return [{"branch_id": r["branch_id"], "name": r["name"], "branch_type": r["branch_type"] if "branch_type" in r.keys() else None,
                 "active": bool(r["active"]) if "active" in r.keys() and r["active"] is not None else True}
                for r in con.execute("SELECT * FROM branches WHERE name != 'ALL' ORDER BY branch_id")]
    except sqlite3.OperationalError:
        return []


def menu_names(con) -> list:
    try:
        return [r[0] for r in con.execute("SELECT description FROM skus WHERE description IS NOT NULL")]
    except sqlite3.OperationalError:
        return []


def thai_today() -> str:
    return (datetime.datetime.utcnow() + datetime.timedelta(hours=7)).strftime("%Y-%m-%d")


def thai_now() -> str:
    return (datetime.datetime.utcnow() + datetime.timedelta(hours=7)).strftime("%Y-%m-%d %H:%M:%S")


def sales_available(con) -> bool:
    have = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    return "sales_transaction" in have and "sale_bill_item" in have
