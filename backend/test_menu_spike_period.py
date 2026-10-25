"""PR-SP / PR-ST · /api/sales/menu-spike by period: day, this week, MTD, a custom range.

- PR-ST (owner 2026-10-25): per head - plates per 100 customers (buffet menus on buffet customers, the rest on all
  customers) against the weekday-fair normal of the 3 weeks before, in every period INCLUDING the day mode (the frozen
  copy of the old day mode is gone: the day mode now equals a one-day range); a branch without customer counts keeps the
  plate standard (basis "days"), which is what the constructed DB of sections 1-7 has - so their numbers are PR-SP's;
  section 8 builds customers and the buffet-tier fact tables (rate math, the tier split, a-la-carte, stale facts);
- week / mtd bounds (Monday / the 1st -> the anchor, never past the last finished day);
- the weekday-fair "ปกติ": a weekend-heavy menu is not flagged in a period that is mostly weekend;
- custom range validation answers 400 in formal Thai;
- the 5-plate floor; the new-menu list (28-day boundary, per_day, trend, day mode) - section 7;
- the pure JS helper periodRange(kind, anchor) of the card (lifted into js/01-core.js by PR-PP1);
- PR-SC (owner 2026-10-25) · section 9: ต้นทุนเพิ่ม = extra plates x the month-weighted dish cost of THE engine, its share of the
  scope's food cost and its points of food-cost %, the order by it (uncosted / package rows last), top = 15, and no cost key at all
  for a role without cost.analysis / profit.pnl.
"""
import datetime as dt
import json
import sqlite3
import subprocess
import tempfile
from contextlib import contextmanager
from pathlib import Path

import pytest

from routers import sales
from test_bom_center_batch2 import needs_node, NODE


# ── a constructed DB: one branch, 2026-07-01 .. 2026-08-31 (all finished days) ──
SAT_SUN = (5, 6)          # date.weekday()


def _db(plan, closed_mondays_before=None):
    """plan(day: date) -> {sku_id: qty}; the branch trades every day unless closed_mondays_before
    is set (no rows on a Monday before that date)."""
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.executescript("""
      CREATE TABLE branches(branch_id INTEGER PRIMARY KEY, name TEXT, branch_type TEXT);
      CREATE TABLE skus(sku_id INTEGER PRIMARY KEY, sku_code TEXT, description TEXT);
      CREATE TABLE sales_sku_daily(branch_id INT, day TEXT, sku_id INT, qty REAL, subtotal_ex_vat REAL);
      CREATE INDEX ix_ssd ON sales_sku_daily(branch_id, day, sku_id);
      CREATE VIEW v_sales_sku_daily AS SELECT * FROM sales_sku_daily
        WHERE sku_id NOT IN (SELECT sku_id FROM skus WHERE UPPER(sku_code) IN ('SERVICECHARGE','SERVICE'));
      INSERT INTO branches VALUES (1, 'สาขาทดสอบ', 'master'), (2, 'สาขาสอง', 'franchise');
      INSERT INTO skus VALUES (1, 'BASE', '(B) ข้าวปั้น'), (2, 'WKND', '(B) ซาชิมิ'), (3, 'NEW1', '(A) เมนูใหม่'),
                              (4, 'SERVICECHARGE', 'ค่าบริการ'), (5, 'SMALL', 'เมนูเล็ก'), (6, 'NEW2', 'เมนูใหม่น้อย');
    """)
    d = dt.date(2026, 7, 1)
    while d <= dt.date(2026, 8, 31):
        if not (closed_mondays_before and d < closed_mondays_before and d.weekday() == 0):
            for sid, q in plan(d).items():
                if q is not None:
                    con.execute("INSERT INTO sales_sku_daily VALUES (1, ?, ?, ?, 0)", (d.isoformat(), sid, q))
            con.execute("INSERT INTO sales_sku_daily VALUES (2, ?, 1, 10, 0)", (d.isoformat(),))
        d += dt.timedelta(days=1)
    return con


def _weekend_plan(d):
    """BASE 10 a day; WKND 30 on Sat/Sun and 2 on weekdays (weekend-heavy), every day the same."""
    return {1: 10, 2: 30 if d.weekday() in SAT_SUN else 2, 4: 100}


def _call(con, monkeypatch, **kw):
    @contextmanager
    def fake_db():
        yield con
    monkeypatch.setattr(sales, "get_db", fake_db)
    a = dict(date=None, branch=None, group=None, limit=300, threshold=1.5, period="day", date_from=None, date_to=None)
    a.update(kw)
    return sales.menu_spike(**a)


def _row(res, menu, branch="สาขาทดสอบ"):
    return next((s for s in res["spikes"] if s["menu"] == menu and s["branch"] == branch), None)


# ── 1. day mode = the per-head, weekday-fair standard on one day (PR-ST, owner 2026-10-25) ───────────────────────────
# Before PR-ST the day mode compared the day with the 21-day average of the days the menu sold (a frozen copy of that code
# stood here). Owner decision 2 (2026-10-25): the day mode uses the same standard as every period - the same weekday of
# the 3 weeks before, per head - so the day mode now equals a one-day custom range.
_PERIOD_KEYS = ("period", "latest_day")


def _same_as_range(day_res, range_res):
    strip = lambda r: {k: v for k, v in r.items() if k not in _PERIOD_KEYS}
    assert strip(day_res) == strip(range_res)


@pytest.mark.parametrize("params", [{}, {"date": "2026-06-02"}, {"branch": "BranchA"}, {"group": "franchise"}])
def test_day_mode_on_the_fixture_is_the_one_day_range(client, params):
    for thr in (1.5, 1.0):
        for extra in ({}, {"period": "day"}):
            r = client.get("/api/sales/menu-spike", params={"threshold": thr, "limit": 300, **params, **extra})
            assert r.status_code == 200, (params, extra)
            d = r.json()
            assert d["period"] == "day" and d["date_from"] == d["date_to"] == d["date"] and d["days"] == 1
            assert (d["baseline_from"], d["baseline_to"]) == (
                (dt.date.fromisoformat(d["date"]) - dt.timedelta(days=21)).isoformat(),
                (dt.date.fromisoformat(d["date"]) - dt.timedelta(days=1)).isoformat())
            rp = {k: v for k, v in params.items() if k != "date"}
            g = client.get("/api/sales/menu-spike", params={"threshold": thr, "limit": 300, **rp, "period": "range",
                                                            "date_from": d["date"], "date_to": d["date"]}).json()
            _same_as_range(d, g)


def test_day_mode_with_spikes(monkeypatch):
    def plan(d):
        p = _weekend_plan(d)
        if d == dt.date(2026, 8, 20):
            p.update({1: 40, 3: 25, 6: 12})     # a spike, a new menu, a new menu under the 20-plate floor
        if d >= dt.date(2026, 8, 18):
            p[3] = p.get(3, 25)
        return p
    con = _db(plan)
    got = _call(con, monkeypatch, date="2026-08-20")
    s = _row(got, "ข้าวปั้น")
    assert (s["today"], s["expected"], s["x"], s["excess"], s["basis"]) == (40, 10, 4.0, 30, "days")
    assert _row(got, "เมนูใหม่") is None and got["period"] == "day" and got["count"] == 1
    assert [(m["menu"], m["qty"]) for m in got["new_menus"]] == [("เมนูใหม่", 25), ("เมนูใหม่น้อย", 12)]
    for kw in ({"date": "2026-08-20", "branch": "สาขาทดสอบ"}, {"date": "2026-08-20", "threshold": 3.0}):
        rng = _call(con, monkeypatch, period="range", date_from="2026-08-20", date_to="2026-08-20",
                    **{k: v for k, v in kw.items() if k != "date"})
        _same_as_range(_call(con, monkeypatch, **kw), rng)
    assert _call(con, monkeypatch, date="2026-08-20", threshold=5.0)["spike_count"] == 0


def test_day_mode_is_weekday_fair(monkeypatch):
    # ซาชิมิ 30 on a Saturday, 2 on weekdays: Sat 22 Aug at its usual 30 is normal (the old 21-day average, ~10, said x3)
    con = _db(_weekend_plan)
    r = _call(con, monkeypatch, date="2026-08-22", threshold=1.0)
    s = _row(r, "ซาชิมิ")
    assert (s["today"], s["expected"], s["x"]) == (30, 30, 1.0)
    assert _call(con, monkeypatch, date="2026-08-22")["count"] == 0


# ── 2. bounds ─────────────────────────────────────────────────────────────────────────────────────
def test_week_and_mtd_bounds_on_the_fixture(client):
    w = client.get("/api/sales/menu-spike", params={"period": "week", "date": "2026-06-03"}).json()
    assert (w["period"], w["date_from"], w["date_to"], w["days"]) == ("week", "2026-06-01", "2026-06-03", 3)   # 1 Jun 2026 is a Monday
    assert (w["baseline_from"], w["baseline_to"]) == ("2026-05-11", "2026-05-31")
    m = client.get("/api/sales/menu-spike", params={"period": "mtd", "date": "2026-06-02"}).json()
    assert (m["date_from"], m["date_to"], m["days"], m["date"]) == ("2026-06-01", "2026-06-02", 2, "2026-06-02")
    # no date = the last finished day; a date past it is held at it (the day still trading never enters)
    for p in ({"period": "mtd"}, {"period": "mtd", "date": "2026-06-30"}):
        m = client.get("/api/sales/menu-spike", params=p).json()
        assert (m["date_from"], m["date_to"], m["latest_day"]) == ("2026-06-01", "2026-06-03", "2026-06-03"), p
    for k in ("today", "avg", "expected", "per_day", "x", "excess", "is_new"):
        assert all(k in s for s in m["spikes"])


def test_week_starts_on_monday(monkeypatch):
    con = _db(_weekend_plan)
    for anchor, start in (("2026-08-23", "2026-08-17"), ("2026-08-17", "2026-08-17"), ("2026-08-20", "2026-08-17")):
        r = _call(con, monkeypatch, period="week", date=anchor)
        assert (r["date_from"], r["date_to"]) == (start, anchor)
        assert r["baseline_from"] == (dt.date.fromisoformat(start) - dt.timedelta(days=21)).isoformat()
        assert r["baseline_to"] == (dt.date.fromisoformat(start) - dt.timedelta(days=1)).isoformat()


