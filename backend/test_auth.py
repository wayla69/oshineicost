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


def test_login_events_logged(client, app_db, auth_on):
    """AUD-1/AUD-6: successful + failed logins land in the append-only
    recon_audit_log (created on demand by the dashboard if recon hasn't yet)."""
    import sqlite3
    import main
    c = _fresh(app_db)
    assert _login(c, "operation1").status_code == 200       # success
    _login(c, "operation1", "wrongpass")                    # failure
    con = sqlite3.connect(main.DB_PATH)
    rows = con.execute("SELECT action, by FROM recon_audit_log WHERE entity='auth'").fetchall()
    con.close()
    actions = {r[0] for r in rows}
    assert "login" in actions and "login_fail" in actions
    assert any(r[1] == "operation1" for r in rows)


def test_missing_cookie_is_401(client, app_db, auth_on):
    c = _fresh(app_db)
    assert c.get("/api/sales/monthly").status_code == 401


def test_forged_cookie_is_401(client, app_db, auth_on):
    c = _fresh(app_db)
    c.cookies.set("oshinei_session", "tampered.signature")
    assert c.get("/api/sales/monthly").status_code == 401


def test_seed_db_bypasses_login_middleware(client, app_db, auth_on):
    # /api/_admin/seed-db is a headless machine-to-machine endpoint with its OWN
    # X-Seed-Token auth — it must NOT be caught by the login middleware, else enabling
    # OSHINEI_AUTH silently breaks the hosted data-refresh push. With no cookie it
    # must NOT 401 (it gets past the gate; missing file → 422, bad token → 404).
    c = _fresh(app_db)
    assert c.post("/api/_admin/seed-db", headers={"X-Seed-Token": "wrong"}).status_code != 401


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
                   json={"username": "tester9", "password": "Pw@1234567",
                         "role": "analyst"}).status_code == 200
    # the new user can log in and inherits analyst modules
    t = _fresh(app_db)
    assert _login(t, "tester9", "Pw@1234567").status_code == 200
    assert t.get("/api/cost/analysis").status_code == 200       # analyst → cost.analysis
    # change role to marketing → cost now denied
    assert mg.put("/api/auth/users/tester9",
                  json={"role": "marketing"}).status_code == 200
    t2 = _fresh(app_db)
    _login(t2, "tester9", "Pw@1234567")
    assert t2.get("/api/cost/analysis").status_code == 403
    # deactivate → can't log in
    assert mg.put("/api/auth/users/tester9",
                  json={"active": False}).status_code == 200
    assert _login(_fresh(app_db), "tester9", "Pw@1234567").status_code == 401
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


def test_management_report_is_its_own_module():
    """รายงานผู้บริหาร (owner 2026-10-28): the management summary, its month series and its Excel are report.management,
    held by management alone by default - sales.overview or report.export no longer open it."""
    mg = auth.modules_for_role("management")
    for path in ("/api/reports/management", "/api/reports/management/months", "/api/reports/management.xlsx"):
        assert auth.path_allowed("GET", path, mg) is True
        for role in ("analyst", "marketing", "operation", "accounting", "innovation", "salessupport"):
            assert auth.path_allowed("GET", path, auth.modules_for_role(role)) is False, (role, path)
    assert "report.management" in auth.MODULES and auth.ROLE_MODULES["management"] >= {"report.management"}
    # the POS detail pages went back to operation; the analyst keeps ส่วนลด (owner 2026-10-28)
    assert "ops.posextra" not in auth.ROLE_MODULES["analyst"] and "ops.discounts" in auth.ROLE_MODULES["analyst"]


def test_role_permissions_reset_returns_to_code_defaults(client, app_db, auth_on):
    """The permissions page's "ใช้ค่าตั้งต้นของระบบ": DELETE drops the saved matrix (management only)."""
    mg = _fresh(app_db); _login(mg, "management1")
    custom = {r: sorted(auth.ROLE_MODULES[r]) for r in auth.ROLES}
    custom["marketing"] = sorted(set(custom["marketing"]) | {"ops.discounts"})
    assert mg.put("/api/auth/role-permissions", json={"permissions": custom}).status_code == 200
    assert "ops.discounts" in auth.modules_for_role("marketing")
    mk = _fresh(app_db); _login(mk, "marketing1")
    assert mk.delete("/api/auth/role-permissions").status_code == 403
    r = mg.delete("/api/auth/role-permissions")
    assert r.status_code == 200 and "ops.discounts" not in r.json()["permissions"]["marketing"]
    assert auth.modules_for_role("marketing") == auth.ROLE_MODULES["marketing"]


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


