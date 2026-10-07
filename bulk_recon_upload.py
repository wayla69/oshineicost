#!/usr/bin/env python3
"""
Bulk uploader for recon documents from "All recon data" folder → Oshinei recon app.
Reads branch/channel/month/file structure, maps to app endpoints, uploads in parallel.

Usage:
  python bulk_recon_upload.py --url https://oshinei-dashboard.fly.dev:8443 \
    --token <write-token> [--folder "<path to All recon data>"]

The data folder defaults to $RECON_DATA_DIR, else the OneDrive archive
(see recon_paths.py) -- the raw documents are not kept in this repo.

Set OSHINEI_WRITE_TOKEN env var to skip --token flag.
"""

import argparse
import os
import re
import sys
import json
from pathlib import Path
from collections import defaultdict
from urllib.parse import urljoin, urlsplit, urlunsplit
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

from recon_paths import recon_data_dir

# Map folder names (user-facing) to branch IDs (database)
BRANCH_MAP = {
    "1.CNX": 7,    # Chiang Mai (เชียงใหม่)
    "2.KR1": 4,    # Korat 1 (โคราช1)
    "4.UBN": 26,   # Ubon Ratchathani (อุบลราชธานี)
    "5.UDN": 25,   # Udon Thani (อุดรธานี)
    "6.SRN": 24,   # Surin (สุรินทร์)
}

# Some documents do not sit in the branch/channel/month tree at all -- e.g. the
# flat "May" folder, which holds every bank statement we have. For those the
# branch, month and provider have to come out of the FILENAME instead.
BRANCH_CODE_MAP = {
    "CNX": 7,
    "KR1": 4,
    "UBN": 26,
    "UDN": 25,
    "SRN": 24,
}

# Branch codes we recognise and deliberately do NOT upload, with the reason.
# Listed explicitly so nobody "fixes" the skip later by mapping them to the
# nearest branch id -- that would book another brand's takings against an
# Oshinei branch and inflate its bank side against POS forever.
OUT_OF_SCOPE_CODES = {
    # KBank SA 260-1-03492-4, สาขาถนนชยางกูร อุบลราชธานี -- same account holder
    # (บริษัท โอชิเน เอ็นเตอร์ไพรส์) and same KBank branch as UBN, but a
    # different restaurant: Renshin Ubon. No branch row, no POS data here, so
    # nothing to reconcile it against.
    "RUB": "Renshin Ubon -- different restaurant brand, not in this database",
}

# Whole-word tokens in a loose filename -> (source_type, provider)
LOOSE_SOURCE_TOKENS = {
    "KTB": ("bank", "KTB"),
    "KB": ("bank", "KBank"),
    "KBANK": ("bank", "KBank"),
    "SCB": ("bank", "SCB"),
    "BBL": ("bank", "BBL"),
    "TTB": ("bank", "TTB"),
    "KTC": ("credit_card", "KTC"),
    "KRUNGSRI": ("credit_card", "Krungsri"),
    "BAY": ("credit_card", "Krungsri"),
    "GRAB": ("grab", "Grab"),
    "LINEMAN": ("lineman", "Lineman"),
}

# "05.69", "05.2569", "05.2026", "05-2026", "05_2026"
MONTH_IN_NAME = re.compile(r"(?<!\d)(0[1-9]|1[0-2])[._-](\d{4}|\d{2})(?!\d)")

# A folder like "3.XXX" is a branch folder we do not know -- not a loose bundle
BRANCH_FOLDER_RE = re.compile(r"^\d+\.")

# Map channel folders to document source_type
# Uses substring matching so it's flexible with numbering
CHANNEL_SOURCE = {
    "line man": "lineman",
    "grab food": "grab",
    "grab": "grab",
    "krungsri": "credit_card",
    "kbank": "bank",
    "ktb": "bank",
    "scb": "bank",
    "bbl": "bank",
    "ttb": "bank",
    "krungthai": "bank",
    "ktc": "credit_card",
}

