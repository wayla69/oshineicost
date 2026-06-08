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
import hashlib
import hmac
import json
import os
import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from datetime import datetime as _dt, timedelta as _td
from typing import Literal

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .matcher import (
    _eff_fee_vat,
    category_reconciliation,
    daily_board,
    daily_reconciliation,
    lane_ext_lines,
    lane_lines,
    lane_pos_days,
    payout_reconciliation,
    pos_codes_scope,
    run_auto_match,
)
from .seed_payment_map import seed_payment_map
from .parsers import (
    parse_bank_statement,
    parse_cc_settlement,
    parse_grab_report,
    parse_lineman_report,
)

# ── Setup ─────────────────────────────────────────────────────────────────────

_DB_PATH   = Path(os.getenv("OSHINEI_DB", "/data/oshinei.db"))
_SCHEMA    = Path(__file__).parent / "schema.sql"

app = FastAPI(title="Oshinei Revenue Reconciliation", version="1.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@contextmanager
def get_db():
    conn = sqlite3.connect(_DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
    finally:
        conn.close()


# Idempotent column adds for DBs created before a schema column existed
# (CREATE TABLE IF NOT EXISTS never alters an existing table).
_MIGRATIONS = {
    "recon_bank_lines":    [("account_no", "TEXT")],
    "recon_cc_lines":      [("acquirer", "TEXT"), ("fee_manual", "REAL"), ("vat_manual", "REAL"),
                            ("fee_vat_by", "TEXT"), ("fee_vat_at", "TEXT")],
    "recon_grab_lines":    [("fee_manual", "REAL"), ("vat_manual", "REAL"),
                            ("fee_vat_by", "TEXT"), ("fee_vat_at", "TEXT")],
    "recon_lineman_lines": [("fee_manual", "REAL"), ("vat_manual", "REAL"),
                            ("fee_vat_by", "TEXT"), ("fee_vat_at", "TEXT")],
    "recon_discrepancies": [("category", "TEXT"), ("bank", "TEXT"), ("assigned_to", "TEXT")],
    "recon_uploads":       [("file_hash", "TEXT")],
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
    """Widen recon_payment_map.bank to allow BAY/SCB/BBL (the CHECK constraint
    can't be ALTERed in SQLite → recreate the table once, preserving rows). Then
    future-proof the franchise-only 'Credit BAY' tender (seed_payment_map is
    master-scoped so it never picks it up)."""
    try:
        row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='recon_payment_map'").fetchone()
        if row and "'BAY'" not in (row[0] or ""):
            conn.executescript("""
                CREATE TABLE recon_payment_map__new (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    payment_code_norm TEXT NOT NULL UNIQUE,
                    category TEXT NOT NULL CHECK(category IN
                        ('CASH','QR','TRANSFER','CREDIT','GRAB','LINEMAN','COUPON','ENTERTAIN','OTHER')),
                    bank TEXT NOT NULL DEFAULT 'NONE' CHECK(bank IN
                        ('KTB','KBANK','KTC','BAY','SCB','BBL','PROMPTPAY','NONE','UNKNOWN')),
                    reconcile INTEGER NOT NULL DEFAULT 1,
                    notes TEXT, updated_at TEXT DEFAULT (datetime('now')), updated_by TEXT
                );
                INSERT INTO recon_payment_map__new
                    (id,payment_code_norm,category,bank,reconcile,notes,updated_at,updated_by)
                    SELECT id,payment_code_norm,category,bank,reconcile,notes,updated_at,updated_by
                    FROM recon_payment_map;
                DROP TABLE recon_payment_map;
                ALTER TABLE recon_payment_map__new RENAME TO recon_payment_map;
            """)
        conn.execute("""INSERT INTO recon_payment_map (payment_code_norm,category,bank,reconcile,updated_by)
            VALUES ('Credit BAY','CREDIT','BAY',1,'seed-franchise')
            ON CONFLICT(payment_code_norm) DO NOTHING""")
        # widen recon_acquirer_mid.acquirer to allow BAY/SCB/BBL (same CHECK problem)
        arow = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='recon_acquirer_mid'").fetchone()
        if arow and "'BAY'" not in (arow[0] or ""):
            conn.executescript("""
                CREATE TABLE recon_acquirer_mid__new (
                    mid_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    branch_id INTEGER NOT NULL REFERENCES branches(branch_id),
                    acquirer TEXT NOT NULL CHECK(acquirer IN ('KTB','KBANK','KTC','BAY','SCB','BBL')),
                    merchant_id TEXT, terminal_id TEXT, active INTEGER NOT NULL DEFAULT 1
                );
                INSERT INTO recon_acquirer_mid__new (mid_id,branch_id,acquirer,merchant_id,terminal_id,active)
                    SELECT mid_id,branch_id,acquirer,merchant_id,terminal_id,active FROM recon_acquirer_mid;
                DROP TABLE recon_acquirer_mid;
                ALTER TABLE recon_acquirer_mid__new RENAME TO recon_acquirer_mid;
            """)
    except sqlite3.OperationalError:
        pass


@app.on_event("startup")
def startup():
    with get_db() as conn:
        conn.executescript(_SCHEMA.read_text())
        _migrate(conn)
        _migrate_payment_bank(conn)   # widen bank CHECK + future-proof Credit BAY
        seed_payment_map(conn)   # idempotent; never overwrites staff edits
        conn.commit()


# ── Static frontend ───────────────────────────────────────────────────────────

_FRONTEND = Path(__file__).parent / "frontend"
if _FRONTEND.exists():
    app.mount("/app", StaticFiles(directory=str(_FRONTEND), html=True), name="frontend")


# ── Branches (read from shared branches table) ────────────────────────────────
# Reconciliation is scoped to MASTER (company-owned) branches only; franchises
# are out of scope. COALESCE keeps legacy NULL rows (default 'master') in scope.

_MASTER_ONLY = "COALESCE(branch_type,'master')='master' AND active=1"

# Reconciliation only considers data from this month onward — the app reconciles
# the live business, not the full historical DB (which the dashboard shares).
# Floor only; nothing is deleted. Override with env RECON_START_MONTH (YYYY-MM).
RECON_START_MONTH = os.getenv("RECON_START_MONTH", "2026-05")
RECON_START_DAY   = RECON_START_MONTH + "-01"


# ── Write auth + upload validation ────────────────────────────────────────────
# Mirrors the dashboard's gate: when OSHINEI_SEED_TOKEN is set (hosted deploys) a
# matching token is required via the `X-Upload-Token` header or `?token=`. Unset
# locally → writes are open, so the gate is invisible in dev.

MAX_UPLOAD_BYTES = 15 * 1024 * 1024          # 15 MB
_ALLOWED_EXT     = (".xlsx", ".xls")
_PERIOD_RE       = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


def _accepted_tokens() -> dict:
    """Parse OSHINEI_SEED_TOKEN into {token: label}. Comma/newline list so several
    people can each hold a token; entries may be 'label:token'. Single token still
    works. Shared shape with the dashboard gate."""
    raw = os.getenv("OSHINEI_SEED_TOKEN", "") or ""
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

def require_write_token(token: str = Query(""), x_upload_token: str = Header(None)):
    toks = _accepted_tokens()
    if toks:
        provided = (x_upload_token or token or "").strip()
        if not any(hmac.compare_digest(provided, t) for t in toks):
            raise HTTPException(403, "A valid write token is required.")


async def _read_upload(file: UploadFile) -> bytes:
    if not (file.filename or "").lower().endswith(_ALLOWED_EXT):
        raise HTTPException(415, "Only .xlsx/.xls files are accepted.")
    raw = await file.read()
    if not raw:
        raise HTTPException(422, "Empty file.")
    if len(raw) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"File exceeds the {MAX_UPLOAD_BYTES // (1024 * 1024)} MB limit.")
    return raw


def _validate_period(period_month: str):
    if not _PERIOD_RE.match(period_month or ""):
        raise HTTPException(400, "period_month must be in 'YYYY-MM' format.")


def _validate_master_branch(conn, branch_id: int):
    if not conn.execute(
        f"SELECT 1 FROM branches WHERE branch_id=? AND {_MASTER_ONLY}", (branch_id,)
    ).fetchone():
        raise HTTPException(400, f"branch_id {branch_id} is not an active master branch.")


def _assert_period_open(conn, branch_id: int, period_month: str):
    """Reject mutations to an approved/closed period. Reopen by setting the
    session status back to In-Review (or Draft). 423 = Locked."""
    row = conn.execute(
        "SELECT status FROM recon_sessions WHERE branch_id=? AND period_month=?",
        (branch_id, period_month)).fetchone()
    if row and row["status"] in ("approved", "closed"):
        raise HTTPException(
            423, f"{period_month} is {row['status']} for this branch — set the "
                 "session back to In-Review to make changes.")


@app.get("/api/branches")
def list_branches():
    with get_db() as conn:
        rows = conn.execute(
            f"SELECT branch_id, name, branch_type FROM branches "
            f"WHERE {_MASTER_ONLY} ORDER BY name"
        ).fetchall()
        return [dict(r) for r in rows]


# ── Config: payment-code map ──────────────────────────────────────────────────

_CATEGORIES = {"CASH", "QR", "TRANSFER", "CREDIT", "GRAB", "LINEMAN", "COUPON", "ENTERTAIN", "OTHER"}
_BANKS      = {"KTB", "KBANK", "KTC", "BAY", "SCB", "BBL", "PROMPTPAY", "NONE", "UNKNOWN"}


class PaymentMapUpsert(BaseModel):
    payment_code_norm: str | None = None   # required on create, ignored on update
    category:          str
    bank:              str = "NONE"
    reconcile:         int = 1
    notes:             str = ""
    updated_by:        str = ""


def _validate_map(body: PaymentMapUpsert):
    if body.category not in _CATEGORIES:
        raise HTTPException(400, f"category must be one of {sorted(_CATEGORIES)}")
    if body.bank not in _BANKS:
        raise HTTPException(400, f"bank must be one of {sorted(_BANKS)}")


@app.get("/api/payment-map")
def list_payment_map(needs_attention: int = 0):
    from .seed_payment_map import normalize
    with get_db() as conn:
        sql = "SELECT * FROM recon_payment_map"
        if needs_attention:
            sql += " WHERE bank='UNKNOWN'"
        sql += " ORDER BY (bank='UNKNOWN') DESC, category, bank, payment_code_norm"
        rows = [dict(r) for r in conn.execute(sql)]
        # Attach POS volume per normalized code (master branches) so staff can see
        # which codes actually matter — e.g. KNNQR is the single biggest tender.
        amounts: dict = {}
        try:
            for r in conn.execute("""
                SELECT p.payment_code, SUM(p.amount) amt, SUM(p.txn_count) n
                FROM pos_payment_month p JOIN branches b ON b.branch_id = p.branch_id
                WHERE COALESCE(b.branch_type,'master')='master' AND b.active=1
                  AND p.period_month >= ?
                GROUP BY p.payment_code
            """, (RECON_START_MONTH,)):
                a = amounts.setdefault(normalize(r["payment_code"]), {"amount": 0.0, "txn": 0})
                a["amount"] += float(r["amt"] or 0)
                a["txn"]    += int(r["n"] or 0)
        except sqlite3.OperationalError:
            pass
        for row in rows:
            a = amounts.get(row["payment_code_norm"], {"amount": 0.0, "txn": 0})
            row["pos_amount"] = round(a["amount"], 2)
            row["pos_txn"]    = a["txn"]
        return rows


@app.post("/api/payment-map", dependencies=[Depends(require_write_token)])
def create_payment_map(body: PaymentMapUpsert):
    _validate_map(body)
    code = (body.payment_code_norm or "").strip()
    if not code:
        raise HTTPException(400, "payment_code_norm is required")
    with get_db() as conn:
        try:
            cur = conn.execute("""
                INSERT INTO recon_payment_map (payment_code_norm, category, bank, reconcile, notes, updated_by)
                VALUES (?,?,?,?,?,?)
            """, (code, body.category, body.bank, body.reconcile, body.notes, body.updated_by or "manual"))
            conn.commit()
            return {"id": cur.lastrowid}
        except sqlite3.IntegrityError:
            raise HTTPException(409, f"payment_code_norm {code!r} already mapped")


@app.patch("/api/payment-map/{map_id}", dependencies=[Depends(require_write_token)])
def update_payment_map(map_id: int, body: PaymentMapUpsert):
    _validate_map(body)
    with get_db() as conn:
        old = conn.execute(
            "SELECT category, bank, reconcile FROM recon_payment_map WHERE id=?",
            (map_id,)).fetchone()
        conn.execute("""
            UPDATE recon_payment_map
            SET category=?, bank=?, reconcile=?, notes=?, updated_by=?, updated_at=datetime('now')
            WHERE id=?
        """, (body.category, body.bank, body.reconcile, body.notes, body.updated_by or "manual", map_id))
        if old:
            diffs = []
            if old["category"] != body.category:
                diffs.append({"field": "category", "old": old["category"], "new": body.category})
            if old["bank"] != body.bank:
                diffs.append({"field": "bank", "old": old["bank"], "new": body.bank})
            if old["reconcile"] != body.reconcile:
                diffs.append({"field": "reconcile", "old": old["reconcile"], "new": body.reconcile})
            if diffs:
                _audit(conn, "payment_map", map_id, "update", body.updated_by or "staff", diffs)
        conn.commit()
    return {"id": map_id}


@app.delete("/api/payment-map/{map_id}", dependencies=[Depends(require_write_token)])
def delete_payment_map(map_id: int):
    with get_db() as conn:
        conn.execute("DELETE FROM recon_payment_map WHERE id=?", (map_id,))
        conn.commit()
    return {"deleted": map_id}


@app.post("/api/payment-map/seed", dependencies=[Depends(require_write_token)])
def reseed_payment_map():
    with get_db() as conn:
        result = seed_payment_map(conn)
    return result


# ── Config: bank accounts ─────────────────────────────────────────────────────

class BankAccount(BaseModel):
    branch_id:    int
    bank:         str
    account_no:   str
    account_name: str = ""
    active:       int = 1


@app.get("/api/bank-accounts")
def list_bank_accounts(branch_id: int | None = None):
    with get_db() as conn:
        sql = """SELECT a.*, b.name AS branch_name FROM recon_bank_account a
                 JOIN branches b ON b.branch_id = a.branch_id"""
        params: list = []
        if branch_id:
            sql += " WHERE a.branch_id=?"; params.append(branch_id)
        sql += " ORDER BY b.name, a.bank"
        return [dict(r) for r in conn.execute(sql, params)]


@app.post("/api/bank-accounts", dependencies=[Depends(require_write_token)])
def create_bank_account(body: BankAccount):
    if body.bank not in ("KTB", "KBANK"):
        raise HTTPException(400, "bank must be KTB or KBANK")
    with get_db() as conn:
        _validate_master_branch(conn, body.branch_id)
        try:
            cur = conn.execute("""
                INSERT INTO recon_bank_account (branch_id, bank, account_no, account_name, active)
                VALUES (?,?,?,?,?)
            """, (body.branch_id, body.bank, body.account_no, body.account_name, body.active))
            conn.commit()
            return {"account_id": cur.lastrowid}
        except sqlite3.IntegrityError:
            raise HTTPException(409, f"account {body.bank}/{body.account_no} already exists")


@app.patch("/api/bank-accounts/{account_id}", dependencies=[Depends(require_write_token)])
def update_bank_account(account_id: int, body: BankAccount):
    if body.bank not in ("KTB", "KBANK"):
        raise HTTPException(400, "bank must be KTB or KBANK")
    with get_db() as conn:
        _validate_master_branch(conn, body.branch_id)
        conn.execute("""
            UPDATE recon_bank_account SET branch_id=?, bank=?, account_no=?, account_name=?, active=?
            WHERE account_id=?
        """, (body.branch_id, body.bank, body.account_no, body.account_name, body.active, account_id))
        conn.commit()
    return {"account_id": account_id}


@app.delete("/api/bank-accounts/{account_id}", dependencies=[Depends(require_write_token)])
def delete_bank_account(account_id: int):
    with get_db() as conn:
        conn.execute("DELETE FROM recon_bank_account WHERE account_id=?", (account_id,))
        conn.commit()
    return {"deleted": account_id}


# ── Config: credit-card acquirer MIDs ─────────────────────────────────────────

class AcquirerMid(BaseModel):
    branch_id:   int
    acquirer:    str
    merchant_id: str = ""
    terminal_id: str = ""
    active:      int = 1


@app.get("/api/acquirer-mids")
def list_acquirer_mids(branch_id: int | None = None):
    with get_db() as conn:
        sql = """SELECT m.*, b.name AS branch_name FROM recon_acquirer_mid m
                 JOIN branches b ON b.branch_id = m.branch_id"""
        params: list = []
        if branch_id:
            sql += " WHERE m.branch_id=?"; params.append(branch_id)
        sql += " ORDER BY b.name, m.acquirer"
        return [dict(r) for r in conn.execute(sql, params)]


@app.post("/api/acquirer-mids", dependencies=[Depends(require_write_token)])
def create_acquirer_mid(body: AcquirerMid):
    if body.acquirer not in ("KTB", "KBANK", "KTC", "BAY", "SCB", "BBL"):
        raise HTTPException(400, "acquirer must be KTB, KBANK, KTC, BAY, SCB or BBL")
    with get_db() as conn:
        _validate_master_branch(conn, body.branch_id)
        cur = conn.execute("""
            INSERT INTO recon_acquirer_mid (branch_id, acquirer, merchant_id, terminal_id, active)
            VALUES (?,?,?,?,?)
        """, (body.branch_id, body.acquirer, body.merchant_id, body.terminal_id, body.active))
        conn.commit()
        return {"mid_id": cur.lastrowid}


@app.patch("/api/acquirer-mids/{mid_id}", dependencies=[Depends(require_write_token)])
def update_acquirer_mid(mid_id: int, body: AcquirerMid):
    if body.acquirer not in ("KTB", "KBANK", "KTC", "BAY", "SCB", "BBL"):
        raise HTTPException(400, "acquirer must be KTB, KBANK, KTC, BAY, SCB or BBL")
    with get_db() as conn:
        _validate_master_branch(conn, body.branch_id)
        conn.execute("""
            UPDATE recon_acquirer_mid SET branch_id=?, acquirer=?, merchant_id=?, terminal_id=?, active=?
            WHERE mid_id=?
        """, (body.branch_id, body.acquirer, body.merchant_id, body.terminal_id, body.active, mid_id))
        conn.commit()
    return {"mid_id": mid_id}


@app.delete("/api/acquirer-mids/{mid_id}", dependencies=[Depends(require_write_token)])
def delete_acquirer_mid(mid_id: int):
    with get_db() as conn:
        conn.execute("DELETE FROM recon_acquirer_mid WHERE mid_id=?", (mid_id,))
        conn.commit()
    return {"deleted": mid_id}


# ── Uploads ───────────────────────────────────────────────────────────────────

SourceType = Literal["bank", "credit_card", "grab", "lineman"]

_LINE_TABLE = {
    "bank":        "recon_bank_lines",
    "credit_card": "recon_cc_lines",
    "grab":        "recon_grab_lines",
    "lineman":     "recon_lineman_lines",
}


def _parse(source_type: SourceType, raw: bytes, provider: str):
    """Returns (provider, rows, skipped_rows)."""
    if source_type == "bank":
        prov, rows, skipped = parse_bank_statement(raw, provider or None)
    elif source_type == "credit_card":
        prov, rows, skipped = parse_cc_settlement(raw, provider or None)
    elif source_type == "grab":
        rows, skipped = parse_grab_report(raw)
        prov = "Grab"
    else:
        rows, skipped = parse_lineman_report(raw)
        prov = "Lineman"
    return prov, rows, skipped


def _parse_or_422(source_type: SourceType, raw: bytes, provider: str):
    try:
        return _parse(source_type, raw, provider)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(422, f"Parse error: {exc}")


@app.post("/api/uploads/{source_type}/preview")
async def preview_upload(
    source_type: SourceType,
    file: UploadFile = File(...),
    branch_id: int   = Form(...),
    period_month: str = Form(...),
    provider: str    = Form(""),
):
    _validate_period(period_month)
    raw = await _read_upload(file)
    with get_db() as conn:
        _validate_master_branch(conn, branch_id)
    prov, rows, skipped = _parse_or_422(source_type, raw, provider)
    return {
        "provider":     prov,
        "row_count":    len(rows),
        "skipped_rows": skipped,   # data rows dropped for an unparseable date
        "preview":      rows[:50],
        "columns":      list(rows[0].keys()) if rows else [],
    }


@app.post("/api/uploads/{source_type}/confirm", dependencies=[Depends(require_write_token)])
async def confirm_upload(
    source_type: SourceType,
    file: UploadFile = File(...),
    branch_id: int   = Form(...),
    period_month: str = Form(...),
    provider: str    = Form(""),
):
    _validate_period(period_month)
    raw = await _read_upload(file)
    file_hash = hashlib.sha256(raw).hexdigest()

    with get_db() as conn:
        _validate_master_branch(conn, branch_id)
        _assert_period_open(conn, branch_id, period_month)

        # Double-count guard: refuse an identical file already confirmed for the
        # same branch / month / source. Delete the old one to legitimately replace.
        dup = conn.execute("""
            SELECT upload_id, file_name, uploaded_at FROM recon_uploads
            WHERE branch_id=? AND period_month=? AND source_type=?
              AND file_hash=? AND status='confirmed'
        """, (branch_id, period_month, source_type, file_hash)).fetchone()
        if dup:
            raise HTTPException(
                409, f"Identical file already uploaded for this branch/month: "
                     f"'{dup['file_name']}' on {dup['uploaded_at']}. "
                     "Delete that upload first if you mean to replace it.")

        prov, rows, skipped = _parse_or_422(source_type, raw, provider)

        cur = conn.execute("""
            INSERT INTO recon_uploads
                (source_type, branch_id, provider, file_name, period_month, row_count, file_hash, status)
            VALUES (?,?,?,?,?,?,?,'confirmed')
        """, (source_type, branch_id, prov, file.filename, period_month, len(rows), file_hash))
        upload_id = cur.lastrowid

        table = _LINE_TABLE[source_type]
        for row in rows:
            row["upload_id"] = upload_id
            row["branch_id"] = branch_id
            cols         = ", ".join(row.keys())
            placeholders = ", ".join("?" * len(row))
            conn.execute(f"INSERT INTO {table} ({cols}) VALUES ({placeholders})",
                         list(row.values()))
        _audit(conn, "upload", upload_id, "create", "staff",
               [{"field": "file_name", "old": None, "new": file.filename}])
        conn.commit()

    return {"upload_id": upload_id, "rows_saved": len(rows), "skipped_rows": skipped, "provider": prov}


@app.get("/api/uploads")
def list_uploads(branch_id: int | None = None, period_month: str | None = None):
    with get_db() as conn:
        filters, params = [], []
        if branch_id:
            filters.append("u.branch_id=?");    params.append(branch_id)
        if period_month:
            filters.append("u.period_month=?"); params.append(period_month)
        where = ("WHERE " + " AND ".join(filters)) if filters else ""
        rows = conn.execute(f"""
            SELECT u.*, b.name AS branch_name
            FROM recon_uploads u
            LEFT JOIN branches b ON b.branch_id = u.branch_id
            {where}
            ORDER BY u.uploaded_at DESC LIMIT 200
        """, params).fetchall()
        return [dict(r) for r in rows]


@app.delete("/api/uploads/{upload_id}", dependencies=[Depends(require_write_token)])
def delete_upload(upload_id: int):
    with get_db() as conn:
        up = conn.execute(
            "SELECT branch_id, period_month, file_name FROM recon_uploads WHERE upload_id=?",
            (upload_id,)).fetchone()
        if up:
            _assert_period_open(conn, up["branch_id"], up["period_month"])
        conn.execute("DELETE FROM recon_uploads WHERE upload_id=?", (upload_id,))
        _audit(conn, "upload", upload_id, "delete", "staff",
               [{"field": "file_name", "old": up["file_name"] if up else None, "new": None}])
        conn.commit()
    return {"deleted": upload_id}


# ── Evidence attachments (photos / PDFs per branch+day) ───────────────────────
_ATTACH_MAX = 10 * 1024 * 1024
_ATTACH_EXT = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".pdf", ".heic")

@app.get("/api/attachments")
def list_attachments(branch_id: int, day: str):
    if not _DAY_RE.match(day or ""):
        raise HTTPException(400, "day must be in 'YYYY-MM-DD' format.")
    with get_db() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT id, branch_id, day, category, file_name, mime, size_bytes, note, "
            "uploaded_by, uploaded_at FROM recon_attachment "
            "WHERE branch_id=? AND day=? ORDER BY uploaded_at DESC", (branch_id, day))]


