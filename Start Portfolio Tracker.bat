@echo off
set "SCRIPT_DIR=%~dp0"

powershell -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_DIR%run_nicegui.ps1"
set "TRACKER_EXIT_CODE=%ERRORLEVEL%"

if "%TRACKER_EXIT_CODE%"=="2" (
    echo.
    echo Graceful shutdown timed out. Forced termination was required, and the launched process tree is confirmed stopped.
    echo This was not a safe graceful shutdown.
    pause
    exit /b 2
)

if "%TRACKER_EXIT_CODE%"=="3" (
    echo.
    echo ERROR: Portfolio Tracker could not confirm termination of its launched process tree. Processes may remain active.
    pause
    exit /b 3
)

if not "%TRACKER_EXIT_CODE%"=="0" (
    echo.
    echo Portfolio Tracker failed during startup or exited unexpectedly.
    pause
    exit /b %TRACKER_EXIT_CODE%
)

exit /b 0
