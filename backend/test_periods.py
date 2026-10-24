# -*- coding: utf-8 -*-
"""PR-PP1 · the one period pattern (backend/periods.py) and its JS mirror (periodRange in js/01-core.js).

Week / month / year, unfinished (to-date) and finished, month-end clamping (31 -> 30 / 28), leap day, the year
boundary, the anchor never on the day still trading; then the same date table through node, field for field."""
import json
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).parent.resolve()
sys.path.insert(0, str(HERE))

import periods as P  # noqa: E402

CORE = (HERE.parent / "js" / "01-core.js").read_text(encoding="utf-8")
NODE = shutil.which("node")


def _w(kind, anchor, **kw):
    r = P.resolve(kind, anchor, **kw)
    return r["cur"], r["prev"], r["ly"], r["days"], r["complete"]


# ── week (Mon–Sun) ──────────────────────────────────────────────────────────────────────────────────────────────
def test_week_unfinished_is_to_date_same_weekdays():
    # Fri 2026-10-23: Mon 19..Fri 23 vs Mon 12..Fri 16 (not 28 days back), last year 364 days back (Mon..Fri)
    cur, prev, ly, n, done = _w("week", "2026-10-23")
    assert cur == ("2026-10-19", "2026-10-23") and n == 5 and done is False
    assert prev == ("2026-10-12", "2026-10-16")
    assert ly == ("2025-10-20", "2025-10-24")


def test_week_finished_is_full_vs_full():
    cur, prev, ly, n, done = _w("week", "2026-10-25")          # Sunday
    assert (cur, prev, n, done) == (("2026-10-19", "2026-10-25"), ("2026-10-12", "2026-10-18"), 7, True)
    # an older week picked by any of its days, the anchor later: whole
    cur, prev, _, n, done = _w("week", "2026-10-23", date_from="2026-10-07")
    assert (cur, prev, n, done) == (("2026-10-05", "2026-10-11"), ("2026-09-28", "2026-10-04"), 7, True)


def test_week_across_year_boundary():
    cur, prev, ly, n, _ = _w("week", "2027-01-01")              # Fri; that week starts Mon 28 Dec 2026
    assert cur == ("2026-12-28", "2027-01-01") and n == 5
    assert prev == ("2026-12-21", "2026-12-25") and ly == ("2025-12-29", "2026-01-02")


# ── month ───────────────────────────────────────────────────────────────────────────────────────────────────────
def test_month_unfinished_is_day_for_day():
    cur, prev, ly, n, done = _w("month", "2026-10-23")
    assert cur == ("2026-10-01", "2026-10-23") and n == 23 and done is False
    assert prev == ("2026-09-01", "2026-09-23") and ly == ("2025-10-01", "2025-10-23")


def test_month_finished_is_whole_vs_whole():
    cur, prev, ly, n, done = _w("month", "2026-10-31")
    assert (cur, prev, ly, n, done) == (("2026-10-01", "2026-10-31"), ("2026-09-01", "2026-09-30"),
                                        ("2025-10-01", "2025-10-31"), 31, True)
    cur, prev, _, _, done = _w("month", "2026-03-31")          # 31 days vs the whole of a 28-day February
    assert prev == ("2026-02-01", "2026-02-28") and done is True
    cur, prev, _, _, done = _w("month", "2026-10-23", date_from="2026-09-15")   # a closed month picked
    assert cur == ("2026-09-01", "2026-09-30") and prev == ("2026-08-01", "2026-08-31") and done


def test_month_end_clamping():
    # 1..30 March (open) -> 1..28 February; 1..30 May (open) -> 1..30 April (all of it); 31 -> 30 for a finished one above
    assert _w("month", "2026-03-30")[1] == ("2026-02-01", "2026-02-28")
    assert _w("month", "2026-03-29")[1] == ("2026-02-01", "2026-02-28")
    assert _w("month", "2026-05-30")[1] == ("2026-04-01", "2026-04-30")


def test_month_year_boundary():
    cur, prev, ly, n, _ = _w("month", "2027-01-10")
    assert cur == ("2027-01-01", "2027-01-10") and prev == ("2026-12-01", "2026-12-10") and ly == ("2026-01-01", "2026-01-10")


def test_leap_day():
    # Feb 2028 has 29 days: to 28 Feb is still open; last year's February has 28
    cur, prev, ly, n, done = _w("month", "2028-02-28")
    assert done is False and ly == ("2027-02-01", "2027-02-28") and prev == ("2028-01-01", "2028-01-28")
    cur, prev, ly, n, done = _w("month", "2028-02-29")
    assert done is True and n == 29 and ly == ("2027-02-01", "2027-02-28")
    # March 2028 to the 30th -> 1..29 February 2028
    assert _w("month", "2028-03-30")[1] == ("2028-02-01", "2028-02-29")
    # year to 29 Feb -> 1 Jan..28 Feb last year; a day on 29 Feb -> 364 days back
    assert _w("year", "2028-02-29")[1] == ("2027-01-01", "2027-02-28")
    assert _w("day", "2028-02-29")[2] == ("2027-03-02", "2027-03-02")


