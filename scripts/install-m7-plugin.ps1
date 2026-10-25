[CmdletBinding()]
param(
    [string]$CodexHome = '',
    [switch]$Disable
)

$ErrorActionPreference = 'Stop'
$repoRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$oldHome = $env:CODEX_HOME

function Set-PluginEnabled([bool]$Enabled) {
    $codexRoot = if ($env:CODEX_HOME) { [IO.Path]::GetFullPath($env:CODEX_HOME) } else { Join-Path ([Environment]::GetFolderPath('UserProfile')) '.codex' }
    $configPath = Join-Path $codexRoot 'config.toml'
    if (-not (Test-Path -LiteralPath $configPath -PathType Leaf)) { return }
    $section = '[plugins."shutu-skill-workshop@shutu-workshop-local"]'
    $lines = @(Get-Content -LiteralPath $configPath -Encoding utf8)
    $start = [Array]::IndexOf($lines, $section)
    if ($start -lt 0) {
        $lines += '', $section, ("enabled = " + ($Enabled.ToString().ToLowerInvariant()))
    } else {
        $end = $lines.Count
        for ($index = $start + 1; $index -lt $lines.Count; $index++) {
            if ($lines[$index] -match '^\[') { $end = $index; break }
        }
        $enabledIndex = -1
        for ($index = $start + 1; $index -lt $end; $index++) {
            if ($lines[$index] -match '^\s*enabled\s*=') { $enabledIndex = $index; break }
        }
        $value = "enabled = " + ($Enabled.ToString().ToLowerInvariant())
        if ($enabledIndex -ge 0) {
            $lines[$enabledIndex] = $value
        } else {
            $before = @($lines[0..($end - 1)])
            $after = if ($end -lt $lines.Count) { @($lines[$end..($lines.Count - 1)]) } else { @() }
            $lines = @($before + $value + $after)
        }
    }
    $temporary = "$configPath.m7.tmp"
    [IO.File]::WriteAllText($temporary, (($lines -join [Environment]::NewLine) + [Environment]::NewLine), [Text.UTF8Encoding]::new($false))
    Move-Item -LiteralPath $temporary -Destination $configPath -Force
}

try {
    if ($CodexHome) {
        $env:CODEX_HOME = [IO.Path]::GetFullPath($CodexHome)
        New-Item -ItemType Directory -Force -Path $env:CODEX_HOME | Out-Null
    }
    $shell = (Get-Command pwsh -ErrorAction SilentlyContinue).Source
    if (-not $shell) { $shell = (Get-Command powershell -ErrorAction Stop).Source }
    & $shell -NoProfile -File (Join-Path $repoRoot 'scripts\verify-m7-plugin.ps1') -Json | Out-Host
    if ($LASTEXITCODE -ne 0) { throw 'plugin package verification failed' }

    $marketplaceOutput = & codex plugin marketplace add $repoRoot --json 2>&1
    if ($LASTEXITCODE -ne 0) { throw "codex marketplace add failed: $marketplaceOutput" }
    $addArgs = @('plugin', 'add', 'shutu-skill-workshop@shutu-workshop-local', '--json')
    $pluginOutput = & codex @addArgs 2>&1
    if ($LASTEXITCODE -ne 0) { throw "codex plugin add failed: $pluginOutput" }
    if ($Disable) { Set-PluginEnabled $false }

    [ordered]@{
        status = 'HOST_VERIFIED'
        marketplace = 'shutu-workshop-local'
        plugin = 'shutu-skill-workshop'
        codex_home_scope = if ($CodexHome) { [IO.Path]::GetFullPath($CodexHome) } else { 'default' }
        disabled = [bool]$Disable
        marketplace_output = (@($marketplaceOutput) -join "`n")
        plugin_output = (@($pluginOutput) -join "`n")
    } | ConvertTo-Json -Depth 8 -Compress
} finally {
    if ($null -eq $oldHome) { Remove-Item Env:CODEX_HOME -ErrorAction SilentlyContinue } else { $env:CODEX_HOME = $oldHome }
}
