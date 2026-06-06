"""
Auto-matching engine.

Primary POS data source: pos_payment_month (from MSSQL sync)
  - exact PaymentCode strings per branch per month
  - covers all Easy POS payment types
Fallback: sales_month_summary (populated from Excel re-import after ingest fix)

Reconciliation strategy:
  credit_card  →  pos_payment_month WHERE payment_code LIKE credit keywords
  bank         →  pos daily total from sales_transaction
  grab         →  pos_customer_type_month WHERE type_name matches Grab
  lineman      →  pos_customer_type_month WHERE type_name matches Lineman
"""

from __future__ import annotations
import re
import sqlite3
from datetime import date, timedelta

TOLERANCE_AMOUNT = 5.00   # THB — exact (daily) match threshold
TOLERANCE_DAYS   = 3      # max day offset for bank deposit timing
MONTHLY_TOL_PCT  = 1.0    # % — monthly aggregate (CC / delivery) tolerance

# ── PaymentCode keyword patterns (Easy POS codes vary by branch config) ────────

_CC_PAT      = re.compile(r'credit|card|บัตร|visa|master|amex|jcb|union', re.I)
_QR_PAT      = re.compile(r'qr|prompt|k.?shop|kplus|พร้อมเพย์', re.I)
_TRANSFER_PAT = re.compile(r'transfer|โอน|wire', re.I)
_CASH_PAT    = re.compile(r'cash|เงินสด', re.I)
_GRAB_PAT    = re.compile(r'grab|แกร็บ', re.I)
_LINEMAN_PAT = re.compile(r'line.?man|lineman|ไลน์แมน|line man', re.I)


def _date_range(d: str, window: int) -> list[str]:
    base = date.fromisoformat(d)
    return [(base + timedelta(days=i)).isoformat() for i in range(-window, window + 1)]


# ── pos_payment_month helpers ─────────────────────────────────────────────────

def _payment_total(conn: sqlite3.Connection, branch_id: int, period_month: str, pattern: re.Pattern) -> float:
    """
    Sum all payment codes matching pattern from pos_payment_month.
    Returns 0.0 if table doesn't exist (MSSQL sync not run yet).
    """
    try:
        rows = conn.execute(
            "SELECT payment_code, amount FROM pos_payment_month WHERE branch_id=? AND period_month=?",
            (branch_id, period_month)
        ).fetchall()
    except sqlite3.OperationalError:
        return 0.0
    return sum(float(r["amount"] or 0) for r in rows if pattern.search(r["payment_code"] or ""))


def _customer_type_total(conn: sqlite3.Connection, branch_id: int, period_month: str, pattern: re.Pattern) -> float:
    """
    Sum net revenue for customer types matching pattern from pos_customer_type_month.
    net column is inc-VAT; divide by 1.07 to get ex-VAT.
    Returns 0.0 if table doesn't exist.
    """
    try:
        rows = conn.execute(
            "SELECT type_name, net FROM pos_customer_type_month WHERE branch_id=? AND period_month=?",
            (branch_id, period_month)
        ).fetchall()
    except sqlite3.OperationalError:
        return 0.0
    return sum(float(r["net"] or 0) / 1.07 for r in rows if pattern.search(r["type_name"] or ""))


def get_pos_payment_breakdown(conn: sqlite3.Connection, branch_id: int, period_month: str) -> dict:
    """
    Returns full payment breakdown for a branch+month.
    Tries pos_payment_month first (MSSQL sync), falls back to sales_month_summary.
    """
    cc       = _payment_total(conn, branch_id, period_month, _CC_PAT)
    qr       = _payment_total(conn, branch_id, period_month, _QR_PAT)
    transfer = _payment_total(conn, branch_id, period_month, _TRANSFER_PAT)
    cash     = _payment_total(conn, branch_id, period_month, _CASH_PAT)
    grab_net = _customer_type_total(conn, branch_id, period_month, _GRAB_PAT)
    lm_net   = _customer_type_total(conn, branch_id, period_month, _LINEMAN_PAT)

    # Fall back to sales_month_summary if pos_payment_month returned all zeros
    if cc == 0 and qr == 0 and transfer == 0 and cash == 0:
        row = conn.execute("""
            SELECT COALESCE(SUM(cash),0) cash, COALESCE(SUM(credit_card),0) credit_card,
                   COALESCE(SUM(transfer),0) transfer, COALESCE(SUM(qr),0) qr,
                   COALESCE(SUM(net_sales),0) net_sales
            FROM sales_month_summary
            WHERE branch_id=? AND period_month=? AND source='Easy'
        """, (branch_id, period_month)).fetchone()
        if row:
            cash     = row["cash"]
            cc       = row["credit_card"]
            transfer = row["transfer"]
            qr       = row["qr"]

    pos_net = conn.execute("""
        SELECT COALESCE(SUM(net_sales), 0)
        FROM sales_month_summary
        WHERE branch_id=? AND period_month=?
    """, (branch_id, period_month)).fetchone()[0]

    return {
        "cash":        cash,
        "credit_card": cc,
        "transfer":    transfer,
        "qr":          qr,
        "grab":        grab_net,
        "lineman":     lm_net,
        "pos_net":     pos_net,
        "source":      "pos_payment_month" if (cc or qr or transfer or cash) else "sales_month_summary",
    }


