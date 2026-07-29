@echo off
REM ============================================================
REM  Oshinei - shared Python resolver.  Sets %PY%.
REM
REM  WHY THIS EXISTS (bug found 2026-07-29):
REM  A Scheduled Task's cmd gets the registry PATH, where
REM  %LOCALAPPDATA%\Programs\Python\Python312 comes FIRST - and that
REM  interpreter has NO pymssql/fastapi installed.  The real environment
REM  is the Python Install Manager shim at %LOCALAPPDATA%\Python\bin
REM  (pythoncore-3.14).  So bare "python" worked by hand and failed every
REM  night at 06:00/10:00 with a bare exit 1 (ModuleNotFoundError before
REM  the sync printed a single line).  Never call bare "python" from a
REM  bat that a scheduler runs - call this first and use "%PY%".
REM
REM  Usage:  call "%~dp0_python.bat" pymssql
REM          if errorlevel 1 exit /b 1
REM          "%PY%" sync_mssql.py
REM  Arg 1 = module that MUST be importable (default: sys).
REM ============================================================
set "PY="
set "_REQ=%~1"
if "%_REQ%"=="" set "_REQ=sys"

for %%P in (
    "%LOCALAPPDATA%\Python\bin\python.exe"
    "%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
    "python.exe"
) do (
    if not defined PY (
        "%%~P" -c "import %_REQ%" >nul 2>&1
        if not errorlevel 1 set "PY=%%~P"
    )
)

if not defined PY (
    echo ERROR: no Python found that can "import %_REQ%".
    echo        Tried: %LOCALAPPDATA%\Python\bin, Python312, then PATH.
    echo        Fix:   pip install %_REQ%   for the interpreter you intend to use.
    exit /b 1
)
exit /b 0
