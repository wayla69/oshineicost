@echo off
REM ============================================================
REM  Oshinei - SYNC LIVE data: Easy POS (MSSQL) -> SQLite -> Fly
REM  Near real-time. Run manually (double-click) or via Task Scheduler.
REM  Scheduler: call with an arg to skip the pause, e.g.
REM     "Sync Live Data.bat" auto
REM ============================================================
cd /d "%~dp0backend"

python --version >nul 2>&1
if errorlevel 1 ( echo ERROR: Python not found. & if "%~1"=="" pause & exit /b 1 )
set "PATH=%USERPROFILE%\.fly\bin;%PATH%"

echo [1/2] Sync from POS (MSSQL) -> oshinei.db.incoming + reconcile...
python sync_mssql.py
if errorlevel 1 (
    echo.
    echo *** SYNC/RECONCILE FAILED - NOT pushing to production. ***
    if "%~1"=="" pause
    exit /b 1
)

echo.
echo [2/2] Push synced DB to Fly (staged swap + restart)...
python refresh_fly_db.py --db oshinei.db.incoming
echo.
echo Done. Live data is on https://oshinei-dashboard.fly.dev/
if "%~1"=="" pause
