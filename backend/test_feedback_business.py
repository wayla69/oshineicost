# -*- coding: utf-8 -*-
"""E2 — voices against the till: complaints on busy vs quiet days, the dishes
guests name against their sales rank and unit cost, branch voices against
revenue / guests / discount share / spend per head, and the reading."""
import sqlite3
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).parent.resolve()
sys.path.insert(0, str(HERE))

from routers import feedback_business as B  # noqa: E402

TODAY = "2026-07-01"          # the fixture's months are 2026-05 / 2026-06


def _wipe(con, table):
    if con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
        con.execute(f"DELETE FROM {table}")


@pytest.fixture(autouse=True)
def _clean(request):
    B._CACHE.clear()
    yield
    B._CACHE.clear()
    if "client" not in request.fixturenames:
        return
    import core
    con = sqlite3.connect(str(core.DB_PATH))
    _wipe(con, "customer_feedback")
    con.execute("DELETE FROM sales_transaction WHERE receipt_no LIKE 'e2-%'")
    con.execute("DELETE FROM sale_bill_item WHERE receipt_no LIKE 'e2-%' OR receipt_no LIKE '2026-06-%'")
    con.commit(); con.close()
    try:
        import main
        main.invalidate_cache()
    except Exception:
        pass


def _voices(client, rows):
    body = [{"branch": b, "date": d, "source": "Google", "rating": r, "text": t, "external_id": f"e2:{i}:{b}"}
            for i, (b, d, r, t) in enumerate(rows)]
    r = client.post("/api/feedback/upload", json={"rows": body, "by": "test"})
    assert r.status_code == 200, r.text
    return r.json()


def test_windows():
    assert B._window("2026-05", "2026-06", TODAY) == ("2026-05-01", "2026-06-30")
    assert B._window(None, None, "2026-09-05") == ("2026-07-01", "2026-09-05")
    assert B._window("2026-09", "2026-09", "2026-09-05") == ("2026-09-01", "2026-09-05")
    assert B._prev_window("2026-05-01", "2026-06-30") == ("2026-03-01", "2026-04-30")


def test_voices_against_the_till(client):
    import core
    con = sqlite3.connect(str(core.DB_PATH)); con.row_factory = sqlite3.Row
    days = [r[0][:10] for r in con.execute("SELECT DISTINCT substr(sold_at,1,10) FROM sales_transaction WHERE branch_id=1 AND sold_at LIKE '2026-06%' ORDER BY 1")]
    assert len(days) >= 3, days
    busy_day, quiet_day = days[0], days[1]
    # BranchA: one extra-busy day (4 bills instead of the usual 1) and dish sales on the fixture bills
    for i in range(3):
        con.execute("INSERT INTO sales_transaction(branch_id,receipt_no,sold_at,customer_count,food_subtotal,drink_subtotal,other_subtotal,"
                    "discount,service_charge,total_inc_vat,source) VALUES(1,?,?,4,900,100,0,2000,50,1070.0,'Easy')",
                    (f"e2-{i}", f"{busy_day}T13:00:00"))
    bills = [r[0] for r in con.execute("SELECT receipt_no FROM sales_transaction WHERE branch_id=1 AND sold_at LIKE '2026-06%'")]
    con.execute("CREATE TABLE IF NOT EXISTS sale_bill_item(receipt_no TEXT, sku_id INTEGER, qty REAL, rev REAL, bom_cost REAL)")
    for rc in bills:
        con.execute("INSERT INTO sale_bill_item VALUES(?,?,?,?,?)", (rc, 2, 30, 0, 240))      # (B) แซลมอน: 30 pieces, ฿8 each
        con.execute("INSERT INTO sale_bill_item VALUES(?,?,?,?,?)", (rc, 3, 1, 350, 120))    # (A) สเต๊ก
    con.commit(); con.close()
    _voices(client, [("BranchA", busy_day, 1, "รอนานมาก คิวยาว"), ("BranchA", busy_day, 2, "รอคิวนานมาก แซลมอนไม่สด"),
                     ("BranchA", busy_day, 2, "รอนาน"), ("BranchA", quiet_day, 2, "รอนาน พนักงานน้อย แซลมอนคาว"),
                     ("BranchB", quiet_day, 5, "แซลมอนสดมาก อร่อย"), ("BranchB", days[2], 5, "แซลมอนสด"),
                     ("BranchA", quiet_day, 1, "แพงไม่คุ้ม ราคาสูง")])
    j = client.get("/api/feedback/business", params={"month_from": "2026-06", "month_to": "2026-06", "today": TODAY}).json()
    assert j["range"] == ["2026-06-01", "2026-06-30"] and j["voices"] == 7
    wait = next(t for t in j["load"]["themes"] if t["theme"] == "รอนาน")
    assert wait["n"] == 4 and wait["matched"] == 4 and wait["on_busy_days"] == 3 and wait["busy_share"] == 75
    assert wait["verdict"].startswith("เกิดตอนแน่น")
    lb = {x["branch"]: x for x in j["load"]["branches"]}
    assert lb["BranchA"]["busy"] == 3 and lb["BranchA"]["median_bills"] == 1 and j["load"]["busy_days"] == 1
    menus = {m["menu"]: m for m in j["menus"]}
    salmon = menus["แซลมอน"]
    assert salmon["neg"] == 2 and salmon["pos"] == 2 and salmon["kind"] == "B" and salmon["cost_per_unit"] == 8.0
    assert salmon["rank"] == 1 and salmon["rank_pct"] == 50 and j["dishes_sold"] == 2
    assert any("ไม่พบยอดขาย" not in s for s in salmon["signals"]) or salmon["signals"] == []
    br = {b["branch"]: b for b in j["branches"]}
    a = br["BranchA"]
    assert a["voices"] == 5 and a["neg_pct"] == 100.0 and a["pax"] > 0 and a["disc_share"] is not None
    assert a["disc_share"] > br["BranchB"]["disc_share"] and a["rev_delta_pct"] is not None
    assert any("ส่วนลดสูงกว่าเครือ" in s for s in a["signals"]) and j["chain"]["disc_share_median"] is not None
    assert j["chain"]["spend_head_median"] and j["notes"] and any("รอนาน" in n for n in j["notes"])
    # the branch filter narrows everything; the cache serves the second read; `fresh` clears it
    jb = client.get("/api/feedback/business", params={"month_from": "2026-06", "month_to": "2026-06", "branch": "BranchB", "today": TODAY}).json()
    assert jb["voices"] == 2 and [b["branch"] for b in jb["branches"]] == ["BranchB"] and jb["menus"][0]["menu"] == "แซลมอน"
    assert client.get("/api/feedback/business", params={"month_from": "2026-06", "month_to": "2026-06", "branch": "BranchB", "today": TODAY}).json() == jb
    assert client.get("/api/feedback/business", params={"month_from": "2026-06", "month_to": "2026-06", "branch": "Nowhere", "today": TODAY, "fresh": 1}).json()["voices"] == 0


def test_empty_window_reads_cleanly(client):
    j = client.get("/api/feedback/business", params={"month_from": "2026-05", "month_to": "2026-05", "today": TODAY}).json()
    assert j["voices"] == 0 and j["menus"] == [] and all(t["n"] == 0 for t in j["load"]["themes"])
    assert j["notes"] and "ยังไม่มีสัญญาณ" in j["notes"][0] and len(j["branches"]) == 2