# ── year ────────────────────────────────────────────────────────────────────────────────────────────────────────
def test_year_unfinished_and_finished():
    cur, prev, ly, n, done = _w("year", "2026-10-23")
    assert cur == ("2026-01-01", "2026-10-23") and done is False and n == 296
    assert prev == ly == ("2025-01-01", "2025-10-23")
    cur, prev, ly, n, done = _w("year", "2025-12-31")
    assert done is True and n == 365 and prev == ("2024-01-01", "2024-12-31")


# ── day · range ─────────────────────────────────────────────────────────────────────────────────────────────────
def test_day_same_weekday():
    cur, prev, ly, n, done = _w("day", "2026-10-23")
    assert cur == ("2026-10-23", "2026-10-23") and prev == ("2026-10-16", "2026-10-16") and ly == ("2025-10-24", "2025-10-24")
    assert n == 1 and done is True


def test_range_clamped_to_anchor():
    cur, prev, ly, n, done = _w("range", "2026-10-23", date_from="2026-10-10", date_to="2026-10-30")
    # 21-day range, 14 days so far: the first 14 days of the 21 days just before; last year the same dates
    assert cur == ("2026-10-10", "2026-10-23") and n == 14 and done is False
    assert prev == ("2026-09-19", "2026-10-02") and ly == ("2025-10-10", "2025-10-23")
    cur, prev, _, n, done = _w("range", "2026-10-23", date_from="2026-10-01", date_to="2026-10-07")
    assert prev == ("2026-09-24", "2026-09-30") and n == 7 and done


# ── the anchor and errors ───────────────────────────────────────────────────────────────────────────────────────
def test_anchor_excludes_today():
    # the newest data is today (a noon sync): the window stops the day before
    r = P.resolve("month", "2026-10-24", today="2026-10-24")
    assert r["cur"] == ("2026-10-01", "2026-10-23") and r["prev"] == ("2026-09-01", "2026-09-23")
    assert P.resolve("week", "2026-10-22", today="2026-10-24")["cur"] == ("2026-10-19", "2026-10-22")


def test_anchor_from_db_skips_the_trading_day():
    con = sqlite3.connect(":memory:")
    con.executescript("""CREATE TABLE branches(branch_id INTEGER, name TEXT);
        CREATE TABLE sales_transaction(branch_id INTEGER, sold_at TEXT, source TEXT);
        INSERT INTO branches VALUES (1,'A');""")
    for d in range(1, 11):
        con.executemany("INSERT INTO sales_transaction VALUES (1, ?, 'Easy')", [(f"2020-01-{d:02d} 12:00",)] * 10)
    con.execute("INSERT INTO sales_transaction VALUES (1, date('now','+7 hours') || ' 12:00', 'Easy')")
    assert P.anchor(con) == "2020-01-10"


def test_errors():
    with pytest.raises(ValueError):
        P.resolve("quarter", "2026-10-23")
    with pytest.raises(ValueError):
        P.resolve("month", "2026-10-23", date_from="2026-11-01")     # starts after the anchor
    with pytest.raises(ValueError):
        P.resolve("range", "2026-10-23", date_from="2026-10-05", date_to="2026-10-01")
    assert P.resolve("mtd", "2026-10-23")["kind"] == "month"


def test_labels():
    r = P.resolve("month", "2026-10-23")
    assert (r["label_th"], r["prev_label_th"], r["ly_label_th"]) == ("1–23 ต.ค. 2569", "1–23 ก.ย. 2569", "1–23 ต.ค. 2568")
    assert P.label_th("2026-09-28", "2026-10-03") == "28 ก.ย.–3 ต.ค. 2569"
    assert P.label_th("2025-12-29", "2026-01-04") == "29 ธ.ค. 2568–4 ม.ค. 2569"
    assert P.label_th("2026-10-23", "2026-10-23") == "23 ต.ค. 2569"


def test_pct_and_pp():
    assert P.pct(110, 100) == 10.0 and P.pct(90, 100) == -10.0
    assert P.pct(5, 0) is None and P.pct(5, None) is None and P.pct(None, 5) is None and P.pct(5, -3) is None
    assert P.pp(42.5, 40.25) == 2.3 and P.pp(None, 40) is None and P.pp(40, None) is None