@app.post("/api/attachments", dependencies=[Depends(require_write_token)])
async def create_attachment(branch_id: int = Form(...), day: str = Form(...),
                            category: str = Form(""), note: str = Form(""),
                            uploaded_by: str = Form(""), file: UploadFile = File(...)):
    if not _DAY_RE.match(day or ""):
        raise HTTPException(400, "day must be in 'YYYY-MM-DD' format.")
    fn = (file.filename or "").strip()
    if not fn.lower().endswith(_ATTACH_EXT):
        raise HTTPException(415, "Only images (jpg/png/webp/gif/heic) or PDF are accepted.")
    raw = await file.read()
    if not raw:
        raise HTTPException(422, "Empty file.")
    if len(raw) > _ATTACH_MAX:
        raise HTTPException(413, f"File exceeds the {_ATTACH_MAX // (1024*1024)} MB limit.")
    with get_db() as conn:
        _validate_master_branch(conn, branch_id)
        cur = conn.execute(
            "INSERT INTO recon_attachment(branch_id,day,category,file_name,mime,size_bytes,data,note,uploaded_by) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            (branch_id, day, (category or None), fn, file.content_type, len(raw), raw,
             (note or None), (uploaded_by or None)))
        conn.commit()
        return {"id": cur.lastrowid, "file_name": fn, "size_bytes": len(raw)}


