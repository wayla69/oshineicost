"""Which statement lines and uploads are explained by which matcher — coverage, upload stats, lane status.

Split out of recon/matcher.py on 2026-09-05 — same code, one lane per module;
recon.matcher re-exports every name."""
from __future__ import annotations
from datetime import date, timedelta
from ._shared import (
    _CHANNEL_CATEGORY,
    _REAL_BANKS,
    _cc_acquirer,
    _point_mids,
    _provider_acquirer,
    _provider_bank,
    memo_scoped,
)
from .cash import cash_deposit_matching, prev_month_consumed_ids
from .payout import (
    _PAYOUT_CHANNELS,
    _PAYOUT_CHAN_CAT,
    _prev_payout_consumed_ids,
    payout_matching,
)
from .credit import credit_three_way
from .category import category_reconciliation
from .buckets import _settlement_claimed_ids


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
    # Multi-bank (ANY) lanes are covered as far as the furthest statement we have.
    any_cov = [v for (src, b), v in cov.items() if src == "bank" and b in _REAL_BANKS and v]
    if any_cov:
        cov[("bank", "ANY")] = max(any_cov)
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


@memo_scoped
def upload_match_stats(conn, branch_id, period_month):
    """{upload_id: {total, matched, excluded}} computed live by lane status."""
    lane_status = _lane_status_map(conn, branch_id, period_month)
    cash = cash_deposit_matching(conn, branch_id, period_month)
    # a card settlement / provider payout posted as รับโอนเงิน is explained there
    explained = {g["line_id"] for g in cash["groups"]} | _settlement_claimed_ids(conn, branch_id, period_month)

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
        st = _line_status(r["ms"], cat, bank, lane_status, explained, r["id"])
        if st == "excluded":
            bump(r["upload_id"], "excluded"); continue
        bump(r["upload_id"], "total")
        if st == "matched":
            bump(r["upload_id"], "matched")

    pmids = _point_mids(conn, branch_id)
    for r in conn.execute("""
        SELECT l.upload_id, l.match_status ms, l.acquirer, l.merchant_id, u.provider
        FROM recon_cc_lines l JOIN recon_uploads u ON u.upload_id=l.upload_id
        WHERE l.branch_id=? AND strftime('%Y-%m', l.settlement_date)=?
    """, (branch_id, period_month)):
        bank = _cc_acquirer(r, pmids) or ""
        if bank.endswith("POINT"):        # point rows reconcile in the COUPON lane
            st = _line_status(r["ms"], "COUPON", "NONE", lane_status)
        else:
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


@memo_scoped
def bank_statement_coverage(conn, branch_id, period_month):
    """Per-line coverage of every bank-statement credit for a branch/month, plus the
    CREDIT 3-document chain. Returns {totals, groups, credit_three_way,
    unmatched_lines, lines}."""
    lane_status = _lane_status_map(conn, branch_id, period_month)
    cash = cash_deposit_matching(conn, branch_id, period_month)
    cash_ids = set(cash["consumed_ids"]) | {g["line_id"] for g in cash["groups"]}
    prev_ids = prev_month_consumed_ids(conn, branch_id, period_month)

    # CREDIT 3-document chain (per acquirer) — also yields the bank lines it consumed.
    c3 = credit_three_way(conn, branch_id, period_month, exclude_ids=cash_ids)
    credit_ids = set(c3["bank_line_ids"])

    # Grab / Line Man net payouts → the credits payout_matching claimed (the SAME
    # matcher as the channel trace: LINE MAN ฿500 threshold, month-end sweep, tagged-
    # first), never a re-match under other rules. Cash/credit claims win (elif order).
    pm = payout_matching(conn, branch_id, period_month)
    payout_ids = {cat: {p["credit_id"] for p in pm.get(cat, {}).get("payouts", [])
                        if p["status"] == "matched" and p.get("credit_id")}
                  for cat in ("GRAB", "LINEMAN")}
    # payouts / card settlements LAST month's matchers claimed that landed in this
    # month (T+1, LINE MAN sweep) — explained there, informational here
    prev_payout = {i: cat for cat, ids in _prev_payout_consumed_ids(conn, branch_id, period_month).items()
                   for i in ids}
    y, m = int(period_month[:4]), int(period_month[5:7])
    prev = f"{y - 1}-12" if m == 1 else f"{y}-{m - 1:02d}"
    for i in credit_three_way(conn, branch_id, prev, _prev_credit=False)["bank_line_ids"]:
        prev_payout.setdefault(i, "CREDIT")

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
        elif r["id"] in prev_ids:    # explained by LAST month's cash (T+1 rollover)
            status, group, lane_bank = "informational", "CASH", "NONE"
        elif r["id"] in cash_ids:
            status, group, lane_bank = "matched", "CASH", "NONE"
        elif r["id"] in credit_ids:
            status, group, lane_bank = "matched", "CREDIT", bankp
        elif r["id"] in payout_ids["GRAB"]:
            status, group, lane_bank = "matched", "GRAB", "NONE"
        elif r["id"] in payout_ids["LINEMAN"]:
            status, group, lane_bank = "matched", "LINEMAN", "NONE"
        elif r["id"] in prev_payout:  # last month's payout / card settlement
            status, group, lane_bank = "informational", prev_payout[r["id"]], "NONE"
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
