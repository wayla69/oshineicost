"""The Management report's §3 must describe the month UP TO THE REPORT'S DATE.

The daily report deliberately anchors on the last COMPLETE day, so while today
is still trading the month holds one day more than the report describes. A
report dated the 3rd was headlining "4 days so far" and averaging three full
days plus a few hours of the fourth over four days — understating the run rate,
the projection built on it, and the weekday-fair MoM baseline sized by
days_elapsed.

The fixture is built relative to the real Thai today because the anchor is
chosen by SQL date('now','+7 hours'), which a test cannot move.
"""
import os
os.environ["OSHINEI_NO_SWAP"] = "1"
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

TH_TODAY = dt.datetime.utcnow() + dt.timedelta(hours=7)


@pytest.fixture(scope="module")
def partial_day_client(tmp_path_factory):
    """Every day of the current month up to and including today — so the latest
    day with data is the still-open one, exactly as in production."""
    if TH_TODAY.day < 2:
        pytest.skip("needs at least one complete day before today in the month")
    db = tmp_path_factory.mktemp("mtd") / "test.db"
    _cf._build_db(str(db))
    con = sqlite3.connect(str(db))
    con.execute("DELETE FROM sales_transaction")
    con.execute("DELETE FROM sales_sku_daily")
    for d in range(1, TH_TODAY.day + 1):
        day = TH_TODAY.replace(day=d).strftime("%Y-%m-%d")
        for bid in (1, 2):
            con.execute(
                "INSERT INTO sales_transaction(branch_id,receipt_no,sold_at,customer_count,"
                "food_subtotal,drink_subtotal,other_subtotal,sp_discount,discount,"
                "service_charge,total_inc_vat,source) VALUES(?,?,?,?,?,?,?,?,?,?,?,'Easy')",
                (bid, f"{day}-{bid}", f"{day}T12:00:00", 10,
                 1000.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1070.0))
            con.execute("INSERT INTO sales_sku_daily VALUES(?,?,?,?,?)", (bid, day, 3, 5, 500.0))
    con.commit(); con.close()

    # Repointing main.DB_PATH is global. Without restoring it this module would
    # leave every later test reading THIS fixture — which is how it first showed
    # up: test_mtd expected cutoff_day 3 and got today's day instead.
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


def _mgt(client, **kw):
    r = client.get("/api/reports/management", params={"period": "daily", **kw})
    assert r.status_code == 200, r.text
    return r.json()


def test_daily_report_anchors_on_the_last_complete_day(partial_day_client):
    j = _mgt(partial_day_client)
    assert j.get("available") is not False
    # daily reports collapse the range to a single day: range == [anchor, anchor]
    anchor = TH_TODAY.replace(day=TH_TODAY.day - 1).strftime("%Y-%m-%d")
    assert j["range"] == [anchor, anchor], "must not default onto the open day"


def test_mtd_days_stop_at_the_report_date(partial_day_client):
    """The bug: mtd_days counted every day present in the month, including the
    still-open one after the report's own date."""
    j = _mgt(partial_day_client)
    pace = j["pace"]
    expected = TH_TODAY.day - 1
    assert pace["mtd_days"] == expected, (
        f"report is dated day {expected} but §3 claims {pace['mtd_days']} days")
    assert pace["days_elapsed"] == expected


def test_mtd_revenue_excludes_the_open_day(partial_day_client):
    """Each seeded day is ฿1,070 inc-VAT x 2 branches = ฿2,000 ex-VAT, no service
    charge — so the total pins the window exactly, not just the day count."""
    j = _mgt(partial_day_client)
    assert round(j["pace"]["mtd_revenue"]) == 2000 * (TH_TODAY.day - 1)


def test_projection_uses_the_same_window(partial_day_client):
    """projected = mtd_revenue / days_elapsed x days_total. Dividing full days of
    revenue by one more day than produced it was the visible symptom."""
    j = _mgt(partial_day_client)
    p = j["pace"]
    assert round(p["projected"]) == round(
        p["mtd_revenue"] / p["days_elapsed"] * p["days_total"])
    # each complete day is worth ฿2,000, so the run rate must land on it
    assert round(p["mtd_revenue"] / p["days_elapsed"]) == 2000


def test_an_explicitly_requested_open_day_still_includes_itself(partial_day_client):
    """Anchoring is what moved, not the window rule: ask for today and §3 covers
    the month through today."""
    today = TH_TODAY.strftime("%Y-%m-%d")
    j = _mgt(partial_day_client, date=today)
    assert j["pace"]["mtd_days"] == TH_TODAY.day
