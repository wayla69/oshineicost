"""sync_mssql staging transforms, exercised WITHOUT a live MSSQL.

A FakeMC plays the remote cursor: it pattern-matches each query the stagers
issue and returns canned as_dict rows, so stage_reports/stage_extra run their
REAL row-shaping (branch/item id mapping, receipt building, payment-code
normalisation, TIS-620 decode, rounding, unknown-branch dropping) end-to-end
into a real in-memory SQLite. Also covers the pure helpers and the
ResilientCursor retry loop that test_sync_mssql.py doesn't reach.
"""
import os
import re
import sqlite3
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).parent.resolve()
sys.path.insert(0, str(HERE))

sync = pytest.importorskip("sync_mssql")

TH = "ลูกค้าทั่วไป"
TH_MANGLED = TH.encode("cp874").decode("latin1")   # how pymssql hands varchar back


# ── pure helpers ─────────────────────────────────────────────────────────────
@pytest.mark.parametrize("cat,expected", [
    ("SOFT DRINK", "drink"), ("Local Beverage", "drink"), ("เครื่องดื่ม", "drink"),
    ("ALCOHOL BAR", "drink"),
    ("BUFFET PREMIUM", "other"), ("Delivery Fee", "other"), ("PROMOTION", "other"),
    ("แพ็คเกจ A", "other"), ("GIFT VOUCHER", "other"), ("เงินโอน", "other"),
    ("Sashimi", "food"), ("A La Carte", "food"), ("", "food"), (None, "food"),
])
def test_bucket_for_cat(cat, expected):
    assert sync.bucket_for_cat(cat) == expected


def test_norm_paycode_collapses_cash_case_dups():
    assert sync._norm_paycode("CASH") == "Cash"
    assert sync._norm_paycode(" cash ") == "Cash"
    assert sync._norm_paycode("Credit") == "Credit"
    assert sync._norm_paycode("QR code TTB") == "QR code TTB"
    assert sync._norm_paycode(None) == ""


def test_month_windows_are_contiguous_and_honour_since_day():
    import datetime
    wins = sync._month_windows("2026-01-15")
    # first window starts exactly at `since`, later ones on the 1st
    assert wins[0][0] == "2026-01-15"
    assert wins[0][1] == "2026-02-01"
    for (lo, hi), (lo2, _hi2) in zip(wins, wins[1:]):
        assert hi == lo2, "windows must be contiguous"
        assert lo2.endswith("-01")
    # covers up to (and including) the current month: last hi is next month's 1st
    today = datetime.date.today()
    last_hi = datetime.date.fromisoformat(wins[-1][1])
    assert (last_hi.year, last_hi.month) > (today.year, today.month) or (
        last_hi.year == today.year + (today.month == 12)
    )
    assert datetime.date.fromisoformat(wins[-1][0]) <= today


def test_chunk_runs_once_per_window_and_concatenates():
    calls = []

    class MC:
        def execute(self, sql, params=None):
            calls.append((sql, params))
        def fetchall(self):
            return [len(calls)]   # distinct row per window

    windows = [("2026-01-01", "2026-02-01"), ("2026-02-01", "2026-03-01")]
    out = sync._chunk(MC(), "SELECT x FROM t WHERE {RANGE}", windows, col="s.SaleDate")
    assert out == [1, 2]
    assert len(calls) == 2
    for (sql, params), (lo, hi) in zip(calls, windows):
        assert "s.SaleDate >= %s AND s.SaleDate < %s" in sql
        assert "{RANGE}" not in sql
        assert params == (lo, hi)


# ── ResilientCursor ──────────────────────────────────────────────────────────
def test_resilient_cursor_retries_transient_then_succeeds(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)
    attempts = {"n": 0}

    class FlakyCur:
        def execute(self, sql, params=None):
            attempts["n"] += 1
            if attempts["n"] < 3:
                raise Exception("DB-Lib error 20047: DBPROCESS is dead or not enabled")
        def fetchall(self):
            return [{"ok": 1}]

    class Conn:
        def cursor(self, as_dict=True): return FlakyCur()
        def close(self): pass

    rc = sync.ResilientCursor(lambda: Conn())
    rc.execute("SELECT 1")
    assert rc.fetchall() == [{"ok": 1}]
    assert rc.fetchone() == {"ok": 1}
    assert attempts["n"] == 3    # 2 transient failures + 1 success


