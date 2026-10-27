"""
Revenue Reconciliation API
Second service in the same Fly app as the main dashboard.
Shares /data/oshinei.db via the shared Fly volume.

Run locally:
    uvicorn recon.main:app --port 8001 --reload

Environment:
    OSHINEI_DB   shared database path  (default: /data/oshinei.db)
"""

from __future__ import annotations
import os
import re
import sqlite3
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .seed_payment_map import seed_payment_map
from . import auth as _auth
# Everything more than one endpoint family shares lives in core.py (see its
# docstring). Every name is re-exported here so main.X keeps resolving; to point
# the app at another database assign recon.core._DB_PATH — main._DB_PATH is a copy.
from .core import (  # noqa: F401  — main is the facade: every core name, re-exported
    _parse_session, _SESSION_COOKIE, _AUTH_IMPORT_ERR, _DB_PATH, _SCHEMA, _ATTACH_DIR,
    _SNAPSHOT_DIR, get_db, _has_table, _POS_DAY_UNAVAILABLE, _METHOD_DEFAULTS, _seed_method_defaults,
    _REQUIREMENT_CODES, _SEED_AUTHORS, _seed_requirement_codes, _MASTER_ONLY, RECON_START_MONTH, RECON_START_DAY,
    _PERIOD_RE, _accepted_tokens, require_write_token, _require_manager, require_manager_token, _actor,
    _validate_period, _validate_master_branch, _assert_period_open, _CATEGORIES, _BANKS, _BANK_CH_TO_CAT,
    _PARSE_CH_TH, _statement_code_overrides, _override_channel, _statement_codes, _audit, _DAY_RE,
    _m, _MATCH_CH_TH, _account_map, _FEE_CH_SPEC, _FINANCE_CH_ORDER, _daily_finance_data,
)


# ── Setup ─────────────────────────────────────────────────────────────────────


__all__ = [
    "RECON_START_DAY",
    "RECON_START_MONTH",
    "_ATTACH_DIR",
    "_BANKS",
    "_BANK_CH_TO_CAT",
    "_CATEGORIES",
    "_DAY_RE",
    "_DB_PATH",
    "_FEE_CH_SPEC",
    "_FINANCE_CH_ORDER",
    "_MASTER_ONLY",
    "_MATCH_CH_TH",
    "_METHOD_DEFAULTS",
    "_PARSE_CH_TH",
    "_PERIOD_RE",
    "_POS_DAY_UNAVAILABLE",
    "_REQUIREMENT_CODES",
    "_SCHEMA",
    "_SEED_AUTHORS",
    "_SNAPSHOT_DIR",
    "_accepted_tokens",
    "_account_map",
    "_actor",
    "_assert_period_open",
    "_audit",
    "_daily_finance_data",
    "_has_table",
    "_m",
    "_override_channel",
    "_require_manager",
    "_seed_method_defaults",
    "_seed_requirement_codes",
    "_statement_code_overrides",
    "_statement_codes",
    "_validate_master_branch",
    "_validate_period",
    "get_db",
    "require_manager_token",
    "require_write_token",
    "_AUTH_IMPORT_ERR",
    "_SESSION_COOKIE",
    "_detect_branch_from_rows",
    "_infer_source_type",
    "_monthly_finance_html",
    "_parse_session",
]

app = FastAPI(title="Oshinei Revenue Reconciliation", version="1.0.0")
# The frontend is served same-origin (/app on this service), so it needs no CORS
# grant; restrict cross-origin reads to the known hosts instead of a blanket '*'.
# Override with OSHINEI_CORS_ORIGINS (comma-separated) if another origin must call in.
_CORS_ORIGINS = [o.strip() for o in os.getenv(
    "OSHINEI_CORS_ORIGINS",
    "https://oshinei-dashboard.fly.dev,https://oshinei-dashboard.fly.dev:8443,"
    "http://localhost:8001,http://127.0.0.1:8001,http://localhost:8013,http://127.0.0.1:8013"
).split(",") if o.strip()]
app.add_middleware(CORSMiddleware, allow_origins=_CORS_ORIGINS, allow_methods=["*"], allow_headers=["*"])


