# -*- coding: utf-8 -*-
"""The cloud pull of Easy POS (backend/cloud_sync.py, PR-CS): the schedule, the guards in front of the child,
how its exit code becomes a status, the status surviving a data swap, the admin endpoints and the push alert.

No MSSQL and no real child: the process is a scripted fake, the database a scratch SQLite file, the clock a
fixed Bangkok time. test_sync_main.py / test_sync_golden.py keep the sync's own output pinned.
"""
import json
import os
import sqlite3
import subprocess
import sys
from collections import namedtuple
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import pytest

HERE = Path(__file__).parent.resolve()
sys.path.insert(0, str(HERE))

import cloud_sync as cs  # noqa: E402
from test_sync_lock import _holder  # noqa: E402

BKK = cs.BKK
CREDS = {"OSH_SVR": "10.1.2.3", "OSH_PORT": "14369", "OSH_USER": "ro_user", "OSH_PWD": "pw-123456", "OSH_DB": "HQ_Oshinei"}
Usage = namedtuple("Usage", "total used free")
TIMES = cs.parse_times("06:00,12:00,16:00")


def at(h, m=0, day=21):
    return datetime(2026, 10, day, h, m, tzinfo=BKK)


# ── the schedule ─────────────────────────────────────────────────────────────
def test_parse_times_sorts_dedups_and_falls_back():
    assert cs.parse_times("16:00, 06:00,12:00,06:00") == [(6, 0), (12, 0), (16, 0)]
    assert cs.parse_times("bad,25:00,7:5,12:60") == [(7, 5)]
    assert cs.parse_times("") == TIMES and cs.parse_times(None) == TIMES and cs.parse_times("nonsense") == TIMES
    assert cs.configured_times({}) == TIMES
    assert cs.configured_times({"OSHINEI_CLOUD_SYNC_TIMES": "07:30;13:00"}) == [(7, 30), (13, 0)]


@pytest.mark.parametrize("now, prev, nxt", [
    (at(5, 59), at(16, 0, day=20), at(6, 0)),            # before the first slot: yesterday's last one
    (at(6, 0), at(6, 0), at(12, 0)),                     # exactly on a slot: that slot has started
    (at(12, 5), at(12, 0), at(16, 0)),
    (at(16, 30), at(16, 0), at(6, 0, day=22)),           # after the last slot: tomorrow's first
    (at(23, 59), at(16, 0), at(6, 0, day=22)),
])
def test_slot_math_in_bangkok_time(now, prev, nxt):
    assert cs.previous_slot(now, TIMES) == prev
    assert cs.next_slot(now, TIMES) == nxt


def test_a_clock_in_utc_is_read_as_bangkok():
    utc = datetime(2026, 10, 20, 23, 0, tzinfo=timezone.utc)            # = 06:00 on the 21st in Bangkok
    assert cs.previous_slot(utc, TIMES) == at(6, 0)
    assert cs.next_slot(utc, TIMES) == at(12, 0)


def test_the_first_slot_of_the_day_is_the_full_rebuild():
    assert cs.is_full_slot(at(6, 0), TIMES)
    assert not cs.is_full_slot(at(12, 0), TIMES) and not cs.is_full_slot(at(16, 0), TIMES)
    assert cs.is_full_slot(at(7, 30), [(7, 30), (13, 0)])                # the rule follows the configured times


def test_full_run_takes_no_window_and_the_others_re_pull_45_days():
    assert cs.child_args(True) == []
    assert cs.child_args(False) == ["--window-days", "45", "--full-every-days", "7"]


def test_the_child_command_line_is_exact():
    sync_py = str(HERE / "sync_mssql.py")
    assert cs.child_command(True, python="/usr/bin/python", nice=True) == \
        ["nice", "-n", "10", "/usr/bin/python", "-u", sync_py]
    assert cs.child_command(False, python="/usr/bin/python", nice=True) == \
        ["nice", "-n", "10", "/usr/bin/python", "-u", sync_py, "--window-days", "45", "--full-every-days", "7"]
    assert cs.child_command(False, python="py", nice=False)[0] == "py"          # no nice(1) on this machine: bare python -u


