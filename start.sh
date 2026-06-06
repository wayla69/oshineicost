#!/bin/sh
# Run BOTH FastAPI apps on a single Fly machine so they share the one
# oshinei_data volume (a Fly volume attaches to exactly one machine, so the
# dashboard and recon services cannot live on separate machines and still share
# /data/oshinei.db). recon runs in the background; the dashboard is exec'd as
# PID 1 for clean signal handling and restarts.
uvicorn recon.main:app --host 0.0.0.0 --port 8001 &
exec uvicorn backend.main:app --host 0.0.0.0 --port "${PORT:-8080}"
