"""sync_mssql.main() end to end — the nightly sync, run for real against a fake
MSSQL and a scratch copy of the dashboard database (Phase 8a, 2026-09).

Every other sync test exercises one helper. This one runs the pipeline the
scheduler runs: parse args → incremental cutoff → copy the live DB to
.incoming → map branches/items by natural key → pull sales_sku_monthly and
sales_transaction → stage the pos_* report tables → roll up
sales_month_summary → ANALYZE/VACUUM → reconcile against the live DB →
publish .incoming → record last_full_sync → (optionally) swap it in.

The fake answers every query main() and the stagers issue with canned rows
and — unlike test_sync_staging's FakeMC — HONOURS the per-month {RANGE}
window (_chunk passes (lo, hi) as params), so a row dated 2026-06 comes back
from the 2026-06 window only. That is what makes the full run's output
independent of today's date even though _month_windows() always runs up to
the current month, and what makes the incremental run meaningful: rows
outside the window are simply not offered.

  regenerate the golden:  GOLDEN_UPDATE=1 python -m pytest backend/test_sync_main.py
"""
import datetime as dt
import json
import os
import re
import sqlite3
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).parent.resolve()
sys.path.insert(0, str(HERE))

sync = pytest.importorskip("sync_mssql")
from test_sync_staging import PATTERNS as STAGE_PATTERNS, BID_POS, BID_UNKNOWN  # noqa: E402
from test_sync_golden import _snapshot  # noqa: E402

GOLDEN = HERE / "golden" / "sync_main.json"
UPDATE = os.environ.get("GOLDEN_UPDATE") == "1"

BID_A, BID_B, BID_X = BID_POS, 11, BID_UNKNOWN     # MSSQL BranchIds: BranchA, BranchB, unmapped
TODAY = dt.date.today()
THIS_YM = TODAY.isoformat()[:7]


def _D(s):
    return dt.datetime.fromisoformat(s)