# ── 3. the weekday-fair "ปกติ" ─────────────────────────────────────────────────────────────────────
def test_a_weekend_heavy_menu_is_not_flagged_in_a_mostly_weekend_period(monkeypatch):
    con = _db(_weekend_plan)
    # 1 Aug 2026 is a Saturday: MTD on Sun 2 Aug = Sat + Sun; a plain daily average (10/day) would call 60 plates x3
    for kw in ({"period": "mtd", "date": "2026-08-02"}, {"period": "range", "date_from": "2026-08-08", "date_to": "2026-08-09"},
               {"period": "week", "date": "2026-08-23"}, {"period": "range", "date_from": "2026-08-07", "date_to": "2026-08-30"}):
        r = _call(con, monkeypatch, **kw, threshold=1.0)
        s = _row(r, "ซาชิมิ")
        assert s is not None and s["x"] == 1.0 and s["today"] == s["expected"], (kw, s)
        assert _row(r, "ค่าบริการ") is None                     # service charge is not a menu
    r = _call(con, monkeypatch, period="mtd", date="2026-08-02")
    assert r["count"] == 0
    s = _call(con, monkeypatch, period="mtd", date="2026-08-02", threshold=1.0)
    assert _row(s, "ซาชิมิ")["expected"] == 60 and _row(s, "ซาชิมิ")["per_day"] == 30.0


def test_a_real_weekend_spike_is_flagged_with_its_fields(monkeypatch):
    def plan(d):
        p = _weekend_plan(d)
        if d >= dt.date(2026, 8, 1) and d.weekday() in SAT_SUN:
            p[2] = 75                                            # 2.5x the usual weekend
        return p
    con = _db(plan)
    r = _call(con, monkeypatch, period="mtd", date="2026-08-02")
    s = _row(r, "ซาชิมิ")
    # no customer counts in this DB: basis "days" = one per trading day, the plate standard of PR-SP (rate None)
    assert s == {"branch": "สาขาทดสอบ", "menu": "ซาชิมิ", "today": 150, "per_day": 75.0, "avg": 60.0, "expected": 60,
                 "x": 2.5, "pct": 150, "excess": 90, "is_new": False, "spike": True, "kind": "buffet", "basis": "days", "customers": None,
                 "rate": None, "normal_rate": None, "tiers": None, "tier_note": "ยังไม่มีข้อมูลแยก tier"}
    assert _row(r, "ข้าวปั้น") is None                          # flat menu


def test_days_without_the_menu_count_as_zero_and_a_missing_weekday_uses_the_plain_average(monkeypatch):
    # closed on Mondays until 10 Aug; BASE 10 a day, so the plain baseline daily average is 10
    con = _db(lambda d: {1: 10}, closed_mondays_before=dt.date(2026, 8, 10))
    r = _call(con, monkeypatch, period="range", date_from="2026-08-10", date_to="2026-08-16", threshold=1.0)
    assert _row(r, "ข้าวปั้น")["expected"] == 70                 # Monday (no baseline Monday) = 10, the rest 10 each
    # a menu absent on one of the three baseline Saturdays while the branch traded: that Saturday is a zero,
    # so the Saturday norm is (0 + 30 + 30) / 3 = 20, not the average of the days it sold (30)
    def plan(d):
        p = {1: 10, 2: 30 if d.weekday() == 5 else 5}
        if d == dt.date(2026, 8, 1):
            del p[2]
        return p
    con = _db(plan)
    r = _call(con, monkeypatch, period="range", date_from="2026-08-22", date_to="2026-08-22", threshold=1.0)
    assert _row(r, "ซาชิมิ")["expected"] == 20 and _row(r, "ซาชิมิ")["x"] == 1.5


# ── 4. new menu and floors ────────────────────────────────────────────────────────────────────────
def test_new_menus_leave_the_spike_list_and_the_five_plate_floor(monkeypatch):
    def plan(d):
        p = {1: 10, 5: 1}
        if d >= dt.date(2026, 8, 17):
            p[3] = 4                                             # new: 4 a day from 17 Aug
            p[6] = 2                                             # new but small: 2 a day
            p[5] = 2                                             # x2 but only 4 plates in 2 days
        if dt.date(2026, 8, 14) <= d < dt.date(2026, 8, 17):
            p[3] = 1                                             # 3 baseline days (< 5)
        return p
    con = _db(plan)
    r = _call(con, monkeypatch, period="range", date_from="2026-08-17", date_to="2026-08-23")
    assert _row(r, "เมนูใหม่") is None and _row(r, "เมนูใหม่น้อย") is None     # new (first sold 14 / 17 Aug): not spikes
    nm = {m["menu"]: m for m in r["new_menus"]}
    assert (nm["เมนูใหม่"]["qty"], nm["เมนูใหม่"]["first_sold"], nm["เมนูใหม่"]["age_days"]) == (28, "2026-08-14", 10)
    assert nm["เมนูใหม่น้อย"]["qty"] == 14                       # no plate floor and no threshold for a new menu
    assert r["new_count"] == 2 and list(nm) == ["เมนูใหม่", "เมนูใหม่น้อย"]    # by plates, most first
    assert _row(r, "เมนูเล็ก")["x"] == 2.0                        # 14 plates over 7 days >= 5
    assert all(s["is_new"] is False for s in r["spikes"])
    r = _call(con, monkeypatch, period="range", date_from="2026-08-17", date_to="2026-08-18")
    assert _row(r, "เมนูเล็ก") is None                           # x2 but 4 plates < 5


def test_branch_and_group_filters(monkeypatch):
    con = _db(_weekend_plan)
    r = _call(con, monkeypatch, period="mtd", date="2026-08-02", threshold=1.0, branch="สาขาสอง")
    assert {s["branch"] for s in r["spikes"]} == {"สาขาสอง"}
    r = _call(con, monkeypatch, period="mtd", date="2026-08-02", threshold=1.0, group="master")
    assert {s["branch"] for s in r["spikes"]} == {"สาขาทดสอบ"}


# ── 5. validation ─────────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("params, word", [
    ({"period": "range"}, "ต้องระบุ"),
    ({"period": "range", "date_from": "2026-06-01"}, "ต้องระบุ"),
    ({"period": "range", "date_from": "2026/06/01", "date_to": "2026-06-03"}, "ไม่ถูกต้อง"),
    ({"period": "range", "date_from": "2026-06-03", "date_to": "2026-06-01"}, "ไม่อยู่หลัง"),
    ({"period": "range", "date_from": "2026-03-01", "date_to": "2026-06-03"}, "92"),
    ({"period": "range", "date_from": "2026-06-01", "date_to": "2026-06-10"}, "2026-06-03"),
    ({"period": "month"}, "period"),
    ({"period": "week", "date": "not-a-date"}, "ไม่ถูกต้อง"),
])
def test_range_validation_answers_400_in_thai(client, params, word):
    r = client.get("/api/sales/menu-spike", params=params)
    assert r.status_code == 400, (params, r.status_code)
    msg = r.json()["detail"]
    assert word in msg and "กรุณา" not in msg and "ระบบจะ" not in msg, msg


def test_a_92_day_range_is_accepted(client):
    r = client.get("/api/sales/menu-spike", params={"period": "range", "date_from": "2026-03-04", "date_to": "2026-06-03"})
    assert r.status_code == 200 and r.json()["days"] == 92


# ── 6. the card's pure period helper: periodRange, lifted into js/01-core.js by PR-PP1 (its <period-core> block) ──
@needs_node
def test_period_range_helper():
    src = (Path(__file__).resolve().parent.parent / "js" / "01-core.js").read_text(encoding="utf-8")
    block = src[src.index("// <period-core>"):src.index("// </period-core>")]
    js = ("const _M = ['ม.ค.','ก.พ.','มี.ค.','เม.ย.','พ.ค.','มิ.ย.','ก.ค.','ส.ค.','ก.ย.','ต.ค.','พ.ย.','ธ.ค.'];\n"
          "const fmtDateTh = s => `${+s.slice(8)} ${_M[+s.slice(5, 7) - 1]}`;\n" + block)
    cases = [["day", "2026-10-23"], ["week", "2026-10-23"], ["week", "2026-10-19"], ["mtd", "2026-10-23"],
             ["range", {"from": "2026-09-28", "to": "2026-10-03"}], ["range", {"from": "2026-10-05", "to": "2026-10-01"}],
             ["week", "bad"], ["year", "2026-10-23"]]
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
        f.write(js + f"\nconsole.log(JSON.stringify({json.dumps(cases, ensure_ascii=False)}.map(c => periodRange(c[0], c[1]))));")
    try:
        out = json.loads(subprocess.run([NODE, f.name], capture_output=True, text=True, encoding="utf-8", check=True).stdout)
    finally:
        Path(f.name).unlink()
    # the PR-SP fields are kept; PR-PP1 adds cur / prev / ly / complete / Thai labels beside them (backend/test_periods.py)
    sp = [None if o is None else {k: o[k] for k in ("from", "to", "days", "label")} for o in out]
    assert sp[0] == {"from": "2026-10-23", "to": "2026-10-23", "days": 1, "label": "23 ต.ค."}
    assert sp[1] == {"from": "2026-10-19", "to": "2026-10-23", "days": 5, "label": "19–23 ต.ค."}   # Monday 19 Oct
    assert sp[2]["from"] == "2026-10-19" and sp[2]["days"] == 1
    assert sp[3] == {"from": "2026-10-01", "to": "2026-10-23", "days": 23, "label": "1–23 ต.ค."}
    assert sp[4] == {"from": "2026-09-28", "to": "2026-10-03", "days": 6, "label": "28 ก.ย.–3 ต.ค."}
    assert sp[5] is None and sp[6] is None
    assert sp[7] == {"from": "2026-01-01", "to": "2026-10-23", "days": 296, "label": "1 ม.ค.–23 ต.ค."}   # PR-PP1: year is a kind now


# ── 7. PR-SN · new menus (owner 2026-10-24): new while period end - first day sold < 28 days ──────────────
def _nm(res, menu, branch="สาขาทดสอบ"):
    return next((m for m in res["new_menus"] if m["menu"] == menu and m["branch"] == branch), None)


