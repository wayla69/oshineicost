"""Requisitions vs recipes (routers/requisition.py).

The report exists because two honest numbers disagreed: operations read food cost
off the stores' issue reports (78.4%), the app off recipes (68.1%). These tests pin
the parser against the accounting system's real sheet layout, the replace-on-
re-upload rule, and the switch from "issued" to "used" once stock counts exist —
the difference between an upper bound and a measurement.
"""
import io

import openpyxl
import pytest

from conftest import LATEST_MONTH, PREV_MONTH

import routers.requisition as rq


def _workbook(sheets):
    """sheets = {sheet_name: (thai_date_from, [(code, name, qty, unit, price), …])}
    laid out like รายงานจ่ายสินค้าภายใน: a blank first column, 4 header rows, a
    document row, then the item rows."""
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for name, (date_from, items) in sheets.items():
        ws = wb.create_sheet(name)
        ws.append([None, "บริษัท ทดสอบ จำกัด"])
        ws.append([None, "รายงานจ่ายสินค้าภายใน"])
        ws.append([None, f"วันที่จาก  {date_from}      ถึง 30/06/2569"])
        ws.append([None, "เอกสาร", "วันที่เอกสาร", "แผนก", "หมายเหตุ", "ลำดับ", "รหัสสินค้า",
                   "รายละเอียด", "คลังที่", "จำนวน", "หน่วยนับ", "ราคาต่อหน่วย", "มูลค่ารวม"])
        ws.append([None, "OU6906001", "2569-06-08", "สนง1", "ตัดเบิก"])
        for i, (code, nm, qty, unit, price) in enumerate(items, 1):
            ws.append([None, None, None, None, None, i, code, nm, 1, qty, unit, price, qty * price])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


@pytest.fixture()
def br1(monkeypatch):
    monkeypatch.setattr(rq, "SHEET2BR", {"KR1": 1})        # the fixture's BranchA
    return 1


def _upload(client, data, **params):
    return client.post("/api/requisition/upload", params=params,
                       files={"file": ("req.xlsx", data, "application/vnd.ms-excel")})


def test_parser_reads_the_real_layout_and_the_buddhist_month(br1):
    data = _workbook({"Use KR1": ("01/06/2569", [("02-01-001", "แซลมอน", 10, "กิโลกรัม", 374.5),
                                                  ("V-015", "ผักกาดขาว", 0, "กิโลกรัม", 39.0)]),   # qty 0 -> dropped
                      "Use RKR": ("01/06/2569", [("CD001", "x", 1, "kg", 1)]),
                      "Use ZZZ": ("01/06/2569", [("A", "x", 1, "kg", 1)])})
    lines, info = rq.parse_workbook(data)
    assert [(ln["item_code"], ln["period_month"], ln["doc_no"], ln["value"]) for ln in lines] == \
        [("02-01-001", "2026-06", "OU6906001", 3745.0)]
    assert info["sheets"] == [{"sheet": "Use KR1", "branch_id": 1, "month": "2026-06", "rows": 1, "value": 3745.0}]
    assert {s["sheet"] for s in info["skipped"]} == {"Use RKR", "Use ZZZ"}     # and each says why


def test_upload_then_report_then_a_corrected_file_replaces_the_month(client, br1):
    assert client.get("/api/requisition/vs-recipe").json()["ready"] is False
    first = _workbook({"Use KR1": ("01/06/2569", [("02-01-001", "แซลมอน", 10, "กก.", 400.0)])})
    assert _upload(client, first, dry_run=True).json()["dry_run"] is True
    assert client.get("/api/requisition/vs-recipe").json()["ready"] is False      # a dry run writes nothing
    assert _upload(client, first).status_code == 200

    d = client.get("/api/requisition/vs-recipe", params={"month": LATEST_MONTH}).json()
    row = d["branches"][0]
    # fixture June, BranchA: recipe COGS = (500*8 + 50*10) * 0.8 = 3600, revenue 12000, 80 covers
    assert (row["issued"], row["recipe"], row["covers"], row["basis"]) == (4000, 3600, 80, "issued")
    assert row["ratio"] == 1.11 and row["food_pct_actual"] == 33.3 and row["food_pct_recipe"] == 30.0
    assert row["actual_head"] == 50.0 and row["recipe_head"] == 45.0 and "เบิก" in d["caveat"]

    fixed = _workbook({"Use KR1": ("01/06/2569", [("02-01-001", "แซลมอน", 9, "กก.", 400.0)])})
    assert _upload(client, fixed).status_code == 200
    assert client.get("/api/requisition/vs-recipe").json()["branches"][0]["issued"] == 3600   # replaced, not doubled


