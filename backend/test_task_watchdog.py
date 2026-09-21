"""task_watchdog — what counts as a problem, why it failed, and the app side.

Born from `Oshinei Push Invisible` exiting 1 for 15 days (2026-09-06 -> 09-21)
with nobody told. The shapes below are that incident and the ones around it.
"""
import datetime as dt
import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent))
import core  # noqa: E402
import main  # noqa: E402
import task_watchdog as tw  # noqa: E402

NOW = dt.datetime(2026, 9, 21, 12, 17)


def daily(hhmm, since="2026-07-20"):
    return {"kind": "MSFT_TaskDailyTrigger", "start": f"{since}T{hhmm}:00+07:00", "days_interval": 1, "enabled": True}


def task(name="Oshinei X", state="Ready", last="2026-09-21T10:45:01", result=0, triggers=None, action=""):
    return {"name": name, "state": state, "last_run": last, "result": result,
            "triggers": [daily("10:45")] if triggers is None else triggers, "action": action}


# ── judge ────────────────────────────────────────────────────────────────────
def test_a_green_task_that_ran_at_its_slot_is_ok():
    assert tw.judge(task(), NOW)["level"] == "ok"


def test_exit_1_is_a_failure_and_says_where_to_look():
    f = tw.judge(task(result=1), NOW)
    assert f["level"] == "fail" and "log" in f["meaning"]


def test_the_window_closed_code_is_named_not_shown_as_a_number():
    f = tw.judge(task(result=3221225786), NOW)          # 0xC000013A
    assert f["level"] == "fail" and "window was closed" in f["meaning"]


def test_terminated_names_the_time_limit_not_just_a_person():
    # 2026-09-21: the healed Auto Sync ran 60 min (MSSQL drops + a busy PC) and Task Scheduler cut it at
    # its PT1H limit - yet the batch finished the Fly push and the local apply on its own. "someone
    # stopped it" would have sent the owner looking for a person.
    f = tw.judge(task(result=267014), NOW)
    assert f["level"] == "fail" and "time limit" in f["meaning"] and "read its log" in f["meaning"]


def test_an_unknown_code_is_shown_in_hex():
    assert "0x80070005" in tw.judge(task(result=0x80070005), NOW)["meaning"]


def test_running_is_fine_until_it_has_run_too_long():
    assert tw.judge(task(state="Running", last="2026-09-21T12:00:00", result=267009), NOW)["level"] == "ok"
    assert tw.judge(task(state="Running", last="2026-09-21T08:00:00", result=267009), NOW)["level"] == "stuck"


def test_a_disabled_task_is_never_a_problem_even_with_an_old_red_result():
    assert tw.judge(task(state="Disabled", last="2026-06-05T09:00:00", result=1), NOW)["level"] == "disabled"


def test_the_morning_the_pc_slept_through_is_missed_not_stale():
    f = tw.judge(task(last="2026-09-20T06:00:01", triggers=[daily("06:00")]), NOW)
    assert f["level"] == "missed" and "06:00" in f["meaning"]


def test_two_days_without_a_run_is_stale():
    f = tw.judge(task(last="2026-09-18T06:00:01", triggers=[daily("06:00")]), NOW)
    assert f["level"] == "stale" and "3 days" in f["meaning"]


def test_a_slot_inside_the_grace_window_is_not_yet_missed():
    # 12:17 now, slot 11:15 -> only 62 min ago: it may still be starting
    assert tw.judge(task(last="2026-09-20T11:15:01", triggers=[daily("11:15")]), NOW)["level"] == "ok"


def test_with_two_daily_slots_the_later_one_decides():
    trig = [daily("12:00"), daily("16:00")]
    now = dt.datetime(2026, 9, 21, 18, 0)
    assert tw.judge(task(last="2026-09-21T16:00:02", triggers=trig), now)["level"] == "ok"
    assert tw.judge(task(last="2026-09-21T12:00:02", triggers=trig), now)["level"] == "missed"


