#!/bin/sh
# The dashboard serves BOTH apps from one process: the reconciliation app is
# mounted at /recon (see backend/main.py), so it's reachable on the standard 443
# port at /recon/app/. One process means the DB swap (_maybe_swap_incoming_db,
# which carries recon_* tables forward) runs at import before any request — no
# race. PID 1 = uvicorn for clean signals/restarts.
exec uvicorn backend.main:app --host 0.0.0.0 --port "${PORT:-8080}"
