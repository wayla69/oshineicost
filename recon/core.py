"""recon/core.py — the state and helpers every recon endpoint family shares.

Moved out of main.py by the AST in Phase 7b (2026-09): the database path and
the per-request connection (see get_db for why it is NOT backend.core's), the
write/manager token gates, period and branch validation, the audit trail
writer, and the constants and seed tables more than one family reads.

Nothing here imports main.py. main.py imports this and re-exports every name,
so `main.get_db`, `main._DB_PATH` … keep working for the code and tests that
spell them that way. Assign through THIS module — `recon.core._DB_PATH = x` —
to point the app at another database; main._DB_PATH is a copy.
"""
from __future__ import annotations
from .matcher import _cc_acquirer
from .matcher import _eff_fee_vat
from .matcher import _point_mids
from .matcher import coupon_discount_total_daily
from .matcher import daily_reconciliation
from .matcher import point_pos_total_daily
from .parsers.bank import STATEMENT_CODE_MAP
# Session parsing comes from the dashboard's auth module when it is importable —
# recon can also run standalone, in which case the audit "by" falls back to the
# client value and /api/_diag/auth-wiring reports why.
try:
    from backend.auth import parse_token as _parse_session, SESSION_COOKIE as _SESSION_COOKIE
    _AUTH_IMPORT_ERR = None
except Exception as _e:                              # standalone recon
    _parse_session, _SESSION_COOKIE = None, "oshinei_session"
    _AUTH_IMPORT_ERR = repr(_e)
from . import auth as _auth   # recon's OWN login (RECON_LOGIN) — not backend.auth
from contextlib import contextmanager
from datetime import datetime as _dt
from fastapi import HTTPException
from fastapi import Header
from fastapi import Request
from pathlib import Path
import hmac
import os
import re
import sqlite3


_DB_PATH    = Path(os.getenv("OSHINEI_DB", "/data/oshinei.db"))


_SCHEMA     = Path(__file__).parent / "schema.sql"


_ATTACH_DIR = Path(os.getenv("OSHINEI_ATTACH_DIR", str(_DB_PATH.parent / "attachments")))


_SNAPSHOT_DIR = Path(os.getenv("OSHINEI_SNAPSHOT_DIR", str(_DB_PATH.parent / "snapshots")))


@contextmanager
def get_db():
    """One connection per request — deliberately NOT backend.core.get_db.

    core keeps a persistent connection per worker thread and relies on the
    hot-swap in the SAME process to close them when /data/oshinei.db is
    replaced. Recon runs as its own uvicorn process (start.sh); a cached
    connection here would keep the old file's inode open and serve stale data
    after every data refresh until a restart. Reconnecting per request is what
    makes recon see the swapped-in file immediately. Same PRAGMAs as core
    (WAL, foreign keys on); the write token is the same OSHINEI_SEED_TOKEN.
    (Structural plan phase 5, 2026-09: shared where sharing is right.)"""
    conn = sqlite3.connect(_DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
    finally:
        conn.close()


def _has_table(conn, name: str) -> bool:
    """The POS day/month tables (pos_payment_day, sales_month_summary, …) are
    written by the dashboard's sync, not by recon. On a fresh deployment, a
    restored database or the test fixture they do not exist yet, and the
    endpoints that read them used to answer 500. They check first now and say
    "not available yet" — which is the truth, not an error."""
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('table','view') AND name=?", (name,)
    ).fetchone() is not None


_POS_DAY_UNAVAILABLE = ("ยังไม่มีข้อมูล POS รายวันในฐานข้อมูลนี้ — "
                        "จะปรากฏหลัง sync ยอดขายจากสำนักงานใหญ่ครั้งแรก")


