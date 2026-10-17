function Invoke-FishGramMemoryDiagnostics {
    param(
        [Parameter(Mandatory)][string]$Python,
        [Parameter(Mandatory)][ValidateSet('build-context','build-failure','build-complete')][string]$Label,
        [Parameter(Mandatory)][string]$Linker,
        [Parameter(Mandatory)][string]$Output
    )
    $ErrorActionPreference = 'Stop'
    $priorExit = Get-Variable -Name LASTEXITCODE -Scope Global -ValueOnly -ErrorAction SilentlyContinue
    try {
        & $Python (Join-Path $PSScriptRoot 'native_memory.py') --label $Label --linker $Linker --output $Output
        if ($LASTEXITCODE -ne 0) { Write-Warning 'Memory measurement failed; the original build result is preserved.' }
    } catch {
        Write-Warning 'Memory measurement could not run; the original build result is preserved.'
    } finally {
        $global:LASTEXITCODE = $priorExit
    }
}
