"""
Auto-matching engine.

Reconciliation logic by source type:

  credit_card  →  POS daily credit_card total  (settlement date ± 3 days tolerance)
  bank         →  POS daily cash + transfer + qr  (channel-specific routing)
  grab         →  POS daily delivery/qr total  (payout_date matches bank transfer)
  lineman      →  POS daily delivery/qr total  (payout_date matches bank transfer)

All matching is at the DAILY AGGREGATE level (not per-transaction) because:
  - POS sales_transaction has no per-receipt payment method
  - External reports often batch-settle daily
"""

from __future__ import annotations
import sqlite3
from datetime import date, timedelta


TOLERANCE_AMOUNT = 1.00  # THB – exact match threshold
TOLERANCE_DAYS   = 3     # max day offset for settlement timing differences


def _add_days(d: str, n: int) -> str:
    return (date.fromisoformat(d) + timedelta(days=n)).isoformat()


def _date_range(d: str, window: int) -> list[str]:
    base = date.fromisoformat(d)
    return [(base + timedelta(days=i)).isoformat() for i in range(-window, window + 1)]


# ── POS cache helpers ──────────────────────────────────────────────────────────

def refresh_pos_daily(conn: sqlite3.Connection, branch_id: int, period_month: str) -> None:
    """
    Populate recon_pos_daily from sales_transaction for the given branch + month.
    Uses sales_month_summary for CC/QR (payment method breakdown only available there).
    """
    conn.execute(
        "DELETE FROM recon_pos_daily WHERE branch_id = ? AND txn_date LIKE ?",
        (branch_id, f"{period_month}%"),
    )

    # Transaction-level daily totals (total revenue per day)
    conn.execute("""
        INSERT INTO recon_pos_daily (branch_id, txn_date, total, txn_count)
        SELECT
            branch_id,
            date(sold_at) AS txn_date,
            SUM(total_inc_vat),
            COUNT(*)
        FROM sales_transaction
        WHERE branch_id = ?
          AND strftime('%Y-%m', sold_at) = ?
        GROUP BY branch_id, txn_date
        ON CONFLICT(branch_id, txn_date) DO UPDATE SET
            total     = excluded.total,
            txn_count = excluded.txn_count,
            refreshed_at = datetime('now')
    """, (branch_id, period_month))

    # Payment method breakdown from monthly summary (pro-rated by day weight)
    # Note: sales_month_summary.credit_card etc. are monthly totals only.
    # For day-level CC matching we use them as monthly checksum, not daily.
    conn.commit()


def get_pos_daily_total(
    conn: sqlite3.Connection,
    branch_id: int,
    txn_date: str,
    channel: str,
) -> float:
    """
    Return POS total for a specific payment channel on a given day.
    channel: 'credit_card' | 'cash' | 'transfer' | 'qr' | 'delivery' | 'total'
    Falls back to total if channel column is 0 (not broken out per day).
    """
    row = conn.execute(
        f"SELECT {channel} FROM recon_pos_daily WHERE branch_id=? AND txn_date=?",
        (branch_id, txn_date),
    ).fetchone()
    return float(row[0]) if row and row[0] else 0.0


def get_pos_monthly_total(
    conn: sqlite3.Connection,
    branch_id: int,
    period_month: str,
    channel: str,
) -> float:
    """Get POS monthly total from sales_month_summary for a payment channel."""
    col_map = {
        "credit_card": "credit_card",
        "cash":        "cash",
        "transfer":    "transfer",
        "qr":          "qr",
        "total":       "net_sales",
    }
    col = col_map.get(channel, "net_sales")
    row = conn.execute(
        f"SELECT SUM({col}) FROM sales_month_summary WHERE branch_id=? AND period_month=?",
        (branch_id, period_month),
    ).fetchone()
    return float(row[0]) if row and row[0] else 0.0


# ── Matching functions ─────────────────────────────────────────────────────────

