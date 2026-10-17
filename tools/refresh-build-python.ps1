. (Join-Path $PSScriptRoot 'common.ps1')
$root = Split-Path -Parent $PSScriptRoot
$recipe = Get-FishGramRecipe $root
$toolchain = Get-FishGramToolchain $recipe
Enter-FishGramToolchain $recipe $toolchain
$null = Assert-FishGramSource $root $recipe
# A cached Windows venv retains its base interpreter's absolute toolcache path.
# Upstream's explicit stage filter recreates only Python; C++ dependencies stay.
$thirdParty = [IO.Path]::GetFullPath((Join-Path $root 'ThirdParty'))
$pythonDirectory = [IO.Path]::GetFullPath((Join-Path $thirdParty 'python'))
if (-not $pythonDirectory.StartsWith($thirdParty + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) { throw 'Invalid Python preparation target.' }
foreach ($directory in @($thirdParty, $pythonDirectory)) {
    if ((Test-Path -LiteralPath $directory) -and ((Get-Item -LiteralPath $directory).Attributes -band [IO.FileAttributes]::ReparsePoint)) { throw 'Python preparation path cannot be a link.' }
}
Push-Location $root
try {
    & $toolchain.python (Join-Path $PSScriptRoot 'prepare-compat.py') silent python
    Assert-NativeSuccess 'Recreate runner-local build Python'
    & (Join-Path $pythonDirectory 'Scripts/python.exe') -c 'import sys, six, mesonbuild; print("Build Python:", sys.version.split()[0])'
    Assert-NativeSuccess 'Verify runner-local build Python'
} finally { Pop-Location }
