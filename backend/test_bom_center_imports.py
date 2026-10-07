"""BoM Center — imports that work in BOTH ways the app starts.

Locally the app runs from backend/ (modules are `routers.x`); production runs
`uvicorn backend.main:app` (modules are `backend.routers.x`). An absolute
`from routers.ingredients import …` inside the BoM Center code therefore loads a
SECOND copy of that module on production, which core.ttl_cache refuses — so the
forced-order list never reached the master seed, and the DATAP / price imports
would have failed (found 2026-10-07 when the production seed showed 0 aliases).
"""
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent


def test_no_absolute_router_imports_in_bom_center():
    files = [HERE / "routers" / "bom_center.py"] + sorted((HERE / "bom_center").glob("*.py"))
    bad = []
    for f in files:
        for i, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            if re.match(r"\s*(from routers[.\s]|import routers\b)", line):
                bad.append(f"{f.name}:{i}: {line.strip()}")
    assert not bad, "use a relative import (routers/) or forecast._loaded() (bom_center/): " + "; ".join(bad)


def test_loaded_finds_the_running_copy():
    import sys
    from bom_center.forecast import _loaded
    mod = _loaded("routers.ingredients")
    assert mod is sys.modules.get("routers.ingredients") or mod is sys.modules.get("backend.routers.ingredients")
    assert hasattr(mod, "_ing_season_resolve")


def test_forced_list_reaches_the_seed():
    from routers import bom_center as R
    f = R._forced()
    assert f is not None and "items" in f      # the list may be empty in tests, but the import must work
