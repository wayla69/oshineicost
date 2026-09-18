"""Confirming a cost in the app — and keeping it.

sku_cost and bom_ingredients arrive with every daily swap from the sync's base
DB, so a cost typed into the live site used to last exactly one day (that is how
the durian correction was lost in August 2026). A confirmation is therefore
stored as an INTENT in cost_override, which the swap preserves, and re-applied by
cost_sources.apply_all — on Fly, and in the staging DB the fact tables are built
from. These tests pin each link of that chain.
"""
import json
import sqlite3

import cost_sources as cs


def _db():
    con = sqlite3.connect(":memory:")
    con.executescript("""
      CREATE TABLE skus(sku_id INTEGER PRIMARY KEY, sku_code TEXT, description TEXT);
      CREATE TABLE sku_cost(sku_id INTEGER PRIMARY KEY, unit_cost REAL, effective_from TEXT, updated_at TEXT);
      CREATE TABLE bom_ingredients(sku_id INT, seq INT, ingredient_code TEXT, ingredient_name TEXT,
        qty_per_serving REAL, unit TEXT, price_per_unit REAL, ingredient_cost REAL,
        ingredient_type TEXT, source_file TEXT, PRIMARY KEY(sku_id, seq));
      INSERT INTO skus VALUES(1, 'YUKE', 'ยูเกะญี่ปุ่น');
      INSERT INTO skus VALUES(2, 'PEPSI', 'Pepsi');
    """)
    con.execute(cs.OVERRIDE_DDL)
    con.execute(cs.PROVENANCE_DDL)
    return con


def _confirm(con, code, kind, unit_cost=None, lines=None):
    con.execute("INSERT OR REPLACE INTO cost_override VALUES(?,?,?,?,?,?,?)",
                (code, kind, unit_cost, json.dumps(lines, ensure_ascii=False) if lines else None,
                 "", "chef1", "2026-09-18T10:00:00"))


def test_a_confirmed_flat_cost_pins_sku_cost_and_is_tagged():
    con = _db()
    _confirm(con, "PEPSI", "cost", unit_cost=9.5)
    assert cs.apply_overrides(con)["cost"] == 1
    assert con.execute("SELECT unit_cost FROM sku_cost WHERE sku_id=2").fetchone()[0] == 9.5
    src, val = con.execute("SELECT source, value FROM cost_provenance WHERE sku_id=2").fetchone()
    assert (src, val) == ("confirmed", 9.5)


def test_a_confirmed_recipe_replaces_the_proposed_one_and_clears_a_stale_estimate():
    """A non-zero sku_cost OUTRANKS a recipe in the engine — so an estimate left
    behind would hide the very recipe the kitchen just confirmed."""
    con = _db()
    con.execute("INSERT INTO sku_cost VALUES(1, 34.30, '2026-09-01', 'x')")
    cs._tag(con, 1, "estimate", "peer", 34.30, "", "now")
    con.execute("INSERT INTO bom_ingredients VALUES(1,1,'B','beef',50,'ก',0.564,28.2,'protein','recipes_2026-09.csv')")
    _confirm(con, "YUKE", "recipe", lines=[
        {"code": "13-03-311", "name": "เนื้อ", "qty": 40, "unit": "กรัม", "price": 0.564},
        {"code": "F-004", "name": "ไข่แดง", "qty": 1, "unit": "ฟอง", "price": 3.6}])
    assert cs.apply_overrides(con)["recipe"] == 1
    rows = con.execute("SELECT qty_per_serving, source_file FROM bom_ingredients WHERE sku_id=1 ORDER BY seq").fetchall()
    assert rows == [(40.0, cs.IN_APP_SOURCE), (1.0, cs.IN_APP_SOURCE)]
    assert con.execute("SELECT COUNT(*) FROM sku_cost WHERE sku_id=1").fetchone()[0] == 0
    assert round(con.execute("SELECT value FROM cost_provenance WHERE sku_id=1").fetchone()[0], 2) == 26.16


def test_the_file_loaders_never_touch_a_confirmed_dish(tmp_path):
    con = _db()
    _confirm(con, "PEPSI", "cost", unit_cost=9.5)
    cs.apply_overrides(con)
    est = tmp_path / "e.csv"
    est.write_text("sku_code,unit_cost,basis,note\nPEPSI,13.59,requisition,x\n", encoding="utf-8")
    out = cs.load_estimated_costs(con, est)
    assert out["already_costed"] == 1 and out["applied"] == 0 and out["updated"] == 0
    assert con.execute("SELECT unit_cost FROM sku_cost WHERE sku_id=2").fetchone()[0] == 9.5
    assert con.execute("SELECT source FROM cost_provenance WHERE sku_id=2").fetchone()[0] == "confirmed"


