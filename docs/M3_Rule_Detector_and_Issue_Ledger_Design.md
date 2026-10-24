# M3 Rule Detector and Issue Ledger Design

## Boundary

`RuleDetector` is a deterministic, offline reader of a bounded
`DetectorContext`. It does not call an LLM, execute SQL, modify a Skill/MCP
artifact, or run in the relay hot path. `DiagnosisService` owns batching,
checkpoints, reconciliation, and SQLite writes.

The first registry contains six versioned rules: R01 explicit MCP business
error, R02 network/auth/transport failure, R03 evidence-backed schema
mismatch, R04 explicit retry failure, R05 observation gap, and R06 explicit
expected-capability missing trigger.

## Contracts

- `RuleSpec` defines ID/version, required fields, scope, and severity policy.
- `EvidenceRef` points to a stored `trace_id`/`event_id`, optional capability
  version, and a redacted correlation scope.
- `RuleFinding` contains a stable signature, safe observed fact, evidence refs,
  owner hypotheses, unknown fields, and an evidence grade.
- `AttributionHypothesis` is deliberately not a causal assertion. Owner kinds
  include `network`, `auth`, `observer`, `mcp_server`, `skill`, and `unknown`.

## SQLite v4

M3 applies an additive migration in the existing `.skill_workshop/state.db`:

- `analysis_runs` records detector/ruleset fingerprints, bounded counts,
  status, and errors.
- `issue_clusters` is the aggregate Issue layer. It is separate from the M0
  manual Skill `issues` table.
- `issue_occurrences` stores one redacted evidence occurrence and a unique
  finding fingerprint. It never stores a prompt, complete tool arguments,
  Authorization, Cookie, or raw error text.
- `analysis_checkpoints` keys `(trace_id, ruleset_fingerprint)` and stores a
  revision digest made from event identity/digest set, count, and trace state.
- `issue_status_history` preserves explicit human status transitions and rule
  reopen transitions.

All tables use primary/unique constraints and indexes for status, rule,
trace, capability, checkpoint, and history lookups. Existing M0/M1/M2 tables
are not rebuilt or altered. SQLite uses the existing `busy_timeout`, WAL, and
short `BEGIN IMMEDIATE` transactions.

## Deduplication and reconciliation

An Issue `dedup_key` is a digest of workspace, rule/version, category,
normalized signature, and explicit capability identity. An occurrence
fingerprint includes the trace ID, rule/version, evidence event IDs, and
capability version. Thus independent connections can aggregate into one Issue
without becoming one occurrence, and a repeated import of an unchanged Trace
is skipped.

When a Trace revision changes, its prior occurrences are removed inside the
same write transaction and findings are recalculated. Empty clusters are
removed; surviving clusters have counts/timestamps recomputed from the
database. A new finding reopens a resolved/ignored cluster and writes status
history. A stale maximum sequence cannot hide a late event because the
revision includes the complete event identity/digest set.

## Status and API

`DiagnosisService` exposes `analyze_trace`, bounded `analyze_new`,
`list_issues`, `show_issue`, and optimistic `set_issue_status`. Valid manual
states are `open`, `acknowledged`, `resolved`, and `ignored`. A supplied
`expected_row_version` must match or the update fails with
`ConcurrentUpdateError`.

The CLI is local only:

```powershell
$env:PYTHONPATH = 'servers'
python -m shutu_workshop --workspace .test-m3 diagnose rules
python -m shutu_workshop --workspace .test-m3 diagnose new --limit 100 --json
python -m shutu_workshop --workspace .test-m3 issue list --status open --json
python -m shutu_workshop --workspace .test-m3 issue show <issue-id> --json
python -m shutu_workshop --workspace .test-m3 issue status <issue-id> acknowledged
```

M3 ends at evidence-backed findings and a reviewable ledger. M4 may consume
this ledger for diagnosis proposals/evaluation, but M3 does not auto-fix or
publish anything.
