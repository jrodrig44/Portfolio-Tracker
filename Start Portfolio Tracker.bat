@echo off
set "SCRIPT_DIR=%~dp0"

powershell -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_DIR%run_nicegui.ps1"

if errorlevel 1 (
    echo.
    echo Portfolio Tracker failed to start or exited with an error.
    pause
)
