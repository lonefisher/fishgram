param([switch]$Silent)
. (Join-Path $PSScriptRoot 'common.ps1')
$root = Split-Path -Parent $PSScriptRoot
$recipe = Get-FishGramRecipe $root
$toolchain = Get-FishGramToolchain $recipe
Enter-FishGramToolchain $recipe $toolchain
$source = Assert-FishGramSource $root $recipe
Push-Location $root
try {
    $arguments = @((Join-Path $PSScriptRoot 'prepare-compat.py'))
    if ($Silent) { $arguments += 'silent' }
    & $toolchain.python @arguments
    Assert-NativeSuccess 'Dependency preparation'
} finally { Pop-Location }
