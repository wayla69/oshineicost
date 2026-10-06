"""What every lane shares: tolerances, the channel→category map, method config, bank/acquirer helpers, POS totals.

Split out of recon/matcher.py on 2026-09-05 — same code, one lane per module;
recon.matcher re-exports every name."""
from __future__ import annotations
import contextvars
import copy
import functools
import json
import sqlite3
from contextlib import contextmanager
from datetime import date
from ..seed_payment_map import normalize


# ── Per-computation memo for the expensive matchers ───────────────────────────
# cash / payout / card matchers each look one month back (so a credit is never
# claimed twice across a month boundary) and are called by most views — often
# several times in one request. Within ONE composed computation (a scope) their
# results are shared; the key carries conn.total_changes so a write on that
# connection (run-match) invalidates it. Nothing outlives the outermost call.
_MEMO: contextvars.ContextVar = contextvars.ContextVar("recon_matcher_memo", default=None)


@contextmanager
def memo_scope():
    if _MEMO.get() is not None:                 # nested: share the outer scope
        yield
        return
    tok = _MEMO.set({})
    try:
        yield
    finally:
        _MEMO.reset(tok)


def memo_scoped(fn):
    """Run fn inside one memo scope (top-level views composing many matchers)."""
    @functools.wraps(fn)
    def wrap(*a, **kw):
        with memo_scope():
            return fn(*a, **kw)
    return wrap


def _freeze(x):
    if isinstance(x, dict):
        return tuple(sorted((k, _freeze(v)) for k, v in x.items()))
    if isinstance(x, (set, frozenset)):
        return frozenset(x)
    if isinstance(x, (list, tuple)):
        return tuple(_freeze(v) for v in x)
    return x


def memoized(fn):
    """Cache fn(conn, …) inside the current memo scope; callers get a deep copy."""
    @functools.wraps(fn)
    def wrap(conn, *a, **kw):
        with memo_scope():
            cache = _MEMO.get()
            try:
                key = (fn.__name__, id(conn), conn.total_changes, _freeze(a), _freeze(kw))
                hash(key)
            except (AttributeError, TypeError):
                return fn(conn, *a, **kw)
            if key not in cache:
                cache[key] = fn(conn, *a, **kw)
            return copy.deepcopy(cache[key])
    return wrap


TOL_ABS = 100.0    # THB  — monthly tolerance


TOL_PCT = 0.5      # %


TOL_ABS_DAY = 50.0 # THB  — daily tolerance (smaller amounts per day)


TOL_PCT_DAY = 1.0  # %


# Engine defaults for grouped matching (overridable per lane via recon_method_config)
DEF_CASH_MERGE, DEF_CASH_LAG = 4, 3     # cash lump deposits: ≤4 days into one credit, T+1..T+3


DEF_PAYOUT_MERGE, DEF_PAYOUT_LAG = 4, 7 # provider payouts / CC settlements


# LINE MAN settles on a balance THRESHOLD (฿500), not a daily T+1 schedule: small
# days accumulate until the balance crosses ฿500, and a whole month that never
# reaches ฿500 is remitted on the 1st of the NEXT month. So LINE MAN's payout
# grouping must be able to span a full month and reach the credit that lands just
# after the month boundary (mirrors the cash matcher's next-month window).
LINEMAN_PAYOUT_THRESHOLD = 500.0


DEF_LINEMAN_MERGE, DEF_LINEMAN_LAG = 31, 7


# Bank-statement channels that map to a reconcilable POS category
_CHANNEL_CATEGORY = {"QR": "QR", "TRANSFER": "TRANSFER", "CASH_DEP": "CASH"}


# category → a representative source_type for the discrepancy row
_CATEGORY_SOURCE = {
    "CASH": "bank", "QR": "bank", "TRANSFER": "bank",
    "CREDIT": "credit_card", "GRAB": "grab", "LINEMAN": "lineman", "COUPON": "pos",
}


def method_config(conn) -> dict:
    """{(category, bank): row-dict} from recon_method_config (the owner's per-lane
    method parameters: docs, tolerance, settlement lag, merge days). {} when the
    table doesn't exist yet (old DB / bare test schema) → engine defaults apply."""
    try:
        return {(r["category"], r["bank"]): dict(r) for r in
                conn.execute("SELECT * FROM recon_method_config")}
    except sqlite3.OperationalError:
        return {}


