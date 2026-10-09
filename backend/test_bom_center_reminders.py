# -*- coding: utf-8 -*-
"""Order reminders (bom_center/reminders.py + main._maybe_supply_reminder):
the day's duties per pilot branch and the queue, once a day after 07:00 on the
Thai clock, the cutoff note once after 13:15, sent only when Telegram is set up."""
from datetime import date, datetime

from bom_center import reminders as RM
from bom_center.clock import BKK
from test_bom_center_s11 import _report, udon  # noqa: F401  (branch 1 = อุดรธานี)
from test_bom_center_s4b import con  # noqa: F401

MON = date(2026, 10, 5)        # round 1's cutoff day (Wednesday's delivery, 13:15)
TUE = date(2026, 10, 6)        # that cutoff has passed
WED = date(2026, 10, 7)        # no cutoff today


def _line(rec):
    return next(b for b in rec["branches"] if b["name"] == "อุดรธานี")


def test_thai_date():
    assert RM.thai_date(TUE) == "อังคาร 6 ต.ค. 2569"


def test_morning_names_the_duties_and_follows_them(udon):
    c = udon
    rec = RM.morning(c, today=MON, app_url="https://x.test")
    assert rec["any_work"] is True and "จันทร์ 5 ต.ค. 2569" in rec["text"]
    line = _line(rec)
    assert line["due_today"] and line["pending"]
    assert "ส่งใบสั่งซื้อรอบส่งวันพุธ 7/10 ภายในวันนี้ 13:15" in line["text"]
    assert "https://x.test/" in rec["text"] and rec["queue"]["count"] == 0
    c.execute("INSERT INTO bc_order(branch_id, cycle_date, round, status) VALUES(1, '2026-10-05', '1', 'submitted')")
    rec = RM.morning(c, today=MON)
    line = _line(rec)
    assert "ส่งใบสั่งซื้อรอบส่งวันพุธ 7/10 แล้ว" in line["text"] and line["pending"] is False
    assert rec["queue"]["count"] == 1 and "คลัง" in rec["text"] and "ค้างคิว 1 ใบ" in rec["text"]
    off = RM.morning(c, today=WED)
    assert "ไม่มีกำหนดส่งใบสั่งซื้อวันนี้ (ถัดไป รอบส่งวันเสาร์ 10/10 ภายในวันพฤหัส 13:15)" in _line(off)["text"]


def test_a_missed_cutoff_is_named_with_the_late_fee(udon):
    rec = RM.morning(udon, today=TUE)                     # Tuesday morning: Wednesday's cutoff passed yesterday
    line = _line(rec)
    assert line["pending"] and "ยังไม่ส่งใบสั่งซื้อรอบส่งวันพุธ 7/10 (ล่าช้า มีค่าปรับ)" in line["text"]
    c = udon
    c.execute("INSERT INTO bc_order(branch_id, cycle_date, round, status, late) VALUES(1, '2026-10-05', '1', 'submitted', 1)")
    rec = RM.morning(c, today=TUE)
    assert "ส่งล่าช้า 1 ใบ (มีค่าปรับ)" in rec["text"] and rec["queue"]["late_orders"] == 1


def test_cutoff_note_only_when_someone_missed_it_or_orders_wait(udon):
    c = udon
    after = datetime(2026, 10, 5, 13, 20, tzinfo=BKK)
    s = RM.cutoff(c, today=MON, app_url="https://x.test", now=after)
    assert s["missed"] == [{"branch_id": 1, "name": "อุดรธานี", "delivery": "2026-10-07", "delivery_th": "วันพุธ 7/10"}]
    assert "<b>ตัดรอบ 13:15 · จันทร์ 5 ต.ค. 2569</b>" in s["text"]
    assert "อุดรธานี: ยังไม่ส่งใบสั่งซื้อรอบส่งวันพุธ 7/10 (ล่าช้า มีค่าปรับ)" in s["text"] and "https://x.test/" in s["text"]
    assert RM.cutoff(c, today=MON, now=datetime(2026, 10, 5, 13, 0, tzinfo=BKK)) is None      # before the cutoff: nothing missed
    c.execute("INSERT INTO bc_order(branch_id, cycle_date, round, status) VALUES(1, '2026-10-05', '1', 'processed')")
    assert RM.cutoff(c, today=MON, now=after) is None      # sent in time and already processed: nothing to say
    c.execute("UPDATE bc_order SET status='submitted'")
    q = RM.cutoff(c, today=MON, now=after)
    assert q["missed"] == [] and q["count"] == 1 and "คลัง: ใบสั่งซื้อค้างคิว 1 ใบ ระบุยอดส่งจริงภายใน 14:00 ของวันตัดรอบ" in q["text"]


