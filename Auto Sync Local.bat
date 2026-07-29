@echo off
REM ============================================================
REM  Oshinei - AUTO-SYNC from Easy POS (MSSQL)
REM    1. pull live sales      -> oshinei.db.incoming + reconcile
REM    2. push to Fly          (from the fresh .incoming)        [PUSH_FLY]
REM    3. apply to local DB    (.incoming -> oshinei.db)
REM
REM  Step 3 note: if the local dashboard is RUNNING it holds the DB open, so the
REM  direct swap is skipped and the app's built-in hot-swap poller applies the
REM  .incoming within ~60s (no restart). If the app is NOT running, step 3 swaps
REM  the file directly and the next launch serves fresh data.
REM
REM  Task Scheduler: pass "auto" so it never waits for a keypress:
REM     "Auto Sync Local.bat" auto
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
call "%~f0" %* >> "%~dp0backend\logs\auto_sync_local.log" 2>&1
exit /b %errorlevel%

:run
set "PUSH_FLY=1"
cd /d "%~dp0backend"
echo ==================== %DATE% %TIME% ====================
set "PATH=%USERPROFILE%\.fly\bin;%PATH%"

REM Resolve %PY% to an interpreter that actually HAS pymssql. Bare "python" hits
REM Python312 (no pymssql) under the scheduler's PATH - see _python.bat.
call "%~dp0_python.bat" pymssql
if errorlevel 1 ( if not "%~1"=="auto" pause & exit /b 1 )

echo [1/3] Sync from POS (MSSQL) -^> oshinei.db.incoming + reconcile ...
echo       python: %PY%
"%PY%" sync_mssql.py
if errorlevel 1 (
    echo.
    echo *** SYNC/RECONCILE FAILED - nothing pushed or applied. ***
    if not "%~1"=="auto" pause
    exit /b 1
)

if "%PUSH_FLY%"=="1" (
    echo.
    echo [2/3] Push fresh data to Fly ...
    "%PY%" refresh_fly_db.py --db oshinei.db.incoming
)

echo.
echo [3/3] Apply to local oshinei.db ...
"%PY%" -c "import sync_mssql; sync_mssql.apply_local_swap()"

echo.
echo Done %DATE% %TIME% - POS sync complete (local + Fly).
if not "%~1"=="auto" pause
