$ErrorActionPreference = "Stop"
$script:applicationReady = $false
$script:trapExitCode = 1

trap {
    if ($script:trapExitCode -eq 3) {
        Write-Host "Portfolio Tracker could not confirm termination of the launched process tree."
    }
    elseif ($script:applicationReady) {
        Write-Host "Portfolio Tracker exited unexpectedly. $($_.Exception.Message)"
    }
    else {
        Write-Host "Portfolio Tracker failed to start. $($_.Exception.Message)"
    }
    exit $script:trapExitCode
}

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $projectRoot

function ConvertTo-CreationTimeUtcTicks {
    param([Parameter(Mandatory = $true)]$CreationDate)

    if ($CreationDate -is [DateTime]) {
        return [long]$CreationDate.ToUniversalTime().Ticks
    }
    return [long][System.Management.ManagementDateTimeConverter]::ToDateTime(
        [string]$CreationDate
    ).ToUniversalTime().Ticks
}

function Get-TrackedProcessTreeIds {
    param(
        [Parameter(Mandatory = $true)][int]$RootProcessId,
        [Parameter(Mandatory = $true)][long]$RootCreationTimeUtcTicks
    )

    $processes = @(Get-CimInstance -ClassName Win32_Process -ErrorAction Stop)
    $byId = @{}
    foreach ($process in $processes) {
        $processId = [int]$process.ProcessId
        $creationTicks = ConvertTo-CreationTimeUtcTicks -CreationDate $process.CreationDate
        $byId[$processId] = [pscustomobject]@{
            ProcessId = $processId
            ParentProcessId = [int]$process.ParentProcessId
            CreationTimeUtcTicks = [long]$creationTicks
        }
    }

    if (-not $byId.ContainsKey($RootProcessId) -or
        $byId[$RootProcessId].CreationTimeUtcTicks -ne $RootCreationTimeUtcTicks) {
        return @()
    }

    $tracked = @{}
    $rootIdentity = $byId[$RootProcessId]
    $tracked["$RootProcessId`:$RootCreationTimeUtcTicks"] = $rootIdentity
    $changed = $true
    while ($changed) {
        $changed = $false
        foreach ($identity in $byId.Values) {
            $parent = $byId[[int]$identity.ParentProcessId]
            if ($null -eq $parent -or $parent.CreationTimeUtcTicks -lt $RootCreationTimeUtcTicks -or
                $identity.CreationTimeUtcTicks -lt $parent.CreationTimeUtcTicks) {
                continue
            }
            $parentKey = "$($parent.ProcessId):$($parent.CreationTimeUtcTicks)"
            $identityKey = "$($identity.ProcessId):$($identity.CreationTimeUtcTicks)"
            if ($tracked.ContainsKey($parentKey) -and -not $tracked.ContainsKey($identityKey)) {
                $tracked[$identityKey] = $identity
                $changed = $true
            }
        }
    }
    return @($tracked.Values)
}

function Get-ProcessIdentity {
    param([Parameter(Mandatory = $true)][int]$ProcessId)

    $process = Get-CimInstance -ClassName Win32_Process -Filter "ProcessId = $ProcessId" -ErrorAction Stop
    if ($null -eq $process) {
        throw "Unable to capture the launched process identity."
    }
    return [pscustomobject]@{
        ProcessId = [int]$process.ProcessId
        CreationTimeUtcTicks = ConvertTo-CreationTimeUtcTicks -CreationDate $process.CreationDate
    }
}

function Merge-TrackedProcessTreeIds {
    param(
        [AllowEmptyCollection()][object[]]$ExistingIds = @(),
        [AllowEmptyCollection()][object[]]$CurrentIds = @()
    )

    $tracked = @{}
    foreach ($identity in @($ExistingIds) + @($CurrentIds)) {
        if ($null -ne $identity) {
            $key = "$([int]$identity.ProcessId):$([long]$identity.CreationTimeUtcTicks)"
            $tracked[$key] = $identity
        }
    }
    return @($tracked.Values)
}

