# M6.1 Real-Agent Closure Acceptance

Status at handoff: `PARTIAL` / provider `BLOCKED_ENV`.

The Codex CLI was real (`codex-cli 0.163.0`) and the local baseline/candidate artifacts were independently activated. The isolated provider home was not authenticated, so the real Codex task stopped before any provider-backed MCP call. No FakeProvider, mock transcript, hand-written tool result, or production target was used.

## Gate ledger

| Gate | Result | Evidence or limit |
|---|---|---|
| G0 | BLOCKED | actual baseline is `3be9b51`, while the task names unavailable `4b070b4`; dirty/untracked work was preserved |
| G1 | PASS | M0–M6 tests, compileall, CLI help, and M6.1 tests were run |
| G2 | PARTIAL | isolated HOME/version/auth layers are implemented; auth is not available |
| G3 | BLOCKED_ENV | no provider-backed Codex text response |
| G4 | BLOCKED_ENV | direct fixture handshake passed, but Codex-host initialize was not observed |
| G5 | BLOCKED_ENV | Codex-host tools/call/result count is zero |
| G6 | PASS | baseline/candidate bytes and hashes differ |
| G7 | PASS | prelaunch/runtime hashes and scoped activation records match each side |
| G8 | PASS | declared-vs-actual and post-freeze mutation negatives pass |
| G9 | PASS | side commands, processes, homes, and MCP scopes are independent |
| G10 | PASS | `FALSE_EXACT_JOIN_COUNT=0`; ids are scoped by trace/side/case |
| G11 | BLOCKED_ENV | three cases were attempted on both sides, but provider prevented task completion |
| G12 | PASS | holdout Oracle reads observed result events only; it does not receive expected answers |
| G13 | PASS | pair records comparable runner/environment fields and side-specific activation data |
| G14 | PASS | activation/tool evidence is required; metadata alone cannot promote a run |
| G15 | PASS | no-tool/401/timeout paths remain blocked or unknown, never success |
| G16 | PASS | pre-release identity is separate from later release linking |
| G17 | PASS | unknown historical binding stays unknown; old traces are not rewritten |
| G18 | PARTIAL | observation/feedback integration is implemented; this run cannot produce a supported improvement signal |
| G19 | PASS | no production release or production target mutation was attempted |
| G20 | BLOCKED_ENV | OpenCode used local read/glob tools, but no configured MCP hook/provider call was observed |
| G21 | PASS | redaction, timeout process-tree cleanup, schema migration, and regression checks are covered |
| G22 | PASS | required runbook, probe, design, acceptance, and JSON artifacts are present |
| G23 | PASS or BLOCKED_ENV | becomes PASS only after remote SHA verification; otherwise report the actual push blocker |

## Required report

```text
BASELINE_HEAD=3be9b51
FINAL_HEAD=<commit SHA recorded at handoff>
REMOTE_HEAD=<verified origin/main SHA, or BLOCKED_ENV>
M6_1_STATUS=PARTIAL
PROVIDER_AUTH_PRECHECK=BLOCKED_ENV
CODEX_REAL_TEXT_RESPONSE=BLOCKED_ENV
REAL_CODEX_MCP_INITIALIZE=BLOCKED_ENV
REAL_CODEX_MCP_TOOLS_LIST=BLOCKED_ENV
REAL_CODEX_MCP_TOOLS_CALL=BLOCKED_ENV
BASELINE_ARTIFACT_ACTIVATION=PASS
CANDIDATE_ARTIFACT_ACTIVATION=PASS
DECLARED_VS_ACTUAL_HASH_NEGATIVE_TEST=PASS
REAL_AGENT_PAIRED_CASES=3
REAL_AGENT_INDEPENDENT_ORACLE=BLOCKED_ENV
REAL_AGENT_PAIR_EXECUTION=BLOCKED_ENV
REAL_AGENT_OPTIMIZATION_VERDICT=BLOCKED_ENV
HISTORICAL_BEFORE_BINDING=PASS
REAL_OBSERVATION_FEEDBACK=PARTIAL
OPEN_CODE_REAL_TOOL_HOOK=BLOCKED_ENV
OFFICIAL_MCP_SDK_INTEROP=UNVERIFIED
FALSE_EXACT_JOIN_COUNT=0
PRODUCTION_TARGET_RELEASE=NOT_EXECUTED
M0_M6_REGRESSION=PASS
TESTS=79/79; skipped=0
GIT_PUSH=<actual result>
COMMIT=<actual commit SHA>
DOCS=docs/M6_1_Provider_Auth_Probe.md, docs/M6_1_Artifact_Activation_And_Paired_Experiment_Design.md, docs/M6_1_Real_Agent_Closure_Acceptance.md, docs/M6_1_Operations_Runbook.md, docs/M6_1_Acceptance.json
NEXT_STAGE=authenticate an explicitly isolated Codex HOME, rerun provider preflight, then rerun all three paired cases and require non-zero Codex MCP call/result refs plus the independent Oracle before claiming optimization.
```

The pair is an isolated fixture validation, not a production business-environment optimization claim. `REAL_AGENT_OBSERVED` and `REAL_ARTIFACT_ACTIVATED` are intentionally not reported as `REAL_TASK_EXECUTED`.
