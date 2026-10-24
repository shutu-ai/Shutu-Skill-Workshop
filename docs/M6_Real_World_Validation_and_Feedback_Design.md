# M6 real-world validation and feedback design

## Boundary

M6 is a read-only validation and feedback layer. It does not enable production target writes, automatic rollback, pull requests, background observers or global Codex/OpenCode configuration changes. M5 remains limited to `isolated_workspace`, and a real target release remains `NOT_EXECUTED`.

## Evidence tiers

The observation layer keeps evidence provenance explicit:

- `REAL_AGENT_OBSERVED`: a real host emitted the trace; a completed task still needs an independent Oracle before it can support a task result.
- `ISOLATED_ARTIFACT_EXECUTED`: the repository's bounded file-backed runner executed a frozen artifact.
- `FIXTURE_EXECUTED`: a controlled fixture ran.
- `SIMULATED_TRACE`: deliberately generated demonstration data.
- `UNKNOWN`: not enough provenance to compare.

`SIMULATED_TRACE` is never upgraded. It produces `SIMULATION_ONLY / DEMONSTRATION_ONLY` and cannot close an Issue, accept a Proposal or unlock a release.

## Comparison contract

Before release, freeze a `FrozenComparisonManifest`. A before trace is eligible when its baseline artifact/capability, task, environment, runner and manifest bridge match; it does not require a future release ID. An after trace additionally has to match the ReleaseLink and candidate artifact. Missing identity evidence excludes the trace or leaves it unknown rather than joining by time proximity, prompt text or an unscoped request ID.

Each phase stores:

```text
n_total, n_observed, n_success, n_failed, n_unknown
success_rate, failure_rate, unknown_rate
tool_call_count, retry_count, duration median/p95
task/environment/runner composition
trace IDs and content digests
excluded counts and reasons
source evidence tiers and fingerprint
```

Comparison checks composition before interpreting rates. Failure rates use observed denominators. If unpaired sample sizes differ, the result is `NO_CLEAR_CHANGE`; if evidence is missing, the result is `INSUFFICIENT_EVIDENCE` or `NOT_COMPARABLE`.

## Real Codex runner

`CodexRealTaskRunner` is deliberately small:

1. resolve the Windows executable shim with `shutil.which`;
2. use a caller-supplied isolated `CODEX_HOME` and read-only `codex exec --json --ephemeral`;
3. stream bounded JSONL through the existing `CodexAdapter` and M1 ingestion path;
4. bind case, pair, experiment, artifact, capability and manifest metadata after capture;
5. retain only redacted output samples, digests and event references;
6. call an independent Oracle supplied by the caller, never derive the expected answer from the prompt;
7. persist an idempotent `execution_manifest_hash` in `real_agent_runs`.

Provider/transport errors with no tool events are classified as `BLOCKED_ENV`. A host trace alone is not a successful task. Missing Codex Skill lifecycle events are reported as `NOT_OBSERVABLE`.

## Baseline/candidate evaluation

`run_pair()` fixes the same case suite, pair ID and experiment ID for baseline and candidate. The suite should contain target, normal regression, unknown/boundary and holdout cases. Holdout answers belong only to the independent Oracle. Candidate byte hashes and capability versions are recorded per run; tool-call count alone cannot establish benefit.

The current environment did not complete a pair because the isolated Codex home had no provider bearer credential. The code therefore reports `BLOCKED_ENV` rather than manufacturing `REAL_TASK_EXECUTED` evidence.

## Feedback contract

`OptimizationFeedbackService` writes SQLite v9 `optimization_feedback` rows. It stores observation/manifest identity, before/after metrics, trace digests, source tier, evidence quality, and optional verified references to Issue, Diagnosis, Proposal and Evaluation rows.

Reference validation is conservative:

- an Issue must exist and have an occurrence on an included trace;
- a Diagnosis must belong to that Issue;
- a Proposal must reference that Issue/Diagnosis;
- an Evaluation must exist;
- missing or mismatched references are recorded as empty, not guessed.

Feedback states are:

| Observation | Feedback |
| --- | --- |
| simulated | `DEMONSTRATION_ONLY`, `NO_PRODUCTION_ACTION` |
| insufficient/not comparable | `NEEDS_MORE_EVIDENCE`, `OBSERVATION_GAP` |
| regression | `REVIEW_REQUIRED`, `HUMAN_REVIEW_OR_CONTROLLED_ROLLBACK` |
| real observed improvement | `SUPPORTED_IMPROVEMENT_SIGNAL`, `HUMAN_REVIEW` |
| non-real improvement | `EVALUATION_ONLY`, `NO_PRODUCTION_ACTION` |

The fingerprint is idempotent. A late trace changes the source fingerprint and creates a new feedback revision for the same observation/manifest; it does not overwrite history or perform a production action.
