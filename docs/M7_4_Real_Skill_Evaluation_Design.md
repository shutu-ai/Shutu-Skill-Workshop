# M7.4 Real Skill Evaluation Design

## Identity and manifest

`RealCodexThreeArmRunner` freezes a tree SHA-256 for `no_skill`, `baseline`,
and `candidate`, rejects a `SKILL.md` in the no-skill arm, and rejects equal
baseline/candidate hashes. Each execution is identified by
`experiment_id`, `pair_id`, `case_id`, `arm`, `trace_id`, and an execution
manifest hash. The manifest binds the task-input hash, artifact hash, runner
ID/version, model, host, sandbox, and environment fingerprint. Task input is
passed to the child only at execution time; the report stores its hash and
redacted evidence, not sensitive plaintext.

The three required case categories are `target`, `normal-regression`, and
`holdout`. Every arm receives the same `QualityCase.runner_input()` and the
runner never receives an answer key, another arm's result, or the Oracle
function. The external Oracle is called only after the agent execution and
can validate files/tests in the arm workspace. Missing execution, timeout,
Provider error, missing result, or Oracle error is `unknown`.

## Separate conclusions

Reports keep these conclusions independent:

- `SKILL_CONDITION_VERIFIED`: artifact identity and isolated workspace/HOME
  condition;
- `REAL_TASK_EXECUTED`: actual Codex process evidence;
- `SKILL_INVOCATION_OBSERVED`: native lifecycle event, currently
  `NOT_OBSERVABLE` on this Host;
- `EXPERIMENT_CONDITION_EFFECT`: A/B/C task-quality difference, never a
  natural Skill trigger rate;
- `PRODUCTION_BENEFIT_VERDICT`: only improved after complete, comparable,
  Oracle-verified cases with no normal/holdout regression.

## Integrity gates

Prelaunch, runtime, and postrun artifact hashes are retained. A changed
artifact turns the execution into unknown/blocked evidence. Correlation uses
trace and connection scope; a bare JSON-RPC ID or same tool name cannot form
an exact cross-arm join. Observer close is `COMPLETE` only after EOF, no
pending request, and no recording error; otherwise it remains `PARTIAL`.
