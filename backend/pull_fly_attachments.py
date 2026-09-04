#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Pull /data/attachments from the Fly volume into the backup folder (AUD-2).

The recon app stores uploaded evidence (slips, receipts) as files under
/data/attachments on the Fly volume, NOT in oshinei.db — so the nightly DB
backup alone does not cover them. This tars the folder on the machine, ships it
through `flyctl ssh console` as base64 (the console stream is text-safe, a raw
tarball is not) and writes attachments_<stamp>.tgz next to the DB backups.
Empty folder → nothing written (no 4 KB tarballs piling up).

    python backend/pull_fly_attachments.py --out "D:/OneDrive/Oshinei Backups" --stamp 2026-09-05_0730
"""
import argparse
import base64
import io
import re
import subprocess
import sys
import tarfile
from pathlib import Path

HERE = Path(__file__).parent.resolve()
sys.path.insert(0, str(HERE))
from refresh_fly_db import find_flyctl, fly_env  # noqa: E402

_B64 = re.compile(r"^[A-Za-z0-9+/=]{16,}$")


def _console(fly, app, cmd, env):
    r = subprocess.run([fly, "ssh", "console", "-a", app, "-C", cmd],
                       capture_output=True, text=True, env=env)
    return r.returncode, (r.stdout or ""), (r.stderr or "")


def count_files(fly, app, env) -> int:
    """How many files the folder holds; -1 when the machine could not be asked."""
    rc, out, err = _console(fly, app, "sh -c 'find /data/attachments -type f 2>/dev/null | wc -l'", env)
    nums = [ln.strip() for ln in out.splitlines() if ln.strip().isdigit()]
    if rc != 0 or not nums:
        print(f"     could not count /data/attachments (rc={rc}): {(err or out).strip()[:200]}")
        return -1
    return int(nums[-1])


def pull(app, out_dir: Path, stamp: str, fly=None, env=None):
    """Returns the tarball path, or None when there was nothing to pull / it failed."""
    fly = fly or find_flyctl()
    if not fly:
        print("     flyctl not found — attachments not pulled")
        return None
    env = env or fly_env()
    n = count_files(fly, app, env)
    if n <= 0:
        print("     /data/attachments is empty — nothing to pull" if n == 0 else "     attachments skipped")
        return None
    rc, out, err = _console(fly, app, "sh -c 'tar czf - -C /data attachments | base64 -w0'", env)
    blobs = [ln.strip() for ln in out.splitlines() if _B64.match(ln.strip())]
    if rc != 0 or not blobs:
        print(f"     attachments transfer failed (rc={rc}): {(err or out).strip()[:200]}")
        return None
    data = base64.b64decode(max(blobs, key=len))
    try:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tf:
            members = [m for m in tf.getmembers() if m.isfile()]
    except (tarfile.TarError, EOFError) as e:
        print(f"     attachments tarball unreadable: {e}")
        return None
    if len(members) != n:
        print(f"     WARNING: tarball holds {len(members)} files, the machine reported {n}")
    out_dir.mkdir(parents=True, exist_ok=True)
    dest = out_dir / f"attachments_{stamp}.tgz"
    dest.write_bytes(data)
    print(f"[OK] attachments: {len(members)} file(s), {round(len(data) / 1e6, 1)} MB -> {dest.name}")
    return dest


def main() -> int:
    ap = argparse.ArgumentParser(description="Pull /data/attachments from Fly into the backup folder")
    ap.add_argument("--app", default="oshinei-dashboard")
    ap.add_argument("--out", required=True)
    ap.add_argument("--stamp", required=True)
    args = ap.parse_args()
    pull(args.app, Path(args.out), args.stamp)
    return 0          # never fails the backup run: the DB copy is the critical part


if __name__ == "__main__":
    sys.exit(main())
