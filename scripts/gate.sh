#!/usr/bin/env bash
# The merge gate: everything a PR must pass, on the machine that merges it.
# Run this before every merge and read the LAST line, which is the only thing
# that matters. It is NECESSARY, not sufficient: GitHub Actions is enabled again
# (.github/workflows/tests.yml runs the same suites on ubuntu for every push and
# PR, and a red run emails the owner). This gate runs on Windows, so a test that
# only works here passes it and fails CI - 28 red runs on 2026-09-21 from one such
# test. Merge only when `gh pr checks <n>` is green as well.
#
#   bash scripts/gate.sh            # backend + recon suites + pyflakes
#   bash scripts/gate.sh backend    # one area: backend | recon | js
#
# Gates on EXIT CODES. `pytest -q | tail -1` shows the progress bar, not
# "N passed", and once (#108) a red suite was merged that way.
set -u
cd "$(dirname "$0")/.."
# Python 3.12 = the interpreter that carries the PINNED production versions
# (backend/requirements.txt: fastapi 0.141 / starlette 1.6, same as Fly and CI).
# The 3.14 install has an older FastAPI that flattens included routers, so a
# recon route-table test passed here and failed on CI (2026-09-04). Override
# with PY="py -3.14" only to compare.
PY="${PY:-py -3.12}"
[ -x "$(command -v py 2>/dev/null)" ] || PY="${PY:-python}"
area="${1:-all}"
fail=0

run() {   # run <label> <command…> — print one line per check, remember failures
    local label="$1"; shift
    if "$@" > /tmp/gate_$$.log 2>&1; then
        printf '  ✓ %s\n' "$label"
    else
        printf '  ✗ %s\n' "$label"; tail -15 /tmp/gate_$$.log | sed 's/^/      /'; fail=1
    fi
}

echo "gate: $(git rev-parse --short HEAD) ($(git branch --show-current))"
# pyflakes covers the SOURCE modules (kept at zero warnings since Phase 2); test
# files import fixtures by name, which pyflakes reads as unused/redefined.
src() { ls "$1"/*.py | grep -v '/test_\|/conftest\.py'; }
if [ "$area" = all ] || [ "$area" = backend ]; then
    run "pyflakes backend"  $PY -m pyflakes $(src backend) backend/routers/*.py
    run "pytest backend"    $PY -m pytest backend -q -p no:cacheprovider
fi
if [ "$area" = all ] || [ "$area" = js ]; then
    run "check-js (tsc --checkJs)" bash scripts/check-js.sh
fi
if [ "$area" = all ] || [ "$area" = recon ]; then
    run "pyflakes recon"    $PY -m pyflakes $(src recon) recon/routers/*.py
    run "pytest recon"      $PY -m pytest recon/tests -q -p no:cacheprovider
fi
rm -f /tmp/gate_$$.log
if [ "$fail" = 0 ]; then echo "GATE GREEN"; else echo "GATE RED — do not merge"; fi
exit $fail