@app.get("/api/attachments/{att_id}/file")
def get_attachment_file(att_id: int):
    with get_db() as conn:
        r = conn.execute("SELECT file_name, mime, data FROM recon_attachment WHERE id=?",
                         (att_id,)).fetchone()
    if not r:
        raise HTTPException(404, "not found")
    return Response(content=r["data"], media_type=r["mime"] or "application/octet-stream",
                    headers={"Content-Disposition": f'inline; filename="{r["file_name"]}"'})


@app.delete("/api/attachments/{att_id}", dependencies=[Depends(require_write_token)])
def delete_attachment(att_id: int):
    with get_db() as conn:
        conn.execute("DELETE FROM recon_attachment WHERE id=?", (att_id,))
        conn.commit()
    return {"deleted": att_id}


# ── Sessions ──────────────────────────────────────────────────────────────────

class SessionCreate(BaseModel):
    branch_id:    int
    period_month: str
    notes:        str = ""
    created_by:   str = ""


@app.post("/api/sessions", dependencies=[Depends(require_write_token)])
def create_session(body: SessionCreate):
    _validate_period(body.period_month)
    with get_db() as conn:
        _validate_master_branch(conn, body.branch_id)
        try:
            cur = conn.execute("""
                INSERT INTO recon_sessions (branch_id, period_month, notes, created_by)
                VALUES (?,?,?,?)
            """, (body.branch_id, body.period_month, body.notes, body.created_by))
            conn.commit()
            return {"session_id": cur.lastrowid}
        except sqlite3.IntegrityError:
            raise HTTPException(409, f"Session already exists for branch {body.branch_id} / {body.period_month}")


@app.get("/api/sessions")
def list_sessions(branch_id: int | None = None, period_month: str | None = None):
    with get_db() as conn:
        filters, params = [], []
        if branch_id:
            filters.append("s.branch_id=?");    params.append(branch_id)
        if period_month:
            filters.append("s.period_month=?"); params.append(period_month)
        where = ("WHERE " + " AND ".join(filters)) if filters else ""
        rows = conn.execute(f"""
            SELECT s.*, b.name AS branch_name
            FROM recon_sessions s
            JOIN branches b ON b.branch_id = s.branch_id
            {where}
            ORDER BY s.period_month DESC, b.name
        """, params).fetchall()
        return [dict(r) for r in rows]


@app.get("/api/sessions/{session_id}")
def get_session(session_id: int):
    with get_db() as conn:
        row = conn.execute("""
            SELECT s.*, b.name AS branch_name
            FROM recon_sessions s
            JOIN branches b ON b.branch_id = s.branch_id
            WHERE s.session_id=?
        """, (session_id,)).fetchone()
        if not row:
            raise HTTPException(404)
        return dict(row)


@app.post("/api/sessions/{session_id}/run-match", dependencies=[Depends(require_write_token)])
def run_match(session_id: int):
    with get_db() as conn:
        session = conn.execute(
            "SELECT branch_id, period_month FROM recon_sessions WHERE session_id=?",
            (session_id,)
        ).fetchone()
        if not session:
            raise HTTPException(404)
        _assert_period_open(conn, session["branch_id"], session["period_month"])
        result = run_auto_match(conn, session_id, session["branch_id"], session["period_month"])
    return result


@app.patch("/api/sessions/{session_id}/status", dependencies=[Depends(require_write_token)])
def update_session_status(
    session_id:  int,
    status:      str = Form(...),
    approved_by: str = Form(""),
):
    valid = {"draft", "in_review", "approved", "closed"}
    if status not in valid:
        raise HTTPException(400, f"status must be one of {valid}")
    with get_db() as conn:
        old_row = conn.execute(
            "SELECT status FROM recon_sessions WHERE session_id=?", (session_id,)).fetchone()
        old_status = old_row["status"] if old_row else None
        # Approval gate: don't let a month be signed off while issues are open.
        if status in ("approved", "closed"):
            pending = conn.execute(
                "SELECT COUNT(*) FROM recon_discrepancies "
                "WHERE session_id=? AND resolution='pending'",
                (session_id,)).fetchone()[0]
            if pending:
                raise HTTPException(
                    409, f"Cannot {status} — {pending} open discrepancy(ies) remain. "
                         "Resolve or accept them first.")
        # Only stamp approval metadata when the session is actually approved;
        # moving back to draft/in_review must not record a (false) approval.
        if status == "approved":
            conn.execute("""
                UPDATE recon_sessions
                SET status=?, approved_by=?, approved_at=datetime('now')
                WHERE session_id=?
            """, (status, approved_by, session_id))
        else:
            conn.execute(
                "UPDATE recon_sessions SET status=? WHERE session_id=?",
                (status, session_id),
            )
        if old_status != status:
            _audit(conn, "session", session_id, "update", approved_by or "staff",
                   [{"field": "status", "old": old_status, "new": status}])
        conn.commit()
    return {"session_id": session_id, "status": status}


# ── Session summary + category reconciliation ─────────────────────────────────

@app.get("/api/sessions/{session_id}/summary")
def session_summary(session_id: int):
    """Live per-category reconciliation (POS tender vs the right external source),
    computed fresh so it reflects current uploads/fee-VAT edits."""
    with get_db() as conn:
        session = conn.execute(
            "SELECT branch_id, period_month FROM recon_sessions WHERE session_id=?",
            (session_id,)
        ).fetchone()
        if not session:
            raise HTTPException(404)
        bid, pm = session["branch_id"], session["period_month"]

        rows, unmapped = category_reconciliation(conn, bid, pm)
        # per-lane manual-match tally (so the cards can flag manually-cleared lanes)
        manual: dict = {}
        for mr in conn.execute(
            "SELECT category, bank, COUNT(*) n, COALESCE(SUM(pos_amount),0) amt "
            "FROM recon_manual_match WHERE branch_id=? AND period_month=? GROUP BY category, bank",
            (bid, pm)):
            manual[f"{mr['category']}|{mr['bank']}"] = {"count": mr["n"], "amount": round(mr["amt"], 2)}
        categories = []
        for r in rows:
            c = {k: r[k] for k in
                 ("category", "bank", "pos_amount", "ext_amount", "ext_net", "variance", "status")}
            c["manual"] = manual.get(f"{r['category']}|{r['bank']}")
            categories.append(c)
        payouts = payout_reconciliation(conn, bid, pm)

        pending = conn.execute(
            "SELECT COUNT(*) FROM recon_discrepancies WHERE session_id=? AND resolution='pending'",
            (session_id,)
        ).fetchone()[0]

        pos_total = sum(r["pos_amount"] for r in rows)
        ext_total = sum(r["ext_amount"] for r in rows)
        return {
            "categories":            categories,
            "payouts":               payouts,
            "unmapped":              [{"code": k, "amount": round(v, 2)} for k, v in sorted(unmapped.items())],
            "pos_total":             round(pos_total, 2),
            "ext_total":             round(ext_total, 2),
            "variance":              round(ext_total - pos_total, 2),
            "matched":               sum(1 for r in rows if r["status"] == "matched"),
            "pending_discrepancies": pending,
        }


@app.get("/api/sessions/{session_id}/categories")
def session_categories(session_id: int):
    """Persisted per-category results from the last run-match."""
    with get_db() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM recon_category_result WHERE session_id=? ORDER BY category, bank",
            (session_id,))]


@app.get("/api/sessions/{session_id}/daily")
def session_daily(session_id: int):
    """Day-by-day reconciliation board with per-day pending tracking for late
    documents. Computed live so it reflects current uploads + fee/VAT edits."""
    with get_db() as conn:
        s = conn.execute(
            "SELECT branch_id, period_month FROM recon_sessions WHERE session_id=?",
            (session_id,)).fetchone()
        if not s:
            raise HTTPException(404)
        return daily_reconciliation(conn, s["branch_id"], s["period_month"])


