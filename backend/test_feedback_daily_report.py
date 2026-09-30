# -*- coding: utf-8 -*-
"""📰 Daily report (marketing): one calendar day of customer_feedback through
the same _aggregate() /api/feedback/summary uses, plus a printable HTML edition."""
import json
import sqlite3
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).parent.resolve()
sys.path.insert(0, str(HERE))

from routers import feedback_daily_report as D  # noqa: E402

DAY = "2026-06-15"


def _wipe(con, table):
    if con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
        con.execute(f"DELETE FROM {table}")


@pytest.fixture(autouse=True)
def _clean(request):
    yield
    if "client" not in request.fixturenames:
        return
    import core
    con = sqlite3.connect(str(core.DB_PATH))
    _wipe(con, "customer_feedback")
    _wipe(con, "feedback_action")
    con.commit(); con.close()


def _seed(con, rows):
    """rows: (branch, rated_at, rating, text, sentiment, themes, menus, contact, severity)"""
    from routers.feedback import _ensure
    _ensure(con)
    for i, r in enumerate(rows):
        branch, rated_at, rating, text, sentiment, themes, menus, contact, severity = r
        con.execute("INSERT INTO customer_feedback(branch_id, branch_name, source, rated_at, rating, text, sentiment, "
                    "kind, themes, menus, external_id, contact, severity) VALUES (1,?,?,?,?,?,?, 'review',?,?,?,?,?)",
                    (branch, "Google", rated_at, rating, text, sentiment,
                     json.dumps(themes, ensure_ascii=False), json.dumps(menus, ensure_ascii=False),
                     f"dr:{i}", contact, severity))


def _seed_action(con, **kw):
    from routers.feedback_actions import _ensure as _ensure_actions
    _ensure_actions(con)
    kw.setdefault("created_at", f"{DAY} 08:00:00")
    kw.setdefault("status", "open")
    cols = ", ".join(kw.keys())
    marks = ", ".join("?" for _ in kw)
    con.execute(f"INSERT INTO feedback_action({cols}) VALUES ({marks})", list(kw.values()))


def test_tel_href_dials_a_phone_but_not_a_line_id():
    assert D._tel_href("081-234-5678") == "tel:0812345678"
    assert D._tel_href("+66 81 234 5678") == "tel:+66812345678"
    assert D._tel_href("lineid_test99") is None
    assert D._tel_href("") is None
    assert D._tel_href(None) is None


def test_compute_daily_report_is_one_day_of_the_same_aggregate(client):
    import core
    con = sqlite3.connect(str(core.DB_PATH))
    _seed(con, [
        ("BranchA", f"{DAY} 09:00:00", 1, "รอนานมาก บริการแย่", "neg", ["รอนาน"], ["แซลมอน"], None, "low"),
        ("BranchA", f"{DAY} 23:59:59", 2, "อาหารมีปัญหา", "neg", ["คุณภาพอาหาร"], [], "081-999-8888", "high"),
        ("BranchA", f"{DAY} 12:00:00", 5, "แซลมอนสดมาก", "pos", [], ["แซลมอน"], None, "low"),
        ("BranchA", "2026-06-14 12:00:00", 5, "เมื่อวานก็ดี", "pos", [], [], None, "low"),   # the day before: excluded
        ("BranchA", "2026-06-16 00:00:01", 1, "พรุ่งนี้แย่", "neg", [], [], None, "low"),     # the day after: excluded
    ])
    con.commit(); con.close()
    with core.get_db() as con:
        d = D.compute_daily_report(con, DAY)
    assert d["date_from"] == DAY and d["date_to"] == DAY and d["total"] == 3 and d["sentiment"] == {"pos": 1, "neg": 2, "neu": 0}
    assert d["severe"] == 1 and "3 เสียง" in d["headline"] and "⚠ ร้ายแรง 1" in d["headline"]
    assert any(m["menu"] == "แซลมอน" for m in d["menus"]) and any(t["theme"] == "รอนาน" for t in d["themes"])
    neg_texts = {it["text"] for it in d["recent_negative"]}
    assert neg_texts == {"รอนานมาก บริการแย่", "อาหารมีปัญหา"}
    sev = next(it for it in d["recent_negative"] if it["text"] == "อาหารมีปัญหา")
    assert sev["severity"] == "high" and sev["contact"] == "081-999-8888"
    assert d["recent_positive"][0]["text"] == "แซลมอนสดมาก"
    assert d["date_from"] == DAY and d["date_to"] == DAY
    assert d["prev_period"] == {"date_from": "2026-06-08", "date_to": "2026-06-08", "total": 0}   # one day → the same weekday 7 days earlier