def test_resilient_cursor_raises_non_transient_immediately(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: pytest.fail("must not back off"))

    class BadCur:
        def execute(self, sql, params=None):
            raise Exception("Incorrect syntax near 'FROM'")

    class Conn:
        def cursor(self, as_dict=True): return BadCur()
        def close(self): pass

    rc = sync.ResilientCursor(lambda: Conn())
    with pytest.raises(Exception, match="Incorrect syntax"):
        rc.execute("SELECT broken")


def test_resilient_cursor_gives_up_after_backoff_schedule(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)
    attempts = {"n": 0}

    class DeadCur:
        def execute(self, sql, params=None):
            attempts["n"] += 1
            raise Exception("unable to connect: Adaptive Server is unavailable")

    class Conn:
        def cursor(self, as_dict=True): return DeadCur()
        def close(self): pass

    rc = sync.ResilientCursor(lambda: Conn())
    with pytest.raises(Exception, match="unable to connect"):
        rc.execute("SELECT 1")
    assert attempts["n"] == len(sync.ResilientCursor._BACKOFFS) + 1


def test_apply_local_swap(tmp_path, monkeypatch):
    db = tmp_path / "oshinei.db"
    incoming = tmp_path / "oshinei.db.incoming"
    monkeypatch.setattr(sync, "DB", db)
    monkeypatch.setattr(sync, "INCOMING", incoming)
    # nothing to apply
    assert sync.apply_local_swap() is False
    # stale WAL/SHM are dropped and .incoming atomically replaces the live DB
    db.write_bytes(b"old")
    (tmp_path / "oshinei.db-wal").write_bytes(b"wal")
    (tmp_path / "oshinei.db-shm").write_bytes(b"shm")
    incoming.write_bytes(b"new")
    assert sync.apply_local_swap() is True
    assert db.read_bytes() == b"new"
    assert not incoming.exists()
    assert not (tmp_path / "oshinei.db-wal").exists()
    assert not (tmp_path / "oshinei.db-shm").exists()


