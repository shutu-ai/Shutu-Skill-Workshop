# M6.1 Provider/Auth Probe

Date: 2026-10-25
Scope: isolated Codex CLI and local stdio MCP fixture only. No production Skill, MCP, SMS, SSH, SQL, or user `CODEX_HOME` was touched.

## Recorded baseline

The local `.git-push` checkout and its cached `origin/main` ref were at `3be9b51` (`feat: add M1 capability trace foundation`) when work started. A live proxy `ls-remote` then verified the task baseline `origin/main=4b070b4`; the stale local ref was merged without force-pushing or rewriting history.

## Probe result

| Layer | Result | Evidence |
|---|---|---|
| CLI | PASS | `codex-cli 0.163.0`; version/help completed |
| Isolated HOME | PASS | `.test-m61/codex-home/<side>` and `.test-m61/pair-codex-home-final`; only metadata/config fingerprints were retained |
| Auth | PASS | authenticated rerun reported `Logged in using ChatGPT` in both pair-side homes |
| Provider/no-tool text | PASS | both isolated homes returned the exact text response `READY`; model-catalog refresh warnings did not prevent the response |
| MCP registration | PASS | `codex mcp add` completed in each isolated home |
| Direct MCP initialize/list/call | PASS | the runner performed a real local stdio `initialize`, `tools/list`, and `tools/call` exchange |
| Codex-host MCP call | INCONCLUSIVE | one real `echo` call was observed and failed; the other five side runs emitted no tool event |

The direct MCP row is deliberately separate from the Codex-host row. It proves the fixture and observer protocol, not that Codex selected or called the server. The authenticated pair evidence is `.test-m61/evidence/m61-pair-authenticated.json`: six completed redacted side runs across three cases, six verified activations, one observed MCP call with a failed result, five runs without a tool event, and six `task_result=unknown` results.

No bearer, cookie, `auth.json`, complete prompt, complete provider error, or raw MCP payload was written to SQLite or the report. Hashes and error digests are one-way evidence only.

## Minimal reproduction

Run from the repository root in Windows PowerShell:

```powershell
$env:PYTHONPATH = 'servers'
.\scripts\m6_1_real_agent.ps1 -Side baseline -Timeout 5
.\scripts\m6_1_real_agent.ps1 -Side baseline `
  -TaskInputPath .test-m61\task-input.json -Timeout 5
```

The first command performs the preflight and local MCP handshake. The second attempts one isolated real Codex task and returns redacted evidence. The authenticated pair command is the current evidence-producing command; a completed process is not sufficient without a successful independent Oracle result.

## Recovery

Authentication is now complete in the two pair-side homes. The remaining recovery is at the MCP task layer: diagnose the failed `echo` result and make each case explicitly call its configured side server, then require successful result events and the independent Oracle before closure.

```powershell
$env:PYTHONPATH = 'servers'
codex login status
python scripts/m6_1_real_pair.py `
  --workspace .test-m61\pair-workspace-authenticated `
  --codex-home .test-m61\pair-codex-home-final `
  --baseline-root .test-m61\artifacts\baseline `
  --candidate-root .test-m61\artifacts\candidate `
  --output .test-m61\evidence\m61-pair-authenticated.json `
  --timeout 120
```

If the provider requires a network proxy, supply it only to that process/session and remove it afterwards. Context7 is optional for this task and was unavailable; it is not a substitute for provider authorization.
