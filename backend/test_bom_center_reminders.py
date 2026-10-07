# -*- coding: utf-8 -*-
"""Order-day reminders (bom_center/reminders.py + main._maybe_supply_reminder):
the day's duties per pilot branch and the queue, once a day after 07:00 on the
Thai clock, the queue note once after 10:00, sent only when Telegram is set up."""
from datetime import date, datetime

from bom_center import reminders as RM
from bom_center import stock as ST
from test_bom_center_s11 import _report, udon  # noqa: F401  (branch 1 = อุดรธานี)
from test_bom_center_s4b import con  # noqa: F401
from test_bom_center_todo import PACK

TUE = date(2026, 10, 6)        # round 1 in the default calendar
WED = date(2026, 10, 7)        # no round


def test_thai_date():
    assert RM.thai_date(TUE) == "อังคาร 6 ต.ค. 2569"


def test_morning_names_the_duties_and_follows_them(udon):
    c = udon
    rec = RM.morning(c, today=TUE, app_url="https://x.test")
    assert rec["any_work"] is True and "อังคาร 6 ต.ค. 2569" in rec["text"]
    line = next(b for b in rec["branches"] if b["name"] == "อุดรธานี")
    assert line["order_day"] and line["pending"] and "วันสั่งรอบ 1" in line["text"]
    assert "ยังไม่ตรวจนับ" in line["text"] and "ยังไม่ส่งใบสั่งซื้อรอบ 1" in line["text"]
    assert "https://x.test/" in rec["text"] and rec["queue"]["count"] == 0
    ST.save(c, 1, PACK, "u", submit=True, count_date="2026-10-06")
    c.execute("INSERT INTO bc_order(branch_id, cycle_date, round, status) VALUES(1, '2026-10-06', '1', 'submitted')")
    rec = RM.morning(c, today=TUE)
    line = next(b for b in rec["branches"] if b["name"] == "อุดรธานี")
    assert "ตรวจนับแล้ว" in line["text"] and "ส่งใบสั่งซื้อรอบ 1 แล้ว" in line["text"] and line["pending"] is False
    assert rec["queue"]["count"] == 1 and "Sales Support" in rec["text"] and "ค้างคิว 1 ใบ" in rec["text"]
    off = RM.morning(c, today=WED)
    assert "ไม่มีวันสั่ง" in next(b for b in off["branches"] if b["name"] == "อุดรธานี")["text"]


def test_sla_only_when_orders_wait(udon):
    c = udon
    assert RM.sla(c, today=TUE) is None
    c.execute("INSERT INTO bc_order(branch_id, cycle_date, round, status) VALUES(1, '2026-10-06', '1', 'submitted')")
    s = RM.sla(c, today=TUE, app_url="https://x.test")
    assert s["count"] == 1 and "ค้างคิว 1 ใบ" in s["text"] and s["late"] is False and "https://x.test/" in s["text"]
    assert RM.sla(c, today=WED)["late"] is True


def test_trigger_once_a_day_after_seven_and_the_queue_once_after_ten(client, monkeypatch):
    import main
    monkeypatch.setenv("OSHINEI_TG_TOKEN", "t")
    monkeypatch.setenv("OSHINEI_TG_CHAT_ID", "c")
    sent = []

    def send(text):
        sent.append(text)
        return True
    stub = {"today": "2026-10-06", "text": "morning", "any_work": True, "branches": [], "queue": {"count": 0, "late": False}}
    monkeypatch.setattr(RM, "morning", lambda con, today=None, app_url="": dict(stub))
    monkeypatch.setattr(RM, "sla", lambda con, today=None, app_url="": {"today": "2026-10-06", "text": "sla", "count": 1, "late": False})
    main._usage_meta_set("supply_reminder_day", "")
    main._usage_meta_set("supply_sla_day", "")
    assert main._maybe_supply_reminder(now=datetime(2026, 10, 6, 6, 30), send=send) is None and sent == []
    rec = main._maybe_supply_reminder(now=datetime(2026, 10, 6, 7, 30), send=send)
    assert rec and rec["sent"] is True and rec["telegram_configured"] is True and sent == ["morning"]
    assert main._maybe_supply_reminder(now=datetime(2026, 10, 6, 8, 0), send=send) is None and sent == ["morning"]
    rec2 = main._maybe_supply_reminder(now=datetime(2026, 10, 6, 10, 30), send=send)
    assert rec2 and rec2["text"] == "sla" and sent == ["morning", "sla"]
    assert main._maybe_supply_reminder(now=datetime(2026, 10, 6, 11, 0), send=send) is None and sent == ["morning", "sla"]
    # a quiet day: stored, nothing sent
    main._usage_meta_set("supply_reminder_day", "")
    monkeypatch.setattr(RM, "morning", lambda con, today=None, app_url="": dict(stub, any_work=False))
    rec3 = main._maybe_supply_reminder(now=datetime(2026, 10, 7, 7, 30), send=send)
    assert rec3 and rec3["sent"] is False and sent == ["morning", "sla"]


def test_unconfigured_telegram_stores_but_does_not_send(client, monkeypatch):
    import main
    monkeypatch.delenv("OSHINEI_TG_TOKEN", raising=False)
    monkeypatch.delenv("OSHINEI_TG_CHAT_ID", raising=False)
    monkeypatch.setattr(RM, "morning", lambda con, today=None, app_url="": {"today": "2026-10-13", "text": "m", "any_work": True, "branches": [], "queue": {"count": 0, "late": False}})
    main._usage_meta_set("supply_reminder_day", "")
    rec = main._maybe_supply_reminder(now=datetime(2026, 10, 13, 7, 0), send=lambda t: (_ for _ in ()).throw(AssertionError("must not send")))
    assert rec and rec["telegram_configured"] is False and rec["sent"] is False
