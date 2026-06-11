"""
Reconciliation engine — category/bank driven.

POS side: pos_payment_month, classified into (category, bank) buckets via the
editable recon_payment_map (seed_payment_map.py). Reconcile=0 codes (e.g.
ENTERTAIN comps) are skipped.

External side, per the owner's workflow:
  CASH               → bank statement CASH_DEP lines (either bank)
  QR(bank)           → bank statement QR lines for that bank
  TRANSFER(bank)     → bank statement TRANSFER lines for that bank
  CREDIT(acquirer)   → credit-card settlement gross for that acquirer
  GRAB / LINEMAN     → their report's gross (Completed orders)
  COUPON             → no external source yet → flagged for accounting

Fee + VAT are entered manually per line (Grab/Line Man/CC); the effective net
(gross − fee − VAT [− promo/refund]) is reported alongside.

Anything outside tolerance becomes a discrepancy for accounting staff.
"""

from __future__ import annotations
import json
import sqlite3
from datetime import date, timedelta

from .seed_payment_map import normalize

TOL_ABS = 100.0    # THB  — monthly tolerance
TOL_PCT = 0.5      # %
TOL_ABS_DAY = 50.0 # THB  — daily tolerance (smaller amounts per day)
TOL_PCT_DAY = 1.0  # %

# Bank-statement channels that map to a reconcilable POS category
_CHANNEL_CATEGORY = {"QR": "QR", "TRANSFER": "TRANSFER", "CASH_DEP": "CASH"}

# category → a representative source_type for the discrepancy row
_CATEGORY_SOURCE = {
    "CASH": "bank", "QR": "bank", "TRANSFER": "bank",
    "CREDIT": "credit_card", "GRAB": "grab", "LINEMAN": "lineman", "COUPON": "pos",
}


def _within_tol(ext: float, pos: float) -> bool:
    if abs(ext - pos) <= TOL_ABS:
        return True
    return abs(ext - pos) / max(abs(pos), 1.0) * 100 <= TOL_PCT


def _provider_bank(provider: str | None) -> str:
    p = (provider or "").lower()
    if "ktb" in p or "krungthai" in p or "กรุงไทย" in (provider or ""):       return "KTB"
    if "kbank" in p or "kasikorn" in p or "กสิกร" in (provider or ""):        return "KBANK"
    return "UNKNOWN"


def _provider_acquirer(provider: str | None) -> str:
    p = (provider or "").lower()
    if "ktc" in p:                                  return "KTC"
    if "ktb" in p or "krungthai" in p:              return "KTB"
    if "kbank" in p or "kasikorn" in p:             return "KBANK"
    return "UNKNOWN"


# ── POS side ──────────────────────────────────────────────────────────────────

def pos_category_totals(conn: sqlite3.Connection, branch_id: int, period_month: str):
    """Bucket pos_payment_month amounts into {(category, bank): amount} using the
    payment map. Returns (totals, unmapped) where unmapped is {norm_code: amount}
    for codes with no map row (so the UI can prompt staff to map them)."""
    try:
        pmap = {r["payment_code_norm"]: r for r in conn.execute(
            "SELECT payment_code_norm, category, bank, reconcile FROM recon_payment_map")}
        rows = conn.execute(
            "SELECT payment_code, amount FROM pos_payment_month WHERE branch_id=? AND period_month=?",
            (branch_id, period_month)).fetchall()
    except sqlite3.OperationalError:
        return {}, {}

    totals: dict = {}
    unmapped: dict = {}
    for r in rows:
        norm = normalize(r["payment_code"])
        amt = float(r["amount"] or 0)
        m = pmap.get(norm)
        if m is None:
            unmapped[norm] = unmapped.get(norm, 0.0) + amt
            continue
        if not m["reconcile"]:
            continue
        # Cash has no bank dimension on the statement side (a deposit is a deposit),
        # so collapse CASH to NONE even if the code was mapped to a bank.
        bank = "NONE" if m["category"] == "CASH" else m["bank"]
        key = (m["category"], bank)
        totals[key] = totals.get(key, 0.0) + amt
    return totals, unmapped


# ── External side ─────────────────────────────────────────────────────────────

def _eff_fee_vat(row, fee_col, vat_col):
    fee = row["fee_manual"] if row["fee_manual"] is not None else float(row[fee_col] or 0)
    if row["vat_manual"] is not None:
        vat = row["vat_manual"]
    else:
        vat = float(row[vat_col] or 0) if vat_col else 0.0
    return float(fee), float(vat)


def external_buckets(conn: sqlite3.Connection, branch_id: int, period_month: str) -> dict:
    """Aggregate external lines into {(category, bank): {amount, net, lines:[(table,id)]}}."""
    buckets: dict = {}

    def add(key, table, line_id, amount, net):
        b = buckets.setdefault(key, {"amount": 0.0, "net": 0.0, "lines": []})
        b["amount"] += amount
        b["net"]    += net
        b["lines"].append((table, line_id))

    # Bank statement: QR / TRANSFER / CASH_DEP credits ('excluded' on a bank
    # line = operator-flagged internal transfer, not revenue — skipped everywhere).
    # Lines the cash matcher claimed as deposits (often labelled QR/TRANSFER, e.g.
    # KTB 'SDCH') are re-bucketed to the CASH lane so monthly cash ties out and the
    # spurious QR/transfer ext_only disappears.
    cash_ids = cash_deposit_matching(conn, branch_id, period_month)["consumed_ids"]
    for r in conn.execute("""
        SELECT l.id, l.credit, l.channel, u.provider
        FROM recon_bank_lines l JOIN recon_uploads u ON u.upload_id = l.upload_id
        WHERE l.branch_id=? AND l.credit>0 AND l.match_status!='excluded'
          AND strftime('%Y-%m', l.txn_date)=?
    """, (branch_id, period_month)):
        amt = float(r["credit"] or 0)
        if r["id"] in cash_ids:
            add(("CASH", "NONE"), "recon_bank_lines", r["id"], amt, amt)
            continue
        cat = _CHANNEL_CATEGORY.get(r["channel"])
        if not cat:
            continue
        bank = "NONE" if cat == "CASH" else _provider_bank(r["provider"])
        add((cat, bank), "recon_bank_lines", r["id"], amt, amt)

    # Credit-card settlement: gross vs net (after effective fee/VAT)
    pmids = _point_mids(conn, branch_id)
    for r in conn.execute("""
        SELECT l.*, u.provider FROM recon_cc_lines l JOIN recon_uploads u ON u.upload_id = l.upload_id
        WHERE l.branch_id=? AND strftime('%Y-%m', l.settlement_date)=?
    """, (branch_id, period_month)):
        acq = _cc_acquirer(r, pmids)
        if acq.endswith("POINT"):      # loyalty (BAY/KTC point) — no POS-credit tender, reconciled separately
            continue
        fee, vat = _eff_fee_vat(r, "mdr_fee", "vat_amount")
        gross = float(r["gross_amount"] or 0)
        net = gross - float(r["refund_amount"] or 0) - fee - vat
        add(("CREDIT", acq), "recon_cc_lines", r["id"], gross, net)

    # Delivery reports: gross (food) vs net (after effective fee/VAT/promo); Completed only
    for cat, table, vat_col in (("GRAB", "recon_grab_lines", "vat_amount"),
                                ("LINEMAN", "recon_lineman_lines", "vat_on_commission")):
        for r in conn.execute(f"""
            SELECT * FROM {table}
            WHERE branch_id=? AND order_status='Completed' AND strftime('%Y-%m', order_date)=?
        """, (branch_id, period_month)):
            fee, vat = _eff_fee_vat(r, "commission_amt", vat_col)
            gross = float(r["food_amount"] or 0)
            net = gross - fee - vat - float(r["promo_deduct"] or 0)
            add((cat, "NONE"), table, r["id"], gross, net)

    return buckets


# ── Reconciliation (pure compute) ─────────────────────────────────────────────

def category_reconciliation(conn: sqlite3.Connection, branch_id: int, period_month: str):
    """Compare POS buckets to external buckets. Returns (rows, unmapped).

    Lane adjustments (recon_lane_adj) are applied at read-time: adj_pos is
    added to the POS side, adj_ext to the external side. No re-run-match needed.
    """
    pos, unmapped = pos_category_totals(conn, branch_id, period_month)
    ext = external_buckets(conn, branch_id, period_month)

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
            status = "matched" if _within_tol(ext_amt, pos_amt) else "mismatch"
        elif pos_amt > 0:
            status = "no_source" if category == "COUPON" else "pos_only"
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


# ── Payout chain (3-way tie): provider NET → bank payout credit ───────────────
# category_reconciliation ties POS gross ↔ provider/statement gross. This closes
# the loop: the NET a provider should remit (gross − fee − VAT − promo, or CC
# gross − refund − fee − VAT) must equal what actually credited the bank
# (GRAB_PAYOUT / LINEMAN_PAYOUT / CC_SETTLE lines, already classified by the bank
# parser). A gap here is a fee/skim/short-payment, not a sales discrepancy.

_PAYOUT_CHANNEL = {"GRAB": "GRAB_PAYOUT", "LINEMAN": "LINEMAN_PAYOUT", "CREDIT": "CC_SETTLE"}


def _report_net_by_category(conn, branch_id, period_month):
    """{category: net} for GRAB/LINEMAN/CREDIT from external_buckets (sum over banks)."""
    nets: dict = {}
    for (cat, _bank), b in external_buckets(conn, branch_id, period_month).items():
        if cat in _PAYOUT_CHANNEL:
            nets[cat] = nets.get(cat, 0.0) + b["net"]
    return nets


def _bank_payout_totals(conn, branch_id, period_month):
    """{category: bank-credit total} from recon_bank_lines payout channels."""
    out: dict = {}
    inv = {v: k for k, v in _PAYOUT_CHANNEL.items()}
    try:
        rows = conn.execute("""
            SELECT channel, SUM(credit) tot FROM recon_bank_lines
            WHERE branch_id=? AND credit>0 AND strftime('%Y-%m', txn_date)=?
              AND channel IN ('GRAB_PAYOUT','LINEMAN_PAYOUT','CC_SETTLE')
            GROUP BY channel
        """, (branch_id, period_month)).fetchall()
    except sqlite3.OperationalError:
        return out
    for r in rows:
        cat = inv.get(r["channel"])
        if cat:
            out[cat] = out.get(cat, 0.0) + float(r["tot"] or 0)
    return out


