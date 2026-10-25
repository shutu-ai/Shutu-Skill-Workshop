[CmdletBinding()]
param(
    [ValidateSet('Auto', 'Native', 'Compat')]
    [string]$Mode = 'Auto',
    [ValidateSet('User', 'Project')]
    [string]$Scope = 'User',
    [string]$CodexHome = '',
    [switch]$RemoveMarketplace,
    [switch]$WhatIf
)

$ErrorActionPreference = 'Stop'
$repoRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$oldHome = $env:CODEX_HOME

function Get-CompatServer {
    $saved = $ErrorActionPreference
    $ErrorActionPreference = 'SilentlyContinue'
    $output = & codex mcp get 'shutu-workshop' --json 2>$null
    $exitCode = $LASTEXITCODE
    $ErrorActionPreference = $saved
    if ($exitCode -ne 0) { return $null }
    try { return (($output -join "`n") | ConvertFrom-Json) } catch { return $null }
}

function Test-OwnedCompatServer([object]$Server) {
    if ($null -eq $Server) { return $false }
    $transport = if ($Server.transport) { $Server.transport } else { $Server }
    return ([string]$transport.command -eq 'python' -and ((@($transport.args) -join ' ') -match 'run_mcp\.py'))
}

try {
    if ($Scope -eq 'Project' -and -not $CodexHome) {
        throw 'HOST_UNSUPPORTED: this Codex version has no project-scoped plugin/mcp write command; pass -CodexHome explicitly if an isolated HOME is intended'
    }
    $targetHome = if ($CodexHome) {
        [IO.Path]::GetFullPath($CodexHome)
    } elseif ($Scope -eq 'User') {
        Join-Path ([Environment]::GetFolderPath('UserProfile')) '.codex'
    } else {
        Join-Path $repoRoot '.codex'
    }
    if (-not $WhatIf) { $env:CODEX_HOME = $targetHome }
    $existing = if ($WhatIf) { $null } else { Get-CompatServer }
    if ($WhatIf) {
        [ordered]@{ status = 'WHAT_IF'; mode = $Mode; scope = $Scope; codex_home = $targetHome; removes_compat_mcp = ($Mode -ne 'Native') } | ConvertTo-Json -Depth 8 -Compress
        return
    }
    if ($null -ne $existing) {
        if (-not (Test-OwnedCompatServer $existing)) { throw 'CONFLICT: refusing to remove an unrelated shutu-workshop MCP server' }
        $removeOutput = & codex mcp remove 'shutu-workshop' 2>&1
        if ($LASTEXITCODE -ne 0) { throw "compat MCP removal failed: $removeOutput" }
    }
    $shell = (Get-Command pwsh -ErrorAction SilentlyContinue).Source
    if (-not $shell) { $shell = (Get-Command powershell -ErrorAction Stop).Source }
    $args = @('-NoProfile', '-File', (Join-Path $repoRoot 'scripts\uninstall-m7-plugin.ps1'), '-CodexHome', $targetHome)
    if ($RemoveMarketplace) { $args += '-RemoveMarketplace' }
    $pluginOutput = & $shell @args 2>&1
    if ($LASTEXITCODE -ne 0) { throw "M7 plugin uninstall failed: $pluginOutput" }
    [ordered]@{
        status = 'HOST_VERIFIED'
        mode = $Mode.ToUpperInvariant()
        scope = $Scope
        codex_home = $targetHome
        compat_mcp_removed = ($null -ne $existing)
        marketplace_removed = [bool]$RemoveMarketplace
        note = 'Workshop SQLite data is not removed.'
    } | ConvertTo-Json -Depth 8 -Compress
} finally {
    if ($null -eq $oldHome) { Remove-Item Env:CODEX_HOME -ErrorAction SilentlyContinue } else { $env:CODEX_HOME = $oldHome }
}
