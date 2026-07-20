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
from fastapi.responses import FileResponse, JSONResponse
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
    docs_req = path == "/docs" or path.startswith("/docs/")
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


@app.get("/api/sales/monthly")
@ttl_cache
def sales_monthly(
    branch:      Optional[str] = Query(None),
    group:       Optional[str] = Query(None),
    source:      Optional[str] = Query(None),
    month:       Optional[str] = Query(None),
    month_from:  Optional[str] = Query(None),
    month_to:    Optional[str] = Query(None),
    date_from:   Optional[str] = Query(None),   # YYYY-MM-DD  for daily/weekly
    date_to:     Optional[str] = Query(None),   # YYYY-MM-DD  for daily/weekly
    granularity: str           = Query("monthly"),  # daily|weekly|monthly|quarterly
):
    """
    Revenue trend at the requested granularity.
    daily/weekly  â†' sales_transaction  (date_from / date_to)
    monthly       â†' sales_sku_monthly  (month_from / month_to)
    quarterly     â†' sales_sku_monthly aggregated to quarters
    """
    # â"€â"€ daily â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€
    if granularity == "daily":
        filters = ["b.name != 'ALL'", "st.source = 'Easy'"]
        params: dict = {}
        if branch:
            filters.append("b.name = :branch")
            params["branch"] = branch
        if date_from:
            filters.append("DATE(st.sold_at) >= :date_from")
            params["date_from"] = date_from
        if date_to:
            filters.append("DATE(st.sold_at) <= :date_to")
            params["date_to"] = date_to
        where = "WHERE " + " AND ".join(filters)
        with get_db() as con:
            rows = con.execute(
                f"""
                SELECT DATE(st.sold_at)              AS period_month,
                       ROUND(SUM(st.total_inc_vat) / 1.07,2) AS revenue,
                       COUNT(*)                       AS tx_count,
                       SUM(st.customer_count)         AS customer_count
                FROM sales_transaction st
                JOIN branches b ON b.branch_id = st.branch_id
                {where}
                GROUP BY DATE(st.sold_at)
                ORDER BY period_month
                """,
                params,
            ).fetchall()
        return [{"period_month": r["period_month"], "revenue": r["revenue"],
                 "tx_count": r["tx_count"], "customer_count": r["customer_count"],
                 "source": "Easy"} for r in rows]

    # â"€â"€ weekly â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€
    if granularity == "weekly":
        filters = ["b.name != 'ALL'", "st.source = 'Easy'"]
        params = {}
        if branch:
            filters.append("b.name = :branch")
            params["branch"] = branch
        if date_from:
            filters.append("DATE(st.sold_at) >= :date_from")
            params["date_from"] = date_from
        if date_to:
            filters.append("DATE(st.sold_at) <= :date_to")
            params["date_to"] = date_to
        where = "WHERE " + " AND ".join(filters)
        _wk = _iso_week_sql('st.sold_at')
        with get_db() as con:
            rows = con.execute(
                f"""
                SELECT {_wk}                            AS period_month,
                       MIN(DATE(st.sold_at))            AS week_start,
                       ROUND(SUM(st.total_inc_vat) / 1.07, 2)  AS revenue,
                       COUNT(*)                         AS tx_count,
                       SUM(st.customer_count)           AS customer_count
                FROM sales_transaction st
                JOIN branches b ON b.branch_id = st.branch_id
                {where}
                GROUP BY {_wk}
                ORDER BY period_month
                """,
                params,
            ).fetchall()
        return [{"period_month": r["period_month"], "week_start": r["week_start"],
                 "revenue": r["revenue"], "tx_count": r["tx_count"],
                 "customer_count": r["customer_count"], "source": "Easy"} for r in rows]

    # â"€â"€ quarterly â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€
    if granularity == "quarterly":
        filters = ["b.name != 'ALL'"]
        params = {}
        if branch:
            filters.append("b.name = :branch")
            params["branch"] = branch
        _month_range_filter(filters, params, month_from, month_to, month, col="sm.period_month")
        where = "WHERE " + " AND ".join(filters)
        with get_db() as con:
            rows = con.execute(
                f"""
                SELECT qtr AS period_month,
                       ROUND(SUM(rev_ex_vat), 2)   AS revenue,
                       ROUND(SUM(rev_ex_vat), 2)   AS revenue_ex_vat,
                       ROUND(SUM(rev_inc_vat), 2)  AS pos_total_inc_vat_diagnostic
                FROM (
                    SELECT
                        substr(sm.period_month,1,4) || '-Q' ||
                        ((CAST(substr(sm.period_month,6,2) AS INTEGER)-1)/3+1) AS qtr,
                        sm.total_inc_vat   AS rev_inc_vat,
                        sm.subtotal_ex_vat AS rev_ex_vat
                    FROM sales_sku_monthly sm
                    JOIN branches b ON b.branch_id = sm.branch_id
                    {where}
                ) sub
                GROUP BY qtr
                ORDER BY qtr
                """,
                params,
            ).fetchall()
        return [{"period_month": r["period_month"], "revenue": r["revenue"],
                 "revenue_ex_vat": r["revenue_ex_vat"], "source": "Easy"} for r in rows]

    # -- yearly (YoY comparison: returns (year, month_num) rows, NO month range filter) --
    if granularity == "yearly":
        filters_y = ["b.name != 'ALL'"]
        params_y: dict = {}
        if branch:
            filters_y.append("b.name = :branch")
            params_y["branch"] = branch
        _group_filter(filters_y, params_y, group)
        where_y = "WHERE " + " AND ".join(filters_y)
        with get_db() as con:
            rows = con.execute(
                f"""
                SELECT sm.period_month                       AS period_month,
                       substr(sm.period_month, 1, 4)          AS year,
                       substr(sm.period_month, 6, 2)          AS month_num,
                       ROUND(SUM(sm.subtotal_ex_vat), 2)      AS revenue,
                       ROUND(SUM(sm.subtotal_ex_vat), 2)      AS revenue_ex_vat,
                       ROUND(SUM(sm.total_inc_vat), 2)        AS pos_total_inc_vat_diagnostic
                FROM sales_sku_monthly sm
                JOIN branches b ON b.branch_id = sm.branch_id
                {where_y}
                GROUP BY sm.period_month
                ORDER BY sm.period_month
                """,
                params_y,
            ).fetchall()
            # Tx + customer counts from the pre-aggregated month summary.
            # SUM(transaction_count)/SUM(customer_count) per period_month is
            # identical to COUNT(*)/SUM(customer_count) over raw sales_transaction
            # (verified across every branch + filter), but reads ~26 rows/month
            # instead of sorting 570k tx rows in a temp b-tree (~320ms -> <1ms).
            tx_rows_y = con.execute(
                f"""
                SELECT sm.period_month            AS period_month,
                       SUM(sm.transaction_count)  AS tx_count,
                       SUM(sm.customer_count)     AS customer_count
                FROM sales_month_summary sm
                JOIN branches b ON b.branch_id = sm.branch_id
                {where_y}
                GROUP BY sm.period_month
                """,
                params_y,
            ).fetchall()
            tx_map_y = {r["period_month"]: dict(r) for r in tx_rows_y}
            missing_y = [r["period_month"] for r in rows
                         if not (tx_map_y.get(r["period_month"], {}).get("tx_count"))]
            tx_map_y.update(_txn_fallback_counts(con, missing_y, branch, group, None))
        out = []
        for r in rows:
            tx = tx_map_y.get(r["period_month"], {})
            out.append({
                "period_month":   r["period_month"],
                "year":           r["year"],
                "month_num":      r["month_num"],
                "revenue":        r["revenue"],
                "revenue_ex_vat": r["revenue_ex_vat"],
                "tx_count":       tx.get("tx_count", 0),
                "customer_count": tx.get("customer_count", 0),
                "source":         "Easy",
            })
        return out

    # -- monthly (default) --
    with get_db() as con:
        filters_sku: list = ["b.name != 'ALL'"]
        params_sku: dict = {}
        if branch:
            filters_sku.append("b.name = :branch")
            params_sku["branch"] = branch
        _group_filter(filters_sku, params_sku, group)
        if source:
            filters_sku.append("sm.source = :source")
            params_sku["source"] = source
        _month_range_filter(filters_sku, params_sku, month_from, month_to, month, col="sm.period_month")
        where_sku = "WHERE " + " AND ".join(filters_sku)

        # Revenue from Easy SKU data
        sku_rows = con.execute(
            f"""
            SELECT sm.period_month,
                   ROUND(SUM(sm.subtotal_ex_vat), 2)  AS revenue,
                   ROUND(SUM(sm.subtotal_ex_vat), 2)  AS revenue_ex_vat,
                   ROUND(SUM(sm.total_inc_vat), 2)    AS pos_total_inc_vat_diagnostic
            FROM sales_sku_monthly sm
            JOIN branches b ON b.branch_id = sm.branch_id
            {where_sku}
            GROUP BY sm.period_month
            ORDER BY sm.period_month
            """,
            params_sku,
        ).fetchall()
        # Transaction counts from sales_transaction (Easy data)
        filters_tx: list = ["b.name != 'ALL'"]
        params_tx: dict = {}
        if branch:
            filters_tx.append("b.name = :branch")
            params_tx["branch"] = branch
        _group_filter(filters_tx, params_tx, group)
        if source:
            filters_tx.append("sm.source = :source")
            params_tx["source"] = source
        _month_range_filter(filters_tx, params_tx, month_from, month_to, month, col="sm.period_month")
        where_tx = "WHERE " + " AND ".join(filters_tx)

        # Counts from the pre-aggregated month summary — identical result to
        # COUNT(*)/SUM(customer_count) over raw sales_transaction (verified per
        # branch + filter), without the 570k-row temp-btree sort (~320ms -> <1ms).
        tx_rows = con.execute(
            f"""
            SELECT sm.period_month            AS period_month,
                   SUM(sm.transaction_count)  AS tx_count,
                   SUM(sm.customer_count)     AS customer_count
            FROM sales_month_summary sm
            JOIN branches b ON b.branch_id = sm.branch_id
            {where_tx}
            GROUP BY sm.period_month
            ORDER BY period_month
            """,
            params_tx,
        ).fetchall()

        tx_map = {r["period_month"]: dict(r) for r in tx_rows}
        missing = [mo for mo in {r["period_month"] for r in sku_rows}
                   if not (tx_map.get(mo, {}).get("tx_count"))]
        tx_map.update(_txn_fallback_counts(con, missing, branch, group, source))

    all_months = sorted(r["period_month"] for r in sku_rows)
    sku_map = {r["period_month"]: dict(r) for r in sku_rows}

    result = []
    for mo in all_months:
        sku = sku_map.get(mo, {})
        tx  = tx_map.get(mo, {})
        total_tx   = tx.get("tx_count")       or 0
        total_cust = tx.get("customer_count") or 0
        result.append({
            "period_month":   mo,
            "revenue":        sku.get("revenue"),
            "revenue_ex_vat": sku.get("revenue_ex_vat"),
            "tx_count":       total_tx if total_tx else None,
            "customer_count": total_cust if total_cust else None,
            "source":         "Easy",
        })

    return result


# ---------------------------------------------------------------------------
# /api/sales/category   -  revenue by category for a month/range
# ---------------------------------------------------------------------------
@app.get("/api/sales/category")
def sales_by_category(
    month:      Optional[str] = Query(None, description="YYYY-MM; omit for all-time"),
    month_from: Optional[str] = Query(None),
    month_to:   Optional[str] = Query(None),
    branch:     Optional[str] = Query(None),
    group:      Optional[str] = Query(None),
    source:     Optional[str] = Query(None),
):
    """Revenue breakdown by category."""
    filters = ["b.name != 'ALL'"]
    params: dict = {}

    if branch:
        filters.append("b.name = :branch")
        params["branch"] = branch
    _group_filter(filters, params, group)
    _month_range_filter(filters, params, month_from, month_to, month, col="sm.period_month")
    if source:
        filters.append("sm.source = :source")
        params["source"] = source

    where = "WHERE " + " AND ".join(filters)

    with get_db() as con:
        rows = con.execute(
            f"""
            SELECT c.name AS category,
                   ROUND(SUM(sm.subtotal_ex_vat), 2) AS revenue,
                   ROUND(SUM(sm.qty), 2)            AS qty
            FROM sales_sku_monthly sm
            JOIN branches b    ON b.branch_id    = sm.branch_id
            JOIN skus sk       ON sk.sku_id       = sm.sku_id
            JOIN categories c  ON c.category_id   = sk.category_id
            {where}
            GROUP BY c.name
            ORDER BY revenue DESC
            """,
            params,
        ).fetchall()

    return [{"category": r["category"], "revenue": r["revenue"], "qty": r["qty"]} for r in rows]


# ---------------------------------------------------------------------------
# /api/sales/skus   -  top SKUs
# ---------------------------------------------------------------------------
@app.get("/api/sales/skus")
def top_skus(
    month:      Optional[str] = Query(None),
    month_from: Optional[str] = Query(None),
    month_to:   Optional[str] = Query(None),
    branch:     Optional[str] = Query(None),
    group:      Optional[str] = Query(None),
    source:     Optional[str] = Query(None),
    category:   Optional[str] = Query(None),
    sort:       str           = Query("revenue", description="'revenue' or 'qty'"),
    limit:      int           = Query(20, ge=1, le=200),
):
    """Top SKUs by revenue/qty, with cost and margin if sku_cost is populated.

    FOOD-ONLY menu ranking (2026-06-01): classified by CATEGORY (robust, vs the
    earlier fragile A#/B# code-prefix rule). Excludes drinks/alcohol, non-product
    lines (buffet tier, payment, delivery, service, promotion, voucher, discount,
    deposit, take-away, catering, condiment), dessert/ice-cream, and per-branch
    'Special <branch>' menus — leaving the core Oshinei food dishes. See
    _FOOD_CATEGORY_FILTER.
    """
    filters = ["b.name != 'ALL'", "COALESCE(b.active,1) = 1", "COALESCE(sk.active,1) = 1"]
    params: dict = {}

    filters.append(_FOOD_CATEGORY_FILTER)   # real food dishes only

    if branch:
        filters.append("b.name = :branch")
        params["branch"] = branch
    _group_filter(filters, params, group)
    _month_range_filter(filters, params, month_from, month_to, month, col="sm.period_month")
    if source:
        filters.append("sm.source = :source")
        params["source"] = source
    if category:
        filters.append("c.name = :category")
        params["category"] = category

    where = "WHERE " + " AND ".join(filters)
    params["limit"] = limit

    with get_db() as con:
        rows = con.execute(
            f"""
            SELECT sk.sku_code,
                   sk.description,
                   c.name                            AS category,
                   sk.unit,
                   ROUND(SUM(sm.qty), 2)             AS qty,
                   ROUND(SUM(sm.subtotal_ex_vat), 2)   AS revenue,
                   ROUND(SUM(sm.subtotal_ex_vat), 2) AS revenue_ex_vat,
                   SUM({_cogs_case(ssm='sm', sk='sk', sc='sc')}) AS cogs_total,
                   SUM(CASE WHEN {_effective_cost_subquery(ssm='sm', sc='sc')} IS NOT NULL
                            THEN sm.qty ELSE 0 END)              AS costed_qty
            FROM sales_sku_monthly sm
            JOIN branches b    ON b.branch_id   = sm.branch_id
            JOIN skus sk       ON sk.sku_id      = sm.sku_id
            JOIN categories c  ON c.category_id  = sk.category_id
            LEFT JOIN sku_cost sc ON sc.sku_id   = sm.sku_id
            {where}
            GROUP BY sk.sku_id
            ORDER BY {"qty" if sort == "qty" else "revenue"} DESC
            LIMIT :limit
            """,
            params,
        ).fetchall()

    result = []
    for r in rows:
        cogs_total = r["cogs_total"]
        costed_qty = r["costed_qty"] or 0
        qty       = r["qty"] or 0.0
        rev_ex    = r["revenue_ex_vat"] or 0.0
        has_cost  = cogs_total is not None and costed_qty > 0
        cost      = (cogs_total / costed_qty) if has_cost else None
        cogs      = round(cogs_total, 2) if has_cost else None
        margin    = round(rev_ex - cogs, 2) if cogs is not None else None
        margin_pct = round(margin / rev_ex * 100, 1) if (margin is not None and rev_ex) else None
        result.append({
            "sku_code":    r["sku_code"],
            "description": r["description"],
            "category":    r["category"],
            "unit":        r["unit"],
            "qty":         qty,
            "revenue":     r["revenue"],
            "unit_cost":   cost,
            "cogs":        cogs,
            "margin":      margin,
            "margin_pct":  margin_pct,
        })
    return result


def _mtd_window(con):
    """Equal-period (MTD) comparison window when the latest month is incomplete.
    Returns None when the latest month is complete (use normal monthly MoM).
    STANDING RULE: never compare a partial month against a full month."""
    import calendar as _cal
    row = con.execute("SELECT MAX(day) m FROM sales_sku_daily").fetchone()
    dmax = row["m"] if row else None
    if not dmax:
        return None
    y, m, d = (int(x) for x in dmax.split("-"))
    if d >= _cal.monthrange(y, m)[1]:
        return None
    pidx = y * 12 + (m - 1) - 1; py, pm = pidx // 12, pidx % 12 + 1
    ped = min(d, _cal.monthrange(py, pm)[1])
    return {"cutoff_day": d, "latest_month": f"{y:04d}-{m:02d}", "prev_month": f"{py:04d}-{pm:02d}",
            "cur_s": f"{y:04d}-{m:02d}-01", "cur_e": dmax,
            "prev_s": f"{py:04d}-{pm:02d}-01", "prev_e": f"{py:04d}-{pm:02d}-{ped:02d}"}


@app.get("/api/sales/top_ordered")
def top_ordered(
    month:      Optional[str] = Query(None),
    month_from: Optional[str] = Query(None),
    month_to:   Optional[str] = Query(None),
    branch:     Optional[str] = Query(None),
    group:      Optional[str] = Query(None),
    sort:       str           = Query("qty", description="'qty' or 'revenue'"),
    limit:      int           = Query(15, ge=1, le=100),
):
    """Most-ordered food SKUs with share-of-total quantity and month-over-month
    change. Share = SKU qty ÷ total food qty in the selected window. MoM compares
    the two most recent months present (≤ month_to) so it stays meaningful even
    when the ranking window is a single month or a wide range."""
    base = ["b.name != 'ALL'", "COALESCE(b.active,1) = 1", "COALESCE(sk.active,1) = 1",
            _FOOD_CATEGORY_FILTER]
    params: dict = {}
    if branch:
        base.append("b.name = :branch")
        params["branch"] = branch
    _group_filter(base, params, group)
    _month_range_filter(base, params, month_from, month_to, month, col="sm.period_month")
    where = "WHERE " + " AND ".join(base)
    order = {"qty": "qty", "cogs": "cogs", "revenue": "revenue"}.get(sort, "revenue")
    cogs_expr = _unified_cogs_expr("sm")   # per-row BoM COGS (qty × recipe cost; tier=0)

    with get_db() as con:
        rows = con.execute(f"""
            SELECT sk.sku_id, sk.sku_code, sk.description, c.name AS category,
                   ROUND(SUM(sm.qty), 2)             AS qty,
                   ROUND(SUM(sm.subtotal_ex_vat), 2) AS revenue,
                   ROUND(SUM({cogs_expr}), 2)        AS cogs
            FROM sales_sku_monthly sm
            JOIN branches b    ON b.branch_id   = sm.branch_id
            JOIN skus sk       ON sk.sku_id     = sm.sku_id
            JOIN categories c  ON c.category_id = sk.category_id
            {where}
            GROUP BY sk.sku_id
            ORDER BY {order} DESC
            LIMIT :limit
        """, {**params, "limit": limit}).fetchall()

        total_row = con.execute(f"""
            SELECT ROUND(SUM(sm.qty), 2) AS total_qty,
                   ROUND(SUM(sm.subtotal_ex_vat), 2) AS total_rev,
                   ROUND(SUM({cogs_expr}), 2) AS total_cogs
            FROM sales_sku_monthly sm
            JOIN branches b    ON b.branch_id   = sm.branch_id
            JOIN skus sk       ON sk.sku_id     = sm.sku_id
            JOIN categories c  ON c.category_id = sk.category_id
            {where}
        """, params).fetchone()

        # Total covers (heads) in the same window — for cost-per-head normalization.
        # All paying customers, not food-only: cost/head spreads a dish's ingredient
        # cost across every cover served that period.
        cov_filt = ["b.name != 'ALL'", "t.source = 'Easy'"]
        cov_params: dict = {}
        if branch:
            cov_filt.append("b.name = :cbranch"); cov_params["cbranch"] = branch
        _group_filter(cov_filt, cov_params, group)
        _cf = month_from or month; _ct = month_to or month
        if _cf:
            cov_filt.append("strftime('%Y-%m',t.sold_at) >= :cmf"); cov_params["cmf"] = _cf
        if _ct:
            cov_filt.append("strftime('%Y-%m',t.sold_at) <= :cmt"); cov_params["cmt"] = _ct
        cov_row = con.execute(
            f"""SELECT SUM(t.customer_count) AS covers
                FROM sales_transaction t JOIN branches b ON b.branch_id = t.branch_id
                WHERE {' AND '.join(cov_filt)}""", cov_params).fetchone()
        total_covers = (cov_row["covers"] or 0) if cov_row else 0

        # MoM: two most recent months present in the (branch/group) scope, ≤ month_to
        mfilt = ["b.name != 'ALL'", _FOOD_CATEGORY_FILTER]
        mparams: dict = {}
        if branch:
            mfilt.append("b.name = :branch")
            mparams["branch"] = branch
        _group_filter(mfilt, mparams, group)
        cutoff = month_to or month
        if cutoff:
            mfilt.append("sm.period_month <= :cutoff")
            mparams["cutoff"] = cutoff
        months = con.execute(f"""
            SELECT DISTINCT sm.period_month AS m
            FROM sales_sku_monthly sm
            JOIN branches b    ON b.branch_id   = sm.branch_id
            JOIN skus sk       ON sk.sku_id     = sm.sku_id
            JOIN categories c  ON c.category_id = sk.category_id
            WHERE {' AND '.join(mfilt)}
            ORDER BY m DESC LIMIT 2
        """, mparams).fetchall()
        latest = months[0]["m"] if months else None
        prev   = months[1]["m"] if len(months) > 1 else None

        mom_map: dict = {}
        rev_mom_map: dict = {}
        mom_basis = "month"
        mtd_cut = None
        ids = [r["sku_id"] for r in rows]
        if latest and ids:
            ph  = ",".join(f":id{i}" for i in range(len(ids)))
            idp = {f"id{i}": v for i, v in enumerate(ids)}
            mom_params = {**idp, "lm": latest, "pm": prev or "__none__"}
            branch_clause = ""
            if branch:
                branch_clause = "AND b.name = :branch"
                mom_params["branch"] = branch
            mrows = con.execute(f"""
                SELECT sm.sku_id, sm.period_month AS m,
                       SUM(sm.qty) AS q, SUM(sm.subtotal_ex_vat) AS rev
                FROM sales_sku_monthly sm
                JOIN branches b ON b.branch_id = sm.branch_id
                WHERE sm.sku_id IN ({ph})
                  AND sm.period_month IN (:lm, :pm) {branch_clause}
                GROUP BY sm.sku_id, sm.period_month
            """, mom_params).fetchall()
            tmp: dict = {}; tmpR: dict = {}
            for mr in mrows:
                tmp.setdefault(mr["sku_id"], {})[mr["m"]]  = mr["q"]   or 0
                tmpR.setdefault(mr["sku_id"], {})[mr["m"]] = mr["rev"] or 0
            for sid, mm in tmp.items():
                cur = mm.get(latest, 0)
                pr  = mm.get(prev) if prev else None
                mom_map[sid] = ((cur - pr) / pr * 100) if (pr and pr > 0) else None
            for sid, mm in tmpR.items():
                cur = mm.get(latest, 0)
                pr  = mm.get(prev) if prev else None
                rev_mom_map[sid] = ((cur - pr) / pr * 100) if (pr and pr > 0) else None

        # MTD override (STANDING RULE): if the latest month is incomplete, recompute
        # MoM from sales_sku_daily over the SAME day-range of both months.
        mtd = _mtd_window(con)
        if mtd and latest == mtd["latest_month"] and ids:
            mom_basis = "mtd"; mtd_cut = mtd["cutoff_day"]
            phd = ",".join(f":mid{i}" for i in range(len(ids)))
            dparams = {f"mid{i}": v for i, v in enumerate(ids)}
            dparams.update({"cs": mtd["cur_s"], "ce": mtd["cur_e"], "ps": mtd["prev_s"], "pe": mtd["prev_e"]})
            dwhere = [f"d.sku_id IN ({phd})", "(d.day BETWEEN :cs AND :ce OR d.day BETWEEN :ps AND :pe)"]
            if branch:
                dwhere.append("b.name = :dbranch"); dparams["dbranch"] = branch
            gf: list = []; _group_filter(gf, dparams, group)
            dwhere += gf
            drows = con.execute(f"""
                SELECT d.sku_id,
                  SUM(CASE WHEN d.day BETWEEN :cs AND :ce THEN d.qty ELSE 0 END) cq,
                  SUM(CASE WHEN d.day BETWEEN :ps AND :pe THEN d.qty ELSE 0 END) pq,
                  SUM(CASE WHEN d.day BETWEEN :cs AND :ce THEN d.subtotal_ex_vat ELSE 0 END) cr,
                  SUM(CASE WHEN d.day BETWEEN :ps AND :pe THEN d.subtotal_ex_vat ELSE 0 END) pr
                FROM sales_sku_daily d JOIN branches b ON b.branch_id = d.branch_id
                WHERE {' AND '.join(dwhere)} GROUP BY d.sku_id""", dparams).fetchall()
            mom_map = {}; rev_mom_map = {}
            for dr in drows:
                mom_map[dr["sku_id"]]     = ((dr["cq"] - dr["pq"]) / dr["pq"] * 100) if dr["pq"] else None
                rev_mom_map[dr["sku_id"]] = ((dr["cr"] - dr["pr"]) / dr["pr"] * 100) if dr["pr"] else None
            latest = mtd["latest_month"]; prev = mtd["prev_month"]

        # Covers over the SAME comparison windows used for MoM (MTD-fair when the latest
        # month is partial) — lets the client show how each menu's cost-per-head changed,
        # reflecting BOTH the dish's volume change and the traffic (covers) change.
        def _covers_window(ds, de):
            cf = ["b.name != 'ALL'", "t.source = 'Easy'", "date(t.sold_at) BETWEEN :wf AND :wt"]
            cp = {"wf": ds, "wt": de}
            if branch:
                cf.append("b.name = :wbranch"); cp["wbranch"] = branch
            _group_filter(cf, cp, group)
            row = con.execute(f"SELECT SUM(t.customer_count) c FROM sales_transaction t "
                              f"JOIN branches b ON b.branch_id = t.branch_id "
                              f"WHERE {' AND '.join(cf)}", cp).fetchone()
            return (row["c"] or 0) if row else 0
        cmp_covers_cur = cmp_covers_prev = 0
        if mom_basis == "mtd" and mtd:
            cmp_covers_cur  = _covers_window(mtd["cur_s"],  mtd["cur_e"])
            cmp_covers_prev = _covers_window(mtd["prev_s"], mtd["prev_e"])
        elif latest and prev:
            cmp_covers_cur  = _covers_window(latest + "-01", latest + "-31")
            cmp_covers_prev = _covers_window(prev   + "-01", prev   + "-31")

        # Sparkline: per-SKU monthly qty series (YTD / last 12 months ≤ cutoff)
        spark_map: dict = {}
        spark_months: list = []
        if ids:
            allm = con.execute(f"""
                SELECT DISTINCT sm.period_month AS m
                FROM sales_sku_monthly sm
                JOIN branches b   ON b.branch_id   = sm.branch_id
                JOIN skus sk      ON sk.sku_id      = sm.sku_id
                JOIN categories c ON c.category_id  = sk.category_id
                WHERE {' AND '.join(mfilt)}
                ORDER BY m
            """, mparams).fetchall()
            spark_months = [r["m"] for r in allm][-12:]
            if spark_months:
                ph2 = ",".join(f":sid{i}" for i in range(len(ids)))
                mph = ",".join(f":spm{i}" for i in range(len(spark_months)))
                sp_params = {f"sid{i}": v for i, v in enumerate(ids)}
                sp_params.update({f"spm{i}": v for i, v in enumerate(spark_months)})
                sbranch = ""
                if branch:
                    sbranch = "AND b.name = :sbranch"; sp_params["sbranch"] = branch
                srows = con.execute(f"""
                    SELECT sm.sku_id, sm.period_month AS m, SUM(sm.qty) AS q
                    FROM sales_sku_monthly sm
                    JOIN branches b ON b.branch_id = sm.branch_id
                    WHERE sm.sku_id IN ({ph2})
                      AND sm.period_month IN ({mph}) {sbranch}
                    GROUP BY sm.sku_id, sm.period_month
                """, sp_params).fetchall()
                tmpS: dict = {}
                for sr in srows:
                    tmpS.setdefault(sr["sku_id"], {})[sr["m"]] = round(sr["q"] or 0, 2)
                for sid, mm in tmpS.items():
                    spark_map[sid] = [mm.get(m, 0) for m in spark_months]

    total_qty  = (total_row["total_qty"]  or 0) if total_row else 0
    total_rev  = (total_row["total_rev"]  or 0) if total_row else 0
    total_cogs = (total_row["total_cogs"] or 0) if total_row else 0
    items = []
    for r in rows:
        q = r["qty"] or 0.0
        rev = r["revenue"] or 0.0
        cogs = r["cogs"] or 0.0
        mom = mom_map.get(r["sku_id"])
        rmom = rev_mom_map.get(r["sku_id"])
        items.append({
            "sku_code":      r["sku_code"],
            "description":   r["description"],
            "category":      r["category"],
            "qty":           q,
            "revenue":       rev,
            "cogs":          cogs,
            "qty_share_pct": round(q / total_qty * 100, 1) if total_qty else None,
            "qty_mom_pct":   round(mom, 1) if mom is not None else None,
            "rev_share_pct": round(rev / total_rev * 100, 1) if total_rev else None,
            "rev_mom_pct":   round(rmom, 1) if rmom is not None else None,
            "cogs_share_pct": round(cogs / total_cogs * 100, 1) if total_cogs else None,
            "spark":         spark_map.get(r["sku_id"]) or [],
        })
    return {"total_qty": total_qty, "total_rev": total_rev, "total_cogs": total_cogs,
            "total_covers": total_covers,
            "cmp_covers_cur": cmp_covers_cur, "cmp_covers_prev": cmp_covers_prev,
            "latest_month": latest, "prev_month": prev,
            "mom_basis": mom_basis, "mtd_cutoff_day": mtd_cut,
            "spark_months": spark_months, "items": items}


# Shared aggregator for the A/B-compare endpoints: groups a sales window (month
# range via sm, OR day range via sales_sku_daily) by an arbitrary GROUP BY column.
def _menucmp_agg(con, gb_select, gb_col, extra_where, p_base, mfrom, mto, dfrom, dto):
    base = list(extra_where); p = dict(p_base)
    if dfrom or dto:
        if dfrom: base.append("d.day>=:df"); p["df"] = dfrom
        if dto:   base.append("d.day<=:dt"); p["dt"] = dto
        # sales_sku_daily has no `source` column (it's Easy-only), so use a daily-specific
        # COGS expr that drops the source check but keeps buffet-tier=0.
        # Chain mirrors _unified_cogs_expr: cost-management (non-zero) → BoM → alias → 0.
        cogs = (f"CASE WHEN (SELECT sk_t.sku_code FROM skus sk_t WHERE sk_t.sku_id=d.sku_id) "
                f"IN {_EASY_TIER_CODES_SQL} THEN 0 ELSE d.qty * COALESCE("
                f"(SELECT sec.recipe_cost FROM sku_effective_cost sec WHERE sec.sku_id=d.sku_id AND sec.recipe_cost>0), "
                f"(SELECT SUM(bi.ingredient_cost) FROM bom_ingredients bi WHERE bi.sku_id=d.sku_id), "
                f"(SELECT SUM(bi2.ingredient_cost) FROM bom_ingredients bi2 "
                f"JOIN bom_sku_alias a ON a.resolved_sku_id=bi2.sku_id "
                f"JOIN skus s_al ON s_al.sku_id=d.sku_id WHERE a.bom_sku_code=s_al.sku_code), "
                f"0) END")
        sql = (f"SELECT {gb_select}, ROUND(SUM(d.qty),2) qty, ROUND(SUM(d.subtotal_ex_vat),2) rev, "
               f"ROUND(SUM({cogs}),2) cogs FROM sales_sku_daily d "
               f"JOIN branches b ON b.branch_id=d.branch_id JOIN skus sk ON sk.sku_id=d.sku_id "
               f"JOIN categories c ON c.category_id=sk.category_id "
               f"WHERE {' AND '.join(base)} GROUP BY {gb_col}")
    else:
        if mfrom: base.append("sm.period_month>=:mf"); p["mf"] = mfrom
        if mto:   base.append("sm.period_month<=:mt"); p["mt"] = mto
        cogs = _unified_cogs_expr("sm")
        sql = (f"SELECT {gb_select}, ROUND(SUM(sm.qty),2) qty, ROUND(SUM(sm.subtotal_ex_vat),2) rev, "
               f"ROUND(SUM({cogs}),2) cogs FROM sales_sku_monthly sm "
               f"JOIN branches b ON b.branch_id=sm.branch_id JOIN skus sk ON sk.sku_id=sm.sku_id "
               f"JOIN categories c ON c.category_id=sk.category_id "
               f"WHERE {' AND '.join(base)} GROUP BY {gb_col}")
    return [dict(r) for r in con.execute(sql, p).fetchall()]


def _cmp_pct(c, pv):
    return round((c - pv) / pv * 100, 1) if pv else None


def _covers_by_branch(con, mfrom, mto, dfrom, dto, group):
    """{branch: total covers} over a month range OR a day range — for per-head views."""
    base = ["b.name!='ALL'", "t.source='Easy'"]; p: dict = {}
    _group_filter(base, p, group)
    if dfrom or dto:
        if dfrom: base.append("date(t.sold_at)>=:cf"); p["cf"] = dfrom
        if dto:   base.append("date(t.sold_at)<=:ct"); p["ct"] = dto
    else:
        if mfrom: base.append("strftime('%Y-%m',t.sold_at)>=:cmf"); p["cmf"] = mfrom
        if mto:   base.append("strftime('%Y-%m',t.sold_at)<=:cmt"); p["cmt"] = mto
    rows = con.execute(f"SELECT b.name br, SUM(t.customer_count) c "
                       f"FROM sales_transaction t JOIN branches b ON b.branch_id=t.branch_id "
                       f"WHERE {' AND '.join(base)} GROUP BY b.name", p).fetchall()
    return {r["br"]: (r["c"] or 0) for r in rows}


def _covers_total(con, mfrom, mto, dfrom, dto, branch, group):
    """Total Easy covers over a month OR day range for the given branch/group
    scope — the denominator for per-head (ต่อหัว) cost on the compare table."""
    base = ["b.name!='ALL'", "t.source='Easy'"]; p: dict = {}
    if branch:
        base.append("b.name=:cbr"); p["cbr"] = branch
    _group_filter(base, p, group)
    if dfrom or dto:
        if dfrom: base.append("date(t.sold_at)>=:cf"); p["cf"] = dfrom
        if dto:   base.append("date(t.sold_at)<=:ct"); p["ct"] = dto
    else:
        if mfrom: base.append("strftime('%Y-%m',t.sold_at)>=:cmf"); p["cmf"] = mfrom
        if mto:   base.append("strftime('%Y-%m',t.sold_at)<=:cmt"); p["cmt"] = mto
    row = con.execute(f"SELECT SUM(t.customer_count) c FROM sales_transaction t "
                      f"JOIN branches b ON b.branch_id=t.branch_id "
                      f"WHERE {' AND '.join(base)}", p).fetchone()
    return row["c"] or 0


@app.get("/api/sales/menu-compare")
def menu_compare(
    a_from: Optional[str] = Query(None), a_to: Optional[str] = Query(None),
    b_from: Optional[str] = Query(None), b_to: Optional[str] = Query(None),
    a_date_from: Optional[str] = Query(None), a_date_to: Optional[str] = Query(None),
    b_date_from: Optional[str] = Query(None), b_date_to: Optional[str] = Query(None),
    branch: Optional[str] = Query(None), group: Optional[str] = Query(None),
    limit:  int           = Query(60, ge=1, le=300),
):
    """Top-N menu items compared across two arbitrary periods A and B — each a
    MONTH range (*_from/*_to, YYYY-MM) or a DAY range (*_date_from/*_date_to,
    YYYY-MM-DD). Per-SKU qty/rev/cogs for each period + deltas, ranked by
    combined volume, with a totals row over the shown rows."""
    # Buffet-only analysis: à-la-carte SKUs (description prefixed "(A)") are excluded
    # here on purpose — buffet is the core business and à-la-carte distorts the mix.
    ew = ["b.name!='ALL'", "COALESCE(sk.active,1)=1", _FOOD_CATEGORY_FILTER,
          "COALESCE(sk.description,'') NOT LIKE '(A)%'"]
    pb: dict = {}
    if branch:
        ew.append("b.name=:br"); pb["br"] = branch
    _group_filter(ew, pb, group)
    sel = "sk.sku_id sku_id, sk.sku_code sku_code, sk.description description, c.name category"
    with get_db() as con:
        A = {r["sku_id"]: r for r in _menucmp_agg(con, sel, "sk.sku_id", ew, pb, a_from, a_to, a_date_from, a_date_to)}
        B = {r["sku_id"]: r for r in _menucmp_agg(con, sel, "sk.sku_id", ew, pb, b_from, b_to, b_date_from, b_date_to)}
        covers_a = _covers_total(con, a_from, a_to, a_date_from, a_date_to, branch, group)
        covers_b = _covers_total(con, b_from, b_to, b_date_from, b_date_to, branch, group)
    items = []
    for sid in set(A) | set(B):
        a = A.get(sid); b = B.get(sid); meta = a or b
        qa = (a or {}).get("qty", 0) or 0; qb = (b or {}).get("qty", 0) or 0
        ra = (a or {}).get("rev", 0) or 0; rb = (b or {}).get("rev", 0) or 0
        items.append({
            "sku_id": sid, "sku_code": meta["sku_code"], "description": meta["description"],
            "category": meta.get("category"),
            "qty_a": qa, "qty_b": qb, "qty_delta_pct": _cmp_pct(qa, qb),
            "rev_a": ra, "rev_b": rb, "rev_delta_pct": _cmp_pct(ra, rb),
            "cogs_a": (a or {}).get("cogs", 0) or 0, "cogs_b": (b or {}).get("cogs", 0) or 0,
            "only_in_a": b is None, "only_in_b": a is None,
        })
    items.sort(key=lambda r: -((r["qty_a"] or 0) + (r["qty_b"] or 0)))
    items = items[:limit]
    s = lambda k: round(sum(i[k] for i in items), 2)
    totals = {"qty_a": s("qty_a"), "qty_b": s("qty_b"), "rev_a": s("rev_a"), "rev_b": s("rev_b"),
              "cogs_a": s("cogs_a"), "cogs_b": s("cogs_b"),
              "covers_a": covers_a, "covers_b": covers_b,
              "qty_delta_pct": _cmp_pct(s("qty_a"), s("qty_b")), "rev_delta_pct": _cmp_pct(s("rev_a"), s("rev_b"))}
    return {"a_range": [a_from or a_date_from, a_to or a_date_to],
            "b_range": [b_from or b_date_from, b_to or b_date_to],
            "covers_a": covers_a, "covers_b": covers_b,
            "n": len(items), "items": items, "totals": totals}


@app.get("/api/sales/menu-compare/drill")
def menu_compare_drill(
    sku_code: str = Query(...),
    a_from: Optional[str] = Query(None), a_to: Optional[str] = Query(None),
    b_from: Optional[str] = Query(None), b_to: Optional[str] = Query(None),
    a_date_from: Optional[str] = Query(None), a_date_to: Optional[str] = Query(None),
    b_date_from: Optional[str] = Query(None), b_date_to: Optional[str] = Query(None),
    group: Optional[str] = Query(None),
):
    """Dive-in for ONE menu from the compare tab: per-branch A/B breakdown + a
    full monthly trend (qty/rev/cogs). BoM detail is fetched separately from
    /api/bom-cost/recipe/{sku_code}."""
    ew = ["b.name!='ALL'", "sk.sku_code=:sc"]; pb = {"sc": sku_code}
    _group_filter(ew, pb, group)
    with get_db() as con:
        A = {r["br"]: r for r in _menucmp_agg(con, "b.name br", "b.name", ew, pb, a_from, a_to, a_date_from, a_date_to)}
        B = {r["br"]: r for r in _menucmp_agg(con, "b.name br", "b.name", ew, pb, b_from, b_to, b_date_from, b_date_to)}
        covA = _covers_by_branch(con, a_from, a_to, a_date_from, a_date_to, group)
        covB = _covers_by_branch(con, b_from, b_to, b_date_from, b_date_to, group)
        cogs_m = _unified_cogs_expr("sm")
        trend = [dict(r) for r in con.execute(
            f"""SELECT sm.period_month month, ROUND(SUM(sm.qty),2) qty,
                       ROUND(SUM(sm.subtotal_ex_vat),2) rev, ROUND(SUM({cogs_m}),2) cogs
                FROM sales_sku_monthly sm JOIN branches b ON b.branch_id=sm.branch_id
                JOIN skus sk ON sk.sku_id=sm.sku_id
                WHERE {' AND '.join(ew)} GROUP BY sm.period_month ORDER BY sm.period_month""", pb).fetchall()]
    branches = []
    for nm in set(A) | set(B):
        a = A.get(nm); b = B.get(nm)
        qa = (a or {}).get("qty", 0) or 0; qb = (b or {}).get("qty", 0) or 0
        branches.append({"branch": nm, "qty_a": qa, "qty_b": qb, "qty_delta_pct": _cmp_pct(qa, qb),
                         "rev_a": (a or {}).get("rev", 0) or 0, "rev_b": (b or {}).get("rev", 0) or 0,
                         "cogs_a": (a or {}).get("cogs", 0) or 0, "cogs_b": (b or {}).get("cogs", 0) or 0,
                         "covers_a": covA.get(nm, 0), "covers_b": covB.get(nm, 0)})
    branches.sort(key=lambda r: -((r["qty_a"] or 0) + (r["qty_b"] or 0)))
    return {"sku_code": sku_code, "branches": branches, "trend": trend}


@app.get("/api/sales/heavy-eaters")
def heavy_eaters(
    branch:     Optional[str] = Query(None),
    group:      Optional[str] = Query(None),
    date_from:  Optional[str] = Query(None),
    date_to:    Optional[str] = Query(None),
    min_covers: int           = Query(1, ge=1, le=50),
    limit:      int           = Query(40, ge=1, le=200),
):
    """Per-cover economics & 'heavy-eater' distribution for the AYCE buffet.

    Joins per-bill BoM COGS (sale_bill_cogs) with the bill header
    (sales_transaction → covers + ex-VAT revenue) on receipt_no. For each
    bill: revenue/head, COGS/head and margin/head. Then summarises the
    distribution + the costly tail (heavy eaters = COGS/head ≥ p90) and the
    loss-making bills (margin/head < 0). sale_bill_cogs is a rolling ~31-day
    window, so the date range is bounded to whatever it currently holds."""
    from collections import defaultdict
    base = ["t.source='Easy'", "b.name!='ALL'", "t.customer_count>=:mc", "t.total_inc_vat>0"]
    p: dict = {"mc": min_covers}
    if branch:
        base.append("b.name=:br"); p["br"] = branch
    _group_filter(base, p, group)
    if date_from: base.append("sbc.day>=:df"); p["df"] = date_from
    if date_to:   base.append("sbc.day<=:dt"); p["dt"] = date_to
    with get_db() as con:
        win = con.execute("SELECT MIN(day) lo, MAX(day) hi FROM sale_bill_cogs").fetchone()
        rows = con.execute(f"""
            SELECT b.name br, sbc.day day, sbc.receipt_no rcpt, t.customer_count cov,
                   t.total_inc_vat/1.07 rev, sbc.cogs_bom cogs
            FROM sale_bill_cogs sbc
            JOIN sales_transaction t ON t.receipt_no = sbc.receipt_no
            JOIN branches b ON b.branch_id = sbc.branch_id
            WHERE {' AND '.join(base)}""", p).fetchall()
    bills = []
    for r in rows:
        cov = r["cov"] or 0
        if cov < 1:
            continue
        rev = r["rev"] or 0.0; cogs = r["cogs"] or 0.0
        bills.append({"branch": r["br"], "day": r["day"], "receipt": r["rcpt"],
                      "covers": cov, "rev_head": rev / cov, "cogs_head": cogs / cov,
                      "margin_head": (rev - cogs) / cov, "rev": rev, "cogs": cogs})
    n = len(bills)

    def pctile(sv, q):
        if not sv:
            return 0.0
        i = min(len(sv) - 1, max(0, int(round(q * (len(sv) - 1)))))
        return sv[i]
    ch_sorted = sorted(b["cogs_head"] for b in bills)
    percentiles = {k: round(pctile(ch_sorted, q), 2) for k, q in
                   (("p50", .5), ("p75", .75), ("p90", .9), ("p95", .95), ("p99", .99))}
    p90 = pctile(ch_sorted, .9)
    tot_cov = sum(b["covers"] for b in bills)
    tot_cogs = sum(b["cogs"] for b in bills)
    tot_rev = sum(b["rev"] for b in bills)

    def agg(lst):
        c = sum(x["covers"] for x in lst); cg = sum(x["cogs"] for x in lst); rv = sum(x["rev"] for x in lst)
        return {"bills": len(lst), "covers": c, "cogs": round(cg, 2), "rev": round(rv, 2),
                "avg_cogs_head": round(cg / c, 2) if c else 0, "avg_rev_head": round(rv / c, 2) if c else 0,
                "avg_margin_head": round((rv - cg) / c, 2) if c else 0,
                "pct_bills": round(100 * len(lst) / n, 1) if n else 0,
                "pct_cogs": round(100 * cg / tot_cogs, 1) if tot_cogs else 0}
    heavy = [b for b in bills if p90 > 0 and b["cogs_head"] >= p90]
    loss = [b for b in bills if b["margin_head"] < 0]

    hi = pctile(ch_sorted, .99) or (ch_sorted[-1] if ch_sorted else 0)
    nb = 12; width = (hi / nb) if hi > 0 else 1
    hcounts = [0] * (nb + 1)
    for b in bills:
        idx = min(nb, int(b["cogs_head"] // width)) if width > 0 else 0
        hcounts[idx] += 1
    histogram = [{"lo": round(i * width, 1), "hi": round((i + 1) * width, 1) if i < nb else None,
                  "count": hcounts[i]} for i in range(nb + 1)]

    bb = defaultdict(lambda: {"bills": 0, "heavy": 0, "covers": 0, "cogs": 0.0, "rev": 0.0})
    for b in bills:
        d = bb[b["branch"]]; d["bills"] += 1; d["covers"] += b["covers"]
        d["cogs"] += b["cogs"]; d["rev"] += b["rev"]
        if p90 > 0 and b["cogs_head"] >= p90:
            d["heavy"] += 1
    by_branch = []
    for nm, d in bb.items():
        c = d["covers"]
        by_branch.append({"branch": nm, "bills": d["bills"], "heavy_bills": d["heavy"],
                          "heavy_pct": round(100 * d["heavy"] / d["bills"], 1) if d["bills"] else 0,
                          "cogs_head": round(d["cogs"] / c, 2) if c else 0,
                          "rev_head": round(d["rev"] / c, 2) if c else 0,
                          "margin_head": round((d["rev"] - d["cogs"]) / c, 2) if c else 0})
    by_branch.sort(key=lambda x: -x["heavy_pct"])

    worst = sorted(bills, key=lambda b: b["margin_head"])[:limit]
    examples = [{"branch": b["branch"], "day": b["day"], "receipt": b["receipt"], "covers": b["covers"],
                 "rev_head": round(b["rev_head"], 2), "cogs_head": round(b["cogs_head"], 2),
                 "margin_head": round(b["margin_head"], 2)} for b in worst]

    return {"window": {"lo": win["lo"] if win else None, "hi": win["hi"] if win else None},
            "range": [date_from or (win["lo"] if win else None), date_to or (win["hi"] if win else None)],
            "n_bills": n, "total_covers": tot_cov,
            "avg_cogs_head": round(tot_cogs / tot_cov, 2) if tot_cov else 0,
            "avg_rev_head": round(tot_rev / tot_cov, 2) if tot_cov else 0,
            "avg_margin_head": round((tot_rev - tot_cogs) / tot_cov, 2) if tot_cov else 0,
            "percentiles": percentiles, "heavy_threshold": round(p90, 2),
            "heavy": agg(heavy), "loss": agg(loss),
            "histogram": histogram, "by_branch": by_branch, "examples": examples}


@app.get("/api/sales/daily")
def sales_daily(
    date:   Optional[str] = Query(None),
    branch: Optional[str] = Query(None),
    group:  Optional[str] = Query(None),
    gran:   str           = Query("day"),     # day | week | month
    trend:  int           = Query(8, ge=2, le=26),
):
    """Per-branch sales for a day/week/month with same-period MoM & YoY deltas,
    revenue/head, a trailing trend, and anomaly flags. REVENUE ONLY (ex-VAT
    goods = food+drink+other) — no cost here; COGS stays on BoM elsewhere."""
    import datetime as _dt, calendar as _cal
    if gran not in ("day", "week", "month"):
        gran = "day"
    base = ["t.source='Easy'", "b.name!='ALL'"]
    params: dict = {}
    if branch:
        base.append("b.name=:branch"); params["branch"] = branch
    _group_filter(base, params, group)
    where = " AND ".join(base)

    mtd_cut_day = None   # set below when the latest month is partial → MTD-fair month compare
    def bounds(d):
        if gran == "day":
            return d, d
        if gran == "week":
            s = d - _dt.timedelta(days=d.weekday()); return s, s + _dt.timedelta(days=6)
        s = d.replace(day=1)
        last = _cal.monthrange(s.year, s.month)[1]
        # When the latest month is still partial, clamp EVERY month period (current /
        # prev / YoY) to the same elapsed days so month-vs-month is MTD-vs-MTD fair.
        end_day = min(last, mtd_cut_day) if mtd_cut_day else last
        return s, s.replace(day=end_day)

    def minus_month(d, n=1):
        idx = d.year * 12 + (d.month - 1) - n
        y, m = idx // 12, idx % 12 + 1
        return _dt.date(y, m, min(d.day, _cal.monthrange(y, m)[1]))

    def comp(fdate, back):       # back = 'month' | 'year'
        if gran == "day":
            if back == "month":
                return minus_month(fdate, 1)
            try:    return fdate.replace(year=fdate.year - 1)
            except ValueError: return fdate.replace(year=fdate.year - 1, day=28)
        if gran == "week":
            return fdate - _dt.timedelta(days=28 if back == "month" else 364)
        if back == "month":
            return minus_month(fdate.replace(day=1), 1)
        return fdate.replace(year=fdate.year - 1, day=1)

    def pct(c, p):
        return round((c - p) / p * 100, 1) if p else None

    with get_db() as con:
        maxd = con.execute(
            f"SELECT MAX(date(t.sold_at)) FROM sales_transaction t "
            f"JOIN branches b ON b.branch_id=t.branch_id WHERE {where}", params).fetchone()[0]
        if not maxd:
            return {"gran": gran, "focus": None, "branches": [], "total": {}, "trend": [], "anomalies": []}
        # Default focus must skip a not-yet-fully-synced trailing month, else the
        # latest "month" is a 3-branch sliver -> -100% MoM for the unsynced branches.
        if not date:
            _pm = _partial_months(con)
            if maxd[:7] in _pm:
                _mp = {f"_pm{i}": m for i, m in enumerate(_pm)}
                _ph = ",".join(f":{k}" for k in _mp)
                _rd = con.execute(
                    f"SELECT MAX(date(t.sold_at)) FROM sales_transaction t "
                    f"JOIN branches b ON b.branch_id=t.branch_id WHERE {where} "
                    f"AND strftime('%Y-%m', t.sold_at) NOT IN ({_ph})",
                    {**params, **_mp}).fetchone()[0]
                if _rd:
                    maxd = _rd
        focus = (date or maxd)[:10]
        fy, fm, fd = (int(x) for x in focus.split("-"))
        fdate = _dt.date(fy, fm, fd)
        # MTD-fair month compare: if the focus month is the latest, still-partial month,
        # clamp every month period to the same elapsed days (set the closure's cutoff).
        if gran == "month":
            my, mm, mdd = (int(x) for x in maxd[:10].split("-"))
            if (fy, fm) == (my, mm) and mdd < _cal.monthrange(my, mm)[1]:
                mtd_cut_day = mdd

        def fetch(d):
            s, e = bounds(d)
            # revenue ex-VAT = total_inc_vat / 1.07 (the reconciled basis that matches
            # accounting & sales_sku_monthly). NOT food+drink+other — those double-count
            # buffet (buffet dishes duplicated in food + the package in other → +~50%).
            rows = con.execute(f"""
                SELECT b.name branch,
                       ROUND(SUM(t.total_inc_vat)/1.07,2) rev,
                       SUM(t.customer_count) cust, COUNT(*) bills
                FROM sales_transaction t JOIN branches b ON b.branch_id=t.branch_id
                WHERE {where} AND date(t.sold_at) BETWEEN :s AND :e
                GROUP BY b.name""", {**params, "s": s.isoformat(), "e": e.isoformat()}).fetchall()
            return {r["branch"]: {"rev": r["rev"] or 0, "cust": r["cust"] or 0, "bills": r["bills"]} for r in rows}, (s, e)

        cur, (cs, ce) = fetch(fdate)
        pm,  (pms, pme) = fetch(comp(fdate, "month"))
        py,  (pys, pye) = fetch(comp(fdate, "year"))

        # Guard partial coverage: if a comparison period starts before our data
        # begins, it's incomplete → drop it (avoids bogus deltas like YoY +472%
        # vs the partial first month). Same idea as the benchmark guard.
        mind = con.execute(
            f"SELECT MIN(date(t.sold_at)) FROM sales_transaction t "
            f"JOIN branches b ON b.branch_id=t.branch_id WHERE {where}", params).fetchone()[0] or "9999-99"
        if pms.isoformat() < mind:
            pm = {}
        if pys.isoformat() < mind:
            py = {}

        # Weekday-fair MoM (mirrors the Management report §3): a raw same-elapsed-
        # days compare misleads early in the month (1–2 Jul = Wed+Thu vs 1–2 Jun =
        # holiday Mon+Tue read as "-44%"), so while the focus month is partial the
        # previous-month side of every MoM number (per-branch and total, revenue
        # and customers) is rebuilt from that month's per-weekday daily averages
        # summed over the same elapsed dates. YoY keeps the plain same-days clamp.
        mom_basis = "same_days" if mtd_cut_day else "month"
        dow_tot = None
        if mtd_cut_day and pm:
            elapsed_wd = [int((cs + _dt.timedelta(days=i)).strftime("%w"))
                          for i in range(mtd_cut_day)]

            def dow_base(rows_):
                avg = {r["wd"]: ((r["rev"] or 0) / r["days"], (r["cust"] or 0) / r["days"])
                       for r in rows_ if r["days"]}
                if not avg:
                    return None
                fb = (sum(v[0] for v in avg.values()) / len(avg),
                      sum(v[1] for v in avg.values()) / len(avg))
                return {"rev":  round(sum(avg.get(w, fb)[0] for w in elapsed_wd), 2),
                        "cust": round(sum(avg.get(w, fb)[1] for w in elapsed_wd), 1),
                        "bills": 0}

            dow_sel = """CAST(strftime('%w', t.sold_at) AS INT) wd,
                       SUM(t.total_inc_vat)/1.07 rev, SUM(t.customer_count) cust,
                       COUNT(DISTINCT date(t.sold_at)) days
                FROM sales_transaction t JOIN branches b ON b.branch_id=t.branch_id"""
            dow_prm = {**params, "pmm": pms.strftime("%Y-%m")}
            by_branch: dict = {}
            for r in con.execute(f"""
                SELECT b.name branch, {dow_sel}
                WHERE {where} AND strftime('%Y-%m', t.sold_at)=:pmm
                GROUP BY b.name, wd""", dow_prm).fetchall():
                by_branch.setdefault(r["branch"], []).append(r)
            pm_dow = {nm: base for nm, rs in by_branch.items() if (base := dow_base(rs))}
            # chain-level baseline over the whole scope (NOT the sum of per-branch
            # baselines) so the total MoM ties out with the Management report §3.
            dow_tot = dow_base(con.execute(f"""
                SELECT {dow_sel}
                WHERE {where} AND strftime('%Y-%m', t.sold_at)=:pmm
                GROUP BY wd""", dow_prm).fetchall())
            if pm_dow and dow_tot:
                pm, mom_basis = pm_dow, "dow"
            else:
                dow_tot = None

        out = []
        for nm in sorted(set(cur) | set(pm) | set(py)):
            c = cur.get(nm, {"rev": 0, "cust": 0, "bills": 0})
            p = pm.get(nm); y = py.get(nm)
            crev, ccust = c["rev"], c["cust"]
            out.append({
                "branch": nm, "revenue": crev, "customers": ccust, "bills": c["bills"],
                "rev_per_head": round(crev / ccust, 1) if ccust else None,
                "mom_rev": (p or {}).get("rev"),
                "mom_delta": round(crev - (p or {}).get("rev", 0), 2) if p else None,
                "mom_pct": pct(crev, (p or {}).get("rev", 0)) if p else None,
                "yoy_rev": (y or {}).get("rev"),
                "yoy_delta": round(crev - (y or {}).get("rev", 0), 2) if y else None,
                "yoy_pct": pct(crev, (y or {}).get("rev", 0)) if y else None,
            })
        out.sort(key=lambda r: -r["revenue"])

        def tot(d): return sum(x["rev"] for x in d.values()), sum(x["cust"] for x in d.values())
        crv, ccu = tot(cur); prv, pcu = tot(pm); yrv, ycu = tot(py)
        if dow_tot:   # total MoM against the scope-level weekday baseline
            prv, pcu = dow_tot["rev"], dow_tot["cust"]
        cur_rph  = crv / ccu if ccu else None
        prev_rph = prv / pcu if pcu else None
        yoy_rph  = yrv / ycu if ycu else None
        total = {"revenue": round(crv, 2), "customers": ccu,
                 "rev_per_head": round(crv / ccu, 1) if ccu else None,
                 "mom_delta": round(crv - prv, 2), "mom_pct": pct(crv, prv),
                 "yoy_delta": round(crv - yrv, 2), "yoy_pct": pct(crv, yrv),
                 # customer-count + revenue/head deltas over the SAME MTD-fair periods as revenue
                 "cust_mom_pct": pct(ccu, pcu), "cust_yoy_pct": pct(ccu, ycu),
                 "rph_mom_pct": pct(cur_rph, prev_rph) if cur_rph is not None else None,
                 "rph_yoy_pct": pct(cur_rph, yoy_rph) if cur_rph is not None else None}

        # trailing trend (chain total rev), `trend` periods ending at focus
        tr = []; d = fdate
        for _ in range(trend):
            data, (s, e) = fetch(d)
            tr.append({"start": s.isoformat(), "end": e.isoformat(),
                       "revenue": round(sum(x["rev"] for x in data.values()), 2)})
            d = d - _dt.timedelta(days=1) if gran == "day" else \
                d - _dt.timedelta(days=7) if gran == "week" else minus_month(d.replace(day=1), 1)
        tr.reverse()

        # anomaly: chain total vs trailing average + big per-branch MoM movers
        prior = [x["revenue"] for x in tr[:-1]]
        avg = sum(prior) / len(prior) if prior else 0
        anomalies = []
        ap = pct(crv, avg)
        if ap is not None and abs(ap) >= 20:
            anomalies.append({"scope": "chain", "pct": ap,
                              "msg": f"ยอดรวม{'สูง' if ap > 0 else 'ต่ำ'}กว่าค่าเฉลี่ย {abs(ap):.0f}%"})
        for r in out:
            if r["mom_pct"] is not None and abs(r["mom_pct"]) >= 30:
                anomalies.append({"scope": r["branch"], "pct": r["mom_pct"],
                                  "msg": f"{r['branch']} {'พุ่ง' if r['mom_pct'] > 0 else 'ตก'} {abs(r['mom_pct']):.0f}% vs ช่วงก่อน"})

    return {"gran": gran, "focus": focus,
            "cur_range": [cs.isoformat(), ce.isoformat()],
            "prev_range": [pms.isoformat(), pme.isoformat()],
            "yoy_range": [pys.isoformat(), pye.isoformat()],
            "mtd": mtd_cut_day is not None, "mtd_cutoff_day": mtd_cut_day,
            "mom_basis": mom_basis,
            "branches": out, "total": total, "trend": tr, "anomalies": anomalies}


@app.get("/api/home/branch-scorecard")
@ttl_cache
def home_branch_scorecard(date: Optional[str] = Query(None), group: Optional[str] = Query(None)):
    """Per-branch scorecard for a focus day: revenue, customers, bills, rev/head,
    7-day sparkline, vs trailing-7-day average %, vs same-weekday-last-week %, and
    a status (above/below normal). REVENUE ONLY (total_inc_vat/1.07 = reconciled)."""
    import datetime as _dt
    base = ["t.source='Easy'", "b.name!='ALL'"]; params: dict = {}
    _group_filter(base, params, group)
    where = " AND ".join(base)
    with get_db() as con:
        maxd = con.execute(f"SELECT MAX(date(t.sold_at)) FROM sales_transaction t "
                           f"JOIN branches b ON b.branch_id=t.branch_id WHERE {where}", params).fetchone()[0]
        if not maxd:
            return {"date": None, "days": [], "branches": []}
        focus = (date or maxd)[:10]
        fy, fm, fd = (int(x) for x in focus.split("-"))
        fdate = _dt.date(fy, fm, fd)
        start = fdate - _dt.timedelta(days=7)
        days = [(start + _dt.timedelta(days=i)).isoformat() for i in range(8)]   # incl focus
        rows = con.execute(f"""
            SELECT b.name branch, date(t.sold_at) d,
                   ROUND(SUM(t.total_inc_vat)/1.07,2) rev, SUM(t.customer_count) cust, COUNT(*) bills
            FROM sales_transaction t JOIN branches b ON b.branch_id=t.branch_id
            WHERE {where} AND date(t.sold_at) BETWEEN :s AND :e
            GROUP BY b.name, date(t.sold_at)""",
            {**params, "s": start.isoformat(), "e": focus}).fetchall()
        bm: dict = {}
        for r in rows:
            bm.setdefault(r["branch"], {})[r["d"]] = {"rev": r["rev"] or 0, "cust": r["cust"] or 0, "bills": r["bills"]}
        out = []
        for nm, dm in bm.items():
            spark = [round(dm.get(d, {}).get("rev", 0), 0) for d in days]
            today = dm.get(focus, {})
            trev, tcust, tbills = today.get("rev", 0), today.get("cust", 0), today.get("bills", 0)
            prior = [dm.get(d, {}).get("rev", 0) for d in days[:-1]]
            prior = [x for x in prior if x > 0]
            avg7 = sum(prior) / len(prior) if prior else 0
            wow = dm.get(days[0], {}).get("rev")        # same weekday last week
            vs_avg = round((trev - avg7) / avg7 * 100, 1) if avg7 else None
            vs_wow = round((trev - wow) / wow * 100, 1) if wow else None
            status = "up" if (vs_avg is not None and vs_avg >= 10) else \
                     "down" if (vs_avg is not None and vs_avg <= -10) else "ok"
            out.append({"branch": nm, "revenue": trev, "customers": tcust, "bills": tbills,
                        "rev_per_head": round(trev / tcust, 1) if tcust else None,
                        "spark": spark, "avg7": round(avg7, 0),
                        "vs_avg_pct": vs_avg, "wow": wow, "vs_wow_pct": vs_wow, "status": status})
        out.sort(key=lambda r: -r["revenue"])
    return {"date": focus, "days": days, "branches": out}


@app.get("/api/sales/menu-spike")
def menu_spike(date: Optional[str] = Query(None), branch: Optional[str] = Query(None),
               group: Optional[str] = Query(None), limit: int = Query(300, ge=1, le=1000),
               threshold: float = Query(1.5, ge=1.0)):
    """Menus whose QTY on a given day spiked vs their trailing 21-day average, per
    branch (answers '#5: which menu was abnormally high that day'). Shows ALL items
    above `threshold`× (default 1.5×). Qty-only from sales_sku_daily — NO cost (iron rule)."""
    import datetime as _dt
    with get_db() as con:
        try:
            maxd = con.execute("SELECT MAX(day) FROM sales_sku_daily").fetchone()[0]
        except Exception:
            return {"date": None, "spikes": [], "note": "no daily sku data"}
        if not maxd:
            return {"date": None, "spikes": []}
        focus = (date or maxd)[:10]
        fy, fm, fd = (int(x) for x in focus.split("-"))
        wstart = (_dt.date(fy, fm, fd) - _dt.timedelta(days=21)).isoformat()
        bfilt = ""; params = {"focus": focus, "ws": wstart}
        if branch:
            bfilt += " AND b.name=:branch"; params["branch"] = branch
        if group:
            bfilt += " AND COALESCE(b.branch_type,'master')=:grp"; params["grp"] = group
        rows = con.execute(f"""
            WITH today AS (
              SELECT d.branch_id, d.sku_id, SUM(d.qty) q
              FROM sales_sku_daily d JOIN branches b ON b.branch_id=d.branch_id
              WHERE d.day=:focus {bfilt} GROUP BY d.branch_id, d.sku_id),
            hist AS (
              SELECT branch_id, sku_id, AVG(qty) avgq, COUNT(*) n
              FROM sales_sku_daily WHERE day>=:ws AND day<:focus GROUP BY branch_id, sku_id)
            SELECT b.name branch, s.description, s.sku_code, t.q today,
                   COALESCE(h.avgq,0) avgq, COALESCE(h.n,0) n
            FROM today t JOIN branches b ON b.branch_id=t.branch_id
            JOIN skus s ON s.sku_id=t.sku_id
            LEFT JOIN hist h ON h.branch_id=t.branch_id AND h.sku_id=t.sku_id
            WHERE t.q>0""", params).fetchall()
        spikes = []
        for r in rows:
            today = r["today"] or 0; avg = r["avgq"] or 0; n = r["n"] or 0
            name = (r["description"] or r["sku_code"] or "").replace("(B) ", "").replace("(A) ", "")
            if avg > 0 and n >= 5:
                x = today / avg
                # show ALL items above the threshold (default 1.5×); tiny floor (≥5
                # plates) keeps trivial 1→2 jumps out of the list.
                if x >= threshold and today >= 5:
                    spikes.append({"branch": r["branch"], "menu": name, "today": round(today),
                                   "avg": round(avg, 1), "x": round(x, 1), "pct": round((x - 1) * 100),
                                   "excess": round(today - avg), "is_new": False})
            elif n < 5 and today >= 20:
                spikes.append({"branch": r["branch"], "menu": name, "today": round(today),
                               "avg": 0, "x": None, "pct": None, "excess": round(today), "is_new": True})
        spikes.sort(key=lambda s: -s["excess"])
    return {"date": focus, "window_days": 21, "threshold": threshold,
            "count": len(spikes), "spikes": spikes[:limit]}


@app.get("/api/sales/cost-per-head")
def cost_per_head(date: Optional[str] = Query(None), branch: Optional[str] = Query(None),
                  group: Optional[str] = Query(None), gran: str = Query("day")):
    """#10 — BoM cost per head per branch, with previous-period comparison.
    COGS via our BoM only (_unified_cogs_expr). Daily grain from sales_sku_daily
    (rolling window), customers from sales_transaction."""
    import datetime as _dt, calendar as _cal
    if gran not in ("day", "week", "month"):
        gran = "day"
    cogs_expr = _unified_cogs_expr("ssm")

    def bounds(d):
        if gran == "day": return d, d
        if gran == "week":
            s = d - _dt.timedelta(days=d.weekday()); return s, s + _dt.timedelta(days=6)
        s = d.replace(day=1)
        nxt = s.replace(year=s.year + 1, month=1) if s.month == 12 else s.replace(month=s.month + 1)
        return s, nxt - _dt.timedelta(days=1)

    def prev_focus(d):
        if gran == "day":  return d - _dt.timedelta(days=1)
        if gran == "week": return d - _dt.timedelta(days=7)
        idx = d.year * 12 + (d.month - 1) - 1
        y, m = idx // 12, idx % 12 + 1
        return _dt.date(y, m, min(d.day, _cal.monthrange(y, m)[1]))

    tfilt = ["t.source='Easy'", "b.name!='ALL'"]; tp: dict = {}
    if branch: tfilt.append("b.name=:branch"); tp["branch"] = branch
    _group_filter(tfilt, tp, group)
    twhere = " AND ".join(tfilt)
    dbf = ""  # branch filter for the daily-sku side (by name via join)
    if branch: dbf = " AND b.name=:branch"
    # fix A1: build group condition for the COGS/revenue query so it matches the customer query scope
    _gcond_list: list = []; _gcond_params: dict = {}
    _group_filter(_gcond_list, _gcond_params, group)
    gcond = (" AND " + " AND ".join(_gcond_list)) if _gcond_list else ""

    with get_db() as con:
        maxd = con.execute("SELECT MAX(day) FROM sales_sku_daily").fetchone()[0]
        if not maxd:
            return {"gran": gran, "focus": None, "branches": [], "total": {}}
        focus = (date or maxd)[:10]
        fy, fm, fd = (int(x) for x in focus.split("-"))
        fdate = _dt.date(fy, fm, fd)

        def fetch(d):
            s, e = bounds(d)
            cust = {r["branch"]: r["cust"] or 0 for r in con.execute(
                f"""SELECT b.name branch, SUM(t.customer_count) cust
                    FROM sales_transaction t JOIN branches b ON b.branch_id=t.branch_id
                    WHERE {twhere} AND date(t.sold_at) BETWEEN :s AND :e
                    GROUP BY b.name""", {**tp, "s": s.isoformat(), "e": e.isoformat()}).fetchall()}
            cogs, rev = {}, {}
            for r in con.execute(
                f"""SELECT b.name branch, ROUND(SUM({cogs_expr}),2) cogs,
                           ROUND(SUM(ssm.subtotal_ex_vat),2) rev
                    FROM (SELECT branch_id, sku_id, qty, subtotal_ex_vat, 'Easy' AS source
                          FROM sales_sku_daily WHERE day BETWEEN :s AND :e) ssm
                    JOIN branches b ON b.branch_id=ssm.branch_id
                    WHERE b.name!='ALL'{dbf}{gcond} GROUP BY b.name""",
                {"s": s.isoformat(), "e": e.isoformat(), **({"branch": branch} if branch else {}), **_gcond_params}).fetchall():
                cogs[r["branch"]] = r["cogs"] or 0
                rev[r["branch"]]  = r["rev"] or 0
            return cust, cogs, rev, (s, e)

        cust, cogs, rev, (cs, ce) = fetch(fdate)
        pcust, pcogs, _prev_rev, (ps, pe) = fetch(prev_focus(fdate))

        def pct(c, p): return round((c - p) / p * 100, 1) if p else None
        out = []
        for nm in sorted(set(cust) | set(cogs)):
            cu, co, re = cust.get(nm, 0), cogs.get(nm, 0), rev.get(nm, 0)
            pcu, pco = pcust.get(nm, 0), pcogs.get(nm, 0)
            ch  = round(co / cu, 1) if cu else None          # cost / head
            pch = round(pco / pcu, 1) if pcu else None       # prev cost / head
            rph = round(re / cu, 1) if cu else None          # revenue / head (spend)
            cost_pct = round(co / re * 100, 1) if re else None   # food-cost ratio
            gph = round((re - co) / cu, 1) if cu else None       # GP / head
            out.append({"branch": nm, "customers": cu, "cogs": round(co, 2),
                        "revenue": round(re, 2), "cost_per_head": ch, "prev_cost_per_head": pch,
                        "delta_pct": pct(ch or 0, pch or 0) if pch else None,
                        "rev_per_head": rph, "cost_pct": cost_pct, "gp_per_head": gph})
        out.sort(key=lambda r: -(r["cost_pct"] if r["cost_pct"] is not None else -1))
        tcu = sum(cust.values()); tco = sum(cogs.values()); tre = sum(rev.values())
        ptcu = sum(pcust.values()); ptco = sum(pcogs.values())
        tch = round(tco / tcu, 1) if tcu else None
        ptch = round(ptco / ptcu, 1) if ptcu else None
        total = {"customers": tcu, "cogs": round(tco, 2), "revenue": round(tre, 2),
                 "cost_per_head": tch, "prev_cost_per_head": ptch,
                 "delta_pct": pct(tch or 0, ptch or 0) if ptch else None,
                 "rev_per_head": round(tre / tcu, 1) if tcu else None,
                 "cost_pct": round(tco / tre * 100, 1) if tre else None,
                 "gp_per_head": round((tre - tco) / tcu, 1) if tcu else None}
    return {"gran": gran, "focus": focus, "cur_range": [cs.isoformat(), ce.isoformat()],
            "prev_range": [ps.isoformat(), pe.isoformat()], "branches": out, "total": total}


# ── Top-60 menu cost-per-head · MASTER branches (overview → per-branch → variance) ──
# Powers the "💸 Top 60 ต้นทุน/หัว · สาขา Master" report tab. COGS via the LIVE
# _unified_cogs_expr (same source of truth as the rest of the app), so the in-app
# tab, the printable HTML, and the Excel all tie out. Master-only by design — the
# whole point is cross-branch benchmarking against the combined overview.
_CH_PREMIUM_KW = [
    "แซลมอน", "ทุเรียน", "หอยเชลล์", "กุ้ง", "ปู", "ทูน่า", "อูนิ", "ไข่ปลา",
    "ไข่หอยเม่น", "คาเวียร์", "ปลาดิบ", "ซาชิมิ", "ดอง", "เนื้อ", "วากิว",
    "โอโทโร่", "ชูโทโร่", "อิคุระ", "หอยนางรม", "สเต็ก", "ออส", "ปลาไหล", "อูนางิ",
]


def _ch_is_premium(desc: str) -> bool:
    d = desc or ""
    return any(k in d for k in _CH_PREMIUM_KW)


# scope → branch_type filter (active branches only). 'all' = master + franchise.
_CH_SCOPES = {
    "master":    "branch_type='master' AND active=1",
    "franchise": "branch_type='franchise' AND active=1",
    "all":       "active=1",
}
_CH_SCOPE_LABEL = {"master": "Master", "franchise": "แฟรนไชส์", "all": "ทุกสาขา"}


def _cost_head_scopes(con, month: str, scope: str = "master"):
    """Build (overview, [branch,...]) scope dicts for `month`, generator-shaped so they
    can feed the standalone report's build_html/write_xlsx unchanged — but computed with
    the LIVE _unified_cogs_expr. `scope` ∈ master|franchise|all selects which branches.
    Returns (None, []) when no branch in scope has data.

    cost-per-head of a menu = (menu monthly COGS) ÷ (scope covers = Σ customer_count).
    Buffet TIER rows are 0-cost inside the expr (consumption is on the B0xxx item rows),
    so they drop out of the costed menu list but their package revenue is added back into
    the food-cost-% denominator (Σ subtotal_ex_vat over ALL rows)."""
    cogs = _unified_cogs_expr("ssm")
    where = _CH_SCOPES.get(scope, _CH_SCOPES["master"])
    masters = [(r["branch_id"], r["name"]) for r in con.execute(
        f"SELECT branch_id, name FROM branches WHERE {where} ORDER BY branch_id").fetchall()]
    masters = [(bid, nm) for bid, nm in masters if (con.execute(
        "SELECT COALESCE(SUM(customer_count),0) FROM sales_month_summary "
        "WHERE period_month=? AND branch_id=?", (month, bid)).fetchone()[0] or 0) > 0]
    if not masters:
        return None, []

    def covers(ids):
        ph = ",".join("?" * len(ids))
        return int(con.execute(
            f"SELECT COALESCE(SUM(customer_count),0) FROM sales_month_summary "
            f"WHERE period_month=? AND branch_id IN ({ph})", [month, *ids]).fetchone()[0] or 0)

    def revenue(ids):
        ph = ",".join("?" * len(ids))
        return float(con.execute(
            f"SELECT COALESCE(SUM(subtotal_ex_vat),0) FROM sales_sku_monthly "
            f"WHERE period_month=? AND branch_id IN ({ph})", [month, *ids]).fetchone()[0] or 0)

    def menus(ids):
        ph = ",".join("?" * len(ids))
        rows = con.execute(
            f"""SELECT ssm.sku_id AS sku_id, sk.sku_code AS code, sk.description AS desc,
                       ROUND(SUM(ssm.qty),0) AS qty, ROUND(SUM({cogs}),2) AS cogs
                FROM sales_sku_monthly ssm
                JOIN skus sk ON sk.sku_id = ssm.sku_id
                WHERE ssm.period_month=? AND ssm.branch_id IN ({ph})
                GROUP BY ssm.sku_id HAVING SUM({cogs}) > 0
                ORDER BY cogs DESC""", [month, *ids]).fetchall()
        return [dict(r) for r in rows]

    def scope(name, ids):
        cv = covers(ids); ms = menus(ids)
        tc = sum(m["cogs"] for m in ms); tr = revenue(ids)
        for m in ms:
            m["cph"] = (m["cogs"] / cv) if cv else 0.0
            m["qph"] = (m["qty"] / cv) if cv else 0.0
            m["share"] = (m["cogs"] / tc) if tc else 0.0
            m["rev"] = 0.0
        return {"name": name, "branch_ids": ids, "covers": cv, "menus": ms,
                "total_cogs": tc, "total_rev": tr,
                "cph_total": (tc / cv) if cv else 0.0,
                "food_pct": (tc / tr * 100) if tr else 0.0,
                "by_sku": {m["sku_id"]: m for m in ms}}

    all_ids = [bid for bid, _ in masters]
    overview = scope("ภาพรวมทุกสาขา", all_ids)
    branches = [scope(nm, [bid]) for bid, nm in masters]
    return overview, branches


def _cost_head_deviation(scope, overview):
    """Per-menu contribution to (branch cph − overview cph), most-positive / most-negative."""
    ov = overview["by_sku"]
    union = set(scope["by_sku"]) | set(ov)
    items = []
    for sid in union:
        b = scope["by_sku"].get(sid); o = ov.get(sid)
        src = b or o
        b_cph = b["cph"] if b else 0.0
        o_cph = o["cph"] if o else 0.0
        items.append({"code": src["code"], "desc": src["desc"],
                      "prem": _ch_is_premium(src["desc"]),
                      "b_cph": b_cph, "o_cph": o_cph, "d": b_cph - o_cph})
    items.sort(key=lambda x: x["d"])
    lows = [x for x in items if x["d"] < 0][:8]
    highs = [x for x in items if x["d"] > 0][-8:][::-1]
    return highs, lows


def _load_top60_gen():
    """Lazy-import the standalone report generator (presentation only: build_html /
    write_xlsx). Kept out of module import so the JSON tab endpoint never depends on it."""
    import importlib, sys as _sys, os as _os2
    bd = _os2.path.dirname(_os2.path.abspath(__file__))
    if bd not in _sys.path:
        _sys.path.insert(0, bd)
    return importlib.import_module("gen_top60_cost_per_head_report")


_CH_TH_MONTHS = ["", "มกราคม", "กุมภาพันธ์", "มีนาคม", "เมษายน", "พฤษภาคม", "มิถุนายน",
                 "กรกฎาคม", "สิงหาคม", "กันยายน", "ตุลาคม", "พฤศจิกายน", "ธันวาคม"]


def _ch_gen_dt():
    import datetime as _dt
    d = _dt.date.today()
    return f"{d.day} {_CH_TH_MONTHS[d.month]} {d.year + 543}"


def _ch_resolve_month(con, month):
    """Default to the latest month with active-branch covers, EXCLUDING the current
    (in-progress) calendar month.

    Not plain MAX(period_month): now that sales_month_summary is backfilled for
    live-synced months, a handful of sales on the 1st create a summary row for the
    brand-new month — defaulting to it would open the report on a near-empty
    sliver. Exclude the current Thai calendar month (UTC+7) so the report opens on
    the newest month with a meaningful full picture. Scope-agnostic."""
    if month:
        return month
    # Current calendar month in Thai time (POS local); '+7 hours' aligns UTC 'now'.
    cur_ym = con.execute("SELECT strftime('%Y-%m','now','+7 hours')").fetchone()[0]
    r = con.execute(
        "SELECT MAX(s.period_month) FROM sales_month_summary s "
        "JOIN branches b ON b.branch_id = s.branch_id "
        "WHERE b.active=1 AND s.customer_count > 0 AND s.period_month < ?",
        (cur_ym,)).fetchone()
    if r and r[0]:
        return r[0]
    r2 = con.execute("SELECT MAX(period_month) FROM sales_sku_monthly "
                     "WHERE period_month < ?", (cur_ym,)).fetchone()
    if r2 and r2[0]:
        return r2[0]
    # Fallback: nothing before the current month → latest available anywhere.
    r3 = con.execute("SELECT MAX(period_month) FROM sales_sku_monthly").fetchone()
    return r3[0] if r3 else None


# ---------------------------------------------------------------------------
# 🧪 Menu Impact (วิเคราะห์วัตถุดิบ) — with/without per-head analysis of one menu
# SKU or ingredient group over a chosen date range. Powered by the pre-aggregated
# menu_fact_day / menu_fact_total tables (built from live MSSQL bill-level data
# by backend/build_menu_fact.py — run it after each sync to keep coverage fresh).
# Stratified by branch x day so branch mix / calendar mix can't fake the effect.
# ---------------------------------------------------------------------------
_MI_SCOPE_LABEL = {"master": "Master (เจ้าของเอง)", "franchise": "แฟรนไชส์", "all": "ทั้งหมด"}


def _mi_branch_ids(con, scope: str):
    if scope in ("master", "franchise"):
        return [r[0] for r in con.execute(
            "SELECT branch_id FROM branches WHERE branch_type=?", (scope,))]
    return [r[0] for r in con.execute("SELECT branch_id FROM branches")]


def _mi_ready(con) -> bool:
    return bool(con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='menu_fact_total'").fetchone())


@app.get("/api/reports/menu-impact/entities")
@ttl_cache
def menu_impact_entities():
    """Selector list for the tab: ingredient groups + individual menu SKUs that
    actually sold inside the fact coverage, plus coverage/build info."""
    with get_db() as con:
        if not _mi_ready(con):
            return {"ready": False, "groups": [], "skus": [], "coverage": None,
                    "hint": "ยังไม่มีตารางข้อมูล — รัน python backend/build_menu_fact.py ก่อน"}
        lo, hi = con.execute("SELECT MIN(day), MAX(day) FROM menu_fact_total").fetchone()
        state = dict(con.execute("SELECT k, v FROM menu_fact_state").fetchall())
        qty = dict(con.execute("SELECT ent, SUM(qty) FROM menu_fact_day GROUP BY ent").fetchall())
        groups, skus = [], []
        for ent, label, kind, unit_cost, members in con.execute(
                "SELECT ent, label, kind, unit_cost, members FROM menu_fact_meta"):
            q = qty.get(ent) or 0
            if q <= 0:
                continue
            row = {"ent": ent, "label": label, "cost": round(unit_cost or 0, 2),
                   "qty": round(q)}
            if kind == "group":
                row["n_members"] = len([m for m in (members or "").split(",") if m])
                groups.append(row)
            else:
                skus.append(row)
        skus.sort(key=lambda r: -r["qty"])
        return {"ready": True, "coverage": [lo, hi], "last_build": state.get("last_build"),
                "hc_cost": float(state.get("hc_cost") or 30), "groups": groups, "skus": skus[:400]}


_MI_TIER_ORDER = ("350", "590", "649", "689", "799", "999", "promo", "child")


@app.get("/api/reports/menu-impact")
@ttl_cache
def menu_impact(ent: str = Query(..., description="s:<sku_id> หรือ g:<slug>"),
                date_from: Optional[str] = Query(None), date_to: Optional[str] = Query(None),
                scope: str = Query("all", description="master | franchise | all"),
                tier: str = Query("all", description="all | 350|590|649|689|799|999|promo|child")):
    """เลือกวัตถุดิบ/เมนู + ช่วงเวลา (+Tier ราคาบุฟเฟ่ต์) → เทียบบิลที่มี vs ไม่มี
    (ต่อหัว, คุมสาขา×วัน×tier)."""
    if scope not in _MI_SCOPE_LABEL:
        scope = "all"
    with get_db() as con:
        if not _mi_ready(con):
            return {"ready": False, "hint": "รัน python backend/build_menu_fact.py ก่อนใช้งานแท็บนี้"}
        # tier column arrived in fact-schema v2 — degrade gracefully on a v1 DB
        has_tier = bool(con.execute(
            "SELECT 1 FROM pragma_table_info('menu_fact_total') WHERE name='tier'").fetchone())
        if tier != "all" and (tier not in _MI_TIER_ORDER or not has_tier):
            tier = "all"
        lo, hi = con.execute("SELECT MIN(day), MAX(day) FROM menu_fact_total").fetchone()
        if not lo:
            return {"ready": False, "hint": "ตารางข้อมูลว่าง — รัน build_menu_fact.py"}
        date_from = date_from or lo
        date_to = date_to or hi
        meta = con.execute("SELECT label, kind, unit_cost, members FROM menu_fact_meta WHERE ent=?",
                           (ent,)).fetchone()
        if not meta:
            return {"ready": True, "error": f"ไม่รู้จัก entity {ent}"}
        bids = _mi_branch_ids(con, scope)
        ph = ",".join("?" * len(bids))
        tier_col = "f.tier" if has_tier else "''"
        tier_col_t = "tier" if has_tier else "''"
        join_tier = "AND t.tier = f.tier" if has_tier else ""
        tier_where_f = "AND f.tier = ?" if tier != "all" else ""
        tier_where_t = "AND tier = ?" if tier != "all" else ""
        tp1 = [tier] if tier != "all" else []
        f = con.execute(
            f"""SELECT f.branch_id, f.day, {tier_col}, f.bills, f.pax, f.qty, f.ent_cogs, f.ent_hc,
                       f.cogs, f.rev, f.hc,
                       t.bills, t.pax, t.cogs, t.rev, t.hc
                FROM menu_fact_day f JOIN menu_fact_total t
                  ON t.branch_id = f.branch_id AND t.day = f.day {join_tier}
                WHERE f.ent = ? AND f.day BETWEEN ? AND ? AND f.branch_id IN ({ph}) {tier_where_f}""",
            [ent, date_from, date_to] + bids + tp1).fetchall()
        tot = con.execute(
            f"""SELECT branch_id, day, {tier_col_t}, bills, pax, cogs, rev, hc FROM menu_fact_total
                WHERE day BETWEEN ? AND ? AND branch_id IN ({ph}) {tier_where_t}""",
            [date_from, date_to] + bids + tp1).fetchall()
        bname = dict(con.execute("SELECT branch_id, name FROM branches").fetchall())

    T = {"bills": 0, "pax": 0.0, "cogs": 0.0, "rev": 0.0, "hc": 0.0}
    tot_tier = {}
    for _b, _d, tr_, nb, px, cg, rv, hc in tot:
        T["bills"] += nb; T["pax"] += px; T["cogs"] += cg; T["rev"] += rv; T["hc"] += hc
        tt = tot_tier.setdefault(tr_, {"bills": 0, "pax": 0.0})
        tt["bills"] += nb; tt["pax"] += px
    if T["pax"] <= 0:
        return {"ready": True, "error": "ไม่มีข้อมูลบิลบุฟเฟ่ต์ในช่วง/ขอบเขตที่เลือก",
                "coverage": [lo, hi]}

    # with-set summary + stratified with/without (stratum = branch x day)
    W = {"bills": 0, "pax": 0.0, "qty": 0.0, "ecogs": 0.0}
    wsum = {k: 0.0 for k in ("cogs_w", "cogs_o", "oth_w", "oth_o", "hc_w", "hc_o",
                             "rev_w", "rev_o", "gp_w", "gp_o")}
    wt = 0.0
    by_branch, by_month, by_day, by_tier = {}, {}, {}, {}
    for r in f:
        (bid, day, tr_, nb, px, q, ec, eh, cg, rv, hc, tb, tp, tc, trv, th) = r
        W["bills"] += nb; W["pax"] += px; W["qty"] += q; W["ecogs"] += ec
        bb = by_branch.setdefault(bid, {"bills": 0, "pax": 0.0, "qty": 0.0, "ecogs": 0.0,
                                        "d_oth": 0.0, "w": 0.0})
        bb["bills"] += nb; bb["pax"] += px; bb["qty"] += q; bb["ecogs"] += ec
        tb_ = by_tier.setdefault(tr_, {"bills": 0, "pax": 0.0, "qty": 0.0, "ecogs": 0.0,
                                       "d_oth": 0.0, "d_gp": 0.0, "w": 0.0})
        tb_["bills"] += nb; tb_["pax"] += px; tb_["qty"] += q; tb_["ecogs"] += ec
        for key, dct in ((day[:7], by_month), (day, by_day)):
            m = dct.setdefault(key, {"bills": 0, "pax": 0.0, "qty": 0.0, "ecogs": 0.0})
            m["bills"] += nb; m["pax"] += px; m["qty"] += q; m["ecogs"] += ec
        opx, onb = tp - px, tb - nb
        if px <= 0 or opx <= 0 or onb <= 0:
            continue
        w = px * opx / (px + opx)
        wt += w
        oc, orv, ohc = tc - cg, trv - rv, th - hc
        wsum["cogs_w"] += w * cg / px;             wsum["cogs_o"] += w * oc / opx
        wsum["oth_w"] += w * (cg - ec) / px;       wsum["oth_o"] += w * oc / opx
        wsum["hc_w"] += w * (hc - eh) / px;        wsum["hc_o"] += w * ohc / opx
        wsum["rev_w"] += w * rv / px;              wsum["rev_o"] += w * orv / opx
        wsum["gp_w"] += w * (rv - cg) / px;        wsum["gp_o"] += w * (orv - oc) / opx
        bb["d_oth"] += w * ((cg - ec) / px - oc / opx); bb["w"] += w
        tb_["d_oth"] += w * ((cg - ec) / px - oc / opx)
        tb_["d_gp"] += w * ((rv - cg) / px - (orv - oc) / opx)
        tb_["w"] += w

    if wt <= 0:
        return {"ready": True, "error": "ข้อมูลไม่พอสำหรับเทียบมี/ไม่มี (ทุกวันมีหรือไม่มีทั้งหมด)",
                "coverage": [lo, hi]}
    for k in wsum:
        wsum[k] /= wt
    # mix-adjusted: high-cost consumption expected if it scaled with total "other" appetite
    exp_hc = wsum["hc_o"] * (wsum["oth_w"] / wsum["oth_o"]) if wsum["oth_o"] > 0 else None
    label, kind, unit_cost, members = meta
    trend_m = [{"ym": k, "pen": v["bills"], "qty": v["qty"], "ecogs": v["ecogs"]}
               for k, v in sorted(by_month.items())]
    tot_m, tot_d = {}, {}
    for b, d, tr_, nb, px, cg, rv, hc in tot:
        for key, dct in ((d[:7], tot_m), (d, tot_d)):
            m = dct.setdefault(key, {"bills": 0, "pax": 0.0})
            m["bills"] += nb; m["pax"] += px
    for row in trend_m:
        tm = tot_m.get(row["ym"], {"bills": 0, "pax": 0.0})
        row["pen"] = round(row["pen"] / tm["bills"], 4) if tm["bills"] else None
        row["qty_head"] = round(row["qty"] / tm["pax"], 3) if tm["pax"] else None
        row["ecogs_head"] = round(row["ecogs"] / tm["pax"], 2) if tm["pax"] else None
        row["qty"] = round(row["qty"]); row["ecogs"] = round(row["ecogs"])
    trend_d = []
    for k in sorted(tot_d):
        v = by_day.get(k, {"bills": 0, "qty": 0.0, "ecogs": 0.0})
        td = tot_d[k]
        trend_d.append({"day": k,
                        "pen": round(v["bills"] / td["bills"], 4) if td["bills"] else None,
                        "qty_head": round(v["qty"] / td["pax"], 3) if td["pax"] else None})
    branches_out = sorted((
        {"branch_id": b, "name": bname.get(b, str(b)), "bills": v["bills"],
         "qty": round(v["qty"]), "ecogs": round(v["ecogs"]),
         "qty_head_takers": round(v["qty"] / v["pax"], 2) if v["pax"] else None,
         "d_other_cogs": round(v["d_oth"] / v["w"], 2) if v["w"] > 0 else None}
        for b, v in by_branch.items()), key=lambda r: -(r["ecogs"] or 0))
    rd = lambda x, nd=2: round(x, nd)
    # per-tier breakdown (only meaningful when looking across all tiers)
    tiers_out = []
    if has_tier:
        for tr_ in _MI_TIER_ORDER:
            v = by_tier.get(tr_)
            tt = tot_tier.get(tr_)
            if not v or not tt or not tt["bills"]:
                continue
            tiers_out.append({
                "tier": tr_, "bills": tt["bills"], "bills_with": v["bills"],
                "pen": rd(v["bills"] / tt["bills"], 4),
                "qty": rd(v["qty"], 0),
                "qty_head_takers": rd(v["qty"] / v["pax"]) if v["pax"] else None,
                "ecogs_head_takers": rd(v["ecogs"] / v["pax"]) if v["pax"] else None,
                "ecogs": rd(v["ecogs"], 0),
                "d_other_cogs": rd(v["d_oth"] / v["w"]) if v["w"] > 0 else None,
                "d_gp": rd(v["d_gp"] / v["w"]) if v["w"] > 0 else None,
            })
    return {
        "ready": True, "ent": ent, "label": label, "kind": kind, "has_tier": has_tier,
        "tier": tier, "tiers": tiers_out,
        "unit_cost": rd(unit_cost or 0), "scope": scope, "scope_label": _MI_SCOPE_LABEL[scope],
        "members": [m for m in (members or "").split(",") if m] if kind == "group" else None,
        "coverage": [lo, hi], "date_from": date_from, "date_to": date_to,
        "totals": {"bills": T["bills"], "pax": rd(T["pax"], 0), "cogs_head": rd(T["cogs"] / T["pax"]),
                   "rev_head": rd(T["rev"] / T["pax"]), "hc_head": rd(T["hc"] / T["pax"])},
        "summary": {
            "pen_bills": rd(W["bills"] / T["bills"], 4), "bills_with": W["bills"],
            "qty": rd(W["qty"], 0), "ecogs": rd(W["ecogs"], 0),
            "ecogs_share": rd(W["ecogs"] / T["cogs"], 4) if T["cogs"] else None,
            "qty_head_takers": rd(W["qty"] / W["pax"]) if W["pax"] else None,
            "ecogs_head_takers": rd(W["ecogs"] / W["pax"]) if W["pax"] else None,
            "ecogs_head_all": rd(W["ecogs"] / T["pax"]),
        },
        "compare": {   # per-head, stratified branch x day, harmonic-pax weights
            "cogs": {"with": rd(wsum["cogs_w"]), "without": rd(wsum["cogs_o"]),
                     "diff": rd(wsum["cogs_w"] - wsum["cogs_o"])},
            "other_cogs": {"with": rd(wsum["oth_w"]), "without": rd(wsum["oth_o"]),
                           "diff": rd(wsum["oth_w"] - wsum["oth_o"])},
            "hc_other": {"with": rd(wsum["hc_w"]), "without": rd(wsum["hc_o"]),
                         "diff": rd(wsum["hc_w"] - wsum["hc_o"])},
            "rev": {"with": rd(wsum["rev_w"]), "without": rd(wsum["rev_o"]),
                    "diff": rd(wsum["rev_w"] - wsum["rev_o"])},
            "gp": {"with": rd(wsum["gp_w"]), "without": rd(wsum["gp_o"]),
                   "diff": rd(wsum["gp_w"] - wsum["gp_o"])},
            "hc_expected_prop": rd(exp_hc) if exp_hc is not None else None,
            "hc_rel_saving": rd(wsum["hc_w"] - exp_hc) if exp_hc is not None else None,
        },
        "trend_month": trend_m, "trend_day": trend_d, "branches": branches_out,
    }


# ---------------------------------------------------------------------------
# 🏷️ Price-tier analysis (วิเคราะห์ Tier ราคาขายบุฟเฟ่ต์) — section of the 🧪 tab.
# Reads the tier-dimensioned menu_fact_total / menu_fact_day fact tables
# (schema v2, build_menu_fact.py): every buffet bill carries tier = canonical
# adult-buffet price ex-VAT ('350'…'999'), 'promo' (non-canonical price) or
# 'child' (no adult cover). Compares the chosen range A against the SAME-LENGTH
# window right before it (or the first vs second half of the range when there
# is not enough history before it).
# ---------------------------------------------------------------------------
_PT_TOP_N = 30          # "30 เมนูหลักในต้นทุน"
_PT_ORDER = ("999", "799", "689", "649", "590", "350", "promo", "child")
_PT_LABEL = {"promo": "โปรฯ/ราคาอื่น", "child": "เด็กล้วน/อื่นๆ"}


def _pt_label(t):
    return _PT_LABEL.get(t, f"฿{int(t):,}" if str(t).isdigit() else str(t))


def _pt_sum_tiers(con, ph, bids, d_from, d_to):
    """{tier: {bills,pax,cogs,rev,hc}} over a window (v2 fact tables)."""
    out = {}
    for t, nb, px, cg, rv, hc in con.execute(
            f"""SELECT tier, SUM(bills), SUM(pax), SUM(cogs), SUM(rev), SUM(hc)
                FROM menu_fact_total
                WHERE day BETWEEN ? AND ? AND branch_id IN ({ph}) GROUP BY tier""",
            [d_from, d_to] + bids):
        out[str(t or "promo")] = {"bills": nb or 0, "pax": px or 0.0, "cogs": cg or 0.0,
                                  "rev": rv or 0.0, "hc": hc or 0.0}
    return out


def _pt_metrics(v):
    px = v["pax"] or 0.0
    if px <= 0:
        return {"bills": v["bills"], "pax": 0, "rev_head": None, "cogs_head": None,
                "hc_head": None, "gp_head": None, "food_pct": None, "pax_bill": None}
    return {"bills": v["bills"], "pax": round(px),
            "rev_head": round(v["rev"] / px, 2), "cogs_head": round(v["cogs"] / px, 2),
            "hc_head": round(v["hc"] / px, 2), "gp_head": round((v["rev"] - v["cogs"]) / px, 2),
            "food_pct": round(v["cogs"] / v["rev"] * 100, 1) if v["rev"] else None,
            "pax_bill": round(px / v["bills"], 2) if v["bills"] else None}


@app.get("/api/reports/price-tier")
@ttl_cache
def price_tier_report(date_from: Optional[str] = Query(None), date_to: Optional[str] = Query(None),
                      scope: str = Query("all", description="master | franchise | all")):
    """วิเคราะห์ Tier ราคาขายบุฟเฟ่ต์: จำนวนคนทาน, Top-30 เมนูต้นทุน, เทรนด์ และข้อเสนอแนะ
    — ช่วงที่เลือก (A) เทียบช่วงก่อนหน้าความยาวเท่ากัน (B) อัตโนมัติ."""
    from datetime import date, timedelta
    if scope not in _MI_SCOPE_LABEL:
        scope = "all"
    with get_db() as con:
        has_tier = _mi_ready(con) and bool(con.execute(
            "SELECT 1 FROM pragma_table_info('menu_fact_total') WHERE name='tier'").fetchone())
        if not has_tier:
            return {"ready": False,
                    "hint": "ยังไม่มีข้อมูล Tier (fact schema v2) — รัน python backend/build_menu_fact.py ก่อน"}
        lo, hi = con.execute("SELECT MIN(day), MAX(day) FROM menu_fact_total").fetchone()
        if not lo:
            return {"ready": False, "hint": "ตารางข้อมูลว่าง — รัน build_menu_fact.py"}
        a_from = max(date_from or lo, lo)
        a_to = min(date_to or hi, hi)
        if a_from > a_to:
            return {"ready": True, "error": "ช่วงวันที่ไม่ถูกต้อง", "coverage": [lo, hi]}
        dA_from, dA_to = date.fromisoformat(a_from), date.fromisoformat(a_to)
        span = (dA_to - dA_from).days + 1
        b_to_d = dA_from - timedelta(days=1)
        b_from_d = b_to_d - timedelta(days=span - 1)
        if b_from_d >= date.fromisoformat(lo) and span >= 2:
            mode = "prev"
        else:  # not enough history before A → compare halves of A itself
            mode = "split"
            mid = dA_from + timedelta(days=span // 2)
            b_from_d, b_to_d = dA_from, mid - timedelta(days=1)
            dA_from = mid
            a_from = dA_from.isoformat()
        b_from, b_to = b_from_d.isoformat(), b_to_d.isoformat()

        bids = _mi_branch_ids(con, scope)
        ph = ",".join("?" * len(bids))
        FA = _pt_sum_tiers(con, ph, bids, a_from, a_to)
        FB = _pt_sum_tiers(con, ph, bids, b_from, b_to)
        pax_a = sum(v["pax"] for v in FA.values())
        pax_b = sum(v["pax"] for v in FB.values())
        if pax_a <= 0:
            return {"ready": True, "error": "ไม่มีข้อมูลบิลบุฟเฟ่ต์ในช่วง/ขอบเขตที่เลือก",
                    "coverage": [lo, hi]}
        tier_keys = [t for t in _PT_ORDER
                     if FA.get(t, {}).get("pax", 0) > 0 or FB.get(t, {}).get("pax", 0) > 0]

        # per costed menu SKU x tier for both windows (groups g:* excluded — they
        # would double-count their member SKUs in the Top-30 ranking)
        def ent_rows(d1, d2):
            out = {}
            for ent, t, q, ec in con.execute(
                    f"""SELECT ent, tier, SUM(qty), SUM(ent_cogs) FROM menu_fact_day
                        WHERE day BETWEEN ? AND ? AND branch_id IN ({ph}) AND ent LIKE 's:%'
                        GROUP BY ent, tier""",
                    [d1, d2] + bids):
                m = out.setdefault(ent, {}).setdefault(str(t or "promo"), {"qty": 0.0, "ecogs": 0.0})
                m["qty"] += q or 0.0
                m["ecogs"] += ec or 0.0
            return out
        EA, EB = ent_rows(a_from, a_to), ent_rows(b_from, b_to)
        meta = {ent: (label, uc) for ent, label, uc in con.execute(
            "SELECT ent, label, unit_cost FROM menu_fact_meta WHERE kind='sku'")}

        # monthly trend x tier over the FULL coverage (scope-filtered)
        trend = {}
        for ym, t, px, cg, rv in con.execute(
                f"""SELECT substr(day,1,7), tier, SUM(pax), SUM(cogs), SUM(rev)
                    FROM menu_fact_total WHERE branch_id IN ({ph})
                    GROUP BY substr(day,1,7), tier""", bids):
            m = trend.setdefault(ym, {}).setdefault(str(t or "promo"),
                                                    {"pax": 0.0, "cogs": 0.0, "rev": 0.0})
            m["pax"] += px or 0.0; m["cogs"] += cg or 0.0; m["rev"] += rv or 0.0

    rd = lambda x, nd=2: round(x, nd) if x is not None else None
    pct = lambda c, p: round((c - p) / p * 100, 1) if p else None

    tiers = []
    for t in tier_keys:
        va = FA.get(t, {"bills": 0, "pax": 0.0, "cogs": 0.0, "rev": 0.0, "hc": 0.0})
        vb = FB.get(t, {"bills": 0, "pax": 0.0, "cogs": 0.0, "rev": 0.0, "hc": 0.0})
        A, B = _pt_metrics(va), _pt_metrics(vb)
        A["share"] = rd(va["pax"] / pax_a * 100, 1) if pax_a else None
        B["share"] = rd(vb["pax"] / pax_b * 100, 1) if pax_b else None
        tiers.append({
            "tier": t, "label": _pt_label(t), "a": A, "b": B,
            "d_pax_pct": pct(va["pax"], vb["pax"]),
            "d_share_pp": rd((A["share"] or 0) - (B["share"] or 0), 1) if A["share"] is not None and B["share"] is not None else None,
            "d_cogs_head": rd(A["cogs_head"] - B["cogs_head"]) if A["cogs_head"] is not None and B["cogs_head"] is not None else None,
            "d_hc_head": rd(A["hc_head"] - B["hc_head"]) if A["hc_head"] is not None and B["hc_head"] is not None else None,
        })

    # ── Top-30 menus by COGS in A: per-tier consumption/head + Δ vs B ──
    rank = sorted(EA.items(), key=lambda kv: -sum(m["ecogs"] for m in kv[1].values()))[:_PT_TOP_N]
    paxA = {t: FA.get(t, {}).get("pax", 0.0) for t in tier_keys}
    paxB = {t: FB.get(t, {}).get("pax", 0.0) for t in tier_keys}
    top30 = []
    for ent, per in rank:
        label, uc = meta.get(ent, (ent, 0.0))
        qa = sum(m["qty"] for m in per.values()); ca = sum(m["ecogs"] for m in per.values())
        perB = EB.get(ent, {})
        qb = sum(m["qty"] for m in perB.values())
        row = {"ent": ent, "label": label, "unit_cost": rd(uc or 0),
               "qty": rd(qa, 0), "ecogs": rd(ca, 0),
               "qty_head": rd(qa / pax_a, 3) if pax_a else None,
               "cogs_head": rd(ca / pax_a) if pax_a else None,
               "d_qty_head_pct": pct(qa / pax_a if pax_a else 0, qb / pax_b if pax_b else 0),
               "tiers": {}}
        for t in tier_keys:
            qha = per.get(t, {}).get("qty", 0.0) / paxA[t] if paxA[t] else None
            qhb = perB.get(t, {}).get("qty", 0.0) / paxB[t] if paxB[t] else None
            row["tiers"][t] = {"qty_head": rd(qha, 3) if qha is not None else None,
                               "d_pct": pct(qha or 0, qhb) if qhb else None}
        top30.append(row)

    # ── monthly trend payload ──
    trend_month = []
    for ym in sorted(trend):
        mt = trend[ym]
        tot_px = sum(v["pax"] for v in mt.values())
        trend_month.append({"ym": ym, "tiers": {
            t: {"pax": rd(v["pax"], 0),
                "share": rd(v["pax"] / tot_px * 100, 1) if tot_px else None,
                "cogs_head": rd(v["cogs"] / v["pax"]) if v["pax"] else None,
                "rev_head": rd(v["rev"] / v["pax"]) if v["pax"] else None}
            for t, v in mt.items()}})

    # ── blended per-head + mix effect ──
    cogs_a = sum(v["cogs"] for v in FA.values()); cogs_b = sum(v["cogs"] for v in FB.values())
    rev_a = sum(v["rev"] for v in FA.values()); rev_b = sum(v["rev"] for v in FB.values())
    blend = {"pax_a": rd(pax_a, 0), "pax_b": rd(pax_b, 0), "d_pax_pct": pct(pax_a, pax_b),
             "rev_head_a": rd(rev_a / pax_a) if pax_a else None,
             "rev_head_b": rd(rev_b / pax_b) if pax_b else None,
             "cogs_head_a": rd(cogs_a / pax_a) if pax_a else None,
             "cogs_head_b": rd(cogs_b / pax_b) if pax_b else None,
             "food_pct_a": rd(cogs_a / rev_a * 100, 1) if rev_a else None,
             "food_pct_b": rd(cogs_b / rev_b * 100, 1) if rev_b else None}
    # mix effect on rev/head: A's tier mix priced at B's per-tier rev/head vs B blended
    mix_effect = None
    if pax_a and pax_b and rev_b:
        at_b_prices = sum((FA[t]["pax"] / pax_a) * (FB[t]["rev"] / FB[t]["pax"])
                          for t in FA if FB.get(t, {}).get("pax", 0) > 0)
        mix_effect = rd(at_b_prices - rev_b / pax_b)

    # ── insights + actions (Thai) ──
    period_word = "ช่วงก่อนหน้า" if mode == "prev" else "ครึ่งแรกของช่วงเดียวกัน"
    fb = lambda x: f"฿{x:,.2f}" if x is not None else "-"
    insights, actions = [], []
    if blend["d_pax_pct"] is not None:
        up = blend["d_pax_pct"] >= 0
        insights.append(f"จำนวนคนทานรวม {blend['pax_a']:,.0f} คน "
                        f"{'เพิ่มขึ้น' if up else 'ลดลง'} {abs(blend['d_pax_pct'])}% เทียบ{period_word} "
                        f"({blend['pax_b']:,.0f} คน)")
    # tier-level pax insights: canonical price tiers only (promo/child mix with
    # price-list changes and would mislead the "กู้ยอด" framing)
    real_tiers = [x for x in tiers if str(x["tier"]).isdigit() and x["d_pax_pct"] is not None]
    if real_tiers:
        gain = max(real_tiers, key=lambda x: x["d_pax_pct"])
        lose = min(real_tiers, key=lambda x: x["d_pax_pct"])
        if gain["d_pax_pct"] > 0:
            insights.append(f"Tier {gain['label']} คนทานเพิ่มแรงสุด +{gain['d_pax_pct']}% "
                            f"(สัดส่วน {gain['b']['share']}% → {gain['a']['share']}%)")
        if lose["d_pax_pct"] < 0:
            insights.append(f"Tier {lose['label']} คนทานลดแรงสุด {lose['d_pax_pct']}% "
                            f"(สัดส่วน {lose['b']['share']}% → {lose['a']['share']}%)")
            if abs(lose["d_pax_pct"]) >= 5:
                actions.append({"title": f"กู้ยอดลูกค้า Tier {lose['label']}",
                                "detail": f"คนทานลดลง {abs(lose['d_pax_pct'])}% เทียบ{period_word} "
                                          f"(เหลือ {lose['a']['pax']:,} คน) — ตรวจสาขาหลักที่ขาย tier นี้, "
                                          f"จัดโปรวันธรรมดา/ช่วง off-peak เฉพาะ tier, และเช็คว่าราคา/คู่แข่งในพื้นที่เปลี่ยนหรือไม่"})
    promo = next((x for x in tiers if x["tier"] == "promo"), None)
    if promo and promo["d_share_pp"] is not None and abs(promo["d_share_pp"]) >= 2:
        insights.append(f"สัดส่วนบิลราคาโปรโมชัน/นอกราคามาตรฐาน {promo['b']['share']}% → {promo['a']['share']}% "
                        f"({'+' if promo['d_share_pp'] > 0 else ''}{promo['d_share_pp']} pp)")
    if mix_effect is not None and abs(mix_effect) >= 1:
        if mix_effect < 0:
            insights.append(f"ลูกค้าย้ายไป tier ถูกลง — ผลจาก mix อย่างเดียวกดรายได้/หัวลง {fb(-mix_effect)}")
            actions.append({"title": "ดัน upsell ไป tier ที่สูงขึ้น",
                            "detail": f"สัดส่วนลูกค้าเลื่อนไป tier ราคาต่ำ ทำให้รายได้/หัวหายไป {fb(-mix_effect)} "
                                      f"ทั้งที่ราคาแต่ละ tier ไม่เปลี่ยน — เทรนพนักงานเชียร์อัปเกรด (เมนูเด่นเฉพาะ tier บน, "
                                      f"ป้ายเทียบสิทธิ์ที่โต๊ะ), ตั้งเป้า % อัปเกรดรายสาขา"})
        else:
            insights.append(f"ลูกค้าย้ายขึ้น tier แพงขึ้น — ผลจาก mix อย่างเดียวเพิ่มรายได้/หัว {fb(mix_effect)}")
    for x in real_tiers:
        if x["d_cogs_head"] is not None and x["d_cogs_head"] >= 3:
            drivers = sorted((r for r in top30 if r["tiers"].get(x["tier"], {}).get("d_pct")),
                             key=lambda r: -(r["tiers"][x["tier"]]["d_pct"] or 0))[:3]
            dtxt = ", ".join(f"{r['label']} (+{r['tiers'][x['tier']]['d_pct']}%)" for r in drivers
                             if (r["tiers"][x["tier"]]["d_pct"] or 0) > 0)
            insights.append(f"ต้นทุน/หัว tier {x['label']} เพิ่ม {fb(x['d_cogs_head'])} "
                            f"({fb(x['b']['cogs_head'])} → {fb(x['a']['cogs_head'])})"
                            + (f" — ตัวดัน: {dtxt}" if dtxt else ""))
            actions.append({"title": f"คุมต้นทุน/หัว tier {x['label']}",
                            "detail": f"ต้นทุนอาหาร/หัวเพิ่ม {fb(x['d_cogs_head'])} เทียบ{period_word}"
                                      + (f" ตัวดันหลัก: {dtxt} — คุมพอร์ชัน/ความถี่เติมของเมนูเหล่านี้, "
                                         f"เช็คของเสียครัว และทบทวนต้นทุนวัตถุดิบกับซัพพลายเออร์" if dtxt else
                                         " — ไล่ดูเมนู Top-30 ของ tier นี้ในตารางด้านล่าง")})
    worst_fp = max((x for x in real_tiers if x["a"]["food_pct"]), key=lambda x: x["a"]["food_pct"], default=None)
    if worst_fp and worst_fp["a"]["food_pct"] >= 40:
        insights.append(f"Tier {worst_fp['label']} food cost สูงสุด {worst_fp['a']['food_pct']}% "
                        f"(GP/หัว {fb(worst_fp['a']['gp_head'])})")
        actions.append({"title": f"ทบทวนความคุ้มของ tier {worst_fp['label']}",
                        "detail": f"food cost {worst_fp['a']['food_pct']}% ของรายได้ (GP เหลือ {fb(worst_fp['a']['gp_head'])}/หัว) — "
                                  f"พิจารณาปรับไลน์เมนูเฉพาะ tier (สลับเมนูต้นทุนสูงเป็นเมนูภาพลักษณ์ดีแต่ต้นทุนต่ำกว่า), "
                                  f"หรือทดลองขึ้นราคาแบบ A/B บางสาขา"})
    hc_up = [x for x in real_tiers if x["d_hc_head"] is not None and x["d_hc_head"] >= 2]
    if hc_up:
        t0 = max(hc_up, key=lambda x: x["d_hc_head"])
        insights.append(f"การทานเมนูแพง (ต้นทุน ≥฿30) tier {t0['label']} เพิ่ม {fb(t0['d_hc_head'])}/หัว")
        actions.append({"title": f"เบรกเมนูต้นทุนสูงใน tier {t0['label']}",
                        "detail": f"เมนูแพง/หัวเพิ่ม {fb(t0['d_hc_head'])} — ใช้เทคนิค menu placement "
                                  f"(วางเมนูข้าว/ของดองไว้ต้น ๆ), ลดขนาดพอร์ชันต่อจาน (เติมได้แต่ชะลอ), "
                                  f"และตรวจว่าสาขาไหนปล่อยเมนูแพงเกินเพื่อน"})
    rising = [r for r in top30 if (r["d_qty_head_pct"] or 0) >= 10 and (r["cogs_head"] or 0) >= 1]
    if rising:
        names = ", ".join(f"{r['label']} (+{r['d_qty_head_pct']}%)" for r in rising[:4])
        insights.append(f"เมนูต้นทุนหลักที่ถูกทานเพิ่มชัดเจน: {names}")
        actions.append({"title": "จัดการเมนูที่การทานพุ่ง",
                        "detail": f"{names} — ถ้าเป็นเมนูดึงลูกค้า (เช็คจากแท็บวิเคราะห์วัตถุดิบว่า GP/หัวของบิลที่สั่งไม่แย่ลง) "
                                  f"ให้คงไว้แต่ล็อกต้นทุนซื้อ; ถ้าไม่ใช่ ให้ลดความถี่เติม/ขนาดเสิร์ฟ"})
    if not actions:
        actions.append({"title": "โครงสร้าง tier แข็งแรง",
                        "detail": "ไม่พบสัญญาณผิดปกติเกินเกณฑ์ในช่วงที่เลือก — คงกลยุทธ์ปัจจุบัน "
                                  "และติดตามสัดส่วน tier กับต้นทุน/หัวรายเดือนต่อไป"})

    return {"ready": True, "scope": scope, "scope_label": _MI_SCOPE_LABEL[scope],
            "coverage": [lo, hi], "mode": mode,
            "a_range": [a_from, a_to], "b_range": [b_from, b_to],
            "blend": blend, "mix_effect": mix_effect,
            "tiers": tiers, "tier_keys": tier_keys,
            "tier_labels": {t: _pt_label(t) for t in tier_keys},
            "top30": top30, "trend_month": trend_month,
            "insights": insights, "actions": actions}


@app.get("/api/reports/cost-head-top60")
@ttl_cache
def report_cost_head_top60(month: Optional[str] = Query(None),
                           scope: str = Query("master", description="master | franchise | all")):
    """Top-60 menu cost-per-head for one branch scope, one month: combined overview
    + each branch's FULL Top-60 with Δ/head vs the overview, plus a cross-branch
    ranking and headline. scope = master (default) | franchise | all.
    Companion exports: /api/reports/cost-head-top60.print (HTML) | .xlsx."""
    TOPN = 60
    if scope not in _CH_SCOPES:
        scope = "master"
    with get_db() as con:
        months = [r[0] for r in con.execute(
            "SELECT DISTINCT period_month FROM sales_sku_monthly ORDER BY period_month DESC").fetchall()]
        month = _ch_resolve_month(con, month)
        overview, branches = _cost_head_scopes(con, month, scope)
        if not overview:
            return {"month": month, "months": months, "scope": scope,
                    "scope_label": _CH_SCOPE_LABEL.get(scope, scope),
                    "overview": None, "branches": [], "analysis": None}

        def top_rows(sc, variant):
            ov = overview["by_sku"]; cum = 0.0; out = []
            for i, m in enumerate(sc["menus"][:TOPN], 1):
                cum += m["share"]
                row = {"rank": i, "code": m["code"], "desc": m["desc"],
                       "prem": _ch_is_premium(m["desc"]), "qty": m["qty"],
                       "qph": round(m["qph"], 2), "cogs": round(m["cogs"], 2),
                       "cph": round(m["cph"], 2), "share": round(m["share"] * 100, 1),
                       "cum": round(cum * 100, 1)}
                if variant:
                    o = ov.get(m["sku_id"]); o_cph = o["cph"] if o else None
                    d = m["cph"] - (o_cph or 0.0)
                    row["o_cph"] = round(o_cph, 2) if o_cph is not None else None
                    row["d"] = round(d, 2)
                    row["dp"] = round(d / o_cph * 100, 0) if (o_cph and o_cph > 0) else None
                out.append(row)
            return out

        def scope_head(sc, variant):
            h = {"name": sc["name"], "covers": sc["covers"],
                 "cph_total": round(sc["cph_total"], 2), "food_pct": round(sc["food_pct"], 1),
                 "total_cogs": round(sc["total_cogs"], 2), "total_rev": round(sc["total_rev"], 2),
                 "menu_count": len(sc["menus"]), "menus": top_rows(sc, variant)}
            if variant:
                h["branch_id"] = sc["branch_ids"][0]
                h["gap"] = round(sc["cph_total"] - overview["cph_total"], 2)
                highs, lows = _cost_head_deviation(sc, overview)
                pack = lambda x: {"code": x["code"], "desc": x["desc"], "prem": x["prem"],
                                  "b_cph": round(x["b_cph"], 2), "o_cph": round(x["o_cph"], 2),
                                  "d": round(x["d"], 2)}
                h["highs"] = [pack(x) for x in highs[:6]]
                h["lows"] = [pack(x) for x in lows[:6]]
                h["drivers"] = " · ".join(f'{x["desc"][:20]} {x["d"]:+.1f}' for x in highs[:3])
            return h

        ov_out = scope_head(overview, False)
        br_out = [scope_head(b, True) for b in branches]
        ranked = sorted(br_out, key=lambda b: b["cph_total"], reverse=True)
        analysis = {
            "overview_cph": round(overview["cph_total"], 2),
            "ranked": [{"name": b["name"], "covers": b["covers"], "cph_total": b["cph_total"],
                        "gap": b["gap"], "food_pct": b["food_pct"], "total_cogs": b["total_cogs"],
                        "drivers": b["drivers"]} for b in ranked],
        }
        if ranked:
            hot = ranked[0]
            analysis.update({
                "hottest": hot["name"], "hottest_gap": hot["gap"],
                "hottest_covers": hot["covers"],
                "hottest_excess_month": round(hot["gap"] * hot["covers"], 0),
                "hottest_drivers": [{"desc": x["desc"], "d": x["d"]} for x in hot["highs"][:3]],
            })
    return {"month": month, "months": months, "scope": scope,
            "scope_label": _CH_SCOPE_LABEL.get(scope, scope),
            "overview": ov_out, "branches": br_out, "analysis": analysis}


@app.get("/api/reports/cost-head-top60.xlsx")
def report_cost_head_top60_xlsx(month: Optional[str] = Query(None),
                                scope: str = Query("master")):
    """Excel export of the Top-60 cost-per-head report (reuses the generator's write_xlsx,
    fed the live-computed scopes — ties out with the tab)."""
    import io as _io_ch
    from fastapi.responses import StreamingResponse as _SR
    if scope not in _CH_SCOPES:
        scope = "master"
    with get_db() as con:
        month = _ch_resolve_month(con, month)
        overview, branches = _cost_head_scopes(con, month, scope)
    if not overview:
        raise HTTPException(status_code=404, detail="ไม่มีข้อมูลสำหรับเดือนนี้")
    try:
        gen = _load_top60_gen()
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"export module unavailable: {e}")
    buf = _io_ch.BytesIO()
    gen.write_xlsx(buf, month, overview, branches, _ch_gen_dt(),
                   scope_label=_CH_SCOPE_LABEL.get(scope, scope))   # openpyxl save() accepts a file-like
    buf.seek(0)
    return _SR(buf, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
               headers={"Content-Disposition": f"attachment; filename=TOP60_COST_PER_HEAD_{scope}_{month}.xlsx"})


@app.get("/api/reports/cost-head-top60.print")
def report_cost_head_top60_print(month: Optional[str] = Query(None),
                                 scope: str = Query("master")):
    """Full printable HTML report (the standalone generator's output, computed live)."""
    if scope not in _CH_SCOPES:
        scope = "master"
    with get_db() as con:
        month = _ch_resolve_month(con, month)
        overview, branches = _cost_head_scopes(con, month, scope)
    if not overview:
        raise HTTPException(status_code=404, detail="ไม่มีข้อมูลสำหรับเดือนนี้")
    try:
        gen = _load_top60_gen()
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"export module unavailable: {e}")
    html_out = gen.build_html(month, overview, branches, _ch_gen_dt(), "live",
                              scope_label=_CH_SCOPE_LABEL.get(scope, scope))
    return Response(content=html_out, media_type="text/html; charset=utf-8")


@app.get("/api/overall/latest-month-mtd")
def latest_month_mtd(branch: Optional[str] = Query(None), group: Optional[str] = Query(None)):
    """Fair month-to-date comparison for the latest (possibly incomplete) month:
    current month 1→latest-day vs previous month 1→same-day. Returns revenue (ex-VAT),
    customers, discount, and BoM COGS for both periods. COGS via our BoM only (iron rule)."""
    import datetime as _dt, calendar as _cal
    cogs_expr = _unified_cogs_expr("ssm")
    tfilt = ["t.source='Easy'", "b.name!='ALL'"]; tp: dict = {}
    if branch: tfilt.append("b.name=:branch"); tp["branch"] = branch
    _group_filter(tfilt, tp, group)
    twhere = " AND ".join(tfilt)
    dbf = " AND b.name=:branch" if branch else ""
    # fix A2: propagate group filter into the COGS query so it matches the revenue query scope
    _gcond_list2: list = []; _gcond_params2: dict = {}
    _group_filter(_gcond_list2, _gcond_params2, group)
    gcond2 = (" AND " + " AND ".join(_gcond_list2)) if _gcond_list2 else ""
    dbp = ({"branch": branch} if branch else {})
    dbp.update(_gcond_params2)

    with get_db() as con:
        maxday = con.execute("SELECT MAX(date(sold_at)) FROM sales_transaction WHERE source='Easy'").fetchone()[0]
        if not maxday:
            return {"available": False}
        y, m, d = (int(x) for x in maxday.split("-"))
        last_dom = _cal.monthrange(y, m)[1]
        partial = d < last_dom
        cur_s = _dt.date(y, m, 1); cur_e = _dt.date(y, m, d)
        pidx = y * 12 + (m - 1) - 1; py, pm = pidx // 12, pidx % 12 + 1
        prev_s = _dt.date(py, pm, 1)
        prev_e = _dt.date(py, pm, min(d, _cal.monthrange(py, pm)[1]))

        def agg(s, e):
            r = con.execute(
                f"""SELECT ROUND(SUM(t.total_inc_vat)/1.07,2) rev, SUM(t.customer_count) cust,
                    ROUND(SUM(t.discount),2) disc, COUNT(*) bills
                    FROM sales_transaction t JOIN branches b ON b.branch_id=t.branch_id
                    WHERE {twhere} AND date(t.sold_at) BETWEEN :s AND :e""",
                {**tp, "s": s.isoformat(), "e": e.isoformat()}).fetchone()
            co = con.execute(
                f"""SELECT ROUND(SUM({cogs_expr}),2) cogs
                    FROM (SELECT branch_id, sku_id, qty, 'Easy' AS source FROM sales_sku_daily
                          WHERE day BETWEEN :s AND :e) ssm
                    JOIN branches b ON b.branch_id=ssm.branch_id
                    WHERE b.name!='ALL'{dbf}{gcond2}""",
                {"s": s.isoformat(), "e": e.isoformat(), **dbp}).fetchone()
            rev = r["rev"] or 0; cogs = co["cogs"] or 0
            return {"revenue_ex_vat": rev, "customers": r["cust"] or 0, "discount": r["disc"] or 0,
                    "bills": r["bills"] or 0, "cogs": cogs, "gross_profit": round(rev - cogs, 2)}

        cur = agg(cur_s, cur_e); prev = agg(prev_s, prev_e)
        # COGS window is rolling — flag if prev period predates available daily-sku data
        mindd = con.execute("SELECT MIN(day) FROM sales_sku_daily").fetchone()[0]
        cogs_ok = bool(mindd) and mindd <= prev_s.isoformat()
    return {"available": True, "partial": partial, "cutoff_day": d, "days_in_month": last_dom,
            "cur_month": f"{y:04d}-{m:02d}", "prev_month": f"{py:04d}-{pm:02d}",
            "cur_range": [cur_s.isoformat(), cur_e.isoformat()],
            "prev_range": [prev_s.isoformat(), prev_e.isoformat()],
            "cur": cur, "prev": prev, "cogs_comparable": cogs_ok}


# Gross-margin distribution bands: (idx, label, lo, hi). lo/hi are GM% bounds
# (lo inclusive, hi exclusive). idx -1 = loss bucket. Shared by the dist build
# and the drill-down endpoint so labels/ranges never drift.
def _build_gm_buckets():
    out = [(-1, "ขาดทุน (<0%)", None, 0.0)]
    for i in range(10):                       # 0–5 … 45–50 (5% bands)
        lo = i * 5; out.append((i, f"{lo}–{lo+5}%", float(lo), float(lo + 5)))
    for j in range(5):                        # 50–60 … 90–100 (10% bands)
        lo = 50 + j * 10; out.append((10 + j, f"{lo}–{lo+10}%", float(lo), float(lo + 10)))
    return out
_PNL_GM_BUCKETS = _build_gm_buckets()


def _pnl_bounds(d, gran):
    import datetime as _dt
    if gran == "day": return d, d
    if gran == "week":
        sd = d - _dt.timedelta(days=d.weekday()); return sd, sd + _dt.timedelta(days=6)
    sd = d.replace(day=1)
    nxt = sd.replace(year=sd.year + 1, month=1) if sd.month == 12 else sd.replace(month=sd.month + 1)
    return sd, nxt - _dt.timedelta(days=1)


@app.get("/api/reports/pnl-bill-drill")
def pnl_bill_drill(idx: int = Query(...), date: Optional[str] = Query(None),
                   branch: Optional[str] = Query(None), group: Optional[str] = Query(None),
                   gran: str = Query("day"), limit: int = Query(500)):
    """Drill-down: bills whose gross margin falls in distribution band `idx`
    (see _PNL_GM_BUCKETS). COGS via BoM only."""
    import datetime as _dt
    if gran not in ("day", "week", "month"):
        gran = "day"
    band = next((b for b in _PNL_GM_BUCKETS if b[0] == idx), None)
    if band is None:
        return {"idx": idx, "bills": [], "stat": {}}
    _, label, lo, hi = band
    tfilt = ["t.source='Easy'", "b.name!='ALL'"]; tp: dict = {}
    if branch: tfilt.append("b.name=:branch"); tp["branch"] = branch
    _group_filter(tfilt, tp, group)
    tw = " AND ".join(tfilt)
    with get_db() as con:
        maxd = con.execute("SELECT MAX(day) FROM sale_bill_cogs").fetchone()[0]
        if not maxd:
            return {"idx": idx, "label": label, "bills": [], "stat": {}}
        focus = (date or maxd)[:10]
        fy, fm, fd = (int(x) for x in focus.split("-"))
        s, e = _pnl_bounds(_dt.date(fy, fm, fd), gran)
        JN = ("FROM sales_transaction t JOIN sale_bill_cogs c ON c.receipt_no=t.receipt_no "
              "JOIN branches b ON b.branch_id=t.branch_id "
              f"WHERE {tw} AND date(t.sold_at) BETWEEN :s AND :e AND t.total_inc_vat>0")
        params = {**tp, "s": s.isoformat(), "e": e.isoformat()}
        gm = "(t.total_inc_vat/1.07 - c.cogs_bom)/(t.total_inc_vat/1.07)*100"
        cond = f"{gm} < 0" if idx == -1 else f"{gm} >= {lo} AND {gm} < {hi}"
        bills = [{"receipt": x["receipt_no"], "branch": x["branch"], "rev": x["rev"], "cogs": x["cogs"],
                  "disc": x["disc"], "cust": x["cust"], "gp_pct": x["gpp"]}
                 for x in con.execute(f"""SELECT t.receipt_no, b.name branch, ROUND(t.total_inc_vat/1.07) rev,
                   ROUND(c.cogs_bom) cogs, ROUND(t.discount) disc, t.customer_count cust,
                   ROUND({gm},1) gpp {JN} AND {cond} ORDER BY gpp ASC LIMIT :lim""",
                   {**params, "lim": limit}).fetchall()]
        st = con.execute(f"""SELECT COUNT(*) n, ROUND(SUM(t.total_inc_vat/1.07)) rev,
                   ROUND(SUM(c.cogs_bom)) cogs, SUM(t.customer_count) cust, ROUND(SUM(t.discount)) disc,
                   ROUND(AVG({gm}),1) avg_gm {JN} AND {cond}""", params).fetchone()
        rev = (st["rev"] or 0) if st else 0; cogs = (st["cogs"] or 0) if st else 0
        stat = {"n": (st["n"] or 0) if st else 0, "rev": rev, "cogs": cogs, "gp": round(rev - cogs),
                "cust": (st["cust"] or 0) if st else 0, "disc": (st["disc"] or 0) if st else 0,
                "avg_gm": (st["avg_gm"]) if st else None}
    return {"idx": idx, "label": label, "lo": lo, "hi": hi, "range": [s.isoformat(), e.isoformat()],
            "bills": bills, "capped": len(bills) >= limit, "stat": stat}


@app.get("/api/reports/pnl-bill")
def pnl_bill(date: Optional[str] = Query(None), branch: Optional[str] = Query(None),
             group: Optional[str] = Query(None), gran: str = Query("day")):
    """Per-bill P&L for a day/week/month: revenue − BoM COGS = gross profit, gp/bill,
    gp/head, gp% distribution, and worst (lowest-margin / heavy-discount) bills.
    COGS via our BoM only (sale_bill_cogs). Limited to the costed rolling window."""
    import datetime as _dt
    if gran not in ("day", "week", "month"):
        gran = "day"
    tfilt = ["t.source='Easy'", "b.name!='ALL'"]; tp: dict = {}
    if branch: tfilt.append("b.name=:branch"); tp["branch"] = branch
    _group_filter(tfilt, tp, group)
    tw = " AND ".join(tfilt)

    def bounds(d):
        if gran == "day": return d, d
        if gran == "week":
            s = d - _dt.timedelta(days=d.weekday()); return s, s + _dt.timedelta(days=6)
        s = d.replace(day=1)
        nxt = s.replace(year=s.year + 1, month=1) if s.month == 12 else s.replace(month=s.month + 1)
        return s, nxt - _dt.timedelta(days=1)

    with get_db() as con:
        try:
            maxd = con.execute("SELECT MAX(day) FROM sale_bill_cogs").fetchone()[0]
        except Exception:
            return {"focus": None, "note": "no per-bill cost data", "summary": {}, "dist": [], "worst": []}
        if not maxd:
            return {"focus": None, "summary": {}, "dist": [], "worst": []}
        focus = (date or maxd)[:10]
        fy, fm, fd = (int(x) for x in focus.split("-")); fdate = _dt.date(fy, fm, fd)
        s, e = bounds(fdate)
        JN = (f"FROM sales_transaction t JOIN sale_bill_cogs c ON c.receipt_no=t.receipt_no "
              f"JOIN branches b ON b.branch_id=t.branch_id "
              f"WHERE {tw} AND date(t.sold_at) BETWEEN :s AND :e AND t.total_inc_vat>0")
        params = {**tp, "s": s.isoformat(), "e": e.isoformat()}
        r = con.execute(f"SELECT COUNT(*) bills, ROUND(SUM(t.total_inc_vat/1.07),2) rev, "
                        f"ROUND(SUM(c.cogs_bom),2) cogs, SUM(t.customer_count) cust, "
                        f"ROUND(SUM(t.discount),2) disc {JN}", params).fetchone()
        bills = r["bills"] or 0; rev = r["rev"] or 0; cogs = r["cogs"] or 0; cust = r["cust"] or 0
        gp = round(rev - cogs, 2); gpp = round(gp / rev * 100, 1) if rev else None
        summary = {"bills": bills, "revenue": rev, "cogs": cogs, "gross_profit": gp, "gp_pct": gpp,
                   "discount": r["disc"] or 0, "customers": cust,
                   "gp_per_bill": round(gp / bills) if bills else None,
                   "gp_per_head": round(gp / cust) if cust else None}
        # Fine-grained gross-margin distribution: 5% bands across 0–50%, 10% above.
        # bkt = -1 loss · 0..9 = 0–5..45–50 · 10..14 = 50–60..90–100.
        gm_expr = "(t.total_inc_vat/1.07 - c.cogs_bom)/(t.total_inc_vat/1.07)*100"
        rows = con.execute(f"""SELECT bkt, COUNT(*) n, ROUND(SUM(rev)) rev, ROUND(SUM(cogs)) cogs,
                   ROUND(SUM(rev-cogs)) gp, SUM(cust) cust FROM (
                     SELECT rev, cogs, cust,
                       CASE WHEN gm<0 THEN -1 WHEN gm<50 THEN CAST(gm/5 AS INT)
                            ELSE 10+MIN(4,CAST((gm-50)/10 AS INT)) END bkt
                     FROM (SELECT t.total_inc_vat/1.07 rev, c.cogs_bom cogs, t.customer_count cust,
                           {gm_expr} gm {JN}) x) y GROUP BY bkt""", params).fetchall()
        bmap = {r["bkt"]: r for r in rows}
        dist = []
        for idx, label, lo, hi in _PNL_GM_BUCKETS:
            r = bmap.get(idx)
            dist.append({"idx": idx, "label": label, "lo": lo, "hi": hi,
                         "n": (r["n"] if r else 0), "rev": (r["rev"] or 0) if r else 0,
                         "cogs": (r["cogs"] or 0) if r else 0, "gp": (r["gp"] or 0) if r else 0,
                         "cust": (r["cust"] or 0) if r else 0})
        def rows_for(extra_where, limit):
            return [{"receipt": x["receipt_no"], "branch": x["branch"], "rev": x["rev"], "cogs": x["cogs"],
                     "disc": x["disc"], "cust": x["cust"], "gp_pct": x["gpp"], "dwell_min": x["dwell_min"]}
                    for x in con.execute(f"""SELECT t.receipt_no, b.name branch, ROUND(t.total_inc_vat/1.07) rev,
                      ROUND(c.cogs_bom) cogs, ROUND(t.discount) disc, t.customer_count cust, t.dwell_min,
                      ROUND((t.total_inc_vat/1.07 - c.cogs_bom)/(t.total_inc_vat/1.07)*100,1) gpp
                      {JN}{extra_where} ORDER BY gpp ASC {limit}""", params).fetchall()]
        worst = rows_for("", "LIMIT 12")
        # Per-branch margin aggregate (replaces the noisy 12-worst-bills list):
        # avg GM%, GP/bill, bill count and loss-bill count — sorted worst margin first.
        by_branch = []
        for x in con.execute(f"""
            SELECT b.name branch, COUNT(*) bills,
                   ROUND(SUM(t.total_inc_vat/1.07)) rev,
                   ROUND(SUM(c.cogs_bom)) cogs,
                   ROUND(SUM(t.total_inc_vat/1.07 - c.cogs_bom)) gp,
                   SUM(CASE WHEN c.cogs_bom > t.total_inc_vat/1.07 THEN 1 ELSE 0 END) loss_bills,
                   SUM(t.customer_count) cust
            {JN} GROUP BY b.name""", params).fetchall():
            bb_rev = x["rev"] or 0; bb_gp = x["gp"] or 0; bb_bills = x["bills"] or 0
            by_branch.append({
                "branch": x["branch"], "bills": bb_bills, "rev": bb_rev, "cogs": x["cogs"] or 0,
                "gp": bb_gp, "gm_pct": round(bb_gp / bb_rev * 100, 1) if bb_rev else None,
                "gp_per_bill": round(bb_gp / bb_bills) if bb_bills else None,
                "loss_bills": x["loss_bills"] or 0, "cust": x["cust"] or 0,
            })
        by_branch.sort(key=lambda r: (r["gm_pct"] if r["gm_pct"] is not None else 999))
        # ALL loss-making bills (COGS > revenue, i.e. negative gross profit). Capped
        # high for safety; loss_count/loss_total report the true totals regardless.
        loss = rows_for(" AND c.cogs_bom > t.total_inc_vat/1.07", "LIMIT 2000")
        ls = con.execute(f"""SELECT COUNT(*) n, ROUND(SUM(c.cogs_bom - t.total_inc_vat/1.07)) loss
                             {JN} AND c.cogs_bom > t.total_inc_vat/1.07""", params).fetchone()
    return {"focus": focus, "gran": gran, "range": [s.isoformat(), e.isoformat()],
            "summary": summary, "dist": dist, "worst": worst, "by_branch": by_branch,
            "loss": loss, "loss_count": (ls["n"] or 0) if ls else 0,
            "loss_total": (ls["loss"] or 0) if ls else 0, "loss_capped": len(loss) >= 2000}


@app.get("/api/reports/pnl-bill-items")
def pnl_bill_items(receipt: str = Query(..., description="receipt_no of the bill to drill into")):
    """Menu lines for a single (loss-making) bill: qty, per-unit cost, ex-VAT revenue,
    COGS and per-line gross profit — so you can see exactly what the customer ordered
    that drove COGS above revenue. Source: sale_bill_item (qty/rev staged by the sync
    for LOSS bills only). COGS is recomputed LIVE per line as qty × sku_effective_cost
    (cost-management table first) so the drill always reflects current costs, not the
    snapshot frozen at sync time. `synced` is False until the next sync builds the table."""
    with get_db() as con:
        synced = con.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='sale_bill_item'").fetchone() is not None
        header = None
        # Header comes from sales_transaction (always present) so the drill works for
        # ANY bill — discounted, loss, or normal — not only the loss bills that have a
        # sale_bill_cogs snapshot. cogs_bom is LEFT-joined (NULL when outside the
        # rolling per-bill-COGS window); when menu lines exist it is overridden below
        # with the live line sum.
        hdr = con.execute(
            """SELECT t.receipt_no, b.name branch, t.sold_at, t.customer_count cust,
                      ROUND(t.total_inc_vat/1.07) rev, ROUND(c.cogs_bom) cogs,
                      ROUND(COALESCE(t.discount,0) + COALESCE(t.sp_discount,0)) disc
               FROM sales_transaction t
               LEFT JOIN sale_bill_cogs c ON c.receipt_no=t.receipt_no
               JOIN branches b ON b.branch_id=t.branch_id WHERE t.receipt_no=:r""",
            {"r": receipt}).fetchone()
        if hdr:
            header = {"receipt": hdr["receipt_no"], "branch": hdr["branch"], "sold_at": hdr["sold_at"],
                      "cust": hdr["cust"] or 0, "rev": hdr["rev"] or 0, "cogs": hdr["cogs"] or 0,
                      "disc": hdr["disc"] or 0}
        items = []
        if synced:
            _build_sku_effective_cost(con, force=False)   # ensure the lookup exists
            items = [{"menu": (x["description"] or x["sku_code"] or "—").replace("(B) ", "").replace("(A) ", ""),
                      "sku_code": x["sku_code"], "qty": x["qty"] or 0,
                      "unit_cost": round(x["uc"] or 0, 2), "rev": x["rev"] or 0,
                      "cogs": x["cogs"] or 0, "margin": round((x["rev"] or 0) - (x["cogs"] or 0))}
                     for x in con.execute(
                        f"""SELECT s.description, s.sku_code, ROUND(SUM(i.qty),2) qty,
                                  ROUND(SUM(i.rev)) rev,
                                  CASE WHEN s.sku_code IN {_EASY_TIER_CODES_SQL} THEN 0
                                       ELSE COALESCE((SELECT sec.recipe_cost FROM sku_effective_cost sec
                                            WHERE sec.sku_id=i.sku_id), 0) END uc,
                                  ROUND(SUM(i.qty) * CASE WHEN s.sku_code IN {_EASY_TIER_CODES_SQL} THEN 0
                                       ELSE COALESCE((SELECT sec.recipe_cost FROM sku_effective_cost sec
                                            WHERE sec.sku_id=i.sku_id), 0) END) cogs
                           FROM sale_bill_item i JOIN skus s ON s.sku_id=i.sku_id
                           WHERE i.receipt_no=:r GROUP BY i.sku_id ORDER BY cogs DESC, rev DESC""",
                        {"r": receipt}).fetchall()]
            if header and items:
                # header COGS comes from sale_bill_cogs (sync snapshot) — override with
                # the live recomputation so the summary tiles match the line table.
                header["cogs"] = round(sum(x["cogs"] for x in items))
    return {"receipt": receipt, "synced": synced, "header": header, "items": items}


def _bom_unit_cost_sql(s: str = "s") -> str:
    """Per-serving effective unit cost for a SKU (alias `s` = skus). sku_cost
    (cost-management table, non-zero) → recipe → alias → 0; BUFFET tier = 0.
    Same priority as _unified_cogs_expr (cost-management first, 2026-06-11)."""
    return (f"CASE WHEN {s}.sku_code LIKE 'BUFFET%' THEN 0 ELSE COALESCE("
            f"(SELECT NULLIF(sc.unit_cost,0) FROM sku_cost sc WHERE sc.sku_id={s}.sku_id),"
            f"(SELECT SUM(bi.ingredient_cost) FROM bom_ingredients bi WHERE bi.sku_id={s}.sku_id),"
            f"(SELECT SUM(bi2.ingredient_cost) FROM bom_ingredients bi2 "
            f" JOIN bom_sku_alias a ON a.resolved_sku_id=bi2.sku_id WHERE a.bom_sku_code={s}.sku_code),0) END")


_DTYPE_LABEL = {0: "ทานที่ร้าน", 1: "ทานที่ร้าน", 2: "เดลิเวอรี", 3: "กลับบ้าน", 4: "อื่น ๆ"}

@app.get("/api/reports/ops")
def reports_ops(month: Optional[str] = Query(None), branch: Optional[str] = Query(None),
                date_from: Optional[str] = Query(None, description="YYYY-MM-DD; when given the report aggregates DAILY tables over [date_from, date_to]"),
                date_to: Optional[str] = Query(None, description="YYYY-MM-DD; range end (defaults to date_from when only one is given)")):
    """Ops/sales-side report: payment mix, channel mix, void, table turnover, promo
    — from the staged pos_* tables. Revenue/qty only (no cost).

    Scope follows the caller's period filter: when a date range (date_from/date_to) is
    supplied the report aggregates the DAILY tables over [date_from, date_to]
    (pos_payment_day / pos_coupon_day / pos_table_turn_day by day, plus pos_void_day /
    pos_channel_day); otherwise it uses the MONTHLY tables for `month` (latest month by
    default — unchanged behaviour). The daily Void / Channel tables are newer than the
    others, so they degrade to empty until the next sync stages them (Payment / Promo /
    Table per-day work immediately — their daily tables already populate every sync)."""
    daily = bool(date_from or date_to)
    with get_db() as con:
        bid = None
        if branch:
            row = con.execute("SELECT branch_id FROM branches WHERE name=?", (branch,)).fetchone()
            bid = row["branch_id"] if row else -1
        def bf(col="branch_id"):
            return (f" AND {col}=:bid", {"bid": bid}) if bid is not None else ("", {})
        bfc, bp = bf()
        # Which staged tables exist — daily Void/Channel are added by a later sync, so a
        # daily request degrades those sections to empty until then (not a 500).
        tabs = {r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name IN "
            "('pos_payment_day','pos_channel_day','pos_void_day','pos_coupon_day','pos_table_turn_day')")}

        if daily:
            d1 = date_from or date_to
            d2 = date_to or date_from
            dscope = {"d1": d1, "d2": d2}
            def dwhere(alias=""):                      # date predicate (col `day` on *_day tables)
                a = f"{alias}." if alias else ""
                return f"{a}day BETWEEN :d1 AND :d2"
            pay_tbl, ch_tbl, void_tbl, cpn_tbl = "pos_payment_day", "pos_channel_day", "pos_void_day", "pos_coupon_day"
            scope = {"month": None, "date_from": d1, "date_to": d2, "daily": True}
        else:
            mrow = con.execute("SELECT MAX(period_month) m FROM pos_payment_month").fetchone()
            mon = month or (mrow["m"] if mrow else None)
            if not mon:
                return {"month": None, "date_from": None, "date_to": None, "daily": False,
                        "payment": [], "channel": [], "void": [],
                        "void_total": {"value": 0, "qty": 0, "bom_cost": 0}, "table": {}, "promo": []}
            dscope = {"m": mon}
            def dwhere(alias=""):                      # date predicate (col `period_month`)
                a = f"{alias}." if alias else ""
                return f"{a}period_month=:m"
            pay_tbl, ch_tbl, void_tbl, cpn_tbl = "pos_payment_month", "pos_channel_month", "pos_void", "pos_coupon_month"
            scope = {"month": mon, "date_from": None, "date_to": None, "daily": False}

        prm = {**dscope, **bp}
        # In daily mode a *_day table may not be staged yet → degrade that section to empty.
        def _ok(tbl):
            return (not daily) or (tbl in tabs)

        payment = [dict(r) for r in con.execute(
            f"SELECT payment_code code, ROUND(SUM(amount)) amount, SUM(txn_count) n FROM {pay_tbl} "
            f"WHERE {dwhere()}{bfc} GROUP BY payment_code ORDER BY amount DESC", prm).fetchall()] if _ok(pay_tbl) else []

        channel = []
        if _ok(ch_tbl):
            ch = con.execute(f"SELECT delivery_type dtype, SUM(bills) bills, SUM(customers) cust, ROUND(SUM(total_inc_vat/1.07)) rev "
                             f"FROM {ch_tbl} WHERE {dwhere()}{bfc} GROUP BY delivery_type", prm).fetchall()
            channel = [{"label": _DTYPE_LABEL.get(r["dtype"], "อื่น ๆ"), "bills": r["bills"], "customers": r["cust"], "revenue": r["rev"] or 0} for r in ch]

        _uc = _bom_unit_cost_sql("s")
        void, vt = [], None
        if _ok(void_tbl):
            void = [dict(r) for r in con.execute(
                f"SELECT COALESCE(s.description,s.sku_code) menu, ROUND(SUM(v.qty)) qty, ROUND(SUM(v.ext_price)) value, "
                f"ROUND(SUM(v.qty * ({_uc}))) bom_cost "
                f"FROM {void_tbl} v JOIN skus s ON s.sku_id=v.sku_id WHERE {dwhere('v')}{bfc} "
                f"GROUP BY v.sku_id ORDER BY bom_cost DESC LIMIT 12", prm).fetchall()]
            vt = con.execute(
                f"SELECT ROUND(SUM(v.ext_price)) total, ROUND(SUM(v.qty)) qty, ROUND(SUM(v.qty * ({_uc}))) bom_cost "
                f"FROM {void_tbl} v JOIN skus s ON s.sku_id=v.sku_id WHERE {dwhere('v')}{bfc}", prm).fetchone()

        # table turnover (always the daily table; range = day BETWEEN, month = substr match)
        tbf, tbp = bf()
        table = {"avg_turn_min": None, "tabs": 0, "customers": 0}
        if "pos_table_turn_day" in tabs:
            tcond = "day BETWEEN :d1 AND :d2" if daily else "substr(day,1,7)=:m"
            tt = con.execute(f"SELECT ROUND(AVG(avg_turn_min),0) avg_min, SUM(tabs) tabs, SUM(customers) cust "
                             f"FROM pos_table_turn_day WHERE {tcond}{tbf} AND avg_turn_min>0", {**dscope, **tbp}).fetchone()
            table = {"avg_turn_min": tt["avg_min"], "tabs": tt["tabs"] or 0, "customers": tt["cust"] or 0}

        promo = []
        if _ok(cpn_tbl):
            _has_cs = con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='coupon_series'").fetchone()
            _cs_join = "LEFT JOIN coupon_series cs ON cs.coupon_series_id=cm.coupon_series_id" if _has_cs else ""
            _cs_name = "COALESCE(NULLIF(TRIM(cs.name),''),'Series '||cm.coupon_series_id)" if _has_cs else "'Series '||cm.coupon_series_id"
            _cs_code = "cs.code" if _has_cs else "NULL"
            promo = [dict(r) for r in con.execute(
                f"SELECT cm.coupon_series_id series, {_cs_name} name, {_cs_code} code, "
                f"SUM(cm.used_count) uses, ROUND(SUM(cm.discount_amount)) discount "
                f"FROM {cpn_tbl} cm {_cs_join} WHERE {dwhere('cm')}{bfc} "
                f"GROUP BY cm.coupon_series_id ORDER BY discount DESC LIMIT 12", prm).fetchall()]
    return {**scope, "payment": payment, "channel": channel, "void": void,
            "void_total": {"value": (vt["total"] or 0) if vt else 0, "qty": (vt["qty"] or 0) if vt else 0,
                           "bom_cost": (vt["bom_cost"] or 0) if vt else 0},
            "table": table, "promo": promo,
            # daily Void/Channel tables aren't staged yet → tell the UI to show a "after next
            # sync" hint instead of a bare "no data" for those two sections in day/range mode.
            "void_pending": bool(daily and "pos_void_day" not in tabs),
            "channel_pending": bool(daily and "pos_channel_day" not in tabs)}


# ═══════════════════════════════════════════════════════════════════════════
# EXTRA — deeper POS analytics from previously-untapped tables (separate group)
# All counts/amounts only · NO POS cost (iron rule: COGS stays on our BoM).
# ═══════════════════════════════════════════════════════════════════════════
def _extra_months(con, table, col="period_month"):
    r = con.execute(f"SELECT MAX({col}) m FROM {table}").fetchone()
    return r["m"] if r else None


@app.get("/api/extra/cash")
def extra_cash(month: Optional[str] = Query(None), branch: Optional[str] = Query(None),
               group: Optional[str] = Query(None)):
    """Cash over/short + ops summary per branch per day (loss prevention)."""
    filt = ["b.name!='ALL'"]; p: dict = {}
    if branch: filt.append("b.name=:branch"); p["branch"] = branch
    _group_filter(filt, p, group)
    tw = " AND ".join(filt)
    with get_db() as con:
        mon = month or _extra_months(con, "pos_cashcount_day", "substr(day,1,7)")
        if not mon:
            return {"month": None, "branches": [], "worst_days": [], "summary": {}}
        pm = {**p, "m": mon}
        JN = "FROM pos_cashcount_day x JOIN branches b ON b.branch_id=x.branch_id WHERE " + tw + " AND substr(x.day,1,7)=:m"
        branches = [dict(r) for r in con.execute(
            f"""SELECT b.name branch, ROUND(SUM(x.over_short)) over_short, ROUND(SUM(x.total_sales)) sales,
                SUM(x.pax) pax, ROUND(SUM(x.discount)) discount, ROUND(SUM(x.cancel)) cancel,
                SUM(CASE WHEN x.over_short<0 THEN 1 ELSE 0 END) short_days
                {JN} GROUP BY b.name ORDER BY over_short ASC""", pm).fetchall()]
        worst = [dict(r) for r in con.execute(
            f"""SELECT b.name branch, x.day, ROUND(x.over_short) over_short, ROUND(x.total_sales) sales, x.pax
                {JN} AND x.over_short<>0 ORDER BY x.over_short ASC LIMIT 15""", pm).fetchall()]
        s = con.execute(
            f"""SELECT ROUND(SUM(x.over_short)) net, ROUND(SUM(CASE WHEN x.over_short<0 THEN x.over_short ELSE 0 END)) short_total,
                ROUND(SUM(CASE WHEN x.over_short>0 THEN x.over_short ELSE 0 END)) over_total,
                SUM(CASE WHEN x.over_short<0 THEN 1 ELSE 0 END) short_days, COUNT(*) day_branch
                {JN}""", pm).fetchone()
        summary = {"net": (s["net"] or 0), "short_total": (s["short_total"] or 0),
                   "over_total": (s["over_total"] or 0), "short_days": (s["short_days"] or 0),
                   "day_branch": (s["day_branch"] or 0)}
    return {"month": mon, "branches": branches, "worst_days": worst, "summary": summary}


@app.get("/api/extra/customer-type")
def extra_customer_type(month: Optional[str] = Query(None), branch: Optional[str] = Query(None),
                        group: Optional[str] = Query(None)):
    """Customer-type / channel mix: walk-in vs Grab vs Line Man vs takeaway vs credit."""
    filt = ["b.name!='ALL'"]; p: dict = {}
    if branch: filt.append("b.name=:branch"); p["branch"] = branch
    _group_filter(filt, p, group)
    tw = " AND ".join(filt)
    with get_db() as con:
        mon = month or _extra_months(con, "pos_customer_type_month")
        if not mon:
            return {"month": None, "types": []}
        # net is INC-VAT in pos_customer_type_month → divide to ex-VAT (rule: revenue
        # is always ex-VAT). The `customers` count is a POS channel-tag tally, ~10x
        # real covers — usable only for CHANNEL MIX %, never as absolute pax, so we
        # expose no per-head figure here (it would be meaningless).
        rows = [dict(r) for r in con.execute(
            f"""SELECT x.type_name type, SUM(x.customers) customers, ROUND(SUM(x.net)/{VAT_DIVISOR}) net
                FROM pos_customer_type_month x JOIN branches b ON b.branch_id=x.branch_id
                WHERE {tw} AND x.period_month=:m GROUP BY x.type_name ORDER BY customers DESC""",
            {**p, "m": mon}).fetchall()]
        tc = sum(r["customers"] or 0 for r in rows)
        tn = sum(r["net"] or 0 for r in rows)
        tc_guard = tc or 1
        tn_guard = tn or 1
        for r in rows:
            r["cust_pct"] = round((r["customers"] or 0) / tc_guard * 100, 1)
            r["net_pct"] = round((r["net"] or 0) / tn_guard * 100, 1)
        # Per-branch breakdown (แยกตามสาขา): total + delivery share per branch.
        brows = con.execute(
            f"""SELECT b.name branch, x.type_name type, SUM(x.customers) customers, ROUND(SUM(x.net)/{VAT_DIVISOR}) net
                FROM pos_customer_type_month x JOIN branches b ON b.branch_id=x.branch_id
                WHERE {tw} AND x.period_month=:m GROUP BY b.name, x.type_name""",
            {**p, "m": mon}).fetchall()
    import re as _re
    _DELIV = _re.compile(r'grab|line|food\s*panda|foodpanda|delivery|robinhood|shopee|เดลิเวอรี|แกร็บ|ไลน์|ส่ง', _re.I)
    bmap: dict = {}
    for r in brows:
        e = bmap.setdefault(r["branch"], {"branch": r["branch"], "customers": 0, "net": 0, "delivery_cust": 0})
        e["customers"] += r["customers"] or 0
        e["net"] += r["net"] or 0
        if _DELIV.search(r["type"] or ""):
            e["delivery_cust"] += r["customers"] or 0
    by_branch = []
    for e in bmap.values():
        cu = e["customers"]
        by_branch.append({**e, "delivery_pct": round(e["delivery_cust"] / cu * 100, 1) if cu else 0,
                          "net_pct": round(e["net"] / tn_guard * 100, 1) if tn else 0})
    by_branch.sort(key=lambda r: -(r["net"] or 0))
    return {"month": mon, "types": rows, "total_customers": tc, "total_net": tn,
            "by_branch": by_branch, "count_caveat": True}


@app.get("/api/extra/modifiers")
def extra_modifiers(branch: Optional[str] = Query(None), group: Optional[str] = Query(None),
                    limit: int = Query(25)):
    """Top customer special-requests (free modifiers) — for kitchen prep planning.
    All-time counts (no revenue; choices are free)."""
    filt = ["b.name!='ALL'"]; p: dict = {}
    if branch: filt.append("b.name=:branch"); p["branch"] = branch
    _group_filter(filt, p, group)
    tw = " AND ".join(filt)
    with get_db() as con:
        JN = "FROM pos_modifier x JOIN branches b ON b.branch_id=x.branch_id WHERE " + tw
        choices = [dict(r) for r in con.execute(
            f"SELECT x.choice_name choice, SUM(x.n) n, ROUND(SUM(x.qty)) qty {JN} "
            f"GROUP BY x.choice_name ORDER BY n DESC LIMIT :lim", {**p, "lim": limit}).fetchall()]
        tot = con.execute(f"SELECT SUM(x.n) n {JN}", p).fetchone()
    total = (tot["n"] or 0) if tot else 0
    for r in choices:
        r["pct"] = round((r["n"] or 0) / total * 100, 1) if total else 0
    return {"choices": choices, "total": total}


@app.get("/api/extra/price-zone")
def extra_price_zone(month: Optional[str] = Query(None), branch: Optional[str] = Query(None),
                     group: Optional[str] = Query(None), limit: int = Query(100, ge=1, le=500)):  # fix B5
    """Price-override log (governance) + table-zone layout per branch."""
    filt = ["b.name!='ALL'"]; p: dict = {}
    if branch: filt.append("b.name=:branch"); p["branch"] = branch
    _group_filter(filt, p, group)
    tw = " AND ".join(filt)
    with get_db() as con:
        mfilt = ""
        if month:
            mfilt = " AND substr(x.change_date,1,7)=:m"; p["m"] = month
        changes = [dict(r) for r in con.execute(
            f"""SELECT x.change_date date, b.name branch, x.branch_id bid, x.sku_id sid,
                COALESCE(s.description,x.item_desc) item,
                ROUND(x.default_price) default_price, ROUND(x.new_price) new_price,
                ROUND(x.new_price - x.default_price) delta, x.user_name user
                FROM pos_price_change x JOIN branches b ON b.branch_id=x.branch_id
                LEFT JOIN skus s ON s.sku_id=x.sku_id WHERE {tw}{mfilt}
                ORDER BY x.change_date DESC LIMIT :lim""", {**p, "lim": limit}).fetchall()]
        # Price elasticity: avg daily qty 14d before vs 14d after each change
        # (same branch+sku, from sales_sku_daily). Directional signal only.
        import datetime as _dt
        drange = con.execute("SELECT MIN(day) lo, MAX(day) hi FROM sales_sku_daily").fetchone()
        dlo = drange["lo"] if drange else None; dhi = drange["hi"] if drange else None
        for c in changes:
            c["elasticity"] = None; c["qty_pct"] = None; c["before_qty"] = None; c["after_qty"] = None
            if not (c.get("sid") and c.get("bid") and dlo and c.get("date")):
                continue
            cd = c["date"][:10]
            if cd < dlo or cd > dhi:
                continue
            d0 = _dt.date(*(int(x) for x in cd.split("-")))
            bS = (d0 - _dt.timedelta(days=14)).isoformat(); bE = (d0 - _dt.timedelta(days=1)).isoformat()
            aS = cd; aE = (d0 + _dt.timedelta(days=13)).isoformat()
            er = con.execute("""
                SELECT AVG(CASE WHEN day BETWEEN :bS AND :bE THEN qty END) bq,
                       AVG(CASE WHEN day BETWEEN :aS AND :aE THEN qty END) aq
                FROM sales_sku_daily WHERE branch_id=:bid AND sku_id=:sid
                  AND day BETWEEN :bS AND :aE""",
                {"bid": c["bid"], "sid": c["sid"], "bS": bS, "bE": bE, "aS": aS, "aE": aE}).fetchone()
            bq = er["bq"]; aq = er["aq"]
            if bq and bq > 0 and aq is not None:
                c["before_qty"] = round(bq, 1); c["after_qty"] = round(aq, 1)
                qpct = (aq - bq) / bq * 100
                c["qty_pct"] = round(qpct, 1)
                dp = (c["new_price"] or 0) - (c["default_price"] or 0)
                ppct = (dp / c["default_price"] * 100) if c["default_price"] else None
                if ppct:
                    c["elasticity"] = round(qpct / ppct, 2)
        for c in changes:
            c.pop("bid", None); c.pop("sid", None)
        # strip month param for the zone query (zones have no date)
        zp = {k: v for k, v in p.items() if k != "m"}
        zones = [dict(r) for r in con.execute(
            f"""SELECT b.name branch, COUNT(*) zones, SUM(x.tables) tables, SUM(x.seats) seats
                FROM pos_zone x JOIN branches b ON b.branch_id=x.branch_id WHERE {tw}
                GROUP BY b.name ORDER BY tables DESC""", zp).fetchall()]
        cnt = con.execute(f"SELECT COUNT(*) n FROM pos_price_change x JOIN branches b ON b.branch_id=x.branch_id WHERE {tw}{mfilt}", p).fetchone()
    return {"changes": changes, "zones": zones, "change_count": (cnt["n"] or 0) if cnt else 0,
            "month": month}


@app.get("/api/extra/cancel-bills")
def extra_cancel_bills(month: Optional[str] = Query(None), branch: Optional[str] = Query(None),
                       group: Optional[str] = Query(None)):
    """Whole BILLS voided/deleted (Sale.Active=0), by branch and by reason.
    Distinct from item-level Void in the Ops report."""
    filt = ["b.name!='ALL'"]; p: dict = {}
    if branch: filt.append("b.name=:branch"); p["branch"] = branch
    _group_filter(filt, p, group)
    tw = " AND ".join(filt)
    with get_db() as con:
        mon = month or _extra_months(con, "pos_cancel_bill")
        if not mon:
            return {"month": None, "branches": [], "reasons": [], "summary": {}}
        pm = {**p, "m": mon}
        JN = "FROM pos_cancel_bill x JOIN branches b ON b.branch_id=x.branch_id WHERE " + tw + " AND x.period_month=:m"
        branches = [dict(r) for r in con.execute(
            f"SELECT b.name branch, SUM(x.bills) bills, ROUND(SUM(x.amount)) amount {JN} "
            f"GROUP BY b.name ORDER BY bills DESC", pm).fetchall()]
        reasons = [dict(r) for r in con.execute(
            f"""SELECT CASE WHEN x.reason_id=0 THEN 'ไม่ระบุเหตุผล'
                         ELSE COALESCE(rr.reason_desc, 'รหัส '||x.reason_id) END reason,
                   SUM(x.bills) bills, ROUND(SUM(x.amount)) amount
                FROM pos_cancel_bill x JOIN branches b ON b.branch_id=x.branch_id
                LEFT JOIN pos_cancel_reason rr ON rr.reason_id=x.reason_id
                WHERE {tw} AND x.period_month=:m
                GROUP BY x.reason_id ORDER BY bills DESC""", pm).fetchall()]
        s = con.execute(f"SELECT SUM(x.bills) bills, ROUND(SUM(x.amount)) amount {JN}", pm).fetchone()
        # context: valid bills that month (to express cancel rate)
        vb = con.execute(
            f"""SELECT COUNT(*) n FROM sales_transaction t JOIN branches b ON b.branch_id=t.branch_id
                WHERE {tw} AND t.source='Easy' AND strftime('%Y-%m',t.sold_at)=:m""", pm).fetchone()
        valid = (vb["n"] or 0) if vb else 0
        cbills = (s["bills"] or 0) if s else 0
        summary = {"bills": cbills, "amount": (s["amount"] or 0) if s else 0, "valid_bills": valid,
                   "cancel_rate": round(cbills / (valid + cbills) * 100, 2) if (valid + cbills) else None}
    return {"month": mon, "branches": branches, "reasons": reasons, "summary": summary}


@app.get("/api/extra/cash/detail")
def extra_cash_detail(branch: str = Query(...), month: str = Query(...)):
    """Per-DAY cash over/short for one branch in a month (drill from the by-branch
    table). Cash variance is a drawer-close metric — per-day is its finest grain
    (there is no per-bill over/short)."""
    with get_db() as con:
        rows = [dict(r) for r in con.execute(
            """SELECT x.day, ROUND(x.over_short,2) over_short, ROUND(x.total_sales) sales,
                      x.pax, x.invoices, ROUND(x.discount) discount, ROUND(x.cancel) cancel
               FROM pos_cashcount_day x JOIN branches b ON b.branch_id=x.branch_id
               WHERE b.name=:branch AND substr(x.day,1,7)=:m ORDER BY x.day""",
            {"branch": branch, "m": month}).fetchall()]
    return {"branch": branch, "month": month, "days": rows}


@app.get("/api/extra/cash/day")
def extra_cash_day(branch: str = Query(...), day: str = Query(...)):
    """Single-day cash over/short drill for one branch (click a worst-day row).
    Cash variance has no per-bill grain, so the actionable context is: the day's
    headline (over/short, sales, pax, discount, cancel) + the individual voided
    bills on that exact day — voids are the most common cause of a drawer short.
    Reads only already-staged tables (pos_cashcount_day + pos_cancel_bill_detail);
    cancel_staged=False if the per-bill table predates the current sync."""
    with get_db() as con:
        hd = con.execute(
            """SELECT ROUND(x.over_short,2) over_short, ROUND(x.total_sales) sales,
                      x.pax, x.invoices, ROUND(x.discount) discount, ROUND(x.cancel) cancel
               FROM pos_cashcount_day x JOIN branches b ON b.branch_id=x.branch_id
               WHERE b.name=:branch AND x.day=:day""",
            {"branch": branch, "day": day}).fetchone()
        head = dict(hd) if hd else {}
        try:
            cols = {r[1] for r in con.execute("PRAGMA table_info(pos_cancel_bill_detail)")}
            user_sel = "x.cancel_user" if "cancel_user" in cols else "NULL"
            bills = [dict(r) for r in con.execute(
                f"""SELECT x.sale_date, x.receipt_no, x.amount, x.reason_id,
                       CASE WHEN x.reason_id=0 THEN 'ไม่ระบุเหตุผล'
                            ELSE COALESCE(rr.reason_desc, 'รหัส '||x.reason_id) END reason,
                       {user_sel} AS cancel_user
                    FROM pos_cancel_bill_detail x JOIN branches b ON b.branch_id=x.branch_id
                    LEFT JOIN pos_cancel_reason rr ON rr.reason_id=x.reason_id
                    WHERE b.name=:branch AND substr(x.sale_date,1,10)=:day
                    ORDER BY x.sale_date""", {"branch": branch, "day": day}).fetchall()]
            cancel_staged = True
        except Exception:
            bills, cancel_staged = [], False
    return {"branch": branch, "day": day, "head": head,
            "cancel_bills": bills, "cancel_staged": cancel_staged}


@app.get("/api/extra/cancel-bills/detail")
def extra_cancel_bills_detail(branch: str = Query(...), month: Optional[str] = Query(None),
                              limit: int = Query(400, ge=1, le=2000)):
    """Individual voided/deleted bills for one branch (drill to check each error).
    Reads pos_cancel_bill_detail, staged by sync_mssql from Sale.Active=0. Returns
    staged=False if that table isn't present yet (run a sync to populate)."""
    with get_db() as con:
        try:
            params = {"branch": branch, "lim": limit}
            mfilt = ""
            if month:
                mfilt = " AND x.period_month=:m"; params["m"] = month
            # cancel_user column only exists after a sync with the updated sync_mssql; select it
            # defensively so the drill keeps working on an older DB that predates the column.
            cols = {r[1] for r in con.execute("PRAGMA table_info(pos_cancel_bill_detail)")}
            user_sel = "x.cancel_user" if "cancel_user" in cols else "NULL"
            rows = [dict(r) for r in con.execute(
                f"""SELECT x.sale_date, x.receipt_no, x.amount, x.reason_id,
                       CASE WHEN x.reason_id=0 THEN 'ไม่ระบุเหตุผล'
                            ELSE COALESCE(rr.reason_desc, 'รหัส '||x.reason_id) END reason,
                       {user_sel} AS cancel_user
                    FROM pos_cancel_bill_detail x JOIN branches b ON b.branch_id=x.branch_id
                    LEFT JOIN pos_cancel_reason rr ON rr.reason_id=x.reason_id
                    WHERE b.name=:branch{mfilt}
                    ORDER BY x.sale_date DESC LIMIT :lim""", params).fetchall()]
            return {"branch": branch, "month": month, "bills": rows, "staged": True,
                    "capped": len(rows) >= limit}
        except Exception:
            # pos_cancel_bill_detail not yet staged (older DB) — drill gracefully empty.
            return {"branch": branch, "month": month, "bills": [], "staged": False}


@app.get("/api/analysis/discount-reasons")
def discount_reasons(month_from: Optional[str] = Query(None), month_to: Optional[str] = Query(None),
                     month: Optional[str] = Query(None), branch: Optional[str] = Query(None),
                     group: Optional[str] = Query(None)):
    """Discount breakdown by REASON/TYPE (Sale.DiscountReasonId → DiscountSaleReason),
    over a month range. reason_id 0 = 'ไม่ระบุ' (cashier discounted without picking a
    reason) — usually the biggest leak. Staged by sync into pos_discount_month +
    pos_discount_reason; returns empty/staged=False on a DB synced before they existed."""
    base = ["b.name!='ALL'"]; p: dict = {}
    if branch:
        base.append("b.name=:branch"); p["branch"] = branch
    _group_filter(base, p, group)
    _month_range_filter(base, p, month_from, month_to, month, col="dm.period_month")
    with get_db() as con:
        if not con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='pos_discount_month'").fetchone():
            return {"reasons": [], "total_bills": 0, "total_amount": 0, "untyped_pct": None, "staged": False}
        rows = con.execute(f"""
            SELECT COALESCE(dm.reason_id,0) reason_id,
                   COALESCE(dr.reason_desc, CASE WHEN COALESCE(dm.reason_id,0)=0 THEN 'ไม่ระบุ (ไม่เลือกเหตุผล)'
                                                 ELSE 'รหัส '||dm.reason_id END) reason,
                   SUM(dm.bills) bills, ROUND(SUM(dm.amount)) amount
            FROM pos_discount_month dm JOIN branches b ON b.branch_id=dm.branch_id
            LEFT JOIN pos_discount_reason dr ON dr.reason_id=dm.reason_id
            WHERE {' AND '.join(base)}
            GROUP BY COALESCE(dm.reason_id,0), reason
            ORDER BY amount DESC""", p).fetchall()
        reasons = [dict(r) for r in rows]
        tot_amt = sum(r["amount"] or 0 for r in reasons)
        tot_bills = sum(r["bills"] or 0 for r in reasons)
        for r in reasons:
            r["pct"] = round(100 * (r["amount"] or 0) / tot_amt, 1) if tot_amt else 0
        untyped = next((r for r in reasons if r["reason_id"] == 0), None)
        return {"reasons": reasons, "total_bills": tot_bills, "total_amount": tot_amt,
                "untyped_pct": (round(100 * (untyped["amount"] or 0) / tot_amt, 1) if (untyped and tot_amt) else 0),
                "staged": True}


@app.get("/api/analysis/coupon-series")
def coupon_series_breakdown(month_from: Optional[str] = Query(None), month_to: Optional[str] = Query(None),
                            month: Optional[str] = Query(None), branch: Optional[str] = Query(None),
                            group: Optional[str] = Query(None)):
    """Coupon/promotion breakdown by SERIES, with the human-readable series name
    (CouponSeries master → coupon_series table). pos_coupon_month holds usage per
    branch × month × coupon_series_id; this joins the title so staff see WHICH promo
    (เด็กสูง / ผู้สูงอายุ / วันเกิด / VIP / ผู้ถือหุ้น …) drives the most discount."""
    base = ["b.name!='ALL'"]; p: dict = {}
    if branch:
        base.append("b.name=:branch"); p["branch"] = branch
    _group_filter(base, p, group)
    _month_range_filter(base, p, month_from, month_to, month, col="cm.period_month")
    with get_db() as con:
        if not con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='pos_coupon_month'").fetchone():
            return {"series": [], "total_uses": 0, "total_amount": 0, "series_count": 0, "staged": False}
        has_cs = con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='coupon_series'").fetchone()
        name_c = "COALESCE(NULLIF(TRIM(cs.name),''),'Series '||cm.coupon_series_id)" if has_cs else "'Series '||cm.coupon_series_id"
        code_c = "cs.code" if has_cs else "NULL"
        actv_c = "COALESCE(cs.active,0)" if has_cs else "0"
        join   = "LEFT JOIN coupon_series cs ON cs.coupon_series_id=cm.coupon_series_id" if has_cs else ""
        rows = con.execute(f"""
            SELECT cm.coupon_series_id id, {name_c} name, {code_c} code, {actv_c} active,
                   SUM(cm.used_count) uses, ROUND(SUM(cm.discount_amount)) amount
            FROM pos_coupon_month cm JOIN branches b ON b.branch_id=cm.branch_id {join}
            WHERE {' AND '.join(base)}
            GROUP BY cm.coupon_series_id
            ORDER BY amount DESC""", p).fetchall()
        series = [dict(r) for r in rows]
        tot_amt = sum(r["amount"] or 0 for r in series)
        tot_uses = sum(r["uses"] or 0 for r in series)
        for r in series:
            r["pct"] = round(100 * (r["amount"] or 0) / tot_amt, 1) if tot_amt else 0
            r["avg"] = round((r["amount"] or 0) / r["uses"]) if r["uses"] else 0

        # ── MoM per series: latest month in scope vs the month before it ──
        # (branch/group filtered, but NOT range-clipped, so prev month is available
        #  even when a single month is selected). amount column stays the range total.
        bbase = ["b.name!='ALL'"]; bp: dict = {}
        if branch:
            bbase.append("b.name=:branch"); bp["branch"] = branch
        _group_filter(bbase, bp, group)
        cur_m = month or month_to or con.execute(
            f"SELECT MAX(cm.period_month) m FROM pos_coupon_month cm JOIN branches b ON b.branch_id=cm.branch_id "
            f"WHERE {' AND '.join(bbase)}", bp).fetchone()["m"]
        mom_label = None
        if cur_m:
            yy, mm = int(cur_m[:4]), int(cur_m[5:7]); mm -= 1
            if mm == 0: yy -= 1; mm = 12
            prev_m = f"{yy:04d}-{mm:02d}"
            mom_label = {"cur": cur_m, "prev": prev_m}
            cur_amt, prev_amt = {}, {}
            for r in con.execute(
                f"SELECT cm.coupon_series_id id, cm.period_month ym, SUM(cm.discount_amount) amt "
                f"FROM pos_coupon_month cm JOIN branches b ON b.branch_id=cm.branch_id "
                f"WHERE {' AND '.join(bbase)} AND cm.period_month IN (:cur,:prev) "
                f"GROUP BY cm.coupon_series_id, cm.period_month",
                {**bp, "cur": cur_m, "prev": prev_m}):
                (cur_amt if r["ym"] == cur_m else prev_amt)[r["id"]] = float(r["amt"] or 0)
            for r in series:
                c, pv = cur_amt.get(r["id"], 0.0), prev_amt.get(r["id"], 0.0)
                r["cur_amount"] = round(c)
                r["mom_pct"] = round((c - pv) / pv * 100, 1) if pv > 0 else (None if c == 0 else None)
        return {"series": series, "total_uses": tot_uses, "total_amount": tot_amt,
                "series_count": len(series), "mom": mom_label, "staged": True}


@app.get("/api/analysis/coupon-series/drill")
def coupon_series_drill(series_id: int, by: str = Query("branch"),
                        month_from: Optional[str] = Query(None), month_to: Optional[str] = Query(None),
                        month: Optional[str] = Query(None), branch: Optional[str] = Query(None),
                        group: Optional[str] = Query(None)):
    """Drill one coupon series → usage by BRANCH (from pos_coupon_month) or by DAY
    (from pos_coupon_day, populated by the sync). Honours the same month/branch
    filters as the parent table."""
    by = "day" if by == "day" else "branch"
    with get_db() as con:
        nm = con.execute("SELECT name, code FROM coupon_series WHERE coupon_series_id=?", (series_id,)).fetchone() \
             if con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='coupon_series'").fetchone() else None
        name = (nm["name"] if nm and (nm["name"] or "").strip() else f"Series {series_id}")
        if by == "day":
            if not con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='pos_coupon_day'").fetchone():
                return {"by": "day", "series_id": series_id, "name": name, "rows": [], "available": False}
            base = ["b.name!='ALL'", "cd.coupon_series_id=:sid"]; p = {"sid": series_id}
            if branch: base.append("b.name=:branch"); p["branch"] = branch
            _group_filter(base, p, group)
            _month_range_filter(base, p, month_from, month_to, month, col="substr(cd.day,1,7)")
            rows = [dict(r) for r in con.execute(
                f"SELECT cd.day label, SUM(cd.used_count) uses, ROUND(SUM(cd.discount_amount)) amount "
                f"FROM pos_coupon_day cd JOIN branches b ON b.branch_id=cd.branch_id "
                f"WHERE {' AND '.join(base)} GROUP BY cd.day ORDER BY cd.day", p)]
            return {"by": "day", "series_id": series_id, "name": name, "rows": rows, "available": True}
        # by branch
        base = ["b.name!='ALL'", "cm.coupon_series_id=:sid"]; p = {"sid": series_id}
        if branch: base.append("b.name=:branch"); p["branch"] = branch
        _group_filter(base, p, group)
        _month_range_filter(base, p, month_from, month_to, month, col="cm.period_month")
        rows = [dict(r) for r in con.execute(
            f"SELECT b.name label, SUM(cm.used_count) uses, ROUND(SUM(cm.discount_amount)) amount "
            f"FROM pos_coupon_month cm JOIN branches b ON b.branch_id=cm.branch_id "
            f"WHERE {' AND '.join(base)} GROUP BY b.name ORDER BY amount DESC", p)]
        return {"by": "branch", "series_id": series_id, "name": name, "rows": rows, "available": True}


@app.get("/api/extra/closing-time")
def extra_closing_time(branch: Optional[str] = Query(None), group: Optional[str] = Query(None),
                       date: Optional[str] = Query(None)):
    """Per-branch data freshness + typical shop closing time.

    Surfaces the data-lag issue: each branch's LATEST transaction (all-time) shows
    how current its synced data is; a branch sitting several days behind the chain
    maximum has not reported / synced recent sales. The typical closing time (avg
    of each day's last bill over the trailing 30 days) is the operational read —
    times before 06:00 are treated as after-midnight closes (+24h) so a 01:30 close
    sorts after a 23:00 one rather than looking like the earliest open.

    When ``date`` (YYYY-MM-DD) is given, switches to single-day mode: for that exact
    date it shows each branch's last-bill time + bill count and flags branches that
    posted NO data that day (mode='date'). Use it to inspect a specific day's gaps,
    e.g. which branches reported on 2026-06-04."""
    filt = ["b.name!='ALL'"]; p: dict = {}
    if branch: filt.append("b.name=:branch"); p["branch"] = branch
    _group_filter(filt, p, group)
    tw = " AND ".join(filt)
    import datetime as _dt
    with get_db() as con:
        bounds = con.execute("SELECT MAX(sold_at) mx, MIN(sold_at) mn FROM sales_transaction").fetchone()
        dm = bounds["mx"]
        if not dm:
            return {"data_max": None, "today": _dt.date.today().isoformat(),
                    "branches": [], "summary": {}}
        data_max_date = dm[:10]
        data_min_date = (bounds["mn"] or dm)[:10]
        d_hi = _dt.date(*(int(x) for x in data_max_date.split("-")))
        since = (d_hi - _dt.timedelta(days=30)).isoformat()

        # All-time latest per branch (freshness) + lifetime bill count. This is also
        # the branch universe — so single-day mode can flag branches with NO data.
        last_rows = {r["branch"]: r for r in con.execute(
            f"""SELECT b.name branch, MAX(t.sold_at) last_sold, COUNT(*) total_bills
                FROM sales_transaction t JOIN branches b ON b.branch_id=t.branch_id
                WHERE {tw} GROUP BY b.name""", p).fetchall()}

        # ── Single-day mode ──────────────────────────────────────────────────
        if date:
            day_rows = {r["branch"]: r for r in con.execute(
                f"""SELECT b.name branch, MAX(t.sold_at) day_last, COUNT(*) day_bills
                    FROM sales_transaction t JOIN branches b ON b.branch_id=t.branch_id
                    WHERE {tw} AND date(t.sold_at)=:date
                    GROUP BY b.name""", {**p, "date": date}).fetchall()}
            out = []; reported = missing = 0; day_bill_total = 0
            for name, lr in last_rows.items():
                dr = day_rows.get(name)
                day_bills = (dr["day_bills"] if dr else 0) or 0
                day_last = dr["day_last"] if dr else None
                day_close = None
                if day_last:
                    day_close = f"{day_last[11:13]}:{day_last[14:16]}"
                rep = day_bills > 0
                reported += int(rep); missing += int(not rep); day_bill_total += day_bills
                out.append({"branch": name, "day_bills": day_bills, "day_last": day_last,
                            "day_close": day_close, "last_sold": lr["last_sold"],
                            "status": "reported" if rep else "missing"})
            # Missing branches first (the gaps you came to see), then by name.
            out.sort(key=lambda r: (r["status"] != "missing", r["branch"]))
            return {"mode": "date", "date": date, "data_max": dm,
                    "data_max_date": data_max_date, "data_min_date": data_min_date,
                    "today": _dt.date.today().isoformat(), "branches": out,
                    "summary": {"reported": reported, "missing": missing,
                                "branch_count": len(out), "day_bills": day_bill_total}}

        # Trailing-30d typical close: average of each day's last-bill time-of-day.
        close_rows = {r["branch"]: r for r in con.execute(
            f"""WITH day_close AS (
                  SELECT t.branch_id, date(t.sold_at) d, MAX(t.sold_at) last_at
                  FROM sales_transaction t JOIN branches b ON b.branch_id=t.branch_id
                  WHERE {tw} AND t.sold_at >= :since
                  GROUP BY t.branch_id, date(t.sold_at)
                )
                SELECT b.name branch, COUNT(*) days_open,
                  AVG( CAST(substr(dc.last_at,12,2) AS REAL)*60 + CAST(substr(dc.last_at,15,2) AS REAL)
                       + CASE WHEN CAST(substr(dc.last_at,12,2) AS INTEGER) < 6 THEN 1440 ELSE 0 END
                     ) avg_close_min
                FROM day_close dc JOIN branches b ON b.branch_id=dc.branch_id
                GROUP BY b.name""", {**p, "since": since}).fetchall()}

    branches = []
    behind = current = 0
    max_behind = 0
    for name, lr in last_rows.items():
        last_sold = lr["last_sold"]
        last_date = last_sold[:10] if last_sold else None
        days_behind = None
        if last_date:
            d_b = _dt.date(*(int(x) for x in last_date.split("-")))
            days_behind = (d_hi - d_b).days
        cr = close_rows.get(name)
        avg_min = round(cr["avg_close_min"]) if cr and cr["avg_close_min"] is not None else None
        avg_close = None
        if avg_min is not None:
            hh = (avg_min // 60) % 24; mm = avg_min % 60
            avg_close = f"{hh:02d}:{mm:02d}"
        if days_behind == 0:
            status = "current"; current += 1
        elif days_behind is not None and days_behind >= 14:
            status = "inactive"; behind += 1
        else:
            status = "behind"; behind += 1
        if days_behind is not None:
            max_behind = max(max_behind, days_behind)
        branches.append({
            "branch": name, "last_sold": last_sold, "last_date": last_date,
            "days_behind": days_behind, "avg_close": avg_close, "avg_close_min": avg_min,
            "days_open": (cr["days_open"] if cr else 0), "total_bills": lr["total_bills"],
            "status": status,
        })
    # Most-stale first so problems sit at the top.
    branches.sort(key=lambda r: (-(r["days_behind"] if r["days_behind"] is not None else 9999)))
    summary = {"behind": behind, "current": current, "max_days_behind": max_behind,
               "branch_count": len(branches)}
    return {"mode": "latest", "data_max": dm, "data_max_date": data_max_date,
            "data_min_date": data_min_date, "today": _dt.date.today().isoformat(),
            "branches": branches, "summary": summary}


@app.get("/api/buffet/child")
@ttl_cache
def buffet_child(branch: Optional[str] = Query(None)):
    """Child-buffet (BUFFETCHILD) summary per branch: latest-month covers,
    revenue, % of buffet covers, MoM vs previous month, plus a chain-wide
    monthly trend for the YTD line. (There is no senior/age discount data, and
    'child' is a buffet TIER, not a tagged discount — this is the closest
    available signal.)"""
    bfilt = ["b.name != 'ALL'"]
    bparams: dict = {}
    if branch:
        bfilt.append("b.name = :branch")
        bparams["branch"] = branch
    where = "WHERE " + " AND ".join(bfilt + ["sk.sku_code IN ('BUFFETCHILD','BUFFET')"])
    with get_db() as con:
        rows = con.execute(f"""
            SELECT b.name AS branch, sm.period_month AS m, sk.sku_code AS code,
                   ROUND(SUM(sm.qty), 0)             AS qty,
                   ROUND(SUM(sm.subtotal_ex_vat), 2) AS rev
            FROM sales_sku_monthly sm
            JOIN branches b ON b.branch_id = sm.branch_id
            JOIN skus sk    ON sk.sku_id   = sm.sku_id
            {where}
            GROUP BY b.name, sm.period_month, sk.sku_code
        """, bparams).fetchall()

    from collections import defaultdict
    # per branch -> month -> {child_qty, child_rev, adult_qty}
    bm: dict = defaultdict(lambda: defaultdict(lambda: {"cq": 0.0, "cr": 0.0, "aq": 0.0}))
    trend_m: dict = defaultdict(lambda: {"covers": 0.0, "revenue": 0.0})
    months: set = set()
    for r in rows:
        months.add(r["m"])
        cell = bm[r["branch"]][r["m"]]
        if r["code"] == "BUFFETCHILD":
            cell["cq"] += r["qty"] or 0; cell["cr"] += r["rev"] or 0
            tm = trend_m[r["m"]]; tm["covers"] += r["qty"] or 0; tm["revenue"] += r["rev"] or 0
        else:
            cell["aq"] += r["qty"] or 0

    months_sorted = sorted(months)
    latest = months_sorted[-1] if months_sorted else None
    prev   = months_sorted[-2] if len(months_sorted) > 1 else None

    by_branch = []
    for br, mm in bm.items():
        last = mm.get(latest, {"cq": 0, "cr": 0, "aq": 0}) if latest else {"cq": 0, "cr": 0, "aq": 0}
        pv   = mm.get(prev,   {"cq": 0, "cr": 0, "aq": 0}) if prev   else {"cq": 0, "cr": 0, "aq": 0}
        covers = last["cq"]; prev_covers = pv["cq"]
        tot_buffet = last["cq"] + last["aq"]
        by_branch.append({
            "branch":        br,
            "covers":        round(covers),
            "revenue":       round(last["cr"], 2),
            "prev_covers":   round(prev_covers),
            "pct_of_buffet": round(last["cq"] / tot_buffet * 100, 1) if tot_buffet else None,
            "mom_pct":       round((covers - prev_covers) / prev_covers * 100, 1) if prev_covers > 0 else None,
        })
    by_branch.sort(key=lambda x: -x["covers"])

    trend = [{"month": m, "covers": round(trend_m[m]["covers"]),
              "revenue": round(trend_m[m]["revenue"], 2)} for m in months_sorted]
    return {"latest_month": latest, "prev_month": prev,
            "by_branch": by_branch, "trend": trend}


# ---------------------------------------------------------------------------
# /api/summary   -  single-call KPI card data
# ---------------------------------------------------------------------------
@app.get("/api/summary")
def summary(
    month:      Optional[str] = Query(None),
    month_from: Optional[str] = Query(None),
    month_to:   Optional[str] = Query(None),
    date_from:  Optional[str] = Query(None),   # YYYY-MM-DD — daily/weekly window
    date_to:    Optional[str] = Query(None),   # YYYY-MM-DD — daily/weekly window
    branch:     Optional[str] = Query(None),
    group:      Optional[str] = Query(None),
):
    """
    High-level KPIs for the header cards:
    total revenue, transaction count, avg ticket, customer count.
    If month is omitted, returns all-time totals.

    When date_from/date_to are supplied (daily/weekly granularity), the KPIs are
    computed at day resolution straight from sales_transaction (Easy-only — the
    only day-resolved source). The monthly tables have no sub-month resolution,
    so they cannot answer a Day/Week window.
    """
    # ── date-window mode (daily/weekly) — transaction-level, Easy-only ──────
    if date_from or date_to:
        filters = ["b.name != 'ALL'", "st.source = 'Easy'"]
        params: dict = {}
        if branch:
            filters.append("b.name = :branch")
            params["branch"] = branch
        _group_filter(filters, params, group)
        if date_from:
            filters.append("DATE(st.sold_at) >= :date_from")
            params["date_from"] = date_from
        if date_to:
            filters.append("DATE(st.sold_at) <= :date_to")
            params["date_to"] = date_to
        where = "WHERE " + " AND ".join(filters)
        with get_db() as con:
            r = con.execute(
                f"""
                SELECT SUM(st.total_inc_vat)   AS rev_inc,
                       COUNT(*)                AS tx,
                       SUM(st.customer_count)  AS cust
                FROM sales_transaction st
                JOIN branches b ON b.branch_id = st.branch_id
                {where}
                """,
                params,
            ).fetchone()
        rev_inc = r["rev_inc"] or 0.0
        tx      = r["tx"] or 0
        cust    = r["cust"] or 0
        rev_ex  = ex_vat(rev_inc)          # VAT is never revenue
        return {
            "revenue":         round(rev_ex, 2),
            "revenue_ex_vat":  round(rev_ex, 2),
            "revenue_inc_vat": round(rev_inc, 2),
            "tx_count":        tx,
            "customer_count":  cust,
            "avg_ticket":      round(rev_ex / tx, 2) if tx else 0,
        }

    filters_sku = ["b.name != 'ALL'", "sm.source = 'Easy'"]   # fix B4
    filters_tx  = ["b.name != 'ALL'"]
    params_sku: dict = {}
    params_tx:  dict = {}

    if branch:
        filters_sku.append("b.name = :branch")
        filters_tx.append("b.name = :branch")
        params_sku["branch"] = branch
        params_tx["branch"]  = branch
    _group_filter(filters_sku, params_sku, group)
    _group_filter(filters_tx,  params_tx,  group)
    _month_range_filter(filters_sku, params_sku, month_from, month_to, month, col="sm.period_month")
    _month_range_filter(filters_tx,  params_tx,  month_from, month_to, month, col="strftime('%Y-%m', st.sold_at)")

    where_sku = "WHERE " + " AND ".join(filters_sku)
    where_tx  = "WHERE " + " AND ".join(filters_tx)

    # month_summary filter (Easy — used as a revenue/tx fallback for any
    # branch×month missing from sku_monthly / sales_transaction)
    filters_ms = ["b.name != 'ALL'"]
    params_ms: dict = {}
    if branch:
        filters_ms.append("b.name = :branch")
        params_ms["branch"] = branch
    _group_filter(filters_ms, params_ms, group)
    _month_range_filter(filters_ms, params_ms, month_from, month_to, month, col="sms.period_month")
    where_ms = "WHERE " + " AND ".join(filters_ms)

    with get_db() as con:
        # SKU-level revenue per (branch, month) — primary, preferred when available
        sku_rev_rows = con.execute(
            f"""
            SELECT b.name AS branch, sm.period_month AS m,
                   SUM(sm.total_inc_vat) AS rev_inc,
                   SUM(sm.subtotal_ex_vat) AS rev_ex
            FROM sales_sku_monthly sm
            JOIN branches b ON b.branch_id = sm.branch_id
            {where_sku}
            GROUP BY b.name, sm.period_month
            """,
            params_sku,
        ).fetchall()

        # Month summary as FALLBACK for branches/months not in sku_monthly
        ms_rev_rows = con.execute(
            f"""
            SELECT b.name AS branch, sms.period_month AS m,
                   SUM(sms.net_sales) AS net,
                   SUM(sms.transaction_count) AS tx,
                   SUM(sms.customer_count) AS cust
            FROM sales_month_summary sms
            JOIN branches b ON b.branch_id = sms.branch_id
            {where_ms}
            GROUP BY b.name, sms.period_month
            """,
            params_ms,
        ).fetchall()

        # Easy transactions (Easy-only)
        easy_tx_rows = con.execute(
            f"""
            SELECT b.name AS branch, strftime('%Y-%m', st.sold_at) AS m,
                   COUNT(*) AS tx, SUM(customer_count) AS cust,
                   SUM(total_inc_vat) AS rev_inc
            FROM sales_transaction st
            JOIN branches b ON b.branch_id = st.branch_id
            {where_tx}
            GROUP BY b.name, strftime('%Y-%m', st.sold_at)
            """,
            params_tx,
        ).fetchall()

    # Build per-(branch, month) merged map
    sku_map = {(r["branch"], r["m"]): {"rev_inc": r["rev_inc"] or 0, "rev_ex": r["rev_ex"] or 0} for r in sku_rev_rows}
    easy_tx_map = {(r["branch"], r["m"]): {"tx": r["tx"] or 0, "cust": r["cust"] or 0, "rev_inc": r["rev_inc"] or 0} for r in easy_tx_rows}

    # Aggregate totals
    total_rev_inc = 0.0
    total_rev_ex  = 0.0
    total_tx      = 0
    total_cust    = 0

    # Track (branch, month) we've covered to avoid double-count
    covered = set()
    for (br, m), v in sku_map.items():
        total_rev_inc += v["rev_inc"]
        total_rev_ex  += v["rev_ex"]
        covered.add((br, m))

    for r in ms_rev_rows:
        k = (r["branch"], r["m"])
        if k in covered:
            continue  # sku_monthly already counted this branch×month
        # net_sales is already VAT-EXCLUSIVE (ingest hard rule); reconstruct inc-VAT for the inc total (fix: B1)
        _net = r["net"] or 0
        total_rev_ex  += _net
        total_rev_inc += _net * 1.07
        covered.add(k)

    # Tx + customers
    # For each (br, m): use transaction-level Easy counts when available, otherwise
    # fall back to the monthly summary aggregate (some months were only loaded monthly).
    ms_tx_by_key = {}
    for r in ms_rev_rows:
        k = (r["branch"], r["m"])
        ms_tx_by_key.setdefault(k, []).append({"tx": r["tx"] or 0, "cust": r["cust"] or 0})

    all_keys = set(easy_tx_map.keys()) | set(ms_tx_by_key.keys())
    for k in all_keys:
        e = easy_tx_map.get(k)
        if e and e["tx"] > 0:
            total_tx   += e["tx"]
            total_cust += e["cust"]
            continue
        # No transaction-level rows — fall back to the monthly summary aggregate
        for ms in ms_tx_by_key.get(k, []):
            total_tx   += ms["tx"]
            total_cust += ms["cust"]
            break

    # Hard rule: VAT is never revenue. Headline revenue + avg ticket are ex-VAT.
    avg_ticket = round(total_rev_ex / total_tx, 2) if total_tx else 0

    return {
        "revenue":         round(total_rev_ex, 2),   # ex-VAT (headline)
        "revenue_ex_vat":  round(total_rev_ex, 2),   # back-compat alias
        "revenue_inc_vat": round(total_rev_inc, 2),  # POS reconciliation only
        "tx_count":        total_tx,
        "customer_count":  total_cust,
        "avg_ticket":      avg_ticket,                # ex-VAT
    }


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


# ---------------------------------------------------------------------------
# /api/sales/margin   -  per-category margin for a period
# ---------------------------------------------------------------------------
@app.get("/api/sales/margin")
@ttl_cache
def sales_margin(
    month:      Optional[str] = Query(None),
    month_from: Optional[str] = Query(None),
    month_to:   Optional[str] = Query(None),
    branch:     Optional[str] = Query(None),
):
    """Per-category margin. Requires sku_cost to be populated."""
    filters = ["b.name != 'ALL'", "sm.source = 'Easy'"]   # fix B4
    params: dict = {}

    if branch:
        filters.append("b.name = :branch")
        params["branch"] = branch
    _month_range_filter(filters, params, month_from, month_to, month, col="sm.period_month")

    where = "WHERE " + " AND ".join(filters)

    with get_db() as con:
        rows = con.execute(
            f"""
            SELECT c.name AS category,
                   ROUND(SUM(sm.subtotal_ex_vat), 2)                       AS revenue,
                   ROUND(SUM({_cogs_case(ssm='sm', sk='sk', sc='sc')}), 2) AS cost,
                   COUNT(DISTINCT CASE WHEN {_effective_cost_subquery(ssm='sm', sc='sc')} IS NULL
                                       THEN sk.sku_id END)                  AS uncosted_skus
            FROM sales_sku_monthly sm
            JOIN branches b   ON b.branch_id   = sm.branch_id
            JOIN skus sk      ON sk.sku_id      = sm.sku_id
            JOIN categories c ON c.category_id  = sk.category_id
            LEFT JOIN sku_cost sc ON sc.sku_id  = sm.sku_id
            {where}
            GROUP BY c.name
            ORDER BY revenue DESC
            """,
            params,
        ).fetchall()

        uncosted_total = con.execute(
            f"""
            SELECT COUNT(DISTINCT CASE WHEN {_effective_cost_subquery(ssm='sm', sc='sc')} IS NULL THEN sk.sku_id END)
            FROM sales_sku_monthly sm
            JOIN branches b   ON b.branch_id   = sm.branch_id
            JOIN skus sk      ON sk.sku_id      = sm.sku_id
            LEFT JOIN sku_cost sc ON sc.sku_id  = sm.sku_id
            {where}
            """,
            params,
        ).fetchone()[0] or 0

    by_category = []
    total_rev = 0.0
    total_cost = 0.0

    for r in rows:
        rev  = r["revenue"] or 0.0
        cost = r["cost"] or 0.0
        margin = round(rev - cost, 2)
        margin_pct = round(margin / rev * 100, 1) if rev else 0.0
        total_rev  += rev
        total_cost += cost
        by_category.append({
            "category":     r["category"],
            "revenue":      rev,
            "cost":         cost,
            "margin":       margin,
            "margin_pct":   margin_pct,
            "uncosted_skus": r["uncosted_skus"] or 0,
        })

    total_rev   = round(total_rev, 2)
    total_cost  = round(total_cost, 2)
    total_margin = round(total_rev - total_cost, 2)
    total_pct    = round(total_margin / total_rev * 100, 1) if total_rev else 0.0

    return {
        "by_category": by_category,
        "total": {
            "revenue":    total_rev,
            "cost":       total_cost,
            "margin":     total_margin,
            "margin_pct": total_pct,
        },
        "uncosted_sku_count": uncosted_total,
    }


def _month_add(ym: str, delta: int) -> str:
    """'YYYY-MM' shifted by `delta` months (delta may be negative)."""
    y, m = _ym_parts(ym)
    idx = (y * 12 + (m - 1)) + delta
    return f"{idx // 12:04d}-{idx % 12 + 1:02d}"


# ---------------------------------------------------------------------------
# /api/sales/dish-movers  -  per-DISH MoM movers (food menu items)
# ---------------------------------------------------------------------------
@app.get("/api/sales/dish-movers")
@ttl_cache
def sales_dish_movers(
    month:      Optional[str] = Query(None),
    month_from: Optional[str] = Query(None),
    month_to:   Optional[str] = Query(None),
    vs_month:   Optional[str] = Query(None, description="explicit comparison month (overrides auto-previous)"),
    branch:     Optional[str] = Query(None),
    metric:     str           = Query("qty", description="'qty' or 'revenue'"),
    limit:      int           = Query(8, ge=1, le=30),
):
    """Which FOOD DISHES rose/fell most vs a comparison period.

    Per-dish (not category) and food-only (uses _FOOD_CATEGORY_FILTER), so it
    surfaces actionable menu movement. Default metric = qty (orders), which is
    buffet-aware (buffet dishes have ~0 à-la-carte revenue). Set metric=revenue
    for à-la-carte revenue movement.

    Comparison period: by default the previous equal-length period (MoM). Pass
    ``vs_month`` to compare the current month against a SPECIFIC chosen month
    instead (the month-vs-month picker on the Top Menu Movers card).
    """
    cur_to   = month_to or month
    cur_from = month_from or month
    if not cur_to:
        with get_db() as con:
            cur_to = con.execute(
                "SELECT MAX(period_month) FROM sales_sku_monthly WHERE source='Easy'"
            ).fetchone()[0]
        cur_from = cur_to
    if not cur_from:
        cur_from = cur_to

    fy, fm = _ym_parts(cur_from); ty, tm = _ym_parts(cur_to)
    span = (ty * 12 + tm) - (fy * 12 + fm)
    if vs_month:
        # Explicit month-vs-month: compare against the chosen month exactly.
        prev_from = prev_to = vs_month
    else:
        prev_to   = _month_add(cur_from, -1)
        prev_from = _month_add(prev_to, -span)

    val_col = "ssm.qty" if metric == "qty" else "ssm.subtotal_ex_vat"

    def _dish_vals(mf: str, mt: str) -> dict:
        filters = ["b.name != 'ALL'", "ssm.source = 'Easy'",
                   "ssm.period_month >= :mf", "ssm.period_month <= :mt",
                   _FOOD_CATEGORY_FILTER]
        params = {"mf": mf, "mt": mt}
        if branch:
            filters.append("b.name = :branch"); params["branch"] = branch
        where = "WHERE " + " AND ".join(filters)
        with get_db() as con:
            rows = con.execute(
                f"""SELECT sk.sku_code AS code,
                           COALESCE(sk.description, sk.sku_code) AS name,
                           c.name AS category,
                           SUM({val_col}) AS val
                    FROM sales_sku_monthly ssm
                    JOIN branches b   ON b.branch_id   = ssm.branch_id
                    JOIN skus sk      ON sk.sku_id      = ssm.sku_id
                    JOIN categories c ON c.category_id  = sk.category_id
                    {where}
                    GROUP BY sk.sku_id""",
                params,
            ).fetchall()
        return {r["code"]: {"name": r["name"], "category": r["category"],
                            "val": r["val"] or 0} for r in rows}

    cur_v  = _dish_vals(cur_from, cur_to)
    prev_v = _dish_vals(prev_from, prev_to)

    movers = []
    for code in set(cur_v) | set(prev_v):
        now = cur_v.get(code, {}).get("val", 0)
        was = prev_v.get(code, {}).get("val", 0)
        meta = cur_v.get(code) or prev_v.get(code)
        delta = round(now - was, 2)
        if delta == 0:
            continue
        pct = round(delta / was * 100, 1) if was > 0 else None
        movers.append({
            "code": code, "name": meta["name"], "category": meta["category"],
            "now": round(now, 2), "was": round(was, 2),
            "delta": delta, "delta_pct": pct, "is_new": was == 0 and now > 0,
        })

    movers.sort(key=lambda m: abs(m["delta"]), reverse=True)
    top = movers[:limit]
    # Independent top gainers / decliners (not a shared abs-delta top-N, which
    # was all gainers for high-volume dishes → empty decliners).
    gainers   = sorted([m for m in movers if m["delta"] > 0], key=lambda m: m["delta"], reverse=True)[:limit]
    decliners = sorted([m for m in movers if m["delta"] < 0], key=lambda m: m["delta"])[:limit]
    return {
        "metric":   metric,
        "current":  {"from": cur_from, "to": cur_to},
        "previous": {"from": prev_from, "to": prev_to},
        "gainers":   gainers,
        "decliners": decliners,
    }


# ---------------------------------------------------------------------------
# /api/buffet/tier-mix
# ---------------------------------------------------------------------------
@app.get("/api/buffet/tier-mix")
@ttl_cache
def buffet_tier_mix(
    branch:     Optional[str] = Query(None),
    month_from: Optional[str] = Query(None),
    month_to:   Optional[str] = Query(None),
    month:      Optional[str] = Query(None),
):
    """Revenue and GP breakdown by buffet tier per month."""
    filters: list = [
        "c.name = 'BUFFET'"
    ]
    params: dict = {}

    if branch:
        filters.append("b.name = :branch")
        params["branch"] = branch

    _month_range_filter(filters, params, month_from, month_to, month, col="sm.period_month")

    where = "WHERE " + " AND ".join(filters) if filters else ""

    sql = f"""
        SELECT
            sm.period_month,
            sk.sku_code,
            sk.description,
            SUM(sm.qty)               AS qty,
            SUM(sm.subtotal_ex_vat)     AS revenue,
            SUM(sm.subtotal_ex_vat)   AS revenue_ex_vat,
            SUM({_cogs_case(ssm='sm', sk='sk', sc='sc')}) AS cogs_total,
            SUM(CASE WHEN {_effective_cost_subquery(ssm='sm', sc='sc')} IS NOT NULL
                     THEN sm.qty ELSE 0 END)                AS costed_qty
        FROM sales_sku_monthly sm
        JOIN branches b   ON b.branch_id = sm.branch_id
        JOIN skus sk      ON sk.sku_id   = sm.sku_id
        JOIN categories c ON c.category_id = sk.category_id
        LEFT JOIN sku_cost sc ON sc.sku_id = sm.sku_id
        {where}
        GROUP BY sm.period_month, sk.sku_code, sk.description
        ORDER BY sm.period_month, revenue DESC
    """

    with get_db() as con:
        rows = con.execute(sql, params).fetchall()

    # Build per-period detail + summary
    from collections import defaultdict
    period_rows: dict = defaultdict(list)
    period_totals: dict = defaultdict(lambda: {"qty": 0, "revenue": 0.0, "revenue_ex_vat": 0.0, "cogs": 0.0, "gp": None, "has_cost": True})

    result_rows = []
    for r in rows:
        qty          = r["qty"] or 0
        revenue      = round(r["revenue"] or 0.0, 2)
        rev_ex       = round(r["revenue_ex_vat"] or 0.0, 2)
        cogs_total   = r["cogs_total"]
        costed_qty   = r["costed_qty"] or 0
        has_cost     = cogs_total is not None and costed_qty > 0
        unit_cost    = (cogs_total / costed_qty) if has_cost else None
        cogs         = round(cogs_total, 2) if has_cost else None
        gp           = round(rev_ex - cogs, 2)   if cogs is not None else None
        gp_per_cover = round(gp / qty, 2)         if (gp is not None and qty) else None

        row_dict = {
            "period_month":  r["period_month"],
            "sku_code":      r["sku_code"],
            "description":   r["description"],
            "qty":           qty,
            "revenue":       revenue,
            "revenue_ex_vat": rev_ex,
            "unit_cost":     unit_cost,
            "cogs":          cogs,
            "gp":            gp,
            "gp_per_cover":  gp_per_cover,
        }
        result_rows.append(row_dict)

        pt = period_totals[r["period_month"]]
        pt["qty"]          += qty
        pt["revenue"]      += revenue
        pt["revenue_ex_vat"] += rev_ex
        if cogs is not None:
            pt["cogs"] += cogs
        else:
            pt["has_cost"] = False

    totals_by_period = []
    for pm, pt in sorted(period_totals.items()):
        cogs_total = round(pt["cogs"], 2) if pt["has_cost"] else None
        gp_total   = round(pt["revenue_ex_vat"] - cogs_total, 2) if cogs_total is not None else None
        totals_by_period.append({
            "period_month":  pm,
            "qty":           pt["qty"],
            "revenue":       round(pt["revenue"], 2),
            "revenue_ex_vat": round(pt["revenue_ex_vat"], 2),
            "cogs":          cogs_total,
            "gp":            gp_total,
        })

    return {"rows": result_rows, "totals_by_period": totals_by_period}


# ---------------------------------------------------------------------------
# /api/buffet/cover-economics
# ---------------------------------------------------------------------------
@app.get("/api/buffet/cover-economics")
@ttl_cache
def buffet_cover_economics(
    branch:     Optional[str] = Query(None),
    month_from: Optional[str] = Query(None),
    month_to:   Optional[str] = Query(None),
    month:      Optional[str] = Query(None),
    date_from:  Optional[str] = Query(None),
    date_to:    Optional[str] = Query(None),
):
    """Party-size distribution, hourly covers, and table-zone stats for buffet transactions."""
    tx_filters: list = [
        "t.customer_count BETWEEN 1 AND 20",
        "t.source = 'Easy'",
    ]
    tx_params: dict = {}

    if branch:
        tx_filters.append("b.name = :branch")
        tx_params["branch"] = branch

    # Date-range filter on sold_at
    if date_from or month_from or month:
        df = date_from or (month_from or month) + "-01"
        tx_filters.append("DATE(t.sold_at) >= :date_from")
        tx_params["date_from"] = df
    if date_to or month_to or month:
        # Use end-of-month if only month given
        dt_val = date_to
        if not dt_val:
            m = month_to or month
            import calendar as _cal
            y, mo = int(m[:4]), int(m[5:7])
            last_day = _cal.monthrange(y, mo)[1]
            dt_val = f"{m}-{last_day:02d}"
        tx_filters.append("DATE(t.sold_at) <= :date_to")
        tx_params["date_to"] = dt_val

    tx_where = "WHERE " + " AND ".join(tx_filters) if tx_filters else ""

    with get_db() as con:
        # 1. Party size distribution
        party_rows = con.execute(
            f"""
            SELECT
                t.customer_count                   AS party_size,
                COUNT(*)                           AS tx_count,
                SUM(t.total_inc_vat) / 1.07        AS revenue,
                AVG(t.total_inc_vat / 1.07 / t.customer_count) AS avg_revenue_per_cover,
                AVG(t.total_inc_vat / 1.07)        AS avg_ticket
            FROM sales_transaction t
            JOIN branches b ON b.branch_id = t.branch_id
            {tx_where}
            GROUP BY t.customer_count
            ORDER BY t.customer_count
            """,
            tx_params,
        ).fetchall()

        # 2. Hourly covers
        hourly_rows = con.execute(
            f"""
            SELECT
                CAST(strftime('%H', t.sold_at) AS INTEGER) AS hour,
                SUM(t.customer_count)                      AS total_covers,
                COUNT(*)                                   AS tx_count,
                AVG(t.customer_count)                      AS avg_covers_per_tx,
                SUM(t.total_inc_vat) / 1.07                AS revenue
            FROM sales_transaction t
            JOIN branches b ON b.branch_id = t.branch_id
            {tx_where}
            GROUP BY hour
            ORDER BY hour
            """,
            tx_params,
        ).fetchall()

        # 3. Table zone stats
        zone_rows = con.execute(
            f"""
            SELECT
                CASE
                    WHEN t.table_no LIKE 'T%' THEN 'Dine-in'
                    WHEN t.table_no LIKE 'G%' THEN 'VIP'
                    WHEN t.table_no LIKE 'C%' THEN 'Counter'
                    WHEN t.table_no LIKE 'Take%' THEN 'Takeaway'
                    ELSE 'Other'
                END AS zone,
                COUNT(*)              AS tx_count,
                SUM(t.customer_count) AS total_covers,
                SUM(t.total_inc_vat) / 1.07 AS revenue,
                AVG(t.total_inc_vat / 1.07) AS avg_ticket,
                AVG(t.customer_count) AS avg_covers
            FROM sales_transaction t
            JOIN branches b ON b.branch_id = t.branch_id
            {tx_where}
            GROUP BY zone
            ORDER BY revenue DESC
            """,
            tx_params,
        ).fetchall()

        # 4. Summary totals
        summary_row = con.execute(
            f"""
            SELECT
                AVG(t.customer_count)               AS avg_party_size,
                SUM(t.total_inc_vat) / 1.07 / NULLIF(SUM(t.customer_count), 0) AS avg_revenue_per_cover,
                SUM(t.customer_count)               AS total_covers,
                COUNT(*)                            AS total_tx
            FROM sales_transaction t
            JOIN branches b ON b.branch_id = t.branch_id
            {tx_where}
            """,
            tx_params,
        ).fetchone()

        # 5. Child cover estimate from sales_sku_monthly
        child_filters: list = []
        child_params: dict = {}
        if branch:
            child_filters.append("b.name = :branch")
            child_params["branch"] = branch
        _month_range_filter(child_filters, child_params, month_from, month_to, month, col="sm.period_month")
        child_filters.append(
            "(sk.sku_code LIKE 'B04%' OR sk.sku_code LIKE 'B05%' OR sk.sku_code LIKE 'B12%' "
            "OR sk.sku_code LIKE 'BUFFETCHILD%' "
            "OR LOWER(sk.description) LIKE LOWER('%child%') "
            "OR LOWER(sk.description) LIKE LOWER('%เด็ก%'))"
        )
        child_where = "WHERE " + " AND ".join(child_filters) if child_filters else ""
        child_row = con.execute(
            f"""
            SELECT SUM(sm.qty) AS child_qty
            FROM sales_sku_monthly sm
            JOIN branches b ON b.branch_id = sm.branch_id
            JOIN skus sk    ON sk.sku_id   = sm.sku_id
            {child_where}
            """,
            child_params,
        ).fetchone()

    party_size_dist = [
        {
            "party_size":            r["party_size"],
            "tx_count":              r["tx_count"],
            "revenue":               round(r["revenue"] or 0.0, 2),
            "avg_revenue_per_cover": round(r["avg_revenue_per_cover"] or 0.0, 2),
            "avg_ticket":            round(r["avg_ticket"] or 0.0, 2),
        }
        for r in party_rows
    ]

    hourly_covers = [
        {
            "hour":           r["hour"],
            "total_covers":   r["total_covers"] or 0,
            "tx_count":       r["tx_count"] or 0,
            "avg_covers_per_tx": round(r["avg_covers_per_tx"] or 0.0, 2),
            "revenue":        round(r["revenue"] or 0.0, 2),
        }
        for r in hourly_rows
    ]

    table_zone_stats = [
        {
            "zone":         r["zone"],
            "tx_count":     r["tx_count"] or 0,
            "total_covers": r["total_covers"] or 0,
            "revenue":      round(r["revenue"] or 0.0, 2),
            "avg_ticket":   round(r["avg_ticket"] or 0.0, 2),
            "avg_covers":   round(r["avg_covers"] or 0.0, 2),
        }
        for r in zone_rows
    ]

    summary = {
        "avg_party_size":        round(summary_row["avg_party_size"] or 0.0, 2),
        "avg_revenue_per_cover": round(summary_row["avg_revenue_per_cover"] or 0.0, 2),
        "total_covers":          summary_row["total_covers"] or 0,
        "total_tx":              summary_row["total_tx"] or 0,
        "child_cover_estimate":  child_row["child_qty"] or 0,
    }

    return {
        "party_size_dist":  party_size_dist,
        "hourly_covers":    hourly_covers,
        "table_zone_stats": table_zone_stats,
        "summary":          summary,
    }


# ---------------------------------------------------------------------------
# /api/buffet/revpash
# ---------------------------------------------------------------------------
@app.get("/api/buffet/revpash")
@ttl_cache
def buffet_revpash(
    branch:     Optional[str] = Query(None),
    month:      Optional[str] = Query(None),
    month_from: Optional[str] = Query(None),
    month_to:   Optional[str] = Query(None),
    seats:      int           = Query(120),
):
    """Revenue per available seat-hour (RevPASH) by hour and day-of-week, dine-in only."""
    filters: list = [
        "(t.table_no LIKE 'T%' OR t.table_no LIKE 'G%')"
    ]
    params: dict = {}

    if branch:
        filters.append("b.name = :branch")
        params["branch"] = branch

    _month_range_filter(
        filters, params, month_from, month_to, month,
        col="strftime('%Y-%m', t.sold_at)",
    )

    where = "WHERE " + " AND ".join(filters) if filters else ""

    with get_db() as con:
        hourly_rows = con.execute(
            f"""
            SELECT
                CAST(strftime('%H', t.sold_at) AS INTEGER) AS hour,
                SUM(t.total_inc_vat) / 1.07                AS revenue,
                SUM(t.customer_count)                      AS covers,
                COUNT(*)                                   AS tx_count
            FROM sales_transaction t
            JOIN branches b ON b.branch_id = t.branch_id
            {where}
            GROUP BY hour
            ORDER BY hour
            """,
            params,
        ).fetchall()

        dow_rows = con.execute(
            f"""
            SELECT
                CAST(strftime('%w', t.sold_at) AS INTEGER) AS dow,
                SUM(t.total_inc_vat) / 1.07                AS revenue,
                SUM(t.customer_count)                      AS covers,
                COUNT(DISTINCT DATE(t.sold_at))            AS day_count
            FROM sales_transaction t
            JOIN branches b ON b.branch_id = t.branch_id
            {where}
            GROUP BY dow
            ORDER BY dow
            """,
            params,
        ).fetchall()

    # Build full 24-hour array (fill zeros for missing hours)
    hourly_map = {r["hour"]: r for r in hourly_rows}
    DOW_LABELS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]

    hourly = []
    for h in range(24):
        r = hourly_map.get(h)
        rev     = round(r["revenue"] or 0.0, 2) if r else 0.0
        covers  = (r["covers"] or 0) if r else 0
        tx_cnt  = (r["tx_count"] or 0) if r else 0
        revpash = round(rev / seats, 4) if seats else 0.0
        hourly.append({
            "hour":    h,
            "revenue": rev,
            "covers":  covers,
            "tx_count": tx_cnt,
            "revpash": revpash,
        })

    dow = []
    for r in dow_rows:
        d         = r["dow"]
        rev       = round(r["revenue"] or 0.0, 2)
        covers    = r["covers"] or 0
        day_count = r["day_count"] or 1
        avg_rev   = round(rev / day_count, 2)
        avg_rph   = round(avg_rev / seats, 4) if seats else 0.0
        dow.append({
            "dow":          d,
            "label":        DOW_LABELS[d],
            "revenue":      rev,
            "covers":       covers,
            "avg_revpash":  avg_rph,
        })

    # Peak / dead hours
    non_zero = [h for h in hourly if h["revpash"] > 0]
    peak_hour = max(non_zero, key=lambda h: h["revpash"])["hour"] if non_zero else None
    dead_hour = min(non_zero, key=lambda h: h["revpash"])["hour"] if non_zero else None

    return {
        "hourly":    hourly,
        "dow":       dow,
        "seats":     seats,
        "peak_hour": peak_hour,
        "dead_hour": dead_hour,
    }


# ---------------------------------------------------------------------------
# /api/buffet/upgrade-simulator
# ---------------------------------------------------------------------------
@app.get("/api/buffet/upgrade-simulator")
@ttl_cache
def buffet_upgrade_simulator(
    branch:     Optional[str] = Query(None),
    month_from: Optional[str] = Query(None),
    month_to:   Optional[str] = Query(None),
    month:      Optional[str] = Query(None),
    shift_pct:  float         = Query(10.0),
):
    """What-if simulator: shift shift_pct% of 590-tier covers to 799-tier."""
    filters: list = ["c.name = 'BUFFET'"]
    params: dict = {}

    if branch:
        filters.append("b.name = :branch")
        params["branch"] = branch

    _month_range_filter(filters, params, month_from, month_to, month, col="sm.period_month")

    where = "WHERE " + " AND ".join(filters) if filters else ""

    sql = f"""
        SELECT
            sk.sku_code,
            sk.description,
            SUM(sm.qty)             AS qty,
            SUM(sm.subtotal_ex_vat)   AS revenue,
            SUM(sm.subtotal_ex_vat) AS revenue_ex_vat,
            SUM({_cogs_case(ssm='sm', sk='sk', sc='sc')}) AS cogs_total,
            SUM(CASE WHEN {_effective_cost_subquery(ssm='sm', sc='sc')} IS NOT NULL
                     THEN sm.qty ELSE 0 END)               AS costed_qty
        FROM sales_sku_monthly sm
        JOIN branches b   ON b.branch_id  = sm.branch_id
        JOIN skus sk      ON sk.sku_id    = sm.sku_id
        JOIN categories c ON c.category_id = sk.category_id
        LEFT JOIN sku_cost sc ON sc.sku_id = sm.sku_id
        {where}
        GROUP BY sk.sku_code, sk.description
        ORDER BY sk.sku_code
    """

    with get_db() as con:
        rows = con.execute(sql, params).fetchall()

    # Map sku_code to tier price label
    def _tier_label(sku_code: str, description: str) -> str:
        code = sku_code.upper()
        desc = description.upper() if description else ""
        if code in ("B04", "B05", "B12") or "BUFFETCHILD" in code or "CHILD" in desc or "เด็ก" in desc:
            return "child"
        if code == "B01" or "350" in code or "350" in desc:
            return "350"
        if code == "B07" or "500" in code or "500" in desc:
            return "500"
        if code == "B02" or "590" in code or "590" in desc:
            return "590"
        if code == "B11" or "799" in code or "799" in desc:
            return "799"
        if code == "B03" or "1150" in code or "1150" in desc:
            return "1150"
        return code  # fallback: use sku_code itself

    # Aggregate by tier
    from collections import defaultdict
    tier_data: dict = defaultdict(lambda: {"qty": 0, "revenue": 0.0, "revenue_ex_vat": 0.0, "cogs": 0.0, "has_cost": True, "unit_cost": None})

    for r in rows:
        label      = _tier_label(r["sku_code"], r["description"])
        qty        = r["qty"] or 0
        revenue    = r["revenue"] or 0.0
        rev_ex     = r["revenue_ex_vat"] or 0.0
        cogs_total = r["cogs_total"]
        costed_qty = r["costed_qty"] or 0
        has_row_cost = cogs_total is not None and costed_qty > 0
        unit_cost  = (cogs_total / costed_qty) if has_row_cost else None

        td = tier_data[label]
        td["qty"]          += qty
        td["revenue"]      += revenue
        td["revenue_ex_vat"] += rev_ex
        if has_row_cost:
            td["cogs"] += cogs_total
            if td["unit_cost"] is None:
                td["unit_cost"] = unit_cost
        else:
            td["has_cost"] = False

    def _build_tier_list(data: dict) -> list:
        result = []
        for name, td in sorted(data.items()):
            qty    = td["qty"]
            rev    = round(td["revenue"], 2)
            rev_ex = round(td["revenue_ex_vat"], 2)
            cogs   = round(td["cogs"], 2) if td["has_cost"] else None
            gp     = round(rev_ex - cogs, 2) if cogs is not None else None
            gpc    = round(gp / qty, 2) if (gp is not None and qty) else None
            result.append({
                "name":         name,
                "qty":          qty,
                "revenue":      rev,
                "gp":           gp,
                "gp_per_cover": gpc,
            })
        return result

    current_tiers = _build_tier_list(tier_data)
    current_gp    = sum(t["gp"] for t in current_tiers if t["gp"] is not None)

    # Simulate: shift shift_pct% of 590 qty → 799
    import copy
    sim_data = copy.deepcopy(tier_data)

    tier_590 = sim_data.get("590")
    tier_799 = sim_data.get("799")

    delta_gp   = 0.0
    delta_note = None

    if tier_590 and tier_799:
        shift_qty = tier_590["qty"] * (shift_pct / 100.0)

        # Revenue impact: shift_qty covers move from 590 tier price to 799 tier price
        avg_rev_590 = tier_590["revenue"] / tier_590["qty"] if tier_590["qty"] else 0
        avg_rev_799 = tier_799["revenue"] / tier_799["qty"] if tier_799["qty"] else 0
        avg_rev_ex_590 = tier_590["revenue_ex_vat"] / tier_590["qty"] if tier_590["qty"] else 0
        avg_rev_ex_799 = tier_799["revenue_ex_vat"] / tier_799["qty"] if tier_799["qty"] else 0

        # Adjust 590 tier
        tier_590["qty"]           -= shift_qty
        tier_590["revenue"]       -= shift_qty * avg_rev_590
        tier_590["revenue_ex_vat"] -= shift_qty * avg_rev_ex_590
        if tier_590["has_cost"] and tier_590["unit_cost"] is not None:
            tier_590["cogs"] -= shift_qty * tier_590["unit_cost"]

        # Adjust 799 tier
        tier_799["qty"]           += shift_qty
        tier_799["revenue"]       += shift_qty * avg_rev_799
        tier_799["revenue_ex_vat"] += shift_qty * avg_rev_ex_799
        if tier_799["has_cost"] and tier_799["unit_cost"] is not None:
            tier_799["cogs"] += shift_qty * tier_799["unit_cost"]
    else:
        delta_note = "590 or 799 tier data not found; simulation unchanged"

    simulated_tiers = _build_tier_list(sim_data)
    simulated_gp    = sum(t["gp"] for t in simulated_tiers if t["gp"] is not None)

    delta_gp  = round(simulated_gp - current_gp, 2)
    delta_pct = round(delta_gp / current_gp * 100, 2) if current_gp else None

    result = {
        "current":   {"tiers": current_tiers,   "total_gp": round(current_gp, 2)},
        "simulated": {"tiers": simulated_tiers,  "total_gp": round(simulated_gp, 2)},
        "delta_gp":  delta_gp,
        "delta_pct": delta_pct,
        "shift_pct": shift_pct,
    }
    if delta_note:
        result["note"] = delta_note
    return result


# ---------------------------------------------------------------------------
# Buffet Analytics Helpers
# ---------------------------------------------------------------------------

_BUFFET_ADULT_CODES = ("BUFFET", "BUFFET350", "BUFFET500", "BUFFET590", "BUFFET799", "B01", "B02", "B07", "B11", "B03")
_BUFFET_CHILD_CODES = ("B04", "B05", "B12", "BUFFETCHILD")
_BUFFET_PREMIUM_CODES = ("BUFFET590", "BUFFET799", "B02", "B11", "B03")


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
# GET /api/buffet/gp-calendar
# ---------------------------------------------------------------------------
@app.get("/api/buffet/gp-calendar")
@ttl_cache
def buffet_gp_calendar(
    branch: Optional[str] = Query(None),
    month:  str           = Query(...),
):
    """Daily GP calendar for a given YYYY-MM. Prorates monthly COGS/revenue to daily."""
    params: dict = {"month": month}

    # --- Daily revenue & tx from sales_transaction (all Easy receipts) -----
    # NOTE: do NOT restrict to table_no LIKE 'T%'/'G%' — in this dataset tables
    # are mostly numeric ("1","2","25, 26"…) and that filter dropped ~82% of
    # revenue, leaving the calendar near-empty and margins nonsensical. Using
    # all Easy receipts keeps the revenue base consistent with the all-SKU BoM
    # COGS below (both reconcile to ~120M ex-VAT / 54% food cost for 2026-05).
    day_filters = ["st.source = 'Easy'",
                   "SUBSTR(st.sold_at, 1, 7) = :month"]
    if branch:
        day_filters.append("b.name = :branch")
        params["branch"] = branch

    day_sql = f"""
        SELECT
            DATE(st.sold_at)          AS day,
            SUM(st.total_inc_vat)     AS revenue_inc,
            COUNT(*)                  AS tx_count,
            SUM(st.customer_count)    AS customer_count
        FROM sales_transaction st
        JOIN branches b ON b.branch_id = st.branch_id
        WHERE {' AND '.join(day_filters)}
        GROUP BY DATE(st.sold_at)
        ORDER BY DATE(st.sold_at)
    """

    # --- Monthly BoM COGS from sales_sku_monthly ---------------------------
    # Revenue comes from the actual dine-in receipts above (total_inc_vat ÷1.07),
    # so COGS must be the matching ALL-SKU BoM cost — NOT category='BUFFET'
    # (that bucket holds only the tier SKUs, whose COGS is deliberately 0 since
    #  consumption is logged on the item-level B0xxx rows). Filtering to BUFFET
    #  therefore returned null cogs and an inflated revenue base (~4×). We use
    #  _unified_cogs_expr over every SKU and pro-rate it onto days by revenue.
    mon_filters = ["sm.period_month = :month", "b.name != 'ALL'"]
    if branch:
        mon_filters.append("b.name = :branch")

    mon_sql = f"""
        SELECT SUM({_unified_cogs_expr(ssm='sm')}) AS cogs
        FROM sales_sku_monthly sm
        JOIN branches b ON b.branch_id = sm.branch_id
        WHERE {' AND '.join(mon_filters)}
    """

    with get_db() as con:
        day_rows  = con.execute(day_sql, params).fetchall()
        mon_row   = con.execute(mon_sql, params).fetchone()

    monthly_cogs = (mon_row["cogs"] or None) if mon_row else None

    total_rev_inc = sum(r["revenue_inc"] or 0.0 for r in day_rows)
    total_rev_ex  = round(total_rev_inc / 1.07, 2)

    days_out = []
    for r in day_rows:
        day_rev = r["revenue_inc"] or 0.0
        weight  = day_rev / total_rev_inc if total_rev_inc else 0.0

        day_rev_ex = round(day_rev / 1.07, 2)
        day_cogs   = round(monthly_cogs * weight, 2) if monthly_cogs is not None else None

        if day_cogs is not None and day_rev_ex:
            day_gp     = round(day_rev_ex - day_cogs, 2)
            margin_pct = round(day_gp / day_rev_ex * 100, 2)
        elif day_cogs is not None:
            day_gp, margin_pct = round(-day_cogs, 2), None
        else:
            day_gp, margin_pct = None, None

        days_out.append({
            "date":           r["day"],
            "revenue_inc":    round(day_rev, 2),
            "revenue_ex":     day_rev_ex,
            "cogs":           day_cogs,
            "gp":             day_gp,
            "margin_pct":     margin_pct,
            "tx_count":       r["tx_count"]      or 0,
            "customer_count": r["customer_count"] or 0,
        })

    # Monthly margin
    if monthly_cogs is not None and total_rev_ex:
        monthly_margin_pct = round((total_rev_ex - monthly_cogs) / total_rev_ex * 100, 2)
    else:
        monthly_margin_pct = None

    return {
        "month":               month,
        "days":                days_out,
        "monthly_margin_pct":  monthly_margin_pct,
    }


# ---------------------------------------------------------------------------
# GET /api/buffet/child-trend
# ---------------------------------------------------------------------------
@app.get("/api/buffet/child-trend")
@ttl_cache
def buffet_child_trend(
    branch:     Optional[str] = Query(None),
    month_from: Optional[str] = Query(None),
    month_to:   Optional[str] = Query(None),
):
    """Monthly adult/child pax split and GP% trend for BUFFET category.
    fix C2/C4: adult codes include 'BUFFET'; COGS from all-SKU query (item COGS on B0xxx rows).
    """
    filters: list = []
    params:  dict = {}
    if branch:
        filters.append("b.name = :branch")
        params["branch"] = branch
    _month_range_filter(filters, params, month_from, None, col="sm.period_month")
    _month_range_filter([], params, None, month_to, col="sm.period_month")
    # rebuild to_filter directly
    mf_filter = [f for f in filters]
    if month_to:
        mf_filter.append("sm.period_month <= :month_to")
        params["month_to"] = month_to

    where = ("WHERE " + " AND ".join(mf_filter)) if mf_filter else ""

    # Tier/pax query — BUFFET category for revenue and cover counts
    sql = f"""
        SELECT
            sm.period_month,
            sk.sku_code,
            sk.description,
            c.name          AS category,
            SUM(sm.qty)     AS qty,
            SUM(sm.subtotal_ex_vat) AS rev_ex
        FROM sales_sku_monthly sm
        JOIN branches   b  ON b.branch_id   = sm.branch_id
        JOIN skus       sk ON sk.sku_id     = sm.sku_id
        JOIN categories c  ON c.category_id = sk.category_id
        {where}
        GROUP BY sm.period_month, sk.sku_code, sk.description, c.name
        ORDER BY sm.period_month
    """

    # All-SKU COGS per month — item consumption is on B0xxx rows, not BUFFET category
    cogs_base_filters = [f for f in mf_filter if "period_month" in f or "b.name" in f]
    cogs_where = ("WHERE " + " AND ".join(cogs_base_filters + ["b.name != 'ALL'"])) if cogs_base_filters else "WHERE b.name != 'ALL'"
    cogs_sql = f"""
        SELECT sm.period_month, SUM({_unified_cogs_expr(ssm='sm')}) AS cogs
        FROM sales_sku_monthly sm
        JOIN branches b ON b.branch_id = sm.branch_id
        {cogs_where}
        GROUP BY sm.period_month
    """

    with get_db() as con:
        rows      = con.execute(sql,      params).fetchall()
        cogs_rows = con.execute(cogs_sql, params).fetchall()

    cogs_by_month = {r["period_month"]: (r["cogs"] or 0.0) for r in cogs_rows}

    # Aggregate per month
    from collections import defaultdict
    monthly: dict = defaultdict(lambda: {"adult": 0.0, "child": 0.0, "rev_ex": 0.0})

    for r in rows:
        mo   = r["period_month"]
        code = (r["sku_code"] or "").upper()
        desc = (r["description"] or "").lower()
        qty  = r["qty"] or 0.0
        cat  = r["category"] or ""

        is_child = (code in _BUFFET_CHILD_CODES or
                    "child" in desc or "เด็ก" in desc)
        is_adult = code in _BUFFET_ADULT_CODES

        if is_child:
            monthly[mo]["child"] += qty
        elif is_adult:
            monthly[mo]["adult"] += qty

        if cat == "BUFFET":
            monthly[mo]["rev_ex"] += r["rev_ex"] or 0.0

    result = []
    for mo in sorted(monthly):
        d          = monthly[mo]
        adult_pax  = d["adult"]
        child_pax  = d["child"]
        total_pax  = adult_pax + child_pax
        child_pct  = round(child_pax / total_pax * 100, 2) if total_pax else 0.0
        rev_ex     = d["rev_ex"]
        gp_pct     = None
        month_cogs = cogs_by_month.get(mo)
        if month_cogs is not None and rev_ex:
            gp         = rev_ex - month_cogs
            gp_pct     = round(gp / rev_ex * 100, 2)

        result.append({
            "period_month": mo,
            "adult_pax":    adult_pax,
            "child_pax":    child_pax,
            "child_pct":    child_pct,
            "gp_pct":       gp_pct,
        })

    return result


# ---------------------------------------------------------------------------
# GET /api/buffet/drink-attach-by-branch
# ---------------------------------------------------------------------------
@app.get("/api/buffet/drink-attach-by-branch")
@ttl_cache
def buffet_drink_attach_by_branch(
    month:      Optional[str] = Query(None),
    month_from: Optional[str] = Query(None),
    month_to:   Optional[str] = Query(None),
):
    """Drink attachment rates per branch from Easy POS transactions."""
    filters: list = ["st.source = 'Easy'", "b.name != 'ALL'"]
    params:  dict = {}
    _month_range_filter(filters, params, month_from, month_to, month,
                        col="SUBSTR(st.sold_at,1,7)")

    sql = f"""
        SELECT
            b.name                                  AS branch,
            COUNT(*)                                AS total_bills,
            SUM(CASE WHEN st.drink_subtotal > 0 THEN 1 ELSE 0 END) AS bills_with_drink,
            AVG(CASE WHEN st.drink_subtotal > 0 THEN st.drink_subtotal ELSE NULL END) AS avg_drink_per_bill,
            SUM(st.drink_subtotal)                  AS total_drink_revenue,
            SUM(st.total_inc_vat) / 1.07                   AS total_revenue
        FROM sales_transaction st
        JOIN branches b ON b.branch_id = st.branch_id
        WHERE {' AND '.join(filters)}
        GROUP BY b.name
        ORDER BY bills_with_drink * 1.0 / NULLIF(COUNT(*), 0) DESC
    """

    with get_db() as con:
        rows = con.execute(sql, params).fetchall()

    result = []
    for r in rows:
        total_bills       = r["total_bills"]        or 0
        bills_with_drink  = r["bills_with_drink"]   or 0
        attach_rate_pct   = round(bills_with_drink / total_bills * 100, 2) if total_bills else 0.0
        total_rev         = r["total_revenue"]       or 0.0
        total_drink       = r["total_drink_revenue"] or 0.0
        drink_rev_pct     = round(total_drink / total_rev * 100, 2) if total_rev else 0.0

        result.append({
            "branch":               r["branch"],
            "total_bills":          total_bills,
            "bills_with_drink":     bills_with_drink,
            "attach_rate_pct":      attach_rate_pct,
            "avg_drink_per_bill":   round(r["avg_drink_per_bill"] or 0.0, 2),
            "drink_revenue_pct":    drink_rev_pct,
            "total_drink_revenue":  round(total_drink, 2),
        })

    return result


# ---------------------------------------------------------------------------
# GET /api/buffet/breakeven
# ---------------------------------------------------------------------------
@app.get("/api/buffet/breakeven")
@ttl_cache
def buffet_breakeven(
    branch:               Optional[str]   = Query(None),
    month:                Optional[str]   = Query(None),
    month_from:           Optional[str]   = Query(None),
    month_to:             Optional[str]   = Query(None),
    monthly_fixed_cost:   float           = Query(0.0),
):
    """Breakeven analysis: covers needed to cover fixed costs given avg GP/cover."""
    # fix C2: revenue + pax from BUFFET tier SKUs; COGS from ALL SKUs (tier COGS is on B0xxx item rows)
    sku_filters:  list = ["c.name = 'BUFFET'", "b.name != 'ALL'"]
    cogs_filters: list = ["b.name != 'ALL'"]
    sku_params:   dict = {}
    if branch:
        sku_filters.append("b.name = :branch")
        cogs_filters.append("b.name = :branch")
        sku_params["branch"] = branch
    _month_range_filter(sku_filters, sku_params, month_from, month_to, month)
    _month_range_filter(cogs_filters, sku_params, month_from, month_to, month)

    sku_sql = f"""
        SELECT
            SUM(sm.qty)             AS total_pax,
            SUM(sm.subtotal_ex_vat) AS rev_ex
        FROM sales_sku_monthly sm
        JOIN branches   b  ON b.branch_id   = sm.branch_id
        JOIN skus       sk ON sk.sku_id     = sm.sku_id
        JOIN categories c  ON c.category_id = sk.category_id
        WHERE {' AND '.join(sku_filters)}
    """

    cogs_sql = f"""
        SELECT SUM({_unified_cogs_expr(ssm='sm')}) AS total_cogs
        FROM sales_sku_monthly sm
        JOIN branches b ON b.branch_id = sm.branch_id
        WHERE {' AND '.join(cogs_filters)}
    """

    tx_filters: list = ["st.source = 'Easy'"]
    tx_params:  dict = {}
    if branch:
        tx_filters.append("b.name = :branch")
        tx_params["branch"] = branch
    _month_range_filter(tx_filters, tx_params, month_from, month_to, month,
                        col="SUBSTR(st.sold_at,1,7)")

    tx_sql = f"""
        SELECT
            SUM(st.customer_count) AS total_covers,
            COUNT(DISTINCT SUBSTR(st.sold_at,1,10)) AS active_days
        FROM sales_transaction st
        JOIN branches b ON b.branch_id = st.branch_id
        WHERE {' AND '.join(tx_filters)} AND b.name != 'ALL'
    """

    with get_db() as con:
        sku_row  = con.execute(sku_sql,  sku_params).fetchone()
        cogs_row = con.execute(cogs_sql, sku_params).fetchone()
        tx_row   = con.execute(tx_sql,   tx_params).fetchone()

    total_pax  = (sku_row["total_pax"] or 0) if sku_row else 0
    rev_ex     = (sku_row["rev_ex"]    or 0.0) if sku_row else 0.0
    total_cogs = (cogs_row["total_cogs"]) if cogs_row else None

    avg_rev_per_cover = round(rev_ex / total_pax, 2) if total_pax else 0.0

    if total_cogs is not None and total_pax:
        gp_total         = rev_ex - total_cogs
        avg_gp_per_cover = round(gp_total / total_pax, 2)
    else:
        avg_gp_per_cover = None

    active_days       = (tx_row["active_days"]  or 1)  if tx_row else 1
    total_covers      = (tx_row["total_covers"] or 0)  if tx_row else 0
    actual_avg_covers = round(total_covers / active_days, 2) if active_days else 0.0

    if monthly_fixed_cost > 0 and avg_gp_per_cover:
        required_per_month = round(monthly_fixed_cost / avg_gp_per_cover, 2)
        required_per_day   = round(required_per_month / 30, 2)
        breakeven_util     = round(actual_avg_covers / required_per_day * 100, 2) if required_per_day else None
    else:
        required_per_month = None
        required_per_day   = None
        breakeven_util     = None

    return {
        "avg_gp_per_cover":           avg_gp_per_cover,
        "avg_revenue_per_cover":       avg_rev_per_cover,
        "monthly_fixed_cost":          monthly_fixed_cost,
        "required_covers_per_month":   required_per_month,
        "required_covers_per_day":     required_per_day,
        "actual_avg_covers_per_day":   actual_avg_covers,
        "breakeven_utilization_pct":   breakeven_util,
    }


# ---------------------------------------------------------------------------
# GET /api/buffet/forecast
# ---------------------------------------------------------------------------
@ttl_cache
def buffet_forecast(
    branch:     Optional[str] = Query(None),
    periods:    int           = Query(3, ge=1, le=12),
    month_from: Optional[str] = Query(None),
    month_to:   Optional[str] = Query(None),
):
    """Linear-regression forecast of buffet pax and revenue for future months."""
    filters: list = ["c.name = 'BUFFET'", "b.name != 'ALL'"]
    params:  dict = {}
    if branch:
        filters.append("b.name = :branch")
        params["branch"] = branch
    _month_range_filter(filters, params, month_from, month_to)

    sql = f"""
        SELECT
            sm.period_month,
            SUM(sm.qty)           AS pax,
            SUM(sm.subtotal_ex_vat) AS revenue
        FROM sales_sku_monthly sm
        JOIN branches   b  ON b.branch_id   = sm.branch_id
        JOIN skus       sk ON sk.sku_id     = sm.sku_id
        JOIN categories c  ON c.category_id = sk.category_id
        WHERE {' AND '.join(filters)}
        GROUP BY sm.period_month
        ORDER BY sm.period_month
    """

    with get_db() as con:
        rows = con.execute(sql, params).fetchall()

    if len(rows) < 3:
        raise HTTPException(400, "At least 3 historical data points are required for forecasting.")

    historical = [{"period": r["period_month"], "pax": r["pax"] or 0.0, "revenue": r["revenue"] or 0.0}
                  for r in rows]

    xs  = list(range(len(historical)))
    pax_ys  = [h["pax"]     for h in historical]
    rev_ys  = [h["revenue"] for h in historical]

    pax_slope,  pax_intercept,  pax_r2  = _ols_regression(xs, pax_ys)
    rev_slope,  rev_intercept,  rev_r2  = _ols_regression(xs, rev_ys)

    r_squared = round((pax_r2 + rev_r2) / 2, 4)

    # Trend from slope relative to mean
    pax_mean = sum(pax_ys) / len(pax_ys) if pax_ys else 1
    rel_slope = pax_slope / pax_mean if pax_mean else 0
    if rel_slope > 0.01:
        trend = "up"
    elif rel_slope < -0.01:
        trend = "down"
    else:
        trend = "flat"

    # Monthly growth rate (avg MoM %)
    mom_rates = []
    for i in range(1, len(pax_ys)):
        prev = pax_ys[i - 1]
        if prev:
            mom_rates.append((pax_ys[i] - prev) / prev * 100)
    monthly_growth_rate = round(sum(mom_rates) / len(mom_rates), 2) if mom_rates else 0.0

    confidence = "high" if r_squared > 0.7 else ("med" if r_squared > 0.4 else "low")

    last_period = historical[-1]["period"]
    forecast_out = []
    for i in range(1, periods + 1):
        xi  = len(historical) - 1 + i
        fpax  = max(0.0, pax_slope * xi + pax_intercept)
        frev  = max(0.0, rev_slope * xi + rev_intercept)
        fperiod = _next_period_month(last_period, i)
        forecast_out.append({
            "period":     fperiod,
            "pax":        round(fpax, 2),
            "revenue":    round(frev, 2),
            "confidence": confidence,
        })

    return {
        "historical":          historical,
        "forecast":            forecast_out,
        "r_squared":           r_squared,
        "trend":               trend,
        "monthly_growth_rate": monthly_growth_rate,
    }


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


# ---------------------------------------------------------------------------
# /api/analysis/daily   -  day-by-day sales for a given month
# ---------------------------------------------------------------------------
@app.get("/api/analysis/daily")
def analysis_daily(
    month: str = Query(..., description="YYYY-MM"),
    branch: Optional[str] = Query(None),
):
    """
    Daily revenue, tx count, and customer count for each calendar day
    in the given month (Easy transaction-level data).
    """
    filters = ["b.name != 'ALL'", "strftime('%Y-%m', st.sold_at) = :month"]
    params: dict = {"month": month}
    if branch:
        filters.append("b.name = :branch")
        params["branch"] = branch

    where = "WHERE " + " AND ".join(filters)

    with get_db() as con:
        rows = con.execute(
            f"""
            SELECT strftime('%Y-%m-%d', st.sold_at) AS day,
                   COUNT(*)                          AS tx_count,
                   SUM(st.customer_count)            AS customer_count,
                   ROUND(SUM(st.total_inc_vat) / 1.07, 2)  AS revenue
            FROM sales_transaction st
            JOIN branches b ON b.branch_id = st.branch_id
            {where}
            GROUP BY day
            ORDER BY day
            """,
            params,
        ).fetchall()

    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# /api/analysis/gross_profit   -  GP (Revenue − COGS) at daily/weekly/monthly
# ---------------------------------------------------------------------------
@app.get("/api/analysis/gross_profit")
@ttl_cache
def analysis_gross_profit(
    branch:      Optional[str] = Query(None),
    granularity: str           = Query("daily"),   # daily | weekly | monthly
    month:       Optional[str] = Query(None),
    month_from:  Optional[str] = Query(None),
    month_to:    Optional[str] = Query(None),
    date_from:   Optional[str] = Query(None),
    date_to:     Optional[str] = Query(None),
):
    """
    Gross profit = Revenue (ex-VAT) − COGS (qty × sku_cost.unit_cost).

    Monthly: computed directly from sales_sku_monthly + sku_cost.
    Daily/Weekly: COGS is prorated by daily revenue share within each
    (branch, month) — exact at month level, distributed by sales mix per day.
    """
    granularity = (granularity or "daily").lower()

    # ---- monthly: direct, no proration ------------------------------------
    if granularity == "monthly":
        filters = ["b.name != 'ALL'"]
        params: dict = {}
        if branch:
            filters.append("b.name = :branch")
            params["branch"] = branch
        _month_range_filter(filters, params, month_from, month_to, month, col="sm.period_month")
        where = "WHERE " + " AND ".join(filters)
        with get_db() as con:
            rows = con.execute(
                f"""
                SELECT sm.period_month AS period,
                       ROUND(SUM(sm.subtotal_ex_vat), 2)                          AS revenue,
                       ROUND(SUM({_cogs_case(ssm='sm', sk='sk', sc='sc')}), 2)     AS cogs,
                       SUM(CASE WHEN {_effective_cost_subquery(ssm='sm', sc='sc')} IS NULL THEN sm.subtotal_ex_vat ELSE 0 END)
                                                                                   AS uncosted_rev
                FROM sales_sku_monthly sm
                JOIN branches b ON b.branch_id = sm.branch_id
                JOIN skus sk ON sk.sku_id = sm.sku_id
                LEFT JOIN sku_cost sc ON sc.sku_id = sm.sku_id
                {where}
                GROUP BY sm.period_month
                ORDER BY sm.period_month
                """,
                params,
            ).fetchall()
        out = []
        for r in rows:
            rev  = r["revenue"] or 0.0
            cogs = r["cogs"] or 0.0
            gp   = round(rev - cogs, 2)
            pct  = round(gp / rev * 100, 1) if rev else 0.0
            out.append({
                "period":       r["period"],
                "revenue":      rev,
                "cogs":         cogs,
                "gross_profit": gp,
                "margin_pct":   pct,
                "uncosted_rev": round(r["uncosted_rev"] or 0.0, 2),
            })
        return out

    # ---- daily / weekly: prorate COGS by daily revenue share --------------
    # Step 1: per-(branch, month) revenue + cogs from sales_sku_monthly
    bm_filters = ["b.name != 'ALL'"]
    bm_params: dict = {}
    if branch:
        bm_filters.append("b.name = :branch")
        bm_params["branch"] = branch
    # If date range supplied, derive month range from it; otherwise use month_*
    mf = month_from or month or (date_from[:7] if date_from else None)
    mt = month_to   or month or (date_to[:7]   if date_to   else None)
    if mf:
        bm_filters.append("sm.period_month >= :mf")
        bm_params["mf"] = mf
    if mt:
        bm_filters.append("sm.period_month <= :mt")
        bm_params["mt"] = mt
    bm_where = "WHERE " + " AND ".join(bm_filters)

    # Step 2: per-day revenue from sales_transaction (Easy only — only source with per-tx data)
    tx_filters = ["b.name != 'ALL'", "st.source = 'Easy'"]
    tx_params: dict = {}
    if branch:
        tx_filters.append("b.name = :branch")
        tx_params["branch"] = branch
    if date_from:
        tx_filters.append("DATE(st.sold_at) >= :date_from")
        tx_params["date_from"] = date_from
    if date_to:
        tx_filters.append("DATE(st.sold_at) <= :date_to")
        tx_params["date_to"] = date_to
    if mf and not date_from:
        tx_filters.append("strftime('%Y-%m', st.sold_at) >= :mf")
        tx_params["mf"] = mf
    if mt and not date_to:
        tx_filters.append("strftime('%Y-%m', st.sold_at) <= :mt")
        tx_params["mt"] = mt
    tx_where = "WHERE " + " AND ".join(tx_filters)

    with get_db() as con:
        bm_rows = con.execute(
            f"""
            SELECT b.name AS branch, sm.period_month AS m,
                   SUM(sm.subtotal_ex_vat) AS rev_ex,
                   SUM(sm.total_inc_vat)   AS rev_inc,
                   SUM({_cogs_case(ssm='sm', sk='sk', sc='sc')}) AS cogs
            FROM sales_sku_monthly sm
            JOIN branches b ON b.branch_id = sm.branch_id
            JOIN skus sk ON sk.sku_id = sm.sku_id
            LEFT JOIN sku_cost sc ON sc.sku_id = sm.sku_id
            {bm_where}
            GROUP BY b.name, sm.period_month
            """,
            bm_params,
        ).fetchall()

        day_rows = con.execute(
            f"""
            SELECT b.name AS branch,
                   DATE(st.sold_at) AS day,
                   strftime('%Y-%m', st.sold_at) AS m,
                   SUM(st.total_inc_vat) AS rev_inc
            FROM sales_transaction st
            JOIN branches b ON b.branch_id = st.branch_id
            {tx_where}
            GROUP BY b.name, DATE(st.sold_at)
            ORDER BY day
            """,
            tx_params,
        ).fetchall()

    # Build per-(branch, month) totals
    bm_map: Dict = {}
    for r in bm_rows:
        bm_map[(r["branch"], r["m"])] = {
            "rev_ex":  r["rev_ex"]  or 0.0,
            "rev_inc": r["rev_inc"] or 0.0,
            "cogs":    r["cogs"]    or 0.0,
        }

    # Aggregate daily-tx revenue per (branch, month) so we can compute share
    bm_daily_total_inc: Dict = {}
    for r in day_rows:
        k = (r["branch"], r["m"])
        bm_daily_total_inc[k] = bm_daily_total_inc.get(k, 0.0) + (r["rev_inc"] or 0.0)

    # Build daily output with prorated COGS
    daily_out = []
    for r in day_rows:
        k = (r["branch"], r["m"])
        bm = bm_map.get(k)
        day_rev_inc = r["rev_inc"] or 0.0
        if not bm or bm["rev_inc"] <= 0 or bm_daily_total_inc.get(k, 0) <= 0:
            # No monthly costing info or no monthly revenue — fall back to day_rev_ex with 0 COGS
            day_rev_ex = ex_vat(day_rev_inc)
            daily_out.append({
                "branch":       r["branch"],
                "day":          r["day"],
                "period_month": r["m"],
                "revenue":      round(day_rev_ex, 2),
                "revenue_inc":  round(day_rev_inc, 2),
                "cogs":         None,   # fix C10: None so aggregates use `or 0` guard, not inflate GP
                "gross_profit": None,
                "margin_pct":   None,
            })
            continue
        share = day_rev_inc / bm["rev_inc"]   # fraction of full-month revenue — correct proration (fix: C1)
        day_cogs = bm["cogs"] * share
        # Day revenue ex-VAT: use the same share against monthly ex-VAT (keeps GP consistent)
        day_rev_ex = bm["rev_ex"] * share
        gp = day_rev_ex - day_cogs
        pct = (gp / day_rev_ex * 100) if day_rev_ex else None
        daily_out.append({
            "branch":       r["branch"],
            "day":          r["day"],
            "period_month": r["m"],
            "revenue":      round(day_rev_ex, 2),
            "revenue_inc":  round(day_rev_inc, 2),
            "cogs":         round(day_cogs, 2),
            "gross_profit": round(gp, 2),
            "margin_pct":   round(pct, 1) if pct is not None else None,
        })

    if granularity == "daily":
        # Roll up across branches per day (in case branch=None)
        agg: Dict = {}
        for d in daily_out:
            k = d["day"]
            a = agg.setdefault(k, {"period": k, "revenue": 0.0, "cogs": 0.0, "gross_profit": 0.0})
            a["revenue"]      += d["revenue"]
            a["cogs"]         += d["cogs"]
            a["gross_profit"] += d["gross_profit"]
        out = []
        for k in sorted(agg.keys()):
            a = agg[k]
            pct = round(a["gross_profit"] / a["revenue"] * 100, 1) if a["revenue"] else None
            out.append({
                "period":       a["period"],
                "revenue":      round(a["revenue"], 2),
                "cogs":         round(a["cogs"], 2),
                "gross_profit": round(a["gross_profit"], 2),
                "margin_pct":   pct,
            })
        return out

    # weekly: ISO-8601 week key 'YYYY-Www' (matches _iso_week_sql used by the SQL paths)
    if granularity == "weekly":
        from datetime import date as _date
        agg = {}
        for d in daily_out:
            try:
                y, mo, dd = d["day"].split("-")
                iso = _date(int(y), int(mo), int(dd)).isocalendar()
                wk = f"{iso[0]}-W{iso[1]:02d}"
            except Exception:
                continue
            a = agg.setdefault(wk, {"period": wk, "revenue": 0.0, "cogs": 0.0,
                                    "gross_profit": 0.0, "week_start": d["day"]})
            a["revenue"]      += d["revenue"]
            a["cogs"]         += d["cogs"]
            a["gross_profit"] += d["gross_profit"]
            if d["day"] < a["week_start"]:
                a["week_start"] = d["day"]
        out = []
        for k in sorted(agg.keys()):
            a = agg[k]
            pct = round(a["gross_profit"] / a["revenue"] * 100, 1) if a["revenue"] else None
            out.append({
                "period":       a["period"],
                "week_start":   a["week_start"],
                "revenue":      round(a["revenue"], 2),
                "cogs":         round(a["cogs"], 2),
                "gross_profit": round(a["gross_profit"], 2),
                "margin_pct":   pct,
            })
        return out

    raise HTTPException(400, f"Unknown granularity: {granularity}")


# ---------------------------------------------------------------------------
# /api/analysis/hourly   -  revenue/tx breakdown by hour of day
# ---------------------------------------------------------------------------
@app.get("/api/analysis/hourly")
@ttl_cache
def analysis_hourly(
    month:      Optional[str] = Query(None),
    month_from: Optional[str] = Query(None),
    month_to:   Optional[str] = Query(None),
    date_from:  Optional[str] = Query(None),
    date_to:    Optional[str] = Query(None),
    branch:     Optional[str] = Query(None),
):
    """Revenue, tx count, avg ticket by hour (0-23). Fills missing hours with zeros."""
    filters = ["b.name != 'ALL'", "st.source = 'Easy'"]
    params: dict = {}
    if branch:
        filters.append("b.name = :branch")
        params["branch"] = branch
    if date_from or date_to:
        if date_from:
            filters.append("DATE(st.sold_at) >= :date_from")
            params["date_from"] = date_from
        if date_to:
            filters.append("DATE(st.sold_at) <= :date_to")
            params["date_to"] = date_to
    else:
        _month_range_filter(
            filters, params, month_from, month_to, month,
            col="strftime('%Y-%m', st.sold_at)",
        )
    where = "WHERE " + " AND ".join(filters)
    with get_db() as con:
        rows = con.execute(
            f"""
            SELECT CAST(strftime('%H', st.sold_at) AS INTEGER) AS hour,
                   COUNT(*)                                     AS tx_count,
                   COALESCE(SUM(st.customer_count), 0)         AS customer_count,
                   ROUND(SUM(st.total_inc_vat) / 1.07, 2)             AS revenue,
                   ROUND(AVG(st.total_inc_vat), 2)             AS avg_ticket
            FROM sales_transaction st
            JOIN branches b ON b.branch_id = st.branch_id
            {where}
            GROUP BY hour
            ORDER BY hour
            """,
            params,
        ).fetchall()
    hour_map = {r["hour"]: dict(r) for r in rows}
    return [
        hour_map.get(h, {"hour": h, "tx_count": 0, "customer_count": 0,
                         "revenue": 0.0, "avg_ticket": 0.0})
        for h in range(24)
    ]


# ---------------------------------------------------------------------------
# /api/analysis/dow   -  day-of-week distribution
# ---------------------------------------------------------------------------
@app.get("/api/analysis/dow")
@ttl_cache
def analysis_dow(
    month:       Optional[str] = Query(None),
    month_from:  Optional[str] = Query(None),
    month_to:    Optional[str] = Query(None),
    branch:      Optional[str] = Query(None),
    group:       Optional[str] = Query(None),
    granularity: str           = Query("daily"),  # daily|weekly|monthly
):
    """Revenue and tx count grouped by day-of-week (0=Sun â€¦ 6=Sat)."""
    # The no-branch case (the Analysis Report default) drops the branches JOIN and
    # the b.name!='ALL' guard so the query is served index-only by idx_tx_ym_dow_cov.
    # Safe: sales_transaction holds no 'ALL'-named branch, so the guard excludes
    # nothing (verified). With a branch filter we keep the JOIN for b.name=:branch.
    filters: list = []
    params: dict = {}
    join = ""
    if branch or group:
        join = "JOIN branches b ON b.branch_id = st.branch_id"
    if branch:
        filters.append("b.name = :branch")
        params["branch"] = branch
    _group_filter(filters, params, group)
    _month_range_filter(filters, params, month_from, month_to, month, col="strftime('%Y-%m', st.sold_at)")

    where = ("WHERE " + " AND ".join(filters)) if filters else ""

    with get_db() as con:
        rows = con.execute(
            f"""
            SELECT CAST(strftime('%w', st.sold_at) AS INTEGER) AS dow,
                   COUNT(*)                                     AS tx_count,
                   SUM(st.customer_count)                       AS customer_count,
                   ROUND(SUM(st.total_inc_vat) / 1.07, 2)             AS revenue,
                   ROUND(AVG(st.total_inc_vat), 2)             AS avg_ticket,
                   COUNT(DISTINCT DATE(st.sold_at))             AS day_count
            FROM sales_transaction st
            {join}
            {where}
            GROUP BY dow
            ORDER BY dow
            """,
            params,
        ).fetchall()

    # For weekly/monthly: compute total periods in the selected range for normalisation
    total_periods = None
    if granularity in ("weekly", "monthly"):
        _pexpr = (_iso_week_sql('st.sold_at') if granularity == "weekly"
                  else "strftime('%Y-%m', st.sold_at)")
        with get_db() as con2:
            pr = con2.execute(
                f"SELECT COUNT(DISTINCT {_pexpr}) AS cnt "
                f"FROM sales_transaction st {join} {where}",
                params
            ).fetchone()
        total_periods = max(pr["cnt"] if pr else 1, 1)

    DOW = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]
    result = {d: {"dow": d, "label": DOW[d], "tx_count": 0, "customer_count": 0,
                  "revenue": 0.0, "avg_ticket": 0.0, "day_count": 0,
                  "avg_revenue": 0.0, "avg_tx": 0.0} for d in range(7)}
    for r in rows:
        if granularity == "daily":
            dc = max(r["day_count"] or 1, 1)
        else:
            dc = total_periods
        result[r["dow"]] = {
            "dow": r["dow"], "label": DOW[r["dow"]],
            "tx_count": r["tx_count"], "customer_count": r["customer_count"],
            "revenue": r["revenue"], "avg_ticket": r["avg_ticket"],
            "day_count":   r["day_count"] or 0,
            "avg_revenue": round((r["revenue"] or 0) / dc, 2),
            "avg_tx":      round((r["tx_count"] or 0) / dc, 2),
        }
    return list(result.values())


# ---------------------------------------------------------------------------
# /api/analysis/compare   -  KPI comparison between two periods
# ---------------------------------------------------------------------------
@app.get("/api/analysis/compare")
@ttl_cache
def analysis_compare(
    month: str = Query(..., description="Primary period YYYY-MM"),
    vs: str = Query(..., description="Comparison period YYYY-MM"),
    branch: Optional[str] = Query(None),
):
    """
    Returns KPI objects for `month` and `vs` side by side:
    revenue, revenue_ex_vat, tx_count, customer_count, avg_ticket,
    gross_sales, net_sales, discounts.
    Pulls from sales_sku_monthly + sales_month_summary (Easy).
    """
    import calendar as _cal
    def fetch_kpis(period: str, con, mtd_cut=None) -> dict:
        branch_filter = "AND b.name = :branch" if branch else ""
        p: dict = {"period": period}
        if branch:
            p["branch"] = branch

        if mtd_cut:
            # MTD-fair: compare the SAME elapsed days, all from sales_transaction
            # (day-capable) so a partial current month isn't matched to a full month.
            p["s"] = f"{period}-01"; p["e"] = f"{period}-{mtd_cut:02d}"
            r = con.execute(f"""
                SELECT ROUND(SUM(st.total_inc_vat)/1.07, 2) revenue, COUNT(*) tx_count,
                       SUM(st.customer_count) customer_count, ROUND(AVG(st.total_inc_vat) / 1.07, 2) avg_ticket
                FROM sales_transaction st JOIN branches b ON b.branch_id=st.branch_id
                WHERE b.name!='ALL' AND st.source='Easy'
                  AND date(st.sold_at) BETWEEN :s AND :e {branch_filter}""", p).fetchone()
            _rev = (r["revenue"] or 0) if r else 0
            return {"period": period, "revenue": _rev, "revenue_ex": _rev,
                    "tx_count": (r["tx_count"] or 0) if r else 0,
                    "customer_count": (r["customer_count"] or 0) if r else 0,
                    "avg_ticket": (r["avg_ticket"] or 0) if r else 0,
                    "gross_sales": 0, "net_sales": _rev, "discounts": 0}

        rev = con.execute(
            f"""
            SELECT ROUND(SUM(sm.subtotal_ex_vat), 2)   AS revenue,
                   ROUND(SUM(sm.subtotal_ex_vat), 2)  AS revenue_ex_vat
            FROM sales_sku_monthly sm
            JOIN branches b ON b.branch_id = sm.branch_id
            WHERE b.name != 'ALL' AND sm.period_month = :period
              {branch_filter}
            """,
            p,
        ).fetchone()

        tx = con.execute(
            f"""
            SELECT COUNT(*) AS tx_count,
                   SUM(customer_count) AS customer_count,
                   ROUND(AVG(total_inc_vat) / 1.07, 2) AS avg_ticket
            FROM sales_transaction st
            JOIN branches b ON b.branch_id = st.branch_id
            WHERE b.name != 'ALL' AND strftime('%Y-%m', st.sold_at) = :period
              {branch_filter}
            """,
            p,
        ).fetchone()

        sms = con.execute(
            f"""
            SELECT ROUND(SUM(gross_sales), 2) AS gross_sales,
                   ROUND(SUM(net_sales), 2)   AS net_sales,
                   ROUND(SUM(discounts), 2)   AS discounts,
                   SUM(transaction_count)     AS tx_count_sms,
                   SUM(customer_count)        AS customer_count_sms
            FROM sales_month_summary sms
            JOIN branches b ON b.branch_id = sms.branch_id
            WHERE b.name != 'ALL' AND sms.period_month = :period
              {branch_filter}
            """,
            p,
        ).fetchone()

        # Use SKU/transaction data when available, else fall back to the monthly summary
        revenue     = (rev["revenue"]        or 0) if rev else 0
        revenue_ex  = (rev["revenue_ex_vat"] or 0) if rev else 0
        tx_count    = (tx["tx_count"]        or 0) if tx else 0
        cust_count  = (tx["customer_count"]  or 0) if tx else 0
        avg_ticket  = (tx["avg_ticket"]      or 0) if tx else 0

        if revenue == 0 and sms:
            revenue    = sms["net_sales"]     or 0
            revenue_ex = sms["net_sales"]     or 0
        if tx_count == 0 and sms:
            tx_count   = sms["tx_count_sms"]  or 0
            cust_count = sms["customer_count_sms"] or 0
            avg_ticket = round(revenue / tx_count, 2) if tx_count else 0

        return {
            "period":       period,
            "revenue":      revenue,
            "revenue_ex":   revenue_ex,
            "tx_count":     tx_count,
            "customer_count": cust_count,
            "avg_ticket":   avg_ticket,
            "gross_sales":  (sms["gross_sales"] or 0) if sms else 0,
            "net_sales":    (sms["net_sales"]   or 0) if sms else 0,
            "discounts":    (sms["discounts"]   or 0) if sms else 0,
        }

    def pct_change(a, b):
        if b == 0:
            return None
        return round((a - b) / b * 100, 1)

    with get_db() as con:
        # MTD-fair when the PRIMARY month is the latest, still-partial month.
        maxd = con.execute("SELECT MAX(date(sold_at)) FROM sales_transaction WHERE source='Easy'").fetchone()[0]
        mtd_cut = None
        if maxd:
            my, mm, mdd = (int(x) for x in maxd.split("-"))
            try:
                py, pm = (int(x) for x in month.split("-")[:2])
                if (py, pm) == (my, mm) and mdd < _cal.monthrange(my, mm)[1]:
                    mtd_cut = mdd
            except Exception:
                mtd_cut = None
        current = fetch_kpis(month, con, mtd_cut)
        prior   = fetch_kpis(vs, con, mtd_cut)

    return {
        "current": current,
        "prior":   prior,
        "mtd": mtd_cut is not None, "mtd_cutoff_day": mtd_cut,
        "delta": {
            "revenue":        pct_change(current["revenue"],        prior["revenue"]),
            "tx_count":       pct_change(current["tx_count"],       prior["tx_count"]),
            "customer_count": pct_change(current["customer_count"], prior["customer_count"]),
            "avg_ticket":     pct_change(current["avg_ticket"],     prior["avg_ticket"]),
        },
    }


# ---------------------------------------------------------------------------
# /api/analysis/persona   -  customer behaviour stats
# ---------------------------------------------------------------------------
@app.get("/api/analysis/persona")
@ttl_cache
def analysis_persona(
    month:      Optional[str] = Query(None),
    month_from: Optional[str] = Query(None),
    month_to:   Optional[str] = Query(None),
    branch:     Optional[str] = Query(None),
    group:      Optional[str] = Query(None),
):
    """
    Customer behaviour from sales_transaction:
    - Party size distribution (1,2,3,4,5,6+)
    - Avg spend per head by party size
    - Food vs drink vs other split (% of subtotals)
    - Busy hours (0-23 tx count)
    """
    filters = ["b.name != 'ALL'"]
    params: dict = {}
    if branch:
        filters.append("b.name = :branch")
        params["branch"] = branch
    _group_filter(filters, params, group)
    _month_range_filter(filters, params, month_from, month_to, month, col="strftime('%Y-%m', st.sold_at)")

    where = "WHERE " + " AND ".join(filters)

    with get_db() as con:
        # Party size distribution
        party_rows = con.execute(
            f"""
            SELECT CASE
                     WHEN customer_count <= 0 THEN 1
                     WHEN customer_count >= 6 THEN 6
                     ELSE customer_count
                   END AS party_size,
                   COUNT(*)                          AS tx_count,
                   ROUND(AVG(total_inc_vat / 1.07), 2)     AS avg_bill,
                   ROUND(AVG(total_inc_vat / 1.07 / CASE WHEN customer_count <= 0 THEN 1
                                                   ELSE customer_count END), 2) AS avg_per_head
            FROM sales_transaction st
            JOIN branches b ON b.branch_id = st.branch_id
            {where}
            GROUP BY party_size
            ORDER BY party_size
            """,
            params,
        ).fetchall()

        # Food / drink / other split
        split_row = con.execute(
            f"""
            SELECT ROUND(SUM(food_subtotal), 2)  AS food,
                   ROUND(SUM(drink_subtotal), 2) AS drink,
                   ROUND(SUM(other_subtotal), 2) AS other
            FROM sales_transaction st
            JOIN branches b ON b.branch_id = st.branch_id
            {where}
            """,
            params,
        ).fetchone()

        # Busy hours
        hour_rows = con.execute(
            f"""
            SELECT CAST(strftime('%H', st.sold_at) AS INTEGER) AS hour,
                   COUNT(*) AS tx_count,
                   SUM(customer_count) AS customer_count
            FROM sales_transaction st
            JOIN branches b ON b.branch_id = st.branch_id
            {where}
            GROUP BY hour
            ORDER BY hour
            """,
            params,
        ).fetchall()

        # Overall stats
        overall = con.execute(
            f"""
            SELECT COUNT(*)                          AS tx_count,
                   ROUND(AVG(customer_count), 2)    AS avg_party_size,
                   ROUND(AVG(total_inc_vat / 1.07), 2)     AS avg_ticket,
                   ROUND(SUM(total_inc_vat) / 1.07 /
                         NULLIF(SUM(customer_count), 0), 2) AS avg_per_head
            FROM sales_transaction st
            JOIN branches b ON b.branch_id = st.branch_id
            {where}
            """,
            params,
        ).fetchone()

    hours_map = {h: 0 for h in range(24)}
    for r in hour_rows:
        hours_map[r["hour"]] = r["tx_count"]

    food  = (split_row["food"]  or 0) if split_row else 0
    drink = (split_row["drink"] or 0) if split_row else 0
    other = (split_row["other"] or 0) if split_row else 0
    total_split = food + drink + other or 1

    return {
        "overall": {
            "tx_count":       (overall["tx_count"]      or 0) if overall else 0,
            "avg_party_size": (overall["avg_party_size"] or 0) if overall else 0,
            "avg_ticket":     (overall["avg_ticket"]     or 0) if overall else 0,
            "avg_per_head":   (overall["avg_per_head"]   or 0) if overall else 0,
        },
        "party_sizes": [
            {
                "size":         r["party_size"],
                "label":        f"{r['party_size']}+" if r["party_size"] >= 6 else str(r["party_size"]),
                "tx_count":     r["tx_count"],
                "avg_bill":     r["avg_bill"],
                "avg_per_head": r["avg_per_head"],
            }
            for r in party_rows
        ],
        "food_drink_split": {
            "food":  round(food  / total_split * 100, 1),
            "drink": round(drink / total_split * 100, 1),
            "other": round(other / total_split * 100, 1),
            "food_abs": food, "drink_abs": drink, "other_abs": other,
        },
        "busy_hours": [{"hour": h, "tx_count": hours_map[h]} for h in range(24)],
    }


# ---------------------------------------------------------------------------
# /api/analysis/branches   -  cross-branch KPI comparison
# ---------------------------------------------------------------------------
@app.get("/api/analysis/branches")
@ttl_cache
def analysis_branches(
    month:      Optional[str] = Query(None),
    month_from: Optional[str] = Query(None),
    month_to:   Optional[str] = Query(None),
    group:      Optional[str] = Query(None),
):
    """KPIs for every branch side-by-side (Easy transaction-level data)."""
    filters_tx = ["b.name != 'ALL'"]
    params_tx: dict = {}
    _group_filter(filters_tx, params_tx, group)
    _month_range_filter(filters_tx, params_tx, month_from, month_to, month, col="strftime('%Y-%m', st.sold_at)")
    where_tx = "WHERE " + " AND ".join(filters_tx)

    with get_db() as con:
        easy_rows = con.execute(
            f"""
            SELECT b.name AS branch,
                   COUNT(*)                                           AS tx_count,
                   SUM(st.customer_count)                            AS customer_count,
                   ROUND(SUM(st.total_inc_vat) / 1.07, 2)                  AS revenue,
                   SUM(CASE WHEN st.discount > 0 THEN 1 ELSE 0 END) AS discount_tx,
                   ROUND(SUM(st.discount), 2)                        AS total_discount
            FROM sales_transaction st
            JOIN branches b ON b.branch_id = st.branch_id
            {where_tx}
            GROUP BY b.name
            """,
            params_tx,
        ).fetchall()

    merged: dict = {}
    for r in easy_rows:
        merged[r["branch"]] = {
            "branch": r["branch"],
            "tx_count":       r["tx_count"] or 0,
            "customer_count": r["customer_count"] or 0,
            "revenue":        r["revenue"] or 0,
            "discount_tx":    r["discount_tx"] or 0,
            "total_discount": r["total_discount"] or 0,
        }

    # Compute derived metrics
    out = []
    for m in merged.values():
        avg_ticket   = round(m["revenue"] / m["tx_count"], 2) if m["tx_count"] else 0
        discount_rate = round(100.0 * m["discount_tx"] / m["tx_count"], 1) if m["tx_count"] else 0
        out.append({
            **m,
            "avg_ticket":   avg_ticket,
            "discount_rate": discount_rate,
        })
    out.sort(key=lambda r: -r["revenue"])
    return out


# ---------------------------------------------------------------------------
# /api/analysis/cashiers   -  cashier leaderboard
# ---------------------------------------------------------------------------
@app.get("/api/analysis/cashiers")
def analysis_cashiers(
    month:      Optional[str] = Query(None),
    month_from: Optional[str] = Query(None),
    month_to:   Optional[str] = Query(None),
    branch:     Optional[str] = Query(None),
    by_branch:  bool          = Query(False),
    limit:      int           = Query(30, ge=1, le=100),
):
    """Cashier performance ranked by revenue.

    by_branch=False (default): one row per cashier, aggregated across all branches
    they keyed in (primary_branch + branch_count summarise the spread).
    by_branch=True: one row per (cashier, branch) pair — so a shared POS account like
    'Manager' becomes a separate row for each branch instead of being merged.
    """
    filters = ["b.name != 'ALL'", "TRIM(COALESCE(st.cashier,'')) != ''"]
    params: dict = {}
    if branch:
        filters.append("b.name = :branch")
        params["branch"] = branch
    _month_range_filter(filters, params, month_from, month_to, month, col="strftime('%Y-%m', st.sold_at)")
    where = "WHERE " + " AND ".join(filters)
    params["limit"] = limit

    group_by   = "st.cashier, b.name" if by_branch else "st.cashier"
    branch_sel = "b.name AS branch," if by_branch else ""

    with get_db() as con:
        # Per-(cashier, branch) revenue so we can find the primary branch in Python
        br_rows = con.execute(
            f"""
            SELECT st.cashier, b.name AS branch,
                   ROUND(SUM(st.total_inc_vat) / 1.07, 2) AS rev
            FROM sales_transaction st
            JOIN branches b ON b.branch_id = st.branch_id
            {where}
            GROUP BY st.cashier, b.name
            """, {k: v for k, v in params.items() if k != "limit"}
        ).fetchall()
        from collections import defaultdict
        per_cashier = defaultdict(list)
        for r in br_rows:
            per_cashier[r["cashier"]].append({"branch": r["branch"], "rev": r["rev"] or 0})

        rows = con.execute(
            f"""
            SELECT st.cashier,
                   {branch_sel}
                   COUNT(*)                                           AS tx_count,
                   SUM(st.customer_count)                            AS customer_count,
                   ROUND(SUM(st.total_inc_vat) / 1.07, 2)                  AS revenue,
                   ROUND(AVG(st.total_inc_vat), 2)                  AS avg_ticket,
                   SUM(CASE WHEN st.discount > 0 THEN 1 ELSE 0 END) AS discount_tx,
                   ROUND(SUM(st.discount), 2)                        AS total_discount,
                   ROUND(100.0 * SUM(CASE WHEN st.discount > 0 THEN 1 ELSE 0 END)
                         / COUNT(*), 1)                              AS discount_rate
            FROM sales_transaction st
            JOIN branches b ON b.branch_id = st.branch_id
            {where}
            GROUP BY {group_by}
            ORDER BY revenue DESC
            LIMIT :limit
            """,
            params,
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        if by_branch:
            # one branch per row already
            d["primary_branch"] = d.get("branch")
            d["branch_count"]   = 1
            d["branches"]       = [d.get("branch")] if d.get("branch") else []
        else:
            brs = per_cashier.get(d["cashier"], [])
            brs.sort(key=lambda x: -x["rev"])
            d["primary_branch"] = brs[0]["branch"] if brs else None
            d["branch_count"]   = len(brs)
            d["branches"]       = [b["branch"] for b in brs]
        out.append(d)
    return out


# ---------------------------------------------------------------------------
# /api/analysis/discounts   -  discount monitor
# ---------------------------------------------------------------------------
@app.get("/api/analysis/discounts")
def analysis_discounts(
    month:      Optional[str] = Query(None),
    month_from: Optional[str] = Query(None),
    month_to:   Optional[str] = Query(None),
    branch:     Optional[str] = Query(None),
    group:      Optional[str] = Query(None),
    date_from:  Optional[str] = Query(None),
    date_to:    Optional[str] = Query(None),
):
    """Discount analysis: overall summary, by branch, trend (all months), top cashiers."""
    filters = ["b.name != 'ALL'"]
    params: dict = {}
    if branch:
        filters.append("b.name = :branch")
        params["branch"] = branch
    _group_filter(filters, params, group)
    _apply_period_filter(filters, params, date_from, date_to, month_from, month_to, month)
    where = "WHERE " + " AND ".join(filters)

    # Trend always spans all months  -  ignore the month param
    trend_filters = ["b.name != 'ALL'"]
    trend_params: dict = {}
    if branch:
        trend_filters.append("b.name = :branch")
        trend_params["branch"] = branch
    _group_filter(trend_filters, trend_params, group)
    trend_where = "WHERE " + " AND ".join(trend_filters)

    with get_db() as con:
        overall = con.execute(
            f"""
            SELECT COUNT(*) AS total_tx,
                   SUM(CASE WHEN discount > 0 THEN 1 ELSE 0 END) AS discount_tx,
                   ROUND(SUM(discount), 2)                        AS total_discount,
                   ROUND(AVG(CASE WHEN discount > 0 THEN discount END), 0) AS avg_discount,
                   ROUND(SUM((total_inc_vat - COALESCE(service_charge,0)) / 1.07 + discount), 2) AS gross_sales,
                   SUM(CASE WHEN discount > 0 AND COALESCE(print_times,0) > 1 THEN 1 ELSE 0 END) AS reprinted_disc_bills,
                   MAX(COALESCE(print_times, 0)) AS max_print_times
            FROM sales_transaction st
            JOIN branches b ON b.branch_id = st.branch_id
            {where}
            """,
            params,
        ).fetchone()

        by_branch = con.execute(
            f"""
            SELECT b.name AS branch,
                   COUNT(*) AS total_tx,
                   SUM(CASE WHEN discount > 0 THEN 1 ELSE 0 END) AS discount_tx,
                   ROUND(100.0 * SUM(CASE WHEN discount > 0 THEN 1 ELSE 0 END)
                         / COUNT(*), 1) AS rate,
                   ROUND(SUM(discount), 2) AS total_discount,
                   ROUND(AVG(CASE WHEN discount > 0 THEN discount END), 0) AS avg_discount,
                   ROUND(100.0 * SUM(discount)
                         / NULLIF(SUM((total_inc_vat - COALESCE(service_charge,0)) / 1.07 + discount),0), 1) AS disc_pct_of_sales
            FROM sales_transaction st
            JOIN branches b ON b.branch_id = st.branch_id
            {where}
            GROUP BY b.name
            ORDER BY total_discount DESC
            """,
            params,
        ).fetchall()

        trend = con.execute(
            f"""
            SELECT strftime('%Y-%m', st.sold_at) AS period_month,
                   COUNT(*) AS total_tx,
                   SUM(CASE WHEN discount > 0 THEN 1 ELSE 0 END) AS discount_tx,
                   ROUND(100.0 * SUM(CASE WHEN discount > 0 THEN 1 ELSE 0 END)
                         / COUNT(*), 1) AS rate,
                   ROUND(SUM(discount), 2) AS total_discount
            FROM sales_transaction st
            JOIN branches b ON b.branch_id = st.branch_id
            {trend_where}
            GROUP BY period_month
            ORDER BY period_month
            """,
            trend_params,
        ).fetchall()

        top_cashiers = con.execute(
            f"""
            SELECT st.cashier,
                   b.name AS branch,
                   COUNT(*) AS total_tx,
                   SUM(CASE WHEN discount > 0 THEN 1 ELSE 0 END) AS discount_tx,
                   ROUND(100.0 * SUM(CASE WHEN discount > 0 THEN 1 ELSE 0 END)
                         / COUNT(*), 1) AS rate,
                   ROUND(SUM(discount), 2) AS total_discount,
                   ROUND(AVG(CASE WHEN discount > 0 THEN discount END), 0) AS avg_discount,
                   ROUND(100.0 * SUM(discount)
                         / NULLIF(SUM((total_inc_vat - COALESCE(service_charge,0)) / 1.07 + discount),0), 1) AS disc_pct_of_sales,
                   SUM(CASE WHEN discount > 0 AND COALESCE(print_times,0) > 1 THEN 1 ELSE 0 END) AS reprinted_disc_bills,
                   MAX(COALESCE(print_times, 0)) AS max_print_times
            FROM sales_transaction st
            JOIN branches b ON b.branch_id = st.branch_id
            {where}
            GROUP BY st.cashier, b.name
            HAVING SUM(discount) > 0
            ORDER BY b.name, total_discount DESC
            """,
            params,
        ).fetchall()

        # Discount PATTERN groups — bucket each discounted bill by discount / gross ratio
        pattern_groups = con.execute(
            f"""
            WITH t AS (
              SELECT discount AS d,
                     ((total_inc_vat - COALESCE(service_charge,0)) / 1.07 + discount) AS gross
              FROM sales_transaction st JOIN branches b ON b.branch_id = st.branch_id
              {where} AND discount > 0
            )
            SELECT CASE
                     WHEN gross <= 0          THEN 'ไม่มียอดบิล (เฉพาะส่วนลด)'
                     WHEN d >= gross          THEN '100% (ฟรี/เท่ายอดบิล)'
                     WHEN d*1.0/gross >= 0.5  THEN '50% ขึ้นไป'
                     WHEN d*1.0/gross >= 0.2  THEN '20–50%'
                     WHEN d*1.0/gross >= 0.1  THEN '10–20%'
                     ELSE 'ต่ำกว่า 10%' END AS grp,
                   COUNT(*) AS bills,
                   ROUND(SUM(d), 0) AS baht
            FROM t GROUP BY grp ORDER BY baht DESC
            """,
            params,
        ).fetchall()

        # Most common fixed discount amounts (likely coupons / standard promos)
        common_amounts = con.execute(
            f"""
            SELECT discount AS amount, COUNT(*) AS bills, ROUND(SUM(discount),0) AS baht
            FROM sales_transaction st JOIN branches b ON b.branch_id = st.branch_id
            {where} AND discount > 0
            GROUP BY discount ORDER BY bills DESC LIMIT 15
            """,
            params,
        ).fetchall()

    # Flag outlier cashiers: discount % of sales >= 2× the chain median (min 4%)
    cashiers = [dict(r) for r in top_cashiers]
    pcts = sorted(c["disc_pct_of_sales"] for c in cashiers if c.get("disc_pct_of_sales") is not None)
    median = pcts[len(pcts)//2] if pcts else 0
    flag_at = max(median * 2, 4.0)
    for c in cashiers:
        c["is_outlier"] = (c.get("disc_pct_of_sales") or 0) >= flag_at

    # Enrich overall with the rate fields the by-branch rows already carry, so the
    # summary KPIs have both: % of bills discounted, and discount as % of sales.
    ov = dict(overall) if overall else {}
    if ov:
        _tt = ov.get("total_tx") or 0
        _gs = ov.get("gross_sales") or 0
        ov["discount_rate"] = round(100.0 * (ov.get("discount_tx") or 0) / _tt, 1) if _tt else 0
        ov["disc_pct_of_sales"] = round(100.0 * (ov.get("total_discount") or 0) / _gs, 2) if _gs else 0
    return {
        "overall":      ov,
        "by_branch":    [dict(r) for r in by_branch],
        "trend":        [dict(r) for r in trend],
        "top_cashiers": cashiers,
        "pattern_groups": [dict(r) for r in pattern_groups],
        "common_amounts": [dict(r) for r in common_amounts],
        "outlier_threshold_pct": round(flag_at, 1),
    }


# ---------------------------------------------------------------------------
# /api/analysis/discount_bills  -  individual bills (receipts) with a discount
# ---------------------------------------------------------------------------
@app.get("/api/analysis/discount_bills")
def analysis_discount_bills(
    month:        Optional[str]   = Query(None),
    month_from:   Optional[str]   = Query(None),
    month_to:     Optional[str]   = Query(None),
    branch:       Optional[str]   = Query(None),
    cashier:      Optional[str]   = Query(None),
    min_discount: float           = Query(0.0, ge=0),
    amount:       Optional[float] = Query(None),
    min_pct:      Optional[float] = Query(None),
    max_pct:      Optional[float] = Query(None),
    gross_zero:   bool            = Query(False),
    payment:        Optional[str]   = Query(None, description="filter by dominant payment code (e.g. Cash, Credit)"),
    reprinted_only: bool            = Query(False, description="only bills printed more than once"),
    sort:           str             = Query("discount", pattern="^(discount|pct|date|total|reprints)$"),
    limit:          int             = Query(100, ge=1, le=1000),
    date_from:      Optional[str]   = Query(None),
    date_to:        Optional[str]   = Query(None),
):
    """Receipt-level drill-down of discounted bills, newest/biggest first.

    Lets a manager see the actual bills behind an abnormally high discount total —
    receipt no, date/time, cashier, table, bill value and the discount given, plus
    the discount as a % of the bill's gross. Also returns a summary (count, total,
    average, max, and the p95 threshold) so "abnormally high" can be flagged.

    Drill filters used by the Discounts tab:
      amount=N      → bills with exactly that discount value (common fixed amounts).
      min_pct/max_pct → discount as % of bill gross within [min,max) (pattern buckets).
      gross_zero=1  → bills whose gross is <= 0 (discount-only / zeroed bills).
    """
    gross_expr = "(st.food_subtotal + st.drink_subtotal + st.other_subtotal)"
    # TRUE pre-discount bill value (ex-VAT, ex-service). The three *_subtotal
    # columns only capture à-la-carte lines — for a buffet bill the per-head
    # buffet charge is NOT in them, so discount / subtotal overshot wildly
    # (221 bills read >100%, e.g. discount 2,962 on an 825 subtotal but an
    #  8,851 bill). Reconstruct the real base from what was actually paid
    # (total_inc_vat, minus service, de-VATed) plus the discount added back.
    # For à-la-carte bills this reproduces the subtotal to within ~0.3%.
    base_expr = "((st.total_inc_vat - COALESCE(st.service_charge,0)) / 1.07 + st.discount)"
    filters = ["b.name != 'ALL'", "st.discount > 0"]
    params: dict = {}
    if branch:
        filters.append("b.name = :branch")
        params["branch"] = branch
    if cashier:
        filters.append("st.cashier = :cashier")
        params["cashier"] = cashier
    if min_discount > 0:
        filters.append("st.discount >= :min_discount")
        params["min_discount"] = min_discount
    if amount is not None:
        filters.append("st.discount = :amount")
        params["amount"] = amount
    if reprinted_only:
        filters.append("COALESCE(st.print_times, 0) > 1")
    if gross_zero:
        filters.append(f"{gross_expr} <= 0")
    else:
        # percentage-of-bill bucket (only meaningful when gross > 0)
        if min_pct is not None:
            filters.append(f"{base_expr} > 0 AND 100.0 * st.discount / {base_expr} >= :min_pct")
            params["min_pct"] = min_pct
        if max_pct is not None:
            filters.append(f"{base_expr} > 0 AND 100.0 * st.discount / {base_expr} < :max_pct")
            params["max_pct"] = max_pct
    _apply_period_filter(filters, params, date_from, date_to, month_from, month_to, month)
    where = "WHERE " + " AND ".join(filters)

    order = {
        "discount": "st.discount DESC",
        "pct":      "disc_pct DESC",
        "date":     "st.sold_at DESC",
        "total":    "st.total_inc_vat DESC",
        "reprints": "COALESCE(st.print_times, 0) DESC, st.discount DESC",
    }[sort]
    params["limit"] = limit

    with get_db() as con:
        # Per-bill discount reason + coupon name + payment method (synced via
        # sync_mssql). Tables/columns may be absent on a DB synced before they
        # existed → degrade gracefully (NULL / 0).
        tabs = {r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name IN "
            "('pos_discount_reason','coupon_series','sale_payment','sale_bill_item','sale_bill_cogs')")}
        stcols = {r[1] for r in con.execute("PRAGMA table_info(sales_transaction)")}
        has_type = {"discount_reason_id", "coupon_series_id"} <= stcols
        join_r = " LEFT JOIN pos_discount_reason dr ON dr.reason_id = st.discount_reason_id" if ("pos_discount_reason" in tabs and has_type) else ""
        join_c = " LEFT JOIN coupon_series cs ON cs.coupon_series_id = st.coupon_series_id" if ("coupon_series" in tabs and has_type) else ""
        sel_r = "dr.reason_desc" if join_r else "NULL"
        sel_c = "cs.name" if join_c else "NULL"
        # Per-bill BoM COGS → powers the กำไร/ขาดทุน (P&L) column in the table and seeds
        # the per-bill P&L drill. SAME source & priority as /reports/pnl-bill-items: the
        # LIVE sum of sale_bill_item menu lines (qty × effective unit cost, BUFFET tier=0)
        # when those lines exist (ALL discount bills, full synced history), else the
        # rolling 31-day sale_bill_cogs snapshot. NULL when neither is present (pre-sync)
        # → the column shows "—". Note: net revenue is total_inc_vat/1.07 (Sale.Total is
        # ALREADY post-discount), so the client renders P&L = net − COGS — the same
        # definition as บิลขาดทุน GP, NOT net − discount.
        if "sale_bill_item" in tabs:
            _build_sku_effective_cost(con, force=False)   # ensure the unit-cost lookup exists
            _live_cogs = (f"(SELECT SUM(i.qty * CASE WHEN s2.sku_code IN {_EASY_TIER_CODES_SQL} THEN 0 "
                          f"ELSE COALESCE((SELECT sec.recipe_cost FROM sku_effective_cost sec "
                          f"WHERE sec.sku_id=i.sku_id),0) END) "
                          f"FROM sale_bill_item i JOIN skus s2 ON s2.sku_id=i.sku_id "
                          f"WHERE i.receipt_no=st.receipt_no)")
        else:
            _live_cogs = "NULL"
        _snap_cogs = ("(SELECT c.cogs_bom FROM sale_bill_cogs c WHERE c.receipt_no=st.receipt_no)"
                      if "sale_bill_cogs" in tabs else "NULL")
        sel_cogs = (f"ROUND(COALESCE({_live_cogs}, {_snap_cogs}))"
                    if (_live_cogs != "NULL" or _snap_cogs != "NULL") else "NULL")
        # Payment method per receipt. A bill can split across codes → show the
        # DOMINANT (largest-amount) code; payment_n = how many distinct codes.
        has_pay = "sale_payment" in tabs
        _pc = "CASE WHEN UPPER(sp.payment_code)='CASH' THEN 'Cash' ELSE sp.payment_code END"
        sel_pay  = (f"(SELECT {_pc} FROM sale_payment sp WHERE sp.branch_id=st.branch_id "
                    "AND sp.receipt_no=st.receipt_no ORDER BY sp.amount DESC LIMIT 1)" if has_pay else "NULL")
        sel_payn = (f"(SELECT COUNT(DISTINCT {_pc}) FROM sale_payment sp WHERE sp.branch_id=st.branch_id "
                    "AND sp.receipt_no=st.receipt_no)" if has_pay else "0")
        # payment-method filter (by dominant code). Keep `where_base` (scope WITHOUT
        # the payment filter) for the dropdown option list below.
        where_base = where
        if payment and has_pay:
            filters.append(f"{sel_pay} = :payment")
            params["payment"] = payment
            where = "WHERE " + " AND ".join(filters)
        rows = con.execute(
            f"""
            SELECT st.receipt_no, st.sold_at, st.cashier, st.table_no,
                   st.customer_count,
                   b.name AS branch,
                   ROUND(st.total_inc_vat, 2)                    AS total_inc_vat,
                   ROUND(st.discount, 2)                         AS discount,
                   ROUND(st.sp_discount, 2)                      AS sp_discount,
                   (st.food_subtotal + st.drink_subtotal + st.other_subtotal) AS gross,
                   ROUND({base_expr}, 2)                         AS bill_base,
                   ROUND(100.0 * st.discount / NULLIF({base_expr}, 0), 1) AS disc_pct,
                   {sel_r} AS reason_desc, {sel_c} AS coupon_name,
                   {sel_pay} AS payment_code, {sel_payn} AS payment_n,
                   COALESCE(st.print_times, 0) AS print_times,
                   {sel_cogs} AS cogs_bom
            FROM sales_transaction st
            JOIN branches b ON b.branch_id = st.branch_id
            {join_r}{join_c}
            {where}
            ORDER BY {order}
            LIMIT :limit
            """,
            params,
        ).fetchall()

        summary = con.execute(
            f"""
            SELECT COUNT(*) AS bills,
                   ROUND(SUM(st.discount), 2) AS total_discount,
                   ROUND(AVG(st.discount), 2) AS avg_discount,
                   ROUND(MAX(st.discount), 2) AS max_discount,
                   SUM(CASE WHEN COALESCE(st.print_times,0) > 1 THEN 1 ELSE 0 END) AS reprinted_bills,
                   MAX(COALESCE(st.print_times, 0)) AS max_print_times
            FROM sales_transaction st
            JOIN branches b ON b.branch_id = st.branch_id
            {where}
            """,
            params,
        ).fetchone()

        # Distinct payment codes available in this scope (ignoring the payment
        # filter) → powers the "filter by วิธีจ่าย" dropdown.
        payment_codes = []
        if has_pay:
            base_params = {k: v for k, v in params.items() if k != "payment"}
            # dominant tender per bill via indexed lookup over only the scope's bills —
            # not a ROW_NUMBER() window over the whole sale_payment table.
            dom_code = ("(SELECT CASE WHEN UPPER(sp.payment_code)='CASH' THEN 'Cash' ELSE sp.payment_code END "
                        "FROM sale_payment sp WHERE sp.branch_id=st.branch_id AND sp.receipt_no=st.receipt_no "
                        "ORDER BY sp.amount DESC LIMIT 1)")
            pc_rows = con.execute(
                f"""
                SELECT code, COUNT(*) AS n FROM (
                  SELECT {dom_code} AS code
                  FROM sales_transaction st JOIN branches b ON b.branch_id = st.branch_id
                  {where_base}
                ) WHERE code IS NOT NULL
                GROUP BY code ORDER BY n DESC
                """,
                base_params,
            ).fetchall()
            payment_codes = [r["code"] for r in pc_rows if r["code"]]

    # p95 of discount amount — anything above is "abnormally high" for this scope.
    discs = sorted((r["discount"] or 0) for r in rows)
    p95 = discs[min(len(discs) - 1, int(len(discs) * 0.95))] if discs else 0
    return {
        "bills": [dict(r) for r in rows],
        "summary": {
            **(dict(summary) if summary else {}),
            "p95_discount": round(p95, 2),
            "returned": len(rows),
        },
        "payment_codes": payment_codes,
        "payment": payment,
    }


# ---------------------------------------------------------------------------
# /api/analysis/discount_composition  -  per-branch discount split by type
# ---------------------------------------------------------------------------
@app.get("/api/analysis/discount_composition")
@ttl_cache
def analysis_discount_composition(
    month:      Optional[str] = Query(None),
    month_from: Optional[str] = Query(None),
    month_to:   Optional[str] = Query(None),
    branch:     Optional[str] = Query(None),
    group:      Optional[str] = Query(None),
):
    """Per-branch discount composition by discount-as-%-of-bill bucket, for a period.

    Lets §9 of the report show the *nature* of each branch's discounting (mostly
    small markdowns vs heavy 50%+/free bills) and compare it month-over-month.
    Buckets mirror the discounts-tab pattern groups. Amounts are baht of discount.
    """
    # fix D2: use reconstructed pre-discount base to avoid overstating disc% on buffet bills
    gross = "((st.total_inc_vat - COALESCE(st.service_charge,0)) / 1.07 + st.discount)"
    bucket = f"""CASE
        WHEN {gross} <= 0            THEN 'zero_gross'
        WHEN st.discount >= {gross}  THEN 'free'
        WHEN st.discount*1.0/{gross} >= 0.5 THEN 'ge50'
        WHEN st.discount*1.0/{gross} >= 0.2 THEN 'p20_50'
        WHEN st.discount*1.0/{gross} >= 0.1 THEN 'p10_20'
        ELSE 'lt10' END"""
    filters = ["b.name != 'ALL'", "st.discount > 0"]
    params: dict = {}
    if branch:
        filters.append("b.name = :branch")
        params["branch"] = branch
    _group_filter(filters, params, group)
    _month_range_filter(filters, params, month_from, month_to, month, col="strftime('%Y-%m', st.sold_at)")
    where = "WHERE " + " AND ".join(filters)

    with get_db() as con:
        rows = con.execute(f"""
            SELECT b.name AS branch, {bucket} AS bucket,
                   COUNT(*) AS bills, ROUND(SUM(st.discount), 2) AS baht
            FROM sales_transaction st JOIN branches b ON b.branch_id = st.branch_id
            {where}
            GROUP BY b.name, bucket
        """, params).fetchall()

    keys = ["free", "ge50", "p20_50", "p10_20", "lt10", "zero_gross"]
    per: dict = {}
    for r in rows:
        d = per.setdefault(r["branch"], {
            "branch": r["branch"], "total_discount": 0.0, "bills": 0,
            "buckets": {k: 0.0 for k in keys}, "bucket_bills": {k: 0 for k in keys},
        })
        d["buckets"][r["bucket"]] = r["baht"] or 0
        d["bucket_bills"][r["bucket"]] = r["bills"] or 0
        d["total_discount"] += r["baht"] or 0
        d["bills"] += r["bills"] or 0
    out = sorted(per.values(), key=lambda x: -x["total_discount"])
    for d in out:
        d["total_discount"] = round(d["total_discount"], 2)
        # dominant bucket by baht
        d["dominant"] = max(d["buckets"], key=lambda k: d["buckets"][k]) if d["total_discount"] else None
    return {"bucket_keys": keys, "branches": out}


# ---------------------------------------------------------------------------
# /api/analysis/discount_leakage  -  per-branch discount KPI boxes + leakage summary
# ---------------------------------------------------------------------------
@app.get("/api/analysis/discount_leakage")
@ttl_cache
def analysis_discount_leakage(
    month:      Optional[str] = Query(None),
    month_from: Optional[str] = Query(None),
    month_to:   Optional[str] = Query(None),
    date_from:  Optional[str] = Query(None),
    date_to:    Optional[str] = Query(None),
):
    """Per-branch discount KPI boxes for the 'Discounts & Leakage' view, plus a
    cross-branch comparison and an 'abnormal usage' summary.

    Always spans ALL branches (this view IS a cross-branch comparison), honouring
    only the period filter. disc% uses the RECONSTRUCTED pre-discount base
    ((total_inc_vat - service)/1.07 + discount) — the same base as discount_bills,
    NOT food+drink+other_subtotal which undercounts buffet bills and overstates
    disc% (see discount_bills for the rationale). So this %, by design, will not
    match the legacy /api/analysis/discounts disc_pct_of_sales.
    """
    base_expr = "((st.total_inc_vat - COALESCE(st.service_charge,0)) / 1.07 + st.discount)"
    filters = ["b.name != 'ALL'"]
    params: dict = {}
    _apply_period_filter(filters, params, date_from, date_to, month_from, month_to, month)
    where = "WHERE " + " AND ".join(filters)
    eff_mf, eff_mt = _eff_month_range(date_from, date_to, month_from, month_to, month)

    with get_db() as con:
        rows = con.execute(f"""
            SELECT b.name AS branch,
                   COUNT(*) AS total_tx,
                   SUM(CASE WHEN st.discount > 0 THEN 1 ELSE 0 END) AS discount_tx,
                   ROUND(SUM(st.discount), 2) AS total_discount,
                   ROUND(AVG(CASE WHEN st.discount > 0 THEN st.discount END), 0) AS avg_discount,
                   ROUND(SUM({base_expr}), 2) AS gross_base
            FROM sales_transaction st JOIN branches b ON b.branch_id = st.branch_id
            {where}
            GROUP BY b.name
        """, params).fetchall()

        # Per-branch top discount REASON (Sale.DiscountReasonId → DiscountSaleReason),
        # staged into pos_discount_month by sync. Absent on older DBs → skip gracefully.
        reasons_staged = bool(con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='pos_discount_month'").fetchone())
        reason_rows = []
        if reasons_staged:
            rfilters = ["b.name != 'ALL'"]; rparams: dict = {}
            _month_range_filter(rfilters, rparams, eff_mf, eff_mt, col="dm.period_month")
            reason_rows = con.execute(f"""
                SELECT b.name AS branch, COALESCE(dm.reason_id,0) AS reason_id,
                       COALESCE(dr.reason_desc,
                                CASE WHEN COALESCE(dm.reason_id,0)=0 THEN 'ไม่ระบุ (ไม่เลือกเหตุผล)'
                                     ELSE 'รหัส '||dm.reason_id END) AS reason,
                       ROUND(SUM(dm.amount)) AS amount, SUM(dm.bills) AS bills
                FROM pos_discount_month dm JOIN branches b ON b.branch_id = dm.branch_id
                LEFT JOIN pos_discount_reason dr ON dr.reason_id = dm.reason_id
                WHERE {' AND '.join(rfilters)}
                GROUP BY b.name, COALESCE(dm.reason_id,0), reason
            """, rparams).fetchall()

    # Build per-branch reason map: top reason (by amount) + 'untyped' share (the
    # biggest controllable leak — cashier discounted without picking a reason).
    by_reason: dict = {}
    for r in reason_rows:
        by_reason.setdefault(r["branch"], []).append(dict(r))
    reason_top: dict = {}
    for br, lst in by_reason.items():
        tot = sum(x["amount"] or 0 for x in lst) or 0
        top = max(lst, key=lambda x: x["amount"] or 0) if lst else None
        untyped = next((x for x in lst if x["reason_id"] == 0), None)
        reason_top[br] = {
            "top_reason":        (top["reason"] if top else None),
            "top_reason_amount": (top["amount"] if top else 0),
            "top_reason_pct":    (round(100 * (top["amount"] or 0) / tot, 1) if (top and tot) else 0),
            "untyped_pct":       (round(100 * (untyped["amount"] or 0) / tot, 1) if (untyped and tot) else 0),
        }

    branches = []
    for r in rows:
        d = dict(r)
        gb = d.get("gross_base") or 0
        d["disc_pct"]      = round(100.0 * (d.get("total_discount") or 0) / gb, 2) if gb > 0 else 0
        d["discount_rate"] = round(100.0 * (d.get("discount_tx") or 0) / d["total_tx"], 1) if d["total_tx"] else 0
        d.update(reason_top.get(d["branch"], {
            "top_reason": None, "top_reason_amount": 0, "top_reason_pct": 0, "untyped_pct": 0}))
        branches.append(d)

    # Chain baseline (weighted): total discount / total pre-discount base, all branches.
    chain_disc = sum(b.get("total_discount") or 0 for b in branches)
    chain_base = sum(b.get("gross_base") or 0 for b in branches)
    chain_tx   = sum(b.get("total_tx") or 0 for b in branches)
    chain_dtx  = sum(b.get("discount_tx") or 0 for b in branches)
    chain_pct  = round(100.0 * chain_disc / chain_base, 2) if chain_base > 0 else 0

    # Abnormal = disc% clearly above the typical branch: >= 1.5× the branch median
    # (floor 3% so nothing is flagged when everyone discounts lightly) AND above the
    # chain average. Threshold is returned so the UI can show the rule.
    pcts = sorted(b["disc_pct"] for b in branches if (b.get("gross_base") or 0) > 0)
    median = pcts[len(pcts)//2] if pcts else 0
    threshold = round(max(median * 1.5, 3.0), 2)
    for b in branches:
        b["vs_avg_pts"]     = round(b["disc_pct"] - chain_pct, 2)
        b["ratio_vs_chain"] = round(b["disc_pct"] / chain_pct, 2) if chain_pct > 0 else None
        b["is_abnormal"]    = (b.get("gross_base") or 0) > 0 and b["disc_pct"] >= threshold and b["disc_pct"] > chain_pct
    branches.sort(key=lambda x: -x["disc_pct"])

    abnormal = [b for b in branches if b["is_abnormal"]]
    if abnormal:
        parts = [f"{b['branch']} {b['disc_pct']:.2f}%"
                 + (f" (×{b['ratio_vs_chain']:.2f} ของค่าเฉลี่ย)" if b["ratio_vs_chain"] else "")
                 + (f", ส่วนใหญ่: {b['top_reason']}" if b.get("top_reason") else "")
                 for b in abnormal[:6]]
        headline = (f"🚩 {len(abnormal)} สาขาใช้ส่วนลดสูงผิดปกติ (เกิน {threshold:.2f}% ของยอดก่อนลด · "
                    f"ค่าเฉลี่ยทุกสาขา {chain_pct:.2f}%): " + " · ".join(parts))
    else:
        headline = (f"✓ ไม่พบสาขาที่ใช้ส่วนลดผิดปกติ — ทุกสาขาต่ำกว่าเกณฑ์ {threshold:.2f}% "
                    f"(ค่าเฉลี่ยทุกสาขา {chain_pct:.2f}% ของยอดก่อนลด)")

    return {
        "chain": {"total_discount": round(chain_disc, 2), "gross_base": round(chain_base, 2),
                  "disc_pct": chain_pct, "total_tx": chain_tx, "discount_tx": chain_dtx,
                  "discount_rate": round(100.0 * chain_dtx / chain_tx, 1) if chain_tx else 0,
                  "median_branch_pct": round(median, 2), "threshold_pct": threshold,
                  "branch_count": len(branches)},
        "branches": branches,
        "abnormal_branches": [b["branch"] for b in abnormal],
        "summary": headline,
        "reasons_staged": reasons_staged,
    }


# ---------------------------------------------------------------------------
# /api/analysis/payment_discount  -  payment method × discount cross-analysis
# ---------------------------------------------------------------------------
@app.get("/api/analysis/payment_discount")
@ttl_cache
def analysis_payment_discount(
    month:      Optional[str] = Query(None),
    month_from: Optional[str] = Query(None),
    month_to:   Optional[str] = Query(None),
    date_from:  Optional[str] = Query(None),
    date_to:    Optional[str] = Query(None),
):
    """Payment-method × discount cross-analysis for the Discounts tab:
      • mix       — payment-code split on discounted bills (by dominant code)
      • heaviest  — which payment type gets the deepest discounts (avg disc% of bill)
      • cash_heavy — the leakage flag: cash-paid bills discounted >=20% of bill,
                     by branch (cash + big markdown = highest shrinkage risk)
    disc% uses the reconstructed pre-discount base. Payment code is Cash-normalized
    at query level (POS has 'CASH'/'Cash' dups). Needs the per-bill sale_payment
    table (sync_mssql) → returns available=False if it isn't synced yet.
    """
    base = "((st.total_inc_vat - COALESCE(st.service_charge,0))/1.07 + st.discount)"
    HEAVY = 0.20   # discount >= 20% of the (pre-discount) bill = "heavy"
    filters = ["b.name != 'ALL'", "st.discount > 0"]
    params: dict = {}
    _apply_period_filter(filters, params, date_from, date_to, month_from, month_to, month)
    where = "WHERE " + " AND ".join(filters)

    with get_db() as con:
        if not con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='sale_payment'").fetchone():
            return {"available": False, "mix": [], "heaviest": [], "cash_heavy": {},
                    "heavy_pct": int(HEAVY * 100)}
        # Dominant (largest-amount) payment code per discounted receipt, Cash-normalised.
        # Computed per period bill via an indexed (branch_id, receipt_no, amount) lookup
        # in a derived table — NOT a ROW_NUMBER() window over the entire sale_payment
        # table (that scan made this endpoint ~700ms). bill_src yields one row per
        # discounted bill in scope with its dominant tender; both aggregates reuse it.
        dom_code = ("(SELECT CASE WHEN UPPER(sp.payment_code)='CASH' THEN 'Cash' ELSE sp.payment_code END "
                    "FROM sale_payment sp WHERE sp.branch_id=st.branch_id AND sp.receipt_no=st.receipt_no "
                    "ORDER BY sp.amount DESC LIMIT 1)")
        bill_src = (f"SELECT b.name AS branch, st.discount AS discount, {base} AS basev, {dom_code} AS code "
                    f"FROM sales_transaction st JOIN branches b ON b.branch_id=st.branch_id {where}")
        rows = con.execute(f"""
            SELECT code, COUNT(*) AS bills, ROUND(SUM(discount)) AS discount,
                   ROUND(100.0 * SUM(discount) / NULLIF(SUM(basev), 0), 1) AS disc_pct,
                   ROUND(AVG(discount)) AS avg_discount
            FROM ({bill_src})
            WHERE code IS NOT NULL
            GROUP BY code ORDER BY bills DESC""", params).fetchall()
        mix = [dict(r) for r in rows]

        # cash + heavy discount, by branch (the leakage flag)
        ch_rows = con.execute(f"""
            SELECT branch, COUNT(*) AS bills, ROUND(SUM(discount)) AS discount
            FROM ({bill_src})
            WHERE code='Cash' AND basev > 0 AND discount * 1.0 / basev >= {HEAVY}
            GROUP BY branch ORDER BY discount DESC""", params).fetchall()
        cash_heavy_branches = [dict(r) for r in ch_rows]

    total_bills = sum(m["bills"] for m in mix)
    total_disc  = sum(m["discount"] or 0 for m in mix)
    # heaviest payment types (>=50 bills) by disc% of bill
    heaviest = sorted([m for m in mix if m["bills"] >= 50],
                      key=lambda m: -(m["disc_pct"] or 0))[:6]
    ch_bills = sum(b["bills"] for b in cash_heavy_branches)
    ch_disc  = sum(b["discount"] or 0 for b in cash_heavy_branches)
    top = cash_heavy_branches[0] if cash_heavy_branches else None
    return {
        "available": True,
        "heavy_pct": int(HEAVY * 100),
        "total_discount_bills": total_bills,
        "total_discount": total_disc,
        "mix": mix[:14],
        "heaviest": heaviest,
        "cash_heavy": {
            "bills": ch_bills, "discount": ch_disc,
            "share_of_discount": round(100.0 * ch_disc / total_disc, 1) if total_disc else 0,
            "branches": cash_heavy_branches[:10],
            "top_branch": (top["branch"] if top else None),
            "top_branch_discount": (top["discount"] if top else 0),
        },
    }


# ---------------------------------------------------------------------------
# /api/analysis/discount_alerts  -  consolidated discount-risk alert engine
# ---------------------------------------------------------------------------
@app.get("/api/analysis/discount_alerts")
@ttl_cache
def analysis_discount_alerts(
    month:      Optional[str] = Query(None),
    month_from: Optional[str] = Query(None),
    month_to:   Optional[str] = Query(None),
    date_from:  Optional[str] = Query(None),
    date_to:    Optional[str] = Query(None),
):
    """Prioritized discount-risk flags so leakage/fraud is seen without drilling:
      • cash_heavy — branches with big cash + heavy-discount (≥20%) totals
      • untyped    — branches discounting mostly without a reason (control gap)
      • cashier    — cashiers whose disc% is far above the chain median
      • repeat     — same cashier + exact discount amount + cash, repeated (manual-discount abuse)
      • late       — discounts concentrated after 22:00 by branch
    Reconstructed pre-discount base; cash signals need sale_payment (degrade gracefully).
    Each flag carries a `drill` so the UI can open the matching discount_bills view.
    """
    base = "((st.total_inc_vat - COALESCE(st.service_charge,0))/1.07 + st.discount)"
    TH = {"cash_pct": 20, "cash_min": 50000, "untyped_pct": 80, "untyped_min": 50000,  # DEFAULTS
          "cashier_mult": 2.0, "cashier_floor": 5.0, "cashier_min_disc": 30000,
          "repeat_min": 30, "late_from": 22, "late_min_bills": 40, "late_min_share": 25.0,
          "void_min": 20000}
    filters = ["b.name != 'ALL'", "st.discount > 0"]
    params: dict = {}
    _apply_period_filter(filters, params, date_from, date_to, month_from, month_to, month)
    where = "WHERE " + " AND ".join(filters)
    eff_mf, eff_mt = _eff_month_range(date_from, date_to, month_from, month_to, month)
    flags = []
    def thb(x): return f"{(x or 0):,.0f}"
    with get_db() as con:
        # Configurable overrides (Settings key 'discount_alert_thresholds', JSON).
        # SECURITY: these values are interpolated into SQL → accept only known keys
        # and coerce to numbers; never let a raw string through.
        try:
            row = con.execute("SELECT value FROM app_settings WHERE key='discount_alert_thresholds'").fetchone()
            if row and row[0]:
                stored = _json.loads(row[0])
                for _k in TH:
                    if _k in stored:
                        try: TH[_k] = float(stored[_k])
                        except (TypeError, ValueError): pass
        except Exception:
            pass
        has_pay = bool(con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='sale_payment'").fetchone())
        dom = ("WITH dom AS (SELECT branch_id, receipt_no, "
               "CASE WHEN UPPER(payment_code)='CASH' THEN 'Cash' ELSE payment_code END code, "
               "ROW_NUMBER() OVER (PARTITION BY branch_id,receipt_no ORDER BY amount DESC) rn FROM sale_payment)")

        # 1) cash + heavy discount, by branch
        if has_pay:
            for r in con.execute(f"""{dom}
                SELECT b.name br, COUNT(*) bills, ROUND(SUM(st.discount)) disc
                FROM sales_transaction st JOIN branches b ON b.branch_id=st.branch_id
                JOIN dom d ON d.branch_id=st.branch_id AND d.receipt_no=st.receipt_no AND d.rn=1
                {where} AND d.code='Cash' AND {base}>0 AND st.discount*1.0/{base} >= {TH['cash_pct']/100.0}
                GROUP BY b.name HAVING SUM(st.discount) >= {TH['cash_min']} ORDER BY disc DESC LIMIT 6""", params):
                flags.append({"type": "cash_heavy", "severity": "high" if (r["disc"] or 0) >= 200000 else "med",
                    "branch": r["br"], "title": "เงินสด + ลดหนัก",
                    "text": f"{r['br']}: บิลเงินสดที่ลด ≥{TH['cash_pct']}% — {r['bills']:,} บิล · ฿{thb(r['disc'])}",
                    "amount": r["disc"], "drill": {"branch": r["br"], "payment": "Cash"}})

        # 2) untyped-reason share high, by branch (from pos_discount_month)
        if con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='pos_discount_month'").fetchone():
            rf = ["b.name!='ALL'"]; rp: dict = {}
            _month_range_filter(rf, rp, eff_mf, eff_mt, col="dm.period_month")
            for r in con.execute(f"""
                SELECT b.name br, ROUND(SUM(dm.amount)) tot,
                       ROUND(SUM(CASE WHEN COALESCE(dm.reason_id,0)=0 THEN dm.amount ELSE 0 END)) unty
                FROM pos_discount_month dm JOIN branches b ON b.branch_id=dm.branch_id
                WHERE {' AND '.join(rf)} GROUP BY b.name""", rp):
                tot, unty = (r["tot"] or 0), (r["unty"] or 0)
                if tot >= TH["untyped_min"] and unty >= TH["untyped_min"] and 100.0*unty/tot >= TH["untyped_pct"]:
                    pct = round(100.0*unty/tot, 1)
                    flags.append({"type": "untyped", "severity": "med", "branch": r["br"], "title": "ลดไม่ระบุเหตุผล",
                        "text": f"{r['br']}: ลดโดยไม่เลือกเหตุผล {pct}% (฿{thb(unty)}) — คุมนโยบายไม่ได้",
                        "amount": unty, "drill": {"branch": r["br"]}})

        # 3) cashier disc% outliers (>= 2× branch-cashier median, floor 5%)
        crows = con.execute(f"""
            SELECT st.cashier ca, b.name br, COUNT(*) bills, ROUND(SUM(st.discount)) disc,
                   ROUND(100.0*SUM(st.discount)/NULLIF(SUM({base}),0),1) pct
            FROM sales_transaction st JOIN branches b ON b.branch_id=st.branch_id
            {where} AND st.cashier<>'' GROUP BY st.cashier, b.name HAVING SUM(st.discount) >= {TH['cashier_min_disc']}""",
            params).fetchall()
        pcts = sorted([r["pct"] for r in crows if r["pct"] is not None])
        med = pcts[len(pcts)//2] if pcts else 0
        thr = max(med*TH["cashier_mult"], TH["cashier_floor"])
        ncash = 0
        for r in sorted(crows, key=lambda x: -(x["pct"] or 0)):
            if (r["pct"] or 0) >= thr and ncash < 6:
                ncash += 1
                flags.append({"type": "cashier", "severity": "high" if (r["pct"] or 0) >= thr*1.5 else "med",
                    "branch": r["br"], "cashier": r["ca"], "title": "แคชเชียร์ลดเกินเกณฑ์",
                    "text": f"{r['ca']} ({r['br']}): ลด {r['pct']}% ของยอด (เกณฑ์ {thr:.1f}%) · ฿{thb(r['disc'])}",
                    "amount": r["disc"], "drill": {"branch": r["br"], "cashier": r["ca"]}})

        # 4) repeat exact-amount cash discounts by the same cashier (manual-discount abuse)
        if has_pay:
            for r in con.execute(f"""{dom}
                SELECT b.name br, st.cashier ca, st.discount amt, COUNT(*) n, ROUND(SUM(st.discount)) tot
                FROM sales_transaction st JOIN branches b ON b.branch_id=st.branch_id
                JOIN dom d ON d.branch_id=st.branch_id AND d.receipt_no=st.receipt_no AND d.rn=1
                {where} AND d.code='Cash' AND st.cashier<>''
                GROUP BY b.name, st.cashier, st.discount HAVING COUNT(*) >= {TH['repeat_min']}
                ORDER BY n DESC LIMIT 5""", params):
                flags.append({"type": "repeat", "severity": "high" if r["n"] >= 80 else "med",
                    "branch": r["br"], "cashier": r["ca"], "title": "ลดเงินสดค่าเดิมซ้ำ",
                    "text": f"{r['ca']} ({r['br']}): ลดเงินสด ฿{thb(r['amt'])} ซ้ำ {r['n']:,} ครั้ง (฿{thb(r['tot'])})",
                    "amount": r["tot"], "drill": {"branch": r["br"], "cashier": r["ca"], "payment": "Cash", "amount": r["amt"]}})

        # 5) late-hour discount clustering, by branch
        for r in con.execute(f"""
            SELECT b.name br,
                   SUM(CASE WHEN CAST(strftime('%H',st.sold_at) AS INT) >= {TH['late_from']} THEN 1 ELSE 0 END) late_bills,
                   COUNT(*) bills,
                   ROUND(SUM(CASE WHEN CAST(strftime('%H',st.sold_at) AS INT) >= {TH['late_from']} THEN st.discount ELSE 0 END)) late_disc
            FROM sales_transaction st JOIN branches b ON b.branch_id=st.branch_id
            {where} GROUP BY b.name""", params):
            lb, tb = (r["late_bills"] or 0), (r["bills"] or 1)
            share = 100.0*lb/tb
            if lb >= TH["late_min_bills"] and share >= TH["late_min_share"]:
                flags.append({"type": "late", "severity": "med", "branch": r["br"], "title": "ลดช่วงดึก",
                    "text": f"{r['br']}: ลดหลัง {TH['late_from']}:00 — {lb:,} บิล ({share:.0f}% ของบิลลด) · ฿{thb(r['late_disc'])}",
                    "amount": r["late_disc"], "drill": {"branch": r["br"]}})

        # 6) baseline drift — a branch's latest month disc% vs its OWN trailing-3 avg
        # (full history, ignores the period filter; catches a branch trending worse).
        bd: dict = {}
        for r in con.execute(f"""
            SELECT b.name br, strftime('%Y-%m', st.sold_at) m,
                   ROUND(100.0*SUM(st.discount)/NULLIF(SUM({base}),0),2) pct, ROUND(SUM(st.discount)) disc
            FROM sales_transaction st JOIN branches b ON b.branch_id=st.branch_id
            WHERE b.name!='ALL' GROUP BY b.name, m HAVING SUM({base}) > 0"""):
            bd.setdefault(r["br"], []).append((r["m"], r["pct"] or 0, r["disc"] or 0))
        for br_name, series in bd.items():
            series.sort()
            if len(series) < 4:
                continue
            latest = series[-1]
            tavg = sum(x[1] for x in series[-4:-1]) / 3.0   # 3 months before the latest
            if tavg > 0 and latest[1] >= 1.5*tavg and latest[1] >= 3.0 and latest[2] >= 50000:
                flags.append({"type": "drift", "severity": "med", "branch": br_name, "title": "ส่วนลดพุ่งผิดปกติ",
                    "text": f"{br_name}: เดือน {latest[0]} ลด {latest[1]:.1f}% ของยอด (ปกติ ~{tavg:.1f}%) — พุ่ง {latest[1]/tavg:.1f}× เทียบตัวเอง",
                    "amount": latest[2], "drill": {"branch": br_name, "month_from": latest[0], "month_to": latest[0]}})

        # 7) discount applied then bill VOIDED — manipulation risk (needs the
        # discount column on pos_cancel_bill_detail, added 2026-06-09 → guard).
        try:
            ccols = {rr[1] for rr in con.execute("PRAGMA table_info(pos_cancel_bill_detail)")}
        except Exception:
            ccols = set()
        if "discount" in ccols:
            vf = ["b.name!='ALL'"]; vp: dict = {}
            _month_range_filter(vf, vp, eff_mf, eff_mt, col="cd.period_month")
            for r in con.execute(f"""
                SELECT b.name br, COUNT(*) n, ROUND(SUM(cd.discount)) disc
                FROM pos_cancel_bill_detail cd JOIN branches b ON b.branch_id=cd.branch_id
                WHERE {' AND '.join(vf)} AND cd.discount > 0
                GROUP BY b.name HAVING SUM(cd.discount) >= {TH['void_min']} ORDER BY disc DESC LIMIT 6""", vp):
                flags.append({"type": "void", "severity": "high" if (r["disc"] or 0) >= 50000 else "med",
                    "branch": r["br"], "title": "ลดแล้วยกเลิกบิล",
                    "text": f"{r['br']}: บิล 'ลดแล้วถูกยกเลิก' {r['n']:,} ใบ · ส่วนลดรวม ฿{thb(r['disc'])} — เสี่ยงแก้ไข/ลบบิล"})
                    # (no drill: voided bills aren't in sales_transaction; review via cancel-bills detail)

    rank = {"high": 0, "med": 1}
    flags.sort(key=lambda f: (rank.get(f["severity"], 2), -(f.get("amount") or 0)))
    return {
        "flags": flags, "thresholds": TH, "has_payment": has_pay,
        "counts": {"high": sum(1 for f in flags if f["severity"] == "high"),
                   "med":  sum(1 for f in flags if f["severity"] == "med"), "total": len(flags)},
    }


# ===========================================================================
# NEW ADVANCED FEATURES  -  period compare, heatmap, anomalies, health, search
# ===========================================================================

def _build_period_filters(branch, month_from, month_to, date_from, date_to,
                          col_month="strftime('%Y-%m', st.sold_at)",
                          col_date="DATE(st.sold_at)"):
    """Helper: build SQL WHERE clauses from period range params (supports both month and date)."""
    filters = ["b.name != 'ALL'"]
    params: dict = {}
    if branch:
        filters.append("b.name = :branch")
        params["branch"] = branch
    if date_from:
        filters.append(f"{col_date} >= :date_from")
        params["date_from"] = date_from
    if date_to:
        filters.append(f"{col_date} <= :date_to")
        params["date_to"] = date_to
    if month_from and not date_from:
        filters.append(f"{col_month} >= :month_from")
        params["month_from"] = month_from
    if month_to and not date_to:
        filters.append(f"{col_month} <= :month_to")
        params["month_to"] = month_to
    return filters, params


def _fetch_period_kpis(con, branch, month_from, month_to, date_from, date_to):
    """Aggregate KPIs from sales_transaction over an arbitrary period."""
    filters, params = _build_period_filters(branch, month_from, month_to, date_from, date_to)
    where = "WHERE " + " AND ".join(filters)
    row = con.execute(
        f"""
        SELECT COUNT(*)                                  AS tx_count,
               COALESCE(SUM(st.customer_count), 0)      AS customer_count,
               ROUND(COALESCE(SUM(st.total_inc_vat) / 1.07, 0), 2) AS revenue,
               ROUND(COALESCE(AVG(st.total_inc_vat), 0), 2) AS avg_ticket,
               ROUND(COALESCE(SUM(st.discount), 0), 2)  AS discount,
               ROUND(COALESCE(SUM(st.food_subtotal), 0), 2)  AS food,
               ROUND(COALESCE(SUM(st.drink_subtotal), 0), 2) AS drink
        FROM sales_transaction st
        JOIN branches b ON b.branch_id = st.branch_id
        {where}
        """,
        params,
    ).fetchone()

    # Top branches in this period
    branches_top = con.execute(
        f"""
        SELECT b.name AS branch,
               ROUND(SUM(st.total_inc_vat) / 1.07, 2) AS revenue,
               COUNT(*) AS tx_count
        FROM sales_transaction st
        JOIN branches b ON b.branch_id = st.branch_id
        {where}
        GROUP BY b.name
        ORDER BY revenue DESC
        LIMIT 10
        """,
        params,
    ).fetchall()

    # Top SKUs (uses sales_sku_monthly w/ month range only) — FOOD-ONLY so the
    # report's "Top SKUs" lists real dishes, not the deprecated BUFFET aggregate
    # or service charge (see _FOOD_CATEGORY_FILTER; needs the categories join).
    sku_filters = ["b.name != 'ALL'", _FOOD_CATEGORY_FILTER]
    sku_params: dict = {}
    if branch:
        sku_filters.append("b.name = :branch")
        sku_params["branch"] = branch
    # Convert date_from/to to month range if necessary
    mf = month_from or (date_from[:7] if date_from else None)
    mt = month_to   or (date_to[:7]   if date_to   else None)
    if mf:
        sku_filters.append("sm.period_month >= :mf")
        sku_params["mf"] = mf
    if mt:
        sku_filters.append("sm.period_month <= :mt")
        sku_params["mt"] = mt
    sku_where = "WHERE " + " AND ".join(sku_filters)
    skus_top = con.execute(
        f"""
        SELECT sk.sku_code, COALESCE(sk.description, sk.sku_code) AS description,
               ROUND(SUM(sm.subtotal_ex_vat), 2) AS revenue,
               ROUND(SUM(sm.qty), 0)            AS qty
        FROM sales_sku_monthly sm
        JOIN branches b ON b.branch_id = sm.branch_id
        JOIN skus sk    ON sk.sku_id    = sm.sku_id
        JOIN categories c ON c.category_id = sk.category_id
        {sku_where}
        GROUP BY sk.sku_id
        ORDER BY revenue DESC
        LIMIT 10
        """,
        sku_params,
    ).fetchall()

    return {
        "revenue":        (row["revenue"]        or 0) if row else 0,
        "tx_count":       (row["tx_count"]       or 0) if row else 0,
        "customer_count": (row["customer_count"] or 0) if row else 0,
        "avg_ticket":     (row["avg_ticket"]     or 0) if row else 0,
        "discount":       (row["discount"]       or 0) if row else 0,
        "food":           (row["food"]           or 0) if row else 0,
        "drink":          (row["drink"]          or 0) if row else 0,
        "top_branches":   [dict(b) for b in branches_top],
        "top_skus":       [dict(s) for s in skus_top],
    }


# ---------------------------------------------------------------------------
# /api/analysis/period_compare   -  A vs B range comparison
# ---------------------------------------------------------------------------
@app.get("/api/analysis/period_compare")
@ttl_cache
def period_compare(
    a_month_from: Optional[str] = Query(None),
    a_month_to:   Optional[str] = Query(None),
    a_date_from:  Optional[str] = Query(None),
    a_date_to:    Optional[str] = Query(None),
    b_month_from: Optional[str] = Query(None),
    b_month_to:   Optional[str] = Query(None),
    b_date_from:  Optional[str] = Query(None),
    b_date_to:    Optional[str] = Query(None),
    c_month_from: Optional[str] = Query(None),
    c_month_to:   Optional[str] = Query(None),
    c_date_from:  Optional[str] = Query(None),
    c_date_to:    Optional[str] = Query(None),
    d_month_from: Optional[str] = Query(None),
    d_month_to:   Optional[str] = Query(None),
    d_date_from:  Optional[str] = Query(None),
    d_date_to:    Optional[str] = Query(None),
    branch:       Optional[str] = Query(None),
):
    """Compare up to 4 arbitrary periods A/B/C/D. Returns KPIs, deltas, top branches/SKUs."""
    def pct(a, b):
        if not b: return None
        return round((a - b) / b * 100, 1)

    def _has(mf, mt, df, dt):
        return any([mf, mt, df, dt])

    def _get_rev(period_data, name):
        if not period_data: return 0
        return next((x["revenue"] for x in period_data["top_branches"] if x["branch"] == name), 0)

    with get_db() as con:
        a = _fetch_period_kpis(con, branch, a_month_from, a_month_to, a_date_from, a_date_to)
        b = _fetch_period_kpis(con, branch, b_month_from, b_month_to, b_date_from, b_date_to)
        c = _fetch_period_kpis(con, branch, c_month_from, c_month_to, c_date_from, c_date_to) \
            if _has(c_month_from, c_month_to, c_date_from, c_date_to) else None
        d = _fetch_period_kpis(con, branch, d_month_from, d_month_to, d_date_from, d_date_to) \
            if _has(d_month_from, d_month_to, d_date_from, d_date_to) else None

    # Collect all branch names across active periods
    all_names = set(
        [br["branch"] for br in a["top_branches"]] +
        [br["branch"] for br in b["top_branches"]] +
        ([br["branch"] for br in c["top_branches"]] if c else []) +
        ([br["branch"] for br in d["top_branches"]] if d else [])
    )

    rank_a = {br["branch"]: i + 1 for i, br in enumerate(a["top_branches"])}
    rank_b = {br["branch"]: i + 1 for i, br in enumerate(b["top_branches"])}
    rank_c = {br["branch"]: i + 1 for i, br in enumerate(c["top_branches"])} if c else {}
    rank_d = {br["branch"]: i + 1 for i, br in enumerate(d["top_branches"])} if d else {}

    branch_changes = []
    for name in all_names:
        rev_a = _get_rev(a, name)
        rev_b = _get_rev(b, name)
        rev_c = _get_rev(c, name)
        rev_d = _get_rev(d, name)
        branch_changes.append({
            "branch":     name,
            "revenue_a":  rev_a, "revenue_b": rev_b,
            "revenue_c":  rev_c, "revenue_d": rev_d,
            "delta_b_pct": pct(rev_a, rev_b),
            "delta_c_pct": pct(rev_a, rev_c) if c else None,
            "delta_d_pct": pct(rev_a, rev_d) if d else None,
            "rank_a": rank_a.get(name), "rank_b": rank_b.get(name),
            "rank_c": rank_c.get(name), "rank_d": rank_d.get(name),
            "rank_shift": (rank_b.get(name, 99) - rank_a.get(name, 99)),
        })
    branch_changes.sort(key=lambda x: -(x["revenue_a"] or 0))

    return {
        "a": {**a, "label": _period_label(a_month_from, a_month_to, a_date_from, a_date_to)},
        "b": {**b, "label": _period_label(b_month_from, b_month_to, b_date_from, b_date_to)},
        "c": {**c, "label": _period_label(c_month_from, c_month_to, c_date_from, c_date_to)} if c else None,
        "d": {**d, "label": _period_label(d_month_from, d_month_to, d_date_from, d_date_to)} if d else None,
        "delta": {
            "revenue":        pct(a["revenue"],        b["revenue"]),
            "tx_count":       pct(a["tx_count"],       b["tx_count"]),
            "customer_count": pct(a["customer_count"], b["customer_count"]),
            "avg_ticket":     pct(a["avg_ticket"],     b["avg_ticket"]),
            "discount":       pct(a["discount"],       b["discount"]),
        },
        "branch_changes": branch_changes,
    }


def _period_label(mf, mt, df, dt):
    if df or dt:
        return f"{df or '...'} â†' {dt or '...'}"
    if mf or mt:
        return f"{mf or '...'} â†' {mt or '...'}"
    return "All time"


# ---------------------------------------------------------------------------
# /api/analysis/heatmap_branch   -  branch  x  period heatmap
# ---------------------------------------------------------------------------
@app.get("/api/analysis/heatmap_branch")
@ttl_cache
def heatmap_branch(
    month_from:  Optional[str] = Query(None),
    month_to:    Optional[str] = Query(None),
    date_from:   Optional[str] = Query(None),
    date_to:     Optional[str] = Query(None),
    granularity: str           = Query("weekly"),  # daily|weekly|monthly
):
    """Branch × period revenue grid (ex-VAT).
    Monthly: Easy SKU-level (sales_sku_monthly). Daily/Weekly: Easy transactions."""

    # ── MONTHLY: Easy SKU-level ────────────────────────────────────────────────
    if granularity == "monthly":
        filters_easy = ["b.name != 'ALL'", "sm.source = 'Easy'"]
        params_easy: dict = {}
        _month_range_filter(filters_easy, params_easy, month_from, month_to, None, col="sm.period_month")
        where_easy = "WHERE " + " AND ".join(filters_easy)

        with get_db() as con:
            easy_rows = con.execute(
                f"""
                SELECT b.name AS branch,
                       sm.period_month AS period,
                       ROUND(SUM(sm.subtotal_ex_vat), 2) AS revenue
                FROM sales_sku_monthly sm
                JOIN branches b ON b.branch_id = sm.branch_id
                {where_easy}
                GROUP BY b.name, sm.period_month
                """,
                params_easy,
            ).fetchall()
        return [{"branch": r["branch"], "period": r["period"], "revenue": r["revenue"] or 0}
                for r in sorted(easy_rows, key=lambda x: (x["branch"], x["period"]))]

    # ── DAILY / WEEKLY: Easy transaction-level ─────────────────────────────────
    period_expr = "DATE(st.sold_at)" if granularity == "daily" else _iso_week_sql('st.sold_at')
    filters, params = _build_period_filters(None, month_from, month_to, date_from, date_to)
    where = "WHERE " + " AND ".join(filters)

    with get_db() as con:
        rows = con.execute(
            f"""
            SELECT b.name AS branch,
                   {period_expr} AS period,
                   ROUND(SUM(st.total_inc_vat) / 1.07, 2) AS revenue,
                   COUNT(*) AS tx_count
            FROM sales_transaction st
            JOIN branches b ON b.branch_id = st.branch_id
            {where}
            GROUP BY b.name, period
            ORDER BY b.name, period
            """,
            params,
        ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# /api/analysis/anomalies   -  z-score outliers
# ---------------------------------------------------------------------------
@app.get("/api/analysis/anomalies")
@ttl_cache
def analysis_anomalies(
    month_from: Optional[str] = Query(None),
    month_to:   Optional[str] = Query(None),
    branch:     Optional[str] = Query(None),
    group:      Optional[str] = Query(None),
    threshold:  float         = Query(1.5, description="z-score threshold"),
):
    """
    Actionable month-on-month insights. Returns:
    - revenue spike / dip: latest month vs prior month (>= 5% MoM)
    - fastest-growing / biggest-dropping branches month-over-month
    - BoM cost % spike / dip month-over-month
    All comparisons are like-for-like consecutive months (house rule), never
    against an all-period average. Uses Easy transaction-level data.
    """
    insights: List[dict] = []

    # Restrict every sub-query to a branch group (master|franchise) when asked.
    # Two roll-up queries below are hard-coded WHEREs with no bound params, so we
    # also build a literal fragment; group is validated to a fixed allow-list first,
    # so the interpolation is injection-safe.
    if group not in ("master", "franchise"):
        group = None
    grp_sql = f" AND COALESCE(b.branch_type,'master') = '{group}'" if group else ""

    # 1. Monthly revenue z-score: Easy transaction-level
    filters_tx = ["b.name != 'ALL'"]
    params: dict = {}
    if branch:
        filters_tx.append("b.name = :branch")
        params["branch"] = branch
    _group_filter(filters_tx, params, group)
    _month_range_filter(filters_tx, params, month_from, month_to, None,
                        col="strftime('%Y-%m', st.sold_at)")
    where_tx = "WHERE " + " AND ".join(filters_tx)

    # Fair comparison (same logic as the latest-month hero): if the most recent
    # month is incomplete, compare month-to-date vs the SAME day-range of the
    # prior month instead of a partial-vs-full month. We do this by clamping ALL
    # months to day ≤ cutoff; only the last two are ever surfaced below.
    import calendar as _cal
    mtd_note = ""
    with get_db() as con:
        partial_months = _partial_months(con)
        # Base the MTD clamp on the latest FULLY-synced month, not a trailing sliver:
        # a complete latest month is then compared full-vs-full (real declines still
        # surface) while a partial current month is excluded from the series entirely.
        if partial_months:
            _mp = {f"_pm{i}": m for i, m in enumerate(partial_months)}
            _ph = ",".join(f":{k}" for k in _mp)
            maxday = con.execute(
                f"SELECT MAX(date(sold_at)) FROM sales_transaction WHERE source='Easy' "
                f"AND strftime('%Y-%m', sold_at) NOT IN ({_ph})", _mp).fetchone()[0]
        else:
            maxday = con.execute(
                "SELECT MAX(date(sold_at)) FROM sales_transaction WHERE source='Easy'").fetchone()[0]
        day_clause = ""
        if maxday:
            _y, _m, _d = (int(x) for x in maxday.split("-"))
            if _d < _cal.monthrange(_y, _m)[1]:   # latest month is partial
                day_clause = f" AND CAST(strftime('%d', st.sold_at) AS INT) <= {_d}"
                mtd_note = f" (เทียบ 1–{_d} วันเท่ากัน · MTD)"

        easy_monthly = con.execute(
            f"""
            SELECT strftime('%Y-%m', st.sold_at) AS month,
                   ROUND(SUM(st.total_inc_vat) / 1.07, 2) AS revenue
            FROM sales_transaction st
            JOIN branches b ON b.branch_id = st.branch_id
            {where_tx}{day_clause}
            GROUP BY month
            ORDER BY month
            """,
            params,
        ).fetchall()

        # Branch MoM growth — Easy tx
        bgrowth_easy = con.execute(
            f"""
            SELECT b.name AS branch,
                   strftime('%Y-%m', st.sold_at) AS month,
                   ROUND(SUM(st.total_inc_vat) / 1.07, 2) AS revenue
            FROM sales_transaction st
            JOIN branches b ON b.branch_id = st.branch_id
            WHERE b.name != 'ALL'{grp_sql}{day_clause}
            GROUP BY b.name, month
            ORDER BY b.name, month
            """,
        ).fetchall()

    # Monthly revenue totals (Easy)
    rev_by_month: dict = {}
    for r in easy_monthly:
        rev_by_month[r["month"]] = (rev_by_month.get(r["month"]) or 0) + (r["revenue"] or 0)
    monthly_combined = [{"month": m, "revenue": v} for m, v in sorted(rev_by_month.items())]

    # Branch growth rows (Easy) — drop not-yet-synced trailing months so a partial
    # current month cannot fake a -100% MoM for branches that have not synced yet.
    bgrowth_map: dict = {}
    for r in bgrowth_easy:
        if r["month"] in partial_months:
            continue
        bgrowth_map.setdefault(r["branch"], {})[r["month"]] = (r["revenue"] or 0)
    bgrowth = [
        {"branch": br, "month": mo, "revenue": rev}
        for br, months in bgrowth_map.items()
        for mo, rev in sorted(months.items())
    ]
    monthly = [m for m in monthly_combined if m["month"] not in partial_months]

    # Revenue MoM — use threshold param as sensitivity (fix D10)
    _thr = threshold
    if len(monthly) >= 2:
        last = monthly[-1]
        prev = monthly[-2]
        last_rev = last["revenue"] or 0
        prev_rev = prev["revenue"] or 0
        if prev_rev > 0:
            mom_pct = (last_rev - prev_rev) / prev_rev * 100
            if mom_pct >= _thr:
                insights.append({
                    "type": "spike", "severity": "good",
                    "metric": "revenue", "period": last["month"],
                    "value": last_rev, "mom_pct": round(mom_pct, 1),
                    "message": f"📈 รายได้เดือน {last['month']} โต {mom_pct:+.1f}% จากเดือน {prev['month']} — {last_rev:,.0f} (ก่อนหน้า {prev_rev:,.0f}){mtd_note}",
                })
            elif mom_pct <= -_thr:
                insights.append({
                    "type": "dip", "severity": "bad",
                    "metric": "revenue", "period": last["month"],
                    "value": last_rev, "mom_pct": round(mom_pct, 1),
                    "message": f"📉 รายได้เดือน {last['month']} ลด {mom_pct:.1f}% จากเดือน {prev['month']} — {last_rev:,.0f} (ก่อนหน้า {prev_rev:,.0f}){mtd_note}",
                })

    # Branch MoM growth
    by_branch: dict = {}
    for r in bgrowth:
        by_branch.setdefault(r["branch"], []).append(r)
    growths = []
    for name, recs in by_branch.items():
        if len(recs) >= 2:
            last = recs[-1]["revenue"] or 0
            prev = recs[-2]["revenue"] or 0
            if prev > 0:
                growths.append({
                    "branch": name, "growth_pct": round((last - prev) / prev * 100, 1),
                    "last_revenue": last, "prev_revenue": prev,
                    "last_month": recs[-1]["month"], "prev_month": recs[-2]["month"],
                })
    growths.sort(key=lambda x: -x["growth_pct"])
    if growths:
        top = growths[0]
        if top["growth_pct"] > _thr:
            insights.append({
                "type": "growth", "severity": "good", "metric": "branch_mom",
                "branch": top["branch"], "value": top["growth_pct"],
                "message": f"🚀 เติบโตเร็วสุด: {top['branch']} +{top['growth_pct']:.1f}% MoM ({top['last_revenue']:,.0f}){mtd_note}",
            })
        worst = growths[-1]
        if worst["growth_pct"] < -_thr:
            insights.append({
                "type": "drop", "severity": "bad", "metric": "branch_mom",
                "branch": worst["branch"], "value": worst["growth_pct"],
                "message": f"⚠️ ลดมากสุด: {worst['branch']} {worst['growth_pct']:.1f}% MoM ({worst['last_revenue']:,.0f}){mtd_note}",
            })

    # BoM cost % spike / dip detection (MoM delta >= 5 pp)
    # Phase 18d: migrated to _cogs_case helper so monthly cost overrides + tier
    # exclusion (incl. plain BUFFET) are unified with the rest of the app.
    try:
        with get_db() as con2:
            cost_monthly_rows = con2.execute(f"""
                SELECT s.period_month AS month,
                       ROUND(SUM({_cogs_case('s','sk','c')}), 2) AS bom_cost,
                       ROUND(SUM(s.subtotal_ex_vat), 2)          AS revenue
                FROM sales_sku_monthly s
                JOIN skus sk     ON sk.sku_id = s.sku_id
                LEFT JOIN sku_cost c ON c.sku_id = s.sku_id
                JOIN branches b  ON b.branch_id = s.branch_id
                WHERE b.name != 'ALL'{grp_sql}
                GROUP BY s.period_month
                ORDER BY s.period_month
            """).fetchall()
        cost_series = [
            (r["month"], r["bom_cost"], r["revenue"])
            for r in cost_monthly_rows
            if r["bom_cost"] and r["revenue"] and r["revenue"] > 0
            and r["month"] not in partial_months
        ]
        for i in range(1, len(cost_series)):
            m, bc, rev   = cost_series[i]
            _, pbc, prev = cost_series[i - 1]
            curr_pct = bc  / rev  * 100
            prev_pct = pbc / prev * 100
            delta = curr_pct - prev_pct
            if delta >= _thr:
                insights.append({
                    "type": "cost_spike", "severity": "bad",
                    "metric": "bom_cost_pct", "period": m,
                    "value": round(curr_pct, 1),
                    "message": f"⚠️ ต้นทุน BoM พุ่งในเดือน {m} — {curr_pct:.1f}% of revenue (+{delta:.1f}pp จากเดือนก่อน)",
                })
            elif delta <= -_thr:
                insights.append({
                    "type": "cost_drop", "severity": "good",
                    "metric": "bom_cost_pct", "period": m,
                    "value": round(curr_pct, 1),
                    "message": f"✅ ต้นทุน BoM ลดในเดือน {m} — {curr_pct:.1f}% of revenue ({delta:.1f}pp จากเดือนก่อน)",
                })
    except Exception:
        pass

    return {"insights": insights, "branch_growth": growths[:10]}


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
# /api/analysis/health   -  composite Health Score per branch
# ---------------------------------------------------------------------------
@app.get("/api/analysis/health")
def analysis_health(
    month_from: Optional[str] = Query(None),
    month_to:   Optional[str] = Query(None),
    group:      Optional[str] = Query(None),
):
    """
    Composite health score per branch (0-100):
      = 0.4 * revenue_score    (normalized vs leader)
      + 0.3 * growth_score     (last vs prior month, capped ±50%)
      + 0.2 * consistency_score (1 - coefficient of variation)
      + 0.1 * momentum_score   (slope of last 3 periods)
    Uses Easy transaction-level data (sales_transaction).
    """
    filters_tx = ["b.name != 'ALL'"]
    params_tx: dict = {}
    _group_filter(filters_tx, params_tx, group)
    # Health is a TRAILING metric (MoM growth, consistency, momentum all need a
    # multi-month series). Ignore month_from so a single-month / "This Month"
    # filter doesn't collapse the series to length 1 (which made MoM read 0.0%
    # and zeroed the score). We still honour month_to as an "as-of" cutoff.
    _month_range_filter(filters_tx, params_tx, None, month_to, None,
                        col="strftime('%Y-%m', st.sold_at)")
    where_tx = "WHERE " + " AND ".join(filters_tx)

    with get_db() as con:
        easy_rows = con.execute(
            f"""
            SELECT b.name AS branch,
                   strftime('%Y-%m', st.sold_at) AS month,
                   ROUND(SUM(st.total_inc_vat) / 1.07, 2) AS revenue
            FROM sales_transaction st
            JOIN branches b ON b.branch_id = st.branch_id
            {where_tx}
            GROUP BY b.name, month
            ORDER BY b.name, month
            """,
            params_tx,
        ).fetchall()

    branch_month_h: dict = {}
    for r in easy_rows:
        branch_month_h.setdefault(r["branch"], {})[r["month"]] = \
            branch_month_h.get(r["branch"], {}).get(r["month"], 0) + (r["revenue"] or 0)

    by_branch: dict = {
        br: [months[m] for m in sorted(months)]
        for br, months in branch_month_h.items()
    }

    if not by_branch:
        return []

    # Score the LATEST MONTH's standing (monthly), not the cumulative total —
    # otherwise a long-open branch wins on accumulation, not current performance.
    max_last = max((v[-1] if v else 0) for v in by_branch.values()) or 1

    out = []
    for branch, revs in by_branch.items():
        total = sum(revs)
        last_rev = revs[-1] if revs else 0
        revenue_score = min(100, last_rev / max_last * 100)

        if len(revs) >= 2 and revs[-2] > 0:
            growth_pct = (revs[-1] - revs[-2]) / revs[-2] * 100
            growth_score = max(0, min(100, 50 + growth_pct))
        else:
            growth_score = 50.0
            growth_pct = 0.0

        if len(revs) >= 2:
            mean = sum(revs) / len(revs)
            var  = sum((v - mean) ** 2 for v in revs) / len(revs)
            sd   = var ** 0.5
            cv   = sd / mean if mean else 1
            consistency_score = max(0, min(100, (1 - cv) * 100))
        else:
            consistency_score = 50.0

        if len(revs) >= 3:
            last3 = revs[-3:]
            slope = (last3[-1] - last3[0]) / (last3[0] or 1) * 100
            momentum_score = max(0, min(100, 50 + slope))
        else:
            momentum_score = 50.0

        composite = (
            0.4 * revenue_score +
            0.3 * growth_score  +
            0.2 * consistency_score +
            0.1 * momentum_score
        )
        out.append({
            "branch": branch,
            "score": round(composite, 1),
            "revenue_score":     round(revenue_score, 1),
            "growth_score":      round(growth_score, 1),
            "consistency_score": round(consistency_score, 1),
            "momentum_score":    round(momentum_score, 1),
            "total_revenue":     round(total, 2),
            "last_month_revenue": revs[-1] if revs else 0,
            "growth_pct":        round(growth_pct, 1),
        })
    out.sort(key=lambda x: -x["score"])
    return out


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


# ===========================================================================
# ADVANCED ANALYTICS  -  yoy, forecast, cohort, affinity, percentiles, ai
# ===========================================================================

@app.get("/api/analysis/yoy")
@ttl_cache
def analysis_yoy(
    month_from: Optional[str] = Query(None),
    month_to:   Optional[str] = Query(None),
    branch:     Optional[str] = Query(None),
    group:      Optional[str] = Query(None),
):
    """For each month in range, return current revenue + same month last year."""
    if not month_from and not month_to:
        return []
    filters = ["b.name != 'ALL'"]
    params: dict = {}
    if branch:
        filters.append("b.name = :branch")
        params["branch"] = branch
    _group_filter(filters, params, group)
    where_branch = " AND " + " AND ".join(filters[1:]) if len(filters) > 1 else ""

    with get_db() as con:
        # Pull all months in range
        rng = con.execute(
            f"""
            SELECT sm.period_month, ROUND(SUM(sm.subtotal_ex_vat),2) AS revenue
            FROM sales_sku_monthly sm
            JOIN branches b ON b.branch_id = sm.branch_id
            WHERE b.name != 'ALL'
              AND sm.period_month BETWEEN :mf AND :mt
              {where_branch}
            GROUP BY sm.period_month
            ORDER BY sm.period_month
            """,
            {**params, "mf": month_from or "0000-00", "mt": month_to or "9999-99"},
        ).fetchall()

        # fix D11: batch prior-year revenue into one query instead of N+1
        cur_months = [r["period_month"] for r in rng]
        ly_months_unique = list({f"{int(m.split('-')[0])-1}-{m.split('-')[1]}" for m in cur_months})
        if ly_months_unique:
            ly_ph = ",".join(f":ly{i}" for i in range(len(ly_months_unique)))
            ly_params = {**params, **{f"ly{i}": m for i, m in enumerate(ly_months_unique)}}
            ly_rows = con.execute(
                f"""SELECT sm.period_month, ROUND(SUM(sm.subtotal_ex_vat),2) AS revenue
                    FROM sales_sku_monthly sm
                    JOIN branches b ON b.branch_id = sm.branch_id
                    WHERE b.name != 'ALL' AND sm.period_month IN ({ly_ph}) {where_branch}
                    GROUP BY sm.period_month""",
                ly_params,
            ).fetchall()
            ly_rev_map = {r["period_month"]: (r["revenue"] or 0) for r in ly_rows}
        else:
            ly_rev_map = {}

        out = []
        for r in rng:
            cur_mo = r["period_month"]
            yr, mo = cur_mo.split("-")
            ly_mo  = f"{int(yr)-1}-{mo}"
            ly_rev = ly_rev_map.get(ly_mo, 0)
            yoy_pct = ((r["revenue"] - ly_rev) / ly_rev * 100) if ly_rev > 0 else None
            out.append({
                "period_month": cur_mo,
                "revenue":      r["revenue"],
                "ly_period":    ly_mo,
                "ly_revenue":   ly_rev,
                "yoy_pct":      round(yoy_pct, 1) if yoy_pct is not None else None,
            })
    return out


@ttl_cache
def analysis_forecast(
    branch: Optional[str] = Query(None),
    group:  Optional[str] = Query(None),
):
    """
    Projects revenue for the current month using daily run-rate from transactions
    in the latest month. Returns mtd, days_elapsed, days_total, projected, confidence_lo, confidence_hi.
    """
    filters = ["b.name != 'ALL'", "st.source = 'Easy'"]
    params: dict = {}
    if branch:
        filters.append("b.name = :branch")
        params["branch"] = branch
    _group_filter(filters, params, group)
    where = "WHERE " + " AND ".join(filters)
    with get_db() as con:
        latest = con.execute(
            f"""
            SELECT MAX(DATE(st.sold_at)) AS d
            FROM sales_transaction st JOIN branches b ON b.branch_id = st.branch_id {where}
            """, params
        ).fetchone()
        if not latest or not latest["d"]:
            return {"available": False}
        from datetime import datetime as _dt, timedelta as _td
        from calendar import monthrange
        d = _dt.strptime(latest["d"], "%Y-%m-%d")
        period = d.strftime("%Y-%m")
        if period in _partial_months(con):
            # current month not fully synced (few branches) -> run-rate is meaningless
            return {"available": False, "reason": "current month not fully synced yet"}
        params["period"] = period
        filters_p = filters + ["strftime('%Y-%m', st.sold_at) = :period"]
        where_p = "WHERE " + " AND ".join(filters_p)
        daily = con.execute(
            f"""
            SELECT DATE(st.sold_at) AS day, SUM(st.total_inc_vat) / 1.07 AS rev
            FROM sales_transaction st JOIN branches b ON b.branch_id = st.branch_id {where_p}
            GROUP BY day ORDER BY day
            """, params
        ).fetchall()
    if not daily:
        return {"available": False}
    days_in_month = monthrange(d.year, d.month)[1]
    days_elapsed = d.day
    revs = [r["rev"] or 0 for r in daily]
    mtd = sum(revs)
    avg_daily = mtd / max(1, days_elapsed)
    projected = avg_daily * days_in_month
    # Confidence band Â±1 SD of daily revenue projected over remaining days
    if len(revs) >= 2:
        mean = sum(revs)/len(revs)
        sd = (sum((v-mean)**2 for v in revs)/len(revs)) ** 0.5
        remain = days_in_month - days_elapsed
        band = sd * (remain ** 0.5)
    else:
        band = 0
    return {
        "available":  True,
        "period":     period,
        "days_elapsed": days_elapsed,
        "days_total":   days_in_month,
        "mtd":          round(mtd, 2),
        "avg_daily":    round(avg_daily, 2),
        "projected":    round(projected, 2),
        "lo":           round(projected - band, 2),
        "hi":           round(projected + band, 2),
        "daily":        [{"day": r["day"], "revenue": r["rev"] or 0} for r in daily],
    }


@ttl_cache
def analysis_forecast_branches(group: Optional[str] = Query(None)):
    """Per-branch month-end revenue projection (run-rate) for the latest month,
    plus the prior month's actual for trend.

    Methodology: projected = MTD / days_elapsed * days_in_month, where days_elapsed
    is the calendar day-of-month of the latest sale date (closed days count as zero
    revenue, the honest run-rate). Every branch shares the same days_elapsed divisor,
    so the per-branch projections sum exactly to the total. Revenue is ex-VAT
    (inc-VAT / 1.07). mom_pct compares the projected month-end vs the prior full month.
    """
    filters = ["b.name != 'ALL'", "st.source = 'Easy'"]
    params: dict = {}
    _group_filter(filters, params, group)
    where = "WHERE " + " AND ".join(filters)
    with get_db() as con:
        latest = con.execute(
            f"""SELECT MAX(DATE(st.sold_at)) AS d
                FROM sales_transaction st JOIN branches b ON b.branch_id = st.branch_id {where}""",
            params,
        ).fetchone()
        if not latest or not latest["d"]:
            return {"available": False}
        from datetime import datetime as _dt
        from calendar import monthrange
        d = _dt.strptime(latest["d"], "%Y-%m-%d")
        period = d.strftime("%Y-%m")
        days_in_month = monthrange(d.year, d.month)[1]
        days_elapsed = d.day
        py, pm = (d.year - 1, 12) if d.month == 1 else (d.year, d.month - 1)
        prior = f"{py:04d}-{pm:02d}"

        params_cur = dict(params); params_cur["period"] = period
        cur = con.execute(
            f"""SELECT b.name AS branch, SUM(st.total_inc_vat) / 1.07 AS mtd
                FROM sales_transaction st JOIN branches b ON b.branch_id = st.branch_id
                {where} AND strftime('%Y-%m', st.sold_at) = :period
                GROUP BY b.name""", params_cur,
        ).fetchall()
        params_pri = dict(params); params_pri["period"] = prior
        pri = con.execute(
            f"""SELECT b.name AS branch, SUM(st.total_inc_vat) / 1.07 AS rev
                FROM sales_transaction st JOIN branches b ON b.branch_id = st.branch_id
                {where} AND strftime('%Y-%m', st.sold_at) = :period
                GROUP BY b.name""", params_pri,
        ).fetchall()
        # All real branches + their last sale date — to flag inactive ones (no sales
        # in the forecast month) so the 26-vs-27 gap is explained, not silently dropped.
        # fix D13: move st.source='Easy' to JOIN ON so inactive branches aren't dropped
        allbr_branch_filters = [f for f in filters if "st.source" not in f]
        allbr_where = ("WHERE " + " AND ".join(allbr_branch_filters)) if allbr_branch_filters else ""
        allbr = con.execute(
            f"""SELECT b.name AS branch, MAX(DATE(st.sold_at)) AS last_sale
                FROM branches b LEFT JOIN sales_transaction st
                    ON st.branch_id = b.branch_id AND st.source = 'Easy'
                {allbr_where} GROUP BY b.name""", params,
        ).fetchall()

    active_names = {r["branch"] for r in cur}
    inactive = [{"branch": r["branch"], "last_sale": r["last_sale"]}
                for r in allbr if r["branch"] not in active_names]
    prior_map = {r["branch"]: (r["rev"] or 0) for r in pri}
    factor = days_in_month / max(1, days_elapsed)
    out = []
    for r in cur:
        mtd = r["mtd"] or 0
        projected = mtd * factor
        prior_rev = prior_map.get(r["branch"])
        mom_pct = round((projected - prior_rev) / prior_rev * 100, 1) if prior_rev else None
        out.append({
            "branch":       r["branch"],
            "mtd":          round(mtd, 2),
            "projected":    round(projected, 2),
            "prior_actual": round(prior_rev, 2) if prior_rev is not None else None,
            "mom_pct":      mom_pct,
        })
    out.sort(key=lambda x: -x["projected"])
    total_mtd   = sum(x["mtd"] for x in out)
    total_proj  = sum(x["projected"] for x in out)
    total_prior = sum(prior_map.values())
    return {
        "available":    True,
        "period":       period,
        "prior_period": prior,
        "days_elapsed": days_elapsed,
        "days_total":   days_in_month,
        "branches":     out,
        "total_branches":    len(allbr),
        "active_branches":   len(out),
        "inactive_branches": inactive,
        "total": {
            "mtd":          round(total_mtd, 2),
            "projected":    round(total_proj, 2),
            "prior_actual": round(total_prior, 2) if total_prior else None,
            "mom_pct":      round((total_proj - total_prior) / total_prior * 100, 1) if total_prior else None,
        },
    }


@app.get("/api/analysis/cohort")
@ttl_cache
def analysis_cohort(
    branch: Optional[str] = Query(None),
    group:  Optional[str] = Query(None),
):
    """
    Approximate cohort retention: groups receipts by cashier+date as a proxy customer
    if no customer ID. Since restaurants typically lack stable customer IDs, this falls
    back to grouping receipts per month and showing same-period repeat-visit proxy
    (table_no + cashier as a weak identifier).
    Returns a {cohort_month: {0:100, 1:retention%, ...}} matrix.
    """
    filters = ["b.name != 'ALL'"]
    params: dict = {}
    if branch:
        filters.append("b.name = :branch")
        params["branch"] = branch
    _group_filter(filters, params, group)
    where = "WHERE " + " AND ".join(filters)
    with get_db() as con:
        rows = con.execute(
            f"""
            SELECT strftime('%Y-%m', st.sold_at) AS month,
                   COUNT(DISTINCT st.receipt_no) AS tx_count,
                   SUM(st.customer_count)        AS customer_count,
                   ROUND(SUM(st.total_inc_vat) / 1.07,2) AS revenue
            FROM sales_transaction st
            JOIN branches b ON b.branch_id = st.branch_id
            {where}
            GROUP BY month
            ORDER BY month
            """, params,
        ).fetchall()
    # Without unique customer IDs, render a returning-customer proxy as customer_count growth rate vs avg
    months = [r["month"] for r in rows]
    if not months:
        return {"cohorts": [], "matrix": []}
    avgCust = sum((r["customer_count"] or 0) for r in rows) / len(rows) or 1
    matrix = []
    for r in rows:
        # Synthetic retention curve: index against avg customers
        base = (r["customer_count"] or 0)
        ratio = base / avgCust * 100 if avgCust else 0
        matrix.append({
            "cohort":  r["month"],
            "tx":      r["tx_count"],
            "customers": r["customer_count"] or 0,
            "revenue": r["revenue"] or 0,
            "vs_avg":  round(ratio, 1),
        })
    return {
        "note": "Restaurant POS data lacks unique customer IDs; this view shows monthly customer volume vs the period average - useful as a soft retention signal.",
        "cohorts": months,
        "matrix":  matrix,
    }


@app.get("/api/analysis/affinity")
@ttl_cache
def analysis_affinity(
    sku_code: Optional[str] = Query(None, description="Anchor SKU  -  find what's bought with it"),
    month_from: Optional[str] = Query(None),
    month_to:   Optional[str] = Query(None),
    branch:     Optional[str] = Query(None),
    limit:      int           = Query(10, ge=1, le=50),
):
    """
    Item affinity via receipt co-occurrence.
    NOTE: sales_transaction has totals only, not line items per receipt.
    Use sales_sku_monthly grouped at (branch, month) as a proxy basket.
    Returns SKUs most likely to appear in the same period+branch as the anchor.
    """
    if not sku_code:
        return {"items": [], "note": "Pass sku_code to anchor"}
    filters = ["b.name != 'ALL'"]
    params: dict = {"anchor": sku_code}
    if branch:
        filters.append("b.name = :branch"); params["branch"] = branch
    _month_range_filter(filters, params, month_from, month_to, None, col="sm.period_month")
    where = "WHERE " + " AND ".join(filters)
    with get_db() as con:
        # Find baskets (branch+month) where anchor sku appeared
        anchor_baskets = con.execute(
            f"""
            SELECT DISTINCT sm.branch_id || '|' || sm.period_month AS basket
            FROM sales_sku_monthly sm
            JOIN branches b ON b.branch_id = sm.branch_id
            JOIN skus sk    ON sk.sku_id    = sm.sku_id
            {where} AND sk.sku_code = :anchor
            """, params,
        ).fetchall()
        baskets = {r["basket"] for r in anchor_baskets}
        if not baskets:
            return {"items": [], "anchor": sku_code, "baskets": 0}

        # Find co-occurring SKUs across those baskets
        rows = con.execute(
            f"""
            SELECT sk.sku_code, COALESCE(sk.description, sk.sku_code) AS description,
                   COUNT(DISTINCT sm.branch_id || '|' || sm.period_month) AS co_count,
                   ROUND(SUM(sm.subtotal_ex_vat), 2) AS co_revenue,
                   ROUND(SUM(sm.qty), 0) AS co_qty
            FROM sales_sku_monthly sm
            JOIN branches b ON b.branch_id = sm.branch_id
            JOIN skus sk    ON sk.sku_id    = sm.sku_id
            {where} AND sk.sku_code != :anchor
              AND sm.branch_id || '|' || sm.period_month IN ({','.join('?' for _ in baskets)})
            GROUP BY sk.sku_id
            ORDER BY co_count DESC, co_revenue DESC
            LIMIT :limit
            """, {**params, "limit": limit, **{f"_b{i}": v for i, v in enumerate(baskets)}},
        ).fetchall() if False else None

        # The IN clause with named params + dict isn't well-supported in sqlite3  -  use a tuple-based query instead
        basket_list = list(baskets)
        placeholders = ",".join("?" * len(basket_list))
        # Rebuild with positional params
        sql = f"""
            SELECT sk.sku_code, COALESCE(sk.description, sk.sku_code) AS description,
                   COUNT(DISTINCT sm.branch_id || '|' || sm.period_month) AS co_count,
                   ROUND(SUM(sm.subtotal_ex_vat), 2) AS co_revenue,
                   ROUND(SUM(sm.qty), 0) AS co_qty
            FROM sales_sku_monthly sm
            JOIN branches b ON b.branch_id = sm.branch_id
            JOIN skus sk    ON sk.sku_id    = sm.sku_id
            WHERE b.name != 'ALL' AND sk.sku_code != ?
              AND sm.branch_id || '|' || sm.period_month IN ({placeholders})
            GROUP BY sk.sku_id
            ORDER BY co_count DESC, co_revenue DESC
            LIMIT ?
        """
        rows = con.execute(sql, [sku_code, *basket_list, limit]).fetchall()
    n_baskets = len(baskets)
    return {
        "anchor":   sku_code,
        "baskets":  n_baskets,
        "items":    [{**dict(r), "lift": round((r["co_count"]/n_baskets)*100, 1)} for r in rows],
    }


@app.get("/api/analysis/percentiles")
@ttl_cache
def analysis_percentiles(
    month_from: Optional[str] = Query(None),
    month_to:   Optional[str] = Query(None),
):
    """For each branch, compute percentile rank in revenue, avg ticket, customers."""
    filters = ["b.name != 'ALL'"]
    params: dict = {}
    _month_range_filter(filters, params, month_from, month_to, None, col="strftime('%Y-%m', st.sold_at)")
    where = "WHERE " + " AND ".join(filters)
    with get_db() as con:
        rows = con.execute(
            f"""
            SELECT b.name AS branch,
                   COUNT(*)                                AS tx,
                   SUM(st.customer_count)                  AS cust,
                   ROUND(SUM(st.total_inc_vat) / 1.07, 2)        AS revenue,
                   ROUND(AVG(st.total_inc_vat), 2)        AS avg_ticket
            FROM sales_transaction st
            JOIN branches b ON b.branch_id = st.branch_id
            {where}
            GROUP BY b.name
            """, params,
        ).fetchall()
    arr = [dict(r) for r in rows]
    def pctile(values, v):
        if not values: return 0
        ordered = sorted(values)
        rank = sum(1 for x in ordered if x <= v)
        return round(rank / len(ordered) * 100)
    revs   = [r["revenue"]   or 0 for r in arr]
    custs  = [r["cust"]      or 0 for r in arr]
    avgs   = [r["avg_ticket"] or 0 for r in arr]
    for r in arr:
        r["p_revenue"]    = pctile(revs,  r["revenue"]   or 0)
        r["p_customers"]  = pctile(custs, r["cust"]      or 0)
        r["p_avg_ticket"] = pctile(avgs,  r["avg_ticket"] or 0)
    arr.sort(key=lambda x: -(x["revenue"] or 0))
    return arr


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


@app.get("/api/buffet/performance")
@ttl_cache
def buffet_performance(
    branch:     Optional[str] = Query(None),
    group:      Optional[str] = Query(None),
    month_from: Optional[str] = Query(None),
    month_to:   Optional[str] = Query(None),
    food_cost_target_lo: float = Query(55.0),
    food_cost_target_hi: float = Query(70.0),
):
    """Comprehensive Buffet Performance metrics for the core all-you-can-eat business.

    Returns:
    - kpis: pax, bills, avg_pax_per_bill, premium_tier_pct, food_cost_pct, over_consumption_pct, beverage_attach_pct
    - tier_trend: per-month tier mix (BUFFET350/500/590/799 + CHILD)
    - food_cost_trend: monthly food_cost % vs target band
    - seat_util: per-branch daily pax vs network peak (proxy)
    - turnover:   per-branch bills/table/day (turnover proxy)
    - waste:      per-month over-consumption rate from OT0012/0013/0016
    - beverage_attach: per-month beverage revenue % of buffet bills
    - daypart_tier: lunch vs dinner tier mix
    - cohort:     per-branch performance bucketed by months-since-opening
    - pricing_rec: current weighted tier price vs revenue-optimal
    """
    # Optional master|franchise scope. grp_sql = literal fragment for the two
    # roll-up queries below that use no bound params (group is allow-listed first).
    if group not in ("master", "franchise"):
        group = None
    grp_sql = f" AND COALESCE(b.branch_type,'master') = '{group}'" if group else ""

    filters_sku = ["b.name != 'ALL'"]
    params_sku: dict = {}
    if branch:
        filters_sku.append("b.name = :branch")
        params_sku["branch"] = branch
    _group_filter(filters_sku, params_sku, group)
    _month_range_filter(filters_sku, params_sku, month_from, month_to, None, col="sm.period_month")
    where_sku = "WHERE " + " AND ".join(filters_sku)

    filters_tx = ["st.source='Easy'", "b.name != 'ALL'"]
    params_tx: dict = {}
    if branch:
        filters_tx.append("b.name = :branch")
        params_tx["branch"] = branch
    _group_filter(filters_tx, params_tx, group)
    _month_range_filter(filters_tx, params_tx, month_from, month_to, None, col="strftime('%Y-%m', st.sold_at)")
    where_tx = "WHERE " + " AND ".join(filters_tx)

    BUFFET_TIERS = ('BUFFET350', 'BUFFET500', 'BUFFET590', 'BUFFET799', 'BUFFETCHILD')
    WASTE_CODES  = ('OT0012', 'OT0013', 'OT0014', 'OT0015', 'OT0016')

    # Build named-param dicts for IN clauses so we can mix safely with named month params
    tier_named  = {f"bt{i}": v for i, v in enumerate(BUFFET_TIERS)}
    tier_ph     = ", ".join(f":bt{i}" for i in range(len(BUFFET_TIERS)))
    waste_named = {f"wc{i}": v for i, v in enumerate(WASTE_CODES)}
    waste_ph    = ", ".join(f":wc{i}" for i in range(len(WASTE_CODES)))

    with get_db() as con:
        # ── 1. Tier mix trend (monthly pax + revenue per tier) ────────────────
        tier_rows = con.execute(f"""
            SELECT sm.period_month AS m, sk.sku_code AS tier,
                   SUM(sm.qty)             AS pax,
                   SUM(sm.total_inc_vat)   AS rev_inc,
                   SUM(sm.subtotal_ex_vat) AS rev_ex
            FROM sales_sku_monthly sm
            JOIN skus sk      ON sk.sku_id    = sm.sku_id
            JOIN branches b   ON b.branch_id  = sm.branch_id
            {where_sku}
              AND sk.sku_code IN ({tier_ph})
            GROUP BY sm.period_month, sk.sku_code
            ORDER BY sm.period_month, sk.sku_code
        """, {**params_sku, **tier_named}).fetchall()

        # ── 2. Tier unit costs (for cost%) ────────────────────────────────────
        tier_cost = {}
        for r in con.execute(f"""
            SELECT sk.sku_code, sc.unit_cost
            FROM sku_cost sc JOIN skus sk ON sk.sku_id = sc.sku_id
            WHERE sk.sku_code IN ({tier_ph})
        """, tier_named).fetchall():
            tier_cost[r["sku_code"]] = r["unit_cost"]

        # Monthly tier-cost overrides (Phase 8): {(period_month, sku_code): unit_cost}
        tier_cost_monthly: dict = {}
        for r in con.execute(f"""
            SELECT scm.period_month AS m, sk.sku_code AS code, scm.unit_cost AS unit_cost
            FROM sku_cost_monthly scm JOIN skus sk ON sk.sku_id = scm.sku_id
            WHERE sk.sku_code IN ({tier_ph})
        """, tier_named).fetchall():
            tier_cost_monthly[(r["m"], r["code"])] = r["unit_cost"]

        # ── 3. Per-branch daily pax (for seat utilisation proxy) ──────────────
        daily_pax_rows = con.execute(f"""
            SELECT b.name AS branch,
                   DATE(st.sold_at) AS d,
                   SUM(st.customer_count) AS pax,
                   COUNT(*) AS bills,
                   COUNT(DISTINCT st.table_no) AS tables
            FROM sales_transaction st
            JOIN branches b ON b.branch_id = st.branch_id
            {where_tx}
              AND st.customer_count > 0
            GROUP BY b.name, DATE(st.sold_at)
        """, params_tx).fetchall()

        # ── 4. Waste / over-consumption (OT codes — kg fines for buffet leftovers) ────
        waste_rows = con.execute(f"""
            SELECT sm.period_month AS m,
                   SUM(sm.qty) AS qty,
                   SUM(sm.subtotal_ex_vat) AS rev
            FROM sales_sku_monthly sm
            JOIN skus sk ON sk.sku_id = sm.sku_id
            JOIN branches b ON b.branch_id = sm.branch_id
            {where_sku}
              AND sk.sku_code IN ({waste_ph})
            GROUP BY sm.period_month
        """, {**params_sku, **waste_named}).fetchall()

        # Buffet pax per month (for waste rate denominator)
        buffet_pax_rows = con.execute(f"""
            SELECT sm.period_month AS m, SUM(sm.qty) AS pax
            FROM sales_sku_monthly sm
            JOIN skus sk ON sk.sku_id = sm.sku_id
            JOIN branches b ON b.branch_id = sm.branch_id
            {where_sku}
              AND sk.sku_code IN ({tier_ph})
            GROUP BY sm.period_month
        """, {**params_sku, **tier_named}).fetchall()
        pax_by_month = {r["m"]: r["pax"] or 0 for r in buffet_pax_rows}

        # ── 5. Beverage attach rate (from drink_subtotal / total) ─────────────
        bev_rows = con.execute(f"""
            SELECT strftime('%Y-%m', st.sold_at) AS m,
                   SUM(st.drink_subtotal) AS drink_rev,
                   SUM(st.food_subtotal)  AS food_rev,
                   SUM(st.total_inc_vat) / 1.07 AS total_rev,  -- ex-VAT, consistent with drink/food subtotals
                   COUNT(*)               AS bills,
                   SUM(CASE WHEN st.drink_subtotal > 0 THEN 1 ELSE 0 END) AS bills_with_drink
            FROM sales_transaction st
            JOIN branches b ON b.branch_id = st.branch_id
            {where_tx}
            GROUP BY strftime('%Y-%m', st.sold_at)
            ORDER BY m
        """, params_tx).fetchall()

        # ── 6. Daypart tier mix (lunch vs dinner) ─────────────────────────────
        daypart_rows = con.execute(f"""
            SELECT CASE
                     WHEN CAST(strftime('%H', st.sold_at) AS INT) BETWEEN 11 AND 14 THEN 'lunch'
                     WHEN CAST(strftime('%H', st.sold_at) AS INT) BETWEEN 17 AND 22 THEN 'dinner'
                     ELSE 'other' END AS daypart,
                   CASE
                     WHEN (st.food_subtotal/st.customer_count) < 450 THEN 'BUFFET350'
                     WHEN (st.food_subtotal/st.customer_count) < 600 THEN 'BUFFET500'
                     WHEN (st.food_subtotal/st.customer_count) < 720 THEN 'BUFFET590'
                     ELSE 'BUFFET799' END AS tier,
                   SUM(st.customer_count) AS pax
            FROM sales_transaction st
            JOIN branches b ON b.branch_id = st.branch_id
            {where_tx}
              AND st.customer_count > 0 AND st.food_subtotal > 0
              AND (st.food_subtotal / st.customer_count) BETWEEN 380 AND 1000
            GROUP BY daypart, tier
        """, params_tx).fetchall()

        # ── 7. Branch opening month (cohort: months-since-open) ───────────────
        branch_open = con.execute(f"""
            SELECT b.name AS branch, MIN(sm.period_month) AS first_month
            FROM sales_sku_monthly sm
            JOIN branches b ON b.branch_id = sm.branch_id
            WHERE b.name != 'ALL'{grp_sql}
            GROUP BY b.name
        """).fetchall()
        branch_first = {r["branch"]: r["first_month"] for r in branch_open}

        # Per-branch monthly buffet revenue (for cohort curve)
        bcohort_rows = con.execute(f"""
            SELECT b.name AS branch, sm.period_month AS m,
                   SUM(sm.subtotal_ex_vat) AS rev,
                   SUM(sm.qty) AS pax
            FROM sales_sku_monthly sm
            JOIN skus sk ON sk.sku_id = sm.sku_id
            JOIN branches b ON b.branch_id = sm.branch_id
            WHERE b.name != 'ALL'{grp_sql}
              AND sk.sku_code IN ({tier_ph})
            GROUP BY b.name, sm.period_month
            ORDER BY b.name, sm.period_month
        """, tier_named).fetchall()

        # ── Real buffet food-cost trend (2026-06-02 fix) ──────────────────────
        # The old food_cost_trend multiplied tier pax × tier unit_cost, but the
        # per-tier rows (BUFFET350/500/…) carry no data — all adult buffet is on
        # code 'BUFFET', child on 'BUFFETCHILD', and the actual food consumed is
        # logged on the item-level rows (business_type='buffet'). So buffet food
        # cost = unified COGS of buffet items, revenue = the buffet_tier rows.
        bf_cost_rows = con.execute(f"""
            SELECT sm.period_month AS m,
                   SUM(CASE WHEN sk.business_type = 'buffet_tier'
                            THEN sm.subtotal_ex_vat ELSE 0 END)  AS rev_ex,
                   SUM({_unified_cogs_expr('sm', 'sk')})         AS cogs
            FROM sales_sku_monthly sm
            JOIN skus     sk ON sk.sku_id   = sm.sku_id
            JOIN branches b  ON b.branch_id = sm.branch_id
            {where_sku}
              AND sk.business_type IN ('buffet', 'buffet_tier')
            GROUP BY sm.period_month
            ORDER BY sm.period_month
        """, params_sku).fetchall()

    bf_cost_by_month = {r["m"]: (float(r["rev_ex"] or 0), float(r["cogs"] or 0))
                        for r in bf_cost_rows}

    # ── Adult vs Child split (real codes: BUFFET = adult, BUFFETCHILD = child) ─
    with get_db() as con:
        ac_rows = con.execute(f"""
            SELECT sk.sku_code AS code, SUM(sm.qty) AS qty,
                   SUM(sm.subtotal_ex_vat) AS rev
            FROM sales_sku_monthly sm
            JOIN skus sk     ON sk.sku_id   = sm.sku_id
            JOIN branches b  ON b.branch_id = sm.branch_id
            {where_sku}
              AND sk.sku_code IN ('BUFFET', 'BUFFETCHILD')
            GROUP BY sk.sku_code
        """, params_sku).fetchall()
    adult_pax = next((float(r["qty"] or 0) for r in ac_rows if r["code"] == 'BUFFET'), 0.0)
    child_pax = next((float(r["qty"] or 0) for r in ac_rows if r["code"] == 'BUFFETCHILD'), 0.0)
    adult_rev = next((float(r["rev"] or 0) for r in ac_rows if r["code"] == 'BUFFET'), 0.0)
    child_rev = next((float(r["rev"] or 0) for r in ac_rows if r["code"] == 'BUFFETCHILD'), 0.0)
    buffet_billed_pax = adult_pax + child_pax  # buffet covers sold (tier rows)
    child_pct = (child_pax / buffet_billed_pax * 100) if buffet_billed_pax else 0

    # ── Aggregate tier_trend (pax + cost%) ──────────────────────────────────
    from collections import defaultdict
    tier_trend = defaultdict(lambda: defaultdict(float))
    tier_rev   = defaultdict(lambda: defaultdict(float))
    for r in tier_rows:
        tier_trend[r["m"]][r["tier"]] = r["pax"] or 0
        tier_rev  [r["m"]][r["tier"]] = r["rev_ex"] or 0
    months = sorted(tier_trend.keys())

    # Tier list for chart
    tier_list = ['BUFFET350', 'BUFFET500', 'BUFFET590', 'BUFFET799', 'BUFFETCHILD']
    tier_trend_out = [{
        "period": mo,
        **{t: tier_trend[mo].get(t, 0) for t in tier_list},
        "total_pax": sum(tier_trend[mo].values()),
    } for mo in months]

    # ── Food cost trend (Buffet only — real unified COGS) ───────────────────
    # 2026-06-02: replaced the tier-pax × tier-unit-cost calc (which produced 0
    # because per-tier rows are empty) with the real buffet COGS computed above.
    fc_months = sorted(bf_cost_by_month.keys()) or months
    food_cost_trend = []
    for mo in fc_months:
        rev_ex, cost = bf_cost_by_month.get(mo, (0.0, 0.0))
        pct = (cost / rev_ex * 100) if rev_ex else None
        food_cost_trend.append({
            "period": mo, "revenue_ex": round(rev_ex, 0), "cost": round(cost, 0),
            "cost_pct": round(pct, 1) if pct is not None else None,
        })

    # ── Seat utilization proxy: per-branch avg pax/day vs network peak ──────
    branch_pax = defaultdict(list)
    branch_bills = defaultdict(list)
    branch_tables = defaultdict(list)
    for r in daily_pax_rows:
        branch_pax  [r["branch"]].append(r["pax"]   or 0)
        branch_bills[r["branch"]].append(r["bills"] or 0)
        branch_tables[r["branch"]].append(r["tables"] or 0)
    # Network peak = 90th percentile of daily pax across all branches
    all_daily_pax = sorted(p for vals in branch_pax.values() for p in vals)
    peak_pax = all_daily_pax[int(len(all_daily_pax) * 0.9)] if all_daily_pax else 1
    seat_util = []
    for br, vals in branch_pax.items():
        avg_pax = sum(vals) / len(vals) if vals else 0
        max_pax = max(vals) if vals else 0
        utilization = (avg_pax / peak_pax * 100) if peak_pax else 0
        seat_util.append({
            "branch": br,
            "avg_pax_per_day": round(avg_pax, 1),
            "max_pax_per_day": max_pax,
            "utilization_pct": round(utilization, 1),
        })
    seat_util.sort(key=lambda x: -x["utilization_pct"])

    # ── Turnover proxy: bills per table per day ─────────────────────────────
    turnover = []
    for br in branch_bills:
        bills_avg  = (sum(branch_bills[br])  / len(branch_bills[br]))  if branch_bills[br]  else 0
        tables_avg = (sum(branch_tables[br]) / len(branch_tables[br])) if branch_tables[br] else 0
        ratio = (bills_avg / tables_avg) if tables_avg > 0 else 0
        turnover.append({
            "branch":           br,
            "bills_per_day":    round(bills_avg, 1),
            "tables_per_day":   round(tables_avg, 1),
            "bills_per_table":  round(ratio, 2),
        })
    turnover.sort(key=lambda x: -x["bills_per_table"])

    # ── Waste / over-consumption rate ───────────────────────────────────────
    waste_trend = []
    for r in waste_rows:
        mo = r["m"]
        pax = pax_by_month.get(mo, 0) or 0
        rate = (r["qty"] / pax * 100) if pax > 0 else 0
        waste_trend.append({
            "period":   mo,
            "qty":      r["qty"] or 0,
            "revenue":  r["rev"] or 0,
            "rate_pct": round(rate, 2),  # # of fine charges per 100 pax
        })
    # Fill in missing months with 0
    waste_periods = {w["period"] for w in waste_trend}
    for mo in months:
        if mo not in waste_periods:
            waste_trend.append({"period": mo, "qty": 0, "revenue": 0, "rate_pct": 0})
    waste_trend.sort(key=lambda x: x["period"])

    # ── Beverage attach ─────────────────────────────────────────────────────
    bev_trend = []
    for r in bev_rows:
        total = r["total_rev"] or 0
        drink = r["drink_rev"] or 0
        attach_bills = (r["bills_with_drink"] or 0) / (r["bills"] or 1) * 100
        attach_rev   = (drink / total * 100) if total else 0
        bev_trend.append({
            "period":         r["m"],
            "drink_revenue":  round(drink, 0),
            "total_revenue":  round(total, 0),
            "attach_pct":     round(attach_rev, 1),
            "bills_attach_pct": round(attach_bills, 1),
        })

    # ── Daypart tier mix ────────────────────────────────────────────────────
    daypart_mix = defaultdict(lambda: defaultdict(int))
    for r in daypart_rows:
        daypart_mix[r["daypart"]][r["tier"]] += r["pax"] or 0
    daypart_out = []
    for dp in ('lunch', 'dinner', 'other'):
        tot = sum(daypart_mix[dp].values()) or 1
        daypart_out.append({
            "daypart": dp,
            "total_pax": sum(daypart_mix[dp].values()),
            **{t: round(daypart_mix[dp].get(t, 0) / tot * 100, 1) for t in tier_list},
        })

    # ── Branch cohort: months-since-open vs revenue/pax ─────────────────────
    from datetime import datetime as _dt
    def months_diff(a, b):
        try:
            da = _dt.strptime(a, "%Y-%m"); db = _dt.strptime(b, "%Y-%m")
            return (db.year - da.year) * 12 + (db.month - da.month)
        except Exception:
            return None

    cohort = defaultdict(list)  # months_since_open -> [revenues]
    for r in bcohort_rows:
        first = branch_first.get(r["branch"])
        if not first: continue
        m_diff = months_diff(first, r["m"])
        if m_diff is None or m_diff < 0 or m_diff > 24: continue
        cohort[m_diff].append({"revenue": r["rev"] or 0, "pax": r["pax"] or 0})
    cohort_curve = []
    for m_since in sorted(cohort.keys()):
        vals = cohort[m_since]
        cohort_curve.append({
            "months_since_open": m_since,
            "avg_revenue":  round(sum(v["revenue"] for v in vals) / len(vals), 0),
            "avg_pax":      round(sum(v["pax"] for v in vals) / len(vals), 0),
            "branch_count": len(vals),
        })

    # ── Pricing recommendation ──────────────────────────────────────────────
    TIER_PRICES = {'BUFFET350': 350, 'BUFFET500': 500, 'BUFFET590': 590, 'BUFFET799': 799, 'BUFFETCHILD': 295}
    # fix E6: use actual food-cost ratio from BoM data instead of raw sku_cost
    # (sku_cost.unit_cost ≈ selling price for tier SKUs, not ingredient cost)
    _fc_rev_total  = sum(v[0] for v in bf_cost_by_month.values())
    _fc_cogs_total = sum(v[1] for v in bf_cost_by_month.values())
    _food_cost_ratio = (_fc_cogs_total / _fc_rev_total) if _fc_rev_total > 0 else 0.40
    # Overall actual tier mix (across all months)
    actual_pax = {t: 0 for t in tier_list}
    for mo_data in tier_trend.values():
        for t, p in mo_data.items():
            actual_pax[t] += p
    total_pax = sum(actual_pax.values()) or 1
    actual_mix_pct = {t: actual_pax[t] / total_pax * 100 for t in tier_list}
    # Revenue contribution per tier (cost from real food-cost ratio, not sku_cost)
    tier_revenue_actual = {}
    tier_margin_actual = {}
    for t in tier_list:
        rev  = TIER_PRICES[t] * actual_pax[t]
        cost = TIER_PRICES[t] * _food_cost_ratio * actual_pax[t]
        tier_revenue_actual[t] = rev
        tier_margin_actual[t]  = rev - cost
    actual_total_rev    = sum(tier_revenue_actual.values())
    actual_total_margin = sum(tier_margin_actual.values())
    actual_margin_pct   = (actual_total_margin / actual_total_rev * 100) if actual_total_rev else 0

    # Optimal mix: shift 10% of 350/500 pax to 590, and 5% to 799 (conservative up-sell scenario)
    target_pax = dict(actual_pax)
    shift_350 = actual_pax.get('BUFFET350', 0) * 0.30  # 30% of 350 customers upgrade
    shift_500 = actual_pax.get('BUFFET500', 0) * 0.30
    target_pax['BUFFET350'] -= shift_350
    target_pax['BUFFET500'] -= shift_500
    target_pax['BUFFET590'] += shift_350 * 0.7 + shift_500 * 0.7
    target_pax['BUFFET799'] += shift_350 * 0.3 + shift_500 * 0.3
    target_revenue = sum(TIER_PRICES[t] * target_pax[t] for t in tier_list)
    target_cost    = sum(TIER_PRICES[t] * _food_cost_ratio * target_pax[t] for t in tier_list)
    target_margin  = target_revenue - target_cost
    target_margin_pct = (target_margin / target_revenue * 100) if target_revenue else 0
    margin_gain = target_margin - actual_total_margin
    revenue_gain = target_revenue - actual_total_rev

    pricing_rec = {
        "current_mix": {t: round(actual_mix_pct[t], 1) for t in tier_list},
        "target_mix":  {t: round(target_pax[t] / total_pax * 100, 1) for t in tier_list},
        "current_revenue": round(actual_total_rev, 0),
        "current_margin":  round(actual_total_margin, 0),
        "current_margin_pct": round(actual_margin_pct, 2),
        "target_revenue":  round(target_revenue, 0),
        "target_margin":   round(target_margin, 0),
        "target_margin_pct": round(target_margin_pct, 2),
        "revenue_gain":    round(revenue_gain, 0),
        "margin_gain":     round(margin_gain, 0),
        "assumption":      "30% ของลูกค้า tier 350/500 ขยับขึ้นไป 590 (70%) และ 799 (30%)",
    }

    # ── KPI summary ─────────────────────────────────────────────────────────
    total_buffet_pax = sum(actual_pax.values())
    premium_pax = actual_pax.get('BUFFET590', 0) + actual_pax.get('BUFFET799', 0)
    premium_pct = (premium_pax / total_buffet_pax * 100) if total_buffet_pax else 0
    # Per-tier % split (350/500/590/799/CHILD individually)
    tier_pct_split = {
        t: round(actual_pax.get(t, 0) / total_buffet_pax * 100, 1) if total_buffet_pax else 0
        for t in ('BUFFET350', 'BUFFET500', 'BUFFET590', 'BUFFET799', 'BUFFETCHILD')
    }
    waste_total_qty = sum(w["qty"] for w in waste_trend)
    waste_rate_overall = (waste_total_qty / total_buffet_pax * 100) if total_buffet_pax else 0
    bev_total = sum(b["drink_revenue"] for b in bev_trend)
    total_billed = sum(b["total_revenue"] for b in bev_trend)
    bev_attach_overall = (bev_total / total_billed * 100) if total_billed else 0
    # Bill-level attach: % of bills that ordered a drink (the actionable headline,
    # vs revenue-share which buffet dominates). From raw bev_rows counts.
    _bev_bills = sum((r["bills"] or 0) for r in bev_rows)
    _bev_bills_drink = sum((r["bills_with_drink"] or 0) for r in bev_rows)
    bev_bill_attach_overall = (_bev_bills_drink / _bev_bills * 100) if _bev_bills else 0
    food_cost_overall = sum(f["cost"] for f in food_cost_trend)
    rev_ex_overall    = sum(f["revenue_ex"] for f in food_cost_trend)
    food_cost_pct_overall = (food_cost_overall / rev_ex_overall * 100) if rev_ex_overall else 0
    total_bills = sum(sum(b) for b in branch_bills.values())

    # ── Phase 12: Combined whole-restaurant numbers (buffet + à la carte,
    # all SKUs, applies source-aware COGS rule). This is what the user wants
    # to see in the KPI hero row — one overall picture, not split by variant.
    combined_filters = ["b.name != 'ALL'"]
    combined_params: dict = {}
    if branch:
        combined_filters.append("b.name = :branch")
        combined_params["branch"] = branch
    _group_filter(combined_filters, combined_params, group)
    _month_range_filter(combined_filters, combined_params, month_from, month_to, None, col="ssm.period_month")
    combined_where = "WHERE " + " AND ".join(combined_filters)
    with get_db() as con:
        crow = con.execute(f"""
            SELECT
              COALESCE(SUM(ssm.subtotal_ex_vat), 0)        AS rev_ex,
              COALESCE(SUM(ssm.total_inc_vat),  0)        AS rev_inc,
              COALESCE(SUM({_cogs_case('ssm','sk','sc')}), 0)        AS cogs,
              COALESCE(SUM({_costed_rev_case('ssm','sk','sc')}), 0)  AS costed_rev_ex
              FROM sales_sku_monthly ssm
              JOIN branches b   ON b.branch_id = ssm.branch_id
              JOIN skus     sk  ON sk.sku_id   = ssm.sku_id
              LEFT JOIN sku_cost sc ON sc.sku_id = ssm.sku_id
            {combined_where}
        """, combined_params).fetchone()
    c_rev_ex      = float(crow["rev_ex"] or 0)
    c_rev_inc     = float(crow["rev_inc"] or 0)
    c_cogs        = float(crow["cogs"] or 0)
    c_costed_rev  = float(crow["costed_rev_ex"] or 0)   # coverage diagnostic only
    c_gp          = c_rev_ex - c_cogs
    c_gp_pct      = (c_gp   / c_rev_ex * 100) if c_rev_ex > 0 else None
    c_food_pct    = (c_cogs / c_rev_ex * 100) if c_rev_ex > 0 else None

    # Real pax = customer_count from sales_transaction (the trustworthy headcount),
    # not the tier-row qty which only counts buffet covers sold.
    real_total_pax = sum((r["pax"] or 0) for r in daily_pax_rows)
    waste_rate_real = (waste_total_qty / buffet_billed_pax * 100) if buffet_billed_pax else 0

    kpis = {
        "total_pax":             int(real_total_pax),
        "total_bills":           int(total_bills),
        "avg_pax_per_bill":      round(real_total_pax / total_bills, 2) if total_bills else 0,
        # Adult vs Child (real buffet codes) — replaces the empty tier split
        "adult_pax":             int(adult_pax),
        "child_pax":             int(child_pax),
        "child_pct":             round(child_pct, 1),
        "buffet_food_cost_pct":  round(food_cost_pct_overall, 1),  # buffet-only, real COGS
        "premium_tier_pct":      round(premium_pct, 1),
        "premium_tier_pct_target": round(
            (pricing_rec["target_mix"].get("BUFFET590", 0)
             + pricing_rec["target_mix"].get("BUFFET799", 0)),
            1,
        ),
        "food_cost_pct":         round(food_cost_pct_overall, 1),
        "food_cost_target_lo":   food_cost_target_lo,
        "food_cost_target_hi":   food_cost_target_hi,
        "over_consumption_pct":  round(waste_rate_real, 2),
        "beverage_attach_pct":   round(bev_attach_overall, 1),
        "beverage_bill_attach_pct": round(bev_bill_attach_overall, 1),
        "branch_count":          len(branch_pax),
        # ── Combined whole-restaurant (Phase 12) ──────────────────────────
        "combined_revenue_ex_vat":  round(c_rev_ex, 2),
        "combined_pos_total_inc_vat_diagnostic": round(c_rev_inc, 2),
        "combined_cogs":            round(c_cogs, 2),
        "combined_gross_profit":    round(c_gp, 2),
        "combined_gp_pct":          round(c_gp_pct, 1)   if c_gp_pct   is not None else None,
        "combined_food_cost_pct":   round(c_food_pct, 1) if c_food_pct is not None else None,
    }

    return {
        "kpis":            kpis,
        "tier_pct_split":  tier_pct_split,
        "tier_trend":      tier_trend_out,
        "food_cost_trend": food_cost_trend,
        "seat_util":       seat_util,
        "turnover":        turnover,
        "waste":           waste_trend,
        "beverage":        bev_trend,
        "daypart_tier":    daypart_out,
        "cohort":          cohort_curve,
        "pricing_rec":     pricing_rec,
    }


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


# ============================================================================
# HOURLY analysis with operating-hours filter (uses closed_hours table)
# ============================================================================
@app.get("/api/analysis/hourly_open")
@ttl_cache
def analysis_hourly_open(
    month:       Optional[str] = Query(None),
    month_from:  Optional[str] = Query(None),
    month_to:    Optional[str] = Query(None),
    branch:      Optional[str] = Query(None),
    group:       Optional[str] = Query(None),
    granularity: str           = Query("daily"),  # daily|weekly|monthly
):
    """Like /api/analysis/hourly but each hour is annotated with `is_open`,
    derived from the closed_hours table. Hours marked closed are still returned
    (count may be 0) so the chart can show them greyed out."""
    # Inline the hourly query (can't call FastAPI endpoints as plain functions).
    # No-branch default drops the branches JOIN + b.name!='ALL' guard (keeping the
    # source='Easy' filter) so the query uses the existing idx_tx_src_hour instead
    # of full-scanning. JOIN kept only when a branch is selected. Safe: no
    # 'ALL'-named branch rows exist (verified).
    filters = ["st.source = 'Easy'"]
    params: dict = {}
    join = ""
    if branch or group:
        join = "JOIN branches b ON b.branch_id = st.branch_id"
    if branch:
        filters.append("b.name = :branch")
        params["branch"] = branch
    _group_filter(filters, params, group)
    _month_range_filter(filters, params, month_from, month_to, month,
                        col="strftime('%Y-%m', st.sold_at)")
    where = "WHERE " + " AND ".join(filters)
    with get_db() as con:
        rows = con.execute(f"""
            SELECT CAST(strftime('%H', st.sold_at) AS INTEGER) AS hour,
                   COUNT(*)                                    AS tx_count,
                   COALESCE(SUM(st.customer_count), 0)         AS customer_count,
                   ROUND(SUM(st.total_inc_vat) / 1.07, 2)             AS revenue,
                   ROUND(AVG(st.total_inc_vat), 2)             AS avg_ticket
            FROM sales_transaction st
            {join}
            {where}
            GROUP BY hour ORDER BY hour
        """, params).fetchall()
        # Count distinct periods for average computation
        if granularity == "weekly":
            _period_expr = _iso_week_sql('st.sold_at')
        elif granularity == "monthly":
            _period_expr = "strftime('%Y-%m', st.sold_at)"
        else:
            _period_expr = "DATE(st.sold_at)"
        day_row = con.execute(f"""
            SELECT COUNT(DISTINCT {_period_expr}) AS cnt
            FROM sales_transaction st
            {join}
            {where}
        """, params).fetchone()
        total_days = max(day_row["cnt"] if day_row else 1, 1)

        hour_map = {r["hour"]: dict(r) for r in rows}
        raw = [hour_map.get(h, {"hour": h, "tx_count": 0, "customer_count": 0,
                                "revenue": 0.0, "avg_ticket": 0.0}) for h in range(24)]

        if branch:
            ch_rows = con.execute("""
                SELECT ch.hour_from, ch.hour_to, ch.is_closed
                FROM closed_hours ch
                JOIN branches b ON b.branch_id = ch.branch_id
                WHERE b.name = :branch
            """, {"branch": branch}).fetchall()
        else:
            ch_rows = con.execute(
                "SELECT hour_from, hour_to, is_closed FROM closed_hours"
            ).fetchall()

    if ch_rows:
        # OPEN-WINDOW model: each (branch, dow) row gives an open window
        # [hour_from..hour_to] unless is_closed (whole day shut). The chart's
        # is_open is the UNION of open windows across the rows in scope.
        open_set = set()
        any_open = False
        for r in ch_rows:
            if r["is_closed"]:
                continue  # this (branch, dow) is fully closed → contributes no open hours
            hf = r["hour_from"] if r["hour_from"] is not None else 10
            ht = r["hour_to"]   if r["hour_to"]   is not None else 21
            if hf <= ht:
                for h in range(hf, ht + 1): open_set.add(h)
            else:  # window wraps past midnight
                for h in range(hf, 24):    open_set.add(h)
                for h in range(0, ht + 1): open_set.add(h)
            any_open = True
        open_hours = open_set if any_open else set(range(10, 22))
    else:
        open_hours = set(range(10, 22))  # default 10:00–21:00 (open hours 10..21)
    for h in raw:
        h["is_open"] = h["hour"] in open_hours
        h["avg_daily_revenue"] = round((h["revenue"] or 0) / total_days, 2)
        h["avg_daily_tx"]      = round((h["tx_count"] or 0) / total_days, 2)
        h["total_days"]        = total_days
    return raw


@app.get("/api/analysis/hour_detail")
@ttl_cache
def analysis_hour_detail(
    hour:       int           = Query(..., ge=0, le=23),
    month:      Optional[str] = Query(None),
    month_from: Optional[str] = Query(None),
    month_to:   Optional[str] = Query(None),
    branch:     Optional[str] = Query(None),
):
    """Drill-down for one hour-of-day: revenue/tx/customers split by branch and by
    day-of-week, powering the Time & Flow peak-hour cards. Revenue is ex-VAT."""
    filters = ["b.name != 'ALL'", "st.source = 'Easy'",
               "CAST(strftime('%H', st.sold_at) AS INTEGER) = :hour"]
    params: dict = {"hour": hour}
    if branch:
        filters.append("b.name = :branch")
        params["branch"] = branch
    _month_range_filter(filters, params, month_from, month_to, month,
                        col="strftime('%Y-%m', st.sold_at)")
    where = "WHERE " + " AND ".join(filters)
    with get_db() as con:
        by_branch = con.execute(f"""
            SELECT b.name AS branch, COUNT(*) AS tx_count,
                   COALESCE(SUM(st.customer_count), 0) AS customer_count,
                   ROUND(SUM(st.total_inc_vat) / 1.07, 2) AS revenue
            FROM sales_transaction st JOIN branches b ON b.branch_id = st.branch_id
            {where} GROUP BY b.name ORDER BY revenue DESC""", params).fetchall()
        by_dow = con.execute(f"""
            SELECT CAST(strftime('%w', st.sold_at) AS INTEGER) AS dow,
                   COUNT(*) AS tx_count,
                   ROUND(SUM(st.total_inc_vat) / 1.07, 2) AS revenue
            FROM sales_transaction st JOIN branches b ON b.branch_id = st.branch_id
            {where} GROUP BY dow ORDER BY dow""", params).fetchall()
        dd = con.execute(f"""SELECT COUNT(DISTINCT DATE(st.sold_at)) AS c
            FROM sales_transaction st JOIN branches b ON b.branch_id = st.branch_id
            {where}""", params).fetchone()
    total_days = max(dd["c"] if dd else 1, 1)
    grand = sum(r["revenue"] or 0 for r in by_branch)
    return {
        "hour":        hour,
        "total_days":  total_days,
        "grand_revenue": round(grand, 2),
        "by_branch":   [dict(r) for r in by_branch],
        "by_dow":      [dict(r) for r in by_dow],
    }


# ---------------------------------------------------------------------------
# /api/home/brief  — Daily Brief summary
# ---------------------------------------------------------------------------
@app.get("/api/home/brief")
@ttl_cache
def home_brief(branch: Optional[str] = Query(None)):
    """Yesterday vs target + MTD + recent anomalies + top/bottom branch."""
    with get_db() as con:
        # Get yesterday's date from MAX(sold_at)
        max_row = con.execute(
            "SELECT DATE(MAX(sold_at)) AS max_date FROM sales_transaction WHERE source='Easy'"
        ).fetchone()
        yesterday = max_row["max_date"] if max_row else None
        if not yesterday:
            return {"yesterday": None}

        from datetime import datetime as _dt
        try:
            yd = _dt.strptime(yesterday, "%Y-%m-%d")
            dow_names = ["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday"]
            dow_label = dow_names[yd.weekday()]
            current_month = yd.strftime("%Y-%m")
            wow_date = (_dt.strptime(yesterday, "%Y-%m-%d").replace(
                day=yd.day) if True else None)
            import datetime as _dtt
            wow_date_str = (_dt.strptime(yesterday, "%Y-%m-%d") - _dtt.timedelta(days=7)).strftime("%Y-%m-%d")
        except Exception:
            dow_label = ""
            current_month = yesterday[:7]
            wow_date_str = None

        # Branch filter
        branch_filter = ""
        branch_params: dict = {"yesterday": yesterday}
        if branch:
            branch_filter = " AND b.name = :branch"
            branch_params["branch"] = branch

        # Revenue / tx / customers yesterday
        yest_row = con.execute(f"""
            SELECT
                ROUND(SUM(st.total_inc_vat) / 1.07, 2)        AS revenue,
                COUNT(*)                                AS tx_count,
                COALESCE(SUM(st.customer_count), 0)    AS customer_count,
                ROUND(AVG(st.total_inc_vat), 2)        AS avg_ticket
            FROM sales_transaction st
            JOIN branches b ON b.branch_id = st.branch_id
            WHERE DATE(st.sold_at) = :yesterday
              AND st.source = 'Easy'
              AND b.name != 'ALL'
              {branch_filter}
        """, branch_params).fetchone()

        # Revenue same day last week
        wow_row = None
        if wow_date_str:
            wow_params = dict(branch_params)
            wow_params["yesterday"] = wow_date_str
            wow_row = con.execute(f"""
                SELECT ROUND(SUM(st.total_inc_vat) / 1.07, 2) AS revenue
                FROM sales_transaction st
                JOIN branches b ON b.branch_id = st.branch_id
                WHERE DATE(st.sold_at) = :yesterday
                  AND st.source = 'Easy'
                  AND b.name != 'ALL'
                  {branch_filter}
            """, wow_params).fetchone()

        # MTD revenue
        mtd_params: dict = {"month": current_month}
        if branch:
            mtd_params["branch"] = branch
        mtd_row = con.execute(f"""
            SELECT
                ROUND(SUM(st.total_inc_vat) / 1.07, 2)     AS mtd_revenue,
                COUNT(DISTINCT DATE(st.sold_at))    AS mtd_days
            FROM sales_transaction st
            JOIN branches b ON b.branch_id = st.branch_id
            WHERE strftime('%Y-%m', st.sold_at) = :month
              AND st.source = 'Easy'
              AND b.name != 'ALL'
              {'AND b.name = :branch' if branch else ''}
        """, mtd_params).fetchone()

        # Revenue by branch yesterday (top 3)
        yest_branch_rows = con.execute(f"""
            SELECT b.name AS branch,
                   ROUND(SUM(st.total_inc_vat) / 1.07, 2) AS revenue
            FROM sales_transaction st
            JOIN branches b ON b.branch_id = st.branch_id
            WHERE DATE(st.sold_at) = :yesterday
              AND st.source = 'Easy'
              AND b.name != 'ALL'
              {branch_filter}
            GROUP BY b.name
            ORDER BY revenue DESC
            LIMIT 3
        """, branch_params).fetchall()

        # MTD by branch
        mtd_branch_rows = con.execute(f"""
            SELECT b.name AS branch,
                   ROUND(SUM(st.total_inc_vat) / 1.07, 2) AS mtd_revenue
            FROM sales_transaction st
            JOIN branches b ON b.branch_id = st.branch_id
            WHERE strftime('%Y-%m', st.sold_at) = :month
              AND st.source = 'Easy'
              AND b.name != 'ALL'
              {'AND b.name = :branch' if branch else ''}
            GROUP BY b.name
            ORDER BY mtd_revenue DESC
        """, mtd_params).fetchall()

        # Branch targets for current month
        try:
            target_rows = con.execute("""
                SELECT b.name AS branch, bt.revenue_target
                FROM branch_targets bt
                JOIN branches b ON b.branch_id = bt.branch_id
                WHERE bt.period_month = :month
            """, {"month": current_month}).fetchall()
            target_map = {r["branch"]: r["revenue_target"] for r in target_rows}
        except Exception:
            target_map = {}

        mtd_by_branch = []
        for r in mtd_branch_rows:
            br = r["branch"]
            mtd_by_branch.append({
                "branch": br,
                "mtd_revenue": r["mtd_revenue"],
                "revenue_target": target_map.get(br),
            })

        # MTD BoM food cost % (tier SKUs excluded to avoid double-count)
        # Phase 18d: migrated to _cogs_case helper — unified tier exclusion (incl. plain
        # BUFFET) + monthly cost overrides; was leaking ~825M phantom COGS before.
        bom_params: dict = {"month": current_month}
        if branch:
            bom_params["branch"] = branch
        mtd_bom_row = con.execute(f"""
            SELECT ROUND(SUM({_cogs_case('s','sk','c')}), 2) AS bom_cost
            FROM sales_sku_monthly s
            JOIN skus sk     ON sk.sku_id = s.sku_id
            LEFT JOIN sku_cost c ON c.sku_id = s.sku_id
            JOIN branches b  ON b.branch_id = s.branch_id
            WHERE s.period_month = :month
              AND b.name != 'ALL'
              {'AND b.name = :branch' if branch else ''}
        """, bom_params).fetchone()
        food_cost_pct_mtd = None
        mtd_bom_cost = mtd_bom_row["bom_cost"] if mtd_bom_row else None
        mtd_rev_val  = mtd_row["mtd_revenue"] if mtd_row else 0
        if mtd_bom_cost and mtd_rev_val and mtd_rev_val > 0:
            food_cost_pct_mtd = round(mtd_bom_cost / mtd_rev_val * 100, 1)

    return {
        "yesterday": yesterday,
        "dow_label": dow_label,
        "revenue_yesterday": yest_row["revenue"] if yest_row else 0,
        "tx_yesterday": yest_row["tx_count"] if yest_row else 0,
        "customer_yesterday": yest_row["customer_count"] if yest_row else 0,
        "avg_ticket_yesterday": yest_row["avg_ticket"] if yest_row else 0,
        "revenue_wow": wow_row["revenue"] if wow_row else None,
        "mtd_revenue": mtd_row["mtd_revenue"] if mtd_row else 0,
        "mtd_days": mtd_row["mtd_days"] if mtd_row else 0,
        "yesterday_by_branch": [dict(r) for r in yest_branch_rows],
        "mtd_by_branch": mtd_by_branch,
        "food_cost_pct_mtd": food_cost_pct_mtd,
        "mtd_bom_cost": mtd_bom_cost,
    }


# ---------------------------------------------------------------------------
# /api/home/leak-watch  — daily money-leak radar for the Daily Brief
# ---------------------------------------------------------------------------
@app.get("/api/home/leak-watch")
@ttl_cache
def home_leak_watch(date: Optional[str] = Query(None), branch: Optional[str] = Query(None),
                    group: Optional[str] = Query(None)):
    """One focus day's money-leak signals (default = latest day) for the Daily Brief:
      · heavy_discount — bills discounted >=30% of the (reconstructed) pre-discount base, incl. free bills
      · cash_heavy     — the subset paid mostly in CASH (the classic skim flag — no card/transfer trail)
      · reprinted      — discounted bills printed more than once (re-print abuse)
      · zero_gross     — discount-only / zeroed bills (gross <= 0)
    All from sales_transaction at date grain. Void/cancelled bills live in pos_cancel_bill
    (month-grain only) and are intentionally not folded in here."""
    base = "((st.total_inc_vat - COALESCE(st.service_charge,0))/1.07 + st.discount)"
    with get_db() as con:
        maxd = con.execute(
            "SELECT DATE(MAX(sold_at)) FROM sales_transaction WHERE source='Easy'"
        ).fetchone()[0]
        if not maxd:
            return {"date": None}
        focus = (date or maxd)[:10]
        bf = ""; p: dict = {"focus": focus}
        if branch:
            bf += " AND b.name=:branch"; p["branch"] = branch
        if group:
            bf += " AND COALESCE(b.branch_type,'master')=:grp"; p["grp"] = group
        where = f"WHERE DATE(st.sold_at)=:focus AND st.source='Easy' AND b.name!='ALL'{bf}"
        heavy = con.execute(f"""
            SELECT b.name AS branch, COUNT(*) AS bills, ROUND(SUM(st.discount)) AS baht
            FROM sales_transaction st JOIN branches b ON b.branch_id=st.branch_id
            {where} AND st.discount>0 AND {base}>0 AND st.discount*1.0/{base} >= 0.30
            GROUP BY b.name ORDER BY baht DESC""", p).fetchall()
        # Resolve each heavy bill's dominant tender via an indexed (branch_id, receipt_no,
        # amount) lookup, computed in a derived table over ONLY the heavy bills (post-WHERE)
        # — not a ROW_NUMBER() window over the entire sale_payment table.
        dom_code = ("(SELECT CASE WHEN UPPER(sp.payment_code)='CASH' THEN 'Cash' ELSE sp.payment_code END "
                    "FROM sale_payment sp WHERE sp.branch_id=st.branch_id AND sp.receipt_no=st.receipt_no "
                    "ORDER BY sp.amount DESC LIMIT 1)")
        cash_heavy = con.execute(f"""
            SELECT branch, COUNT(*) AS bills, ROUND(SUM(discount)) AS baht FROM (
              SELECT b.name AS branch, st.discount AS discount, {dom_code} dc
              FROM sales_transaction st JOIN branches b ON b.branch_id=st.branch_id
              {where} AND st.discount>0 AND {base}>0 AND st.discount*1.0/{base} >= 0.30
            ) WHERE dc='Cash'
            GROUP BY branch ORDER BY baht DESC""", p).fetchall()
        reprint = con.execute(f"""
            SELECT b.name AS branch, COUNT(*) AS bills, ROUND(SUM(st.discount)) AS baht
            FROM sales_transaction st JOIN branches b ON b.branch_id=st.branch_id
            {where} AND st.discount>0 AND COALESCE(st.print_times,0)>1
            GROUP BY b.name ORDER BY bills DESC""", p).fetchall()
        zero = con.execute(f"""
            SELECT b.name AS branch, COUNT(*) AS bills, ROUND(SUM(st.discount)) AS baht
            FROM sales_transaction st JOIN branches b ON b.branch_id=st.branch_id
            {where} AND st.discount>0 AND {base} <= 0
            GROUP BY b.name ORDER BY baht DESC""", p).fetchall()
        # ALL discounted bills for the focus day — the reconciling total the abnormal
        # subsets above are carved from (ties out with the Discounts menu for the day).
        total = con.execute(f"""
            SELECT b.name AS branch, COUNT(*) AS bills, ROUND(SUM(st.discount)) AS baht
            FROM sales_transaction st JOIN branches b ON b.branch_id=st.branch_id
            {where} AND st.discount>0
            GROUP BY b.name ORDER BY baht DESC""", p).fetchall()

    def pack(rows):
        L = [dict(r) for r in rows]
        return {"bills": sum(r["bills"] for r in L),
                "baht": sum(r["baht"] or 0 for r in L),
                "by_branch": L}
    return {"date": focus, "heavy_discount": pack(heavy), "cash_heavy": pack(cash_heavy),
            "reprinted": pack(reprint), "zero_gross": pack(zero), "total": pack(total)}


# ---------------------------------------------------------------------------
# /api/home/menu-movers  — yesterday's biggest menu movers UP and DOWN (by impact)
# ---------------------------------------------------------------------------
@app.get("/api/home/menu-movers")
@ttl_cache
def home_menu_movers(date: Optional[str] = Query(None), branch: Optional[str] = Query(None),
                     group: Optional[str] = Query(None), limit: int = Query(6, ge=1, le=30),
                     min_ph: float = Query(0.5, ge=0.0), min_qty: int = Query(10, ge=1)):
    """Biggest menu movers UP and DOWN on one day, measured PER HEAD (plates per 100
    customers) vs the trailing 21-day average — owner wants per-head, not totals, because it
    strips out the traffic confound: +plates can just mean +customers, but a per-head rise
    means each customer genuinely chose it more. Ranked by absolute per-100-head change.
    A mover must clear ALL of: min_qty plates today (real presence), a meaningful ratio
    (>=1.5x up / <=0.5x down), and min_ph plates/100-head of swing (impact floor — keeps a
    slow/busy day from reading every menu as a mover, and big-%/no-impact items out).
    Qty only from sales_sku_daily; customers from sales_transaction (no cost — iron rule)."""
    import datetime as _dt
    with get_db() as con:
        maxd = con.execute("SELECT MAX(day) FROM sales_sku_daily").fetchone()[0]
        if not maxd:
            return {"date": None, "up": [], "down": []}
        focus = (date or maxd)[:10]
        fy, fm, fd = (int(x) for x in focus.split("-"))
        ws = (_dt.date(fy, fm, fd) - _dt.timedelta(days=21)).isoformat()
        bf = ""; p: dict = {"focus": focus, "ws": ws}
        if branch:
            bf += " AND b.name=:branch"; p["branch"] = branch
        if group:
            bf += " AND COALESCE(b.branch_type,'master')=:grp"; p["grp"] = group
        # customers per day in scope — the denominator for per-head
        today_c_row = con.execute(f"""
            SELECT SUM(st.customer_count) c
            FROM sales_transaction st JOIN branches b ON b.branch_id=st.branch_id
            WHERE st.source='Easy' AND b.name!='ALL' AND DATE(st.sold_at)=:focus {bf}""", p).fetchone()
        today_c = (today_c_row["c"] if today_c_row else 0) or 0
        if not today_c:
            return {"date": focus, "up": [], "down": [], "today_customers": 0}
        p["tc"] = today_c
        rows = con.execute(f"""
            WITH cust AS (
              SELECT DATE(st.sold_at) day, SUM(st.customer_count) c
              FROM sales_transaction st JOIN branches b ON b.branch_id=st.branch_id
              WHERE st.source='Easy' AND b.name!='ALL'
                AND DATE(st.sold_at)>=:ws AND DATE(st.sold_at)<:focus {bf}
              GROUP BY day),
            food AS (
              SELECT sk.sku_id FROM skus sk
              JOIN categories c ON c.category_id=sk.category_id
              WHERE {_FOOD_CATEGORY_FILTER}),
            today AS (
              SELECT d.sku_id, SUM(d.qty) q, SUM(d.subtotal_ex_vat) rev
              FROM sales_sku_daily d JOIN branches b ON b.branch_id=d.branch_id
              WHERE d.day=:focus {bf} GROUP BY d.sku_id),
            histday AS (
              SELECT d.day, d.sku_id, SUM(d.qty) daily, SUM(d.subtotal_ex_vat) drev
              FROM sales_sku_daily d JOIN branches b ON b.branch_id=d.branch_id
              WHERE d.day>=:ws AND d.day<:focus {bf}
              GROUP BY d.day, d.sku_id),
            hist AS (
              SELECT hd.sku_id, AVG(hd.daily * 1.0 / cust.c) avg_ph, AVG(hd.drev) avg_rev, COUNT(*) n
              FROM histday hd JOIN cust ON cust.day=hd.day
              WHERE cust.c > 0
              GROUP BY hd.sku_id)
            SELECT s.description, s.sku_code,
                   COALESCE(t.q,0) today, COALESCE(h.avg_ph,0) avg_ph, COALESCE(h.n,0) n,
                   COALESCE(t.rev,0) rev_today, COALESCE(h.avg_rev,0) rev_avg
            FROM hist h
            LEFT JOIN today t ON t.sku_id=h.sku_id
            JOIN skus s ON s.sku_id=h.sku_id
            WHERE h.n >= 10 AND h.sku_id IN (SELECT sku_id FROM food)
            UNION
            SELECT s.description, s.sku_code, t.q today, 0 avg_ph, 0 n,
                   t.rev rev_today, 0 rev_avg
            FROM today t JOIN skus s ON s.sku_id=t.sku_id
            WHERE t.sku_id NOT IN (SELECT sku_id FROM hist WHERE n >= 10)
              AND t.sku_id IN (SELECT sku_id FROM food)
        """, p).fetchall()
        # total food revenue in scope today — denominator for the movers' revenue share
        frow = con.execute(f"""
            SELECT COALESCE(SUM(d.subtotal_ex_vat),0) rev
            FROM sales_sku_daily d JOIN branches b ON b.branch_id=d.branch_id
            WHERE d.day=:focus {bf}
              AND d.sku_id IN (SELECT sk.sku_id FROM skus sk
                               JOIN categories c ON c.category_id=sk.category_id
                               WHERE {_FOOD_CATEGORY_FILTER})""", p).fetchone()
        today_food_rev = (frow["rev"] if frow else 0) or 0
        # Buffet PACKAGE sales (the real revenue driver for buffet dishes, which carry
        # no line revenue): covers + revenue + avg price/cover, focus day vs trailing avg.
        bp = con.execute(f"""
            SELECT COALESCE(SUM(CASE WHEN d.day=:focus THEN d.subtotal_ex_vat END),0) rev_t,
                   COALESCE(SUM(CASE WHEN d.day=:focus THEN d.qty END),0) cov_t,
                   COALESCE(SUM(CASE WHEN d.day<:focus THEN d.subtotal_ex_vat END),0) rev_h,
                   COALESCE(SUM(CASE WHEN d.day<:focus THEN d.qty END),0) cov_h,
                   COUNT(DISTINCT CASE WHEN d.day<:focus THEN d.day END) hdays
            FROM sales_sku_daily d JOIN branches b ON b.branch_id=d.branch_id JOIN skus s ON s.sku_id=d.sku_id
            WHERE d.day>=:ws AND d.day<=:focus AND s.sku_code LIKE 'BUFFET%' {bf}""", p).fetchone()
        hd = (bp["hdays"] or 1) if bp else 1
        buf = {
            "rev_today": round(bp["rev_t"]), "rev_avg": round(bp["rev_h"] / hd),
            "covers_today": round(bp["cov_t"]), "covers_avg": round(bp["cov_h"] / hd),
            "ppc_today": round(bp["rev_t"] / bp["cov_t"]) if bp["cov_t"] else 0,
            "ppc_avg": round(bp["rev_h"] / bp["cov_h"]) if bp["cov_h"] else 0,
        } if bp else None
        # à-la-carte food revenue trailing daily average (today = today_food_rev)
        af = con.execute(f"""
            SELECT COALESCE(SUM(d.subtotal_ex_vat),0) rev, COUNT(DISTINCT d.day) hdays
            FROM sales_sku_daily d JOIN branches b ON b.branch_id=d.branch_id
            WHERE d.day>=:ws AND d.day<:focus {bf}
              AND d.sku_id IN (SELECT sk.sku_id FROM skus sk
                               JOIN categories c ON c.category_id=sk.category_id
                               WHERE {_FOOD_CATEGORY_FILTER})""", p).fetchone()
        ala_avg = round((af["rev"] or 0) / (af["hdays"] or 1)) if af else 0
    up, down = [], []
    for r in rows:
        today = r["today"] or 0
        avg_ph = r["avg_ph"] or 0          # avg plates per customer (trailing)
        n = r["n"] or 0
        name = (r["description"] or r["sku_code"] or "").replace("(B) ", "").replace("(A) ", "")
        today_ph = today / today_c
        ph100 = round(today_ph * 100, 2)   # plates per 100 customers today
        rev_today = round(r["rev_today"] or 0)                 # this menu's revenue today
        rev_delta = round((r["rev_today"] or 0) - (r["rev_avg"] or 0))  # vs trailing avg day
        if n < 10:
            # new / no-history item: only an UP mover, and only if it has real presence
            if today >= max(min_qty, 20):
                up.append({"menu": name, "today": round(today), "ph100": ph100, "avg_ph100": 0,
                           "delta_ph100": ph100, "pct": None, "is_new": True,
                           "rev_today": rev_today, "rev_delta": rev_today})
            continue
        if avg_ph <= 0 or today < min_qty:
            continue
        ratio = today_ph / avg_ph
        delta100 = (today_ph - avg_ph) * 100      # per-100-head swing (the impact metric)
        if delta100 >= min_ph and ratio >= 1.5:
            up.append({"menu": name, "today": round(today), "ph100": ph100,
                       "avg_ph100": round(avg_ph * 100, 2), "delta_ph100": round(delta100, 2),
                       "pct": round((ratio - 1) * 100), "is_new": False,
                       "rev_today": rev_today, "rev_delta": rev_delta})
        elif delta100 <= -min_ph and ratio <= 0.5:
            down.append({"menu": name, "today": round(today), "ph100": ph100,
                         "avg_ph100": round(avg_ph * 100, 2), "delta_ph100": round(delta100, 2),
                         "pct": round((ratio - 1) * 100), "is_new": False,
                         "rev_today": rev_today, "rev_delta": rev_delta})
    up.sort(key=lambda s: -s["delta_ph100"])
    down.sort(key=lambda s: s["delta_ph100"])
    up_lim, down_lim = up[:limit], down[:limit]
    # Revenue relationship: how the displayed menu-mix shift moved food revenue.
    rev_up = round(sum(x["rev_delta"] for x in up_lim))      # extra ฿ from rising menus
    rev_down = round(sum(x["rev_delta"] for x in down_lim))  # ฿ lost from falling menus (<=0)
    top_gain = max(up_lim, key=lambda s: s["rev_delta"], default=None)
    top_loss = min(down_lim, key=lambda s: s["rev_delta"], default=None)
    return {"date": focus, "window_days": 21, "min_ph": min_ph, "today_customers": today_c,
            "up": up_lim, "down": down_lim,
            "today_food_rev": round(today_food_rev),
            "rev_up_delta": rev_up, "rev_down_delta": rev_down, "rev_net_delta": rev_up + rev_down,
            "top_gain": top_gain, "top_loss": top_loss,
            "sales": {"buffet": buf, "alacarte_today": round(today_food_rev), "alacarte_avg": ala_avg}}


# ---------------------------------------------------------------------------
# /api/reports/management — Daily, Weekly & Monthly Management Summary (printable)
# ---------------------------------------------------------------------------
@app.get("/api/reports/management")
@ttl_cache
def report_management(
    period: str = Query("daily"),            # 'daily' | 'weekly' | 'monthly'
    date:   Optional[str] = Query(None),     # focus day (default = latest day in data)
    week:   Optional[str] = Query(None),     # weekly: any date in the wanted FULL week (default = last completed week)
    month:  Optional[str] = Query(None),     # monthly: 'YYYY-MM' (default = last COMPLETE month)
    branch: Optional[str] = Query(None),
    group:  Optional[str] = Query(None),
    lang:   str = Query("en"),               # 'en' | 'th' — language for labels, comp_label & action items
):
    """One-glance management summary for a day, a COMPLETE week or a COMPLETE month.

    Mirrors the proven idioms used across the dashboard so the numbers tie out:
      · revenue = SUM(total_inc_vat)/1.07  (ex-VAT, the reconciled accounting basis)
      · current-vs-prior is WoW-fair (same weekday last week / full prior week);
        monthly compares against the previous calendar month + same month last year
      · food-cost % is the BoM figure for the period's month (_cogs_case — tier-safe)
      · money-leak signals reuse the Daily-Brief leak definitions (heavy/cash/reprint/zero)
    Weekly reports cover only FULL Mon–Sun weeks; monthly reports only COMPLETE
    calendar months. The response carries the selectable periods (`weeks`/`months`)
    and the chosen one (`selected_week`/`selected_month`), so the same simple
    presentation serves the Daily, Weekly AND Monthly report."""
    import datetime as _dt, calendar as _cal
    if period not in ("daily", "weekly", "monthly"):
        period = "daily"
    th = (lang == "th")          # localise labels / comp_label / action items
    if th:
        MON = ["ม.ค.","ก.พ.","มี.ค.","เม.ย.","พ.ค.","มิ.ย.","ก.ค.","ส.ค.","ก.ย.","ต.ค.","พ.ย.","ธ.ค."]
        DOW = ["วันจันทร์","วันอังคาร","วันพุธ","วันพฤหัสบดี","วันศุกร์","วันเสาร์","วันอาทิตย์"]
    else:
        MON = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"]
        DOW = ["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday"]
    def dlabel(d): return f"{d.day} {MON[d.month-1]} {d.year}"

    base = ["st.source='Easy'", "b.name!='ALL'"]
    params: dict = {}
    if branch:
        base.append("b.name=:branch"); params["branch"] = branch
    _group_filter(base, params, group)
    where = " AND ".join(base)

    def pct(c, p):
        return round((c - p) / p * 100, 1) if p else None

    with get_db() as con:
        rng = con.execute(
            f"SELECT MIN(date(st.sold_at)) lo, MAX(date(st.sold_at)) hi "
            f"FROM sales_transaction st JOIN branches b ON b.branch_id=st.branch_id "
            f"WHERE {where}", params).fetchone()
        maxd = rng["hi"] if rng else None
        mind = (rng["lo"] if rng else None) or "9999-99-99"
        if not maxd:
            return {"available": False, "period": period}
        # Never DEFAULT the daily report onto the in-progress current day: its handful
        # of early sales read as a chain-wide ~-100% collapse across every section.
        # With no explicit date, anchor on the latest COMPLETE day (strictly before
        # Thai 'today', UTC+7). An explicitly-requested today is honoured but flagged
        # partial below. Mirrors the weekly guard, which never picks the open week.
        today_th = con.execute("SELECT date('now','+7 hours')").fetchone()[0]
        if date:
            focus = date[:10]
        else:
            _cd = con.execute(
                f"SELECT MAX(date(st.sold_at)) d FROM sales_transaction st "
                f"JOIN branches b ON b.branch_id=st.branch_id "
                f"WHERE {where} AND date(st.sold_at) < :t",
                {**params, "t": today_th}).fetchone()
            focus = ((_cd["d"] if _cd and _cd["d"] else maxd))[:10]
        fy, fm, fd = (int(x) for x in focus.split("-"))
        fdate = _dt.date(fy, fm, fd)

        # ---- period bounds + WoW-fair comparison window -------------------
        latest_day = _dt.date.fromisoformat(maxd)
        weeks: list = []
        selected_week = None
        months: list = []
        selected_month = None
        if period == "daily":
            cs = ce = fdate
            ps = pe = fdate - _dt.timedelta(days=7)
            days_in_period = 1
            partial = (focus == today_th)   # in-progress current day (explicit date=today)
            ref_anchor = fdate
            if th:
                label = f"{DOW[fdate.weekday()]} {dlabel(fdate)}"
                comp_label = "วันเดียวกันสัปดาห์ก่อน"
            else:
                label = f"{DOW[fdate.weekday()]}, {dlabel(fdate)}"
                comp_label = "same weekday last week"
        elif period == "monthly":
            # COMPLETE calendar months only (mirrors the weekly full-week rule): a
            # month is selectable once its last day is <= the latest data day —
            # never the in-progress month, whose partial totals would read as a
            # chain-wide collapse against a full prior month. Most recent first.
            mrows = con.execute(
                f"SELECT DISTINCT strftime('%Y-%m', st.sold_at) m FROM sales_transaction st "
                f"JOIN branches b ON b.branch_id=st.branch_id WHERE {where}", params).fetchall()
            for m in sorted((r["m"] for r in mrows if r["m"]), reverse=True):
                my, mm = int(m[:4]), int(m[5:7])
                if _dt.date(my, mm, _cal.monthrange(my, mm)[1]) <= latest_day:
                    months.append({"month": m, "label": f"{MON[mm-1]} {my}"})
                if len(months) >= 24:
                    break
            # Default = latest complete month; fall back to the latest data month
            # (flagged partial) only when no month has completed yet.
            sel = months[0]["month"] if months else maxd[:7]
            if month:
                m10 = month[:7]
                if any(x["month"] == m10 for x in months):
                    sel = m10
            sy, sm = int(sel[:4]), int(sel[5:7])
            cs = _dt.date(sy, sm, 1)
            ce = _dt.date(sy, sm, _cal.monthrange(sy, sm)[1])
            days_in_period = (ce - cs).days + 1
            partial = ce > latest_day
            ref_anchor = ce
            pm_l = cs - _dt.timedelta(days=1)          # previous calendar month
            ps = pm_l.replace(day=1)
            pe = pm_l
            selected_month = sel
            if th:
                label = f"เดือน {MON[sm-1]} {sy}"
                comp_label = "เดือนก่อนหน้า"
            else:
                label = f"{MON[sm-1]} {sy}"
                comp_label = "previous month"
        else:
            # FULL weeks only (Mon–Sun). First, the selectable COMPLETED weeks that
            # have data — most recent first, cap 26 — so the picker and the default
            # both come from real, finished weeks (never the current partial week).
            mon_expr = "date(st.sold_at, '-' || ((CAST(strftime('%w', st.sold_at) AS INT)+6)%7) || ' days')"
            monrows = con.execute(
                f"SELECT DISTINCT {mon_expr} mon FROM sales_transaction st "
                f"JOIN branches b ON b.branch_id=st.branch_id WHERE {where}", params).fetchall()
            for m in sorted((r["mon"] for r in monrows if r["mon"]), reverse=True):
                md = _dt.date.fromisoformat(m)
                sun = md + _dt.timedelta(days=6)
                if sun <= latest_day:
                    weeks.append({"start": m, "end": sun.isoformat(),
                                  "label": f"{dlabel(md)} – {dlabel(sun)}"})
                if len(weeks) >= 26:
                    break
            # Default = most recent completed week WITH data; fall back to the calendar
            # week before the latest day if there is no fully-completed week yet.
            cur_mon = latest_day - _dt.timedelta(days=latest_day.weekday())
            default_mon = (_dt.date.fromisoformat(weeks[0]["start"]) if weeks
                           else cur_mon - _dt.timedelta(days=7))
            sel_mon = default_mon
            if week:
                try:
                    wd = _dt.date.fromisoformat(week[:10])
                    cand = wd - _dt.timedelta(days=wd.weekday())          # snap to that week's Monday
                    if (cand + _dt.timedelta(days=6)) <= latest_day:      # never an incomplete week
                        sel_mon = cand
                except Exception:
                    pass
            cs = sel_mon
            ce = sel_mon + _dt.timedelta(days=6)
            days_in_period = 7
            partial = False
            ref_anchor = ce
            ps = cs - _dt.timedelta(days=7)
            pe = cs - _dt.timedelta(days=1)
            selected_week = cs.isoformat()
            if th:
                label = f"สัปดาห์ {dlabel(cs)} – {dlabel(ce)} (จ.–อา.)"
                comp_label = "สัปดาห์ก่อนหน้า (จ.–อา.)"
            else:
                label = f"Week of {dlabel(cs)} – {dlabel(ce)} (Mon–Sun)"
                comp_label = "previous week (Mon–Sun)"
        has_prev = ps.isoformat() >= mind
        # YoY window — 364 days back (exactly 52 weeks) for BOTH daily and weekly, so the
        # weekday stays aligned. Restaurant revenue is strongly weekday-seasonal, so a
        # same-calendar-date YoY would drift (e.g. Sat→Fri) and distort the comparison.
        # Dropped if it predates the data. Monthly compares the SAME CALENDAR month
        # last year (full month vs full month — the standard for monthly reporting;
        # weekday drift washes out over a whole month).
        if period == "daily":
            ys = ye = fdate - _dt.timedelta(days=364)
        elif period == "monthly":
            ys = cs.replace(year=cs.year - 1)
            ye = ys.replace(day=_cal.monthrange(ys.year, ys.month)[1])
        else:
            ys = cs - _dt.timedelta(days=364); ye = ce - _dt.timedelta(days=364)
        has_yoy = ys.isoformat() >= mind

        # ---- headline totals for an arbitrary [s,e] range -----------------
        def agg(s, e):
            r = con.execute(f"""
                SELECT ROUND(SUM(st.total_inc_vat)/1.07,2) rev,
                       COALESCE(SUM(st.customer_count),0)  cust,
                       COUNT(*)                            bills,
                       ROUND(COALESCE(SUM(st.discount),0),2) disc
                FROM sales_transaction st JOIN branches b ON b.branch_id=st.branch_id
                WHERE {where} AND date(st.sold_at) BETWEEN :s AND :e
            """, {**params, "s": s.isoformat(), "e": e.isoformat()}).fetchone()
            rev = r["rev"] or 0; cust = r["cust"] or 0; bills = r["bills"] or 0
            return {"revenue": rev, "customers": cust, "bills": bills, "discount": r["disc"] or 0,
                    "rev_per_head": round(rev/cust, 1) if cust else None,
                    "avg_ticket":   round(rev/bills, 1) if bills else None}

        cur  = agg(cs, ce)
        prev = agg(ps, pe) if has_prev else None
        headline = dict(cur)
        headline["discount_pct"] = round(cur["discount"]/cur["revenue"]*100, 1) if cur["revenue"] else None
        if prev:
            headline["prev"] = prev
            headline["rev_wow_pct"]   = pct(cur["revenue"], prev["revenue"])
            headline["cust_wow_pct"]  = pct(cur["customers"], prev["customers"])
            headline["bills_wow_pct"] = pct(cur["bills"], prev["bills"])
            headline["rph_wow_pct"]   = pct(cur["rev_per_head"] or 0, prev["rev_per_head"]) if prev["rev_per_head"] else None
            headline["ticket_wow_pct"]= pct(cur["avg_ticket"] or 0, prev["avg_ticket"]) if prev["avg_ticket"] else None
        yoy = agg(ys, ye) if has_yoy else None
        if yoy:
            headline["yoy"] = yoy
            headline["rev_yoy_pct"]  = pct(cur["revenue"], yoy["revenue"])
            headline["cust_yoy_pct"] = pct(cur["customers"], yoy["customers"])
            headline["rph_yoy_pct"]  = pct(cur["rev_per_head"] or 0, yoy["rev_per_head"]) if yoy["rev_per_head"] else None

        # ---- branch league table -----------------------------------------
        def by_branch(s, e):
            rows = con.execute(f"""
                SELECT b.name branch,
                       ROUND(SUM(st.total_inc_vat)/1.07,2) rev,
                       COALESCE(SUM(st.customer_count),0)  cust,
                       COUNT(*)                            bills
                FROM sales_transaction st JOIN branches b ON b.branch_id=st.branch_id
                WHERE {where} AND date(st.sold_at) BETWEEN :s AND :e
                GROUP BY b.name""", {**params, "s": s.isoformat(), "e": e.isoformat()}).fetchall()
            return {r["branch"]: r for r in rows}
        cur_b  = by_branch(cs, ce)
        prev_b = by_branch(ps, pe) if has_prev else {}
        tot_rev = sum((r["rev"] or 0) for r in cur_b.values()) or 0
        branches = []
        for nm, r in cur_b.items():
            rev = r["rev"] or 0; cust = r["cust"] or 0
            p = prev_b.get(nm)
            branches.append({
                "branch": nm, "revenue": rev, "customers": cust, "bills": r["bills"],
                "rev_per_head": round(rev/cust, 1) if cust else None,
                "share_pct": round(rev/tot_rev*100, 1) if tot_rev else None,
                "wow_pct": pct(rev, p["rev"] or 0) if p else None,
            })
        branches.sort(key=lambda x: -x["revenue"])
        prev_rank_map = {nm: i + 1 for i, (nm, _) in
                         enumerate(sorted(prev_b.items(), key=lambda kv: -(kv[1]["rev"] or 0)))}
        for i, b in enumerate(branches):
            b["rank"] = i + 1
            pr = prev_rank_map.get(b["branch"])
            b["rank_change"] = (pr - (i + 1)) if pr else None  # positive = moved up

        # ---- money-leak radar over the period (+ last-period comparison) ----
        # leak_total/cash_heavy_total take an explicit [s,e] window so the SAME signal
        # can be measured for the current period AND the comparison period (last week /
        # same weekday last week) — the §6 cards then show a WoW delta.
        lk = "((st.total_inc_vat - COALESCE(st.service_charge,0))/1.07 + st.discount)"
        def leak_total(extra, s, e):
            r = con.execute(f"""
                SELECT COUNT(*) bills, ROUND(COALESCE(SUM(st.discount),0)) baht
                FROM sales_transaction st JOIN branches b ON b.branch_id=st.branch_id
                WHERE {where} AND date(st.sold_at) BETWEEN :s AND :e {extra}""",
                {**params, "s": s.isoformat(), "e": e.isoformat()}).fetchone()
            return {"bills": r["bills"] or 0, "baht": r["baht"] or 0}
        def leak_branches(extra, by="baht"):
            rows = con.execute(f"""
                SELECT b.name branch, COUNT(*) bills, ROUND(COALESCE(SUM(st.discount),0)) baht
                FROM sales_transaction st JOIN branches b ON b.branch_id=st.branch_id
                WHERE {where} AND date(st.sold_at) BETWEEN :s AND :e {extra}
                GROUP BY b.name ORDER BY {by} DESC LIMIT 3""",
                {**params, "s": cs.isoformat(), "e": ce.isoformat()}).fetchall()
            return [dict(r) for r in rows]
        # cash-heavy: of the period's heavy-discount bills (a SMALL set), how many were
        # paid mostly in CASH. Drive FROM the heavy bills and pick each bill's dominant
        # tender with an indexed (branch_id, receipt_no, amount) lookup — NOT a window
        # over the entire sale_payment table (that scan made this the slowest query in
        # the report). Guards a missing sale_payment.
        def cash_heavy_total(s, e, with_branches=False):
            try:
                dom_code = ("(SELECT CASE WHEN UPPER(sp.payment_code)='CASH' THEN 'Cash' ELSE sp.payment_code END "
                            "FROM sale_payment sp WHERE sp.branch_id=h.branch_id AND sp.receipt_no=h.receipt_no "
                            "ORDER BY sp.amount DESC LIMIT 1)")
                heavy_sub = (f"SELECT st.branch_id, st.receipt_no, st.discount, b.name AS branch "
                             f"FROM sales_transaction st JOIN branches b ON b.branch_id=st.branch_id "
                             f"WHERE {where} AND date(st.sold_at) BETWEEN :s AND :e "
                             f"AND st.discount>0 AND {lk}>0 AND st.discount*1.0/{lk} >= 0.30")
                lpe = {**params, "s": s.isoformat(), "e": e.isoformat()}
                cr = con.execute(f"""
                    SELECT COUNT(*) bills, ROUND(COALESCE(SUM(discount),0)) baht
                    FROM (SELECT discount, {dom_code} dc FROM ({heavy_sub}) h)
                    WHERE dc='Cash'""", lpe).fetchone()
                out = {"bills": cr["bills"] or 0, "baht": cr["baht"] or 0}
                if with_branches:
                    cb = con.execute(f"""
                        SELECT branch, COUNT(*) bills, ROUND(COALESCE(SUM(discount),0)) baht
                        FROM (SELECT branch, discount, {dom_code} dc FROM ({heavy_sub}) h)
                        WHERE dc='Cash' GROUP BY branch ORDER BY baht DESC LIMIT 3""", lpe).fetchall()
                    out["by_branch"] = [dict(r) for r in cb]
                return out
            except Exception:
                return {"bills": 0, "baht": 0, "by_branch": [], "unavailable": True}
        # WoW helper — attaches prev figures + % deltas onto a leak card.
        def _leak_wow(cur_o, prev_o):
            return {"prev_bills": prev_o["bills"], "prev_baht": prev_o["baht"],
                    "bills_wow": pct(cur_o["bills"], prev_o["bills"]),
                    "baht_wow":  pct(cur_o["baht"],  prev_o["baht"])}

        heavy_extra   = f"AND st.discount>0 AND {lk}>0 AND st.discount*1.0/{lk} >= 0.30"
        reprint_extra = "AND st.discount>0 AND COALESCE(st.print_times,0)>1"
        zero_extra    = f"AND st.discount>0 AND {lk} <= 0"
        all_extra     = "AND st.discount>0"
        heavy   = {**leak_total(heavy_extra, cs, ce),   "by_branch": leak_branches(heavy_extra)}
        reprint = {**leak_total(reprint_extra, cs, ce), "by_branch": leak_branches(reprint_extra, "bills")}
        zero    = {**leak_total(zero_extra, cs, ce),    "by_branch": leak_branches(zero_extra)}
        cash_heavy = cash_heavy_total(cs, ce, with_branches=True)
        ltot       = leak_total(all_extra, cs, ce)
        if has_prev:
            heavy.update(  _leak_wow(heavy,   leak_total(heavy_extra,   ps, pe)))
            reprint.update(_leak_wow(reprint, leak_total(reprint_extra, ps, pe)))
            zero.update(   _leak_wow(zero,    leak_total(zero_extra,    ps, pe)))
            ltot.update(   _leak_wow(ltot,    leak_total(all_extra,     ps, pe)))
            ch_prev = cash_heavy_total(ps, pe)
            if not cash_heavy.get("unavailable") and not ch_prev.get("unavailable"):
                cash_heavy.update(_leak_wow(cash_heavy, ch_prev))
        # ALL discounted bills in the period (every branch in scope) — the reconciling
        # total so the radar's abnormal-only subsets read against the full figure and
        # tie out with the Discounts menu (both = SUM(Sale.Discount) over the same scope).
        leak = {"heavy_discount": heavy, "cash_heavy": cash_heavy,
                "reprinted": reprint, "zero_gross": zero, "total": ltot}
        # Per-MASTER-branch discount follow-up: amount + % of that branch's ex-VAT sales
        # + bill count, for the period. Master (company-owned) stores are the owner's own
        # money leak — franchise discounts are the franchisee's cost — so this is the line
        # to action on. Honours the report's branch/period scope; always master-only.
        mb_rows = con.execute(f"""
            SELECT b.name branch,
                   ROUND(SUM(st.total_inc_vat)/1.07) sales,
                   ROUND(COALESCE(SUM(CASE WHEN st.discount>0 THEN st.discount END),0)) disc,
                   SUM(CASE WHEN st.discount>0 THEN 1 ELSE 0 END) bills
            FROM sales_transaction st JOIN branches b ON b.branch_id=st.branch_id
            WHERE {where} AND date(st.sold_at) BETWEEN :s AND :e AND COALESCE(b.branch_type,'master')='master'
            GROUP BY b.name HAVING SUM(st.total_inc_vat) > 0
            ORDER BY disc DESC""", {**params, "s": cs.isoformat(), "e": ce.isoformat()}).fetchall()
        leak["by_master_branch"] = [
            {"branch": r["branch"], "sales": r["sales"] or 0, "disc": r["disc"] or 0,
             "bills": r["bills"] or 0,
             "pct": round((r["disc"] or 0) / r["sales"] * 100, 1) if r["sales"] else None}
            for r in mb_rows]

        # ---- menu: food COST PER HEAD by dish + biggest per-head cost movers ----
        # Same idea as วิเคราะห์เมนู / the Daily-Brief menu-movers, but on the COST side:
        #   cost/head = (dish COGS over the period) / (covers in scope), current vs the
        #   comparison period. COGS uses the app's effective unit cost (sku_effective_cost
        #   → non-zero sku_cost fallback) — the same basis as _unified_cogs_expr.
        food_sub = (f"(SELECT sk.sku_id FROM skus sk JOIN categories c "
                    f"ON c.category_id=sk.category_id WHERE {_FOOD_CATEGORY_FILTER})")
        sb = ["b.name!='ALL'"]; sp: dict = {}
        if branch:
            sb.append("b.name=:branch"); sp["branch"] = branch
        _group_filter(sb, sp, group)
        sw = " AND ".join(sb)
        _eff_cost = ("COALESCE((SELECT sec.recipe_cost FROM sku_effective_cost sec WHERE sec.sku_id=d.sku_id),"
                     " (SELECT NULLIF(sc.unit_cost,0) FROM sku_cost sc WHERE sc.sku_id=d.sku_id), 0)")
        def menu_cost_map(s, e):
            rows = con.execute(f"""
                SELECT d.sku_id, s.description, s.sku_code,
                       SUM(d.qty) qty, SUM(d.qty * {_eff_cost}) cost
                FROM sales_sku_daily d JOIN branches b ON b.branch_id=d.branch_id
                JOIN skus s ON s.sku_id=d.sku_id
                WHERE {sw} AND d.day BETWEEN :s AND :e AND d.sku_id IN {food_sub}
                GROUP BY d.sku_id""", {**sp, "s": s.isoformat(), "e": e.isoformat()}).fetchall()
            return {r["sku_id"]: r for r in rows}
        def clean(nm):
            return (nm or "").replace("(A) ", "").replace("(B) ", "").strip()
        covers_cur  = cur["customers"] or 0
        covers_prev = (prev["customers"] or 0) if prev else 0
        try:
            cur_m  = menu_cost_map(cs, ce)
            prev_m = menu_cost_map(ps, pe) if has_prev else {}
        except Exception:
            cur_m, prev_m = {}, {}
        def cph(cost, covers): return round(cost / covers, 2) if covers else None
        cmp_ok = has_prev and covers_prev > 0      # only show deltas vs a period that had covers
        menu_rows = []
        for sid, r in cur_m.items():
            cost_cur  = r["cost"] or 0
            cost_prev = (prev_m[sid]["cost"] if sid in prev_m else 0) or 0
            c_cur  = cph(cost_cur, covers_cur)
            c_prev = cph(cost_prev, covers_prev)
            if not c_cur and not c_prev:
                continue
            menu_rows.append({
                "menu": clean(r["description"] or r["sku_code"]),
                "qty": round(r["qty"] or 0), "cost": round(cost_cur),
                "cost_per_head": c_cur,
                "cost_per_head_prev": c_prev if cmp_ok else None,
                "delta": round((c_cur or 0) - (c_prev or 0), 2) if cmp_ok else None,
                "pct": pct(c_cur or 0, c_prev or 0) if cmp_ok else None,
            })
        # dishes that carried cost last period but none now → cost/head fell to 0
        if cmp_ok:
            for sid, r in prev_m.items():
                if sid in cur_m:
                    continue
                c_prev = cph(r["cost"] or 0, covers_prev)
                if not c_prev:
                    continue
                menu_rows.append({
                    "menu": clean(r["description"] or r["sku_code"]),
                    "qty": 0, "cost": 0, "cost_per_head": 0.0,
                    "cost_per_head_prev": c_prev,
                    "delta": round(-c_prev, 2), "pct": -100.0,
                })
        cost_top   = sorted([m for m in menu_rows if (m["cost_per_head"] or 0) > 0],
                            key=lambda x: -(x["cost_per_head"] or 0))[:12]
        cost_up    = sorted([m for m in menu_rows if m["delta"] and m["delta"] > 0],
                            key=lambda x: -x["delta"])[:12]
        cost_down  = sorted([m for m in menu_rows if m["delta"] and m["delta"] < 0],
                            key=lambda x: x["delta"])[:12]
        tot_cur_cost  = sum((r["cost"] or 0) for r in cur_m.values())
        tot_prev_cost = sum((r["cost"] or 0) for r in prev_m.values()) if has_prev else 0
        total_cph      = cph(tot_cur_cost, covers_cur)
        total_cph_prev = cph(tot_prev_cost, covers_prev) if cmp_ok else None
        menu_out = {
            "basis": "cost_per_head",
            "covers": covers_cur, "covers_prev": covers_prev if has_prev else None,
            "total_cph": total_cph, "total_cph_prev": total_cph_prev,
            "total_cph_delta": (round((total_cph or 0) - (total_cph_prev or 0), 2)
                                if (total_cph is not None and total_cph_prev is not None) else None),
            "top": cost_top, "risers": cost_up, "savers": cost_down,
        }

        # ---- buffet price-tier split (tier-dimensioned menu_fact_*; optional) ----
        # Same current-vs-comparison windows as the headline. None (section hidden)
        # when the fact tables are missing/old schema or the window predates their
        # coverage. pax here counts BUFFET bills only (menu_fact universe), so the
        # total can sit slightly under the headline covers.
        tier_out = None
        try:
            if con.execute("SELECT 1 FROM pragma_table_info('menu_fact_total') "
                           "WHERE name='tier'").fetchone():
                t_bids = [r[0] for r in con.execute(
                    f"SELECT b.branch_id FROM branches b WHERE {sw}", sp)]
                t_ph = ",".join("?" * len(t_bids))

                def tier_sum(s, e):
                    out = {}
                    for t, nb2, px, cg, rv in con.execute(
                            f"""SELECT tier, SUM(bills), SUM(pax), SUM(cogs), SUM(rev)
                                FROM menu_fact_total
                                WHERE day BETWEEN ? AND ? AND branch_id IN ({t_ph})
                                GROUP BY tier""",
                            [s.isoformat(), e.isoformat()] + t_bids):
                        out[str(t or "promo")] = {"bills": nb2 or 0, "pax": px or 0.0,
                                                  "cogs": cg or 0.0, "rev": rv or 0.0}
                    return out
                TC = tier_sum(cs, ce)
                TP = tier_sum(ps, pe) if has_prev else {}
                pax_c = sum(v["pax"] for v in TC.values())
                pax_p = sum(v["pax"] for v in TP.values())
                if pax_c > 0:
                    TLBL = ({"promo": "โปรฯ/ราคาอื่น", "child": "เด็กล้วน/อื่นๆ"} if th else
                            {"promo": "Promo/other price", "child": "Child-only/other"})
                    rows_t = []
                    for t in _PT_ORDER:
                        vc0 = TC.get(t); vp = TP.get(t)
                        if not vc0 and not vp:
                            continue
                        vc = vc0 or {"bills": 0, "pax": 0.0, "cogs": 0.0, "rev": 0.0}
                        pxc = vc["pax"]; pxp = (vp or {}).get("pax", 0.0)
                        rows_t.append({
                            "tier": t, "label": TLBL.get(t, f"฿{int(t):,}" if t.isdigit() else t),
                            "pax": round(pxc), "pax_prev": round(pxp) if has_prev else None,
                            "d_pax_pct": pct(pxc, pxp) if (has_prev and pxp) else None,
                            "share": round(pxc / pax_c * 100, 1),
                            "share_prev": round(pxp / pax_p * 100, 1) if pax_p else None,
                            "rev_head": round(vc["rev"] / pxc, 2) if pxc else None,
                            "cogs_head": round(vc["cogs"] / pxc, 2) if pxc else None,
                            "cogs_head_prev": round(vp["cogs"] / pxp, 2) if (vp and pxp) else None,
                            "gp_head": round((vc["rev"] - vc["cogs"]) / pxc, 2) if pxc else None,
                            "food_pct": round(vc["cogs"] / vc["rev"] * 100, 1) if vc["rev"] else None,
                        })
                    # mix effect on rev/head: current tier mix priced at the comparison
                    # period's per-tier rev/head, vs its blended rev/head — isolates
                    # "customers moved tier" from "tiers themselves changed".
                    mix_eff = None
                    rev_p = sum(v["rev"] for v in TP.values())
                    if pax_c and pax_p and rev_p:
                        at_prev = sum((TC[t]["pax"] / pax_c) * (TP[t]["rev"] / TP[t]["pax"])
                                      for t in TC if TP.get(t, {}).get("pax", 0) > 0)
                        mix_eff = round(at_prev - rev_p / pax_p, 2)
                    tier_out = {"rows": rows_t, "pax": round(pax_c),
                                "pax_prev": round(pax_p) if has_prev else None,
                                "mix_effect": mix_eff}

                    # cost drivers per mainstream tier: which ingredient groups /
                    # single menus push each tier's cost/head up — turns the Food%
                    # column into an actionable "because guests eat X". Computes a
                    # payload for EVERY mainstream tier (share>=5%) so the UI can
                    # switch/compare; `default_tier` = worst food%. Reads the
                    # tier-dimensioned menu_fact_day (ent LIKE 'g:%' = ingredient
                    # group, 's:%' = single menu). Optional: wrapped so a driver
                    # failure never nukes the tier table above.
                    try:
                        cand = [r for r in rows_t if r["tier"].isdigit()
                                and (r["share"] or 0) >= 5 and r["food_pct"] is not None
                                and r["pax"]]
                        if cand:
                            lblmap = {e: (l or e) for e, l in con.execute(
                                "SELECT ent, label FROM menu_fact_meta")}

                            def _drv(kind_like, tier, pax):
                                d = {}
                                if not pax:
                                    return d
                                for ent, cg, q in con.execute(
                                        f"""SELECT ent, SUM(ent_cogs), SUM(qty)
                                            FROM menu_fact_day
                                            WHERE day BETWEEN ? AND ? AND branch_id IN ({t_ph})
                                              AND tier=? AND ent LIKE ? GROUP BY ent""",
                                        [cs.isoformat(), ce.isoformat()] + t_bids + [tier, kind_like]):
                                    d[ent] = ((cg or 0.0) / pax, (q or 0.0) / pax * 100)
                                return d

                            def _build(frow):
                                ft = frow["tier"]; ftpax = TC[ft]["pax"]
                                fch = frow.get("cogs_head") or 0.0
                                lower = [x for x in cand if int(x["tier"]) < int(ft)]
                                ref = max(lower, key=lambda x: int(x["tier"])) if lower else None
                                rt = ref["tier"] if ref else None
                                rtpax = TC[rt]["pax"] if rt else 0.0
                                gF = _drv("g:%", ft, ftpax)
                                gR = _drv("g:%", rt, rtpax) if rt else {}
                                sh = lambda ch: (round(ch / fch * 100, 1) if fch else None)
                                grp = [{"label": lblmap.get(e, e), "cogs_head": round(v[0], 1),
                                        "share_pct": sh(v[0]),
                                        "vs_ref": (round(v[0] - gR.get(e, (0.0, 0.0))[0], 1) if rt else None)}
                                       for e, v in sorted(gF.items(), key=lambda kv: -kv[1][0])[:6]
                                       if v[0] >= 1]
                                mF = _drv("s:%", ft, ftpax)
                                mnu = [{"label": lblmap.get(e, e), "cogs_head": round(v[0], 1),
                                        "share_pct": sh(v[0]), "qty_100": round(v[1], 1)}
                                       for e, v in sorted(mF.items(), key=lambda kv: -kv[1][0])[:6]
                                       if v[0] >= 1]
                                if not (grp or mnu):
                                    return None
                                return {"focus_tier": ft, "focus_label": frow["label"],
                                        "focus_food_pct": frow["food_pct"],
                                        "focus_cogs_head": round(fch, 1),
                                        "ref_tier": rt, "ref_label": (ref["label"] if ref else None),
                                        "groups": grp, "menus": mnu}

                            by_tier = {}
                            for frow in sorted(cand, key=lambda r: int(r["tier"])):
                                payload = _build(frow)
                                if payload:
                                    by_tier[frow["tier"]] = payload
                            if by_tier:
                                worst = max((r for r in cand if r["tier"] in by_tier),
                                            key=lambda r: r["food_pct"])
                                tier_out["drivers"] = {
                                    "default_tier": worst["tier"],
                                    "tier_order": sorted(by_tier, key=lambda t: int(t)),
                                    "by_tier": by_tier}
                    except Exception:
                        pass
        except Exception:
            tier_out = None

        # ---- gross profit for THIS period (revenue − period food COGS, BoM basis) ----
        gp_cur = round(cur["revenue"] - tot_cur_cost, 2)
        gp_pct = round(gp_cur / cur["revenue"] * 100, 1) if cur["revenue"] else None
        gp_prev_pct = (round((prev["revenue"] - tot_prev_cost) / prev["revenue"] * 100, 1)
                       if (cmp_ok and prev and prev["revenue"]) else None)
        gp = {
            "revenue": cur["revenue"], "food_cost": round(tot_cur_cost),
            "gp": gp_cur, "gp_pct": gp_pct,
            "gp_per_head": round(gp_cur / cur["customers"], 1) if cur["customers"] else None,
            "gp_pct_prev": gp_prev_pct,
            "gp_pct_delta_pp": round(gp_pct - gp_prev_pct, 1) if (gp_pct is not None and gp_prev_pct is not None) else None,
        }

        # ---- food cost % (BoM) + month pace, anchored on the period's month ----
        cur_month = ref_anchor.strftime("%Y-%m")
        mb = ["b.name!='ALL'", "s.period_month=:month"]; mp: dict = {"month": cur_month}
        if branch:
            mb.append("b.name=:branch"); mp["branch"] = branch
        _group_filter(mb, mp, group)
        mw = " AND ".join(mb)
        bom_row = con.execute(f"""
            SELECT ROUND(SUM({_cogs_case('s','sk','c')}),2) cost
            FROM sales_sku_monthly s JOIN skus sk ON sk.sku_id=s.sku_id
            LEFT JOIN sku_cost c ON c.sku_id=s.sku_id
            JOIN branches b ON b.branch_id=s.branch_id
            WHERE {mw}""", mp).fetchone()
        # sales_sku_monthly is not populated for the in-progress (live-synced-only)
        # month, so the month food-cost % would go blank for a current-month report.
        # Fall back to sales_sku_daily (same tier-safe COGS expr; a wrapped subquery
        # supplies the `source` column the expr expects). [[sync_no_month_summary]]
        if not (bom_row and bom_row["cost"]):
            mw_daily = mw.replace("s.period_month=:month", "strftime('%Y-%m', s.day)=:month")
            bom_row = con.execute(f"""
                SELECT ROUND(SUM({_cogs_case('s','sk','c')}),2) cost
                FROM (SELECT *, 'Easy' AS source FROM sales_sku_daily) s
                JOIN skus sk ON sk.sku_id=s.sku_id
                LEFT JOIN sku_cost c ON c.sku_id=s.sku_id
                JOIN branches b ON b.branch_id=s.branch_id
                WHERE {mw_daily}""", mp).fetchone()
        mtd_row = con.execute(f"""
            SELECT ROUND(SUM(st.total_inc_vat)/1.07,2) rev,
                   COUNT(DISTINCT date(st.sold_at))    days
            FROM sales_transaction st JOIN branches b ON b.branch_id=st.branch_id
            WHERE {where} AND strftime('%Y-%m', st.sold_at)=:month""",
            {**params, "month": cur_month}).fetchone()
        mtd_rev  = mtd_row["rev"] or 0
        mtd_days = mtd_row["days"] or 0
        bom_cost = bom_row["cost"] if bom_row else None
        food_cost_pct = round(bom_cost/mtd_rev*100, 1) if bom_cost and mtd_rev else None
        days_total = _cal.monthrange(ref_anchor.year, ref_anchor.month)[1]
        # MTD up to the latest data for the current month; a full (past) month otherwise.
        days_elapsed = latest_day.day if cur_month == maxd[:7] else days_total
        month_complete = days_elapsed >= days_total
        projected = round(mtd_rev/days_elapsed*days_total, 2) if (mtd_rev and days_elapsed) else None
        tb = ["bt.period_month=:month", "b.name!='ALL'"]; tp: dict = {"month": cur_month}
        if branch:
            tb.append("b.name=:branch"); tp["branch"] = branch
        _group_filter(tb, tp, group)
        tw = " AND ".join(tb)
        tgt_row = con.execute(f"""
            SELECT COALESCE(SUM(bt.revenue_target),0) tgt
            FROM branch_targets bt JOIN branches b ON b.branch_id=bt.branch_id
            WHERE {tw}""", tp).fetchone()
        target = tgt_row["tgt"] or 0
        pace = {
            "month": cur_month, "mtd_revenue": mtd_rev, "mtd_days": mtd_days,
            "days_elapsed": days_elapsed, "days_total": days_total, "month_complete": month_complete,
            "projected": projected, "target": target or None,
            "proj_vs_target_pct": round(projected/target*100, 1) if (projected and target) else None,
            "food_cost_pct": food_cost_pct, "bom_cost_mtd": bom_cost,
        }
        # `prev_month`: previous calendar month — full total, the SAME-elapsed-days MTD
        # (fair MTD-vs-MTD when the current month is partial) and its BoM food-cost %.
        pm_first   = ref_anchor.replace(day=1) - _dt.timedelta(days=1)
        prev_month = pm_first.strftime("%Y-%m")
        pm_full = con.execute(f"""
            SELECT ROUND(SUM(st.total_inc_vat)/1.07,2) rev
            FROM sales_transaction st JOIN branches b ON b.branch_id=st.branch_id
            WHERE {where} AND strftime('%Y-%m', st.sold_at)=:month""",
            {**params, "month": prev_month}).fetchone()
        pm_mtd = con.execute(f"""
            SELECT ROUND(SUM(st.total_inc_vat)/1.07,2) rev
            FROM sales_transaction st JOIN branches b ON b.branch_id=st.branch_id
            WHERE {where} AND strftime('%Y-%m', st.sold_at)=:month
              AND CAST(strftime('%d', st.sold_at) AS INT) <= :d""",
            {**params, "month": prev_month, "d": days_elapsed}).fetchone()
        pm_bom = con.execute(f"""
            SELECT ROUND(SUM({_cogs_case('s','sk','c')}),2) cost
            FROM sales_sku_monthly s JOIN skus sk ON sk.sku_id=s.sku_id
            LEFT JOIN sku_cost c ON c.sku_id=s.sku_id
            JOIN branches b ON b.branch_id=s.branch_id
            WHERE {mw}""", {**mp, "month": prev_month}).fetchone()
        pm_rev_full = (pm_full["rev"] or 0) if pm_full else 0
        pm_rev_mtd  = (pm_mtd["rev"] or 0) if pm_mtd else 0
        pm_cost     = pm_bom["cost"] if pm_bom else None
        # Per-weekday average revenue of the previous month. A raw same-elapsed-days
        # MoM misleads early in the month (1-2 Jul = Wed+Thu vs 1-2 Jun = holiday
        # Mon+Tue read as "-44%"), and the flat mtd/days*days_total projection
        # undershoots until the first weekend is in — so while the month is partial,
        # both the MoM baseline and the projection are weighted by weekday instead.
        pm_dow = con.execute(f"""
            SELECT CAST(strftime('%w', st.sold_at) AS INT) wd,
                   SUM(st.total_inc_vat)/1.07 rev,
                   COUNT(DISTINCT date(st.sold_at)) days
            FROM sales_transaction st JOIN branches b ON b.branch_id=st.branch_id
            WHERE {where} AND strftime('%Y-%m', st.sold_at)=:month
            GROUP BY wd""", {**params, "month": prev_month}).fetchall()
        dow_avg = {r["wd"]: (r["rev"] or 0) / r["days"] for r in pm_dow if r["days"]}
        dow_fallback = sum(dow_avg.values()) / len(dow_avg) if dow_avg else 0

        # ---- §3 weekly bars (this month) + last-month comparison --------------
        # `weekly`: ex-VAT revenue per Mon–Sun week WITHIN the period's month (each bar =
        # that week's in-month revenue), for a small weekly trend bar in §3. Each bar's
        # comparison (`fair_prev`) is last month's weekday-matched total for the EXACT
        # calendar days summed into that bar — not a flat full-week average — so an
        # in-progress week (e.g. only Wed–Sun so far) is never judged against a 7-day norm.
        mon_e3 = "date(st.sold_at, '-' || ((CAST(strftime('%w', st.sold_at) AS INT)+6)%7) || ' days')"
        wk_rows = con.execute(f"""
            SELECT {mon_e3} k, ROUND(SUM(st.total_inc_vat)/1.07) rev
            FROM sales_transaction st JOIN branches b ON b.branch_id=st.branch_id
            WHERE {where} AND strftime('%Y-%m', st.sold_at)=:month
            GROUP BY k ORDER BY k""", {**params, "month": cur_month}).fetchall()
        month_first = ref_anchor.replace(day=1)
        month_last  = ref_anchor.replace(day=days_total)
        pace["weekly"] = []
        for r in wk_rows:
            wk_start = _dt.date.fromisoformat(r["k"])
            d0 = max(wk_start, month_first)
            d1 = min(wk_start + _dt.timedelta(days=6), month_last, latest_day)
            n_days = max(0, (d1 - d0).days + 1)
            fair_prev = sum(dow_avg.get(int((d0 + _dt.timedelta(days=i)).strftime("%w")), dow_fallback)
                            for i in range(n_days))
            pace["weekly"].append({
                "label": f"{wk_start.day} {MON[wk_start.month-1]}",
                "start": r["k"], "revenue": r["rev"] or 0,
                "fair_prev": round(fair_prev, 2), "days": n_days,
                "complete": n_days >= 7,
            })
        mom_baseline, mom_basis = pm_rev_mtd, "same_days"
        if not month_complete and dow_avg and days_elapsed:
            _wsum = lambda n: sum(
                dow_avg.get(int((month_first + _dt.timedelta(days=i)).strftime("%w")), dow_fallback)
                for i in range(n))
            w_elapsed = _wsum(days_elapsed)
            mom_baseline, mom_basis = round(w_elapsed, 2), "dow"
            if mtd_rev and w_elapsed:
                pace["projected"] = projected = round(mtd_rev / w_elapsed * _wsum(days_total), 2)
                pace["proj_vs_target_pct"] = round(projected/target*100, 1) if target else None
                pace["proj_method"] = "dow"
        pace["prev_month"] = {
            "month": prev_month, "revenue_full": pm_rev_full, "revenue_mtd": pm_rev_mtd,
            "baseline_mtd": mom_baseline,
            "food_cost_pct": round(pm_cost/pm_rev_full*100, 1) if (pm_cost and pm_rev_full) else None,
        }
        pace["mom_pct"]   = pct(mtd_rev, mom_baseline)  # MTD vs weekday-fair baseline (same-elapsed days once month is complete)
        pace["mom_basis"] = mom_basis
        pace["food_cost_pct_prev"] = pace["prev_month"]["food_cost_pct"]

        # ---- trailing trend (8 periods) — ONE grouped query, not 8 round-trips ----
        trend = []
        if period == "daily":
            start = cs - _dt.timedelta(days=7)
            rows = con.execute(f"""
                SELECT date(st.sold_at) k, ROUND(SUM(st.total_inc_vat)/1.07,2) rev
                FROM sales_transaction st JOIN branches b ON b.branch_id=st.branch_id
                WHERE {where} AND date(st.sold_at) BETWEEN :s AND :e
                GROUP BY k""", {**params, "s": start.isoformat(), "e": cs.isoformat()}).fetchall()
            rev_by = {r["k"]: r["rev"] or 0 for r in rows}
            for i in range(7, -1, -1):
                d = cs - _dt.timedelta(days=i)
                trend.append({"label": dlabel(d), "start": d.isoformat(),
                              "revenue": rev_by.get(d.isoformat(), 0)})
        elif period == "monthly":
            mlist = []                       # 8 calendar months ending at the selected one
            for i in range(7, -1, -1):
                mi = (cs.month - 1 - i) % 12 + 1
                yi = cs.year + (cs.month - 1 - i - (mi - 1)) // 12
                mlist.append((yi, mi))
            start = _dt.date(mlist[0][0], mlist[0][1], 1)
            rows = con.execute(f"""
                SELECT strftime('%Y-%m', st.sold_at) k, ROUND(SUM(st.total_inc_vat)/1.07,2) rev
                FROM sales_transaction st JOIN branches b ON b.branch_id=st.branch_id
                WHERE {where} AND date(st.sold_at) BETWEEN :s AND :e
                GROUP BY k""", {**params, "s": start.isoformat(), "e": ce.isoformat()}).fetchall()
            rev_by = {r["k"]: r["rev"] or 0 for r in rows}
            for yi, mi in mlist:
                k = f"{yi:04d}-{mi:02d}"
                trend.append({"label": f"{MON[mi-1]} {yi % 100:02d}", "start": f"{k}-01",
                              "revenue": rev_by.get(k, 0)})
        else:
            start = cs - _dt.timedelta(days=49)      # 8 full Mon–Sun weeks incl. the current one
            mon_e = "date(st.sold_at, '-' || ((CAST(strftime('%w', st.sold_at) AS INT)+6)%7) || ' days')"
            rows = con.execute(f"""
                SELECT {mon_e} k, ROUND(SUM(st.total_inc_vat)/1.07,2) rev
                FROM sales_transaction st JOIN branches b ON b.branch_id=st.branch_id
                WHERE {where} AND date(st.sold_at) BETWEEN :s AND :e
                GROUP BY k""", {**params, "s": start.isoformat(), "e": ce.isoformat()}).fetchall()
            rev_by = {r["k"]: r["rev"] or 0 for r in rows}
            for i in range(7, -1, -1):
                d = cs - _dt.timedelta(days=7 * i)
                sun = d + _dt.timedelta(days=6)   # label with week-end Sunday (matches report header)
                trend.append({"label": f"{sun.day} {MON[sun.month-1]}", "start": d.isoformat(),
                              "revenue": rev_by.get(d.isoformat(), 0)})

        # ---- peak hours & dayparts (revenue + covers by hour over the period) ----
        hr_rows = con.execute(f"""
            SELECT CAST(strftime('%H', st.sold_at) AS INT) hr,
                   ROUND(SUM(st.total_inc_vat)/1.07,2) rev,
                   COALESCE(SUM(st.customer_count),0) cust, COUNT(*) bills
            FROM sales_transaction st JOIN branches b ON b.branch_id=st.branch_id
            WHERE {where} AND date(st.sold_at) BETWEEN :s AND :e
            GROUP BY hr ORDER BY hr""", {**params, "s": cs.isoformat(), "e": ce.isoformat()}).fetchall()
        hourly = [{"hour": r["hr"], "revenue": r["rev"] or 0, "covers": r["cust"] or 0, "bills": r["bills"]} for r in hr_rows]
        def _daypart(hh):
            if 11 <= hh <= 14: return "Lunch"
            if 17 <= hh <= 22: return "Dinner"
            return "Other"
        dp: dict = {}
        for r in hourly:
            d2 = dp.setdefault(_daypart(r["hour"]), {"name": _daypart(r["hour"]), "revenue": 0, "covers": 0})
            d2["revenue"] += r["revenue"]; d2["covers"] += r["covers"]
        tot_hr_rev = sum(r["revenue"] for r in hourly) or 0
        dayparts = []
        for k in ("Lunch", "Dinner", "Other"):
            if k in dp:
                d2 = dp[k]
                d2["pct"] = round(d2["revenue"]/tot_hr_rev*100, 1) if tot_hr_rev else None
                d2["revenue"] = round(d2["revenue"]); dayparts.append(d2)
        ph = max(hourly, key=lambda r: r["revenue"], default=None)
        peak = {"hourly": hourly, "dayparts": dayparts, "total_rev": round(tot_hr_rev),
                "peak_hour": ({"hour": ph["hour"], "revenue": round(ph["revenue"])} if ph and ph["revenue"] else None)}
        # last-period comparison — daypart revenue, scope total and the peak hour, so §8
        # can show a WoW delta against the same weekday last week / the previous week.
        if has_prev:
            ph_rows = con.execute(f"""
                SELECT CAST(strftime('%H', st.sold_at) AS INT) hr,
                       ROUND(SUM(st.total_inc_vat)/1.07,2) rev
                FROM sales_transaction st JOIN branches b ON b.branch_id=st.branch_id
                WHERE {where} AND date(st.sold_at) BETWEEN :s AND :e
                GROUP BY hr""", {**params, "s": ps.isoformat(), "e": pe.isoformat()}).fetchall()
            pdp: dict = {}
            for r in ph_rows:
                nm = _daypart(r["hr"]); pdp[nm] = pdp.get(nm, 0) + (r["rev"] or 0)
            pphr = max(ph_rows, key=lambda r: r["rev"] or 0, default=None)
            peak["prev"] = {
                "dayparts":  {k: round(v) for k, v in pdp.items()},
                "total_rev": round(sum((r["rev"] or 0) for r in ph_rows)),
                "peak_hour": ({"hour": pphr["hr"], "revenue": round(pphr["rev"])} if pphr and pphr["rev"] else None),
            }
        if period != "daily":      # which day of the week drove the week
            dow_rows = con.execute(f"""
                SELECT CAST(strftime('%w', st.sold_at) AS INT) w,
                       ROUND(SUM(st.total_inc_vat)/1.07,2) rev, COALESCE(SUM(st.customer_count),0) cust
                FROM sales_transaction st JOIN branches b ON b.branch_id=st.branch_id
                WHERE {where} AND date(st.sold_at) BETWEEN :s AND :e
                GROUP BY w""", {**params, "s": cs.isoformat(), "e": ce.isoformat()}).fetchall()
            dm = {r["w"]: r for r in dow_rows}
            by_day = []
            for i, nm in enumerate(["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]):
                r = dm.get((i + 1) % 7)      # %w: 0=Sun..6=Sat → Mon-first
                by_day.append({"dow": nm, "revenue": round(r["rev"] or 0) if r else 0,
                               "covers": (r["cust"] or 0) if r else 0})
            bd = max(by_day, key=lambda d: d["revenue"], default=None)
            peak["by_day"] = by_day
            peak["best_day"] = bd["dow"] if bd and bd["revenue"] else None

        # ---- table turnover & occupancy (pos_table_turn_day, day-grain) ----
        table = {"available": False}
        try:
            if con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='pos_table_turn_day'").fetchone():
                tt_base = ["b.name!='ALL'"]; tparams: dict = {"s": cs.isoformat(), "e": ce.isoformat()}
                if branch:
                    tt_base.append("b.name=:branch"); tparams["branch"] = branch
                _group_filter(tt_base, tparams, group)
                tw2 = " AND ".join(tt_base)
                tot = con.execute(f"""
                    SELECT COALESCE(SUM(t.tabs),0) tabs, COALESCE(SUM(t.customers),0) cust,
                           ROUND(AVG(NULLIF(t.avg_turn_min,0))) avg_turn
                    FROM pos_table_turn_day t JOIN branches b ON b.branch_id=t.branch_id
                    WHERE {tw2} AND t.day BETWEEN :s AND :e""", tparams).fetchone()
                brows = con.execute(f"""
                    SELECT b.name branch, COALESCE(SUM(t.tabs),0) tabs, COALESCE(SUM(t.customers),0) cust,
                           ROUND(AVG(NULLIF(t.avg_turn_min,0))) avg_turn
                    FROM pos_table_turn_day t JOIN branches b ON b.branch_id=t.branch_id
                    WHERE {tw2} AND t.day BETWEEN :s AND :e
                    GROUP BY b.name HAVING SUM(t.tabs)>0
                    ORDER BY SUM(t.customers)*1.0/SUM(t.tabs) DESC""", tparams).fetchall()
                tabs = tot["tabs"] or 0; tcust = tot["cust"] or 0
                if tabs:
                    table = {
                        "available": True, "tables": tabs, "covers": tcust,
                        "covers_per_table": round(tcust/tabs, 2), "avg_turn_min": tot["avg_turn"],
                        "by_branch": [{"branch": r["branch"], "tables": r["tabs"], "covers": r["cust"],
                                       "covers_per_table": round((r["cust"] or 0)/r["tabs"], 2) if r["tabs"] else None,
                                       "avg_turn_min": r["avg_turn"]} for r in brows][:12],
                    }
                    if has_prev:      # last-period turnover for the §9 WoW deltas
                        pt = con.execute(f"""
                            SELECT COALESCE(SUM(t.tabs),0) tabs, COALESCE(SUM(t.customers),0) cust,
                                   ROUND(AVG(NULLIF(t.avg_turn_min,0))) avg_turn
                            FROM pos_table_turn_day t JOIN branches b ON b.branch_id=t.branch_id
                            WHERE {tw2} AND t.day BETWEEN :ps AND :pe""",
                            {**tparams, "ps": ps.isoformat(), "pe": pe.isoformat()}).fetchone()
                        ptabs = pt["tabs"] or 0; pcust = pt["cust"] or 0
                        table["prev"] = {
                            "tables": ptabs, "covers": pcust,
                            "covers_per_table": round(pcust/ptabs, 2) if ptabs else None,
                            "avg_turn_min": pt["avg_turn"],
                        }
        except Exception:
            table = {"available": False}

        # ---- payment channels for the scope (how the branches got paid) ------
        # Tender mix over the period [cs, ce] from pos_payment_day: the scope total by
        # payment code (`channels`, each with % of scope total — defines the §10 matrix
        # column order) AND a per-branch breakdown (`by_branch`, each branch's own channel
        # mix). Powers §10, rendered for the MASTER group broken out per branch in the
        # all-branches split view. Daily table → degrades to unavailable if a pre-sync DB
        # lacks it.
        payment = {"available": False, "channels": [], "by_branch": [], "total": 0}
        try:
            if con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='pos_payment_day'").fetchone():
                pp_base = ["b.name!='ALL'"]; pparams: dict = {"s": cs.isoformat(), "e": ce.isoformat()}
                if branch:
                    pp_base.append("b.name=:branch"); pparams["branch"] = branch
                _group_filter(pp_base, pparams, group)
                pw = " AND ".join(pp_base)
                prows = con.execute(f"""
                    SELECT b.name branch, pp.payment_code code, ROUND(SUM(pp.amount)) amount, SUM(pp.txn_count) n
                    FROM pos_payment_day pp JOIN branches b ON b.branch_id=pp.branch_id
                    WHERE {pw} AND pp.day BETWEEN :s AND :e
                    GROUP BY b.name, pp.payment_code""", pparams).fetchall()
                ptot = sum((r["amount"] or 0) for r in prows)
                if ptot > 0:
                    agg: dict = {}; bdata: dict = {}
                    for r in prows:
                        code = r["code"] or "—"; amt = r["amount"] or 0
                        agg[code] = agg.get(code, 0) + amt
                        bd = bdata.setdefault(r["branch"], {"branch": r["branch"], "total": 0, "ch": {}})
                        bd["ch"][code] = {"amount": round(amt), "txns": r["n"] or 0}
                        bd["total"] += amt
                    channels = [{"code": c, "amount": round(a), "pct": round(a / ptot * 100, 1)}
                                for c, a in sorted(agg.items(), key=lambda kv: -kv[1])]
                    by_branch = []
                    for bd in bdata.values():
                        bt = bd["total"] or 1
                        chans = {c: {"amount": v["amount"], "txns": v["txns"], "pct": round(v["amount"] / bt * 100, 1)}
                                 for c, v in bd["ch"].items()}
                        by_branch.append({"branch": bd["branch"], "total": round(bd["total"]), "channels": chans})
                    by_branch.sort(key=lambda x: -x["total"])
                    payment = {"available": True, "total": round(ptot), "channels": channels, "by_branch": by_branch}
                    if has_prev:      # last-period tender mix for the §10 WoW deltas
                        pprev = con.execute(f"""
                            SELECT pp.payment_code code, ROUND(SUM(pp.amount)) amount
                            FROM pos_payment_day pp JOIN branches b ON b.branch_id=pp.branch_id
                            WHERE {pw} AND pp.day BETWEEN :ps AND :pe
                            GROUP BY pp.payment_code""",
                            {**pparams, "ps": ps.isoformat(), "pe": pe.isoformat()}).fetchall()
                        pby = {(r["code"] or "—"): (r["amount"] or 0) for r in pprev}
                        ptot_prev = sum(pby.values())
                        payment["prev"] = {
                            "total": round(ptot_prev),
                            "by_code": {k: round(v) for k, v in pby.items()},
                            "channels": {c["code"]: {"amount": round(pby.get(c["code"], 0)),
                                                     "pct": round(pby.get(c["code"], 0) / ptot_prev * 100, 1) if ptot_prev else None}
                                         for c in channels},
                        }
        except Exception:
            payment = {"available": False, "channels": [], "by_branch": [], "total": 0}

    # ---- auto-generated action items (localised, severity-ordered) -------
    pword = (("วัน" if period == "daily" else "เดือน" if period == "monthly" else "สัปดาห์") if th
             else ("day" if period == "daily" else "month" if period == "monthly" else "week"))
    actions = []
    if prev and headline.get("rev_wow_pct") is not None and headline["rev_wow_pct"] <= -10:
        actions.append({"sev": "high",
            "text": (f"ยอดขายรวมทั้งเครือลดลง {abs(headline['rev_wow_pct']):.0f}% เทียบ{pword}ก่อน — ตรวจสอบสาเหตุ"
                     if th else
                     f"Chain net sales down {abs(headline['rev_wow_pct']):.0f}% vs last {pword} — investigate drivers.")})
    for r in branches:
        if r["wow_pct"] is not None and r["wow_pct"] <= -25 and r["revenue"] > 0:
            actions.append({"sev": "high",
                "text": (f"{r['branch']} ยอดขายลดลง {abs(r['wow_pct']):.0f}% เทียบ{pword}ก่อน (฿{r['revenue']:,.0f})"
                         if th else
                         f"{r['branch']} net sales down {abs(r['wow_pct']):.0f}% vs last {pword} (฿{r['revenue']:,.0f}).")})
    if cash_heavy.get("bills"):
        actions.append({"sev": "high",
            "text": (f"{cash_heavy['bills']} บิลส่วนลดที่จ่ายด้วยเงินสดเป็นหลัก (฿{cash_heavy['baht']:,.0f}) — ตรวจสอบความเสี่ยงเงินขาด"
                     if th else
                     f"{cash_heavy['bills']} cash-heavy discounted bills (฿{cash_heavy['baht']:,.0f}) — review for skim risk.")})
    if heavy["bills"]:
        actions.append({"sev": "med",
            "text": (f"{heavy['bills']} บิลให้ส่วนลด ≥30% (฿{heavy['baht']:,.0f}) ใน{pword}นี้"
                     if th else
                     f"{heavy['bills']} bills discounted ≥30% (฿{heavy['baht']:,.0f}) this {pword}.")})
    if reprint["bills"]:
        actions.append({"sev": "med",
            "text": (f"{reprint['bills']} บิลส่วนลดถูกพิมพ์ซ้ำมากกว่า 1 ครั้ง — อาจมีการพิมพ์ซ้ำผิดปกติ"
                     if th else
                     f"{reprint['bills']} discounted bills reprinted >1× — possible re-print abuse.")})
    if food_cost_pct is not None and food_cost_pct >= 53:
        actions.append({"sev": "med",
            "text": (f"ต้นทุนอาหาร {food_cost_pct:.0f}% — สูงกว่าเป้า ~50%"
                     if th else
                     f"Food cost {food_cost_pct:.0f}% — above the ~50% target.")})
    if projected and target and projected < target * 0.95:
        gap = (1 - projected/target) * 100
        if th:
            word = "ยอดรวมทั้งเดือน" if month_complete else "ประมาณการสิ้นเดือน"
            actions.append({"sev": "med",
                "text": f"{word} ฿{projected:,.0f} ต่ำกว่าเป้า ฿{target:,.0f} อยู่ {gap:.0f}%"})
        else:
            word = "Month total" if month_complete else "Month-end projection"
            actions.append({"sev": "med",
                "text": f"{word} ฿{projected:,.0f} is {gap:.0f}% below target ฿{target:,.0f}."})
    if has_prev and cost_up and (cost_up[0].get("delta") or 0) >= 0.5:
        m0 = cost_up[0]
        actions.append({"sev": "low",
            "text": (f"{m0['menu']} ต้นทุนต่อหัวเพิ่มขึ้น ฿{m0['delta']:.2f} เทียบ{pword}ก่อน "
                     f"(฿{m0['cost_per_head']:.2f}/หัว) — ตรวจสอบการตักเสิร์ฟ / ต้นทุนวัตถุดิบ"
                     if th else
                     f"{m0['menu']} food cost/head up ฿{m0['delta']:.2f} vs last {pword} "
                     f"(฿{m0['cost_per_head']:.2f}/head) — check portioning / ingredient cost.")})
    if gp.get("gp_pct_delta_pp") is not None and gp["gp_pct_delta_pp"] <= -3:
        actions.append({"sev": "med",
            "text": (f"อัตรากำไรขั้นต้นลดลง {abs(gp['gp_pct_delta_pp']):.1f}pp เทียบ{pword}ก่อน — ตอนนี้ {gp['gp_pct']:.0f}%"
                     if th else
                     f"Gross margin down {abs(gp['gp_pct_delta_pp']):.1f}pp vs last {pword} — now {gp['gp_pct']:.0f}%.")})
    sev_rank = {"high": 0, "med": 1, "low": 2}
    actions.sort(key=lambda a: sev_rank.get(a["sev"], 9))
    actions = actions[:7]
    if not actions:
        actions = [{"sev": "low", "text": ("ไม่พบประเด็นเร่งด่วนในช่วงนี้" if th else "No critical issues flagged for this period.")}]

    return {
        "available": True,
        "period": period,
        "lang": ("th" if th else "en"),
        "label": label,
        "comp_label": comp_label,
        "focus": focus,
        "range": [cs.isoformat(), ce.isoformat()],
        "comp_range": [ps.isoformat(), pe.isoformat()] if has_prev else None,
        "yoy_range": [ys.isoformat(), ye.isoformat()] if has_yoy else None,
        "partial": partial,
        "selected_week": selected_week,
        "weeks": weeks,
        "selected_month": selected_month,
        "months": months,
        "days_in_period": days_in_period,
        "latest_day": maxd,
        "min_day": mind,
        "branch_scope": branch or ("Group: " + group if group else "All branches"),
        "has_prev": has_prev,
        "has_yoy": has_yoy,
        "headline": headline,
        "branches": branches,
        "gp": gp,
        "leak": leak,
        "menu": menu_out,
        "pace": pace,
        "peak": peak,
        "table": table,
        "payment": payment,
        "tier": tier_out,
        "trend": trend,
        "actions": actions,
    }


# ---------------------------------------------------------------------------
# /api/reports/management.xlsx — the same Management Summary as a formatted .xlsx
# ---------------------------------------------------------------------------
@app.get("/api/reports/management.xlsx")
def report_management_xlsx(
    period: str = Query("daily"),
    date:   Optional[str] = Query(None),
    week:   Optional[str] = Query(None),
    month:  Optional[str] = Query(None),
    branch: Optional[str] = Query(None),
    group:  Optional[str] = Query(None),
    lang:   str = Query("en"),               # match the on-screen language
):
    """Download the Daily/Weekly/Monthly Management Summary as a multi-sheet Excel
    file. Reuses report_management() so the numbers match the on-screen report."""
    try:
        import openpyxl
        from openpyxl.styles import Font, PatternFill, Alignment
        from openpyxl.utils import get_column_letter
    except ImportError:
        raise HTTPException(status_code=500, detail="openpyxl not installed")
    # report_management is @ttl_cache-wrapped → returns a Response(bytes); parse its body.
    resp = report_management(period=period, date=date, week=week, month=month,
                             branch=branch, group=group, lang=lang)
    j = _json.loads(resp.body) if hasattr(resp, "body") else resp
    if not j.get("available"):
        raise HTTPException(status_code=404, detail="No sales data for this period")

    wb = openpyxl.Workbook()
    HDR    = Font(bold=True, color="FFFFFF")
    BOLD   = Font(bold=True)
    FILL_H = PatternFill("solid", fgColor="0F766E")
    NUM, NUM2, PCT = '#,##0', '#,##0.00', '0.0%'
    CENTER = Alignment(horizontal="center")
    pdelta = lambda v: (v / 100.0) if v is not None else None

    def header(ws, cols):
        ws.append(cols)
        for c in ws[ws.max_row]:
            c.font = HDR; c.fill = FILL_H; c.alignment = CENTER

    def autofit(ws):
        for col in ws.columns:
            mx = max((len(str(c.value or "")) for c in col), default=8)
            ws.column_dimensions[get_column_letter(col[0].column)].width = min(mx + 2, 52)

    H = j.get("headline", {}) or {}
    P = j.get("pace", {}) or {}

    # Sheet 1 — Summary
    ws = wb.active; ws.title = "Summary"
    ws.append(["Oshinei — Management Summary Report"]); ws["A1"].font = Font(bold=True, size=14)
    ws.append(["Report", {"daily": "Daily", "monthly": "Monthly"}.get(j["period"], "Weekly"), j.get("label", "")])
    ws.append(["Scope", j.get("branch_scope", "")])
    ws.append(["Compared with", j.get("comp_label", "")])
    ws.append(["Latest data day", j.get("latest_day", "")])
    ws.append([])
    G = j.get("gp", {}) or {}
    header(ws, ["Headline metric", "Value", "vs prev %", "YoY %"])
    for name, val, wow, yoy in [
        ("Net Sales (ex-VAT)", H.get("revenue"),     H.get("rev_wow_pct"),    H.get("rev_yoy_pct")),
        ("Gross Profit",       G.get("gp"),          None,                    None),
        ("Customers (covers)", H.get("customers"),    H.get("cust_wow_pct"),   H.get("cust_yoy_pct")),
        ("Spend / Head",       H.get("rev_per_head"), H.get("rph_wow_pct"),    H.get("rph_yoy_pct")),
        ("Avg Ticket",         H.get("avg_ticket"),   H.get("ticket_wow_pct"), None),
        ("Bills",              H.get("bills"),        H.get("bills_wow_pct"),  None),
        ("Discounts",          H.get("discount"),     None,                    None),
    ]:
        ws.append([name, val, pdelta(wow), pdelta(yoy)])
        ws.cell(ws.max_row, 2).number_format = NUM
        if wow is not None: ws.cell(ws.max_row, 3).number_format = PCT
        if yoy is not None: ws.cell(ws.max_row, 4).number_format = PCT
    ws.append([])
    header(ws, ["Pacing & cost (" + str(P.get("month", "")) + ")", "Value"])
    complete = P.get("month_complete")
    for name, val, fmt in [
        (("Month net sales" if complete else "MTD net sales"), P.get("mtd_revenue"), NUM),
        (("Month total" if complete else "Projected month-end"), P.get("projected"), NUM),
        ("Revenue target", P.get("target"), NUM),
        ("Projection vs target", pdelta(P.get("proj_vs_target_pct")), PCT),
        ("Food cost % (BoM, MTD)", pdelta(P.get("food_cost_pct")), PCT),
        ("Gross margin % (this period)", pdelta(G.get("gp_pct")), PCT),
    ]:
        ws.append([name, val]); ws.cell(ws.max_row, 2).number_format = fmt
    autofit(ws)

    # Sheet 2 — Branch League
    ws = wb.create_sheet("Branch League")
    header(ws, ["Branch", "Net Sales", "Covers", "Spend/Head", "Share %", "vs prev %"])
    for b in j.get("branches", []):
        ws.append([b["branch"], b["revenue"], b["customers"], b["rev_per_head"],
                   pdelta(b["share_pct"]), pdelta(b["wow_pct"])])
        r = ws.max_row
        for c in (2, 3, 4): ws.cell(r, c).number_format = NUM
        ws.cell(r, 5).number_format = PCT; ws.cell(r, 6).number_format = PCT
    autofit(ws)

    # Sheet 3 — Money-Leak Radar
    ws = wb.create_sheet("Money-Leak Radar")
    header(ws, ["Signal", "Bills", "Baht", "Top branches"])
    L = j.get("leak", {}) or {}
    for key, lbl in [("heavy_discount", "Heavy discounts (>=30%)"),
                     ("cash_heavy", "Cash-heavy discounts"),
                     ("reprinted", "Reprinted bills"),
                     ("zero_gross", "Zero-gross bills")]:
        o = L.get(key, {}) or {}
        tops = ", ".join(x.get("branch", "") for x in (o.get("by_branch") or [])[:3])
        ws.append([lbl, o.get("bills", 0), o.get("baht", 0), tops])
        ws.cell(ws.max_row, 2).number_format = NUM; ws.cell(ws.max_row, 3).number_format = NUM
    autofit(ws)

    # Sheet 4 — Menu Cost per Head
    ws = wb.create_sheet("Menu Cost per Head")
    M = j.get("menu", {}) or {}
    ws.append([f"Total food cost / head: {M.get('total_cph')}  (over {M.get('covers')} covers)"])
    ws["A1"].font = BOLD
    ws.append([])
    def menu_block(title, rows, signed):
        header(ws, [title, "Cost/head", "Prev -> now", "%"] if signed else [title, "Cost/head", "Qty", ""])
        for m in rows:
            if signed:
                ws.append([m["menu"], m["delta"],
                           f"{m.get('cost_per_head_prev')} -> {m.get('cost_per_head')}", pdelta(m.get("pct"))])
                ws.cell(ws.max_row, 2).number_format = NUM2; ws.cell(ws.max_row, 4).number_format = PCT
            else:
                ws.append([m["menu"], m.get("cost_per_head"), m.get("qty"), ""])
                ws.cell(ws.max_row, 2).number_format = NUM2
        ws.append([])
    menu_block("Highest cost / head", M.get("top", []), False)
    menu_block("Cost / head RISING (margin risk)", M.get("risers", []), True)
    menu_block("Cost / head FALLING", M.get("savers", []), True)
    autofit(ws)

    # Sheet 5 — Trend
    ws = wb.create_sheet("Trend")
    header(ws, ["Period start", "Label", "Net Sales"])
    for t in j.get("trend", []):
        ws.append([t.get("start"), t.get("label"), t.get("revenue")])
        ws.cell(ws.max_row, 3).number_format = NUM
    autofit(ws)

    # Sheet 6 — Peak Hours & Dayparts
    PK = j.get("peak", {}) or {}
    ws = wb.create_sheet("Peak & Dayparts")
    header(ws, ["Daypart", "Revenue", "% of revenue", "Covers"])
    for d in PK.get("dayparts", []):
        ws.append([d.get("name"), d.get("revenue"), pdelta(d.get("pct")), d.get("covers")])
        ws.cell(ws.max_row, 2).number_format = NUM; ws.cell(ws.max_row, 3).number_format = PCT
    ws.append([])
    if PK.get("peak_hour"):
        ws.append(["Busiest hour", f"{PK['peak_hour']['hour']:02d}:00", PK["peak_hour"]["revenue"]])
        ws.cell(ws.max_row, 3).number_format = NUM
    if PK.get("best_day"):
        ws.append(["Busiest day", PK["best_day"]])
    ws.append([])
    if PK.get("by_day"):
        header(ws, ["Day of week", "Revenue", "Covers"])
        for d in PK["by_day"]:
            ws.append([d["dow"], d["revenue"], d["covers"]]); ws.cell(ws.max_row, 2).number_format = NUM
    else:
        header(ws, ["Hour", "Revenue", "Covers", "Bills"])
        for hrow in PK.get("hourly", []):
            ws.append([f"{hrow['hour']:02d}:00", hrow["revenue"], hrow["covers"], hrow["bills"]])
            ws.cell(ws.max_row, 2).number_format = NUM
    autofit(ws)

    # Sheet 7 — Table Turnover (only when available)
    TB = j.get("table", {}) or {}
    if TB.get("available"):
        ws = wb.create_sheet("Table Turnover")
        ws.append(["Covers / table", TB.get("covers_per_table")])
        ws.append(["Avg turn (min)", TB.get("avg_turn_min")])
        ws.append(["Tables used", TB.get("tables")])
        ws.append(["Total covers", TB.get("covers")])
        ws.append([])
        header(ws, ["Branch", "Tables", "Covers", "Covers/Table", "Avg turn (min)"])
        for b in TB.get("by_branch", []):
            ws.append([b["branch"], b["tables"], b["covers"], b["covers_per_table"], b["avg_turn_min"]])
        autofit(ws)

    # Sheet — Buffet Price Tiers (only when the tier fact tables cover the window)
    TIER = j.get("tier") or {}
    if TIER.get("rows"):
        ws = wb.create_sheet("Price Tiers")
        ws.append([f"Buffet guests by price tier: {TIER.get('pax')}"
                   + (f"  (prev {TIER.get('pax_prev')})" if TIER.get("pax_prev") is not None else "")])
        ws["A1"].font = BOLD
        if TIER.get("mix_effect") is not None:
            ws.append([f"Tier-mix effect on spend/head vs comparison period: {TIER['mix_effect']:+.2f} THB"])
        ws.append([])
        header(ws, ["Tier", "Guests", "Guests prev", "vs prev %", "Share %",
                    "Rev/Head", "Cost/Head", "Cost/Head prev", "GP/Head", "Food %"])
        for r in TIER["rows"]:
            ws.append([r["label"], r["pax"], r.get("pax_prev"), pdelta(r.get("d_pax_pct")),
                       pdelta(r.get("share")), r.get("rev_head"), r.get("cogs_head"),
                       r.get("cogs_head_prev"), r.get("gp_head"), pdelta(r.get("food_pct"))])
            rw = ws.max_row
            for c in (2, 3): ws.cell(rw, c).number_format = NUM
            for c in (6, 7, 8, 9): ws.cell(rw, c).number_format = NUM2
            for c in (4, 5, 10): ws.cell(rw, c).number_format = PCT
        DRV = TIER.get("drivers") or {}
        DPAY = (DRV.get("by_tier") or {}).get(DRV.get("default_tier"))
        if DPAY:
            def _short_menu(s):
                s = (s or "").strip()
                if s.startswith("(B) "):
                    s = s[4:]
                if s.lower().endswith("(buffet)"):
                    s = s[:-len("(buffet)")].rstrip()
                return s
            _frac = lambda p: (p / 100.0 if p is not None else None)
            ws.append([])
            ws.append([f"Why {DPAY.get('focus_label', '')} runs the highest food %"
                       + (f" ({DPAY['focus_food_pct']:.1f}%)" if DPAY.get("focus_food_pct") is not None else "")
                       + " — cost drivers / head"])
            ws.cell(ws.max_row, 1).font = BOLD
            has_vs = bool(DPAY.get("ref_label"))
            header(ws, ["Ingredient group", "Cost/Head", "% of tier cost"]
                   + ([f"vs {DPAY['ref_label']}"] if has_vs else []))
            for g in DPAY.get("groups", []):
                ws.append([g["label"], g.get("cogs_head"), _frac(g.get("share_pct"))]
                          + ([g.get("vs_ref")] if has_vs else []))
                ws.cell(ws.max_row, 2).number_format = NUM2
                ws.cell(ws.max_row, 3).number_format = PCT
                if has_vs:
                    ws.cell(ws.max_row, 4).number_format = NUM2
            ws.append([])
            header(ws, ["Top menu item", "Cost/Head", "% of tier cost", "Qty/100 guests"])
            for m in DPAY.get("menus", []):
                ws.append([_short_menu(m["label"]), m.get("cogs_head"),
                           _frac(m.get("share_pct")), m.get("qty_100")])
                ws.cell(ws.max_row, 2).number_format = NUM2
                ws.cell(ws.max_row, 3).number_format = PCT
                ws.cell(ws.max_row, 4).number_format = NUM
        autofit(ws)

    # Sheet — Action Items
    ws = wb.create_sheet("Action Items")
    header(ws, ["Severity", "Action"])
    for a in j.get("actions", []):
        ws.append([a.get("sev", ""), a.get("text", "")])
    autofit(ws)

    buf = _io.BytesIO(); wb.save(buf); buf.seek(0)
    tag = j.get("selected_month") or j.get("selected_week") or j.get("focus") or "report"
    fname = f"management_{j['period']}_{tag}.xlsx"
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename={fname}"},
    )


# ---------------------------------------------------------------------------
# /api/analysis/sph  — Spend-per-Head distribution
# ---------------------------------------------------------------------------
@app.get("/api/analysis/sph")
@ttl_cache
def analysis_sph(
    month: Optional[str] = Query(None),
    month_from: Optional[str] = Query(None),
    month_to: Optional[str] = Query(None),
    branch: Optional[str] = Query(None),
):
    filters = ["st.customer_count > 0", "st.source = 'Easy'", "b.name != 'ALL'"]
    params: dict = {}
    if branch:
        filters.append("b.name = :branch")
        params["branch"] = branch
    _month_range_filter(filters, params, month_from, month_to, month,
                        col="strftime('%Y-%m', st.sold_at)")
    where = "WHERE " + " AND ".join(filters)

    with get_db() as con:
        rows = con.execute(f"""
            SELECT (st.total_inc_vat / 1.07) / st.customer_count AS sph,
                   b.name                               AS branch,
                   CAST(strftime('%H', st.sold_at) AS INTEGER) AS hour
            FROM sales_transaction st
            JOIN branches b ON b.branch_id = st.branch_id
            {where}
        """, params).fetchall()

    if not rows:
        return {"by_branch": [], "distribution": [], "overall": {}, "by_daypart": [], "trend": []}

    sphs = [r["sph"] for r in rows if r["sph"] is not None]
    sphs.sort()
    n = len(sphs)

    def median(lst):
        if not lst: return 0
        m = len(lst) // 2
        return (lst[m - 1] + lst[m]) / 2 if len(lst) % 2 == 0 else lst[m]

    def percentile(lst, p):
        if not lst: return 0
        idx = int(p / 100 * len(lst))
        return lst[min(idx, len(lst) - 1)]

    avg_sph = sum(sphs) / n if n else 0

    # By branch
    branch_map: dict = {}
    for r in rows:
        br = r["branch"]
        if br not in branch_map:
            branch_map[br] = []
        if r["sph"] is not None:
            branch_map[br].append(r["sph"])
    by_branch = sorted([
        {
            "branch": br,
            "avg_sph": round(sum(vals) / len(vals), 2) if vals else 0,
            "median_sph": round(median(sorted(vals)), 2) if vals else 0,
            "tx_count": len(vals),
        }
        for br, vals in branch_map.items()
    ], key=lambda x: x["avg_sph"], reverse=True)

    # Distribution buckets aligned to buffet price tiers (350 / 500 / 590 / 799)
    # Boundaries account for the typical VAT + service-charge markup (~17–25%).
    # ที่บัฟเฟ่ฯ ผู้ใหญ่: 350 → ราคาขาย ~380-470, 500 → ~530-620, 590 → ~620-770, 799 → ~770-960
    buckets = [
        ("ต่ำกว่า Buffet 350", 0, 379.99, '#94a3b8'),
        ("Buffet 350",          380, 469.99,  '#eab308'),
        ("Buffet 500",          470, 619.99,  '#10b981'),
        ("Buffet 590",          620, 769.99,  '#4f46e5'),
        ("Buffet 799",          770, 959.99,  '#f97316'),
        ("สูงกว่า Buffet 799",  960, float("inf"), '#ef4444'),
    ]
    distribution = []
    for label, lo, hi, color in buckets:
        count = sum(1 for s in sphs if lo <= s <= hi)
        pct = round(count / n * 100, 1) if n else 0
        distribution.append({"bucket": label, "count": count, "pct": pct, "color": color, "lo": lo, "hi": hi if hi != float("inf") else None})

    # By daypart
    daypart_map: dict = {"Lunch 11-14": [], "Dinner 17-22": [], "Other": []}
    for r in rows:
        h = r["hour"]
        if 11 <= h <= 14:
            daypart_map["Lunch 11-14"].append(r["sph"])
        elif 17 <= h <= 22:
            daypart_map["Dinner 17-22"].append(r["sph"])
        else:
            daypart_map["Other"].append(r["sph"])
    by_daypart = [
        {
            "daypart": dp,
            "avg_sph": round(sum(vals) / len(vals), 2) if vals else 0,
            "tx_count": len(vals),
        }
        for dp, vals in daypart_map.items()
    ]

    # Monthly trend — must honour the same period filter as the rest of the endpoint.
    filters2 = ["st.customer_count > 0", "st.source = 'Easy'", "b.name != 'ALL'"]
    params2: dict = {}
    if branch:
        filters2.append("b.name = :branch")
        params2["branch"] = branch
    _month_range_filter(filters2, params2, month_from, month_to, month,
                        col="strftime('%Y-%m', st.sold_at)")
    where2 = "WHERE " + " AND ".join(filters2)
    with get_db() as con:
        trend_rows = con.execute(f"""
            SELECT strftime('%Y-%m', st.sold_at) AS period_month,
                   ROUND(AVG((st.total_inc_vat / 1.07) / st.customer_count), 2) AS avg_sph
            FROM sales_transaction st
            JOIN branches b ON b.branch_id = st.branch_id
            {where2}
            GROUP BY period_month ORDER BY period_month
        """, params2).fetchall()
    trend = [dict(r) for r in trend_rows]

    # Dominant tier + share above 590
    dom_tier = max(distribution, key=lambda d: d["count"]) if distribution else None
    above_590_count = sum(b["count"] for b in distribution if b["lo"] >= 620)
    below_350_count = sum(b["count"] for b in distribution if (b.get("hi") or float("inf")) < 380)
    return {
        "by_branch": by_branch,
        "distribution": distribution,
        "overall": {
            "avg_sph": round(avg_sph, 2),
            "median_sph": round(median(sphs), 2),
            "p10": round(percentile(sphs, 10), 2),
            "p25": round(percentile(sphs, 25), 2),
            "p75": round(percentile(sphs, 75), 2),
            "p90": round(percentile(sphs, 90), 2),
            "total_tx": n,
            "dominant_tier": dom_tier["bucket"] if dom_tier else None,
            "dominant_pct":  round(dom_tier["count"] / n * 100, 1) if dom_tier and n else 0,
            "pct_above_590": round(above_590_count / n * 100, 1) if n else 0,
            "pct_below_350": round(below_350_count / n * 100, 1) if n else 0,
            "sph_min":      round(min(sphs), 2) if sphs else 0,
            "sph_max":      round(max(sphs), 2) if sphs else 0,
        },
        "by_daypart": by_daypart,
        "trend": trend,
    }


# ---------------------------------------------------------------------------
# /api/analysis/daypart_grid  — Hourly × DOW 2D matrix
# ---------------------------------------------------------------------------
@app.get("/api/analysis/daypart_grid")
@ttl_cache
def analysis_daypart_grid(
    month: Optional[str] = Query(None),
    month_from: Optional[str] = Query(None),
    month_to: Optional[str] = Query(None),
    branch: Optional[str] = Query(None),
    metric: str = Query("revenue"),  # revenue|tx_count|customer_count|avg_ticket
):
    filters = ["st.source = 'Easy'", "b.name != 'ALL'"]
    params: dict = {}
    if branch:
        filters.append("b.name = :branch")
        params["branch"] = branch
    _month_range_filter(filters, params, month_from, month_to, month,
                        col="strftime('%Y-%m', st.sold_at)")
    where = "WHERE " + " AND ".join(filters)

    with get_db() as con:
        rows = con.execute(f"""
            SELECT
                CAST(strftime('%w', st.sold_at) AS INTEGER)  AS dow,
                CAST(strftime('%H', st.sold_at) AS INTEGER)  AS hour,
                COUNT(*)                                      AS tx_count,
                COALESCE(SUM(st.customer_count), 0)           AS customer_count,
                ROUND(SUM(st.total_inc_vat) / 1.07, 2)               AS revenue,
                ROUND(AVG(st.total_inc_vat), 2)               AS avg_ticket,
                COUNT(DISTINCT DATE(st.sold_at))              AS day_count
            FROM sales_transaction st
            JOIN branches b ON b.branch_id = st.branch_id
            {where}
            GROUP BY dow, hour
        """, params).fetchall()

    cells = []
    max_value = 0.0
    for r in rows:
        if metric == "revenue":
            val = (r["revenue"] or 0) / max(r["day_count"] or 1, 1)
        elif metric == "tx_count":
            val = (r["tx_count"] or 0) / max(r["day_count"] or 1, 1)
        elif metric == "customer_count":
            val = (r["customer_count"] or 0) / max(r["day_count"] or 1, 1)
        else:  # avg_ticket
            val = r["avg_ticket"] or 0
        val = round(val, 2)
        cells.append({"dow": r["dow"], "hour": r["hour"], "value": val})
        if val > max_value:
            max_value = val

    peak = max(cells, key=lambda c: c["value"]) if cells else None

    return {
        "cells": cells,
        "peak": peak,
        "max_value": round(max_value, 2),
        "labels": {
            "dow": ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"],
            "hours": list(range(24)),
        },
    }


# ---------------------------------------------------------------------------
# /api/analysis/branch_peak  — Per-branch peak hour/dow summary for Time & Flow
# ---------------------------------------------------------------------------
@app.get("/api/analysis/branch_peak")
@ttl_cache
def analysis_branch_peak(
    month: Optional[str] = Query(None),
    month_from: Optional[str] = Query(None),
    month_to: Optional[str] = Query(None),
    metric: str = Query("revenue"),
):
    """Returns per-branch peak hour, peak DOW, and hourly+DOW distribution for Time & Flow tab."""
    filters = ["st.source='Easy'", "b.name != 'ALL'"]
    params: dict = {}
    _month_range_filter(filters, params, month_from, month_to, month,
                        col="strftime('%Y-%m', st.sold_at)")
    where = "WHERE " + " AND ".join(filters)

    with get_db() as con:
        rows = con.execute(f"""
            SELECT
                b.name AS branch,
                CAST(strftime('%H', st.sold_at) AS INTEGER)  AS hour,
                CAST(strftime('%w', st.sold_at) AS INTEGER)  AS dow,
                COUNT(*) AS tx_count,
                COALESCE(SUM(st.customer_count), 0) AS customer_count,
                ROUND(SUM(st.total_inc_vat) / 1.07, 2) AS revenue,
                ROUND(AVG(st.total_inc_vat), 2) AS avg_ticket,
                COUNT(DISTINCT DATE(st.sold_at)) AS day_count
            FROM sales_transaction st
            JOIN branches b ON b.branch_id = st.branch_id
            {where}
            GROUP BY b.name, hour, dow
        """, params).fetchall()

    if not rows:
        return {"branches": []}

    DOW_LABELS = ["อา", "จ", "อ", "พ", "พฤ", "ศ", "ส"]

    def get_val(r):
        if metric == "tx_count":      return (r["tx_count"] or 0) / max(r["day_count"] or 1, 1)
        if metric == "customer_count":return (r["customer_count"] or 0) / max(r["day_count"] or 1, 1)
        if metric == "avg_ticket":    return r["avg_ticket"] or 0
        return (r["revenue"] or 0) / max(r["day_count"] or 1, 1)

    # Group by branch
    from collections import defaultdict
    branch_data: dict = defaultdict(list)
    for r in rows:
        branch_data[r["branch"]].append({"hour": r["hour"], "dow": r["dow"], "val": get_val(r)})

    result = []
    for br, cells in branch_data.items():
        # Peak hour (collapse over all DOWs)
        hour_agg: dict = defaultdict(float)
        dow_agg: dict = defaultdict(float)
        for c in cells:
            hour_agg[c["hour"]] += c["val"]
            dow_agg[c["dow"]]   += c["val"]
        peak_hour = max(hour_agg, key=hour_agg.__getitem__) if hour_agg else None
        peak_dow  = max(dow_agg,  key=dow_agg.__getitem__)  if dow_agg  else None

        # Hourly array (sum val across DOWs, 0-23)
        hourly = [round(hour_agg.get(h, 0), 1) for h in range(24)]
        # DOW array (0=Sun..6=Sat)
        dow_arr = [round(dow_agg.get(d, 0), 1) for d in range(7)]
        max_val = max(hourly) if hourly else 1

        result.append({
            "branch": br,
            "peak_hour": peak_hour,
            "peak_dow": peak_dow,
            "peak_dow_label": DOW_LABELS[peak_dow] if peak_dow is not None else None,
            "hourly": hourly,
            "dow": dow_arr,
            "max_val": max_val,
        })

    result.sort(key=lambda x: x["branch"])
    return {"branches": result, "metric": metric}


# ---------------------------------------------------------------------------
# /api/menu/engineering  — Kasavana-Smith matrix
# ---------------------------------------------------------------------------
def _menu_engineering_compute(
    month=None, month_from=None, month_to=None,
    branch=None, category=None, include_buffet_drink=False,
):
    """Core of /api/menu/engineering — returns the items + medians dict (UNCACHED,
    so it's callable in-process). The cached endpoint below is a thin wrapper, and
    /api/menu/action-board reuses this so its quadrant/margin numbers are guaranteed
    identical to the Menu Engineering tab (no duplicated classification, no drift)."""
    filters = ["b.name != 'ALL'"]
    params: dict = {}
    if branch:
        filters.append("b.name = :branch")
        params["branch"] = branch
    _month_range_filter(filters, params, month_from, month_to, month,
                        col="ssm.period_month")
    if category:
        filters.append("COALESCE(cat.name, 'Uncategorised') = :category")
        params["category"] = category
    # Default: exclude non-food categories so menu engineering reflects à la carte food only.
    # Exception: keep ice-cream items (look for ICE / ไอติม / ไอศกรีม / GELATO in description)
    # even if they sit inside a DESSERT category — user wants ice cream in food analysis.
    EXCLUDED_CATS = (
        'BUFFET', 'Oshinei SOFT DRINK', 'Oshinei ALCOHOL',
        'Oshinei DESSERT', 'DESSERT', 'Payment', 'Other',
        'Oshinei DELIVERY', 'Special DELIVERY',
    )
    if not include_buffet_drink and not category:
        ph = ",".join(f":_excl_{i}" for i in range(len(EXCLUDED_CATS)))
        filters.append(
            f"(COALESCE(cat.name,'Uncategorised') NOT IN ({ph}) "
            f" OR UPPER(COALESCE(s.description,'')) LIKE '%ICE CREAM%' "
            f" OR UPPER(COALESCE(s.description,'')) LIKE '%ICECREAM%' "
            f" OR UPPER(COALESCE(s.description,'')) LIKE '%GELATO%' "
            f" OR UPPER(COALESCE(s.description,'')) LIKE '%SOFT SERVE%' "
            f" OR COALESCE(s.description,'') LIKE '%ไอติม%' "
            f" OR COALESCE(s.description,'') LIKE '%ไอศกรีม%' "
            f" OR COALESCE(s.description,'') LIKE '%ไอ-ติม%')"
        )
        for i, c in enumerate(EXCLUDED_CATS):
            params[f"_excl_{i}"] = c
    where = "WHERE " + " AND ".join(filters) if filters else ""

    with get_db() as con:
        rows = con.execute(f"""
            SELECT
                s.sku_code,
                COALESCE(s.description, s.sku_code)          AS description,
                COALESCE(cat.name, 'Uncategorised')           AS category,
                SUM(ssm.qty)                                  AS qty,
                ROUND(SUM(ssm.subtotal_ex_vat), 2)            AS subtotal_ex_vat,
                ROUND(SUM(ssm.subtotal_ex_vat), 2)            AS revenue,
                ROUND(SUM(ssm.total_inc_vat), 2)              AS pos_total_inc_vat_diagnostic,
                SUM({_cogs_case(ssm='ssm', sk='s', sc='sc')}) AS cogs_total,
                SUM(CASE WHEN {_effective_cost_subquery(ssm='ssm', sc='sc')} IS NOT NULL
                         THEN ssm.qty ELSE 0 END)             AS costed_qty
            FROM sales_sku_monthly ssm
            JOIN skus s ON s.sku_id = ssm.sku_id
            JOIN branches b ON b.branch_id = ssm.branch_id
            LEFT JOIN categories cat ON cat.category_id = s.category_id
            LEFT JOIN sku_cost sc ON sc.sku_id = s.sku_id
            {where}
            GROUP BY s.sku_code, s.description, cat.name
        """, params).fetchall()

    if not rows:
        return {"items": [], "median_popularity_pct": 0, "median_margin": 0,
                "total_skus": 0, "costed_skus": 0}

    total_qty = sum(r["qty"] or 0 for r in rows) or 1

    items = []
    for r in rows:
        qty = r["qty"] or 0
        revenue = r["revenue"] or 0
        subtotal_ex = r["subtotal_ex_vat"] or 0
        cogs = r["cogs_total"]
        costed_qty = r["costed_qty"] or 0
        has_cost = cogs is not None and costed_qty > 0
        unit_cost = (cogs / costed_qty) if has_cost else None
        popularity_pct = round(qty / total_qty * 100, 4) if total_qty else 0
        margin_per_unit = round((subtotal_ex - cogs) / qty, 2) if has_cost and qty and cogs is not None else None
        margin_pct = round((subtotal_ex - cogs) / subtotal_ex * 100, 2) if has_cost and subtotal_ex and cogs is not None else None
        items.append({
            "sku_code": r["sku_code"],
            "description": r["description"],
            "category": r["category"],
            "qty": qty,
            "revenue": revenue,
            "margin_per_unit": margin_per_unit,
            "margin_pct": margin_pct,
            "popularity_pct": popularity_pct,
            "quadrant": None,
            "has_cost": has_cost,
        })

    costed = [i for i in items if i["has_cost"] and i["margin_per_unit"] is not None]
    total_skus = len(items)
    costed_skus = len(costed)

    if costed:
        pops_sorted = sorted(i["popularity_pct"] for i in costed)
        margs_sorted = sorted(i["margin_per_unit"] for i in costed)
        n = len(costed)

        def _median(lst):
            m = n // 2
            return (lst[m - 1] + lst[m]) / 2 if n % 2 == 0 else lst[m]

        med_pop = _median(pops_sorted)
        med_marg = _median(margs_sorted)

        for i in items:
            if not i["has_cost"] or i["margin_per_unit"] is None:
                continue
            high_pop = i["popularity_pct"] > med_pop
            high_marg = i["margin_per_unit"] > med_marg
            if high_pop and high_marg:
                i["quadrant"] = "star"
            elif high_pop and not high_marg:
                i["quadrant"] = "plowhorse"
            elif not high_pop and high_marg:
                i["quadrant"] = "puzzle"
            else:
                i["quadrant"] = "dog"
    else:
        med_pop = 0
        med_marg = 0

    return {
        "items": items,
        "median_popularity_pct": round(med_pop, 4),
        "median_margin": round(med_marg, 2),
        "total_skus": total_skus,
        "costed_skus": costed_skus,
    }


@app.get("/api/menu/engineering")
@ttl_cache
def menu_engineering(
    month: Optional[str] = Query(None),
    month_from: Optional[str] = Query(None),
    month_to: Optional[str] = Query(None),
    branch: Optional[str] = Query(None),
    category: Optional[str] = Query(None),
    include_buffet_drink: bool = Query(False, description="If false (default), exclude BUFFET/drink/dessert/payment so analysis focuses on actual food items"),
):
    return _menu_engineering_compute(month, month_from, month_to, branch, category, include_buffet_drink)


# ---------------------------------------------------------------------------
# Lifecycle slope/stage for a set of SKUs over a month window — the SAME formula
# as /api/menu/lifecycle (linear-regression slope on non-zero months), factored
# so the Action Board can tag each dish growing/declining/dead without a second
# full lifecycle pass. Returns {sku_code: {"stage":…, "slope_pct":…}}.
# ---------------------------------------------------------------------------
def _menu_lifecycle_stages(con, branch=None, month_from=None, month_to=None):
    filters = []
    params: dict = {}
    if branch:
        filters.append("b.name = :branch")
        params["branch"] = branch
    _month_range_filter(filters, params, month_from, month_to, col="sm.period_month")
    where = ("WHERE " + " AND ".join(filters)) if filters else ""
    rows = con.execute(f"""
        SELECT sk.sku_code AS code, sm.period_month AS m, SUM(sm.qty) AS qty
        FROM sales_sku_monthly sm
        JOIN skus     sk ON sk.sku_id    = sm.sku_id
        JOIN branches b  ON b.branch_id  = sm.branch_id
        {where}
        GROUP BY sk.sku_code, sm.period_month
        ORDER BY sk.sku_code, sm.period_month
    """, params).fetchall()

    months = sorted({r["m"] for r in rows})
    from collections import defaultdict
    by_sku: dict = defaultdict(dict)
    for r in rows:
        by_sku[r["code"]][r["m"]] = r["qty"] or 0

    out: dict = {}
    for code, monthly in by_sku.items():
        series = [monthly.get(m, 0) for m in months]
        n = len(series)
        recent_2 = series[-2:] if n >= 2 else series
        nz_start = next((i for i, v in enumerate(series) if v > 0), None)
        is_dead = all(v == 0 for v in recent_2) and any(v > 0 for v in series)
        is_new  = nz_start is not None and nz_start >= n - 2
        nonzero = [(i, v) for i, v in enumerate(series) if v > 0]
        if len(nonzero) >= 3:
            xs = [p[0] for p in nonzero]; ys = [p[1] for p in nonzero]
            xm = sum(xs) / len(xs); ym = sum(ys) / len(ys)
            num = sum((x - xm) * (y - ym) for x, y in zip(xs, ys))
            den = sum((x - xm) ** 2 for x in xs) or 1
            slope_pct = round((num / den) / ym * 100, 1) if ym else 0
        else:
            slope_pct = 0
        if is_dead:        stage = "dead"
        elif is_new:       stage = "new"
        elif slope_pct > 10:  stage = "growing"
        elif slope_pct < -10: stage = "declining"
        else:              stage = "stable"
        out[code] = {"stage": stage, "slope_pct": slope_pct}
    return out


@app.get("/api/menu/action-board")
@ttl_cache
def menu_action_board(
    month: Optional[str] = Query(None),
    month_from: Optional[str] = Query(None),
    month_to: Optional[str] = Query(None),
    branch: Optional[str] = Query(None),
    category: Optional[str] = Query(None),
):
    """DECISION layer over Menu Engineering + lifecycle: sorts every food dish into
    one action bucket so a GM knows what to DO, not just what the numbers are.

      🚀 feature     — Stars (popular & profitable) → ดันต่อ / จัดโปรโมชัน
      🔍 market      — Puzzles (profitable but under-ordered) → โปรโมต/วางตำแหน่งใหม่
      💰 fix_margin  — Plowhorses (popular but margin บาง) → ขึ้นราคา/ลดต้นทุน
      ✂️ review_cut  — Dogs that are ALSO declining/dead & low-volume → ทบทวน/พิจารณาตัด
      ℹ️ needs_cost  — no cost data → ยังจัดอันดับด้วยมาร์จิ้นไม่ได้ (honest, not hidden)

    Reuses _menu_engineering_compute (identical quadrant/margin) + _menu_lifecycle_stages
    (identical slope/stage), so figures tie out to the Menu Engineering & Lifecycle tabs.
    review_cut is a FLAGGED shortlist with a reason — it never deletes anything."""
    eng = _menu_engineering_compute(month, month_from, month_to, branch, category)
    items = eng.get("items", [])
    with get_db() as con:
        stages = _menu_lifecycle_stages(con, branch, month_from or month, month_to or month)

    buckets: dict = {"feature": [], "market": [], "fix_margin": [], "review_cut": [], "needs_cost": []}
    for it in items:
        code = it["sku_code"]
        lc = stages.get(code, {"stage": "stable", "slope_pct": 0})
        stage = lc["stage"]; slope_pct = lc["slope_pct"]
        quad = it.get("quadrant")
        mpct = it.get("margin_pct")
        pop  = it.get("popularity_pct") or 0
        rev  = it.get("revenue") or 0

        row = {
            "sku_code": code, "description": it["description"], "category": it["category"],
            "qty": it["qty"], "revenue": rev,
            "margin_pct": mpct, "popularity_pct": pop,
            "quadrant": quad, "stage": stage, "slope_pct": slope_pct,
        }

        if not it.get("has_cost") or quad is None:
            row["action"] = "needs_cost"
            row["priority_score"] = rev
            row["reason"] = "ยังไม่มีข้อมูลต้นทุน — เพิ่ม BoM/ต้นทุนเพื่อจัดอันดับด้วยมาร์จิ้น"
            buckets["needs_cost"].append(row)
            continue

        if quad == "star":
            action = "feature"
            row["reason"] = f"ขายดี + กำไรดี (มาร์จิ้น {mpct}% · ส่วนแบ่ง {round(pop,2)}%)" + (" และกำลังโต" if stage == "growing" else "") + " → ดันต่อ/จัดโปรโมชัน"
        elif quad == "puzzle":
            # high margin, low popularity → push volume; a growing puzzle is the best promo bet
            action = "feature" if stage == "growing" else "market"
            if action == "feature":
                row["reason"] = f"กำไรดีและกำลังโต (มาร์จิ้น {mpct}%, +{slope_pct}%/เดือน) → ดันให้เป็นดาว"
            else:
                row["reason"] = f"กำไรดีแต่ยังขายน้อย (มาร์จิ้น {mpct}% · ส่วนแบ่ง {round(pop,2)}%) → โปรโมต/วางตำแหน่งเมนู/อัปเซล"
        elif quad == "plowhorse":
            action = "fix_margin"
            row["reason"] = f"ขายดีแต่กำไรบาง (มาร์จิ้น {mpct}% · ส่วนแบ่ง {round(pop,2)}%) → ขึ้นราคาเล็กน้อยหรือลดต้นทุน"
        else:  # dog
            if stage in ("declining", "dead"):
                action = "review_cut"
                row["reason"] = f"ขายน้อย กำไรบาง และ{'ตายแล้ว' if stage=='dead' else 'กำลังลดลง'} (มาร์จิ้น {mpct}% · ส่วนแบ่ง {round(pop,2)}%) → ทบทวน/พิจารณาตัด"
            else:
                # dog but still stable/new — keep watching, not a cut candidate yet
                action = "market"
                row["reason"] = f"ขายน้อยและกำไรบาง (มาร์จิ้น {mpct}%) → ลองโปรโมต/ปรับเมนูก่อนตัดสินใจตัด"
        row["action"] = action
        # priority: feature/market rank by revenue (biggest upside first);
        # fix_margin by revenue at risk; review_cut by lowest revenue (least to lose)
        row["priority_score"] = rev
        buckets[action].append(row)

    for k, lst in buckets.items():
        reverse = (k != "review_cut")
        lst.sort(key=lambda r: r.get("priority_score") or 0, reverse=reverse)

    return {
        "buckets": buckets,
        "counts": {k: len(v) for k, v in buckets.items()},
        "median_popularity_pct": eng.get("median_popularity_pct", 0),
        "median_margin": eng.get("median_margin", 0),
        "total_skus": eng.get("total_skus", 0),
        "costed_skus": eng.get("costed_skus", 0),
        "month_range": [month_from or month, month_to or month],
    }


# ===========================================================================
# Menu Opportunities — data-driven "what could we add / revive / expand" signals.
# Deliberately COGS/margin-free (qty · revenue · share · trend only) so it stays
# under the menu.analysis module and is visible to Marketing / Innovation roles.
# ===========================================================================
def _opp_slope_pct(series):
    """Linear-regression %/period over non-zero points — same shape as lifecycle."""
    nonzero = [(i, v) for i, v in enumerate(series) if v > 0]
    if len(nonzero) < 3:
        return 0.0
    xs = [p[0] for p in nonzero]; ys = [p[1] for p in nonzero]
    xm = sum(xs) / len(xs); ym = sum(ys) / len(ys)
    num = sum((x - xm) * (y - ym) for x, y in zip(xs, ys))
    den = sum((x - xm) ** 2 for x in xs) or 1
    return round((num / den) / ym * 100, 1) if ym else 0.0


def _opp_median(vals):
    s = sorted(vals)
    n = len(s)
    if not n:
        return 0
    m = n // 2
    return (s[m - 1] + s[m]) / 2 if n % 2 == 0 else s[m]


def _menu_opportunities_compute(branch=None, month=None, month_from=None, month_to=None):
    """Three honest, sales-grounded opportunity signals (no bundle guesses — true
    item bundles need per-receipt lines we don't have broadly; branch-month
    co-occurrence is too coarse, so it's intentionally omitted):
      cross_branch_gaps  — chain winners a branch under-sells vs its peers
      category_whitespace — categories with demand concentrated in few SKUs / trending
      revive             — dead dishes that once sold well → relaunch candidates
    """
    base = ["b.name != 'ALL'", "COALESCE(b.active,1) = 1", "COALESCE(sk.active,1) = 1",
            "ssm.source = 'Easy'", _FOOD_CATEGORY_FILTER]
    params: dict = {}
    _month_range_filter(base, params, month_from, month_to, month, col="ssm.period_month")
    where = "WHERE " + " AND ".join(base)

    with get_db() as con:
        rows = con.execute(f"""
            SELECT b.name AS branch, sk.sku_code AS code,
                   COALESCE(sk.description, sk.sku_code) AS descr,
                   COALESCE(c.name, 'Uncategorised') AS category,
                   ssm.period_month AS m,
                   SUM(ssm.qty) AS qty, SUM(ssm.subtotal_ex_vat) AS rev
            FROM sales_sku_monthly ssm
            JOIN skus       sk ON sk.sku_id     = ssm.sku_id
            JOIN branches   b  ON b.branch_id   = ssm.branch_id
            JOIN categories c  ON c.category_id = sk.category_id
            {where}
            GROUP BY b.name, sk.sku_code, ssm.period_month
        """, params).fetchall()

    if not rows:
        return {"cross_branch_gaps": [], "category_whitespace": [], "revive": [],
                "month_range": [month_from or month, month_to or month]}

    months = sorted({r["m"] for r in rows})
    descr = {}; cat = {}
    sku_branch_qty: dict = {}           # code -> {branch: qty}
    branch_food_qty: dict = {}          # branch -> total food qty
    sku_month_qty: dict = {}            # code -> {month: qty}  (chain-wide)
    cat_month_qty: dict = {}            # category -> {month: qty}
    cat_rev: dict = {}                  # category -> revenue
    cat_skus: dict = {}                 # category -> set(code)
    for r in rows:
        code = r["code"]; br = r["branch"]; q = r["qty"] or 0; rv = r["rev"] or 0
        descr.setdefault(code, r["descr"]); cat.setdefault(code, r["category"])
        sku_branch_qty.setdefault(code, {}); sku_branch_qty[code][br] = sku_branch_qty[code].get(br, 0) + q
        branch_food_qty[br] = branch_food_qty.get(br, 0) + q
        sku_month_qty.setdefault(code, {}); sku_month_qty[code][r["m"]] = sku_month_qty[code].get(r["m"], 0) + q
        cname = r["category"]
        cat_month_qty.setdefault(cname, {}); cat_month_qty[cname][r["m"]] = cat_month_qty[cname].get(r["m"], 0) + q
        cat_rev[cname] = cat_rev.get(cname, 0) + rv
        cat_skus.setdefault(cname, set()).add(code)

    active_branches = [b for b, t in branch_food_qty.items() if t > 0]

    # ── 1. Cross-branch gaps ────────────────────────────────────────────────
    chain_qty = {c: sum(bq.values()) for c, bq in sku_branch_qty.items()}
    # proven chain winners: sold in ≥5 branches, ranked by chain volume
    winners = sorted(
        [c for c, bq in sku_branch_qty.items() if len([1 for v in bq.values() if v > 0]) >= 5],
        key=lambda c: chain_qty[c], reverse=True)[:40]
    gaps = []
    target_branches = [branch] if branch else active_branches
    for code in winners:
        bq = sku_branch_qty[code]
        peer_shares = [bq[b] / branch_food_qty[b] for b in active_branches
                       if branch_food_qty.get(b) and bq.get(b, 0) > 0]
        if len(peer_shares) < 3:
            continue
        med_share = _opp_median(peer_shares)
        if med_share <= 0:
            continue
        for b in target_branches:
            tot = branch_food_qty.get(b) or 0
            if tot <= 0:
                continue
            share = bq.get(b, 0) / tot
            if share < 0.4 * med_share:          # selling at <40% of peer norm
                potential = (med_share - share) * tot
                gaps.append({
                    "branch": b, "sku_code": code, "description": descr[code],
                    "category": cat[code],
                    "branch_qty": round(bq.get(b, 0)), "chain_qty": round(chain_qty[code]),
                    "peer_branches": len(peer_shares),
                    "potential_qty": round(potential),
                    "note": ("ยังไม่ขายเลย" if bq.get(b, 0) == 0 else "ขายต่ำกว่าค่ากลางสาขาอื่นมาก"),
                })
    gaps.sort(key=lambda g: g["potential_qty"], reverse=True)
    gaps = gaps[:30]

    # ── 2. Category whitespace ──────────────────────────────────────────────
    whitespace = []
    for cname, skus in cat_skus.items():
        qty = sum(sku_month_qty[c2].get(m, 0) for c2 in skus for m in months)
        n_skus = len(skus)
        rev = cat_rev.get(cname, 0)
        series = [cat_month_qty[cname].get(m, 0) for m in months]
        slope = _opp_slope_pct(series)
        rev_per_sku = round(rev / n_skus) if n_skus else 0
        qty_per_sku = round(qty / n_skus) if n_skus else 0
        whitespace.append({
            "category": cname, "n_skus": n_skus,
            "qty": round(qty), "revenue": round(rev),
            "qty_per_sku": qty_per_sku, "revenue_per_sku": rev_per_sku,
            "slope_pct": slope,
        })
    if whitespace:
        med_rps = _opp_median([w["revenue_per_sku"] for w in whitespace])
        for w in whitespace:
            if w["revenue_per_sku"] > med_rps and w["slope_pct"] >= 0:
                w["label"] = "expand"; w["reason"] = "ดีมานด์กระจุกในไม่กี่เมนู + ทรงตัว/โต → เพิ่มความหลากหลายได้"
            elif w["slope_pct"] < -10:
                w["label"] = "trim"; w["reason"] = "หมวดกำลังหดตัว → ทบทวนเมนูที่อ่อน"
            else:
                w["label"] = "watch"; w["reason"] = "เฝ้าดูแนวโน้ม"
        whitespace.sort(key=lambda w: (0 if w["label"] == "expand" else 1 if w["label"] == "trim" else 2,
                                       -w["revenue_per_sku"]))

    # ── 3. Revive dead winners ──────────────────────────────────────────────
    revive = []
    for code, mq in sku_month_qty.items():
        series = [mq.get(m, 0) for m in months]
        n = len(series)
        recent_2 = series[-2:] if n >= 2 else series
        if not (all(v == 0 for v in recent_2) and any(v > 0 for v in series)):
            continue
        alive = [v for v in series if v > 0]
        avg_alive = sum(alive) / len(alive) if alive else 0
        last_idx = max((i for i, v in enumerate(series) if v > 0), default=None)
        revive.append({
            "sku_code": code, "description": descr[code], "category": cat[code],
            "avg_monthly_when_alive": round(avg_alive),
            "total_qty": round(sum(series)),
            "last_active_month": months[last_idx] if last_idx is not None else None,
        })
    revive.sort(key=lambda r: r["avg_monthly_when_alive"], reverse=True)
    revive = revive[:20]

    return {"cross_branch_gaps": gaps, "category_whitespace": whitespace, "revive": revive,
            "month_range": [month_from or month, month_to or month]}


@app.get("/api/menu/opportunities")
@ttl_cache
def menu_opportunities(
    branch: Optional[str] = Query(None),
    month: Optional[str] = Query(None),
    month_from: Optional[str] = Query(None),
    month_to: Optional[str] = Query(None),
):
    """Data-driven menu opportunities (no COGS/margin — see _menu_opportunities_compute)."""
    return _menu_opportunities_compute(branch, month, month_from, month_to)


class MenuIdeasBody(BaseModel):
    branch:     Optional[str] = None
    month:      Optional[str] = None
    month_from: Optional[str] = None
    month_to:   Optional[str] = None


@app.post("/api/menu/ai-ideas", dependencies=[Depends(require_write_token)])
def menu_ai_ideas(body: MenuIdeasBody):
    """Draft NEW dish / promo CONCEPTS in Thai from the data-driven opportunity
    signals (cross-branch gaps, category whitespace, revive candidates). Uses real
    Claude when ANTHROPIC_API_KEY is set, otherwise a
    deterministic data-derived fallback (no crash, no SDK). Concepts are creative
    and NOT proven by sales — the UI labels them as such."""
    import os, json
    opp = _menu_opportunities_compute(body.branch, body.month, body.month_from, body.month_to)
    gaps = opp.get("cross_branch_gaps", [])[:8]
    ws   = [w for w in opp.get("category_whitespace", []) if w.get("label") == "expand"][:5]
    rev  = opp.get("revive", [])[:6]

    # Always-available deterministic fallback — builds usable ideas straight from data.
    def deterministic():
        ideas = []
        for w in ws[:2]:
            ideas.append({
                "title": f"เพิ่มเมนูใหม่ในหมวด {w['category']}",
                "rationale": f"หมวดนี้ทำรายได้/เมนูสูง ({w['revenue_per_sku']:,} บาท/เมนู) จากเพียง {w['n_skus']} เมนู — มีที่ว่างให้เติมความหลากหลาย",
                "builds_on": "category_whitespace",
            })
        for g in gaps[:2]:
            ideas.append({
                "title": f"ดัน {g['description']} ที่สาขา {g['branch']}",
                "rationale": f"สาขาอื่นขายดี (รวม {g['chain_qty']:,} ชิ้น) แต่สาขานี้{g['note']} — โอกาสเพิ่ม ~{g['potential_qty']:,} ชิ้น",
                "builds_on": "cross_branch_gaps",
            })
        for r in rev[:2]:
            ideas.append({
                "title": f"รื้อฟื้น/รีแบรนด์ {r['description']}",
                "rationale": f"เคยขายเฉลี่ย {r['avg_monthly_when_alive']:,} ชิ้น/เดือน แต่หยุดขายตั้งแต่ {r['last_active_month']} — กลับมาทำใหม่หรือพัฒนาเวอร์ชันใหม่",
                "builds_on": "revive",
            })
        if not ideas:
            ideas.append({"title": "ยังไม่มีสัญญาณข้อมูลเด่นในช่วงนี้",
                          "rationale": "ลองขยายช่วงเดือนหรือเลือกทุกสาขาเพื่อหาโอกาสเพิ่มเติม",
                          "builds_on": "none"})
        return ideas

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return {"ideas": deterministic(), "generated_by": "fallback"}

    signals = {"cross_branch_gaps": gaps, "category_whitespace": ws, "revive": rev}
    try:
        import urllib.request
        prompt = (
            "You are a menu-innovation advisor for a Thai buffet/à-la-carte restaurant chain. "
            "Based ONLY on these data signals, propose 4-6 concrete NEW dish or promotion ideas. "
            "Reply in THAI as a JSON array of objects with keys title, rationale, builds_on "
            "(builds_on = one of cross_branch_gaps / category_whitespace / revive). "
            "Each rationale: 1-2 sentences, specific, cite the numbers. Output JSON only, no prose. "
            f"Signals: {json.dumps(signals, ensure_ascii=False)}"
        )
        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages", method="POST",
            headers={"Content-Type": "application/json", "x-api-key": api_key,
                     "anthropic-version": "2023-06-01"},
            data=json.dumps({
                "model": "claude-haiku-4-5-20251001", "max_tokens": 900,
                "messages": [{"role": "user", "content": prompt}],
            }).encode("utf-8"),
        )
        with urllib.request.urlopen(req, timeout=25) as r:
            data = json.loads(r.read().decode("utf-8"))
            text = data.get("content", [{}])[0].get("text", "") or ""
        txt = text.strip()
        if txt.startswith("```"):
            txt = txt.strip("`")
            txt = txt[txt.find("["):]
        ideas = json.loads(txt[txt.find("["): txt.rfind("]") + 1])
        ideas = [i for i in ideas if isinstance(i, dict) and i.get("title")]
        if not ideas:
            return {"ideas": deterministic(), "generated_by": "fallback (empty AI reply)"}
        return {"ideas": ideas, "generated_by": "claude"}
    except Exception as e:
        return {"ideas": deterministic(), "generated_by": f"fallback ({e})"}


@app.get("/api/analysis/food_cost_trend")
@ttl_cache
def food_cost_trend(
    branch: Optional[str] = Query(None),
    group:  Optional[str] = Query(None),
    alert_threshold: float = Query(70.0, description="Food cost % alert threshold (actual Oshinei AYCE buffet 55-70%)"),
):
    """Monthly food cost % trend per branch. Returns months, cost%, alert threshold."""
    filters = ["b.name != 'ALL'"]
    params: dict = {}
    if branch:
        filters.append("b.name = :branch")
        params["branch"] = branch
    _group_filter(filters, params, group)
    where = "WHERE " + " AND ".join(filters)

    with get_db() as con:
        rows = con.execute(
            f"""
            SELECT sm.period_month,
                   b.name AS branch,
                   ROUND(SUM(sm.subtotal_ex_vat), 2)                              AS revenue,
                   ROUND(SUM({_cogs_case(ssm='sm', sk='sk', sc='sc')}), 2)         AS cost,
                   ROUND(SUM(sm.qty), 0)                                          AS servings,
                   COUNT(DISTINCT CASE WHEN {_unified_cogs_expr('sm', 'sk')} = 0
                                       THEN sk.sku_id END)                         AS uncosted
            FROM sales_sku_monthly sm
            JOIN branches b   ON b.branch_id   = sm.branch_id
            JOIN skus sk      ON sk.sku_id      = sm.sku_id
            LEFT JOIN sku_cost sc ON sc.sku_id  = sm.sku_id
            {where}
            GROUP BY sm.period_month, b.name
            ORDER BY sm.period_month, b.name
            """,
            params,
        ).fetchall()

    # Build per-branch monthly series
    branch_data: dict = {}
    all_months: list = []
    for r in rows:
        mo = r["period_month"]
        if mo not in all_months:
            all_months.append(mo)
        rev  = r["revenue"]  or 0
        cost = r["cost"]     or 0
        srv  = r["servings"] or 0
        pct  = round(cost / rev * 100, 1) if rev > 0 else None
        branch_data.setdefault(r["branch"], {})[mo] = {
            "revenue": rev, "cost": cost, "servings": srv, "cost_pct": pct,
            "uncosted": r["uncosted"] or 0,
        }

    # Overall (network) monthly
    overall: dict = {}
    for mo in all_months:
        tot_rev  = sum(bd.get(mo, {}).get("revenue", 0)  for bd in branch_data.values())
        tot_cost = sum(bd.get(mo, {}).get("cost", 0)     for bd in branch_data.values())
        tot_srv  = sum(bd.get(mo, {}).get("servings", 0) for bd in branch_data.values())
        overall[mo] = {
            "revenue":  tot_rev,
            "cost":     tot_cost,
            "servings": tot_srv,
            "cost_pct": round(tot_cost / tot_rev * 100, 1) if tot_rev > 0 else None,
            "rev_per_serving":  round(tot_rev / tot_srv, 2)  if tot_srv > 0 else None,
            "cost_per_serving": round(tot_cost / tot_srv, 2) if tot_srv > 0 else None,
        }

    # Latest month alert
    alerts = []
    if all_months:
        last_mo = all_months[-1]
        for br, mdata in branch_data.items():
            d = mdata.get(last_mo, {})
            pct = d.get("cost_pct")
            if pct is not None and pct > alert_threshold:
                alerts.append({
                    "branch": br, "period": last_mo,
                    "cost_pct": pct, "threshold": alert_threshold,
                    "message": f"⚠️ {br}: food cost {pct}% exceeds {alert_threshold}% threshold in {last_mo}",
                })

    return {
        "months": all_months,
        "overall": [{"period_month": mo, **v} for mo, v in overall.items()],
        "by_branch": {br: [{"period_month": mo, **mdata.get(mo, {"cost_pct": None})}
                            for mo in all_months]
                      for br, mdata in branch_data.items()},
        "alert_threshold": alert_threshold,
        "alerts": alerts,
    }


@app.get("/api/analysis/buffet_mix")
@ttl_cache
def buffet_mix(
    month:      Optional[str] = Query(None),
    month_from: Optional[str] = Query(None),
    month_to:   Optional[str] = Query(None),
    branch:     Optional[str] = Query(None),
):
    """
    Buffet price-tier mix from SKU sales.
    Detects tiers by matching '350','500','590','799' in category or sku_code or description.
    Also flags a la carte (อลาคาท / Ala Carte) separately.
    Returns tier revenue share, qty share, and trend.
    """
    filters = ["b.name != 'ALL'"]
    params: dict = {}
    if branch:
        filters.append("b.name = :branch")
        params["branch"] = branch
    _month_range_filter(filters, params, month_from, month_to, month, col="sm.period_month")
    where = "WHERE " + " AND ".join(filters)

    with get_db() as con:
        rows = con.execute(
            f"""
            SELECT sm.period_month,
                   b.name AS branch,
                   COALESCE(cat.name, '') AS category,
                   sk.sku_code,
                   COALESCE(sk.description, sk.sku_code) AS description,
                   ROUND(SUM(sm.qty), 2)                 AS qty,
                   ROUND(SUM(sm.subtotal_ex_vat), 2)       AS revenue
            FROM sales_sku_monthly sm
            JOIN branches b   ON b.branch_id  = sm.branch_id
            JOIN skus sk      ON sk.sku_id     = sm.sku_id
            LEFT JOIN categories cat ON cat.category_id = sk.category_id
            {where}
            GROUP BY b.name, sm.period_month, cat.name, sk.sku_code, sk.description
            ORDER BY sm.period_month
            """,
            params,
        ).fetchall()

    TIERS = ['350', '500', '590', '799']
    ALACARTE_KEYWORDS = ['อลาคาท', 'ala carte', 'a la carte', 'alacarte', 'อา ลา คาร์ท']
    # Buffet "charge" SKUs = a per-cover price (vs the B0xxx food-item rows that
    # are logged at 0 baht). In this data the whole adult buffet is booked under
    # one generic 'BUFFET' code, so we recover the tier from the realised price.
    CHARGE_CODES = {'BUFFET', 'BUFFET350', 'BUFFET500', 'BUFFET590', 'BUFFET799',
                    'B01', 'B02', 'B07', 'B11'}

    def price_tier(unit_price_ex_vat):
        """Map a per-head ex-VAT price to the nearest legacy tier (midpoints
        425/545/695 between 350·500·590·799)."""
        up = unit_price_ex_vat or 0
        if up < 425: return '350'
        if up < 545: return '500'
        if up < 695: return '590'
        return '799'

    def classify(cat, sku, desc, qty, rev):
        combined = f"{cat} {sku} {desc}".lower()
        for kw in ALACARTE_KEYWORDS:
            if kw.lower() in combined:
                return 'à la carte'
        if sku == 'BUFFETCHILD' or ('เด็ก' in combined and 'บุฟเฟ' in combined):
            return 'เด็ก (Child)'
        for t in TIERS:                       # explicit tier in code/desc wins
            if t in combined:
                return t
        is_buffet_charge = (sku in CHARGE_CODES) or ('บุฟเฟ' in combined) or ('buffet' in combined)
        if is_buffet_charge:
            if qty and rev and rev > 0:        # priced buffet cover → bucket by price
                return price_tier(rev / qty)
            return None                        # 0-baht buffet food item → not a price level
        # Non-buffet regular menu / à-la-carte food: not a buffet price level —
        # exclude so this chart is purely the buffet price-tier mix (covers & ฿).
        return None

    # Aggregate
    totals: dict = {}   # tier -> {qty, revenue}
    monthly: dict = {}  # period_month -> tier -> revenue

    for r in rows:
        tier = classify(r["category"], r["sku_code"], r["description"],
                        r["qty"] or 0, r["revenue"] or 0)
        if tier is None:
            continue
        t = totals.setdefault(tier, {"qty": 0.0, "revenue": 0.0})
        t["qty"]     += r["qty"]     or 0
        t["revenue"] += r["revenue"] or 0
        mo = monthly.setdefault(r["period_month"], {})
        mo[tier] = mo.get(tier, 0.0) + (r["revenue"] or 0)

    total_rev = sum(v["revenue"] for v in totals.values()) or 1
    total_qty = sum(v["qty"]     for v in totals.values()) or 1

    summary = [
        {
            "tier": tier,
            "qty": round(vals["qty"], 1),
            "revenue": round(vals["revenue"], 2),
            "revenue_pct": round(vals["revenue"] / total_rev * 100, 1),
            "qty_pct":     round(vals["qty"]     / total_qty * 100, 1),
        }
        for tier, vals in sorted(totals.items(),
                                  key=lambda x: -x[1]["revenue"])
    ]

    all_months = sorted(monthly.keys())
    all_tiers  = list(totals.keys())
    trend = [
        {"period_month": mo,
         **{t: round(monthly[mo].get(t, 0), 2) for t in all_tiers}}
        for mo in all_months
    ]

    return {"summary": summary, "trend": trend, "tiers": all_tiers, "months": all_months}


@ttl_cache
def table_turnover(
    month:      Optional[str] = Query(None),
    month_from: Optional[str] = Query(None),
    month_to:   Optional[str] = Query(None),
    branch:     Optional[str] = Query(None),
):
    """
    Estimate table turnover and dwell time from sales_transaction.
    - turns_per_day: avg number of bills per table per operating day
    - est_dwell_min: median gap (minutes) between consecutive bills at the same table on the same day
    - peak_hour distribution of first bills
    """
    filters = ["b.name != 'ALL'", "TRIM(COALESCE(st.table_no,'')) != ''", "st.source = 'Easy'"]
    params: dict = {}
    if branch:
        filters.append("b.name = :branch")
        params["branch"] = branch
    _month_range_filter(filters, params, month_from, month_to, month,
                        col="strftime('%Y-%m', st.sold_at)")
    where = "WHERE " + " AND ".join(filters)

    with get_db() as con:
        # Bills per table per day per branch
        day_rows = con.execute(
            f"""
            SELECT b.name AS branch,
                   st.table_no,
                   DATE(st.sold_at)                     AS day,
                   COUNT(*)                              AS bill_count,
                   MIN(st.sold_at)                       AS first_bill,
                   MAX(st.sold_at)                       AS last_bill,
                   ROUND(SUM(st.total_inc_vat) / 1.07, 2)      AS day_revenue,
                   SUM(st.customer_count)               AS day_pax
            FROM sales_transaction st
            JOIN branches b ON b.branch_id = st.branch_id
            {where}
            GROUP BY b.name, st.table_no, DATE(st.sold_at)
            ORDER BY b.name, day
            """,
            params,
        ).fetchall()

    if not day_rows:
        return {"available": False}

    from datetime import datetime as _dt

    # Per-branch aggregations
    branch_stats: dict = {}
    all_gaps: list = []

    for r in day_rows:
        br = r["branch"]
        bs = branch_stats.setdefault(br, {
            "total_table_days": 0, "total_bills": 0,
            "total_revenue": 0.0, "total_pax": 0,
            "gaps_min": [], "turns_list": [],
        })
        bs["total_table_days"] += 1
        bs["total_bills"]      += r["bill_count"]
        bs["total_revenue"]    += r["day_revenue"] or 0
        bs["total_pax"]        += r["day_pax"] or 0
        bs["turns_list"].append(r["bill_count"])

        # Gap estimate: if >=2 bills, (last_bill - first_bill) / (bills - 1) = avg gap
        if r["bill_count"] >= 2:
            try:
                t1 = _dt.fromisoformat(r["first_bill"])
                t2 = _dt.fromisoformat(r["last_bill"])
                span_min = (t2 - t1).total_seconds() / 60
                avg_gap  = span_min / (r["bill_count"] - 1)
                if 0 < avg_gap < 600:  # ignore gaps > 10 hours (data noise)
                    bs["gaps_min"].append(avg_gap)
                    all_gaps.append(avg_gap)
            except Exception:
                pass

    def _median(lst):
        if not lst: return None
        s = sorted(lst)
        n = len(s)
        return round(s[n // 2] if n % 2 else (s[n//2-1]+s[n//2])/2, 1)

    result_branches = []
    for br, bs in sorted(branch_stats.items(), key=lambda x: -x[1]["total_revenue"]):
        tds  = bs["total_table_days"] or 1
        result_branches.append({
            "branch":            br,
            "avg_turns_per_day": round(bs["total_bills"] / tds, 2),
            "est_dwell_min":     _median(bs["gaps_min"]),
            "avg_revenue_per_table_day": round(bs["total_revenue"] / tds, 2),
            "avg_pax_per_table_day":     round(bs["total_pax"] / tds, 2),
        })

    overall_turns  = round(sum(r["avg_turns_per_day"] for r in result_branches) / max(len(result_branches), 1), 2)
    overall_dwell  = _median(all_gaps)

    return {
        "available":     True,
        "overall": {
            "avg_turns_per_day": overall_turns,
            "est_dwell_min":     overall_dwell,
        },
        "by_branch": result_branches,
    }


# ---------------------------------------------------------------------------
# GET /api/buffet/gp
# Gross Profit from BoM — revenue vs actual item-level cost for the period
# ---------------------------------------------------------------------------
@app.get("/api/buffet/gp")
@ttl_cache
def buffet_gp(
    branch:      Optional[str] = Query(None),
    month_from:  Optional[str] = Query(None),
    month_to:    Optional[str] = Query(None),
    granularity: str           = Query("month"),
):
    """Buffet Gross Profit from BoM cost.

    Revenue = sum(subtotal_ex_vat) from sales_sku_monthly.
    BoM Cost = sum(qty * unit_cost) for SKUs that have cost data.
    For week/day granularity, BoM cost is pro-rated by daily revenue fraction
    (since SKU data is monthly-only — no day-level SKU breakdown available).

    Returns: period_total, series (bucketed), by_branch, top_cost_skus, waterfall.
    """
    if granularity not in ("day", "week", "month"):
        granularity = "month"

    DRINK_CATS = ("Oshinei ALCOHOL", "Oshinei DRINKING", "Oshinei SOFT DRINK",
                  "(YAKI)SOFT DRINK")

    # ── Build SKU-level filters (period_month column) ─────────────────────
    sku_filters: list = ["b.name != 'ALL'"]
    sku_params:  dict = {}
    if branch:
        sku_filters.append("b.name = :branch")
        sku_params["branch"] = branch
    _month_range_filter(sku_filters, sku_params, month_from, month_to, None,
                        col="sm.period_month")
    where_sku = "WHERE " + " AND ".join(sku_filters)

    # ── Build transaction-level filters ───────────────────────────────────
    tx_filters: list = ["b.name != 'ALL'", "st.source = 'Easy'"]
    tx_params:  dict = {}
    if branch:
        tx_filters.append("b.name = :branch")
        tx_params["branch"] = branch
    _month_range_filter(tx_filters, tx_params, month_from, month_to, None,
                        col="SUBSTR(st.sold_at,1,7)")
    where_tx = "WHERE " + " AND ".join(tx_filters)

    with get_db() as con:
        # BoM cost rule (Phase 0 fix):
        #   Exclude Easy tier SKUs (BUFFET350/500/590/799/CHILD) — their cost is
        #   captured at item level (B0xxx rows logged with qty>0, revenue=0).
        #   Including tier unit_cost would double-count.
        bom_cost_sql = _cogs_case(ssm="sm")
        # Uncovered = item SKU without cost (Easy tier SKUs are intentionally
        # treated as "covered via items" so they don't count as uncovered).
        uncovered_sql = (
            f"CASE WHEN sm.source = 'Easy' AND sk.sku_code IN {_EASY_TIER_CODES_SQL} THEN NULL "
            f"     WHEN sc.unit_cost IS NULL THEN sm.sku_id ELSE NULL END"
        )

        # ── 1. Monthly BoM cost by (branch, period_month, category_group) ─
        monthly_bom_rows = con.execute(f"""
            SELECT
                b.name           AS branch,
                sm.period_month  AS m,
                c.name           AS cat,
                sk.sku_code      AS sku_code,
                SUM(sm.qty)      AS qty,
                SUM(sm.subtotal_ex_vat) AS rev,
                SUM({bom_cost_sql}) AS bom_cost,
                COUNT(DISTINCT {uncovered_sql}) AS uncovered
            FROM sales_sku_monthly sm
            JOIN branches   b  ON b.branch_id   = sm.branch_id
            JOIN skus       sk ON sk.sku_id      = sm.sku_id
            JOIN categories c  ON c.category_id  = sk.category_id
            LEFT JOIN sku_cost sc ON sc.sku_id   = sm.sku_id
            {where_sku}
            GROUP BY b.name, sm.period_month, c.name, sk.sku_code
            ORDER BY b.name, sm.period_month
        """, sku_params).fetchall()

        # ── 2. Daily revenue from transactions (for day/week pro-rate) ────
        daily_tx_rows = con.execute(f"""
            SELECT
                b.name                           AS branch,
                DATE(st.sold_at)                 AS d,
                SUBSTR(st.sold_at, 1, 7)         AS m,
                SUM(st.customer_count)           AS covers,
                SUM(COALESCE(st.food_subtotal,0)
                  + COALESCE(st.drink_subtotal,0)
                  + COALESCE(st.other_subtotal,0)) AS rev_tx
            FROM sales_transaction st
            JOIN branches b ON b.branch_id = st.branch_id
            {where_tx}
            GROUP BY b.name, DATE(st.sold_at)
        """, tx_params).fetchall()

    # ── 3. Aggregate monthly totals (branch × month) ──────────────────────
    from collections import defaultdict

    # monthly_agg[branch][month] = {rev, bom_cost, bom_buffet, bom_food, bom_bev, rev_buffet, rev_food, rev_bev, uncovered, sku details}
    monthly_agg: dict = defaultdict(lambda: defaultdict(lambda: {
        "rev": 0.0, "bom_cost": 0.0,
        "bom_buffet": 0.0, "bom_food": 0.0, "bom_bev": 0.0,
        "rev_buffet": 0.0, "rev_food": 0.0, "rev_bev": 0.0,
        "uncovered": 0,
    }))
    # sku-level detail for top_cost_skus
    sku_agg: dict = defaultdict(lambda: {
        "description": "", "category": "", "qty": 0.0,
        "bom_cost": 0.0, "rev": 0.0,
    })

    for r in monthly_bom_rows:
        br, m, cat = r["branch"], r["m"], r["cat"]
        a = monthly_agg[br][m]
        a["rev"]      += r["rev"] or 0.0
        a["bom_cost"] += r["bom_cost"] or 0.0
        a["uncovered"] = max(a["uncovered"], r["uncovered"] or 0)

        is_buffet_tier = r["sku_code"] and r["sku_code"].startswith("BUFFET")
        is_drink = cat in DRINK_CATS

        if is_buffet_tier:
            a["bom_buffet"] += r["bom_cost"] or 0.0
            a["rev_buffet"] += r["rev"] or 0.0
        elif is_drink:
            a["bom_bev"] += r["bom_cost"] or 0.0
            a["rev_bev"] += r["rev"] or 0.0
        else:
            a["bom_food"] += r["bom_cost"] or 0.0
            a["rev_food"] += r["rev"] or 0.0

        sc = sku_agg[r["sku_code"]]
        sc["description"] = r["sku_code"]  # placeholder; description not in this query
        sc["category"]    = cat
        sc["qty"]         += r["qty"] or 0.0
        sc["bom_cost"]    += r["bom_cost"] or 0.0
        sc["rev"]         += r["rev"] or 0.0

    # ── 4. daily_tx index: [branch][month][date] ──────────────────────────
    daily_tx: dict = defaultdict(lambda: defaultdict(dict))
    monthly_rev_tx: dict = defaultdict(lambda: defaultdict(float))
    monthly_covers_tx: dict = defaultdict(lambda: defaultdict(int))

    for r in daily_tx_rows:
        daily_tx[r["branch"]][r["m"]][r["d"]] = r["rev_tx"] or 0.0
        monthly_rev_tx[r["branch"]][r["m"]]  += r["rev_tx"] or 0.0
        monthly_covers_tx[r["branch"]][r["m"]] += r["covers"] or 0

    # ── 5. Build series ───────────────────────────────────────────────────
    def week_bucket(date_str: str) -> str:
        from datetime import date as dt
        d = dt.fromisoformat(date_str)
        iso = d.isocalendar()
        return f"{iso[0]}-W{iso[1]:02d}"

    if granularity == "month":
        bucket_agg: dict = defaultdict(lambda: {"rev": 0.0, "bom_cost": 0.0, "covers": 0})
        for br in monthly_agg:
            for m, a in monthly_agg[br].items():
                b = bucket_agg[m]
                b["rev"]      += a["rev"]
                b["bom_cost"] += a["bom_cost"]
                b["covers"]   += monthly_covers_tx[br][m]
        series = []
        for bucket in sorted(bucket_agg.keys()):
            b = bucket_agg[bucket]
            gp = round(b["rev"] - b["bom_cost"], 2)
            series.append({
                "bucket":   bucket,
                "revenue":  round(b["rev"], 2),
                "bom_cost": round(b["bom_cost"], 2),
                "gp":       gp,
                "margin_pct": round(gp / b["rev"] * 100, 2) if b["rev"] else None,
                "covers":   b["covers"],
            })
    else:
        # Day or week: pro-rate monthly BoM by daily revenue fraction
        # fix F7: pre-index covers to avoid O(days × tx_rows) linear rescan
        daily_covers_idx = {(r2["branch"], r2["d"]): (r2["covers"] or 0) for r2 in daily_tx_rows}
        daily_bucket: dict = defaultdict(lambda: {"rev": 0.0, "bom_cost": 0.0, "covers": 0})
        for br, months in daily_tx.items():
            for m, days in months.items():
                m_rev_tx = monthly_rev_tx[br][m]
                m_bom    = sum(monthly_agg[br][mm]["bom_cost"] for mm in monthly_agg[br] if mm == m)
                for d, d_rev_tx in days.items():
                    fraction   = (d_rev_tx / m_rev_tx) if m_rev_tx else 0.0
                    d_bom_cost = m_bom * fraction
                    bucket = d if granularity == "day" else week_bucket(d)
                    db = daily_bucket[bucket]
                    db["rev"]      += d_rev_tx
                    db["bom_cost"] += d_bom_cost
                    db["covers"]   += daily_covers_idx.get((br, d), 0)
        series = []
        for bucket in sorted(daily_bucket.keys()):
            b = daily_bucket[bucket]
            gp = round(b["rev"] - b["bom_cost"], 2)
            series.append({
                "bucket":     bucket,
                "revenue":    round(b["rev"], 2),
                "bom_cost":   round(b["bom_cost"], 2),
                "gp":         gp,
                "margin_pct": round(gp / b["rev"] * 100, 2) if b["rev"] else None,
                "covers":     b["covers"],
            })

    # ── 6. Period totals (all branches, whole selected period) ───────────
    period_rev      = sum(s["revenue"]  for s in series)
    period_bom      = sum(s["bom_cost"] for s in series)
    period_covers   = sum(s["covers"]   for s in series) if granularity == "month" else \
                      sum(monthly_covers_tx[br][m] for br in monthly_covers_tx for m in monthly_covers_tx[br])
    period_gp       = round(period_rev - period_bom, 2)
    period_margin   = round(period_gp / period_rev * 100, 2) if period_rev else None
    cost_per_cover  = round(period_bom / period_covers, 2) if period_covers else None

    # ── 7. By branch (whole period) ───────────────────────────────────────
    branch_totals: dict = defaultdict(lambda: {
        "rev": 0.0, "bom_cost": 0.0, "covers": 0
    })
    for br in monthly_agg:
        for m, a in monthly_agg[br].items():
            bt = branch_totals[br]
            bt["rev"]      += a["rev"]
            bt["bom_cost"] += a["bom_cost"]
            bt["covers"]   += monthly_covers_tx[br][m]

    network_avg_margin = period_margin or 0.0
    by_branch = []
    for br, bt in sorted(branch_totals.items(), key=lambda x: x[1]["rev"], reverse=True):
        gp_br   = round(bt["rev"] - bt["bom_cost"], 2)
        margin  = round(gp_br / bt["rev"] * 100, 2) if bt["rev"] else None
        gp_cov  = round(gp_br / bt["covers"], 2) if bt["covers"] else None
        diff_pp = round(margin - network_avg_margin, 2) if margin is not None else None
        by_branch.append({
            "branch":       br,
            "covers":       bt["covers"],
            "revenue":      round(bt["rev"], 2),
            "bom_cost":     round(bt["bom_cost"], 2),
            "gp":           gp_br,
            "gp_per_cover": gp_cov,
            "margin_pct":   margin,
            "vs_network_pp": diff_pp,
        })

    # ── 8. Top 20 cost-driver SKUs (exclude tier seats) ──────────────────
    total_bom = period_bom or 1.0
    top_cost_skus = sorted(
        [{"sku_code": k, **v} for k, v in sku_agg.items()
         if v["bom_cost"] > 0 and k not in _ALL_TIER_CODES and not k.startswith("BUFFET")],
        key=lambda x: x["bom_cost"], reverse=True
    )[:20]
    for s in top_cost_skus:
        s["bom_cost"]  = round(s["bom_cost"], 2)
        s["rev"]       = round(s["rev"], 2)
        s["qty"]       = round(s["qty"], 2)
        s["cost_pct"]  = round(s["bom_cost"] / total_bom * 100, 2)

    # ── 9. Waterfall (whole period, all branches) ─────────────────────────
    total_rev_buffet = sum(monthly_agg[br][m]["rev_buffet"]  for br in monthly_agg for m in monthly_agg[br])
    total_rev_food   = sum(monthly_agg[br][m]["rev_food"]    for br in monthly_agg for m in monthly_agg[br])
    total_rev_bev    = sum(monthly_agg[br][m]["rev_bev"]     for br in monthly_agg for m in monthly_agg[br])
    total_bom_buffet = sum(monthly_agg[br][m]["bom_buffet"]  for br in monthly_agg for m in monthly_agg[br])
    total_bom_food   = sum(monthly_agg[br][m]["bom_food"]    for br in monthly_agg for m in monthly_agg[br])
    total_bom_bev    = sum(monthly_agg[br][m]["bom_bev"]     for br in monthly_agg for m in monthly_agg[br])

    waterfall = {
        "revenue":       round(period_rev, 2),
        "rev_buffet":    round(total_rev_buffet, 2),
        "rev_food":      round(total_rev_food, 2),
        "rev_bev":       round(total_rev_bev, 2),
        "bom_buffet":    round(total_bom_buffet, 2),
        "bom_food":      round(total_bom_food, 2),
        "bom_bev":       round(total_bom_bev, 2),
        "bom_total":     round(period_bom, 2),
        "gp":            round(period_gp, 2),
    }

    return {
        "period_total": {
            "revenue":       round(period_rev, 2),
            "bom_cost":      round(period_bom, 2),
            "gp":            period_gp,
            "margin_pct":    period_margin,
            "covers":        period_covers,
            "cost_per_cover": cost_per_cover,
        },
        "granularity": granularity,
        "series":       series,
        "by_branch":    by_branch,
        "top_cost_skus": top_cost_skus,
        "waterfall":    waterfall,
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


# ---------------------------------------------------------------------------
# GET /api/buffet/menu-cost-efficiency   (Phase 6, 2026-06-02)
# Per-dish: servings, % of total servings, BoM cost, % of total cost,
# cost/serving. The key buffet question: which dishes are served a LOT and cost a
# LOT (→ re-engineer recipe / portion to cut cost), vs served a lot but cheap
# (→ promote). Per-branch capable.
# ---------------------------------------------------------------------------
@app.get("/api/buffet/menu-cost-efficiency")
@ttl_cache
def buffet_menu_cost_efficiency(
    branch:     Optional[str] = Query(None),
    month:      Optional[str] = Query(None),
    month_from: Optional[str] = Query(None),
    month_to:   Optional[str] = Query(None),
    limit:      int           = Query(80, ge=10, le=300),
):
    """For every food dish: servings (qty), cost (unified COGS), and each as a %
    of the total — so you can target HIGH-volume × HIGH-cost dishes for cost
    reduction. Buffet tier rows excluded (cost lives on the item dishes)."""
    filters = ["b.name != 'ALL'", "ssm.source = 'Easy'", _FOOD_CATEGORY_FILTER]
    params: dict = {}
    if branch:
        filters.append("b.name = :branch"); params["branch"] = branch
    _month_range_filter(filters, params, month_from, month_to, month, col="ssm.period_month")
    where = "WHERE " + " AND ".join(filters)
    with get_db() as con:
        rows = con.execute(f"""
            SELECT sk.sku_code, COALESCE(sk.description, sk.sku_code) AS description,
                   c.name AS category,
                   SUM(ssm.qty)               AS servings,
                   SUM({_unified_cogs_expr('ssm')}) AS cost,
                   SUM(ssm.subtotal_ex_vat)   AS revenue,
                   CASE
                     WHEN EXISTS (SELECT 1 FROM bom_ingredients bi WHERE bi.sku_id = sk.sku_id) THEN 'bom'
                     -- alias-resolved recipe, matching _unified_cogs_expr (see menu-cost-coverage)
                     WHEN EXISTS (SELECT 1 FROM bom_sku_alias a JOIN bom_ingredients bi2
                                    ON bi2.sku_id = a.resolved_sku_id
                                  WHERE a.bom_sku_code = sk.sku_code) THEN 'bom'
                     WHEN EXISTS (SELECT 1 FROM sku_cost sc WHERE sc.sku_id = sk.sku_id) THEN 'sku_cost'
                     ELSE 'none'
                   END AS cost_source
            FROM sales_sku_monthly ssm
            JOIN branches b   ON b.branch_id   = ssm.branch_id
            JOIN skus sk      ON sk.sku_id      = ssm.sku_id
            JOIN categories c ON c.category_id  = sk.category_id
            {where}
            GROUP BY sk.sku_id
            HAVING SUM(ssm.qty) > 0
        """, params).fetchall()
    tot_serv = sum((r["servings"] or 0) for r in rows) or 1
    tot_cost = sum((r["cost"] or 0) for r in rows) or 1
    items = []
    for r in rows:
        serv = r["servings"] or 0; cost = r["cost"] or 0
        serv_pct = serv / tot_serv * 100
        cost_pct = cost / tot_cost * 100
        items.append({
            "sku_code": r["sku_code"], "description": r["description"], "category": r["category"],
            "servings": round(serv, 0), "serving_pct": round(serv_pct, 2),
            "cost": round(cost, 2), "cost_pct": round(cost_pct, 2),
            "cost_per_serving": round(cost / serv, 2) if serv else 0,
            "revenue": round(r["revenue"] or 0, 2),
            # priority = how much this dish drives cost relative to volume.
            # high cost_pct AND high serving_pct = top re-engineering target.
            "reduce_priority": round(cost_pct, 2),
            "is_high_volume": serv_pct >= (100 / max(1, len(rows)) * 2),  # >2× avg share
            "cost_source": r["cost_source"],
        })
    items.sort(key=lambda x: -x["cost_pct"])
    return {
        "branch": branch or "ทุกสาขา",
        "total_servings": round(tot_serv, 0),
        "total_cost": round(tot_cost, 2),
        "items": items[:limit],
        "n_dishes": len(rows),
    }


# ---------------------------------------------------------------------------
# GET /api/buffet/menu-cost-trend   (Phase 6, 2026-06-09)
# Monthly cost-share trend for the top-N food dishes (by total BoM/COGS cost):
# each dish's cost expressed as a % of total dish cost in that period_month, so
# dishes whose cost share is creeping up can be spotted and re-engineered.
# Powers the "ต้นทุนรายเดือน (top-8)" line chart on the Menu Efficiency tab.
# ---------------------------------------------------------------------------
@app.get("/api/buffet/menu-cost-trend")
@ttl_cache
def buffet_menu_cost_trend(
    branch:     Optional[str] = Query(None),
    month_from: Optional[str] = Query(None),
    month_to:   Optional[str] = Query(None),
    top_n:      int           = Query(8, ge=1, le=30),
):
    """Per-dish monthly cost share (cost / total dish cost that month, %).
    Returns the sorted month axis plus the top-N dishes by total cost across
    the range. Buffet tier rows excluded (cost lives on the item dishes)."""
    filters = ["b.name != 'ALL'", "ssm.source = 'Easy'", _FOOD_CATEGORY_FILTER]
    params: dict = {}
    if branch:
        filters.append("b.name = :branch"); params["branch"] = branch
    _month_range_filter(filters, params, month_from, month_to, None, col="ssm.period_month")
    where = "WHERE " + " AND ".join(filters)
    with get_db() as con:
        rows = con.execute(f"""
            SELECT sk.sku_code, COALESCE(sk.description, sk.sku_code) AS description,
                   ssm.period_month AS month,
                   SUM(ssm.qty)                     AS servings,
                   SUM({_unified_cogs_expr('ssm')}) AS cost
            FROM sales_sku_monthly ssm
            JOIN branches b   ON b.branch_id   = ssm.branch_id
            JOIN skus sk      ON sk.sku_id      = ssm.sku_id
            JOIN categories c ON c.category_id  = sk.category_id
            {where}
            GROUP BY sk.sku_id, ssm.period_month
            HAVING SUM(ssm.qty) > 0
        """, params).fetchall()
    # total dish cost per month → each dish's cost share
    month_tot: dict = {}
    for r in rows:
        month_tot[r["month"]] = month_tot.get(r["month"], 0) + (r["cost"] or 0)
    dishes: dict = {}
    for r in rows:
        d = dishes.setdefault(r["sku_code"], {
            "sku_code": r["sku_code"], "description": r["description"],
            "total_cost": 0.0, "monthly": [],
        })
        cost = r["cost"] or 0
        tot  = month_tot.get(r["month"]) or 1
        d["total_cost"] += cost
        d["monthly"].append({
            "month": r["month"], "cost": round(cost, 2),
            "cost_pct": round(cost / tot * 100, 2),
        })
    months = sorted(month_tot.keys())
    top = sorted(dishes.values(), key=lambda x: -x["total_cost"])[:top_n]
    for d in top:
        d["monthly"].sort(key=lambda x: x["month"])
        d["total_cost"] = round(d["total_cost"], 2)
    return {
        "branch": branch or "ทุกสาขา",
        "months": months,
        "dishes": top,
        "n_dishes": len(dishes),
    }


# ---------------------------------------------------------------------------
# GET /api/buffet/menu-cost-by-branch   (Phase 6, 2026-06-09)
# For ONE dish (sku_code): per-branch servings, cost/serving, cost share and
# food-cost rate — so a single dish's portioning/cost can be benchmarked across
# branches. Drill-down from the Menu Efficiency table.
# ---------------------------------------------------------------------------
@app.get("/api/buffet/menu-cost-by-branch")
@ttl_cache
def buffet_menu_cost_by_branch(
    sku_code:   str           = Query(...),
    month_from: Optional[str] = Query(None),
    month_to:   Optional[str] = Query(None),
):
    """Per-branch cost efficiency for a single dish (cost/serving, share, FC%)."""
    filters = ["b.name != 'ALL'", "ssm.source = 'Easy'", "sk.sku_code = :sku"]
    params: dict = {"sku": sku_code}
    _month_range_filter(filters, params, month_from, month_to, None, col="ssm.period_month")
    where = "WHERE " + " AND ".join(filters)
    with get_db() as con:
        rows = con.execute(f"""
            SELECT b.name AS branch,
                   SUM(ssm.qty)                     AS servings,
                   SUM({_unified_cogs_expr('ssm')}) AS cost,
                   SUM(ssm.subtotal_ex_vat)         AS revenue
            FROM sales_sku_monthly ssm
            JOIN branches b   ON b.branch_id   = ssm.branch_id
            JOIN skus sk      ON sk.sku_id      = ssm.sku_id
            {where}
            GROUP BY b.branch_id
            HAVING SUM(ssm.qty) > 0
        """, params).fetchall()
    tot_cost = sum((r["cost"] or 0) for r in rows) or 1
    branches = []
    for r in rows:
        serv = r["servings"] or 0; cost = r["cost"] or 0; rev = r["revenue"] or 0
        branches.append({
            "branch": r["branch"],
            "servings": round(serv, 0),
            "cost": round(cost, 2),
            "cost_per_serving": round(cost / serv, 2) if serv else 0,
            "cost_pct_of_total": round(cost / tot_cost * 100, 2),
            "food_cost_rate": round(cost / rev * 100, 2) if rev else None,
        })
    branches.sort(key=lambda x: x["cost_per_serving"])
    return {"sku_code": sku_code, "branches": branches, "n_branches": len(branches)}


# ---------------------------------------------------------------------------
# GET /api/buffet/menu-cost-coverage   (Phase 6, 2026-06-08)
# Recipe-coverage stats: how many dishes have BoM, flat sku_cost, or no cost
# data at all. Helps identify engineering potential (add recipes) and blind
# spots (no cost data at all). Returns summary + actionable top-10 lists.
# ---------------------------------------------------------------------------
@app.get("/api/buffet/menu-cost-coverage")
@ttl_cache
def buffet_menu_cost_coverage(
    branch:     Optional[str] = Query(None),
    month_from: Optional[str] = Query(None),
    month_to:   Optional[str] = Query(None),
):
    """Recipe coverage: for every food SKU with sales, classify into bom /
    sku_cost / none buckets, then return summary stats and top-10 action lists."""
    filters = ["b.name != 'ALL'", "ssm.source = 'Easy'", _FOOD_CATEGORY_FILTER]
    params: dict = {}
    if branch:
        filters.append("b.name = :branch"); params["branch"] = branch
    _month_range_filter(filters, params, month_from, month_to, None, col="ssm.period_month")
    where = "WHERE " + " AND ".join(filters)

    with get_db() as con:
        rows = con.execute(f"""
            SELECT sk.sku_id, sk.sku_code,
                   COALESCE(sk.description, sk.sku_code) AS description,
                   c.name AS category,
                   SUM(ssm.qty)             AS servings,
                   SUM({_unified_cogs_expr('ssm')}) AS cost,
                   SUM(ssm.subtotal_ex_vat) AS revenue,
                   CASE
                     WHEN EXISTS (SELECT 1 FROM bom_ingredients bi WHERE bi.sku_id = sk.sku_id) THEN 'bom'
                     -- alias-resolved recipe (bom_sku_alias → bom_ingredients): _unified_cogs_expr
                     -- costs these as real BoM, so the classifier must too or the coverage
                     -- donut labels a costed SKU 'none' while it contributes COGS.
                     WHEN EXISTS (SELECT 1 FROM bom_sku_alias a JOIN bom_ingredients bi2
                                    ON bi2.sku_id = a.resolved_sku_id
                                  WHERE a.bom_sku_code = sk.sku_code) THEN 'bom'
                     WHEN EXISTS (SELECT 1 FROM sku_cost sc2 WHERE sc2.sku_id = sk.sku_id) THEN 'sku_cost'
                     ELSE 'none'
                   END AS bucket
            FROM sales_sku_monthly ssm
            JOIN branches b   ON b.branch_id  = ssm.branch_id
            JOIN skus sk      ON sk.sku_id     = ssm.sku_id
            JOIN categories c ON c.category_id = sk.category_id
            {where}
            GROUP BY sk.sku_id
            HAVING SUM(ssm.qty) > 0
        """, params).fetchall()

    summary: dict = {
        "bom":      {"count": 0, "cost": 0.0, "revenue": 0.0},
        "sku_cost": {"count": 0, "cost": 0.0, "revenue": 0.0},
        "none":     {"count": 0, "cost": 0.0, "revenue": 0.0},
    }
    high_cost_no_bom: list = []   # sku_cost bucket, high cost — CAN be improved
    high_revenue_uncosted: list = []  # none bucket, high revenue — blind spots

    for r in rows:
        bkt  = r["bucket"]
        cost = float(r["cost"] or 0)
        rev  = float(r["revenue"] or 0)
        if bkt not in summary:
            bkt = "none"
        summary[bkt]["count"]   += 1
        summary[bkt]["cost"]    += cost
        summary[bkt]["revenue"] += rev
        item = {
            "sku_code": r["sku_code"],
            "description": r["description"],
            "category": r["category"],
            "servings": round(float(r["servings"] or 0), 0),
            "cost": round(cost, 2),
            "revenue": round(rev, 2),
            "bucket": bkt,
        }
        if bkt == "sku_cost":
            high_cost_no_bom.append(item)
        elif bkt == "none":
            high_revenue_uncosted.append(item)

    # Sort and cap at 10
    high_cost_no_bom.sort(key=lambda x: -x["cost"])
    high_revenue_uncosted.sort(key=lambda x: -x["revenue"])
    high_cost_no_bom      = high_cost_no_bom[:10]
    high_revenue_uncosted = high_revenue_uncosted[:10]

    # Add cost_pct to summary buckets
    tot_cost = sum(v["cost"] for v in summary.values()) or 1
    for bkt, s in summary.items():
        s["cost_pct"] = round(s["cost"] / tot_cost * 100, 1)

    return {
        "summary":              summary,
        "high_cost_no_bom":     high_cost_no_bom,
        "high_revenue_uncosted": high_revenue_uncosted,
    }


# ---------------------------------------------------------------------------
# GET /api/buffet/portion-velocity   🟡3
# qty / cover per SKU per month — detect over-ordering trends
# ---------------------------------------------------------------------------
@app.get("/api/buffet/portion-velocity")
@ttl_cache
def buffet_portion_velocity(
    branch:     Optional[str] = Query(None),
    month_from: Optional[str] = Query(None),
    month_to:   Optional[str] = Query(None),
    top_n:      int           = Query(50, ge=5, le=100),
):
    """Per-SKU qty/cover trend. Covers from transactions; qty from SKU monthly."""
    TIER_CODES = ('BUFFET','BUFFET350','BUFFET500','BUFFET590','BUFFET799','BUFFETCHILD',
                  'B01','B02','B07','B11')
    tier_named = {f"tc{i}": v for i, v in enumerate(TIER_CODES)}
    tier_ph    = ", ".join(f":tc{i}" for i in range(len(TIER_CODES)))

    sku_filters: list = ["b.name != 'ALL'", f"sk.sku_code NOT IN ({tier_ph})"]
    sku_params:  dict = {**tier_named}
    if branch:
        sku_filters.append("b.name = :branch")
        sku_params["branch"] = branch
    _month_range_filter(sku_filters, sku_params, month_from, month_to, None,
                        col="sm.period_month")
    where_sku = "WHERE " + " AND ".join(sku_filters)

    tx_filters: list = ["b.name != 'ALL'", "st.source='Easy'"]
    tx_params:  dict = {}
    if branch:
        tx_filters.append("b.name = :branch")
        tx_params["branch"] = branch
    _month_range_filter(tx_filters, tx_params, month_from, month_to, None,
                        col="SUBSTR(st.sold_at,1,7)")
    where_tx = "WHERE " + " AND ".join(tx_filters)

    with get_db() as con:
        sku_rows = con.execute(f"""
            SELECT sm.period_month AS m,
                   sk.sku_code, COALESCE(sk.description, sk.sku_code) AS description,
                   c.name AS category,
                   SUM(sm.qty) AS qty
            FROM sales_sku_monthly sm
            JOIN branches   b  ON b.branch_id   = sm.branch_id
            JOIN skus       sk ON sk.sku_id      = sm.sku_id
            JOIN categories c  ON c.category_id  = sk.category_id
            {where_sku}
            GROUP BY sm.period_month, sk.sku_code
            ORDER BY sm.period_month, qty DESC
        """, sku_params).fetchall()

        tx_rows = con.execute(f"""
            SELECT SUBSTR(st.sold_at,1,7) AS m,
                   SUM(st.customer_count) AS covers
            FROM sales_transaction st
            JOIN branches b ON b.branch_id = st.branch_id
            {where_tx}
              AND st.customer_count > 0
            GROUP BY SUBSTR(st.sold_at,1,7)
        """, tx_params).fetchall()

    covers_by_month = {r["m"]: (r["covers"] or 1) for r in tx_rows}
    months_sorted   = sorted(covers_by_month.keys())

    from collections import defaultdict
    sku_monthly: dict = defaultdict(lambda: defaultdict(float))
    sku_meta: dict = {}
    for r in sku_rows:
        sku_monthly[r["sku_code"]][r["m"]] = r["qty"] or 0
        sku_meta[r["sku_code"]] = {"description": r["description"], "category": r["category"]}

    # avg qty/cover across all months
    sku_avg: dict = {}
    for code, monthly in sku_monthly.items():
        ratios = [(monthly[m] / covers_by_month[m]) for m in monthly if m in covers_by_month and covers_by_month[m]]
        sku_avg[code] = sum(ratios) / len(ratios) if ratios else 0

    top_skus = sorted(sku_avg.keys(), key=lambda x: -sku_avg[x])[:top_n]

    result = []
    for code in top_skus:
        monthly = sku_monthly[code]
        series  = []
        for m in months_sorted:
            covers = covers_by_month.get(m, 0)
            qty    = monthly.get(m, 0)
            series.append({
                "month":    m,
                "qty":      round(qty, 1),
                "covers":   covers,
                "ratio":    round(qty / covers, 4) if covers else 0,
            })
        # trend: slope of last 3 months vs first 3 months
        vals = [s["ratio"] for s in series if s["ratio"] > 0]
        if len(vals) >= 4:
            mid     = len(vals) // 2
            avg_old = sum(vals[:mid]) / mid
            avg_new = sum(vals[mid:]) / (len(vals) - mid)
            trend_pct = round((avg_new - avg_old) / avg_old * 100, 1) if avg_old else 0
        else:
            trend_pct = 0

        result.append({
            "sku_code":    code,
            "description": sku_meta[code]["description"],
            "category":    sku_meta[code]["category"],
            "avg_ratio":   round(sku_avg[code], 4),
            "trend_pct":   trend_pct,
            "series":      series,
        })

    return {"months": months_sorted, "skus": result}


# ---------------------------------------------------------------------------
# GET /api/buffet/plate-cost-dist   🟡4
# Pro-rate monthly BoM cost to per-bill, return distribution
# ---------------------------------------------------------------------------
@app.get("/api/buffet/plate-cost-dist")
@ttl_cache
def buffet_plate_cost_dist(
    branch:     Optional[str] = Query(None),
    month_from: Optional[str] = Query(None),
    month_to:   Optional[str] = Query(None),
):
    """Estimate cost per bill by pro-rating monthly BoM cost by daily bill revenue."""
    TIER_CODES = ('BUFFET','BUFFET350','BUFFET500','BUFFET590','BUFFET799','BUFFETCHILD',
                  'B01','B02','B07','B11')
    tier_named = {f"tc{i}": v for i, v in enumerate(TIER_CODES)}
    tier_ph    = ", ".join(f":tc{i}" for i in range(len(TIER_CODES)))

    sku_filters: list = ["b.name != 'ALL'"]
    sku_params: dict  = {**tier_named}
    if branch:
        sku_filters.append("b.name = :branch")
        sku_params["branch"] = branch
    _month_range_filter(sku_filters, sku_params, month_from, month_to, None, col="sm.period_month")
    where_sku = "WHERE " + " AND ".join(sku_filters)

    tx_filters: list = ["b.name != 'ALL'", "st.source='Easy'", "st.customer_count > 0"]
    tx_params: dict  = {}
    if branch:
        tx_filters.append("b.name = :branch")
        tx_params["branch"] = branch
    _month_range_filter(tx_filters, tx_params, month_from, month_to, None,
                        col="SUBSTR(st.sold_at,1,7)")
    where_tx = "WHERE " + " AND ".join(tx_filters)

    with get_db() as con:
        # Monthly BoM cost per branch
        bom_rows = con.execute(f"""
            SELECT b.name AS branch, sm.period_month AS m,
                   SUM(CASE WHEN sk.sku_code NOT IN ({tier_ph})
                            THEN {_cogs_case(ssm='sm', sk='sk', sc='sc')} ELSE 0 END) AS bom_cost
            FROM sales_sku_monthly sm
            JOIN branches   b  ON b.branch_id  = sm.branch_id
            JOIN skus       sk ON sk.sku_id     = sm.sku_id
            LEFT JOIN sku_cost sc ON sc.sku_id  = sm.sku_id
            {where_sku}
            GROUP BY b.name, sm.period_month
        """, sku_params).fetchall()

        # Per-bill data
        bill_rows = con.execute(f"""
            SELECT b.name AS branch,
                   SUBSTR(st.sold_at,1,7) AS m,
                   st.receipt_no,
                   st.customer_count,
                   COALESCE(st.food_subtotal,0)+COALESCE(st.drink_subtotal,0)+
                   COALESCE(st.other_subtotal,0) AS bill_rev
            FROM sales_transaction st
            JOIN branches b ON b.branch_id = st.branch_id
            {where_tx}
        """, tx_params).fetchall()

        # Monthly revenue per branch from transactions
        month_rev_rows = con.execute(f"""
            SELECT b.name AS branch, SUBSTR(st.sold_at,1,7) AS m,
                   SUM(COALESCE(st.food_subtotal,0)+COALESCE(st.drink_subtotal,0)+
                       COALESCE(st.other_subtotal,0)) AS monthly_rev
            FROM sales_transaction st
            JOIN branches b ON b.branch_id = st.branch_id
            {where_tx}
            GROUP BY b.name, SUBSTR(st.sold_at,1,7)
        """, tx_params).fetchall()

    from collections import defaultdict
    bom_map   = {(r["branch"], r["m"]): r["bom_cost"] or 0 for r in bom_rows}
    mrev_map  = {(r["branch"], r["m"]): r["monthly_rev"] or 1 for r in month_rev_rows}

    bill_costs = []
    for r in bill_rows:
        key     = (r["branch"], r["m"])
        bom     = bom_map.get(key, 0)
        mrev    = mrev_map.get(key, 1)
        covers  = r["customer_count"] or 1
        bill_rv = r["bill_rev"] or 0
        fraction = bill_rv / mrev if mrev > 0 else 0
        cost_per_cover = (bom * fraction) / covers if covers else 0
        bill_costs.append(cost_per_cover)

    if not bill_costs:
        return {"distribution": [], "percentiles": {}, "mean": 0, "count": 0}

    bill_costs.sort()
    n = len(bill_costs)

    def pct(lst, p):
        idx = max(0, min(len(lst) - 1, int(p / 100 * len(lst))))
        return round(lst[idx], 2)

    buckets = [
        ("<฿100",    0,   100),
        ("฿100-200", 100, 200),
        ("฿200-300", 200, 300),
        ("฿300-400", 300, 400),
        ("฿400-500", 400, 500),
        ("฿500-600", 500, 600),
        ("฿600+",    600, float("inf")),
    ]
    distribution = []
    for label, lo, hi in buckets:
        count = sum(1 for c in bill_costs if lo <= c < hi)
        distribution.append({
            "label": label, "count": count,
            "pct": round(count / n * 100, 1),
            "lo": lo, "hi": hi if hi != float("inf") else None,
        })

    return {
        "distribution": distribution,
        "percentiles": {
            "p25": pct(bill_costs, 25),
            "p50": pct(bill_costs, 50),
            "p75": pct(bill_costs, 75),
            "p90": pct(bill_costs, 90),
            "p95": pct(bill_costs, 95),
        },
        "mean":  round(sum(bill_costs) / n, 2),
        "count": n,
    }


@app.get("/api/buffet/heavy-eater-tail")
def buffet_heavy_eater_tail(branch: Optional[str] = Query(None), group: Optional[str] = Query(None)):
    """The defining AYCE question — REAL per-cover margin (what they paid − what they
    actually ate), not revenue pro-rata. Joins sale_bill_cogs (per-bill BoM COGS)
    to the bill's ex-VAT revenue + pax to surface the net-negative 'heavy eater'
    tail and segment it by daypart / day-of-week / party-size / branch.
    Window = the costed sale_bill_cogs range (~31 days)."""
    filt = ["t.source='Easy'", "b.name!='ALL'", "t.total_inc_vat>0", "t.customer_count>0"]
    p: dict = {}
    if branch: filt.append("b.name=:branch"); p["branch"] = branch
    _group_filter(filt, p, group)
    JN = ("FROM sales_transaction t JOIN sale_bill_cogs c ON c.receipt_no=t.receipt_no "
          "JOIN branches b ON b.branch_id=t.branch_id WHERE " + " AND ".join(filt))
    rev = "t.total_inc_vat/1.07"                # ex-VAT paid
    mh  = f"({rev} - c.cogs_bom)/t.customer_count"   # margin per head
    with get_db() as con:
        win = con.execute("SELECT MIN(day) mn, MAX(day) mx FROM sale_bill_cogs").fetchone()
        s = con.execute(f"""SELECT COUNT(*) bills, SUM(t.customer_count) covers,
            ROUND(SUM({rev})) rev, ROUND(SUM(c.cogs_bom)) cogs,
            SUM(CASE WHEN c.cogs_bom>{rev} THEN 1 ELSE 0 END) loss_bills,
            SUM(CASE WHEN c.cogs_bom>{rev} THEN t.customer_count ELSE 0 END) loss_covers,
            ROUND(SUM(CASE WHEN c.cogs_bom>{rev} THEN c.cogs_bom-{rev} ELSE 0 END)) loss_amount
            {JN}""", p).fetchone()
        bills = s["bills"] or 0; covers = s["covers"] or 0
        revv = s["rev"] or 0; cogs = s["cogs"] or 0
        summary = {"bills": bills, "covers": covers, "rev": revv, "cogs": cogs,
                   "gp": round(revv - cogs), "gp_per_head": round((revv - cogs)/covers, 1) if covers else None,
                   "loss_bills": s["loss_bills"] or 0, "loss_covers": s["loss_covers"] or 0,
                   "loss_amount": s["loss_amount"] or 0,
                   "loss_bill_pct": round((s["loss_bills"] or 0)/bills*100, 1) if bills else 0,
                   "loss_cover_pct": round((s["loss_covers"] or 0)/covers*100, 1) if covers else 0}
        # margin-per-head distribution (the tail lives in the negative buckets)
        dist = [dict(r) for r in con.execute(f"""
            SELECT CASE WHEN {mh}<-100 THEN 0 WHEN {mh}<0 THEN 1 WHEN {mh}<100 THEN 2
                        WHEN {mh}<300 THEN 3 WHEN {mh}<500 THEN 4 ELSE 5 END bkt,
                   COUNT(*) bills, SUM(t.customer_count) covers {JN} GROUP BY bkt ORDER BY bkt""", p).fetchall()]
        _DLBL = {0:"ขาดทุนหนัก (<-100)",1:"ขาดทุน (-100..0)",2:"บางมาก (0-100)",3:"100-300",4:"300-500",5:"สุขภาพดี (500+)"}
        distribution = [{"bkt": d["bkt"], "label": _DLBL[d["bkt"]], "bills": d["bills"], "covers": d["covers"]} for d in dist]
        def seg(expr, label_map=None):
            rows = [dict(r) for r in con.execute(f"""
                SELECT {expr} k, COUNT(*) bills, SUM(t.customer_count) covers,
                    SUM(CASE WHEN c.cogs_bom>{rev} THEN 1 ELSE 0 END) loss_bills,
                    SUM(CASE WHEN c.cogs_bom>{rev} THEN t.customer_count ELSE 0 END) loss_covers,
                    ROUND(AVG({mh}),1) avg_margin_head,
                    ROUND(SUM(CASE WHEN c.cogs_bom>{rev} THEN c.cogs_bom-{rev} ELSE 0 END)) loss_amount
                {JN} GROUP BY k""", p).fetchall()]
            for r in rows:
                r["loss_cover_pct"] = round((r["loss_covers"] or 0)/r["covers"]*100, 1) if r["covers"] else 0
                if label_map: r["label"] = label_map.get(r["k"], str(r["k"]))
            return rows
        by_daypart = seg("CASE WHEN CAST(substr(t.sold_at,12,2) AS INT)<16 THEN 'กลางวัน (lunch)' ELSE 'เย็น (dinner)' END")
        by_dow = seg("CAST(strftime('%w', t.sold_at) AS INT)",
                     {0:"อา",1:"จ",2:"อ",3:"พ",4:"พฤ",5:"ศ",6:"ส"})
        by_party = seg("CASE WHEN t.customer_count=1 THEN '1' WHEN t.customer_count=2 THEN '2' "
                       "WHEN t.customer_count<=4 THEN '3-4' WHEN t.customer_count<=6 THEN '5-6' ELSE '7+' END")
        by_branch = seg("b.name")
        for r in by_branch:
            r["branch"] = r.pop("k")
        by_branch.sort(key=lambda r: -(r["loss_cover_pct"] or 0))
    return {"window": [win["mn"], win["mx"]] if win else [None, None], "summary": summary,
            "distribution": distribution, "by_daypart": by_daypart, "by_dow": by_dow,
            "by_party_size": by_party, "by_branch": by_branch}


@app.get("/api/buffet/turn-efficiency")
def buffet_turn_efficiency(branch: Optional[str] = Query(None), group: Optional[str] = Query(None)):
    """Table throughput efficiency — the OTHER half of buffet profit (a table tied up
    long earns nothing for the next party). Joins real dwell (pos_table_turn_day.
    avg_turn_min) + covers/table to real GP/cover, to rank branches by GP generated
    per table-MINUTE occupied, and flag the worst combo: long dwell + low GP/cover.
    Uses tables (real) — seats are not populated in pos_zone, so we report per-table."""
    filt = ["b.name!='ALL'"]; p: dict = {}
    if branch: filt.append("b.name=:branch"); p["branch"] = branch
    _group_filter(filt, p, group)
    tw = " AND ".join(filt)
    with get_db() as con:
        mx = con.execute("SELECT MAX(day) FROM pos_table_turn_day").fetchone()[0]
        if not mx:
            return {"window": None, "branches": [], "chain": {}}
        import datetime as _dt
        y, m, d = (int(x) for x in mx.split("-")); since = (_dt.date(y, m, d) - _dt.timedelta(days=30)).isoformat()
        rows = [dict(r) for r in con.execute(f"""
            WITH tt AS (
              SELECT x.branch_id,
                     AVG(x.avg_turn_min) turn_min,
                     ROUND(AVG(x.customers)) cov_day,
                     ROUND(AVG(x.tabs)) tabs_day,
                     AVG(CAST(x.customers AS float)/NULLIF(x.tabs,0)) cov_per_tab
              FROM pos_table_turn_day x JOIN branches b ON b.branch_id=x.branch_id
              WHERE {tw} AND x.day>=:since GROUP BY x.branch_id),
            gp AS (
              SELECT t.branch_id, SUM(t.total_inc_vat/1.07 - c.cogs_bom) gp, SUM(t.customer_count) cov
              FROM sales_transaction t JOIN sale_bill_cogs c ON c.receipt_no=t.receipt_no
              JOIN branches b ON b.branch_id=t.branch_id WHERE {tw} AND t.customer_count>0 GROUP BY t.branch_id)
            SELECT b.name branch, ROUND(tt.turn_min,1) turn_min, tt.cov_day, tt.tabs_day,
                   ROUND(tt.cov_per_tab,2) cov_per_tab,
                   ROUND(gp.gp/NULLIF(gp.cov,0)) gp_cover
            FROM tt JOIN branches b ON b.branch_id=tt.branch_id LEFT JOIN gp ON gp.branch_id=tt.branch_id
        """, {**p, "since": since}).fetchall()]
        for r in rows:
            gpc = r.get("gp_cover") or 0; cpt = r.get("cov_per_tab") or 0; tm = r.get("turn_min") or 0
            r["gp_per_table_occasion"] = round(gpc * cpt) if (gpc and cpt) else None
            r["gp_per_table_min"] = round(gpc * cpt / tm, 1) if (gpc and cpt and tm) else None
        valid = [r for r in rows if r.get("gp_per_table_min")]
        n = len(valid) or 1
        chain = {
            "turn_min": round(sum(r["turn_min"] for r in valid) / n, 1) if valid else None,
            "gp_cover": round(sum((r["gp_cover"] or 0) for r in valid) / n) if valid else None,
            "gp_per_table_min": round(sum(r["gp_per_table_min"] for r in valid) / n, 1) if valid else None,
        }
        # flag: dwell above chain avg AND gp/cover below chain avg = inefficient
        for r in rows:
            r["inefficient"] = bool(chain["turn_min"] and chain["gp_cover"]
                                    and (r.get("turn_min") or 0) > chain["turn_min"]
                                    and (r.get("gp_cover") or 0) < chain["gp_cover"])
        rows.sort(key=lambda r: -(r.get("gp_per_table_min") or -1))
    return {"window": [since, mx], "branches": rows, "chain": chain}


# ---------------------------------------------------------------------------
# GET /api/buffet/cost-sensitivity   🟡5
# Top cost-driver SKUs for JS-side simulation
# ---------------------------------------------------------------------------
@app.get("/api/buffet/cost-sensitivity")
@ttl_cache
def buffet_cost_sensitivity(
    branch:     Optional[str] = Query(None),
    month_from: Optional[str] = Query(None),
    month_to:   Optional[str] = Query(None),
    top_n:      int           = Query(50, ge=5, le=100),
):
    """Returns top SKUs by BoM cost with enough data for client-side sensitivity simulation."""
    TIER_CODES = ('BUFFET','BUFFET350','BUFFET500','BUFFET590','BUFFET799','BUFFETCHILD',
                  'B01','B02','B07','B11')
    tier_named = {f"tc{i}": v for i, v in enumerate(TIER_CODES)}
    tier_ph    = ", ".join(f":tc{i}" for i in range(len(TIER_CODES)))

    filters: list = ["b.name != 'ALL'", f"sk.sku_code NOT IN ({tier_ph})"]
    params: dict  = {**tier_named}
    if branch:
        filters.append("b.name = :branch")
        params["branch"] = branch
    _month_range_filter(filters, params, month_from, month_to, None, col="sm.period_month")
    where = "WHERE " + " AND ".join(filters)

    with get_db() as con:
        rows = con.execute(f"""
            SELECT sk.sku_code,
                   COALESCE(sk.description, sk.sku_code) AS description,
                   c.name AS category,
                   sc.unit_cost,
                   SUM(sm.qty)                                            AS total_qty,
                   SUM({_cogs_case(ssm='sm', sk='sk', sc='sc')})          AS total_bom_cost,
                   SUM(sm.subtotal_ex_vat)                                AS total_rev
            FROM sales_sku_monthly sm
            JOIN branches   b  ON b.branch_id  = sm.branch_id
            JOIN skus       sk ON sk.sku_id     = sm.sku_id
            JOIN categories c  ON c.category_id = sk.category_id
            LEFT JOIN sku_cost sc ON sc.sku_id  = sm.sku_id
            {where}
              AND {_effective_cost_subquery(ssm='sm', sc='sc')} IS NOT NULL
              AND {_effective_cost_subquery(ssm='sm', sc='sc')} > 0
            GROUP BY sk.sku_code, sc.unit_cost
            ORDER BY total_bom_cost DESC
            LIMIT :top_n
        """, {**params, "top_n": top_n}).fetchall()

        # Total revenue for the period (to compute margin impact)
        rev_row = con.execute(f"""
            SELECT SUM(sm.subtotal_ex_vat) AS total_rev
            FROM sales_sku_monthly sm
            JOIN branches b ON b.branch_id = sm.branch_id
            JOIN skus sk ON sk.sku_id = sm.sku_id
            {where}
        """, params).fetchone()

    total_rev = (rev_row["total_rev"] or 0) if rev_row else 0
    total_bom = sum(r["total_bom_cost"] or 0 for r in rows)

    skus = [{
        "sku_code":      r["sku_code"],
        "description":   r["description"],
        "category":      r["category"],
        "unit_cost":     r["unit_cost"],
        "total_qty":     round(r["total_qty"] or 0, 1),
        "total_bom_cost": round(r["total_bom_cost"] or 0, 2),
        "total_rev":     round(r["total_rev"] or 0, 2),
        "cost_pct":      round((r["total_bom_cost"] or 0) / total_bom * 100, 2) if total_bom else 0,
    } for r in rows]

    return {
        "skus":          skus,
        "total_revenue": round(total_rev, 2),
        "total_bom":     round(total_bom, 2),
        "base_gp":       round(total_rev - total_bom, 2),
    }


# ---------------------------------------------------------------------------
# GET + POST /api/branches/fixed-costs   🟡6
# Per-branch monthly fixed costs (stored in app_settings as JSON)
# ---------------------------------------------------------------------------
import json as _json


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
# GET /api/buffet/pnl   🟡6 (part 2)
# Net P&L = GP - Fixed Costs per branch
# ---------------------------------------------------------------------------
@app.get("/api/buffet/pnl")
@ttl_cache
def buffet_pnl(
    branch:     Optional[str] = Query(None),
    month_from: Optional[str] = Query(None),
    month_to:   Optional[str] = Query(None),
):
    """Net P&L per branch = Gross Profit (BoM) − monthly fixed costs."""
    # Reuse the same GP logic — aggregate by branch
    TIER_CODES = ('BUFFET','BUFFET350','BUFFET500','BUFFET590','BUFFET799','BUFFETCHILD',
                  'B01','B02','B07','B11')
    tier_named = {f"tc{i}": v for i, v in enumerate(TIER_CODES)}
    tier_ph    = ", ".join(f":tc{i}" for i in range(len(TIER_CODES)))

    sku_filters: list = ["b.name != 'ALL'"]
    sku_params: dict  = {**tier_named}
    if branch:
        sku_filters.append("b.name = :branch")
        sku_params["branch"] = branch
    _month_range_filter(sku_filters, sku_params, month_from, month_to, None, col="sm.period_month")
    where_sku = "WHERE " + " AND ".join(sku_filters)

    tx_filters: list = ["b.name != 'ALL'", "st.source='Easy'"]
    tx_params: dict  = {}
    if branch:
        tx_filters.append("b.name = :branch")
        tx_params["branch"] = branch
    _month_range_filter(tx_filters, tx_params, month_from, month_to, None,
                        col="SUBSTR(st.sold_at,1,7)")
    where_tx = "WHERE " + " AND ".join(tx_filters)

    with get_db() as con:
        gp_rows = con.execute(f"""
            SELECT b.name AS branch,
                   COUNT(DISTINCT sm.period_month)                          AS months,
                   SUM(sm.subtotal_ex_vat)                                  AS revenue,
                   SUM(CASE WHEN sk.sku_code NOT IN ({tier_ph})
                            THEN {_cogs_case(ssm='sm', sk='sk', sc='sc')} ELSE 0 END) AS bom_cost
            FROM sales_sku_monthly sm
            JOIN branches   b  ON b.branch_id   = sm.branch_id
            JOIN skus       sk ON sk.sku_id      = sm.sku_id
            LEFT JOIN sku_cost sc ON sc.sku_id   = sm.sku_id
            {where_sku}
            GROUP BY b.name
        """, sku_params).fetchall()

        covers_rows = con.execute(f"""
            SELECT b.name AS branch, SUM(st.customer_count) AS covers
            FROM sales_transaction st
            JOIN branches b ON b.branch_id = st.branch_id
            {where_tx} AND st.customer_count > 0
            GROUP BY b.name
        """, tx_params).fetchall()

        fc_row = con.execute(
            "SELECT value FROM app_settings WHERE key='branch_fixed_costs'"
        ).fetchone()

    fixed_costs = {}
    if fc_row:
        try:
            fixed_costs = _json.loads(fc_row["value"])
        except Exception:
            pass

    covers_map = {r["branch"]: (r["covers"] or 0) for r in covers_rows}

    result = []
    for r in sorted(gp_rows, key=lambda x: -(x["revenue"] or 0)):
        br          = r["branch"]
        rev         = round(r["revenue"] or 0, 2)
        bom         = round(r["bom_cost"] or 0, 2)
        gp          = round(rev - bom, 2)
        months      = r["months"] or 1
        fc_monthly  = fixed_costs.get(br, 0.0)
        fc_period   = round(fc_monthly * months, 2)
        net_profit  = round(gp - fc_period, 2)
        covers      = covers_map.get(br, 0)
        np_per_cov  = round(net_profit / covers, 2) if covers else None
        gp_margin   = round(gp / rev * 100, 2) if rev else None
        np_margin   = round(net_profit / rev * 100, 2) if rev else None

        result.append({
            "branch":          br,
            "months":          months,
            "revenue":         rev,
            "bom_cost":        bom,
            "gross_profit":    gp,
            "gp_margin_pct":   gp_margin,
            "fixed_cost_period": fc_period,
            "fixed_cost_monthly": fc_monthly,
            "net_profit":      net_profit,
            "np_margin_pct":   np_margin,
            "covers":          covers,
            "np_per_cover":    np_per_cov,
        })

    return {"branches": result}


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
# GET /api/analysis/daypart-economics  (Analysis #4)
# ---------------------------------------------------------------------------
@app.get("/api/analysis/daypart-economics")
@ttl_cache
def daypart_economics(
    branch:     Optional[str] = Query(None),
    month:      Optional[str] = Query(None),
    month_from: Optional[str] = Query(None),
    month_to:   Optional[str] = Query(None),
):
    """Per-daypart economics from transactions: ex-VAT revenue, covers, spend/cover,
    food/drink/other mix and drink attach. Includes an ESTIMATED gross profit using
    transparent component margins (drinks carry more margin than food) — true
    COGS-by-daypart isn't possible (transactions have no SKU-level detail)."""
    DRINK_GP, FOODOTHER_GP = 0.70, 0.45   # assumed contribution rates (documented)
    filters = ["b.name != 'ALL'", "st.source='Easy'"]
    params: dict = {}
    if branch:
        filters.append("b.name = :branch"); params["branch"] = branch
    _month_range_filter(filters, params, month_from, month_to, month, col="SUBSTR(st.sold_at,1,7)")
    where = "WHERE " + " AND ".join(filters)
    # Finer daypart buckets (5 instead of 3) for more detail.
    bucket = ("CASE "
              "WHEN CAST(strftime('%H', st.sold_at) AS INT) < 11 THEN 'morning' "
              "WHEN CAST(strftime('%H', st.sold_at) AS INT) BETWEEN 11 AND 14 THEN 'lunch' "
              "WHEN CAST(strftime('%H', st.sold_at) AS INT) BETWEEN 15 AND 17 THEN 'afternoon' "
              "WHEN CAST(strftime('%H', st.sold_at) AS INT) BETWEEN 18 AND 21 THEN 'dinner' "
              "ELSE 'late' END")
    with get_db() as con:
        rows = con.execute(f"""
            SELECT {bucket} AS daypart,
                   COUNT(*)                       AS bills,
                   SUM(st.customer_count)         AS covers,
                   SUM(st.food_subtotal)          AS food,
                   SUM(st.drink_subtotal)         AS drink,
                   SUM(st.other_subtotal)         AS other,
                   SUM(st.total_inc_vat) / 1.07   AS revenue
            FROM sales_transaction st
            JOIN branches b ON b.branch_id = st.branch_id
            {where}
            GROUP BY daypart
        """, params).fetchall()
    labels = {"morning": "เช้า (ก่อน 11)", "lunch": "กลางวัน (11-14)",
              "afternoon": "บ่าย (15-17)", "dinner": "เย็น (18-21)", "late": "ดึก (22+)"}
    total_rev = sum((r["revenue"] or 0) for r in rows) or 1
    out = []
    for r in rows:
        food = r["food"] or 0; drink = r["drink"] or 0; other = r["other"] or 0
        rev = r["revenue"] or 0; covers = r["covers"] or 0; bills = r["bills"] or 0
        est_gp = drink * DRINK_GP + (food + other) * FOODOTHER_GP
        out.append({
            "daypart":       r["daypart"],
            "label":         labels.get(r["daypart"], r["daypart"]),
            "bills":         bills,
            "covers":        covers,
            "revenue":       round(rev, 2),
            "revenue_pct":   round(rev / total_rev * 100, 1),
            "rev_per_cover": round(rev / covers, 2) if covers else None,
            "avg_bill":      round(rev / bills, 2) if bills else None,
            "covers_per_bill": round(covers / bills, 2) if bills else None,
            "drink_pct":     round(drink / (food + drink + other) * 100, 1) if (food + drink + other) else 0.0,
            "mix": {
                "food":  round(food, 2), "drink": round(drink, 2), "other": round(other, 2),
            },
            "est_gross_profit": round(est_gp, 2),
            "est_gp_margin_pct": round(est_gp / rev * 100, 1) if rev else None,
        })
    order = {"morning": 0, "lunch": 1, "afternoon": 2, "dinner": 3, "late": 4}
    out.sort(key=lambda x: order.get(x["daypart"], 9))
    return {"dayparts": out,
            "assumptions": {"drink_gp": DRINK_GP, "food_other_gp": FOODOTHER_GP,
                            "note": "est_gross_profit is an estimate using fixed component rates"}}


# ---------------------------------------------------------------------------
# GET /api/analysis/table-turnover  (Analysis #7)
# ---------------------------------------------------------------------------
@ttl_cache
def table_turnover_branches(   # distinct __name__ — was 'table_turnover', which collided
    branch:     Optional[str] = Query(None),   # in ttl_cache with /api/analysis/table_turnover
    month:      Optional[str] = Query(None),    # and served its wrong-shaped cached response
    month_from: Optional[str] = Query(None),    # (the More-tab "no data" bug).
    month_to:   Optional[str] = Query(None),
):
    """Per-branch table turnover from the POS turn-time feed (pos_table_turn_day).
    Source change: the MSSQL sync leaves sales_transaction.table_no empty, so the
    old bill-gap method returned nothing; pos_table_turn_day carries the real
    avg_turn_min + table-occasions + covers per branch/day."""
    filters = ["b.name != 'ALL'"]
    params: dict = {}
    if branch:
        filters.append("b.name = :branch"); params["branch"] = branch
    _month_range_filter(filters, params, month_from, month_to, month, col="SUBSTR(x.day,1,7)")
    where = "WHERE " + " AND ".join(filters)
    with get_db() as con:
        rows = con.execute(f"""
            SELECT b.name AS branch,
                   AVG(x.avg_turn_min)                              AS avg_turn_min,
                   SUM(x.tabs)                                       AS turns,
                   SUM(x.customers) * 1.0 / NULLIF(SUM(x.tabs), 0)   AS avg_covers
            FROM pos_table_turn_day x
            JOIN branches b ON b.branch_id = x.branch_id
            {where}
            GROUP BY b.name
        """, params).fetchall()
    out = []
    for r in sorted(rows, key=lambda x: (x["avg_turn_min"] or 9e9)):
        out.append({
            "branch":        r["branch"],
            "avg_turn_min":  round(r["avg_turn_min"], 1) if r["avg_turn_min"] is not None else None,
            "turns":         int(r["turns"]) if r["turns"] is not None else 0,
            "avg_covers_per_turn": round(r["avg_covers"], 1) if r["avg_covers"] is not None else None,
        })
    return {"branches": out, "note": "จาก POS turn-time feed (pos_table_turn_day) · นาที/รอบ เฉลี่ย, รอบ = table-occasions รวม"}


# ---------------------------------------------------------------------------
# GET /api/analysis/forecast  (Analysis #9)  — short-horizon revenue projection
# ---------------------------------------------------------------------------
@ttl_cache
def revenue_forecast(
    branch:  Optional[str] = Query(None),
    horizon: int = Query(3),
):
    """Project ex-VAT revenue for the next `horizon` months from the monthly trend
    via OLS, with a ± band from the residual spread. Branch-specific or chain."""
    horizon = max(1, min(6, horizon))
    # Exclude the in-progress calendar month so OLS is not biased by a partial point (fix: G5)
    filters = ["b.name != 'ALL'",
               "ssm.period_month < strftime('%Y-%m', 'now')"]
    params: dict = {}
    if branch:
        filters.append("b.name = :branch"); params["branch"] = branch
    where = "WHERE " + " AND ".join(filters)
    with get_db() as con:
        rows = con.execute(f"""
            SELECT ssm.period_month AS m, SUM(ssm.subtotal_ex_vat) AS rev
            FROM sales_sku_monthly ssm
            JOIN branches b ON b.branch_id = ssm.branch_id
            {where}
            GROUP BY ssm.period_month
            ORDER BY ssm.period_month
        """, params).fetchall()
    hist = [{"period": r["m"], "revenue": round(r["rev"] or 0, 2)} for r in rows]
    if len(hist) < 3:
        return {"history": hist, "forecast": [], "note": "need >=3 months of history"}
    xs = list(range(len(hist)))
    ys = [h["revenue"] for h in hist]
    slope, intercept, r2 = _ols_regression(xs, ys)
    resid = [ys[i] - (slope * xs[i] + intercept) for i in xs]
    sd = (sum(e * e for e in resid) / len(resid)) ** 0.5
    band = 1.5 * sd
    # advance YYYY-MM
    def add_month(ym, k):
        y, mo = _ym_parts(ym); mo += k
        y += (mo - 1) // 12; mo = (mo - 1) % 12 + 1
        return f"{y:04d}-{mo:02d}"
    forecast = []
    for k in range(1, horizon + 1):
        x = len(hist) - 1 + k
        pred = slope * x + intercept
        forecast.append({
            "period": add_month(hist[-1]["period"], k),
            "revenue": round(max(0, pred), 2),
            "lo": round(max(0, pred - band), 2),
            "hi": round(max(0, pred + band), 2),
        })
    return {
        "history": hist, "forecast": forecast,
        "trend": {"slope_per_month": round(slope, 2), "r_squared": round(r2, 3)},
    }


# ---------------------------------------------------------------------------
# GET /api/buffet/gp/export   🟢7
# Download GP analysis as formatted Excel
# ---------------------------------------------------------------------------
from fastapi.responses import StreamingResponse
import io as _io

@app.get("/api/buffet/gp/export")
def buffet_gp_export(
    branch:      Optional[str] = Query(None),
    month_from:  Optional[str] = Query(None),
    month_to:    Optional[str] = Query(None),
    granularity: str           = Query("month"),
):
    """Export buffet GP analysis to Excel (.xlsx)."""
    try:
        import openpyxl
        from openpyxl.styles import Font, PatternFill, Alignment, numbers
        from openpyxl.utils import get_column_letter
    except ImportError:
        raise HTTPException(status_code=500, detail="openpyxl not installed")

    # Fetch data using same logic as /api/buffet/gp
    params = {}
    if branch:      params["branch"]     = branch
    if month_from:  params["month_from"] = month_from
    if month_to:    params["month_to"]   = month_to
    params["granularity"] = granularity

    # buffet_gp is @ttl_cache-wrapped → a direct (in-process) call returns a JSON
    # Response, NOT a dict. Parse its body. (Falls back to dict if ever un-cached.)
    gp_resp = buffet_gp(
        branch=branch, month_from=month_from,
        month_to=month_to, granularity=granularity,
    )
    gp_data = _json.loads(gp_resp.body) if hasattr(gp_resp, "body") else gp_resp

    wb  = openpyxl.Workbook()
    HDR = Font(bold=True, color="FFFFFF")
    FILL_H = PatternFill("solid", fgColor="1F4E79")
    FILL_GP = PatternFill("solid", fgColor="E8F5E9")
    NUM_FMT  = '#,##0'
    PCT_FMT  = '0.0%'
    CENTER   = Alignment(horizontal="center")

    def make_header(ws, headers, fill=FILL_H):
        ws.append(headers)
        for cell in ws[1]:
            cell.font = HDR
            cell.fill = fill
            cell.alignment = CENTER

    def autofit(ws):
        for col in ws.columns:
            max_len = max((len(str(cell.value or "")) for cell in col), default=8)
            ws.column_dimensions[get_column_letter(col[0].column)].width = min(max_len + 2, 40)

    # Sheet 1: Period Summary
    ws1 = wb.active
    ws1.title = "Period Summary"
    pt = gp_data.get("period_total", {})
    ws1.append(["Metric", "Value"])
    for cell in ws1[1]: cell.font = HDR; cell.fill = FILL_H
    ws1.append(["Revenue (ex-VAT)", pt.get("revenue") or 0])
    ws1.append(["BoM Cost",         pt.get("bom_cost") or 0])
    ws1.append(["Gross Profit",     pt.get("gp") or 0])
    ws1.append(["GP Margin %",      (pt.get("margin_pct") or 0) / 100])
    ws1.append(["Covers",           pt.get("covers") or 0])
    ws1.append(["Cost/Cover",       pt.get("cost_per_cover") or 0])
    ws1["B5"].number_format = PCT_FMT
    autofit(ws1)

    # Sheet 2: Time Series
    ws2 = wb.create_sheet("Time Series")
    make_header(ws2, ["Bucket", "Revenue", "BoM Cost", "Gross Profit", "Margin %", "Covers"])
    for s in gp_data.get("series", []):
        ws2.append([
            s["bucket"], s["revenue"], s["bom_cost"], s["gp"],
            (s["margin_pct"] or 0) / 100, s["covers"],
        ])
    for row in ws2.iter_rows(min_row=2):
        for cell in row[1:4]: cell.number_format = NUM_FMT
        row[4].number_format = PCT_FMT
    autofit(ws2)

    # Sheet 3: By Branch
    ws3 = wb.create_sheet("By Branch")
    make_header(ws3, ["Branch", "Covers", "Revenue", "BoM Cost", "GP", "GP/Cover", "Margin %", "vs Network"])
    for b in gp_data.get("by_branch", []):
        ws3.append([
            b["branch"], b["covers"], b["revenue"], b["bom_cost"],
            b["gp"], b["gp_per_cover"], (b["margin_pct"] or 0) / 100,
            (b["vs_network_pp"] or 0) / 100,
        ])
    for row in ws3.iter_rows(min_row=2):
        for cell in row[2:6]: cell.number_format = NUM_FMT
        row[6].number_format = PCT_FMT
        row[7].number_format = PCT_FMT
    autofit(ws3)

    # Sheet 4: Top Cost SKUs
    ws4 = wb.create_sheet("Top Cost SKUs")
    make_header(ws4, ["SKU Code", "Category", "Qty", "BoM Cost", "% of Total Cost"])
    for s in gp_data.get("top_cost_skus", []):
        ws4.append([s["sku_code"], s["category"], s["qty"], s["bom_cost"], s["cost_pct"] / 100])
    for row in ws4.iter_rows(min_row=2):
        row[3].number_format = NUM_FMT
        row[4].number_format = PCT_FMT
    autofit(ws4)

    buf = _io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    filename = f"buffet_gp_{month_from or 'all'}_{month_to or 'all'}.xlsx"
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


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


# ---------------------------------------------------------------------------
# GET /api/menu/lifecycle   🟢9
# Classify SKUs as New / Growing / Stable / Declining / Dead
# ---------------------------------------------------------------------------
@app.get("/api/menu/lifecycle")
@ttl_cache
def menu_lifecycle(
    branch:     Optional[str] = Query(None),
    months:     int           = Query(6, ge=3, le=12),
    month_from: Optional[str] = Query(None),
    month_to:   Optional[str] = Query(None),
):
    """Classify SKUs by 6-month qty trend into lifecycle stages."""
    with get_db() as con:
        # Last N months of SKU data
        max_month_row = con.execute(
            "SELECT MAX(period_month) AS m FROM sales_sku_monthly"
        ).fetchone()
        if not max_month_row or not max_month_row["m"]:
            return {"skus": [], "summary": {}}
        max_month = max_month_row["m"]

        from datetime import date as _date
        import datetime as _dtt
        if month_from and month_to:
            cutoff_month = month_from
            max_month    = month_to
        elif month_from:
            cutoff_month = month_from
        elif month_to:
            max_month = month_to
            y, mo = int(max_month[:4]), int(max_month[5:7])
            cutoff_dt = _date(y, mo, 1)
            for _ in range(months - 1):
                cutoff_dt = (cutoff_dt.replace(day=1) - _dtt.timedelta(days=1)).replace(day=1)
            cutoff_month = cutoff_dt.strftime("%Y-%m")
        else:
            # compute cutoff month relative to max available
            y, mo = int(max_month[:4]), int(max_month[5:7])
            cutoff_dt = _date(y, mo, 1)
            for _ in range(months - 1):
                cutoff_dt = (cutoff_dt.replace(day=1) - _dtt.timedelta(days=1)).replace(day=1)
            cutoff_month = cutoff_dt.strftime("%Y-%m")

        if branch:
            # Branch-filtered: join branches to apply name filter
            sku_rows = con.execute(
                """
                SELECT sk.sku_code,
                       COALESCE(sk.description, sk.sku_code) AS description,
                       c.name AS category,
                       sm.period_month AS m,
                       SUM(sm.qty)            AS qty,
                       SUM(sm.subtotal_ex_vat) AS rev
                FROM sales_sku_monthly sm
                JOIN branches   b  ON b.branch_id  = sm.branch_id
                JOIN skus       sk ON sk.sku_id     = sm.sku_id
                JOIN categories c  ON c.category_id = sk.category_id
                WHERE b.name = :branch
                  AND sm.period_month >= :cutoff AND sm.period_month <= :max_m
                GROUP BY sm.sku_id, sm.period_month
                ORDER BY sm.sku_id, sm.period_month
                """,
                {"branch": branch, "cutoff": cutoff_month, "max_m": max_month},
            ).fetchall()
        else:
            # All branches: skip branches JOIN — uses idx_ssm_sku_period covering index,
            # avoids temp B-TREE by grouping on sm.sku_id (in index key order)
            sku_rows = con.execute(
                """
                SELECT sk.sku_code,
                       COALESCE(sk.description, sk.sku_code) AS description,
                       c.name AS category,
                       sm.period_month AS m,
                       SUM(sm.qty)            AS qty,
                       SUM(sm.subtotal_ex_vat) AS rev
                FROM sales_sku_monthly sm
                JOIN skus       sk ON sk.sku_id     = sm.sku_id
                JOIN categories c  ON c.category_id = sk.category_id
                WHERE sm.period_month >= :cutoff AND sm.period_month <= :max_m
                GROUP BY sm.sku_id, sm.period_month
                ORDER BY sm.sku_id, sm.period_month
                """,
                {"cutoff": cutoff_month, "max_m": max_month},
            ).fetchall()

    from collections import defaultdict
    sku_data: dict = defaultdict(lambda: {"description": "", "category": "", "monthly": {}})
    for r in sku_rows:
        sd = sku_data[r["sku_code"]]
        sd["description"] = r["description"]
        sd["category"]    = r["category"]
        sd["monthly"][r["m"]] = r["qty"] or 0

    # Get all months in range
    all_months = []
    cutoff_dt = _date(int(cutoff_month[:4]), int(cutoff_month[5:7]), 1)
    cur = cutoff_dt
    max_dt = _date(int(max_month[:4]), int(max_month[5:7]), 1)
    while cur <= max_dt:
        all_months.append(cur.strftime("%Y-%m"))
        nxt = (cur.replace(day=28) + _dtt.timedelta(days=4)).replace(day=1)
        cur = nxt

    result = []
    for code, sd in sku_data.items():
        qty_series = [sd["monthly"].get(m, 0) for m in all_months]
        n = len(qty_series)

        # Lifecycle classification
        recent_2 = qty_series[-2:] if n >= 2 else qty_series
        early_months = [m for m in qty_series[:3] if m > 0]
        all_nonzero_start = next((i for i, v in enumerate(qty_series) if v > 0), None)

        is_dead = all(v == 0 for v in recent_2) and any(v > 0 for v in qty_series)
        is_new  = all_nonzero_start is not None and all_nonzero_start >= n - 2

        # Trend: linear regression slope
        nonzero = [(i, v) for i, v in enumerate(qty_series) if v > 0]
        if len(nonzero) >= 3:
            xs = [p[0] for p in nonzero]
            ys = [p[1] for p in nonzero]
            xm = sum(xs) / len(xs)
            ym = sum(ys) / len(ys)
            num = sum((x - xm) * (y - ym) for x, y in zip(xs, ys))
            den = sum((x - xm) ** 2 for x in xs) or 1
            slope = num / den
            slope_pct = round(slope / ym * 100, 1) if ym else 0
        else:
            slope_pct = 0

        if is_dead:
            stage = "dead"
        elif is_new:
            stage = "new"
        elif slope_pct > 10:
            stage = "growing"
        elif slope_pct < -10:
            stage = "declining"
        else:
            stage = "stable"

        total_qty = sum(qty_series)
        result.append({
            "sku_code":    code,
            "description": sd["description"],
            "category":    sd["category"],
            "stage":       stage,
            "slope_pct":   slope_pct,
            "total_qty":   round(total_qty, 1),
            "avg_monthly": round(total_qty / n, 1) if n else 0,
            "series":      qty_series,
            "months":      all_months,
        })

    summary = {s: sum(1 for r in result if r["stage"] == s)
               for s in ("new", "growing", "stable", "declining", "dead")}
    result.sort(key=lambda x: {"growing": 0, "new": 1, "stable": 2, "declining": 3, "dead": 4}.get(x["stage"], 9))

    return {"skus": result, "summary": summary, "all_months": all_months}


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


@app.get("/api/analysis/table-turnover")
def table_turnover_unified(
    view:       str           = Query("branches", pattern="^(branches|dwell)$"),
    branch:     Optional[str] = Query(None),
    month:      Optional[str] = Query(None),
    month_from: Optional[str] = Query(None),
    month_to:   Optional[str] = Query(None),
):
    """view=branches → per-branch POS turn-time feed (default; same shape the
    path always returned). view=dwell → bill-gap dwell/turns estimate (the old
    /api/analysis/table_turnover, used by the ⏰ Time & Flow tab)."""
    if view == "dwell":
        return table_turnover(month=month, month_from=month_from,
                              month_to=month_to, branch=branch)
    return table_turnover_branches(branch=branch, month=month,
                                   month_from=month_from, month_to=month_to)