# ── Daily POS cache ───────────────────────────────────────────────────────────

def refresh_pos_daily(conn: sqlite3.Connection, branch_id: int, period_month: str) -> None:
    """Build recon_pos_daily from sales_transaction for fast day-level matching."""
    conn.execute(
        "DELETE FROM recon_pos_daily WHERE branch_id=? AND txn_date LIKE ?",
        (branch_id, f"{period_month}%"),
    )
    conn.execute("""
        INSERT INTO recon_pos_daily (branch_id, txn_date, total, txn_count)
        SELECT branch_id, date(sold_at), SUM(total_inc_vat), COUNT(*)
        FROM sales_transaction
        WHERE branch_id=? AND strftime('%Y-%m', sold_at)=?
        GROUP BY branch_id, date(sold_at)
        ON CONFLICT(branch_id, txn_date) DO UPDATE SET
            total        = excluded.total,
            txn_count    = excluded.txn_count,
            refreshed_at = datetime('now')
    """, (branch_id, period_month))
    conn.commit()


def get_pos_daily_total(conn: sqlite3.Connection, branch_id: int, txn_date: str) -> float:
    row = conn.execute(
        "SELECT total FROM recon_pos_daily WHERE branch_id=? AND txn_date=?",
        (branch_id, txn_date)
    ).fetchone()
    return float(row["total"]) if row and row["total"] else 0.0


# ── Matchers ──────────────────────────────────────────────────────────────────

def match_cc_lines(conn: sqlite3.Connection, session_id: int, branch_id: int, period_month: str) -> dict:
    """
    Reconcile credit-card settlements against POS card revenue at the MONTHLY level.

    Daily matching is intentionally avoided: recon_pos_daily.total is the whole
    day's revenue across all tenders, while a CC line is card-only after MDR fee,
    so daily amounts almost never tie. We compare the month's CC settlement total
    against the POS card total (pos_payment_month keyword match, fallback
    sales_month_summary) and settle/flag once for the month.
    """
    pos_cc     = _payment_total(conn, branch_id, period_month, _CC_PAT)
    pos_source = "pos_payment_month"
    if pos_cc == 0:
        row = conn.execute(
            "SELECT COALESCE(SUM(credit_card),0) FROM sales_month_summary WHERE branch_id=? AND period_month=?",
            (branch_id, period_month)
        ).fetchone()
        pos_cc = float(row[0]) if row else 0.0
        pos_source = "sales_month_summary"

    rows = conn.execute("""
        SELECT id, net_amount FROM recon_cc_lines
        WHERE branch_id=? AND strftime('%Y-%m', settlement_date)=?
    """, (branch_id, period_month)).fetchall()
    line_ids  = [r["id"] for r in rows]
    ext_total = sum(float(r["net_amount"] or 0) for r in rows)

    matched = _settle_monthly(conn, session_id, "credit_card", "recon_cc_lines",
                              line_ids, ext_total, pos_cc, branch_id, period_month)
    return {
        "matched_lines": matched,
        "total_lines":   len(line_ids),
        "pos_cc_total":  pos_cc,
        "ext_cc_total":  round(ext_total, 2),
        "monthly_var":   round(ext_total - pos_cc, 2),
        "pos_source":    pos_source,
    }