def test_the_28_day_boundary(monkeypatch):
    # anchor 20 Aug: เมนูใหม่ first sold 24 Jul (anchor - 27 -> new, day 28), เมนูเล็ก first sold 23 Jul (anchor - 28 -> existing)
    def plan(d):
        p = {1: 10}
        if d >= dt.date(2026, 7, 24):
            p[3] = 10
        if d >= dt.date(2026, 7, 23):
            p[5] = 10
        return p
    con = _db(plan)
    for kw in ({"date": "2026-08-20"}, {"period": "mtd", "date": "2026-08-20"}, {"period": "week", "date": "2026-08-20"},
               {"period": "range", "date_from": "2026-08-20", "date_to": "2026-08-20"}):
        r = _call(con, monkeypatch, threshold=1.0, **kw)
        m = _nm(r, "เมนูใหม่")
        assert m is not None and (m["first_sold"], m["first_sold_th"], m["age_days"]) == ("2026-07-24", "24 ก.ค.", 28), kw
        assert _row(r, "เมนูใหม่") is None, kw                                  # a new menu is never a spike
        assert _nm(r, "เมนูเล็ก") is None and _row(r, "เมนูเล็ก")["is_new"] is False, kw   # 28 days: existing, spike rule
        assert r["new_days"] == 28 and r["new_count"] == 1, kw
    r = _call(con, monkeypatch, threshold=1.0, date="2026-08-21")                     # one day on: both existing
    assert r["new_count"] == 0 and _row(r, "เมนูใหม่")["is_new"] is False


def test_an_old_menu_with_a_thin_baseline_stays_in_the_existing_part(monkeypatch):
    # เมนูเล็ก first sold 1 Jul (old), then only on 3 baseline days (2 plates each) before a 30-plate day: the old code
    # called it "เมนูใหม่"; now it is an existing menu with a thin baseline: listed from 20 plates, marked thin_base,
    # "ปกติ" counting the branch's trading days without a sale as zero
    # PR-TD: the two thin menus that sold a little in the baseline also sold every 10-14 days before it (1, 12, 22 Jul: before both
    # baselines), so they never went 21 days without a sale - they stay here; the two with no baseline sale at all came back after
    # 50 days and are in the new-menu part ("กลับมาขาย", see section 10)
    sparse = (dt.date(2026, 7, 1), dt.date(2026, 7, 12), dt.date(2026, 7, 22))

    def plan(d):
        p = {1: 10}
        if d in sparse or d in (dt.date(2026, 8, 5), dt.date(2026, 8, 10), dt.date(2026, 8, 15)):
            p[5] = 2
        if d == dt.date(2026, 8, 20):
            p[5] = 30
        if d in (dt.date(2026, 7, 1), dt.date(2026, 8, 20)):
            p[6] = 25                                            # เมนูใหม่น้อย: old, no baseline sale at all
            p[2] = 1 if d.month == 7 else 19                     # ซาชิมิ: old, thin, 19 plates < 20 -> not listed
        if d in sparse or dt.date(2026, 8, 6) <= d <= dt.date(2026, 8, 8):
            p[3] = 4                                             # เมนูใหม่ (sku 3): old, thin, 3 days x 4 = 12 -> 0.57 a day
        if d == dt.date(2026, 8, 20):
            p[3] = 22
        return p
    con = _db(plan)
    r = _call(con, monkeypatch, date="2026-08-20")
    # day mode (PR-ST: weekday-fair): Thu 20 Aug vs the Thursdays 30 Jul, 6 and 13 Aug, none of which sold เมนูเล็ก -> "ปกติ" 0
    assert _row(r, "เมนูเล็ก") == {"branch": "สาขาทดสอบ", "menu": "เมนูเล็ก", "today": 30, "per_day": 30.0, "avg": 0.0,
                                   "expected": 0, "x": None, "pct": None, "excess": 30, "is_new": False, "spike": True, "thin_base": True,
                                   "kind": "alc", "basis": "days", "customers": None, "rate": None, "normal_rate": None,
                                   "tiers": None, "tier_note": "ยังไม่มีข้อมูลแยก tier"}
    # เมนูใหม่น้อย / ซาชิมิ: no sale from 2 Jul to 19 Aug -> back on 20 Aug: the new-menu part (PR-TD), whatever the plates
    assert _row(r, "เมนูใหม่น้อย") is None and _row(r, "ซาชิมิ") is None
    assert sorted((m["menu"], m["qty"], m["returning"], m["returned_on"]) for m in r["new_menus"]) == [
        ("ซาชิมิ", 19, True, "2026-08-20"), ("เมนูใหม่น้อย", 25, True, "2026-08-20")]
    assert _row(r, "เมนูใหม่")["x"] == 16.5                      # thin, 22 plates vs Thursdays 0 + 4 + 0 = 1.33: listed
    # thin with a baseline but under the threshold: not listed (the same threshold as every spike); no baseline: listed, x "—"
    r3 = _call(con, monkeypatch, date="2026-08-20", threshold=20.0)
    assert _row(r3, "เมนูใหม่") is None and _row(r3, "เมนูเล็ก") is not None
    assert _row(_call(con, monkeypatch, date="2026-08-20", threshold=16.0), "เมนูใหม่")["x"] == 16.5
    # week of 17 Aug (to Thu 20): weekday-fair "ปกติ" = Mon 2/3 + Wed 2/3 = 1.33 plates
    r = _call(con, monkeypatch, period="week", date="2026-08-20")
    s = _row(r, "เมนูเล็ก")
    assert (s["today"], s["avg"], s["expected"], s["x"], s["excess"], s["thin_base"]) == (30, 1.3, 1, 22.5, 29, True)
    assert _row(r, "เมนูใหม่น้อย") is None and _nm(r, "เมนูใหม่น้อย")["returning"] is True
    assert _row(r, "ซาชิมิ") is None and _nm(r, "เมนูเล็ก") is None

    # with >= 5 baseline days the same old menu is a plain spike, no thin_base key
    def plan2(d):
        p = plan(d)
        if dt.date(2026, 8, 1) <= d < dt.date(2026, 8, 20):
            p[5] = 2
        return p
    r = _call(_db(plan2), monkeypatch, date="2026-08-20")
    s = _row(r, "เมนูเล็ก")
    # Thursdays 30 Jul (none), 6 and 13 Aug (2 each) = 1.33 -> 30 / 1.33
    assert s is not None and s["is_new"] is False and s["x"] == 22.5 and "thin_base" not in s and _nm(r, "เมนูเล็ก") is None


def _launch_plan(d):
    """เมนูใหม่ (sku 3) launched Sat 1 Aug: 10 a day the first week, 15 the second, 20 from 14 Aug."""
    p = {1: 10, 4: 100}
    if d >= dt.date(2026, 8, 14):
        p[3] = 20
    elif d >= dt.date(2026, 8, 8):
        p[3] = 15
    elif d >= dt.date(2026, 8, 1):
        p[3] = 10
    return p


def test_per_day_and_trend(monkeypatch):
    # the branch is closed on Mondays (3, 10, 17 Aug): no rows at all those days
    con = _db(_launch_plan, closed_mondays_before=dt.date(2026, 8, 31))
    # MTD 1-20 Aug: 17 trading days; 6 x 10 + 5 x 15 + 6 x 20 = 255 plates
    r = _call(con, monkeypatch, period="mtd", date="2026-08-20")
    m = _nm(r, "เมนูใหม่")
    assert {k: m[k] for k in ("sku_code", "first_sold", "age_days", "qty", "per_day")} == \
        {"sku_code": "NEW1", "first_sold": "2026-08-01", "age_days": 20, "qty": 255, "per_day": 15.0}
    # first 7 days (1-7 Aug, 6 trading days) 10 a day; latest 7 (14-20 Aug, 6 trading days) 20 a day
    assert m["trend"] == {"first_per_day": 10.0, "latest_per_day": 20.0, "pct": 100}
    assert _nm(r, "ค่าบริการ") is None and _row(r, "เมนูใหม่") is None
    # week of 17 Aug (Monday closed): 3 trading days x 20; the trend still reads its first week, before the period
    r = _call(con, monkeypatch, period="week", date="2026-08-20")
    m = _nm(r, "เมนูใหม่")
    assert (m["qty"], m["per_day"], m["trend"]["pct"]) == (60, 20.0, 100)
    # a range that starts before the launch: per_day counts from the first day sold
    r = _call(con, monkeypatch, period="range", date_from="2026-07-20", date_to="2026-08-07")
    m = _nm(r, "เมนูใหม่")
    assert (m["qty"], m["per_day"], m["age_days"], m["trend"]) == (60, 10.0, 7, None)       # age < 14: no trend
    # day mode lists the new menus of that day with the same fields
    r = _call(con, monkeypatch, date="2026-08-12")
    m = _nm(r, "เมนูใหม่")
    assert (m["qty"], m["per_day"], m["age_days"], m["trend"]) == (15, 15.0, 12, None)
    r = _call(con, monkeypatch, date="2026-08-20")
    m = _nm(r, "เมนูใหม่")
    assert (m["qty"], m["per_day"], m["trend"]) == (20, 20.0, {"first_per_day": 10.0, "latest_per_day": 20.0, "pct": 100})
    assert r["count"] == 0 and _row(r, "เมนูใหม่") is None


def test_a_falling_new_menu_and_the_branch_filter(monkeypatch):
    def plan(d):
        p = {1: 10}
        if d >= dt.date(2026, 8, 1):
            p[3] = 30 if d < dt.date(2026, 8, 8) else 12
        return p
    con = _db(plan)
    r = _call(con, monkeypatch, period="mtd", date="2026-08-20")
    assert _nm(r, "เมนูใหม่")["trend"] == {"first_per_day": 30.0, "latest_per_day": 12.0, "pct": -60}
    assert _call(con, monkeypatch, period="mtd", date="2026-08-20", branch="สาขาสอง")["new_menus"] == []


def test_the_first_sold_map_is_kept_per_db_and_follows_new_rows(monkeypatch, tmp_path):
    src = _db(_launch_plan)
    src.commit()                                   # backup() waits on an open write transaction
    f = tmp_path / "spike.db"
    dst = sqlite3.connect(str(f))
    src.backup(dst)
    dst.close()
    con = sqlite3.connect(str(f))
    con.row_factory = sqlite3.Row
    try:
        r = _call(con, monkeypatch, period="mtd", date="2026-08-20")
        assert _nm(r, "เมนูใหม่") is not None and sales._FIRST_SOLD["key"][0].endswith("spike.db")
        assert _call(con, monkeypatch, period="mtd", date="2026-08-20") == r          # served from the kept map
        # an older sale appears (a sync); 20 Jul: less than 21 days before the launch, so not a menu that came back (PR-TD)
        con.execute("INSERT INTO sales_sku_daily VALUES (1, '2026-07-20', 3, 1, 0)")
        con.commit()
        r = _call(con, monkeypatch, period="mtd", date="2026-08-20")
        assert _nm(r, "เมนูใหม่") is None and r["new_count"] == 0
    finally:
        con.close()


