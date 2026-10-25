[CmdletBinding()]
param(
    [ValidateSet('Enable', 'Disable')]
    [string]$Mode = 'Enable',
    [ValidateSet('User', 'Project')]
    [string]$Scope = 'User',
    [string]$CodexHome = '',
    [switch]$WhatIf
)

$ErrorActionPreference = 'Stop'
$repoRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$observer = [IO.Path]::GetFullPath((Join-Path $repoRoot 'scripts\m72_stdio_observer.py'))
$runner = [IO.Path]::GetFullPath((Join-Path $repoRoot 'plugins\shutu-skill-workshop\bin\run_mcp.py'))
$oldHome = $env:CODEX_HOME

function Get-Server {
    $saved = $ErrorActionPreference
    $ErrorActionPreference = 'SilentlyContinue'
    $output = & codex mcp get 'shutu-workshop' --json 2>$null
    $exitCode = $LASTEXITCODE
    $ErrorActionPreference = $saved
    if ($exitCode -ne 0) { return $null }
    try { return (($output -join "`n") | ConvertFrom-Json) } catch { return $null }
}

function Get-Transport([object]$Server) {
    if ($null -eq $Server) { return $null }
    return $(if ($Server.transport) { $Server.transport } else { $Server })
}

function Get-EnvMap([object]$Transport) {
    $map = @{}
    if ($null -ne $Transport -and $null -ne $Transport.env) {
        foreach ($property in $Transport.env.PSObject.Properties) { $map[[string]$property.Name] = [string]$property.Value }
    }
    return $map
}

