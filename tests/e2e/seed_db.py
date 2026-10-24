"""Build the hermetic e2e fixture DB — the exact dataset the backend pytest
fixture uses (backend/conftest._build_db), so the dashboard has coherent
numbers on every tab without a production data pull.

Usage:  python tests/e2e/seed_db.py [output_path]
Then:   OSHINEI_DB=<output_path> uvicorn backend.main:app --port 8010
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

import conftest  # noqa: E402  (backend/conftest.py; sets OSHINEI_NO_SWAP=1)

out = Path(sys.argv[1] if len(sys.argv) > 1 else ROOT / "tests" / "e2e" / ".e2e-fixture.db")
out.parent.mkdir(parents=True, exist_ok=True)
if out.exists():
    out.unlink()
conftest._build_db(str(out))


def _supply_rows(path):
    """PR-MA: the phone e2e (phone-modes.spec.js) taps a row of คิวใบสั่งซื้อ and of ต้นทุนต่อจาน, which the sales fixture has no rows for:
    one order submitted today (the queue lists the last 7 days) and one dish cost of this month, both on the fixture's own branch and SKU."""
    import sqlite3
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from bom_center import dated, schema
    from rules import is_tier_code
    now = datetime.now(ZoneInfo("Asia/Bangkok"))
    con = sqlite3.connect(path)
    schema.ensure(con, commit=True)
    dated.ensure(con)
    bid = con.execute("SELECT MIN(branch_id) FROM branches").fetchone()[0]
    sku = next(s for s, code in con.execute("SELECT sku_id, sku_code FROM skus ORDER BY sku_id") if not is_tier_code(code))   # a dish, not a package
    day, at = now.strftime("%Y-%m-%d"), now.strftime("%Y-%m-%dT%H:%M:%S")
    cur = con.execute("INSERT INTO bc_order(branch_id, cycle_date, round, status, submitted_at) VALUES (?, ?, 'A', 'submitted', ?)", (bid, day, at))
    con.execute("INSERT INTO bc_order_line(order_id, ingredient_code, suggested_units, confirmed_units, order_unit) VALUES (?, 'E2E-001', 2, 2, 'กล่อง')",
                (cur.lastrowid,))
    con.execute("INSERT INTO bc_sku_cost_list(sku_id, month, unit_cost, note, imported_by, imported_at) VALUES (?, ?, 42.5, 'e2e', 'e2e', ?)",
                (sku, now.strftime("%Y-%m"), at))
    con.commit()
    con.close()


_supply_rows(str(out))
print(f"seeded e2e fixture DB -> {out}")
