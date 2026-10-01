"""The Management report's gross profit subtracts the cost of EVERYTHING sold.

Its revenue is every bill (drinks, desserts, delivery included), but until
2026-10-01 the cost it subtracted was only the dine-in food dishes behind the
cost/head list. September 2026 read GP 51.5% against a true 43.3% — ฿7.25M of
COGS missing, and 8 points off the P&L for the same month. The dish list keeps
its food-only scope; only the GP line changes.
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
import conftest as _cf  # noqa: E402


@pytest.fixture(scope="module")
def mgt(tmp_path_factory):
    db = tmp_path_factory.mktemp("mgtgp") / "test.db"
    _cf._build_db(str(db))
    import core
    prev = core.DB_PATH
    core.DB_PATH = db
    main.DB_PATH = db
    main._tls.con = None
    try: main.invalidate_cache()
    except Exception: pass
    try:
        with TestClient(main.app) as c:
            yield c, db
    finally:
        core.DB_PATH = prev
        main.DB_PATH = prev
        main._tls.con = None
        try: main.invalidate_cache()
        except Exception: pass


def _monthly(c):
    r = c.get("/api/reports/management", params={"period": "monthly", "month": "2026-05"})
    assert r.status_code == 200, r.text
    return r.json()


def test_gp_includes_drink_cost_but_dish_list_stays_food(mgt):
    c, db = mgt
    before = _monthly(c)
    assert before["gp"]["cost_basis"] == "daily"
    con = sqlite3.connect(str(db))
    con.execute("INSERT INTO categories(category_id, name) VALUES(901, 'SOFT DRINK')")
    con.execute("INSERT INTO skus(sku_id, sku_code, description, category_id, unit)"
                " VALUES(901, 'DRK901', 'Pepsi', 901, 'glass')")
    con.execute("INSERT INTO sku_cost(sku_id, unit_cost) VALUES(901, 10.0)")
    days = [r[0] for r in con.execute(
        "SELECT DISTINCT day FROM sales_sku_daily WHERE day LIKE '2026-05-%' ORDER BY day")]
    for d in days:
        con.execute("INSERT INTO sales_sku_daily VALUES(1, ?, 901, 3, 0)", (d,))
    con.commit()
    con.close()
    main._tls.con = None
    main.invalidate_cache()
    after = _monthly(c)
    added = 10.0 * 3 * len(days)
    assert added > 0
    assert after["gp"]["food_cost"] == pytest.approx(before["gp"]["food_cost"] + added, abs=1)
    assert after["gp"]["gp"] == pytest.approx(before["gp"]["gp"] - added, abs=1)
    # the cost/head dish list is dine-in food only: a drink does not move it
    assert after["menu"]["total_cph"] == before["menu"]["total_cph"]
