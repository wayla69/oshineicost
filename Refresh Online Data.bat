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

:: Check Python
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python not found. Please install Python first.
    pause
    exit /b 1
)

:: Make sure flyctl is reachable for the restart step
set "PATH=%USERPROFILE%\.fly\bin;%PATH%"

:: Run the refresh (snapshot -> upload -> restart -> verify)
python refresh_fly_db.py %*

echo.
echo Done. You can close this window.
pause
