"""
sync_mssql.py — pull live sales from the Easy POS MS SQL Server into the app's
SQLite, preserving the BoM/cost/settings tables (Option A: near-real-time sync).

Design (decided 2026-06-04):
  * Source  : MS SQL Server  HQ_Oshinei  (Sale, SaleItem, Branch, Items, ItemCategory)
  * Target  : backend/oshinei.db  — only the SALES tables are replaced:
                sales_transaction, sales_sku_monthly
              Everything else (bom_ingredients, sku_cost, app_settings,
              branch_targets, closed_hours, cost_*, skus, categories, branches…)
              is COPIED from the current DB and kept — COGS stays on BoM.
  * Mapping  : reuse the existing skus/branches by NATURAL KEY
                - sku   : MSSQL Items.ItemCode  == sqlite skus.sku_code
                - branch: MSSQL Branch.BranchName --BRANCH_ALIAS--> sqlite branches.name
              → zero convention drift (descriptions, (A)/(B) prefixes,
                business_type all come from the existing skus rows).
  * Output   : writes backend/oshinei.db.incoming then RECONCILES against the
              current DB. It does NOT swap — the app swaps `.incoming` at startup
              (see _maybe_swap_incoming_db in main.py) / Refresh Online Data.bat.

Creds come from backend/.env (gitignored). Run:
    python sync_mssql.py                # full sync → .incoming + reconcile
    python sync_mssql.py --since 2026-05-01
Aggregation is done server-side (GROUP BY) so we never pull the 21M SaleItem rows.
"""
import os, sys, shutil, sqlite3, argparse, time
from pathlib import Path

HERE = Path(__file__).resolve().parent
DB        = HERE / "oshinei.db"
INCOMING  = HERE / "oshinei.db.incoming"

# ── load .env (no external dep) ──────────────────────────────────────────────
def load_env():
    f = HERE / ".env"
    if f.exists():
        for line in f.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())
load_env()

from ingest_may26_update import BRANCH_ALIAS   # MSSQL/full name → short DB name
import month_summary                            # sales_month_summary roll-up (backend/month_summary.py)

SALE_FILTER = "s.IsFinalize=1 AND s.Active=1 AND ISNULL(s.IsReturn,0)=0"

# ── per-LINE qty sanity guard ────────────────────────────────────────────────
# Easy POS accepts fat-fingered quantities and never voids them (real incidents,
# both at ระยอง: 2026-08-03 SaleId 40609 B0032 qty=1,000,000,000 → ฿4.93 BILLION
# phantom COGS on every dashboard/report; 2026-06-05 SaleId 35773 SV0003
# qty=10,000). Every run REBUILDS the qty tables from MSSQL, so a one-off SQLite
# repair is undone the next night — the guard must live in the pull itself.
# No genuine bill line reaches 1,000 units of one menu item. Only the qty that
# feeds qty×cost COGS and menu-mix counts is zeroed; ExtendedPrice (money) is
# never touched, so revenue and reconcile totals are unaffected. Zeroed lines
# are printed by the sync and logged to sync_qty_guard_log (see [6/6] section).
MAX_SANE_LINE_QTY = 1000
QTY_SANE = (f"(CASE WHEN ABS(CAST(si.Qty AS float)) > {MAX_SANE_LINE_QTY} "
            f"THEN 0 ELSE CAST(si.Qty AS float) END)")

# ── Buffet/tier PACKAGE codes: their cost must never be booked ───────────────
# One definition, shared with the app: rules.py has no FastAPI import, so this
# standalone script can use it directly. (Until 2026-09 the tuple was copied
# here and a test stood guard against drift.) sku_cost holds the package PRICE
# for these codes (BUFFET = ฿679.08), not a cost — the real cost is the itemised
# dishes on the same bill, so booking the package too double-counts.
from rules import raw_unit_cost_sql, tier_code_sql  # noqa: E402
from rules import is_tier_code  # noqa: E402,F401  the one package predicate, shared with the app


def refresh_sku_effective_cost(sq):
    """(Re)build sku_effective_cost in the staged DB — MIRROR of
    main._build_sku_effective_cost: same COALESCE chain AND the same tier-code
    zeroing. The sync's INSERT used to lack the tier CASE, so the table
    flip-flopped by last writer (BUFFET recipe_cost=679.08 after a nightly sync,
    0 after an app restart — observed live 2026-08-10). test_invariants.py
    asserts sync output == main output row-for-row."""
    sq.executescript("CREATE TABLE IF NOT EXISTS sku_effective_cost("
                     "sku_id INTEGER PRIMARY KEY, recipe_cost REAL NOT NULL DEFAULT 0);"
                     "DELETE FROM sku_effective_cost;")
    sq.execute(f"""INSERT INTO sku_effective_cost(sku_id, recipe_cost)
                  SELECT s.sku_id, CASE WHEN {tier_code_sql('s.sku_code')} THEN 0
                                        ELSE {raw_unit_cost_sql('s')} END
                  FROM skus s""")

def _norm_paycode(c):
    """Collapse payment-code case-dups (POS has both 'CASH' and 'Cash') to one
    canonical label for the per-bill sale_payment table. Minimal — readable codes
    (Credit, QR code TTB, Bank Transfer, …) are left untouched."""
    c = (c or "").strip()
    return "Cash" if c.upper() == "CASH" else c

def connect_mssql():
    import pymssql
    # Fail loud + clear if creds are missing, instead of a cryptic KeyError deep
    # in the sync (e.g. when backend/.env is absent on a fresh checkout).
    required = ("OSH_SVR", "OSH_PORT", "OSH_USER", "OSH_PWD", "OSH_DB")
    missing = [k for k in required if not os.environ.get(k)]
    if missing:
        sys.exit(f"MSSQL credentials missing: {', '.join(missing)}. "
                 f"Set them in backend/.env (see .env.example) or the environment.")
    # NOTE: default charset (UTF-8) — required so nvarchar Thai (BranchName,
    # CategoryName) reads correctly. charset='CP874' empties nvarchar columns.
    return pymssql.connect(
        server=os.environ["OSH_SVR"], port=os.environ["OSH_PORT"],
        user=os.environ["OSH_USER"], password=os.environ["OSH_PWD"],
        database=os.environ["OSH_DB"], login_timeout=15, timeout=600)


class ResilientCursor:
    """A pymssql cursor that survives dropped connections.

    WHY: the sync runs many heavy server-side aggregates over ONE long-lived
    connection to the remote HQ MSSQL (over the internet). Any network blip, VPN
    hiccup, or server-side connection reset surfaces as 'DBPROCESS is dead or not
    enabled' (DB-Lib error 20047) — and with a plain cursor that aborts the whole
    multi-minute sync at whatever query happened to be running. That's why the
    sync 'fails often': it has zero tolerance for a single transient drop.

    This wrapper transparently re-opens the connection and retries the failed
    query a few times, so a transient drop no longer kills the run. It caches the
    result set on execute(), so every existing `mc.execute(...); mc.fetchall()` /
    `mc.fetchone()` call site keeps working unchanged."""

    _TRANSIENT = ("20047", "20003", "20006", "20009", "DBPROCESS is dead",
                  "is dead or not enabled", "unable to connect", "Communication link",
                  "timed out", "Adaptive Server", "Net-Lib", "connection")

    # Seconds to wait before each reconnect attempt. Patient on purpose: a real HQ
    # outage (the 20047-drop seen in the field is followed by 20009 'unable to
    # connect' for the whole window — i.e. the network/VPN/server is genuinely down
    # for 10-30s, not a 1s blip). This schedule keeps trying for ~4 min total so the
    # sync rides out a VPN reconnect / router reboot / brief SQL restart instead of
    # dying. If the source is down LONGER than this, no client retry can help — that
    # is an infrastructure problem (VPN/internet stability to HQ, or HQ SQL maintenance
    # landing in the sync window).
    _BACKOFFS = (5, 10, 20, 40, 60, 60, 60)

    def __init__(self, connect_fn):
        self._connect = connect_fn
        self._conn = None
        self._cur = None
        self._rows = []
        # DO NOT connect eagerly here. The HQ link over the public internet
        # FLAPS (a probe connects in 0.4s, then the next connect 2s later times
        # out with 10060 → 20009 'Adaptive Server unavailable'). An eager
        # connect in __init__ has NO retry, so a single bad instant kills the
        # whole sync before it starts (field failure 2026-06-15). Connect lazily
        # on the first execute(), which opens through the backoff-retry loop and
        # rides out the flap.

    def _open(self):
        self._conn = self._connect()
        self._cur = self._conn.cursor(as_dict=True)

    def _drop(self):
        try:
            if self._conn:
                self._conn.close()
        except Exception:
            pass
        self._conn = None
        self._cur = None

    @classmethod
    def _is_transient(cls, e):
        try:
            import pymssql
            if isinstance(e, (pymssql.OperationalError, pymssql.InterfaceError)):
                return True
        except Exception:
            pass
        msg = str(e).lower()
        return any(tok.lower() in msg for tok in cls._TRANSIENT)

    def execute(self, sql, params=None):
        import time
        attempt = 0
        while True:
            try:
                # Always ensure a live connection BEFORE running — a dropped/failed
                # reconnect leaves _conn=None and is rebuilt here, so we never burn a
                # retry on a known-dead cursor (the bug in the first version).
                if self._conn is None or self._cur is None:
                    self._open()
                if params is None:
                    self._cur.execute(sql)
                else:
                    self._cur.execute(sql, params)
                self._rows = self._cur.fetchall()
                return
            except Exception as e:
                # _open() failing (server still unreachable) is itself transient → keep
                # backing off through the whole schedule before giving up.
                if attempt >= len(self._BACKOFFS) or not self._is_transient(e):
                    raise
                wait = self._BACKOFFS[attempt]
                attempt += 1
                print(f"        ⚠ MSSQL connection lost ({str(e)[:70]}) — "
                      f"reconnecting & retrying in {wait}s "
                      f"[{attempt}/{len(self._BACKOFFS)}]", flush=True)
                self._drop()          # force a fresh reconnect on the next loop
                time.sleep(wait)

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def close(self):
        try:
            self._conn.close()
        except Exception:
            pass

def short_name(mssql_name: str) -> str:
    n = (mssql_name or "").strip()
    return BRANCH_ALIAS.get(n, n)

# ── drink / other category classification (food = everything else) ───────────
def bucket_for_cat(name: str) -> str:
    u = (name or "").upper()
    # "BAVERAGE" = real-world typo in the new-branch category names ((NEW)Oshinei-AL-BAVERAGE, Hat Yai 2026-07)
    if any(k in u for k in ("SOFT DRINK", "DRINK", "ALCOHOL", "BEVERAGE", "BAVERAGE", "เครื่องดื่ม")):
        return "drink"
    if any(k in u for k in ("BUFFET", "แพ็คเกจ", "PACKAGE", "SERVICE", "DELIVERY",
                            "SPECIAL", "PROMOTION", "PAYMENT", "MEMBER", "GIFT",
                            "THIRDPARTY", "TEMP", "เงินโอน")):
        return "other"
    return "food"

def apply_local_swap():
    """Swap the reconciled .incoming into the live local oshinei.db.

    Mirrors _maybe_swap_incoming_db() in main.py: drop stale WAL/SHM, then
    atomically replace. If the running app still holds oshinei.db open (Windows
    file lock), the replace fails — we leave .incoming in place so the app's
    own startup swap picks it up on the next restart. Returns True if swapped.
    """
    if not INCOMING.exists():
        print("      [apply-local] no .incoming to apply"); return False
    for suf in ("-wal", "-shm"):
        try: os.remove(str(DB) + suf)
        except OSError: pass
    try:
        os.replace(str(INCOMING), str(DB))   # atomic on same filesystem
        print(f"      [apply-local] ✓ swapped → {DB.name} (local app now serves fresh data after reconnect)")
        return True
    except OSError as e:
        print(f"      [apply-local] ⚠ could not swap now ({e}); .incoming kept — "
              "restart the local app to apply (startup swap will pick it up).")
        return False


# ── staged-file preparation (step [1/6]) ─────────────────────────────────────
# The sync builds into oshinei.db.incoming — but on Windows that name can be
# LOCKED: the locally-running dashboard (backend/main.py) re-opens .incoming
# every ~60 s to validate/hot-swap it, and a handle it doesn't close stays held
# for as long as the app window stays open. Seen 2026-08-08..10: every nightly
# sync died at INCOMING.unlink() with PermissionError WinError 32 for 3 straight
# days, until the app was closed. Strategy: retry the claim briefly (a healthy
# poller pass holds the file only a few seconds), then fall back to building
# under a private name and publishing it over .incoming once done — and if even
# that stays blocked, exit with a message that names the fix (close the local
# dashboard) instead of a raw traceback.

STAGE_FALLBACK = HERE / "oshinei.db.incoming.new"

_CLAIM_WAITS   = (1, 2, 4, 8)          # ~15 s — rides out one poller validation pass
_PUBLISH_WAITS = (2, 5, 10, 15, 30)    # ~1 min — covers a full 60 s poller cycle

_LOCK_FIX_HINT = ("close the locally-running dashboard (the backend/main.py app — "
                  "its .incoming swap poller is the usual lock holder), then re-run "
                  "the sync")


