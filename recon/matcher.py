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
from datetime import date

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
        key = (m["category"], m["bank"])
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

    # Bank statement: QR / TRANSFER / CASH_DEP credits
    for r in conn.execute("""
        SELECT l.id, l.credit, l.channel, u.provider
        FROM recon_bank_lines l JOIN recon_uploads u ON u.upload_id = l.upload_id
        WHERE l.branch_id=? AND l.credit>0 AND strftime('%Y-%m', l.txn_date)=?
    """, (branch_id, period_month)):
        cat = _CHANNEL_CATEGORY.get(r["channel"])
        if not cat:
            continue
        bank = "NONE" if cat == "CASH" else _provider_bank(r["provider"])
        amt = float(r["credit"] or 0)
        add((cat, bank), "recon_bank_lines", r["id"], amt, amt)

    # Credit-card settlement: gross vs net (after effective fee/VAT)
    for r in conn.execute("""
        SELECT l.*, u.provider FROM recon_cc_lines l JOIN recon_uploads u ON u.upload_id = l.upload_id
        WHERE l.branch_id=? AND strftime('%Y-%m', l.settlement_date)=?
    """, (branch_id, period_month)):
        acq = r["acquirer"] or _provider_acquirer(r["provider"])
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
    """Compare POS buckets to external buckets. Returns (rows, unmapped)."""
    pos, unmapped = pos_category_totals(conn, branch_id, period_month)
    ext = external_buckets(conn, branch_id, period_month)

    rows = []
    for key in sorted(set(pos) | set(ext)):
        category, bank = key
        pos_amt = pos.get(key, 0.0)
        eb = ext.get(key)
        ext_amt = eb["amount"] if eb else 0.0
        ext_net = eb["net"] if eb else 0.0

        if pos_amt > 0 and ext_amt > 0:
            status = "matched" if _within_tol(ext_amt, pos_amt) else "mismatch"
        elif pos_amt > 0:
            status = "no_source" if category == "COUPON" else "pos_only"
        else:
            status = "ext_only"

        rows.append({
            "category":  category,
            "bank":      bank,
            "pos_amount": round(pos_amt, 2),
            "ext_amount": round(ext_amt, 2),
            "ext_net":    round(ext_net, 2),
            "variance":   round(ext_amt - pos_amt, 2),
            "status":     status,
            "ext_lines":  eb["lines"] if eb else [],
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
    return rows


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
        df, p = _df("l.txn_date")
        for r in conn.execute(f"""
            SELECT l.txn_date d, l.description ref, l.credit amt, u.provider
            FROM recon_bank_lines l JOIN recon_uploads u ON u.upload_id=l.upload_id
            WHERE l.branch_id=? AND l.credit>0 AND l.channel=?{df} ORDER BY l.txn_date
        """, [branch_id, chan] + p):
            rb = "NONE" if category == "CASH" else _provider_bank(r["provider"])
            if rb != bank:
                continue
            out.append({"date": r["d"], "ref": r["ref"] or "", "amount": round(float(r["amt"] or 0), 2),
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
        conn.execute(f"""
            UPDATE {table} SET match_status='unmatched', match_id=NULL
            WHERE branch_id=? AND strftime('%Y-%m', {date_col})=? AND match_status!='manual'
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
        key = (r["day"], m["category"], m["bank"])
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


def _external_buckets_daily(conn, branch_id, period_month):
    """{(day, category, bank): {amount, net}} from external lines, keyed by line date.
    Manually-matched lines (match_status='manual') are excluded — they're accounted
    for in the manual layer, so they don't double-count or show a false variance on
    the lump-sum's own date."""
    buckets = {}
    def add(day, cat, bank, amount, net):
        b = buckets.setdefault((day, cat, bank), {"amount": 0.0, "net": 0.0})
        b["amount"] += amount; b["net"] += net

    for r in conn.execute("""
        SELECT l.txn_date d, l.credit, l.channel, u.provider
        FROM recon_bank_lines l JOIN recon_uploads u ON u.upload_id=l.upload_id
        WHERE l.branch_id=? AND l.credit>0 AND l.match_status!='manual'
          AND strftime('%Y-%m', l.txn_date)=?
    """, (branch_id, period_month)):
        cat = _CHANNEL_CATEGORY.get(r["channel"])
        if not cat:
            continue
        bank = "NONE" if cat == "CASH" else _provider_bank(r["provider"])
        add(r["d"], cat, bank, float(r["credit"] or 0), float(r["credit"] or 0))

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
        cov[("cash", None)] = cash_mx
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


def daily_reconciliation(conn, branch_id, period_month):
    """Per-day, per-lane reconciliation with pending tracking for late documents."""
    pos, unmapped, pos_available = pos_category_totals_daily(conn, branch_id, period_month)
    ext = _external_buckets_daily(conn, branch_id, period_month)
    cov = _coverage(conn, branch_id, period_month)
    consumed = _manual_consumed(conn, branch_id, period_month)   # manual tick-to-match

    by_day = {}
    for (day, cat, bank) in set(pos) | set(ext):
        pos_amt = pos.get((day, cat, bank), 0.0)
        cons    = consumed.get((day, cat, bank), 0.0)     # manually cleared
        pos_eff = pos_amt - cons                          # POS still needing a match
        eb = ext.get((day, cat, bank))
        ext_amt = eb["amount"] if eb else 0.0
        ext_net = eb["net"] if eb else 0.0

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

        by_day.setdefault(day, []).append({
            "category": cat, "bank": bank,
            "pos": round(pos_amt, 2), "ext": round(ext_amt, 2), "ext_net": round(ext_net, 2),
            "manual": round(cons, 2),
            "variance": 0.0 if status == "manual" else round(ext_amt - pos_eff, 2),
            "status": status,
        })

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

    for r in conn.execute(
        "SELECT branch_id, credit, channel FROM recon_bank_lines "
        "WHERE credit>0 AND match_status!='manual' AND txn_date=?", (day,)):
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
