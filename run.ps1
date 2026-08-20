$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $projectRoot

$venvPath = Join-Path $projectRoot ".venv"
$venvPython = Join-Path $venvPath "Scripts\python.exe"
$requirementsPath = Join-Path $projectRoot "requirements.txt"
$runtimeDataPath = Join-Path $projectRoot "data"
$requirementsFingerprintPath = Join-Path $runtimeDataPath ".requirements.sha256"
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

Write-Host "Starting Dividend Portfolio Tracker..."
& $venvPython (Join-Path $projectRoot "start_streamlit.py")
