"""
Oshinei Sales & Cost Analysis  -  FastAPI backend
Run: uvicorn backend.main:app --reload --port 8000
  or from this folder: uvicorn main:app --reload --port 8000
"""

import sqlite3
import tempfile
import shutil
import threading
import time as _time
import functools
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, List, Dict

from fastapi import FastAPI, Query, HTTPException, UploadFile, File, Header, Depends, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
import io as _io
from pydantic import BaseModel

try:                                   # works as `backend.main:app`
    from . import auth as _auth
    from . import month_summary as _month_summary
except ImportError:                     # …and as `main:app` from inside backend/
    import auth as _auth                # role-based authentication (backend/auth.py)
    import month_summary as _month_summary   # sales_month_summary self-heal (backend/month_summary.py)

# ---------------------------------------------------------------------------
# Response cache  (TTL-based, keyed on function name + query-param values)
# ---------------------------------------------------------------------------
_resp_cache: dict = {}
_CACHE_TTL  = 300   # 5 minutes — safe for an analytics dashboard
_cache_lock = threading.Lock()

def _cache_key(fn_name: str, kwargs: dict) -> str:
    return fn_name + "|" + "|".join(f"{k}={v}" for k, v in sorted(kwargs.items()))

import json as _json
from fastapi.responses import Response as _Response

_cache_stats: dict = {"hits": 0, "misses": 0}

_cached_fn_names: set = set()

def ttl_cache(fn):
    """Decorator: caches the pre-serialized JSON bytes for 5 min.

    Returning a ``Response(content=bytes)`` skips FastAPI's jsonable_encoder,
    which matters for large responses (e.g. lifecycle = 270 KB).

    Cache key = fn.__name__ + params, so two cached endpoints MUST NOT share a
    function name (they would poison each other's cache — the table_turnover bug).
    Guard it at import time so the collision can never recur silently.
    """
    if fn.__name__ in _cached_fn_names:
        raise RuntimeError(
            f"ttl_cache: duplicate cached function name '{fn.__name__}' — "
            f"rename one (same name = colliding cache key, serves wrong responses)")
    _cached_fn_names.add(fn.__name__)
    import inspect as _inspect
    sig = _inspect.signature(fn)          # capture original signature once

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        # Normalise args → stable dict keyed by parameter name
        try:
            bound = sig.bind(*args, **kwargs)
            bound.apply_defaults()
            bound_kwargs = dict(bound.arguments)
        except TypeError:
            bound_kwargs = kwargs

        key = _cache_key(fn.__name__, bound_kwargs)
        now = _time.monotonic()
        with _cache_lock:
            entry = _resp_cache.get(key)
            if entry and now - entry[0] < _CACHE_TTL:
                _cache_stats["hits"] += 1
                return _Response(content=entry[1], media_type="application/json")
            _cache_stats["misses"] += 1

        result = fn(*args, **kwargs)
        try:
            result_bytes = _json.dumps(result, ensure_ascii=False).encode("utf-8")
        except (TypeError, ValueError):
            # Non-serializable (e.g. FileResponse) — skip cache, return raw
            return result
        with _cache_lock:
            _resp_cache[key] = (now, result_bytes)
        return _Response(content=result_bytes, media_type="application/json")

    # Expose original signature so FastAPI injects the right query params
    wrapper.__signature__ = sig
    return wrapper

def invalidate_cache(*prefixes: str):
    """Remove cached entries whose key starts with any of the given function names.

    Called with no arguments → flush the entire cache (used after bulk data writes
    where it's hard to know which derived endpoints depend on the change).
    """
    with _cache_lock:
        if not prefixes:
            _resp_cache.clear()
        else:
            drop = [k for k in _resp_cache if any(k.startswith(p) for p in prefixes)]
            for k in drop:
                del _resp_cache[k]
    # A full clear (no prefixes) signals "cost / bom / sales data changed". Rebuild
    # the per-SKU effective-cost lookup so _unified_cogs_expr reflects the new costs
    # immediately (preserving the pre-materialisation live-subquery behaviour).
    # Best-effort and resilient — never break the mutation response if it fails.
    if not prefixes:
        try:
            with get_db() as con:
                _build_sku_effective_cost(con, force=True)
        except Exception as e:
            print(f"[sku_effective_cost] rebuild after cache flush failed: {e}")

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
HERE    = Path(__file__).parent.resolve()
# DB location is overridable so the database can live on a mounted persistent
# volume in hosted deployments (e.g. Fly.io: OSHINEI_DB=/data/oshinei.db).
# Defaults to the in-repo path for local development.
import os as _os_db
DB_PATH = Path(_os_db.environ.get("OSHINEI_DB", str(HERE / "oshinei.db")))


def _carry_forward_preserved(live_path: str, inc_path: str) -> None:
    """Copy locally-owned tables from the live DB into a staged refresh before it
    replaces the live DB.

    These tables live in the shared oshinei.db but are NOT produced by the MSSQL
    sync / data refresh, so a wholesale swap would wipe them:
      • recon_*   — staff reconciliation work (uploads, payment-code maps,
                    bank/acquirer registries, sessions, discrepancies)
      • app_user  — login accounts (passwords, roles, deactivations). Without
                    this, every live-side user edit is lost on each data refresh
                    and the seeded defaults silently return (see backend/auth.py).
    The LIVE copy wins (edits made on the running site survive the swap). We
    recreate each table with its full DDL (preserving PK / UNIQUE / CHECK / FK
    and explicit indexes) and copy its rows.
    """
    if not _os_db.path.exists(live_path):
        return
    live = sqlite3.connect(live_path)
    try:
        objs = live.execute(
            "SELECT type, name, sql FROM sqlite_master "
            "WHERE sql IS NOT NULL AND ("
            "  (type='table' AND (name     LIKE 'recon\\_%' ESCAPE '\\' OR name='app_user')) OR "
            "  (type='index' AND (tbl_name LIKE 'recon\\_%' ESCAPE '\\' OR tbl_name='app_user')))"
            "ORDER BY (type='index')"            # tables first, then their indexes
        ).fetchall()
        if not any(o[0] == "table" for o in objs):
            return
        dest = sqlite3.connect(inc_path)
        try:
            dest.execute("PRAGMA foreign_keys=OFF")
            for typ, name, sql in objs:
                if typ == "table":
                    dest.execute(f'DROP TABLE IF EXISTS "{name}"')
                    dest.execute(sql)
                    rows = live.execute(f'SELECT * FROM "{name}"').fetchall()
                    if rows:
                        ph = ",".join("?" * len(rows[0]))
                        dest.executemany(f'INSERT INTO "{name}" VALUES ({ph})', rows)
                else:
                    dest.execute(f'DROP INDEX IF EXISTS "{name}"')
                    dest.execute(sql)
            dest.commit()
        finally:
            dest.close()
    finally:
        live.close()


def _preserved_counts(db_path: str) -> dict:
    """Row counts of the locally-owned tables that MUST survive a refresh
    (recon_* + app_user). Used to verify a staged swap won't drop any of them."""
    counts: dict = {}
    if not _os_db.path.exists(db_path):
        return counts
    c = sqlite3.connect(db_path)
    try:
        names = [r[0] for r in c.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND "
            "(name LIKE 'recon\\_%' ESCAPE '\\' OR name='app_user')")]
        for n in names:
            try:
                counts[n] = c.execute(f'SELECT COUNT(*) FROM "{n}"').fetchone()[0]
            except sqlite3.Error:
                pass
    finally:
        c.close()
    return counts


def _carry_forward_and_verify(live_path: str, inc_path: str) -> tuple[bool, str]:
    """Carry preserved tables from live into the staged DB, then VERIFY nothing
    was dropped. Returns (safe, reason). FAIL-CLOSED: any error, or any preserved
    table ending up with fewer rows in the staged DB than live, means the caller
    must NOT swap — protecting the only copy of staff reconciliation work from a
    routine data refresh. (The old code swallowed carry-forward errors and swapped
    anyway, which could silently wipe all recon uploads.)"""
    try:
        live_counts = _preserved_counts(live_path)
        _carry_forward_preserved(live_path, inc_path)
        staged_counts = _preserved_counts(inc_path)
    except Exception as exc:
        return False, f"carry-forward failed: {exc!r}"
    lost = {t: (live_counts[t], staged_counts.get(t, 0))
            for t in live_counts if staged_counts.get(t, 0) < live_counts[t]}
    if lost:
        return False, f"staged refresh would drop preserved rows {lost} (live,staged)"
    return True, "ok"


def _maybe_swap_incoming_db():
    """Apply a staged DB refresh at startup, before any connection is opened.

    The refresh endpoint (/api/_admin/seed-db) streams a new DB to
    ``<DB_PATH>.incoming`` instead of touching the live file. Here — at import,
    while nothing holds the DB — we validate that staged file and atomically
    swap it in. A corrupt/empty upload is discarded, so the live DB is never put
    at risk by a bad or partial transfer.
    """
    inc = Path(str(DB_PATH) + ".incoming")
    if not inc.exists():
        return
    try:
        chk = sqlite3.connect(str(inc))
        # quick_check (page-level) not integrity_check (full index cross-check):
        # the realistic failure of a freshly streamed upload is a truncated/partial
        # transfer, which quick_check catches — and it's ~20x faster on a large DB
        # (0.45s vs ~10s on 348 MB), so this no longer dominates startup.
        ok  = chk.execute("PRAGMA quick_check").fetchone()[0]
        n   = chk.execute("SELECT COUNT(*) FROM sales_transaction").fetchone()[0]
        chk.close()
    except Exception:
        ok, n = "bad", 0
    if ok != "ok" or not n:
        try: inc.unlink()
        except OSError: pass
        return
    # Carry forward reconciliation tables so a data refresh never wipes them.
    # FAIL-CLOSED: if the carry-forward can't be verified, keep the live DB and
    # leave the staged file for inspection rather than swap in a wipe.
    safe, reason = _carry_forward_and_verify(str(DB_PATH), str(inc))
    if not safe:
        print(f"[db-swap] ABORTED — {reason}; live DB kept, {inc.name} left for inspection")
        return
    # Discard stale WAL/SHM of the OLD db, then atomically replace it.
    for suf in ("-wal", "-shm"):
        try: _os_db.remove(str(DB_PATH) + suf)
        except OSError: pass
    try:
        _os_db.replace(str(inc), str(DB_PATH))   # atomic on same filesystem
    except OSError:
        # incoming locked (e.g. OneDrive sync) — leave it; a later restart retries.
        pass

# Skip the startup DB swap during tests / tooling (set OSHINEI_NO_SWAP=1).
# Importing this module then has no filesystem side effects.
if _os_db.environ.get("OSHINEI_NO_SWAP") != "1":
    _maybe_swap_incoming_db()
STATIC  = HERE.parent / "vendor"
DOCS    = HERE.parent / "docs"
INDEX   = HERE.parent / "index.html"   # canonical (Phase 17d-fix 2026-05-28: was 'Oshinei Dashboard.html')

# ---------------------------------------------------------------------------
# VAT — single source of truth. Easy POS uses 7% VAT.
# Revenue is ALWAYS ex-VAT (rule: "VAT is never revenue"). To convert an
# inc-VAT total to ex-VAT, divide by VAT_DIVISOR. SQL queries embed the literal
# `1.07` for performance/readability; this constant is the canonical reference
# and is enforced by test_invariants (per-head/ticket must be ex-VAT).
# ---------------------------------------------------------------------------
VAT_RATE    = 0.07
VAT_DIVISOR = 1.0 + VAT_RATE          # 1.07  — inc-VAT ➜ ex-VAT divisor

def ex_vat(inc_vat_amount: float) -> float:
    """Convert an inc-VAT amount to ex-VAT (net) revenue."""
    return (inc_vat_amount or 0.0) / VAT_DIVISOR

# Price-as-cost detector: a unit_cost that lands in this ratio band vs the SKU's
# ex-VAT selling price is almost certainly the (inc-VAT) PRICE loaded as cost,
# not a real cost. Used to guard POST /api/cost and by fix_sku_cost_2026.py.
PRICE_AS_COST_LO = 1.03
PRICE_AS_COST_HI = 1.12

# ---------------------------------------------------------------------------
# "Real food menu" classifier — used by menu rankings (Top SKUs / Most Ordered).
# Category-based (robust) rather than code-prefix. A category is EXCLUDED if its
# name contains any of these keywords (case-insensitive). Excludes: drinks &
# alcohol, non-product lines, dessert/ice-cream, and per-branch 'Special' menus.
# Requires the query to JOIN categories AS c.
# ---------------------------------------------------------------------------
_NONFOOD_CATEGORY_KEYWORDS = (
    "BUFFET", "SOFT DRINK", "DRINK", "ALCOHOL", "DRINKING", "BEER",   # drinks/alcohol
    "PAYMENT", "DELIVERY", "SERVICE", "PROMOTI", "VOUCHER", "DISCOUNT",
    "DEPOSIT", "TAKE AWAY", "TAKEAWAY", "CATERING", "CONDIMENT", "SHUYU",
    "ITEMSET", "BADMINTON",                                            # non-product
    "DESSERT", "ICE-CREAM", "ICECREAM",                               # dessert
    "SPECIAL",                                                         # branch 'Special <x>' menus
)
# Real foods that live under NON-food categories and would be wrongly dropped by
# the keyword blacklist above. Durian is filed under 'Special KKN' / 'Promotion' /
# 'Oshinei SERVICE' (→ hits SPECIAL/PROMOTI/SERVICE) yet sells ~192k servings
# across the 5 master branches during buffet and is costed @30฿ — excluding it
# understated buffet cost/head by ~27฿ (≈60฿ in May). Whitelisted by code so EVERY
# food/cost report counts it (the whole category+code filter is OR'd with this
# list). Add a SKU here when a genuine dish hides under a non-food category.
# (2026-06-11 — see [[eel_bom_estimate]] + debug-mantra "category-not-branch" rule)
_FOOD_SKU_WHITELIST = ('KKN018', 'KKN019', 'PM0010', 'PM0011', 'PM0012', 'DUR-999')  # durian
_FOOD_SKU_WHITELIST_SQL = "(" + ",".join(f"'{c}'" for c in _FOOD_SKU_WHITELIST) + ")"
_FOOD_CATEGORY_FILTER = (
    "((" + " AND ".join(
        f"UPPER(c.name) NOT LIKE '%{kw}%'" for kw in _NONFOOD_CATEGORY_KEYWORDS
    ) + ")"
    # Code-level guards: OT* = penalty / room-opening charges, SERVICECHARGE/SP =
    # service, DEL* = delivery — these can sit inside food categories but are not
    # dishes, so exclude by code too.
    " AND sk.sku_code NOT GLOB 'OT[0-9]*'"
    " AND sk.sku_code NOT GLOB 'DEL[0-9]*'"
    " AND UPPER(sk.sku_code) NOT IN ('SERVICECHARGE','SP','SERVICE','DISCOUNT','DEPOSIT')"
    " AND sk.sku_code NOT LIKE 'BUFFET%'"
    # … OR an explicitly whitelisted real-food SKU that a category keyword caught.
    f" OR sk.sku_code IN {_FOOD_SKU_WHITELIST_SQL})"
)


def is_price_as_cost(unit_cost, ex_vat_price) -> bool:
    """True if unit_cost looks like the (inc-VAT) selling price entered as cost,
    i.e. ratio vs ex-VAT price falls in the VAT-multiplier band. Real food costs
    are a fraction of price (ratio « 1), so this band cannot catch a genuine cost."""
    if not unit_cost or not ex_vat_price or unit_cost <= 0 or ex_vat_price <= 0:
        return False
    return PRICE_AS_COST_LO <= (unit_cost / ex_vat_price) <= PRICE_AS_COST_HI

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
            or path.startswith("/api/_admin/seed-db"):
        # seed-db is a headless machine-to-machine endpoint with its OWN token auth
        # (?token=, returns 404 on mismatch). It must bypass the login middleware,
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
        return JSONResponse(
            {"detail": "บทบาทของคุณไม่มีสิทธิ์เข้าถึงส่วนนี้ (forbidden)."},
            status_code=403)
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


# Serve vendor JS/CSS
if STATIC.exists():
    app.mount("/vendor", StaticFiles(directory=str(STATIC)), name="vendor")

# Serve generated reports / docs (HTML) so they open in-browser
if DOCS.exists():
    app.mount("/docs", StaticFiles(directory=str(DOCS), html=True), name="docs")


# ---------------------------------------------------------------------------
# Write auth — gate data-mutating endpoints behind the owner token
# ---------------------------------------------------------------------------
# Mirrors /api/upload exactly: when OSHINEI_SEED_TOKEN is set (hosted deploys),
# a matching token is required via the `X-Upload-Token` header or `?token=`.
# When the env var is unset (local dev), writes are allowed without a token, so
# the gate is invisible locally. Applied via `dependencies=[Depends(...)]` on
# the route decorator so endpoint signatures stay untouched.
# NOTE: read-only POSTs (e.g. /api/bom-cost/simulate) are intentionally NOT
# gated — they compute but never write, so anonymous use is harmless.
def _accepted_tokens() -> dict:
    """Parse OSHINEI_SEED_TOKEN into {token: label}. Supports a comma/newline list
    so several people can each hold their own token; an entry may be 'label:token'
    (label is for bookkeeping only). A single bare token still works unchanged."""
    raw = _os_db.environ.get("OSHINEI_SEED_TOKEN", "") or ""
    out: dict = {}
    for part in raw.replace("\n", ",").split(","):
        part = part.strip()
        if not part:
            continue
        if ":" in part:
            label, tok = part.split(":", 1)
            tok = tok.strip()
            if tok:
                out[tok] = label.strip()
        else:
            out[part] = ""
    return out

def _token_accepted(provided: str) -> bool:
    import hmac
    provided = (provided or "").strip()
    # Compare against every configured token (constant-time each) so timing can't
    # reveal which one matched.
    return any(hmac.compare_digest(provided, t) for t in _accepted_tokens())

def require_write_token(
    token: str = Query(""),
    x_upload_token: str = Header(None),
):
    toks = _accepted_tokens()
    if toks and not _token_accepted(x_upload_token or token or ""):
        raise HTTPException(status_code=403, detail="A valid write token is required.")


# ---------------------------------------------------------------------------
# DB helper
# ---------------------------------------------------------------------------
_PRAGMAS = (
    "PRAGMA journal_mode=WAL;"
    "PRAGMA synchronous=NORMAL;"
    # Page cache is PER-CONNECTION and each worker thread keeps its own
    # connection. At 64 MiB this multiplied under the Analysis Report's
    # concurrent query burst (~6 threads × 64 MiB ≈ 384 MiB) and OOM-killed the
    # 512 MiB host. 12 MiB keeps the aggregate small; the 5-min response cache
    # absorbs the slightly higher per-query cost.
    "PRAGMA cache_size=-12288;"      # ~12 MiB page cache (negative = KiB)
    "PRAGMA temp_store=FILE;"        # spill big sorts to disk, not RAM
    "PRAGMA mmap_size=134217728;"    # 128 MiB memory-mapped I/O (file-backed)
    "PRAGMA foreign_keys=ON;"
    "PRAGMA busy_timeout=5000;"
)

_tls = threading.local()

# ── Hot-swap support ────────────────────────────────────────────────────────
# Per-thread connections stay open for the worker's life (perf), so on Windows
# the DB file is locked against os.replace — that's why the startup swap only
# runs while nothing holds the file. To apply a fresh `.incoming` WITHOUT a
# restart, a background poller takes an exclusive lock, closes every live
# connection (releasing all file handles + mmap), swaps the file, then bumps a
# generation counter so each thread lazily reconnects to the new file.
#   • readers (get_db) take a shared lock → many run concurrently
#   • the swapper takes the exclusive lock → waits for readers to drain
class _RWLock:
    def __init__(self):
        self._cv = threading.Condition()
        self._readers = 0
        self._writer = False
    def acquire_read(self):
        with self._cv:
            while self._writer:
                self._cv.wait()
            self._readers += 1
    def release_read(self):
        with self._cv:
            self._readers -= 1
            if self._readers == 0:
                self._cv.notify_all()
    def acquire_write(self):
        with self._cv:
            while self._writer:
                self._cv.wait()
            self._writer = True
            while self._readers > 0:
                self._cv.wait()
    def release_write(self):
        with self._cv:
            self._writer = False
            self._cv.notify_all()

_db_rw      = _RWLock()
_db_gen     = 0                     # bumped on every hot-swap
_conn_reg   = []                   # all live connections (so the swapper can close them)
_conn_reg_lock = threading.Lock()

def _new_conn() -> sqlite3.Connection:
    con = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.executescript(_PRAGMAS)
    with _conn_reg_lock:
        _conn_reg.append(con)
    return con

@contextmanager
def get_db():
    """Per-thread persistent connection — avoids reconnect + PRAGMA cost per request.

    Wrapped in a shared (read) lock + generation check so a background hot-swap
    can atomically replace the DB file underneath us between requests.
    """
    depth = getattr(_tls, "rw_depth", 0)
    if depth == 0:
        _db_rw.acquire_read()
    _tls.rw_depth = depth + 1
    try:
        con = getattr(_tls, "con", None)
        if con is None or getattr(_tls, "con_gen", -1) != _db_gen:
            if con is not None:
                try: con.close()
                except Exception: pass
            con = _new_conn()
            _tls.con = con
            _tls.con_gen = _db_gen
        try:
            yield con
        except sqlite3.Error:
            try: con.close()
            except Exception: pass
            with _conn_reg_lock:
                try: _conn_reg.remove(con)
                except ValueError: pass
            _tls.con = None
            raise
    finally:
        _tls.rw_depth -= 1
        if _tls.rw_depth == 0:
            _db_rw.release_read()

def _apply_incoming_live() -> bool:
    """Swap a staged `.incoming` into the live DB while the app is running.

    Mirrors _maybe_swap_incoming_db() but does it under the exclusive lock with
    all connections closed first. Returns True if a swap happened.
    """
    global _db_gen
    inc = Path(str(DB_PATH) + ".incoming")
    if not inc.exists():
        return False
    # Validate the staged file before disturbing anything (cheap quick_check).
    try:
        chk = sqlite3.connect(str(inc))
        ok = chk.execute("PRAGMA quick_check").fetchone()[0]
        n  = chk.execute("SELECT COUNT(*) FROM sales_transaction").fetchone()[0]
        chk.close()
    except Exception:
        ok, n = "bad", 0
    if ok != "ok" or not n:
        try: inc.unlink()
        except OSError: pass
        return False
    # Carry forward reconciliation tables so a refresh never wipes them.
    # FAIL-CLOSED: don't hot-swap unless the carry-forward is verified intact.
    safe, reason = _carry_forward_and_verify(str(DB_PATH), str(inc))
    if not safe:
        print(f"[hot-swap] ABORTED — {reason}; live DB kept, {inc.name} left for inspection")
        return False
    _db_rw.acquire_write()
    try:
        with _conn_reg_lock:
            for c in _conn_reg:
                try: c.close()
                except Exception: pass
            _conn_reg.clear()
        _tls.con = None      # force this thread to reconnect too
        for suf in ("-wal", "-shm"):
            try: _os_db.remove(str(DB_PATH) + suf)
            except OSError: pass
        try:
            _os_db.replace(str(inc), str(DB_PATH))   # atomic; all handles now closed
        except OSError as e:
            print(f"[hot-swap] replace failed: {e}")
            return False
        _db_gen += 1
        print(f"[hot-swap] ✓ applied {inc.name} → {DB_PATH.name} (gen {_db_gen}, {n:,} bills)")
        return True
    finally:
        _db_rw.release_write()

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
# Month-range filter helper
# ---------------------------------------------------------------------------
def _month_range_filter(
    filters: list, params: dict,
    month_from: Optional[str], month_to: Optional[str],
    month: Optional[str] = None,
    col: str = "period_month",
):
    """Adds BETWEEN-style filters. `month` acts as both from and to (backward compat)."""
    mf = month_from or month
    mt = month_to   or month
    if mf:
        filters.append(f"{col} >= :month_from")
        params["month_from"] = mf
    if mt:
        filters.append(f"{col} <= :month_to")
        params["month_to"] = mt


def _apply_period_filter(
    filters: list, params: dict,
    date_from: Optional[str] = None, date_to: Optional[str] = None,
    month_from: Optional[str] = None, month_to: Optional[str] = None,
    month: Optional[str] = None,
    day_col: str = "DATE(st.sold_at)",
    month_col: str = "strftime('%Y-%m', st.sold_at)",
):
    """Apply day-level or month-level filter depending on which params are set.
    When date_from/date_to are present, filters by calendar date (day granularity).
    Otherwise falls back to month-level via _month_range_filter."""
    if date_from or date_to:
        if date_from:
            filters.append(f"{day_col} >= :date_from")
            params["date_from"] = date_from
        if date_to:
            filters.append(f"{day_col} <= :date_to")
            params["date_to"] = date_to
    else:
        _month_range_filter(filters, params, month_from, month_to, month, col=month_col)


def _eff_month_range(date_from, date_to, month_from, month_to, month):
    """When a date filter is active, return the month strings that cover that range
    (for use with monthly-aggregated tables like pos_discount_month)."""
    if date_from or date_to:
        return (date_from[:7] if date_from else None), (date_to[:7] if date_to else None)
    return (month_from or month), (month_to or month)


# ---------------------------------------------------------------------------
# Root  -  serve dashboard HTML
# ---------------------------------------------------------------------------
@app.get("/", include_in_schema=False)
def root():
    if INDEX.exists():
        return FileResponse(str(INDEX))
    return {"message": "Oshinei API running. Dashboard file not found."}


@app.get("/app.js", include_in_schema=False)
def app_js():
    """SPA application JS, split out of index.html (lean-overhaul Phase 4).
    no-cache = the browser revalidates on every load (ETag → 304 when
    unchanged), so a code deploy can never leave stale JS in caches — the
    StaticFiles /vendor mount sends no Cache-Control and relies on heuristic
    freshness, which is fine for third-party libs that never change but not
    for the app itself."""
    p = INDEX.parent / "app.js"
    if p.exists():
        return FileResponse(str(p), media_type="application/javascript",
                            headers={"Cache-Control": "no-cache"})
    raise HTTPException(status_code=404, detail="app.js not found")


# ---------------------------------------------------------------------------
# Authentication endpoints  (see backend/auth.py for the role/module model)
# ---------------------------------------------------------------------------
class _LoginBody(BaseModel):
    username: str
    password: str

class _UserCreate(BaseModel):
    username: str
    password: str
    role: str

class _UserUpdate(BaseModel):
    role: Optional[str] = None
    password: Optional[str] = None
    active: Optional[bool] = None


def _set_session_cookie(response: Response, request: Request, token: str):
    response.set_cookie(
        _auth.SESSION_COOKIE, token,
        max_age=_auth.SESSION_TTL, httponly=True, samesite="lax",
        secure=(request.url.scheme == "https"), path="/",
    )

def _require_admin(request: Request):
    """Resolve the caller's session and assert the system.admin module. When auth
    is disabled (local dev) this is a no-op so the user-admin UI works offline."""
    if not _auth.auth_enabled():
        return None
    payload = _auth.parse_token(request.cookies.get(_auth.SESSION_COOKIE, ""))
    if not payload:
        raise HTTPException(status_code=401, detail="Authentication required.")
    if "system.admin" not in _auth.modules_for_role(payload.get("r", "")):
        raise HTTPException(status_code=403, detail="ต้องเป็น Management เท่านั้น.")
    return payload


class _PwChangeBody(BaseModel):
    current_password: str
    new_password: str


def _client_ip(request: Request) -> str:
    # Behind Fly's proxy the real client is the first X-Forwarded-For hop.
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "?"


def _log_auth_event(con, action: str, username: str, ip: str = "") -> None:
    """AUD-1/AUD-6: best-effort login-event log into the shared, append-only
    recon_audit_log. Never breaks auth if the table is briefly unavailable (it's
    owned by the recon app's schema; create-if-missing so the dashboard can log
    even before recon has run)."""
    try:
        con.execute(
            "CREATE TABLE IF NOT EXISTS recon_audit_log ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL DEFAULT (datetime('now')), "
            "entity TEXT NOT NULL, entity_id INTEGER NOT NULL, action TEXT NOT NULL, "
            "field TEXT, old_val TEXT, new_val TEXT, by TEXT NOT NULL DEFAULT 'staff')")
        con.execute(
            "INSERT INTO recon_audit_log(entity, entity_id, action, field, new_val, by) "
            "VALUES ('auth', 0, ?, 'ip', ?, ?)",
            (action, ip or "", (username or "").strip()))
        con.commit()
    except Exception:
        pass


@app.post("/api/auth/login", include_in_schema=False)
def auth_login(body: _LoginBody, request: Request, response: Response):
    ip = _client_ip(request)
    if _auth.login_throttled(ip, body.username):
        raise HTTPException(status_code=429,
            detail="พยายามเข้าสู่ระบบบ่อยเกินไป — รอสักครู่แล้วลองใหม่.")
    with get_db() as con:
        row = _auth.authenticate(con, body.username, body.password)
        if not row:
            _auth.record_login_failure(ip, body.username)
            _log_auth_event(con, "login_fail", body.username, ip)
            raise HTTPException(status_code=401, detail="ชื่อผู้ใช้หรือรหัสผ่านไม่ถูกต้อง.")
        _auth.clear_login_failures(ip, body.username)
        _log_auth_event(con, "login", row["username"], ip)
    token = _auth.make_token(row["id"], row["username"], row["role"])
    _set_session_cookie(response, request, token)
    return {"username": row["username"], "role": row["role"],
            "must_change": bool(row["must_change"]),
            "modules": sorted(_auth.modules_for_role(row["role"]))}


@app.post("/api/auth/me/password", include_in_schema=False)
def auth_change_own_password(body: _PwChangeBody, request: Request):
    """Self-service password change for ANY logged-in user (verifies the current
    password). Bypasses _require_admin — it only ever affects the caller's own row."""
    if not _auth.auth_enabled():
        raise HTTPException(status_code=400, detail="auth disabled")
    payload = _auth.parse_token(request.cookies.get(_auth.SESSION_COOKIE, ""))
    if not payload:
        raise HTTPException(status_code=401, detail="Authentication required.")
    try:
        _auth.check_password_policy(body.new_password)          # AUD-1 (≥10, not the shared default)
        with get_db() as con:
            ok = _auth.change_own_password(con, payload.get("u", ""),
                                           body.current_password, body.new_password)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not ok:
        raise HTTPException(status_code=403, detail="รหัสผ่านปัจจุบันไม่ถูกต้อง.")
    with get_db() as con:
        _log_auth_event(con, "password_change", payload.get("u", ""), _client_ip(request))
    return {"ok": True}


@app.post("/api/auth/logout", include_in_schema=False)
def auth_logout(request: Request, response: Response):
    payload = _auth.parse_token(request.cookies.get(_auth.SESSION_COOKIE, ""))
    if payload:
        with get_db() as con:
            _log_auth_event(con, "logout", payload.get("u", ""), _client_ip(request))
    response.delete_cookie(_auth.SESSION_COOKIE, path="/")
    return {"ok": True}


@app.get("/api/auth/me", include_in_schema=False)
def auth_me(request: Request):
    """Frontend bootstrap. When auth is disabled, report enabled=False with the
    full module set so nothing is hidden (current behaviour preserved)."""
    if not _auth.auth_enabled():
        return {"enabled": False, "username": None, "role": "management",
                "modules": sorted(_auth.MODULES)}
    payload = _auth.parse_token(request.cookies.get(_auth.SESSION_COOKIE, ""))
    if not payload:
        return {"enabled": True, "authenticated": False, "modules": []}
    # Reflect LIVE account state, not the (up-to-12h-stale) cookie: a demoted or
    # deactivated user is reported logged-out immediately.
    with get_db() as con:
        row = _auth.get_user(con, payload.get("u", ""))
    if not row or not row["active"]:
        return {"enabled": True, "authenticated": False, "modules": []}
    return {"enabled": True, "authenticated": True,
            "username": row["username"], "role": row["role"],
            "must_change": bool(row["must_change"]),
            "modules": sorted(_auth.modules_for_role(row["role"]))}


# ── user administration (Management only — gated by middleware + _require_admin)
@app.get("/api/auth/users", include_in_schema=False)
def auth_users_list(request: Request):
    _require_admin(request)
    with get_db() as con:
        return _auth.list_users(con)


@app.post("/api/auth/users", include_in_schema=False)
def auth_users_create(body: _UserCreate, request: Request):
    _require_admin(request)
    try:
        with get_db() as con:
            if _auth.get_user(con, body.username):
                raise HTTPException(status_code=409, detail="มีชื่อผู้ใช้นี้แล้ว.")
            _auth.create_user(con, body.username, body.password, body.role)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True}


@app.put("/api/auth/users/{username}", include_in_schema=False)
def auth_users_update(username: str, body: _UserUpdate, request: Request):
    payload = _require_admin(request)
    try:
        with get_db() as con:
            target = _auth.get_user(con, username)
            if not target:
                raise HTTPException(status_code=404, detail="ไม่พบผู้ใช้.")
            # Would this change strip an active admin of their admin access?
            new_role   = body.role if body.role is not None else target["role"]
            new_active = bool(target["active"]) if body.active is None else bool(body.active)
            was_admin  = _auth.is_admin_role(target["role"]) and bool(target["active"])
            will_admin = _auth.is_admin_role(new_role) and new_active
            if was_admin and not will_admin:
                # (a) never let an admin demote/disable THEMSELVES (one-click lockout)
                if payload and payload.get("u") == username:
                    raise HTTPException(status_code=400,
                        detail="เปลี่ยนบทบาท/ปิดบัญชีผู้ดูแลของตัวเองไม่ได้.")
                # (b) never remove the LAST active admin
                if _auth.count_active_admins(con) <= 1:
                    raise HTTPException(status_code=400,
                        detail="ต้องมีผู้ดูแล (Management) ที่ใช้งานได้อย่างน้อย 1 บัญชี.")
            _auth.update_user(con, username, role=body.role,
                              password=body.password, active=body.active)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True}


@app.delete("/api/auth/users/{username}", include_in_schema=False)
def auth_users_delete(username: str, request: Request):
    payload = _require_admin(request)
    # Don't let an admin delete their own account out from under themselves.
    if payload and payload.get("u") == username:
        raise HTTPException(status_code=400, detail="ลบบัญชีตัวเองไม่ได้.")
    with get_db() as con:
        target = _auth.get_user(con, username)
        if not target:
            raise HTTPException(status_code=404, detail="ไม่พบผู้ใช้.")
        # Never delete the last active admin (would lock out user management).
        if _auth.is_admin_role(target["role"]) and bool(target["active"]) \
                and _auth.count_active_admins(con) <= 1:
            raise HTTPException(status_code=400,
                detail="ต้องมีผู้ดูแล (Management) ที่ใช้งานได้อย่างน้อย 1 บัญชี.")
        _auth.delete_user(con, username)
    return {"ok": True}


class RolePermissionsBody(BaseModel):
    permissions: Dict[str, List[str]]


@app.get("/api/auth/role-permissions", include_in_schema=False)
def auth_role_permissions_get(request: Request):
    _require_admin(request)
    with get_db() as con:
        return {
            "roles": _auth.ROLES,
            "modules": _auth.MODULES,
            "module_labels": {m: m for m in _auth.MODULES},  # frontend adds labels
            "permissions": _auth.get_role_permissions(con),
        }


@app.put("/api/auth/role-permissions", include_in_schema=False)
def auth_role_permissions_put(request: Request, body: RolePermissionsBody):
    _require_admin(request)
    with get_db() as con:
        _auth.set_role_permissions(con, body.permissions)
    return {"ok": True}


# ---------------------------------------------------------------------------
# DB refresh/seed over HTTPS (hosted deploys). Accepts a gzipped SQLite file —
# far more reliable than the WireGuard SFTP tunnel for large files. Used both to
# seed an empty volume and to refresh an existing one with newer data.
#
# Safe by design:
#   • token-gated — requires env OSHINEI_SEED_TOKEN set AND matching ?token=
#     (unset the Fly secret to disable the endpoint entirely → 404).
#   • the upload is STAGED to <DB_PATH>.incoming and validated (integrity_check
#     + non-empty sales_transaction); the live DB is never touched here.
#   • _maybe_swap_incoming_db() atomically swaps it in at the next startup, so a
#     bad/partial upload can never corrupt the running DB. Caller restarts the
#     machine after a successful upload (see backend/refresh_fly_db.py).
# ---------------------------------------------------------------------------
@app.post("/api/_admin/seed-db", include_in_schema=False)
async def _seed_db(file: UploadFile = File(...), token: str = Query("")):
    import gzip, shutil, tempfile, os as _os2
    toks = _accepted_tokens()
    if not toks or not _token_accepted(token):
        raise HTTPException(status_code=404, detail="not found")
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
    try:
        chk = sqlite3.connect(stage)
        ok  = chk.execute("PRAGMA integrity_check").fetchone()[0]
        rows = chk.execute("SELECT COUNT(*) FROM sales_transaction").fetchone()[0]
        chk.close()
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
# /api/branches
# ---------------------------------------------------------------------------
@app.get("/api/branches")
def list_branches(
    group: Optional[str] = Query(None, description="'master' | 'franchise' | None"),
    include_inactive: bool = Query(False),
):
    """All branch names (excluding the synthetic ALL branch). Optionally filter by branch_type."""
    sql = "SELECT branch_id, name, COALESCE(branch_type,'master') AS branch_type, COALESCE(active,1) AS active FROM branches WHERE name != 'ALL'"
    params: list = []
    if not include_inactive:
        sql += " AND COALESCE(active,1) = 1"
    if group:
        sql += " AND COALESCE(branch_type,'master') = ?"
        params.append(group)
    sql += " ORDER BY name"
    with get_db() as con:
        rows = con.execute(sql, params).fetchall()
    return [{"id": r["branch_id"], "name": r["name"], "branch_type": r["branch_type"], "active": bool(r["active"])} for r in rows]


class BranchTypeUpdate(BaseModel):
    branch_type: str   # 'master' | 'franchise'


@app.put("/api/branches/{branch_id}/type", dependencies=[Depends(require_write_token)])
def update_branch_type(branch_id: int, body: BranchTypeUpdate):
    if body.branch_type not in ("master", "franchise"):
        raise HTTPException(400, "branch_type must be 'master' or 'franchise'")
    with get_db() as con:
        con.execute("UPDATE branches SET branch_type=? WHERE branch_id=?", (body.branch_type, branch_id))
        con.commit()
    invalidate_cache()
    return {"ok": True}


def _group_filter(filters: list, params: dict, group: Optional[str]):
    """Adds a branch-type filter (b.branch_type = :group). Caller must JOIN branches as b."""
    if group:
        filters.append("COALESCE(b.branch_type,'master') = :branch_group")
        params["branch_group"] = group


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


