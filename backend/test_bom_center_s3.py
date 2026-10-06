"""BoM Center — Sprint S3: review and release.

The promises:
  * every one of the checks fires on the problem it names, and only a block can
    stop a release; the ±50 % block can be accepted with a reason, nothing else can;
  * a released version is the one in force for its month and later months, an
    earlier release of the same month steps aside, and rolling back restores
    the version before it;
  * the person who releases cannot be the one who acknowledges;
  * the comparison turns cost changes into money per branch and food cost % per
    guest tier, from what each tier's guests actually ate.
"""
import sqlite3

import pytest

import auth
from bom_center import release as RL
from bom_center import recipes as R
from bom_center import snapshot_v0 as V0
from test_bom_center_s2 import _block, _db


@pytest.fixture
def con(tmp_path):
    c = _db(tmp_path)
    # today's engine: the crab-stick sashimi costs ฿5 flat, the teriyaki chicken has no cost
    c.execute("INSERT INTO sku_cost(sku_id, unit_cost) VALUES(10, 5.0)")
    c.commit()
    V0.build_v0(c)
    c.commit()
    yield c
    c.close()


def _draft(con, blocks, label="2026-11 draft"):
    rid = R.get_or_create_draft(con, label, "2026-11", "2026-07", "analyst")
    R.import_cost_rw(con, {"blocks": blocks}, rid, "analyst")
    return rid


def _codes(summary):
    return {r["check_code"] for r in summary["rows"]}


def test_costs_in_force_starts_from_v0(con):
    f = RL.costs_in_force(con, "2026-11")
    assert f["OTHBFSSM00020"] == (5.0, "v0")


def test_checks_fire_and_block(con):
    bad = [
        {"seq": 1, "code": "13-01-009", "ing": "ปูอัด", "full_price": 1500, "full_qty": 10000, "loss": 0,
         "qty": 2000, "unit": "กรัม", "cost": 300, "raw_cost": "300"},          # 2 kg a serving: C12, and ±50 %: C05
        {"seq": 2, "code": "ZZ-NOPE", "ing": "ไม่รู้จัก", "full_price": 1, "full_qty": 1, "loss": 0,
         "qty": 1, "unit": "กรัม", "cost": 1, "raw_cost": "1"}]                    # not in the master: C04
    rid = _draft(con, [_block(459, "ซาชิมิปูอัด", ings=bad, total=1.0)])
    con.execute("DELETE FROM bc_ingredient WHERE code='ZZ-NOPE'")                # write_recipe registered it; drop it again
    s = RL.run_checks(con, rid)
    codes = _codes(s)
    assert {"C04", "C12"} <= codes
    # an uncostable recipe has no trustworthy total: change % and tie-out wait until it is fixed
    assert not ({"C05", "C08"} & codes)
    assert not s["can_release"]
    with pytest.raises(ValueError):
        RL.do_release(con, rid, "analyst")
    R.save_lines(con, rid, "OTHBFSSM00020", R.recipe_lines(con, rid, "OTHBFSSM00020")[:1], "analyst")
    codes = _codes(RL.run_checks(con, rid))
    assert {"C05", "C08", "C12"} <= codes and "C04" not in codes


