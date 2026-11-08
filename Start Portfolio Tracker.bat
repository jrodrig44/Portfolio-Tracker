@echo off
set "SCRIPT_DIR=%~dp0"

powershell -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_DIR%run_nicegui.ps1"
set "TRACKER_EXIT_CODE=%ERRORLEVEL%"

if "%TRACKER_EXIT_CODE%"=="2" (
    echo.
    echo Shutdown required forced termination of a worker or the launched process tree; all required exits were confirmed.
    echo This was not a wholly graceful shutdown.
    pause
    exit /b 2
)

if "%TRACKER_EXIT_CODE%"=="3" (
    echo.
    echo ERROR: Portfolio Tracker could not confirm shutdown of a worker or its launched process tree. Processes may remain active.
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
