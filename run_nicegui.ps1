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

function Test-PythonCommand {
    param(
        [Parameter(Mandatory = $true)][string]$PythonPath,
        [string[]]$PrefixArguments = @()
    )

    if (-not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) {
        return $false
    }

    try {
        & $PythonPath @PrefixArguments -c "import sys; raise SystemExit(0 if sys.version_info.major == 3 else 1)" 2>$null
        return $LASTEXITCODE -eq 0
    }
    catch {
        return $false
    }
}

function Find-BasePython {
    param(
        [Parameter(Mandatory = $true)][string]$VirtualEnvironmentPath,
        [Parameter(Mandatory = $true)][string]$ConfigurationPath
    )

    $candidates = [System.Collections.Generic.List[object]]::new()
    $seen = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::OrdinalIgnoreCase)

    function Add-PythonCandidate {
        param([string]$Path, [string[]]$Arguments = @(), [string]$Source)
        if ([string]::IsNullOrWhiteSpace($Path)) {
            return
        }
        $key = "$Path|$($Arguments -join ' ')"
        if ($seen.Add($key)) {
            $candidates.Add([pscustomobject]@{ Path = $Path; Arguments = $Arguments; Source = $Source })
        }
    }

    # Preserve the original base-interpreter hint before a broken environment is removed.
    if (Test-Path -LiteralPath $ConfigurationPath -PathType Leaf) {
        try {
            $configuration = Get-Content -LiteralPath $ConfigurationPath -ErrorAction Stop
            $homeLine = $configuration | Where-Object { $_ -match '^\s*home\s*=\s*(.+?)\s*$' } | Select-Object -First 1
            if ($homeLine -match '^\s*home\s*=\s*(.+?)\s*$') {
                $baseHome = $Matches[1].Trim()
                foreach ($name in @('python.exe', 'python3.exe')) {
                    Add-PythonCandidate -Path (Join-Path $baseHome $name) -Source 'pyvenv.cfg'
                }
            }
        }
        catch {
            Write-Warning "Unable to inspect .venv\pyvenv.cfg: $($_.Exception.Message)"
        }
    }

    $resolvedVenvPrefix = [System.IO.Path]::GetFullPath($VirtualEnvironmentPath).TrimEnd('\') + '\'
    foreach ($commandName in @('python.exe', 'python3.exe')) {
        foreach ($command in @(Get-Command $commandName -All -CommandType Application -ErrorAction SilentlyContinue)) {
            $candidatePath = [System.IO.Path]::GetFullPath($command.Source)
            if (-not $candidatePath.StartsWith($resolvedVenvPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
                Add-PythonCandidate -Path $candidatePath -Source $commandName
            }
        }
    }

    $pyLauncher = Get-Command py.exe -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($null -ne $pyLauncher) {
        Add-PythonCandidate -Path $pyLauncher.Source -Arguments @('-3') -Source 'py -3'
    }

    foreach ($candidate in $candidates) {
        if (Test-PythonCommand -PythonPath $candidate.Path -PrefixArguments $candidate.Arguments) {
            return $candidate
        }
    }
    return $null
}

if (-not (Test-PythonCommand -PythonPath $venvPython)) {
    $pyvenvConfiguration = Join-Path $venvPath "pyvenv.cfg"
    $basePython = Find-BasePython -VirtualEnvironmentPath $venvPath -ConfigurationPath $pyvenvConfiguration
    if ($null -eq $basePython) {
        throw "The shared .venv is missing or broken, and no usable base Python interpreter was found. Install or repair Python 3 and ensure python.exe, python3.exe, or the optional py launcher is available. NiceGUI was not started."
    }

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
    Write-Host "Using Python from $($basePython.Source): $($basePython.Path)"
    try {
        & $basePython.Path @($basePython.Arguments) -m venv $venvPath
    }
    catch {
        throw "Virtual environment creation failed. NiceGUI was not started. $($_.Exception.Message)"
    }
    if ($LASTEXITCODE -ne 0 -or -not (Test-PythonCommand -PythonPath $venvPython)) {
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

if (-not (Test-PythonCommand -PythonPath $venvPython)) {
    throw "Shared virtual-environment Python failed its final validation. NiceGUI was not started."
}

$existingListener = Get-NetTCPConnection -LocalPort 8502 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
if ($null -ne $existingListener) {
    $listenerDescription = "process ID $($existingListener.OwningProcess)"
    try {
        $commandLine = (Get-CimInstance Win32_Process -Filter "ProcessId = $($existingListener.OwningProcess)" -ErrorAction Stop).CommandLine
        if ($commandLine -like '*nicegui_app*main.py*') {
            $listenerDescription = "an existing Portfolio Tracker NiceGUI process (PID $($existingListener.OwningProcess))"
        }
    }
    catch {
        # The owner may not be inspectable; never kill or adopt an unknown process.
    }
    throw "Port 8502 is already in use by $listenerDescription. NiceGUI was not started. Close the existing listener and try again."
}

Write-Host "Starting NiceGUI Portfolio Tracker on http://localhost:8502..."
$env:NICEGUI_LAUNCHER_PID = [string]$PID
$shutdownToken = [Guid]::NewGuid().ToString("N")
$shutdownRequestPath = Join-Path ([System.IO.Path]::GetTempPath()) "portfolio-tracker-stop-$shutdownToken.request"
$shutdownWaitPath = Join-Path ([System.IO.Path]::GetTempPath()) "portfolio-tracker-stop-$shutdownToken.waiting"
$env:NICEGUI_SHUTDOWN_REQUEST_FILE = $shutdownRequestPath
$env:NICEGUI_SHUTDOWN_WAIT_FILE = $shutdownWaitPath
$gracefulShutdownTimeoutSeconds = 15
$intentionalShutdown = $false
$forcedShutdown = $false
try {
    $quotedNiceGuiMain = '"' + $niceGuiMain + '"'
    $child = Start-Process -FilePath $venvPython -ArgumentList @($quotedNiceGuiMain) -WorkingDirectory $projectRoot -NoNewWindow -PassThru
    $oldTreatControlCAsInput = [Console]::TreatControlCAsInput
    [Console]::TreatControlCAsInput = $true
    try {
        while (-not $child.HasExited) {
            if ([Console]::KeyAvailable) {
                $key = [Console]::ReadKey($true)
                if (($key.Modifiers -band [ConsoleModifiers]::Control) -and $key.Key -eq [ConsoleKey]::C) {
                    $intentionalShutdown = $true
                    Write-Host "Stopping Portfolio Tracker safely..."
                    New-Item -ItemType File -Path $shutdownRequestPath -Force | Out-Null
                    $deadline = [DateTime]::UtcNow.AddSeconds($gracefulShutdownTimeoutSeconds)
                    $waitingReported = $false
                    while (-not $child.HasExited -and [DateTime]::UtcNow -lt $deadline) {
                        if (-not $waitingReported -and (Test-Path -LiteralPath $shutdownWaitPath -PathType Leaf)) {
                            Write-Host "Waiting for active operations to finish..."
                            $waitingReported = $true
                        }
                        Start-Sleep -Milliseconds 100
                        $child.Refresh()
                    }
                    if (-not $child.HasExited) {
                        Write-Host "Graceful shutdown timed out after $gracefulShutdownTimeoutSeconds seconds."
                        Write-Host "Forcing Portfolio Tracker to stop..."
                        & taskkill.exe /PID $child.Id /T /F | Out-Null
                        $forcedShutdown = $true
                        Write-Host "Portfolio Tracker was force-stopped."
                    }
                    else {
                        Write-Host "Portfolio Tracker stopped safely."
                    }
                    break
                }
            }
            Start-Sleep -Milliseconds 100
            $child.Refresh()
        }
    }
    finally {
        [Console]::TreatControlCAsInput = $oldTreatControlCAsInput
    }
    if ($forcedShutdown) {
        exit 2
    }
    if (-not $intentionalShutdown -and $child.ExitCode -ne 0) {
        throw "NiceGUI exited with code $($child.ExitCode)."
    }
}
finally {
    Remove-Item Env:NICEGUI_LAUNCHER_PID -ErrorAction SilentlyContinue
    Remove-Item Env:NICEGUI_SHUTDOWN_REQUEST_FILE -ErrorAction SilentlyContinue
    Remove-Item Env:NICEGUI_SHUTDOWN_WAIT_FILE -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $shutdownRequestPath -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $shutdownWaitPath -Force -ErrorAction SilentlyContinue
}