def test_empty_day_headline(client):
    import core
    with core.get_db() as con:
        d = D.compute_daily_report(con, DAY, branch="BranchZ")
    assert d["total"] == 0 and d["headline"] == f"ไม่มีเสียงลูกค้าเข้ามาในช่วง {DAY} ที่สาขา BranchZ"


def test_endpoint_defaults_to_today_and_scopes_by_branch(client):
    import core
    con = sqlite3.connect(str(core.DB_PATH))
    _seed(con, [("BranchA", f"{DAY} 10:00:00", 4, "โอเค", "pos", [], [], None, "low"),
                ("BranchB", f"{DAY} 10:00:00", 2, "แย่", "neg", [], [], None, "low")])
    con.commit(); con.close()
    j = client.get("/api/feedback/daily-report", params={"date": DAY, "branch": "BranchA"}).json()
    assert j["total"] == 1 and j["sentiment"]["pos"] == 1
    j_all = client.get("/api/feedback/daily-report", params={"date": DAY}).json()
    assert j_all["total"] == 2
    j_default = client.get("/api/feedback/daily-report").json()               # no date → today, no crash
    assert j_default["date_from"] == j_default["date_to"] and "headline" in j_default


def test_print_edition_is_html_with_the_headline_and_a_tel_link(client):
    import core
    con = sqlite3.connect(str(core.DB_PATH))
    _seed(con, [("BranchA", f"{DAY} 10:00:00", 1, "แย่มาก", "neg", [], [], "081-234-5678", "low")])
    con.commit(); con.close()
    r = client.get("/api/feedback/daily-report.print", params={"date": DAY})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/html")
    body = r.text
    assert DAY in body and "แย่มาก" in body and 'href="tel:0812345678"' in body
    assert "<script" not in body.lower()                                       # a static print page, not a client-rendered one


def test_date_to_extends_it_to_a_range(client):
    """date_to turns the "daily" report into a period report (week/month/custom
    range from the UI) — same engine, just a wider [date_from, date_to] window,
    and the comparison period becomes the immediately preceding one of equal
    length instead of "the same weekday last week"."""
    import core
    con = sqlite3.connect(str(core.DB_PATH))
    _seed(con, [
        ("BranchA", "2026-06-01 09:00:00", 1, "แย่วันแรก", "neg", [], [], None, "low"),
        ("BranchA", "2026-06-04 12:00:00", 5, "ดีกลางสัปดาห์", "pos", [], [], None, "low"),
        ("BranchA", "2026-06-07 23:00:00", 4, "ดีวันสุดท้าย", "pos", [], [], None, "low"),
        ("BranchA", "2026-05-31 12:00:00", 5, "ก่อนช่วง ไม่นับปัจจุบัน แต่นับช่วงก่อนหน้า", "pos", [], [], None, "low"),  # excluded from THIS period, counted in the prev one (its last day)
        ("BranchA", "2026-06-08 00:00:01", 1, "หลังช่วง ไม่นับ", "neg", [], [], None, "low"),   # just after: excluded
        ("BranchA", "2026-05-25 10:00:00", 3, "ช่วงก่อนหน้า", "neu", [], [], None, "low"),      # the prev period's first day
    ])
    con.commit(); con.close()
    with core.get_db() as con:
        d = D.compute_daily_report(con, "2026-06-01", date_to="2026-06-07")
    assert d["date_from"] == "2026-06-01" and d["date_to"] == "2026-06-07" and d["total"] == 3
    assert d["prev_period"] == {"date_from": "2026-05-25", "date_to": "2026-05-31", "total": 2}   # 7 days immediately before, not "same weekday"
    assert "แย่วันแรก" not in {q["text"] for q in d["recent_positive"]}
    # date_from == date_to (still the default single-day path) keeps the old weekday-fair comparison
    with core.get_db() as con:
        one_day = D.compute_daily_report(con, "2026-06-01", date_to="2026-06-01")
    assert one_day["prev_period"]["date_from"] == "2026-05-25" and one_day["prev_period"]["date_to"] == "2026-05-25"
    # a reversed range is swapped back into order rather than returning nothing
    with core.get_db() as con:
        swapped = D.compute_daily_report(con, "2026-06-07", date_to="2026-06-01")
    assert swapped["date_from"] == "2026-06-01" and swapped["date_to"] == "2026-06-07" and swapped["total"] == 3


