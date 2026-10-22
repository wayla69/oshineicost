"""PR-F5 - ร้านของฉัน, ingredient card in stock mode (no recipes) and no "กรุณา" in the owner page's strings.

  * stock mode: ingredients.latest_period is the branch's latest month-end period reconciled the way its สิ้นเดือน variance tab is
    (issue_reports.reconcile: total diff value; diff % of the issued value of the period); no period -> null;
    the 5 items counted furthest below the system stock replace the over-the-recipe list;
  * full mode: unchanged (variance.period);
  * no "กรุณา" in the user-visible strings of the owner page and its messages feature;
  * a franchise login still sees its own branch only.
"""
import json
from pathlib import Path

import pytest

from test_franchise_role import _walk, auth_on, world      # noqa: F401  (fixtures used by name)
from test_franchise_f3 import f3data, _supply, _db, MONTH, SAL, RICE      # noqa: F401

ROOT = Path(__file__).resolve().parent.parent


def _flush():
    import main
    if hasattr(main, "invalidate_cache"):
        try:
            main.invalidate_cache()
        except Exception:
            pass


@pytest.fixture
def mode():
    """Set bc_mode for a test; put the old value back afterwards."""
    c = _db()
    c.execute("CREATE TABLE IF NOT EXISTS app_settings(key TEXT PRIMARY KEY, value TEXT NOT NULL DEFAULT '')")
    old = c.execute("SELECT value FROM app_settings WHERE key='bc_mode'").fetchone()
    c.commit()

    def put(v):
        c.execute("INSERT OR REPLACE INTO app_settings(key, value) VALUES('bc_mode', ?)", (v,))
        c.commit()
        _flush()
    try:
        yield put
    finally:
        if old is None:
            c.execute("DELETE FROM app_settings WHERE key='bc_mode'")
        else:
            c.execute("INSERT OR REPLACE INTO app_settings(key, value) VALUES('bc_mode', ?)", (old[0],))
        c.commit()
        c.close()
        _flush()


@pytest.fixture
def slip(f3data):
    """A confirmed issue slip in the counted period: salmon 4,000 g and rice 2,000 g used (issued value 1,700 baht)."""
    c = _db()
    cur = c.execute("INSERT INTO bc_issue_slip(branch_id, slip_date, status, created_by, created_at)"
                    " VALUES(2,'2026-05-10','confirmed','own_t_b','2026-05-10T10:00:00')")
    sid = cur.lastrowid
    c.executemany("INSERT INTO bc_issue_line(slip_id, ingredient_code, qty_used, qty_waste, unit, qty_used_base, qty_waste_base)"
                  " VALUES(?,?,?,0,'กรัม',?,0)", [(sid, SAL, 4000, 4000), (sid, RICE, 2000, 2000)])
    c.commit()
    try:
        yield
    finally:
        c.execute("DELETE FROM bc_issue_line WHERE slip_id=?", (sid,))
        c.execute("DELETE FROM bc_issue_slip WHERE slip_id=?", (sid,))
        c.commit()
        c.close()


def _end_id(con):
    return con.execute("SELECT count_id FROM bc_stock_count WHERE branch_id=2 AND kind='end'").fetchone()[0]


def _summary(c):
    r = c.get("/api/franchise/summary", params={"month": MONTH})
    assert r.status_code == 200, r.text
    return r.json()


def test_stock_mode_takes_the_gap_from_the_month_end_reconcile(slip, world, mode):
    import main
    from bom_center import issue_reports as IR
    mode("stock")
    lp = _summary(world["b"])["ingredients"]["latest_period"]
    with main.get_db() as con:
        rec = IR.reconcile(con, 2, _end_id(con))
    assert (lp["begin"], lp["end"]) == ("2026-05-01", "2026-06-01")
    assert lp["diff_value"] == round(rec["totals"]["diff_value"]) == -2300           # salmon -2,000 and rice -300
    assert lp["diff_pct"] == round(100.0 * -2300 / 1700, 1)                         # of the 1,700 baht issued in the period


def test_stock_mode_lists_the_five_items_counted_furthest_below_the_system(slip, world, mode):
    mode("stock")
    o = _supply(world["b"])["overuse"]
    assert (o["begin"], o["end"]) == ("2026-05-01", "2026-06-01") and o["lines_over"] == 2
    assert [r["name"] for r in o["rows"]] == ["แซลมอนทดสอบ", "ข้าวทดสอบ"]
    assert [r["value"] for r in o["rows"]] == [2000, 300]                              # shortfall in baht, largest first
    assert o["rows"][0]["qty"] == 5.0 and o["rows"][0]["unit"] == "กิโลกรัม"


def test_stock_mode_without_a_counted_period_has_no_number(world, mode):
    mode("stock")
    d = _summary(world["b"])
    assert d["ingredients"]["latest_period"] is None and d["ingredients"]["stock_mode"] is True


def test_stock_mode_without_a_counted_period_has_no_list(f3data, world, mode):
    c = _db()
    c.execute("DELETE FROM bc_stock_count WHERE branch_id=2")
    c.commit()
    c.close()
    mode("stock")
    assert _supply(world["b"])["overuse"] is None


def test_full_mode_still_compares_with_the_recipes(slip, world, mode):
    import main
    from bom_center import variance as VA
    mode("full")
    lp = _summary(world["b"])["ingredients"]["latest_period"]
    with main.get_db() as con:
        v = VA.period(con, 2, _end_id(con), summary_only=True)
    assert lp["diff_pct"] == round(v["gap_pct"], 1) and lp["diff_value"] == round(v["gap_value"])
    o = _supply(world["b"])["overuse"]
    assert [r["name"] for r in o["rows"]] == ["แซลมอนทดสอบ"] and o["rows"][0]["value"] == 800


def test_the_stock_payload_holds_no_forbidden_word_or_other_branch(slip, world, mode):
    mode("stock")
    for who, other in (("b", "BranchA"), ("a", "BranchB")):
        for d in (_supply(world[who]), _summary(world[who])["ingredients"]):
            text = json.dumps(d, ensure_ascii=False)
            assert other not in text and "F3-SAL" not in text and "own_t_" not in text
            assert not [1 for p, kind, s in _walk(d) if any(w in s.lower() for w in ("recipe", "receipt_no", "bill_no", "cashier"))]


def test_another_branch_is_still_refused(slip, world, mode):
    mode("stock")
    assert world["b"].get("/api/franchise/summary", params={"month": MONTH, "branch_id": 1}).status_code == 403


def test_no_kruna_in_the_owner_page_strings():
    for f in ("js/17-franchise.js", "js/19-fmsg.js", "backend/routers/franchise_msgs.py", "backend/routers/franchise.py"):
        assert "กรุณา" not in (ROOT / f).read_text(encoding="utf-8"), f