@app.middleware("http")
async def _frontend_no_cache(request, call_next):
    """The frontend (index.html + its js/*.js slices, the ui-kit stylesheets and
    vendor files) is served by StaticFiles, which sends only last-modified/etag
    (no Cache-Control). Browsers then apply heuristic caching and may serve a
    stale build even after a refresh. Force revalidation on EVERYTHING under /app
    (not just index.html: a fresh page with a cached old script is the worst
    case) so a fresh deploy is visible immediately (the etag still yields a cheap
    304 when nothing changed)."""
    resp = await call_next(request)
    if request.url.path.startswith("/app"):
        resp.headers["Cache-Control"] = "no-cache, must-revalidate"
    return resp


# Idempotent column adds for DBs created before a schema column existed
# (CREATE TABLE IF NOT EXISTS never alters an existing table).
_MIGRATIONS = {
    "recon_bank_lines":    [("account_no", "TEXT")],
    "recon_cc_lines":      [("acquirer", "TEXT"), ("fee_manual", "REAL"), ("vat_manual", "REAL"),
                            ("fee_vat_by", "TEXT"), ("fee_vat_at", "TEXT"), ("vat_amount", "REAL")],
    "recon_grab_lines":    [("fee_manual", "REAL"), ("vat_manual", "REAL"),
                            ("fee_vat_by", "TEXT"), ("fee_vat_at", "TEXT"), ("vat_amount", "REAL")],
    "recon_lineman_lines": [("fee_manual", "REAL"), ("vat_manual", "REAL"),
                            ("fee_vat_by", "TEXT"), ("fee_vat_at", "TEXT")],
    "recon_discrepancies": [("category", "TEXT"), ("bank", "TEXT"), ("assigned_to", "TEXT")],
    "recon_uploads":       [("file_hash", "TEXT")],
    # Ties the frozen working paper to the exact source documents (name + sha256) that
    # produced it, so a later upload delete/replace can't silently break the chain.
    "recon_close_snapshot": [("source_manifest", "TEXT")],
    # The sale day a channel MOVE corrects (NULL on older rows → last POS day of the lane).
    "recon_channel_move":  [("day", "TEXT")],
}


def _migrate(conn):
    for table, cols in _MIGRATIONS.items():
        try:
            existing = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        except sqlite3.OperationalError:
            continue
        if not existing:
            continue
        for name, decl in cols:
            if name not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")


def _migrate_payment_bank(conn):
    """Widen recon_payment_map.bank to allow BAY/SCB/BBL + 'ANY' (multi-bank
    codes like the generic 'Bank Transfer' — the CHECK constraint can't be
    ALTERed in SQLite → recreate the table once, preserving rows). Then
    future-proof the franchise-only 'Credit BAY' tender (seed_payment_map is
    master-scoped so it never picks it up)."""
    try:
        row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='recon_payment_map'").fetchone()
        if row and any(f"'{b}'" not in (row[0] or "") for b in ("BAY", "ANY", "TTB")):
            # Explicit transaction (NOT executescript): a CHECK failure mid-script
            # must roll the whole recreate back, never leave the table half-gone.
            # (Live data taught us this — it contained bank='TTB'.)
            conn.commit()
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.execute("""
                    CREATE TABLE recon_payment_map__new (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        payment_code_norm TEXT NOT NULL UNIQUE,
                        category TEXT NOT NULL CHECK(category IN
                            ('CASH','QR','TRANSFER','CREDIT','GRAB','LINEMAN','COUPON','ENTERTAIN','OTHER')),
                        bank TEXT NOT NULL DEFAULT 'NONE' CHECK(bank IN
                            ('KTB','KBANK','KTC','BAY','SCB','BBL','TTB','PROMPTPAY','ANY','NONE','UNKNOWN')),
                        reconcile INTEGER NOT NULL DEFAULT 1,
                        notes TEXT, updated_at TEXT DEFAULT (datetime('now')), updated_by TEXT
                    )""")
                conn.execute("""
                    INSERT INTO recon_payment_map__new
                        (id,payment_code_norm,category,bank,reconcile,notes,updated_at,updated_by)
                        SELECT id,payment_code_norm,category,bank,reconcile,notes,updated_at,updated_by
                        FROM recon_payment_map""")
                conn.execute("DROP TABLE recon_payment_map")
                conn.execute("ALTER TABLE recon_payment_map__new RENAME TO recon_payment_map")
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
        conn.execute("""INSERT INTO recon_payment_map (payment_code_norm,category,bank,reconcile,updated_by)
            VALUES ('Credit BAY','CREDIT','BAY',1,'seed-franchise')
            ON CONFLICT(payment_code_norm) DO NOTHING""")
        # widen recon_acquirer_mid.acquirer to allow BAY/SCB/BBL + point acquirers
        arow = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='recon_acquirer_mid'").fetchone()
        if arow and "KTCPOINT" not in (arow[0] or ""):
            conn.executescript("""
                CREATE TABLE recon_acquirer_mid__new (
                    mid_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    branch_id INTEGER NOT NULL REFERENCES branches(branch_id),
                    acquirer TEXT NOT NULL CHECK(acquirer IN
                        ('KTB','KBANK','KTC','BAY','SCB','BBL','KTCPOINT','BAYPOINT')),
                    merchant_id TEXT, terminal_id TEXT, active INTEGER NOT NULL DEFAULT 1
                );
                INSERT INTO recon_acquirer_mid__new (mid_id,branch_id,acquirer,merchant_id,terminal_id,active)
                    SELECT mid_id,branch_id,acquirer,merchant_id,terminal_id,active FROM recon_acquirer_mid;
                DROP TABLE recon_acquirer_mid;
                ALTER TABLE recon_acquirer_mid__new RENAME TO recon_acquirer_mid;
            """)
    except sqlite3.OperationalError:
        pass


