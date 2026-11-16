param(
    [Parameter(Mandatory)]
    [ValidateSet('Rescue', 'Attach', 'Detach')]
    [string]$Direction
)
# Move Libraries, ThirdParty, and build-modified between the checkout and a
# directory outside the runner work folder. Checkout's git clean must not see
# them. Rescue and Detach leave the workspace; Attach brings them back.
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$trees = @('Libraries', 'ThirdParty', 'build-modified')

function Test-PathInside {
    param([string]$Parent, [string]$Child)
    $separator = [IO.Path]::DirectorySeparatorChar
    $parent = $Parent.TrimEnd('\', '/')
    $child = $Child.TrimEnd('\', '/')
    return $child.StartsWith($parent + $separator, [StringComparison]::OrdinalIgnoreCase)
}

function Move-BuildTree {
    param([string]$Source, [string]$Destination)
    if (-not (Test-Path -LiteralPath $Source)) {
        Write-Output "Leave absent: $Source"
        return
    }
    if (Test-Path -LiteralPath $Destination) {
        throw "Refusing to overwrite $Destination because $Source also exists."
    }
    $item = Get-Item -LiteralPath $Source -Force
    if (-not $item.PSIsContainer) { throw "Not a directory: $Source" }
    if ($item.LinkType) { throw "Refusing to move a link: $Source" }
    $parent = Split-Path -Parent $Destination
    if ($parent) { New-Item -ItemType Directory -Force -Path $parent | Out-Null }
    Move-Item -LiteralPath $Source -Destination $Destination
    Write-Output "Moved $Source -> $Destination"
}

$workspace = $env:GITHUB_WORKSPACE
$persistent = $env:FISHGRAM_PERSISTENT_ROOT
if ([string]::IsNullOrWhiteSpace($workspace)) { throw 'GITHUB_WORKSPACE is not set.' }
if ([string]::IsNullOrWhiteSpace($persistent)) { throw 'FISHGRAM_PERSISTENT_ROOT is not set.' }
$workspace = [IO.Path]::GetFullPath($workspace)
$persistent = [IO.Path]::GetFullPath($persistent)
if ($workspace.TrimEnd('\', '/').Equals($persistent.TrimEnd('\', '/'), [StringComparison]::OrdinalIgnoreCase)) {
    throw 'Persistent root cannot be the workspace.'
}
if ((Test-PathInside $workspace $persistent) -or (Test-PathInside $persistent $workspace)) {
    throw 'Persistent root and the workspace must not contain each other.'
}
$workspaceRoot = [IO.Path]::GetPathRoot($workspace)
$persistentRoot = [IO.Path]::GetPathRoot($persistent)
if (-not $workspaceRoot.Equals($persistentRoot, [StringComparison]::OrdinalIgnoreCase)) {
    throw "Persistent root must be on the same volume as the workspace ($workspaceRoot), not $persistentRoot."
}
New-Item -ItemType Directory -Force -Path $persistent | Out-Null

foreach ($name in $trees) {
    $inWorkspace = Join-Path $workspace $name
    $kept = Join-Path $persistent $name
    if ($Direction -eq 'Attach') { Move-BuildTree $kept $inWorkspace }
    else { Move-BuildTree $inWorkspace $kept }
}
