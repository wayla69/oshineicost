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
import hmac
import os
import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .matcher import get_pos_payment_breakdown, run_auto_match
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


@app.on_event("startup")
def startup():
    with get_db() as conn:
        conn.executescript(_SCHEMA.read_text())
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


@app.get("/api/branches")
def list_branches():
    with get_db() as conn:
        rows = conn.execute(
            f"SELECT branch_id, name, branch_type FROM branches "
            f"WHERE {_MASTER_ONLY} ORDER BY name"
        ).fetchall()
        return [dict(r) for r in rows]


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

    with get_db() as conn:
        _validate_master_branch(conn, branch_id)
        prov, rows, skipped = _parse_or_422(source_type, raw, provider)

        cur = conn.execute("""
            INSERT INTO recon_uploads
                (source_type, branch_id, provider, file_name, period_month, row_count, status)
            VALUES (?,?,?,?,?,?,'confirmed')
        """, (source_type, branch_id, prov, file.filename, period_month, len(rows)))
        upload_id = cur.lastrowid

        table = _LINE_TABLE[source_type]
        for row in rows:
            row["upload_id"] = upload_id
            row["branch_id"] = branch_id
            cols         = ", ".join(row.keys())
            placeholders = ", ".join("?" * len(row))
            conn.execute(f"INSERT INTO {table} ({cols}) VALUES ({placeholders})",
                         list(row.values()))
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
        conn.execute("DELETE FROM recon_uploads WHERE upload_id=?", (upload_id,))
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
        conn.commit()
    return {"session_id": session_id, "status": status}


# ── Session summary ───────────────────────────────────────────────────────────

@app.get("/api/sessions/{session_id}/summary")
def session_summary(session_id: int):
    with get_db() as conn:
        session = conn.execute(
            "SELECT branch_id, period_month FROM recon_sessions WHERE session_id=?",
            (session_id,)
        ).fetchone()
        if not session:
            raise HTTPException(404)
        bid, pm = session["branch_id"], session["period_month"]

        # POS breakdown — uses pos_payment_month + pos_customer_type_month (MSSQL)
        # with fallback to sales_month_summary (Excel import)
        pos = get_pos_payment_breakdown(conn, bid, pm)

        def ext_row(table, amount_col, date_col):
            return conn.execute(f"""
                SELECT
                    COALESCE(SUM({amount_col}), 0),
                    COUNT(*),
                    SUM(CASE WHEN match_status='matched' THEN 1 ELSE 0 END)
                FROM {table}
                WHERE branch_id=? AND strftime('%Y-%m', {date_col})=?
                  AND match_status != 'excluded'
            """, (bid, pm)).fetchone()

        bank = ext_row("recon_bank_lines",    "credit",     "txn_date")
        cc   = ext_row("recon_cc_lines",      "net_amount", "settlement_date")
        grab = ext_row("recon_grab_lines",    "net_payout", "order_date")
        lm   = ext_row("recon_lineman_lines", "net_payout", "order_date")

        pending = conn.execute(
            "SELECT COUNT(*) FROM recon_discrepancies WHERE session_id=? AND resolution='pending'",
            (session_id,)
        ).fetchone()[0]

        def summarise(row):
            total, count, matched = row
            matched = matched or 0
            return {
                "total":      total,
                "lines":      count,
                "matched":    matched,
                "unmatched":  count - matched,
                "match_rate": round(matched / count * 100, 1) if count else 0,
            }

        ext_sum   = bank[0] + cc[0] + grab[0] + lm[0]
        pos_total = pos.get("pos_net", 0)
        return {
            "pos":                   pos,
            "bank":                  summarise(bank),
            "credit_card":           summarise(cc),
            "grab":                  summarise(grab),
            "lineman":               summarise(lm),
            "pending_discrepancies": pending,
            "ext_total":             ext_sum,
            "pos_total":             pos_total,
            "variance":              round(ext_sum - pos_total, 2),
        }


# ── Discrepancies ─────────────────────────────────────────────────────────────

@app.get("/api/sessions/{session_id}/discrepancies")
def list_discrepancies(session_id: int, resolution: str = "pending"):
    with get_db() as conn:
        rows = conn.execute("""
            SELECT * FROM recon_discrepancies
            WHERE session_id=? AND resolution=?
            ORDER BY ABS(COALESCE(variance, amount_external)) DESC
        """, (session_id, resolution)).fetchall()
        return [dict(r) for r in rows]


class ResolveBody(BaseModel):
    resolution:      Literal["accepted", "adjusted", "rejected"]
    resolution_note: str = ""
    resolved_by:     str = ""


@app.patch("/api/discrepancies/{disc_id}/resolve", dependencies=[Depends(require_write_token)])
def resolve_discrepancy(disc_id: int, body: ResolveBody):
    with get_db() as conn:
        conn.execute("""
            UPDATE recon_discrepancies
            SET resolution=?, resolution_note=?, resolved_by=?, resolved_at=datetime('now')
            WHERE id=?
        """, (body.resolution, body.resolution_note, body.resolved_by, disc_id))
        conn.commit()
    return {"id": disc_id, "resolution": body.resolution}


# ── Matches ───────────────────────────────────────────────────────────────────

@app.get("/api/sessions/{session_id}/matches")
def list_matches(session_id: int, source_type: str | None = None):
    with get_db() as conn:
        filters = ["session_id=?"]
        params  = [session_id]
        if source_type:
            filters.append("source_type=?"); params.append(source_type)
        rows = conn.execute(f"""
            SELECT * FROM recon_matches
            WHERE {' AND '.join(filters)}
            ORDER BY matched_at DESC
        """, params).fetchall()
        return [dict(r) for r in rows]


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

            pos_total = conn.execute("""
                SELECT COALESCE(SUM(net_sales), 0)
                FROM sales_month_summary
                WHERE branch_id=? AND period_month=?
            """, (bid, period_month)).fetchone()[0]

            ext_total = conn.execute("""
                SELECT
                    (SELECT COALESCE(SUM(credit),0)     FROM recon_bank_lines    WHERE branch_id=? AND strftime('%Y-%m',txn_date)=?)
                  + (SELECT COALESCE(SUM(net_amount),0) FROM recon_cc_lines      WHERE branch_id=? AND strftime('%Y-%m',settlement_date)=?)
                  + (SELECT COALESCE(SUM(net_payout),0) FROM recon_grab_lines    WHERE branch_id=? AND strftime('%Y-%m',order_date)=?)
                  + (SELECT COALESCE(SUM(net_payout),0) FROM recon_lineman_lines WHERE branch_id=? AND strftime('%Y-%m',order_date)=?)
            """, (bid, period_month) * 4).fetchone()[0]

            session = conn.execute(
                "SELECT session_id, status FROM recon_sessions WHERE branch_id=? AND period_month=?",
                (bid, period_month)
            ).fetchone()

            result.append({
                "branch_id":   bid,
                "branch_name": b["name"],
                "pos_total":   pos_total,
                "ext_total":   ext_total,
                "variance":    ext_total - pos_total,
                "session_id":  session["session_id"] if session else None,
                "status":      session["status"] if session else "no_session",
            })

    return result


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