# ── staging end-to-end against a FakeMC ──────────────────────────────────────
# One canned result set per query, matched by a distinctive SQL fragment.
# ORDER MATTERS: first match wins, so more specific patterns come first.
BID_POS, BID_UNKNOWN = 10, 99          # MSSQL BranchIds; 99 is not in bid_map
PATTERNS = [
    (r"MAX\(SaleDate\)", [{"mx": "2026-06-03"}]),
    # per-bill menu lines (31d COGS pull + full-history discount pull share the shape)
    (r"s\.SaleId sid, s\.BranchId bid, s\.SaleNum num",
     [{"sid": 55, "bid": BID_POS, "num": 7, "d": "2026-06-01", "iid": 100, "qty": 2.0, "rev": 500.0},
      {"sid": 55, "bid": BID_POS, "num": 7, "d": "2026-06-01", "iid": 200, "qty": 1.0, "rev": 100.0},
      {"sid": 1, "bid": BID_UNKNOWN, "num": 1, "d": "2026-06-01", "iid": 100, "qty": 9.0, "rev": 9.0}]),
    # sales_sku_daily
    (r"si\.BranchId bid, si\.ItemId iid",
     [{"bid": BID_POS, "iid": 100, "d": "2026-06-01", "qty": 3.006, "rev": 750.004},
      {"bid": BID_POS, "iid": 999, "d": "2026-06-01", "qty": 1.0, "rev": 1.0},     # unmapped item → dropped
      {"bid": BID_UNKNOWN, "iid": 100, "d": "2026-06-01", "qty": 1.0, "rev": 1.0}]),
    (r"FROM CancelSaleItem[\s\S]*char\(7\)",
     [{"bid": BID_POS, "iid": 100, "ym": "2026-06", "rid": 2, "q": 3.004, "ext": 300.006},
      {"bid": BID_UNKNOWN, "iid": 100, "ym": "2026-06", "rid": 2, "q": 1.0, "ext": 1.0}]),
    (r"FROM CancelSaleItem",
     [{"bid": BID_POS, "iid": 100, "d": "2026-06-01", "rid": 2, "q": 3.0, "ext": 300.0}]),
    (r"CouponSeriesId csid", [{"bid": BID_POS, "g": "2026-06", "csid": 5, "amt": 400.0, "n": 4}]),
    # per-receipt payment lines (split payment: CASH + Credit on one bill)
    (r"s\.SaleNum num, s\.SaleId sid",
     [{"bid": BID_POS, "d": "2026-06-01", "num": 7, "sid": 55, "pc": "CASH", "amt": 100.0},
      {"bid": BID_POS, "d": "2026-06-01", "num": 7, "sid": 55, "pc": "Credit", "amt": 970.0}]),
    (r"FROM SalePayment[\s\S]*char\(7\)",
     [{"bid": BID_POS, "ym": "2026-06", "pc": "CASH", "amt": 1000.004, "n": 10}]),
    (r"FROM SalePayment", [{"bid": BID_POS, "d": "2026-06-01", "pc": "Cash", "amt": 500.0, "n": 5}]),
    (r"FROM SaleTab", [{"bid": BID_POS, "d": "2026-06-01", "tabs": 3, "cust": 9, "avgmin": 45.06}]),
    (r"FROM CouponCardOutsideSystemUsed[\s\S]*char\(7\)",
     [{"bid": BID_POS, "ym": "2026-06", "cs": 5, "n": 4, "disc": 400.0}]),
    (r"FROM CouponCardOutsideSystemUsed",
     [{"bid": BID_POS, "d": "2026-06-01", "cs": 5, "n": 1, "disc": 100.0}]),
    (r"FROM CouponSeries\b",
     [{"CouponSeriesId": 5, "CouponSeriesName": " KTC Points ", "CouponSeriesCode": " KTC ", "Active": 1}]),
    (r"DeliveryType[\s\S]*char\(7\)|char\(7\)[\s\S]*DeliveryType",
     [{"bid": BID_POS, "ym": "2026-06", "dt": 1, "oc": 0, "bills": 5, "cust": 12, "total": 5000.0}]),
    (r"FROM Sale s[\s\S]*DeliveryType",
     [{"bid": BID_POS, "d": "2026-06-01", "dt": 1, "oc": 0, "bills": 5, "cust": 12, "total": 5000.0}]),
    (r"CookedTime", [{"bid": BID_POS, "ym": "2026-06", "avgsec": 63.44, "cooked": 100}]),
    # stage_extra sources
    (r"FROM CashCount",
     [{"bid": BID_POS, "d": "2026-06-01", "os": -1500.004, "ts": 100000.0, "pax": 250,
       "inv": 60, "disc": 500.0, "cancel": 200.0},
      {"bid": BID_UNKNOWN, "d": "2026-06-01", "os": 1.0, "ts": 1.0, "pax": 1, "inv": 1,
       "disc": 0.0, "cancel": 0.0}]),
    (r"FROM SaleByCustomerType",
     [{"bid": BID_POS, "ym": "2026-06", "tn": TH_MANGLED, "cust": 400, "net": 40000.0}]),
    (r"FROM SaleItemModifier",
     [{"bid": BID_POS, "iid": 100, "cn": "รับไข่", "n": 30, "q": 30.0}]),
    (r"FROM ChangePriceLog",
     [{"bid": BID_POS, "cd": "2026-06-02T10:00:00", "iid": 100, "descr": "dish",
       "dp": 200.0, "np": 180.0, "un": "DA"}]),
    (r"FROM FBTable", [{"bid": BID_POS, "zid": 1, "tabs": 20, "seats": 80}]),
    (r"FROM DeleteSaleReason", [{"rid": 2, "rd": TH_MANGLED}]),
    (r"FROM DiscountSaleReason", [{"rid": 3, "rd": TH_MANGLED, "pct": 10.0}]),
    (r"TOP 20000",
     [{"bid": BID_POS, "dt": "2026-06-01T13:00:00", "num": 42, "rid": 2, "amt": 600.0,
       "disc": 0.0, "duser": "cashier1"}]),
    (r"ISNULL\(Active,1\)=0|ISNULL\(s?\.?Active,1\)=0",
     [{"bid": BID_POS, "ym": "2026-06", "rid": 2, "n": 5, "amt": 6000.0}]),
    (r"Discount>0", [{"bid": BID_POS, "ym": "2026-06", "rid": 3, "n": 2, "amt": 150.0}]),
]


