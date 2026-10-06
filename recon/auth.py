"""recon/auth.py — the reconciliation app's OWN login (owner decision 2026-10-06).

Separate from the dashboard on purpose: its own users (recon_users), its own
roles (manager / accountant / viewer) and its own signed cookie (recon_session,
never the dashboard's oshinei_session). Nothing here imports backend.auth.

Switches
  RECON_LOGIN=1            enforce the login (main._enforce_recon_session). Unset =
                           today's behaviour exactly (dashboard session + write
                           token + manager code). Logging in already WORKS while it is
                           unset, so the owner can create the accounts first.
  RECON_SESSION_SECRET     the HMAC key for the cookie (>= 16 chars). Missing while
                           RECON_LOGIN=1 -> every /api call answers 503: fail CLOSED.
  RECON_ADMIN_USER /       bootstrap: when recon_users is EMPTY at startup, create
  RECON_ADMIN_PASSWORD     this manager (must change the password at first login).

Crypto is the standard library only: scrypt (n=2**14, r=8, p=1) with a 16-byte
per-user salt, hmac.compare_digest everywhere a secret is compared, secrets for
randomness. The cookie is base64url(JSON payload) + "." + HMAC-SHA256 hex; the
payload carries {uid, role, exp, pwv}. The role and the account state are read
from the DATABASE on every request (a demotion or deactivation applies at once);
pwv must equal the row's pw_version, so a password reset or change logs every
old session out.

Nothing here imports recon.core at module level (core imports this module); the
DB-touching helpers take a connection or import core lazily.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import time
from datetime import datetime, timedelta, timezone

COOKIE = "recon_session"
SESSION_TTL = 12 * 3600                  # seconds — one working day, absolute (not sliding)
ROLES = ("manager", "accountant", "viewer")
ROLE_TH = {"manager": "ผู้จัดการ", "accountant": "บัญชี", "viewer": "ดูอย่างเดียว"}
MIN_PASSWORD = 8
MAX_FAILS = 5
LOCK_MINUTES = 15
_USERNAME_RE = re.compile(r"^[A-Za-z0-9._-]{2,40}$")

MSG_LOGIN_FAIL = ("ชื่อผู้ใช้หรือรหัสผ่านไม่ถูกต้อง — "
                  f"ถ้าใส่ผิด {MAX_FAILS} ครั้งติดกัน บัญชีจะถูกล็อก {LOCK_MINUTES} นาที")
MSG_NO_SECRET = ("ระบบเข้าสู่ระบบยังไม่พร้อม (ยังไม่ได้ตั้งค่า RECON_SESSION_SECRET บนเซิร์ฟเวอร์) — "
                 "ติดต่อผู้ดูแลระบบ")
MSG_NEED_LOGIN = "กรุณาเข้าสู่ระบบก่อนใช้งาน"
MSG_VIEWER = "สิทธิ์ดูอย่างเดียว — บัญชีนี้แก้ไขหรือบันทึกข้อมูลไม่ได้"
MSG_MANAGER_ONLY = "เฉพาะผู้จัดการเท่านั้น — บัญชีของคุณไม่มีสิทธิ์ทำรายการนี้"
MSG_MUST_CHANGE = "ต้องเปลี่ยนรหัสผ่านก่อนเริ่มใช้งาน"

_TRUE = {"1", "true", "yes", "on"}


# ── switches ──────────────────────────────────────────────────────────────────
def login_enabled() -> bool:
    return (os.environ.get("RECON_LOGIN") or "").strip().lower() in _TRUE


def _secret() -> bytes | None:
    s = (os.environ.get("RECON_SESSION_SECRET") or "").strip()
    return s.encode("utf-8") if len(s) >= 16 else None


def secret_ok() -> bool:
    return _secret() is not None


# ── time (UTC, ISO seconds — the same shape the audit log uses) ───────────────
def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)


def _iso(d: datetime) -> str:
    return d.isoformat(timespec="seconds")


def _parse_iso(s) -> datetime | None:
    try:
        return datetime.fromisoformat(str(s).replace(" ", "T")) if s else None
    except ValueError:
        return None


# ── password hashing ──────────────────────────────────────────────────────────
_SCRYPT = (2 ** 14, 8, 1)                 # n, r, p — ~16 MB, ~50 ms
_PBKDF2_ITERS = 600_000                   # fallback only (OpenSSL without scrypt)


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode("ascii").rstrip("=")


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    pw = password.encode("utf-8")
    if hasattr(hashlib, "scrypt"):
        n, r, p = _SCRYPT
        dk = hashlib.scrypt(pw, salt=salt, n=n, r=r, p=p, dklen=32)
        return f"scrypt${n}${r}${p}${_b64(salt)}${_b64(dk)}"
    dk = hashlib.pbkdf2_hmac("sha256", pw, salt, _PBKDF2_ITERS)
    return f"pbkdf2_sha256${_PBKDF2_ITERS}${_b64(salt)}${_b64(dk)}"


def verify_password(password: str, stored: str) -> bool:
    """Constant-time compare; any malformed hash is simply 'no'."""
    try:
        parts = (stored or "").split("$")
        pw = (password or "").encode("utf-8")
        if parts[0] == "scrypt" and len(parts) == 6:
            n, r, p = int(parts[1]), int(parts[2]), int(parts[3])
            want = _unb64(parts[5])
            got = hashlib.scrypt(pw, salt=_unb64(parts[4]), n=n, r=r, p=p, dklen=len(want))
        elif parts[0] == "pbkdf2_sha256" and len(parts) == 4:
            want = _unb64(parts[3])
            got = hashlib.pbkdf2_hmac("sha256", pw, _unb64(parts[2]), int(parts[1]), dklen=len(want))
        else:
            return False
        return hmac.compare_digest(got, want)
    except Exception:
        return False


_DUMMY_HASH: str | None = None


def _burn(password: str) -> None:
    """Spend the same time as a real check, so an unknown or locked username
    can't be told apart from a wrong password by the response time."""
    global _DUMMY_HASH
    if _DUMMY_HASH is None:
        _DUMMY_HASH = hash_password(secrets.token_hex(8))
    verify_password(password, _DUMMY_HASH)


