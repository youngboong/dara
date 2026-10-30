<#
    DARA launcher: installs everything DARA needs inside this folder (pinned uv, a repo-local
    CPython, .venv and the project) and then starts the web app. No Python is required on the PC.
    Windows PowerShell 5.1 compatible. This file contains Korean text and must stay UTF-8 with BOM.
#>
param(
    [switch]$NoBrowser,
    [switch]$SkipApiKeySetup,
    [switch]$SetApiKey,     # show the Materials Project key dialog again to replace a saved key
    [int]$StartupTimeoutSeconds = 180,
    [switch]$Reinstall,     # recreate .venv (uv venv --clear) and reinstall; keeps tools\ and the cache
    [switch]$SetupOnly,     # run setup + BGMN check, then exit 0 without starting the server
    [switch]$Offline,       # never touch the network (UV_OFFLINE=1); fail clearly if something is missing
    [string]$PythonExe = '' # expert override: use this interpreter as-is, skip all setup; env DARA_PYTHON does the same
)

$ErrorActionPreference = 'Stop'

# Pinned toolchain. Tests read these exact names; update version and hash together.
$UvVersion = '0.13.5'
$UvSha256  = 'a38b8d1902b7cfbc76b423ba1bb456f623f42064cdc72628b6075b89afd1019a'
$PythonVersion = '3.12.15'
$LauncherSchema = 1
$UvUrl = "https://github.com/astral-sh/uv/releases/download/$UvVersion/uv-x86_64-pc-windows-msvc.zip"

$script:RepoRoot = Split-Path -Parent $PSScriptRoot
$script:Url = 'http://127.0.0.1:8898'
$script:LogPath = $null
$script:StartTime = Get-Date
$script:UvExe = $null
$script:VenvPython = $null
$script:UsedExistingOffline = $false
$script:Step7Shown = $false
$script:TotalSteps = 7

. (Join-Path $PSScriptRoot 'materials_project_key.ps1')

# ---------------------------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------------------------

function Write-LogOnly {
    param([string]$Message)
    if ([string]::IsNullOrEmpty($script:LogPath)) { return }
    try {
        $stamp = (Get-Date).ToString('yyyy-MM-dd HH:mm:ss')
        [System.IO.File]::AppendAllText($script:LogPath, "$stamp  $Message`r`n", (New-Object System.Text.UTF8Encoding $false))
    }
    catch {
        # Logging must never stop the launcher.
    }
}

function Write-Log {
    param([string]$Message, [string]$Color = '')
    if ($Color) { Write-Host $Message -ForegroundColor $Color } else { Write-Host $Message }
    Write-LogOnly $Message
}

function Get-ElapsedSeconds {
    return [int]((Get-Date) - $script:StartTime).TotalSeconds
}

function Write-Step {
    param([int]$Number, [string]$English, [string]$Korean)
    Write-Log ("[{0}/{1}] {2} / {3}  ({4} s)" -f $Number, $script:TotalSteps, $English, $Korean, (Get-ElapsedSeconds)) 'Cyan'
}

function Initialize-LauncherLog {
    param([string]$RepoRoot = $script:RepoRoot)
    $logDir = Join-Path (Join-Path $RepoRoot 'artifacts') 'launcher'
    [void](New-Item -ItemType Directory -Path $logDir -Force)
    $script:LogPath = Join-Path $logDir ('setup-' + (Get-Date).ToString('yyyyMMdd-HHmmss') + '.log')
    [System.IO.File]::WriteAllText($script:LogPath, '', (New-Object System.Text.UTF8Encoding $false))
    # Keep the newest 10 setup logs.
    $old = @(Get-ChildItem -LiteralPath $logDir -Filter 'setup-*.log' -File | Sort-Object Name -Descending | Select-Object -Skip 10)
    foreach ($item in $old) {
        try { Remove-Item -LiteralPath $item.FullName -Force } catch { }
    }
}

# ---------------------------------------------------------------------------------------------
# Process helpers
# ---------------------------------------------------------------------------------------------

function Format-CmdArg {
    param([string]$Value)
    if ($Value -eq '') { return '""' }
    if ($Value -notmatch '[\s"&|<>^()%!,;]') { return $Value }
    return '"' + ($Value -replace '"', '\"') + '"'
}

