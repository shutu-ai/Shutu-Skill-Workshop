[CmdletBinding()]
param(
    [string]$CodexHome = '',
    [switch]$RemoveMarketplace
)

$ErrorActionPreference = 'Stop'
$repoRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$oldHome = $env:CODEX_HOME

try {
    if ($CodexHome) {
        $env:CODEX_HOME = [IO.Path]::GetFullPath($CodexHome)
        New-Item -ItemType Directory -Force -Path $env:CODEX_HOME | Out-Null
    }
    $pluginOutput = & codex plugin remove 'shutu-skill-workshop@shutu-workshop-local' --json 2>&1
    $pluginExit = $LASTEXITCODE
    $marketplaceOutput = @()
    if ($RemoveMarketplace) {
        $marketplaceOutput = & codex plugin marketplace remove 'shutu-workshop-local' --json 2>&1
        if ($LASTEXITCODE -ne 0) { throw "codex marketplace remove failed: $marketplaceOutput" }
    }
    [ordered]@{
        status = if ($pluginExit -eq 0) { 'HOST_VERIFIED' } else { 'PARTIAL' }
        plugin_exit_code = $pluginExit
        marketplace_removed = [bool]$RemoveMarketplace
        plugin_output = (@($pluginOutput) -join "`n")
        marketplace_output = (@($marketplaceOutput) -join "`n")
    } | ConvertTo-Json -Depth 8 -Compress
} finally {
    if ($null -eq $oldHome) { Remove-Item Env:CODEX_HOME -ErrorAction SilentlyContinue } else { $env:CODEX_HOME = $oldHome }
}