def match_delivery_lines(
    conn: sqlite3.Connection,
    session_id: int,
    branch_id: int,
    period_month: str,
    source_type: str,   # 'grab' | 'lineman'
) -> dict:
    """
    Reconcile Grab/Lineman payouts against pos_customer_type_month at the MONTHLY
    level. Non-completed orders are marked 'excluded' (not revenue); the month's
    Completed net_payout total is settled against POS delivery revenue.

    NOTE: net_payout is after platform commission, while POS delivery revenue is
    gross food (ex-VAT) — they diverge by ~commission. Tune the comparison basis
    once a real report is inspected (see PROGRESS.md).
    """
    table   = "recon_grab_lines" if source_type == "grab" else "recon_lineman_lines"
    pat     = _GRAB_PAT if source_type == "grab" else _LINEMAN_PAT
    pos_net = _customer_type_total(conn, branch_id, period_month, pat)

    conn.execute(f"""
        UPDATE {table} SET match_status='excluded'
        WHERE branch_id=? AND strftime('%Y-%m', order_date)=?
          AND order_status!='Completed' AND match_status='unmatched'
    """, (branch_id, period_month))

    completed = conn.execute(f"""
        SELECT id, net_payout FROM {table}
        WHERE branch_id=? AND order_status='Completed'
          AND strftime('%Y-%m', order_date)=?
    """, (branch_id, period_month)).fetchall()
    line_ids  = [r["id"] for r in completed]
    ext_total = sum(float(r["net_payout"] or 0) for r in completed)

    matched = _settle_monthly(conn, session_id, source_type, table,
                              line_ids, ext_total, pos_net, branch_id, period_month)
    return {
        "ext_total":   round(ext_total, 2),
        "pos_total":   round(pos_net, 2),
        "lines":       len(line_ids),
        "matched":     matched,
        "variance":    round(ext_total - pos_net, 2),
        "pos_source":  "pos_customer_type_month" if pos_net else "unavailable",
    }


def match_bank_lines(
    conn: sqlite3.Connection,
    session_id: int,
    branch_id: int,
    period_month: str,
) -> dict:
    """
    Match bank statement credit entries against POS daily totals.
    Grab/Lineman payout rows (channel='GRAB_PAYOUT'/'LINEMAN_PAYOUT') are
    matched separately via match_delivery_lines and excluded here.
    """
    lines = conn.execute("""
        SELECT id, txn_date, credit, channel
        FROM recon_bank_lines
        WHERE branch_id=? AND match_status='unmatched' AND credit > 0
          AND channel NOT IN ('GRAB_PAYOUT','LINEMAN_PAYOUT','FEE')
          AND strftime('%Y-%m', txn_date)=?
    """, (branch_id, period_month)).fetchall()

    matched = 0
    for row in lines:
        line_id, txn_date, credit = row["id"], row["txn_date"], row["credit"]
        for candidate_date in _date_range(txn_date, TOLERANCE_DAYS):
            pos_day = get_pos_daily_total(conn, branch_id, candidate_date)
            if pos_day > 0 and abs(credit - pos_day) <= TOLERANCE_AMOUNT:
                _create_match(conn, session_id, "bank", "recon_bank_lines",
                              line_id, credit, "daily", candidate_date, branch_id, pos_day,
                              "fuzzy_amount", 0.85)
                matched += 1
                break

    return {"matched": matched, "total_lines": len(lines)}


def run_auto_match(conn: sqlite3.Connection, session_id: int, branch_id: int, period_month: str) -> dict:
    """Run all matchers for a session. Idempotent — safe to re-run."""
    _reset_session(conn, session_id, branch_id, period_month)
    refresh_pos_daily(conn, branch_id, period_month)

    cc_r   = match_cc_lines(conn, session_id, branch_id, period_month)
    grab_r = match_delivery_lines(conn, session_id, branch_id, period_month, "grab")
    lm_r   = match_delivery_lines(conn, session_id, branch_id, period_month, "lineman")
    bank_r = match_bank_lines(conn, session_id, branch_id, period_month)

    _flag_discrepancies(conn, session_id, branch_id, period_month)
    conn.commit()

    return {
        "credit_card": cc_r,
        "grab":        grab_r,
        "lineman":     lm_r,
        "bank":        bank_r,
    }


_LINE_TABLES = [
    ("recon_bank_lines",    "txn_date"),
    ("recon_cc_lines",      "settlement_date"),
    ("recon_grab_lines",    "order_date"),
    ("recon_lineman_lines", "order_date"),
]


