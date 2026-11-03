<#
    DARA updater: downloads only the files that differ from the latest version on GitHub.
    No Git is needed. Each local file is hashed the way Git hashes blobs and compared with the
    file list of the latest commit (one GitHub API call); changed and new files are downloaded,
    checked against their hash and only then moved into place. Files that an earlier update
    installed and that the new version no longer has are removed if they were not edited.
    Untracked things (.venv, tools, settings, the analysis database) are never touched.
    Windows PowerShell 5.1 compatible. This file contains Korean text and must stay UTF-8 with BOM.
#>
param(
    [switch]$CheckOnly,         # report whether an update exists (exit 10 if so) and change nothing
    [switch]$Yes,               # update without asking
    [string]$Launcher = '',     # the .cmd file running this script; it is staged as <name>.new
    [string]$RepoRoot = '',
    [string]$Repo = 'youngboong/dara',
    [string]$Branch = 'main',
    [string]$ApiBase = 'https://api.github.com',
    [string]$RawBase = 'https://raw.githubusercontent.com',
    [int]$TimeoutSec = 30,
    [switch]$FromStart          # called by START: a failed check prints one line and DARA starts as it is
)

$ErrorActionPreference = 'Stop'
if ([string]::IsNullOrWhiteSpace($RepoRoot)) { $RepoRoot = Split-Path -Parent $PSScriptRoot }
$script:UpdateDir = Join-Path $RepoRoot '.dara-update'
$script:ManifestPath = Join-Path $script:UpdateDir 'manifest.json'

function Get-GitBlobSha {
    # SHA-1 of "blob <size>\0" + content: the id Git (and the GitHub tree API) gives a file.
    param([string]$Path)
    $bytes = [System.IO.File]::ReadAllBytes($Path)
    $header = [System.Text.Encoding]::ASCII.GetBytes("blob $($bytes.Length)`0")
    $sha1 = [System.Security.Cryptography.SHA1]::Create()
    try {
        [void]$sha1.TransformBlock($header, 0, $header.Length, $null, 0)
        [void]$sha1.TransformFinalBlock($bytes, 0, $bytes.Length)
        return (($sha1.Hash | ForEach-Object { $_.ToString('x2') }) -join '')
    }
    finally { $sha1.Dispose() }
}

function Get-BlobSha {
    param([byte[]]$Bytes)
    $header = [System.Text.Encoding]::ASCII.GetBytes("blob $($Bytes.Length)`0")
    $sha1 = [System.Security.Cryptography.SHA1]::Create()
    try {
        [void]$sha1.TransformBlock($header, 0, $header.Length, $null, 0)
        [void]$sha1.TransformFinalBlock($Bytes, 0, $Bytes.Length)
        return (($sha1.Hash | ForEach-Object { $_.ToString('x2') }) -join '')
    }
    finally { $sha1.Dispose() }
}

function Test-SameFile {
    # True when the local file has the given blob id, also after turning CRLF into LF in a text
    # file (no NUL byte): Git stores text with LF (.gitattributes text=auto), Windows copies may not.
    param([string]$Path, [string]$Sha)
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return $false }
    $bytes = [System.IO.File]::ReadAllBytes($Path)
    if ((Get-BlobSha $bytes) -eq $Sha) { return $true }
    if ([Array]::IndexOf($bytes, [byte]0) -ge 0 -or [Array]::IndexOf($bytes, [byte]13) -lt 0) { return $false }
    $latin1 = [System.Text.Encoding]::GetEncoding(28591)  # maps every byte to one char and back
    $lf = $latin1.GetBytes($latin1.GetString($bytes).Replace("`r`n", "`n"))
    return (Get-BlobSha $lf) -eq $Sha
}

