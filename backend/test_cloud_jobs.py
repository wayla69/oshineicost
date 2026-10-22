# -*- coding: utf-8 -*-
"""The other cloud jobs (backend/cloud_jobs.py, PR-CJ): the feedback fetch, the Invisible Voice push and the task watchdog.

No network and no Fly: the review sources and Invisible Voice are stubs, the bucket an in-memory stub, the databases scratch
SQLite files, the clock a fixed Bangkok time, the child process a scripted fake.
"""
import base64
import json
import sqlite3
import sys
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path

import pytest

HERE = Path(__file__).parent.resolve()
sys.path.insert(0, str(HERE))

import cloud_backup as cb  # noqa: E402
import cloud_jobs as cj  # noqa: E402
import cloud_sync as cs  # noqa: E402
from test_cloud_backup import CREDS, FakeProc, FakeS3, _login  # noqa: E402
from test_sync_lock import _holder  # noqa: E402

BKK = cj.BKK
TOKEN = "write-token-123"
ALL_ON = {"OSHINEI_CLOUD_SYNC": "1", "OSHINEI_CLOUD_BACKUP": "1", "OSHINEI_CLOUD_FEEDBACK": "1",
          "OSHINEI_CLOUD_INVISIBLE": "1", "OSHINEI_CLOUD_WATCHDOG": "1"}


def at(h, m=0, day=22):
    return datetime(2026, 10, day, h, m, tzinfo=BKK)


def iso(dt):
    return dt.isoformat(timespec="seconds")


def b64(obj) -> str:
    return base64.b64encode(json.dumps(obj).encode("utf-8")).decode("ascii")


def make_db(path: Path, newest_sale="2026-10-22 10:30:00"):
    con = sqlite3.connect(str(path))
    con.execute("CREATE TABLE app_settings(key TEXT PRIMARY KEY, value TEXT NOT NULL DEFAULT '')")
    con.execute("CREATE TABLE sales_transaction(id INTEGER PRIMARY KEY, sold_at TEXT)")
    if newest_sale:
        con.execute("INSERT INTO sales_transaction(sold_at) VALUES(?)", (newest_sale,))
    con.commit()
    con.close()
    return path


class Rig:
    """A Job wired to scratch parts, the way test_cloud_backup.Rig wires a Backup."""

    def __init__(self, tmp_path, name, *, env=None, lines=(), rc=0, now=None, boot=None, check=None):
        self.tmp = tmp_path
        self.db = make_db(tmp_path / "oshinei.db")
        self.calls = []
        self.lines, self.rc = list(lines), rc
        self.env = dict(env or {})
        self.now = [now or at(9, 5)]
        self.job = cj.Job(name, db=self._db, db_path=self.db, popen=self._popen, clock=lambda: self.now[0], env=self.env,
                          log_dir=tmp_path / "logs", sleep=lambda s: None, boot=boot or at(0, 0, day=20), check=check)

    @contextmanager
    def _db(self):
        con = sqlite3.connect(str(self.db))
        try:
            yield con
        finally:
            con.close()

    def _popen(self, cmd, **kw):
        self.calls.append((cmd, kw))
        return FakeProc([l if l.endswith("\n") else l + "\n" for l in self.lines], self.rc)

    def put(self, name, rec):
        """Store another job's status, the way that job's own module does."""
        if name == "sync":
            cs.write_status(rec, self._db)
        elif name == "backup":
            cb.write_status(rec, self._db)
        else:
            cj.write_status(name, rec, self._db)


# ── off by default ───────────────────────────────────────────────────────────
def test_every_job_is_off_unless_its_flag_is_one():
    for name in cj.SPECS:
        assert not cj.enabled_job(name, {}) and not cj.enabled_job(name, {cj.SPECS[name]["flag"]: "0"})
        assert cj.enabled_job(name, {cj.SPECS[name]["flag"]: "1"}) and cj.enabled_job(name, {cj.SPECS[name]["flag"]: "true"})
    assert cj.enabled_job("sync", {"OSHINEI_CLOUD_SYNC": "1"}) and not cj.enabled_job("backup", {"OSHINEI_CLOUD_SYNC": "1"})


def test_start_is_inert_without_a_flag(monkeypatch):
    for spec in cj.SPECS.values():
        monkeypatch.delenv(spec["flag"], raising=False)
    cj._instances.clear()
    assert cj.start() == [] and cj._instances == {}                  # no Job object, no thread, no database touched


def test_start_runs_only_the_jobs_that_are_on(monkeypatch):
    for spec in cj.SPECS.values():
        monkeypatch.delenv(spec["flag"], raising=False)
    monkeypatch.setenv("OSHINEI_CLOUD_WATCHDOG", "1")
    monkeypatch.setattr(cj.Job, "loop", lambda self, *a, **k: None)
    cj._instances.clear()
    try:
        threads = cj.start()
        assert [t.name for t in threads] == ["cloud-watchdog"] and set(cj._instances) == {"watchdog"}
        for t in threads:
            t.join(2)
    finally:
        cj._instances.clear()


# ── the schedule ─────────────────────────────────────────────────────────────
def test_default_times_and_a_typo_does_not_switch_a_job_off():
    assert cj.configured_times("feedback", {}) == [(9, 0)]
    assert cj.configured_times("invisible", {}) == [(10, 45)]
    assert cj.configured_times("watchdog", {}) == [(11, 15), (17, 0)]
    assert cj.configured_times("feedback", {"OSHINEI_CLOUD_FEEDBACK_TIME": "08:30"}) == [(8, 30)]
    assert cj.configured_times("feedback", {"OSHINEI_CLOUD_FEEDBACK_TIME": "nonsense"}) == [(9, 0)]
    assert cj.configured_times("invisible", {"OSHINEI_CLOUD_INVISIBLE_TIME": "9:00,10:00"}) == [(9, 0)]      # one slot a day
    assert cj.configured_times("watchdog", {"OSHINEI_CLOUD_WATCHDOG_TIMES": "12:00;18:30"}) == [(12, 0), (18, 30)]
    assert cj.configured_times("sync", {}) == [(6, 0), (12, 0), (16, 0)] and cj.configured_times("backup", {}) == [(7, 30)]


