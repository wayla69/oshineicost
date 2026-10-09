"""BoM Center — Phase 2 · Sprint S7: goods received and usage variance per period.

The promises:
  * a period runs from one count to the next; a count is the stock BEFORE that day's
    trading and deliveries, so the period holds the sales and deliveries from the begin
    date up to the day before the end count;
  * actual use = begin + received − end; BoM use = plates sold × recipe; the gap is
    judged against the class threshold (A 5 %, B 10 %, C 20 %) and valued in ฿;
  * nothing is guessed: an item missing from a count has no actual use, more stock than
    began + arrived is flagged, and a period stays INCOMPLETE until the branch confirms
    every delivery is recorded;
  * the branch gives a reason per flagged item ("อื่น ๆ" needs a note);
  * a receipt inside a closed period can no longer be deleted;
  * the branch login reads its own variance, never the centre's overview.
"""
import pytest

import auth
from bom_center import stock as ST
from bom_center import variance as VA
from test_bom_center_s4b import con  # noqa: F401  (Sep–Oct sales at branch 1: crab stick 45 g, salmon 20 g a plate)

WEEK_PLATES = 30 + 10 + 10 + 10 + 12 + 20 + 40          # every weekday once: 8–14 Sep 2026


def _count(db, day, lines):
    return ST.save(db, 1, [{"code": c, "qty": q, "unit": "กรัม"} for c, q in lines.items()], "udon",
                   submit=True, count_date=day)["count_id"]


@pytest.fixture
def db(con):  # noqa: F811
    _count(con, "2026-09-08", {"13-01-009": 10000, "02-01-001": 5000, "V-007": 100})
    VA.add_receipt(con, 1, "2026-09-08", [{"code": "13-01-009", "qty": 5, "unit": "แพ็ค 1 กก."}], "udon")   # begin day: in
    VA.add_receipt(con, 1, "2026-09-15", [{"code": "13-01-009", "qty": 9, "unit": "แพ็ค 1 กก."}], "udon")   # end day: next period
    crab_bom = WEEK_PLATES * 45
    salmon_bom = WEEK_PLATES * 20
    end = _count(con, "2026-09-15", {"13-01-009": 15000 - round(crab_bom * 1.3),   # 30 % more than the recipes
                                     "02-01-001": 5000 - salmon_bom})               # exactly the recipes
    con.commit()
    return con, end


def _rows(p):
    return {r["code"]: r for r in p["rows"]}


def test_actual_against_bom(db):
    db, _ = db
    p = VA.period(db, 1)
    assert (p["begin_date"], p["end_date"], p["days"], p["trading_days"]) == ("2026-09-08", "2026-09-15", 7, 7)
    r = _rows(p)
    crab, salmon = r["13-01-009"], r["02-01-001"]
    assert crab["bom"] == pytest.approx(WEEK_PLATES * 45)
    assert crab["received"] == 5000                               # the 15 Sep delivery belongs to the next period
    assert crab["actual"] == pytest.approx(round(WEEK_PLATES * 45 * 1.3))
    assert crab["status"] == "over" and crab["gap_pct"] == pytest.approx(30.0, abs=0.1)
    assert crab["gap_value"] == pytest.approx(crab["gap"] * 0.15, abs=0.01)
    assert salmon["status"] == "ok" and salmon["gap"] == pytest.approx(0, abs=0.001)
    assert r["V-007"]["status"] == "not_counted" and r["V-007"]["actual"] is None   # counted at begin only
    assert p["receipts"] == 1 and not p["complete"] and p["flagged"] == 1 and p["needs_reason"] == 1
    assert p["rows"][0]["code"] == "13-01-009"                    # flagged first


def test_more_stock_than_began_and_arrived_is_flagged(db):
    db, _ = db
    _count(db, "2026-09-22", {"13-01-009": 99999, "02-01-001": 10})
    r = _rows(VA.period(db, 1))
    assert r["13-01-009"]["status"] == "negative" and r["13-01-009"]["needs_reason"]


