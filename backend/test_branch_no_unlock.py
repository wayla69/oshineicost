"""The branch login never needs the owner write-token (owner, 2026-10-08).

A branch phone is confined to its own branch and to the branch.stock routes by the role
rules, so the write-token added only a trip to each phone. Everyone else keeps it, and
skipping the token is not a role grant: central writes stay closed to the branch."""
import base64
import json

import pytest
from fastapi.testclient import TestClient

import auth
import core

TOKEN = "owner-token-xyz"


@pytest.fixture
def auth_on(monkeypatch):
    monkeypatch.setenv("OSHINEI_AUTH", "1")
    monkeypatch.setenv("OSHINEI_AUTH_SECRET", "test-secret-stable")
    monkeypatch.setenv("OSHINEI_SEED_TOKEN", TOKEN)
    yield


def _fresh():
    import main
    return TestClient(main.app)


def _login(c, user, pw=auth.DEFAULT_PASSWORD):
    return c.post("/api/auth/login", json={"username": user, "password": pw})


def _branch_client():
    mg = _fresh()
    assert _login(mg, "management1").status_code == 200
    r = mg.post("/api/auth/users", headers={"X-Upload-Token": TOKEN},
                json={"username": "brn_tok", "password": "Temp@12345", "role": "branch", "branch": "BranchA"})
    assert r.status_code in (200, 409), r.text          # 409 = created by an earlier test in this module
    b = _fresh()
    assert _login(b, "brn_tok", "Temp@12345").status_code == 200
    return b


def test_branch_session_passes_the_write_gate_without_a_token(client, app_db, auth_on):
    b = _branch_client()
    r = b.post("/api/bom-center/stock/count", json={"lines": [], "submit": False})
    assert r.headers.get("X-Denied") != "token", r.text           # the gate let it through (a data error may follow)
    chk = b.post("/api/auth/token-check")
    assert chk.status_code == 200 and chk.json()["gated"] is False   # so the page shows no unlock chip


def test_other_roles_still_need_the_token(client, app_db, auth_on):
    mg = _fresh()
    assert _login(mg, "management1").status_code == 200
    r = mg.post("/api/bom-center/stock/count", json={"lines": [], "submit": False})
    assert r.status_code == 403 and r.headers.get("X-Denied") == "token"
    assert mg.post("/api/auth/token-check").status_code == 403
    ok = mg.post("/api/auth/token-check", headers={"X-Upload-Token": TOKEN})
    assert ok.status_code == 200 and ok.json()["gated"] is True


def test_branch_cannot_use_the_missing_token_to_reach_central_writes(client, app_db, auth_on):
    b = _branch_client()
    for path in ("/api/bom-center/hub/process/1", "/api/bom-center/order-calendar",
                 "/api/bom-center/release-act/release", "/api/cost"):
        r = b.post(path, json={})
        assert r.status_code == 403 and r.headers.get("X-Denied") == "role", (path, r.status_code, r.text)


def test_a_forged_branch_cookie_does_not_pass_the_gate(client, app_db, auth_on):
    body = base64.urlsafe_b64encode(json.dumps({"uid": 1, "u": "x", "r": "branch", "b": "BranchA", "exp": 9999999999}).encode()).decode().rstrip("=")
    fake = type("R", (), {"cookies": {auth.SESSION_COOKIE: f"{body}.forged"}})()
    assert core.is_branch_session(fake) is False
    c = _fresh()
    c.cookies.set(auth.SESSION_COOKIE, f"{body}.forged")
    assert c.post("/api/bom-center/stock/count", json={"lines": []}).status_code == 401


def test_auth_off_branch_logic_is_inert(client, monkeypatch):
    """Local dev (auth off): no session, so nothing is skipped by this rule."""
    monkeypatch.delenv("OSHINEI_AUTH", raising=False)
    fake = type("R", (), {"cookies": {}})()
    assert core.is_branch_session(fake) is False
