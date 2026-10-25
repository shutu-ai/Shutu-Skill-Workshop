[CmdletBinding()]
param(
    [string]$PluginRoot = '',
    [switch]$Json
)

$ErrorActionPreference = 'Stop'
if (-not $PluginRoot) { $PluginRoot = Join-Path $PSScriptRoot '..\plugins\shutu-skill-workshop' }
$root = [IO.Path]::GetFullPath($PluginRoot)
$checks = [ordered]@{}

function Check([string]$Name, [bool]$Passed, [string]$Detail) {
    $script:checks[$Name] = [ordered]@{ passed = $Passed; detail = $Detail }
}

try {
    if (-not (Test-Path -LiteralPath $root -PathType Container)) {
        throw "plugin root does not exist: $root"
    }
    $manifestPath = Join-Path $root 'plugin.json'
    $mcpPath = Join-Path $root 'mcp.json'
    $hooksPath = Join-Path $root 'hooks\hooks.json'
    $hookScript = Join-Path $root 'hooks\ingest_event.py'
    $runner = Join-Path $root 'bin\run_mcp.py'
    $skill = Join-Path $root 'skills\workshop-review\SKILL.md'
    foreach ($path in @($manifestPath, $mcpPath, $hooksPath, $hookScript, $runner, $skill)) {
        Check ((Split-Path $path -Leaf) + '_present') (Test-Path -LiteralPath $path -PathType Leaf) $path
    }

    $manifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding utf8 | ConvertFrom-Json
    Check 'manifest_name' ($manifest.name -eq 'shutu-skill-workshop') ([string]$manifest.name)
    Check 'manifest_version' ([string]$manifest.version -match '^\d+\.\d+\.\d+$') ([string]$manifest.version)
    Check 'manifest_local_executor' ($null -ne $manifest.extensions.'com.openai'.requires_local_executor -and [bool]$manifest.extensions.'com.openai'.requires_local_executor) 'extensions.com.openai.requires_local_executor'
    Check 'manifest_hooks_path' ([string]$manifest.extensions.'com.openai'.hooks -eq './hooks/hooks.json') ([string]$manifest.extensions.'com.openai'.hooks)

    $mcp = Get-Content -LiteralPath $mcpPath -Raw -Encoding utf8 | ConvertFrom-Json
    $server = $mcp.mcpServers.'shutu-workshop'
    Check 'mcp_server_present' ($null -ne $server) 'mcpServers.shutu-workshop'
    Check 'mcp_stdio' ([string]$server.type -eq 'stdio') ([string]$server.type)
    Check 'mcp_root_placeholder' ((@($server.args) -join ' ') -match '\$\{PLUGIN_ROOT\}') ((@($server.args) -join ' '))

    $hooks = Get-Content -LiteralPath $hooksPath -Raw -Encoding utf8 | ConvertFrom-Json
    Check 'hooks_session_start' ($null -ne $hooks.hooks.SessionStart) 'hooks.SessionStart'
    Check 'hooks_post_tool_use' ($null -ne $hooks.hooks.PostToolUse) 'hooks.PostToolUse'
    Check 'hooks_fail_open_command' ((@($hooks.hooks.SessionStart[0].hooks.command) -join ' ') -match 'ingest_event\.py') 'summary-only hook command'

    $marketplacePath = Join-Path (Split-Path $root -Parent | Split-Path -Parent) '.agents\plugins\marketplace.json'
    if (Test-Path -LiteralPath $marketplacePath -PathType Leaf) {
        $marketplace = Get-Content -LiteralPath $marketplacePath -Raw -Encoding utf8 | ConvertFrom-Json
        $entry = @($marketplace.plugins) | Where-Object { $_.name -eq 'shutu-skill-workshop' } | Select-Object -First 1
        Check 'marketplace_entry' ($null -ne $entry) 'shutu-workshop-local/shutu-skill-workshop'
        Check 'marketplace_relative_source' ([string]$entry.source.path -eq './plugins/shutu-skill-workshop') ([string]$entry.source.path)
    } else {
        Check 'marketplace_entry' $false $marketplacePath
        Check 'marketplace_relative_source' $false 'marketplace missing'
    }

    $allPassed = (@($checks.Values | Where-Object { -not $_.passed }).Count -eq 0)
    $result = [ordered]@{
        status = if ($allPassed) { 'PASS' } else { 'FAIL' }
        plugin_root = $root
        checks = $checks
        schema = 'portable-agent-plugins-1.0.0 + OpenAI extension'
    }
    if ($Json) {
        $result | ConvertTo-Json -Depth 10 -Compress
    } else {
        $result.status
        $checks.GetEnumerator() | ForEach-Object { "{0}: {1} - {2}" -f $_.Key, $_.Value.passed, $_.Value.detail }
    }
    if (-not $allPassed) { exit 1 }
    exit 0
} catch {
    $result = [ordered]@{ status = 'FAIL'; plugin_root = $root; error_type = $_.Exception.GetType().Name; error = $_.Exception.Message; checks = $checks }
    if ($Json) { $result | ConvertTo-Json -Depth 10 -Compress } else { $result | ConvertTo-Json -Depth 10 }
    exit 1
}