@app.get("/api/sessions/{session_id}/lane")
def session_lane(session_id: int, category: str, bank: str, day: str | None = None):
    """Drill-down: the external lines that make up one (category, bank) lane,
    optionally for a single day. POS side stays an aggregate (no per-receipt
    tender split exists)."""
    with get_db() as conn:
        s = conn.execute(
            "SELECT branch_id, period_month FROM recon_sessions WHERE session_id=?",
            (session_id,)).fetchone()
        if not s:
            raise HTTPException(404)
        lines = lane_lines(conn, s["branch_id"], s["period_month"], category, bank, day)
        return {"category": category, "bank": bank, "day": day,
                "count": len(lines), "total": round(sum(l["amount"] for l in lines), 2),
                "lines": lines}


# ── Excel month-pack export ───────────────────────────────────────────────────

def _build_export(conn, session) -> bytes:
    """One workbook per session: Summary / Categories / Payout chain / Daily /
    Discrepancies — the artifact an accountant signs off and files."""
    import io
    import openpyxl
    from openpyxl.styles import Font

    bid, pm = session["branch_id"], session["period_month"]
    rows, unmapped = category_reconciliation(conn, bid, pm)
    payouts = payout_reconciliation(conn, bid, pm)
    daily = daily_reconciliation(conn, bid, pm)
    discs = conn.execute(
        "SELECT * FROM recon_discrepancies WHERE session_id=? ORDER BY resolution, ABS(COALESCE(variance,0)) DESC",
        (session["session_id"],)).fetchall()

    wb = openpyxl.Workbook()
    bold = Font(bold=True)

    def sheet(title, headers, data_rows, first=False):
        ws = wb.active if first else wb.create_sheet(title)
        if first:
            ws.title = title
        ws.append(headers)
        for c in ws[1]:
            c.font = bold
        for r in data_rows:
            ws.append(r)
        for i, h in enumerate(headers, 1):
            ws.column_dimensions[chr(64 + i)].width = max(12, len(str(h)) + 2)
        return ws

    pos_total = round(sum(r["pos_amount"] for r in rows), 2)
    ext_total = round(sum(r["ext_amount"] for r in rows), 2)
    pending = sum(1 for d in discs if d["resolution"] == "pending")
    sheet("Summary",
          ["Field", "Value"],
          [["Branch", session["branch_name"]], ["Period", pm], ["Status", session["status"]],
           ["POS tender (reconcilable)", pos_total], ["External total", ext_total],
           ["Variance", round(ext_total - pos_total, 2)],
           ["Matched lanes", sum(1 for r in rows if r["status"] == "matched")],
           ["Open discrepancies", pending],
           ["Unmapped POS codes", ", ".join(u["code"] for u in
                                            [{"code": k} for k in sorted(unmapped)]) or "—"]],
          first=True)

    sheet("Categories",
          ["Category", "Bank", "POS", "Statement", "Net (after fee/VAT)", "Variance", "Status"],
          [[r["category"], r["bank"], r["pos_amount"], r["ext_amount"], r["ext_net"],
            r["variance"], r["status"]] for r in rows])

    sheet("Payout chain",
          ["Category", "Report net", "Bank payout", "Variance", "Status"],
          [[p["category"], p["report_net"], p["bank_payout"], p["variance"], p["status"]] for p in payouts])

    daily_rows = []
    for d in daily.get("days", []):
        for l in d["lanes"]:
            daily_rows.append([d["day"], l["category"], l["bank"], l["pos"], l["ext"],
                               l["variance"], l["status"]])
    sheet("Daily",
          ["Day", "Category", "Bank", "POS", "External", "Variance", "Status"], daily_rows)

    sheet("Discrepancies",
          ["Type", "Category", "Bank", "External", "POS", "Variance", "Resolution", "Assignee", "Note"],
          [[d["disc_type"], d["category"], d["bank"], d["amount_external"], d["amount_pos"],
            d["variance"], d["resolution"], d["assigned_to"] or "", d["resolution_note"] or ""]
           for d in discs])

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


@app.get("/api/sessions/{session_id}/export.xlsx")
def export_session(session_id: int):
    with get_db() as conn:
        session = conn.execute("""
            SELECT s.*, b.name AS branch_name
            FROM recon_sessions s JOIN branches b ON b.branch_id = s.branch_id
            WHERE s.session_id=?""", (session_id,)).fetchone()
        if not session:
            raise HTTPException(404)
        data = _build_export(conn, session)
    from urllib.parse import quote
    fname = f"recon_{session['branch_name']}_{session['period_month']}.xlsx".replace(" ", "_")
    # Branch names are Thai → not latin-1 safe for a raw header. RFC 5987: an ASCII
    # fallback plus a UTF-8 filename* the browser prefers.
    ascii_fallback = f"recon_{session['period_month']}.xlsx"
    return Response(
        content=data,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition":
                 f"attachment; filename=\"{ascii_fallback}\"; filename*=UTF-8''{quote(fname)}"})


# ── Discrepancies ─────────────────────────────────────────────────────────────

@app.get("/api/sessions/{session_id}/discrepancies")
def list_discrepancies(
    session_id: int,
    resolution: str | None = "pending",
    category:   str | None = None,
    bank:       str | None = None,
):
    with get_db() as conn:
        filters: list[str] = ["session_id=?"]
        params:  list      = [session_id]
        if resolution and resolution != "all":
            filters.append("resolution=?"); params.append(resolution)
        if category:
            filters.append("category=?"); params.append(category)
        if bank:
            filters.append("bank=?"); params.append(bank)
        rows = conn.execute(f"""
            SELECT * FROM recon_discrepancies
            WHERE {' AND '.join(filters)}
            ORDER BY ABS(COALESCE(variance, amount_external)) DESC
        """, params).fetchall()
        return [dict(r) for r in rows]


class ResolveBody(BaseModel):
    resolution:      Literal["accepted", "adjusted", "rejected"]
    resolution_note: str = ""
    resolved_by:     str = ""


def _assert_disc_period_open(conn, disc_id: int):
    row = conn.execute("""
        SELECT s.branch_id, s.period_month
        FROM recon_discrepancies d JOIN recon_sessions s ON s.session_id = d.session_id
        WHERE d.id=?""", (disc_id,)).fetchone()
    if row:
        _assert_period_open(conn, row["branch_id"], row["period_month"])


def _audit(conn, entity: str, entity_id: int, action: str, by: str,
           diffs: list[dict] | None = None):
    """Record create/update/delete with optional per-field diffs.
    diffs = [{"field": "status", "old": "draft", "new": "approved"}, ...]
    """
    ts = _dt.utcnow().isoformat(timespec="seconds")
    if not diffs:
        conn.execute(
            "INSERT INTO recon_audit_log(ts,entity,entity_id,action,field,old_val,new_val,by)"
            " VALUES(?,?,?,?,?,?,?,?)",
            (ts, entity, entity_id, action, None, None, None, by or "staff"),
        )
    else:
        for d in diffs:
            old = None if d.get("old") is None else str(d["old"])
            new = None if d.get("new") is None else str(d["new"])
            conn.execute(
                "INSERT INTO recon_audit_log(ts,entity,entity_id,action,field,old_val,new_val,by)"
                " VALUES(?,?,?,?,?,?,?,?)",
                (ts, entity, entity_id, action, d["field"], old, new, by or "staff"),
            )


@app.patch("/api/discrepancies/{disc_id}/resolve", dependencies=[Depends(require_write_token)])
def resolve_discrepancy(disc_id: int, body: ResolveBody):
    with get_db() as conn:
        _assert_disc_period_open(conn, disc_id)
        old = conn.execute(
            "SELECT resolution, resolution_note FROM recon_discrepancies WHERE id=?",
            (disc_id,)).fetchone()
        conn.execute("""
            UPDATE recon_discrepancies
            SET resolution=?, resolution_note=?, resolved_by=?, resolved_at=datetime('now')
            WHERE id=?
        """, (body.resolution, body.resolution_note, body.resolved_by, disc_id))
        diffs = []
        if old:
            if old["resolution"] != body.resolution:
                diffs.append({"field": "resolution", "old": old["resolution"], "new": body.resolution})
            if (old["resolution_note"] or "") != (body.resolution_note or ""):
                diffs.append({"field": "resolution_note",
                               "old": old["resolution_note"], "new": body.resolution_note})
        _audit(conn, "discrepancy", disc_id, "update", body.resolved_by or "staff", diffs or None)
        conn.commit()
    return {"id": disc_id, "resolution": body.resolution}


@app.patch("/api/discrepancies/{disc_id}/assign", dependencies=[Depends(require_write_token)])
def assign_discrepancy(disc_id: int, assigned_to: str = Form(...)):
    with get_db() as conn:
        _assert_disc_period_open(conn, disc_id)
        old = conn.execute(
            "SELECT assigned_to FROM recon_discrepancies WHERE id=?", (disc_id,)).fetchone()
        conn.execute("UPDATE recon_discrepancies SET assigned_to=? WHERE id=?", (assigned_to, disc_id))
        _audit(conn, "discrepancy", disc_id, "update", "staff",
               [{"field": "assigned_to",
                 "old": old["assigned_to"] if old else None, "new": assigned_to}])
        conn.commit()
    return {"id": disc_id, "assigned_to": assigned_to}


class BulkResolveBody(BaseModel):
    ids:             list[int]
    resolution:      Literal["accepted", "adjusted", "rejected"]
    resolution_note: str = ""
    resolved_by:     str = ""


@app.post("/api/sessions/{session_id}/discrepancies/bulk-resolve",
          dependencies=[Depends(require_write_token)])
def bulk_resolve_discrepancies(session_id: int, body: BulkResolveBody):
    if not body.ids:
        raise HTTPException(400, "ids list must not be empty")
    with get_db() as conn:
        s = conn.execute(
            "SELECT branch_id, period_month FROM recon_sessions WHERE session_id=?",
            (session_id,)).fetchone()
        if not s:
            raise HTTPException(404)
        _assert_period_open(conn, s["branch_id"], s["period_month"])
        ph = ",".join("?" * len(body.ids))
        conn.execute(f"""
            UPDATE recon_discrepancies
            SET resolution=?, resolution_note=?, resolved_by=?, resolved_at=datetime('now')
            WHERE session_id=? AND id IN ({ph}) AND resolution='pending'
        """, [body.resolution, body.resolution_note, body.resolved_by or "staff", session_id]
             + list(body.ids))
        _audit(conn, "session", session_id, "update", body.resolved_by or "staff",
               [{"field": "bulk_resolve",
                 "old": "pending",
                 "new": f"{body.resolution} × {len(body.ids)} items"}])
        conn.commit()
        updated = conn.execute(
            f"SELECT COUNT(*) FROM recon_discrepancies WHERE id IN ({ph}) AND resolution=?",
            list(body.ids) + [body.resolution]).fetchone()[0]
    return {"updated": updated, "resolution": body.resolution}


# ── Manual fee / VAT per line (Grab / Line Man / Credit Card) ──────────────────

_FEE_VAT_TABLE = {
    "grab":        "recon_grab_lines",
    "lineman":     "recon_lineman_lines",
    "credit_card": "recon_cc_lines",
}


class FeeVat(BaseModel):
    fee_manual: float | None = None
    vat_manual: float | None = None
    by:         str = ""


