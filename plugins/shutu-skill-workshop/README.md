# Shutu Skill Workshop Codex plugin

This is a local, desktop-only Codex plugin for read-only Workshop evidence.
It bundles the `workshop-review` Skill, a local stdio MCP server, and optional
summary-only Hooks. It never starts a second Codex, asks for an OpenAI login,
publishes a release, or changes a Skill/MCP configuration.

## Local use

Add the repository marketplace from the repository root, then install the
plugin with the provided PowerShell script:

```powershell
pwsh -NoProfile -File .\scripts\install-m7-plugin.ps1
codex plugin list --json
```

For the current Codex CLI, use the explicit compatibility registration when
the interactive host does not expose plugin MCP tools:

```powershell
powershell -NoProfile -File .\scripts\install-m71-codex.ps1 -Mode Compat
powershell -NoProfile -File .\scripts\verify-m71-codex.ps1 -Mode Compat -Json
```

`Native` keeps only the plugin package. `Compat` keeps the same package for
Skill metadata and adds one named `shutu-workshop` stdio MCP server. The
installer refuses an unrelated server with that name and the uninstaller
removes only the matching Workshop registration.

The MCP server uses the Codex session working directory by default. Set
`SHUTU_WORKSHOP_WORKSPACE` when the database lives elsewhere. `PLUGIN_ROOT` is
read-only and `PLUGIN_DATA` is the bounded writable hook-data directory.

Hooks remain inactive until Codex shows and trusts their exact definition.
Use `/hooks` in a normal Codex session to review them. If the host does not
expose plugin Hook or Skill events, the plugin remains useful in manual mode
and reports that observability gap instead of inferring usage.

When trusted host hooks are available, bounded summaries are written to
`PLUGIN_DATA/hook_events.jsonl`. Workshop MCP queries perform a bounded,
idempotent on-demand import into the existing Core TraceStore and run the
existing deterministic rules; no second issue ledger is created.

## Safety boundary

All bundled MCP tools are read-only. Proposal, evaluation, release, and
rollback actions stay in the existing Workshop Core and require an explicit
user workflow outside this daily plugin entry point.
