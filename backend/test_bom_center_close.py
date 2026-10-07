# -*- coding: utf-8 -*-
"""ปิดงวดเดือน (BoM Center UX overhaul, PR-7): the centre's monthly report in ONE upload,
and who may import a count file.

The promises:
  * one call previews BOTH the central list and the month's goods-received notes,
    needs no token, and writes nothing;
  * the real call saves the list and the notes of the branches named, in one
    transaction — the list first, so the notes convert with the units it sets;
  * naming no branch books no note;
  * Sales Support may import a count file (the runbook asks them to) though the
    on-screen count stays the branch's.
"""
import sqlite3

import auth
from test_bom_center_s11 import _report

TOKEN = "s3cret-write-token"


def _rows(db_path):
    c = sqlite3.connect(str(db_path))
    tables = [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'bc%'")]
    snap = {t: c.execute(f"SELECT * FROM {t}").fetchall() for t in tables}
    c.close()
    return snap


def _restore(db_path, snap, branch_id, name):
    c = sqlite3.connect(str(db_path))
    for t, rows in snap.items():
        c.execute(f"DELETE FROM {t}")
        if rows:
            c.executemany(f"INSERT INTO {t} VALUES({','.join('?' * len(rows[0]))})", rows)
    c.execute("UPDATE branches SET name=? WHERE branch_id=?", (name, branch_id))
    c.commit()
    c.close()


def test_one_upload_previews_both_and_saves_both(client, monkeypatch):
    import main
    monkeypatch.setenv("OSHINEI_SEED_TOKEN", TOKEN)
    # the report names its branch "อุดร" (C1001): give the first branch that name for the test
    c = sqlite3.connect(str(main.DB_PATH))
    bid, old_name = c.execute("SELECT branch_id, name FROM branches WHERE name<>'ALL' ORDER BY branch_id LIMIT 1").fetchone()
    client.get("/api/bom-center/central")                       # creates the bc_* tables
    snap = _rows(main.DB_PATH)
    c.execute("UPDATE branches SET name='อุดรธานี' WHERE branch_id=?", (bid,))
    c.executemany("INSERT OR IGNORE INTO bc_ingredient(code, name_th, kind, base_unit) VALUES(?,?,?,?)",
                  [("13-01-009", "ปูอัด", "raw", "กรัม"), ("02-01-001", "แซลมอน", "raw", "กรัม")])
    c.commit()
    c.close()
    try:
        before = _rows(main.DB_PATH)
        # the preview: no token, both parts, nothing written
        r = client.post("/api/bom-center/central/month-import?dry_run=true", files={"file": ("r.xlsx", _report())})
        assert r.status_code == 200, r.text
        j = r.json()
        assert j["dry_run"] is True and j["month"] == "2026-09"
        assert j["catalogue"]["items"] >= 2 and j["catalogue"]["rows"]
        br = {b["code"]: b for b in j["deliveries"]["branches"]}
        assert br["C1001"]["branch_id"] == bid and br["C1001"]["lines"] >= 1
        assert br["C1099"]["error"]                               # no such branch
        assert _rows(main.DB_PATH) == before
        # the real call without a token is refused with the reason
        r = client.post(f"/api/bom-center/central/month-import?dry_run=false&branch_ids={bid}",
                        files={"file": ("r.xlsx", _report())})
        assert r.status_code == 403 and r.headers.get("X-Denied") == "token"
        # the real call: the list and the one note, in one transaction
        r = client.post(f"/api/bom-center/central/month-import?dry_run=false&branch_ids={bid}",
                        files={"file": ("r.xlsx", _report())}, headers={"X-Upload-Token": TOKEN})
        assert r.status_code == 200, r.text
        j = r.json()
        assert j["dry_run"] is False and j["catalogue"]["saved"] >= 2
        note = [b for b in j["deliveries"]["branches"] if b["branch_id"] == bid][0]
        assert note["receipt_id"] and note["lines"] >= 1
        c = sqlite3.connect(str(main.DB_PATH))
        assert c.execute("SELECT COUNT(*) FROM bc_receipt WHERE ref='CENTRAL-2026-09' AND branch_id=?", (bid,)).fetchone()[0] == 1
        assert c.execute("SELECT COUNT(*) FROM bc_central_item WHERE active=1").fetchone()[0] >= 2
        c.close()
        # naming no branch books no note (the list alone is refreshed)
        r = client.post("/api/bom-center/central/month-import?dry_run=false",
                        files={"file": ("r.xlsx", _report())}, headers={"X-Upload-Token": TOKEN})
        assert r.status_code == 200 and r.json()["deliveries"]["branches"] == []
        c = sqlite3.connect(str(main.DB_PATH))
        assert c.execute("SELECT COUNT(*) FROM bc_receipt WHERE ref='CENTRAL-2026-09'").fetchone()[0] == 1
        c.close()
        # the list's own export carries no month: the list refreshes, no note is possible
        exp = client.get("/api/bom-center/central.xlsx").content
        r = client.post("/api/bom-center/central/month-import?dry_run=true", files={"file": ("central.xlsx", exp)})
        assert r.status_code == 200, r.text
        assert r.json()["deliveries"]["branches"] == [] and r.json()["deliveries"]["note"]
        r = client.post(f"/api/bom-center/central/month-import?dry_run=false&branch_ids={bid}",
                        files={"file": ("central.xlsx", exp)}, headers={"X-Upload-Token": TOKEN})
        assert r.status_code == 400, r.text
    finally:
        _restore(main.DB_PATH, snap, bid, old_name)


def test_sales_support_may_import_a_count_file_but_not_count_on_screen():
    ss = auth.ROLE_MODULES["salessupport"]
    assert auth.path_allowed("POST", "/api/bom-center/stock/count-import", ss)
    assert not auth.path_allowed("POST", "/api/bom-center/stock/count", ss)
    assert auth.path_allowed("POST", "/api/bom-center/stock/count-import", auth.ROLE_MODULES["branch"])
    assert auth.path_allowed("GET", "/api/bom-center/stock/count-template.xlsx", ss)