def payout_reconciliation(conn, branch_id, period_month):
    """Tie provider NET (should-remit) to the bank payout credit. Returns rows
    [{category, report_net, bank_payout, variance, status}]. status:
      matched           — both present, within tolerance
      mismatch          — both present, beyond tolerance (a real discrepancy)
      awaiting_payout   — net>0 but no bank payout line yet (informational: the
                          provider settles a few days later — not yet a gap)
      ext_only          — a bank payout line with no provider report behind it."""
    nets = _report_net_by_category(conn, branch_id, period_month)
    pays = _bank_payout_totals(conn, branch_id, period_month)
    rows = []
    for cat in sorted(set(nets) | set(pays)):
        net = round(nets.get(cat, 0.0), 2)
        pay = round(pays.get(cat, 0.0), 2)
        if net > 0 and pay > 0:
            status = "matched" if _within_tol(pay, net) else "mismatch"
        elif net > 0:
            status = "awaiting_payout"
        else:
            status = "ext_only"
        rows.append({"category": cat, "report_net": net, "bank_payout": pay,
                     "variance": round(pay - net, 2), "status": status})

    # BAY Point reimbursement has no GRAB_PAYOUT-style channel (it arrives as a
    # plain transfer), so derive its card from the amount-matched view.
    bp = payout_matching(conn, branch_id, period_month).get("BAYPOINT")
    if bp and (bp["net_total"] > TOL_ABS or bp["payout_total"] > TOL_ABS):
        net, pay = round(bp["net_total"], 2), round(bp["payout_total"], 2)
        status = ("awaiting_payout" if bp["awaiting_total"] > TOL_ABS
                  else "matched" if abs(pay - net) <= TOL_ABS else "mismatch")
        rows.append({"category": "BAYPOINT", "report_net": net, "bank_payout": pay,
                     "variance": round(pay - net, 2), "status": status,
                     "pos_point": bp.get("pos_point_total", 0.0)})

    # KTC Point: 2-way only (POS KTC-point coupon ↔ the KTC-point settlement rows,
    # split by MID). The bank leg is inside the combined KTC deposit (CREDIT·KTC),
    # so there is no separate bank credit to match here.
    ktcp_settle = _point_settlement_gross(conn, branch_id, period_month, "KTCPOINT")
    ktcp_pos = point_pos_total(conn, branch_id, period_month, "KTC")
    if ktcp_settle > TOL_ABS or ktcp_pos > TOL_ABS:
        rows.append({"category": "KTCPOINT", "report_net": round(ktcp_settle, 2),
                     "bank_payout": None, "pos_point": round(ktcp_pos, 2),
                     "variance": round(ktcp_settle - ktcp_pos, 2),
                     "status": "matched" if abs(ktcp_settle - ktcp_pos) <= TOL_ABS else "mismatch",
                     "bank_in_credit": True})
    return rows


# ── Per-payout auto-grouping (net days ↔ bank credit, handles combined days) ───
# Providers remit each day's NET a day or two later, often grouping 2–3 days into
# one bank credit (e.g. weekends). On KBank those land as plain "รับโอนเงิน"
# (TRANSFER) with no Grab/Lineman tag, so we match by AMOUNT + date-lag, grouping
# consecutive net-days until they equal a bank credit. This gives the operator a
# day-level trace ("payout 8 พ.ค. = net 6+7 พ.ค.") instead of a month lump.

_PAYOUT_CANDIDATE_CHANNELS = {
    "GRAB":    ("GRAB_PAYOUT", "TRANSFER"),
    "LINEMAN": ("LINEMAN_PAYOUT", "TRANSFER"),
    "CREDIT":  ("CC_SETTLE", "TRANSFER"),
    "BAYPOINT": ("TRANSFER",),          # Krungsri point reimbursement lands as a bank transfer
}


def _pd(s: str) -> date:
    return date(int(s[0:4]), int(s[5:7]), int(s[8:10]))


def _payout_net_by_day(conn, branch_id, period_month):
    """{category: {day: net}} for GRAB/LINEMAN/CREDIT from the provider reports."""
    out: dict = {}

    def add(cat, day, net):
        b = out.setdefault(cat, {})
        b[day] = round(b.get(day, 0.0) + net, 2)

    for r in conn.execute(
        "SELECT * FROM recon_cc_lines WHERE branch_id=? AND strftime('%Y-%m',settlement_date)=?",
        (branch_id, period_month)):
        # BAY Point is a separate document + separate bank credit → own stream.
        # KTC Point settles INSIDE the KTC deposit (one combined KTB credit), so
        # it stays in CREDIT for the bank leg (the gross split happens elsewhere).
        if r["acquirer"] == "BAYPOINT":
            add("BAYPOINT", r["settlement_date"], float(r["net_amount"] or 0))
            continue
        fee, vat = _eff_fee_vat(r, "mdr_fee", "vat_amount")
        add("CREDIT", r["settlement_date"],
            float(r["gross_amount"] or 0) - float(r["refund_amount"] or 0) - fee - vat)
    for cat, table, vat_col in (("GRAB", "recon_grab_lines", "vat_amount"),
                                ("LINEMAN", "recon_lineman_lines", "vat_on_commission")):
        for r in conn.execute(
            f"SELECT * FROM {table} WHERE branch_id=? AND order_status='Completed' "
            f"AND strftime('%Y-%m',order_date)=?", (branch_id, period_month)):
            fee, vat = _eff_fee_vat(r, "commission_amt", vat_col)
            add(cat, r["order_date"],
                float(r["food_amount"] or 0) - fee - vat - float(r["promo_deduct"] or 0))
    return out


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


def _point_mids(conn, branch_id):
    """{merchant_id: acquirer} for point-redemption MIDs of a branch (e.g. the
    KTC Point MID), from recon_acquirer_mid. Lets us split point rows out of an
    acquirer settlement (KTC RTxInv mixes regular KTC + KTC Point by MID)."""
    try:
        return {(r["merchant_id"] or "").strip(): r["acquirer"]
                for r in conn.execute(
                    "SELECT merchant_id, acquirer FROM recon_acquirer_mid "
                    "WHERE branch_id=? AND acquirer LIKE '%POINT' AND COALESCE(active,1)=1 "
                    "AND COALESCE(merchant_id,'')<>''", (branch_id,))}
    except sqlite3.OperationalError:
        return {}


def _cc_acquirer(row, point_mids):
    """Effective acquirer for a cc settlement row: a point MID overrides to its
    point acquirer (KTCPOINT), else the row's own acquirer."""
    mid = (row["merchant_id"] or "").strip() if "merchant_id" in row.keys() else ""
    if mid and mid in point_mids:
        return point_mids[mid]
    return row["acquirer"] or _provider_acquirer(row["provider"]) if "provider" in row.keys() else row["acquirer"]


def _payout_candidate_credits(conn, branch_id, period_month, channels):
    ph = ",".join("?" * len(channels))
    try:
        rows = conn.execute(f"""
            SELECT id, txn_date, credit, channel FROM recon_bank_lines
            WHERE branch_id=? AND credit>0 AND channel IN ({ph})
              AND match_status!='excluded'
              AND strftime('%Y-%m',txn_date)=?
            ORDER BY txn_date, id
        """, (branch_id, *channels, period_month)).fetchall()
    except sqlite3.OperationalError:
        return []
    return [{"id": r["id"], "date": r["txn_date"], "amount": float(r["credit"] or 0),
             "channel": r["channel"]} for r in rows]


def payout_matching(conn, branch_id, period_month, max_group=4, max_lag=7):
    """Per-provider: group consecutive net-days to the bank credit that pays them.
    Returns {cat: {payouts:[...], net_total, payout_total, awaiting_total}}.
    Each payout row: {payout_date, amount, days:[{day,net}], group_net, combined,
    lag, status('matched'|'awaiting')}. 'awaiting' = a net-day with no matching
    credit yet (late/month-boundary or a short payment to investigate)."""
    nets = _payout_net_by_day(conn, branch_id, period_month)
    result: dict = {}
    for cat, channels in _PAYOUT_CANDIDATE_CHANNELS.items():
        days = sorted((d, n) for d, n in nets.get(cat, {}).items() if n > TOL_ABS_DAY)
        credits = _payout_candidate_credits(conn, branch_id, period_month, channels)
        used: set = set()
        payouts, di = [], 0
        while di < len(days):
            hit = None
            s = 0.0
            for k in range(1, min(max_group, len(days) - di) + 1):
                s = round(s + days[di + k - 1][1], 2)
                last_day = days[di + k - 1][0]
                for c in credits:                       # earliest unused credit that fits
                    if c["id"] in used or abs(c["amount"] - s) > TOL_ABS:
                        continue
                    lag = (_pd(c["date"]) - _pd(last_day)).days
                    if 0 <= lag <= max_lag:
                        hit = (k, c); break
                if hit:
                    break
            if hit:
                k, c = hit
                used.add(c["id"])
                grp = days[di:di + k]
                payouts.append({
                    "payout_date": c["date"], "amount": round(c["amount"], 2),
                    "days": [{"day": d, "net": round(n, 2)} for d, n in grp],
                    "group_net": round(sum(n for _, n in grp), 2),
                    "combined": k > 1, "lag": (_pd(c["date"]) - _pd(grp[-1][0])).days,
                    "status": "matched",
                })
                di += k
            else:
                d, n = days[di]
                payouts.append({"payout_date": None, "amount": 0.0,
                                "days": [{"day": d, "net": round(n, 2)}], "group_net": round(n, 2),
                                "combined": False, "lag": None, "status": "awaiting"})
                di += 1
        result[cat] = {
            "payouts": payouts,
            "net_total":    round(sum(n for _, n in days), 2),
            "payout_total": round(sum(p["amount"] for p in payouts if p["status"] == "matched"), 2),
            "awaiting_total": round(sum(p["group_net"] for p in payouts if p["status"] == "awaiting"), 2),
        }
    # BAY Point is a 3-way: also surface the POS leg (Krungsri point coupons).
    if "BAYPOINT" in result:
        result["BAYPOINT"]["pos_point_total"] = point_pos_total(conn, branch_id, period_month, "BAY")
    return result


# ── Credit card: full 3-document match (POS ↔ settlement ↔ bank), per acquirer ──
# The owner's accounting chain for cards is three documents:
#   1) POS gross  ↔  credit-card settlement summary gross (before fee & VAT)
#   2) settlement net (gross − refund − MDR fee − VAT)  ↔  bank statement credit
# The bank only ever credits the NET, so the gap between gross and the bank line is
# exactly the fee+VAT — already stored on each settlement line and pulled in
# automatically here (via _eff_fee_vat), so it explains the gap instead of looking
# like a discrepancy. We split per acquirer (CREDIT·KTB / CREDIT·KBANK) because each
# acquirer settles into its own bank account.

# Acquirer → the bank account its settlement nets into. KTB/KBANK have statements;
# KTC has no own bank account here (its bank leg, if any, rides a KTB/KBANK transfer).
_ACQ_BANK = {"KTB": "KTB", "KBANK": "KBANK"}


def _match_net_days_to_credits(net_by_day, credits, max_group=4, max_lag=7):
    """Group 1..max_group consecutive net-days to the earliest unused bank credit
    equal to the group sum (daily tolerance) within max_lag days. Mirrors the
    grouping in payout_matching/cash_deposit_matching. Returns
    (matched_total, used_ids, groups[{credit_id, date, amount, days:[{day,net}]}])."""
    days = sorted((d, n) for d, n in net_by_day.items() if n > TOL_ABS_DAY)
    used: set = set()
    groups = []
    di = 0
    while di < len(days):
        hit = None
        s = 0.0
        for k in range(1, min(max_group, len(days) - di) + 1):
            s = round(s + days[di + k - 1][1], 2)
            last_day = days[di + k - 1][0]
            for c in credits:
                if c["id"] in used or not _within_tol_day(c["amount"], s):
                    continue
                lag = (_pd(c["date"]) - _pd(last_day)).days
                if 0 <= lag <= max_lag:
                    hit = (k, c)
                    break
            if hit:
                break
        if hit:
            k, c = hit
            used.add(c["id"])
            grp = days[di:di + k]
            groups.append({"credit_id": c["id"], "date": c["date"],
                           "amount": round(c["amount"], 2),
                           "days": [{"day": d, "net": round(n, 2)} for d, n in grp]})
            di += k
        else:
            di += 1
    return round(sum(g["amount"] for g in groups), 2), used, groups


