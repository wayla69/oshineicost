"""Tests for the Top-60 menu cost-per-head report (master branches).

Endpoint: /api/reports/cost-head-top60 (+ .xlsx / .print exports).
Uses the shared hermetic fixture (conftest): master BranchA + franchise BranchB,
months 2026-05 (scale 1.0) and 2026-06 (scale 0.8). Per branch/month the seeded
COGS = B item 500*scale*8 + A item 50*scale*10 (tier cost 0); covers = 100*scale.
"""
import pytest


def test_cost_head_top60_ties_out(client):
    r = client.get("/api/reports/cost-head-top60", params={"month": "2026-05"})
    assert r.status_code == 200
    d = r.json()
    assert d["month"] == "2026-05"
    ov = d["overview"]
    assert ov is not None
    # MASTER-ONLY: only BranchA counts → covers 100 (NOT 200 incl. franchise)
    assert ov["covers"] == 100
    # tier SKU has cost 0 → excluded; only B + A items are costed
    assert ov["menu_count"] == 2
    # COGS = 500*8 (B) + 50*10 (A) = 4500 → cph 45.0 ; food% = 4500/15000 = 30.0
    assert ov["cph_total"] == pytest.approx(45.0)
    assert ov["food_pct"] == pytest.approx(30.0)
    # ranked by COGS desc: B แซลมอน (cph 40) before A สเต๊ก (cph 5)
    assert [m["cph"] for m in ov["menus"]] == [pytest.approx(40.0), pytest.approx(5.0)]
    assert ov["menus"][0]["prem"] is True   # แซลมอน flagged premium


def test_cost_head_top60_master_only_and_variance(client):
    d = client.get("/api/reports/cost-head-top60", params={"month": "2026-05"}).json()
    names = [b["name"] for b in d["branches"]]
    assert names == ["BranchA"]                 # franchise BranchB excluded
    b = d["branches"][0]
    assert b["covers"] == 100
    assert b["cph_total"] == pytest.approx(45.0)
    assert b["gap"] == pytest.approx(0.0)       # single master == overview
    # every branch menu carries the overview comparison columns
    for m in b["menus"]:
        assert "o_cph" in m and "d" in m
        assert m["d"] == pytest.approx(0.0)     # one master → no deviation
    assert d["analysis"]["hottest"] == "BranchA"


def test_cost_head_top60_default_month_has_data(client):
    # No month → latest month WITH master covers (2026-06 here), not an empty month.
    d = client.get("/api/reports/cost-head-top60").json()
    assert d["month"] == "2026-06"
    assert d["overview"] is not None
    assert d["overview"]["covers"] == 80        # 100 * 0.8


def test_cost_head_top60_exports(client):
    x = client.get("/api/reports/cost-head-top60.xlsx", params={"month": "2026-05"})
    assert x.status_code == 200
    assert "spreadsheetml" in x.headers["content-type"]
    assert len(x.content) > 1000                # a real workbook

    h = client.get("/api/reports/cost-head-top60.print", params={"month": "2026-05"})
    assert h.status_code == 200
    assert "text/html" in h.headers["content-type"]
    assert "ต้นทุน/หัว" in h.text