def _txn_fallback_counts(con, months, branch, group, source):
    """tx_count/customer_count for specific months, straight from sales_transaction.

    sales_month_summary is only populated by the manual Excel-ingest scripts —
    the live MSSQL sync (sync_mssql.py) writes sales_sku_monthly/sales_transaction
    but never rebuilds sales_month_summary. A month that has only ever been
    live-synced (e.g. the current/most-recent month) shows real revenue but a
    NULL/zero tx_count and customer_count without this fallback. Restricted to
    the specific missing months so it doesn't reintroduce the full-table scan
    the month-summary rollup was added to avoid.
    """
    if not months:
        return {}
    filters = ["b.name != 'ALL'", "strftime('%Y-%m', st.sold_at) IN ({})".format(
        ",".join(f":m{i}" for i in range(len(months))))]
    params = {f"m{i}": mo for i, mo in enumerate(months)}
    if branch:
        filters.append("b.name = :branch")
        params["branch"] = branch
    _group_filter(filters, params, group)
    if source:
        filters.append("st.source = :source")
        params["source"] = source
    where = "WHERE " + " AND ".join(filters)
    rows = con.execute(
        f"""
        SELECT strftime('%Y-%m', st.sold_at) AS period_month,
               COUNT(*)               AS tx_count,
               SUM(st.customer_count) AS customer_count
        FROM sales_transaction st
        JOIN branches b ON b.branch_id = st.branch_id
        {where}
        GROUP BY period_month
        """,
        params,
    ).fetchall()
    return {r["period_month"]: dict(r) for r in rows}


# ---------------------------------------------------------------------------
# /api/cost   -  GET: list all SKUs with current cost; POST: upsert costs
# ---------------------------------------------------------------------------
class CostEntry(BaseModel):
    sku_code: str
    unit_cost: float








@app.get("/api/cost")
def get_cost():
    """All SKUs with their current unit_cost (NULL if not yet set). Sorted category, sku_code."""
    with get_db() as con:
        rows = con.execute(
            """
            SELECT sk.sku_id, sk.sku_code, sk.description,
                   c.name AS category,
                   sc.unit_cost, sc.updated_at
            FROM skus sk
            JOIN categories c ON c.category_id = sk.category_id
            LEFT JOIN sku_cost sc ON sc.sku_id = sk.sku_id
            ORDER BY c.name, sk.sku_code
            """
        ).fetchall()
    return [
        {
            "sku_id":      r["sku_id"],
            "sku_code":    r["sku_code"],
            "description": r["description"],
            "category":    r["category"],
            "unit_cost":   r["unit_cost"],
            "updated_at":  r["updated_at"],
        }
        for r in rows
    ]


@app.post("/api/cost", dependencies=[Depends(require_write_token)])
def post_cost(entries: List[CostEntry]):
    """Upsert unit costs by sku_code. Returns count of rows updated.

    GUARD (2026-06-01): rejects any non-tier entry whose unit_cost lands in the
    price-as-cost band vs the SKU's ex-VAT selling price (ratio 1.03–1.12 ≈ the
    VAT multiplier). This is the ONLY code path that writes sku_cost, so it's
    where re-uploading the price spreadsheet as "costs" reintroduces the bad
    data. Such rows are skipped and reported instead of silently corrupting
    every margin number. Tier SKUs (BUFFET*) are exempt — their cost is ignored
    by _cogs_case anyway.
    """
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    updated = 0
    skipped: list = []
    with get_db() as con:
        cur = con.cursor()
        # Pre-compute ex-VAT avg selling price for the incoming SKUs (one query).
        codes = [e.sku_code for e in entries]
        price_by_code: dict = {}
        if codes:
            ph = ",".join("?" for _ in codes)
            for code, q, rev in cur.execute(
                f"""SELECT sk.sku_code, SUM(ssm.qty), SUM(ssm.subtotal_ex_vat)
                    FROM skus sk JOIN sales_sku_monthly ssm ON ssm.sku_id = sk.sku_id
                    WHERE sk.sku_code IN ({ph})
                    GROUP BY sk.sku_code
                    HAVING SUM(ssm.qty) > 0 AND SUM(ssm.subtotal_ex_vat) > 0""",
                codes,
            ).fetchall():
                price_by_code[code] = (rev or 0) / (q or 1)
        for entry in entries:
            row = cur.execute(
                "SELECT sku_id FROM skus WHERE sku_code = ?", (entry.sku_code,)
            ).fetchone()
            if row is None:
                continue
            sku_id = row[0]
            is_tier = str(entry.sku_code).upper().startswith("BUFFET")
            price = price_by_code.get(entry.sku_code)
            if not is_tier and is_price_as_cost(entry.unit_cost, price):
                skipped.append({
                    "sku_code":  entry.sku_code,
                    "unit_cost": round(entry.unit_cost, 2),
                    "ex_vat_price": round(price, 2),
                    "ratio":     round(entry.unit_cost / price, 3),
                })
                continue   # looks like price-as-cost — do NOT write
            cur.execute(
                """INSERT OR REPLACE INTO sku_cost(sku_id, unit_cost, effective_from, updated_at)
                   VALUES(?, ?, ?, ?)""",
                (sku_id, entry.unit_cost, now[:10], now),
            )
            updated += 1
        con.commit()
    invalidate_cache()   # cost data changed — clear all cached analytics responses
    resp = {"updated": updated}
    if skipped:
        resp["skipped_price_as_cost"] = len(skipped)
        resp["skipped_samples"] = skipped[:10]
        resp["warning"] = (
            f"{len(skipped)} entr{'y' if len(skipped)==1 else 'ies'} skipped: "
            "the value matched the ex-VAT selling price × ~1.07, i.e. it looks "
            "like the PRICE was entered as cost. Enter the real per-unit cost."
        )
    return resp


# ---------------------------------------------------------------------------
# Phase 8 — monthly per-SKU unit_cost overrides
# ---------------------------------------------------------------------------
class MonthlyCostEntry(BaseModel):
    sku_code: str
    period_month: str        # 'YYYY-MM'
    unit_cost: float
    notes: Optional[str] = None


def _resolve_effective_monthly_cost(con, sku_id: int, month: str):
    """Python-side mirror of _effective_cost_subquery for a single (sku, month).

    Returns (unit_cost or None, source) where source ∈
    {'monthly','inherited','default','none'}.
    """
    row = con.execute(
        "SELECT unit_cost FROM sku_cost_monthly "
        "WHERE sku_id = ? AND period_month = ?",
        (sku_id, month),
    ).fetchone()
    if row is not None:
        return row[0], "monthly"
    row = con.execute(
        "SELECT unit_cost FROM sku_cost_monthly "
        "WHERE sku_id = ? AND period_month < ? "
        "ORDER BY period_month DESC LIMIT 1",
        (sku_id, month),
    ).fetchone()
    if row is not None:
        return row[0], "inherited"
    row = con.execute(
        "SELECT unit_cost FROM sku_cost WHERE sku_id = ?", (sku_id,)
    ).fetchone()
    if row is not None:
        return row[0], "default"
    return None, "none"


@app.get("/api/cost/monthly")
def get_cost_monthly(
    sku:         Optional[str] = Query(None, description="sku_code filter"),
    month:       Optional[str] = Query(None, description="YYYY-MM filter"),
    period_from: Optional[str] = Query(None),
    period_to:   Optional[str] = Query(None),
):
    """List monthly unit_cost overrides.

    - If ``sku`` is given → return every monthly row for that SKU plus the lifetime
      default (as a synthetic row with ``period_month=null`` and ``source='default'``).
    - If ``month`` is given (without sku) → for every costed SKU return its effective
      cost in that month with the source (monthly/inherited/default).
    - Otherwise → raw dump of sku_cost_monthly within the period_from/period_to window.
    """
    out = []
    with get_db() as con:
        if sku:
            sku_row = con.execute(
                "SELECT sku_id, description FROM skus WHERE sku_code = ?", (sku,)
            ).fetchone()
            if sku_row is None:
                raise HTTPException(404, f"Unknown sku_code: {sku}")
            sku_id  = sku_row[0]
            default_row = con.execute(
                "SELECT unit_cost FROM sku_cost WHERE sku_id = ?", (sku_id,)
            ).fetchone()
            default_cost = default_row[0] if default_row else None

            monthly_rows = con.execute(
                "SELECT period_month, unit_cost, notes, updated_at "
                "FROM sku_cost_monthly WHERE sku_id = ? "
                "ORDER BY period_month",
                (sku_id,),
            ).fetchall()
            for r in monthly_rows:
                out.append({
                    "sku_code":     sku,
                    "description":  sku_row[1],
                    "period_month": r[0],
                    "unit_cost":    r[1],
                    "notes":        r[2],
                    "updated_at":   r[3],
                    "default_cost": default_cost,
                    "source":       "monthly",
                })
            # always include the lifetime default as a reference row
            out.append({
                "sku_code":     sku,
                "description":  sku_row[1],
                "period_month": None,
                "unit_cost":    default_cost,
                "notes":        None,
                "updated_at":   None,
                "default_cost": default_cost,
                "source":       "default",
            })
            return out

        if month:
            rows = con.execute(
                """
                SELECT sk.sku_id, sk.sku_code, sk.description,
                       sc.unit_cost AS default_cost,
                       scm.unit_cost AS monthly_cost,
                       scm.notes, scm.updated_at
                FROM skus sk
                LEFT JOIN sku_cost sc          ON sc.sku_id = sk.sku_id
                LEFT JOIN sku_cost_monthly scm ON scm.sku_id = sk.sku_id
                                              AND scm.period_month = ?
                WHERE sc.unit_cost IS NOT NULL OR scm.unit_cost IS NOT NULL
                ORDER BY sk.sku_code
                """,
                (month,),
            ).fetchall()
            for r in rows:
                eff, src = _resolve_effective_monthly_cost(con, r["sku_id"], month)
                out.append({
                    "sku_code":     r["sku_code"],
                    "description":  r["description"],
                    "period_month": month,
                    "unit_cost":    eff,
                    "default_cost": r["default_cost"],
                    "notes":        r["notes"],
                    "updated_at":   r["updated_at"],
                    "source":       src,
                })
            return out

        # raw window dump
        where, params = [], {}
        if period_from:
            where.append("scm.period_month >= :pf"); params["pf"] = period_from
        if period_to:
            where.append("scm.period_month <= :pt"); params["pt"] = period_to
        wsql = ("WHERE " + " AND ".join(where)) if where else ""
        rows = con.execute(
            f"""
            SELECT sk.sku_code, sk.description, scm.period_month,
                   scm.unit_cost, scm.notes, scm.updated_at,
                   sc.unit_cost AS default_cost
            FROM sku_cost_monthly scm
            JOIN skus sk         ON sk.sku_id = scm.sku_id
            LEFT JOIN sku_cost sc ON sc.sku_id = scm.sku_id
            {wsql}
            ORDER BY scm.period_month, sk.sku_code
            """,
            params,
        ).fetchall()
        return [
            {
                "sku_code":     r["sku_code"],
                "description":  r["description"],
                "period_month": r["period_month"],
                "unit_cost":    r["unit_cost"],
                "default_cost": r["default_cost"],
                "notes":        r["notes"],
                "updated_at":   r["updated_at"],
                "source":       "monthly",
            }
            for r in rows
        ]


@app.post("/api/cost/monthly", dependencies=[Depends(require_write_token)])
def post_cost_monthly(entries: List[MonthlyCostEntry]):
    """Upsert monthly per-SKU unit_cost overrides.

    Returns {written, unknown_skus, invalid_months}.
    """
    import re
    month_re = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
    written = 0
    unknown: List[str] = []
    invalid_months: List[str] = []
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    with get_db() as con:
        cur = con.cursor()
        for e in entries:
            if not month_re.match(e.period_month or ""):
                invalid_months.append(f"{e.sku_code}:{e.period_month}")
                continue
            row = cur.execute(
                "SELECT sku_id FROM skus WHERE sku_code = ?", (e.sku_code,)
            ).fetchone()
            if row is None:
                unknown.append(e.sku_code)
                continue
            sku_id = row[0]
            cur.execute(
                """INSERT INTO sku_cost_monthly(sku_id, period_month, unit_cost, notes, updated_at)
                   VALUES(?,?,?,?,?)
                   ON CONFLICT(sku_id, period_month) DO UPDATE SET
                       unit_cost  = excluded.unit_cost,
                       notes      = excluded.notes,
                       updated_at = excluded.updated_at""",
                (sku_id, e.period_month, e.unit_cost, e.notes, now),
            )
            written += 1
        con.commit()
    invalidate_cache()
    return {
        "written":        written,
        "unknown_skus":   sorted(set(unknown)),
        "invalid_months": invalid_months,
    }


@app.delete("/api/cost/monthly", dependencies=[Depends(require_write_token)])
def delete_cost_monthly(
    sku:   str = Query(..., description="sku_code"),
    month: str = Query(..., description="YYYY-MM"),
):
    """Remove a single monthly override row."""
    with get_db() as con:
        row = con.execute(
            "SELECT sku_id FROM skus WHERE sku_code = ?", (sku,)
        ).fetchone()
        if row is None:
            raise HTTPException(404, f"Unknown sku_code: {sku}")
        cur = con.execute(
            "DELETE FROM sku_cost_monthly WHERE sku_id = ? AND period_month = ?",
            (row[0], month),
        )
        con.commit()
        deleted = cur.rowcount
    invalidate_cache()
    return {"deleted": deleted}


@app.get("/api/cost/monthly/template")
def cost_monthly_template(month: str = Query(..., description="YYYY-MM")):
    """CSV template of every costed SKU with its current default + monthly cost (if any).

    Columns: sku_code, description, default_unit_cost, monthly_unit_cost, notes
    """
    import io, csv, re
    if not re.match(r"^\d{4}-(0[1-9]|1[0-2])$", month or ""):
        raise HTTPException(400, "month must be YYYY-MM")
    with get_db() as con:
        rows = con.execute(
            """
            SELECT sk.sku_code,
                   COALESCE(sk.description, sk.sku_code) AS description,
                   sc.unit_cost  AS default_unit_cost,
                   scm.unit_cost AS monthly_unit_cost,
                   scm.notes
            FROM skus sk
            LEFT JOIN sku_cost sc          ON sc.sku_id = sk.sku_id
            LEFT JOIN sku_cost_monthly scm ON scm.sku_id = sk.sku_id
                                          AND scm.period_month = ?
            WHERE sc.unit_cost IS NOT NULL OR scm.unit_cost IS NOT NULL
            ORDER BY sk.sku_code
            """,
            (month,),
        ).fetchall()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["sku_code", "description", "default_unit_cost", "monthly_unit_cost", "notes"])
    for r in rows:
        w.writerow([
            r["sku_code"],
            r["description"] or "",
            "" if r["default_unit_cost"] is None else r["default_unit_cost"],
            "" if r["monthly_unit_cost"] is None else r["monthly_unit_cost"],
            r["notes"] or "",
        ])
    from fastapi.responses import Response as _R
    return _R(
        content=buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="cost_monthly_{month}.csv"'},
    )


def _iso_week_sql(col: str) -> str:
    """SQLite expression producing an ISO-8601 week label 'YYYY-Www' for a date column.

    Matches Python's date.isocalendar() exactly (verified across year boundaries),
    unlike strftime('%Y-W%W', ...) which mis-buckets the days straddling Jan 1.
    The ISO week-year is the year of that week's Thursday = date(col,'-3 days','weekday 4').
    """
    thu = f"date({col},'-3 days','weekday 4')"
    return (
        f"strftime('%Y', {thu}) || '-W' || "
        f"substr('0' || ((strftime('%j', {thu}) - 1) / 7 + 1), -2)"
    )