function Normalize([string]$Value) {
    if (-not $Value) { return '' }
    try { return [IO.Path]::GetFullPath($Value).TrimEnd('\').ToLowerInvariant() } catch { return $Value.TrimEnd('\').ToLowerInvariant() }
}

function Test-Compat([object]$Server) {
    $transport = Get-Transport $Server
    if ($null -eq $transport -or [string]$transport.command -ne 'python') { return $false }
    $args = @($transport.args)
    return ($args.Count -eq 1 -and (Normalize ([string]$args[0])) -eq (Normalize $runner))
}

function Test-Observer([object]$Server) {
    $transport = Get-Transport $Server
    if ($null -eq $transport -or [string]$transport.command -ne 'python') { return $false }
    $args = @($transport.args)
    $expected = @($observer, '--server-name', 'shutu-workshop', '--startup-timeout', '10', '--', 'python', $runner)
    if ($args.Count -ne $expected.Count) { return $false }
    for ($index = 0; $index -lt $expected.Count; $index++) {
        if ($index -in @(0, 7)) {
            if ((Normalize ([string]$args[$index])) -ne (Normalize ([string]$expected[$index]))) { return $false }
        } elseif ([string]$args[$index] -ne [string]$expected[$index]) {
            return $false
        }
    }
    return $true
}

function Save-Manifest([object]$Server, [string]$Path) {
    $transport = Get-Transport $Server
    $manifest = [ordered]@{
        version = 1
        name = 'shutu-workshop'
        command = [string]$transport.command
        args = @($transport.args)
        env = Get-EnvMap $transport
        cwd = [string]$transport.cwd
    }
    New-Item -ItemType Directory -Force -Path (Split-Path $Path -Parent) | Out-Null
    $manifest | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $Path -Encoding utf8
}

function Read-Manifest([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { throw 'M72 observer manifest is missing; refusing to remove an untracked observer' }
    return Get-Content -LiteralPath $Path -Raw -Encoding utf8 | ConvertFrom-Json
}

function Add-RegisteredServer([object]$Definition) {
    $mcpArgs = @('mcp', 'add', 'shutu-workshop')
    if ($Definition.env) {
        if ($Definition.env -is [System.Collections.IDictionary]) {
            foreach ($entry in $Definition.env.GetEnumerator()) { $mcpArgs += @('--env', ("{0}={1}" -f $entry.Key, [string]$entry.Value)) }
        } else {
            foreach ($property in $Definition.env.PSObject.Properties) { $mcpArgs += @('--env', ("{0}={1}" -f $property.Name, [string]$property.Value)) }
        }
    }
    $mcpArgs += @('--', [string]$Definition.command)
    $mcpArgs += @($Definition.args)
    $output = & codex @mcpArgs 2>&1
    if ($LASTEXITCODE -ne 0) { throw "MCP registration failed: $output" }
}

try {
    if ($Scope -eq 'Project' -and -not $CodexHome) {
        throw 'HOST_UNSUPPORTED: pass -CodexHome for an isolated project scope'
    }
    $targetHome = if ($CodexHome) { [IO.Path]::GetFullPath($CodexHome) } elseif ($Scope -eq 'User') { Join-Path ([Environment]::GetFolderPath('UserProfile')) '.codex' } else { Join-Path $repoRoot '.codex' }
    $manifestPath = Join-Path $targetHome 'plugins\data\shutu-skill-workshop\m72-observer-manifest.json'
    if ($WhatIf) {
        [ordered]@{ status = 'WHAT_IF'; mode = $Mode; scope = $Scope; codex_home = $targetHome; observer = $observer; child = $runner; manifest = $manifestPath } | ConvertTo-Json -Depth 8 -Compress
        return
    }

    $env:CODEX_HOME = $targetHome
    $existing = Get-Server
    if ($Mode -eq 'Enable') {
        if (Test-Observer $existing) {
            [ordered]@{ status = 'HOST_VERIFIED'; mode = 'ENABLE'; action = 'already_present'; codex_home = $targetHome; manifest = $manifestPath } | ConvertTo-Json -Depth 8 -Compress
            return
        }
        if (-not (Test-Compat $existing)) {
            if ($null -eq $existing) { throw 'TARGET_NOT_FOUND: register the owned Workshop Compat MCP first' }
            throw 'CONFLICT: shutu-workshop is not the exact owned Compat MCP; refusing to overwrite it'
        }
        Save-Manifest $existing $manifestPath
        & codex mcp remove 'shutu-workshop' 2>&1 | Out-Null
        if ($LASTEXITCODE -ne 0) { throw 'failed to remove the owned Compat MCP before Observer registration' }
        try {
            $definition = [pscustomobject]@{
                command = 'python'
                args = @($observer, '--server-name', 'shutu-workshop', '--startup-timeout', '10', '--', 'python', $runner)
                env = Get-EnvMap (Get-Transport $existing)
            }
            Add-RegisteredServer $definition
        } catch {
            try { Add-RegisteredServer (Read-Manifest $manifestPath) } catch { throw "Observer registration failed and Compat rollback failed: $($_.Exception.Message)" }
            throw
        }
        [ordered]@{ status = 'HOST_VERIFIED'; mode = 'ENABLE'; action = 'replaced_compat'; codex_home = $targetHome; manifest = $manifestPath; observer = $observer; child = $runner } | ConvertTo-Json -Depth 8 -Compress
        return
    }

    if (-not (Test-Observer $existing)) { throw 'NOT_ACTIVE: the owned M7.2 Observer registration is not active' }
    $manifest = Read-Manifest $manifestPath
    & codex mcp remove 'shutu-workshop' 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'failed to remove the owned Observer registration' }
    try {
        Add-RegisteredServer $manifest
        Remove-Item -LiteralPath $manifestPath -Force
    } catch {
        try {
            $current = [pscustomobject]@{ command = 'python'; args = @($existing.transport.args); env = Get-EnvMap (Get-Transport $existing) }
            Add-RegisteredServer $current
        } catch { throw "Compat restore failed after Observer removal: $($_.Exception.Message)" }
        throw
    }
    [ordered]@{ status = 'HOST_VERIFIED'; mode = 'DISABLE'; action = 'restored_compat'; codex_home = $targetHome; manifest_removed = $true } | ConvertTo-Json -Depth 8 -Compress
} finally {
    if ($null -eq $oldHome) { Remove-Item Env:CODEX_HOME -ErrorAction SilentlyContinue } else { $env:CODEX_HOME = $oldHome }
}
