"""The daily board: day × lane status, and the day-level reconciliation the v2 UI renders.

Split out of recon/matcher.py on 2026-09-05 — same code, one lane per module;
recon.matcher re-exports every name."""
from __future__ import annotations
import json
import sqlite3
from datetime import date, timedelta
from ..seed_payment_map import normalize
from ._shared import (
    DEF_CASH_LAG,
    TOL_ABS_DAY,
    _CATEGORY_SOURCE,
    _CHANNEL_CATEGORY,
    _REAL_BANKS,
    _cc_acquirer,
    _cfg,
    _col_key,
    _col_label,
    _eff_fee_vat,
    _lane_cov_key,
    _manual_consumed,
    _point_mids,
    _within_tol_day,
    memo_scoped,
    method_config,
    pos_category_totals_daily,
)
from .cash import cash_deposit_matching, cash_three_way, prev_month_consumed_ids
from .buckets import (
    _apply_transfer_any,
    _external_buckets_daily,
    _settlement_claimed_ids,
    channel_move_day_deltas,
)
from .coverage import _coverage


@memo_scoped
def daily_reconciliation(conn, branch_id, period_month):
    """Per-day, per-lane reconciliation with pending tracking for late documents."""
    pos, unmapped, pos_available = pos_category_totals_daily(conn, branch_id, period_month)
    cash = cash_deposit_matching(conn, branch_id, period_month)
    # Lines the cash matcher claimed (may be labelled QR/TRANSFER, e.g. KTB SDCH)
    # are excluded from the electronic buckets so they aren't double-counted —
    # likewise month-start deposits the PREVIOUS month's matcher already claimed,
    # and card settlements / provider payouts posted as plain รับโอนเงิน.
    ext = _external_buckets_daily(
        conn, branch_id, period_month, include_cash=False,
        exclude_ids=(set(cash["consumed_ids"]) | prev_month_consumed_ids(conn, branch_id, period_month)
                     | _settlement_claimed_ids(conn, branch_id, period_month)))
    cov = _coverage(conn, branch_id, period_month)
    consumed = _manual_consumed(conn, branch_id, period_month)   # manual tick-to-match
    cfgs = method_config(conn)                                   # per-lane tolerance overrides

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

    # Manual cross-channel moves (booked-in-wrong-channel): a move saved with its sale
    # day shifts THAT day on both lanes. A legacy move (no day) has no per-day grain,
    # so its net delta lands on the lane's last populated POS day (else the month's
    # last active day). Either way the daily rollup stays tied to the monthly figure
    # (no false drift-escalation).
    mv = channel_move_day_deltas(conn, branch_id, period_month)
    if mv:
        eom = max((dy for (dy, _c, _b) in pos), default=f"{period_month}-28")
        legacy_tgt = {}
        for (cat, bank) in {(c, b) for (dd, c, b) in mv if dd is None}:
            lane_days = sorted(dy for (dy, c, b) in pos
                               if c == cat and b == bank and abs(pos[(dy, c, b)]) > 0.005)
            legacy_tgt[(cat, bank)] = lane_days[-1] if lane_days else eom
        for (mday, cat, bank), d in mv.items():
            if abs(d) < 0.005:
                continue
            tgt = mday or legacy_tgt[(cat, bank)]
            pos[(tgt, cat, bank)] = pos.get((tgt, cat, bank), 0.0) + d

    # Multi-bank TRANSFER codes (bank='ANY'): per-bank lanes consume their own
    # statement lines first, the leftover feeds the day's ANY lane. After the moves
    # above, so a transfer moved into a per-bank lane keeps its line there.
    for d in {dy for (dy, c, b) in pos if c == "TRANSFER" and b == "ANY"}:
        _apply_transfer_any(pos, ext, (d, "TRANSFER", "ANY"))

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

        lane_tol = _cfg(cfgs, cat, bank).get("tol_abs_day")
        if cons > 0 and abs(pos_eff) <= TOL_ABS_DAY and ext_amt <= TOL_ABS_DAY:
            status = "manual"                      # fully reconciled by hand
        elif eb and pos_eff != 0:                  # both sides present (pos may be a refund)
            status = "matched" if _within_tol_day(ext_amt, pos_eff, lane_tol) else "variance"
        elif eb:                                   # pos cleared/0, ext present
            status = "ext_only"
        elif cat == "COUPON":
            status = "no_source"
        elif cat in ("QR", "TRANSFER") and bank not in (_REAL_BANKS | {"ANY"}):
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
            # 3-way rows (POS ↔ typed slip ↔ bank deposit) for the v2 CASH channel
            "three_way":         cash_three_way(conn, branch_id, period_month, cash=cash)["rows"],
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


@memo_scoped
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
    # Deposits dated in the first lag days that LAST month's cash matcher claimed
    # (30th/31st cash banked on the 1st) are last month's — not this day's QR/transfer.
    cash_lag = int(_cfg(method_config(conn), "CASH").get("lag_days") or DEF_CASH_LAG)
    month_start = int(day[8:10]) <= cash_lag
    for bid in branches:
        cm = cash_deposit_matching(conn, bid, month)
        cash_consumed |= cm["consumed_ids"]
        if month_start:
            cash_consumed |= prev_month_consumed_ids(conn, bid, month)
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

    pmids: dict = {}
    for r in conn.execute("SELECT * FROM recon_cc_lines WHERE match_status!='manual' AND settlement_date=?", (day,)):
        if r["branch_id"] not in pmids:
            pmids[r["branch_id"]] = _point_mids(conn, r["branch_id"])
        acq = _cc_acquirer(r, pmids[r["branch_id"]]) or ""
        if acq.endswith("POINT"):      # loyalty points belong to COUPON, not a card column
            continue
        fee, vat = _eff_fee_vat(r, "mdr_fee", "vat_amount")
        gross = float(r["gross_amount"] or 0)
        add(r["branch_id"], "CREDIT", acq, gross,
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
            "SELECT branch_id, category, bank, pos_days FROM recon_manual_match WHERE period_month=?",
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
