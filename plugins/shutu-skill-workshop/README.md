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

The MCP server uses the Codex session working directory by default. Set
`SHUTU_WORKSHOP_WORKSPACE` when the database lives elsewhere. `PLUGIN_ROOT` is
read-only and `PLUGIN_DATA` is the bounded writable hook-data directory.

Hooks remain inactive until Codex shows and trusts their exact definition.
Use `/hooks` in a normal Codex session to review them. If the host does not
expose plugin Hook or Skill events, the plugin remains useful in manual mode
and reports that observability gap instead of inferring usage.

## Safety boundary

All bundled MCP tools are read-only. Proposal, evaluation, release, and
rollback actions stay in the existing Workshop Core and require an explicit
user workflow outside this daily plugin entry point.
