# -*- coding: utf-8 -*-
"""The release pane in fewer steps (BoM Center UX overhaul, PR-10).

The promises:
  * a draft's checks can be looked at afresh without saving them (and without a
    token): the preview is computed, shown, and rolled back;
  * accepting a finding that was only previewed saves the run first, then accepts;
  * one reason accepts a whole code's open findings — only for a code that may be
    accepted, and never without a reason;
  * the releases list says what each version waits for (release / acknowledge).
"""
import sqlite3

import pytest

from bom_center import recipes as R
from bom_center import release as RL
from test_bom_center_s2 import _block
from test_bom_center_s3 import _draft, con  # noqa: F401  (v0 in force; crab-stick sashimi ฿5 today)


def _c05_draft(c):
    """A draft whose crab-stick sashimi costs +80 % against today: C05 fires (acceptable)."""
    rid = _draft(c, [_block(459, "ซาชิมิปูอัด")])
    R.save_lines(c, rid, "OTHBFSSM00020", [{"ingredient_code": "13-01-009", "qty": 60, "unit": "กรัม"}], "analyst")
    c.commit()
    return rid


def _open(c, rid, code):
    return c.execute("SELECT COUNT(*) FROM bc_release_check WHERE release_id=? AND check_code=? AND resolved_by IS NULL",
                     (rid, code)).fetchone()[0]


def test_a_preview_is_discarded_and_accept_heals_it(con):
    c = con
    rid = _c05_draft(c)
    s = RL.run_checks(c, rid)                                   # the preview …
    c05 = [r for r in s["rows"] if r["check_code"] == "C05"]
    assert c05 and not s["can_release"]
    c.rollback()                                                # … discarded
    assert c.execute("SELECT COUNT(*) FROM bc_release_check WHERE release_id=? AND check_code<>'unmatched'", (rid,)).fetchone()[0] == 0
    RL.accept(c, rid, "C05", c05[0]["ref"], "ครัวยืนยันเพิ่มปูอัดเป็น 60 กรัม", "analyst")   # saves the run, then accepts
    assert c.execute("SELECT resolved_by FROM bc_release_check WHERE release_id=? AND check_code='C05' AND ref=?",
                     (rid, c05[0]["ref"])).fetchone()[0] == "analyst"
    assert RL.checks_summary(c, rid)["can_release"]


def test_one_reason_for_a_whole_code(con):
    c = con
    rid = _c05_draft(c)
    assert _open(c, rid, "C05") == 0                            # nothing saved yet: accept_many runs the checks itself
    with pytest.raises(ValueError, match="ต้องระบุเหตุผล"):
        RL.accept_many(c, rid, "C05", None, " ", "analyst")
    with pytest.raises(ValueError, match="ไม่สามารถยอมรับได้"):
        RL.accept_many(c, rid, "C12", ["x"], "เหตุผล", "analyst")
    out = RL.accept_many(c, rid, "C05", None, "ครัวยืนยันทั้งชุด", "analyst")
    assert _open(c, rid, "C05") == 0 and out["can_release"]
    assert all(r["resolved_by"] == "analyst" for r in out["rows"] if r["check_code"] == "C05")
    assert RL.run_checks(c, rid)["can_release"]                 # the acceptances survive a re-run
    assert RL.accept_many(c, rid, "C05", None, "อีกครั้ง", "analyst")["can_release"]   # nothing left: a no-op


def test_the_endpoints_preview_and_next_step(client):
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
              " VALUES('RP', 'monthly', '2026-11', 'draft', 'buffet', '2026-07')")
    rid = c.execute("SELECT release_id FROM bc_release WHERE label='RP'").fetchone()[0]
    c.execute("INSERT INTO bc_recipe(release_id, sku_code, kind) VALUES(?, 'OTHBFSSM00020', 'recipe')", (rid,))
    c.execute("INSERT INTO bc_recipe_line(release_id, sku_code, seq, ingredient_code, qty, unit)"
              " VALUES(?, 'OTHBFSSM00020', 1, '13-01-009', 45, 'กรัม')", (rid,))
    c.commit()
    c.close()
    try:
        r = client.get(f"/api/bom-center/release-checks?release_id={rid}&recompute=true")
        assert r.status_code == 200 and r.json()["fresh"] is True
        c = sqlite3.connect(str(main.DB_PATH))
        assert c.execute("SELECT COUNT(*) FROM bc_release_check WHERE release_id=? AND check_code<>'unmatched'", (rid,)).fetchone()[0] == 0
        c.close()
        assert client.get(f"/api/bom-center/release-checks?release_id={rid}").json()["fresh"] is False
        rel = [x for x in client.get("/api/bom-center/releases").json()["releases"] if x["release_id"] == rid][0]
        assert rel["next_step"] == "submit"                     # PR-C: the kitchen submits a draft before the analyst releases
        r = client.post("/api/bom-center/release-act/accept-many",
                        json={"release_id": rid, "check_code": "C05", "refs": ["nope"], "reason": "x"})
        assert r.status_code == 400                             # no such finding, even after the run is saved
        r = client.post("/api/bom-center/release-act/accept-many",
                        json={"release_id": rid, "check_code": "C04", "refs": None, "reason": "x"})
        assert r.status_code == 400                             # not an acceptable code
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
