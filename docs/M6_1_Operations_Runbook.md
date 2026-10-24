# M6.1 Operations Runbook

All commands below run from `D:\dev-projects\Shutu-Skill-Workshop` in Windows PowerShell. They use only `.test-m61` and an explicit isolated Codex home.

## 1. Preflight

```powershell
$env:PYTHONPATH = 'servers'
.\scripts\m6_1_real_agent.ps1 -Side baseline -Timeout 5
.\scripts\m6_1_real_agent.ps1 -Side candidate -Timeout 5
```

The output is redacted JSON. Check CLI version, `auth_status`, provider status, isolated-home scope, MCP registration, and the direct fixture handshake. A direct fixture handshake is not evidence that Codex called the tool.

Equivalent CLI form:

```powershell
python -m shutu_workshop --workspace .test-m61\agent-workspace agent codex preflight `
  --codex-command codex `
  --codex-home (Resolve-Path .test-m61\codex-home\baseline) `
  --cwd (Resolve-Path .test-m61\agent-workspace) `
  --mcp-server-name shutu-m61-baseline `
  --mcp-server-command (Get-Command python).Source (Resolve-Path .test-m61\artifacts\baseline\server.py) `
  --timeout 5 --json
```

## 2. One bounded real task

```powershell
.\scripts\m6_1_real_agent.ps1 -Side baseline `
  -TaskInputPath .test-m61\task-input.json -Timeout 30
```

Only continue to the pair after provider/auth preflight is usable. The current environment returns `NOT_AUTHENTICATED` and `BLOCKED_ENV`; do not loop retries or fill in a tool result.

## 3. Three-case paired run

```powershell
python scripts/m6_1_real_pair.py `
  --workspace .test-m61\pair-workspace-final `
  --codex-home .test-m61\pair-codex-home-final `
  --baseline-root .test-m61\artifacts\baseline `
  --candidate-root .test-m61\artifacts\candidate `
  --output .test-m61\evidence\m61-pair-final.json `
  --timeout 30
```

Inspect only redacted status fields:

```powershell
$evidence = Get-Content .test-m61\evidence\m61-pair-final.json -Raw | ConvertFrom-Json
$evidence.results | Select-Object case_id,baseline_or_candidate,activation_status,execution_status,task_result,execution_evidence_tier
```

The required completion signal is not merely `activation_status=VERIFIED`: it is a non-zero Codex MCP call/result, an independent Oracle verdict, and comparable baseline/candidate evidence for every required case.

## 4. Observation and feedback

Use the existing `ReleaseObservationService` and `OptimizationFeedbackService` only with verified trace/event/activation references. `BLOCKED_ENV`, `UNKNOWN`, and fixture-only evidence remain non-production feedback. Do not publish a candidate or mutate a production release as part of this run.

## 5. OpenCode and Context7 boundary

OpenCode is a separate diagnostic. The bounded check used `.test-m61/opencode-workspace`, `opencode --version`, `opencode plugin list`, and standalone JSON output. Local read/glob tool events were observed, but no configured MCP hook or provider-backed call was found, so the result is `BLOCKED_ENV` and does not affect Codex closure.

Context7 is optional and was unavailable. If it is later needed, use a process-only proxy for that command/session and do not persist global proxy configuration. Official MCP SDK interoperability remains `UNVERIFIED` until a separately reproducible SDK client is exercised.

## 6. Authentication recovery

```powershell
$env:CODEX_HOME = (Resolve-Path .test-m61\codex-home\baseline).Path
codex login --home $env:CODEX_HOME
Remove-Item Env:CODEX_HOME
```

The operator must complete the interactive authorization. Never copy `auth.json` or any credential from the normal home. Re-run the preflight and pair after authorization.

## 7. Git handoff

Check the explicit staged list before committing. Try the normal push first. If it fails, use the one-shot proxy and verify the same remote with the proxy; do not persist proxy configuration:

```powershell
git --git-dir=.git-push --work-tree=. push origin main
git -c http.proxy=http://proxyae.huawei.com:8080 --git-dir=.git-push --work-tree=. push origin main
git -c http.proxy=http://proxyae.huawei.com:8080 --git-dir=.git-push --work-tree=. ls-remote origin refs/heads/main
```

## 8. Cleanup

Only the explicitly created `.test-m61` directory may be removed after evidence is archived. Do not use `git clean`, `reset --hard`, or delete unrelated dirty/untracked files.