# Provider hints (optional, auto-detected from file content mostly)
FILE_PROVIDER = {
    "kbank": "KBank",
    "ktb": "KTB",
    "krungsri": "Krungsri",
    "ktc": "KTC",
    "grab": "Grab",
    "lineman": "Lineman",
}


def normalize_year(y: int) -> int:
    """Return a Gregorian year from a 2- or 4-digit, CE or Thai Buddhist year.

    '2569' -> 2026, '69' -> 2026 (2569 BE), '2026' -> 2026, '26' -> 2026.
    A 2-digit year is read as Buddhist first (25xx) and falls back to 20xx when
    that lands outside the plausible range -- the documents use both.
    """
    if y < 100:
        be = 2500 + y
        if 2015 <= be - 543 <= 2100:
            return be - 543
        return 2000 + y
    if 2400 <= y <= 2600:      # Thai Buddhist era
        return y - 543
    return y


def parse_month(folder_name: str) -> str:
    """Convert '05.2026', '05.2569', '05.69', '2025', '2026' to '2026-05' etc."""
    # If it's a year only (2025, 2026), return None (skip it)
    if folder_name.isdigit() and len(folder_name) == 4:
        return None

    # Format: MM.YYYY or MM.YY
    parts = folder_name.split(".")
    if len(parts) == 2:
        try:
            m = int(parts[0])
            y = normalize_year(int(parts[1]))
            if 1 <= m <= 12 and y >= 1900:
                return f"{y}-{m:02d}"
        except (ValueError, IndexError):
            pass
    return None


def infer_provider(file_path: str, source_type: str) -> str:
    """Guess provider from filename."""
    name = file_path.lower()
    for key, prov in FILE_PROVIDER.items():
        if key in name:
            return prov
    # Defaults by source_type
    return {"bank": "KBank", "credit_card": "Visa", "grab": "Grab",
            "lineman": "Lineman"}.get(source_type, "")


def should_skip(file_path: Path) -> bool:
    """Dot-files, backups, unsupported types, and non-settlement documents."""
    if file_path.name.startswith("."):
        return True
    if file_path.suffix.lower() in (".txt", ".zip"):
        return True

    name_lower = file_path.name.lower()
    skip_patterns = (
        "e-tax_invoice",        # invoices, not settlement reports
        "invoice_",             # other invoices
        "receipt_",             # receipts
        "line_man",             # malformed lineman xlsx
    )
    if any(p in name_lower for p in skip_patterns):
        return True
    if name_lower.startswith("im"):  # Grab expense receipts
        return True
    return False


def parse_loose_file(file_path: Path, channel_label: str) -> dict:
    """Build an upload job for a file that carries its own branch/month/provider.

    Files under a flat bundle like "May" have no branch or channel folder above
    them, so everything is read off the filename:
        05.69-Statement KTB CNX - ....pdf   -> 2026-05, branch CNX, KTB
        CNX_Statements KB_CA_05.2569.pdf    -> 2026-05, branch CNX, KBank
    Returns None (after explaining why) when the name does not identify a
    branch, a month and a document type.
    """
    tokens = [t.upper() for t in re.split(r"[^0-9A-Za-z]+", file_path.name) if t]

    out_of_scope = next((t for t in tokens if t in OUT_OF_SCOPE_CODES), None)
    if out_of_scope:
        print(f"[skip] {file_path.name} ({OUT_OF_SCOPE_CODES[out_of_scope]})",
              file=sys.stderr)
        return None

    branch_code = next((t for t in tokens if t in BRANCH_CODE_MAP), None)
    if not branch_code:
        print(f"[skip] {file_path.name} (no known branch code in the filename)",
              file=sys.stderr)
        return None

    m = MONTH_IN_NAME.search(file_path.name)
    if not m:
        print(f"[skip] {file_path.name} (no month in the filename)", file=sys.stderr)
        return None
    period_month = f"{normalize_year(int(m.group(2)))}-{int(m.group(1)):02d}"

    hit = next((LOOSE_SOURCE_TOKENS[t] for t in tokens if t in LOOSE_SOURCE_TOKENS), None)
    if hit:
        source_type, provider = hit
    else:
        # fall back to the same substring matching the channel folders use
        name_lower = file_path.name.lower()
        source_type = next((v for k, v in CHANNEL_SOURCE.items() if k in name_lower), None)
        if not source_type:
            print(f"[skip] {file_path.name} (unknown document type)", file=sys.stderr)
            return None
        provider = infer_provider(file_path.name, source_type)

    return {
        "file_path": file_path,
        "branch_id": BRANCH_CODE_MAP[branch_code],
        "branch_name": branch_code,
        "source_type": source_type,
        "period_month": period_month,
        "provider": provider,
        "channel": channel_label,
    }


