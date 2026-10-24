[CmdletBinding()]
param(
    [ValidateSet("baseline", "candidate")]
    [string]$Side = "baseline",
    [string]$TaskInputPath = "",
    [double]$Timeout = 30
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$oldLocation = Get-Location
$oldCodexHome = $env:CODEX_HOME
$oldPythonPath = $env:PYTHONPATH
$artifactBase = Join-Path $repoRoot ".test-m61\artifacts"
$workspacePath = Join-Path $repoRoot ".test-m61\agent-workspace"
$codexHomePath = Join-Path $repoRoot (".test-m61\codex-home\" + $Side)
$artifactPath = Join-Path $artifactBase $Side
$serverPath = Join-Path $artifactPath "server.py"

try {
    Set-Location $repoRoot
    New-Item -ItemType Directory -Force -Path $artifactPath, $workspacePath, $codexHomePath | Out-Null
    $serverTemplate = @'
import json
import sys

LABEL = __LABEL__
TOOLS = [{"name": "echo", "description": LABEL, "inputSchema": {"type": "object"}}]

for line in sys.stdin:
    if not line.strip():
        continue
    request = json.loads(line)
    method = request.get("method")
    if method in {"initialized", "notifications/initialized"}:
        continue
    if method == "initialize":
        result = {"protocolVersion": "2025-11-25", "serverInfo": {"name": LABEL, "version": "1"}, "capabilities": {"tools": {}}}
    elif method == "tools/list":
        result = {"tools": TOOLS}
    elif method == "tools/call":
        arguments = request.get("params", {}).get("arguments", {})
        result = {"content": [{"type": "text", "text": LABEL + ":" + str(arguments.get("text", ""))}]}
    else:
        continue
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": request.get("id"), "result": result}) + "\n")
    sys.stdout.flush()
'@
    $serverTemplate.Replace("__LABEL__", "'$Side'") | Set-Content -LiteralPath $serverPath -Encoding utf8
    $env:CODEX_HOME = [IO.Path]::GetFullPath($codexHomePath)
    $env:PYTHONPATH = "$repoRoot\servers;$repoRoot\tests"
    $pythonExe = (Get-Command python).Source
    $artifactHash = (& python -c "from shutu_workshop.release import artifact_tree_hash; print(artifact_tree_hash(r'$artifactPath'))").Trim()

    & python -m shutu_workshop --workspace $workspacePath agent codex preflight `
        --codex-command codex --codex-home $codexHomePath --cwd $workspacePath `
        --mcp-server-name ("shutu-m61-" + $Side) --mcp-server-command $pythonExe $serverPath `
        --timeout $Timeout --json
    if ($LASTEXITCODE -ne 0) { throw "Codex preflight command failed: $LASTEXITCODE" }

    if ($TaskInputPath) {
        $inputPath = [IO.Path]::GetFullPath((Join-Path $repoRoot $TaskInputPath))
        if (-not (Test-Path -LiteralPath $inputPath -PathType Leaf)) { throw "Task input does not exist: $inputPath" }
        & python -m shutu_workshop --workspace $workspacePath agent codex `
            --case-id ("m61-" + $Side) --input-file $inputPath --side $Side `
            --artifact-hash $artifactHash --artifact-root $artifactPath `
            --mcp-server-name ("shutu-m61-" + $Side) --mcp-server-command $pythonExe $serverPath `
            --codex-home $codexHomePath --cwd $workspacePath --timeout $Timeout --json
        exit $LASTEXITCODE
    }
    exit 0
}
finally {
    Set-Location $oldLocation
    if ($null -eq $oldCodexHome) { Remove-Item Env:CODEX_HOME -ErrorAction SilentlyContinue } else { $env:CODEX_HOME = $oldCodexHome }
    if ($null -eq $oldPythonPath) { Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue } else { $env:PYTHONPATH = $oldPythonPath }
}