def test_the_child_env_points_at_the_volume_and_drops_the_secrets_it_does_not_need():
    base = {**CREDS, "OSHINEI_SEED_TOKEN": "t", "OSHINEI_VAPID_PRIVATE": "v", "OSHINEI_AUTH_SECRET": "s", "KEEP": "1"}
    env = cs.child_env("/data/oshinei.db", base)
    assert env["OSHINEI_DB"] == "/data/oshinei.db"
    assert env["OSHINEI_SYNC_STAGE_PRIVATE"] == env["OSHINEI_SYNC_LOCK_HELD"] == env["OSHINEI_SYNC_ON_LIVE"] == "1"
    assert env["OSH_PWD"] == CREDS["OSH_PWD"] and env["KEEP"] == "1"           # the MSSQL login does reach it
    assert not {"OSHINEI_SEED_TOKEN", "OSHINEI_VAPID_PRIVATE", "OSHINEI_AUTH_SECRET"} & set(env)


def test_exit_codes_map_to_results():
    assert cs.result_for_exit(0) == "ok"
    assert cs.result_for_exit(3) == "reconcile_failed"                         # sync_mssql._sync_reconcile
    for rc in (1, 2, 4, 137, -9, None):
        assert cs.result_for_exit(rc) == "failed"
    assert cs.result_for_exit(0, timed_out=True) == "failed"


def test_the_feature_is_off_unless_the_flag_is_one():
    assert not cs.enabled({}) and not cs.enabled({"OSHINEI_CLOUD_SYNC": "0"}) and not cs.enabled({"OSHINEI_CLOUD_SYNC": ""})
    assert cs.enabled({"OSHINEI_CLOUD_SYNC": "1"}) and cs.enabled({"OSHINEI_CLOUD_SYNC": "true"})


# ── a Cloud wired to scratch parts ───────────────────────────────────────────
class FakeProc:
    def __init__(self, lines, rc):
        self.stdout = iter(lines)
        self.pid = 999_999
        self._rc = rc
        self.killed = False

    def wait(self, timeout=None):
        return self._rc

    def kill(self):
        self.killed = True


class Rig:
    def __init__(self, tmp_path, *, env=None, free=10 * 10**9, lines=("[1/6] copy\n", "[6/6] done\n"), rc=0,
                 clock=None, sleep=None, swap_wait_s=0):
        self.tmp = tmp_path
        self.db = tmp_path / "oshinei.db"
        con = sqlite3.connect(str(self.db))
        con.execute("CREATE TABLE app_settings(key TEXT PRIMARY KEY, value TEXT NOT NULL DEFAULT '')")
        con.execute("CREATE TABLE sales_transaction(id INTEGER PRIMARY KEY)")
        con.executemany("INSERT INTO sales_transaction(id) VALUES(?)", [(i,) for i in range(1, 8)])
        con.commit()
        con.close()
        self.calls = []
        self.lines, self.rc = list(lines), rc
        self.env = {**CREDS, **(env or {})}
        self.now = [clock or at(12, 5)]
        self.cloud = cs.Cloud(db=self._db, db_path=self.db, popen=self._popen, clock=lambda: self.now[0], env=self.env,
                              lock_path=tmp_path / "x.lock", log_dir=tmp_path / "logs",
                              usage=lambda p: Usage(10**11, 0, free), swap_wait_s=swap_wait_s,
                              sleep=sleep or (lambda s: None))

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

    def status(self):
        return cs.read_status(self._db)


def test_a_finished_run_is_recorded_with_the_numbers_the_page_shows(tmp_path):
    rig = Rig(tmp_path, env={"OSHINEI_NO_AUTOSWAP": "1"}, lines=["[1/6] copy", "10.1.2.3 refused"])
    inc = Path(str(rig.db) + ".incoming")
    inc.write_bytes(b"staged")
    rig.cloud.apply_fn = lambda: (inc.unlink() or True)
    st = rig.cloud.run("schedule", slot=at(12, 0))
    assert st["result"] == "ok" and st["exit_code"] == 0 and st["swapped"] is True and st["running"] is False
    assert st["kind"] == "incremental" and st["trigger"] == "schedule" and st["tx_count"] == 7
    assert st["last_ok"] == st["last_end"] and st["streak"] == 0 and st["duration_s"] is not None
    assert st["log_tail"] == ["[1/6] copy", "*** refused"]                     # the server's address never reaches the page
    assert rig.status() == st                                                  # persisted under app_settings
    cmd, kw = rig.calls[0]
    assert cmd[-4:] == ["--window-days", "45", "--full-every-days", "7"]
    assert kw["env"]["OSHINEI_DB"] == str(rig.db) and kw["env"]["OSHINEI_SYNC_LOCK_HELD"] == "1"


