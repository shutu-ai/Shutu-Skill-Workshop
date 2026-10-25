[CmdletBinding()]
param(
    [ValidateSet('Auto', 'Native', 'Compat')]
    [string]$Mode = 'Auto',
    [ValidateSet('User', 'Project')]
    [string]$Scope = 'User',
    [string]$CodexHome = '',
    [switch]$Disable,
    [switch]$WhatIf
)

$ErrorActionPreference = 'Stop'
$repoRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$runner = [IO.Path]::GetFullPath((Join-Path $repoRoot 'plugins\shutu-skill-workshop\bin\run_mcp.py'))
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
    $argsText = @($transport.args) -join ' '
    return ([string]$transport.command -eq 'python' -and $argsText -match 'run_mcp\.py')
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
    $dataRoot = Join-Path $targetHome 'plugins\data\shutu-skill-workshop'
    $existing = $null
    if (-not $WhatIf) {
        $env:CODEX_HOME = $targetHome
        $existing = Get-CompatServer
    }

    if ($WhatIf) {
        [ordered]@{
            status = 'WHAT_IF'
            mode = $Mode
            scope = $Scope
            codex_home = $targetHome
            runner = $runner
            would_add_compat_mcp = ($Mode -eq 'Compat')
        } | ConvertTo-Json -Depth 8 -Compress
        return
    }

    $shell = (Get-Command pwsh -ErrorAction SilentlyContinue).Source
    if (-not $shell) { $shell = (Get-Command powershell -ErrorAction Stop).Source }
    $installArgs = @('-NoProfile', '-File', (Join-Path $repoRoot 'scripts\install-m7-plugin.ps1'), '-CodexHome', $targetHome)
    if ($Disable) { $installArgs += '-Disable' }
    $pluginOutput = & $shell @installArgs 2>&1
    if ($LASTEXITCODE -ne 0) { throw "M7 plugin install failed: $pluginOutput" }

    $compatAction = 'not_requested'
    if ($Mode -eq 'Compat') {
        if ($null -ne $existing -and -not (Test-OwnedCompatServer $existing)) {
            throw 'CONFLICT: an unrelated MCP server already uses the name shutu-workshop'
        }
        if ($null -eq $existing) {
            $mcpArgs = @(
                'mcp', 'add', 'shutu-workshop',
                '--env', "SHUTU_WORKSHOP_SOURCE=$repoRoot",
                '--env', "PLUGIN_DATA=$dataRoot",
                '--', 'python', $runner
            )
            $mcpOutput = & codex @mcpArgs 2>&1
            if ($LASTEXITCODE -ne 0) { throw "compat MCP registration failed: $mcpOutput" }
            $compatAction = 'added'
        } else {
            $compatAction = 'already_present'
        }
    } elseif ($Mode -eq 'Native' -and $null -ne $existing) {
        if (-not (Test-OwnedCompatServer $existing)) {
            throw 'CONFLICT: an unrelated MCP server already uses the name shutu-workshop'
        }
        $removeOutput = & codex mcp remove 'shutu-workshop' 2>&1
        if ($LASTEXITCODE -ne 0) { throw "compat MCP removal failed: $removeOutput" }
        $compatAction = 'removed_for_native'
    } elseif ($Mode -eq 'Auto' -and $null -ne $existing) {
        if (-not (Test-OwnedCompatServer $existing)) {
            throw 'CONFLICT: an unrelated MCP server already uses the name shutu-workshop'
        }
        $compatAction = 'already_present'
    }

    $reportedMode = if ($Mode -eq 'Auto') { 'NATIVE_UNVERIFIED' } else { $Mode.ToUpperInvariant() }
    [ordered]@{
        status = 'HOST_VERIFIED'
        mode = $reportedMode
        scope = $Scope
        codex_home = $targetHome
        plugin = 'shutu-skill-workshop@shutu-workshop-local'
        plugin_disabled = [bool]$Disable
        compat_mcp = $compatAction
        source_root = $repoRoot
        plugin_data = $dataRoot
        note = 'Interactive host tool calls still decide Native vs Compat; this command does not fabricate that evidence.'
    } | ConvertTo-Json -Depth 8 -Compress
} finally {
    if ($null -eq $oldHome) { Remove-Item Env:CODEX_HOME -ErrorAction SilentlyContinue } else { $env:CODEX_HOME = $oldHome }
}
