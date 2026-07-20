#!/usr/bin/env python3
"""Verify what's actually uploaded in production, per branch/month/source."""
import requests
import warnings
from collections import defaultdict
warnings.filterwarnings('ignore')

BASE = "https://oshinei-dashboard.fly.dev:8443"

BRANCHES = {7: "1.CNX (Chiang Mai)", 4: "2.KR1 (Korat 1)",
            26: "4.UBN (Ubon)", 25: "5.UDN (Udon)", 24: "6.SRN (Surin)"}

print("=" * 70)
print("PRODUCTION UPLOAD VERIFICATION")
print("=" * 70)

grand_total = 0
grand_rows = 0
for bid, bname in BRANCHES.items():
    r = requests.get(f"{BASE}/api/uploads", params={"branch_id": bid,
                     "period_month": "2026-05"}, verify=False, timeout=30)
    if r.status_code != 200:
        print(f"\n{bname}: ERROR HTTP {r.status_code}")
        continue
    uploads = r.json()
    by_source = defaultdict(lambda: {"files": 0, "rows": 0})
    for u in uploads:
        st = u.get("source_type", "?")
        by_source[st]["files"] += 1
        by_source[st]["rows"] += (u.get("row_count") or 0)

    print(f"\n{bname} — branch_id {bid}")
    if not uploads:
        print("  (no uploads)")
        continue
    for st in sorted(by_source):
        d = by_source[st]
        print(f"  {st:14} {d['files']:4} files  {d['rows']:8,} rows")
    sub = len(uploads)
    rows = sum((u.get('row_count') or 0) for u in uploads)
    grand_total += sub
    grand_rows += rows
    print(f"  {'TOTAL':14} {sub:4} files  {rows:8,} rows")

print("\n" + "=" * 70)
print(f"GRAND TOTAL: {grand_total} uploads, {grand_rows:,} parsed rows")
print("=" * 70)
