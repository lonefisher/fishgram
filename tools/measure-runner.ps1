param([Parameter(Mandatory)][ValidatePattern('^[a-z-]+$')][string]$Label)
$ErrorActionPreference='Stop'
$root=Split-Path -Parent $PSScriptRoot
$drive=Get-PSDrive -Name ([IO.Path]::GetPathRoot($root).Substring(0,1))
$record=[ordered]@{ label=$Label; utc=[DateTime]::UtcNow.ToString('o'); freeBytes=$drive.Free; usedBytes=$drive.Used; logicalProcessors=[Environment]::ProcessorCount; directories=@{} }
foreach ($directory in @('Libraries','ThirdParty','build-modified')) {
    $path=Join-Path $root $directory
    if (Test-Path -LiteralPath $path) {
        $size=(Get-ChildItem -LiteralPath $path -File -Recurse | Measure-Object -Property Length -Sum).Sum
        $record.directories[$directory]=[long]$size
    }
}
New-Item -ItemType Directory -Path (Join-Path $root 'reports') -Force | Out-Null
$record | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $root ('reports\runner-'+$Label+'.json')) -Encoding utf8
$record | ConvertTo-Json -Depth 4
