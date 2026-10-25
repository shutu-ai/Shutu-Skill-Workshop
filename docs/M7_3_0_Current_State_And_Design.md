# M7.3.0 Current State and Design Freeze

## Scope

M7.3 adds a bounded quality layer for Skills and MCPs.  It reuses the M1
`CapabilityRegistry`/`CapabilityVersion`, M1 TraceStore, M3 diagnosis ledger,
M4 evaluation verdicts and evidence rules, M6 isolated runner identities, and
the M7.2 transparent stdio observer.  It does not create a second issue ledger
or rewrite historical rows.

The implementation is intentionally read-only by default.  The only database
change is additive migration 11, which creates `quality_runs` and
`quality_evidence`; existing traces, issues, evaluation verdicts and release
rows retain their previous meaning.

## Frozen contracts

`SkillObservation` records a scope-qualified capability ID, frontmatter
summary, file count, content/tree hash, version ID and status fields.  A
`loaded` or `invoked` value is `null` unless a host event proves it.  Reading a
`SKILL.md` or hashing a script is `ARTIFACT_ACCESS_OBSERVED`, not a native
Skill invocation.

`SkillSelectionLabel` freezes `case_id`, task-input hash, target Skill,
`should_use`, scenario, expected tools, label source and selection mode.
`FORCED` samples are excluded from natural routing metrics.  If host invocation
truth is absent, precision, recall and F1 are `N/A` with a missing-data reason.

`QualityCase`, `QualityMetric`, `QualityEvidence`, `QualityFinding` and
`QualityReport` carry the minimum evidence fields from the M7.3 contract while
remaining serializable without a schema fork.  `QualityEvidence` keeps
artifact/version, trace/event, Oracle, source, correlation and unknown fields
explicit.

## Skill discovery boundary

`SkillRegistry.discover()` scans only caller-provided roots.  It rejects
symlinked files, ignores paths outside the resolved root, handles invalid
UTF-8 as a partial observation, and stores only bounded frontmatter and file
metadata/hash values.  Same-name Skills from different roots retain distinct
scope/root-qualified IDs.

## Three-arm evaluation boundary

`ThreeArmQualityRunner` freezes `no_skill`, `baseline` and `candidate` tree
hashes before execution and checks for mutation after each run.  The runner
receives only `QualityCase.task_input`; the independent Oracle is called by the
evaluation process after the runner returns.  It requires target,
normal-regression and holdout cases.  Local validator runs are explicitly
`FIXTURE_EXECUTED`; they demonstrate engineering capability, not production
optimization benefit.  Missing host/provider evidence remains
`BLOCKED_ENV`/`INCONCLUSIVE`.

## MCP boundary

`ThirdPartyMcpManager` is an explicit, project-local manifest manager for
credential-free stdio test MCPs.  It refuses reserved Workshop names,
recursive Observer commands, non-stdio transports, secret-bearing command
arguments/environment variables, and non-identical overwrite.  Probes use the
existing `StdioObserver` and `McpTraceRecorder`; no retry or replay is added.
Manifests are identity checked and can be disabled without touching unrelated
registrations.

## Attribution and reporting

`JointAttribution` emits `UNVERIFIED_HYPOTHESIS` with required verification
when a Skill/MCP link is based only on a shared request ID, name or time.
`QualityService` persists bounded reports and exposes `summary`/`detail` to the
CLI and the two read-only Workshop MCP tools.  Empty data returns `NO_DATA` and
does not invent a trend or proposal.

## Known host limits

The current Codex host does not expose a reliable native Skill lifecycle event,
PostToolUse result, Desktop surface, or complete `codex exec` tool-result
chain.  Those fields remain `NOT_OBSERVABLE`, `HOST_UNSUPPORTED` or
`BLOCKED_ENV`; the M6.2 real optimization benefit remains `INCONCLUSIVE`.
Observer overhead is also `UNVERIFIED` until a paired real workload is
measured.
