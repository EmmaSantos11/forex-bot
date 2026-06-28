@echo off
title Mr Bera's Bot - Master Launcher
cd /d "%~dp0"
color 0B
chcp 65001 >nul
set PYTHONIOENCODING=utf-8

echo.
echo  ======================================================
echo       MR BERA'S BOT  -  Master Launcher v2.0
echo  ======================================================
echo.
echo  Starting all systems...
echo  Close this window (or press Ctrl+C) to stop everything.
echo.

:: Prevent PC from sleeping while the system is running
powercfg /change standby-timeout-ac 0  >nul 2>&1
powercfg /change monitor-timeout-ac 0  >nul 2>&1
echo  [OK] Sleep prevention active - PC will not sleep while bot runs.
echo.

:: Activate virtual environment (lives outside OneDrive to avoid DLL blocks)
if not exist "C:\forex_venv\Scripts\activate.bat" (
    echo  [ERROR] Virtual environment not found at C:\forex_venv
    echo.
    echo  Run these commands once to create it:
    echo    python -m venv C:\forex_venv
    echo    C:\forex_venv\Scripts\pip install -r requirements.txt
    echo.
    pause
    exit /b 1
)

call C:\forex_venv\Scripts\activate.bat
echo  [OK] Virtual environment activated  (C:\forex_venv)
echo.

:: Launch everything via Python orchestrator
python launch.py

:: Restore Windows sleep settings on exit
powercfg /change standby-timeout-ac 30 >nul 2>&1
powercfg /change monitor-timeout-ac 15 >nul 2>&1
echo.
echo  [OK] Sleep settings restored.
echo  All systems stopped. Press any key to close.
pause >nul
