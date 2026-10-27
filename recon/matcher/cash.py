"""CASH — POS cash days ↔ the deposits that banked them (T+1, grouped), the deposit-slip 3-way, and last month's rollover.

Split out of recon/matcher.py on 2026-09-05 — same code, one lane per module;
recon.matcher re-exports every name."""
from __future__ import annotations
import sqlite3
from datetime import date, timedelta
from ._shared import (
    DEF_CASH_LAG,
    DEF_CASH_MERGE,
    TOL_ABS_DAY,
    _CHANNEL_CATEGORY,
    _REAL_BANKS,
    _cfg,
    _pd,
    _provider_bank,
    _within_tol_day,
    memoized,
    method_config,
    pos_category_totals_daily,
)


def prev_month_consumed_ids(conn, branch_id, period_month) -> set:
    """Bank-line ids in THIS month that the PREVIOUS month's cash matcher already
    claimed (T+1 rollover: cash sold on the 30th/31st is deposited on the 1st).
    Those credits are last month's revenue — without this, every month-start
    deposit shows up as a spurious ext_only/QR diff in the new month."""
    y, m = int(period_month[:4]), int(period_month[5:7])
    prev = f"{y - 1}-12" if m == 1 else f"{y}-{m - 1:02d}"
    try:
        consumed = cash_deposit_matching(conn, branch_id, prev)["consumed_ids"]
    except sqlite3.OperationalError:
        return set()
    if not consumed:
        return set()
    qs = ",".join("?" * len(consumed))
    return {r["id"] for r in conn.execute(
        f"SELECT id FROM recon_bank_lines WHERE id IN ({qs}) "
        f"AND strftime('%Y-%m', txn_date)=?", (*consumed, period_month))}


# ── CASH 3-way: POS cash ↔ typed deposit slip ↔ bank deposit ──────────────────
# The owner's table: staff deposit day-D cash the next day before 13:00; the slip
# amount may differ from POS by a few baht. Slips are typed by staff
# (recon_deposit_slip); a missing slip degrades to the 2-way (never blocks close).

def deposit_slips(conn, branch_id, period_month):
    """{sale_day: slip-row-dict} for a branch/month; {} when table absent."""
    try:
        return {r["sale_day"]: dict(r) for r in conn.execute(
            "SELECT * FROM recon_deposit_slip WHERE branch_id=? AND substr(sale_day,1,7)=?",
            (branch_id, period_month))}
    except sqlite3.OperationalError:
        return {}


def cash_three_way(conn, branch_id, period_month, cash=None):
    """Per sale-day 3-way rows for the CASH channel. Tolerance = the CASH lane's
    tol_abs_day (owner: ฿10 strict) — within tolerance counts matched but the
    variance stays visible. `cash` lets callers reuse an existing
    cash_deposit_matching result (it's expensive enough to not recompute)."""
    mc = _cfg(method_config(conn), "CASH")
    tol = mc.get("tol_abs_day")
    tol_eff = float(tol) if tol is not None else TOL_ABS_DAY
    cash = cash or cash_deposit_matching(conn, branch_id, period_month)
    slips = deposit_slips(conn, branch_id, period_month)
    pos_days, available = _cash_pos_days(conn, branch_id, period_month)

    day_info: dict = {}
    for g in cash["groups"]:
        for j, dd in enumerate(g["days"]):
            is_last = j == len(g["days"]) - 1
            day_info[dd["day"]] = {
                "bank_share": dd["pos"] + (g["variance"] if is_last else 0.0),
                "deposit_date": g["deposit_date"], "combined": g["combined"], "lag": g["lag"]}

    rows = []
    for day in sorted(set(d for d, a in pos_days.items() if a > 0) | set(slips)):
        pos = round(pos_days.get(day, 0.0), 2)
        s = slips.get(day)
        slip_amt = round(float(s["amount"]), 2) if s else None
        info = day_info.get(day)
        bank_amt = round(info["bank_share"], 2) if info else None

        if s is None:
            slip_leg = "no_slip"                      # informational — never blocks
        elif pos <= 0:
            slip_leg = "slip_only"                    # slip typed for a day with no POS cash
        else:
            slip_leg = "matched" if abs(slip_amt - pos) <= tol_eff else "variance"
        if info is None:
            bank_leg = "pending" if (pos > 0 or slip_amt) else "none"
        else:
            ref = slip_amt if slip_amt is not None else pos
            bank_leg = "matched" if abs(bank_amt - ref) <= tol_eff else "variance"

        rows.append({
            "day": day, "pos_cash": pos,
            "slip_id": s["id"] if s else None,
            "slip_amount": slip_amt,
            "slip_day": (s["deposit_day"] if s else None),
            "attachment_id": (s["attachment_id"] if s else None),
            "slip_note": (s["note"] if s else None),
            "bank_deposit": bank_amt,
            "deposit_date": info["deposit_date"] if info else None,
            "deposit_combined": info["combined"] if info else False,
            "slip_leg": slip_leg, "bank_leg": bank_leg,
            "slip_variance": round(slip_amt - pos, 2) if (s and pos > 0) else None,
            "bank_variance": round(bank_amt - pos, 2) if info else None,
        })
    return {
        "rows": rows,
        "slip_count": sum(1 for r in rows if r["slip_amount"] is not None),
        "pos_total":  round(sum(r["pos_cash"] for r in rows), 2),
        "slip_total": round(sum(r["slip_amount"] or 0 for r in rows), 2),
        "bank_total": round(sum(r["bank_deposit"] or 0 for r in rows), 2),
        "pos_available": available,
    }


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
    lanes = {(cat, bank) for (_day, cat, bank), amt in pos.items()
             if cat in ("QR", "TRANSFER", "CREDIT") and amt > 0}
    if ("TRANSFER", "ANY") in lanes:
        # a multi-bank transfer code claims BOTH banks' transfer lines — the cash
        # matcher must not mistake them for lump cash deposits
        lanes |= {("TRANSFER", b) for b in _REAL_BANKS}
    return lanes


