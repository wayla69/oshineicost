"""สรุปประจำวัน (owner 2026-10-27, mockup B) - GET /api/franchise/daily at the top of ร้านของฉัน.

  * the branch is the signed session's own (_resolve_branch): another branch_id is 403, a franchise login with no branch is 403,
    the centre must name the branch;
  * the day is the branch's last FINISHED day (rules.latest_settled_day): a bill on the Thai day still trading never moves it;
  * the day's figures are the dashboard's: net sales on rules.net_revenue_sum (the summary's daily table agrees), per head, the same
    weekday a week earlier, discount on the pre-discount base against the ceiling, food cost from period_cost against the ONE target;
  * no last-year figure for the day (the owner left it out); the month block is the summary's own month-to-date figures and target;
  * voided bills of the month carry the controls card's bands (0.5 % / 1 %);
  * the payload holds no other branch, no bill / recipe / price / staff key or word.
"""
import json
import sqlite3
from datetime import date, timedelta

import auth
from test_franchise_role import _walk, auth_on, world      # noqa: F401  (fixtures used by name)

FORBIDDEN = ("recipe", "price", "cashier", "staff", "supplier", "receipt_no", "bill_no", "username", "password", "token", "bom")


def _daily(c, **q):
    r = c.get("/api/franchise/daily", params=q)
    assert r.status_code == 200, r.text
    return r.json()


def test_the_daily_get_is_under_the_one_franchise_rule_and_nothing_writes():
    mods = auth.ROLE_MODULES["franchise"]
    assert auth.path_allowed("GET", "/api/franchise/daily", mods)
    for m in ("POST", "PUT", "PATCH", "DELETE"):
        assert not auth.path_allowed(m, "/api/franchise/daily", mods), m


def test_the_branch_is_the_sessions_own(world):
    assert _daily(world["b"])["branch"] == "BranchB"
    assert _daily(world["a"])["branch"] == "BranchA"
    r = world["b"].get("/api/franchise/daily", params={"branch_id": 1})
    assert r.status_code == 403 and r.headers.get("X-Denied") == "role"
    assert world["nobranch"].get("/api/franchise/daily").status_code == 403
    assert world["mg"].get("/api/franchise/daily").status_code == 400                     # the centre names the branch
    assert _daily(world["mg"], branch_id=2)["branch"] == "BranchB"


def test_the_day_is_the_last_finished_day_and_matches_the_summary(world):
    d = _daily(world["b"])
    s = world["b"].get("/api/franchise/summary", params={"month": "2026-06"}).json()["sales"]
    D = d["day"]
    assert d["available"] is True and D["date"] == s["last_day"]["date"] == "2026-06-03"
    row = next(x for x in s["daily"] if x["date"] == D["date"])
    assert D["revenue"] == row["revenue"] and D["bills"] == row["bills"] and D["customers"] == row["customers"]
    assert D["weekday"] == row["weekday"] == "พุธ"                                          # 3 June 2026 is a Wednesday
    assert D["per_head"] == round(D["revenue"] / D["customers"]) and D["per_bill"] == round(D["revenue"] / D["bills"])
    # THE daily comparison (2026-10-31): against "ปกติ", the median of the same weekday over 8 weeks - the same figure as the
    # summary's daily table and the scorecard's row for the branch
    assert "vs_last_week_pct" not in D and D["vs_normal_pct"] == row["vs_normal_pct"] and D["normal_weeks"] == row["normal_weeks"]
    assert not any("year" in k or "yoy" in k for k in D)                                   # no last-year figure for the day


def test_a_bill_on_the_day_still_trading_never_moves_the_day(world, app_db):
    import main
    from rules import TH_TODAY_SQL
    c = sqlite3.connect(str(main.DB_PATH))
    today = c.execute(f"SELECT {TH_TODAY_SQL}").fetchone()[0]
    c.execute("INSERT INTO sales_transaction(branch_id, receipt_no, sold_at, customer_count, total_inc_vat, service_charge, discount, source) "
              "VALUES(2, 'DAILY-TODAY-1', ?, 2, 1070, 0, 0, 'Easy')", (today + " 12:00:00",))
    c.commit()
    try:
        assert _daily(world["b"])["day"]["date"] == "2026-06-03"
    finally:
        c.execute("DELETE FROM sales_transaction WHERE receipt_no='DAILY-TODAY-1'")
        c.commit()
        c.close()


def test_discount_and_food_cost_follow_the_one_rules(world, app_db):
    import main
    import period_cost as PC
    from rules import discount_base_row, discount_policy_pct, food_cost_target
    D = _daily(world["b"])["day"]
    con = sqlite3.connect(str(main.DB_PATH))
    con.row_factory = sqlite3.Row
    try:
        disc, base = con.execute(f"SELECT COALESCE(SUM(t.discount),0), SUM({discount_base_row('t')}) FROM sales_transaction t "
                                 "WHERE t.branch_id=2 AND t.source='Easy' AND date(t.sold_at)=?", (D["date"],)).fetchone()
        assert D["discount_pct"] == (round(100.0 * disc / base, 1) if base else None)
        pol = discount_policy_pct(con)
        assert D["discount_policy_pct"] == round(pol, 1)
        want = None if D["discount_pct"] is None else "fix" if D["discount_pct"] > 2 * pol else "watch" if D["discount_pct"] > pol else "good"
        assert D["discount_level"] == want
        fc, _ = PC.cost_sum(con, D["date"], D["date"], "BranchB", None, PC.cost_floor(con))
        assert D["food_cost_target_pct"] == food_cost_target(con) == 58.0
        if fc and fc.get("food_cost_pct") is not None:
            assert D["food_cost_pct"] == round(fc["food_cost_pct"], 1) and D["food_cost_reason"] is None
            assert D["food_cost_status_th"].startswith("ในเป้า" if D["food_cost_pct"] <= 58 else "เกินเป้า")
        else:
            assert D["food_cost_pct"] is None and D["food_cost_reason"]
    finally:
        con.close()


