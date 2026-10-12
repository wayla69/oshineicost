"""Every page prices a sales line at the cost of ITS month (one answer per question).

PR #386 made the management report's gross profit read the released BoM Center cost the
way the P&L does (rules.effective_unit_cost_sql with a month). Five more places still
multiplied by the undated cost (sku_effective_cost), so from September 2026 they ignored
the released cost:

  - the day-range menu compare (routers/sales.py, _menucmp_agg)
  - the estimated food cost % on the home page (routers/home_extra.py, _cost_health)
  - the giveaways valuation on the home page (same function)
  - the live per-bill COGS of the discount bills (routers/analysis_discounts.py)
  - the per-bill drill (routers/reports.py, pnl_bill_items)

The fixture is a tiny DB with three dishes, all sold in August and September 2026:

  sku 4  "(B) dated dish"  sku_cost 12 before the cut-over, released at 40 from 2026-09
  sku 5  "(B) uncosted"    no cost at all (gives the home estimate something to fill)
  sku 6  "Free soup"       sku_cost 3, released at 9 from 2026-09 (a giveaway by its name)

August (before the cut-over) must keep the old chain byte for byte.
"""
import os
os.environ["OSHINEI_NO_SWAP"] = "1"
import sqlite3
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

HERE = Path(__file__).parent.resolve()
sys.path.insert(0, str(HERE))
import main  # noqa: E402
from test_mgt_months import _build, _restore_db, _swap_db  # noqa: E402

DISH_OLD, DISH_NEW = 12.0, 40.0
FREE_OLD, FREE_NEW = 3.0, 9.0


@pytest.fixture(scope="module")
def dated(tmp_path_factory):
    db = tmp_path_factory.mktemp("datedsites") / "test.db"
    _build(db, with_sales=False)
    prev = _swap_db(db)
    try:
        with TestClient(main.app) as c:
            from bom_center.dated import DDL
            con = sqlite3.connect(str(db))
            con.execute(DDL)
            con.execute("DELETE FROM bc_sku_cost_month")
            con.executemany("INSERT INTO skus(sku_id,sku_code,description,category_id,unit,business_type,active)"
                            " VALUES(?,?,?,?,?,?,1)",
                            [(4, "A0004", "(B) dated dish", 3, "plate", "buffet"),
                             (5, "A0005", "(B) uncosted", 3, "plate", "buffet"),
                             (6, "A0006", "Free soup", 3, "plate", "buffet")])
            con.executemany("INSERT INTO sku_cost(sku_id,unit_cost) VALUES(?,?)", [(4, DISH_OLD), (6, FREE_OLD)])
            con.executemany("INSERT INTO bc_sku_cost_month(sku_id, month, unit_cost, release_id) VALUES(?,?,?,1)",
                            [(4, "2026-09", DISH_NEW), (6, "2026-09", FREE_NEW)])
            for month, day in (("2026-08", "2026-08-15"), ("2026-09", "2026-09-15")):
                for sid, qty in ((4, 10), (5, 5), (6, 4)):
                    con.execute("INSERT INTO sales_sku_daily VALUES(1,?,?,?,?)", (day, sid, qty, 500.0))
                    con.execute("INSERT INTO sales_sku_monthly(branch_id,period_month,sku_id,qty,subtotal_ex_vat,tax_total,"
                                "total_inc_vat,source) VALUES(1,?,?,?,500.0,35.0,535.0,'Easy')", (month, sid, qty))
            # one discounted bill a month, two plates of sku 4 on each
            con.execute("CREATE TABLE IF NOT EXISTS sale_bill_item(receipt_no TEXT, sku_id INTEGER, qty REAL, rev REAL, bom_cost REAL)")
            for rc, at in (("R-AUG", "2026-08-15T12:00:00"), ("R-SEP", "2026-09-15T12:00:00")):
                con.execute("INSERT INTO sales_transaction(branch_id,receipt_no,sold_at,customer_count,food_subtotal,"
                            "drink_subtotal,other_subtotal,sp_discount,discount,service_charge,total_inc_vat,source)"
                            " VALUES(1,?,?,4,1000,0,0,0,50,0,1000,'Easy')", (rc, at))
                con.execute("INSERT INTO sale_bill_item VALUES(?,4,2,100,0)", (rc,))
            con.commit()
            con.close()
            main._tls.con = None
            main.invalidate_cache()
            yield c
    finally:
        _restore_db(prev)


def _cogs_a(c, **q):
    r = c.get("/api/sales/menu-compare", params=q)
    assert r.status_code == 200, r.text
    return {i["sku_code"]: i["cogs_a"] for i in r.json()["items"]}


