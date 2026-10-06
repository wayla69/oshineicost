"""BoM Center — Phase 2 · Sprint S10: hardening and the pilot.

The promises:
  * every date a branch action defaults to is Bangkok's calendar day — the live server
    runs in UTC and is still on yesterday until 07:00 in Bangkok;
  * readiness lists, for the centre and for each pilot branch, what is still to set up
    (master, prices, branch login, order calendar, first count);
  * the scorecard measures each pilot branch against plan §7.3: counts on order days
    ≥ 90 %, time to count ≤ 20 min, gaps with a reason ≥ 80 %, class-A forecast error ≤ 15 %.
"""
import sqlite3
from datetime import date, datetime, timedelta, timezone

import auth
from bom_center import clock
from bom_center import pilot as PL
from bom_center import stock as ST
from bom_center import variance as VA
from test_bom_center_s4b import con  # noqa: F401  (sales Sep–Oct 2026 at branch 1)


def test_bangkok_day_not_the_servers(monkeypatch):
    class Fake(datetime):
        @classmethod
        def now(cls, tz=None):
            t = datetime(2026, 10, 6, 23, 30, tzinfo=timezone.utc)      # 06:30 on the 7th in Bangkok
            return t.astimezone(tz) if tz else t.replace(tzinfo=None)
    monkeypatch.setattr(clock, "datetime", Fake)
    assert clock.today() == date(2026, 10, 7)
    assert clock.BKK.utcoffset(None) == timedelta(hours=7)


def test_a_count_without_a_date_is_bangkoks_today(con, monkeypatch):  # noqa: F811
    monkeypatch.setattr(ST, "bkk_today", lambda: date(2026, 10, 7))
    out = ST.save(con, 1, [{"code": "13-01-009", "qty": 1}], "udon", submit=True)
    assert ST.detail(con, out["count_id"])["count_date"] == "2026-10-07"


def test_readiness(con):  # noqa: F811
    con.row_factory = sqlite3.Row
    con.execute("UPDATE branches SET name='อุดรธานี' WHERE branch_id=1")
    auth.init_auth(con)
    r = PL.readiness(con)
    assert [b["branch"] for b in r["branches"]] == ["อุดรธานี"]
    assert set(r["missing_branches"]) == {"อุบลราชธานี", "ขอนแก่น", "ระยอง"}
    assert {s["key"]: s["done"] for s in r["centre_steps"]}["master"] is True
    b = r["branches"][0]
    assert {s["key"]: s["done"] for s in b["steps"]} == {"account": False, "calendar": False, "begin_count": False}
    auth.create_user(con, "udon_branch", "Abcdefgh1234!", "branch", branch="อุดรธานี")
    for rnd, wd in (("1", 2), ("2", 5), ("salmon", 1)):
        con.execute("INSERT INTO bc_order_calendar(branch_id, round, order_weekday, filled_by) VALUES(1,?,?, 'sales')",
                    (rnd, wd))
    ST.save(con, 1, [{"code": "13-01-009", "qty": 1}], "udon", submit=True, count_date="2026-09-22")
    b = PL.readiness(con)["branches"][0]
    assert b["ready"] and b["first_count"] == "2026-09-22" and b["accounts"][0]["username"] == "udon_branch"


def test_scorecard_against_the_pass_criteria(con):  # noqa: F811
    def count(day, crab):
        return ST.save(con, 1, [{"code": "13-01-009", "qty": crab, "unit": "กรัม"},
                                {"code": "02-01-001", "qty": 9000, "unit": "กรัม"}], "udon",
                       submit=True, count_date=day)["count_id"]
    count("2026-09-22", 20000)
    e = count("2026-09-29", 1000)                                  # far more crab used than the recipes
    VA.set_reason(con, 1, e, "13-01-009", "waste", None, "udon")
    con.commit()
    assert PL.scorecard(con, 2, weeks=2, as_of=date(2026, 10, 5))["verdict"] == "not_started"
    s = PL.scorecard(con, 1, weeks=3, as_of=date(2026, 10, 5))
    assert s["from"] == "2026-09-22"                               # starts at the first count, not 3 weeks back
    assert s["cycles"] == 4                                        # Tue 22 & 29 Sep, Fri 25 Sep & 2 Oct
    m, p = s["metrics"], s["passes"]
    assert m["count_coverage"] == 50.0 and p["count_coverage"] is False
    assert m["count_minutes"] is not None and p["count_minutes"] is True
    assert m["reason_coverage"] is not None and s["flagged"] >= 1
    assert m["order_submission"] == 0.0
    assert s["verdict"] == "fail"
    ov = PL.overview(con, 2)
    assert ov["targets"]["mape_a"] == 15.0


def test_pilot_page_is_the_centres():
    assert auth.path_allowed("GET", "/api/bom-center/hub/pilot", auth.ROLE_MODULES["operation"])
    assert not auth.path_allowed("GET", "/api/bom-center/hub/pilot", auth.ROLE_MODULES["branch"])