def test_stock_counts_turn_issued_into_used(client, br1):
    _upload(client, _workbook({"Use KR1": ("01/06/2569", [("02-01-001", "แซลมอน", 10, "กก.", 400.0)])}))
    put = lambda m, v: client.post("/api/requisition/stock-count",
                                   json={"period_month": m, "branch_id": 1, "closing_value": v})
    assert put(LATEST_MONTH, 900).status_code == 200
    # only ONE end of the month is known -> still an upper bound
    assert client.get("/api/requisition/vs-recipe").json()["branches"][0]["basis"] == "issued"
    assert put(PREV_MONTH, 500).status_code == 200
    row = client.get("/api/requisition/vs-recipe").json()["branches"][0]
    # used = opening 500 + drawn 4000 - closing 900 = 3600: the branch built ฿400 of stock
    assert (row["basis"], row["issued"], row["actual"], row["ratio"]) == ("used", 4000, 3600, 1.0)
    assert put("2026-6", 1).status_code == 400 and put(LATEST_MONTH, -1).status_code == 400


def test_ingredient_gap_separates_booked_from_never_in_a_recipe(client, br1):
    _upload(client, _workbook({"Use KR1": ("01/06/2569", [("NOT-IN-BOM", "ถุงมือ", 5, "กล่อง", 100.0)])}))
    d = client.get("/api/requisition/vs-recipe").json()
    item = d["ingredients"][0]
    assert (item["code"], item["drawn"], item["booked"], item["in_recipes"]) == ("NOT-IN-BOM", 500, 0, False)
    assert d["not_in_any_recipe"] == 500


def test_a_placeholder_recipe_code_is_bridged_to_its_stock_code(client, br1):
    """Durian recipes name their ingredient EST-DURIAN, the stores issue FV00043.
    Unbridged, the largest line in the issue reports (฿7.6M over two months) read
    as 'in no recipe at all' while ฿68 a portion of it was in fact booked."""
    import sqlite3
    import core
    con = sqlite3.connect(str(core.DB_PATH))
    con.execute("CREATE TABLE IF NOT EXISTS bom_ingredients(sku_id INT, seq INT, ingredient_code TEXT,"
                " ingredient_name TEXT, qty_per_serving REAL, unit TEXT, price_per_unit REAL,"
                " ingredient_cost REAL, ingredient_type TEXT, source_file TEXT, PRIMARY KEY(sku_id, seq))")
    con.execute("INSERT OR REPLACE INTO bom_ingredients VALUES(2, 99, 'EST-DURIAN', 'ทุเรียน', 180, 'กรัม',"
                " 0.3778, 68.0, 'protein', 't')")
    con.commit(); con.close()
    try:
        _upload(client, _workbook({"Use KR1": ("01/06/2569", [("FV00043", "ทุเรียน", 100, "กก.", 380.0)])}))
        d = client.get("/api/requisition/vs-recipe", params={"month": LATEST_MONTH}).json()
        item = next(i for i in d["ingredients"] if i["code"] == "FV00043")
        # fixture June: 400 plates of sku 2 for BranchA x ฿68
        assert item["in_recipes"] is True and item["booked"] == 27200 and item["gap"] == 38000 - 27200
    finally:
        con = sqlite3.connect(str(core.DB_PATH))
        con.execute("DELETE FROM bom_ingredients WHERE sku_id=2 AND seq=99")
        con.commit(); con.close()
