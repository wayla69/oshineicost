"""BoM Center — a quote sheet's undated rows take the sheet's own date.

DATAP dates most rows 23-07-2569; 18 rows carry no date. Imported from the app's
button (no month given) those rows lost their quote price — 299 instead of 317
reference prices on production (found 2026-10-07 before step 2 of the import).
"""
from datetime import datetime

from bom_center import master as M
from test_bom_center_s2 import _db


def test_undated_rows_use_the_sheets_date(tmp_path):
    con = _db(tmp_path)
    rows = [{"code": "X-1", "name": "a", "full_price": 100, "qty_per_recv": 1000, "date": datetime(2026, 7, 23)},
            {"code": "X-2", "name": "b", "full_price": 50, "qty_per_recv": 500, "date": datetime(2026, 7, 23)},
            {"code": "X-3", "name": "c", "full_price": 80, "qty_per_recv": 1000, "date": None}]
    st = M.import_rows(con, rows, "t")
    assert st["ref_prices"] == 3 and st["undated_month"] == "2026-07"
    assert con.execute("SELECT month FROM bc_ingredient_price WHERE ingredient_code='X-3'").fetchone()[0] == "2026-07"


def test_a_given_month_still_wins(tmp_path):
    con = _db(tmp_path)
    rows = [{"code": "X-1", "name": "a", "full_price": 100, "qty_per_recv": 1000, "date": None}]
    st = M.import_rows(con, rows, "t", default_month="2026-06")
    assert st["ref_prices"] == 1 and "undated_month" not in st
