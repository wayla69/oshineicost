"""Recon's own login (recon/auth.py, routers/auth.py, main._recon_login_gate).

Owner decision 2026-10-06: recon has its own users, roles and session, separate
from the dashboard. Enforcement is OFF until RECON_LOGIN=1 — the first test pins
that nothing changes while it is off. Platform-neutral: env via monkeypatch, a
temp DB per test, no network, no real secrets (values are generated here).
"""
import os
import secrets
import sqlite3
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import recon.main as main  # noqa: E402
import recon.core as core  # noqa: E402
import recon.auth as A  # noqa: E402
from test_recon import _make_db, DEMO_POS  # noqa: E402
from test_fix_routers import _manual_setup, _manual_body  # noqa: E402
from approval_helpers import make_approvable  # noqa: E402

PM = "2026-06"
BOSS_PW = "Bx-" + secrets.token_hex(6)          # generated per run — test-only values
NEW_PW = "Nw-" + secrets.token_hex(6)
_ENV = ("RECON_LOGIN", "RECON_SESSION_SECRET", "RECON_ADMIN_USER", "RECON_ADMIN_PASSWORD",
        "OSHINEI_SEED_TOKEN", "OSHINEI_MANAGER_TOKEN", "RECON_ENFORCE_AUTH")


def _q(sql, *a):
    k = sqlite3.connect(main._DB_PATH)
    k.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in k.execute(sql, a)]
    finally:
        k.close()


def _x(sql, *a):
    k = sqlite3.connect(main._DB_PATH)
    try:
        k.execute(sql, a); k.commit()
    finally:
        k.close()


@pytest.fixture
def env(tmp_path, monkeypatch):
    core._DB_PATH = main._DB_PATH = _make_db(tmp_path, DEMO_POS)
    for k in _ENV:
        monkeypatch.delenv(k, raising=False)
    return monkeypatch


@pytest.fixture
def on(env):
    """Login ON, a secret, and the bootstrap manager 'boss' (must change password)."""
    env.setenv("RECON_LOGIN", "1")
    env.setenv("RECON_SESSION_SECRET", secrets.token_urlsafe(32))
    env.setenv("RECON_ADMIN_USER", "boss")
    env.setenv("RECON_ADMIN_PASSWORD", BOSS_PW)
    with TestClient(main.app) as cl:               # startup → schema + bootstrap
        yield cl


def _login(cl, user, pw):
    return cl.post("/api/auth/login", json={"username": user, "password": pw})


def _boss(on):
    """'on' logged in as boss, the forced password change done."""
    assert _login(on, "boss", BOSS_PW).status_code == 200
    r = on.post("/api/auth/change-password", json={"old": BOSS_PW, "new": NEW_PW})
    assert r.status_code == 200, r.text
    return on


def _add(mgr, username, role):
    pw = "Tmp-" + secrets.token_hex(5)
    r = mgr.post("/api/users", json={"username": username, "display_name": username.title(),
                                     "role": role, "password": pw})
    assert r.status_code == 200, r.text
    return r.json()["id"], pw


def _as(username, pw):
    """A second browser: its own cookie jar, logged in, first-login change done."""
    cl = TestClient(main.app)
    assert _login(cl, username, pw).status_code == 200
    assert cl.post("/api/auth/change-password", json={"old": pw, "new": NEW_PW}).status_code == 200
    return cl