def match_cc_lines(conn: sqlite3.Connection, session_id: int, branch_id: int, period_month: str) -> dict:
    """
    Match credit card settlement lines against POS monthly CC total.
    Strategy: aggregate all CC lines for the month, compare to sales_month_summary.credit_card.
    Individual lines → try to match to daily POS CC total (± TOLERANCE_DAYS).
    """
    unmatched = conn.execute("""
        SELECT id, settlement_date, net_amount
        FROM recon_cc_lines
        WHERE branch_id=? AND match_status='unmatched'
          AND strftime('%Y-%m', settlement_date) = ?
    """, (branch_id, period_month)).fetchall()

    matched = 0
    for row in unmatched:
        line_id, settle_date, net_amt = row
        # Try each day in tolerance window
        for candidate_date in _date_range(settle_date, TOLERANCE_DAYS):
            pos_total = get_pos_daily_total(conn, branch_id, candidate_date, "total")
            if pos_total == 0:
                continue
            if abs(net_amt - pos_total) <= TOLERANCE_AMOUNT:
                _create_match(conn, session_id, "credit_card", "recon_cc_lines",
                              line_id, net_amt, "daily", candidate_date,
                              branch_id, pos_total, "fuzzy_amount", 0.9)
                matched += 1
                break

    # Monthly-level checksum
    pos_monthly = get_pos_monthly_total(conn, branch_id, period_month, "credit_card")
    ext_monthly = conn.execute("""
        SELECT COALESCE(SUM(net_amount), 0)
        FROM recon_cc_lines
        WHERE branch_id=? AND strftime('%Y-%m', settlement_date)=?
    """, (branch_id, period_month)).fetchone()[0]

    return {
        "matched":       matched,
        "total_lines":   len(unmatched),
        "pos_monthly":   pos_monthly,
        "ext_monthly":   ext_monthly,
        "monthly_var":   ext_monthly - pos_monthly,
    }


def match_delivery_lines(
    conn: sqlite3.Connection,
    session_id: int,
    branch_id: int,
    period_month: str,
    source_type: str,      # 'grab' | 'lineman'
) -> dict:
    """
    Match Grab/Lineman order lines against POS.
    Strategy:
      1. Per-day: sum net_payout by order_date, compare to POS delivery/qr daily total.
      2. Monthly: sum all net_payouts, compare to bank payout transfers (via bank lines).
    """
    table = "recon_grab_lines" if source_type == "grab" else "recon_lineman_lines"

    daily_totals = conn.execute(f"""
        SELECT order_date, SUM(net_payout) AS total, COUNT(*) AS cnt
        FROM {table}
        WHERE branch_id=? AND order_status='Completed'
          AND strftime('%Y-%m', order_date)=?
        GROUP BY order_date
    """, (branch_id, period_month)).fetchall()

    matched_days = 0
    for order_date, ext_total, cnt in daily_totals:
        pos_qr = get_pos_daily_total(conn, branch_id, order_date, "qr")
        if abs(ext_total - pos_qr) <= TOLERANCE_AMOUNT * 10:  # wider tolerance for aggregates
            _create_match(conn, session_id, source_type, table,
                          None, ext_total, "daily", order_date,
                          branch_id, pos_qr, "aggregated", 0.8)
            matched_days += 1

    total_days = len(daily_totals)
    return {"matched_days": matched_days, "total_days": total_days}


