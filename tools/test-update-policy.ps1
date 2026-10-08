param([string]$SourceRoot='')
$ErrorActionPreference='Stop'
. (Join-Path $PSScriptRoot 'common.ps1')
$root=Split-Path -Parent $PSScriptRoot
$recipe=Get-FishGramRecipe $root
$toolchain=Get-FishGramToolchain $recipe
Enter-FishGramToolchain $recipe $toolchain
if (-not $SourceRoot) { $SourceRoot=Join-Path $root 'tdesktop' }
$out=Join-Path $root 'build-update-tests'
New-Item -ItemType Directory -Path $out -Force | Out-Null
$exe=Join-Path $out 'update-policy-tests.exe'
& cl /nologo /std:c++20 /EHsc /W4 /WX /I (Join-Path $SourceRoot 'Telegram\SourceFiles') (Join-Path $SourceRoot 'Telegram\Tests\fishgram_update\update_policy_tests.cpp') ("/Fe:$exe") ("/Fo:$out\update-policy-tests.obj")
Assert-NativeSuccess 'Compile update policy tests'
& $exe
Assert-NativeSuccess 'Run update policy tests'
Write-Output 'Production update version and payload path tests passed.'
