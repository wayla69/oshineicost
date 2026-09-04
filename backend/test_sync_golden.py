"""Golden for the sync's staging step — every table it writes, pinned.

stage_reports() (sync_mssql.py) is the function that turns the HQ MSSQL rows
into the dashboard's report tables. The `staged` fixture already runs it
end-to-end on an in-memory copy of the schema with canned MSSQL rows; this
snapshots what it wrote — row count and a content hash per table — so the
function can be restructured (Phase 6b) with the same guarantee the API golden
gives the endpoints: identical output, or a diff to look at.

  regenerate:  GOLDEN_UPDATE=1 python -m pytest backend/test_sync_golden.py
"""
import hashlib
import json
import os
from pathlib import Path

import pytest

from test_sync_staging import staged  # noqa: F401  (the fixture that runs stage_reports)

GOLDEN = Path(__file__).parent / "golden" / "sync_stage_reports.json"
UPDATE = os.environ.get("GOLDEN_UPDATE") == "1"


# write clocks are the one thing that legitimately differs run to run
_TS_COLS = {"updated_at", "created_at", "synced_at", "issued_at", "captured_at", "staged_at"}


def _snapshot(con) -> dict:
    out = {}
    tables = [r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
    for t in tables:
        rows = con.execute(f'SELECT * FROM "{t}"').fetchall()
        cols = [d[0] for d in con.execute(f'SELECT * FROM "{t}" LIMIT 0').description]
        canon = sorted(json.dumps([("<ts>" if c in _TS_COLS else _c(v)) for c, v in zip(cols, r)],
                                  ensure_ascii=False) for r in rows)
        out[t] = {"rows": len(rows), "cols": cols,
                  "sha1": hashlib.sha1("\n".join(canon).encode("utf-8")).hexdigest()[:12]}
    return out


def _c(v):
    return round(v, 6) if isinstance(v, float) else v


def test_stage_reports_output_is_pinned(staged):  # noqa: F811
    got = _snapshot(staged)
    if UPDATE or not GOLDEN.exists():
        GOLDEN.parent.mkdir(exist_ok=True)
        GOLDEN.write_text(json.dumps(got, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        if not UPDATE:
            pytest.skip("sync golden created")
        return
    want = json.loads(GOLDEN.read_text(encoding="utf-8"))
    diffs = []
    for t in sorted(set(got) | set(want)):
        if got.get(t) != want.get(t):
            diffs.append(f"{t}: golden={want.get(t)} now={got.get(t)}")
    assert not diffs, "\n".join(diffs[:8])
    assert sum(v["rows"] for v in got.values()) > 0, "the fixture wrote nothing — the golden would be vacuous"
