$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $projectRoot

$localAppData = [Environment]::GetFolderPath([Environment+SpecialFolder]::LocalApplicationData)
if ([string]::IsNullOrWhiteSpace($localAppData)) {
    throw "Unable to locate the current user's local application data folder. Streamlit rollback was not started."
}
$resolvedProjectRoot = [System.IO.Path]::GetFullPath($projectRoot).TrimEnd('\')
$pathHasher = [System.Security.Cryptography.SHA256]::Create()
try {
    $projectHash = [BitConverter]::ToString(
        $pathHasher.ComputeHash([Text.Encoding]::UTF8.GetBytes($resolvedProjectRoot.ToUpperInvariant()))
    ).Replace("-", "").ToLowerInvariant().Substring(0, 16)
}
finally {
    $pathHasher.Dispose()
}
$venvRoot = Join-Path $localAppData "PortfolioTracker\venvs"
$environmentName = "portfolio-tracker-$projectHash"
$venvPath = Join-Path $venvRoot $environmentName
$venvPython = Join-Path $venvPath "Scripts\python.exe"
$requirementsPath = Join-Path $projectRoot "requirements.txt"
$requirementsLockPath = Join-Path $projectRoot "requirements-lock.txt"
$runtimeDataPath = Join-Path $projectRoot "data"
$requirementsFingerprintPath = Join-Path $runtimeDataPath ".requirements.sha256"
$rollbackPort = 8502
$rollbackUrl = "http://localhost:$rollbackPort"
$environmentCreated = $false
$useUvEnvironment = $false
$uvPythonSelector = "3.12"

function Test-PythonCommand {
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

function Test-ProjectDependencies {
    param(
        [Parameter(Mandatory = $true)][string]$PythonPath,
        [Parameter(Mandatory = $true)][string]$RequirementsFile,
        [Parameter(Mandatory = $true)][string]$LockFile
    )

    try {
        & $PythonPath -m dependency_validation --requirements $RequirementsFile --lock $LockFile
        return $LASTEXITCODE -eq 0
    }
    catch {
        return $false
    }
}

if (-not (Test-PythonCommand -PythonPath $venvPython)) {
    $uvCommand = Get-Command uv.exe -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($null -eq $uvCommand) {
        throw "The machine-local Python environment is missing or broken and uv.exe is unavailable. Repair Python or run the NiceGUI launcher once to build the environment. Streamlit rollback was not started."
    }
    Write-Host "Shared virtual environment is unavailable; using an isolated uv rollback environment."
    $managedPython = Get-ChildItem -LiteralPath $projectRoot -Directory -Force -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -like ".uv-python*" } |
        ForEach-Object { Get-ChildItem -LiteralPath $_.FullName -Filter "python.exe" -Recurse -Force -ErrorAction SilentlyContinue } |
        Where-Object { Test-PythonCommand -PythonPath $_.FullName } |
        Select-Object -First 1
    if ($null -ne $managedPython) {
        $uvPythonSelector = $managedPython.FullName
    }
    $useUvEnvironment = $true
}

if (-not (Test-Path -LiteralPath $requirementsPath -PathType Leaf)) {
    throw "requirements.txt was not found. Portfolio Tracker was not started."
}
if (-not (Test-Path -LiteralPath $requirementsLockPath -PathType Leaf)) {
    throw "requirements-lock.txt was not found. Portfolio Tracker was not started."
}

if (-not $useUvEnvironment) {
    & $venvPython -m pip --version *> $null
    if ($LASTEXITCODE -ne 0) {
        & $venvPython -m ensurepip --upgrade
        if ($LASTEXITCODE -ne 0) {
            throw "pip is unavailable in the machine-local environment and ensurepip could not repair it. Streamlit rollback was not started."
        }
    }
}

try {
    $requirementsFileHash = (Get-FileHash -LiteralPath $requirementsPath -Algorithm SHA256 -ErrorAction Stop).Hash.ToLowerInvariant()
    $requirementsLockHash = (Get-FileHash -LiteralPath $requirementsLockPath -Algorithm SHA256 -ErrorAction Stop).Hash.ToLowerInvariant()
    $hashPayload = [Text.Encoding]::UTF8.GetBytes("$requirementsFileHash`n$requirementsLockHash")
    $fingerprintHasher = [System.Security.Cryptography.SHA256]::Create()
    try {
        $requirementsHash = [BitConverter]::ToString($fingerprintHasher.ComputeHash($hashPayload)).Replace("-", "").ToLowerInvariant()
    }
    finally {
        $fingerprintHasher.Dispose()
    }
}
catch {
    throw "Unable to read or hash requirements.txt and requirements-lock.txt. Portfolio Tracker was not started. $($_.Exception.Message)"
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

$dependenciesNeedInstall = -not $useUvEnvironment -and (
    $environmentCreated -or
    ($installedHash -ne $requirementsHash) -or
    -not (Test-ProjectDependencies -PythonPath $venvPython -RequirementsFile $requirementsPath -LockFile $requirementsLockPath)
)
if ($dependenciesNeedInstall) {
    Write-Host "Installing/reconciling machine-local dependencies from the tested lock..."
    & $venvPython -m pip install --disable-pip-version-check -r $requirementsPath -r $requirementsLockPath
    $pipExitCode = $LASTEXITCODE
    if ($pipExitCode -ne 0) {
        throw "Dependency installation failed with exit code $pipExitCode. The dependency fingerprint was not updated, and Portfolio Tracker was not started."
    }
    if (-not (Test-ProjectDependencies -PythonPath $venvPython -RequirementsFile $requirementsPath -LockFile $requirementsLockPath)) {
        throw "Dependency validation failed after installation. The dependency fingerprint was not updated, and Portfolio Tracker was not started."
    }

    New-Item -ItemType Directory -Path $runtimeDataPath -Force | Out-Null
    Set-Content -LiteralPath $requirementsFingerprintPath -Value $requirementsHash -NoNewline -Encoding ascii
}
else {
    if ($useUvEnvironment) {
        Write-Host "Dependencies will be validated by uv using the tested requirements lock."
    }
    else {
        Write-Host "Dependencies unchanged; skipping installation."
    }
}

$existingListener = Get-NetTCPConnection -LocalPort $rollbackPort -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
if ($null -ne $existingListener) {
    throw "Rollback port $rollbackPort is already in use by process ID $($existingListener.OwningProcess). Streamlit rollback was not started. Close that listener and try again."
}

Write-Host "Starting temporary Streamlit rollback on $rollbackUrl..."
$env:STREAMLIT_SERVER_PORT = [string]$rollbackPort
$env:STREAMLIT_BROWSER_GATHER_USAGE_STATS = "false"
try {
    if ($useUvEnvironment) {
        $env:UV_CACHE_DIR = Join-Path $projectRoot ".uv-cache"
        $env:UV_PYTHON_INSTALL_DIR = Join-Path $projectRoot ".uv-python-ai"
        & $uvCommand.Source run --python $uvPythonSelector --with pip --with-requirements $requirementsPath --with-requirements $requirementsLockPath python -m dependency_validation --requirements $requirementsPath --lock $requirementsLockPath
        if ($LASTEXITCODE -ne 0) {
            throw "Dependency validation failed in the isolated uv environment with exit code $LASTEXITCODE. Streamlit rollback was not started."
        }
        & $uvCommand.Source run --python $uvPythonSelector --with pip --with-requirements $requirementsPath --with-requirements $requirementsLockPath python (Join-Path $projectRoot "start_streamlit.py")
        if ($LASTEXITCODE -ne 0) {
            throw "The isolated uv Streamlit process exited with code $LASTEXITCODE. Streamlit rollback failed."
        }
    }
    else {
        & $venvPython (Join-Path $projectRoot "start_streamlit.py")
    }
}
finally {
    Remove-Item Env:STREAMLIT_SERVER_PORT -ErrorAction SilentlyContinue
    Remove-Item Env:STREAMLIT_BROWSER_GATHER_USAGE_STATS -ErrorAction SilentlyContinue
    if ($useUvEnvironment) {
        Remove-Item Env:UV_CACHE_DIR -ErrorAction SilentlyContinue
        Remove-Item Env:UV_PYTHON_INSTALL_DIR -ErrorAction SilentlyContinue
    }
}
