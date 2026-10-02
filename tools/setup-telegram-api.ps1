# Saves Telegram API credentials using Windows DPAPI for the current user.
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
$workspace = Split-Path -Parent $PSScriptRoot
$credentialDirectory = Join-Path $workspace '.private'
$credentialFile = Join-Path $credentialDirectory 'telegram-api.clixml'
$form = New-Object System.Windows.Forms.Form
$form.Text = 'Telegram API - local encrypted setup'
$form.ClientSize = New-Object System.Drawing.Size(450,190)
$form.StartPosition = 'CenterScreen'
$form.FormBorderStyle = 'FixedDialog'
$form.MaximizeBox = $false
$form.MinimizeBox = $false
$idLabel = New-Object System.Windows.Forms.Label
$idLabel.Text = 'api_id'
$idLabel.Location = New-Object System.Drawing.Point(20,25)
$idLabel.Size = New-Object System.Drawing.Size(90,25)
$idInput = New-Object System.Windows.Forms.TextBox
$idInput.Location = New-Object System.Drawing.Point(120,22)
$idInput.Size = New-Object System.Drawing.Size(305,25)
$hashLabel = New-Object System.Windows.Forms.Label
$hashLabel.Text = 'api_hash'
$hashLabel.Location = New-Object System.Drawing.Point(20,65)
$hashLabel.Size = New-Object System.Drawing.Size(90,25)
$hashInput = New-Object System.Windows.Forms.TextBox
$hashInput.Location = New-Object System.Drawing.Point(120,62)
$hashInput.Size = New-Object System.Drawing.Size(305,25)
$hashInput.UseSystemPasswordChar = $true
$saveButton = New-Object System.Windows.Forms.Button
$saveButton.Text = 'Save encrypted'
$saveButton.Location = New-Object System.Drawing.Point(270,125)
$saveButton.Size = New-Object System.Drawing.Size(155,35)
$saveButton.Add_Click({
    $numericId = 0
    if (-not [int]::TryParse($idInput.Text.Trim(), [ref]$numericId) -or $numericId -le 0 -or $hashInput.Text -notmatch '^[0-9a-fA-F]{32}$') {
        [System.Windows.Forms.MessageBox]::Show('Enter a positive api_id and a 32-character hexadecimal api_hash.', 'Check input') | Out-Null
        return
    }
    $secret = ConvertTo-SecureString $hashInput.Text -AsPlainText -Force
    $credential = New-Object System.Management.Automation.PSCredential ($numericId.ToString(), $secret)
    New-Item -ItemType Directory -Path $credentialDirectory -Force | Out-Null
    $credential | Export-Clixml -LiteralPath $credentialFile
    $hashInput.Clear()
    [System.Windows.Forms.MessageBox]::Show('Saved locally with Windows encryption. No credentials were sent to chat.', 'Saved') | Out-Null
    $form.DialogResult = [System.Windows.Forms.DialogResult]::OK
    $form.Close()
})
$form.Controls.AddRange(@($idLabel,$idInput,$hashLabel,$hashInput,$saveButton))
$form.AcceptButton = $saveButton
try { [void]$form.ShowDialog() } finally { $hashInput.Clear(); $form.Dispose() }
