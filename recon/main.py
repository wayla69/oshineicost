"""
Revenue Reconciliation API  –  Standalone FastAPI app
Runs as a separate Fly.io app (oshinei-recon).

Own database : /data/recon.db   (Fly volume: recon_data)
POS data     : fetched from main app via Fly private network
               MAIN_API_URL = http://oshinei-dashboard.internal:8080

Run locally:
    uvicorn recon.main:app --port 8001 --reload

Environment variables:
    RECON_DB      path to recon.db           (default: /data/recon.db)
    MAIN_API_URL  base URL of main dashboard  (default: http://oshinei-dashboard.internal:8080)
"""

from __future__ import annotations
import os
import sqlite3
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path
from typing import Literal

import httpx
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .matcher import run_auto_match
from .parsers import (
    parse_bank_statement,
    parse_cc_settlement,
    parse_grab_report,
    parse_lineman_report,
)

# ── Config ────────────────────────────────────────────────────────────────────

_DB_PATH     = Path(os.getenv("RECON_DB", "/data/recon.db"))
_MAIN_API    = os.getenv("MAIN_API_URL", "http://oshinei-dashboard.internal:8080").rstrip("/")
_SCHEMA_SQL  = Path(__file__).parent / "schema.sql"

# ── HTTP client for calling the main dashboard API ────────────────────────────

_http: httpx.AsyncClient | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _http
    _http = httpx.AsyncClient(base_url=_MAIN_API, timeout=10.0)
    # Apply schema on startup
    with get_db() as conn:
        conn.executescript(_SCHEMA_SQL.read_text())
        conn.commit()
    yield
    await _http.aclose()