def test_the_first_slot_runs_the_full_rebuild(tmp_path):
    rig = Rig(tmp_path, env={"OSHINEI_NO_AUTOSWAP": "1"}, clock=at(6, 5))
    inc = Path(str(rig.db) + ".incoming")
    inc.write_bytes(b"staged")
    rig.cloud.apply_fn = lambda: (inc.unlink() or True)
    st = rig.cloud.run("schedule", slot=at(6, 0))
    cmd = rig.calls[0][0]
    assert st["kind"] == "full" and "--window-days" not in cmd and cmd[-1].endswith("sync_mssql.py")


def test_exit_3_is_the_reconcile_gate_and_leaves_everything_alone(tmp_path):
    rig = Rig(tmp_path, rc=3, lines=["RECONCILE FAILED"])
    inc = Path(str(rig.db) + ".incoming")
    rig.cloud.apply_fn = lambda: pytest.fail("a refused build must never be swapped")
    st = rig.cloud.run("schedule", slot=at(12, 0))
    assert st["result"] == "reconcile_failed" and st["exit_code"] == 3 and st["swapped"] is False and st["streak"] == 1
    assert not inc.exists()
    assert st["log_tail"] == ["RECONCILE FAILED"]
    st2 = rig.cloud.run("schedule", slot=at(16, 0))                            # twice in a row
    assert st2["streak"] == 2


def test_any_other_exit_is_a_failure_with_the_exit_code(tmp_path):
    rig = Rig(tmp_path, rc=1, lines=["MSSQL credentials missing: OSH_PWD"])
    st = rig.cloud.run("schedule", slot=at(12, 0))
    assert st["result"] == "failed" and st["exit_code"] == 1 and st["tx_count"] is None
    assert rig.cloud.run("schedule", slot=at(16, 0))["streak"] == 2
    rig.rc = 0                                                                  # the next one passes: the streak resets
    inc = Path(str(rig.db) + ".incoming")
    inc.write_bytes(b"staged")
    rig.env["OSHINEI_NO_AUTOSWAP"] = "1"
    rig.cloud.apply_fn = lambda: (inc.unlink() or True)
    assert rig.cloud.run("manual")["streak"] == 0


def test_the_log_tail_keeps_the_last_forty_lines(tmp_path):
    rig = Rig(tmp_path, rc=1, lines=[f"line {i}" for i in range(100)])
    tail = rig.cloud.run("manual")["log_tail"]
    assert len(tail) == 40 and tail[0] == "line 60" and tail[-1] == "line 99"


def test_exit_0_waits_for_the_pollers_swap(tmp_path):
    import shutil
    db = tmp_path / "oshinei.db"
    inc = Path(str(db) + ".incoming")
    rig = Rig(tmp_path, sleep=lambda s: os.replace(str(inc), str(db)) if inc.exists() else None, swap_wait_s=30)
    shutil.copy2(db, inc)                                                       # the finished build the poller will swap in
    st = rig.cloud.run("manual")
    assert st["result"] == "ok" and st["swapped"] is True and not inc.exists()


def test_a_staged_file_the_poller_discarded_is_not_a_swap(tmp_path):
    """_apply_incoming_live deletes a .incoming that fails its quick_check: gone from the folder, but the live DB did not change."""
    rig = Rig(tmp_path, sleep=lambda s: Path(str(tmp_path / "oshinei.db") + ".incoming").unlink(missing_ok=True), swap_wait_s=30)
    Path(str(rig.db) + ".incoming").write_bytes(b"corrupt")
    st = rig.cloud.run("manual")
    assert st["result"] == "failed" and st["swapped"] is False and any("discarded" in l for l in st["log_tail"])


def test_exit_0_but_the_swap_never_happens_is_a_failure(tmp_path):
    rig = Rig(tmp_path, swap_wait_s=0)
    Path(str(rig.db) + ".incoming").write_bytes(b"staged")
    st = rig.cloud.run("manual")
    assert st["result"] == "failed" and st["exit_code"] == 0 and st["swapped"] is False
    assert any("not swapped" in l for l in st["log_tail"])


def test_exit_0_without_a_published_build_is_a_failure(tmp_path):
    st = Rig(tmp_path).cloud.run("manual")
    assert st["result"] == "failed" and any("no .incoming" in l for l in st["log_tail"])