def _next_period_month(ym: str, offset: int) -> str:
    """Add `offset` months to a YYYY-MM string."""
    y, m = _ym_parts(ym)
    m += offset
    while m > 12:
        m -= 12
        y += 1
    return f"{y:04d}-{m:02d}"


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
    token: str = Query(""),
    x_upload_token: str = Header(None),
):
    """Accept a multipart Easy data upload, ingest it, return a summary.

    AUTH: when OSHINEI_SEED_TOKEN is set (hosted deploys), a matching token is
    required via the `X-Upload-Token` header or `?token=` — otherwise this
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
        provided = x_upload_token or token or ""
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
    except Exception as exc:
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


# ---------------------------------------------------------------------------
# /api/closed_hours   -  operating hours per branch per day-of-week
# ---------------------------------------------------------------------------
class ClosedHoursEntry(BaseModel):
    branch_id: int
    dow:       int   # 0=Sun â€¦ 6=Sat
    hour_from: int   # 0..23 inclusive (open hour)
    hour_to:   int   # 0..23 inclusive (last open hour)
    is_closed: bool = False


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


@app.get("/api/closed_hours")
def get_closed_hours():
    with get_db() as con:
        rows = con.execute(
            """
            SELECT b.branch_id, b.name AS branch, ch.dow,
                   COALESCE(ch.hour_from, 10)  AS hour_from,
                   COALESCE(ch.hour_to,   21)  AS hour_to,
                   COALESCE(ch.is_closed, 0)   AS is_closed
            FROM branches b
            LEFT JOIN closed_hours ch ON ch.branch_id = b.branch_id
            WHERE b.name != 'ALL'
            ORDER BY b.name, ch.dow
            """
        ).fetchall()
    return [dict(r) for r in rows]


@app.put("/api/closed_hours", dependencies=[Depends(require_write_token)])
def put_closed_hours(entries: List[ClosedHoursEntry]):
    # fix D12: validate ranges before touching the DB
    for e in entries:
        if not (0 <= e.dow <= 6):
            raise HTTPException(400, f"dow must be 0-6, got {e.dow}")
        if not (0 <= e.hour_from <= 23):
            raise HTTPException(400, f"hour_from must be 0-23, got {e.hour_from}")
        if not (0 <= e.hour_to <= 23):
            raise HTTPException(400, f"hour_to must be 0-23, got {e.hour_to}")
    with get_db() as con:
        cur = con.cursor()
        for e in entries:
            cur.execute(
                """INSERT OR REPLACE INTO closed_hours
                       (branch_id, dow, hour_from, hour_to, is_closed)
                   VALUES(?,?,?,?,?)""",
                (e.branch_id, e.dow, e.hour_from, e.hour_to, 1 if e.is_closed else 0),
            )
        con.commit()
    return {"ok": True, "saved": len(entries)}


@app.get("/api/cost/diff")
@ttl_cache
def cost_diff(threshold_pct: float = Query(10.0, description="Flag changes >= this %")):
    """List SKUs whose unit_cost was recently updated and the change vs prior value (rough)."""
    # We don't have cost history  -  return SKUs with cost recently set as a placeholder
    with get_db() as con:
        rows = con.execute(
            """
            SELECT sk.sku_code, COALESCE(sk.description, sk.sku_code) AS description,
                   sc.unit_cost, sc.updated_at
            FROM sku_cost sc JOIN skus sk ON sk.sku_id = sc.sku_id
            WHERE sc.updated_at IS NOT NULL
            ORDER BY sc.updated_at DESC
            LIMIT 20
            """).fetchall()
    return {"recent_updates": [dict(r) for r in rows], "note": "Cost history not yet tracked  -  showing 20 most recently updated SKUs."}


# ===========================================================================
# SETTINGS  -  new tables + endpoints
# ===========================================================================

# ---------------------------------------------------------------------------
# Ensure new tables exist (runs once on import, safe if DB already has them)
# ---------------------------------------------------------------------------
def _init_settings_tables():
    if not DB_PATH.exists():
        return
    with get_db() as con:
        # Bootstrap core schema if the DB is empty/partial (e.g. a fresh hosted
        # volume). schema.sql is shipped beside this file and is fully idempotent
        # (CREATE TABLE IF NOT EXISTS …). On a populated DB `branches` already
        # exists, so this is skipped and behaviour is unchanged.
        has_branches = con.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='branches'"
        ).fetchone()
        if not has_branches:
            schema_path = HERE / "schema.sql"
            if schema_path.exists():
                con.executescript(schema_path.read_text(encoding="utf-8"))
        con.executescript("""
            CREATE TABLE IF NOT EXISTS branch_targets (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                branch_id       INTEGER NOT NULL REFERENCES branches(branch_id),
                period_month    TEXT    NOT NULL,
                revenue_target  REAL    DEFAULT 0,
                customer_target INTEGER DEFAULT 0,
                UNIQUE(branch_id, period_month)
            );
            CREATE TABLE IF NOT EXISTS app_settings (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL DEFAULT ''
            );
        """)
        # branch_type migration (idempotent). Guarded so a DB still missing the
        # core tables (schema.sql absent) can't crash import.
        branch_cols = [r[1] for r in con.execute("PRAGMA table_info(branches)").fetchall()]
        cols = branch_cols
        if branch_cols and "branch_type" not in branch_cols:
            con.execute("ALTER TABLE branches ADD COLUMN branch_type TEXT DEFAULT 'master'")
        if branch_cols and "active" not in branch_cols:
            con.execute("ALTER TABLE branches ADD COLUMN active INTEGER DEFAULT 1")
        # skus.active migration (idempotent)
        sku_cols = [r[1] for r in con.execute("PRAGMA table_info(skus)").fetchall()]
        if sku_cols and "active" not in sku_cols:
            con.execute("ALTER TABLE skus ADD COLUMN active INTEGER DEFAULT 1")
        # per-bill discount reason + coupon series (idempotent) — powers the
        # cashier-discount drill "ประเภทส่วนลด/คูปอง" column. Populated by sync_mssql.
        st_cols = [r[1] for r in con.execute("PRAGMA table_info(sales_transaction)").fetchall()]
        if st_cols and "discount_reason_id" not in st_cols:
            con.execute("ALTER TABLE sales_transaction ADD COLUMN discount_reason_id INTEGER")
        if st_cols and "coupon_series_id" not in st_cols:
            con.execute("ALTER TABLE sales_transaction ADD COLUMN coupon_series_id INTEGER")
        if st_cols and "print_times" not in st_cols:
            con.execute("ALTER TABLE sales_transaction ADD COLUMN print_times INTEGER DEFAULT 0")
        # per-bill sitting duration (open→close, minutes) — populated by sync_mssql
        # from SaleTab. Powers the "ระยะเวลานั่ง" column on the loss-bill table.
        if st_cols and "dwell_min" not in st_cols:
            con.execute("ALTER TABLE sales_transaction ADD COLUMN dwell_min INTEGER")
        for k, v in [
            ("default_branch", ""),
            ("default_month",  "latest"),
            ("fiscal_year_start", "1"),
        ]:
            con.execute(
                "INSERT OR IGNORE INTO app_settings(key,value) VALUES(?,?)", (k, v)
            )
        con.commit()

_init_settings_tables()
# Load dynamic role permissions into memory cache (falls back to hardcoded ROLE_MODULES if not stored)
with get_db() as _boot_con:
    _auth.refresh_perm_cache(_boot_con)


# ---------------------------------------------------------------------------
# Pydantic models for settings endpoints
# ---------------------------------------------------------------------------
class TargetEntry(BaseModel):
    branch: str
    period_month: str
    revenue_target: float = 0
    customer_target: int = 0

class BranchUpdate(BaseModel):
    name: str

class ActiveUpdate(BaseModel):
    active: bool


# ---------------------------------------------------------------------------
# /api/settings   -  GET: all key-value prefs; PUT: bulk upsert
# ---------------------------------------------------------------------------
@app.get("/api/settings")
def get_settings():
    with get_db() as con:
        rows = con.execute("SELECT key, value FROM app_settings").fetchall()
    return {r["key"]: r["value"] for r in rows}


@app.put("/api/settings", dependencies=[Depends(require_write_token)])
def put_settings(body: Dict[str, str]):
    with get_db() as con:
        cur = con.cursor()
        for k, v in body.items():
            cur.execute(
                "INSERT OR REPLACE INTO app_settings(key,value) VALUES(?,?)", (k, str(v))
            )
        con.commit()
    # settings feed cached endpoints (e.g. discount_alerts thresholds) → drop stale
    invalidate_cache("analysis_discount_alerts")
    return {"ok": True}


# ---------------------------------------------------------------------------
# /api/branches/{id}   -  PUT: rename a branch
# ---------------------------------------------------------------------------
@app.put("/api/branches/{branch_id}", dependencies=[Depends(require_write_token)])
def update_branch(branch_id: int, body: BranchUpdate):
    with get_db() as con:
        clash = con.execute(
            "SELECT branch_id FROM branches WHERE name=? AND branch_id!=?",
            (body.name, branch_id),
        ).fetchone()
        if clash:
            raise HTTPException(400, "Branch name already in use")
        con.execute(
            "UPDATE branches SET name=? WHERE branch_id=?", (body.name, branch_id)
        )
        con.commit()
    return {"ok": True}


@app.put("/api/branches/{branch_id}/active", dependencies=[Depends(require_write_token)])
def update_branch_active(branch_id: int, body: ActiveUpdate):
    with get_db() as con:
        con.execute(
            "UPDATE branches SET active=? WHERE branch_id=?",
            (1 if body.active else 0, branch_id),
        )
        con.commit()
    return {"ok": True}


# ---------------------------------------------------------------------------
# /api/skus   -  list all SKUs; PUT active status
# ---------------------------------------------------------------------------
@app.get("/api/skus")
def list_skus(include_inactive: bool = Query(False)):
    sql = """
        SELECT sk.sku_id, sk.sku_code, sk.description,
               COALESCE(c.name,'') AS category,
               COALESCE(sk.active,1) AS active
        FROM skus sk
        LEFT JOIN categories c ON c.category_id = sk.category_id
        WHERE 1=1
    """
    params: list = []
    if not include_inactive:
        sql += " AND COALESCE(sk.active,1) = 1"
    sql += " ORDER BY sk.sku_code"
    with get_db() as con:
        rows = con.execute(sql, params).fetchall()
    return [
        {
            "sku_code": r["sku_code"],
            "description": r["description"],
            "category": r["category"],
            "active": bool(r["active"]),
        }
        for r in rows
    ]


@app.put("/api/skus/{sku_code}/active", dependencies=[Depends(require_write_token)])
def update_sku_active(sku_code: str, body: ActiveUpdate):
    with get_db() as con:
        updated = con.execute(
            "UPDATE skus SET active=? WHERE sku_code=?",
            (1 if body.active else 0, sku_code),
        ).rowcount
        con.commit()
    if updated == 0:
        raise HTTPException(404, "SKU not found")
    return {"ok": True}


# ---------------------------------------------------------------------------
# /api/targets   -  GET: targets for a month (all branches); PUT: upsert
# ---------------------------------------------------------------------------
@app.get("/api/targets")
def get_targets(month: Optional[str] = Query(None)):
    """Returns all branches with their targets for the given month (0 if unset)."""
    params: dict = {"month": month or ""}
    month_join = "AND bt.period_month = :month" if month else ""

    with get_db() as con:
        rows = con.execute(
            f"""
            SELECT b.branch_id,
                   b.name AS branch,
                   COALESCE(bt.period_month, :month) AS period_month,
                   COALESCE(bt.revenue_target,  0)   AS revenue_target,
                   COALESCE(bt.customer_target, 0)   AS customer_target
            FROM branches b
            LEFT JOIN branch_targets bt
                   ON bt.branch_id = b.branch_id {month_join}
            WHERE b.name != 'ALL'
            ORDER BY b.name
            """,
            params,
        ).fetchall()
    return [dict(r) for r in rows]


@app.get("/api/targets/suggest")
def suggest_targets(
    month:    Optional[str] = Query(None, description="target month (default: latest data month)"),
    uplift:   float = Query(1.05, ge=0.5, le=3.0, description="stretch factor vs trailing avg"),
    lookback: int   = Query(3, ge=1, le=12),
):
    """Suggest per-branch revenue/customer targets from each branch's trailing
    `lookback`-month average × `uplift`. Read-only — the client fills the editor so
    the owner reviews before saving. Seeds the (currently empty) branch_targets."""
    with get_db() as con:
        latest = con.execute("SELECT MAX(period_month) m FROM sales_sku_monthly").fetchone()["m"]
        target_month = month or latest
        mrows = con.execute(
            "SELECT DISTINCT period_month FROM sales_sku_monthly WHERE period_month < ? "
            "ORDER BY period_month DESC LIMIT ?", (target_month, lookback)).fetchall()
        mlist = [r["period_month"] for r in mrows]
        if not mlist:
            return {"month": target_month, "uplift": uplift, "lookback": 0,
                    "trailing_months": [], "suggestions": []}
        ph = ",".join("?" * len(mlist))
        rev = con.execute(
            f"""SELECT b.name br, AVG(m.rev) avg_rev
                  FROM (SELECT branch_id, period_month, SUM(subtotal_ex_vat) rev
                          FROM sales_sku_monthly WHERE period_month IN ({ph})
                         GROUP BY branch_id, period_month) m
                  JOIN branches b ON b.branch_id = m.branch_id
                 WHERE b.name != 'ALL' AND COALESCE(b.active,1) = 1
                 GROUP BY b.name""", mlist).fetchall()
        cust = con.execute(
            f"""SELECT b.name br, AVG(m.c) avg_c
                  FROM (SELECT branch_id, strftime('%Y-%m', sold_at) pm, SUM(customer_count) c
                          FROM sales_transaction WHERE source='Easy'
                           AND strftime('%Y-%m', sold_at) IN ({ph})
                         GROUP BY branch_id, pm) m
                  JOIN branches b ON b.branch_id = m.branch_id
                 WHERE b.name != 'ALL' GROUP BY b.name""", mlist).fetchall()
    cmap = {r["br"]: (r["avg_c"] or 0) for r in cust}
    out = [{
        "branch": r["br"],
        "revenue_target":  round((r["avg_rev"] or 0) * uplift, -3),   # nearest ฿1,000
        "customer_target": int(round(cmap.get(r["br"], 0) * uplift)),
    } for r in rev]
    out.sort(key=lambda x: -x["revenue_target"])
    return {"month": target_month, "uplift": uplift, "lookback": len(mlist),
            "trailing_months": mlist, "suggestions": out}


@app.put("/api/targets", dependencies=[Depends(require_write_token)])
def put_targets(entries: List[TargetEntry]):
    with get_db() as con:
        cur = con.cursor()
        saved = 0
        for e in entries:
            row = cur.execute(
                "SELECT branch_id FROM branches WHERE name=?", (e.branch,)
            ).fetchone()
            if row is None:
                continue
            cur.execute(
                """INSERT OR REPLACE INTO branch_targets
                       (branch_id, period_month, revenue_target, customer_target)
                   VALUES(?,?,?,?)""",
                (row[0], e.period_month, e.revenue_target, e.customer_target),
            )
            saved += 1
        con.commit()
    return {"ok": True, "saved": saved}


# ---------------------------------------------------------------------------
# /api/cost/detail   -  searchable SKU cost list with revenue stats
# ---------------------------------------------------------------------------
@app.get("/api/cost/detail")
@ttl_cache
def get_cost_detail(
    q:        Optional[str] = Query(None),
    category: Optional[str] = Query(None),
    priced:   Optional[str] = Query(None, description="'yes'|'no'  -  has unit_cost set"),
    min_rev:  Optional[float] = Query(None, description="min total_revenue"),
    sort:     Optional[str] = Query(None, description="code|revenue|qty|cost"),
    business_type: Optional[str] = Query(None, description="filter by skus.business_type"),
    limit:    int           = Query(80, ge=1, le=500),
    offset:   int           = Query(0,  ge=0),
):
    filters: List[str] = []
    params: dict = {"limit": limit, "offset": offset}
    if q:
        filters.append(
            "(LOWER(sk.sku_code) LIKE :q OR LOWER(COALESCE(sk.description,'')) LIKE :q)"
        )
        params["q"] = f"%{q.lower()}%"
    if category:
        filters.append("c.name = :category")
        params["category"] = category
    if priced == "yes":
        filters.append("sc.unit_cost IS NOT NULL")
    elif priced == "no":
        filters.append("sc.unit_cost IS NULL")
    if business_type:
        filters.append("COALESCE(sk.business_type,'other') = :business_type")
        params["business_type"] = business_type
    sort_clause = "ORDER BY c.name, sk.sku_code"
    if sort == "revenue":
        sort_clause = "ORDER BY total_revenue DESC NULLS LAST"
    elif sort == "qty":
        sort_clause = "ORDER BY total_qty DESC NULLS LAST"
    elif sort == "cost":
        sort_clause = "ORDER BY sc.unit_cost DESC NULLS LAST"
    elif sort == "code":
        sort_clause = "ORDER BY sk.sku_code"
    having_clause = ""
    if min_rev is not None:
        having_clause = "HAVING total_revenue >= :min_rev"
        params["min_rev"] = min_rev

    # SCOPE (2026-06-01): Cost Management lists ONLY menu items actually sold via
    # Easy. This excludes ~845 legacy / CM-POS-era / never-sold active SKUs that
    # are not part of the current (Easy-only since 2026-05-27) menu and would
    # otherwise clutter the cost worklist. Enforced by requiring an Easy sales row.
    filters.append(
        "EXISTS (SELECT 1 FROM sales_sku_monthly s "
        "WHERE s.sku_id = sk.sku_id AND s.source = 'Easy')"
    )
    where = ("WHERE " + " AND ".join(filters)) if filters else ""
    cnt_params = {k: v for k, v in params.items() if k not in ("limit", "offset")}

    with get_db() as con:
        rows = con.execute(
            f"""
            SELECT sk.sku_id, sk.sku_code,
                   COALESCE(sk.description,'') AS description,
                   c.name AS category,
                   COALESCE(sk.business_type,'buffet') AS business_type,  -- buffet-first default (85% of business)
                   sc.unit_cost, sc.updated_at,
                   -- Effective COGS the rest of the app actually uses (BoM recipe →
                   -- alias BoM → sku_cost → 0). Lets the worklist show a real cost for
                   -- the ~98 recipe-costed SKUs whose manual sc.unit_cost is NULL, so
                   -- they no longer read as "uncosted" when they're costed via BoM.
                   (SELECT sec.recipe_cost FROM sku_effective_cost sec
                      WHERE sec.sku_id = sk.sku_id) AS effective_cost,
                   ROUND(SUM(CASE WHEN ssm.source='Easy' THEN ssm.subtotal_ex_vat END), 0) AS total_revenue,
                   ROUND(SUM(CASE WHEN ssm.source='Easy' THEN ssm.qty END), 0)            AS total_qty
            FROM skus sk
            JOIN  categories c   ON c.category_id = sk.category_id
            LEFT JOIN sku_cost sc ON sc.sku_id = sk.sku_id
            LEFT JOIN sales_sku_monthly ssm ON ssm.sku_id = sk.sku_id
            {where}
            GROUP BY sk.sku_id
            {having_clause}
            {sort_clause}
            LIMIT :limit OFFSET :offset
            """,
            params,
        ).fetchall()

        # fix E2: wrap in subquery to apply the same HAVING filter as the main query
        total = con.execute(
            f"""
            SELECT COUNT(*) FROM (
                SELECT sk.sku_id
                FROM skus sk
                JOIN categories c ON c.category_id = sk.category_id
                LEFT JOIN sku_cost sc ON sc.sku_id = sk.sku_id
                LEFT JOIN sales_sku_monthly ssm ON ssm.sku_id = sk.sku_id
                {where}
                GROUP BY sk.sku_id
                {having_clause}
            )
            """,
            cnt_params,
        ).fetchone()[0]

    return {
        "total": total,
        "items": [dict(r) for r in rows],
    }


# ---------------------------------------------------------------------------
# /api/cost/template  - full SKU list for CSV template download
# ---------------------------------------------------------------------------
@app.get("/api/cost/template")
def cost_template():
    """SKUs actually sold through Easy (real line items) with sales context +
    current unit_cost for CSV template download.

    Scope (2026-06-01): only SKUs with at least one Easy sale — excludes the
    ~847 never-sold active SKUs that just bloat the sheet — and excludes buffet
    TIER codes (BUFFET*), whose 'cost' is the customer payment, not a line-item
    cost (they're handled by _cogs_case, never costed here). Sorted by revenue
    desc so highest-volume items to cost appear first.
    """
    with get_db() as con:
        rows = con.execute(
            """
            SELECT sk.sku_code,
                   COALESCE(sk.description, sk.sku_code) AS description,
                   c.name AS category,
                   COALESCE(CAST(sc.unit_cost AS TEXT), '') AS unit_cost,
                   ROUND(SUM(ssm.subtotal_ex_vat), 0) AS total_revenue,
                   ROUND(SUM(ssm.qty), 0)             AS total_qty,
                   MAX(ssm.period_month)              AS last_sold
            FROM skus sk
            JOIN  categories c    ON c.category_id = sk.category_id
            JOIN  sales_sku_monthly ssm ON ssm.sku_id = sk.sku_id AND ssm.source = 'Easy'
            LEFT JOIN sku_cost sc ON sc.sku_id = sk.sku_id
            WHERE COALESCE(sk.active, 1) = 1
              AND sk.sku_code NOT LIKE 'BUFFET%'
              AND c.name NOT IN ('Payment', 'Other', 'Oshinei DELIVERY', 'Special DELIVERY')
              AND UPPER(sk.sku_code) NOT IN ('SERVICECHARGE', 'SP', 'SERVICE')
            GROUP BY sk.sku_id
            HAVING SUM(ssm.qty) > 0
            ORDER BY total_revenue DESC, sk.sku_code
            """
        ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# /api/cost/analysis   -  full per-SKU menu engineering analysis
# ---------------------------------------------------------------------------
@app.get("/api/cost/analysis")
@ttl_cache
def cost_analysis(
    branch:     Optional[str] = Query(None),
    month:      Optional[str] = Query(None),
    month_from: Optional[str] = Query(None),
    month_to:   Optional[str] = Query(None),
    include_buffet_drink: bool = Query(False),
    group:      Optional[str] = Query(None),
):
    """
    Per-SKU revenue + cost breakdown with menu engineering quadrant classification.
    Returns items with sales > 0 only. Excludes buffet/drink/dessert/payment by default
    so the analysis focuses on à la carte food items.
    Quadrant = star | plowhorse | puzzle | dog | uncosted
    """
    filters = ["b.name != 'ALL'", "ssm.source = 'Easy'"]   # fix E8
    params: dict = {}
    if branch:
        filters.append("b.name = :branch")
        params["branch"] = branch
    _group_filter(filters, params, group)
    _month_range_filter(filters, params, month_from, month_to, month, col="ssm.period_month")
    EXCLUDED_CATS = (
        'BUFFET', 'Oshinei SOFT DRINK', 'Oshinei ALCOHOL',
        'Oshinei DESSERT', 'DESSERT', 'Payment', 'Other',
        'Oshinei DELIVERY', 'Special DELIVERY',
    )
    if not include_buffet_drink:
        ph = ",".join(f":_excl_{i}" for i in range(len(EXCLUDED_CATS)))
        # Keep ice-cream items even if they live in a DESSERT category
        filters.append(
            f"(c.name NOT IN ({ph}) "
            f" OR UPPER(COALESCE(sk.description,'')) LIKE '%ICE CREAM%' "
            f" OR UPPER(COALESCE(sk.description,'')) LIKE '%ICECREAM%' "
            f" OR UPPER(COALESCE(sk.description,'')) LIKE '%GELATO%' "
            f" OR UPPER(COALESCE(sk.description,'')) LIKE '%SOFT SERVE%' "
            f" OR COALESCE(sk.description,'') LIKE '%ไอติม%' "
            f" OR COALESCE(sk.description,'') LIKE '%ไอศกรีม%')"
        )
        for i, c in enumerate(EXCLUDED_CATS):
            params[f"_excl_{i}"] = c
    where = "WHERE " + " AND ".join(filters)

    with get_db() as con:
        rows = con.execute(
            f"""
            SELECT
                sk.sku_id,
                sk.sku_code,
                COALESCE(sk.description, sk.sku_code) AS description,
                c.name AS category,
                ROUND(SUM(ssm.qty), 0)                  AS qty,
                ROUND(SUM(ssm.subtotal_ex_vat), 2)       AS revenue,
                ROUND(SUM(ssm.subtotal_ex_vat), 2)       AS revenue_ex_vat,
                ROUND(SUM(ssm.total_inc_vat), 2)         AS pos_total_inc_vat_diagnostic,
                sc.unit_cost,
                -- COGS via canonical helper: monthly-aware effective cost +
                -- Easy buffet-tier exclusion (prevents tier x B0xxx double-count).
                -- gross_profit / margin_pct are derived in Python from these.
                ROUND(SUM({_cogs_case()}), 2)                               AS cogs,
                MAX(CASE WHEN {_effective_cost_subquery()} IS NOT NULL
                         THEN 1 ELSE 0 END)                                  AS has_cost
            FROM sales_sku_monthly ssm
            JOIN branches b   ON b.branch_id  = ssm.branch_id
            JOIN skus sk      ON sk.sku_id    = ssm.sku_id
            JOIN categories c ON c.category_id = sk.category_id
            LEFT JOIN sku_cost sc ON sc.sku_id = ssm.sku_id
            {where}
            GROUP BY sk.sku_id
            HAVING SUM(ssm.qty) > 0
            ORDER BY SUM(ssm.subtotal_ex_vat) DESC
            """,
            params,
        ).fetchall()

    items = [dict(r) for r in rows]

    # Buffet-component leak guard: a few buffet item-level SKUs (B0xxx) carry a
    # negligible à-la-carte revenue (the meal is paid via the buffet tier) while
    # their real BoM cost is large — yielding nonsense margins in this menu-
    # engineering view (e.g. B0119 ปีกไก่ทอด: ฿10 revenue vs ฿3M cost = −30,306,864%).
    # Their per-unit ex-VAT price is ≈฿0, so drop items priced below a small floor;
    # real à-la-carte food is ฿20+/unit, so nothing legitimate is touched. Counted
    # (not silently capped) and surfaced as summary.buffet_leak_excluded. The whole-
    # store KPI block (ws_*) is computed separately and still includes every row.
    _LEAK_PRICE_FLOOR = 5.0   # ฿ ex-VAT per unit
    def _unit_price(r):
        q = r["qty"] or 0
        return (r["revenue_ex_vat"] or 0) / q if q else 0.0
    _leak_ids = {r["sku_id"] for r in items
                 if (r["qty"] or 0) > 0 and _unit_price(r) < _LEAK_PRICE_FLOOR}
    buffet_leak_excluded = len(_leak_ids)
    if _leak_ids:
        items = [r for r in items if r["sku_id"] not in _leak_ids]

    # Effective-cost-aware costed flag + derived GP/margin (Phase 8 monthly cost).
    # "Costed" means the COALESCE chain (monthly exact -> monthly prior -> default)
    # resolved a unit cost for at least one row — broader than sc.unit_cost alone.
    for r in items:
        # Raw unified BoM COGS — kept even when has_cost (sku_cost-based) is False, so
        # recipe-costed items (e.g. buffet-component SKUs with a BoM but no sku_cost
        # row) still expose their true ingredient cost for cost-contribution views.
        r["bom_cogs"] = r["cogs"]
        r["has_cost"] = bool(r.pop("has_cost", 0))
        if r["has_cost"]:
            rev_ex = r["revenue_ex_vat"] or 0
            r["gross_profit"] = round(rev_ex - (r["cogs"] or 0), 2)
            r["margin_pct"] = round(r["gross_profit"] / rev_ex * 100, 1) if rev_ex else None
        else:
            r["cogs"] = None
            r["gross_profit"] = None
            r["margin_pct"] = None

    # â"€â"€ Derived fields â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€
    total_revenue     = sum(r["revenue"]     or 0 for r in items)
    total_qty         = sum(r["qty"]         or 0 for r in items)
    total_rev_ex      = sum(r["revenue_ex_vat"] or 0 for r in items)
    costed_items      = [r for r in items if r["has_cost"]]
    total_cogs        = sum(r["cogs"]        or 0 for r in costed_items)
    total_gp          = sum(r["gross_profit"] or 0 for r in costed_items)
    costed_rev_ex     = sum(r["revenue_ex_vat"] or 0 for r in costed_items)
    # Revenue-weighted margin EXCLUDING implausible items (margin < -100%, i.e.
    # cost > 2x revenue). Those are near-zero-revenue, high-qty buffet-component
    # SKUs that otherwise drag the headline KPI to nonsense (e.g. -7500%). They
    # remain in the table; only the aggregate excludes them.
    sane_margin_items = [
        r for r in costed_items
        if r["margin_pct"] is not None and r["margin_pct"] >= -100
    ]
    sane_gp     = sum(r["gross_profit"]   or 0 for r in sane_margin_items)
    sane_rev_ex = sum(r["revenue_ex_vat"] or 0 for r in sane_margin_items)
    overall_margin = round(sane_gp / sane_rev_ex * 100, 1) if sane_rev_ex > 0 else None
    excluded_outliers = len(costed_items) - len(sane_margin_items)

    for r in items:
        r["revenue_mix_pct"] = round(r["revenue"] / total_revenue * 100, 2) if total_revenue else 0
        r["qty_mix_pct"]     = round(r["qty"]     / total_qty     * 100, 2) if total_qty     else 0

    # â"€â"€ Menu engineering quadrant (costed items only) â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€
    # margin_pct can be None even when unit_cost is set (rows with subtotal_ex_vat=0).
    # Filter them out for the avg/quadrant math; otherwise sum() raises TypeError.
    quad_items = [r for r in costed_items if r["margin_pct"] is not None]
    if quad_items:
        def _median(xs):
            s = sorted(xs); n = len(s)
            return 0 if n == 0 else (s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2)
        # MEDIAN boundaries, not mean: a few tiny-revenue SKUs produce margins of
        # ±thousands-of-% (gross_profit / a few baht), which drag a mean off-scale
        # and misclassify everything (the classic "everything is a Puzzle" bug).
        med_qty    = _median([r["qty"]        for r in quad_items])
        med_margin = _median([r["margin_pct"] for r in quad_items])
        for r in items:
            if not r["has_cost"] or r["margin_pct"] is None:   # fix E7: use has_cost (covers BoM-only items)
                r["quadrant"] = "uncosted"
            else:
                hi_pop = r["qty"]        >= med_qty
                hi_mar = r["margin_pct"] >= med_margin
                r["quadrant"] = (
                    "star"      if hi_pop and hi_mar  else
                    "plowhorse" if hi_pop             else
                    "puzzle"    if hi_mar             else
                    "dog"
                )
        avg_qty_all    = med_qty
        avg_margin_all = med_margin
    else:
        for r in items:
            r["quadrant"] = "uncosted"
        avg_qty_all, avg_margin_all = None, None

    # â"€â"€ Pareto (revenue) â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€
    cumrev = 0
    for r in items:
        cumrev += r["revenue"] or 0
        r["cum_revenue_pct"] = round(cumrev / total_revenue * 100, 1) if total_revenue else 0

    # ── WHOLE-STORE reconciled headline totals (2026-06 fix) ──────────────────
    # The per-item table above is à-la-carte-focused (BUFFET category excluded for
    # menu-engineering). But its summed revenue (฿31M, à-la-carte only) does NOT
    # reconcile with the COGS (which includes buffet B0xxx dish cost) → produced a
    # nonsense GROSS PROFIT of -242M with a 70.8% margin. The KPI cards must show
    # the WHOLE-STORE figures (all categories, unified COGS) so rev/COGS/GP/margin
    # agree with every other tab (~฿944M rev, ~40% GP). Per-item analytics keep
    # their own à-la-carte scope below.
    ws_filters = ["b.name != 'ALL'", "ssm.source = 'Easy'"]
    ws_params: dict = {}
    if branch:
        ws_filters.append("b.name = :wbranch"); ws_params["wbranch"] = branch
    _group_filter(ws_filters, ws_params, group)
    _month_range_filter(ws_filters, ws_params, month_from, month_to, month, col="ssm.period_month")
    ws_where = "WHERE " + " AND ".join(ws_filters)
    with get_db() as con:
        ws = con.execute(f"""
            SELECT ROUND(SUM(ssm.subtotal_ex_vat), 2) AS revenue,
                   ROUND(SUM({_unified_cogs_expr('ssm')}), 2) AS cogs,
                   ROUND(SUM(ssm.qty), 0) AS qty
            FROM sales_sku_monthly ssm
            JOIN branches b ON b.branch_id = ssm.branch_id
            JOIN skus sk    ON sk.sku_id   = ssm.sku_id
            {ws_where}
        """, ws_params).fetchone()
    ws_rev  = (ws["revenue"] or 0) if ws else 0
    ws_cogs = (ws["cogs"] or 0) if ws else 0
    ws_gp   = ws_rev - ws_cogs
    ws_margin = round(ws_gp / ws_rev * 100, 1) if ws_rev > 0 else None

    return {
        "items": items,
        "summary": {
            # Headline = whole-store reconciled (rev/COGS/GP/margin all agree)
            "total_revenue":      round(ws_rev, 2),
            "total_cogs":         round(ws_cogs, 2),
            "total_gross_profit": round(ws_gp, 2),
            "overall_margin_pct": ws_margin,
            "total_qty":          round(ws["qty"] or 0, 0) if ws else 0,
            # à-la-carte menu-engineering scope (for the table / scatter / pareto)
            "alacarte_revenue":   round(total_revenue, 2),
            "alacarte_cogs":      round(total_cogs, 2),
            "alacarte_margin_pct": overall_margin,
            "total_skus":         len(items),
            "costed_skus":        len(costed_items),
            "uncosted_skus":      len(items) - len(costed_items),
            "avg_qty":            round(avg_qty_all, 1) if avg_qty_all else None,
            "avg_margin":         round(avg_margin_all, 1) if avg_margin_all else None,
            "margin_outliers_excluded": excluded_outliers,
            "buffet_leak_excluded": buffet_leak_excluded,
        },
    }


# ============================================================================
# PROFIT ANALYSIS  -  joins sales_sku_monthly x sku_cost to compute COGS + GP
# ============================================================================

# A month whose chain-wide active-branch coverage is far below the recent norm is
# a not-yet-fully-synced sliver (e.g. the 1st of a new month, before the nightly
# sync has fanned out to every branch). Reports must NOT headline such a month: it
# yields nonsense next-month forecasts, false "revenue dip" anomalies, and -100%
# MoM for the branches that simply have not synced yet. A month must carry at
# least this fraction of the recent peak branch-count to count as "reportable".
COVERAGE_MIN_RATIO = 0.6


def _partial_months(con, source: str = "Easy") -> set:
    """Trailing 'YYYY-MM' months whose chain-wide active-branch coverage is below
    COVERAGE_MIN_RATIO of the recent peak. Only strips from the tail (stops at the
    first well-covered month), so a genuinely low mid-series month is untouched."""
    rows = con.execute(
        "SELECT strftime('%Y-%m', sold_at) AS m, COUNT(DISTINCT branch_id) AS nb "
        "FROM sales_transaction WHERE source = ? GROUP BY m ORDER BY m",
        (source,),
    ).fetchall()
    if not rows:
        return set()
    peak = max(r[1] for r in rows[-6:])
    if peak <= 0:
        return set()
    bad = set()
    for r in reversed(rows):
        if r[1] < COVERAGE_MIN_RATIO * peak:
            bad.add(r[0])
        else:
            break
    return bad


def _profit_filters(branch, group, month_from, month_to, month):
    """Common WHERE-builder for profit endpoints. Returns (where_sql, params)."""
    filters = ["b.name != 'ALL'", "COALESCE(b.active,1) = 1"]
    params: dict = {}
    if branch:
        filters.append("b.name = :branch")
        params["branch"] = branch
    _group_filter(filters, params, group)
    _month_range_filter(filters, params, month_from, month_to, month, col="ssm.period_month")
    return "WHERE " + " AND ".join(filters), params


def _tx_filters(branch, group, month_from, month_to, month):
    """Common WHERE-builder for transaction-based queries."""
    filters = ["b.name != 'ALL'", "st.source = 'Easy'"]
    params: dict = {}
    if branch:
        filters.append("b.name = :branch")
        params["branch"] = branch
    _group_filter(filters, params, group)
    _month_range_filter(filters, params, month_from, month_to, month,
                        col="strftime('%Y-%m', st.sold_at)")
    return "WHERE " + " AND ".join(filters), params


# ─── Source-aware COGS rule (Phase 0 GP fix) ─────────────────────────────────
# Easy source logs both tier SKUs (BUFFET350/500/590/799/CHILD with revenue =
# customer payment) AND item SKUs (B0xxx with qty>0, revenue=0 — the real
# consumption). Using tier unit_cost AND item unit_cost double-counts.
# Rule: for Easy rows whose sku_code is a buffet tier, contribute zero cost
# (and zero costed-revenue); for everything else, use unit_cost normally.
# 'BUFFET' (plain, sku_id=1) is a deprecated legacy buffet code. Some Easy
# bills still emit it as a fallback. Treat same as tiered codes: count revenue,
# skip unit_cost (avoid double-counting 825M of phantom COGS).
_EASY_TIER_CODES = ('BUFFET','BUFFET350','BUFFET500','BUFFET590','BUFFET799','BUFFETCHILD')
_EASY_TIER_CODES_SQL = "('BUFFET','BUFFET350','BUFFET500','BUFFET590','BUFFET799','BUFFETCHILD')"
# Legacy buffet tier-level codes (no item breakdown) — kept as a SKU exclusion list.
_LEGACY_TIER_CODES = ('B01','B02','B03','B07','B11')
_ALL_TIER_CODES = _EASY_TIER_CODES + _LEGACY_TIER_CODES


def _effective_cost_subquery(ssm: str = "ssm", sc: str = "sc") -> str:
    """SQL fragment returning the effective unit_cost for a sales_sku_monthly row.

    Resolution order (Phase 8 monthly cost support):
      1. sku_cost_monthly exact-month match for the row's period_month
      2. sku_cost_monthly latest prior month  (carry-forward)
      3. sku_cost.unit_cost — lifetime default
      4. NULL (caller decides whether to treat as 0 or "uncosted")

    Requires the outer query to expose ``{ssm}.sku_id``, ``{ssm}.period_month``
    and to LEFT JOIN ``sku_cost {sc}`` on sku_id (for the default fallback).
    """
    return (
        "COALESCE("
        f"(SELECT unit_cost FROM sku_cost_monthly "
        f" WHERE sku_id = {ssm}.sku_id AND period_month = {ssm}.period_month), "
        f"(SELECT unit_cost FROM sku_cost_monthly "
        f" WHERE sku_id = {ssm}.sku_id AND period_month < {ssm}.period_month "
        f" ORDER BY period_month DESC LIMIT 1), "
        f"{sc}.unit_cost"
        ")"
    )


def _cogs_case(ssm: str = "ssm", sk: str = "sk", sc: str = "sc") -> str:
    """DEPRECATED shim → delegates to _unified_cogs_expr (2026-06-02 overhaul).

    Kept so the ~34 existing call sites keep working unchanged. The `sc` arg is
    ignored (the unified expr uses a correlated subquery for the sku_cost
    fallback, so no sku_cost join is needed). All COGS in the app now flows
    through one definition: BoM recipe first, sku_cost fallback, tier rows = 0.
    """
    return _unified_cogs_expr(ssm, sk)


def _bom_cogs_expr(ssm: str = "ssm") -> str:
    """SQL expression for per-row COGS from the BoM RECIPE model (the trustworthy
    path: real ingredient costs from bom_ingredients, resolved directly or via
    bom_sku_alias). This is the SAME basis as the BoM Cost tab and Branch P&L —
    use it for profit/GP so margins reflect reality (~42%), not the inflated
    'costed-revenue-only' figure from sku_cost.

    Returns 0 for SKUs with no recipe (uncosted) — so pair revenue with
    _bom_costed_rev_expr() if you want margin over costed revenue only.

    NOTE (2026-06-02): now delegates to _unified_cogs_expr so BoM and the old
    _cogs_case sites produce IDENTICAL COGS. (Requires a `sk`/skus join, which
    every caller already has.)
    """
    return _unified_cogs_expr(ssm, "sk")


def _bom_costed_rev_expr(ssm: str = "ssm") -> str:
    """Revenue portion that has a BoM recipe (pairs with _bom_cogs_expr)."""
    return (
        f"CASE WHEN COALESCE("
        f"(SELECT 1 FROM bom_ingredients bi WHERE bi.sku_id = {ssm}.sku_id LIMIT 1), "
        f"(SELECT 1 FROM bom_sku_alias a JOIN skus s_al ON s_al.sku_id = {ssm}.sku_id "
        f"   WHERE a.bom_sku_code = s_al.sku_code LIMIT 1)) IS NOT NULL "
        f"THEN {ssm}.subtotal_ex_vat ELSE 0 END"
    )


def _unified_cogs_expr(ssm: str = "ssm", sk: str = "sk") -> str:
    """THE single COGS expression for the whole app (2026-06-02 overhaul).

    Per row:
      1. Easy buffet TIER rows (BUFFET350/500/590/799/CHILD) → 0
         (consumption is logged on the item-level B0xxx rows; counting the tier
          too would double-count).
      2. else if the SKU has a non-zero sku_cost.unit_cost (the Cost-Management
         table, owner-maintained) → qty × unit_cost
         (2026-06-11 priority flip: cost-management is the source of truth now
          that the table holds real unit costs — median cost/price ≈ 0.20;
          previously BoM won, which inflated e.g. B0040 to 38.14 vs actual 13.40)
      3. else if the SKU has a BoM RECIPE (direct or via bom_sku_alias)
         → qty × recipe ingredient cost
      4. else → 0 (genuinely uncosted; sku_cost rows stored as 0 mean "no data"
         and fall through to the BoM, never force COGS to 0)

    Self-contained: needs only the sales row alias <ssm> (it resolves sku_code,
    recipe, and unit_cost via correlated subqueries), so it's a true drop-in for
    every old call site regardless of how/whether they join skus or sku_cost.
    The `sk` arg is accepted for signature compatibility but not required.
    Buffet revenue stays intact; only the cost side is unified.
    """
    return (
        f"CASE "
        f"  WHEN {ssm}.source = 'Easy' AND "
        f"       (SELECT sk_t.sku_code FROM skus sk_t WHERE sk_t.sku_id = {ssm}.sku_id) "
        f"       IN {_EASY_TIER_CODES_SQL} THEN 0 "
        # Per-SKU effective unit cost is precomputed once into sku_effective_cost
        # (built at startup + on every cost/bom edit via invalidate_cache), turning
        # what used to be THREE correlated subqueries PER sales row — evaluated
        # ~87k+ times per request — into ONE rowid lookup into a ~1,200-row hot
        # table. The original three subqueries are KEPT as a COALESCE fallback so
        # the expression stays self-contained and CORRECT even if the table is
        # absent/empty (tests, offline scripts, or a SKU added since the last
        # build): COALESCE short-circuits, so the subqueries never execute once the
        # table has the SKU. recipe_cost stores RAW cost; uncosted SKUs are stored
        # as 0 (non-NULL) so a hit short-circuits correctly. Verified identical.
        f"  ELSE {ssm}.qty * COALESCE("
        f"    (SELECT sec.recipe_cost FROM sku_effective_cost sec WHERE sec.sku_id = {ssm}.sku_id), "
        f"    (SELECT NULLIF(sc2.unit_cost, 0) FROM sku_cost sc2 WHERE sc2.sku_id = {ssm}.sku_id), "
        f"    (SELECT SUM(bi.ingredient_cost) FROM bom_ingredients bi WHERE bi.sku_id = {ssm}.sku_id), "
        f"    (SELECT SUM(bi2.ingredient_cost) FROM bom_ingredients bi2 "
        f"       JOIN bom_sku_alias a ON a.resolved_sku_id = bi2.sku_id "
        f"       JOIN skus s_al ON s_al.sku_id = {ssm}.sku_id WHERE a.bom_sku_code = s_al.sku_code), "
        f"    0) "
        f"END"
    )


def _build_sku_effective_cost(con, force: bool = True):
    """(Re)build the per-SKU effective unit COGS lookup that backs _unified_cogs_expr.

    recipe_cost = COALESCE(NULLIF(sku_cost.unit_cost, 0), direct BoM recipe SUM,
    alias-path BoM SUM via bom_sku_alias on sku_code, 0) — EXACTLY the chain the
    inline expression uses (cost-management table first since 2026-06-11), computed
    ONCE per SKU. Pure function of skus / bom_ingredients / bom_sku_alias / sku_cost.
    Rebuilt at startup and whenever cost/bom data changes (hooked into the full-clear
    branch of invalidate_cache), so edits reflect immediately as before.

    Compute-then-replace: a transient failure never wipes the table to all-zero COGS.
    """
    con.execute(
        "CREATE TABLE IF NOT EXISTS sku_effective_cost("
        "sku_id INTEGER PRIMARY KEY, recipe_cost REAL NOT NULL DEFAULT 0)"
    )
    if not force:
        try:
            if con.execute("SELECT 1 FROM sku_effective_cost LIMIT 1").fetchone():
                return
        except Exception:
            pass
    rows = con.execute(
        """
        SELECT s.sku_id, COALESCE(
            (SELECT NULLIF(sc2.unit_cost, 0) FROM sku_cost sc2 WHERE sc2.sku_id = s.sku_id),
            (SELECT SUM(bi.ingredient_cost) FROM bom_ingredients bi WHERE bi.sku_id = s.sku_id),
            (SELECT SUM(bi2.ingredient_cost) FROM bom_ingredients bi2
               JOIN bom_sku_alias a ON a.resolved_sku_id = bi2.sku_id
               WHERE a.bom_sku_code = s.sku_code),
            0) AS rc
        FROM skus s
        """
    ).fetchall()
    if not rows:
        return
    con.execute("DELETE FROM sku_effective_cost")
    con.executemany(
        "INSERT INTO sku_effective_cost(sku_id, recipe_cost) VALUES (?, ?)",
        [(r[0], r[1]) for r in rows],
    )
    con.commit()


def _costed_rev_case(ssm: str = "ssm", sk: str = "sk", sc: str = "sc") -> str:
    """SQL CASE expression for the revenue portion that aligns with COGS.

    "Has effective cost" now considers the monthly fallback chain (Phase 8).
    """
    eff = _effective_cost_subquery(ssm, sc)
    return (
        f"CASE WHEN {ssm}.source = 'Easy' AND {sk}.sku_code IN {_EASY_TIER_CODES_SQL} "
        f"          THEN {ssm}.subtotal_ex_vat "
        f"     WHEN {eff} IS NOT NULL THEN {ssm}.subtotal_ex_vat "
        f"     ELSE 0 END"
    )


# ─── (A)/(B) variant filter (Phase 9, revised) ───────────────────────────────
# Easy POS labels description prefix unreliably — "(A)" items often turn out to
# be buffet-consumed (qty>0, revenue=0). So we filter by REVENUE BEHAVIOUR
# rather than description prefix:
#   'A' = à la carte: customer paid separately → subtotal_ex_vat > 0
#         AND not a buffet tier SKU.
#   'B' = buffet:   tier SKUs (customer paid via tier) OR items consumed
#         during buffet (qty > 0 AND subtotal_ex_vat <= 0).
# Requires JOIN skus sk and the row to expose ssm.subtotal_ex_vat / ssm.qty.
def _variant_filter(variant: Optional[str], sk: str = "sk", ssm: str = "ssm") -> str:
    """Return a SQL fragment (no leading AND) restricting rows to (A) or (B)
    by REVENUE behaviour, not description prefix. Empty string when no filter."""
    if not variant:
        return ""
    v = variant.strip().upper()
    if v in ("", "ALL", "A+B", "AB"):
        return ""
    if v == "A":
        return (
            f"({ssm}.subtotal_ex_vat > 0 "
            f" AND {sk}.sku_code NOT IN {_EASY_TIER_CODES_SQL})"
        )
    if v == "B":
        return (
            f"({sk}.sku_code IN {_EASY_TIER_CODES_SQL} "
            f" OR ({ssm}.qty > 0 AND COALESCE({ssm}.subtotal_ex_vat,0) <= 0))"
        )
    return ""


@app.get("/api/profit/buffet_per_bill")
@ttl_cache
def buffet_per_bill(
    branch:      Optional[str] = Query(None),
    month_from:  Optional[str] = Query(None),
    month_to:    Optional[str] = Query(None),
    granularity: str           = Query("monthly", description="daily | weekly | monthly | quarterly"),
):
    """Per-bill profit deep-dive for the buffet business.

    Joins sales_transaction with the buffet SKU costs via inferred tier.
    For each bill: revenue ex-VAT, estimated buffet BoM cost, profit, margin %.
    Aggregated by the requested granularity.
    """
    if granularity == "daily":
        period_expr = "DATE(st.sold_at)"
    elif granularity == "weekly":
        period_expr = _iso_week_sql('st.sold_at')
    elif granularity == "quarterly":
        period_expr = ("substr(strftime('%Y-%m', st.sold_at), 1, 4) || '-Q' || "
                       "((CAST(substr(strftime('%Y-%m', st.sold_at), 6, 2) AS INTEGER)-1)/3+1)")
    else:
        period_expr = "strftime('%Y-%m', st.sold_at)"

    filters = ["st.source = 'Easy'", "b.name != 'ALL'", "st.customer_count > 0", "st.food_subtotal > 0"]
    params: dict = {}
    if branch:
        filters.append("b.name = :branch")
        params["branch"] = branch
    _month_range_filter(filters, params, month_from, month_to, None,
                        col="strftime('%Y-%m', st.sold_at)")
    where = "WHERE " + " AND ".join(filters)

    # 2026-06-02: per-bill cost now uses the REAL buffet food-cost ratio per month
    # (unified COGS of buffet items ÷ buffet revenue), NOT a fabricated tier-price
    # assumption. The old code looked up BUFFET350/500/590/799 unit_cost (which is
    # empty) and fell back to a flat 45%-margin guess, so margins were fictional.
    sm_filters = ["b.name != 'ALL'", "sk.business_type IN ('buffet','buffet_tier')"]
    sm_params: dict = {}
    if branch:
        sm_filters.append("b.name = :branch")
        sm_params["branch"] = branch
    _month_range_filter(sm_filters, sm_params, month_from, month_to, None, col="sm.period_month")
    sm_where = "WHERE " + " AND ".join(sm_filters)
    with get_db() as con:
        ratio_by_month = {}
        _rrev_tot = _rcogs_tot = 0.0
        for r in con.execute(f"""
            SELECT sm.period_month AS m,
                   SUM(CASE WHEN sk.business_type='buffet_tier'
                            THEN sm.subtotal_ex_vat ELSE 0 END) AS rev_ex,
                   SUM({_unified_cogs_expr('sm', 'sk')})        AS cogs
            FROM sales_sku_monthly sm
            JOIN skus sk     ON sk.sku_id   = sm.sku_id
            JOIN branches b  ON b.branch_id = sm.branch_id
            {sm_where}
            GROUP BY sm.period_month
        """, sm_params).fetchall():
            rev = float(r["rev_ex"] or 0); cg = float(r["cogs"] or 0)
            if rev > 0:
                ratio_by_month[r["m"]] = cg / rev
                _rrev_tot += rev; _rcogs_tot += cg
        overall_ratio = (_rcogs_tot / _rrev_tot) if _rrev_tot else 0.60

        # Pull all eligible buffet bills (per-head spend used only for the spend-bracket mix)
        rows = con.execute(f"""
            SELECT b.name AS branch,
                   {period_expr} AS period,
                   strftime('%Y-%m', st.sold_at)                  AS ym,
                   st.customer_count                              AS pax,
                   st.food_subtotal / st.customer_count           AS per_head,
                   st.total_inc_vat                               AS rev_inc,
                   st.total_inc_vat / 1.07                        AS rev_ex,
                   st.discount + st.sp_discount                   AS discount
            FROM sales_transaction st
            JOIN branches b ON b.branch_id = st.branch_id
            {where}
              AND (st.food_subtotal / st.customer_count) BETWEEN 200 AND 1500
        """, params).fetchall()

    # Spend-bracket label from real per-head spend (NOT the dead tier SKU codes)
    def tier_of(per_head):
        if per_head < 450:  return ("≤450",    450)
        if per_head < 600:  return ("450-600", 600)
        if per_head < 720:  return ("600-720", 720)
        return (">720", 800)

    from collections import defaultdict
    agg = defaultdict(lambda: {"bills": 0, "pax": 0, "revenue_ex": 0.0, "revenue_inc": 0.0,
                               "cost": 0.0, "discount": 0.0,
                               "tier_mix": defaultdict(int)})
    overall = {"bills": 0, "pax": 0, "revenue_ex": 0.0, "cost": 0.0,
               "tier_mix": defaultdict(int), "tier_cost_mix": defaultdict(float)}

    for r in rows:
        tier_code, tier_price = tier_of(r["per_head"])
        pax = r["pax"] or 0
        rev_ex_bill = r["rev_ex"] or 0
        # Real cost = bill revenue × that month's buffet food-cost ratio
        cost = rev_ex_bill * ratio_by_month.get(r["ym"], overall_ratio)
        a = agg[r["period"]]
        a["bills"]       += 1
        a["pax"]         += pax
        a["revenue_ex"]  += r["rev_ex"] or 0
        a["revenue_inc"] += r["rev_inc"] or 0
        a["cost"]        += cost
        a["discount"]    += r["discount"] or 0
        a["tier_mix"][tier_code] += pax
        overall["bills"]       += 1
        overall["pax"]         += pax
        overall["revenue_ex"]  += r["rev_ex"] or 0
        overall["cost"]        += cost
        overall["tier_mix"][tier_code] += pax
        overall["tier_cost_mix"][tier_code] += cost

    out_rows = []
    for period in sorted(agg.keys()):
        a = agg[period]
        profit = a["revenue_ex"] - a["cost"]
        margin = (profit / a["revenue_ex"] * 100) if a["revenue_ex"] else 0
        avg_per_bill_rev    = a["revenue_ex"] / a["bills"] if a["bills"] else 0
        avg_per_bill_cost   = a["cost"]       / a["bills"] if a["bills"] else 0
        avg_per_bill_profit = profit          / a["bills"] if a["bills"] else 0
        # Dominant tier this period
        dom = max(a["tier_mix"].items(), key=lambda kv: kv[1], default=(None, 0))
        out_rows.append({
            "period":              period,
            "bills":               a["bills"],
            "pax":                 a["pax"],
            "revenue_ex":          round(a["revenue_ex"], 2),
            "revenue_inc":         round(a["revenue_inc"], 2),
            "cost":                round(a["cost"], 2),
            "profit":              round(profit, 2),
            "margin_pct":          round(margin, 2),
            "avg_per_bill_rev":    round(avg_per_bill_rev, 2),
            "avg_per_bill_cost":   round(avg_per_bill_cost, 2),
            "avg_per_bill_profit": round(avg_per_bill_profit, 2),
            "dominant_tier":       dom[0],
            "tier_mix":            dict(a["tier_mix"]),
        })

    profit_overall = overall["revenue_ex"] - overall["cost"]
    return {
        "granularity": granularity,
        "rows":        out_rows,
        "overall": {
            "bills":              overall["bills"],
            "pax":                overall["pax"],
            "revenue_ex":         round(overall["revenue_ex"], 2),
            "cost":               round(overall["cost"], 2),
            "profit":             round(profit_overall, 2),
            "margin_pct":         round(profit_overall / overall["revenue_ex"] * 100, 2) if overall["revenue_ex"] else 0,
            "avg_per_bill_rev":   round(overall["revenue_ex"] / overall["bills"], 2) if overall["bills"] else 0,
            "avg_per_bill_cost":  round(overall["cost"]       / overall["bills"], 2) if overall["bills"] else 0,
            "avg_per_bill_profit":round(profit_overall        / overall["bills"], 2) if overall["bills"] else 0,
            "tier_mix":           {k: int(v) for k, v in overall["tier_mix"].items()},
            "tier_cost_mix":      {k: round(v, 0) for k, v in overall["tier_cost_mix"].items()},
        },
    }


@app.get("/api/profit/summary")
@ttl_cache
def profit_summary(
    branch:     Optional[str] = Query(None),
    group:      Optional[str] = Query(None),
    month_from: Optional[str] = Query(None),
    month_to:   Optional[str] = Query(None),
    month:      Optional[str] = Query(None),
):
    """Aggregate profit metrics for the period (revenue, COGS, GP, margin, per-bill)."""
    where, params = _profit_filters(branch, group, month_from, month_to, month)
    with get_db() as con:
        row = con.execute(f"""
            SELECT
              ROUND(SUM(ssm.subtotal_ex_vat), 2)               AS revenue,
              ROUND(SUM(ssm.subtotal_ex_vat), 2)               AS revenue_ex_vat,
              ROUND(SUM(ssm.total_inc_vat), 2)                 AS pos_total_inc_vat_diagnostic,
              ROUND(SUM({_bom_cogs_expr()}), 2)                AS cogs,
              SUM(CASE WHEN COALESCE(
                    (SELECT 1 FROM bom_ingredients bi WHERE bi.sku_id = ssm.sku_id LIMIT 1),
                    (SELECT 1 FROM bom_sku_alias a JOIN skus s2 ON s2.sku_id = ssm.sku_id
                       WHERE a.bom_sku_code = s2.sku_code LIMIT 1)) IS NOT NULL
                   THEN ssm.qty ELSE 0 END)                    AS costed_qty,
              SUM(ssm.qty) AS qty_sold
            FROM sales_sku_monthly ssm
            JOIN branches b ON b.branch_id = ssm.branch_id
            JOIN skus     sk ON sk.sku_id  = ssm.sku_id
            {where}
        """, params).fetchone()

    revenue        = row["revenue"]                or 0   # ex-VAT (Rule 1)
    revenue_ex     = row["revenue_ex_vat"]         or 0
    cogs           = row["cogs"]                   or 0   # BoM recipe COGS (real)
    qty_sold       = row["qty_sold"]               or 0
    costed_qty     = row["costed_qty"]             or 0
    costed_rev_ex  = revenue_ex   # BoM applies across all revenue; kept for response compat
    gross_profit   = revenue_ex - cogs
    # Margin over TOTAL revenue (honest) — not just costed revenue, which inflated
    # GP to ~72%. With BoM coverage ~70-92%, this reflects the real ~42% margin.
    margin_pct     = round(gross_profit / revenue_ex * 100, 2) if revenue_ex > 0 else None
    # Coverage = % of QTY SOLD that has a BoM recipe. (Revenue-based coverage is
    # meaningless for buffet: dishes are costed but their revenue is on the tier.)
    coverage_pct   = round(costed_qty / qty_sold * 100, 1) if qty_sold > 0 else 0

    # tx count from sales_transaction (bills only — total_inc_vat is diagnostic, not revenue)
    tx_where, tx_params = _tx_filters(branch, group, month_from, month_to, month)
    with get_db() as con:
        tx_row = con.execute(f"""
            SELECT COUNT(*) AS tx_count,
                   ROUND(SUM(st.total_inc_vat), 2) AS pos_total_inc_vat_diagnostic
            FROM sales_transaction st
            JOIN branches b ON b.branch_id = st.branch_id
            {tx_where}
        """, tx_params).fetchone()
    tx_count = tx_row["tx_count"] or 0

    profit_per_bill = round(gross_profit / tx_count, 2) if tx_count > 0 else 0
    # avg_ticket: revenue (ex-VAT) per bill — switched from inc-VAT total per Rule 1
    avg_ticket      = round(revenue      / tx_count, 2) if tx_count > 0 else 0

    # Monthly trend (Rule 2) — pair the cumulative totals with per-period breakdown
    with get_db() as con:
        trend_rows = con.execute(f"""
            SELECT ssm.period_month                              AS period_month,
              ROUND(SUM(ssm.subtotal_ex_vat), 2)                 AS revenue,
              ROUND(SUM({_bom_cogs_expr()}), 2)                  AS cogs,
              ROUND(SUM({_bom_costed_rev_expr()}), 2)            AS costed_revenue_ex_vat
            FROM sales_sku_monthly ssm
            JOIN branches b ON b.branch_id = ssm.branch_id
            JOIN skus     sk ON sk.sku_id  = ssm.sku_id
            {where}
            GROUP BY ssm.period_month
            ORDER BY ssm.period_month
        """, params).fetchall()
    monthly_trend = []
    for tr in trend_rows:
        d = dict(tr)
        rev = d["revenue"] or 0
        gp = rev - (d["cogs"] or 0)   # GP over total revenue (BoM COGS)
        d["gross_profit"] = round(gp, 2)
        d["margin_pct"]   = round(gp / rev * 100, 2) if rev > 0 else None
        monthly_trend.append(d)

    return {
        "revenue":           revenue,
        "revenue_ex_vat":    revenue_ex,
        "cogs":              cogs,
        "gross_profit":      round(gross_profit, 2),
        "margin_pct":        margin_pct,
        "tx_count":          tx_count,
        "avg_ticket":        avg_ticket,
        "profit_per_bill":   profit_per_bill,
        "cost_coverage_pct": coverage_pct,
        "qty_sold":          row["qty_sold"] or 0,
        "monthly_trend":     monthly_trend,
        "pos_total_inc_vat_diagnostic": row["pos_total_inc_vat_diagnostic"] or 0,
    }


@app.get("/api/profit/trend")
@ttl_cache
def profit_trend(
    branch:     Optional[str] = Query(None),
    group:      Optional[str] = Query(None),
    month_from: Optional[str] = Query(None),
    month_to:   Optional[str] = Query(None),
):
    """Monthly trend of revenue, COGS, gross profit, margin."""
    where, params = _profit_filters(branch, group, month_from, month_to, None)
    with get_db() as con:
        rows = con.execute(f"""
            SELECT ssm.period_month,
              ROUND(SUM(ssm.subtotal_ex_vat), 2)   AS revenue_ex_vat,
              ROUND(SUM({_bom_cogs_expr()}), 2)    AS cogs,
              ROUND(SUM({_bom_costed_rev_expr()}), 2)  AS costed_revenue_ex_vat
            FROM sales_sku_monthly ssm
            JOIN branches b ON b.branch_id = ssm.branch_id
            JOIN skus     sk ON sk.sku_id  = ssm.sku_id
            {where}
            GROUP BY ssm.period_month
            ORDER BY ssm.period_month
        """, params).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        rev = d["revenue_ex_vat"] or 0
        gp = rev - (d["cogs"] or 0)   # BoM COGS, margin over total revenue
        d["gross_profit"] = round(gp, 2)
        d["margin_pct"]   = round(gp / rev * 100, 2) if rev > 0 else None
        out.append(d)
    return out


@app.get("/api/profit/by-branch")
@ttl_cache
def profit_by_branch(
    group:      Optional[str] = Query(None),
    month_from: Optional[str] = Query(None),
    month_to:   Optional[str] = Query(None),
    month:      Optional[str] = Query(None),
):
    """Profit per branch."""
    where, params = _profit_filters(None, group, month_from, month_to, month)
    with get_db() as con:
        rows = con.execute(f"""
            SELECT b.branch_id, b.name AS branch,
              ROUND(SUM(ssm.subtotal_ex_vat), 2)   AS revenue_ex_vat,
              ROUND(SUM({_bom_cogs_expr()}), 2)    AS cogs,
              ROUND(SUM({_bom_costed_rev_expr()}), 2)  AS costed_revenue_ex_vat,
              SUM(ssm.qty) AS qty
            FROM sales_sku_monthly ssm
            JOIN branches b ON b.branch_id = ssm.branch_id
            JOIN skus     sk ON sk.sku_id  = ssm.sku_id
            {where}
            GROUP BY b.branch_id
            ORDER BY revenue_ex_vat DESC
        """, params).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        rev = d["revenue_ex_vat"] or 0
        gp = rev - (d["cogs"] or 0)   # BoM COGS, margin over total revenue
        d["gross_profit"] = round(gp, 2)
        d["margin_pct"]   = round(gp / rev * 100, 2) if rev > 0 else None
        out.append(d)
    return out


# -----------------------------------------------------------------------------
# GP by Branch x Month - BoM rule (Easy):
#   COGS = SUM(qty*unit_cost) EXCLUDING tier SKUs (BUFFET350/500/590/799/CHILD)
#   Revenue = SUM(subtotal_ex_vat)
# -----------------------------------------------------------------------------
@app.get("/api/gp/by-branch-month")
@ttl_cache
def gp_by_branch_month(
    month_from: Optional[str] = Query(None),
    month_to:   Optional[str] = Query(None),
    month:      Optional[str] = Query(None),
    variant:    Optional[str] = Query(None, description="A | B | all — filter à la carte vs buffet SKUs"),
    group:      Optional[str] = Query(None),
    granularity: str          = Query("month", description="day | week | month — bucket size for per-branch trend"),
):
    """GP per branch x month using the BoM rule (Easy data).

    `granularity` ∈ {day, week, month}: when day/week, each branch row also
    gains a `trend` array of pro-rated buckets (monthly COGS pro-rated by
    daily Easy-tx revenue, same approach as /api/buffet/gp)."""
    if granularity not in ("day", "week", "month"):
        granularity = "month"
    filters = ["b.name != 'ALL'", "COALESCE(b.active,1) = 1"]
    params: dict = {}
    _group_filter(filters, params, group)
    _month_range_filter(filters, params, month_from, month_to, month, col="ssm.period_month")
    var_clause = _variant_filter(variant)
    if var_clause:
        filters.append(var_clause)
    where = "WHERE " + " AND ".join(filters)

    # COGS from the BoM RECIPE model (real ingredient cost), consistent with the
    # Profit tab, Branch P&L, and BoM Cost tab. Was _cogs_case (sku_cost), which
    # gave inflated GP; switched to BoM 2026-06-01 so GP รายสาขา matches reality.
    cogs_expr = f"SUM({_bom_cogs_expr('ssm')})"

    with get_db() as con:
        rows = con.execute(f"""
            SELECT b.branch_id, b.name AS branch,
                   ssm.period_month AS month,
                   ssm.source       AS source,
                   ROUND(SUM(ssm.subtotal_ex_vat), 2) AS revenue,
                   ROUND({cogs_expr}, 2)              AS cogs
              FROM sales_sku_monthly ssm
              JOIN branches b ON b.branch_id = ssm.branch_id
              JOIN skus     sk ON sk.sku_id  = ssm.sku_id
            {where}
            GROUP BY b.branch_id, ssm.period_month, ssm.source
            ORDER BY b.name, ssm.period_month
        """, params).fetchall()

        try:
            srows = con.execute(
                "SELECT key, value FROM app_settings WHERE key IN ('cost_target_lo','cost_target_hi')"
            ).fetchall()
            settings = {r["key"]: r["value"] for r in srows}
        except Exception:
            settings = {}

    try:
        target_lo = float(settings.get("cost_target_lo", 55))
    except Exception:
        target_lo = 55.0
    try:
        target_hi = float(settings.get("cost_target_hi", 70))
    except Exception:
        target_hi = 70.0

    by_branch: dict = {}
    for r in rows:
        d = dict(r)
        bid = d["branch_id"]
        b = by_branch.setdefault(bid, {
            "branch": d["branch"], "branch_id": bid, "monthly": [],
            "total_revenue": 0.0, "total_cogs": 0.0,
        })
        rev  = float(d["revenue"] or 0)
        cogs = float(d["cogs"]    or 0)
        gp   = rev - cogs
        gp_pct = (gp / rev * 100) if rev > 0 else None
        fc_pct = (cogs / rev * 100) if rev > 0 else None
        b["monthly"].append({
            "month":         d["month"],
            "source":        d["source"],
            "revenue":       round(rev, 2),
            "cogs":          round(cogs, 2),
            "gp":            round(gp, 2),
            "gp_pct":        round(gp_pct, 2) if gp_pct is not None else None,
            "food_cost_pct": round(fc_pct, 2) if fc_pct is not None else None,
        })
        b["total_revenue"] += rev
        b["total_cogs"]    += cogs

    out_rows = []
    grand_rev = grand_cogs = 0.0
    for b in by_branch.values():
        tr = b["total_revenue"]; tc = b["total_cogs"]; tg = tr - tc
        b["total_revenue"]       = round(tr, 2)
        b["total_cogs"]          = round(tc, 2)
        b["total_gp"]            = round(tg, 2)
        b["total_gp_pct"]        = round(tg / tr * 100, 2) if tr > 0 else None
        b["total_food_cost_pct"] = round(tc / tr * 100, 2) if tr > 0 else None
        grand_rev  += tr
        grand_cogs += tc
        out_rows.append(b)

    out_rows.sort(key=lambda x: x["total_revenue"], reverse=True)

    grand_gp = grand_rev - grand_cogs

    # ── Granularity-aware per-branch trend (day/week buckets) ────────────
    if granularity in ("day", "week") and out_rows:
        from datetime import date as _date_cls
        def _wkb(date_str: str) -> str:
            d = _date_cls.fromisoformat(date_str)
            iso = d.isocalendar()
            return f"{iso[0]}-W{iso[1]:02d}"

        # Daily tx revenue per branch over the period
        tx_filters = ["b.name != 'ALL'", "st.source = 'Easy'", "COALESCE(b.active,1) = 1"]
        tx_params: dict = {}
        _group_filter(tx_filters, tx_params, group)
        _month_range_filter(tx_filters, tx_params, month_from, month_to, month,
                            col="SUBSTR(st.sold_at,1,7)")
        tx_where = "WHERE " + " AND ".join(tx_filters)
        with get_db() as con:
            daily_rows = con.execute(f"""
                SELECT b.branch_id,
                       DATE(st.sold_at)        AS d,
                       SUBSTR(st.sold_at,1,7)  AS m,
                       SUM(COALESCE(st.food_subtotal,0)
                         + COALESCE(st.drink_subtotal,0)
                         + COALESCE(st.other_subtotal,0)) AS rev_tx
                  FROM sales_transaction st
                  JOIN branches b ON b.branch_id = st.branch_id
                {tx_where}
                GROUP BY b.branch_id, DATE(st.sold_at)
            """, tx_params).fetchall()

        # Index: monthly tx rev per (branch, month)
        m_tx_rev: dict = {}
        for r in daily_rows:
            key = (r["branch_id"], r["m"])
            m_tx_rev[key] = m_tx_rev.get(key, 0.0) + float(r["rev_tx"] or 0)

        # Per branch: bucket → {rev, cogs}
        for b in out_rows:
            bid = b["branch_id"]
            monthly_map = {m["month"]: m for m in b["monthly"]}
            buckets: dict = {}
            for r in daily_rows:
                if r["branch_id"] != bid:
                    continue
                mo = r["m"]
                mrow = monthly_map.get(mo)
                if not mrow:
                    continue
                m_total = m_tx_rev.get((bid, mo), 0.0)
                if m_total <= 0:
                    continue
                frac = float(r["rev_tx"] or 0) / m_total
                d_cogs = float(mrow["cogs"] or 0)    * frac
                d_rev  = float(mrow["revenue"] or 0) * frac
                k = r["d"] if granularity == "day" else _wkb(r["d"])
                bb = buckets.setdefault(k, {"rev": 0.0, "cogs": 0.0})
                bb["rev"]  += d_rev
                bb["cogs"] += d_cogs
            trend = []
            for k in sorted(buckets.keys()):
                v   = buckets[k]
                gpv = v["rev"] - v["cogs"]
                trend.append({
                    "bucket":        k,
                    "revenue":       round(v["rev"],  2),
                    "cogs":          round(v["cogs"], 2),
                    "gp":            round(gpv, 2),
                    "gp_pct":        round(gpv / v["rev"] * 100, 2) if v["rev"] > 0 else None,
                    "food_cost_pct": round(v["cogs"] / v["rev"] * 100, 2) if v["rev"] > 0 else None,
                })
            b["trend"] = trend

    return {
        "rows": out_rows,
        "granularity": granularity,
        "target_lo": target_lo,
        "target_hi": target_hi,
        "grand_total": {
            "revenue":       round(grand_rev, 2),
            "cogs":          round(grand_cogs, 2),
            "gp":            round(grand_gp, 2),
            "gp_pct":        round(grand_gp / grand_rev * 100, 2) if grand_rev > 0 else None,
            "food_cost_pct": round(grand_cogs / grand_rev * 100, 2) if grand_rev > 0 else None,
        },
    }


@app.get("/api/profit/per-bill-distribution")
@ttl_cache
def profit_per_bill_distribution(
    branch:     Optional[str] = Query(None),
    group:      Optional[str] = Query(None),
    month_from: Optional[str] = Query(None),
    month_to:   Optional[str] = Query(None),
    month:      Optional[str] = Query(None),
):
    """Approximate distribution of profit per bill.
    Since transactions don't link to SKUs, we apply the period's overall
    costed-margin to each transaction's food+drink+other subtotal."""
    # NOTE: do NOT call profit_summary() here — it's @ttl_cache-wrapped and returns
    # a Response (JSON bytes), not a dict, so subscripting it 500s. Compute the
    # costed-margin directly from the same source.
    _ps_where, _ps_params = _profit_filters(branch, group, month_from, month_to, month)
    with get_db() as con:
        _ps = con.execute(f"""
            SELECT ROUND(SUM({_bom_cogs_expr()}), 2)         AS cogs,
                   ROUND(SUM(ssm.subtotal_ex_vat), 2)        AS revenue
            FROM sales_sku_monthly ssm
            JOIN branches b ON b.branch_id = ssm.branch_id
            JOIN skus     sk ON sk.sku_id  = ssm.sku_id
            {_ps_where}
        """, _ps_params).fetchone()
    _rev   = (_ps["revenue"] or 0) if _ps else 0
    _cogs  = (_ps["cogs"] or 0) if _ps else 0
    margin_pct  = round((_rev - _cogs) / _rev * 100, 2) if _rev > 0 else 0  # BoM, over total rev

    if margin_pct == 0:
        return {
            "buckets": [], "total_tx": 0, "avg_profit": 0,
            "min_profit": 0, "max_profit": 0, "margin_pct_used": 0,
            "note": "No costed SKUs in period. Upload SKU costs to enable.",
        }

    tx_where, tx_params = _tx_filters(branch, group, month_from, month_to, month)
    m_frac = margin_pct / 100.0
    with get_db() as con:
        r = con.execute(f"""
            WITH bills AS (
              SELECT (COALESCE(st.food_subtotal,0)
                    + COALESCE(st.drink_subtotal,0)
                    + COALESCE(st.other_subtotal,0)) * {m_frac} AS est_profit
              FROM sales_transaction st
              JOIN branches b ON b.branch_id = st.branch_id
              {tx_where}
            )
            SELECT
              SUM(CASE WHEN est_profit <= 0    THEN 1 ELSE 0 END) AS b_neg,
              SUM(CASE WHEN est_profit > 0    AND est_profit <= 100  THEN 1 ELSE 0 END) AS b_0_100,
              SUM(CASE WHEN est_profit > 100  AND est_profit <= 300  THEN 1 ELSE 0 END) AS b_100_300,
              SUM(CASE WHEN est_profit > 300  AND est_profit <= 500  THEN 1 ELSE 0 END) AS b_300_500,
              SUM(CASE WHEN est_profit > 500  AND est_profit <= 1000 THEN 1 ELSE 0 END) AS b_500_1000,
              SUM(CASE WHEN est_profit > 1000 THEN 1 ELSE 0 END) AS b_1000_plus,
              COUNT(*) AS total,
              ROUND(AVG(est_profit), 2) AS avg_profit,
              ROUND(MIN(est_profit), 2) AS min_profit,
              ROUND(MAX(est_profit), 2) AS max_profit
            FROM bills
        """, tx_params).fetchone()

    return {
        "buckets": [
            {"label": "<=0",      "count": r["b_neg"]        or 0},
            {"label": "1-100",    "count": r["b_0_100"]      or 0},
            {"label": "101-300",  "count": r["b_100_300"]    or 0},
            {"label": "301-500",  "count": r["b_300_500"]    or 0},
            {"label": "501-1000", "count": r["b_500_1000"]   or 0},
            {"label": "1000+",    "count": r["b_1000_plus"]  or 0},
        ],
        "total_tx":         r["total"]      or 0,
        "avg_profit":       r["avg_profit"] or 0,
        "min_profit":       r["min_profit"] or 0,
        "max_profit":       r["max_profit"] or 0,
        "margin_pct_used":  margin_pct,
    }


# ============================================================================
# DEEP P&L / PER-BILL ANALYSIS
# ============================================================================

def _pnl_sku_query(where: str, params: dict) -> str:
    return f"""
        SELECT ssm.period_month,
          ROUND(SUM(ssm.subtotal_ex_vat), 2)  AS revenue,
          ROUND(SUM(ssm.subtotal_ex_vat), 2)  AS revenue_ex_vat,
          ROUND(SUM(ssm.total_inc_vat), 2)    AS pos_total_inc_vat_diagnostic,
          ROUND(SUM({_cogs_case()}), 2)        AS cogs,
          ROUND(SUM({_costed_rev_case()}), 2)  AS costed_rev_ex
        FROM sales_sku_monthly ssm
        JOIN branches b ON b.branch_id = ssm.branch_id
        JOIN skus     sk ON sk.sku_id  = ssm.sku_id
        LEFT JOIN sku_cost sc ON sc.sku_id = ssm.sku_id
        {where}
        GROUP BY ssm.period_month
        ORDER BY ssm.period_month
    """


def _add_gp_fields(rows_out: list) -> list:
    """In-place: compute gross_profit, margin_pct, mom_pct.

    GP uses the FULL ex-VAT revenue base (revenue_ex_vat) so GP == the row's
    displayed revenue - COGS and reconciles across every P&L / Profit / BoM tab
    (2026-07 base-unification; previously used costed_rev_ex, which understated
    GP vs the Profit tab by the uncosted-revenue share). costed_rev_ex is kept
    on the row purely as a cost-coverage diagnostic.
    """
    prev_gp = None
    for d in rows_out:
        rev = (d.get("revenue_ex_vat") if d.get("revenue_ex_vat") is not None
               else d.get("revenue")) or 0
        gp = rev - (d.get("cogs") or 0)
        d["gross_profit"] = round(gp, 2)
        d["margin_pct"] = round(gp / rev * 100, 2) if rev > 0 else None
        d["mom_pct"] = round((gp - prev_gp) / prev_gp * 100, 1) if (prev_gp and prev_gp > 0) else None
        prev_gp = gp if gp else prev_gp
    return rows_out


@app.get("/api/pnl/monthly")
@ttl_cache
def pnl_monthly(
    branch:     Optional[str] = Query(None),
    group:      Optional[str] = Query(None),
    month_from: Optional[str] = Query(None),
    month_to:   Optional[str] = Query(None),
):
    """Monthly P&L trend: revenue, COGS, GP, margin%, tx_count, profit_per_bill."""
    where, params       = _profit_filters(branch, group, month_from, month_to, None)
    tx_where, tx_params = _tx_filters(branch, group, month_from, month_to, None)

    with get_db() as con:
        sku_rows = con.execute(_pnl_sku_query(where, params), params).fetchall()
        tx_rows  = con.execute(f"""
            SELECT strftime('%Y-%m', st.sold_at) AS period_month,
                   COUNT(*) AS tx_count,
                   SUM(st.customer_count) AS customer_count,
                   ROUND(AVG(st.total_inc_vat) / 1.07, 2) AS avg_ticket
            FROM sales_transaction st
            JOIN branches b ON b.branch_id = st.branch_id
            {tx_where}
            GROUP BY period_month
        """, tx_params).fetchall()

    tx_map = {r["period_month"]: dict(r) for r in tx_rows}
    out = _add_gp_fields([dict(r) for r in sku_rows])
    for d in out:
        tx = tx_map.get(d["period_month"], {})
        tx_count = tx.get("tx_count") or 0
        d["tx_count"]       = tx_count
        d["customer_count"] = tx.get("customer_count") or 0
        d["avg_ticket"]     = tx.get("avg_ticket") or 0
        gp = d["gross_profit"] or 0
        d["profit_per_bill"] = round(gp / tx_count, 2) if tx_count > 0 else None
    return out


@app.get("/api/pnl/branches")
@ttl_cache
def pnl_branches_deep(
    group:      Optional[str] = Query(None),
    month_from: Optional[str] = Query(None),
    month_to:   Optional[str] = Query(None),
):
    """All-branch P&L comparison with per-bill metrics."""
    where, params = _profit_filters(None, group, month_from, month_to, None)

    tx_filters = ["b.name != 'ALL'", "st.source = 'Easy'", "COALESCE(b.active,1) = 1"]
    tx_params: dict = {}
    if group:
        tx_filters.append("COALESCE(b.branch_type,'master') = :group")
        tx_params["group"] = group
    _month_range_filter(tx_filters, tx_params, month_from, month_to, None,
                        col="strftime('%Y-%m', st.sold_at)")
    tx_where = "WHERE " + " AND ".join(tx_filters)

    with get_db() as con:
        sku_rows = con.execute(f"""
            SELECT b.name AS branch,
              ROUND(SUM(ssm.subtotal_ex_vat), 2)   AS revenue,
              ROUND(SUM(ssm.subtotal_ex_vat), 2)   AS revenue_ex_vat,
              ROUND(SUM(ssm.total_inc_vat), 2)     AS pos_total_inc_vat_diagnostic,
              ROUND(SUM({_cogs_case()}), 2)        AS cogs,
              ROUND(SUM({_costed_rev_case()}), 2)  AS costed_rev_ex,
              COUNT(DISTINCT CASE WHEN sc.unit_cost IS NOT NULL THEN ssm.sku_id END) AS costed_skus,
              COUNT(DISTINCT ssm.sku_id) AS total_skus
            FROM sales_sku_monthly ssm
            JOIN branches b ON b.branch_id = ssm.branch_id
            JOIN skus     sk ON sk.sku_id  = ssm.sku_id
            LEFT JOIN sku_cost sc ON sc.sku_id = ssm.sku_id
            {where}
            GROUP BY b.name
            ORDER BY SUM(ssm.subtotal_ex_vat) DESC
        """, params).fetchall()

        tx_rows = con.execute(f"""
            SELECT b.name AS branch,
                   COUNT(*) AS tx_count,
                   SUM(st.customer_count) AS customer_count,
                   ROUND(AVG(st.total_inc_vat) / 1.07, 2) AS avg_ticket
            FROM sales_transaction st
            JOIN branches b ON b.branch_id = st.branch_id
            {tx_where}
            GROUP BY b.name
        """, tx_params).fetchall()

    tx_map = {r["branch"]: dict(r) for r in tx_rows}
    out = []
    for r in sku_rows:
        d = dict(r)
        rev = (d.get("revenue_ex_vat") or 0)
        gp = rev - (d.get("cogs") or 0)
        d["gross_profit"] = round(gp, 2)
        d["margin_pct"] = round(gp / rev * 100, 2) if rev > 0 else None
        tx = tx_map.get(d["branch"], {})
        tx_count = tx.get("tx_count") or 0
        d["tx_count"]   = tx_count
        d["avg_ticket"] = tx.get("avg_ticket") or 0
        d["profit_per_bill"] = round(gp / tx_count, 2) if tx_count > 0 else None
        out.append(d)

    ranked = sorted(out, key=lambda x: -(x["gross_profit"] or 0))
    for i, r in enumerate(ranked):
        r["gp_rank"] = i + 1
    return out


@app.get("/api/pnl/branch-detail")
@ttl_cache
def pnl_branch_detail(
    branch:     str            = Query(...),
    month_from: Optional[str] = Query(None),
    month_to:   Optional[str] = Query(None),
):
    """Deep dive for one branch: monthly P&L, top 30 SKUs by GP, bill revenue stats."""
    sku_filters = ["b.name != 'ALL'", "COALESCE(b.active,1) = 1", "b.name = :branch"]
    sku_params: dict = {"branch": branch}
    _month_range_filter(sku_filters, sku_params, month_from, month_to, None, col="ssm.period_month")
    sku_where = "WHERE " + " AND ".join(sku_filters)

    tx_filters = ["b.name = :branch", "st.source = 'Easy'"]
    tx_params: dict = {"branch": branch}
    _month_range_filter(tx_filters, tx_params, month_from, month_to, None,
                        col="strftime('%Y-%m', st.sold_at)")
    tx_where = "WHERE " + " AND ".join(tx_filters)

    with get_db() as con:
        monthly_rows = con.execute(_pnl_sku_query(sku_where, sku_params), sku_params).fetchall()

        # Top 60 dishes by BoM COST (real recipe cost), with each dish's share of
        # the branch's total COGS — so you can see what drives the branch's cost.
        top_skus = con.execute(f"""
            SELECT sk.sku_code,
                   COALESCE(sk.description, sk.sku_code) AS description,
                   c.name AS category,
                   ROUND(SUM(ssm.qty), 0)                AS qty,
                   ROUND(SUM(ssm.subtotal_ex_vat), 2)    AS revenue,
                   ROUND(SUM(ssm.subtotal_ex_vat), 2)    AS revenue_ex_vat,
                   sc.unit_cost,
                   ROUND(SUM({_bom_cogs_expr()}), 2)        AS cogs,
                   ROUND(SUM(ssm.subtotal_ex_vat) - SUM({_bom_cogs_expr()}), 2) AS gp,
                   CASE WHEN SUM(ssm.subtotal_ex_vat) > 0
                        THEN ROUND(100.0*(SUM(ssm.subtotal_ex_vat) - SUM({_bom_cogs_expr()}))
                                   / SUM(ssm.subtotal_ex_vat), 1) ELSE NULL END AS margin_pct
            FROM sales_sku_monthly ssm
            JOIN branches b   ON b.branch_id   = ssm.branch_id
            JOIN skus sk      ON sk.sku_id      = ssm.sku_id
            JOIN categories c ON c.category_id  = sk.category_id
            LEFT JOIN sku_cost sc ON sc.sku_id  = ssm.sku_id
            {sku_where}
            GROUP BY sk.sku_id
            HAVING SUM(ssm.qty) > 0 AND SUM({_bom_cogs_expr()}) > 0
            ORDER BY SUM({_bom_cogs_expr()}) DESC
            LIMIT 60
        """, sku_params).fetchall()

        bill_vals = con.execute(f"""
            SELECT ROUND(st.total_inc_vat, 2) AS val
            FROM sales_transaction st
            JOIN branches b ON b.branch_id = st.branch_id
            {tx_where}
            ORDER BY val
        """, tx_params).fetchall()

        tx_monthly = con.execute(f"""
            SELECT strftime('%Y-%m', st.sold_at) AS period_month,
                   COUNT(*) AS tx_count,
                   ROUND(AVG(st.total_inc_vat), 2) AS avg_ticket,
                   SUM(st.customer_count) AS customer_count
            FROM sales_transaction st
            JOIN branches b ON b.branch_id = st.branch_id
            {tx_where}
            GROUP BY period_month
        """, tx_params).fetchall()

    # Build monthly with GP and TX data
    monthly_out = _add_gp_fields([dict(r) for r in monthly_rows])
    tx_mo_map = {r["period_month"]: dict(r) for r in tx_monthly}
    for d in monthly_out:
        tx = tx_mo_map.get(d["period_month"], {})
        tx_count = tx.get("tx_count") or 0
        d["tx_count"]       = tx_count
        d["avg_ticket"]     = tx.get("avg_ticket") or 0
        d["customer_count"] = tx.get("customer_count") or 0
        gp = d["gross_profit"] or 0
        d["profit_per_bill"] = round(gp / tx_count, 2) if tx_count > 0 else None

    # Bill revenue distribution and percentiles
    vals = sorted([r["val"] for r in bill_vals if r["val"] is not None])
    n = len(vals)

    def pct_val(p):
        if n == 0: return 0
        idx = max(0, min(n - 1, int(n * p)))
        return round(vals[idx], 2)

    BUCKETS = [(0, 100), (100, 200), (200, 300), (300, 500), (500, 750), (750, 1000), (1000, 1500), (1500, None)]
    bill_buckets = []
    for lo, hi in BUCKETS:
        count = sum(1 for v in vals if v >= lo and (hi is None or v < hi))
        label = f"{lo}-{hi}" if hi else f"{lo}+"
        bill_buckets.append({"label": label, "count": count})

    # Compute blended margin from monthly data
    total_rev        = sum((d.get("revenue_ex_vat") or 0) for d in monthly_out)
    total_cogs       = sum((d.get("cogs") or 0) for d in monthly_out)
    total_gp         = total_rev - total_cogs
    margin_frac      = (total_gp / total_rev) if total_rev > 0 else 0

    profit_buckets = []
    PBUCKETS = [(None, 0), (0, 50), (50, 100), (100, 200), (200, 400), (400, 700), (700, None)]
    if margin_frac > 0 and n > 0:
        profit_vals = sorted([v * margin_frac for v in vals])
        for lo, hi in PBUCKETS:
            count = sum(1 for v in profit_vals if
                        (lo is None or v >= lo) and (hi is None or v < hi))
            label = "<0" if lo is None else (f"{lo}+" if hi is None else f"{lo}-{hi}")
            profit_buckets.append({"label": label, "count": count})

    bill_stats = {
        "tx_count":         n,
        "avg":              round(sum(vals) / n, 2) if n else 0,
        "min":              vals[0] if n else 0,
        "max":              vals[-1] if n else 0,
        "p10":              pct_val(0.1),
        "p25":              pct_val(0.25),
        "p50":              pct_val(0.5),
        "p75":              pct_val(0.75),
        "p90":              pct_val(0.9),
        "buckets":          bill_buckets,
        "profit_buckets":   profit_buckets,
        "margin_pct_used":  round(margin_frac * 100, 1),
        "avg_profit":       round(sum(vals) * margin_frac / n, 2) if n else 0,
    }

    # Each dish's share of the branch's TOTAL BoM cost (across ALL dishes, not
    # just the top 60) so the % is honest.
    with get_db() as con:
        _tc = con.execute(f"""
            SELECT ROUND(SUM({_bom_cogs_expr()}), 2) AS total_cogs
            FROM sales_sku_monthly ssm
            JOIN branches b ON b.branch_id = ssm.branch_id
            JOIN skus sk    ON sk.sku_id   = ssm.sku_id
            {sku_where}
        """, sku_params).fetchone()

        # Per-branch INGREDIENT consumption (which raw materials drive this
        # branch's cost) + per-branch CATEGORY cost mix — the "go deeper" asks.
        ing_rows = con.execute(f"""
            SELECT bi.ingredient_name AS name, bi.ingredient_type AS type,
                   SUM(ssm.qty * bi.ingredient_cost) AS cost
            FROM sales_sku_monthly ssm
            JOIN branches b ON b.branch_id = ssm.branch_id
            JOIN skus sk    ON sk.sku_id   = ssm.sku_id
            JOIN bom_ingredients bi ON bi.sku_id = ssm.sku_id
            {sku_where}
            GROUP BY bi.ingredient_name
        """, sku_params).fetchall()

        cat_rows = con.execute(f"""
            SELECT c.name AS category,
                   ROUND(SUM(ssm.subtotal_ex_vat), 2) AS revenue,
                   ROUND(SUM({_bom_cogs_expr()}), 2)  AS cogs
            FROM sales_sku_monthly ssm
            JOIN branches b   ON b.branch_id   = ssm.branch_id
            JOIN skus sk      ON sk.sku_id      = ssm.sku_id
            JOIN categories c ON c.category_id  = sk.category_id
            {sku_where}
            GROUP BY c.name
        """, sku_params).fetchall()

    branch_total_cogs = (_tc["total_cogs"] or 0) if _tc else 0
    top_list = [dict(r) for r in top_skus]
    for d in top_list:
        d["cost_pct"] = round((d["cogs"] or 0) / branch_total_cogs * 100, 1) if branch_total_cogs > 0 else 0

    # Group ingredients into families (salmon variants → Salmon), top 15 by cost.
    fam: dict = {}
    for r in ing_rows:
        label = _ingredient_family(r["name"])
        fam[label] = fam.get(label, 0.0) + (r["cost"] or 0)
    ing_total = sum(fam.values()) or 1
    top_ingredients = [
        {"name": k, "cost": round(v, 2), "pct": round(v / ing_total * 100, 1)}
        for k, v in sorted(fam.items(), key=lambda x: -x[1])
    ][:15]

    # Category cost mix (top by cost), each as % of branch COGS.
    cat_mix = []
    for r in sorted(cat_rows, key=lambda x: -(x["cogs"] or 0)):
        cogs_v = r["cogs"] or 0
        if cogs_v <= 0:
            continue
        cat_mix.append({
            "category":  r["category"],
            "revenue":   r["revenue"] or 0,
            "cogs":      cogs_v,
            "cost_pct":  round(cogs_v / branch_total_cogs * 100, 1) if branch_total_cogs > 0 else 0,
        })

    return {
        "branch":   branch,
        "monthly":  monthly_out,
        "top_skus": top_list,
        "branch_total_cogs": branch_total_cogs,
        "top_ingredients":   top_ingredients,
        "category_mix":      cat_mix,
        "bill_stats": bill_stats,
    }


# ---------------------------------------------------------------------------
# GET /api/pnl/branch-month-detail  — drill a branch+month from the P&L table
#   • P&L summary for that month (rev / COGS / GP)
#   • BoM cost broken down by dish (each dish's % of the month's COGS)
#   • top ingredients (families) driving cost
#   • MoM: which dishes rose/fell most in SERVINGS vs the previous month
# ---------------------------------------------------------------------------
@app.get("/api/pnl/branch-month-detail")
@ttl_cache
def pnl_branch_month_detail(
    branch: str = Query(...),
    month:  str = Query(..., description="YYYY-MM"),
    limit:  int = Query(40, ge=5, le=200),
):
    prev = _month_add(month, -1)
    cogs_x = _unified_cogs_expr("ssm")

    def _dishes(mon: str) -> dict:
        with get_db() as con:
            rows = con.execute(f"""
                SELECT sk.sku_code,
                       COALESCE(sk.description, sk.sku_code) AS description,
                       c.name AS category,
                       SUM(ssm.qty)             AS servings,
                       SUM({cogs_x})            AS cogs,
                       SUM(ssm.subtotal_ex_vat) AS revenue
                FROM sales_sku_monthly ssm
                JOIN branches b   ON b.branch_id   = ssm.branch_id
                JOIN skus sk      ON sk.sku_id      = ssm.sku_id
                JOIN categories c ON c.category_id  = sk.category_id
                WHERE b.name = :branch AND ssm.period_month = :m
                GROUP BY sk.sku_id
                HAVING SUM(ssm.qty) > 0
            """, {"branch": branch, "m": mon}).fetchall()
        return {r["sku_code"]: dict(r) for r in rows}

    cur = _dishes(month)
    pre = _dishes(prev)

    tot_cogs = sum((d["cogs"] or 0) for d in cur.values())
    tot_rev  = sum((d["revenue"] or 0) for d in cur.values())

    # BoM cost breakdown by dish (this month), each as % of month COGS
    cost_breakdown = sorted(
        ({"sku_code": k, "description": d["description"], "category": d["category"],
          "servings": round(d["servings"] or 0, 0), "cogs": round(d["cogs"] or 0, 2),
          "cost_pct": round((d["cogs"] or 0) / tot_cogs * 100, 1) if tot_cogs > 0 else 0}
         for k, d in cur.items()),
        key=lambda x: -x["cogs"]
    )[:limit]

    # Ingredient families this month
    fam: dict = {}
    with get_db() as con:
        ing = con.execute("""
            SELECT bi.ingredient_name AS name, SUM(ssm.qty*bi.ingredient_cost) AS cost
            FROM sales_sku_monthly ssm
            JOIN branches b ON b.branch_id = ssm.branch_id
            JOIN bom_ingredients bi ON bi.sku_id = ssm.sku_id
            WHERE b.name = :branch AND ssm.period_month = :m
            GROUP BY bi.ingredient_name
        """, {"branch": branch, "m": month}).fetchall()
    for r in ing:
        lbl = _ingredient_family(r["name"]); fam[lbl] = fam.get(lbl, 0.0) + (r["cost"] or 0)
    ing_tot = sum(fam.values()) or 1
    top_ingredients = [{"name": k, "cost": round(v, 2), "pct": round(v/ing_tot*100, 1)}
                       for k, v in sorted(fam.items(), key=lambda x: -x[1])][:12]

    # MoM rising/falling dishes by servings
    movers = []
    for code in set(cur) | set(pre):
        now = (cur.get(code, {}).get("servings") or 0)
        was = (pre.get(code, {}).get("servings") or 0)
        if now == was:
            continue
        meta = cur.get(code) or pre.get(code)
        delta = now - was
        pct = round(delta / was * 100, 1) if was > 0 else None
        movers.append({"sku_code": code, "description": meta["description"],
                       "now": round(now, 0), "was": round(was, 0), "delta": round(delta, 0),
                       "delta_pct": pct, "is_new": was == 0 and now > 0})
    movers.sort(key=lambda m: abs(m["delta"]), reverse=True)
    rising   = sorted([m for m in movers if m["delta"] > 0], key=lambda m: -m["delta"])[:10]
    falling  = sorted([m for m in movers if m["delta"] < 0], key=lambda m: m["delta"])[:10]

    prev_cogs = sum((d["cogs"] or 0) for d in pre.values())
    prev_rev  = sum((d["revenue"] or 0) for d in pre.values())
    return {
        "branch": branch, "month": month, "prev_month": prev,
        "summary": {
            "revenue": round(tot_rev, 2), "cogs": round(tot_cogs, 2),
            "gross_profit": round(tot_rev - tot_cogs, 2),
            "gp_margin_pct": round((tot_rev - tot_cogs)/tot_rev*100, 1) if tot_rev > 0 else None,
            "food_cost_pct": round(tot_cogs/tot_rev*100, 1) if tot_rev > 0 else None,
            "prev_revenue": round(prev_rev, 2), "prev_cogs": round(prev_cogs, 2),
            "rev_delta_pct": round((tot_rev-prev_rev)/prev_rev*100, 1) if prev_rev > 0 else None,
            "dish_count": len(cur),
        },
        "cost_breakdown": cost_breakdown,
        "top_ingredients": top_ingredients,
        "rising_dishes": rising,
        "falling_dishes": falling,
    }


# ---------------------------------------------------------------------------
# GET /api/cost/audit   🔴1
# Flag SKUs where unit_cost looks suspicious vs actual selling price
# ---------------------------------------------------------------------------
@app.get("/api/cost/audit")
@ttl_cache
def cost_audit():
    """Flag SKUs whose unit_cost appears suspicious.

    A SKU is flagged when:
    - unit_cost > implied_selling_price  (ratio > 1.0)  ← definitely wrong
    - unit_cost > 0.9 × implied_selling_price           ← likely wrong (warning)
    - unit_cost is very high relative to peers (z-score > 2)

    implied_selling_price = total_revenue / total_qty  (avg selling price per unit).
    For buffet food items (subtotal_ex_vat ~ 0) implied_price will be near zero,
    so we skip ratio check for those and rely on z-score only.
    """
    TIER_CODES_SET = {'BUFFET350','BUFFET500','BUFFET590','BUFFET799','BUFFETCHILD',
                      'B01','B02','B07','B11'}
    with get_db() as con:
        rows = con.execute("""
            SELECT sk.sku_id, sk.sku_code,
                   COALESCE(sk.description, sk.sku_code) AS description,
                   c.name AS category,
                   sc.unit_cost,
                   ROUND(SUM(ssm.subtotal_ex_vat), 2) AS total_revenue,
                   ROUND(SUM(ssm.qty), 2)            AS total_qty
            FROM sku_cost sc
            JOIN skus sk      ON sk.sku_id      = sc.sku_id
            JOIN categories c ON c.category_id  = sk.category_id
            LEFT JOIN sales_sku_monthly ssm ON ssm.sku_id = sk.sku_id
            WHERE sc.unit_cost IS NOT NULL AND sc.unit_cost > 0
            GROUP BY sk.sku_id
        """).fetchall()

    items = []
    costs = []
    for r in rows:
        uc   = r["unit_cost"]
        qty  = r["total_qty"] or 0
        rev  = r["total_revenue"] or 0
        implied = round(rev / qty, 4) if qty > 0 else None
        ratio   = round(uc / implied, 3) if implied and implied > 0 else None
        costs.append(uc)
        items.append({
            "sku_id":              r["sku_id"],
            "sku_code":            r["sku_code"],
            "description":         r["description"],
            "category":            r["category"],
            "unit_cost":           uc,
            "total_revenue":       rev,
            "total_qty":           qty,
            "implied_selling_price": implied,
            "ratio":               ratio,
            "is_tier":             r["sku_code"] in TIER_CODES_SET,
        })

    # z-score across all unit costs
    n = len(costs)
    mean_uc = sum(costs) / n if n else 0
    var_uc  = sum((c - mean_uc) ** 2 for c in costs) / n if n else 0
    sd_uc   = var_uc ** 0.5

    result = []
    for item in items:
        uc = item["unit_cost"]
        z  = round((uc - mean_uc) / sd_uc, 2) if sd_uc > 0 else 0
        ratio = item["ratio"]
        implied = item["implied_selling_price"]
        qty = item["total_qty"] or 0

        # Buffet-included dish: sold in volume (qty>0) but ~zero à-la-carte revenue
        # (customer pays via the buffet tier, not per plate) → avg sell ≈ 0, which
        # makes the cost/price ratio meaningless (huge false 'Error'). Detect these
        # by data (implied≈0 with real qty), NOT a stale code-prefix list, and
        # skip the ratio check — rely on z-score only. Mark as 'tier' so the UI
        # groups them as buffet, not errors.
        is_buffet_incl = qty > 0 and (implied is None or implied < 1.0)
        item["is_buffet_included"] = is_buffet_incl

        if item["is_tier"] or is_buffet_incl:
            flag = "tier"
        elif ratio is not None and ratio > 1.0:
            flag = "error"       # cost exceeds selling price per unit
        elif ratio is not None and ratio > 0.9:
            flag = "warning"     # cost ≥ 90% of selling price
        elif z > 2.5:
            flag = "high_z"      # statistical outlier among all SKU costs
        else:
            flag = "ok"

        item["z_score"] = z
        item["flag"]    = flag
        result.append(item)

    # Sort: error first, then warning, then high_z, then ok
    order = {"error": 0, "warning": 1, "high_z": 2, "tier": 3, "ok": 4}
    result.sort(key=lambda x: (order.get(x["flag"], 9), -(x["ratio"] or 0)))

    flagged   = [x for x in result if x["flag"] in ("error", "warning", "high_z")]
    summary   = {
        "total_with_cost": n,
        "error_count":   sum(1 for x in result if x["flag"] == "error"),
        "warning_count": sum(1 for x in result if x["flag"] == "warning"),
        "high_z_count":  sum(1 for x in result if x["flag"] == "high_z"),
        "tier_count":    sum(1 for x in result if x["flag"] == "tier"),
    }
    return {"summary": summary, "flagged": flagged, "all": result}


def _assert_known_branch_keys(con, costs: Dict[str, float], setting_label: str):
    """Reject cost payloads whose keys don't match a real branches.name.

    Guards against silently persisting corrupt/garbled keys — e.g. the
    branch_fixed_costs '?????'-key incident where Thai names were mangled to
    literal ASCII '?' at write time, so every P&L fixed.get(name, 0) read 0 and
    operating profit collapsed to gross profit. A bad key now fails loudly
    (HTTP 422) and nothing is saved, instead of corrupting the settings row.
    """
    valid = {r["name"] for r in con.execute("SELECT name FROM branches")}
    unknown = sorted(k for k in costs if k not in valid)
    if unknown:
        raise HTTPException(
            status_code=422,
            detail={
                "error": f"Unknown branch name(s) in {setting_label}; nothing saved.",
                "unknown_keys": unknown,
                "valid_branches": sorted(valid),
            },
        )


@app.get("/api/branches/fixed-costs")
def get_branch_fixed_costs():
    """Return {branch_name: monthly_fixed_cost} from app_settings."""
    with get_db() as con:
        row = con.execute(
            "SELECT value FROM app_settings WHERE key='branch_fixed_costs'"
        ).fetchone()
    if row:
        try:
            return _json.loads(row["value"])
        except Exception:
            return {}
    return {}


class BranchFixedCostBody(BaseModel):
    costs: Dict[str, float]   # {branch_name: monthly_fixed_cost}


@app.post("/api/branches/fixed-costs", dependencies=[Depends(require_write_token)])
def save_branch_fixed_costs(body: BranchFixedCostBody):
    """Save per-branch monthly fixed costs."""
    value = _json.dumps(body.costs)
    with get_db() as con:
        _assert_known_branch_keys(con, body.costs, "branch_fixed_costs")
        con.execute(
            "INSERT OR REPLACE INTO app_settings(key, value) VALUES('branch_fixed_costs', :v)",
            {"v": value},
        )
        con.commit()
    return {"saved": len(body.costs)}


@app.get("/api/branches/labor-costs")
def get_branch_labor_costs():
    """Return {branch_name: monthly_labor_cost} from app_settings."""
    with get_db() as con:
        row = con.execute("SELECT value FROM app_settings WHERE key='branch_labor_costs'").fetchone()
    if row:
        try: return _json.loads(row["value"])
        except Exception: return {}
    return {}


class BranchLaborCostBody(BaseModel):
    costs: Dict[str, float]   # {branch_name: monthly_labor_cost}


@app.post("/api/branches/labor-costs", dependencies=[Depends(require_write_token)])
def save_branch_labor_costs(body: BranchLaborCostBody):
    """Save per-branch monthly labor cost (the largest CONTROLLABLE cost)."""
    value = _json.dumps(body.costs)
    with get_db() as con:
        _assert_known_branch_keys(con, body.costs, "branch_labor_costs")
        con.execute("INSERT OR REPLACE INTO app_settings(key, value) VALUES('branch_labor_costs', :v)", {"v": value})
        con.commit()
    return {"saved": len(body.costs)}


@app.get("/api/finance/prime-cost")
def finance_prime_cost(month: Optional[str] = Query(None), group: Optional[str] = Query(None)):
    """Prime cost = food% + labor% per branch — the two biggest CONTROLLABLE costs.
    Food COGS is real (unified BoM); labor + fixed come from Settings inputs. Shows
    nothing for labor until entered (food% is always real)."""
    cogs_expr = _unified_cogs_expr("sm")
    filt = ["b.name!='ALL'"]; p: dict = {}
    if group: _group_filter(filt, p, group)
    tw = " AND ".join(filt)
    with get_db() as con:
        mon = month or con.execute(
            "SELECT MAX(period_month) FROM sales_sku_monthly WHERE source='Easy'").fetchone()[0]
        rows = {r["branch"]: dict(r) for r in con.execute(f"""
            SELECT b.name branch, ROUND(SUM(sm.subtotal_ex_vat)) rev, ROUND(SUM({cogs_expr})) food_cogs
            FROM sales_sku_monthly sm JOIN branches b ON b.branch_id=sm.branch_id
            WHERE {tw} AND sm.period_month=:m GROUP BY b.name""", {**p, "m": mon}).fetchall()}
        def _load(k):
            r = con.execute("SELECT value FROM app_settings WHERE key=:k", {"k": k}).fetchone()
            if r:
                try: return _json.loads(r["value"])
                except Exception: return {}
            return {}
        labor = _load('branch_labor_costs'); fixed = _load('branch_fixed_costs')
    out = []
    for nm, r in rows.items():
        rev = r["rev"] or 0; fc = r["food_cogs"] or 0
        lb = labor.get(nm, 0) or 0; fx = fixed.get(nm, 0) or 0
        has_lb = lb > 0
        op = rev - fc - lb - fx
        out.append({
            "branch": nm, "rev": rev, "food_cogs": fc,
            "food_pct": round(fc / rev * 100, 1) if rev else None,
            "labor": lb, "labor_pct": round(lb / rev * 100, 1) if (rev and lb) else None,
            "prime_pct": round((fc + lb) / rev * 100, 1) if (rev and lb) else None,
            "fixed": fx, "fixed_pct": round(fx / rev * 100, 1) if (rev and fx) else None,
            "operating": round(op) if (lb or fx) else None,
            "operating_pct": round(op / rev * 100, 1) if (rev and (lb or fx)) else None,
            "has_labor": has_lb,
        })
    out.sort(key=lambda r: -(r["prime_pct"] if r["prime_pct"] is not None else (r["food_pct"] or -1)))
    trev = sum(r["rev"] for r in out); tfood = sum(r["food_cogs"] for r in out)
    tlabor = sum(r["labor"] for r in out); tfixed = sum(r["fixed"] for r in out)
    chain = {"rev": trev, "food_cogs": tfood, "labor": tlabor, "fixed": tfixed,
             "food_pct": round(tfood / trev * 100, 1) if trev else None,
             "labor_pct": round(tlabor / trev * 100, 1) if (trev and tlabor) else None,
             "prime_pct": round((tfood + tlabor) / trev * 100, 1) if (trev and tlabor) else None,
             "branches_with_labor": sum(1 for r in out if r["has_labor"]), "branch_count": len(out)}
    # AYCE buffet prime-cost band (food ~50-55% + labor ~15-22%): good <70, watch 70-78, high >78
    return {"month": mon, "branches": out, "chain": chain, "band": {"good": 70, "high": 78}}


# ---------------------------------------------------------------------------
# GET /api/pnl/branch   — full branch P&L waterfall + break-even (Analysis #1)
# ---------------------------------------------------------------------------
@app.get("/api/pnl/branch")
@ttl_cache
def pnl_branch(
    branch:     Optional[str] = Query(None),
    month:      Optional[str] = Query(None),
    month_from: Optional[str] = Query(None),
    month_to:   Optional[str] = Query(None),
):
    """Per-branch P&L: Revenue (ex-VAT) -> COGS (canonical _cogs_case) -> Gross
    Profit -> minus monthly fixed costs (app_settings) -> Operating Profit, plus
    break-even covers and margin of safety. Includes a chain roll-up."""
    filters = ["b.name != 'ALL'"]
    params: dict = {}
    if branch:
        filters.append("b.name = :branch"); params["branch"] = branch
    _month_range_filter(filters, params, month_from, month_to, month, col="ssm.period_month")
    where = "WHERE " + " AND ".join(filters)
    cogs_expr = _cogs_case()

    tf = ["b.name != 'ALL'", "st.source='Easy'", "st.customer_count > 0"]
    tp: dict = {}
    if branch:
        tf.append("b.name = :branch"); tp["branch"] = branch
    _month_range_filter(tf, tp, month_from, month_to, month, col="SUBSTR(st.sold_at,1,7)")
    twhere = "WHERE " + " AND ".join(tf)

    with get_db() as con:
        rev_rows = con.execute(f"""
            SELECT b.name AS branch,
                   COUNT(DISTINCT ssm.period_month) AS months,
                   SUM(ssm.subtotal_ex_vat)         AS revenue
            FROM sales_sku_monthly ssm
            JOIN branches b ON b.branch_id = ssm.branch_id
            {where}
            GROUP BY b.name
        """, params).fetchall()
        # COGS via the UNIFIED expression (BoM recipe first, sku_cost fallback,
        # tier rows = 0) so this matches every other tab exactly (2026-06-02).
        bom_rows = con.execute(f"""
            SELECT b.name AS branch, SUM({_unified_cogs_expr('ssm')}) AS bom_cost
            FROM sales_sku_monthly ssm
            JOIN branches b ON b.branch_id = ssm.branch_id
            {where}
            GROUP BY b.name
        """, params).fetchall()
        cov = con.execute(f"""
            SELECT b.name AS branch, SUM(st.customer_count) AS covers
            FROM sales_transaction st
            JOIN branches b ON b.branch_id = st.branch_id
            {twhere}
            GROUP BY b.name
        """, tp).fetchall()
        fc_row = con.execute(
            "SELECT value FROM app_settings WHERE key='branch_fixed_costs'"
        ).fetchone()

    fixed = {}
    if fc_row:
        try:
            fixed = _json.loads(fc_row["value"])
        except Exception:
            pass
    covmap = {r["branch"]: (r["covers"] or 0) for r in cov}
    bom_map = {r["branch"]: (r["bom_cost"] or 0.0) for r in bom_rows}

    out = []
    tot = {"revenue": 0.0, "cogs": 0.0, "fixed": 0.0, "covers": 0}
    for r in sorted(rev_rows, key=lambda x: -(x["revenue"] or 0)):
        br      = r["branch"]
        rev     = round(r["revenue"] or 0.0, 2)
        cogs_v  = round(bom_map.get(br, 0.0), 2)
        gp      = round(rev - cogs_v, 2)
        months  = r["months"] or 1
        fcm     = float(fixed.get(br, 0) or 0)
        fcp     = round(fcm * months, 2)
        op      = round(gp - fcp, 2)
        covers  = covmap.get(br, 0)
        gp_per_cover = (gp / covers) if covers else None
        breakeven_covers = round(fcp / gp_per_cover) if (gp_per_cover and gp_per_cover > 0) else None
        mos_pct = round((covers - breakeven_covers) / covers * 100, 1) if (breakeven_covers and covers) else None
        out.append({
            "branch":            br,
            "months":            months,
            "revenue":           rev,
            "cogs":              cogs_v,
            "gross_profit":      gp,
            "gp_margin_pct":     round(gp / rev * 100, 1) if rev else None,
            "fixed_cost_period": fcp,
            "fixed_cost_monthly": round(fcm, 2),
            "operating_profit":  op,
            "op_margin_pct":     round(op / rev * 100, 1) if rev else None,
            "covers":            covers,
            "op_per_cover":      round(op / covers, 2) if covers else None,
            "gp_per_cover":      round(gp_per_cover, 2) if gp_per_cover is not None else None,
            "breakeven_covers":  breakeven_covers,
            "margin_of_safety_pct": mos_pct,
        })
        tot["revenue"] += rev; tot["cogs"] += cogs_v; tot["fixed"] += fcp; tot["covers"] += covers

    chain_gp = round(tot["revenue"] - tot["cogs"], 2)
    chain_op = round(chain_gp - tot["fixed"], 2)
    chain = {
        "revenue":          round(tot["revenue"], 2),
        "cogs":             round(tot["cogs"], 2),
        "gross_profit":     chain_gp,
        "gp_margin_pct":    round(chain_gp / tot["revenue"] * 100, 1) if tot["revenue"] else None,
        "fixed_cost_period": round(tot["fixed"], 2),
        "operating_profit": chain_op,
        "op_margin_pct":    round(chain_op / tot["revenue"] * 100, 1) if tot["revenue"] else None,
        "covers":           tot["covers"],
        "fixed_costs_set":  bool(fixed),
    }
    return {"branches": out, "chain": chain}


# ---------------------------------------------------------------------------
# GET /api/cost/uncosted-coverage  — cost-data coverage tracker (Analysis #3)
# ---------------------------------------------------------------------------
@app.get("/api/cost/uncosted-coverage")
@ttl_cache
def cost_uncosted_coverage(
    branch:     Optional[str] = Query(None),
    month:      Optional[str] = Query(None),
    month_from: Optional[str] = Query(None),
    month_to:   Optional[str] = Query(None),
):
    """How much revenue rests on costed vs uncosted SKUs. 'Costed' = the SKU has an
    effective unit cost OR is a buffet tier (costed via item-level BoM). Returns
    per-branch coverage, a monthly trend, and the top uncosted SKUs by revenue —
    a prioritised worklist for filling in missing costs."""
    filters = ["b.name != 'ALL'"]
    params: dict = {}
    if branch:
        filters.append("b.name = :branch"); params["branch"] = branch
    _month_range_filter(filters, params, month_from, month_to, month, col="ssm.period_month")
    where = "WHERE " + " AND ".join(filters)
    costed = _costed_rev_case()            # tier rev + costed-sku rev, else 0
    eff    = _effective_cost_subquery()
    uncosted_pred = (f"({eff}) IS NULL AND ssm.subtotal_ex_vat > 0 "
                     f"AND sk.sku_code NOT IN {_EASY_TIER_CODES_SQL}")

    with get_db() as con:
        brows = con.execute(f"""
            SELECT b.name AS branch,
                   SUM(ssm.subtotal_ex_vat) AS total_rev,
                   SUM({costed})            AS costed_rev,
                   COUNT(DISTINCT ssm.sku_id) AS skus,
                   COUNT(DISTINCT CASE WHEN {uncosted_pred} THEN ssm.sku_id END) AS uncosted_skus
            FROM sales_sku_monthly ssm
            JOIN branches b ON b.branch_id = ssm.branch_id
            JOIN skus sk    ON sk.sku_id   = ssm.sku_id
            LEFT JOIN sku_cost sc ON sc.sku_id = ssm.sku_id
            {where}
            GROUP BY b.name
        """, params).fetchall()
        trows = con.execute(f"""
            SELECT ssm.period_month AS m,
                   SUM(ssm.subtotal_ex_vat) AS total_rev,
                   SUM({costed})            AS costed_rev
            FROM sales_sku_monthly ssm
            JOIN branches b ON b.branch_id = ssm.branch_id
            JOIN skus sk    ON sk.sku_id   = ssm.sku_id
            LEFT JOIN sku_cost sc ON sc.sku_id = ssm.sku_id
            {where}
            GROUP BY ssm.period_month ORDER BY ssm.period_month
        """, params).fetchall()
        urows = con.execute(f"""
            SELECT sk.sku_code, COALESCE(sk.description, sk.sku_code) AS description,
                   c.name AS category,
                   SUM(ssm.subtotal_ex_vat) AS rev, ROUND(SUM(ssm.qty),0) AS qty
            FROM sales_sku_monthly ssm
            JOIN branches b   ON b.branch_id  = ssm.branch_id
            JOIN skus sk      ON sk.sku_id    = ssm.sku_id
            JOIN categories c ON c.category_id = sk.category_id
            LEFT JOIN sku_cost sc ON sc.sku_id = ssm.sku_id
            {where} AND {uncosted_pred}
            GROUP BY sk.sku_id
            ORDER BY rev DESC LIMIT 25
        """, params).fetchall()

    branches = []
    tot_rev = tot_costed = 0.0
    for r in sorted(brows, key=lambda x: -((x["total_rev"] or 0) - (x["costed_rev"] or 0))):
        total = r["total_rev"] or 0.0
        costed_v = r["costed_rev"] or 0.0
        unc = total - costed_v
        tot_rev += total; tot_costed += costed_v
        branches.append({
            "branch":          r["branch"],
            "total_revenue":   round(total, 2),
            "costed_revenue":  round(costed_v, 2),
            "uncosted_revenue": round(unc, 2),
            "pct_uncosted":    round(unc / total * 100, 1) if total else 0.0,
            "skus":            r["skus"] or 0,
            "uncosted_skus":   r["uncosted_skus"] or 0,
        })
    trend = []
    for r in trows:
        total = r["total_rev"] or 0.0
        costed_v = r["costed_rev"] or 0.0
        trend.append({
            "period": r["m"],
            "pct_uncosted": round((total - costed_v) / total * 100, 1) if total else 0.0,
            "uncosted_revenue": round(total - costed_v, 2),
        })
    top_uncosted = [{
        "sku_code": r["sku_code"], "description": r["description"], "category": r["category"],
        "revenue": round(r["rev"] or 0, 2), "qty": r["qty"] or 0,
    } for r in urows]

    return {
        "chain": {
            "total_revenue":    round(tot_rev, 2),
            "costed_revenue":   round(tot_costed, 2),
            "uncosted_revenue": round(tot_rev - tot_costed, 2),
            "pct_uncosted":     round((tot_rev - tot_costed) / tot_rev * 100, 1) if tot_rev else 0.0,
        },
        "branches":     branches,
        "monthly_trend": trend,
        "top_uncosted": top_uncosted,
    }


# ---------------------------------------------------------------------------
# GET /api/export/monthly-pack   (Deliverable B — Monthly Review Pack)
# ---------------------------------------------------------------------------
def _mpack_collect(month: str) -> dict:
    """Gather all the numbers needed for the Monthly Review Pack.
    Honours Rule 1 (ex-VAT revenue from sales_sku_monthly)."""
    from datetime import date as _date_cls, timedelta as _td
    # Prior month
    y, mo = _ym_parts(month)
    first = _date_cls(y, mo, 1)
    prior_first = (first - _td(days=1)).replace(day=1)
    prior_month = prior_first.strftime("%Y-%m")

    cogs_expr       = _cogs_case('ssm', 'sk', 'sc')
    costed_rev_expr = _costed_rev_case('ssm', 'sk', 'sc')

    with get_db() as con:
        # Targets from app_settings
        try:
            srows = con.execute(
                "SELECT key, value FROM app_settings WHERE key IN ('cost_target_lo','cost_target_hi')"
            ).fetchall()
            settings = {r["key"]: r["value"] for r in srows}
        except Exception:
            settings = {}
        try:
            target_lo = float(settings.get("cost_target_lo", 55))
        except Exception:
            target_lo = 55.0
        try:
            target_hi = float(settings.get("cost_target_hi", 70))
        except Exception:
            target_hi = 70.0

        # Network summary — current + prior
        def _net(m: str) -> dict:
            r = con.execute(f"""
                SELECT COALESCE(SUM(ssm.subtotal_ex_vat),0)  AS revenue,
                       COALESCE(SUM({cogs_expr}),0)          AS cogs,
                       COALESCE(SUM({costed_rev_expr}),0)    AS costed_rev,
                       COUNT(DISTINCT ssm.branch_id)         AS branch_count
                  FROM sales_sku_monthly ssm
                  JOIN branches b ON b.branch_id = ssm.branch_id
                  JOIN skus     sk ON sk.sku_id  = ssm.sku_id
                  LEFT JOIN sku_cost sc ON sc.sku_id = ssm.sku_id
                 WHERE ssm.period_month = :m
                   AND b.name != 'ALL'
                   AND COALESCE(b.active,1) = 1
            """, {"m": m}).fetchone()
            return {
                "month":       m,
                "revenue":     float(r["revenue"]    or 0),
                "cogs":        float(r["cogs"]       or 0),
                "costed_rev":  float(r["costed_rev"] or 0),
                "branch_count": int(r["branch_count"] or 0),
            }
        summary_cur   = _net(month)
        summary_prior = _net(prior_month)

        # P&L per branch (current month)
        branch_rows = con.execute(f"""
            SELECT b.name AS branch,
                   COALESCE(SUM(ssm.subtotal_ex_vat),0) AS revenue,
                   COALESCE(SUM({cogs_expr}),0)         AS cogs,
                   COALESCE(SUM({costed_rev_expr}),0)   AS costed_rev
              FROM sales_sku_monthly ssm
              JOIN branches b ON b.branch_id = ssm.branch_id
              JOIN skus     sk ON sk.sku_id  = ssm.sku_id
              LEFT JOIN sku_cost sc ON sc.sku_id = ssm.sku_id
             WHERE ssm.period_month = :m
               AND b.name != 'ALL'
               AND COALESCE(b.active,1) = 1
             GROUP BY b.branch_id
             ORDER BY revenue DESC
        """, {"m": month}).fetchall()

        # Top 30 cost drivers (current month, network-wide)
        top_drivers = con.execute(f"""
            SELECT sk.sku_code,
                   COALESCE(sk.description, sk.sku_code) AS description,
                   SUM(ssm.qty)                          AS qty,
                   SUM({cogs_expr}) / NULLIF(SUM(ssm.qty),0) AS unit_cost,
                   SUM({cogs_expr})                      AS total_cogs
              FROM sales_sku_monthly ssm
              JOIN branches b ON b.branch_id = ssm.branch_id
              JOIN skus     sk ON sk.sku_id  = ssm.sku_id
              LEFT JOIN sku_cost sc ON sc.sku_id = ssm.sku_id
             WHERE ssm.period_month = :m
               AND b.name != 'ALL'
               AND COALESCE(b.active,1) = 1
             GROUP BY sk.sku_id
             HAVING total_cogs > 0
             ORDER BY total_cogs DESC
             LIMIT 50
        """, {"m": month}).fetchall()

        # For sparklines: last-12-month qty per SKU (top drivers only)
        sku_ids_csv = ""  # unused
        spark_data: dict = {}
        if top_drivers:
            # Build SKU id list
            sku_ids = con.execute(f"""
                SELECT sku_id, sku_code FROM skus
                 WHERE sku_code IN ({",".join(":c"+str(i) for i in range(len(top_drivers)))})
            """, {f"c{i}": r["sku_code"] for i, r in enumerate(top_drivers)}).fetchall()
            code_to_id = {r["sku_code"]: r["sku_id"] for r in sku_ids}
            # 12-month window ending at `month`
            y2, m2 = _ym_parts(month)
            from_dt = _date_cls(y2, m2, 1)
            for _ in range(11):
                from_dt = (from_dt.replace(day=1) - _td(days=1)).replace(day=1)
            month_from_12 = from_dt.strftime("%Y-%m")
            ids = list(code_to_id.values())
            if ids:
                ph = ",".join(":s"+str(i) for i in range(len(ids)))
                qrows = con.execute(f"""
                    SELECT sku_id, period_month, SUM(qty) AS qty
                      FROM sales_sku_monthly
                     WHERE sku_id IN ({ph})
                       AND period_month >= :mf AND period_month <= :mt
                     GROUP BY sku_id, period_month
                     ORDER BY sku_id, period_month
                """, {**{f"s{i}": sid for i, sid in enumerate(ids)},
                      "mf": month_from_12, "mt": month}).fetchall()
                for qr in qrows:
                    spark_data.setdefault(qr["sku_id"], []).append(
                        (qr["period_month"], float(qr["qty"] or 0))
                    )
            # Map back by sku_code
            top_drivers_out = []
            total_cost = sum(float(r["total_cogs"] or 0) for r in top_drivers) or 1.0
            for r in top_drivers:
                sid = code_to_id.get(r["sku_code"])
                top_drivers_out.append({
                    "sku_code":    r["sku_code"],
                    "description": r["description"],
                    "qty":         float(r["qty"] or 0),
                    "unit_cost":   float(r["unit_cost"] or 0),
                    "total_cogs":  float(r["total_cogs"] or 0),
                    "pct_of_total": float(r["total_cogs"] or 0) / total_cost * 100,
                    "sparkline":   spark_data.get(sid, []),
                })
        else:
            top_drivers_out = []

        # Top 25 ingredients across all branches (current month)
        ing_rows = con.execute("""
            WITH eff AS (
              SELECT ssm.sku_id, ssm.qty,
                COALESCE(
                  (SELECT bi.sku_id FROM bom_ingredients bi WHERE bi.sku_id = ssm.sku_id LIMIT 1),
                  (SELECT a.resolved_sku_id FROM bom_sku_alias a
                     JOIN skus s ON s.sku_id = ssm.sku_id
                     WHERE a.bom_sku_code = s.sku_code LIMIT 1)
                ) AS bom_sku_id
              FROM sales_sku_monthly ssm
              JOIN branches b ON b.branch_id = ssm.branch_id
              WHERE ssm.period_month = :m
                AND b.name != 'ALL'
                AND COALESCE(b.active,1) = 1
            )
            SELECT COALESCE(NULLIF(bi.ingredient_code,''), '__'||bi.ingredient_name) AS ing_key,
                   MAX(bi.ingredient_code) AS code,
                   MAX(bi.ingredient_name) AS name,
                   MAX(bi.ingredient_type) AS type,
                   MAX(bi.unit) AS unit,
                   SUM(eff.qty * bi.qty_per_serving) AS total_qty,
                   SUM(eff.qty * bi.ingredient_cost) AS total_cost
              FROM eff
              JOIN bom_ingredients bi ON bi.sku_id = eff.bom_sku_id
             WHERE eff.bom_sku_id IS NOT NULL
             GROUP BY ing_key
             ORDER BY total_cost DESC
             LIMIT 50
        """, {"m": month}).fetchall()

    # Derive
    def _enrich(s):
        rev = s["revenue"]; cogs = s["cogs"]; cr = s["costed_rev"]
        s["gp"]            = rev - cogs
        s["gp_pct"]        = (s["gp"]/rev*100) if rev > 0 else None
        s["food_cost_pct"] = (cogs/rev*100)    if rev > 0 else None
        return s
    summary_cur   = _enrich(summary_cur)
    summary_prior = _enrich(summary_prior)

    def _delta_pct(cur, prev):
        if prev is None or prev == 0: return None
        return (cur - prev) / prev * 100
    summary_cur["vs_prior_revenue_pct"] = _delta_pct(summary_cur["revenue"], summary_prior["revenue"])
    summary_cur["vs_prior_gp_pct_pp"]   = (
        (summary_cur["gp_pct"] - summary_prior["gp_pct"])
        if summary_cur["gp_pct"] is not None and summary_prior["gp_pct"] is not None else None
    )

    # Per-branch P&L
    branches = []
    alerts = []
    for r in branch_rows:
        rev = float(r["revenue"] or 0)
        cogs = float(r["cogs"] or 0)
        cr   = float(r["costed_rev"] or 0)
        gp   = rev - cogs
        gp_p = (gp/rev*100)   if rev > 0 else None
        fc_p = (cogs/rev*100) if rev > 0 else None
        if fc_p is None:
            status = "no-data"
        elif fc_p > target_hi:
            status = "out"
        elif fc_p < target_lo - 5:
            status = "low"        # suspiciously low
        elif fc_p > target_hi - 3:
            status = "borderline"
        else:
            status = "in"
        branches.append({
            "branch": r["branch"], "revenue": rev, "cogs": cogs,
            "costed_rev": cr, "gp": gp, "gp_pct": gp_p, "food_cost_pct": fc_p,
            "status": status,
        })
        # Alerts
        if fc_p is not None and fc_p > 70.0:
            alerts.append({
                "branch": r["branch"], "severity": "high",
                "message": f"Food cost {fc_p:.1f}% เกิน 70% (เป้า {target_lo}-{target_hi}%)",
            })
        if gp is not None and gp < 0:
            alerts.append({
                "branch": r["branch"], "severity": "critical",
                "message": f"GP ติดลบ {gp:,.0f}฿ — ทบทวนต้นทุนเร่งด่วน",
            })

    # Top ingredients
    total_ing_cost = sum(float(r["total_cost"] or 0) for r in ing_rows) or 1.0
    ingredients = []
    for r in ing_rows:
        c = float(r["total_cost"] or 0)
        ingredients.append({
            "ingredient_code": r["code"] or "",
            "ingredient_name": r["name"] or "",
            "ingredient_type": r["type"] or "other",
            "unit":            r["unit"] or "",
            "total_qty":       float(r["total_qty"] or 0),
            "total_cost":      c,
            "pct_of_total":    c / total_ing_cost * 100,
        })

    # Auto action items
    actions = []
    # 1) Branches over target
    over = sorted([b for b in branches if b["food_cost_pct"] and b["food_cost_pct"] > target_hi],
                  key=lambda x: -x["food_cost_pct"])[:5]
    for b in over:
        actions.append(f"ทบทวน BoM/ราคาขาย ที่สาขา {b['branch']} — Food Cost {b['food_cost_pct']:.1f}% (เป้า ≤{target_hi}%)")
    # 2) Top cost driver concentration
    if top_drivers_out:
        top5_pct = sum(d["pct_of_total"] for d in top_drivers_out[:5])
        if top5_pct >= 30:
            actions.append(f"Top 5 SKU คิดเป็น {top5_pct:.1f}% ของต้นทุน — เจรจาราคากับ supplier")
    # 3) Top ingredients
    if ingredients:
        top3 = ingredients[:3]
        names = ", ".join(i["ingredient_name"] for i in top3)
        actions.append(f"วัตถุดิบต้นทุนสูงสุด: {names} — เปรียบเทียบราคา supplier รายเดือน")
    # 4) GP trend
    if summary_cur.get("vs_prior_gp_pct_pp") is not None and summary_cur["vs_prior_gp_pct_pp"] < -2:
        actions.append(f"GP% ลดลง {abs(summary_cur['vs_prior_gp_pct_pp']):.1f} pp จากเดือนก่อน — สอบสวนสาเหตุ")
    if not actions:
        actions.append("ไม่มีรายการที่ต้องดำเนินการเร่งด่วนในเดือนนี้")

    return {
        "month": month,
        "prior_month": prior_month,
        "summary_current": summary_cur,
        "summary_prior":   summary_prior,
        "target_lo": target_lo,
        "target_hi": target_hi,
        "branches":  branches,
        "top_drivers": top_drivers_out,
        "ingredients": ingredients,
        "alerts": alerts,
        "actions": actions,
    }


def _mpack_render_xlsx(pack: dict) -> bytes:
    """Render the Monthly Pack to an xlsx workbook."""
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.utils import get_column_letter

    HDR_FONT  = Font(bold=True, color="FFFFFF", size=11)
    HDR_FILL  = PatternFill("solid", fgColor="1F4E79")
    TITLE     = Font(bold=True, size=14, color="1F4E79")
    SUBTITLE  = Font(italic=True, size=10, color="64748B")
    CENTER    = Alignment(horizontal="center", vertical="center")
    NUM_FMT   = '#,##0'
    PCT_FMT   = '0.0"%"'

    STATUS_COLOR = {
        "in":         ("E8F5E9", "1B5E20", "✅ ในเป้า"),
        "borderline": ("FFF3E0", "E65100", "⚠️ ใกล้ขอบ"),
        "out":        ("FFEBEE", "B71C1C", "🔴 เกินเป้า"),
        "low":        ("E3F2FD", "0D47A1", "ℹ️ ต่ำผิดปกติ"),
        "no-data":    ("ECEFF1", "455A64", "— ไม่มีข้อมูล"),
    }

    def make_header(ws, headers, row=1):
        for i, h in enumerate(headers, start=1):
            c = ws.cell(row=row, column=i, value=h)
            c.font = HDR_FONT; c.fill = HDR_FILL; c.alignment = CENTER

    def autofit(ws, min_w=10, max_w=42):
        for col in ws.columns:
            mx = max((len(str(cell.value)) if cell.value is not None else 0) for cell in col)
            ws.column_dimensions[get_column_letter(col[0].column)].width = max(min_w, min(mx + 2, max_w))

    wb = openpyxl.Workbook()

    # ── Sheet: Summary ────────────────────────────────────────────────────
    ws = wb.active
    ws.title = "Summary"
    sc = pack["summary_current"]; sp = pack["summary_prior"]
    ws["A1"] = f"Oshinei — Monthly Review Pack · {pack['month']}"
    ws["A1"].font = TITLE
    ws.merge_cells("A1:E1")
    ws["A2"] = f"เทียบกับ {pack['prior_month']} · เป้า Food Cost {pack['target_lo']:.0f}–{pack['target_hi']:.0f}%"
    ws["A2"].font = SUBTITLE
    ws.merge_cells("A2:E2")
    make_header(ws, ["Metric", pack["month"], pack["prior_month"], "Δ", "Δ %"], row=4)
    def _row(label, cur, prev, fmt=NUM_FMT, is_pct=False):
        r = ws.max_row + 1
        ws.cell(row=r, column=1, value=label).font = Font(bold=True)
        ws.cell(row=r, column=2, value=cur).number_format  = fmt
        ws.cell(row=r, column=3, value=prev).number_format = fmt
        if cur is not None and prev is not None:
            delta = cur - prev
            ws.cell(row=r, column=4, value=delta).number_format = fmt
            if prev != 0 and not is_pct:
                ws.cell(row=r, column=5, value=(delta/prev*100)).number_format = PCT_FMT
    _row("Revenue (ex-VAT)",   sc["revenue"],       sp["revenue"])
    _row("COGS",               sc["cogs"],          sp["cogs"])
    _row("Gross Profit",       sc["gp"],            sp["gp"])
    _row("GP %",               sc["gp_pct"],        sp["gp_pct"], fmt=PCT_FMT, is_pct=True)
    _row("Food Cost %",        sc["food_cost_pct"], sp["food_cost_pct"], fmt=PCT_FMT, is_pct=True)
    _row("Branch count",       sc["branch_count"],  sp["branch_count"])
    autofit(ws)

    # ── Sheet: P&L per Branch ─────────────────────────────────────────────
    ws2 = wb.create_sheet("P&L per Branch")
    make_header(ws2, ["Branch", "Revenue", "COGS", "GP", "GP %", "Food Cost %", "Status"])
    for b in pack["branches"]:
        r = ws2.max_row + 1
        ws2.cell(row=r, column=1, value=b["branch"])
        ws2.cell(row=r, column=2, value=b["revenue"]).number_format       = NUM_FMT
        ws2.cell(row=r, column=3, value=b["cogs"]).number_format          = NUM_FMT
        ws2.cell(row=r, column=4, value=b["gp"]).number_format            = NUM_FMT
        ws2.cell(row=r, column=5, value=b["gp_pct"]).number_format        = PCT_FMT
        ws2.cell(row=r, column=6, value=b["food_cost_pct"]).number_format = PCT_FMT
        bg, fg, label = STATUS_COLOR.get(b["status"], STATUS_COLOR["no-data"])
        sc_cell = ws2.cell(row=r, column=7, value=label)
        sc_cell.fill = PatternFill("solid", fgColor=bg)
        sc_cell.font = Font(color=fg, bold=True)
    autofit(ws2)

    # ── Sheet: Top 50 Cost Drivers ────────────────────────────────────────
    ws3 = wb.create_sheet("Top 50 Cost Drivers")
    make_header(ws3, ["SKU", "Description", "Qty", "Unit Cost", "Total COGS",
                       "% of Total", "12-mo Qty Trend"])
    for d in pack["top_drivers"]:
        r = ws3.max_row + 1
        ws3.cell(row=r, column=1, value=d["sku_code"])
        ws3.cell(row=r, column=2, value=d["description"])
        ws3.cell(row=r, column=3, value=d["qty"]).number_format        = NUM_FMT
        ws3.cell(row=r, column=4, value=d["unit_cost"]).number_format  = '#,##0.00'
        ws3.cell(row=r, column=5, value=d["total_cogs"]).number_format = NUM_FMT
        ws3.cell(row=r, column=6, value=d["pct_of_total"]).number_format = PCT_FMT
        spark = ",".join(f"{m}:{int(q)}" for m, q in d.get("sparkline", []))
        ws3.cell(row=r, column=7, value=spark)
    autofit(ws3)

    # ── Sheet: BoM Ingredients ────────────────────────────────────────────
    ws4 = wb.create_sheet("BoM Ingredients")
    make_header(ws4, ["Code", "Name", "Type", "Unit", "Total Qty",
                       "Total Cost", "% of Total"])
    for ing in pack["ingredients"]:
        r = ws4.max_row + 1
        ws4.cell(row=r, column=1, value=ing["ingredient_code"])
        ws4.cell(row=r, column=2, value=ing["ingredient_name"])
        ws4.cell(row=r, column=3, value=ing["ingredient_type"])
        ws4.cell(row=r, column=4, value=ing["unit"])
        ws4.cell(row=r, column=5, value=ing["total_qty"]).number_format    = '#,##0.00'
        ws4.cell(row=r, column=6, value=ing["total_cost"]).number_format   = NUM_FMT
        ws4.cell(row=r, column=7, value=ing["pct_of_total"]).number_format = PCT_FMT
    autofit(ws4)

    # ── Sheet: Alerts ─────────────────────────────────────────────────────
    ws5 = wb.create_sheet("Alerts")
    make_header(ws5, ["Branch", "Severity", "Message"])
    if not pack["alerts"]:
        ws5.cell(row=2, column=1, value="ไม่มี alert ในเดือนนี้").font = Font(italic=True)
    for a in pack["alerts"]:
        r = ws5.max_row + 1
        ws5.cell(row=r, column=1, value=a["branch"])
        sev_cell = ws5.cell(row=r, column=2, value=a["severity"])
        if a["severity"] == "critical":
            sev_cell.fill = PatternFill("solid", fgColor="FFEBEE")
            sev_cell.font = Font(color="B71C1C", bold=True)
        elif a["severity"] == "high":
            sev_cell.fill = PatternFill("solid", fgColor="FFF3E0")
            sev_cell.font = Font(color="E65100", bold=True)
        ws5.cell(row=r, column=3, value=a["message"])
    autofit(ws5)

    # ── Sheet: Action Items ───────────────────────────────────────────────
    ws6 = wb.create_sheet("Action Items")
    make_header(ws6, ["#", "Action"])
    for i, a in enumerate(pack["actions"], start=1):
        ws6.cell(row=i + 1, column=1, value=i)
        ws6.cell(row=i + 1, column=2, value=a)
    autofit(ws6)

    buf = _io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _mpack_render_pdf(pack: dict) -> bytes:
    """Render the Monthly Pack to a multi-page PDF using reportlab + matplotlib."""
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib import colors
    from reportlab.lib.units import cm
    from reportlab.platypus import (
        SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
        PageBreak, Image as RLImage,
    )
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    buf = _io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4,
                            leftMargin=1.5*cm, rightMargin=1.5*cm,
                            topMargin=1.5*cm, bottomMargin=1.5*cm,
                            title=f"Monthly Pack {pack['month']}")
    styles = getSampleStyleSheet()
    h1 = ParagraphStyle("h1", parent=styles["Title"], textColor=colors.HexColor("#1F4E79"))
    h2 = ParagraphStyle("h2", parent=styles["Heading2"], textColor=colors.HexColor("#1F4E79"))
    body  = styles["BodyText"]
    note  = ParagraphStyle("note", parent=body, textColor=colors.grey, fontSize=9, italic=True)
    story = []

    sc = pack["summary_current"]; sp = pack["summary_prior"]
    fmt_n = lambda v: f"{v:,.0f}" if isinstance(v, (int,float)) else "—"
    fmt_p = lambda v: f"{v:,.1f}%" if isinstance(v, (int,float)) else "—"

    # ── Cover page ────────────────────────────────────────────────────────
    story.append(Spacer(1, 3*cm))
    story.append(Paragraph(f"Oshinei Monthly Review Pack", h1))
    story.append(Paragraph(f"{pack['month']}", h2))
    story.append(Spacer(1, 1*cm))
    story.append(Paragraph(f"<b>{sc['branch_count']}</b> สาขา · เป้า Food Cost <b>{pack['target_lo']:.0f}–{pack['target_hi']:.0f}%</b>", body))
    story.append(Spacer(1, 0.6*cm))
    cover_tbl = Table([
        ["Metric", pack["month"], pack["prior_month"]],
        ["Revenue (ex-VAT)", fmt_n(sc["revenue"]),      fmt_n(sp["revenue"])],
        ["COGS",             fmt_n(sc["cogs"]),         fmt_n(sp["cogs"])],
        ["Gross Profit",     fmt_n(sc["gp"]),           fmt_n(sp["gp"])],
        ["GP %",             fmt_p(sc["gp_pct"]),       fmt_p(sp["gp_pct"])],
        ["Food Cost %",      fmt_p(sc["food_cost_pct"]),fmt_p(sp["food_cost_pct"])],
    ], colWidths=[5*cm, 5*cm, 5*cm])
    cover_tbl.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#1F4E79")),
        ("TEXTCOLOR",  (0,0), (-1,0), colors.white),
        ("FONTNAME",   (0,0), (-1,0), "Helvetica-Bold"),
        ("ALIGN",      (1,1), (-1,-1), "RIGHT"),
        ("FONTNAME",   (0,1), (0,-1), "Helvetica-Bold"),
        ("GRID",       (0,0), (-1,-1), 0.5, colors.HexColor("#cbd5e1")),
        ("ROWBACKGROUNDS", (0,1), (-1,-1), [colors.whitesmoke, colors.white]),
    ]))
    story.append(cover_tbl)
    story.append(Spacer(1, 1.5*cm))
    story.append(Paragraph("Generated by Oshinei Dashboard · Rule: revenue is ex-VAT.", note))
    story.append(PageBreak())

    # ── P&L per Branch ────────────────────────────────────────────────────
    story.append(Paragraph("P&L per Branch", h2))
    # Bar chart of GP% per branch
    branches = pack["branches"]
    if branches:
        labels = [b["branch"] for b in branches]
        gpps   = [b["gp_pct"] if b["gp_pct"] is not None else 0 for b in branches]
        fig, ax = plt.subplots(figsize=(7.5, max(3, len(labels)*0.25)))
        bar_colors = []
        for b in branches:
            st = b["status"]
            bar_colors.append({"in":"#10b981","borderline":"#f59e0b","out":"#ef4444",
                               "low":"#3b82f6","no-data":"#94a3b8"}.get(st, "#94a3b8"))
        ax.barh(labels, gpps, color=bar_colors)
        ax.invert_yaxis()
        ax.set_xlabel("GP %")
        ax.axvline(100 - pack["target_hi"], color="#10b981", linestyle="--", lw=0.7, alpha=0.5)
        ax.axvline(100 - pack["target_lo"], color="#10b981", linestyle="--", lw=0.7, alpha=0.5)
        ax.grid(axis="x", alpha=0.2)
        fig.tight_layout()
        imgbuf = _io.BytesIO()
        fig.savefig(imgbuf, format="png", dpi=110)
        plt.close(fig)
        imgbuf.seek(0)
        story.append(RLImage(imgbuf, width=17*cm, height=min(18*cm, max(5*cm, len(labels)*0.6*cm))))
        story.append(Spacer(1, 0.4*cm))

    # Branch table
    tbl_data = [["Branch", "Revenue", "COGS", "GP", "GP %", "FC %", "Status"]]
    for b in branches:
        tbl_data.append([
            b["branch"], fmt_n(b["revenue"]), fmt_n(b["cogs"]), fmt_n(b["gp"]),
            fmt_p(b["gp_pct"]), fmt_p(b["food_cost_pct"]),
            {"in":"✅","borderline":"⚠️","out":"🔴","low":"ℹ️","no-data":"—"}.get(b["status"],"—"),
        ])
    branch_tbl = Table(tbl_data, colWidths=[3.5*cm, 2.5*cm, 2.5*cm, 2.5*cm, 1.8*cm, 1.8*cm, 1.4*cm])
    branch_tbl.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#1F4E79")),
        ("TEXTCOLOR",  (0,0), (-1,0), colors.white),
        ("FONTNAME",   (0,0), (-1,0), "Helvetica-Bold"),
        ("FONTSIZE",   (0,0), (-1,-1), 8),
        ("ALIGN",      (1,1), (-1,-1), "RIGHT"),
        ("ALIGN",      (6,1), (6,-1), "CENTER"),
        ("GRID",       (0,0), (-1,-1), 0.3, colors.HexColor("#cbd5e1")),
        ("ROWBACKGROUNDS", (0,1), (-1,-1), [colors.whitesmoke, colors.white]),
    ]))
    story.append(branch_tbl)
    story.append(PageBreak())

    # ── Top 50 Cost Drivers ───────────────────────────────────────────────
    story.append(Paragraph("Top 50 Cost Drivers", h2))
    drivers = pack["top_drivers"]
    if drivers:
        # Chart: top 25 cost share (larger since list is now 50)
        top25 = drivers[:25]
        fig, ax = plt.subplots(figsize=(7.5, 7))
        ax.barh([d["sku_code"] for d in top25][::-1],
                [d["total_cogs"] for d in top25][::-1], color="#d946ef")
        ax.set_xlabel("Total COGS (฿)")
        ax.grid(axis="x", alpha=0.2)
        fig.tight_layout()
        imgbuf = _io.BytesIO()
        fig.savefig(imgbuf, format="png", dpi=110)
        plt.close(fig)
        imgbuf.seek(0)
        story.append(RLImage(imgbuf, width=17*cm, height=10*cm))
        story.append(Spacer(1, 0.3*cm))

        tbl_data = [["SKU", "Description", "Qty", "Unit Cost", "COGS", "%"]]
        for d in drivers[:30]:
            tbl_data.append([
                d["sku_code"], (d["description"] or "")[:32],
                fmt_n(d["qty"]), f"{d['unit_cost']:,.2f}",
                fmt_n(d["total_cogs"]), f"{d['pct_of_total']:.1f}%",
            ])
        t = Table(tbl_data, colWidths=[2.3*cm, 7*cm, 2*cm, 2*cm, 2.3*cm, 1.4*cm])
        t.setStyle(TableStyle([
            ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#1F4E79")),
            ("TEXTCOLOR",  (0,0), (-1,0), colors.white),
            ("FONTNAME",   (0,0), (-1,0), "Helvetica-Bold"),
            ("FONTSIZE",   (0,0), (-1,-1), 7),
            ("ALIGN",      (2,1), (-1,-1), "RIGHT"),
            ("GRID",       (0,0), (-1,-1), 0.3, colors.HexColor("#cbd5e1")),
        ]))
        story.append(t)
    story.append(PageBreak())

    # ── BoM Ingredients ───────────────────────────────────────────────────
    story.append(Paragraph("Top 25 Ingredients", h2))
    ings = pack["ingredients"]
    if ings:
        fig, ax = plt.subplots(figsize=(7.5, 5))
        ax.barh([i["ingredient_name"][:24] for i in ings][::-1],
                [i["total_cost"] for i in ings][::-1], color="#0891b2")
        ax.set_xlabel("Total Cost (฿)")
        ax.grid(axis="x", alpha=0.2)
        fig.tight_layout()
        imgbuf = _io.BytesIO()
        fig.savefig(imgbuf, format="png", dpi=110)
        plt.close(fig)
        imgbuf.seek(0)
        story.append(RLImage(imgbuf, width=17*cm, height=11*cm))
        story.append(Spacer(1, 0.3*cm))

        tbl_data = [["Code", "Name", "Type", "Qty", "Cost", "%"]]
        for i in ings:
            tbl_data.append([
                i["ingredient_code"], i["ingredient_name"][:28],
                i["ingredient_type"], f"{i['total_qty']:,.1f}",
                fmt_n(i["total_cost"]), f"{i['pct_of_total']:.1f}%",
            ])
        t = Table(tbl_data, colWidths=[2.2*cm, 6.5*cm, 2.2*cm, 2*cm, 2.3*cm, 1.4*cm])
        t.setStyle(TableStyle([
            ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#1F4E79")),
            ("TEXTCOLOR",  (0,0), (-1,0), colors.white),
            ("FONTNAME",   (0,0), (-1,0), "Helvetica-Bold"),
            ("FONTSIZE",   (0,0), (-1,-1), 7),
            ("ALIGN",      (3,1), (-1,-1), "RIGHT"),
            ("GRID",       (0,0), (-1,-1), 0.3, colors.HexColor("#cbd5e1")),
        ]))
        story.append(t)
    story.append(PageBreak())

    # ── Alerts ────────────────────────────────────────────────────────────
    story.append(Paragraph("Alerts", h2))
    alerts = pack["alerts"]
    if not alerts:
        story.append(Paragraph("ไม่มี alert ในเดือนนี้", note))
    else:
        a_data = [["Branch", "Severity", "Message"]]
        for a in alerts:
            a_data.append([a["branch"], a["severity"], a["message"]])
        a_tbl = Table(a_data, colWidths=[3.5*cm, 2.5*cm, 11*cm])
        a_tbl.setStyle(TableStyle([
            ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#1F4E79")),
            ("TEXTCOLOR",  (0,0), (-1,0), colors.white),
            ("FONTNAME",   (0,0), (-1,0), "Helvetica-Bold"),
            ("FONTSIZE",   (0,0), (-1,-1), 9),
            ("GRID",       (0,0), (-1,-1), 0.3, colors.HexColor("#cbd5e1")),
            ("VALIGN",     (0,0), (-1,-1), "TOP"),
        ]))
        story.append(a_tbl)
    story.append(PageBreak())

    # ── Action Items ──────────────────────────────────────────────────────
    story.append(Paragraph("Action Items", h2))
    for i, a in enumerate(pack["actions"], start=1):
        story.append(Paragraph(f"<b>{i}.</b> {a}", body))
        story.append(Spacer(1, 0.15*cm))

    doc.build(story)
    return buf.getvalue()