def test_a_manual_run_after_the_slot_counts():
    assert tw.judge(task(last="2026-09-21T12:08:29"), NOW)["level"] == "ok"


def test_a_task_created_today_is_not_blamed_for_yesterday():
    t = task(last=None, result=tw.NEVER_RAN, triggers=[daily("17:00", since="2026-09-21")])
    assert tw.judge(t, NOW)["level"] == "ok"


def test_a_task_without_a_daily_trigger_is_only_judged_on_its_exit_code():
    assert tw.judge(task(last="2026-08-01T00:00:00", triggers=[{"kind": "MSFT_TaskLogonTrigger", "enabled": True}]), NOW)["level"] == "ok"


# ── how long, and when to say it again ───────────────────────────────────────
def test_the_first_sighting_is_remembered_so_day_15_says_15():
    day1 = dt.datetime(2026, 9, 6, 11, 15)
    rep, since = tw.build_report([task(name="Oshinei Push Invisible", last="2026-09-06T10:45:02", result=1)], day1, {})
    assert rep["tasks"][0]["days"] == 0
    rep, since = tw.build_report([task(name="Oshinei Push Invisible", last="2026-09-21T10:45:02", result=1)], NOW, since)
    assert rep["tasks"][0]["days"] == 15 and not rep["ok"] and rep["problems"] == 1
    rep, since = tw.build_report([task(name="Oshinei Push Invisible")], NOW, since)
    assert rep["ok"] and since == {}                      # healed -> forgotten


def test_the_watchdog_does_not_judge_itself():
    rep, _ = tw.build_report([task(name=tw.SELF_NAME, result=1)], NOW, {})
    assert rep["tasks"] == [] and rep["ok"]


def test_notify_on_change_then_every_20_hours_then_once_on_recovery():
    red, green = {"signature": "Oshinei X=fail"}, {"signature": ""}
    assert tw.should_notify(red, {}, NOW)                                                  # new
    told = {"notified_signature": "Oshinei X=fail", "notified_at": "2026-09-21T11:15:00"}
    assert not tw.should_notify(red, told, NOW)                                            # told an hour ago
    assert tw.should_notify(red, told, NOW + dt.timedelta(hours=20))                       # still red tomorrow
    assert tw.should_notify(green, told, NOW)                                              # recovered: say so once
    assert not tw.should_notify(green, {"notified_signature": ""}, NOW)                    # green and quiet


# ── healing: re-run what is safe to run late ─────────────────────────────────
BACKUP, FEEDBACK, AUTOSYNC = "Oshinei DB Backup", "Oshinei Feedback Sync", "Oshinei Auto Sync"


class FakeScheduler:
    """Task Scheduler + a clock that only moves when the watchdog sleeps."""

    def __init__(self, tasks, now=NOW, run_seconds=120, exit_code=0):
        self.tasks = {t["name"]: dict(t) for t in tasks}
        self.now, self.run_seconds, self.exit_code = now, run_seconds, exit_code
        self.started, self.ends = [], {}

    def clock(self):
        return self.now

    def sleep(self, seconds):
        self.now += dt.timedelta(seconds=seconds)
        for name, end in list(self.ends.items()):
            if self.now >= end:
                self.tasks[name].update(state="Ready", result=self.exit_code)
                del self.ends[name]

    def start(self, name):
        self.started.append((name, self.now.strftime("%H:%M")))
        self.tasks[name].update(state="Running", result=tw.RUNNING, last_run=self.now.strftime("%Y-%m-%dT%H:%M:%S"))
        self.ends[name] = self.now + dt.timedelta(seconds=self.run_seconds)
        return True

    def read(self):
        return [dict(t) for t in self.tasks.values()]

    def heal(self, tried=None):
        tried = {} if tried is None else tried
        findings = [tw.judge(t, self.now) for t in self.read()]
        return tw.heal(findings, tried, read=self.read, start=self.start, clock=self.clock, sleep=self.sleep), tried


