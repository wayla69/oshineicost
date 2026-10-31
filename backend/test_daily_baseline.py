# -*- coding: utf-8 -*-
"""THE daily baseline (owner 2026-10-31, docs/UI_REVIEW_MOBILE_2026-10-31.md no. 12): every daily comparison compares a day
with "ปกติ" = the MEDIAN of the same weekday over the 8 weeks before (rules.daily_baseline).

  * the median, not the mean: one holiday week does not drag the normal;
  * a same weekday without bills (the branch was shut) is skipped, not counted as zero;
  * under 2 usable weeks there is no verdict ("ยังไม่มีฐาน"); 2..7 weeks give a verdict labelled "ปกติจาก n สัปดาห์";
  * per branch each branch on its own days; chain-wide = the branches trading now summed per day, then the median;
  * the anchor is the last finished day (latest_settled_day), never the day still trading;
  * one answer: the phone hero / Daily Brief (/api/home/brief), the branch scorecard, the management report's daily headline
    and league, and ร้านของฉัน · สรุปประจำวัน all report the SAME normal and the same % for the same branch and day.
"""
import datetime as dt
import sqlite3

import pytest

import conftest as _cf
import rules as R


def _days(**by):
    return {d: {"rev": v, "cust": 4, "bills": 1, "disc": 0} for d, v in by.items()}


D = "2026-06-17"                                              # a Wednesday
W = [(dt.date(2026, 6, 17) - dt.timedelta(days=7 * k)).isoformat() for k in range(1, 9)]


# ── the rule, on rows ─────────────────────────────────────────────────────────
def test_the_dates_are_the_same_weekday_of_the_8_weeks_before():
    assert R.baseline_dates(D) == W
    assert all(dt.date.fromisoformat(x).weekday() == 2 for x in W)


def test_the_normal_is_the_median_not_the_mean():
    by = {W[k]: {"rev": 1000.0, "cust": 4, "bills": 1, "disc": 0} for k in range(8)}
    by[W[2]] = {"rev": 9000.0, "cust": 30, "bills": 9, "disc": 0}        # a holiday
    by[D] = {"rev": 800.0, "cust": 4, "bills": 1, "disc": 0}
    b = R.baseline_from_days(by, D)
    assert b["normal"]["revenue"] == 1000.0 and b["normal"]["n_weeks"] == 8   # the mean would be 2,000
    assert b["vs_normal_pct"] == -20.0 and b["vs_normal_baht"] == -200
    assert b["below_normal"] is True and b["status"] == "down"
    assert b["label"] == "เทียบปกติ (ค่ากลาง 8 สัปดาห์)" and b["label_short"] == "เทียบปกติ"
    assert b["cust_vs_normal_pct"] == 0.0 and b["rph_vs_normal_pct"] == -20.0


def test_a_shut_day_is_skipped_not_zero():
    by = {W[k]: {"rev": 1000.0, "cust": 4, "bills": 1, "disc": 0} for k in range(8)}
    by[W[1]] = {"rev": 0.0, "cust": 0, "bills": 0, "disc": 0}           # shut
    del by[W[4]]                                                        # no row at all
    by[D] = {"rev": 1000.0, "cust": 4, "bills": 1, "disc": 0}
    b = R.baseline_from_days(by, D)
    assert b["normal"]["n_weeks"] == 6 and W[1] not in b["normal"]["dates"] and W[4] not in b["normal"]["dates"]
    assert b["normal"]["revenue"] == 1000.0 and b["vs_normal_pct"] == 0.0 and b["status"] == "ok"
    assert b["label"] == "เทียบปกติจาก 6 สัปดาห์"


def test_under_two_weeks_there_is_no_verdict():
    one = R.baseline_from_days(_days(**{W[0]: 1000.0, D: 100.0}), D)
    assert one["has_verdict"] is False and one["vs_normal_pct"] is None and one["below_normal"] is False
    assert one["status"] == "nobase" and one["label"] == "ยังไม่มีฐาน"
    two = R.baseline_from_days(_days(**{W[0]: 1000.0, W[5]: 1200.0, D: 500.0}), D)
    assert two["has_verdict"] is True and two["normal"]["revenue"] == 1100.0 and two["below_normal"] is True
    assert two["label"] == "เทียบปกติจาก 2 สัปดาห์"
    # the day itself without bills: its data has not arrived - never a "−100 %"
    miss = R.baseline_from_days(_days(**{W[0]: 1000.0, W[1]: 1000.0}), D)
    assert miss["status"] == "missing" and miss["vs_normal_pct"] is None and miss["below_normal"] is False


