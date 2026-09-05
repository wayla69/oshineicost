"""Suggestions: wrong-channel reclassifications, internal transfers to exclude, unmapped bank lines.

Split out of recon/matcher.py on 2026-09-05 — same code, one lane per module;
recon.matcher re-exports every name."""
from __future__ import annotations
import sqlite3
from datetime import timedelta
from ..seed_payment_map import normalize
from ._shared import (
    TOL_ABS_DAY,
    _REAL_BANKS,
    _d,
    _manual_consumed,
    _pd,
    _provider_acquirer,
    _provider_bank,
    _within_tol_day,
    pos_category_totals_daily,
)
from .cash import cash_deposit_matching, prev_month_consumed_ids
from .buckets import _external_buckets_daily
from .lines import _pick_ext_lines, pos_codes_scope


def suggest_reclassifications(conn, branch_id, period_month, max_day_gap=1):
    """Detect likely wrong-channel bookings (same bank, different category, equal
    amount, within ±max_day_gap days). Returns a list of suggestions, each a
    ready-to-POST /api/manual payload plus display fields."""
    pos, _unmapped, available = pos_category_totals_daily(conn, branch_id, period_month)
    if not available:
        return []
    consumed = _manual_consumed(conn, branch_id, period_month)
    # Exclude the bank lines the cash matcher already claimed as cash deposits, exactly
    # like daily_reconciliation does. KTB posts next-day cash deposits under 'SDCH',
    # which the parser labels QR; without this they resurface here as a phantom QR
    # surplus and get mis-suggested as "cash booked as QR" (the inverse of the truth —
    # the cash is real, just deposited T+1). See [[sdch-statement-codes]].
    cash = cash_deposit_matching(conn, branch_id, period_month)
    ext = _external_buckets_daily(
        conn, branch_id, period_month,
        exclude_ids=set(cash["consumed_ids"]) | prev_month_consumed_ids(conn, branch_id, period_month))

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
        # Best opposite lane: different category, closest day, equal amount.
        # Normally the bank must match, BUT the single most common misclassification
        # — a customer transfer rung in POS as Cash — pairs a CASH lane (bank='NONE')
        # with a TRANSFER statement line (bank='KTB'/'KBANK'). When either side is
        # CASH we match on amount+day only (C5); such cross-bank ties are 'med'.
        best = None
        for cb, bb, db, xb in ext_surplus:
            cash_involved = (ca == "CASH" or cb == "CASH")
            if (bb != ba and not cash_involved) or cb == ca or (cb, bb, db) in used_ext:
                continue
            gap = abs((_d(da) - _d(db)).days)
            if gap > max_day_gap or abs(xa - xb) > TOL_ABS_DAY:
                continue
            if best is None or gap < best[0]:
                best = (gap, cb, bb, db, xb)
        if not best:
            continue
        gap, cb, bb, db, xb = best
        same_bank = (bb == ba)
        lines = _pick_ext_lines(conn, branch_id, period_month, cb, bb, db, xb)
        if not lines:
            continue
        used_pos.add((ca, ba, da))
        used_ext.add((cb, bb, db))
        out.append({
            "bank":       ba,
            "amount":     round(xa, 2),
            "confidence": "high" if (gap == 0 and same_bank) else "med",
            "day_gap":    gap,
            # ready-to-POST /api/manual payload
            "category":     ca,           # record the match under the POS lane
            "period_month": period_month,
            "branch_id":    branch_id,
            "pos_days":   [{"day": da, "amount": round(xa, 2)}],
            "ext":        [{"table": t, "id": i, "amount": round(a, 2)} for (t, i, a) in lines],
            # display — raw lane string (classic.html / tests) + the components
            # reconcile_v2 maps to plain Thai so cards never show "CASH·KBANK"
            "pos_lane": f"{ca}·{ba}",
            "ext_lane": f"{cb}·{bb}",
            "pos_cat":  ca,    # POS-surplus lane (rung here by mistake)
            "ext_cat":  cb,    # bank-surplus lane (where the money really is)
            "pos_bank": ba,
            "ext_bank": bb,
            "pos_day":  da,
            "ext_day":  db,
            "note": (f"Suggested: POS {ca}·{ba} ↔ statement {cb}·{bb}, "
                     f"equal {xa:,.0f} on {da}" + ("" if gap == 0 else f" / {db}")),
        })
    return out


# Intercompany sweeps look like any other statement credit except they're huge AND
# round (e.g. ฿2,000,000 'รับโอนเงิน') and carry no sales descriptor — a customer's
# transfer-for-payment never is. We SUGGEST these (never auto-exclude — staff
# confirm, debug rule #12); the actual drop reuses PATCH /api/bank-lines/{id}/ignore
# (reversible + audited). Verified on live data: this isolates the ฿2M lumps at
# branches 4/26 and flags zero real QR/transfer sales.
_INTXFER_FLOOR = 100_000      # "ก้อนใหญ่" — real customer transfers sit far below this


_INTXFER_ROUND = 10_000       # sweeps are round to the ten-thousand; sales totals aren't


_INTXFER_SALES_KW = ("ขาย", "qr", "payment", "settlement", "sdch", "grab", "line")


# KTB statement codes for transfers between own accounts / inter-bank receipts.
# The chain has no POS tender code for transfers (customers pay QR or cash), so
# a credit carrying one of these is never a sale — whatever its size. Staff
# were finding the small ones by hand (B2, 2026-09). Still a suggestion only.
_INTXFER_CODES = ("IORSDT", "NBSDT")


def suggest_internal_transfers(conn, branch_id, period_month):
    """Unmatched bank credits that look like internal/own-account transfers rather
    than revenue: (reason "large_round") large AND round AND without a sales
    descriptor, or (reason "code") carrying a KTB own-account transfer code
    (_INTXFER_CODES) at any amount. Each row carries the line id so the UI can
    exclude it (PATCH /api/bank-lines/{id}/ignore, or POST /api/bank-lines/
    bulk-ignore for the whole list). Pure suggestion: nothing is excluded until
    staff act."""
    out = []
    try:
        rows = conn.execute(
            "SELECT id, txn_date, description, channel, credit FROM recon_bank_lines "
            "WHERE branch_id=? AND credit>0 AND match_status='unmatched' "
            "AND strftime('%Y-%m', txn_date)=? ORDER BY credit DESC",
            (branch_id, period_month)).fetchall()
    except sqlite3.OperationalError:
        return out
    for r in rows:
        amt = float(r["credit"] or 0)
        desc = r["description"] or ""
        up = desc.upper()
        if any(code in up for code in _INTXFER_CODES):
            reason = "code"
        elif (amt >= _INTXFER_FLOOR and round(amt) % _INTXFER_ROUND == 0
              and not any(k in desc.lower() for k in _INTXFER_SALES_KW)):
            reason = "large_round"
        else:
            continue
        out.append({"id": r["id"], "date": r["txn_date"], "description": desc,
                    "channel": r["channel"], "amount": round(amt, 2), "reason": reason})
    return out


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
