param([Parameter(Mandatory)][string]$InputDirectory, [Parameter(Mandatory)][string]$BuildRecord, [Parameter(Mandatory)][string]$OutputDirectory)
. (Join-Path $PSScriptRoot 'common.ps1')
$root = Split-Path -Parent $PSScriptRoot
$recipe = Get-FishGramRecipe $root
$toolchain = Get-FishGramToolchain $recipe
$null = Assert-FishGramSource $root $recipe
& $toolchain.python (Join-Path $PSScriptRoot 'release_tools.py') package --root $root --payload $InputDirectory --record $BuildRecord --output $OutputDirectory
Assert-NativeSuccess 'Candidate packaging'
