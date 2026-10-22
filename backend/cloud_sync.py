# -*- coding: utf-8 -*-
"""Cloud sync: the Fly machine pulls the Easy POS MSSQL itself, on a schedule (owner 2026-10-21).

Until now the pull ran only on the office PC (Task Scheduler: "Oshinei Auto Sync" 06:00 = full rebuild
-> push to Fly; "Oshinei Sync Live (Fly)" 12:00 + 16:00 = --window-days 45 --full-every-days 7 -> push).
With this module the web process starts one daemon thread that, at the same three times (Asia/Bangkok),
runs backend/sync_mssql.py as a CHILD PROCESS and lets the existing hot-swap apply what it built.

OFF unless OSHINEI_CLOUD_SYNC=1 - deploying the code changes nothing until the owner flips it. Settings:

    OSHINEI_CLOUD_SYNC            1 = the scheduler runs (default: off)
    OSHINEI_CLOUD_SYNC_TIMES      "06:00,12:00,16:00" (Bangkok). The FIRST slot of the day is the full rebuild,
                                  the others re-pull 45 days and force a full rebuild every 7 days
    OSHINEI_CLOUD_SYNC_TIMEOUT_MIN  kill the child after this long (default 150)
    OSH_SVR / OSH_PORT / OSH_USER / OSH_PWD / OSH_DB   the MSSQL login (Fly secrets; never in the repo)

How a run goes (Cloud.run):
    creds present?  -> else status creds_missing
    free space on the volume >= 2.5 x oshinei.db?  -> else no_space (the volume needs extending)
    the sync lock (sync_lock.py) free?  -> else locked
    child: nice -n 10 python -u sync_mssql.py [--window-days 45 --full-every-days 7]
           with OSHINEI_DB=/data/oshinei.db, so the build and .incoming land on the volume. The child builds
           under a PRIVATE name (OSHINEI_SYNC_STAGE_PRIVATE) and publishes over .incoming only when the build
           is finished and reconciled, because the web app's poller swaps whatever sits in .incoming within
           60 s. Exit 0 = ok, 3 = the reconcile gate refused it (live DB untouched), anything else = failed.
    exit 0 -> the poller (main._autoswap_poller) applies .incoming; when OSHINEI_NO_AUTOSWAP=1 turned the
           poller off, the same _apply_incoming_live() path is called directly. There is no second swap.

The outcome is kept in app_settings['cloud_sync_status'] (a PRESERVED table: core._PRESERVED_EXACT, so a swap
carries it over) and the child's output in <data dir>/logs/cloud_sync.log (14 days). Failures reach the
managers' phones through the existing "sync stuck" Web Push alert (routers/push_api.py).
"""
from __future__ import annotations

import collections
import json
import logging
import logging.handlers
import os
import shutil
import subprocess
import sys
import threading
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Optional

HERE = Path(__file__).resolve().parent
BKK = timezone(timedelta(hours=7))                  # Thailand has no DST: a fixed offset needs no tzdata in the image

STATUS_KEY = "cloud_sync_status"
DEFAULT_TIMES = "06:00,12:00,16:00"
WINDOW_DAYS, FULL_EVERY_DAYS = 45, 7                # the same arguments "Sync Live Data.bat" passes
SPACE_FACTOR = 2.5                                  # free space needed, as a multiple of oshinei.db
CRED_KEYS = ("OSH_SVR", "OSH_PORT", "OSH_USER", "OSH_PWD", "OSH_DB")
TAIL_LINES = 40
LOG_KEEP_DAYS = 14
TICK_S = 30                                         # how often the loop looks at the clock
STARTUP_DELAY_S = 120                               # let the app finish booting before a catch-up run
MIN_CATCHUP_GAP_S = 30 * 60                         # a crash loop must not start a pull per boot
DEFAULT_TIMEOUT_MIN = 150                           # a full rebuild takes ~45 min on the PC
RESULTS = ("ok", "failed", "reconcile_failed", "locked", "no_space", "creds_missing")
# the two secrets and the signing key the child has no use for - it never sees them
_CHILD_DROP_ENV = ("OSHINEI_SEED_TOKEN", "OSHINEI_VAPID_PRIVATE", "OSHINEI_AUTH_SECRET")


# ── settings ────────────────────────────────────────────────────────────────
def enabled(env=None) -> bool:
    env = os.environ if env is None else env
    return (env.get("OSHINEI_CLOUD_SYNC") or "").strip().lower() in ("1", "true", "yes", "on")


