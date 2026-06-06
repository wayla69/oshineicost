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

TOL_ABS = 100.0    # THB
TOL_PCT = 0.5      # %

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

    conn.commit()

    matched = sum(1 for r in rows if r["status"] == "matched")
    return {
        "categories":  [{k: r[k] for k in
                         ("category", "bank", "pos_amount", "ext_amount", "ext_net", "variance", "status")}
                        for r in rows],
        "matched":     matched,
        "discrepancies": len(rows) - matched,
        "unmapped":    [{"code": k, "amount": round(v, 2)} for k, v in sorted(unmapped.items())],
    }