def test_with_the_poller_off_the_same_swap_function_is_called_once(tmp_path):
    rig = Rig(tmp_path, env={"OSHINEI_NO_AUTOSWAP": "1"})
    Path(str(rig.db) + ".incoming").write_bytes(b"staged")
    called = []
    rig.cloud.apply_fn = lambda: (called.append(1) or True)
    st = rig.cloud.run("manual")
    assert called == [1] and st["result"] == "ok"
    (tmp_path / "b").mkdir()
    rig2 = Rig(tmp_path / "b", env={"OSHINEI_NO_AUTOSWAP": "1"})
    Path(str(rig2.db) + ".incoming").write_bytes(b"staged")
    rig2.cloud.apply_fn = None
    assert rig2.cloud.run("manual")["result"] == "failed"                      # no swap wired: never silently "ok"


def test_creds_missing_is_a_status_not_a_crash(tmp_path):
    rig = Rig(tmp_path)
    rig.env.pop("OSH_PWD")
    rig.env["OSH_SVR"] = " "
    st = rig.cloud.run("schedule", slot=at(12, 0))
    assert st["result"] == "creds_missing" and rig.calls == []
    assert "OSH_SVR" in st["log_tail"][0] and "OSH_PWD" in st["log_tail"][0]


def test_not_enough_free_space_skips_without_starting_the_child(tmp_path):
    rig = Rig(tmp_path)
    size = rig.db.stat().st_size
    need = int(size * 2.5)
    rig.cloud._usage = lambda p: Usage(10**9, 0, need - 1)                      # one byte short of 2.5 x oshinei.db
    st = rig.cloud.run("schedule", slot=at(12, 0))
    assert st["result"] == "no_space" and rig.calls == [] and "need" in st["log_tail"][0]
    assert cs.space_check(rig.db, usage=lambda p: Usage(1, 0, need))[0] is True
    assert cs.space_check(rig.db, usage=lambda p: Usage(1, 0, need - 1)) == (False, need - 1, need)
    rig.cloud._usage = lambda p: Usage(10**9, 0, need)
    assert rig.cloud.run("schedule", slot=at(16, 0))["result"] != "no_space"


def test_a_leftover_private_build_counts_as_free_space(tmp_path):
    db = tmp_path / "oshinei.db"
    db.write_bytes(b"x" * 100_000)
    Path(str(db) + ".incoming.new").write_bytes(b"y" * 60_000)                 # a killed run's build, deleted by the next
    assert cs.space_check(db, usage=lambda p: Usage(1, 0, 200_000)) == (True, 260_000, 250_000)


def test_a_held_sync_lock_skips_the_run(tmp_path):
    rig = Rig(tmp_path)
    holder = _holder(tmp_path / "x.lock", 30)                                   # another job, in another process
    try:
        st = rig.cloud.run("schedule", slot=at(12, 0))
    finally:
        holder.kill()
        holder.wait()
    assert st["result"] == "locked" and rig.calls == []
    assert rig.cloud.running is False
    assert rig.cloud.run("manual")["result"] != "locked"                       # released: the next run goes ahead


def test_a_second_run_while_one_is_going_is_refused(tmp_path):
    rig = Rig(tmp_path)
    assert rig.cloud._run_lock.acquire(blocking=False)
    try:
        assert rig.cloud.run("manual") is None
        assert rig.cloud.run_in_background() is False
    finally:
        rig.cloud._run_lock.release()


def test_a_hung_child_is_killed_at_the_timeout(tmp_path, monkeypatch):
    rig = Rig(tmp_path, env={"OSHINEI_CLOUD_SYNC_TIMEOUT_MIN": "0.0005"}, rc=-9)         # 30 ms

    class Hung(FakeProc):
        def __init__(self):
            import threading
            self.ev = threading.Event()
            self.stdout = self._lines()
            self.pid, self._rc, self.killed = 999_998, -9, False

        def _lines(self):
            yield "[1/6] copy\n"
            self.ev.wait(5)                                                      # blocks until killed
            return

        def kill(self):
            self.killed = True
            self.ev.set()
    proc = Hung()
    rig._popen = lambda cmd, **kw: (rig.calls.append(cmd) or proc)
    rig.cloud._popen = rig._popen
    monkeypatch.setattr(cs, "_kill_tree", lambda p: p.kill())
    st = rig.cloud.run("manual")
    assert proc.killed and st["result"] == "failed" and any("killed after" in l for l in st["log_tail"])