def _migrate_lane_adj_accept(conn):
    """Widen recon_lane_adj.adj_type to allow 'accept' (v2 acknowledged
    variance). SQLite can't ALTER a CHECK → recreate once, preserving rows."""
    try:
        row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='recon_lane_adj'").fetchone()
        if row and "'accept'" not in (row[0] or ""):
            conn.executescript("""
                CREATE TABLE recon_lane_adj__new (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    branch_id    INTEGER NOT NULL REFERENCES branches(branch_id),
                    period_month TEXT    NOT NULL,
                    category     TEXT    NOT NULL,
                    bank         TEXT    NOT NULL DEFAULT 'NONE',
                    adj_pos      REAL    NOT NULL DEFAULT 0,
                    adj_ext      REAL    NOT NULL DEFAULT 0,
                    note         TEXT,
                    adj_type     TEXT    NOT NULL DEFAULT 'other'
                                     CHECK(adj_type IN ('off_pos','deposit_note','other','accept')),
                    created_by   TEXT,
                    created_at   TEXT    DEFAULT (datetime('now')),
                    UNIQUE(branch_id, period_month, category, bank)
                );
                INSERT INTO recon_lane_adj__new
                    (id,branch_id,period_month,category,bank,adj_pos,adj_ext,note,adj_type,created_by,created_at)
                    SELECT id,branch_id,period_month,category,bank,adj_pos,adj_ext,note,adj_type,created_by,created_at
                    FROM recon_lane_adj;
                DROP TABLE recon_lane_adj;
                ALTER TABLE recon_lane_adj__new RENAME TO recon_lane_adj;
                CREATE INDEX IF NOT EXISTS idx_rla_scope ON recon_lane_adj(branch_id, period_month);
            """)
    except sqlite3.OperationalError:
        pass


def _migrate_attachment_to_fs(conn):
    """Replace recon_attachment.data (BLOB) with file_path (TEXT).
    Files uploaded before this migration are lost (acceptable — feature was unused).
    Idempotent: skips if file_path already present."""
    try:
        existing = {r[1] for r in conn.execute("PRAGMA table_info(recon_attachment)")}
    except sqlite3.OperationalError:
        return
    if "file_path" in existing:
        return  # already migrated
    conn.executescript("""
        CREATE TABLE recon_attachment__new (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            branch_id   INTEGER NOT NULL,
            day         TEXT NOT NULL,
            category    TEXT,
            file_name   TEXT NOT NULL,
            mime        TEXT,
            size_bytes  INTEGER,
            file_path   TEXT,
            note        TEXT,
            uploaded_by TEXT,
            uploaded_at TEXT
        );
        INSERT INTO recon_attachment__new
            (id,branch_id,day,category,file_name,mime,size_bytes,note,uploaded_by,uploaded_at)
            SELECT id,branch_id,day,category,file_name,mime,size_bytes,note,uploaded_by,uploaded_at
            FROM recon_attachment;
        DROP TABLE recon_attachment;
        ALTER TABLE recon_attachment__new RENAME TO recon_attachment;
        CREATE INDEX IF NOT EXISTS idx_recon_attachment ON recon_attachment(branch_id, day);
    """)


