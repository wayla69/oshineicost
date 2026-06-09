"""
Seed the branch registry (bank accounts + acquirer MIDs) from the branch ↔
account/MID template CSV.

Two registries, one CSV:
  • recon_bank_account  — KTB / KBANK account number → branch. Lets a bank
    statement be auto-attributed to its branch by the account it's drawn on.
  • recon_acquirer_mid  — acquirer merchant IDs → branch, incl. the point MIDs
    that split point redemptions out of a settlement (KTC RTxInv mixes regular
    KTC + KTC Point by MID):
        KTC MID → 'KTC'   KTC Point MID → 'KTCPOINT'   BAY MID → 'BAYPOINT'

Trailing spaces are stripped; 'ไม่มี' / blank cells skipped. Idempotent.

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
import urllib.request
from pathlib import Path

# CSV column header → acquirer code
_COL_ACQ  = {"KTC MID": "KTC", "KTC Point MID": "KTCPOINT", "BAY MID": "BAYPOINT"}
# CSV column header → bank code (account-number columns)
_COL_BANK = {"KTB บัญชี": "KTB", "KBANK บัญชี": "KBANK"}
_SKIP = ("", "ไม่มี", "-")


def _rows(csv_path: str):
    with open(csv_path, encoding="utf-8-sig") as f:
        yield from csv.DictReader(f)


def _mids(csv_path: str):
    for row in _rows(csv_path):
        bid = (row.get("branch_id") or "").strip()
        if not bid.isdigit():
            continue
        for col, acq in _COL_ACQ.items():
            mid = (row.get(col) or "").strip()
            if mid not in _SKIP:
                yield int(bid), acq, mid


def _accounts(csv_path: str):
    for row in _rows(csv_path):
        bid = (row.get("branch_id") or "").strip()
        if not bid.isdigit():
            continue
        name = (row.get("สาขา") or "").strip()
        for col, bank in _COL_BANK.items():
            acc = (row.get(col) or "").strip()
            if acc not in _SKIP:
                yield int(bid), bank, acc, name


def seed_sqlite(csv_path: str, db_path: str) -> tuple[int, int]:
    conn = sqlite3.connect(db_path)
    mids = 0
    for bid, acq, mid in _mids(csv_path):
        if not conn.execute("SELECT 1 FROM recon_acquirer_mid WHERE branch_id=? AND acquirer=? AND merchant_id=?",
                            (bid, acq, mid)).fetchone():
            conn.execute("INSERT INTO recon_acquirer_mid (branch_id,acquirer,merchant_id,terminal_id,active) "
                         "VALUES (?,?,?,?,1)", (bid, acq, mid, "")); mids += 1
    accts = 0
    for bid, bank, acc, name in _accounts(csv_path):
        if not conn.execute("SELECT 1 FROM recon_bank_account WHERE bank=? AND account_no=?", (bank, acc)).fetchone():
            conn.execute("INSERT INTO recon_bank_account (branch_id,bank,account_no,account_name,active) "
                         "VALUES (?,?,?,?,1)", (bid, bank, acc, name)); accts += 1
    conn.commit(); conn.close()
    return mids, accts


def _post(base, path, token, body):
    req = urllib.request.Request(base.rstrip("/") + path, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json", "X-Upload-Token": token}, method="POST")
    urllib.request.urlopen(req)


def seed_api(csv_path: str, base: str, token: str) -> tuple[int, int]:
    base = base.rstrip("/")
    have_mid = {(m["branch_id"], m["acquirer"], (m.get("merchant_id") or "").strip())
                for m in json.load(urllib.request.urlopen(base + "/api/acquirer-mids"))}
    have_acc = {(a["bank"], (a.get("account_no") or "").strip())
                for a in json.load(urllib.request.urlopen(base + "/api/bank-accounts"))}
    mids = 0
    for bid, acq, mid in _mids(csv_path):
        if (bid, acq, mid) in have_mid:
            continue
        try:
            _post(base, "/api/acquirer-mids", token,
                  {"branch_id": bid, "acquirer": acq, "merchant_id": mid, "terminal_id": "", "active": 1}); mids += 1
        except Exception as e:
            print(f"  ⚠ MID {bid}/{acq}/{mid}: {e}")
    accts = 0
    for bid, bank, acc, name in _accounts(csv_path):
        if (bank, acc) in have_acc:
            continue
        try:
            _post(base, "/api/bank-accounts", token,
                  {"branch_id": bid, "bank": bank, "account_no": acc, "account_name": name, "active": 1}); accts += 1
        except Exception as e:
            print(f"  ⚠ ACCT {bid}/{bank}/{acc}: {e}")
    return mids, accts


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("csv", nargs="?", default=str(
        Path(__file__).resolve().parents[1] / "recon_accounts_mids_template.csv"))
    ap.add_argument("db", nargs="?", default=os.getenv("OSHINEI_DB", "/data/oshinei.db"))
    ap.add_argument("--api", help="base URL of the live recon service (POST mode)")
    ap.add_argument("--token", default="", help="write token for --api mode")
    a = ap.parse_args()
    if a.api:
        m, ac = seed_api(a.csv, a.api, a.token)
        print(f"seeded {m} MID(s) + {ac} bank account(s) via {a.api}")
    else:
        m, ac = seed_sqlite(a.csv, a.db)
        print(f"seeded {m} MID(s) + {ac} bank account(s) into {a.db}")