function Get-RunningTrackedProcessIds {
    param([Parameter(Mandatory = $true)][AllowEmptyCollection()][object[]]$ProcessIds)

    $processes = @(Get-CimInstance -ClassName Win32_Process -ErrorAction Stop)
    $currentById = @{}
    foreach ($process in $processes) {
        $processId = [int]$process.ProcessId
        $currentById[$processId] = ConvertTo-CreationTimeUtcTicks -CreationDate $process.CreationDate
    }
    $running = [System.Collections.Generic.List[object]]::new()
    foreach ($identity in $ProcessIds) {
        $processId = [int]$identity.ProcessId
        if ($currentById.ContainsKey($processId) -and
            $currentById[$processId] -eq [long]$identity.CreationTimeUtcTicks) {
            $running.Add($identity)
        }
    }
    return @($running)
}

function Get-IntentionalShutdownExitCode {
    param(
        [Parameter(Mandatory = $true)]$ApplicationStatus,
        [Parameter(Mandatory = $true)][int]$ServerExitCode,
        [Parameter(Mandatory = $true)][bool]$ForcedTreeTermination,
        [Parameter(Mandatory = $true)][bool]$TreeTerminationConfirmed
    )

    if ($ApplicationStatus.outcome -eq "failed") {
        return 1
    }
    if (-not $TreeTerminationConfirmed) {
        return 3
    }
    if ($ForcedTreeTermination) {
        return 2
    }
    if ($ServerExitCode -ne 0) {
        return 1
    }
    if ($ApplicationStatus.outcome -in @("unconfirmed", "missing", "invalid")) {
        return 3
    }
    if ($ApplicationStatus.outcome -eq "forced") {
        return 2
    }
    if ($ApplicationStatus.outcome -eq "graceful") {
        return 0
    }
    return 3
}

function Get-StartupCleanupExitCode {
    param([Parameter(Mandatory = $true)][bool]$TreeTerminationConfirmed)

    if ($TreeTerminationConfirmed) {
        return 1
    }
    return 3
}

function Read-ApplicationShutdownStatus {
    param([Parameter(Mandatory = $true)][string]$Path)

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        return [pscustomobject]@{ outcome = "missing"; workers = @{} }
    }
    try {
        $status = Get-Content -Raw -LiteralPath $Path -ErrorAction Stop | ConvertFrom-Json -ErrorAction Stop
        if ($status.schema_version -ne 1 -or
            $status.outcome -notin @("graceful", "forced", "unconfirmed", "failed")) {
            return [pscustomobject]@{ outcome = "invalid"; workers = @{} }
        }
        return $status
    }
    catch {
        Write-Host "Unable to read the application shutdown status file."
        return [pscustomobject]@{ outcome = "invalid"; workers = @{} }
    }
}

