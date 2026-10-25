"""/api/reports/management?period=monthly&mtd=1 — the phone's month view.

The printed pack only ever describes a COMPLETE month. The phone summary wants
the month in progress too, so `mtd=1` adds it to the selectable months as
MONTH-TO-DATE through the latest complete day, and clamps every comparison to
the same elapsed days: previous month 1..d, same month last year 1..d. Without
the flag nothing changes — the desktop report keeps its closed-month default.

Built relative to the real Thai today (the anchor is SQL date('now','+7 hours')).
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

TH_TODAY = (dt.datetime.utcnow() + dt.timedelta(hours=7)).date()


def _prev_month(d):
    first = d.replace(day=1)
    return (first - dt.timedelta(days=1)).replace(day=1)


@pytest.fixture(scope="module")
def mtd_client(tmp_path_factory):
    """The previous month complete, and the current month through today."""
    if TH_TODAY.day < 3:
        pytest.skip("needs at least two complete days of the current month")
    db = tmp_path_factory.mktemp("mtdm") / "test.db"
    _cf._build_db(str(db))
    con = sqlite3.connect(str(db))
    con.execute("DELETE FROM sales_transaction")
    con.execute("DELETE FROM sales_sku_daily")
    pm = _prev_month(TH_TODAY)
    days = [pm.replace(day=i) for i in range(1, calendar.monthrange(pm.year, pm.month)[1] + 1)]
    days += [TH_TODAY.replace(day=i) for i in range(1, TH_TODAY.day + 1)]
    for day in days:
        ds = day.isoformat()
        for bid in (1, 2):
            con.execute(
                "INSERT INTO sales_transaction(branch_id,receipt_no,sold_at,customer_count,"
                "food_subtotal,drink_subtotal,other_subtotal,sp_discount,discount,"
                "service_charge,total_inc_vat,source) VALUES(?,?,?,?,?,?,?,?,?,?,?,'Easy')",
                (bid, f"{ds}-{bid}", f"{ds}T12:00:00", 10,
                 1000.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1070.0))
            con.execute("INSERT INTO sales_sku_daily VALUES(?,?,?,?,?)", (bid, ds, 3, 5, 500.0))
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


def test_without_flag_the_report_keeps_the_closed_month(mtd_client):
    # PR-PP2: the month in progress is OFFERED to date in the picker (it was the phone's mtd=1 only), but the
    # report still opens on the latest closed month without the flag
    j = mtd_client.get("/api/reports/management?period=monthly").json()
    pm = _prev_month(TH_TODAY).strftime("%Y-%m")
    assert j["available"] and j["selected_month"] == pm
    in_progress = [m for m in j["months"] if m.get("mtd")]
    assert in_progress == [] or (len(in_progress) == 1 and in_progress[0]["month"] == TH_TODAY.strftime("%Y-%m")
                                 and j["months"][0] is not None and j["months"][0]["mtd"] is True)
    assert j["partial"] is False and j["to_date"] is False


def test_mtd_flag_adds_the_month_in_progress_month_to_date(mtd_client):
    j = mtd_client.get("/api/reports/management?period=monthly&mtd=1&lang=th").json()
    cur = TH_TODAY.strftime("%Y-%m")
    yesterday = (TH_TODAY - dt.timedelta(days=1)).isoformat()
    assert j["available"] and j["selected_month"] == cur and j["partial"] is True
    assert j["months"][0]["month"] == cur and j["months"][0]["mtd"] is True
    assert j["months"][0]["through"] == yesterday
    # the window runs 1..yesterday; the comparison is the previous month 1..same day
    assert j["range"] == [TH_TODAY.replace(day=1).isoformat(), yesterday]
    pm = _prev_month(TH_TODAY)
    d = TH_TODAY.day - 1
    assert j["comp_range"] == [pm.isoformat(), pm.replace(day=min(d, calendar.monthrange(pm.year, pm.month)[1])).isoformat()]
    assert j["days_in_period"] == d
    # two branches × ฿1000 ex-VAT a day, both windows → MoM is flat, not a collapse
    assert j["headline"]["revenue"] == pytest.approx(2000.0 * d, rel=1e-6)
    assert j["headline"]["rev_wow_pct"] == 0.0
    assert "1–" in j["label"] and "1–" in j["comp_label"]


def test_mtd_flag_still_serves_a_closed_month_on_request(mtd_client):
    pm = _prev_month(TH_TODAY).strftime("%Y-%m")
    j = mtd_client.get(f"/api/reports/management?period=monthly&mtd=1&month={pm}").json()
    assert j["selected_month"] == pm and j["partial"] is False
    assert j["range"][1].endswith(str(calendar.monthrange(int(pm[:4]), int(pm[5:7]))[1]))
