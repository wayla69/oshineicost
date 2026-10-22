# -*- coding: utf-8 -*-
"""The cloud backup of oshinei.db (backend/cloud_backup.py, PR-CB): the schedule, the guards in front of the copy,
the restore test, the upload and the 7-daily + 6-monthly retention, the status and the push alert, the admin endpoints.

No network and no Fly: the bucket is an in-memory stub injected as the S3 client, the database a scratch SQLite
file, the clock a fixed Bangkok time, the child process a scripted fake.
"""
import gzip
import hashlib
import json
import sqlite3
import sys
from collections import namedtuple
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path

import pytest

HERE = Path(__file__).parent.resolve()
sys.path.insert(0, str(HERE))

import cloud_backup as cb  # noqa: E402
import db_backup  # noqa: E402
from test_sync_lock import _holder  # noqa: E402

BKK = cb.BKK
CREDS = {"AWS_ACCESS_KEY_ID": "AKIAEXAMPLE12345", "AWS_SECRET_ACCESS_KEY": "secret-key-abcdef-123456",
         "AWS_ENDPOINT_URL_S3": "https://fly.storage.tigris.dev", "AWS_REGION": "auto", "BUCKET_NAME": "oshinei-backups"}
Usage = namedtuple("Usage", "total used free")
TIMES = [(7, 30)]


def at(h, m=0, day=22):
    return datetime(2026, 10, day, h, m, tzinfo=BKK)


# ── a bucket in memory ───────────────────────────────────────────────────────
class FakeS3:
    """Just the calls cloud_backup makes. `corrupt` stores a truncated object (a silent upload fault)."""

    def __init__(self, objects=None, fail_upload=False, corrupt=False, page=2):
        self.objects = {}                 # key -> (bytes, metadata)
        for k in objects or []:
            self.objects[k] = (b"old", {"sha256": "x"})
        self.fail_upload, self.corrupt, self.page = fail_upload, corrupt, page
        self.deleted = []
        self.calls = []

    def upload_file(self, path, bucket, key, ExtraArgs=None):
        self.calls.append(("upload_file", key))
        if self.fail_upload:
            raise RuntimeError("EndpointConnectionError: could not reach the bucket")
        data = Path(path).read_bytes()
        self.objects[key] = (data[:-5] if self.corrupt else data, dict((ExtraArgs or {}).get("Metadata") or {}))

    def head_object(self, Bucket, Key):
        data, meta = self.objects[Key]
        return {"ContentLength": len(data), "Metadata": meta}

    def put_object(self, Bucket, Key, Body, ContentType=None):
        self.calls.append(("put_object", Key))
        self.objects[Key] = (Body, {})

    def list_objects_v2(self, Bucket, Prefix="", ContinuationToken=None):
        keys = sorted(k for k in self.objects if k.startswith(Prefix))
        start = int(ContinuationToken or 0)
        chunk = keys[start:start + self.page]
        r = {"Contents": [{"Key": k, "Size": len(self.objects[k][0]), "LastModified": datetime(2026, 10, 1)} for k in chunk]}
        if start + self.page < len(keys):
            r["IsTruncated"], r["NextContinuationToken"] = True, str(start + self.page)
        return r

    def delete_objects(self, Bucket, Delete):
        for o in Delete["Objects"]:
            self.deleted.append(o["Key"])
            self.objects.pop(o["Key"], None)

    def download_file(self, Bucket, Key, Filename):
        Path(Filename).write_bytes(self.objects[Key][0])


def make_db(path: Path, sales=7):
    con = sqlite3.connect(str(path))
    con.execute("CREATE TABLE app_settings(key TEXT PRIMARY KEY, value TEXT NOT NULL DEFAULT '')")
    for t in ("recon_uploads", "recon_bank_lines", "recon_audit_log", "app_user"):
        con.execute(f"CREATE TABLE {t}(id INTEGER PRIMARY KEY, v TEXT)")
        con.executemany(f"INSERT INTO {t}(v) VALUES(?)", [("a",), ("b",)])
    con.execute("CREATE TABLE sales_transaction(id INTEGER PRIMARY KEY, v TEXT)")
    con.executemany("INSERT INTO sales_transaction(v) VALUES(?)", [("s",)] * sales)
    con.commit()
    con.close()
    return path


def key_for(day, hm="0730"):
    return f"db/oshinei_2026-{day}_{hm}.db.gz"


def run_perform(tmp_path, s3, *, env=None, now=None, free=10**11, db=None, **kw):
    db = db or make_db(tmp_path / "oshinei.db")
    lines = []
    res = cb.perform(env={**CREDS, **(env or {})}, db_path=db, now=now or at(7, 30), s3=s3,
                     lock_path=tmp_path / "x.lock", lock_wait_s=kw.pop("lock_wait_s", 0),
                     usage=lambda p: Usage(10**12, 0, free), log=lines.append, **kw)
    return res, lines


