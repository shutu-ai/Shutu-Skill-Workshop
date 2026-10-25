# M6.1 Real-Agent Closure Acceptance

Status at handoff: `PARTIAL` / authenticated provider, inconclusive MCP task execution.

The Codex CLI was real (`codex-cli 0.163.0`), both isolated homes authenticated, and all baseline/candidate artifacts were independently activated. The authenticated pair completed six processes, but only one real MCP call was observed and it failed; five runs emitted no tool event. No FakeProvider, mock transcript, hand-written tool result, or production target was used.

## Gate ledger

| Gate | Result | Evidence or limit |
|---|---|---|
| G0 | PASS | local checkout started at `3be9b51`; live remote baseline `4b070b4` was verified through the proxy; dirty/untracked work was preserved |
| G1 | PASS | M0–M6 tests, compileall, CLI help, and M6.1 tests were run |
| G2 | PASS | both pair-side isolated HOMEs reported authenticated |
| G3 | PASS | both isolated Codex text probes returned `READY` |
| G4 | UNVERIFIED | a Codex tool call proves the host reached MCP, but initialize/list events are not separately captured |
| G5 | INCONCLUSIVE | one real tools/call/result failed; five runs had no tool event |
| G6 | PASS | baseline/candidate bytes and hashes differ |
| G7 | PASS | prelaunch/runtime hashes and scoped activation records match each side |
| G8 | PASS | declared-vs-actual and post-freeze mutation negatives pass |
| G9 | PASS | side commands, processes, homes, and MCP scopes are independent |
| G10 | PASS | `FALSE_EXACT_JOIN_COUNT=0`; ids are scoped by trace/side/case |
| G11 | INCONCLUSIVE | three cases were attempted on both sides; all six task results remain `unknown` |
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
BASELINE_HEAD=4b070b4
FINAL_HEAD=<commit SHA recorded at handoff>
REMOTE_HEAD=<verified origin/main SHA, or BLOCKED_ENV>
M6_1_STATUS=PARTIAL
PROVIDER_AUTH_PRECHECK=PASS
CODEX_REAL_TEXT_RESPONSE=PASS
REAL_CODEX_MCP_INITIALIZE=UNVERIFIED
REAL_CODEX_MCP_TOOLS_LIST=UNVERIFIED
REAL_CODEX_MCP_TOOLS_CALL=PASS
BASELINE_ARTIFACT_ACTIVATION=PASS
CANDIDATE_ARTIFACT_ACTIVATION=PASS
DECLARED_VS_ACTUAL_HASH_NEGATIVE_TEST=PASS
REAL_AGENT_PAIRED_CASES=3
REAL_AGENT_INDEPENDENT_ORACLE=UNVERIFIED
REAL_AGENT_PAIR_EXECUTION=INCONCLUSIVE
REAL_AGENT_OPTIMIZATION_VERDICT=INCONCLUSIVE
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
NEXT_STAGE=diagnose the failed MCP tool result, make each side/case explicitly call its configured server, then require successful MCP result refs plus the independent Oracle before claiming optimization.
```

The pair is an isolated fixture validation, not a production business-environment optimization claim. `REAL_AGENT_OBSERVED` and `REAL_ARTIFACT_ACTIVATED` are intentionally not reported as `REAL_TASK_EXECUTED`.