# ── the catch-up rule and the loop ───────────────────────────────────────────
def _with_status(rig, **kw):
    cs.write_status(kw, rig._db)


def test_catch_up_runs_when_the_last_success_is_older_than_the_previous_slot(tmp_path):
    rig = Rig(tmp_path)
    assert rig.cloud.catch_up_due(at(16, 30)) == at(16, 0)                      # never ran: catch up
    _with_status(rig, last_ok=at(12, 10).isoformat(), last_start=at(12, 0).isoformat())
    assert rig.cloud.catch_up_due(at(16, 30)) == at(16, 0)                      # the 16:00 slot was missed
    _with_status(rig, last_ok=at(16, 10).isoformat())
    assert rig.cloud.catch_up_due(at(16, 30)) is None                           # up to date
    _with_status(rig, last_ok=at(12, 10).isoformat(), last_start=at(16, 20).isoformat())
    assert rig.cloud.catch_up_due(at(16, 30)) is None                           # a pull started 10 min ago: a crash loop must not repeat it
    _with_status(rig, last_ok=at(12, 10, day=20).isoformat(), last_start=at(12, 0, day=20).isoformat())
    assert rig.cloud.catch_up_due(at(5, 0)) == at(16, 0, day=20)                # before the first slot: yesterday's last


def test_the_loop_catches_up_once_then_runs_each_slot_once(tmp_path):
    rig = Rig(tmp_path, clock=at(16, 30))
    runs = []
    rig.cloud.run = lambda trigger, full=None, slot=None: (runs.append((trigger, slot)) or {})
    stop = []

    def sleeper(s):
        stop.append(s)
        if len(stop) >= 3:
            raise KeyboardInterrupt
    rig.cloud._sleep = sleeper
    with pytest.raises(KeyboardInterrupt):
        rig.cloud.loop(startup_delay=0, tick_s=1)
    assert runs == [("catchup", at(16, 0))]                                     # one catch-up, and the loop's first look adds nothing


def test_tick_starts_one_run_per_slot(tmp_path):
    rig = Rig(tmp_path)
    runs = []
    rig.cloud.run = lambda trigger, full=None, slot=None: (runs.append(slot) or {})
    rig.cloud.handled_slot = at(6, 0)
    assert rig.cloud.tick(at(11, 59)) is None and runs == []
    rig.cloud.tick(at(12, 0))
    assert rig.cloud.tick(at(12, 0)) is None and rig.cloud.tick(at(15, 59)) is None
    rig.cloud.tick(at(16, 1))
    rig.cloud.tick(at(23, 0))
    assert runs == [at(12, 0), at(16, 0)]


def test_a_running_flag_left_by_a_dead_process_becomes_a_failed_run(tmp_path):
    rig = Rig(tmp_path)
    _with_status(rig, running=True, result="ok", last_start=at(12, 0).isoformat(), log_tail=["[3/6] aggregate"])
    rig.cloud.reconcile_stale_running()
    st = rig.status()
    assert st["running"] is False and st["result"] == "failed" and st["log_tail"][-1].startswith("interrupted")


def test_the_page_view_has_the_next_run_and_the_flag(tmp_path):
    rig = Rig(tmp_path, clock=at(12, 5), env={"OSHINEI_CLOUD_SYNC": "1"})
    v = rig.cloud.view()
    assert v["enabled"] is True and v["running"] is False and v["creds_present"] is True
    assert v["times"] == ["06:00", "12:00", "16:00"] and v["next_run"] == at(16, 0).isoformat() and v["next_kind"] == "incremental"
    rig.now[0] = at(17, 0)
    v = rig.cloud.view()
    assert v["next_run"] == at(6, 0, day=22).isoformat() and v["next_kind"] == "full"
    (tmp_path / "off").mkdir()
    assert Rig(tmp_path / "off").cloud.view()["enabled"] is False


# ── inert when off ───────────────────────────────────────────────────────────
def test_start_is_inert_without_the_flag(monkeypatch):
    monkeypatch.delenv("OSHINEI_CLOUD_SYNC", raising=False)
    before = cs.get().thread
    assert cs.start(apply_fn=lambda: True) is None
    assert cs.get().thread is before
    import main
    main._cloud_sync_startup()                                                 # the app's startup hook: nothing starts
    assert cs.get().thread is before


