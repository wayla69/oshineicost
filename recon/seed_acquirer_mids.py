"""
Seed recon_acquirer_mid from the branch ↔ account/MID template CSV.

Loads each branch's acquirer merchant IDs so the reconciler can attribute a
settlement to a branch and, crucially, split point-redemption rows out of an
acquirer settlement (the KTC RTxInv mixes regular KTC + KTC Point, distinguished
only by MID). Maps:
    KTC MID        → acquirer 'KTC'
    KTC Point MID  → acquirer 'KTCPOINT'
    BAY MID        → acquirer 'BAYPOINT'
Trailing spaces are stripped; 'ไม่มี' / blank cells are skipped. Idempotent
(re-running won't duplicate a (branch, acquirer, merchant_id) row).

Run:
    python -m recon.seed_acquirer_mids [csv_path] [db_path]
defaults: ./recon_accounts_mids_template.csv  and  $OSHINEI_DB
"""

from __future__ import annotations
import csv
import os
import sqlite3
import sys
from pathlib import Path

# CSV column header → acquirer code stored in recon_acquirer_mid
_COL_ACQ = {"KTC MID": "KTC", "KTC Point MID": "KTCPOINT", "BAY MID": "BAYPOINT"}


def seed(csv_path: str, db_path: str) -> int:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    inserted = 0
    with open(csv_path, encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            bid = (row.get("branch_id") or "").strip()
            if not bid.isdigit():
                continue
            bid = int(bid)
            for col, acq in _COL_ACQ.items():
                mid = (row.get(col) or "").strip()
                if not mid or mid in ("ไม่มี", "-"):
                    continue
                exists = conn.execute(
                    "SELECT 1 FROM recon_acquirer_mid WHERE branch_id=? AND acquirer=? AND merchant_id=?",
                    (bid, acq, mid)).fetchone()
                if exists:
                    continue
                conn.execute(
                    "INSERT INTO recon_acquirer_mid (branch_id, acquirer, merchant_id, terminal_id, active) "
                    "VALUES (?,?,?,?,1)", (bid, acq, mid, ""))
                inserted += 1
    conn.commit()
    conn.close()
    return inserted


if __name__ == "__main__":
    csv_path = sys.argv[1] if len(sys.argv) > 1 else str(
        Path(__file__).resolve().parents[1] / "recon_accounts_mids_template.csv")
    db_path = sys.argv[2] if len(sys.argv) > 2 else os.getenv("OSHINEI_DB", "/data/oshinei.db")
    n = seed(csv_path, db_path)
    print(f"seeded {n} acquirer MID(s) into {db_path}")
