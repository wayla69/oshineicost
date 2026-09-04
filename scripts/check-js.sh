#!/usr/bin/env bash
# Type-check the dashboard's JS slices without a build step (jsconfig.json,
# js/globals.d.ts). Part of scripts/gate.sh.
#
# Fails on the error classes that mean a broken program:
#   TS2304/TS2552  a name that is defined nowhere (or a typo tsc can spell-correct)
#   TS2551         a property typo tsc can spell-correct
#   TS2448         a name used before its declaration (the usage panel was dead
#                  for weeks this way: a local `const api` shadowing api())
#   TS2554         a call with the wrong number of arguments
#   TS2362/TS2365  arithmetic or comparison on a string
#   TS2630         assigning to a function (wrap it deliberately with @ts-ignore)
# and only COUNTS the classes that are noise in an untyped codebase:
#   TS2339         property on a DOM base type / an inferred object literal
#   TS2345/TS2322  argument or assignment shape (URLSearchParams with numbers,
#                  Chart.js config inference)
set -u
cd "$(dirname "$0")/.."
command -v npx >/dev/null 2>&1 || { echo "check-js: npx not found — install Node.js to run the JS check"; exit 0; }
out=$(npx --yes -p typescript tsc -p jsconfig.json 2>&1)
noise='TS2339|TS2345|TS2322'
real=$(printf '%s\n' "$out" | grep "error TS" | grep -Ev "$noise" || true)
n_noise=$(printf '%s\n' "$out" | grep "error TS" | grep -Ec "$noise" || true)
if [ -n "$real" ]; then
    printf '%s\n' "$real"
    echo "check-js: $(printf '%s\n' "$real" | grep -c .) error(s) — $n_noise type-shape note(s) ignored"
    exit 1
fi
echo "check-js: clean — $n_noise type-shape note(s) ignored"