app = FastAPI(title="Oshinei Revenue Reconciliation", version="1.0.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@contextmanager
def get_db():
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(_DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
    finally:
        conn.close()


# ── Static frontend ───────────────────────────────────────────────────────────

_FRONTEND = Path(__file__).parent / "frontend"
if _FRONTEND.exists():
    app.mount("/app", StaticFiles(directory=str(_FRONTEND), html=True), name="frontend")


# ── Branch sync (pulls branch list from main app into local cache) ────────────

@app.post("/api/sync-branches")
async def sync_branches():
    """
    Pull the current branch list from the main dashboard and cache it locally.
    Call this once after deploying, and whenever branches change.
    """
    resp = await _http.get("/api/branches")
    resp.raise_for_status()
    branches = resp.json()   # expects [{"branch_id": 1, "name": "...", ...}, ...]

    with get_db() as conn:
        for b in branches:
            conn.execute("""
                INSERT INTO recon_branches (branch_id, name, branch_type, active, synced_at)
                VALUES (?,?,?,?,datetime('now'))
                ON CONFLICT(branch_id) DO UPDATE SET
                    name=excluded.name,
                    branch_type=excluded.branch_type,
                    active=excluded.active,
                    synced_at=excluded.synced_at
            """, (b["branch_id"], b["name"], b.get("branch_type","master"), b.get("active",1)))
        conn.commit()

    return {"synced": len(branches)}


@app.get("/api/branches")
def list_branches():
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM recon_branches WHERE active=1 ORDER BY name"
        ).fetchall()
        return [dict(r) for r in rows]


# ── POS data proxy (fetches from main app and caches in recon_pos_daily) ──────

@app.post("/api/sync-pos")
async def sync_pos(branch_id: int = Form(...), period_month: str = Form(...)):
    """
    Pull daily POS totals from the main app for a branch+month and cache locally.
    Uses the main app's /api/analysis/daily endpoint.
    Cached in recon_pos_daily for use by the auto-matcher.
    """
    # Fetch daily breakdown from main dashboard
    resp = await _http.get(
        "/api/analysis/daily",
        params={"branch_id": branch_id, "period_month": period_month}
    )
    if resp.status_code != 200:
        # Fallback: try monthly summary only
        monthly_resp = await _http.get(
            "/api/sales/monthly",
            params={"branch_id": branch_id, "period_month": period_month}
        )
        monthly_resp.raise_for_status()
        data = monthly_resp.json()
        with get_db() as conn:
            conn.execute("""
                INSERT INTO recon_pos_monthly
                    (branch_id, period_month, cash, credit_card, transfer, qr, net_sales,
                     txn_count, refreshed_at)
                VALUES (?,?,?,?,?,?,?,?,datetime('now'))
                ON CONFLICT(branch_id, period_month) DO UPDATE SET
                    cash=excluded.cash, credit_card=excluded.credit_card,
                    transfer=excluded.transfer, qr=excluded.qr,
                    net_sales=excluded.net_sales, txn_count=excluded.txn_count,
                    refreshed_at=excluded.refreshed_at
            """, (
                branch_id, period_month,
                data.get("cash", 0), data.get("credit_card", 0),
                data.get("transfer", 0), data.get("qr", 0),
                data.get("net_sales", 0), data.get("transaction_count", 0),
            ))
            conn.commit()
        return {"synced": "monthly_only", "branch_id": branch_id, "period_month": period_month}

    daily_data = resp.json()   # expects list of {date, total, txn_count, ...}

    with get_db() as conn:
        conn.execute(
            "DELETE FROM recon_pos_daily WHERE branch_id=? AND txn_date LIKE ?",
            (branch_id, f"{period_month}%")
        )
        for day in daily_data:
            conn.execute("""
                INSERT INTO recon_pos_daily
                    (branch_id, txn_date, cash, credit_card, transfer, qr, total, txn_count)
                VALUES (?,?,?,?,?,?,?,?)
                ON CONFLICT(branch_id, txn_date) DO UPDATE SET
                    total=excluded.total, txn_count=excluded.txn_count,
                    refreshed_at=datetime('now')
            """, (
                branch_id,
                day.get("date") or day.get("txn_date"),
                day.get("cash", 0), day.get("credit_card", 0),
                day.get("transfer", 0), day.get("qr", 0),
                day.get("total") or day.get("net_sales", 0),
                day.get("txn_count") or day.get("transaction_count", 0),
            ))
        conn.commit()

    return {"synced": len(daily_data), "branch_id": branch_id, "period_month": period_month}


async def get_pos_monthly_from_main(branch_id: int, period_month: str) -> dict:
    """Fetch monthly payment breakdown from main app — used for overview comparison."""
    resp = await _http.get(
        "/api/sales/monthly",
        params={"branch_id": branch_id, "period_month": period_month}
    )
    if resp.status_code != 200:
        return {}
    return resp.json()


# ── Upload endpoints ──────────────────────────────────────────────────────────

SourceType = Literal["bank", "credit_card", "grab", "lineman"]

_LINE_TABLE = {
    "bank":        "recon_bank_lines",
    "credit_card": "recon_cc_lines",
    "grab":        "recon_grab_lines",
    "lineman":     "recon_lineman_lines",
}


def _run_parser(source_type: SourceType, raw: bytes, provider: str):
    if source_type == "bank":
        return parse_bank_statement(raw, provider or None)
    if source_type == "credit_card":
        return parse_cc_settlement(raw, provider or None)
    if source_type == "grab":
        return ("Grab", parse_grab_report(raw))
    return ("Lineman", parse_lineman_report(raw))


@app.post("/api/uploads/{source_type}/preview")
async def preview_upload(
    source_type: SourceType,
    file: UploadFile = File(...),
    branch_id: int   = Form(...),
    period_month: str = Form(...),
    provider: str    = Form(""),
):
    raw = await file.read()
    try:
        prov, rows = _run_parser(source_type, raw, provider)
    except Exception as exc:
        raise HTTPException(422, f"Parse error: {exc}")

    return {
        "provider":  prov,
        "row_count": len(rows),
        "preview":   rows[:50],
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
    raw = await file.read()
    try:
        prov, rows = _run_parser(source_type, raw, provider)
    except Exception as exc:
        raise HTTPException(422, f"Parse error: {exc}")

    with get_db() as conn:
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
            cols = ", ".join(row.keys())
            placeholders = ", ".join("?" * len(row))
            conn.execute(f"INSERT INTO {table} ({cols}) VALUES ({placeholders})",
                         list(row.values()))
        conn.commit()

    return {"upload_id": upload_id, "rows_saved": len(rows), "provider": prov}


@app.get("/api/uploads")
def list_uploads(branch_id: int | None = None, period_month: str | None = None):
    with get_db() as conn:
        filters, params = [], []
        if branch_id:
            filters.append("branch_id=?"); params.append(branch_id)
        if period_month:
            filters.append("period_month=?"); params.append(period_month)
        where = ("WHERE " + " AND ".join(filters)) if filters else ""
        rows = conn.execute(
            f"SELECT * FROM recon_uploads {where} ORDER BY uploaded_at DESC LIMIT 200",
            params
        ).fetchall()
        return [dict(r) for r in rows]


@app.delete("/api/uploads/{upload_id}")
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
            LEFT JOIN recon_branches b ON b.branch_id = s.branch_id
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
            LEFT JOIN recon_branches b ON b.branch_id = s.branch_id
            WHERE s.session_id=?
        """, (session_id,)).fetchone()
        if not row:
            raise HTTPException(404)
        return dict(row)


@app.post("/api/sessions/{session_id}/run-match")
def run_match(session_id: int):
    """Trigger auto-matching. Requires POS data already synced via /api/sync-pos."""
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
async def session_summary(session_id: int):
    with get_db() as conn:
        session = conn.execute(
            "SELECT branch_id, period_month FROM recon_sessions WHERE session_id=?",
            (session_id,)
        ).fetchone()
        if not session:
            raise HTTPException(404)
        bid, pm = session["branch_id"], session["period_month"]

        # Try local POS cache first; fall back to fetching from main app
        pos_row = conn.execute("""
            SELECT COALESCE(SUM(cash),0) AS cash,
                   COALESCE(SUM(credit_card),0) AS credit_card,
                   COALESCE(SUM(transfer),0) AS transfer,
                   COALESCE(SUM(qr),0) AS qr,
                   COALESCE(SUM(total),0) AS total
            FROM recon_pos_daily
            WHERE branch_id=? AND txn_date LIKE ?
        """, (bid, f"{pm}%")).fetchone()

        pos = dict(pos_row) if pos_row and pos_row["total"] else {}
        if not pos.get("total"):
            pos = await get_pos_monthly_from_main(bid, pm)

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
            matched = matched or 0
            return {"total": total, "lines": count, "matched": matched,
                    "unmatched": count - matched,
                    "match_rate": round(matched / count * 100, 1) if count else 0}

        pos_total = pos.get("total") or pos.get("net_sales") or 0
        ext_total_sum = bank[0] + cc[0] + grab[0] + lm[0]
        return {
            "pos":                   pos,
            "bank":                  summarise(bank),
            "credit_card":           summarise(cc),
            "grab":                  summarise(grab),
            "lineman":               summarise(lm),
            "pending_discrepancies": disc_count,
            "ext_total":             ext_total_sum,
            "pos_total":             pos_total,
            "variance":              ext_total_sum - pos_total,
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
async def overview(period_month: str):
    """
    All branches × one month: POS total vs external uploads.
    POS totals are fetched live from the main app.
    """
    with get_db() as conn:
        branches = conn.execute(
            "SELECT branch_id, name FROM recon_branches WHERE active=1 ORDER BY name"
        ).fetchall()

    result = []
    for b in branches:
        bid = b["branch_id"]

        # Fetch POS total from main app
        try:
            pos_data = await get_pos_monthly_from_main(bid, period_month)
            pos_total = pos_data.get("net_sales") or pos_data.get("total") or 0
        except Exception:
            pos_total = 0

        with get_db() as conn:
            ext_row = conn.execute("""
                SELECT
                    (SELECT COALESCE(SUM(credit),0)     FROM recon_bank_lines    WHERE branch_id=? AND strftime('%Y-%m',txn_date)=?) +
                    (SELECT COALESCE(SUM(net_amount),0) FROM recon_cc_lines      WHERE branch_id=? AND strftime('%Y-%m',settlement_date)=?) +
                    (SELECT COALESCE(SUM(net_payout),0) FROM recon_grab_lines    WHERE branch_id=? AND strftime('%Y-%m',order_date)=?) +
                    (SELECT COALESCE(SUM(net_payout),0) FROM recon_lineman_lines WHERE branch_id=? AND strftime('%Y-%m',order_date)=?)
            """, (bid, period_month, bid, period_month, bid, period_month, bid, period_month)).fetchone()
            ext_total = ext_row[0] if ext_row else 0

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


# ── Health check ──────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    try:
        pong = await _http.get("/health", timeout=3.0)
        main_ok = pong.status_code == 200
    except Exception:
        main_ok = False
    return {"status": "ok", "main_app_reachable": main_ok}