def test_print_period_wording_and_date_shown_per_item(client):
    import core
    con = sqlite3.connect(str(core.DB_PATH))
    _seed(con, [("BranchA", "2026-06-01 09:00:00", 1, "แย่วันแรก", "neg", [], [], None, "low"),
                ("BranchA", "2026-06-07 10:00:00", 5, "ดีวันท้าย", "pos", [], [], None, "low")])
    con.commit(); con.close()
    body = client.get("/api/feedback/daily-report.print", params={"date": "2026-06-01", "date_to": "2026-06-07"}).text
    assert "รายงานตามช่วง 2026-06-01 – 2026-06-07" in body
    assert "ต้องติดตามช่วงนี้" in body and "ช่องทางที่มาช่วงนี้" in body       # "วันนี้" only makes sense for a single day
    assert "2026-06-01 ·" in body and "2026-06-07 ·" in body                # each item shows its own date across a multi-day range
    single = client.get("/api/feedback/daily-report.print", params={"date": "2026-06-01"}).text
    assert "รายงานประจำวัน 2026-06-01" in single and "ต้องติดตามวันนี้" in single


def test_print_groups_by_branch_only_on_the_all_branches_view(client):
    import core
    con = sqlite3.connect(str(core.DB_PATH))
    _seed(con, [("BranchA", f"{DAY} 09:00:00", 1, "แย่ที่ A", "neg", [], [], None, "low"),
                ("BranchA", f"{DAY} 10:00:00", 5, "ดีที่ A", "pos", [], [], None, "low"),
                ("BranchB", f"{DAY} 11:00:00", 2, "แย่ที่ B", "neg", [], [], None, "low")])
    con.commit(); con.close()
    all_body = client.get("/api/feedback/daily-report.print", params={"date": DAY}).text
    assert 'class="branch-h">BranchA' in all_body and 'class="branch-h">BranchB' in all_body      # grouped: a branch header per branch
    one_body = client.get("/api/feedback/daily-report.print", params={"date": DAY, "branch": "BranchA"}).text
    assert 'class="branch-h">' not in one_body and "แย่ที่ A" in one_body   # scoped: no grouping needed
    # detail sections added alongside branch grouping
    assert "ช่องทางที่มาวันนี้" in all_body and "อัตราตอบกลับ" in all_body and "พนักงานที่ถูกพูดถึง" in all_body


def test_new_issues_skips_themes_that_already_have_an_open_action(client):
    """A negative theme this period is "new" only if nobody has claimed it yet
    (feedback_actions §🔁) — once an open/doing action exists for that
    (branch, theme), it drops out of new_issues (it shows in open_issues instead)."""
    import core
    con = sqlite3.connect(str(core.DB_PATH))
    _seed(con, [
        ("BranchA", f"{DAY} 09:00:00", 1, "รอนานมาก", "neg", ["รอนาน"], [], None, "low"),
        ("BranchA", f"{DAY} 10:00:00", 2, "อาหารเย็นชืด", "neg", ["คุณภาพอาหาร"], [], None, "low"),
    ])
    _seed_action(con, branch_name="BranchA", theme="คุณภาพอาหาร", title="ตามครัวเรื่องอาหารเย็น", status="doing")
    con.commit(); con.close()
    with core.get_db() as con:
        d = D.compute_daily_report(con, DAY, branch="BranchA")
    new_themes = {it["theme"] for it in d["new_issues"]}
    assert new_themes == {"รอนาน"}                       # คุณภาพอาหาร already has a doing action → not "new"
    open_titles = {it["title"] for it in d["open_issues"]}
    assert open_titles == {"ตามครัวเรื่องอาหารเย็น"}


def test_resolved_issues_shows_days_to_fix_and_result_within_the_period(client):
    import core
    con = sqlite3.connect(str(core.DB_PATH))
    _seed_action(con, branch_name="BranchA", theme="รอนาน", title="เพิ่มพนักงานช่วงพีค",
                 created_at="2026-06-01 08:00:00", status="done", done_at="2026-06-10",
                 baseline_total=100, baseline_n=20, baseline_neg=10,
                 after_total=100, after_n=10, after_neg=2, result="better", verified_at="2026-07-10")
    _seed_action(con, branch_name="BranchA", theme="ที่จอดรถ", title="ปิดนอกช่วงรายงาน",
                 created_at="2026-05-01 08:00:00", status="done", done_at="2026-05-15")
    con.commit(); con.close()
    with core.get_db() as con:
        d = D.compute_daily_report(con, "2026-06-01", date_to="2026-06-30")
    assert len(d["resolved_issues"]) == 1
    it = d["resolved_issues"][0]
    assert it["title"] == "เพิ่มพนักงานช่วงพีค" and it["days_to_fix"] == 9 and it["result"] == "better"
    assert it["baseline_rate"] == 10.0 and it["after_rate"] == 2.0