def slept_through_the_morning():
    return [task(name="Oshinei Auto Sync", last="2026-09-20T06:00:01", triggers=[daily("06:00")]),
            task(name=BACKUP, last="2026-09-20T07:30:01", triggers=[daily("07:30")]),
            task(name=FEEDBACK, last="2026-09-20T09:00:01", triggers=[daily("09:00")]),
            task(name="Oshinei Sync Live (Fly)", last="2026-09-20T16:00:01", triggers=[daily("12:00"), daily("16:00")])]


def test_heal_at_1115_reruns_the_two_short_tasks_but_never_a_40_minute_sync_before_noon():
    sch = FakeScheduler(slept_through_the_morning(), now=dt.datetime(2026, 9, 21, 11, 15))
    notes, tried = sch.heal()
    assert [n for n, _ in sch.started] == [BACKUP, FEEDBACK]                  # Auto Sync also missed - 12:00 is too close
    assert sch.started[1][1] > sch.started[0][1]                              # the second waited for the first
    assert notes == {BACKUP: "re-run by the watchdog at 11:15", FEEDBACK: "re-run by the watchdog at 11:17",
                     AUTOSYNC: "re-run held back - Oshinei Sync Live (Fly) starts at 12:00"}
    assert tried == {BACKUP: "2026-09-21", FEEDBACK: "2026-09-21"}            # a held-back sync keeps its attempt
    rep, _ = tw.build_report(sch.read(), sch.now, {}, notes)                  # rebuilt: says what is true NOW
    levels = {f["name"]: f["level"] for f in rep["tasks"]}
    assert levels[BACKUP] == levels[FEEDBACK] == "ok" and levels["Oshinei Auto Sync"] == "missed"
    assert "healed: re-run by the watchdog at 11:15" in tw.text_report(rep)


def test_heal_holds_back_while_a_sync_is_running():
    tasks = slept_through_the_morning()
    tasks[3].update(state="Running", result=tw.RUNNING, last="2026-09-21T12:00:00")
    sch = FakeScheduler(tasks, now=dt.datetime(2026, 9, 21, 12, 25))
    notes, tried = sch.heal()
    assert sch.started == [] and tried == {}                                  # not counted as an attempt
    assert notes[BACKUP] == "re-run held back - Oshinei Sync Live (Fly) is running"
    rep, _ = tw.build_report(sch.read(), sch.now, {}, notes)
    backup = next(f for f in rep["tasks"] if f["name"] == BACKUP)
    assert backup["level"] == "missed" and backup["meaning"].endswith("held back - Oshinei Sync Live (Fly) is running")


def test_heal_holds_back_when_another_task_is_due_within_ten_minutes():
    sch = FakeScheduler(slept_through_the_morning(), now=dt.datetime(2026, 9, 21, 11, 52))     # logged on at 11:49
    notes, _ = sch.heal()
    assert sch.started == [] and notes[BACKUP] == "re-run held back - Oshinei Sync Live (Fly) starts at 12:00"
    sch = FakeScheduler(slept_through_the_morning(), now=dt.datetime(2026, 9, 21, 11, 40))     # 20 min of room
    assert [n for n, _ in sch.heal()[1].items()] == [BACKUP, FEEDBACK]


def test_heal_tries_once_a_day_and_forgets_yesterday():
    sch = FakeScheduler(slept_through_the_morning(), now=dt.datetime(2026, 9, 21, 17, 0))
    notes, tried = sch.heal(tried={BACKUP: "2026-09-21", FEEDBACK: "2026-09-20"})
    assert [n for n, _ in sch.started] == [FEEDBACK, AUTOSYNC]                # the backup already had its try today
    assert notes[BACKUP] == "not re-run again - already tried today"
    assert tried == {BACKUP: "2026-09-21", FEEDBACK: "2026-09-21", AUTOSYNC: "2026-09-21"}