function Invoke-Native {
    # Runs a program with stderr merged into stdout inside cmd.exe. Windows PowerShell 5.1 would
    # otherwise turn every stderr line (uv logs there) into a terminating NativeCommandError.
    param([string]$FilePath, [string[]]$Arguments = @(), [switch]$Quiet)
    $parts = @(Format-CmdArg $FilePath) + @($Arguments | ForEach-Object { Format-CmdArg $_ })
    $commandLine = ($parts -join ' ') + ' 2>&1'
    $lines = New-Object 'System.Collections.Generic.List[string]'
    Write-LogOnly ('> ' + ($parts -join ' '))
    # Build the command line ourselves: Windows PowerShell 5.1 mangles embedded quotes when it
    # passes a string to "& cmd.exe", so use ProcessStartInfo.Arguments verbatim.
    $startInfo = New-Object System.Diagnostics.ProcessStartInfo
    $startInfo.FileName = $env:ComSpec
    $startInfo.Arguments = '/d /s /c "' + $commandLine + '"'
    $startInfo.UseShellExecute = $false
    $startInfo.CreateNoWindow = $true
    $startInfo.RedirectStandardOutput = $true
    $startInfo.StandardOutputEncoding = [System.Text.Encoding]::UTF8
    $process = [System.Diagnostics.Process]::Start($startInfo)
    try {
        while ($true) {
            $text = $process.StandardOutput.ReadLine()
            if ($null -eq $text) { break }
            $lines.Add($text)
            if ($Quiet) { Write-LogOnly $text } else { Write-Log $text }
        }
        $process.WaitForExit()
        $code = $process.ExitCode
    }
    finally {
        $process.Dispose()
    }
    Write-LogOnly "(exit code $code)"
    return [pscustomobject]@{ ExitCode = $code; Lines = $lines.ToArray() }
}

function Write-LogTail {
    param([string[]]$Lines, [int]$Count = 30)
    $tail = @($Lines | Select-Object -Last $Count)
    foreach ($line in $tail) { Write-Host "    $line" -ForegroundColor DarkGray }
}

# ---------------------------------------------------------------------------------------------
# Environment hygiene and network
# ---------------------------------------------------------------------------------------------

function Set-DaraEnvironment {
    param([string]$RepoRoot = $script:RepoRoot)
    try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }
    $global:ProgressPreference = 'SilentlyContinue'
    [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12

    foreach ($name in @('PYTHONHOME', 'PYTHONPATH', 'VIRTUAL_ENV', 'CONDA_PREFIX', 'PYTHONSTARTUP')) {
        Remove-Item -LiteralPath "Env:$name" -ErrorAction SilentlyContinue
    }
    $toolsDir = Join-Path $RepoRoot 'tools'
    $env:UV_CACHE_DIR = Join-Path $toolsDir 'uv-cache'
    $env:UV_PYTHON_INSTALL_DIR = Join-Path $toolsDir 'python'
    $env:UV_SYSTEM_CERTS = '1'
    $env:NO_COLOR = '1'
    $env:UV_NO_PROGRESS = '1'
    $env:UV_HTTP_TIMEOUT = '120'
    if ($Offline) { $env:UV_OFFLINE = '1' }

    # uv only reads proxy environment variables; mirror the Windows/IE proxy when none is set.
    if ([string]::IsNullOrEmpty($env:HTTPS_PROXY) -and [string]::IsNullOrEmpty($env:https_proxy)) {
        try {
            $target = [Uri]'https://pypi.org/simple/'
            $proxy = [System.Net.WebRequest]::GetSystemWebProxy().GetProxy($target)
            if ($null -ne $proxy -and $proxy.AbsoluteUri -ne $target.AbsoluteUri) {
                $proxyText = $proxy.AbsoluteUri.TrimEnd('/')
                $env:HTTPS_PROXY = $proxyText
                $env:HTTP_PROXY = $proxyText
                if ([string]::IsNullOrEmpty($env:NO_PROXY) -and [string]::IsNullOrEmpty($env:no_proxy)) {
                    $env:NO_PROXY = '127.0.0.1,localhost'
                }
                $script:DetectedProxy = $proxy.Authority   # host:port only, never credentials
            }
        }
        catch { }
    }
    try {
        [System.Net.WebRequest]::DefaultWebProxy.Credentials = [System.Net.CredentialCache]::DefaultNetworkCredentials
    }
    catch { }
}

function Test-InternetAvailable {
    try {
        $response = Invoke-WebRequest -Uri 'https://pypi.org/simple/' -Method Head -UseBasicParsing -TimeoutSec 10
        return ($response.StatusCode -ge 200 -and $response.StatusCode -lt 400)
    }
    catch {
        Write-LogOnly ('Connectivity check failed: ' + $_.Exception.Message)
        return $false
    }
}

