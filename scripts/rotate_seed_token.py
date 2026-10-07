#!/usr/bin/env python3
"""Rotate OSHINEI_SEED_TOKEN — the write token shared by the dashboard and recon.

The Fly secret is a comma list of 'label:token' (one per person / machine, see
backend/core.py _accepted_tokens); the local backup is backend/.seed_tokens.txt, one
'label:token' per line, FIRST line = the token the PC's scheduled scripts use
(refresh_fly_db, pull_fly_state, build_menu_fact, fetch_feedback, task_watchdog,
bom_center/dated, bulk_recon_upload).

Every holder named in --keep gets a NEW token; every holder left out is revoked.
Order of work, so a failure never leaves the PC without a working token:
  1. write the new backup (old file archived to backend/.seed_tokens.revoked-<date>.txt)
  2. flyctl secrets set OSHINEI_SEED_TOKEN=<new list>      (restarts the app ~20 s)
  3. probe the live gate: every new token accepted, every old token refused
     (POST /api/manual with an empty body — refused before anything is written)
  4. print the hand-out list (label → new token) for the owner to send privately

  py -3.12 scripts/rotate_seed_token.py --keep admin Patcharee          # dry run: shows the plan
  py -3.12 scripts/rotate_seed_token.py --keep admin Patcharee --apply  # does it

Never run during a sync (06:00 / 12:00 / 16:00): the push uses the token.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import os
import re
import secrets
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
BACKUP = REPO / "backend" / ".seed_tokens.txt"
APP = "oshinei-dashboard"
RECON = "https://oshinei-dashboard.fly.dev:8443"
PC_LABEL = "admin"                       # the scheduled scripts read the first line
_LABEL_RE = re.compile(r"^[A-Za-z0-9_.-]{1,40}$")


def parse_backup(text: str) -> list[tuple[str, str]]:
    """'label:token' lines → [(label, token)] in file order (comments/blank skipped)."""
    out = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        label, tok = line.split(":", 1)
        if tok.strip():
            out.append((label.strip(), tok.strip()))
    return out


def plan(current: list[tuple[str, str]], keep: list[str]) -> list[tuple[str, str]]:
    """New [(label, token)]: a fresh 256-bit token for every kept label, PC label first."""
    for k in keep:
        if not _LABEL_RE.match(k):
            raise SystemExit(f"bad label {k!r} — letters, digits, . _ - only")
    if PC_LABEL not in keep:
        raise SystemExit(f"--keep must include {PC_LABEL!r}: the PC's scheduled sync uses it")
    if len(set(k.lower() for k in keep)) != len(keep):
        raise SystemExit("duplicate label in --keep")
    order = [PC_LABEL] + [k for k in keep if k != PC_LABEL]
    return [(k, secrets.token_hex(32)) for k in order]


def in_sync_window(now: _dt.datetime) -> bool:
    """10 min before → 60 min after each scheduled sync (the push uses the token)."""
    m = now.hour * 60 + now.minute
    return any(start - 10 <= m < start + 60 for start in (6 * 60, 12 * 60, 16 * 60))


def fly_value(new: list[tuple[str, str]]) -> str:
    return ",".join(f"{lab}:{tok}" for lab, tok in new)


def backup_text(new: list[tuple[str, str]], when: str) -> str:
    return (f"# OSHINEI_SEED_TOKEN backup — Fly is write-only; this is the only copy. Never commit.\n"
            f"# rotated {when}. FIRST line = the token the PC's scheduled scripts use.\n"
            + "".join(f"{lab}:{tok}\n" for lab, tok in new))


def _fly_env() -> dict:
    env = dict(os.environ)
    if not env.get("FLY_API_TOKEN"):
        cfg = Path.home() / ".fly" / "config.yml"
        if cfg.exists():
            m = re.search(r"^access_token:\s*(\S+)", cfg.read_text(encoding="utf-8"), re.M)
            if m:
                env["FLY_API_TOKEN"] = m.group(1)
    return env


def probe(token: str) -> int:
    """HTTP status of a no-op write: 422 = token accepted (body refused), 401/403 = refused."""
    req = urllib.request.Request(RECON + "/api/manual", data=b"{}", method="POST",
                                 headers={"Content-Type": "application/json", "X-Upload-Token": token})
    try:
        return urllib.request.urlopen(req, timeout=20).status
    except urllib.error.HTTPError as e:
        return e.code
    except Exception:
        return 0


def _wait_up(seconds: int = 120) -> bool:
    end = time.time() + seconds
    while time.time() < end:
        try:
            if urllib.request.urlopen(RECON + "/health", timeout=10).status == 200:
                return True
        except Exception:
            pass
        time.sleep(3)
    return False


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--keep", nargs="+", required=True, metavar="LABEL",
                    help=f"holders who get a NEW token (must include {PC_LABEL!r}); everyone else is revoked")
    ap.add_argument("--apply", action="store_true", help="actually rotate (default: dry run)")
    ap.add_argument("--backup", type=Path, default=BACKUP, help=argparse.SUPPRESS)   # tests / other checkouts
    a = ap.parse_args(argv)
    backup = a.backup

    if not backup.exists():
        raise SystemExit(f"{backup} not found — the current list is unknown, refusing to rotate")
    current = parse_backup(backup.read_text(encoding="utf-8"))
    new = plan(current, a.keep)
    cur_labels = [lab for lab, _ in current]
    revoked = [lab for lab in cur_labels if lab not in a.keep]
    added = [lab for lab, _ in new if lab not in cur_labels]
    print(f"current holders : {', '.join(cur_labels) or '(none)'}")
    print(f"new tokens for  : {', '.join(lab for lab, _ in new)}"
          + (f"   (new holders: {', '.join(added)})" if added else ""))
    print(f"revoked         : {', '.join(revoked) or '(none)'}  + every token held anywhere else")
    if not a.apply:
        print("\ndry run — nothing changed. Add --apply to rotate.")
        return 0

    if in_sync_window(_dt.datetime.now()):
        raise SystemExit("too close to a scheduled sync (06:00/12:00/16:00) — try again later")

    when = _dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    archive = backup.with_name(f".seed_tokens.revoked-{_dt.date.today():%Y%m%d}.txt")
    archive.write_text(f"# REVOKED {when} — kept only to answer 'whose token was this'\n"
                       + backup.read_text(encoding="utf-8"), encoding="utf-8")
    backup.write_text(backup_text(new, when), encoding="utf-8")
    print(f"\n1/3 backup written: {backup.name} (old list archived to {archive.name})")

    r = subprocess.run(["flyctl", "secrets", "set", "-a", APP, f"OSHINEI_SEED_TOKEN={fly_value(new)}"],
                       env=_fly_env(), capture_output=True, text=True)
    if r.returncode != 0:
        backup.write_text(archive.read_text(encoding="utf-8").split("\n", 1)[1], encoding="utf-8")
        print((r.stderr or r.stdout)[-800:])
        raise SystemExit("flyctl failed — the old backup was restored, nothing changed on Fly")
    print("2/3 Fly secret set — waiting for the app to come back…")
    if not _wait_up():
        print("  ! the app did not answer /health within 2 min — check `flyctl status -a oshinei-dashboard`")

    print("3/3 checking the live gate…")
    ok = True
    for lab, tok in new:
        code = probe(tok)
        good = code == 422
        ok &= good
        print(f"  new {lab:14} → {'accepted' if good else f'NOT accepted ({code})'}")
    for lab, tok in current:
        code = probe(tok)
        good = code in (401, 403)
        ok &= good
        print(f"  old {lab:14} → {'refused' if good else f'STILL ACCEPTED ({code})'}")
    print("\n" + ("✓ rotation complete" if ok else "✗ check the lines above"))

    print("\nHand these out PRIVATELY (LINE/direct message), one per person — then delete this window's output:")
    for lab, tok in new:
        where = "(this PC — already in the backup file; nothing to do)" if lab == PC_LABEL else ""
        print(f"  {lab}: {tok} {where}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
