# -*- coding: utf-8 -*-
"""Code guard (owner 2026-10-27: the app opens to the branches, so no reverse engineering, no copying
the code, and notice an AI that tries). See backend/code_guard.py for the layers."""
import itertools
import sqlite3

import pytest
from fastapi.testclient import TestClient

import auth
import code_guard as cg
import core

PW = "Code-Guard-2026!"
_GEN = itertools.count(200_000)
EDGE = {"Fly-Client-IP": "203.0.113.7"}          # a request from outside (not the machine itself)
CHROME = {"User-Agent": "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0 Mobile Safari/537.36"}


@pytest.fixture
def auth_on(monkeypatch):
    monkeypatch.setenv("OSHINEI_AUTH", "1")
    monkeypatch.setenv("OSHINEI_AUTH_SECRET", "test-secret-stable")
    yield


@pytest.fixture
def iso_db(client, app_db, tmp_path, monkeypatch):
    dst = tmp_path / "iso.db"
    src = sqlite3.connect(str(app_db)); out = sqlite3.connect(str(dst))
    src.backup(out); src.close(); out.close()
    monkeypatch.setattr(core, "DB_PATH", dst)
    monkeypatch.setattr(core, "_db_gen", core._db_gen + next(_GEN))
    core.invalidate_cache(rebuild_cost=False)
    yield dst
    core.invalidate_cache(rebuild_cost=False)


def _mk_user(db, username, role, branch=None):
    c = sqlite3.connect(str(db)); c.row_factory = sqlite3.Row
    try:
        auth.create_user(c, username, PW, role, branch=branch)
        c.execute("UPDATE app_user SET must_change=0 WHERE username=?", (username,))
        c.commit()
    finally:
        c.close()


def _signed_in(db, username="cg_mgr", role="management"):
    import main
    _mk_user(db, username, role)
    c = TestClient(main.app, headers={**EDGE, **CHROME})
    assert c.post("/api/auth/login", json={"username": username, "password": PW}).status_code == 200
    return c


def test_no_api_map():
    import main
    c = TestClient(main.app)
    for path in ("/openapi.json", "/redoc"):
        assert c.get(path).status_code == 404


def test_before_a_login_only_the_login_page(iso_db, auth_on):
    import main
    c = TestClient(main.app, headers={**EDGE, **CHROME})
    r = c.get("/")
    assert r.status_code == 200 and r.headers["X-Login-Page"] == "1" and r.headers["Cache-Control"] == "no-store"
    assert 'id="u"' in r.text and "/js/" not in r.text and "<!--" not in r.text
    assert "navigator" in r.text and "/api/sec/signal" in r.text          # the automation check rides along
    for path in ("/js/01-core.js", "/js/17-franchise.js", "/tabs.html", "/app.js"):
        assert c.get(path).status_code == 401, path


def test_a_signed_in_login_gets_the_app_with_its_mark(iso_db, auth_on):
    c = _signed_in(iso_db)
    shell = c.get("/")
    assert "X-Login-Page" not in shell.headers and "/js/01-core.js?v=" in shell.text and "<!--" not in shell.text
    mark = cg.mark_for("cg_mgr", auth._secret())
    js = c.get("/js/17-franchise.js")
    assert js.status_code == 200 and js.text.startswith(f'var __cg=__cg||"{mark}";')
    assert "public" not in js.headers["Cache-Control"]
    tabs = c.get("/tabs.html")
    assert f'data-cg="{mark}"' in tabs.text and "<!--" not in tabs.text and "private" in tabs.headers["Cache-Control"]


def test_an_admin_can_trace_a_mark_back_to_its_login(iso_db, auth_on):
    c = _signed_in(iso_db, "cg_admin", "management")
    mark = cg.mark_for("cg_mgr", auth._secret())
    _mk_user(iso_db, "cg_mgr", "management")
    r = c.get("/api/usage/security", params={"mark": mark})
    assert r.status_code == 200 and r.json()["mark"]["username"] == "cg_mgr"
    other = _signed_in(iso_db, "cg_staff", "marketing")
    assert other.get("/api/usage/security").status_code == 403


@pytest.mark.parametrize("ua", ["Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; GPTBot/1.2)",
                                "Mozilla/5.0 (compatible; ClaudeBot/1.0; +claudebot@anthropic.com)",
                                "python-requests/2.32", "curl/8.4.0", "",
                                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 HeadlessChrome/129.0 Safari/537.36"])
def test_ai_agents_and_scripts_are_refused_and_recorded(iso_db, auth_on, ua):
    import main
    c = TestClient(main.app, headers={**EDGE, "User-Agent": ua})
    for path in ("/", "/js/01-core.js", "/api/auth/login", "/api/months"):
        assert c.get(path).status_code == 403, path
    kinds = [e["kind"] for e in cg.recent(main._usage_db_path(), 20)]
    assert "bot_ua" in kinds


def test_machine_endpoints_and_the_machine_itself_stay_reachable(iso_db, auth_on):
    import main
    c = TestClient(main.app, headers={**EDGE, "User-Agent": "python-urllib/3.12"})
    assert c.get("/api/meta/build").status_code == 200
    assert c.get("/api/_admin/export-state").status_code == 404        # its own token check answers, not the bot guard
    local = TestClient(main.app, headers={"User-Agent": "python-urllib/3.12"})   # a report render on the machine
    assert local.get("/").status_code == 200


def test_a_browser_driven_by_a_program_is_signed_out_and_held(iso_db, auth_on):
    import main
    c = _signed_in(iso_db, "cg_bot")
    assert c.get("/js/01-core.js").status_code == 200
    r = c.post("/api/sec/signal", json={"w": True})
    assert r.status_code == 200
    assert c.get("/").headers.get("X-Login-Page") == "1"
    assert "cg_bot" in main._cg_blocked
    again = TestClient(main.app, headers={**EDGE, **CHROME})
    assert again.post("/api/auth/login", json={"username": "cg_bot", "password": PW}).status_code == 200
    assert again.get("/js/01-core.js").status_code == 401             # held for 30 minutes even after a new login
    main._cg_blocked.pop("cg_bot", None)


def test_a_sweep_is_counted_by_distinct_paths():
    s = cg.Sweep(window=60)
    for i in range(50):
        n = s.hit("u", f"/api/x/{i}", now=1000 + i * 0.1)           # numbers fold: one shape
    assert n == 1
    for i in range(30):
        n = s.hit("u", f"/api/p{i}", now=1010)
    assert n == 31
    assert s.hit("u", "/api/zz", now=1200) == 1                      # the window slid


def test_security_headers_and_robots():
    import main
    c = TestClient(main.app)
    r = c.get("/robots.txt")
    assert r.text.startswith("User-agent: *\nDisallow: /")
    for k in ("X-Frame-Options", "X-Content-Type-Options", "Referrer-Policy", "X-Robots-Tag", "Content-Security-Policy"):
        assert k in r.headers


def test_bot_ua_keeps_real_browsers():
    for ua in (CHROME["User-Agent"],
               "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1",
               "Mozilla/5.0 (iPhone) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 Line/14.15.0"):
        assert not cg.bot_ua(ua)
