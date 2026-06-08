"""Auth tests: login/session, the role→module enforcement matrix, and the
admin-only user API. The `client` fixture (conftest) seeds the 12 default users
at startup; we spin fresh TestClients for cookie isolation per test.

Enforcement is env-gated, so most tests run under the `auth_on` fixture which
sets OSHINEI_AUTH=1 (and a stable secret). With it unset the app is wide-open —
that path is covered by `test_me_reports_disabled` and the rest of the suite.
"""
import pytest
from fastapi.testclient import TestClient

import auth

DEFAULT_PW = auth.DEFAULT_PASSWORD


@pytest.fixture
def auth_on(monkeypatch):
    monkeypatch.setenv("OSHINEI_AUTH", "1")
    monkeypatch.setenv("OSHINEI_AUTH_SECRET", "test-secret-stable")
    yield


def _fresh(app_db):
    """A TestClient with its own cookie jar (app already seeded by `client`)."""
    import main
    return TestClient(main.app)


def _login(c, username, password=DEFAULT_PW):
    return c.post("/api/auth/login", json={"username": username, "password": password})


# ── auth disabled (default) ─────────────────────────────────────────────────
def test_me_reports_disabled(client):
    j = client.get("/api/auth/me").json()
    assert j["enabled"] is False
    assert "cost.analysis" in j["modules"]      # nothing hidden when auth is off


# ── login / session ─────────────────────────────────────────────────────────
def test_login_success_and_me(client, app_db, auth_on):
    c = _fresh(app_db)
    r = _login(c, "management1")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["role"] == "management" and body["username"] == "management1"
    me = c.get("/api/auth/me").json()
    assert me["enabled"] and me["authenticated"] and me["role"] == "management"


def test_login_bad_password(client, app_db, auth_on):
    c = _fresh(app_db)
    assert _login(c, "management1", "nope").status_code == 401


def test_missing_cookie_is_401(client, app_db, auth_on):
    c = _fresh(app_db)
    assert c.get("/api/sales/monthly").status_code == 401


def test_forged_cookie_is_401(client, app_db, auth_on):
    c = _fresh(app_db)
    c.cookies.set("oshinei_session", "tampered.signature")
    assert c.get("/api/sales/monthly").status_code == 401


def test_logout_clears_session(client, app_db, auth_on):
    c = _fresh(app_db)
    _login(c, "management1")
    assert c.get("/api/sales/monthly").status_code == 200
    c.post("/api/auth/logout")
    assert c.get("/api/sales/monthly").status_code == 401


# ── role → module enforcement ───────────────────────────────────────────────
def test_marketing_sales_yes_cost_no(client, app_db, auth_on):
    c = _fresh(app_db); _login(c, "marketing1")
    assert c.get("/api/sales/monthly").status_code == 200          # sales.overview
    assert c.get("/api/cost/analysis").status_code == 403          # cost.analysis denied
    assert c.get("/api/pnl/monthly").status_code == 403            # profit.pnl denied
    assert c.get("/api/analysis/discounts").status_code == 403     # ops.discounts denied


def test_operation_discounts_cash_yes_cost_no(client, app_db, auth_on):
    c = _fresh(app_db); _login(c, "operation1")
    assert c.get("/api/analysis/discounts").status_code == 200     # ops.discounts
    assert c.get("/api/extra/cash").status_code != 403             # ops.cash (allowed)
    assert c.get("/api/cost/analysis").status_code == 403          # no cost.analysis
    assert c.get("/api/pnl/monthly").status_code == 403            # no profit.pnl


def test_analyst_cost_and_pnl_yes(client, app_db, auth_on):
    c = _fresh(app_db); _login(c, "analyst1")
    assert c.get("/api/cost/analysis").status_code == 200
    assert c.get("/api/pnl/monthly").status_code == 200
    assert c.get("/api/analysis/cashiers").status_code == 403      # no ops.staff


