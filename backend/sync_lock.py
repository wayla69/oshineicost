"""One-at-a-time guard for the jobs that build, push or apply oshinei.db.incoming.

WHY: two scheduled jobs share `.incoming` — "Oshinei Auto Sync" (Auto Sync
Local.bat: sync -> push to Fly -> apply locally) and "Oshinei Sync Live (Fly)"
(Sync Live Data.bat: sync -> push). Their start times never overlap, but Task
Scheduler runs every MISSED task as soon as the PC comes back. On 2026-09-30
the office PC had been off since 09-27; at 09:36 both syncs started in the same
second, each copied oshinei.db over the same staging file, and Auto Sync read
the other's half-written copy and died on `no such table: branches`. The Fly
push still went through, but the local DB stayed three days stale.

Every step that writes, reads or swaps `.incoming` now runs inside
`sync_lock()`. A second job waits its turn (default up to 90 min, a full sync
takes ~45) instead of racing. The lock is an OS file lock, so it is released
the moment the holding process exits, including when its window is closed or
it crashes; there is no stale lock file to clean up.
"""
import os
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
LOCK_PATH = HERE / "oshinei.db.sync.lock"

DEFAULT_WAIT_S = int(os.environ.get("OSHINEI_SYNC_LOCK_WAIT", 90 * 60))
POLL_S = 30

_held = {}          # lock path -> [file handle, depth]; re-entrant within a process


def _try_lock(fh):
    """Non-blocking exclusive lock on byte 0 of `fh`. True if we got it."""
    if os.name == "nt":
        import msvcrt
        fh.seek(0)
        try:
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            return False
    import fcntl
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def _unlock(fh):
    try:
        if os.name == "nt":
            import msvcrt
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
    except OSError:
        pass
    fh.close()


def _owner_path(lock_path):
    return Path(str(lock_path) + ".owner")


def _read_owner(lock_path):
    try:
        return _owner_path(lock_path).read_text(encoding="utf-8").strip()
    except OSError:
        return "another sync job"


class SyncLockTimeout(SystemExit):
    """Raised when the lock stays held past the wait. A SystemExit so a script
    run by a scheduler .bat exits non-zero with the message, not a traceback."""


@contextmanager
def sync_lock(label, wait_s=None, poll_s=POLL_S, lock_path=None):
    """Hold the `.incoming` lock for the duration of the block.

    Re-entrant within one process (sync_mssql.main() calls apply_local_swap(),
    which locks too). Waits up to `wait_s` seconds for another process to let go,
    then raises SyncLockTimeout, a non-zero exit."""
    path = Path(lock_path) if lock_path else LOCK_PATH
    key = str(path)
    if key in _held:
        _held[key][1] += 1
        try:
            yield
        finally:
            _held[key][1] -= 1
        return

    wait_s = DEFAULT_WAIT_S if wait_s is None else wait_s
    fh = open(path, "a+b")
    deadline = time.monotonic() + wait_s
    announced = False
    while not _try_lock(fh):
        if time.monotonic() >= deadline:
            fh.close()
            raise SyncLockTimeout(
                f"*** {label}: {_read_owner(path)} still holds {path.name} after "
                f"{wait_s // 60} min - not starting. It re-runs at its next slot; "
                f"if nothing is running, re-run this job. ***")
        if not announced:
            print(f"      [sync-lock] {label}: waiting - {_read_owner(path)} is using "
                  f"oshinei.db.incoming (checks every {poll_s}s, up to {wait_s // 60} min)",
                  flush=True)
            announced = True
        time.sleep(poll_s)
    if announced:
        print(f"      [sync-lock] {label}: got the lock, continuing", flush=True)
    try:
        _owner_path(path).write_text(
            f"{label} (pid {os.getpid()}, since {datetime.now():%Y-%m-%d %H:%M})",
            encoding="utf-8")
    except OSError:
        pass
    _held[key] = [fh, 1]
    try:
        yield
    finally:
        del _held[key]
        try:
            _owner_path(path).unlink()
        except OSError:
            pass
        _unlock(fh)
