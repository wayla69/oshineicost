# -*- coding: utf-8 -*-
"""The order-calendar page (ตั้งค่า → รอบสั่งของสาขา, PR-16): the GET names the
pilot branches so the page can show them alone, and a save of some rows leaves
every other (branch, round) untouched — the page sends only what changed."""
from bom_center import forecast as FC
from bom_center.pilot import PILOT


def test_calendar_get_names_the_pilot_branches(client):
    j = client.get("/api/bom-center/order-calendar").json()
    assert j["pilot"] == list(PILOT)
    assert j["rows"] and all({"branch_id", "branch", "r1", "r2", "rsalmon"} <= set(r) for r in j["rows"])
    assert j["defaults"]["1"]["order_weekday"] == 2


def test_a_partial_save_leaves_the_other_rounds_as_defaults(client, app_db, monkeypatch):
    monkeypatch.setenv("OSHINEI_SEED_TOKEN", "t")
    rows = client.get("/api/bom-center/order-calendar").json()["rows"]
    bid = rows[0]["branch_id"]
    r = client.post("/api/bom-center/order-calendar", headers={"X-Upload-Token": "t"},
                    json=[{"branch_id": bid, "round": "1", "order_weekday": 3, "lead_days": 2}])
    assert r.status_code == 200, r.text
    assert r.json()["rows"] == 1
    row = next(x for x in client.get("/api/bom-center/order-calendar").json()["rows"] if x["branch_id"] == bid)
    assert row["r1"]["filled"] and row["r1"]["order_weekday"] == 3 and row["r1"]["lead_days"] == 2
    assert not row["r2"]["filled"] and not row["rsalmon"]["filled"]        # untouched rounds stay defaults
    assert row["r2"]["order_weekday"] == FC.DEFAULT_CAL["2"]["order_weekday"]
