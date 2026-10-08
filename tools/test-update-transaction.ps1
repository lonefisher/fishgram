param([string]$SourceRoot='')
. (Join-Path $PSScriptRoot 'common.ps1')
$root=Split-Path -Parent $PSScriptRoot
$recipe=Get-FishGramRecipe $root
$toolchain=Get-FishGramToolchain $recipe
Enter-FishGramToolchain $recipe $toolchain
if (-not $SourceRoot) { $SourceRoot=Join-Path $root 'tdesktop' }
$out=Join-Path $env:TEMP ('fishgram-update-transaction-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $out | Out-Null
$exe=Join-Path $out 'transaction-tests.exe'
$source=Join-Path $SourceRoot 'Telegram\Tests\fishgram_update\transaction_tests.cpp'
& cl /nologo /std:c++20 /EHsc /W4 /WX /I (Join-Path $SourceRoot 'Telegram\SourceFiles') $source ("/Fe:$exe") ("/Fo:$out\transaction-tests.obj")
Assert-NativeSuccess 'Compile Windows update transaction tests'
& $exe
Assert-NativeSuccess 'Run Windows update transaction tests'
Write-Output 'Windows portable update transaction tests passed.'
