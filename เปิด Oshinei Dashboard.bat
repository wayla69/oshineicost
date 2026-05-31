@echo off
cd /d "%~dp0backend"

echo Starting Oshinei Dashboard...
echo.

:: Check Python
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python not found. Please install Python first.
    pause
    exit /b 1
)

:: Install dependencies silently if needed
pip install fastapi uvicorn openpyxl python-multipart --quiet --disable-pip-version-check

:: Pick a free port — try 8001 first (Oshinei default), fall back to 8002/8003 if busy
set PORT=8001
netstat -ano | findstr LISTENING | findstr :8001 >nul 2>&1
if not errorlevel 1 (
    set PORT=8002
    netstat -ano | findstr LISTENING | findstr :8002 >nul 2>&1
    if not errorlevel 1 set PORT=8003
)

echo Using port %PORT%
echo.

:: Open browser after short delay (runs in background)
start "" cmd /c "timeout /t 3 >nul && start http://localhost:%PORT%"

:: Start server (blocking)
python -m uvicorn main:app --port %PORT%
pause
