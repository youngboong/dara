param(
    [switch]$NoBrowser,
    [int]$StartupTimeoutSeconds = 120
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$url = "http://127.0.0.1:8898"

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
    throw "Python 3.10 or newer is required. Install Python, then double-click Start DARA Web.cmd again."
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

if (Test-DaraWeb) {
    Write-Host "DARA is already running. Opening $url"
    if (-not $NoBrowser) {
        Start-Process $url
    }
    exit 0
}

$daraExecutable = Find-DaraExecutable
if ($null -eq $daraExecutable -or (Test-Path -LiteralPath (Join-Path $repoRoot ".venv\.dara-setup-pending"))) {
    $daraExecutable = Install-Dara
}
Write-Host "Starting DARA with $daraExecutable"
Write-Host "The browser will open when the server is ready."
Write-Host "Keep this window open. Press Ctrl+C to stop DARA."
Write-Host ""

$browserJob = $null
if (-not $NoBrowser) {
    $browserJob = Start-Job -ScriptBlock {
        param($TargetUrl, $TimeoutSeconds)

        $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
        while ((Get-Date) -lt $deadline) {
            try {
                $response = Invoke-WebRequest -Uri $TargetUrl -UseBasicParsing -TimeoutSec 2
                if ($response.StatusCode -ge 200 -and $response.StatusCode -lt 500) {
                    Start-Process $TargetUrl
                    return
                }
            }
            catch {
                Start-Sleep -Milliseconds 500
            }
        }
    } -ArgumentList $url, $StartupTimeoutSeconds
}

try {
    Push-Location $repoRoot
    & $daraExecutable server
    # Windows reports Ctrl+C/termination as a negative exit code. Treat that
    # as a normal stop so the click-to-run terminal can close cleanly.
    if ($LASTEXITCODE -gt 0) {
        throw "DARA exited with code $LASTEXITCODE."
    }
}
finally {
    Pop-Location
    if ($null -ne $browserJob) {
        Stop-Job $browserJob -ErrorAction SilentlyContinue
        Remove-Job $browserJob -Force -ErrorAction SilentlyContinue
    }
}