def test_day_range_menu_compare_prices_each_day_at_its_month(dated):
    """The day range read the undated cost: September's dish showed 10 x 12 where the month
    range (and the P&L) showed 10 x 40. August stays on the old chain."""
    c = dated
    sep_day = _cogs_a(c, a_date_from="2026-09-01", a_date_to="2026-09-30")
    sep_month = _cogs_a(c, a_from="2026-09", a_to="2026-09")
    assert sep_day["A0004"] == pytest.approx(10 * DISH_NEW) == pytest.approx(sep_month["A0004"])
    assert sep_day["A0006"] == pytest.approx(4 * FREE_NEW) == pytest.approx(sep_month["A0006"])
    aug_day = _cogs_a(c, a_date_from="2026-08-01", a_date_to="2026-08-31")
    aug_month = _cogs_a(c, a_from="2026-08", a_to="2026-08")
    assert aug_day["A0004"] == pytest.approx(10 * DISH_OLD) == pytest.approx(aug_month["A0004"])
    # a range that crosses the cut-over prices every day at its own month
    both = _cogs_a(c, a_date_from="2026-08-01", a_date_to="2026-09-30")
    assert both["A0004"] == pytest.approx(10 * DISH_OLD + 10 * DISH_NEW)
    # the dish nobody costed stays at 0 on either path
    assert sep_day.get("A0005", 0) == 0 == sep_month.get("A0005", 0)


def test_home_estimate_and_giveaways_use_the_released_cost(dated):
    import core
    from routers.home_extra import _cost_health
    with core.get_db() as con:
        sep = _cost_health(con, "2026-09", None, 50.0, 1000.0)
        aug = _cost_health(con, "2026-08", None, 50.0, 1000.0)
    # est. food % = food % + uncosted plates x (cost of the costed plates / their count) / revenue
    assert sep["coverage_pct"] is not None and sep["uncosted_plates"] == 5
    assert sep["est_food_pct"] == pytest.approx(50.0 + 5 * (10 * DISH_NEW + 4 * FREE_NEW) / 14 / 1000.0 * 100, abs=0.06)
    assert aug["est_food_pct"] == pytest.approx(50.0 + 5 * (10 * DISH_OLD + 4 * FREE_OLD) / 14 / 1000.0 * 100, abs=0.06)
    assert sep["giveaways"]["value"] == round(4 * FREE_NEW)
    assert aug["giveaways"]["value"] == round(4 * FREE_OLD)


def test_the_bill_drill_and_the_discount_bills_price_a_bill_at_the_month_it_was_sold(dated):
    c = dated
    for rc, unit in (("R-AUG", DISH_OLD), ("R-SEP", DISH_NEW)):
        d = c.get("/api/reports/pnl-bill-items", params={"receipt": rc}).json()
        line = {i["sku_code"]: i for i in d["items"]}["A0004"]
        assert line["unit_cost"] == pytest.approx(unit) and line["cogs"] == pytest.approx(2 * unit)
        assert d["header"]["cogs"] == round(2 * unit)
    for month, rc, unit in (("2026-08", "R-AUG", DISH_OLD), ("2026-09", "R-SEP", DISH_NEW)):
        bills = c.get("/api/analysis/discount_bills", params={"month": month}).json()["bills"]
        assert {b["receipt_no"]: b["cogs_bom"] for b in bills}[rc] == round(2 * unit)


def test_a_buffet_tier_line_stays_free_on_every_dated_site(dated):
    """The tier rules ride along: a package line is never costed, released cost or not."""
    c = dated
    con = sqlite3.connect(str(main.DB_PATH))
    try:
        con.execute("INSERT OR IGNORE INTO bc_sku_cost_month(sku_id, month, unit_cost, release_id) VALUES(1, '2026-09', 99.0, 1)")
        con.execute("INSERT INTO sale_bill_item VALUES('R-SEP', 1, 3, 300, 0)")      # the BUFFET tier on the same bill
        con.execute("INSERT INTO sales_sku_daily VALUES(1,'2026-09-16',1,7,700.0)")
        con.commit()
    finally:
        con.close()
    main._tls.con = None
    main.invalidate_cache()
    d = c.get("/api/reports/pnl-bill-items", params={"receipt": "R-SEP"}).json()
    assert {i["sku_code"]: i["cogs"] for i in d["items"]}["BUFFET"] == 0
    assert d["header"]["cogs"] == round(2 * DISH_NEW)
    bills = c.get("/api/analysis/discount_bills", params={"month": "2026-09"}).json()["bills"]
    assert {b["receipt_no"]: b["cogs_bom"] for b in bills}["R-SEP"] == round(2 * DISH_NEW)
    import core
    from routers.sales import _menucmp_agg
    with core.get_db() as con2:
        rows = _menucmp_agg(con2, "sk.sku_code code", "sk.sku_code", ["b.name!='ALL'"], {}, None, None,
                            "2026-09-01", "2026-09-30")
    assert {r["code"]: r["cogs"] for r in rows}["BUFFET"] == 0