class FakeMC:
    def execute(self, sql, params=None):
        # what the real server would reject: a {RANGE} left unsubstituted, or the
        # doubled {{RANGE}} of a template that lost its f-prefix (2026-09-04)
        assert "{" not in sql and "}" not in sql, f"brace in SQL sent to MSSQL: {sql[:160]!r}"
        self._rows = []
        for pat, rows in PATTERNS:
            if re.search(pat, sql):
                self._rows = rows
                return

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None


@pytest.fixture(scope="module")
def staged():
    sq = sqlite3.connect(":memory:")
    sq.row_factory = sqlite3.Row
    sq.executescript((HERE / "schema.sql").read_text(encoding="utf-8"))
    sq.executescript("""
      CREATE TABLE IF NOT EXISTS bom_sku_alias(bom_sku_code TEXT PRIMARY KEY,
        resolved_sku_id INTEGER, match_method TEXT, confidence REAL, notes TEXT);
    """)
    sq.execute("INSERT INTO branches(branch_id,name) VALUES(1,'BranchA')")
    sq.execute("INSERT INTO categories(category_id,name) VALUES(1,'Food')")
    sq.execute("INSERT INTO skus(sku_id,sku_code,description,category_id) VALUES(1,'A0001','dish',1)")
    sq.execute("INSERT INTO sku_cost(sku_id,unit_cost) VALUES(1, 10.0)")
    # receipt total so the loss-bill scope has revenue to compare against
    sq.execute("INSERT INTO sales_transaction(branch_id,receipt_no,sold_at,total_inc_vat,source)"
               " VALUES(1,'2026-06-01-7-55','2026-06-01T12:00:00', 10.7, 'Easy')")
    sync.stage_reports(sq, FakeMC(), "2026-01-01",
                       bid_map={BID_POS: 1}, item_map={100: 1},
                       name2bid={"BranchA": 1},
                       windows=[("2026-01-01", "2026-02-01")])
    return sq


def test_stage_void_maps_ids_rounds_and_drops_unknown_branch(staged):
    rows = [tuple(r) for r in staged.execute("SELECT * FROM pos_void")]
    assert rows == [(1, "2026-06", 1, 2, 3.0, 300.01)]   # bid 10→1, iid 100→1, rounded 2dp


def test_stage_sale_payment_builds_receipt_and_normalises_cash(staged):
    rows = {(r["payment_code"]): r for r in
            staged.execute("SELECT * FROM sale_payment ORDER BY payment_code")}
    # split payment: both lines on the same reconstructed receipt
    assert set(rows) == {"Cash", "Credit"}               # 'CASH' collapsed to 'Cash'
    for r in rows.values():
        assert r["receipt_no"] == "2026-06-01-7-55"      # "<date>-<SaleNum>-<SaleId>"
        assert r["branch_id"] == 1


def test_stage_channel_and_tab_and_kitchen(staged):
    ch = staged.execute("SELECT * FROM pos_channel_month").fetchone()
    assert tuple(ch) == (1, "2026-06", 1, 0, 5, 12, 5000.0)
    tab = staged.execute("SELECT * FROM pos_table_turn_day").fetchone()
    assert tuple(tab) == (1, "2026-06-01", 3, 9, 45.1)   # avg dwell rounded to 1dp
    kt = staged.execute("SELECT * FROM pos_kitchen_month").fetchone()
    assert tuple(kt) == (1, "2026-06", 63.4, 100)


def test_stage_coupon_series_names_are_stripped(staged):
    cs = staged.execute("SELECT * FROM coupon_series").fetchone()
    assert tuple(cs) == (5, "KTC Points", "KTC", 1)