# ── 8. PR-ST · per head (จาน/100 ลูกค้า) and the buffet tiers behind the increase (owner 2026-10-25) ─────────────────────
# A DB with customers: one branch, every day 1 Jul .. 31 Aug. cust(d) -> {"799": n, "599": n, "nb": n} (buffet customers per tier,
# non-buffet customers); plates(d) -> {sku_id: {"799": q, "599": q, "nb": q}} (plates on the bills of that tier / on non-buffet
# bills). sales_transaction holds all customers (source Easy), menu_fact_total / menu_fact_day the buffet bills up to fact_to.
def _db_pax(cust, plates, fact_to="2026-08-31", first=None):
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.executescript("""
      CREATE TABLE branches(branch_id INTEGER PRIMARY KEY, name TEXT, branch_type TEXT);
      CREATE TABLE skus(sku_id INTEGER PRIMARY KEY, sku_code TEXT, description TEXT);
      CREATE TABLE sales_sku_daily(branch_id INT, day TEXT, sku_id INT, qty REAL, subtotal_ex_vat REAL);
      CREATE INDEX ix_ssd ON sales_sku_daily(branch_id, day, sku_id);
      CREATE TABLE sales_transaction(branch_id INT, receipt_no TEXT, sold_at TEXT, customer_count INT, source TEXT);
      CREATE TABLE menu_fact_total(branch_id INT, day TEXT, tier TEXT, bills INT, pax REAL, cogs REAL, rev REAL, hc REAL,
        PRIMARY KEY(branch_id, day, tier));
      CREATE TABLE menu_fact_day(branch_id INT, day TEXT, tier TEXT, ent TEXT, bills INT, pax REAL, qty REAL,
        ent_cogs REAL, ent_hc REAL, cogs REAL, rev REAL, hc REAL, PRIMARY KEY(branch_id, day, tier, ent));
      CREATE INDEX ix_mfd_be ON menu_fact_day(branch_id, ent, day, tier, qty);
      INSERT INTO branches VALUES (1, 'สาขาทดสอบ', 'master');
      INSERT INTO skus VALUES (1, 'BASE', '(B) ข้าวปั้น'), (3, 'NEW1', '(A) เมนูใหม่'), (7, 'RAMEN', '(AL) ราเมง');
    """)
    d = dt.date(2026, 7, 1)
    while d <= dt.date(2026, 8, 31):
        day = d.isoformat()
        c = cust(d)
        con.execute("INSERT INTO sales_transaction VALUES (1, ?, ?, ?, 'Easy')", (f"R{day}", day + " 12:00:00", sum(c.values())))
        for sid, by in plates(d).items():
            if sum(by.values()):
                con.execute("INSERT INTO sales_sku_daily VALUES (1, ?, ?, ?, 0)", (day, sid, sum(by.values())))
            if day <= fact_to:
                for t, q in by.items():
                    if t != "nb" and q:
                        con.execute("INSERT INTO menu_fact_day VALUES (1, ?, ?, ?, 1, ?, ?, 0, 0, 0, 0, 0)", (day, t, f"s:{sid}", c[t], q))
        if day <= fact_to:
            for t, n in c.items():
                if t != "nb" and n:
                    con.execute("INSERT INTO menu_fact_total VALUES (1, ?, ?, 1, ?, 0, 0, 0)", (day, t, n))
        d += dt.timedelta(days=1)
    return con


_P0, _P1 = dt.date(2026, 8, 17), dt.date(2026, 8, 23)           # the period: Mon 17 .. Sun 23 Aug; baseline 27 Jul .. 16 Aug


def _in(d):
    return _P0 <= d <= _P1


def _spike_range(con, monkeypatch, **kw):
    return _call(con, monkeypatch, period="range", date_from=_P0.isoformat(), date_to=_P1.isoformat(), **kw)


def test_customers_double_and_plates_double_is_not_a_spike(monkeypatch):
    # the owner's case: a busy week. 100 buffet customers a day (60 at 799, 40 at 599), ข้าวปั้น 0.5 a head; the period has
    # twice the customers and twice the plates - the plate standard said x2.0, per head it is x1.0
    k = lambda d: 2 if _in(d) else 1
    con = _db_pax(lambda d: {"799": 60 * k(d), "599": 40 * k(d), "nb": 20},
                  lambda d: {1: {"799": 30 * k(d), "599": 20 * k(d)}})
    r = _spike_range(con, monkeypatch, threshold=1.0)
    s = _row(r, "ข้าวปั้น")
    assert (s["kind"], s["basis"], s["customers"], s["today"]) == ("buffet", "buffet", 1400, 700)
    assert (s["rate"], s["normal_rate"], s["x"], s["excess"]) == (50.0, 50.0, 1.0, 0)
    assert _spike_range(con, monkeypatch)["count"] == 0
    assert r["rate_per"] == 100 and r["tier_data_to"] == "2026-08-31"


def test_customers_halve_and_plates_stay_is_a_spike(monkeypatch):
    k = lambda d: 0.5 if _in(d) else 1
    con = _db_pax(lambda d: {"799": 60 * k(d), "599": 40 * k(d), "nb": 20},
                  lambda d: {1: {"799": 30, "599": 20}})
    s = _row(_spike_range(con, monkeypatch), "ข้าวปั้น")
    assert (s["customers"], s["today"], s["rate"], s["normal_rate"], s["x"], s["pct"]) == (350, 350, 100.0, 50.0, 2.0, 100)
    assert s["excess"] == 175                                        # 350 - 0.5 x 350 customers ("+จาน" at the normal rate)


def test_the_tier_split_sums_to_the_excess(monkeypatch):
    # the same customers and mix every day; in the period 799 eats 1.0 a head (0.5 before), 599 0.6 (0.5 before)
    con = _db_pax(lambda d: {"799": 60, "599": 40, "nb": 20},
                  lambda d: {1: {"799": 60 if _in(d) else 30, "599": 24 if _in(d) else 20}})
    s = _row(_spike_range(con, monkeypatch), "ข้าวปั้น")
    assert (s["today"], s["expected"], s["excess"]) == (588, 350, 238)
    t = {g["tier"]: g for g in s["tiers"]}
    assert [g["tier"] for g in s["tiers"]] == ["799", "599"]         # largest excess first; no non-buffet plates -> no group
    assert t["799"] == {"tier": "799", "label": "฿799", "plates": 420, "customers": 420, "rate": 100.0, "normal_rate": 50.0,
                        "excess": 210.0, "x": 2.0, "high": True}
    assert (t["599"]["excess"], t["599"]["x"], t["599"]["high"]) == (28.0, 1.2, False)
    assert abs(sum(g["excess"] for g in s["tiers"]) - s["excess"]) <= 1 and s["tier_rest"] == 0.0
    assert "tier_to" not in s and "tier_note" not in s


def test_a_tier_mix_shift_is_reported_as_the_rest(monkeypatch):
    # every tier eats as usual, but customers moved from 599 (0.25 a head) to 799 (0.75 a head): the menu is up per head,
    # no tier is - the excess is the mix, reported as tier_rest, never pinned on a tier
    con = _db_pax(lambda d: {"799": 90 if _in(d) else 50, "599": 10 if _in(d) else 50, "nb": 0},
                  lambda d: {1: {"799": (90 if _in(d) else 50) * 0.75, "599": (10 if _in(d) else 50) * 0.25}})
    s = _row(_spike_range(con, monkeypatch, threshold=1.0), "ข้าวปั้น")
    assert s["x"] == 1.4 and all(abs(g["excess"]) < 0.01 for g in s["tiers"])
    assert s["tier_rest"] == s["excess"] == 140


def test_a_la_carte_on_all_customers_with_a_non_buffet_group(monkeypatch):
    # ราเมง: 6 a day on 799 buffet bills (0.1 a head), 10 a day on non-buffet bills (20 customers); in the period the
    # non-buffet bills order 30 a day
    con = _db_pax(lambda d: {"799": 60, "599": 40, "nb": 20},
                  lambda d: {1: {"799": 30, "599": 20}, 7: {"799": 6, "nb": 30 if _in(d) else 10}})
    s = _row(_spike_range(con, monkeypatch), "(AL) ราเมง")         # only the (B) / (A) prefixes are cut from a name
    assert (s["kind"], s["basis"], s["customers"]) == ("alc", "all", 840)      # 120 customers a day, every bill
    assert (s["today"], s["rate"], s["normal_rate"], s["x"]) == (252, 30.0, 13.3, 2.2)
    assert [(g["label"], g["excess"], g["high"]) for g in s["tiers"]] == [("ไม่ใช่บุฟเฟ่ต์", 140.0, True), ("฿799", 0.0, False)]
    nb = s["tiers"][0]
    assert (nb["tier"], nb["plates"], nb["customers"], nb["rate"], nb["normal_rate"]) == ("nonbuffet", 210, 140, 150.0, 50.0)
    assert s["tier_rest"] == 0.0


def test_stale_fact_tables_give_no_split(monkeypatch):
    # the fact tables end on 10 Aug, inside the baseline: no tier split ("—" with the reason), and the buffet menu is measured
    # on all customers (its buffet customers are not known for the whole window)
    con = _db_pax(lambda d: {"799": 60, "599": 40, "nb": 20},
                  lambda d: {1: {"799": 60 if _in(d) else 30, "599": 20}}, fact_to="2026-08-10")
    s = _row(_spike_range(con, monkeypatch), "ข้าวปั้น")
    assert s["tiers"] is None and "10 ส.ค." in s["tier_note"] and s["basis"] == "all" and s["customers"] == 840
    assert (s["rate"], s["normal_rate"]) == (66.7, 41.7)


def test_fact_tables_one_sync_behind_split_the_covered_days(monkeypatch):
    con = _db_pax(lambda d: {"799": 60, "599": 40, "nb": 20},
                  lambda d: {1: {"799": 60 if _in(d) else 30, "599": 20}}, fact_to="2026-08-20")
    s = _row(_spike_range(con, monkeypatch), "ข้าวปั้น")
    assert s["basis"] == "all" and s["tier_to"] == "2026-08-20" and "tier_rest" not in s
    t = {g["tier"]: g for g in s["tiers"]}
    assert (t["799"]["plates"], t["799"]["customers"], t["799"]["excess"]) == (240, 240, 120.0)   # 17-20 Aug only
    assert t["599"]["excess"] == 0.0 and set(t) == {"799", "599"}


