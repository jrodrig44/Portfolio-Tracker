$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $projectRoot

$venvPath = Join-Path $projectRoot ".venv"
$venvPython = Join-Path $venvPath "Scripts\python.exe"
$requirementsPath = Join-Path $projectRoot "requirements.txt"
$runtimeDataPath = Join-Path $projectRoot "data"
$requirementsFingerprintPath = Join-Path $runtimeDataPath ".requirements.sha256"
$rollbackPort = 8502
$rollbackUrl = "http://localhost:$rollbackPort"
$environmentCreated = $false

if (-not (Test-Path $venvPython)) {
    Write-Host "Creating virtual environment..."
    & py -3 -m venv $venvPath
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path $venvPython)) {
        throw "Virtual environment creation failed. Portfolio Tracker was not started."
    }
    $environmentCreated = $true
}

try {
    $requirementsHash = (Get-FileHash -LiteralPath $requirementsPath -Algorithm SHA256 -ErrorAction Stop).Hash.ToLowerInvariant()
}
catch {
    throw "Unable to read or hash requirements.txt. Portfolio Tracker was not started. $($_.Exception.Message)"
}

$installedHash = ""
if (Test-Path -LiteralPath $requirementsFingerprintPath) {
    try {
        $installedHash = (Get-Content -Raw -LiteralPath $requirementsFingerprintPath -ErrorAction Stop).Trim().ToLowerInvariant()
    }
    catch {
        throw "Unable to read the dependency fingerprint. Portfolio Tracker was not started. $($_.Exception.Message)"
    }
}

$dependenciesNeedInstall = $environmentCreated -or ($installedHash -ne $requirementsHash)
if ($dependenciesNeedInstall) {
    Write-Host "Installing/updating dependencies..."
    & $venvPython -m pip install -r $requirementsPath
    $pipExitCode = $LASTEXITCODE
    if ($pipExitCode -ne 0) {
        throw "Dependency installation failed with exit code $pipExitCode. The dependency fingerprint was not updated, and Portfolio Tracker was not started."
    }

    New-Item -ItemType Directory -Path $runtimeDataPath -Force | Out-Null
    Set-Content -LiteralPath $requirementsFingerprintPath -Value $requirementsHash -NoNewline -Encoding ascii
}
else {
    Write-Host "Dependencies unchanged; skipping installation."
}

$existingListener = Get-NetTCPConnection -LocalPort $rollbackPort -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
if ($null -ne $existingListener) {
    throw "Rollback port $rollbackPort is already in use by process ID $($existingListener.OwningProcess). Streamlit rollback was not started. Close that listener and try again."
}

Write-Host "Starting temporary Streamlit rollback on $rollbackUrl..."
$env:STREAMLIT_SERVER_PORT = [string]$rollbackPort
$env:STREAMLIT_BROWSER_GATHER_USAGE_STATS = "false"
try {
    & $venvPython (Join-Path $projectRoot "start_streamlit.py")
}
finally {
    Remove-Item Env:STREAMLIT_SERVER_PORT -ErrorAction SilentlyContinue
    Remove-Item Env:STREAMLIT_BROWSER_GATHER_USAGE_STATS -ErrorAction SilentlyContinue
}
