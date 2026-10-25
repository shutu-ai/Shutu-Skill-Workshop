# M7.1 Operations Runbook

## Read-only preflight

```powershell
cd D:\dev-projects\Shutu-Skill-Workshop
codex --version
codex plugin --help
codex mcp --help
codex plugin marketplace list
codex plugin list --marketplace shutu-workshop-local --json
codex mcp list
```

Do not print the full user `config.toml`; it can contain tokens and unrelated
MCP credentials. Inspect only redacted metadata and the Workshop entry.

## Install the current CLI compatibility path

```powershell
powershell -NoProfile -File .\scripts\install-m71-codex.ps1 -Mode Compat
powershell -NoProfile -File .\scripts\verify-m71-codex.ps1 -Mode Compat -Json
```

This changes only the Workshop plugin/compatibility entry in the selected
Codex HOME. The compatibility server receives the source checkout and a
separate plugin-data directory; the Workshop SQLite database remains in the
workspace.

Start a normal interactive session (`codex`, not `codex exec`) in the target
workspace. Approve the read-only MCP calls when the host asks, then request
`workshop_health` followed by `workshop_list_issues`. A valid empty result is
`NO_DATA` followed by `NO_ACTIONABLE_ISSUE`.

Request `workshop-review` explicitly and preserve the three sections Fact,
Inference, and Proposal. If `/hooks` does not show the Workshop Hook, report
`HOST_UNSUPPORTED` or `NOT_TRUSTED`; never run the script manually and label
that output as a Codex Hook event.

## Native mode

```powershell
powershell -NoProfile -File .\scripts\install-m71-codex.ps1 -Mode Native
powershell -NoProfile -File .\scripts\verify-m71-codex.ps1 -Mode Native -Json
```

Use this only after an interactive host proves the plugin MCP Tool Call/Result.
The command removes the matching compatibility registration so the same
server is not registered twice.

## Desktop

In a supported Windows Desktop host, install/enable the same local Marketplace
plugin, inspect the plugin and Hook trust UI, then call `workshop_health`,
`workshop_list_issues`, and `workshop-review`. Capture the host's Tool
Call/Result and one real Hook event. If Desktop is unavailable, record
`DESKTOP_HOST=BLOCKED_ENV` and do not infer it from CLI or `codex exec`.

## Uninstall and rollback

```powershell
powershell -NoProfile -File .\scripts\uninstall-m71-codex.ps1 -Mode Compat -RemoveMarketplace
```

The command removes only the matching compatibility server, Workshop plugin,
and optionally the local Marketplace entry. It does not delete Workshop
SQLite history or other plugins/MCP servers. `-WhatIf` is available on the
installer/uninstaller; `-Scope Project` requires an explicit isolated
`-CodexHome` because this CLI version has no project-scoped write command.

## Network and evidence

Local MCP/Marketplace operations do not need a proxy. For GitHub only, try a
direct operation first; if it fails, use the one-shot command form
`git -c http.proxy=http://proxyae.huawei.com:8080 ...` and verify the remote
SHA with the same one-shot setting. Do not persist proxy settings.