@app.get("/api/lines/{source}")
def list_lines(source: str, branch_id: int, period_month: str):
    """Lines for the fee/VAT editor, with effective fee/VAT and computed net."""
    if source not in _FEE_VAT_TABLE:
        raise HTTPException(400, f"source must be one of {sorted(_FEE_VAT_TABLE)}")
    table = _FEE_VAT_TABLE[source]
    spec = {
        "grab":        ("order_date",      "order_id",  "food_amount",  "commission_amt", None,               "promo_deduct"),
        "lineman":     ("order_date",      "order_id",  "food_amount",  "commission_amt", "vat_on_commission","promo_deduct"),
        "credit_card": ("settlement_date", "batch_no",  "gross_amount", "mdr_fee",        None,               None),
    }[source]
    date_col, ref_col, gross_col, fee_col, vat_col, promo_col = spec
    out = []
    with get_db() as conn:
        extra = " AND order_status='Completed'" if source in ("grab", "lineman") else ""
        rows = conn.execute(f"""
            SELECT * FROM {table}
            WHERE branch_id=? AND strftime('%Y-%m', {date_col})=?{extra}
            ORDER BY {date_col}
        """, (branch_id, period_month)).fetchall()
        for r in rows:
            gross  = float(r[gross_col] or 0)
            fee    = r["fee_manual"] if r["fee_manual"] is not None else float(r[fee_col] or 0)
            if r["vat_manual"] is not None:
                vat = r["vat_manual"]
            else:
                vat = float(r[vat_col] or 0) if vat_col else 0.0
            promo  = float(r[promo_col] or 0) if promo_col else 0.0
            refund = float(r["refund_amount"] or 0) if source == "credit_card" else 0.0
            out.append({
                "id":         r["id"],
                "date":       r[date_col],
                "ref":        r[ref_col] or "",
                "gross":      round(gross, 2),
                "fee":        r["fee_manual"],
                "vat":        r["vat_manual"],
                "fee_parsed": round(float(r[fee_col] or 0), 2),
                "other":      round(refund + promo, 2),   # non-fee deductions → client live-recompute
                "net":        round(gross - refund - fee - vat - promo, 2),
            })
    return out


@app.patch("/api/lines/{source}/{line_id}/fee-vat", dependencies=[Depends(require_write_token)])
def set_fee_vat(source: str, line_id: int, body: FeeVat):
    table = _FEE_VAT_TABLE.get(source)
    if not table:
        raise HTTPException(400, f"source must be one of {sorted(_FEE_VAT_TABLE)}")
    date_col = "settlement_date" if source == "credit_card" else "order_date"
    with get_db() as conn:
        line = conn.execute(
            f"SELECT branch_id, {date_col} AS d, fee_manual, vat_manual FROM {table} WHERE id=?",
            (line_id,)).fetchone()
        if line and line["d"]:
            _assert_period_open(conn, line["branch_id"], str(line["d"])[:7])
        conn.execute(f"""
            UPDATE {table}
            SET fee_manual=?, vat_manual=?, fee_vat_by=?, fee_vat_at=datetime('now')
            WHERE id=?
        """, (body.fee_manual, body.vat_manual, body.by or "staff", line_id))
        if line:
            diffs = []
            if line["fee_manual"] != body.fee_manual:
                diffs.append({"field": "fee_manual", "old": line["fee_manual"], "new": body.fee_manual})
            if line["vat_manual"] != body.vat_manual:
                diffs.append({"field": "vat_manual", "old": line["vat_manual"], "new": body.vat_manual})
            if diffs:
                _audit(conn, "line", line_id, "update", body.by or "staff", diffs)
        conn.commit()
    return {"source": source, "line_id": line_id,
            "fee_manual": body.fee_manual, "vat_manual": body.vat_manual}


# ── Matches (reconciled category buckets from the last run-match) ─────────────

@app.get("/api/sessions/{session_id}/matches")
def list_matches(session_id: int, category: str | None = None):
    with get_db() as conn:
        filters = ["session_id=?", "status='matched'"]
        params: list = [session_id]
        if category:
            filters.append("category=?"); params.append(category)
        rows = conn.execute(f"""
            SELECT * FROM recon_category_result
            WHERE {' AND '.join(filters)}
            ORDER BY category, bank
        """, params).fetchall()
        return [dict(r) for r in rows]


# ── Cross-branch daily board ──────────────────────────────────────────────────

_DAY_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$")


@app.get("/api/daily-board/dates")
def daily_board_dates():
    """Min / max day with POS daily data — drives the date picker bounds + default.

    min_day is floored to RECON_START_DAY so the board never surfaces pre-cutoff
    data (the historical rows stay in the DB for the dashboard)."""
    with get_db() as conn:
        try:
            row = conn.execute(
                "SELECT MIN(day) mn, MAX(day) mx FROM pos_payment_day WHERE day >= ?",
                (RECON_START_DAY,)).fetchone()
            return {"min_day": row["mn"] or RECON_START_DAY, "max_day": row["mx"]}
        except sqlite3.OperationalError:
            return {"min_day": None, "max_day": None}


def _coupon_range(gran: str, period_month: str, day: str | None):
    """Resolve (date_from, date_to, label) for a coupon view. gran = day|week|month.
    Day strings compare lexicographically, so '-31' is a safe month upper bound."""
    if gran == "day":
        if not (day and _DAY_RE.match(day)):
            raise HTTPException(400, "gran=day needs a valid 'day' (YYYY-MM-DD)")
        return day, day, day
    if gran == "week":
        if not (day and _DAY_RE.match(day)):
            raise HTTPException(400, "gran=week needs a valid 'day' (YYYY-MM-DD)")
        d = _dt.strptime(day, "%Y-%m-%d").date()
        mon = d - _td(days=d.weekday())
        sun = mon + _td(days=6)
        return mon.isoformat(), sun.isoformat(), f"{mon.isoformat()} – {sun.isoformat()}"
    _validate_period(period_month)
    return period_month + "-01", period_month + "-31", period_month


def _coupon_prev(gran: str, period_month: str, day: str | None):
    """Previous comparable period (prev day / prev week / prev month) for MoM."""
    if gran == "day":
        d = _dt.strptime(day, "%Y-%m-%d").date() - _td(days=1)
        return d.isoformat(), d.isoformat(), d.isoformat()
    if gran == "week":
        d = _dt.strptime(day, "%Y-%m-%d").date()
        mon = d - _td(days=d.weekday() + 7)
        sun = mon + _td(days=6)
        return mon.isoformat(), sun.isoformat(), f"{mon.isoformat()} – {sun.isoformat()}"
    y, m = int(period_month[:4]), int(period_month[5:7])
    pm = f"{y-1:04d}-12" if m == 1 else f"{y:04d}-{m-1:02d}"
    return pm + "-01", pm + "-31", pm


def _coupon_series_map(conn, d_from, d_to, branch_id):
    """series_id → discount total for a date range (+ optional branch)."""
    w = ("WHERE cd.day BETWEEN ? AND ? AND COALESCE(b.branch_type,'master')='master' AND b.active=1")
    p = [d_from, d_to]
    if branch_id:
        w += " AND cd.branch_id=?"; p.append(branch_id)
    out = {}
    for r in conn.execute(f"""SELECT cd.coupon_series_id sid, SUM(cd.discount_amount) amt
                              FROM pos_coupon_day cd JOIN branches b ON b.branch_id=cd.branch_id
                              {w} GROUP BY cd.coupon_series_id""", p):
        out[r["sid"]] = r["amt"] or 0
    return out


@app.get("/api/coupon-series")
def coupon_series(period_month: str = "", gran: str = "month",
                  day: str | None = None, branch_id: int | None = None,
                  series_id: int | None = None):
    """COUPON detail by coupon series, at day / week / month grain (from
    pos_coupon_day). Floored to RECON_START_MONTH; master only. Pass series_id to
    drill one series (by_branch = that series per branch)."""
    if gran not in ("day", "week", "month"):
        raise HTTPException(400, "gran must be day, week or month")
    if gran == "month" and not period_month:
        raise HTTPException(400, "period_month required for gran=month")
    d_from, d_to, label = _coupon_range(gran, period_month, day)
    empty = {"gran": gran, "label": label, "range": [d_from, d_to],
             "series": [], "by_branch": [], "total": 0.0, "total_used": 0}
    if d_to < RECON_START_DAY:
        return empty
    if d_from < RECON_START_DAY:
        d_from = RECON_START_DAY
    with get_db() as conn:
        params = [d_from, d_to]
        where = ("WHERE cd.day BETWEEN ? AND ? "
                 "AND COALESCE(b.branch_type,'master')='master' AND b.active=1")
        if branch_id:
            where += " AND cd.branch_id=?"; params.append(branch_id)
        if series_id is not None:
            where += " AND cd.coupon_series_id=?"; params.append(series_id)
        try:
            rows = [dict(r) for r in conn.execute(f"""
                SELECT cd.coupon_series_id AS series_id,
                       SUM(cd.used_count)      AS used_count,
                       SUM(cd.discount_amount) AS discount_amount
                FROM pos_coupon_day cd JOIN branches b ON b.branch_id = cd.branch_id
                {where}
                GROUP BY cd.coupon_series_id
                ORDER BY discount_amount DESC
            """, params)]
        except sqlite3.OperationalError:
            return {**empty, "hint": "pos_coupon_day not found — run sync_mssql.py"}
        total = round(sum(r["discount_amount"] or 0 for r in rows), 2)
        total_used = int(sum(r["used_count"] or 0 for r in rows))
        name_map = {}
        try:
            for nr in conn.execute("SELECT coupon_series_id, name, code FROM coupon_series"):
                name_map[nr["coupon_series_id"]] = (nr["name"] or "", nr["code"] or "")
        except sqlite3.OperationalError:
            pass
        # Previous comparable period → MoM per series
        pf, pt, plabel = _coupon_prev(gran, period_month, day)
        if pf < RECON_START_DAY:
            pf = RECON_START_DAY
        prev_map = _coupon_series_map(conn, pf, pt, branch_id) if pt >= RECON_START_DAY else {}
        for r in rows:
            r["discount_amount"] = round(r["discount_amount"] or 0, 2)
            nm = name_map.get(r["series_id"], ("", ""))
            r["series_name"], r["series_code"] = nm[0], nm[1]
            prev = prev_map.get(r["series_id"], 0)
            r["prev_amount"] = round(prev, 2)
            r["mom_pct"] = round((r["discount_amount"] - prev) / prev * 100, 1) if prev else None
        by_branch = []
        if not branch_id:
            by_branch = [dict(r) for r in conn.execute(f"""
                SELECT b.branch_id, b.name AS branch_name,
                       SUM(cd.used_count)      AS used_count,
                       SUM(cd.discount_amount) AS discount_amount
                FROM pos_coupon_day cd JOIN branches b ON b.branch_id = cd.branch_id
                {where}
                GROUP BY b.branch_id
                ORDER BY discount_amount DESC
            """, params)]
            for r in by_branch:
                r["discount_amount"] = round(r["discount_amount"] or 0, 2)
        # Drill: per-day breakdown for a single series
        by_day = []
        if series_id is not None:
            by_day = [dict(r) for r in conn.execute(f"""
                SELECT cd.day,
                       SUM(cd.used_count)      AS used_count,
                       SUM(cd.discount_amount) AS discount_amount
                FROM pos_coupon_day cd JOIN branches b ON b.branch_id = cd.branch_id
                {where}
                GROUP BY cd.day
                ORDER BY cd.day
            """, params)]
            for r in by_day:
                r["discount_amount"] = round(r["discount_amount"] or 0, 2)
        return {"gran": gran, "label": label, "range": [d_from, d_to],
                "prev_label": plabel, "prev_range": [pf, pt],
                "period_month": period_month, "branch_id": branch_id,
                "series": rows, "by_branch": by_branch, "by_day": by_day,
                "total": total, "total_used": total_used,
                "prev_total": round(sum(prev_map.values()), 2)}


