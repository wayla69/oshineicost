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
from contextlib import contextmanager, closing
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
    # closing() so the probe handle NEVER outlives this block — if a query raises
    # (e.g. COUNT on a malformed upload), a bare chk.close() is skipped and the
    # leaked handle keeps .incoming open on Windows: our own unlink below fails
    # AND every nightly sync dies at its INCOMING.unlink() with WinError 32
    # (2026-08-08..10: three days of silent sync failures until the app closed).
    try:
        with closing(sqlite3.connect(str(inc))) as chk:
            # quick_check (page-level) not integrity_check (full index cross-check):
            # the realistic failure of a freshly streamed upload is a truncated/partial
            # transfer, which quick_check catches — and it's ~20x faster on a large DB
            # (0.45s vs ~10s on 348 MB), so this no longer dominates startup.
            ok  = chk.execute("PRAGMA quick_check").fetchone()[0]
            n   = chk.execute("SELECT COUNT(*) FROM sales_transaction").fetchone()[0]
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
    # The Grand Menu (2026-09) re-coded those charges as OTHOT* — ค่าปรับบุฟเฟ่ต์
    # (food-waste penalty) and ค่าเปิดห้อง, which the OT[0-9] pattern does not match. They
    # were landing in food analytics as revenue that will never have a recipe,
    # inflating the uncosted gap and pulling food % down (฿11.6k on 1 Sep alone).
    " AND sk.sku_code NOT GLOB 'OTHOT*'"
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
    # closing() is load-bearing: this runs in the 60s poller, so a probe handle
    # leaked on a raised query would be re-leaked every poll and keep .incoming
    # locked indefinitely — blocking both the unlink below and the nightly
    # sync's staging (WinError 32, the 2026-08-08..10 outage).
    try:
        with closing(sqlite3.connect(str(inc))) as chk:
            ok = chk.execute("PRAGMA quick_check").fetchone()[0]
            n  = chk.execute("SELECT COUNT(*) FROM sales_transaction").fetchone()[0]
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
        # the freshly-swapped file is a straight copy from sync — it has the tables
        # but not the views the revenue queries read from.
        # NOTE: `with sqlite3.connect(...)` is a TRANSACTION context manager, not a
        # closing one — it would leave the handle open, and on Windows an open handle
        # on the live DB is exactly what breaks the next swap. Close it explicitly.
        _c = None
        try:
            _c = sqlite3.connect(str(DB_PATH))
            _ensure_rev_views(_c)
            _c.commit()
        except Exception as e:
            print(f"[hot-swap] rev-view ensure failed: {e}")
        finally:
            if _c is not None:
                _c.close()
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
    return _re.sub(r'src="/js/([0-9a-z-]+\.js)"', sub, html)


