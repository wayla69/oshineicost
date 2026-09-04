#!/usr/bin/env bash
# The merge gate: everything a PR must pass, on the machine that merges it.
# GitHub Actions is disabled on this repo (repo settings → Actions, since
# 2026-08-10), so nothing runs the suites unless someone does — run this before
# every merge and read the LAST line, which is the only thing that matters.
#
#   bash scripts/gate.sh            # backend + recon suites + pyflakes
#   bash scripts/gate.sh backend    # one area
#
# Gates on EXIT CODES. `pytest -q | tail -1` shows the progress bar, not
# "N passed", and once (#108) a red suite was merged that way.
set -u
cd "$(dirname "$0")/.."
PY="${PY:-py -3.14}"
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
if [ "$area" = all ] || [ "$area" = recon ]; then
    run "pyflakes recon"    $PY -m pyflakes $(src recon) recon/routers/*.py
    run "pytest recon"      $PY -m pytest recon/tests -q -p no:cacheprovider
fi
rm -f /tmp/gate_$$.log
if [ "$fail" = 0 ]; then echo "GATE GREEN"; else echo "GATE RED — do not merge"; fi
exit $fail