def _is_file_locked_err(e: OSError) -> bool:
    """A Windows sharing/lock violation (file open in another process)?
    os.remove/unlink of an open file raises WinError 32; os.replace onto an open
    target raises WinError 5 — both surface as PermissionError."""
    return isinstance(e, PermissionError) or getattr(e, "winerror", None) in (5, 32, 33)


def _clear_staging_sidecars(base, announce=False) -> bool:
    """Remove <base>-wal/-shm. Returns False if a sidecar exists but is locked.

    WHY sidecars must go before (re)using <base>: copy2/os.replace move ONLY the
    main file, so a -wal left behind by a run that died mid-sync gets REPLAYED
    into the fresh file — and it belongs to a DIFFERENT database image, so
    SQLite happily grafts its pages on top: duplicate sqlite_master rows, "2nd
    reference to page N", cross-linked b-trees. Seen 2026-08-07 (#72): a 502 MB
    stale WAL from a killed run turned every later sync into "malformed database
    schema (recon_attachment)". SQLite cannot detect this — a WAL carries no
    identity of the database it came from. A sidecar that exists but CANNOT be
    removed is therefore a hard stop for staging at <base>."""
    ok = True
    for suf in ("-wal", "-shm"):
        side = str(base) + suf
        try:
            os.remove(side)
            if announce:
                print(f"      removed stale {os.path.basename(side)} (orphan from an interrupted run)")
        except FileNotFoundError:
            pass
        except OSError as e:
            print(f"      ⚠ cannot remove {os.path.basename(side)}: {e}")
            ok = False
    return ok


def _claim_staging(path):
    """Try to free `path` (and its WAL/SHM sidecars) for a fresh staging build.
    Returns (ok, blocker) — blocker describes the lock when ok is False.
    Non-lock errors (bad path, disk trouble) raise: retrying can't help those."""
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    except OSError as e:
        if _is_file_locked_err(e):
            return False, e
        raise
    if not _clear_staging_sidecars(path, announce=True):
        return False, f"{path.name}-wal/-shm sidecar is locked"
    return True, None


def prepare_staging():
    """Claim INCOMING for the build, falling back to STAGE_FALLBACK when locked.
    Returns the Path the sync must build into (publish_staging() moves a
    fallback build onto INCOMING at the end)."""
    blocker = None
    for i, wait in enumerate((0,) + _CLAIM_WAITS):
        if wait:
            time.sleep(wait)
        ok, blocker = _claim_staging(INCOMING)
        if ok:
            # Sweep a fallback build a previous run left behind (its publish
            # failed, or it died before publishing) — this fresh build
            # supersedes it, and a ~500 MB stray must not rot on disk.
            if STAGE_FALLBACK.exists():
                _claim_staging(STAGE_FALLBACK)
            return INCOMING
        if i == 0:
            print(f"      ⚠ {INCOMING.name} is locked by another process ({blocker}) — "
                  f"retrying for ~{sum(_CLAIM_WAITS)}s…")
    print(f"      ⚠ {INCOMING.name} still locked — staging to {STAGE_FALLBACK.name} instead; "
          f"it will be published over {INCOMING.name} once the build is done")
    ok, blocker = _claim_staging(STAGE_FALLBACK)
    if not ok:
        sys.exit(f"[1/6] ✗ fallback staging file {STAGE_FALLBACK.name} is locked too "
                 f"({blocker}). To fix: {_LOCK_FIX_HINT}.")
    return STAGE_FALLBACK


def publish_staging(stage) -> bool:
    """Make the finished build available under the INCOMING name.

    No-op when the build already lives at INCOMING (the normal path). A
    fallback build is os.replace()d over .incoming — atomic, but on Windows the
    replace fails while ANY process still holds .incoming open, so retry across
    a full poller cycle. Orphan .incoming-wal/-shm are cleared first each try:
    publishing next to a stale WAL would graft it onto the fresh DB at first
    open (#72). Returns True when INCOMING is the fresh build; on False the
    stage file is kept (a multi-minute build is never thrown away) and an
    actionable message has been printed."""
    if stage == INCOMING:
        return True
    # Fold the stage's own -wal in first (e.g. the menu-fact subprocess died
    # mid-write): unlike an orphan .incoming-wal it belongs to THIS file, so
    # opening the DB replays it legitimately; os.replace of the bare main file
    # would silently drop those committed frames.
    if os.path.exists(str(stage) + "-wal"):
        try:
            c = sqlite3.connect(str(stage))
            c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            c.close()
        except sqlite3.Error as e:
            print(f"      ⚠ could not checkpoint {stage.name}-wal: {e}")
    blocker = None
    for wait in (0,) + _PUBLISH_WAITS:
        if wait:
            time.sleep(wait)
        if not _clear_staging_sidecars(INCOMING):
            blocker = f"{INCOMING.name}-wal/-shm sidecar is locked"
            continue
        try:
            os.replace(str(stage), str(INCOMING))
            print(f"      ✓ published {stage.name} → {INCOMING.name}")
            return True
        except OSError as e:
            if not _is_file_locked_err(e):
                raise
            blocker = e
    print(f"      ✗ could not publish {stage.name} → {INCOMING.name}: still locked "
          f"after ~{sum(_PUBLISH_WAITS)}s ({blocker}).\n"
          f"        The fully-built, reconciled DB is KEPT at {stage.name}.\n"
          f"        To fix: {_LOCK_FIX_HINT} — or apply the staged file manually:\n"
          f"          move /Y \"{stage}\" \"{INCOMING}\"")
    return False


def _month_windows(since):
    """Calendar-month [lo, hi) windows from `since` up to and including the current
    month. The first window honours the exact `since` day; the rest start on the
    1st. Each window's `hi` is the first day of the next month (exclusive)."""
    import datetime
    parts = since[:10].split("-")
    start = datetime.date(int(parts[0]), int(parts[1]), 1)
    today = datetime.date.today()
    last = datetime.date(today.year, today.month, 1)
    wins, cur = [], start
    while cur <= last:
        nxt = datetime.date(cur.year + cur.month // 12, cur.month % 12 + 1, 1)
        lo = since[:10] if cur == start else cur.isoformat()
        wins.append((lo, nxt.isoformat()))
        cur = nxt
    return wins


def _chunk(mc, sql_tmpl, windows, col="s.SaleDate"):
    """Run `sql_tmpl` once per month window and concatenate the rows. `sql_tmpl`
    must contain a single `{RANGE}` token, replaced with
    `<col> >= %s AND <col> < %s`, with the window bounds passed as params.

    WHY: the killer queries scan SaleItem/Sale over the WHOLE history (15M+ rows)
    in one multi-minute server-side aggregate. A single transient MSSQL drop
    (err 20047) mid-scan aborts the entire query, and ResilientCursor must re-run
    it from scratch — expensive and likely to hit another drop. Bounding each
    query to ONE month keeps it short (~30s), so a drop costs at most one cheap
    month-retry instead of the whole scan. Every chunked query GROUPs BY a date
    expression (or is keyed by a single-dated Sale), so the per-month result sets
    are disjoint and concatenation is identical to the full-range query."""
    pred = f"{col} >= %s AND {col} < %s"
    # A template written as a plain string may carry the doubled {{RANGE}} an
    # f-string needs: 2026-09-04 five of them had lost their f-prefix to a lint
    # pass and the sync died at [4b/6] with "Incorrect syntax near '>'" — the
    # fake-cursor tests never execute SQL. Accept both, and refuse to send a
    # brace to the server; test_sync_main.py / test_sync_staging.py assert the same.
    sql = sql_tmpl.replace("{{RANGE}}", "{RANGE}").replace("{RANGE}", pred)
    if "{" in sql or "}" in sql:
        raise ValueError(f"SQL template still carries a brace after the {{RANGE}} substitution: {sql[:200]!r}")
    out = []
    for lo, hi in windows:
        mc.execute(sql, (lo, hi))
        out.extend(mc.fetchall())
    return out


def _sync_args():
    # names this section hands on; None where a path never assigns one
    args = None
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="2025-01-01", help="sync sales on/after this date")
    ap.add_argument("--apply-local", action="store_true",
                    help="after reconcile, swap .incoming into the live local oshinei.db")
    ap.add_argument("--no-menu-fact", action="store_true",
                    help="skip the menu_fact_* refresh (🧪 วิเคราะห์วัตถุดิบ tab data)")
    ap.add_argument("--window-days", type=int, default=0, metavar="N",
                    help="INCREMENTAL: re-pull only the last N days of sales instead of "
                         "everything since --since. The cutoff is snapped BACK to the first "
                         "of that month, so whole months are always replaced. 0 = full rebuild.")
    ap.add_argument("--full-every-days", type=int, default=7, metavar="D",
                    help="with --window-days, still force a FULL rebuild when the last one "
                         "is older than D days (0 = never force)")
    args = ap.parse_args()
    return {"args": args}


def _sync_cutoff(*, args):
    # names this section hands on; None where a path never assigns one
    _dtm = None
    incr_from = None
    windows = None
    # ── INCREMENTAL cutoff ──────────────────────────────────────────────────
    # A full rebuild re-pulls 20 monthly chunks from MSSQL every run — measured at
    # ~7 min for the sku aggregate and ~10 min for the per-sale split, which is why
    # the run kept being killed half way (0xC000013A in Task Scheduler).
    #
    # THE SNAP TO A MONTH BOUNDARY IS NOT COSMETIC. sales_sku_monthly is keyed by
    # month, so re-pulling from mid-month would delete a whole month and re-insert
    # only part of it — August would silently lose half its revenue. The cutoff is
    # therefore always the 1st of the month containing (today - N days).
    import datetime as _dtm          # NOT `dt`: that name is a per-row datetime later in main()
    incr_from = None
    if args.window_days and args.window_days > 0:
        _c = _dtm.date.today() - _dtm.timedelta(days=args.window_days)
        incr_from = _c.replace(day=1).isoformat()
        if incr_from <= args.since[:10]:
            incr_from = None          # window reaches back past --since: just do it all
    # Drift insurance: an edit to a bill OLDER than the window is invisible to an
    # incremental run, so a full rebuild is forced periodically regardless.
    if incr_from and args.full_every_days:
        try:
            sq0 = sqlite3.connect(str(DB))
            sq0.execute("CREATE TABLE IF NOT EXISTS sync_state(k TEXT PRIMARY KEY, v TEXT)")
            row = sq0.execute("SELECT v FROM sync_state WHERE k='last_full_sync'").fetchone()
            sq0.close()
            last_full = row[0] if row else None
            if not last_full or (_dtm.date.today() - _dtm.date.fromisoformat(last_full[:10])).days >= args.full_every_days:
                print(f"      [incremental] last full rebuild {last_full or 'never'} — forcing a FULL run")
                incr_from = None
        except Exception as e:
            print(f"      [incremental] cannot read sync_state ({e}) — falling back to FULL")
            incr_from = None

    windows = _month_windows(incr_from or args.since)   # per-month chunking of the heavy aggregates
    if incr_from:
        print(f"      [incremental] re-pulling from {incr_from} "
              f"({len(windows)} month chunk(s)); older rows are kept from the copied DB")
    return {"_dtm": _dtm, "incr_from": incr_from, "windows": windows}


def _sync_copy():
    # names this section hands on; None where a path never assigns one
    code2sid = None
    max_sid = None
    name2bid = None
    sq = None
    staging = None
    if not DB.exists():
        sys.exit(f"current DB not found: {DB}")

    print(f"[1/6] copy {DB.name} → {INCOMING.name} (preserves BoM/cost/settings)")
    # Claims .incoming (retrying/falling back if the dashboard holds it locked —
    # WinError 32) and clears orphan -wal/-shm (#72). See prepare_staging().
    staging = prepare_staging()
    shutil.copy2(DB, staging)

    sq = sqlite3.connect(str(staging)); sq.row_factory = sqlite3.Row
    # idempotent column adds — mirror backend/main.py startup migration so a sync
    # against an older oshinei.db doesn't fail with "no column named …".
    st_cols = {r[1] for r in sq.execute("PRAGMA table_info(sales_transaction)").fetchall()}
    if st_cols and "discount_reason_id" not in st_cols:
        sq.execute("ALTER TABLE sales_transaction ADD COLUMN discount_reason_id INTEGER")
    if st_cols and "coupon_series_id" not in st_cols:
        sq.execute("ALTER TABLE sales_transaction ADD COLUMN coupon_series_id INTEGER")
    if st_cols and "print_times" not in st_cols:
        sq.execute("ALTER TABLE sales_transaction ADD COLUMN print_times INTEGER DEFAULT 0")
    if st_cols and "dwell_min" not in st_cols:
        sq.execute("ALTER TABLE sales_transaction ADD COLUMN dwell_min INTEGER")
    # natural-key maps from the EXISTING (preserved) tables
    name2bid = {r["name"]: r["branch_id"] for r in sq.execute("SELECT branch_id,name FROM branches")}
    code2sid = {r["sku_code"]: r["sku_id"] for r in sq.execute("SELECT sku_id,sku_code FROM skus")}
    max_sid = sq.execute("SELECT COALESCE(MAX(sku_id),0) m FROM skus").fetchone()["m"]
    return {"code2sid": code2sid, "max_sid": max_sid, "name2bid": name2bid, "sq": sq, "staging": staging}