@app.get("/api/export/monthly-pack")
def export_monthly_pack(
    month:  str = Query(..., description="Period in YYYY-MM"),
    format: str = Query("xlsx", description="xlsx | pdf"),
):
    """Monthly Review Pack — multi-sheet workbook or multi-page PDF.

    Sheets / pages: Summary, P&L per Branch, Top 50 Cost Drivers,
    BoM Ingredients, Alerts, Action Items.
    """
    if not month or len(month) != 7 or month[4] != "-":
        raise HTTPException(400, "month must be YYYY-MM")
    fmt = (format or "xlsx").lower()
    if fmt not in ("xlsx", "pdf"):
        raise HTTPException(400, "format must be xlsx or pdf")
    pack = _mpack_collect(month)
    if fmt == "xlsx":
        data = _mpack_render_xlsx(pack)
        media = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        fname = f"oshinei_monthly_pack_{month}.xlsx"
    else:
        try:
            data = _mpack_render_pdf(pack)
        except ImportError as e:
            raise HTTPException(500, f"PDF dependency missing: {e}")
        media = "application/pdf"
        fname = f"oshinei_monthly_pack_{month}.pdf"
    return StreamingResponse(
        _io.BytesIO(data),
        media_type=media,
        headers={"Content-Disposition": f"attachment; filename={fname}"},
    )


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