def test_the_thresholds_are_the_daily_briefs():
    by = {W[k]: {"rev": 1000.0, "cust": 4, "bills": 1, "disc": 0} for k in range(8)}
    for rev, st in ((921.0, "ok"), (920.0, "down"), (1099.0, "ok"), (1100.0, "up")):
        assert R.baseline_from_days({**by, D: {"rev": rev, "cust": 4, "bills": 1, "disc": 0}}, D)["status"] == st, rev


def test_the_chain_sums_per_day_then_takes_the_median():
    a = {W[k]: {"rev": 1000.0, "cust": 4, "bills": 1, "disc": 0} for k in range(8)}
    b = {W[k]: {"rev": 500.0, "cust": 2, "bills": 1, "disc": 0} for k in range(8)}
    a[W[0]] = {"rev": 5000.0, "cust": 4, "bills": 1, "disc": 0}           # a's strong week
    b[W[1]] = {"rev": 5000.0, "cust": 4, "bills": 1, "disc": 0}           # b's strong week, another week
    ch = R.baseline_from_days(R.chain_days({"A": a, "B": b}), D)
    assert ch["normal"]["revenue"] == 1500.0                               # median of the per-day sums (2 weeks at 5,500 / 6,000)
    assert R.baseline_from_days(a, D)["normal"]["revenue"] == 1000.0 and R.baseline_from_days(b, D)["normal"]["revenue"] == 500.0


# ── the rule, on a database ───────────────────────────────────────────────────
def _bill(con, bid, rcpt, day, net, cust=4, disc=0.0):
    con.execute("INSERT INTO sales_transaction(branch_id,receipt_no,sold_at,customer_count,food_subtotal,drink_subtotal,other_subtotal,"
                "discount,service_charge,total_inc_vat,source) VALUES(?,?,?,?,?,0,0,?,0,?, 'Easy')",
                (bid, rcpt, f"{day}T12:00:00", cust, net * 1.07, disc, net * 1.07))
    con.execute("INSERT INTO sale_payment VALUES(?,?,?,?)", (bid, rcpt, "Cash", net * 1.07))


@pytest.fixture
def world(tmp_path, monkeypatch):
    """Two new branches trading up to the last finished day (Thai yesterday): 'สาขาฐาน' with 8 same weekdays before it - one a
    holiday (฿10,000), one shut - and 'สาขาใหม่' with one; every day of the week before has bills (the sparkline)."""
    p = tmp_path / "bl.db"
    _cf._build_db(str(p))
    con = sqlite3.connect(str(p))
    con.row_factory = sqlite3.Row
    con.execute("INSERT INTO branches(branch_id, name) VALUES(31, 'สาขาฐาน')")
    con.execute("INSERT INTO branches(branch_id, name) VALUES(32, 'สาขาใหม่')")
    day = _cf.bkk_today() - dt.timedelta(days=1)
    same = [day - dt.timedelta(days=7 * k) for k in range(1, 9)]
    for k, d in enumerate(same, 1):
        if k == 5:
            continue                                                  # shut that week
        _bill(con, 31, f"B31-{d}", d.isoformat(), 10000.0 if k == 3 else 1000.0)
    for i in range(1, 7):                                             # the other days of the sparkline week
        d = day - dt.timedelta(days=i)
        _bill(con, 31, f"B31-{d}", d.isoformat(), 900.0)
        _bill(con, 32, f"B32-{d}", d.isoformat(), 700.0)
    _bill(con, 32, f"B32-{same[0]}", same[0].isoformat(), 700.0)
    _bill(con, 31, f"B31-{day}", day.isoformat(), 700.0, cust=2)      # the day: ฿700, 2 guests
    _bill(con, 32, f"B32-{day}", day.isoformat(), 700.0)
    import core
    core._ensure_rev_views(con)                                       # the views a live DB gets at startup
    con.commit()
    con.close()
    monkeypatch.setattr(core, "DB_PATH", p)
    try:
        import main
        if hasattr(main, "_tls"):
            main._tls.con = None
    except Exception:
        pass
    core.invalidate_cache()
    yield {"path": p, "day": day.isoformat()}
    core.invalidate_cache()


