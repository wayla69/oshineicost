"""Golden-file regression: every smoke endpoint must return byte-identical JSON.

Phase 1 of the structural plan replaces the same three business rules where
they are hand-copied across the codebase — revenue (÷1.07 − service charge,
124 sites), the effective-cost chain (9 sites), and the branch/group filter
(140 sites) — with one helper each. The replacement is meant to change NOTHING
a caller can see. This test is what makes that claim checkable: the JSON of
all 132 smoke requests, captured on the code as it stood before the first
replacement, compared to the cent after every one.

The fixture DB is deterministic and the responses carry no timestamps (verified
by snapshotting twice a second apart: zero differing paths), so any diff is a
behaviour change — intended or not — and has to be looked at.

  regenerate:  GOLDEN_UPDATE=1 python -m pytest backend/test_golden.py
  inspect:     git diff backend/golden/

Regenerating is a deliberate act: it means "this new output is the correct
one", and the diff of the golden files is part of the review.
"""
import json
import os
import re
from pathlib import Path

import pytest

import test_endpoints as te

GOLDEN = Path(__file__).parent / "golden"
UPDATE = os.environ.get("GOLDEN_UPDATE") == "1"


def _slug(path: str, params: dict) -> str:
    base = re.sub(r"[^a-z0-9]+", "_", path.replace("/api/", "")).strip("_")
    if params:
        q = "_".join(f"{k}-{v}" for k, v in sorted(params.items()))
        base += "__" + re.sub(r"[^A-Za-z0-9._-]+", "_", q)
    return base[:150] + ".json"


# Write timestamps are the one thing that legitimately differs run to run: an
# earlier test in the session POSTs a cost, and /api/cost/diff echoes the
# moment it did. The value carries no business meaning, so it is masked.
_TS_KEYS = {"updated_at", "created_at", "issued_at", "generated_at", "captured_at"}


def _canon(obj):
    """Floats to 6 dp so a harmless 1e-12 reordering of a SUM never shows up."""
    if isinstance(obj, float):
        return round(obj, 6)
    if isinstance(obj, dict):
        return {k: ("<ts>" if k in _TS_KEYS and isinstance(v, str) else _canon(v))
                for k, v in obj.items()}
    if isinstance(obj, list):
        items = [_canon(v) for v in obj]
        # Lists of records are compared as SETS. /api/analysis/period_compare
        # returns its branch rows with no ORDER BY, so two identical databases
        # can hand them back in either order; sorting here keeps that noise out.
        # The price is that this harness cannot see an ORDER BY regression —
        # ordering is pinned by the targeted tests, this one pins the numbers.
        if items and all(isinstance(v, dict) for v in items):
            items.sort(key=lambda v: json.dumps(v, sort_keys=True, ensure_ascii=False))
        return items
    return obj


def _dump(obj) -> str:
    return json.dumps(_canon(obj), ensure_ascii=False, sort_keys=True, indent=1) + "\n"


@pytest.mark.parametrize("path,params", te.SMOKE, ids=[_slug(p, q)[:-5] for p, q in te.SMOKE])
def test_golden(client, path, params):
    r = client.get(path, params=params)
    assert r.status_code == 200, r.text[:300]
    got = _dump(r.json())
    f = GOLDEN / _slug(path, params)
    if UPDATE or not f.exists():
        GOLDEN.mkdir(exist_ok=True)
        f.write_text(got, encoding="utf-8")
        if not UPDATE:
            pytest.skip(f"golden file created: {f.name}")
        return
    want = f.read_text(encoding="utf-8")
    if got != want:
        # show the first differing line, not two 40 KB blobs
        gl, wl = got.splitlines(), want.splitlines()
        for i, (a, b) in enumerate(zip(gl, wl)):
            if a != b:
                pytest.fail(f"{f.name} differs at line {i + 1}:\n  golden: {b[:160]}\n  now:    {a[:160]}")
        pytest.fail(f"{f.name}: length differs ({len(wl)} → {len(gl)} lines)")


def test_every_smoke_entry_has_a_golden_file():
    """A new smoke entry without a snapshot would silently be unguarded."""
    missing = [_slug(p, q) for p, q in te.SMOKE if not (GOLDEN / _slug(p, q)).exists()]
    assert not missing, f"run with GOLDEN_UPDATE=1 to capture: {missing[:5]}"