# Manual cost corrections — applied at every startup after BOM seed.
# Use when the BOM workbook has a verified-wrong ingredient cost and cannot
# be corrected immediately. Each entry removes the SKU's BOM rows (so the
# COALESCE falls through to sku_cost) and upserts the confirmed unit cost.
_COST_PATCHES: list[tuple[str, float]] = [
    ("B0040",    13.40),  # หอยเชลล์ต้มฮอกไกโด (1 ชิ้น)  — BOM workbook had ~38.2 (wrong)
    ("B0322",    13.40),  # หอยเชลล์ฮอกไกโดซาชิมิ (1 ตัว) — same ingredient, same correction
    ("1150-084", 13.40),  # HOKKAIDO HOTATE SASHIMI (1PCS) — same ingredient, same correction
]


def _apply_cost_patches():
    """Delete BOM rows for patched SKUs and upsert the verified sku_cost."""
    if not _COST_PATCHES:
        return
    try:
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
        with get_db() as con:
            for sku_code, unit_cost in _COST_PATCHES:
                row = con.execute(
                    "SELECT sku_id FROM skus WHERE sku_code = ?", (sku_code,)
                ).fetchone()
                if row is None:
                    print(f"[cost-patch] SKU not found: {sku_code} — skipped")
                    continue
                sku_id = row[0]
                deleted = con.execute(
                    "DELETE FROM bom_ingredients WHERE sku_id = ?", (sku_id,)
                ).rowcount
                con.execute(
                    """INSERT OR REPLACE INTO sku_cost(sku_id, unit_cost, effective_from, updated_at)
                       VALUES(?, ?, ?, ?)""",
                    (sku_id, unit_cost, now[:10], now),
                )
                print(f"[cost-patch] {sku_code}: bom rows removed={deleted}, sku_cost={unit_cost}")
            con.commit()
        invalidate_cache()
    except Exception as e:
        print(f"[cost-patch] failed: {e}")


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
    _apply_cost_patches()
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
)