# Lane defaults encode the owner's table. Parameter values = the engine's current
# constants (so seeding alone changes no live result) EXCEPT cash tol_abs_day=10
# (owner decision 2026-06-12: deposit slips may differ by a few baht — pass ≤฿10
# but keep the variance visible).
_METHOD_DEFAULTS = [
    # (category, bank, doc1, doc2, method, tol_abs, tol_abs_day, lag, merge, fee_vat, notes)
    ("CASH", "*", "deposit_slip", "bank_stmt_*", "cash_3way", None, 10.0, 3, 4, 0,
     "เงินสดของวัน D ฝากวันถัดไปก่อน 13:00 — เทียบ POS ↔ สลิปนำฝาก ↔ สเตทเมนต์ (ต่างได้หลักบาท)"),
    # itemized lanes: lag_days/merge_days seeded for display in Setup UI only —
    # the itemized matcher uses daily exact-match, not lag/merge grouping.
    ("QR", "KBANK", None, "bank_stmt_KBANK", "itemized", None, None, 1, 1, 0,
     "QR พร้อมเพย์ (K-SHOP/KNNQR/QR code K-bank) — เงินเข้าวันเดียวกันหรือวันถัดไป แยกรายการตาม POS"),
    ("TRANSFER", "KTB", None, "bank_stmt_KTB", "itemized", None, None, 1, 1, 0,
     "ลูกค้าโอนเข้า KTB — เข้าวันเดียวกันหรือวันถัดไป แยกรายการตาม POS"),
    ("TRANSFER", "KBANK", None, "bank_stmt_KBANK", "itemized", None, None, 1, 1, 0,
     "ลูกค้าโอนเข้า KBANK — เข้าวันเดียวกันหรือวันถัดไป แยกรายการตาม POS"),
    ("TRANSFER", "ANY", None, "bank_stmt_*", "itemized", None, None, 1, 1, 0,
     "Bank Transfer ทั่วไป — เข้าได้ทั้ง KTB และ KBANK ตรวจกับทั้งสองสเตทเมนต์"),
    ("CREDIT", "KBANK", "cc_summary", "bank_stmt_KBANK", "two_stage", None, None, 7, 4, 1,
     "ขั้น 1: สรุปยอด KBANK ↔ สเตทเมนต์ KBANK (อาจรวมหลายวัน) · ขั้น 2: gross ก่อนค่าธรรมเนียม+VAT ↔ POS"),
    ("CREDIT", "KTB", "cc_summary", "bank_stmt_KTB", "two_stage", None, None, 7, 4, 1,
     "ขั้น 1: สรุปยอด KTC ↔ สเตทเมนต์ KTB (อาจรวมหลายวัน) · ขั้น 2: gross ก่อนค่าธรรมเนียม+VAT ↔ POS"),
    ("CREDIT", "*", "cc_summary", "bank_stmt_*", "two_stage", None, None, 7, 4, 1, None),
    ("GRAB", "*", "grab_report", "bank_stmt_KBANK", "two_stage", None, None, 7, 4, 1,
     "Grab โอนเข้า KBANK วันทำการถัดไป (อาจรวม 2 วันเป็นยอดเดียว) — สรุปมีค่าคอม+VAT"),
    ("LINEMAN", "*", "lineman_report", "bank_stmt_KBANK", "two_stage", None, None, 7, 4, 1,
     "Lineman โอนเข้า KBANK วันทำการถัดไป (อาจรวม 2 วันเป็นยอดเดียว) — สรุปมีค่าคอม+VAT"),
    ("COUPON", "KTC", "points_summary", "bank_stmt_KTB", "issuer_points", None, None, None, None, 0,
     "แต้ม KTC — ใบสรุปคะแนนจาก KTC ↔ สเตทเมนต์ KTB (อยู่ในยอดฝากรวมของบัตร KTC)"),
    ("COUPON", "BAY", "points_summary", "bank_stmt_KBANK", "issuer_points", None, None, None, None, 0,
     "แต้ม BAY (กรุงศรี) — ใบสรุปคะแนนจาก BAY ↔ สเตทเมนต์ KBANK"),
]


def _seed_method_defaults(conn) -> dict:
    inserted = 0
    for row in _METHOD_DEFAULTS:
        cur = conn.execute("""
            INSERT INTO recon_method_config
                (category, bank, doc1, doc2, method, tol_abs, tol_abs_day,
                 lag_days, merge_days, fee_vat_expected, notes, updated_by)
            VALUES (?,?,?,?,?,?,?,?,?,?,?, 'seed')
            ON CONFLICT(category, bank) DO NOTHING
        """, row)
        inserted += cur.rowcount
    return {"inserted": inserted, "skipped": len(_METHOD_DEFAULTS) - inserted}