@app.get("/api/daily-board")
def daily_board_endpoint(day: str):
    if not _DAY_RE.match(day or ""):
        raise HTTPException(400, "day must be in 'YYYY-MM-DD' format.")
    with get_db() as conn:
        return daily_board(conn, day)


# ── Daily reconciliation report (print-to-PDF evidence) ───────────────────────
_STATUS_TH = {"matched": "ตรงกับเอกสาร", "variance": "มีส่วนต่าง", "pos_only": "รอเอกสาร",
              "ext_only": "เกินจากเอกสาร", "no_source": "ไม่มีแหล่งอ้างอิง", "manual": "กระทบมือ"}

def _m(v):
    return f"{(v or 0):,.2f}"

def _daily_report_html(conn, day: str, branch_id: int | None) -> str:
    board = daily_board(conn, day)
    cmeta = board.get("column_meta", {})
    rows = board["branches"]
    if branch_id:
        rows = [r for r in rows if r["branch_id"] == branch_id]
    keep = {r["branch_id"] for r in rows}
    bf = " AND branch_id=?" if branch_id else ""
    p = [day, branch_id] if branch_id else [day]

    def grouped(sql, params):
        out = {}
        for r in conn.execute(sql, params):
            out.setdefault(r["branch_id"], []).append(r)
        return out
    cc   = grouped(f"SELECT * FROM recon_cc_lines WHERE settlement_date=?{bf}", p)
    grab = grouped(f"SELECT * FROM recon_grab_lines WHERE order_date=? AND order_status='Completed'{bf}", p)
    lnmn = grouped(f"SELECT * FROM recon_lineman_lines WHERE order_date=? AND order_status='Completed'{bf}", p)
    bank = grouped(f"SELECT * FROM recon_bank_lines WHERE txn_date=? AND credit>0{bf}", p)
    # manual matches for the month, keep those that touch this day
    mm = {}
    mp = [day[:7], branch_id] if branch_id else [day[:7]]
    for r in conn.execute(f"SELECT * FROM recon_manual_match WHERE period_month=?{bf}", mp):
        try:
            days = json.loads(r["pos_days"] or "[]")
        except Exception:
            days = []
        amt = sum(float(x.get("amount") or 0) for x in days if x.get("day") == day)
        if amt:
            mm.setdefault(r["branch_id"], []).append((r, amt))

    css = """<style>
      @page{size:A4;margin:14mm}
      *{box-sizing:border-box} body{font-family:'Sarabun','TH Sarabun New',system-ui,sans-serif;color:#1e293b;font-size:12px;margin:0;padding:24px;background:#f8fafc}
      .sheet{background:#fff;max-width:900px;margin:0 auto 18px;padding:26px 30px;border-radius:10px;box-shadow:0 1px 4px rgba(0,0,0,.08)}
      h1{font-size:19px;margin:0 0 2px} h2{font-size:15px;margin:22px 0 8px;border-left:4px solid #ea580c;padding-left:8px}
      h3{font-size:13px;margin:14px 0 6px;color:#475569}
      .muted{color:#64748b;font-size:11px} .right{text-align:right} .mono{font-variant-numeric:tabular-nums;font-family:ui-monospace,monospace}
      table{width:100%;border-collapse:collapse;margin:6px 0 10px;font-size:11.5px}
      th,td{padding:5px 7px;border-bottom:1px solid #eef2f7;text-align:left}
      th{color:#64748b;font-size:10px;text-transform:uppercase;border-bottom:1.5px solid #e2e8f0}
      tfoot td{border-top:1.5px solid #cbd5e1;font-weight:700}
      .kpis{display:flex;gap:10px;flex-wrap:wrap;margin:10px 0}
      .kpi{flex:1;min-width:120px;border:1px solid #e5e7eb;border-radius:8px;padding:8px 10px}
      .kpi .l{font-size:10px;color:#64748b} .kpi .v{font-size:17px;font-weight:700}
      .pos{color:#059669} .neg{color:#dc2626} .badge{padding:1px 7px;border-radius:99px;font-size:10px;font-weight:600}
      .b-ok{background:#dcfce7;color:#166534}.b-warn{background:#fef9c3;color:#854d0e}.b-err{background:#fee2e2;color:#b91c1c}.b-mut{background:#f1f5f9;color:#475569}
      .toolbar{max-width:900px;margin:0 auto 14px;text-align:right}
      .btn{background:#ea580c;color:#fff;border:0;border-radius:8px;padding:9px 18px;font-size:13px;cursor:pointer}
      .note{background:#fff7ed;border-left:3px solid #f59e0b;padding:6px 10px;border-radius:4px;margin:4px 0}
      .sign{display:flex;gap:40px;margin-top:34px} .sign div{flex:1;border-top:1px solid #94a3b8;padding-top:5px;text-align:center;font-size:11px;color:#475569}
      @media print{body{background:#fff;padding:0}.toolbar{display:none}.sheet{box-shadow:none;margin:0;max-width:none;border-radius:0;page-break-after:always}}
    </style>"""

    def status_badge(s):
        cls = {"matched": "b-ok", "manual": "b-ok", "variance": "b-err", "ext_only": "b-warn",
               "pos_only": "b-warn", "no_source": "b-mut"}.get(s, "b-mut")
        return f'<span class="badge {cls}">{_STATUS_TH.get(s, s)}</span>'

    scope = "ทุกสาขา" if not branch_id else (rows[0]["branch_name"] if rows else "—")
    parts = [f"<!doctype html><html lang='th'><head><meta charset='utf-8'>",
             "<link href='https://fonts.googleapis.com/css2?family=Sarabun:wght@400;600;700&display=swap' rel='stylesheet'>",
             css, "</head><body>",
             "<div class='toolbar'><button class='btn' onclick='window.print()'>🖨️ พิมพ์ / บันทึกเป็น PDF</button></div>"]

    if not rows:
        parts.append(f"<div class='sheet'><h1>รายงานกระทบยอดรายวัน</h1><p class='muted'>{day} · {scope}</p>"
                     "<p>ไม่มีข้อมูลการขายสำหรับวันนี้</p></div>")
        parts.append("</body></html>")
        return "".join(parts)

    for b in rows:
        bid = b["branch_id"]
        parts.append("<div class='sheet'>")
        parts.append(f"<h1>รายงานกระทบยอดรายวัน — หลักฐานการกระทบยอด</h1>"
                     f"<p class='muted'>สาขา <b>{b['branch_name']}</b> · วันที่ <b>{day}</b> · "
                     f"ออกรายงานเมื่อ {_dt.now().strftime('%Y-%m-%d %H:%M')}</p>")
        var = b["variance"]; vc = "pos" if abs(var) < 1 else "neg"
        parts.append("<div class='kpis'>"
                     f"<div class='kpi'><div class='l'>ยอด POS (ที่กระทบได้)</div><div class='v mono'>{_m(b['pos_total'])}</div></div>"
                     f"<div class='kpi'><div class='l'>ยอดจากเอกสาร</div><div class='v mono'>{_m(b['ext_total'])}</div></div>"
                     f"<div class='kpi'><div class='l'>ส่วนต่าง</div><div class='v mono {vc}'>{_m(var)}</div></div>"
                     f"<div class='kpi'><div class='l'>รายการมีปัญหา</div><div class='v'>{b['issues']}</div></div>"
                     "</div>")

        # 1) Category reconciliation (POS vs documents)
        parts.append("<h2>① กระทบยอดตามช่องทาง (POS เทียบเอกสาร)</h2>"
                     "<table><thead><tr><th>ช่องทาง</th><th class='right'>POS</th><th class='right'>เอกสาร</th>"
                     "<th class='right'>กระทบมือ</th><th class='right'>ส่วนต่าง</th><th>สถานะ</th></tr></thead><tbody>")
        for ck, cell in b["cells"].items():
            lbl = (cmeta.get(ck) or {}).get("label", ck)
            v = cell["variance"]
            parts.append(f"<tr><td>{lbl}</td><td class='right mono'>{_m(cell['pos'])}</td>"
                         f"<td class='right mono'>{_m(cell['ext'])}</td><td class='right mono'>{_m(cell.get('manual'))}</td>"
                         f"<td class='right mono {'pos' if abs(v)<1 else 'neg'}'>{_m(v)}</td><td>{status_badge(cell['status'])}</td></tr>")
        parts.append(f"<tr><td colspan='6' class='muted' style='border:0'></td></tr></tbody><tfoot><tr><td>รวม</td>"
                     f"<td class='right mono'>{_m(b['pos_total'])}</td><td class='right mono'>{_m(b['ext_total'])}</td>"
                     f"<td></td><td class='right mono {vc}'>{_m(var)}</td><td></td></tr></tfoot></table>")

        # 2) Fees & VAT — line level
        cc_l, gr_l, ln_l = cc.get(bid, []), grab.get(bid, []), lnmn.get(bid, [])
        tot = {"gross": 0.0, "fee": 0.0, "vat": 0.0, "promo": 0.0, "refund": 0.0, "net": 0.0}
        feerows = []
        for r in cc_l:
            fee, _ = _eff_fee_vat(r, "mdr_fee", None); vat = r["vat_manual"] or 0.0
            gross = float(r["gross_amount"] or 0); refund = float(r["refund_amount"] or 0)
            net = gross - refund - fee - vat
            tot["gross"] += gross; tot["fee"] += fee; tot["vat"] += vat; tot["refund"] += refund; tot["net"] += net
            feerows.append(("CREDIT " + (r["acquirer"] or ""), gross, refund, fee, vat, 0.0, net,
                            "MDR" + (" (แก้มือ)" if r["fee_manual"] is not None else "")))
        for r in gr_l:
            fee, _ = _eff_fee_vat(r, "commission_amt", None); vat = r["vat_manual"] or 0.0
            gross = float(r["food_amount"] or 0); promo = float(r["promo_deduct"] or 0)
            net = gross - fee - vat - promo
            tot["gross"] += gross; tot["fee"] += fee; tot["vat"] += vat; tot["promo"] += promo; tot["net"] += net
            feerows.append(("GRAB", gross, 0.0, fee, vat, promo, net, "ค่าคอมมิชชั่น"))
        for r in ln_l:
            fee, vat = _eff_fee_vat(r, "commission_amt", "vat_on_commission")
            gross = float(r["food_amount"] or 0); promo = float(r["promo_deduct"] or 0)
            net = gross - fee - vat - promo
            tot["gross"] += gross; tot["fee"] += fee; tot["vat"] += vat; tot["promo"] += promo; tot["net"] += net
            feerows.append(("LINEMAN", gross, 0.0, fee, vat, promo, net, "ค่าคอมมิชชั่น + VAT"))
        if feerows:
            parts.append("<h2>② ค่าธรรมเนียม &amp; VAT (รายบรรทัดจากเอกสาร)</h2>"
                         "<table><thead><tr><th>ช่องทาง</th><th class='right'>ยอดเต็ม</th><th class='right'>คืนเงิน</th>"
                         "<th class='right'>ค่าธรรมเนียม</th><th class='right'>VAT</th><th class='right'>โปรโมชัน</th>"
                         "<th class='right'>สุทธิ</th><th>หมายเหตุ</th></tr></thead><tbody>")
            for nm, g, rf, fe, va, pr, nt, note in feerows:
                parts.append(f"<tr><td>{nm}</td><td class='right mono'>{_m(g)}</td><td class='right mono'>{_m(rf)}</td>"
                             f"<td class='right mono neg'>{_m(fe)}</td><td class='right mono neg'>{_m(va)}</td>"
                             f"<td class='right mono neg'>{_m(pr)}</td><td class='right mono'>{_m(nt)}</td><td class='muted'>{note}</td></tr>")
            parts.append(f"</tbody><tfoot><tr><td>รวม</td><td class='right mono'>{_m(tot['gross'])}</td>"
                         f"<td class='right mono'>{_m(tot['refund'])}</td><td class='right mono neg'>{_m(tot['fee'])}</td>"
                         f"<td class='right mono neg'>{_m(tot['vat'])}</td><td class='right mono neg'>{_m(tot['promo'])}</td>"
                         f"<td class='right mono'>{_m(tot['net'])}</td><td></td></tr></tfoot></table>"
                         f"<p class='muted'>สุทธิ = ยอดเต็ม − คืนเงิน − ค่าธรรมเนียม − VAT − โปรโมชัน · "
                         f"ค่าธรรมเนียมรวม {_m(tot['fee'])} · VAT รวม {_m(tot['vat'])}</p>")

        # 3) Manual matches — wrong-category reclassification with reason
        ms = mm.get(bid, [])
        if ms:
            parts.append("<h2>③ การกระทบมือ (สาขาบันทึกผิดประเภท / จับคู่เอง)</h2>")
            parts.append("<table><thead><tr><th>ช่องทาง</th><th>ธนาคาร</th><th class='right'>ยอดที่กระทบ (วันนี้)</th>"
                         "<th>เหตุผล / หมายเหตุ</th><th>โดย</th></tr></thead><tbody>")
            for r, amt in ms:
                parts.append(f"<tr><td>{r['category']}</td><td>{r['bank'] or '—'}</td>"
                             f"<td class='right mono'>{_m(amt)}</td><td>{(r['note'] or '—')}</td>"
                             f"<td class='muted'>{(r['matched_by'] or '—')}<br>{(r['matched_at'] or '')[:16]}</td></tr>")
            parts.append("</tbody></table>")

        # 4) Document evidence — the actual lines used
        parts.append("<h2>④ เอกสารที่ใช้อ้างอิง (หลักฐาน)</h2>")
        bk = bank.get(bid, [])
        if bk:
            parts.append("<h3>รายการเดินบัญชีธนาคาร</h3><table><thead><tr><th>ช่องทาง</th><th>บัญชี</th>"
                         "<th class='right'>เงินเข้า</th></tr></thead><tbody>")
            for r in bk:
                parts.append(f"<tr><td>{r['channel']}</td><td class='mono'>{r['account_no'] or '—'}</td>"
                             f"<td class='right mono'>{_m(r['credit'])}</td></tr>")
            parts.append("</tbody></table>")
        if cc_l:
            parts.append("<h3>สรุปยอดบัตรเครดิต (settlement)</h3><table><thead><tr><th>ผู้รับบัตร</th>"
                         "<th>MID/Terminal</th><th class='right'>ยอดเต็ม</th><th class='right'>สุทธิ</th></tr></thead><tbody>")
            for r in cc_l:
                parts.append(f"<tr><td>{r['acquirer'] or '—'}</td><td class='mono'>{(r['merchant_id'] or '—')}/{(r['terminal_id'] or '—')}</td>"
                             f"<td class='right mono'>{_m(r['gross_amount'])}</td><td class='right mono'>{_m(r['net_amount'])}</td></tr>")
            parts.append("</tbody></table>")
        if gr_l or ln_l:
            parts.append("<h3>รายงาน Grab / Line Man (ออเดอร์สำเร็จ)</h3><table><thead><tr><th>ผู้ให้บริการ</th>"
                         "<th class='right'>จำนวนออเดอร์</th><th class='right'>ยอดอาหาร</th></tr></thead><tbody>")
            if gr_l: parts.append(f"<tr><td>Grab</td><td class='right'>{len(gr_l)}</td><td class='right mono'>{_m(sum(float(r['food_amount'] or 0) for r in gr_l))}</td></tr>")
            if ln_l: parts.append(f"<tr><td>Line Man</td><td class='right'>{len(ln_l)}</td><td class='right mono'>{_m(sum(float(r['food_amount'] or 0) for r in ln_l))}</td></tr>")
            parts.append("</tbody></table>")
        if not (bk or cc_l or gr_l or ln_l):
            parts.append("<p class='note'>ยังไม่มีเอกสารอัปโหลดสำหรับวันนี้ — ยอดที่แสดงเป็นฝั่ง POS เท่านั้น</p>")

        parts.append("<div class='sign'><div>ผู้จัดทำ (กระทบยอด)</div><div>ผู้ตรวจสอบ</div><div>ผู้อนุมัติ</div></div>")
        parts.append("</div>")  # sheet

    parts.append("</body></html>")
    return "".join(parts)


