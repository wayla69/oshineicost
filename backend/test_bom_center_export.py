# -*- coding: utf-8 -*-
"""The consolidated order export (BoM Center UX overhaul, PR-11).

The promises:
  * the consolidation follows the status asked for (open = sent or processed is the
    default, as before); received orders are no longer silently included or excluded
    against the chip the person chose;
  * split=true adds one sheet per supplier whose totals equal the summary sheet's;
  * the file name carries the status and the date range; an unknown status is a 400.
"""
import io
import sqlite3

from openpyxl import load_workbook


def _rows(db_path):
    c = sqlite3.connect(str(db_path))
    tables = [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'bc%'")]
    snap = {t: c.execute(f"SELECT * FROM {t}").fetchall() for t in tables}
    c.close()
    return snap


def _restore(db_path, snap):
    c = sqlite3.connect(str(db_path))
    for t, rows in snap.items():
        c.execute(f"DELETE FROM {t}")
        if rows:
            c.executemany(f"INSERT INTO {t} VALUES({','.join('?' * len(rows[0]))})", rows)
    c.commit()
    c.close()


def test_status_split_and_filename(client):
    import main
    client.get("/api/bom-center/hub/queue")                    # creates the bc_* tables
    snap = _rows(main.DB_PATH)
    c = sqlite3.connect(str(main.DB_PATH))
    bid = c.execute("SELECT branch_id FROM branches WHERE name<>'ALL' ORDER BY branch_id LIMIT 1").fetchone()[0]
    c.executemany("INSERT OR REPLACE INTO bc_ingredient(code, name_th, kind, base_unit, recv_unit, qty_per_recv, supplier, active)"
                  " VALUES(?,?,?,?,?,?,?,1)",
                  [("X-1", "ของ ก", "raw", "กรัม", "แพ็ค", 1000, "ผู้จำหน่าย ก"), ("X-2", "ของ ข", "raw", "กรัม", "แพ็ค", 500, "ผู้จำหน่าย ข")])
    c.execute("INSERT INTO bc_order(branch_id, cycle_date, round, status, value) VALUES(?, '2026-10-06', '1', 'submitted', 0)", (bid,))
    sub = c.execute("SELECT MAX(order_id) FROM bc_order").fetchone()[0]
    c.execute("INSERT INTO bc_order(branch_id, cycle_date, round, status, value) VALUES(?, '2026-10-02', '2', 'received', 0)", (bid,))
    rec = c.execute("SELECT MAX(order_id) FROM bc_order").fetchone()[0]
    c.executemany("INSERT INTO bc_order_line(order_id, ingredient_code, suggested_units, confirmed_units, order_unit) VALUES(?,?,?,?,'pack')",
                  [(sub, "X-1", 3, 3), (sub, "X-2", 2, 2), (rec, "X-1", 5, 5)])
    c.commit()
    c.close()
    try:
        q = "date_from=2026-09-01&date_to=2026-10-31"
        d = client.get(f"/api/bom-center/hub/consolidated?{q}").json()              # default: open
        assert d["status"] == "open" and {g["supplier"] for g in d["groups"]} == {"ผู้จำหน่าย ก", "ผู้จำหน่าย ข"}
        assert sum(it["total"] for g in d["groups"] for it in g["items"] if it["code"] == "X-1") == 3
        d = client.get(f"/api/bom-center/hub/consolidated?{q}&status=received").json()
        assert d["status_th"] == "สาขารับแล้ว"
        assert [it["total"] for g in d["groups"] for it in g["items"]] == [5]
        assert client.get(f"/api/bom-center/hub/consolidated?{q}&status=bogus").status_code == 400
        r = client.get(f"/api/bom-center/hub/consolidated.xlsx?{q}&status=submitted&split=true")
        assert r.status_code == 200
        assert "bom_center_orders_submitted_2026-09-01_2026-10-31.xlsx" in r.headers["content-disposition"]
        wb = load_workbook(io.BytesIO(r.content), read_only=True)
        assert wb.sheetnames == ["รวมตามผู้จำหน่าย", "ต่อสาขา", "ผู้จำหน่าย ก", "ผู้จำหน่าย ข"]
        summary = {(row[1], row[2]): row[5] for row in wb["รวมตามผู้จำหน่าย"].iter_rows(min_row=3, values_only=True) if row[2]}
        assert summary[("ผู้จำหน่าย ก", "X-1")] == 3 and summary[("ผู้จำหน่าย ข", "X-2")] == 2
        per = {row[1]: row[4] for row in wb["ผู้จำหน่าย ก"].iter_rows(min_row=3, values_only=True) if row[1]}
        assert per == {"X-1": 3}                                                     # the supplier's page matches
        r = client.get(f"/api/bom-center/hub/consolidated.xlsx?{q}")
        assert load_workbook(io.BytesIO(r.content), read_only=True).sheetnames == ["รวมตามผู้จำหน่าย", "ต่อสาขา"]
        assert "bom_center_orders_open_" in r.headers["content-disposition"]
    finally:
        _restore(main.DB_PATH, snap)