def test_management_sees_everything(client, app_db, auth_on):
    c = _fresh(app_db); _login(c, "management1")
    for path in ("/api/cost/analysis", "/api/pnl/monthly",
                 "/api/analysis/discounts", "/api/extra/cash",
                 "/api/analysis/cashiers"):
        assert c.get(path).status_code != 403, path


def test_public_paths_allowed_for_any_role(client, app_db, auth_on):
    c = _fresh(app_db); _login(c, "marketing1")
    assert c.get("/api/branches").status_code == 200
    assert c.get("/api/months").status_code == 200


# ── admin-only user API ─────────────────────────────────────────────────────
def test_users_api_admin_only(client, app_db, auth_on):
    mk = _fresh(app_db); _login(mk, "marketing1")
    assert mk.get("/api/auth/users").status_code == 403
    mg = _fresh(app_db); _login(mg, "management1")
    r = mg.get("/api/auth/users")
    assert r.status_code == 200 and len(r.json()) >= 12


def test_admin_create_update_delete_user(client, app_db, auth_on):
    mg = _fresh(app_db); _login(mg, "management1")
    # create
    assert mg.post("/api/auth/users",
                   json={"username": "tester9", "password": "Pw@123456",
                         "role": "analyst"}).status_code == 200
    # the new user can log in and inherits analyst modules
    t = _fresh(app_db)
    assert _login(t, "tester9", "Pw@123456").status_code == 200
    assert t.get("/api/cost/analysis").status_code == 200       # analyst → cost.analysis
    # change role to marketing → cost now denied
    assert mg.put("/api/auth/users/tester9",
                  json={"role": "marketing"}).status_code == 200
    t2 = _fresh(app_db)
    _login(t2, "tester9", "Pw@123456")
    assert t2.get("/api/cost/analysis").status_code == 403
    # deactivate → can't log in
    assert mg.put("/api/auth/users/tester9",
                  json={"active": False}).status_code == 200
    assert _login(_fresh(app_db), "tester9", "Pw@123456").status_code == 401
    # delete
    assert mg.delete("/api/auth/users/tester9").status_code == 200


def test_password_hash_roundtrip():
    h = auth.hash_password("secret")
    assert auth.verify_password("secret", h["hash"], h["salt"])
    assert not auth.verify_password("Secret", h["hash"], h["salt"])


def test_token_tamper_rejected(monkeypatch):
    monkeypatch.setenv("OSHINEI_AUTH_SECRET", "k1")
    tok = auth.make_token(1, "u", "management")
    assert auth.parse_token(tok)["u"] == "u"
    monkeypatch.setenv("OSHINEI_AUTH_SECRET", "k2")   # different secret → invalid
    assert auth.parse_token(tok) is None


# ── red-set: closed cost/P&L data leaks ─────────────────────────────────────
MARKETING_DENIED_COST = [
    "/api/finance/prime-cost", "/api/branches/fixed-costs", "/api/branches/labor-costs",
    "/api/analysis/food_cost_trend", "/api/menu/engineering", "/api/export/monthly-pack",
    "/api/buffet/gp", "/api/buffet/gp-calendar", "/api/buffet/pnl",
    "/api/buffet/cover-economics", "/api/buffet/plate-cost-dist",
    "/api/buffet/cost-sensitivity", "/api/sales/heavy-eaters",
]

def test_marketing_denied_all_cost_endpoints(client, app_db, auth_on):
    c = _fresh(app_db); _login(c, "marketing1")
    for p in MARKETING_DENIED_COST:
        assert c.get(p).status_code == 403, p
    # …but customer-facing buffet paths stay open to customer.buffet
    assert c.get("/api/buffet/tier-mix").status_code != 403
    assert c.get("/api/buffet/child").status_code != 403