def _perf_startup():
    """One-time DB perf setup: build the per-SKU effective-cost lookup and ensure the
    covering indexes exist. Resilient — a failure here must never block app startup."""
    try:
        with get_db() as con:
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


# Benchmarks for ingredient-type mix (used by UI)
BOM_TYPE_BENCHMARKS = {
    "ayce_buffet": {
        "label": "Top AYCE buffet (Japan/KR)",
        "protein":   [40, 50],
        "carb":      [10, 15],
        "veg":       [15, 25],
        "sauce":     [5, 10],
        "oil":       [3, 8],
        "seasoning": [2, 5],
        "other":     [5, 10],
    },
    "fine_dining": {
        "label": "Fine-dining Japanese",
        "protein":   [50, 60],
        "carb":      [5, 10],
        "veg":       [15, 20],
        "sauce":     [5, 10],
        "oil":       [3, 8],
        "seasoning": [2, 5],
        "other":     [3, 10],
    },
}


@app.get("/api/bom/sku-list")
@ttl_cache
def bom_sku_list():
    """All SKUs that have BoM ingredient data, with menu name and total recipe cost."""
    with get_db() as con:
        rows = con.execute("""
            SELECT s.sku_code,
                   s.description,
                   ROUND(SUM(b.ingredient_cost), 2) AS total_cost,
                   COUNT(*)                           AS n_ingredients
              FROM bom_ingredients b
              JOIN skus s ON s.sku_id = b.sku_id
             GROUP BY s.sku_id
             ORDER BY s.sku_code
        """).fetchall()
    out = [{
        "sku_code":      r["sku_code"],
        "description":   r["description"] or "",
        "total_cost":    float(r["total_cost"] or 0),
        "n_ingredients": int(r["n_ingredients"] or 0),
    } for r in rows]
    return {"count": len(out), "skus": out}


@app.get("/api/bom/composition")
@ttl_cache
def bom_composition(sku: str = Query(..., description="SKU code, e.g. B0005")):
    """Single-SKU ingredient breakdown."""
    sku = (sku or "").strip().upper()
    with get_db() as con:
        srow = con.execute(
            "SELECT sku_id, sku_code, description FROM skus WHERE sku_code = ?",
            (sku,),
        ).fetchone()
        if srow is None:
            raise HTTPException(404, f"SKU {sku} not found")
        rows = con.execute("""
            SELECT seq, ingredient_code, ingredient_name, qty_per_serving, unit,
                   price_per_unit, ingredient_cost, ingredient_type, source_file
              FROM bom_ingredients
             WHERE sku_id = ?
             ORDER BY seq
        """, (srow["sku_id"],)).fetchall()
    if not rows:
        raise HTTPException(404, f"No BoM data for SKU {sku}")
    total_cost = sum(float(r["ingredient_cost"] or 0) for r in rows)
    ingredients = []
    type_totals: Dict[str, float] = {}
    for r in rows:
        cost = float(r["ingredient_cost"] or 0)
        t = r["ingredient_type"] or "other"
        type_totals[t] = type_totals.get(t, 0.0) + cost
        ingredients.append({
            "seq":   int(r["seq"]),
            "code":  r["ingredient_code"] or "",
            "name":  r["ingredient_name"] or "",
            "qty":   float(r["qty_per_serving"] or 0),
            "unit":  r["unit"] or "",
            "price_per_unit": float(r["price_per_unit"] or 0),
            "cost":  round(cost, 4),
            "pct":   round(100.0 * cost / total_cost, 2) if total_cost > 0 else 0,
            "type":  t,
        })
    by_type = {k: round(100.0 * v / total_cost, 2) if total_cost > 0 else 0
               for k, v in type_totals.items()}
    by_type_cost = {k: round(v, 2) for k, v in type_totals.items()}
    return {
        "sku_code":    srow["sku_code"],
        "description": srow["description"] or "",
        "total_cost":  round(total_cost, 2),
        "ingredients": ingredients,
        "by_type":     by_type,
        "by_type_cost": by_type_cost,
        "source_file": rows[0]["source_file"] if rows else None,
    }


# Ingredient family keyword map — rolls up the many name variants (e.g. 24 salmon
# spellings) into one family so consumption truly reflects the dominant proteins.
# Order matters: first matching family wins. TH + EN keywords.
_INGREDIENT_FAMILIES = [
    # --- specific seafood/proteins FIRST (so generic "ปลา"/"หอย"/"ไข่" don't steal them) ---
    ("Salmon · แซลมอน",      ("แซลมอน", "แซม่อน", "แซลมอล", "salmon")),
    ("Eel/Unagi · ปลาไหล",   ("ปลาไหล", "อูนางิ", "unagi", "eel")),
    ("Tuna · ทูน่า",         ("ทูน่า", "มากุโระ", "โอโทโร่", "ชูโทโร่", "อาคามิ", "tuna", "maguro", "toro", "akami")),
    ("Hamachi · ฮามาจิ",     ("ฮามาจิ", "hamachi", "yellowtail", "buri")),
    ("Roe · ไข่ปลา/ไข่กุ้ง",   ("ไข่กุ้ง", "ไข่ปลา", "ไข่หอย", "roe", "ikura", "tobiko", "masago")),
    ("Shrimp · กุ้ง",        ("กุ้ง", "shrimp", "prawn", "ebi")),
    ("Crab · ปู",            ("ปูอัด", "มันปู", "ปู ", "crab", "kani")),
    ("Squid/Octopus · หมึก", ("ปลาหมึก", "หมึก", "ทาโกะ", "squid", "octopus", "ika", "tako")),
    ("Scallop · หอยเชลล์",   ("หอยเชลล์", "scallop", "hotate")),
    ("Oyster · หอยนางรม",    ("หอยนางรม", "oyster")),
    ("Uni · อูนิ",           ("อูนิ", "uni", "sea urchin")),
    ("Shellfish · หอย",      ("หอยแมลงภู่", "หอยลาย", "หอย", "mussel", "clam")),  # generic หอย AFTER scallop/oyster
    ("Fish (other) · ปลาอื่นๆ", ("ปลาเนื้อขาว", "ปลาซาบะ", "ซาบะ", "ปลาดอลลี่", "ปลาตาเดียว",
                              "คามาโบโกะ", "ลูกชิ้นปลา", "ปลา", "fish", "saba", "dory", "tai")),  # generic ปลา last among fish
    # --- meats ---
    ("Beef · เนื้อวัว",       ("เนื้อวัว", "ลิ้นวัว", "โคขุน", "ชาโรเล่ส์", "เสือร้องไห้", "ใบพาย",
                              "ออสเตรเลีย", "วากิว", "สเต็ก", "เนื้อสไลด์", "เนื้อสันใน", "เนื้อเสือ",
                              "beef", "wagyu", "charolais", "sirloin")),
    ("Pork · หมู",           ("หมู", "เบคอน", "pork", "bacon", "buta")),
    ("Chicken · ไก่",        ("ไก่", "chicken", "tori")),
    ("Egg · ไข่",            ("ไข่", "ไข่หวาน", "ทามาโกะ", "egg", "tamago")),  # generic ไข่ AFTER roe
    # --- staples / sides ---
    ("Noodle · เส้น",        ("เส้น", "โซบะ", "อุด้ง", "ราเมน", "บุก", "ชิราตากิ", "udon", "soba", "ramen", "noodle")),
    ("Rice · ข้าว",          ("ข้าวญี่ปุ่น", "ข้าวสวย", "ข้าว", "rice", "gohan")),
    ("Seaweed · สาหร่าย",    ("สาหร่าย", "โนริ", "วากาเมะ", "nori", "seaweed", "wakame")),
    ("Tofu · เต้าหู้",        ("เต้าหู้", "tofu")),
    ("Cheese/Dairy · ชีส/นม", ("ชีส", "เนย", "ครีม", "นมสด", "cheese", "butter", "cream")),
    ("Flour/Batter · แป้ง",   ("แป้ง", "เทมปุระ", "เกล็ดขนมปัง", "เกล็ดแป้ง", "tempura", "breadcrumb", "flour", "panko")),
    ("Mushroom · เห็ด",      ("เห็ด", "mushroom", "shiitake", "enoki")),
    # --- fats / seasonings (seasoning BEFORE veg so พริกไทย≠พริก) ---
    ("Oil · น้ำมัน",         ("น้ำมัน", "oil")),
    ("Sauce/Seasoning · ซอส/เครื่องปรุง",
                            ("ซอส", "น้ำซุป", "น้ำดอง", "น้ำจิ้ม", "น้ำพอนสึ", "พอนสึ", "น้ำยำ",
                             "โชยุ", "โชยู", "มิริน", "มายองเนส", "เทอริยากิ", "สาเก", "เกลือ",
                             "พริกไทย", "ดาชิ", "มิโซะ", "sauce", "teriyaki", "mayo", "mirin",
                             "soy", "shoyu", "dashi", "ponzu", "miso", "broth", "soup")),
    ("Vegetable · ผัก",      ("ผัก", "สลัด", "แตงกวา", "ต้นหอม", "ขิง", "หอมใหญ่", "หอม",
                             "หัวไชเท้า", "มะเขือเทศ", "พริก", "กระเทียม", "แครอท", "มะนาว",
                             "ใบโอบะ", "veg", "salad", "lettuce", "onion", "garlic", "carrot")),
]

def _ingredient_family(name: str) -> str:
    """Map an ingredient name to its family label, else 'Other · อื่นๆ'."""
    if not name:
        return "Other · อื่นๆ"
    low = name.lower()
    for label, kws in _INGREDIENT_FAMILIES:
        for kw in kws:
            if kw.lower() in low:
                return label
    return "Other · อื่นๆ"


@app.get("/api/bom/consumption-analysis")
@ttl_cache
def bom_consumption_analysis(
    month_from: Optional[str] = Query(None),
    month_to:   Optional[str] = Query(None),
    branch:     Optional[str] = Query(None),
    limit:      int           = Query(50),
    group_family: bool        = Query(False, description="Roll up name variants into ingredient families (e.g. all salmon → Salmon)"),
):
    """
    Period rollup of ingredient consumption across all sold menus.
    Joins sales_sku_monthly x bom_ingredients.

    group_family=true consolidates the many spelling variants of the same
    ingredient (e.g. 24 salmon names) into one family line, so the list reflects
    true ingredient dominance (salmon ≈ 25% of cost) instead of being fragmented.
    """
    filters = ["b.name != 'ALL'"]
    params:  Dict = {}
    _month_range_filter(filters, params, month_from, month_to)
    if branch:
        filters.append("b.name = :branch")
        params["branch"] = branch
    where = " AND ".join(filters)

    sql = """
        SELECT
            COALESCE(NULLIF(bi.ingredient_code, ''), '__' || bi.ingredient_name) AS ing_key,
            MAX(bi.ingredient_code)   AS code,
            MAX(bi.ingredient_name)   AS name,
            MAX(bi.ingredient_type)   AS type,
            MAX(bi.unit)              AS unit,
            SUM(ssm.qty * bi.qty_per_serving) AS total_qty,
            SUM(ssm.qty * bi.ingredient_cost) AS total_cost,
            COUNT(DISTINCT ssm.sku_id)        AS n_menus
          FROM sales_sku_monthly ssm
          JOIN branches b         ON b.branch_id = ssm.branch_id
          JOIN bom_ingredients bi ON bi.sku_id   = ssm.sku_id
         WHERE """ + where + """
         GROUP BY ing_key
         ORDER BY total_cost DESC
    """
    with get_db() as con:
        rows = con.execute(sql, params).fetchall()

    grand_total = sum(float(r["total_cost"] or 0) for r in rows)
    all_rows = []
    type_totals: Dict[str, float] = {}
    for r in rows:
        cost = float(r["total_cost"] or 0)
        t = r["type"] or "other"
        type_totals[t] = type_totals.get(t, 0.0) + cost
        all_rows.append({
            "code":     r["code"] or "",
            "name":     r["name"] or "",
            "type":     t,
            "unit":     r["unit"] or "",
            "total_qty": round(float(r["total_qty"] or 0), 2),
            "total_cost": round(cost, 2),
            "pct":      round(100.0 * cost / grand_total, 2) if grand_total > 0 else 0,
            "n_menus":  int(r["n_menus"] or 0),
        })
    # Optional family roll-up: consolidate name variants (24 salmon spellings →
    # one "Salmon" line) so dominance is visible instead of fragmented.
    if group_family:
        fam: Dict[str, dict] = {}
        for r in all_rows:
            label = _ingredient_family(r["name"])
            f = fam.setdefault(label, {
                "code": "", "name": label, "type": r["type"], "unit": r["unit"],
                "total_qty": 0.0, "total_cost": 0.0, "n_menus": 0, "n_variants": 0,
            })
            f["total_qty"]  += r["total_qty"]
            f["total_cost"] += r["total_cost"]
            f["n_menus"]     = max(f["n_menus"], r["n_menus"])
            f["n_variants"] += 1
        all_rows = sorted(fam.values(), key=lambda x: x["total_cost"], reverse=True)
        for r in all_rows:
            r["total_qty"]  = round(r["total_qty"], 2)
            r["total_cost"] = round(r["total_cost"], 2)
            r["pct"]        = round(100.0 * r["total_cost"] / grand_total, 2) if grand_total > 0 else 0

    by_type = {k: round(100.0 * v / grand_total, 2) if grand_total > 0 else 0
               for k, v in type_totals.items()}
    by_type_cost = {k: round(v, 2) for k, v in type_totals.items()}

    bench = BOM_TYPE_BENCHMARKS["ayce_buffet"]
    bench_status = {}
    for t, pct in by_type.items():
        rng = bench.get(t)
        if not rng:
            bench_status[t] = {"status": "n/a", "range": None, "pct": pct}
            continue
        if rng[0] <= pct <= rng[1]:
            st = "in"
        elif pct < rng[0]:
            st = "under"
        else:
            st = "over"
        bench_status[t] = {"status": st, "range": rng, "pct": pct}

    return {
        "month_from": month_from,
        "month_to":   month_to,
        "branch":     branch,
        "grand_total_cost": round(grand_total, 2),
        "grouped":          group_family,
        "ingredients":      all_rows[:limit],
        "n_ingredients_total": len(all_rows),
        "by_type":          by_type,
        "by_type_cost":     by_type_cost,
        "benchmarks":       BOM_TYPE_BENCHMARKS,
        "benchmark_status": bench_status,
    }


# ════════════════════════════════════════════════════════════════════════════
# Phase 11 — Cost recalibration tool
# ════════════════════════════════════════════════════════════════════════════
@app.get("/api/cost/recalibration")
@ttl_cache
def cost_recalibration(
    target_pct:  float = Query(65.0, description="Target food cost % (typical AYCE: 55-70)"),
    limit:       int   = Query(50,   description="Top N cost-driver SKUs to surface (default 50)"),
    month_from:  Optional[str] = Query(None),
    month_to:    Optional[str] = Query(None),
):
    """Top-N cost-driver SKUs with recalibration suggestions.

    Returns each SKU's current unit_cost, total consumption qty, current COGS
    contribution, % of total COGS, average à la carte selling price (sanity check),
    and a suggested new unit_cost based on proportional scaling so overall food
    cost % hits target_pct.
    """
    # Phase 14b: use _cogs_case so monthly overrides are honored.
    # fix G3: use LEFT JOIN and HAVING so BoM-only SKUs (no sku_cost row) are included
    cogs_expr = _cogs_case(ssm='ssm', sk='sk', sc='sc')
    filters = [f"sk.sku_code NOT IN {_EASY_TIER_CODES_SQL}",
               "sk.sku_code != 'SERVICECHARGE'"]
    params: dict = {}
    if month_from:
        filters.append("ssm.period_month >= :mf")
        params["mf"] = month_from
    if month_to:
        filters.append("ssm.period_month <= :mt")
        params["mt"] = month_to
    where = "WHERE " + " AND ".join(filters)

    totals_filters = []
    if month_from: totals_filters.append("ssm.period_month >= :mf")
    if month_to:   totals_filters.append("ssm.period_month <= :mt")
    totals_where = ("WHERE " + " AND ".join(totals_filters)) if totals_filters else ""

    with get_db() as con:
        totals = con.execute(f"""
            SELECT
              SUM(ssm.subtotal_ex_vat) AS rev,
              SUM(CASE
                WHEN sk.sku_code IN {_EASY_TIER_CODES_SQL} THEN 0
                ELSE {cogs_expr}
              END) AS cogs
            FROM sales_sku_monthly ssm
            JOIN skus sk ON sk.sku_id = ssm.sku_id
            LEFT JOIN sku_cost sc ON sc.sku_id = ssm.sku_id
            {totals_where}
        """, params).fetchone()

        rev  = totals["rev"]  or 0
        cogs = totals["cogs"] or 0
        target_cogs = rev * target_pct / 100
        scale = (target_cogs / cogs) if cogs > 0 else 1.0

        # Phase 18d deep-verify fix: current_unit_cost should reflect EFFECTIVE
        # cost (monthly override → default fallback), not just sku_cost default.
        # Derived as SUM(cogs) / SUM(qty) which is the weighted-avg effective
        # unit_cost over the selected period.
        rows = con.execute(f"""
            SELECT sk.sku_code,
                   COALESCE(sk.description, sk.sku_code) AS description,
                   sc.unit_cost AS default_unit_cost,
                   SUM(ssm.qty) AS qty,
                   SUM(ssm.subtotal_ex_vat) AS rev,
                   SUM({cogs_expr}) AS cogs,
                   CASE WHEN SUM(ssm.qty) > 0 THEN SUM({cogs_expr}) / SUM(ssm.qty) ELSE sc.unit_cost END AS current_unit_cost,
                   SUM(ssm.subtotal_ex_vat) / NULLIF(SUM(ssm.qty), 0) AS avg_sell
            FROM sales_sku_monthly ssm
            JOIN skus sk ON sk.sku_id = ssm.sku_id
            LEFT JOIN sku_cost sc ON sc.sku_id = ssm.sku_id
            {where}
            GROUP BY sk.sku_id
            HAVING SUM({cogs_expr}) > 0
            ORDER BY SUM({cogs_expr}) DESC
            LIMIT :n
        """, {**params, "n": limit}).fetchall()

        skus = []
        for r in rows:
            d = dict(r)
            uc      = d["current_unit_cost"] or 0
            sku_qty = d["qty"] or 0
            sku_rev = d["rev"] or 0
            sku_cogs = d["cogs"] or 0
            suggested = round(uc * scale, 2)
            avg_sell = d["avg_sell"] or 0
            # Flag suspect: cost > avg_sell (means cost > what customer paid à la carte)
            # avg_sell ≈ 0 means buffet-consumed only; can't validate
            flag = None
            if avg_sell > 0 and uc > avg_sell * 0.9:
                flag = "cost ≥ 90% of sell price"
            elif avg_sell < 1 and sku_qty > 10000:
                flag = "high qty, no à la carte sales (buffet-only)"
            skus.append({
                "sku_code":            d["sku_code"],
                "description":         d["description"],
                "current_unit_cost":   round(uc, 2),
                "qty":                 int(sku_qty),
                "total_rev":           round(sku_rev, 0),
                "total_cogs":          round(sku_cogs, 0),
                "pct_of_total_cogs":   round(sku_cogs / cogs * 100, 2) if cogs > 0 else 0,
                "avg_sell_per_unit":   round(avg_sell, 2),
                "suggested_unit_cost": suggested,
                "flag":                flag,
            })

        # Rule 2: pair the cumulative total with a monthly trend.
        trend_rows = con.execute(f"""
            SELECT ssm.period_month AS period_month,
              SUM(ssm.subtotal_ex_vat) AS rev,
              SUM(CASE WHEN sk.sku_code IN {_EASY_TIER_CODES_SQL} THEN 0
                       ELSE {cogs_expr} END) AS cogs
            FROM sales_sku_monthly ssm
            JOIN skus sk ON sk.sku_id = ssm.sku_id
            LEFT JOIN sku_cost sc ON sc.sku_id = ssm.sku_id
            {totals_where}
            GROUP BY ssm.period_month
            ORDER BY ssm.period_month
        """, params).fetchall()
        monthly_trend = []
        for tr in trend_rows:
            mrev = float(tr["rev"] or 0); mcogs = float(tr["cogs"] or 0)
            monthly_trend.append({
                "period_month":  tr["period_month"],
                "revenue":       round(mrev, 2),
                "cogs":          round(mcogs, 2),
                "food_cost_pct": round(mcogs / mrev * 100, 2) if mrev > 0 else None,
            })

        return {
            "current_revenue":         round(rev, 0),
            "current_cogs":            round(cogs, 0),
            "current_food_cost_pct":   round(cogs / rev * 100, 2) if rev > 0 else 0,
            "target_food_cost_pct":    target_pct,
            "target_cogs":             round(target_cogs, 0),
            "scale_factor":            round(scale, 4),
            "cogs_reduction_needed":   round(cogs - target_cogs, 0),
            "top_n_covers_pct":        round(sum(s["pct_of_total_cogs"] for s in skus), 1),
            "skus":                    skus,
            "monthly_trend":           monthly_trend,
            "month_from":              month_from,
            "month_to":                month_to,
        }


class CostUpdate(BaseModel):
    sku_code:  str
    unit_cost: float


@app.post("/api/cost/bulk-update", dependencies=[Depends(require_write_token)])
def cost_bulk_update(updates: list[CostUpdate]):
    """Apply unit_cost updates to multiple SKUs. Returns rows updated + any errors.
    Used by the Phase 11 recalibration UI to apply user edits in one click."""
    written = 0
    unknown: list = []
    with get_db() as con:
        for u in updates:
            row = con.execute(
                "SELECT sku_id FROM skus WHERE sku_code = ?", (u.sku_code,)
            ).fetchone()
            if not row:
                unknown.append(u.sku_code)
                continue
            con.execute(
                "INSERT OR REPLACE INTO sku_cost (sku_id, unit_cost, updated_at) "
                "VALUES (?, ?, CURRENT_TIMESTAMP)",
                (row["sku_id"], u.unit_cost),
            )
            written += 1
        con.commit()
    invalidate_cache()
    return {"written": written, "unknown_skus": unknown}


