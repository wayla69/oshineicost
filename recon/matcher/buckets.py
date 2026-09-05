"""External lines bucketed per (category, bank) — monthly and daily — plus the TRANSFER·ANY spill and manual channel moves.

Split out of recon/matcher.py on 2026-09-05 — same code, one lane per module;
recon.matcher re-exports every name."""
from __future__ import annotations
import sqlite3
from datetime import date, timedelta
from ._shared import (
    _CHANNEL_CATEGORY,
    _REAL_BANKS,
    _cc_acquirer,
    _eff_fee_vat,
    _point_mids,
    _provider_acquirer,
    _provider_bank,
)
from .cash import cash_deposit_matching, prev_month_consumed_ids


def external_buckets(conn: sqlite3.Connection, branch_id: int, period_month: str) -> dict:
    """Aggregate external lines into {(category, bank): {amount, net, lines:[(table,id)]}}."""
    buckets: dict = {}

    def add(key, table, line_id, amount, net):
        b = buckets.setdefault(key, {"amount": 0.0, "net": 0.0, "lines": []})
        b["amount"] += amount
        b["net"]    += net
        b["lines"].append((table, line_id))

    # Bank statement: QR / TRANSFER / CASH_DEP credits ('excluded' on a bank
    # line = operator-flagged internal transfer, not revenue — skipped everywhere).
    # Lines the cash matcher claimed as deposits (often labelled QR/TRANSFER, e.g.
    # KTB 'SDCH') are re-bucketed to the CASH lane so monthly cash ties out and the
    # spurious QR/transfer ext_only disappears.
    cash_ids = cash_deposit_matching(conn, branch_id, period_month)["consumed_ids"]
    prev_ids = prev_month_consumed_ids(conn, branch_id, period_month)
    seen_ids = set()
    for r in conn.execute("""
        SELECT l.id, l.credit, l.channel, u.provider
        FROM recon_bank_lines l JOIN recon_uploads u ON u.upload_id = l.upload_id
        WHERE l.branch_id=? AND l.credit>0 AND l.match_status!='excluded'
          AND strftime('%Y-%m', l.txn_date)=?
    """, (branch_id, period_month)):
        seen_ids.add(r["id"])
        amt = float(r["credit"] or 0)
        if r["id"] in prev_ids:      # last month's rollover cash — not this month's revenue
            continue
        if r["id"] in cash_ids:
            add(("CASH", "NONE"), "recon_bank_lines", r["id"], amt, amt)
            continue
        cat = _CHANNEL_CATEGORY.get(r["channel"])
        if not cat:
            continue
        bank = "NONE" if cat == "CASH" else _provider_bank(r["provider"])
        add((cat, bank), "recon_bank_lines", r["id"], amt, amt)

    # Month-boundary fix: this month's last-day cash is banked next month (T+1), so
    # its deposit carries a NEXT-month txn_date that the in-month query above never
    # sees — yet the cash matcher already CLAIMED it for this month's sale days.
    # Add those claimed-but-out-of-month deposits so the monthly CASH total matches
    # the daily 3-way view (without this, every branch under-reports cash by ~the
    # last day's deposit). No double-count: NEXT month skips them via prev_ids.
    boundary_ids = [i for i in cash_ids if i not in seen_ids]
    if boundary_ids:
        ph = ",".join("?" * len(boundary_ids))
        for r in conn.execute(
            f"SELECT id, credit FROM recon_bank_lines "
            f"WHERE id IN ({ph}) AND match_status!='excluded'", tuple(boundary_ids)):
            amt = float(r["credit"] or 0)
            add(("CASH", "NONE"), "recon_bank_lines", r["id"], amt, amt)

    # Credit-card settlement: gross vs net (after effective fee/VAT)
    pmids = _point_mids(conn, branch_id)
    for r in conn.execute("""
        SELECT l.*, u.provider FROM recon_cc_lines l JOIN recon_uploads u ON u.upload_id = l.upload_id
        WHERE l.branch_id=? AND strftime('%Y-%m', l.settlement_date)=?
    """, (branch_id, period_month)):
        acq = _cc_acquirer(r, pmids)
        if acq.endswith("POINT"):      # loyalty (BAY/KTC point) — no POS-credit tender, reconciled separately
            continue
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


# ── TRANSFER 'ANY' (multi-bank codes, e.g. generic 'Bank Transfer') ───────────
# The owner's table: a generic transfer can land in KTB OR KBANK. Per-bank
# TRANSFER lanes consume their own statement lines FIRST; whatever transfer
# credit is left over feeds the (TRANSFER, ANY) lane so the money is matched
# without double-counting and a per-bank lane never shows a false surplus.