@app.get("/", include_in_schema=False)
def root():
    if INDEX.exists():
        # no-cache: the shell must always be fresh — it carries the js hashes
        return _Response(content=_versioned_index_html(), media_type="text/html",
                         headers={"Cache-Control": "no-cache"})
    return {"message": "Oshinei API running. Dashboard file not found."}


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
            gaps.append({"branch": sp["br"], "missing_days": missing, "dates": miss[:20]})
        gaps.sort(key=lambda g: -g["missing_days"])
        add("missing_days", "วันที่ไม่มีข้อมูล ระหว่างช่วงที่สาขาเปิดขายอยู่", "warn",
            sum(g["missing_days"] for g in gaps),
            "ช่องว่างกลางคาบการขายของสาขานั้นเอง (ไม่นับก่อนเปิด/หลังปิดสาขา) — "
            "กู้คืนได้ด้วยการ re-sync ช่วงวันดังกล่าวจาก POS เท่านั้น ระบบจะไม่สร้างข้อมูลขึ้นเอง",
            rows=gaps[:10])

        # 2. Bills carrying revenue but zero covers — every per-head figure that
        #    divides by covers is guarded, but these bills still under-count pax.
        z = con.execute("""
            SELECT b.name br, COUNT(*) n, ROUND(SUM((t.total_inc_vat/1.07 - COALESCE(t.service_charge,0)))) rev
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
        latest = con.execute("SELECT MAX(period_month) m FROM v_sales_sku_monthly").fetchone()["m"]
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
                          FROM v_sales_sku_monthly WHERE period_month IN ({ph})
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
# Derived from the tuple (never hand-maintained — that drift is bug class #7).
_ALL_TIER_CODES_SQL = "(" + ",".join(f"'{c}'" for c in _ALL_TIER_CODES) + ")"


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
    # Buffet-PACKAGE codes are forced to 0 here, at the source of the derived
    # table. Their sku_cost row holds the package PRICE, not a cost (BUFFET =
    # ฿679.08 vs a realised ฿642 average selling price) — the package's real
    # cost is the dishes guests actually eat, which are costed individually.
    # Every COGS path already zeroes these via _cogs_case/_unified_cogs_expr,
    # but that left ฿1.07B of phantom cost one un-guarded query away: any new
    # code reading sku_effective_cost directly would book it. Zeroing the
    # derived value makes the safe answer the DEFAULT instead of something each
    # caller must remember. Survives a price-as-cost re-upload, since this table
    # is rebuilt from sku_cost on every cache flush / startup.
    rows = con.execute(
        f"""
        SELECT s.sku_id,
               CASE WHEN s.sku_code IN {_ALL_TIER_CODES_SQL} THEN 0 ELSE COALESCE(
            (SELECT NULLIF(sc2.unit_cost, 0) FROM sku_cost sc2 WHERE sc2.sku_id = s.sku_id),
            (SELECT SUM(bi.ingredient_cost) FROM bom_ingredients bi WHERE bi.sku_id = s.sku_id),
            (SELECT SUM(bi2.ingredient_cost) FROM bom_ingredients bi2
               JOIN bom_sku_alias a ON a.resolved_sku_id = bi2.sku_id
               WHERE a.bom_sku_code = s.sku_code),
            0) END AS rc
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
                  FROM v_sales_sku_monthly ssm
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
              FROM v_sales_sku_monthly ssm
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
              FROM v_sales_sku_monthly ssm
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
                      FROM v_sales_sku_monthly
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
              FROM v_sales_sku_monthly ssm
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
GRAND_MENU_COST_FILE = HERE / "data" / "grand_menu_costs_2026-09.csv"


def _load_grand_menu_costs(path=None):
    """Upsert the Grand Menu master costs into sku_cost. Returns a summary dict."""
    import csv as _csv
    path = Path(path) if path else GRAND_MENU_COST_FILE
    out = {"applied": 0, "unknown_sku": 0, "rejected_price_as_cost": 0, "bad_value": 0}
    if not path.exists():
        print(f"[grand-menu] cost file not found: {path} — skipped")
        return out
    try:
        rows = []
        with open(path, encoding="utf-8-sig", newline="") as fh:
            for rec in _csv.DictReader(fh):
                code = (rec.get("sku_code") or "").strip()
                if not code:
                    continue
                try:
                    cost = float(rec.get("cost_per_serve") or 0)
                except (TypeError, ValueError):
                    out["bad_value"] += 1
                    continue
                if cost <= 0:
                    out["bad_value"] += 1
                    continue
                rows.append((code, cost))

        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
        with get_db() as con:
            ids = {r[1]: r[0] for r in con.execute(
                "SELECT sku_id, sku_code FROM skus WHERE sku_code IN (%s)"
                % ",".join("?" * len(rows)), [c for c, _ in rows])} if rows else {}
            # A selling price pasted into a cost column has reached this project
            # three separate times. Where the SKU has actually sold we can tell
            # the difference, so we check — and refuse the row rather than book
            # a dish at its own menu price.
            price = {}
            if ids:
                for r in con.execute(
                    "SELECT sku_id, SUM(subtotal_ex_vat) rev, SUM(qty) qty "
                    "FROM sales_sku_monthly WHERE sku_id IN (%s) GROUP BY 1"
                    % ",".join("?" * len(ids)), list(ids.values())
                ):
                    if (r[2] or 0) > 0 and (r[1] or 0) > 0:
                        price[r[0]] = r[1] / r[2]

            for code, cost in rows:
                sku_id = ids.get(code)
                if sku_id is None:
                    out["unknown_sku"] += 1
                    continue
                if is_price_as_cost(cost, price.get(sku_id)):
                    print(f"[grand-menu] {code}: {cost} looks like the selling price — rejected")
                    out["rejected_price_as_cost"] += 1
                    continue
                con.execute(
                    "INSERT OR REPLACE INTO sku_cost(sku_id, unit_cost, effective_from, updated_at)"
                    " VALUES(?, ?, ?, ?)",
                    (sku_id, cost, "2026-09-01", now),
                )
                out["applied"] += 1
            con.commit()
        print(f"[grand-menu] master costs: {out}")
        invalidate_cache()
    except Exception as e:
        print(f"[grand-menu] load failed: {e}")
    return out


# Manual cost corrections — applied at every startup after BOM seed.
# Use when the BOM workbook has a verified-wrong ingredient cost and cannot
# be corrected immediately. Each entry removes the SKU's BOM rows (so the
# COALESCE falls through to sku_cost) and upserts the confirmed unit cost.
_COST_PATCHES: list[tuple[str, float]] = [
    ("B0040",    13.40),  # หอยเชลล์ต้มฮอกไกโด (1 ชิ้น)  — BOM workbook had ~38.2 (wrong)
    ("B0322",    13.40),  # หอยเชลล์ฮอกไกโดซาชิมิ (1 ตัว) — same ingredient, same correction
    ("1150-084", 13.40),  # HOKKAIDO HOTATE SASHIMI (1PCS) — same ingredient, same correction
    # Durian: the requisition audit priced it at ฿68/serve, but the correction was
    # applied to a database and then lost — sync copies the cost tables out of the
    # LOCAL base DB into .incoming, which is hot-swapped over the live one, so a DB-only
    # fix survives exactly until the next swap. It was found back at ฿20/฿30/฿40 on
    # 2026-09-02, understating COGS by ฿36.7M across the history. Listed here so it
    # re-asserts itself on every startup no matter which DB is swapped in.
    # The BoM is NOT deleted: EST-DURIAN already totals ฿68, so the guard above leaves
    # the recipe alone and the ingredient order plan keeps seeing durian.
    ("KKN018",        68.00),  # (B) ทุเรียน (1 พู)
    ("KKN019",        68.00),  # (B) ทุเรียน (1 เม็ด)
    ("DUR-999",       68.00),  # ทุเรียน (B)(Buffet)
    ("PM0010",        68.00),  # (B) ทุเรียนยำน้ำปลาร้า
    ("PM0011",        68.00),  # (B) เทมปุระทุเรียนมัทชะ (5 ชิ้น)
    ("PM0012",        68.00),  # (B) ทุเรียนไม่อั้น
    ("OTHBFPMO00013", 68.00),  # (BF) ทุเรียน — Grand Menu 2026-09 code for the same dish
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
                # Only drop the BOM when it DISAGREES with the verified cost.
                # The patch exists because these SKUs once carried a BOM saying
                # ~฿38.2 against a true ฿13.40 — not because a recipe is unwanted.
                # A recipe that already totals the verified cost is harmless to
                # COGS (sku_cost still wins in _build_sku_effective_cost) and is
                # what lets the ingredient order plan see the dish at all, so
                # deleting it would keep หอยเชลล์ฮอกไกโด permanently unorderable.
                bom_total = con.execute(
                    "SELECT SUM(ingredient_cost) FROM bom_ingredients WHERE sku_id = ?",
                    (sku_id,)
                ).fetchone()[0]
                deleted = 0
                if bom_total is not None and abs(float(bom_total) - float(unit_cost)) > 0.01:
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
    # Owner's Grand Menu master first, hand-verified patches second — a patch
    # exists precisely because a bulk source was wrong, so it must win.
    _load_grand_menu_costs()
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
    # menu-impact selector runs `SELECT ent, SUM(qty) ... GROUP BY ent` over the
    # 2.1M-row fact table on every load, purely to know which entities sold. The
    # existing ix_mfd_ent(ent, day) doesn't carry qty, so SQLite still visited
    # every row; adding qty makes it an index-ONLY scan. 2,016ms → 120ms, and the
    # endpoint as a whole 8.8s → 163ms.
    "CREATE INDEX IF NOT EXISTS ix_mfd_ent_qty ON menu_fact_day(ent, qty)",
)

# The 5% service charge is not revenue, and the SKU roll-ups book it as a
# SERVICECHARGE pseudo-SKU — so every SUM(subtotal_ex_vat) over them silently
# carried it (฿4.6M in Aug 2026 alone). Those roll-ups are read from ~180 places
# with no shared WHERE clause to hook the exclusion into, and most of them do not
# join `skus` at all, so the rule lives here ONCE and readers select the view.
# Revenue readers use the view; writers (sync, ingest) keep the base table, which
# still holds every line the POS produced.
_REV_VIEWS = (
    """CREATE VIEW IF NOT EXISTS v_sales_sku_monthly AS
         SELECT * FROM sales_sku_monthly
          WHERE sku_id NOT IN (SELECT sku_id FROM skus
                                WHERE UPPER(sku_code) IN ('SERVICECHARGE','SERVICE'))""",
    """CREATE VIEW IF NOT EXISTS v_sales_sku_daily AS
         SELECT * FROM sales_sku_daily
          WHERE sku_id NOT IN (SELECT sku_id FROM skus
                                WHERE UPPER(sku_code) IN ('SERVICECHARGE','SERVICE'))""",
)


def _ensure_rev_views(con):
    """Create the service-charge-free roll-up views on whichever DB is live.

    Every revenue read selects from these, so a DB without them answers 500 — and a
    DB can become live WITHOUT going through startup (the hot-swap poller replaces
    the file under a running app). Called from both paths, and cheap enough to be
    unconditional: CREATE VIEW IF NOT EXISTS on an existing view is a no-op."""
    for ddl in _REV_VIEWS:
        con.execute(ddl)


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

# ═══════════════════════════════════════════════════════════════════════════
# Ingredient ordering — per-branch purchase planning (🧺 แผนสั่งวัตถุดิบรายสาขา)
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

_ING_BOM_MAP = """(
        SELECT sku_id AS sale_sku, sku_id AS bom_sku
          FROM (SELECT DISTINCT sku_id FROM bom_ingredients)
        UNION ALL
        SELECT s.sku_id, a.resolved_sku_id
          FROM bom_sku_alias a
          JOIN skus s ON s.sku_code = a.bom_sku_code
         WHERE s.sku_id NOT IN (SELECT DISTINCT sku_id FROM bom_ingredients)
    )"""

# Units that are counted in small base units and read better scaled up on a
# purchase order (2,400 กรัม → 2.4 กก.).
_ING_SCALE = {"กรัม": (1000.0, "กก."), "มิลลิลิตร": (1000.0, "ลิตร")}


def _ing_pretty(qty: float, unit: str):
    """(value, unit_label, text) for an order line — scales g→kg, ml→L."""
    q = float(qty or 0)
    u = (unit or "").strip()
    f, big = _ING_SCALE.get(u, (None, None))
    if f and q >= f:
        v = q / f
        return v, big, f"{v:,.2f} {big}"
    dec = 0 if q >= 100 else (1 if q >= 10 else 2)
    return q, u, f"{q:,.{dec}f} {u}".rstrip()


def _ing_window(con, days: int, date_to: Optional[str]):
    """Resolve the lookback window on sales_sku_daily. Returns (start, end, days)."""
    maxd = con.execute("SELECT MAX(day) FROM v_sales_sku_daily").fetchone()[0]
    if not maxd:
        return None, None, 0
    end = date_to or maxd
    if end > maxd:
        end = maxd
    days = max(1, min(int(days or 28), 365))
    start = con.execute("SELECT date(:e, :off)", {"e": end, "off": f"-{days - 1} day"}).fetchone()[0]
    return start, end, days


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

_ING_ORDER_DDL = """
CREATE TABLE IF NOT EXISTS ingredient_order_unit (
  ingredient_code TEXT PRIMARY KEY,
  base_unit       TEXT NOT NULL,
  order_unit      TEXT NOT NULL,
  qty_per_order   REAL NOT NULL,
  price_per_order REAL,
  moq             REAL DEFAULT 1,
  round_step      REAL DEFAULT 1,
  note            TEXT,
  source          TEXT,
  updated_at      TEXT DEFAULT CURRENT_TIMESTAMP
)"""

# base unit -> (order unit label, base units per order unit)
_ING_UNIT_FALLBACK = {
    "กรัม": ("กก.", 1000.0),
    "มิลลิลิตร": ("ลิตร", 1000.0),
}


def _ing_order_units(con) -> Dict[str, dict]:
    con.execute(_ING_ORDER_DDL)
    return {r["ingredient_code"]: dict(r)
            for r in con.execute("SELECT * FROM ingredient_order_unit")}


def _ing_to_order(need_base: float, unit: str, pack: Optional[dict]):
    """Base-unit demand -> whole ordering units, always rounded UP.

    Returns the pack description, how many to order, and what actually arrives
    (packs x pack size) so the branch can see the carry-over it is accepting."""
    import math as _m
    if pack:
        per = float(pack.get("qty_per_order") or 0) or 1.0
        label = pack.get("order_unit") or unit
        step = float(pack.get("round_step") or 1) or 1.0
        moq = float(pack.get("moq") or 0)
        price = pack.get("price_per_order")
        known = True
    else:
        label, per = _ING_UNIT_FALLBACK.get(unit or "", (unit or "หน่วย", 1.0))
        step, moq, price, known = 1.0, 0.0, None, False
    units = 0.0
    if need_base > 0:
        units = _m.ceil((need_base / per) / step) * step
        if moq and units < moq:
            units = moq
    return {
        "order_unit": label,
        "qty_per_order": round(per, 4),
        "units": int(units) if float(units).is_integer() else round(units, 2),
        "actual_base": round(units * per, 2),
        "price_per_order": price,
        "pack_known": known,
    }


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

_ING_SOURCING_DDL = """
CREATE TABLE IF NOT EXISTS ingredient_sourcing (
  ingredient_code TEXT NOT NULL,
  branch          TEXT NOT NULL DEFAULT '',
  mode            TEXT NOT NULL DEFAULT 'central',
  note            TEXT,
  updated_at      TEXT DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (ingredient_code, branch)
)"""


def _ing_sourcing(con):
    """Returns (defaults, per_branch) so a lookup is default → branch override."""
    con.execute(_ING_SOURCING_DDL)
    defaults, per_branch = {}, {}
    for r in con.execute("SELECT ingredient_code, branch, mode FROM ingredient_sourcing"):
        mode = (r["mode"] or "central").lower()
        if r["branch"]:
            per_branch[(r["ingredient_code"], r["branch"])] = mode
        else:
            defaults[r["ingredient_code"]] = mode
    return defaults, per_branch


def _ing_mode(code: str, branch: str, defaults: dict, per_branch: dict) -> str:
    if not code:
        return "central"
    return per_branch.get((code, branch or ""), defaults.get(code, "central"))


def _ing_plan_rows(con, start: str, end: str, days: int, branch: Optional[str],
                   group: Optional[str], basis: str, buffer_pct: float,
                   ing_type: Optional[str], q: Optional[str], group_family: bool,
                   per_branch: bool, include: str = "all"):
    """Core aggregation: consumption per ingredient (optionally per branch)."""
    filters = ["b.name != 'ALL'", "d.day BETWEEN :start AND :end"]
    params: Dict = {"start": start, "end": end}
    if branch:
        filters.append("b.name = :branch")
        params["branch"] = branch
    _group_filter(filters, params, group)
    if ing_type:
        filters.append("bi.ingredient_type = :itype")
        params["itype"] = ing_type
    if q:
        filters.append("(bi.ingredient_name LIKE :q OR bi.ingredient_code LIKE :q)")
        params["q"] = f"%{q}%"

    # trend windows: last 7 days of the range vs the 7 before it
    params["r_start"] = con.execute("SELECT date(:e,'-6 day')", {"e": end}).fetchone()[0]
    params["p_start"] = con.execute("SELECT date(:e,'-13 day')", {"e": end}).fetchone()[0]

    key_cols = "b.name AS branch, " if per_branch else ""
    grp_cols = "b.branch_id, " if per_branch else ""
    sql = f"""
        SELECT {key_cols}
               COALESCE(NULLIF(bi.ingredient_code,''), '__' || bi.ingredient_name) AS ing_key,
               MAX(bi.ingredient_code) AS code,
               MAX(bi.ingredient_name) AS name,
               MAX(bi.ingredient_type) AS type,
               bi.unit                 AS unit,
               SUM(d.qty * bi.qty_per_serving) AS qty,
               SUM(CASE WHEN d.day >= :r_start THEN d.qty * bi.qty_per_serving ELSE 0 END) AS qty_recent,
               SUM(CASE WHEN d.day >= :p_start AND d.day < :r_start
                        THEN d.qty * bi.qty_per_serving ELSE 0 END) AS qty_prev,
               SUM(d.qty * bi.ingredient_cost) AS cost,
               COUNT(DISTINCT d.sku_id) AS n_menus
          FROM v_sales_sku_daily d
          JOIN branches b         ON b.branch_id = d.branch_id
          JOIN {_ING_BOM_MAP} m   ON m.sale_sku  = d.sku_id
          JOIN bom_ingredients bi ON bi.sku_id   = m.bom_sku
         WHERE {' AND '.join(filters)}
         GROUP BY {grp_cols}ing_key, bi.unit
    """
    raw = con.execute(sql, params).fetchall()

    # trading days per branch inside the window (a branch that opened mid-window,
    # or closed for refurbishment, must not be averaged over days it never sold)
    tf = ["b.name != 'ALL'", "d.day BETWEEN :start AND :end"]
    tp: Dict = {"start": start, "end": end}
    if branch:
        tf.append("b.name = :branch")
        tp["branch"] = branch
    _group_filter(tf, tp, group)
    trading, trading_recent = {}, {}
    for r in con.execute(f"""
        SELECT b.name AS branch, COUNT(DISTINCT d.day) AS nd,
               COUNT(DISTINCT CASE WHEN d.day >= date(:end,'-6 day') THEN d.day END) AS nr
          FROM v_sales_sku_daily d JOIN branches b ON b.branch_id = d.branch_id
         WHERE {' AND '.join(tf)} GROUP BY b.branch_id
    """, tp):
        trading[r["branch"]] = int(r["nd"] or 0)
        trading_recent[r["branch"]] = int(r["nr"] or 0)
    # chain-wide rows span every branch, so the window itself is the right divisor
    scope_days = trading.get(branch, days) if branch else days
    scope_recent = trading_recent.get(branch, min(7, days)) if branch else min(7, days)

    recent_days = min(7, days)
    out = []
    for r in raw:
        qty = float(r["qty"] or 0)
        if qty <= 0:
            continue
        cost = float(r["cost"] or 0)
        bkey = r["branch"] if per_branch else branch
        d_eff = (trading.get(bkey, days) if per_branch else scope_days) or days
        r_eff = (trading_recent.get(bkey, recent_days) if per_branch else scope_recent) or recent_days
        per_day_avg = qty / d_eff
        per_day_recent = float(r["qty_recent"] or 0) / r_eff
        per_day = per_day_recent if (basis == "recent" and per_day_recent > 0) else per_day_avg
        mult = 1.0 + (buffer_pct / 100.0)
        unit_cost = (cost / qty) if qty else 0.0
        prev = float(r["qty_prev"] or 0)
        trend = None
        if prev > 0 and days >= 14:
            trend = round(100.0 * (float(r["qty_recent"] or 0) - prev) / prev, 1)
        row = {
            "code": r["code"] or "",
            "name": r["name"] or "",
            "type": r["type"] or "other",
            "unit": r["unit"] or "",
            "qty_used": round(qty, 2),
            "per_day": round(per_day, 3),
            "per_week": round(per_day * 7, 2),
            "per_month": round(per_day * 30, 2),
            "order_week": round(per_day * 7 * mult, 2),
            "order_month": round(per_day * 30 * mult, 2),
            "unit_cost": round(unit_cost, 4),
            "cost_used": round(cost, 2),
            "value_week": round(per_day * 7 * mult * unit_cost, 2),
            "value_month": round(per_day * 30 * mult * unit_cost, 2),
            "n_menus": int(r["n_menus"] or 0),
            "trend_pct": trend,
        }
        if per_branch:
            row["branch"] = r["branch"]
        out.append(row)

    # tag sourcing BEFORE the family rollup — the rollup keys on it so a
    # central item never merges into the same line as a branch-sourced one
    src_def, src_branch = _ing_sourcing(con)
    for r in out:
        r["sourcing"] = _ing_mode(r["code"], r.get("branch") or branch or "",
                                  src_def, src_branch)
    if include == "central":
        out = [r for r in out if r["sourcing"] == "central"]
    elif include == "local":
        out = [r for r in out if r["sourcing"] == "local"]

    if group_family:
        fam: Dict[str, dict] = {}
        for r in out:
            k = ((r.get("branch") or "") + "|" + _ingredient_family(r["name"])
                 + "|" + (r["unit"] or "") + "|" + (r.get("sourcing") or ""))
            f = fam.get(k)
            if not f:
                f = dict(r)
                f["name"] = _ingredient_family(r["name"])
                f["code"] = ""
                f["n_variants"] = 0
                fam[k] = f
            else:
                for fld in ("qty_used", "per_day", "per_week", "per_month",
                            "order_week", "order_month", "cost_used",
                            "value_week", "value_month"):
                    f[fld] = round(f[fld] + r[fld], 3)
                f["n_menus"] = max(f["n_menus"], r["n_menus"])
            f["n_variants"] += 1
        out = list(fam.values())
        for f in out:
            f["unit_cost"] = round(f["cost_used"] / f["qty_used"], 4) if f["qty_used"] else 0

    packs = _ing_order_units(con)
    seen_units: Dict[str, set] = {}
    for r in out:
        key = (r.get("branch") or "") + "|" + (r["code"] or r["name"])
        seen_units.setdefault(key, set()).add(r["unit"])
    for r in out:
        key = (r.get("branch") or "") + "|" + (r["code"] or r["name"])
        r["mixed_unit"] = len(seen_units.get(key, ())) > 1
        r["order_week_disp"] = _ing_pretty(r["order_week"], r["unit"])[2]
        r["order_month_disp"] = _ing_pretty(r["order_month"], r["unit"])[2]
        r["per_day_disp"] = _ing_pretty(r["per_day"], r["unit"])[2]
        # consumption -> whole ordering units (rounded UP)
        pk = packs.get(r["code"]) if r["code"] else None
        if pk and (pk.get("base_unit") or r["unit"]) != r["unit"]:
            pk = None                      # pack is for a different base unit
        for hz in ("week", "month"):
            o = _ing_to_order(r["order_" + hz], r["unit"], pk)
            r["order_unit"] = o["order_unit"]
            r["qty_per_order"] = o["qty_per_order"]
            r["pack_known"] = o["pack_known"]
            r[f"order_{hz}_units"] = o["units"]
            r[f"order_{hz}_actual"] = o["actual_base"]
            r[f"order_{hz}_units_disp"] = (
                f"{o['units']:,g} {o['order_unit']}"
                # the carry-over only matters when a real pack size forces it;
                # on the กก./ลิตร fallback the parenthetical just repeats itself
                + (f" ({_ing_pretty(o['actual_base'], r['unit'])[2]})"
                   if o["pack_known"] and o["qty_per_order"] != 1 else ""))
            if o["price_per_order"]:
                r[f"value_{hz}"] = round(o["units"] * float(o["price_per_order"]), 2)
    out.sort(key=lambda x: x["value_month"], reverse=True)
    return out


def _ing_trading_days(con, start: str, end: str, branch: str) -> int:
    """Days this branch actually sold inside the window (the averaging divisor)."""
    row = con.execute(
        "SELECT COUNT(DISTINCT d.day) FROM v_sales_sku_daily d "
        "JOIN branches b ON b.branch_id = d.branch_id "
        "WHERE b.name = :branch AND d.day BETWEEN :start AND :end",
        {"branch": branch, "start": start, "end": end}).fetchone()
    return int(row[0] or 0)


def _ing_reapply_order_units(con, rows):
    """Re-round to whole packs after the seasonal uplift — scaling the already
    rounded number would leave fractional packs on the order."""
    packs = _ing_order_units(con)
    for r in rows:
        pk = packs.get(r["code"]) if r["code"] else None
        if pk and (pk.get("base_unit") or r["unit"]) != r["unit"]:
            pk = None
        for hz in ("week", "month"):
            o = _ing_to_order(r["order_" + hz], r["unit"], pk)
            r[f"order_{hz}_units"] = o["units"]
            r[f"order_{hz}_actual"] = o["actual_base"]
            r[f"order_{hz}_units_disp"] = (
                f"{o['units']:,g} {o['order_unit']}"
                + (f" ({_ing_pretty(o['actual_base'], r['unit'])[2]})"
                   if o["pack_known"] and o["qty_per_order"] != 1 else ""))
            if o["price_per_order"]:
                r[f"value_{hz}"] = round(o["units"] * float(o["price_per_order"]), 2)
        r["order_week_disp"] = _ing_pretty(r["order_week"], r["unit"])[2]
        r["order_month_disp"] = _ing_pretty(r["order_month"], r["unit"])[2]
        r["per_day_disp"] = _ing_pretty(r["per_day"], r["unit"])[2]


def _ing_coverage(con, start: str, end: str, branch: Optional[str], group: Optional[str]):
    """How much of what was sold actually has a recipe behind it.

    Buffet TIER packages are excluded from the denominator — they are the cover
    charge, not a dish, so they can never have ingredients."""
    filters = ["b.name != 'ALL'", "d.day BETWEEN :start AND :end",
               f"s.sku_code NOT IN {_EASY_TIER_CODES_SQL}"]
    params: Dict = {"start": start, "end": end}
    if branch:
        filters.append("b.name = :branch")
        params["branch"] = branch
    _group_filter(filters, params, group)
    row = con.execute(f"""
        SELECT SUM(d.qty) AS tot,
               SUM(CASE WHEN m.sale_sku IS NOT NULL THEN d.qty ELSE 0 END) AS cov
          FROM v_sales_sku_daily d
          JOIN branches b ON b.branch_id = d.branch_id
          JOIN skus s     ON s.sku_id    = d.sku_id
          LEFT JOIN {_ING_BOM_MAP} m ON m.sale_sku = d.sku_id
         WHERE {' AND '.join(filters)}
    """, params).fetchone()
    tot = float(row["tot"] or 0)
    cov = float(row["cov"] or 0)
    missing = con.execute(f"""
        SELECT s.sku_code AS code, s.description AS name, ROUND(SUM(d.qty)) AS qty
          FROM v_sales_sku_daily d
          JOIN branches b ON b.branch_id = d.branch_id
          JOIN skus s     ON s.sku_id    = d.sku_id
          LEFT JOIN {_ING_BOM_MAP} m ON m.sale_sku = d.sku_id
         WHERE {' AND '.join(filters)} AND m.sale_sku IS NULL
         GROUP BY s.sku_id ORDER BY qty DESC LIMIT 8
    """, params).fetchall()
    return {
        "pct": round(100.0 * cov / tot, 1) if tot else 0.0,
        "qty_total": round(tot),
        "qty_covered": round(cov),
        "top_missing": [{"code": r["code"], "name": r["name"] or "", "qty": int(r["qty"] or 0)}
                        for r in missing],
    }


@app.get("/api/ingredients/order-plan")
@ttl_cache
def ingredients_order_plan(
    branch:       Optional[str] = Query(None),
    group:        Optional[str] = Query(None),
    days:         int           = Query(28, description="lookback window in days"),
    date_to:      Optional[str] = Query(None),
    basis:        str           = Query("avg", description="avg = whole window · recent = last 7 days"),
    buffer_pct:   float         = Query(10.0, description="safety buffer added on top of forecast demand"),
    type:         Optional[str] = Query(None, description="ingredient_type filter"),
    q:            Optional[str] = Query(None),
    group_family: bool          = Query(False),
    include:      str           = Query("all", description="all | central (แนะนำสั่ง) | local (สาขาซื้อเอง)"),
    season:       str           = Query("off", description="off | auto — scale for festivals from last year"),
    cycle_start:  Optional[str] = Query(None, description="first day of the cycle being ordered for"),
    limit:        int           = Query(400),
):
    """Per-branch ingredient demand → how much to order per week / per month.

    Derived from real sales × BoM. Excludes on-hand stock by design (see the
    module note above), so the answer is "expected consumption", and the caller
    subtracts what is already in the fridge."""
    buffer_pct = max(0.0, min(float(buffer_pct or 0), 100.0))
    basis = "recent" if str(basis).lower() == "recent" else "avg"
    with get_db() as con:
        start, end, days = _ing_window(con, days, date_to)
        if not start:
            return {"available": False, "reason": "no daily sales data"}
        include = include if include in ("all", "central", "local") else "all"
        rows = _ing_plan_rows(con, start, end, days, branch, group, basis, buffer_pct,
                              type, q, group_family, per_branch=False, include=include)
        n_local = len(_ing_plan_rows(con, start, end, days, branch, group, basis,
                                     buffer_pct, type, q, group_family,
                                     per_branch=False, include="local"))
        cov = _ing_coverage(con, start, end, branch, group)
        # festival / promotion scaling — applied last so the base numbers above
        # stay the honest measured demand and the uplift is auditable
        c_start = cycle_start or con.execute("SELECT date('now','localtime')").fetchone()[0]
        c_end = con.execute("SELECT date(:s,'+6 day')", {"s": c_start}).fetchone()[0]
        season_info = _ing_season_resolve(con, branch, c_start, c_end,
                                          "auto" if season == "auto" else "off")
        sf = float(season_info.get("factor") or 1.0)
        if abs(sf - 1.0) > 1e-6:
            for r in rows:
                for k in ("per_day", "per_week", "per_month", "order_week", "order_month",
                          "value_week", "value_month"):
                    r[k] = round(r[k] * sf, 4)
                r["season_factor"] = sf
            _ing_reapply_order_units(con, rows)
        by_branch = []
        if not branch:
            per_b = _ing_plan_rows(con, start, end, days, None, group, basis, buffer_pct,
                                   type, q, False, per_branch=True, include=include)
            agg: Dict[str, dict] = {}
            for r in per_b:
                a = agg.setdefault(r["branch"], {"branch": r["branch"], "ingredients": 0,
                                                 "value_week": 0.0, "value_month": 0.0})
                a["ingredients"] += 1
                a["value_week"] += r["value_week"]
                a["value_month"] += r["value_month"]
            by_branch = sorted(agg.values(), key=lambda x: x["value_month"], reverse=True)
            for a in by_branch:
                a["value_week"] = round(a["value_week"], 2)
                a["value_month"] = round(a["value_month"], 2)

    totals = {
        "ingredients": len(rows),
        "value_week": round(sum(r["value_week"] for r in rows), 2),
        "value_month": round(sum(r["value_month"] for r in rows), 2),
    }
    return {
        "available": True,
        "meta": {"date_from": start, "date_to": end, "days": days, "branch": branch or "",
                 "group": group or "", "basis": basis, "buffer_pct": buffer_pct,
                 "include": include, "local_count": n_local, "season": season_info,
                 "trading_days": _ing_trading_days(con, start, end, branch) if branch else days,
                 "coverage": cov, "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M")},
        "totals": totals,
        "rows": rows[:max(1, min(int(limit or 400), 2000))],
        "branches": by_branch,
    }


