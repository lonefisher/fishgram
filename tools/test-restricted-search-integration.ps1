param([string]$SourceRoot = '', [string]$OutputDirectory = '')
. (Join-Path $PSScriptRoot 'common.ps1')
$root = Split-Path -Parent $PSScriptRoot
$recipe = Get-FishGramRecipe $root
$toolchain = Get-FishGramToolchain $recipe
Enter-FishGramToolchain $recipe $toolchain
if (-not $SourceRoot) { $SourceRoot = Join-Path $root 'tdesktop' }
if (-not $OutputDirectory) { $OutputDirectory = Join-Path $root 'build-search-integration-tests' }
$test = Join-Path $SourceRoot 'Telegram\Tests\restricted_search_integration\async_lifecycle_harness.cpp'
if (-not (Test-Path -LiteralPath $test -PathType Leaf)) { throw 'Production restricted-search core harness source is missing.' }
New-Item -ItemType Directory -Path $OutputDirectory -Force | Out-Null
$exe = Join-Path $OutputDirectory 'restricted_search_async_lifecycle_harness.exe'
$obj = Join-Path $OutputDirectory 'restricted_search_async_lifecycle_harness.obj'
& cl /nologo /std:c++20 /EHsc /W4 /WX /I (Join-Path $SourceRoot 'Telegram\SourceFiles') $test ("/Fe:$exe") ("/Fo:$obj")
Assert-NativeSuccess 'Compile production restricted-search core integration harness'
& $exe
Assert-NativeSuccess 'Run production restricted-search core integration harness'
