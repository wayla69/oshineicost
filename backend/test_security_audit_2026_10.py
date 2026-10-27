# -*- coding: utf-8 -*-
"""Security / data-loss audit 2026-10: one focused test per fix.

 1  onclick JSON args are fully HTML-escaped (stored XSS from survey text)
 2  hot-swap takes the write lock BEFORE the carry-forward
 3  get_db never hands back / leaves a connection mid-transaction
 4  /api/_admin/* machine endpoints accept only the owner ("admin" / bare) token
 5  /api/upload accepts a listed token from the comma list
 6  role / branch helpers read the LIVE account, not the cookie
 7  /api/auth/me/password is behind the session gate and needs an active account
 8  login throttle: Fly-Client-IP (not X-Forwarded-For), per-account ceiling, bounded dict
 9  google_review_url must be https:// (save + render)
10  branch rename moves every name-keyed reference
11  service worker: only '/' is the shell, /api/auth/me never cached, cache cleared on login/expiry

Platform-neutral: no Windows paths; the isolated DB is a tmp_path copy of the session fixture.
"""
import html
import itertools
import json
import re
import sqlite3
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from starlette.requests import Request

import auth
import core

ROOT = Path(__file__).resolve().parent.parent
PW = "Sec-Audit-2026!"
_GEN = itertools.count(100_000)


@pytest.fixture
def auth_on(monkeypatch):
    monkeypatch.setenv("OSHINEI_AUTH", "1")
    monkeypatch.setenv("OSHINEI_AUTH_SECRET", "test-secret-stable")
    yield


@pytest.fixture
def iso_db(client, app_db, tmp_path, monkeypatch):
    """A private copy of the seeded fixture DB that get_db() serves for this test only, so the
    writes below never leak into the shared session DB. Bumping core._db_gen makes every worker
    thread reconnect; monkeypatch restores both at teardown, which bumps them back again."""
    dst = tmp_path / "iso.db"
    src = sqlite3.connect(str(app_db))
    out = sqlite3.connect(str(dst))
    src.backup(out)
    src.close(); out.close()
    monkeypatch.setattr(core, "DB_PATH", dst)
    # a generation no thread has seen yet (a fixed +1 would repeat across tests and keep the
    # previous test's private DB connected)
    monkeypatch.setattr(core, "_db_gen", core._db_gen + next(_GEN))
    core.invalidate_cache(rebuild_cost=False)
    yield dst
    core.invalidate_cache(rebuild_cost=False)


def _q(db, sql, args=()):
    c = sqlite3.connect(str(db))
    try:
        return c.execute(sql, args).fetchall()
    finally:
        c.close()


def _exec(db, sql, args=()):
    c = sqlite3.connect(str(db))
    try:
        c.execute(sql, args); c.commit()
    finally:
        c.close()


def _mk_user(db, username, role, branch=None, active=True):
    c = sqlite3.connect(str(db)); c.row_factory = sqlite3.Row
    try:
        auth.create_user(c, username, PW, role, branch=branch)
        c.execute("UPDATE app_user SET must_change=0, active=? WHERE username=?", (1 if active else 0, username))
        c.commit()
    finally:
        c.close()


def _req(cookie: str = "", headers=None, client=("1.2.3.4", 5555)):
    hs = [(b"cookie", f"{auth.SESSION_COOKIE}={cookie}".encode())] if cookie else []
    for k, v in (headers or {}).items():
        hs.append((k.lower().encode(), v.encode()))
    return Request({"type": "http", "method": "GET", "path": "/", "headers": hs, "client": client,
                    "query_string": b""})


# ── 1. stored XSS through onclick JSON arguments ─────────────────────────────
def _js_files():
    return sorted((ROOT / "js").glob("*.js"))