# ── flag off: today's behaviour, untouched ────────────────────────────────────
def test_flag_off_keeps_todays_behaviour(env):
    env.setenv("RECON_SESSION_SECRET", secrets.token_urlsafe(32))
    with TestClient(main.app) as cl:
        me = cl.get("/api/me").json()
        assert me == {"login_enabled": False, "login_ready": True, "user": None}
        # open locally (no tokens set): writes work with no login at all
        assert cl.post("/api/sessions", json={"branch_id": 4, "period_month": PM}).status_code == 200
        assert cl.get("/api/branches").status_code == 200
        # write token set → still the token, and a recon cookie does NOT stand in for it
        env.setenv("OSHINEI_SEED_TOKEN", "tok-x")
        c = sqlite3.connect(main._DB_PATH)
        A.create_user(c, "amy", "Amy", "manager", "Amy-pass-123", "test", must_change=False)
        c.commit(); c.close()
        assert _login(cl, "amy", "Amy-pass-123").status_code == 200   # login works before enforcement
        assert cl.get("/api/me").json()["user"]["username"] == "amy"
        assert cl.post("/api/sessions", json={"branch_id": 5, "period_month": PM}).status_code == 403
        assert cl.post("/api/sessions", json={"branch_id": 5, "period_month": PM, "created_by": "prep"},
                       headers={"X-Upload-Token": "tok-x"}).status_code == 200
        # the shared manager code still gates approval (not the recon role)
        env.setenv("OSHINEI_MANAGER_TOKEN", "mgr-x")
        sid = _q("SELECT session_id FROM recon_sessions WHERE branch_id=5")[0]["session_id"]
        make_approvable(main._DB_PATH, sid)
        h = {"X-Upload-Token": "tok-x"}
        assert cl.patch(f"/api/sessions/{sid}/status", data={"status": "approved", "approved_by": "zed"},
                        headers=h).status_code == 403
        assert cl.patch(f"/api/sessions/{sid}/status", data={"status": "approved", "approved_by": "zed"},
                        headers={**h, "X-Manager-Token": "mgr-x"}).status_code == 200


def test_flag_off_enforce_auth_path_still_works_and_spares_login(env):
    env.setenv("RECON_ENFORCE_AUTH", "1")
    env.setenv("RECON_SESSION_SECRET", secrets.token_urlsafe(32))
    with TestClient(main.app) as cl:
        if core._parse_session is not None:          # dashboard auth importable → the old 401
            assert cl.post("/api/sessions", json={"branch_id": 4, "period_month": PM}).status_code == 401
        # the recon login endpoint is NOT behind the dashboard gate (generic 401 = wrong creds)
        r = _login(cl, "nobody", "whatever-123")
        assert r.status_code == 401 and r.json()["detail"] == A.MSG_LOGIN_FAIL


# ── flag on, misconfigured → closed ───────────────────────────────────────────
def test_flag_on_without_secret_fails_closed(env):
    env.setenv("RECON_LOGIN", "1")
    with TestClient(main.app) as cl:
        for url in ("/api/branches", "/api/sessions", f"/api/v2/overview?month={PM}", "/api/users"):
            r = cl.get(url)
            assert r.status_code == 503 and "RECON_SESSION_SECRET" in r.json()["detail"], url
        assert cl.post("/api/sessions", json={"branch_id": 4, "period_month": PM}).status_code == 503
        assert _login(cl, "boss", "x" * 10).status_code == 503
        assert cl.get("/api/me").json() == {"login_enabled": True, "login_ready": False, "user": None}
        assert cl.get("/health").status_code == 200
        assert cl.get("/app/").status_code == 200


# ── login / me / logout / forced change ───────────────────────────────────────
def test_login_me_logout_and_forced_password_change(on):
    assert on.get("/api/branches").status_code == 401
    assert on.get("/app/").status_code == 200 and on.get("/health").status_code == 200
    r = _login(on, "BOSS", BOSS_PW)                  # username is case-insensitive
    assert r.status_code == 200 and r.json()["user"]["must_change_pw"] is True
    ck = r.headers.get("set-cookie", "")
    assert A.COOKIE in ck and "httponly" in ck.lower() and "samesite=lax" in ck.lower()
    assert "oshinei_session" not in ck
    assert on.get("/api/branches").status_code == 403                 # must change first
    assert on.post("/api/auth/change-password", json={"old": "wrong-old-pw", "new": NEW_PW}).status_code == 400
    assert on.post("/api/auth/change-password", json={"old": BOSS_PW, "new": "short"}).status_code == 400
    assert on.post("/api/auth/change-password", json={"old": BOSS_PW, "new": NEW_PW}).status_code == 200
    assert on.get("/api/branches").status_code == 200
    me = on.get("/api/me").json()
    assert me["login_enabled"] and me["user"]["username"] == "boss" and me["user"]["role"] == "manager"
    assert me["user"]["must_change_pw"] is False
    assert on.post("/api/auth/logout").status_code == 200
    assert on.get("/api/branches").status_code == 401
    assert on.get("/api/me").json()["user"] is None
    assert _login(on, "boss", BOSS_PW).status_code == 401              # the old password is gone
    assert _login(on, "boss", NEW_PW).status_code == 200


