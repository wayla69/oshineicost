# -*- coding: utf-8 -*-
"""The sync pulls the HQ database a month at a time and never holds the whole history (PR-CS, 2026-10).

Until then sync_mssql built a Python dict per row of ~800 k bills, ~1.8 M discount-bill lines and ~830 k payment lines
before inserting any of them: a peak over 1.3 GB, fine on the office PC and an out-of-memory kill on the 2 GB Fly
machine that also serves the app (cloud_sync.py). These tests pin the pieces that keep that from coming back:
the month iterator, the private staging name, the dwell lookup that crosses a month, and a measured ceiling
for a synthetic history. The sync's OUTPUT is pinned by test_sync_main.py / test_sync_golden.py.
"""
import datetime as dt
import sqlite3
import sys
import tracemalloc
from pathlib import Path

import pytest

HERE = Path(__file__).parent.resolve()
sys.path.insert(0, str(HERE))

sync = pytest.importorskip("sync_mssql")
import test_sync_main as tsm  # noqa: E402
from test_sync_staging import PATTERNS as STAGE_PATTERNS, BID_POS  # noqa: E402


class _MC:
    def __init__(self):
        self.sql = []

    def execute(self, sql, params=None):
        self.sql.append((sql, params))
        self._rows = [{"lo": params[0]}]

    def fetchall(self):
        return self._rows


def test_chunk_iter_yields_one_window_at_a_time():
    mc = _MC()
    it = sync._chunk_iter(mc, "SELECT x FROM t WHERE {RANGE}", [("2026-04-01", "2026-05-01"), ("2026-05-01", "2026-06-01")], col="s.SaleDate")
    assert mc.sql == []                                                        # nothing is pulled until a month is asked for
    lo, hi, rows = next(it)
    assert (lo, hi, rows) == ("2026-04-01", "2026-05-01", [{"lo": "2026-04-01"}]) and len(mc.sql) == 1
    assert "s.SaleDate >= %s AND s.SaleDate < %s" in mc.sql[0][0] and mc.sql[0][1] == ("2026-04-01", "2026-05-01")
    assert [(l, h) for l, h, _ in it] == [("2026-05-01", "2026-06-01")]
    assert len(mc.sql) == 2


def test_chunk_iter_refuses_a_stray_brace_when_called_not_when_first_used():
    with pytest.raises(ValueError):
        sync._chunk_iter(_MC(), "SELECT 1 FROM T WHERE {RANGE} AND x IN {ids}", [("a", "b")], col="d")


def test_chunk_is_the_concatenation_of_chunk_iter():
    mc = _MC()
    wins = [("2026-04-01", "2026-05-01"), ("2026-05-01", "2026-06-01"), ("2026-06-01", "2026-07-01")]
    assert sync._chunk(mc, "SELECT 1 WHERE {RANGE}", wins) == [{"lo": lo} for lo, _ in wins]


# ── the private staging name (the Fly poller swaps whatever sits in .incoming) ───────────────────
def test_a_private_build_is_published_only_when_finished(tmp_path, monkeypatch, capsys):
    tsm.seed_current_db(tmp_path / "oshinei.db")
    monkeypatch.setenv("OSHINEI_SYNC_STAGE_PRIVATE", "1")
    seen = {}
    real = sync.reconcile

    def spy(sq):
        seen["incoming_during_build"] = (tmp_path / "oshinei.db.incoming").exists()
        seen["private_during_build"] = (tmp_path / "oshinei.db.incoming.new").exists()
        return real(sq)
    monkeypatch.setattr(sync, "reconcile", spy)
    assert tsm.run_sync(tmp_path) == 0
    assert seen == {"incoming_during_build": False, "private_during_build": True}   # the poller has nothing to pick up mid-build
    assert (tmp_path / "oshinei.db.incoming").exists() and not (tmp_path / "oshinei.db.incoming.new").exists()
    con = sqlite3.connect(str(tmp_path / "oshinei.db.incoming"))
    try:
        assert con.execute("SELECT COUNT(*) FROM sales_transaction").fetchone()[0] == 4
        # the full-run clock is written into the build BEFORE it is published (the poller may swap it at once)
        assert con.execute("SELECT v FROM sync_state WHERE k='last_full_sync'").fetchone()[0] == dt.date.today().isoformat()
    finally:
        con.close()