@app.get("/api/ingredients/order-plan.xlsx")
def ingredients_order_plan_xlsx(
    branch:     Optional[str] = Query(None),
    group:      Optional[str] = Query(None),
    days:       int           = Query(28),
    date_to:    Optional[str] = Query(None),
    basis:      str           = Query("avg"),
    buffer_pct: float         = Query(10.0),
):
    """Excel export of the order plan — one row per branch × ingredient."""
    import io as _io_ing
    from fastapi.responses import StreamingResponse as _SRing
    try:
        from openpyxl import Workbook as _WB
        from openpyxl.styles import Font as _F, PatternFill as _PF, Alignment as _AL
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"openpyxl unavailable: {e}")
    buffer_pct = max(0.0, min(float(buffer_pct or 0), 100.0))
    basis = "recent" if str(basis).lower() == "recent" else "avg"
    with get_db() as con:
        start, end, days = _ing_window(con, days, date_to)
        if not start:
            raise HTTPException(status_code=404, detail="ไม่มีข้อมูลยอดขายรายวัน")
        rows = _ing_plan_rows(con, start, end, days, branch, group, basis, buffer_pct,
                              None, None, False, per_branch=not branch)
    wb = _WB()
    ws = wb.active
    ws.title = "แผนสั่งวัตถุดิบ"
    ws.append([f"แผนสั่งวัตถุดิบ · ข้อมูลขายจริง {start} ถึง {end} ({days} วัน) · "
               f"เผื่อ {buffer_pct:g}% · ฐาน {'7 วันล่าสุด' if basis == 'recent' else 'เฉลี่ยทั้งช่วง'}"])
    ws["A1"].font = _F(bold=True, size=12)
    ws.append([])
    head = (["สาขา"] if not branch else []) + [
        "รหัส", "วัตถุดิบ", "ประเภท", "หน่วยนับ", "ใช้จริงในช่วง", "เฉลี่ย/วัน",
        "ใช้/สัปดาห์", "ใช้/เดือน", "หน่วยสั่ง", "ขนาดบรรจุ",
        "สั่ง/สัปดาห์ (หน่วยสั่ง)", "สั่ง/เดือน (หน่วยสั่ง)",
        "ต้นทุน/หน่วย", "มูลค่า/สัปดาห์", "มูลค่า/เดือน",
        "จำนวนเมนูที่ใช้", "เทรนด์ 7 วัน %", "ตั้งขนาดบรรจุแล้ว", "การจัดหา"]
    ws.append(head)
    for c in ws[3]:
        c.font = _F(bold=True, color="FFFFFF")
        c.fill = _PF("solid", fgColor="0F766E")
        c.alignment = _AL(horizontal="center", wrap_text=True)
    for r in rows:
        ws.append(([r.get("branch", "")] if not branch else []) + [
            r["code"], r["name"], r["type"], r["unit"], r["qty_used"], r["per_day"],
            r["order_week"], r["order_month"], r.get("order_unit", ""),
            r.get("qty_per_order", ""), r.get("order_week_units", ""),
            r.get("order_month_units", ""), r["unit_cost"], r["value_week"],
            r["value_month"], r["n_menus"], r["trend_pct"],
            "ใช่" if r.get("pack_known") else "ยังไม่ตั้ง",
            "สาขาซื้อเอง" if r.get("sourcing") == "local" else "สั่งส่วนกลาง"])
    widths = ([22] if not branch else []) + [12, 34, 10, 9, 13, 11, 12, 12, 12,
                                             11, 15, 15, 12, 13, 13, 9, 12, 13, 13]
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[ws.cell(row=3, column=i).column_letter].width = w
    ws.freeze_panes = "A4"
    buf = _io_ing.BytesIO()
    wb.save(buf)
    buf.seek(0)
    # HTTP headers are latin-1: a Thai branch name in filename= raises
    # UnicodeEncodeError and 500s the export. RFC 5987 carries the real name.
    from urllib.parse import quote as _q
    tag = (branch or group or "all").replace(" ", "_")
    ascii_tag = tag.encode("ascii", "ignore").decode() or "all"
    return _SRing(buf, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                  headers={"Content-Disposition":
                           f"attachment; filename=ORDER_PLAN_{ascii_tag}_{end}.xlsx; "
                           f"filename*=UTF-8''{_q(f'ORDER_PLAN_{tag}_{end}.xlsx')}"})