# ── Requirement-table seeds (owner's "ตารางจับคู่รหัสรับเงิน", 2026-06) ─────────
# Both seeds are idempotent and NEVER overwrite a staff edit (guarded on
# updated_by/ON CONFLICT DO NOTHING). Re-runnable from /api/setup/seed-requirement.


@app.on_event("startup")
def startup():
    _ATTACH_DIR.mkdir(parents=True, exist_ok=True)
    with get_db() as conn:
        conn.executescript(_SCHEMA.read_text(encoding="utf-8"))
        _migrate(conn)
        _migrate_payment_bank(conn)   # widen bank CHECK + future-proof Credit BAY
        _migrate_lane_adj_accept(conn)  # widen adj_type CHECK for v2 'accept'
        _migrate_attachment_to_fs(conn)
        # recon_lane_adj: created by schema.sql executescript above (IF NOT EXISTS)
        seed_payment_map(conn)   # idempotent; never overwrites staff edits
        _seed_method_defaults(conn)     # owner's per-lane method table
        _seed_requirement_codes(conn)   # owner's 12-code routing answers
        # recon login: first manager from RECON_ADMIN_USER/PASSWORD while recon_users is empty
        boot = _auth.bootstrap_admin(conn)
        if boot:
            _audit(conn, "recon_user", 0, "bootstrap", "bootstrap", [{"field": "username", "new": boot}])
        conn.commit()


# ── Static frontend ───────────────────────────────────────────────────────────

_FRONTEND = Path(__file__).parent / "frontend"
# The dashboard's shared design kit (docs/ui-kit: tokens.css = the single source
# of colour/radius/shadow tokens, theme-groups.css = group hues). The frontend
# links it as ui-kit/… so recon wears the same tokens, light and dark. Mounted
# BEFORE /app so the /app StaticFiles mount does not shadow it; read-only, and
# skipped when the folder is absent (the page then falls back to its own values).
_UI_KIT = Path(__file__).resolve().parent.parent / "docs" / "ui-kit"
if _UI_KIT.is_dir():
    app.mount("/app/ui-kit", StaticFiles(directory=str(_UI_KIT)), name="ui-kit")
if _FRONTEND.exists():
    app.mount("/app", StaticFiles(directory=str(_FRONTEND), html=True), name="frontend")


# ── Branches (read from shared branches table) ────────────────────────────────
# Reconciliation is scoped to MASTER (company-owned) branches only; franchises
# are out of scope. COALESCE keeps legacy NULL rows (default 'master') in scope.


# ── Write auth + upload validation ────────────────────────────────────────────
# Mirrors the dashboard's gate: when OSHINEI_SEED_TOKEN is set (hosted deploys) a
# matching token is required in the `X-Upload-Token` header (never `?token=`). Unset
# locally → writes are open, so the gate is invisible in dev.


# ── Trusted actor identity (control gap fix 2026-06-19) ───────────────────────
# Recon shares the machine + DB + signing secret with the dashboard and answers on
# the SAME domain (different port → cookies are not port-scoped), so the dashboard's
# signed session cookie reaches us too. Derive the acting username from it SERVER-SIDE
# instead of trusting the client-supplied created_by/matched_by/approved_by — that
# makes the audit "by" tamper-resistant when login is in use. Falls back to the
# client value (then "staff") when there is no valid session, so open/local mode and
# the test suite are unchanged.
# (session parsing and its standalone fallback live in core.py — imported above)


# ── Config: payment-code map ──────────────────────────────────────────────────


# ── Config: per-lane method parameters (advanced setup — manager token) ───────


# ── Advanced setup: composed master view + requirement reseed ─────────────────


# ── Config: bank accounts ─────────────────────────────────────────────────────


# ── Config: credit-card acquirer MIDs ─────────────────────────────────────────