# ── the schedule ─────────────────────────────────────────────────────────────
def test_the_daily_slot_defaults_to_0730_and_a_typo_does_not_switch_it_off():
    assert cb.configured_times({}) == TIMES
    assert cb.configured_times({"OSHINEI_CLOUD_BACKUP_TIME": "08:15"}) == [(8, 15)]
    assert cb.configured_times({"OSHINEI_CLOUD_BACKUP_TIME": "nonsense"}) == TIMES
    assert cb.configured_times({"OSHINEI_CLOUD_BACKUP_TIME": "09:00,12:00"}) == [(9, 0)]      # one slot a day


@pytest.mark.parametrize("now, prev, nxt", [
    (at(7, 29), at(7, 30, day=21), at(7, 30)),
    (at(7, 30), at(7, 30), at(7, 30, day=23)),
    (at(23, 59), at(7, 30), at(7, 30, day=23)),
    (at(0, 5), at(7, 30, day=21), at(7, 30)),
])
def test_slot_math_in_bangkok_time(now, prev, nxt):
    import cloud_sync as cs
    assert cs.previous_slot(now, TIMES) == prev and cs.next_slot(now, TIMES) == nxt


def test_the_stamp_is_the_bangkok_minute():
    assert cb.stamp_for(at(7, 30)) == "2026-10-22_0730"
    from datetime import timezone
    assert cb.stamp_for(datetime(2026, 10, 21, 23, 5, tzinfo=timezone.utc)) == "2026-10-22_0605"


def test_the_feature_is_off_unless_the_flag_is_one():
    assert not cb.enabled({}) and not cb.enabled({"OSHINEI_CLOUD_BACKUP": "0"})
    assert cb.enabled({"OSHINEI_CLOUD_BACKUP": "1"}) and cb.enabled({"OSHINEI_CLOUD_BACKUP": "true"})


def test_start_is_inert_without_the_flag(monkeypatch):
    monkeypatch.delenv("OSHINEI_CLOUD_BACKUP", raising=False)
    before = cb.get().thread
    assert cb.start() is None and cb.get().thread is before
    import main
    main._cloud_backup_startup()                                               # the app's startup hook: nothing starts
    assert cb.get().thread is before


# ── a Backup wired to scratch parts ──────────────────────────────────────────
class FakeProc:
    def __init__(self, lines, rc):
        self.stdout = iter(lines)
        self.pid = 999_999
        self._rc = rc

    def wait(self, timeout=None):
        return self._rc

    def kill(self):
        pass


class Rig:
    def __init__(self, tmp_path, *, env=None, lines=(), rc=0, clock=None):
        self.tmp = tmp_path
        self.db = make_db(tmp_path / "oshinei.db")
        self.calls = []
        self.lines, self.rc = list(lines), rc
        self.env = {**CREDS, **(env or {})}
        self.now = [clock or at(7, 35)]
        self.backup = cb.Backup(db=self._db, db_path=self.db, popen=self._popen, clock=lambda: self.now[0], env=self.env,
                                log_dir=tmp_path / "logs", usage=lambda p: Usage(10**11, 0, 10**10),
                                sleep=lambda s: None, client_factory=lambda env: self.s3)
        self.s3 = FakeS3([key_for("10-21")])

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
        return cb.read_status(self._db)


def result_line(**kw):
    return cb.RESULT_LINE + json.dumps({"result": "ok", "verify": "PASS", "size_mb": 250.5, "sha256": "ab" * 32,
                                         "key": key_for("10-22"), "kept": 8, **kw})


def test_a_good_run_is_recorded_with_the_numbers_the_page_shows(tmp_path):
    rig = Rig(tmp_path, lines=["[OK] backup written", result_line()])
    st = rig.backup.run("schedule", slot=at(7, 30))
    assert st["result"] == "ok" and st["verify"] == "PASS" and st["size_mb"] == 250.5 and st["kept"] == 8
    assert st["key"] == key_for("10-22") and st["last_ok"] == st["last_end"] and st["streak"] == 0 and st["running"] is False
    assert st["log_tail"] == ["[OK] backup written"]                           # the result line is data, not log
    assert rig.status() == st
    cmd, kw = rig.calls[0]
    assert cmd[-2:] == [str(HERE / "cloud_backup.py"), "child"] and kw["env"]["OSHINEI_DB"] == str(rig.db)


def test_the_child_command_is_niced_and_does_not_see_the_other_secrets():
    assert cb.child_command(python="/usr/bin/python", nice=True) == \
        ["nice", "-n", "10", "/usr/bin/python", "-u", str(HERE / "cloud_backup.py"), "child"]
    assert cb.child_command(python="py", nice=False)[0] == "py"
    env = cb.child_env("/data/oshinei.db", {**CREDS, "OSHINEI_SEED_TOKEN": "t", "OSHINEI_VAPID_PRIVATE": "v",
                                            "OSHINEI_AUTH_SECRET": "s"})
    assert env["OSHINEI_DB"] == "/data/oshinei.db" and env["AWS_SECRET_ACCESS_KEY"] == CREDS["AWS_SECRET_ACCESS_KEY"]
    assert not {"OSHINEI_SEED_TOKEN", "OSHINEI_VAPID_PRIVATE", "OSHINEI_AUTH_SECRET"} & set(env)


