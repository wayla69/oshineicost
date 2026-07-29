@echo off
REM ============================================================
REM  Oshinei - SYNC LIVE data: Easy POS (MSSQL) -> SQLite -> Fly
REM  Near real-time. Run manually (double-click) or via Task Scheduler.
REM  Scheduler: call with an arg to skip the pause, e.g.
REM     "Sync Live Data.bat" auto
REM ============================================================
REM --- Scheduler runs get their whole output teed to a log. WHY: Task Scheduler
REM keeps only an exit code, its Operational log is off by default, and this bat
REM printed nowhere - so two days of silent failures (2026-07-29) left no trace.
REM Re-enters itself once with output redirected; interactive runs are unaffected.
REM Keep the goto form: "exit /b %errorlevel%" inside a ( ) block would expand at
REM parse time and always report 0, hiding the very failures we are logging.
if /i not "%~1"=="auto" goto :run
if "%OSHINEI_LOGGED%"=="1" goto :run
if not exist "%~dp0backend\logs" mkdir "%~dp0backend\logs"
set "OSHINEI_LOGGED=1"
call "%~f0" %* >> "%~dp0backend\logs\sync_live_fly.log" 2>&1
exit /b %errorlevel%

:run
cd /d "%~dp0backend"
echo ==================== %DATE% %TIME% ====================

REM Resolve %PY% to an interpreter that actually HAS pymssql. Bare "python" hits
REM Python312 (no pymssql) under the scheduler's PATH - see _python.bat.
call "%~dp0_python.bat" pymssql
if errorlevel 1 ( if "%~1"=="" pause & exit /b 1 )
set "PATH=%USERPROFILE%\.fly\bin;%PATH%"

REM NOTE: keep the arrow escaped as -^>. An unescaped > REDIRECTS this echo into
REM a file literally named "oshinei.db.incoming", destroying the staged DB.
echo [1/2] Sync from POS (MSSQL) -^> oshinei.db.incoming + reconcile...
echo       python: %PY%
"%PY%" sync_mssql.py
if errorlevel 1 (
    echo.
    echo *** SYNC/RECONCILE FAILED - NOT pushing to production. ***
    if "%~1"=="" pause
    exit /b 1
)

echo.
echo [2/2] Push synced DB to Fly (staged swap + restart)...
"%PY%" refresh_fly_db.py --db oshinei.db.incoming
echo.
echo Done. Live data is on https://oshinei-dashboard.fly.dev/
if "%~1"=="" pause