@app.get("/api/ingredients/order-letter.print")
def ingredients_order_letter(
    branch:     Optional[str] = Query(None, description="one branch; omit for every branch (one page each)"),
    group:      Optional[str] = Query(None),
    days:       int           = Query(28),
    date_to:    Optional[str] = Query(None),
    basis:      str           = Query("avg"),
    buffer_pct: float         = Query(10.0),
    horizon:    str           = Query("week", description="week | month"),
    top:        int           = Query(40, description="ingredients listed per branch"),
    issuer:     Optional[str] = Query(None, description="department line on the signature block"),
    note:       Optional[str] = Query(None, description="extra paragraph before the table"),
):
    """Printable Thai notice to each branch: how much of each ingredient to order.

    One A4 page per branch. Deliberately states that on-hand stock is NOT
    deducted and leaves the signature blank — this is a template a human signs,
    not an auto-authorised purchase order."""
    import html as _html_ing
    horizon = "month" if str(horizon).lower() == "month" else "week"
    buffer_pct = max(0.0, min(float(buffer_pct or 0), 100.0))
    basis = "recent" if str(basis).lower() == "recent" else "avg"
    top = max(5, min(int(top or 40), 200))
    esc = lambda s: _html_ing.escape(str(s or ""))

    with get_db() as con:
        start, end, days = _ing_window(con, days, date_to)
        if not start:
            raise HTTPException(status_code=404, detail="ไม่มีข้อมูลยอดขายรายวัน")
        if branch:
            names = [branch]
        else:
            bf = ["name != 'ALL'", "COALESCE(active,1)=1"]
            bp: Dict = {}
            if group:
                bf.append("COALESCE(branch_type,'master') = :branch_group")
                bp["branch_group"] = group
            names = [r["name"] for r in con.execute(
                f"SELECT name FROM branches WHERE {' AND '.join(bf)} ORDER BY name", bp)]
        pages = []
        for nm in names:
            allrows = _ing_plan_rows(con, start, end, days, nm, None, basis, buffer_pct,
                                     None, None, False, per_branch=False)
            rows = [r for r in allrows if r.get("sourcing") != "local"]
            local = [r for r in allrows if r.get("sourcing") == "local"]
            if rows:
                pages.append((nm, rows[:top], _ing_coverage(con, start, end, nm, None), local))

    if not pages:
        raise HTTPException(status_code=404, detail="ไม่พบข้อมูลการใช้วัตถุดิบของสาขาที่เลือก")

    hz_label = "สัปดาห์" if horizon == "week" else "เดือน"
    qty_key = "order_week_units_disp" if horizon == "week" else "order_month_units_disp"
    val_key = "value_week" if horizon == "week" else "value_month"
    today = datetime.now().strftime("%d/%m/%Y")
    issuer_line = esc(issuer or "ฝ่ายวิเคราะห์ต้นทุนและจัดซื้อ")
    basis_label = "ค่าเฉลี่ย 7 วันล่าสุด" if basis == "recent" else f"ค่าเฉลี่ยตลอด {days} วัน"

    body = []
    for nm, rows, cov, local in pages:
        total_val = sum(r[val_key] for r in rows)
        trs = []
        for i, r in enumerate(rows, start=1):
            tr = r.get("trend_pct")
            trend_html = ("<span style='color:#b91c1c'>▲ %.0f%%</span>" % tr if tr and tr >= 10 else
                          "<span style='color:#047857'>▼ %.0f%%</span>" % abs(tr) if tr and tr <= -10 else
                          "<span style='color:#94a3b8'>–</span>")
            trs.append(
                f"<tr><td class='c'>{i}</td><td class='mono'>{esc(r['code'])}</td>"
                f"<td>{esc(r['name'])}</td><td class='r'>{esc(r['per_day_disp'])}</td>"
                f"<td class='r b'>{esc(r[qty_key])}</td>"
                f"<td class='r'>{r[val_key]:,.0f}</td><td class='c'>{trend_html}</td></tr>")
        body.append(f"""
<section class="page">
  <div class="head">
    <div class="brand">โอชิเน (Oshinei)</div>
    <div class="doc">ใบแจ้งปริมาณสั่งวัตถุดิบที่แนะนำ<br><span>Recommended Ingredient Order</span></div>
  </div>
  <table class="meta"><tr>
    <td><b>เรียน</b> ผู้จัดการสาขา {esc(nm)}</td>
    <td><b>วันที่</b> {today}</td></tr><tr>
    <td><b>เรื่อง</b> ปริมาณวัตถุดิบที่ควรสั่งประจำ{hz_label}</td>
    <td><b>อ้างอิงยอดขายจริง</b> {start} ถึง {end} ({days} วัน)</td></tr>
  </table>
  <p class="lead">ตามที่{issuer_line}ได้วิเคราะห์ปริมาณการใช้วัตถุดิบจริงของสาขา{esc(nm)}
     จากยอดขายรายวันคูณกับสูตรมาตรฐาน (BoM) จึงขอแจ้งปริมาณที่ควรสั่งประจำ{hz_label}
     โดยคำนวณจาก{basis_label} และเผื่อความผันผวนไว้ {buffer_pct:g}% ดังรายการต่อไปนี้</p>
  {('<p class="lead">' + esc(note) + '</p>') if note else ''}
  <table class="grid">
    <thead><tr><th>ลำดับ</th><th>รหัส</th><th>วัตถุดิบ</th><th>ใช้เฉลี่ย/วัน</th>
      <th>ควรสั่ง/{hz_label}</th><th>มูลค่าโดยประมาณ (บาท)</th><th>แนวโน้ม</th></tr></thead>
    <tbody>{''.join(trs)}</tbody>
    <tfoot><tr><td colspan="5" class="r b">รวมมูลค่าโดยประมาณ</td>
      <td class="r b">{total_val:,.0f}</td><td></td></tr></tfoot>
  </table>
  {('<p class="excl"><b>รายการที่สาขาจัดหาเอง (ไม่รวมในใบสั่งนี้):</b> '
     + esc(' · '.join(x['name'] for x in local[:14]))
     + (f" และอื่น ๆ อีก {len(local) - 14} รายการ" if len(local) > 14 else '')
     + '</p>') if local else ''}
  <div class="notes">
    <b>หมายเหตุสำคัญ</b>
    <ol>
      <li><b>ตัวเลขนี้คือปริมาณที่คาดว่าจะ “ใช้” ยังไม่ได้หักสต๊อกคงเหลือในร้าน</b>
          กรุณาตรวจนับของคงเหลือก่อนออกใบสั่งซื้อทุกครั้ง</li>
      <li>คำนวณจากสูตร BoM ที่ครอบคลุม {cov['pct']:g}% ของจำนวนจานที่ขายในช่วงอ้างอิง
          (เมนูที่ยังไม่มีสูตรจะไม่ปรากฏในรายการนี้)</li>
      <li>ปริมาณข้างต้นเป็นการประมาณการจากยอดขายที่ผ่านมา หากมีเทศกาล โปรโมชัน
          หรือการปิดปรับปรุง กรุณาปรับเพิ่ม–ลดตามความเหมาะสม</li>
      <li>ช่อง “ควรสั่ง” ปัดขึ้นเป็นจำนวนเต็มหน่วยสั่งแล้ว (ในวงเล็บคือปริมาณจริงที่จะได้รับ)
          รายการที่ยังไม่ได้ตั้งขนาดบรรจุจะแสดงเป็นหน่วยมาตรฐาน (กก./ลิตร)</li>
      <li>คอลัมน์ “แนวโน้ม” เทียบการใช้ 7 วันล่าสุดกับ 7 วันก่อนหน้า
          (▲ = ใช้เพิ่มขึ้น ควรเผื่อเพิ่ม · ▼ = ใช้ลดลง ควรลดปริมาณสั่ง)</li>
    </ol>
  </div>
  <div class="sign">
    <div>
      <div class="line"></div>
      <div class="cap">{issuer_line}</div>
      <div class="cap">ผู้จัดทำ · วันที่ ......../......../........</div>
    </div>
    <div>
      <div class="line"></div>
      <div class="cap">ผู้จัดการสาขา {esc(nm)}</div>
      <div class="cap">ผู้รับทราบ · วันที่ ......../......../........</div>
    </div>
  </div>
</section>""")

    html_out = f"""<!doctype html><html lang="th"><head><meta charset="utf-8">
<title>ใบแจ้งปริมาณสั่งวัตถุดิบ · {esc(pages[0][0] if branch else 'ทุกสาขา')} · {end}</title>
<style>
  @page {{ size: A4; margin: 14mm 12mm; }}
  * {{ box-sizing: border-box; }}
  body {{ font-family:"Sarabun","Leelawadee UI","Tahoma",sans-serif; color:#0f172a;
         margin:0; background:#f1f5f9; font-size:12px; }}
  .page {{ background:#fff; width:190mm; min-height:263mm; margin:8mm auto; padding:10mm 9mm;
           box-shadow:0 1px 6px rgba(0,0,0,.12); page-break-after:always; }}
  .page:last-child {{ page-break-after:auto; }}
  .head {{ display:flex; justify-content:space-between; align-items:flex-end;
           border-bottom:2.5px solid #0f766e; padding-bottom:6px; margin-bottom:10px; }}
  .brand {{ font-size:20px; font-weight:700; color:#0f766e; letter-spacing:.5px; }}
  .doc {{ text-align:right; font-size:14px; font-weight:700; line-height:1.3; }}
  .doc span {{ font-size:10px; font-weight:400; color:#64748b; }}
  table.meta {{ width:100%; margin-bottom:10px; font-size:11.5px; }}
  table.meta td {{ padding:2px 0; }}
  table.meta td:last-child {{ text-align:right; color:#334155; }}
  .lead {{ text-indent:2em; line-height:1.75; margin:0 0 10px; }}
  table.grid {{ width:100%; border-collapse:collapse; font-size:11px; }}
  table.grid th {{ background:#0f766e; color:#fff; font-weight:600; padding:5px 4px;
                   border:1px solid #0f766e; }}
  table.grid td {{ border:1px solid #cbd5e1; padding:3.5px 5px; }}
  table.grid tbody tr:nth-child(even) {{ background:#f8fafc; }}
  table.grid tfoot td {{ background:#ecfdf5; border-top:2px solid #0f766e; }}
  .r {{ text-align:right; }} .c {{ text-align:center; }} .b {{ font-weight:700; }}
  .mono {{ font-family:"Consolas",monospace; font-size:10px; color:#475569; }}
  .excl {{ margin-top:8px; font-size:10.5px; color:#475569; background:#f1f5f9;
           border-left:3px solid #94a3b8; padding:6px 9px; line-height:1.6; }}
  .notes {{ margin-top:10px; font-size:10.5px; background:#fffbeb; border-left:3px solid #f59e0b;
            padding:7px 10px; line-height:1.6; }}
  .notes ol {{ margin:4px 0 0 16px; padding:0; }}
  .sign {{ display:flex; justify-content:space-between; gap:24px; margin-top:24px; }}
  .sign > div {{ width:46%; text-align:center; }}
  .line {{ border-bottom:1px dotted #475569; height:34px; }}
  .cap {{ font-size:10.5px; color:#475569; margin-top:3px; }}
  .toolbar {{ text-align:center; padding:10px; }}
  .toolbar button {{ background:#0f766e; color:#fff; border:0; border-radius:6px;
                     padding:8px 18px; font-size:13px; cursor:pointer; font-family:inherit; }}
  @media print {{ body {{ background:#fff; }} .toolbar {{ display:none; }}
                  .page {{ margin:0; box-shadow:none; width:auto; min-height:0; padding:0; }}
                  table.grid th {{ -webkit-print-color-adjust:exact; print-color-adjust:exact; }} }}
</style></head><body>
<div class="toolbar"><button onclick="window.print()">🖨️ พิมพ์ / บันทึกเป็น PDF</button>
  &nbsp;<span style="font-size:11px;color:#64748b">{len(pages)} สาขา · 1 หน้าต่อสาขา</span></div>
{''.join(body)}
</body></html>"""
    return Response(content=html_out, media_type="text/html; charset=utf-8")


