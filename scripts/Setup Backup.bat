@echo off
REM ============================================================
REM  Oshinei - set up the nightly owner-data backup (run once, by the owner).
REM  1. registers the Task Scheduler task "Oshinei Owner Data Backup" (02:30 daily)
REM  2. runs one backup now (--once) so you can see it work
REM  Remove the task again:   "Setup Backup.bat" -Remove
REM  Needs the admin token in OSHINEI_SEED_TOKEN, backend\.env or
REM  backend\.seed_tokens.txt (the same one the data refresh uses).
REM  Docs: docs\BACKUP_OWNER_DATA.md
REM  ASCII ONLY in this file (see Fetch Feedback.bat for why).
REM ============================================================
cd /d "%~dp0.."
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0register_owner_backup.ps1" %*
if errorlevel 1 ( echo. & echo *** could not register the task & pause & exit /b 1 )
if /i "%~1"=="-Remove" ( pause & exit /b 0 )
echo.
echo ---- one backup now ----
call "%~dp0..\Backup Owner Data.bat" --once
echo.
pause