# The 12 codes of the owner's table with their now-known routing. Inserted when
# missing; existing SEED rows with an unresolved bank are upgraded (staff edits
# are never touched — guarded on updated_by).
_REQUIREMENT_CODES = [
    ("CASH",            "CASH",     "NONE"),
    ("Cash",            "CASH",     "NONE"),
    ("Coupon",          "COUPON",   "NONE"),
    ("Credit กสิกรไทย",  "CREDIT",   "KBANK"),
    ("Credit กรุงไทย",   "CREDIT",   "KTB"),
    ("GB",              "GRAB",     "NONE"),
    ("LM",              "LINEMAN",  "NONE"),
    ("K-SHOP",          "QR",       "KBANK"),
    ("KNNQR",           "QR",       "KBANK"),
    ("QR code K-bank",  "QR",       "KBANK"),
    ("Bank Transfer",   "TRANSFER", "ANY"),
    ("โอน KTB",         "TRANSFER", "KTB"),
]


_SEED_AUTHORS = ("seed", "seed-req")


def _seed_requirement_codes(conn) -> dict:
    """Apply the owner's routing answers to un-edited seed rows + insert any of
    the 12 canonical codes still missing. Every change is audited."""
    upgraded = inserted = 0
    for code, category, bank in _REQUIREMENT_CODES:
        old = conn.execute(
            "SELECT id, category, bank, updated_by FROM recon_payment_map "
            "WHERE payment_code_norm=?", (code,)).fetchone()
        if old is None:
            cur = conn.execute("""
                INSERT INTO recon_payment_map (payment_code_norm, category, bank, reconcile, updated_by)
                VALUES (?,?,?,1,'seed-req') ON CONFLICT(payment_code_norm) DO NOTHING
            """, (code, category, bank))
            inserted += cur.rowcount
            continue
        # Upgrade seed-authored rows (any bank) whose mapping differs from the canonical value.
        if (old["updated_by"] in _SEED_AUTHORS
                and (old["category"], old["bank"]) != (category, bank)):
            conn.execute(
                "UPDATE recon_payment_map SET category=?, bank=?, "
                "updated_by='seed-req', updated_at=datetime('now') WHERE id=?",
                (category, bank, old["id"]))
            diffs = [{"field": "bank", "old": old["bank"], "new": bank}]
            if old["category"] != category:
                diffs.append({"field": "category", "old": old["category"], "new": category})
            _audit(conn, "payment_map", old["id"], "update", "seed-req", diffs)
            upgraded += 1
    return {"upgraded": upgraded, "inserted": inserted}


_MASTER_ONLY = "COALESCE(branch_type,'master')='master' AND active=1"


# Reconciliation only considers data from this month onward — the app reconciles
# the live business, not the full historical DB (which the dashboard shares).
# Floor only; nothing is deleted. Override with env RECON_START_MONTH (YYYY-MM).
RECON_START_MONTH = os.getenv("RECON_START_MONTH", "2026-05")


RECON_START_DAY   = RECON_START_MONTH + "-01"


_PERIOD_RE       = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


def _accepted_tokens() -> dict:
    """Parse OSHINEI_SEED_TOKEN into {token: label}. Comma/newline list so several
    people can each hold a token; entries may be 'label:token'. Single token still
    works. Shared shape with the dashboard gate."""
    raw = os.getenv("OSHINEI_SEED_TOKEN", "") or ""
    out: dict = {}
    for part in raw.replace("\n", ",").split(","):
        part = part.strip()
        if not part:
            continue
        if ":" in part:
            label, tok = part.split(":", 1)
            tok = tok.strip()
            if tok:
                out[tok] = label.strip()
        else:
            out[part] = ""
    return out


def _same_secret(a: str, b: str) -> bool:
    # compare_digest on str raises TypeError for non-ASCII (a header decoded as
    # latin-1) → a 500 instead of a 403. Bytes compare any value safely.
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


