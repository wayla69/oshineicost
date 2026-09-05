@echo off
REM ============================================================
REM  Oshinei - collect customer reviews (Google Business Profile,
REM  Facebook Pages) into the live dashboard's customer-feedback tab.
REM  Daily via Task Scheduler ("Oshinei Feedback Sync", 09:00 - before
REM  the 10:00 DB push; the reviews live on Fly and survive the push).
REM  ASCII ONLY in this file: under _tee.ps1 (UTF-8 console) cmd loses
REM  its read position on multibyte characters and executes fragments
REM  of earlier lines ("'customer' is not recognized", 2026-09-05).
REM  Manual: double-click. Scheduler: call with an arg to skip the pause:
REM     "Fetch Feedback.bat" auto
REM
REM  Setup (once): docs\FEEDBACK_SOURCES.html. Credentials live in
REM  backend\.feedback_config.json (gitignored; Backup DB.bat copies it
REM  with the other secrets). Nothing configured yet = exit 0 with a
REM  note, not a failure. Log: backend\logs\fetch_feedback.log (tee'd
REM  like the sync logs, so a closed window still leaves a trace).
REM ============================================================
if /i not "%~1"=="auto" goto :run
if "%OSHINEI_LOGGED%"=="1" goto :run
set "OSHINEI_LOGGED=1"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0_tee.ps1" -Bat "%~f0" -Log "%~dp0backend\logs\fetch_feedback.log" -ArgLine "%*"
exit /b %errorlevel%

:run
cd /d "%~dp0"
echo ==================== %DATE% %TIME% ====================

REM Resolve %PY% the same way the syncs do (bare "python" may be the wrong one).
call "%~dp0_python.bat" sys
if errorlevel 1 ( if "%~1"=="" pause & exit /b 1 )
echo       python: %PY%

REM -u: unbuffered, so progress reaches the console/log line by line.
"%PY%" -u backend\fetch_feedback.py run --source all --since auto
set "RC=%errorlevel%"
if not "%RC%"=="0" (
    echo.
    echo *** FEEDBACK SYNC FAILED - exit %RC% - see backend\logs\fetch_feedback.log ***
    if "%~1"=="" pause
    exit /b %RC%
)
if "%~1"=="" pause
exit /b 0
