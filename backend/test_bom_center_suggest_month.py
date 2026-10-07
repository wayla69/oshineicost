"""BoM Center — the price month the screen suggests is the latest ACTUAL purchase month.

On production (2026-10-07) DATAP quotes are dated 2026-07 and the accounts' actual
purchases June 2026; the screen suggested 2026-07, and the first buffet draft was
costed at the supplier's quote instead of what was actually paid.
"""
import sqlite3

from bom_center import schema as S


def test_suggested_price_month_is_the_latest_actual_month(client):
    import main
    c = sqlite3.connect(str(main.DB_PATH))
    S.ensure(c, commit=True)
    keep = c.execute("SELECT * FROM bc_ingredient_price").fetchall()
    try:
        c.execute("DELETE FROM bc_ingredient_price")
        c.executemany("INSERT INTO bc_ingredient_price(ingredient_code, month, price_per_base, source) VALUES(?,?,?,?)",
                      [("X-1", "2026-07", 0.2, "list_ref"), ("X-1", "2026-06", 0.18, "purchase_avg"),
                       ("X-2", "2026-07", 1.0, "list_ref")])
        c.commit()
        d = client.get("/api/bom-center/releases").json()
        assert d["suggest"]["price_month"] == "2026-06"
        assert d["price_months"][0] == "2026-07"                 # every priced month is still listed
        c.execute("DELETE FROM bc_ingredient_price WHERE source='purchase_avg'")
        c.commit()
        assert client.get("/api/bom-center/releases").json()["suggest"]["price_month"] == "2026-07"   # no actuals yet
    finally:
        c.execute("DELETE FROM bc_ingredient_price")
        if keep:
            c.executemany(f"INSERT INTO bc_ingredient_price VALUES({','.join('?' * len(keep[0]))})", keep)
        c.commit()
        c.close()