@app.get("/api/daily-report")
def daily_report(day: str, branch_id: int | None = None):
    """Print-to-PDF daily reconciliation evidence: POS vs documents, manual
    matches (wrong-category reclass + reason), all fees, and VAT — per the
    selected day. Omit branch_id for an all-branches pack (one page each)."""
    if not _DAY_RE.match(day or ""):
        raise HTTPException(400, "day must be in 'YYYY-MM-DD' format.")
    with get_db() as conn:
        html = _daily_report_html(conn, day, branch_id)
    return Response(content=html, media_type="text/html; charset=utf-8")


# ── Transaction-level drill-down (mapped / unmapped codes + statement lines) ───

@app.get("/api/drill")
def drill(branch_id: int, category: str | None = None, bank: str | None = None,
          day: str | None = None, period_month: str | None = None):
    """Day-scoped (or month-scoped) drill into one branch's payment codes and the
    matching statement lines, so staff can see which codes are mapped vs unmapped
    and act on the unmapped. `category`/`bank` narrow to a single lane."""
    if day and not _DAY_RE.match(day):
        raise HTTPException(400, "day must be in 'YYYY-MM-DD' format.")
    if not day and not (period_month and _PERIOD_RE.match(period_month)):
        raise HTTPException(400, "pass a valid day (YYYY-MM-DD) or period_month (YYYY-MM).")
    with get_db() as conn:
        _validate_master_branch(conn, branch_id)
        codes = pos_codes_scope(conn, branch_id, day=day, period_month=period_month)
        for c in codes:
            c["in_lane"] = bool(category) and c["category"] == category \
                and (not bank or bank in ("*", "") or c["bank"] == bank)
        lane_codes = [c for c in codes if c["in_lane"]] if category else codes
        attention  = [c for c in codes if c["needs_map"]]

        ext_lines = []
        if category:
            ext_lines = lane_lines(conn, branch_id, (day or period_month)[:7],
                                   category, bank or "NONE", day)

        pos_total = round(sum(c["amount"] for c in lane_codes if c["reconcile"]), 2)
        ext_total = round(sum(l["amount"] for l in ext_lines), 2)
        bname = conn.execute("SELECT name FROM branches WHERE branch_id=?",
                             (branch_id,)).fetchone()["name"]
        return {
            "branch_id": branch_id, "branch_name": bname,
            "day": day, "period_month": period_month,
            "category": category, "bank": bank,
            "codes": codes, "lane_codes": lane_codes, "attention": attention,
            "ext_lines": ext_lines,
            "pos_total": pos_total, "ext_total": ext_total,
            "variance": round(ext_total - pos_total, 2),
        }


# ── Manual reconciliation (tick-to-match, reversible) ──────────────────────────

_EXT_TABLES = {"recon_bank_lines", "recon_cc_lines", "recon_grab_lines", "recon_lineman_lines"}


class ManualMatch(BaseModel):
    branch_id:    int
    category:     str
    bank:         str
    period_month: str
    pos_days:     list[dict] = []   # [{day, amount}]
    ext:          list[dict] = []   # [{table, id, amount}]
    note:         str = ""
    matched_by:   str = ""


@app.get("/api/manual")
def manual_list(branch_id: int, category: str, bank: str, period_month: str):
    _validate_period(period_month)
    with get_db() as conn:
        _validate_master_branch(conn, branch_id)
        pos = lane_pos_days(conn, branch_id, period_month, category, bank)
        ext = lane_ext_lines(conn, branch_id, period_month, category, bank)

        matches, consumed = [], {}
        for m in conn.execute(
            "SELECT * FROM recon_manual_match WHERE branch_id=? AND period_month=? "
            "AND category=? AND bank=? ORDER BY matched_at DESC",
            (branch_id, period_month, category, bank)):
            pd = json.loads(m["pos_days"] or "[]")
            for x in pd:
                consumed[x["day"]] = consumed.get(x["day"], 0.0) + float(x.get("amount") or 0)
            lines = [dict(r) for r in conn.execute(
                "SELECT ext_table, ext_id, amount FROM recon_manual_match_line WHERE match_id=?",
                (m["match_id"],))]
            matches.append({**dict(m), "pos_days": pd, "lines": lines})

        for d in pos:
            d["consumed"]  = round(consumed.get(d["day"], 0.0), 2)
            d["remaining"] = round(d["total"] - d["consumed"], 2)

        return {"branch_id": branch_id, "category": category, "bank": bank,
                "period_month": period_month, "pos_days": pos, "ext_lines": ext,
                "matches": matches}


@app.post("/api/manual", dependencies=[Depends(require_write_token)])
def manual_create(body: ManualMatch):
    _validate_period(body.period_month)
    if not body.pos_days or not body.ext:
        raise HTTPException(400, "Tick at least one POS day and one statement line.")
    pos_amount = round(sum(float(x.get("amount") or 0) for x in body.pos_days), 2)
    ext_amount = round(sum(float(x.get("amount") or 0) for x in body.ext), 2)
    with get_db() as conn:
        _validate_master_branch(conn, body.branch_id)
        _assert_period_open(conn, body.branch_id, body.period_month)

        # Partial allocation guard: a day can't be allocated beyond what's left.
        totals = {d["day"]: d["total"] for d in
                  lane_pos_days(conn, body.branch_id, body.period_month, body.category, body.bank)}
        already: dict = {}
        for m in conn.execute(
            "SELECT pos_days FROM recon_manual_match WHERE branch_id=? AND period_month=? "
            "AND category=? AND bank=?",
            (body.branch_id, body.period_month, body.category, body.bank)):
            for x in json.loads(m["pos_days"] or "[]"):
                already[x["day"]] = already.get(x["day"], 0.0) + float(x.get("amount") or 0)
        for x in body.pos_days:
            day, amt = x.get("day"), float(x.get("amount") or 0)
            if amt <= 0:
                raise HTTPException(400, f"amount for {day} must be positive.")
            remaining = round(totals.get(day, 0.0) - already.get(day, 0.0), 2)
            if amt > remaining + 0.5:
                raise HTTPException(
                    409, f"{day}: amount {amt:.0f} exceeds remaining {remaining:.0f}.")
        cur = conn.execute("""
            INSERT INTO recon_manual_match
                (branch_id, category, bank, period_month, pos_amount, ext_amount,
                 variance, pos_days, note, matched_by)
            VALUES (?,?,?,?,?,?,?,?,?,?)
        """, (body.branch_id, body.category, body.bank, body.period_month,
              pos_amount, ext_amount, round(ext_amount - pos_amount, 2),
              json.dumps(body.pos_days), body.note, body.matched_by or "staff"))
        mid = cur.lastrowid
        for e in body.ext:
            tbl = e.get("table")
            if tbl not in _EXT_TABLES:
                raise HTTPException(400, f"bad ext table {tbl!r}")
            eid = int(e.get("id"))
            conn.execute("INSERT INTO recon_manual_match_line (match_id, ext_table, ext_id, amount) "
                         "VALUES (?,?,?,?)", (mid, tbl, eid, float(e.get("amount") or 0)))
            conn.execute(f"UPDATE {tbl} SET match_status='manual' WHERE id=?", (eid,))
        _audit(conn, "manual_match", mid, "create", body.matched_by or "staff",
               [{"field": "match", "old": None,
                 "new": f"POS {pos_amount:.0f} ↔ EXT {ext_amount:.0f} (var {ext_amount-pos_amount:.0f})"}])
        conn.commit()
    return {"match_id": mid, "pos_amount": pos_amount, "ext_amount": ext_amount,
            "variance": round(ext_amount - pos_amount, 2)}


