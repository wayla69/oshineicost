@echo off
rem Deploy the latest committed code (origin/main) to Fly from a clean isolated worktree.
rem Pass a ref to deploy something else, e.g.:  "Deploy to Fly (clean).bat" v1.2
cd /d "%~dp0"
bash scripts/deploy-clean.sh %1
echo.
pause
