"""Line-level drill-downs: the POS bills and statement/report lines behind a lane or a day.

Split out of recon/matcher.py on 2026-09-05 — same code, one lane per module;
recon.matcher re-exports every name."""
from __future__ import annotations
import sqlite3
from datetime import date, timedelta
from ..seed_payment_map import normalize
from ._shared import TOL_ABS_DAY, _cc_acquirer, _point_mids, _provider_bank
from .payout import _PAYOUT_CHANNEL


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
            if bank != "ANY" and rb != bank:        # ANY = multi-bank lane → both statements
                continue
            out.append({"id": r["id"], "match_status": r["ms"],
                        "date": r["d"], "ref": r["ref"] or "", "amount": round(float(r["amt"] or 0), 2),
                        "detail": r["provider"] or ""})
        return out

    if category == "CREDIT":
        df, p = _df("l.settlement_date")
        pmids = _point_mids(conn, branch_id)          # point MIDs → KTCPOINT, not this card lane
        for r in conn.execute(f"""
            SELECT l.settlement_date d, l.batch_no ref, l.gross_amount amt, l.acquirer,
                   l.merchant_id, u.provider
            FROM recon_cc_lines l JOIN recon_uploads u ON u.upload_id=l.upload_id
            WHERE l.branch_id=?{df} ORDER BY l.settlement_date
        """, [branch_id] + p):
            acq = _cc_acquirer(r, pmids)
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


def pos_lane_lines(conn, branch_id, period_month, category, bank=None,
                   day=None, code=None, limit=1500):
    """Receipt-level POS tender behind a channel — the bill-by-bill POS side of a
    lane drill. `sale_payment` is the per-receipt source (receipt_no starts with
    YYYY-MM-DD); summing it ties out exactly to pos_payment_day / _month, so this
    drill always reconciles to the channel/day total the staff already sees.

    Filters: `day`='YYYY-MM-DD' (else whole month), `bank` (omit/ANY = every bank
    of the category), `code` = one normalised payment code. Returns receipts plus
    a per-code rollup; `truncated` flags when more than `limit` receipts existed."""
    try:
        pmap = {r["payment_code_norm"]: r for r in conn.execute(
            "SELECT payment_code_norm, category, bank, reconcile FROM recon_payment_map")}
    except sqlite3.OperationalError:
        return {"lines": [], "by_code": [], "by_day": [], "total": 0.0, "count": 0, "truncated": False}
    if day:
        where, params = "branch_id=? AND substr(receipt_no,1,10)=?", [branch_id, day]
    else:
        where, params = "branch_id=? AND substr(receipt_no,1,7)=?", [branch_id, period_month]
    try:
        rows = conn.execute(
            f"SELECT receipt_no, payment_code, amount FROM sale_payment "
            f"WHERE {where} ORDER BY receipt_no", params).fetchall()
    except sqlite3.OperationalError:        # sale_payment is sync-built; absent pre-sync
        return {"lines": [], "by_code": [], "by_day": [], "total": 0.0, "count": 0, "truncated": False}

    lines, by_code, by_day = [], {}, {}
    for r in rows:
        cd = normalize(r["payment_code"])
        m = pmap.get(cd)
        if not m or not m["reconcile"] or m["category"] != category:
            continue
        if bank and bank != "ANY" and m["bank"] != bank:
            continue
        if code and cd != code:
            continue
        amt = round(float(r["amount"] or 0), 2)
        d_ = r["receipt_no"][:10]
        agg = by_code.setdefault(cd, {"code": cd, "bank": m["bank"], "amount": 0.0, "count": 0})
        agg["amount"] += amt
        agg["count"] += 1
        by_day[d_] = by_day.get(d_, 0.0) + amt
        lines.append({"receipt_no": r["receipt_no"], "day": d_,
                      "code": cd, "bank": m["bank"], "amount": amt})
    by = sorted(by_code.values(), key=lambda x: -x["amount"])
    for b in by:
        b["amount"] = round(b["amount"], 2)
    total = round(sum(l["amount"] for l in lines), 2)
    # by_day is computed over EVERY matching receipt (before the line cap) so the
    # per-day rollup stays exact even when raw receipts are truncated.
    return {"lines": lines[:limit], "by_code": by,
            "by_day": [{"day": d, "amount": round(v, 2)} for d, v in sorted(by_day.items())],
            "total": total, "count": len(lines), "truncated": len(lines) > limit}


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
            if bank != "ANY" and rb != bank:        # ANY = multi-bank lane → both statements
                continue
            add("recon_bank_lines", r["id"], r["d"], r["ref"], r["provider"], r["amt"], r["ms"])
    elif category == "CREDIT":
        pmids = _point_mids(conn, branch_id)          # point MIDs → KTCPOINT, not this card lane
        for r in conn.execute("""
            SELECT l.id, l.settlement_date d, l.batch_no ref, l.gross_amount amt,
                   l.acquirer, l.merchant_id, l.match_status ms, u.provider
            FROM recon_cc_lines l JOIN recon_uploads u ON u.upload_id=l.upload_id
            WHERE l.branch_id=? AND strftime('%Y-%m',l.settlement_date)=?
            ORDER BY l.settlement_date""", (branch_id, period_month)):
            acq = _cc_acquirer(r, pmids)
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