def test_dashboard_cookie_does_not_count_when_login_on(on):
    on.cookies.set("oshinei_session", "anything.at.all")
    assert on.get("/api/branches").status_code == 401


# ── wrong password, lockout ───────────────────────────────────────────────────
def test_wrong_password_is_generic_and_locks_after_five(on):
    _boss(on); on.post("/api/auth/logout")
    unknown = _login(on, "ghost", "whatever-123")
    assert unknown.status_code == 401 and unknown.json()["detail"] == A.MSG_LOGIN_FAIL
    for _ in range(A.MAX_FAILS):
        r = _login(on, "boss", "not-the-password")
        assert r.status_code == 401 and r.json()["detail"] == A.MSG_LOGIN_FAIL
    locked = _login(on, "boss", NEW_PW)                                # right password, but locked
    assert locked.status_code == 401 and locked.json()["detail"] == A.MSG_LOGIN_FAIL
    row = _q("SELECT locked_until FROM recon_users WHERE username='boss'")[0]
    assert row["locked_until"]
    reasons = [r["new_val"] for r in _q("SELECT new_val FROM recon_audit_log WHERE entity='recon_auth' "
                                        "AND action='login_fail' AND field='reason'")]
    assert reasons.count("bad_password") == 4 and "bad_password_locked" in reasons
    assert "locked" in reasons and "unknown_user" in reasons
    _x("UPDATE recon_users SET locked_until='2000-01-01T00:00:00' WHERE username='boss'")   # 15 min later
    assert _login(on, "boss", NEW_PW).status_code == 200


# ── cookie integrity ──────────────────────────────────────────────────────────
def _set(cl, value):
    cl.cookies.clear()
    cl.cookies.set(A.COOKIE, value)


def test_tampered_foreign_and_expired_cookies_are_rejected(on, monkeypatch):
    _boss(on)
    good = on.cookies.get(A.COOKIE)
    assert on.get("/api/branches").status_code == 200
    body, _, sig = good.rpartition(".")
    p = A.parse_cookie(good)
    import base64, json
    forged = base64.urlsafe_b64encode(json.dumps({**p, "uid": p["uid"], "role": "manager",
                                                  "exp": p["exp"] + 999999}).encode()).decode().rstrip("=")
    for bad in (f"{forged}.{sig}", f"{body}.{'0' * 64}", body, "x.y"):
        _set(on, bad)
        assert on.get("/api/branches").status_code == 401, bad
    row = _q("SELECT id, role, pw_version FROM recon_users WHERE username='boss'")[0]
    _set(on, A.make_cookie(row["id"], row["role"], row["pw_version"],
                           now=time.time() - A.SESSION_TTL - 5))
    assert on.get("/api/branches").status_code == 401                  # expired
    _set(on, A.make_cookie(row["id"], row["role"], row["pw_version"]))
    assert on.get("/api/branches").status_code == 200                  # same secret, fresh: fine
    monkeypatch.setenv("RECON_SESSION_SECRET", secrets.token_urlsafe(32))   # rotated secret
    assert on.get("/api/branches").status_code == 401


def test_password_reset_and_deactivation_end_old_sessions(on):
    mgr = _boss(on)
    aid, pw = _add(mgr, "acc1", "accountant")
    acc = _as("acc1", pw)
    assert acc.get("/api/branches").status_code == 200
    assert mgr.post(f"/api/users/{aid}/reset-password", json={"password": "Reset-pass-1"}).status_code == 200
    assert acc.get("/api/branches").status_code == 401                 # the old cookie is dead
    acc = _as("acc1", "Reset-pass-1")
    assert acc.get("/api/branches").status_code == 200
    assert mgr.patch(f"/api/users/{aid}", json={"active": False}).status_code == 200
    assert acc.get("/api/branches").status_code == 401
    assert _login(TestClient(main.app), "acc1", NEW_PW).status_code == 401


# ── roles ─────────────────────────────────────────────────────────────────────
def test_viewer_reads_but_cannot_write(on):
    mgr = _boss(on)
    _, pw = _add(mgr, "vee", "viewer")
    v = _as("vee", pw)                                                 # own password change is allowed
    assert v.get("/api/branches").status_code == 200
    assert v.get(f"/api/v2/overview?month={PM}").status_code == 200
    r = v.post("/api/sessions", json={"branch_id": 4, "period_month": PM})
    assert r.status_code == 403 and r.json()["detail"] == A.MSG_VIEWER
    assert v.delete("/api/manual/1").status_code == 403
    assert v.get("/api/users").status_code == 403
    assert v.post("/api/auth/logout").status_code == 200