# The daily/bill pulls chunk over their OWN window lists derived from
# MAX(SaleDate) (120d / 31d lookback), so the FakeMC returns the same canned
# rows once per window — expectations must scale by the window count.
def _n_windows(lookback_days):
    import datetime
    mx = datetime.date(2026, 6, 3)                       # canned MAX(SaleDate)
    return len(sync._month_windows((mx - datetime.timedelta(days=lookback_days)).isoformat()))


def test_stage_daily_drops_unmapped_item_and_branch(staged):
    rows = [tuple(r) for r in staged.execute("SELECT * FROM sales_sku_daily")]
    # one identical row per 120-day window; unmapped item/branch rows dropped
    assert set(rows) == {(1, "2026-06-01", 1, 3.01, 750.0)}
    assert len(rows) == _n_windows(120)


def test_stage_bill_cogs_uses_cost_management_price(staged):
    # bill 7-55 per window: 2 × mapped item (cost 10) + 1 × unmapped (cost 0) = 20
    r = staged.execute("SELECT * FROM sale_bill_cogs").fetchone()
    assert r["receipt_no"] == "2026-06-01-7-55"
    assert r["cogs_bom"] == 20.0 * _n_windows(31)
    # COGS > bill revenue 10.7/1.07 = 10 → it's a LOSS bill, so its menu lines
    # are kept for the per-bill P&L drill (only mapped SKUs; deduped on
    # (receipt, sku) so repeated windows don't double-count).
    lines = [tuple(r) for r in staged.execute(
        "SELECT receipt_no, sku_id, qty, rev, bom_cost FROM sale_bill_item")]
    assert lines == [("2026-06-01-7-55", 1, 2.0, 500.0, 20.0)]


def test_stage_extra_cashcount_and_thai_decode(staged):
    cc = [tuple(r) for r in staged.execute("SELECT * FROM pos_cashcount_day")]
    assert cc == [(1, "2026-06-01", -1500.0, 100000.0, 250, 60, 500.0, 200.0)]
    ct = staged.execute("SELECT * FROM pos_customer_type_month").fetchone()
    assert ct["type_name"] == TH                         # TIS-620 mojibake decoded
    cr = staged.execute("SELECT * FROM pos_cancel_reason").fetchone()
    assert cr["reason_desc"] == TH
    dr = staged.execute("SELECT * FROM pos_discount_reason").fetchone()
    assert dr["reason_desc"] == TH and dr["amount_percent"] == 10.0


def test_stage_extra_cancel_and_discount_bills(staged):
    cb = staged.execute("SELECT * FROM pos_cancel_bill").fetchone()
    assert tuple(cb) == (1, "2026-06", 2, 5, 6000.0)
    cbd = staged.execute("SELECT * FROM pos_cancel_bill_detail").fetchone()
    assert cbd["period_month"] == "2026-06"              # derived from sale_date[:7]
    assert cbd["cancel_user"] == "cashier1"
    dm = staged.execute("SELECT * FROM pos_discount_month").fetchone()
    assert tuple(dm) == (1, "2026-06", 3, 2, 150.0)


# ── staged-file preparation: locked .incoming (WinError 32) resilience ───────
# 2026-08-08..10 the dashboard's swap poller held oshinei.db.incoming open for
# 3 days and every nightly sync died at INCOMING.unlink(). prepare_staging /
# publish_staging must ride out a transient lock, fall back to a private
# staging name for a persistent one, and never end in a raw traceback.

def _lock_err():
    e = PermissionError(13, "The process cannot access the file because it is "
                            "being used by another process")
    e.winerror = 32
    return e


@pytest.fixture
def staging_paths(tmp_path, monkeypatch):
    inc = tmp_path / "oshinei.db.incoming"
    fb = tmp_path / "oshinei.db.incoming.new"
    monkeypatch.setattr(sync, "INCOMING", inc)
    monkeypatch.setattr(sync, "STAGE_FALLBACK", fb)
    return inc, fb


