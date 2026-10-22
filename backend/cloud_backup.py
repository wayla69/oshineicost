# -*- coding: utf-8 -*-
"""Cloud backup: the Fly machine backs up its own database to object storage, daily (owner 2026-10-22).

Until now the backup was a task on the office PC ("Oshinei DB Backup" 07:30 -> Backup DB.bat ->
db_backup.py backup + verify -> OneDrive). The MSSQL pull moved to Fly (cloud_sync.py), so the PC copy of the
database stops being current: the backup has to run where the data is. Fly's own volume snapshots (5 days) are on
the same provider and are NOT an off-site copy, so the copy goes to S3-compatible object storage (Fly Tigris,
created by the owner with `flyctl storage create`, which sets the AWS_* / BUCKET_NAME secrets).

OFF unless OSHINEI_CLOUD_BACKUP=1 - deploying the code changes nothing until the owner flips it. Settings:

    OSHINEI_CLOUD_BACKUP           1 = the daily scheduler runs (default: off)
    OSHINEI_CLOUD_BACKUP_TIME      "07:30" (Asia/Bangkok), after the 06:00 cloud sync
    OSHINEI_CLOUD_BACKUP_TIMEOUT_MIN  kill the child after this long (default 150, lock wait included)
    AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY / AWS_ENDPOINT_URL_S3 / AWS_REGION / BUCKET_NAME   Fly secrets

One run (Backup.run -> a CHILD process, `nice -n 10 python -u cloud_backup.py child`):
    creds present?                                  -> else creds_missing
    the sync lock (sync_lock.py) - waits up to 90 min for a running cloud sync, then `locked`
    free space in <data>/backups/tmp >= 1.5 x DB    -> else no_space
    db_backup.cmd_backup: SQLite online-backup copy (never a plain file copy of a live WAL DB) + gzip + manifest
    verify: gunzip to a temp file, sha256 = manifest, PRAGMA quick_check, row counts = manifest  -> else verify_failed
    upload db/oshinei_<stamp>.db.gz + .manifest.json (boto3), HEAD the .gz: size and sha256 metadata
    retention in the bucket: 7 daily + 6 monthly (the first backup of a month) - only after a VERIFIED upload
    delete the temp files.
The outcome is kept in app_settings['cloud_backup_status'] (a PRESERVED table, so a data swap carries it over) and
the child's output in <data dir>/logs/cloud_backup.log (14 days). Failures reach the managers' phones through the
Web Push alert path of the cloud sync (routers/push_api.py).

Restore (the owner, by hand - docs/CLOUD_SYNC.md "สำรองข้อมูล"):
    python cloud_backup.py list
    python cloud_backup.py download --key db/oshinei_2026-10-22_0730.db.gz --out oshinei.db.gz
    python db_backup.py verify --backup oshinei.db.gz
"""
from __future__ import annotations

import argparse
import collections
import gzip
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable, Optional

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import cloud_sync as _cs  # noqa: E402  (the scheduler maths, the log file and the Bangkok clock are shared)

BKK = _cs.BKK
STATUS_KEY = "cloud_backup_status"
DEFAULT_TIME = "07:30"
PREFIX = "db/"
KEEP_DAILY, KEEP_MONTHLY = 7, 6                      # the same rule as "Backup DB.bat"
SPACE_FACTOR = 1.5                                   # free space needed, as a multiple of oshinei.db
LOCK_WAIT_S = 90 * 60
CRED_KEYS = ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_ENDPOINT_URL_S3", "BUCKET_NAME")
TAIL_LINES = 40
TICK_S = 30
STARTUP_DELAY_S = 180
MIN_CATCHUP_GAP_S = 30 * 60
DEFAULT_TIMEOUT_MIN = 150
RESULTS = ("ok", "failed", "verify_failed", "locked", "no_space", "creds_missing")
ALERT_RESULTS = ("failed", "verify_failed", "no_space", "creds_missing")
RESULT_LINE = "CB_RESULT "                           # the child's last line: the result as JSON
_CHILD_DROP_ENV = ("OSHINEI_SEED_TOKEN", "OSHINEI_VAPID_PRIVATE", "OSHINEI_AUTH_SECRET")
_KEY_RE = re.compile(r"^db/oshinei_(?P<date>\d{4}-\d{2}-\d{2})(?:_(?P<hm>\d{4}))?\.db\.gz$")