@pytest.mark.parametrize("now, prev, nxt", [
    (at(8, 59), at(9, 0, day=21), at(9, 0)),
    (at(9, 0), at(9, 0), at(9, 0, day=23)),
    (at(0, 5), at(9, 0, day=21), at(9, 0)),
])
def test_slot_math_in_bangkok_time(now, prev, nxt):
    assert cs.previous_slot(now, [(9, 0)]) == prev and cs.next_slot(now, [(9, 0)]) == nxt


def test_a_restart_catches_up_once_on_the_missed_slot(tmp_path):
    rig = Rig(tmp_path, "feedback", env=ALL_ON, now=at(9, 40))
    assert rig.job.catch_up_due(at(9, 40)) == at(9, 0)                        # never ran: catch up
    rig.job.save({"last_ok": iso(at(9, 2)), "last_start": iso(at(9, 0))})
    assert rig.job.catch_up_due(at(9, 40)) is None                            # ran this slot
    rig.job.save({"last_ok": iso(at(9, 2, day=21)), "last_start": iso(at(9, 25))})
    assert rig.job.catch_up_due(at(9, 40)) is None                            # a run started in the last half hour: a crash loop
    rig.job.save({"last_ok": iso(at(9, 2, day=21)), "last_start": iso(at(9, 2, day=21))})
    assert rig.job.catch_up_due(at(9, 40)) == at(9, 0)


def test_a_slot_runs_once_and_the_next_slot_runs_again(tmp_path):
    rig = Rig(tmp_path, "feedback", env=ALL_ON, lines=["CJ_RESULT " + json.dumps({"result": "ok", "sources": ["google"]})])
    assert rig.job.tick(at(9, 1)) is not None and len(rig.calls) == 1
    assert rig.job.tick(at(9, 30)) is None and len(rig.calls) == 1            # same slot
    rig.now[0] = at(9, 1, day=23)
    assert rig.job.tick(at(9, 1, day=23)) is not None and len(rig.calls) == 2


def test_the_watchdog_checks_at_each_of_its_two_slots(tmp_path):
    seen = []
    rig = Rig(tmp_path, "watchdog", env=ALL_ON, check=lambda j: (seen.append(j._clock()) or {"findings": [], "checked_at": "x"}))
    rig.job.handled_slot = cs.previous_slot(at(11, 20), rig.job.times())      # the loop sets this after the startup check
    assert rig.job.tick(at(11, 20)) is None and seen == []
    assert rig.job.tick(at(17, 1)) is not None and len(seen) == 1
    assert rig.job.tick(at(17, 30)) is None


def test_a_run_left_running_by_a_dead_process_is_marked_interrupted(tmp_path):
    rig = Rig(tmp_path, "invisible", env=ALL_ON)
    rig.job.save({"running": True, "result": "ok", "log_tail": ["pushing"]})
    rig.job.reconcile_stale_running()
    st = rig.job.status()
    assert st["running"] is False and st["result"] == "failed" and "interrupted" in st["log_tail"][-1]


# ── the child process: recorded the way the page shows it ────────────────────
def test_the_child_command_is_niced_and_keeps_only_its_own_login():
    assert cj.child_command("feedback", python="/usr/bin/python", nice=True) == \
        ["nice", "-n", "10", "/usr/bin/python", "-u", str(HERE / "cloud_jobs.py"), "child", "feedback"]
    base = {"OSHINEI_SEED_TOKEN": "t", "OSHINEI_VAPID_PRIVATE": "v", "OSHINEI_AUTH_SECRET": "s",
            "FEEDBACK_CONFIG_B64": "cfg", "INVISIBLE_API_KEY": "key", "BUCKET_NAME": "b"}
    fb = cj.child_env("feedback", "/data/oshinei.db", base)
    iv = cj.child_env("invisible", "/data/oshinei.db", base)
    assert fb["OSHINEI_DB"] == "/data/oshinei.db" and fb["FEEDBACK_CONFIG_B64"] == "cfg" and "INVISIBLE_API_KEY" not in fb
    assert iv["INVISIBLE_API_KEY"] == "key" and "FEEDBACK_CONFIG_B64" not in iv
    for env in (fb, iv):
        assert not {"OSHINEI_SEED_TOKEN", "OSHINEI_VAPID_PRIVATE", "OSHINEI_AUTH_SECRET"} & set(env)


def test_a_good_child_run_is_recorded_and_its_numbers_kept(tmp_path):
    res = {"result": "ok", "sales_rows": 120, "since": "2026-10-19", "menu_rows": 300}
    rig = Rig(tmp_path, "invisible", env={**ALL_ON, "INVISIBLE_API_KEY": "k" * 12}, lines=["pushed sales", "CJ_RESULT " + json.dumps(res)])
    st = rig.job.run("schedule", slot=at(10, 45))
    assert st["result"] == "ok" and st["last_ok"] == st["last_end"] and st["streak"] == 0 and st["running"] is False
    assert st["detail"] == {"sales_rows": 120, "since": "2026-10-19", "menu_rows": 300} and st["log_tail"] == ["pushed sales"]
    assert rig.job.status() == st
    cmd, kw = rig.calls[0]
    assert cmd[-3:] == [str(HERE / "cloud_jobs.py"), "child", "invisible"] and kw["env"]["OSHINEI_DB"] == str(rig.db)


def test_no_source_is_a_success_not_a_failure_and_no_alert(tmp_path):
    rig = Rig(tmp_path, "feedback", env=ALL_ON, lines=["CJ_RESULT " + json.dumps({"result": "no_source", "sources": []})])
    st = rig.job.run("schedule", slot=at(9, 0))
    assert st["result"] == "no_source" and st["last_ok"] == st["last_end"] and st["streak"] == 0
    from routers import push_api as P
    assert P.cloud_job_payloads({"feedback": st}, datetime(2026, 10, 22, 12, 0)) == []