def test_a_private_build_that_fails_the_gate_publishes_nothing(tmp_path, monkeypatch):
    tsm.seed_current_db(tmp_path / "oshinei.db")
    monkeypatch.setenv("OSHINEI_SYNC_STAGE_PRIVATE", "1")
    assert tsm.run_sync(tmp_path, "--apply-local") == 0
    (tmp_path / "oshinei.db.incoming").unlink(missing_ok=True)
    before = (tmp_path / "oshinei.db").read_bytes()
    assert tsm.run_sync(tmp_path, source=tsm.make_source(may_scale=0.5)) == 3        # the reconcile gate
    assert not (tmp_path / "oshinei.db.incoming").exists()                              # nothing for the poller to swap
    assert (tmp_path / "oshinei.db").read_bytes() == before


# ── the base is the running site's own database ──────────────────────────────────────────────────
def test_snapshot_copy_includes_what_is_still_in_the_wal(tmp_path):
    live = tmp_path / "live.db"
    hold = sqlite3.connect(str(live))                      # the web app's connection: it keeps the WAL from being checkpointed away
    hold.execute("PRAGMA journal_mode=WAL")
    hold.execute("PRAGMA wal_autocheckpoint=0")
    hold.execute("CREATE TABLE t(x)")
    hold.commit()
    hold.executemany("INSERT INTO t VALUES(?)", [(i,) for i in range(500)])
    hold.commit()
    assert (tmp_path / "live.db-wal").stat().st_size > 0   # the rows are in the WAL, not in the main file
    try:
        plain = tmp_path / "plain.db"
        import shutil
        shutil.copy2(live, plain)                           # what the PC's path does: the main file alone
        snap = tmp_path / "snap.db"
        sync.snapshot_copy(live, snap)
        assert sqlite3.connect(str(snap)).execute("SELECT COUNT(*) FROM t").fetchone()[0] == 500
        try:
            assert sqlite3.connect(str(plain)).execute("SELECT COUNT(*) FROM t").fetchone()[0] < 500
        except sqlite3.DatabaseError:
            pass                                            # a main file without its WAL may not even have the table
    finally:
        hold.close()


def test_a_run_on_the_live_db_builds_the_same_output(tmp_path, monkeypatch):
    """OSHINEI_SYNC_ON_LIVE=1 changes how the base is copied and skips the pulls from the site; the rows built are the same."""
    tsm.seed_current_db(tmp_path / "oshinei.db")
    monkeypatch.setenv("OSHINEI_SYNC_ON_LIVE", "1")
    monkeypatch.setenv("OSHINEI_SYNC_STAGE_PRIVATE", "1")
    assert tsm.run_sync(tmp_path) == 0
    con = sqlite3.connect(str(tmp_path / "oshinei.db.incoming"))
    got = con.execute("SELECT branch_id, period_month, sku_id, qty, subtotal_ex_vat FROM sales_sku_monthly "
                      "ORDER BY period_month, branch_id, sku_id").fetchall()
    con.close()
    assert got[0] == (1, "2026-04", 1, 40.0, 8000.0) and len(got) == 7
    assert not (tmp_path / "oshinei.db.incoming.new").exists()


# ── a bill's table can open in the month before its SaleDate ─────────────────────────────────────
def _source_with_dwell(rows):
    src = tsm.make_source()
    return [(pat, rows) if pat == r"AS dwell\b" else (pat, canned) for pat, canned in src]


def test_dwell_is_found_in_the_neighbouring_month_and_the_later_window_wins(tmp_path):
    tsm.seed_current_db(tmp_path / "oshinei.db")
    src = _source_with_dwell([
        # sale 55 is billed 2026-06-01, its table opened on 05-31: the row sits in the MAY window
        {"sid": 55, "bid": BID_POS, "dwell": 45, "_d": "2026-05-31"},
        # sale 56 (05-15) has a row in May and a later one in June: the later window wins, as the whole-history dict did
        {"sid": 56, "bid": BID_POS, "dwell": 30, "_d": "2026-05-15"},
        {"sid": 56, "bid": BID_POS, "dwell": 50, "_d": "2026-06-02"},
        {"sid": 57, "bid": 11, "dwell": 400, "_d": "2026-05-20"},                       # over 360 min: dropped
    ])
    assert tsm.run_sync(tmp_path, source=src) == 0
    con = sqlite3.connect(str(tmp_path / "oshinei.db.incoming"))
    got = dict(con.execute("SELECT receipt_no, dwell_min FROM sales_transaction").fetchall())
    con.close()
    assert got["2026-06-01-7-55"] == 45 and got["2026-05-15-3-56"] == 50 and got["2026-05-20-1-57"] is None


