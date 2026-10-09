"""
Oshinei Sales & Cost Analysis  -  FastAPI backend
Run: uvicorn backend.main:app --reload --port 8000
  or from this folder: uvicorn main:app --reload --port 8000
"""

import sqlite3
import tempfile
import hashlib
import threading
from contextlib import closing
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, Query, HTTPException, UploadFile, File, Header, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse

try:                                   # works as `backend.main:app`
    from . import auth as _auth
    from . import month_summary as _month_summary
except ImportError:                     # …and as `main:app` from inside backend/
    import auth as _auth                # role-based authentication (backend/auth.py)
    import month_summary as _month_summary   # sales_month_summary self-heal (backend/month_summary.py)


import json as _json
from fastapi.responses import Response as _Response


# Fly imports this file as `backend.main` (see start.sh); locally and in tests it is
# top-level `main`. Sibling modules (rules, month_summary, auth, routers) resolve
# by plain name only in the second layout, so make the first one match — the
# alternative is a try/except around every sibling import. 2026-09-04: a bare
# `from rules import …` took production down for eight minutes.
HERE = Path(__file__).parent.resolve()
import sys as _sys_path_fix
if str(HERE) not in _sys_path_fix.path:
    _sys_path_fix.path.insert(0, str(HERE))
# Everything the routers share now lives in core.py (see its docstring). Every
# name is re-exported here so main.X keeps resolving for the code and tests that
# still spell it that way. To point the app at another database assign
# core.DB_PATH — main.DB_PATH is a copy.
from core import (  # noqa: F401
    BOM_TYPE_BENCHMARKS, COVERAGE_MIN_RATIO, CostEntry, DB_PATH, HERE, PRICE_AS_COST_HI,
    PRICE_AS_COST_LO, _CACHE_TTL, _FOOD_CATEGORY_FILTER, _FOOD_SKU_WHITELIST, _FOOD_SKU_WHITELIST_SQL, _INGREDIENT_FAMILIES,
    _NONFOOD_CATEGORY_KEYWORDS, _PRAGMAS, _REV_VIEWS, _RWLock, _accepted_tokens, _apply_incoming_live,
    _apply_period_filter, _bom_cogs_expr, _bom_cost_period_filters, _bom_costed_rev_expr, _build_sku_effective_cost, _cache_key,
    _cache_lock, _cache_stats, _cached_fn_names, _carry_forward_and_verify, _carry_forward_preserved, _cogs_case,
    _conn_reg, _conn_reg_lock, _costed_rev_case, _db_gen, _db_rw, _eff_month_range,
    _effective_cost_subquery, _ensure_rev_views, _group_filter, _ingredient_family, _iso_week_sql, _maybe_swap_incoming_db,
    _month_range_filter, _new_conn, _next_period_month, _ols_regression, _partial_months, _preserved_counts,
    _profit_filters, _resp_cache, _service_charge_map, _tls, _token_accepted, _tx_filters,
    _txn_fallback_counts, _unified_cogs_expr, _variant_filter, _ym_parts, ex_vat, get_db,
    invalidate_cache, is_price_as_cost, require_write_token, ttl_cache, warm_cache_async,
)
# DB location is overridable so the database can live on a mounted persistent
# volume in hosted deployments (e.g. Fly.io: OSHINEI_DB=/data/oshinei.db).
# Defaults to the in-repo path for local development.
import os as _os_db


# Skip the startup DB swap during tests / tooling (set OSHINEI_NO_SWAP=1).
# Importing this module then has no filesystem side effects.
if _os_db.environ.get("OSHINEI_NO_SWAP") != "1":
    _maybe_swap_incoming_db()
STATIC  = HERE.parent / "vendor"
PWA     = HERE.parent / "pwa"          # web app manifest + icons (a phone keeps the app on its home screen)
DOCS    = HERE.parent / "docs"
INDEX   = HERE.parent / "index.html"   # canonical (Phase 17d-fix 2026-05-28: was 'Oshinei Dashboard.html')

# ---------------------------------------------------------------------------
# VAT — single source of truth. Easy POS uses 7% VAT.
# Revenue is ALWAYS ex-VAT (rule: "VAT is never revenue"). To convert an
# inc-VAT total to ex-VAT, divide by VAT_DIVISOR. SQL queries embed the literal
# `1.07` for performance/readability; this constant is the canonical reference
# and is enforced by test_invariants (per-head/ticket must be ex-VAT).
# ---------------------------------------------------------------------------
# VAT, tier codes and the COGS expression are defined ONCE in rules.py so the
# live sync and the standalone generators state them the same way the app does.
from rules import (VAT_RATE, VAT_DIVISOR, net_revenue_row, net_revenue_sum,  # noqa: F401
                   scope_filter, cogs_expr as _rules_cogs_expr,
                   raw_unit_cost_sql as _raw_unit_cost_sql, effective_unit_cost_sql,
                   EASY_TIER_CODES as _EASY_TIER_CODES, EASY_TIER_CODES_SQL as _EASY_TIER_CODES_SQL,
                   LEGACY_TIER_CODES as _LEGACY_TIER_CODES, ALL_TIER_CODES as _ALL_TIER_CODES,
                   ALL_TIER_CODES_SQL as _ALL_TIER_CODES_SQL)

# main.py is the compatibility facade: everything the routers share lives in
# core.py and rules.py and is re-exported here so `main.X` keeps resolving for the
# scripts and tests that spell it that way. Listing the names in __all__ is what
# tells a static checker the re-exports are on purpose.
__all__ = [
    "BOM_TYPE_BENCHMARKS",
    "COVERAGE_MIN_RATIO",
    "CostEntry",
    "DB_PATH",
    "HERE",
    "PRICE_AS_COST_HI",
    "PRICE_AS_COST_LO",
    "VAT_DIVISOR",
    "VAT_RATE",
    "_ALL_TIER_CODES",
    "_ALL_TIER_CODES_SQL",
    "_CACHE_TTL",
    "_EASY_TIER_CODES",
    "_EASY_TIER_CODES_SQL",
    "_FOOD_CATEGORY_FILTER",
    "_FOOD_SKU_WHITELIST",
    "_FOOD_SKU_WHITELIST_SQL",
    "_INGREDIENT_FAMILIES",
    "_LEGACY_TIER_CODES",
    "_NONFOOD_CATEGORY_KEYWORDS",
    "_PRAGMAS",
    "_REV_VIEWS",
    "_RWLock",
    "_accepted_tokens",
    "_apply_incoming_live",
    "_apply_period_filter",
    "_bom_cogs_expr",
    "_bom_cost_period_filters",
    "_bom_costed_rev_expr",
    "_build_sku_effective_cost",
    "_cache_key",
    "_cache_lock",
    "_cache_stats",
    "_cached_fn_names",
    "_carry_forward_and_verify",
    "_carry_forward_preserved",
    "_cogs_case",
    "_conn_reg",
    "_conn_reg_lock",
    "_costed_rev_case",
    "_db_gen",
    "_db_rw",
    "_eff_month_range",
    "_effective_cost_subquery",
    "_ensure_rev_views",
    "_group_filter",
    "_ingredient_family",
    "_iso_week_sql",
    "_maybe_swap_incoming_db",
    "_month_range_filter",
    "_new_conn",
    "_next_period_month",
    "_ols_regression",
    "_partial_months",
    "_preserved_counts",
    "_profit_filters",
    "_raw_unit_cost_sql",
    "_resp_cache",
    "_rules_cogs_expr",
    "_service_charge_map",
    "_tls",
    "_token_accepted",
    "_tx_filters",
    "_txn_fallback_counts",
    "_unified_cogs_expr",
    "_variant_filter",
    "_ym_parts",
    "effective_unit_cost_sql",
    "ex_vat",
    "get_db",
    "invalidate_cache",
    "is_price_as_cost",
    # cost_sources re-exports — tests and scripts still reach them through main
    "GRAND_MENU_COST_FILE", "GRAND_MENU_ALIAS_FILE", "RECIPE_FILE",
    "ESTIMATED_COST_FILE", "_COST_PATCHES",
    "net_revenue_row",
    "net_revenue_sum",
    "require_write_token",
    "scope_filter",
    "ttl_cache",
    "report_management",   # router internal re-exported for back-compat (tests import it through main)
    "report_management_xlsx",   # router internal re-exported for back-compat (tests import it through main)
    "_bom_unit_cost_sql",   # router internal re-exported for back-compat (tests import it through main)
    "analysis_sph",   # router internal re-exported for back-compat (tests import it through main)
    "analysis_forecast",   # router internal re-exported for back-compat (tests import it through main)
    "analysis_forecast_branches",   # router internal re-exported for back-compat (tests import it through main)
    "revenue_forecast",   # router internal re-exported for back-compat (tests import it through main)
    "_mtd_window",   # router internal re-exported for back-compat (tests import it through main)
    "_month_add",   # router internal re-exported for back-compat (tests import it through main)
    "_menucmp_agg",   # router internal re-exported for back-compat (tests import it through main)
    "get_cost_detail",   # router internal re-exported for back-compat (tests import it through main)
    "branch_bom_analysis",   # router internal re-exported for back-compat (tests import it through main)
    "buffet_per_bill",   # router internal re-exported for back-compat (tests import it through main)
]


app = FastAPI(title="Oshinei Dashboard API")

# The SPA is served same-origin from this app (GET /), so it needs no CORS at all.
# We allow only explicit localhost origins for local dev tooling. NEVER use "*"/"null"
# here: with no auth layer, a wildcard lets any website issue cross-origin writes
# (POST/PUT/DELETE cost & settings). Override via OSHINEI_CORS_ORIGINS (comma-separated).
import os as _os
_cors_env = _os.environ.get("OSHINEI_CORS_ORIGINS", "").strip()
_cors_origins = (
    [o.strip() for o in _cors_env.split(",") if o.strip()]
    if _cors_env else
    [f"http://{host}:{port}"
     for host in ("localhost", "127.0.0.1")
     for port in ("8000", "8001")]
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["*"],
)
# Compress responses (esp. the ~1.3 MB index.html and JSON API payloads). Browsers
# that send Accept-Encoding: gzip get a ~5× smaller transfer; biggest win on the
# Fly.io cold start. minimum_size skips tiny bodies where framing overhead dominates.
from fastapi.middleware.gzip import GZipMiddleware
# compresslevel=6: ~same ratio as the default 9 at a fraction of the CPU — matters
# because every cached-response hit is re-compressed on the 1-CPU host.
app.add_middleware(GZipMiddleware, minimum_size=1000, compresslevel=6)

# ---------------------------------------------------------------------------
# Auth enforcement middleware
# ---------------------------------------------------------------------------
# Env-gated (OSHINEI_AUTH): when unset the dashboard behaves exactly as before
# (no login, every endpoint open) so local dev + the test-suite are unaffected.
# When set, every /api/* request must carry a valid session cookie and the
# user's role must grant the module the path maps to (see backend/auth.py).
# /docs/* (generated business reports — cost figures, internal plans) is gated
# by the SAME session cookie: any logged-in ACTIVE user may read them (no
# role-module mapping — reports are linked from the manual for every role).
_DOCS_401_HTML = (
    "<!doctype html><meta charset='utf-8'>"
    "<body style='font-family:sans-serif;padding:3rem;text-align:center'>"
    "<h3>กรุณาเข้าสู่ระบบก่อนเปิดเอกสาร (login required)</h3>"
    "<p><a href='/'>ไปหน้าเข้าสู่ระบบ / open the dashboard</a> "
    "แล้วกลับมาเปิดลิงก์นี้อีกครั้ง</p></body>")

@app.middleware("http")
async def enforce_auth(request: Request, call_next):
    if not _auth.auth_enabled():
        return await call_next(request)
    path = request.url.path
    # /docs/ui-kit/ stays OPEN: index.html's <head> loads its design tokens
    # (tokens.css) as the SPA's single :root variable source — gating it breaks
    # the styling of the LOGIN page itself. It is CSS only, no business data.
    docs_req = (path == "/docs" or path.startswith("/docs/")) \
        and not path.startswith("/docs/ui-kit/")
    # Fail CLOSED: auth is on but no real signing secret is configured → every
    # cookie would be forgeable with the source-tree dev key. Refuse all API
    # and docs traffic (the SPA shell still loads so the operator sees the 503).
    if (path.startswith("/api/") or docs_req) and not _auth.secret_configured():
        return JSONResponse(
            {"detail": "Auth misconfigured: set OSHINEI_AUTH_SECRET."}, status_code=503)
    # Only gate the API + /docs. The SPA shell, static assets and login itself
    # stay open so an unauthenticated browser can load the login form.
    if not (path.startswith("/api/") or docs_req) or path.startswith("/api/auth/login") \
            or path.startswith("/api/auth/me") or path.startswith("/api/auth/logout") \
            or path.startswith("/api/analysis/health") \
            or path.startswith("/api/_admin/seed-db") \
            or path.startswith("/api/_admin/export-state")             or path.startswith("/api/_admin/cost-overrides")             or path.startswith("/api/_admin/task-health") \
            or path.startswith("/api/_admin/bc-sku-cost-month") \
            or path.startswith("/api/_admin/backup/") \
            or path.startswith("/api/feedback/webhook/") \
            or path.startswith("/api/feedback/survey/"):
        # …and so are guests' QR-survey answers (/api/feedback/survey/{code}): the branch
        # code in the URL is the only credential a guest has; routers/survey.py rate-limits
        # and bot-guards it. The trailing slash keeps /api/feedback/survey-links gated.
        # …and so is the Meta webhook receiver (/api/feedback/webhook/meta): Meta
        # calls it without a session; it verifies its own HMAC signature / verify token
        # (routers/feedback.py). The trailing slash keeps /api/feedback/webhook-events gated.
        # seed-db is a headless machine-to-machine endpoint with its OWN token auth
        # (X-Seed-Token header, returns 404 on mismatch). It must bypass the login middleware,
        # else enabling OSHINEI_AUTH silently breaks the hosted data-refresh push.
        return await call_next(request)
    def _deny():
        # a human opening a report link directly gets a pointer to the login
        # page instead of a bare JSON blob; API callers keep the JSON contract
        if docs_req:
            return _Response(content=_DOCS_401_HTML, media_type="text/html",
                             status_code=401)
        return JSONResponse({"detail": "Authentication required."}, status_code=401)
    payload = _auth.parse_token(request.cookies.get(_auth.SESSION_COOKIE, ""))
    if not payload:
        return _deny()
    # Re-read the LIVE account so a role change / deactivation / deletion takes
    # effect immediately instead of lingering until the 12h cookie expires.
    # Cheap at this scale (indexed lookup on a ~12-row table, served from cache).
    try:
        with get_db() as con:
            row = _auth.get_user(con, payload.get("u", ""))
    except Exception:
        row = None
    if not row or not row["active"]:
        return _deny()
    if docs_req:                                   # any active user may read docs
        return await call_next(request)
    mods = _auth.modules_for_role(row["role"])     # live role, not the cookie's
    if not _auth.path_allowed(request.method, path, mods):
        # X-Denied: role — the page must not mistake this for a missing write token
        # (core.require_write_token marks its own 403 "token").
        return JSONResponse(
            {"detail": "บทบาทของคุณไม่มีสิทธิ์เข้าถึงส่วนนี้ (forbidden).", "code": "role"},
            status_code=403, headers={"X-Denied": "role"})
    return await call_next(request)

