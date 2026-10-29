# -*- coding: utf-8 -*-
"""The other jobs that ran on the owner's Windows PC, now on the Fly machine (owner 2026-10-22: "move the watchdog and
the remaining jobs to the cloud"). Three jobs, each OFF unless its flag is 1 - deploying the code changes nothing:

    feedback    OSHINEI_CLOUD_FEEDBACK=1   09:00  "Oshinei Feedback Sync"  -> fetch_feedback.py run --source all --since auto
    invisible   OSHINEI_CLOUD_INVISIBLE=1  10:45  "Oshinei Push Invisible"  -> push_invisible.py --days 3 --menu
    watchdog    OSHINEI_CLOUD_WATCHDOG=1   11:15, 17:00 and 5 min after start  "Oshinei Task Watchdog" -> checks the cloud jobs

Settings (all optional): OSHINEI_CLOUD_FEEDBACK_TIME / OSHINEI_CLOUD_INVISIBLE_TIME ("09:00"), OSHINEI_CLOUD_WATCHDOG_TIMES
("11:15,17:00"), OSHINEI_CLOUD_<JOB>_TIMEOUT_MIN (kill the child after this long, lock wait included).

This follows the pattern of cloud_sync.py and cloud_backup.py and REUSES their scheduler maths, log file, process killer and
Bangkok clock (cloud_sync.previous_slot / next_slot / get_logger / _kill_tree). Those two modules are NOT touched: a
shared runner would have meant rewriting two live, tested schedulers to save ~150 lines, and the owner has not yet cut
either over. (`cloud_jobs.Job` is the third copy of the same loop on purpose; fold the three together after the cut-over.)

A feedback or invisible run is a CHILD process (`nice -n 10 python -u cloud_jobs.py child <job>`), under the sync lock
(sync_lock.py) and only while no staged `oshinei.db.incoming` is waiting for the hot-swap: both jobs touch the live
database, and a swap in the middle would send their writes to the old file. They wait up to 60 minutes, then skip as `locked`.

    feedback   Reads the review sources' logins from FEEDBACK_CONFIG_B64 (the owner's backend/.feedback_config.json, base64) and
               writes the rows straight into /data/oshinei.db through fetch_feedback's DbSink - the code that handles
               POST /api/feedback/upload - not through the API, so no write token or login is needed on the machine.
               No review source configured at all -> `no_source` (not an error, no alert); sources configured but without
               their logins -> `creds_missing`. Rotated tokens (TikTok) are kept in /data/.feedback_config.json.
    invisible  Reads the live database through a read-only connection and posts to Invisible Voice with INVISIBLE_API_KEY.
               The "last day pushed" mark lives in /data/.invisible_last_ok.txt (the image is wiped at every deploy).
    watchdog   In the web process, no child. For every ENABLED cloud job (sync, backup, feedback, invisible) it flags
               FAILED, MISSED (no start within 30 min after its slot), STUCK (running > 2 x its usual time, at least 15 min,
               or > 3 h), `creds_missing` / `no_space`; plus stale sales data (newest sold_at older than 2 days) and a stale
               backup (newest object in the bucket older than 36 h). The result goes to the admins' home banner (usage.db,
               key task_health_cloud, merged by main._task_health_view with the PC watchdog's report) and to the managers'
               phones by Web Push, once per finding per day (routers/push_api.py).

The outcome of each job is in app_settings['cloud_job_<job>_status'] (a PRESERVED table, so a data swap carries it over) and
the child's output in <data dir>/logs/cloud_<job>.log (14 days).
"""
from __future__ import annotations

import argparse
import base64
import collections
import contextlib
import hashlib
import io
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Optional

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import cloud_backup as _cb  # noqa: E402
import cloud_sync as _cs  # noqa: E402

BKK = _cs.BKK
RESULT_LINE = "CJ_RESULT "
TAIL_LINES = 40
TICK_S = 30
STARTUP_DELAY_S = 150
WATCHDOG_STARTUP_S = 300                  # "once 5 min after startup"
MIN_CATCHUP_GAP_S = 30 * 60
LOCK_WAIT_S = 60 * 60                     # feedback / invisible wait this long for a sync, then skip as `locked`
MISSED_AFTER = timedelta(minutes=30)
STUCK_MAX = timedelta(hours=3)
STUCK_FACTOR, STUCK_FLOOR_S = 2, 15 * 60
DATA_STALE_AFTER = timedelta(days=2)
BACKUP_STALE_AFTER = timedelta(hours=36)

NAMES = ("sync", "backup", "feedback", "invisible", "watchdog")
LABEL_TH = {"sync": "ดึงข้อมูลจาก POS", "backup": "สำรองข้อมูล", "feedback": "ดึงรีวิวลูกค้า",
            "invisible": "ส่งข้อมูลไป Invisible Voice", "watchdog": "ตรวจงานอัตโนมัติ"}
RESULTS = {
    "feedback": ("ok", "failed", "locked", "no_source", "creds_missing"),
    "invisible": ("ok", "failed", "locked", "creds_missing"),
    "watchdog": ("ok", "findings", "failed"),
}
ALERT_RESULTS = ("failed", "creds_missing")
# What each job's child must NOT inherit besides the app's own secrets: the other job's login.
_CHILD_DROP_ENV = ("OSHINEI_SEED_TOKEN", "OSHINEI_VAPID_PRIVATE", "OSHINEI_AUTH_SECRET")
_JOB_SECRET_ENV = {"feedback": ("FEEDBACK_CONFIG_B64",), "invisible": ("INVISIBLE_API_KEY",)}