def test_no_json_arg_escaped_only_for_quotes():
    """`JSON.stringify(x).replace(/"/g,'&quot;')` (or the &#39; twin) leaves `&` alone, so text that
    holds `&quot;` decodes to a bare quote inside the attribute and closes the JS string."""
    bad = re.compile(r"""JSON\.stringify\((?:[^()]|\([^()]*\))*\)\.replace\(/["']/g,\s*["']&(?:quot|#39);["']\)""")
    hits = []
    for f in _js_files():
        for i, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            if bad.search(line):
                hits.append(f"{f.name}:{i}")
    assert not hits, hits
    bom = (ROOT / "js" / "13-bom-center.js").read_text(encoding="utf-8")
    assert "function _bmcQ(v) { return esc(JSON.stringify(v)); }" in bom


def test_full_escape_survives_entity_payload():
    """The mechanism: esc() (& < > " ') over the JSON, then the HTML parser's entity decoding, gives
    back exactly the JSON - so the review text arrives as data, never as code."""
    def esc(s):
        return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;").replace("'", "&#39;")
    payload = 'nice&quot;);alert(document.cookie);//&#39; </a>'
    attr = esc(json.dumps(payload))
    assert '"' not in attr                     # nothing can close a double-quoted attribute
    assert json.loads(html.unescape(attr)) == payload
    # the old quote-only escape: decoding turns the stored &quot; into a real quote inside the JS string
    old = json.dumps(payload).replace('"', "&quot;")
    with pytest.raises(ValueError):
        json.loads(html.unescape(old))
    src = (ROOT / "js" / "11-feedback.js").read_text(encoding="utf-8")
    assert "fbCopy(this, ${_fbEsc(JSON.stringify(" in src


# ── 2. hot-swap: write lock first ────────────────────────────────────────────
def _mk_live(p):
    c = sqlite3.connect(str(p))
    c.execute("CREATE TABLE recon_uploads(upload_id INTEGER PRIMARY KEY, file_name TEXT)")
    c.execute("INSERT INTO recon_uploads(file_name) VALUES('a')")
    c.commit(); c.close()


def _mk_incoming(p):
    c = sqlite3.connect(str(p))
    c.execute("CREATE TABLE sales_transaction(id INTEGER PRIMARY KEY)")
    c.execute("INSERT INTO sales_transaction(id) VALUES(1)")
    c.commit(); c.close()


@pytest.mark.parametrize("verdict", [(False, "forced abort"), None])
def test_hot_swap_holds_write_lock_during_carry_forward(tmp_path, monkeypatch, verdict):
    live = tmp_path / "oshinei.db"; _mk_live(live)
    inc = Path(str(live) + ".incoming"); _mk_incoming(inc)
    monkeypatch.setattr(core, "DB_PATH", live)
    real = core._carry_forward_and_verify
    seen = {}

    def spy(lp, ip):
        seen["writer"] = core._db_rw._writer          # the exclusive lock is already held
        return verdict if verdict is not None else real(lp, ip)
    monkeypatch.setattr(core, "_carry_forward_and_verify", spy)
    done = core._apply_incoming_live()
    assert seen["writer"] is True
    assert core._db_rw._writer is False                # released on the abort path and the success path
    assert done is (verdict is None)
    if verdict is not None:
        assert inc.exists()                            # aborted: live kept, staged file left for inspection


# ── 3. get_db transaction hygiene ────────────────────────────────────────────
def _can_write_now(db) -> bool:
    c = sqlite3.connect(str(db), timeout=0.2)
    try:
        c.execute("BEGIN IMMEDIATE"); c.rollback()
        return True
    except sqlite3.OperationalError:
        return False
    finally:
        c.close()


def test_get_db_rolls_back_on_http_exception(iso_db):
    _exec(iso_db, "CREATE TABLE IF NOT EXISTS _sec_probe(x)")
    with pytest.raises(HTTPException):
        with core.get_db() as con:
            con.execute("INSERT INTO _sec_probe VALUES (1)")
            raise HTTPException(409, "dup")
    assert not core._tls.con.in_transaction
    assert _can_write_now(iso_db)
    assert _q(iso_db, "SELECT COUNT(*) FROM _sec_probe")[0][0] == 0