# ---------------------------------------------------------------------------
# Usage telemetry  (lean-overhaul Phase 0 — docs/LEAN_OVERHAUL_PLAN.md)
# ---------------------------------------------------------------------------
# Counts endpoint hits and tab opens so the Phase-2 keep/merge/kill decisions
# run on real usage, not guesses. Lives in a SEPARATE sqlite file next to the
# main DB (usage.db) because the daily MSSQL sync / data-refresh replace the
# main DB file wholesale — telemetry must survive those swaps. One UPSERTed
# row per (day, kind, name), so the file stays tiny forever. Every write is
# best-effort: telemetry must never break or slow a real request.

def _usage_db_path() -> Path:
    # Resolved at call time (not import) so the test-suite's DB_PATH rebind
    # sends telemetry to the temp dir instead of the real backend/usage.db.
    return Path(str(DB_PATH)).parent / "usage.db"

def _usage_bump(kind: str, name: str, user: str = "") -> None:
    try:
        con = sqlite3.connect(str(_usage_db_path()), timeout=5)
        try:
            con.execute(
                "CREATE TABLE IF NOT EXISTS usage_daily("
                " day TEXT NOT NULL, kind TEXT NOT NULL, name TEXT NOT NULL,"
                " hits INTEGER NOT NULL DEFAULT 0, last_user TEXT DEFAULT '',"
                " PRIMARY KEY(day, kind, name))")
            con.execute(
                # date('now','+7 hours') = Thai calendar day (host clock is UTC on Fly)
                "INSERT INTO usage_daily(day, kind, name, hits, last_user)"
                " VALUES (date('now','+7 hours'), ?, ?, 1, ?)"
                " ON CONFLICT(day, kind, name) DO UPDATE SET"
                " hits = hits + 1, last_user = excluded.last_user",
                (kind, name[:120], (user or "")[:40]))
            # the same count per USER (2026-09-05): who uses what, for the adoption
            # view — usage_daily kept only the last user of each row. '' = no login
            # (auth off locally, or a machine caller without a session cookie).
            con.execute(
                "CREATE TABLE IF NOT EXISTS usage_user_daily("
                " day TEXT NOT NULL, user TEXT NOT NULL DEFAULT '', kind TEXT NOT NULL, name TEXT NOT NULL,"
                " hits INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(day, user, kind, name))")
            con.execute(
                "INSERT INTO usage_user_daily(day, user, kind, name, hits)"
                " VALUES (date('now','+7 hours'), ?, ?, ?, 1)"
                " ON CONFLICT(day, user, kind, name) DO UPDATE SET hits = hits + 1",
                ((user or "")[:40], kind, name[:120]))
            con.commit()
        finally:
            con.close()
    except Exception:
        pass  # best-effort by design

def _usage_user(request: Request) -> str:
    try:
        payload = _auth.parse_token(request.cookies.get(_auth.SESSION_COOKIE, ""))
        return (payload or {}).get("u", "")
    except Exception:
        return ""

@app.middleware("http")
async def usage_counter(request: Request, call_next):
    response = await call_next(request)
    try:
        path = request.url.path
        if (path.startswith("/api/") and not path.startswith("/api/usage")
                and response.status_code < 400):
            route = request.scope.get("route")
            name = getattr(route, "path", None) or path
            _usage_bump("api", f"{request.method} {name}", _usage_user(request))
            if name == "/api/home/brief":          # someone opened the app: Monday → this week's adoption digest
                _maybe_weekly_adoption_digest()
            if name in ("/api/auth/me", "/api/home/brief", "/api/bom-center/todo"):   # someone's day starts
                _maybe_supply_reminder()                                          # → the order-day reminder
    except Exception:
        pass
    return response

@app.post("/api/usage/tab")
async def usage_tab(request: Request):
    """switchTab() beacon: {"tab": name}. Accepts sendBeacon's text/plain body.
    Deliberately NOT write-token gated — it records nothing but a counter."""
    tab = ""
    try:
        raw = await request.body()
        tab = str((_json.loads(raw or b"{}") or {}).get("tab", ""))[:60]
    except Exception:
        pass
    if tab:
        _usage_bump("tab", tab, _usage_user(request))
    return {"ok": bool(tab)}

@app.get("/api/usage/summary")
def usage_summary(days: int = Query(30, ge=1, le=365)):
    """Hit totals per tab / endpoint over the last N Thai days (Settings →
    การใช้งานจริง). Empty lists until any traffic is recorded."""
    out: dict = {"days": days, "tabs": [], "api": []}
    try:
        con = sqlite3.connect(str(_usage_db_path()), timeout=5)
        con.row_factory = sqlite3.Row
        try:
            for kind, key in (("tab", "tabs"), ("api", "api")):
                rows = con.execute(
                    "SELECT name, SUM(hits) AS hits,"
                    " COUNT(DISTINCT day) AS days_seen, MAX(day) AS last_seen"
                    " FROM usage_daily"
                    " WHERE kind = ? AND day >= date('now','+7 hours', ?)"
                    " GROUP BY name ORDER BY hits DESC",
                    (kind, f"-{int(days)} day")).fetchall()
                out[key] = [dict(r) for r in rows]
        finally:
            con.close()
    except Exception:
        pass
    return out


_MACHINE_API = ("/api/_admin/", "/api/usage", "/api/auth/", "/api/feedback/webhook", "/api/feedback/sync-log",
                "/api/feedback/reply-result")


def _usage_people(days: int) -> dict:
    """The adoption view (Settings → การใช้งานจริง → การเข้าใช้รายคน): every
    account with its last login, logins and failures in the window, the days it
    opened the dashboard, tab opens and its three most-opened tabs. Status:
    'never' = no successful login on record (still on the shared default
    password), 'idle' = has logged in before but not opened anything in the
    window, 'active' = opened a tab or logged in within the window. Machine
    paths (sync push, backup export, auth) are left out of the per-user API
    count so a person's row is what THEY did."""
    out: dict = {"days": days, "users": [], "summary": {}}
    accounts: list = []
    auth: dict = {}
    with get_db() as con:
        try:
            rows = con.execute("SELECT username, role, active, must_change, COALESCE(branch, '') AS branch, created_at"
                               " FROM app_user ORDER BY role, username").fetchall()
        except sqlite3.OperationalError:
            rows = con.execute("SELECT username, role, active, must_change, '' AS branch, created_at"
                               " FROM app_user ORDER BY role, username").fetchall()
        accounts = [dict(r) for r in rows]
        try:
            for r in con.execute(
                    "SELECT by AS u, action, MAX(ts) AS last,"
                    " SUM(CASE WHEN ts >= datetime('now', ?) THEN 1 ELSE 0 END) AS n"
                    " FROM recon_audit_log WHERE entity = 'auth' AND action IN ('login', 'login_fail')"
                    " GROUP BY by, action", (f"-{int(days)} days",)):
                auth.setdefault(r["u"], {})[r["action"]] = {"last": r["last"], "n": r["n"] or 0}
        except sqlite3.OperationalError:
            pass
    usage: dict = {}
    top: dict = {}
    try:
        ucon = sqlite3.connect(str(_usage_db_path()), timeout=5)
        ucon.row_factory = sqlite3.Row
        try:
            if ucon.execute("SELECT 1 FROM sqlite_master WHERE name = 'usage_user_daily'").fetchone():
                since = f"-{int(days)} day"
                for r in ucon.execute(
                        "SELECT user, kind, SUM(hits) AS hits, COUNT(DISTINCT day) AS days_seen, MAX(day) AS last_seen"
                        " FROM usage_user_daily WHERE day >= date('now', '+7 hours', ?)"
                        " GROUP BY user, kind", (since,)):
                    usage.setdefault(r["user"], {})[r["kind"]] = dict(r)
                for r in ucon.execute(
                        "SELECT user, SUM(hits) AS hits FROM usage_user_daily"
                        " WHERE kind = 'api' AND day >= date('now', '+7 hours', ?)"
                        " AND (" + " AND ".join(f"name NOT LIKE '% {p}%'" for p in _MACHINE_API) + ")"
                        " GROUP BY user", (since,)):
                    usage.setdefault(r["user"], {})["api_human"] = r["hits"] or 0
                for r in ucon.execute(
                        "SELECT user, name, SUM(hits) AS h FROM usage_user_daily"
                        " WHERE kind = 'tab' AND day >= date('now', '+7 hours', ?)"
                        " GROUP BY user, name ORDER BY user, h DESC", (since,)):
                    if len(top.setdefault(r["user"], [])) < 3:
                        top[r["user"]].append({"tab": r["name"], "hits": r["h"]})
        finally:
            ucon.close()
    except Exception:
        pass

    def row(username, a):
        u = usage.get(username, {})
        tab = u.get("tab", {})
        logins = auth.get(username, {}).get("login", {})
        fails = auth.get(username, {}).get("login_fail", {})
        tab_hits = int(tab.get("hits") or 0)
        days_seen = int(tab.get("days_seen") or 0)
        if not logins.get("last") and a.get("must_change", 0):
            status = "never"
        elif tab_hits or int(logins.get("n") or 0):
            status = "active"
        else:
            status = "idle"
        return {"username": username, "role": a.get("role", ""), "branch": a.get("branch", "") or "",
                "active": int(a.get("active", 1) or 0), "must_change": int(a.get("must_change", 0) or 0),
                "created_at": a.get("created_at", ""),
                "last_login": logins.get("last"), "logins": int(logins.get("n") or 0), "failed": int(fails.get("n") or 0),
                "tab_hits": tab_hits, "days_seen": days_seen, "last_seen": tab.get("last_seen"),
                "api_hits": int(u.get("api_human") or 0), "top_tabs": top.get(username, []), "status": status}
    users = [row(a["username"], a) for a in accounts]
    known = {a["username"] for a in accounts}
    for u in sorted(usage):                       # activity by a name that is not an account: '' (no login) or a removed user
        if u not in known and (usage[u].get("tab") or usage[u].get("api_human")):
            r = row(u, {"role": "", "must_change": 0, "active": 1})
            r["username"] = u or "(ไม่ระบุ — ไม่มี login)"
            r["status"] = "anonymous"
            users.append(r)
    out["users"] = users
    out["summary"] = {"accounts": len(accounts),
                      "active": sum(1 for x in users if x["status"] == "active"),
                      "idle": sum(1 for x in users if x["status"] == "idle"),
                      "never": sum(1 for x in users if x["status"] == "never")}
    return out


def _usage_admin(request: Request) -> None:
    """Admin-gated like /api/auth/users (per-person login times are personal
    data); a no-op when auth is off."""
    if __package__:
        from .routers.auth_api import _require_admin
    else:
        from routers.auth_api import _require_admin
    _require_admin(request)


@app.get("/api/usage/users")
def usage_users(request: Request, days: int = Query(30, ge=1, le=365)):
    """See _usage_people."""
    _usage_admin(request)
    return _usage_people(days)


@app.get("/api/usage/me")
def usage_me(request: Request):
    """The caller's own footprint — first_time = has never opened a tab, which is
    when the role start card shows (js: maybeShowStartCard). Anonymous ('')
    when auth is off, so the card never shows locally by itself."""
    user = _usage_user(request)
    hits, days_seen = 0, 0
    if user:
        try:
            con = sqlite3.connect(str(_usage_db_path()), timeout=5)
            try:
                if con.execute("SELECT 1 FROM sqlite_master WHERE name = 'usage_user_daily'").fetchone():
                    r = con.execute("SELECT COALESCE(SUM(hits), 0), COUNT(DISTINCT day) FROM usage_user_daily"
                                    " WHERE user = ? AND kind = 'tab'", (user,)).fetchone()
                    hits, days_seen = int(r[0] or 0), int(r[1] or 0)
            finally:
                con.close()
        except Exception:
            pass
    role = ""
    try:
        payload = _auth.parse_token(request.cookies.get(_auth.SESSION_COOKIE, ""))
        role = (payload or {}).get("r", "")
    except Exception:
        pass
    return {"username": user, "role": role, "tab_hits": hits, "days_seen": days_seen,
            "first_time": bool(user) and hits == 0}


# ── the weekly adoption digest ───────────────────────────────────────────────
# Every Monday (Thai clock), on the first request that opens the app, the
# people view is summarised — who has never logged in, who has been away more
# than ADOPTION_AWAY_DAYS — stored in usage.db (survives the daily DB push) for
# the home banner, and sent to Telegram when OSHINEI_TG_TOKEN / _CHAT_ID exist.
ADOPTION_AWAY_DAYS = 14
APP_URL = "https://oshinei-dashboard.fly.dev"


def _thai_now():
    from datetime import timedelta as _td
    return datetime.utcnow() + _td(hours=7)


def _usage_meta_get(key: str):
    try:
        con = sqlite3.connect(str(_usage_db_path()), timeout=5)
        try:
            if not con.execute("SELECT 1 FROM sqlite_master WHERE name = 'usage_meta'").fetchone():
                return None
            r = con.execute("SELECT value FROM usage_meta WHERE key = ?", (key,)).fetchone()
            return r[0] if r else None
        finally:
            con.close()
    except Exception:
        return None