# Every environment variable the two child jobs read (names only; docs/CLOUD_SYNC.md "งานอื่นบน cloud").
ENV_NAMES = {
    "feedback": ("OSHINEI_CLOUD_FEEDBACK", "OSHINEI_CLOUD_FEEDBACK_TIME", "FEEDBACK_CONFIG_B64"),
    "invisible": ("OSHINEI_CLOUD_INVISIBLE", "OSHINEI_CLOUD_INVISIBLE_TIME", "INVISIBLE_API_KEY", "INVISIBLE_API_URL"),
    "watchdog": ("OSHINEI_CLOUD_WATCHDOG", "OSHINEI_CLOUD_WATCHDOG_TIMES"),
}

SPECS = {
    "feedback": {"flag": "OSHINEI_CLOUD_FEEDBACK", "time_env": "OSHINEI_CLOUD_FEEDBACK_TIME", "default": "09:00",
                 "timeout_min": 90, "child": True},
    "invisible": {"flag": "OSHINEI_CLOUD_INVISIBLE", "time_env": "OSHINEI_CLOUD_INVISIBLE_TIME", "default": "10:45",
                  "timeout_min": 90, "child": True},
    "watchdog": {"flag": "OSHINEI_CLOUD_WATCHDOG", "time_env": "OSHINEI_CLOUD_WATCHDOG_TIMES", "default": "11:15,17:00",
                 "timeout_min": 10, "child": False},
}


# ── settings ────────────────────────────────────────────────────────────────
def _on(raw) -> bool:
    return (raw or "").strip().lower() in ("1", "true", "yes", "on")


def enabled_job(name: str, env=None) -> bool:
    """Is this job switched on? Works for all five (sync and backup answer through their own modules)."""
    env = os.environ if env is None else env
    if name == "sync":
        return _cs.enabled(env)
    if name == "backup":
        return _cb.enabled(env)
    return _on(env.get(SPECS[name]["flag"]))


def parse_times(raw: Optional[str], default: str) -> list:
    """'11:15,17:00' -> [(11, 15), (17, 0)], sorted. Bad entries are dropped; nothing usable falls back to `default`
    (a typo must not switch a job off)."""
    def _parse(text):
        out = set()
        for part in re.split(r"[,;]", text or ""):
            m = re.fullmatch(r"\s*(\d{1,2}):(\d{2})\s*", part)
            if m and int(m.group(1)) <= 23 and int(m.group(2)) <= 59:
                out.add((int(m.group(1)), int(m.group(2))))
        return sorted(out)
    return _parse(raw) or _parse(default)


def configured_times(name: str, env=None) -> list:
    env = os.environ if env is None else env
    if name == "sync":
        return _cs.configured_times(env)
    if name == "backup":
        return _cb.configured_times(env)
    spec = SPECS[name]
    times = parse_times(env.get(spec["time_env"]) or "", spec["default"])
    return times[:1] if name != "watchdog" else times


def fmt_times(times: list) -> list:
    return [f"{h:02d}:{m:02d}" for h, m in times]


# ── the guard in front of anything that touches the live database ───────────
def db_quiet(db_path, *, label: str, lock_path=None, wait_s: float = LOCK_WAIT_S, poll_s: float = 30.0, sleep=time.sleep):
    """Context manager: the sync lock is ours AND no staged `.incoming` is waiting for the hot-swap. Raises
    SyncLockTimeout after `wait_s`. (A cloud sync publishes .incoming and releases the lock; the swap follows within a
    minute - writing in that gap would land in the file that is about to be replaced.)"""
    from sync_lock import sync_lock, SyncLockTimeout

    @contextlib.contextmanager
    def cm():
        deadline = time.monotonic() + wait_s
        with sync_lock(label, wait_s=wait_s, poll_s=min(poll_s, max(0.2, wait_s / 10)), lock_path=lock_path):
            inc = Path(str(db_path) + ".incoming")
            while inc.exists():
                if time.monotonic() >= deadline:
                    raise SyncLockTimeout(f"*** {label}: a staged database is still waiting to be swapped in after {wait_s // 60:.0f} min ***")
                sleep(min(poll_s, 5.0, max(0.05, wait_s / 5)))
            yield
    return cm()


# ── feedback ────────────────────────────────────────────────────────────────
SOURCE_KEYS = ("google", "facebook", "instagram", "tiktok", "x", "youtube", "web")


def _has(d, *keys) -> bool:
    return isinstance(d, dict) and all(str(d.get(k) or "").strip() for k in keys)


def source_ready(name: str, conf) -> bool:
    """Does this configured review source carry the logins its fetcher needs? ('web' is public RSS: always.)"""
    if name == "google":
        return _has(conf, "client_id", "client_secret", "refresh_token")
    if name == "facebook":
        return any(_has(p, "id", "token") for p in (conf or {}).get("pages") or [])
    if name == "instagram":
        return any(_has(p, "ig_user_id", "token") for p in (conf or {}).get("accounts") or [])
    if name == "tiktok":
        return any(_has(p, "access_token") or _has(p, "refresh_token") for p in (conf or {}).get("accounts") or [])
    if name == "x":
        return _has(conf, "bearer_token")
    if name == "youtube":
        return _has(conf, "api_key")
    return bool(conf)