# ── a measured ceiling ───────────────────────────────────────────────────────────────────────────
WINDOWS = 22          # 2025-01 .. 2026-10, the months a full run pulls
PER_MONTH_BILLS = 1500
PER_MONTH_DISC_LINES = 3000
PER_MONTH_PAY = 1400


def _gen(sql, lo):
    day, ym = lo[:10], lo[:7]
    if "AS num, s.SaleDate AS dt" in sql:
        return [{"bid": BID_POS, "sid": 100000 + i, "num": i % 900, "dt": dt.datetime.fromisoformat(f"{day} 12:{i % 60:02d}:00"),
                 "cust": 3, "disc": 0.0, "rid": None, "svc": 12.5, "tip": 0.0, "total": 899.5,
                 "cashier": f"cashier{i % 40}", "ptimes": 1} for i in range(PER_MONTH_BILLS)]
    if ") AS drink," in sql:
        return [{"sid": 100000 + i, "bid": BID_POS, "food": 700.0, "drink": 100.0, "other": 0.0} for i in range(PER_MONTH_BILLS)]
    if "AS dwell" in sql:
        return [{"sid": 100000 + i, "bid": BID_POS, "dwell": 60} for i in range(PER_MONTH_BILLS)]
    if "si.BranchId AS bid, si.ItemId AS iid" in sql:
        return [{"bid": BID_POS, "iid": 100, "ym": ym, "qty": 10.0, "sub_exvat": 100.0, "tax": 7.0}]
    if "s.SaleId sid, s.BranchId bid, s.SaleNum num" in sql:
        n = PER_MONTH_DISC_LINES if "s.Discount>0" in sql else PER_MONTH_DISC_LINES // 2
        return [{"sid": 100000 + i // 4, "bid": BID_POS, "num": (i // 4) % 900, "d": day, "iid": (100, 200, 500, 600)[i % 4],
                 "qty": 2.0, "rev": 500.0} for i in range(n)]
    if "s.SaleNum num, s.SaleId sid" in sql:
        return [{"bid": BID_POS, "d": day, "num": i % 900, "sid": 100000 + i, "pc": "CASH", "amt": 100.0} for i in range(PER_MONTH_PAY)]
    return None


class _GenCursor(tsm.FakeCursor):
    def execute(self, sql, params=None):
        rows = None
        if params is not None and len(params) == 2 and ">= %s AND" in sql:
            rows = _gen(sql, str(params[0]))
        if rows is None:
            super().execute(sql, params)
        else:
            self._rows = rows


class _GenConn:
    def __init__(self, patterns):
        self._patterns = patterns

    def cursor(self, as_dict=True):
        return _GenCursor(self._patterns)

    def close(self):
        pass


def test_a_full_run_over_a_synthetic_history_stays_under_the_ceiling(tmp_path, monkeypatch):
    """22 months of ~1,500 bills / 3,000 discount lines / 1,400 payment lines each. Python-allocated memory (tracemalloc) must stay
    far below what holding that history would take (the old whole-history lists peaked at 51 MB for this size, 1.3-1.6 GB at production size; this stays near 7 MB)."""
    tsm.seed_current_db(tmp_path / "oshinei.db")
    patterns = [(r"MAX\(SaleDate\)", [{"mx": dt.date.today().isoformat()}])] + tsm.make_source() + STAGE_PATTERNS
    monkeypatch.setattr(sync, "DB", tmp_path / "oshinei.db")
    monkeypatch.setattr(sync, "INCOMING", tmp_path / "oshinei.db.incoming")
    monkeypatch.setattr(sync, "STAGE_FALLBACK", tmp_path / "oshinei.db.incoming.new")
    monkeypatch.setattr(sync, "connect_mssql", lambda: _GenConn(patterns))
    monkeypatch.setattr(sys, "argv", ["sync_mssql.py", "--since", "2025-01-01", "--no-menu-fact"])
    tracemalloc.start()
    try:
        try:
            sync.main()
        except SystemExit as e:
            assert not e.code
        _cur, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    con = sqlite3.connect(str(tmp_path / "oshinei.db.incoming"))
    n = con.execute("SELECT COUNT(*) FROM sales_transaction").fetchone()[0]
    con.close()
    print(f"tracemalloc peak {peak / 1e6:.1f} MB")
    assert n == PER_MONTH_BILLS * WINDOWS          # the data really went through
    assert peak < 20 * 1024 * 1024, f"peak {peak / 1e6:.0f} MB: something holds the whole history again"