# ── the fake HQ database ─────────────────────────────────────────────────────
def make_source(*, may_scale=1.0, new_sale=False):
    """Canned answers for every query main() issues, most specific first, in
    front of the staging patterns test_sync_staging already keeps. Rows that a
    chunked query returns carry a date key (ym / d / dt / _d) so the fake can
    window them; `_d` is an extra key the consumer never reads."""
    branches = [{"BranchId": BID_A, "BranchName": "BranchA", "BranchCode": "0001"},
                {"BranchId": BID_B, "BranchName": " BranchB ", "BranchCode": None},   # stripped by short_name
                {"BranchId": BID_X, "BranchName": "OSHINEI สาขา ไม่รู้จัก", "BranchCode": "0099"}]  # not in branches → skipped
    categories = [{"CategoryId": 1, "CategoryName": "Sashimi"},
                  {"CategoryId": 2, "CategoryName": "SOFT DRINK"},
                  {"CategoryId": 3, "CategoryName": "BUFFET PREMIUM"}]
    items = [{"ItemId": 100, "ItemCode": "A0001", "CategoryId": 1, "ItemNm": "dish"},
             {"ItemId": 200, "ItemCode": "D0001", "CategoryId": 2, "ItemNm": "cola"},
             {"ItemId": 300, "ItemCode": " NEW001 ", "CategoryId": 1, "ItemNm": "เมนูใหม่"},   # not in skus → stub
             {"ItemId": 400, "ItemCode": "", "CategoryId": 1, "ItemNm": "no code"},          # unusable
             {"ItemId": 500, "ItemCode": "BUFFET590", "CategoryId": 3, "ItemNm": "BUFFET590"},
             {"ItemId": 600, "ItemCode": "STUB001", "CategoryId": 1, "ItemNm": "ชื่อจริง"}]    # names an old stub
    skum = [
        {"bid": BID_A, "iid": 100, "ym": "2026-04", "qty": 40.0, "sub_exvat": 8000.0, "tax": 560.0},
        {"bid": BID_A, "iid": 100, "ym": "2026-05", "qty": 50.0, "sub_exvat": 10000.0 * may_scale, "tax": 700.0 * may_scale},
        {"bid": BID_A, "iid": 200, "ym": "2026-05", "qty": 20.0, "sub_exvat": 1000.0, "tax": 70.0},
        {"bid": BID_B, "iid": 100, "ym": "2026-05", "qty": 30.0, "sub_exvat": 6000.0, "tax": 420.0},
        {"bid": BID_A, "iid": 100, "ym": "2026-06", "qty": 10.004, "sub_exvat": 2000.004, "tax": 140.0003},
        {"bid": BID_A, "iid": 300, "ym": "2026-06", "qty": 3.0, "sub_exvat": 450.0, "tax": 31.5},
        {"bid": BID_A, "iid": 500, "ym": "2026-06", "qty": 12.0, "sub_exvat": 7080.0, "tax": 495.6},
        {"bid": BID_X, "iid": 100, "ym": "2026-06", "qty": 5.0, "sub_exvat": 999.0, "tax": 69.93},   # unmapped branch
        {"bid": BID_A, "iid": 400, "ym": "2026-06", "qty": 1.0, "sub_exvat": 77.0, "tax": 5.39},     # item without a code
    ]
    split = [
        {"sid": 55, "bid": BID_A, "_d": "2026-06-01", "food": 500.0, "drink": 100.0, "other": 0.0},
        {"sid": 56, "bid": BID_A, "_d": "2026-05-15", "food": 300.0, "drink": 0.0, "other": 200.0},
        {"sid": 57, "bid": BID_B, "_d": "2026-05-20", "food": 200.0, "drink": 0.0, "other": 0.0},
        {"sid": 58, "bid": BID_X, "_d": "2026-06-02", "food": 50.0, "drink": 0.0, "other": 0.0},
        {"sid": 59, "bid": BID_A, "_d": "2026-04-10", "food": 100.0, "drink": 0.0, "other": 0.0},
    ]
    coupons = [{"sid": 56, "bid": BID_A, "cs": 5, "amt": 50.0, "_d": "2026-05-15"},
               {"sid": 56, "bid": BID_A, "cs": 6, "amt": 20.0, "_d": "2026-05-15"}]   # the larger discount wins
    dwell = [{"sid": 55, "bid": BID_A, "dwell": 45, "_d": "2026-06-01"},
             {"sid": 56, "bid": BID_A, "dwell": 400, "_d": "2026-05-15"},            # > 360 min → dropped
             {"sid": 57, "bid": BID_B, "dwell": None, "_d": "2026-05-20"}]
    tx = [
        {"bid": BID_A, "sid": 55, "num": 7, "dt": _D("2026-06-01 12:00:00"), "cust": 2, "disc": 0.0, "rid": None,
         "svc": 0.0, "tip": 0.0, "total": 642.0, "cashier": "cashier1", "ptimes": 1},
        {"bid": BID_A, "sid": 56, "num": 3, "dt": _D("2026-05-15 18:30:00"), "cust": 3, "disc": 50.0, "rid": 3,
         "svc": 25.0, "tip": 0.0, "total": 535.0, "cashier": "", "ptimes": 0},
        {"bid": BID_B, "sid": 57, "num": 1, "dt": _D("2026-05-20 19:00:00"), "cust": 1, "disc": None, "rid": None,
         "svc": None, "tip": None, "total": 214.0, "cashier": "boss", "ptimes": None},
        {"bid": BID_X, "sid": 58, "num": 9, "dt": _D("2026-06-02 11:00:00"), "cust": 1, "disc": 0.0, "rid": None,
         "svc": 0.0, "tip": 0.0, "total": 53.5, "cashier": "x", "ptimes": 0},
        {"bid": BID_A, "sid": 59, "num": 2, "dt": _D("2026-04-10 13:00:00"), "cust": 1, "disc": 0.0, "rid": None,
         "svc": 0.0, "tip": 0.0, "total": 107.0, "cashier": "cashier1", "ptimes": 2},
    ]
    if new_sale:   # one bill in the CURRENT month — what an incremental window re-pulls
        skum.append({"bid": BID_A, "iid": 100, "ym": THIS_YM, "qty": 1.0, "sub_exvat": 100.0, "tax": 7.0})
        split.append({"sid": 90, "bid": BID_A, "_d": TODAY.isoformat(), "food": 100.0, "drink": 0.0, "other": 0.0})
        tx.append({"bid": BID_A, "sid": 90, "num": 5, "dt": _D(TODAY.isoformat() + " 12:00:00"), "cust": 1,
                   "disc": 0.0, "rid": None, "svc": 0.0, "tip": 0.0, "total": 107.0, "cashier": "cashier1",
                   "ptimes": 0})
    return [
        (r"SELECT BranchId, BranchName, BranchCode FROM Branch", branches),
        (r"FROM ItemCategory", categories),
        (r"TABLE_NAME='Items'", [{"COLUMN_NAME": "ItemName"}, {"COLUMN_NAME": "ItemCode"}]),
        (r"COLUMN_NAME='PrintTimes'", [{"ok": 1}]),
        (r"LIKE '%Approv%'", []),
        (r"SELECT ItemId, ItemCode, CategoryId", items),
        (r"si\.BranchId AS bid, si\.ItemId AS iid", skum),
        (r"\) AS drink,", split),
        (r"CouponSeriesId AS cs,", coupons),
        (r"AS dwell\b", dwell),
        (r"s\.SaleNum AS num, s\.SaleDate AS dt", tx),
    ] + STAGE_PATTERNS


