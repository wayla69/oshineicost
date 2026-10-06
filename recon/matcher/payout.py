"""GRAB / LINE MAN / card payouts — net-day groups ↔ bank credits, the ฿500 LINE MAN threshold, last month's carried sweep.

Split out of recon/matcher.py on 2026-09-05 — same code, one lane per module;
recon.matcher re-exports every name."""
from __future__ import annotations
import sqlite3
from datetime import date, timedelta
from ._shared import (
    DEF_LINEMAN_LAG,
    DEF_LINEMAN_MERGE,
    DEF_PAYOUT_LAG,
    DEF_PAYOUT_MERGE,
    LINEMAN_PAYOUT_THRESHOLD,
    TOL_ABS,
    TOL_ABS_DAY,
    _cfg,
    _eff_fee_vat,
    _pd,
    _provider_bank,
    _within_tol,
    method_config,
)
from .buckets import external_buckets
from .cash import cash_deposit_matching
from .points import _point_settlement_gross, point_pos_total


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


def _prev_payout_carried_amounts(conn, branch_id, period_month) -> dict:
    """{cat: baht} — amount of the PREVIOUS month's matched payouts whose bank credit
    is dated IN this month AND sits in a TAGGED payout channel (so it is what
    _bank_payout_totals counts). Subtracting it stops last month's payout — e.g. an
    under-B500 LINE MAN residual swept on the 1st — from being double-counted as this
    month's payout, which could otherwise reconcile a real shortfall as 'matched'.
    Reuses _prev_payout_consumed_ids (same carried credit ids) and keeps only the
    tagged-channel ones, since a carry that landed as a plain TRANSFER was never in
    _bank_payout_totals to begin with."""
    out: dict = {}
    try:
        ids_by_cat = _prev_payout_consumed_ids(conn, branch_id, period_month)
    except sqlite3.OperationalError:
        return out
    for cat, ids in ids_by_cat.items():
        if not ids:
            continue
        ph = ",".join("?" * len(ids))
        try:
            row = conn.execute(
                f"SELECT COALESCE(SUM(credit),0) FROM recon_bank_lines "
                f"WHERE id IN ({ph}) "
                f"AND channel IN ('GRAB_PAYOUT','LINEMAN_PAYOUT','CC_SETTLE')",
                tuple(ids)).fetchone()
        except sqlite3.OperationalError:
            continue
        amt = round(float(row[0] or 0), 2)
        if amt > 0:
            out[cat] = amt
    return out


def _payout_carried_out_amounts(conn, pm, period_month) -> dict:
    """{cat: baht} — the mirror of _prev_payout_carried_amounts: THIS month's
    matched payouts whose TAGGED credit lands NEXT month (the 30th's Grab paid on
    the 1st). _bank_payout_totals only sees in-month credits, so without adding
    these every T+1 provider reads a false shortfall each month."""
    out: dict = {}
    for cat in _PAYOUT_CHANNEL:
        ids = [p["credit_id"] for p in (pm.get(cat) or {}).get("payouts", [])
               if p.get("status") == "matched" and p.get("credit_id")
               and (p.get("payout_date") or "")[:7] > period_month]
        if not ids:
            continue
        ph = ",".join("?" * len(ids))
        try:
            row = conn.execute(
                f"SELECT COALESCE(SUM(credit),0) FROM recon_bank_lines WHERE id IN ({ph}) "
                f"AND channel IN ('GRAB_PAYOUT','LINEMAN_PAYOUT','CC_SETTLE')", tuple(ids)).fetchone()
        except sqlite3.OperationalError:
            continue
        amt = round(float(row[0] or 0), 2)
        if amt > 0:
            out[cat] = amt
    return out


def payout_reconciliation(conn, branch_id, period_month):
    """Tie provider NET (should-remit) to the bank payout credit. Returns rows
    [{category, report_net, bank_payout, variance, status}]. status:
      matched           — both present, within tolerance
      mismatch          — both present, beyond tolerance (a real discrepancy)
      awaiting_payout   — net>0 but no bank payout line yet (informational: the
                          provider settles a few days later — not yet a gap)
      ext_only          — a bank payout line with no provider report behind it."""
    # Compare provider NET to the bank payout TOTAL (so a short payment shows up as a
    # mismatch), but subtract any tagged payout credit that last month's matcher
    # already claimed and that merely landed on the 1st-few of this month (e.g. the
    # LINE MAN under-B500 sweep). Without this, that carried credit is double-counted
    # and can hide a real shortfall as "matched".
    # Symmetric: this month's payout that lands next month (claimed by
    # payout_matching) is ADDED, as last month's that landed here is subtracted.
    nets = _report_net_by_category(conn, branch_id, period_month)
    pays = _bank_payout_totals(conn, branch_id, period_month)
    carried = _prev_payout_carried_amounts(conn, branch_id, period_month)
    pm = payout_matching(conn, branch_id, period_month)
    carried_out = _payout_carried_out_amounts(conn, pm, period_month)
    rows = []
    for cat in sorted(set(nets) | set(pays) | set(carried_out)):
        net = round(nets.get(cat, 0.0), 2)
        pay = round(pays.get(cat, 0.0) - carried.get(cat, 0.0) + carried_out.get(cat, 0.0), 2)
        if pay < 0:
            pay = 0.0
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
    bp = pm.get("BAYPOINT")
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


