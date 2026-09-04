"""Production imports the app as `backend.main` (start.sh), tests as `main`.

A sibling import that works in one layout and not the other passes every test
and fails on Fly — which is exactly what happened on 2026-09-04 with a bare
`from rules import …`. This runs the production import in a fresh interpreter
from the repository root, the way start.sh does.
"""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_app_imports_as_backend_main_like_production():
    env = {**os.environ, "OSHINEI_NO_SWAP": "1", "PYTHONIOENCODING": "utf-8"}
    r = subprocess.run([sys.executable, "-c", "import backend.main"], cwd=str(ROOT),
                       capture_output=True, text=True, env=env, timeout=180)
    assert r.returncode == 0, r.stderr[-1500:]


def test_core_imports_standalone_like_recon_does():
    """The recon app imports backend.core directly, without backend.main having
    put backend/ on sys.path first. core.py must manage that on its own."""
    env = {**os.environ, "OSHINEI_NO_SWAP": "1", "PYTHONIOENCODING": "utf-8"}
    r = subprocess.run([sys.executable, "-c", "import backend.core"], cwd=str(ROOT),
                       capture_output=True, text=True, env=env, timeout=180)
    assert r.returncode == 0, r.stderr[-1500:]
