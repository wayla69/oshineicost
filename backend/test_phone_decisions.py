# -*- coding: utf-8 -*-
"""PR-PD (owner 2026-10-29) · the phone's decision lines: every figure answers "what do I do today".

  * no. 1  the branch scorecard carries the cause behind a day below normal (customers, spend per head, discount share) and
           whether the day's bills arrived (reported / missing);
  * no. 2  pace.run_rate: days left, the baht a day the branches with a target must sell, the baht a day they have sold;
  * no. 5  peak.weak_day: the weekday that fell 10 % or more against the same weekday of the comparison period;
  * no. 11 leak.cash_heavy.top: the branch x shift that holds most of the cash-heavy discount baht;
  * no. 18 the cost-riser line says ต้นทุนต่อจาน (never a selling price) and "เท่าเดิม" when it did not move.
"""
import datetime as dt
import sqlite3

import conftest as _cf
from routers import reports as R


def _db(tmp_path):
    p = tmp_path / "pd.db"
    _cf._build_db(str(p))
    c = sqlite3.connect(str(p))
    c.row_factory = sqlite3.Row
    return c, p


def _pct(c, p):
    return round((c - p) / p * 100, 1) if p else None


def _bill(con, bid, rcpt, at, inc, disc=0.0, cust=2, pay="Cash"):
    con.execute("INSERT INTO sales_transaction(branch_id,receipt_no,sold_at,customer_count,food_subtotal,drink_subtotal,other_subtotal,"
                "discount,service_charge,total_inc_vat,source) VALUES(?,?,?,?,?,0,0,?,0,?, 'Easy')", (bid, rcpt, at, cust, inc, disc, inc))
    con.execute("INSERT INTO sale_payment VALUES(?,?,?,?)", (bid, rcpt, pay, inc))


def test_the_cost_riser_line_names_the_cost_per_plate_and_says_when_it_did_not_move():
    m0 = {"menu": "ปลาแซลมอนย่างเทริยากิ", "delta": 3.65, "cost_per_head": 4.46, "price_delta": 0.0, "usage_delta": 3.65}
    t = R._cost_riser_action(m0, "เดือน", True)
    assert "ต้นทุนต่อจานเท่าเดิม" in t and "กินต่อหัว +฿3.65" in t and t.endswith("ตรวจสอบการตักเสิร์ฟ")
    assert "ราคาต่อจาน" not in t and "฿0.00" not in t
    t1 = R._cost_riser_action(dict(m0, price_delta=2.0, usage_delta=0.5), "เดือน", True)
    assert "ต้นทุนต่อจาน +฿2.00 · กินต่อหัว +฿0.50" in t1 and "ราคาวัตถุดิบ" in t1
    assert "cost per plate unchanged · plates per head +฿3.65" in R._cost_riser_action(m0, "month", False)


def test_cash_heavy_names_the_branch_and_the_shift_that_hold_most_of_it(tmp_path):
    con, _ = _db(tmp_path)
    nm = {r["branch_id"]: r["name"] for r in con.execute("SELECT branch_id, name FROM branches")}
    # three heavy-discount bills paid in cash: two at branch 2 in the evening, one at branch 1 at noon
    _bill(con, 2, "H-1", "2026-06-02T19:10:00", 2000.0, 1200.0)
    _bill(con, 2, "H-2", "2026-06-03T20:00:00", 1500.0, 1000.0)
    _bill(con, 1, "H-3", "2026-06-02T12:30:00", 800.0, 400.0)
    _bill(con, 1, "H-4", "2026-06-03T19:30:00", 800.0, 400.0, pay="Credit")        # a card bill is never cash-heavy
    con.commit()
    out = R._mgt_leak(con, ce=dt.date(2026, 6, 3), cs=dt.date(2026, 6, 1), has_prev=True, params={}, pct=_pct,
                      pe=dt.date(2026, 5, 3), ps=dt.date(2026, 5, 1), where="b.name!='ALL'")
    ch = out["cash_heavy"]
    assert ch["bills"] == 3 and ch["baht"] == 2600
    assert ch["top"] == {"branch": nm[2], "shift": "เย็น", "bills": 2, "baht": 2200, "share_pct": 85}
    assert out["heavy"]["bills"] == 4                                             # the card bill counts as a heavy discount


