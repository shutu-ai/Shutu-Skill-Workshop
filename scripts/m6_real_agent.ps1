[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$TaskInputPath,
    [ValidateSet("baseline", "candidate")]
    [string]$Side = "baseline",
    [string]$Workspace = ".test-m6\agent-workspace",
    [string]$CodexHome = ".test-m6\codex-home",
    [string]$CaseId = "m6-real-echo",
    [string]$ArtifactHash = ("b" * 64),
    [string]$CapabilityVersionId = "",
    [string]$PairId = "",
    [string]$ExperimentId = "",
    [string]$ComparisonManifestHash = "",
    [string]$TaskCategory = "m6-real-task",
    [double]$Timeout = 120
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$oldLocation = Get-Location
$oldCodexHome = $env:CODEX_HOME
$oldPythonPath = $env:PYTHONPATH

try {
    Set-Location $repoRoot
    $workspacePath = [IO.Path]::GetFullPath((Join-Path $repoRoot $Workspace))
    $codexHomePath = [IO.Path]::GetFullPath((Join-Path $repoRoot $CodexHome))
    $inputPath = [IO.Path]::GetFullPath((Join-Path $repoRoot $TaskInputPath))
    if (-not (Test-Path -LiteralPath $inputPath -PathType Leaf)) {
        throw "Task input file does not exist: $inputPath"
    }
    New-Item -ItemType Directory -Force -Path $workspacePath, $codexHomePath | Out-Null
    $env:CODEX_HOME = $codexHomePath
    $env:PYTHONPATH = "$repoRoot\servers;$repoRoot\tests"

    & codex mcp add shutu-fixture -- python -m fixtures.mcp_test_server --protocol legacy | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "isolated Codex MCP fixture registration failed"
    }

    $arguments = @(
        "-m", "shutu_workshop", "--workspace", $workspacePath,
        "agent", "codex", "--case-id", $CaseId, "--input-file", $inputPath,
        "--side", $Side, "--artifact-hash", $ArtifactHash,
        "--task-category", $TaskCategory, "--codex-home", $codexHomePath,
        "--cwd", $workspacePath, "--timeout", $Timeout, "--json"
    )
    if ($CapabilityVersionId) { $arguments += @("--capability-version-id", $CapabilityVersionId) }
    if ($PairId) { $arguments += @("--pair-id", $PairId) }
    if ($ExperimentId) { $arguments += @("--experiment-id", $ExperimentId) }
    if ($ComparisonManifestHash) { $arguments += @("--comparison-manifest-hash", $ComparisonManifestHash) }
    & python @arguments
    exit $LASTEXITCODE
}
finally {
    Set-Location $oldLocation
    if ($null -eq $oldCodexHome) { Remove-Item Env:CODEX_HOME -ErrorAction SilentlyContinue } else { $env:CODEX_HOME = $oldCodexHome }
    if ($null -eq $oldPythonPath) { Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue } else { $env:PYTHONPATH = $oldPythonPath }
}