def _date_of(row):
    for k in ("ym", "d", "dt", "cd", "g", "_d"):
        v = row.get(k)
        if v is not None:
            s = v.strftime("%Y-%m-%d") if hasattr(v, "strftime") else str(v)
            return s + "-01" if len(s) == 7 else s[:10]
    return None


class FakeCursor:
    def __init__(self, patterns):
        self._patterns, self._rows = patterns, []

    def execute(self, sql, params=None):
        # what the real server would reject: a brace left in the SQL (2026-09-04:
        # five templates that lost their f-prefix kept the doubled {{RANGE}})
        assert "{" not in sql and "}" not in sql, f"brace in SQL sent to MSSQL: {sql[:160]!r}"
        rows = []
        for pat, canned in self._patterns:
            if re.search(pat, sql):
                rows = list(canned)
                break
        if params is not None and len(params) == 2 and ">= %s AND" in sql:   # a _chunk() window
            lo, hi = str(params[0])[:10], str(params[1])[:10]
            rows = [r for r in rows if _date_of(r) is None or lo <= _date_of(r) < hi]
        self._rows = rows

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None


class FakeConn:
    def __init__(self, patterns):
        self._patterns = patterns

    def cursor(self, as_dict=True):
        return FakeCursor(self._patterns)

    def close(self):
        pass


# ── the scratch "live" database and a run of the sync against it ─────────────
def seed_current_db(db: Path):
    """The dashboard DB as the sync finds it: schema, the master tables it must
    PRESERVE (branches, skus, costs) and — for the first run — no sales."""
    con = sqlite3.connect(str(db))
    con.executescript((HERE / "schema.sql").read_text(encoding="utf-8"))
    con.executescript("""
      CREATE TABLE IF NOT EXISTS bom_sku_alias(bom_sku_code TEXT PRIMARY KEY,
        resolved_sku_id INTEGER, match_method TEXT, confidence REAL, notes TEXT);
      ALTER TABLE skus ADD COLUMN active INTEGER DEFAULT 1;
      ALTER TABLE skus ADD COLUMN business_type TEXT;
    """)
    con.executemany("INSERT INTO branches(branch_id,name) VALUES(?,?)", [(1, "BranchA"), (2, "BranchB")])
    con.executemany("INSERT INTO categories(category_id,name) VALUES(?,?)",
                    [(1, "Food"), (2, "Drink"), (3, "Package")])
    con.executemany("INSERT INTO skus(sku_id,sku_code,description,category_id) VALUES(?,?,?,?)",
                    [(1, "A0001", "dish", 1), (2, "D0001", "cola", 2),
                     (3, "BUFFET590", "Buffet 590", 3), (4, "STUB001", "STUB001", 1)])
    con.executemany("INSERT INTO sku_cost(sku_id,unit_cost) VALUES(?,?)", [(1, 10.0), (3, 590.0)])
    con.commit()
    con.close()