class OrderUnitRow(BaseModel):
    ingredient_code: str
    base_unit: str
    order_unit: str
    qty_per_order: float
    price_per_order: Optional[float] = None
    moq: Optional[float] = 1
    round_step: Optional[float] = 1
    note: Optional[str] = None


@app.get("/api/ingredients/order-units")
def ingredients_order_units(q: Optional[str] = Query(None)):
    """The consumption-unit → ordering-unit map (pack sizes), plus every
    ingredient still on the fallback so ops can see what needs setting up."""
    with get_db() as con:
        packs = _ing_order_units(con)
        used = con.execute("""
            SELECT ingredient_code AS code, MAX(ingredient_name) AS name, unit,
                   COUNT(*) AS n_lines
              FROM bom_ingredients
             WHERE ingredient_code <> ''
             GROUP BY ingredient_code, unit
        """).fetchall()
    rows = []
    for u in used:
        p = packs.get(u["code"])
        if p and (p.get("base_unit") or u["unit"]) != u["unit"]:
            p = None
        fb = _ING_UNIT_FALLBACK.get(u["unit"] or "", (u["unit"] or "หน่วย", 1.0))
        rows.append({
            "code": u["code"], "name": u["name"] or "", "base_unit": u["unit"] or "",
            "n_lines": u["n_lines"],
            "order_unit": (p or {}).get("order_unit", fb[0]),
            "qty_per_order": (p or {}).get("qty_per_order", fb[1]),
            "price_per_order": (p or {}).get("price_per_order"),
            "moq": (p or {}).get("moq", 1), "round_step": (p or {}).get("round_step", 1),
            "note": (p or {}).get("note", ""), "source": (p or {}).get("source", "fallback"),
            "pack_known": bool(p),
        })
    if q:
        ql = q.lower()
        rows = [r for r in rows if ql in r["code"].lower() or ql in r["name"].lower()]
    rows.sort(key=lambda r: (r["pack_known"], -r["n_lines"]))
    return {"rows": rows,
            "summary": {"total": len(rows),
                        "with_pack": sum(1 for r in rows if r["pack_known"]),
                        "fallback": sum(1 for r in rows if not r["pack_known"])}}


@app.post("/api/ingredients/order-units", dependencies=[Depends(require_write_token)])
def ingredients_order_units_save(rows: List[OrderUnitRow]):
    """Upsert pack sizes. qty_per_order must be > 0 — a zero would make the
    ceil() divide by zero and silently fall back to 1 base unit per pack."""
    bad = [r.ingredient_code for r in rows if not r.ingredient_code or r.qty_per_order <= 0]
    if bad:
        raise HTTPException(400, f"qty_per_order ต้องมากกว่า 0: {', '.join(bad[:5])}")
    with get_db() as con:
        con.execute(_ING_ORDER_DDL)
        for r in rows:
            con.execute("""
                INSERT INTO ingredient_order_unit
                    (ingredient_code, base_unit, order_unit, qty_per_order,
                     price_per_order, moq, round_step, note, source, updated_at)
                VALUES (?,?,?,?,?,?,?,?, 'manual', CURRENT_TIMESTAMP)
                ON CONFLICT(ingredient_code) DO UPDATE SET
                    base_unit=excluded.base_unit, order_unit=excluded.order_unit,
                    qty_per_order=excluded.qty_per_order,
                    price_per_order=excluded.price_per_order,
                    moq=excluded.moq, round_step=excluded.round_step,
                    note=excluded.note, source='manual',
                    updated_at=CURRENT_TIMESTAMP
            """, (r.ingredient_code.strip(), r.base_unit.strip(), r.order_unit.strip(),
                  float(r.qty_per_order), r.price_per_order,
                  float(r.moq or 1), float(r.round_step or 1), r.note))
        con.commit()
    invalidate_cache()
    return {"ok": True, "saved": len(rows)}


class SourcingRow(BaseModel):
    ingredient_code: str
    branch: Optional[str] = ""      # "" = ค่าเริ่มต้นทุกสาขา
    mode: str = "central"           # central = แนะนำสั่ง · local = สาขาซื้อเอง
    note: Optional[str] = None


@app.get("/api/ingredients/sourcing")
def ingredients_sourcing(branch: Optional[str] = Query(None)):
    """Which ingredients the plan recommends ordering, and which are left to the
    branch. Anything with no row is 'central' (recommend) by default."""
    with get_db() as con:
        con.execute(_ING_SOURCING_DDL)
        rows = [dict(r) for r in con.execute(
            "SELECT ingredient_code, branch, mode, note, updated_at "
            "FROM ingredient_sourcing ORDER BY branch, ingredient_code")]
    if branch is not None:
        rows = [r for r in rows if r["branch"] in ("", branch)]
    return {"rows": rows,
            "summary": {"local_default": sum(1 for r in rows
                                             if not r["branch"] and r["mode"] == "local"),
                        "branch_overrides": sum(1 for r in rows if r["branch"])}}


@app.post("/api/ingredients/sourcing", dependencies=[Depends(require_write_token)])
def ingredients_sourcing_save(rows: List[SourcingRow]):
    """Set 'สั่งส่วนกลาง' / 'สาขาซื้อเอง' per ingredient (branch='' = ทุกสาขา).

    Setting a branch row back to the default is done by posting mode='default',
    which DELETES the override rather than pinning it — otherwise a later change
    to the chain-wide default would silently not reach that branch."""
    with get_db() as con:
        con.execute(_ING_SOURCING_DDL)
        for r in rows:
            code = (r.ingredient_code or "").strip()
            if not code:
                continue
            br = (r.branch or "").strip()
            mode = (r.mode or "central").lower()
            if mode == "default":
                con.execute("DELETE FROM ingredient_sourcing WHERE ingredient_code=? AND branch=?",
                            (code, br))
                continue
            if mode not in ("central", "local"):
                raise HTTPException(400, f"mode ต้องเป็น central | local | default: {mode}")
            con.execute("""
                INSERT INTO ingredient_sourcing (ingredient_code, branch, mode, note, updated_at)
                VALUES (?,?,?,?, CURRENT_TIMESTAMP)
                ON CONFLICT(ingredient_code, branch) DO UPDATE SET
                    mode=excluded.mode, note=excluded.note, updated_at=CURRENT_TIMESTAMP
            """, (code, br, mode, r.note))
        con.commit()
    invalidate_cache()
    return {"ok": True, "saved": len(rows)}


# ═══════════════════════════════════════════════════════════════════════════
# Order cycles: issue → record what was actually ordered → compliance → notice
# ═══════════════════════════════════════════════════════════════════════════
# The recommendation moves every day as new sales arrive. Comparing what a
# branch ordered last week against a number recomputed today would be a
# different number than the one they were given — indefensible in a warning
# letter. So issuing a cycle SNAPSHOTS the recommendation (issue + issue_line),
# actuals attach to that snapshot, and every comparison reads the frozen
# figures. Re-running the plan later never changes what a branch was judged on.

_ING_ISSUE_DDL = [
    """CREATE TABLE IF NOT EXISTS ingredient_order_issue (
        issue_id     INTEGER PRIMARY KEY,
        branch       TEXT NOT NULL,
        horizon      TEXT NOT NULL DEFAULT 'week',
        period_start TEXT NOT NULL,
        period_end   TEXT NOT NULL,
        basis_from   TEXT, basis_to TEXT, basis TEXT,
        buffer_pct   REAL,
        issued_at    TEXT DEFAULT CURRENT_TIMESTAMP,
        issued_by    TEXT, note TEXT,
        UNIQUE(branch, period_start, period_end, horizon))""",
    """CREATE TABLE IF NOT EXISTS ingredient_order_issue_line (
        issue_id        INTEGER NOT NULL,
        ingredient_code TEXT NOT NULL,
        name TEXT, base_unit TEXT, order_unit TEXT,
        qty_per_order REAL, rec_base REAL, rec_units REAL, unit_cost REAL,
        PRIMARY KEY (issue_id, ingredient_code))""",
    """CREATE TABLE IF NOT EXISTS ingredient_order_actual (
        issue_id        INTEGER NOT NULL,
        ingredient_code TEXT NOT NULL,
        qty_units  REAL NOT NULL DEFAULT 0,
        qty_base   REAL,
        source TEXT, note TEXT,
        updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (issue_id, ingredient_code))""",
]
# Ordering below this share of the recommendation counts as under-ordering.
_ING_TOLERANCE_DEFAULT = 20.0


def _ing_issue_tables(con):
    for ddl in _ING_ISSUE_DDL:
        con.execute(ddl)


def _ing_cycle_dates(con, horizon: str, period_start: Optional[str]):
    """The order cycle a snapshot covers — defaults to the next 7 / 30 days."""
    if period_start:
        start = period_start
    else:
        start = con.execute("SELECT date('now','localtime')").fetchone()[0]
    span = "+6 day" if horizon == "week" else "+29 day"
    end = con.execute("SELECT date(:s, :sp)", {"s": start, "sp": span}).fetchone()[0]
    return start, end


class IssueBody(BaseModel):
    branch: Optional[str] = None      # omit = every active branch
    group: Optional[str] = None
    horizon: str = "week"
    period_start: Optional[str] = None
    days: int = 28
    date_to: Optional[str] = None
    basis: str = "avg"
    buffer_pct: float = 10.0
    top: int = 40
    issued_by: Optional[str] = None
    note: Optional[str] = None


