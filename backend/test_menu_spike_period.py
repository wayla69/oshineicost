"""PR-SP · /api/sales/menu-spike by period: day (unchanged), this week, MTD, a custom range.

- day mode returns what the code before PR-SP returned (a frozen copy of it is below), except that new menus
  left `spikes` for their own list (PR-SN, owner 2026-10-24: new = period end - first day sold < 28 days);
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


# ── a frozen copy of the day mode before PR-SP (the reference the day mode must still match) ──
def _day_reference(con, date=None, branch=None, group=None, limit=300, threshold=1.5):
    maxd = con.execute("SELECT MAX(day) FROM sales_sku_daily").fetchone()[0]
    if not maxd:
        return {"date": None, "spikes": []}
    focus = (date or maxd)[:10]
    fy, fm, fd = (int(x) for x in focus.split("-"))
    wstart = (dt.date(fy, fm, fd) - dt.timedelta(days=21)).isoformat()
    bfilt = ""; params = {"focus": focus, "ws": wstart}
    if branch:
        bfilt += " AND b.name=:branch"; params["branch"] = branch
    if group:
        bfilt += " AND COALESCE(b.branch_type,'master')=:grp"; params["grp"] = group
    rows = con.execute(f"""
        WITH today AS (
          SELECT d.branch_id, d.sku_id, SUM(d.qty) q
          FROM v_sales_sku_daily d JOIN branches b ON b.branch_id=d.branch_id
          WHERE d.day=:focus {bfilt} GROUP BY d.branch_id, d.sku_id),
        hist AS (
          SELECT branch_id, sku_id, AVG(qty) avgq, COUNT(*) n
          FROM v_sales_sku_daily WHERE day>=:ws AND day<:focus GROUP BY branch_id, sku_id)
        SELECT b.name branch, s.description, s.sku_code, t.q today,
               COALESCE(h.avgq,0) avgq, COALESCE(h.n,0) n
        FROM today t JOIN branches b ON b.branch_id=t.branch_id
        JOIN skus s ON s.sku_id=t.sku_id
        LEFT JOIN hist h ON h.branch_id=t.branch_id AND h.sku_id=t.sku_id
        WHERE t.q>0""", params).fetchall()
    spikes = []
    for r in rows:
        today = r["today"] or 0; avg = r["avgq"] or 0; n = r["n"] or 0
        name = (r["description"] or r["sku_code"] or "").replace("(B) ", "").replace("(A) ", "")
        if avg > 0 and n >= 5:
            x = today / avg
            if x >= threshold and today >= 5:
                spikes.append({"branch": r["branch"], "menu": name, "today": round(today),
                               "avg": round(avg, 1), "x": round(x, 1), "pct": round((x - 1) * 100),
                               "excess": round(today - avg), "is_new": False})
        elif n < 5 and today >= 20:
            spikes.append({"branch": r["branch"], "menu": name, "today": round(today),
                           "avg": 0, "x": None, "pct": None, "excess": round(today), "is_new": True})
    spikes.sort(key=lambda s: -s["excess"])
    return {"date": focus, "window_days": 21, "threshold": threshold,
            "count": len(spikes), "spikes": spikes[:limit]}


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


# ── 1. day mode unchanged ─────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("params", [{}, {"date": "2026-06-02"}, {"branch": "BranchA"}, {"group": "franchise"}])
def test_day_mode_on_the_fixture_matches_the_code_before(client, app_db, params):
    old = sqlite3.connect(str(app_db)); old.row_factory = sqlite3.Row
    try:
        want = _day_reference(old, **params)
    finally:
        old.close()
    for extra in ({}, {"period": "day"}):
        r = client.get("/api/sales/menu-spike", params={"threshold": 1.5, "limit": 300, **params, **extra})
        assert r.status_code == 200, (params, extra)
        got = r.json()
        _assert_day_matches_without_new(got, want, str(app_db))


def _first_by_name(con):
    """(branch name, menu name as the card prints it) -> the first day sold, as a date."""
    out = {}
    for b, desc, code, d in con.execute("""
            SELECT b.name, s.description, s.sku_code, MIN(x.day) FROM sales_sku_daily x
            JOIN branches b ON b.branch_id=x.branch_id JOIN skus s ON s.sku_id=x.sku_id
            WHERE x.qty > 0 GROUP BY x.branch_id, x.sku_id""").fetchall():
        name = (desc or code or "").replace("(B) ", "").replace("(A) ", "")
        out[(b, name)] = min(out.get((b, name), d[:10]), d[:10])
    return {k: dt.date.fromisoformat(v) for k, v in out.items()}


def _assert_day_matches_without_new(got, want, db):
    """PR-SN: the day mode = the frozen reference, except that (1) its rows of menus first sold < 28 days ago left for
    new_menus and (2) its old is_new rows (n < 5 baseline days, >= 20 plates) of menus older than that stay in spikes
    as thin_base rows ("ปกติ" over the branch's trading days, not the days sold); every other row and key unchanged."""
    con = sqlite3.connect(db) if isinstance(db, str) else db
    first = _first_by_name(con)
    focus = dt.date.fromisoformat(want["date"]) if want.get("date") else None

    def is_new(s):
        f = first.get((s["branch"], s["menu"]))
        return f is not None and (focus - f).days < 28
    keep = [s for s in want["spikes"] if not s["is_new"] and not is_new(s)]
    thin_old = {(s["branch"], s["menu"]) for s in want["spikes"] if s["is_new"] and not is_new(s)}
    thin_got = [s for s in got["spikes"] if s.get("thin_base")]
    # a thin row is one of those old is_new rows that also clears the threshold (or has no baseline)
    assert {(s["branch"], s["menu"]) for s in thin_got} <= thin_old
    assert all(s["x"] is None or s["x"] >= want["threshold"] for s in thin_got)
    assert all(s["is_new"] is False and s["today"] >= 20 for s in thin_got)
    rest = [s for s in got["spikes"] if not s.get("thin_base")]
    exp = {**want, "spikes": keep, "count": len(keep) + len(thin_got)} if want.get("date") else want
    assert {**{k: v for k, v in got.items() if k not in ("new_menus", "new_count", "new_days")}, "spikes": rest} == exp
    assert got["new_count"] == len(got["new_menus"]) and all(m["age_days"] <= 28 for m in got["new_menus"])
    assert all(not s["is_new"] for s in got["spikes"])


def test_day_mode_with_spikes_matches_the_code_before(monkeypatch):
    def plan(d):
        p = _weekend_plan(d)
        if d == dt.date(2026, 8, 20):
            p.update({1: 40, 3: 25, 6: 12})     # a spike, a new menu, a new menu under the 20-plate floor
        if d >= dt.date(2026, 8, 18):
            p[3] = p.get(3, 25)
        return p
    con = _db(plan)
    for kw in ({}, {"date": "2026-08-20"}, {"date": "2026-08-20", "branch": "สาขาทดสอบ"}, {"date": "2026-08-20", "threshold": 3.0}):
        got = _call(con, monkeypatch, **kw)
        _assert_day_matches_without_new(got, _day_reference(con, **kw), con)
    got = _call(con, monkeypatch, date="2026-08-20")
    assert _day_reference(con, date="2026-08-20")["count"] == got["count"] + 1      # the old list held the new menu
    assert _row(got, "ข้าวปั้น")["x"] == 4.0 and _row(got, "เมนูใหม่") is None and "period" not in got
    assert [(m["menu"], m["qty"]) for m in got["new_menus"]] == [("เมนูใหม่", 25), ("เมนูใหม่น้อย", 12)]


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
    assert s == {"branch": "สาขาทดสอบ", "menu": "ซาชิมิ", "today": 150, "per_day": 75.0, "avg": 60.0, "expected": 60,
                 "x": 2.5, "pct": 150, "excess": 90, "is_new": False}
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
    old = _day_reference(con, date="2026-08-20")
    assert next(s for s in old["spikes"] if s["menu"] == "เมนูเล็ก")["is_new"] is True    # the old rule
    r = _call(con, monkeypatch, date="2026-08-20")
    # day mode: 6 plates over the 21 trading days = 0.29 a day
    assert _row(r, "เมนูเล็ก") == {"branch": "สาขาทดสอบ", "menu": "เมนูเล็ก", "today": 30, "avg": 0.3, "x": 105.0,
                                   "pct": 10400, "excess": 30, "is_new": False, "thin_base": True}
    assert _row(r, "เมนูใหม่น้อย") == {"branch": "สาขาทดสอบ", "menu": "เมนูใหม่น้อย", "today": 25, "avg": 0.0, "x": None,
                                        "pct": None, "excess": 25, "is_new": False, "thin_base": True}
    assert _row(r, "ซาชิมิ") is None and r["new_menus"] == []
    assert _row(r, "เมนูใหม่")["x"] == 38.5                      # thin, 22 plates vs 0.57: listed (>= 1.5x)
    # thin with a baseline but under 1.5x: not listed (the same threshold as every spike)
    r3 = _call(con, monkeypatch, date="2026-08-20", threshold=50.0)
    assert _row(r3, "เมนูใหม่") is None and _row(r3, "เมนูเล็ก") is not None   # x 38.5 < 50, x 105 >= 50
    assert _row(r3, "เมนูใหม่น้อย")["x"] is None                 # no baseline: listed from 20 plates, x "—"
    r3 = _call(con, monkeypatch, date="2026-08-20", threshold=200.0)
    assert _row(r3, "เมนูเล็ก") is None and _row(r3, "เมนูใหม่น้อย") is not None
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
    assert s is not None and s["is_new"] is False and s["x"] == 15.0 and "thin_base" not in s and _nm(r, "เมนูเล็ก") is None


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