def test_new_menu_rate_and_a_per_head_trend(monkeypatch):
    # เมนูใหม่ launched Sat 1 Aug: 10 plates a day on 100 customers the first week, 20 on 200 from 14 Aug - twice the plates
    # a day, the same per head: the trend is 0 %, not +100 %
    busy = lambda d: d >= dt.date(2026, 8, 14)
    con = _db_pax(lambda d: {"799": 100 if busy(d) else 50, "599": 0, "nb": 100 if busy(d) else 50},
                  lambda d: {1: {"799": 25}, 3: {"nb": 0 if d < dt.date(2026, 8, 1) else 20 if busy(d) else 10}})
    r = _call(con, monkeypatch, period="mtd", date="2026-08-20")
    m = _nm(r, "เมนูใหม่")
    assert (m["kind"], m["basis"], m["qty"], m["age_days"]) == ("alc", "all", 270, 20)
    assert m["trend"] == {"first_per_day": 10.0, "latest_per_day": 20.0, "pct": 0, "first_rate": 10.0, "latest_rate": 10.0}
    assert (m["customers"], m["rate"]) == (2700, 10.0)
    # day mode: the same fields
    m = _nm(_call(con, monkeypatch, date="2026-08-20"), "เมนูใหม่")
    assert (m["qty"], m["customers"], m["rate"], m["trend"]["pct"]) == (20, 200, 10.0, 0)


def test_day_mode_per_head(monkeypatch):
    # Thu 20 Aug: twice the usual customers and plates -> not a spike; the same plates on half the customers -> x2.0
    con = _db_pax(lambda d: {"799": 120 if d == dt.date(2026, 8, 20) else 60, "599": 0, "nb": 0},
                  lambda d: {1: {"799": 60 if d == dt.date(2026, 8, 20) else 30}})
    assert _call(con, monkeypatch, date="2026-08-20")["count"] == 0
    con = _db_pax(lambda d: {"799": 30 if d == dt.date(2026, 8, 20) else 60, "599": 0, "nb": 0},
                  lambda d: {1: {"799": 30}})
    s = _row(_call(con, monkeypatch, date="2026-08-20"), "ข้าวปั้น")
    assert (s["rate"], s["normal_rate"], s["x"], s["basis"]) == (100.0, 50.0, 2.0, "buffet")
    assert [(g["label"], g["excess"]) for g in s["tiers"]] == [("฿799", 15.0)]


# ── 9. PR-SC · ต้นทุนเพิ่ม: the spike list sorted by the food cost of the extra plates, top 15 (owner 2026-10-25) ───────────
# One branch, 17 Aug .. 4 Oct 2026, 100 customers and one bill of ฿10,700 (net ฿10,000) a day. The period is Mon 28 Sep .. Sun 4 Oct
# (3 days in September, 4 in October); its baseline 7 .. 27 Sep. Per day, baseline -> period:
#   แซลมอน 10 -> 30 (excess 140)  dish cost: the analyst's list ฿50 in Sep, ฿60 in Oct (weighted (90x50 + 120x60) / 210)
#   หมึก   20 -> 60 (excess 280)  ฿10 from the cost-management table (no list row)
#   ไม่มีต้นทุน 10 -> 30 (excess 140) no cost anywhere -> last, uncosted
#   BUFFET799 10 -> 30 (excess 140) a tier PACKAGE: a sku_cost row of ฿999 that must never be used; PR-SX: in no list at all
#   ข้าว 50 -> 50 (no spike, ฿5 in the food cost) · เมนูใหม่ from 25 Sep, 5 a day, list ฿20 (new menu: period_cost 35 x 20)
_PC0, _PC1 = dt.date(2026, 9, 28), dt.date(2026, 10, 4)
_COST_ROW = {"unit_cost", "extra_cost", "cost_share", "extra_cost_per_head_pp", "uncosted", "package", "period_cost"}
_COST_TOP = {"food_cost_total", "revenue_total", "revenue_basis", "top_extra_cost", "top_cost_share", "top_pp", "uncosted_count"}


def _db_cost():
    con = sqlite3.connect(":memory:", check_same_thread=False)        # the app answers in its own worker thread
    con.row_factory = sqlite3.Row
    con.executescript("""
      CREATE TABLE branches(branch_id INTEGER PRIMARY KEY, name TEXT, branch_type TEXT);
      CREATE TABLE skus(sku_id INTEGER PRIMARY KEY, sku_code TEXT, description TEXT);
      CREATE TABLE sales_sku_daily(branch_id INT, day TEXT, sku_id INT, qty REAL, subtotal_ex_vat REAL);
      CREATE INDEX ix_ssd ON sales_sku_daily(branch_id, day, sku_id);
      CREATE VIEW v_sales_sku_daily AS SELECT * FROM sales_sku_daily
        WHERE sku_id NOT IN (SELECT sku_id FROM skus WHERE UPPER(sku_code) IN ('SERVICECHARGE','SERVICE'));
      CREATE TABLE sales_transaction(branch_id INT, receipt_no TEXT, sold_at TEXT, customer_count INT, source TEXT,
        total_inc_vat REAL, service_charge REAL);
      CREATE TABLE menu_fact_total(branch_id INT, day TEXT, tier TEXT, bills INT, pax REAL, cogs REAL, rev REAL, hc REAL,
        PRIMARY KEY(branch_id, day, tier));
      CREATE TABLE menu_fact_day(branch_id INT, day TEXT, tier TEXT, ent TEXT, bills INT, pax REAL, qty REAL,
        ent_cogs REAL, ent_hc REAL, cogs REAL, rev REAL, hc REAL, PRIMARY KEY(branch_id, day, tier, ent));
      CREATE TABLE bc_sku_cost_list(sku_id INT, month TEXT, unit_cost REAL);
      CREATE TABLE bc_sku_cost_month(sku_id INT, month TEXT, unit_cost REAL);
      CREATE TABLE sku_effective_cost(sku_id INT, recipe_cost REAL);
      CREATE TABLE sku_cost(sku_id INT, unit_cost REAL);
      CREATE TABLE bom_ingredients(sku_id INT, ingredient_cost REAL);
      CREATE TABLE bom_sku_alias(bom_sku_code TEXT, resolved_sku_id INT);
      INSERT INTO branches VALUES (1, 'สาขาทดสอบ', 'master');
      INSERT INTO skus VALUES (1, 'SALMON', '(A) แซลมอน'), (2, 'SQUID', '(A) หมึก'), (3, 'NOCOST', '(A) ไม่มีต้นทุน'),
                              (4, 'BUFFET799', '(B) บุฟเฟ่ต์ 799'), (5, 'RICE', '(A) ข้าว'), (6, 'NEWM', '(A) เมนูใหม่');
      INSERT INTO bc_sku_cost_list VALUES (1, '2026-09', 50), (1, '2026-10', 60), (6, '2026-09', 20);
      INSERT INTO sku_cost VALUES (2, 10), (4, 999), (5, 5);
    """)
    d = dt.date(2026, 8, 17)
    while d <= _PC1:
        day, p = d.isoformat(), _PC0 <= d <= _PC1
        con.execute("INSERT INTO sales_transaction VALUES (1, ?, ?, 100, 'Easy', 10700, 0)", (f"R{day}", day + " 12:00:00"))
        plan = {1: 30 if p else 10, 2: 60 if p else 20, 3: 30 if p else 10, 4: 30 if p else 10, 5: 50}
        if d >= dt.date(2026, 9, 25):
            plan[6] = 5
        for sid, q in plan.items():
            con.execute("INSERT INTO sales_sku_daily VALUES (1, ?, ?, ?, 0)", (day, sid, q))
        d += dt.timedelta(days=1)
    return con


def _cost_range(con, monkeypatch, **kw):
    return _call(con, monkeypatch, period="range", date_from=_PC0.isoformat(), date_to=_PC1.isoformat(), **kw)


def test_extra_cost_and_cost_share_two_months_of_dish_cost(monkeypatch):
    r = _cost_range(_db_cost(), monkeypatch)
    assert r["cost_view"] is True and r["sort"] == "extra_cost" and r["top"] == 15
    # the scope's food cost of the period = its COGS: 90x50 + 120x60 + 420x10 + 350x5 + the new menu 35x20 (the September list
    # price stays in force in October); the package and the uncosted menu at ฿0
    assert r["food_cost_total"] == 11700 + 4200 + 1750 + 700
    assert (r["revenue_total"], r["revenue_basis"]) == (70000.0, "net")
    s = _row(r, "แซลมอน")
    assert s["excess"] == 140 and s["unit_cost"] == round((90 * 50 + 120 * 60) / 210, 2)
    assert s["extra_cost"] == 7800.0                                     # 140 x 55.71..., not 140 x 50 nor 140 x 60
    assert s["cost_share"] == round(7800 / 18350 * 100, 2) and s["extra_cost_per_head_pp"] == round(7800 / 70000 * 100, 2)
    assert (s["uncosted"], s["package"]) == (False, False)
    q = _row(r, "หมึก")
    assert (q["unit_cost"], q["extra_cost"]) == (10.0, 2800.0)
    assert (r["top_extra_cost"], r["top_cost_share"], r["top_pp"]) == (10600.0, round(10600 / 18350 * 100, 2), round(10600 / 70000 * 100, 2))
    nm = _nm(r, "เมนูใหม่")
    assert (nm["unit_cost"], nm["period_cost"]) == (20.0, 700.0)


def test_sorted_by_extra_cost_uncosted_last_and_no_package(monkeypatch):
    r = _cost_range(_db_cost(), monkeypatch)
    assert [s["menu"] for s in r["spikes"]][:2] == ["แซลมอน", "หมึก"]          # by ฿, although หมึก has twice the extra plates
    tail = r["spikes"][2:]
    assert len(tail) == 1 and all(s["unit_cost"] == 0 and s["extra_cost"] == 0 for s in tail)
    nc = tail[0]
    assert nc["menu"] == "ไม่มีต้นทุน" and nc["uncosted"] is True and nc["package"] is False and r["uncosted_count"] == 1
    # PR-SX: the buffet tier package code is not a menu - in no list; its ฿999 sku_cost row is not in the food cost either
    assert all(s["menu"] != "บุฟเฟ่ต์ 799" for s in r["spikes"] + r["spikes_plates"] + r["new_menus"])
    assert r["food_cost_total"] == 18350                                 # 210 x ฿999 would add ฿209,790


def test_no_cost_view_keeps_the_excess_order_and_no_cost_key(monkeypatch):
    con = _db_cost()
    r = sales._menu_spike_period(con, "range", None, _PC0.isoformat(), _PC1.isoformat(), "", {}, 300, 1.5, with_cost=False)
    assert r["cost_view"] is False and r["sort"] == "excess" and r["top"] == 15
    assert [s["menu"] for s in r["spikes"]][0] == "หมึก"                    # +280 plates first, as before PR-SC
    assert not (_COST_TOP & set(r))
    for s in r["spikes"] + r["new_menus"]:
        assert not (_COST_ROW & set(s)), s