function Test-DaraWeb {
    try {
        $response = Invoke-WebRequest -Uri $script:Url -UseBasicParsing -TimeoutSec 2
        return $response.StatusCode -ge 200 -and $response.StatusCode -lt 500
    }
    catch {
        return $false
    }
}

# ---------------------------------------------------------------------------------------------
# Setup fingerprint and marker
# ---------------------------------------------------------------------------------------------

function Get-SetupFingerprint {
    param([string]$RepoRoot = $script:RepoRoot)
    $pyproject = Join-Path $RepoRoot 'pyproject.toml'
    $constraints = Join-Path (Join-Path $RepoRoot 'scripts') 'constraints-py312-win.txt'
    $fingerprint = [ordered]@{
        launcher_schema   = $LauncherSchema
        uv_version        = $UvVersion
        python_version    = $PythonVersion
        pyproject_sha256  = (Get-FileHash -LiteralPath $pyproject -Algorithm SHA256).Hash.ToLower()
        constraints_sha256 = (Get-FileHash -LiteralPath $constraints -Algorithm SHA256).Hash.ToLower()
        repo_root         = [System.IO.Path]::GetFullPath($RepoRoot).TrimEnd('\').ToLowerInvariant()
    }
    return $fingerprint
}

function Get-SetupPaths {
    param([string]$RepoRoot = $script:RepoRoot)
    $venv = Join-Path $RepoRoot '.venv'
    return [pscustomobject]@{
        Venv    = $venv
        Python  = Join-Path (Join-Path $venv 'Scripts') 'python.exe'
        Marker  = Join-Path $venv '.dara-setup-complete'
        Pending = Join-Path $venv '.dara-setup-pending'
    }
}

function Write-SetupFile {
    param([string]$Path, [string]$RepoRoot = $script:RepoRoot)
    $data = Get-SetupFingerprint -RepoRoot $RepoRoot
    $data['created'] = (Get-Date).ToString('o')
    $parent = Split-Path -Parent $Path
    if (-not (Test-Path -LiteralPath $parent)) { [void](New-Item -ItemType Directory -Path $parent -Force) }
    [System.IO.File]::WriteAllText($Path, ($data | ConvertTo-Json), (New-Object System.Text.UTF8Encoding $false))
}

function Write-SetupMarker {
    param([string]$RepoRoot = $script:RepoRoot)
    $paths = Get-SetupPaths -RepoRoot $RepoRoot
    Write-SetupFile -Path $paths.Marker -RepoRoot $RepoRoot
}

function Read-SetupFile {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return $null }
    try {
        $text = [System.IO.File]::ReadAllText($Path)
        if ([string]::IsNullOrWhiteSpace($text)) { return $null }
        return ($text | ConvertFrom-Json)
    }
    catch { return $null }
}

function Test-SetupMarker {
    param([string]$RepoRoot = $script:RepoRoot)
    $paths = Get-SetupPaths -RepoRoot $RepoRoot
    if (-not (Test-Path -LiteralPath $paths.Python -PathType Leaf)) { return $false }
    if (Test-Path -LiteralPath $paths.Pending) { return $false }
    $marker = Read-SetupFile -Path $paths.Marker
    if ($null -eq $marker) { return $false }
    $current = Get-SetupFingerprint -RepoRoot $RepoRoot
    foreach ($key in $current.Keys) {
        if ("$($marker.$key)" -ne "$($current[$key])") { return $false }
    }
    return $true
}

function Test-VenvReusable {
    # An existing .venv may be kept (and just updated) only when this launcher built it for the
    # same folder and Python version and it still runs.
    param([string]$RepoRoot = $script:RepoRoot)
    $paths = Get-SetupPaths -RepoRoot $RepoRoot
    if (-not (Test-Path -LiteralPath $paths.Python -PathType Leaf)) { return $false }
    $info = Read-SetupFile -Path $paths.Marker
    if ($null -eq $info) { $info = Read-SetupFile -Path $paths.Pending }
    if ($null -eq $info) { return $false }
    $current = Get-SetupFingerprint -RepoRoot $RepoRoot
    if ("$($info.repo_root)" -ne $current['repo_root']) { return $false }
    if ("$($info.python_version)" -ne $current['python_version']) { return $false }
    $probe = Invoke-Native -FilePath $paths.Python -Arguments @('-I', '-c', 'import sys') -Quiet
    return ($probe.ExitCode -eq 0)
}

