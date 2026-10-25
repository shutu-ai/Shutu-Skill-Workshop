[CmdletBinding()]
param(
    [ValidateSet('Auto', 'Native', 'Compat')]
    [string]$Mode = 'Auto',
    [ValidateSet('User', 'Project')]
    [string]$Scope = 'User',
    [string]$CodexHome = '',
    [switch]$Json
)

$ErrorActionPreference = 'Stop'
$repoRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$oldHome = $env:CODEX_HOME
$checks = [ordered]@{}

function Check([string]$Name, [string]$Status, [string]$Detail) {
    $script:checks[$Name] = [ordered]@{ status = $Status; detail = $Detail }
}

function Get-CompatServer {
    $saved = $ErrorActionPreference
    $ErrorActionPreference = 'SilentlyContinue'
    $output = & codex mcp get 'shutu-workshop' --json 2>$null
    $exitCode = $LASTEXITCODE
    $ErrorActionPreference = $saved
    if ($exitCode -ne 0) { return $null }
    try { return (($output -join "`n") | ConvertFrom-Json) } catch { return $null }
}

try {
    if ($Scope -eq 'Project' -and -not $CodexHome) {
        Check 'scope' 'HOST_UNSUPPORTED' 'Project scope requires an explicit CodexHome with this CLI version.'
    }
    $targetHome = if ($CodexHome) { [IO.Path]::GetFullPath($CodexHome) } elseif ($Scope -eq 'User') { Join-Path ([Environment]::GetFolderPath('UserProfile')) '.codex' } else { Join-Path $repoRoot '.codex' }
    $env:CODEX_HOME = $targetHome
    $package = & powershell -NoProfile -File (Join-Path $repoRoot 'scripts\verify-m7-plugin.ps1') -Json 2>&1
    if ($LASTEXITCODE -eq 0) { Check 'package' 'PASS' 'M7 portable plugin package passes schema checks.' } else { Check 'package' 'FAIL' (@($package) -join "`n") }
    $installed = $null
    $pluginOutput = & codex plugin list --marketplace shutu-workshop-local --json 2>$null
    if ($LASTEXITCODE -eq 0) { $installed = (($pluginOutput -join "`n") | ConvertFrom-Json) }
    $plugin = @($installed.installed) | Where-Object { $_.name -eq 'shutu-skill-workshop' } | Select-Object -First 1
    $pluginStatus = if ($null -ne $plugin -and $plugin.enabled) { 'PASS' } else { 'FAIL' }
    Check 'plugin_installation' $pluginStatus 'default Codex HOME plugin installation/enabled state'
    $server = Get-CompatServer
    $transport = if ($server.transport) { $server.transport } else { $server }
    $argsText = if ($transport) { @($transport.args) -join ' ' } else { '' }
    if ($Mode -eq 'Compat') {
        $compatStatus = if ($server -and $argsText -match 'run_mcp\.py') { 'PASS' } else { 'FAIL' }
        $compatDetail = if ($argsText) { $argsText } else { 'shutu-workshop is not registered' }
        Check 'compat_mcp_registration' $compatStatus $compatDetail
    } elseif ($Mode -eq 'Native') {
        $compatStatus = if ($null -eq $server) { 'PASS' } else { 'FAIL' }
        Check 'compat_mcp_registration' $compatStatus 'Native mode keeps the standalone compatibility server absent.'
    } else {
        $compatStatus = if ($null -eq $server -or $argsText -match 'run_mcp\.py') { 'UNRESOLVED' } else { 'CONFLICT' }
        Check 'compat_mcp_registration' $compatStatus 'Interactive Native host evidence is required to resolve Auto.'
    }
    $version = (& codex --version 2>$null) -join ' '
    Check 'codex_version' 'PASS' $version
    Check 'hooks' 'NOT_OBSERVABLE' 'Hook trust and real event capture require a normal interactive host session.'
    Check 'tool_calls' 'NOT_OBSERVABLE' 'This verifier never substitutes a direct server call for a host Tool Call/Result.'
    $failed = @($checks.Values | Where-Object { $_.status -eq 'FAIL' }).Count
    $result = [ordered]@{
        status = $null
        mode = $Mode
        scope = $Scope
        codex_home = $targetHome
        checks = $checks
    }
    if ($failed) { $result.status = 'FAIL' }
    elseif (@($checks.Values | Where-Object { $_.status -eq 'HOST_UNSUPPORTED' }).Count) { $result.status = 'BLOCKED_ENV' }
    else { $result.status = 'PASS' }
    if ($Json) { $result | ConvertTo-Json -Depth 10 -Compress } else { $result | ConvertTo-Json -Depth 10 }
    if ($failed) { exit 1 }
} catch {
    $result = [ordered]@{ status = 'FAIL'; error_type = $_.Exception.GetType().Name; error = $_.Exception.Message; checks = $checks }
    if ($Json) { $result | ConvertTo-Json -Depth 10 -Compress } else { $result | ConvertTo-Json -Depth 10 }
    exit 1
} finally {
    if ($null -eq $oldHome) { Remove-Item Env:CODEX_HOME -ErrorAction SilentlyContinue } else { $env:CODEX_HOME = $oldHome }
}
