"""BoM Center — Sprint S4a: the cut-over.

Owner decision 2026-10-06: the center's costs apply from 1 September 2026 (the
Grand Menu month) and never to an earlier month. The promises:
  * no release -> every number in the app unchanged (the golden suite proves it);
  * a released version changes COGS for its SKUs from its effective month on,
    month by month, and NOTHING before September 2026;
  * a version for a month already under way needs a recorded backdate reason;
  * rolling back restores the old numbers;
  * the PC sync receives the same dated costs (by sku_code) and never fails on them.
"""
import json
import sqlite3
from datetime import date

import pytest

import rules
from bom_center import dated as D
from bom_center import recipes as R
from bom_center import release as RL
from bom_center import snapshot_v0 as V0
from test_bom_center_s2 import _block, _db


@pytest.fixture
def con(tmp_path):
    c = _db(tmp_path)
    c.execute("INSERT INTO sku_cost(sku_id, unit_cost) VALUES(10, 5.0)")
    c.commit()
    V0.build_v0(c)
    c.commit()
    yield c
    c.close()


def _released(con, eff="2026-11", qty=45, today=date(2026, 10, 6), reason=None):
    rid = R.get_or_create_draft(con, f"{eff} v", eff, "2026-07", "analyst")
    R.import_cost_rw(con, {"blocks": [_block(459, "ซาชิมิปูอัด")]}, rid, "analyst")
    R.save_lines(con, rid, "OTHBFSSM00020", [{"ingredient_code": "13-01-009", "qty": qty, "unit": "กรัม"}], "analyst")
    for r in RL.run_checks(con, rid)["rows"]:
        if r["check_code"] == "C05":
            RL.accept(con, rid, "C05", r["ref"], "ok", "analyst")
    RL.do_release(con, rid, "analyst", backdate_reason=reason, today=today)
    return rid


def _cogs(con, month):
    return con.execute(f"SELECT SUM({rules.cogs_expr('ssm')}) FROM sales_sku_monthly ssm"
                       " WHERE ssm.sku_id=10 AND ssm.period_month=?", (month,)).fetchone()[0]


def test_drafts_before_the_cutover_are_refused(con):
    with pytest.raises(ValueError):
        R.get_or_create_draft(con, "old", "2026-08", "2026-07", "analyst")
    rid = _released(con)
    with pytest.raises(ValueError):
        RL.new_draft_from(con, rid, "older", "2026-06", "2026-07", "analyst")


def test_backdated_release_needs_a_reason(con):
    with pytest.raises(ValueError, match="ย้อนหลัง"):
        _released(con, eff="2026-09", reason=None)
    rid = _released(con, eff="2026-09", reason="เริ่มเมนูใหม่ 1 ก.ย. — ต้นทุนตามสูตรจริง")
    rel = R.release_row(con, rid)
    assert rel["status"] == "released"
    audit = json.loads(con.execute("SELECT new_value FROM bc_audit WHERE action='release'").fetchone()[0])
    assert audit["backdated"] is True


def test_dated_table_and_cogs_follow_the_release_month_by_month(con):
    for m in ("2026-08", "2026-09", "2026-10", "2026-11", "2026-12"):
        con.execute("INSERT INTO sales_sku_monthly(branch_id, period_month, sku_id, qty, subtotal_ex_vat, tax_total,"
                    " total_inc_vat, source) VALUES(1, ?, 10, 10, 0, 0, 0, 'Easy')", (m,))
    before = {m: _cogs(con, m) for m in ("2026-08", "2026-09", "2026-11")}
    assert before == {"2026-08": 50.0, "2026-09": 50.0, "2026-11": 50.0}     # ฿5 flat everywhere
    sep = _released(con, eff="2026-09", qty=45, reason="first release")       # ฿6.75 + radish line? no: one line, ฿6.75
    nov = _released(con, eff="2026-11", qty=60)                                # ฿9.00 from November
    months = {m for (m,) in con.execute("SELECT DISTINCT month FROM bc_sku_cost_month")}
    assert min(months) == D.CUTOVER_MONTH == "2026-09"                         # nothing before the Grand Menu
    assert _cogs(con, "2026-08") == pytest.approx(50.0)                       # August: old engine, untouched
    assert _cogs(con, "2026-09") == pytest.approx(67.5)
    assert _cogs(con, "2026-10") == pytest.approx(67.5)                       # September's version still in force
    assert _cogs(con, "2026-11") == pytest.approx(90.0)
    assert _cogs(con, "2026-12") == pytest.approx(90.0)                       # next month: the newest version
    RL.retire(con, nov, "boss", "สูตรผิด")
    assert _cogs(con, "2026-11") == pytest.approx(67.5)                       # rolled back to September's
    RL.retire(con, sep, "boss", "ย้อนทั้งหมด")
    assert _cogs(con, "2026-09") == pytest.approx(50.0)                       # and to the old engine
    assert con.execute("SELECT COUNT(*) FROM bc_sku_cost_month").fetchone()[0] == 0


def test_rebuild_runs_to_next_month_and_skips_tier_codes(con):
    _released(con, eff="2026-09", reason="first")
    out = D.rebuild(con, today=date(2026, 12, 3))
    assert out["months"][0] == "2026-09" and out["months"][-1] == "2027-01"
    assert not con.execute("SELECT 1 FROM bc_sku_cost_month m JOIN skus s USING(sku_id)"
                           " WHERE s.sku_code LIKE 'BUFFET%'").fetchone()


