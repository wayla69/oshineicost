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
    assert d["date"] == DAY and d["total"] == 3 and d["sentiment"] == {"pos": 1, "neg": 2, "neu": 0}
    assert d["severe"] == 1 and "3 เสียง" in d["headline"] and "⚠ ร้ายแรง 1" in d["headline"]
    assert any(m["menu"] == "แซลมอน" for m in d["menus"]) and any(t["theme"] == "รอนาน" for t in d["themes"])
    neg_texts = {it["text"] for it in d["recent_negative"]}
    assert neg_texts == {"รอนานมาก บริการแย่", "อาหารมีปัญหา"}
    sev = next(it for it in d["recent_negative"] if it["text"] == "อาหารมีปัญหา")
    assert sev["severity"] == "high" and sev["contact"] == "081-999-8888"
    assert d["recent_positive"][0]["text"] == "แซลมอนสดมาก"
    assert d["prev_week_same_day"]["date"] == "2026-06-08"                     # 7 days earlier


def test_empty_day_headline(client):
    import core
    with core.get_db() as con:
        d = D.compute_daily_report(con, DAY, branch="BranchZ")
    assert d["total"] == 0 and d["headline"] == f"ไม่มีเสียงลูกค้าเข้ามาในวันที่ {DAY} ที่สาขา BranchZ"


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
    assert "date" in j_default and "headline" in j_default


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
