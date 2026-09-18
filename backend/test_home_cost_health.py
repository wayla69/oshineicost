"""The home page's food-cost reading (routers/home_extra._cost_health).

The tile used to judge a buffet against à-la-carte bars (38% / 45%): red every
day, and "ต้นทุนอาหารสูง" the permanent #1 urgent action. These tests pin what
replaced it — the business's own band, the move against last month, and how
complete the costing is — plus the cost work the page now surfaces.
"""
import sqlite3

import core
import rules
from conftest import LATEST_MONTH, PREV_MONTH


def test_band_defaults_to_the_buffet_band_and_is_editable_in_settings(client):
    d = client.get("/api/home/brief").json()["cost_health"]
    assert (d["band_lo"], d["band_hi"]) == rules.FOOD_COST_BAND == (55.0, 70.0)
    assert client.put("/api/settings", json={"food_cost_band_lo": "50", "food_cost_band_hi": "65"}).status_code == 200
    try:
        d = client.get("/api/home/brief").json()["cost_health"]
        assert (d["band_lo"], d["band_hi"]) == (50.0, 65.0)
        # nonsense (lo >= hi) must fall back, never blank the home page
        client.put("/api/settings", json={"food_cost_band_lo": "80", "food_cost_band_hi": "60"})
        d = client.get("/api/home/brief").json()["cost_health"]
        assert (d["band_lo"], d["band_hi"]) == (55.0, 70.0)
    finally:
        con = sqlite3.connect(str(core.DB_PATH))
        con.execute("DELETE FROM app_settings WHERE key LIKE 'food_cost_band_%'")
        con.commit(); con.close()


def test_movement_is_measured_on_one_basis_and_coverage_by_plates(client):
    import routers.home_extra as he
    con = sqlite3.connect(str(core.DB_PATH)); con.row_factory = sqlite3.Row
    try:
        h = he._cost_health(con, LATEST_MONTH, None, 30.0, 24000.0)
    finally:
        con.close()
    # fixture: every month books cogs/rev = 4500/15000 (scaled alike) -> no movement
    assert h["prev_food_pct"] == 30.0 and h["delta_pp"] == 0.0
    # plates: the B dish and the A dish are costed; the BUFFET package line is excluded, not "uncosted"
    assert h["coverage_pct"] == 100.0 and h["uncosted_plates"] == 0
    assert h["requisition"]["missing_month"] == PREV_MONTH          # nothing uploaded for last month


def test_an_uncosted_dish_makes_the_figure_a_floor_and_a_free_dish_gets_a_price(client):
    import routers.home_extra as he
    con = sqlite3.connect(str(core.DB_PATH)); con.row_factory = sqlite3.Row
    try:
        con.execute("INSERT INTO skus(sku_id, sku_code, description, category_id, unit, business_type, active)"
                    " VALUES(801, 'X-UNCOSTED', '(BF) จานไม่มีต้นทุน', 3, 'plate', 'buffet', 1)")
        con.execute("INSERT INTO skus(sku_id, sku_code, description, category_id, unit, business_type, active)"
                    " VALUES(802, 'X-FREE', '(TW) Free หัวปลาแซลมอน', 3, 'plate', 'buffet', 1)")
        con.execute("INSERT INTO sku_cost(sku_id, unit_cost) VALUES(802, 150.0)")
        for sid, qty in ((801, 400), (802, 10)):
            con.execute("INSERT INTO sales_sku_monthly(branch_id, period_month, sku_id, qty, subtotal_ex_vat,"
                        " tax_total, total_inc_vat, source) VALUES(1, ?, ?, ?, 0, 0, 0, 'Easy')", (LATEST_MONTH, sid, qty))
        core._build_sku_effective_cost(con, force=True)
        h = he._cost_health(con, LATEST_MONTH, None, 30.0, 24000.0)
        assert h["uncosted_plates"] == 400 and h["coverage_pct"] < 80
        assert h["est_food_pct"] > 30.0                                  # the fuller, higher number
        assert h["giveaways"]["value"] == 1500 and h["giveaways"]["plates"] == 10
        assert "Free" in h["giveaways"]["top"][0]["name"]
    finally:
        con.execute("DELETE FROM sales_sku_monthly WHERE sku_id IN (801, 802)")
        con.execute("DELETE FROM sku_cost WHERE sku_id = 802")
        con.execute("DELETE FROM skus WHERE sku_id IN (801, 802)")
        con.commit()
        core._build_sku_effective_cost(con, force=True)
        con.close()
