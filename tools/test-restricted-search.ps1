param([string]$SourceRoot = '', [string]$OutputDirectory = '')
. (Join-Path $PSScriptRoot 'common.ps1')
$root = Split-Path -Parent $PSScriptRoot
$recipe = Get-FishGramRecipe $root
$toolchain = Get-FishGramToolchain $recipe
Enter-FishGramToolchain $recipe $toolchain
if (-not $SourceRoot) { $SourceRoot = Join-Path $root 'tdesktop' }
if (-not $OutputDirectory) { $OutputDirectory = Join-Path $root 'build-tests' }
New-Item -ItemType Directory -Path $OutputDirectory -Force | Out-Null
$tests = @(Get-ChildItem -LiteralPath (Join-Path $SourceRoot 'Telegram\Tests\restricted_search') -Filter '*_tests.cpp' -File)
if ($tests.Count -ne 5) { throw 'Expected the five reviewed production helper tests.' }
foreach ($test in $tests) {
    $exe = Join-Path $OutputDirectory ($test.BaseName + '.exe')
    $obj = Join-Path $OutputDirectory ($test.BaseName + '.obj')
    & cl /nologo /std:c++20 /EHsc /W4 /WX /I (Join-Path $SourceRoot 'Telegram\SourceFiles') $test.FullName ("/Fe:$exe") ("/Fo:$obj")
    Assert-NativeSuccess ('Compile ' + $test.Name)
    & $exe
    Assert-NativeSuccess ('Run ' + $test.Name)
}
Write-Output ('Passed ' + $tests.Count + ' production helper tests.')