# ════════════════════════════════════════════════════════════════════════════
# Phase 12 — Per-branch BoM cost analysis
# ════════════════════════════════════════════════════════════════════════════
# Combined whole-restaurant view for a single branch. Shows:
#   • Branch totals (revenue, cogs, gp, gp%, food cost%)
#   • Monthly trend
#   • Top cost-driver menus with monthly cogs sparkline
#   • Ingredient-level drill-down (joining sales × bom_ingredients,
#     using bom_sku_alias as a fallback for SKUs without a direct recipe)
#   • Cost concentration (top-5/10/20 share of branch COGS)
#
# This is the back-end for the "🔬 BoM รายสาขา" sidebar tab.
@app.get("/api/branch-bom/analysis")
@ttl_cache
def branch_bom_analysis(
    branch:     str           = Query(..., description="Branch name, or 'ALL' for chain-wide aggregate"),
    month_from: Optional[str] = Query(None),
    month_to:   Optional[str] = Query(None),
    top_n:      int           = Query(50, description="How many top menus to return (default 50)"),
    granularity: str          = Query("month", description="day | week | month — bucket size for the trend array"),
):
    """Per-branch BoM cost analysis (or chain-wide if branch='ALL').

    The `trend` array (always returned) honours the granularity param:
      • month  — one entry per ``YYYY-MM`` (mirrors `monthly_trend`).
      • week   — ISO weeks ``YYYY-Www``; daily COGS pro-rated by daily tx revenue.
      • day    — one entry per ``YYYY-MM-DD``; same pro-ration pattern as `/api/buffet/gp`.

    `monthly_trend` is preserved (always monthly) for backwards compatibility.

    Phase 16: when `branch='ALL'` (or 'all'), aggregates across ALL active
    branches — chain-wide overview. Hides per-branch filter, returns same
    response shape with `branch: "ALL (เครือ)"`.
    """
    if not branch:
        raise HTTPException(400, "branch is required ('ALL' for chain-wide)")
    if granularity not in ("day", "week", "month"):
        granularity = "month"

    is_chain = branch.upper() == "ALL"

    # Effective COGS / costed-rev for the branch (or chain-wide) ------------
    if is_chain:
        filters = ["COALESCE(b.active,1) = 1", "b.name != 'ALL'"]
        params: dict = {}
    else:
        filters = ["b.name = :branch", "b.name != 'ALL'"]
        params = {"branch": branch}
    _month_range_filter(filters, params, month_from, month_to, None, col="ssm.period_month")
    where = "WHERE " + " AND ".join(filters)

    cogs_expr        = _cogs_case('ssm', 'sk', 'sc')
    costed_rev_expr  = _costed_rev_case('ssm', 'sk', 'sc')

    with get_db() as con:
        # Verify branch exists (skip for chain-wide mode)
        if not is_chain:
            brow = con.execute(
                "SELECT branch_id, name FROM branches WHERE name = ?", (branch,)
            ).fetchone()
            if brow is None:
                raise HTTPException(404, f"Branch '{branch}' not found")

        # ── 1. Branch totals ──────────────────────────────────────────────
        totals_row = con.execute(f"""
            SELECT
              COALESCE(SUM(ssm.subtotal_ex_vat), 0)        AS revenue_ex,
              COALESCE(SUM(ssm.total_inc_vat),  0)        AS pos_total_inc_vat_diagnostic,
              COALESCE(SUM({cogs_expr}), 0)                AS cogs,
              COALESCE(SUM({costed_rev_expr}), 0)          AS costed_rev_ex,
              COUNT(DISTINCT ssm.sku_id)                   AS menus_total,
              COUNT(DISTINCT CASE WHEN {cogs_expr} > 0 THEN ssm.sku_id END) AS menus_with_cost
            FROM sales_sku_monthly ssm
            JOIN branches b   ON b.branch_id = ssm.branch_id
            JOIN skus     sk  ON sk.sku_id   = ssm.sku_id
            LEFT JOIN sku_cost sc ON sc.sku_id = ssm.sku_id
            {where}
        """, params).fetchone()

        # ── 2. Monthly trend ──────────────────────────────────────────────
        trend_rows = con.execute(f"""
            SELECT ssm.period_month AS m,
                   COALESCE(SUM(ssm.subtotal_ex_vat), 0)        AS revenue_ex,
                   COALESCE(SUM({cogs_expr}), 0)                AS cogs,
                   COALESCE(SUM({costed_rev_expr}), 0)          AS costed_rev_ex
              FROM sales_sku_monthly ssm
              JOIN branches b   ON b.branch_id = ssm.branch_id
              JOIN skus     sk  ON sk.sku_id   = ssm.sku_id
              LEFT JOIN sku_cost sc ON sc.sku_id = ssm.sku_id
            {where}
            GROUP BY ssm.period_month
            ORDER BY ssm.period_month
        """, params).fetchall()

        # ── 3. Top menus by COGS (limit 30) ───────────────────────────────
        top_menu_rows = con.execute(f"""
            SELECT ssm.sku_id,
                   sk.sku_code,
                   COALESCE(sk.description,'') AS description,
                   COALESCE(SUM(ssm.qty), 0)                       AS qty,
                   COALESCE(SUM(ssm.subtotal_ex_vat), 0)           AS revenue,
                   COALESCE(SUM({cogs_expr}), 0)                   AS total_cogs,
                   CASE WHEN SUM(ssm.qty) > 0
                        THEN SUM({cogs_expr}) / SUM(ssm.qty)
                        ELSE 0 END                                  AS avg_unit_cost
              FROM sales_sku_monthly ssm
              JOIN branches b   ON b.branch_id = ssm.branch_id
              JOIN skus     sk  ON sk.sku_id   = ssm.sku_id
              LEFT JOIN sku_cost sc ON sc.sku_id = ssm.sku_id
            {where}
            GROUP BY ssm.sku_id
            HAVING total_cogs > 0
            ORDER BY total_cogs DESC
            LIMIT :top_n
        """, {**params, "top_n": int(top_n)}).fetchall()

        top_sku_ids = [r["sku_id"] for r in top_menu_rows]

        # Monthly cogs per top sku — for sparklines
        monthly_by_sku: Dict[int, list] = {sid: [] for sid in top_sku_ids}
        if top_sku_ids:
            placeholders = ",".join(":sid" + str(i) for i in range(len(top_sku_ids)))
            mp = {**params, **{"sid" + str(i): sid for i, sid in enumerate(top_sku_ids)}}
            mrows = con.execute(f"""
                SELECT ssm.sku_id, ssm.period_month AS m,
                       COALESCE(SUM({cogs_expr}), 0) AS cogs
                  FROM sales_sku_monthly ssm
                  JOIN branches b   ON b.branch_id = ssm.branch_id
                  JOIN skus     sk  ON sk.sku_id   = ssm.sku_id
                  LEFT JOIN sku_cost sc ON sc.sku_id = ssm.sku_id
                {where} AND ssm.sku_id IN ({placeholders})
                GROUP BY ssm.sku_id, ssm.period_month
                ORDER BY ssm.sku_id, ssm.period_month
            """, mp).fetchall()
            for r in mrows:
                monthly_by_sku.setdefault(r["sku_id"], []).append({
                    "month": r["m"],
                    "cogs":  round(float(r["cogs"] or 0), 2),
                })

        # has_bom_recipe flag — does this sku_id have a direct or aliased recipe?
        bom_flag_rows = con.execute(f"""
            SELECT s.sku_id,
              CASE WHEN EXISTS (SELECT 1 FROM bom_ingredients b WHERE b.sku_id = s.sku_id)
                    OR EXISTS (SELECT 1 FROM bom_sku_alias a WHERE a.bom_sku_code = s.sku_code)
                   THEN 1 ELSE 0 END AS has_bom
              FROM skus s WHERE s.sku_id IN ({placeholders if top_sku_ids else "NULL"})
        """, {"sid" + str(i): sid for i, sid in enumerate(top_sku_ids)} if top_sku_ids else {}).fetchall() if top_sku_ids else []
        has_bom_map = {r["sku_id"]: bool(r["has_bom"]) for r in bom_flag_rows}

        # ── 4. Ingredient-level drill-down ────────────────────────────────
        # Effective BoM sku id = direct match, else aliased.
        # We sum (sales qty × ingredient_cost) — same approach as
        # /api/bom/consumption-analysis but using the alias fallback.
        ing_rows = con.execute(f"""
            WITH eff AS (
              SELECT ssm.sku_id   AS sales_sku_id,
                     ssm.qty      AS qty,
                     COALESCE(
                       (SELECT bi.sku_id FROM bom_ingredients bi WHERE bi.sku_id = ssm.sku_id LIMIT 1),
                       (SELECT a.resolved_sku_id
                          FROM bom_sku_alias a
                          JOIN skus s ON s.sku_id = ssm.sku_id
                         WHERE a.bom_sku_code = s.sku_code LIMIT 1)
                     ) AS bom_sku_id
                FROM sales_sku_monthly ssm
                JOIN branches b   ON b.branch_id = ssm.branch_id
                JOIN skus     sk  ON sk.sku_id   = ssm.sku_id
                LEFT JOIN sku_cost sc ON sc.sku_id = ssm.sku_id
              {where}
            )
            SELECT
              COALESCE(NULLIF(bi.ingredient_code,''), '__' || bi.ingredient_name) AS ing_key,
              MAX(bi.ingredient_code)              AS code,
              MAX(bi.ingredient_name)              AS name,
              MAX(bi.ingredient_type)              AS type,
              MAX(bi.unit)                         AS unit,
              SUM(eff.qty * bi.qty_per_serving)    AS total_qty,
              SUM(eff.qty * bi.ingredient_cost)    AS total_cost,
              COUNT(DISTINCT eff.sales_sku_id)     AS n_menus
              FROM eff
              JOIN bom_ingredients bi ON bi.sku_id = eff.bom_sku_id
             WHERE eff.bom_sku_id IS NOT NULL
             GROUP BY ing_key
             ORDER BY total_cost DESC
             LIMIT 25
        """, params).fetchall()

        # For each top ingredient: list the menus (sku_codes) using it
        ing_menus: Dict[str, list] = {}
        if ing_rows:
            ing_key_list = [r["ing_key"] for r in ing_rows]
            ph = ",".join(":ik" + str(i) for i in range(len(ing_key_list)))
            ikp = {**params, **{"ik" + str(i): k for i, k in enumerate(ing_key_list)}}
            menu_link_rows = con.execute(f"""
                WITH eff AS (
                  SELECT ssm.sku_id AS sales_sku_id, sk.sku_code AS sku_code,
                         ssm.qty AS qty,
                         COALESCE(
                           (SELECT bi.sku_id FROM bom_ingredients bi WHERE bi.sku_id = ssm.sku_id LIMIT 1),
                           (SELECT a.resolved_sku_id
                              FROM bom_sku_alias a
                              JOIN skus s ON s.sku_id = ssm.sku_id
                             WHERE a.bom_sku_code = s.sku_code LIMIT 1)
                         ) AS bom_sku_id
                    FROM sales_sku_monthly ssm
                    JOIN branches b   ON b.branch_id = ssm.branch_id
                    JOIN skus     sk  ON sk.sku_id   = ssm.sku_id
                    LEFT JOIN sku_cost sc ON sc.sku_id = ssm.sku_id
                  {where}
                )
                SELECT
                  COALESCE(NULLIF(bi.ingredient_code,''), '__' || bi.ingredient_name) AS ing_key,
                  eff.sku_code AS sku_code,
                  SUM(eff.qty * bi.ingredient_cost) AS cost
                  FROM eff
                  JOIN bom_ingredients bi ON bi.sku_id = eff.bom_sku_id
                 WHERE eff.bom_sku_id IS NOT NULL
                   AND COALESCE(NULLIF(bi.ingredient_code,''), '__' || bi.ingredient_name) IN ({ph})
                 GROUP BY ing_key, eff.sku_code
            """, ikp).fetchall()
            # bucket by ing_key, sort each by cost desc, keep top 5 sku_codes
            from collections import defaultdict as _dd
            tmp = _dd(list)
            for r in menu_link_rows:
                tmp[r["ing_key"]].append((r["sku_code"], float(r["cost"] or 0)))
            for k, lst in tmp.items():
                lst.sort(key=lambda x: -x[1])
                ing_menus[k] = [code for code, _ in lst[:5]]

        # ── 5. Total # of menus with a BoM recipe (for coverage stat) ─────
        coverage_row = con.execute(f"""
            WITH eff AS (
              SELECT DISTINCT ssm.sku_id,
                COALESCE(
                  (SELECT bi.sku_id FROM bom_ingredients bi WHERE bi.sku_id = ssm.sku_id LIMIT 1),
                  (SELECT a.resolved_sku_id
                     FROM bom_sku_alias a
                     JOIN skus s ON s.sku_id = ssm.sku_id
                    WHERE a.bom_sku_code = s.sku_code LIMIT 1)
                ) AS bom_sku_id
                FROM sales_sku_monthly ssm
                JOIN branches b   ON b.branch_id = ssm.branch_id
              {where}
            )
            SELECT SUM(CASE WHEN bom_sku_id IS NOT NULL THEN 1 ELSE 0 END) AS with_bom,
                   COUNT(*) AS total
              FROM eff
        """, params).fetchone()

    # ── Assemble response ─────────────────────────────────────────────────
    revenue_ex    = float(totals_row["revenue_ex"]    or 0)
    revenue_inc   = float(totals_row["pos_total_inc_vat_diagnostic"] or 0)
    cogs          = float(totals_row["cogs"]          or 0)
    costed_rev_ex = float(totals_row["costed_rev_ex"] or 0)   # coverage diagnostic
    gp            = revenue_ex - cogs
    gp_pct        = (gp   / revenue_ex * 100) if revenue_ex > 0 else None
    food_pct      = (cogs / revenue_ex * 100) if revenue_ex > 0 else None

    # period
    months_list   = [r["m"] for r in trend_rows]
    n_months      = len(months_list)

    monthly_trend = []
    for r in trend_rows:
        m_cogs      = float(r["cogs"] or 0)
        m_costed    = float(r["costed_rev_ex"] or 0)
        m_rev       = float(r["revenue_ex"] or 0)
        m_gp        = m_rev - m_cogs
        m_gp_pct    = (m_gp   / m_rev * 100) if m_rev > 0 else None
        m_food_pct  = (m_cogs / m_rev * 100) if m_rev > 0 else None
        monthly_trend.append({
            "month":         r["m"],
            "revenue":       round(m_rev, 2),
            "cogs":          round(m_cogs, 2),
            "gp":            round(m_gp, 2),
            "gp_pct":        round(m_gp_pct, 1)   if m_gp_pct   is not None else None,
            "food_cost_pct": round(m_food_pct, 1) if m_food_pct is not None else None,
        })

    top_menus = []
    for r in top_menu_rows:
        tc      = float(r["total_cogs"] or 0)
        pct     = (tc / cogs * 100) if cogs > 0 else 0
        top_menus.append({
            "sku_id":            r["sku_id"],
            "sku_code":          r["sku_code"],
            "description":       r["description"],
            "qty":               round(float(r["qty"] or 0), 2),
            "revenue":           round(float(r["revenue"] or 0), 2),
            "unit_cost":         round(float(r["avg_unit_cost"] or 0), 4),
            "total_cogs":        round(tc, 2),
            "pct_of_branch_cogs": round(pct, 2),
            "monthly_cogs":      monthly_by_sku.get(r["sku_id"], []),
            "has_bom_recipe":    has_bom_map.get(r["sku_id"], False),
        })

    # concentration
    cumulative = 0.0
    cum_at = {}
    for i, m in enumerate(top_menus, start=1):
        cumulative += m["total_cogs"]
        cum_at[i] = cumulative
    def _share(n):
        if cogs <= 0 or n not in cum_at: return None
        # Pad to n: if fewer than n menus, use last cumulative.
        if not cum_at: return None
        max_n = max(cum_at)
        val = cum_at.get(n, cum_at[max_n])
        return round(val / cogs * 100, 2)
    concentration = {
        "top_5_menus_pct_of_cogs":  _share(5),
        "top_10_menus_pct_of_cogs": _share(10),
        "top_20_menus_pct_of_cogs": _share(20),
    }

    top_ingredients = []
    cost_by_type: Dict[str, float] = {}
    for r in ing_rows:
        c    = float(r["total_cost"] or 0)
        t    = r["type"] or "other"
        cost_by_type[t] = cost_by_type.get(t, 0.0) + c
        top_ingredients.append({
            "ingredient_code":     r["code"] or "",
            "ingredient_name":     r["name"] or "",
            "ingredient_type":     t,
            "unit":                r["unit"] or "",
            "total_qty":           round(float(r["total_qty"] or 0), 2),
            "total_cost":          round(c, 2),
            "pct_of_branch_cogs":  round(c / cogs * 100, 2) if cogs > 0 else 0,
            "n_menus_using":       int(r["n_menus"] or 0),
            "top_menus":           ing_menus.get(r["ing_key"], []),
        })
    cost_by_type_out = {k: round(v, 2) for k, v in cost_by_type.items()}

    menus_with_recipe = int(coverage_row["with_bom"] or 0) if coverage_row else 0
    menus_total       = int(coverage_row["total"]    or 0) if coverage_row else 0

    # ── Granularity-aware trend array ────────────────────────────────────
    # Always include `trend` keyed by `bucket`. For month: mirrors monthly_trend.
    # For week/day: pro-rate each month's COGS by daily tx revenue fraction
    # (same pattern as /api/buffet/gp — SKU data is monthly-only so we have
    # no day-level COGS breakdown).
    trend: list = []
    if granularity == "month":
        for r in monthly_trend:
            trend.append({
                "bucket":        r["month"],
                "revenue":       r["revenue"],
                "cogs":          r["cogs"],
                "gp":            r["gp"],
                "gp_pct":        r["gp_pct"],
                "food_cost_pct": r["food_cost_pct"],
            })
    elif months_list:
        # 1. Fetch daily transaction revenue for this branch over the months
        tx_filters = ["b.name = :branch", "b.name != 'ALL'", "st.source = 'Easy'"]
        tx_params: dict = {"branch": branch}
        _month_range_filter(tx_filters, tx_params, month_from, month_to, None,
                            col="SUBSTR(st.sold_at,1,7)")
        tx_where = "WHERE " + " AND ".join(tx_filters)
        with get_db() as con:
            daily_rows = con.execute(f"""
                SELECT DATE(st.sold_at)               AS d,
                       SUBSTR(st.sold_at,1,7)         AS m,
                       SUM(COALESCE(st.food_subtotal,0)
                         + COALESCE(st.drink_subtotal,0)
                         + COALESCE(st.other_subtotal,0)) AS rev_tx
                  FROM sales_transaction st
                  JOIN branches b ON b.branch_id = st.branch_id
                {tx_where}
                GROUP BY DATE(st.sold_at)
                ORDER BY d
            """, tx_params).fetchall()

        # Index monthly cogs / costed_rev / rev from existing trend_rows
        m_index = {r["m"]: {
            "cogs":      float(r["cogs"]          or 0),
            "costed":    float(r["costed_rev_ex"] or 0),
            "rev_ex":    float(r["revenue_ex"]    or 0),
        } for r in trend_rows}

        # Aggregate monthly tx revenue
        m_tx_rev: dict = {}
        for r in daily_rows:
            m_tx_rev[r["m"]] = m_tx_rev.get(r["m"], 0.0) + float(r["rev_tx"] or 0)

        # Build daily buckets
        from datetime import date as _date_cls
        def _week_bucket(date_str: str) -> str:
            d = _date_cls.fromisoformat(date_str)
            iso = d.isocalendar()
            return f"{iso[0]}-W{iso[1]:02d}"

        buckets: dict = {}
        for r in daily_rows:
            mo = r["m"]
            mi = m_index.get(mo)
            if not mi:
                continue
            month_tx_rev = m_tx_rev.get(mo, 0.0)
            if month_tx_rev <= 0:
                continue
            frac = float(r["rev_tx"] or 0) / month_tx_rev
            day_cogs   = mi["cogs"]   * frac
            day_costed = mi["costed"] * frac
            day_rev    = mi["rev_ex"] * frac
            key = r["d"] if granularity == "day" else _week_bucket(r["d"])
            b = buckets.setdefault(key, {"cogs": 0.0, "costed": 0.0, "rev": 0.0})
            b["cogs"]   += day_cogs
            b["costed"] += day_costed
            b["rev"]    += day_rev

        for k in sorted(buckets.keys()):
            v   = buckets[k]
            bgp = v["rev"] - v["cogs"]
            gpp = (bgp / v["rev"] * 100) if v["rev"] > 0 else None
            fcp = (v["cogs"] / v["rev"] * 100) if v["rev"] > 0 else None
            trend.append({
                "bucket":        k,
                "revenue":       round(v["rev"],   2),
                "cogs":          round(v["cogs"],  2),
                "gp":            round(bgp,        2),
                "gp_pct":        round(gpp, 1) if gpp is not None else None,
                "food_cost_pct": round(fcp, 1) if fcp is not None else None,
            })

    return {
        "branch": ("ALL (รวมเครือ)" if is_chain else branch),
        "is_chain": is_chain,
        "granularity": granularity,
        "period": {
            "month_from": month_from or (months_list[0]  if months_list else None),
            "month_to":   month_to   or (months_list[-1] if months_list else None),
            "months":     n_months,
        },
        "totals": {
            "revenue":              round(revenue_ex, 2),
            "pos_total_inc_vat_diagnostic": round(revenue_inc, 2),
            "cogs":                 round(cogs, 2),
            "costed_revenue_ex_vat": round(costed_rev_ex, 2),
            "gp":                   round(gp, 2),
            "gp_pct":               round(gp_pct, 1)   if gp_pct   is not None else None,
            "food_cost_pct":        round(food_pct, 1) if food_pct is not None else None,
            "menus_with_cost":      int(totals_row["menus_with_cost"] or 0),
            "menus_total":          int(totals_row["menus_total"] or 0),
            "menus_with_bom_recipe": menus_with_recipe,
            "menus_in_period":       menus_total,
        },
        "monthly_trend":            monthly_trend,
        "trend":                    trend,
        "top_menus_by_cost":        top_menus,
        "top_ingredients_by_cost":  top_ingredients,
        "cost_by_ingredient_type":  cost_by_type_out,
        "concentration":            concentration,
    }


# =============================================================================
# Phase 17c — business_type management
# =============================================================================
_VALID_BUSINESS_TYPES = {"buffet_tier", "buffet", "alacarte", "beverage",
                          "takeaway", "fee", "other"}


class BusinessTypeUpdate(BaseModel):
    business_type: str


@app.put("/api/skus/{sku_code}/business-type", dependencies=[Depends(require_write_token)])
def update_sku_business_type(sku_code: str, body: BusinessTypeUpdate):
    bt = (body.business_type or "").strip().lower()
    if bt not in _VALID_BUSINESS_TYPES:
        raise HTTPException(400, f"invalid business_type; must be one of {sorted(_VALID_BUSINESS_TYPES)}")
    with get_db() as con:
        cur = con.execute("UPDATE skus SET business_type=? WHERE sku_code=?", (bt, sku_code))
        con.commit()
        if cur.rowcount == 0:
            raise HTTPException(404, f"sku_code '{sku_code}' not found")
    invalidate_cache("get_cost_detail", "bom_cost_", "branch_bom_analysis")
    return {"sku_code": sku_code, "business_type": bt, "updated": True}


# =============================================================================
# Phase 17d — BoM Cost Analysis (4-mode page) + Simulator
# =============================================================================
def _service_charge_map(con, month_from=None, month_to=None, branches=None):
    """Per-(branch_name, period_month) EX-VAT service charge from the bill-level
    `sales_transaction` table.

    The `service_charge` column is stored PRE-VAT (verified: total_inc_vat ≈
    (food+drink+other - discount + service_charge) * 1.07), so it pairs directly
    with ex-VAT revenue / costed revenue. Returns {(branch, 'YYYY-MM'): amount}.
    """
    filt = ["b.name != 'ALL'", "COALESCE(b.active,1) = 1"]
    params: dict = {}
    if month_from:
        filt.append("strftime('%Y-%m',st.sold_at) >= :sf"); params["sf"] = month_from
    if month_to:
        filt.append("strftime('%Y-%m',st.sold_at) <= :stt"); params["stt"] = month_to
    if branches:
        ph = ",".join(f":sb{i}" for i in range(len(branches)))
        filt.append(f"b.name IN ({ph})")
        for i, br in enumerate(branches):
            params[f"sb{i}"] = br
    rows = con.execute(f"""
        SELECT b.name AS branch, strftime('%Y-%m',st.sold_at) AS m,
               COALESCE(SUM(st.service_charge), 0) AS svc
          FROM sales_transaction st
          JOIN branches b ON b.branch_id = st.branch_id
         WHERE {' AND '.join(filt)}
         GROUP BY b.name, m
    """, params).fetchall()
    return {(r["branch"], r["m"]): float(r["svc"] or 0) for r in rows}


def _bom_cost_period_filters(month_from, month_to, branches=None):
    """Build (where_sql, params) for chain-wide BoM cost queries."""
    filters = ["b.name != 'ALL'", "COALESCE(b.active,1) = 1"]
    params: dict = {}
    if branches:
        ph = ",".join(f":b{i}" for i in range(len(branches)))
        filters.append(f"b.name IN ({ph})")
        for i, br in enumerate(branches):
            params[f"b{i}"] = br
    _month_range_filter(filters, params, month_from, month_to, None, col="ssm.period_month")
    return "WHERE " + " AND ".join(filters), params


@app.get("/api/bom-cost/overview")
@ttl_cache
def bom_cost_overview(
    month_from: Optional[str] = Query(None),
    month_to:   Optional[str] = Query(None),
    group:      Optional[str] = Query(None),
    granularity: str          = Query("month", description="day | week | month"),
    incl_service: bool        = Query(False, description="Count ex-VAT service charge as revenue (and in the Food-Cost%/GP% denominator)"),
):
    """Chain-wide BoM cost overview - KPIs, heatmap, top-50 SKUs, top-30 ingredients,
    branch breakdown."""
    if granularity not in ("day", "week", "month"):
        granularity = "month"

    where, params = _bom_cost_period_filters(month_from, month_to)
    if group in ("master", "franchise"):
        where += " AND COALESCE(b.branch_type,'master') = :branch_group"
        params["branch_group"] = group
    cogs_expr       = _cogs_case('ssm', 'sk', 'sc')
    costed_rev_expr = _costed_rev_case('ssm', 'sk', 'sc')

    with get_db() as con:
        tot = con.execute(f"""
            SELECT COALESCE(SUM(ssm.subtotal_ex_vat), 0)       AS revenue_ex,
                   COALESCE(SUM({cogs_expr}), 0)               AS cogs,
                   COALESCE(SUM({costed_rev_expr}), 0)         AS costed_rev_ex,
                   COUNT(DISTINCT ssm.sku_id)                  AS menus_total
              FROM sales_sku_monthly ssm
              JOIN branches b   ON b.branch_id = ssm.branch_id
              JOIN skus     sk  ON sk.sku_id   = ssm.sku_id
              LEFT JOIN sku_cost sc ON sc.sku_id = ssm.sku_id
            {where}
        """, params).fetchone()

        hmap = con.execute(f"""
            SELECT b.name AS branch, ssm.period_month AS m,
                   COALESCE(SUM(ssm.subtotal_ex_vat), 0)  AS rev,
                   COALESCE(SUM({cogs_expr}), 0)          AS cogs,
                   COALESCE(SUM({costed_rev_expr}), 0)    AS costed
              FROM sales_sku_monthly ssm
              JOIN branches b   ON b.branch_id = ssm.branch_id
              JOIN skus     sk  ON sk.sku_id   = ssm.sku_id
              LEFT JOIN sku_cost sc ON sc.sku_id = ssm.sku_id
            {where}
            GROUP BY b.name, ssm.period_month
            ORDER BY b.name, ssm.period_month
        """, params).fetchall()

        top_rows = con.execute(f"""
            SELECT ssm.sku_id, sk.sku_code,
                   COALESCE(sk.description,'') AS description,
                   COALESCE(sk.business_type,'other') AS business_type,
                   COALESCE(SUM(ssm.qty), 0)            AS qty,
                   COALESCE(SUM({cogs_expr}), 0)        AS total_cogs,
                   CASE WHEN SUM(ssm.qty) > 0
                        THEN SUM({cogs_expr}) / SUM(ssm.qty)
                        ELSE 0 END                        AS avg_unit_cost
              FROM sales_sku_monthly ssm
              JOIN branches b   ON b.branch_id = ssm.branch_id
              JOIN skus     sk  ON sk.sku_id   = ssm.sku_id
              LEFT JOIN sku_cost sc ON sc.sku_id = ssm.sku_id
            {where}
            GROUP BY ssm.sku_id
            HAVING total_cogs > 0
            ORDER BY total_cogs DESC
            LIMIT 50
        """, params).fetchall()

        top_sku_ids = [r["sku_id"] for r in top_rows]
        sparkline_map: Dict[int, list] = {sid: [] for sid in top_sku_ids}
        if top_sku_ids:
            ph = ",".join(f":sid{i}" for i in range(len(top_sku_ids)))
            sp = {**params, **{f"sid{i}": sid for i, sid in enumerate(top_sku_ids)}}
            srows = con.execute(f"""
                SELECT ssm.sku_id, ssm.period_month AS m,
                       COALESCE(SUM({cogs_expr}), 0) AS cogs
                  FROM sales_sku_monthly ssm
                  JOIN branches b   ON b.branch_id = ssm.branch_id
                  JOIN skus     sk  ON sk.sku_id   = ssm.sku_id
                  LEFT JOIN sku_cost sc ON sc.sku_id = ssm.sku_id
                {where} AND ssm.sku_id IN ({ph})
                GROUP BY ssm.sku_id, ssm.period_month
                ORDER BY ssm.sku_id, ssm.period_month
            """, sp).fetchall()
            for r in srows:
                sparkline_map.setdefault(r["sku_id"], []).append({
                    "month": r["m"], "cogs": round(float(r["cogs"] or 0), 2)
                })

        branch_rows = con.execute(f"""
            SELECT b.name AS branch,
                   COALESCE(SUM(ssm.subtotal_ex_vat), 0)   AS rev,
                   COALESCE(SUM({cogs_expr}), 0)           AS cogs,
                   COALESCE(SUM({costed_rev_expr}), 0)     AS costed
              FROM sales_sku_monthly ssm
              JOIN branches b   ON b.branch_id = ssm.branch_id
              JOIN skus     sk  ON sk.sku_id   = ssm.sku_id
              LEFT JOIN sku_cost sc ON sc.sku_id = ssm.sku_id
            {where}
            GROUP BY b.name
            ORDER BY cogs DESC
        """, params).fetchall()

        svc_map = _service_charge_map(con, month_from, month_to) if incl_service else {}

        ing_rows = con.execute(f"""
            WITH eff AS (
              SELECT ssm.sku_id AS sales_sku_id, ssm.qty AS qty,
                     COALESCE(
                       (SELECT bi.sku_id FROM bom_ingredients bi WHERE bi.sku_id = ssm.sku_id LIMIT 1),
                       (SELECT a.resolved_sku_id
                          FROM bom_sku_alias a
                          JOIN skus s ON s.sku_id = ssm.sku_id
                         WHERE a.bom_sku_code = s.sku_code LIMIT 1)
                     ) AS bom_sku_id
                FROM sales_sku_monthly ssm
                JOIN branches b   ON b.branch_id = ssm.branch_id
                JOIN skus     sk  ON sk.sku_id   = ssm.sku_id
                LEFT JOIN sku_cost sc ON sc.sku_id = ssm.sku_id
              {where}
            )
            SELECT COALESCE(NULLIF(bi.ingredient_code,''), '__' || bi.ingredient_name) AS ing_key,
                   MAX(bi.ingredient_name) AS name,
                   MAX(bi.ingredient_type) AS type,
                   SUM(eff.qty * bi.ingredient_cost) AS total_cost
              FROM eff
              JOIN bom_ingredients bi ON bi.sku_id = eff.bom_sku_id
             WHERE eff.bom_sku_id IS NOT NULL
             GROUP BY ing_key
             ORDER BY total_cost DESC
             LIMIT 30
        """, params).fetchall()

    svc_total     = sum(svc_map.values())           # 0 when incl_service is False
    svc_by_branch: Dict[str, float] = {}
    for (br, _m), v in svc_map.items():
        svc_by_branch[br] = svc_by_branch.get(br, 0.0) + v

    revenue_ex    = float(tot["revenue_ex"]    or 0) + svc_total
    cogs          = float(tot["cogs"]          or 0)
    costed_rev_ex = float(tot["costed_rev_ex"] or 0) + svc_total   # coverage diagnostic only
    gp            = revenue_ex - cogs
    gp_pct        = (gp   / revenue_ex * 100) if revenue_ex > 0 else None
    food_pct      = (cogs / revenue_ex * 100) if revenue_ex > 0 else None

    heatmap = []
    for r in hmap:
        rev    = float(r["rev"]    or 0)
        c      = float(r["cogs"]   or 0)
        costed = float(r["costed"] or 0)
        fc     = (c / rev * 100) if rev > 0 else None
        heatmap.append({
            "branch": r["branch"], "month": r["m"],
            "revenue": round(rev, 2), "cogs": round(c, 2),
            "gp": round(rev - c, 2),
            "food_cost_pct": round(fc, 1) if fc is not None else None,
        })

    top_skus = []
    for r in top_rows:
        tc  = float(r["total_cogs"] or 0)
        pct = (tc / cogs * 100) if cogs > 0 else 0
        top_skus.append({
            "sku_id":        r["sku_id"],
            "sku_code":      r["sku_code"],
            "description":   r["description"],
            "business_type": r["business_type"],
            "qty":           round(float(r["qty"] or 0), 2),
            "unit_cost":     round(float(r["avg_unit_cost"] or 0), 4),
            "total_cogs":    round(tc, 2),
            "pct_of_chain_cogs": round(pct, 2),
            "sparkline":     sparkline_map.get(r["sku_id"], []),
        })

    def _share(n):
        if cogs <= 0 or not top_skus: return None
        s = sum(x["total_cogs"] for x in top_skus[:n])
        return round(s / cogs * 100, 2)
    concentration = {
        "top_5_pct":  _share(5),
        "top_10_pct": _share(10),
        "top_20_pct": _share(20),
        "top_50_pct": _share(50),
    }

    branches_out = []
    for r in branch_rows:
        c      = float(r["cogs"]   or 0)
        svc    = svc_by_branch.get(r["branch"], 0.0)
        costed = float(r["costed"] or 0) + svc
        full   = float(r["rev"] or 0) + svc
        fc     = (c / full * 100) if full > 0 else None
        branches_out.append({
            "branch": r["branch"],
            "revenue": round(float(r["rev"] or 0) + svc, 2),
            "cogs": round(c, 2),
            "food_cost_pct": round(fc, 1) if fc is not None else None,
        })

    ings = []
    by_type: Dict[str, float] = {}
    for r in ing_rows:
        c = float(r["total_cost"] or 0)
        t = r["type"] or "other"
        by_type[t] = by_type.get(t, 0.0) + c
        ings.append({
            "ingredient_name": r["name"] or "",
            "ingredient_type": t,
            "total_cost":      round(c, 2),
            "pct_of_chain_cogs": round(c / cogs * 100, 2) if cogs > 0 else 0,
        })

    return {
        "granularity": granularity,
        "period": {"month_from": month_from, "month_to": month_to},
        "totals": {
            "revenue":       round(revenue_ex, 2),
            "cogs":          round(cogs, 2),
            "costed_revenue": round(costed_rev_ex, 2),
            "gp":            round(gp, 2),
            "gp_pct":        round(gp_pct, 1)   if gp_pct   is not None else None,
            "food_cost_pct": round(food_pct, 1) if food_pct is not None else None,
            "menus_total":   int(tot["menus_total"] or 0),
            "service_charge": round(svc_total, 2),
            "incl_service":  bool(incl_service),
        },
        "heatmap":          heatmap,
        "top_skus":         top_skus,
        "concentration":    concentration,
        "branch_breakdown": branches_out,
        "top_ingredients":  ings,
        "cost_by_ingredient_type": {k: round(v, 2) for k, v in by_type.items()},
    }


@app.get("/api/bom-cost/cost-drivers")
@ttl_cache
def bom_cost_drivers(
    scope:      str           = Query("chain", description="chain | branch"),
    branch:     Optional[str] = Query(None),
    month_from: Optional[str] = Query(None),
    month_to:   Optional[str] = Query(None),
    limit:      int           = Query(60, ge=1, le=200),
):
    """Unified Top-N cost-driver SKUs (BoM COGS) used by ALL BoM-cost modes
    (overview / branch / compare) so they look identical. Returns per-SKU window
    COGS + %share + prev-period COGS + MoM% (MTD-aware, STANDING RULE) and a
    grand-total row. COGS via our BoM only (iron rule)."""
    brs = [branch] if (scope == "branch" and branch and branch != "ALL") else None
    where, params = _bom_cost_period_filters(month_from, month_to, brs)
    cogs_expr = _cogs_case("ssm", "sk", "sc")
    JOINS = ("FROM sales_sku_monthly ssm JOIN branches b ON b.branch_id=ssm.branch_id "
             "JOIN skus sk ON sk.sku_id=ssm.sku_id LEFT JOIN sku_cost sc ON sc.sku_id=ssm.sku_id")
    with get_db() as con:
        rows = con.execute(f"""
            SELECT ssm.sku_id, sk.sku_code, COALESCE(sk.description,'') description,
                   COALESCE(sk.business_type,'other') business_type,
                   COALESCE(SUM(ssm.qty),0) qty, COALESCE(SUM({cogs_expr}),0) total_cogs,
                   CASE WHEN SUM(ssm.qty)>0 THEN SUM({cogs_expr})/SUM(ssm.qty) ELSE 0 END unit_cost
            {JOINS} {where} GROUP BY ssm.sku_id HAVING total_cogs>0
            ORDER BY total_cogs DESC LIMIT :lim""", {**params, "lim": limit}).fetchall()
        tot = con.execute(f"SELECT COALESCE(SUM({cogs_expr}),0) c, COALESCE(SUM(ssm.qty),0) q {JOINS} {where}", params).fetchone()
        total_all = float(tot["c"] or 0); qty_all = float(tot["q"] or 0)
        ids = [r["sku_id"] for r in rows]

        # MoM per SKU: equal-period (MTD) when latest month incomplete, else month-over-month.
        mom_map: dict = {}; mom_basis = "month"; cutoff = None
        cur_tot = prev_tot = 0.0
        mtd = _mtd_window(con)
        if ids:
            phd = ",".join(f":k{i}" for i in range(len(ids)))
            kp = {f"k{i}": v for i, v in enumerate(ids)}
            bclause = ""
            if brs:
                bclause = " AND b.name=:cbr"; kp["cbr"] = branch
            if mtd:
                mom_basis = "mtd"; cutoff = mtd["cutoff_day"]
                ucx = _unified_cogs_expr("ssm")
                kp.update({"cs": mtd["cur_s"], "ce": mtd["cur_e"], "ps": mtd["prev_s"], "pe": mtd["prev_e"]})
                drows = con.execute(f"""
                    SELECT ssm.sku_id,
                      SUM(CASE WHEN ssm.day BETWEEN :cs AND :ce THEN ({ucx}) ELSE 0 END) cur,
                      SUM(CASE WHEN ssm.day BETWEEN :ps AND :pe THEN ({ucx}) ELSE 0 END) prev
                    FROM (SELECT branch_id, sku_id, qty, day, 'Easy' AS source FROM sales_sku_daily
                          WHERE sku_id IN ({phd}) AND (day BETWEEN :cs AND :ce OR day BETWEEN :ps AND :pe)) ssm
                    JOIN branches b ON b.branch_id=ssm.branch_id
                    WHERE b.name!='ALL'{bclause} GROUP BY ssm.sku_id""", kp).fetchall()
                for d in drows:
                    cur = float(d["cur"] or 0); prev = float(d["prev"] or 0)
                    cur_tot += cur; prev_tot += prev
                    mom_map[d["sku_id"]] = {"prev": round(prev, 2),
                                            "mom": round((cur - prev) / prev * 100, 1) if prev > 0 else None}
            else:
                # monthly: latest two months present in window
                mrows = con.execute(f"""SELECT DISTINCT ssm.period_month m {JOINS} {where} ORDER BY m DESC LIMIT 2""", params).fetchall()
                lm = mrows[0]["m"] if mrows else None
                pm = mrows[1]["m"] if len(mrows) > 1 else None
                if lm:
                    kp2 = {**kp, "lm": lm, "pm": pm or "__none__"}
                    mrows2 = con.execute(f"""
                        SELECT ssm.sku_id, ssm.period_month m, COALESCE(SUM({cogs_expr}),0) c
                        {JOINS} WHERE ssm.sku_id IN ({phd}) AND ssm.period_month IN (:lm,:pm){bclause}
                        GROUP BY ssm.sku_id, ssm.period_month""", kp2).fetchall()
                    tmp: dict = {}
                    for d in mrows2:
                        tmp.setdefault(d["sku_id"], {})[d["m"]] = float(d["c"] or 0)
                    for sid, mm in tmp.items():
                        cur = mm.get(lm, 0); prev = mm.get(pm) if pm else None
                        cur_tot += cur; prev_tot += (prev or 0)
                        mom_map[sid] = {"prev": round(prev, 2) if prev is not None else None,
                                        "mom": round((cur - prev) / prev * 100, 1) if (prev and prev > 0) else None}

        items = []
        for r in rows:
            mm = mom_map.get(r["sku_id"], {})
            items.append({
                "sku_code": r["sku_code"], "description": r["description"],
                "business_type": r["business_type"], "qty": round(float(r["qty"] or 0), 2),
                "unit_cost": round(float(r["unit_cost"] or 0), 2),
                "total_cogs": round(float(r["total_cogs"] or 0), 2),
                "pct_of_total": round(float(r["total_cogs"] or 0) / total_all * 100, 1) if total_all else None,
                "prev_cogs": mm.get("prev"), "mom_pct": mm.get("mom"),
            })
        shown = sum(i["total_cogs"] for i in items)
        total_mom = round((cur_tot - prev_tot) / prev_tot * 100, 1) if (mom_basis and prev_tot > 0) else None
        return {"scope": scope, "branch": branch, "mom_basis": mom_basis, "mtd_cutoff_day": cutoff,
                "items": items, "total": {
                    "total_cogs": round(total_all, 2), "qty": round(qty_all, 2),
                    "shown_cogs": round(shown, 2),
                    "shown_pct": round(shown / total_all * 100, 1) if total_all else None,
                    "prev_cogs": round(prev_tot, 2) if prev_tot else None, "mom_pct": total_mom}}