def _credit_bank_candidates(conn, branch_id, period_month, bank, exclude_ids=None):
    """Bank credit lines that could be a card settlement landing on `bank` this
    month. Acquirers remit the net under inconsistent codes — KTB posts CC_SETTLE
    (SDCK / 'รับเงินจากการขาย'), KBank often a plain รับโอนเงิน (TRANSFER) — so we
    consider both channels and match by amount + date-lag (same idea as
    payout_matching)."""
    exclude_ids = exclude_ids or set()
    try:
        rows = conn.execute("""
            SELECT l.id, l.txn_date, l.credit, l.channel, u.provider
            FROM recon_bank_lines l JOIN recon_uploads u ON u.upload_id=l.upload_id
            WHERE l.branch_id=? AND l.credit>0
              AND l.match_status NOT IN ('manual','excluded')
              AND l.channel IN ('CC_SETTLE','TRANSFER')
              AND strftime('%Y-%m', l.txn_date)=?
            ORDER BY l.txn_date, l.id
        """, (branch_id, period_month)).fetchall()
    except sqlite3.OperationalError:
        return []
    out = []
    for r in rows:
        if r["id"] in exclude_ids:
            continue
        if _provider_bank(r["provider"]) != bank:
            continue
        out.append({"id": r["id"], "date": r["txn_date"],
                    "amount": float(r["credit"] or 0), "channel": r["channel"]})
    return out


def credit_three_way(conn, branch_id, period_month, exclude_ids=None):
    """Per-acquirer 3-document credit reconciliation. Returns
    {rows: [...], bank_line_ids: set}. Each row:
      acquirer, bank, pos_gross, settle_gross, settle_refund, settle_fee (MDR+VAT,
      auto-pulled), settle_net, bank_credit, pos_leg, bank_leg, fee_variance,
      bank_groups (day-traces of the bank match).
    `exclude_ids` are bank-line ids already claimed elsewhere (e.g. by cash
    matching) so they aren't double-counted as a card settlement."""
    exclude_ids = set(exclude_ids or set())
    pos, _unmapped = pos_category_totals(conn, branch_id, period_month)
    pos_credit = {bank: amt for (cat, bank), amt in pos.items() if cat == "CREDIT"}

    pmids = _point_mids(conn, branch_id)
    agg: dict = {}        # acq -> {gross, refund, fee, net}
    net_day: dict = {}    # acq -> {day: net}
    for r in conn.execute("""
        SELECT l.*, u.provider FROM recon_cc_lines l JOIN recon_uploads u ON u.upload_id=l.upload_id
        WHERE l.branch_id=? AND strftime('%Y-%m', l.settlement_date)=?
    """, (branch_id, period_month)):
        acq = _cc_acquirer(r, pmids)
        if acq.endswith("POINT"):     # loyalty point settles separately (own stream)
            continue
        fee, vat = _eff_fee_vat(r, "mdr_fee", "vat_amount")
        gross = float(r["gross_amount"] or 0)
        refund = float(r["refund_amount"] or 0)
        net = gross - refund - fee - vat
        a = agg.setdefault(acq, {"gross": 0.0, "refund": 0.0, "fee": 0.0, "net": 0.0})
        a["gross"] += gross
        a["refund"] += refund
        a["fee"] += fee + vat
        a["net"] += net
        nd = net_day.setdefault(acq, {})
        nd[r["settlement_date"]] = nd.get(r["settlement_date"], 0.0) + net

    rows = []
    bank_line_ids: set = set()
    for acq in sorted(set(agg) | set(pos_credit)):
        a = agg.get(acq, {"gross": 0.0, "refund": 0.0, "fee": 0.0, "net": 0.0})
        pos_gross = pos_credit.get(acq, 0.0)
        bank = _ACQ_BANK.get(acq)
        bank_credit, used, groups = 0.0, set(), []
        if bank:
            credits = _credit_bank_candidates(conn, branch_id, period_month, bank,
                                              exclude_ids | bank_line_ids)
            bank_credit, used, groups = _match_net_days_to_credits(net_day.get(acq, {}), credits)
            bank_line_ids |= used

        # POS leg: POS gross ↔ settlement gross
        if pos_gross > 0 and a["gross"] > 0:
            pos_leg = "matched" if _within_tol(a["gross"], pos_gross) else "mismatch"
        elif pos_gross > 0:
            pos_leg = "pos_only"          # POS booked a card tender, no settlement doc yet
        else:
            pos_leg = "ext_only"          # settlement with no POS card tender

        # Bank leg: settlement net ↔ bank credit (fee already auto-pulled into net)
        if a["net"] > TOL_ABS and bank_credit > 0:
            bank_leg = "matched" if _within_tol(bank_credit, a["net"]) else "mismatch"
        elif a["net"] > TOL_ABS:
            bank_leg = "awaiting" if bank else "no_bank"   # net to land / no statement acct
        elif bank_credit > 0:
            bank_leg = "ext_only"
        else:
            bank_leg = "none"

        rows.append({
            "acquirer":      acq,
            "bank":          bank or acq,
            "pos_gross":     round(pos_gross, 2),
            "settle_gross":  round(a["gross"], 2),
            "settle_refund": round(a["refund"], 2),
            "settle_fee":    round(a["fee"], 2),       # MDR + VAT, auto-pulled
            "settle_net":    round(a["net"], 2),
            "bank_credit":   round(bank_credit, 2),
            "pos_leg":       pos_leg,
            "bank_leg":      bank_leg,
            "pos_variance":  round(a["gross"] - pos_gross, 2),
            "bank_variance": round(bank_credit - a["net"], 2),
            "bank_groups":   groups,
        })
    return {"rows": rows, "bank_line_ids": bank_line_ids}


# ── Line-level drill-down ─────────────────────────────────────────────────────
# The external lines that make up a (category, bank) lane, so staff can see what
# drives a variance. (POS has no per-receipt tender split — the POS side stays an
# aggregate — but the statement/report side is fully itemised.)

def lane_lines(conn, branch_id, period_month, category, bank, day=None):
    """List the external lines behind a lane. `day`='YYYY-MM-DD' narrows to one day."""
    def _df(col):
        return (f" AND {col}=?", [day]) if day else (f" AND strftime('%Y-%m',{col})=?", [period_month])

    out = []
    if bank == "PAYOUT":                                  # the bank payout credit behind a payout tie
        chan = _PAYOUT_CHANNEL.get(category)
        if not chan:
            return out
        df, p = _df("l.txn_date")
        for r in conn.execute(f"""
            SELECT l.txn_date d, l.description ref, l.credit amt, u.provider
            FROM recon_bank_lines l JOIN recon_uploads u ON u.upload_id=l.upload_id
            WHERE l.branch_id=? AND l.credit>0 AND l.channel=?{df} ORDER BY l.txn_date
        """, [branch_id, chan] + p):
            out.append({"date": r["d"], "ref": r["ref"] or "", "amount": round(float(r["amt"] or 0), 2),
                        "detail": r["provider"] or ""})
        return out

    if category in ("CASH", "QR", "TRANSFER"):
        chan = {"CASH": "CASH_DEP", "QR": "QR", "TRANSFER": "TRANSFER"}[category]
        # CASH_DEP: the deposit lands on sale_day+1. When drilling a single POS day,
        # query txn_date = day+1 so the caller sees the matching bank line.
        if category == "CASH" and day:
            try:
                deposit_day = (date.fromisoformat(day) + timedelta(days=1)).isoformat()
                df, p = " AND l.txn_date=?", [deposit_day]
            except (ValueError, TypeError):
                df, p = _df("l.txn_date")
        else:
            df, p = _df("l.txn_date")
        for r in conn.execute(f"""
            SELECT l.id, l.match_status ms, l.txn_date d, l.description ref, l.credit amt, u.provider
            FROM recon_bank_lines l JOIN recon_uploads u ON u.upload_id=l.upload_id
            WHERE l.branch_id=? AND l.credit>0 AND l.channel=?{df} ORDER BY l.txn_date
        """, [branch_id, chan] + p):
            rb = "NONE" if category == "CASH" else _provider_bank(r["provider"])
            if rb != bank:
                continue
            out.append({"id": r["id"], "match_status": r["ms"],
                        "date": r["d"], "ref": r["ref"] or "", "amount": round(float(r["amt"] or 0), 2),
                        "detail": r["provider"] or ""})
        return out

    if category == "CREDIT":
        df, p = _df("l.settlement_date")
        for r in conn.execute(f"""
            SELECT l.settlement_date d, l.batch_no ref, l.gross_amount amt, l.acquirer, u.provider
            FROM recon_cc_lines l JOIN recon_uploads u ON u.upload_id=l.upload_id
            WHERE l.branch_id=?{df} ORDER BY l.settlement_date
        """, [branch_id] + p):
            acq = r["acquirer"] or _provider_acquirer(r["provider"])
            if acq != bank:
                continue
            out.append({"date": r["d"], "ref": r["ref"] or "", "amount": round(float(r["amt"] or 0), 2),
                        "detail": acq})
        return out

    if category in ("GRAB", "LINEMAN"):
        table = "recon_grab_lines" if category == "GRAB" else "recon_lineman_lines"
        df, p = _df("order_date")
        for r in conn.execute(f"""
            SELECT order_date d, order_id ref, food_amount amt, order_status
            FROM {table} WHERE branch_id=? AND order_status='Completed'{df} ORDER BY order_date
        """, [branch_id] + p):
            out.append({"date": r["d"], "ref": r["ref"] or "", "amount": round(float(r["amt"] or 0), 2),
                        "detail": r["order_status"]})
    return out


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

    for r in rows:
        conn.execute("""
            INSERT INTO recon_category_result
                (session_id, category, bank, pos_amount, ext_amount, ext_net, variance, status)
            VALUES (?,?,?,?,?,?,?,?)
        """, (session_id, r["category"], r["bank"], r["pos_amount"], r["ext_amount"],
              r["ext_net"], r["variance"], r["status"]))

        if r["status"] == "matched":
            for table, line_id in r["ext_lines"]:
                conn.execute(f"UPDATE {table} SET match_status='matched' WHERE id=?", (line_id,))
        else:
            disc_type = {
                "mismatch":  "amount_mismatch",
                "ext_only":  "unmatched_external",
                "pos_only":  "unmatched_pos",
                "no_source": "unmatched_pos",
            }[r["status"]]
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
        if p["status"] == "mismatch":
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


# ── Daily reconciliation ──────────────────────────────────────────────────────
# Staff reconcile per day. Some statements arrive late, so each day/lane carries a
# status that distinguishes a genuine gap ('missing' — the statement covers that
# day but the line isn't there) from an awaited document ('pending' — no statement
# yet covers that day).

def _within_tol_day(ext: float, pos: float) -> bool:
    if abs(ext - pos) <= TOL_ABS_DAY:
        return True
    return abs(ext - pos) / max(abs(pos), 1.0) * 100 <= TOL_PCT_DAY