def test_a_confirmation_survives_the_daily_swap(tmp_path):
    """The whole reason cost_override exists. 'Swap' = the live DB is replaced by
    a fresh staged one whose cost tables know nothing of the confirmation, with
    only the preserved tables carried over — then the app starts and re-applies."""
    import core
    assert "cost_override" in core._PRESERVED_EXACT
    live, staged = tmp_path / "live.db", tmp_path / "staged.db"
    for path in (live, staged):
        c = sqlite3.connect(str(path))
        c.executescript("""
          CREATE TABLE skus(sku_id INTEGER PRIMARY KEY, sku_code TEXT, description TEXT);
          CREATE TABLE sku_cost(sku_id INTEGER PRIMARY KEY, unit_cost REAL, effective_from TEXT, updated_at TEXT);
          CREATE TABLE bom_ingredients(sku_id INT, seq INT, ingredient_code TEXT, ingredient_name TEXT,
            qty_per_serving REAL, unit TEXT, price_per_unit REAL, ingredient_cost REAL,
            ingredient_type TEXT, source_file TEXT, PRIMARY KEY(sku_id, seq));
          INSERT INTO skus VALUES(2, 'PEPSI', 'Pepsi');""")
        c.commit(); c.close()
    c = sqlite3.connect(str(live))
    c.execute(cs.OVERRIDE_DDL)
    _confirm(c, "PEPSI", "cost", unit_cost=9.5)
    c.commit(); c.close()

    core._carry_forward_preserved(str(live), str(staged))       # what the swap does
    c = sqlite3.connect(str(staged))
    assert c.execute("SELECT COUNT(*) FROM sku_cost").fetchone()[0] == 0     # the cost itself is gone…
    cs.apply_overrides(c)                                                    # …until startup re-applies the intent
    assert c.execute("SELECT unit_cost FROM sku_cost WHERE sku_id=2").fetchone()[0] == 9.5


def test_withdrawing_a_confirmation_hands_the_dish_back_to_the_files(tmp_path):
    con = _db()
    _confirm(con, "PEPSI", "cost", unit_cost=9.5)
    cs.apply_overrides(con)
    assert cs.remove_override(con, "PEPSI") is True
    assert con.execute("SELECT COUNT(*) FROM sku_cost WHERE sku_id=2").fetchone()[0] == 0
    est = tmp_path / "e.csv"
    est.write_text("sku_code,unit_cost,basis,note\nPEPSI,13.59,requisition,x\n", encoding="utf-8")
    assert cs.load_estimated_costs(con, est)["applied"] == 1
    assert cs.remove_override(con, "PEPSI") is False            # nothing left to withdraw


def test_the_sync_mirrors_live_confirmations_and_never_fails_the_build(monkeypatch):
    import build_menu_fact as b
    con = _db()
    _confirm(con, "OLD", "cost", unit_cost=1.0)                 # a confirmation since withdrawn on the site

    class _Resp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self):
            return json.dumps({"columns": ["sku_code", "kind", "unit_cost", "lines", "note",
                                           "confirmed_by", "confirmed_at", "a_future_column"],
                               "rows": [["PEPSI", "cost", 9.5, None, "", "chef1", "2026-09-18", "x"]]}).encode()
    monkeypatch.setenv("OSHINEI_SEED_TOKEN", "backup:tok")
    seen = []
    monkeypatch.setattr("urllib.request.urlopen", lambda url, timeout=0: (seen.append(url), _Resp())[1])
    assert b._pull_confirmed_costs(con, cs) == 1
    assert "token=tok" in seen[0] and "cost-overrides" in seen[0]
    assert [r[0] for r in con.execute("SELECT sku_code FROM cost_override")] == ["PEPSI"]   # mirrored, OLD gone

    def boom(url, timeout=0): raise OSError("offline")
    monkeypatch.setattr("urllib.request.urlopen", boom)
    assert b._pull_confirmed_costs(con, cs) == 0                 # logged, not raised
    assert [r[0] for r in con.execute("SELECT sku_code FROM cost_override")] == ["PEPSI"]   # and nothing wiped


# ─── the endpoints ──────────────────────────────────────────────────────────

def test_confirm_review_and_withdraw_through_the_api(client):
    assert client.post("/api/cost/confirm", json={"sku_code": "B0001", "kind": "bogus"}).status_code == 400
    assert client.post("/api/cost/confirm", json={"sku_code": "B0001", "kind": "cost", "unit_cost": 0}).status_code == 400
    assert client.post("/api/cost/confirm", json={"sku_code": "NOPE", "kind": "cost", "unit_cost": 5}).status_code == 404
    assert client.post("/api/cost/confirm", json={"sku_code": "B0001", "kind": "recipe", "lines": []}).status_code == 400

    r = client.post("/api/cost/confirm", json={"sku_code": "B0001", "kind": "recipe", "note": "ชั่งจริง", "lines": [
        {"code": "02-01-001", "name": "แซลมอน", "qty": 18, "unit": "กรัม", "price": 0.5967},
        {"code": "V-024", "name": "หัวไชเท้า", "qty": 10, "unit": "กรัม", "price": 0.0316}]})
    assert r.status_code == 200 and r.json()["cost"] == round(18 * 0.5967 + 10 * 0.0316, 4)

    d = client.get("/api/cost/review").json()
    item = next(i for i in d["items"] if i["sku_code"] == "B0001")
    assert item["source"] == "confirmed" and item["source_label"] == "ยืนยันแล้ว"
    assert [ln["qty"] for ln in item["lines"]] == [18, 10]
    assert "ชั่งจริง" in item["note"] and d["summary"]["confirmed"] >= 1

    assert client.delete("/api/cost/confirm/B0001").status_code == 200
    assert client.delete("/api/cost/confirm/B0001").status_code == 404
    assert all(i["sku_code"] != "B0001" for i in client.get("/api/cost/review").json()["items"])


def test_the_sync_endpoint_is_token_gated(client, monkeypatch):
    monkeypatch.delenv("OSHINEI_SEED_TOKEN", raising=False)
    assert client.get("/api/_admin/cost-overrides", params={"token": "x"}).status_code == 404
    monkeypatch.setenv("OSHINEI_SEED_TOKEN", "backup:tok")
    assert client.get("/api/_admin/cost-overrides", params={"token": "wrong"}).status_code == 404
    r = client.get("/api/_admin/cost-overrides", params={"token": "tok"})
    assert r.status_code == 200 and r.json()["columns"][:2] == ["sku_code", "kind"]
