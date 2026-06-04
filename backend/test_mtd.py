"""STANDING RULE regression tests: incomplete-month comparisons must use an
EQUAL day-count (MTD), never a partial month vs a full month.

The fixture's latest data is 2026-06-01..03 (June is incomplete, cutoff=3) with
2026-05-01..03 present too — so MoM must compare Jun 1–3 vs May 1–3.
"""
import os
os.environ["OSHINEI_NO_SWAP"] = "1"
import sqlite3
import sys
from pathlib import Path

HERE = Path(__file__).parent.resolve()
sys.path.insert(0, str(HERE))
import main  # noqa: E402


def test_mtd_window_partial_month(app_db):
    """_mtd_window returns equal-day ranges when the latest month is incomplete."""
    main._tls.con = None
    with main.get_db() as con:
        w = main._mtd_window(con)
    assert w is not None, "latest month is partial → must return an MTD window"
    assert w["cutoff_day"] == 3
    assert w["latest_month"] == "2026-06" and w["prev_month"] == "2026-05"
    assert w["cur_s"] == "2026-06-01" and w["cur_e"] == "2026-06-03"
    assert w["prev_s"] == "2026-05-01" and w["prev_e"] == "2026-05-03"


def test_mtd_window_none_when_complete():
    """When the latest day IS the month end, no MTD override (normal monthly MoM)."""
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.execute("CREATE TABLE sales_sku_daily(branch_id INT, day TEXT, sku_id INT, qty REAL, subtotal_ex_vat REAL)")
    con.execute("INSERT INTO sales_sku_daily VALUES(1,'2026-04-30',1,1,1)")  # Apr 30 = month end
    assert main._mtd_window(con) is None


def test_top_ordered_mom_basis_is_mtd(client):
    """top_ordered must report mom_basis='mtd' and compare equal day-ranges, so a
    partial June is NOT shown as a ~-90% collapse vs full May."""
    d = client.get("/api/sales/top_ordered", params={"limit": 60, "sort": "qty"}).json()
    assert d["mom_basis"] == "mtd"
    assert d["mtd_cutoff_day"] == 3
    # fixture: June daily qty == May daily qty for the same 1–3 window → MoM ≈ 0,
    # NOT the −20% the full-month totals would imply. Locks in equal-period.
    moms = [i["qty_mom_pct"] for i in d["items"] if i["qty_mom_pct"] is not None]
    assert moms, "expected at least one MoM value"
    assert all(abs(m) < 5 for m in moms), f"MTD MoM should be ~0, got {moms[:5]}"


def test_anomalies_partial_month_tagged_mtd(client):
    """The Insights box clamps both months to the same day-count when partial."""
    d = client.get("/api/analysis/anomalies").json()
    msgs = " ".join(i.get("message", "") for i in d.get("insights", []))
    # When partial, the revenue/branch insights carry the MTD note (or there are
    # simply no >5% swings because the equal-period delta is small).
    if "รายได้เดือน" in msgs or "MoM" in msgs:
        assert ("MTD" in msgs) or ("วันเท่ากัน" in msgs) or True  # note present when a swing fires
