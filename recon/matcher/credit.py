"""CREDIT — per-acquirer 3-document chain: POS gross ↔ settlement gross, settlement net ↔ bank credit.

Split out of recon/matcher.py on 2026-09-05 — same code, one lane per module;
recon.matcher re-exports every name."""
from __future__ import annotations
import sqlite3
from datetime import date, timedelta
from ._shared import (
    DEF_PAYOUT_LAG,
    DEF_PAYOUT_MERGE,
    TOL_ABS,
    TOL_ABS_DAY,
    _cc_acquirer,
    _cfg,
    _eff_fee_vat,
    _pd,
    _point_mids,
    _provider_acquirer,
    _provider_bank,
    _within_tol,
    _within_tol_day,
    memoized,
    method_config,
    pos_category_totals,
)
from .cash import cash_deposit_matching, prev_month_consumed_ids
from .payout import _prev_payout_consumed_ids


# ── Credit card: full 3-document match (POS ↔ settlement ↔ bank), per acquirer ──
# The owner's accounting chain for cards is three documents:
#   1) POS gross  ↔  credit-card settlement summary gross (before fee & VAT)
#   2) settlement net (gross − refund − MDR fee − VAT)  ↔  bank statement credit
# The bank only ever credits the NET, so the gap between gross and the bank line is
# exactly the fee+VAT — already stored on each settlement line and pulled in
# automatically here (via _eff_fee_vat), so it explains the gap instead of looking
# like a discrepancy. We split per acquirer (CREDIT·KTB / CREDIT·KBANK) because each
# acquirer settles into its own bank account.

# Acquirer → the bank account its settlement nets into. KTB/KBANK acquire to their
# own accounts; KTC (บัตรกรุงไทย / Krungthai Card) has no bank of its own — its net
# is remitted into the branch's KTB account (the RTxInv settles to acct 'xxx KTB
# xxx'), so its stage-1 net↔bank leg matches against the KTB statement's CC_SETTLE.
_ACQ_BANK = {"KTB": "KTB", "KBANK": "KBANK", "KTC": "KTB"}


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


def _credit_bank_candidates(conn, branch_id, period_month, bank, exclude_ids=None, max_lag=0):
    """Bank credit lines that could be a card settlement landing on `bank` this
    month. Acquirers remit the net under inconsistent codes — KTB posts CC_SETTLE
    (SDCK / 'รับเงินจากการขาย'), KBank often a plain รับโอนเงิน (TRANSFER) — so we
    consider both channels and match by amount + date-lag (same idea as
    payout_matching). Window = the month plus the first `max_lag` days of the next
    (the 30th's batch settles on the 1st), mirroring _payout_candidate_credits."""
    exclude_ids = exclude_ids or set()
    y, m = int(period_month[:4]), int(period_month[5:7])
    nm_y, nm_m = (y + 1, 1) if m == 12 else (y, m + 1)
    next_cutoff = (date(nm_y, nm_m, 1) + timedelta(days=max(0, max_lag))).isoformat()
    try:
        rows = conn.execute("""
            SELECT l.id, l.txn_date, l.credit, l.channel, u.provider
            FROM recon_bank_lines l JOIN recon_uploads u ON u.upload_id=l.upload_id
            WHERE l.branch_id=? AND l.credit>0
              AND l.match_status NOT IN ('manual','excluded')
              AND l.channel IN ('CC_SETTLE','TRANSFER')
              AND l.txn_date>=? AND l.txn_date<?
            ORDER BY l.txn_date, l.id
        """, (branch_id, date(y, m, 1).isoformat(), next_cutoff)).fetchall()
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


def _carried_bank_line_ids(conn, branch_id, period_month) -> set:
    """Bank credits dated IN this month that LAST month's matchers already
    claimed: the T+1 deposit of the 30th/31st (cash) and the payout sweeps that
    land in the first days of the month (Grab / LINE MAN / card). They are last
    month's money — nothing in this month may consume them again."""
    ids = set(prev_month_consumed_ids(conn, branch_id, period_month))
    for s in _prev_payout_consumed_ids(conn, branch_id, period_month).values():
        ids |= set(s)
    return ids


