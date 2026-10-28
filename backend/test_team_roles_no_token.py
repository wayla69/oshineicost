"""The warehouse, kitchen (innovation) and analyst logins save without the owner
write-token, like the branch (owner, 2026-10-28). Everyone else keeps it, and skipping
the token is not a role grant: a write outside the role stays closed."""
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


def _client_for(role):
    mg = _fresh()
    assert _login(mg, "management1").status_code == 200
    user = f"tf_{role}"
    r = mg.post("/api/auth/users", headers={"X-Upload-Token": TOKEN},
                json={"username": user, "password": "Temp@12345", "role": role})
    assert r.status_code in (200, 409), r.text          # 409 = created by an earlier test in this module
    c = _fresh()
    assert _login(c, user, "Temp@12345").status_code == 200
    return c


@pytest.mark.parametrize("role", ["warehouse", "innovation", "analyst"])
def test_team_role_passes_the_write_gate_without_a_token(client, app_db, auth_on, role):
    c = _client_for(role)
    chk = c.post("/api/auth/token-check")
    assert chk.status_code == 200 and chk.json()["gated"] is False   # no ปลดล็อกการบันทึก chip
    me = c.get("/api/auth/me").json()
    assert me["write_gated"] is False


@pytest.mark.parametrize("role", ["accounting", "marketing", "operation"])   # franchise: test_franchise_role
def test_other_roles_still_need_the_token(client, app_db, auth_on, role):
    c = _client_for(role)
    assert c.post("/api/auth/token-check").status_code == 403
    assert c.get("/api/auth/me").json()["write_gated"] is True


def test_management_still_needs_the_token(client, app_db, auth_on):
    mg = _fresh()
    assert _login(mg, "management1").status_code == 200
    r = mg.post("/api/auth/token-check")
    assert r.status_code == 403 and r.headers.get("X-Denied") == "token"


def test_skipping_the_token_is_not_a_role_grant(client, app_db, auth_on):
    c = _client_for("warehouse")
    r = c.post("/api/auth/users", json={"username": "x_wh", "password": "Temp@12345", "role": "management"})
    assert r.status_code == 403 and r.headers.get("X-Denied") != "token", r.text


def test_auth_off_is_inert(client, monkeypatch):
    monkeypatch.delenv("OSHINEI_AUTH", raising=False)
    fake = type("R", (), {"cookies": {}})()
    assert core.is_token_free_session(fake) is False
