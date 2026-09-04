"""The recon route table, pinned: every path and method the app serves.

Phase 7c moves the handlers into routers by family. A handler dropped on the
floor by the move would take its URL with it silently — this test compares the
live route table against the one captured before the split.

  regenerate:  GOLDEN_UPDATE=1 python -m pytest recon/tests/test_recon_routes.py
"""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import recon.main as main  # noqa: E402

GOLDEN = Path(__file__).parent / "golden" / "routes.json"


def _walk(routes, prefix=""):
    """Every (path, methods) under `routes`, descending into nested routers.
    Starlette 1.6 / FastAPI 0.141 (production, CI) keep an include_router() as
    a nested _IncludedRouter entry; older versions flatten it. Both must give
    the same table — on 2026-09-04 the flat walk read an EMPTY table on CI."""
    for r in routes:
        sub = getattr(r, "routes", None)
        if sub is None:                       # Starlette 1.6: _IncludedRouter wraps the APIRouter
            sub = getattr(getattr(r, "original_router", None), "routes", None)
        if sub is not None:
            mount = prefix + (getattr(r, "path", "") if type(r).__name__ == "Mount" else "")
            yield from _walk(sub, mount)
            continue
        methods = sorted(m for m in (getattr(r, "methods", None) or set()) if m not in ("HEAD", "OPTIONS"))
        if methods:
            yield [prefix + r.path, methods]


def _routes():
    return sorted(_walk(main.app.routes))


def test_route_table_is_unchanged():
    got = _routes()
    if os.environ.get("GOLDEN_UPDATE") == "1" or not GOLDEN.exists():
        GOLDEN.write_text(json.dumps(got, indent=1) + "\n", encoding="utf-8")
        return
    want = json.loads(GOLDEN.read_text(encoding="utf-8"))
    missing = [r for r in want if r not in got]
    extra = [r for r in got if r not in want]
    assert not missing and not extra, f"missing={missing[:5]} extra={extra[:5]}"
    assert len(got) >= 120, len(got)
