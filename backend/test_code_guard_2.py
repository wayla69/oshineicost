# -*- coding: utf-8 -*-
"""Code guard 2 (owner 2026-10-28): the shared default password never signs in on the live server, a handed-out
account gets its own temporary password, the accounts still on the default move to their own (rotate script),
a branch login that sweeps the API is held, and the code a branch reads names no password."""
import itertools
import os
import sqlite3
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import auth
import core
import rotate_default_passwords as rot

ROOT = Path(__file__).resolve().parent.parent
_GEN = itertools.count(400_000)
HDR = {"Fly-Client-IP": "203.0.113.9",
       "User-Agent": "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0 Mobile Safari/537.36"}


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
    auth._LOGIN_FAILS.clear()
    yield dst
    core.invalidate_cache(rebuild_cost=False)
    auth._LOGIN_FAILS.clear()


def _con(db):
    c = sqlite3.connect(str(db)); c.row_factory = sqlite3.Row
    return c


def _login(tc, user, pw):
    return tc.post("/api/auth/login", json={"username": user, "password": pw})


def test_temp_passwords_are_random_typeable_and_pass_the_policy():
    seen = {auth.temp_password() for _ in range(200)}
    assert len(seen) == 200
    for pw in seen:
        auth.check_password_policy(pw)
        assert set(pw.replace("-", "")) <= set(auth._TEMP_ALPHABET)


def test_the_shared_default_never_signs_in_on_the_live_server(iso_db, auth_on, monkeypatch):
    import main
    c = _con(iso_db)
    auth.provision_user(c, "cg2_mgr", "management", password="Strong-Pass-2026")
    h = auth.hash_password(auth.DEFAULT_PASSWORD)
    c.execute("UPDATE app_user SET pw_hash=?, pw_salt=? WHERE username='cg2_mgr'", (h["hash"], h["salt"]))
    c.commit(); c.close()
    tc = TestClient(main.app, headers=HDR)
    monkeypatch.setenv("FLY_APP_NAME", "oshinei-dashboard")
    r = _login(tc, "cg2_mgr", auth.DEFAULT_PASSWORD)
    assert r.status_code == 401 and "ยกเลิก" in r.json()["detail"]
    monkeypatch.delenv("FLY_APP_NAME")                       # a local DB / the tests still sign in with the seed
    auth._LOGIN_FAILS.clear()
    assert _login(tc, "cg2_mgr", auth.DEFAULT_PASSWORD).status_code == 200


def test_admin_create_and_reset_answer_a_temp_password_once(iso_db, auth_on):
    import main
    c = _con(iso_db)
    auth.provision_user(c, "cg2_admin", "management", password="Strong-Pass-2026")
    c.execute("UPDATE app_user SET must_change=0 WHERE username='cg2_admin'"); c.commit(); c.close()
    tc = TestClient(main.app, headers=HDR)
    assert _login(tc, "cg2_admin", "Strong-Pass-2026").status_code == 200
    r = tc.post("/api/auth/users", json={"username": "cg2_new", "role": "marketing"})
    assert r.status_code == 200, r.text
    pw = r.json()["temp_password"]
    assert pw and pw != auth.DEFAULT_PASSWORD and "default_password" not in r.json()
    r = tc.put("/api/auth/users/cg2_new", json={"reset_default": True})
    pw2 = r.json()["temp_password"]
    assert pw2 and pw2 != pw
    other = TestClient(main.app, headers=HDR)
    assert _login(other, "cg2_new", pw).status_code == 401
    assert _login(other, "cg2_new", pw2).status_code == 200


def test_rotate_moves_every_active_default_account_to_its_own(tmp_path):
    db = tmp_path / "r.db"
    c = _con(db)
    auth.init_auth(c)
    h = auth.hash_password(auth.DEFAULT_PASSWORD)
    for u, role, active in (("wh_aaa", "branch", 1), ("own_aaa", "franchise", 1), ("wh_old", "branch", 0)):
        c.execute("INSERT INTO app_user(username,pw_hash,pw_salt,role,active,must_change) VALUES (?,?,?,?,?,1)",
                  (u, h["hash"], h["salt"], role, active))
    auth.provision_user(c, "mine", "analyst", password="Own-Pass-2026x")
    c.commit()
    assert [r["username"] for r in rot.on_default(c)] == ["wh_aaa", "own_aaa"] or \
        sorted(r["username"] for r in rot.on_default(c)) == ["own_aaa", "wh_aaa"]
    done = rot.rotate(c)
    assert sorted(u for u, *_ in done) == ["own_aaa", "wh_aaa"]
    for u, _role, _b, pw in done:
        assert auth.authenticate(c, u, pw) is not None
        assert auth.authenticate(c, u, auth.DEFAULT_PASSWORD) is None
        assert auth.get_user(c, u)["must_change"] == 1
    assert auth.authenticate(c, "mine", "Own-Pass-2026x") is not None   # an own password is untouched
    assert rot.on_default(c) == []
    c.close()


def test_a_branch_login_that_sweeps_the_api_is_held(iso_db, auth_on, monkeypatch):
    import main
    import code_guard as cg
    c = _con(iso_db)
    name = c.execute("SELECT name FROM branches ORDER BY branch_id LIMIT 1").fetchone()[0]
    auth.provision_user(c, "cg2_wh", "branch", branch=name, password="Strong-Pass-2026")
    c.execute("UPDATE app_user SET must_change=0 WHERE username='cg2_wh'"); c.commit(); c.close()
    monkeypatch.setattr(cg, "SWEEP_LIMIT_BRANCH", 5)
    monkeypatch.setattr(main, "_cg_sweep", cg.Sweep())
    main._cg_blocked.pop("cg2_wh", None)
    tc = TestClient(main.app, headers=HDR)
    assert _login(tc, "cg2_wh", "Strong-Pass-2026").status_code == 200
    codes = [tc.get(f"/api/cg2-probe-{i}").status_code for i in range(8)]
    assert codes[-1] == 401 and 401 not in codes[:5]
    assert tc.get("/api/auth/me").json().get("authenticated") is not True or tc.get("/js/01-core.js").status_code == 401
    main._cg_blocked.pop("cg2_wh", None)


def test_no_shipped_page_or_code_names_the_default_password():
    files = list((ROOT / "js").glob("*.js")) + list((ROOT / "tabs").glob("*.html")) + \
        [ROOT / "index.html", ROOT / "login.html", ROOT / "sw.js"]
    leaks = [f.name for f in files if auth.DEFAULT_PASSWORD in f.read_text(encoding="utf-8")]
    assert not leaks, leaks


def test_the_login_page_carries_the_use_notice():
    html = (ROOT / "login.html").read_text(encoding="utf-8")
    assert "ห้ามใช้โปรแกรมอัตโนมัติหรือ AI" in html and "การใช้งานทุกครั้งถูกบันทึก" in html