def parse_times(raw: Optional[str]) -> list:
    """'06:00,12:00,16:00' -> [(6, 0), (12, 0), (16, 0)], sorted, de-duplicated. Bad entries are dropped;
    nothing usable falls back to the default (a typo must not silently switch the schedule off)."""
    out = set()
    for part in (raw or "").replace(";", ",").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            h, m = part.split(":")
            h, m = int(h), int(m)
        except ValueError:
            continue
        if 0 <= h <= 23 and 0 <= m <= 59:
            out.add((h, m))
    return sorted(out) if out else parse_times(DEFAULT_TIMES)


def configured_times(env=None) -> list:
    env = os.environ if env is None else env
    return parse_times(env.get("OSHINEI_CLOUD_SYNC_TIMES") or DEFAULT_TIMES)


def now_bkk() -> datetime:
    return datetime.now(BKK)


def _at(day: date, hm: tuple) -> datetime:
    return datetime(day.year, day.month, day.day, hm[0], hm[1], tzinfo=BKK)


def previous_slot(now: datetime, times: list) -> datetime:
    """The latest scheduled time that is <= now (yesterday's last slot before the first of the day)."""
    now = now.astimezone(BKK)
    cands = [_at(now.date() + timedelta(days=d), hm) for d in (-1, 0) for hm in times]
    return max(s for s in cands if s <= now)


def next_slot(now: datetime, times: list) -> datetime:
    now = now.astimezone(BKK)
    cands = [_at(now.date() + timedelta(days=d), hm) for d in (0, 1) for hm in times]
    return min(s for s in cands if s > now)


def is_full_slot(slot: datetime, times: list) -> bool:
    """The first slot of the day is the full rebuild - the PC's 06:00 'Auto Sync Local'."""
    s = slot.astimezone(BKK)
    return (s.hour, s.minute) == times[0]


def child_args(full: bool) -> list:
    """Arguments of sync_mssql.py: a full rebuild takes none (--since defaults to 2025-01-01, as the PC's
    06:00 job runs it); the other slots re-pull the last 45 days (snapped back to the 1st of the month) and
    still force a full rebuild when the last one is a week old."""
    return [] if full else ["--window-days", str(WINDOW_DAYS), "--full-every-days", str(FULL_EVERY_DAYS)]


def child_command(full: bool, python: Optional[str] = None, nice: Optional[bool] = None) -> list:
    py = python or sys.executable
    cmd = [py, "-u", str(HERE / "sync_mssql.py"), *child_args(full)]
    if nice is None:
        nice = shutil.which("nice") is not None
    return (["nice", "-n", "10"] + cmd) if nice else cmd