def require_write_token(request: Request, x_upload_token: str = Header(None)):
    """Staff write gate. With the recon login ON (RECON_LOGIN=1) a logged-in user
    passes — the middleware already refused viewers and anonymous callers; a
    caller with no session must hold the write token (the bulk-upload script).
    Login OFF: unchanged — the token when OSHINEI_SEED_TOKEN is set, open locally."""
    if _auth.login_enabled():
        if _auth.current_user(request) is not None:
            return
        # Session-less with the login ON: the token opens the upload endpoints only
        # (the bulk uploader) — never matching, sign-off evidence or config.
        if not _auth.machine_path_ok(request):
            raise HTTPException(401, _auth.MSG_NEED_LOGIN)
    toks = _accepted_tokens()
    if toks:
        provided = (x_upload_token or "").strip()      # header only - a ?token= would be logged
        if not any(_same_secret(provided, t) for t in toks):
            raise HTTPException(403, "A valid write token is required.")


def _require_manager(x_manager_token: str | None, request=None):
    """Two-tier close: approving a month needs the manager code when
    OSHINEI_MANAGER_TOKEN is set (open locally when unset, like the write gate).
    With the recon login ON the shared code is not used: the session's ROLE must be
    manager (no request → refused: fail closed)."""
    if _auth.login_enabled():
        _auth.require_role(request, "manager")
        return
    expected = (os.environ.get("OSHINEI_MANAGER_TOKEN") or "").strip()
    if expected and not _same_secret((x_manager_token or "").strip(), expected):
        raise HTTPException(403, "ต้องใช้รหัสผู้จัดการเพื่อปิดเดือน (X-Manager-Token)")


def require_manager_token(request: Request, x_manager_token: str = Header(None)):
    """Dependency for advanced-setup mutations (method parameters, tolerances):
    a bad value can mass-flip lane statuses, so these need the MANAGER code —
    ordinary code↔bank mapping stays on the staff write token (owner decision
    2026-06-12). Open locally when OSHINEI_MANAGER_TOKEN is unset.
    With the recon login ON: the session role must be manager instead."""
    if _auth.login_enabled():
        _auth.require_role(request, "manager")
        return
    expected = (os.environ.get("OSHINEI_MANAGER_TOKEN") or "").strip()
    if expected and not _same_secret((x_manager_token or "").strip(), expected):
        raise HTTPException(403, "ต้องใช้รหัสผู้จัดการ (X-Manager-Token) สำหรับการตั้งค่าขั้นสูง")


def _actor(request, fallback=None, default="staff"):
    """Who did it. Recon login ON: the recon user's USERNAME (unique and never
    renamed, so SoD compares real identities); a session-less machine call (bulk
    upload with the write token) is 'token[:label]'; the client value is ignored.
    Login OFF (unchanged): the dashboard session's username, else the
    client-supplied fallback, else `default`. Pass default='' for identity fields
    that must stay blank when the actor is unknown (e.g. SoD compares created_by vs
    approved_by — two blanks must NOT read as the same person). Never raises."""
    if _auth.login_enabled():
        try:
            u = _auth.current_user(request)
            if u:
                return u["username"]
            return (_auth.machine_token_label(request) if request is not None else None) or default
        except Exception:
            return default
    if _parse_session is not None and request is not None:
        try:
            tok = request.cookies.get(_SESSION_COOKIE)
            if tok:
                payload = _parse_session(tok)
                if payload and payload.get("u"):
                    return str(payload["u"])
        except Exception:
            pass
    return (str(fallback).strip() if fallback else "") or default


def _validate_period(period_month: str):
    if not _PERIOD_RE.match(period_month or ""):
        raise HTTPException(400, "period_month must be in 'YYYY-MM' format.")


def _validate_master_branch(conn, branch_id: int):
    if not conn.execute(
        f"SELECT 1 FROM branches WHERE branch_id=? AND {_MASTER_ONLY}", (branch_id,)
    ).fetchone():
        raise HTTPException(400, f"branch_id {branch_id} is not an active master branch.")