def test_reasons_and_confirmation(db):
    db, e = db
    with pytest.raises(ValueError):
        VA.set_reason(db, 1, e, "13-01-009", "aliens", None, "udon")
    with pytest.raises(ValueError):
        VA.set_reason(db, 1, e, "13-01-009", "other", "  ", "udon")
    with pytest.raises(ValueError):
        VA.set_reason(db, 2, e, "13-01-009", "waste", None, "udon")      # not this branch's period
    VA.set_reason(db, 1, e, "13-01-009", "overportion", "พนักงานใหม่ตักเกิน", "udon")
    VA.confirm_receipts(db, 1, e, "udon")
    p = VA.period(db, 1)
    crab = _rows(p)["13-01-009"]
    assert crab["reason"] == "overportion" and crab["reason_th"] == "เสิร์ฟเกินปริมาณตามสูตร" and not crab["needs_reason"]
    assert p["complete"] and p["receipts_confirmed_by"] == "udon" and p["needs_reason"] == 0
    VA.confirm_receipts(db, 1, e, "udon", undo=True)
    assert not VA.period(db, 1)["complete"]


def test_receipts_validate_and_lock(db):
    db, _ = db
    with pytest.raises(ValueError):
        VA.add_receipt(db, 1, "2026-09-16", [], "udon")
    with pytest.raises(ValueError):
        VA.add_receipt(db, 1, "16/09/2026", [{"code": "S-011", "qty": 1}], "udon")
    with pytest.raises(ValueError):
        VA.add_receipt(db, 1, "2026-09-16", [{"code": "13-01-009", "qty": 0}], "udon")
    with pytest.raises(ValueError):
        VA.add_receipt(db, 1, "2026-09-16", [{"code": "13-01-009", "qty": 1, "unit": "ช้อนโต๊ะ"}], "udon")
    with pytest.raises(ValueError):
        VA.add_receipt(db, 1, "2026-09-16", [{"code": "13-01-009", "qty": 1, "unit": "กรัม"},
                                             {"code": "13-01-009", "qty": 1, "unit": "แพ็ค 1 กก."}], "udon")
    out = VA.add_receipt(db, 1, "2026-09-16", [{"code": "13-01-009", "qty": 1, "unit": "แพ็ค 1 กก."},
                                               {"code": "13-01-009", "qty": 2, "unit": "แพ็ค 1 กก."}], "udon")
    assert out["lines"] == 1
    rs = {r["receipt_id"]: r for r in VA.receipts(db, 1)}
    assert rs[out["receipt_id"]]["lines"][0]["qty_base"] == 3000 and not rs[out["receipt_id"]]["locked"]
    old = min(rs)                                                 # 8 Sep — inside the measured period
    assert rs[old]["locked"]
    with pytest.raises(ValueError):
        VA.delete_receipt(db, 1, old, "udon")
    assert VA.delete_receipt(db, 1, out["receipt_id"], "udon")
    with pytest.raises(ValueError):
        VA.delete_receipt(db, 2, old, "udon")


def test_periods_list_and_overview(db):
    db, _ = db
    _count(db, "2026-09-22", {"13-01-009": 1000, "02-01-001": 1000})
    ps = VA.periods(db, 1)
    assert [p["end_date"] for p in ps] == ["2026-09-22", "2026-09-15"]
    ov = VA.overview(db)
    assert ov[0]["branch_id"] == 1 and ov[0]["end_date"] == "2026-09-22" and ov[0]["last_count"] == "2026-09-22"
    # PR-J1: a confirmed issue slip is not a period boundary — the periods stay as they were
    from datetime import datetime
    from bom_center import issues as IS
    from bom_center.clock import BKK
    IS.save(db, 1, "2026-09-25", [{"code": "13-01-009", "qty_used": 5, "unit": "กรัม"}], "udon", submit=True,
            now=datetime(2026, 9, 25, 20, 0, tzinfo=BKK))
    assert [p["end_date"] for p in VA.periods(db, 1)] == ["2026-09-22", "2026-09-15"]
    assert ST.sheet(db, 1)["next_kind"] == "end"


def test_one_count_is_not_a_period(con):  # noqa: F811
    _count(con, "2026-09-08", {"13-01-009": 1})
    with pytest.raises(ValueError):
        VA.period(con, 1)


def test_branch_role_reads_its_own_variance_only():
    mods = auth.ROLE_MODULES["branch"]
    assert auth.path_allowed("GET", "/api/bom-center/variance/period", mods)
    assert auth.path_allowed("POST", "/api/bom-center/variance/reason", mods)
    assert auth.path_allowed("POST", "/api/bom-center/stock/receipt", mods)
    assert not auth.path_allowed("GET", "/api/bom-center/variance/overview", mods)
    assert auth.path_allowed("GET", "/api/bom-center/variance/overview", auth.ROLE_MODULES["operation"])
