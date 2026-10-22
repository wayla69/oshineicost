# -*- coding: utf-8 -*-
"""PR-H8 (owner 2026-10-22): the history behind every suggestion / normal / estimate is 8 weeks (forecast.HISTORY_WEEKS).

The promises:
  * one constant drives the default window of suggest / covers / backtest / the per-฿100,000 basis and report, the missing-day
    estimate of the system stock, the branch's own normal and the all-branch median window;
  * SLIP_MIN_DAYS and the Grand Menu changeover (era_start) are untouched, so until early November the window is shorter than 8 weeks;
  * the issue slip sheet carries `normal_qty` / `normal_days` = the mean issued (used + waste) of the item on the SAME weekday over the
    last 8 weeks of confirmed slips (a confirmed day without the item counts as 0; fewer days found are handled).
"""
import inspect
from datetime import date, timedelta

import pytest

from bom_center import forecast as FC
from bom_center import issue_reports as IR
from bom_center import issues as IS
from bom_center import per_sales as PS
from bom_center import stock as ST
from test_bom_center_issues import CRAB, NOW, build_stock, slip, used
from test_bom_center_s4b import con  # noqa: F401
from test_bom_center_s11 import udon  # noqa: F401
from test_slip_forecast import SALMON, _slip, chain  # noqa: F401


def _default(fn, name):
    return inspect.signature(fn).parameters[name].default


def test_the_constant_is_8_weeks_and_every_default_window_follows_it():
    assert FC.HISTORY_WEEKS == 8 and FC.SLIP_MIN_DAYS == 14
    for fn, arg in ((FC.suggest, "weeks"), (FC.covers, "weeks"), (FC.backtest, "history_weeks"), (PS.normal_sales, "weeks"),
                    (PS.reference_basis, "weeks"), (PS.benchmark_basis, "weeks"), (PS.report, "weeks")):
        assert _default(fn, arg) == FC.HISTORY_WEEKS, (fn.__name__, arg)
    assert _default(FC.backtest, "weeks") == 4                           # the number of evaluated weeks, not the history
    assert ST.SLIP_MEAN_WEEKS == FC.HISTORY_WEEKS and ST.LOOKBACK_DAYS == 7 * FC.HISTORY_WEEKS == 56
    assert IR.NORMAL_WEEKS == FC.HISTORY_WEEKS and IR.MEDIAN_WINDOW_DAYS == 7 * FC.HISTORY_WEEKS == 56


def test_era_clipping_still_holds(chain):  # noqa: F811
    w = 7 * FC.HISTORY_WEEKS - 1
    assert FC.era_start(date(2026, 10, 5) - timedelta(days=w), date(2026, 10, 5)) == date(2026, 9, 1)      # the changeover wins
    assert FC.era_start(date(2026, 12, 1) - timedelta(days=w), date(2026, 12, 1)) == date(2026, 10, 7)     # a full 56 days
    lab = FC.source_label(chain, 1)                                      # sales run 1 Sep - 5 Oct: the window is the 35 days since the changeover
    assert lab["window_days"] == 35 and lab["min_days"] == 14


def test_a_missing_day_is_the_mean_of_the_same_weekday_over_8_weeks(chain):  # noqa: F811
    c = chain
    for d, q in (("2026-08-11", 1000.0), ("2026-08-04", 7777.0), ("2026-09-29", 200.0)):    # 8 weeks before Mon 6 Oct is 11 Aug; 4 Aug is the 9th
        _slip(c, 1, d, {CRAB: (q, 0.0)})
    days, est = ST.estimated_use(c, 1, ["2026-10-06"])
    assert days == ["2026-10-06"] and est[CRAB] == pytest.approx((1000 + 200) / 2)


def test_the_branch_normal_reaches_8_weeks_back():
    day = date(2026, 10, 6)                                                # a Tuesday: 29 Sep is the 1st back, 11 Aug the 8th, 4 Aug the 9th
    h = {"days": {"2026-09-29", "2026-08-11", "2026-08-04"}, "sales": {},
         "iss": {"2026-09-29": {CRAB: 400.0}, "2026-08-11": {CRAB: 9000.0}, "2026-08-04": {CRAB: 5555.0}}}
    assert IR.normal_of(h, day) == {CRAB: pytest.approx((400 + 9000) / 2)}
    h["days"].discard("2026-08-11")                                        # only one day left in the window: no normal
    assert IR.normal_of(h, day) is None


def test_the_median_window_is_56_days():
    day = date(2026, 10, 20)

    def hist(first):
        days = {(date.fromisoformat(first) + timedelta(days=i)).isoformat() for i in range(14)}
        return {"days": days, "sales": {x: 1000.0 for x in days}, "iss": {x: {CRAB: 100.0} for x in days}}
    inside, outside = hist("2026-08-26"), hist("2026-08-10")              # 55 days before the day … 14 days; the second ends 57 days back
    assert IR.median_of([inside, inside], day, 100000.0) is not None
    assert IR.median_of([outside, outside], day, 100000.0) is None        # fewer than SLIP_MIN_DAYS inside the window


# ── the guide beside the boxes ───────────────────────────────────────────────────────────────
@pytest.mark.usefixtures("recipe_mode")                                  # the count list the sheet is built from comes from the recipes
def test_the_sheet_carries_the_same_weekday_normal_over_8_weeks(con):  # noqa: F811
    build_stock(con)
    slip(con, "2026-09-28", [used(qty=1000)])                              # Mondays before Mon 5 Oct
    slip(con, "2026-09-21", [used(qty=400, qty_waste=200, waste_reason="spill")])   # used + waste
    slip(con, "2026-09-14", [used(code=SALMON, qty=100)])                  # a confirmed day without the crab counts as 0
    slip(con, "2026-09-07", [used(qty=500)], submit=False)                 # a draft is nobody's figure
    slip(con, "2026-08-10", [used(qty=200)])                               # the 8th Monday back
    slip(con, "2026-08-03", [used(qty=5000)])                              # the 9th: outside
    slip(con, "2026-09-30", [used(qty=3333)])                              # another weekday
    con.commit()
    sh = IS.sheet(con, 1, "2026-10-05", now=NOW)
    crab = next(i for z in sh["zones"] for i in z["items"] if i["code"] == CRAB)
    assert crab["normal_days"] == 4                                         # 28 Sep, 21 Sep, 14 Sep, 10 Aug
    assert crab["normal_qty"] == pytest.approx((1000 + 600 + 0 + 200) / 4 / 1000)   # in packs, the count unit
    assert "avg_used_7d" in crab                                            # kept for compatibility


@pytest.mark.usefixtures("recipe_mode")                                  # the count list the sheet is built from comes from the recipes
def test_a_weekday_with_no_confirmed_slip_has_no_normal(con):  # noqa: F811
    build_stock(con)
    sh = IS.sheet(con, 1, "2026-10-07", now=NOW)                            # a Wednesday: none before it
    crab = next(i for z in sh["zones"] for i in z["items"] if i["code"] == CRAB)
    assert crab["normal_days"] == 0 and crab["normal_qty"] is None
