@echo off
REM ============================================================
REM  Oshinei - push the daily numbers to Invisible Voice (the
REM  customer-voice SaaS): last 3 days of sales per branch per day,
REM  plus the menu names. Idempotent upserts, safe to re-run.
REM  Daily via Task Scheduler ("Oshinei Push Invisible", 10:45 -
REM  after the 10:00 live sync has refreshed backend\oshinei.db).
REM  ASCII ONLY in this file (see Fetch Feedback.bat for why).
REM  Manual: double-click. Scheduler: call with an arg to skip the pause:
REM     "Push Invisible.bat" auto
REM
REM  Config: backend\.invisible_key.txt (gitignored API key, scope
REM  sales:write). Log: backend\logs\push_invisible.log (tee'd).
REM  First load was done by hand on 2026-09-05 with --since 2025-05-01.
REM ============================================================
if /i not "%~1"=="auto" goto :run
if "%OSHINEI_LOGGED%"=="1" goto :run
set "OSHINEI_LOGGED=1"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0_tee.ps1" -Bat "%~f0" -Log "%~dp0backend\logs\push_invisible.log" -ArgLine "%*"
exit /b %errorlevel%

:run
cd /d "%~dp0"
echo ==================== %DATE% %TIME% ====================

call "%~dp0_python.bat" sys
if errorlevel 1 ( if "%~1"=="" pause & exit /b 1 )
echo       python: %PY%

"%PY%" -u backend\push_invisible.py --days 3 --menu
set "RC=%errorlevel%"
if not "%RC%"=="0" (
    echo.
    echo *** INVISIBLE PUSH FAILED - exit %RC% - see backend\logs\push_invisible.log ***
    if "%~1"=="" pause
    exit /b %RC%
)
if "%~1"=="" pause
exit /b 0