def test_missing_storage_login_is_creds_missing_and_starts_no_child(tmp_path):
    rig = Rig(tmp_path, env={"AWS_SECRET_ACCESS_KEY": "", "BUCKET_NAME": " "})
    st = rig.backup.run("schedule", slot=at(7, 30))
    assert st["result"] == "creds_missing" and rig.calls == []
    assert "AWS_SECRET_ACCESS_KEY" in st["log_tail"][0] and "BUCKET_NAME" in st["log_tail"][0]
    assert st["verify"] is None and st["streak"] == 1
    assert rig.backup.run("schedule", slot=at(7, 30))["streak"] == 2
    res, _ = run_perform(tmp_path, FakeS3(), env={"BUCKET_NAME": ""}, db=rig.db)    # the child refuses too
    assert res["result"] == "creds_missing"


def test_a_failed_child_without_a_result_is_failed_and_the_keys_never_reach_the_page(tmp_path):
    rig = Rig(tmp_path, rc=1, lines=[f"boto error talking to {CREDS['AWS_ENDPOINT_URL_S3']} as {CREDS['AWS_ACCESS_KEY_ID']}"])
    st = rig.backup.run("schedule", slot=at(7, 30))
    assert st["result"] == "failed" and st["exit_code"] == 1
    assert st["log_tail"] == ["boto error talking to *** as ***"]
    assert rig.backup.run("manual")["streak"] == 2


def test_every_result_code_the_child_reports_is_kept_and_an_unknown_one_is_failed(tmp_path):
    def run_with(name, line, rc=1):
        (tmp_path / name).mkdir()
        return Rig(tmp_path / name, lines=[line], rc=rc).backup.run("manual")

    for res in ("verify_failed", "no_space", "locked"):
        st = run_with(res, cb.RESULT_LINE + json.dumps({"result": res, "verify": "FAIL" if res == "verify_failed" else None}))
        assert st["result"] == res
    assert run_with("odd", cb.RESULT_LINE + json.dumps({"result": "wat"}))["result"] == "failed"
    assert run_with("liar", result_line())["result"] == "failed"                # "ok" with a non-zero exit is not ok


def test_a_run_already_going_is_not_started_twice(tmp_path):
    rig = Rig(tmp_path)
    assert rig.backup._run_lock.acquire(blocking=False)
    assert rig.backup.run("manual") is None and rig.backup.run_in_background() is False
    rig.backup._run_lock.release()


# ── catch-up and the loop ────────────────────────────────────────────────────
def _with_status(rig, **kw):
    cb.write_status(kw, rig._db)


def test_catch_up_runs_when_the_last_success_is_older_than_the_last_slot(tmp_path):
    rig = Rig(tmp_path)
    assert rig.backup.catch_up_due(at(9, 0)) == at(7, 30)                      # never ran: catch up (the owner just turned it on)
    _with_status(rig, last_ok=at(7, 40, day=21).isoformat(), last_start=at(7, 30, day=21).isoformat())
    assert rig.backup.catch_up_due(at(9, 0)) == at(7, 30)                      # yesterday's is the last success: today's was missed
    _with_status(rig, last_ok=at(7, 40).isoformat())
    assert rig.backup.catch_up_due(at(9, 0)) is None                           # up to date
    _with_status(rig, last_ok=at(7, 40, day=21).isoformat(), last_start=at(8, 50).isoformat())
    assert rig.backup.catch_up_due(at(9, 0)) is None                           # one started 10 minutes ago: a crash loop must not repeat it
    _with_status(rig, last_ok=at(7, 40, day=21).isoformat())
    assert rig.backup.catch_up_due(at(6, 0)) is None                           # before today's slot, yesterday's success is current


def test_the_loop_catches_up_once_then_runs_each_slot_once(tmp_path):
    rig = Rig(tmp_path, clock=at(9, 0))
    runs = []
    rig.backup.run = lambda trigger, slot=None: (runs.append((trigger, slot)) or {})
    stop = []

    def sleeper(s):
        stop.append(s)
        if len(stop) >= 3:
            raise KeyboardInterrupt
    rig.backup._sleep = sleeper
    with pytest.raises(KeyboardInterrupt):
        rig.backup.loop(startup_delay=0, tick_s=1)
    assert runs == [("catchup", at(7, 30))]


def test_tick_starts_one_run_per_slot(tmp_path):
    rig = Rig(tmp_path)
    runs = []
    rig.backup.run = lambda trigger, slot=None: (runs.append(slot) or {})
    rig.backup.handled_slot = at(7, 30, day=21)
    assert rig.backup.tick(at(7, 29)) is None and runs == []
    rig.backup.tick(at(7, 30))
    assert rig.backup.tick(at(7, 31)) is None and rig.backup.tick(at(23, 0)) is None
    rig.backup.tick(at(7, 30, day=23))
    assert runs == [at(7, 30), at(7, 30, day=23)]