def test_a_child_that_dies_without_a_result_is_failed_and_the_logins_never_reach_the_page(tmp_path):
    key = "inv-secret-key-123456"
    cfg = {"google": {"client_id": "cid", "client_secret": "gsecret-abcdef", "refresh_token": "rtoken-abcdef-xyz"}}
    rig = Rig(tmp_path, "invisible", rc=1, env={**ALL_ON, "INVISIBLE_API_KEY": key, "FEEDBACK_CONFIG_B64": b64(cfg)},
              lines=[f"HTTP 401 for key {key}", "refresh rtoken-abcdef-xyz rejected"])
    st = rig.job.run("schedule", slot=at(10, 45))
    assert st["result"] == "failed" and st["exit_code"] == 1
    assert st["log_tail"] == ["HTTP 401 for key ***", "refresh *** rejected"]
    assert rig.job.run("manual")["streak"] == 2


def test_a_missing_popen_is_failed_not_a_crash(tmp_path):
    rig = Rig(tmp_path, "feedback", env=ALL_ON)

    def boom(cmd, **kw):
        raise OSError("no such file")
    rig.job._popen = boom
    assert rig.job.run("manual")["result"] == "failed"


# ── feedback: no_source vs creds_missing, and where the rows go ───────────────
def run_feedback(tmp_path, env, runner=None, **kw):
    db = make_db(tmp_path / "oshinei.db") if not (tmp_path / "oshinei.db").exists() else tmp_path / "oshinei.db"
    lines = []
    res = cj.perform_feedback(env=env, db_path=db, lock_path=tmp_path / "x.lock", lock_wait_s=kw.pop("lock_wait_s", 1),
                              runner=runner, log=lines.append)
    return res, lines


GOOD_GOOGLE = {"google": {"client_id": "cid", "client_secret": "sec", "refresh_token": "rt"}}


def test_no_review_source_at_all_is_no_source(tmp_path):
    called = []
    res, lines = run_feedback(tmp_path, {}, runner=lambda a: called.append(a) or 0)
    assert res["result"] == "no_source" and called == [] and "nothing to fetch" in lines[-1]
    res, _ = run_feedback(tmp_path, {"FEEDBACK_CONFIG_B64": b64({})}, runner=lambda a: called.append(a) or 0)
    assert res["result"] == "no_source" and called == []
    res, _ = run_feedback(tmp_path, {"FEEDBACK_CONFIG_B64": b64({"google": {}, "facebook": None})}, runner=lambda a: called.append(a) or 0)
    assert res["result"] == "no_source"                                      # empty entries are not sources


def test_sources_without_their_logins_are_creds_missing(tmp_path):
    called = []
    for cfg in ({"google": {"client_id": "only-this"}}, {"facebook": {"pages": [{"id": "1", "name": "x"}]}},
                {"x": {"keywords": ["oshinei"]}}, {"google": {"client_id": "a"}, "youtube": {"keywords": ["b"]}}):
        res, _ = run_feedback(tmp_path, {"FEEDBACK_CONFIG_B64": b64(cfg)}, runner=lambda a: called.append(a) or 0)
        assert res["result"] == "creds_missing", cfg
    assert called == []


def test_a_configured_source_runs_the_fetcher_against_the_live_db_with_the_same_arguments_as_the_pc(tmp_path):
    seen = []
    res, _ = run_feedback(tmp_path, {"FEEDBACK_CONFIG_B64": b64(GOOD_GOOGLE)}, runner=lambda a: seen.append(a) or 0)
    assert res["result"] == "ok" and res["sources"] == ["google"]
    argv = seen[0]
    assert argv[:5] == ["run", "--source", "all", "--since", "auto"]
    assert argv[argv.index("--db") + 1] == str(tmp_path / "oshinei.db")      # DbSink: the endpoint's own insert code, no API, no token
    assert argv[argv.index("--config") + 1] == str(tmp_path / ".feedback_config.json")
    assert "--api" not in argv and "--token" not in argv
    res, _ = run_feedback(tmp_path, {"FEEDBACK_CONFIG_B64": b64(GOOD_GOOGLE)}, runner=lambda a: 1)
    assert res["result"] == "failed"
    res, _ = run_feedback(tmp_path, {"FEEDBACK_CONFIG_B64": b64(GOOD_GOOGLE)}, runner=lambda a: (_ for _ in ()).throw(RuntimeError("quota")))
    assert res["result"] == "failed" and "quota" in res["error"]


def test_a_damaged_secret_is_a_failure_with_a_plain_message(tmp_path):
    res, lines = run_feedback(tmp_path, {"FEEDBACK_CONFIG_B64": "!!not-base64-json"})
    assert res["result"] == "failed" and "FEEDBACK_CONFIG_B64" in res["error"]


def test_the_config_is_rewritten_only_when_the_secret_changes(tmp_path):
    env = {"FEEDBACK_CONFIG_B64": b64(GOOD_GOOGLE)}
    p = cj.materialize_feedback_config(env, tmp_path)
    assert json.loads(p.read_text(encoding="utf-8")) == GOOD_GOOGLE
    rotated = {"google": {**GOOD_GOOGLE["google"], "refresh_token": "ROTATED-by-the-fetcher"}}
    p.write_text(json.dumps(rotated), encoding="utf-8")                      # a fetcher keeps rotated tokens here
    cj.materialize_feedback_config(env, tmp_path)
    assert json.loads(p.read_text(encoding="utf-8")) == rotated              # same secret: the rotated token survives
    new = {"google": {**GOOD_GOOGLE["google"], "refresh_token": "owner-set-a-new-one"}}
    cj.materialize_feedback_config({"FEEDBACK_CONFIG_B64": b64(new)}, tmp_path)
    assert json.loads(p.read_text(encoding="utf-8")) == new                  # a new secret wins
    assert cj.materialize_feedback_config({}, tmp_path / "empty") is None


def test_feedback_waits_for_a_sync_then_skips_as_locked(tmp_path):
    lock = tmp_path / "x.lock"
    holder = _holder(lock, 15)
    called = []
    try:
        db = make_db(tmp_path / "oshinei.db")
        lines = []
        res = cj.perform_feedback(env={"FEEDBACK_CONFIG_B64": b64(GOOD_GOOGLE)}, db_path=db, lock_path=lock, lock_wait_s=1,
                                  runner=lambda a: called.append(a) or 0, log=lines.append)
    finally:
        holder.kill()
        holder.wait()
    assert res["result"] == "locked" and called == [] and "holder" in lines[-1]


