"""Recon login + users admin (recon/auth.py holds the crypto and the rules).

  GET  /api/me                          always open: is the login on, who am I
  POST /api/auth/login                  username + password -> recon_session cookie
  POST /api/auth/logout
  POST /api/auth/change-password        old + new (also ends a forced change)
  GET  /api/users                       managers only (any time — not only when
  POST /api/users                       RECON_LOGIN=1, so the owner can create the
  PATCH /api/users/{id}                 accounts BEFORE switching enforcement on)
  POST /api/users/{id}/reset-password

Every login, failed login and user change is written to recon_audit_log
(entity 'recon_auth' / 'recon_user'; NOT 'auth', which is the dashboard's).
"""
from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel

from .. import auth as A
from ..core import _audit, get_db

router = APIRouter()


def _ip(request: Request) -> str:
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()[:64]
    return request.client.host if request.client else ""


def _need_secret():
    if not A.secret_ok():
        raise HTTPException(503, A.MSG_NO_SECRET)


@router.get("/api/me")
def me(request: Request):
    u = A.current_user(request) if A.secret_ok() else None
    return {"login_enabled": A.login_enabled(), "login_ready": A.secret_ok(),
            "user": A.me_dict(u) if u else None}


class _LoginBody(BaseModel):
    username: str = ""
    password: str = ""


@router.post("/api/auth/login")
def login(body: _LoginBody, request: Request, response: Response):
    _need_secret()
    uname = (body.username or "").strip()[:64]
    with get_db() as conn:
        row, reason = A.authenticate(conn, uname, body.password or "")
        if reason != "ok":
            _audit(conn, "recon_auth", row["id"] if row else 0, "login_fail", uname or "(blank)",
                   [{"field": "reason", "new": reason}, {"field": "ip", "new": _ip(request)}])
            conn.commit()
            raise HTTPException(401, A.MSG_LOGIN_FAIL)
        _audit(conn, "recon_auth", row["id"], "login", row["username"],
               [{"field": "ip", "new": _ip(request)}])
        conn.commit()
    A.set_cookie(response, request, row)
    return {"ok": True, "user": A.me_dict(dict(row))}


@router.post("/api/auth/logout")
def logout(request: Request, response: Response):
    u = A.current_user(request) if A.secret_ok() else None
    if u:
        with get_db() as conn:
            _audit(conn, "recon_auth", u["id"], "logout", u["username"])
            conn.commit()
    A.clear_cookie(response, request)
    return {"ok": True}


class _ChangePwBody(BaseModel):
    old: str = ""
    new: str = ""


@router.post("/api/auth/change-password")
def change_password(body: _ChangePwBody, request: Request, response: Response):
    _need_secret()
    u = A.require_role(request)
    problem = A.password_problem(body.new)
    if problem:
        raise HTTPException(400, problem)
    if body.new == body.old:
        raise HTTPException(400, "รหัสผ่านใหม่ต้องไม่ซ้ำกับรหัสผ่านเดิม")
    with get_db() as conn:
        row = conn.execute("SELECT * FROM recon_users WHERE id=?", (u["id"],)).fetchone()
        if not row or not A.verify_password(body.old, row["pw_hash"]):
            raise HTTPException(400, "รหัสผ่านเดิมไม่ถูกต้อง")
        A.set_password(conn, u["id"], body.new, must_change=False)
        _audit(conn, "recon_user", u["id"], "password_change", u["username"])
        conn.commit()
        row = conn.execute("SELECT * FROM recon_users WHERE id=?", (u["id"],)).fetchone()
    A.set_cookie(response, request, row)          # the old cookie died with the version bump
    return {"ok": True, "user": A.me_dict(dict(row))}


# ── users admin (managers) ────────────────────────────────────────────────────
def _manager(request: Request) -> dict:
    _need_secret()
    return A.require_role(request, "manager")


@router.get("/api/users")
def list_users(request: Request):
    _manager(request)
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM recon_users ORDER BY active DESC, "
                            "CASE role WHEN 'manager' THEN 0 WHEN 'accountant' THEN 1 ELSE 2 END, "
                            "username").fetchall()
        return [A.public(r) for r in rows]


class _NewUser(BaseModel):
    username: str
    display_name: str = ""
    role: str
    password: str