# ── Uploads ───────────────────────────────────────────────────────────────────


# ── Bulk upload (B1): many files / whole folder / zips in one pass ─────────────


# ── Deposit slips (สลิปนำฝาก — CASH stage-1 evidence, typed by staff) ──────────


# ── Sessions ──────────────────────────────────────────────────────────────────


# ── Session summary + category reconciliation ─────────────────────────────────


# ── Reconcile v2 — channel-first composed view (no session required) ──────────


# ── Excel month-pack export ───────────────────────────────────────────────────


# ── Immutable close snapshot (AUD-5) ──────────────────────────────────────────


# ── Branch / staff error report ───────────────────────────────────────────────
# Built from the acknowledged-variance markers (รับทราบ / recon_lane_adj accepts):
# each is a recorded "this lane was off and here's why", with who + when. Aggregated
# per branch (+month) it shows which branch mis-records most often + the baht at
# stake — a fair, evidence-based input to staff evaluation.


# ── Wrong-channel mis-entry report (รายงานเงินบันทึกผิดช่อง) ────────────────────
# A "เงินอาจถูกบันทึกผิดช่อง" suggestion, once confirmed, becomes a manual match:
#   • category      = the POS lane the cashier rang the money into BY MISTAKE
#   • matched lines = the statement entries showing where the money ACTUALLY landed
# So every such match is a logged correction (with reason + who + when). We surface
# them per branch as a สรุป + an acknowledgment sheet the branch signs and fixes.


# ── Statement-code dictionary (รหัสในสเตทเมนต์ → ความหมาย → ช่องทาง) ────────────
# Surfaces parsers.bank.STATEMENT_CODE_MAP (the single source the parser uses) so the
# team can review every abbreviation and flag wrong meanings (the SDCH=cash case).


# ── Reclass-pattern guard (catch a misclassified statement code early) ─────────
# The SDCH bug showed up as a flood of identical wrong-channel suggestions: same
# from→to, same day-gap, cross-bank, one direction — a structural fingerprint, not
# scattered human error. This diagnostic clusters live suggestions and flags those
# that look systematic, so the next mis-mapped code is caught before staff "confirm".


# ── Discrepancies ─────────────────────────────────────────────────────────────


# ── Manual fee / VAT per line (Grab / Line Man / Credit Card) ──────────────────


# ── Matches (reconciled category buckets from the last run-match) ─────────────


# ── Cross-branch daily board ──────────────────────────────────────────────────


# ── Monthly matching audit (รายงานการจับคู่รายวันทั้งเดือน) ────────────────────
# One printable sheet per branch: every day × channel with POS vs document, the
# variance, and the match status — so the owner/auditor can confirm the whole month
# reconciled correctly in one pass. Deep line-level evidence stays in /api/daily-report.


# ── Daily accounting summary, one card per day (สรุปบัญชีรายวัน รายใบ) ──────────
# Stand-alone section: the same per-day GROSS / discount-on-its-own-line breakdown that
# the monthly report carries, but pulled out as its own browsable view — each day is a
# card ('1 วัน = 1 ใบ'), one branch at a time, with a print-to-PDF version.


# ── Outstanding-documents follow-up (รายงานติดตามเอกสารที่ขาด รายสาขา/เดือน) ────
# "Which documents does each branch still need to upload this month, and how much
# money is waiting on each?" — works for ANY period so the team can chase month-to-month.


# ════════════════════════════════════════════════════════════════════════════
#  Internal-control reports (Big4 audit evidence) — all READ-ONLY.
#  #1 Management-override register · #2 Reconciliation sign-off certificate ·
#  #3 Open-items aging.  None of these mutate state or touch accounting/recon
#  logic; they only READ the existing reversible-adjustment tables and
#  reconcile_month(). Master-branch (company-owned) scope only.
# ════════════════════════════════════════════════════════════════════════════


# ── #4 Close register + #5 Accepted-variance summary (audit evidence) ─────────


