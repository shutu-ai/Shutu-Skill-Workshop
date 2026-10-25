# M7 Codex Native Plugin Design

## Runtime shape

```text
Codex host
  ├─ enabled plugin manifest + user-trusted hooks
  ├─ local stdio MCP server
  └─ explicit workshop-review Skill
         │
         ▼
  Workshop Core (one SQLite schema, one evidence model)
    Trace / Capability / Issue / Diagnosis
    Proposal / Evaluation / Release / Observation / Feedback
```

The daily path is passive observation plus on-demand read-only review. The
existing M6.1 Runner remains an explicit A/B validation tool, not a plugin
dependency.

## MCP boundary

`servers/shutu_workshop/mcp_server/service.py` owns the Core-facing adapter;
`server.py` owns only JSON-RPC/MCP framing. The seven first-party tools are:

- `workshop_health`
- `workshop_recent_activity`
- `workshop_list_issues`
- `workshop_issue_detail`
- `workshop_capability_versions`
- `workshop_evaluation_report`
- `workshop_release_status`

Every response has a status, source, evidence tier, and explicit empty/unknown
state. Payload bodies are not returned. A missing issue returns
`NOT_FOUND`; an empty ledger returns `NO_ACTIONABLE_ISSUE` rather than a demo
issue.

## Hook boundary

`hooks/ingest_event.py` reads one bounded JSON object from stdin, writes a
summary-only JSONL spool under `PLUGIN_DATA`, and keeps a bounded retention
window. It exits zero on all observation failures. `hooks/hooks.json` registers
`SessionStart` and `PostToolUse`, but installation does not imply trust; the
user must review the exact definition in `/hooks` on a supporting Codex host.

## Skill boundary

`workshop-review` requires health first, then issue and evidence queries. It
separates Fact, Inference, and Proposal and never approves or publishes a
change. Skill loading/invocation is reported only when the host exposes it;
prompt wording is not used as a proxy.

## Installation state

The repo Marketplace is `.agents/plugins/marketplace.json`. The PowerShell
scripts use the current project or an explicitly supplied isolated
`CODEX_HOME`; they do not change global Git/npm/proxy settings. `codex plugin
remove` owns cache cleanup, while an optional marketplace removal only removes
this repo source.
