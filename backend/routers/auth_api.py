"""Login, sessions, users and role permissions. The auth LIBRARY (signed
cookies, role tables) is backend/auth.py; this is the HTTP surface.

Moved out of main.py in Phase 2b (2026-09) by the AST: these endpoints and
the helpers only they use, unchanged. URLs are unchanged.
"""
from fastapi import APIRouter
from core import get_db
from fastapi import HTTPException
from fastapi import Request
from fastapi import Response
from pydantic import BaseModel
from typing import Dict
from typing import List
from typing import Optional
import auth as _auth

router = APIRouter()


# ---------------------------------------------------------------------------
# Authentication endpoints  (see backend/auth.py for the role/module model)
# ---------------------------------------------------------------------------
class _LoginBody(BaseModel):
    username: str
    password: str


class _UserCreate(BaseModel):
    username: str
    password: Optional[str] = None        # blank → shared default, forced change at first login
    role: str
    branch: Optional[str] = None          # E4: confine this login to one branch


class _UserUpdate(BaseModel):
    role: Optional[str] = None
    password: Optional[str] = None
    reset_default: Optional[bool] = None  # True → back to the shared default, forced change
    active: Optional[bool] = None
    branch: Optional[str] = None          # "" clears the scope


def _set_session_cookie(response: Response, request: Request, token: str):
    response.set_cookie(
        _auth.SESSION_COOKIE, token,
        max_age=_auth.SESSION_TTL, httponly=True, samesite="lax",
        secure=(request.url.scheme == "https"), path="/",
    )


def _require_admin(request: Request):
    """Resolve the caller's session and assert the system.admin module. When auth
    is disabled (local dev) this is a no-op so the user-admin UI works offline."""
    if not _auth.auth_enabled():
        return None
    payload = _auth.parse_token(request.cookies.get(_auth.SESSION_COOKIE, ""))
    if not payload:
        raise HTTPException(status_code=401, detail="Authentication required.")
    if "system.admin" not in _auth.modules_for_role(payload.get("r", "")):
        raise HTTPException(status_code=403, detail="ต้องเป็น Management เท่านั้น.")
    return payload


class _PwChangeBody(BaseModel):
    current_password: str
    new_password: str


def _client_ip(request: Request) -> str:
    # Behind Fly's proxy the real client is the first X-Forwarded-For hop.
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "?"


def _log_auth_event(con, action: str, username: str, ip: str = "") -> None:
    """AUD-1/AUD-6: best-effort login-event log into the shared, append-only
    recon_audit_log. Never breaks auth if the table is briefly unavailable (it's
    owned by the recon app's schema; create-if-missing so the dashboard can log
    even before recon has run)."""
    try:
        con.execute(
            "CREATE TABLE IF NOT EXISTS recon_audit_log ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL DEFAULT (datetime('now')), "
            "entity TEXT NOT NULL, entity_id INTEGER NOT NULL, action TEXT NOT NULL, "
            "field TEXT, old_val TEXT, new_val TEXT, by TEXT NOT NULL DEFAULT 'staff')")
        con.execute(
            "INSERT INTO recon_audit_log(entity, entity_id, action, field, new_val, by) "
            "VALUES ('auth', 0, ?, 'ip', ?, ?)",
            (action, ip or "", (username or "").strip()))
        con.commit()
    except Exception as e:
        # never block a login on the audit row, but never lose the fact either
        print(f"[auth] audit log write failed for {action!r}: {e}")


@router.post("/api/auth/login", include_in_schema=False)
def auth_login(body: _LoginBody, request: Request, response: Response):
    ip = _client_ip(request)
    if _auth.login_throttled(ip, body.username):
        raise HTTPException(status_code=429,
            detail="พยายามเข้าสู่ระบบบ่อยเกินไป — รอสักครู่แล้วลองใหม่.")
    with get_db() as con:
        row = _auth.authenticate(con, body.username, body.password)
        if not row:
            _auth.record_login_failure(ip, body.username)
            _log_auth_event(con, "login_fail", body.username, ip)
            raise HTTPException(status_code=401, detail="ชื่อผู้ใช้หรือรหัสผ่านไม่ถูกต้อง.")
        _auth.clear_login_failures(ip, body.username)
        _log_auth_event(con, "login", row["username"], ip)
    branch = row["branch"] if "branch" in row.keys() else None
    token = _auth.make_token(row["id"], row["username"], row["role"], branch=branch)
    _set_session_cookie(response, request, token)
    return {"username": row["username"], "role": row["role"], "branch": branch,
            "must_change": bool(row["must_change"]),
            "modules": sorted(_auth.modules_for_role(row["role"]))}


@router.post("/api/auth/me/password", include_in_schema=False)
def auth_change_own_password(body: _PwChangeBody, request: Request):
    """Self-service password change for ANY logged-in user (verifies the current
    password). Bypasses _require_admin — it only ever affects the caller's own row."""
    if not _auth.auth_enabled():
        raise HTTPException(status_code=400, detail="auth disabled")
    payload = _auth.parse_token(request.cookies.get(_auth.SESSION_COOKIE, ""))
    if not payload:
        raise HTTPException(status_code=401, detail="Authentication required.")
    try:
        _auth.check_password_policy(body.new_password)          # AUD-1 (≥10, not the shared default)
        with get_db() as con:
            ok = _auth.change_own_password(con, payload.get("u", ""),
                                           body.current_password, body.new_password)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not ok:
        raise HTTPException(status_code=403, detail="รหัสผ่านปัจจุบันไม่ถูกต้อง.")
    with get_db() as con:
        _log_auth_event(con, "password_change", payload.get("u", ""), _client_ip(request))
    return {"ok": True}


