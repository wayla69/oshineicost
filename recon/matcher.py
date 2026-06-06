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

TOLERANCE_AMOUNT = 5.00   # THB — exact match threshold
TOLERANCE_DAYS   = 3      # max day offset for CC settlement timing

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
    Match credit card settlement lines against POS credit card total.
    Uses pos_payment_month (keyword match on payment_code) as primary source.
    Monthly-level cross-check: sum of all CC settlements vs POS CC total.
    """
    pos_cc = _payment_total(conn, branch_id, period_month, _CC_PAT)
    if pos_cc == 0:
        # Fallback to sales_month_summary (from Excel re-import fix)
        row = conn.execute(
            "SELECT COALESCE(SUM(credit_card),0) FROM sales_month_summary WHERE branch_id=? AND period_month=?",
            (branch_id, period_month)
        ).fetchone()
        pos_cc = float(row[0]) if row else 0.0

    unmatched = conn.execute("""
        SELECT id, settlement_date, net_amount
        FROM recon_cc_lines
        WHERE branch_id=? AND match_status='unmatched'
          AND strftime('%Y-%m', settlement_date)=?
    """, (branch_id, period_month)).fetchall()

    matched = 0
    for row in unmatched:
        line_id, settle_date, net_amt = row["id"], row["settlement_date"], row["net_amount"]
        # Try daily POS total within tolerance window
        for candidate_date in _date_range(settle_date, TOLERANCE_DAYS):
            pos_day = get_pos_daily_total(conn, branch_id, candidate_date)
            if pos_day > 0 and abs(net_amt - pos_day) <= TOLERANCE_AMOUNT:
                _create_match(conn, session_id, "credit_card", "recon_cc_lines",
                              line_id, net_amt, "daily", candidate_date, branch_id, pos_day,
                              "fuzzy_amount", 0.9)
                matched += 1
                break

    ext_monthly = conn.execute("""
        SELECT COALESCE(SUM(net_amount), 0)
        FROM recon_cc_lines
        WHERE branch_id=? AND strftime('%Y-%m', settlement_date)=?
    """, (branch_id, period_month)).fetchone()[0]

    return {
        "matched":       matched,
        "total_lines":   len(unmatched),
        "pos_cc_total":  pos_cc,
        "ext_cc_total":  ext_monthly,
        "monthly_var":   round(ext_monthly - pos_cc, 2),
        "pos_source":    "pos_payment_month" if pos_cc else "sales_month_summary",
    }


def match_delivery_lines(
    conn: sqlite3.Connection,
    session_id: int,
    branch_id: int,
    period_month: str,
    source_type: str,   # 'grab' | 'lineman'
) -> dict:
    """
    Match Grab/Lineman payout lines against pos_customer_type_month.
    Monthly total comparison: sum of net_payouts vs POS delivery revenue.
    """
    table   = "recon_grab_lines" if source_type == "grab" else "recon_lineman_lines"
    pat     = _GRAB_PAT if source_type == "grab" else _LINEMAN_PAT
    pos_net = _customer_type_total(conn, branch_id, period_month, pat)

    ext_row = conn.execute(f"""
        SELECT COALESCE(SUM(net_payout), 0) total, COUNT(*) lines
        FROM {table}
        WHERE branch_id=? AND order_status='Completed'
          AND strftime('%Y-%m', order_date)=?
    """, (branch_id, period_month)).fetchone()

    ext_total = ext_row[0] if ext_row else 0.0
    ext_lines = ext_row[1] if ext_row else 0

    # Create one aggregate match for the whole month if totals are close
    if ext_total > 0 and pos_net > 0:
        variance_pct = abs(ext_total - pos_net) / max(pos_net, 1) * 100
        confidence   = 0.95 if variance_pct < 2 else 0.8 if variance_pct < 10 else 0.5
        _create_match(conn, session_id, source_type, table,
                      -1, ext_total, "monthly", None, branch_id, pos_net,
                      "aggregated", confidence)

    return {
        "ext_total":   ext_total,
        "pos_total":   pos_net,
        "lines":       ext_lines,
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
    """Run all matchers for a session. Call after all uploads are confirmed."""
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
    """Create discrepancy records for unmatched lines in this session."""
    for table, src, amt_col, date_col in [
        ("recon_bank_lines",    "bank",        "credit",     "txn_date"),
        ("recon_cc_lines",      "credit_card", "net_amount", "settlement_date"),
        ("recon_grab_lines",    "grab",        "net_payout", "order_date"),
        ("recon_lineman_lines", "lineman",     "net_payout", "order_date"),
    ]:
        rows = conn.execute(f"""
            SELECT id, {amt_col}
            FROM {table}
            WHERE branch_id=? AND match_status='unmatched'
              AND strftime('%Y-%m', {date_col})=?
        """, (branch_id, period_month)).fetchall()

        for row in rows:
            conn.execute("""
                INSERT OR IGNORE INTO recon_discrepancies
                    (session_id, disc_type, source_type, external_id, amount_external)
                VALUES (?, 'unmatched_external', ?, ?, ?)
            """, (session_id, src, row[0], row[1]))