# ── Optional login enforcement (opt-in via RECON_ENFORCE_AUTH) ────────────────
# Recon shares the dashboard's signed session cookie (see _actor). When this flag is
# set, WRITE requests must carry a valid session — making the actor identity provable,
# not just best-effort. DEFAULT OFF: unset → unchanged (open/token-gated), so nothing
# breaks until the owner turns it on (login lives on the dashboard app).
# With the flag on, the reads that hand out evidence wholesale (control reports, the
# audit log, month packs, working-paper/snapshot/attachment downloads, printable
# evidence) need a session too. The frontend shell (/app), /health and the working
# GETs the screens load stay open. No role/branch gating here — RBAC is the dashboard's.
_SENSITIVE_GET = re.compile(
    r"^/api/(reports/|audit|monthly-report|daily-report|daily-accounts\.print"
    r"|sessions/\d+/export\.xlsx$|snapshots/\d+/file$|attachments/\d+/file$)")


# ── Recon's own login (RECON_LOGIN=1, recon/auth.py — owner decision 2026-10-06) ──
# When the flag is ON it REPLACES the RECON_ENFORCE_AUTH path above: the dashboard
# cookie no longer counts; every /api call needs a recon session, except
#   • /api/me, /api/auth/login, /api/auth/logout (the SPA must reach its login screen;
#     /app/* static and /health are outside /api and stay open);
#   • a session-less POST to the upload endpoints (/api/uploads/<type>/confirm and
#     /api/uploads/bulk) carrying a valid X-Upload-Token — the bulk-upload script.
#     Nothing else: matching, acks, sign-off, deposit slips and config need a recon
#     session (2026-10 audit; before, the token alone passed every non-manager write).
#     The endpoint's require_write_token re-checks both the token and the path.
# Roles: viewer → every POST/PATCH/PUT/DELETE 403; accountant → work actions, not
# approve/close/reopen nor the manager setup (core._require_manager /
# require_manager_token check the role) nor /api/users; manager → everything.
# A missing RECON_SESSION_SECRET with the flag on → 503 for all of /api: fail CLOSED.
_LOGIN_OPEN = {"/api/me", "/api/auth/login", "/api/auth/logout"}
_SELF_SERVICE = {"/api/me", "/api/auth/logout", "/api/auth/change-password"}
_MUTATING = ("POST", "PATCH", "PUT", "DELETE")


async def _recon_login_gate(request: Request):
    """None = let the request through; else the refusal response."""
    from fastapi.responses import JSONResponse
    from starlette.concurrency import run_in_threadpool
    path = request.url.path
    if not path.startswith("/api/") or path in _LOGIN_OPEN:
        return None
    if not _auth.secret_ok():
        return JSONResponse({"detail": _auth.MSG_NO_SECRET}, status_code=503)
    user = await run_in_threadpool(_auth.current_user, request)
    if user is None:
        if _auth.machine_path_ok(request) and _auth.machine_token_label(request):
            return None                                     # bulk uploader: upload POSTs only
        return JSONResponse({"detail": _auth.MSG_NEED_LOGIN}, status_code=401)
    if user["must_change_pw"] and path not in _SELF_SERVICE:
        return JSONResponse({"detail": _auth.MSG_MUST_CHANGE}, status_code=403)
    if user["role"] == "viewer" and request.method in _MUTATING and path not in _SELF_SERVICE:
        return JSONResponse({"detail": _auth.MSG_VIEWER}, status_code=403)
    if path.startswith("/api/users") and user["role"] != "manager":
        return JSONResponse({"detail": _auth.MSG_MANAGER_ONLY}, status_code=403)
    return None


@app.middleware("http")
async def _enforce_recon_session(request: Request, call_next):
    path = request.url.path
    if _auth.login_enabled():
        refused = await _recon_login_gate(request)
        if refused is not None:
            return refused
        return await call_next(request)
    # RECON_LOGIN off → the RECON_ENFORCE_AUTH behaviour exactly as before. The new
    # /api/auth/* and /api/users endpoints are exempt: they check a RECON session
    # themselves (so the owner can sign in and create accounts before switching on).
    if (os.environ.get("RECON_ENFORCE_AUTH", "").strip()
            and path.startswith("/api/")
            and not path.startswith(("/api/auth/", "/api/users"))
            and (request.method in ("POST", "PATCH", "PUT", "DELETE")
                 or (request.method in ("GET", "HEAD") and _SENSITIVE_GET.match(path)))):
        from fastapi.responses import JSONResponse
        if _parse_session is None:
            # Fail CLOSED: the flag promises a login gate; without backend.auth there
            # is no way to check one (see /api/_diag/auth-wiring for why it's missing).
            return JSONResponse(
                {"detail": "ระบบตรวจสอบการเข้าสู่ระบบไม่พร้อม — ติดต่อผู้ดูแล"}, status_code=503)
        try:
            ok = bool(_parse_session(request.cookies.get(_SESSION_COOKIE, "") or ""))
        except Exception:
            ok = False
        if not ok:
            return JSONResponse(
                {"detail": "ต้องเข้าสู่ระบบก่อนทำรายการ (ล็อกอินที่หน้าหลัก)"}, status_code=401)
    return await call_next(request)