def _assert_period_open(conn, branch_id: int, period_month: str):
    """Reject mutations to an approved/closed period. Reopen by setting the
    session status back to In-Review (or Draft). 423 = Locked."""
    row = conn.execute(
        "SELECT status FROM recon_sessions WHERE branch_id=? AND period_month=?",
        (branch_id, period_month)).fetchone()
    if row and row["status"] in ("approved", "closed"):
        raise HTTPException(
            423, f"{period_month} is {row['status']} for this branch — set the "
                 "session back to In-Review to make changes.")


def _prev_month(period_month: str) -> str:
    y, m = int(period_month[:4]), int(period_month[5:7])
    return f"{y - 1}-12" if m == 1 else f"{y}-{m - 1:02d}"


def _is_locked(conn, branch_id: int, period_month: str) -> bool:
    row = conn.execute(
        "SELECT status FROM recon_sessions WHERE branch_id=? AND period_month=?",
        (branch_id, period_month)).fetchone()
    return bool(row and row["status"] in ("approved", "closed"))


def _locked_prev_claims(conn, branch_id: int, txn_date: str | None, cache: dict | None = None):
    """(prev_month, claimed bank-line ids) when the line's date falls in the window
    LAST month's matchers read (the first max-lag days of its month) and last month
    is approved/closed for the branch; else None. The cash / card / payout matchers
    of month M read bank credits up to lag_days into M+1, so such a line can be part
    of a signed-off month's result. `cache` (per request) avoids recomputing."""
    from .matcher import max_lag_days, month_claimed_bank_line_ids
    d = str(txn_date or "")[:10]
    if not _DAY_RE.match(d):
        return None
    if cache is not None and "lag" in cache:
        lag = cache["lag"]
    else:
        lag = max_lag_days(conn)
        if cache is not None:
            cache["lag"] = lag
    if int(d[8:10]) > lag:                      # outside every next-month window
        return None
    prev = _prev_month(d[:7])
    if not _is_locked(conn, branch_id, prev):
        return None
    # Cached per request WITHOUT total_changes: the cache serves removal checks only,
    # and taking out a credit the month did not claim leaves its claims unchanged.
    key = (branch_id, prev)
    if cache is not None and key in cache:
        return prev, cache[key]
    ids = month_claimed_bank_line_ids(conn, branch_id, prev)
    if cache is not None:
        cache[key] = ids
    return prev, ids


def _lock_msg(prev: str, txn_date: str) -> str:
    return (f"รายการวันที่ {str(txn_date)[:10]} ถูกใช้กระทบยอดของเดือน {prev} ซึ่งปิด/อนุมัติแล้ว "
            f"— แก้ไขไม่ได้ (เปิดงวด {prev} ก่อนหากจำเป็น)")


def _assert_bank_line_open(conn, branch_id: int, line_id: int, txn_date: str | None,
                           cache: dict | None = None) -> None:
    """423 when a bank line may not change: its own month is approved/closed, or it
    is one of the credits a signed-off PREVIOUS month's result rests on (a 1st-of-
    month deposit that banked last month's 31st). Use before a change that takes the
    line OUT of the matchers (ignore, manual match, delete): a credit last month did
    not claim can't alter last month's figures when it leaves."""
    _assert_period_open(conn, branch_id, str(txn_date or "")[:7])
    hit = _locked_prev_claims(conn, branch_id, txn_date, cache)
    if hit and line_id in hit[1]:
        raise HTTPException(423, _lock_msg(hit[0], txn_date))


def _assert_bank_line_release_ok(conn, branch_id: int, line_id: int, txn_date: str | None) -> None:
    """After a change that puts a bank line BACK into the matchers (undo an ignore,
    undo a manual match) — call inside the transaction, after the UPDATE: 423 when a
    signed-off previous month would now claim it (its figures would change). The
    caller's uncommitted UPDATE is discarded with the refusal."""
    hit = _locked_prev_claims(conn, branch_id, txn_date, None)
    if hit and line_id in hit[1]:
        raise HTTPException(423, _lock_msg(hit[0], txn_date))