def test_accountant_works_but_cannot_approve_or_admin(on):
    mgr = _boss(on)
    _, pw = _add(mgr, "acc1", "accountant")
    acc = _as("acc1", pw)
    lid = _manual_setup(acc)                                           # payment-map PATCH + data
    r = acc.post("/api/manual", json=_manual_body([{"table": "recon_bank_lines", "id": lid, "amount": 100}],
                                                 matched_by="someone-else"))
    assert r.status_code == 200, r.text
    assert _q("SELECT matched_by FROM recon_manual_match")[0]["matched_by"] == "acc1"   # body ignored
    sid = acc.post("/api/sessions", json={"branch_id": 4, "period_month": PM,
                                          "created_by": "boss"}).json()["session_id"]
    assert _q("SELECT created_by FROM recon_sessions WHERE session_id=?", sid)[0]["created_by"] == "acc1"
    make_approvable(main._DB_PATH, sid)
    assert acc.patch(f"/api/sessions/{sid}/status", data={"status": "in_review"}).status_code == 200
    r = acc.patch(f"/api/sessions/{sid}/status", data={"status": "approved"})
    assert r.status_code == 403 and r.json()["detail"] == A.MSG_MANAGER_ONLY
    assert acc.post("/api/method-config", json={"category": "QR"}).status_code == 403
    assert acc.post("/api/setup/seed-requirement").status_code == 403
    assert acc.get("/api/users").status_code == 403
    assert acc.post("/api/users", json={"username": "x1", "role": "manager",
                                        "password": "Whatever-123"}).status_code == 403
    # manager approves the accountant's month (own password = step-up); the approver is the session user
    r = mgr.patch(f"/api/sessions/{sid}/status", data={"status": "approved", "approved_by": "acc1",
                                                       "password": NEW_PW})
    assert r.status_code == 200, r.text
    assert _q("SELECT approved_by, status FROM recon_sessions WHERE session_id=?", sid)[0] == \
        {"approved_by": "boss", "status": "approved"}
    # reopen = manager only, reason still required (+ the manager's own password)
    assert acc.patch(f"/api/sessions/{sid}/status", data={"status": "draft", "password": NEW_PW,
                     "reason": "found a missing statement"}).status_code == 403
    assert mgr.patch(f"/api/sessions/{sid}/status", data={"status": "draft", "password": NEW_PW,
                     "reason": "found a missing statement"}).status_code == 200


def test_sod_with_two_real_users(on):
    mgr = _boss(on)
    _, pw = _add(mgr, "boss2", "manager")
    m2 = _as("boss2", pw)
    sid = mgr.post("/api/sessions", json={"branch_id": 4, "period_month": PM}).json()["session_id"]
    assert _q("SELECT created_by FROM recon_sessions WHERE session_id=?", sid)[0]["created_by"] == "boss"
    make_approvable(main._DB_PATH, sid)
    own = mgr.patch(f"/api/sessions/{sid}/status", data={"status": "approved", "approved_by": "boss2"})
    assert own.status_code == 409 and "SoD" in own.json()["detail"]     # the body can't fake another approver
    assert m2.patch(f"/api/sessions/{sid}/status", data={"status": "approved",
                                                         "password": NEW_PW}).status_code == 200


# ── step-up: approving / closing / reopening re-asks the signed-in user's password ──
def _in_review(mgr):
    """An accountant's month sent for review; returns (accountant client, session id)."""
    _, pw = _add(mgr, "acc1", "accountant")
    acc = _as("acc1", pw)
    sid = acc.post("/api/sessions", json={"branch_id": 4, "period_month": PM}).json()["session_id"]
    make_approvable(main._DB_PATH, sid)
    assert acc.patch(f"/api/sessions/{sid}/status", data={"status": "in_review"}).status_code == 200
    return acc, sid


def _status(sid):
    return _q("SELECT status, approved_by FROM recon_sessions WHERE session_id=?", sid)[0]


def _fails(user="boss"):
    return _q("SELECT failed_count, locked_until FROM recon_users WHERE username=?", user)[0]


