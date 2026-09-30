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


def test_usage_me_reports_first_time_only_for_a_logged_in_user_with_no_tabs(client, monkeypatch):
    import main
    j = client.get("/api/usage/me").json()
    assert j["username"] == "" and j["first_time"] is False                 # auth off: anonymous, never "first time"
    _seed("adoption_first")
    try:
        monkeypatch.setenv("OSHINEI_AUTH", "1"); monkeypatch.setenv("OSHINEI_AUTH_SECRET", "test-secret-stable")
        c = TestClient(main.app)
        assert c.post("/api/auth/login", json={"username": "adoption_first", "password": auth.DEFAULT_PASSWORD}).status_code == 200
        j = c.get("/api/usage/me").json()
        assert j["username"] == "adoption_first" and j["role"] == "marketing" and j["first_time"] is True and j["tab_hits"] == 0
        assert c.post("/api/usage/tab", json={"tab": "feedback"}).json()["ok"]
        j = c.get("/api/usage/me").json()
        assert j["first_time"] is False and j["tab_hits"] == 1 and j["days_seen"] == 1
    finally:
        monkeypatch.delenv("OSHINEI_AUTH", raising=False)
        _drop("adoption_first")


def test_weekly_adoption_digest_builds_once_per_week_and_sends_when_configured(client, monkeypatch):
    import datetime as dt
    import main
    _seed("adoption_never")
    sent = []
    fake_send = lambda text: (sent.append(text), True)[1]          # noqa: E731
    monkeypatch.setenv("OSHINEI_TG_TOKEN", "t"); monkeypatch.setenv("OSHINEI_TG_CHAT_ID", "c")
    try:
        ucon = sqlite3.connect(str(main._usage_db_path()))
        ucon.execute("DROP TABLE IF EXISTS usage_meta"); ucon.commit(); ucon.close()
        monday = dt.datetime(2026, 9, 7, 8, 30)                     # a Monday on the Thai clock
        assert main._maybe_weekly_adoption_digest(now=dt.datetime(2026, 9, 6, 8, 30), send=fake_send) is None   # Sunday: nothing
        rec = main._maybe_weekly_adoption_digest(now=monday, send=fake_send)
        assert rec and rec["week"] == "2026-W37" and rec["sent"] is True and rec["telegram_configured"] is True
        assert "adoption_never" in rec["never"] and "ยังไม่เคยเข้าใช้" in rec["text"] and "2026-W37" in rec["text"]
        assert len(sent) == 1 and sent[0] == rec["text"]
        assert main._maybe_weekly_adoption_digest(now=monday, send=fake_send) is None        # same week: once
        assert len(sent) == 1
        # the stored record feeds the endpoint; sending now forces a new one
        j = client.get("/api/usage/adoption-digest").json()
        assert j["latest"]["week"] == "2026-W37" and "adoption_never" in j["never"] and j["preview"].startswith("<b>สรุปการเข้าใช้แดชบอร์ด</b>")
        import notify
        monkeypatch.setattr(notify, "send_telegram", fake_send)      # the endpoint sends through notify
        r = client.post("/api/usage/adoption-digest/send").json()
        assert r["ok"] and r["week"] and "adoption_never" in r["never"]
        assert main._usage_meta_get("adoption_digest_week") == r["week"]
        # away: someone who logged in long ago and has not opened a tab since
        people = {"days": 30, "summary": {"accounts": 3, "active": 1, "idle": 1, "never": 1},
                  "users": [{"username": "a", "role": "x", "status": "active", "last_login": "2026-09-06 01:00:00", "last_seen": "2026-09-06", "days_seen": 3},
                            {"username": "b", "role": "x", "status": "idle", "last_login": "2026-08-01 01:00:00", "last_seen": None, "days_seen": 0},
                            {"username": "n", "role": "x", "status": "never", "last_login": None, "last_seen": None, "days_seen": 0}]}
        lists = main._adoption_lists(people, now=monday)
        assert [u["username"] for u in lists["never"]] == ["n"] and [u["username"] for u in lists["away"]] == ["b"] and [u["username"] for u in lists["active"]] == ["a"]
        text = main._adoption_digest_text(people, lists, "2026-W37")
        assert "หายไปเกิน" in text and "b — ล่าสุด 2026-08-01" in text and "ใช้งานอยู่: a (3 วัน)" in text
    finally:
        _drop("adoption_never")


def test_adoption_endpoints_are_admin_gated_when_auth_is_on(client, monkeypatch):
    import main
    monkeypatch.setenv("OSHINEI_AUTH", "1"); monkeypatch.setenv("OSHINEI_AUTH_SECRET", "test-secret-stable")
    try:
        anon = TestClient(main.app)
        assert anon.get("/api/usage/adoption-digest").status_code == 401
        assert anon.post("/api/usage/adoption-digest/send").status_code == 401
        assert anon.get("/api/usage/me").status_code == 401                    # me needs a session too (it describes the caller)
    finally:
        monkeypatch.delenv("OSHINEI_AUTH", raising=False)
