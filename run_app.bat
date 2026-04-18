@echo off
title Options Analyzer Pro
echo ============================================
echo    Options Analyzer Pro — Starting Server
echo ============================================
echo.
cd /d "%~dp0"

REM Check if venv exists
if not exist "venv\Scripts\python.exe" (
    echo [Setup] Creating virtual environment...
    python -m venv venv
    echo [Setup] Installing dependencies...
    venv\Scripts\pip install -r requirements.txt
)

echo [Server] Starting Flask server...
echo [Server] Open browser: http://localhost:5001
echo.
venv\Scripts\python app.py
pause