def child_env(db_path, base: Optional[dict] = None) -> dict:
    env = dict(os.environ if base is None else base)
    for k in _CHILD_DROP_ENV:
        env.pop(k, None)
    env.update(
        OSHINEI_DB=str(db_path),                # .incoming lands beside the live DB, on the volume
        OSHINEI_SYNC_STAGE_PRIVATE="1",         # build privately, publish when reconciled (poller safety)
        OSHINEI_SYNC_LOCK_HELD="1",             # this process holds the sync lock for the child
        OSHINEI_SYNC_ON_LIVE="1",               # the base DB IS the live DB: no HTTP pulls from the site
        PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
    return env


def missing_creds(env=None) -> list:
    env = os.environ if env is None else env
    return [k for k in CRED_KEYS if not (env.get(k) or "").strip()]


def result_for_exit(rc: Optional[int], timed_out: bool = False) -> str:
    if timed_out:
        return "failed"
    if rc == 0:
        return "ok"
    if rc == 3:
        return "reconcile_failed"        # sync_mssql._sync_reconcile: the staged DB failed the gate
    return "failed"


def space_check(db_path, factor: float = SPACE_FACTOR, usage=shutil.disk_usage) -> tuple:
    """(ok, free_bytes, need_bytes). The build is a copy of the DB, a rollback/WAL file of about the same
    size and VACUUM's working space, so the volume needs a few times the file. A leftover private build of a
    killed run is deleted by the next run and counts as free."""
    p = Path(db_path)
    size = p.stat().st_size if p.exists() else 0
    need = int(size * factor)
    leftover = 0
    for suffix in (".incoming.new", ".incoming.new-wal", ".incoming.new-shm"):
        q = Path(str(p) + suffix)
        try:
            leftover += q.stat().st_size
        except OSError:
            pass
    free = usage(str(p.parent)).free + leftover
    return free >= need, free, need


def _redactor(env=None):
    """Replace the connection values that may show up in an error line (the server's address, the login)
    before a line reaches the status JSON or the log."""
    env = os.environ if env is None else env
    secrets = sorted({(env.get(k) or "").strip() for k in ("OSH_SVR", "OSH_USER", "OSH_PWD", "OSH_DB", "OSH_PORT")
                      if len((env.get(k) or "").strip()) >= 3}, key=len, reverse=True)

    def clean(line: str) -> str:
        for s in secrets:
            if s in line:
                line = line.replace(s, "***")
        return line
    return clean


# ── status (app_settings) ───────────────────────────────────────────────────
def _default_db():
    import core
    return core.get_db()


def status_from_con(con) -> dict:
    """The stored status through an open connection ({} when none yet / unreadable)."""
    try:
        row = con.execute("SELECT value FROM app_settings WHERE key = ?", (STATUS_KEY,)).fetchone()
        return json.loads(row[0]) if row and row[0] else {}
    except Exception:  # noqa: BLE001 - a missing table or a bad value is "no status yet"
        return {}


def read_status(db=None) -> dict:
    try:
        with (db or _default_db)() as con:
            return status_from_con(con)
    except Exception:  # noqa: BLE001
        return {}


def write_status(rec: dict, db=None) -> None:
    with (db or _default_db)() as con:
        con.execute("CREATE TABLE IF NOT EXISTS app_settings(key TEXT PRIMARY KEY, value TEXT NOT NULL DEFAULT '')")
        con.execute("INSERT OR REPLACE INTO app_settings(key, value) VALUES(?, ?)",
                    (STATUS_KEY, json.dumps(rec, ensure_ascii=False)))
        con.commit()


def _iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.astimezone(BKK).isoformat(timespec="seconds") if dt else None


def _parse_iso(s) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(s) if s else None
    except (TypeError, ValueError):
        return None


# ── the log (14 days) ───────────────────────────────────────────────────────
_log_lock = threading.Lock()
_loggers: dict = {}


def get_logger(log_dir, name: str = "cloud_sync") -> logging.Logger:
    """A midnight-rotating file logger `<name>.log` in `log_dir` (kept 14 days). One per directory and name; a
    directory that cannot be created gives a logger that discards - the run never fails for want of a log.
    (cloud_backup.py reuses it with name="cloud_backup".)"""
    key = f"{log_dir}|{name}"
    with _log_lock:
        lg = _loggers.get(key)
        if lg is not None:
            return lg
        lg = logging.getLogger(f"oshinei.{name}.{key}")
        lg.setLevel(logging.INFO)
        lg.propagate = False
        try:
            Path(log_dir).mkdir(parents=True, exist_ok=True)
            h = logging.handlers.TimedRotatingFileHandler(
                str(Path(log_dir) / f"{name}.log"), when="midnight", backupCount=LOG_KEEP_DAYS,
                encoding="utf-8", delay=True)
            h.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
            lg.addHandler(h)
        except OSError:
            lg.addHandler(logging.NullHandler())
        _loggers[key] = lg
        return lg


# ── the runner ──────────────────────────────────────────────────────────────
def _peak_rss_mb(pid: int) -> Optional[float]:
    """The child's high-water RSS (Linux /proc VmHWM), None elsewhere."""
    try:
        for line in Path(f"/proc/{pid}/status").read_text().splitlines():
            if line.startswith("VmHWM"):
                return int(line.split()[1]) / 1024.0
    except (OSError, ValueError, IndexError):
        pass
    return None


def _fingerprint(path) -> tuple:
    """Identity of the live DB file: an os.replace() (the hot-swap) gives it a new inode. (A checkpoint changes its
    mtime and size without being a swap, so those count only where the filesystem reports no inode.)"""
    try:
        st = os.stat(str(path))
        return (st.st_ino,) if st.st_ino else (st.st_mtime_ns, st.st_size)
    except OSError:
        return (None,)


def _kill_tree(proc) -> None:
    try:
        if os.name != "nt":
            import signal
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)       # the child and build_menu_fact beneath it
        else:
            proc.kill()
    except (OSError, ProcessLookupError):
        try:
            proc.kill()
        except OSError:
            pass