@router.post("/api/auth/logout", include_in_schema=False)
def auth_logout(request: Request, response: Response):
    payload = _auth.parse_token(request.cookies.get(_auth.SESSION_COOKIE, ""))
    if payload:
        with get_db() as con:
            _log_auth_event(con, "logout", payload.get("u", ""), _client_ip(request))
    response.delete_cookie(_auth.SESSION_COOKIE, path="/")
    return {"ok": True}


@router.get("/api/auth/me", include_in_schema=False)
def auth_me(request: Request):
    """Frontend bootstrap. When auth is disabled, report enabled=False with the
    full module set so nothing is hidden (current behaviour preserved)."""
    if not _auth.auth_enabled():
        return {"enabled": False, "username": None, "role": "management",
                "modules": sorted(_auth.MODULES)}
    payload = _auth.parse_token(request.cookies.get(_auth.SESSION_COOKIE, ""))
    if not payload:
        return {"enabled": True, "authenticated": False, "modules": []}
    # Reflect LIVE account state, not the (up-to-12h-stale) cookie: a demoted or
    # deactivated user is reported logged-out immediately.
    with get_db() as con:
        row = _auth.get_user(con, payload.get("u", ""))
    if not row or not row["active"]:
        return {"enabled": True, "authenticated": False, "modules": []}
    return {"enabled": True, "authenticated": True,
            "username": row["username"], "role": row["role"],
            "branch": row["branch"] if "branch" in row.keys() else None,
            "must_change": bool(row["must_change"]),
            "modules": sorted(_auth.modules_for_role(row["role"]))}


# ── user administration (Management only — gated by middleware + _require_admin)
@router.get("/api/auth/users", include_in_schema=False)
def auth_users_list(request: Request):
    _require_admin(request)
    with get_db() as con:
        return _auth.list_users(con)


@router.post("/api/auth/users", include_in_schema=False)
def auth_users_create(body: _UserCreate, request: Request):
    _require_admin(request)
    try:
        with get_db() as con:
            if _auth.get_user(con, body.username):
                raise HTTPException(status_code=409, detail="มีชื่อผู้ใช้นี้แล้ว.")
            if body.password:
                _auth.create_user(con, body.username, body.password, body.role, branch=body.branch)
            else:
                # No password typed → the seeding path: shared default + must_change,
                # so the person sets their own on first login.
                _auth.provision_user(con, body.username, body.role, branch=body.branch)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "default_password": None if body.password else _auth.DEFAULT_PASSWORD}


@router.put("/api/auth/users/{username}", include_in_schema=False)
def auth_users_update(username: str, body: _UserUpdate, request: Request):
    payload = _require_admin(request)
    try:
        with get_db() as con:
            target = _auth.get_user(con, username)
            if not target:
                raise HTTPException(status_code=404, detail="ไม่พบผู้ใช้.")
            # Would this change strip an active admin of their admin access?
            new_role   = body.role if body.role is not None else target["role"]
            new_active = bool(target["active"]) if body.active is None else bool(body.active)
            was_admin  = _auth.is_admin_role(target["role"]) and bool(target["active"])
            will_admin = _auth.is_admin_role(new_role) and new_active
            if was_admin and not will_admin:
                # (a) never let an admin demote/disable THEMSELVES (one-click lockout)
                if payload and payload.get("u") == username:
                    raise HTTPException(status_code=400,
                        detail="เปลี่ยนบทบาท/ปิดบัญชีผู้ดูแลของตัวเองไม่ได้.")
                # (b) never remove the LAST active admin
                if _auth.count_active_admins(con) <= 1:
                    raise HTTPException(status_code=400,
                        detail="ต้องมีผู้ดูแล (Management) ที่ใช้งานได้อย่างน้อย 1 บัญชี.")
            if body.reset_default:
                _auth.provision_user(con, username, target["role"], reset_existing=True)
            _auth.update_user(con, username, role=body.role,
                              password=body.password, active=body.active,
                              branch=(body.branch if body.branch is not None else _auth.UNSET))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True}


@router.delete("/api/auth/users/{username}", include_in_schema=False)
def auth_users_delete(username: str, request: Request):
    payload = _require_admin(request)
    # Don't let an admin delete their own account out from under themselves.
    if payload and payload.get("u") == username:
        raise HTTPException(status_code=400, detail="ลบบัญชีตัวเองไม่ได้.")
    with get_db() as con:
        target = _auth.get_user(con, username)
        if not target:
            raise HTTPException(status_code=404, detail="ไม่พบผู้ใช้.")
        # Never delete the last active admin (would lock out user management).
        if _auth.is_admin_role(target["role"]) and bool(target["active"]) \
                and _auth.count_active_admins(con) <= 1:
            raise HTTPException(status_code=400,
                detail="ต้องมีผู้ดูแล (Management) ที่ใช้งานได้อย่างน้อย 1 บัญชี.")
        _auth.delete_user(con, username)
    return {"ok": True}


class RolePermissionsBody(BaseModel):
    permissions: Dict[str, List[str]]


@router.get("/api/auth/role-permissions", include_in_schema=False)
def auth_role_permissions_get(request: Request):
    _require_admin(request)
    with get_db() as con:
        return {
            "roles": _auth.ROLES,
            "modules": _auth.MODULES,
            "module_labels": {m: m for m in _auth.MODULES},  # frontend adds labels
            "permissions": _auth.get_role_permissions(con),
        }


@router.put("/api/auth/role-permissions", include_in_schema=False)
def auth_role_permissions_put(request: Request, body: RolePermissionsBody):
    _require_admin(request)
    with get_db() as con:
        _auth.set_role_permissions(con, body.permissions)
    return {"ok": True}