@app.post("/api/ingredients/order-plan/issue", dependencies=[Depends(require_write_token)])
def ingredients_order_issue(body: IssueBody):
    """Freeze today's recommendation as an order cycle, one snapshot per branch.

    Re-issuing the same branch+period REPLACES the snapshot (and its recorded
    actuals) — a cycle is corrected, never silently duplicated."""
    horizon = "month" if str(body.horizon).lower() == "month" else "week"
    buffer_pct = max(0.0, min(float(body.buffer_pct or 0), 100.0))
    basis = "recent" if str(body.basis).lower() == "recent" else "avg"
    with get_db() as con:
        _ing_issue_tables(con)
        start_w, end_w, days = _ing_window(con, body.days, body.date_to)
        if not start_w:
            raise HTTPException(404, "ไม่มีข้อมูลยอดขายรายวัน")
        p_start, p_end = _ing_cycle_dates(con, horizon, body.period_start)
        if body.branch:
            names = [body.branch]
        else:
            bf, bp = ["name != 'ALL'", "COALESCE(active,1)=1"], {}
            if body.group:
                bf.append("COALESCE(branch_type,'master') = :g")
                bp["g"] = body.group
            names = [r["name"] for r in con.execute(
                f"SELECT name FROM branches WHERE {' AND '.join(bf)} ORDER BY name", bp)]
        issued = []
        for nm in names:
            rows = _ing_plan_rows(con, start_w, end_w, days, nm, None, basis, buffer_pct,
                                  None, None, False, per_branch=False, include="central")
            rows = rows[:max(5, min(int(body.top or 40), 300))]
            if not rows:
                continue
            con.execute("""INSERT INTO ingredient_order_issue
                    (branch, horizon, period_start, period_end, basis_from, basis_to,
                     basis, buffer_pct, issued_by, note)
                 VALUES (?,?,?,?,?,?,?,?,?,?)
                 ON CONFLICT(branch, period_start, period_end, horizon) DO UPDATE SET
                     basis_from=excluded.basis_from, basis_to=excluded.basis_to,
                     basis=excluded.basis, buffer_pct=excluded.buffer_pct,
                     issued_at=CURRENT_TIMESTAMP, issued_by=excluded.issued_by,
                     note=excluded.note""",
                        (nm, horizon, p_start, p_end, start_w, end_w, basis,
                         buffer_pct, body.issued_by, body.note))
            iid = con.execute("""SELECT issue_id FROM ingredient_order_issue
                                  WHERE branch=? AND period_start=? AND period_end=? AND horizon=?""",
                              (nm, p_start, p_end, horizon)).fetchone()["issue_id"]
            con.execute("DELETE FROM ingredient_order_issue_line WHERE issue_id=?", (iid,))
            con.execute("DELETE FROM ingredient_order_actual WHERE issue_id=?", (iid,))
            key_units = "order_%s_units" % horizon
            key_base = "order_%s" % horizon
            con.executemany("""INSERT INTO ingredient_order_issue_line
                    (issue_id, ingredient_code, name, base_unit, order_unit,
                     qty_per_order, rec_base, rec_units, unit_cost)
                 VALUES (?,?,?,?,?,?,?,?,?)""",
                            [(iid, r["code"] or r["name"], r["name"], r["unit"],
                              r.get("order_unit") or r["unit"], r.get("qty_per_order") or 1,
                              r[key_base], r.get(key_units) or 0, r["unit_cost"])
                             for r in rows])
            issued.append({"issue_id": iid, "branch": nm, "lines": len(rows)})
        con.commit()
    invalidate_cache()
    return {"ok": True, "horizon": horizon, "period_start": p_start, "period_end": p_end,
            "issued": issued}


@app.get("/api/ingredients/order-cycles")
def ingredients_order_cycles(branch: Optional[str] = Query(None),
                             limit: int = Query(60)):
    """Issued cycles with their compliance counts, newest first."""
    with get_db() as con:
        _ing_issue_tables(con)
        f, p = [], {"lim": max(1, min(int(limit or 60), 500))}
        if branch:
            f.append("i.branch = :branch")
            p["branch"] = branch
        where = ("WHERE " + " AND ".join(f)) if f else ""
        rows = con.execute(f"""
            SELECT i.*,
                   (SELECT COUNT(*) FROM ingredient_order_issue_line l
                     WHERE l.issue_id = i.issue_id) AS lines,
                   (SELECT COUNT(*) FROM ingredient_order_actual a
                     WHERE a.issue_id = i.issue_id AND a.qty_units > 0) AS recorded
              FROM ingredient_order_issue i {where}
             ORDER BY i.period_start DESC, i.branch LIMIT :lim""", p).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["compliance"] = _ing_compliance_summary(con, r["issue_id"])
            out.append(d)
    return {"rows": out}


def _ing_compliance_rows(con, issue_id: int, tolerance_pct: float):
    """Frozen recommendation vs what was actually ordered, per ingredient."""
    tol = max(0.0, min(float(tolerance_pct), 100.0))
    floor = 1.0 - tol / 100.0
    rows = []
    for r in con.execute("""
        SELECT l.*, COALESCE(a.qty_units, 0) AS act_units, a.qty_base AS act_base,
               a.note AS act_note, a.updated_at AS act_at
          FROM ingredient_order_issue_line l
          LEFT JOIN ingredient_order_actual a
                 ON a.issue_id = l.issue_id AND a.ingredient_code = l.ingredient_code
         WHERE l.issue_id = ?
         ORDER BY l.rec_base * COALESCE(l.unit_cost,0) DESC""", (issue_id,)):
        rec_base = float(r["rec_base"] or 0)
        per = float(r["qty_per_order"] or 1) or 1.0
        act_units = float(r["act_units"] or 0)
        act_base = float(r["act_base"]) if r["act_base"] is not None else act_units * per
        if rec_base <= 0:
            continue
        ratio = act_base / rec_base
        if act_units <= 0:
            status = "not_ordered"
        elif ratio < floor:
            status = "under"
        else:
            status = "ok"
        rows.append({
            "ingredient_code": r["ingredient_code"], "name": r["name"] or "",
            "base_unit": r["base_unit"] or "", "order_unit": r["order_unit"] or "",
            "qty_per_order": per,
            "rec_units": float(r["rec_units"] or 0), "rec_base": round(rec_base, 2),
            "rec_disp": _ing_pretty(rec_base, r["base_unit"])[2],
            "act_units": act_units, "act_base": round(act_base, 2),
            "act_disp": _ing_pretty(act_base, r["base_unit"])[2],
            "pct_of_rec": round(100.0 * ratio, 1),
            "gap_base": round(rec_base - act_base, 2),
            "gap_disp": _ing_pretty(max(0.0, rec_base - act_base), r["base_unit"])[2],
            "gap_value": round(max(0.0, rec_base - act_base) * float(r["unit_cost"] or 0), 2),
            "status": status, "note": r["act_note"] or "", "recorded_at": r["act_at"],
        })
    return rows


def _ing_compliance_summary(con, issue_id: int, tolerance_pct: float = _ING_TOLERANCE_DEFAULT):
    rows = _ing_compliance_rows(con, issue_id, tolerance_pct)
    n_not = sum(1 for r in rows if r["status"] == "not_ordered")
    n_under = sum(1 for r in rows if r["status"] == "under")
    return {"lines": len(rows), "not_ordered": n_not, "under": n_under,
            "ok": len(rows) - n_not - n_under,
            "gap_value": round(sum(r["gap_value"] for r in rows
                                   if r["status"] != "ok"), 2),
            "compliant": (n_not + n_under) == 0}


@app.get("/api/ingredients/order-compliance")
def ingredients_order_compliance(issue_id: int = Query(...),
                                 tolerance_pct: float = Query(_ING_TOLERANCE_DEFAULT)):
    """What the branch was told to order vs what it actually ordered."""
    with get_db() as con:
        _ing_issue_tables(con)
        head = con.execute("SELECT * FROM ingredient_order_issue WHERE issue_id=?",
                           (issue_id,)).fetchone()
        if not head:
            raise HTTPException(404, "ไม่พบรอบสั่งนี้")
        rows = _ing_compliance_rows(con, issue_id, tolerance_pct)
        summary = _ing_compliance_summary(con, issue_id, tolerance_pct)
    return {"issue": dict(head), "tolerance_pct": tolerance_pct,
            "summary": summary, "rows": rows}


class ActualRow(BaseModel):
    ingredient_code: str
    qty_units: float = 0
    note: Optional[str] = None


class ActualBody(BaseModel):
    issue_id: int
    rows: List[ActualRow]
    source: Optional[str] = "manual"


@app.post("/api/ingredients/order-actual", dependencies=[Depends(require_write_token)])
def ingredients_order_actual(body: ActualBody):
    """Record what a branch actually ordered, in the ORDER units of that cycle.

    qty is converted to base units using the pack size FROZEN in the issue, so a
    later pack-size change never rewrites history."""
    with get_db() as con:
        _ing_issue_tables(con)
        if not con.execute("SELECT 1 FROM ingredient_order_issue WHERE issue_id=?",
                           (body.issue_id,)).fetchone():
            raise HTTPException(404, "ไม่พบรอบสั่งนี้")
        packs = {r["ingredient_code"]: float(r["qty_per_order"] or 1)
                 for r in con.execute(
                     "SELECT ingredient_code, qty_per_order FROM ingredient_order_issue_line "
                     "WHERE issue_id=?", (body.issue_id,))}
        saved = 0
        for r in body.rows:
            code = (r.ingredient_code or "").strip()
            if code not in packs:
                continue                      # not part of this cycle — ignore silently
            qty = max(0.0, float(r.qty_units or 0))
            con.execute("""INSERT INTO ingredient_order_actual
                    (issue_id, ingredient_code, qty_units, qty_base, source, note, updated_at)
                 VALUES (?,?,?,?,?,?, CURRENT_TIMESTAMP)
                 ON CONFLICT(issue_id, ingredient_code) DO UPDATE SET
                     qty_units=excluded.qty_units, qty_base=excluded.qty_base,
                     source=excluded.source, note=excluded.note,
                     updated_at=CURRENT_TIMESTAMP""",
                        (body.issue_id, code, qty, qty * packs[code], body.source, r.note))
            saved += 1
        con.commit()
    invalidate_cache()
    return {"ok": True, "saved": saved}


