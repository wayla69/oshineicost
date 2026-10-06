"""run_auto_match — the session-level pass that writes match rows for every lane.

Split out of recon/matcher.py on 2026-09-05 — same code, one lane per module;
recon.matcher re-exports every name."""
from __future__ import annotations
import sqlite3
from ._shared import TOL_ABS, _CATEGORY_SOURCE, memo_scoped
from .cash import cash_deposit_matching
from .payout import payout_reconciliation
from .category import category_reconciliation


# ── Run + persist ─────────────────────────────────────────────────────────────

_LINE_TABLES = [
    ("recon_bank_lines",    "txn_date"),
    ("recon_cc_lines",      "settlement_date"),
    ("recon_grab_lines",    "order_date"),
    ("recon_lineman_lines", "order_date"),
]


def _reset_session(conn, session_id, branch_id, period_month):
    """Clear prior auto output so a re-run is idempotent. Manual matches and
    manually-resolved discrepancies are preserved."""
    conn.execute("DELETE FROM recon_category_result WHERE session_id=?", (session_id,))
    conn.execute("DELETE FROM recon_discrepancies WHERE session_id=? AND resolution='pending'", (session_id,))
    for table, date_col in _LINE_TABLES:
        # 'manual' (tick-to-match) and 'excluded' (cancelled delivery orders /
        # operator-flagged internal transfers) are decisions — a re-run keeps them.
        conn.execute(f"""
            UPDATE {table} SET match_status='unmatched', match_id=NULL
            WHERE branch_id=? AND strftime('%Y-%m', {date_col})=?
              AND match_status NOT IN ('manual','excluded')
        """, (branch_id, period_month))


