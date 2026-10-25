# M7 Operations Runbook

## Windows prerequisites

Use Python 3.11+ (the current host uses Python 3.14.7), a working `codex`
command, and a writable workspace. The plugin does not require Node, npm, a
new Python dependency, or a second Codex login. The default workspace is the
Codex session working directory; set `SHUTU_WORKSHOP_WORKSPACE` for a separate
workspace database.

## Verify and install

```powershell
powershell -NoProfile -File .\scripts\verify-m7-plugin.ps1 -Json
powershell -NoProfile -File .\scripts\install-m7-plugin.ps1
codex plugin list --json
```

For a safe isolated probe:

```powershell
powershell -NoProfile -File .\scripts\install-m7-plugin.ps1 `
  -CodexHome .test-m7\codex-home
```

The script adds only this repo Marketplace and installs
`shutu-skill-workshop@shutu-workshop-local`. It does not overwrite another
Marketplace or modify global proxy settings.

## Enable, disable, trust

The plugin is installed enabled by the Codex CLI. To test disabled state,
use:

```powershell
powershell -NoProfile -File .\scripts\install-m7-plugin.ps1 `
  -CodexHome .test-m7\codex-home -Disable
codex plugin list --json
powershell -NoProfile -File .\scripts\install-m7-plugin.ps1 `
  -CodexHome .test-m7\codex-home
```

The current CLI stores the plugin state as
`[plugins."shutu-skill-workshop@shutu-workshop-local"] enabled = ...`; the
script changes only that field. Plugin-bundled Hooks remain untrusted until a
supporting host shows their exact definition in `/hooks` and the user trusts
it. Installation alone is not Hook execution evidence.

## Manual MCP check

The server is local stdio and does not use an HTTP proxy:

```powershell
$env:PYTHONPATH = (Resolve-Path 'servers').Path
'{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}' |
  python -m shutu_workshop.mcp_server.server --workspace .
```

The normal review sequence is `workshop_health`,
`workshop_list_issues`, `workshop_issue_detail`, and then version/evaluation
queries as needed. Empty data must remain `NO_DATA` or
`NO_ACTIONABLE_ISSUE`.

## Skill and Hook diagnosis

Use `workshop-review` only for explicit review. It cannot approve a Proposal,
change a Skill/MCP configuration, execute a Release, or roll back. If the host
does not expose the Skill invocation or Hook events, report `BLOCKED_ENV` or
`NOT_OBSERVABLE`; do not infer them from a prompt or a successful install.

Hook data is under `PLUGIN_DATA`:

- `hook_events.jsonl`: bounded summary records only;
- `hook_state.json`: captured/dropped/unknown counters and last timestamp;
- `hook.lock`: process coordination file.

If the Hook is broken, disable/uninstall the plugin first. The host tool should
continue to work because the handler exits zero and never writes stdout.

## Upgrade, uninstall, and recovery

```powershell
powershell -NoProfile -File .\scripts\install-m7-plugin.ps1
powershell -NoProfile -File .\scripts\uninstall-m7-plugin.ps1
powershell -NoProfile -File .\scripts\uninstall-m7-plugin.ps1 `
  -RemoveMarketplace
```

`codex plugin remove` removes the plugin cache. The optional Marketplace switch
removes only `shutu-workshop-local`. The Workshop SQLite database is not
deleted; back up `.skill_workshop/state.db` before any separately authorized
data maintenance. A plugin uninstall does not mutate Core history.

## Model login and network boundaries

The plugin uses the existing Codex Host authentication and does not call an
LLM. A provider/model timeout, Context7 failure, or unsupported desktop host
is recorded as an environment limitation. Do not clear a working provider
proxy merely to test this local stdio MCP.

If GitHub push is required and direct HTTPS fails, use one process-scoped
fallback and verify the remote SHA:

```powershell
git -c http.proxy=http://proxyae.huawei.com:8080 `
  --git-dir=.git-push --work-tree=. push origin HEAD:main
git -c http.proxy=http://proxyae.huawei.com:8080 `
  --git-dir=.git-push ls-remote origin refs/heads/main
```

Do not persist that proxy in Git, npm, Codex, or system settings. Context7 is a
separate network dependency and must be reported `BLOCKED_ENV` when it cannot
be reached.

## Performance measurement

M7 package tests cover bounded retention and fail-open behavior. A production
performance claim still requires the same controlled tool workload with Hook
on/off, median/P95 latency, drop count, and SQLite/CPU/RSS observations. No
such host pair was available for this closure, so `M7_OBSERVER_OVERHEAD` is
`UNVERIFIED`.