@app.get("/api/ingredients/compliance-letter.print")
def ingredients_compliance_letter(
    issue_id:      Optional[int] = Query(None, description="one cycle; omit to sweep a period"),
    period_start:  Optional[str] = Query(None),
    horizon:       str           = Query("week"),
    tolerance_pct: float         = Query(_ING_TOLERANCE_DEFAULT),
    due_date:      Optional[str] = Query(None, description="deadline to correct"),
    issuer:        Optional[str] = Query(None),
    note:          Optional[str] = Query(None),
):
    """Warning notice to branches that did not order centrally, or ordered more
    than `tolerance_pct` below the recommendation.

    Compliant branches get NO page — a notice is only produced where there is
    something concrete to answer for, and every line shows the frozen
    recommendation, the recorded actual and the shortfall it is based on."""
    import html as _html_c
    esc = lambda s: _html_c.escape(str(s or ""))
    horizon = "month" if str(horizon).lower() == "month" else "week"

    with get_db() as con:
        _ing_issue_tables(con)
        if issue_id:
            heads = con.execute("SELECT * FROM ingredient_order_issue WHERE issue_id=?",
                                (issue_id,)).fetchall()
        else:
            f = ["horizon = :hz"]
            p: Dict = {"hz": horizon}
            if period_start:
                f.append("period_start = :ps")
                p["ps"] = period_start
            else:
                last = con.execute("SELECT MAX(period_start) FROM ingredient_order_issue "
                                   "WHERE horizon=:hz", p).fetchone()[0]
                if not last:
                    raise HTTPException(404, "ยังไม่มีรอบสั่งที่ออกไว้")
                f.append("period_start = :ps")
                p["ps"] = last
            heads = con.execute(f"SELECT * FROM ingredient_order_issue "
                                f"WHERE {' AND '.join(f)} ORDER BY branch", p).fetchall()
        if not heads:
            raise HTTPException(404, "ไม่พบรอบสั่งตามเงื่อนไข")
        pages = []
        for h in heads:
            rows = _ing_compliance_rows(con, h["issue_id"], tolerance_pct)
            bad = [r for r in rows if r["status"] != "ok"]
            if bad:
                pages.append((dict(h), bad, _ing_compliance_summary(
                    con, h["issue_id"], tolerance_pct)))
        if not pages:
            return Response(content=
                "<!doctype html><meta charset='utf-8'>"
                "<div style=\"font-family:Sarabun,Tahoma,sans-serif;padding:40px;"
                "text-align:center;color:#047857\"><h2>✅ ไม่มีสาขาที่ต้องออกหนังสือแจ้งเตือน</h2>"
                "<p style='color:#475569'>ทุกสาขาสั่งวัตถุดิบผ่านส่วนกลางครบตามแผน "
                f"(เกณฑ์: ต่ำกว่าที่แนะนำเกิน {tolerance_pct:g}%)</p></div>",
                media_type="text/html; charset=utf-8")

    today = datetime.now().strftime("%d/%m/%Y")
    due = due_date or ""
    issuer_line = esc(issuer or "ฝ่ายวิเคราะห์ต้นทุนและจัดซื้อ")
    hz_label = "สัปดาห์" if horizon == "week" else "เดือน"

    body = []
    for h, bad, summ in pages:
        trs = []
        for i, r in enumerate(bad, start=1):
            if r["status"] == "not_ordered":
                st = "<span class='bad'>ไม่ได้สั่งผ่านส่วนกลาง</span>"
                pct = "0%"
            else:
                st = "<span class='warn'>ต่ำกว่าแผน</span>"
                pct = f"{r['pct_of_rec']:.0f}%"
            trs.append(
                f"<tr><td class='c'>{i}</td><td class='mono'>{esc(r['ingredient_code'])}</td>"
                f"<td>{esc(r['name'])}</td>"
                f"<td class='r'>{r['rec_units']:,g} {esc(r['order_unit'])}</td>"
                f"<td class='r'>{r['act_units']:,g} {esc(r['order_unit'])}</td>"
                f"<td class='c'>{pct}</td>"
                f"<td class='r'>{esc(r['gap_disp'])}</td>"
                f"<td class='r'>{r['gap_value']:,.0f}</td><td class='c'>{st}</td></tr>")
        body.append(f"""
<section class="page">
  <div class="head">
    <div class="brand">โอชิเน (Oshinei)</div>
    <div class="doc">หนังสือแจ้งเตือน<br><span>การสั่งวัตถุดิบไม่เป็นไปตามแผน</span></div>
  </div>
  <table class="meta"><tr>
    <td><b>เรียน</b> ผู้จัดการสาขา {esc(h['branch'])}</td>
    <td><b>วันที่</b> {today}</td></tr><tr>
    <td><b>เรื่อง</b> การสั่งวัตถุดิบผ่านส่วนกลางต่ำกว่าแผนที่แจ้งไว้</td>
    <td><b>รอบสั่ง</b> {h['period_start']} ถึง {h['period_end']} ({hz_label})</td></tr><tr>
    <td colspan="2"><b>อ้างถึง</b> ใบแจ้งปริมาณสั่งวัตถุดิบที่ออกเมื่อ {esc(str(h['issued_at'])[:16])}
        (อ้างอิงยอดขายจริง {h['basis_from']} ถึง {h['basis_to']})</td></tr>
  </table>
  <p class="lead">ตามที่{issuer_line}ได้แจ้งปริมาณวัตถุดิบที่ควรสั่งประจำ{hz_label}ให้สาขา{esc(h['branch'])}
     ทราบตามเอกสารอ้างถึงนั้น จากการตรวจสอบพบว่าสาขามีการสั่งวัตถุดิบผ่านส่วนกลาง
     <b>ไม่ครบตามแผนจำนวน {len(bad)} รายการ</b>
     (ไม่ได้สั่งเลย {summ['not_ordered']} รายการ · สั่งต่ำกว่าที่แนะนำเกิน {tolerance_pct:g}%
     จำนวน {summ['under']} รายการ) คิดเป็นมูลค่าส่วนที่ขาด {summ['gap_value']:,.0f} บาท
     รายละเอียดดังนี้</p>
  {('<p class="lead">' + esc(note) + '</p>') if note else ''}
  <table class="grid">
    <thead><tr><th>ลำดับ</th><th>รหัส</th><th>วัตถุดิบ</th><th>แผนแจ้งให้สั่ง</th>
      <th>สั่งจริง</th><th>% ของแผน</th><th>ส่วนที่ขาด</th><th>มูลค่าที่ขาด (บาท)</th><th>สถานะ</th></tr></thead>
    <tbody>{''.join(trs)}</tbody>
  </table>
  <div class="action">
    <b>ขอให้สาขาดำเนินการดังนี้</b>
    <ol>
      <li>ชี้แจงเหตุผลของการไม่สั่ง หรือสั่งต่ำกว่าแผนในแต่ละรายการข้างต้น</li>
      <li>หากเป็นการจัดซื้อจากผู้ขายภายนอก ให้แจ้งชื่อผู้ขาย ปริมาณ และราคาที่ซื้อ
          พร้อมแนบหลักฐานการซื้อเพื่อประกอบการพิจารณา</li>
      <li>ปรับการสั่งซื้อรอบถัดไปให้เป็นไปตามแผนที่ส่วนกลางแจ้ง
          หรือประสานเพื่อขอปรับแผนหากปริมาณที่แจ้งไม่สอดคล้องกับการใช้งานจริง</li>
    </ol>
    <p class="due"><b>กำหนดชี้แจง/แก้ไขภายในวันที่ {esc(due) if due else '......../......../........'}</b>
       — หากพ้นกำหนดข้างต้นแล้วยังไม่มีการแก้ไขหรือชี้แจง
       บริษัทจะดำเนินการตามขั้นตอนถัดไป</p>
  </div>
  <div class="notes">
    ปริมาณ “แผนแจ้งให้สั่ง” ในตารางนี้เป็นตัวเลขที่บันทึกไว้ ณ วันที่ออกใบแจ้ง
    ไม่ได้คำนวณใหม่ภายหลัง · เกณฑ์การแจ้งเตือน = สั่งต่ำกว่าแผนเกิน {tolerance_pct:g}%
    · หากสาขามีวัตถุดิบคงเหลือยกมาจากรอบก่อน กรุณาระบุในคำชี้แจงเพื่อประกอบการพิจารณา
  </div>
  <div class="sign">
    <div><div class="line"></div><div class="cap">{issuer_line}</div>
      <div class="cap">ผู้ออกหนังสือ · วันที่ ......../......../........</div></div>
    <div><div class="line"></div><div class="cap">ผู้จัดการสาขา {esc(h['branch'])}</div>
      <div class="cap">ผู้รับทราบ · วันที่ ......../......../........</div></div>
  </div>
</section>""")

    html_out = f"""<!doctype html><html lang="th"><head><meta charset="utf-8">
<title>หนังสือแจ้งเตือนการสั่งวัตถุดิบ · {len(pages)} สาขา</title>
<style>
  @page {{ size: A4; margin: 14mm 12mm; }}
  * {{ box-sizing: border-box; }}
  body {{ font-family:"Sarabun","Leelawadee UI","Tahoma",sans-serif; color:#0f172a;
         margin:0; background:#f1f5f9; font-size:12px; }}
  .page {{ background:#fff; width:190mm; min-height:263mm; margin:8mm auto; padding:10mm 9mm;
           box-shadow:0 1px 6px rgba(0,0,0,.12); page-break-after:always; }}
  .page:last-child {{ page-break-after:auto; }}
  .head {{ display:flex; justify-content:space-between; align-items:flex-end;
           border-bottom:2.5px solid #b91c1c; padding-bottom:6px; margin-bottom:10px; }}
  .brand {{ font-size:20px; font-weight:700; color:#0f766e; letter-spacing:.5px; }}
  .doc {{ text-align:right; font-size:14px; font-weight:700; line-height:1.3; color:#b91c1c; }}
  .doc span {{ font-size:10px; font-weight:400; color:#64748b; }}
  table.meta {{ width:100%; margin-bottom:10px; font-size:11.5px; }}
  table.meta td {{ padding:2px 0; vertical-align:top; }}
  table.meta td:last-child {{ text-align:right; color:#334155; }}
  table.meta td[colspan] {{ text-align:left; }}
  .lead {{ text-indent:2em; line-height:1.75; margin:0 0 10px; }}
  table.grid {{ width:100%; border-collapse:collapse; font-size:10.5px; }}
  table.grid th {{ background:#b91c1c; color:#fff; font-weight:600; padding:5px 4px;
                   border:1px solid #b91c1c; }}
  table.grid td {{ border:1px solid #cbd5e1; padding:3.5px 5px; }}
  table.grid tbody tr:nth-child(even) {{ background:#fef2f2; }}
  .r {{ text-align:right; }} .c {{ text-align:center; }}
  .mono {{ font-family:"Consolas",monospace; font-size:10px; color:#475569; }}
  .bad {{ color:#b91c1c; font-weight:700; }}
  .warn {{ color:#b45309; font-weight:700; }}
  .action {{ margin-top:12px; font-size:11px; background:#fff7ed; border-left:3px solid #ea580c;
             padding:8px 11px; line-height:1.65; }}
  .action ol {{ margin:4px 0 6px 16px; padding:0; }}
  .due {{ margin:6px 0 0; color:#b91c1c; }}
  .notes {{ margin-top:9px; font-size:10px; color:#475569; background:#f1f5f9;
            border-left:3px solid #94a3b8; padding:6px 9px; line-height:1.6; }}
  .sign {{ display:flex; justify-content:space-between; gap:24px; margin-top:22px; }}
  .sign > div {{ width:46%; text-align:center; }}
  .line {{ border-bottom:1px dotted #475569; height:34px; }}
  .cap {{ font-size:10.5px; color:#475569; margin-top:3px; }}
  .toolbar {{ text-align:center; padding:10px; }}
  .toolbar button {{ background:#b91c1c; color:#fff; border:0; border-radius:6px;
                     padding:8px 18px; font-size:13px; cursor:pointer; font-family:inherit; }}
  @media print {{ body {{ background:#fff; }} .toolbar {{ display:none; }}
                  .page {{ margin:0; box-shadow:none; width:auto; min-height:0; padding:0; }}
                  table.grid th {{ -webkit-print-color-adjust:exact; print-color-adjust:exact; }} }}
</style></head><body>
<div class="toolbar"><button onclick="window.print()">🖨️ พิมพ์ / บันทึกเป็น PDF</button>
  &nbsp;<span style="font-size:11px;color:#64748b">{len(pages)} สาขาที่ต้องแจ้งเตือน · 1 หน้าต่อสาขา</span></div>
{''.join(body)}
</body></html>"""
    return Response(content=html_out, media_type="text/html; charset=utf-8")


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

_ING_CAL_DDL = """
CREATE TABLE IF NOT EXISTS demand_calendar (
  event_id  INTEGER PRIMARY KEY,
  name      TEXT NOT NULL,
  date_from TEXT NOT NULL,
  date_to   TEXT NOT NULL,
  branch    TEXT NOT NULL DEFAULT '',
  factor    REAL,
  note      TEXT,
  updated_at TEXT DEFAULT CURRENT_TIMESTAMP
)"""

_SEASON_BASELINE_DAYS = 56      # "normal" stretch used as the denominator
_SEASON_GAP_DAYS = 14           # ends this far before the festival starts
_SEASON_MIN_COVER = 0.6         # need this share of days present in both windows
_SEASON_CLAMP = (0.5, 3.0)      # refuse absurd factors from thin data


def _ing_daily_pax(con, branch: Optional[str], start: str, end: str):
    """(avg daily covers, days present) for a branch (or the chain) in a window."""
    f = ["date(t.sold_at) BETWEEN :s AND :e", "b.name != 'ALL'"]
    p: Dict = {"s": start, "e": end}
    if branch:
        f.append("b.name = :branch")
        p["branch"] = branch
    row = con.execute(f"""
        SELECT AVG(pax) AS avg_pax, COUNT(*) AS nd FROM (
            SELECT date(t.sold_at) AS d, SUM(t.customer_count) AS pax
              FROM sales_transaction t JOIN branches b ON b.branch_id = t.branch_id
             WHERE {' AND '.join(f)} GROUP BY date(t.sold_at))
    """, p).fetchone()
    return float(row["avg_pax"] or 0), int(row["nd"] or 0)


