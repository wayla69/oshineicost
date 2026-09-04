"""gen_branch_report.py — one branch, two months, benchmarked against the chain,
on the shared fixture (BranchA: May vs June; June is scaled to 0.8)."""
import sqlite3
import sys
from pathlib import Path

HERE = Path(__file__).parent.resolve()
sys.path.insert(0, str(HERE))

import conftest as _cf  # noqa: E402
import gen_branch_report as G  # noqa: E402


def _db(tmp_path):
    p = tmp_path / "t.db"
    _cf._build_db(str(p))
    con = sqlite3.connect(str(p))
    # the app builds this at startup; the report reads it through effective_unit_cost_sql
    con.executescript("CREATE TABLE IF NOT EXISTS sku_effective_cost(sku_id INTEGER PRIMARY KEY, recipe_cost REAL NOT NULL DEFAULT 0)")
    con.close()
    return p


def test_compute_uses_the_app_money_rules(tmp_path):
    con = sqlite3.connect(f"file:{_db(tmp_path)}?mode=ro", uri=True)
    d = G.compute(con, "BranchA", "2026-05", "2026-06")
    con.close()
    may, jun = d["branch_months"]["2026-05"], d["branch_months"]["2026-06"]
    # BranchA May: tier 10,000 (cost 0 — the ฿99 price on BUFFET is ignored) + B item 500×฿8 + A item 50×฿10
    assert may["revenue"] == 15000 and may["covers"] == 100 and may["bills"] == 25
    assert may["cogs"] == 4500 and may["food_pct"] == 30.0 and may["cost_head"] == 45.0 and may["gp_head"] == 105.0
    # June is the same dataset ×0.8 → identical ratios
    assert jun["revenue"] == 12000 and jun["covers"] == 80 and round(jun["cogs"], 6) == 3600
    assert round(jun["food_pct"], 6) == 30.0 and jun["cost_head"] == 45.0
    # chain = both branches
    assert d["chain_months"]["2026-05"]["revenue"] == 30000 and d["chain_months"]["2026-05"]["covers"] == 200
    assert d["n_branches"] == 2 and d["rank_cost_head"] in (1, 2) and d["n_ranked"] == 2
    assert round(d["share_rev"]["2026-06"], 6) == 50.0
    # drivers: the buffet tier never appears; the B item is the biggest cost
    names = [x["code"] for x in d["drivers"]]
    assert names[0] == "B0001" and "BUFFET" not in names
    assert d["drivers"][0]["cost_head2"] == 3200 / 80 and d["drivers"][0]["share2"] > 85


def test_html_has_five_pages_and_thai_prose(tmp_path):
    con = sqlite3.connect(f"file:{_db(tmp_path)}?mode=ro", uri=True)
    d = G.compute(con, "BranchA", "2026-05", "2026-06")
    con.close()
    h = G.build_html(d, "4 กันยายน 2569", "t.db")
    assert h.count('class="page"') == 5
    assert "สาขาBranchA" in h and "พฤษภาคม 2569" in h and "มิถุนายน 2569" in h
    f = G.findings(d)
    assert len(f["findings"]) >= 4 and f["recommendations"]
    assert any("ต้นทุนอาหาร" in x for x in f["findings"])


def test_unknown_branch_exits(tmp_path):
    import pytest
    con = sqlite3.connect(f"file:{_db(tmp_path)}?mode=ro", uri=True)
    with pytest.raises(SystemExit):
        G.compute(con, "Nowhere", "2026-05", "2026-06")
    con.close()