def test_missing_price_unit_and_zero_lines(con):
    ings = [
        {"seq": 1, "code": "V-007", "ing": "หัวไชเท้า", "full_price": 40, "full_qty": 1000, "loss": 0,
         "qty": 10, "unit": "ลิตร", "cost": 0.4, "raw_cost": "0.4"},             # grams vs litres: C03
        {"seq": 2, "code": "13-01-009", "ing": "ปูอัด", "full_price": 1500, "full_qty": 10000, "loss": 0,
         "qty": 45, "unit": "กรัม", "cost": 6.75, "raw_cost": "6.75"}]
    rid = _draft(con, [_block(459, "ซาชิมิปูอัด", ings=ings)])
    con.execute("INSERT INTO bc_ingredient(code, name_th, kind, base_unit) VALUES('NOPRICE','ไม่มีราคา','raw','กรัม')")
    con.execute("INSERT INTO bc_ingredient(code, name_th, kind, base_unit) VALUES('FREE','ราคา 0','raw','กรัม')")
    con.execute("INSERT INTO bc_ingredient_price(ingredient_code, month, price_per_base, source) VALUES('FREE','2026-07',0,'manual')")
    lines = R.recipe_lines(con, rid, "OTHBFSSM00020") + [
        {"ingredient_code": "NOPRICE", "ingredient_name": "x", "qty": 1, "unit": "กรัม"},
        {"ingredient_code": "FREE", "ingredient_name": "y", "qty": 1, "unit": "กรัม"}]
    R.save_lines(con, rid, "OTHBFSSM00020", lines, "analyst")
    assert {"C01", "C02", "C03"} <= _codes(RL.run_checks(con, rid))


def test_accept_release_force_retire_and_ack(con):
    rid = _draft(con, [_block(459, "ซาชิมิปูอัด")])                               # ฿7.18 vs ฿5 today: +44 % -> C06 only
    s = RL.run_checks(con, rid)
    assert "C06" in _codes(s) and s["can_release"]
    R.save_lines(con, rid, "OTHBFSSM00020", [{"ingredient_code": "13-01-009", "qty": 60, "unit": "กรัม"}], "analyst")
    s = RL.run_checks(con, rid)                                                    # ฿9 vs ฿5: +80 % -> C05 blocks
    c05 = [r for r in s["rows"] if r["check_code"] == "C05"]
    assert c05 and not s["can_release"]
    with pytest.raises(ValueError):
        RL.accept(con, rid, "C05", c05[0]["ref"], "", "analyst")                  # a reason is required
    with pytest.raises(ValueError):
        RL.accept(con, rid, "C04", "x", "because", "analyst")                     # only C05 is acceptable
    RL.accept(con, rid, "C05", c05[0]["ref"], "ครัวยืนยันเพิ่มปูอัดเป็น 60 กรัม", "analyst")
    assert RL.run_checks(con, rid)["can_release"]                                  # the acceptance survives a re-run
    out = RL.do_release(con, rid, "analyst")
    assert out["released"]
    assert RL.costs_in_force(con, "2026-11")["OTHBFSSM00020"][0] == pytest.approx(9.0)
    assert RL.costs_in_force(con, "2026-10")["OTHBFSSM00020"][0] == 5.0            # the month before keeps v0
    with pytest.raises(ValueError):
        R.save_lines(con, rid, "OTHBFSSM00020", [], "analyst")                     # released = read-only
    with pytest.raises(ValueError):
        RL.acknowledge(con, rid, "analyst")                                        # not the person who released
    RL.acknowledge(con, rid, "boss", "ok")
    assert R.release_row(con, rid)["acknowledged_by"] == "boss"
    # next month: a copy, re-released, makes the first step aside for its own month only
    rid2 = RL.new_draft_from(con, rid, "2026-12 draft", "2026-12", "2026-07", "analyst")
    assert R.cost_recipe(con, rid2, "OTHBFSSM00020")["total"] == pytest.approx(9.0)
    R.save_lines(con, rid2, "OTHBFSSM00020", [{"ingredient_code": "13-01-009", "qty": 50, "unit": "กรัม"}], "analyst")
    RL.do_release(con, rid2, "analyst")
    assert RL.costs_in_force(con, "2026-12")["OTHBFSSM00020"][0] == pytest.approx(7.5)
    assert RL.costs_in_force(con, "2026-11")["OTHBFSSM00020"][0] == pytest.approx(9.0)
    with pytest.raises(ValueError):
        RL.retire(con, rid2, "boss", "")
    RL.retire(con, rid2, "boss", "สูตรผิด")
    assert RL.costs_in_force(con, "2026-12")["OTHBFSSM00020"][0] == pytest.approx(9.0)
    # a second release for the SAME month replaces the first
    rid3 = RL.new_draft_from(con, rid, "2026-11 rev2", "2026-11", "2026-07", "analyst")
    R.save_lines(con, rid3, "OTHBFSSM00020", [{"ingredient_code": "13-01-009", "qty": 40, "unit": "กรัม"}], "analyst")
    for r in RL.run_checks(con, rid3)["rows"]:
        if r["check_code"] == "C05":
            RL.accept(con, rid3, "C05", r["ref"], "ok", "analyst")
    RL.do_release(con, rid3, "analyst")
    assert R.release_row(con, rid)["status"] == "retired"
    assert RL.costs_in_force(con, "2026-11")["OTHBFSSM00020"][0] == pytest.approx(6.0)


