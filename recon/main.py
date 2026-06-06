"""
Revenue Reconciliation API  –  FastAPI app
Shares the same oshinei.db SQLite file as the main cost dashboard.

Run:
    uvicorn recon.main:app --port 8001 --reload

Environment variables:
    OSHINEI_DB   path to the shared oshinei.db  (default: ../backend/oshinei.db)
"""

from __future__ import annotations
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .matcher import run_auto_match
from .parsers import (
    parse_bank_statement,
    parse_cc_settlement,
    parse_grab_report,
    parse_lineman_report,
)

# ── Schema setup ──────────────────────────────────────────────────────────────

_DB_PATH    = Path(os.getenv("OSHINEI_DB", "backend/oshinei.db"))
_SCHEMA_SQL = Path(__file__).parent / "schema.sql"
_TOLERANCE  = 5.0  # THB – amount diff threshold to auto-flag as discrepancy

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
        conn.executescript(_SCHEMA_SQL.read_text())
        conn.commit()


# ── Static frontend ───────────────────────────────────────────────────────────

_FRONTEND = Path(__file__).parent / "frontend"
if _FRONTEND.exists():
    app.mount("/app", StaticFiles(directory=str(_FRONTEND), html=True), name="frontend")


# ── Upload endpoints ──────────────────────────────────────────────────────────

SourceType = Literal["bank", "credit_card", "grab", "lineman"]


@app.post("/api/uploads/{source_type}/preview")
async def preview_upload(
    source_type: SourceType,
    file: UploadFile = File(...),
    branch_id: int  = Form(...),
    period_month: str = Form(...),   # 'YYYY-MM'
    provider: str   = Form(""),
):
    """
    Parse uploaded file and return rows for user preview (no DB write yet).
    The frontend shows the table; user clicks Confirm to actually save.
    """
    raw = await file.read()
    try:
        if source_type == "bank":
            prov, rows = parse_bank_statement(raw, provider or None)
        elif source_type == "credit_card":
            prov, rows = parse_cc_settlement(raw, provider or None)
        elif source_type == "grab":
            rows = parse_grab_report(raw)
            prov = "Grab"
        elif source_type == "lineman":
            rows = parse_lineman_report(raw)
            prov = "Lineman"
        else:
            raise HTTPException(400, "Unknown source_type")
    except Exception as exc:
        raise HTTPException(422, f"Parse error: {exc}")

    return {
        "provider":  prov,
        "row_count": len(rows),
        "preview":   rows[:50],   # first 50 rows for preview
        "columns":   list(rows[0].keys()) if rows else [],
    }


@app.post("/api/uploads/{source_type}/confirm")
async def confirm_upload(
    source_type: SourceType,
    file: UploadFile = File(...),
    branch_id: int   = Form(...),
    period_month: str = Form(...),
    provider: str    = Form(""),
):
    """Parse file and persist rows to the relevant recon_*_lines table."""
    raw = await file.read()
    try:
        if source_type == "bank":
            prov, rows = parse_bank_statement(raw, provider or None)
        elif source_type == "credit_card":
            prov, rows = parse_cc_settlement(raw, provider or None)
        elif source_type == "grab":
            rows = parse_grab_report(raw)
            prov = "Grab"
        else:
            rows = parse_lineman_report(raw)
            prov = "Lineman"
    except Exception as exc:
        raise HTTPException(422, f"Parse error: {exc}")

    _TABLE = {
        "bank":        "recon_bank_lines",
        "credit_card": "recon_cc_lines",
        "grab":        "recon_grab_lines",
        "lineman":     "recon_lineman_lines",
    }

    with get_db() as conn:
        cur = conn.execute("""
            INSERT INTO recon_uploads
                (source_type, branch_id, provider, file_name, period_month, row_count, status)
            VALUES (?,?,?,?,?,?,'confirmed')
        """, (source_type, branch_id, prov, file.filename, period_month, len(rows)))
        upload_id = cur.lastrowid

        table = _TABLE[source_type]
        for row in rows:
            row["upload_id"] = upload_id
            row["branch_id"] = branch_id
            cols = ", ".join(row.keys())
            placeholders = ", ".join("?" * len(row))
            conn.execute(f"INSERT INTO {table} ({cols}) VALUES ({placeholders})",
                         list(row.values()))
        conn.commit()

    return {"upload_id": upload_id, "rows_saved": len(rows), "provider": prov}


@app.delete("/api/uploads/{upload_id}")
def delete_upload(upload_id: int):
    """Remove an upload and all its lines (CASCADE handles child rows)."""
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


@app.post("/api/sessions")
def create_session(body: SessionCreate):
    with get_db() as conn:
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
            filters.append("s.branch_id=?"); params.append(branch_id)
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


@app.post("/api/sessions/{session_id}/run-match")
def run_match(session_id: int):
    """Trigger the auto-matching engine for this session."""
    with get_db() as conn:
        session = conn.execute(
            "SELECT branch_id, period_month FROM recon_sessions WHERE session_id=?",
            (session_id,)
        ).fetchone()
        if not session:
            raise HTTPException(404)
        result = run_auto_match(conn, session_id, session["branch_id"], session["period_month"])
    return result


@app.patch("/api/sessions/{session_id}/status")
def update_session_status(session_id: int, status: str = Form(...), approved_by: str = Form("")):
    valid = {"draft", "in_review", "approved", "closed"}
    if status not in valid:
        raise HTTPException(400, f"status must be one of {valid}")
    with get_db() as conn:
        conn.execute("""
            UPDATE recon_sessions SET status=?, approved_by=?, approved_at=datetime('now')
            WHERE session_id=?
        """, (status, approved_by, session_id))
        conn.commit()
    return {"session_id": session_id, "status": status}