# ── UAT sign-off sheet (เอกสารทดสอบ + รับรองการใช้งาน) ─────────────────────────
# Printable acceptance-test checklist with a usage narrative beside each case, for the
# team to walk through, mark pass/fail, and sign off before going fully live.


# ── Transaction-level drill-down (mapped / unmapped codes + statement lines) ───


# ── Manual reconciliation (tick-to-match, reversible) ──────────────────────────


# ── Lane adjustments (off-POS revenue / manual deposit entries) ──────────────


# ── Manual cross-channel MOVE (booked-in-wrong-channel correction) ───────────
# Declare that ฿amount was rung up under the WRONG channel and actually belongs in
# another; at read-time the move shifts the POS base OUT of (from) and INTO (to),
# correcting BOTH lanes at once (กระทบสองยอดพร้อมกัน). Reversible by deleting the
# row. The shift itself lives in matcher.channel_move_deltas.


# ── Per-day acknowledged variances (reconcile BY DATE) ───────────────────────
# Staff work a channel's variance day-by-day: each differing day is either
# fixed/matched or acknowledged here with a reason. The channel's todo clears once
# every differing day is acknowledged (or the whole channel is accepted via
# recon_lane_adj). The reason rides into the close Excel pack + all-errors report.


# ── Chart-of-accounts map (report account code/name per channel / journal slot) ──
# Reports render these columns blank until the owner fills them in here (no deploy).


# ── Cross-branch overview ─────────────────────────────────────────────────────


# ── Session trend (per-day + weekly rollup) ───────────────────────────────────


# ── Audit log ─────────────────────────────────────────────────────────────────


# ── Diagnostic endpoints ──────────────────────────────────────────────────────
# Use these to inspect what's actually in the database before tuning matchers.


# ── Health ────────────────────────────────────────────────────────────────────


@app.middleware("http")
async def _nosniff(request, call_next):
    """Registered LAST so it is the OUTERMOST middleware and also covers the
    login gate's early refusals. X-Content-Type-Options: nosniff on every response, so a browser never
    second-guesses a declared type (an uploaded file must not render as HTML)."""
    resp = await call_next(request)
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    return resp


# ── Routers by family (Phase 7c, 2026-09) ─────────────────────────────────
# Every endpoint lives in recon/routers/<family>.py with the helpers only it
# uses; what more than one family needs is in core.py. Registered last, after
# app, its middlewares and the startup hook above.
from .routers.diagnostics import router as _diagnostics_router  # noqa: E402
from .routers.config import router as _config_router  # noqa: E402
from .routers.uploads import router as _uploads_router  # noqa: E402
from .routers.uploads import _detect_branch_from_rows  # noqa: E402,F401  (tests reach it as main._detect_branch_from_rows)
from .routers.sessions import router as _sessions_router  # noqa: E402
from .routers.matching import router as _matching_router  # noqa: E402
from .routers.reports import router as _reports_router  # noqa: E402
from .routers.daily import router as _daily_router  # noqa: E402
from .routers.auth import router as _auth_router  # noqa: E402
from .routers.daily import _monthly_finance_html  # noqa: E402,F401  (tests reach it as main._monthly_finance_html)
app.include_router(_diagnostics_router)
app.include_router(_config_router)
app.include_router(_uploads_router)
app.include_router(_sessions_router)
app.include_router(_matching_router)
app.include_router(_reports_router)
app.include_router(_daily_router)
app.include_router(_auth_router)
from .routers.uploads import _infer_source_type  # noqa: E402,F401  (tests reach it as main._infer_source_type)