@memo_scoped
def run_auto_match(conn: sqlite3.Connection, session_id: int, branch_id: int, period_month: str) -> dict:
    """Reconcile a session and persist category results + discrepancies. Idempotent."""
    _reset_session(conn, session_id, branch_id, period_month)

    # Cancelled/refunded delivery orders aren't revenue — exclude from the universe
    for table in ("recon_grab_lines", "recon_lineman_lines"):
        conn.execute(f"""
            UPDATE {table} SET match_status='excluded'
            WHERE branch_id=? AND strftime('%Y-%m', order_date)=?
              AND order_status!='Completed' AND match_status!='manual'
        """, (branch_id, period_month))

    rows, unmapped = category_reconciliation(conn, branch_id, period_month)

    # Channel-level acknowledged variances (v2 "รับทราบ" — lane_adj adj_type='accept'):
    # their lanes still get a discrepancy row for the audit trail, but born
    # 'accepted' with the staff reason, so the approval gate doesn't block on them.
    try:
        accepted = {a["category"]: a for a in conn.execute(
            "SELECT category, note, created_by FROM recon_lane_adj "
            "WHERE branch_id=? AND period_month=? AND adj_type='accept'",
            (branch_id, period_month))}
    except sqlite3.OperationalError:
        accepted = {}

    # Discrepancies staff already resolved/accepted survive the reset. A re-run must
    # not plant a fresh pending twin of them (duplicates + a 409 at approval, so
    # ปิดเดือน — run-match then approve — could never close a month). A resolution
    # covers the variance it was made on: if the lane has moved beyond tolerance
    # since, the new figure is a new discrepancy.
    settled: dict = {}
    for d in conn.execute(
            "SELECT category, bank, disc_type, variance FROM recon_discrepancies "
            "WHERE session_id=? AND resolution!='pending'", (session_id,)):
        settled.setdefault((d["category"], d["bank"], d["disc_type"]), []).append(
            float(d["variance"] or 0))

    def _settled(category, bank, disc_type, variance):
        return any(abs(float(variance or 0) - v) <= TOL_ABS
                   for v in settled.get((category, bank, disc_type), ()))

    for r in rows:
        conn.execute("""
            INSERT INTO recon_category_result
                (session_id, category, bank, pos_amount, ext_amount, ext_net, variance, status)
            VALUES (?,?,?,?,?,?,?,?)
        """, (session_id, r["category"], r["bank"], r["pos_amount"], r["ext_amount"],
              r["ext_net"], r["variance"], r["status"]))

        if r["status"] == "matched":
            for table, line_id in r["ext_lines"]:
                # manual (tick-to-match) / excluded lines are staff decisions — keep them
                conn.execute(f"UPDATE {table} SET match_status='matched' WHERE id=? "
                             f"AND match_status NOT IN ('manual','excluded')", (line_id,))
        else:
            # COUPON with no issuer points has nothing to check against — the branch
            # page shows it as no_source (no todo), so it must not block approval.
            if r["category"] == "COUPON" and r["status"] == "no_source":
                continue
            disc_type = {
                "mismatch":    "amount_mismatch",
                "ext_only":    "unmatched_external",
                "pos_only":    "unmatched_pos",
                "no_source":   "unmatched_pos",
                "waiting_doc": "unmatched_pos",     # COUPON: points summary not uploaded
            }.get(r["status"], "amount_mismatch")
            if _settled(r["category"], r["bank"], disc_type, r["variance"]):
                continue
            acc = accepted.get(r["category"])
            if acc:
                conn.execute("""
                    INSERT INTO recon_discrepancies
                        (session_id, disc_type, source_type, category, bank,
                         amount_external, amount_pos, variance,
                         resolution, resolution_note, resolved_by, resolved_at)
                    VALUES (?,?,?,?,?,?,?,?,'accepted',?,?,datetime('now'))
                """, (session_id, disc_type, _CATEGORY_SOURCE.get(r["category"], "pos"),
                      r["category"], r["bank"], r["ext_amount"], r["pos_amount"], r["variance"],
                      f"รับทราบ: {acc['note']}", acc["created_by"] or "staff"))
            else:
                conn.execute("""
                    INSERT INTO recon_discrepancies
                        (session_id, disc_type, source_type, category, bank,
                         amount_external, amount_pos, variance)
                    VALUES (?,?,?,?,?,?,?,?)
                """, (session_id, disc_type, _CATEGORY_SOURCE.get(r["category"], "pos"),
                      r["category"], r["bank"], r["ext_amount"], r["pos_amount"], r["variance"]))

    # Cash: deposits matched by the grouped cash matcher are reconciled lines —
    # mark them so per-upload match stats (and the lump-sum's own date) read true.
    cash = cash_deposit_matching(conn, branch_id, period_month)
    for g in cash["groups"]:
        conn.execute("UPDATE recon_bank_lines SET match_status='matched' WHERE id=?",
                     (g["line_id"],))

    # Payout chain: provider NET ↔ bank payout credit. Only a real mismatch is a
    # discrepancy; awaiting_payout/ext_only are informational (shown, not queued).
    payouts = payout_reconciliation(conn, branch_id, period_month)
    for p in payouts:
        if p["status"] == "mismatch" and not _settled(p["category"], "PAYOUT",
                                                        "amount_mismatch", p["variance"]):
            conn.execute("""
                INSERT INTO recon_discrepancies
                    (session_id, disc_type, source_type, category, bank,
                     amount_external, amount_pos, variance)
                VALUES (?,?,?,?,?,?,?,?)
            """, (session_id, "amount_mismatch", _CATEGORY_SOURCE.get(p["category"], "bank"),
                  p["category"], "PAYOUT", p["bank_payout"], p["report_net"], p["variance"]))

    conn.commit()

    matched = sum(1 for r in rows if r["status"] == "matched")
    payout_mismatch = sum(1 for p in payouts if p["status"] == "mismatch")
    return {
        "categories":  [{k: r[k] for k in
                         ("category", "bank", "pos_amount", "ext_amount", "ext_net", "variance", "status")}
                        for r in rows],
        "matched":     matched,
        "discrepancies": (len(rows) - matched) + payout_mismatch,
        "payouts":     payouts,
        "unmapped":    [{"code": k, "amount": round(v, 2)} for k, v in sorted(unmapped.items())],
    }
