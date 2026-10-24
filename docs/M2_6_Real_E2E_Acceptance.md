# M2.6 Real E2E Acceptance

Date: 2026-10-24.  Results below distinguish fixture/runtime checks from
provider-blocked work; a passing unit suite does not upgrade a blocked Agent
run.

| Gate | Result | Evidence |
|---|---|---|
| G0 Git baseline/isolation | PASS | baseline recorded; `.test-m26/` ignored; dirty M0/M1 work preserved |
| G1 M0/M1/M2 regression | PASS | full suite run after implementation |
| G2 OpenCode sink contract | PASS | authenticated bounded sink test and real `observer.ready` |
| G3 real OpenCode plugin load | PASS | project-local V2 plugin loaded by OpenCode 2.1.3 |
| G4 OpenCode tool before/after | BLOCKED_ENV | provider run emitted only repeated `step_start`; no tool call |
| G5 scoped MCP correlation | PASS | repeated IDs on separate connections remain separate |
| G6 explicit bridge evidence | PASS | verified manifest bridge joins; bare ID does not |
| G7 MCP client-configurable relay | PASS | Codex and OpenCode point at loopback relay |
| G8 legacy/modern/unknown handling | PASS | legacy/modern fixtures and unknown protocol labels are preserved |
| G9 relay business transparency | PASS | body/status/error/SSE/notification/concurrency tests; no retry |
| G10 observation fail-open | PASS | bounded failures are reported without resubmission |
| G11 streaming capture | PASS | bounded stdout/stderr, partial line, restart/idempotence tests |
| G12 privacy/permissions | PASS | loopback binding, temporary tokens, redacted metadata, no global edits |
| G13 real Codex + MCP | PASS | real `tools/list` + `tools/call`; Agent and observer traces |
| G14 real OpenCode + MCP | BLOCKED_ENV | MCP handshake/list works; Agent provider did not emit tool call |
| G15 official SDK interop/overhead | UNVERIFIED | no compatible SDK installed; only local fixture timing |
| G16 delivery completeness | PASS | four docs, tests, commit, and proxy push |

## Reproduction commands

```powershell
$env:PYTHONPATH = 'servers'
python -m unittest discover -s tests -v
python -m compileall -q servers/shutu_workshop
python -m unittest tests.test_m26_integration_closure -v
```

The real Codex command, fixture/relay startup, OpenCode project configuration,
and evidence interpretation are documented in
`docs/M2_6_Real_Integration_Probe.md` and
`docs/M2_6_Operations_Runbook.md`.

## Acceptance facts

- `FALSE_EXACT_JOIN_COUNT=0`.
- Codex real fixture result: `M26_REAL_CODEX` in and out; captured Agent JSONL
  imported with 10 accepted events and no rejects/conflicts.
- Codex observer result: relay trace contains `initialize`, `tools/list`,
  `tools/call`, and `tool_result`.
- OpenCode real plugin result: `observer.ready` in the sink with V2 and host
  version metadata.
- OpenCode real Agent tool result: not observed because the local provider run
  did not progress beyond repeated `step_start`; this remains
  `BLOCKED_ENV`, not `PASS`.
- No production database, SSH host, external business MCP, global config, or
  user credential was modified.

## Release decision

`M2_6_STATUS=PARTIAL`. The requested implementation, local relay, bounded
capture, correlation guard, Codex real E2E, and OpenCode plugin-load evidence
are complete. The remaining host-dependent OpenCode Agent tool hook and
official SDK interoperability checks must not be hidden by a green regression
suite.
