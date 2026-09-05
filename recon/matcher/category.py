"""The monthly category/bank reconciliation: POS lanes vs external buckets, tolerances, TRANSFER·ANY, moves, coupon.

Split out of recon/matcher.py on 2026-09-05 — same code, one lane per module;
recon.matcher re-exports every name."""
from __future__ import annotations
import sqlite3
from ._shared import _cfg, _within_tol, method_config, pos_category_totals
from .buckets import _apply_transfer_any, channel_move_deltas, external_buckets
from .coupon import coupon_reconciliation


def category_reconciliation(conn: sqlite3.Connection, branch_id: int, period_month: str):
    """Compare POS buckets to external buckets. Returns (rows, unmapped).

    Lane adjustments (recon_lane_adj) are applied at read-time: adj_pos is
    added to the POS side, adj_ext to the external side. No re-run-match needed.
    """
    pos, unmapped = pos_category_totals(conn, branch_id, period_month)
    ext = external_buckets(conn, branch_id, period_month)
    cfgs = method_config(conn)
    _apply_transfer_any(pos, ext)

    # Manual cross-channel moves (booked-in-wrong-channel): shift the POS base out of
    # the wrong lane and into the right one BEFORE adjustments, so both lanes correct
    # at once. Lives in `pos` (never in recon_lane_adj) → no UNIQUE/accept collision.
    for k, d in channel_move_deltas(conn, branch_id, period_month).items():
        pos[k] = pos.get(k, 0.0) + d

    # Load lane adjustments (graceful if table not yet created)
    adjs: dict = {}
    try:
        for r in conn.execute(
            "SELECT id, category, bank, adj_pos, adj_ext, note, adj_type "
            "FROM recon_lane_adj WHERE branch_id=? AND period_month=?",
            (branch_id, period_month)):
            adjs[(r["category"], r["bank"])] = r
    except Exception:
        pass

    rows = []
    _coupon_rows = None   # lazily populated on first COUPON row, shared across iterations
    for key in sorted(set(pos) | set(ext) | set(adjs)):
        category, bank = key
        pos_base = pos.get(key, 0.0)
        eb = ext.get(key)
        ext_base = eb["amount"] if eb else 0.0
        ext_net  = eb["net"]    if eb else 0.0

        adj      = adjs.get(key)
        adj_pos  = float(adj["adj_pos"] or 0) if adj else 0.0
        adj_ext  = float(adj["adj_ext"] or 0) if adj else 0.0

        pos_amt  = pos_base + adj_pos
        ext_amt  = ext_base + adj_ext
        ext_net  = ext_net  + adj_ext   # adj_ext is net (no fee)

        if pos_amt > 0 and ext_amt > 0:
            tol = _cfg(cfgs, category, bank).get("tol_abs")
            status = "matched" if _within_tol(ext_amt, pos_amt, tol) else "mismatch"
        elif pos_amt > 0:
            if category == "COUPON":
                # Delegate to coupon_reconciliation so category snapshots (close/export)
                # reflect the real issuer-point status, not a permanent "no_source".
                if _coupon_rows is None:
                    _coupon_rows = coupon_reconciliation(conn, branch_id, period_month)
                if not _coupon_rows:
                    status = "no_source"
                elif any(r["status"] == "mismatch" for r in _coupon_rows):
                    status = "mismatch"
                elif any(r["status"] in ("waiting_doc", "awaiting_payout") for r in _coupon_rows):
                    status = "waiting_doc"
                else:
                    status = "matched"
            else:
                status = "pos_only"
        elif ext_amt > 0:
            status = "ext_only"
        else:
            continue   # adj-only row with 0/0 — skip

        rows.append({
            "category":   category,
            "bank":       bank,
            "pos_amount": round(pos_amt, 2),
            "ext_amount": round(ext_amt, 2),
            "ext_net":    round(ext_net, 2),
            "variance":   round(ext_amt - pos_amt, 2),
            "status":     status,
            "ext_lines":  eb["lines"] if eb else [],
            # Adjustment fields (None when no adj set)
            "adj_pos":    round(adj_pos, 2) if adj_pos else None,
            "adj_ext":    round(adj_ext, 2) if adj_ext else None,
            "adj_note":   adj["note"]     if adj else None,
            "adj_type":   adj["adj_type"] if adj else None,
            "adj_id":     adj["id"]       if adj else None,
        })
    return rows, unmapped
