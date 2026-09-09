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


def test_cost_head_top60_scope(client):
    # master (default): only BranchA
    dm = client.get("/api/reports/cost-head-top60", params={"month": "2026-05", "scope": "master"}).json()
    assert [b["name"] for b in dm["branches"]] == ["BranchA"]
    assert dm["overview"]["covers"] == 100
    assert dm["scope"] == "master"
    # franchise: only BranchB
    df = client.get("/api/reports/cost-head-top60", params={"month": "2026-05", "scope": "franchise"}).json()
    assert [b["name"] for b in df["branches"]] == ["BranchB"]
    assert df["overview"]["covers"] == 100
    assert df["scope_label"] == "แฟรนไชส์"
    # all: both branches, covers summed
    da = client.get("/api/reports/cost-head-top60", params={"month": "2026-05", "scope": "all"}).json()
    assert sorted(b["name"] for b in da["branches"]) == ["BranchA", "BranchB"]
    assert da["overview"]["covers"] == 200
    assert da["scope_label"] == "ทุกสาขา"
    # unknown scope falls back to master
    db = client.get("/api/reports/cost-head-top60", params={"month": "2026-05", "scope": "bogus"}).json()
    assert db["scope"] == "master"
    # exports honour scope
    x = client.get("/api/reports/cost-head-top60.xlsx", params={"month": "2026-05", "scope": "franchise"})
    assert x.status_code == 200 and "spreadsheetml" in x.headers["content-type"]


# ─── the "เมนูที่ดันต้นทุนขึ้น" driver labels in `analysis.ranked[].drivers` ───
# A bare desc[:20] slice used to cut a dish name mid-word with no ellipsis —
# "(B) แซลมอน ซาชิมิ (1" looked like broken data, not a shortened label.

def test_short_label_leaves_short_names_alone():
    from routers.reports import _short_label
    assert _short_label("(B) กุ้งสด (5 ชิ้น )") == "(B) กุ้งสด (5 ชิ้น )"


def test_short_label_cuts_on_a_word_boundary_with_an_ellipsis():
    from routers.reports import _short_label
    out = _short_label("(B) แซลมอน ซาชิมิ (1 ชิ้น)", n=20)
    assert out.endswith("…")
    assert "(1" not in out          # must not stop mid-parenthetical like the old [:20]
    assert len(out) <= 21           # n chars + the ellipsis mark


def test_short_label_falls_back_to_a_hard_cut_with_no_word_boundary():
    from routers.reports import _short_label
    out = _short_label("a" * 40, n=10)
    assert out == "a" * 10 + "…"