# ── matrix fidelity + session lifecycle ─────────────────────────────────────
def test_role_modules_matrix_resolves_exactly(client):
    # the permission cache is filled at boot from whatever DB was bound at import
    # time; re-read it from the fixture DB (no override row) so this asserts the
    # code defaults, not a matrix someone saved in the real local DB
    import sqlite3
    import core
    con = sqlite3.connect(str(core.DB_PATH))
    try:
        auth.refresh_perm_cache(con)
    finally:
        con.close()
    for role, mods in auth.ROLE_MODULES.items():
        assert auth.modules_for_role(role) == set(mods), role
    assert auth.modules_for_role("management") == set(auth.MODULES)
    assert auth.modules_for_role("nonsense") == set()

def test_expired_cookie_rejected_at_endpoint(client, app_db, auth_on):
    c = _fresh(app_db)
    c.cookies.set(auth.SESSION_COOKIE, auth.make_token(1, "management1", "management", ttl=-10))
    assert c.get("/api/sales/monthly").status_code == 401

def test_login_sets_httponly_samesite_cookie(client, app_db, auth_on):
    c = _fresh(app_db)
    sc = _login(c, "management1").headers.get("set-cookie", "").lower()
    assert "oshinei_session=" in sc and "httponly" in sc and "samesite=lax" in sc


# ── #14: role/active changes take effect immediately ────────────────────────
def test_deactivation_and_role_change_take_effect_immediately(client, app_db, auth_on):
    mg = _fresh(app_db); _login(mg, "management1")
    mg.post("/api/auth/users", json={"username": "tmp14", "password": "Temp@12345", "role": "analyst"})
    u = _fresh(app_db); _login(u, "tmp14", "Temp@12345")
    assert u.get("/api/cost/analysis").status_code == 200          # analyst → cost ok
    mg.put("/api/auth/users/tmp14", json={"role": "marketing"})    # downgrade
    assert u.get("/api/cost/analysis").status_code == 403          # …immediately denied
    mg.put("/api/auth/users/tmp14", json={"active": False})        # deactivate
    assert u.get("/api/sales/monthly").status_code == 401          # …immediately logged out
    mg.delete("/api/auth/users/tmp14")


# ── #6: password lifecycle ──────────────────────────────────────────────────
def test_seeded_and_created_users_must_change(client, app_db, auth_on):
    c = _fresh(app_db)
    assert _login(c, "management1").json()["must_change"] is True

def test_self_service_password_change_flow(client, app_db, auth_on):
    mg = _fresh(app_db); _login(mg, "management1")
    mg.post("/api/auth/users", json={"username": "pwuser", "password": "Temp@12345", "role": "analyst"})
    u = _fresh(app_db)
    assert _login(u, "pwuser", "Temp@12345").json()["must_change"] is True
    # wrong current → 403; too-short new → 400
    assert u.post("/api/auth/me/password",
                  json={"current_password": "nope", "new_password": "NewPw@1234"}).status_code == 403
    assert u.post("/api/auth/me/password",
                  json={"current_password": "Temp@12345", "new_password": "short"}).status_code == 400
    # success clears must_change and the new password works
    assert u.post("/api/auth/me/password",
                  json={"current_password": "Temp@12345", "new_password": "NewPw@1234"}).status_code == 200
    u2 = _fresh(app_db)
    assert _login(u2, "pwuser", "NewPw@1234").json()["must_change"] is False
    mg.delete("/api/auth/users/pwuser")


# ── #8: login throttle ──────────────────────────────────────────────────────
def test_login_throttle_unit(monkeypatch):
    monkeypatch.setattr(auth, "_LOGIN_FAILS", {})
    ip, u = "10.0.0.1", "marketing1"
    assert not auth.login_throttled(ip, u)
    for _ in range(auth._LOGIN_MAX_FAILS):
        auth.record_login_failure(ip, u)
    assert auth.login_throttled(ip, u)
    auth.clear_login_failures(ip, u)
    assert not auth.login_throttled(ip, u)