def pos_category_totals_daily(conn, branch_id, period_month):
    """{(day, category, bank): amount} from pos_payment_day via the payment map.
    Returns (totals, unmapped, available). available=False if pos_payment_day
    isn't synced yet; unmapped = {code: amount} for codes with no map row."""
    try:
        pmap = {r["payment_code_norm"]: r for r in conn.execute(
            "SELECT payment_code_norm, category, bank, reconcile FROM recon_payment_map")}
        rows = conn.execute(
            "SELECT day, payment_code, amount FROM pos_payment_day WHERE branch_id=? AND substr(day,1,7)=?",
            (branch_id, period_month)).fetchall()
    except sqlite3.OperationalError:
        return {}, {}, False
    totals, unmapped = {}, {}
    for r in rows:
        norm = normalize(r["payment_code"])
        amt = float(r["amount"] or 0)
        m = pmap.get(norm)
        if m is None:
            unmapped[norm] = unmapped.get(norm, 0.0) + amt
            continue
        if not m["reconcile"]:
            continue
        bank = "NONE" if m["category"] == "CASH" else m["bank"]   # cash: no bank dimension
        key = (r["day"], m["category"], bank)
        totals[key] = totals.get(key, 0.0) + amt
    return totals, unmapped, True


def _manual_consumed(conn, branch_id, period_month):
    """{(day, category, bank): amount} reconciled manually (tick-to-match).
    These POS day amounts are considered cleared, so the daily views subtract
    them and mark fully-cleared lane-days as 'manual'."""
    out: dict = {}
    try:
        rows = conn.execute(
            "SELECT category, bank, pos_days FROM recon_manual_match "
            "WHERE branch_id=? AND period_month=?", (branch_id, period_month)).fetchall()
    except sqlite3.OperationalError:
        return out
    for r in rows:
        for x in json.loads(r["pos_days"] or "[]"):
            k = (x["day"], r["category"], r["bank"])
            out[k] = out.get(k, 0.0) + float(x.get("amount") or 0)
    return out


def _external_buckets_daily(conn, branch_id, period_month, include_cash=True, exclude_ids=None):
    """{(day, category, bank): {amount, net}} from external lines, keyed by line date.
    Manually-matched lines (match_status='manual') are excluded — they're accounted
    for in the manual layer, so they don't double-count or show a false variance on
    the lump-sum's own date.

    include_cash=False skips CASH_DEP lines: daily_reconciliation matches cash
    through cash_deposit_matching() (handles T+1, same-day, and multi-day lump
    deposits), so the naive shift-back-one-day bucket would double-count.

    exclude_ids: bank-line ids the cash matcher has claimed as cash deposits (they
    may be parsed as QR/TRANSFER, e.g. KTB 'SDCH') — skip them here so they don't
    also show as an ext_only QR/transfer line on their own date."""
    exclude_ids = exclude_ids or set()
    buckets = {}
    def add(day, cat, bank, amount, net):
        b = buckets.setdefault((day, cat, bank), {"amount": 0.0, "net": 0.0})
        b["amount"] += amount; b["net"] += net

    for r in conn.execute("""
        SELECT l.id, l.txn_date d, l.credit, l.channel, u.provider
        FROM recon_bank_lines l JOIN recon_uploads u ON u.upload_id=l.upload_id
        WHERE l.branch_id=? AND l.credit>0
          AND l.match_status NOT IN ('manual','excluded')
          AND strftime('%Y-%m', l.txn_date)=?
    """, (branch_id, period_month)):
        if r["id"] in exclude_ids:
            continue
        cat = _CHANNEL_CATEGORY.get(r["channel"])
        if not cat:
            continue
        bank = "NONE" if cat == "CASH" else _provider_bank(r["provider"])
        # CASH_DEP lands on the bank a day after the sale (next-business-day deposit).
        # Shift the key back 1 day so the bucket aligns with the POS sale day.
        if cat == "CASH":
            if not include_cash:
                continue
            try:
                key_day = (date.fromisoformat(r["d"]) - timedelta(days=1)).isoformat()
            except (ValueError, TypeError):
                key_day = r["d"]
            if key_day[:7] != period_month:   # deposit in next month → out of scope
                continue
        else:
            key_day = r["d"]
        add(key_day, cat, bank, float(r["credit"] or 0), float(r["credit"] or 0))

    for r in conn.execute("""
        SELECT l.*, u.provider FROM recon_cc_lines l JOIN recon_uploads u ON u.upload_id=l.upload_id
        WHERE l.branch_id=? AND l.match_status!='manual' AND strftime('%Y-%m', l.settlement_date)=?
    """, (branch_id, period_month)):
        acq = r["acquirer"] or _provider_acquirer(r["provider"])
        fee, vat = _eff_fee_vat(r, "mdr_fee", "vat_amount")
        gross = float(r["gross_amount"] or 0)
        add(r["settlement_date"], "CREDIT", acq, gross,
            gross - float(r["refund_amount"] or 0) - fee - vat)

    for cat, table, vat_col in (("GRAB", "recon_grab_lines", "vat_amount"),
                                ("LINEMAN", "recon_lineman_lines", "vat_on_commission")):
        for r in conn.execute(f"""
            SELECT * FROM {table}
            WHERE branch_id=? AND order_status='Completed' AND match_status!='manual'
              AND strftime('%Y-%m', order_date)=?
        """, (branch_id, period_month)):
            fee, vat = _eff_fee_vat(r, "commission_amt", vat_col)
            gross = float(r["food_amount"] or 0)
            add(r["order_date"], cat, "NONE", gross, gross - fee - vat - float(r["promo_deduct"] or 0))
    return buckets


def _coverage(conn, branch_id, period_month):
    """Latest date each external source has data for. Days after this (with POS
    activity but no line) are 'pending' (document not yet arrived), not 'missing'."""
    cov = {}
    for r in conn.execute("""
        SELECT u.provider, MAX(l.txn_date) mx
        FROM recon_bank_lines l JOIN recon_uploads u ON u.upload_id=l.upload_id
        WHERE l.branch_id=? AND strftime('%Y-%m', l.txn_date)=? GROUP BY u.provider
    """, (branch_id, period_month)):
        cov[("bank", _provider_bank(r["provider"]))] = r["mx"]
    # Cash coverage = latest CASH_DEP line only (not QR/TRANSFER), else a QR line
    # on a later day would falsely mark cash days 'missing' instead of 'pending'.
    cash_mx = conn.execute(
        "SELECT MAX(txn_date) FROM recon_bank_lines "
        "WHERE branch_id=? AND channel='CASH_DEP' AND strftime('%Y-%m',txn_date)=?",
        (branch_id, period_month)).fetchone()[0]
    if cash_mx:
        # Deposit date = sale day + 1; coverage in POS terms = last deposit_date - 1.
        try:
            cov[("cash", None)] = (date.fromisoformat(cash_mx) - timedelta(days=1)).isoformat()
        except (ValueError, TypeError):
            cov[("cash", None)] = cash_mx
    else:
        # Cash often posts under non-CASH_DEP codes (e.g. KTB 'SDCH'), so there may
        # be no literal CASH_DEP line yet the deposits are really there. Coverage =
        # last bank-statement day − 1: a deposit lands T+1, so the last sale day we
        # could possibly see banked is (last statement day − 1). Days beyond → still
        # awaiting next period's statement ('pending'); earlier gaps → 'missing'.
        bank_cov = [v for (src, _b), v in cov.items() if src == "bank" and v]
        if bank_cov:
            try:
                cov[("cash", None)] = (date.fromisoformat(max(bank_cov)) - timedelta(days=1)).isoformat()
            except (ValueError, TypeError):
                cov[("cash", None)] = max(bank_cov)
    for r in conn.execute("""
        SELECT u.provider, MAX(l.settlement_date) mx
        FROM recon_cc_lines l JOIN recon_uploads u ON u.upload_id=l.upload_id
        WHERE l.branch_id=? AND strftime('%Y-%m', l.settlement_date)=? GROUP BY u.provider
    """, (branch_id, period_month)):
        cov[("cc", _provider_acquirer(r["provider"]))] = r["mx"]
    for src, table in (("grab", "recon_grab_lines"), ("lineman", "recon_lineman_lines")):
        mx = conn.execute(
            f"SELECT MAX(order_date) FROM {table} WHERE branch_id=? AND strftime('%Y-%m',order_date)=?",
            (branch_id, period_month)).fetchone()[0]
        if mx:
            cov[(src, None)] = mx
    return cov


def _lane_cov_key(category, bank):
    if category == "CASH":                  return ("cash", None)
    if category in ("QR", "TRANSFER"):      return ("bank", bank)
    if category == "CREDIT":                return ("cc", bank)
    if category == "GRAB":                  return ("grab", None)
    if category == "LINEMAN":               return ("lineman", None)
    return (category.lower(), None)         # COUPON etc → no external source


_REAL_BANKS = {"KTB", "KBANK"}              # banks we get statements from


# ── Cash deposit matching (T+1 / same-day / multi-day lump sums) ──────────────
# Cash is normally banked the NEXT day (T+1). But weekends and holidays produce
# one lump deposit covering 2–3 sale days, some branches deposit same-day, and
# the last days of a month are deposited in the NEXT month. A fixed one-day
# shift can't express any of that, so cash gets its own grouped matcher
# (mirroring payout_matching): group 1..max_group consecutive POS-cash days and
# find the earliest unused CASH_DEP credit equal to the group sum.

def _cash_pos_days(conn, branch_id, period_month):
    """{day: pos_cash_amount} for the month (all CASH lanes, bank collapsed)."""
    pos, _unmapped, available = pos_category_totals_daily(conn, branch_id, period_month)
    out: dict = {}
    for (day, cat, _bank), amt in pos.items():
        if cat == "CASH":
            out[day] = out.get(day, 0.0) + amt
    return out, available


def _claimed_lanes(conn, branch_id, period_month):
    """(category, bank) lanes that mapped POS electronic tenders settle into —
    their bank credits are 'spoken for' (QR/transfer/card) and must NOT be
    grabbed by cash matching, so a real KNNQR-on-KBANK line is never mistaken
    for a cash deposit."""
    try:
        pos, _u, _a = pos_category_totals_daily(conn, branch_id, period_month)
    except sqlite3.OperationalError:
        return set()
    return {(cat, bank) for (_day, cat, bank), amt in pos.items()
            if cat in ("QR", "TRANSFER", "CREDIT") and amt > 0}