# ── invisible: the login, the lock, the staged swap, the read-only connection ─
def run_invisible(tmp_path, env=None, runner=None, lock_wait_s=1):
    db = tmp_path / "oshinei.db"
    if not db.exists():
        make_db(db)
    lines = []
    res = cj.perform_invisible(env={"INVISIBLE_API_KEY": "k" * 12, **(env or {})}, db_path=db, lock_path=tmp_path / "x.lock",
                               lock_wait_s=lock_wait_s, runner=runner, log=lines.append)
    return res, lines


def test_invisible_without_its_key_is_creds_missing(tmp_path):
    called = []
    res, _ = run_invisible(tmp_path, {"INVISIBLE_API_KEY": " "}, runner=lambda a: called.append(a) or 0)
    assert res["result"] == "creds_missing" and called == []


def test_invisible_pushes_three_days_and_the_menu_from_the_live_db_read_only_under_the_lock(tmp_path):
    import sync_lock
    held = []

    def runner(argv):
        held.append(str(tmp_path / "x.lock") in sync_lock._held)              # the sync lock is ours while the push reads
        print(json.dumps({"sales": {"since": "2026-10-19", "rows": 12, "results": []}, "menu": {"rows": 5, "results": []}}))
        return 0
    res, lines = run_invisible(tmp_path, runner=runner)
    assert res["result"] == "ok" and res["sales_rows"] == 12 and res["menu_rows"] == 5 and res["since"] == "2026-10-19"
    assert held == [True] and not sync_lock._held
    seen = []
    run_invisible(tmp_path, {"INVISIBLE_API_URL": "https://example.invalid"}, runner=lambda a: seen.append(a) or 0)
    argv = seen[0]
    assert argv[:3] == ["--days", "3", "--menu"] and "--readonly" in argv
    assert argv[argv.index("--db") + 1] == str(tmp_path / "oshinei.db")
    assert argv[argv.index("--state-file") + 1] == str(tmp_path / ".invisible_last_ok.txt")    # on the volume, not the image
    assert argv[argv.index("--url") + 1] == "https://example.invalid"


def test_invisible_waits_for_a_running_sync_then_skips_as_locked(tmp_path):
    lock = tmp_path / "x.lock"
    holder = _holder(lock, 15)
    called = []
    try:
        make_db(tmp_path / "oshinei.db")
        res, lines = run_invisible(tmp_path, runner=lambda a: called.append(a) or 0, lock_wait_s=1)
    finally:
        holder.kill()
        holder.wait()
    assert res["result"] == "locked" and called == [] and "holder" in lines[-1]


def test_invisible_does_not_read_while_a_staged_database_waits_for_the_swap(tmp_path):
    db = make_db(tmp_path / "oshinei.db")
    inc = Path(str(db) + ".incoming")
    inc.write_bytes(b"staged")
    called = []
    res, _ = run_invisible(tmp_path, runner=lambda a: called.append(a) or 0, lock_wait_s=0.5)
    assert res["result"] == "locked" and called == []
    inc.unlink()                                                              # the hot-swap took it
    res, _ = run_invisible(tmp_path, runner=lambda a: called.append(a) or 0)
    assert res["result"] == "ok" and len(called) == 1


def test_an_http_error_or_a_bad_key_is_failed_with_the_message_but_no_key(tmp_path):
    def runner(argv):
        raise SystemExit("/api/v1/adapters/sales → HTTP 404: Application not found")
    res, lines = run_invisible(tmp_path, runner=runner)
    assert res["result"] == "failed" and "HTTP 404" in res["error"]


def test_push_invisible_reads_read_only_and_keeps_its_mark_where_it_is_told(tmp_path, monkeypatch):
    import push_invisible as pi
    db = make_db(tmp_path / "live.db")
    state = tmp_path / "vol" / "mark.txt"
    state.parent.mkdir()
    attempts = []

    def sales(con, since):
        try:
            con.execute("CREATE TABLE z(x)")
            attempts.append("wrote")
        except sqlite3.OperationalError:
            attempts.append("refused")
        return [{"location_code": "BR01", "day": "2026-10-21", "revenue": 1.0, "bills": 1, "covers": 1, "discount": 0.0}]
    posted = []
    monkeypatch.setattr(pi, "sales_rows", sales)
    monkeypatch.setattr(pi, "menu_rows", lambda con: [])
    monkeypatch.setattr(pi, "post", lambda path, rows, **kw: posted.append((path, len(rows))) or {"ok": True})
    monkeypatch.setenv("INVISIBLE_API_KEY", "x" * 12)
    assert pi.main(["--days", "3", "--db", str(db), "--readonly", "--state-file", str(state)]) == 0
    assert attempts == ["refused"] and posted == [("/api/v1/adapters/sales", 1)]
    assert state.read_text(encoding="utf-8") == "2026-10-21"


# ── the watchdog: what it flags ──────────────────────────────────────────────
def judge(name, st, now=at(11, 30), times=None, usual=None, boot=at(0, 0, day=20)):
    return cj.judge_job(name, st, now=now, times=times or [(10, 45)], boot=boot, usual=usual)


def kinds(fs):
    return [f["kind"] for f in fs]


def test_a_failed_run_is_flagged_and_so_are_a_missing_login_and_a_full_disk():
    base = {"last_start": iso(at(10, 45)), "running": False}
    assert kinds(judge("invisible", {**base, "result": "failed", "log_tail": ["HTTP 404"]})) == ["failed"]
    assert kinds(judge("sync", {**base, "result": "reconcile_failed"})) == ["failed"]
    assert kinds(judge("backup", {**base, "result": "verify_failed"})) == ["failed"]
    assert kinds(judge("backup", {**base, "result": "creds_missing"})) == ["creds_missing"]
    assert kinds(judge("sync", {**base, "result": "no_space"})) == ["no_space"]
    for fine in ("ok", "locked", "no_source", None):
        assert judge("feedback", {**base, "result": fine}) == [], fine


def test_a_slot_with_no_start_within_half_an_hour_is_missed():
    st = {"result": "ok", "last_start": iso(at(10, 45, day=21)), "running": False}
    assert kinds(judge("invisible", st, now=at(11, 20))) == ["missed"]
    assert judge("invisible", st, now=at(11, 10)) == []                       # inside the 30-minute grace
    assert judge("invisible", {**st, "last_start": iso(at(10, 46))}, now=at(11, 20)) == []      # it did start
    assert judge("invisible", st, now=at(11, 20), boot=at(11, 0)) == []        # the process was not up at the slot: catch-up's job
    assert kinds(judge("invisible", {}, now=at(11, 20))) == ["missed"]         # enabled, never started


