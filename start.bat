@echo off
setlocal enabledelayedexpansion
title Hive
cd /d "%~dp0"

echo.
echo   +==============================+
echo   :         H I V E              :
echo   +==============================+
echo.

:: ── Step 1: Find Python ──────────────────────────────────────────
set "PY="
where python >nul 2>nul && set "PY=python"
if not defined PY (
    where python3 >nul 2>nul && set "PY=python3"
)
if not defined PY (
    where py >nul 2>nul && set "PY=py -3"
)

if not defined PY (
    echo   [ERROR] Python 3 is not installed or not in PATH.
    echo   Download it from https://python.org
    echo.
    pause
    exit /b 1
)

:: Verify it's Python 3
for /f "tokens=*" %%v in ('!PY! -c "import sys; print(sys.version_info.major)" 2^>nul') do set "PYVER=%%v"
if not "!PYVER!"=="3" (
    echo   [ERROR] Python 3 required but found Python !PYVER!
    echo   Download Python 3 from https://python.org
    echo.
    pause
    exit /b 1
)

for /f "tokens=*" %%v in ('!PY! --version 2^>^&1') do set "PYVERSTR=%%v"
echo   [OK] Found !PYVERSTR!

:: ── Step 2: Create venv if missing ───────────────────────────────
if not exist "venv\Scripts\python.exe" (
    echo   [1/3] Creating virtual environment...
    !PY! -m venv venv
    if !errorlevel! neq 0 (
        echo   [ERROR] Failed to create virtual environment.
        echo   Try: !PY! -m pip install virtualenv
        pause
        exit /b 1
    )
    echo         Done.
) else (
    echo   [1/3] Virtual environment found.
)

set "VPYTHON=.\venv\Scripts\python.exe"

:: ── Step 3: Install dependencies ─────────────────────────────────
echo   [2/3] Installing dependencies...
%VPYTHON% -m pip install -q --upgrade pip 2>nul
%VPYTHON% -m pip install -q -r requirements.txt 2>nul
if !errorlevel! neq 0 (
    echo   [WARN] Some packages may have failed. Continuing anyway...
) else (
    echo         Done.
)

:: ── Step 4: Ensure models folder ─────────────────────────────────
if not exist "models" mkdir models

:: ── Step 5: Start server ─────────────────────────────────────────
echo   [3/3] Starting Hive server...
echo.
echo   ----------------------------------------
echo     App:     http://127.0.0.1:8080
echo     Stop:    Press CTRL+C in this window
echo   ----------------------------------------
echo.

:: Open browser after 2s delay (background)
start /b %VPYTHON% -c "import time,webbrowser; time.sleep(2); webbrowser.open('http://127.0.0.1:8080')"

:: Launch uvicorn
%VPYTHON% -m uvicorn backend.main:app --host 127.0.0.1 --port 8080

:: If we get here, server stopped
echo.
echo   Hive stopped.
pause