def collect_uploads(data_folder: Path) -> list[dict]:
    """Scan folder structure and return list of upload jobs."""
    jobs = []

    for branch_folder in sorted(data_folder.iterdir()):
        # Loose documents that name their own branch, e.g. the flat "May" bundle
        if branch_folder.is_file():
            job = parse_loose_file(branch_folder, "(loose)")
            if job:
                jobs.append(job)
            continue

        branch_name = branch_folder.name
        branch_id = BRANCH_MAP.get(branch_name)
        if not branch_id:
            if BRANCH_FOLDER_RE.match(branch_name):
                # numbered like a branch folder, but not one we know
                print(f"⚠️  Skipping unknown branch: {branch_name}", file=sys.stderr)
                continue
            for f in sorted(branch_folder.rglob("*")):
                if not f.is_file() or should_skip(f):
                    continue
                job = parse_loose_file(f, branch_name)
                if job:
                    jobs.append(job)
            continue

        # Scan channel/month/files
        for channel_folder in branch_folder.iterdir():
            if not channel_folder.is_dir():
                continue

            channel_name = channel_folder.name
            # Use case-insensitive substring matching
            source_type = None
            for key, val in CHANNEL_SOURCE.items():
                if key.lower() in channel_name.lower():
                    source_type = val
                    break
            if not source_type:
                print(f"[skip] {branch_name}/{channel_name} (unknown channel type)",
                      file=sys.stderr)
                continue

            for month_folder in channel_folder.iterdir():
                if not month_folder.is_dir():
                    continue

                period_month = parse_month(month_folder.name)
                if not period_month:
                    print(f"⚠️  Could not parse month: {month_folder.name}",
                          file=sys.stderr)
                    continue

                # All files in this month/channel
                for file_path in month_folder.iterdir():
                    if not file_path.is_file():
                        continue

                    if should_skip(file_path):
                        continue

                    jobs.append({
                        "file_path": file_path,
                        "branch_id": branch_id,
                        "branch_name": branch_name,
                        "source_type": source_type,
                        "period_month": period_month,
                        "provider": infer_provider(file_path.name, source_type),
                        "channel": channel_name,
                    })

    return sorted(jobs, key=lambda x: (x["branch_id"], x["period_month"],
                                        x["source_type"], x["file_path"].name))


