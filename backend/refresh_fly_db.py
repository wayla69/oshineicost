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
  5. verify       → poll the live API until its row count matches local. When the
                    live deploy has OSHINEI_AUTH enabled the verify endpoint needs
                    a session cookie, so this logs in first (see login resolution).

Usage:
    python backend/refresh_fly_db.py
    python backend/refresh_fly_db.py --no-restart      # upload only
    python backend/refresh_fly_db.py --db /path/to/oshinei.db --app oshinei-dashboard
    python backend/refresh_fly_db.py --user owner --password '…'   # for auth verify

Token resolution:  --token  →  env OSHINEI_SEED_TOKEN  →  backend/.seed_token.txt
Login resolution (verify only, when OSHINEI_AUTH is on):  --user/--password  →
    env OSHINEI_REFRESH_USER/OSHINEI_REFRESH_PASS  →  backend/.refresh_login.txt
    ('user:pass' on one line, or username and password on two lines). Optional —
    without it the upload/swap still happen; only the row-count verify is skipped.
Restart step needs flyctl on PATH (or ~/.fly/bin).
"""
import argparse, gzip, http.cookiejar, json, os, shutil, sqlite3, subprocess, sys, tempfile, time
import urllib.request, urllib.error
from pathlib import Path

# Force UTF-8 stdout/stderr so the ✓/✗/… status glyphs don't raise
# UnicodeEncodeError on a Windows cp1252 console (which would crash AFTER a
# successful verify and report a false non-zero exit). No-op on UTF-8 consoles.
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except Exception:
        pass

HERE = Path(__file__).parent.resolve()


def find_flyctl():
    exe = shutil.which("flyctl") or shutil.which("fly")
    if exe:
        return exe
    cand = Path.home() / ".fly" / "bin" / ("flyctl.exe" if os.name == "nt" else "flyctl")
    return str(cand) if cand.exists() else None


def fly_env(config=None, environ=None) -> dict:
    """Environment for a flyctl call: FLY_API_TOKEN filled from ~/.fly/config.yml
    when the caller has none. flyctl sometimes fails to read its own config
    ("no access token available", DEPLOY.md) — under Task Scheduler it always
    did, so every nightly push ended with "could not list machines" and the
    staged DB waited for the app's next restart to be swapped in."""
    env = dict(os.environ if environ is None else environ)
    if env.get("FLY_API_TOKEN"):
        return env
    cfg = Path(config) if config else Path.home() / ".fly" / "config.yml"
    try:
        for line in cfg.read_text(encoding="utf-8").splitlines():
            k, _, v = line.partition(":")
            if k.strip() == "access_token" and v.strip():
                env["FLY_API_TOKEN"] = v.strip().strip('"')
                break
    except OSError:
        pass
    return env


def _parse_token_file(path: Path) -> str | None:
    """Return the first valid token from a token file.
    Accepts both bare hex tokens and 'label:hex' lines (multi-user format)."""
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" in line:
            _, tok = line.split(":", 1)
            tok = tok.strip()
            if tok:
                return tok
        else:
            return line
    return None


def get_token(args):
    if args.token:
        tok = args.token.strip()
        # Handle 'label:hex' passed directly — extract just the hex part
        if ":" in tok:
            tok = tok.split(":", 1)[1].strip()
        return tok
    if os.environ.get("OSHINEI_SEED_TOKEN"):
        return os.environ["OSHINEI_SEED_TOKEN"].strip()
    # Accept both .seed_token.txt (bare) and .seed_tokens.txt (multi-user label:hex)
    for name in (".seed_token.txt", ".seed_tokens.txt"):
        f = HERE / name
        if f.exists():
            tok = _parse_token_file(f)
            if tok:
                return tok
    sys.exit("No token found. Pass --token, set $OSHINEI_SEED_TOKEN, "
             "or create backend/.seed_token.txt")


def rowcount(db_path):
    c = sqlite3.connect(str(db_path))
    try:
        return c.execute("SELECT COUNT(*) FROM sales_transaction").fetchone()[0]
    finally:
        c.close()


def get_login(args):
    """Resolve (username, password) for the post-refresh verify when the live
    deploy has OSHINEI_AUTH enabled (the verify endpoint then needs a session).
    Order: --user/--password → env OSHINEI_REFRESH_USER/OSHINEI_REFRESH_PASS →
    backend/.refresh_login.txt ('user:pass' one line, or user + pass two lines).
    Returns (None, None) when nothing is configured (verify is then skipped)."""
    if args.user and args.password:
        return args.user.strip(), args.password
    u, p = os.environ.get("OSHINEI_REFRESH_USER"), os.environ.get("OSHINEI_REFRESH_PASS")
    if u and p:
        return u.strip(), p
    f = HERE / ".refresh_login.txt"
    if f.exists():
        lines = [ln.strip() for ln in f.read_text(encoding="utf-8").splitlines() if ln.strip()]
        if lines and ":" in lines[0]:
            u, p = lines[0].split(":", 1)
            return u.strip(), p.strip()
        if len(lines) >= 2:
            return lines[0], lines[1]
    return None, None