def test_get_db_drops_a_dangling_transaction_before_reuse(iso_db):
    _exec(iso_db, "CREATE TABLE IF NOT EXISTS _sec_probe(x)")
    with core.get_db() as con:
        pass
    con.execute("INSERT INTO _sec_probe VALUES (2)")   # outside any `with`: a forgotten transaction
    assert con.in_transaction
    with core.get_db() as con2:
        assert con2 is con and not con2.in_transaction
    assert _can_write_now(iso_db)


def test_get_db_commits_uncommitted_writes_on_clean_exit(iso_db):
    _exec(iso_db, "CREATE TABLE IF NOT EXISTS _sec_probe(x)")
    with core.get_db() as con:
        con.execute("INSERT INTO _sec_probe VALUES (3)")
    assert _q(iso_db, "SELECT COUNT(*) FROM _sec_probe WHERE x=3")[0][0] == 1
    assert _can_write_now(iso_db)


def test_duplicate_downtime_409_does_not_lock_the_db(iso_db, monkeypatch):
    monkeypatch.delenv("OSHINEI_SEED_TOKEN", raising=False)
    import main
    c = TestClient(main.app)
    c.get("/api/branch-downtime")                       # creates the table on a DB that has none yet
    body = {"branch": "BranchA", "date_from": "2025-01-02", "date_to": "2025-01-03"}
    assert c.post("/api/branch-downtime", json=body).status_code == 200
    assert c.post("/api/branch-downtime", json=body).status_code == 409
    assert _can_write_now(iso_db)                      # no worker connection is left holding the lock


# ── 4. machine endpoints: owner token only ───────────────────────────────────
def test_admin_endpoints_refuse_staff_tokens(client, monkeypatch):
    monkeypatch.setenv("OSHINEI_SEED_TOKEN", "admin:OWNER-tok-1,Patcharee:STAFF-tok-2,BARE-tok-3")
    monkeypatch.delenv("OSHINEI_AUTH", raising=False)
    url = "/api/_admin/cost-overrides"
    assert client.get(url, headers={"X-Seed-Token": "STAFF-tok-2"}).status_code == 404
    assert client.get(url, headers={"X-Seed-Token": "nope"}).status_code == 404
    assert client.get(url).status_code == 404
    assert client.get(url, headers={"X-Seed-Token": "OWNER-tok-1"}).status_code == 200
    assert client.get(url, headers={"X-Seed-Token": "BARE-tok-3"}).status_code == 200
    # export-state streams password hashes: same gate
    assert client.get("/api/_admin/export-state", headers={"X-Seed-Token": "STAFF-tok-2"}).status_code == 404
    # seed-db replaces the whole database: a staff token is refused before the upload is read
    r = client.post("/api/_admin/seed-db", headers={"X-Seed-Token": "STAFF-tok-2"},
                    files={"file": ("x.db", b"not a db", "application/octet-stream")})
    assert r.status_code == 404


# ── 5. /api/upload with the comma list ───────────────────────────────────────
def test_upload_accepts_a_token_from_the_list(client, monkeypatch):
    monkeypatch.setenv("OSHINEI_SEED_TOKEN", "admin:OWNER-tok-1,Patcharee:STAFF-tok-2")
    f = {"file": ("random.txt", b"not an export", "text/plain")}
    assert client.post("/api/upload", files=f).status_code == 403
    assert client.post("/api/upload", files=f, headers={"X-Upload-Token": "wrong"}).status_code == 403
    for tok in ("OWNER-tok-1", "STAFF-tok-2"):
        r = client.post("/api/upload", files={"file": ("random.txt", b"not an export", "text/plain")},
                        headers={"X-Upload-Token": tok})
        assert r.status_code != 403, r.text[:200]   # through the gate (then refused as an unknown format)
    monkeypatch.setenv("OSHINEI_SEED_TOKEN", " , ")  # set but holding no usable entry: closed, not open
    assert client.post("/api/upload", files={"file": ("random.txt", b"x", "text/plain")},
                       headers={"X-Upload-Token": ""}).status_code == 403


