"""A later supplier quote prices an item never bought — the first release needs it.

The DATAP sheet is a supplier quote dated 23-07-2569, while the latest accounting
month is June. An item with no June purchase therefore had no price at all for a
draft priced at June. The rule: an item with NO price up to the month takes the
earliest LATER quote (list_ref); a later actual purchase never reaches back, and any
price up to the month — actual or quote — still wins.
"""
import sqlite3

from bom_center import prices as P
from bom_center import schema


def _db():
    c = sqlite3.connect(":memory:")
    schema.ensure(c)
    c.executemany("INSERT INTO bc_ingredient_price(ingredient_code, month, price_per_base, source) VALUES(?,?,?,?)", [
        ("QUOTE-ONLY", "2026-07", 0.5, "list_ref"),
        ("QUOTE-ONLY", "2026-09", 0.6, "list_ref"),
        ("BOUGHT", "2026-06", 1.0, "purchase_avg"),
        ("BOUGHT", "2026-07", 9.0, "list_ref"),
        ("LATER-BUY", "2026-07", 2.0, "purchase_avg"),
    ])
    return c


def test_a_later_quote_prices_an_item_never_bought():
    p = P.price_for(_db(), "QUOTE-ONLY", "2026-06")
    assert p == (0.5, "list_ref", "2026-07", -1)                  # the earliest later quote, age < 0


def test_anything_up_to_the_month_still_wins():
    assert P.price_for(_db(), "BOUGHT", "2026-06")[:2] == (1.0, "purchase_avg")
    assert P.price_for(_db(), "QUOTE-ONLY", "2026-08")[:3] == (0.5, "list_ref", "2026-07")


def test_a_later_purchase_never_reaches_back():
    assert P.price_for(_db(), "LATER-BUY", "2026-06") is None
    assert P.price_for(_db(), "NEVER", "2026-06") is None