def test_a_running_flag_left_by_a_dead_process_becomes_a_failed_run(tmp_path):
    rig = Rig(tmp_path)
    _with_status(rig, running=True, result="ok", last_start=at(7, 30).isoformat(), log_tail=["[OK] copy"])
    rig.backup.reconcile_stale_running()
    st = rig.status()
    assert st["running"] is False and st["result"] == "failed" and st["log_tail"][-1].startswith("interrupted")


def test_the_page_view_has_the_next_run_the_flag_and_the_copies_newest_first(tmp_path):
    rig = Rig(tmp_path, clock=at(7, 35), env={"OSHINEI_CLOUD_BACKUP": "1"})
    rig.s3 = FakeS3([key_for("10-20"), key_for("10-22"), key_for("10-21"), "db/oshinei_2026-10-21_0730.manifest.json", "db/readme.txt"])
    v = rig.backup.view()
    assert v["enabled"] is True and v["creds_present"] is True and v["time"] == "07:30" and v["bucket"] == "oshinei-backups"
    assert v["next_run"] == at(7, 30, day=23).isoformat() and v["keep"] == {"daily": 7, "monthly": 6}
    assert [o["date"] for o in v["objects"]] == ["2026-10-22", "2026-10-21", "2026-10-20"]      # paged listing, newest first
    assert v["objects"][0]["key"] == key_for("10-22") and "size" in v["objects"][0] and v["objects_error"] is None
    rig.backup._client_factory = lambda env: (_ for _ in ()).throw(RuntimeError("no route"))
    rig.backup._objs_cache = (0.0, None, None)
    v = rig.backup.view()
    assert v["objects"] == [] and "no route" in v["objects_error"]
    (tmp_path / "n").mkdir()
    nocreds = Rig(tmp_path / "n", env={"BUCKET_NAME": ""})
    assert nocreds.backup.view()["objects"] == [] and nocreds.backup.view()["enabled"] is False


# ── the guards in front of the copy ──────────────────────────────────────────
def test_not_enough_free_space_stops_before_anything_is_copied(tmp_path):
    s3 = FakeS3()
    db = make_db(tmp_path / "oshinei.db")
    need = int(db.stat().st_size * 1.5)
    res, lines = run_perform(tmp_path, s3, free=need - 1, db=db)
    assert res["result"] == "no_space" and s3.calls == []
    assert not (tmp_path / "backups" / "tmp").exists() or not list((tmp_path / "backups" / "tmp").iterdir())
    assert "1.5 x" in lines[-1]
    ok, free, n = cb.space_check(db, usage=lambda p: Usage(10**12, 0, need))
    assert ok and n == need
    ok, _, _ = cb.space_check(db, usage=lambda p: Usage(10**12, 0, need - 1))
    assert not ok


def test_leftovers_of_a_killed_run_count_as_free_and_are_cleared(tmp_path):
    db = make_db(tmp_path / "oshinei.db")
    tmp = cb.tmp_dir_for(db)
    tmp.mkdir(parents=True)
    (tmp / "oshinei_old.db").write_bytes(b"x" * 5000)
    need = int(db.stat().st_size * 1.5)
    ok, free, _ = cb.space_check(db, usage=lambda p: Usage(10**12, 0, need - 100))
    assert not ok or free >= need
    s3 = FakeS3()
    res, _ = run_perform(tmp_path, s3, free=need + 10, db=db)
    assert res["result"] == "ok" and not tmp.exists()


def test_a_running_cloud_sync_is_waited_for_then_the_backup_proceeds(tmp_path):
    p = _holder(tmp_path / "x.lock", 1)                  # another process holds the sync lock for a second
    try:
        res, _ = run_perform(tmp_path, FakeS3(), lock_wait_s=30)
    finally:
        p.kill()
        p.wait()
    assert res["result"] == "ok"


def test_a_sync_that_outlasts_the_wait_skips_the_run_as_locked(tmp_path):
    p = _holder(tmp_path / "x.lock", 20)
    s3 = FakeS3()
    try:
        res, lines = run_perform(tmp_path, s3, lock_wait_s=1)
    finally:
        p.kill()
        p.wait()
    assert res["result"] == "locked" and s3.calls == [] and "holder" in lines[-1]


# ── retention: 7 daily + 6 monthly ───────────────────────────────────────────
def gz(day, hm="0730", month="10"):
    return f"db/oshinei_2026-{month}-{day}_{hm}.db.gz"


def man(k):
    return cb.manifest_key(k)


