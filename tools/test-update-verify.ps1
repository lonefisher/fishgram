param([string]$DependencyRoot='', [string]$SourceRoot='')
$ErrorActionPreference='Stop'
. (Join-Path $PSScriptRoot 'common.ps1')
$root=Split-Path -Parent $PSScriptRoot
$recipe=Get-FishGramRecipe $root
$toolchain=Get-FishGramToolchain $recipe
Enter-FishGramToolchain $recipe $toolchain
if (-not $DependencyRoot) { $DependencyRoot=$root }
if (-not $SourceRoot) { $SourceRoot=Join-Path $root 'tdesktop' }
$DependencyRoot=(Resolve-Path -LiteralPath $DependencyRoot).ProviderPath
$SourceRoot=(Resolve-Path -LiteralPath $SourceRoot).ProviderPath
$SourceRoot=$SourceRoot.Replace('\','/')
$DependencyRoot=$DependencyRoot.Replace('\','/')
$qt=Join-Path $DependencyRoot ('Libraries/win64/Qt-'+$recipe.qt)
$out=Join-Path $root 'build-update-verify-tests'
$fixture=Join-Path $root ('.private/test-trust-' + [guid]::NewGuid().ToString('N'))
& $toolchain.python (Join-Path $root 'tests/create_update_trust_fixture.py') $fixture
Assert-NativeSuccess 'Generate disposable public trust fixture'
& $toolchain.cmake -S (Join-Path $root 'tests/update_cpp') -B $out -G Ninja -D CMAKE_BUILD_TYPE=Release -D "SOURCE_ROOT=$SourceRoot" -D "CMAKE_PREFIX_PATH=$qt" -D "DEPENDENCY_ROOT=$DependencyRoot" -D "TEST_TRUST_DIRECTORY=$($fixture.Replace('\','/'))"
Assert-NativeSuccess 'Configure production update tests'
& $toolchain.cmake --build $out --parallel 2
Assert-NativeSuccess 'Compile production update tests'
& (Join-Path $out 'restart_tests.exe')
Assert-NativeSuccess 'Run updated-client restart privilege tests'
& (Join-Path $out 'feed_tests.exe')
Assert-NativeSuccess 'Run production update feed tests'
& (Join-Path $out 'payload_tests.exe')
Assert-NativeSuccess 'Run verified Windows payload tests'
& (Join-Path $out 'verify_tests.exe')
Assert-NativeSuccess 'Run production v2 signature tests'
& (Join-Path $out 'verify_embedded.exe')
Assert-NativeSuccess 'Run configured FishGram trust tests'
$env:FISHGRAM_PACKER=Join-Path $out 'packer_tests.exe'
$env:FISHGRAM_PACKAGE_VERIFY=Join-Path $out 'package_verify.exe'
try {
    & $toolchain.python -m unittest discover -s (Join-Path $root 'tests') -p test_update_package.py -v
    Assert-NativeSuccess 'Run production signed package roundtrip tests'
} finally {
    Remove-Item Env:FISHGRAM_PACKER, Env:FISHGRAM_PACKAGE_VERIFY
}
