@echo off
REM ============================================================
REM  Oshinei - AUD-2 off-site backup + restore test of oshinei.db
REM  Daily via Task Scheduler ("Oshinei DB Backup", 07:30, after the
REM  06:00 local sync has swapped the fresh DB in). Manual: double-click.
REM  Scheduler: call with an arg to skip the pause:  "Backup DB.bat" auto
REM
REM  What it does:  backend\db_backup.py backup  -> verified online copy of
REM  backend\oshinei.db, gzipped (~250 MB), + manifest (row counts, sha256),
REM  into the OneDrive folder (= off-site: OneDrive syncs it to the cloud),
REM  rotated to 7 daily + 6 monthly copies; then db_backup.py verify on the
REM  file just written = the restore test auditors ask evidence for. The
REM  console+log line "RESULT: PASS" per day is that evidence
REM  (backend\logs\db_backup.log, tee'd like the sync logs).
REM ============================================================
if /i not "%~1"=="auto" goto :run
if "%OSHINEI_LOGGED%"=="1" goto :run
set "OSHINEI_LOGGED=1"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0_tee.ps1" -Bat "%~f0" -Log "%~dp0backend\logs\db_backup.log" -ArgLine "%*"
exit /b %errorlevel%

:run
cd /d "%~dp0"
echo ==================== %DATE% %TIME% ====================

REM Resolve %PY% the same way the syncs do (bare "python" may be the wrong one).
call "%~dp0_python.bat" sqlite3
if errorlevel 1 ( if "%~1"=="" pause & exit /b 1 )

set "OUT=%USERPROFILE%\OneDrive\Oshinei Backups"
for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyy-MM-dd_HHmm"') do set "STAMP=%%i"

echo [1/2] backup backend\oshinei.db -^> "%OUT%\oshinei_%STAMP%.db.gz"
"%PY%" -u backend\db_backup.py backup --db backend\oshinei.db --out "%OUT%" --stamp %STAMP% --gzip --keep-daily 7 --keep-monthly 6
if errorlevel 1 (
    echo.
    echo *** BACKUP FAILED ***
    if "%~1"=="" pause
    exit /b 1
)

REM Recon evidence files (slips/receipts) live on the Fly volume, not in the DB.
REM Non-fatal: the DB copy above is the critical part; an empty folder writes nothing.
"%PY%" -u backend\pull_fly_attachments.py --out "%OUT%" --stamp %STAMP%

REM The gitignored secrets live only on this PC: without them a fresh clone
REM cannot sync (the 2026-07-27 outage). Keep an off-site copy next to the DB.
if not exist "%OUT%\secrets" mkdir "%OUT%\secrets"
for %%F in ("backend\.env" "backend\.seed_tokens.txt" "backend\.manager_token.txt" "backend\.refresh_login.txt" "backend\.feedback_config.json" "recon\.pdf_passwords.txt" "upload.bat") do (
    if exist "%%~F" copy /y "%%~F" "%OUT%\secrets\" >nul
)
echo       secrets copied to "%OUT%\secrets"

echo.
echo [2/2] restore test of the file just written
"%PY%" -u backend\db_backup.py verify --backup "%OUT%\oshinei_%STAMP%.db.gz"
if errorlevel 1 (
    echo.
    echo *** RESTORE TEST FAILED - the backup written today is not usable ***
    if "%~1"=="" pause
    exit /b 1
)

echo.
echo Done %DATE% %TIME% - backup + restore test PASS
if "%~1"=="" pause