def test_stepup_approve_needs_the_managers_own_password(on):
    mgr = _boss(on)
    _, sid = _in_review(mgr)
    r = mgr.patch(f"/api/sessions/{sid}/status", data={"status": "approved"})
    assert r.status_code == 403 and r.json()["detail"] == A.MSG_STEPUP_NEED
    assert _status(sid)["status"] == "in_review" and _fails()["failed_count"] == 0   # blank: not counted
    r = mgr.patch(f"/api/sessions/{sid}/status", data={"status": "approved", "password": "not-my-password"})
    assert r.status_code == 403 and r.json()["detail"] == A.MSG_STEPUP_FAIL
    assert "เปลี่ยนรหัสผ่าน" not in r.json()["detail"]       # the UI would open the change-password screen
    assert _status(sid)["status"] == "in_review" and _fails()["failed_count"] == 1
    log = _q("SELECT field, new_val, by FROM recon_audit_log WHERE entity='recon_auth' AND action='stepup_fail'")
    assert {"field": "reason", "new_val": "bad_password", "by": "boss"} in log
    assert {"field": "session_id", "new_val": str(sid), "by": "boss"} in log
    r = mgr.patch(f"/api/sessions/{sid}/status", data={"status": "approved", "password": NEW_PW})
    assert r.status_code == 200, r.text
    assert _status(sid) == {"status": "approved", "approved_by": "boss"}
    assert _fails()["failed_count"] == 0                        # a good step-up clears the count
    # 'closed' (the archive step) is a sign-off too
    assert mgr.patch(f"/api/sessions/{sid}/status", data={"status": "closed"}).status_code == 403
    assert mgr.patch(f"/api/sessions/{sid}/status",
                     data={"status": "closed", "password": NEW_PW}).status_code == 200


def test_stepup_reopen_and_the_ordinary_moves(on):
    mgr = _boss(on)
    acc, sid = _in_review(mgr)
    # send back for fixes / send for review: no password
    assert mgr.patch(f"/api/sessions/{sid}/status", data={"status": "draft"}).status_code == 200
    assert acc.patch(f"/api/sessions/{sid}/status", data={"status": "in_review"}).status_code == 200
    assert mgr.patch(f"/api/sessions/{sid}/status",
                     data={"status": "approved", "password": NEW_PW}).status_code == 200
    why = "พบสเตทเมนต์ KBANK วันที่ 30 อัปไม่ครบ"
    r = mgr.patch(f"/api/sessions/{sid}/status", data={"status": "draft", "reason": why})
    assert r.status_code == 403 and r.json()["detail"] == A.MSG_STEPUP_NEED
    r = mgr.patch(f"/api/sessions/{sid}/status", data={"status": "draft", "reason": "short",
                                                       "password": "wrong-password-1"})
    assert r.status_code == 400 and _fails()["failed_count"] == 0   # reason checked before the password
    r = mgr.patch(f"/api/sessions/{sid}/status", data={"status": "draft", "reason": why,
                                                       "password": "wrong-password-1"})
    assert r.status_code == 403 and _status(sid)["status"] == "approved"
    r = mgr.patch(f"/api/sessions/{sid}/status", data={"status": "draft", "reason": why, "password": NEW_PW})
    assert r.status_code == 200 and _status(sid)["status"] == "draft"
    assert _q("SELECT new_val FROM recon_audit_log WHERE entity='session' AND field='reopen_reason'")[0] \
        == {"new_val": why}


def test_stepup_sod_refusal_burns_no_password_try(on):
    mgr = _boss(on)
    sid = mgr.post("/api/sessions", json={"branch_id": 4, "period_month": PM}).json()["session_id"]
    make_approvable(main._DB_PATH, sid)
    r = mgr.patch(f"/api/sessions/{sid}/status", data={"status": "approved", "password": "wrong-password-1"})
    assert r.status_code == 409 and "SoD" in r.json()["detail"]
    assert _fails()["failed_count"] == 0


