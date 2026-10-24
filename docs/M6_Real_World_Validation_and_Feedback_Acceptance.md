# M6 real-world validation and feedback acceptance

## Overall result

`M6_STATUS=PARTIAL`. M6.0 and M6.4 are implemented with passing regression tests. The real Codex process and isolated MCP registration were exercised, but the provider returned `401 Unauthorized: Missing bearer`, so real MCP calls and paired baseline/candidate task evidence are `BLOCKED_ENV`. OpenCode has no usable provider/plugin run in this workspace and remains `BLOCKED_ENV`. Production target release is `NOT_EXECUTED`.

## Gate matrix

| Gate | Status | Evidence |
| --- | --- | --- |
| G0 | `PASS_REPORTED` | baseline HEAD, dirty worktree and isolated scope recorded |
| G1 | `PASS_REPORTED` | before binding no longer requires future release ID |
| G2 | `PASS_REPORTED` | manifest/capability bridge required; scoped correlation test |
| G3 | `PASS_REPORTED` | denominator-based 100/200 sample test |
| G4 | `PASS_REPORTED` | unknown/composition/sample gates retained |
| G5 | `PASS_REPORTED` | simulation guard and late-event recomputation test |
| G6 | `PASS_REPORTED` | real local Codex CLI launched in isolated home |
| G7 | `BLOCKED_ENV` | provider auth failed before Codex could call fixture tools |
| G8 | `PASS_REPORTED` | Skill lifecycle is explicitly `NOT_OBSERVABLE` |
| G9 | `PASS_REPORTED` | redaction, scoped IDs and bounded capture retained |
| G10 | `BLOCKED_ENV` | no completed real baseline/candidate pair |
| G11 | `PASS_REPORTED` | Oracle is a caller-supplied independent callback; no answer injection |
| G12 | `PASS_REPORTED` | real, isolated, fixture and simulated tiers are distinct |
| G13 | `PASS_REPORTED` | execution manifest and artifact/capability hashes are persisted |
| G14 | `PASS_REPORTED` | OpenCode version/config/plugin diagnostics were run |
| G15 | `BLOCKED_ENV` | no real OpenCode Provider tool execution |
| G16 | `PASS_REPORTED` | observation-to-feedback state mapping is tested |
| G17 | `PASS_REPORTED` | links require verified rows and trace occurrences |
| G18 | `PASS_REPORTED` | idempotency plus late-trace revision is tested |
| G19 | `PASS_REPORTED` | no PR, deploy, rollback or background action is automatic |
| G20 | `PASS_REPORTED` | additive v9 migration and prior M5 tests pass |
| G21 | `UNVERIFIED` | no dedicated long-running performance benchmark was claimed |
| G22 | `PASS_REPORTED` | Windows runbook and reproducible blocked commands are included |
| G23 | `PASS_REPORTED` | final commit/remote verification is recorded in the terminal handoff |

## Required status fields

```text
M6_STATUS=PARTIAL
M6_0_BEFORE_TRACE_BINDING=PASS_REPORTED
M6_0_RATE_COMPARISON=PASS_REPORTED
M6_0_SIMULATION_GUARD=PASS_REPORTED
M6_1_REAL_CODEX_MCP=BLOCKED_ENV
M6_1_SKILL_INVOCATION_OBSERVABILITY=NOT_OBSERVABLE
M6_2_REAL_AGENT_BASELINE_CANDIDATE=BLOCKED_ENV
M6_2_REAL_TASK_EVIDENCE_TIER=BLOCKED_ENV
M6_3_OPENCODE_REAL_TOOL_HOOK=BLOCKED_ENV
M6_4_FEEDBACK_LOOP=PASS_REPORTED
M6_4_FALSE_EXACT_JOIN_COUNT=0 in the scoped-correlation fixture
REAL_AGENT_OBSERVATION_SAMPLE_COUNT=0 completed task samples
REAL_AGENT_PAIRED_CASES=0
REAL_LLM_DIAGNOSIS=BLOCKED_ENV
OFFICIAL_MCP_SDK_INTEROP=UNVERIFIED
PRODUCTION_TARGET_RELEASE=NOT_EXECUTED
```

## Commands

```powershell
$env:PYTHONPATH = 'servers'
python -m unittest discover -s tests -v
python -m compileall -q servers/shutu_workshop
python -m shutu_workshop --help
git diff --check
```

For the real probe, create a UTF-8 JSON task file under `.test-m6/` and run `scripts\m6_real_agent.ps1`. The command must be allowed to return exit code 2 for `BLOCKED_ENV`; that is evidence, not a passing task result.
