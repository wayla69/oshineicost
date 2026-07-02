"""Point recon at a writable scratch DB BEFORE recon.main is imported.

recon.main derives _DB_PATH / _ATTACH_DIR / _SNAPSHOT_DIR from OSHINEI_DB at
import time, defaulting to /data/oshinei.db (the Fly volume). On dev machines
and CI runners /data doesn't exist and can't be created, so the app's startup
hook (_ATTACH_DIR.mkdir) raises PermissionError the moment a TestClient spins
up. The test fixtures already re-point _DB_PATH per-test; this only keeps the
module-level defaults harmless off-Fly.
"""
import os
import tempfile
from pathlib import Path

if "OSHINEI_DB" not in os.environ:
    os.environ["OSHINEI_DB"] = str(
        Path(tempfile.mkdtemp(prefix="recon_test_")) / "oshinei.db"
    )
