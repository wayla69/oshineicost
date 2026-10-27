"""Test helper: make a session's persisted match results count as FRESH.

Approval re-runs the auto-match when the persisted results are stale (2026-10
audit #8) — and on the demo POS data (tenders, no statements) a real re-run
plants pending discrepancies that block approval. Tests about the sign-off
transitions themselves (SoD, tokens, step-up, snapshots) use this to stand in for
"staff ran the match and cleared the month" without reconciling the demo data.
"""
import sqlite3


def make_approvable(db, sid: int) -> None:
    k = sqlite3.connect(db)
    try:
        k.execute("INSERT OR REPLACE INTO recon_category_result"
                  "(session_id, category, bank, status, computed_at) "
                  "VALUES (?, 'CASH', 'NONE', 'matched', '2999-12-31 23:59:59')", (sid,))
        k.execute("UPDATE recon_discrepancies SET resolution='accepted' "
                  "WHERE session_id=? AND resolution='pending'", (sid,))
        k.commit()
    finally:
        k.close()
