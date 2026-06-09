"""
Seed recon_acquirer_mid from the branch ↔ account/MID template CSV.

Loads each branch's acquirer merchant IDs so the reconciler can attribute a
settlement to a branch and split point-redemption rows out of an acquirer
settlement (the KTC RTxInv mixes regular KTC + KTC Point, distinguished only by
MID). Maps:
    KTC MID        → acquirer 'KTC'
    KTC Point MID  → acquirer 'KTCPOINT'
    BAY MID        → acquirer 'BAYPOINT'
Trailing spaces are stripped; 'ไม่มี' / blank cells are skipped. Idempotent.

Two sinks:
  • SQLite (default) — write straight into a DB file (local):
        python -m recon.seed_acquirer_mids [csv] [db_path]
  • Live API — POST through the running recon service (LIVE recon_* tables are
    preserved on every data refresh, so they must be seeded on the server):
        python -m recon.seed_acquirer_mids recon_accounts_mids_template.csv \
            --api https://oshinei-dashboard.fly.dev:8443 --token <WRITE_TOKEN>
"""

from __future__ import annotations
import argparse
import csv
import json
import os
import sqlite3
import sys
import urllib.request
from pathlib import Path

# CSV column header → acquirer code stored in recon_acquirer_mid
_COL_ACQ = {"KTC MID": "KTC", "KTC Point MID": "KTCPOINT", "BAY MID": "BAYPOINT"}


def _mids_from_csv(csv_path: str):
    """Yield (branch_id:int, acquirer:str, merchant_id:str) from the template."""
    with open(csv_path, encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            bid = (row.get("branch_id") or "").strip()
            if not bid.isdigit():
                continue
            for col, acq in _COL_ACQ.items():
                mid = (row.get(col) or "").strip()
                if mid and mid not in ("ไม่มี", "-"):
                    yield int(bid), acq, mid


def seed_sqlite(csv_path: str, db_path: str) -> int:
    conn = sqlite3.connect(db_path)
    inserted = 0
    for bid, acq, mid in _mids_from_csv(csv_path):
        if conn.execute("SELECT 1 FROM recon_acquirer_mid WHERE branch_id=? AND acquirer=? AND merchant_id=?",
                        (bid, acq, mid)).fetchone():
            continue
        conn.execute("INSERT INTO recon_acquirer_mid (branch_id, acquirer, merchant_id, terminal_id, active) "
                     "VALUES (?,?,?,?,1)", (bid, acq, mid, ""))
        inserted += 1
    conn.commit(); conn.close()
    return inserted


def seed_api(csv_path: str, base: str, token: str) -> int:
    base = base.rstrip("/")
    hdr = {"Content-Type": "application/json", "X-Upload-Token": token}
    existing = json.load(urllib.request.urlopen(urllib.request.Request(f"{base}/api/acquirer-mids")))
    have = {(m["branch_id"], m["acquirer"], (m.get("merchant_id") or "").strip()) for m in existing}
    inserted = 0
    for bid, acq, mid in _mids_from_csv(csv_path):
        if (bid, acq, mid) in have:
            continue
        body = json.dumps({"branch_id": bid, "acquirer": acq, "merchant_id": mid,
                           "terminal_id": "", "active": 1}).encode()
        try:
            urllib.request.urlopen(urllib.request.Request(f"{base}/api/acquirer-mids", data=body, headers=hdr, method="POST"))
            inserted += 1
        except Exception as e:
            print(f"  ⚠ {bid}/{acq}/{mid}: {e}")
    return inserted


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("csv", nargs="?", default=str(
        Path(__file__).resolve().parents[1] / "recon_accounts_mids_template.csv"))
    ap.add_argument("db", nargs="?", default=os.getenv("OSHINEI_DB", "/data/oshinei.db"))
    ap.add_argument("--api", help="base URL of the live recon service (POST mode)")
    ap.add_argument("--token", default="", help="write token for --api mode")
    a = ap.parse_args()
    if a.api:
        n = seed_api(a.csv, a.api, a.token)
        print(f"seeded {n} acquirer MID(s) via {a.api}")
    else:
        n = seed_sqlite(a.csv, a.db)
        print(f"seeded {n} acquirer MID(s) into {a.db}")