def test_a_run_that_outlasts_twice_its_usual_time_or_three_hours_is_stuck():
    st = {"running": True, "last_start": iso(at(11, 0)), "result": "ok"}
    assert judge("invisible", st, now=at(11, 10), usual=60) == []              # 10 min, the floor is 15
    assert kinds(judge("invisible", st, now=at(11, 20), usual=60)) == ["stuck"]
    assert judge("invisible", st, now=at(12, 40), usual=3000) == []            # 100 min < 2 x 50 min
    assert kinds(judge("invisible", st, now=at(12, 40), usual=2000)) == ["stuck"]
    assert judge("sync", st, now=at(13, 50), usual=None) == []                 # no history: the 3-hour cap
    assert kinds(judge("sync", st, now=at(14, 1), usual=None)) == ["stuck"]
    assert kinds(judge("sync", st, now=at(15, 0), usual=99999)) == ["stuck"]   # never longer than 3 h
    assert judge("sync", {**st, "result": "failed"}, now=at(11, 5)) == []      # a running job is judged on its running only


def test_stale_sales_data_is_flagged_after_two_days(tmp_path):
    db = make_db(tmp_path / "a.db", newest_sale="2026-10-19 21:00:00")
    con = sqlite3.connect(str(db))
    f = cj.data_freshness(con, at(11, 30))
    assert f["kind"] == "stale_data" and f["level"] == "stale" and f["days"] == 3
    assert cj.data_freshness(con, at(20, 59, day=21)) is None                  # 47 h 59 min
    con.execute("DELETE FROM sales_transaction")
    assert cj.data_freshness(con, at(11, 30)) is None                          # an empty table is not a stale one
    con.close()


def test_a_stale_or_unreachable_backup_is_flagged():
    now = at(11, 30)
    fresh = FakeS3(["db/oshinei_2026-10-22_0730.db.gz"])
    fresh.list_objects_v2 = lambda **kw: {"Contents": [{"Key": "db/oshinei_2026-10-22_0730.db.gz", "Size": 5,
                                                         "LastModified": datetime(2026, 10, 22, 0, 30)}]}      # 07:30 Bangkok in UTC
    assert cj.backup_freshness(CREDS, now, lambda env: fresh) is None
    old = FakeS3(["db/oshinei_2026-10-20_0730.db.gz"])
    old.list_objects_v2 = lambda **kw: {"Contents": [{"Key": "db/oshinei_2026-10-20_0730.db.gz", "Size": 5,
                                                       "LastModified": datetime(2026, 10, 20, 0, 30)}]}
    f = cj.backup_freshness(CREDS, now, lambda env: old)
    assert f["kind"] == "stale_backup" and f["level"] == "stale"
    broken = lambda env: (_ for _ in ()).throw(ConnectionError("down"))       # noqa: E731
    assert cj.backup_freshness(CREDS, now, broken)["kind"] == "stale_backup"
    assert cj.backup_freshness({}, now, lambda env: fresh) is None             # no storage login: the backup job reports that itself


def watchdog_rig(tmp_path, *, now=at(11, 30), **kw):
    s3 = kw.pop("s3", None)
    usage = tmp_path / "usage.db"
    rig = Rig(tmp_path, "watchdog", env={**ALL_ON, **CREDS}, now=now,
              check=lambda j: cj.run_watchdog_check(j, client_factory=lambda env: s3, usage_path=usage), **kw)
    return rig, usage


def banner(usage):
    con = sqlite3.connect(str(usage))
    try:
        return json.loads(con.execute("SELECT value FROM usage_meta WHERE key = ?", (cj.HEALTH_KEY,)).fetchone()[0])
    finally:
        con.close()


def stale_bucket():
    s3 = FakeS3()
    s3.list_objects_v2 = lambda **kw: {"Contents": [{"Key": "db/oshinei_2026-10-19_0730.db.gz", "Size": 5,
                                                      "LastModified": datetime(2026, 10, 19, 0, 30)}]}
    return s3


def test_the_watchdog_flags_every_kind_of_problem_and_writes_the_banner(tmp_path):
    rig, usage = watchdog_rig(tmp_path, s3=stale_bucket())
    rig.db.unlink()
    make_db(rig.db, newest_sale="2026-10-18 20:00:00")                         # stale sales
    rig.put("sync", {"result": "failed", "last_start": iso(at(6, 0)), "log_tail": ["pymssql timeout"]})
    rig.put("backup", {"result": "creds_missing", "last_start": iso(at(7, 30))})
    rig.put("feedback", {"running": True, "last_start": iso(at(7, 0)), "result": "ok"})                # 4.5 h
    rig.put("invisible", {"result": "ok", "last_start": iso(at(10, 45, day=21))})                      # missed today
    st = rig.job.run("schedule", slot=at(11, 15))
    assert st["result"] == "findings"
    got = {(f["job"], f["kind"]) for f in st["findings"]}
    assert got == {("sync", "failed"), ("backup", "creds_missing"), ("feedback", "stuck"), ("invisible", "missed"),
                   ("data", "stale_data"), ("backup", "stale_backup")}
    b = banner(usage)
    rep = b["report"]
    assert rep["source"] == "cloud" and rep["ok"] is False and rep["problems"] == 6 and rep["host"] == "fly"
    assert {t["level"] for t in rep["tasks"] if t["level"] != "ok"} == {"fail", "stuck", "missed", "stale"}
    assert all({"name", "level", "meaning", "last_run"} <= set(t) for t in rep["tasks"])        # what the banner prints
    assert "invisible=missed" in rep["signature"] and b["received_at"] == "2026-10-22 11:30:00"
    assert [l for l in st["log_tail"] if "Invisible" in l]


