param(
    [switch]$NoBrowser,
    [switch]$ConfigureApiKey,
    [switch]$SkipApiKeySetup,
    [int]$StartupTimeoutSeconds = 180
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$url = "http://127.0.0.1:8898"
. (Join-Path $PSScriptRoot "materials_project_key.ps1")

if ($ConfigureApiKey) {
    [void](Initialize-MaterialsProjectKey -Force)
    Write-Host "If DARA is running, close its server terminal and run START.cmd to apply changes."
    exit 0
}

function Test-DaraWeb {
    try {
        $response = Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 2
        return $response.StatusCode -ge 200 -and $response.StatusCode -lt 500
    }
    catch {
        return $false
    }
}

function Find-DaraExecutable {
    # Prefer project environments so an unrelated global DARA installation
    # cannot accidentally serve an older frontend.
    $candidates = @(
        (Join-Path $repoRoot "..\xrd\Scripts\dara.exe"),
        (Join-Path $repoRoot ".venv\Scripts\dara.exe"),
        (Join-Path $repoRoot "venv\Scripts\dara.exe"),
        (Join-Path $repoRoot "xrd\Scripts\dara.exe")
    )

    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate -PathType Leaf) {
            return (Resolve-Path -LiteralPath $candidate).Path
        }
    }

    $pathCommand = Get-Command dara -CommandType Application -ErrorAction SilentlyContinue
    if ($null -ne $pathCommand) {
        return $pathCommand.Source
    }

    return $null
}

function Find-SetupPython {
    foreach ($commandName in @("py", "python", "python3")) {
        $command = Get-Command $commandName -CommandType Application -ErrorAction SilentlyContinue
        if ($null -eq $command) {
            continue
        }
        $pythonPath = & $command.Source -c "import sys; print(sys.executable if sys.version_info >= (3, 10) else '')" 2>$null
        if ($LASTEXITCODE -eq 0 -and $pythonPath -and (Test-Path -LiteralPath "$pythonPath")) {
            return "$pythonPath"
        }
    }
    throw "Python 3.10 or newer is required. Install Python, then run START.cmd again."
}

function Install-Dara {
    $environmentPath = Join-Path $repoRoot ".venv"
    $pythonPath = Join-Path $environmentPath "Scripts\python.exe"
    if (-not (Test-Path -LiteralPath $pythonPath -PathType Leaf)) {
        $setupPython = Find-SetupPython
        Write-Host "Creating a project Python environment..."
        & $setupPython -m venv $environmentPath | Out-Host
        if ($LASTEXITCODE -ne 0) {
            throw "Could not create the Python environment."
        }
    }

    $pendingPath = Join-Path $environmentPath ".dara-setup-pending"
    New-Item -ItemType File -Path $pendingPath -Force | Out-Null
    Write-Host "Installing dara-xrd (first launch may take several minutes)..."
    & $pythonPath -m pip install dara-xrd | Out-Host
    if ($LASTEXITCODE -ne 0) {
        throw "Could not install dara-xrd. Check your internet connection and the pip error above, then try again."
    }

    # The PyPI package supplies dependencies; this checkout supplies the
    # customized UI and server. Do not silently launch the upstream UI.
    Write-Host "Connecting this repository's customized DARA..."
    & $pythonPath -m pip install --no-deps --editable $repoRoot | Out-Host
    if ($LASTEXITCODE -ne 0) {
        throw "Could not install the customized DARA. See the pip error above."
    }

    $executable = Join-Path $environmentPath "Scripts\dara.exe"
    if (-not (Test-Path -LiteralPath $executable -PathType Leaf)) {
        throw "Installation finished without creating dara.exe. See the pip output above."
    }
    Remove-Item -LiteralPath $pendingPath
    return $executable
}

$apiKeyChanged = $false
if (-not $SkipApiKeySetup) {
    $apiKeyChanged = Initialize-MaterialsProjectKey
}

if ($apiKeyChanged -and (Test-DaraWeb)) {
    [void][System.Windows.Forms.MessageBox]::Show(
        "Your API key is saved. DARA is already running: close its server terminal and run START.cmd again to use the new key.",
        "DARA - Restart required"
    )
}

$daraExecutable = Find-DaraExecutable
if ($null -eq $daraExecutable -or (Test-Path -LiteralPath (Join-Path $repoRoot ".venv\.dara-setup-pending"))) {
    $daraExecutable = Install-Dara
}
Write-Host "Starting DARA with $daraExecutable"
Write-Host "Opening the startup page, then DARA automatically."
Write-Host "Keep this START window open while using DARA."
Write-Host ""

$scriptsPath = Split-Path -Parent $daraExecutable
$pythonCandidates = @(
    (Join-Path $scriptsPath "python.exe"),
    (Join-Path (Split-Path -Parent $scriptsPath) "python.exe")
)
$serverPython = $pythonCandidates | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } | Select-Object -First 1
if (-not $serverPython) {
    throw "Could not find the Python interpreter for $daraExecutable."
}
$launchArguments = @("-u", (Join-Path $PSScriptRoot "launch_dara_web.py"), "--timeout", "$StartupTimeoutSeconds")
if ($NoBrowser) {
    $launchArguments += "--no-browser"
}
& $serverPython @launchArguments
if ($LASTEXITCODE -gt 0) {
    throw "DARA could not start. See the startup page and artifacts\launcher logs."
}