# ---------------------------------------------------------------------------------------------
# Setup steps (each returns an exit code; 0 = success)
# ---------------------------------------------------------------------------------------------

function Get-UvVersionText {
    param([string]$UvPath)
    if (-not (Test-Path -LiteralPath $UvPath -PathType Leaf)) { return '' }
    try {
        $result = Invoke-Native -FilePath $UvPath -Arguments @('--version') -Quiet
        if ($result.ExitCode -eq 0 -and $result.Lines.Count -gt 0) { return "$($result.Lines[0])" }
    }
    catch { }
    return ''
}

function Install-Uv {
    param([string]$RepoRoot = $script:RepoRoot)
    $uvDir = Join-Path (Join-Path $RepoRoot 'tools') 'uv'
    $uvExe = Join-Path $uvDir 'uv.exe'
    $script:UvExe = $uvExe
    if ((Get-UvVersionText -UvPath $uvExe).StartsWith("uv $UvVersion ")) {
        Write-Log "uv $UvVersion is already installed."
        return 0
    }
    if ($Offline) {
        Write-Log 'uv is not installed and -Offline was given, so it cannot be downloaded. / uv가 설치되어 있지 않고 -Offline 모드여서 내려받을 수 없습니다.' 'Red'
        return 2
    }

    $downloadDir = Join-Path $uvDir 'download'
    $partial = Join-Path $downloadDir 'uv.zip.partial'
    $zipPath = Join-Path $downloadDir 'uv.zip'
    $staging = Join-Path $uvDir '_staging'
    foreach ($dir in @($downloadDir, $staging)) {
        if (Test-Path -LiteralPath $dir) { Remove-Item -LiteralPath $dir -Recurse -Force }
    }
    [void](New-Item -ItemType Directory -Path $downloadDir -Force)

    Write-Log "Downloading uv $UvVersion from GitHub... / GitHub에서 uv $UvVersion 을(를) 내려받는 중..."
    $downloaded = $false
    for ($attempt = 1; $attempt -le 3 -and -not $downloaded; $attempt++) {
        try {
            Invoke-WebRequest -Uri $UvUrl -OutFile $partial -UseBasicParsing -TimeoutSec 300
            $downloaded = $true
        }
        catch {
            Write-Log "  attempt $attempt of 3 failed: $($_.Exception.Message)" 'Yellow'
            if (Test-Path -LiteralPath $partial) { Remove-Item -LiteralPath $partial -Force -ErrorAction SilentlyContinue }
            if ($attempt -lt 3) { Start-Sleep -Seconds (2 * $attempt) }
        }
    }
    if (-not $downloaded) {
        Write-Log "Could not download uv from $UvUrl. Check the internet connection, proxy or firewall (github.com must be reachable). / uv를 내려받지 못했습니다. 인터넷, 프록시, 방화벽(github.com 접근)을 확인하세요." 'Red'
        return 4
    }

    $actual = (Get-FileHash -LiteralPath $partial -Algorithm SHA256).Hash.ToLower()
    if ($actual -ne $UvSha256) {
        Remove-Item -LiteralPath $partial -Force -ErrorAction SilentlyContinue
        Write-Log "The downloaded uv tool failed its SHA-256 check (expected $UvSha256, got $actual). A proxy or antivirus may have altered the download. / 내려받은 uv 파일의 SHA-256 검증에 실패했습니다. 프록시나 백신 프로그램이 파일을 변경했을 수 있습니다." 'Red'
        return 4
    }

    Move-Item -LiteralPath $partial -Destination $zipPath -Force
    Expand-Archive -LiteralPath $zipPath -DestinationPath $staging -Force
    foreach ($name in @('uv.exe', 'uvx.exe')) {
        $source = Join-Path $staging $name
        if (Test-Path -LiteralPath $source) { Move-Item -LiteralPath $source -Destination (Join-Path $uvDir $name) -Force }
    }
    Remove-Item -LiteralPath $staging -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $downloadDir -Recurse -Force -ErrorAction SilentlyContinue

    $versionText = Get-UvVersionText -UvPath $uvExe
    if (-not $versionText.StartsWith("uv $UvVersion ")) {
        Write-Log "uv was extracted but does not report version $UvVersion (got '$versionText'). / 압축을 푼 uv의 버전이 일치하지 않습니다." 'Red'
        return 4
    }
    Write-Log "Installed $versionText"
    return 0
}