def feedback_config_path(data_dir) -> Path:
    return Path(data_dir) / ".feedback_config.json"


def materialize_feedback_config(env, data_dir) -> Optional[Path]:
    """The review-source logins as a file the fetcher can read and update (TikTok rotates its tokens into it).
    FEEDBACK_CONFIG_B64 (base64 of backend/.feedback_config.json) is written to <data dir>/.feedback_config.json whenever its
    hash differs from the one written last time - so changing the secret takes effect, and an unchanged secret never
    overwrites a token the fetcher has rotated. Returns the path, or None when there is no config at all."""
    path = feedback_config_path(data_dir)
    raw = (env.get("FEEDBACK_CONFIG_B64") or "").strip()
    if raw:
        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        mark = Path(str(path) + ".src")
        try:
            same = mark.read_text(encoding="utf-8").strip() == digest and path.exists()
        except OSError:
            same = False
        if not same:
            text = base64.b64decode(raw).decode("utf-8-sig")
            json.loads(text or "{}")                                  # ValueError on a damaged secret
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
            mark.write_text(digest, encoding="utf-8")
    return path if path.exists() else None


def perform_feedback(*, env=None, db_path=None, lock_path=None, lock_wait_s: float = LOCK_WAIT_S, runner=None,
                     log: Callable[[str], None] = print) -> dict:
    """One feedback fetch. Returns {result, sources, ...}; never raises for an expected failure."""
    env = os.environ if env is None else env
    db = Path(db_path or env.get("OSHINEI_DB") or (HERE / "oshinei.db"))
    out = {"result": "failed", "sources": []}
    try:
        cfg_path = materialize_feedback_config(env, db.parent)
        cfg = json.loads(cfg_path.read_text(encoding="utf-8-sig") or "{}") if cfg_path else {}
    except (ValueError, OSError) as e:
        log(f"FEEDBACK_CONFIG_B64 is not usable: {type(e).__name__}")
        out["error"] = "FEEDBACK_CONFIG_B64 is not valid base64 JSON"
        return out
    names = [k for k in SOURCE_KEYS if cfg.get(k)]
    out["sources"] = names
    if not names:
        log("no review source configured - nothing to fetch")
        out["result"] = "no_source"
        return out
    ready = [k for k in names if source_ready(k, cfg.get(k))]
    if not ready:
        log("review sources without their logins: " + ", ".join(names))
        out["result"] = "creds_missing"
        return out
    for k in names:
        if k not in ready:
            log(f"{k}: login incomplete - this source will fail")
    if runner is None:
        import fetch_feedback

        def runner(argv):
            return fetch_feedback.main(argv)
    from sync_lock import SyncLockTimeout
    try:
        with db_quiet(db, label="cloud-feedback", lock_path=lock_path, wait_s=lock_wait_s):
            rc = runner(["run", "--source", "all", "--since", "auto", "--db", str(db), "--config", str(cfg_path)])
    except SyncLockTimeout as e:
        log("skipped: " + str(e)[:200])
        out["result"] = "locked"
        return out
    except SystemExit as e:
        log(f"the fetcher stopped: {e}")
        out["error"] = str(e)[:300]
        return out
    except Exception as e:  # noqa: BLE001
        log(f"{type(e).__name__}: {e}")
        out["error"] = str(e)[:300]
        return out
    out["result"] = "ok" if rc == 0 else "failed"
    return out


# ── invisible ───────────────────────────────────────────────────────────────
def invisible_state_file(db_path) -> Path:
    return Path(db_path).parent / ".invisible_last_ok.txt"


def perform_invisible(*, env=None, db_path=None, lock_path=None, lock_wait_s: float = LOCK_WAIT_S, runner=None,
                      log: Callable[[str], None] = print) -> dict:
    env = os.environ if env is None else env
    db = Path(db_path or env.get("OSHINEI_DB") or (HERE / "oshinei.db"))
    out = {"result": "failed"}
    if not (env.get("INVISIBLE_API_KEY") or "").strip():
        log("INVISIBLE_API_KEY is not set")
        out["result"] = "creds_missing"
        return out
    if runner is None:
        import push_invisible

        def runner(argv):
            return push_invisible.main(argv)
    argv = ["--days", "3", "--menu", "--db", str(db), "--readonly", "--state-file", str(invisible_state_file(db))]
    if (env.get("INVISIBLE_API_URL") or "").strip():
        argv += ["--url", env["INVISIBLE_API_URL"].strip()]
    from sync_lock import SyncLockTimeout
    buf = io.StringIO()
    try:
        with db_quiet(db, label="cloud-invisible", lock_path=lock_path, wait_s=lock_wait_s):
            with contextlib.redirect_stdout(buf):
                rc = runner(argv)
    except SyncLockTimeout as e:
        log("skipped: " + str(e)[:200])
        out["result"] = "locked"
        return out
    except SystemExit as e:                   # push_invisible exits with a message on a bad key or an HTTP error
        log(f"push stopped: {str(e.code if e.code is not None else e)[:300]}")
        out["error"] = str(e.code if e.code is not None else e)[:300]
        return out
    except Exception as e:  # noqa: BLE001
        log(f"{type(e).__name__}: {e}")
        out["error"] = str(e)[:300]
        return out
    try:
        j = json.loads(buf.getvalue() or "{}")
        out["sales_rows"] = (j.get("sales") or {}).get("rows")
        out["since"] = (j.get("sales") or {}).get("since")
        out["menu_rows"] = (j.get("menu") or {}).get("rows")
        log(f"pushed sales since {out['since']}: {out['sales_rows']} row(s); menu: {out['menu_rows']} row(s)")
    except ValueError:
        log(buf.getvalue()[-300:])
    out["result"] = "ok" if rc == 0 else "failed"
    return out