def test_stepup_failures_share_the_login_lockout(on):
    mgr = _boss(on)
    _, sid = _in_review(mgr)
    for _ in range(A.MAX_FAILS - 1):                            # 4 wrong logins (another browser)…
        assert _login(TestClient(main.app), "boss", "bad-password-x").status_code == 401
    r = mgr.patch(f"/api/sessions/{sid}/status", data={"status": "approved", "password": "bad-password-y"})
    assert r.status_code == 403 and r.json()["detail"] == A.MSG_STEPUP_LOCKED   # …+1 step-up = locked
    assert _fails()["locked_until"]
    # locked: the right password is refused too, both at step-up and at the login
    r = mgr.patch(f"/api/sessions/{sid}/status", data={"status": "approved", "password": NEW_PW})
    assert r.status_code == 403 and r.json()["detail"] == A.MSG_STEPUP_LOCKED
    assert _login(TestClient(main.app), "boss", NEW_PW).status_code == 401
    assert _status(sid)["status"] == "in_review"
    reasons = [x["new_val"] for x in _q("SELECT new_val FROM recon_audit_log WHERE entity='recon_auth' "
                                         "AND action='stepup_fail' AND field='reason' ORDER BY id")]
    assert reasons == ["bad_password_locked", "locked"]
    _x("UPDATE recon_users SET locked_until='2000-01-01T00:00:00' WHERE username='boss'")   # 15 min later
    assert mgr.patch(f"/api/sessions/{sid}/status",
                     data={"status": "approved", "password": NEW_PW}).status_code == 200


def test_stepup_login_off_keeps_the_manager_code(env):
    """Login OFF: no password is asked or checked — the manager code is the gate."""
    env.setenv("OSHINEI_MANAGER_TOKEN", "mgr-x")
    with TestClient(main.app) as cl:
        sid = cl.post("/api/sessions", json={"branch_id": 4, "period_month": PM,
                                             "created_by": "prep"}).json()["session_id"]
        make_approvable(main._DB_PATH, sid)
        f = {"status": "approved", "approved_by": "zed", "password": "anything-at-all"}
        assert cl.patch(f"/api/sessions/{sid}/status", data=f).status_code == 403
        assert cl.patch(f"/api/sessions/{sid}/status", data={"status": "approved", "approved_by": "zed"},
                        headers={"X-Manager-Token": "mgr-x"}).status_code == 200
        r = cl.patch(f"/api/sessions/{sid}/status", data={"status": "draft", "reason": "ทดสอบเปิดงวดใหม่อีกครั้ง"},
                     headers={"X-Manager-Token": "mgr-x"})
        assert r.status_code == 200
        assert not _q("SELECT * FROM recon_audit_log WHERE action='stepup_fail'")


# ── the bulk uploader keeps working ───────────────────────────────────────────
def test_write_token_still_works_for_machine_writes(on, monkeypatch):
    monkeypatch.setenv("OSHINEI_SEED_TOKEN", "bulk:tok-123")
    m = TestClient(main.app)                                           # no cookie at all
    h = {"X-Upload-Token": "tok-123"}
    assert m.post("/api/sessions", json={"branch_id": 4, "period_month": PM}).status_code == 401
    # 2026-10 audit: the token alone opens the UPLOAD endpoints only — not sessions,
    # matching, deposit slips or config (those need a recon session)
    r = m.post("/api/sessions", json={"branch_id": 4, "period_month": PM}, headers=h)
    assert r.status_code == 401
    assert m.post("/api/deposit-slips", headers=h,
                  json={"branch_id": 4, "sale_day": "2026-06-10", "amount": 100}).status_code == 401
    assert m.post("/api/manual", headers=h, json={}).status_code == 401
    assert not _q("SELECT * FROM recon_sessions")
    up = m.post("/api/uploads/bank/confirm", headers=h,
                files={"file": ("x.csv", b"not,a,statement\n", "text/csv")},
                data={"branch_id": "4", "period_month": PM, "provider": "kbank"})
    assert up.status_code not in (401, 403, 503), up.text             # past the gate (parser decides)
    assert m.post("/api/uploads/bank/confirm", headers={"X-Upload-Token": "wrong"},
                  files={"file": ("x.csv", b"a\n", "text/csv")},
                  data={"branch_id": "4", "period_month": PM}).status_code == 401
    _x("INSERT INTO recon_sessions(branch_id, period_month, status) VALUES (4, ?, 'draft')", PM)
    sid = _q("SELECT session_id FROM recon_sessions")[0]["session_id"]
    assert m.patch(f"/api/sessions/{sid}/status", data={"status": "approved"}, headers=h).status_code == 401
    assert m.delete("/api/uploads/1", headers=h).status_code == 401    # upload POSTs only
    assert m.get("/api/branches", headers=h).status_code == 401        # the token is for writes only
    assert m.get("/api/users", headers=h).status_code == 401
    # a logged-in accountant needs no token even though one is configured
    mgr = _boss(on)
    _, pw = _add(mgr, "acc1", "accountant")
    assert _as("acc1", pw).post("/api/sessions", json={"branch_id": 5, "period_month": PM}).status_code == 200