# ── 6. live role / branch, not the cookie ────────────────────────────────────
def test_helpers_use_the_live_account(iso_db, auth_on):
    from routers.auth_api import _require_admin
    _mk_user(iso_db, "sec_mgr", "management")
    _mk_user(iso_db, "sec_br", "branch", branch="BranchA")
    mgr_cookie = auth.make_token(1, "sec_mgr", "management")
    assert "system.admin" in auth.request_modules(_req(mgr_cookie))
    assert _require_admin(_req(mgr_cookie))["u"] == "sec_mgr"
    # demoted in the DB: the still-valid management cookie no longer carries admin
    _exec(iso_db, "UPDATE app_user SET role='marketing' WHERE username='sec_mgr'")
    assert "system.admin" not in auth.request_modules(_req(mgr_cookie))
    with pytest.raises(HTTPException) as e:
        _require_admin(_req(mgr_cookie))
    assert e.value.status_code == 403
    # deactivated: no session at all
    _exec(iso_db, "UPDATE app_user SET active=0 WHERE username='sec_mgr'")
    with pytest.raises(HTTPException) as e:
        _require_admin(_req(mgr_cookie))
    assert e.value.status_code == 401
    assert auth.request_modules(_req(mgr_cookie)) == set()
    # a branch cookie for a login since moved to BranchB / switched to another role
    br_cookie = auth.make_token(2, "sec_br", "branch", branch="BranchA")
    assert core.is_branch_session(_req(br_cookie)) and auth.scope_branch(_req(br_cookie)) == "BranchA"
    _exec(iso_db, "UPDATE app_user SET branch='BranchB' WHERE username='sec_br'")
    assert auth.scope_branch(_req(br_cookie)) == "BranchB"
    _exec(iso_db, "UPDATE app_user SET role='marketing', branch=NULL WHERE username='sec_br'")
    assert not core.is_branch_session(_req(br_cookie))
    assert auth.scope_branch(_req(br_cookie)) is None


def test_middleware_puts_the_live_identity_on_request_state(iso_db, auth_on):
    import main
    _mk_user(iso_db, "sec_state", "management")
    c = TestClient(main.app)
    assert c.post("/api/auth/login", json={"username": "sec_state", "password": PW}).status_code == 200
    orig = auth.live_session
    calls = []

    def spy(r):
        calls.append(getattr(r.state, "auth_user", None))     # what the middleware left for the helper
        return orig(r)
    auth.live_session = spy
    try:
        assert c.get("/api/auth/users").status_code == 200
    finally:
        auth.live_session = orig
    assert calls and calls[0] and calls[0]["u"] == "sec_state" and calls[0]["live"] is True


# ── 7. /api/auth/me/password behind the gate, active only ────────────────────
def test_password_change_needs_an_active_session(iso_db, auth_on):
    import main
    _mk_user(iso_db, "sec_pw", "marketing")
    c = TestClient(main.app)
    assert c.post("/api/auth/login", json={"username": "sec_pw", "password": PW}).status_code == 200
    _exec(iso_db, "UPDATE app_user SET active=0 WHERE username='sec_pw'")
    r = c.post("/api/auth/me/password", json={"current_password": PW, "new_password": "Brand-New-Pw-99"})
    assert r.status_code == 401
    # the hash did not change
    con = sqlite3.connect(str(iso_db)); con.row_factory = sqlite3.Row
    try:
        assert auth.authenticate(con, "sec_pw", PW) is None          # inactive
        row = auth.get_user(con, "sec_pw")
        assert auth.verify_password(PW, row["pw_hash"], row["pw_salt"])
        assert auth.change_own_password(con, "sec_pw", PW, "Brand-New-Pw-99") is False
    finally:
        con.close()
    # an unknown sub-path of /api/auth/me is no longer let through without a session
    anon = TestClient(main.app)
    assert anon.post("/api/auth/me/password", json={"current_password": "x", "new_password": "y"}).status_code == 401
    assert anon.get("/api/auth/me").status_code == 200                 # the bootstrap itself stays open