def _sync_masters(*, code2sid, name2bid, sq):
    # names this section hands on; None where a path never assigns one
    bid_map = None
    cat_bucket = None
    item_map = None
    itemid2code = None
    itemid2name = None
    mc = None
    new_skus = None
    print("[2/6] connect MSSQL + load Branch / Items / ItemCategory")
    mc = ResilientCursor(connect_mssql)   # auto-reconnects on dropped connection (err 20047)
    mc.execute("SELECT BranchId, BranchName FROM Branch")
    bid_map = {}                       # MSSQL BranchId → sqlite branch_id
    unmatched_branches = []
    for r in mc.fetchall():
        sn = short_name(r["BranchName"])
        if sn in name2bid:
            bid_map[r["BranchId"]] = name2bid[sn]
        else:
            unmatched_branches.append(r["BranchName"])

    mc.execute("SELECT CategoryId, CategoryName FROM ItemCategory")
    cat_bucket = {}                    # MSSQL CategoryId → bucket
    for r in mc.fetchall():
        cat_bucket[r["CategoryId"]] = bucket_for_cat(r["CategoryName"])

    # Item NAME column: schema-probed, never guessed — vendor builds differ
    # (ItemName / Name / Description / ItemDesc). Without it every lazily-created
    # stub SKU kept description = code, so new-branch code series (THBF*/THAL*/
    # OSN-PM-*) showed as raw codes across the UI forever. Degrades silently to
    # the previous behaviour when no such column exists.
    mc.execute("""SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS
                  WHERE TABLE_NAME='Items' AND DATA_TYPE IN
                        ('nvarchar','varchar','nchar','char')""")
    _item_cols = [r["COLUMN_NAME"] for r in mc.fetchall()]
    name_col = next((c for c in ("ItemName", "Name", "Description", "ItemDesc",
                                 "ItemNameThai", "ItemDescription")
                     if c in _item_cols), None)
    print(f"      Items name column = {name_col or 'NOT FOUND (stubs keep code as name)'}")

    mc.execute(f"SELECT ItemId, ItemCode, CategoryId"
               f"{', ' + name_col + ' AS ItemNm' if name_col else ''} FROM Items")
    itemid2code, itemid2name = {}, {}
    for r in mc.fetchall():
        code = (r["ItemCode"] or "").strip()
        itemid2code[r["ItemId"]] = code
        if name_col:
            nm = (r["ItemNm"] or "").strip()
            if nm and nm != code:
                itemid2name[r["ItemId"]] = nm
    item_map = {iid: code2sid[c] for iid, c in itemid2code.items() if c and c in code2sid}
    new_skus = 0                       # stubs added lazily (only for items that actually have sales)

    # Backfill EXISTING stub SKUs (description still == sku_code) now that names
    # are available — one-time self-heal, no re-ingest needed. Excel-ingested
    # names already differ from the code, so they are never touched.
    if itemid2name:
        code2name = {itemid2code[i]: n for i, n in itemid2name.items() if itemid2code.get(i)}
        stubs = sq.execute("SELECT sku_id, sku_code FROM skus "
                           "WHERE description IS NULL OR TRIM(description)='' "
                           "   OR description = sku_code").fetchall()
        fixed = [(code2name[s["sku_code"]], s["sku_id"])
                 for s in stubs if s["sku_code"] in code2name]
        if fixed:
            sq.executemany("UPDATE skus SET description=? WHERE sku_id=?", fixed)
        print(f"      backfilled names on {len(fixed):,} stub skus (of {len(stubs):,} stubs)")

    print(f"      branches mapped={len(bid_map)} (unmatched/skipped={len(unmatched_branches)}: {unmatched_branches[:6]})")
    print(f"      items pre-mapped={len(item_map)} of {len(itemid2code)}")
    return {"bid_map": bid_map, "cat_bucket": cat_bucket, "item_map": item_map, "itemid2code": itemid2code, "itemid2name": itemid2name, "mc": mc, "new_skus": new_skus}


def _sync_sku_monthly(*, bid_map, code2sid, incr_from, item_map, itemid2code, itemid2name, max_sid, mc, new_skus, sq, windows):
    print(f"[3/6] aggregate sales_sku_monthly (server-side GROUP BY · {len(windows)} monthly chunks)")
    skum_rows = _chunk(mc, f"""
        SELECT si.BranchId AS bid, si.ItemId AS iid,
               CONVERT(char(7), s.SaleDate, 126) AS ym,
               SUM({QTY_SANE})                       AS qty,
               SUM(CAST(si.ExtendedPrice AS float))  AS sub_exvat,
               SUM(CAST(ISNULL(si.Tax,0) AS float))  AS tax
        FROM SaleItem si
        JOIN Sale s ON s.SaleId=si.SaleId AND s.BranchId=si.BranchId
        WHERE {SALE_FILTER} AND ISNULL(si.Active,1)=1 AND {{RANGE}}
        GROUP BY si.BranchId, si.ItemId, CONVERT(char(7), s.SaleDate, 126)
    """, windows)
    skum = []
    skipped_rev = 0.0
    for r in skum_rows:
        bid = bid_map.get(r["bid"])
        if bid is None:
            skipped_rev += (r["sub_exvat"] or 0); continue
        sid = item_map.get(r["iid"])
        if sid is None:                              # lazy stub: item with sales but not in skus
            code = itemid2code.get(r["iid"])
            if not code:
                skipped_rev += (r["sub_exvat"] or 0); continue
            max_sid += 1
            sq.execute("INSERT INTO skus(sku_id,sku_code,description,category_id,active,business_type) "
                       "VALUES (?,?,?,?,1,?)",
                       (max_sid, code, itemid2name.get(r["iid"], code), None, None))
            code2sid[code] = max_sid; item_map[r["iid"]] = max_sid; sid = max_sid; new_skus += 1
        sub = round(r["sub_exvat"] or 0, 2); tax = round(r["tax"] or 0, 2)
        skum.append((bid, r["ym"], sid, round(r["qty"] or 0, 2), sub, tax, round(sub + tax, 2), "Easy"))
    if incr_from:
        # whole months only — see the snap note above
        sq.execute("DELETE FROM sales_sku_monthly WHERE period_month >= ?", (incr_from[:7],))
    else:
        sq.execute("DELETE FROM sales_sku_monthly")
    sq.executemany("INSERT INTO sales_sku_monthly(branch_id,period_month,sku_id,qty,subtotal_ex_vat,tax_total,total_inc_vat,source) "
                   "VALUES (?,?,?,?,?,?,?,?)", skum)
    print(f"      inserted {len(skum):,} rows · new stub skus={new_skus} · skipped revenue (unmapped branch)=฿{skipped_rev:,.0f}")
    return {}