def test_top_param_sums_the_first_rows(monkeypatch):
    r = _cost_range(_db_cost(), monkeypatch, top=1)
    assert r["top"] == 1 and r["top_extra_cost"] == 7800.0 and len(r["spikes"]) == 3      # every row still comes; the card cuts


def test_the_food_cost_is_the_cogs_expression_on_the_fixture(client, app_db):
    """The scope's food cost = SUM(rules.cogs_expr) over the same rows (THE engine, not a copy of it)."""
    import rules
    j = client.get("/api/sales/menu-spike", params={"period": "range", "date_from": "2026-05-01", "date_to": "2026-06-03",
                                                    "threshold": 1.0}).json()
    assert j["cost_view"] is True and j["food_cost_total"] > 0
    con = sqlite3.connect(str(app_db))
    try:
        want = con.execute(f"SELECT SUM({rules.cogs_expr('ssm')}) FROM (SELECT branch_id, sku_id, qty, subtotal_ex_vat, "
                           f"'Easy' AS source, substr(day, 1, 7) AS period_month FROM v_sales_sku_daily "
                           f"WHERE day BETWEEN '2026-05-01' AND '2026-06-03') ssm").fetchone()[0] or 0
    finally:
        con.close()
    assert abs(j["food_cost_total"] - want) < 0.05


@pytest.fixture
def auth_on(monkeypatch):
    monkeypatch.setenv("OSHINEI_AUTH", "1")
    monkeypatch.setenv("OSHINEI_AUTH_SECRET", "test-secret-stable")
    yield


def _as(user):
    import auth
    import main
    from fastapi.testclient import TestClient
    c = TestClient(main.app)
    assert c.post("/api/auth/login", json={"username": user, "password": auth.DEFAULT_PASSWORD}).status_code == 200
    return c


def test_roles_without_cost_get_no_cost_key(client, app_db, auth_on, monkeypatch):
    """Through the app with auth on (the signed-in role decides), on the cost DB of this section."""
    con = _db_cost()

    @contextmanager
    def fake_db():
        yield con
    monkeypatch.setattr(sales, "get_db", fake_db)
    q = {"period": "range", "date_from": _PC0.isoformat(), "date_to": _PC1.isoformat()}
    mk = _as("marketing1").get("/api/sales/menu-spike", params=q)
    assert mk.status_code == 200
    a = mk.json()
    assert a.get("cost_view") is False and a["sort"] == "excess" and a["top"] == 15, a
    assert not (_COST_TOP & set(a))
    assert len(a["spikes"]) == 3 and a["new_menus"]
    for s in a["spikes"] + a["new_menus"]:
        assert not (_COST_ROW & set(s)), s
    assert a["spikes"][0]["menu"] == "หมึก"
    ex = [x["excess"] for x in a["spikes"]]
    assert ex == sorted(ex, reverse=True)
    # operation holds neither menu.analysis nor customer.buffet: the card's data is refused altogether
    assert _as("operation1").get("/api/sales/menu-spike", params=q).status_code == 403
    for user in ("management1", "analyst1"):
        b = _as(user).get("/api/sales/menu-spike", params=q).json()
        assert b.get("cost_view") is True and _COST_TOP <= set(b), (user, b)
        assert all({"unit_cost", "extra_cost", "cost_share", "extra_cost_per_head_pp"} <= set(s) for s in b["spikes"])
        # the plates are the same answer for everyone; only the order and the cost keys differ
        key = lambda r: sorted((s["menu"], s["branch"], s["today"], s["excess"]) for s in r["spikes"])
        assert key(a) == key(b)
        assert [x["menu"] for x in b["spikes"]][:2] == ["แซลมอน", "หมึก"]


# ── 10. PR-TD · an existing menu that came back goes to the new-menu part ("กลับมาขาย", owner 2026-10-25) ────────────────────────
# A menu first sold >= 28 days before the period end that sold again after >= 21 days without a sale at the branch (the branch itself
# open on >= 14 of those days) is a new menu again from the day it came back (returned_on = first_sold), for 28 days.
def _back_plan(gap_from, back_on, closed=False):
    """เมนูเล็ก (sku 5): 10 a day 1 Jul .. gap_from - 1, nothing to back_on - 1, 10 a day from back_on; BASE 10 a day. closed: the
    branch has no row at all during the gap (a branch shut for three weeks is not a menu that stopped)."""
    def plan(d):
        if closed and gap_from <= d < back_on:
            return {}
        p = {1: 10}
        if d < gap_from or d >= back_on:
            p[5] = 10
        return p
    return plan


def test_a_menu_back_after_21_days_is_a_new_menu_from_that_day(monkeypatch):
    # no sale 6 .. 27 Jul (22 days), back on Tue 28 Jul
    con = _db(_back_plan(dt.date(2026, 7, 6), dt.date(2026, 7, 28)))
    for kw in ({"date": "2026-08-20"}, {"period": "week", "date": "2026-08-20"}, {"period": "mtd", "date": "2026-08-20"}):
        r = _call(con, monkeypatch, threshold=1.0, **kw)
        m = _nm(r, "เมนูเล็ก")
        assert m is not None and _row(r, "เมนูเล็ก") is None, kw
        assert (m["returning"], m["returned_on"], m["first_sold"], m["first_sold_th"], m["first_sold_ever"], m["age_days"]) == (
            True, "2026-07-28", "2026-07-28", "28 ก.ค.", "2026-07-01", 24), kw
        assert r["new_count"] == 1, kw
    # a range from 1 Jul: the plates are the ones since it came back (24 days x 10), not the 50 before the gap
    r = _call(con, monkeypatch, period="range", date_from="2026-07-01", date_to="2026-08-20")
    m = _nm(r, "เมนูเล็ก")
    assert (m["qty"], m["per_day"]) == (240, 10.0)
    # 28 days after it came back (24 Aug = day 28: still new; 25 Aug: existing, the spike rule)
    assert _nm(_call(con, monkeypatch, threshold=1.0, date="2026-08-24"), "เมนูเล็ก")["age_days"] == 28
    r = _call(con, monkeypatch, threshold=1.0, date="2026-08-25")
    assert _nm(r, "เมนูเล็ก") is None and _row(r, "เมนูเล็ก")["is_new"] is False
    assert all("returning" not in s for s in r["spikes"])


def test_a_return_before_the_period_counts_only_the_periods_plates(monkeypatch):
    # DV1 (phone table review 2026-10-25, §5 no. 1): back on 28 Jul, period = one day (20 Aug) - the plates are that day's 10, not
    # the 240 since 28 Jul divided by one day's customers (the 1,515 plates / 2,295 per 100 customers of the review)
    con = _db(_back_plan(dt.date(2026, 7, 6), dt.date(2026, 7, 28)))
    m = _nm(_call(con, monkeypatch, threshold=1.0, date="2026-08-20"), "เมนูเล็ก")
    assert (m["qty"], m["per_day"], m["returned_on"]) == (10, 10.0, "2026-07-28")
    # this week (Mon 17 .. Thu 20 Aug): 4 days x 10
    m = _nm(_call(con, monkeypatch, threshold=1.0, period="week", date="2026-08-20"), "เมนูเล็ก")
    assert (m["qty"], m["per_day"]) == (40, 10.0)


def test_a_20_day_gap_or_a_closed_branch_is_not_a_return(monkeypatch):
    # 20 days without a sale (6 .. 25 Jul): an existing menu
    r = _call(_db(_back_plan(dt.date(2026, 7, 6), dt.date(2026, 7, 26))), monkeypatch, threshold=1.0, date="2026-08-10")
    assert _nm(r, "เมนูเล็ก") is None and _row(r, "เมนูเล็ก") is not None
    # 22 days, but the branch itself was shut (no rows at all): not a return either
    r = _call(_db(_back_plan(dt.date(2026, 7, 6), dt.date(2026, 7, 28), closed=True)), monkeypatch, threshold=1.0, date="2026-08-10")
    assert _nm(r, "เมนูเล็ก") is None and _row(r, "เมนูเล็ก") is not None
    # a brand-new menu is never "returning"
    r = _call(_db(_launch_plan), monkeypatch, date="2026-08-20")
    assert all("returning" not in m for m in r["new_menus"]) and r["new_menus"]


# ── 11. PR-TD · กำหนดเอง เทียบกับ a chosen period (base_from / base_to, owner 2026-10-25) ────────────────────────────────────
# The period is Mon 17 .. Sun 23 Aug (_P0.._P1). The base week is Mon 3 .. Sun 9 Aug: its per-head rate is the normal, over the whole of
# it (not per weekday), and the period and the base may differ in length.
_B0, _B1 = dt.date(2026, 8, 3), dt.date(2026, 8, 9)


def _in_base(d):
    return _B0 <= d <= _B1


def test_compare_with_a_week_doubled_customers_and_plates_is_not_a_spike(monkeypatch):
    # customers x2 in the period; ข้าวปั้น 0.5 a head in the base week and in the period, 0.2 a head on every other day:
    # against ปกติ (the 3 weeks before, mostly 0.2 a head) the period spikes; against the base week it is x1.0
    k = lambda d: 2 if _in(d) else 1
    head = lambda d: 0.5 if _in(d) or _in_base(d) else 0.2
    con = _db_pax(lambda d: {"799": 60 * k(d), "599": 40 * k(d), "nb": 0},
                  lambda d: {1: {"799": round(60 * k(d) * head(d)), "599": round(40 * k(d) * head(d))}})
    assert _row(_spike_range(con, monkeypatch), "ข้าวปั้น") is not None          # ปกติ: flagged
    r = _spike_range(con, monkeypatch, base_from=_B0.isoformat(), base_to=_B1.isoformat())
    assert r["count"] == 0 and r["base"] == {"kind": "range", "from": "2026-08-03", "to": "2026-08-09"}
    assert (r["baseline_from"], r["baseline_to"]) == ("2026-08-03", "2026-08-09")
    s = _row(_spike_range(con, monkeypatch, threshold=1.0, base_from=_B0.isoformat(), base_to=_B1.isoformat()), "ข้าวปั้น")
    assert (s["customers"], s["today"], s["rate"], s["normal_rate"], s["x"], s["excess"]) == (1400, 700, 50.0, 50.0, 1.0, 0)
    assert all(abs(g["excess"]) < 0.5 for g in s["tiers"])
    # the strip: customers per day of the period vs the base week
    sm = r["summary"]
    assert (sm["customers"], sm["customers_per_day"], sm["base_customers_per_day"], sm["customers_vs_base_pct"]) == (1400, 200.0, 100.0, 100.0)
    # a 3-day base (3 .. 5 Aug) against the 7-day period: per head, the same answer
    s3 = _row(_spike_range(con, monkeypatch, threshold=1.0, base_from="2026-08-03", base_to="2026-08-05"), "ข้าวปั้น")
    assert (s3["normal_rate"], s3["x"], s3["excess"]) == (50.0, 1.0, 0)