class Cloud:
    """One per process. Everything the outside world touches is injectable, so the tests drive it with a
    fake clock, a fake child and a scratch database."""

    def __init__(self, *, db=None, db_path=None, popen=None, clock=None, env=None, lock_path=None,
                 apply_fn: Optional[Callable[[], bool]] = None, log_dir=None, usage=shutil.disk_usage,
                 swap_wait_s: Optional[float] = None, sleep=time.sleep):
        self._db = db
        self._db_path = db_path
        self._popen = popen or subprocess.Popen
        self._clock = clock or now_bkk
        self._env = env if env is not None else os.environ
        self._lock_path = lock_path
        self.apply_fn = apply_fn
        self._log_dir = log_dir
        self._usage = usage
        self._swap_wait_s = swap_wait_s
        self._sleep = sleep
        self._run_lock = threading.Lock()
        self.running = False
        self._prev: dict = {}
        self.handled_slot: Optional[datetime] = None
        self.thread: Optional[threading.Thread] = None

    # -- paths ------------------------------------------------------------
    @property
    def db_path(self) -> Path:
        if self._db_path:
            return Path(self._db_path)
        import core
        return Path(core.DB_PATH)

    @property
    def log_dir(self) -> Path:
        return Path(self._log_dir) if self._log_dir else self.db_path.parent / "logs"

    def times(self) -> list:
        return configured_times(self._env)

    # -- status -----------------------------------------------------------
    def status(self) -> dict:
        return read_status(self._db)

    def save(self, rec: dict) -> None:
        try:
            write_status(rec, self._db)
        except Exception as e:  # noqa: BLE001 - the pull must not die because the status row could not be written
            print(f"[cloud-sync] could not save the status: {e}")

    def view(self) -> dict:
        """What GET /api/admin/cloud-sync/status returns."""
        now = self._clock()
        times = self.times()
        nxt = next_slot(now, times)
        rec = dict(self.status())
        ok, free, need = space_check(self.db_path, usage=self._usage) if self.db_path.exists() else (True, None, None)
        return {
            "enabled": enabled(self._env),
            "running": self.running or bool(rec.get("running")),
            "times": [f"{h:02d}:{m:02d}" for h, m in times],
            "next_run": _iso(nxt), "next_kind": "full" if is_full_slot(nxt, times) else "incremental",
            "creds_present": not missing_creds(self._env),
            "free_gb": None if free is None else round(free / 1e9, 2),
            "need_gb": None if need is None else round(need / 1e9, 2),
            "status": rec,
        }

    # -- one run ----------------------------------------------------------
    def run(self, trigger: str = "schedule", full: Optional[bool] = None, slot: Optional[datetime] = None) -> Optional[dict]:
        """Run one pull and return the saved status, or None when a run is already going."""
        if not self._run_lock.acquire(blocking=False):
            return None
        self.running = True
        try:
            return self._run(trigger, full, slot)
        finally:
            self.running = False
            self._run_lock.release()

    def _finish(self, rec: dict, result: str, started: datetime, t0: float, **extra) -> dict:
        prev = self._prev
        end = self._clock()
        rec.update(extra)
        rec.update(running=False, result=result, last_end=_iso(end), duration_s=round(time.monotonic() - t0, 1))
        if result == "ok":
            rec["last_ok"] = _iso(end)
            rec["streak"] = 0
        else:
            rec.setdefault("last_ok", prev.get("last_ok"))
            rec["streak"] = (prev.get("streak", 0) + 1) if prev.get("result") == result else 1
        self.save(rec)
        print(f"[cloud-sync] {result} ({rec.get('kind')}, {rec.get('trigger')}) in {rec['duration_s']}s")
        return rec

    def _run(self, trigger: str, full: Optional[bool], slot: Optional[datetime]) -> dict:
        times = self.times()
        started = self._clock()
        t0 = time.monotonic()
        if full is None:
            full = bool(slot and is_full_slot(slot, times))
        self._prev = prev = self.status()
        rec = {"last_start": _iso(started), "last_end": None, "last_ok": prev.get("last_ok"),
               "running": True, "result": prev.get("result"), "exit_code": None, "duration_s": None,
               "tx_count": prev.get("tx_count"), "kind": "full" if full else "incremental",
               "trigger": trigger, "slot": _iso(slot), "swapped": False, "peak_rss_mb": None,
               "streak": prev.get("streak", 0), "log_tail": []}
        log = get_logger(self.log_dir)
        log.info("--- cloud sync start: %s, %s, slot %s", trigger, rec["kind"], rec["slot"])

        lack = missing_creds(self._env)
        if lack:
            rec["log_tail"] = ["MSSQL login missing: " + ", ".join(lack)]
            log.info(rec["log_tail"][0])
            return self._finish(rec, "creds_missing", started, t0)

        ok, free, need = space_check(self.db_path, usage=self._usage)
        if not ok:
            rec["log_tail"] = [f"free space {free / 1e9:.2f} GB, need {need / 1e9:.2f} GB (2.5 x oshinei.db)"]
            log.info(rec["log_tail"][0])
            return self._finish(rec, "no_space", started, t0)

        from sync_lock import sync_lock, SyncLockTimeout
        try:
            with sync_lock("cloud-sync", wait_s=0, lock_path=self._lock_path):
                return self._run_child(rec, full, started, t0, log)
        except SyncLockTimeout as e:
            rec["log_tail"] = [str(e)[:300]]
            log.info("skipped: locked - %s", e)
            return self._finish(rec, "locked", started, t0)

    def _run_child(self, rec: dict, full: bool, started: datetime, t0: float, log: logging.Logger) -> dict:
        db = self.db_path
        tail: collections.deque = collections.deque(maxlen=TAIL_LINES)
        clean = _redactor(self._env)
        rec["tx_count_before"] = self._tx_count()
        timeout_s = float(self._env.get("OSHINEI_CLOUD_SYNC_TIMEOUT_MIN") or DEFAULT_TIMEOUT_MIN) * 60
        kwargs = {"stdout": subprocess.PIPE, "stderr": subprocess.STDOUT, "cwd": str(HERE),
                  "env": child_env(db, dict(self._env)), "text": True, "encoding": "utf-8", "errors": "replace",
                  "bufsize": 1}
        if os.name != "nt":
            kwargs["start_new_session"] = True        # its own process group, so a timeout kills build_menu_fact too
        cmd = child_command(full)
        rec["command"] = " ".join(["sync_mssql.py", *child_args(full)])
        try:
            proc = self._popen(cmd, **kwargs)
        except OSError as e:
            rec["log_tail"] = [f"could not start the child: {e}"]
            log.info(rec["log_tail"][0])
            return self._finish(rec, "failed", started, t0, exit_code=None)

        timed_out = {"v": False}

        def _watchdog():
            timed_out["v"] = True
            _kill_tree(proc)
        timer = threading.Timer(timeout_s, _watchdog)
        timer.daemon = True
        timer.start()
        peak = {"v": None}
        stop = threading.Event()

        def _sample():
            while not stop.wait(5):
                v = _peak_rss_mb(proc.pid)
                if v and (peak["v"] is None or v > peak["v"]):
                    peak["v"] = v
        sampler = threading.Thread(target=_sample, daemon=True, name="cloud-sync-rss")
        sampler.start()
        try:
            for line in proc.stdout:
                line = clean(line.rstrip("\r\n"))
                if not line:
                    continue
                log.info(line)
                tail.append(line[:300])
            rc = proc.wait()
        finally:
            timer.cancel()
            stop.set()
        v = _peak_rss_mb(proc.pid)             # a gone process has no /proc entry: the sampler's value stands
        if v and (peak["v"] is None or v > peak["v"]):
            peak["v"] = v
        rec["peak_rss_mb"] = round(peak["v"], 0) if peak["v"] else None
        result = result_for_exit(rc, timed_out["v"])
        if timed_out["v"]:
            tail.append(f"killed after {timeout_s / 60:.0f} min (OSHINEI_CLOUD_SYNC_TIMEOUT_MIN)")
        swapped = False
        if result == "ok":
            swapped, note = self._apply(db, log)
            if note:
                tail.append(note)
            if not swapped:
                result = "failed"
        rec["log_tail"] = list(tail)
        rec["tx_count"] = self._tx_count() if swapped else rec.get("tx_count")
        return self._finish(rec, result, started, t0, exit_code=rc, swapped=swapped)

    def _apply(self, db: Path, log) -> tuple:
        """Let the existing hot-swap take the finished .incoming. Returns (swapped, note)."""
        inc = Path(str(db) + ".incoming")
        if not inc.exists():
            return False, "exit 0 but no .incoming was published"
        if self._env.get("OSHINEI_NO_AUTOSWAP") == "1":
            # the poller is off on this machine: call the same swap the poller calls, once
            if self.apply_fn is None:
                return False, "OSHINEI_NO_AUTOSWAP=1 and no swap function wired"
            ok = bool(self.apply_fn())
            return ok, ("" if ok else "the swap was refused (see the [hot-swap] lines in the app log)")
        poll = float(self._env.get("OSHINEI_AUTOSWAP_SECS") or 60)
        wait = self._swap_wait_s if self._swap_wait_s is not None else max(180.0, poll * 3 + 60)
        before = _fingerprint(db)
        deadline = time.monotonic() + wait
        while inc.exists() and time.monotonic() < deadline:
            self._sleep(2)
        if inc.exists():
            return False, "the staged DB was not swapped in time (see the [hot-swap] lines in the app log)"
        if _fingerprint(db) == before:
            # the poller deletes a staged file that fails its quick_check - gone is not the same as swapped
            return False, "the staged DB was discarded without being swapped in (see the [hot-swap] lines in the app log)"
        log.info("swapped in by the hot-swap poller")
        return True, ""

    def _tx_count(self) -> Optional[int]:
        try:
            with (self._db or _default_db)() as con:
                return int(con.execute("SELECT COUNT(*) FROM sales_transaction").fetchone()[0])
        except Exception:  # noqa: BLE001
            return None

    # -- the schedule -----------------------------------------------------
    def reconcile_stale_running(self) -> None:
        """A 'running' flag left by a process that died with the machine is an interrupted run."""
        rec = self.status()
        if rec.get("running"):
            rec.update(running=False, result="failed", exit_code=None, last_end=_iso(self._clock()))
            rec["log_tail"] = (rec.get("log_tail") or [])[-TAIL_LINES + 1:] + ["interrupted: the machine restarted during the run"]
            rec["streak"] = (rec.get("streak", 0) + 1) if rec.get("result") == "failed" else 1
            self.save(rec)

    def catch_up_due(self, now: Optional[datetime] = None) -> Optional[datetime]:
        """The slot to catch up on at startup, or None: the previous slot when the last success is older than
        it (a restart across a slot), unless a pull started in the last half hour (a crash loop)."""
        now = now or self._clock()
        slot = previous_slot(now, self.times())
        rec = self.status()
        last_ok = _parse_iso(rec.get("last_ok"))
        if last_ok is not None and last_ok >= slot:
            return None
        last_start = _parse_iso(rec.get("last_start"))
        if last_start is not None and (now - last_start).total_seconds() < MIN_CATCHUP_GAP_S:
            return None
        return slot

    def tick(self, now: Optional[datetime] = None) -> Optional[dict]:
        """One look at the clock: start the run for a slot that has just passed (at most one per slot)."""
        now = now or self._clock()
        slot = previous_slot(now, self.times())
        if self.handled_slot is not None and slot <= self.handled_slot:
            return None
        self.handled_slot = slot
        return self.run("schedule", slot=slot)

    def loop(self, startup_delay: float = STARTUP_DELAY_S, tick_s: float = TICK_S) -> None:
        self.reconcile_stale_running()
        self._sleep(startup_delay)
        now = self._clock()
        slot = self.catch_up_due(now)
        self.handled_slot = previous_slot(now, self.times())
        if slot is not None:
            print(f"[cloud-sync] catching up on the {slot:%H:%M} slot")
            try:
                self.run("catchup", slot=slot)
            except Exception as e:  # noqa: BLE001
                print(f"[cloud-sync] catch-up error: {e}")
        while True:
            try:
                self.tick()
            except Exception as e:  # noqa: BLE001 - the loop must never die
                print(f"[cloud-sync] scheduler error: {e}")
            self._sleep(tick_s)

    def run_in_background(self, full: bool = False) -> bool:
        """Start a manual run in a thread. False when one is already going."""
        if self.running or self._run_lock.locked():
            return False
        t = threading.Thread(target=lambda: self.run("manual", full=full), name="cloud-sync-manual", daemon=True)
        t.start()
        return True


# ── the process-wide instance ───────────────────────────────────────────────
_instance: Optional[Cloud] = None
_instance_lock = threading.Lock()


def get() -> Cloud:
    global _instance
    with _instance_lock:
        if _instance is None:
            _instance = Cloud()
        return _instance


def start(apply_fn: Optional[Callable[[], bool]] = None) -> Optional[threading.Thread]:
    """Called from the app's startup. Inert (returns None) unless OSHINEI_CLOUD_SYNC=1."""
    if not enabled():
        return None
    c = get()
    c.apply_fn = apply_fn
    if c.thread is not None and c.thread.is_alive():
        return c.thread
    c.thread = threading.Thread(target=c.loop, name="cloud-sync", daemon=True)
    c.thread.start()
    print(f"[cloud-sync] scheduler started, times {', '.join(f'{h:02d}:{m:02d}' for h, m in c.times())} (Asia/Bangkok)")
    return c.thread
