# -*- coding: utf-8 -*-
"""🔁 ปิดวงจร — an action on a theme keeps a 28-day baseline, is measured again
after it is done and verified better / same / worse; the weekly digest counts
the window against the one before; the fetcher sends it on Mondays; the
Management Report carries it as §12."""
import sqlite3
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).parent.resolve()
sys.path.insert(0, str(HERE))

import fetch_feedback as FF  # noqa: E402
import notify  # noqa: E402
from routers import feedback_actions as A  # noqa: E402
from test_feedback_sources import _args, _seed_db  # noqa: E402


def _wipe(con, table):
    """Empty a table if it exists — never create a stub that would shadow the real DDL."""
    if con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
        con.execute(f"DELETE FROM {table}")


@pytest.fixture(autouse=True)
def _clean(request, monkeypatch):
    yield
    if "client" not in request.fixturenames:
        return
    import core
    con = sqlite3.connect(str(core.DB_PATH))
    _wipe(con, "customer_feedback")
    _wipe(con, "feedback_action")
    con.commit(); con.close()
    try:
        import main
        main.invalidate_cache()
    except Exception:
        pass


def _voices(client, rows):
    body = [{"branch": b, "date": d, "source": "Google", "rating": r, "text": t, "external_id": f"v:{i}:{d}:{b}"}
            for i, (b, d, r, t) in enumerate(rows)]
    r = client.post("/api/feedback/upload", json={"rows": body, "by": "test"})
    assert r.status_code == 200, r.text
    return r.json()


def test_action_baseline_after_window_and_verdict(client, monkeypatch):
    monkeypatch.setattr(A, "_today", lambda: "2026-09-05")
    # 28 days before 2026-09-05 = 2026-08-08 … 2026-09-04: BranchA has 5 voices, 2 negative about waiting
    _voices(client, [("BranchA", "2026-08-10", 1, "รอนานมาก พนักงานไม่สนใจ"), ("BranchA", "2026-08-20", 2, "รอคิวนาน อาหารช้า"),
                     ("BranchA", "2026-08-25", 5, "แซลมอนสดมาก"), ("BranchA", "2026-09-01", 4, "บริการดี"), ("BranchA", "2026-09-03", 5, "อร่อย"),
                     ("BranchB", "2026-08-15", 1, "รอนานมาก"), ("BranchA", "2026-07-01", 1, "รอนาน (นอกหน้าต่าง)")])
    assert client.post("/api/feedback/actions", json={"title": ""}).status_code == 400
    r = client.post("/api/feedback/actions", json={"title": "เพิ่มคนเติมไลน์ 18–20 น.", "branch": "BranchA", "theme": "รอนาน",
                                                   "owner": "เก่ง", "due": "2026-09-01", "by": "owner"}).json()
    assert r["status"] == "open" and r["created_by"] == "owner" and r["overdue"] is True
    assert (r["baseline_from"], r["baseline_to"]) == ("2026-08-08", "2026-09-04")
    assert (r["baseline_total"], r["baseline_n"], r["baseline_neg"]) == (5, 2, 2) and r["baseline_rate"] == 40.0
    aid = r["id"]
    lst = client.get("/api/feedback/actions").json()
    assert lst["counts"]["open"] == 1 and lst["counts"]["overdue"] == 1 and lst["window_days"] == 28
    assert client.get("/api/feedback/actions", params={"branch": "BranchB"}).json()["actions"] == []
    assert client.post("/api/feedback/actions/999", json={"status": "doing"}).status_code == 404
    assert client.post(f"/api/feedback/actions/{aid}", json={"status": "bogus"}).status_code == 400
    assert client.post(f"/api/feedback/actions/{aid}", json={"status": "doing", "due": "2026-09-10"}).json()["overdue"] is False
    # done today → the after-window runs 09-06 … 10-03; partial while it runs
    d = client.post(f"/api/feedback/actions/{aid}", json={"status": "done", "note": "เพิ่มคน 1 คน"}).json()
    assert d["done_at"] == "2026-09-05" and d["result"] is None and d["after"]["total"] == 0 and d["after_days_left"] == 28
    monkeypatch.setattr(A, "_today", lambda: "2026-09-20")
    _voices(client, [("BranchA", "2026-09-10", 5, "เร็วขึ้นมาก"), ("BranchA", "2026-09-12", 4, "ดี"), ("BranchA", "2026-09-15", 2, "รอนานอยู่")])
    d = client.get("/api/feedback/actions").json()["actions"][0]
    assert d["result"] is None and d["after"]["total"] == 3 and d["after"]["neg"] == 1 and d["after_days_left"] == 13
    # the window has passed: 1 negative in 10 voices = 10 per 100 vs 40 before → better, stored once
    monkeypatch.setattr(A, "_today", lambda: "2026-10-04")
    _voices(client, [("BranchA", f"2026-09-{dd:02d}", 5, "ดีมาก") for dd in (21, 22, 23, 24, 25, 26, 27)])
    d = client.get("/api/feedback/actions").json()["actions"][0]
    assert d["result"] == "better" and d["verified_at"] == "2026-10-04" and d["after_rate"] == 10.0 and d["after"]["total"] == 10
    assert client.get("/api/feedback/actions").json()["counts"]["better"] == 1
    # reopening voids the verdict; dropping keeps it out of the counts
    d = client.post(f"/api/feedback/actions/{aid}", json={"status": "open"}).json()
    assert d["result"] is None and d["done_at"] is None and d["after"] is None
    d = client.post(f"/api/feedback/actions/{aid}", json={"status": "dropped"}).json()
    assert d["status"] == "dropped" and client.get("/api/feedback/actions").json()["counts"]["open"] == 0