def test_trigger_once_a_day_after_seven_and_the_cutoff_note_once_after_1315(client, monkeypatch):
    import main
    monkeypatch.setenv("OSHINEI_TG_TOKEN", "t")
    monkeypatch.setenv("OSHINEI_TG_CHAT_ID", "c")
    sent = []

    def send(text):
        sent.append(text)
        return True
    stub = {"today": "2026-10-06", "text": "morning", "any_work": True, "branches": [], "queue": {"count": 0, "late": False}}
    monkeypatch.setattr(RM, "morning", lambda con, today=None, app_url="": dict(stub))
    monkeypatch.setattr(RM, "cutoff", lambda con, today=None, app_url="", now=None:
                        {"today": "2026-10-06", "text": "cutoff", "count": 1, "late": False})
    main._usage_meta_set("supply_reminder_day", "")
    main._usage_meta_set("supply_sla_day", "")
    assert main._maybe_supply_reminder(now=datetime(2026, 10, 6, 6, 30), send=send) is None and sent == []
    rec = main._maybe_supply_reminder(now=datetime(2026, 10, 6, 7, 30), send=send)
    assert rec and rec["sent"] is True and rec["telegram_configured"] is True and sent == ["morning"]
    assert main._maybe_supply_reminder(now=datetime(2026, 10, 6, 8, 0), send=send) is None and sent == ["morning"]
    assert main._maybe_supply_reminder(now=datetime(2026, 10, 6, 10, 30), send=send) is None and sent == ["morning"]
    assert main._maybe_supply_reminder(now=datetime(2026, 10, 6, 13, 10), send=send) is None and sent == ["morning"]
    rec2 = main._maybe_supply_reminder(now=datetime(2026, 10, 6, 13, 20), send=send)
    assert rec2 and rec2["text"] == "cutoff" and sent == ["morning", "cutoff"]
    assert main._maybe_supply_reminder(now=datetime(2026, 10, 6, 14, 0), send=send) is None and sent == ["morning", "cutoff"]
    # a quiet day: stored, nothing sent
    main._usage_meta_set("supply_reminder_day", "")
    monkeypatch.setattr(RM, "morning", lambda con, today=None, app_url="": dict(stub, any_work=False))
    rec3 = main._maybe_supply_reminder(now=datetime(2026, 10, 7, 7, 30), send=send)
    assert rec3 and rec3["sent"] is False and sent == ["morning", "cutoff"]


def test_unconfigured_telegram_stores_but_does_not_send(client, monkeypatch):
    import main
    monkeypatch.delenv("OSHINEI_TG_TOKEN", raising=False)
    monkeypatch.delenv("OSHINEI_TG_CHAT_ID", raising=False)
    monkeypatch.setattr(RM, "morning", lambda con, today=None, app_url="": {"today": "2026-10-13", "text": "m", "any_work": True, "branches": [], "queue": {"count": 0, "late": False}})
    main._usage_meta_set("supply_reminder_day", "")
    rec = main._maybe_supply_reminder(now=datetime(2026, 10, 13, 7, 0), send=lambda t: (_ for _ in ()).throw(AssertionError("must not send")))
    assert rec and rec["telegram_configured"] is False and rec["sent"] is False