def test_retention_keeps_seven_days_and_the_first_of_the_last_six_months():
    names = [f"2026-{m:02d}-{d:02d}" for m, d in [(3, 5), (3, 20), (4, 1), (4, 28), (5, 2), (5, 30), (6, 11), (7, 3), (7, 31),
                                                  (8, 15), (8, 16), (9, 1), (9, 30), (10, 1), (10, 14), (10, 15), (10, 16), (10, 17),
                                                  (10, 18), (10, 19), (10, 20), (10, 21), (10, 22)]]
    keys = [f"db/oshinei_{n}_0730.db.gz" for n in names]
    gone = cb.select_retention(keys, date(2026, 10, 22))
    kept = sorted(set(keys) - set(gone))
    # daily: the 7 days 16..22 Oct; monthly: the first of each of the last 6 months with a backup (May..Oct)
    assert kept == sorted([f"db/oshinei_2026-{x}_0730.db.gz" for x in
                           ["05-02", "06-11", "07-03", "08-15", "09-01", "10-01", "10-16", "10-17", "10-18", "10-19", "10-20", "10-21", "10-22"]])
    assert set(gone) == {f"db/oshinei_2026-{x}_0730.db.gz" for x in
                         ["03-05", "03-20", "04-01", "04-28", "05-30", "07-31", "08-16", "09-30", "10-14", "10-15"]}


def test_retention_with_a_month_boundary_and_a_missing_first_day():
    keys = [gz("31", month="08"), gz("02", month="09"), gz("30", month="09"), gz("01"), gz("05"), gz("22")]
    gone = cb.select_retention(keys, date(2026, 10, 22), keep_daily=7, keep_monthly=2)
    # months kept: Sep (first present = the 2nd) and Oct (the 1st); Aug falls off; 09-30 is neither recent nor a first
    assert set(gone) == {gz("31", month="08"), gz("30", month="09"), gz("05")}


def test_retention_takes_the_manifest_with_the_backup_and_removes_orphans_but_not_strangers():
    keys = [gz("01", month="08"), man(gz("01", month="08")), gz("05"), man(gz("05")), gz("22"), man(gz("22")),
            "db/oshinei_2026-07-01_0730.manifest.json", "db/notes.txt", "other/oshinei_2020-01-01_0730.db.gz"]
    gone = set(cb.select_retention(keys, date(2026, 10, 22), keep_daily=7, keep_monthly=1))
    # 10-05 is the first backup of October (the one month kept) although 17 days old; August goes with its manifest;
    # the manifest of July has no backup beside it; the stranger names are not ours to touch
    assert gone == {gz("01", month="08"), man(gz("01", month="08")), "db/oshinei_2026-07-01_0730.manifest.json"}
    assert "db/notes.txt" not in gone and "other/oshinei_2020-01-01_0730.db.gz" not in gone


def test_two_backups_on_one_day_are_both_daily_and_the_earlier_is_the_months_first():
    keys = [gz("01", "0730"), gz("01", "1500"), gz("22")]
    assert cb.select_retention(keys, date(2026, 10, 22), 7, 6) == [gz("01", "1500")]
    assert cb.select_retention(keys, date(2026, 10, 3), 7, 6) == []


def test_same_rule_as_the_pc_backup(tmp_path):
    """db_backup.prune is what 'Backup DB.bat' runs; the bucket must keep exactly what it keeps."""
    days = [date(2026, 3, 5), date(2026, 4, 1), date(2026, 4, 28), date(2026, 5, 2), date(2026, 6, 11), date(2026, 8, 15), date(2026, 9, 1),
            date(2026, 9, 30), date(2026, 10, 1), date(2026, 10, 14), date(2026, 10, 16), date(2026, 10, 22)]
    for d in days:
        (tmp_path / f"oshinei_{d}_0730.db.gz").write_bytes(b"x")
    removed = {p.name for p in db_backup.prune(tmp_path, 7, 6, today=date(2026, 10, 22))}
    mine = {k[len("db/"):] for k in cb.select_retention([f"db/oshinei_{d}_0730.db.gz" for d in days], date(2026, 10, 22))}
    assert mine == removed and removed


# ── the whole run against the stub bucket ────────────────────────────────────
def test_a_full_run_uploads_verifies_prunes_and_leaves_no_temp_files(tmp_path):
    firsts = [gz("01", month=m) for m in ("03", "04", "05", "06", "07", "08")]
    old = []
    for k in firsts + [gz("05", month="09"), gz("01"), gz("15"), gz("21")]:
        old += [k, man(k)]
    s3 = FakeS3(old)
    res, lines = run_perform(tmp_path, s3)
    new = "db/oshinei_2026-10-22_0730.db.gz"
    assert res["result"] == "ok" and res["verify"] == "PASS" and res["key"] == new and res["size_mb"] is not None
    assert len(res["sha256"]) == 64
    data, meta = s3.objects[new]
    assert hashlib.sha256(data).hexdigest() == meta["sha256"] == res["sha256"]            # sha256 metadata is the stored object's
    restored = tmp_path / "restored.db"
    restored.write_bytes(gzip.decompress(data))
    assert db_backup._counts(restored)["sales_transaction"] == 7                           # a real, restorable copy
    mj = json.loads(s3.objects[man(new)][0])
    assert mj["verify"] == "PASS" and mj["counts"]["sales_transaction"] == 7 and mj["gz_sha256"] == res["sha256"] and mj["key"] == new
    # eight months now have a backup: March and April fall off the last six; 10-15 is 7 days old and not the month's
    # first (10-01 is); 10-21 is recent; 10-22 is the new one
    assert set(s3.deleted) == {k for d in (gz("01", month="03"), gz("01", month="04"), gz("15")) for k in (d, man(d))}
    assert gz("21") in s3.objects and gz("05", month="09") in s3.objects and gz("01") in s3.objects and new in s3.objects
    assert res["kept"] == len(cb.backup_objects(cb.list_objects(s3, "oshinei-backups"))) == 8
    assert not (tmp_path / "backups" / "tmp").exists()
    assert any("HEAD check ok" in l for l in lines)


