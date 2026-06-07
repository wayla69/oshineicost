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
set "PUSH_FLY=1"
cd /d "%~dp0backend"
set "PATH=%USERPROFILE%\.fly\bin;%PATH%"

python --version >nul 2>&1
if errorlevel 1 ( echo ERROR: Python not found. & if not "%~1"=="auto" pause & exit /b 1 )

echo [1/3] Sync from POS (MSSQL) -^> oshinei.db.incoming + reconcile ...
python sync_mssql.py
if errorlevel 1 (
    echo.
    echo *** SYNC/RECONCILE FAILED - nothing pushed or applied. ***
    if not "%~1"=="auto" pause
    exit /b 1
)

if "%PUSH_FLY%"=="1" (
    echo.
    echo [2/3] Push fresh data to Fly ...
    python refresh_fly_db.py --db oshinei.db.incoming
)

echo.
echo [3/3] Apply to local oshinei.db ...
python -c "import sync_mssql; sync_mssql.apply_local_swap()"

echo.
echo Done %DATE% %TIME% - POS sync complete (local + Fly).
if not "%~1"=="auto" pause