def test_a_healthy_day_is_ok_and_the_banner_clears(tmp_path):
    fresh = FakeS3()
    fresh.list_objects_v2 = lambda **kw: {"Contents": [{"Key": "db/oshinei_2026-10-22_0730.db.gz", "Size": 5,
                                                         "LastModified": datetime(2026, 10, 22, 0, 30)}]}
    rig, usage = watchdog_rig(tmp_path, s3=fresh)
    rig.put("sync", {"result": "ok", "last_start": iso(at(6, 0)), "duration_s": 2400})
    rig.put("backup", {"result": "ok", "last_start": iso(at(7, 30)), "duration_s": 200})
    rig.put("feedback", {"result": "no_source", "last_start": iso(at(9, 0)), "duration_s": 3})
    rig.put("invisible", {"result": "ok", "last_start": iso(at(10, 45)), "duration_s": 40})
    st = rig.job.run("schedule", slot=at(11, 15))
    assert st["result"] == "ok" and st["findings"] == [] and st["detail"]["jobs_checked"] == 4
    rep = banner(usage)["report"]
    assert rep["ok"] is True and rep["problems"] == 0 and rep["signature"] == ""
    assert st["usual_s"]["sync"] == 2400 and st["usual_s"]["invisible"] == 40  # the watchdog learns how long each job takes


def test_a_disabled_job_is_not_judged(tmp_path):
    rig, usage = watchdog_rig(tmp_path, s3=stale_bucket())
    rig.env.update(OSHINEI_CLOUD_SYNC="0", OSHINEI_CLOUD_BACKUP="0", OSHINEI_CLOUD_FEEDBACK="0", OSHINEI_CLOUD_INVISIBLE="0")
    rig.put("sync", {"result": "failed", "last_start": iso(at(6, 0))})
    st = rig.job.run("schedule", slot=at(11, 15))
    assert st["findings"] == [] and st["detail"]["jobs_checked"] == 0           # no backup check either: the backup job is off


def test_a_watchdog_that_cannot_write_the_banner_still_records_its_findings(tmp_path):
    rig = Rig(tmp_path, "watchdog", env=ALL_ON, check=lambda j: cj.run_watchdog_check(
        j, client_factory=lambda env: stale_bucket(), usage_path=tmp_path / "no" / "such" / "usage.db"))
    rig.put("sync", {"result": "failed", "last_start": iso(at(6, 0))})
    st = rig.job.run("manual")
    assert st["result"] == "findings" and any(f["kind"] == "failed" for f in st["findings"])


# ── push alerts: once per finding per day ─────────────────────────────────────
def test_a_failed_feedback_or_invisible_run_is_pushed_once_a_day_and_a_locked_one_not_at_all():
    from routers import push_api as P
    now = datetime(2026, 10, 22, 12, 0)
    end = "2026-10-22T10:50:00+07:00"
    got = P.cloud_job_payloads({"invisible": {"result": "failed", "last_end": end}, "feedback": {"result": "creds_missing", "last_end": end}}, now)
    assert sorted(k for k, _ in got) == ["stale:job:feedback:creds_missing:2026-10-22", "stale:job:invisible:failed:2026-10-22"]
    assert all(p["tag"] == "oshinei-stale" and p["url"] for _, p in got)
    for res in ("ok", "locked", "no_source", None):
        assert P.cloud_job_payloads({"feedback": {"result": res, "last_end": end}}, now) == []
    assert P.cloud_job_payloads({"feedback": {"result": "failed", "last_end": end, "running": True}}, now) == []
    assert P.cloud_job_payloads({"feedback": {"result": "failed", "last_end": "2026-10-20T10:50:00+07:00"}}, now) == []
    assert P.cloud_job_payloads(None, now) == [] and P.cloud_job_payloads({"feedback": {}}, now) == []


def test_the_watchdog_pushes_what_a_run_cannot_say_about_itself():
    from routers import push_api as P
    now = datetime(2026, 10, 22, 12, 0)
    status = {"last_end": "2026-10-22T11:30:00+07:00", "findings": [
        {"job": "invisible", "name": "ส่งข้อมูลไป Invisible Voice", "kind": "missed", "meaning": "ไม่ได้เริ่มรันตามรอบล่าสุด — รอบ 10:45"},
        {"job": "data", "name": "data", "kind": "stale_data", "meaning": "ข้อมูลขายไม่เข้า — ยอดขายล่าสุด 19/10"},
        {"job": "sync", "name": "ดึงข้อมูลจาก POS", "kind": "failed", "meaning": "รอบล่าสุดไม่สำเร็จ"},          # the sync has its own alert
        {"job": "backup", "name": "สำรองข้อมูล", "kind": "creds_missing", "meaning": "x"}]}                    # so has the backup
    got = P.cloud_watchdog_payloads(status, now)
    assert [k for k, _ in got] == ["stale:watch:invisible:missed:2026-10-22", "stale:watch:data:stale_data:2026-10-22"]
    assert P.cloud_watchdog_payloads({**status, "running": True}, now) == []
    assert P.cloud_watchdog_payloads({**status, "last_end": "2026-10-19T11:30:00+07:00"}, now) == []
    assert P.cloud_watchdog_payloads(None, now) == []


def test_the_alert_loop_sends_each_cloud_job_finding_once_and_only_while_the_job_is_on(client, monkeypatch):
    from routers import push_api as P
    import main
    now = datetime(2026, 10, 22, 12, 0)
    rec = {"result": "findings", "running": False, "last_end": "2026-10-22T11:30:00+07:00",
           "findings": [{"job": "invisible", "name": "ส่งข้อมูลไป Invisible Voice", "kind": "missed", "meaning": "ไม่ได้เริ่มรัน — รอบ 10:45"}]}
    with main.get_db() as con:
        con.execute("INSERT OR REPLACE INTO app_settings(key, value) VALUES(?, ?)", (cj.status_key("watchdog"), json.dumps(rec)))
        con.execute("INSERT OR REPLACE INTO app_settings(key, value) VALUES(?, ?)",
                    (cj.status_key("invisible"), json.dumps({"result": "failed", "last_end": "2026-10-22T10:50:00+07:00"})))
        con.commit()
    try:
        for name in cj.SPECS:
            monkeypatch.delenv(cj.SPECS[name]["flag"], raising=False)
        off = P.maybe_send_alerts(now=now, force=True, scorecard={}, cashdata=[])
        assert not [k for k in off["sent"] if k.startswith(("stale:job", "stale:watch"))]
        monkeypatch.setenv("OSHINEI_CLOUD_WATCHDOG", "1")
        monkeypatch.setenv("OSHINEI_CLOUD_INVISIBLE", "1")
        first = P.maybe_send_alerts(now=now, force=True, scorecard={}, cashdata=[])
        assert sorted(k for k in first["sent"] if k.startswith(("stale:job", "stale:watch"))) == \
            ["stale:job:invisible:failed:2026-10-22", "stale:watch:invisible:missed:2026-10-22"]
        second = P.maybe_send_alerts(now=now, force=True, scorecard={}, cashdata=[])
        assert not [k for k in second["sent"] if k.startswith(("stale:job", "stale:watch"))]        # once per finding per day
        later = P.maybe_send_alerts(now=now + timedelta(days=1), force=True, scorecard={}, cashdata=[])
        assert not [k for k in later["sent"] if k.startswith(("stale:job", "stale:watch"))]         # the finding is a day old: not re-pushed
    finally:
        with main.get_db() as con:
            con.execute("DELETE FROM app_settings WHERE key IN (?, ?)", (cj.status_key("watchdog"), cj.status_key("invisible")))
            con.commit()