# ── 8. login throttle ────────────────────────────────────────────────────────
def test_client_ip_ignores_x_forwarded_for():
    import routers.survey as sv
    r = _req(headers={"X-Forwarded-For": "6.6.6.6"})
    assert auth.client_ip(r) == "1.2.3.4" and sv._client_ip(r) == "1.2.3.4"
    r = _req(headers={"X-Forwarded-For": "6.6.6.6", "Fly-Client-IP": "9.9.9.9"})
    assert auth.client_ip(r) == "9.9.9.9" and sv._client_ip(r) == "9.9.9.9"


def test_throttle_per_account_and_bounded(monkeypatch):
    monkeypatch.setattr(auth, "_LOGIN_FAILS", {})
    for i in range(auth._LOGIN_USER_MAX_FAILS):
        assert not auth.login_throttled(f"10.0.0.{i}", "victim")
        auth.record_login_failure(f"10.0.0.{i}", "victim")
    assert auth.login_throttled("10.9.9.9", "victim")      # a fresh address does not buy more guesses
    assert not auth.login_throttled("10.9.9.9", "someone_else")
    monkeypatch.setattr(auth, "_LOGIN_FAILS", {})
    monkeypatch.setattr(auth, "_LOGIN_MAX_KEYS", 50)
    for i in range(400):
        auth.record_login_failure(f"172.16.{i // 250}.{i % 250}", f"junk{i}")
    assert len(auth._LOGIN_FAILS) <= 50


def test_rotating_x_forwarded_for_does_not_reset_the_throttle(iso_db, auth_on, monkeypatch):
    import main
    monkeypatch.setattr(auth, "_LOGIN_FAILS", {})
    _mk_user(iso_db, "sec_thr", "marketing")
    c = TestClient(main.app)
    for i in range(auth._LOGIN_MAX_FAILS):
        r = c.post("/api/auth/login", json={"username": "sec_thr", "password": "wrong"},
                   headers={"X-Forwarded-For": f"203.0.113.{i}"})
        assert r.status_code == 401
    r = c.post("/api/auth/login", json={"username": "sec_thr", "password": PW},
               headers={"X-Forwarded-For": "203.0.113.200"})
    assert r.status_code == 429


# ── 9. google_review_url ─────────────────────────────────────────────────────
def test_review_url_must_be_https(iso_db, monkeypatch):
    import collections
    import main
    import routers.survey as sv
    monkeypatch.delenv("OSHINEI_SEED_TOKEN", raising=False)
    monkeypatch.delenv("OSHINEI_AUTH", raising=False)
    monkeypatch.setattr(sv, "_HITS", collections.defaultdict(collections.deque))
    c = TestClient(main.app)
    for bad in ("javascript:alert(1)", "JaVaScRiPt:alert(1)", "data:text/html,x", "http://g.page/r/x",
                'https://x.com/"><script>', "https://"):
        r = c.post("/api/feedback/survey-links", json={"branch_id": 1, "google_review_url": bad})
        assert r.status_code == 400, (bad, r.text[:200])
    good = "https://g.page/r/abc/review"
    r = c.post("/api/feedback/survey-links", json={"branch_id": 1, "google_review_url": good})
    assert r.status_code == 200, r.text[:200]
    code = _q(iso_db, "SELECT code FROM feedback_survey_link WHERE branch_id=1")[0][0]
    ans = c.post(f"/api/feedback/survey/{code}", json={"rating": 5, "text": "อร่อยมาก"}).json()
    assert ans["review_url"] == good
    # a bad value already in the DB (saved before this fix) is never handed to the page
    _exec(iso_db, "UPDATE feedback_survey_link SET google_review_url='javascript:alert(1)' WHERE branch_id=1")
    ans = c.post(f"/api/feedback/survey/{code}", json={"rating": 5, "text": "อร่อยอีก"}).json()
    assert ans["review_url"] is None
    page = c.get(f"/s/{code}")
    assert page.status_code == 200
    assert "slice(0,8).toLowerCase()==='https://'" in page.text      # the page-side guard too


