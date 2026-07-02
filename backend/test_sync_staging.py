"""sync_mssql staging transforms, exercised WITHOUT a live MSSQL.

A FakeMC plays the remote cursor: it pattern-matches each query the stagers
issue and returns canned as_dict rows, so stage_reports/stage_extra run their
REAL row-shaping (branch/item id mapping, receipt building, payment-code
normalisation, TIS-620 decode, rounding, unknown-branch dropping) end-to-end
into a real in-memory SQLite. Also covers the pure helpers and the
ResilientCursor retry loop that test_sync_mssql.py doesn't reach.
"""
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