def _cash_dep_credits(conn, branch_id, period_month, max_lag):
    """Bank credits that could be a cash lump deposit for this month.

    Banks post the next-day cash deposit under inconsistent codes (CASH_DEP,
    but also SDCH / รับโอนเงิน / plain transfer depending on the account), so we
    can't match on the channel label alone. Candidates therefore are:
      • every line literally parsed as CASH_DEP, plus
      • QR/TRANSFER credits in a lane NO mapped POS tender claims (an
        unexplained credit — cash is the only tender with no electronic trail).
    Card settlements, fees and payout legs are never cash. Window = the month
    plus the first max_lag days of the next month (day-31 cash banked on the 1st).
    """
    y, m = int(period_month[:4]), int(period_month[5:7])
    month_start = date(y, m, 1).isoformat()
    nm_y, nm_m = (y + 1, 1) if m == 12 else (y, m + 1)
    next_cutoff = (date(nm_y, nm_m, 1) + timedelta(days=max_lag)).isoformat()
    claimed = _claimed_lanes(conn, branch_id, period_month)
    try:
        rows = conn.execute("""
            SELECT l.id, l.txn_date, l.credit, l.channel, u.provider
            FROM recon_bank_lines l JOIN recon_uploads u ON u.upload_id=l.upload_id
            WHERE l.branch_id=? AND l.credit>0
              AND l.match_status NOT IN ('manual','excluded')
              AND l.txn_date>=? AND l.txn_date<?
            ORDER BY l.txn_date, l.id
        """, (branch_id, month_start, next_cutoff)).fetchall()
    except sqlite3.OperationalError:
        return []
    out = []
    for r in rows:
        ch = r["channel"]
        if ch != "CASH_DEP":
            cat = _CHANNEL_CATEGORY.get(ch)          # only QR/TRANSFER are credible cash codes
            if cat not in ("QR", "TRANSFER"):
                continue
            if (cat, _provider_bank(r["provider"])) in claimed:
                continue                             # a real electronic lane — leave it alone
        out.append({"id": r["id"], "date": r["txn_date"],
                    "amount": float(r["credit"] or 0), "channel": ch})
    return out


def cash_deposit_matching(conn, branch_id, period_month, max_group=4, max_lag=3):
    """Group POS-cash days to the CASH_DEP bank credits that banked them.

    Returns {
      groups:   [{deposit_date, amount, days:[{day,pos}], group_pos, lag,
                  combined, variance}],         — matched deposits
      day_group: {day: index into groups},      — which group covers a POS day
      unmatched_pos:    [day, ...],             — cash days with no deposit found
      unmatched_credits:[{id, date, amount}],   — deposits matching no POS days
      pos_total, deposit_total, matched_pos_total,
      has_deposit_lines: bool,                  — False = no CASH_DEP lines AT ALL
      pos_available: bool,
    }"""
    cash_days, available = _cash_pos_days(conn, branch_id, period_month)
    credits = _cash_dep_credits(conn, branch_id, period_month, max_lag)
    days = sorted((d, a) for d, a in cash_days.items() if a > TOL_ABS_DAY)

    groups, day_group, used = [], {}, set()
    di = 0
    while di < len(days):
        hit = None
        s = 0.0
        for k in range(1, min(max_group, len(days) - di) + 1):
            s = round(s + days[di + k - 1][1], 2)
            last_day = days[di + k - 1][0]
            for c in credits:                        # earliest unused credit that fits
                if c["id"] in used or not _within_tol_day(c["amount"], s):
                    continue
                lag = (_pd(c["date"]) - _pd(last_day)).days
                if 0 <= lag <= max_lag:
                    hit = (k, c)
                    break
            if hit:
                break
        if hit:
            k, c = hit
            used.add(c["id"])
            grp = days[di:di + k]
            gp = round(sum(a for _, a in grp), 2)
            day_group.update({d: len(groups) for d, _ in grp})
            groups.append({
                "deposit_date": c["date"], "amount": round(c["amount"], 2),
                "line_id": c["id"],
                "days": [{"day": d, "pos": round(a, 2)} for d, a in grp],
                "group_pos": gp, "lag": (_pd(c["date"]) - _pd(grp[-1][0])).days,
                "combined": k > 1, "variance": round(c["amount"] - gp, 2),
            })
            di += k
        else:
            di += 1

    matched_days = set(day_group)
    return {
        "groups": groups,
        "day_group": day_group,
        "consumed_ids": used,                         # bank-line ids now accounted as cash
        "unmatched_pos": [d for d, _ in days if d not in matched_days],
        # Only LITERAL cash-deposit lines that found no sales are surfaced as cash
        # ext_only. Broadened candidates (QR/transfer the matcher merely considered)
        # stay in their own lane if unused — they aren't necessarily cash.
        "unmatched_credits": [c for c in credits
                              if c["id"] not in used and c.get("channel") == "CASH_DEP"],
        "pos_total": round(sum(cash_days.values()), 2),
        "deposit_total": round(sum(g["amount"] for g in groups), 2),
        "matched_pos_total": round(sum(g["group_pos"] for g in groups), 2),
        # True = we found & matched at least one cash deposit. False → the loud
        # "no cash deposit found" alert (statements cover the month but cash never
        # reconciled — a real gap, not a late document).
        "has_deposit_lines": bool(groups),
        "pos_available": available,
    }


def daily_reconciliation(conn, branch_id, period_month):
    """Per-day, per-lane reconciliation with pending tracking for late documents."""
    pos, unmapped, pos_available = pos_category_totals_daily(conn, branch_id, period_month)
    cash = cash_deposit_matching(conn, branch_id, period_month)
    # Lines the cash matcher claimed (may be labelled QR/TRANSFER, e.g. KTB SDCH)
    # are excluded from the electronic buckets so they aren't double-counted.
    ext = _external_buckets_daily(conn, branch_id, period_month,
                                  include_cash=False, exclude_ids=cash["consumed_ids"])
    cov = _coverage(conn, branch_id, period_month)
    consumed = _manual_consumed(conn, branch_id, period_month)   # manual tick-to-match

    # Feed matched cash groups back in as per-day external amounts: each POS day
    # in a group is covered by its deposit (the group's variance lands on the
    # LAST day so a lump's rounding shows once, not on every member day).
    cash_day_info: dict = {}
    for gi, g in enumerate(cash["groups"]):
        for j, dd in enumerate(g["days"]):
            is_last = j == len(g["days"]) - 1
            ext_share = dd["pos"] + (g["variance"] if is_last else 0.0)
            cash_day_info[dd["day"]] = {
                "ext": ext_share, "deposit_date": g["deposit_date"],
                "combined": g["combined"], "lag": g["lag"], "group": gi,
            }
    # Unmatched deposits surface as ext_only on deposit_date − 1 (the sale day
    # they most plausibly belong to under the normal T+1 rhythm).
    for c in cash["unmatched_credits"]:
        try:
            kd = (date.fromisoformat(c["date"]) - timedelta(days=1)).isoformat()
        except (ValueError, TypeError):
            kd = c["date"]
        if kd[:7] != period_month:
            continue
        b = ext.setdefault((kd, "CASH", "NONE"), {"amount": 0.0, "net": 0.0})
        b["amount"] += c["amount"]; b["net"] += c["amount"]

    by_day = {}
    for (day, cat, bank) in set(pos) | set(ext):
        pos_amt = pos.get((day, cat, bank), 0.0)
        cons    = consumed.get((day, cat, bank), 0.0)     # manually cleared
        pos_eff = pos_amt - cons                          # POS still needing a match
        eb = ext.get((day, cat, bank))
        ext_amt = eb["amount"] if eb else 0.0
        ext_net = eb["net"] if eb else 0.0
        cash_info = cash_day_info.get(day) if cat == "CASH" else None
        if cash_info:                              # day covered by a matched deposit group
            ext_amt += cash_info["ext"]
            ext_net += cash_info["ext"]
            eb = True

        if cons > 0 and abs(pos_eff) <= TOL_ABS_DAY and ext_amt <= TOL_ABS_DAY:
            status = "manual"                      # fully reconciled by hand
        elif eb and pos_eff != 0:                  # both sides present (pos may be a refund)
            status = "matched" if _within_tol_day(ext_amt, pos_eff) else "variance"
        elif eb:                                   # pos cleared/0, ext present
            status = "ext_only"
        elif cat == "COUPON":
            status = "no_source"
        elif cat in ("QR", "TRANSFER") and bank not in _REAL_BANKS:
            status = "unmapped_bank"               # map this code's bank in Setup (e.g. PROMPTPAY/UNKNOWN)
        elif cat == "CREDIT" and bank not in (_REAL_BANKS | {"KTC"}):
            status = "unmapped_bank"
        else:                                      # pos > 0, real bank, no external line
            cmax = cov.get(_lane_cov_key(cat, bank))
            status = "missing" if (cmax and day <= cmax) else "pending"

        lane = {
            "category": cat, "bank": bank,
            "pos": round(pos_amt, 2), "ext": round(ext_amt, 2), "ext_net": round(ext_net, 2),
            "manual": round(cons, 2),
            "variance": 0.0 if status == "manual" else round(ext_amt - pos_eff, 2),
            "status": status,
        }
        if cash_info:                              # deposit trace for the UI tooltip
            lane["deposit_date"] = cash_info["deposit_date"]
            lane["deposit_lag"] = cash_info["lag"]
            lane["deposit_combined"] = cash_info["combined"]
        by_day.setdefault(day, []).append(lane)

    days = []
    pending_by_source = {}
    for day in sorted(by_day):
        lanes = sorted(by_day[day], key=lambda x: (x["category"], x["bank"]))
        pending = [l for l in lanes if l["status"] == "pending"]
        issues  = [l for l in lanes if l["status"] in ("variance", "missing", "ext_only", "unmapped_bank")]
        for l in pending:
            src = _CATEGORY_SOURCE.get(l["category"], "pos")
            pending_by_source[src] = pending_by_source.get(src, 0) + 1
        days.append({
            "day": day,
            "pos_total": round(sum(l["pos"] for l in lanes), 2),
            "ext_total": round(sum(l["ext"] for l in lanes), 2),
            "lanes": lanes,
            "pending": [{"category": l["category"], "bank": l["bank"]} for l in pending],
            "matched": sum(1 for l in lanes if l["status"] in ("matched", "manual")),
            "pending_count": len(pending),
            "issue_count": len(issues),
        })

    return {
        "pos_daily_available": pos_available,
        "days": days,
        "unmapped": [{"code": k, "amount": round(v, 2)} for k, v in sorted(unmapped.items())],
        # Cash banking summary: surfaces "เงินสดไม่เคยถูกนำฝาก" loud and clear.
        "cash_summary": {
            "pos_total":         cash["pos_total"],
            "deposit_total":     cash["deposit_total"],
            "matched_pos_total": cash["matched_pos_total"],
            "matched_groups":    len(cash["groups"]),
            "combined_groups":   sum(1 for g in cash["groups"] if g["combined"]),
            "unmatched_days":    len(cash["unmatched_pos"]),
            "has_deposit_lines": cash["has_deposit_lines"],
            "groups":            cash["groups"],
            "unmatched_credits": cash["unmatched_credits"],
        },
        "pending_summary": {
            "pending_lane_days": sum(d["pending_count"] for d in days),
            "days_with_pending": sum(1 for d in days if d["pending_count"]),
            "by_source": pending_by_source,
            "coverage": {f"{k[0]}/{k[1] or '-'}": v for k, v in cov.items()},
        },
    }


# ── Cross-branch daily board ───────────────────────────────────────────────────
# One chosen day, every branch at once, broken down by payment category. This is
# the company-wide "did today's money land?" view. POS revenue per channel comes
# from pos_payment_day (mapped via recon_payment_map); the external side is the
# same uploaded documents, summed on their own line date. Categories collapse over
# bank here (a branch row is wide enough already) — the per-branch Daily tab keeps
# the bank-level split.

_BOARD_CAT_ORDER = ["CASH", "QR", "TRANSFER", "CREDIT", "GRAB", "LINEMAN", "COUPON"]

# Categories that get one board column PER acquirer/bank instead of one combined
# column. CREDIT → e.g. กรุงไทย vs กสิกร, matching the rows in recon_payment_map.
_SPLIT_BY_BANK = {"CREDIT"}
_BANK_LABEL = {"KTB": "กรุงไทย", "KBANK": "กสิกร", "KTC": "KTC", "SCB": "SCB",
               "BBL": "กรุงเทพ", "BAY": "กรุงศรี"}