def _cfg(cfgs: dict, category: str, bank: str | None = None) -> dict:
    """Lane config with '*'-bank fallback."""
    return cfgs.get((category, bank)) or cfgs.get((category, "*")) or {}


def _within_tol(ext: float, pos: float, tol_abs: float | None = None) -> bool:
    # An explicit lane override is a strict absolute tolerance (no % fallback):
    # the owner sets e.g. ฿10 for cash slips and means exactly that.
    if tol_abs is not None:
        return abs(ext - pos) <= tol_abs
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
        # Cash has no bank dimension on the statement side (a deposit is a deposit),
        # so collapse CASH to NONE even if the code was mapped to a bank.
        bank = "NONE" if m["category"] == "CASH" else m["bank"]
        key = (m["category"], bank)
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


def _pd(s: str) -> date:
    return date(int(s[0:4]), int(s[5:7]), int(s[8:10]))


def _point_mids(conn, branch_id):
    """{merchant_id: acquirer} for point-redemption MIDs of a branch (e.g. the
    KTC Point MID), from recon_acquirer_mid. Lets us split point rows out of an
    acquirer settlement (KTC RTxInv mixes regular KTC + KTC Point by MID)."""
    try:
        return {(r["merchant_id"] or "").strip(): r["acquirer"]
                for r in conn.execute(
                    "SELECT merchant_id, acquirer FROM recon_acquirer_mid "
                    "WHERE branch_id=? AND acquirer LIKE '%POINT' AND COALESCE(active,1)=1 "
                    "AND COALESCE(merchant_id,'')<>''", (branch_id,))}
    except sqlite3.OperationalError:
        return {}


def _cc_acquirer(row, point_mids):
    """Effective acquirer for a cc settlement row: a point MID overrides to its
    point acquirer (KTCPOINT), else the row's own acquirer."""
    mid = (row["merchant_id"] or "").strip() if "merchant_id" in row.keys() else ""
    if mid and mid in point_mids:
        return point_mids[mid]
    return row["acquirer"] or _provider_acquirer(row["provider"]) if "provider" in row.keys() else row["acquirer"]


# ── Daily reconciliation ──────────────────────────────────────────────────────
# Staff reconcile per day. Some statements arrive late, so each day/lane carries a
# status that distinguishes a genuine gap ('missing' — the statement covers that
# day but the line isn't there) from an awaited document ('pending' — no statement
# yet covers that day).

def _within_tol_day(ext: float, pos: float, tol_abs: float | None = None) -> bool:
    if tol_abs is not None:                     # explicit lane override: strict absolute
        return abs(ext - pos) <= tol_abs
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
        bank = "NONE" if m["category"] == "CASH" else m["bank"]   # cash: no bank dimension
        key = (r["day"], m["category"], bank)
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


def _lane_cov_key(category, bank):
    if category == "CASH":                  return ("cash", None)
    if category in ("QR", "TRANSFER"):      return ("bank", bank)
    if category == "CREDIT":                return ("cc", bank)
    if category == "GRAB":                  return ("grab", None)
    if category == "LINEMAN":               return ("lineman", None)
    return (category.lower(), None)         # COUPON etc → no external source


_REAL_BANKS = {"KTB", "KBANK"}              # banks we get statements from


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


# ── Wrong-channel suggestions (same bank, equal amount, ±1 day) ───────────────
# Common data-entry error: a sale is booked under the wrong POS payment code, so
# the POS lands in lane A (e.g. QR·KBANK) while the money shows on the statement
# under lane B (e.g. TRANSFER·KBANK). It surfaces as a POS surplus in A and an
# unmatched statement line in B of the SAME amount, same bank, same/adjacent day.
# We detect those pairs and return ready-to-apply manual-match payloads — the
# operator just clicks Apply (which POSTs to /api/manual).

def _d(s: str) -> date:
    return date(int(s[0:4]), int(s[5:7]), int(s[8:10]))
