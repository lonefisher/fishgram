Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Get-FishGramRecipe {
    param([string]$Root = (Split-Path -Parent $PSScriptRoot))
    $recipe = Get-Content -Raw -LiteralPath (Join-Path $Root 'fishgram.json') | ConvertFrom-Json
    if ($recipe.schema -ne 1 -or $recipe.product -ne 'FishGram' -or $recipe.platform -ne 'windows-x64') { throw 'Unsupported build recipe.' }
    if ($recipe.upstreamVersion -notmatch '^\d+\.\d+\.\d+$' -or $recipe.revision -le 0 -or $recipe.revision -gt [uint32]::MaxValue) { throw 'Invalid version.' }
    if ($recipe.channel -notin @('stable', 'beta')) { throw 'Unsupported channel.' }
    return $recipe
}

function Get-FishGramVersion {
    param($Recipe)
    return ($Recipe.upstreamVersion + '-r' + $Recipe.revision)
}

function Assert-NativeSuccess {
    param([string]$Action)
    if ($LASTEXITCODE -ne 0) { throw ($Action + ' failed (exit ' + $LASTEXITCODE + ').') }
}

function Get-FishGramToolchain {
    param($Recipe)
    # Optional machine overrides are never committed. No API identity belongs here.
    $root = Split-Path -Parent $PSScriptRoot
    $overrides = @{}
    $localFile = Join-Path $root 'tools.local.json'
    if (Test-Path -LiteralPath $localFile) {
        $local = Get-Content -Raw -LiteralPath $localFile | ConvertFrom-Json
        foreach ($entry in $local.PSObject.Properties) {
            if ($entry.Name -notin @('visualStudio', 'cmake', 'ninja', 'python')) { throw 'Unknown local tool override.' }
            $overrides[$entry.Name] = [string]$entry.Value
        }
    }
    if ($overrides.ContainsKey('visualStudio')) { $vs = $overrides.visualStudio }
    else {
        $finder = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\vswhere.exe'
        if (-not (Test-Path -LiteralPath $finder)) { throw 'Install Visual Studio Build Tools with C++ tools.' }
        $vs = & $finder -latest -products '*' -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
        Assert-NativeSuccess 'Visual Studio discovery'
    }
    if (-not $vs) { throw 'No supported Visual Studio installation found.' }
    $vcvars = Join-Path $vs 'VC\Auxiliary\Build\vcvars64.bat'
    $versions = @(Get-ChildItem -LiteralPath (Join-Path $vs 'VC\Tools\MSVC') -Directory | Where-Object { $_.Name.StartsWith($Recipe.msvc + '.') })
    if ($versions.Count -eq 0 -or -not (Test-Path -LiteralPath $vcvars)) { throw ('Required MSVC ' + $Recipe.msvc + ' is missing.') }
    $sdk = Join-Path ${env:ProgramFiles(x86)} ('Windows Kits\10\Include\' + $Recipe.windowsSdk)
    if (-not (Test-Path -LiteralPath $sdk)) { throw ('Required SDK ' + $Recipe.windowsSdk + ' is missing.') }
    $resolved = @{ visualStudio = $vs; vcvars = $vcvars; msvc = ($versions | Sort-Object Name -Descending | Select-Object -First 1).Name; sdk = $Recipe.windowsSdk }
    foreach ($name in @('cmake', 'ninja', 'python')) {
        $command = Get-Command $name -ErrorAction SilentlyContinue
        $candidate = if ($overrides.ContainsKey($name)) { $overrides[$name] } elseif ($command) { $command.Source } elseif ($name -eq 'cmake') { Join-Path $vs 'Common7\IDE\CommonExtensions\Microsoft\CMake\CMake\bin\cmake.exe' } elseif ($name -eq 'ninja') { Join-Path $vs 'Common7\IDE\CommonExtensions\Microsoft\CMake\Ninja\ninja.exe' } else { '' }
        if (-not $candidate -or -not (Test-Path -LiteralPath $candidate -PathType Leaf)) { throw ('Required tool not found: ' + $name) }
        $resolved[$name] = $candidate
    }
    return $resolved
}

function Enter-FishGramToolchain {
    param($Recipe, $Toolchain)
    $setup = '"' + $Toolchain.vcvars + '" ' + $Recipe.windowsSdk + ' -vcvars_ver=' + $Recipe.msvc + ' >nul && set'
    $variables = & $env:ComSpec /d /s /c $setup
    Assert-NativeSuccess 'MSVC environment setup'
    $nativePath = $null
    foreach ($line in $variables) {
        if ($line -match '^([^=]+)=(.*)$') {
            $variableName = $Matches[1]
            $variableValue = $Matches[2]
            if ($variableName -ieq 'PATH') {
                # Some launch environments contain both PATH and Path. Prefer the
                # vcvars result rather than a duplicate inherited value.
                if (-not $nativePath -or $variableValue -match '\\VC\\Tools\\MSVC\\') { $nativePath = $variableValue }
            }
            else { Set-Item -LiteralPath ('Env:' + $variableName) -Value $variableValue }
        }
    }
    if (-not $nativePath) { throw 'MSVC environment did not return PATH.' }
    $env:PATH = ((@('cmake', 'ninja', 'python') | ForEach-Object { Split-Path -Parent $Toolchain[$_] }) -join ';') + ';' + $nativePath
    $env:QT = $Recipe.qt
}

function Assert-FishGramSource {
    param([string]$Root, $Recipe)
    $source = Join-Path $Root 'tdesktop'
    $versionFile = Join-Path $source 'Telegram\SourceFiles\core\version.h'
    $version = Get-Content -Raw -LiteralPath $versionFile
    if ($version -notmatch ('AppVersionStr\s*=\s*"' + [regex]::Escape($Recipe.upstreamVersion) + '"')) { throw 'Source version does not match the recipe.' }
    $pointer = & git -c core.longpaths=true -C $Root ls-files --stage -- tdesktop
    Assert-NativeSuccess 'Parent source pointer'
    $head = & git -c core.longpaths=true -C $source rev-parse HEAD
    Assert-NativeSuccess 'Source identity'
    if ($pointer -notmatch ('^160000 ' + [regex]::Escape($head) + ' 0\s+tdesktop$')) { throw 'Source checkout does not match the parent gitlink.' }
    $dirty = @(& git -c core.longpaths=true -C $source status --porcelain --untracked-files=no)
    Assert-NativeSuccess 'Source cleanliness'
    if ($dirty.Count -ne 0) { throw 'Source has uncommitted tracked changes.' }
    return $source
}
