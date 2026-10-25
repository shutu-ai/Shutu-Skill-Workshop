# M7.4.0 Architecture and Evidence Probe

## Scope

M7.4 extends the M7.3 quality layer and reuses the existing M1 trace store,
M4 Oracle/evaluation contracts, M6.1 `CodexRealTaskRunner`, and M7.2 stdio
Observer. It does not create a parallel Issue Ledger, copy the default Codex
HOME, or publish a Skill automatically.

| Area | Reused capability | M7.4 closure | Boundary |
|---|---|---|---|
| Skill inventory | `SkillRegistry` and capability snapshots | frozen tree identity and no-skill leakage checks | artifact access is not lifecycle invocation |
| Routing | `SkillRoutingEvaluator` | native Host event, scope, version, and event-ref gate | Codex CLI 0.163.0 exposes no native Skill lifecycle event |
| Three-arm quality | `ThreeArmQualityRunner` | pluggable executor plus `RealCodexThreeArmRunner` | real Pilot requires isolated authenticated Provider |
| Agent evidence | `CodexRealTaskRunner` | no-skill side, post-run hash, sandbox selection, redacted preflight RCA | task completion is not Skill invocation |
| Oracle | external callable | execution and Oracle verdicts remain separate | no Oracle means unknown, never pass |
| MCP | `StdioObserver`, `McpTraceRecorder` | trace-scoped event IDs and pending-request close gate | externally owned Codex shutdown may remain partial |
| Query | `QualityService`, CLI, Workshop MCP | tier/unknown summaries and read-only detail | no auto-release or mutation |

## Evidence tiers

`HOST_OBSERVED` requires a verifiable `skill.loaded`/`skill.invoked` event,
matching `host_event_ref`, scope, and version. `ARTIFACT_ACCESS_OBSERVED` is
used for a discovered or read `SKILL.md`; it cannot enter natural trigger
Precision/Recall/F1. Three-arm results explicitly carry `FIXTURE_EXECUTED` or
real Codex evidence and are not interchangeable.

## Local versus host-dependent results

The local engineering checks and negative cases are executable without a
Provider. The real three-arm Pilot needs a new isolated `CODEX_HOME` with the
user completing normal `codex login` there. The 2026-10-25 preflight reached
the CLI but returned `NOT_AUTHENTICATED`/`AUTHENTICATION`; therefore no real
Pilot claim is made. Native Skill lifecycle events, Desktop lifecycle events,
and paired long-run performance remain `NOT_OBSERVABLE` or `UNVERIFIED`.

## Minimal path

1. Validate routing evidence and freeze A/B/C artifact hashes.
2. Run fixture regression through the existing validator path.
3. After isolated Provider smoke PASS, run 3 cases × 3 arms with a separate
   external Oracle and a fresh workspace/HOME per arm.
4. Query the persisted report read-only; publish nothing automatically.