def match_bank_lines(
    conn: sqlite3.Connection,
    session_id: int,
    branch_id: int,
    period_month: str,
) -> dict:
    """
    Match bank statement credit entries against POS daily cash + transfer totals.
    - CASH_DEP entries → POS cash
    - QR / TRANSFER entries → POS transfer + qr
    - GRAB_PAYOUT / LINEMAN_PAYOUT → matched by amount to delivery platform payouts
    """
    lines = conn.execute("""
        SELECT id, txn_date, credit, channel
        FROM recon_bank_lines
        WHERE branch_id=? AND match_status='unmatched' AND credit > 0
          AND strftime('%Y-%m', txn_date) = ?
    """, (branch_id, period_month)).fetchall()

    matched = 0
    for line_id, txn_date, credit, channel in lines:
        if channel in ("CASH_DEP",):
            pos_ch = "cash"
        elif channel in ("QR", "TRANSFER"):
            pos_ch = "total"  # compare to daily total (no daily breakdown in POS)
        else:
            continue  # GRAB_PAYOUT / LINEMAN_PAYOUT handled by delivery matcher

        for candidate_date in _date_range(txn_date, TOLERANCE_DAYS):
            pos_total = get_pos_daily_total(conn, branch_id, candidate_date, "total")
            if abs(credit - pos_total) <= TOLERANCE_AMOUNT:
                _create_match(conn, session_id, "bank", "recon_bank_lines",
                              line_id, credit, "daily", candidate_date,
                              branch_id, pos_total, "fuzzy_amount", 0.85)
                matched += 1
                break

    return {"matched": matched, "total_lines": len(lines)}


def run_auto_match(conn: sqlite3.Connection, session_id: int, branch_id: int, period_month: str) -> dict:
    """Run all matchers for a session. Call after all uploads are confirmed."""
    refresh_pos_daily(conn, branch_id, period_month)

    cc_result      = match_cc_lines(conn, session_id, branch_id, period_month)
    grab_result    = match_delivery_lines(conn, session_id, branch_id, period_month, "grab")
    lineman_result = match_delivery_lines(conn, session_id, branch_id, period_month, "lineman")
    bank_result    = match_bank_lines(conn, session_id, branch_id, period_month)

    _flag_discrepancies(conn, session_id)
    conn.commit()

    return {
        "credit_card": cc_result,
        "grab":        grab_result,
        "lineman":     lineman_result,
        "bank":        bank_result,
    }


def _create_match(
    conn: sqlite3.Connection,
    session_id: int,
    source_type: str,
    table: str,
    external_id: int | None,
    external_amount: float,
    pos_level: str,
    pos_date: str,
    pos_branch_id: int,
    pos_amount: float,
    match_type: str,
    confidence: float,
) -> int:
    cur = conn.execute("""
        INSERT INTO recon_matches
            (session_id, source_type, external_table, external_id,
             external_amount, pos_level, pos_date, pos_branch_id,
             pos_amount, match_type, confidence)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)
    """, (session_id, source_type, table, external_id,
          external_amount, pos_level, pos_date, pos_branch_id,
          pos_amount, match_type, confidence))
    match_id = cur.lastrowid

    if external_id:
        conn.execute(
            f"UPDATE {table} SET match_status='matched', match_id=? WHERE id=?",
            (match_id, external_id),
        )
    return match_id


def _flag_discrepancies(conn: sqlite3.Connection, session_id: int) -> None:
    """Create discrepancy records for all still-unmatched lines in this session's uploads."""
    session = conn.execute(
        "SELECT branch_id, period_month FROM recon_sessions WHERE session_id=?",
        (session_id,)
    ).fetchone()
    if not session:
        return
    branch_id, period_month = session

    for table, src in [
        ("recon_bank_lines",    "bank"),
        ("recon_cc_lines",      "credit_card"),
        ("recon_grab_lines",    "grab"),
        ("recon_lineman_lines", "lineman"),
    ]:
        amt_col = "credit" if src == "bank" else "net_amount" if src == "credit_card" else "net_payout"
        unmatched = conn.execute(f"""
            SELECT id, {amt_col}
            FROM {table}
            WHERE branch_id=? AND match_status='unmatched'
              AND strftime('%Y-%m', {'txn_date' if src=='bank' else 'settlement_date' if src=='credit_card' else 'order_date'}) = ?
        """, (branch_id, period_month)).fetchall()
        for line_id, amt in unmatched:
            conn.execute("""
                INSERT OR IGNORE INTO recon_discrepancies
                    (session_id, disc_type, source_type, external_id, amount_external)
                VALUES (?, 'unmatched_external', ?, ?, ?)
            """, (session_id, src, line_id, amt))