def test_heal_never_reruns_a_failed_task_and_reports_a_rerun_that_failed():
    sch = FakeScheduler([task(name=BACKUP, last="2026-09-21T07:30:01", result=1, triggers=[daily("07:30")])])
    assert sch.heal()[0] == {} and sch.started == []                          # red needs a person
    sch = FakeScheduler([task(name=BACKUP, last="2026-09-20T07:30:01", triggers=[daily("07:30")])], exit_code=1)
    notes, _ = sch.heal()
    rep, _ = tw.build_report(sch.read(), sch.now, {}, notes)
    f = rep["tasks"][0]
    assert f["level"] == "fail" and f["meaning"].endswith("re-run by the watchdog at 12:17")


def test_heal_says_so_when_the_rerun_outlasts_the_wait():
    sch = FakeScheduler([task(name=BACKUP, last="2026-09-20T07:30:01", triggers=[daily("07:30")])], run_seconds=3600)
    assert sch.heal()[0] == {BACKUP: "re-run by the watchdog at 12:17 - still running"}


def test_a_rerun_that_finished_before_the_report_is_not_called_still_running():
    # 2026-09-21: the midday backup took 6 min, the wait gave up at 6 - and the report, built a
    # moment later against a finished task, still said "still running"
    note = {BACKUP: "re-run by the watchdog at 12:50" + tw.STILL_RUNNING}
    done = task(name=BACKUP, last="2026-09-21T12:50:53", triggers=[daily("07:30")])
    rep, _ = tw.build_report([done], dt.datetime(2026, 9, 21, 12, 57), {}, note)
    assert rep["tasks"][0]["rerun"] == "re-run by the watchdog at 12:50"
    busy = task(name=BACKUP, state="Running", result=tw.RUNNING, last="2026-09-21T12:50:53", triggers=[daily("07:30")])
    rep, _ = tw.build_report([busy], dt.datetime(2026, 9, 21, 12, 57), {}, note)
    assert rep["tasks"][0]["rerun"].endswith(tw.STILL_RUNNING)


def test_heal_at_1700_runs_the_sync_last_and_does_not_wait_for_it():
    sch = FakeScheduler(slept_through_the_morning(), now=dt.datetime(2026, 9, 21, 17, 0), run_seconds=120)
    sch.tasks["Oshinei Sync Live (Fly)"]["last_run"] = "2026-09-21T16:00:01"          # the 16:00 live sync is done
    notes, tried = sch.heal()
    assert [n for n, _ in sch.started] == [BACKUP, FEEDBACK, AUTOSYNC]                # config order: the long one last
    assert notes[AUTOSYNC] == "re-run by the watchdog at 17:04 - still running"       # started, not waited on
    assert sch.now < dt.datetime(2026, 9, 21, 17, 6) and tried[AUTOSYNC] == "2026-09-21"
    rep, _ = tw.build_report(sch.read(), sch.now, {}, notes)
    auto = next(f for f in rep["tasks"] if f["name"] == AUTOSYNC)
    assert auto["level"] == "ok" and "started: re-run by the watchdog at 17:04 - still running" in tw.text_report(rep)
    assert "healed: re-run by the watchdog at 17:00" in tw.text_report(rep)           # the backup did finish


def test_the_sync_needs_75_clear_minutes_before_any_other_slot():
    push = task(name="Oshinei Push Invisible", last="2026-09-20T10:45:01", triggers=[daily("10:45")])
    morning = [t for t in slept_through_the_morning() if t["name"] in (AUTOSYNC, "Oshinei Sync Live (Fly)")] + [push]
    sch = FakeScheduler(morning, now=dt.datetime(2026, 9, 21, 9, 20))                 # 85 min before 10:45
    assert sch.heal()[0] == {AUTOSYNC: "re-run by the watchdog at 09:20 - still running"}
    sch = FakeScheduler(morning, now=dt.datetime(2026, 9, 21, 9, 40))                 # 65 min: a 45-min sync could still be going
    assert sch.heal()[0] == {AUTOSYNC: "re-run held back - Oshinei Push Invisible starts at 10:45"} and sch.started == []
    # 2026-09-14: a manual run at 11:15 never reached "Done" - it ran into the 12:00 live sync
    assert tw.rerun_blocker(AUTOSYNC, slept_through_the_morning(), dt.datetime(2026, 9, 14, 11, 15)) \
        == "Oshinei Sync Live (Fly) starts at 12:00"