def test_the_new_object_is_never_a_retention_candidate_even_on_a_odd_listing(tmp_path):
    s3 = FakeS3()
    new = "db/oshinei_2026-10-22_0730.db.gz"
    s3.objects[new] = (b"n", {})
    lines = []
    s3.list_objects_v2 = lambda **kw: {"Contents": []}                          # a listing that lacks the new object is not trusted
    s3.objects[gz("01", month="01")] = (b"old", {})
    cb.apply_retention(s3, "b", new, date(2026, 10, 22), lines.append)
    assert s3.deleted == [] and "skipped" in lines[-1]


def test_a_failed_upload_deletes_nothing(tmp_path):
    old = [gz("01", month="03"), gz("15"), gz("21")]
    s3 = FakeS3(old, fail_upload=True)
    res, lines = run_perform(tmp_path, s3)
    assert res["result"] == "failed" and "could not reach" in res["error"]
    assert s3.deleted == [] and set(s3.objects) == set(old)                    # the older copies are all still there
    assert not (tmp_path / "backups" / "tmp").exists()


def test_a_silently_short_upload_is_caught_by_the_head_check_removed_and_deletes_nothing(tmp_path):
    old = [gz("01", month="03"), gz("15")]
    s3 = FakeS3(old, corrupt=True)
    res, _ = run_perform(tmp_path, s3)
    new = "db/oshinei_2026-10-22_0730.db.gz"
    assert res["result"] == "failed" and "upload check failed" in res["error"]
    assert new not in s3.objects and set(s3.objects) == set(old)
    assert s3.deleted == [new]                                                 # only the bad object was removed


def test_a_retention_error_does_not_undo_a_good_backup(tmp_path):
    s3 = FakeS3([gz("15")])

    def boom(**kw):
        raise RuntimeError("list failed")
    orig = s3.list_objects_v2
    calls = {"n": 0}

    def flaky(**kw):
        calls["n"] += 1
        return boom() if calls["n"] == 1 else orig(**kw)
    s3.list_objects_v2 = flaky
    res, lines = run_perform(tmp_path, s3)
    assert res["result"] == "ok" and res["kept"] is None and any("retention error" in l for l in lines)


# ── the restore test ─────────────────────────────────────────────────────────
def _copy(tmp_path):
    db = make_db(tmp_path / "oshinei.db")
    tmp = tmp_path / "w"
    man_ = cb.make_copy(db, tmp, "2026-10-22_0730", lambda s: None)
    return tmp, man_


def test_the_restore_test_passes_a_good_copy_and_fails_each_kind_of_damage(tmp_path):
    tmp, m = _copy(tmp_path)
    gzp = Path(m["gz_path"])
    ok, why = cb.verify_copy(gzp, m, tmp, lambda s: None)
    assert ok, why
    assert not list(tmp.glob("restore_*"))                                      # the unzipped copy is gone
    bad = dict(m, sha256="0" * 64)
    assert cb.verify_copy(gzp, bad, tmp, lambda s: None) == (False, "the unzipped copy does not match the manifest sha256")
    ok, why = cb.verify_copy(gzp, dict(m, counts={**m["counts"], "sales_transaction": 99}), tmp, lambda s: None)
    assert not ok and "row counts" in why


def test_an_empty_sales_table_fails_the_restore_test(tmp_path):
    db = make_db(tmp_path / "oshinei.db", sales=0)
    m = cb.make_copy(db, tmp_path / "w", "2026-10-22_0730", lambda s: None)
    ok, why = cb.verify_copy(Path(m["gz_path"]), m, tmp_path / "w", lambda s: None)
    assert not ok and "sales_transaction is empty" in why


def test_a_restore_test_that_fails_is_recorded_alerted_and_nothing_is_uploaded(tmp_path, monkeypatch):
    s3 = FakeS3([gz("15")])
    monkeypatch.setattr(cb, "verify_copy", lambda *a, **k: (False, "quick_check: *** in database disk image"))
    res, lines = run_perform(tmp_path, s3)
    assert res["result"] == "verify_failed" and res["verify"] == "FAIL" and s3.calls == [] and s3.deleted == []
    assert any("RESTORE TEST FAIL" in l for l in lines)
    # through the runner: the page's status shows FAIL, and the push alert fires once per day
    (tmp_path / "r").mkdir()
    rig = Rig(tmp_path / "r", lines=[cb.RESULT_LINE + json.dumps(res)], rc=1)
    st = rig.backup.run("schedule", slot=at(7, 30))
    assert st["result"] == "verify_failed" and st["verify"] == "FAIL"
    from routers import push_api as P
    got = P.cloud_backup_payloads(st, datetime(2026, 10, 22, 8, 0))
    assert len(got) == 1 and got[0][0] == "stale:backup:verify_failed:2026-10-22" and got[0][1]["tag"] == "oshinei-stale"
    assert P.BACKUP_LABEL["verify_failed"] in got[0][1]["body"]


