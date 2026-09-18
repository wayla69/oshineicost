#!/usr/bin/env python3
"""Where the raw recon documents live.

The 241 MB "All recon data" tree stays in the OneDrive folder (backed up,
never committed) while the code lives in this repo. Resolution order:

  1. $RECON_DATA_DIR                        -- explicit override
  2. ./All recon data                       -- a local copy, if one exists
  3. the OneDrive archive folder            -- the normal case
"""
import os
from pathlib import Path

# The old working copy is renamed "_ARCHIVED_Cost Analysis" as the last step of the
# move off OneDrive (scripts/archive_onedrive_copy.ps1). Know BOTH names, or the
# rename that finishes the migration silently breaks every recon script.
_ONEDRIVE_2026 = Path(r"C:/Users/ASUS/OneDrive/Daiki/2026")
_ONEDRIVE_CANDIDATES = (_ONEDRIVE_2026 / "_ARCHIVED_Cost Analysis" / "All recon data",
                        _ONEDRIVE_2026 / "Cost Analysis" / "All recon data")
ONEDRIVE_RECON_DATA = next((c for c in _ONEDRIVE_CANDIDATES if c.is_dir()), _ONEDRIVE_CANDIDATES[0])

_REPO = Path(__file__).resolve().parent


def recon_data_dir(override: str | None = None) -> Path:
    """Return the recon data root. Does not check that it exists."""
    if override:
        return Path(override)
    env = os.getenv("RECON_DATA_DIR")
    if env:
        return Path(env)
    local = _REPO / "All recon data"
    if local.is_dir():
        return local
    return ONEDRIVE_RECON_DATA


def require_recon_data_dir(override: str | None = None) -> Path:
    """Same, but fail loudly with the paths we tried."""
    root = recon_data_dir(override)
    if not root.is_dir():
        raise SystemExit(
            f"Recon data folder not found: {root}\n"
            f"Set RECON_DATA_DIR, pass --folder, or restore the OneDrive "
            f"archive at {ONEDRIVE_RECON_DATA}")
    return root