def test_the_sync_is_never_started_beside_the_live_sync_and_a_slot_after_midnight_counts():
    tasks = slept_through_the_morning()
    tasks[3].update(state="Running", result=tw.RUNNING)
    assert tw.rerun_blocker(AUTOSYNC, tasks, dt.datetime(2026, 9, 21, 16, 20)) == "Oshinei Sync Live (Fly) is running"
    night = [task(name=AUTOSYNC, triggers=[daily("06:00")]), task(name="Oshinei Nightly", triggers=[daily("00:15")])]
    assert tw.rerun_blocker(AUTOSYNC, night, dt.datetime(2026, 9, 21, 23, 30)) == "Oshinei Nightly starts at 00:15"
    assert tw.rerun_blocker(BACKUP, night, dt.datetime(2026, 9, 21, 23, 30)) is None   # 45 min is room enough for 2


def test_only_the_tasks_the_owner_approved_are_ever_started_and_the_long_one_is_last():
    assert list(tw.RERUN_WHEN_MISSED) == ["Oshinei DB Backup", "Oshinei Feedback Sync", "Oshinei Auto Sync"]
    ahead, wait = tw.RERUN_WHEN_MISSED["Oshinei Auto Sync"]
    assert ahead >= dt.timedelta(minutes=60) and wait == dt.timedelta(0)              # > its 45-min worst case; never waited on
    assert "Oshinei Sync Live (Fly)" not in tw.RERUN_WHEN_MISSED
    assert "fail" not in tw.RERUN_LEVELS and "stuck" not in tw.RERUN_LEVELS


# ── why it failed ────────────────────────────────────────────────────────────
PUSH_LOG = """==================== Sun 09/20/2026 10:45:02.65 ====================
      python: C:\\Python\\python.exe
/api/v1/adapters/sales -> HTTP 404: {"status":"error","code":404,"message":"Application not found"}

*** INVISIBLE PUSH FAILED - exit 1 - see backend\\logs\\push_invisible.log ***
==================== Mon 09/21/2026 10:45:02.96 ====================
      python: C:\\Python\\python.exe
/api/v1/adapters/sales -> HTTP 404: {"status":"error","code":404,"message":"Application not found","request_id":"c1"}

*** INVISIBLE PUSH FAILED - exit 1 - see backend\\logs\\push_invisible.log ***
"""


@pytest.mark.parametrize("encoding", ["utf-8", "utf-16-le"])
def test_the_line_that_explains_the_failure_is_found_in_either_log_encoding(tmp_path, encoding):
    log = tmp_path / "push_invisible.log"
    log.write_bytes(PUSH_LOG.encode(encoding))
    line = tw.last_telling_line(log)
    assert "HTTP 404" in line and "Application not found" in line and "c1" in line     # last run, not the banner


def test_the_log_is_found_through_the_bat_the_task_runs(tmp_path):
    bat = tmp_path / "Push Invisible.bat"
    bat.write_text('powershell -File "%~dp0_tee.ps1" -Bat "%~f0" -Log "%~dp0backend\\logs\\push_invisible.log" -ArgLine "%*"\r\n', encoding="ascii")
    got = tw.log_path_for(f'cmd.exe /c ""{bat}"" auto')
    assert got == tmp_path / "backend\\logs\\push_invisible.log"
    assert tw.log_path_for("cmd.exe /c something-else") is None


def test_the_bat_is_ascii_and_the_task_query_is_too():
    root = Path(__file__).resolve().parents[1]
    for f in (root / "Task Watchdog.bat", root / "scripts" / "register_task_watchdog.ps1"):
        assert all(b < 128 for b in f.read_bytes()), f.name           # PS 5.1 / cmd under the scheduler
    assert tw.PS_QUERY.isascii() and tw._TOAST_PS.isascii()