def upload_file(session, base_url: str, token: str, job: dict,
                verbose: bool = False) -> dict:
    """Upload one file via /api/uploads/{source_type}/confirm endpoint."""
    url = urljoin(base_url, f"/api/uploads/{job['source_type']}/confirm")

    try:
        with open(job["file_path"], "rb") as f:
            files = {"file": (job["file_path"].name, f, "application/octet-stream")}
            data = {
                "branch_id": str(job["branch_id"]),
                "period_month": job["period_month"],
                "provider": job["provider"],
            }
            headers = {"X-Upload-Token": token}

            resp = session.post(url, files=files, data=data, headers=headers,
                                verify=False, timeout=30)

        if resp.status_code == 200:
            result = resp.json()
            status = "OK"
            msg = f"{result.get('rows_saved', 0)} rows"
        else:
            status = "FAIL"
            msg = f"HTTP {resp.status_code}: {resp.text[:100]}"

        if verbose or resp.status_code != 200:
            print(f"[{status}] {job['branch_name']:8} {job['period_month']} "
                  f"{job['source_type']:12} {job['file_path'].name:40} — {msg}")

        return {
            "file": job["file_path"].name,
            "status": status,
            "code": resp.status_code,
            "message": msg,
        }

    except Exception as e:
        print(f"[ERROR] {job['branch_name']:8} {job['period_month']} "
              f"{job['source_type']:12} {job['file_path'].name:40} — {e}",
              file=sys.stderr)
        return {
            "file": job["file_path"].name,
            "status": "FAIL",
            "code": 0,
            "message": str(e),
        }


def login(session, login_url: str, creds_path: Path) -> str:
    """Sign in on the dashboard so the recon writes carry a real session.

    Recon runs with RECON_ENFORCE_AUTH: every POST to /api/ must present the
    dashboard's signed session cookie, so X-Upload-Token alone gets a 401 --
    the token gate and the login gate are two different doors, and writes need
    both. The cookie is host-scoped, not port-scoped, so signing in on the
    dashboard covers the recon app on :8443 of the same host.

    Credentials are read from a file (user:password on one line) that is never
    committed -- backend/.refresh_login.txt by default.
    """
    if not creds_path.is_file():
        raise SystemExit(
            f"Login file not found: {creds_path}. Expected one line "
            f"'username:password'. Pass --login-file, or set RECON_LOGIN_FILE.")
    user, sep, password = creds_path.read_text(encoding="utf-8").strip().partition(":")
    if not sep or not user or not password:
        raise SystemExit(f"{creds_path} must hold one line 'username:password'")

    resp = session.post(urljoin(login_url, "/api/auth/login"),
                        json={"username": user, "password": password},
                        verify=False, timeout=30)
    if resp.status_code != 200:
        raise SystemExit(f"Login failed for {user}: HTTP {resp.status_code} "
                         f"{resp.text[:200]}")
    if not session.cookies.get_dict():
        raise SystemExit(f"Login returned 200 but set no cookie -- the uploads "
                         f"would all 401. Check {login_url}")
    return user


def default_login_url(upload_url: str) -> str:
    """The dashboard (login) origin for a recon URL -- same host, no :8443."""
    parts = urlsplit(upload_url)
    return urlunsplit((parts.scheme, parts.hostname or "", "", "", ""))


def token_from_backup(path: Path | None = None) -> str | None:
    """The PC's write token: the first 'label:token' line of backend/.seed_tokens.txt (the
    backup scripts/rotate_seed_token.py writes) -- so a rotation needs no edit here or in upload.bat."""
    path = path or Path(__file__).resolve().parent / "backend" / ".seed_tokens.txt"
    if not path.is_file():
        return None
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            return (line.split(":", 1)[1] if ":" in line else line).strip() or None
    return None


def recon_login_enabled(session, base_url: str) -> bool:
    """recon's own login (RECON_LOGIN=1, 2026-10-07): writes then pass on X-Upload-Token alone --
    the dashboard cookie means nothing to recon."""
    try:
        r = session.get(urljoin(base_url, "/api/me"), verify=False, timeout=20)
        return bool(r.ok and r.json().get("login_enabled"))
    except Exception:
        return False