@router.post("/api/users")
def add_user(body: _NewUser, request: Request):
    me_ = _manager(request)
    problem = A.validate_new_user(body.username, body.display_name, body.role, body.password)
    if problem:
        raise HTTPException(400, problem)
    with get_db() as conn:
        if conn.execute("SELECT 1 FROM recon_users WHERE username=?", (body.username.strip(),)).fetchone():
            raise HTTPException(409, f"มีชื่อผู้ใช้ {body.username.strip()} อยู่แล้ว")
        uid = A.create_user(conn, body.username, body.display_name, body.role, body.password,
                            created_by=me_["username"], must_change=True)
        row = conn.execute("SELECT * FROM recon_users WHERE id=?", (uid,)).fetchone()
        _audit(conn, "recon_user", uid, "create", me_["username"], [
            {"field": "username", "new": row["username"]},
            {"field": "display_name", "new": row["display_name"]},
            {"field": "role", "new": row["role"]}])
        conn.commit()
        return A.public(row)


class _PatchUser(BaseModel):
    display_name: str | None = None
    role: str | None = None
    active: bool | None = None


@router.patch("/api/users/{user_id}")
def patch_user(user_id: int, body: _PatchUser, request: Request):
    me_ = _manager(request)
    if body.role is not None and body.role not in A.ROLES:
        raise HTTPException(400, "บทบาทต้องเป็น manager / accountant / viewer")
    if body.display_name is not None and len(body.display_name.strip()) > 80:
        raise HTTPException(400, "ชื่อที่แสดงยาวเกินไป")
    with get_db() as conn:
        conn.execute("BEGIN IMMEDIATE")                  # the last-manager check and the write as one
        old = conn.execute("SELECT * FROM recon_users WHERE id=?", (user_id,)).fetchone()
        if not old:
            raise HTTPException(404, "ไม่พบผู้ใช้นี้")
        new = {"display_name": old["display_name"], "role": old["role"], "active": old["active"]}
        if body.display_name is not None:
            new["display_name"] = body.display_name.strip() or old["username"]
        if body.role is not None:
            new["role"] = body.role
        if body.active is not None:
            new["active"] = 1 if body.active else 0
        stays_manager = new["role"] == "manager" and new["active"]
        if (old["role"] == "manager" and old["active"] and not stays_manager
                and A.active_managers(conn, excluding=user_id) == 0):
            raise HTTPException(409, "ต้องมีผู้จัดการที่ใช้งานได้อย่างน้อย 1 คน — "
                                     "เพิ่มหรือเปิดใช้ผู้จัดการคนอื่นก่อน แล้วค่อยลดสิทธิ์/ปิดบัญชีนี้")
        diffs = [{"field": k, "old": old[k], "new": new[k]} for k in new if new[k] != old[k]]
        if not diffs:
            return A.public(old)
        # deactivating ends every session of the account at once (version bump)
        bump = 1 if (old["active"] and not new["active"]) else 0
        conn.execute("UPDATE recon_users SET display_name=?, role=?, active=?, pw_version=pw_version+?"
                     " WHERE id=?", (new["display_name"], new["role"], new["active"], bump, user_id))
        _audit(conn, "recon_user", user_id, "update", me_["username"],
               [{"field": "username", "new": old["username"]}] + diffs)
        conn.commit()
        return A.public(conn.execute("SELECT * FROM recon_users WHERE id=?", (user_id,)).fetchone())


class _ResetBody(BaseModel):
    password: str


@router.post("/api/users/{user_id}/reset-password")
def reset_password(user_id: int, body: _ResetBody, request: Request):
    me_ = _manager(request)
    problem = A.password_problem(body.password)
    if problem:
        raise HTTPException(400, problem)
    with get_db() as conn:
        row = conn.execute("SELECT * FROM recon_users WHERE id=?", (user_id,)).fetchone()
        if not row:
            raise HTTPException(404, "ไม่พบผู้ใช้นี้")
        A.set_password(conn, user_id, body.password, must_change=True)
        _audit(conn, "recon_user", user_id, "reset_password", me_["username"],
               [{"field": "username", "new": row["username"]}])
        conn.commit()
        return A.public(conn.execute("SELECT * FROM recon_users WHERE id=?", (user_id,)).fetchone())
