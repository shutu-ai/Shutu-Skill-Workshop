# M7.2 Operations Runbook

## Preflight

```powershell
cd D:\dev-projects\Shutu-Skill-Workshop
codex --version
codex mcp get shutu-workshop --json
powershell -NoProfile -File .\scripts\verify-m71-codex.ps1 -Mode Compat -Json
```

Do not print the complete user `config.toml`. It may contain unrelated MCP
credentials. The installer reads and changes only the owned `shutu-workshop`
entry.

## Enable the transparent Observer

```powershell
powershell -NoProfile -File .\scripts\install-m72-observer.ps1 -Mode Enable
powershell -NoProfile -File .\scripts\verify-m72-observer.ps1 -Json
codex mcp get shutu-workshop --json
```

The command requires the exact M7.1 Compat registration. It preserves the
original command, arguments, environment, and working directory in a private
manifest. It registers exactly one wrapper and the original `run_mcp.py` as
the child. Normal CLI use is the only runtime path; no second Codex is started.

For a dry run:

```powershell
powershell -NoProfile -File .\scripts\install-m72-observer.ps1 -Mode Enable -WhatIf
```

## Real interactive verification

Start `codex`, not `codex exec`, in the target workspace. Request only:

1. `shutu-workshop.workshop_health`
2. `shutu-workshop.workshop_list_issues`

Then use a controlled read-only error such as
`shutu-workshop.workshop_issue_detail` with `{"issue_id":""}` and follow it
with `workshop_list_issues`. The expected error is `INVALID_ARGUMENT`; the
Observer must preserve the original result and the Issue list must remain
read-only. Confirm the SQLite event has `observation_source=mcp_observer`, an
exact same-connection request id, and an R01 evidence reference.

An empty normal ledger is reported as `NO_ACTIONABLE_ISSUE`, not as a success
rate. A tool result is not an independent Oracle for the user's business task.

## Disable and rollback

```powershell
powershell -NoProfile -File .\scripts\uninstall-m72-observer.ps1
powershell -NoProfile -File .\scripts\verify-m71-codex.ps1 -Mode Compat -Json
powershell -NoProfile -File .\scripts\verify-m72-observer.ps1 -Json
```

Disable restores only the saved Workshop Compat entry. It does not delete
SQLite traces, Issues, releases, other MCP servers, plugins, or Hook data. If
the name has been manually changed, the script refuses to overwrite it.

## Troubleshooting

| Symptom | Action |
|---|---|
| `TARGET_NOT_FOUND` | Restore the owned M7.1 Compat entry, then enable M7.2 |
| `CONFLICT` | Inspect only `codex mcp get shutu-workshop --json`; do not overwrite a non-owned entry |
| `startup_timeout` | Check the child command and server handshake; no retry is sent |
| `idle_timeout` | It is opt-in; remove it for a normal long-lived MCP session |
| `failed_observations` | Keep the forwarded result, record the gap, and inspect stderr/trace; do not resend the call |
| stale `running` trace after a disconnected TUI | Stop only the matching Observer process tree, then close that trace as `incomplete/unknown` |
| GitHub direct connection fails | Retry the single Git command with `-c http.proxy=http://proxyae.huawei.com:8080`; do not persist proxy settings |

The Observer does not require a `clear-proxy-env` step. Proxy state is not
written by this workflow; only the explicitly retried Git command uses the
one-shot proxy.

## Data retention and privacy

The existing redaction and bounded summary policy applies. Do not paste raw
prompts, full tool arguments/results, tokens, cookies, or complete user config
into reports. Install/disable never purges history; use the existing Workshop
retention process for any separately authorized archive or cleanup.