def main():
    parser = argparse.ArgumentParser(
        description="Bulk upload recon documents to Oshinei recon app")
    parser.add_argument("--url", default="https://oshinei-dashboard.fly.dev:8443",
                       help="Base URL (default: production Fly)")
    parser.add_argument("--folder", default=None,
                       help="Path to recon data folder "
                            "(default: $RECON_DATA_DIR, else the OneDrive archive)")
    parser.add_argument("--token", default=os.getenv("OSHINEI_WRITE_TOKEN"),
                       help="Write token (or set OSHINEI_WRITE_TOKEN env var)")
    parser.add_argument("--workers", type=int, default=4,
                       help="Parallel upload threads (default: 4)")
    parser.add_argument("--verbose", "-v", action="store_true",
                       help="Print every upload (default: only errors)")
    parser.add_argument("--dry-run", action="store_true",
                       help="Show what would be uploaded, don't actually upload")
    parser.add_argument("--login-file", default=os.getenv("RECON_LOGIN_FILE"),
                       help="File holding 'username:password' for the dashboard "
                            "login (default: backend/.refresh_login.txt)")
    parser.add_argument("--login-url", default=None,
                       help="Dashboard origin to log in against "
                            "(default: same host as --url, no port)")
    parser.add_argument("--no-login", action="store_true",
                       help="Skip the dashboard login (not needed when recon has its own login on)")

    args = parser.parse_args()

    if not args.token:
        args.token = token_from_backup()           # backend/.seed_tokens.txt, first line (the PC's token)
    if not args.token:
        # Prompt for token if not provided
        try:
            import getpass
            args.token = getpass.getpass("Enter OSHINEI_WRITE_TOKEN: ").strip()
        except Exception:
            args.token = input("Enter OSHINEI_WRITE_TOKEN: ").strip()

        if not args.token:
            print("[ERROR] Token required.", file=sys.stderr)
            sys.exit(1)

    data_folder = recon_data_dir(args.folder)
    if not data_folder.exists():
        print(f"❌ Folder not found: {data_folder}", file=sys.stderr)
        sys.exit(1)

    # Collect all upload jobs
    jobs = collect_uploads(data_folder)
    n_branches = len(set(j['branch_id'] for j in jobs))
    print(f"[INFO] Found {len(jobs)} files to upload across {n_branches} branches")

    if args.dry_run:
        print("\n[DRY-RUN] Would upload:")
        for job in jobs[:20]:
            print(f"  {job['branch_name']:8} {job['period_month']} "
                  f"{job['source_type']:12} {job['file_path'].name}")
        if len(jobs) > 20:
            print(f"  ... and {len(jobs) - 20} more")
        return

    # The session + sign-in were lost after #79 (main() used an undefined `session`).
    session = requests.Session()
    if args.no_login:
        pass
    elif recon_login_enabled(session, args.url):
        print("[INFO] recon has its own login -- uploading with the write token only")
    else:
        creds = (Path(args.login_file) if args.login_file
                 else Path(__file__).resolve().parent / "backend" / ".refresh_login.txt")
        who = login(session, args.login_url or default_login_url(args.url), creds)
        print(f"[INFO] signed in on the dashboard as {who}")

    # Upload in parallel
    print(f"\n[START] Uploading {len(jobs)} files ({args.workers} workers)...")
    start = datetime.now()

    results = []
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {
            executor.submit(upload_file, session, args.url, args.token, job, args.verbose): job
            for job in jobs
        }

        for i, future in enumerate(as_completed(futures), 1):
            result = future.result()
            results.append(result)
            if (i % 50 == 0 or i == len(jobs)) and not args.verbose:
                ok = sum(1 for r in results if r["status"] == "OK")
                print(f"  {i}/{len(jobs)} done ({ok} successful)")

    elapsed = (datetime.now() - start).total_seconds()
    ok = sum(1 for r in results if r["status"] == "OK")
    fail = len(results) - ok

    print(f"\n[DONE] {ok} uploaded, {fail} failed in {elapsed:.1f}s")

    if fail > 0:
        print("\n[ERRORS]:")
        for r in results:
            if r["status"] != "OK":
                print(f"  {r['file']} — {r['message']}")
        sys.exit(1)


if __name__ == "__main__":
    main()