def test_the_month_block_is_the_summarys_month_to_date(world):
    d = _daily(world["b"])
    s = world["b"].get("/api/franchise/summary", params={"month": "2026-06"}).json()
    M = d["month"]
    assert M["month"] == "2026-06" and M["through"] == s["sales"]["through"] == d["day"]["date"]
    assert M["mtd"] == s["sales"]["mtd"] and M["complete"] == s["sales"]["month_complete"]
    assert M["vs_last_month_pct"] == s["sales"]["mtd_vs_last_month_pct"] and M["vs_last_year_pct"] == s["sales"]["mtd_vs_last_year_pct"]
    assert M["target"] == s["target"]
    assert set(M["voids"]) == {"bills", "rate_pct", "level", "watch_pct", "fix_pct"}
    assert (M["voids"]["watch_pct"], M["voids"]["fix_pct"]) == (0.5, 1.0)


def test_voided_bills_carry_the_controls_bands(monkeypatch):
    from routers import franchise as F
    from routers import home_extra
    for rate, want in ((None, None), (0.4, "good"), (0.5, "good"), (0.8, "watch"), (1.0, "watch"), (2.9, "fix")):
        monkeypatch.setattr(home_extra, "extra_cancel_bills",
                            lambda month, branch, group, _r=rate: {"month": month, "summary": {"bills": 3, "cancel_rate": _r}})
        assert F._daily_voids("BranchB", "2026-06")["level"] == want, rate
    monkeypatch.setattr(home_extra, "extra_cancel_bills", lambda month, branch, group: {"month": "2026-05", "summary": {"cancel_rate": 9}})
    assert F._daily_voids("BranchB", "2026-06") == {"bills": 0, "rate_pct": None, "level": None, "watch_pct": 0.5, "fix_pct": 1.0}


def test_no_finished_day_answers_not_available(monkeypatch, app_db):
    import rules
    from routers import franchise as F
    monkeypatch.setattr(rules, "latest_settled_day", lambda con, where, params=None: {"day": None, "skipped_day": None})
    assert F.franchise_daily_data(2) == {"available": False, "branch": "BranchB", "day": None, "month": None}


def test_the_payload_holds_no_other_branch_or_forbidden_word(world):
    for who, other in (("b", "BranchA"), ("a", "BranchB")):
        d = _daily(world[who])
        hits = [(p, kind, s) for p, kind, s in _walk(d) if any(w in s.lower() for w in FORBIDDEN)]
        assert not hits, hits
        assert other not in json.dumps(d, ensure_ascii=False)


def test_the_page_draws_the_card_first_and_fetches_the_day():
    from pathlib import Path
    js = (Path(__file__).resolve().parent.parent / "js" / "17-franchise.js").read_text(encoding="utf-8")
    tab = (Path(__file__).resolve().parent.parent / "tabs" / "56-franchise.html").read_text(encoding="utf-8")
    assert "frcShowDay(gen, _frcGetJson('/api/franchise/daily'))" in js
    # the card has its own slot above ติดต่อสำนักงานใหญ่ and the month cards
    assert tab.index('id="frcDay"') < tab.index('id="frcMsg"') < tab.index('id="frcBody"')
    card = js[js.index("function _frcDayCard"):js.index("function _frcDayMonth")]
    hero = card[card.index("const hero"):card.index("const fc")]
    assert "ปีก่อน" not in hero and "_frcHeroChip(" in hero                  # no last-year chip for the day (owner 2026-10-27)
    assert "verdict" not in card and "frcAskAbout" not in card              # no ควรดู items in the card


def test_the_month_ends_on_the_cards_day_when_the_newest_day_has_not_fully_arrived(world, monkeypatch):
    """Owner 2026-10-27: on the live app at 10:00 the card showed 25 Oct (26 Oct held 14 bills, skipped) but the month ran to
    26 Oct. When latest_settled_day skips the newest day, the month block stops on the card's day, on the same to-date rules."""
    import rules
    full = _daily(world["b"])["month"]                                  # the report path: through 2026-06-03
    daily = world["b"].get("/api/franchise/summary", params={"month": "2026-06"}).json()["sales"]["daily"]
    monkeypatch.setattr(rules, "latest_settled_day",
                        lambda con, where, params=None, **k: {"day": "2026-06-02", "skipped_day": "2026-06-03", "skipped_bills": 1})
    d = _daily(world["b"])
    M = d["month"]
    assert d["day"]["date"] == "2026-06-02" and d["skipped_day"] == "2026-06-03"
    assert M["through"] == "2026-06-02" and M["complete"] is False and M["label"].endswith("(1–2)")
    assert M["mtd"] == round(sum(x["revenue"] or 0 for x in daily if x["date"] <= "2026-06-02"), 2) < full["mtd"]
    assert set(M) == set(full) and set(M["target"] or {}) == set(full["target"] or {})
    if M["target"] and M["target"]["source"] != "none":
        assert M["target"]["to_date"] <= full["target"]["to_date"]
    assert M["voids"] == full["voids"]                                  # voided bills are monthly either way