function Get-LocalPath {
    param([string]$RelativePath)
    return Join-Path $RepoRoot ($RelativePath -replace '/', '\')
}

function Test-SafeRelativePath {
    # Only plain relative paths inside the DARA folder; never the updater's own state.
    param([string]$RelativePath)
    if ([string]::IsNullOrWhiteSpace($RelativePath)) { return $false }
    if ($RelativePath.StartsWith('/') -or $RelativePath -match '(^|/)\.\.(/|$)' -or $RelativePath -match '[:\\]') { return $false }
    if ($RelativePath -match '^\.(git|dara-update|venv)(/|$)') { return $false }
    return $true
}

function Invoke-Api {
    param([string]$Path)
    $headers = @{ 'User-Agent' = 'DARA-updater'; 'Accept' = 'application/vnd.github+json' }
    return Invoke-RestMethod -Uri "$ApiBase$Path" -Headers $headers -TimeoutSec $TimeoutSec -UseBasicParsing
}

function Get-RemoteVersion {
    $commit = Invoke-Api "/repos/$Repo/commits/$Branch"
    $tree = Invoke-Api "/repos/$Repo/git/trees/$($commit.sha)?recursive=1"
    if ($tree.truncated) { throw 'The GitHub file list was truncated; download the full ZIP instead.' }
    $files = @{}
    foreach ($entry in $tree.tree) {
        # Regular files only (no submodules or symbolic links).
        if ($entry.type -ne 'blob' -or $entry.mode -eq '120000') { continue }
        if (-not (Test-SafeRelativePath $entry.path)) { continue }
        $files[$entry.path] = [pscustomobject]@{ Sha = $entry.sha; Size = [int64]$entry.size }
    }
    $message = "$($commit.commit.message)".Split("`n")[0].Trim()
    return [pscustomobject]@{ Sha = $commit.sha; Date = $commit.commit.committer.date; Message = $message; Files = $files }
}

function Read-Manifest {
    if (-not (Test-Path -LiteralPath $script:ManifestPath -PathType Leaf)) { return $null }
    try { return ([System.IO.File]::ReadAllText($script:ManifestPath) | ConvertFrom-Json) } catch { return $null }
}

function Get-UpdatePlan {
    param($Remote)
    $download = New-Object System.Collections.Generic.List[string]
    foreach ($path in $Remote.Files.Keys) {
        if (-not (Test-SameFile (Get-LocalPath $path) $Remote.Files[$path].Sha)) {
            $download.Add($path)
        }
    }
    # Remove only what an earlier update installed, is gone upstream and was not edited since.
    $remove = New-Object System.Collections.Generic.List[string]
    $manifest = Read-Manifest
    if ($null -ne $manifest -and $null -ne $manifest.files) {
        foreach ($property in $manifest.files.PSObject.Properties) {
            $path = $property.Name
            if ($Remote.Files.ContainsKey($path) -or -not (Test-SafeRelativePath $path)) { continue }
            if (Test-SameFile (Get-LocalPath $path) $property.Value) {
                $remove.Add($path)
            }
        }
    }
    $bytes = [int64]0
    foreach ($path in $download) { $bytes += $Remote.Files[$path].Size }
    return [pscustomobject]@{ Download = @($download | Sort-Object); Remove = @($remove | Sort-Object); Bytes = $bytes }
}

function Save-RemoteFile {
    param($Remote, [string]$Path, [string]$Target)
    $segments = $Path.Split('/') | ForEach-Object { [Uri]::EscapeDataString($_) }
    $url = "$RawBase/$Repo/$($Remote.Sha)/" + ($segments -join '/')
    $parent = Split-Path -Parent $Target
    if (-not (Test-Path -LiteralPath $parent)) { [void](New-Item -ItemType Directory -Path $parent -Force) }
    Invoke-WebRequest -Uri $url -OutFile $Target -UseBasicParsing -TimeoutSec ([Math]::Max($TimeoutSec, 120)) -Headers @{ 'User-Agent' = 'DARA-updater' }
    if ((Get-GitBlobSha $Target) -ne $Remote.Files[$Path].Sha) {
        throw "Downloaded $Path does not match its expected hash."
    }
}

function Write-Manifest {
    param($Remote)
    $files = [ordered]@{}
    foreach ($path in ($Remote.Files.Keys | Sort-Object)) { $files[$path] = $Remote.Files[$path].Sha }
    $data = [ordered]@{ commit = $Remote.Sha; date = $Remote.Date; message = $Remote.Message; updated = (Get-Date).ToString('o'); files = $files }
    [void](New-Item -ItemType Directory -Path $script:UpdateDir -Force)
    [System.IO.File]::WriteAllText($script:ManifestPath, ($data | ConvertTo-Json -Depth 4), (New-Object System.Text.UTF8Encoding $false))
}

function Format-Size {
    param([int64]$Bytes)
    if ($Bytes -ge 1MB) { return ('{0:N1} MB' -f ($Bytes / 1MB)) }
    return ('{0:N0} KB' -f [Math]::Ceiling($Bytes / 1KB))
}

function Invoke-DaraUpdate {
    # Returns 0 (up to date), 20 (updated), 10 (update available, -CheckOnly or declined), 1 (failed).
    try {
        [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
    }
    catch { }
    try {
        Write-Host 'Checking for a new DARA version... / 새 DARA 버전을 확인하는 중...'
        $remote = Get-RemoteVersion
        $plan = Get-UpdatePlan $remote
        if ($plan.Download.Count -eq 0 -and $plan.Remove.Count -eq 0) {
            Write-Host 'DARA is up to date. / DARA가 최신 버전입니다.' -ForegroundColor Green
            if (-not $CheckOnly) { Write-Manifest $remote }
            return 0
        }
        $when = ''
        try { $when = ([datetime]$remote.Date).ToLocalTime().ToString('yyyy-MM-dd HH:mm') } catch { }
        Write-Host ''
        Write-Host "New version / 새 버전: $when  $($remote.Message)" -ForegroundColor Cyan
        Write-Host ("{0} file(s) to download ({1}), {2} to remove. / 받을 파일 {0}개({1}), 지울 파일 {2}개." -f $plan.Download.Count, (Format-Size $plan.Bytes), $plan.Remove.Count)
        if ($CheckOnly) { return 10 }
        if (-not $Yes) {
            $answer = Read-Host 'Update now? / 지금 업데이트할까요? [Y/n]'
            if ($answer -match '^\s*[nN]') {
                Write-Host 'Update skipped. / 업데이트를 건너뜁니다.'
                return 10
            }
        }

        # Download everything first; nothing in the DARA folder changes until all files verify.
        $staging = Join-Path $script:UpdateDir 'staging'
        if (Test-Path -LiteralPath $staging) { Remove-Item -LiteralPath $staging -Recurse -Force }
        $count = 0
        foreach ($path in $plan.Download) {
            $count += 1
            Write-Host ("  [{0}/{1}] {2}" -f $count, $plan.Download.Count, $path)
            Save-RemoteFile -Remote $remote -Path $path -Target (Join-Path $staging ($path -replace '/', '\'))
        }
        foreach ($path in $plan.Download) {
            $target = Get-LocalPath $path
            if ($Launcher -and $path -ieq $Launcher) { $target = "$target.new" }  # the running .cmd swaps itself
            $parent = Split-Path -Parent $target
            if (-not (Test-Path -LiteralPath $parent)) { [void](New-Item -ItemType Directory -Path $parent -Force) }
            Move-Item -LiteralPath (Join-Path $staging ($path -replace '/', '\')) -Destination $target -Force
        }
        foreach ($path in $plan.Remove) {
            Remove-Item -LiteralPath (Get-LocalPath $path) -Force -ErrorAction SilentlyContinue
        }
        Remove-Item -LiteralPath $staging -Recurse -Force -ErrorAction SilentlyContinue
        Write-Manifest $remote
        Write-Host 'DARA was updated. / DARA를 업데이트했습니다.' -ForegroundColor Green
        return 20
    }
    catch {
        if ($FromStart) {
            Write-Host "Could not check for updates ($($_.Exception.Message)); starting the current version. / 업데이트를 확인하지 못해 현재 버전으로 시작합니다." -ForegroundColor Yellow
            return 1
        }
        Write-Host "Update failed / 업데이트 실패: $($_.Exception.Message)" -ForegroundColor Red
        Write-Host 'Nothing was changed if the download did not finish. Check the internet connection and try again. / 내려받기가 끝나지 않았다면 바뀐 것은 없습니다. 인터넷 연결을 확인하고 다시 시도하세요.' -ForegroundColor Red
        return 1
    }
}

if ($MyInvocation.InvocationName -ne '.') {
    exit ([int](@(Invoke-DaraUpdate)[-1]))
}