def _col_key(category: str, bank: str | None) -> str:
    """Column identity on the board. Split categories key on category+bank so each
    acquirer is its own column; everything else stays one column per category."""
    if category in _SPLIT_BY_BANK and bank and bank != "UNKNOWN":
        return f"{category}:{bank}"
    return category

def _col_label(category: str, bank: str | None) -> str:
    if category in _SPLIT_BY_BANK and bank and bank != "UNKNOWN":
        return f"{category} {_BANK_LABEL.get(bank, bank)}"
    return category


def lane_pos_days(conn, branch_id, period_month, category, bank):
    """Per-day POS totals for one (category, bank) lane in a month — the POS side
    of the manual tick-to-match screen."""
    pmap = {r["payment_code_norm"]: r for r in conn.execute(
        "SELECT payment_code_norm, category, bank, reconcile FROM recon_payment_map")}
    days: dict = {}
    for r in conn.execute(
        "SELECT day, payment_code, SUM(amount) amt FROM pos_payment_day "
        "WHERE branch_id=? AND substr(day,1,7)=? GROUP BY day, payment_code",
        (branch_id, period_month)):
        m = pmap.get(normalize(r["payment_code"]))
        if not m or not m["reconcile"]:
            continue
        if m["category"] != category or m["bank"] != bank:
            continue
        days[r["day"]] = days.get(r["day"], 0.0) + float(r["amt"] or 0)
    return [{"day": d, "total": round(v, 2)} for d, v in sorted(days.items())]


def lane_ext_lines(conn, branch_id, period_month, category, bank):
    """Statement lines for one (category, bank) lane in a month, WITH table+id+
    match_status so the manual matcher can tick and reverse them."""
    out = []
    def add(table, lid, date, ref, detail, amount, ms):
        out.append({"table": table, "id": lid, "date": date, "ref": ref or "",
                    "detail": detail or "", "amount": round(float(amount or 0), 2),
                    "match_status": ms})

    if category in ("CASH", "QR", "TRANSFER"):
        chan = {"CASH": "CASH_DEP", "QR": "QR", "TRANSFER": "TRANSFER"}[category]
        for r in conn.execute("""
            SELECT l.id, l.txn_date d, l.description ref, l.credit amt, l.match_status ms, u.provider
            FROM recon_bank_lines l JOIN recon_uploads u ON u.upload_id=l.upload_id
            WHERE l.branch_id=? AND l.credit>0 AND l.channel=? AND strftime('%Y-%m',l.txn_date)=?
            ORDER BY l.txn_date""", (branch_id, chan, period_month)):
            rb = "NONE" if category == "CASH" else _provider_bank(r["provider"])
            if rb != bank:
                continue
            add("recon_bank_lines", r["id"], r["d"], r["ref"], r["provider"], r["amt"], r["ms"])
    elif category == "CREDIT":
        for r in conn.execute("""
            SELECT l.id, l.settlement_date d, l.batch_no ref, l.gross_amount amt,
                   l.acquirer, l.match_status ms, u.provider
            FROM recon_cc_lines l JOIN recon_uploads u ON u.upload_id=l.upload_id
            WHERE l.branch_id=? AND strftime('%Y-%m',l.settlement_date)=?
            ORDER BY l.settlement_date""", (branch_id, period_month)):
            acq = r["acquirer"] or _provider_acquirer(r["provider"])
            if acq != bank:
                continue
            add("recon_cc_lines", r["id"], r["d"], r["ref"], acq, r["amt"], r["ms"])
    elif category in ("GRAB", "LINEMAN"):
        table = "recon_grab_lines" if category == "GRAB" else "recon_lineman_lines"
        for r in conn.execute(f"""
            SELECT id, order_date d, order_id ref, food_amount amt, order_status, match_status ms
            FROM {table} WHERE branch_id=? AND order_status='Completed'
              AND strftime('%Y-%m',order_date)=? ORDER BY order_date""",
            (branch_id, period_month)):
            add(table, r["id"], r["d"], r["ref"], r["order_status"], r["amt"], r["ms"])
    return out


# ── Wrong-channel suggestions (same bank, equal amount, ±1 day) ───────────────
# Common data-entry error: a sale is booked under the wrong POS payment code, so
# the POS lands in lane A (e.g. QR·KBANK) while the money shows on the statement
# under lane B (e.g. TRANSFER·KBANK). It surfaces as a POS surplus in A and an
# unmatched statement line in B of the SAME amount, same bank, same/adjacent day.
# We detect those pairs and return ready-to-apply manual-match payloads — the
# operator just clicks Apply (which POSTs to /api/manual).

def _d(s: str) -> date:
    return date(int(s[0:4]), int(s[5:7]), int(s[8:10]))


def _pick_ext_lines(conn, branch_id, period_month, category, bank, day, amount):
    """Unmatched statement lines in lane (category,bank) on `day` that sum to
    ~amount. Prefer a single exact line; else greedily accumulate. Returns
    [(table, id, amount)] or [] if nothing fits."""
    cand = [l for l in lane_ext_lines(conn, branch_id, period_month, category, bank)
            if l["date"] == day and l["match_status"] != "manual"]
    for l in cand:                                   # exact single line first
        if abs(l["amount"] - amount) <= TOL_ABS_DAY:
            return [(l["table"], l["id"], l["amount"])]
    picked, run = [], 0.0                             # else greedy sum
    for l in sorted(cand, key=lambda x: -x["amount"]):
        if run >= amount - TOL_ABS_DAY:
            break
        picked.append((l["table"], l["id"], l["amount"]))
        run += l["amount"]
    return picked if abs(run - amount) <= TOL_ABS_DAY else []


def suggest_reclassifications(conn, branch_id, period_month, max_day_gap=1):
    """Detect likely wrong-channel bookings (same bank, different category, equal
    amount, within ±max_day_gap days). Returns a list of suggestions, each a
    ready-to-POST /api/manual payload plus display fields."""
    pos, _unmapped, available = pos_category_totals_daily(conn, branch_id, period_month)
    if not available:
        return []
    consumed = _manual_consumed(conn, branch_id, period_month)
    ext = _external_buckets_daily(conn, branch_id, period_month)   # excludes manual lines

    # Net imbalance per (category, bank, day): POS-surplus vs external-surplus.
    pos_surplus, ext_surplus = [], []
    for (d, c, b) in set(pos) | set(ext):
        p = pos.get((d, c, b), 0.0) - consumed.get((d, c, b), 0.0)
        e = ext.get((d, c, b), {}).get("amount", 0.0)
        net = round(p - e, 2)
        if net > TOL_ABS_DAY:
            pos_surplus.append((c, b, d, net))
        elif net < -TOL_ABS_DAY:
            ext_surplus.append((c, b, d, -net))

    out, used_pos, used_ext = [], set(), set()
    for ca, ba, da, xa in sorted(pos_surplus, key=lambda t: -t[3]):
        if (ca, ba, da) in used_pos:
            continue
        # Best opposite lane: same bank, different category, closest day, equal amount.
        best = None
        for cb, bb, db, xb in ext_surplus:
            if bb != ba or cb == ca or (cb, bb, db) in used_ext:
                continue
            gap = abs((_d(da) - _d(db)).days)
            if gap > max_day_gap or abs(xa - xb) > TOL_ABS_DAY:
                continue
            if best is None or gap < best[0]:
                best = (gap, cb, bb, db, xb)
        if not best:
            continue
        gap, cb, bb, db, xb = best
        lines = _pick_ext_lines(conn, branch_id, period_month, cb, bb, db, xb)
        if not lines:
            continue
        used_pos.add((ca, ba, da))
        used_ext.add((cb, bb, db))
        out.append({
            "bank":       ba,
            "amount":     round(xa, 2),
            "confidence": "high" if gap == 0 else "med",
            "day_gap":    gap,
            # ready-to-POST /api/manual payload
            "category":     ca,           # record the match under the POS lane
            "period_month": period_month,
            "branch_id":    branch_id,
            "pos_days":   [{"day": da, "amount": round(xa, 2)}],
            "ext":        [{"table": t, "id": i, "amount": round(a, 2)} for (t, i, a) in lines],
            # display
            "pos_lane": f"{ca}·{ba}",
            "ext_lane": f"{cb}·{bb}",
            "pos_day":  da,
            "ext_day":  db,
            "note": (f"Suggested: POS {ca}·{ba} ↔ statement {cb}·{bb}, "
                     f"equal {xa:,.0f} on {da}" + ("" if gap == 0 else f" / {db}")),
        })
    return out


def pos_codes_scope(conn: sqlite3.Connection, branch_id: int,
                    day: str | None = None, period_month: str | None = None):
    """POS payment codes for a branch on one day (or month), each tagged with its
    mapping status from recon_payment_map. This is the finest POS grain available
    (no per-receipt tender split exists), so 'transaction-level' = code-level.
    Returns a list sorted by amount desc."""
    pmap = {r["payment_code_norm"]: r for r in conn.execute(
        "SELECT payment_code_norm, category, bank, reconcile FROM recon_payment_map")}
    if day:
        sql = ("SELECT payment_code, SUM(amount) amt, SUM(txn_count) n "
               "FROM pos_payment_day WHERE branch_id=? AND day=? GROUP BY payment_code")
        params = (branch_id, day)
    else:
        sql = ("SELECT payment_code, SUM(amount) amt, SUM(txn_count) n "
               "FROM pos_payment_day WHERE branch_id=? AND substr(day,1,7)=? GROUP BY payment_code")
        params = (branch_id, period_month)
    out = []
    for r in conn.execute(sql, params):
        nm = normalize(r["payment_code"])
        m = pmap.get(nm)
        bank = m["bank"] if m else None
        out.append({
            "code": r["payment_code"], "norm": nm,
            "amount": round(float(r["amt"] or 0), 2), "txn_count": int(r["n"] or 0),
            "mapped": m is not None,
            "category": m["category"] if m else None,
            "bank": bank,
            "reconcile": int(m["reconcile"]) if m else 0,
            # action item if no map row, or mapped to a bank we can't reconcile against
            "needs_map": (m is None) or (bank in ("UNKNOWN", "PROMPTPAY")),
        })
    out.sort(key=lambda x: -x["amount"])
    return out


# ── Per-document match status (live, no Run Match needed) ─────────────────────
# "How much of this statement reconciled?" answered read-only by lane: a line is
# matched if it was hand-matched (manual) or its (category,bank) lane reconciled
# this month. 'excluded' lines (ignored internal transfers) are reported apart,
# out of the denominator. Because it's recomputed on read, the numbers are always
# current — staff never have to remember to press Run Match first.

def _line_status(ms, cat, bank, lane_status, cash_ids=None, line_id=None):
    if ms == "excluded":
        return "excluded"
    if ms == "manual":
        return "matched"
    # A line the cash matcher claimed as a deposit is matched, whatever the bank
    # labelled its channel (KTB posts cash as 'SDCH', parsed as QR).
    if cash_ids and line_id in cash_ids:
        return "matched"
    return "matched" if lane_status.get((cat, bank)) == "matched" else "unmatched"


