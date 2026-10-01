"""A COST ranking of dishes covers everything that carries a cost (2026-10-01).

The Menu tab's cost Top 60 used the dine-in food filter while the Top-60
cost/head page, BoM รายสาขา and the P&L drill ranked every costed line — the
same question, four lists. sort=cogs now uses rules.needs_cost_sql, so its
total is the P&L's COGS for the same window; qty/revenue stay dine-in food.
"""


def test_cost_ranking_total_is_the_pnl_cogs(client):
    top = client.get("/api/sales/top_ordered", params={"month": "2026-05", "sort": "cogs", "limit": 100}).json()
    pnl = client.get("/api/profit/summary", params={"month": "2026-05"}).json()
    assert top["scope"] == "costed"
    assert abs(top["total_cogs"] - pnl["cogs"]) < 0.5


def test_qty_ranking_stays_dine_in_food(client):
    top = client.get("/api/sales/top_ordered", params={"month": "2026-05", "sort": "qty"}).json()
    assert top["scope"] == "food"
