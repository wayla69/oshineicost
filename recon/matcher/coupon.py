"""COUPON = issuer points: KTC 2-way and BAY 3-way reconciliation.

Split out of recon/matcher.py on 2026-09-05 — same code, one lane per module;
recon.matcher re-exports every name."""
from __future__ import annotations
from ._shared import TOL_ABS, memo_scoped
from .points import point_pos_total, point_settlement_total
from .payout import payout_matching


@memo_scoped
def coupon_reconciliation(conn, branch_id, period_month):
    """Per-issuer loyalty-point reconciliation for the COUPON channel (the
    owner's table: Coupon = KTC/BAY credit-card points used as discount).

    KTC — 2-way: POS KTC-point coupons ↔ KTC-Point settlement rows; the bank
    leg rides INSIDE the combined KTC deposit (CREDIT·KTB lane), flagged
    bank_in_credit. BAY — 3-way: POS ↔ BAYPOINT settlement ↔ KBANK transfer
    credit (matched by payout_matching's BAYPOINT stream).

    Returns rows [{issuer, pos_point, settle_gross, bank_payout, bank_in_credit,
    variance, status}] — status: matched | mismatch | waiting_doc (POS points
    exist, no summary doc) | awaiting_payout | ext_only. Empty list when the
    branch has no point activity at all."""
    rows = []

    def _status(pos, settle):
        if settle > 0 and pos > 0:
            return "matched" if abs(settle - pos) <= TOL_ABS else "mismatch"
        return "waiting_doc" if pos > 0 else "ext_only"

    pos_ktc = point_pos_total(conn, branch_id, period_month, "KTC")
    settle_ktc = point_settlement_total(conn, branch_id, period_month, "KTCPOINT")
    if pos_ktc > 0 or settle_ktc > 0:
        rows.append({"issuer": "KTC", "pos_point": pos_ktc, "settle_gross": settle_ktc,
                     "bank_payout": None, "bank_in_credit": True,
                     "variance": round(settle_ktc - pos_ktc, 2),
                     "status": _status(pos_ktc, settle_ktc)})

    pos_bay = point_pos_total(conn, branch_id, period_month, "BAY")
    settle_bay = point_settlement_total(conn, branch_id, period_month, "BAYPOINT")
    if pos_bay > 0 or settle_bay > 0:
        bp = payout_matching(conn, branch_id, period_month).get("BAYPOINT") or {}
        status = _status(pos_bay, settle_bay)
        if status == "matched" and bp.get("awaiting_total", 0.0) > TOL_ABS:
            status = "awaiting_payout"            # summary ties to POS, money not landed yet
        rows.append({"issuer": "BAY", "pos_point": pos_bay, "settle_gross": settle_bay,
                     "bank_payout": round(bp.get("payout_total", 0.0), 2),
                     "bank_in_credit": False,
                     "variance": round(settle_bay - pos_bay, 2), "status": status})
    return rows