def test_verdict_rules():
    v = A._verdict
    assert v({"total": 10, "neg": 4}, {"total": 10, "neg": 1}) == "better"
    assert v({"total": 10, "neg": 4}, {"total": 10, "neg": 4}) == "same"
    assert v({"total": 10, "neg": 2}, {"total": 10, "neg": 5}) == "worse"
    assert v({"total": 10, "neg": 2}, {"total": 0, "neg": 0}) == "no_data"
    assert v({"total": 0, "neg": 0}, {"total": 5, "neg": 1}) == "worse"
    assert v({"total": 0, "neg": 0}, {"total": 5, "neg": 0}) == "same"
    assert A._rate({"total": 8, "neg": 2}) == 25.0 and A._rate({"total": 0, "neg": 0}) is None


def test_digest_counts_the_window_against_the_one_before(client, monkeypatch):
    monkeypatch.setattr(A, "_today", lambda: "2026-09-05")
    _voices(client, [("BranchA", "2026-09-01", 1, "รอนานมาก พนักงานไม่สนใจ"), ("BranchA", "2026-09-02", 5, "แซลมอนสดมาก"),
                     ("BranchA", "2026-09-03", 2, "ของหมดเร็ว"), ("BranchB", "2026-09-04", 5, "บริการดี"),
                     ("BranchA", "2026-08-25", 5, "ดี"), ("BranchA", "2026-08-26", 4, "ดี"), ("BranchB", "2026-08-27", 1, "รอนาน")])
    r = client.post("/api/feedback/actions", json={"title": "x", "branch": "BranchA", "theme": "รอนาน", "due": "2026-09-01"}).json()
    client.post(f"/api/feedback/actions/{r['id']}", json={"status": "done"})
    d = client.get("/api/feedback/digest", params={"days": 7}).json()
    assert d["range"] == ["2026-08-30", "2026-09-05"] and d["prev_range"] == ["2026-08-23", "2026-08-29"]
    assert d["voices"] == 4 and d["prev_voices"] == 3 and d["negatives"] == 2 and d["positives"] == 2
    assert d["neg_pct"] == 50.0 and d["prev_neg_pct"] == 33.3
    assert d["themes"][0]["theme"] in ("รอนาน", "บริการ", "ปริมาณ/ของหมด")
    assert any(t["theme"] == "รอนาน" and t["prev_n"] == 1 for t in d["themes"])
    assert d["branches_watch"][0]["branch"] == "BranchA" and d["branches_watch"][0]["neg"] == 2
    assert d["actions"]["done"] == 1 and d["actions"]["open"] == 0 and d["actions"]["items"][0]["title"] == "x"
    assert d["unreplied"] == 3 and d["sla_breached"] == 3          # the negatives wait for an answer, all older than a day
    d1 = client.get("/api/feedback/digest", params={"days": 7, "branch": "BranchB"}).json()
    assert d1["voices"] == 1 and d1["prev_voices"] == 1 and d1["actions"]["done"] == 0
    dg = client.get("/api/feedback/digest", params={"days": 7, "group": "franchise"}).json()
    assert dg["voices"] == 1                                         # BranchB is the franchise branch of the fixture
    text = notify.weekly_digest(d, "https://app")
    assert "สรุปเสียงลูกค้า 7 วัน" in text and "เสียง <b>4</b> (▲1 ✓)" in text and "ลบ <b>50%</b>" in text
    assert "BranchA" in text and "งานแก้" in text and "https://app/" in text
    # the report carries the same block as §12
    j = client.get("/api/reports/management").json()
    assert "feedback" in j and (j["feedback"] is None or "voices" in j["feedback"])