# ── the home banner: the cloud report beside the PC's ─────────────────────────
@pytest.fixture
def usage_home(tmp_path, monkeypatch):
    import core
    import main
    monkeypatch.setattr(core, "DB_PATH", tmp_path / "t.db")
    monkeypatch.setattr(main, "DB_PATH", tmp_path / "t.db")
    return main


def put_health(main, key, report, received):
    main._usage_meta_set(key, json.dumps({"received_at": received, "report": report}))


PC = {"host": "OFFICE-PC", "checked_at": "2026-10-22 11:15:00", "signature": "Oshinei Push Invisible=fail",
      "tasks": [{"name": "Oshinei Push Invisible", "level": "fail", "meaning": "x"}]}
CLOUD = {"host": "fly", "source": "cloud", "checked_at": "2026-10-22 11:30:00", "signature": "invisible=missed",
         "tasks": [{"name": "ส่งข้อมูลไป Invisible Voice", "level": "missed", "meaning": "m"}, {"name": "ดึงรีวิวลูกค้า", "level": "ok"}]}


def test_without_a_cloud_report_the_banner_is_exactly_the_pcs(usage_home):
    main = usage_home
    assert main._task_health_view() == {"known": False}
    put_health(main, "task_health", PC, "2026-10-22 11:16:00")
    v = main._task_health_view(now=datetime(2026, 10, 22, 12, 0))
    assert v["host"] == "OFFICE-PC" and "source" not in v and not v["silent"] and len(v["problems"]) == 1


def test_a_cloud_report_alone_is_the_heartbeat_and_a_silent_pc_is_no_longer_a_finding(usage_home):
    main = usage_home
    put_health(main, "task_health", PC, "2026-10-10 11:16:00")                 # the PC was switched off after the cut-over
    put_health(main, "task_health_cloud", CLOUD, "2026-10-22 11:30:00")
    v = main._task_health_view(now=datetime(2026, 10, 22, 12, 0))
    assert v["source"] == "cloud" and v["silent"] is False and v["host"] == "fly"
    assert [p["name"] for p in v["problems"]] == ["ส่งข้อมูลไป Invisible Voice"] and v["signature"] == "invisible=missed"
    later = main._task_health_view(now=datetime(2026, 10, 24, 12, 0))
    assert later["silent"] is True                                             # the cloud watchdog itself went quiet


def test_while_both_report_the_banner_lists_both(usage_home):
    main = usage_home
    put_health(main, "task_health", PC, "2026-10-22 11:16:00")
    put_health(main, "task_health_cloud", CLOUD, "2026-10-22 11:30:00")
    v = main._task_health_view(now=datetime(2026, 10, 22, 12, 0))
    assert v["source"] == "both" and len(v["problems"]) == 2 and v["signature"] == "Oshinei Push Invisible=fail|invisible=missed"


# ── the status survives a data swap, and is not handed to every role ─────────
def test_the_status_lives_in_a_preserved_table_and_survives_the_swap(tmp_path):
    import core
    assert "app_settings" in core._PRESERVED_EXACT
    live, inc = tmp_path / "live.db", tmp_path / "inc.db"
    for path, rec in ((live, {"result": "ok", "detail": {"sales_rows": 9}}), (inc, {"result": "failed"})):
        c = sqlite3.connect(str(path))
        c.execute("CREATE TABLE app_settings(key TEXT PRIMARY KEY, value TEXT NOT NULL DEFAULT '')")
        c.execute("INSERT INTO app_settings VALUES(?, ?)", (cj.status_key("invisible"), json.dumps(rec)))
        c.execute("INSERT INTO app_settings VALUES(?, ?)", (cj.status_key("watchdog"), json.dumps(rec)))
        c.execute("CREATE TABLE sales_transaction(id INTEGER PRIMARY KEY)")
        c.commit()
        c.close()
    safe, reason = core._carry_forward_and_verify(str(live), str(inc))
    assert safe, reason
    c = sqlite3.connect(str(inc))
    got = json.loads(c.execute("SELECT value FROM app_settings WHERE key=?", (cj.status_key("invisible"),)).fetchone()[0])
    c.close()
    assert got == {"result": "ok", "detail": {"sales_rows": 9}}                 # the LIVE copy wins


def test_settings_endpoint_does_not_hand_the_job_statuses_to_every_role(client):
    import main
    with main.get_db() as con:
        con.execute("INSERT OR REPLACE INTO app_settings(key, value) VALUES(?, ?)", (cj.status_key("feedback"), "{}"))
        con.commit()
    try:
        assert cj.status_key("feedback") not in client.get("/api/settings").json()
    finally:
        with main.get_db() as con:
            con.execute("DELETE FROM app_settings WHERE key=?", (cj.status_key("feedback"),))
            con.commit()


