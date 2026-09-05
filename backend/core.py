"""core — the infrastructure and the shared SQL helpers every router uses.

Until 2026-09 all of this sat in main.py, and the routers reached it with
    M = sys.modules["main"]; globals().update({n: getattr(M, n) for n in _SHARED})
— a circular dependency held together by a dictionary. It worked, but no
static tool could see the 55 names it injected: pyflakes reported 1,753
undefined names across the routers, and a mistyped helper would have hidden
among them until a customer opened the endpoint. This module is what main.py
called "P1 structure refactor, step 1 … hoist the infra into a core.py and flip
the import direction", finished.

What lives here, and why together:
  • the response cache (ttl_cache / invalidate_cache) — invalidation rebuilds
    sku_effective_cost, so it needs the database layer
  • the database layer (get_db, the RW lock, the connection registry) and the
    hot-swap that replaces the live file under it — the swap bumps _db_gen and
    closes registered connections, so it must own that state, not borrow it
  • the write-token dependency
  • the SQL helper family — period/branch filters, the COGS expressions, the
    per-SKU effective-cost build, the ingredient-family and benchmark tables

Nothing here imports main.py. main.py imports this and re-exports every name,
so `main.get_db`, `main.DB_PATH`, `main._tls` … keep working for the code and
tests that spell them that way. The one thing a caller may not do any more is
ASSIGN through main — `main.DB_PATH = x` rebinds main's copy, not the one
get_db() reads. Assign core.DB_PATH.

Imported as `core` in both layouts (main.py puts backend/ on sys.path first).
"""

from contextlib import closing
from contextlib import contextmanager
from fastapi import HTTPException
from fastapi import Header
from fastapi import Query
from fastapi.responses import Response as _Response
from pathlib import Path
from pydantic import BaseModel
# core.py is imported as `core` by main.py (which puts backend/ on sys.path first)
# but also directly — `import backend.core` from the recon app and standalone
# scripts. Its sibling imports (rules, auth, month_summary) resolve by bare name,
# so it must be able to put backend/ on sys.path itself.
import sys as _sys_path_fix
from pathlib import Path as _P
if str(_P(__file__).parent.resolve()) not in _sys_path_fix.path:
    _sys_path_fix.path.insert(0, str(_P(__file__).parent.resolve()))

from rules import EASY_TIER_CODES_SQL as _EASY_TIER_CODES_SQL
from rules import VAT_DIVISOR
from rules import cogs_expr as _rules_cogs_expr
from rules import raw_unit_cost_sql as _raw_unit_cost_sql
from rules import tier_code_sql as _tier_code_sql
from typing import Optional
import functools
import json as _json
import os as _os_db
import sqlite3
import threading
import time as _time


# ---------------------------------------------------------------------------
# Response cache  (TTL-based, keyed on function name + query-param values)
# ---------------------------------------------------------------------------
_resp_cache: dict = {}


_CACHE_TTL  = 300   # 5 minutes — safe for an analytics dashboard


_cache_lock = threading.Lock()


def _cache_key(fn_name: str, kwargs: dict) -> str:
    return fn_name + "|" + "|".join(f"{k}={v}" for k, v in sorted(kwargs.items()))


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


DB_PATH = Path(_os_db.environ.get("OSHINEI_DB", str(HERE / "oshinei.db")))


# Tables that live in the shared oshinei.db but are NOT produced by the MSSQL
# sync / data refresh. A wholesale swap would wipe them, so the LIVE copy wins:
#   recon_*            staff reconciliation work (uploads, payment-code maps,
#                      bank/acquirer registries, sessions, discrepancies)
#   app_user           login accounts (passwords, roles, deactivations); without
#                      it every live-side user edit is lost on each refresh and
#                      the seeded defaults silently return (backend/auth.py)
#   customer_feedback  reviews uploaded on the live site or fetched from Google /
#                      Facebook (2026-09-05: the tab shipped a day without this
#                      line — the next 10:00 push would have wiped every review)
#   feedback_*         its sync log and source tables
#   checklist_*        go-live checklist ticks and sign-offs (routers/checklist.py)
# app_settings + branch_targets: edited on the running site (role permissions, discount ceiling,
# fixed/labor costs, targets) — the daily push used to overwrite them with the local copy (found 2026-09-05)
_PRESERVED_EXACT = ("app_user", "customer_feedback", "branch_targets", "app_settings")
_PRESERVED_PREFIXES = ("recon", "feedback", "checklist")        # + "_" (escaped in the LIKE)


def _preserved_pred(col: str) -> str:
    """SQL predicate on `col` (name / tbl_name of sqlite_master) selecting the
    preserved tables — one definition for the carry-forward and its verify."""
    parts = [f"{col} LIKE '{p}\\_%' ESCAPE '\\'" for p in _PRESERVED_PREFIXES]
    parts += [f"{col}='{n}'" for n in _PRESERVED_EXACT]
    return "(" + " OR ".join(parts) + ")"


def _carry_forward_preserved(live_path: str, inc_path: str) -> None:
    """Copy locally-owned tables (see _PRESERVED_EXACT / _PRESERVED_PREFIXES)
    from the live DB into a staged refresh before it replaces the live DB.
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
            f"  (type='table' AND {_preserved_pred('name')}) OR "
            f"  (type='index' AND {_preserved_pred('tbl_name')}))"
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
    (_PRESERVED_EXACT / _PRESERVED_PREFIXES). Used to verify a staged swap
    won't drop any of them."""
    counts: dict = {}
    if not _os_db.path.exists(db_path):
        return counts
    c = sqlite3.connect(db_path)
    try:
        names = [r[0] for r in c.execute(
            f"SELECT name FROM sqlite_master WHERE type='table' AND {_preserved_pred('name')}")]
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
    f" AND NOT {_tier_code_sql('sk.sku_code')}"
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


def _group_filter(filters: list, params: dict, group: Optional[str]):
    """Adds a branch-type filter (b.branch_type = :group). Caller must JOIN branches as b."""
    if group:
        filters.append("COALESCE(b.branch_type,'master') = :branch_group")
        params["branch_group"] = group


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
    return _rules_cogs_expr(ssm, sk)   # the text lives in rules.py; see its docstring


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
            pass                      # no table yet -> fall through and build it
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
               CASE WHEN {_tier_code_sql('s.sku_code')} THEN 0
                    ELSE {_raw_unit_cost_sql('s')} END AS rc
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
