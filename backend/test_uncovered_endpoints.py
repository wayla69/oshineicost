# -*- coding: utf-8 -*-
"""The six endpoints no test referenced (structural audit closeout, 2026-09-05):
hourly_open, role-permissions (GET + PUT, admin-gated), bom-cost/recipe,
bom/composition, cost/suggestions.csv and market (with its network fetchers
stubbed). Each pins the contract the frontend relies on, not the numbers."""
import sqlite3
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

HERE = Path(__file__).parent.resolve()
sys.path.insert(0, str(HERE))

import auth  # noqa: E402


def test_hourly_open_marks_closed_hours(client):
    j = client.get("/api/analysis/hourly_open", params={"month": "2026-05"}).json()
    rows = j["hours"] if isinstance(j, dict) and "hours" in j else j
    assert isinstance(rows, list) and rows, j
    assert {"hour", "is_open"} <= set(rows[0].keys())
    assert all(r["is_open"] in (0, 1, True, False) for r in rows)
    assert len({r["hour"] for r in rows}) == len(rows)          # one row per hour, no duplicates


def test_role_permissions_roundtrip_and_admin_gate(client, monkeypatch):
    # auth off (local dev): open, and the shape the Settings UI renders
    j = client.get("/api/auth/role-permissions").json()
    assert j["roles"] == auth.ROLES and j["modules"] == auth.MODULES
    assert set(j["permissions"]) >= set(auth.ROLES)
    before = j["permissions"]["analyst"]
    try:
        r = client.put("/api/auth/role-permissions", json={"permissions": {**j["permissions"], "analyst": ["sales.overview"]}})
        assert r.status_code == 200 and r.json() == {"ok": True}
        assert client.get("/api/auth/role-permissions").json()["permissions"]["analyst"] == ["sales.overview"]
        # auth on: anonymous is refused, a non-admin role is refused, management passes
        monkeypatch.setenv("OSHINEI_AUTH", "1"); monkeypatch.setenv("OSHINEI_AUTH_SECRET", "test-secret-stable")
        import main
        anon = TestClient(main.app)
        assert anon.get("/api/auth/role-permissions").status_code == 401
        staff = TestClient(main.app)
        assert staff.post("/api/auth/login", json={"username": "analyst1", "password": auth.DEFAULT_PASSWORD}).status_code == 200
        assert staff.get("/api/auth/role-permissions").status_code == 403
        assert staff.put("/api/auth/role-permissions", json={"permissions": j["permissions"]}).status_code == 403
        boss = TestClient(main.app)
        assert boss.post("/api/auth/login", json={"username": "management1", "password": auth.DEFAULT_PASSWORD}).status_code == 200
        assert boss.get("/api/auth/role-permissions").status_code == 200
    finally:
        monkeypatch.delenv("OSHINEI_AUTH", raising=False)
        client.put("/api/auth/role-permissions", json={"permissions": {**j["permissions"], "analyst": before}})


_COLS = "sku_id, seq, ingredient_code, ingredient_name, qty_per_serving, unit, price_per_unit, ingredient_cost, ingredient_type, source_file"


def _recipe_rows(sid):
    """Whatever recipe the shared fixture DB holds for the SKU right now (other
    test files seed one), so the test can clear it and put it back."""
    import core
    con = sqlite3.connect(str(core.DB_PATH))
    rows = con.execute(f"SELECT {_COLS} FROM bom_ingredients WHERE sku_id=?", (sid,)).fetchall()
    con.close()
    return rows


def _set_recipe(sid, rows):
    import core
    con = sqlite3.connect(str(core.DB_PATH))
    con.execute("DELETE FROM bom_ingredients WHERE sku_id=?", (sid,))
    con.executemany(f"INSERT INTO bom_ingredients({_COLS}) VALUES (?,?,?,?,?,?,?,?,?,?)", rows)
    con.commit(); con.close()
    core.invalidate_cache()


def test_bom_recipe_and_composition(client):
    import core
    assert client.get("/api/bom-cost/recipe/NOPE").status_code == 404
    assert client.get("/api/bom/composition", params={"sku": "NOPE"}).status_code == 404
    con = sqlite3.connect(str(core.DB_PATH))
    sid = con.execute("SELECT sku_id FROM skus WHERE sku_code='B0001'").fetchone()[0]
    con.close()
    saved = _recipe_rows(sid)
    try:
        _set_recipe(sid, [])
        j = client.get("/api/bom-cost/recipe/B0001").json()
        assert j == {"sku_code": "B0001", "has_recipe": False, "ingredients": []}   # a SKU without a recipe says so
        _set_recipe(sid, [(sid, 1, "SAL", "แซลมอน", 0.05, "kg", 400.0, 20.0, "main", "test"),
                          (sid, 2, "RIC", "ข้าว", 0.02, "kg", 30.0, 0.6, "side", "test")])
        j = client.get("/api/bom-cost/recipe/B0001").json()
        assert j["has_recipe"] and j["total_cost"] == pytest.approx(20.6) and [i["ingredient_name"] for i in j["ingredients"]] == ["แซลมอน", "ข้าว"]
        c = client.get("/api/bom/composition", params={"sku": "b0001"})             # case-insensitive
        assert c.status_code == 200
        body = c.json()
        assert body.get("sku_code") == "B0001"
        names = {i.get("ingredient_name") or i.get("name") for i in body.get("ingredients") or body.get("items") or []}
        assert "แซลมอน" in names
    finally:
        _set_recipe(sid, saved)


def test_cost_suggestions_csv_is_a_csv(client):
    r = client.get("/api/cost/suggestions.csv", params={"limit": 5})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    lines = r.text.lstrip("﻿").splitlines()
    assert lines and "sku_code" in lines[0].lower()                                    # header row


def test_market_serves_curated_context_without_the_network(client, monkeypatch):
    import main
    monkeypatch.setattr(main, "_fetch_market_news", lambda: ([{"title": "t", "link": "http://x", "published": "2026-09-01"}], "ok"))
    monkeypatch.setattr(main, "_fetch_market_quotes", lambda: ({"USDTHB": {"price": 36.1, "prev": 36.0}}, "ok"))
    j = client.get("/api/market").json()
    assert j["as_of"] == main.MARKET_AS_OF
    assert j["news"][0]["title"] == "t" and j["news_status"] == "ok"
    assert j["quotes"]["USDTHB"]["price"] == 36.1
    # the fetchers failing must not break the page: it degrades to the curated figures
    monkeypatch.setattr(main, "_fetch_market_news", lambda: ([], "unavailable"))
    monkeypatch.setattr(main, "_fetch_market_quotes", lambda: ({}, "unavailable"))
    j = client.get("/api/market").json()
    assert j["news"] == [] and j["news_status"] == "unavailable"