def run_sync(tmp: Path, *argv, source=None):
    """main() with the module's paths pointed at `tmp`, the MSSQL connector
    replaced by the fake, and the CLI arguments given. Returns the exit code
    (0 unless main() called sys.exit)."""
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(sync, "DB", tmp / "oshinei.db")
        mp.setattr(sync, "INCOMING", tmp / "oshinei.db.incoming")
        mp.setattr(sync, "STAGE_FALLBACK", tmp / "oshinei.db.incoming.new")
        mp.setattr(sync, "connect_mssql", lambda: FakeConn(source or make_source()))
        mp.setattr(sys, "argv", ["sync_mssql.py", "--since", "2026-04-01", "--no-menu-fact", *argv])
        try:
            sync.main()
        except SystemExit as e:
            return e.code or 0
    return 0


def _open(path):
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def _rows(con, sql):
    return [tuple(r) for r in con.execute(sql)]


STAGED_TABLES = [   # what stage_reports writes from the HQ tables, by month or day
    "pos_void", "pos_void_day", "pos_staff_month", "pos_payment_month", "pos_payment_day",
    "pos_coupon_payment_month", "pos_coupon_payment_day", "sale_payment", "pos_table_turn_day",
    "pos_channel_month", "pos_channel_day", "pos_coupon_month", "pos_coupon_day",
    "pos_kitchen_month", "sale_bill_item",
]


@pytest.fixture
def scratch(tmp_path):
    seed_current_db(tmp_path / "oshinei.db")
    return tmp_path


# ── a full sync onto an empty history ────────────────────────────────────────
def test_full_sync_builds_incoming_and_leaves_the_live_db_alone(scratch, capsys):
    before = (scratch / "oshinei.db").read_bytes()
    assert run_sync(scratch) == 0
    out = capsys.readouterr().out
    assert "[6/6] done → oshinei.db.incoming ready & reconciled" in out
    assert (scratch / "oshinei.db.incoming").exists()
    assert (scratch / "oshinei.db").read_bytes() == before      # not swapped without --apply-local
    assert not (scratch / "oshinei.db.incoming.new").exists()   # normal path: no fallback build

    con = _open(scratch / "oshinei.db.incoming")
    # branch_pos: the POS's own id + code for every MAPPED branch (the live app cannot ask the POS);
    # a branch without a code keeps NULL, an unmapped POS branch is not written at all
    assert _rows(con, "SELECT branch_id, pos_branch_id, pos_branch_code, pos_branch_name FROM branch_pos "
                      "ORDER BY branch_id") == [(1, BID_A, "0001", "BranchA"), (2, BID_B, None, " BranchB ")]
    # sales_sku_monthly: mapped branches only, tax rounded, stub sku created for NEW001
    assert _rows(con, "SELECT branch_id,period_month,sku_id,qty,subtotal_ex_vat,tax_total,total_inc_vat "
                      "FROM sales_sku_monthly ORDER BY period_month,branch_id,sku_id") == [
        (1, "2026-04", 1, 40.0, 8000.0, 560.0, 8560.0),
        (1, "2026-05", 1, 50.0, 10000.0, 700.0, 10700.0),
        (1, "2026-05", 2, 20.0, 1000.0, 70.0, 1070.0),
        (2, "2026-05", 1, 30.0, 6000.0, 420.0, 6420.0),
        (1, "2026-06", 1, 10.0, 2000.0, 140.0, 2140.0),
        (1, "2026-06", 3, 12.0, 7080.0, 495.6, 7575.6),
        (1, "2026-06", 5, 3.0, 450.0, 31.5, 481.5),
    ]
    assert _rows(con, "SELECT sku_id,sku_code,description FROM skus WHERE sku_id>=4 ORDER BY sku_id") == [
        (4, "STUB001", "ชื่อจริง"),      # an old stub got its real name from Items
        (5, "NEW001", "เมนูใหม่"),       # lazily created, code stripped, named
    ]
    assert "skipped revenue (unmapped branch)=฿1,076" in out       # 999 (branch) + 77 (no code)
    assert "new stub skus=1" in out and "backfilled names on 1 stub skus" in out
    # sales_transaction: receipt = date-num-id; coupon = largest; dwell capped; NULLs → 0
    assert _rows(con, "SELECT branch_id,receipt_no,customer_count,food_subtotal,drink_subtotal,other_subtotal,"
                      "tip,discount,service_charge,total_inc_vat,cashier,discount_reason_id,coupon_series_id,"
                      "print_times,dwell_min FROM sales_transaction ORDER BY sold_at") == [
        (1, "2026-04-10-2-59", 1, 100.0, 0.0, 0.0, 0.0, 0.0, 0.0, 107.0, "cashier1", None, None, 2, None),
        (1, "2026-05-15-3-56", 3, 300.0, 0.0, 200.0, 0.0, 50.0, 25.0, 535.0, "", 3, 5, 0, None),
        (2, "2026-05-20-1-57", 1, 200.0, 0.0, 0.0, 0.0, 0.0, 0.0, 214.0, "boss", None, None, 0, None),
        (1, "2026-06-01-7-55", 2, 500.0, 100.0, 0.0, 0.0, 0.0, 0.0, 642.0, "cashier1", None, None, 1, 45),
    ]
    # package codes never carry a cost; the dish keeps its cost-management price
    assert dict(_rows(con, "SELECT sku_id, recipe_cost FROM sku_effective_cost")) == {1: 10.0, 2: 0.0, 3: 0.0, 4: 0.0, 5: 0.0}
    # month summary rolled up from the transactions just built
    assert _rows(con, "SELECT branch_id,period_month,net_sales,customer_count,transaction_count "
                      "FROM sales_month_summary ORDER BY period_month,branch_id") == [
        (1, "2026-04", 100.0, 1, 1),
        (1, "2026-05", 475.0, 3, 1),          # 535/1.07 − 25 service charge
        (2, "2026-05", 200.0, 1, 1),
        (1, "2026-06", 600.0, 2, 1),
    ]
    assert _rows(con, "SELECT * FROM sync_state") == [("last_full_sync", TODAY.isoformat())]
    assert "SETTLED" not in out           # nothing to compare against on an empty history
    con.close()