def test_the_weekday_that_fell_against_the_comparison_period(tmp_path):
    con, _ = _db(tmp_path)
    con.execute("INSERT INTO branches(branch_id, name) VALUES(3, 'สาขาทดสอบ')")
    cs, ce = dt.date(2026, 6, 1), dt.date(2026, 6, 14)        # Mon 1 Jun .. Sun 14 Jun: two of every weekday
    ps, pe = dt.date(2026, 5, 4), dt.date(2026, 5, 17)        # Mon 4 May .. Sun 17 May
    d = ps
    while d <= pe:
        _bill(con, 3, f"P-{d}", f"{d}T12:00:00", 1070.0); d += dt.timedelta(days=1)
    d = cs
    while d <= ce:
        _bill(con, 3, f"C-{d}", f"{d}T12:00:00", 535.0 if d.weekday() == 5 else 1070.0); d += dt.timedelta(days=1)   # Saturdays halved
    con.commit()
    peak: dict = {}
    R._mgt_table(con, _daypart=lambda h: "Other", branch=None, ce=ce, cs=cs, group=None, has_prev=True, params={}, pe=pe,
                 peak=peak, period="monthly", ps=ps, where="b.branch_id=3")
    by = {d["dow"]: d for d in peak["by_day"]}
    assert by["Sat"]["delta_pct"] == -50.0 and by["Mon"]["delta_pct"] == 0.0 and by["Sat"]["prev_avg_revenue"] == 1000
    assert peak["weak_day"] == {"dow": "Sat", "delta_pct": -50.0, "avg_revenue": 500, "prev_avg_revenue": 1000}
    # a fall under 10 % is no weak day
    con.execute("UPDATE sales_transaction SET total_inc_vat=1017, food_subtotal=1017 WHERE receipt_no LIKE 'C-%' AND total_inc_vat=535")
    con.commit()
    peak = {}
    R._mgt_table(con, _daypart=lambda h: "Other", branch=None, ce=ce, cs=cs, group=None, has_prev=True, params={}, pe=pe,
                 peak=peak, period="monthly", ps=ps, where="b.branch_id=3")
    assert peak["weak_day"] is None


def test_the_run_rate_says_the_baht_a_day_the_target_branches_must_sell(monkeypatch):
    monkeypatch.setattr(R, "attainment", lambda con, month, revs, f, branch, group: {"target": 660000, "pct": 75.0, "projected": 495000, "branches": 1, "no_target_branches": 1})
    ctx = {"period": "monthly", "cs": dt.date(2026, 6, 1), "month_complete": False,
           "pace": {"month": "2026-06", "mtd_revenue": 420000, "projected": 600000, "days_elapsed": 22, "days_total": 31},
           "branches": [{"branch": "A", "revenue": 300000, "target": 400000, "vs_target_pct": 75.0}, {"branch": "B", "revenue": 120000, "target": None}]}
    R._mgt_target_scope(None, ctx)
    rr = ctx["pace"]["run_rate"]
    assert rr == {"days_left": 9, "need_per_day": 40000, "normal_per_day": 13636, "mtd": 300000, "branches": 1}
    # the month is complete: nothing left to sell, no run rate
    ctx2 = {**ctx, "month_complete": True, "pace": {**{k: v for k, v in ctx["pace"].items() if k != "run_rate"}, "days_elapsed": 31},
            "branches": [dict(b) for b in ctx["branches"]]}
    R._mgt_target_scope(None, ctx2)
    assert "run_rate" not in ctx2["pace"]


def test_the_scorecard_carries_the_cause_and_whether_the_day_arrived(tmp_path, monkeypatch):
    con, path = _db(tmp_path)
    con.execute("INSERT INTO branches(branch_id, name) VALUES(3, 'สาขาทดสอบ')")
    con.execute("INSERT INTO branches(branch_id, name) VALUES(4, 'สาขาเงียบ')")
    focus = dt.date(2026, 5, 17)
    for i in range(1, 8):                                            # the seven days before: 4 guests, ฿1,000 net, ฿20 off
        d = focus - dt.timedelta(days=i)
        _bill(con, 3, f"S3-{d}", f"{d}T12:00:00", 1070.0, 20.0, cust=4)
        _bill(con, 4, f"S4-{d}", f"{d}T12:00:00", 1070.0, 20.0, cust=4)
    _bill(con, 3, f"S3-{focus}", f"{focus}T12:00:00", 535.0, 100.0, cust=2)   # the focus day: half the guests, the same spend per head, a heavy discount
    con.commit()
    con.close()
    import core
    monkeypatch.setattr(core, "DB_PATH", path)
    try:
        import main
        if hasattr(main, "_tls"):
            main._tls.con = None
    except Exception:
        pass
    from routers import home_extra as H
    d = H.home_branch_scorecard(date=focus.isoformat(), group=None, branch=None)
    if hasattr(d, "body"):                                           # the cached route answers a JSONResponse
        import json
        d = json.loads(d.body)
    rows = {r["branch"]: r for r in d["branches"]}
    r3 = rows["สาขาทดสอบ"]
    assert r3["reported"] is True and r3["vs_avg_pct"] == -50.0
    assert r3["cust_vs_avg_pct"] == -50.0 and r3["rph_vs_avg_pct"] == 0.0
    assert r3["discount_pct"] == round(100 / 600 * 100, 1) and r3["discount_pct_avg7"] == round(20 / 1020 * 100, 1)
    if "สาขาเงียบ" in rows:                                           # no bills on the focus day: its data has not arrived, never "−100 %"
        assert rows["สาขาเงียบ"]["reported"] is False
        assert "สาขาเงียบ" in d["missing"]
    assert d["reported"] == sum(1 for r in d["branches"] if r["revenue"] > 0)
    assert d["missing"] == [r["branch"] for r in d["branches"] if not r["reported"]]