def test_login_throttle_endpoint(client, app_db, auth_on, monkeypatch):
    monkeypatch.setattr(auth, "_LOGIN_FAILS", {})
    c = _fresh(app_db)
    for _ in range(auth._LOGIN_MAX_FAILS):
        assert c.post("/api/auth/login",
                      json={"username": "management1", "password": "wrong"}).status_code == 401
    # now throttled even with the CORRECT password
    assert c.post("/api/auth/login",
                  json={"username": "management1", "password": DEFAULT_PW}).status_code == 429


# ── #2: app_user survives a data-refresh DB swap ────────────────────────────
def test_carry_forward_preserves_app_user(tmp_path):
    import main, sqlite3
    live, inc = str(tmp_path / "live.db"), str(tmp_path / "inc.db")
    lc = sqlite3.connect(live); auth.init_auth(lc)
    h = auth.hash_password("x")
    lc.execute("INSERT INTO app_user(username,pw_hash,pw_salt,role,active) VALUES('boss',?,?,'management',1)",
               (h["hash"], h["salt"])); lc.commit(); lc.close()
    ic = sqlite3.connect(inc); auth.init_auth(ic)
    h2 = auth.hash_password("y")
    ic.execute("INSERT INTO app_user(username,pw_hash,pw_salt,role,active) VALUES('stale',?,?,'marketing',1)",
               (h2["hash"], h2["salt"])); ic.commit(); ic.close()
    main._carry_forward_preserved(live, inc)     # live wins
    ic = sqlite3.connect(inc); ic.row_factory = sqlite3.Row
    names = {r["username"] for r in ic.execute("SELECT username FROM app_user")}
    ic.close()
    assert names == {"boss"}


# ── /docs gate: business reports require a login (any active role) ──────────
def test_docs_open_when_auth_disabled(client):
    r = client.get("/docs/UAT_SIGNOFF_MONTHLY_REPORT_2026-07-20.md")
    assert r.status_code == 200


def test_docs_requires_login_when_auth_on(client, app_db, auth_on):
    c = _fresh(app_db)
    r = c.get("/docs/UAT_SIGNOFF_MONTHLY_REPORT_2026-07-20.md")
    assert r.status_code == 401
    assert "text/html" in r.headers.get("content-type", "")   # human-friendly page
    # any logged-in ACTIVE user may read docs (no role-module mapping)
    assert _login(c, "operation1").status_code == 200
    assert c.get("/docs/UAT_SIGNOFF_MONTHLY_REPORT_2026-07-20.md").status_code == 200
    # SPA shell + static assets stay open for the login form itself
    c2 = _fresh(app_db)
    assert c2.get("/").status_code == 200
    # /docs/ui-kit/ (design tokens CSS) must stay open — index.html <head>
    # loads tokens.css as the SPA's only :root variable source; gating it
    # renders the LOGIN page itself unstyled (live regression 2026-07-20)
    r = c2.get("/docs/ui-kit/tokens.css")
    assert r.status_code == 200 and ":root" in r.text


# ── Sales Support role + create/reset without typing a password ─────────────
def test_admin_can_create_user_with_no_password_on_a_temp_password(client, app_db, auth_on):
    mg = _fresh(app_db); _login(mg, "management1")
    r = mg.post("/api/auth/users", json={"username": "ss_new", "role": "salessupport"})
    assert r.status_code == 200, r.text
    tmp = r.json()["temp_password"]                                # admin is told what to pass on (its own, not the default)
    assert tmp and tmp != DEFAULT_PW
    u = _fresh(app_db)
    assert _login(u, "ss_new", tmp).json()["must_change"] is True  # forced off it at first login
    # the forced change works from the temporary password, and the shared default is refused as the NEW one
    assert u.post("/api/auth/me/password",
                  json={"current_password": tmp, "new_password": DEFAULT_PW}).status_code == 400
    assert u.post("/api/auth/me/password",
                  json={"current_password": tmp, "new_password": "MyOwnPass#99"}).status_code == 200
    # admin reset hands out a new temporary password and re-arms the change
    r = mg.put("/api/auth/users/ss_new", json={"reset_default": True})
    assert r.status_code == 200 and r.json()["temp_password"]
    u2 = _fresh(app_db)
    assert _login(u2, "ss_new", r.json()["temp_password"]).json()["must_change"] is True
    mg.delete("/api/auth/users/ss_new")