def test_fetcher_sends_the_digest_on_mondays_and_on_demand(tmp_path, monkeypatch):
    # Pin the digest clock: the seeded voice is dated, so against the REAL today
    # it falls out of the 7-day window and the digest goes empty.
    monkeypatch.setattr(A, "_today", lambda: "2026-09-05")
    db = tmp_path / "local.db"; _seed_db(db)
    sink = FF.DbSink(db)
    sink.upload([{"branch": "ขอนแก่น", "date": "2026-09-03", "source": "Google", "rating": 1, "text": "รอนานมาก", "external_id": "d1"}], by="t")
    sent, lines = [], []
    notifier = lambda text: (sent.append(text), True)[1]            # noqa: E731 — Telegram accepted it
    monkeypatch.setattr(FF, "_weekday", lambda: 2)                   # a Wednesday: nothing unless asked
    assert FF._digest_step(_args(), sink, notifier, lines.append) is False and not sent
    assert FF._digest_step(_args(digest=True), sink, notifier, lines.append) is True
    assert len(sent) == 1 and "สรุปเสียงลูกค้า" in sent[0] and "รอนาน 1" in sent[0]   # one voice: the theme line, no branch league yet
    monkeypatch.setattr(FF, "_weekday", lambda: 0)                   # Monday: sent after the run
    assert FF._digest_step(_args(), sink, notifier, lines.append) is True and len(sent) == 2
    assert FF._digest_step(_args(dry_run=True), sink, notifier, lines.append) is False
    assert any("digest:" in ln for ln in lines)
    assert sink.digest(7)["voices"] == 1


def test_fetcher_sends_the_daily_report_every_run_unless_opted_out(tmp_path):
    """Unlike the weekly digest (Mondays only), _daily_report_step fires on
    every run — the scheduled fetch already runs once a day, so this rides
    that same cadence rather than needing its own day-of-week gate."""
    db = tmp_path / "local.db"; _seed_db(db)
    sink = FF.DbSink(db)
    sink.upload([{"branch": "ขอนแก่น", "date": "2026-09-03", "source": "Google", "rating": 1, "text": "รอนานมาก", "external_id": "d1"}], by="t")
    sent, lines = [], []
    notifier = lambda text: (sent.append(text), True)[1]            # noqa: E731
    assert FF._daily_report_step(_args(no_daily_report=False, date="2026-09-03"), sink, notifier, lines.append) is True
    assert len(sent) == 1 and "รายงานเสียงลูกค้า 2026-09-03" in sent[0] and "1 เสียง" in sent[0]
    assert FF._daily_report_step(_args(date="2026-09-03"), sink, notifier, lines.append) is False   # default (_args): opted out
    assert len(sent) == 1                                            # the opt-out really opts out
    assert FF._daily_report_step(_args(no_daily_report=False, dry_run=True, date="2026-09-03"), sink, notifier, lines.append) is False
    assert any("daily report:" in ln for ln in lines)
    # a theme with no open/doing action shows up as "new" in the pushed text
    sink.upload([{"branch": "ขอนแก่น", "date": "2026-09-03", "source": "Google", "rating": 1,
                 "text": "รอคิวนานมาก", "external_id": "d2", "themes_extra": ["รอนาน"], "sentiment_hint": "neg"}], by="t")
    assert FF._daily_report_step(_args(no_daily_report=False, date="2026-09-03"), sink, notifier, lines.append) is True
    assert "ปัญหาใหม่ยังไม่มีคนตาม" in sent[-1] and "รอนาน" in sent[-1]


def test_fetcher_sla_alert_pings_once_per_case(tmp_path):
    """A case that just crossed the SLA gets an immediate ping (its own
    --no-sla-alert flag, separate from --no-alert's new-negatives alert) and
    is acked so it never re-fires on a later run."""
    db = tmp_path / "local.db"; _seed_db(db)
    sink = FF.DbSink(db)
    sink.upload([{"branch": "ขอนแก่น", "date": "2026-01-01", "source": "Google", "rating": 1, "text": "รอนานมาก", "external_id": "s1"}], by="t")
    sent, lines = [], []
    notifier = lambda text: (sent.append(text), True)[1]            # noqa: E731
    assert FF._sla_alert_step(_args(no_sla_alert=False), sink, notifier, lines.append) is True
    assert len(sent) == 1 and "เกิน SLA 1 รายการ" in sent[0] and "รอนานมาก" in sent[0] and "ขอนแก่น" in sent[0]
    assert FF._sla_alert_step(_args(no_sla_alert=False), sink, notifier, lines.append) is False   # acked: nothing left to alert
    assert len(sent) == 1
    assert FF._sla_alert_step(_args(), sink, notifier, lines.append) is False   # default (_args): opted out
    assert any("sla alert:" in ln for ln in lines)
