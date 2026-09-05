"""Issuer loyalty points (KTC / BAY): POS point-redemption totals and the issuers' settlements.

Split out of recon/matcher.py on 2026-09-05 — same code, one lane per module;
recon.matcher re-exports every name."""
from __future__ import annotations
import sqlite3
from ._shared import _point_mids


# Loyalty-point redemptions are booked on POS as Coupon tenders under issuer-
# specific coupon series. Classify by series NAME (ids vary / grow over time).
_POINT_SERIES_KW = {"BAY": "กรุงศรี", "KTC": "KTC"}


def point_pos_total(conn, branch_id, period_month, provider):
    """POS amount the customers redeemed as <provider> loyalty points that month
    (Coupon tenders on the issuer's point-redemption series). 0 if the coupon-
    payment breakdown hasn't been synced yet."""
    kw = _POINT_SERIES_KW.get(provider)
    if not kw:
        return 0.0
    try:
        row = conn.execute("""
            SELECT COALESCE(SUM(p.amount), 0) FROM pos_coupon_payment_month p
            JOIN coupon_series s ON s.coupon_series_id = p.coupon_series_id
            WHERE p.branch_id=? AND p.period_month=? AND s.name LIKE ? AND s.name LIKE '%คะแนน%' COLLATE NOCASE
        """, (branch_id, period_month, f"%{kw}%")).fetchone()
        if row and row[0]:
            return round(float(row[0]), 2)
        # fall back: any matching-issuer series (some point series say 'แต้ม' not 'คะแนน')
        row = conn.execute("""
            SELECT COALESCE(SUM(p.amount), 0) FROM pos_coupon_payment_month p
            JOIN coupon_series s ON s.coupon_series_id = p.coupon_series_id
            WHERE p.branch_id=? AND p.period_month=? AND s.name LIKE ?
              AND (s.name LIKE '%คะแนน%' OR s.name LIKE '%แต้ม%' OR s.name LIKE '%point%')
        """, (branch_id, period_month, f"%{kw}%")).fetchone()
        return round(float(row[0] or 0), 2) if row else 0.0
    except sqlite3.OperationalError:
        return 0.0


def point_pos_total_daily(conn, branch_id, period_month):
    """Per-DAY POS amount redeemed as KTC+BAY loyalty points that month
    ({day: amount}), classified by coupon-series NAME exactly like
    point_pos_total (strict '%คะแนน%' per issuer, falling back to
    คะแนน/แต้ม/point only when the strict query is empty for that issuer).

    Sums BAY+KTC into one per-day figure; summed over the month it equals
    point_pos_total(KTC)+point_pos_total(BAY). Used by the accounting split
    that books KTC/BAY points as real 'received' money and the remainder of
    the Coupon tender as a discount deduction. {} when the coupon-payment
    breakdown hasn't been synced yet."""
    out: dict = {}
    base = ("SELECT p.day d, COALESCE(SUM(p.amount),0) amt FROM pos_coupon_payment_day p "
            "JOIN coupon_series s ON s.coupon_series_id = p.coupon_series_id "
            "WHERE p.branch_id=? AND substr(p.day,1,7)=? AND s.name LIKE ? ")
    try:
        for kw in _POINT_SERIES_KW.values():
            rows = conn.execute(
                base + "AND s.name LIKE '%คะแนน%' COLLATE NOCASE GROUP BY p.day",
                (branch_id, period_month, f"%{kw}%")).fetchall()
            if not rows:                                  # some point series say 'แต้ม' / 'point'
                rows = conn.execute(
                    base + "AND (s.name LIKE '%คะแนน%' OR s.name LIKE '%แต้ม%' "
                           "OR s.name LIKE '%point%') GROUP BY p.day",
                    (branch_id, period_month, f"%{kw}%")).fetchall()
            for r in rows:
                out[r[0]] = round(out.get(r[0], 0.0) + float(r[1] or 0), 2)
    except sqlite3.OperationalError:
        return {}
    return out


def coupon_discount_total_daily(conn, branch_id, period_month):
    """Per-DAY total of bill-level coupon DISCOUNTS ({day: amount}) from
    pos_coupon_day.discount_amount — the child / elderly / birthday / staff
    discounts that reduce the bill, after which the customer pays the NET via
    cash/QR/card. These never appear in the Coupon payment tender, so the
    accounting draft would otherwise show ส่วนลด = 0 even where discounts are huge.

    EXCLUDES point-redemption series (คะแนน / แต้ม / point): those settle as real
    issuer money via the Coupon tender (booked as revenue, not a discount), so
    counting them here would double up. {} when pos_coupon_day isn't synced yet."""
    try:
        rows = conn.execute(
            "SELECT p.day d, COALESCE(SUM(p.discount_amount),0) amt "
            "FROM pos_coupon_day p JOIN coupon_series s ON s.coupon_series_id=p.coupon_series_id "
            "WHERE p.branch_id=? AND substr(p.day,1,7)=? "
            "AND s.name NOT LIKE '%คะแนน%' AND s.name NOT LIKE '%แต้ม%' "
            "AND s.name NOT LIKE '%point%' COLLATE NOCASE "
            "GROUP BY p.day", (branch_id, period_month)).fetchall()
    except sqlite3.OperationalError:
        return {}
    return {r[0]: round(float(r[1] or 0), 2) for r in rows if (r[1] or 0)}


def _point_settlement_gross(conn, branch_id, period_month, point_acq):
    """Gross of the acquirer-settlement rows whose MID is a point MID (e.g. the
    KTC Point rows inside the KTC RTxInv), via recon_acquirer_mid."""
    pm = _point_mids(conn, branch_id)
    mids = [m for m, a in pm.items() if a == point_acq and m]
    if not mids:
        return 0.0
    ph = ",".join("?" * len(mids))
    try:
        row = conn.execute(
            f"SELECT COALESCE(SUM(gross_amount),0) FROM recon_cc_lines "
            f"WHERE branch_id=? AND strftime('%Y-%m',settlement_date)=? "
            f"AND TRIM(COALESCE(merchant_id,'')) IN ({ph})",
            (branch_id, period_month, *mids)).fetchone()
        return round(float(row[0] or 0), 2)
    except sqlite3.OperationalError:
        return 0.0


def point_settlement_total(conn, branch_id, period_month, point_acq):
    """Total gross of <point_acq> settlement rows for a branch/month, found by
    EITHER the row's own acquirer (the BAY Point doc parses as acquirer=BAYPOINT)
    OR a registered point MID (KTC Point rows hide inside the KTC RTxInv under
    acquirer=KTC, split by MID). One query — no double count when both match."""
    pm = _point_mids(conn, branch_id)
    mids = [m for m, a in pm.items() if a == point_acq and m]
    cond = "l.acquirer=?"
    params: list = [branch_id, period_month, point_acq]
    if mids:
        ph = ",".join("?" * len(mids))
        cond += f" OR TRIM(COALESCE(l.merchant_id,'')) IN ({ph})"
        params += mids
    try:
        row = conn.execute(
            f"SELECT COALESCE(SUM(l.gross_amount),0) FROM recon_cc_lines l "
            f"WHERE l.branch_id=? AND strftime('%Y-%m',l.settlement_date)=? AND ({cond})",
            params).fetchone()
        return round(float(row[0] or 0), 2)
    except sqlite3.OperationalError:
        return 0.0