def test_full_sync_output_does_not_depend_on_the_number_of_windows(tmp_path):
    """The golden must hold in any month: a canned row is offered by the fake in
    its own month's window only, so three more windows change nothing."""
    outs = []
    for i, since in enumerate(("2026-04-01", "2026-01-01")):
        d = tmp_path / str(i)
        d.mkdir()
        seed_current_db(d / "oshinei.db")
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(sync, "DB", d / "oshinei.db")
            mp.setattr(sync, "INCOMING", d / "oshinei.db.incoming")
            mp.setattr(sync, "STAGE_FALLBACK", d / "oshinei.db.incoming.new")
            mp.setattr(sync, "connect_mssql", lambda: FakeConn(make_source()))
            mp.setattr(sys, "argv", ["sync_mssql.py", "--since", since, "--no-menu-fact"])
            sync.main()
        con = _open(d / "oshinei.db.incoming")
        outs.append(_snapshot(con))
        con.close()
    assert outs[0] == outs[1]


def test_full_sync_output_is_pinned(scratch):
    assert run_sync(scratch) == 0
    con = _open(scratch / "oshinei.db.incoming")
    got = _snapshot(con)
    con.close()
    got.pop("sync_state", None)                       # carries today's date
    if UPDATE or not GOLDEN.exists():
        GOLDEN.parent.mkdir(exist_ok=True)
        GOLDEN.write_text(json.dumps(got, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        if not UPDATE:
            pytest.skip("sync main golden created")
        return
    want = json.loads(GOLDEN.read_text(encoding="utf-8"))
    diffs = [f"{t}: golden={want.get(t)} now={got.get(t)}"
             for t in sorted(set(got) | set(want)) if got.get(t) != want.get(t)]
    assert not diffs, "\n".join(diffs[:8])


# ── a second run reconciles against the first; --apply-local swaps it in ─────
def test_apply_local_then_rerun_reconciles_the_settled_month_strictly(scratch, capsys):
    assert run_sync(scratch, "--apply-local") == 0
    out = capsys.readouterr().out
    assert "[6/6] reconciled → applying to local oshinei.db" in out
    assert not (scratch / "oshinei.db.incoming").exists()
    con = _open(scratch / "oshinei.db")
    assert con.execute("SELECT COUNT(*) FROM sales_transaction").fetchone()[0] == 4
    con.close()

    assert run_sync(scratch, "--apply-local") == 0
    out = capsys.readouterr().out
    settled = [ln for ln in out.splitlines() if "SETTLED rev 2026-05" in ln]
    assert settled and "+0.00%" in settled[0] and "✓" in settled[0]
    live = [ln for ln in out.splitlines() if "LIVE rev 2026-06" in ln]
    assert live and "✓" in live[0]


def test_reconcile_failure_exits_3_and_keeps_the_live_db(scratch, capsys):
    assert run_sync(scratch, "--apply-local") == 0
    capsys.readouterr()
    before = (scratch / "oshinei.db").read_bytes()
    # HQ now reports half of May for BranchA — a settled month lost revenue: history loss
    assert run_sync(scratch, "--apply-local", source=make_source(may_scale=0.5)) == 3
    out = capsys.readouterr().out
    assert "RECONCILE FAILED" in out
    settled = [ln for ln in out.splitlines() if "SETTLED rev 2026-05" in ln]
    assert settled and "⚠" in settled[0]
    assert (scratch / "oshinei.db").read_bytes() == before      # nothing applied


# ── incremental runs (--window-days) ─────────────────────────────────────────
def test_incremental_run_keeps_history_and_pulls_only_the_window(scratch, capsys):
    assert run_sync(scratch, "--apply-local") == 0
    capsys.readouterr()
    con = _open(scratch / "oshinei.db")
    full = _snapshot(con)
    # an incremental run must not reset the full-rebuild clock: mark it a day old
    con.close()
    w = sqlite3.connect(str(scratch / "oshinei.db"))
    w.execute("UPDATE sync_state SET v=? WHERE k='last_full_sync'", ((TODAY - dt.timedelta(days=1)).isoformat(),))
    w.commit(); w.close()

    assert run_sync(scratch, "--window-days", "1", "--full-every-days", "0", source=make_source(new_sale=True)) == 0
    out = capsys.readouterr().out
    cutoff = (TODAY - dt.timedelta(days=1)).replace(day=1).isoformat()
    assert f"[incremental] re-pulling from {cutoff}" in out
    assert "forcing a FULL run" not in out

    con = _open(scratch / "oshinei.db.incoming")
    inc = _snapshot(con)
    # the window's month arrived …
    assert con.execute("SELECT COUNT(*) FROM sales_sku_monthly WHERE period_month=?",
                       (THIS_YM,)).fetchone()[0] == 1
    assert con.execute("SELECT COUNT(*) FROM sales_transaction WHERE substr(sold_at,1,7)=?",
                       (THIS_YM,)).fetchone()[0] == 1
    # … the months before it were kept from the copied DB, row for row …
    for t in ("sales_sku_monthly", "sales_transaction", "sales_month_summary"):
        old_rows = _rows(con, f"SELECT * FROM {t} WHERE {'period_month' if t != 'sales_transaction' else 'substr(sold_at,1,7)'} < '{THIS_YM}'")
        assert len(old_rows) == full[t]["rows"], t
    # … and so were the staged report tables: the HQ offered nothing for the
    # window, so an incremental run must leave every older month in place.
    lost = [t for t in STAGED_TABLES if inc[t] != full[t]]
    assert not lost, f"incremental run dropped staged history in: {lost}"
    assert _rows(con, "SELECT * FROM sync_state") == [("last_full_sync", (TODAY - dt.timedelta(days=1)).isoformat())]
    con.close()


def test_stale_full_rebuild_forces_a_full_run(scratch, capsys):
    assert run_sync(scratch, "--apply-local") == 0
    capsys.readouterr()
    w = sqlite3.connect(str(scratch / "oshinei.db"))
    w.execute("UPDATE sync_state SET v=? WHERE k='last_full_sync'", ((TODAY - dt.timedelta(days=30)).isoformat(),))
    w.commit(); w.close()
    assert run_sync(scratch, "--window-days", "1") == 0        # --full-every-days defaults to 7
    out = capsys.readouterr().out
    assert "forcing a FULL run" in out
    assert "[incremental] re-pulling" not in out
    con = _open(scratch / "oshinei.db.incoming")
    assert _rows(con, "SELECT * FROM sync_state") == [("last_full_sync", TODAY.isoformat())]
    con.close()


def test_incremental_window_reaching_past_since_is_a_full_run(scratch, capsys):
    assert run_sync(scratch, "--window-days", "100000") == 0     # cutoff earlier than --since → everything
    out = capsys.readouterr().out
    assert "[incremental]" not in out