def test_compare_with_a_week_the_math_and_the_tiers(monkeypatch):
    # 100 customers a day everywhere; the base week 0.5 a head, the period 1.0 a head -> x2.0, +350 plates (799: +210, 599: +140)
    head = lambda d: 1.0 if _in(d) else 0.5 if _in_base(d) else 0.8
    con = _db_pax(lambda d: {"799": 60, "599": 40, "nb": 0},
                  lambda d: {1: {"799": round(60 * head(d)), "599": round(40 * head(d))}})
    r = _spike_range(con, monkeypatch, base_from=_B0.isoformat(), base_to=_B1.isoformat())
    s = _row(r, "ข้าวปั้น")
    assert (s["today"], s["rate"], s["normal_rate"], s["x"], s["excess"], s["expected"]) == (700, 100.0, 50.0, 2.0, 350, 350)
    assert sorted((g["label"], g["excess"]) for g in s["tiers"]) == [("฿599", 140.0), ("฿799", 210.0)]
    assert r["summary"]["tier_top"] == {"label": "฿799", "excess": 210} and r["summary"]["top_excess"] == 350
    assert r["summary"]["customers_vs_base_pct"] == 0.0
    # ปกติ (27 Jul .. 16 Aug: 0.8 a head on 14 days, 0.5 on 7) -> a smaller x than against the base week
    n = _row(_spike_range(con, monkeypatch, threshold=1.0), "ข้าวปั้น")
    assert n["x"] < s["x"] and r["base"]["kind"] == "range" and _spike_range(con, monkeypatch)["base"]["kind"] == "normal"


@pytest.mark.parametrize("base, word", [
    ({"base_from": "2026-08-03"}, "ต้องระบุ"),
    ({"base_from": "2026-08-09", "base_to": "2026-08-03"}, "ไม่อยู่หลัง"),
    ({"base_from": "2026-08-10", "base_to": "2026-08-17"}, "สิ้นสุดก่อนวันเริ่มต้น"),          # touches the period
    ({"base_from": "2026-08-18", "base_to": "2026-08-20"}, "สิ้นสุดก่อนวันเริ่มต้น"),          # inside it
    ({"base_from": "2026-03-01", "base_to": "2026-08-09"}, "92"),
    ({"base_from": "2026-08-03", "base_to": "2026/08/09"}, "ไม่ถูกต้อง"),
])
def test_compare_validation_answers_400_in_formal_thai(monkeypatch, base, word):
    from fastapi import HTTPException
    con = _db(_weekend_plan)
    with pytest.raises(HTTPException) as e:
        _spike_range(con, monkeypatch, **base)
    assert e.value.status_code == 400
    msg = e.value.detail
    assert word in msg and "กรุณา" not in msg and "ระบบจะ" not in msg, msg


def test_compare_base_must_be_finished_days(client):
    d = client.get("/api/sales/menu-spike", params={"period": "range", "date_from": "2026-06-01", "date_to": "2026-06-03"}).json()
    r = client.get("/api/sales/menu-spike", params={"period": "range", "date_from": "2026-05-25", "date_to": "2026-05-31",
                                                     "base_from": "2026-05-30", "base_to": "2026-06-10"})
    assert r.status_code == 400 and d["latest_day"] in r.json()["detail"]
    ok = client.get("/api/sales/menu-spike", params={"period": "range", "date_from": "2026-05-25", "date_to": "2026-05-31",
                                                      "base_from": "2026-05-18", "base_to": "2026-05-24"}).json()
    assert ok["base"] == {"kind": "range", "from": "2026-05-18", "to": "2026-05-24"} and "summary" in ok


# ── 12. PR-TD · แบบเดิม: the raw-plates list at the bottom of the card (owner 2026-10-25) ──────────────────────────────────────
_PLATE_KEYS = {"branch", "menu", "today", "per_day", "avg", "expected", "x", "pct", "excess", "is_new", "kind", "in_per_head"}


def test_raw_plates_flag_a_busy_week_that_per_head_does_not(monkeypatch):
    # customers x2 and plates x2 in the period: per head x1.0 (not a spike); raw plates x2.0 (listed in spikes_plates)
    k = lambda d: 2 if _in(d) else 1
    con = _db_pax(lambda d: {"799": 60 * k(d), "599": 40 * k(d), "nb": 20},
                  lambda d: {1: {"799": 30 * k(d), "599": 20 * k(d)}})
    r = _spike_range(con, monkeypatch)
    assert r["count"] == 0 and _row(r, "ข้าวปั้น") is None
    assert r["plates_count"] == 1
    s = r["spikes_plates"][0]
    assert set(s) == _PLATE_KEYS
    assert (s["menu"], s["today"], s["expected"], s["x"], s["pct"], s["excess"]) == ("ข้าวปั้น", 700, 350, 2.0, 100, 350)
    # against a chosen base week with the same busy customers it is a plain x2.0 too (plates per trading day)
    rb = _spike_range(con, monkeypatch, base_from=_B0.isoformat(), base_to=_B1.isoformat())
    assert rb["count"] == 0 and rb["spikes_plates"][0]["x"] == 2.0


def test_raw_plates_keep_the_rules_new_and_returning_menus_excluded(monkeypatch):
    # day mode on the PR-SP plate standard: the weekday-fair "ปกติ" plates; a new menu and a returning menu are never in the list
    def plan(d):
        p = {1: 10, 2: 30 if d.weekday() in SAT_SUN else 2}
        if d == dt.date(2026, 8, 20):
            p.update({1: 40, 3: 25, 6: 30})              # ข้าวปั้น x4; เมนูใหม่ new; เมนูใหม่น้อย back after 50 days
        if d == dt.date(2026, 7, 1):
            p[6] = 1
        if d == dt.date(2026, 8, 19):
            p[3] = 5
        return p
    r = _call(_db(plan), monkeypatch, date="2026-08-20")
    menus = [s["menu"] for s in r["spikes_plates"]]
    assert menus == ["ข้าวปั้น"] and r["spikes_plates"][0]["x"] == 4.0 and r["plates_count"] == 1
    assert _nm(r, "เมนูใหม่") is not None and _nm(r, "เมนูใหม่น้อย")["returning"] is True
    # sorted by +จาน, every row, the limit applies
    def many(d):
        p = {1: 10, 5: 10, 2: 10}
        if _in(d):
            p.update({1: 40, 5: 30, 2: 50})
        return p
    r = _call(_db(many), monkeypatch, period="range", date_from=_P0.isoformat(), date_to=_P1.isoformat())
    assert [s["menu"] for s in r["spikes_plates"]] == ["ซาชิมิ", "ข้าวปั้น", "เมนูเล็ก"] and r["plates_count"] == 3
    r = _call(_db(many), monkeypatch, period="range", date_from=_P0.isoformat(), date_to=_P1.isoformat(), limit=2)
    assert len(r["spikes_plates"]) == 2 and r["plates_count"] == 3


# ── 13. PR-SX (owner 2026-10-25, "แก้เลย") · no buffet package in any list; raw-plates rows say whether per head lists them too ─────────
def _db_pkg():
    """One branch, 1 Jul .. 31 Aug, no customer counts (basis "days"). In the period Mon 17 .. Sun 23 Aug every line below x3:
    the package lines BUFFET (adult), BUFFETCHILD, BUFFETCHILDFREE and the legacy tier code B01; a real dish B0101 (not a tier
    code); and BUFFETNEW, a package variant first sold on 18 Aug (it would be a new menu)."""
    con = _db(lambda d: {1: 30 if _in(d) else 10})
    con.executescript("""
      INSERT INTO skus VALUES (20, 'BUFFET', 'บุฟเฟ่ต์ (ผู้ใหญ่)'), (21, 'BUFFETCHILD', 'บุฟเฟ่ต์ (เด็ก)'),
                              (22, 'BUFFETCHILDFREE', 'BUFFETCHILD_FREE'), (23, 'B01', 'บุฟเฟ่ต์ 299'),
                              (24, 'B0101', '(B) ปลาหมึกย่าง'), (25, 'BUFFETNEW', 'บุฟเฟ่ต์ 999');
    """)
    d = dt.date(2026, 7, 1)
    while d <= dt.date(2026, 8, 31):
        for sid in (20, 21, 22, 23, 24):
            con.execute("INSERT INTO sales_sku_daily VALUES (1, ?, ?, ?, 0)", (d.isoformat(), sid, 30 if _in(d) else 10))
        if d >= dt.date(2026, 8, 18):
            con.execute("INSERT INTO sales_sku_daily VALUES (1, ?, 25, 40, 0)", (d.isoformat(),))
        d += dt.timedelta(days=1)
    return con


_PKG_NAMES = {"บุฟเฟ่ต์ (ผู้ใหญ่)", "บุฟเฟ่ต์ (เด็ก)", "BUFFETCHILD_FREE", "บุฟเฟ่ต์ 299", "บุฟเฟ่ต์ 999"}


@pytest.mark.parametrize("cost", [True, False])
def test_buffet_packages_are_in_no_list_and_no_count(monkeypatch, cost):
    con = _db_pkg()
    r = sales._menu_spike_period(con, "range", None, _P0.isoformat(), _P1.isoformat(), "", {}, 300, 1.5, with_cost=cost)
    names = [x["menu"] for x in r["spikes"] + r["spikes_plates"] + r["new_menus"]]
    assert not (_PKG_NAMES & set(names)), names
    # the real dishes stay: ข้าวปั้น and the (B) dish whose code starts with B01 but is not the tier code B01
    assert {s["menu"] for s in r["spikes"]} == {"ข้าวปั้น", "ปลาหมึกย่าง"}
    assert (r["count"], r["plates_count"], r["new_count"]) == (2, 2, 0)
    assert r["summary"]["top_excess"] == 2 * (210 - 70)
    # the same answer through the endpoint, the day mode too
    d = _call(con, monkeypatch, date="2026-08-20")
    assert not (_PKG_NAMES & {x["menu"] for x in d["spikes"] + d["spikes_plates"] + d["new_menus"]})