# ── the app side ─────────────────────────────────────────────────────────────
@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "DB_PATH", tmp_path / "t.db")
    monkeypatch.setattr(main, "DB_PATH", tmp_path / "t.db")
    monkeypatch.delenv("OSHINEI_AUTH", raising=False)
    monkeypatch.setenv("OSHINEI_SEED_TOKEN", "owner:" + "ab" * 32)
    return TestClient(main.app)


REPORT = {"host": "OFFICE-PC", "checked_at": "2026-09-21 11:15:00", "ok": False, "problems": 1,
          "signature": "Oshinei Push Invisible=fail",
          "tasks": [{"name": "Oshinei Push Invisible", "level": "fail", "days": 15, "last_run": "2026-09-21T10:45:02",
                     "meaning": "the .bat ended on its FAILED branch", "log_line": "HTTP 404: Application not found"},
                    {"name": "Oshinei DB Backup", "level": "ok", "last_run": "2026-09-21T07:30:01", "meaning": "ok"}]}


def test_report_needs_the_token_in_the_header_and_404s_without_it(client):
    assert client.post("/api/_admin/task-health", json=REPORT).status_code == 404
    assert client.post("/api/_admin/task-health", json=REPORT, headers={"X-Seed-Token": "nope"}).status_code == 404
    assert client.post("/api/_admin/task-health?token=" + "ab" * 32, json=REPORT).status_code == 404    # never the URL
    assert client.get("/api/usage/task-health").json() == {"known": False}


def test_report_is_stored_and_read_back_with_its_age(client):
    r = client.post("/api/_admin/task-health", json=REPORT, headers={"X-Seed-Token": "ab" * 32})
    assert r.status_code == 200 and r.json() == {"ok": True}
    v = client.get("/api/usage/task-health").json()
    assert v["known"] and not v["silent"] and v["host"] == "OFFICE-PC" and v["age_hours"] < 0.1
    assert [t["name"] for t in v["problems"]] == ["Oshinei Push Invisible"] and len(v["tasks"]) == 2
    assert v["problems"][0]["days"] == 15 and v["signature"] == REPORT["signature"]


def test_a_pc_that_stops_reporting_is_itself_the_finding(client):
    client.post("/api/_admin/task-health", json={**REPORT, "ok": True, "problems": 0, "signature": "", "tasks": []},
                headers={"X-Seed-Token": "ab" * 32})
    later = main._thai_now() + dt.timedelta(hours=main.TASK_HEALTH_SILENT_HOURS + 1)
    v = main._task_health_view(now=later)
    assert v["silent"] and v["problems"] == [] and round(v["age_hours"]) == main.TASK_HEALTH_SILENT_HOURS + 1


def test_junk_is_refused(client):
    h = {"X-Seed-Token": "ab" * 32}
    assert client.post("/api/_admin/task-health", content=b"not json", headers=h).status_code == 400
    assert client.post("/api/_admin/task-health", json={"tasks": "nope"}, headers=h).status_code == 400
    assert client.post("/api/_admin/task-health", content=json.dumps({"tasks": [], "pad": "x" * 70000}), headers=h).status_code == 413


def test_the_view_is_admin_only_and_the_report_passes_the_login_gate(client, monkeypatch):
    monkeypatch.setenv("OSHINEI_AUTH", "1")
    monkeypatch.setenv("OSHINEI_AUTH_SECRET", "test-secret-stable")
    try:
        assert client.get("/api/usage/task-health").status_code == 401
        r = client.post("/api/_admin/task-health", json=REPORT, headers={"X-Seed-Token": "ab" * 32})
        assert r.status_code == 200                       # machine-to-machine: its own token, no session
    finally:
        monkeypatch.delenv("OSHINEI_AUTH", raising=False)