function Stop-TrackedProcessTree {
    param(
        [Parameter(Mandatory = $true)][int]$RootProcessId,
        [Parameter(Mandatory = $true)][long]$RootCreationTimeUtcTicks,
        [Parameter(Mandatory = $true)][int]$ConfirmationTimeoutSeconds,
        [object[]]$KnownProcessIds = @(),
        [switch]$RequireObservedDescendantWhenRootGone
    )

    $snapshotSucceeded = $true
    $rootIdentity = [pscustomobject]@{
        ProcessId = $RootProcessId
        CreationTimeUtcTicks = $RootCreationTimeUtcTicks
    }
    $trackedIds = @(Merge-TrackedProcessTreeIds -ExistingIds @($rootIdentity) -CurrentIds $KnownProcessIds)
    try {
        $currentIds = @(Get-TrackedProcessTreeIds -RootProcessId $RootProcessId -RootCreationTimeUtcTicks $RootCreationTimeUtcTicks)
        $trackedIds = @(Merge-TrackedProcessTreeIds -ExistingIds $trackedIds -CurrentIds $currentIds)
    }
    catch {
        $snapshotSucceeded = $false
        Write-Host "Unable to snapshot the launched process tree: $($_.Exception.Message)"
    }

    $taskkillSucceeded = $true
    $taskkillErrors = [System.Collections.Generic.List[string]]::new()
    try {
        $runningIds = @(Get-RunningTrackedProcessIds -ProcessIds $trackedIds)
    }
    catch {
        $snapshotSucceeded = $false
        $taskkillSucceeded = $false
        $taskkillErrors.Add("Unable to verify tracked process identities: $($_.Exception.Message)")
        $runningIds = @()
    }
    if ($RequireObservedDescendantWhenRootGone -and
        -not ($runningIds | Where-Object { $_.ProcessId -eq $RootProcessId }) -and
        $trackedIds.Count -le 1) {
        $snapshotSucceeded = $false
        $taskkillSucceeded = $false
        $taskkillErrors.Add("The root exited before any descendant process identity was observed.")
    }
    if ($runningIds.Count -gt 0) {
        $targets = @($runningIds | Sort-Object CreationTimeUtcTicks -Descending)
        foreach ($identity in $targets) {
            $processId = [int]$identity.ProcessId
            try {
                $stillOwned = @(Get-RunningTrackedProcessIds -ProcessIds @($identity))
            }
            catch {
                $taskkillSucceeded = $false
                $taskkillErrors.Add("Unable to revalidate PID $processId before termination: $($_.Exception.Message)")
                continue
            }
            if ($stillOwned.Count -eq 0) {
                continue
            }
            try {
                $null = & taskkill.exe /PID $processId /F 2>&1
                $commandExitCode = $LASTEXITCODE
            }
            catch {
                $commandExitCode = -1
                $taskkillErrors.Add("taskkill for PID $processId failed: $($_.Exception.Message)")
            }
            if ($commandExitCode -ne 0) {
                $taskkillSucceeded = $false
                if ($commandExitCode -ne -1) {
                    $taskkillErrors.Add("taskkill for PID $processId returned $commandExitCode")
                }
            }
        }
    }

    $deadline = [DateTime]::UtcNow.AddSeconds($ConfirmationTimeoutSeconds)
    do {
        try {
            $runningIds = @(Get-RunningTrackedProcessIds -ProcessIds $trackedIds)
        }
        catch {
            $snapshotSucceeded = $false
            $runningIds = @($trackedIds)
            $taskkillErrors.Add("Unable to confirm tracked process exit: $($_.Exception.Message)")
        }
        if ($runningIds.Count -eq 0) {
            break
        }
        Start-Sleep -Milliseconds 100
    } while ([DateTime]::UtcNow -lt $deadline)

    return [pscustomobject]@{
        SnapshotSucceeded = $snapshotSucceeded
        TaskkillSucceeded = $taskkillSucceeded
        TaskkillErrors = @($taskkillErrors)
        RunningProcessIds = @($runningIds | ForEach-Object { $_.ProcessId })
        Terminated = $snapshotSucceeded -and $taskkillSucceeded -and $runningIds.Count -eq 0
    }
}

$localAppData = [Environment]::GetFolderPath([Environment+SpecialFolder]::LocalApplicationData)
if ([string]::IsNullOrWhiteSpace($localAppData)) {
    throw "Unable to locate the current user's local application data folder. NiceGUI was not started."
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
$niceGuiMain = Join-Path $projectRoot "nicegui_app\main.py"
$productionPort = 8501
$productionUrl = "http://localhost:$productionPort"
$readinessUrl = "http://127.0.0.1:$productionPort/healthz"
$environmentCreated = $false

# Do not inspect, repair, or install packages while the production server may be
# using this shared machine-local environment.
$existingListener = Get-NetTCPConnection -LocalPort $productionPort -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
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
    throw "Port $productionPort is already in use by $listenerDescription. Portfolio Tracker was not started. Close the existing listener or choose the explicitly named Streamlit rollback launcher."
}

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