_CATEGORIES = {"CASH", "QR", "TRANSFER", "CREDIT", "GRAB", "LINEMAN", "COUPON", "ENTERTAIN", "OTHER"}


_BANKS      = {"KTB", "KBANK", "KTC", "BAY", "SCB", "BBL", "TTB", "PROMPTPAY", "ANY", "NONE", "UNKNOWN"}


# bank statement channel codes → our category vocabulary
_BANK_CH_TO_CAT = {
    "QR": "QR", "TRANSFER": "TRANSFER", "CASH_DEP": "CASH",
    "CC_SETTLE": "CREDIT", "GRAB_PAYOUT": "GRAB", "LINEMAN_PAYOUT": "LINEMAN",
}


# parser channel → (recon category, plain-Thai label)
_PARSE_CH_TH = {
    "QR":             ("QR",      "QR พร้อมเพย์"),
    "TRANSFER":       ("TRANSFER", "เงินโอน"),
    "CASH_DEP":       ("CASH",    "เงินสด (ฝากเข้าบัญชี)"),
    "CC_SETTLE":      ("CREDIT",  "บัตรเครดิต (settlement)"),
    "GRAB_PAYOUT":    ("GRAB",    "Grab (เงินโอนยอด)"),
    "LINEMAN_PAYOUT": ("LINEMAN", "Line Man (เงินโอนยอด)"),
    "FEE":            ("—",       "ค่าธรรมเนียม (ไม่ใช่รายได้)"),
    "OTHER":          ("—",       "อื่น ๆ / ไม่จัดประเภท"),
}


def _statement_code_overrides(conn) -> dict:
    """{CODE(upper): channel} — manager remaps that win over the hardcoded map."""
    try:
        return {r["code"].upper(): r["channel"]
                for r in conn.execute("SELECT code, channel FROM recon_statement_code_map")}
    except sqlite3.OperationalError:
        return {}


def _override_channel(description: str | None, base_channel: str | None, overrides: dict) -> str | None:
    """Override wins when its code is a substring of the line description (longest code
    first); otherwise the parser's base channel stands."""
    if overrides:
        d = (description or "").upper()
        hits = [(code, ch) for code, ch in overrides.items() if code and code in d]
        if hits:
            return max(hits, key=lambda x: len(x[0]))[1]
    return base_channel


def _statement_codes(conn=None) -> list[dict]:
    overrides = _statement_code_overrides(conn) if conn is not None else {}
    out, matched = [], set()
    for e in STATEMENT_CODE_MAP:
        cat, ch_th = _PARSE_CH_TH.get(e["channel"], (e["channel"], e["channel"]))
        eff, ov_code = e["channel"], None
        for code, ch in overrides.items():       # an override whose code is in this entry's pattern
            if code in e["pattern"].upper():
                eff, ov_code = ch, code; matched.add(code)
        eff_cat, eff_th = _PARSE_CH_TH.get(eff, (eff, eff))
        out.append({**e, "category": cat, "channel_th": ch_th,
                    "effective_channel": eff, "effective_channel_th": eff_th,
                    "effective_category": eff_cat,
                    "overridden": eff != e["channel"], "override_code": ov_code})
    for code, ch in overrides.items():           # codes the team added that aren't in the base map
        if code not in matched:
            eff_cat, eff_th = _PARSE_CH_TH.get(ch, (ch, ch))
            out.append({"pattern": code, "code": code, "kind": "code", "bank": "*",
                        "meaning_th": "(เพิ่มโดยทีม)", "status": "confirmed", "note": "",
                        "channel": ch, "category": eff_cat, "channel_th": eff_th,
                        "effective_channel": ch, "effective_channel_th": eff_th,
                        "effective_category": eff_cat, "overridden": True, "override_code": code})
    return out