def test_c07_and_c11(con):
    rid = _draft(con, [_block(459, "ซาชิมิปูอัด")])
    con.execute("INSERT INTO sales_sku_monthly(branch_id, period_month, sku_id, qty, subtotal_ex_vat, tax_total,"
                " total_inc_vat, source) VALUES(1, ?, 11, 40, 0, 0, 0, 'Easy')", (RL.last_full_month(con),))
    con.execute("INSERT INTO sales_sku_monthly(branch_id, period_month, sku_id, qty, subtotal_ex_vat, tax_total,"
                " total_inc_vat, source) VALUES(1, ?, 10, 10, 75, 0, 0, 'Easy')", (RL.last_full_month(con),))
    codes = _codes(RL.run_checks(con, rid))
    assert "C07" in codes                                     # teriyaki chicken sold, no recipe here
    assert "C11" in codes                                     # ฿7.18 cost vs ฿7.50 average price


def test_compare_branch_and_guest_tier(con):
    month = RL.last_full_month(con)
    con.execute("INSERT INTO sales_sku_monthly(branch_id, period_month, sku_id, qty, subtotal_ex_vat, tax_total,"
                " total_inc_vat, source) VALUES(1, ?, 10, 100, 0, 0, 0, 'Easy')", (month,))
    con.executescript("""
      CREATE TABLE menu_fact_day(branch_id INT, day TEXT, tier TEXT, ent TEXT, bills INT, pax INT, qty REAL,
                                 ent_cogs REAL, ent_hc REAL, cogs REAL, rev REAL, hc REAL);
      CREATE TABLE menu_fact_total(branch_id INT, day TEXT, tier TEXT, bills INT, pax INT, cogs REAL, rev REAL, hc REAL);""")
    con.execute("INSERT INTO menu_fact_day VALUES(1, ?, '459', 's:10', 10, 40, 100, 500, 0, 0, 0, 0)", (f"{month}-05",))
    con.execute("INSERT INTO menu_fact_total VALUES(1, ?, '459', 10, 40, 4000, 10000, 0)", (f"{month}-05",))
    rid = _draft(con, [_block(459, "ซาชิมิปูอัด")])
    d = RL.compare(con, rid, month)
    dish = d["dishes"][0]
    assert dish["old"] == 5.0 and dish["new"] == pytest.approx(7.1756, abs=1e-3)
    assert d["branches"][0]["delta"] == pytest.approx(100 * (dish["new"] - 5.0), abs=0.01)
    t = d["tiers"][0]
    assert t["tier"] == "459" and t["food_pct_old"] == 40.0
    assert t["food_pct_new"] == pytest.approx(100 * (4000 + 100 * (dish["new"] - 5.0)) / 10000, abs=0.05)


def test_release_paths_carry_their_own_permission():
    a = {"bom.center"}
    assert auth.path_allowed("POST", "/api/bom-center/release-act/check", a)
    assert not auth.path_allowed("POST", "/api/bom-center/release-act/release", a)
    assert auth.path_allowed("POST", "/api/bom-center/release-act/release", {"bom.release"})
    assert not auth.path_allowed("POST", "/api/bom-center/release-act/acknowledge", {"bom.release", "bom.center"})
    assert auth.path_allowed("POST", "/api/bom-center/release-act/acknowledge", {"bom.audit"})
    assert auth.path_allowed("POST", "/api/bom-center/release-act/retire", {"bom.audit"})
    assert "bom.release" in auth.ROLE_MODULES["analyst"] and "bom.audit" not in auth.ROLE_MODULES["analyst"]