# ── settings ────────────────────────────────────────────────────────────────
def enabled(env=None) -> bool:
    env = os.environ if env is None else env
    return (env.get("OSHINEI_CLOUD_BACKUP") or "").strip().lower() in ("1", "true", "yes", "on")


def configured_times(env=None) -> list:
    """[(7, 30)] - one slot a day. A bad value falls back to the default (a typo must not switch it off)."""
    env = os.environ if env is None else env
    for part in re.split(r"[,;]", env.get("OSHINEI_CLOUD_BACKUP_TIME") or ""):
        m = re.fullmatch(r"\s*(\d{1,2}):(\d{2})\s*", part)
        if m and int(m.group(1)) <= 23 and int(m.group(2)) <= 59:
            return [(int(m.group(1)), int(m.group(2)))]
    return _cs.parse_times(DEFAULT_TIME)


def missing_creds(env=None) -> list:
    env = os.environ if env is None else env
    return [k for k in CRED_KEYS if not (env.get(k) or "").strip()]


def stamp_for(now: datetime) -> str:
    return now.astimezone(BKK).strftime("%Y-%m-%d_%H%M")


def child_command(python: Optional[str] = None, nice: Optional[bool] = None) -> list:
    cmd = [python or sys.executable, "-u", str(HERE / "cloud_backup.py"), "child"]
    if nice is None:
        nice = shutil.which("nice") is not None
    return (["nice", "-n", "10"] + cmd) if nice else cmd