# ── the JS mirror: one date table through both ──────────────────────────────────────────────────────────────────
CASES = [(k, a, {}) for k in ("day", "week", "month", "year")
         for a in ("2026-10-23", "2026-10-25", "2026-10-31", "2026-03-30", "2026-03-31", "2027-01-01", "2027-01-10",
                   "2028-02-28", "2028-02-29", "2028-03-30", "2025-12-31", "2026-02-28")]
CASES += [("month", "2026-10-23", {"date_from": "2026-09-15"}), ("week", "2026-10-23", {"date_from": "2026-10-07"}),
          ("year", "2026-10-23", {"date_from": "2025-06-01"}), ("day", "2026-10-23", {"date_from": "2026-10-01"}),
          ("range", "2026-10-23", {"date_from": "2026-10-10", "date_to": "2026-10-30"}),
          ("range", "2026-10-23", {"date_from": "2026-10-01", "date_to": "2026-10-07"}),
          ("range", "2028-03-05", {"date_from": "2028-02-25", "date_to": "2028-03-05"}),
          ("month", "2026-10-24", {"today": "2026-10-24"}), ("week", "2026-10-25", {"today": "2026-10-25"}),
          ("mtd", "2026-10-23", {}),
          ("month", "2026-10-23", {"date_from": "2026-11-01"}), ("quarter", "2026-10-23", {}),
          ("range", "2026-10-23", {"date_from": "2026-10-05", "date_to": "2026-10-01"})]
_KEYS = ("kind", "cur", "prev", "ly", "days", "complete", "label_th", "prev_label_th", "ly_label_th")


def _block():
    a, b = CORE.index("// <period-core>"), CORE.index("// </period-core>")
    return CORE[a:b]


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_js_parity():
    src = _block() + "\nconst CASES = " + json.dumps(CASES, ensure_ascii=False) + ";\n" + \
        "process.stdout.write(JSON.stringify({r: CASES.map(c => periodRange(c[0], c[1], c[2])), " \
        "p: [periodPct(110, 100), periodPct(5, 0), periodPct(5, null), periodPct(5, -3), periodPp(42.5, 40.25), periodPp(null, 1)]}));"
    out = subprocess.run([NODE, "-e", src], capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert out.returncode == 0, out.stderr
    js = json.loads(out.stdout)
    for (kind, anchor, kw), got in zip(CASES, js["r"]):
        try:
            want = P.resolve(kind, anchor, **kw)
        except ValueError:
            assert got is None, (kind, anchor, kw, got)
            continue
        assert got is not None, (kind, anchor, kw)
        for k in _KEYS:
            w = list(want[k]) if isinstance(want[k], tuple) else want[k]
            assert got[k] == w, (kind, anchor, kw, k, got[k], w)
        assert (got["from"], got["to"]) == tuple(want["cur"])
    assert js["p"] == [P.pct(110, 100), P.pct(5, 0), P.pct(5, None), P.pct(5, -3), P.pp(42.5, 40.25), P.pp(None, 1)]


def test_top_bar_default_is_year_until_pp3():
    """Until PP2/PP3 move the pages, the ONE period bar opens on year to date (1 Jan -> the anchor), so a page that still reads
    selMonthFrom / selMonthTo keeps Jan -> latest; a device's own choice (oshinei_gperiod) still wins. PP3 switches it to 'month'."""
    import re
    assert re.search(r"^const PERIOD_DEFAULT_KIND = 'year';", CORE, re.M)
    boot = CORE[CORE.index("let _gp = (() => {"):CORE.index("let _gpChartWin")]
    assert "localStorage.getItem(_GP_LS)" in boot and "return { kind: PERIOD_DEFAULT_KIND," in boot
    html = (HERE.parent / "index.html").read_text(encoding="utf-8")
    assert 'class="gran-btn gran-active" data-k="year" aria-pressed="true"' in html


def test_sp_call_shape_kept():
    """PR-SP calls periodRange('range', {from, to}) and periodRange('day', iso) - lifting it into the core keeps them."""
    if NODE is None:
        pytest.skip("node is not installed")
    src = _block() + "\nprocess.stdout.write(JSON.stringify([periodRange('range', {from: '2026-10-20', to: '2026-10-23'}), " \
        "periodRange('day', '2026-10-23'), periodRange('range', {from: '2026-10-24', to: '2026-10-23'}), periodRange('day', 'x')]));"
    out = subprocess.run([NODE, "-e", src], capture_output=True, text=True, encoding="utf-8", timeout=60)
    r = json.loads(out.stdout)
    assert (r[0]["from"], r[0]["to"], r[0]["days"], r[0]["label"]) == ("2026-10-20", "2026-10-23", 4, "20–23 ต.ค. 2569")
    assert (r[1]["from"], r[1]["days"]) == ("2026-10-23", 1)
    assert r[2] is None and r[3] is None
