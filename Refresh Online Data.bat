@echo off
cd /d "%~dp0backend"

echo ============================================================
echo  Oshinei - Refresh ONLINE database (Fly.io) from local data
echo ============================================================
echo.
echo This uploads your current local data to the live site
echo   https://oshinei-dashboard.fly.dev/
echo so your team sees the latest numbers. Safe to run anytime.
echo.

:: Resolve %PY% to an interpreter that has the deps (see _python.bat)
call "%~dp0_python.bat" requests
if errorlevel 1 ( pause & exit /b 1 )

:: Make sure flyctl is reachable for the restart step
set "PATH=%USERPROFILE%\.fly\bin;%PATH%"

:: Run the refresh (snapshot -> upload -> restart -> verify)
"%PY%" refresh_fly_db.py %*

echo.
echo Done. You can close this window.
pause