def _sync_transactions(*, bid_map, cat_bucket, incr_from, mc, sq, windows):
    print("[4/6] build sales_transaction (header + food/drink/other split)")
    # per-sale food/drink/other ex-VAT split via conditional SUM on SaleItem.CategoryId
    drink_ids = [c for c, b in cat_bucket.items() if b == "drink"]
    other_ids = [c for c, b in cat_bucket.items() if b == "other"]
    def inlist(ids): return "(" + ",".join(str(int(i)) for i in ids) + ")" if ids else "(-1)"
    split_rows = _chunk(mc, f"""
        SELECT si.SaleId AS sid, si.BranchId AS bid,
          SUM(CASE WHEN si.CategoryId IN {inlist(drink_ids)} THEN CAST(si.ExtendedPrice AS float) ELSE 0 END) AS drink,
          SUM(CASE WHEN si.CategoryId IN {inlist(other_ids)} THEN CAST(si.ExtendedPrice AS float) ELSE 0 END) AS other,
          SUM(CASE WHEN si.CategoryId NOT IN {inlist(drink_ids + other_ids)} THEN CAST(si.ExtendedPrice AS float) ELSE 0 END) AS food
        FROM SaleItem si JOIN Sale s ON s.SaleId=si.SaleId AND s.BranchId=si.BranchId
        WHERE {SALE_FILTER} AND ISNULL(si.Active,1)=1 AND {{RANGE}}
        GROUP BY si.SaleId, si.BranchId
    """, windows)
    split = {(r["bid"], r["sid"]): (r["food"] or 0, r["drink"] or 0, r["other"] or 0) for r in split_rows}

    # coupon series applied per sale (keep the largest-discount coupon per bill)
    coupon_rows = _chunk(mc, f"""SELECT c.SaleId AS sid, c.BranchId AS bid, c.CouponSeriesId AS cs,
                 SUM(CAST(ISNULL(c.DiscountAmount,0) AS float)) AS amt
                 FROM CouponCardOutsideSystemUsed c
                 JOIN Sale s ON s.SaleId=c.SaleId AND s.BranchId=c.BranchId
                 WHERE {SALE_FILTER} AND {{RANGE}}
                 GROUP BY c.SaleId, c.BranchId, c.CouponSeriesId""", windows)
    sale_coupon = {}   # (bid,sid) -> coupon_series_id with the biggest discount
    for r in coupon_rows:
        k = (r["bid"], r["sid"]); cur = sale_coupon.get(k)
        if cur is None or (r["amt"] or 0) > cur[1]:
            sale_coupon[k] = (r["cs"], r["amt"] or 0)

    # per-bill sitting duration (เวลานั่ง) = first table OPEN → last table CLOSE,
    # from SaleTab (StartTime/EndTime). A bill may span several tab rows (split/join/
    # move) so we take MIN(StartTime)..MAX(EndTime). Cap to [0,360] min — drop tables
    # never properly closed (~0.27% with absurd dwell, e.g. days). Avg of the kept
    # band is ~92 min, matching pos_table_turn_day's avg_turn_min.
    dwell_rows = _chunk(mc, """SELECT st.SaleId AS sid, st.BranchId AS bid,
                 DATEDIFF(minute, MIN(st.StartTime), MAX(st.EndTime)) AS dwell
                 FROM SaleTab st
                 WHERE ISNULL(st.IsCancel,0)=0 AND st.EndTime > st.StartTime AND {RANGE}
                 GROUP BY st.SaleId, st.BranchId""", windows, col="st.StartTime")
    dwell_map = {}     # (bid,sid) -> dwell minutes
    for r in dwell_rows:
        dv = r["dwell"]
        if dv is not None and 0 <= dv <= 360:
            dwell_map[(r["bid"], r["sid"])] = int(dv)

    # PrintTimes is optional — older Easy POS schemas don't have it. Probe once and degrade to 0.
    mc.execute("SELECT 1 AS ok FROM INFORMATION_SCHEMA.COLUMNS "
               "WHERE TABLE_NAME='Sale' AND COLUMN_NAME='PrintTimes'")
    has_ptimes = bool(mc.fetchall())
    ptimes_expr = "ISNULL(s.PrintTimes, 0)" if has_ptimes else "0"

    # One-shot schema probe: does the Sale table carry a DISTINCT discount-approver
    # (manager-PIN) column, separate from UserId (the bill owner we already stage as
    # `cashier`)? Read-only — just logs. If a column like ApproveUserId / DiscountUserId
    # / ManagerId exists we can stage it and surface a "ผู้อนุมัติ" column in the drill;
    # if not, per-bill approver simply isn't recorded by this POS.
    try:
        mc.execute("""SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS
                      WHERE TABLE_NAME='Sale' AND (
                        COLUMN_NAME LIKE '%Approv%'      OR COLUMN_NAME LIKE '%Authoriz%' OR
                        COLUMN_NAME LIKE '%Manager%'     OR COLUMN_NAME LIKE '%DiscountUser%' OR
                        COLUMN_NAME LIKE '%DiscountBy%'  OR COLUMN_NAME LIKE '%PermitUser%' OR
                        COLUMN_NAME LIKE '%VoidUser%')""")
        _appr = [r["COLUMN_NAME"] for r in mc.fetchall()]
        print(f"      [probe] Sale discount-approver candidate columns: {_appr}" if _appr
              else "      [probe] Sale has NO distinct discount-approver column "
                   "(only UserId = bill owner) — per-bill approver not available in this POS")
    except Exception as _e:
        print(f"      [probe] approver-column probe skipped: {_e}")
    tx_rows = _chunk(mc, f"""
        SELECT s.BranchId AS bid, s.SaleId AS sid, s.SaleNum AS num, s.SaleDate AS dt,
               s.NumOfCust AS cust, CAST(s.Discount AS float) AS disc, s.DiscountReasonId AS rid,
               CAST(ISNULL(s.ServiceChargeAmount,0) AS float) AS svc,
               CAST(ISNULL(s.TipsAmount,0) AS float) AS tip, CAST(s.Total AS float) AS total,
               LTRIM(RTRIM(COALESCE(u.Username,''))) AS cashier,
               {ptimes_expr} AS ptimes
        FROM Sale s LEFT JOIN Users u ON u.Id = s.UserId
        WHERE {SALE_FILTER} AND {{RANGE}}
    """, windows)
    txr = []
    for r in tx_rows:
        bid = bid_map.get(r["bid"])
        if bid is None:
            continue
        f, d, o = split.get((r["bid"], r["sid"]), (0, 0, 0))
        dt = r["dt"]
        sold = dt.strftime("%Y-%m-%d %H:%M:%S") if hasattr(dt, "strftime") else str(dt)
        receipt = f"{sold[:10]}-{r['num']}-{r['sid']}"
        cs = sale_coupon.get((r["bid"], r["sid"]))
        txr.append((bid, receipt, sold, r["cashier"] or "", "", r["cust"] or 0,
                    round(f, 2), round(d, 2), round(o, 2), round(r["tip"] or 0, 2),
                    0.0, round(r["disc"] or 0, 2), round(r["svc"] or 0, 2), round(r["total"] or 0, 2), "Easy",
                    (r["rid"] or None), (cs[0] if cs else None), int(r["ptimes"] or 0),
                    dwell_map.get((r["bid"], r["sid"]))))
    if incr_from:
        sq.execute("DELETE FROM sales_transaction WHERE date(sold_at) >= ?", (incr_from,))
    else:
        sq.execute("DELETE FROM sales_transaction")
    sq.executemany("""INSERT INTO sales_transaction
        (branch_id,receipt_no,sold_at,cashier,table_no,customer_count,food_subtotal,drink_subtotal,
         other_subtotal,tip,sp_discount,discount,service_charge,total_inc_vat,source,
         discount_reason_id,coupon_series_id,print_times,dwell_min)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", txr)
    print(f"      inserted {len(txr):,} transactions")
    return {}


def _sync_reports(*, args, bid_map, incr_from, item_map, mc, name2bid, sq, windows):
    print("[4b/6] stage report tables (void/staff/payment/table-turn/delivery/coupon/kitchen)")
    stage_reports(sq, mc, args.since, bid_map, item_map, name2bid, windows, incr_from=incr_from)

    # sales_month_summary is written only by the Excel ingest, never by this sync,
    # so live-synced-only months would have 0 counts/gross/discounts in every
    # reader that trusts it (Top-60 cost/head, /api/analysis/compare, ...). Roll it
    # up from the just-synced sales_transaction for any month missing a summary row
    # (existing Excel rows untouched). See backend/month_summary.py.
    n_ms = month_summary.backfill(sq)
    print(f"[4b/6] sales_month_summary: backfilled {n_ms} live-synced (branch, month) rows")

    sq.commit()
    mc.close()

    # Optimize after the bulk rebuild: refresh query-planner stats + compact the
    # file (house rule: VACUUM+ANALYZE after a bulk mutate). Autocommit so VACUUM
    # is not attempted inside a transaction.
    print("[4c/6] optimize (ANALYZE + VACUUM)")
    sq.isolation_level = None
    sq.execute("ANALYZE")
    sq.execute("VACUUM")
    sq.isolation_level = ""
    return {}


def _sync_reconcile(*, sq, staging):
    print(f"[5/6] reconcile {staging.name} vs current DB")
    ok = reconcile(sq)
    sq.close()
    if not ok:
        print(f"[6/6] ⚠ RECONCILE FAILED — {staging.name} built but NOT safe to push. Investigate.")
        sys.exit(3)
    return {}


def _sync_publish(*, _dtm, args, incr_from, staging):
    # ── [5b/6] refresh menu_fact_* in the .incoming (🧪 วิเคราะห์วัตถุดิบ tab) ──
    # Rebuilds the last ~40 days (builder default) so the tab stays current on
    # every daily sync; older days ride along via the DB file copy in step 1.
    # NON-FATAL by design: a fact-build hiccup must never block the sales sync —
    # the tab just shows slightly stale coverage until the next run.
    if not args.no_menu_fact:
        print(f"[5b/6] refresh menu_fact_* (build_menu_fact.py --db {staging.name})")
        try:
            import subprocess
            env = dict(os.environ, PYTHONIOENCODING="utf-8")
            rc = subprocess.run(
                [sys.executable, str(HERE / "build_menu_fact.py"), "--db", str(staging)],
                timeout=2400, env=env).returncode
            if rc:
                print(f"      ⚠ menu-fact build exited rc={rc} — tab data stays at previous coverage")
        except Exception as e:
            print(f"      ⚠ menu-fact build skipped: {e}")

    # A fallback build (dashboard held .incoming locked at step 1) is published
    # over .incoming only NOW, fully built + reconciled — the poller/refresh_fly
    # never see a half-written file. Exit non-zero if the lock never cleared so
    # the scheduler bat aborts instead of pushing/applying a STALE .incoming.
    if not publish_staging(staging):
        sys.exit(4)

    # Only a FULL run resets the clock — that is what the --full-every-days guard
    # measures against, and an incremental run cannot stand in for it: it never
    # looked at the months it skipped.
    if not incr_from:
        try:
            sq2 = sqlite3.connect(str(INCOMING if INCOMING.exists() else DB))
            sq2.execute("CREATE TABLE IF NOT EXISTS sync_state(k TEXT PRIMARY KEY, v TEXT)")
            sq2.execute("INSERT OR REPLACE INTO sync_state(k, v) VALUES('last_full_sync', ?)",
                        (_dtm.date.today().isoformat(),))
            sq2.commit(); sq2.close()
        except Exception as e:
            print(f"      [incremental] could not record last_full_sync: {e}")

    if args.apply_local:
        print("[6/6] reconciled → applying to local oshinei.db")
        apply_local_swap()
    else:
        print("[6/6] done → oshinei.db.incoming ready & reconciled. Push with:")
        print("       python refresh_fly_db.py --db oshinei.db.incoming")
    return {}


def main():
    # Each stage is its own function (_sync_*, Phase 8 2026-09); they run in
    # order and hand what they compute forward through `ctx`. The statements
    # are the ones this function used to hold inline; the tests pin the output.
    ctx = {}
    ctx.update(_sync_args())
    ctx.update(_sync_cutoff(args=ctx.get("args")))
    ctx.update(_sync_copy())
    ctx.update(_sync_masters(code2sid=ctx.get("code2sid"), name2bid=ctx.get("name2bid"), sq=ctx.get("sq")))
    ctx.update(_sync_sku_monthly(bid_map=ctx.get("bid_map"), code2sid=ctx.get("code2sid"), incr_from=ctx.get("incr_from"), item_map=ctx.get("item_map"), itemid2code=ctx.get("itemid2code"), itemid2name=ctx.get("itemid2name"), max_sid=ctx.get("max_sid"), mc=ctx.get("mc"), new_skus=ctx.get("new_skus"), sq=ctx.get("sq"), windows=ctx.get("windows")))
    ctx.update(_sync_transactions(bid_map=ctx.get("bid_map"), cat_bucket=ctx.get("cat_bucket"), incr_from=ctx.get("incr_from"), mc=ctx.get("mc"), sq=ctx.get("sq"), windows=ctx.get("windows")))
    ctx.update(_sync_reports(args=ctx.get("args"), bid_map=ctx.get("bid_map"), incr_from=ctx.get("incr_from"), item_map=ctx.get("item_map"), mc=ctx.get("mc"), name2bid=ctx.get("name2bid"), sq=ctx.get("sq"), windows=ctx.get("windows")))
    ctx.update(_sync_reconcile(sq=ctx.get("sq"), staging=ctx.get("staging")))
    ctx.update(_sync_publish(_dtm=ctx.get("_dtm"), args=ctx.get("args"), incr_from=ctx.get("incr_from"), staging=ctx.get("staging")))

def _reset_table(sq, table, incr_from, date_col, cols):
    """Prepare a staged table for (re)filling from the HQ pull.

    Full run (incr_from None): drop and recreate — the pull covers the whole
    history. Incremental run: the pull covers only the months from incr_from,
    so keep the older rows the copied DB already holds and delete just the
    window's rows before they are re-inserted. `date_col` is the column the
    window is cut on: period_month compares YYYY-MM; day-grain columns and
    expressions such as substr(receipt_no,1,10) compare YYYY-MM-DD.

    WHY: until 2026-09 every stager dropped its table on every run, so the
    scheduled --window-days sync (the Fly run since #90) shipped pos_* tables
    holding the window's two or three months and nothing before — reports,
    drills and the recon's POS tender lines for any older month went blank on
    Fly while the local full run still had them. A table whose columns no
    longer match `cols` is rebuilt from scratch either way: its old rows are
    unusable."""
    sq.commit()                         # executescript() committed here too
    wanted = [c.strip().split()[0] for c in cols.split(",")]
    have = [r[1] for r in sq.execute(f"PRAGMA table_info({table})").fetchall()]
    if incr_from is None or have != wanted:
        sq.executescript(f"DROP TABLE IF EXISTS {table}; CREATE TABLE {table}({cols});")
        return
    cut = incr_from[:7] if date_col == "period_month" else incr_from[:10]
    sq.execute(f"DELETE FROM {table} WHERE {date_col} >= ?", (cut,))


def _stage_void(*, bid_map, incr_from, item_map, run, sq):
    # ── pos_void: cancelled/void items (qty + sale price; cost via BoM later) ──
    _reset_table(sq, "pos_void", incr_from, "period_month",
                 "branch_id INT, period_month TEXT, sku_id INT, reason_id INT, qty REAL, ext_price REAL")
    rows = run("""SELECT BranchId bid, ItemId iid, CONVERT(char(7),CancelDate,126) ym, ReasonId rid,
                 SUM(CAST(Qty AS float)) q, SUM(CAST(ExtendedPrice AS float)) ext
                 FROM CancelSaleItem WHERE {RANGE} GROUP BY BranchId, ItemId, CONVERT(char(7),CancelDate,126), ReasonId""", col="CancelDate")
    sq.executemany("INSERT INTO pos_void VALUES(?,?,?,?,?,?)",
        [(bid_map.get(r["bid"]), r["ym"], item_map.get(r["iid"]), r["rid"], round(r["q"] or 0,2), round(r["ext"] or 0,2))
         for r in rows if bid_map.get(r["bid"])])
    print(f"        pos_void: {len(rows)} groups")

    # ── pos_void_day: same voids per DAY (powers the by-day Operations report) ──
    # Mirror of pos_void grouped by char(10) CancelDate instead of char(7).
    _reset_table(sq, "pos_void_day", incr_from, "day",
                 "branch_id INT, day TEXT, sku_id INT, reason_id INT, qty REAL, ext_price REAL")
    vdrows = run("""SELECT BranchId bid, ItemId iid, CONVERT(char(10),CancelDate,126) d, ReasonId rid,
                 SUM(CAST(Qty AS float)) q, SUM(CAST(ExtendedPrice AS float)) ext
                 FROM CancelSaleItem WHERE {RANGE} GROUP BY BranchId, ItemId, CONVERT(char(10),CancelDate,126), ReasonId""", col="CancelDate")
    sq.executemany("INSERT INTO pos_void_day VALUES(?,?,?,?,?,?)",
        [(bid_map.get(r["bid"]), r["d"], item_map.get(r["iid"]), r["rid"], round(r["q"] or 0,2), round(r["ext"] or 0,2))
         for r in vdrows if bid_map.get(r["bid"])])
    sq.execute("CREATE INDEX IF NOT EXISTS ix_pvd ON pos_void_day(branch_id, day)")
    print(f"        pos_void_day: {len(vdrows)} groups")
    return {}


def _stage_payment(*, F, bid_map, incr_from, run, sq):
    # ── pos_staff_month: items served per staff (upsell/performance) ──
    _reset_table(sq, "pos_staff_month", incr_from, "period_month",
                 "branch_id INT, period_month TEXT, staff_id INT, staff_name TEXT, item_qty REAL")
    rows = run(f"""SELECT st.BranchId bid, CONVERT(char(7),s.SaleDate,126) ym, st.StaffId sid, st.StaffName nm,
                 SUM(CAST(st.Qty AS float)) q
                 FROM SaleItemStaff st JOIN Sale s ON s.SaleId=st.SaleId AND s.BranchId=st.BranchId
                 WHERE {F} AND {{RANGE}} GROUP BY st.BranchId, CONVERT(char(7),s.SaleDate,126), st.StaffId, st.StaffName""", col="s.SaleDate")
    sq.executemany("INSERT INTO pos_staff_month VALUES(?,?,?,?,?)",
        [(bid_map.get(r["bid"]), r["ym"], r["sid"], r["nm"], round(r["q"] or 0,2)) for r in rows if bid_map.get(r["bid"])])
    print(f"        pos_staff_month: {len(rows)} groups")

    # ── pos_payment_month: payment mix ──
    _reset_table(sq, "pos_payment_month", incr_from, "period_month",
                 "branch_id INT, period_month TEXT, payment_code TEXT, amount REAL, txn_count INT")
    rows = run(f"""SELECT p.BranchId bid, CONVERT(char(7),s.SaleDate,126) ym, p.PaymentCode pc,
                 SUM(CAST(p.Amount AS float)) amt, COUNT(*) n
                 FROM SalePayment p JOIN Sale s ON s.SaleId=p.SaleId AND s.BranchId=p.BranchId
                 WHERE {F} AND {{RANGE}} AND ISNULL(p.Active,1)=1 GROUP BY p.BranchId, CONVERT(char(7),s.SaleDate,126), p.PaymentCode""", col="s.SaleDate")
    sq.executemany("INSERT INTO pos_payment_month VALUES(?,?,?,?,?)",
        [(bid_map.get(r["bid"]), r["ym"], r["pc"], round(r["amt"] or 0,2), r["n"]) for r in rows if bid_map.get(r["bid"])])
    print(f"        pos_payment_month: {len(rows)} groups")
    return {}


def _stage_channel_tab(*, F, bid_map, incr_from, run, sq):
    # ── pos_payment_day: daily payment mix (powers daily reconciliation) ──
    # Same query as pos_payment_month but grouped by day (char(10)) instead of
    # month (char(7)). Lets the recon app reconcile POS tender vs bank/CC/Grab/
    # Lineman per day. Volume ~ branches × days × codes (small).
    _reset_table(sq, "pos_payment_day", incr_from, "day",
                 "branch_id INT, day TEXT, payment_code TEXT, amount REAL, txn_count INT")
    rows = run(f"""SELECT p.BranchId bid, CONVERT(char(10),s.SaleDate,126) d, p.PaymentCode pc,
                 SUM(CAST(p.Amount AS float)) amt, COUNT(*) n
                 FROM SalePayment p JOIN Sale s ON s.SaleId=p.SaleId AND s.BranchId=p.BranchId
                 WHERE {F} AND {{RANGE}} AND ISNULL(p.Active,1)=1 GROUP BY p.BranchId, CONVERT(char(10),s.SaleDate,126), p.PaymentCode""", col="s.SaleDate")
    sq.executemany("INSERT INTO pos_payment_day VALUES(?,?,?,?,?)",
        [(bid_map.get(r["bid"]), r["d"], r["pc"], round(r["amt"] or 0,2), r["n"]) for r in rows if bid_map.get(r["bid"])])
    sq.execute("CREATE INDEX IF NOT EXISTS ix_ppd ON pos_payment_day(branch_id, day)")
    print(f"        pos_payment_day: {len(rows)} groups")

    # ── pos_coupon_payment_month/day: Coupon tenders split by CouponSeriesId ──
    # SalePayment rolls every coupon up to PaymentCode='Coupon'; loyalty-point
    # redemptions (BAY/Krungsri, KTC) are distinguished only by CouponSeriesId.
    # Capture that split so the recon can use the point-redemption series as the
    # POS leg of the points 3-way (POS coupon ↔ issuer settlement ↔ bank deposit).
    for grain, col, conv in (("month", "period_month", "char(7)"), ("day", "day", "char(10)")):
        tbl = f"pos_coupon_payment_{grain}"
        _reset_table(sq, tbl, incr_from, col,
                     f"branch_id INT, {col} TEXT, coupon_series_id INT, amount REAL, txn_count INT")
        rows = run(f"""SELECT p.BranchId bid, CONVERT({conv},s.SaleDate,126) g, p.CouponSeriesId csid,
                     SUM(CAST(p.Amount AS float)) amt, COUNT(*) n
                     FROM SalePayment p JOIN Sale s ON s.SaleId=p.SaleId AND s.BranchId=p.BranchId
                     WHERE {F} AND {{RANGE}} AND ISNULL(p.Active,1)=1 AND p.PaymentCode='Coupon'
                     GROUP BY p.BranchId, CONVERT({conv},s.SaleDate,126), p.CouponSeriesId""", col="s.SaleDate")
        sq.executemany(f"INSERT INTO {tbl} VALUES(?,?,?,?,?)",
            [(bid_map.get(r["bid"]), r["g"], r["csid"], round(r["amt"] or 0, 2), r["n"])
             for r in rows if bid_map.get(r["bid"])])
        if grain == "day":
            sq.execute(f"CREATE INDEX IF NOT EXISTS ix_pcp ON {tbl}(branch_id, day)")
        print(f"        {tbl}: {len(rows)} groups")
    return {}


def _stage_coupon(*, F, bid_map, incr_from, run, sq):
    # ── sale_payment: per-RECEIPT payment lines (powers the discount-bill drill) ──
    # Same SalePayment source as pos_payment_month, but kept per receipt instead of
    # rolled up — a bill can have >1 row (split payment: e.g. cash + credit).
    # receipt_no is built IDENTICALLY to sales_transaction ("<date>-<SaleNum>-<SaleId>",
    # see the header build above) so it joins 1:1.
    _reset_table(sq, "sale_payment", incr_from, "substr(receipt_no,1,10)",
                 "branch_id INT, receipt_no TEXT, payment_code TEXT, amount REAL")
    rows = run(f"""SELECT p.BranchId bid, CONVERT(char(10),s.SaleDate,126) d, s.SaleNum num, s.SaleId sid,
                 p.PaymentCode pc, SUM(CAST(p.Amount AS float)) amt
                 FROM SalePayment p JOIN Sale s ON s.SaleId=p.SaleId AND s.BranchId=p.BranchId
                 WHERE {F} AND {{RANGE}} AND ISNULL(p.Active,1)=1
                 GROUP BY p.BranchId, CONVERT(char(10),s.SaleDate,126), s.SaleNum, s.SaleId, p.PaymentCode""", col="s.SaleDate")
    sq.executemany("INSERT INTO sale_payment VALUES(?,?,?,?)",
        [(bid_map.get(r["bid"]), f'{r["d"]}-{r["num"]}-{r["sid"]}', _norm_paycode(r["pc"]), round(r["amt"] or 0, 2))
         for r in rows if bid_map.get(r["bid"])])
    sq.execute("CREATE INDEX IF NOT EXISTS ix_salepay ON sale_payment(branch_id, receipt_no)")
    print(f"        sale_payment: {len(rows)} rows (per receipt × code)")

    # ── pos_table_turn_day: turnover / dwell ──
    _reset_table(sq, "pos_table_turn_day", incr_from, "day",
                 "branch_id INT, day TEXT, tabs INT, customers INT, avg_turn_min REAL")
    rows = run("""SELECT BranchId bid, CONVERT(char(10),StartTime,126) d, COUNT(*) tabs, SUM(NumOfCust) cust,
                 CAST(AVG(CASE WHEN EndTime>StartTime THEN DATEDIFF(minute,StartTime,EndTime)*1.0 END) AS float) avgmin
                 FROM SaleTab WHERE ISNULL(IsCancel,0)=0 AND {RANGE}
                 GROUP BY BranchId, CONVERT(char(10),StartTime,126)""", col="StartTime")
    sq.executemany("INSERT INTO pos_table_turn_day VALUES(?,?,?,?,?)",
        [(bid_map.get(r["bid"]), r["d"], r["tabs"], r["cust"] or 0, round(r["avgmin"] or 0,1)) for r in rows if bid_map.get(r["bid"])])
    print(f"        pos_table_turn_day: {len(rows)} groups")
    return {}


def _stage_cashcount(*, F, bid_map, incr_from, run, sq):
    # ── pos_channel_month: dine-in vs delivery vs takeaway (from Sale, revenue side) ──
    # (SaleByDeliveryType uses varchar Thai branch names that mojibake under UTF-8,
    #  so we derive channel from Sale.DeliveryType / OrderChannel instead — int codes,
    #  branch via BranchId. Map codes→labels in the report layer.)
    _reset_table(sq, "pos_channel_month", incr_from, "period_month",
                 "branch_id INT, period_month TEXT, delivery_type INT, order_channel INT, bills INT, customers INT, total_inc_vat REAL")
    rows = run(f"""SELECT s.BranchId bid, CONVERT(char(7),s.SaleDate,126) ym,
                 ISNULL(s.DeliveryType,0) dt, ISNULL(s.OrderChannel,0) oc,
                 COUNT(*) bills, SUM(s.NumOfCust) cust, SUM(CAST(s.Total AS float)) total
                 FROM Sale s WHERE {F} AND {{RANGE}}
                 GROUP BY s.BranchId, CONVERT(char(7),s.SaleDate,126), ISNULL(s.DeliveryType,0), ISNULL(s.OrderChannel,0)""", col="s.SaleDate")
    sq.executemany("INSERT INTO pos_channel_month VALUES(?,?,?,?,?,?,?)",
        [(bid_map.get(r["bid"]), r["ym"], r["dt"], r["oc"], r["bills"], r["cust"] or 0, round(r["total"] or 0,2))
         for r in rows if bid_map.get(r["bid"])])
    print(f"        pos_channel_month: {len(rows)} groups")

    # ── pos_channel_day: same channel mix per DAY (powers the by-day Operations report) ──
    # Mirror of pos_channel_month grouped by char(10) SaleDate instead of char(7).
    _reset_table(sq, "pos_channel_day", incr_from, "day",
                 "branch_id INT, day TEXT, delivery_type INT, order_channel INT, bills INT, customers INT, total_inc_vat REAL")
    cdrows = run(f"""SELECT s.BranchId bid, CONVERT(char(10),s.SaleDate,126) d,
                 ISNULL(s.DeliveryType,0) dt, ISNULL(s.OrderChannel,0) oc,
                 COUNT(*) bills, SUM(s.NumOfCust) cust, SUM(CAST(s.Total AS float)) total
                 FROM Sale s WHERE {F} AND {{RANGE}}
                 GROUP BY s.BranchId, CONVERT(char(10),s.SaleDate,126), ISNULL(s.DeliveryType,0), ISNULL(s.OrderChannel,0)""", col="s.SaleDate")
    sq.executemany("INSERT INTO pos_channel_day VALUES(?,?,?,?,?,?,?)",
        [(bid_map.get(r["bid"]), r["d"], r["dt"], r["oc"], r["bills"], r["cust"] or 0, round(r["total"] or 0,2))
         for r in cdrows if bid_map.get(r["bid"])])
    sq.execute("CREATE INDEX IF NOT EXISTS ix_pchd ON pos_channel_day(branch_id, day)")
    print(f"        pos_channel_day: {len(cdrows)} groups")

    # ── pos_coupon_month: promo / coupon usage ──
    _reset_table(sq, "pos_coupon_month", incr_from, "period_month",
                 "branch_id INT, period_month TEXT, coupon_series_id INT, used_count INT, discount_amount REAL")
    rows = run("""SELECT BranchId bid, CONVERT(char(7),UsedDate,126) ym, CouponSeriesId cs,
                 COUNT(*) n, SUM(CAST(DiscountAmount AS float)) disc
                 FROM CouponCardOutsideSystemUsed WHERE {RANGE} GROUP BY BranchId, CONVERT(char(7),UsedDate,126), CouponSeriesId""", col="UsedDate")
    sq.executemany("INSERT INTO pos_coupon_month VALUES(?,?,?,?,?)",
        [(bid_map.get(r["bid"]), r["ym"], r["cs"], r["n"], round(r["disc"] or 0,2)) for r in rows if bid_map.get(r["bid"])])
    print(f"        pos_coupon_month: {len(rows)} groups")
    return {}


def _stage_discount(*, F, bid_map, incr_from, mc, run, sq):
    # ── pos_coupon_day: same usage but per DAY (powers the by-day coupon drill) ──
    _reset_table(sq, "pos_coupon_day", incr_from, "day",
                 "branch_id INT, day TEXT, coupon_series_id INT, used_count INT, discount_amount REAL")
    drows = run("""SELECT BranchId bid, CONVERT(char(10),UsedDate,126) d, CouponSeriesId cs,
                 COUNT(*) n, SUM(CAST(DiscountAmount AS float)) disc
                 FROM CouponCardOutsideSystemUsed WHERE {RANGE} GROUP BY BranchId, CONVERT(char(10),UsedDate,126), CouponSeriesId""", col="UsedDate")
    sq.executemany("INSERT INTO pos_coupon_day VALUES(?,?,?,?,?)",
        [(bid_map.get(r["bid"]), r["d"], r["cs"], r["n"], round(r["disc"] or 0,2)) for r in drows if bid_map.get(r["bid"])])
    sq.execute("CREATE INDEX IF NOT EXISTS idx_pcd ON pos_coupon_day(coupon_series_id, day)")
    sq.execute("CREATE INDEX IF NOT EXISTS idx_cpd ON pos_coupon_day(day, branch_id)")
    print(f"        pos_coupon_day: {len(drows)} groups")

    # ── coupon_series: master names for the series ids above (recon drill labels) ──
    sq.executescript("DROP TABLE IF EXISTS coupon_series; CREATE TABLE coupon_series("
                     "coupon_series_id INT PRIMARY KEY, name TEXT, code TEXT, active INT);")
    mc.execute("SELECT CouponSeriesId, CouponSeriesName, CouponSeriesCode, Active FROM CouponSeries")
    csrows = mc.fetchall()
    sq.executemany("INSERT OR REPLACE INTO coupon_series VALUES(?,?,?,?)",
        [(r["CouponSeriesId"], (r["CouponSeriesName"] or "").strip(),
          (r["CouponSeriesCode"] or "").strip(), int(r["Active"] or 0)) for r in csrows])
    print(f"        coupon_series: {len(csrows)} series names")

    # ── pos_kitchen_month: cook/ticket time ──
    _reset_table(sq, "pos_kitchen_month", incr_from, "period_month",
                 "branch_id INT, period_month TEXT, avg_cook_sec REAL, cooked_count INT")
    rows = run(f"""SELECT si.BranchId bid, CONVERT(char(7),s.SaleDate,126) ym,
                 CAST(AVG(CASE WHEN si.CookedTime IS NOT NULL AND si.CookedTime>si.InsertDate THEN DATEDIFF(second,si.InsertDate,si.CookedTime)*1.0 END) AS float) avgsec,
                 SUM(CASE WHEN si.CookedTime IS NOT NULL THEN 1 ELSE 0 END) cooked
                 FROM SaleItem si JOIN Sale s ON s.SaleId=si.SaleId AND s.BranchId=si.BranchId
                 WHERE {F} AND {{RANGE}} GROUP BY si.BranchId, CONVERT(char(7),s.SaleDate,126)""", col="s.SaleDate")
    sq.executemany("INSERT INTO pos_kitchen_month VALUES(?,?,?,?)",
        [(bid_map.get(r["bid"]), r["ym"], round(r["avgsec"] or 0,1), r["cooked"] or 0) for r in rows if bid_map.get(r["bid"])])
    print(f"        pos_kitchen_month: {len(rows)} groups")
    return {}


def _stage_daily(*, F, bid_map, item_map, mc, since, sq):
    # names this stage hands on
    _dt = None
    dly_since = None
    mx = None
    # ── sales_sku_daily: per-branch per-sku DAILY qty/revenue (rolling 120 days) ──
    #    Foundation for daily menu-spike anomaly (#5) and daily cost/head (#10,
    #    COGS via our BoM only). Bounded window keeps the DB push light.
    import datetime as _dt
    mc.execute("SELECT CONVERT(char(10), MAX(SaleDate), 126) AS mx FROM Sale")
    mx = mc.fetchone()["mx"]
    dly_since = since
    if mx:
        y, m, d = (int(x) for x in mx.split("-"))
        dly_since = (_dt.date(y, m, d) - _dt.timedelta(days=120)).isoformat()
    # Surface every line the qty guard zeroes (never a silent cap) and keep a
    # durable audit trail in the staged DB — sync_qty_guard_log rides the swap to
    # local AND Fly, so the 🩺 data-quality side can read it later. INSERT OR
    # IGNORE keeps the original first_seen when the same line re-appears nightly.
    mc.execute(f"""SELECT CONVERT(char(10), s.SaleDate, 126) d, b.BranchName bn,
                          s.SaleId sid, i.ItemCode code, CAST(si.Qty AS float) qty
                   FROM SaleItem si
                   JOIN Sale s ON s.SaleId=si.SaleId AND s.BranchId=si.BranchId
                   JOIN Branch b ON b.BranchId=s.BranchId
                   LEFT JOIN Items i ON i.ItemId=si.ItemId
                   WHERE {F} AND ISNULL(si.Active,1)=1 AND s.SaleDate >= '{dly_since}'
                         AND ABS(CAST(si.Qty AS float)) > {MAX_SANE_LINE_QTY}""")
    bad_lines = mc.fetchall()
    sq.execute("CREATE TABLE IF NOT EXISTS sync_qty_guard_log("
               "sale_date TEXT, branch TEXT, sale_id INT, item_code TEXT, qty REAL, "
               "first_seen TEXT, PRIMARY KEY(sale_date, sale_id, item_code))")
    if bad_lines:
        _now = _dt.datetime.now().isoformat(timespec="seconds")
        sq.executemany("INSERT OR IGNORE INTO sync_qty_guard_log VALUES(?,?,?,?,?,?)",
                       [(r["d"], r["bn"], r["sid"], (r["code"] or "").strip(), r["qty"], _now)
                        for r in bad_lines])
        print(f"        QTY GUARD: zeroed {len(bad_lines)} insane line(s) "
              f"(|qty| > {MAX_SANE_LINE_QTY:,}/line):")
        for r in bad_lines[:10]:
            print(f"          {r['d']} {r['bn']} SaleId={r['sid']} "
                  f"{(r['code'] or '').strip()} qty={r['qty']:,.0f}")
    sq.executescript("DROP TABLE IF EXISTS sales_sku_daily; CREATE TABLE sales_sku_daily("
                     "branch_id INT, day TEXT, sku_id INT, qty REAL, subtotal_ex_vat REAL);")
    drows = _chunk(mc, f"""
        SELECT si.BranchId bid, si.ItemId iid, CONVERT(char(10), s.SaleDate, 126) d,
               SUM({QTY_SANE}) qty, SUM(CAST(si.ExtendedPrice AS float)) rev
        FROM SaleItem si JOIN Sale s ON s.SaleId=si.SaleId AND s.BranchId=si.BranchId
        WHERE {F} AND ISNULL(si.Active,1)=1 AND {{RANGE}}
        GROUP BY si.BranchId, si.ItemId, CONVERT(char(10), s.SaleDate, 126)
    """, _month_windows(dly_since))
    sq.executemany("INSERT INTO sales_sku_daily VALUES(?,?,?,?,?)",
        [(bid_map.get(r["bid"]), r["d"], item_map.get(r["iid"]), round(r["qty"] or 0,2), round(r["rev"] or 0,2))
         for r in drows if bid_map.get(r["bid"]) and item_map.get(r["iid"])])
    sq.execute("CREATE INDEX IF NOT EXISTS ix_ssd ON sales_sku_daily(branch_id, day, sku_id)")
    print(f"        sales_sku_daily: {len(drows)} rows (since {dly_since})")
    return {"_dt": _dt, "dly_since": dly_since, "mx": mx}


def _stage_bill_cogs(*, F, _dt, bid_map, dly_since, incr_from, item_map, mc, mx, since, sq, windows):
    # ── sale_bill_cogs: per-BILL BoM COGS (rolling 31 days) → exact P&L per bill ──
    #    Per-sku unit cost from OUR data (sku_cost cost-management table first,
    #    then recipe → alias; tier codes=0 via is_tier_code). Same priority as main._unified_cogs_expr
    #    (cost-management first since 2026-06-11). Heavy pull but stored per-bill.
    bill_since = dly_since
    if mx:
        y, m, d = (int(x) for x in mx.split("-"))
        bill_since = (_dt.date(y, m, d) - _dt.timedelta(days=31)).isoformat()
    costmap = {}
    for r in sq.execute(f"""
        SELECT s.sku_id, s.sku_code, {raw_unit_cost_sql('s')} AS c
        FROM skus s""").fetchall():
        costmap[r["sku_id"]] = 0.0 if is_tier_code(r["sku_code"]) else (r["c"] or 0.0)
    # Refresh the per-SKU effective-cost lookup in the staged DB, so a swapped-in
    # DB never serves stale costs (main only force-rebuilds it at startup, not on
    # hot-swap pre-2026-06-11). Shared helper = same tier-code zeroing as main.
    refresh_sku_effective_cost(sq)
    sq.executescript("DROP TABLE IF EXISTS sale_bill_cogs; CREATE TABLE sale_bill_cogs("
                     "receipt_no TEXT PRIMARY KEY, branch_id INT, day TEXT, cogs_bom REAL);")
    bill_rows = _chunk(mc, f"""
        SELECT s.SaleId sid, s.BranchId bid, s.SaleNum num, CONVERT(char(10),s.SaleDate,126) d,
               si.ItemId iid, SUM({QTY_SANE}) qty,
               SUM(CAST(si.ExtendedPrice AS float)) rev
        FROM SaleItem si JOIN Sale s ON s.SaleId=si.SaleId AND s.BranchId=si.BranchId
        WHERE {F} AND ISNULL(si.Active,1)=1 AND {{RANGE}}
        GROUP BY s.SaleId, s.BranchId, s.SaleNum, CONVERT(char(10),s.SaleDate,126), si.ItemId
    """, _month_windows(bill_since))
    bills = {}
    lines = []   # (receipt_no, sku_id, qty, rev, bom_cost) — per-menu-line detail, kept for the loss-bill drill
    for r in bill_rows:
        bid = bid_map.get(r["bid"])
        if bid is None:
            continue
        sid_local = item_map.get(r["iid"])
        c = costmap.get(sid_local, 0.0)
        qty = r["qty"] or 0
        rcpt = f"{r['d']}-{r['num']}-{r['sid']}"
        b = bills.get(rcpt)
        if b is None:
            bills[rcpt] = [bid, r["d"], qty * c]
        else:
            b[2] += qty * c
        if sid_local is not None:
            lines.append((rcpt, sid_local, round(qty, 2), round(r["rev"] or 0, 2), round(qty * c, 4)))
    sq.executemany("INSERT OR REPLACE INTO sale_bill_cogs VALUES(?,?,?,?)",
        [(rc, v[0], v[1], round(v[2], 2)) for rc, v in bills.items()])
    sq.execute("CREATE INDEX IF NOT EXISTS ix_sbc ON sale_bill_cogs(branch_id, day)")
    print(f"        sale_bill_cogs: {len(bills)} bills (since {bill_since})")

    # ── sale_bill_item: per-receipt MENU LINES for LOSS bills (31d) + DISCOUNT bills ──
    #    Powers the per-bill P&L drill (🧾 P&L ต่อบิล) reached from the บิลขาดทุน table
    #    AND from clicking any row in the บิลส่วนลด drill. Two line sources, de-duped:
    #      · LOSS bills in the rolling 31-day window (COGS > revenue) — the existing
    #        "บิลขาดทุน" scope, from `lines` already pulled above.
    #      · ALL DISCOUNT bills across the FULL synced history (Sale.Discount>0) — so a
    #        manager can open the P&L of any discounted bill, not just recent loss ones.
    #    Discount bills are ~7–8% of bills; this keeps the table bounded (NOT every bill).
    #    rev = SUM(ExtendedPrice) treated ex-VAT (consistent with sales_sku_daily);
    #    bom_cost = qty × per-SKU BoM unit cost (BUFFET tier = 0; IRON RULE: BoM only).
    revmap = {row["receipt_no"]: (row["t"] or 0) / 1.07 for row in
              sq.execute("SELECT receipt_no, total_inc_vat t FROM sales_transaction WHERE total_inc_vat>0")}
    loss_set = {rc for rc, v in bills.items() if v[2] > revmap.get(rc, float("inf"))}
    # de-dup on (receipt_no, sku_id) — a 31-day bill that is BOTH loss and discount
    # would otherwise emit the same line twice → double-count in the P&L SUM.
    item_rows = {(ln[0], ln[1]): ln for ln in lines if ln[0] in loss_set}
    # full-history per-line pull for discount bills (same shape/grouping as bill_rows).
    disc_line_rows = _chunk(mc, f"""
        SELECT s.SaleId sid, s.BranchId bid, s.SaleNum num, CONVERT(char(10),s.SaleDate,126) d,
               si.ItemId iid, SUM({QTY_SANE}) qty,
               SUM(CAST(si.ExtendedPrice AS float)) rev
        FROM SaleItem si JOIN Sale s ON s.SaleId=si.SaleId AND s.BranchId=si.BranchId
        WHERE {F} AND ISNULL(si.Active,1)=1 AND s.Discount>0 AND {{RANGE}}
        GROUP BY s.SaleId, s.BranchId, s.SaleNum, CONVERT(char(10),s.SaleDate,126), si.ItemId
    """, windows)
    disc_bill_set = set()
    for r in disc_line_rows:
        if bid_map.get(r["bid"]) is None:
            continue
        sid_local = item_map.get(r["iid"])
        if sid_local is None:
            continue
        qty = r["qty"] or 0
        c = costmap.get(sid_local, 0.0)
        rcpt = f"{r['d']}-{r['num']}-{r['sid']}"
        disc_bill_set.add(rcpt)
        item_rows[(rcpt, sid_local)] = (rcpt, sid_local, round(qty, 2),
                                        round(r["rev"] or 0, 2), round(qty * c, 4))
    _reset_table(sq, "sale_bill_item", incr_from, "substr(receipt_no,1,10)",
                 "receipt_no TEXT, sku_id INT, qty REAL, rev REAL, bom_cost REAL")
    if incr_from is not None:
        # the loss-bill lines reach back 31 days from the last sale — earlier than
        # the window can be — so replace those receipts too, never duplicate them
        sq.executemany("DELETE FROM sale_bill_item WHERE receipt_no=?",
                       [(rc,) for rc in {k[0] for k in item_rows}])
    sq.executemany("INSERT INTO sale_bill_item VALUES(?,?,?,?,?)", list(item_rows.values()))
    sq.execute("CREATE INDEX IF NOT EXISTS ix_sbi ON sale_bill_item(receipt_no)")
    print(f"        sale_bill_item: {len(item_rows)} lines across "
          f"{len(loss_set | disc_bill_set)} bills ({len(loss_set)} loss + {len(disc_bill_set)} discount)")

    # ── Extra POS-deep-dive tables (separate "Extra" report group) ──
    stage_extra(sq, mc, since, bid_map, item_map)
    return {}


def stage_reports(sq, mc, since, bid_map, item_map, name2bid, windows, incr_from=None):
    """Stage POS report data into pos_* tables — server-side aggregates only.
    IRON RULE: NO POS cost is staged. We keep quantities / revenue / counts /
    timings; all COGS valuation is done later from our BoM.

    `windows` is the month range being pulled; on an incremental run
    (incr_from set) it is only the recent months, and every windowed table
    keeps the older rows the copied DB already holds (see _reset_table)."""
    F = SALE_FILTER

    def run(sql, params=None, col=None):
        # col set → per-month chunked (sql must contain {RANGE}); keeps each heavy
        # scan short so a transient MSSQL drop costs one cheap month-retry.
        if col is not None:
            return _chunk(mc, sql, windows, col)
        mc.execute(sql, params or (since,)); return mc.fetchall()

    # Each table family is its own stage (_stage_*, Phase 6b 2026-09) — the
    # [k/6] steps the log already names. They run in order and hand forward
    # through `ctx`; backend/test_sync_golden.py pins every table they write.
    ctx = {"F": F, "bid_map": bid_map, "incr_from": incr_from, "item_map": item_map, "mc": mc, "name2bid": name2bid, "run": run, "since": since, "sq": sq, "windows": windows}
    ctx.update(_stage_void(bid_map=ctx.get("bid_map"), incr_from=ctx.get("incr_from"), item_map=ctx.get("item_map"), run=ctx.get("run"), sq=ctx.get("sq")))
    ctx.update(_stage_payment(F=ctx.get("F"), bid_map=ctx.get("bid_map"), incr_from=ctx.get("incr_from"), run=ctx.get("run"), sq=ctx.get("sq")))
    ctx.update(_stage_channel_tab(F=ctx.get("F"), bid_map=ctx.get("bid_map"), incr_from=ctx.get("incr_from"), run=ctx.get("run"), sq=ctx.get("sq")))
    ctx.update(_stage_coupon(F=ctx.get("F"), bid_map=ctx.get("bid_map"), incr_from=ctx.get("incr_from"), run=ctx.get("run"), sq=ctx.get("sq")))
    ctx.update(_stage_cashcount(F=ctx.get("F"), bid_map=ctx.get("bid_map"), incr_from=ctx.get("incr_from"), run=ctx.get("run"), sq=ctx.get("sq")))
    ctx.update(_stage_discount(F=ctx.get("F"), bid_map=ctx.get("bid_map"), incr_from=ctx.get("incr_from"), mc=ctx.get("mc"), run=ctx.get("run"), sq=ctx.get("sq")))
    ctx.update(_stage_daily(F=ctx.get("F"), bid_map=ctx.get("bid_map"), item_map=ctx.get("item_map"), mc=ctx.get("mc"), since=ctx.get("since"), sq=ctx.get("sq")))
    ctx.update(_stage_bill_cogs(F=ctx.get("F"), _dt=ctx.get("_dt"), bid_map=ctx.get("bid_map"), incr_from=ctx.get("incr_from"), dly_since=ctx.get("dly_since"), item_map=ctx.get("item_map"), mc=ctx.get("mc"), mx=ctx.get("mx"), since=ctx.get("since"), sq=ctx.get("sq"), windows=ctx.get("windows")))


def _th(s):
    """Decode TIS-620 (CP874) varchar that pymssql returns mis-decoded as latin1
    under the default UTF-8 connection. Returns the original string on failure."""
    if not s:
        return s
    try:
        return s.encode("latin1").decode("cp874")
    except Exception:
        return s


def stage_extra(sq, mc, since, bid_map, item_map):
    """Extra analytics from previously-untapped POS tables (separate UI group).
    IRON RULE: still NO POS cost — quantities / counts / amounts / over-short only."""
    def run(sql, params=None):
        mc.execute(sql, params if params is not None else (since,)); return mc.fetchall()

    # ── pos_cashcount_day: cash over/short + ops summary per branch per day ──
    sq.executescript("DROP TABLE IF EXISTS pos_cashcount_day; CREATE TABLE pos_cashcount_day("
                     "branch_id INT, day TEXT, over_short REAL, total_sales REAL, pax INT, "
                     "invoices INT, discount REAL, cancel REAL);")
    rows = run("""SELECT BranchId bid, CONVERT(char(10),SalesDate,126) d,
                 SUM(CAST(OverShortAmount AS float)) os, SUM(CAST(TotalSales AS float)) ts,
                 SUM(CountPax) pax, SUM(CountInvoice) inv, SUM(CAST(TotalDiscount AS float)) disc,
                 SUM(CAST(TotalCancel AS float)) cancel
                 FROM CashCount WHERE ISNULL(Active,1)=1 AND SalesDate >= %s
                 GROUP BY BranchId, CONVERT(char(10),SalesDate,126)""")
    sq.executemany("INSERT INTO pos_cashcount_day VALUES(?,?,?,?,?,?,?,?)",
        [(bid_map.get(r["bid"]), r["d"], round(r["os"] or 0,2), round(r["ts"] or 0,2), r["pax"] or 0,
          r["inv"] or 0, round(r["disc"] or 0,2), round(r["cancel"] or 0,2))
         for r in rows if bid_map.get(r["bid"])])
    sq.execute("CREATE INDEX IF NOT EXISTS ix_cc ON pos_cashcount_day(branch_id, day)")
    print(f"        pos_cashcount_day: {len(rows)} groups")

    # ── pos_customer_type_month: walk-in vs Grab vs Line Man vs takeaway vs credit ──
    sq.executescript("DROP TABLE IF EXISTS pos_customer_type_month; CREATE TABLE pos_customer_type_month("
                     "branch_id INT, period_month TEXT, type_name TEXT, customers INT, net REAL);")
    rows = run("""SELECT b.BranchId bid, CONVERT(char(7),sct.SaleDate,126) ym, sct.CustomerTypeName tn,
                 SUM(sct.NumOfCustomer) cust, SUM(CAST(sct.NetAmnt AS float)) net
                 FROM SaleByCustomerType sct JOIN Branch b ON b.BranchCode=sct.BranchCode
                 WHERE sct.SaleDate >= %s
                 GROUP BY b.BranchId, CONVERT(char(7),sct.SaleDate,126), sct.CustomerTypeName""")
    sq.executemany("INSERT INTO pos_customer_type_month VALUES(?,?,?,?,?)",
        [(bid_map.get(r["bid"]), r["ym"], _th(r["tn"]), r["cust"] or 0, round(r["net"] or 0,2))
         for r in rows if bid_map.get(r["bid"])])
    print(f"        pos_customer_type_month: {len(rows)} groups")

    # ── pos_modifier: top special requests per menu per branch (all-time, free choices) ──
    sq.executescript("DROP TABLE IF EXISTS pos_modifier; CREATE TABLE pos_modifier("
                     "branch_id INT, sku_id INT, choice_name TEXT, n INT, qty REAL);")
    rows = run("""SELECT BranchId bid, ItemId iid, ChoiceName cn, COUNT(*) n,
                 SUM(CAST(ISNULL(ChoiceQty,1) AS float)) q
                 FROM SaleItemModifier WHERE ChoiceName IS NOT NULL
                 GROUP BY BranchId, ItemId, ChoiceName HAVING COUNT(*) >= 20""", ())
    sq.executemany("INSERT INTO pos_modifier VALUES(?,?,?,?,?)",
        [(bid_map.get(r["bid"]), item_map.get(r["iid"]), r["cn"], r["n"], round(r["q"] or 0,2))
         for r in rows if bid_map.get(r["bid"])])
    sq.execute("CREATE INDEX IF NOT EXISTS ix_mod ON pos_modifier(branch_id)")
    print(f"        pos_modifier: {len(rows)} groups")

    # ── pos_price_change: price-override log (governance) — rolling since ──
    sq.executescript("DROP TABLE IF EXISTS pos_price_change; CREATE TABLE pos_price_change("
                     "branch_id INT, change_date TEXT, sku_id INT, item_desc TEXT, "
                     "default_price REAL, new_price REAL, user_name TEXT);")
    rows = run("""SELECT BranchId bid, CONVERT(char(19),ChangeDate,126) cd, ItemId iid, ItemDesc descr,
                 CAST(DefaultPrice AS float) dp, CAST(NewPrice AS float) np, UserName un
                 FROM ChangePriceLog WHERE ChangeDate >= %s""")
    sq.executemany("INSERT INTO pos_price_change VALUES(?,?,?,?,?,?,?)",
        [(bid_map.get(r["bid"]), r["cd"], item_map.get(r["iid"]), r["descr"],
          round(r["dp"] or 0,2), round(r["np"] or 0,2), r["un"])
         for r in rows if bid_map.get(r["bid"])])
    print(f"        pos_price_change: {len(rows)} rows")

    # ── pos_zone: table zones — #tables + seats per zone per branch (FBTable master) ──
    sq.executescript("DROP TABLE IF EXISTS pos_zone; CREATE TABLE pos_zone("
                     "branch_id INT, zone_id INT, tables INT, seats INT);")
    rows = run("""SELECT BranchId bid, ISNULL(ZoneId,0) zid, COUNT(*) tabs, SUM(ISNULL(NumberOfSeat,0)) seats
                 FROM FBTable WHERE ISNULL(Active,1)=1 GROUP BY BranchId, ISNULL(ZoneId,0)""", ())
    sq.executemany("INSERT INTO pos_zone VALUES(?,?,?,?)",
        [(bid_map.get(r["bid"]), r["zid"], r["tabs"], r["seats"] or 0) for r in rows if bid_map.get(r["bid"])])
    print(f"        pos_zone: {len(rows)} groups")

    # ── pos_cancel_reason: delete/void reason lookup (decoded Thai) ──
    sq.executescript("DROP TABLE IF EXISTS pos_cancel_reason; CREATE TABLE pos_cancel_reason("
                     "reason_id INT PRIMARY KEY, reason_desc TEXT);")
    rrows = run("SELECT ReasonId rid, ReasonDesc rd FROM DeleteSaleReason", ())
    sq.executemany("INSERT OR REPLACE INTO pos_cancel_reason VALUES(?,?)",
        [(r["rid"], _th(r["rd"])) for r in rrows])
    print(f"        pos_cancel_reason: {len(rrows)} rows")

    # ── pos_cancel_bill: whole BILLS voided/deleted (Sale.Active=0) per branch/month/reason.
    #    Distinct from pos_void (which is cancelled ITEMS within otherwise-valid bills). ──
    sq.executescript("DROP TABLE IF EXISTS pos_cancel_bill; CREATE TABLE pos_cancel_bill("
                     "branch_id INT, period_month TEXT, reason_id INT, bills INT, amount REAL);")
    rows = run("""SELECT BranchId bid, CONVERT(char(7),SaleDate,126) ym, ISNULL(DeleteReasonId,0) rid,
                 COUNT(*) n, SUM(CAST(ISNULL(Total,0) AS float)) amt
                 FROM Sale WHERE ISNULL(Active,1)=0 AND SaleDate >= %s
                 GROUP BY BranchId, CONVERT(char(7),SaleDate,126), ISNULL(DeleteReasonId,0)""")
    sq.executemany("INSERT INTO pos_cancel_bill VALUES(?,?,?,?,?)",
        [(bid_map.get(r["bid"]), r["ym"], r["rid"], r["n"], round(r["amt"] or 0,2))
         for r in rows if bid_map.get(r["bid"])])
    print(f"        pos_cancel_bill: {len(rows)} groups")

    # ── pos_discount_reason: discount-reason lookup (Sale.DiscountReasonId -> name) ──
    #    e.g. "ส่วนลด 10%", "ส่วนลดด่วน". ReasonId 0 = no reason set on the bill.
    sq.executescript("DROP TABLE IF EXISTS pos_discount_reason; CREATE TABLE pos_discount_reason("
                     "reason_id INT PRIMARY KEY, reason_desc TEXT, amount_percent REAL);")
    drrows = run("SELECT ReasonId rid, ReasonDesc rd, CAST(ISNULL(AmountPercent,0) AS float) pct FROM DiscountSaleReason", ())
    sq.executemany("INSERT OR REPLACE INTO pos_discount_reason VALUES(?,?,?)",
        [(r["rid"], _th(r["rd"]), r["pct"] or 0) for r in drrows])
    print(f"        pos_discount_reason: {len(drrows)} rows")

    # ── pos_discount_month: discounted BILLS per branch/month/reason (Sale.Discount>0).
    #    Surfaces "why was it discounted" — reason_id 0 = untyped (no reason chosen). ──
    sq.executescript("DROP TABLE IF EXISTS pos_discount_month; CREATE TABLE pos_discount_month("
                     "branch_id INT, period_month TEXT, reason_id INT, bills INT, amount REAL);")
    rows = run(f"""SELECT s.BranchId bid, CONVERT(char(7),s.SaleDate,126) ym, ISNULL(s.DiscountReasonId,0) rid,
                 COUNT(*) n, SUM(CAST(s.Discount AS float)) amt
                 FROM Sale s WHERE {SALE_FILTER} AND s.Discount>0 AND s.SaleDate >= %s
                 GROUP BY s.BranchId, CONVERT(char(7),s.SaleDate,126), ISNULL(s.DiscountReasonId,0)""")
    sq.executemany("INSERT INTO pos_discount_month VALUES(?,?,?,?,?)",
        [(bid_map.get(r["bid"]), r["ym"], r["rid"], r["n"], round(r["amt"] or 0,2))
         for r in rows if bid_map.get(r["bid"])])
    print(f"        pos_discount_month: {len(rows)} groups")

    # ── pos_cancel_bill_detail: INDIVIDUAL voided bills (TOP 20000 most recent) so the
    #    UI can drill per-bill to inspect each error. Bounded for size. ──
    # Sale has no dedicated "delete user" column (only DeleteReasonId/Notes/Date), so the
    # best "who" for a voided bill is the login user who owned it: Sale.UserId -> Users.Username.
    # NOTE: individual (per-person) attribution is NOT possible in this POS — the Staff
    # master table is empty and SaleItemStaff.StaffName is blank for all rows, so cancel_user
    # (a shared login/role account) is the only "who" available.
    sq.executescript("DROP TABLE IF EXISTS pos_cancel_bill_detail; CREATE TABLE pos_cancel_bill_detail("
                     "branch_id INT, period_month TEXT, sale_date TEXT, receipt_no TEXT, reason_id INT, amount REAL, cancel_user TEXT, discount REAL);")
    drows = run("""SELECT TOP 20000 s.BranchId bid, CONVERT(char(19),s.SaleDate,126) dt, s.SaleNum num,
                 ISNULL(s.DeleteReasonId,0) rid, CAST(ISNULL(s.Total,0) AS float) amt,
                 CAST(ISNULL(s.Discount,0) AS float) disc,
                 LTRIM(RTRIM(COALESCE(u.Username,''))) duser
                 FROM Sale s LEFT JOIN Users u ON u.Id = s.UserId
                 WHERE ISNULL(s.Active,1)=0 AND s.SaleDate >= %s ORDER BY s.SaleDate DESC""")
    sq.executemany("INSERT INTO pos_cancel_bill_detail VALUES(?,?,?,?,?,?,?,?)",
        [(bid_map.get(r["bid"]), (r["dt"] or '')[:7], r["dt"], str(r["num"]), r["rid"], round(r["amt"] or 0,2),
          (r["duser"] or None), round(r["disc"] or 0,2))
         for r in drows if bid_map.get(r["bid"])])
    sq.execute("CREATE INDEX IF NOT EXISTS idx_cbd_branch ON pos_cancel_bill_detail(branch_id, period_month)")
    print(f"        pos_cancel_bill_detail: {len(drows)} bills")


# (table, month expression) — every table the stagers fill from a month window.
# reconcile() refuses a build whose copy of any of these starts LATER than the
# current DB's: that is the incremental sync dropping history (#113).
HISTORY_TABLES = (
    ("pos_void", "period_month"), ("pos_staff_month", "period_month"),
    ("pos_payment_month", "period_month"), ("pos_payment_day", "substr(day,1,7)"),
    ("pos_coupon_payment_month", "period_month"), ("sale_payment", "substr(receipt_no,1,7)"),
    ("pos_table_turn_day", "substr(day,1,7)"), ("pos_channel_month", "period_month"),
    ("pos_coupon_month", "period_month"), ("pos_kitchen_month", "period_month"),
    ("sale_bill_item", "substr(receipt_no,1,7)"),
)


def reconcile(sq):
    cur = sqlite3.connect(str(DB)); cur.row_factory = sqlite3.Row
    def one(con, q, p=()):
        return con.execute(q, p).fetchone()[0]
    checks = []   # (name, old, new, mode) — mode: 'strict' = |Δ|<2%; 'nodrop' = Δ ≥ -2% (growth OK);
                  #                                  'info'/'note' = printed only, never gates
    # The synced db's latest month is the LIVE current month, which legitimately
    # GROWS between syncs as new days finalize. So we can't strict-compare it —
    # an incremental daily sync would always trip a 2% drift gate mid-month.
    # Strategy:
    #   • SETTLED months (anything before the live month): must match within 2%
    #     in both DBs → catches corruption / history loss.
    #   • LIVE month: only guard against a DROP (data loss); growth is expected.
    syn_max = sq.execute("SELECT MAX(period_month) m FROM sales_sku_monthly").fetchone()["m"]
    cur_max = cur.execute("SELECT MAX(period_month) m FROM sales_sku_monthly").fetchone()["m"]
    # latest SETTLED month = newest month, in the current DB, strictly before the live month
    anchor = cur.execute(
        "SELECT MAX(period_month) m FROM sales_sku_monthly WHERE period_month < ?", (syn_max,)
    ).fetchone()["m"]
    print(f"    (current latest={cur_max} · synced latest={syn_max} · settled-anchor={anchor})")

    # LIVE month — growth expected, only block a drop
    lv_new = one(sq, "SELECT ROUND(SUM(subtotal_ex_vat),0) FROM sales_sku_monthly WHERE period_month=?", (syn_max,)) or 0
    lv_old = one(cur, "SELECT ROUND(SUM(subtotal_ex_vat),0) FROM sales_sku_monthly WHERE period_month=?", (syn_max,)) or 0
    checks.append((f"LIVE rev {syn_max} (ex-VAT, growth OK)", lv_old, lv_new, 'nodrop'))
    lvb_new = one(sq, "SELECT COUNT(*) FROM sales_transaction WHERE substr(sold_at,1,7)=?", (syn_max,))
    lvb_old = one(cur, "SELECT COUNT(*) FROM sales_transaction WHERE substr(sold_at,1,7)=?", (syn_max,))
    checks.append((f"LIVE bills {syn_max} (growth OK)", lvb_old, lvb_new, 'nodrop'))

    # SETTLED month anchor — strict integrity check (should be identical run-to-run).
    # BUT restrict the strict comparison to branches that ALREADY had data in the
    # CURRENT db for the anchor month ("shared" branches). A newly-mapped branch
    # (e.g. a fixed BRANCH_ALIAS bringing back a branch that sync_mssql had been
    # silently dropping into skipped_rev — 2026-06: ขอนแก่น + 8 others) legitimately
    # ADDS revenue to a settled month; that is not corruption and must not gate.
    # The shared-branch subset still strict-catches real history loss / drift, and a
    # branch that DISAPPEARS (was in current, now gone) drops the subset → fails.
    if anchor:
        existing_bids = [r[0] for r in cur.execute(
            "SELECT DISTINCT branch_id FROM sales_sku_monthly WHERE period_month=?", (anchor,)).fetchall()]
        bid_in = "(" + ",".join(str(int(b)) for b in existing_bids) + ")" if existing_bids else "(-1)"
        a_new = one(sq, f"SELECT ROUND(SUM(subtotal_ex_vat),0) FROM sales_sku_monthly WHERE period_month=? AND branch_id IN {bid_in}", (anchor,)) or 0
        a_old = one(cur, f"SELECT ROUND(SUM(subtotal_ex_vat),0) FROM sales_sku_monthly WHERE period_month=? AND branch_id IN {bid_in}", (anchor,)) or 0
        # A settled month is only truly FROZEN if the current DB was captured after
        # that month had ended — i.e. it already holds a later month. When the anchor
        # IS the current DB's newest month, that snapshot was taken while the month
        # was still filling: HQ delivers each day's bills about a day late, so the
        # month legitimately GROWS on the next sync and a strict two-sided gate blocks
        # every run in the first days of a new month. Seen 2026-08-03: settled 2026-07
        # was +3.44%, and ALL of it was 2026-07-31 alone (60 bills captured on 08-01 →
        # 1,716 delivered), every other July day byte-identical, zero duplicate
        # (branch, receipt) keys. A DROP still fails — that is the direction that means
        # history loss.
        tail_open = (anchor == cur_max)
        a_mode = 'nodrop' if tail_open else 'strict'
        if tail_open:
            print(f"    [tail-open] current DB's newest month IS the settled anchor {anchor} → it was "
                  f"captured mid-fill, so growth is expected (late HQ delivery); only a DROP gates")
        checks.append((f"SETTLED rev {anchor} (shared branches)", a_old, a_new, a_mode))
        ab_new = one(sq, f"SELECT COUNT(*) FROM sales_transaction t JOIN branches b ON b.branch_id=t.branch_id WHERE substr(t.sold_at,1,7)=? AND t.branch_id IN {bid_in}", (anchor,))
        ab_old = one(cur, f"SELECT COUNT(*) FROM sales_transaction t JOIN branches b ON b.branch_id=t.branch_id WHERE substr(t.sold_at,1,7)=? AND t.branch_id IN {bid_in}", (anchor,))
        checks.append((f"SETTLED bills {anchor} (shared branches)", ab_old, ab_new, a_mode))
        # Relaxing the anchor must never leave the sync without a two-sided canary, so
        # when it is relaxed, strict-check the month before it — that one is frozen by
        # construction (the current DB holds two later months).
        if tail_open:
            frozen = cur.execute(
                "SELECT MAX(period_month) m FROM sales_sku_monthly WHERE period_month < ?", (anchor,)
            ).fetchone()["m"]
            if frozen:
                fz_bids = [r[0] for r in cur.execute(
                    "SELECT DISTINCT branch_id FROM sales_sku_monthly WHERE period_month=?", (frozen,)).fetchall()]
                fz_in = "(" + ",".join(str(int(b)) for b in fz_bids) + ")" if fz_bids else "(-1)"
                f_new = one(sq, f"SELECT ROUND(SUM(subtotal_ex_vat),0) FROM sales_sku_monthly WHERE period_month=? AND branch_id IN {fz_in}", (frozen,)) or 0
                f_old = one(cur, f"SELECT ROUND(SUM(subtotal_ex_vat),0) FROM sales_sku_monthly WHERE period_month=? AND branch_id IN {fz_in}", (frozen,)) or 0
                checks.append((f"FROZEN rev {frozen} (shared branches)", f_old, f_new, 'strict'))
        # Revenue from branches NEW to this settled month (present in synced, absent
        # from current) — reported for visibility; never gates. A large value here is
        # expected right after a BRANCH_ALIAS fix and disappears on the next sync.
        added_rev = one(sq, f"SELECT ROUND(SUM(subtotal_ex_vat),0) FROM sales_sku_monthly WHERE period_month=? AND branch_id NOT IN {bid_in}", (anchor,)) or 0
        if added_rev:
            added_names = [r["name"] for r in sq.execute(
                f"SELECT DISTINCT b.name FROM sales_sku_monthly m JOIN branches b ON b.branch_id=m.branch_id "
                f"WHERE m.period_month=? AND m.branch_id NOT IN {bid_in} AND b.name!='ALL'", (anchor,)).fetchall()]
            print(f"    [branch-add] settled {anchor}: +฿{added_rev:,.0f} from {len(added_names)} newly-mapped "
                  f"branch(es): {', '.join(added_names[:12])}")
            checks.append((f"SETTLED rev {anchor} ADDED branches", 0, added_rev, 'note'))

    # Ubon Apr 2026 receipts ex-VAT — fixed historical anchor (must never move)
    ub_new = one(sq, "SELECT ROUND(SUM(total_inc_vat)/1.07,0) FROM sales_transaction t JOIN branches b ON b.branch_id=t.branch_id WHERE b.name='อุบลราชธานี' AND substr(t.sold_at,1,7)='2026-04'") or 0
    ub_old = one(cur, "SELECT ROUND(SUM(total_inc_vat)/1.07,0) FROM sales_transaction t JOIN branches b ON b.branch_id=t.branch_id WHERE b.name='อุบลราชธานี' AND substr(t.sold_at,1,7)='2026-04'") or 0
    checks.append(("Ubon Apr2026 ex-VAT (receipts)", ub_old, ub_new, 'strict'))
    # buffet (B) revenue should be ~0 (informational)
    buf_new = one(sq, "SELECT ROUND(SUM(subtotal_ex_vat),0) FROM sales_sku_monthly m JOIN skus s ON s.sku_id=m.sku_id WHERE s.description LIKE '(B)%' AND m.period_month=?", (syn_max,)) or 0
    checks.append(("buffet (B) rev (should ≈0)", 0, buf_new, 'info'))

    # Staged history must never SHRINK. An incremental run re-pulls only the
    # window, so every windowed table must still start where the current DB's
    # copy starts — #113 (2026-09-04): each stager dropped its table and Fly served
    # pos_* / sale_payment / sale_bill_item from July only, for two days, with every
    # numeric check above green. Growth (a backfill) is fine; a later first month
    # is history loss and gates like a strict check. A table absent from either DB
    # is skipped (older DB, or a table the sync has not written yet).
    history = []   # (table, current first month, synced first month, passed)
    for tbl, col in HISTORY_TABLES:
        have = {k for k, con in (("cur", cur), ("new", sq))
                if con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (tbl,)).fetchone()}
        if have != {"cur", "new"}:
            continue
        c_min = one(cur, f"SELECT MIN({col}) FROM {tbl}")
        n_min = one(sq, f"SELECT MIN({col}) FROM {tbl}")
        passed = not c_min or (n_min is not None and n_min <= c_min)
        history.append((tbl, c_min, n_min, passed))

    ok = True
    print("    " + "-"*82)
    print(f"    {'check':42} {'current':>15} {'synced':>15}  Δ%")
    for name, old, new, mode in checks:
        d = (new-old)/old*100 if old else (0 if not new else 999)
        if mode == 'strict':
            passed = abs(d) < 2.0
        elif mode == 'nodrop':
            passed = d >= -2.0          # growth is fine; only a real drop fails
        elif mode == 'note':            # added-branch revenue — informational, always shown ✓
            passed = True
        else:  # info — buffet near-zero, never gates
            passed = (abs(new) < (lv_old or 1) * 0.002)
        if mode in ('strict', 'nodrop') and not passed:
            ok = False
        print(f"    {name:42} {old:>15,.0f} {new:>15,.0f}  {d:+6.2f}%{'  ✓' if passed else '  ⚠'}")
    for tbl, c_min, n_min, passed in history:
        print(f"    {'HISTORY ' + tbl + ' starts':42} {str(c_min):>15} {str(n_min):>15}  "
              f"{'      ' if passed else 'LOST  '}{'  ✓' if passed else '  ⚠'}")
        if not passed:
            ok = False
    print("    " + "-"*82)
    cur.close()
    return ok

if __name__ == "__main__":
    main()
