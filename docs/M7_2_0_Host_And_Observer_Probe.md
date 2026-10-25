# M7.2.0 Host and Observer Probe

Observed on 2026-10-25 from the Windows checkout.

## Baseline and host

| Item | Observed value |
|---|---|
| baseline/source HEAD | `fc5717fd2c7e1f370b2aeb702b228bc105200b4a` |
| Codex CLI | `codex-cli 0.163.0` |
| authentication | `Logged in using ChatGPT` |
| Codex HOME | `C:\Users\<user>\.codex` (redacted) |
| target registration | `shutu-workshop`, one stdio entry |
| original child | `python ...\plugins\shutu-skill-workshop\bin\run_mcp.py` |
| observer entry | `python ...\scripts\m72_stdio_observer.py` |
| preserved environment | names `PLUGIN_DATA`, `SHUTU_WORKSHOP_SOURCE`; values redacted to repo/user scopes |

The owned Compat registration was verified before switching. The M7.2 installer
saves its command, arguments, environment names/values, and working directory in
the private manifest under the selected Codex HOME. It refuses an absent or
non-owned `shutu-workshop` entry and supports rollback.

## Support matrix

| Capability | Result | Evidence |
|---|---|---|
| JSON-lines stdio | PASS | unit and direct observer probe |
| `Content-Length` framing | PASS | byte-for-byte forwarding test |
| default long-lived session | PASS | no fixed 30-second lifetime; controlled slow child |
| startup/handshake timeout | PASS | explicit `startup_timeout` test |
| idle timeout | PASS as opt-in | explicit constructor/CLI option; not enabled for normal CLI |
| bounded test lifetime | PASS as opt-in | `--timeout`; default is unset |
| server-to-client request direction | PASS | correlation regression test |
| same JSON-RPC id across connections | PASS | connection-scoped pending map |
| cancel/protocol/business/timeout/EOF states | PASS | state classification and lifecycle tests |
| Windows child cleanup | PASS | process-tree stop/kill path |
| raw stdout bytes | PASS | observer writes the original frame unchanged |
| observer failure behavior | PASS | failures are recorded and calls are not retried |
| real interactive Codex MCP calls | PASS | live trace below |
| real Codex PostToolUse Hook | HOST_UNSUPPORTED | current host does not expose Workshop PostToolUse |
| Desktop acceptance | BLOCKED_ENV | no verifiable Desktop GUI in this environment |
| `codex exec` completed MCP result | HOST_UNSUPPORTED | prior host probe did not expose a completed result |

## Real host evidence

The normal interactive `codex` session called the registered Workshop server.
The Observer trace was persisted in the existing SQLite store:

- trace: `mcp-trace_a53de005239e4002b5eb659791f0333f`
- success calls/results: `workshop_health` request `2`, and
  `workshop_list_issues` request `3`, each with paired `mcp.tool_call` and
  `mcp.tool_result` events;
- controlled failure: `workshop_issue_detail` request `4` returned
  `INVALID_ARGUMENT` / `issue_id is required`, followed by a real
  `workshop_list_issues` request `5` call;
- error event: `mcp-observer:server_to_client:10`, `error_type=mcp.error`,
  `correlation_quality=exact`;
- existing M3 R01 occurrence: `issue_1df517b8b7fe48edaff7195a60023c07`, with
  an evidence reference back to that trace/event.

The stored payloads are summaries only. Raw prompts, tool arguments/results,
credentials, and bearer/cookie values were not copied into the evidence.

## Known probe limits

The current app-server can retain MCP child processes after a disconnected TUI
client. The runbook therefore treats process cleanup and `incomplete` trace
closure as an explicit operational step. This does not change the Observer's
normal connection semantics. A paired baseline/candidate overhead measurement
was not collected; overhead is reported as `UNVERIFIED`.