@app.delete("/api/manual/{match_id}", dependencies=[Depends(require_write_token)])
def manual_delete(match_id: int):
    with get_db() as conn:
        m = conn.execute("SELECT branch_id, period_month FROM recon_manual_match WHERE match_id=?",
                         (match_id,)).fetchone()
        if not m:
            raise HTTPException(404)
        _assert_period_open(conn, m["branch_id"], m["period_month"])
        for ln in conn.execute("SELECT ext_table, ext_id FROM recon_manual_match_line WHERE match_id=?",
                               (match_id,)):
            if ln["ext_table"] in _EXT_TABLES:
                conn.execute(f"UPDATE {ln['ext_table']} SET match_status='unmatched' "
                             f"WHERE id=? AND match_status='manual'", (ln["ext_id"],))
        conn.execute("DELETE FROM recon_manual_match_line WHERE match_id=?", (match_id,))
        conn.execute("DELETE FROM recon_manual_match WHERE match_id=?", (match_id,))
        _audit(conn, "manual_match", match_id, "delete", "staff",
               [{"field": "match", "old": "matched", "new": None}])
        conn.commit()
    return {"deleted": match_id}


# ── Cross-branch overview ─────────────────────────────────────────────────────

@app.get("/api/overview")
def overview(period_month: str):
    with get_db() as conn:
        branches = conn.execute(
            f"SELECT branch_id, name FROM branches WHERE {_MASTER_ONLY} ORDER BY name"
        ).fetchall()

        result = []
        for b in branches:
            bid = b["branch_id"]

            # Reconcilable POS tender total vs external statement total (same engine
            # as the session view, so overview and detail agree).
            rows, _ = category_reconciliation(conn, bid, period_month)
            pos_total = round(sum(r["pos_amount"] for r in rows), 2)
            ext_total = round(sum(r["ext_amount"] for r in rows), 2)

            session = conn.execute(
                "SELECT session_id, status FROM recon_sessions WHERE branch_id=? AND period_month=?",
                (bid, period_month)
            ).fetchone()

            # Open issues to follow up: pending discrepancies (if a session exists)
            # + lanes that aren't matched in the live reconciliation.
            open_issues = 0
            if session:
                open_issues = conn.execute(
                    "SELECT COUNT(*) FROM recon_discrepancies WHERE session_id=? AND resolution='pending'",
                    (session["session_id"],)).fetchone()[0]
            unmatched = sum(1 for r in rows if r["status"] != "matched")

            result.append({
                "branch_id":   bid,
                "branch_name": b["name"],
                "pos_total":   pos_total,
                "ext_total":   ext_total,
                "variance":    round(ext_total - pos_total, 2),
                "session_id":  session["session_id"] if session else None,
                "status":      session["status"] if session else "no_session",
                "open_issues": open_issues,
                "unmatched":   unmatched,
            })

    return result


# ── Session trend (per-day + weekly rollup) ───────────────────────────────────

@app.get("/api/sessions/{session_id}/trend")
def session_trend(session_id: int):
    """Per-day and per-week recon health for one session.
    Reuses the daily_reconciliation engine so numbers agree with the Daily tab."""
    with get_db() as conn:
        s = conn.execute(
            "SELECT branch_id, period_month FROM recon_sessions WHERE session_id=?",
            (session_id,)).fetchone()
        if not s:
            raise HTTPException(404)
        data = daily_reconciliation(conn, s["branch_id"], s["period_month"])

    daily_out: list[dict] = []
    for dr in data.get("days", []):
        lanes       = dr.get("lanes", [])
        matched     = dr.get("matched", 0)
        total_lanes = len(lanes)
        pos         = float(dr.get("pos_total") or 0)
        ext         = float(dr.get("ext_total") or 0)
        lane_pct    = round(matched / total_lanes * 100) if total_lanes else 0
        cov_pct     = min(100, round(ext / pos * 100)) if pos else 0
        daily_out.append({
            "day":         dr["day"],
            "pos":         round(pos, 2),
            "ext":         round(ext, 2),
            "matched":     matched,
            "total_lanes": total_lanes,
            "lane_pct":    lane_pct,
            "cov_pct":     cov_pct,
        })

    # Weekly rollup: W1=days 1-7, W2=8-14, W3=15-21, W4=22+
    week_data: dict = {}
    for d in daily_out:
        day_num = int(d["day"][8:10])
        week_n  = min((day_num - 1) // 7 + 1, 4)
        wk      = f"W{week_n}"
        if wk not in week_data:
            week_data[wk] = {"week": wk, "week_n": week_n,
                              "pos": 0.0, "ext": 0.0, "matched": 0, "lanes": 0}
        w = week_data[wk]
        w["pos"]     += d["pos"]
        w["ext"]     += d["ext"]
        w["matched"] += d["matched"]
        w["lanes"]   += d["total_lanes"]

    weekly_out = []
    for w in sorted(week_data.values(), key=lambda x: x["week_n"]):
        w["pos"]      = round(w["pos"], 2)
        w["ext"]      = round(w["ext"], 2)
        w["lane_pct"] = round(w["matched"] / w["lanes"] * 100) if w["lanes"] else 0
        w["cov_pct"]  = min(100, round(w["ext"] / w["pos"] * 100)) if w["pos"] else 0
        weekly_out.append(w)

    return {"session_id": session_id, "daily": daily_out, "weekly": weekly_out}


# ── Audit log ─────────────────────────────────────────────────────────────────

@app.get("/api/audit")
def get_audit_log(
    entity:     str | None = None,
    entity_id:  int | None = None,
    session_id: int | None = None,
    limit:      int        = 100,
):
    """Field-level change log.
    Pass session_id to get all events scoped to one session (session-level + its discrepancies).
    Pass entity + entity_id for a specific record's history."""
    cap = min(limit, 500)
    with get_db() as conn:
        if session_id and not entity and not entity_id:
            rows = conn.execute("""
                SELECT * FROM recon_audit_log
                WHERE (entity='session' AND entity_id=?)
                   OR (entity='discrepancy'
                       AND entity_id IN (SELECT id FROM recon_discrepancies WHERE session_id=?))
                ORDER BY ts DESC, id DESC LIMIT ?
            """, (session_id, session_id, cap)).fetchall()
        else:
            filters: list[str] = []
            params:  list      = []
            if entity:
                filters.append("entity=?");    params.append(entity)
            if entity_id:
                filters.append("entity_id=?"); params.append(entity_id)
            where = ("WHERE " + " AND ".join(filters)) if filters else ""
            rows = conn.execute(f"""
                SELECT * FROM recon_audit_log {where}
                ORDER BY ts DESC, id DESC LIMIT ?
            """, params + [cap]).fetchall()
        return [dict(r) for r in rows]


# ── Diagnostic endpoints ──────────────────────────────────────────────────────
# Use these to inspect what's actually in the database before tuning matchers.

@app.get("/api/diagnostics/payment-codes")
def diag_payment_codes(period_month: str, branch_id: int | None = None):
    """
    Show all PaymentCode values in pos_payment_month for a given month.
    Use this to verify keyword patterns match your actual Easy POS codes.
    Example: GET /api/diagnostics/payment-codes?period_month=2025-12
    """
    with get_db() as conn:
        try:
            filters = ["period_month=?"]
            params: list = [period_month]
            if branch_id:
                filters.append("branch_id=?"); params.append(branch_id)
            rows = conn.execute(f"""
                SELECT payment_code, SUM(amount) amount, SUM(txn_count) txn_count,
                       COUNT(DISTINCT branch_id) branches
                FROM pos_payment_month
                WHERE {' AND '.join(filters)}
                GROUP BY payment_code
                ORDER BY SUM(amount) DESC
            """, params).fetchall()
            return {"period_month": period_month, "codes": [dict(r) for r in rows]}
        except Exception as exc:
            return {"error": str(exc), "hint": "pos_payment_month not found — run sync_mssql.py first"}


@app.get("/api/diagnostics/customer-types")
def diag_customer_types(period_month: str, branch_id: int | None = None):
    """
    Show all CustomerTypeName values in pos_customer_type_month for a given month.
    Use this to verify Grab/Lineman type names match your POS configuration.
    Example: GET /api/diagnostics/customer-types?period_month=2025-12
    """
    with get_db() as conn:
        try:
            filters = ["period_month=?"]
            params: list = [period_month]
            if branch_id:
                filters.append("branch_id=?"); params.append(branch_id)
            rows = conn.execute(f"""
                SELECT type_name, SUM(customers) customers, ROUND(SUM(net)/1.07) net_ex_vat,
                       COUNT(DISTINCT branch_id) branches
                FROM pos_customer_type_month
                WHERE {' AND '.join(filters)}
                GROUP BY type_name
                ORDER BY SUM(net) DESC
            """, params).fetchall()
            return {"period_month": period_month, "types": [dict(r) for r in rows]}
        except Exception as exc:
            return {"error": str(exc), "hint": "pos_customer_type_month not found — run sync_mssql.py first"}


@app.get("/api/diagnostics/excel-payment-cols")
def diag_excel_payment_cols(period_month: str, branch_id: int | None = None):
    """
    Show Easy POS Excel payment column totals from sales_month_summary.
    cash/credit_card/qr are only populated after re-importing with the fixed ingest.
    Use to verify col 40 (qr field) actually contains QR/PromptPay data.
    """
    with get_db() as conn:
        filters = ["period_month=?", "source='Easy'"]
        params: list = [period_month]
        if branch_id:
            filters.append("branch_id=?"); params.append(branch_id)
        rows = conn.execute(f"""
            SELECT b.name branch, s.cash, s.credit_card, s.transfer, s.qr, s.coupon, s.net_sales
            FROM sales_month_summary s
            JOIN branches b ON b.branch_id = s.branch_id
            WHERE {' AND '.join(filters)}
            ORDER BY b.name
        """, params).fetchall()
        return {"period_month": period_month, "rows": [dict(r) for r in rows]}


# ── Health ────────────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    with get_db() as conn:
        conn.execute("SELECT 1")
    return {"status": "ok"}