def verify_live(api, local_n, opener):
    """Poll /api/profit/summary until tx_count == local_n. Returns 'ok' on match,
    'auth' if it requires login (HTTP 401), or 'timeout' within the ~4 min window.
    `opener` carries the cookie jar so a prior login is reused."""
    deadline = time.time() + 240          # generous: swap-at-startup + matplotlib import
    while time.time() < deadline:
        try:
            with opener.open(f"{api}/api/profit/summary", timeout=60) as r:
                if json.load(r).get("tx_count") == local_n:
                    return "ok"
        except urllib.error.HTTPError as e:
            if e.code == 401:
                return "auth"
        except Exception:
            pass
        time.sleep(4)
    return "timeout"


def run_verify(api, local_n, args):
    """Step 5: confirm the live DB now reports local_n rows. When the deploy has
    OSHINEI_AUTH enabled the verify endpoint needs a session, so this logs in
    first (credentials via get_login). Never fails the refresh — at worst it
    prints a note explaining the staged swap still applied."""
    print("5/5 verifying live data… (cold start + swap can take ~1-2 min)")
    # Cookie-aware opener so we can authenticate when OSHINEI_AUTH is enabled on
    # the live deploy (the verify endpoint then returns 401 without a session).
    opener = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    result = verify_live(api, local_n, opener)

    if result == "auth":
        # Live API requires a login. Authenticate (if creds are configured) and
        # re-check the row count, reusing the cookie now held by `opener`.
        user, password = get_login(args)
        if user and password:
            try:
                body = json.dumps({"username": user, "password": password}).encode()
                req = urllib.request.Request(
                    f"{api}/api/auth/login", data=body, method="POST",
                    headers={"Content-Type": "application/json"})
                with opener.open(req, timeout=60) as r:
                    who = json.load(r)
                print(f"     logged in as {who.get('username', user)} "
                      f"({who.get('role', '?')}); confirming row count…")
                result = verify_live(api, local_n, opener)
            except urllib.error.HTTPError as e:
                detail = e.read().decode(errors="replace")[:200]
                print(f"     WARNING: login failed (HTTP {e.code} — {detail}). The "
                      "upload staged + machine restarted, so the swap still applies;")
                print(f"              confirm visually at {api}/  (staged {local_n:,} rows).")
                return
            except Exception as e:
                print(f"     WARNING: login error ({e}); could not row-verify. The "
                      f"swap still applies — confirm at {api}/ .")
                return
        else:
            print("     NOTE: live API requires login (OSHINEI_AUTH) and no verify "
                  "credentials are configured, so the row count was not auto-checked.")
            print("           The app answered (HTTP 401) → it restarted and the staged "
                  "swap applies at startup; the upload itself was validated server-side.")
            print(f"           staged {local_n:,} rows OK — verify visually at {api}/ .")
            print("           Enable auto-verify next time via --user/--password, env "
                  "OSHINEI_REFRESH_USER/OSHINEI_REFRESH_PASS, or backend/.refresh_login.txt")
            return

    if result == "ok":
        print(f"     ✓ live tx_count = {local_n:,} (matches local) — refresh complete.")
    else:   # timeout
        print("     Could not confirm within ~4 min. The swap usually still applies —"
              f" open {api}/ and check, or re-run if needed.")


def main():
    ap = argparse.ArgumentParser(description="Refresh the Fly.io DB from local data.")
    ap.add_argument("--api", default="https://oshinei-dashboard.fly.dev")
    ap.add_argument("--app", default="oshinei-dashboard")
    ap.add_argument("--db",  default=str(HERE / "oshinei.db"))
    ap.add_argument("--token", default=None)
    ap.add_argument("--user", default=None,
                    help="login username for the verify step (when OSHINEI_AUTH is on)")
    ap.add_argument("--password", default=None, help="login password (see --user)")
    ap.add_argument("--no-restart", action="store_true",
                    help="upload + stage only; restart the machine yourself")
    ap.add_argument("--verify-only", action="store_true",
                    help="skip snapshot/upload/restart; only run the auth-aware "
                         "row-count verify against the live API")
    args = ap.parse_args()
    api = args.api.rstrip("/")
    db = Path(args.db)
    if not db.exists():
        sys.exit(f"local DB not found: {db}")

    local_n = rowcount(db)
    print(f"local DB : {db}\n           {local_n:,} sales rows")

    if args.verify_only:
        run_verify(api, local_n, args)
        return

    token = get_token(args)

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
        req = urllib.request.Request(                    # token in a header, never the URL (it is logged)
            f"{api}/api/_admin/seed-db", data=body, method="POST",
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}", "X-Seed-Token": token})
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
    env = fly_env()
    listing = subprocess.run([fly, "machine", "list", "--app", args.app, "--json"],
                             capture_output=True, text=True, env=env)
    try:
        machines = json.loads(listing.stdout or "[]")
        ids = [m["id"] for m in machines]
    except Exception:
        ids = []
    if not ids:
        why = (listing.stderr or listing.stdout or "").strip()[:300]
        sys.exit(f"could not list machines ({why or 'no output'}); the staged DB is applied at the "
                 f"app's next restart — or restart now: flyctl machine restart <id> --app {args.app}")
    for mid in ids:
        subprocess.run([fly, "machine", "restart", mid, "--app", args.app], check=False, env=env)

    run_verify(api, local_n, args)


if __name__ == "__main__":
    main()