# ── Reconciliation summary ────────────────────────────────────────────────────

@app.get("/api/sessions/{session_id}/summary")
def session_summary(session_id: int):
    """
    Returns a summary for the reconciliation dashboard:
      - POS totals (from sales_month_summary)
      - External totals per source
      - Match counts and match rate
      - Total variance
    """
    with get_db() as conn:
        session = conn.execute(
            "SELECT branch_id, period_month FROM recon_sessions WHERE session_id=?",
            (session_id,)
        ).fetchone()
        if not session:
            raise HTTPException(404)
        bid, pm = session["branch_id"], session["period_month"]

        # POS monthly totals
        pos = conn.execute("""
            SELECT
                COALESCE(SUM(net_sales),0)    AS total,
                COALESCE(SUM(cash),0)         AS cash,
                COALESCE(SUM(credit_card),0)  AS credit_card,
                COALESCE(SUM(transfer),0)     AS transfer,
                COALESCE(SUM(qr),0)           AS qr
            FROM sales_month_summary
            WHERE branch_id=? AND period_month=?
        """, (bid, pm)).fetchone()

        # External totals
        def ext_total(table, amount_col, date_col):
            return conn.execute(f"""
                SELECT COALESCE(SUM({amount_col}),0), COUNT(*),
                       SUM(CASE WHEN match_status='matched' THEN 1 ELSE 0 END)
                FROM {table}
                WHERE branch_id=? AND strftime('%Y-%m',{date_col})=?
            """, (bid, pm)).fetchone()

        bank  = ext_total("recon_bank_lines",    "credit",     "txn_date")
        cc    = ext_total("recon_cc_lines",      "net_amount", "settlement_date")
        grab  = ext_total("recon_grab_lines",    "net_payout", "order_date")
        lm    = ext_total("recon_lineman_lines", "net_payout", "order_date")

        disc_count = conn.execute(
            "SELECT COUNT(*) FROM recon_discrepancies WHERE session_id=? AND resolution='pending'",
            (session_id,)
        ).fetchone()[0]

        def summarise(row):
            total, count, matched = row
            return {"total": total, "lines": count, "matched": matched,
                    "unmatched": count - matched,
                    "match_rate": round(matched / count * 100, 1) if count else 0}

        return {
            "pos":                  dict(pos),
            "bank":                 summarise(bank),
            "credit_card":          summarise(cc),
            "grab":                 summarise(grab),
            "lineman":              summarise(lm),
            "pending_discrepancies": disc_count,
            "ext_total":            bank[0] + cc[0] + grab[0] + lm[0],
            "pos_total":            pos["total"],
            "variance":             (bank[0] + cc[0] + grab[0] + lm[0]) - pos["total"],
        }


# ── Discrepancy endpoints ─────────────────────────────────────────────────────

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


@app.patch("/api/discrepancies/{disc_id}/resolve")
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
        filters = ["m.session_id=?"]
        params  = [session_id]
        if source_type:
            filters.append("m.source_type=?"); params.append(source_type)
        rows = conn.execute(f"""
            SELECT * FROM recon_matches
            WHERE {' AND '.join(filters)}
            ORDER BY m.matched_at DESC
        """, params).fetchall()
        return [dict(r) for r in rows]


# ── Cross-branch overview ─────────────────────────────────────────────────────

@app.get("/api/overview")
def overview(period_month: str):
    """
    All branches × one month: show POS total vs sum of all uploaded externals.
    Useful as the landing page summary across 27 branches.
    """
    with get_db() as conn:
        branches = conn.execute(
            "SELECT branch_id, name FROM branches WHERE active=1 ORDER BY name"
        ).fetchall()
        result = []
        for b in branches:
            bid = b["branch_id"]
            pos = conn.execute("""
                SELECT COALESCE(SUM(net_sales),0) FROM sales_month_summary
                WHERE branch_id=? AND period_month=?
            """, (bid, period_month)).fetchone()[0]

            ext = conn.execute("""
                SELECT
                    (SELECT COALESCE(SUM(credit),0)     FROM recon_bank_lines    WHERE branch_id=? AND strftime('%Y-%m',txn_date)=?) +
                    (SELECT COALESCE(SUM(net_amount),0) FROM recon_cc_lines      WHERE branch_id=? AND strftime('%Y-%m',settlement_date)=?) +
                    (SELECT COALESCE(SUM(net_payout),0) FROM recon_grab_lines    WHERE branch_id=? AND strftime('%Y-%m',order_date)=?) +
                    (SELECT COALESCE(SUM(net_payout),0) FROM recon_lineman_lines WHERE branch_id=? AND strftime('%Y-%m',order_date)=?)
            """, (bid, period_month, bid, period_month, bid, period_month, bid, period_month)).fetchone()[0]

            session = conn.execute(
                "SELECT session_id, status FROM recon_sessions WHERE branch_id=? AND period_month=?",
                (bid, period_month)
            ).fetchone()

            result.append({
                "branch_id":   bid,
                "branch_name": b["name"],
                "pos_total":   pos,
                "ext_total":   ext,
                "variance":    ext - pos,
                "session_id":  session["session_id"] if session else None,
                "status":      session["status"] if session else "no_session",
            })
        return result