def _lane_status_map(conn, branch_id, period_month):
    rows, _ = category_reconciliation(conn, branch_id, period_month)
    return {(r["category"], r["bank"]): r["status"] for r in rows}


def upload_match_stats(conn, branch_id, period_month):
    """{upload_id: {total, matched, excluded}} computed live by lane status."""
    lane_status = _lane_status_map(conn, branch_id, period_month)
    cash = cash_deposit_matching(conn, branch_id, period_month)
    cash_ids = {g["line_id"] for g in cash["groups"]}

    out: dict = {}
    def bump(uid, key):
        d = out.setdefault(uid, {"total": 0, "matched": 0, "excluded": 0})
        d[key] += 1

    for r in conn.execute("""
        SELECT l.id, l.upload_id, l.channel, l.match_status ms, u.provider
        FROM recon_bank_lines l JOIN recon_uploads u ON u.upload_id=l.upload_id
        WHERE l.branch_id=? AND l.credit>0 AND strftime('%Y-%m', l.txn_date)=?
    """, (branch_id, period_month)):
        cat = _CHANNEL_CATEGORY.get(r["channel"])
        if not cat:                       # FEE / OTHER / payout legs — not a revenue lane
            continue
        bank = "NONE" if cat == "CASH" else _provider_bank(r["provider"])
        st = _line_status(r["ms"], cat, bank, lane_status, cash_ids, r["id"])
        if st == "excluded":
            bump(r["upload_id"], "excluded"); continue
        bump(r["upload_id"], "total")
        if st == "matched":
            bump(r["upload_id"], "matched")

    for r in conn.execute("""
        SELECT l.upload_id, l.match_status ms, l.acquirer, u.provider
        FROM recon_cc_lines l JOIN recon_uploads u ON u.upload_id=l.upload_id
        WHERE l.branch_id=? AND strftime('%Y-%m', l.settlement_date)=?
    """, (branch_id, period_month)):
        bank = r["acquirer"] or _provider_acquirer(r["provider"])
        st = _line_status(r["ms"], "CREDIT", bank, lane_status)
        if st == "excluded":
            bump(r["upload_id"], "excluded"); continue
        bump(r["upload_id"], "total")
        if st == "matched":
            bump(r["upload_id"], "matched")

    for cat, table in (("GRAB", "recon_grab_lines"), ("LINEMAN", "recon_lineman_lines")):
        for r in conn.execute(f"""
            SELECT upload_id, match_status ms FROM {table}
            WHERE branch_id=? AND order_status='Completed' AND strftime('%Y-%m', order_date)=?
        """, (branch_id, period_month)):
            st = _line_status(r["ms"], cat, "NONE", lane_status)
            if st == "excluded":
                bump(r["upload_id"], "excluded"); continue
            bump(r["upload_id"], "total")
            if st == "matched":
                bump(r["upload_id"], "matched")
    return out


# ── Bank-statement coverage (bank-first: is every credit line explained?) ─────
# Reconcile/Daily are POS-first. This view is BANK-first: walk every credit line on
# the statements and answer "did this money get explained?" — matched to a revenue
# lane (cash/QR/transfer), consumed as a cash deposit, tied to a provider net payout
# (incl. the CREDIT 3-document chain, where the fee+VAT is auto-pulled to bridge
# gross→net), ignored as an internal transfer, or still unmatched (→ staff matches it
# in POS). Non-revenue debits/fees are informational and stay out of the denominator.

_PAYOUT_CHANNELS = {"CC_SETTLE", "GRAB_PAYOUT", "LINEMAN_PAYOUT"}
_PAYOUT_CHAN_CAT = {"CC_SETTLE": "CREDIT", "GRAB_PAYOUT": "GRAB", "LINEMAN_PAYOUT": "LINEMAN"}


def bank_statement_coverage(conn, branch_id, period_month):
    """Per-line coverage of every bank-statement credit for a branch/month, plus the
    CREDIT 3-document chain. Returns {totals, groups, credit_three_way,
    unmatched_lines, lines}."""
    lane_status = _lane_status_map(conn, branch_id, period_month)
    cash = cash_deposit_matching(conn, branch_id, period_month)
    cash_ids = set(cash["consumed_ids"]) | {g["line_id"] for g in cash["groups"]}

    # CREDIT 3-document chain (per acquirer) — also yields the bank lines it consumed.
    c3 = credit_three_way(conn, branch_id, period_month, exclude_ids=cash_ids)
    credit_ids = set(c3["bank_line_ids"])

    # Grab / Line Man net payouts → which bank credits they consumed. Share one 'used'
    # set seeded with cash + credit ids so a single line is never double-claimed.
    used = set(cash_ids) | credit_ids
    payout_ids = {"GRAB": set(), "LINEMAN": set()}
    net_by_day_all = _payout_net_by_day(conn, branch_id, period_month)
    for cat in ("GRAB", "LINEMAN"):
        cand = [c for c in _payout_candidate_credits(
                    conn, branch_id, period_month, _PAYOUT_CANDIDATE_CHANNELS[cat])
                if c["id"] not in used]
        _tot, u, _g = _match_net_days_to_credits(net_by_day_all.get(cat, {}), cand)
        payout_ids[cat] = u
        used |= u

    lines = []
    groups: dict = {}
    tot = {"matched": 0, "unmatched": 0, "excluded": 0, "informational": 0,
           "amt_matched": 0.0, "amt_unmatched": 0.0, "amt_excluded": 0.0}

    for r in conn.execute("""
        SELECT l.id, l.txn_date d, l.description ref, l.credit amt, l.channel,
               l.match_status ms, u.provider
        FROM recon_bank_lines l JOIN recon_uploads u ON u.upload_id=l.upload_id
        WHERE l.branch_id=? AND l.credit>0 AND strftime('%Y-%m', l.txn_date)=?
        ORDER BY l.txn_date, l.id
    """, (branch_id, period_month)):
        amt = float(r["amt"] or 0)
        chan = r["channel"]
        bankp = _provider_bank(r["provider"])
        cat = _CHANNEL_CATEGORY.get(chan)        # CASH/QR/TRANSFER or None

        if r["ms"] == "excluded":
            status, group, lane_bank = "excluded", "excluded", bankp
        elif r["id"] in cash_ids:
            status, group, lane_bank = "matched", "CASH", "NONE"
        elif r["id"] in credit_ids:
            status, group, lane_bank = "matched", "CREDIT", bankp
        elif r["id"] in payout_ids["GRAB"]:
            status, group, lane_bank = "matched", "GRAB", "NONE"
        elif r["id"] in payout_ids["LINEMAN"]:
            status, group, lane_bank = "matched", "LINEMAN", "NONE"
        elif chan in _PAYOUT_CHANNELS:           # payout leg with no provider net behind it
            status, group, lane_bank = "unmatched", _PAYOUT_CHAN_CAT[chan], bankp
        elif cat:
            bank = "NONE" if cat == "CASH" else bankp
            st = _line_status(r["ms"], cat, bank, lane_status, cash_ids, r["id"])
            status = "matched" if st == "matched" else "unmatched"
            group, lane_bank = cat, bank
        else:                                     # FEE / OTHER credits — not a revenue lane
            status, group, lane_bank = "informational", "OTHER", bankp

        lines.append({"id": r["id"], "date": r["d"], "ref": r["ref"] or "",
                      "amount": round(amt, 2), "channel": chan, "bank": lane_bank,
                      "category": group, "status": status})

        if status == "excluded":
            tot["excluded"] += 1; tot["amt_excluded"] += amt
        elif status == "informational":
            tot["informational"] += 1
        else:
            tot[status] += 1
            tot["amt_matched" if status == "matched" else "amt_unmatched"] += amt
        g = groups.setdefault(group, {"matched": 0, "unmatched": 0, "excluded": 0,
                                      "informational": 0, "count": 0,
                                      "amt_matched": 0.0, "amt_unmatched": 0.0})
        g["count"] += 1
        g[status] += 1
        if status == "matched":
            g["amt_matched"] += amt
        elif status == "unmatched":
            g["amt_unmatched"] += amt

    denom = tot["matched"] + tot["unmatched"]
    return {
        "totals": {
            "lines_total":      denom,
            "matched":          tot["matched"],
            "unmatched":        tot["unmatched"],
            "excluded":         tot["excluded"],
            "informational":    tot["informational"],
            "amount_matched":   round(tot["amt_matched"], 2),
            "amount_unmatched": round(tot["amt_unmatched"], 2),
            "amount_excluded":  round(tot["amt_excluded"], 2),
            "pct_matched":      round(100.0 * tot["matched"] / denom, 1) if denom else 100.0,
        },
        "groups": {k: {**v, "amt_matched": round(v["amt_matched"], 2),
                       "amt_unmatched": round(v["amt_unmatched"], 2)}
                   for k, v in sorted(groups.items())},
        "credit_three_way": c3["rows"],
        "unmatched_lines":  [l for l in lines if l["status"] == "unmatched"],
        "lines":            lines,
    }


# ── Bank suggestions for unmapped POS codes ───────────────────────────────────
# The biggest setup blocker is a code like KNNQR (฿80M) sitting on UNKNOWN bank.
# But the documents already know the answer: if the code's per-day POS series
# tracks one statement lane's per-day credits (minus what mapped codes already
# explain), that lane's bank is where the code settles. We surface that as a
# one-click suggestion instead of making staff guess.

