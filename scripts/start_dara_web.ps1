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

    throw "DARA is not installed. Install it first with: pip install dara-xrd"
}

if (Test-DaraWeb) {
    Write-Host "DARA is already running. Opening $url"
    if (-not $NoBrowser) {
        Start-Process $url
    }
    exit 0
}

$daraExecutable = Find-DaraExecutable
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
