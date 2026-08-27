#!/usr/bin/env python3
"""Extract all RDB003_*.zip Krungsri settlements to .xls in-place."""
import zipfile
from pathlib import Path

from recon_paths import require_recon_data_dir

ROOT = require_recon_data_dir()
extracted, skipped = 0, 0

for zip_path in ROOT.rglob("RDB003_*.zip"):
    target_dir = zip_path.parent
    try:
        with zipfile.ZipFile(zip_path) as zf:
            for name in zf.namelist():
                out = target_dir / Path(name).name
                if out.exists():
                    skipped += 1
                    continue
                with zf.open(name) as src, open(out, "wb") as dst:
                    dst.write(src.read())
                extracted += 1
    except Exception as e:
        print(f"ERROR {zip_path.name}: {e}")

print(f"Extracted {extracted} files, skipped {skipped} existing")

# Count what we now have
xls = list(ROOT.rglob("RDB003_*.xls"))
print(f"Total RDB003 .xls now available: {len(xls)}")
by_branch = {}
for f in xls:
    branch = f.relative_to(ROOT).parts[0]  # <branch>/...
    by_branch[branch] = by_branch.get(branch, 0) + 1
for b in sorted(by_branch):
    print(f"  {b}: {by_branch[b]} files")
