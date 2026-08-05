$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $projectRoot

$venvPath = Join-Path $projectRoot ".venv"
$venvPython = Join-Path $venvPath "Scripts\python.exe"
$venvPip = Join-Path $venvPath "Scripts\pip.exe"

if (-not (Test-Path $venvPython)) {
    Write-Host "Creating virtual environment..."
    py -3 -m venv $venvPath
}

Write-Host "Installing/updating dependencies..."
& $venvPip install -r (Join-Path $projectRoot "requirements.txt") | Out-Host

Write-Host "Starting Dividend Portfolio Tracker..."
& $venvPython (Join-Path $projectRoot "start_streamlit.py")