def test_the_hot_swap_poller_and_the_cloud_sync_share_one_swap_function():
    import main
    import inspect
    assert "_swap_incoming_and_warm()" in inspect.getsource(main._autoswap_poller)
    assert "_apply_incoming_live()" in inspect.getsource(main._swap_incoming_and_warm)


# ── the status survives a data swap ──────────────────────────────────────────
def test_the_status_lives_in_a_preserved_table_and_survives_the_swap(tmp_path):
    import core
    assert "app_settings" in core._PRESERVED_EXACT
    live, inc = tmp_path / "live.db", tmp_path / "inc.db"
    c = sqlite3.connect(str(live))
    c.execute("CREATE TABLE app_settings(key TEXT PRIMARY KEY, value TEXT NOT NULL DEFAULT '')")
    c.execute("INSERT INTO app_settings VALUES(?, ?)", (cs.STATUS_KEY, json.dumps({"result": "ok", "tx_count": 5})))
    c.execute("INSERT INTO app_settings VALUES('role_permissions', '{}')")
    c.commit(); c.close()
    c = sqlite3.connect(str(inc))                    # what the sync built from an older copy: another status
    c.execute("CREATE TABLE app_settings(key TEXT PRIMARY KEY, value TEXT NOT NULL DEFAULT '')")
    c.execute("INSERT INTO app_settings VALUES(?, ?)", (cs.STATUS_KEY, json.dumps({"result": "failed"})))
    c.execute("CREATE TABLE sales_transaction(id INTEGER PRIMARY KEY)")
    c.commit(); c.close()
    safe, reason = core._carry_forward_and_verify(str(live), str(inc))
    assert safe, reason
    c = sqlite3.connect(str(inc))
    got = json.loads(c.execute("SELECT value FROM app_settings WHERE key=?", (cs.STATUS_KEY,)).fetchone()[0])
    c.close()
    assert got == {"result": "ok", "tx_count": 5}                              # the LIVE copy wins


def test_settings_endpoint_does_not_hand_the_log_to_every_role(client):
    with __import__("main").get_db() as con:
        con.execute("INSERT OR REPLACE INTO app_settings(key, value) VALUES(?, ?)", (cs.STATUS_KEY, "{}"))
        con.commit()
    try:
        assert cs.STATUS_KEY not in client.get("/api/settings").json()
    finally:
        with __import__("main").get_db() as con:
            con.execute("DELETE FROM app_settings WHERE key=?", (cs.STATUS_KEY,))
            con.commit()


# ── the admin endpoints ──────────────────────────────────────────────────────
TOKEN = "write-token-123"


def _login(user):
    import auth
    import main
    from fastapi.testclient import TestClient
    c = TestClient(main.app)
    r = c.post("/api/auth/login", json={"username": user, "password": auth.DEFAULT_PASSWORD})
    assert r.status_code == 200, r.text
    return c


@pytest.fixture
def auth_on(client, monkeypatch):
    monkeypatch.setenv("OSHINEI_AUTH", "1")
    monkeypatch.setenv("OSHINEI_AUTH_SECRET", "test-secret-stable")
    monkeypatch.delenv("OSHINEI_SEED_TOKEN", raising=False)
    yield


def test_status_endpoint_is_managements_alone(auth_on):
    anon = __import__("fastapi.testclient", fromlist=["TestClient"]).TestClient(__import__("main").app)
    assert anon.get("/api/admin/cloud-sync/status").status_code == 401
    for who in ("salessupport1", "branch1", "analyst1", "operation1"):
        assert _login(who).get("/api/admin/cloud-sync/status").status_code == 403, who
    r = _login("management1").get("/api/admin/cloud-sync/status")
    assert r.status_code == 200
    j = r.json()
    assert j["enabled"] is False and j["times"] == ["06:00", "12:00", "16:00"] and "next_run" in j and "status" in j


def test_run_endpoint_needs_management_and_the_write_token(auth_on, monkeypatch):
    monkeypatch.setenv("OSHINEI_SEED_TOKEN", TOKEN)
    monkeypatch.setenv("OSHINEI_CLOUD_SYNC", "1")
    started = []
    monkeypatch.setattr(cs.get(), "run_in_background", lambda full=False: (started.append(full) or True))
    for who in ("salessupport1", "branch1", "analyst1"):
        assert _login(who).post("/api/admin/cloud-sync/run", headers={"X-Upload-Token": TOKEN}).status_code == 403, who
    boss = _login("management1")
    assert boss.post("/api/admin/cloud-sync/run").status_code == 403                       # no token
    assert boss.post("/api/admin/cloud-sync/run", headers={"X-Upload-Token": "wrong"}).status_code == 403
    assert started == []
    r = boss.post("/api/admin/cloud-sync/run", headers={"X-Upload-Token": TOKEN})
    assert r.status_code == 200 and r.json()["started"] is True and started == [False]
    r = boss.post("/api/admin/cloud-sync/run", headers={"X-Upload-Token": TOKEN}, json={"full": True})
    assert r.json()["kind"] == "full" and started == [False, True]