def test_raw_plates_rows_say_whether_per_head_lists_them_too(monkeypatch):
    # customers x2 in the period: ข้าวปั้น plates x2 (per head x1.0 - only in แบบเดิม); ซาชิมิ plates x4 (per head x2.0 - in both)
    k = lambda d: 2 if _in(d) else 1
    con = _db_pax(lambda d: {"799": 60 * k(d), "599": 40 * k(d), "nb": 20},
                  lambda d: {1: {"799": 30 * k(d), "599": 20 * k(d)}})
    d = dt.date(2026, 7, 1)
    while d <= dt.date(2026, 8, 31):
        con.execute("INSERT INTO sales_sku_daily VALUES (1, ?, 7, ?, 0)", (d.isoformat(), 40 if _in(d) else 10))
        d += dt.timedelta(days=1)
    r = _spike_range(con, monkeypatch)
    assert [s["menu"] for s in r["spikes"]] == ["(AL) ราเมง"]
    by = {s["menu"]: s["in_per_head"] for s in r["spikes_plates"]}
    assert by == {"(AL) ราเมง": True, "ข้าวปั้น": False}
    assert (r["plates_count"], r["plates_only_count"]) == (2, 1)


# ── 12. PR-RK · the existing-menu list ranks by extra cost, no 1.5x gate (owner 2026-10-25, "เมนูที่นิยมไม่ติดเลย เรียงผิด") ──────
# One branch, 100 customers a day, the period 28 Sep .. 4 Oct (7 days), its normal the 21 days before. Per day, baseline -> period:
#   ยอดนิยม   200 -> 220 (x1.1, +140 plates, ฿30 a plate = ฿4,200)   the popular menu 10 % up per head: the largest extra cost
#   หมึก      100 -> 130 (x1.3, +210 plates, ฿5 = ฿1,050)              the most extra plates
#   นาน ๆ ที  2 -> 6     (x3.0, +28 plates,  ฿20 = ฿560)               the old rule: spike, the chip
#   เกือบเท่า 100 -> 103 (x1.03, +21 plates) under x1.05 -> not listed
#   นิดเดียว  10 -> 10, 12 on the last day (+2 plates) under +3 plates -> not listed
#   (OT) ค่าปรับบุฟเฟ่ต์ 789 Net (10 กรัม) 1 -> 20, ค่ามัดจำจาน 1 -> 10, คูปองมูลค่า789 1 -> 10: fee lines, in no section
def _db_rank():
    con = sqlite3.connect(":memory:", check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.executescript("""
      CREATE TABLE branches(branch_id INTEGER PRIMARY KEY, name TEXT, branch_type TEXT);
      CREATE TABLE skus(sku_id INTEGER PRIMARY KEY, sku_code TEXT, description TEXT);
      CREATE TABLE sales_sku_daily(branch_id INT, day TEXT, sku_id INT, qty REAL, subtotal_ex_vat REAL);
      CREATE INDEX ix_ssd ON sales_sku_daily(branch_id, day, sku_id);
      CREATE VIEW v_sales_sku_daily AS SELECT * FROM sales_sku_daily
        WHERE sku_id NOT IN (SELECT sku_id FROM skus WHERE UPPER(sku_code) IN ('SERVICECHARGE','SERVICE'));
      CREATE TABLE sales_transaction(branch_id INT, receipt_no TEXT, sold_at TEXT, customer_count INT, source TEXT,
        total_inc_vat REAL, service_charge REAL);
      CREATE TABLE bc_sku_cost_list(sku_id INT, month TEXT, unit_cost REAL);
      CREATE TABLE bc_sku_cost_month(sku_id INT, month TEXT, unit_cost REAL);
      CREATE TABLE sku_effective_cost(sku_id INT, recipe_cost REAL);
      CREATE TABLE sku_cost(sku_id INT, unit_cost REAL);
      CREATE TABLE bom_ingredients(sku_id INT, ingredient_cost REAL);
      CREATE TABLE bom_sku_alias(bom_sku_code TEXT, resolved_sku_id INT);
      INSERT INTO branches VALUES (1, 'สาขาทดสอบ', 'master');
      INSERT INTO skus VALUES (1, 'POP', '(A) ยอดนิยม'), (2, 'SQUID', '(A) หมึก'), (3, 'RARE', '(A) นาน ๆ ที'),
        (4, 'FLAT', '(A) เกือบเท่า'), (5, 'TINY', '(A) นิดเดียว'),
        (6, 'OTHOTOTH00011', '(OT) ค่าปรับบุฟเฟ่ต์ 789 Net (10 กรัม)'), (7, 'DEP1', 'ค่ามัดจำจาน'), (8, 'OT0000110', 'คูปองมูลค่า789');
      INSERT INTO sku_cost VALUES (1, 30), (2, 5), (3, 20), (4, 30), (5, 30), (6, 50), (7, 50), (8, 50);
    """)
    d = dt.date(2026, 8, 17)
    while d <= _PC1:
        day, p = d.isoformat(), _PC0 <= d <= _PC1
        con.execute("INSERT INTO sales_transaction VALUES (1, ?, ?, 100, 'Easy', 10700, 0)", (f"R{day}", day + " 12:00:00"))
        plan = {1: 220 if p else 200, 2: 130 if p else 100, 3: 6 if p else 2, 4: 103 if p else 100,
                5: 12 if d == _PC1 else 10, 6: 20 if p else 1, 7: 10 if p else 1, 8: 10 if p else 1}
        for sid, q in plan.items():
            con.execute("INSERT INTO sales_sku_daily VALUES (1, ?, ?, ?, 0)", (day, sid, q))
        d += dt.timedelta(days=1)
    return con


_FEES = {"(OT) ค่าปรับบุฟเฟ่ต์ 789 Net (10 กรัม)", "ค่ามัดจำจาน", "คูปองมูลค่า789"}


def _rank(cost=True, threshold=1.5):
    return sales._menu_spike_period(_db_rank(), "range", None, _PC0.isoformat(), _PC1.isoformat(), "", {}, 300, threshold,
                                    with_cost=cost)


def test_the_ranked_list_puts_the_popular_menu_with_the_largest_extra_cost_first(monkeypatch):
    r = _cost_range(_db_rank(), monkeypatch)
    assert [(s["menu"], s["x"], s["excess"], s["extra_cost"], s["spike"]) for s in r["spikes"]] == [
        ("ยอดนิยม", 1.1, 140, 4200.0, False), ("หมึก", 1.3, 210, 1050.0, False), ("นาน ๆ ที", 3.0, 28, 560.0, True)]
    assert (r["count"], r["spike_count"]) == (3, 1)
    assert r["top_extra_cost"] == 4200 + 1050 + 560                  # ต้นทุนเพิ่มรวม = the first `top` rows of the ranked list
    assert _cost_range(_db_rank(), monkeypatch, top=1)["top_extra_cost"] == 4200.0


def test_the_chip_threshold_and_the_noise_floor():
    r = _rank(threshold=1.25)
    assert {s["menu"]: s["spike"] for s in r["spikes"]} == {"ยอดนิยม": False, "หมึก": True, "นาน ๆ ที": True}
    assert r["spike_count"] == 2 and r["threshold"] == 1.25
    names = {s["menu"] for s in _rank()["spikes"]}
    assert "เกือบเท่า" not in names and "นิดเดียว" not in names            # x1.03 and +2 plates: under the noise floor
    # a chip threshold of 1.0 makes every row of >= 5 plates above normal a spike, and a spike is always listed
    assert {"เกือบเท่า", "นิดเดียว"} <= {s["menu"] for s in _rank(threshold=1.0)["spikes"]}


def test_a_role_without_cost_ranks_by_extra_plates():
    r = _rank(cost=False)
    assert [s["menu"] for s in r["spikes"]] == ["หมึก", "ยอดนิยม", "นาน ๆ ที"]
    assert [s["spike"] for s in r["spikes"]] == [False, False, True] and r["spike_count"] == 1
    assert not (_COST_ROW & {k for s in r["spikes"] for k in s})


@pytest.mark.parametrize("cost", [True, False])
def test_fee_lines_are_in_no_section(cost):
    r = _rank(cost=cost)
    names = {x["menu"] for x in r["spikes"] + r["spikes_plates"] + r["new_menus"]}
    assert not (_FEES & names), names
    assert r["count"] == 3 and r["new_count"] == 0
    if cost:
        assert all(s["unit_cost"] != 50 for s in r["spikes"])


def test_a_raw_plates_row_is_per_head_only_when_the_ranked_row_is_a_spike():
    # same customers every day, so raw x = per-head x: หมึก x1.3 is a raw-plates row under a 1.25 threshold and a ranked spike too;
    # under 1.5 only นาน ๆ ที is in แบบเดิม, and it is a ranked spike
    r = _rank(cost=False, threshold=1.25)
    assert {s["menu"]: s["in_per_head"] for s in r["spikes_plates"]} == {"หมึก": True, "นาน ๆ ที": True}
    r = _rank(cost=False, threshold=1.5)
    assert {s["menu"]: s["in_per_head"] for s in r["spikes_plates"]} == {"นาน ๆ ที": True}
    assert r["plates_only_count"] == 0


@pytest.mark.parametrize("code,desc,want", [
    ("OTHOTOTH00011", "(OT) ค่าปรับบุฟเฟ่ต์ 789 Net (10 กรัม)", True), ("OT0007", "ค่าปรับบุฟเฟ่ต์ 350++ (100 กรัม)", True),
    ("OT0021", "ค่าปรับบุฟเฟต์ 1150++ + (100 กรัม)", True), ("X1", "ค่าบริการจัดส่ง", True), ("X2", "ค่ามัดจำจาน", True),
    ("X3", "ค่าเสียหายอุปกรณ์", True), ("OTHOTOTH00002", "(OT) ค่าเปิดห้อง (เล็ก)", True), ("OT0018", "ค่าเปิดเหล้า,ไวน์,เบียร์(100)", True),
    ("OT0000110", "คูปองมูลค่า789", True), ("X4", "  (ot) อื่น ๆ", True), ("SERVICECHARGE", "Service", True),
    ("BUFFET799", "(B) บุฟเฟ่ต์ 799", True), ("B01", "บุฟเฟ่ต์ (ผู้ใหญ่)", True),
    ("A001", "(A) ซาชิมิแซลมอน", False), ("B0123", "(B) ซอฟต์ครีมมัตฉะ", False), ("CR0001", "ค่าอาหารจัดเลี้ยง", False),
    (None, None, False)])
def test_the_non_food_predicate_and_its_sql_twin_agree(code, desc, want):
    from rules import is_non_food_line, non_food_sql
    assert is_non_food_line(code, desc) is want
    con = sqlite3.connect(":memory:")
    assert bool(con.execute(f"SELECT {non_food_sql('?1', '?2')}", (code, desc)).fetchone()[0]) is want