def test_pull_from_live_maps_by_code_and_never_raises(tmp_path, monkeypatch):
    sq = sqlite3.connect(str(tmp_path / "staging.db"))
    sq.executescript("CREATE TABLE skus(sku_id INTEGER PRIMARY KEY, sku_code TEXT);"
                     "INSERT INTO skus VALUES (77, 'OTHBFSSM00020');")
    monkeypatch.setenv("OSHINEI_SEED_TOKEN", "admin:tok")   # the machine endpoints take the owner ("admin") token only
    seen = []

    class _Resp:
        def __init__(self, body): self.body = body
        def read(self): return self.body
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def fake(req, timeout=0):
        seen.append(req)
        return _Resp(json.dumps({"rows": [["OTHBFSSM00020", "2026-09", 6.75, 3, "t"],
                                          ["NOT-HERE", "2026-09", 1.0, 3, "t"]]}).encode())
    monkeypatch.setattr("urllib.request.urlopen", fake)
    assert D.pull_from_live(sq, api="http://x") == 2
    assert sq.execute("SELECT sku_id, month, unit_cost FROM bc_sku_cost_month").fetchall() == [(77, "2026-09", 6.75)]
    assert "token" not in seen[0].full_url and dict(seen[0].header_items())["X-seed-token"] == "tok"

    def boom(req, timeout=0):
        raise OSError("offline")
    monkeypatch.setattr("urllib.request.urlopen", boom)
    assert D.pull_from_live(sq, api="http://x") == 0                          # logged, not raised
    assert sq.execute("SELECT COUNT(*) FROM bc_sku_cost_month").fetchone()[0] == 1   # and nothing wiped


def test_admin_export_is_token_gated(client, monkeypatch):
    H = {"X-Seed-Token": "tok"}
    monkeypatch.delenv("OSHINEI_SEED_TOKEN", raising=False)
    monkeypatch.delenv("OSHINEI_WRITE_TOKEN", raising=False)
    monkeypatch.setenv("OSHINEI_SEED_TOKEN", "admin:tok")   # the machine endpoints take the owner ("admin") token only
    assert client.get("/api/_admin/bc-sku-cost-month", headers={"X-Seed-Token": "wrong"}).status_code == 404
    r = client.get("/api/_admin/bc-sku-cost-month", headers=H)
    assert r.status_code == 200 and r.json()["columns"][0] == "sku_code"


def test_api_release_changes_september_and_not_june(client):
    """Through the app: the fixture's June sales keep their COGS; a September sale
    follows the release; a full cache flush makes the reports see it at once."""
    import main
    from bom_center import schema as S
    c = sqlite3.connect(str(main.DB_PATH))
    S.ensure(c, commit=True)
    tables = [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'bc%'")]
    snap = {t: c.execute(f"SELECT * FROM {t}").fetchall() for t in tables}
    c.execute("INSERT INTO sales_sku_monthly(branch_id, period_month, sku_id, qty, subtotal_ex_vat, tax_total,"
              " total_inc_vat, source) VALUES(1, '2026-09', 2, 10, 0, 0, 0, 'Easy')")
    c.execute("INSERT OR REPLACE INTO bc_ingredient(code, name_th, kind, base_unit) VALUES('ING-T','t','raw','กรัม')")
    c.execute("INSERT OR REPLACE INTO bc_ingredient_price(ingredient_code, month, price_per_base, source)"
              " VALUES('ING-T', '2026-07', 0.5, 'list_ref')")
    c.execute("INSERT INTO bc_release(label, kind, effective_month, status, scope, price_month)"
              " VALUES('S4', 'monthly', '2026-09', 'draft', 'buffet', '2026-07')")
    rid = c.execute("SELECT release_id FROM bc_release WHERE label='S4'").fetchone()[0]
    c.execute("INSERT INTO bc_recipe(release_id, sku_code, kind) VALUES(?, 'B0001', 'recipe')", (rid,))
    c.execute("INSERT INTO bc_recipe_line(release_id, sku_code, seq, ingredient_code, qty, unit)"
              " VALUES(?, 'B0001', 1, 'ING-T', 20, 'กรัม')", (rid,))               # ฿10 vs ฿8 today
    c.commit()
    c.close()
    q = (f"SELECT period_month, SUM({rules.cogs_expr('ssm')}) FROM sales_sku_monthly ssm WHERE sku_id=2"
         " GROUP BY period_month")
    try:
        con = sqlite3.connect(str(main.DB_PATH))
        before = dict(con.execute(q).fetchall())
        con.close()
        assert client.post(f"/api/bom-center/release-act/release?release_id={rid}", json={}).status_code == 400
        r = client.post(f"/api/bom-center/release-act/release?release_id={rid}",
                        json={"backdate_reason": "เริ่มเมนูใหม่"})
        assert r.status_code == 200, r.text
        assert r.json()["backdated"] is True
        con = sqlite3.connect(str(main.DB_PATH))
        after = dict(con.execute(q).fetchall())
        con.close()
        for m in ("2026-04", "2026-05", "2026-06"):
            assert after[m] == before[m]                                        # before the cut-over: untouched
        assert before["2026-09"] == pytest.approx(80.0) and after["2026-09"] == pytest.approx(100.0)
    finally:
        c = sqlite3.connect(str(main.DB_PATH))
        c.execute("DELETE FROM sales_sku_monthly WHERE period_month='2026-09'")
        for t in [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'bc%'")]:
            c.execute(f"DELETE FROM {t}")
            rows = snap.get(t) or []
            if rows:
                c.executemany(f"INSERT INTO {t} VALUES({','.join('?' * len(rows[0]))})", rows)
        c.commit()
        c.close()
        main.invalidate_cache()
