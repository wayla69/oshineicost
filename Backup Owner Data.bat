@echo off
REM ============================================================
REM  Oshinei - nightly OFF-SITE backup of the owner-data tables.
REM  Asks the live app for a SQLite file holding ONLY the tables that
REM  exist nowhere else (users, BoM Center bc_*, reconciliation,
REM  feedback, checklists, settings, push subscriptions - the same set
REM  the data swap protects), verifies it and keeps the last 30 files in
REM    %USERPROFILE%\Oshinei_backups
REM  Task Scheduler: "Oshinei Owner Data Backup", 02:30 daily.
REM  Register it once with:  scripts\Setup Backup.bat
REM  Manual: double-click. Scheduler: call with an arg to skip the pause:
REM     "Backup Owner Data.bat" auto
REM  Other uses (extra args go to the script):
REM     "Backup Owner Data.bat" --dry-run
REM     "Backup Owner Data.bat" --restore-check "%USERPROFILE%\Oshinei_backups\oshinei_owner_data_YYYYMMDD_HHMM.sqlite"
REM  The admin token is read from OSHINEI_SEED_TOKEN, backend\.env or
REM  backend\.seed_tokens.txt and is never printed.
REM  Log: backend\logs\backup_owner_data.log (tee'd) and, one line per
REM  run, %USERPROFILE%\Oshinei_backups\backup.log
REM  Docs: docs\BACKUP_OWNER_DATA.md
REM  ASCII ONLY in this file (see Fetch Feedback.bat for why).
REM ============================================================
if /i not "%~1"=="auto" goto :run
if "%OSHINEI_LOGGED%"=="1" goto :run
set "OSHINEI_LOGGED=1"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0_tee.ps1" -Bat "%~f0" -Log "%~dp0backend\logs\backup_owner_data.log" -ArgLine "%*"
exit /b %errorlevel%

:run
cd /d "%~dp0"
echo ==================== %DATE% %TIME% ====================

call "%~dp0_python.bat" sys
if errorlevel 1 ( if "%~1"=="" pause & exit /b 1 )
echo       python: %PY%

REM "auto" is only the tee switch - it is not an argument of the script.
set "ARGS=%*"
if /i "%~1"=="auto" set "ARGS="
"%PY%" -u scripts\backup_owner_tables.py %ARGS%
set "RC=%errorlevel%"
if not "%RC%"=="0" (
    echo.
    echo *** OWNER DATA BACKUP FAILED - exit %RC% - the reason is in %USERPROFILE%\Oshinei_backups\backup.log ***
    if "%~1"=="" pause
    exit /b %RC%
)
if "%~1"=="" pause
exit /b 0