PERFORM = {"feedback": perform_feedback, "invisible": perform_invisible}


# ── status (app_settings) ───────────────────────────────────────────────────
def status_key(name: str) -> str:
    return f"cloud_job_{name}_status"


def _default_db():
    import core
    return core.get_db()


def status_from_con(con, name: str) -> dict:
    try:
        row = con.execute("SELECT value FROM app_settings WHERE key = ?", (status_key(name),)).fetchone()
        return json.loads(row[0]) if row and row[0] else {}
    except Exception:  # noqa: BLE001
        return {}


def read_status(name: str, db=None) -> dict:
    try:
        with (db or _default_db)() as con:
            return status_from_con(con, name)
    except Exception:  # noqa: BLE001
        return {}


def write_status(name: str, rec: dict, db=None) -> None:
    with (db or _default_db)() as con:
        con.execute("CREATE TABLE IF NOT EXISTS app_settings(key TEXT PRIMARY KEY, value TEXT NOT NULL DEFAULT '')")
        con.execute("INSERT OR REPLACE INTO app_settings(key, value) VALUES(?, ?)",
                    (status_key(name), json.dumps(rec, ensure_ascii=False)))
        con.commit()


def status_of(name: str, db=None) -> dict:
    """The stored status of any of the five jobs."""
    if name == "sync":
        return _cs.read_status(db)
    if name == "backup":
        return _cb.read_status(db)
    return read_status(name, db)


_iso, _parse_iso = _cs._iso, _cs._parse_iso


def child_command(name: str, python: Optional[str] = None, nice: Optional[bool] = None) -> list:
    cmd = [python or sys.executable, "-u", str(HERE / "cloud_jobs.py"), "child", name]
    if nice is None:
        nice = shutil.which("nice") is not None
    return (["nice", "-n", "10"] + cmd) if nice else cmd


