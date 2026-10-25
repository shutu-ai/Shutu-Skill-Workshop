# M7.1 Codex CLI and Desktop Unified Integration

## Scope

M7.1 keeps the existing Workshop Core and read-only seven-tool MCP surface.
The integration order is:

1. verify the real interactive CLI host;
2. use the plugin's native MCP/Skill/Hook package when the host exposes it;
3. use one named compatibility MCP registration when native plugin MCP is not
   exposed;
4. keep Desktop and `codex exec` as independent acceptance hosts.

The package never starts another Codex agent, copies provider credentials, or
changes model/provider configuration.

## Runtime paths

```text
Codex Hook summary
  -> PLUGIN_DATA/hook_events.jsonl
  -> HookSpoolImporter (lock + event-key checkpoint + bounded batch)
  -> existing TraceIngestor / TraceStore
  -> existing DiagnosisService / issue ledger
  -> Workshop MCP query
```

The importer stores only the already-redacted hook summary. It uses the hook
event key, session hash, request hash, tool name, argument-key list, result
status, and source label. It does not import prompt text, tool arguments,
tool results, cookies, bearer tokens, or decrypted credentials.

`HookSpoolImporter` reads under the same lock as the writer, commits Core data
before advancing its checkpoint, and keeps a bounded event-key set. Therefore
an atomic spool replacement is safe, a process crash is retryable, and a
replayed event is a Core duplicate rather than a second Issue occurrence.

MCP `workshop_health`, `workshop_recent_activity`, and
`workshop_list_issues` perform the bounded import on demand. Diagnosis uses
the existing M3 rules and M3 checkpoints; no `hook_issues` table or parallel
ledger is introduced.

## Native versus compatibility

Native and compatibility are separate host modes. The installer never claims
Native success from package installation alone. `install-m71-codex.ps1
-Mode Compat` registers only the matching `shutu-workshop` stdio server and
refuses to overwrite an unrelated registration. `-Mode Native` removes that
matching compatibility registration before leaving the plugin enabled.

The current observed CLI (`codex-cli 0.163.0`) loads the Skill package but did
not expose the plugin MCP namespace or plugin PostToolUse Hook in a native
session. Compatibility mode then produced real interactive calls and results
for `workshop_health` and `workshop_list_issues`.

## Evidence boundary

An interactive Tool Call/Result is required for the CLI gates. Directly
running `run_mcp.py`, reading the cache, or calling `codex exec` is not a
substitute. Hook tests and importer tests prove code behavior only; they do
not prove that a host emitted a real Hook event. Desktop remains independent
and is reported `BLOCKED_ENV` when no GUI host is available.

References:

- https://developers.openai.com/plugins/build/plugins
- https://developers.openai.com/codex/hooks
- https://developers.openai.com/plugins/deploy/desktop-only
