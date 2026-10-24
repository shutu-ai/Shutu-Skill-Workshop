# M2.6 Real Integration Probe

Date: 2026-10-24
Workspace: `D:\dev-projects\Shutu-Skill-Workshop`
Baseline: `d4ddfa874500df699195c88bd33fbb1f5f5d1620`

This is the evidence log for the real integration closure. It separates
source-confirmed facts, runtime observations, inferences, and unverified
compatibility work.

## Status vocabulary

- `SOURCE_CONFIRMED`: supported by the installed CLI or official documentation.
- `RUNTIME_VERIFIED`: observed in this workspace with the command shown.
- `INFERRED`: a local design conclusion, not a host guarantee.
- `BLOCKED_ENV`: the code path was reached, but the host/provider/network did
  not permit the requested next step.
- `UNVERIFIED`: no compatible SDK or independent implementation was available
  for the check.

## Runtime baseline

| Item | Result | Evidence |
|---|---|---|
| OS/shell | `Windows / PowerShell` | `environment_context` |
| Python | `3.14.7` | `python --version` |
| Node/npm | `v24.19.0 / 12.0.2` | `node --version`, `npm --version` |
| Codex CLI | `0.163.0` | `codex --version` |
| OpenCode | `2.1.3` | `opencode --version` |
| local fixture | available | `tests/fixtures/mcp_test_server.py` |
| official MCP SDK | not installed | no SDK package was used in the probe |

The local probe data, SQLite files, JSONL, and temporary project configuration
are under `.test-m26/`, which is ignored and is not a deliverable.

## Official interface checks

The installed CLIs confirmed the supported entry points:

```powershell
codex exec --help
codex mcp add --help
codex app-server --help
opencode run --help
opencode mcp list
```

The current OpenCode V2 plugin contract uses a project-local
`.opencode/plugins/<name>/index.ts`, `Plugin.define`, `setup`, and tool hooks.
The official MCP configuration uses a remote URL, while Codex supports a
Streamable HTTP URL through `codex mcp add` or `[mcp_servers.<name>] url`.

Context7 was attempted with a process-local proxy:

```powershell
$env:HTTP_PROXY = 'http://proxyae.huawei.com:8080'
$env:HTTPS_PROXY = 'http://proxyae.huawei.com:8080'
$env:ALL_PROXY = 'http://proxyae.huawei.com:8080'
npx --yes ctx7@latest --help
```

The client returned no usable result within the bounded probe. The implementation
therefore uses the installed CLI help and the official OpenCode, Codex, and MCP
documentation as the source-confirmed references; this is not an SDK interop
claim.

## OpenCode sink and plugin

The test project was `.test-m26/opencode/`. The plugin was copied into the
project's real `.opencode/plugins/shutu-workshop-observer/index.ts` path and
loaded by OpenCode itself. The sink was a loopback HTTP listener on
`127.0.0.1`, authenticated with a temporary token and backed by SQLite.

`RUNTIME_VERIFIED` evidence:

- `opencode mcp list` reported the project-local `shutu_fixture` remote as
  connected through the relay.
- The sink database contains one real `observer.ready` event with
  `host_version=2.1.3` and `plugin_api_generation=v2`.
- The event was emitted by the running plugin, not by a direct adapter fixture.
- Sink requests are bounded, authenticated, allow-listed, metadata-only, and
  fail-open for the agent process. No input, output, prompt, or permission
  body is sent by `plugin.ts`.

`OPENCODE_REAL_TOOL_HOOK=BLOCKED_ENV`: a bounded real
`opencode run --standalone --format json --auto` attempt repeatedly emitted
`step_start` for the same session for 30 seconds and was stopped with Ctrl+C.
The sink contained no `tool.before`/`tool.after`, and the relay contained no
OpenCode `tools/call` for that attempt. This is an execution/provider block,
not evidence that the plugin was not loaded. No global OpenCode configuration,
network policy, or authentication state was changed.

## Codex and MCP

The Codex probe used the installed CLI with a temporary command-line URL
override pointing at a loopback relay and the no-side-effect fixture. The first
token-protected attempt correctly reached the relay and received `401` because
the current CLI did not honor the ad-hoc header override. A second isolated
loopback relay without a listener token separated that configuration issue from
the actual agent path.

`RUNTIME_VERIFIED` successful run:

```powershell
codex exec --json --ephemeral --skip-git-repo-check --sandbox read-only `
  -c 'mcp_servers.shutu_fixture.url="http://127.0.0.1:18768/mcp"' `
  'Use only the shutu_fixture server. Call its echo tool with text exactly M26_REAL_CODEX, then report the returned result. Do not use any other tool.'
```

Observed result: Codex discovered `shutu_fixture.echo`, sent
`M26_REAL_CODEX`, and received `M26_REAL_CODEX`. The relay SQLite trace contains
`initialize`, `tools/list`, `tools/call`, and the matching `tool_result`.
The captured JSONL was also ingested by `CodexAdapter` into
`trace_codex_real_m26` with 10 accepted events and no rejects or conflicts.

The same run logged a model-refresh timeout and ignored obsolete user hook
settings. Those diagnostics did not prevent the fixture MCP call, and they are
recorded as host noise rather than treated as a successful model-health claim.

## Correlation and capture checks

- Bare `request_id=1` across Agent and MCP sources produces no cross-source
  exact join.
- The same ID on two MCP connections remains separated by
  `mcp_connection`; only an explicit verified bridge manifest can join sources.
- `FALSE_EXACT_JOIN_COUNT=0` in the M2.6 regression test.
- Stream capture bounds stdout/stderr, ingests complete JSONL lines in batches,
  records partial/oversize input, preserves the first trace identity on restart,
  and makes repeated source events idempotent.
- The relay forwards application status/body/SSE/error semantics and never
  retries a tool call when observation fails.

Local fixture-only timing (30 warmed requests, new loopback HTTP connection per
request) was: direct median `2.353 ms`, relay median `33.211 ms`, median
overhead `30.858 ms`; p95 was `9.324 ms` direct versus `60.733 ms` through the
relay. This is an environment-specific diagnostic, not a production
performance guarantee or an official SDK comparison.

## Current conclusion

`M2_6_SOURCE_PROBE=PARTIAL`: the source contracts are clear and the local
runtime probes are reproducible, but Context7 did not return and the official
SDK cross-implementation check remains unverified. Codex + MCP is closed on the
local fixture; OpenCode plugin loading and MCP handshake are closed, while the
OpenCode Agent tool hook remains `BLOCKED_ENV`.
