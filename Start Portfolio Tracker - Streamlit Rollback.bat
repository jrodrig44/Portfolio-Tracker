@echo off
set "SCRIPT_DIR=%~dp0"

echo Starting temporary Streamlit rollback interface...
powershell -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_DIR%run.ps1"

if errorlevel 1 (
    echo.
    echo Streamlit rollback failed to start or exited with an error.
    pause
)