def test_run_endpoint_refuses_when_off_or_already_running(auth_on, monkeypatch):
    monkeypatch.setenv("OSHINEI_SEED_TOKEN", TOKEN)
    boss = _login("management1")
    hdr = {"X-Upload-Token": TOKEN}
    monkeypatch.delenv("OSHINEI_CLOUD_SYNC", raising=False)
    assert boss.post("/api/admin/cloud-sync/run", headers=hdr).status_code == 409           # inert while off
    monkeypatch.setenv("OSHINEI_CLOUD_SYNC", "1")
    monkeypatch.setattr(cs.get(), "run_in_background", lambda full=False: False)
    assert boss.post("/api/admin/cloud-sync/run", headers=hdr).status_code == 409           # already running


def test_the_endpoints_are_open_to_nobody_but_system_admin_in_the_role_table():
    import auth
    for method in ("GET", "POST"):
        for path in ("/api/admin/cloud-sync/status", "/api/admin/cloud-sync/run"):
            allowed = [r for r in auth.ROLES if auth.path_allowed(method, path, auth.modules_for_role(r))]
            assert allowed == ["management"], (method, path, allowed)


# ── the push alert ───────────────────────────────────────────────────────────
def _cloud(result, streak=1, end=datetime(2026, 10, 21, 12, 40)):
    return {"result": result, "streak": streak, "running": False,
            "last_end": end.replace(tzinfo=BKK).isoformat()}


def test_failures_that_need_a_person_are_pushed_once_a_day_under_the_stale_kind():
    from routers import push_api as P
    now = datetime(2026, 10, 21, 13, 0)
    for res in ("failed", "no_space", "creds_missing"):
        got = P.cloud_sync_payloads(_cloud(res), now)
        assert len(got) == 1 and got[0][0] == f"stale:cloud:{res}:2026-10-21", res            # kind "stale": a device that muted it is skipped
        assert got[0][1]["tag"] == "oshinei-stale" and P.CLOUD_LABEL[res] in got[0][1]["body"]
    assert P.cloud_sync_payloads(_cloud("ok"), now) == [] and P.cloud_sync_payloads(_cloud("locked"), now) == []
    assert P.cloud_sync_payloads(_cloud("reconcile_failed", streak=1), now) == []             # once is a late-delivery day
    assert len(P.cloud_sync_payloads(_cloud("reconcile_failed", streak=2), now)) == 1          # twice in a row is not
    assert P.cloud_sync_payloads({**_cloud("failed"), "running": True}, now) == []            # a retry is under way
    assert P.cloud_sync_payloads(_cloud("failed", end=datetime(2026, 10, 19, 12, 0)), now) == []   # older than a day: the stale alert's job
    assert P.cloud_sync_payloads({}, now) == [] and P.cloud_sync_payloads(None, now) == []


def test_the_stuck_sync_alert_names_what_the_cloud_pull_did():
    from routers import push_api as P
    now = datetime(2026, 10, 7, 13, 0)
    sc = {"date": "2026-10-05", "branches": []}
    old = dict(P.alert_payloads(sc, now))["stale:2026-10-07"]["body"]
    assert "เครื่องซิงก์" in old                                                       # unchanged without the cloud pull
    body = dict(P.alert_payloads(sc, now, cloud=_cloud("failed", end=datetime(2026, 10, 7, 12, 40))))["stale:2026-10-07"]["body"]
    assert "ดึงข้อมูลไม่สำเร็จ" in body and "7 ต.ค. 12:40" in body and "เครื่องซิงก์" not in body
    assert "ยังไม่เคยรัน" in dict(P.alert_payloads(sc, now, cloud={}))["stale:2026-10-07"]["body"]


