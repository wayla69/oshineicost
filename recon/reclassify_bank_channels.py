"""
One-shot script: re-run channel classification on all recon_bank_lines rows
that are currently OTHER (or were wrongly classified) using the updated patterns
from parsers/bank.py.

Run inside a Fly.io SSH console:
  python /app/recon/reclassify_bank_channels.py

Or locally:
  python recon/reclassify_bank_channels.py
"""

import sqlite3
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from recon.parsers.bank import _normalise_channel

DB_PATH = os.getenv("OSHINEI_DB", "/data/oshinei.db")

conn = sqlite3.connect(DB_PATH)
conn.row_factory = sqlite3.Row

rows = conn.execute(
    "SELECT id, description, channel FROM recon_bank_lines"
).fetchall()

updates: list[tuple[str, int]] = []
for r in rows:
    new_ch = _normalise_channel(r["description"] or "")
    if new_ch != r["channel"]:
        updates.append((new_ch, r["id"]))

if not updates:
    print("No rows need reclassification.")
    conn.close()
    sys.exit(0)

# Group for reporting
from collections import defaultdict
summary: dict[str, list[str]] = defaultdict(list)
for new_ch, row_id in updates:
    old_ch = next(r["channel"] for r in rows if r["id"] == row_id)
    summary[f"{old_ch} → {new_ch}"].append(str(row_id))

conn.executemany(
    "UPDATE recon_bank_lines SET channel=? WHERE id=?", updates
)
conn.commit()

print(f"Updated {len(updates)} rows:")
for label, ids in sorted(summary.items()):
    print(f"  {label}: {len(ids)} rows  (ids: {', '.join(ids[:5])}{'...' if len(ids)>5 else ''})")

conn.close()
print("Done. Re-run Match (Run Reconciliation) in the app to regenerate results.")