def _payout_candidate_credits(conn, branch_id, period_month, channels, max_lag=0, exclude_ids=None):
    """Bank credits that could be a provider payout for this month. The window is
    the month PLUS the first `max_lag` days of the NEXT month, because a payout for
    the month's last (or — when under the ฿500 threshold — its whole) net lands on
    the 1st of next month (mirrors `_cash_dep_credits`). `exclude_ids` drops credits
    the previous month's matcher already claimed (the carried boundary transfer),
    so the same credit is never counted in two months."""
    exclude_ids = exclude_ids or set()
    y, m = int(period_month[:4]), int(period_month[5:7])
    month_start = date(y, m, 1).isoformat()
    nm_y, nm_m = (y + 1, 1) if m == 12 else (y, m + 1)
    next_cutoff = (date(nm_y, nm_m, 1) + timedelta(days=max(0, max_lag))).isoformat()
    ph = ",".join("?" * len(channels))
    try:
        rows = conn.execute(f"""
            SELECT l.id, l.txn_date, l.credit, l.channel, u.provider
            FROM recon_bank_lines l JOIN recon_uploads u ON u.upload_id=l.upload_id
            WHERE l.branch_id=? AND l.credit>0 AND l.channel IN ({ph})
              AND l.match_status!='excluded'
              AND l.txn_date>=? AND l.txn_date<?
            ORDER BY l.txn_date, l.id
        """, (branch_id, *channels, month_start, next_cutoff)).fetchall()
    except sqlite3.OperationalError:
        return []
    return [{"id": r["id"], "date": r["txn_date"], "amount": float(r["credit"] or 0),
             "channel": r["channel"], "bank": _provider_bank(r["provider"])}
            for r in rows if r["id"] not in exclude_ids]


def _prev_payout_consumed_ids(conn, branch_id, period_month) -> dict:
    """{cat: {credit_id,...}} — bank credits dated IN this month that the PREVIOUS
    month's payout matcher already claimed (a payout for last month's net that
    landed on the 1st–few of this month, e.g. an under-฿500 LINE MAN month paid on
    the 1st). Mirrors `prev_month_consumed_ids` for cash so the carried transfer
    isn't matched twice and never shows as a next-month surplus. Recursion-safe:
    the prev-month call runs with _dedup_prev=False (it only looks forward)."""
    y, m = int(period_month[:4]), int(period_month[5:7])
    prev = f"{y - 1}-12" if m == 1 else f"{y}-{m - 1:02d}"
    out: dict = {}
    try:
        pm = payout_matching(conn, branch_id, prev, _dedup_prev=False)
    except sqlite3.OperationalError:
        return out
    for cat, r in pm.items():
        ids = {p["credit_id"] for p in r["payouts"]
               if p.get("status") == "matched" and p.get("credit_id")
               and (p.get("payout_date") or "")[:7] == period_month}
        if ids:
            out[cat] = ids
    return out