def test_prepare_staging_claims_incoming_and_drops_stale_sidecars(staging_paths):
    inc, fb = staging_paths
    inc.write_bytes(b"old staged db")
    Path(str(inc) + "-wal").write_bytes(b"stale wal")     # the #72 orphan
    Path(str(inc) + "-shm").write_bytes(b"stale shm")
    fb.write_bytes(b"orphan of a failed fallback publish")
    assert sync.prepare_staging() == inc
    assert not inc.exists()
    assert not Path(str(inc) + "-wal").exists()
    assert not Path(str(inc) + "-shm").exists()
    assert not fb.exists()                                # superseded stray swept


def test_prepare_staging_retries_through_transient_lock(staging_paths, monkeypatch):
    inc, _fb = staging_paths
    inc.write_bytes(b"x")
    sleeps = []
    monkeypatch.setattr("time.sleep", sleeps.append)
    real_unlink, fails = Path.unlink, {"n": 2}

    def flaky_unlink(self, *a, **k):
        if self == inc and fails["n"]:
            fails["n"] -= 1
            raise _lock_err()
        return real_unlink(self, *a, **k)

    monkeypatch.setattr(Path, "unlink", flaky_unlink)
    assert sync.prepare_staging() == inc      # lock cleared during the retries
    assert sleeps == [1, 2]                   # backed off exactly while locked
    assert not inc.exists()


def test_prepare_staging_falls_back_when_incoming_stays_locked(staging_paths, monkeypatch, capsys):
    inc, fb = staging_paths
    inc.write_bytes(b"held by the dashboard")
    fb.write_bytes(b"leftover from a crashed fallback run")
    Path(str(fb) + "-wal").write_bytes(b"leftover wal")
    monkeypatch.setattr("time.sleep", lambda s: None)
    real_unlink = Path.unlink

    def locked_unlink(self, *a, **k):
        if self == inc:
            raise _lock_err()
        return real_unlink(self, *a, **k)

    monkeypatch.setattr(Path, "unlink", locked_unlink)
    assert sync.prepare_staging() == fb
    assert inc.exists()                       # untouched — another process owns it
    assert not fb.exists()                    # leftover cleaned, ready for copy2
    assert not Path(str(fb) + "-wal").exists()
    assert "locked" in capsys.readouterr().out


def test_prepare_staging_exits_actionably_when_everything_is_locked(staging_paths, monkeypatch):
    inc, fb = staging_paths
    inc.write_bytes(b"x")
    fb.write_bytes(b"y")
    monkeypatch.setattr("time.sleep", lambda s: None)

    def locked_unlink(self, *a, **k):
        raise _lock_err()

    monkeypatch.setattr(Path, "unlink", locked_unlink)
    with pytest.raises(SystemExit) as ei:
        sync.prepare_staging()
    # actionable message naming the holder, not a raw PermissionError traceback
    assert "dashboard" in str(ei.value)


def test_publish_staging_is_noop_on_direct_path(staging_paths):
    inc, _fb = staging_paths
    assert sync.publish_staging(inc) is True  # build already lives at .incoming


def test_publish_staging_replaces_incoming_and_clears_orphan_sidecars(staging_paths):
    inc, fb = staging_paths
    fb.write_bytes(b"fresh build")
    inc.write_bytes(b"stale locked-era incoming")          # lock has since cleared
    Path(str(inc) + "-wal").write_bytes(b"orphan wal")     # would graft (#72) if left
    assert sync.publish_staging(fb) is True
    assert inc.read_bytes() == b"fresh build"
    assert not fb.exists()
    assert not Path(str(inc) + "-wal").exists()