def test_alerts_send_the_cloud_failure_to_whole_chain_devices_only_while_the_pull_is_on(client, monkeypatch):
    import main
    from routers import push_api as P
    monkeypatch.setenv("OSHINEI_VAPID_PRIVATE", "cHJpdmF0ZQ")
    monkeypatch.setenv("OSHINEI_VAPID_PUBLIC", "cHVibGlj")
    with main.get_db() as con:
        P.ensure(con)
        con.execute("DELETE FROM push_subscription")
        con.execute("DELETE FROM usage_meta WHERE key LIKE 'push_%'")
        con.execute("INSERT INTO push_subscription(username, endpoint, p256dh, auth, created_at, branch) "
                    "VALUES ('boss', 'https://push.example/boss', 'p', 'a', '2026-10-21 08:00', '')")
        con.execute("INSERT OR REPLACE INTO app_settings(key, value) VALUES(?, ?)",
                    (cs.STATUS_KEY, json.dumps(_cloud("failed"))))
        con.commit()
    sent = []
    send = lambda sub, data: (sent.append(json.loads(data)) or 201)
    sc = {"date": "2026-10-20", "branches": []}
    monkeypatch.delenv("OSHINEI_CLOUD_SYNC", raising=False)
    r = P.maybe_send_alerts(now=datetime(2026, 10, 21, 13, 0), send=send, scorecard=sc, cashdata={})
    assert r["sent"] == [] and sent == []                                      # the feature is off: its status is ignored
    monkeypatch.setenv("OSHINEI_CLOUD_SYNC", "1")
    r = P.maybe_send_alerts(now=datetime(2026, 10, 21, 13, 11), send=send, scorecard=sc, cashdata={})
    assert "stale:cloud:failed:2026-10-21" in r["sent"] and any(m["title"] == "ดึงข้อมูลจาก POS ไม่สำเร็จ" for m in sent)
    n = len(sent)
    P.maybe_send_alerts(now=datetime(2026, 10, 21, 13, 22), send=send, scorecard=sc, cashdata={})
    assert len(sent) == n                                                      # once per day
    with main.get_db() as con:
        con.execute("DELETE FROM app_settings WHERE key=?", (cs.STATUS_KEY,))
        con.execute("DELETE FROM push_subscription")
        con.execute("DELETE FROM usage_meta WHERE key LIKE 'push_%'")
        con.commit()


# ── what sync_mssql.py does differently when the cloud runs it ───────────────
def _py(code, env):
    full = {**os.environ, **env}
    return subprocess.run([sys.executable, "-c", code], cwd=str(HERE), env=full, capture_output=True, text=True, timeout=60)


def test_the_sync_follows_oshinei_db_to_the_volume(tmp_path):
    code = "import sync_mssql as s; print(s.DB, s.INCOMING, s.STAGE_FALLBACK, sep='|')"
    out = _py(code, {"OSHINEI_DB": str(tmp_path / "oshinei.db")}).stdout.strip().split("|")
    assert out == [str(tmp_path / "oshinei.db"), str(tmp_path / "oshinei.db.incoming"), str(tmp_path / "oshinei.db.incoming.new")]
    env = {k: v for k, v in os.environ.items() if k != "OSHINEI_DB"}
    r = subprocess.run([sys.executable, "-c", code], cwd=str(HERE), env=env, capture_output=True, text=True, timeout=60)
    db, inc, new = r.stdout.strip().split("|")
    assert db.endswith("oshinei.db") and Path(db).parent == HERE and inc == db + ".incoming" and new == db + ".incoming.new"


def test_the_child_skips_the_lock_its_parent_holds(tmp_path):
    """OSHINEI_SYNC_LOCK_HELD=1: the script does not queue behind the lock cloud_sync.py holds for it."""
    from sync_lock import sync_lock
    env = {"OSHINEI_DB": str(tmp_path / "nothing.db"), "OSHINEI_SYNC_LOCK_WAIT": "0"}
    with sync_lock("parent", wait_s=0):
        blocked = subprocess.run([sys.executable, "-u", str(HERE / "sync_mssql.py")], cwd=str(HERE),
                                 env={**os.environ, **env}, capture_output=True, text=True, timeout=60)
        held = subprocess.run([sys.executable, "-u", str(HERE / "sync_mssql.py")], cwd=str(HERE),
                              env={**os.environ, **env, "OSHINEI_SYNC_LOCK_HELD": "1"}, capture_output=True, text=True, timeout=60)
    assert blocked.returncode != 0 and "still holds" in (blocked.stderr + blocked.stdout)
    assert "current DB not found" in (held.stderr + held.stdout)               # it got past the lock, to the work