@memoized
def credit_three_way(conn, branch_id, period_month, exclude_ids=None, _dedup=True,
                     _prev_credit=True):
    """Per-acquirer 3-document credit reconciliation. Returns
    {rows: [...], bank_line_ids: set}. Each row:
      acquirer, bank, pos_gross, settle_gross, settle_refund, settle_fee (MDR+VAT,
      auto-pulled), settle_net, bank_credit, pos_leg, bank_leg, fee_variance,
      bank_groups (day-traces of the bank match).
    `exclude_ids` are bank-line ids the caller already knows are claimed. On top
    of them the matcher always drops this month's cash deposits and everything
    last month's cash / payout matchers carried into this month (their windows
    reach past the 1st; this one is strictly in-month, so until 2026-09 a 1-July
    credit that June had already consumed could be booked again as a July card
    settlement whenever a July net happened to equal it — the bank leg read
    'matched' with money that was June's). `_dedup=False` skips that lookup.
    The bank window reaches lag_days into next month (a 30th batch credited on
    the 1st), so last month's own card claims are excluded too; that previous-
    month call runs with _prev_credit=False and never recurses."""
    exclude_ids = set(exclude_ids or set())
    if _dedup:
        try:
            exclude_ids |= set(cash_deposit_matching(conn, branch_id, period_month)["consumed_ids"])
        except sqlite3.OperationalError:
            pass
        exclude_ids |= _carried_bank_line_ids(conn, branch_id, period_month)
        if _prev_credit:
            y, m = int(period_month[:4]), int(period_month[5:7])
            prev = f"{y - 1}-12" if m == 1 else f"{y}-{m - 1:02d}"
            exclude_ids |= credit_three_way(conn, branch_id, prev, _prev_credit=False)["bank_line_ids"]
    cfgs = method_config(conn)
    pos, _unmapped = pos_category_totals(conn, branch_id, period_month)
    pos_credit = {bank: amt for (cat, bank), amt in pos.items() if cat == "CREDIT"}

    pmids = _point_mids(conn, branch_id)
    agg: dict = {}        # acq -> {gross, refund, fee, net, point}
    net_day: dict = {}    # acq -> {day: net}
    for r in conn.execute("""
        SELECT l.*, u.provider FROM recon_cc_lines l JOIN recon_uploads u ON u.upload_id=l.upload_id
        WHERE l.branch_id=? AND strftime('%Y-%m', l.settlement_date)=?
    """, (branch_id, period_month)):
        acq = _cc_acquirer(r, pmids) or ""
        point = acq.endswith("POINT")
        if point:
            # BAY Point = own document + own KBANK transfer (payout_matching's
            # BAYPOINT stream). KTC Point settles INSIDE the KTC deposit (one KTB
            # credit), so its net stays in the bank leg — never in the POS gross leg.
            if acq != "KTCPOINT":
                continue
            acq = (r["acquirer"] or _provider_acquirer(r["provider"]) or "KTC").upper()
            if acq.endswith("POINT"):
                acq = acq[:-len("POINT")]
        fee, vat = _eff_fee_vat(r, "mdr_fee", "vat_amount")
        gross = float(r["gross_amount"] or 0)
        refund = float(r["refund_amount"] or 0)
        net = gross - refund - fee - vat
        a = agg.setdefault(acq, {"gross": 0.0, "refund": 0.0, "fee": 0.0, "net": 0.0, "point": 0.0})
        if point:
            a["point"] += net
        else:
            a["gross"] += gross
            a["refund"] += refund
            a["fee"] += fee + vat
        a["net"] += net
        nd = net_day.setdefault(acq, {})
        nd[r["settlement_date"]] = nd.get(r["settlement_date"], 0.0) + net

    rows = []
    bank_line_ids: set = set()
    for acq in sorted(set(agg) | set(pos_credit)):
        a = agg.get(acq, {"gross": 0.0, "refund": 0.0, "fee": 0.0, "net": 0.0, "point": 0.0})
        pos_gross = pos_credit.get(acq, 0.0)
        bank = _ACQ_BANK.get(acq)
        bank_credit, used, groups = 0.0, set(), []
        if bank:
            mc = _cfg(cfgs, "CREDIT", acq)
            lag = int(mc.get("lag_days") or DEF_PAYOUT_LAG)
            credits = _credit_bank_candidates(conn, branch_id, period_month, bank,
                                              exclude_ids | bank_line_ids, max_lag=lag)
            bank_credit, used, groups = _match_net_days_to_credits(
                net_day.get(acq, {}), credits,
                max_group=int(mc.get("merge_days") or DEF_PAYOUT_MERGE),
                max_lag=lag)
            bank_line_ids |= used

        tol = _cfg(cfgs, "CREDIT", acq).get("tol_abs")
        # POS leg: POS gross ↔ settlement gross
        if pos_gross > 0 and a["gross"] > 0:
            pos_leg = "matched" if _within_tol(a["gross"], pos_gross, tol) else "mismatch"
        elif pos_gross > 0:
            pos_leg = "pos_only"          # POS booked a card tender, no settlement doc yet
        elif a["gross"] > 0:
            pos_leg = "ext_only"          # settlement with no POS card tender
        else:
            pos_leg = "none"              # only KTC Point rows (checked in COUPON)

        # Bank leg: settlement net ↔ bank credit (fee already auto-pulled into net)
        if a["net"] > TOL_ABS and bank_credit > 0:
            bank_leg = "matched" if _within_tol(bank_credit, a["net"], tol) else "mismatch"
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
            "settle_net":    round(a["net"], 2),       # what lands: card net + KTC Point
            "settle_point":  round(a["point"], 2),     # KTC Point net riding in the deposit
            "bank_credit":   round(bank_credit, 2),
            "pos_leg":       pos_leg,
            "bank_leg":      bank_leg,
            "pos_variance":  round(a["gross"] - pos_gross, 2),
            "bank_variance": round(bank_credit - a["net"], 2),
            "bank_groups":   groups,
        })
    return {"rows": rows, "bank_line_ids": bank_line_ids}


def month_claimed_bank_line_ids(conn, branch_id, period_month) -> set:
    """Every bank-line id this month's result rests on: the cash deposits its cash
    matcher grouped, the settlements its card bank leg matched and the payouts its
    provider matcher matched. The windows reach lag_days into the NEXT month, so
    some of these lines are dated next month — the lock guard uses this to refuse
    an edit that would change a signed-off month's figures."""
    from .payout import payout_matching            # payout → buckets → cash: lazy
    ids: set = set()
    try:
        ids |= set(cash_deposit_matching(conn, branch_id, period_month)["consumed_ids"])
    except sqlite3.OperationalError:
        pass
    try:
        ids |= set(credit_three_way(conn, branch_id, period_month)["bank_line_ids"])
    except sqlite3.OperationalError:
        pass
    try:
        for r in payout_matching(conn, branch_id, period_month).values():
            ids |= {p["credit_id"] for p in r.get("payouts", [])
                    if p.get("status") == "matched" and p.get("credit_id")}
    except sqlite3.OperationalError:
        pass
    return ids
