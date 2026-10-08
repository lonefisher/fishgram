param([int]$Parallel = 2, [switch]$TestIdentity, [switch]$TestUpdateSystem, [switch]$ProductCandidate)
$ErrorActionPreference = 'Stop'
if ($TestUpdateSystem -and -not $TestIdentity) { throw 'Disposable update trust is only permitted with the non-releasable test identity.' }
if ($ProductCandidate -and ($TestIdentity -or $TestUpdateSystem)) { throw 'A product candidate cannot use the test identity or disposable update trust.' }
if ($ProductCandidate -and (-not $env:FISHGRAM_API_ID -or -not $env:FISHGRAM_API_HASH)) { throw 'Protected product candidates require FISHGRAM_API_ID and FISHGRAM_API_HASH from the candidate environment.' }
. (Join-Path $PSScriptRoot 'common.ps1')
$root = Split-Path -Parent $PSScriptRoot
$recipe = Get-FishGramRecipe $root
$toolchain = Get-FishGramToolchain $recipe
Enter-FishGramToolchain $recipe $toolchain
$source = Assert-FishGramSource $root $recipe
if ($Parallel -lt 1) { throw 'Parallel must be positive.' }
$build = Join-Path $root 'build-modified'
$private = Join-Path $root '.private'
New-Item -ItemType Directory -Path $private -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $root 'logs') -Force | Out-Null
$credential = $null
if ($TestIdentity) {
    # Upstream's public test identity: never issue a release built with this identity.
    $apiId = 611335
    $apiHash = 'd524b414d21f4d37f08684c1df41ac9c'
} elseif ($env:FISHGRAM_API_ID -and $env:FISHGRAM_API_HASH) {
    $apiId = 0
    if (-not [int]::TryParse($env:FISHGRAM_API_ID, [ref]$apiId) -or $apiId -le 0) { throw 'Invalid application identity.' }
    $apiHash = $env:FISHGRAM_API_HASH
} else {
    if ($ProductCandidate) { throw 'Protected product candidates cannot read the local DPAPI identity.' }
    $credential = Import-Clixml -LiteralPath (Join-Path $private 'fishgram-api.clixml')
    if ($credential -isnot [Management.Automation.PSCredential]) { throw 'Invalid encrypted identity.' }
    $apiId = 0
    if (-not [int]::TryParse($credential.UserName, [ref]$apiId) -or $apiId -le 0) { throw 'Invalid application identity.' }
    $apiHash = $credential.GetNetworkCredential().Password
}
if ($apiHash -notmatch '^[a-fA-F0-9]{32}$') { throw 'Invalid application identity.' }
$cache = Join-Path $private ('api-' + [guid]::NewGuid().ToString('N') + '.cmake')
try {
    $contents = 'set(TDESKTOP_API_ID "' + $apiId + '" CACHE STRING "" FORCE)' + "`n" + 'set(TDESKTOP_API_HASH "' + $apiHash + '" CACHE STRING "" FORCE)' + "`n"
    Set-Content -LiteralPath $cache -Value $contents -Encoding ascii
    $autoUpdate = [bool]($recipe.autoUpdate -or $TestUpdateSystem -or $ProductCandidate)
    $disabled = if ($autoUpdate) { 'OFF' } else { 'ON' }
    $trust = Join-Path $root 'config/update-trust'
    if ($ProductCandidate) {
        foreach ($name in @('root-public.pem', 'issuer-public.pem', 'manifest.min.json', 'manifest.sig')) {
            if (-not (Test-Path -LiteralPath (Join-Path $trust $name) -PathType Leaf)) { throw ('Product update trust is incomplete: ' + $name) }
        }
        if ((Get-ChildItem -LiteralPath $trust -Force | Where-Object { $_.Name -match 'test|fixture|private|secret' }).Count -gt 0) { throw 'Product update trust contains forbidden test or private material.' }
    }
    if ($TestUpdateSystem) {
        $trust = Join-Path $private ('build-test-trust-' + [guid]::NewGuid().ToString('N'))
        & $toolchain.python (Join-Path $root 'tests/create_update_trust_fixture.py') $trust
        Assert-NativeSuccess 'Generate disposable public update trust for CI compilation'
    }
    & $toolchain.cmake -S $source -B $build -G 'Ninja Multi-Config' -C $cache -D "DESKTOP_APP_DISABLE_AUTOUPDATE=$disabled" -D DESKTOP_APP_DISABLE_CRASH_REPORTS=ON -D "FISHGRAM_REVISION=$($recipe.revision)" -D "TDESKTOP_UPDATE_CHANNEL=$($recipe.channel)" -D "FISHGRAM_UPDATE_TRUST_DIRECTORY=$trust" -D CMAKE_CONFIGURATION_TYPES=Release *> (Join-Path $root 'logs\configure.log')
    $configureExit = $LASTEXITCODE
    if ($configureExit -ne 0 -and $TestIdentity) {
        & $toolchain.python (Join-Path $PSScriptRoot 'build_diagnostics.py') (Join-Path $root 'logs/configure.log') (Join-Path $root 'reports/configure-diagnostics.json')
    }
    $global:LASTEXITCODE = $configureExit
    Assert-NativeSuccess 'Configure; inspect private logs locally'
    $targets = @('Telegram', 'Updater')
    if ($ProductCandidate) { $targets += 'Packer' }
    & $toolchain.cmake --build $build --config Release --target $targets --parallel $Parallel *> (Join-Path $root 'logs\build.log')
    $compileExit = $LASTEXITCODE
    if ($compileExit -ne 0 -and $TestIdentity) {
        # Only codes and source basenames may leave the runner. Raw compiler
        # output and command lines can contain application identity settings.
        & $toolchain.python (Join-Path $PSScriptRoot 'build_diagnostics.py') (Join-Path $root 'logs/build.log') (Join-Path $root 'reports/build-diagnostics.json')
    }
    $global:LASTEXITCODE = $compileExit
    Assert-NativeSuccess 'Build; inspect private logs locally'
    $identity = if ($TestIdentity) { 'test' } else { 'product' }
    $record = [ordered]@{ version = (Get-FishGramVersion $recipe); channel = $recipe.channel; identity = $identity; productCandidate = [bool]$ProductCandidate; recipeAutoUpdate = [bool]$recipe.autoUpdate; parentCommit = (& git -C $root rev-parse HEAD); sourceCommit = (& git -C $source rev-parse HEAD); toolchain = @{ msvc = $toolchain.msvc; sdk = $toolchain.sdk; qt = $recipe.qt }; autoUpdate = $autoUpdate; testUpdateTrust = [bool]$TestUpdateSystem }
    # Bind packaging to bytes produced by this build, before any QA or signing.
    $record.files = [ordered]@{}
    foreach ($name in @($recipe.payloadFiles) + @($recipe.payloadDlls)) {
        $file = Join-Path (Join-Path $build 'Release') $name
        if (Test-Path -LiteralPath $file -PathType Leaf) {
            $record.files[$name] = @{ size = (Get-Item -LiteralPath $file).Length; sha256 = (Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash.ToLowerInvariant() }
        }
    }
    if ($ProductCandidate) {
        foreach ($name in @($recipe.payloadFiles) + @($recipe.payloadDlls)) {
            if (-not $record.files.Contains($name)) { throw ('Build output is missing ' + $name) }
        }
        $packer = Join-Path (Join-Path $build 'Release') 'Packer.exe'
        if (-not (Test-Path -LiteralPath $packer -PathType Leaf)) { throw 'Build output is missing the reviewed signing tool.' }
        $record.tools = [ordered]@{ 'Packer.exe' = @{ size = (Get-Item -LiteralPath $packer).Length; sha256 = (Get-FileHash -LiteralPath $packer -Algorithm SHA256).Hash.ToLowerInvariant() } }
    }
    if (-not $record.files.Contains('Telegram.exe')) { throw 'Build output is missing the client.' }
    $record | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $build 'build-record.json') -Encoding utf8
    Write-Output ('Built internal candidate ' + $record.version + ' (' + $identity + ' identity).')
} finally {
    $apiHash = $null; $contents = $null; $credential = $null
    if (Test-Path -LiteralPath $cache) { Remove-Item -LiteralPath $cache }
}
