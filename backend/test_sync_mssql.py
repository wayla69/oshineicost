"""Tests for the MSSQL→SQLite sync's pure transforms and the reconcile gate
(the safety net that blocks a bad prod push). No live MSSQL needed.

If sync_mssql can't import in this environment (missing optional deps), the
whole module is skipped rather than failing CI.
"""
import sqlite3
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).parent.resolve()
sys.path.insert(0, str(HERE))

sync = pytest.importorskip("sync_mssql")


def test_th_decodes_tis620():
    """varchar Thai comes back latin1-mangled under the UTF-8 connection; _th
    must restore it (this is the CP874/charset bug that emptied branch names)."""
    original = "ลูกค้าทั่วไป"
    mangled = original.encode("cp874").decode("latin1")   # how pymssql hands it back
    assert sync._th(mangled) == original
    assert sync._th(None) is None
    assert sync._th("") == ""


def test_short_name_uses_alias_or_passthrough():
    # passthrough when no alias
    assert sync.short_name("BranchThatHasNoAlias") == "BranchThatHasNoAlias"
    # alias mapping is applied (BRANCH_ALIAS maps full POS names → short DB names)
    assert isinstance(sync.BRANCH_ALIAS, dict)
    for full, short in list(sync.BRANCH_ALIAS.items())[:1]:
        assert sync.short_name(full) == short


def _mini_db(path, chain_rev, ub_apr, bills):
    con = sqlite3.connect(path)
    con.executescript("""
      CREATE TABLE branches(branch_id INT PRIMARY KEY, name TEXT);
      CREATE TABLE skus(sku_id INT PRIMARY KEY, sku_code TEXT, description TEXT);
      CREATE TABLE sales_sku_monthly(branch_id INT, period_month TEXT, sku_id INT,
        qty REAL, subtotal_ex_vat REAL, tax_total REAL, total_inc_vat REAL, source TEXT);
      CREATE TABLE sales_transaction(branch_id INT, receipt_no TEXT, sold_at TEXT,
        customer_count INT, total_inc_vat REAL, source TEXT);
    """)
    con.execute("INSERT INTO branches VALUES(1,'อุบลราชธานี')")
    con.execute("INSERT INTO skus VALUES(1,'A0001','(A) item')")
    con.execute("INSERT INTO sales_sku_monthly VALUES(1,'2026-05',1,10,?,0,?, 'Easy')",
                (chain_rev, chain_rev * 1.07))
    for i in range(bills):
        con.execute("INSERT INTO sales_transaction VALUES(1,?, '2026-05-10T12:00', 2, 100, 'Easy')", (f"m{i}",))
    # Ubon Apr 2026 receipts (critical anchor) — inc-VAT so /1.07 = ub_apr
    con.execute("INSERT INTO sales_transaction VALUES(1,'apr1','2026-04-10T12:00',2,?, 'Easy')",
                (round(ub_apr * 1.07, 2),))
    con.commit(); con.close()


def test_reconcile_passes_when_within_tolerance(tmp_path, monkeypatch):
    cur = tmp_path / "current.db"; inc = tmp_path / "incoming.db"
    _mini_db(str(cur), chain_rev=1_000_000, ub_apr=500_000, bills=100)
    _mini_db(str(inc), chain_rev=1_005_000, ub_apr=500_500, bills=100)   # +0.5% drift
    monkeypatch.setattr(sync, "DB", cur)
    sq = sqlite3.connect(str(inc)); sq.row_factory = sqlite3.Row
    assert sync.reconcile(sq) is True
    sq.close()


def test_reconcile_fails_on_big_drift(tmp_path, monkeypatch):
    cur = tmp_path / "current.db"; inc = tmp_path / "incoming.db"
    _mini_db(str(cur), chain_rev=1_000_000, ub_apr=500_000, bills=100)
    _mini_db(str(inc), chain_rev=1_200_000, ub_apr=500_000, bills=100)   # +20% chain rev → must FAIL
    monkeypatch.setattr(sync, "DB", cur)
    sq = sqlite3.connect(str(inc)); sq.row_factory = sqlite3.Row
    assert sync.reconcile(sq) is False, "reconcile must block a >2% chain-revenue drift"
    sq.close()
