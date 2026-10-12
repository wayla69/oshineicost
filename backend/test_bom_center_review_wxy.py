"""BoM Center - PR-Z: fixes from the correctness review of PR-W / PR-X / PR-Y.

The promises:
  * a central report that holds BOTH the register code and an active alias of it books each line with ITS OWN pack size, and
    the register / central list take ONE pack (the row under the item's current accounting code), never the last file row;
  * the recipe check is rebuilt when a draft count is submitted or a price file is re-imported with the same row count;
  * a Cost-RW recipe line that names an active alias code uses the register item and registers nothing new.
"""
from datetime import date

from bom_center import aliases as AL, central as CE, forecast as FC, recipe_check as RC, recipes as R, stock as ST
from test_bom_center_benchmark import rc, sold, _flat_season, _by, CRAB as RC_CRAB, TODAY  # noqa: F401
from test_bom_center_s11 import _report, udon  # noqa: F401
from test_bom_center_s4b import con  # noqa: F401

CRAB = "13-01-009"
OLD = (CRAB, "ปูอัดซาชิมิ Kani Kamoboko 0.5Kg/Pack(20Pk)", "กล20.00/PK", 1, 1500, "Box")      # 20 x 500 g = 10,000 g
NEW = ("03-NEW-CRAB", "ปูอัดซาชิมิ Kani Kamoboko 0.5Kg/Pack(10Pk)", "กล10.00/PK", 1, 800, "Box")  # 10 x 500 g = 5,000 g


def _received(db):
    return db.execute("SELECT qty_base FROM bc_receipt_line WHERE ingredient_code=?", (CRAB,)).fetchall()


def test_old_and_new_code_in_one_report(udon):
    AL.add_alias(udon, CRAB, "03-NEW-CRAB", "acc", None, None, "ana")
    p = CE.parse_report(udon, _report([NEW, OLD]))
    rows = CE.plan_catalogue(udon, p)["rows"]
    assert [(r["code"], r["base_per_sale"]) for r in rows] == [(CRAB, 5000.0), (CRAB, 10000.0)]
    out = CE.apply_catalogue(udon, p, "ana")
    assert out["items"] == 1 and out["saved"] == 1                    # one item, not two
    dl = CE.deliveries(udon, p, "ana", None, dry_run=False)
    assert dl["branches"][0]["lines"] == 2
    assert _received(udon) == [(15000.0,)]                            # 1 box of 10 x 500 g + 1 box of 20 x 500 g


def test_register_pack_does_not_follow_file_order(udon):
    AL.add_alias(udon, CRAB, "03-NEW-CRAB", "acc", None, None, "ana")
    packs = []
    for src in ([OLD, NEW], [NEW, OLD]):
        p = CE.parse_report(udon, _report(src))
        CE.apply_catalogue(udon, p, "ana")
        reg = udon.execute("SELECT recv_unit, qty_per_recv FROM bc_ingredient WHERE code=?", (CRAB,)).fetchone()
        ci = udon.execute("SELECT base_per_sale, price_per_sale FROM bc_central_item WHERE code=?", (CRAB,)).fetchone()
        packs.append((reg, ci))
        udon.execute("DELETE FROM bc_receipt_line")
        udon.execute("DELETE FROM bc_receipt")
        CE.deliveries(udon, p, "ana", None, dry_run=False)
        assert _received(udon) == [(15000.0,)]
    assert packs[0] == packs[1]
    assert packs[0][1] == (5000.0, 800)                               # the row under the current accounting code speaks for the item


def test_register_code_row_wins_when_no_alias_is_current(udon):
    AL.add_alias(udon, CRAB, "03-NEW-CRAB", "acc", None, None, "ana", make_current=False)
    p = CE.parse_report(udon, _report([NEW, OLD]))
    CE.apply_catalogue(udon, p, "ana")
    assert udon.execute("SELECT base_per_sale FROM bc_central_item WHERE code=?", (CRAB,)).fetchone() == (10000.0,)


def _save(db, day, lines, bid, submit):
    return ST.save(db, bid, [{"code": c, "qty": q, "unit": "กรัม"} for c, q in lines.items()], "udon",
                   submit=submit, count_date=day, require_all=False)


def test_submitting_a_draft_count_refreshes_the_recipe_check(rc, monkeypatch):
    monkeypatch.setattr(FC, "USAGE_CACHE_TTL", 600)
    use = FC.usage_history(rc, 3, date(2026, 9, 1), date(2026, 9, 30), "2026-09")[0]
    crab = sum(use[RC_CRAB].values())
    rc.execute("UPDATE bc_ingredient_price_branch SET qty_bought=?, value_bought=? WHERE branch_id=3 AND ingredient_code=?",
               (crab + 4500, crab + 4500, RC_CRAB))
    rc.commit()
    RC.clear_cache()
    _save(rc, "2026-09-01", {RC_CRAB: 4000}, 3, True)
    _save(rc, "2026-10-01", {RC_CRAB: 8500}, 3, False)               # the end count is still a draft
    rc.commit()
    assert _by(RC.check(rc, "2026-09", TODAY))[RC_CRAB]["branches"][1]["adjusted"] is False
    _save(rc, "2026-10-01", {RC_CRAB: 8500}, 3, True)                # now submitted: same count id, new state
    rc.commit()
    assert _by(RC.check(rc, "2026-09", TODAY))[RC_CRAB]["branches"][1]["adjusted"] is True


def test_price_reimport_with_same_rows_refreshes_the_signatures(rc):
    from bom_center import per_sales as PS
    before = (RC._signature(rc, "2026-09", []), PS._purchase_signature(rc))
    rc.execute("UPDATE bc_ingredient_price SET price_per_base=price_per_base + 0.5 WHERE month='2026-09'")
    rc.execute("UPDATE bc_ingredient_price_branch SET price_per_base=price_per_base + 0.5 WHERE month='2026-09'")
    rc.commit()
    after = (RC._signature(rc, "2026-09", []), PS._purchase_signature(rc))
    assert before[0] != after[0] and before[1] != after[1]


def test_cost_rw_alias_code_uses_the_register_item(udon):
    AL.add_alias(udon, CRAB, "03-NEW-CRAB", "acc", None, None, "ana")
    rid = R.get_or_create_draft(udon, "T", "2026-11", "2026-09", "t")
    n = udon.execute("SELECT COUNT(*) FROM bc_ingredient").fetchone()[0]
    R.write_recipe(udon, rid, "OTHBFSSM00020",
                   [{"ingredient_code": "03-NEW-CRAB", "ingredient_name": "ปูอัด", "qty": 30, "unit": "กรัม"}],
                   source="cost-rw", who="t")
    assert udon.execute("SELECT COUNT(*) FROM bc_ingredient").fetchone()[0] == n
    assert udon.execute("SELECT 1 FROM bc_ingredient WHERE code='03-NEW-CRAB'").fetchone() is None
    assert udon.execute("SELECT ingredient_code FROM bc_recipe_line WHERE release_id=? AND sku_code='OTHBFSSM00020'",
                        (rid,)).fetchall() == [(CRAB,)]
