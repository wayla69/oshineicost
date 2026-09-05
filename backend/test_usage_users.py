# -*- coding: utf-8 -*-
"""The adoption view: usage is counted per user beside the per-tab count, and
GET /api/usage/users joins accounts × login events × tab opens into one row
per person with a status — 'never' (no successful login, still on the shared
default password), 'idle', 'active' — plus anonymous activity when auth is off.
Other test files log the seeded users in, so this one seeds its own account and
reads deltas rather than absolute counts."""
import sqlite3
import sys
from pathlib import Path

from fastapi.testclient import TestClient

HERE = Path(__file__).parent.resolve()
sys.path.insert(0, str(HERE))

import auth  # noqa: E402


def _seed(username, role="marketing"):
    import core
    con = sqlite3.connect(str(core.DB_PATH))
    h = auth.hash_password(auth.DEFAULT_PASSWORD)
    con.execute("DELETE FROM app_user WHERE username=?", (username,))
    con.execute("INSERT INTO app_user(username, pw_hash, pw_salt, role, must_change) VALUES (?,?,?,?,1)",
                (username, h["hash"], h["salt"], role))
    con.commit(); con.close()


def _drop(username):
    import core
    con = sqlite3.connect(str(core.DB_PATH))
    con.execute("DELETE FROM app_user WHERE username=?", (username,))
    con.commit(); con.close()


def _tab_rows(main, user):
    ucon = sqlite3.connect(str(main._usage_db_path()))
    try:
        if not ucon.execute("SELECT 1 FROM sqlite_master WHERE name='usage_user_daily'").fetchone():
            return {}
        return {r[0]: r[1] for r in ucon.execute("SELECT name, SUM(hits) FROM usage_user_daily WHERE user=? AND kind='tab' GROUP BY name", (user,))}
    finally:
        ucon.close()


def test_users_view_flags_never_and_active(client, monkeypatch):
    import main
    _seed("adoption_probe")
    try:
        # auth off: the beacon is anonymous; an account that never logged in is 'never'
        assert client.post("/api/usage/tab", json={"tab": "home"}).json()["ok"]
        j = client.get("/api/usage/users").json()
        by = {u["username"]: u for u in j["users"]}
        assert j["days"] == 30 and j["summary"]["accounts"] >= 13
        p = by["adoption_probe"]
        assert p["status"] == "never" and p["logins"] == 0 and p["tab_hits"] == 0 and p["last_login"] is None and p["must_change"] == 1
        anon = [u for u in j["users"] if u["status"] == "anonymous"]
        assert anon and anon[0]["tab_hits"] >= 1 and any(t["tab"] == "home" for t in anon[0]["top_tabs"])
        assert j["summary"]["never"] >= 1
        assert j["summary"]["accounts"] == sum(1 for u in j["users"] if u["status"] != "anonymous")
        # auth on: a login + tab beacons become that person's row (deltas — other files use these accounts too)
        before = _tab_rows(main, "management1")
        monkeypatch.setenv("OSHINEI_AUTH", "1"); monkeypatch.setenv("OSHINEI_AUTH_SECRET", "test-secret-stable")
        c = TestClient(main.app)
        assert c.post("/api/auth/login", json={"username": "management1", "password": auth.DEFAULT_PASSWORD}).status_code == 200
        assert c.post("/api/auth/login", json={"username": "adoption_probe", "password": "wrong-password"}).status_code == 401
        for tab in ("home", "home", "discounts"):
            assert c.post("/api/usage/tab", json={"tab": tab}).json()["ok"]
        c.get("/api/months")                                       # a human API hit
        j = c.get("/api/usage/users").json()
        by = {u["username"]: u for u in j["users"]}
        m = by["management1"]
        assert m["status"] == "active" and m["logins"] >= 1 and m["last_login"] and m["days_seen"] >= 1
        after = _tab_rows(main, "management1")
        assert after.get("home", 0) - before.get("home", 0) == 2 and after.get("discounts", 0) - before.get("discounts", 0) == 1
        assert m["tab_hits"] >= 3 and any(t["tab"] == "home" for t in m["top_tabs"]) and m["api_hits"] >= 1
        pr = by["adoption_probe"]
        assert pr["failed"] >= 1 and pr["logins"] == 0 and pr["status"] == "never"      # a failed attempt is not a login
        assert j["summary"]["active"] >= 1
        # the view is admin-gated when auth is on (per-person login times are personal data)
        anon_client = TestClient(main.app)
        assert anon_client.get("/api/usage/users").status_code == 401
    finally:
        monkeypatch.delenv("OSHINEI_AUTH", raising=False)
        _drop("adoption_probe")


def test_users_view_survives_missing_tables(client):
    """A fresh DB (no per-user usage yet) still answers with the accounts."""
    import main
    ucon = sqlite3.connect(str(main._usage_db_path()))
    ucon.execute("DROP TABLE IF EXISTS usage_user_daily"); ucon.commit(); ucon.close()
    j = client.get("/api/usage/users", params={"days": 7}).json()
    assert j["days"] == 7 and j["summary"]["accounts"] >= 12
    assert all(u["tab_hits"] == 0 for u in j["users"])