def test_the_copy_is_made_with_the_online_backup_api_on_a_live_wal_database(tmp_path):
    db = make_db(tmp_path / "oshinei.db")
    live = sqlite3.connect(str(db))
    live.execute("PRAGMA journal_mode=WAL")
    live.execute("INSERT INTO sales_transaction(v) VALUES('wal-only row')")        # lives in the -wal file until a checkpoint
    live.commit()
    try:
        m = cb.make_copy(db, tmp_path / "w", "2026-10-22_0730", lambda s: None)
        assert m["counts"]["sales_transaction"] == 8                             # the WAL row is in the copy
        assert cb.verify_copy(Path(m["gz_path"]), m, tmp_path / "w", lambda s: None)[0]
    finally:
        live.close()


# ── push alerts ──────────────────────────────────────────────────────────────
def _status(result, end=datetime(2026, 10, 22, 7, 50), running=False):
    return {"result": result, "running": running, "last_end": end.replace(tzinfo=BKK).isoformat()}


def test_failures_are_pushed_once_a_day_and_good_or_old_ones_are_not():
    from routers import push_api as P
    now = datetime(2026, 10, 22, 9, 0)
    for res in ("failed", "no_space", "creds_missing", "verify_failed"):
        got = P.cloud_backup_payloads(_status(res), now)
        assert len(got) == 1 and got[0][0] == f"stale:backup:{res}:2026-10-22", res
    for res in ("ok", "locked", None):
        assert P.cloud_backup_payloads(_status(res), now) == []
    assert P.cloud_backup_payloads(_status("failed", running=True), now) == []
    assert P.cloud_backup_payloads(_status("failed", end=datetime(2026, 10, 20, 7, 50)), now) == []     # older than a day
    assert P.cloud_backup_payloads(None, now) == [] and P.cloud_backup_payloads({}, now) == []


def test_the_alert_loop_reads_the_backup_status_only_while_the_feature_is_on(client, monkeypatch):
    from routers import push_api as P
    import main
    seen = []
    monkeypatch.setattr(P, "cloud_backup_payloads", lambda b, now: (seen.append(b) or []))
    with main.get_db() as con:
        con.execute("INSERT OR REPLACE INTO app_settings(key, value) VALUES(?, ?)", (cb.STATUS_KEY, json.dumps({"result": "failed"})))
        con.commit()
    try:
        monkeypatch.delenv("OSHINEI_CLOUD_BACKUP", raising=False)
        P.maybe_send_alerts(now=datetime(2026, 10, 22, 12, 0), force=True, scorecard={}, cashdata=[])
        monkeypatch.setenv("OSHINEI_CLOUD_BACKUP", "1")
        P.maybe_send_alerts(now=datetime(2026, 10, 22, 12, 0), force=True, scorecard={}, cashdata=[])
    finally:
        with main.get_db() as con:
            con.execute("DELETE FROM app_settings WHERE key=?", (cb.STATUS_KEY,))
            con.commit()
    assert seen == [None, {"result": "failed"}]


# ── the status survives a data swap ──────────────────────────────────────────
def test_the_status_lives_in_a_preserved_table_and_survives_the_swap(tmp_path):
    import core
    assert "app_settings" in core._PRESERVED_EXACT
    live, inc = tmp_path / "live.db", tmp_path / "inc.db"
    c = sqlite3.connect(str(live))
    c.execute("CREATE TABLE app_settings(key TEXT PRIMARY KEY, value TEXT NOT NULL DEFAULT '')")
    c.execute("INSERT INTO app_settings VALUES(?, ?)", (cb.STATUS_KEY, json.dumps({"result": "ok", "verify": "PASS"})))
    c.commit(); c.close()
    c = sqlite3.connect(str(inc))
    c.execute("CREATE TABLE app_settings(key TEXT PRIMARY KEY, value TEXT NOT NULL DEFAULT '')")
    c.execute("INSERT INTO app_settings VALUES(?, ?)", (cb.STATUS_KEY, json.dumps({"result": "failed"})))
    c.execute("CREATE TABLE sales_transaction(id INTEGER PRIMARY KEY)")
    c.commit(); c.close()
    safe, reason = core._carry_forward_and_verify(str(live), str(inc))
    assert safe, reason
    c = sqlite3.connect(str(inc))
    got = json.loads(c.execute("SELECT value FROM app_settings WHERE key=?", (cb.STATUS_KEY,)).fetchone()[0])
    c.close()
    assert got == {"result": "ok", "verify": "PASS"}                            # the LIVE copy wins