def _apply_transfer_any(pos: dict, ext: dict, key=("TRANSFER", "ANY")):
    """Mutates ext: moves each real-bank TRANSFER lane's leftover (ext beyond its
    own POS) into the ANY lane. No-op when no ANY POS exists."""
    if key not in pos:
        return
    resid, lines = 0.0, []
    for bk in _REAL_BANKS:
        k = ("TRANSFER", bk) if len(key) == 2 else (key[0], "TRANSFER", bk)
        eb = ext.get(k)
        if not eb:
            continue
        p = max(pos.get(k, 0.0), 0.0)
        leftover = max(0.0, eb["amount"] - p)
        if leftover > 0:
            resid += leftover
            eb["amount"] -= leftover
            if "net" in eb:
                eb["net"] -= leftover
    if resid > 0:
        b = ext.setdefault(key, {"amount": 0.0, "net": 0.0, "lines": lines})
        b["amount"] += resid
        b["net"] = b.get("net", 0.0) + resid


# ── Reconciliation (pure compute) ─────────────────────────────────────────────

def channel_move_deltas(conn, branch_id, period_month):
    """{(category, bank): signed_amount} — manual 'booked-in-wrong-channel' moves
    (recon_channel_move). Each move subtracts its amount from the (from_category,
    from_bank) POS lane and adds it to the (to_category, to_bank) POS lane, so a
    mis-entry is corrected on BOTH lanes at once. Folded into the POS base once at
    read-time; reversible by deleting the row. {} when the table isn't there yet."""
    out: dict = {}
    try:
        rows = conn.execute(
            "SELECT from_category, from_bank, to_category, to_bank, amount "
            "FROM recon_channel_move WHERE branch_id=? AND period_month=?",
            (branch_id, period_month)).fetchall()
    except sqlite3.OperationalError:
        return {}
    for r in rows:
        amt = float(r["amount"] or 0)
        if amt == 0:
            continue
        fk = (r["from_category"], r["from_bank"] or "NONE")
        tk = (r["to_category"],   r["to_bank"]   or "NONE")
        out[fk] = out.get(fk, 0.0) - amt
        out[tk] = out.get(tk, 0.0) + amt
    return out


def _external_buckets_daily(conn, branch_id, period_month, include_cash=True, exclude_ids=None):
    """{(day, category, bank): {amount, net}} from external lines, keyed by line date.
    Manually-matched lines (match_status='manual') are excluded — they're accounted
    for in the manual layer, so they don't double-count or show a false variance on
    the lump-sum's own date.

    include_cash=False skips CASH_DEP lines: daily_reconciliation matches cash
    through cash_deposit_matching() (handles T+1, same-day, and multi-day lump
    deposits), so the naive shift-back-one-day bucket would double-count.

    exclude_ids: bank-line ids the cash matcher has claimed as cash deposits (they
    may be parsed as QR/TRANSFER, e.g. KTB 'SDCH') — skip them here so they don't
    also show as an ext_only QR/transfer line on their own date."""
    exclude_ids = exclude_ids or set()
    buckets = {}
    def add(day, cat, bank, amount, net):
        b = buckets.setdefault((day, cat, bank), {"amount": 0.0, "net": 0.0})
        b["amount"] += amount; b["net"] += net

    for r in conn.execute("""
        SELECT l.id, l.txn_date d, l.credit, l.channel, u.provider
        FROM recon_bank_lines l JOIN recon_uploads u ON u.upload_id=l.upload_id
        WHERE l.branch_id=? AND l.credit>0
          AND l.match_status NOT IN ('manual','excluded')
          AND strftime('%Y-%m', l.txn_date)=?
    """, (branch_id, period_month)):
        if r["id"] in exclude_ids:
            continue
        cat = _CHANNEL_CATEGORY.get(r["channel"])
        if not cat:
            continue
        bank = "NONE" if cat == "CASH" else _provider_bank(r["provider"])
        # CASH_DEP lands on the bank a day after the sale (next-business-day deposit).
        # Shift the key back 1 day so the bucket aligns with the POS sale day.
        if cat == "CASH":
            if not include_cash:
                continue
            try:
                key_day = (date.fromisoformat(r["d"]) - timedelta(days=1)).isoformat()
            except (ValueError, TypeError):
                key_day = r["d"]
            if key_day[:7] != period_month:   # deposit in next month → out of scope
                continue
        else:
            key_day = r["d"]
        add(key_day, cat, bank, float(r["credit"] or 0), float(r["credit"] or 0))

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