def _con(world):
    c = sqlite3.connect(str(world["path"]))
    c.row_factory = sqlite3.Row
    return c


def _json(r):
    if hasattr(r, "body"):
        import json
        return json.loads(r.body)
    return r


def test_the_anchor_is_the_last_finished_day_and_the_branch_is_on_its_own_days(world):
    con = _con(world)
    bl = R.daily_baseline(con, "t.source='Easy' AND b.branch_id=31", {})
    assert bl["day"] == world["day"]                                  # no day given: the last finished day
    assert bl["normal"]["n_weeks"] == 7 and bl["normal"]["revenue"] == 1000.0
    assert bl["vs_normal_pct"] == -30.0 and bl["below_normal"] is True
    per = R.daily_baseline(con, "t.source='Easy' AND b.branch_id IN (31, 32)", {}, day=world["day"], by_branch=True)
    assert per["สาขาฐาน"]["vs_normal_pct"] == -30.0
    assert per["สาขาใหม่"]["status"] == "nobase" and per["สาขาใหม่"]["vs_normal_pct"] is None


def test_one_answer_everywhere(world):
    """The phone hero / Daily Brief, the scorecard, the management daily report and ร้านของฉัน · สรุปประจำวัน: the same
    normal and the same % for the same branch and day, and the same count of branches below normal."""
    from routers import home_extra as H
    from routers import reports as RP
    from routers import franchise as F
    day, nm = world["day"], "สาขาฐาน"
    brief = _json(getattr(H.home_brief, "__wrapped__", H.home_brief)(branch=nm))
    sc = _json(getattr(H.home_branch_scorecard, "__wrapped__", H.home_branch_scorecard)(date=None, group=None, branch=None))
    mg = _json(getattr(RP.report_management, "__wrapped__", RP.report_management)(
        period="daily", date=None, week=None, month=None, branch=nm, group=None, lang="th", mtd=None, year=None))
    mg_all = _json(getattr(RP.report_management, "__wrapped__", RP.report_management)(
        period="daily", date=None, week=None, month=None, branch=None, group=None, lang="th", mtd=None, year=None))
    fr = F.franchise_daily_data(31)
    row = next(b for b in sc["branches"] if b["branch"] == nm)
    assert brief["yesterday"] == sc["date"] == mg["focus"] == mg_all["focus"]                   # one day: the last finished day
    assert fr["day"]["date"] == day == sc["date"]
    # the branch: -30 % against a normal of ฿1,000 from 7 weeks, on every screen
    assert row["vs_normal_pct"] == brief["revenue_vs_normal_pct"] == mg["headline"]["rev_wow_pct"] == fr["day"]["vs_normal_pct"] == -30.0
    assert row["normal"] == 1000 and brief["revenue_normal"] == 1000.0 and mg["headline"]["prev"]["revenue"] == 1000.0
    assert fr["day"]["normal"] == 1000.0 and row["normal_weeks"] == brief["normal_weeks"] == fr["day"]["normal_weeks"] == 7
    lg = next(b for b in mg_all["branches"] if b["branch"] == nm)
    assert lg["wow_pct"] == -30.0 and lg["prev_revenue"] == 1000.0 and lg["below_normal"] is True
    assert mg["comp_label"] == "ปกติจาก 7 สัปดาห์" and mg["prev_label"] == "วันเดียวกันสัปดาห์ก่อน"
    # the chain: the hero's figure (the brief without a branch) is the scorecard's chain and the report's chain headline
    brief_all = _json(getattr(H.home_brief, "__wrapped__", H.home_brief)(branch=None))
    assert brief_all["revenue_vs_normal_pct"] == sc["chain"]["vs_normal_pct"] == mg_all["headline"]["rev_wow_pct"]
    # the count of branches below normal: one answer (the scorecard's verdict), the report's league agrees
    assert sc["below_count"] == sum(1 for b in sc["branches"] if b["below_normal"])
    assert {b["branch"] for b in sc["branches"] if b["below_normal"]} == {b["branch"] for b in mg_all["branches"] if b.get("below_normal")}
    nb = next(b for b in sc["branches"] if b["branch"] == "สาขาใหม่")
    assert nb["status"] == "nobase" and nb["below_normal"] is False and nb["vs_normal_pct"] is None
