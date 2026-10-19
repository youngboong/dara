# Run with powershell.exe -STA -NoProfile -File tests\check_api_key_dialog.ps1.
# Uses dummy input only; never changes the user's saved API key.
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "..\scripts\materials_project_key.ps1")
Add-Type -AssemblyName System.Windows.Forms

function Test-DialogAction {
    param([string]$Action)
    $script:dialogAction = $Action
    $script:emptySaveStayedOpen = $false
    $timer = New-Object System.Windows.Forms.Timer
    $timer.Interval = 150
    $timer.Add_Tick({
        $timer.Stop()
        $openForm = [System.Windows.Forms.Application]::OpenForms[0]
        if ($script:dialogAction -eq "Save") {
            $openForm.Controls["SaveKey"].PerformClick()
            $script:emptySaveStayedOpen = $openForm.Visible
            $openForm.Controls["ApiKey"].Text = "  dummy-key-for-dialog-test  "
            $openForm.Controls["SaveKey"].PerformClick()
        }
        else {
            $openForm.Controls["SkipKey"].PerformClick()
        }
    })
    try {
        $timer.Start()
        $result = Show-MaterialsProjectKeyDialog
        if ($Action -eq "Save") {
            if (-not $script:emptySaveStayedOpen) { throw "Blank key was accepted" }
            if ($result -ne "dummy-key-for-dialog-test") { throw "Dialog did not return trimmed input" }
        }
        elseif ($null -ne $result) { throw "Skip returned a key" }
    }
    finally { $timer.Dispose() }
}

Test-DialogAction -Action Save
Test-DialogAction -Action Skip

# Ensure a configured user does not receive another prompt; use process-only
# dummy state and restore it. No SetEnvironmentVariable(..., 'User') call runs.
$previousKey = $env:MP_API_KEY
try {
    function Get-MaterialsProjectKey { return "dummy-existing-key" }
    function Show-MaterialsProjectKeyDialog { throw "Existing key incorrectly prompted" }
    $changed = Initialize-MaterialsProjectKey
    if ($changed) { throw "Existing key incorrectly reported a settings change" }
    if ($env:MP_API_KEY -ne "dummy-existing-key") { throw "Key was not passed to the server environment" }
}
finally { $env:MP_API_KEY = $previousKey }
Write-Output "PASS: masked dialog save, empty input, skip, existing-key reuse"
