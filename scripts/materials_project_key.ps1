function Get-MaterialsProjectKey {
    # Explorer can retain an old environment snapshot after a key was saved.
    $savedKey = [Environment]::GetEnvironmentVariable("MP_API_KEY", "User")
    if (-not [string]::IsNullOrWhiteSpace($savedKey)) {
        return $savedKey.Trim()
    }
    return $env:MP_API_KEY
}

function Show-MaterialsProjectKeyDialog {
    Add-Type -AssemblyName System.Windows.Forms
    Add-Type -AssemblyName System.Drawing
    $form = New-Object System.Windows.Forms.Form
    $form.Text = "DARA - Materials Project API key"
    $form.ClientSize = New-Object System.Drawing.Size(530, 225)
    $form.StartPosition = "CenterScreen"
    $form.FormBorderStyle = "FixedDialog"
    $form.MaximizeBox = $false
    $form.MinimizeBox = $false

    $label = New-Object System.Windows.Forms.Label
    $label.Text = "Paste your Materials Project API key (optional).`nSaved for your Windows account and used automatically next time.`nSkip to use DARA without Materials Project."
    $label.SetBounds(20, 15, 490, 65)
    $form.Controls.Add($label)

    $link = New-Object System.Windows.Forms.LinkLabel
    $link.Text = "Open Materials Project to get an API key"
    $link.AutoSize = $true
    $link.Location = New-Object System.Drawing.Point(20, 85)
    $link.Add_LinkClicked({ Start-Process "https://materialsproject.org" })
    $form.Controls.Add($link)

    $inputBox = New-Object System.Windows.Forms.TextBox
    $inputBox.Name = "ApiKey"
    $inputBox.UseSystemPasswordChar = $true
    $inputBox.SetBounds(20, 115, 490, 25)
    $form.Controls.Add($inputBox)

    $save = New-Object System.Windows.Forms.Button
    $save.Name = "SaveKey"
    $save.Text = "Save and continue"
    $save.SetBounds(215, 170, 155, 32)
    $save.Add_Click({
        if ([string]::IsNullOrWhiteSpace($inputBox.Text)) {
            $label.Text = "Paste an API key, or choose Skip for now."
            $inputBox.Focus()
            return
        }
        $form.DialogResult = [System.Windows.Forms.DialogResult]::OK
    })
    $form.Controls.Add($save)

    $skip = New-Object System.Windows.Forms.Button
    $skip.Name = "SkipKey"
    $skip.Text = "Skip for now"
    $skip.DialogResult = [System.Windows.Forms.DialogResult]::Cancel
    $skip.SetBounds(380, 170, 130, 32)
    $form.Controls.Add($skip)
    $form.AcceptButton = $save
    $form.CancelButton = $skip
    $form.Add_Shown({ $inputBox.Focus() })
    try {
        if ($form.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) {
            return $inputBox.Text.Trim()
        }
        return $null
    }
    finally {
        $inputBox.Clear()
        $form.Dispose()
    }
}

function Initialize-MaterialsProjectKey {
    # -Force (START.cmd -SetApiKey) shows the dialog even when a key is saved or the setup
    # was skipped before, so a wrong or expired key can be replaced.
    param([switch]$Force)
    $existingKey = Get-MaterialsProjectKey
    if (-not $Force -and -not [string]::IsNullOrWhiteSpace($existingKey)) {
        $env:MP_API_KEY = $existingKey
        return $false
    }

    $settingsDir = Join-Path ([Environment]::GetFolderPath("LocalApplicationData")) "DARA"
    $skipMarker = Join-Path $settingsDir "mp-api-setup-skipped"
    if (-not $Force -and (Test-Path -LiteralPath $skipMarker)) { return $false }

    $enteredKey = Show-MaterialsProjectKeyDialog
    if (-not [string]::IsNullOrWhiteSpace($enteredKey)) {
        # Never put keys in the repository, command arguments, or console output.
        [Environment]::SetEnvironmentVariable("MP_API_KEY", $enteredKey, "User")
        $env:MP_API_KEY = $enteredKey
        Remove-Item -LiteralPath $skipMarker -Force -ErrorAction SilentlyContinue
        Write-Host "Materials Project API key saved."
        return $true
    }
    elseif (-not [string]::IsNullOrWhiteSpace($existingKey)) {
        $env:MP_API_KEY = $existingKey  # skipped a forced prompt: keep the saved key
    }
    else {
        New-Item -ItemType Directory -Path $settingsDir -Force | Out-Null
        New-Item -ItemType File -Path $skipMarker -Force | Out-Null
        Write-Host "Materials Project setup skipped. Turn off MP under Reference databases to use COD without a key."
    }
    return $false
}
