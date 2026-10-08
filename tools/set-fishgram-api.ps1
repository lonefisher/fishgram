param([switch]$ConfirmOwnApplication)
$ErrorActionPreference='Stop'
if (-not $ConfirmOwnApplication) { throw 'Use -ConfirmOwnApplication only for the Telegram application assigned to FishGram.' }
$root=Split-Path -Parent $PSScriptRoot
$private=Join-Path $root '.private'
New-Item -ItemType Directory -Path $private -Force | Out-Null
$id=Read-Host 'FishGram api_id'
$parsed=0
if (-not [int]::TryParse($id,[ref]$parsed) -or $parsed -le 0) { throw 'Invalid api_id.' }
$hash=Read-Host 'FishGram api_hash (hidden)' -AsSecureString
$credential=[Management.Automation.PSCredential]::new($id,$hash)
$path=Join-Path $private 'fishgram-api.clixml'
if (Test-Path -LiteralPath $path) { throw 'Existing FishGram identity found; move it to a private backup before replacement.' }
$credential | Export-Clixml -LiteralPath $path
Write-Output 'FishGram identity saved with local user DPAPI protection. Do not copy this file into Git or CI.'