# ── the admin endpoints ──────────────────────────────────────────────────────
@pytest.fixture
def auth_on(client, monkeypatch):
    monkeypatch.setenv("OSHINEI_AUTH", "1")
    monkeypatch.setenv("OSHINEI_AUTH_SECRET", "test-secret-stable")
    monkeypatch.delenv("OSHINEI_SEED_TOKEN", raising=False)
    for k in cb.CRED_KEYS:
        monkeypatch.delenv(k, raising=False)
    for name in cj.SPECS:
        monkeypatch.delenv(cj.SPECS[name]["flag"], raising=False)
    monkeypatch.delenv("OSHINEI_CLOUD_SYNC", raising=False)
    monkeypatch.delenv("OSHINEI_CLOUD_BACKUP", raising=False)
    yield


def test_status_endpoint_is_managements_alone_and_lists_all_five_jobs(auth_on):
    from fastapi.testclient import TestClient
    import main
    assert TestClient(main.app).get("/api/admin/cloud-jobs/status").status_code == 401
    for who in ("salessupport1", "branch1", "analyst1", "operation1"):
        assert _login(who).get("/api/admin/cloud-jobs/status").status_code == 403, who
    r = _login("management1").get("/api/admin/cloud-jobs/status")
    assert r.status_code == 200
    jobs = {j["job"]: j for j in r.json()["jobs"]}
    assert list(jobs) == ["sync", "backup", "feedback", "invisible", "watchdog"]
    assert jobs["feedback"]["times"] == ["09:00"] and jobs["invisible"]["times"] == ["10:45"] and jobs["watchdog"]["times"] == ["11:15", "17:00"]
    assert jobs["sync"]["times"] == ["06:00", "12:00", "16:00"] and jobs["backup"]["times"] == ["07:30"]
    assert all(j["enabled"] is False and j["next_run"] is None and "label" in j and "result" in j for j in jobs.values())


def test_status_shows_next_run_and_last_result_for_the_jobs_that_are_on(auth_on, monkeypatch):
    import main
    monkeypatch.setenv("OSHINEI_CLOUD_FEEDBACK", "1")
    with main.get_db() as con:
        con.execute("INSERT OR REPLACE INTO app_settings(key, value) VALUES(?, ?)",
                    (cj.status_key("feedback"), json.dumps({"result": "no_source", "last_end": "2026-10-22T09:00:05+07:00", "streak": 0})))
        con.commit()
    try:
        jobs = {j["job"]: j for j in _login("management1").get("/api/admin/cloud-jobs/status").json()["jobs"]}
    finally:
        with main.get_db() as con:
            con.execute("DELETE FROM app_settings WHERE key=?", (cj.status_key("feedback"),))
            con.commit()
    f = jobs["feedback"]
    assert f["enabled"] is True and f["result"] == "no_source" and f["next_run"] and f["runnable"] is True
    assert jobs["invisible"]["enabled"] is False


def test_run_endpoint_needs_management_and_the_write_token(auth_on, monkeypatch):
    monkeypatch.setenv("OSHINEI_SEED_TOKEN", TOKEN)
    monkeypatch.setenv("OSHINEI_CLOUD_FEEDBACK", "1")
    started = []
    monkeypatch.setattr(cj.get("feedback"), "run_in_background", lambda: (started.append(1) or True))
    body = {"job": "feedback"}
    for who in ("salessupport1", "branch1", "analyst1"):
        assert _login(who).post("/api/admin/cloud-jobs/run", json=body, headers={"X-Upload-Token": TOKEN}).status_code == 403, who
    boss = _login("management1")
    assert boss.post("/api/admin/cloud-jobs/run", json=body).status_code == 403                     # no token
    assert boss.post("/api/admin/cloud-jobs/run", json=body, headers={"X-Upload-Token": "wrong"}).status_code == 403
    assert started == []
    r = boss.post("/api/admin/cloud-jobs/run", json=body, headers={"X-Upload-Token": TOKEN})
    assert r.status_code == 200 and r.json() == {"ok": True, "started": True, "job": "feedback"} and started == [1]


def test_run_endpoint_refuses_unknown_jobs_when_off_and_when_already_running(auth_on, monkeypatch):
    monkeypatch.setenv("OSHINEI_SEED_TOKEN", TOKEN)
    boss = _login("management1")
    hdr = {"X-Upload-Token": TOKEN}
    assert boss.post("/api/admin/cloud-jobs/run", json={"job": "sync"}, headers=hdr).status_code == 400      # sync / backup have their own pages
    assert boss.post("/api/admin/cloud-jobs/run", json={"job": "nonsense"}, headers=hdr).status_code == 400
    assert boss.post("/api/admin/cloud-jobs/run", json={"job": "invisible"}, headers=hdr).status_code == 409   # off: inert
    monkeypatch.setenv("OSHINEI_CLOUD_INVISIBLE", "1")
    monkeypatch.setattr(cj.get("invisible"), "run_in_background", lambda: False)
    assert boss.post("/api/admin/cloud-jobs/run", json={"job": "invisible"}, headers=hdr).status_code == 409   # already running


def test_the_endpoints_are_open_to_nobody_but_system_admin_in_the_role_table():
    import auth
    for method in ("GET", "POST"):
        for path in ("/api/admin/cloud-jobs/status", "/api/admin/cloud-jobs/run"):
            allowed = [r for r in auth.ROLES if auth.path_allowed(method, path, auth.modules_for_role(r))]
            assert allowed == ["management"], (method, path, allowed)


# ── the secrets the docs tell the owner to import: exactly the names the code reads ──
def test_the_documented_secret_names_are_the_ones_the_code_reads():
    doc = (HERE.parent / "docs" / "CLOUD_SYNC.md").read_text(encoding="utf-8")
    section = doc[doc.index("งานอื่นบน cloud"):]
    for names in cj.ENV_NAMES.values():
        for n in names:
            assert n in section, n
    for n in ("OSHINEI_CLOUD_FEEDBACK=1", "OSHINEI_CLOUD_INVISIBLE=1", "OSHINEI_CLOUD_WATCHDOG=1"):
        assert n in section
    assert 'findstr /R "^INVISIBLE_API_KEY= ^INVISIBLE_API_URL=' in section
    assert "flyctl secrets import -a oshinei-dashboard --stage" in section
    src = (HERE / "cloud_jobs.py").read_text(encoding="utf-8") + (HERE / "push_invisible.py").read_text(encoding="utf-8")
    for n in ("FEEDBACK_CONFIG_B64", "INVISIBLE_API_KEY", "INVISIBLE_API_URL"):
        assert n in src