def password_problem(password: str) -> str | None:
    if not isinstance(password, str) or len(password) < MIN_PASSWORD:
        return f"รหัสผ่านต้องยาวอย่างน้อย {MIN_PASSWORD} ตัวอักษร"
    if len(password) > 200:
        return "รหัสผ่านยาวเกินไป"
    return None


# ── session cookie ────────────────────────────────────────────────────────────
def make_cookie(uid: int, role: str, pwv: int, now: float | None = None) -> str:
    key = _secret()
    if key is None:
        raise RuntimeError("RECON_SESSION_SECRET is not set")
    payload = {"uid": int(uid), "role": role, "exp": int((now or time.time()) + SESSION_TTL),
               "pwv": int(pwv)}
    body = _b64(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    sig = hmac.new(key, body.encode("ascii"), hashlib.sha256).hexdigest()
    return f"{body}.{sig}"


def parse_cookie(token: str | None, now: float | None = None) -> dict | None:
    """The payload when the signature checks out and it has not expired, else None."""
    key = _secret()
    if not key or not token or "." not in token:
        return None
    body, _, sig = token.rpartition(".")
    want = hmac.new(key, body.encode("ascii", "replace"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig.encode("ascii", "replace"), want.encode("ascii")):
        return None
    try:
        p = json.loads(_unb64(body))
        if int(p["exp"]) <= int(now or time.time()):
            return None
        int(p["uid"]), int(p["pwv"])
        return p
    except Exception:
        return None


def cookie_secure(request) -> bool:
    """Secure flag when the browser is on https. Behind Fly's proxy the app sees
    plain http, so the forwarded scheme counts too."""
    if request.url.scheme == "https":
        return True
    return (request.headers.get("x-forwarded-proto") or "").split(",")[0].strip().lower() == "https"


def set_cookie(response, request, user_row) -> None:
    response.set_cookie(COOKIE, make_cookie(user_row["id"], user_row["role"], user_row["pw_version"]),
                        max_age=SESSION_TTL, httponly=True, samesite="lax",
                        secure=cookie_secure(request), path="/")


def clear_cookie(response, request) -> None:
    response.delete_cookie(COOKIE, path="/", httponly=True, samesite="lax",
                           secure=cookie_secure(request))


# ── users ─────────────────────────────────────────────────────────────────────
_PUBLIC = ("id", "username", "display_name", "role", "active", "must_change_pw",
           "created_at", "created_by", "last_login_at", "failed_count", "locked_until")


def public(row) -> dict:
    d = {k: row[k] for k in _PUBLIC}
    lu = _parse_iso(row["locked_until"])
    d["locked"] = bool(lu and lu > _now())
    d["role_th"] = ROLE_TH.get(row["role"], row["role"])
    return d


def me_dict(u: dict) -> dict:
    return {"username": u["username"], "display_name": u["display_name"] or u["username"],
            "role": u["role"], "role_th": ROLE_TH.get(u["role"], u["role"]),
            "must_change_pw": bool(u["must_change_pw"])}


def _has_users_table(conn) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='recon_users'"
                        ).fetchone() is not None


def user_for_session(conn, payload: dict) -> dict | None:
    """The live account behind a cookie payload: active, same password version."""
    if not payload or not _has_users_table(conn):
        return None
    row = conn.execute("SELECT * FROM recon_users WHERE id=?", (int(payload["uid"]),)).fetchone()
    if not row or not row["active"] or int(row["pw_version"]) != int(payload["pwv"]):
        return None
    return {"id": row["id"], "username": row["username"], "display_name": row["display_name"],
            "role": row["role"], "must_change_pw": bool(row["must_change_pw"])}


_SCOPE_KEY = "recon_auth_user"


def current_user(request) -> dict | None:
    """The logged-in recon user for this request, or None. Cached on the ASGI scope,
    so the middleware and the endpoint dependencies read the DB once per request."""
    if request is None:
        return None
    scope = getattr(request, "scope", None)
    if isinstance(scope, dict) and _SCOPE_KEY in scope:
        return scope[_SCOPE_KEY]
    user = None
    payload = parse_cookie(request.cookies.get(COOKIE))
    if payload:
        from .core import get_db                     # lazy: core imports this module
        try:
            with get_db() as conn:
                user = user_for_session(conn, payload)
        except Exception:
            user = None                               # DB briefly unavailable → not logged in
    if isinstance(scope, dict):
        scope[_SCOPE_KEY] = user
    return user


def machine_token_label(request) -> str | None:
    """'token[:label]' when the request carries a valid X-Upload-Token (the bulk
    uploader); None otherwise — also None when no token is configured at all."""
    from .core import _accepted_tokens, _same_secret  # lazy: core imports this module
    provided = (request.headers.get("x-upload-token") or "").strip()
    if not provided:
        return None
    for tok, label in _accepted_tokens().items():
        if _same_secret(provided, tok):
            return f"token:{label}" if label else "token"
    return None


def require_role(request, *roles: str) -> dict:
    """Raise 401 (not logged in) / 403 (wrong role) — used when login is enforced."""
    from fastapi import HTTPException
    u = current_user(request)
    if u is None:
        raise HTTPException(401, MSG_NEED_LOGIN)
    if roles and u["role"] not in roles:
        raise HTTPException(403, MSG_MANAGER_ONLY if roles == ("manager",) else MSG_VIEWER)
    return u


def validate_new_user(username: str, display_name: str, role: str, password: str) -> str | None:
    if not _USERNAME_RE.match(username or ""):
        return "ชื่อผู้ใช้ต้องเป็นตัวอักษรอังกฤษ ตัวเลข หรือ . _ - ยาว 2–40 ตัว (ไม่มีเว้นวรรค)"
    if role not in ROLES:
        return "บทบาทต้องเป็น manager / accountant / viewer"
    if len((display_name or "").strip()) > 80:
        return "ชื่อที่แสดงยาวเกินไป"
    return password_problem(password)


def create_user(conn, username: str, display_name: str, role: str, password: str,
                created_by: str, must_change: bool = True) -> int:
    """Insert one account (the caller validates, audits and commits)."""
    cur = conn.execute(
        "INSERT INTO recon_users(username, display_name, role, pw_hash, must_change_pw, created_at, created_by)"
        " VALUES(?,?,?,?,?,?,?)",
        (username.strip(), (display_name or "").strip() or username.strip(), role,
         hash_password(password), 1 if must_change else 0, _iso(_now()), created_by))
    return cur.lastrowid


def set_password(conn, uid: int, password: str, must_change: bool) -> None:
    """New hash + bump pw_version (every old session dies) + clear the lockout."""
    conn.execute(
        "UPDATE recon_users SET pw_hash=?, pw_version=pw_version+1, must_change_pw=?,"
        " failed_count=0, locked_until=NULL WHERE id=?",
        (hash_password(password), 1 if must_change else 0, uid))


def authenticate(conn, username: str, password: str) -> tuple[dict | None, str]:
    """(row, 'ok') on success, else (row-or-None, reason). Reasons are for the
    audit log only — the client always gets the same generic message.
    Five wrong passwords in a row lock the account for 15 minutes."""
    uname = (username or "").strip()
    row = conn.execute("SELECT * FROM recon_users WHERE username=?", (uname,)).fetchone() \
        if uname and _has_users_table(conn) else None
    if row is None:
        _burn(password)
        return None, "unknown_user"
    if not row["active"]:
        _burn(password)
        return row, "inactive"
    now = _now()
    lu = _parse_iso(row["locked_until"])
    if lu and lu > now:
        _burn(password)
        return row, "locked"
    if not verify_password(password, row["pw_hash"]):
        fails = int(row["failed_count"] or 0) + 1
        if fails >= MAX_FAILS:
            conn.execute("UPDATE recon_users SET failed_count=0, locked_until=? WHERE id=?",
                         (_iso(now + timedelta(minutes=LOCK_MINUTES)), row["id"]))
            return row, "bad_password_locked"
        conn.execute("UPDATE recon_users SET failed_count=?, locked_until=NULL WHERE id=?",
                     (fails, row["id"]))
        return row, "bad_password"
    conn.execute("UPDATE recon_users SET failed_count=0, locked_until=NULL, last_login_at=? WHERE id=?",
                 (_iso(now), row["id"]))
    return conn.execute("SELECT * FROM recon_users WHERE id=?", (row["id"],)).fetchone(), "ok"


def active_managers(conn, excluding: int | None = None) -> int:
    sql, a = "SELECT COUNT(*) FROM recon_users WHERE role='manager' AND active=1", []
    if excluding is not None:
        sql += " AND id<>?"; a.append(excluding)
    return conn.execute(sql, a).fetchone()[0]


def bootstrap_admin(conn) -> str | None:
    """First manager from RECON_ADMIN_USER / RECON_ADMIN_PASSWORD — only while
    recon_users is EMPTY (afterwards the variables are ignored). Returns the
    username created, or None. The caller audits and commits."""
    user = (os.environ.get("RECON_ADMIN_USER") or "").strip()
    pw = os.environ.get("RECON_ADMIN_PASSWORD") or ""
    if not user or not pw or not _has_users_table(conn):
        return None
    if conn.execute("SELECT COUNT(*) FROM recon_users").fetchone()[0]:
        return None
    problem = validate_new_user(user, user, "manager", pw)
    if problem:
        print(f"[recon-auth] RECON_ADMIN_USER/PASSWORD ignored: {problem}")
        return None
    create_user(conn, user, user, "manager", pw, created_by="bootstrap", must_change=True)
    return user
