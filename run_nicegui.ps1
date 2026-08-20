$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $projectRoot

$venvPath = Join-Path $projectRoot ".venv"
$venvPython = Join-Path $venvPath "Scripts\python.exe"
$requirementsPath = Join-Path $projectRoot "requirements.txt"
$runtimeDataPath = Join-Path $projectRoot "data"
$requirementsFingerprintPath = Join-Path $runtimeDataPath ".requirements.sha256"
$niceGuiMain = Join-Path $projectRoot "nicegui_app\main.py"
$environmentCreated = $false

function Test-VenvPython {
    param([Parameter(Mandatory = $true)][string]$PythonPath)

    if (-not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) {
        return $false
    }

    try {
        & $PythonPath -c "import sys; raise SystemExit(0 if sys.version_info.major == 3 else 1)" 2>$null
        return $LASTEXITCODE -eq 0
    }
    catch {
        return $false
    }
}

if (-not (Test-VenvPython -PythonPath $venvPython)) {
    if (Test-Path -LiteralPath $venvPath) {
        $resolvedProject = (Resolve-Path -LiteralPath $projectRoot).Path.TrimEnd('\')
        $resolvedVenv = (Resolve-Path -LiteralPath $venvPath).Path.TrimEnd('\')
        $expectedVenv = Join-Path $resolvedProject ".venv"
        if (-not $resolvedVenv.Equals($expectedVenv, [System.StringComparison]::OrdinalIgnoreCase)) {
            throw "Refusing to remove an unexpected virtual-environment path: $resolvedVenv"
        }
        Write-Host "Removing broken shared virtual environment..."
        Remove-Item -LiteralPath $resolvedVenv -Recurse -Force
    }

    Write-Host "Creating shared virtual environment..."
    try {
        & py -3 -m venv $venvPath
    }
    catch {
        throw "Virtual environment creation failed. NiceGUI was not started. $($_.Exception.Message)"
    }
    if ($LASTEXITCODE -ne 0 -or -not (Test-VenvPython -PythonPath $venvPython)) {
        throw "Virtual environment creation or Python validation failed. NiceGUI was not started."
    }
    $environmentCreated = $true
}

if (-not (Test-Path -LiteralPath $requirementsPath -PathType Leaf)) {
    throw "requirements.txt was not found. NiceGUI was not started."
}
if (-not (Test-Path -LiteralPath $niceGuiMain -PathType Leaf)) {
    throw "nicegui_app\main.py was not found. NiceGUI was not started."
}

try {
    $requirementsHash = (Get-FileHash -LiteralPath $requirementsPath -Algorithm SHA256 -ErrorAction Stop).Hash.ToLowerInvariant()
}
catch {
    throw "Unable to read or hash requirements.txt. NiceGUI was not started. $($_.Exception.Message)"
}

$installedHash = ""
if (Test-Path -LiteralPath $requirementsFingerprintPath -PathType Leaf) {
    try {
        $installedHash = (Get-Content -Raw -LiteralPath $requirementsFingerprintPath -ErrorAction Stop).Trim().ToLowerInvariant()
    }
    catch {
        throw "Unable to read the dependency fingerprint. NiceGUI was not started. $($_.Exception.Message)"
    }
}

$dependenciesNeedInstall = $environmentCreated -or ($installedHash -ne $requirementsHash)
if ($dependenciesNeedInstall) {
    Write-Host "Installing/updating shared dependencies..."
    & $venvPython -m pip install -r $requirementsPath
    $pipExitCode = $LASTEXITCODE
    if ($pipExitCode -ne 0) {
        throw "Dependency installation failed with exit code $pipExitCode. The dependency fingerprint was not updated, and NiceGUI was not started."
    }

    New-Item -ItemType Directory -Path $runtimeDataPath -Force | Out-Null
    Set-Content -LiteralPath $requirementsFingerprintPath -Value $requirementsHash -NoNewline -Encoding ascii
}
else {
    Write-Host "Dependencies unchanged; skipping installation."
}

if (-not (Test-VenvPython -PythonPath $venvPython)) {
    throw "Shared virtual-environment Python failed its final validation. NiceGUI was not started."
}

Write-Host "Starting NiceGUI Portfolio Tracker on http://localhost:8502..."
& $venvPython $niceGuiMain
if ($LASTEXITCODE -ne 0) {
    throw "NiceGUI exited with code $LASTEXITCODE."
}