def test_analyst_allowed_cost_endpoints(client, app_db, auth_on):
    c = _fresh(app_db); _login(c, "analyst1")
    for p in ("/api/finance/prime-cost", "/api/analysis/food_cost_trend",
              "/api/menu/engineering", "/api/buffet/gp", "/api/buffet/cover-economics",
              "/api/sales/heavy-eaters", "/api/export/monthly-pack"):
        assert c.get(p).status_code != 403, p

def test_branches_list_public_but_cost_subpaths_gated(client, app_db, auth_on):
    c = _fresh(app_db); _login(c, "marketing1")
    assert c.get("/api/branches").status_code == 200          # plain list = public
    assert c.get("/api/branches/fixed-costs").status_code == 403
    assert c.get("/api/branches/labor-costs").status_code == 403

def test_path_allowed_longest_prefix_unit():
    mk = auth.modules_for_role("marketing")
    an = auth.modules_for_role("analyst")
    assert auth.path_allowed("GET", "/api/buffet/tier-mix", mk) is True
    assert auth.path_allowed("GET", "/api/buffet/gp", mk) is False
    assert auth.path_allowed("GET", "/api/buffet/gp-calendar", mk) is False
    assert auth.path_allowed("GET", "/api/buffet/gp", an) is True
    assert auth.path_allowed("GET", "/api/branches", mk) is True
    assert auth.path_allowed("GET", "/api/branches/fixed-costs", mk) is False
    # the old PUBLIC-short-circuit bug: a write under a public parent must still gate
    assert auth.path_allowed("PUT", "/api/branches/1/type", mk) is False


# ── red-set: admin self-lockout guards ──────────────────────────────────────
def test_admin_cannot_demote_or_disable_self(client, app_db, auth_on):
    mg = _fresh(app_db); _login(mg, "management1")
    assert mg.put("/api/auth/users/management1",
                  json={"role": "marketing"}).status_code == 400      # self-demote
    assert mg.put("/api/auth/users/management1",
                  json={"active": False}).status_code == 400          # self-disable
    # and the account is unchanged — still an active admin
    me = mg.get("/api/auth/me").json()
    assert me["role"] == "management" and me["authenticated"]

def test_last_admin_guard_helpers():
    import sqlite3
    con = sqlite3.connect(":memory:"); con.row_factory = sqlite3.Row
    auth.init_auth(con)
    h = auth.hash_password("x")
    con.execute("INSERT INTO app_user(username,pw_hash,pw_salt,role) VALUES('m1',?,?,'management')",
                (h["hash"], h["salt"]))
    con.commit()
    assert auth.count_active_admins(con) == 1
    assert auth.is_admin_role("management") and not auth.is_admin_role("marketing")


# ── red-set: secret hardening (domain separation + fail-closed) ──────────────
def test_session_key_domain_separated_from_write_token(monkeypatch):
    import hashlib
    monkeypatch.setenv("OSHINEI_AUTH", "1")
    monkeypatch.delenv("OSHINEI_AUTH_SECRET", raising=False)
    monkeypatch.setenv("OSHINEI_SEED_TOKEN", "sharedtok")
    key = auth._secret()
    # MUST NOT equal the naive sha256(write-token) — else a write-token holder
    # could derive the signing key and forge cookies.
    assert key != hashlib.sha256(b"sharedtok").digest()
    assert key == hashlib.sha256(auth._SESSION_KEY_DOMAIN + b"sharedtok").digest()

def test_fail_closed_when_enabled_without_secret(client, app_db, monkeypatch):
    monkeypatch.setenv("OSHINEI_AUTH", "1")
    monkeypatch.delenv("OSHINEI_AUTH_SECRET", raising=False)
    monkeypatch.delenv("OSHINEI_SEED_TOKEN", raising=False)
    c = _fresh(app_db)
    assert c.post("/api/auth/login",
                  json={"username": "management1", "password": DEFAULT_PW}).status_code == 503
    assert c.get("/api/sales/monthly").status_code == 503
    # and the secret helper refuses rather than signing with the dev constant
    import pytest as _pt
    with _pt.raises(auth.AuthMisconfigured):
        auth._secret()