def _audit(conn, entity: str, entity_id: int, action: str, by: str,
           diffs: list[dict] | None = None):
    """Record create/update/delete with optional per-field diffs.
    diffs = [{"field": "status", "old": "draft", "new": "approved"}, ...]
    """
    ts = _dt.utcnow().isoformat(timespec="seconds")
    if not diffs:
        conn.execute(
            "INSERT INTO recon_audit_log(ts,entity,entity_id,action,field,old_val,new_val,by)"
            " VALUES(?,?,?,?,?,?,?,?)",
            (ts, entity, entity_id, action, None, None, None, by or "staff"),
        )
    else:
        for d in diffs:
            old = None if d.get("old") is None else str(d["old"])
            new = None if d.get("new") is None else str(d["new"])
            conn.execute(
                "INSERT INTO recon_audit_log(ts,entity,entity_id,action,field,old_val,new_val,by)"
                " VALUES(?,?,?,?,?,?,?,?)",
                (ts, entity, entity_id, action, d["field"], old, new, by or "staff"),
            )


_DAY_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$")


def _m(v):
    return f"{(v or 0):,.2f}"


_MATCH_CH_TH = {"QR": "QR พร้อมเพย์", "CASH": "เงินสด", "TRANSFER": "เงินโอน",
                "CREDIT": "บัตรเครดิต", "GRAB": "Grab", "LINEMAN": "Line Man", "COUPON": "คูปอง",
                "DISCOUNT": "ส่วนลด (คูปอง)"}


def _account_map(conn):
    """slot → (account_code, account_name) from recon_account_map; {} if unset.
    Reports render blank cells for slots with no code yet (owner fills in later)."""
    try:
        return {r["slot"]: (r["account_code"] or "", r["account_name"] or "")
                for r in conn.execute(
                    "SELECT slot, account_code, account_name FROM recon_account_map")}
    except sqlite3.OperationalError:
        return {}


# Channels carrying a platform fee/VAT + a separate discount line (gross from the
# document). cols: (channel, table, date_col, gross, fee, vat, discount, extra-where)
_FEE_CH_SPEC = [
    ("CREDIT",  "recon_cc_lines",      "settlement_date", "gross_amount", "mdr_fee",        "vat_amount",        "refund_amount", ""),
    ("GRAB",    "recon_grab_lines",    "order_date",      "food_amount",  "commission_amt", "vat_amount",        "promo_deduct",  " AND order_status='Completed'"),
    ("LINEMAN", "recon_lineman_lines", "order_date",      "food_amount",  "commission_amt", "vat_on_commission", "promo_deduct",  " AND order_status='Completed'"),
]


_FINANCE_CH_ORDER = {"CASH": 0, "QR": 1, "TRANSFER": 2, "CREDIT": 3, "GRAB": 4, "LINEMAN": 5,
                     "COUPON": 6, "DISCOUNT": 7}


