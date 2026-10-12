# Compatibility entry point; only an explicitly confirmed FishGram identity is accepted.
param([switch]$ConfirmOwnApplication)
$ErrorActionPreference = 'Stop'
& (Join-Path $PSScriptRoot 'set-fishgram-api.ps1') -ConfirmOwnApplication:$ConfirmOwnApplication