def _usage_meta_set(key: str, value: str) -> None:
    try:
        con = sqlite3.connect(str(_usage_db_path()), timeout=5)
        try:
            con.execute("CREATE TABLE IF NOT EXISTS usage_meta(key TEXT PRIMARY KEY, value TEXT)")
            con.execute("INSERT INTO usage_meta(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value))
            con.commit()
        finally:
            con.close()
    except Exception:
        pass


def _iso_week(now) -> str:
    y, w, _ = now.isocalendar()
    return f"{y}-W{w:02d}"


def _adoption_lists(people: dict, now=None, away_days: int = ADOPTION_AWAY_DAYS) -> dict:
    """never = no successful login on record; away = has logged in before but
    neither logged in nor opened a tab for `away_days`; active = the rest that
    used the app in the window."""
    from datetime import timedelta as _td
    now = now or _thai_now()
    cutoff = (now - _td(days=away_days)).strftime("%Y-%m-%d %H:%M:%S")
    never, away, active = [], [], []
    for u in people["users"]:
        if u["status"] == "anonymous":
            continue
        if u["status"] == "never":
            never.append(u)
        elif (u.get("last_login") or "") < cutoff and (u.get("last_seen") or "") < cutoff[:10]:
            away.append(u)
        elif u["status"] == "active":
            active.append(u)
    return {"never": never, "away": away, "active": active}


def _adoption_digest_text(people: dict, lists: dict, week: str) -> str:
    s = people["summary"]
    L = [f"<b>สรุปการเข้าใช้แดชบอร์ด</b> สัปดาห์ {week} (นับ {people['days']} วัน)",
         f"ใช้งานอยู่ {s['active']} · ไม่ได้ใช้ในช่วงนี้ {s['idle']} · ยังไม่เคยเข้า {s['never']} · จาก {s['accounts']} บัญชี"]
    if lists["never"]:
        L.append(f"✗ <b>ยังไม่เคยเข้าใช้</b> ({len(lists['never'])}): " + ", ".join(f"{u['username']} ({u['role']})" for u in lists["never"]))
    if lists["away"]:
        L.append(f"<b>หายไปเกิน {ADOPTION_AWAY_DAYS} วัน</b> ({len(lists['away'])}): "
                 + ", ".join(f"{u['username']} — ล่าสุด {(u.get('last_login') or '')[:10] or '—'}" for u in lists["away"]))
    if lists["active"]:
        L.append("ใช้งานอยู่: " + ", ".join(f"{u['username']} ({u['days_seen']} วัน)" for u in lists["active"]))
    L.append(f"รายละเอียด: {APP_URL} → Settings → การใช้งานจริง")
    return "\n".join(L)


def _adoption_digest_build(days: int = 30, now=None, send=None, force: bool = False):
    """Build this week's digest, store it, send it to Telegram when configured.
    Once per ISO week unless `force`. Returns the record, or None when this
    week's already exists."""
    import notify as _notify
    now = now or _thai_now()
    week = _iso_week(now)
    if not force and _usage_meta_get("adoption_digest_week") == week:
        return None
    people = _usage_people(days)
    lists = _adoption_lists(people, now)
    text = _adoption_digest_text(people, lists, week)
    configured = bool(_notify.telegram_config()[0] and _notify.telegram_config()[1])
    sent = False
    if configured:
        try:
            sent = bool((send or _notify.send_telegram)(text))
        except Exception as e:
            print(f"[adoption] telegram failed: {e}")
    rec = {"week": week, "generated_at": now.strftime("%Y-%m-%d %H:%M"), "text": text, "sent": sent,
           "telegram_configured": configured, "summary": people["summary"],
           "never": [u["username"] for u in lists["never"]], "away": [u["username"] for u in lists["away"]],
           "active": [u["username"] for u in lists["active"]]}
    _usage_meta_set("adoption_digest_week", week)
    _usage_meta_set("adoption_digest", _json.dumps(rec, ensure_ascii=False))
    return rec


def _maybe_weekly_adoption_digest(now=None, send=None):
    """Monday on the Thai clock, first request of the week → build + store (+ send). Best-effort."""
    try:
        now = now or _thai_now()
        if now.weekday() != 0:
            return None
        return _adoption_digest_build(now=now, send=send)
    except Exception as e:
        print(f"[adoption] weekly digest failed: {e}")
        return None


# ── order-day reminders for the pilot (วัตถุดิบและใบสั่งซื้อ) ─────────────────
def _supply_reminder_send(text: str, send=None) -> tuple:
    """(telegram_configured, sent) — the same delivery as the adoption digest."""
    import notify as _notify
    configured = bool(_notify.telegram_config()[0] and _notify.telegram_config()[1])
    sent = False
    if configured:
        try:
            sent = bool((send or _notify.send_telegram)(text))
        except Exception as e:
            print(f"[supply] telegram failed: {e}")
    return configured, sent


def _maybe_supply_reminder(now=None, send=None):
    """The first API request after 07:00 (Thai clock) each day builds the day's
    reminder (the pilot branches' duties and the queue, from the same facts as
    หน้างาน), stores it and sends it to Telegram when the bot is configured; the
    first request after 13:15 (the branch order cutoff) adds a note of the branches
    that missed it and the ใบสั่งซื้อ waiting in the warehouse queue.
    Best-effort and once a day each; a quiet day is stored but not sent."""
    try:
        from bom_center import reminders as _rem
        now = now or _thai_now()
        if now.hour < 7:
            return None
        day = now.strftime("%Y-%m-%d")
        out = None
        if _usage_meta_get("supply_reminder_day") != day:
            _usage_meta_set("supply_reminder_day", day)          # claim first: a burst of requests sends once
            with get_db() as con:
                rec = _rem.morning(con, today=now.date(), app_url=APP_URL)
            configured, sent = _supply_reminder_send(rec["text"], send) if rec["any_work"] else (False, False)
            rec.update({"generated_at": now.strftime("%Y-%m-%d %H:%M"), "telegram_configured": configured, "sent": sent})
            _usage_meta_set("supply_reminder", _json.dumps(rec, ensure_ascii=False))
            out = rec
        if (now.hour, now.minute) >= (13, 15) and _usage_meta_get("supply_sla_day") != day:
            _usage_meta_set("supply_sla_day", day)
            from bom_center.clock import BKK as _BKK
            with get_db() as con:
                rec2 = _rem.cutoff(con, today=now.date(), app_url=APP_URL,
                                   now=now if now.tzinfo else now.replace(tzinfo=_BKK))
            if rec2:
                configured, sent = _supply_reminder_send(rec2["text"], send)
                rec2.update({"generated_at": now.strftime("%Y-%m-%d %H:%M"), "telegram_configured": configured, "sent": sent})
                _usage_meta_set("supply_sla", _json.dumps(rec2, ensure_ascii=False))
                out = out or rec2
        return out
    except Exception as e:
        print(f"[supply] reminder failed: {e}")
        return None


@app.get("/api/usage/adoption-digest")
def usage_adoption_digest(request: Request, days: int = Query(30, ge=1, le=365)):
    """The latest stored digest (home banner, Settings) plus a live preview."""
    _usage_admin(request)
    import notify as _notify
    now = _thai_now()
    people = _usage_people(days)
    lists = _adoption_lists(people, now)
    latest = _usage_meta_get("adoption_digest")
    tok, chat = _notify.telegram_config()
    return {"week": _iso_week(now), "latest": _json.loads(latest) if latest else None,
            "preview": _adoption_digest_text(people, lists, _iso_week(now)),
            "never": [u["username"] for u in lists["never"]], "away": [u["username"] for u in lists["away"]],
            "telegram_configured": bool(tok and chat)}


@app.post("/api/usage/adoption-digest/send")
def usage_adoption_digest_send(request: Request):
    """Build and store the digest now (and send it when Telegram is configured)."""
    _usage_admin(request)
    rec = _adoption_digest_build(force=True)
    return {"ok": True, **rec}


# ── scheduled-task health (the office PC's watchdog) ─────────────────────────
# backend/task_watchdog.py runs on the office PC, reads Task Scheduler and POSTs
# what it found. Kept in usage.db (survives the daily DB push) for the admins'
# home banner. The receive time is OURS, not the PC's: a report that stops
# arriving is itself the finding - the PC is off and nothing is syncing - and it
# is the one failure nothing on that PC can report.
TASK_HEALTH_SILENT_HOURS = 36
_TASK_PROBLEM_LEVELS = ("fail", "stale", "stuck", "missed")


@app.post("/api/_admin/task-health", include_in_schema=False)
async def _task_health_report(request: Request):
    """Machine-to-machine, seed token in the X-Seed-Token header (404 on mismatch,
    like seed-db); exempt from the login gate for the same reason seed-db is."""
    _seed_token_or_404(request.headers.get("X-Seed-Token", ""))
    raw = await request.body()
    if len(raw) > 64_000:
        raise HTTPException(status_code=413, detail="report too large")
    try:
        rep = _json.loads(raw.decode("utf-8"))
        if not isinstance(rep, dict) or not isinstance(rep.get("tasks"), list):
            raise ValueError
    except ValueError:
        raise HTTPException(status_code=400, detail="bad report")
    _usage_meta_set("task_health", _json.dumps(
        {"received_at": _thai_now().strftime("%Y-%m-%d %H:%M:%S"), "report": rep}, ensure_ascii=False))
    return {"ok": True}


def _task_health_view(now=None) -> dict:
    raw = _usage_meta_get("task_health")
    if not raw:
        return {"known": False}
    rec = _json.loads(raw)
    rep = rec.get("report") or {}
    now = now or _thai_now()
    age = (now - datetime.strptime(rec["received_at"], "%Y-%m-%d %H:%M:%S")).total_seconds() / 3600
    problems = [t for t in rep.get("tasks") or [] if t.get("level") in _TASK_PROBLEM_LEVELS]
    return {"known": True, "received_at": rec["received_at"], "age_hours": round(age, 1),
            "silent": age > TASK_HEALTH_SILENT_HOURS, "silent_after_hours": TASK_HEALTH_SILENT_HOURS,
            "host": rep.get("host"), "checked_at": rep.get("checked_at"), "signature": rep.get("signature") or "",
            "problems": problems, "tasks": rep.get("tasks") or []}


@app.get("/api/usage/task-health")
def usage_task_health(request: Request):
    """What the office PC's watchdog last reported, and how long ago (admins)."""
    _usage_admin(request)
    return _task_health_view()


# Serve vendor JS/CSS
if STATIC.exists():
    app.mount("/vendor", StaticFiles(directory=str(STATIC)), name="vendor")
if PWA.exists():
    app.mount("/pwa", StaticFiles(directory=str(PWA)), name="pwa")

# Serve generated reports / docs (HTML) so they open in-browser
if DOCS.exists():
    app.mount("/docs", StaticFiles(directory=str(DOCS), html=True), name="docs")


def _autoswap_poller():
    """Background daemon: apply a fresh `.incoming` to the running app, no restart."""
    import time as _t
    interval = int(_os_db.environ.get("OSHINEI_AUTOSWAP_SECS", "60"))
    while True:
        _t.sleep(interval)
        try:
            if Path(str(DB_PATH) + ".incoming").exists():
                if _apply_incoming_live():
                    # The staged DB carries whatever sku_effective_cost it was
                    # synced with (sync_mssql doesn't rebuild it) — flush the
                    # response cache AND force-rebuild the effective-cost lookup
                    # so post-swap COGS reflects the current sku_cost/BoM chain.
                    invalidate_cache()
                    # then recompute the most-requested reports in the background,
                    # one at a time, so nobody pays the cold query after a sync
                    warm_cache_async()
        except Exception as e:
            print(f"[hot-swap] poller error: {e}")


# ---------------------------------------------------------------------------
# Tier SKU codes to exclude from BoM cost (seat-tier pricing; included separately)
# Phase 18d 2026-05-28: added 'BUFFET' (plain, deprecated legacy code that still
# appears in some Easy bills). Was leaking ~825M phantom COGS at insight/MTD
# endpoints that referenced this constant. See _EASY_TIER_CODES at line ~5211
# for the canonical SQL constant used by all helper-based queries.
# ---------------------------------------------------------------------------
TIER_CODES: tuple = (
    'BUFFET', 'BUFFET350', 'BUFFET500', 'BUFFET590', 'BUFFET799',
    'BUFFETCHILD', 'B01', 'B02', 'B07', 'B11',
)


# ---------------------------------------------------------------------------
# Root  -  serve dashboard HTML (with content-hash cache-busting for /js/)
# ---------------------------------------------------------------------------
_JS_DIR = INDEX.parent / "js"

# Immutable-cache scheme (no build step): the shell is served no-cache and the
# server injects ?v=<md5-8> onto every /js/ script src at request time, so a
# slice URL changes IFF its content changes. The slice route then serves
# versioned URLs with a 1-year immutable Cache-Control — repeat page loads
# skip 9 revalidation round-trips entirely, while a deploy (new hash → new
# URL) still picks up fresh JS instantly. Hashes are cached per (path, mtime,
# size) so local edits re-hash automatically without a restart.
_js_hash_cache: dict = {}


def _js_hash(p) -> str:
    import hashlib
    try:
        st = p.stat()
        key = (str(p), st.st_mtime_ns, st.st_size)
        h = _js_hash_cache.get(key)
        if h is None:
            h = hashlib.md5(p.read_bytes()).hexdigest()[:8]
            _js_hash_cache.clear()          # old (path, mtime) keys are dead weight
            for q in _JS_DIR.glob("*.js"):  # rebuild the full set in one pass
                qs = q.stat()
                _js_hash_cache[(str(q), qs.st_mtime_ns, qs.st_size)] = \
                    hashlib.md5(q.read_bytes()).hexdigest()[:8]
            h = _js_hash_cache[key]
        return h
    except OSError:
        return "0"


def _versioned_index_html() -> str:
    import re as _re
    html = INDEX.read_text(encoding="utf-8")
    def sub(m):
        p = _JS_DIR / m.group(1)
        return f'src="/js/{m.group(1)}?v={_js_hash(p)}"' if p.exists() else m.group(0)
    html = _re.sub(r'src="/js/([0-9a-z-]+\.js)"', sub, html)
    # the lazily loaded slices carry the same content hash on their inert tags
    def sub_lazy(m):
        p = _JS_DIR / m.group(1)
        return f'data-lazy="/js/{m.group(1)}?v={_js_hash(p)}"' if p.exists() else m.group(0)
    html = _re.sub(r'data-lazy="/js/([0-9a-z-]+\.js)"', sub_lazy, html)
    return html.replace('<div id="tabsRoot"></div>',
                        f'<div id="tabsRoot" data-tabs-v="{_tabs_bundle()[1]}"></div>', 1)


@app.get("/", include_in_schema=False)
def root():
    if INDEX.exists():
        # no-cache: the shell must always be fresh — it carries the js hashes
        return _Response(content=_versioned_index_html(), media_type="text/html",
                         headers={"Cache-Control": "no-cache"})
    return {"message": "Oshinei API running. Dashboard file not found."}


_TABS_DIR = INDEX.parent / "tabs"
_tabs_cache = {"key": None, "html": "", "hash": ""}


def _tabs_bundle() -> tuple:
    """The 48 tab panels, concatenated in file order, with a content hash.

    Re-read when any fragment's mtime or size changes, so an edit shows up on
    the next request locally and a deploy is one fresh read. The shell carries
    the hash as data-tabs-v, so a stale cached shell fetches its own version."""
    files = sorted(_TABS_DIR.glob("*.html")) if _TABS_DIR.exists() else []
    key = tuple((f.name, f.stat().st_mtime_ns, f.stat().st_size) for f in files)
    if key != _tabs_cache["key"]:
        html = "".join(f.read_text(encoding="utf-8") for f in files)
        _tabs_cache.update(key=key, html=html,
                           hash=hashlib.sha1(html.encode("utf-8")).hexdigest()[:10])
    return _tabs_cache["html"], _tabs_cache["hash"]


@app.get("/tabs.html", include_in_schema=False)
def tabs_html(v: Optional[str] = Query(None)):
    html, h = _tabs_bundle()
    cache = "public, max-age=31536000, immutable" if v == h else "no-cache"
    return _Response(content=html, media_type="text/html; charset=utf-8",
                     headers={"Cache-Control": cache, "X-Tabs-Version": h})


_SW_FILE = INDEX.parent / "sw.js"


def _build_id() -> str:
    """One short hash over every js slice's hash + the tabs bundle: it changes whenever
    any of them does, so the service worker's shell cache is retired on a deploy."""
    parts = [_js_hash(p) for p in sorted(_JS_DIR.glob("*.js"))] if _JS_DIR.exists() else []
    try:
        parts.append(_tabs_bundle()[1])
    except Exception:
        pass
    return hashlib.md5("|".join(parts).encode("utf-8")).hexdigest()[:10]


@app.get("/sw.js", include_in_schema=False)
def service_worker():
    """The service worker (repo root sw.js) with __BUILD__ replaced by the deploy's
    content hash. no-cache so the browser re-checks it on every visit; served from
    the root so it controls the whole app."""
    if not _SW_FILE.exists():
        raise HTTPException(status_code=404, detail="not found")
    body = _SW_FILE.read_text(encoding="utf-8").replace("__BUILD__", _build_id())
    return _Response(content=body, media_type="application/javascript",
                     headers={"Cache-Control": "no-cache", "Service-Worker-Allowed": "/"})


@app.get("/js/{name}", include_in_schema=False)
def app_js_slice(name: str, v: Optional[str] = Query(None)):
    """One slice of the SPA JS (P2 refactor: app.js split into js/01…09,
    loaded sequentially by index.html — shared global scope, order matters).
    Requested WITH the current ?v=<content-hash> (as the served shell always
    does) → 1-year immutable cache: the URL changes whenever the content
    does, so stale JS is impossible and repeat loads skip revalidation.
    Requested without ?v (or with a stale hash) → no-cache, the safe
    pre-versioning behaviour. Name is whitelist-validated (no path parts)."""
    import re as _re
    if not _re.fullmatch(r"[0-9a-z][0-9a-z-]*\.js", name):
        raise HTTPException(status_code=404, detail="not found")
    p = _JS_DIR / name
    if p.exists() and p.parent == _JS_DIR:
        cache = ("public, max-age=31536000, immutable"
                 if v and v == _js_hash(p) else "no-cache")
        return FileResponse(str(p), media_type="application/javascript",
                            headers={"Cache-Control": cache})
    raise HTTPException(status_code=404, detail="not found")


@app.get("/app.js", include_in_schema=False)
def app_js():
    """Back-compat during the P2 rollout: a browser holding a CACHED shell
    still references /app.js — serve the concatenation of the js/ slices
    (sorted = load order) so it keeps working; the fresh shell loads the
    slices individually. Droppable once caches have cycled."""
    if _JS_DIR.exists():
        parts = [p.read_text(encoding="utf-8") for p in sorted(_JS_DIR.glob("*.js"))]
        if parts:
            return _Response(content="\n".join(parts),
                             media_type="application/javascript",
                             headers={"Cache-Control": "no-cache"})
    raise HTTPException(status_code=404, detail="app.js not found")


# ---------------------------------------------------------------------------
# DB refresh/seed over HTTPS (hosted deploys). Accepts a gzipped SQLite file —
# far more reliable than the WireGuard SFTP tunnel for large files. Used both to
# seed an empty volume and to refresh an existing one with newer data.
#
# Safe by design:
#   • token-gated — requires env OSHINEI_SEED_TOKEN set AND a matching X-Seed-Token
#     header. NEVER ?token=: uvicorn's access log prints the request line, so a token
#     in the query string landed in Fly's logs on every push (moved 2026-09-22)
#     (unset the Fly secret to disable the endpoint entirely → 404).
#   • the upload is STAGED to <DB_PATH>.incoming and validated (integrity_check
#     + non-empty sales_transaction); the live DB is never touched here.
#   • _maybe_swap_incoming_db() atomically swaps it in at the next startup, so a
#     bad/partial upload can never corrupt the running DB. Caller restarts the
#     machine after a successful upload (see backend/refresh_fly_db.py).
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# /api/_admin/export-state — the tables that live ONLY on Fly (core._PRESERVED_*:
# users, customer voices, survey links, actions, competitor snapshots, checklist
# ticks, recon uploads) as gzip'd NDJSON for the nightly off-site pull
# (backend/pull_fly_state.py, AUD-2). Same token gate as seed-db: OSHINEI_SEED_TOKEN
# unset → 404, wrong token → 404. Streams row by row, so memory stays flat.
# ---------------------------------------------------------------------------
# /api/_admin/cost-overrides — the costs confirmed in the app, for the SYNC to
# pull before it builds menu_fact_*: that build runs against a staging DB on
# another machine, which would otherwise never hear about a confirmation made
# here. Same token gate as export-state; a few dozen tiny rows, so plain JSON.
def _seed_token_or_404(x_seed_token: str = Header(None)) -> str:
    """The machine-to-machine gate shared by the /api/_admin endpoints: the seed token
    in the X-Seed-Token HEADER (never the URL - see the note above), 404 on anything
    else so the endpoints do not even admit to existing."""
    if not _accepted_tokens() or not _token_accepted(x_seed_token or ""):
        raise HTTPException(status_code=404, detail="not found")
    return x_seed_token


@app.get("/api/_admin/cost-overrides", include_in_schema=False)
def _cost_overrides(_tok: str = Depends(_seed_token_or_404)):
    import cost_sources as _csrc
    with get_db() as con:
        con.execute(_csrc.OVERRIDE_DDL)
        cols = [r[1] for r in con.execute("PRAGMA table_info(cost_override)")]
        rows = [list(r) for r in con.execute(f"SELECT {', '.join(cols)} FROM cost_override")]
    return {"columns": cols, "rows": rows}


@app.get("/api/_admin/bc-sku-cost-month", include_in_schema=False)
def _bc_sku_cost_month(_tok: str = Depends(_seed_token_or_404)):
    """The released costs per SKU per month (BoM Center cut-over), for the SYNC to
    price per-bill COGS and the tier fact tables the way the app prices COGS.
    Keyed by sku_code: sku ids are local to each database."""
    from bom_center import dated as _bcd
    with get_db() as con:
        rows = _bcd.export_rows(con)
    return {"columns": ["sku_code", "month", "unit_cost", "release_id", "built_at"], "rows": rows}


# /api/_admin/backup/owner-data.{sqlite,json} — the nightly off-site copy of the owner-data
# tables as a ready-to-open SQLite file (full DDL + indexes + rows; scripts/backup_owner_tables.py,
# docs/BACKUP_OWNER_DATA.md). The file holds password hashes, so the gate is the admin token
# ALONE and it fails closed: OSHINEI_SEED_TOKEN unset, missing or wrong -> 403, whatever the
# session (a login cookie, any role, never opens it - core.require_write_token's branch-session
# bypass and its "no token configured = open" dev mode are deliberately NOT used here).
# Header only (X-Seed-Token or X-Upload-Token); never the URL.
def _backup_token_or_403(x_seed_token: str = Header(None), x_upload_token: str = Header(None)) -> str:
    import hmac as _hmac
    tok = (x_seed_token or x_upload_token or "").strip()
    # The file carries password hashes, so only the owner's own token opens it: the bare token or the
    # one labelled "admin" (docs/TOKEN_ROTATION.md). The per-person tokens staff type into the
    # browser's "รหัส" button (Patcharee, Arnan, AR ...) are good for data edits, not for this.
    owner = {t for t, label in _accepted_tokens().items() if label.lower() in ("", "admin")}
    if not tok or not any(_hmac.compare_digest(tok, t) for t in owner):
        raise HTTPException(status_code=403, detail="A valid admin token is required.",
                            headers={"X-Denied": "token"})
    return tok


_backup_lock = threading.Lock()


def _build_owner_backup(tmp_dir: str) -> tuple:
    """Build the file inside tmp_dir (one build at a time: it copies every owner table).
    Returns (path, info, stamp)."""
    import datetime as _dt
    from core import export_owner_data as _export
    if not _backup_lock.acquire(blocking=False):
        raise HTTPException(status_code=429, detail="A backup export is already running; try again in a minute.")
    try:
        stamp = (_dt.datetime.utcnow() + _dt.timedelta(hours=7)).strftime("%Y%m%d_%H%M")      # Bangkok
        path = _os_db.path.join(tmp_dir, f"oshinei_owner_data_{stamp}.sqlite")
        info = _export(str(DB_PATH), path)
    finally:
        _backup_lock.release()
    if info["integrity"] != "ok":
        raise HTTPException(status_code=500, detail=f"backup integrity_check failed: {info['integrity']}")
    return path, info, stamp


@app.get("/api/_admin/backup/owner-data.sqlite", include_in_schema=False)
def _backup_owner_data(_tok: str = Depends(_backup_token_or_403)):
    import json as _json, shutil as _shutil, tempfile as _tempfile
    from fastapi.responses import FileResponse as _File
    from starlette.background import BackgroundTask as _Bg
    tmp = _tempfile.mkdtemp(prefix="owner_backup_")
    try:
        path, info, stamp = _build_owner_backup(tmp)
    except BaseException:
        _shutil.rmtree(tmp, ignore_errors=True)
        raise
    counts = {t["name"]: t["rows"] for t in info["tables"]}
    return _File(path, media_type="application/vnd.sqlite3",
                 headers={"Content-Disposition": f"attachment; filename=oshinei_owner_data_{stamp}.sqlite",
                          "X-Backup-Counts": _json.dumps(counts, separators=(",", ":")),    # the exact snapshot the file holds
                          "Cache-Control": "no-store"},
                 background=_Bg(_shutil.rmtree, tmp, True))


@app.get("/api/_admin/backup/owner-data.json", include_in_schema=False)
def _backup_owner_data_json(_tok: str = Depends(_backup_token_or_403)):
    """What the export holds right now - for monitoring and --dry-run. Builds the file in a
    temp dir and discards it, so the numbers are exactly what a download would contain."""
    import datetime as _dt, shutil as _shutil, tempfile as _tempfile
    tmp = _tempfile.mkdtemp(prefix="owner_backup_")
    try:
        _path, info, _stamp = _build_owner_backup(tmp)
    finally:
        _shutil.rmtree(tmp, ignore_errors=True)
    return {"tables": info["tables"], "bytes": info["bytes"],
            "rows": sum(t["rows"] for t in info["tables"]),
            "generated_at": (_dt.datetime.utcnow() + _dt.timedelta(hours=7)).strftime("%Y-%m-%d %H:%M:%S")}


@app.get("/api/_admin/export-state", include_in_schema=False)
def _export_state(_tok: str = Depends(_seed_token_or_404)):
    import base64 as _b64, datetime as _dt, json as _json, sqlite3 as _sq, zlib as _zlib
    from fastapi.responses import StreamingResponse as _Stream
    from core import _preserved_pred as _pp

    def _cell(v):
        return {"b64": _b64.b64encode(v).decode("ascii")} if isinstance(v, (bytes, bytearray, memoryview)) else v

    def gen():
        z = _zlib.compressobj(6, _zlib.DEFLATED, 31)          # 31 = gzip container
        con = _sq.connect(f"file:{DB_PATH}?mode=ro", uri=True)
        counts = {}
        try:
            from core import _MERGED_ROWS as _merged          # rows a person wrote on the site live only here too
            _also = " OR name IN (" + ",".join(f"'{t}'" for t in _merged) + ")" if _merged else ""
            names = [r[0] for r in con.execute(f"SELECT name FROM sqlite_master WHERE type='table' AND ({_pp('name')}{_also}) ORDER BY name")]
            for t in names:
                cols = [r[1] for r in con.execute(f"PRAGMA table_info([{t}])")]
                yield z.compress((_json.dumps({"table": t, "columns": cols}, ensure_ascii=False) + "\n").encode("utf-8"))
                n = 0
                for row in con.execute(f"SELECT {', '.join('[' + c + ']' for c in cols)} FROM [{t}]"):
                    n += 1
                    yield z.compress((_json.dumps({"t": t, "r": [_cell(v) for v in row]}, ensure_ascii=False) + "\n").encode("utf-8"))
                counts[t] = n
        finally:
            con.close()
        now = (_dt.datetime.utcnow() + _dt.timedelta(hours=7)).strftime("%Y-%m-%d %H:%M:%S")
        yield z.compress((_json.dumps({"done": True, "counts": counts, "exported_at": now}) + "\n").encode("utf-8"))
        yield z.flush()
    return _Stream(gen(), media_type="application/gzip",
                   headers={"Content-Disposition": "attachment; filename=fly_state.ndjson.gz", "Cache-Control": "no-store"})


@app.post("/api/_admin/seed-db", include_in_schema=False)
async def _seed_db(file: UploadFile = File(...), _tok: str = Depends(_seed_token_or_404)):
    import gzip, shutil, tempfile, os as _os2
    inc = str(DB_PATH) + ".incoming"
    # Decompress+validate into a .partial first, then atomically rename to the
    # .incoming name the hot-swap poller watches — as the LAST step. Otherwise the
    # poller can grab a half-written .incoming, or swap it out from under us before
    # we build the response (FileNotFoundError on the final stat → spurious 500
    # even though the data applied fine).
    stage = inc + ".partial"
    # Stream upload → temp gz, then stream-decompress into the .partial stage.
    with tempfile.NamedTemporaryFile(delete=False) as tmp:
        shutil.copyfileobj(file.file, tmp, 1024 * 1024)
        tmp_path = tmp.name
    try:
        # Cap the DECOMPRESSED size so a gzip-bomb can't fill the volume.
        max_bytes = 2 * 1024 * 1024 * 1024  # 2 GB — well above a real ~270 MB DB
        written = 0
        with gzip.open(tmp_path, "rb") as gz, open(stage, "wb") as out:
            while True:
                chunk = gz.read(1024 * 1024)
                if not chunk:
                    break
                written += len(chunk)
                if written > max_bytes:
                    raise ValueError("decompressed payload exceeds size limit")
                out.write(chunk)
    except Exception as e:
        for p in (tmp_path, stage):
            try: _os2.remove(p)
            except OSError: pass
        raise HTTPException(status_code=400, detail=f"decompress failed: {e}")
    finally:
        try: _os2.remove(tmp_path)
        except OSError: pass
    # Validate the .partial before we ever expose it to the swap poller.
    # closing() even on error: a leaked probe handle would follow the rename
    # below (the handle tracks the file, not the name) and keep the published
    # .incoming locked; on the 422 path it blocks the remove() → orphan .partial.
    try:
        with closing(sqlite3.connect(stage)) as chk:
            ok  = chk.execute("PRAGMA integrity_check").fetchone()[0]
            rows = chk.execute("SELECT COUNT(*) FROM sales_transaction").fetchone()[0]
    except Exception as e:
        ok, rows = f"error: {e}", 0
    if ok != "ok" or not rows:
        try: _os2.remove(stage)
        except OSError: pass
        raise HTTPException(status_code=422,
                            detail=f"staged DB rejected (integrity={ok}, rows={rows})")
    # Capture the size NOW (before the poller can swap the file away), then
    # publish atomically. The response uses the captured values — never re-stats.
    staged_bytes = _os2.path.getsize(stage)
    _os2.replace(stage, inc)   # atomic: poller only ever sees a complete, valid DB
    return {"ok": True, "staged_bytes": staged_bytes, "staged_rows": rows,
            "note": "restart the machine to apply (atomic swap at startup)"}


# ---------------------------------------------------------------------------
# /api/months
# ---------------------------------------------------------------------------
@app.get("/api/months")
def list_months(branch: Optional[str] = Query(None)):
    """
    All available YYYY-MM periods across sales_sku_monthly AND sales_month_summary.
    Optionally filtered by branch name.
    """
    with get_db() as con:
        if branch:
            rows = con.execute(
                """
                SELECT DISTINCT period_month FROM (
                  SELECT sm.period_month
                  FROM sales_sku_monthly sm
                  JOIN branches b ON b.branch_id = sm.branch_id
                  WHERE b.name = ?
                  UNION
                  SELECT sms.period_month
                  FROM sales_month_summary sms
                  JOIN branches b ON b.branch_id = sms.branch_id
                  WHERE b.name = ? AND b.name != 'ALL'
                )
                ORDER BY period_month
                """,
                (branch, branch),
            ).fetchall()
        else:
            rows = con.execute(
                """
                SELECT DISTINCT period_month FROM (
                  SELECT period_month FROM sales_sku_monthly
                  UNION
                  SELECT sms.period_month
                  FROM sales_month_summary sms
                  JOIN branches b ON b.branch_id = sms.branch_id
                  WHERE b.name != 'ALL'
                )
                ORDER BY period_month
                """
            ).fetchall()
    return [r["period_month"] for r in rows]


@app.get("/api/data-coverage")
@ttl_cache
def data_coverage():
    """Latest date the sales data actually covers — for the dashboard 'ข้อมูลถึง…'
    note. Flags when the latest month is missing its final calendar day (the known
    upstream Easy-export off-by-one; see upload_data._day_coverage)."""
    import calendar as _cal
    with get_db() as con:
        row = con.execute(
            "SELECT MAX(date(sold_at)) AS d FROM sales_transaction"
        ).fetchone()
    last_day = row["d"] if row and row["d"] else None
    last_month = last_day[:7] if last_day else None
    last_day_missing = None
    if last_day:
        y, mo, dd = (int(x) for x in last_day.split("-"))
        ndays = _cal.monthrange(y, mo)[1]
        last_day_missing = dd < ndays
    return {
        "last_date":        last_day,     # 'YYYY-MM-DD'
        "last_month":       last_month,   # 'YYYY-MM'
        "last_day_missing": last_day_missing,
    }


# ---------------------------------------------------------------------------
# /api/data-quality  — integrity findings the headline numbers never surface
# ---------------------------------------------------------------------------
@app.get("/api/data-quality")
@ttl_cache
def data_quality():
    """Read-only integrity report: gaps and malformed rows that quietly distort
    reports. It REPORTS rather than repairs, deliberately — these are real
    financial records (deleting them destroys history) and missing days can only
    come back from the POS source via a re-sync, never be invented here.

    Every finding carries the numbers needed to judge it plus a `benign` flag, so
    a known-by-design pattern is never dressed up as a problem."""
    import datetime as _dt
    out = {"generated": _dt.datetime.utcnow().isoformat(timespec="seconds") + "Z",
           "findings": []}
    with get_db() as con:
        def add(key, title, sev, count, detail, benign=False, rows=None):
            out["findings"].append({"key": key, "title": title, "severity": sev,
                                    "count": count, "detail": detail,
                                    "benign": benign, "rows": rows or []})

        # 1. Missing trading days INSIDE a branch's own active span. A branch that
        #    opened later (or closed) is not a gap — only holes between its own
        #    first and last sale count.
        gaps = []
        for sp in con.execute("""
                SELECT b.name br, MIN(date(t.sold_at)) lo, MAX(date(t.sold_at)) hi,
                       COUNT(DISTINCT date(t.sold_at)) days
                FROM sales_transaction t JOIN branches b ON b.branch_id=t.branch_id
                WHERE b.name!='ALL' GROUP BY b.name""").fetchall():
            if not sp["lo"]:
                continue
            lo = _dt.date.fromisoformat(sp["lo"]); hi = _dt.date.fromisoformat(sp["hi"])
            expected = (hi - lo).days + 1
            missing = expected - (sp["days"] or 0)
            if missing <= 0:
                continue
            have = {r[0] for r in con.execute(
                """SELECT DISTINCT date(t.sold_at) FROM sales_transaction t
                   JOIN branches b ON b.branch_id=t.branch_id WHERE b.name=?""",
                (sp["br"],))}
            miss = [(lo + _dt.timedelta(days=i)).isoformat() for i in range(expected)
                    if (lo + _dt.timedelta(days=i)).isoformat() not in have]
            gaps.append({"branch": sp["br"], "missing_days": missing, "dates": miss})

        # A day the branch was shut is not a lost day. branch_downtime is the only
        # place that knows the difference — the POS has no "closed today" record, so
        # without it a renovation and a broken sync look identical. Recorded days are
        # SPLIT OUT, never dropped: they keep their own finding so a wrong entry stays
        # visible instead of quietly deleting a real gap from the report.
        known = {}
        try:
            for r in con.execute("SELECT branch, date_from, date_to, reason, note "
                                 "FROM branch_downtime").fetchall():
                known.setdefault(r["branch"], []).append(
                    (r["date_from"], r["date_to"], r["reason"], r["note"]))
        except sqlite3.Error:
            known = {}          # table not created yet — report everything as unknown

        def _cover(branch, day):
            for f, t, reason, note in known.get(branch, ()):
                if f <= day <= t:
                    return reason, note
            return None

        explained, unexplained = [], []
        for g in gaps:
            hit = [(d, _cover(g["branch"], d)) for d in g["dates"]]
            ex = [d for d, c in hit if c]
            un = [d for d, c in hit if not c]
            if ex:
                reasons = sorted({c[0] for _, c in hit if c})
                explained.append({"branch": g["branch"], "missing_days": len(ex),
                                  "dates": ex[:20], "reason": ", ".join(reasons)})
            if un:
                unexplained.append({"branch": g["branch"], "missing_days": len(un),
                                    "dates": un[:20]})
        unexplained.sort(key=lambda g: -g["missing_days"])
        explained.sort(key=lambda g: -g["missing_days"])
        add("missing_days", "วันที่ไม่มีข้อมูล ระหว่างช่วงที่สาขาเปิดขายอยู่", "warn",
            sum(g["missing_days"] for g in unexplained),
            "ช่องว่างกลางคาบการขายของสาขานั้นเอง ที่ยังไม่มีการบันทึกสาเหตุไว้ — "
            "ตรวจก่อนว่าสาขาปิดจริงหรือไม่ (บันทึกได้ด้วย backend/mark_downtime.py) "
            "ถ้าไม่ใช่ กู้คืนได้ด้วยการ re-sync ช่วงวันนั้นจาก POS เท่านั้น ระบบจะไม่สร้างข้อมูลขึ้นเอง",
            rows=unexplained[:10])
        if explained:
            add("known_downtime", "วันที่ไม่มีข้อมูล — บันทึกสาเหตุไว้แล้ว", "info",
                sum(g["missing_days"] for g in explained),
                "สาขาปิดจริง/ยังไม่เปิดขายเต็มรูปแบบในวันเหล่านี้ จึงไม่ใช่ข้อมูลสูญหาย — "
                "แสดงไว้เพื่อให้ตรวจสอบย้อนหลังได้ว่าใครบันทึกและด้วยเหตุผลใด",
                benign=True, rows=explained[:10])

        # 2. Bills carrying revenue but zero covers — every per-head figure that
        #    divides by covers is guarded, but these bills still under-count pax.
        z = con.execute(f"""
            SELECT b.name br, COUNT(*) n, ROUND(SUM({net_revenue_row('t')})) rev
            FROM sales_transaction t JOIN branches b ON b.branch_id=t.branch_id
            WHERE COALESCE(t.customer_count,0)=0 AND t.total_inc_vat>0
            GROUP BY b.name ORDER BY n DESC""").fetchall()
        add("zero_covers", "บิลที่มียอดขาย แต่จำนวนลูกค้า = 0", "warn",
            sum(r["n"] for r in z),
            "ทำให้ยอดต่อหัวของสาขานั้นเพี้ยนเล็กน้อย (นับบิล แต่ไม่มีหัว) — แก้ที่การคีย์หน้าร้าน",
            rows=[dict(r) for r in z[:10]])

        # 3. Discount larger than the bill — any discount% computed as
        #    discount/total runs past 100% on these.
        d = con.execute("""
            SELECT b.name br, COUNT(*) n, ROUND(SUM(t.discount)) disc,
                   ROUND(SUM(t.total_inc_vat)) tot
            FROM sales_transaction t JOIN branches b ON b.branch_id=t.branch_id
            WHERE t.discount > t.total_inc_vat
            GROUP BY b.name ORDER BY n DESC""").fetchall()
        add("discount_gt_total", "บิลที่ส่วนลดมากกว่ายอดบิล", "high",
            sum(r["n"] for r in d),
            "% ส่วนลดที่คิดจาก discount ÷ total จะเกิน 100% — ตรวจว่าเป็นบิลยกเลิก/แจกฟรี "
            "ที่คีย์ผิดช่องหรือไม่",
            rows=[dict(r) for r in d[:10]])

        # 4. Items still selling without a name (the POS code shows as the dish).
        stub = con.execute("""
            SELECT COUNT(DISTINCT s.sku_id) n, ROUND(SUM(m.qty)) qty
            FROM v_sales_sku_monthly m JOIN skus s ON s.sku_id=m.sku_id
            WHERE s.description = s.sku_code""").fetchone()
        add("stub_names", "เมนูที่ยังไม่มีชื่อ (แสดงเป็นรหัส POS)", "warn",
            (stub["n"] or 0) if stub else 0,
            f"ขายไปแล้ว {(stub['qty'] or 0):,.0f} ชิ้น — ชื่อจะถูกเติมอัตโนมัติเมื่อ sync รอบถัดไปทำงาน")

        # 5. Dishes with sales but no unit cost — food-cost% is a floor while these exist.
        unc = con.execute(f"""
            SELECT COUNT(DISTINCT s.sku_id) n, ROUND(SUM(m.qty)) qty
            FROM v_sales_sku_monthly m JOIN skus s ON s.sku_id=m.sku_id
            WHERE s.sku_code NOT IN {_ALL_TIER_CODES_SQL}
              AND COALESCE((SELECT recipe_cost FROM sku_effective_cost WHERE sku_id=m.sku_id),0)=0
              AND COALESCE((SELECT NULLIF(unit_cost,0) FROM sku_cost WHERE sku_id=m.sku_id),0)=0
        """).fetchone()
        add("uncosted_skus", "เมนูที่ขายแล้ว แต่ยังไม่มีต้นทุน", "high",
            (unc["n"] or 0) if unc else 0,
            f"รวม {(unc['qty'] or 0):,.0f} ชิ้น — ทำให้ต้นทุนอาหารต่ำกว่าความจริง "
            f"เติมได้ที่ ตั้งค่า → Cost Management")

        # 6. Retired dishes never deactivated — cosmetic only, flagged benign.
        dead = con.execute("""
            SELECT COUNT(*) n FROM skus s
            WHERE COALESCE(s.active,1)=1
              AND (SELECT MAX(period_month) FROM sales_sku_monthly WHERE sku_id=s.sku_id)
                  < (SELECT MAX(period_month) FROM sales_sku_monthly)""").fetchone()
        add("stale_skus", "เมนูที่ไม่มียอดขายในเดือนล่าสุด แต่ยังเปิดใช้งานอยู่", "info",
            (dead["n"] or 0) if dead else 0,
            "เมนูเลิกขายที่ไม่ได้ปิดสถานะ — รายการเมนูยาวเกินจำเป็น ไม่กระทบตัวเลขการเงิน",
            benign=True)

    out["total"] = sum(f["count"] for f in out["findings"] if not f["benign"])
    return out


# ---------------------------------------------------------------------------
# /api/sales/monthly   -  monthly revenue trend
# ---------------------------------------------------------------------------
@app.get("/api/meta/data-window")
@ttl_cache
def data_window():
    """Data coverage window + which month is still PARTIAL.

    Reports must not compare a partial trailing month (e.g. data through Jun-03)
    against a full prior month — that yields a misleading ~-90% MoM. This tells the
    UI the last COMPLETE month (use it for headline/ranking comparisons) and the
    partial month (label it MTD / exclude from like-for-like)."""
    import calendar as _cal
    with get_db() as con:
        row = con.execute("SELECT MIN(sold_at) mn, MAX(sold_at) mx FROM sales_transaction").fetchone()
    mx = (row["mx"] or "")[:10] if row else ""
    mn = (row["mn"] or "")[:10] if row else ""
    last_complete = partial = None
    if mx:
        y, m, d = (int(x) for x in mx.split("-"))
        last_day = _cal.monthrange(y, m)[1]
        if d >= last_day:                       # max date is the month-end → month is complete
            last_complete = f"{y:04d}-{m:02d}"
        else:                                   # trailing month is partial
            partial = f"{y:04d}-{m:02d}"
            pm = m - 1 or 12; py = y if m > 1 else y - 1
            last_complete = f"{py:04d}-{pm:02d}"
    return {"min_date": mn, "max_date": mx, "max_month": mx[:7] if mx else None,
            "max_day": int(mx.split("-")[2]) if mx else None,
            "last_complete_month": last_complete, "partial_month": partial}


# ---------------------------------------------------------------------------
# /api/upload   -  ingest an Easy .txt file
# ---------------------------------------------------------------------------
# Max accepted upload size (anti disk-fill). Real Easy exports are < ~60 MB.
_MAX_UPLOAD_BYTES = 150 * 1024 * 1024

# NOTE: sync `def` (not async) on purpose — the body parses xlsx and writes
# SQLite synchronously, so Starlette runs it in a threadpool and it never blocks
# the event loop for other requests.
@app.post("/api/upload")
def upload_file(
    file: UploadFile = File(...),
    x_upload_token: str = Header(None),
):
    """Accept a multipart Easy data upload, ingest it, return a summary.

    AUTH: when OSHINEI_SEED_TOKEN is set (hosted deploys), a matching token is
    required in the `X-Upload-Token` header (never `?token=`: the access log
    prints the request line) — otherwise this
    public mutation could let anyone overwrite a month of sales data. When the
    env var is unset (local dev), uploads are allowed without a token.

    The DB is backed up before any write, and curated cost tables are verified
    unchanged afterwards (Easy SALES rows only; period+branch-scoped upsert).
    """
    import hmac
    from pathlib import Path as _Path

    # ── AUTH gate ──────────────────────────────────────────────────────────
    expected = _os_db.environ.get("OSHINEI_SEED_TOKEN")
    if expected:
        provided = x_upload_token or ""
        if not hmac.compare_digest(provided, expected):
            raise HTTPException(status_code=403, detail="A valid upload token is required.")

    # Use the basename only — file.filename is attacker-controlled and a value like
    # "..\\..\\x" would otherwise let the rename below write/delete outside the temp dir.
    filename = _Path(file.filename or "upload.txt").name or "upload.txt"
    suffix = _Path(filename).suffix or ".txt"

    tmp_path = None
    tmp_dir = None
    try:
        # Stream to disk with a hard size cap (anti disk-fill DoS).
        total = 0
        with tempfile.NamedTemporaryFile(
            delete=False, suffix=suffix, prefix="easy_upload_"
        ) as tmp:
            while True:
                chunk = file.file.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > _MAX_UPLOAD_BYTES:
                    tmp_path = _Path(tmp.name)
                    raise HTTPException(status_code=413, detail="File too large.")
                tmp.write(chunk)
            tmp_path = _Path(tmp.name)

        # Rename the temp file to match the original name so pattern matching
        # works. Guard: the target MUST stay inside the temp directory. A
        # per-upload unique DIRECTORY keeps concurrent same-name uploads safe
        # (fix C11) while preserving the exact filename — the previous
        # stem-prefix approach broke upload_data.classify(), whose
        # startswith("ES1_") checks never matched a prefixed name, so raw
        # .xlsx uploads were always rejected as unknown files.
        tmp_dir = _Path(tempfile.mkdtemp(prefix="easy_upload_"))
        named_path = tmp_dir / filename
        if named_path.resolve().parent != tmp_dir.resolve():
            raise HTTPException(status_code=400, detail="Invalid filename")
        tmp_path.rename(named_path)
        tmp_path = named_path

        import sys as _sys
        if str(HERE) not in _sys.path:
            _sys.path.insert(0, str(HERE))
        import upload_data

        is_raw = bool(upload_data.classify(tmp_path))

        # Validate the cleansed-raw file BEFORE the (expensive) backup, so a bad
        # file fails fast without copying the whole DB.
        if is_raw:
            with get_db() as con:
                upload_data.ingest_one(con, tmp_path, apply=False, allow_new_branch=False)

        # Back up the live DB before any write so a bad upload is recoverable.
        backup = upload_data.rr.safe_backup(DB_PATH)

        def _curated_snapshot(con):
            return {t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                    for t in upload_data.CURATED}

        # Two upload paths, one per format, sharing the SAME engines as the CLI:
        #  • cleansed flat raw .xlsx (ES1_*_raw / 23 ES_*_raw) → upload_data.ingest_one
        #    (period+branch-scoped upsert; preserves curated cost tables).
        #  • legacy Easy .txt (Type A/B)                       → ingest_easy.ingest_file.
        with get_db() as con:
            con.execute("PRAGMA journal_mode=WAL")
            pre = _curated_snapshot(con)
            if is_raw:
                # allow_new_branch=False keeps the duplicate-branch guard on.
                r = upload_data.ingest_one(con, tmp_path, apply=True, allow_new_branch=False)
                resp = {
                    "file":          filename,
                    "branch":        f"{r['branches']} branch(es)",
                    "period":        r["period"],
                    "rows_ingested": r["rows"],
                    "file_type":     r["kind"],
                }
            else:
                import ingest_easy
                result = ingest_easy.ingest_file(tmp_path, con)
                resp = {
                    "file":          filename,
                    "branch":        result["branch"],
                    "period":        result["period"],
                    "rows_ingested": result["rows_ingested"],
                    "file_type":     result["file_type"],
                }
            post = _curated_snapshot(con)

        if pre != post:
            # Loaders must never touch curated cost tables — fail loudly if they did.
            import sys as _sys
            print(f"UPLOAD ABORT — curated tables changed: {pre} -> {post}. "
                  f"Backup at {backup.name}", file=_sys.stderr)
            raise HTTPException(status_code=500,
                                detail="Upload changed protected cost tables and was rejected.")

        invalidate_cache()   # new data uploaded — clear cached analytics responses
        return resp

    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception:
        # Log full detail server-side; return a generic message to the client.
        import traceback, sys as _sys
        print("Ingest error:", traceback.format_exc(), file=_sys.stderr)
        raise HTTPException(status_code=500, detail="Ingest failed. See server log.")
    finally:
        if tmp_path and tmp_path.exists():
            try:
                tmp_path.unlink()
            except Exception:
                pass
        if tmp_dir and tmp_dir.exists():
            try:
                tmp_dir.rmdir()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# /api/market   -  external market & industry context for the Home brief.
# Two parts:
#   (1) CURATED, SOURCED figures (Thai macro + F&B + Japanese-buffet segment).
#       Hand-compiled from public reports — update MARKET_* + MARKET_AS_OF when
#       refreshing. These are static, so they never break the page.
#   (2) LIVE news-headline strip fetched server-side from Google News RSS,
#       cached with a TTL and degrading gracefully (status flag) so a slow or
#       blocked feed can never break the dashboard.
# stdlib only (urllib + xml.etree) — no new pip dependency. NOT wrapped in the
# 5-min response cache so a transient feed failure isn't cached as "unavailable".
# ---------------------------------------------------------------------------
MARKET_AS_OF = "2025 actuals + 2026F · refreshed 2026-06"   # vintage of curated data

MARKET_MACRO = [
    {"label": "GDP Growth (TH)",     "value": "+2.4%",  "year": "2025",     "trend": "down",
     "sub": "2026F ~+1.7–2.0% — slowing",             "source": "BoT / Min. of Finance"},
    {"label": "Headline Inflation",  "value": "~0%",    "year": "2025",     "trend": "flat",
     "sub": "2026F ~+1% — subdued",                   "source": "IMF / BoT"},
    {"label": "Consumer Confidence", "value": "50.6",   "year": "Apr 2026", "trend": "down",
     "sub": "lowest since Aug — demand soft",         "source": "UTCC"},
    {"label": "Foreign Tourists",    "value": "32.9M",  "year": "2025",     "trend": "down",
     "sub": "−7.2% YoY — first drop since COVID",      "source": "TAT"},
]

MARKET_INDUSTRY = [
    {"label": "TH Foodservice market",     "value": "$38.1B",
     "sub": "2026F · +7.6% YoY (CAGR ~7.6% to 2031)",          "source": "Mordor Intelligence"},
    {"label": "Restaurant sales growth",   "value": "+4–5%/yr",
     "sub": "2024–26 · restaurants = 93% of F&B income",       "source": "Krungsri Research"},
    {"label": "Japanese restaurants (TH)", "value": "5,916",
     "sub": "2024 census · +2.9% YoY (+165 outlets)",          "source": "JETRO Bangkok"},
    {"label": "Japanese segment growth",   "value": "+6.0%/yr",
     "sub": "$1.45B (2025) → $2.31B (2033) · buffet = price wars, thin margins", "source": "Phoenix Research"},
]

# Annual industry growth used as the benchmark line against the chain's own YoY.
MARKET_BENCHMARK = {
    "industry_growth_pct": 6.0,     # blended TH restaurant/foodservice growth (2026)
    "jp_segment_growth_pct": 6.0,   # Japanese-restaurant segment CAGR (2026–33)
    "label": "TH foodservice ~+7.6%/yr · restaurant ~+4–5% · Japanese ~+6%/yr",
}

MARKET_SOURCES = [
    {"name": "Bank of Thailand — Economic Outlook",
     "url": "https://www.bot.or.th/en/thai-economy/economic-outlook.html"},
    {"name": "IMF — Thailand 2025 Article IV",
     "url": "https://www.imf.org/en/news/articles/2026/02/13/pr26048-thailand-imf-executive-board-concludes-2025-article-iv-consultation-with-thailand"},
    {"name": "Mordor Intelligence — Thailand Foodservice Market",
     "url": "https://www.mordorintelligence.com/industry-reports/thailand-foodservice-market"},
    {"name": "Krungsri Research — F&B / Restaurant Outlook 2024-26",
     "url": "https://www.krungsri.com/en/research/industry/industry-outlook/services/food-beverages/io/io-food-beverage-restaurant-2024-2026"},
    {"name": "JETRO — Japanese restaurants in Thailand (≈5,916)",
     "url": "https://www.nationthailand.com/business/economy/40045018"},
    {"name": "Phoenix Research — Thailand Japanese Restaurant Market",
     "url": "https://www.pheonixresearch.com/food-beverages/foodservice-restaurant/market-report/thailand-japanese-restaurant-market/"},
]

_MARKET_NEWS_CACHE = {"ts": 0.0, "items": None}
_MARKET_NEWS_TTL = 3 * 3600   # 3 hours

def _fetch_market_news():
    """Fetch a few Thailand restaurant-industry headlines from Google News RSS.
    Returns (items, status) where status ∈ {'ok','stale','unavailable'}.
    Never raises — degrades gracefully so the Home page never breaks."""
    import time as _t
    now = _t.time()
    cache = _MARKET_NEWS_CACHE
    if cache["items"] is not None and (now - cache["ts"]) < _MARKET_NEWS_TTL:
        return cache["items"], "ok"
    try:
        import urllib.request as _u, urllib.parse as _up, xml.etree.ElementTree as _ET
        q = _up.quote('Thailand restaurant industry OR ร้านอาหาร')
        url = f"https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en"
        req = _u.Request(url, headers={"User-Agent": "Mozilla/5.0 (OshineiDashboard)"})
        with _u.urlopen(req, timeout=6) as resp:
            raw = resp.read()
        root = _ET.fromstring(raw)
        items = []
        for it in root.findall(".//item")[:6]:
            title = (it.findtext("title") or "").strip()
            link  = (it.findtext("link") or "").strip()
            pub   = (it.findtext("pubDate") or "").strip()
            src_el = it.find("source")
            src = (src_el.text.strip() if src_el is not None and src_el.text else "")
            if src and title.endswith(f" - {src}"):
                title = title[: -(len(src) + 3)].strip()
            if title and link:
                items.append({"title": title, "url": link, "source": src, "published": pub})
        if items:
            cache["items"] = items
            cache["ts"] = now
            return items, "ok"
        if cache["items"]:
            return cache["items"], "stale"
        return [], "unavailable"
    except Exception:
        if cache["items"]:
            return cache["items"], "stale"
        return [], "unavailable"


_MARKET_QUOTES_CACHE = {"ts": 0.0, "items": None}
_MARKET_QUOTES_TTL = 30 * 60   # 30 min — intraday but not real-time

def _yahoo_quote(symbol):
    """(price, prev_close) for a Yahoo Finance symbol via its public chart API,
    or (None, None) on any failure. Used for live FX / gold / oil quotes."""
    import urllib.request as _u, json as _j
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?interval=1d&range=5d"
    req = _u.Request(url, headers={"User-Agent": "Mozilla/5.0 (OshineiDashboard)"})
    with _u.urlopen(req, timeout=6) as resp:
        d = _j.loads(resp.read())
    meta = d["chart"]["result"][0]["meta"]
    return meta.get("regularMarketPrice"), meta.get("chartPreviousClose")

def _fetch_market_quotes():
    """Live macro quotes — FX (USD/JPY/RMB vs THB), oil (WTI/Brent) and gold — from
    Yahoo Finance, cached 30 min. Gives the owner a macro/cost picture: import cost via
    FX (esp. JPY for Japanese ingredients), energy/transport via oil, inflation via gold.
    NOTE on source: the owner asked for investing.com, but investing.com has no public
    API and Cloudflare-blocks datacenter IPs (the Fly host), so a scrape would be fragile
    and break silently. Yahoo Finance tracks the same global spot rates and is reliable.
    Each symbol is fetched independently so one hiccup can't blank the whole strip.
    Never raises; degrades to last-known/empty gracefully. Returns (items, status)."""
    import time as _t
    now = _t.time()
    cache = _MARKET_QUOTES_CACHE
    if cache["items"] is not None and (now - cache["ts"]) < _MARKET_QUOTES_TTL:
        return cache["items"], "ok"

    def q(sym, tries=3):
        # Yahoo occasionally returns an empty/garbled body for a single symbol;
        # a quick retry almost always succeeds, so the important quotes (USD/THB)
        # don't silently drop out of the strip.
        for _ in range(tries):
            try:
                r = _yahoo_quote(sym)
                if r and r[0]:
                    return r
            except Exception:
                pass
            _t.sleep(0.4)
        return (None, None)

    usdthb, usdthb_prev = q("THB=X")
    jpythb, jpythb_prev = q("JPYTHB=X")   # THB per 1 JPY
    cnythb, cnythb_prev = q("CNYTHB=X")   # THB per 1 CNY (RMB)
    gold,   gold_prev   = q("GC=F")
    wti,    wti_prev    = q("CL=F")
    brent,  brent_prev  = q("BZ=F")

    def chg(p, pv):
        return round((p - pv) / pv * 100, 1) if (p and pv) else None

    items = []
    if usdthb:
        c = chg(usdthb, usdthb_prev)
        sub = "อัตราแลกเปลี่ยน"
        if c is not None:
            sub = "เงินบาทอ่อนค่า — นำเข้าแพงขึ้น" if c > 0 else "เงินบาทแข็งค่า — นำเข้าถูกลง" if c < 0 else "ทรงตัว"
        items.append({"label": "USD/THB", "value": f"{usdthb:.2f}", "change_pct": c, "sub": sub})
    if jpythb:
        c = chg(jpythb, jpythb_prev)   # ratio change is the same whether per-1 or per-100 yen
        sub = "เยน vs บาท"
        if c is not None:
            sub = "เยนแข็ง — วัตถุดิบญี่ปุ่นแพงขึ้น" if c > 0 else "เยนอ่อน — วัตถุดิบญี่ปุ่นถูกลง" if c < 0 else "ทรงตัว"
        items.append({"label": "JPY/THB (100¥)", "value": f"{jpythb * 100:.2f}", "change_pct": c, "sub": sub})
    if cnythb:
        c = chg(cnythb, cnythb_prev)
        items.append({"label": "CNY/THB (หยวน)", "value": f"{cnythb:.2f}", "change_pct": c, "sub": "หยวน vs บาท · นักท่องเที่ยว/นำเข้าจีน"})
    if gold:
        c = chg(gold, gold_prev)
        thb_baht = gold * usdthb * (15.244 / 31.1035) if usdthb else None   # THB per 1 baht-weight
        items.append({"label": "ทองคำ (Gold)", "value": f"${gold:,.0f}/oz", "change_pct": c,
                      "sub": f"~฿{thb_baht:,.0f}/บาททอง" if thb_baht else "ราคาทองโลก · เครื่องชี้เงินเฟ้อ"})
    if wti:
        c = chg(wti, wti_prev)
        items.append({"label": "น้ำมัน WTI", "value": f"${wti:.1f}/บาร์เรล", "change_pct": c,
                      "sub": "ต้นทุนพลังงาน/ขนส่ง"})
    if brent:
        c = chg(brent, brent_prev)
        items.append({"label": "น้ำมัน Brent", "value": f"${brent:.1f}/บาร์เรล", "change_pct": c,
                      "sub": "อ้างอิงราคาน้ำมันโลก"})
    # Only cache a set that has the USD/THB anchor — never let a partial fetch
    # (e.g. a Yahoo hiccup that dropped the key quotes) overwrite a good cache.
    if items and usdthb:
        cache["items"] = items
        cache["ts"] = now
        return items, "ok"
    if cache["items"]:
        return cache["items"], "stale"
    if items:
        return items, "ok"          # first run, partial — better than nothing
    return [], "unavailable"


@app.get("/api/market")
def api_market():
    """Curated market/industry context + live (server-fetched) news + macro quotes."""
    news, status = _fetch_market_news()
    quotes, qstatus = _fetch_market_quotes()
    return {
        "as_of": MARKET_AS_OF,
        "macro": MARKET_MACRO,
        "industry": MARKET_INDUSTRY,
        "benchmark": MARKET_BENCHMARK,
        "sources": MARKET_SOURCES,
        "news": news,
        "news_status": status,
        "quotes": quotes,
        "quotes_status": qstatus,
    }


# ---------------------------------------------------------------------------
# /api/search   -  autocomplete for branches + SKUs
# ---------------------------------------------------------------------------
@app.get("/api/search")
def global_search(q: str = Query(..., min_length=1), limit: int = Query(15, ge=1, le=50)):
    """Autocomplete search across branches and SKUs."""
    qlike = f"%{q.lower()}%"
    with get_db() as con:
        branches = con.execute(
            """
            SELECT branch_id AS id, name
            FROM branches
            WHERE name != 'ALL' AND LOWER(name) LIKE ?
            ORDER BY name
            LIMIT ?
            """,
            (qlike, limit),
        ).fetchall()
        skus = con.execute(
            """
            SELECT sk.sku_code, sk.description, c.name AS category
            FROM skus sk
            JOIN categories c ON c.category_id = sk.category_id
            WHERE LOWER(sk.sku_code) LIKE ? OR LOWER(COALESCE(sk.description,'')) LIKE ?
            ORDER BY sk.sku_code
            LIMIT ?
            """,
            (qlike, qlike, limit),
        ).fetchall()
    return {
        "branches": [{"type": "branch", "id": r["id"], "name": r["name"]} for r in branches],
        "skus":     [{"type": "sku", "sku_code": r["sku_code"],
                      "description": r["description"], "category": r["category"]}
                     for r in skus],
    }


def _init_closed_hours():
    if not DB_PATH.exists():
        return
    with get_db() as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS closed_hours (
                branch_id INTEGER NOT NULL REFERENCES branches(branch_id),
                dow       INTEGER NOT NULL,
                hour_from INTEGER NOT NULL DEFAULT 0,
                hour_to   INTEGER NOT NULL DEFAULT 23,
                is_closed INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (branch_id, dow)
            )
        """)
        con.commit()
_init_closed_hours()


# ============================================================================
# PROFIT ANALYSIS  -  joins sales_sku_monthly x sku_cost to compute COGS + GP
# ============================================================================


# ─── Source-aware COGS rule (Phase 0 GP fix) ─────────────────────────────────
# Easy source logs both tier SKUs (BUFFET350/500/590/799/CHILD with revenue =
# customer payment) AND item SKUs (B0xxx with qty>0, revenue=0 — the real
# consumption). Using tier unit_cost AND item unit_cost double-counts.
# Rule: for Easy rows whose sku_code is a buffet tier, contribute zero cost
# (and zero costed-revenue); for everything else, use unit_cost normally.
# 'BUFFET' (plain, sku_id=1) is a deprecated legacy buffet code. Some Easy
# bills still emit it as a fallback. Treat same as tiered codes: count revenue,
# skip unit_cost (avoid double-counting 825M of phantom COGS).
# (tier-code tuples and their SQL forms now come from rules.py — imported above)


# ===========================================================================
# Phase 4 — Ingredient-level BoM composition
# ===========================================================================

def _bom_ensure_and_seed():
    """Create bom_ingredients table if missing; seed from Excel files if empty."""
    try:
        with get_db() as con:
            con.executescript("""
                CREATE TABLE IF NOT EXISTS bom_ingredients (
                  sku_id           INTEGER NOT NULL REFERENCES skus(sku_id),
                  seq              INTEGER NOT NULL,
                  ingredient_code  TEXT,
                  ingredient_name  TEXT NOT NULL,
                  qty_per_serving  REAL,
                  unit             TEXT,
                  price_per_unit   REAL,
                  ingredient_cost  REAL NOT NULL,
                  ingredient_type  TEXT,
                  source_file      TEXT,
                  PRIMARY KEY (sku_id, seq)
                );
                CREATE INDEX IF NOT EXISTS idx_bom_sku  ON bom_ingredients(sku_id);
                CREATE INDEX IF NOT EXISTS idx_bom_code ON bom_ingredients(ingredient_code);
            """)
            con.commit()
            n = con.execute("SELECT COUNT(*) FROM bom_ingredients").fetchone()[0]
        if n == 0:
            try:
                import importlib.util, sys
                _ib_path = HERE / "ingest_bom.py"
                if not _ib_path.exists():
                    return
                spec = importlib.util.spec_from_file_location("ingest_bom", str(_ib_path))
                _ib = importlib.util.module_from_spec(spec)
                sys.modules["ingest_bom"] = _ib
                spec.loader.exec_module(_ib)
                stats = _ib.ingest()
                print(f"[bom] auto-seeded: {stats}")
            except Exception as e:
                print(f"[bom] auto-seed failed: {e}")
    except Exception as e:
        print(f"[bom] ensure_table failed: {e}")


# The Grand Menu (from 2026-09-01) ships its costs as an owner-maintained master
# file rather than a recipe: BoM/GrandMenu_2026-09/master_item_costs.csv, one
# cost-per-serve per SKU for all 384 dishes (192 buffet + 192 a-la-carte).
#
# It is loaded HERE, at startup, for the same reason the durian correction is:
# sync_mssql copies the cost tables out of the local base DB into `.incoming`
# and that file is hot-swapped over the live one, so a cost written only into a
# database survives exactly until the next swap. A file in the repo is deployed
# with the code and re-asserts itself on every boot, whichever DB is underneath.
# It lives under backend/data/ and NOT beside the recipe workbooks in BoM/,
# which is gitignored — an uncommitted file is not deployed, so the loader would
# find nothing on Fly and silently fall back to the recipes it is meant to replace.
#
# It runs BEFORE _apply_cost_patches() so a hand-verified patch always outranks
# the sheet. Both write sku_cost, which _build_sku_effective_cost prefers over
# any recipe — for these SKUs the owner's number IS the authority.
# The loaders themselves live in cost_sources.py, shared with build_menu_fact so
# the app and the fact tables can never cost a dish differently. These wrappers
# keep the historical names, own the connection, and flush the cache.
import cost_sources as _cs
from cost_sources import (GRAND_MENU_COST_FILE, GRAND_MENU_ALIAS_FILE,          # noqa: F401
                          RECIPE_FILE, ESTIMATED_COST_FILE,
                          COST_PATCHES as _COST_PATCHES)


def _with_db(fn, *a):
    with get_db() as con:
        out = fn(con, *a)
    invalidate_cache()
    return out


def _load_grand_menu_costs(path=None):
    return _with_db(_cs.load_grand_menu_costs, path)


def _load_recipe_file(path=None):
    return _with_db(_cs.load_recipe_file, path)


def _load_grand_menu_aliases(path=None):
    return _with_db(_cs.load_grand_menu_aliases, path)


def _apply_cost_patches():
    return _with_db(_cs.apply_cost_patches)


def _load_estimated_costs(path=None):
    return _with_db(_cs.load_estimated_costs, path)


def _sku_cost_monthly_ensure():
    """Create sku_cost_monthly table (Phase 8) if missing."""
    try:
        with get_db() as con:
            con.executescript("""
                CREATE TABLE IF NOT EXISTS sku_cost_monthly (
                  sku_id        INTEGER NOT NULL REFERENCES skus(sku_id),
                  period_month  TEXT    NOT NULL,
                  unit_cost     REAL    NOT NULL,
                  notes         TEXT,
                  updated_at    TEXT    DEFAULT CURRENT_TIMESTAMP,
                  PRIMARY KEY (sku_id, period_month)
                );
                CREATE INDEX IF NOT EXISTS idx_skucm_period ON sku_cost_monthly(period_month);
                CREATE INDEX IF NOT EXISTS idx_skucm_sku    ON sku_cost_monthly(sku_id);
            """)
            con.commit()
    except Exception as e:
        print(f"[sku_cost_monthly] ensure_table failed: {e}")


@app.on_event("startup")
def _bom_startup():
    _bom_ensure_and_seed()
    # BoM Calculation Center tables (backend/bom_center, docs/ADR-001): bc_* are
    # site-owned and carried across swaps by prefix, bcd_* are derived. Creating
    # them here means the first deploy after this lands puts them on the live DB,
    # where the nightly fly_state backup and the swap's carry-forward see them.
    try:
        from bom_center import schema as _bc_schema
        from bom_center import dated as _bc_dated
        with get_db() as con:
            _bc_schema.ensure(con, commit=True)
            # the released costs per month: rebuilt from the (preserved) releases on every
            # start, so a new month gets its row the first time the app boots in it
            print(f"[bom-center] dated costs: {_bc_dated.rebuild(con)}")
            con.commit()
    except Exception as e:
        print(f"[bom-center] ensure failed: {e}")
    # Every file-borne cost source, in precedence order, through the SAME entry
    # point build_menu_fact uses — see cost_sources.apply_all for the order and
    # why it is what it is.
    try:
        print(f"[cost-sources] {_with_db(_cs.apply_all)}")
    except Exception as e:
        print(f"[cost-sources] apply_all failed: {e}")
    _sku_cost_monthly_ensure()
    _perf_startup()
    _month_summary_backfill()
    # Auth tables + seed the 12 default users (idempotent; no-op once seeded).
    try:
        with get_db() as con:
            _auth.init_auth(con)
            _auth.seed_users(con)
    except Exception as e:
        print(f"[auth] init/seed failed: {e}")
    # Background hot-swap: pick up a freshly-synced .incoming without a restart.
    # Disable with OSHINEI_NO_AUTOSWAP=1 (e.g. tests). On Fly the daily refresh
    # restarts the machine anyway, but the poller is harmless there too.
    if _os_db.environ.get("OSHINEI_NO_AUTOSWAP") != "1":
        try:
            threading.Thread(target=_autoswap_poller, name="db-autoswap",
                             daemon=True).start()
            print("[hot-swap] auto-swap poller started")
        except Exception as e:
            print(f"[hot-swap] could not start poller: {e}")


# Covering indexes that turn the heaviest Analysis-Report scans into index-only
# seeks. Each uses the EXACT functional expression of an existing index family so
# the planner matches them. Created idempotently at startup (one-time cost on the
# first boot after deploy; IF NOT EXISTS makes later cold starts free).
_PERF_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_tx_disc_cov ON sales_transaction"
    "(branch_id, strftime('%Y-%m', sold_at), cashier, discount, food_subtotal, drink_subtotal, other_subtotal)",
    "CREATE INDEX IF NOT EXISTS idx_tx_persona_cov ON sales_transaction"
    "(branch_id, strftime('%Y-%m', sold_at), customer_count, total_inc_vat, food_subtotal, drink_subtotal, other_subtotal)",
    # Day-of-week covering index, used by analysis_dow's no-branch JOIN-drop path.
    # (Hourly reuses the existing idx_tx_src_hour, so no separate index is needed.)
    "CREATE INDEX IF NOT EXISTS idx_tx_ym_dow_cov ON sales_transaction"
    "(strftime('%Y-%m', sold_at), strftime('%w', sold_at), sold_at, total_inc_vat, customer_count)",
    # sale_payment lookup by receipt — needed by discount_bills correlated subqueries
    # (dominant payment code + payment count per receipt). Without this index SQLite
    # does a full table scan for every row in the result set → O(n * |sale_payment|).
    "CREATE INDEX IF NOT EXISTS idx_sp_receipt ON sale_payment(branch_id, receipt_no, amount)",
    # menu-impact selector runs `SELECT ent, SUM(qty) ... GROUP BY ent` over the
    # 2.1M-row fact table on every load, purely to know which entities sold. The
    # existing ix_mfd_ent(ent, day) doesn't carry qty, so SQLite still visited
    # every row; adding qty makes it an index-ONLY scan. 2,016ms → 120ms, and the
    # endpoint as a whole 8.8s → 163ms.
    "CREATE INDEX IF NOT EXISTS ix_mfd_ent_qty ON menu_fact_day(ent, qty)",
)


def _perf_startup():
    """One-time DB perf setup: build the per-SKU effective-cost lookup and ensure the
    covering indexes exist. Resilient — a failure here must never block app startup."""
    try:
        with get_db() as con:
            _ensure_rev_views(con)
            for ddl in _PERF_INDEXES:
                try:
                    con.execute(ddl)
                except Exception as e:
                    print(f"[perf idx] {ddl[:60]}... -> {e}")
            con.commit()
            # The planner only picks the new covering indexes once their stats exist.
            # Keyed on the most-recently-added index so a deploy that introduces a new
            # one re-runs ANALYZE once; the result persists so later cold starts skip it.
            try:
                has_stats = con.execute(
                    "SELECT 1 FROM sqlite_stat1 WHERE idx='idx_tx_ym_dow_cov' LIMIT 1"
                ).fetchone()
            except sqlite3.OperationalError:
                has_stats = None  # sqlite_stat1 not created yet
            if not has_stats:
                con.execute("ANALYZE sales_transaction")
                con.commit()
            _build_sku_effective_cost(con, force=True)
    except Exception as e:
        print(f"[perf startup] failed: {e}")


def _month_summary_backfill():
    """Self-heal sales_month_summary for live-synced months (see month_summary.py).

    The live sync writes sales_transaction but not sales_month_summary, so a
    live-synced-only month has real data yet 0 summary rows — breaking the Top-60
    cost-per-head report and /api/analysis/compare. Running the shared roll-up
    here fixes the currently-live DB on the next deploy (no sync needed) and keeps
    the invariant holding. Conservative: only fills months missing from the
    summary; existing Excel rows are untouched. Resilient — never blocks startup."""
    try:
        with get_db() as con:
            n = _month_summary.backfill(con)
            con.commit()
            if n:
                print(f"[month-summary] backfilled {n} live-synced (branch, month) rows")
    except Exception as e:
        print(f"[month-summary backfill] failed: {e}")


# ═══════════════════════════════════════════════════════════════════════════
# Lean-overhaul Phase 3 — unified dispatcher endpoints
# ═══════════════════════════════════════════════════════════════════════════
# Four forecast routes and two table-turnover routes collapsed into one path
# each (mode/view param). The implementations above keep their own @ttl_cache
# with distinct __name__s (cache keys must never collide — see the
# table_turnover cache-poisoning note at the top of this file), so these
# dispatchers are cache-transparent and behaviour-identical per mode.

@app.get("/api/forecast")
def forecast_unified(
    mode:       str           = Query("month", pattern="^(month|branches|trend|buffet)$"),
    branch:     Optional[str] = Query(None),
    group:      Optional[str] = Query(None),
    periods:    int           = Query(3, ge=1, le=12),
    horizon:    int           = Query(3),
    month_from: Optional[str] = Query(None),
    month_to:   Optional[str] = Query(None),
):
    """One forecast path, four views:
    mode=month    → current-month run-rate projection      (was /api/analysis/forecast)
    mode=branches → per-branch month-end projection        (was /api/analysis/forecast_branches)
    mode=trend    → next-N-months OLS revenue projection   (was /api/analysis/revenue-forecast)
    mode=buffet   → buffet pax/revenue regression forecast (was /api/buffet/forecast)"""
    if mode == "branches":
        return analysis_forecast_branches(group=group)
    if mode == "trend":
        return revenue_forecast(branch=branch, horizon=horizon)
    if mode == "buffet":
        return buffet_forecast(branch=branch, periods=periods,
                               month_from=month_from, month_to=month_to)
    return analysis_forecast(branch=branch, group=group)


# ═══════════════════════════════════════════════════════════════════════════
# P1 structure refactor — the /api/reports/* domain lives in routers/reports.py
# (moved verbatim; shared infra stays here for now). Registered at the BOTTOM
# so the router sees a fully-initialized module when it pulls shared names.
# Local/tests import this file as `main`; prod uvicorn runs `backend.main:app`
# — hence the dual import. report_management is re-exported for back-compat.
# ═══════════════════════════════════════════════════════════════════════════
if __package__:
    from .routers.reports import router as _reports_router
    from .routers.reports import (report_management, report_management_xlsx,  # noqa: F401
                                  _bom_unit_cost_sql)                          # noqa: F401
else:
    from routers.reports import router as _reports_router
    from routers.reports import (report_management, report_management_xlsx,  # noqa: F401
                                 _bom_unit_cost_sql)                          # noqa: F401
app.include_router(_reports_router)
if __package__:
    from .routers.analysis import router as _analysis_router
    from .routers.analysis import (analysis_sph, analysis_forecast,  # noqa: F401
        analysis_forecast_branches, revenue_forecast)             # noqa: F401  (back-compat: tests + /api/forecast facade)
else:
    from routers.analysis import router as _analysis_router
    from routers.analysis import (analysis_sph, analysis_forecast,  # noqa: F401
        analysis_forecast_branches, revenue_forecast)            # noqa: F401  (back-compat: tests + /api/forecast facade)
app.include_router(_analysis_router)
if __package__:
    from .routers.analysis_discounts import router as _analysis_discounts_router
    from .routers.analysis_time import router as _analysis_time_router
else:
    from routers.analysis_discounts import router as _analysis_discounts_router
    from routers.analysis_time import router as _analysis_time_router
app.include_router(_analysis_discounts_router)
app.include_router(_analysis_time_router)
if __package__:
    from .routers.sales import router as _sales_router
    from .routers.sales import (_mtd_window, _month_add, _menucmp_agg)  # noqa: F401
else:
    from routers.sales import router as _sales_router
    from routers.sales import (_mtd_window, _month_add, _menucmp_agg)  # noqa: F401
# ^ back-compat: _mtd_window (main caller + test_mtd), _month_add (main caller),
#   _menucmp_agg (gen_menu_ab_deep_report.py imports it from main)
app.include_router(_sales_router)
if __package__:
    from .routers.menu import router as _menu_router
else:
    from routers.menu import router as _menu_router
app.include_router(_menu_router)
if __package__:
    from .routers.feedback import router as _feedback_router
else:
    from routers.feedback import router as _feedback_router
app.include_router(_feedback_router)
if __package__:
    from .routers.checklist import router as _checklist_router
else:
    from routers.checklist import router as _checklist_router
app.include_router(_checklist_router)
if __package__:
    from .routers.survey import router as _survey_router
else:
    from routers.survey import router as _survey_router
app.include_router(_survey_router)
if __package__:
    from .routers.feedback_actions import router as _feedback_actions_router
else:
    from routers.feedback_actions import router as _feedback_actions_router
app.include_router(_feedback_actions_router)
if __package__:
    from .routers.feedback_ai import router as _feedback_ai_router
else:
    from routers.feedback_ai import router as _feedback_ai_router
app.include_router(_feedback_ai_router)
if __package__:
    from .routers.feedback_business import router as _feedback_business_router
else:
    from routers.feedback_business import router as _feedback_business_router
app.include_router(_feedback_business_router)
if __package__:
    from .routers.feedback_competitors import router as _feedback_competitors_router
else:
    from routers.feedback_competitors import router as _feedback_competitors_router
app.include_router(_feedback_competitors_router)
if __package__:
    from .routers.feedback_daily_report import router as _feedback_daily_report_router
else:
    from routers.feedback_daily_report import router as _feedback_daily_report_router
app.include_router(_feedback_daily_report_router)
if __package__:
    from .routers.buffet import router as _buffet_router
    from .routers.buffet import buffet_forecast  # noqa: F401  (/api/forecast facade)
else:
    from routers.buffet import router as _buffet_router
    from routers.buffet import buffet_forecast  # noqa: F401  (/api/forecast facade)
app.include_router(_buffet_router)
if __package__:
    from .routers.home_extra import router as _home_extra_router
else:
    from routers.home_extra import router as _home_extra_router
app.include_router(_home_extra_router)
if __package__:
    from .routers import push_api as _push_api
else:
    from routers import push_api as _push_api
app.include_router(_push_api.router)


def _push_scheduler_loop():
    """Every minute: send the morning brief as a notification once a day after 08:30
    Thai time (routers/push_api.maybe_send_morning). A daemon thread — the Fly machine
    never stops (min_machines_running = 1), so nobody has to open the app for it."""
    import time as _time
    while True:
        try:
            _push_api.maybe_send_morning()
            _push_api.maybe_send_alerts()
            _push_api.maybe_send_weekly()
        except Exception as e:  # noqa: BLE001 — never let the loop die
            print(f"[push] scheduler: {e}")
        _time.sleep(60)


@app.on_event("startup")
def _push_startup():
    try:
        with get_db() as con:
            _push_api.ensure(con)
            con.commit()
    except Exception as e:
        print(f"[push] ensure failed: {e}")
    import os as _os, sys as _sys
    if _os.environ.get("OSHINEI_PUSH_SCHEDULER", "1") == "1" and "pytest" not in _sys.modules:
        threading.Thread(target=_push_scheduler_loop, name="push-morning", daemon=True).start()
if __package__:
    from .routers.cost_bom import router as _cost_bom_router
    from .routers.cost_bom import get_cost_detail, branch_bom_analysis  # noqa: F401
else:
    from routers.cost_bom import router as _cost_bom_router
    from routers.cost_bom import get_cost_detail, branch_bom_analysis  # noqa: F401
# ^ back-compat: both are called by the skus/business-type write path in main
app.include_router(_cost_bom_router)
if __package__:
    from .routers.profit_pnl import router as _profit_pnl_router
    from .routers.profit_pnl import buffet_per_bill  # noqa: F401  (test back-compat)
else:
    from routers.profit_pnl import router as _profit_pnl_router
    from routers.profit_pnl import buffet_per_bill  # noqa: F401  (test back-compat)
app.include_router(_profit_pnl_router)
if __package__:
    from .routers.auth_api import router as _auth_api_router
else:
    from routers.auth_api import router as _auth_api_router
app.include_router(_auth_api_router)
if __package__:
    from .routers.admin import router as _admin_router
else:
    from routers.admin import router as _admin_router
app.include_router(_admin_router)
if __package__:
    from .routers.export import router as _export_router
else:
    from routers.export import router as _export_router
app.include_router(_export_router)
if __package__:
    from .routers.ingredients import router as _ingredients_router
else:
    from routers.ingredients import router as _ingredients_router
app.include_router(_ingredients_router)
try:
    from .routers.requisition import router as _requisition_router
except ImportError:
    from routers.requisition import router as _requisition_router
app.include_router(_requisition_router)
try:
    from .routers.bom_center import router as _bom_center_router
except ImportError:
    from routers.bom_center import router as _bom_center_router
app.include_router(_bom_center_router)
try:
    from .routers.franchise import router as _franchise_router
except ImportError:
    from routers.franchise import router as _franchise_router
app.include_router(_franchise_router)

# ═══════════════════════════════════════════════════════════════════════════
# Ingredient ordering — per-branch purchase planning (แผนสั่งวัตถุดิบรายสาขา)
# ═══════════════════════════════════════════════════════════════════════════
# Consumption is DERIVED, never counted: qty sold per SKU (sales_sku_daily)
# × qty_per_serving of that SKU's recipe (bom_ingredients), resolved through the
# same direct→alias chain the cost engine uses (_build_sku_effective_cost), so a
# plate's ingredients tie out with its COGS.
#
# It is a DEMAND FORECAST, not a stock count: it says how much a branch is
# expected to CONSUME and knows nothing about what is already in the fridge.
# Whoever orders must subtract on-hand stock — that caveat is printed on the
# letter, because an order plan that looks like a stock order is dangerous.


# ── Order-unit conversion ────────────────────────────────────────────────────
# A recipe counts in กรัม / มิลลิลิตร / ชิ้น, but nobody orders 6,516,040 กรัม of
# salmon — they order whole packs. ingredient_order_unit maps each ingredient to
# the pack it is BOUGHT in, and the plan rounds UP to whole packs (never down:
# ordering short is worse than a little carry-over).
#
#   qty_per_order  base units in one pack   (1 กก. pack of grams  -> 1000)
#   round_step     order in multiples of N packs (a case of 6 -> 6)
#   moq            supplier minimum, in packs
#
# Rows are seeded from the supplier sheet by backend/seed_order_units.py and can
# be corrected through POST /api/ingredients/order-units. Anything with no row
# falls back to _ING_UNIT_FALLBACK, and the row is flagged pack_known=false so
# the UI can show which ingredients still need a real pack size.


# ── Sourcing: which ingredients the order plan should actually recommend ─────
# Branches buy some items locally (fresh veg from the market, ice, a local
# bakery) and it is easier to leave those off the central order. ingredient_
# sourcing marks each ingredient 'central' (recommend it) or 'local' (branch
# sources it — keep it out of the letter but keep showing the usage, because
# knowing the consumption still matters for costing).
#
# branch = '' is the default for every branch; a row with a branch name
# overrides just that branch, so ผักสด can be central everywhere except one
# branch next to a market.


# ═══════════════════════════════════════════════════════════════════════════
# Order cycles: issue → record what was actually ordered → compliance → notice
# ═══════════════════════════════════════════════════════════════════════════
# The recommendation moves every day as new sales arrive. Comparing what a
# branch ordered last week against a number recomputed today would be a
# different number than the one they were given — indefensible in a warning
# letter. So issuing a cycle SNAPSHOTS the recommendation (issue + issue_line),
# actuals attach to that snapshot, and every comparison reads the frozen
# figures. Re-running the plan later never changes what a branch was judged on.


# ═══════════════════════════════════════════════════════════════════════════
# Seasonality: scale the order plan for festivals using LAST YEAR's covers
# ═══════════════════════════════════════════════════════════════════════════
# A 28-day average knows nothing about สงกรานต์ or ปีใหม่. Measured on this
# chain: Songkran 13-15 Apr ran 6,335 pax/day against 4,299 in a normal March
# (x1.47) and 28 Dec-2 Jan ran 7,314 against 4,075 in November (x1.79). Ordering
# a normal week into either of those runs the branch dry.
#
# The factor is computed PER BRANCH as
#     (that branch's daily covers over the same calendar dates last year)
#   / (that branch's daily covers over a normal stretch just before them)
# Both windows sit ~2 months apart in the SAME branch, so network growth and new
# openings cancel out — comparing this year against last year directly would
# read the chain's growth as seasonality.
#
# demand_calendar holds named events. factor NULL = derive from last year;
# an explicit factor covers a promotion that has no history to learn from.


# ═══════════════════════════════════════════════════════════════════════════
# Forecast accuracy — tune the buffer from what actually got consumed
# ═══════════════════════════════════════════════════════════════════════════
# The compliance report answers "did the branch order what we told it to".
# That is NOT accuracy: a branch can comply perfectly with a bad forecast.
# Accuracy is the frozen recommendation against what the branch actually
# CONSUMED over that same cycle — which we can compute once the cycle's period
# has passed, from sales_sku_daily x BoM, the same way the plan was built.
#
# The buffer suggestion is a service level, not an average. Ordering the mean
# leaves you short half the time, so we take the ratio of consumption to the
# RAW forecast (buffer removed) and pick the percentile the owner asks for:
# "a buffer that would have covered N% of past cycles". Under-ordering costs a
# stock-out; over-ordering costs carry — so the default leans to 80%.


