[CmdletBinding()]
param(
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

try {
    if ($Scope -eq 'Project' -and -not $CodexHome) { Check 'scope' 'HOST_UNSUPPORTED' 'Project scope requires an explicit CodexHome.' }
    $targetHome = if ($CodexHome) { [IO.Path]::GetFullPath($CodexHome) } elseif ($Scope -eq 'User') { Join-Path ([Environment]::GetFolderPath('UserProfile')) '.codex' } else { Join-Path $repoRoot '.codex' }
    $env:CODEX_HOME = $targetHome
    $observer = [IO.Path]::GetFullPath((Join-Path $repoRoot 'scripts\m72_stdio_observer.py'))
    $manifest = Join-Path $targetHome 'plugins\data\shutu-skill-workshop\m72-observer-manifest.json'
    $entrypointStatus = if (Test-Path -LiteralPath $observer -PathType Leaf) { 'PASS' } else { 'FAIL' }
    $manifestStatus = if (Test-Path -LiteralPath $manifest -PathType Leaf) { 'PASS' } else { 'NOT_ACTIVE' }
    Check 'entrypoint' $entrypointStatus $observer
    Check 'manifest' $manifestStatus $manifest
    $saved = $ErrorActionPreference
    $ErrorActionPreference = 'SilentlyContinue'
    $output = & codex mcp get 'shutu-workshop' --json 2>$null
    $exitCode = $LASTEXITCODE
    $ErrorActionPreference = $saved
    $server = $null
    if ($exitCode -eq 0) { try { $server = (($output -join "`n") | ConvertFrom-Json) } catch {} }
    $transport = if ($server -and $server.transport) { $server.transport } else { $server }
    $argsText = if ($transport) { @($transport.args) -join ' ' } else { '' }
    $registrationStatus = if ($argsText -match '(?i)m72_stdio_observer\.py' -and $argsText -match '(?i)run_mcp\.py') { 'PASS' } else { 'NOT_ACTIVE' }
    Check 'observer_registration' $registrationStatus ($argsText -replace [regex]::Escape($repoRoot), '<repo>')
    Check 'codex_version' 'PASS' ((& codex --version 2>$null) -join ' ')
    $failed = @($checks.Values | Where-Object { $_.status -eq 'FAIL' }).Count
    $result = [ordered]@{ status = if ($failed) { 'FAIL' } elseif (@($checks.Values | Where-Object { $_.status -eq 'HOST_UNSUPPORTED' }).Count) { 'BLOCKED_ENV' } elseif (@($checks.Values | Where-Object { $_.status -eq 'PASS' }).Count -ge 3) { 'PASS' } else { 'NOT_ACTIVE' }; scope = $Scope; codex_home = $targetHome; checks = $checks }
    if ($Json) { $result | ConvertTo-Json -Depth 10 -Compress } else { $result | ConvertTo-Json -Depth 10 }
    if ($failed) { exit 1 }
} catch {
    $result = [ordered]@{ status = 'FAIL'; error_type = $_.Exception.GetType().Name; error = $_.Exception.Message; checks = $checks }
    if ($Json) { $result | ConvertTo-Json -Depth 10 -Compress } else { $result | ConvertTo-Json -Depth 10 }
    exit 1
} finally {
    if ($null -eq $oldHome) { Remove-Item Env:CODEX_HOME -ErrorAction SilentlyContinue } else { $env:CODEX_HOME = $oldHome }
}
