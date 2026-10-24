# -*- coding: utf-8 -*-
"""The delivery-calendar page (ตั้งค่า → รอบส่งของสาขา, PR-16 / PR-A): the GET names the
pilot branches so the page can show them alone, and a save of some rows leaves
every other (branch, round) untouched — the page sends only what changed. The sales team
sets the DELIVERY weekday; the cutoff weekday (13:15) and the lead are derived."""
from bom_center import forecast as FC
from bom_center.pilot import PILOT


def test_calendar_get_names_the_pilot_branches(client):
    j = client.get("/api/bom-center/order-calendar").json()
    assert j["pilot"] == list(PILOT)
    assert j["rows"] and all({"branch_id", "branch", "r1", "r2", "rsalmon"} <= set(r) for r in j["rows"])
    assert j["defaults"]["1"]["delivery_weekday"] == 3          # round 1 is delivered on Wednesday
    assert j["cutoff_time"] == "13:15"
    assert j["rows"][0]["r1"]["cutoff_th"] == "วันจันทร์ 13:15"


def test_a_partial_save_leaves_the_other_rounds_as_defaults(client, app_db, monkeypatch):
    monkeypatch.setenv("OSHINEI_SEED_TOKEN", "t")
    rows = client.get("/api/bom-center/order-calendar").json()["rows"]
    bid = rows[0]["branch_id"]
    # RV-M: the save below changed branch 1's round 1 for the rest of the session (its delivery day became Friday), so a later module
    # that orders for 7 Oct got "วันส่งไม่ตรงกับวันส่งที่กำหนดไว้ของรอบนี้" whenever it ran after this one in the same process
    # (test_slip_forecast in a split run). The branch's calendar rows are put back afterwards.
    import sqlite3
    k = sqlite3.connect(str(app_db))
    cols = [r[1] for r in k.execute("PRAGMA table_info(bc_order_calendar)")]
    saved = k.execute("SELECT * FROM bc_order_calendar WHERE branch_id=?", (bid,)).fetchall()
    k.close()
    try:
        r = client.post("/api/bom-center/order-calendar", headers={"X-Upload-Token": "t"},
                        json=[{"branch_id": bid, "round": "1", "delivery_weekday": 5}])        # Friday → cutoff Wednesday
        assert r.status_code == 200, r.text
        assert r.json()["rows"] == 1
        row = next(x for x in client.get("/api/bom-center/order-calendar").json()["rows"] if x["branch_id"] == bid)
        assert row["r1"]["filled"] and row["r1"]["delivery_weekday"] == 5
        assert row["r1"]["order_weekday"] == 3 and row["r1"]["lead_days"] == 2 and row["r1"]["cutoff_th"] == "วันพุธ 13:15"
        assert not row["r2"]["filled"] and not row["rsalmon"]["filled"]        # untouched rounds stay defaults
        assert row["r2"]["delivery_weekday"] == FC.DEFAULT_CAL["2"]["delivery_weekday"]
    finally:
        k = sqlite3.connect(str(app_db))
        k.execute("DELETE FROM bc_order_calendar WHERE branch_id=?", (bid,))
        k.executemany(f"INSERT INTO bc_order_calendar({', '.join(cols)}) VALUES({', '.join('?' * len(cols))})", saved)
        k.commit()
        k.close()
