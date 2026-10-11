$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'common.ps1')
$root = Split-Path -Parent $PSScriptRoot
$recipe = Get-FishGramRecipe $root
$options = @(Get-FishGramReleaseCmakeOptions $recipe)
if ($options.Count -ne 1 -or $options[0] -ne '-DCMAKE_MSVC_DEBUG_INFORMATION_FORMAT=') { throw 'Release recipe must explicitly disable debug information.' }
$toolchain = Get-FishGramToolchain $recipe
Enter-FishGramToolchain $recipe $toolchain
$probe = Join-Path $root ('.private/debug-policy-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $probe -Force | Out-Null
$cmakeSource = @'
cmake_minimum_required(VERSION 3.25)
project(FishGramDebugPolicy LANGUAGES CXX)
set(DESKTOP_APP_SPECIAL_TARGET "")
set(build_win64 1)
set(build_winarm 0)
set(build_winstore 0)
include("${FISHGRAM_SOURCE}/cmake/variables.cmake")
add_library(common_options INTERFACE)
include("${FISHGRAM_SOURCE}/cmake/options_win.cmake")
add_executable(probe main.cpp)
target_link_libraries(probe PRIVATE common_options)
'@
Set-Content -LiteralPath (Join-Path $probe 'CMakeLists.txt') -Value $cmakeSource -Encoding ascii
Set-Content -LiteralPath (Join-Path $probe 'main.cpp') -Value 'int main() { return 0; }' -Encoding ascii
foreach ($mode in @('Embedded', 'None')) {
    $build = Join-Path $probe $mode
    $debugOptions = @(if ($mode -eq 'None') { $options } else { '-DCMAKE_MSVC_DEBUG_INFORMATION_FORMAT=Embedded' })
    & $toolchain.cmake -S $probe -B $build -G Ninja '-DCMAKE_BUILD_TYPE=Release' "-DFISHGRAM_SOURCE=$root/tdesktop" @debugOptions *> (Join-Path $probe "configure-$mode.log")
    Assert-NativeSuccess "Configure $mode native probe"
    & $toolchain.cmake --build $build --verbose *> (Join-Path $probe "build-$mode.log")
    Assert-NativeSuccess "Build $mode native probe"
    $commands = Get-Content -LiteralPath (Join-Path $probe "build-$mode.log") -Raw
    if ($mode -eq 'Embedded') {
        if ($commands -notmatch '[/-]Z7' -or $commands -notmatch '/DEBUG(\s|\b)') { throw 'Baseline probe did not reproduce upstream debug flags.' }
    } else {
        if ($commands -match '[/-]Z7|[/-]Zi|[/-]ZI' -or $commands -notmatch '/DEBUG:NONE') { throw 'Release probe still generates debug information.' }
        if (Test-Path -LiteralPath (Join-Path $build 'probe.pdb')) { throw 'Unexpected release PDB.' }
    }
    & (Join-Path $build 'probe.exe')
    Assert-NativeSuccess "Run $mode native probe"
}
Write-Output 'PASS: upstream Embedded and FishGram None flags verified by actual MSVC compile/link/run.'
