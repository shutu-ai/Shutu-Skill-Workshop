# M7.1 Host Capability Matrix

Observed on 2026-10-25 from repository baseline
`112c73b735c07dc331dbdc04833a94d59389c58f` before M7.1 changes.

| Capability | Interactive CLI Native | Interactive CLI Compat | Windows Desktop | `codex exec` |
|---|---|---|---|---|
| Host version | `codex-cli 0.163.0` | `codex-cli 0.163.0` | `NOT_AVAILABLE` | `codex-cli 0.163.0` |
| Plugin discovery | `PASS` (`plugin list --json`) | `PASS` | `BLOCKED_ENV` | `HOST_UNSUPPORTED` |
| Plugin enablement | `PASS` | `PASS` | `BLOCKED_ENV` | `HOST_UNSUPPORTED` |
| MCP process start | `HOST_UNSUPPORTED` | `PASS` | `BLOCKED_ENV` | `HOST_UNSUPPORTED` |
| `tools/list` / tool exposure | `HOST_UNSUPPORTED` | `PASS` by real Tool Calls | `BLOCKED_ENV` | `HOST_UNSUPPORTED` |
| `workshop_health` Tool Call/Result | `HOST_UNSUPPORTED` | `PASS` | `BLOCKED_ENV` | `HOST_UNSUPPORTED` |
| `workshop_list_issues` Tool Call/Result | `HOST_UNSUPPORTED` | `PASS` | `BLOCKED_ENV` | `HOST_UNSUPPORTED` |
| `workshop-review` Skill discovery | `PASS` | `PASS` | `BLOCKED_ENV` | `HOST_UNSUPPORTED` |
| Skill workflow with real MCP | `NOT_OBSERVABLE` | `PASS` | `BLOCKED_ENV` | `HOST_UNSUPPORTED` |
| Skill lifecycle event | `NOT_OBSERVABLE` | `NOT_OBSERVABLE` | `BLOCKED_ENV` | `NOT_OBSERVABLE` |
| Plugin Hook installed/active | `HOST_UNSUPPORTED` (`PostToolUse 0/0`) | `HOST_UNSUPPORTED` | `BLOCKED_ENV` | `HOST_UNSUPPORTED` |
| Real Hook capture | `HOST_UNSUPPORTED` | `HOST_UNSUPPORTED` | `BLOCKED_ENV` | `HOST_UNSUPPORTED` |
| Hook spool import code | `PASS` by bounded tests | `PASS` by bounded tests | `BLOCKED_ENV` for real event | `HOST_UNSUPPORTED` |
| Existing Issue Detector path | `PARTIAL` | `PASS` for controlled importer tests | `BLOCKED_ENV` for real Hook | `HOST_UNSUPPORTED` |
| Provider authentication | `PASS` | `PASS` | `BLOCKED_ENV` | `PASS`/call incomplete |
| Additional provider auth | `NO` | `NO` | `UNKNOWN` | `NO` |

The Native column is intentionally not inferred from package installation. The
Compat column is the accepted CLI integration mode for this host. Desktop was
not available to this execution environment, and `codex exec` emitted an
in-progress MCP item without a completed Tool Result, so it remains separate
and unresolved.