def _ing_season_factor(con, branch: Optional[str], start: str, end: str):
    """How much busier the same calendar dates were last year vs normal.

    Returns a dict that always carries `factor` (1.0 when it cannot be measured)
    plus the evidence, so the UI and the letter can show WHY it was scaled."""
    ly_start = con.execute("SELECT date(:d,'-1 year')", {"d": start}).fetchone()[0]
    ly_end = con.execute("SELECT date(:d,'-1 year')", {"d": end}).fetchone()[0]
    base_end = con.execute("SELECT date(:d, :g)", {"d": ly_start,
                                                   "g": f"-{_SEASON_GAP_DAYS} day"}).fetchone()[0]
    base_start = con.execute("SELECT date(:d, :g)", {"d": base_end,
                                                     "g": f"-{_SEASON_BASELINE_DAYS - 1} day"}).fetchone()[0]
    want_t = (datetime.strptime(ly_end, "%Y-%m-%d") - datetime.strptime(ly_start, "%Y-%m-%d")).days + 1
    out = {"factor": 1.0, "available": False, "scope": branch or "chain",
           "ly_from": ly_start, "ly_to": ly_end,
           "baseline_from": base_start, "baseline_to": base_end}

    def measure(scope):
        t_pax, t_nd = _ing_daily_pax(con, scope, ly_start, ly_end)
        b_pax, b_nd = _ing_daily_pax(con, scope, base_start, base_end)
        if t_nd < max(1, want_t * _SEASON_MIN_COVER) or b_nd < _SEASON_BASELINE_DAYS * _SEASON_MIN_COVER:
            return None
        if b_pax <= 0 or t_pax <= 0:
            return None
        return t_pax, b_pax

    m = measure(branch) if branch else None
    if m:
        out["scope"] = branch
    else:
        # a branch that did not exist last year falls back to the chain shape
        m = measure(None)
        out["scope"] = "chain" if m else (branch or "chain")
    if not m:
        return out
    t_pax, b_pax = m
    raw = t_pax / b_pax
    out.update({"factor": round(max(_SEASON_CLAMP[0], min(raw, _SEASON_CLAMP[1])), 3),
                "raw_factor": round(raw, 3), "available": True,
                "ly_pax_per_day": round(t_pax, 1), "baseline_pax_per_day": round(b_pax, 1),
                "clamped": not (_SEASON_CLAMP[0] <= raw <= _SEASON_CLAMP[1])})
    return out


def _ing_calendar_hit(con, branch: Optional[str], start: str, end: str):
    """A named event overlapping the cycle — branch-specific beats chain-wide."""
    con.execute(_ING_CAL_DDL)
    rows = con.execute("""
        SELECT * FROM demand_calendar
         WHERE date_from <= :e AND date_to >= :s AND (branch = '' OR branch = :b)
         ORDER BY (branch = '') ASC, (date_to - date_from) ASC""",
        {"s": start, "e": end, "b": branch or ""}).fetchall()
    return dict(rows[0]) if rows else None


def _ing_season_resolve(con, branch: Optional[str], start: str, end: str, mode: str):
    """mode: off | auto. Returns the factor actually applied plus its reason."""
    if mode != "auto" or not start or not end:
        return {"factor": 1.0, "mode": "off", "reason": ""}
    ev = _ing_calendar_hit(con, branch, start, end)
    if ev and ev.get("factor"):
        return {"factor": float(ev["factor"]), "mode": "calendar-manual",
                "event": ev["name"], "reason": f"ตั้งค่าไว้ในปฏิทิน ({ev['name']})",
                "window": [ev["date_from"], ev["date_to"]]}
    win = ([ev["date_from"], ev["date_to"]] if ev else [start, end])
    s = _ing_season_factor(con, branch, win[0], win[1])
    if not s["available"]:
        return {"factor": 1.0, "mode": "auto-nodata", "event": (ev or {}).get("name", ""),
                "reason": "ไม่มีข้อมูลปีก่อนมากพอ จึงไม่ปรับ", "detail": s}
    label = (ev["name"] + " · ") if ev else ""
    return {"factor": s["factor"], "mode": "auto", "event": (ev or {}).get("name", ""),
            "reason": (f"{label}อ้างอิงยอดลูกค้าช่วงเดียวกันปีก่อน "
                       f"({s['ly_from']}–{s['ly_to']} เฉลี่ย {s['ly_pax_per_day']:,.0f} คน/วัน "
                       f"เทียบช่วงปกติ {s['baseline_pax_per_day']:,.0f} คน/วัน)"),
            "window": win, "detail": s}


@app.get("/api/ingredients/season-factor")
def ingredients_season_factor(start: str = Query(...), end: str = Query(...),
                              branch: Optional[str] = Query(None),
                              mode: str = Query("auto")):
    """Preview the festival/promotion factor for a cycle before issuing it."""
    with get_db() as con:
        return _ing_season_resolve(con, branch, start, end,
                                   "auto" if mode == "auto" else "off")


class CalendarEvent(BaseModel):
    event_id: Optional[int] = None
    name: str
    date_from: str
    date_to: str
    branch: Optional[str] = ""
    factor: Optional[float] = None      # None = derive from last year
    note: Optional[str] = None


@app.get("/api/ingredients/demand-calendar")
def ingredients_demand_calendar():
    with get_db() as con:
        con.execute(_ING_CAL_DDL)
        rows = [dict(r) for r in con.execute(
            "SELECT * FROM demand_calendar ORDER BY date_from DESC")]
    return {"rows": rows}


@app.post("/api/ingredients/demand-calendar", dependencies=[Depends(require_write_token)])
def ingredients_demand_calendar_save(events: List[CalendarEvent]):
    with get_db() as con:
        con.execute(_ING_CAL_DDL)
        for e in events:
            if e.date_from > e.date_to:
                raise HTTPException(400, f"ช่วงวันที่ไม่ถูกต้อง: {e.name}")
            if e.event_id:
                con.execute("""UPDATE demand_calendar SET name=?, date_from=?, date_to=?,
                                 branch=?, factor=?, note=?, updated_at=CURRENT_TIMESTAMP
                                WHERE event_id=?""",
                            (e.name.strip(), e.date_from, e.date_to, (e.branch or "").strip(),
                             e.factor, e.note, e.event_id))
            else:
                con.execute("""INSERT INTO demand_calendar
                                 (name, date_from, date_to, branch, factor, note)
                               VALUES (?,?,?,?,?,?)""",
                            (e.name.strip(), e.date_from, e.date_to, (e.branch or "").strip(),
                             e.factor, e.note))
        con.commit()
    invalidate_cache()
    return {"ok": True, "saved": len(events)}


@app.delete("/api/ingredients/demand-calendar/{event_id}",
            dependencies=[Depends(require_write_token)])
def ingredients_demand_calendar_delete(event_id: int):
    with get_db() as con:
        con.execute(_ING_CAL_DDL)
        con.execute("DELETE FROM demand_calendar WHERE event_id=?", (event_id,))
        con.commit()
    invalidate_cache()
    return {"ok": True}


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

def _pctl(sorted_vals, q: float):
    """Linear-interpolated percentile on an already-sorted list."""
    if not sorted_vals:
        return None
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    pos = (len(sorted_vals) - 1) * max(0.0, min(q, 1.0))
    lo = int(pos)
    hi = min(lo + 1, len(sorted_vals) - 1)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (pos - lo)


def _ing_consumed(con, branch: str, start: str, end: str):
    """Actual consumption per ingredient for one branch over a closed window."""
    rows = con.execute(f"""
        SELECT COALESCE(NULLIF(bi.ingredient_code,''), '__' || bi.ingredient_name) AS code,
               bi.unit AS unit,
               SUM(d.qty * bi.qty_per_serving) AS qty
          FROM v_sales_sku_daily d
          JOIN branches b         ON b.branch_id = d.branch_id
          JOIN {_ING_BOM_MAP} m   ON m.sale_sku  = d.sku_id
          JOIN bom_ingredients bi ON bi.sku_id   = m.bom_sku
         WHERE b.name = :branch AND d.day BETWEEN :start AND :end
         GROUP BY code, bi.unit
    """, {"branch": branch, "start": start, "end": end}).fetchall()
    return {(r["code"], r["unit"] or ""): float(r["qty"] or 0) for r in rows}


@app.get("/api/ingredients/order-accuracy")
@ttl_cache
def ingredients_order_accuracy(
    branch:        Optional[str] = Query(None),
    service_level: float         = Query(80.0, description="buffer should cover this % of past cycles"),
    min_cycles:    int           = Query(2, description="ingredients seen in fewer cycles are listed but not tuned"),
    limit:         int           = Query(200),
):
    """How well the plan predicted real consumption, and what buffer to use next.

    Only CLOSED cycles count — a cycle whose period_end is past the last day of
    sales data would be judged on days that have not happened yet."""
    sl = max(50.0, min(float(service_level or 80), 99.0)) / 100.0
    with get_db() as con:
        _ing_issue_tables(con)
        maxd = con.execute("SELECT MAX(day) FROM sales_sku_daily").fetchone()[0]
        if not maxd:
            return {"available": False, "reason": "no daily sales data"}
        f = ["i.period_end <= :maxd"]
        p: Dict = {"maxd": maxd}
        if branch:
            f.append("i.branch = :branch")
            p["branch"] = branch
        issues = con.execute(
            f"SELECT * FROM ingredient_order_issue i WHERE {' AND '.join(f)} "
            f"ORDER BY i.period_start DESC LIMIT 60", p).fetchall()
        if not issues:
            return {"available": False,
                    "reason": "ยังไม่มีรอบสั่งที่ปิดแล้ว (period_end ต้องผ่านวันล่าสุดของข้อมูลขาย)",
                    "data_through": maxd}

        per_ing: Dict[tuple, dict] = {}
        per_branch: Dict[str, dict] = {}
        n_lines = 0
        for i in issues:
            consumed = _ing_consumed(con, i["branch"], i["period_start"], i["period_end"])
            buf = float(i["buffer_pct"] or 0)
            for l in con.execute(
                    "SELECT * FROM ingredient_order_issue_line WHERE issue_id=?",
                    (i["issue_id"],)):
                rec = float(l["rec_base"] or 0)
                if rec <= 0:
                    continue
                used = consumed.get((l["ingredient_code"], l["base_unit"] or ""), 0.0)
                raw = rec / (1.0 + buf / 100.0)          # forecast before the buffer
                if raw <= 0:
                    continue
                ratio = used / raw                        # >1 = we under-forecast
                n_lines += 1
                k = (l["ingredient_code"], l["base_unit"] or "")
                e = per_ing.setdefault(k, {
                    "code": l["ingredient_code"], "name": l["name"] or "",
                    "unit": l["base_unit"] or "", "n": 0,
                    "rec": 0.0, "used": 0.0, "ratios": []})
                e["n"] += 1
                e["rec"] += rec
                e["used"] += used
                e["ratios"].append(ratio)
                b = per_branch.setdefault(i["branch"], {"branch": i["branch"], "n": 0,
                                                        "short": 0, "ratios": []})
                b["n"] += 1
                b["ratios"].append(ratio)
                if used > rec:
                    b["short"] += 1

    def finish(e):
        rs = sorted(e.pop("ratios"))
        p50, p80 = _pctl(rs, 0.5), _pctl(rs, sl)
        e["ratio_median"] = round(p50, 3) if p50 is not None else None
        e["ratio_service"] = round(p80, 3) if p80 is not None else None
        if p80 is not None and e["n"] >= min_cycles:
            e["suggested_buffer_pct"] = round(max(0.0, (p80 - 1.0) * 100.0), 1)
        else:
            e["suggested_buffer_pct"] = None
        return e

    ings = [finish(e) for e in per_ing.values()]
    for e in ings:
        e["rec"] = round(e["rec"], 2)
        e["used"] = round(e["used"], 2)
        e["rec_disp"] = _ing_pretty(e["rec"], e["unit"])[2]
        e["used_disp"] = _ing_pretty(e["used"], e["unit"])[2]
        e["verdict"] = ("under" if (e["ratio_median"] or 1) > 1.05
                        else "over" if (e["ratio_median"] or 1) < 0.8 else "ok")
    ings.sort(key=lambda e: -(e["ratio_median"] or 0))
    brs = [finish(b) for b in per_branch.values()]
    for b in brs:
        b["short_pct"] = round(100.0 * b["short"] / b["n"], 1) if b["n"] else 0

    flat = sorted([x for e in ings for x in [e["ratio_median"]] if x is not None])
    headline = {
        "cycles_evaluated": len(issues),
        "lines_evaluated": n_lines,
        "service_level_pct": round(sl * 100, 1),
        "median_ratio": round(_pctl(flat, 0.5), 3) if flat else None,
        "suggested_buffer_pct": (round(max(0.0, (_pctl(flat, sl) - 1.0) * 100.0), 1)
                                 if flat else None),
        "buffer_used_median": round(sum(float(i["buffer_pct"] or 0) for i in issues) / len(issues), 1),
        "data_through": maxd,
    }
    return {"available": True, "headline": headline,
            "by_ingredient": ings[:max(1, min(int(limit or 200), 1000))],
            "by_branch": sorted(brs, key=lambda b: -b["short_pct"])}