def test_open_issues_are_a_live_snapshot_not_scoped_to_the_report_window(client):
    """Open/doing actions are an ongoing status, not something that happened
    "this period" — an old still-open action must show up on any period's report."""
    import core
    con = sqlite3.connect(str(core.DB_PATH))
    _seed_action(con, branch_name="BranchB", theme="ความสะอาด", title="ล้างพื้นครัวใหญ่",
                 created_at="2026-01-01 08:00:00", status="open", due="2026-01-10")
    con.commit(); con.close()
    with core.get_db() as con:
        d = D.compute_daily_report(con, DAY)
    it = next(x for x in d["open_issues"] if x["title"] == "ล้างพื้นครัวใหญ่")
    assert it["overdue"] is True and it["status"] == "open" and it["days_open"] > 100


def test_chronic_branches_flags_a_theme_repeating_across_periods(client):
    """A (branch, theme) with a negative voice in >=3 of the 4 most recent
    equal-length periods (this week + the 3 weeks before it) is "chronic"."""
    import core
    con = sqlite3.connect(str(core.DB_PATH))
    _seed(con, [
        ("BranchA", "2026-06-22 09:00:00", 1, "รอนานอีกแล้ว", "neg", ["รอนาน"], [], None, "low"),   # this week
        ("BranchA", "2026-06-15 09:00:00", 1, "รอนานอีกแล้ว", "neg", ["รอนาน"], [], None, "low"),   # 1 week before
        ("BranchA", "2026-06-01 09:00:00", 1, "รอนานอีกแล้ว", "neg", ["รอนาน"], [], None, "low"),   # 3 weeks before
        ("BranchA", "2026-06-08 09:00:00", 5, "สัปดาห์นี้ดี", "pos", [], [], None, "low"),           # 2 weeks before: no รอนาน negative
        ("BranchA", "2026-06-22 10:00:00", 1, "จานสกปรก", "neg", ["ความสะอาด"], [], None, "low"),    # only this week: not chronic
    ])
    con.commit(); con.close()
    with core.get_db() as con:
        d = D.compute_daily_report(con, "2026-06-22", date_to="2026-06-28")
    chronic = {(it["branch"], it["theme"]): it["periods"] for it in d["chronic_branches"]}
    assert chronic.get(("BranchA", "รอนาน")) == 3
    assert ("BranchA", "ความสะอาด") not in chronic


def test_chronic_status_reflects_whether_an_action_already_claims_it(client):
    """The print/xlsx editions mark each chronic (branch, theme) as already
    having an open/doing action or not — so a reader knows whether it still
    needs the "🚀 มอบหมาย" the in-app view offers (js/11-feedback.js)."""
    import core
    con = sqlite3.connect(str(core.DB_PATH))
    _seed(con, [
        ("BranchA", "2026-06-22 09:00:00", 1, "รอนานอีกแล้ว", "neg", ["รอนาน"], [], None, "low"),
        ("BranchA", "2026-06-15 09:00:00", 1, "รอนานอีกแล้ว", "neg", ["รอนาน"], [], None, "low"),
        ("BranchA", "2026-06-01 09:00:00", 1, "รอนานอีกแล้ว", "neg", ["รอนาน"], [], None, "low"),
        ("BranchA", "2026-06-22 10:00:00", 1, "จานสกปรก", "neg", ["ความสะอาด"], [], None, "low"),
        ("BranchA", "2026-06-15 10:00:00", 1, "จานสกปรกอีก", "neg", ["ความสะอาด"], [], None, "low"),
        ("BranchA", "2026-06-01 10:00:00", 1, "จานสกปรกอีกแล้ว", "neg", ["ความสะอาด"], [], None, "low"),
    ])
    _seed_action(con, branch_name="BranchA", theme="ความสะอาด", title="ล้างจาน", status="doing")
    con.commit(); con.close()
    body = client.get("/api/feedback/daily-report.print", params={"date": "2026-06-22", "date_to": "2026-06-28"}).text
    assert "รอนาน" in body and "ยังไม่มีงาน" in body and "ความสะอาด" in body and "มีงานอยู่แล้ว" in body