def test_publish_staging_folds_stages_own_wal_before_moving(staging_paths):
    """A -wal belonging to the STAGE file (e.g. menu-fact died pre-checkpoint) is
    replayed into it before the move — os.replace of the bare main file would
    silently drop those committed frames."""
    inc, fb = staging_paths
    con = sqlite3.connect(str(fb))
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA wal_autocheckpoint=0")             # keep the commit in the wal
    con.execute("CREATE TABLE t(x)")
    con.execute("INSERT INTO t VALUES(42)")
    con.commit()
    main_img = fb.read_bytes()                             # pre-checkpoint image
    wal_img = Path(str(fb) + "-wal").read_bytes()
    assert wal_img, "test premise: the commit must still live in the wal"
    con.close()                                            # checkpoints + deletes wal
    fb.write_bytes(main_img)                               # resurrect crashed-writer state
    Path(str(fb) + "-wal").write_bytes(wal_img)
    assert sync.publish_staging(fb) is True
    chk = sqlite3.connect(str(inc))
    assert chk.execute("SELECT x FROM t").fetchone()[0] == 42
    chk.close()
    assert not Path(str(fb) + "-wal").exists()


def test_publish_staging_succeeds_once_the_lock_clears(staging_paths, monkeypatch):
    inc, fb = staging_paths
    fb.write_bytes(b"fresh build")
    inc.write_bytes(b"old")
    monkeypatch.setattr("time.sleep", lambda s: None)
    real_replace, fails = os.replace, {"n": 2}

    def flaky_replace(src, dst):
        if fails["n"]:
            fails["n"] -= 1
            raise _lock_err()
        return real_replace(src, dst)

    monkeypatch.setattr(sync.os, "replace", flaky_replace)
    assert sync.publish_staging(fb) is True
    assert inc.read_bytes() == b"fresh build"


def test_publish_staging_retries_then_reports_actionably(staging_paths, monkeypatch, capsys):
    inc, fb = staging_paths
    fb.write_bytes(b"fresh build")
    inc.write_bytes(b"still locked")
    sleeps = []
    monkeypatch.setattr("time.sleep", sleeps.append)

    def locked_replace(src, dst):
        raise _lock_err()

    monkeypatch.setattr(sync.os, "replace", locked_replace)
    assert sync.publish_staging(fb) is False
    out = capsys.readouterr().out
    assert "dashboard" in out                 # names the holder + the fix
    assert fb.name in out                     # …and where the build was kept
    assert fb.exists()                        # a multi-minute build is never thrown away
    assert sleeps == list(sync._PUBLISH_WAITS)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows sharing-violation semantics")
def test_windows_real_lock_falls_back_then_publishes(staging_paths, monkeypatch):
    """End-to-end against a REAL open handle (what the dashboard poller holds):
    claim falls back to .incoming.new; publish succeeds the moment the holder
    lets go (here: during a retry backoff)."""
    inc, fb = staging_paths
    inc.write_bytes(b"held")
    holder = open(inc, "rb")                  # CRT open() denies delete/replace
    try:
        monkeypatch.setattr("time.sleep", lambda s: None)
        assert sync.prepare_staging() == fb   # unlink hit WinError 32 → fell back
        fb.write_bytes(b"fresh build")
        def sleep_release(_s):
            if not holder.closed:
                holder.close()                # the "user closes the dashboard" moment
        monkeypatch.setattr("time.sleep", sleep_release)
        assert sync.publish_staging(fb) is True
        assert inc.read_bytes() == b"fresh build"
    finally:
        if not holder.closed:
            holder.close()


def test_chunk_accepts_the_doubled_range_spelling_and_refuses_a_stray_brace():
    """A template written as a plain string may keep the doubled {{RANGE}} an
    f-string needs (2026-09-04: five stagers lost their f-prefix to a lint pass
    and MSSQL answered "Incorrect syntax near '>'"). Either spelling must reach
    the server as the window predicate; any other brace must never reach it."""
    class MC:
        def __init__(self):
            self.sql = []

        def execute(self, sql, params=None):
            self.sql.append(sql)

        def fetchall(self):
            return []

    mc = MC()
    win = [("2026-01-01", "2026-02-01")]
    sync._chunk(mc, "SELECT 1 FROM T WHERE {{RANGE}}", win, col="d")
    sync._chunk(mc, "SELECT 1 FROM T WHERE {RANGE}", win, col="d")
    assert mc.sql == ["SELECT 1 FROM T WHERE d >= %s AND d < %s"] * 2
    with pytest.raises(ValueError, match="brace"):
        sync._chunk(mc, "SELECT 1 FROM T WHERE {RANGE} AND x IN {ids}", win, col="d")