# ── users admin ───────────────────────────────────────────────────────────────
def test_last_manager_guard(on):
    mgr = _boss(on)
    bid = _q("SELECT id FROM recon_users WHERE username='boss'")[0]["id"]
    for body in ({"role": "accountant"}, {"active": False}):
        r = mgr.patch(f"/api/users/{bid}", json=body)
        assert r.status_code == 409 and "ผู้จัดการ" in r.json()["detail"], body
    nid, _ = _add(mgr, "boss2", "manager")
    assert mgr.patch(f"/api/users/{nid}", json={"active": False}).status_code == 200
    assert mgr.patch(f"/api/users/{bid}", json={"role": "viewer"}).status_code == 409   # boss2 inactive
    assert mgr.patch(f"/api/users/{nid}", json={"active": True}).status_code == 200
    assert mgr.patch(f"/api/users/{bid}", json={"role": "accountant"}).status_code == 200
    assert mgr.get("/api/users").status_code == 403                    # demoted: applies at once


def test_users_admin_validation_and_listing(on):
    mgr = _boss(on)
    assert mgr.post("/api/users", json={"username": "a b", "role": "viewer",
                                        "password": "Long-enough-1"}).status_code == 400
    assert mgr.post("/api/users", json={"username": "ok1", "role": "admin",
                                        "password": "Long-enough-1"}).status_code == 400
    assert mgr.post("/api/users", json={"username": "ok1", "role": "viewer",
                                        "password": "short"}).status_code == 400
    _add(mgr, "ok1", "viewer")
    assert mgr.post("/api/users", json={"username": "OK1", "role": "viewer",
                                        "password": "Long-enough-1"}).status_code == 409
    users = mgr.get("/api/users").json()
    assert {u["username"] for u in users} == {"boss", "ok1"}
    assert all("pw_hash" not in u for u in users)
    ok1 = next(u for u in users if u["username"] == "ok1")
    assert ok1["must_change_pw"] == 1 and ok1["role_th"] == "ดูอย่างเดียว"
    stored = _q("SELECT pw_hash FROM recon_users WHERE username='ok1'")[0]["pw_hash"]
    assert stored.startswith(("scrypt$", "pbkdf2_sha256$")) and "Long" not in stored


def test_bootstrap_admin_from_env_only_once(on, monkeypatch):
    rows = _q("SELECT username, role, must_change_pw, created_by FROM recon_users")
    assert rows == [{"username": "boss", "role": "manager", "must_change_pw": 1, "created_by": "bootstrap"}]
    assert _q("SELECT * FROM recon_audit_log WHERE entity='recon_user' AND action='bootstrap'")
    monkeypatch.setenv("RECON_ADMIN_USER", "other")
    with TestClient(main.app):                                         # second startup: table not empty
        pass
    assert [r["username"] for r in _q("SELECT username FROM recon_users")] == ["boss"]


def test_bootstrap_rejects_a_short_password(env):
    env.setenv("RECON_ADMIN_USER", "boss")
    env.setenv("RECON_ADMIN_PASSWORD", "short")
    with TestClient(main.app):
        pass
    assert _q("SELECT * FROM recon_users") == []


