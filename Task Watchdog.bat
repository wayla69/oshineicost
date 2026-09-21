@echo off
REM ============================================================
REM  Oshinei - the task watchdog. Reads Windows Task Scheduler and
REM  flags every "Oshinei *" task that FAILED, MISSED its slot, has
REM  not run for days, or is stuck - then tells the live app (admins'
REM  home banner), Telegram (when configured) and this PC (a toast).
REM  Why: "Push Invisible" failed 15 days in a row in 2026-09 and
REM  nobody knew - Task Scheduler tells no one.
REM  Task Scheduler: "Oshinei Task Watchdog", 11:15 and 17:00 daily
REM  + at logon. Register it with:
REM     powershell -ExecutionPolicy Bypass -File scripts\register_task_watchdog.ps1
REM  ASCII ONLY in this file (see Fetch Feedback.bat for why).
REM  Manual: double-click. Scheduler: call with an arg to skip the pause:
REM     "Task Watchdog.bat" auto
REM  Log: backend\logs\task_watchdog.log (tee'd).
REM  Report: backend\logs\task_watchdog.report.txt
REM  It always exits 0 when it managed to LOOK - what it found is in
REM  the report, not in the exit code.
REM ============================================================
if /i not "%~1"=="auto" goto :run
if "%OSHINEI_LOGGED%"=="1" goto :run
set "OSHINEI_LOGGED=1"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0_tee.ps1" -Bat "%~f0" -Log "%~dp0backend\logs\task_watchdog.log" -ArgLine "%*"
exit /b %errorlevel%

:run
cd /d "%~dp0"
echo ==================== %DATE% %TIME% ====================

call "%~dp0_python.bat" sys
if errorlevel 1 ( if "%~1"=="" pause & exit /b 1 )
echo       python: %PY%

"%PY%" -u backend\task_watchdog.py
set "RC=%errorlevel%"
if not "%RC%"=="0" (
    echo.
    echo *** TASK WATCHDOG FAILED - exit %RC% - see backend\logs\task_watchdog.log ***
    if "%~1"=="" pause
    exit /b %RC%
)
if "%~1"=="" pause
exit /b 0