function Test-BasePythonCommand {
    param(
        [Parameter(Mandatory = $true)][string]$PythonPath,
        [string[]]$PrefixArguments = @()
    )

    if (-not (Test-PythonCommand -PythonPath $PythonPath -PrefixArguments $PrefixArguments)) {
        return $false
    }
    try {
        & $PythonPath @PrefixArguments -m venv --help *> $null
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

function Find-BasePython {
    param(
        [Parameter(Mandatory = $true)][string]$VirtualEnvironmentPath,
        [Parameter(Mandatory = $true)][string]$ConfigurationPath
    )

    $candidates = [System.Collections.Generic.List[object]]::new()
    $seen = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::OrdinalIgnoreCase)
    $script:BasePythonDiagnostics = [System.Collections.Generic.List[string]]::new()

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

    # Preserve the original hint before repair, but do not trust it exclusively:
    # Microsoft Store package upgrades can leave pyvenv.cfg pointing at a path
    # that no longer exists. Registry, standard-install, PATH, and py candidates
    # are all capability-tested for Python 3 plus working venv support.
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

    foreach ($registryRoot in @(
        'HKCU:\Software\Python\PythonCore',
        'HKLM:\Software\Python\PythonCore',
        'HKLM:\Software\WOW6432Node\Python\PythonCore'
    )) {
        foreach ($versionKey in @(Get-ChildItem -LiteralPath $registryRoot -ErrorAction SilentlyContinue)) {
            $installKey = Join-Path $versionKey.PSPath 'InstallPath'
            try {
                $install = Get-ItemProperty -LiteralPath $installKey -ErrorAction Stop
                Add-PythonCandidate -Path $install.ExecutablePath -Source "Python registry $($versionKey.PSChildName)"
                $installRoot = $install.'(default)'
                foreach ($name in @('python.exe', 'python3.exe', "python$($versionKey.PSChildName).exe")) {
                    if (-not [string]::IsNullOrWhiteSpace($installRoot)) {
                        Add-PythonCandidate -Path (Join-Path $installRoot $name) -Source "Python registry $($versionKey.PSChildName)"
                    }
                }
            }
            catch {
                $script:BasePythonDiagnostics.Add("Unreadable registry candidate $installKey")
            }
        }
    }

    foreach ($root in @(
        (Join-Path $env:LOCALAPPDATA 'Programs\Python'),
        (Join-Path $env:ProgramFiles 'Python')
    )) {
        foreach ($directory in @(Get-ChildItem -LiteralPath $root -Directory -ErrorAction SilentlyContinue | Sort-Object Name -Descending)) {
            foreach ($name in @('python.exe', 'python3.exe')) {
                Add-PythonCandidate -Path (Join-Path $directory.FullName $name) -Source "standard install $($directory.FullName)"
            }
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
        if (Test-BasePythonCommand -PythonPath $candidate.Path -PrefixArguments $candidate.Arguments) {
            return $candidate
        }
        $script:BasePythonDiagnostics.Add("Rejected $($candidate.Source): $($candidate.Path) $($candidate.Arguments -join ' ')")
    }
    return $null
}

if (-not (Test-PythonCommand -PythonPath $venvPython)) {
    $pyvenvConfiguration = Join-Path $venvPath "pyvenv.cfg"
    $basePython = Find-BasePython -VirtualEnvironmentPath $venvPath -ConfigurationPath $pyvenvConfiguration
    if ($null -eq $basePython) {
        $candidateSummary = if ($script:BasePythonDiagnostics.Count) { $script:BasePythonDiagnostics -join '; ' } else { 'No candidates were discovered.' }
        $wingetCommand = Get-Command winget.exe -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($null -eq $wingetCommand) {
            throw "No usable Python 3 interpreter with venv support was found, and Windows Package Manager (winget) is unavailable. Candidates: $candidateSummary Install Python 3.12 and retry. NiceGUI was not started."
        }

        Write-Host "No usable Python 3 installation found; installing Python 3.12 for the current Windows user..."
        & $wingetCommand.Source install --id Python.Python.3.12 --exact --scope user --silent --accept-package-agreements --accept-source-agreements
        $installExitCode = $LASTEXITCODE
        if ($installExitCode -ne 0) {
            throw "Python installation through winget failed with exit code $installExitCode. Candidates: $candidateSummary Install Python 3.12 and retry. NiceGUI was not started."
        }

        $basePython = Find-BasePython -VirtualEnvironmentPath $venvPath -ConfigurationPath $pyvenvConfiguration
        if ($null -eq $basePython) {
            $candidateSummary = if ($script:BasePythonDiagnostics.Count) { $script:BasePythonDiagnostics -join '; ' } else { 'No candidates were discovered.' }
            throw "Python 3.12 installation completed, but no usable interpreter with venv support was detected. Candidates: $candidateSummary NiceGUI was not started."
        }
    }

    if (Test-Path -LiteralPath $venvPath) {
        $resolvedVenv = (Resolve-Path -LiteralPath $venvPath).Path.TrimEnd('\')
        $expectedVenv = [System.IO.Path]::GetFullPath((Join-Path $venvRoot $environmentName)).TrimEnd('\')
        if (-not $resolvedVenv.Equals($expectedVenv, [System.StringComparison]::OrdinalIgnoreCase)) {
            throw "Refusing to remove an unexpected virtual-environment path: $resolvedVenv"
        }
        Write-Host "Removing broken machine-local virtual environment..."
        Remove-Item -LiteralPath $resolvedVenv -Recurse -Force
    }

    Write-Host "Creating machine-local virtual environment..."
    Write-Host "Using Python from $($basePython.Source): $($basePython.Path)"
    try {
        & $basePython.Path @($basePython.Arguments) -m venv $venvPath
        $venvExitCode = $LASTEXITCODE
    }
    catch {
        $venvExitCode = 1
        $venvFailure = $_.Exception.Message
    }
    if ($venvExitCode -ne 0 -or -not (Test-PythonCommand -PythonPath $venvPython)) {
        try {
            if (Test-Path -LiteralPath $venvPath) {
                Remove-Item -LiteralPath $venvPath -Recurse -Force
            }
        }
        catch {
            throw "Virtual environment creation failed using $($basePython.Path), and the partial .venv could not be removed because it is in use or inaccessible. Close Portfolio Tracker processes and retry. $($_.Exception.Message)"
        }
        throw "Virtual environment creation or validation failed using $($basePython.Path). NiceGUI was not started. $venvFailure"
    }
    $environmentCreated = $true
}

& $venvPython -m pip --version *> $null
if ($LASTEXITCODE -ne 0) {
    & $venvPython -m ensurepip --upgrade
    if ($LASTEXITCODE -ne 0) {
        throw "pip is unavailable in the machine-local environment and ensurepip could not repair it. NiceGUI was not started."
    }
}

if (-not (Test-Path -LiteralPath $requirementsPath -PathType Leaf)) {
    throw "requirements.txt was not found. NiceGUI was not started."
}
if (-not (Test-Path -LiteralPath $requirementsLockPath -PathType Leaf)) {
    throw "requirements-lock.txt was not found. NiceGUI was not started."
}
if (-not (Test-Path -LiteralPath $niceGuiMain -PathType Leaf)) {
    throw "nicegui_app\main.py was not found. NiceGUI was not started."
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
    throw "Unable to read or hash requirements.txt and requirements-lock.txt. NiceGUI was not started. $($_.Exception.Message)"
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

# The fingerprint avoids routine reinstalls, but it is not proof that the shared
# environment is healthy. A recreated environment or failed core-import probe
# forces installation, and the hash is written only after validation succeeds.
$dependenciesNeedInstall = $environmentCreated -or ($installedHash -ne $requirementsHash) -or -not (
    Test-ProjectDependencies -PythonPath $venvPython -RequirementsFile $requirementsPath -LockFile $requirementsLockPath
)
if ($dependenciesNeedInstall) {
    Write-Host "Installing/reconciling machine-local dependencies from the tested lock..."
    & $venvPython -m pip install --disable-pip-version-check -r $requirementsPath -r $requirementsLockPath
    $pipExitCode = $LASTEXITCODE
    if ($pipExitCode -ne 0) {
        throw "Dependency installation failed with exit code $pipExitCode. The dependency fingerprint was not updated, and NiceGUI was not started."
    }
    if (-not (Test-ProjectDependencies -PythonPath $venvPython -RequirementsFile $requirementsPath -LockFile $requirementsLockPath)) {
        throw "Dependency validation failed after installation. The dependency fingerprint was not updated, and NiceGUI was not started."
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

Write-Host "Starting Portfolio Tracker on $productionUrl..."
$env:PORTFOLIO_TRACKER_PORT = [string]$productionPort
$env:NICEGUI_LAUNCHER_PID = [string]$PID
$shutdownToken = [Guid]::NewGuid().ToString("N")
$shutdownRequestPath = Join-Path ([System.IO.Path]::GetTempPath()) "portfolio-tracker-stop-$shutdownToken.request"
$shutdownWaitPath = Join-Path ([System.IO.Path]::GetTempPath()) "portfolio-tracker-stop-$shutdownToken.waiting"
$shutdownStatusPath = Join-Path ([System.IO.Path]::GetTempPath()) "portfolio-tracker-stop-$shutdownToken.json"
$env:NICEGUI_SHUTDOWN_REQUEST_FILE = $shutdownRequestPath
$env:NICEGUI_SHUTDOWN_WAIT_FILE = $shutdownWaitPath
$env:NICEGUI_SHUTDOWN_STATUS_FILE = $shutdownStatusPath
$script:launcherExitCode = 0
$gracefulShutdownTimeoutSeconds = 45
$processTreeConfirmationTimeoutSeconds = 8
$intentionalShutdown = $false
$launcherExitCode = 0
try {
    $quotedNiceGuiMain = '"' + $niceGuiMain + '"'
    $child = Start-Process -FilePath $venvPython -ArgumentList @($quotedNiceGuiMain) -WorkingDirectory $projectRoot -NoNewWindow -PassThru
    $rootIdentity = Get-ProcessIdentity -ProcessId $child.Id
    $childCreationTimeUtcTicks = $rootIdentity.CreationTimeUtcTicks
    $ready = $false
    $readinessError = $null
    $readyDeadline = [DateTime]::UtcNow.AddSeconds(30)
    while (-not $child.HasExited -and -not $ready -and [DateTime]::UtcNow -lt $readyDeadline) {
        try {
            $response = Invoke-WebRequest -Uri $readinessUrl -UseBasicParsing -TimeoutSec 1 -ErrorAction Stop
            $ready = $response.StatusCode -ge 200 -and $response.StatusCode -lt 500
        }
        catch {
            $readinessError = $_.Exception.Message
            Start-Sleep -Milliseconds 200
            $child.Refresh()
        }
    }
    if (-not $ready) {
        $termination = Stop-TrackedProcessTree -RootProcessId $child.Id -RootCreationTimeUtcTicks $childCreationTimeUtcTicks -ConfirmationTimeoutSeconds $processTreeConfirmationTimeoutSeconds -KnownProcessIds @($rootIdentity) -RequireObservedDescendantWhenRootGone
        if (-not $termination.Terminated) {
            $launcherExitCode = Get-StartupCleanupExitCode -TreeTerminationConfirmed $false
            $script:trapExitCode = $launcherExitCode
            throw "Portfolio Tracker failed readiness and the launched process tree could not be confirmed stopped. Remaining PIDs: $($termination.RunningProcessIds -join ', ')."
        }
        if ($readinessError) {
            throw "Portfolio Tracker did not become ready on $readinessUrl within 30 seconds. Last readiness error: $readinessError"
        }
        throw "Portfolio Tracker did not become ready on $readinessUrl within 30 seconds."
    }
    $script:applicationReady = $true
    $processTreeSnapshotSucceeded = $true
    try {
        $trackedProcessIds = @(Get-TrackedProcessTreeIds -RootProcessId $child.Id -RootCreationTimeUtcTicks $childCreationTimeUtcTicks)
    }
    catch {
        $processTreeSnapshotSucceeded = $false
        $trackedProcessIds = @($rootIdentity)
        Write-Host "Unable to snapshot the launched process tree: $($_.Exception.Message)"
    }
    Write-Host "NiceGUI ready to go on $productionUrl"
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
                    # Ask the application to reject new protected writes and
                    # drain admitted ones. The application allows 20 seconds for
                    # protected work; worker and server cleanup share this total
                    # 45-second graceful deadline.
                    $deadline = [DateTime]::UtcNow.AddSeconds($gracefulShutdownTimeoutSeconds)
                    $acknowledgementReported = $false
                    while ([DateTime]::UtcNow -lt $deadline) {
                        $child.Refresh()
                        if (-not $acknowledgementReported -and (Test-Path -LiteralPath $shutdownWaitPath -PathType Leaf)) {
                            Write-Host "Application acknowledged the shutdown request."
                            $acknowledgementReported = $true
                        }
                        if (-not $child.HasExited) {
                            try {
                                $currentProcessIds = @(Get-TrackedProcessTreeIds -RootProcessId $child.Id -RootCreationTimeUtcTicks $childCreationTimeUtcTicks)
                                $trackedProcessIds = @(Merge-TrackedProcessTreeIds -ExistingIds $trackedProcessIds -CurrentIds $currentProcessIds)
                            }
                            catch {
                                $processTreeSnapshotSucceeded = $false
                                Write-Host "Unable to inspect the launched process tree during shutdown: $($_.Exception.Message)"
                            }
                        }
                        $runningProcessIds = @(Get-RunningTrackedProcessIds -ProcessIds $trackedProcessIds)
                        if ($child.HasExited -and $runningProcessIds.Count -eq 0) {
                            break
                        }
                        Start-Sleep -Milliseconds 100
                    }
                    $child.Refresh()
                    if (-not $child.HasExited) {
                        try {
                            $currentProcessIds = @(Get-TrackedProcessTreeIds -RootProcessId $child.Id -RootCreationTimeUtcTicks $childCreationTimeUtcTicks)
                            $trackedProcessIds = @(Merge-TrackedProcessTreeIds -ExistingIds $trackedProcessIds -CurrentIds $currentProcessIds)
                        }
                        catch {
                            $processTreeSnapshotSucceeded = $false
                            Write-Host "Unable to verify the launched process tree after shutdown: $($_.Exception.Message)"
                        }
                    }
                    $runningProcessIds = @(Get-RunningTrackedProcessIds -ProcessIds $trackedProcessIds)
                    $forcedTreeTermination = $false
                    $treeTerminationConfirmed = $processTreeSnapshotSucceeded -and
                        $child.HasExited -and $runningProcessIds.Count -eq 0
                    if (-not $child.HasExited -or $runningProcessIds.Count -gt 0) {
                        Write-Host "Graceful shutdown timed out after $gracefulShutdownTimeoutSeconds seconds."
                        Write-Host "Attempting forced termination of the launched process tree..."
                        $termination = Stop-TrackedProcessTree -RootProcessId $child.Id -RootCreationTimeUtcTicks $childCreationTimeUtcTicks -ConfirmationTimeoutSeconds $processTreeConfirmationTimeoutSeconds -KnownProcessIds $trackedProcessIds
                        $forcedTreeTermination = $termination.Terminated
                        $treeTerminationConfirmed = $termination.Terminated -and
                            $termination.SnapshotSucceeded -and $processTreeSnapshotSucceeded
                        if (-not $treeTerminationConfirmed) {
                            Write-Host "Portfolio Tracker could not confirm termination of its launched process tree."
                            Write-Host "taskkill succeeded: $($termination.TaskkillSucceeded); process-tree snapshots succeeded: $($termination.SnapshotSucceeded -and $processTreeSnapshotSucceeded); remaining PIDs: $($termination.RunningProcessIds -join ', ')."
                            if ($termination.TaskkillErrors.Count -gt 0) {
                                Write-Host ($termination.TaskkillErrors -join "; ")
                            }
                        }
                    }
                    $child.Refresh()
                    $applicationStatus = Read-ApplicationShutdownStatus -Path $shutdownStatusPath
                    $serverExitCode = if ($child.HasExited) { $child.ExitCode } else { -1 }
                    $launcherExitCode = Get-IntentionalShutdownExitCode `
                        -ApplicationStatus $applicationStatus `
                        -ServerExitCode $serverExitCode `
                        -ForcedTreeTermination $forcedTreeTermination `
                        -TreeTerminationConfirmed $treeTerminationConfirmed
                    $script:launcherExitCode = $launcherExitCode
                    switch ($launcherExitCode) {
                        0 {
                            Write-Host "Portfolio Tracker stopped gracefully; the launched process tree has exited."
                        }
                        1 {
                            Write-Host "The application reported a shutdown failure or exited unexpectedly."
                        }
                        2 {
                            if ($applicationStatus.outcome -eq "forced") {
                                Write-Host "A worker required forced termination; the application and launched process tree exited."
                            }
                            else {
                                Write-Host "Graceful shutdown timed out; forced termination of the launched process tree was required."
                            }
                            Write-Host "This was not a wholly graceful shutdown."
                        }
                        3 {
                            Write-Host "Portfolio Tracker could not confirm shutdown of a worker or its launched process tree."
                        }
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
    if ($launcherExitCode -eq 0 -and -not $intentionalShutdown) {
        throw "NiceGUI exited unexpectedly with code $($child.ExitCode)."
    }
}
finally {
    Remove-Item Env:PORTFOLIO_TRACKER_PORT -ErrorAction SilentlyContinue
    Remove-Item Env:NICEGUI_LAUNCHER_PID -ErrorAction SilentlyContinue
    Remove-Item Env:NICEGUI_SHUTDOWN_REQUEST_FILE -ErrorAction SilentlyContinue
    Remove-Item Env:NICEGUI_SHUTDOWN_WAIT_FILE -ErrorAction SilentlyContinue
    Remove-Item Env:NICEGUI_SHUTDOWN_STATUS_FILE -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $shutdownRequestPath -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $shutdownWaitPath -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $shutdownStatusPath -Force -ErrorAction SilentlyContinue
}

exit $launcherExitCode
