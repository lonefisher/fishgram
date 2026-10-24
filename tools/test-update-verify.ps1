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
$qtMajor=([version]$recipe.qt).Major
$qtConfig=Join-Path $qt ("lib/cmake/Qt$qtMajor")
if (-not (Test-Path -LiteralPath (Join-Path $qtConfig "Qt${qtMajor}Config.cmake") -PathType Leaf)) {
    throw 'Recipe Qt package is missing. Prepare the matching dependencies.'
}
$out=Join-Path $root ('build-update-verify-tests/Qt-'+$recipe.qt)
$logs=Join-Path $root 'logs'
New-Item -ItemType Directory -Path $logs -Force | Out-Null
$fixture=Join-Path $root ('.private/test-trust-' + [guid]::NewGuid().ToString('N'))
& $toolchain.python (Join-Path $root 'tests/create_update_trust_fixture.py') $fixture
Assert-NativeSuccess 'Generate disposable public trust fixture'
& $toolchain.cmake -S (Join-Path $root 'tests/update_cpp') -B $out -G Ninja -D CMAKE_BUILD_TYPE=Release -D "SOURCE_ROOT=$SourceRoot" -D "CMAKE_PREFIX_PATH=$qt" -D "QT_DIR=$qtConfig" -D "Qt${qtMajor}_DIR=$qtConfig" -D "FISHGRAM_QT_VERSION=$($recipe.qt)" -D "DEPENDENCY_ROOT=$DependencyRoot" -D "TEST_TRUST_DIRECTORY=$($fixture.Replace('\','/'))" *> (Join-Path $logs 'configure-update-tests.log')
$configureExit=$LASTEXITCODE
if ($configureExit -ne 0) {
    & $toolchain.python (Join-Path $PSScriptRoot 'build_diagnostics.py') (Join-Path $logs 'configure-update-tests.log') (Join-Path $root 'reports/configure-update-tests-diagnostics.json')
}
$global:LASTEXITCODE=$configureExit
Assert-NativeSuccess 'Configure production update tests'
& $toolchain.cmake --build $out --parallel 2 *> (Join-Path $logs 'build-update-tests.log')
$compileExit=$LASTEXITCODE
if ($compileExit -ne 0) {
    & $toolchain.python (Join-Path $PSScriptRoot 'build_diagnostics.py') (Join-Path $logs 'build-update-tests.log') (Join-Path $root 'reports/build-update-tests-diagnostics.json')
}
$global:LASTEXITCODE=$compileExit
Assert-NativeSuccess 'Compile production update tests'
& (Join-Path $out 'failure_tests.exe')
Assert-NativeSuccess 'Run update failure classification tests'
& (Join-Path $out 'identity_tests.exe')
Assert-NativeSuccess 'Run FishGram Windows application identity tests'
& (Join-Path $out 'restart_tests.exe')
Assert-NativeSuccess 'Run updated-client restart privilege tests'
& (Join-Path $out 'data_snapshot_tests.exe')
Assert-NativeSuccess 'Run synthetic native account snapshot tests'
$env:FISHGRAM_NATIVE_SNAPSHOT_TEST=Join-Path $out 'data_snapshot_tests.exe'
try {
    & $toolchain.python -m unittest discover -s (Join-Path $root 'tests') -p test_data_recovery.py -v
    Assert-NativeSuccess 'Run native snapshot and Python recovery interoperability tests'
} finally {
    Remove-Item Env:FISHGRAM_NATIVE_SNAPSHOT_TEST
}
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
