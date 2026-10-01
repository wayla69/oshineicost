"""Week 4 "one answer per question" fixes that change what a page SAYS (2026-10-01).

The cost/head page read only the rolling daily table, so any period before it
showed "฿0.0/head" against real covers. A whole month now falls back to the
monthly table (as the management report does); a day before the window says
"unknown" (null) instead of zero.
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
def early(tmp_path_factory):
    """May has bills and monthly item rows but NO daily item rows — i.e. it sits
    before the rolling daily window, as every month before 2026-03-22 does live."""
    db = tmp_path_factory.mktemp("w4") / "test.db"
    _cf._build_db(str(db))
    con = sqlite3.connect(str(db))
    con.execute("DELETE FROM sales_sku_daily WHERE day < '2026-06-01'")
    con.commit(); con.close()
    import core
    prev = core.DB_PATH
    core.DB_PATH = db
    main.DB_PATH = db
    main._tls.con = None
    try: main.invalidate_cache()
    except Exception: pass
    try:
        with TestClient(main.app) as c:
            yield c
    finally:
        core.DB_PATH = prev
        main.DB_PATH = prev
        main._tls.con = None
        try: main.invalidate_cache()
        except Exception: pass


def test_month_before_the_daily_window_falls_back_to_monthly(early):
    d = early.get("/api/sales/cost-per-head", params={"date": "2026-05-02", "gran": "month"}).json()
    assert d["cost_known"] is True
    assert d["total"]["customers"] > 0
    assert d["total"]["cost_per_head"] and d["total"]["cost_per_head"] > 0


def test_day_before_the_daily_window_is_unknown_not_zero(early):
    d = early.get("/api/sales/cost-per-head", params={"date": "2026-05-02", "gran": "day"}).json()
    assert d["cost_known"] is False
    assert d["total"]["customers"] > 0                 # covers are real...
    assert d["total"]["cost_per_head"] is None         # ...the cost is unknown, not ฿0
    assert all(r["cost_per_head"] is None and r["delta_pct"] is None for r in d["branches"])
