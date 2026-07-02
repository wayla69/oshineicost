"""Build the hermetic e2e fixture DB — the exact dataset the backend pytest
fixture uses (backend/conftest._build_db), so the dashboard has coherent
numbers on every tab without a production data pull.

Usage:  python tests/e2e/seed_db.py [output_path]
Then:   OSHINEI_DB=<output_path> uvicorn backend.main:app --port 8010
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

import conftest  # noqa: E402  (backend/conftest.py; sets OSHINEI_NO_SWAP=1)

out = Path(sys.argv[1] if len(sys.argv) > 1 else ROOT / "tests" / "e2e" / ".e2e-fixture.db")
out.parent.mkdir(parents=True, exist_ok=True)
if out.exists():
    out.unlink()
conftest._build_db(str(out))
print(f"seeded e2e fixture DB -> {out}")
