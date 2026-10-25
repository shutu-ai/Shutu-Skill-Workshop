# M7.4.1 Operations Runbook

## Preflight in an isolated HOME

Use only short, ignored directories inside the Workshop. Restore the current
process environment in `finally` and never persist proxy or Git settings:

```powershell
cd D:\dev-projects\Shutu-Skill-Workshop
$oldCodexHome = $env:CODEX_HOME
try {
    $env:PYTHONPATH = (Resolve-Path .\servers).Path
    $root = Join-Path (Get-Location) ".test-m741"
    New-Item -ItemType Directory -Force -Path `
        (Join-Path $root "workspace"),
        (Join-Path $root "home-no-skill"),
        (Join-Path $root "home-baseline"),
        (Join-Path $root "home-candidate") | Out-Null
    $env:CODEX_HOME = Join-Path $root "home-baseline"
    codex --version
    codex login status
    python -c "from shutu_workshop.adapters.real_agent import codex_preflight; import json; print(json.dumps(codex_preflight('.test-m741/preflight-db', codex_home='.test-m741/home-baseline', cwd='.test-m741/workspace', timeout=45), ensure_ascii=False, sort_keys=True))"
} finally {
    if ($null -eq $oldCodexHome) {
        Remove-Item Env:CODEX_HOME -ErrorAction SilentlyContinue
    } else {
        $env:CODEX_HOME = $oldCodexHome
    }
}
```

`codex login status` and `--help` are not Provider readiness. Only a real
`codex exec --json --ephemeral --sandbox read-only --skip-git-repo-check`
completion with the expected output is `PROVIDER_REAL_READY=PASS`.

If the isolated HOME is not authenticated, the user may run `codex login`
while that HOME is selected, then rerun the smoke once. Do not copy credentials
from the default HOME. A 401 is recorded as authentication or route evidence;
do not clear a working proxy as a reflex. Proxy variable names may be recorded,
but proxy values are never persisted.

## Skill condition proof

Each arm needs its own workspace and HOME. The runner records a bounded,
redacted `effective_skill_inventory` with name, scope, source kind, content
hash, version ID, path digest, and detection method. It checks project and
upper-scope `.agents/skills`, isolated HOME skill roots, and a bounded plugin
root. A filesystem scan is `STATIC_CONFIG_VERIFIED` at most. Use
`HOST_OBSERVED` only when the Host supplies a verifiable discovery record.

No-Skill is invalid if the target name appears in any effective source.
Baseline/Candidate are invalid if the target is not in the effective discovery
path, has a different hash, or collides with another same-name version/scope.

## Pilot and Oracle

Run the three cases in all three arms only after all three isolated HOME READY
smokes pass. The task input is the only input passed to Codex; expected answers
and Oracle logic stay outside the agent workspace. The Oracle runs after the
Codex process exits and accepts only objective file/test output.

Unknown execution, timeout, 401, missing output, unverified condition, Oracle
failure, or any pre/runtime/post artifact hash change remains `unknown`. A
single arm never receives `IMPROVED`; that label belongs only to the complete
cross-arm suite report. A three-case Pilot does not establish production
benefit.

## Observer and cleanup

Use only the owned local stdio fixture for Observer checks. A pending request or
recording error keeps session close `PARTIAL`; complete EOF with no pending
requests is `COMPLETE`. Roll back temporary registration after the probe. Do
not modify production MCP registrations, publish Skills, deploy, or delete an
authorized test HOME automatically.
