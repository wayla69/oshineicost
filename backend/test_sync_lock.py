"""sync_lock: the one-at-a-time guard on oshinei.db.incoming (see sync_lock.py)."""
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

import sync_lock as sl

HERE = Path(__file__).resolve().parent


def _holder(lock_path, seconds):
    """Start a separate process that holds the lock for `seconds`."""
    code = textwrap.dedent(f"""
        import sys, time
        sys.path.insert(0, {str(HERE)!r})
        from sync_lock import sync_lock
        with sync_lock("holder", lock_path={str(lock_path)!r}):
            print("held", flush=True)
            time.sleep({seconds})
    """)
    p = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True)
    assert p.stdout.readline().strip() == "held"
    return p


def test_second_process_times_out_while_held(tmp_path):
    lock = tmp_path / "x.lock"
    p = _holder(lock, 10)
    try:
        with pytest.raises(sl.SyncLockTimeout) as e:
            with sl.sync_lock("waiter", wait_s=1, poll_s=0.2, lock_path=lock):
                pass
        assert "holder" in str(e.value)          # names who is holding it
    finally:
        p.kill(); p.wait()


def test_waiter_proceeds_once_the_holder_exits(tmp_path):
    lock = tmp_path / "x.lock"
    p = _holder(lock, 1)
    t0 = time.monotonic()
    with sl.sync_lock("waiter", wait_s=30, poll_s=0.2, lock_path=lock):
        waited = time.monotonic() - t0
    p.wait()
    assert 0.3 < waited < 25


def test_killed_holder_releases_the_lock(tmp_path):
    lock = tmp_path / "x.lock"
    p = _holder(lock, 60)
    p.kill(); p.wait()                           # a closed window / crash
    with sl.sync_lock("next", wait_s=5, poll_s=0.2, lock_path=lock):
        pass


def test_reentrant_within_one_process(tmp_path):
    lock = tmp_path / "x.lock"
    with sl.sync_lock("outer", wait_s=0, lock_path=lock):
        with sl.sync_lock("inner", wait_s=0, lock_path=lock):   # sync main() -> apply_local_swap()
            pass
        assert str(lock) in sl._held               # still held by the outer block
    assert str(lock) not in sl._held


def test_timeout_is_a_nonzero_exit():
    assert issubclass(sl.SyncLockTimeout, SystemExit)