def test_settings_endpoint_does_not_hand_the_status_to_every_role(client):
    import main
    with main.get_db() as con:
        con.execute("INSERT OR REPLACE INTO app_settings(key, value) VALUES(?, ?)", (cb.STATUS_KEY, "{}"))
        con.commit()
    try:
        assert cb.STATUS_KEY not in client.get("/api/settings").json()
    finally:
        with main.get_db() as con:
            con.execute("DELETE FROM app_settings WHERE key=?", (cb.STATUS_KEY,))
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
    for k in cb.CRED_KEYS:                                                      # the status call must not reach for a real bucket
        monkeypatch.delenv(k, raising=False)
    yield


def test_status_endpoint_is_managements_alone(auth_on):
    from fastapi.testclient import TestClient
    import main
    assert TestClient(main.app).get("/api/admin/cloud-backup/status").status_code == 401
    for who in ("salessupport1", "branch1", "analyst1", "operation1"):
        assert _login(who).get("/api/admin/cloud-backup/status").status_code == 403, who
    r = _login("management1").get("/api/admin/cloud-backup/status")
    assert r.status_code == 200
    j = r.json()
    assert j["enabled"] is False and j["time"] == "07:30" and "next_run" in j and "status" in j and j["objects"] == []


def test_run_endpoint_needs_management_and_the_write_token(auth_on, monkeypatch):
    monkeypatch.setenv("OSHINEI_SEED_TOKEN", TOKEN)
    monkeypatch.setenv("OSHINEI_CLOUD_BACKUP", "1")
    started = []
    monkeypatch.setattr(cb.get(), "run_in_background", lambda: (started.append(1) or True))
    for who in ("salessupport1", "branch1", "analyst1"):
        assert _login(who).post("/api/admin/cloud-backup/run", headers={"X-Upload-Token": TOKEN}).status_code == 403, who
    boss = _login("management1")
    assert boss.post("/api/admin/cloud-backup/run").status_code == 403                       # no token
    assert boss.post("/api/admin/cloud-backup/run", headers={"X-Upload-Token": "wrong"}).status_code == 403
    assert started == []
    r = boss.post("/api/admin/cloud-backup/run", headers={"X-Upload-Token": TOKEN})
    assert r.status_code == 200 and r.json()["started"] is True and started == [1]


def test_run_endpoint_refuses_when_off_or_already_running(auth_on, monkeypatch):
    monkeypatch.setenv("OSHINEI_SEED_TOKEN", TOKEN)
    boss = _login("management1")
    hdr = {"X-Upload-Token": TOKEN}
    monkeypatch.delenv("OSHINEI_CLOUD_BACKUP", raising=False)
    assert boss.post("/api/admin/cloud-backup/run", headers=hdr).status_code == 409           # inert while off
    monkeypatch.setenv("OSHINEI_CLOUD_BACKUP", "1")
    monkeypatch.setattr(cb.get(), "run_in_background", lambda: False)
    assert boss.post("/api/admin/cloud-backup/run", headers=hdr).status_code == 409           # already running


def test_the_endpoints_are_open_to_nobody_but_system_admin_in_the_role_table():
    import auth
    for method in ("GET", "POST"):
        for path in ("/api/admin/cloud-backup/status", "/api/admin/cloud-backup/run"):
            allowed = [r for r in auth.ROLES if auth.path_allowed(method, path, auth.modules_for_role(r))]
            assert allowed == ["management"], (method, path, allowed)


# ── the owner's restore helper ───────────────────────────────────────────────
def test_download_fetches_checks_the_stored_sha256_and_refuses_to_overwrite(tmp_path, monkeypatch, capsys):
    for k, v in CREDS.items():
        monkeypatch.setenv(k, v)
    s3 = FakeS3()
    res, _ = run_perform(tmp_path, s3)
    key = res["key"]
    monkeypatch.setattr(cb, "make_client", lambda env=None: s3)
    out = tmp_path / "dl" / "restored.db.gz"
    ns = type("A", (), {"key": key, "out": str(out), "force": False})
    assert cb._cmd_download(ns) == 0 and out.read_bytes() == s3.objects[key][0]
    assert "db_backup.py verify" in capsys.readouterr().out
    assert db_backup.cmd_verify(type("V", (), {"backup": str(out)})) == 0                  # the documented next step passes
    assert cb._cmd_download(ns) == 1                                                       # exists: needs --force
    s3.objects[key] = (s3.objects[key][0][:-3], s3.objects[key][1])                        # damaged in storage
    ns2 = type("A", (), {"key": key, "out": str(tmp_path / "dl" / "again.db.gz"), "force": False})
    assert cb._cmd_download(ns2) == 1 and not (tmp_path / "dl" / "again.db.gz").exists()
    assert not list((tmp_path / "dl").glob("*.part"))


def test_the_child_prints_one_result_line_the_runner_can_parse(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cb, "perform", lambda **kw: {"result": "no_space", "verify": None})
    assert cb._cmd_child(None) == 1
    last = capsys.readouterr().out.strip().splitlines()[-1]
    assert last.startswith(cb.RESULT_LINE) and json.loads(last[len(cb.RESULT_LINE):])["result"] == "no_space"
