#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Refresh the hosted Fly.io database with the CURRENT local SQLite data.

Pipeline (one command, idempotent):
  1. VACUUM INTO  → a consistent, compact snapshot of the local DB
  2. gzip         → ~58 MB upload
  3. HTTPS POST   → /api/_admin/seed-db (stages to <db>.incoming on the volume,
                    validated server-side; the live DB is NOT touched yet)
  4. restart      → flyctl machine restart; the app atomically swaps the staged
                    DB in at startup (see _maybe_swap_incoming_db in main.py)
  5. verify       → poll the live API until its row count matches local

Usage:
    python backend/refresh_fly_db.py
    python backend/refresh_fly_db.py --no-restart      # upload only
    python backend/refresh_fly_db.py --db /path/to/oshinei.db --app oshinei-dashboard

Token resolution: --token  →  env OSHINEI_SEED_TOKEN  →  backend/.seed_token.txt
Restart step needs flyctl on PATH (or ~/.fly/bin).
"""
import argparse, gzip, json, os, shutil, sqlite3, subprocess, sys, tempfile, time
import urllib.request, urllib.error
from pathlib import Path

HERE = Path(__file__).parent.resolve()


def find_flyctl():
    exe = shutil.which("flyctl") or shutil.which("fly")
    if exe:
        return exe
    cand = Path.home() / ".fly" / "bin" / ("flyctl.exe" if os.name == "nt" else "flyctl")
    return str(cand) if cand.exists() else None


def get_token(args):
    if args.token:
        return args.token.strip()
    if os.environ.get("OSHINEI_SEED_TOKEN"):
        return os.environ["OSHINEI_SEED_TOKEN"].strip()
    f = HERE / ".seed_token.txt"
    if f.exists():
        return f.read_text(encoding="utf-8").strip()
    sys.exit("No token found. Pass --token, set $OSHINEI_SEED_TOKEN, "
             "or create backend/.seed_token.txt")


def rowcount(db_path):
    c = sqlite3.connect(str(db_path))
    try:
        return c.execute("SELECT COUNT(*) FROM sales_transaction").fetchone()[0]
    finally:
        c.close()


def main():
    ap = argparse.ArgumentParser(description="Refresh the Fly.io DB from local data.")
    ap.add_argument("--api", default="https://oshinei-dashboard.fly.dev")
    ap.add_argument("--app", default="oshinei-dashboard")
    ap.add_argument("--db",  default=str(HERE / "oshinei.db"))
    ap.add_argument("--token", default=None)
    ap.add_argument("--no-restart", action="store_true",
                    help="upload + stage only; restart the machine yourself")
    args = ap.parse_args()
    api = args.api.rstrip("/")
    db = Path(args.db)
    if not db.exists():
        sys.exit(f"local DB not found: {db}")
    token = get_token(args)

    local_n = rowcount(db)
    print(f"local DB : {db}\n           {local_n:,} sales rows")

    tmp = Path(tempfile.mkdtemp(prefix="oshinei_refresh_"))
    snap, gz = tmp / "snap.db", tmp / "snap.db.gz"
    try:
        print("1/5 snapshot (VACUUM INTO)…")
        c = sqlite3.connect(str(db))
        c.execute(f"VACUUM INTO '{snap.as_posix()}'")
        c.close()

        print("2/5 compressing…")
        with open(snap, "rb") as f, gzip.open(gz, "wb", compresslevel=6) as g:
            shutil.copyfileobj(f, g, 1024 * 1024)
        mb = gz.stat().st_size / 1048576
        print(f"     {mb:.1f} MB")

        print("3/5 uploading to staging endpoint…")
        boundary = "----oshineiRefreshBoundary"
        head = (f"--{boundary}\r\n"
                'Content-Disposition: form-data; name="file"; filename="db.gz"\r\n'
                "Content-Type: application/gzip\r\n\r\n").encode()
        tail = f"\r\n--{boundary}--\r\n".encode()
        body = head + gz.read_bytes() + tail
        req = urllib.request.Request(
            f"{api}/api/_admin/seed-db?token={token}", data=body, method="POST",
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
        try:
            with urllib.request.urlopen(req, timeout=600) as r:
                resp = json.load(r)
        except urllib.error.HTTPError as e:
            sys.exit(f"     upload failed: HTTP {e.code} — {e.read().decode(errors='replace')[:300]}")
        print(f"     staged: {resp}")
        if resp.get("staged_rows") != local_n:
            print(f"     WARNING: staged_rows {resp.get('staged_rows')} != local {local_n}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    if args.no_restart:
        print("--no-restart set. Apply with: "
              f"flyctl machine restart <id> --app {args.app}")
        return

    print("4/5 restarting machine(s) to apply swap…")
    fly = find_flyctl()
    if not fly:
        sys.exit("flyctl not found — rerun with --no-restart and restart manually.")
    listing = subprocess.run([fly, "machine", "list", "--app", args.app, "--json"],
                             capture_output=True, text=True)
    try:
        machines = json.loads(listing.stdout or "[]")
        ids = [m["id"] for m in machines]
    except Exception:
        ids = []
    if not ids:
        sys.exit("could not list machines; restart manually with flyctl.")
    for mid in ids:
        subprocess.run([fly, "machine", "restart", mid, "--app", args.app], check=False)

    print("5/5 verifying live data… (cold start + 268 MB swap can take ~1-2 min)")
    deadline = time.time() + 240          # generous: swap-at-startup + matplotlib import
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"{api}/api/profit/summary", timeout=60) as r:
                d = json.load(r)
            if d.get("tx_count") == local_n:
                print(f"     ✓ live tx_count = {d['tx_count']:,} (matches local) — refresh complete.")
                return
        except Exception:
            pass
        time.sleep(4)
    print("     Could not confirm within ~4 min. The swap usually still applies —"
          f" open {api}/ and check, or re-run if needed.")


if __name__ == "__main__":
    main()