# ── 10. branch rename moves the name-keyed rows ──────────────────────────────
def test_branch_rename_moves_references(iso_db, monkeypatch):
    import main
    monkeypatch.delenv("OSHINEI_SEED_TOKEN", raising=False)
    old = _q(iso_db, "SELECT name FROM branches WHERE branch_id=2")[0][0]
    _mk_user(iso_db, "sec_brn", "branch", branch=old)
    c = TestClient(main.app)
    c.get("/api/branch-downtime")
    assert c.post("/api/branch-downtime", json={"branch": old, "date_from": "2025-02-01",
                                                "date_to": "2025-02-02"}).status_code == 200
    _exec(iso_db, "INSERT OR REPLACE INTO app_settings(key,value) VALUES('branch_fixed_costs',?)",
          (json.dumps({old: 1000, "BranchA": 5}, ensure_ascii=False),))
    _exec(iso_db, "CREATE TABLE IF NOT EXISTS feedback_survey_link(branch_id INTEGER, branch_name TEXT, code TEXT, "
                  "google_review_url TEXT, active INTEGER, scans INTEGER, responses INTEGER, created_at TEXT, created_by TEXT)")
    _exec(iso_db, "INSERT INTO feedback_survey_link(branch_id, branch_name, code, active) VALUES (2, ?, 'SECRN001', 1)", (old,))
    new = old + "_rn"
    r = c.put("/api/branches/2", json={"name": new})
    assert r.status_code == 200, r.text[:200]
    assert _q(iso_db, "SELECT branch FROM app_user WHERE username='sec_brn'")[0][0] == new
    assert _q(iso_db, "SELECT COUNT(*) FROM branch_downtime WHERE branch=?", (old,))[0][0] == 0
    assert _q(iso_db, "SELECT COUNT(*) FROM branch_downtime WHERE branch=?", (new,))[0][0] == 1
    fc = json.loads(_q(iso_db, "SELECT value FROM app_settings WHERE key='branch_fixed_costs'")[0][0])
    assert fc == {new: 1000, "BranchA": 5}
    assert _q(iso_db, "SELECT branch_name FROM feedback_survey_link WHERE code='SECRN001'")[0][0] == new
    # a clash in a name-keyed table rolls the WHOLE rename back (409), branches included
    _exec(iso_db, "INSERT INTO branch_downtime(branch,date_from,date_to) VALUES (?, '2025-02-01', '2025-02-02')", (old,))
    r = c.put("/api/branches/2", json={"name": old})
    assert r.status_code == 409, r.text[:200]
    assert _q(iso_db, "SELECT name FROM branches WHERE branch_id=2")[0][0] == new
    assert _q(iso_db, "SELECT branch FROM app_user WHERE username='sec_brn'")[0][0] == new
    assert _can_write_now(iso_db)


# ── 11. service worker + session cache ───────────────────────────────────────
def _fn_body(src: str, name: str) -> str:
    i = src.index(name)
    j = src.index("{", i)
    depth, k = 0, j
    while True:
        if src[k] == "{": depth += 1
        elif src[k] == "}":
            depth -= 1
            if depth == 0:
                return src[j:k + 1]
        k += 1


def test_service_worker_caches_only_the_shell_and_never_auth_me():
    sw = (ROOT / "sw.js").read_text(encoding="utf-8")
    api_ok = sw[sw.index("const API_OK"):sw.index("];", sw.index("const API_OK"))]
    assert "auth" not in api_ok
    assert "const API = 'oshinei-api-v1'" not in sw                 # bumped: the old copy is deleted on activate
    body = _fn_body(sw, "async function pageNetworkFirst")
    put = [ln for ln in body.splitlines() if "cache.put('/'" in ln]
    assert put and all("pathname === '/'" in ln for ln in put)


def test_session_changes_clear_the_offline_api_cache():
    core_js = (ROOT / "js" / "01-core.js").read_text(encoding="utf-8")
    assert "postMessage({ type: 'clear-api-cache' })" in _fn_body(core_js, "function _swClearApiCache")
    for fn in ("function onSessionExpired", "async function doLogin", "async function doLogout"):
        assert "_swClearApiCache()" in _fn_body(core_js, fn), fn