function Install-ManagedPython {
    param([string]$RepoRoot = $script:RepoRoot)
    # --system: never pick the project's own .venv (uv searches the current directory for one).
    $script:BasePython = $null
    $install = Invoke-Native -FilePath $script:UvExe -Arguments @('--no-config', 'python', 'install', $PythonVersion, '--no-bin', '--no-registry')
    if ($install.ExitCode -eq 0) {
        $find = Invoke-Native -FilePath $script:UvExe -Arguments @('--no-config', 'python', 'find', $PythonVersion, '--managed-python', '--system') -Quiet
        $path = @($find.Lines | Where-Object { $_ -and (Test-Path -LiteralPath $_ -PathType Leaf) } | Select-Object -Last 1)
        if ($find.ExitCode -eq 0 -and $path.Count -gt 0) {
            $script:BasePython = "$($path[0])"
            Write-Log "Python: $($script:BasePython)"
            return 0
        }
    }
    else {
        Write-LogTail -Lines $install.Lines -Count 15
    }

    # Fallback: a system Python 3.12 that uv can find (for example when GitHub is blocked but PyPI is reachable).
    $system = Invoke-Native -FilePath $script:UvExe -Arguments @('--no-config', 'python', 'find', '3.12', '--no-managed-python', '--system') -Quiet
    $systemPath = @($system.Lines | Where-Object { $_ -and (Test-Path -LiteralPath $_ -PathType Leaf) } | Select-Object -Last 1)
    if ($system.ExitCode -eq 0 -and $systemPath.Count -gt 0) {
        $script:BasePython = "$($systemPath[0])"
        Write-Log "Could not install the managed Python $PythonVersion; using the system Python 3.12 at $($script:BasePython) instead. / 관리형 Python $PythonVersion 설치에 실패하여 시스템 Python 3.12를 대신 사용합니다." 'Yellow'
        return 0
    }
    Write-Log "Python $PythonVersion could not be installed or found. Check that github.com is reachable (Python is downloaded from there), then run START.cmd again. / Python $PythonVersion 을(를) 설치하거나 찾지 못했습니다. github.com 접근이 가능한지 확인한 뒤 START.cmd를 다시 실행하세요." 'Red'
    return 5
}

function New-ProjectVenv {
    param([string]$RepoRoot = $script:RepoRoot)
    $paths = Get-SetupPaths -RepoRoot $RepoRoot
    # The pending file and a missing marker are what make an interrupted setup resumable.
    if (Test-Path -LiteralPath $paths.Marker) { Remove-Item -LiteralPath $paths.Marker -Force }

    $recreate = $true
    if (-not $Reinstall -and (Test-Path -LiteralPath $paths.Venv)) {
        $recreate = -not (Test-VenvReusable -RepoRoot $RepoRoot)
    }
    elseif (-not (Test-Path -LiteralPath $paths.Venv)) {
        $recreate = $true
    }
    if ($recreate) {
        Write-Log 'Creating the project virtual environment (.venv)... / 프로젝트 가상 환경(.venv)을 만드는 중...'
        $venv = Invoke-Native -FilePath $script:UvExe -Arguments @('--no-config', 'venv', $paths.Venv, '--python', $script:BasePython, '--clear')
        if ($venv.ExitCode -ne 0 -or -not (Test-Path -LiteralPath $paths.Python -PathType Leaf)) {
            Write-LogTail -Lines $venv.Lines
            Write-Log "Could not create .venv. See the log: $($script:LogPath). / .venv를 만들지 못했습니다. 로그: $($script:LogPath)" 'Red'
            return 6
        }
    }
    else {
        Write-Log 'Reusing the existing .venv. / 기존 .venv를 재사용합니다.'
    }
    Write-SetupFile -Path $paths.Pending -RepoRoot $RepoRoot
    return 0
}

function Install-Packages {
    param([string]$RepoRoot = $script:RepoRoot)
    $paths = Get-SetupPaths -RepoRoot $RepoRoot
    $constraints = Join-Path (Join-Path $RepoRoot 'scripts') 'constraints-py312-win.txt'
    Write-Log 'Installing DARA and its packages... / DARA와 필요한 패키지를 설치하는 중...'
    $install = Invoke-Native -FilePath $script:UvExe -Arguments @(
        '--no-config', 'pip', 'install', '--python', $paths.Python, '--compile-bytecode', '-c', $constraints, '-e', $RepoRoot)
    if ($install.ExitCode -ne 0) {
        Write-LogTail -Lines $install.Lines
        Write-Log "Package installation failed. See the log: $($script:LogPath). Fix the reported problem and run START.cmd again; completed downloads are reused. / 패키지 설치에 실패했습니다. 로그: $($script:LogPath). 문제를 해결한 뒤 START.cmd를 다시 실행하면 이미 받은 파일은 재사용됩니다." 'Red'
        return 6
    }
    return 0
}

