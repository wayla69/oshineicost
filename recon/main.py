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
import os
import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from datetime import datetime as _dt
from typing import Literal

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .matcher import (
    category_reconciliation,
    daily_board,
    daily_reconciliation,
    lane_lines,
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


@app.on_event("startup")
def startup():
    with get_db() as conn:
        conn.executescript(_SCHEMA.read_text())
        _migrate(conn)
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


# ── Write auth + upload validation ────────────────────────────────────────────
# Mirrors the dashboard's gate: when OSHINEI_SEED_TOKEN is set (hosted deploys) a
# matching token is required via the `X-Upload-Token` header or `?token=`. Unset
# locally → writes are open, so the gate is invisible in dev.

MAX_UPLOAD_BYTES = 15 * 1024 * 1024          # 15 MB
_ALLOWED_EXT     = (".xlsx", ".xls")
_PERIOD_RE       = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


def require_write_token(token: str = Query(""), x_upload_token: str = Header(None)):
    expected = os.getenv("OSHINEI_SEED_TOKEN")
    if expected:
        provided = x_upload_token or token or ""
        if not hmac.compare_digest(provided, expected):
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
_BANKS      = {"KTB", "KBANK", "KTC", "PROMPTPAY", "NONE", "UNKNOWN"}


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
                GROUP BY p.payment_code
            """):
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
    if body.acquirer not in ("KTB", "KBANK", "KTC"):
        raise HTTPException(400, "acquirer must be KTB, KBANK or KTC")
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
    if body.acquirer not in ("KTB", "KBANK", "KTC"):
        raise HTTPException(400, "acquirer must be KTB, KBANK or KTC")
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
        categories = [{k: r[k] for k in
                       ("category", "bank", "pos_amount", "ext_amount", "ext_net", "variance", "status")}
                      for r in rows]
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
    """Min / max day with POS daily data — drives the date picker bounds + default."""
    with get_db() as conn:
        try:
            row = conn.execute("SELECT MIN(day) mn, MAX(day) mx FROM pos_payment_day").fetchone()
            return {"min_day": row["mn"], "max_day": row["mx"]}
        except sqlite3.OperationalError:
            return {"min_day": None, "max_day": None}


@app.get("/api/daily-board")
def daily_board_endpoint(day: str):
    if not _DAY_RE.match(day or ""):
        raise HTTPException(400, "day must be in 'YYYY-MM-DD' format.")
    with get_db() as conn:
        return daily_board(conn, day)


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