def child_env(name: str, db_path, base: Optional[dict] = None) -> dict:
    env = dict(os.environ if base is None else base)
    for k in _CHILD_DROP_ENV:
        env.pop(k, None)
    for other, keys in _JOB_SECRET_ENV.items():          # the other job's login stays out of this child
        if other != name:
            for k in keys:
                env.pop(k, None)
    env.update(OSHINEI_DB=str(db_path), PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
    return env


def _secret_values(env) -> list:
    """Strings that must never reach the page or the log: the Invisible key and every token in the feedback config."""
    out = {(env.get("INVISIBLE_API_KEY") or "").strip()}
    raw = (env.get("FEEDBACK_CONFIG_B64") or "").strip()
    if raw:
        out.add(raw)
        try:
            cfg = json.loads(base64.b64decode(raw).decode("utf-8-sig"))
        except Exception:  # noqa: BLE001
            cfg = None

        def walk(o):
            if isinstance(o, dict):
                for k, v in o.items():
                    if isinstance(v, str) and re.search(r"token|secret|key|password", k, re.I):
                        out.add(v.strip())
                    else:
                        walk(v)
            elif isinstance(o, list):
                for v in o:
                    walk(v)
        walk(cfg)
    return sorted((s for s in out if len(s) >= 8), key=len, reverse=True)


def _redactor(env):
    secrets = _secret_values(env)

    def clean(line: str) -> str:
        for s in secrets:
            if s in line:
                line = line.replace(s, "***")
        return line
    return clean


# ── the runner / scheduler (in the web process) ─────────────────────────────
class Job:
    """One per job and process. Everything outside is injectable, as in cloud_sync.Cloud."""

    def __init__(self, name: str, *, db=None, db_path=None, popen=None, clock=None, env=None, log_dir=None,
                 sleep=time.sleep, check=None, boot: Optional[datetime] = None):
        assert name in SPECS
        self.name = name
        self.spec = SPECS[name]
        self._db = db
        self._db_path = db_path
        self._popen = popen or subprocess.Popen
        self._clock = clock or _cs.now_bkk
        self._env = env if env is not None else os.environ
        self._log_dir = log_dir
        self._sleep = sleep
        self._check = check                       # watchdog: callable(job) -> findings report (tests)
        self.boot = boot or self._clock()
        self._run_lock = threading.Lock()
        self.running = False
        self.handled_slot: Optional[datetime] = None
        self.thread: Optional[threading.Thread] = None
        self._prev: dict = {}

    # -- settings ----------------------------------------------------------
    @property
    def db_path(self) -> Path:
        if self._db_path:
            return Path(self._db_path)
        import core
        return Path(core.DB_PATH)

    @property
    def log_dir(self) -> Path:
        return Path(self._log_dir) if self._log_dir else self.db_path.parent / "logs"

    def enabled(self) -> bool:
        return enabled_job(self.name, self._env)

    def times(self) -> list:
        return configured_times(self.name, self._env)

    def status(self) -> dict:
        return read_status(self.name, self._db)

    def save(self, rec: dict) -> None:
        try:
            write_status(self.name, rec, self._db)
        except Exception as e:  # noqa: BLE001
            print(f"[cloud-{self.name}] could not save the status: {e}")

    def view(self) -> dict:
        return job_row(self.name, self._clock(), self._env, self._db)

    # -- one run -----------------------------------------------------------
    def run(self, trigger: str = "schedule", slot: Optional[datetime] = None) -> Optional[dict]:
        if not self._run_lock.acquire(blocking=False):
            return None
        self.running = True
        try:
            return self._run(trigger, slot)
        finally:
            self.running = False
            self._run_lock.release()

    def _finish(self, rec: dict, result: str, t0: float, **extra) -> dict:
        prev = self._prev
        end = self._clock()
        rec.update(extra)
        rec.update(running=False, result=result, last_end=_iso(end), duration_s=round(time.monotonic() - t0, 1))
        if result in ("ok", "no_source", "findings"):
            rec["last_ok"] = _iso(end)
            rec["streak"] = 0
        else:
            rec.setdefault("last_ok", prev.get("last_ok"))
            rec["streak"] = (prev.get("streak", 0) + 1) if prev.get("result") == result else 1
        self.save(rec)
        print(f"[cloud-{self.name}] {result} ({rec.get('trigger')}) in {rec['duration_s']}s")
        return rec

    def _run(self, trigger: str, slot: Optional[datetime]) -> dict:
        started = self._clock()
        t0 = time.monotonic()
        self._prev = prev = self.status()
        rec = {"last_start": _iso(started), "last_end": None, "last_ok": prev.get("last_ok"), "running": True,
               "result": prev.get("result"), "exit_code": None, "duration_s": None, "trigger": trigger,
               "slot": _iso(slot), "streak": prev.get("streak", 0), "log_tail": [], "detail": prev.get("detail")}
        log = _cs.get_logger(self.log_dir, f"cloud_{self.name}")
        log.info("--- cloud %s start: %s, slot %s", self.name, trigger, rec["slot"])
        if not self.spec["child"]:
            return self._run_inproc(rec, t0, log)
        self.save(rec)                                        # visible as "running" on the page while the child works
        return self._run_child(rec, t0, log)

    def _run_inproc(self, rec: dict, t0: float, log) -> dict:
        try:
            report = (self._check or run_watchdog_check)(self)
        except Exception as e:  # noqa: BLE001
            rec["log_tail"] = [f"{type(e).__name__}: {e}"[:300]]
            log.info(rec["log_tail"][0])
            return self._finish(rec, "failed", t0)
        findings = report.get("findings") or []
        rec["findings"] = findings
        rec["checked_at"] = report.get("checked_at")
        rec["usual_s"] = report.get("usual_s") or {}
        rec["detail"] = {"problems": len(findings), "jobs_checked": report.get("jobs_checked")}
        rec["log_tail"] = [f"{f['name']}: {f['meaning']}" for f in findings][:TAIL_LINES]
        for line in rec["log_tail"]:
            log.info(line)
        return self._finish(rec, "findings" if findings else "ok", t0)

    def _run_child(self, rec: dict, t0: float, log) -> dict:
        db = self.db_path
        tail: collections.deque = collections.deque(maxlen=TAIL_LINES)
        clean = _redactor(self._env)
        timeout_s = float(self._env.get(f"OSHINEI_CLOUD_{self.name.upper()}_TIMEOUT_MIN") or self.spec["timeout_min"]) * 60
        kwargs = {"stdout": subprocess.PIPE, "stderr": subprocess.STDOUT, "cwd": str(HERE),
                  "env": child_env(self.name, db, dict(self._env)), "text": True, "encoding": "utf-8", "errors": "replace",
                  "bufsize": 1}
        if os.name != "nt":
            kwargs["start_new_session"] = True
        try:
            proc = self._popen(child_command(self.name), **kwargs)
        except OSError as e:
            rec["log_tail"] = [f"could not start the child: {e}"]
            log.info(rec["log_tail"][0])
            return self._finish(rec, "failed", t0)
        timed_out = {"v": False}

        def _watchdog():
            timed_out["v"] = True
            _cs._kill_tree(proc)
        timer = threading.Timer(timeout_s, _watchdog)
        timer.daemon = True
        timer.start()
        child_res: Optional[dict] = None
        try:
            for line in proc.stdout:
                line = clean(line.rstrip("\r\n"))
                if not line:
                    continue
                if line.startswith(RESULT_LINE):
                    try:
                        child_res = json.loads(line[len(RESULT_LINE):])
                    except ValueError:
                        pass
                    continue
                log.info(line)
                tail.append(line[:300])
            rc = proc.wait()
        finally:
            timer.cancel()
        if timed_out["v"]:
            tail.append(f"killed after {timeout_s / 60:.0f} min (OSHINEI_CLOUD_{self.name.upper()}_TIMEOUT_MIN)")
        result = "failed"
        if child_res and not timed_out["v"]:
            result = child_res.get("result") if child_res.get("result") in RESULTS[self.name] else "failed"
            if result in ("ok", "no_source") and rc != 0:
                result = "failed"
            rec["detail"] = {k: v for k, v in child_res.items() if k not in ("result", "error")}
        elif rc == 0 and not timed_out["v"]:
            tail.append("the process ended without a result")
        rec["log_tail"] = list(tail)
        return self._finish(rec, result, t0, exit_code=rc)

    # -- the schedule ------------------------------------------------------
    def reconcile_stale_running(self) -> None:
        rec = self.status()
        if rec.get("running"):
            rec.update(running=False, result="failed", exit_code=None, last_end=_iso(self._clock()))
            rec["log_tail"] = (rec.get("log_tail") or [])[-TAIL_LINES + 1:] + ["interrupted: the machine restarted during the run"]
            rec["streak"] = (rec.get("streak", 0) + 1) if rec.get("result") == "failed" else 1
            self.save(rec)

    def catch_up_due(self, now: Optional[datetime] = None) -> Optional[datetime]:
        """The previous slot, when the last success is older than it - unless a run started in the last half hour."""
        now = now or self._clock()
        slot = _cs.previous_slot(now, self.times())
        rec = self.status()
        last_ok = _parse_iso(rec.get("last_ok"))
        if last_ok is not None and last_ok >= slot:
            return None
        last_start = _parse_iso(rec.get("last_start"))
        if last_start is not None and (now - last_start).total_seconds() < MIN_CATCHUP_GAP_S:
            return None
        return slot

    def tick(self, now: Optional[datetime] = None) -> Optional[dict]:
        now = now or self._clock()
        slot = _cs.previous_slot(now, self.times())
        if self.handled_slot is not None and slot <= self.handled_slot:
            return None
        self.handled_slot = slot
        return self.run("schedule", slot=slot)

    def loop(self, startup_delay: Optional[float] = None, tick_s: float = TICK_S) -> None:
        self.reconcile_stale_running()
        if self.name == "watchdog":
            self._sleep(WATCHDOG_STARTUP_S if startup_delay is None else startup_delay)
            now = self._clock()
            self.handled_slot = _cs.previous_slot(now, self.times())       # the slots already past are not re-run
            try:
                self.run("startup", slot=None)
            except Exception as e:  # noqa: BLE001
                print(f"[cloud-watchdog] startup check error: {e}")
        else:
            self._sleep(STARTUP_DELAY_S if startup_delay is None else startup_delay)
            now = self._clock()
            slot = self.catch_up_due(now)
            self.handled_slot = _cs.previous_slot(now, self.times())
            if slot is not None:
                print(f"[cloud-{self.name}] catching up on the {slot:%H:%M} slot")
                try:
                    self.run("catchup", slot=slot)
                except Exception as e:  # noqa: BLE001
                    print(f"[cloud-{self.name}] catch-up error: {e}")
        while True:
            try:
                self.tick()
            except Exception as e:  # noqa: BLE001
                print(f"[cloud-{self.name}] scheduler error: {e}")
            self._sleep(tick_s)

    def run_in_background(self) -> bool:
        if self.running or self._run_lock.locked():
            return False
        threading.Thread(target=lambda: self.run("manual"), name=f"cloud-{self.name}-manual", daemon=True).start()
        return True


_instances: dict = {}
_instances_lock = threading.Lock()


def get(name: str) -> Job:
    with _instances_lock:
        if name not in _instances:
            _instances[name] = Job(name)
        return _instances[name]


def start() -> list:
    """Called from the app's startup. Starts one scheduler thread per ENABLED job; inert (returns []) when none is on."""
    threads = []
    for name in SPECS:
        if not enabled_job(name):
            continue
        j = get(name)
        if j.thread is not None and j.thread.is_alive():
            threads.append(j.thread)
            continue
        j.thread = threading.Thread(target=j.loop, name=f"cloud-{name}", daemon=True)
        j.thread.start()
        print(f"[cloud-{name}] scheduler started, {', '.join(fmt_times(j.times()))} (Asia/Bangkok)")
        threads.append(j.thread)
    return threads


# ── the overview (Settings card, endpoint) ──────────────────────────────────
def job_row(name: str, now: datetime, env=None, db=None) -> dict:
    env = os.environ if env is None else env
    st = status_of(name, db)
    times = configured_times(name, env)
    on = enabled_job(name, env)
    row = {"job": name, "label": LABEL_TH[name], "enabled": on, "times": fmt_times(times),
           "next_run": _iso(_cs.next_slot(now, times)) if on else None,
           "running": bool(st.get("running")), "result": st.get("result"), "last_start": st.get("last_start"),
           "last_end": st.get("last_end"), "last_ok": st.get("last_ok"), "duration_s": st.get("duration_s"),
           "streak": st.get("streak", 0), "runnable": name in SPECS, "detail": st.get("detail")}
    if name == "watchdog":
        row["problems"] = len(st.get("findings") or [])
        row["checked_at"] = st.get("checked_at")
    return row


def overview(*, now: Optional[datetime] = None, env=None, db=None) -> dict:
    now = now or _cs.now_bkk()
    jobs = []
    for n in NAMES:
        r = job_row(n, now, env, db)
        if n in SPECS:
            r["running"] = r["running"] or get(n).running
        jobs.append(r)
    return {"jobs": jobs, "now": _iso(now), "plan_precompute": plan_precompute_view(db)}


PLAN_LABEL_TH = "คำนวณแผนสั่งวัตถุดิบล่วงหน้า"


def plan_precompute_view(db=None) -> dict:
    """The precomputed order plans (bom_center/precompute.py): the pass running or last finished in THIS process (with the store and
    what is pending), else the last finished full pass saved in app_settings. Not a scheduled job: it runs after every data swap
    and every write that changes a plan."""
    try:
        from bom_center import precompute as _pre
        live = _pre.status()
    except Exception:  # noqa: BLE001
        live = {}
    out = {"label": PLAN_LABEL_TH}
    if live.get("last_start"):
        out.update(live)
    else:
        out.update(read_status("plan_precompute", db))
        if live:
            out.update(enabled=live.get("enabled"), pending=live.get("pending"), store=live.get("store"))
    return out


# ── the watchdog ────────────────────────────────────────────────────────────
def _usual(prev_usual: dict, name: str, st: dict) -> Optional[float]:
    """How long this job usually takes: a running average of its finished runs, carried in the watchdog's own status."""
    d = st.get("duration_s")
    old = prev_usual.get(name)
    if st.get("running") or d is None or st.get("result") not in ("ok", "no_source"):
        return old
    return round(d if old is None else 0.7 * old + 0.3 * d, 1)


TH_KIND = {
    "failed": "รอบล่าสุดไม่สำเร็จ", "creds_missing": "ยังไม่ได้ตั้งรหัสเชื่อมต่อ", "no_space": "พื้นที่เก็บข้อมูลไม่พอ",
    "missed": "ไม่ได้เริ่มรันตามรอบล่าสุด", "stuck": "รันนานผิดปกติ (ค้าง)",
    "stale_data": "ข้อมูลขายไม่เข้า", "stale_backup": "สำเนาสำรองเก่าเกิน 36 ชั่วโมง",
}
KIND_LEVEL = {"failed": "fail", "creds_missing": "fail", "no_space": "fail", "missed": "missed", "stuck": "stuck",
              "stale_data": "stale", "stale_backup": "stale"}


def _finding(job: str, kind: str, meaning: str, last_run: Optional[str], days: Optional[int] = None) -> dict:
    f = {"job": job, "name": {**LABEL_TH, "data": "ข้อมูลขาย", "plan": PLAN_LABEL_TH}.get(job, job), "kind": kind, "level": KIND_LEVEL[kind],
         "meaning": f"{TH_KIND[kind]} — {meaning}" if meaning else TH_KIND[kind], "last_run": last_run}
    if days:
        f["days"] = days
    return f


def judge_job(name: str, st: dict, *, now: datetime, times: list, boot: datetime, usual: Optional[float]) -> list:
    """The findings of one ENABLED job from its stored status. Pure."""
    out = []
    last_start = _parse_iso(st.get("last_start"))
    last_run = st.get("last_start")
    if st.get("running"):
        if last_start is not None:
            elapsed = (now - last_start).total_seconds()
            limit = STUCK_MAX.total_seconds()
            if usual:
                limit = min(limit, max(STUCK_FACTOR * usual, STUCK_FLOOR_S))
            if elapsed > limit:
                out.append(_finish_stuck(name, elapsed, last_run))
        return out
    res = st.get("result")
    if res in ("failed", "verify_failed", "reconcile_failed"):
        out.append(_finding(name, "failed", (st.get("log_tail") or [""])[-1][:160] or str(res), last_run))
    elif res in ("creds_missing", "no_space"):
        out.append(_finding(name, res, "", last_run))
    if not out:
        slot = _cs.previous_slot(now, times)
        if (slot >= boot and now - slot > MISSED_AFTER and (last_start is None or last_start < slot)):
            out.append(_finding(name, "missed", f"รอบ {slot:%H:%M}", last_run))
    return out


def _finish_stuck(name: str, elapsed_s: float, last_run) -> dict:
    return _finding(name, "stuck", f"รันมาแล้ว {elapsed_s / 60:.0f} นาที", last_run)


def data_freshness(con, now: datetime) -> Optional[dict]:
    """A finding when the newest sale is older than 2 days (None when fresh or the table is empty)."""
    try:
        row = con.execute("SELECT MAX(sold_at) FROM sales_transaction").fetchone()
    except Exception:  # noqa: BLE001
        return None
    raw = row[0] if row else None
    if not raw:
        return None
    try:
        newest = datetime.fromisoformat(str(raw).replace("T", " ")[:19]).replace(tzinfo=BKK)
    except ValueError:
        return None
    if now - newest > DATA_STALE_AFTER:
        days = (now.date() - newest.date()).days
        return _finding("data", "stale_data", f"ยอดขายล่าสุด {newest:%d/%m %H:%M}", newest.isoformat(timespec="seconds"), days)
    return None


def backup_freshness(env, now: datetime, client_factory=None) -> Optional[dict]:
    """A finding when the newest object in the bucket is older than 36 h (or the bucket cannot be read)."""
    if _cb.missing_creds(env):
        return None
    try:
        s3 = (client_factory or _cb.make_client)(env)
        objs = _cb.backup_objects(_cb.list_objects(s3, env["BUCKET_NAME"].strip()))
    except Exception as e:  # noqa: BLE001
        f = _finding("backup", "stale_backup", f"อ่านที่เก็บข้อมูลสำรองไม่ได้ ({type(e).__name__})", None)
        return f
    stamps = []
    for o in objs:
        t = _parse_iso(o.get("modified"))
        if t is not None:
            stamps.append(t if t.tzinfo else t.replace(tzinfo=_utc()))
    if not stamps:
        return _finding("backup", "stale_backup", "ยังไม่มีสำเนาในที่เก็บข้อมูลสำรอง", None) if objs == [] else None
    newest = max(stamps)
    if now - newest > BACKUP_STALE_AFTER:
        return _finding("backup", "stale_backup", f"สำเนาล่าสุด {newest.astimezone(BKK):%d/%m %H:%M}",
                        newest.astimezone(BKK).isoformat(timespec="seconds"), (now - newest).days or None)
    return None


def _utc():
    from datetime import timezone
    return timezone.utc


def usage_db_path() -> Path:
    import core
    return Path(str(core.DB_PATH)).parent / "usage.db"


HEALTH_KEY = "task_health_cloud"


def write_health(report: dict, now: datetime, usage_path=None) -> None:
    """The home-page banner's store: usage.db / usage_meta (survives a data swap), the same table the PC watchdog posts to.
    main._task_health_view reads this key beside the PC's."""
    path = usage_path or usage_db_path()
    rec = {"received_at": now.astimezone(BKK).strftime("%Y-%m-%d %H:%M:%S"), "report": report}
    con = sqlite3.connect(str(path), timeout=10)
    try:
        con.execute("CREATE TABLE IF NOT EXISTS usage_meta(key TEXT PRIMARY KEY, value TEXT)")
        con.execute("INSERT INTO usage_meta(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    (HEALTH_KEY, json.dumps(rec, ensure_ascii=False)))
        con.commit()
    finally:
        con.close()


def build_report(findings: list, rows: list, now: datetime) -> dict:
    problems = [f for f in findings]
    return {"host": "fly", "source": "cloud", "checked_at": now.astimezone(BKK).strftime("%Y-%m-%d %H:%M:%S"),
            "ok": not problems, "problems": len(problems),
            "signature": "|".join(sorted(f"{f['job']}={f['kind']}" for f in problems)),
            "tasks": [dict(f) for f in problems] + rows}


def run_watchdog_check(job: Job, *, client_factory=None, usage_path=None) -> dict:
    """The cloud watchdog's single pass: judge every enabled job, the data and the backup, write the banner."""
    now = job._clock()
    env = job._env
    prev = job.status()
    prev_usual = dict(prev.get("usual_s") or {})
    usual = dict(prev_usual)
    findings, rows, checked = [], [], 0
    for name in ("sync", "backup", "feedback", "invisible"):
        if not enabled_job(name, env):
            continue
        checked += 1
        st = status_of(name, job._db)
        u = _usual(prev_usual, name, st)
        if u is not None:
            usual[name] = u
        fs = judge_job(name, st, now=now, times=configured_times(name, env), boot=job.boot, usual=prev_usual.get(name))
        findings += fs
        if not fs:
            rows.append({"name": LABEL_TH[name], "level": "ok", "last_run": st.get("last_start"),
                         "meaning": str(st.get("result") or "ยังไม่เคยรัน")})
    try:
        with (job._db or _default_db)() as con:
            f = data_freshness(con, now)
    except Exception:  # noqa: BLE001
        f = None
    if f:
        findings.append(f)
    pre = read_status("plan_precompute", job._db)          # the precomputed order plans: a pass that failed for some branch
    if pre.get("result") == "failed":
        fl = (pre.get("failures") or [{}])[0]
        findings.append(_finding("plan", "failed", f"{len(pre.get('failures') or [])} แผน · {fl.get('error') or ''}"[:160],
                                 pre.get("last_start")))
    if enabled_job("backup", env):
        f = backup_freshness(env, now, client_factory)
        if f:
            findings.append(f)
    order = {"fail": 0, "stuck": 1, "missed": 2, "stale": 3}
    findings.sort(key=lambda x: (order.get(x["level"], 9), x["job"]))
    report = build_report(findings, rows, now)
    try:
        write_health(report, now, usage_path)
    except Exception as e:  # noqa: BLE001 - the banner is best-effort; the status below still records the findings
        print(f"[cloud-watchdog] could not write the banner: {e}")
    return {"findings": findings, "checked_at": report["checked_at"], "usual_s": usual, "jobs_checked": checked}


# ── command line: the child ─────────────────────────────────────────────────
def _cmd_child(args) -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass
    res = PERFORM[args.job](log=lambda s: print(s, flush=True))
    print(RESULT_LINE + json.dumps(res, ensure_ascii=False), flush=True)
    return 0 if res["result"] in ("ok", "no_source") else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="Oshinei cloud jobs (the child run)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("child")
    c.add_argument("job", choices=sorted(PERFORM))
    c.set_defaults(func=_cmd_child)
    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
