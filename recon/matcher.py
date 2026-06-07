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
import sqlite3

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
        fee, vat = _eff_fee_vat(r, "mdr_fee", None)
        gross = float(r["gross_amount"] or 0)
        net = gross - float(r["refund_amount"] or 0) - fee - vat
        add(("CREDIT", acq), "recon_cc_lines", r["id"], gross, net)

    # Delivery reports: gross (food) vs net (after effective fee/VAT/promo); Completed only
    for cat, table, vat_col in (("GRAB", "recon_grab_lines", None),
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


def _external_buckets_daily(conn, branch_id, period_month):
    """{(day, category, bank): {amount, net}} from external lines, keyed by line date."""
    buckets = {}
    def add(day, cat, bank, amount, net):
        b = buckets.setdefault((day, cat, bank), {"amount": 0.0, "net": 0.0})
        b["amount"] += amount; b["net"] += net

    for r in conn.execute("""
        SELECT l.txn_date d, l.credit, l.channel, u.provider
        FROM recon_bank_lines l JOIN recon_uploads u ON u.upload_id=l.upload_id
        WHERE l.branch_id=? AND l.credit>0 AND strftime('%Y-%m', l.txn_date)=?
    """, (branch_id, period_month)):
        cat = _CHANNEL_CATEGORY.get(r["channel"])
        if not cat:
            continue
        bank = "NONE" if cat == "CASH" else _provider_bank(r["provider"])
        add(r["d"], cat, bank, float(r["credit"] or 0), float(r["credit"] or 0))

    for r in conn.execute("""
        SELECT l.*, u.provider FROM recon_cc_lines l JOIN recon_uploads u ON u.upload_id=l.upload_id
        WHERE l.branch_id=? AND strftime('%Y-%m', l.settlement_date)=?
    """, (branch_id, period_month)):
        acq = r["acquirer"] or _provider_acquirer(r["provider"])
        fee, vat = _eff_fee_vat(r, "mdr_fee", None)
        gross = float(r["gross_amount"] or 0)
        add(r["settlement_date"], "CREDIT", acq, gross,
            gross - float(r["refund_amount"] or 0) - fee - vat)

    for cat, table, vat_col in (("GRAB", "recon_grab_lines", None),
                                ("LINEMAN", "recon_lineman_lines", "vat_on_commission")):
        for r in conn.execute(f"""
            SELECT * FROM {table}
            WHERE branch_id=? AND order_status='Completed' AND strftime('%Y-%m', order_date)=?
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

    by_day = {}
    for (day, cat, bank) in set(pos) | set(ext):
        pos_amt = pos.get((day, cat, bank), 0.0)
        eb = ext.get((day, cat, bank))
        ext_amt = eb["amount"] if eb else 0.0
        ext_net = eb["net"] if eb else 0.0

        if eb and pos_amt != 0:                    # both sides present (pos may be a refund)
            status = "matched" if _within_tol_day(ext_amt, pos_amt) else "variance"
        elif eb:                                   # pos 0, ext present
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
            "variance": round(ext_amt - pos_amt, 2), "status": status,
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
            "matched": sum(1 for l in lanes if l["status"] == "matched"),
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