def _daily_finance_data(conn, branch_id: int, period_month: str, daily=None) -> dict:
    """Per-DAY accounting breakdown as structured data: each day → its channels, each
    recorded at GROSS with discount/fee/VAT split out (net = gross − discount − fee −
    VAT) + account code/name from recon_account_map (blank until filled in). Single
    source for the table inside the monthly report (_daily_finance_html), the in-app
    daybook cards, and the printable per-day card report ('1 วัน = 1 ใบ').
    `daily` lets a caller pass a precomputed daily_reconciliation result so the dashboard
    (which also needs the POS↔money side) doesn't recompute it."""
    acct = _account_map(conn)

    # (day, channel) -> {gross, discount, fee, vat} from the fee-bearing documents
    agg: dict = {}
    def cell(day, ch):
        return agg.setdefault((day, ch), {"gross": 0.0, "discount": 0.0, "fee": 0.0, "vat": 0.0})
    pmids = _point_mids(conn, branch_id)
    for ch, tbl, dcol, gcol, fcol, vcol, discol, extra in _FEE_CH_SPEC:
        try:
            lines = conn.execute(
                f"SELECT * FROM {tbl} WHERE branch_id=? AND substr({dcol},1,7)=?{extra}",
                (branch_id, period_month)).fetchall()
        except sqlite3.OperationalError:
            continue
        for r in lines:
            day = (r[dcol] or "")[:10]
            if not day:
                continue
            # KTC/BAY point settlements are booked under COUPON (from the POS tender
            # split below) — their cc rows must not ALSO count as card revenue.
            if ch == "CREDIT" and (_cc_acquirer(r, pmids) or "").endswith("POINT"):
                continue
            fee, vat = _eff_fee_vat(r, fcol, vcol)
            a = cell(day, ch)
            a["gross"] += float(r[gcol] or 0); a["fee"] += fee; a["vat"] += vat
            a["discount"] += float(r[discol] or 0)

    # POS-tender channels (cash/QR/transfer/coupon): gross = POS that day, no fee/discount.
    try:
        d = daily if daily is not None else daily_reconciliation(conn, branch_id, period_month)
    except Exception:
        d = {"days": []}
    for day in d.get("days", []):
        for l in day["lanes"]:
            if l["category"] in ("CASH", "QR", "TRANSFER", "COUPON") and abs(l["pos"]) > 0.005:
                cell(day["day"], l["category"])["gross"] += l["pos"]

    # Split the COUPON tender: KTC/BAY loyalty points settle as REAL money via the
    # card issuer → keep in the COUPON line; everything else (ผู้สูงอายุ/เด็ก/อื่นๆ
    # discount coupons) is NOT money — book the full price as revenue and the discount
    # portion as a deduction (net 0). So revenue stays gross and net = cash actually in.
    points_by_day = point_pos_total_daily(conn, branch_id, period_month)
    for day in [k[0] for k in list(agg) if k[1] == "COUPON"]:
        cp = agg[(day, "COUPON")]
        coupon_total = cp["gross"]
        pts = min(max(points_by_day.get(day, 0.0), 0.0), coupon_total)
        disc = round(coupon_total - pts, 2)
        cp["gross"] = round(pts, 2)
        if disc > 0.005:
            dd = cell(day, "DISCOUNT")
            dd["gross"] += disc; dd["discount"] += disc
        if pts <= 0.005:                       # all discount, no real coupon money — drop empty lane
            agg.pop((day, "COUPON"), None)

    # Bill-level coupon discounts (ส่วนลดเด็ก / ผู้สูงอายุ / วันเกิด …) reduce the bill;
    # the customer then pays the NET via cash/QR/card. They are NOT a payment tender, so
    # without this they're invisible here and ส่วนลด shows 0. Book revenue at GROSS and
    # the discount on its own line (net 0) — same shape as the coupon-points remainder.
    for day, cdisc in coupon_discount_total_daily(conn, branch_id, period_month).items():
        if cdisc > 0.005:
            dd = cell(day, "DISCOUNT")
            dd["gross"] += cdisc
            dd["discount"] += cdisc

    def dom(dstr):
        try:
            return int(dstr[8:10])
        except Exception:
            return dstr

    days = []
    tot = {"gross": 0.0, "discount": 0.0, "fee": 0.0, "vat": 0.0, "net": 0.0}
    for day in sorted({k[0] for k in agg}):
        chans = sorted({c for (dy, c) in agg if dy == day},
                       key=lambda c: _FINANCE_CH_ORDER.get(c, 9))
        rows, dt = [], {"gross": 0.0, "discount": 0.0, "fee": 0.0, "vat": 0.0, "net": 0.0}
        for ch in chans:
            a = agg[(day, ch)]
            net = a["gross"] - a["discount"] - a["fee"] - a["vat"]
            code, name = acct.get(ch, ("", ""))
            rows.append({"ch": ch, "ch_th": _MATCH_CH_TH.get(ch, ch), "code": code, "name": name,
                         "gross": round(a["gross"], 2), "discount": round(a["discount"], 2),
                         "fee": round(a["fee"], 2), "vat": round(a["vat"], 2), "net": round(net, 2)})
            dt["gross"] += a["gross"]; dt["discount"] += a["discount"]; dt["fee"] += a["fee"]
            dt["vat"] += a["vat"]; dt["net"] += net
        days.append({"day": day, "dom": dom(day), "channels": rows,
                     "totals": {k: round(v, 2) for k, v in dt.items()}})
        for k in tot:
            tot[k] += dt[k]
    return {"days": days, "totals": {k: round(v, 2) for k, v in tot.items()}}
