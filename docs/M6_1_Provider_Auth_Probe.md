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
| Auth | BLOCKED_ENV | isolated `codex login status` classified the home as `NOT_AUTHENTICATED` |
| Provider/no-tool text | BLOCKED_ENV | bounded request did not obtain a provider response before timeout; raw output was not persisted |
| MCP registration | PASS | `codex mcp add` completed in each isolated home |
| Direct MCP initialize/list/call | PASS | the runner performed a real local stdio `initialize`, `tools/list`, and `tools/call` exchange |
| Codex-host MCP call | BLOCKED_ENV | Codex stopped before a provider-backed task and emitted zero observed MCP tool calls |

The direct MCP row is deliberately separate from the Codex-host row. It proves the fixture and observer protocol, not that Codex selected or called the server. The pair evidence is `.test-m61/evidence/m61-pair-final.json`; it contains six redacted side runs across three cases, all `blocked_env`, with `mcp_tool_calls=0` and `task_result=unknown`.

No bearer, cookie, `auth.json`, complete prompt, complete provider error, or raw MCP payload was written to SQLite or the report. Hashes and error digests are one-way evidence only.

## Minimal reproduction

Run from the repository root in Windows PowerShell:

```powershell
$env:PYTHONPATH = 'servers'
.\scripts\m6_1_real_agent.ps1 -Side baseline -Timeout 5
.\scripts\m6_1_real_agent.ps1 -Side baseline `
  -TaskInputPath .test-m61\task-input.json -Timeout 5
```

The first command performs the preflight and local MCP handshake. The second attempts one isolated real Codex task and returns redacted evidence; it must remain `BLOCKED_ENV` while the isolated home is unauthenticated.

## Recovery

If an authorized operator wants to complete the real provider leg, authenticate explicitly in the generated isolated home; do not copy credentials from the normal user home:

```powershell
$env:PYTHONPATH = 'servers'
codex login --home (Resolve-Path .test-m61\codex-home\baseline)
.\scripts\m6_1_real_agent.ps1 -Side baseline -Timeout 30
```

Repeat for candidate and then rerun the three-case pair. If the provider requires a network proxy, supply it only to that process/session and remove it afterwards. Context7 is optional for this task and was unavailable; it is not a substitute for provider authorization.