def test_sales_support_sees_the_supply_cluster_and_not_chain_cost(client, app_db, auth_on):
    mg = _fresh(app_db); _login(mg, "management1")
    tmp = mg.post("/api/auth/users", json={"username": "ss_scope", "role": "salessupport"}).json()["temp_password"]
    u = _fresh(app_db); _login(u, "ss_scope", tmp)
    assert "supply.ordering" in u.get("/api/auth/me").json()["modules"]
    assert u.get("/api/ingredients/purchases").status_code == 200
    assert u.get("/api/ingredients/purchases.template.xlsx", params={"weeks": 1}).status_code == 200
    assert u.get("/api/cost/analysis").status_code == 403
    assert u.get("/api/pnl/summary").status_code == 403
    mg.delete("/api/auth/users/ss_scope")


def test_saved_matrix_from_before_a_new_role_or_module_still_grants_defaults(app_db):
    """A role_permissions blob saved before salessupport/supply.ordering existed
    must not leave the new role with nothing, nor strip the new module from the
    roles whose default holds it."""
    import json, sqlite3
    con = sqlite3.connect(app_db); con.row_factory = sqlite3.Row
    old = {r: sorted(auth.ROLE_MODULES[r] - {"supply.ordering"}) for r in auth.ROLES if r != "salessupport"}
    con.execute("CREATE TABLE IF NOT EXISTS app_settings(key TEXT PRIMARY KEY, value TEXT)")
    con.execute("INSERT OR REPLACE INTO app_settings(key, value) VALUES ('role_permissions', ?)", (json.dumps(old),))
    try:
        auth.refresh_perm_cache(con)
        assert auth.modules_for_role("salessupport") == auth.ROLE_MODULES["salessupport"]
        assert "supply.ordering" in auth.modules_for_role("management")
        assert "supply.ordering" not in auth.modules_for_role("marketing")
    finally:
        con.execute("DELETE FROM app_settings WHERE key='role_permissions'"); con.commit()
        auth.refresh_perm_cache(con); con.close()


# ── the branch phone stays signed in for a month; everyone else 12 h ──────────
def test_branch_login_lives_30_days(client, app_db, auth_on):
    import time
    mg = _fresh(app_db); _login(mg, "management1")
    r = mg.post("/api/auth/users", json={"username": "brn_ttl", "password": "Temp@12345", "role": "branch", "branch": "BranchA"})
    assert r.status_code == 200, r.text
    b = _fresh(app_db)
    rb = _login(b, "brn_ttl", "Temp@12345")
    assert rb.status_code == 200, rb.text
    exp = auth.parse_token(b.cookies.get(auth.SESSION_COOKIE))["exp"]
    assert abs(exp - (time.time() + auth.SESSION_TTL_BRANCH)) < 120
    assert f"max-age={auth.SESSION_TTL_BRANCH}" in rb.headers.get("set-cookie", "").lower()
    exp_m = auth.parse_token(mg.cookies.get(auth.SESSION_COOKIE))["exp"]
    assert abs(exp_m - (time.time() + auth.SESSION_TTL)) < 120


# ── "จำการเข้าสู่ระบบบนเครื่องนี้ไว้ 30 วัน": any role, by choice on the login form ──
def test_remembered_login_lives_30_days(client, app_db, auth_on):
    import time
    c = _fresh(app_db)
    r = c.post("/api/auth/login", json={"username": "management1", "password": DEFAULT_PW, "remember": True})
    assert r.status_code == 200, r.text
    exp = auth.parse_token(c.cookies.get(auth.SESSION_COOKIE))["exp"]
    assert abs(exp - (time.time() + auth.SESSION_TTL_REMEMBER)) < 120
    assert f"max-age={auth.SESSION_TTL_REMEMBER}" in r.headers.get("set-cookie", "").lower()
    # unticked — and the field absent, as the PC-side callers send it — stays 12 h
    for body in ({"remember": False}, {}):
        c2 = _fresh(app_db)
        r2 = c2.post("/api/auth/login", json={"username": "management1", "password": DEFAULT_PW, **body})
        assert r2.status_code == 200, r2.text
        exp2 = auth.parse_token(c2.cookies.get(auth.SESSION_COOKIE))["exp"]
        assert abs(exp2 - (time.time() + auth.SESSION_TTL)) < 120