function Test-Installation {
    # -Quiet is used by the offline fallback, which only needs a yes/no answer.
    param([string]$RepoRoot = $script:RepoRoot, [switch]$Quiet)
    $paths = Get-SetupPaths -RepoRoot $RepoRoot
    $code = "import importlib.metadata as m, dara, pymatgen, fastapi, ray, uvicorn, mp_api; print('DARA-VERIFY', m.version('dara-xrd'), dara.__file__)"
    if ($Quiet) {
        $check = Invoke-Native -FilePath $paths.Python -Arguments @('-I', '-X', 'utf8', '-c', $code) -Quiet
    }
    else {
        $check = Invoke-Native -FilePath $paths.Python -Arguments @('-I', '-X', 'utf8', '-c', $code)
    }
    $line = @($check.Lines | Where-Object { $_ -like 'DARA-VERIFY *' } | Select-Object -Last 1)
    $srcRoot = ([System.IO.Path]::GetFullPath((Join-Path $RepoRoot 'src')).TrimEnd('\') + '\').ToLowerInvariant()
    $ok = $false
    if ($check.ExitCode -eq 0 -and $line.Count -gt 0) {
        $location = ("$($line[0])" -replace '^DARA-VERIFY \S+ ', '').ToLowerInvariant()
        $ok = $location.StartsWith($srcRoot)
    }
    if (-not $ok -and $Quiet) { return 7 }
    if (-not $ok) {
        Write-LogTail -Lines $check.Lines
        Write-Log "The installed environment failed the import check (dara must load from this folder's src). See the log: $($script:LogPath). Run START.cmd -Reinstall to rebuild it. / 설치된 환경의 import 검증에 실패했습니다. 로그: $($script:LogPath). START.cmd -Reinstall 로 다시 만드세요." 'Red'
        return 7
    }
    return 0
}

function Test-ExistingEnvironmentUsable {
    # True when a .venv from an earlier START still runs DARA, even if its completion marker is gone.
    # A failed reinstall removes the marker (and leaves the pending file) while the packages stay
    # importable, so "marker or pending file" plus the import probe is what proves it is usable.
    param([string]$RepoRoot = $script:RepoRoot)
    $paths = Get-SetupPaths -RepoRoot $RepoRoot
    if (-not (Test-Path -LiteralPath $paths.Python -PathType Leaf)) { return $false }
    $hasState = (Test-Path -LiteralPath $paths.Marker -PathType Leaf) -or (Test-Path -LiteralPath $paths.Pending -PathType Leaf)
    if (-not $hasState) { return $false }
    return ([int](@(Test-Installation -RepoRoot $RepoRoot -Quiet)[-1]) -eq 0)
}

function Invoke-DaraSetup {
    # Steps 2-7. Returns an exit code and leaves the venv interpreter in $script:VenvPython.
    param([string]$RepoRoot = $script:RepoRoot)
    $paths = Get-SetupPaths -RepoRoot $RepoRoot
    $toolsDir = Join-Path $RepoRoot 'tools'
    [void](New-Item -ItemType Directory -Path $toolsDir -Force)

    $lock = $null
    try {
        $lock = [System.IO.File]::Open((Join-Path $toolsDir '.setup.lock'), 'OpenOrCreate', 'ReadWrite', 'None')
    }
    catch {
        Write-Log 'Another START is already installing DARA. Wait for it to finish. / 다른 START 창에서 설치가 진행 중입니다. 완료될 때까지 기다리세요.' 'Red'
        return 1
    }
    try {
        Write-Step 2 'Checking the internet connection' '인터넷 연결 확인'
        $online = $true
        if (-not $Offline) {
            $online = Test-InternetAvailable
            if (-not $online) {
                $usable = [bool](@(Test-ExistingEnvironmentUsable -RepoRoot $RepoRoot)[-1])
                if ($usable) {
                    Write-Log 'No internet connection: starting the existing DARA environment without checking for updates. / 인터넷 연결이 없어 업데이트 확인 없이 기존 DARA 환경으로 시작합니다.' 'Yellow'
                    $script:VenvPython = $paths.Python
                    $script:UsedExistingOffline = $true
                    return 0
                }
                Write-Log 'No internet connection, and DARA is not installed yet. The first launch downloads about 1 GB (Python 3.12 and scientific packages). Connect to the internet (check proxy/firewall) and run START.cmd again. / 인터넷에 연결되어 있지 않고 DARA가 아직 설치되지 않았습니다. 첫 실행 시 약 1 GB(Python 3.12와 과학 계산 패키지)를 내려받습니다. 인터넷(프록시/방화벽 포함)을 확인한 후 START.cmd를 다시 실행하세요.' 'Red'
                return 2
            }
        }
        if ($script:DetectedProxy) { Write-LogOnly "Using Windows proxy $($script:DetectedProxy) for uv." }

        Write-Step 3 'Preparing the uv tool' 'uv 도구 준비'
        Write-Log 'First-time setup: this downloads about 1 GB and can take 5-15 minutes. Keep this window open. / 최초 설정: 약 1 GB를 내려받으며 5-15분 정도 걸립니다. 이 창을 닫지 마세요.' 'Yellow'
        $code = [int](@(Install-Uv -RepoRoot $RepoRoot)[-1])
        if ($code -ne 0) { return $code }

        Write-Step 4 "Preparing Python $PythonVersion" "Python $PythonVersion 준비"
        $code = [int](@(Install-ManagedPython -RepoRoot $RepoRoot)[-1])
        if ($code -ne 0) { return $code }

        Write-Step 5 'Preparing the virtual environment' '가상 환경 준비'
        $code = [int](@(New-ProjectVenv -RepoRoot $RepoRoot)[-1])
        if ($code -ne 0) { return $code }

        Write-Step 6 'Installing packages' '패키지 설치'
        $code = [int](@(Install-Packages -RepoRoot $RepoRoot)[-1])
        if ($code -ne 0) { return $code }

        $script:Step7Shown = $true
        Write-Step 7 'Verifying the installation and BGMN programs' '설치 및 BGMN 프로그램 검증'
        $code = [int](@(Test-Installation -RepoRoot $RepoRoot)[-1])
        if ($code -ne 0) { return $code }

        Write-SetupMarker -RepoRoot $RepoRoot
        Remove-Item -LiteralPath $paths.Pending -Force -ErrorAction SilentlyContinue
        $script:VenvPython = $paths.Python
        return 0
    }
    finally {
        if ($null -ne $lock) { $lock.Dispose() }
    }
}

function Set-WindowsCaBundle {
    # HTTPS-inspecting networks re-sign traffic with a root certificate only Windows trusts;
    # give Python (requests, urllib) a bundle that includes the Windows certificate store.
    param([string]$Python, [string]$RepoRoot = $script:RepoRoot)
    $result = Invoke-Native -Quiet -FilePath $Python -Arguments @('-I', (Join-Path (Join-Path $RepoRoot 'scripts') 'windows_ca_bundle.py'), '--repo-root', $RepoRoot)
    $bundle = @($result.Lines | Where-Object { $_ -match '\.pem\s*$' })[-1]
    if ($result.ExitCode -eq 0 -and $bundle -and (Test-Path -LiteralPath $bundle.Trim() -PathType Leaf)) {
        foreach ($name in @('SSL_CERT_FILE', 'REQUESTS_CA_BUNDLE', 'CURL_CA_BUNDLE')) {
            Set-Item -LiteralPath "Env:$name" -Value $bundle.Trim()
        }
        Write-LogOnly "CA bundle (certifi + Windows certificate store): $($bundle.Trim())"
    }
    else {
        Write-LogOnly 'Windows CA bundle not created; Python default certificates are used.'
    }
}

function Invoke-BgmnCheck {
    param([string]$Python, [string]$RepoRoot = $script:RepoRoot)
    $arguments = @('-I', (Join-Path (Join-Path $RepoRoot 'scripts') 'ensure_bgmn.py'))
    if ($Offline) { $arguments += '--offline' }
    $result = Invoke-Native -FilePath $Python -Arguments $arguments
    if ($result.ExitCode -ne 0) { return 3 }
    return 0
}

# ---------------------------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------------------------

function Invoke-DaraStart {
    $repoRoot = $script:RepoRoot
    $script:StartTime = Get-Date
    try {
        Set-DaraEnvironment -RepoRoot $repoRoot
        Initialize-LauncherLog -RepoRoot $repoRoot
        Write-LogOnly "DARA START, repo $repoRoot, uv $UvVersion, Python $PythonVersion"

        $apiKeyChanged = $false
        if (-not $SkipApiKeySetup) {
            $apiKeyChanged = Initialize-MaterialsProjectKey -Force:$SetApiKey
        }
        if ($apiKeyChanged -and (Test-DaraWeb)) {
            [void][System.Windows.Forms.MessageBox]::Show(
                'Your API key is saved. DARA is already running: close its server terminal and run START.cmd again to use the new key.',
                'DARA - Restart required'
            )
        }

        Write-Step 1 'Checking the DARA environment' 'DARA 환경 확인'
        $daraPython = $null
        $override = $PythonExe
        if ([string]::IsNullOrWhiteSpace($override)) { $override = $env:DARA_PYTHON }
        if (-not [string]::IsNullOrWhiteSpace($override)) {
            if (-not (Test-Path -LiteralPath $override -PathType Leaf)) {
                Write-Log "The Python executable given by -PythonExe / DARA_PYTHON was not found: $override / 지정한 Python 실행 파일을 찾을 수 없습니다." 'Red'
                return 1
            }
            $daraPython = (Resolve-Path -LiteralPath $override).Path
            Write-Log "Using the Python you specified (setup skipped): $daraPython"
        }
        elseif (-not $Reinstall -and (Test-SetupMarker -RepoRoot $repoRoot)) {
            $daraPython = (Get-SetupPaths -RepoRoot $repoRoot).Python
            Write-Log 'DARA is already installed; skipping setup. / DARA가 이미 설치되어 있어 설치를 건너뜁니다.'
        }
        else {
            $code = [int](@(Invoke-DaraSetup -RepoRoot $repoRoot)[-1])
            if ($code -ne 0) {
                # An -Offline reinstall that cannot finish must not lock out an environment that still works.
                # (Code 1 means another START holds the setup lock, so never fall back then.)
                if ($Offline -and $code -ne 1 -and [bool](@(Test-ExistingEnvironmentUsable -RepoRoot $repoRoot)[-1])) {
                    Write-Log 'The offline update could not finish: starting the existing DARA environment as it is. / 오프라인 업데이트를 마치지 못해 기존 DARA 환경을 그대로 사용해 시작합니다.' 'Yellow'
                    $script:UsedExistingOffline = $true
                    $script:VenvPython = (Get-SetupPaths -RepoRoot $repoRoot).Python
                }
                else {
                    return $code
                }
            }
            $daraPython = $script:VenvPython
        }

        Set-WindowsCaBundle -Python $daraPython -RepoRoot $repoRoot

        if (-not $script:Step7Shown) {
            Write-Step 7 'Checking the BGMN programs' 'BGMN 프로그램 확인'
        }
        $code = [int](@(Invoke-BgmnCheck -Python $daraPython -RepoRoot $repoRoot)[-1])
        if ($code -ne 0) { return $code }

        if ($SetupOnly) {
            Write-Log ("Setup complete / 설정 완료  ({0} s). Log: {1}" -f (Get-ElapsedSeconds), $script:LogPath) 'Green'
            return 0
        }

        Write-Log "Starting DARA with $daraPython"
        Write-Log 'Opening the startup page, then DARA automatically.'
        Write-Log 'DARA stops automatically when all DARA browser windows are closed.'
        Write-Host ''
        $launchArguments = @('-u', (Join-Path $PSScriptRoot 'launch_dara_web.py'), '--timeout', "$StartupTimeoutSeconds")
        if ($NoBrowser) {
            $launchArguments += '--no-browser'
        }
        # Start-Process (not "&") keeps the server's console output out of this function's return value.
        $argumentText = ($launchArguments | ForEach-Object { Format-CmdArg $_ }) -join ' '
        Write-LogOnly "> $daraPython $argumentText"
        $server = Start-Process -FilePath $daraPython -ArgumentList $argumentText -NoNewWindow -Wait -PassThru
        $server.WaitForExit()
        if ($server.ExitCode -gt 0) {
            Write-Log 'DARA could not start. See the startup page and artifacts\launcher logs. / DARA를 시작하지 못했습니다. 시작 페이지와 artifacts\launcher 로그를 확인하세요.' 'Red'
            return 1
        }
        return 0
    }
    catch {
        $message = $_.Exception.Message
        Write-LogOnly ("Unexpected error: " + ($_ | Out-String))
        Write-Host "Unexpected error: $message" -ForegroundColor Red
        if ($script:LogPath) { Write-Host "Log: $($script:LogPath)" -ForegroundColor Red }
        return 1
    }
}

if ($MyInvocation.InvocationName -ne '.') {
    exit ([int](@(Invoke-DaraStart)[-1]))
}