def test_audit_rows_for_login_and_user_admin(on):
    mgr = _boss(on)
    _login(TestClient(main.app), "boss", "bad-password-x")
    uid, _ = _add(mgr, "vee", "viewer")
    mgr.patch(f"/api/users/{uid}", json={"role": "accountant", "display_name": "Vee V"})
    mgr.post(f"/api/users/{uid}/reset-password", json={"password": "Reset-pass-1"})
    log = _q("SELECT entity, entity_id, action, field, old_val, new_val, by FROM recon_audit_log "
             "WHERE entity IN ('recon_auth','recon_user')")
    acts = {(r["entity"], r["action"]) for r in log}
    assert {("recon_auth", "login"), ("recon_auth", "login_fail"), ("recon_user", "password_change"),
            ("recon_user", "create"), ("recon_user", "update"), ("recon_user", "reset_password")} <= acts
    upd = [r for r in log if r["action"] == "update" and r["field"] == "role"]
    assert upd and upd[0]["old_val"] == "viewer" and upd[0]["new_val"] == "accountant" and upd[0]["by"] == "boss"
    # never the dashboard's 'auth' entity (its adoption view counts those)
    assert not _q("SELECT * FROM recon_audit_log WHERE entity='auth'")


def test_recon_users_survive_the_dashboard_db_swap(on, tmp_path):
    """backend/core.py carries every recon_% table into a staged refresh
    (_PRESERVED_PREFIXES) — recon_users must ride along or a data refresh would
    log everyone out for good."""
    os.environ.setdefault("OSHINEI_NO_SWAP", "1")
    try:
        from backend.core import _carry_forward_and_verify, _preserved_counts
    except Exception as e:                                             # pragma: no cover
        pytest.skip(f"backend.core not importable here: {e!r}")
    _boss(on)
    inc = tmp_path / "incoming.db"
    c = sqlite3.connect(inc)
    c.execute("CREATE TABLE sales_transaction(id INTEGER PRIMARY KEY)"); c.commit(); c.close()
    safe, reason = _carry_forward_and_verify(str(main._DB_PATH), str(inc))
    assert safe, reason
    assert _preserved_counts(str(inc)).get("recon_users") == 1
    c = sqlite3.connect(inc)
    try:
        row = c.execute("SELECT username, pw_hash FROM recon_users").fetchone()
        ddl = c.execute("SELECT sql FROM sqlite_master WHERE name='recon_users'").fetchone()[0]
    finally:
        c.close()
    assert row[0] == "boss" and A.verify_password(NEW_PW, row[1])
    assert "COLLATE NOCASE" in ddl and "CHECK(role IN" in ddl


def test_create_user_cli(env, monkeypatch, capsys):
    from recon import create_user
    with TestClient(main.app):
        pass
    pws = iter(["Cli-pass-123", "Cli-pass-123"])
    monkeypatch.setattr(create_user.getpass, "getpass", lambda prompt="": next(pws))
    assert create_user.main(["mgr1", "--role", "manager", "--name", "ผู้จัดการ"]) == 0
    row = _q("SELECT username, display_name, role, must_change_pw, created_by FROM recon_users")[0]
    assert row == {"username": "mgr1", "display_name": "ผู้จัดการ", "role": "manager",
                   "must_change_pw": 1, "created_by": "cli"}
    with pytest.raises(SystemExit):
        create_user.main(["mgr1"])                                     # exists → use --reset
    _x("UPDATE recon_users SET active=0, locked_until='2999-01-01T00:00:00'")
    pws = iter(["Cli-pass-456", "Cli-pass-456"])
    assert create_user.main(["mgr1", "--reset"]) == 0
    r = _q("SELECT active, locked_until, pw_version FROM recon_users")[0]
    assert r["active"] == 1 and r["locked_until"] is None and r["pw_version"] == 2


# ── frontend wiring (the parallel screens rely on window.ME) ──────────────────
def test_frontend_loads_me_before_the_first_route():
    fe = Path(main.__file__).resolve().parent / "frontend"
    core_js = (fe / "js" / "00-core.js").read_text(encoding="utf-8")
    boot = (fe / "js" / "90-boot.js").read_text(encoding="utf-8")
    assert "var ME={login_enabled:false" in core_js and "window.ME=ME" in core_js
    assert "loadMe().then(()=>route())" in boot and "\nroute();" not in boot
    login = (fe / "js" / "05-login.js").read_text(encoding="utf-8")
    for name in ("function renderLogin(", "function renderChangePw(", "function doLogout(",
                 "function _authHdrHtml(", "function _applyRoleUi("):
        assert name in login, name
    users = (fe / "js" / "85-users.js").read_text(encoding="utf-8")
    assert "function usersCardHtml(" in users and "function loadUsersAdmin(" in users
    # the write-token key button survives only while the login is off
    assert "if(!ME.login_enabled)" in login and "setToken()" in login
