# M2.6 Operations Runbook

This runbook starts only local, bounded, no-side-effect processes. It does not
change global Codex/OpenCode configuration or the machine proxy.

## 1. Prepare an isolated probe

```powershell
Set-Location D:\dev-projects\Shutu-Skill-Workshop
New-Item -ItemType Directory -Force .test-m26\fixture, .test-m26\relay-db, .test-m26\sink-db | Out-Null
$env:PYTHONPATH = 'servers;tests'
```

Use temporary values for the examples below. Never put a real credential in a
command copied into Git, a trace database, or a log.

## 2. Start the no-side-effect fixture

```powershell
python -m fixtures.mcp_test_server --transport http --host 127.0.0.1 --port 18765 --protocol legacy
```

The fixture exposes `echo`, `add`, and a deterministic error result. It does
not write business data.

## 3. Start the observer sink and relay

In separate PowerShell windows:

```powershell
$env:SHUTU_WORKSHOP_EVENT_SINK_TOKEN = '<temporary-sink-token>'
python -m shutu_workshop.adapters.opencode.event_sink `
  --workspace .test-m26\sink-db --host 127.0.0.1 --port 18766 `
  --token-env-var SHUTU_WORKSHOP_EVENT_SINK_TOKEN
```

```powershell
$env:SHUTU_WORKSHOP_RELAY_TOKEN = '<temporary-relay-token>'
python -m shutu_workshop.observers.mcp.relay `
  --workspace .test-m26\relay-db `
  --upstream http://127.0.0.1:18765/mcp `
  --host 127.0.0.1 --port 18767 `
  --token-env-var SHUTU_WORKSHOP_RELAY_TOKEN
```

Stop each process with Ctrl+C. The relay and sink are not daemons and do not
start automatically.

## 4. Configure OpenCode in the probe project

Place the project-local V2 plugin at:

```text
.test-m26\opencode\.opencode\plugins\shutu-workshop-observer\index.ts
```

Set process-local variables and configure the project-local remote MCP URL:

```powershell
$env:SHUTU_WORKSHOP_EVENT_SINK = 'http://127.0.0.1:18766/events'
$env:SHUTU_WORKSHOP_EVENT_SINK_TOKEN = '<temporary-sink-token>'
$env:SHUTU_WORKSHOP_RELAY_TOKEN = '<temporary-relay-token>'
Set-Location .test-m26\opencode
opencode mcp add shutu_fixture --url http://127.0.0.1:18767/mcp
```

If the installed OpenCode version does not substitute environment references in
remote headers, use a temporary literal token in this ignored project file
only, or run an isolated relay without a listener token. Do not place that
value in the repository. Confirm the actual project config with:

```powershell
opencode mcp list --log-level error
```

`shutu_fixture connected` proves MCP handshake/list connectivity, not a model
tool call. A real Agent tool call must also produce the sink `tool.before` and
`tool.after` records.

## 5. Configure and run Codex without global mutation

For the simplest local fixture probe, use a loopback relay without a listener
token and a command-line URL override:

```powershell
codex exec --json --ephemeral --skip-git-repo-check --sandbox read-only `
  -c 'mcp_servers.shutu_fixture.url="http://127.0.0.1:18768/mcp"' `
  'Use only the shutu_fixture server. Call its echo tool with text exactly M26_REAL_CODEX, then report the returned result. Do not use any other tool.'
```

For a token-protected deployment, use the installed Codex-supported
`--bearer-token-env-var` configuration in an isolated Codex config directory;
do not add a temporary server to the user's normal config. The relay accepts a
Bearer token as well as its project-local test header.

## 6. Inspect evidence

```powershell
$env:PYTHONPATH = 'servers'
python -m shutu_workshop --workspace .test-m26\sink-db trace events --adapter-id opencode
python -m shutu_workshop --workspace .test-m26\relay-db trace events --adapter-id mcp-observer-relay
python -m shutu_workshop --workspace .test-m26\codex-agent-db trace events --adapter-id codex
```

Look for:

- sink: `observer.ready`, then real `tool.before`/`tool.after` if the Agent
  actually called a tool;
- relay: `mcp.initialize`, `mcp.tools_list`, `mcp.tool_call`, and
  `mcp.tool_result` with the same connection scope;
- Codex: `task.started`, `tool.started`, `tool.result`, and `task.finished`.

Never interpret an Agent's final “OK” as proof of a tool call. Compare the
original JSON-RPC result, the observer event, and the Agent event separately.

## 7. Failure diagnosis

| Symptom | Check | Meaning |
|---|---|---|
| sink `401` | token env and `Authorization` header | authentication/configuration issue |
| relay `502` | fixture process and upstream URL | upstream unavailable; no retry was issued |
| `tools/list` works, no Agent tool event | provider/model run output | Agent execution block; mark `BLOCKED_ENV` |
| repeated same MCP ID | `mcp_connection` in trace metadata | expected IDs are connection-scoped |
| partial line/oversize | `CaptureResult.errors` | bounded capture evidence, not a fake event |
| SQLite busy | retry observation only after bounded handling | business forwarding must remain fail-open |
| Context7 no response | process-local proxy variables and bounded retry | use official docs fallback; do not claim SDK verification |

## 8. Clean stop

Stop the fixture, relay, sink, and any Agent process with Ctrl+C. Inspect
`git status --short` and confirm `.test-m26/` is ignored. Do not use `git clean`,
reset, or a global configuration cleanup as part of normal probe teardown.