def _cash_dep_credits(conn, branch_id, period_month, max_lag, exclude_ids=None):
    """Bank credits that could be a cash lump deposit for this month.

    Banks post the next-day cash deposit under inconsistent codes (CASH_DEP,
    but also SDCH / รับโอนเงิน / plain transfer depending on the account), so we
    can't match on the channel label alone. Candidates therefore are:
      • every line literally parsed as CASH_DEP, plus
      • QR/TRANSFER credits in a lane NO mapped POS tender claims (an
        unexplained credit — cash is the only tender with no electronic trail).
    Card settlements, fees and payout legs are never cash. Window = the month
    plus the first max_lag days of the next month (day-31 cash banked on the 1st).
    `exclude_ids` drops credits last month's matchers already claimed.
    """
    exclude_ids = exclude_ids or set()
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
        if r["id"] in exclude_ids:
            continue
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


def _prev_month_claims(conn, branch_id, period_month) -> set:
    """Bank-line ids dated in THIS month that last month's cash, payout or card
    matcher already claimed (a 1-Jul deposit that banked 30-Jun cash, a 1-Jul
    transfer that paid June's Grab, a 1-Jul KBank รับโอนเงิน that settled June's
    30th card batch). Last month's matchers run non-recursively: cash with
    _dedup_prev=False, card with _dedup=False (given last month's own cash claims
    instead, so it picks what it really picked) — neither looks back again."""
    from .payout import _prev_payout_consumed_ids     # payout → buckets → cash: lazy
    from .credit import credit_three_way              # credit → cash: lazy
    y, m = int(period_month[:4]), int(period_month[5:7])
    prev = f"{y - 1}-12" if m == 1 else f"{y}-{m - 1:02d}"
    prev_cash = set(cash_deposit_matching(conn, branch_id, prev, _dedup_prev=False)["consumed_ids"])
    ids = set(prev_cash)
    try:
        for s in _prev_payout_consumed_ids(conn, branch_id, period_month).values():
            ids |= set(s)
    except sqlite3.OperationalError:
        pass
    try:
        ids |= set(credit_three_way(conn, branch_id, prev, exclude_ids=frozenset(prev_cash),
                                    _dedup=False)["bank_line_ids"])
    except sqlite3.OperationalError:
        pass
    return ids


@memoized
def cash_deposit_matching(conn, branch_id, period_month, max_group=None, max_lag=None,
                          _dedup_prev=True):
    """Group POS-cash days to the CASH_DEP bank credits that banked them.
    max_group/max_lag default to the CASH lane's recon_method_config
    (merge_days/lag_days), falling back to the engine constants. Credits last
    month's matchers already claimed are skipped (TC-35: a 1-Jul deposit that
    banked 30-Jun cash is June's, not July's); `_dedup_prev=False` skips that
    lookup (used for the previous-month call itself, so it never recurses).

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
    mc = _cfg(method_config(conn), "CASH")
    if max_group is None:
        max_group = int(mc.get("merge_days") or DEF_CASH_MERGE)
    if max_lag is None:
        max_lag = int(mc.get("lag_days") or DEF_CASH_LAG)
    # NOTE: the group-fit search keeps the permissive default tolerance so a
    # slightly-off lump deposit is still FOUND and traced; the owner's strict
    # "few baht" tolerance (tol_abs_day) is applied at the day-status level.
    cash_days, available = _cash_pos_days(conn, branch_id, period_month)
    prev_claimed = _prev_month_claims(conn, branch_id, period_month) if _dedup_prev else set()
    credits = _cash_dep_credits(conn, branch_id, period_month, max_lag, exclude_ids=prev_claimed)
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
