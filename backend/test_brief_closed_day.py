"""The Daily Brief (and every "latest day" home tile) describes the latest
FINISHED day, never the day still trading.

The live sync runs at 06:00 / 12:00 / 16:00, so from noon the newest day in the
database is today with a few hours of bills. Anchored on MAX(sold_at) the brief
headlined that sliver as "yesterday" — a chain-wide -99.9% (owner 2026-10-02).
rules.closed_day_clause pins the anchor to Thai today - 1 or earlier.

Built relative to the real Thai today (the clause is SQL date('now','+7 hours')).
"""
import os
os.environ["OSHINEI_NO_SWAP"] = "1"
import calendar
import datetime as dt
import sqlite3
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

HERE = Path(__file__).parent.resolve()
sys.path.insert(0, str(HERE))
import main  # noqa: E402
import conftest as _cf  # noqa: E402

TH_TODAY = _cf.bkk_today()   # the app's Bangkok day (rules.TH_TODAY_SQL) at the session's instant
pytestmark = pytest.mark.usefixtures("bkk_frozen")   # SQLite 'now' = the instant TH_TODAY is built from
YESTERDAY = (TH_TODAY - dt.timedelta(days=1)).isoformat()


@pytest.fixture(scope="module")
def live_client(tmp_path_factory):
    """Previous month complete, current month through TODAY (today = the open day)."""
    db = tmp_path_factory.mktemp("closed") / "test.db"
    _cf._build_db(str(db))
    con = sqlite3.connect(str(db))
    con.execute("DELETE FROM sales_transaction")
    con.execute("DELETE FROM sales_sku_daily")
    pm = (TH_TODAY.replace(day=1) - dt.timedelta(days=1)).replace(day=1)
    days = [pm.replace(day=i) for i in range(1, calendar.monthrange(pm.year, pm.month)[1] + 1)]
    days += [TH_TODAY.replace(day=i) for i in range(1, TH_TODAY.day + 1)]
    for day in days:
        ds = day.isoformat()
        # today holds a single early bill — the "sliver" that used to be headlined
        n = 1 if day == TH_TODAY else 10
        for bid in (1, 2):
            for k in range(n):
                con.execute(
                    "INSERT INTO sales_transaction(branch_id,receipt_no,sold_at,customer_count,"
                    "food_subtotal,drink_subtotal,other_subtotal,sp_discount,discount,"
                    "service_charge,total_inc_vat,source) VALUES(?,?,?,?,?,?,?,?,?,?,?,'Easy')",
                    (bid, f"{ds}-{bid}-{k}", f"{ds}T12:{k:02d}:00", 2,
                     300.0, 0.0, 0.0, 0.0, 0.0, 0.0, 321.0))
            con.execute("INSERT INTO sales_sku_daily VALUES(?,?,?,?,?)", (bid, ds, 3, 5 * n, 50.0 * n))
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


def test_brief_headlines_the_last_finished_day(live_client):
    j = live_client.get("/api/home/brief").json()
    assert j["yesterday"] == YESTERDAY
    assert j["revenue_yesterday"] == pytest.approx(20 * 300.0, rel=1e-6)


def test_other_home_tiles_anchor_on_the_same_day(live_client):
    assert live_client.get("/api/home/branch-scorecard").json()["date"] == YESTERDAY
    assert live_client.get("/api/home/leak-watch").json()["date"] == YESTERDAY
    assert live_client.get("/api/home/menu-movers").json()["date"] == YESTERDAY


def test_sales_daily_defaults_to_the_last_finished_day(live_client):
    d = live_client.get("/api/sales/daily?gran=day").json()
    assert d["focus"] == YESTERDAY
    assert d["total"]["revenue"] == pytest.approx(20 * 300.0, rel=1e-6)
    # an explicit date still reaches the open day
    t = live_client.get(f"/api/sales/daily?gran=day&date={TH_TODAY.isoformat()}").json()
    assert t["focus"] == TH_TODAY.isoformat()