def payout_matching(conn, branch_id, period_month, max_group=None, max_lag=None,
                    _dedup_prev=True):
    """Per-provider: group consecutive net-days to the bank credit that pays them.
    Returns {cat: {payouts:[...], net_total, payout_total, awaiting_total}}.
    Each payout row: {payout_date, amount, credit_id, days:[{day,net}], group_net,
    combined, lag, status('matched'|'awaiting')}. 'awaiting' = net with no matching
    credit yet: a late/month-boundary payout, a short payment to investigate, or —
    for LINE MAN — net still under the ฿500 transfer threshold and accumulating.
    max_group/max_lag default per provider from recon_method_config
    (merge_days/lag_days). LINE MAN settles on a balance threshold, so it defaults
    to a full-month merge window reaching into the first lag-days of next month (the
    under-฿500 month is remitted on the 1st), and counts every earning day (no
    daily noise floor) so sub-฿50 days still accumulate toward the ฿500 transfer."""
    nets = _payout_net_by_day(conn, branch_id, period_month)
    cfgs = method_config(conn)
    prev_consumed = _prev_payout_consumed_ids(conn, branch_id, period_month) if _dedup_prev else {}
    y0, m0 = int(period_month[:4]), int(period_month[5:7])
    nm_start = date(y0 + 1, 1, 1) if m0 == 12 else date(y0, m0 + 1, 1)
    # ONE used set for every stream, seeded with this month's cash deposits: all
    # streams accept plain TRANSFER credits, and one bank credit can only ever be
    # one payout (or a cash deposit) — never Grab AND LINE MAN at once.
    try:
        used: set = set(cash_deposit_matching(conn, branch_id, period_month,
                                              _dedup_prev=_dedup_prev)["consumed_ids"])
    except sqlite3.OperationalError:
        used = set()
    result: dict = {}
    for cat, channels in _PAYOUT_CANDIDATE_CHANNELS.items():
        mc = _cfg(cfgs, "COUPON", "BAY") if cat == "BAYPOINT" else _cfg(cfgs, cat)
        grp_def, lag_def = ((DEF_LINEMAN_MERGE, DEF_LINEMAN_LAG) if cat == "LINEMAN"
                            else (DEF_PAYOUT_MERGE, DEF_PAYOUT_LAG))
        grp_max = max_group if max_group is not None else int(mc.get("merge_days") or grp_def)
        lag_max = max_lag if max_lag is not None else int(mc.get("lag_days") or lag_def)
        # LINE MAN accumulates sub-฿50 days toward the ฿500 threshold, so every
        # earning day must take part in the grouping; daily providers keep the
        # noise floor that suppresses rounding-size rows.
        floor = 0.0 if cat == "LINEMAN" else TOL_ABS_DAY
        days = sorted((d, n) for d, n in nets.get(cat, {}).items() if n > floor)
        credits = _payout_candidate_credits(conn, branch_id, period_month, channels,
                                            max_lag=lag_max,
                                            exclude_ids=prev_consumed.get(cat))
        if cat == "BAYPOINT":
            # BAY reimburses points into KBANK only (owner's table) — don't let
            # the amount-match grab a coincidental KTB transfer.
            credits = [c for c in credits if c["bank"] == "KBANK"]
        # A provider-tagged credit (GRAB_PAYOUT …) is definitive; a plain รับโอนเงิน
        # may be anyone's money — so every group tries the tagged pool first.
        pools = ([c for c in credits if c["channel"] != "TRANSFER"],
                 [c for c in credits if c["channel"] == "TRANSFER"])
        payouts, di = [], 0
        while di < len(days):
            hit = None
            for pool in pools:
                s = 0.0
                for k in range(1, min(grp_max, len(days) - di) + 1):
                    s = round(s + days[di + k - 1][1], 2)
                    last_day = days[di + k - 1][0]
                    for c in pool:                      # earliest unused credit that fits
                        if c["id"] in used or abs(c["amount"] - s) > TOL_ABS:
                            continue
                        lag = (_pd(c["date"]) - _pd(last_day)).days
                        if lag < 0:
                            continue
                        cap = lag_max
                        if cat == "LINEMAN":
                            # LINE MAN only remits once the balance reaches ฿500, so a
                            # small in-month plain transfer is a customer's, not a
                            # payout — only the next-month sweep may be under ฿500.
                            if (c["channel"] == "TRANSFER" and _pd(c["date"]) < nm_start
                                    and c["amount"] < LINEMAN_PAYOUT_THRESHOLD - TOL_ABS):
                                continue
                            # Month-end sweep: an under-฿500 residual is remitted on the
                            # 1st of next month no matter how early the last sale fell, so
                            # also allow the lag measured from month-end (not just from the
                            # last earning day) within lag_max.
                            cap = max(lag_max, (nm_start - _pd(last_day)).days + lag_max)
                        if lag <= cap:
                            hit = (k, c); break
                    if hit:
                        break
                if hit:
                    break
            if hit:
                k, c = hit
                used.add(c["id"])
                grp = days[di:di + k]
                payouts.append({
                    "payout_date": c["date"], "amount": round(c["amount"], 2),
                    "credit_id": c["id"],
                    "days": [{"day": d, "net": round(n, 2)} for d, n in grp],
                    "group_net": round(sum(n for _, n in grp), 2),
                    "combined": k > 1, "lag": (_pd(c["date"]) - _pd(grp[-1][0])).days,
                    "status": "matched",
                })
                di += k
            else:
                d, n = days[di]
                payouts.append({"payout_date": None, "amount": 0.0, "credit_id": None,
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


# ── Bank-statement coverage (bank-first: is every credit line explained?) ─────
# Reconcile/Daily are POS-first. This view is BANK-first: walk every credit line on
# the statements and answer "did this money get explained?" — matched to a revenue
# lane (cash/QR/transfer), consumed as a cash deposit, tied to a provider net payout
# (incl. the CREDIT 3-document chain, where the fee+VAT is auto-pulled to bridge
# gross→net), ignored as an internal transfer, or still unmatched (→ staff matches it
# in POS). Non-revenue debits/fees are informational and stay out of the denominator.

_PAYOUT_CHANNELS = {"CC_SETTLE", "GRAB_PAYOUT", "LINEMAN_PAYOUT"}


_PAYOUT_CHAN_CAT = {"CC_SETTLE": "CREDIT", "GRAB_PAYOUT": "GRAB", "LINEMAN_PAYOUT": "LINEMAN"}