def _reset_session(conn: sqlite3.Connection, session_id: int, branch_id: int, period_month: str) -> None:
    """
    Clear prior auto-match output so a re-run is idempotent. Manual matches and
    manually-resolved discrepancies are preserved.
    """
    conn.execute(
        "DELETE FROM recon_matches WHERE session_id=? AND matched_by='auto'",
        (session_id,),
    )
    conn.execute(
        "DELETE FROM recon_discrepancies WHERE session_id=? AND resolution='pending'",
        (session_id,),
    )
    for table, date_col in _LINE_TABLES:
        conn.execute(f"""
            UPDATE {table} SET match_status='unmatched', match_id=NULL
            WHERE branch_id=? AND strftime('%Y-%m', {date_col})=?
              AND match_status!='manual'
        """, (branch_id, period_month))


def _within_tol(ext: float, pos: float) -> bool:
    if abs(ext - pos) <= TOLERANCE_AMOUNT:
        return True
    return abs(ext - pos) / max(abs(pos), 1.0) * 100 <= MONTHLY_TOL_PCT


def _settle_monthly(
    conn: sqlite3.Connection,
    session_id: int,
    source_type: str,
    table: str,
    line_ids: list[int],
    ext_total: float,
    pos_total: float,
    branch_id: int,
    period_month: str,
) -> int:
    """
    Settle a source at the monthly level. Records one aggregate match; if the
    month's external total ties to POS within tolerance, marks all its lines
    matched and returns the count. Otherwise records a single monthly
    amount_mismatch discrepancy and leaves the lines unmatched.
    """
    if ext_total == 0 and pos_total == 0:
        return 0

    var_pct    = abs(ext_total - pos_total) / max(abs(pos_total), 1.0) * 100
    confidence = 0.95 if var_pct < 2 else 0.8 if var_pct < 10 else 0.5
    agg_id = _create_match(conn, session_id, source_type, table,
                           -1, ext_total, "monthly", f"{period_month}-01",
                           branch_id, pos_total, "aggregated", confidence)

    if _within_tol(ext_total, pos_total):
        for lid in line_ids:
            conn.execute(f"UPDATE {table} SET match_status='matched', match_id=? WHERE id=?",
                         (agg_id, lid))
        return len(line_ids)

    conn.execute("""
        INSERT INTO recon_discrepancies
            (session_id, disc_type, source_type, match_id, amount_external, amount_pos, variance)
        VALUES (?, 'amount_mismatch', ?, ?, ?, ?, ?)
    """, (session_id, source_type, agg_id, ext_total, pos_total, round(ext_total - pos_total, 2)))
    return 0


# ── Internal helpers ──────────────────────────────────────────────────────────

def _create_match(
    conn: sqlite3.Connection,
    session_id: int,
    source_type: str,
    table: str,
    external_id: int,
    external_amount: float,
    pos_level: str,
    pos_date: str | None,
    pos_branch_id: int,
    pos_amount: float,
    match_type: str,
    confidence: float,
) -> int:
    period = pos_date[:7] if pos_date and len(pos_date) >= 7 else None
    cur = conn.execute("""
        INSERT INTO recon_matches
            (session_id, source_type, external_table, external_id,
             external_amount, pos_level, pos_date, pos_period_month,
             pos_branch_id, pos_amount, match_type, confidence)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
    """, (session_id, source_type, table, external_id,
          external_amount, pos_level, pos_date, period,
          pos_branch_id, pos_amount, match_type, confidence))

    if external_id and external_id > 0:
        conn.execute(
            f"UPDATE {table} SET match_status='matched', match_id=? WHERE id=?",
            (cur.lastrowid, external_id),
        )
    return cur.lastrowid


def _flag_discrepancies(
    conn: sqlite3.Connection,
    session_id: int,
    branch_id: int,
    period_month: str,
) -> None:
    """
    Flag genuinely unmatched BANK credit lines as discrepancies. CC, Grab and
    Lineman are reconciled at the monthly level by _settle_monthly, which records
    its own amount_mismatch discrepancies — flagging their lines here too would
    double-count.
    """
    rows = conn.execute("""
        SELECT id, credit FROM recon_bank_lines
        WHERE branch_id=? AND match_status='unmatched' AND credit > 0
          AND channel NOT IN ('GRAB_PAYOUT','LINEMAN_PAYOUT','FEE')
          AND strftime('%Y-%m', txn_date)=?
    """, (branch_id, period_month)).fetchall()

    for row in rows:
        conn.execute("""
            INSERT INTO recon_discrepancies
                (session_id, disc_type, source_type, external_id, amount_external)
            VALUES (?, 'unmatched_external', 'bank', ?, ?)
        """, (session_id, row[0], row[1]))