def child_env(db_path, base: Optional[dict] = None) -> dict:
    env = dict(os.environ if base is None else base)
    for k in _CHILD_DROP_ENV:
        env.pop(k, None)
    env.update(OSHINEI_DB=str(db_path), PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
    return env


def tmp_dir_for(db_path) -> Path:
    return Path(db_path).parent / "backups" / "tmp"


def space_check(db_path, tmp_dir=None, factor: float = SPACE_FACTOR, usage=shutil.disk_usage) -> tuple:
    """(ok, free_bytes, need_bytes). The copy and its gzip, or the unzipped restore test, never exist all at once;
    1.5 x the database covers the larger of the two. Leftovers of a killed run in the tmp dir count as free."""
    p = Path(db_path)
    size = p.stat().st_size if p.exists() else 0
    need = int(size * factor)
    tmp = Path(tmp_dir) if tmp_dir else tmp_dir_for(p)
    leftover = 0
    try:
        for q in tmp.iterdir():
            if q.is_file():
                leftover += q.stat().st_size
    except OSError:
        pass
    probe = tmp if tmp.exists() else p.parent
    free = usage(str(probe)).free + leftover
    return free >= need, free, need


def _redactor(env=None):
    """Replace the storage keys and endpoint if one shows up in an error line before it reaches the page or the log."""
    env = os.environ if env is None else env
    secrets = sorted({(env.get(k) or "").strip() for k in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_ENDPOINT_URL_S3")
                      if len((env.get(k) or "").strip()) >= 6}, key=len, reverse=True)

    def clean(line: str) -> str:
        for s in secrets:
            if s in line:
                line = line.replace(s, "***")
        return line
    return clean


# ── the object store ────────────────────────────────────────────────────────
def make_client(env=None):
    """A boto3 S3 client for the Tigris bucket. boto3 is imported here so nothing else needs it."""
    env = os.environ if env is None else env
    import boto3
    from botocore.config import Config
    return boto3.client(
        "s3", endpoint_url=env["AWS_ENDPOINT_URL_S3"], aws_access_key_id=env["AWS_ACCESS_KEY_ID"],
        aws_secret_access_key=env["AWS_SECRET_ACCESS_KEY"], region_name=(env.get("AWS_REGION") or "auto"),
        config=Config(retries={"max_attempts": 5, "mode": "standard"}, connect_timeout=10, read_timeout=120))


def list_objects(s3, bucket: str, prefix: str = PREFIX) -> list:
    """[{key, size, modified}] under the prefix, every page."""
    out, token = [], None
    while True:
        kw = {"Bucket": bucket, "Prefix": prefix}
        if token:
            kw["ContinuationToken"] = token
        r = s3.list_objects_v2(**kw)
        for o in r.get("Contents") or []:
            m = o.get("LastModified")
            out.append({"key": o["Key"], "size": int(o.get("Size") or 0),
                        "modified": m.isoformat() if hasattr(m, "isoformat") else (m or None)})
        if r.get("IsTruncated") and r.get("NextContinuationToken"):
            token = r["NextContinuationToken"]
            continue
        return out


def backup_objects(objs: list) -> list:
    """The .db.gz objects with the date their name carries, newest first: [{key, size, date, stamp}]."""
    rows = []
    for o in objs:
        m = _KEY_RE.match(o["key"])
        if m:
            rows.append({"key": o["key"], "size": o["size"], "date": m.group("date"),
                         "stamp": f"{m.group('date')}_{m.group('hm') or '0000'}", "modified": o.get("modified")})
    rows.sort(key=lambda r: r["stamp"], reverse=True)
    return rows


def manifest_key(gz_key: str) -> str:
    return gz_key[:-len(".db.gz")] + ".manifest.json"


def select_retention(keys: list, today: date, keep_daily: int = KEEP_DAILY, keep_monthly: int = KEEP_MONTHLY) -> list:
    """The object keys to delete. Same rule as db_backup.prune (and "Backup DB.bat": 7 daily + 6 monthly): keep
    every backup from the last `keep_daily` days plus the FIRST backup of each of the last `keep_monthly` months
    that has one; a backup's manifest goes with it. Names that are not dated backups are never touched, and a
    manifest with no backup beside it is an orphan and goes."""
    dated = []
    manifests = []
    for k in keys:
        m = _KEY_RE.match(k)
        if m:
            dated.append((m.group("date"), f"{m.group('date')}_{m.group('hm') or '0000'}", k))
        elif k.endswith(".manifest.json") and re.match(r"^db/oshinei_\d{4}-\d{2}-\d{2}", k):
            manifests.append(k)
    dated.sort(key=lambda x: (x[1], x[2]))
    keep = set()
    for d, _, k in dated:
        if keep_daily and (today - date.fromisoformat(d)).days < keep_daily:
            keep.add(k)
    if keep_monthly:
        first = {}
        for d, _, k in dated:
            first.setdefault(d[:7], k)
        for month in sorted(first)[-keep_monthly:]:
            keep.add(first[month])
    present = set(keys)
    dated_keys = {k for _, _, k in dated}
    gz_gone = [k for _, _, k in dated if k not in keep]
    orphans = [mk for mk in manifests if mk[:-len(".manifest.json")] + ".db.gz" not in dated_keys]
    return gz_gone + [manifest_key(k) for k in gz_gone if manifest_key(k) in present] + orphans


# ── the pipeline (runs in the child; every outside thing is injectable) ─────
def _sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _parse_manifest_txt(p: Path) -> dict:
    """db_backup.cmd_backup writes a .manifest.txt: sha256 of the .db, gz sha256, the row counts."""
    out = {"counts": {}}
    in_counts = False
    for line in p.read_text(encoding="utf-8").splitlines():
        if line.startswith("row counts:"):
            in_counts = True
            continue
        if in_counts and line.startswith("  "):
            k, _, v = line.strip().partition(": ")
            out["counts"][k] = int(v) if v.lstrip("-").isdigit() else v
            continue
        in_counts = False
        k, _, v = line.partition(": ")
        if k in ("sha256", "gz_sha256", "gz_file", "size_MB", "gz_size_MB"):
            out[k] = v
    return out


def make_copy(db: Path, tmp: Path, stamp: str, log: Callable[[str], None]) -> dict:
    """The online-backup copy, gzipped, with its manifest (db_backup.cmd_backup). Returns the manifest dict + paths."""
    import contextlib
    import io
    import db_backup
    tmp.mkdir(parents=True, exist_ok=True)
    args = argparse.Namespace(db=str(db), out=str(tmp), stamp=stamp, gzip=True, keep_daily=0, keep_monthly=0, today="")
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = db_backup.cmd_backup(args)
    for line in buf.getvalue().splitlines():
        if line.startswith("[") or line.startswith("size_MB") or line.startswith("gz_"):
            log(line)
    if rc != 0:
        raise RuntimeError("backup copy failed: " + (buf.getvalue().strip().splitlines() or ["no output"])[-1])
    gz = tmp / f"oshinei_{stamp}.db.gz"
    txt = tmp / f"oshinei_{stamp}.manifest.txt"
    man = _parse_manifest_txt(txt)
    man.update(file=gz.name, stamp=stamp, gz_bytes=gz.stat().st_size, gz_path=str(gz))
    return man


def verify_copy(gz: Path, manifest: dict, tmp: Path, log: Callable[[str], None]) -> tuple:
    """The restore test on the file just written: gunzip to a temp file, sha256 = the manifest's, quick_check ok,
    row counts = the manifest's (and the sales table is not empty). Returns (ok, reason)."""
    import db_backup
    work = Path(tempfile.mkdtemp(prefix="restore_", dir=str(tmp)))
    try:
        target = work / "restored.db"
        h = hashlib.sha256()
        with gzip.open(gz, "rb") as fi, open(target, "wb") as fo:
            for chunk in iter(lambda: fi.read(1 << 20), b""):
                h.update(chunk)
                fo.write(chunk)
        if h.hexdigest() != manifest.get("sha256"):
            return False, "the unzipped copy does not match the manifest sha256"
        con = sqlite3.connect(f"file:{target}?mode=ro", uri=True)
        try:
            qc = con.execute("PRAGMA quick_check").fetchone()[0]
        finally:
            con.close()
        if qc != "ok":
            return False, f"quick_check: {qc}"
        counts = db_backup._counts(target)
        if counts != manifest.get("counts"):
            return False, f"row counts differ from the manifest: {counts} vs {manifest.get('counts')}"
        sales = counts.get("sales_transaction")
        if not (isinstance(sales, int) and sales > 0):
            return False, "sales_transaction is empty in the restored copy"
        log("restore test: quick_check ok, sha256 = manifest, rows " + ", ".join(f"{k}={v}" for k, v in counts.items()))
        return True, ""
    finally:
        shutil.rmtree(work, ignore_errors=True)


def upload_copy(s3, bucket: str, gz: Path, manifest: dict, log: Callable[[str], None]) -> str:
    """Upload the .gz and its manifest; check the .gz by HEAD (size + sha256 metadata). Returns the .gz key.
    On a mismatch the bad object is removed and RuntimeError raised."""
    key = PREFIX + gz.name
    digest = _sha256(gz)
    size = gz.stat().st_size
    s3.upload_file(str(gz), bucket, key, ExtraArgs={"Metadata": {"sha256": digest}})
    head = s3.head_object(Bucket=bucket, Key=key)
    meta = {k.lower(): v for k, v in (head.get("Metadata") or {}).items()}
    if int(head.get("ContentLength") or -1) != size or meta.get("sha256") != digest:
        try:
            s3.delete_objects(Bucket=bucket, Delete={"Objects": [{"Key": key}]})
        except Exception:  # noqa: BLE001
            pass
        raise RuntimeError(f"upload check failed: stored {head.get('ContentLength')} bytes, expected {size}")
    body = {k: v for k, v in manifest.items() if k != "gz_path"}
    body.update(gz_sha256=digest, gz_bytes=size, verify="PASS", key=key)
    s3.put_object(Bucket=bucket, Key=manifest_key(key), Body=json.dumps(body, ensure_ascii=False, indent=1).encode("utf-8"),
                  ContentType="application/json")
    log(f"uploaded {key} ({size / 1e6:.1f} MB), HEAD check ok")
    return key


def apply_retention(s3, bucket: str, new_key: str, today: date, log: Callable[[str], None]) -> int:
    """Delete what the 7-daily + 6-monthly rule drops. Called only after a verified upload; refuses to delete
    unless the new object is in the listing (a listing that lacks it is not trusted). Returns copies kept."""
    objs = list_objects(s3, bucket)
    keys = [o["key"] for o in objs]
    if new_key not in keys:
        log("retention skipped: the new backup is not in the bucket listing")
        return len(backup_objects(objs))
    gone = [k for k in select_retention(keys, today) if k != new_key and k != manifest_key(new_key)]
    for i in range(0, len(gone), 500):
        s3.delete_objects(Bucket=bucket, Delete={"Objects": [{"Key": k} for k in gone[i:i + 500]]})
    if gone:
        log(f"retention: removed {len([k for k in gone if k.endswith('.db.gz')])} older backup(s)")
    return len(backup_objects(objs)) - len([k for k in gone if k.endswith(".db.gz")])


def perform(*, env=None, db_path=None, now: Optional[datetime] = None, s3=None, lock_path=None,
            lock_wait_s: float = LOCK_WAIT_S, usage=shutil.disk_usage, log: Callable[[str], None] = print) -> dict:
    """One backup, start to finish. Returns {result, ...numbers}; never raises for an expected failure."""
    env = os.environ if env is None else env
    now = now or _cs.now_bkk()
    out = {"result": "failed", "verify": None, "size_mb": None, "sha256": None, "key": None, "kept": None}
    lack = missing_creds(env)
    if lack:
        log("storage login missing: " + ", ".join(lack))
        out["result"] = "creds_missing"
        return out
    db = Path(db_path or env.get("OSHINEI_DB") or (HERE / "oshinei.db"))
    tmp = tmp_dir_for(db)
    stamp = stamp_for(now)
    from sync_lock import sync_lock, SyncLockTimeout
    try:
        with sync_lock("cloud-backup", wait_s=lock_wait_s, poll_s=min(30, max(0.2, lock_wait_s / 10)), lock_path=lock_path):
            ok, free, need = space_check(db, tmp, usage=usage)
            if not ok:
                log(f"free space {free / 1e9:.2f} GB, need {need / 1e9:.2f} GB (1.5 x oshinei.db)")
                out["result"] = "no_space"
                return out
            if tmp.exists():                                     # leftovers of a killed run
                for q in tmp.iterdir():
                    shutil.rmtree(q, ignore_errors=True) if q.is_dir() else q.unlink(missing_ok=True)
            man = make_copy(db, tmp, stamp, log)                  # the only step that reads the live DB
    except SyncLockTimeout as e:
        log("skipped: " + str(e)[:200])
        out["result"] = "locked"
        return out
    except Exception as e:  # noqa: BLE001
        log(f"{type(e).__name__}: {e}")
        out["error"] = str(e)[:300]
        shutil.rmtree(tmp, ignore_errors=True)
        return out
    gz = Path(man["gz_path"])
    try:
        out.update(size_mb=round(gz.stat().st_size / 1e6, 1), sha256=_sha256(gz))
        ok, why = verify_copy(gz, man, tmp, log)
        out["verify"] = "PASS" if ok else "FAIL"
        if not ok:
            log("RESTORE TEST FAIL: " + why)
            out.update(result="verify_failed", error=why)
            return out
        s3 = s3 or make_client(env)
        bucket = env["BUCKET_NAME"].strip()
        out["key"] = upload_copy(s3, bucket, gz, man, log)
        try:
            out["kept"] = apply_retention(s3, bucket, out["key"], now.astimezone(BKK).date(), log)
        except Exception as e:  # noqa: BLE001 - the new copy is safe; only the tidy-up failed
            log(f"retention error (the new backup is stored): {type(e).__name__}: {e}")
        out["result"] = "ok"
        return out
    except Exception as e:  # noqa: BLE001
        log(f"{type(e).__name__}: {e}")
        out.update(result="failed", error=str(e)[:300])
        return out
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ── status (app_settings) ───────────────────────────────────────────────────
def _default_db():
    import core
    return core.get_db()


def status_from_con(con) -> dict:
    try:
        row = con.execute("SELECT value FROM app_settings WHERE key = ?", (STATUS_KEY,)).fetchone()
        return json.loads(row[0]) if row and row[0] else {}
    except Exception:  # noqa: BLE001
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


_iso, _parse_iso = _cs._iso, _cs._parse_iso


# ── the scheduler / runner (in the web process) ─────────────────────────────
class Backup:
    """One per process. Everything outside is injectable, as in cloud_sync.Cloud."""

    def __init__(self, *, db=None, db_path=None, popen=None, clock=None, env=None, log_dir=None, usage=shutil.disk_usage,
                 sleep=time.sleep, client_factory=None):
        self._db = db
        self._db_path = db_path
        self._popen = popen or subprocess.Popen
        self._clock = clock or _cs.now_bkk
        self._env = env if env is not None else os.environ
        self._log_dir = log_dir
        self._usage = usage
        self._sleep = sleep
        self._client_factory = client_factory or make_client
        self._run_lock = threading.Lock()
        self.running = False
        self._prev: dict = {}
        self.handled_slot: Optional[datetime] = None
        self.thread: Optional[threading.Thread] = None
        self._objs_cache: tuple = (0.0, None, None)

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

    def status(self) -> dict:
        return read_status(self._db)

    def save(self, rec: dict) -> None:
        try:
            write_status(rec, self._db)
        except Exception as e:  # noqa: BLE001
            print(f"[cloud-backup] could not save the status: {e}")

    def objects(self, max_age_s: float = 30.0) -> tuple:
        """([{key, size, date}] newest first, error). Needs the storage login; cached for a few seconds."""
        if missing_creds(self._env):
            return [], None
        t, objs, err = self._objs_cache
        if objs is not None and time.monotonic() - t < max_age_s:
            return objs, err
        try:
            s3 = self._client_factory(self._env)
            rows = backup_objects(list_objects(s3, self._env["BUCKET_NAME"].strip()))
            objs, err = [{"key": r["key"], "size": r["size"], "date": r["date"], "stamp": r["stamp"]} for r in rows], None
        except Exception as e:  # noqa: BLE001
            objs, err = [], f"{type(e).__name__}: {str(e)[:160]}"
        self._objs_cache = (time.monotonic(), objs, err)
        return objs, err

    def view(self) -> dict:
        now = self._clock()
        times = self.times()
        nxt = _cs.next_slot(now, times)
        ok, free, need = space_check(self.db_path, usage=self._usage) if self.db_path.exists() else (True, None, None)
        objs, err = self.objects()
        return {
            "enabled": enabled(self._env),
            "running": self.running or bool(self.status().get("running")),
            "time": f"{times[0][0]:02d}:{times[0][1]:02d}",
            "next_run": _iso(nxt),
            "creds_present": not missing_creds(self._env),
            "bucket": (self._env.get("BUCKET_NAME") or "").strip() or None,
            "free_gb": None if free is None else round(free / 1e9, 2),
            "need_gb": None if need is None else round(need / 1e9, 2),
            "keep": {"daily": KEEP_DAILY, "monthly": KEEP_MONTHLY},
            "objects": objs, "objects_error": err,
            "status": dict(self.status()),
        }

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
        if result == "ok":
            rec["last_ok"] = _iso(end)
            rec["streak"] = 0
        else:
            rec.setdefault("last_ok", prev.get("last_ok"))
            rec["streak"] = (prev.get("streak", 0) + 1) if prev.get("result") == result else 1
        self._objs_cache = (0.0, None, None)
        self.save(rec)
        print(f"[cloud-backup] {result} ({rec.get('trigger')}) in {rec['duration_s']}s")
        return rec

    def _run(self, trigger: str, slot: Optional[datetime]) -> dict:
        started = self._clock()
        t0 = time.monotonic()
        self._prev = prev = self.status()
        rec = {"last_start": _iso(started), "last_end": None, "last_ok": prev.get("last_ok"), "running": True,
               "result": prev.get("result"), "exit_code": None, "duration_s": None, "trigger": trigger,
               "slot": _iso(slot), "size_mb": prev.get("size_mb"), "sha256": prev.get("sha256"), "key": prev.get("key"),
               "verify": prev.get("verify"), "kept": prev.get("kept"), "streak": prev.get("streak", 0), "log_tail": []}
        log = _cs.get_logger(self.log_dir, "cloud_backup")
        log.info("--- cloud backup start: %s, slot %s", trigger, rec["slot"])
        lack = missing_creds(self._env)
        if lack:
            rec["log_tail"] = ["storage login missing: " + ", ".join(lack)]
            log.info(rec["log_tail"][0])
            return self._finish(rec, "creds_missing", t0, size_mb=None, sha256=None, key=None, verify=None)
        self.save(rec)                                        # visible as "running" on the page while the child works
        return self._run_child(rec, t0, log)

    def _run_child(self, rec: dict, t0: float, log) -> dict:
        db = self.db_path
        tail: collections.deque = collections.deque(maxlen=TAIL_LINES)
        clean = _redactor(self._env)
        timeout_s = float(self._env.get("OSHINEI_CLOUD_BACKUP_TIMEOUT_MIN") or DEFAULT_TIMEOUT_MIN) * 60
        kwargs = {"stdout": subprocess.PIPE, "stderr": subprocess.STDOUT, "cwd": str(HERE),
                  "env": child_env(db, dict(self._env)), "text": True, "encoding": "utf-8", "errors": "replace", "bufsize": 1}
        if os.name != "nt":
            kwargs["start_new_session"] = True
        try:
            proc = self._popen(child_command(), **kwargs)
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
            tail.append(f"killed after {timeout_s / 60:.0f} min (OSHINEI_CLOUD_BACKUP_TIMEOUT_MIN)")
        result = "failed"
        extra: dict = {}
        if child_res and not timed_out["v"]:
            result = child_res.get("result") if child_res.get("result") in RESULTS else "failed"
            if result == "ok" and rc != 0:
                result = "failed"
            for k in ("size_mb", "sha256", "key", "verify", "kept"):
                extra[k] = child_res.get(k)
        elif rc == 0 and not timed_out["v"]:
            tail.append("the backup process ended without a result")
        rec["log_tail"] = list(tail)
        return self._finish(rec, result, t0, exit_code=rc, **extra)

    # -- the schedule -----------------------------------------------------
    def reconcile_stale_running(self) -> None:
        rec = self.status()
        if rec.get("running"):
            rec.update(running=False, result="failed", exit_code=None, last_end=_iso(self._clock()))
            rec["log_tail"] = (rec.get("log_tail") or [])[-TAIL_LINES + 1:] + ["interrupted: the machine restarted during the run"]
            rec["streak"] = (rec.get("streak", 0) + 1) if rec.get("result") == "failed" else 1
            self.save(rec)

    def catch_up_due(self, now: Optional[datetime] = None) -> Optional[datetime]:
        """The slot to catch up on at startup: the previous slot when the last success is older than it, unless a
        run started in the last half hour (a crash loop)."""
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

    def loop(self, startup_delay: float = STARTUP_DELAY_S, tick_s: float = TICK_S) -> None:
        self.reconcile_stale_running()
        self._sleep(startup_delay)
        now = self._clock()
        slot = self.catch_up_due(now)
        self.handled_slot = _cs.previous_slot(now, self.times())
        if slot is not None:
            print(f"[cloud-backup] catching up on the {slot:%H:%M} slot")
            try:
                self.run("catchup", slot=slot)
            except Exception as e:  # noqa: BLE001
                print(f"[cloud-backup] catch-up error: {e}")
        while True:
            try:
                self.tick()
            except Exception as e:  # noqa: BLE001
                print(f"[cloud-backup] scheduler error: {e}")
            self._sleep(tick_s)

    def run_in_background(self) -> bool:
        if self.running or self._run_lock.locked():
            return False
        threading.Thread(target=lambda: self.run("manual"), name="cloud-backup-manual", daemon=True).start()
        return True


_instance: Optional[Backup] = None
_instance_lock = threading.Lock()


def get() -> Backup:
    global _instance
    with _instance_lock:
        if _instance is None:
            _instance = Backup()
        return _instance


def start() -> Optional[threading.Thread]:
    """Called from the app's startup. Inert (returns None) unless OSHINEI_CLOUD_BACKUP=1."""
    if not enabled():
        return None
    b = get()
    if b.thread is not None and b.thread.is_alive():
        return b.thread
    b.thread = threading.Thread(target=b.loop, name="cloud-backup", daemon=True)
    b.thread.start()
    t = b.times()[0]
    print(f"[cloud-backup] scheduler started, daily at {t[0]:02d}:{t[1]:02d} (Asia/Bangkok)")
    return b.thread


# ── command line: the child, and the owner's restore helpers ────────────────
def _cmd_child(args) -> int:
    res = perform(log=lambda s: print(s, flush=True))
    print(RESULT_LINE + json.dumps(res, ensure_ascii=False), flush=True)
    return 0 if res["result"] == "ok" else 1


def _cmd_list(args) -> int:
    lack = missing_creds()
    if lack:
        print("storage login missing: " + ", ".join(lack))
        return 1
    rows = backup_objects(list_objects(make_client(), os.environ["BUCKET_NAME"].strip()))
    for r in rows:
        print(f"{r['key']}\t{r['size'] / 1e6:.1f} MB")
    print(f"{len(rows)} backup(s)")
    return 0


def _cmd_download(args) -> int:
    lack = missing_creds()
    if lack:
        print("storage login missing: " + ", ".join(lack))
        return 1
    s3, bucket = make_client(), os.environ["BUCKET_NAME"].strip()
    out = Path(args.out)
    if out.exists() and not args.force:
        print(f"{out} exists - use --force to overwrite")
        return 1
    out.parent.mkdir(parents=True, exist_ok=True)
    part = Path(str(out) + ".part")
    s3.download_file(bucket, args.key, str(part))
    want = {k.lower(): v for k, v in (s3.head_object(Bucket=bucket, Key=args.key).get("Metadata") or {}).items()}.get("sha256")
    got = _sha256(part)
    if want and want != got:
        part.unlink(missing_ok=True)
        print(f"FAIL - sha256 {got} does not match the stored {want}")
        return 1
    os.replace(part, out)
    print(f"[OK] {args.key} -> {out} ({out.stat().st_size / 1e6:.1f} MB), sha256 {got}" + ("" if want else " (no stored sha256 to compare)"))
    print(f"next: python db_backup.py verify --backup \"{out}\"")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Oshinei cloud backup (child run, list, download)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("child").set_defaults(func=_cmd_child)
    sub.add_parser("list").set_defaults(func=_cmd_list)
    d = sub.add_parser("download")
    d.add_argument("--key", required=True)
    d.add_argument("--out", required=True)
    d.add_argument("--force", action="store_true")
    d.set_defaults(func=_cmd_download)
    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