def test_endpoints_release_flow(client):
    import main
    c = sqlite3.connect(str(main.DB_PATH))
    from bom_center import schema as S
    S.ensure(c, commit=True)
    tables = [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'bc%'")]
    snap = {t: c.execute(f"SELECT * FROM {t}").fetchall() for t in tables}
    c.execute("INSERT OR IGNORE INTO skus(sku_id, sku_code, description, category_id, unit, business_type, active)"
              " VALUES(10, 'OTHBFSSM00020', '(B) ซาชิมิปูอัด', 3, 'plate', 'buffet', 1)")
    c.execute("CREATE TABLE IF NOT EXISTS sku_tier(sku_id INTEGER PRIMARY KEY, tier TEXT)")
    c.execute("INSERT OR REPLACE INTO sku_tier VALUES(10, '459')")
    c.execute("INSERT OR REPLACE INTO bc_ingredient(code, name_th, kind, base_unit) VALUES('13-01-009','ปูอัด','raw','กรัม')")
    c.execute("INSERT OR REPLACE INTO bc_ingredient_price(ingredient_code, month, price_per_base, source)"
              " VALUES('13-01-009','2026-07',0.15,'list_ref')")
    c.execute("INSERT INTO bc_release(label, kind, effective_month, status, scope, price_month)"
              " VALUES('EP', 'monthly', '2026-11', 'draft', 'buffet', '2026-07')")
    rid = c.execute("SELECT release_id FROM bc_release WHERE label='EP'").fetchone()[0]
    c.execute("INSERT INTO bc_recipe(release_id, sku_code, kind) VALUES(?, 'OTHBFSSM00020', 'recipe')", (rid,))
    c.execute("INSERT INTO bc_recipe_line(release_id, sku_code, seq, ingredient_code, qty, unit)"
              " VALUES(?, 'OTHBFSSM00020', 1, '13-01-009', 45, 'กรัม')", (rid,))
    c.commit()
    c.close()
    try:
        s = client.post(f"/api/bom-center/release-act/check?release_id={rid}").json()
        assert s["can_release"], s
        assert client.get(f"/api/bom-center/release-checks?release_id={rid}").json()["catalog"]
        cmp_ = client.get(f"/api/bom-center/release-compare?release_id={rid}").json()
        assert cmp_["dishes"][0]["new"] == pytest.approx(6.75)
        r = client.post(f"/api/bom-center/release-act/release?release_id={rid}")
        assert r.status_code == 200 and r.json()["released"]
        assert client.post(f"/api/bom-center/release-act/release?release_id={rid}").status_code == 400
        assert client.post("/api/bom-center/release-act/acknowledge", json={"release_id": rid, "note": "ok"}).status_code == 200
        nd = client.post("/api/bom-center/release-act/new-draft",
                         json={"from_release_id": rid, "effective_month": "2026-12", "price_month": "2026-07"}).json()
        assert nd["ok"]
        assert client.post("/api/bom-center/release-act/retire", json={"release_id": rid, "note": ""}).status_code == 400
        assert client.post("/api/bom-center/release-act/retire", json={"release_id": rid, "note": "test"}).status_code == 200
        hist = client.get(f"/api/bom-center/release-history?release_id={rid}").json()["rows"]
        assert {h["action"] for h in hist} >= {"release", "acknowledge", "retire"}
    finally:
        c = sqlite3.connect(str(main.DB_PATH))
        for t in [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'bc%'")]:
            c.execute(f"DELETE FROM {t}")
            rows = snap.get(t) or []
            if rows:
                c.executemany(f"INSERT INTO {t} VALUES({','.join('?' * len(rows[0]))})", rows)
        c.execute("DELETE FROM sku_tier WHERE sku_id=10")
        c.execute("DELETE FROM skus WHERE sku_id=10")
        c.commit()
        c.close()
