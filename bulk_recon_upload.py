#!/usr/bin/env python3
"""
Bulk uploader for recon documents from "All recon data" folder → Oshinei recon app.
Reads branch/channel/month/file structure, maps to app endpoints, uploads in parallel.

Usage:
  python bulk_recon_upload.py --url https://oshinei-dashboard.fly.dev:8443 \
    --token <write-token> --folder "All recon data"

Set OSHINEI_WRITE_TOKEN env var to skip --token flag.
"""

import argparse
import os
import sys
import json
from pathlib import Path
from collections import defaultdict
from urllib.parse import urljoin
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

# Map folder names (user-facing) to branch IDs (database)
BRANCH_MAP = {
    "1.CNX": 7,    # Chiang Mai (เชียงใหม่)
    "2.KR1": 4,    # Korat 1 (โคราช1)
    "4.UBN": 26,   # Ubon Ratchathani (อุบลราชธานี)
    "5.UDN": 25,   # Udon Thani (อุดรธานี)
    "6.SRN": 24,   # Surin (สุรินทร์)
}

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


def parse_month(folder_name: str) -> str:
    """Convert '05.2026', '05.69', '2025', '2026' to '2026-05' or '2025-01' etc."""
    # If it's a year only (2025, 2026), return None (skip it)
    if folder_name.isdigit() and len(folder_name) == 4:
        return None

    # Format: MM.YYYY or MM.YY
    parts = folder_name.split(".")
    if len(parts) == 2:
        try:
            m = int(parts[0])
            y = int(parts[1])
            if len(parts[1]) == 2 and y < 100:
                # Thai Buddhist year (e.g., 69 = 2026)
                y = 2543 + y
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


def collect_uploads(data_folder: Path) -> list[dict]:
    """Scan folder structure and return list of upload jobs."""
    jobs = []

    for branch_folder in data_folder.iterdir():
        if not branch_folder.is_dir():
            continue

        branch_name = branch_folder.name
        branch_id = BRANCH_MAP.get(branch_name)
        if not branch_id:
            print(f"⚠️  Skipping unknown branch: {branch_name}", file=sys.stderr)
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

                    # Skip dot-files, backups, unsupported types, and wrong document types
                    if file_path.name.startswith("."):
                        continue
                    if file_path.suffix.lower() in (".txt", ".zip"):
                        continue

                    name_lower = file_path.name.lower()
                    # Skip files that don't parse correctly
                    skip_patterns = (
                        "e-tax_invoice",        # invoices, not settlement reports
                        "invoice_",             # other invoices
                        "receipt_",             # receipts
                        "line_man",             # malformed lineman xlsx
                    )
                    if any(p in name_lower for p in skip_patterns):
                        continue
                    if name_lower.startswith("im"):  # Grab expense receipts
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


def upload_file(base_url: str, token: str, job: dict, verbose: bool = False) -> dict:
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

            resp = requests.post(url, files=files, data=data, headers=headers,
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


def main():
    parser = argparse.ArgumentParser(
        description="Bulk upload recon documents to Oshinei recon app")
    parser.add_argument("--url", default="https://oshinei-dashboard.fly.dev:8443",
                       help="Base URL (default: production Fly)")
    parser.add_argument("--folder", default="All recon data",
                       help="Path to recon data folder (default: 'All recon data')")
    parser.add_argument("--token", default=os.getenv("OSHINEI_WRITE_TOKEN"),
                       help="Write token (or set OSHINEI_WRITE_TOKEN env var)")
    parser.add_argument("--workers", type=int, default=4,
                       help="Parallel upload threads (default: 4)")
    parser.add_argument("--verbose", "-v", action="store_true",
                       help="Print every upload (default: only errors)")
    parser.add_argument("--dry-run", action="store_true",
                       help="Show what would be uploaded, don't actually upload")

    args = parser.parse_args()

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

    data_folder = Path(args.folder)
    if not data_folder.exists():
        print(f"❌ Folder not found: {args.folder}", file=sys.stderr)
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

    # Upload in parallel
    print(f"\n[START] Uploading {len(jobs)} files ({args.workers} workers)...")
    start = datetime.now()

    results = []
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {
            executor.submit(upload_file, args.url, args.token, job, args.verbose): job
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