def map_bank_suggestions(conn, branch_id, period_month, min_hits=4, min_score=0.6):
    """Suggest (category, bank) for needs-map POS codes by matching their daily
    series against statement-lane residuals. Returns ranked suggestions:
    [{code, norm, map_id, category, bank, hits, active_days, score,
      pos_total, ext_total, sample:[{day, pos, ext}]}]"""
    try:
        codes = pos_codes_scope(conn, branch_id, period_month=period_month)
    except sqlite3.OperationalError:
        return []
    cands = [c for c in codes if c["needs_map"] and c["amount"] > TOL_ABS_DAY]
    if not cands:
        return []

    pmap = {r["payment_code_norm"]: r for r in conn.execute(
        "SELECT id, payment_code_norm, category, bank, reconcile FROM recon_payment_map")}

    # Per-code and per-mapped-lane POS daily series
    code_series: dict = {}
    lane_pos: dict = {}
    for r in conn.execute(
        "SELECT day, payment_code, SUM(amount) amt FROM pos_payment_day "
        "WHERE branch_id=? AND substr(day,1,7)=? GROUP BY day, payment_code",
        (branch_id, period_month)):
        norm = normalize(r["payment_code"])
        amt = float(r["amt"] or 0)
        cs = code_series.setdefault(norm, {})
        cs[r["day"]] = cs.get(r["day"], 0.0) + amt
        m = pmap.get(norm)
        if m and m["reconcile"] and m["bank"] in _REAL_BANKS | {"KTC"}:
            lp = lane_pos.setdefault((m["category"], m["bank"]), {})
            lp[r["day"]] = lp.get(r["day"], 0.0) + amt

    # Statement-lane daily credits (independent of the POS map)
    lane_ext: dict = {}
    for r in conn.execute("""
        SELECT l.txn_date d, l.credit, l.channel, u.provider
        FROM recon_bank_lines l JOIN recon_uploads u ON u.upload_id=l.upload_id
        WHERE l.branch_id=? AND l.credit>0 AND l.match_status!='excluded'
          AND strftime('%Y-%m', l.txn_date)=?
          AND l.channel IN ('QR','TRANSFER')
    """, (branch_id, period_month)):
        key = (r["channel"], _provider_bank(r["provider"]))
        le = lane_ext.setdefault(key, {})
        le[r["d"]] = le.get(r["d"], 0.0) + float(r["credit"] or 0)
    for r in conn.execute("""
        SELECT l.settlement_date d, l.gross_amount g, l.acquirer, u.provider
        FROM recon_cc_lines l JOIN recon_uploads u ON u.upload_id=l.upload_id
        WHERE l.branch_id=? AND strftime('%Y-%m', l.settlement_date)=?
    """, (branch_id, period_month)):
        key = ("CREDIT", r["acquirer"] or _provider_acquirer(r["provider"]))
        le = lane_ext.setdefault(key, {})
        le[r["d"]] = le.get(r["d"], 0.0) + float(r["g"] or 0)

    # Residual = statement minus what mapped POS already explains
    lane_resid: dict = {}
    for key, ed in lane_ext.items():
        pd_ = lane_pos.get(key, {})
        lane_resid[key] = {d: v - pd_.get(d, 0.0) for d, v in ed.items()}

    out = []
    for c in cands:
        cs = code_series.get(c["norm"], {})
        active = [(d, v) for d, v in sorted(cs.items()) if v > TOL_ABS_DAY]
        if len(active) < min_hits:
            continue
        # category fixed when already mapped (e.g. KNNQR is QR/UNKNOWN); else open
        cats = [c["category"]] if c["category"] in ("QR", "TRANSFER", "CREDIT") else \
               ["QR", "TRANSFER", "CREDIT"]
        best = None
        for (lcat, lbank), resid in lane_resid.items():
            if lcat not in cats or lbank == "UNKNOWN":
                continue
            hits, sample = 0, []
            for d, v in active:
                # CC settles T+1/T+2 → also accept the next two days' settlement
                if lcat == "CREDIT":
                    days_try = [d,
                                (_pd(d) + timedelta(days=1)).isoformat(),
                                (_pd(d) + timedelta(days=2)).isoformat()]
                else:
                    days_try = [d]
                ev = next((resid.get(dt) for dt in days_try
                           if resid.get(dt) is not None), None)
                if ev is not None and _within_tol_day(ev, v):
                    hits += 1
                    if len(sample) < 3:
                        sample.append({"day": d, "pos": round(v, 2), "ext": round(ev, 2)})
            score = hits / len(active)
            if hits >= min_hits and score >= min_score:
                cand = {"category": lcat, "bank": lbank, "hits": hits, "score": score,
                        "sample": sample,
                        "ext_total": round(sum(max(x, 0.0) for x in resid.values()), 2)}
                if best is None or (cand["hits"], cand["score"]) > (best["hits"], best["score"]):
                    best = cand
        if best:
            m = pmap.get(c["norm"])
            out.append({
                "code": c["code"], "norm": c["norm"],
                "map_id": m["id"] if m else None,
                "category": best["category"], "bank": best["bank"],
                "hits": best["hits"], "active_days": len(active),
                "score": round(best["score"], 2),
                "pos_total": round(c["amount"], 2), "ext_total": best["ext_total"],
                "sample": best["sample"],
            })
    out.sort(key=lambda x: -x["pos_total"])
    return out


def daily_board(conn: sqlite3.Connection, day: str) -> dict:
    """Reconciliation matrix for a single day across all master branches.
    Returns {day, categories, branches:[{branch_id, branch_name, cells, totals}],
    totals, unmapped}. cells[cat] = {pos, ext, variance, status}."""
    pmap = {r["payment_code_norm"]: r for r in conn.execute(
        "SELECT payment_code_norm, category, bank, reconcile FROM recon_payment_map")}

    branches = {r["branch_id"]: r["name"] for r in conn.execute(
        "SELECT branch_id, name FROM branches "
        "WHERE COALESCE(branch_type,'master')='master' AND active=1")}

    # POS revenue per (branch, column). column = category, or category:bank for
    # split categories (CREDIT). col_meta carries label + raw category/bank.
    pos: dict = {}
    unmapped: dict = {}
    col_meta: dict = {}
    for r in conn.execute(
        "SELECT branch_id, payment_code, SUM(amount) amt "
        "FROM pos_payment_day WHERE day=? GROUP BY branch_id, payment_code", (day,)):
        if r["branch_id"] not in branches:
            continue
        norm = normalize(r["payment_code"])
        amt = float(r["amt"] or 0)
        m = pmap.get(norm)
        if m is None:
            unmapped[norm] = unmapped.get(norm, 0.0) + amt
            continue
        if not m["reconcile"]:
            continue
        ck = _col_key(m["category"], m["bank"])
        col_meta.setdefault(ck, {"label": _col_label(m["category"], m["bank"]),
                                 "category": m["category"], "bank": m["bank"]})
        pos[(r["branch_id"], ck)] = pos.get((r["branch_id"], ck), 0.0) + amt

    # External documents per (branch, category), summed on their own line date
    ext: dict = {}
    def add(bid, cat, bank, amount, net):
        if bid not in branches:
            return
        ck = _col_key(cat, bank)
        col_meta.setdefault(ck, {"label": _col_label(cat, bank), "category": cat, "bank": bank})
        b = ext.setdefault((bid, ck), {"amount": 0.0, "net": 0.0})
        b["amount"] += amount
        b["net"]    += net

    # Cash: use the SAME per-branch lump matcher as the Daily view (channel-
    # agnostic, T+1 / multi-day grouping) so the board is consistent everywhere.
    # For each branch, the deposit matched to this POS day becomes the day's cash
    # ext; the bank lines it claimed (often labelled QR/SDCH) are excluded from
    # the QR/transfer columns below so they don't double-count as ext_only.
    month = day[:7]
    cash_consumed: set = set()
    for bid in branches:
        cm = cash_deposit_matching(conn, bid, month)
        cash_consumed |= cm["consumed_ids"]
        for g in cm["groups"]:
            for j, dd in enumerate(g["days"]):
                if dd["day"] == day:
                    is_last = j == len(g["days"]) - 1
                    share = dd["pos"] + (g["variance"] if is_last else 0.0)
                    add(bid, "CASH", None, share, share)

    # Non-cash bank credits land on their own date (skip cash-claimed lines).
    for r in conn.execute(
        "SELECT id, branch_id, credit, channel FROM recon_bank_lines "
        "WHERE credit>0 AND match_status NOT IN ('manual','excluded') "
        "AND txn_date=? AND channel NOT IN ('CASH_DEP')", (day,)):
        if r["id"] in cash_consumed:
            continue
        cat = _CHANNEL_CATEGORY.get(r["channel"])
        if cat:
            add(r["branch_id"], cat, None, float(r["credit"] or 0), float(r["credit"] or 0))

    for r in conn.execute("SELECT * FROM recon_cc_lines WHERE match_status!='manual' AND settlement_date=?", (day,)):
        fee, vat = _eff_fee_vat(r, "mdr_fee", "vat_amount")
        gross = float(r["gross_amount"] or 0)
        add(r["branch_id"], "CREDIT", r["acquirer"], gross,
            gross - float(r["refund_amount"] or 0) - fee - vat)

    for cat, table, vat_col in (("GRAB", "recon_grab_lines", "vat_amount"),
                                ("LINEMAN", "recon_lineman_lines", "vat_on_commission")):
        for r in conn.execute(
            f"SELECT * FROM {table} WHERE order_status='Completed' AND match_status!='manual' AND order_date=?", (day,)):
            fee, vat = _eff_fee_vat(r, "commission_amt", vat_col)
            gross = float(r["food_amount"] or 0)
            add(r["branch_id"], cat, None, gross, gross - fee - vat - float(r["promo_deduct"] or 0))

    # manually-cleared POS per (branch, category) for this day (bank collapsed)
    consumed: dict = {}
    try:
        for r in conn.execute(
            "SELECT branch_id, category, pos_days FROM recon_manual_match WHERE period_month=?",
            (day[:7],)):
            if r["branch_id"] not in branches:
                continue
            for x in json.loads(r["pos_days"] or "[]"):
                if x["day"] == day:
                    ck = _col_key(r["category"], r["bank"])
                    k = (r["branch_id"], ck)
                    consumed[k] = consumed.get(k, 0.0) + float(x.get("amount") or 0)
    except sqlite3.OperationalError:
        pass

    cats_present = {c for (_, c) in pos} | {c for (_, c) in ext}
    # Order: base category order, then by bank within a split category.
    def _ck_sort(ck):
        cat = col_meta.get(ck, {}).get("category", ck)
        base = _BOARD_CAT_ORDER.index(cat) if cat in _BOARD_CAT_ORDER else 99
        return (base, col_meta.get(ck, {}).get("bank") or "", ck)
    categories = sorted(cats_present, key=_ck_sort)

    rows = []
    tot_pos = tot_ext = tot_var = 0.0
    cat_tot = {c: {"pos": 0.0, "ext": 0.0} for c in categories}
    for bid, name in branches.items():
        cells = {}
        bp = be = 0.0
        issues = 0
        bvar = 0.0
        for c in categories:
            p = pos.get((bid, c), 0.0)
            cons = consumed.get((bid, c), 0.0)        # manually cleared this day
            p_eff = p - cons
            eb = ext.get((bid, c))
            e = eb["amount"] if eb else 0.0
            if p == 0 and e == 0:
                continue
            if cons > 0 and abs(p_eff) <= TOL_ABS_DAY and e <= TOL_ABS_DAY:
                status = "manual"
            elif p_eff > 0 and e > 0:
                status = "matched" if _within_tol_day(e, p_eff) else "variance"
            elif p_eff > 0:
                status = "no_source" if c == "COUPON" else "pos_only"
            elif e > 0:
                status = "ext_only"
            else:
                status = "manual" if cons > 0 else "pos_only"
            if status in ("variance", "ext_only"):
                issues += 1
            cell_var = 0.0 if status == "manual" else (e - p_eff)
            cells[c] = {"pos": round(p, 2), "ext": round(e, 2), "manual": round(cons, 2),
                        "variance": round(cell_var, 2), "status": status}
            bp += p; be += e; bvar += cell_var
            cat_tot[c]["pos"] += p; cat_tot[c]["ext"] += e
        if not cells:
            continue
        rows.append({"branch_id": bid, "branch_name": name, "cells": cells,
                     "pos_total": round(bp, 2), "ext_total": round(be, 2),
                     "variance": round(bvar, 2), "issues": issues})
        tot_pos += bp; tot_ext += be; tot_var += bvar

    rows.sort(key=lambda x: -x["pos_total"])
    return {
        "day": day,
        "categories": categories,
        "column_meta": {c: col_meta.get(c, {"label": c, "category": c, "bank": None})
                        for c in categories},
        "branches": rows,
        "totals": {
            "pos": round(tot_pos, 2),
            "ext": round(tot_ext, 2),
            "variance": round(tot_var, 2),
            "by_category": {c: {"pos": round(v["pos"], 2), "ext": round(v["ext"], 2)}
                            for c, v in cat_tot.items()},
            "branches_reporting": len(rows),
            "branches_with_issues": sum(1 for r in rows if r["issues"]),
        },
        "unmapped": [{"code": k, "amount": round(v, 2)} for k, v in sorted(unmapped.items())],
    }
