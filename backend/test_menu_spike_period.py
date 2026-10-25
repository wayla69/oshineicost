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
- the pure JS helper periodRange(kind, anchor) of the card (lifted into js/01-core.js by PR-PP1).
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
    assert _call(con, monkeypatch, date="2026-08-20", threshold=5.0)["count"] == 0


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
                 "x": 2.5, "pct": 150, "excess": 90, "is_new": False, "kind": "buffet", "basis": "days", "customers": None,
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
    def plan(d):
        p = {1: 10}
        if d == dt.date(2026, 7, 1) or d in (dt.date(2026, 8, 5), dt.date(2026, 8, 10), dt.date(2026, 8, 15)):
            p[5] = 2
        if d == dt.date(2026, 8, 20):
            p[5] = 30
        if d in (dt.date(2026, 7, 1), dt.date(2026, 8, 20)):
            p[6] = 25                                            # เมนูใหม่น้อย: old, no baseline sale at all
            p[2] = 1 if d.month == 7 else 19                     # ซาชิมิ: old, thin, 19 plates < 20 -> not listed
        if d == dt.date(2026, 7, 1) or dt.date(2026, 8, 6) <= d <= dt.date(2026, 8, 8):
            p[3] = 4                                             # เมนูใหม่ (sku 3): old, thin, 3 days x 4 = 12 -> 0.57 a day
        if d == dt.date(2026, 8, 20):
            p[3] = 22
        return p
    con = _db(plan)
    r = _call(con, monkeypatch, date="2026-08-20")
    # day mode (PR-ST: weekday-fair): Thu 20 Aug vs the Thursdays 30 Jul, 6 and 13 Aug, none of which sold เมนูเล็ก -> "ปกติ" 0
    assert _row(r, "เมนูเล็ก") == {"branch": "สาขาทดสอบ", "menu": "เมนูเล็ก", "today": 30, "per_day": 30.0, "avg": 0.0,
                                   "expected": 0, "x": None, "pct": None, "excess": 30, "is_new": False, "thin_base": True,
                                   "kind": "alc", "basis": "days", "customers": None, "rate": None, "normal_rate": None,
                                   "tiers": None, "tier_note": "ยังไม่มีข้อมูลแยก tier"}
    s = _row(r, "เมนูใหม่น้อย")
    assert (s["today"], s["avg"], s["x"], s["pct"], s["excess"], s["thin_base"]) == (25, 0.0, None, None, 25, True)
    assert _row(r, "ซาชิมิ") is None and r["new_menus"] == []
    assert _row(r, "เมนูใหม่")["x"] == 16.5                      # thin, 22 plates vs Thursdays 0 + 4 + 0 = 1.33: listed
    # thin with a baseline but under the threshold: not listed (the same threshold as every spike); no baseline: listed, x "—"
    r3 = _call(con, monkeypatch, date="2026-08-20", threshold=20.0)
    assert _row(r3, "เมนูใหม่") is None and _row(r3, "เมนูเล็ก") is not None and _row(r3, "เมนูใหม่น้อย")["x"] is None
    assert _row(_call(con, monkeypatch, date="2026-08-20", threshold=16.0), "เมนูใหม่")["x"] == 16.5
    # week of 17 Aug (to Thu 20): weekday-fair "ปกติ" = Mon 2/3 + Wed 2/3 = 1.33 plates
    r = _call(con, monkeypatch, period="week", date="2026-08-20")
    s = _row(r, "เมนูเล็ก")
    assert (s["today"], s["avg"], s["expected"], s["x"], s["excess"], s["thin_base"]) == (30, 1.3, 1, 22.5, 29, True)
    assert _row(r, "เมนูใหม่น้อย")["x"] is None and _row(r, "เมนูใหม่น้อย")["excess"] == 25
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
        con.execute("INSERT INTO sales_sku_daily VALUES (1, '2026-07-01', 3, 1, 0)")   # an older sale appears (a sync)
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