@app.get("/api/bom-cost/branch-foodcost-mom")
@ttl_cache
def bom_cost_branch_foodcost_mom(
    month: Optional[str] = Query(None, description="'Current' month (YYYY-MM); default = latest in data"),
    movers_n: int        = Query(5, description="How many top menu movers up/down per branch"),
    incl_service: bool   = Query(False, description="Count ex-VAT service charge as revenue (and in the Food-Cost% denominator)"),
):
    """Per-branch Food Cost % for the latest month vs the previous month (MoM),
    plus the top menu cost movers (increase/decrease) that drove each branch's change.
    Powers the Branch Food-Cost KPI boxes + drill-down."""
    cogs_expr       = _cogs_case('ssm', 'sk', 'sc')
    costed_rev_expr = _costed_rev_case('ssm', 'sk', 'sc')
    movers_n = max(1, min(20, int(movers_n)))

    with get_db() as con:
        months = [r["m"] for r in con.execute(
            "SELECT DISTINCT period_month AS m FROM sales_sku_monthly "
            "ORDER BY period_month DESC LIMIT 36"
        ).fetchall()]
        if not months:
            return {"month": None, "prev_month": None, "branches": []}
        cur_m  = month if (month and month in months) else months[0]
        idx    = months.index(cur_m)
        prev_m = months[idx + 1] if idx + 1 < len(months) else None

        ms = [cur_m] + ([prev_m] if prev_m else [])
        ph = ",".join(f":m{i}" for i in range(len(ms)))
        params = {f"m{i}": m for i, m in enumerate(ms)}

        agg_rows = con.execute(f"""
            SELECT b.name AS branch, ssm.period_month AS m,
                   COALESCE(SUM(ssm.subtotal_ex_vat), 0) AS rev,
                   COALESCE(SUM({cogs_expr}), 0)         AS cogs,
                   COALESCE(SUM({costed_rev_expr}), 0)   AS costed
              FROM sales_sku_monthly ssm
              JOIN branches b   ON b.branch_id = ssm.branch_id
              JOIN skus     sk  ON sk.sku_id   = ssm.sku_id
              LEFT JOIN sku_cost sc ON sc.sku_id = ssm.sku_id
             WHERE b.name != 'ALL' AND COALESCE(b.active,1) = 1
               AND ssm.period_month IN ({ph})
             GROUP BY b.name, ssm.period_month
        """, params).fetchall()

        mover_rows = con.execute(f"""
            SELECT b.name AS branch, ssm.sku_id AS sku_id,
                   sk.sku_code AS sku_code,
                   COALESCE(sk.description,'') AS description,
                   ssm.period_month AS m,
                   COALESCE(SUM({cogs_expr}), 0) AS cogs,
                   COALESCE(SUM(ssm.qty), 0)     AS qty
              FROM sales_sku_monthly ssm
              JOIN branches b   ON b.branch_id = ssm.branch_id
              JOIN skus     sk  ON sk.sku_id   = ssm.sku_id
              LEFT JOIN sku_cost sc ON sc.sku_id = ssm.sku_id
             WHERE b.name != 'ALL' AND COALESCE(b.active,1) = 1
               AND ssm.period_month IN ({ph})
             GROUP BY b.name, ssm.sku_id, ssm.period_month
        """, params).fetchall()

        svc_map = _service_charge_map(con, prev_m or cur_m, cur_m) if incl_service else {}

    # ── assemble per-branch aggregates (service charge folded into rev + costed) ──
    agg: Dict[str, dict] = {}
    for r in agg_rows:
        svc = svc_map.get((r["branch"], r["m"]), 0.0)
        d = agg.setdefault(r["branch"], {})
        d[r["m"]] = {
            "rev":    float(r["rev"]    or 0) + svc,
            "cogs":   float(r["cogs"]   or 0),
            "costed": float(r["costed"] or 0) + svc,   # service = revenue with no COGS
        }

    # ── per-branch menu movers (cur_cogs - prev_cogs) ───────────────────
    mv: Dict[str, Dict[int, dict]] = {}
    for r in mover_rows:
        bm = mv.setdefault(r["branch"], {})
        s  = bm.setdefault(r["sku_id"], {
            "sku_code": r["sku_code"], "description": r["description"],
            "cur": 0.0, "prev": 0.0, "cur_qty": 0.0, "prev_qty": 0.0,
        })
        if r["m"] == cur_m:
            s["cur"]      = float(r["cogs"] or 0)
            s["cur_qty"]  = float(r["qty"] or 0)
        elif prev_m and r["m"] == prev_m:
            s["prev"]     = float(r["cogs"] or 0)
            s["prev_qty"] = float(r["qty"] or 0)

    def _fc(v):
        return (v["cogs"] / v["rev"] * 100) if v.get("rev", 0) > 0 else None

    out = []
    for branch, d in agg.items():
        cur  = d.get(cur_m, {"rev": 0, "cogs": 0, "costed": 0})
        prev = d.get(prev_m, {"rev": 0, "cogs": 0, "costed": 0}) if prev_m else None
        fc      = _fc(cur)
        prev_fc = _fc(prev) if prev else None
        delta_pp = round(fc - prev_fc, 1) if (fc is not None and prev_fc is not None) else None

        # menu movers for this branch
        movers = []
        for sid, s in mv.get(branch, {}).items():
            delta = s["cur"] - s["prev"]
            if abs(delta) < 1:    # ignore noise under ฿1
                continue
            movers.append({
                "sku_code":    s["sku_code"],
                "description": s["description"],
                "cogs":        round(s["cur"], 2),
                "prev_cogs":   round(s["prev"], 2),
                "delta":       round(delta, 2),
                "qty":         round(s["cur_qty"], 2),
                "prev_qty":    round(s["prev_qty"], 2),
                "is_new":      s["prev"] <= 0 and s["cur"] > 0,
                "is_gone":     s["cur"] <= 0 and s["prev"] > 0,
            })
        ups   = sorted([m for m in movers if m["delta"] > 0], key=lambda x: -x["delta"])[:movers_n]
        downs = sorted([m for m in movers if m["delta"] < 0], key=lambda x: x["delta"])[:movers_n]

        out.append({
            "branch":             branch,
            "revenue":            round(cur["rev"], 2),
            "costed_revenue":     round(cur["costed"], 2),
            "prev_costed_revenue": round(prev["costed"], 2) if prev else None,
            "cogs":               round(cur["cogs"], 2),
            "food_cost_pct":      round(fc, 1) if fc is not None else None,
            "prev_revenue":       round(prev["rev"], 2)  if prev else None,
            "prev_cogs":          round(prev["cogs"], 2) if prev else None,
            "prev_food_cost_pct": round(prev_fc, 1) if prev_fc is not None else None,
            "delta_pp":           delta_pp,
            "cogs_delta":         round(cur["cogs"] - (prev["cogs"] if prev else 0), 2),
            "movers_up":          ups,
            "movers_down":        downs,
        })

    # worst (highest food cost %) first; None food-cost sinks to the bottom
    out.sort(key=lambda x: (x["food_cost_pct"] is None, -(x["food_cost_pct"] or 0)))
    return {"month": cur_m, "prev_month": prev_m, "incl_service": bool(incl_service), "branches": out}


@app.get("/api/bom-cost/compare")
@ttl_cache
def bom_cost_compare(
    branches:   str           = Query(..., description="Comma-separated branch names (2-5)"),
    month_from: Optional[str] = Query(None),
    month_to:   Optional[str] = Query(None),
):
    """Per-branch comparison across 2-5 selected branches."""
    branch_list = [b.strip() for b in branches.split(",") if b.strip()]
    if not (2 <= len(branch_list) <= 5):
        raise HTTPException(400, "branches must contain 2 to 5 names")

    where, params = _bom_cost_period_filters(month_from, month_to, branch_list)
    cogs_expr       = _cogs_case('ssm', 'sk', 'sc')
    costed_rev_expr = _costed_rev_case('ssm', 'sk', 'sc')

    with get_db() as con:
        br_tot = con.execute(f"""
            SELECT b.name AS branch,
                   COALESCE(SUM(ssm.subtotal_ex_vat), 0)       AS rev,
                   COALESCE(SUM({cogs_expr}), 0)               AS cogs,
                   COALESCE(SUM({costed_rev_expr}), 0)         AS costed,
                   COUNT(DISTINCT CASE WHEN sc.unit_cost IS NULL THEN ssm.sku_id END) AS uncosted_skus
              FROM sales_sku_monthly ssm
              JOIN branches b   ON b.branch_id = ssm.branch_id
              JOIN skus     sk  ON sk.sku_id   = ssm.sku_id
              LEFT JOIN sku_cost sc ON sc.sku_id = ssm.sku_id
            {where}
            GROUP BY b.name
        """, params).fetchall()

        top_menus = con.execute(f"""
            SELECT branch, sku_code, description, total_cogs FROM (
              SELECT b.name AS branch, sk.sku_code,
                     COALESCE(sk.description,'') AS description,
                     COALESCE(SUM({cogs_expr}), 0) AS total_cogs,
                     ROW_NUMBER() OVER (PARTITION BY b.name ORDER BY SUM({cogs_expr}) DESC) AS rn
                FROM sales_sku_monthly ssm
                JOIN branches b   ON b.branch_id = ssm.branch_id
                JOIN skus     sk  ON sk.sku_id   = ssm.sku_id
                LEFT JOIN sku_cost sc ON sc.sku_id = ssm.sku_id
              {where}
              GROUP BY b.name, ssm.sku_id
            ) WHERE rn <= 3 ORDER BY branch, total_cogs DESC
        """, params).fetchall()

        ing_type = con.execute(f"""
            WITH eff AS (
              SELECT b.name AS branch, ssm.qty AS qty,
                     COALESCE(
                       (SELECT bi.sku_id FROM bom_ingredients bi WHERE bi.sku_id = ssm.sku_id LIMIT 1),
                       (SELECT a.resolved_sku_id
                          FROM bom_sku_alias a
                          JOIN skus s ON s.sku_id = ssm.sku_id
                         WHERE a.bom_sku_code = s.sku_code LIMIT 1)
                     ) AS bom_sku_id
                FROM sales_sku_monthly ssm
                JOIN branches b   ON b.branch_id = ssm.branch_id
                JOIN skus     sk  ON sk.sku_id   = ssm.sku_id
                LEFT JOIN sku_cost sc ON sc.sku_id = ssm.sku_id
              {where}
            )
            SELECT eff.branch, COALESCE(bi.ingredient_type,'other') AS type,
                   SUM(eff.qty * bi.ingredient_cost) AS cost
              FROM eff
              JOIN bom_ingredients bi ON bi.sku_id = eff.bom_sku_id
             WHERE eff.bom_sku_id IS NOT NULL
             GROUP BY eff.branch, type
        """, params).fetchall()

    out: Dict[str, dict] = {br: {
        "branch": br, "revenue": 0, "cogs": 0, "costed_revenue": 0,
        "gp": 0, "gp_pct": None, "food_cost_pct": None,
        "uncosted_skus": 0, "top_menus": [], "cost_by_ingredient_type": {},
    } for br in branch_list}

    for r in br_tot:
        if r["branch"] not in out: continue
        rev    = float(r["rev"]    or 0)
        c      = float(r["cogs"]   or 0)
        costed = float(r["costed"] or 0)
        gp     = rev - c
        out[r["branch"]].update({
            "revenue":        round(rev, 2),
            "cogs":           round(c, 2),
            "costed_revenue": round(costed, 2),
            "gp":             round(gp, 2),
            "gp_pct":         round(gp / rev * 100, 1) if rev > 0 else None,
            "food_cost_pct":  round(c / rev * 100, 1) if rev > 0 else None,
            "uncosted_skus":  int(r["uncosted_skus"] or 0),
        })

    for r in top_menus:
        br = r["branch"]
        if br in out:
            cogs_br = out[br]["cogs"]
            tc = float(r["total_cogs"] or 0)
            out[br]["top_menus"].append({
                "sku_code":    r["sku_code"],
                "description": r["description"],
                "total_cogs":  round(tc, 2),
                "pct_of_branch_cogs": round(tc / cogs_br * 100, 2) if cogs_br > 0 else 0,
            })

    for r in ing_type:
        br = r["branch"]
        if br in out:
            out[br]["cost_by_ingredient_type"][r["type"]] = round(float(r["cost"] or 0), 2)

    def _mean_of(metric):
        vals = [out[b][metric] for b in branch_list if out[b].get(metric) is not None]
        return (sum(vals) / len(vals)) if vals else None

    variance: dict = {}
    for metric in ("food_cost_pct", "gp_pct", "cogs", "revenue"):
        mean = _mean_of(metric)
        if mean is None or mean == 0:
            variance[metric] = {"mean": mean, "outliers": []}
            continue
        outliers = []
        for br in branch_list:
            v = out[br].get(metric)
            if v is None: continue
            dev = (v - mean) / abs(mean) * 100
            if abs(dev) >= 10:
                outliers.append({"branch": br, "value": v, "deviation_pct": round(dev, 1)})
        variance[metric] = {"mean": round(mean, 2), "outliers": outliers}

    return {
        "branches": branch_list,
        "period":   {"month_from": month_from, "month_to": month_to},
        "results":  [out[b] for b in branch_list],
        "variance": variance,
    }


@app.get("/api/bom-cost/simulator-base")
@ttl_cache
def bom_cost_simulator_base(
    month_from: Optional[str] = Query(None),
    month_to:   Optional[str] = Query(None),
    limit:      int           = Query(200, ge=10, le=2000),
):
    """Return baseline chain stats + cost-driver SKUs so client can preview scenarios."""
    where, params = _bom_cost_period_filters(month_from, month_to)
    cogs_expr       = _cogs_case('ssm', 'sk', 'sc')
    costed_rev_expr = _costed_rev_case('ssm', 'sk', 'sc')

    with get_db() as con:
        tot = con.execute(f"""
            SELECT COALESCE(SUM(ssm.subtotal_ex_vat), 0)   AS revenue_ex,
                   COALESCE(SUM({cogs_expr}), 0)           AS cogs,
                   COALESCE(SUM({costed_rev_expr}), 0)     AS costed_rev
              FROM sales_sku_monthly ssm
              JOIN branches b   ON b.branch_id = ssm.branch_id
              JOIN skus     sk  ON sk.sku_id   = ssm.sku_id
              LEFT JOIN sku_cost sc ON sc.sku_id = ssm.sku_id
            {where}
        """, params).fetchone()

        sku_rows = con.execute(f"""
            SELECT ssm.sku_id, sk.sku_code,
                   COALESCE(sk.description,'') AS description,
                   COALESCE(sk.business_type,'other') AS business_type,
                   COALESCE(SUM(ssm.qty), 0)            AS qty,
                   COALESCE(SUM(ssm.subtotal_ex_vat), 0) AS period_revenue,
                   COALESCE(SUM({cogs_expr}), 0)        AS period_cogs,
                   CASE WHEN SUM(ssm.qty) > 0 THEN SUM({cogs_expr})/SUM(ssm.qty) ELSE 0 END
                     AS current_unit_cost
              FROM sales_sku_monthly ssm
              JOIN branches b   ON b.branch_id = ssm.branch_id
              JOIN skus     sk  ON sk.sku_id   = ssm.sku_id
              LEFT JOIN sku_cost sc ON sc.sku_id = ssm.sku_id
            {where}
            GROUP BY ssm.sku_id
            HAVING period_cogs > 0
            ORDER BY period_cogs DESC
            LIMIT :lim
        """, {**params, "lim": int(limit)}).fetchall()

    revenue   = float(tot["revenue_ex"] or 0)
    cogs      = float(tot["cogs"]       or 0)
    costed    = float(tot["costed_rev"] or 0)
    return {
        "period": {"month_from": month_from, "month_to": month_to},
        "baseline": {
            "revenue":        round(revenue, 2),
            "cogs":           round(cogs, 2),
            "costed_revenue": round(costed, 2),
            "gp":             round(revenue - cogs, 2),
            "gp_pct":         round((revenue - cogs) / revenue * 100, 1) if revenue > 0 else None,
            "food_cost_pct":  round(cogs / revenue * 100, 1) if revenue > 0 else None,
        },
        "skus": [{
            "sku_id":            r["sku_id"],
            "sku_code":          r["sku_code"],
            "description":       r["description"],
            "business_type":     r["business_type"],
            "qty":               round(float(r["qty"] or 0), 2),
            "period_revenue":    round(float(r["period_revenue"] or 0), 2),
            "period_cogs":       round(float(r["period_cogs"] or 0), 2),
            "current_unit_cost": round(float(r["current_unit_cost"] or 0), 4),
        } for r in sku_rows],
    }


class ScenarioOverride(BaseModel):
    sku_code: str
    unit_cost: float
    qty: Optional[float] = None        # scenario serving count (None = unchanged)
    disabled: Optional[bool] = False   # menu closed / discontinued


def _ensure_scenario_cols(con):
    cols = {r["name"] for r in con.execute("PRAGMA table_info(cost_scenario_item)")}
    if "new_qty" not in cols:
        con.execute("ALTER TABLE cost_scenario_item ADD COLUMN new_qty REAL")
    if "disabled" not in cols:
        con.execute("ALTER TABLE cost_scenario_item ADD COLUMN disabled INTEGER DEFAULT 0")
    # Phase B (ROI ledger): per-item effective unit cost snapshotted at adoption.
    if "baseline_cost" not in cols:
        con.execute("ALTER TABLE cost_scenario_item ADD COLUMN baseline_cost REAL")
    hcols = {r["name"] for r in con.execute("PRAGMA table_info(cost_scenario)")}
    if "adopted_at" not in hcols:
        con.execute("ALTER TABLE cost_scenario ADD COLUMN adopted_at TEXT")
    if "adopted_by" not in hcols:
        con.execute("ALTER TABLE cost_scenario ADD COLUMN adopted_by TEXT")
    if "baseline_month" not in hcols:
        con.execute("ALTER TABLE cost_scenario ADD COLUMN baseline_month TEXT")


class ScenarioCreate(BaseModel):
    name: str
    description: Optional[str] = None
    overrides: List[ScenarioOverride]


class SimulateBody(BaseModel):
    overrides: List[ScenarioOverride]
    month_from: Optional[str] = None
    month_to: Optional[str] = None


@app.post("/api/bom-cost/scenarios", dependencies=[Depends(require_write_token)])
def create_scenario(body: ScenarioCreate):
    nm = (body.name or "").strip()
    if not nm:
        raise HTTPException(400, "name is required")
    with get_db() as con:
        _ensure_scenario_cols(con)
        cur = con.execute(
            "INSERT INTO cost_scenario(name, description) VALUES(?, ?)",
            (nm, body.description),
        )
        scen_id = cur.lastrowid
        if body.overrides:
            codes = list({o.sku_code for o in body.overrides})
            ph = ",".join("?" * len(codes))
            sku_map = {r["sku_code"]: r["sku_id"] for r in con.execute(
                f"SELECT sku_id, sku_code FROM skus WHERE sku_code IN ({ph})", codes
            ).fetchall()}
            inserted = 0
            for o in body.overrides:
                sid = sku_map.get(o.sku_code)
                if sid is None: continue
                con.execute(
                    "INSERT OR REPLACE INTO cost_scenario_item(scenario_id, sku_id, unit_cost, new_qty, disabled) "
                    "VALUES(?,?,?,?,?)", (scen_id, sid, float(o.unit_cost),
                                          (float(o.qty) if o.qty is not None else None),
                                          1 if o.disabled else 0)
                )
                inserted += 1
        else:
            inserted = 0
        con.commit()
    invalidate_cache("list_scenarios", "get_scenario")
    return {"id": scen_id, "name": nm, "items_inserted": inserted}


@app.get("/api/bom-cost/scenarios")
def list_scenarios():
    with get_db() as con:
        rows = con.execute("""
            SELECT cs.id, cs.name, cs.description, cs.created_at, cs.created_by,
                   COALESCE(cs.is_archived,0) AS is_archived,
                   (SELECT COUNT(*) FROM cost_scenario_item csi WHERE csi.scenario_id = cs.id) AS n_items
              FROM cost_scenario cs
             WHERE COALESCE(cs.is_archived,0) = 0
             ORDER BY cs.id DESC
        """).fetchall()
    return {"scenarios": [dict(r) for r in rows]}


@app.get("/api/bom-cost/scenarios/{scen_id}")
def get_scenario(scen_id: int):
    with get_db() as con:
        _ensure_scenario_cols(con)
        head = con.execute("SELECT * FROM cost_scenario WHERE id=?", (scen_id,)).fetchone()
        if head is None:
            raise HTTPException(404, "scenario not found")
        items = con.execute("""
            SELECT csi.sku_id, sk.sku_code,
                   COALESCE(sk.description,'') AS description,
                   COALESCE(sk.business_type,'other') AS business_type,
                   csi.unit_cost, csi.new_qty, COALESCE(csi.disabled,0) AS disabled
              FROM cost_scenario_item csi
              JOIN skus sk ON sk.sku_id = csi.sku_id
             WHERE csi.scenario_id=?
             ORDER BY sk.sku_code
        """, (scen_id,)).fetchall()
    return {"scenario": dict(head), "items": [dict(r) for r in items]}


@app.delete("/api/bom-cost/scenarios/{scen_id}", dependencies=[Depends(require_write_token)])
def delete_scenario(scen_id: int):
    with get_db() as con:
        cur = con.execute("UPDATE cost_scenario SET is_archived=1 WHERE id=?", (scen_id,))
        con.commit()
        if cur.rowcount == 0:
            raise HTTPException(404, "scenario not found")
    invalidate_cache("list_scenarios", "get_scenario")
    return {"id": scen_id, "archived": True}


@app.post("/api/bom-cost/simulate")
def simulate_scenario(body: SimulateBody):
    """Recompute chain + per-branch GP using the given unit_cost overrides."""
    where, params = _bom_cost_period_filters(body.month_from, body.month_to)
    cogs_expr       = _cogs_case('ssm', 'sk', 'sc')
    costed_rev_expr = _costed_rev_case('ssm', 'sk', 'sc')

    overrides     = {o.sku_code: float(o.unit_cost) for o in (body.overrides or [])}
    override_qty  = {o.sku_code: float(o.qty) for o in (body.overrides or []) if o.qty is not None}  # fix H3
    disabled_skus = {o.sku_code for o in (body.overrides or []) if o.disabled}                        # fix H3

    with get_db() as con:
        rows = con.execute(f"""
            SELECT b.name AS branch, ssm.sku_id, sk.sku_code,
                   COALESCE(SUM(ssm.qty), 0)            AS qty,
                   COALESCE(SUM({cogs_expr}), 0)        AS base_cogs,
                   COALESCE(SUM({costed_rev_expr}), 0)  AS base_costed,
                   COALESCE(SUM(ssm.subtotal_ex_vat),0) AS revenue
              FROM sales_sku_monthly ssm
              JOIN branches b   ON b.branch_id = ssm.branch_id
              JOIN skus     sk  ON sk.sku_id   = ssm.sku_id
              LEFT JOIN sku_cost sc ON sc.sku_id = ssm.sku_id
            {where}
            GROUP BY b.name, ssm.sku_id
        """, params).fetchall()

    chain = {"revenue": 0.0, "base_cogs": 0.0, "base_costed": 0.0,
             "scen_cogs": 0.0, "scen_costed": 0.0}
    per_branch: Dict[str, dict] = {}
    seen_codes = set()

    for r in rows:
        br = r["branch"]
        bb = per_branch.setdefault(br, {
            "revenue": 0.0,
            "base_cogs": 0.0, "base_costed": 0.0,
            "scen_cogs": 0.0, "scen_costed": 0.0,
        })
        rev       = float(r["revenue"]     or 0)
        base_c    = float(r["base_cogs"]   or 0)
        base_cst  = float(r["base_costed"] or 0)
        qty       = float(r["qty"]         or 0)
        code      = r["sku_code"]
        seen_codes.add(code)
        new_c   = base_c
        new_cst = base_cst
        if code in disabled_skus:         # fix H3: disabled items → 0 COGS + 0 costed rev
            new_c   = 0.0
            new_cst = 0.0
        elif code in overrides and base_c > 0:
            scen_qty = override_qty.get(code, qty)   # fix H3: use scenario qty if set
            new_c    = scen_qty * overrides[code]
        chain["revenue"]     += rev
        chain["base_cogs"]   += base_c
        chain["base_costed"] += base_cst
        chain["scen_cogs"]   += new_c
        chain["scen_costed"] += new_cst
        bb["revenue"]     += rev
        bb["base_cogs"]   += base_c
        bb["base_costed"] += base_cst
        bb["scen_cogs"]   += new_c
        bb["scen_costed"] += new_cst

    def _pack(d):
        bc, bcs = d["base_cogs"], d["base_costed"]
        sc_, scs = d["scen_cogs"], d["scen_costed"]
        return {
            "revenue":      round(d["revenue"], 2),
            "baseline": {
                "cogs":           round(bc, 2),
                "gp":             round(bcs - bc, 2),
                "gp_pct":         round((bcs - bc) / bcs * 100, 1) if bcs > 0 else None,
                "food_cost_pct":  round(bc / bcs * 100, 1) if bcs > 0 else None,
            },
            "scenario": {
                "cogs":           round(sc_, 2),
                "gp":             round(scs - sc_, 2),
                "gp_pct":         round((scs - sc_) / scs * 100, 1) if scs > 0 else None,
                "food_cost_pct":  round(sc_ / scs * 100, 1) if scs > 0 else None,
            },
            "delta": {
                "cogs":           round(sc_ - bc, 2),
                "gp":             round((scs - sc_) - (bcs - bc), 2),
            },
        }

    chain_pack = _pack(chain); chain_pack["branch"] = "ALL"
    branches_out = []
    for k in sorted(per_branch.keys()):
        p = _pack(per_branch[k]); p["branch"] = k
        branches_out.append(p)
    return {
        "period": {"month_from": body.month_from, "month_to": body.month_to},
        "overrides_applied": len([c for c in overrides if c in seen_codes]),
        "chain": chain_pack,
        "per_branch": branches_out,
    }


# ── Phase B — Savings Ledger (provable ROI) ──────────────────────────────────
# Turn a what-if cost_scenario into a tracked outcome. On ADOPT we snapshot each
# item's effective unit cost (baseline). Thereafter "realized" saving = how much
# cheaper the SKU is NOW vs that baseline, × the qty sold since adoption — so it
# only accrues once the cheaper recipe is actually implemented. "potential" =
# baseline → scenario target (the goal). progress = realized / potential.

class AdoptBody(BaseModel):
    adopted_by: Optional[str] = None


@app.post("/api/bom-cost/scenarios/{scen_id}/adopt", dependencies=[Depends(require_write_token)])
def adopt_scenario(scen_id: int, body: Optional[AdoptBody] = None):
    """Mark a scenario adopted: snapshot per-item baseline effective cost + stamp
    adopted_at and the baseline (latest data) month."""
    with get_db() as con:
        _ensure_scenario_cols(con)
        head = con.execute("SELECT id FROM cost_scenario WHERE id=?", (scen_id,)).fetchone()
        if head is None:
            raise HTTPException(404, "scenario not found")
        _build_sku_effective_cost(con, force=False)   # ensure current costs exist
        bm = con.execute("SELECT MAX(period_month) m FROM sales_sku_monthly").fetchone()["m"]
        now = datetime.now().isoformat(timespec="seconds")
        items = con.execute("SELECT sku_id FROM cost_scenario_item WHERE scenario_id=?", (scen_id,)).fetchall()
        for it in items:
            base = con.execute("SELECT recipe_cost FROM sku_effective_cost WHERE sku_id=?",
                               (it["sku_id"],)).fetchone()
            con.execute("UPDATE cost_scenario_item SET baseline_cost=? WHERE scenario_id=? AND sku_id=?",
                        (base["recipe_cost"] if base else None, scen_id, it["sku_id"]))
        con.execute("UPDATE cost_scenario SET adopted_at=?, adopted_by=?, baseline_month=? WHERE id=?",
                    (now, (body.adopted_by if body else None), bm, scen_id))
        con.commit()
    invalidate_cache("list_scenarios", "get_scenario")
    return {"id": scen_id, "adopted_at": now, "baseline_month": bm, "items": len(items)}


@app.post("/api/bom-cost/scenarios/{scen_id}/unadopt", dependencies=[Depends(require_write_token)])
def unadopt_scenario(scen_id: int):
    """Revert adoption (clears the ledger baseline)."""
    with get_db() as con:
        _ensure_scenario_cols(con)
        if con.execute("SELECT id FROM cost_scenario WHERE id=?", (scen_id,)).fetchone() is None:
            raise HTTPException(404, "scenario not found")
        con.execute("UPDATE cost_scenario SET adopted_at=NULL, adopted_by=NULL, baseline_month=NULL WHERE id=?", (scen_id,))
        con.execute("UPDATE cost_scenario_item SET baseline_cost=NULL WHERE scenario_id=?", (scen_id,))
        con.commit()
    invalidate_cache("list_scenarios", "get_scenario")
    return {"id": scen_id, "adopted": False}


@app.get("/api/bom-cost/scenarios/{scen_id}/savings")
def scenario_savings(scen_id: int):
    """Realized-vs-potential cost savings for an adopted scenario, with a
    cumulative monthly series. Honest: realized stays 0 until the cheaper cost is
    actually in the data (current effective cost < adoption baseline)."""
    with get_db() as con:
        _ensure_scenario_cols(con)
        head = con.execute("SELECT * FROM cost_scenario WHERE id=?", (scen_id,)).fetchone()
        if head is None:
            raise HTTPException(404, "scenario not found")
        head = dict(head)
        if not head.get("adopted_at"):
            return {"scenario": {"id": scen_id, "name": head["name"]}, "adopted": False}
        _build_sku_effective_cost(con, force=False)
        bm = head.get("baseline_month")
        rows = con.execute("""
            SELECT csi.sku_id, sk.sku_code, COALESCE(sk.description,'') AS description,
                   csi.unit_cost AS target_cost, csi.baseline_cost,
                   (SELECT recipe_cost FROM sku_effective_cost se WHERE se.sku_id=csi.sku_id) AS current_cost
              FROM cost_scenario_item csi JOIN skus sk ON sk.sku_id=csi.sku_id
             WHERE csi.scenario_id=? AND COALESCE(csi.disabled,0)=0
        """, (scen_id,)).fetchall()
        items = [dict(r) for r in rows]
        sku_ids = [it["sku_id"] for it in items]
        qty_by, months = {}, set()
        if sku_ids and bm:
            ph = ",".join("?" * len(sku_ids))
            for r in con.execute(
                f"""SELECT sku_id, period_month, SUM(qty) q FROM sales_sku_monthly
                     WHERE sku_id IN ({ph}) AND period_month >= ?
                     GROUP BY sku_id, period_month""", (*sku_ids, bm)).fetchall():
                qty_by[(r["sku_id"], r["period_month"])] = r["q"] or 0
                months.add(r["period_month"])
    months = sorted(months)
    per_item, monthly = [], {m: 0.0 for m in months}
    realized_total = potential_total = 0.0
    for it in items:
        base = it["baseline_cost"]
        if base is None:
            continue
        cur = it["current_cost"] if it["current_cost"] is not None else base
        tgt = it["target_cost"] if it["target_cost"] is not None else cur
        unit_real = base - cur          # achieved so far (now vs adoption baseline)
        unit_pot  = base - tgt          # full goal (baseline vs scenario target)
        qty_total = sum(qty_by.get((it["sku_id"], m), 0) for m in months)
        r_save, p_save = unit_real * qty_total, unit_pot * qty_total
        realized_total += r_save
        potential_total += p_save
        for m in months:
            monthly[m] += unit_real * qty_by.get((it["sku_id"], m), 0)
        per_item.append({
            "sku_code": it["sku_code"], "description": it["description"],
            "baseline_cost": round(base, 4), "current_cost": round(cur, 4),
            "target_cost": round(tgt, 4), "qty_since_adopt": round(qty_total, 0),
            "realized_saving": round(r_save, 2), "potential_saving": round(p_save, 2),
            "unit_progress_pct": round(unit_real / unit_pot * 100, 1) if unit_pot > 0 else None,
        })
    cum, series = 0.0, []
    for m in months:
        cum += monthly[m]
        series.append({"month": m, "realized": round(monthly[m], 2), "cumulative": round(cum, 2)})
    per_item.sort(key=lambda r: -r["realized_saving"])
    return {
        "scenario": {"id": scen_id, "name": head["name"], "adopted_at": head.get("adopted_at"),
                     "adopted_by": head.get("adopted_by"), "baseline_month": bm},
        "adopted": True,
        "totals": {
            "realized_saving": round(realized_total, 2),
            "potential_saving": round(potential_total, 2),
            "progress_pct": round(realized_total / potential_total * 100, 1) if potential_total > 0 else None,
            "months_tracked": len(months),
        },
        "items": per_item,
        "series": series,
    }


@app.get("/api/bom-cost/recipe/{sku_code}")
def bom_cost_recipe(sku_code: str):
    """Return the BoM recipe for a given sales SKU (direct or aliased)."""
    with get_db() as con:
        sku = con.execute(
            "SELECT sku_id, sku_code, COALESCE(description,'') AS description "
            "FROM skus WHERE sku_code=?", (sku_code,)
        ).fetchone()
        if sku is None:
            raise HTTPException(404, "sku not found")
        bom_sku_id = con.execute(
            "SELECT sku_id FROM bom_ingredients WHERE sku_id=? LIMIT 1", (sku["sku_id"],)
        ).fetchone()
        if bom_sku_id is None:
            bom_sku_id = con.execute(
                "SELECT resolved_sku_id AS sku_id FROM bom_sku_alias WHERE bom_sku_code=? LIMIT 1",
                (sku_code,)
            ).fetchone()
        if bom_sku_id is None:
            return {"sku_code": sku_code, "has_recipe": False, "ingredients": []}
        rows = con.execute("""
            SELECT seq, ingredient_code, ingredient_name, qty_per_serving,
                   unit, price_per_unit, ingredient_cost, ingredient_type
              FROM bom_ingredients WHERE sku_id=? ORDER BY seq
        """, (bom_sku_id["sku_id"],)).fetchall()
    total = sum(float(r["ingredient_cost"] or 0) for r in rows)
    return {
        "sku_code":    sku_code,
        "description": sku["description"],
        "has_recipe":  True,
        "total_cost":  round(total, 4),
        "ingredients": [dict(r) for r in rows],
    }
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


def _ols_regression(xs, ys):
    """Simple OLS linear regression. Returns (slope, intercept, r_squared)."""
    n = len(xs)
    if n < 2:
        return 0.0, ys[0] if ys else 0.0, 0.0
    sum_x  = sum(xs)
    sum_y  = sum(ys)
    sum_xy = sum(x * y for x, y in zip(xs, ys))
    sum_x2 = sum(x * x for x in xs)
    denom  = n * sum_x2 - sum_x ** 2
    if denom == 0:
        return 0.0, sum_y / n, 0.0
    slope     = (n * sum_xy - sum_x * sum_y) / denom
    intercept = (sum_y - slope * sum_x) / n
    y_mean    = sum_y / n
    ss_tot    = sum((y - y_mean) ** 2 for y in ys)
    ss_res    = sum((y - (slope * x + intercept)) ** 2 for x, y in zip(xs, ys))
    r_squared = 1 - ss_res / ss_tot if ss_tot else 1.0
    return slope, intercept, max(0.0, min(1.0, r_squared))
def _ym_parts(s: str) -> tuple:
    """Validate a YYYY-MM string and return (year, month) ints.

    Raises HTTPException(400) on malformed input so a bad query param yields a
    clean 400 instead of an unhandled ValueError -> 500.
    """
    import re as _re
    s = s.strip() if isinstance(s, str) else s
    if not isinstance(s, str) or not _re.fullmatch(r"\d{4}-\d{2}", s):
        raise HTTPException(status_code=400, detail=f"Invalid month '{s}'; expected YYYY-MM")
    y, m = int(s[:4]), int(s[5:7])
    if not (1 <= m <= 12):
        raise HTTPException(status_code=400, detail=f"Invalid month '{s}'; month must be 01-12")
    return y, m












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
