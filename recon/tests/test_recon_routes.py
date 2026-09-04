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


def _routes():
    out = []
    for r in main.app.routes:
        methods = sorted(m for m in (getattr(r, "methods", None) or set()) if m not in ("HEAD", "OPTIONS"))
        if methods:
            out.append([r.path, methods])
    return sorted(out)


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
