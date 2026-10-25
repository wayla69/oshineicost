"""The Daily Brief headlines the latest finished day WHOSE BILLS HAVE ARRIVED.

The 06:00 live sync runs before the POS has posted the previous day: 2026-10-08
the 7th held 7 bills chain-wide (6 branches, ฿4,048) and the desktop brief and the
phone's เมื่อวาน strip headlined a −99.9 % day. rules.latest_settled_day steps back
over a finished day with fewer than 40 % of the trailing week's bills and reports
which day it set aside; /api/home/brief and /api/home/branch-scorecard anchor on it.

Built relative to the real Thai today, like test_brief_closed_day.
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
import rules  # noqa: E402

TH_TODAY = _cf.bkk_today()   # the app's Bangkok day (rules.TH_TODAY_SQL) at the session's instant
pytestmark = pytest.mark.usefixtures("bkk_frozen")   # SQLite 'now' = the instant TH_TODAY is built from
YESTERDAY = (TH_TODAY - dt.timedelta(days=1)).isoformat()
DAY_BEFORE = (TH_TODAY - dt.timedelta(days=2)).isoformat()


def _insert_day(con, ds, per_branch):
    for bid in (1, 2):
        for k in range(per_branch):
            con.execute(
                "INSERT INTO sales_transaction(branch_id,receipt_no,sold_at,customer_count,"
                "food_subtotal,drink_subtotal,other_subtotal,sp_discount,discount,"
                "service_charge,total_inc_vat,source) VALUES(?,?,?,?,?,?,?,?,?,?,?,'Easy')",
                (bid, f"{ds}-{bid}-{k}", f"{ds}T12:{k:02d}:00", 2,
                 300.0, 0.0, 0.0, 0.0, 0.0, 0.0, 321.0))
        con.execute("INSERT INTO sales_sku_daily VALUES(?,?,?,?,?)", (bid, ds, 3, 5 * per_branch, 50.0 * per_branch))


@pytest.fixture(scope="module")
def thin_client(tmp_path_factory):
    """Previous month complete, this month through today; YESTERDAY holds one bill per
    branch (the morning after, before the POS posted it), today one early bill."""
    db = tmp_path_factory.mktemp("settled") / "test.db"
    _cf._build_db(str(db))
    con = sqlite3.connect(str(db))
    con.execute("DELETE FROM sales_transaction")
    con.execute("DELETE FROM sales_sku_daily")
    pm = (TH_TODAY.replace(day=1) - dt.timedelta(days=1)).replace(day=1)
    days = [pm.replace(day=i) for i in range(1, calendar.monthrange(pm.year, pm.month)[1] + 1)]
    days += [TH_TODAY.replace(day=i) for i in range(1, TH_TODAY.day + 1)]
    for day in days:
        ds = day.isoformat()
        n = 1 if day in (TH_TODAY, TH_TODAY - dt.timedelta(days=1)) else 10
        _insert_day(con, ds, n)
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


def test_rule_steps_back_over_a_thin_day(thin_client):
    import core
    con = sqlite3.connect(str(core.DB_PATH))
    try:
        r = rules.latest_settled_day(con)
    finally:
        con.close()
    assert r["day"] == DAY_BEFORE
    assert r["skipped_day"] == YESTERDAY and r["skipped_bills"] == 2   # one bill per branch


def test_brief_headlines_the_settled_day_and_says_so(thin_client):
    j = thin_client.get("/api/home/brief").json()
    assert j["yesterday"] == DAY_BEFORE
    assert j["revenue_yesterday"] == pytest.approx(20 * 300.0, rel=1e-6)
    assert j["skipped_day"] == YESTERDAY and j["skipped_bills"] == 2


def test_scorecard_anchors_on_the_same_settled_day(thin_client):
    j = thin_client.get("/api/home/branch-scorecard").json()
    assert j["date"] == DAY_BEFORE and j["skipped_day"] == YESTERDAY
    # an explicit date is honoured as before, and then nothing was skipped
    j2 = thin_client.get(f"/api/home/branch-scorecard?date={YESTERDAY}").json()
    assert j2["date"] == YESTERDAY and j2["skipped_day"] is None


def test_a_normal_low_day_is_not_skipped(tmp_path):
    """A quiet Monday at 60 % of the week is a real day, not a missing one."""
    db = tmp_path / "quiet.db"
    _cf._build_db(str(db))
    con = sqlite3.connect(str(db))
    con.execute("DELETE FROM sales_transaction"); con.execute("DELETE FROM sales_sku_daily")
    for i in range(1, 9):
        ds = (TH_TODAY - dt.timedelta(days=i)).isoformat()
        _insert_day(con, ds, 6 if i == 1 else 10)   # yesterday 12 bills vs 20 before: 60 %
    con.commit()
    r = rules.latest_settled_day(con)
    con.close()
    assert r["day"] == YESTERDAY and r["skipped_day"] is None
